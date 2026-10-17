"""Observabilidad: logging JSON a stdout, request_id, metricas y tiempos por etapa.

Adaptado de la plantilla de la skill u_observabilidad_ml. Reglas duras:
- Todo evento va a stdout como UNA linea JSON. Ningun FileHandler.
- `features_hash` usa sha256 canonico, NUNCA hash() de Python (aleatorizado
  por proceso: la misma entrada daria hashes distintos entre reinicios).
- Los labels de las metricas son de BAJA cardinalidad: request_id y
  features_hash van al log, no a Prometheus.
- Este dataset no tiene PII, por eso PII_FIELDS esta vacio. Si en el futuro
  se agrega una feature sensible, va aqui y sale hasheada, no en texto.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import os
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime

from prometheus_client import Counter, Gauge, Histogram

MODEL_NAME = os.getenv("MODEL_NAME", "hotel-cancel")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# Columnas del contrato que nunca deben salir en texto plano.
# Vacio a proposito: data/hotel_reservations.csv no contiene PII.
PII_FIELDS: frozenset[str] = frozenset()

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
_stages_var: contextvars.ContextVar[dict[str, float] | None] = contextvars.ContextVar(
    "stages", default=None
)

# ---------------------------------------------------------------------------
# Logging JSON
# ---------------------------------------------------------------------------

_RESERVED = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """Una linea JSON por evento. Los campos de `extra=` suben al primer nivel."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "event": record.getMessage(),
            "logger": record.name,
            "request_id": request_id_var.get(),
            "model_name": MODEL_NAME,
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=True, default=str)


def configure_logging() -> None:
    """Llamar UNA vez al arrancar (lifespan). Todo a stdout, nada a archivos."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(LOG_LEVEL)
    # El access log de uvicorn duplica cada request en texto plano: se apaga
    # para que el stream tenga UN solo formato.
    logging.getLogger("uvicorn.access").disabled = True
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers[:] = []
        logging.getLogger(name).propagate = True


logger = logging.getLogger("service")

# ---------------------------------------------------------------------------
# Hash de features y saneamiento
# ---------------------------------------------------------------------------


def features_hash(features: dict) -> str:
    """sha256 canonico de las features. sort_keys: el orden no cambia el hash."""
    canonical = json.dumps(features, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def safe_features(features: dict) -> dict:
    """Features aptas para el evento de drift: sin columnas PII."""
    return {k: v for k, v in features.items() if k not in PII_FIELDS}


# ---------------------------------------------------------------------------
# Metricas Prometheus (labels de BAJA cardinalidad unicamente)
# ---------------------------------------------------------------------------

LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5)

REQUESTS = Counter("ml_requests_total", "Peticiones de prediccion", ["model_version", "status"])
LATENCY = Histogram(
    "ml_request_latency_seconds",
    "Latencia total de /predict",
    ["model_version"],
    buckets=LATENCY_BUCKETS,
)
STAGE_LATENCY = Histogram(
    "ml_stage_latency_seconds", "Latencia por etapa", ["stage"], buckets=LATENCY_BUCKETS
)
SCORE = Histogram(
    "ml_prediction_score",
    "Probabilidad predicha",
    ["model_version"],
    buckets=tuple(i / 10 for i in range(1, 11)),
)
PREDICTIONS = Counter("ml_predictions_total", "Predicciones por clase", ["model_version", "label"])
INVALID_INPUT = Counter("ml_input_invalid_total", "Inputs rechazados por el contrato", ["field"])
MODEL_LOADED = Gauge("ml_model_loaded", "1 si el modelo esta cargado", ["model_version"])

# ---------------------------------------------------------------------------
# Request id y tiempos por etapa
# ---------------------------------------------------------------------------


def new_request_id(incoming: str | None = None) -> str:
    """Reusa el X-Request-ID entrante si existe; si no, genera uno."""
    rid = incoming or uuid.uuid4().hex
    request_id_var.set(rid)
    _stages_var.set({})
    return rid


@contextmanager
def stage(name: str):
    """Mide una etapa con reloj monotono (perf_counter, no time.time)."""
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - start
        STAGE_LATENCY.labels(stage=name).observe(elapsed)
        stages = _stages_var.get()
        if stages is not None:
            stages[name] = round(elapsed * 1000, 3)


def stages_ms() -> dict[str, float]:
    return dict(_stages_var.get() or {})
