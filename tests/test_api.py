"""Tests de contrato de la API: un 422 por cada regla que escribimos."""

import json

import pytest
from fastapi.testclient import TestClient

from src.config import CATEGORICAL_LEVELS, REPO_ROOT

EJEMPLO = json.loads((REPO_ROOT / "tests" / "fixtures" / "request_ejemplo.json").read_text())


@pytest.fixture
def cliente(monkeypatch, modelo_entrenado):
    import src.api as api

    monkeypatch.setattr(api, "MODELS_DIR", modelo_entrenado)
    with TestClient(api.app) as c:
        yield c


@pytest.fixture
def cliente_sin_modelo(monkeypatch, tmp_path):
    import src.api as api

    monkeypatch.setattr(api, "MODELS_DIR", tmp_path)
    with TestClient(api.app) as c:
        yield c


def test_health_no_requiere_modelo(cliente_sin_modelo):
    """Liveness: responde aunque no haya artefacto."""
    r = cliente_sin_modelo.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_ready_503_sin_modelo(cliente_sin_modelo):
    r = cliente_sin_modelo.get("/ready")
    assert r.status_code == 503
    assert r.json()["ready"] is False


def test_predict_503_sin_modelo(cliente_sin_modelo):
    assert cliente_sin_modelo.post("/predict", json=EJEMPLO).status_code == 503


def test_ready_200_con_modelo(cliente):
    assert cliente.get("/ready").json()["ready"] is True


def test_predict_camino_feliz(cliente):
    r = cliente.post("/predict", json=EJEMPLO)
    assert r.status_code == 200
    cuerpo = r.json()
    # Un 200 con el cuerpo equivocado sigue siendo una API rota.
    assert set(cuerpo) == {"prediction", "probability", "threshold", "model_version", "request_id"}
    assert cuerpo["prediction"] in ("Canceled", "Not_Canceled")
    assert 0.0 <= cuerpo["probability"] <= 1.0


def test_predict_rechaza_campo_extra(cliente):
    assert cliente.post("/predict", json=dict(EJEMPLO, campo_raro=1)).status_code == 422


def test_predict_rechaza_campo_faltante(cliente):
    payload = dict(EJEMPLO)
    del payload["lead_time"]
    assert cliente.post("/predict", json=payload).status_code == 422


@pytest.mark.parametrize(
    ("campo", "valor"),
    [
        ("lead_time", -3),
        ("lead_time", 99999),
        ("avg_price_per_room", -1.0),
        ("no_of_special_requests", 99),
        ("arrival_month", 13),
    ],
)
def test_predict_rechaza_fuera_de_rango(cliente, campo, valor):
    assert cliente.post("/predict", json={**EJEMPLO, campo: valor}).status_code == 422


def test_predict_rechaza_categoria_invalida(cliente):
    payload = dict(EJEMPLO, room_type_reserved="Room_Type 99")
    assert cliente.post("/predict", json=payload).status_code == 422


def test_predict_rechaza_reserva_sin_huespedes(cliente):
    payload = dict(EJEMPLO, no_of_adults=0, no_of_children=0)
    assert cliente.post("/predict", json=payload).status_code == 422


def test_predict_rechaza_dia_que_no_existe_en_el_mes(cliente):
    payload = dict(EJEMPLO, arrival_month=2, arrival_date=31)
    assert cliente.post("/predict", json=payload).status_code == 422


def test_model_info_expone_la_version(cliente):
    cuerpo = cliente.get("/model-info").json()
    assert cuerpo["model_version"]
    assert cuerpo["features"]


def test_model_info_503_sin_modelo(cliente_sin_modelo):
    assert cliente_sin_modelo.get("/model-info").status_code == 503


def test_los_literal_no_se_desincronizan_de_config(cliente):
    """La API duplica los niveles por legibilidad de /docs. Este test los ata."""
    from src.api import PredictRequest

    for campo, niveles in CATEGORICAL_LEVELS.items():
        anotacion = PredictRequest.model_fields[campo].annotation
        assert set(anotacion.__args__) == set(niveles), f"{campo} desincronizado"


def test_docs_disponible(cliente):
    assert cliente.get("/docs").status_code == 200
