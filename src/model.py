"""El modelo como artefacto: Pipeline completo + metadata al lado.

Todo el preprocesamiento vive DENTRO del Pipeline. Si se transformara en el
endpoint, entrenamiento e inferencia divergirian en silencio.
"""

import hashlib
import json
import os
import platform
from datetime import UTC, date, datetime

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, OneHotEncoder

from src.config import SEED
from src.schema import (
    CATEGORICAL_FEATURES,
    NUMERIC_FEATURES,
    add_derived_features,
    derived_feature_names,
)

MODEL_VERSION_FALLBACK = "0.1.0-dev"
VERSION_ENV = "MODEL_VERSION"
ARTIFACT_NAME = "pipeline.joblib"
METADATA_NAME = "metadata.json"
BASELINE_NAME = "baseline.json"


def model_version():
    """Version del modelo. En CI la inyecta el workflow; local queda el fallback."""
    return os.environ.get(VERSION_ENV, MODEL_VERSION_FALLBACK)


def build_pipeline():
    """Pipeline completo: derivadas -> imputacion -> codificacion -> modelo."""
    numericas = Pipeline([("imputer", SimpleImputer(strategy="median"))])
    categoricas = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="constant", fill_value="Desconocido")),
            # handle_unknown="ignore" no es opcional: en produccion llega una
            # categoria que no estaba en el entrenamiento y sin esto es un 500.
            ("encoder", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
        ]
    )
    preprocesador = ColumnTransformer(
        [("num", numericas, NUMERIC_FEATURES), ("cat", categoricas, CATEGORICAL_FEATURES)]
    )
    return Pipeline(
        [
            (
                "derivadas",
                FunctionTransformer(add_derived_features, feature_names_out=derived_feature_names),
            ),
            ("prep", preprocesador),
            ("modelo", HistGradientBoostingClassifier(random_state=SEED)),
        ]
    )


def _numeric_profile(serie, n_bins=10):
    """Perfil de una numerica: conteos, cuantiles y BINS CONGELADOS.

    Los bordes se fijan aqui, en el entrenamiento: si se recalcularan con
    datos de produccion, el PSI mediria ruido en vez de deriva.
    """
    limpio = serie.dropna().astype(float)
    bordes = np.unique(np.quantile(limpio, np.linspace(0, 1, n_bins + 1)))
    bordes[0], bordes[-1] = -np.inf, np.inf
    conteos, _ = np.histogram(limpio, bins=bordes)
    cuantiles = limpio.quantile([0.01, 0.1, 0.5, 0.9, 0.99]).tolist()
    return {
        "kind": "numeric",
        "n": int(limpio.size),
        "null_rate": float(serie.isna().mean()),
        "mean": float(limpio.mean()),
        "std": float(limpio.std()),
        "min": float(limpio.min()),
        "max": float(limpio.max()),
        "quantiles": dict(zip(["p01", "p10", "p50", "p90", "p99"], cuantiles, strict=True)),
        "bin_edges": [float(borde) for borde in bordes],
        "bin_props": (conteos / max(conteos.sum(), 1)).tolist(),
        # Muestra para el KS del job de drift; no es PII en este dataset.
        "sample": limpio.sample(min(2000, limpio.size), random_state=42).round(6).tolist(),
    }


def _categorical_profile(serie):
    """Perfil de una categorica: proporcion por nivel del contrato + __otra__."""
    proporciones = serie.dropna().astype(str).value_counts(normalize=True)
    return {
        "kind": "categorical",
        "n": int(serie.notna().sum()),
        "null_rate": float(serie.isna().mean()),
        "props": {**proporciones.to_dict(), "__otra__": 0.0},
    }


def _feature_importance(pipeline, X, y, muestra=1500, repeticiones=3):
    """Importancia por permutacion sobre una muestra de validacion.

    Se guarda en la metadata para que el job de drift sepa QUE features vigilar
    primero: deriva en una feature irrelevante es un dato, no una alerta.
    """
    from sklearn.inspection import permutation_importance

    tamano = min(muestra, len(X))
    X_m = X.sample(n=tamano, random_state=42)
    y_m = y.loc[X_m.index]
    resultado = permutation_importance(
        pipeline, X_m, y_m, n_repeats=repeticiones, random_state=42, scoring="roc_auc", n_jobs=-1
    )
    return {
        columna: round(float(valor), 6)
        for columna, valor in zip(X.columns, resultado.importances_mean, strict=True)
    }


def build_baseline(X_validacion, proba_validacion, version, dataset_hash, metric, threshold):
    """Congela la referencia contra la que el job de drift compara produccion."""
    caracteristicas = {}
    for columna in X_validacion.columns:
        if pd.api.types.is_numeric_dtype(X_validacion[columna]):
            caracteristicas[columna] = _numeric_profile(X_validacion[columna])
        else:
            caracteristicas[columna] = _categorical_profile(X_validacion[columna])
    return {
        "model_version": version,
        "dataset_hash": dataset_hash,
        "created_at": datetime.now(UTC).isoformat(),
        "metric": metric,
        "features": caracteristicas,
        "prediction": _numeric_profile(pd.Series(proba_validacion, name="probability")),
        "positive_rate": float(np.mean(np.asarray(proba_validacion) >= threshold)),
    }


def save_model(
    pipeline,
    metrics,
    data_path,
    model_dir,
    threshold,
    n_train,
    fingerprint=None,
    validation=None,
    importance=None,
):
    """Guarda el artefacto, su metadata y (si hay validacion) el baseline.

    Nunca pickle.dump directo. La version y el fingerprint vienen del entorno
    (CI los deriva del repo); local quedan en fallback.
    """
    model_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(pipeline, model_dir / ARTIFACT_NAME)
    huella = fingerprint or os.environ.get("FINGERPRINT")
    hash_datos = hashlib.sha256(data_path.read_bytes()).hexdigest()[:16]
    metadata = {
        "model_version": model_version(),
        "sklearn_version": sklearn.__version__,
        "python_version": platform.python_version(),
        "trained_at": date.today().isoformat(),
        "data_sha256": hash_datos,
        "n_rows_train": int(n_train),
        "features": list(getattr(pipeline, "feature_names_in_", [])),
        "threshold": float(threshold),
        "metrics": metrics,
    }
    if huella:
        metadata["fingerprint"] = huella
    if importance:
        metadata["feature_importance"] = importance
    (model_dir / METADATA_NAME).write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    if validation is not None:
        X_val, proba_val = validation
        baseline = build_baseline(
            X_val,
            proba_val,
            version=metadata["model_version"],
            dataset_hash=hash_datos,
            metric=metrics.get("temporal", {}),
            threshold=float(threshold),
        )
        (model_dir / BASELINE_NAME).write_text(json.dumps(baseline, indent=2), encoding="utf-8")
    return metadata


def load_model(model_dir):
    """Carga defensiva: falla claro en vez de predecir basura."""
    artefacto = model_dir / ARTIFACT_NAME
    meta = model_dir / METADATA_NAME
    if not artefacto.exists():
        raise FileNotFoundError(
            f"No existe {artefacto}. Entrena primero con 'uv run python -m src.train'."
        )
    try:
        pipeline = joblib.load(artefacto)
    except Exception as exc:
        raise ValueError(f"{artefacto} no se pudo cargar. Es un artefacto de save_model?") from exc
    if not hasattr(pipeline, "predict_proba"):
        raise ValueError(f"{artefacto} no contiene un clasificador con predict_proba.")

    metadata = json.loads(meta.read_text(encoding="utf-8")) if meta.exists() else {}
    entrenado_con = metadata.get("sklearn_version")
    if entrenado_con and entrenado_con != sklearn.__version__:
        # No es un warning que se pueda ignorar: predice distinto sin avisar.
        raise ValueError(
            f"El modelo se entreno con scikit-learn {entrenado_con} y aqui hay "
            f"{sklearn.__version__}. Reentrena o fija la version en el lock."
        )
    return pipeline, metadata
