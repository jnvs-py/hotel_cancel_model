"""El modelo como artefacto: Pipeline completo + metadata al lado.

Todo el preprocesamiento vive DENTRO del Pipeline. Si se transformara en el
endpoint, entrenamiento e inferencia divergirian en silencio.
"""

import hashlib
import json
import platform
from datetime import date

import joblib
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

MODEL_VERSION = "0.1.0"
ARTIFACT_NAME = "pipeline.joblib"
METADATA_NAME = "metadata.json"


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


def save_model(pipeline, metrics, data_path, model_dir, threshold, n_train):
    """Guarda el artefacto y su metadata. Nunca pickle.dump directo."""
    model_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(pipeline, model_dir / ARTIFACT_NAME)
    metadata = {
        "model_version": MODEL_VERSION,
        "sklearn_version": sklearn.__version__,
        "python_version": platform.python_version(),
        "trained_at": date.today().isoformat(),
        "data_sha256": hashlib.sha256(data_path.read_bytes()).hexdigest()[:16],
        "n_rows_train": int(n_train),
        "features": list(getattr(pipeline, "feature_names_in_", [])),
        "threshold": float(threshold),
        "metrics": metrics,
    }
    (model_dir / METADATA_NAME).write_text(json.dumps(metadata, indent=2), encoding="utf-8")
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
