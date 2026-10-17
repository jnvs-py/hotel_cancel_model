"""Tests de la fundacion de observabilidad: hash canonico, formatter JSON y etapas.

Cubre el Contrato A (esquema de log) y el AC-004 (hash estable, sin hash()).
Los tests de integracion con la API viven en la seccion de la API.
"""

import hashlib
import json
import logging

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
