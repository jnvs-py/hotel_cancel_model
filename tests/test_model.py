"""El artefacto: metadata completa y carga defensiva."""

import json

import pytest
import sklearn

from src.model import ARTIFACT_NAME, METADATA_NAME, load_model


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
