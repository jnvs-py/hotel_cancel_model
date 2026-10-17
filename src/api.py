"""API de prediccion de cancelacion de reservas.

El modelo se carga UNA vez en el lifespan. A nivel de modulo se recargaria en
cada --reload y lo pagaria cualquier script que importe este archivo; dentro
del handler se deserializaria en cada request.
"""

import logging
import time
from contextlib import asynccontextmanager
from typing import Literal

import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator

from src.config import CATEGORICAL_LEVELS, MODELS_DIR, NUMERIC_RANGES
from src.model import load_model, model_version
from src.observability import (
    configure_logging,
    features_hash,
    new_request_id,
    request_id_var,
    safe_features,
    stage,
    stages_ms,
)
from src.schema import FEATURE_ORDER

logger = logging.getLogger("api")

DIAS_POR_MES = {
    1: 31,
    2: 29,
    3: 31,
    4: 30,
    5: 31,
    6: 30,
    7: 31,
    8: 31,
    9: 30,
    10: 31,
    11: 30,
    12: 31,
}

ml = {}


@asynccontextmanager
async def lifespan(app):
    # Logging JSON UNA vez, al arrancar: todo evento va a stdout, nunca a archivo.
    configure_logging()
    try:
        ml["pipeline"], ml["metadata"] = load_model(MODELS_DIR)
        logger.info(
            "model_loaded",
            extra={"model_version": ml["metadata"].get("model_version")},
        )
    except (FileNotFoundError, ValueError) as exc:
        # La app arranca igual y /ready responde 503. Un contenedor que muere
        # al arrancar no deja leer el log del error en la nube.
        ml["error"] = str(exc)
        logger.critical("model_load_failed", extra={"error": str(exc)})
    yield
    ml.clear()


app = FastAPI(
    title="API de cancelacion de reservas",
    version=model_version(),
    summary="Predice si una reserva de hotel sera cancelada",
    lifespan=lifespan,
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Correlaciona log <-> respuesta: X-Request-ID entra o se genera, y sale."""
    rid = new_request_id(request.headers.get("x-request-id"))
    response = await call_next(request)
    response.headers["X-Request-ID"] = rid
    return response


def _campo(col, descripcion):
    minimo, maximo, unidad = NUMERIC_RANGES[col]
    return Field(..., ge=minimo, le=maximo, description=f"{descripcion} ({unidad})")


class PredictRequest(BaseModel):
    # extra="forbid": sin esto, un campo mal escrito se ignora en silencio y
    # predices con el valor por defecto.
    model_config = {
        "extra": "forbid",
        "json_schema_extra": {
            "examples": [
                {
                    "no_of_adults": 2,
                    "no_of_children": 0,
                    "no_of_weekend_nights": 1,
                    "no_of_week_nights": 2,
                    "required_car_parking_space": 0,
                    "lead_time": 224,
                    "arrival_month": 10,
                    "arrival_date": 2,
                    "repeated_guest": 0,
                    "no_of_previous_cancellations": 0,
                    "no_of_previous_bookings_not_canceled": 0,
                    "avg_price_per_room": 65.0,
                    "no_of_special_requests": 0,
                    "type_of_meal_plan": "Meal Plan 1",
                    "room_type_reserved": "Room_Type 1",
                    "market_segment_type": "Offline",
                }
            ]
        },
    }

    no_of_adults: int = _campo("no_of_adults", "Adultos")
    no_of_children: int = _campo("no_of_children", "Ninios")
    no_of_weekend_nights: int = _campo("no_of_weekend_nights", "Noches de fin de semana")
    no_of_week_nights: int = _campo("no_of_week_nights", "Noches entre semana")
    required_car_parking_space: int = _campo("required_car_parking_space", "Pide parqueadero")
    lead_time: int = _campo("lead_time", "Dias entre la reserva y la llegada")
    arrival_month: int = _campo("arrival_month", "Mes de llegada")
    arrival_date: int = _campo("arrival_date", "Dia del mes de llegada")
    repeated_guest: int = _campo("repeated_guest", "Huesped recurrente")
    no_of_previous_cancellations: int = _campo(
        "no_of_previous_cancellations", "Cancelaciones previas"
    )
    no_of_previous_bookings_not_canceled: int = _campo(
        "no_of_previous_bookings_not_canceled", "Reservas previas no canceladas"
    )
    avg_price_per_room: float = _campo("avg_price_per_room", "Precio medio por habitacion")
    no_of_special_requests: int = _campo("no_of_special_requests", "Peticiones especiales")

    type_of_meal_plan: Literal["Meal Plan 1", "Meal Plan 2", "Meal Plan 3", "Not Selected"]
    room_type_reserved: Literal[
        "Room_Type 1",
        "Room_Type 2",
        "Room_Type 3",
        "Room_Type 4",
        "Room_Type 5",
        "Room_Type 6",
        "Room_Type 7",
    ]
    market_segment_type: Literal["Online", "Offline", "Corporate", "Complementary", "Aviation"]

    @model_validator(mode="after")
    def _reglas_cruzadas(self):
        if self.no_of_adults + self.no_of_children < 1:
            raise ValueError("no_of_adults + no_of_children debe ser >= 1: no hay huespedes")
        maximo = DIAS_POR_MES[self.arrival_month]
        if self.arrival_date > maximo:
            raise ValueError(
                f"arrival_date {self.arrival_date} no existe en el mes {self.arrival_month} "
                f"(maximo {maximo})"
            )
        return self


class PredictResponse(BaseModel):
    model_config = {"protected_namespaces": ()}

    prediction: Literal["Canceled", "Not_Canceled"]
    probability: float = Field(..., ge=0, le=1, description="Probabilidad de cancelacion")
    threshold: float = Field(..., ge=0, le=1)
    model_version: str
    request_id: str


class HealthResponse(BaseModel):
    status: str


class ReadyResponse(BaseModel):
    ready: bool
    detail: str | None = None


@app.exception_handler(Exception)
async def _errores_no_previstos(request: Request, exc: Exception):
    # Red de seguridad para errores fuera del camino de prediccion (el fallo
    # del pipeline ya se registra como prediction_failed en el handler).
    # El traceback va al log, nunca a la respuesta.
    request_id = request_id_var.get()
    logger.error(
        "unhandled_error",
        exc_info=True,
        extra={"path": request.url.path, "status": "error"},
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Error interno", "request_id": request_id},
    )


@app.exception_handler(RequestValidationError)
async def _errores_de_contrato(request: Request, exc: RequestValidationError):
    """422 del contrato: se registra que campo se rompio, con nivel WARNING."""
    campos = sorted({str(error["loc"][-1]) for error in exc.errors()})
    logger.warning("input_invalid", extra={"path": request.url.path, "fields": campos})
    # Mismo formato que el default de FastAPI: no rompe a los consumidores.
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(exc.errors())})


@app.get("/health", response_model=HealthResponse, tags=["salud"])
def health():
    """Liveness. No toca el modelo a proposito: un modelo lento no debe hacer
    que la plataforma reinicie un contenedor sano."""
    return {"status": "ok"}


@app.get("/ready", response_model=ReadyResponse, tags=["salud"])
def ready():
    """Readiness. Es lo que decide si nos mandan trafico."""
    if "pipeline" not in ml:
        return JSONResponse(
            status_code=503,
            content={"ready": False, "detail": ml.get("error", "modelo no cargado")},
        )
    return {"ready": True, "detail": None}


@app.get("/model-info", tags=["modelo"])
def model_info():
    """Que modelo produjo esta prediccion."""
    if "metadata" not in ml:
        raise HTTPException(status_code=503, detail="Modelo no disponible")
    return ml["metadata"]


@app.post("/predict", response_model=PredictResponse, tags=["modelo"])
def predict(peticion: PredictRequest):
    if "pipeline" not in ml:
        raise HTTPException(status_code=503, detail="Modelo no disponible")

    version = ml["metadata"].get("model_version") or model_version()
    inicio = time.perf_counter()
    payload = peticion.model_dump()

    try:
        with stage("to_frame"):
            # DataFrame de una fila con el ORDEN congelado: sklearn valida por
            # nombre con DataFrame, pero por posicion con array. Mandarlas
            # desordenadas daria predicciones erroneas sin lanzar excepcion.
            fila = pd.DataFrame([payload], columns=FEATURE_ORDER)
        with stage("predict"):
            probabilidad = float(ml["pipeline"].predict_proba(fila)[0, 1])
        with stage("postprocess"):
            umbral = float(ml["metadata"].get("threshold", 0.5))
            etiqueta = "Canceled" if probabilidad >= umbral else "Not_Canceled"
    except Exception:
        logger.error(
            "prediction_failed",
            exc_info=True,
            extra={"model_version": version, "features_hash": features_hash(payload)},
        )
        raise

    latencia_ms = round((time.perf_counter() - inicio) * 1000, 3)
    logger.info(
        "prediction",
        extra={
            "model_version": version,
            "features_hash": features_hash(payload),
            "prediction": etiqueta,
            "probability": round(probabilidad, 4),
            "threshold": umbral,
            "latency_ms": latencia_ms,
            "stage_ms": stages_ms(),
            "status": "success",
        },
    )
    # Evento separado con los valores del contrato: es la entrada del job de
    # drift. Aqui no hay PII, por eso los valores salen completos.
    logger.info(
        "prediction_features",
        extra={"model_version": version, **safe_features(payload)},
    )
    return {
        "prediction": etiqueta,
        "probability": round(probabilidad, 4),
        "threshold": umbral,
        "model_version": version,
        "request_id": request_id_var.get(),
    }


# Guarda: los Literal de arriba duplican src/config.py por legibilidad de /docs.
# Este chequeo evita que se desincronicen sin que nadie se entere.
assert set(PredictRequest.model_fields["type_of_meal_plan"].annotation.__args__) == set(
    CATEGORICAL_LEVELS["type_of_meal_plan"]
)
