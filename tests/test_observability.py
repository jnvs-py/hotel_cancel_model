"""Tests de la fundacion de observabilidad: hash canonico, formatter JSON y etapas.

Cubre el Contrato A (esquema de log) y el AC-004 (hash estable, sin hash()).
Los tests de integracion con la API viven en la seccion de la API.
"""

import hashlib
import json
import logging

import pytest
from fastapi.testclient import TestClient

from src.config import REPO_ROOT
from src.observability import (
    JsonFormatter,
    configure_logging,
    features_hash,
    new_request_id,
    request_id_var,
    safe_features,
    stage,
    stages_ms,
)


def _record(evento="prediction", **extra):
    registro = logging.LogRecord("service", logging.INFO, __file__, 10, evento, None, None)
    for clave, valor in extra.items():
        setattr(registro, clave, valor)
    return registro


def test_features_hash_es_estable_ante_el_orden_de_claves():
    """AC-004: el mismo diccionario con otro orden de claves da el mismo hash."""
    a = {"lead_time": 224, "avg_price_per_room": 65.0, "arrival_month": 10}
    b = {"arrival_month": 10, "lead_time": 224, "avg_price_per_room": 65.0}
    assert features_hash(a) == features_hash(b)


def test_features_hash_es_sha256_canonico_y_no_hash_de_python():
    """AC-004: hash() variaria con PYTHONHASHSEED; el sha256 canonico no."""
    esperado = hashlib.sha256(b'{"a":1,"b":2}').hexdigest()[:16]
    assert features_hash({"b": 2, "a": 1}) == esperado
    assert len(esperado) == 16


def test_formatter_emite_una_sola_linea_json_con_el_esquema():
    """Contrato A: una linea JSON por evento, con los campos obligatorios."""
    salida = JsonFormatter().format(
        _record(model_version="0.1.0+abc", probability=0.0923, status="success")
    )
    assert "\n" not in salida
    payload = json.loads(salida)
    for clave in (
        "timestamp",
        "level",
        "event",
        "request_id",
        "model_name",
        "model_version",
        "probability",
        "status",
    ):
        assert clave in payload, f"falta {clave} en el evento"
    assert payload["event"] == "prediction"
    assert payload["level"] == "INFO"
    assert payload["request_id"] == "-"


def test_formatter_incluye_el_traceback_solo_en_el_log():
    """El traceback va al log (campo exc_info), nunca a la respuesta HTTP."""
    try:
        raise ValueError("fallo de prueba")
    except ValueError:
        import sys

        registro = logging.LogRecord(
            "service", logging.ERROR, __file__, 10, "prediction_failed", None, sys.exc_info()
        )
    payload = json.loads(JsonFormatter().format(registro))
    assert "ValueError: fallo de prueba" in payload["exc_info"]


def test_request_id_reusa_el_entrante_y_genera_si_falta():
    assert new_request_id("entrante-123") == "entrante-123"
    assert request_id_var.get() == "entrante-123"
    generado = new_request_id(None)
    assert generado != "entrante-123"
    assert len(generado) == 32


def test_stage_acumula_milisegundos_por_etapa():
    new_request_id("rid-etapas")
    with stage("validate"):
        pass
    with stage("predict"):
        pass
    milisegundos = stages_ms()
    assert set(milisegundos) == {"validate", "predict"}
    assert all(valor >= 0 for valor in milisegundos.values())


def test_stages_ms_no_revienta_sin_contexto():
    """Fuera de una peticion no hay etapas: devuelve un diccionario vacio."""
    assert isinstance(stages_ms(), dict)


def test_safe_features_devuelve_copia_sin_mutar_el_original():
    original = {"lead_time": 224, "avg_price_per_room": 65.0}
    copia = safe_features(original)
    assert copia == original
    copia["lead_time"] = 0
    assert original["lead_time"] == 224


def test_configure_logging_usa_json_y_apaga_el_access_log():
    root = logging.getLogger()
    handlers_previos = root.handlers[:]
    nivel_previo = root.level
    try:
        configure_logging()
        assert any(isinstance(h.formatter, JsonFormatter) for h in root.handlers)
        assert logging.getLogger("uvicorn.access").disabled is True
    finally:
        root.handlers[:] = handlers_previos
        root.setLevel(nivel_previo)
        logging.getLogger("uvicorn.access").disabled = False


# ---------------------------------------------------------------------------
# Integracion con la API (AC-001, AC-002, AC-003)
# ---------------------------------------------------------------------------

EJEMPLO = json.loads((REPO_ROOT / "tests" / "fixtures" / "request_ejemplo.json").read_text())


@pytest.fixture
def cliente_con_logs(monkeypatch, modelo_entrenado):
    """Cliente de la API + los eventos JSON que emite.

    configure_logging() (en el lifespan) reemplaza los handlers del root, asi
    que el capturador se instala DESPUES de entrar al contexto y formatea con
    la misma clase que usa el servicio: se prueba el esquema real.
    """
    import src.api as api

    registros: list[dict] = []

    class HandlerLista(logging.Handler):
        def emit(self, record):
            registros.append(json.loads(JsonFormatter().format(record)))

    monkeypatch.setattr(api, "MODELS_DIR", modelo_entrenado)
    # raise_server_exceptions=False: Starlette re-lanza tras responder el 500;
    # en produccion el cliente recibe la respuesta, no la excepcion.
    with TestClient(api.app, raise_server_exceptions=False) as c:
        logging.getLogger().addHandler(HandlerLista())
        yield c, registros


def _eventos(registros: list[dict], nombre: str) -> list[dict]:
    return [evento for evento in registros if evento.get("event") == nombre]


def test_prediction_emite_una_linea_json_con_el_esquema(cliente_con_logs):
    """AC-001: exactamente un evento prediction, con todos los campos."""
    cliente, registros = cliente_con_logs
    respuesta = cliente.post("/predict", json=EJEMPLO)
    assert respuesta.status_code == 200

    eventos = _eventos(registros, "prediction")
    assert len(eventos) == 1
    evento = eventos[0]
    for clave in (
        "timestamp",
        "level",
        "event",
        "request_id",
        "model_name",
        "model_version",
        "features_hash",
        "prediction",
        "probability",
        "threshold",
        "latency_ms",
        "stage_ms",
        "status",
    ):
        assert clave in evento, f"falta {clave} en el evento prediction"
    assert evento["level"] == "INFO"
    assert evento["status"] == "success"
    assert set(evento["stage_ms"]) == {"to_frame", "predict", "postprocess"}


def test_prediction_emite_evento_de_features_para_el_drift(cliente_con_logs):
    """Contrato A: evento separado con los valores del contrato, sin hash."""
    cliente, registros = cliente_con_logs
    cliente.post("/predict", json=EJEMPLO)
    eventos = _eventos(registros, "prediction_features")
    assert len(eventos) == 1
    for campo in EJEMPLO:
        assert campo in eventos[0], f"falta {campo} en prediction_features"


def test_request_id_se_propaga_por_log_header_y_cuerpo(cliente_con_logs):
    """AC-002: el X-Request-ID entrante atraviesa log, header y respuesta."""
    cliente, registros = cliente_con_logs
    respuesta = cliente.post("/predict", json=EJEMPLO, headers={"X-Request-ID": "rid-clase"})
    assert respuesta.headers["X-Request-ID"] == "rid-clase"
    assert respuesta.json()["request_id"] == "rid-clase"

    evento = _eventos(registros, "prediction")[0]
    assert evento["request_id"] == "rid-clase"


def test_request_id_se_genera_si_no_viene(cliente_con_logs):
    """AC-002: sin header entrante se genera uno y se devuelve."""
    cliente, _ = cliente_con_logs
    cabecera = cliente.post("/predict", json=EJEMPLO).headers["X-Request-ID"]
    assert cabecera and len(cabecera) == 32


def test_error_500_lleva_request_id_y_no_traceback(cliente_con_logs, monkeypatch):
    """AC-002: el traceback va al log; la respuesta solo lleva el id."""

    class PipelineRoto:
        def predict_proba(self, _):
            raise RuntimeError("fallo simulado del pipeline")

    import src.api as api

    cliente, registros = cliente_con_logs
    monkeypatch.setitem(api.ml, "pipeline", PipelineRoto())
    respuesta = cliente.post("/predict", json=EJEMPLO, headers={"X-Request-ID": "rid-error"})
    assert respuesta.status_code == 500
    assert respuesta.json()["request_id"] == "rid-error"
    assert "Traceback" not in respuesta.text
    assert "fallo simulado" not in respuesta.text

    eventos = _eventos(registros, "prediction_failed")
    assert len(eventos) == 1
    assert "fallo simulado del pipeline" in eventos[0]["exc_info"]
    # El handler global usa OTRO nombre: evita el doble conteo de errores.
    assert len(_eventos(registros, "unhandled_error")) == 1


def test_input_invalido_loguea_warning_con_el_campo(cliente_con_logs):
    """Contrato A: un 422 se registra como WARNING nombrando el campo culpable."""
    cliente, registros = cliente_con_logs
    respuesta = cliente.post(
        "/predict", json={**EJEMPLO, "lead_time": 99999}, headers={"X-Request-ID": "rid-422"}
    )
    assert respuesta.status_code == 422

    eventos = _eventos(registros, "input_invalid")
    assert len(eventos) == 1
    assert eventos[0]["level"] == "WARNING"
    assert "lead_time" in eventos[0]["fields"]


def test_los_logs_no_contienen_secretos(cliente_con_logs, monkeypatch):
    """AC-003: ni la API key ni cabeceras crudas aparecen en los eventos."""
    monkeypatch.setenv("API_KEY", "clave-secreta-de-prueba")
    cliente, registros = cliente_con_logs
    cliente.post(
        "/predict",
        json=EJEMPLO,
        headers={"Authorization": "Bearer clave-secreta-de-prueba", "X-Request-ID": "rid-s"},
    )
    serializado = json.dumps(registros)
    assert "clave-secreta-de-prueba" not in serializado
    assert "Authorization" not in serializado


def test_los_probes_no_generan_eventos_de_prediccion(cliente_con_logs):
    """AC-006: /health y /ready no se cuentan ni se loguean como trafico."""
    cliente, registros = cliente_con_logs
    assert cliente.get("/health").status_code == 200
    assert cliente.get("/ready").status_code == 200
    assert _eventos(registros, "prediction") == []
