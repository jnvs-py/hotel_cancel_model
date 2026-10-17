"""El artefacto: metadata completa y carga defensiva."""

import json

import pytest
import sklearn

from src.config import TARGET
from src.model import (
    ARTIFACT_NAME,
    BASELINE_NAME,
    METADATA_NAME,
    build_pipeline,
    load_model,
    save_model,
)
from src.schema import FEATURE_ORDER


def test_guarda_artefacto_y_metadata(modelo_entrenado):
    assert (modelo_entrenado / ARTIFACT_NAME).exists()
    assert (modelo_entrenado / METADATA_NAME).exists()


def test_metadata_trae_lo_necesario_para_auditar(modelo_entrenado):
    meta = json.loads((modelo_entrenado / METADATA_NAME).read_text(encoding="utf-8"))
    for clave in ("model_version", "sklearn_version", "data_sha256", "features", "threshold"):
        assert clave in meta, f"falta {clave} en la metadata"
    assert meta["features"], "la lista de features no puede ir vacia: define el orden"


def test_carga_y_predice(modelo_entrenado, fila_valida):
    import pandas as pd

    from src.schema import FEATURE_ORDER

    pipeline, meta = load_model(modelo_entrenado)
    proba = pipeline.predict_proba(pd.DataFrame([fila_valida], columns=FEATURE_ORDER))[0, 1]
    assert 0.0 <= proba <= 1.0
    assert meta["model_version"]


def test_falla_legible_si_no_hay_artefacto(tmp_path):
    with pytest.raises(FileNotFoundError, match=r"src\.train"):
        load_model(tmp_path)


def test_falla_legible_si_el_artefacto_esta_corrupto(tmp_path):
    (tmp_path / ARTIFACT_NAME).write_bytes(b"esto no es un joblib")
    with pytest.raises(ValueError, match="artefacto de save_model"):
        load_model(tmp_path)


def test_falla_si_cambio_la_version_de_sklearn(modelo_entrenado):
    """Es el bug silencioso mas caro: otra version predice distinto sin avisar."""
    meta_path = modelo_entrenado / METADATA_NAME
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["sklearn_version"] = "0.0.1-inventada"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(ValueError, match=sklearn.__version__):
        load_model(modelo_entrenado)


def _csv_minimo(tmp_path):
    """save_model hashea el dataset: para estos tests basta un CSV de mentira."""
    ruta = tmp_path / "datos.csv"
    ruta.write_text("a,b\n1,2\n", encoding="utf-8")
    return ruta


def test_version_inyectada_por_ci(monkeypatch, tmp_path, datos_sinteticos):
    """CI deriva version y fingerprint de git y los pasa por entorno."""
    monkeypatch.setenv("MODEL_VERSION", "0.2.0+a1b2c3d")
    monkeypatch.setenv("FINGERPRINT", "fp-de-ci")
    X = datos_sinteticos[FEATURE_ORDER]
    y = (datos_sinteticos[TARGET] == "Canceled").astype(int)
    pipeline = build_pipeline().fit(X, y)
    metadata = save_model(pipeline, {}, _csv_minimo(tmp_path), tmp_path, 0.5, len(X))
    assert metadata["model_version"] == "0.2.0+a1b2c3d"
    assert metadata["fingerprint"] == "fp-de-ci"


def test_version_local_sin_entorno(tmp_path, datos_sinteticos):
    """Sin CI, el artefacto queda marcado como dev: nunca una version mentirosa."""
    X = datos_sinteticos[FEATURE_ORDER]
    y = (datos_sinteticos[TARGET] == "Canceled").astype(int)
    pipeline = build_pipeline().fit(X, y)
    metadata = save_model(pipeline, {}, _csv_minimo(tmp_path), tmp_path, 0.5, len(X))
    assert metadata["model_version"] == "0.1.0-dev"
    assert "fingerprint" not in metadata


def test_fingerprint_en_fixture_sin_inyeccion(modelo_entrenado):
    """El fixture del conftest no pasa fingerprint: la clave no debe inventarse."""
    meta = json.loads((modelo_entrenado / METADATA_NAME).read_text(encoding="utf-8"))
    assert "fingerprint" not in meta


# ---------------------------------------------------------------------------
# Baseline del modelo (AC-007) — el paso S4 del plan de observabilidad
# ---------------------------------------------------------------------------


def _entrenar_y_guardar(tmp_path, datos_sinteticos, con_validacion=True):
    X = datos_sinteticos[FEATURE_ORDER]
    y = (datos_sinteticos[TARGET] == "Canceled").astype(int)
    pipeline = build_pipeline().fit(X, y)
    validacion = (X, pipeline.predict_proba(X)[:, 1]) if con_validacion else None
    save_model(
        pipeline,
        {"temporal": {"f1": 0.6, "threshold": 0.5}},
        _csv_minimo(tmp_path),
        tmp_path,
        0.5,
        len(X),
        validation=validacion,
    )
    return tmp_path


def test_baseline_se_escribe_con_la_version_del_modelo(tmp_path, datos_sinteticos):
    """AC-007: el baseline es hermano del artefacto y lleva su misma version."""
    modelo_entrenado = _entrenar_y_guardar(tmp_path, datos_sinteticos)
    metadata = json.loads((modelo_entrenado / METADATA_NAME).read_text(encoding="utf-8"))
    baseline = json.loads((modelo_entrenado / BASELINE_NAME).read_text(encoding="utf-8"))

    assert baseline["model_version"] == metadata["model_version"]
    assert baseline["dataset_hash"] == metadata["data_sha256"]


def test_baseline_congela_bins_y_perfiles(tmp_path, datos_sinteticos):
    """Contrato C: bins fijos por numerica, proporciones por categorica."""
    modelo_entrenado = _entrenar_y_guardar(tmp_path, datos_sinteticos)
    baseline = json.loads((modelo_entrenado / BASELINE_NAME).read_text(encoding="utf-8"))

    numerica = baseline["features"]["lead_time"]
    assert numerica["kind"] == "numeric"
    assert len(numerica["bin_edges"]) == len(numerica["bin_props"]) + 1
    assert numerica["bin_edges"][0] == float("-inf")
    assert numerica["bin_edges"][-1] == float("inf")
    assert abs(sum(numerica["bin_props"]) - 1.0) < 1e-6
    assert set(numerica["quantiles"]) == {"p01", "p10", "p50", "p90", "p99"}

    categorica = baseline["features"]["room_type_reserved"]
    assert categorica["kind"] == "categorical"
    assert "__otra__" in categorica["props"]

    assert baseline["prediction"]["kind"] == "numeric"
    assert 0.0 <= baseline["positive_rate"] <= 1.0


def test_sin_validacion_no_se_escribe_baseline(tmp_path, datos_sinteticos):
    """Compatibilidad: el artefacto es valido sin baseline."""
    modelo_entrenado = _entrenar_y_guardar(tmp_path, datos_sinteticos, con_validacion=False)
    assert not (modelo_entrenado / BASELINE_NAME).exists()


def test_load_model_no_necesita_el_baseline(tmp_path, datos_sinteticos):
    """El baseline es aditivo: la API sigue cargando el artefacto sin el."""
    modelo_entrenado = _entrenar_y_guardar(tmp_path, datos_sinteticos, con_validacion=False)
    pipeline, metadata = load_model(modelo_entrenado)
    assert pipeline is not None and metadata["model_version"]
