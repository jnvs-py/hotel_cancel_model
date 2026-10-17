"""Mide el overhead de la instrumentacion (evidencia reproducible de AC-012).

Compara el pipeline solo contra el pipeline + instrumentacion completa
(etapas, metricas y los dos eventos de log con el formatter real), sin el
stack HTTP: lo que se mide es la instrumentacion, no el TestClient.

    uv run python scripts/medir_overhead.py [iteraciones]

Presupuesto del proyecto: overhead p99 < 5 ms.
"""

import json
import logging
import statistics
import sys
import time

import joblib
import pandas as pd

from src.config import MODELS_DIR, REPO_ROOT
from src.observability import (
    LATENCY,
    PREDICTIONS,
    REQUESTS,
    SCORE,
    JsonFormatter,
    features_hash,
    new_request_id,
    safe_features,
    stage,
    stages_ms,
)
from src.schema import FEATURE_ORDER


class _Descarte:
    """Descarta la salida pero paga el formateo JSON, como stdout."""

    def write(self, _):
        pass

    def flush(self):
        pass


def main() -> int:
    iteraciones = int(sys.argv[1]) if len(sys.argv) > 1 else 500
    pipeline = joblib.load(MODELS_DIR / "pipeline.joblib")
    payload = json.loads((REPO_ROOT / "tests" / "fixtures" / "request_ejemplo.json").read_text())
    fila = pd.DataFrame([payload], columns=FEATURE_ORDER)

    logger = logging.getLogger("medicion")
    logger.handlers = [logging.StreamHandler(_Descarte())]
    logger.handlers[0].setFormatter(JsonFormatter())
    logger.setLevel("INFO")
    logger.propagate = False

    def solo_pipeline():
        return float(pipeline.predict_proba(fila)[0, 1])

    def instrumentado():
        new_request_id(None)
        inicio = time.perf_counter()
        with stage("to_frame"):
            pass
        with stage("predict"):
            probabilidad = float(pipeline.predict_proba(fila)[0, 1])
        with stage("postprocess"):
            pass
        latencia = (time.perf_counter() - inicio) * 1000
        REQUESTS.labels(model_version="medicion", status="success").inc()
        LATENCY.labels(model_version="medicion").observe(latencia / 1000)
        SCORE.labels(model_version="medicion").observe(probabilidad)
        PREDICTIONS.labels(model_version="medicion", label="medicion").inc()
        logger.info(
            "prediction",
            extra={
                "model_version": "medicion",
                "features_hash": features_hash(payload),
                "prediction": "x",
                "probability": probabilidad,
                "threshold": 0.67,
                "latency_ms": latencia,
                "stage_ms": stages_ms(),
                "status": "success",
            },
        )
        logger.info("prediction_features", extra=safe_features(payload))
        return probabilidad

    solo_pipeline()
    instrumentado()
    tiempos = {"solo": [], "instrumentado": []}
    for _ in range(iteraciones):
        t = time.perf_counter()
        solo_pipeline()
        tiempos["solo"].append((time.perf_counter() - t) * 1000)
        t = time.perf_counter()
        instrumentado()
        tiempos["instrumentado"].append((time.perf_counter() - t) * 1000)

    def p99(serie):
        return statistics.quantiles(serie, n=100)[98]

    for nombre, serie in tiempos.items():
        print(f"{nombre:15} p50={statistics.median(serie):6.3f}ms  p99={p99(serie):6.3f}ms")
    overhead = p99(tiempos["instrumentado"]) - p99(tiempos["solo"])
    print(f"\noverhead de la instrumentacion: p99 {overhead:+.3f}ms (presupuesto: < 5ms)")
    return 0 if overhead < 5 else 1


if __name__ == "__main__":
    sys.exit(main())
