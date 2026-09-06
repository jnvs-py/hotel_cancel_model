"""Contrato de datos. Unica fuente de verdad sobre que es una fila valida.

Lo importan tanto el entrenamiento como la API, para que no puedan divergir.
Los rangos salen del EDA (fase 2), documentados en reports/eda.md.
"""

import pandas as pd

from src.config import CATEGORICAL_LEVELS, NUMERIC_RANGES, TARGET

# Bandera derivada: el EDA mostro que precio 0 no es un faltante sino una
# reserva de cortesia (1,10% de cancelacion contra 32,76% general).
# Se calcula DENTRO del pipeline, nunca en el endpoint.
DERIVED_FEATURES = ["es_cortesia"]

NUMERIC_FEATURES = list(NUMERIC_RANGES) + DERIVED_FEATURES
CATEGORICAL_FEATURES = list(CATEGORICAL_LEVELS)

# El ORDEN importa: es el que espera el pipeline entrenado. sklearn valida por
# nombre si recibe un DataFrame, pero por posicion si recibe un array.
FEATURE_ORDER = list(NUMERIC_RANGES) + CATEGORICAL_FEATURES

RAW_COLUMNS = FEATURE_ORDER


def add_derived_features(df):
    """Agrega las columnas derivadas. Va como primer paso del Pipeline."""
    df = df.copy()
    df["es_cortesia"] = (df["avg_price_per_room"] == 0).astype(int)
    return df


def derived_feature_names(transformer, input_features):
    """Nombres tras add_derived_features: los de entrada mas las derivadas.

    FunctionTransformer valida que get_feature_names_out coincida con la salida
    real; "one-to-one" no sirve aqui porque agregamos una columna.
    """
    return list(input_features) + DERIVED_FEATURES


def arrival_dates(df):
    """Fecha de llegada como datetime. NaT si la combinacion no existe.

    El EDA encontro 37 filas con 2018-02-29, que no existe: 2018 no fue bisiesto.
    """
    return pd.to_datetime(
        {"year": df["arrival_year"], "month": df["arrival_month"], "day": df["arrival_date"]},
        errors="coerce",
    )


def invalid_reasons(row):
    """Razones por las que una fila viola el contrato. Lista vacia = valida.

    Se usa igual en entrenamiento (para descartar) y en inferencia (para
    rechazar). Los mensajes nombran la columna y el valor recibido.
    """
    razones = []
    for col, (minimo, maximo, unidad) in NUMERIC_RANGES.items():
        valor = row.get(col)
        if valor is None or pd.isna(valor):
            razones.append(f"{col}: falta el valor")
        elif not (minimo <= valor <= maximo):
            razones.append(f"{col} fuera de rango [{minimo}, {maximo}] {unidad}: {valor}")

    for col, niveles in CATEGORICAL_LEVELS.items():
        valor = row.get(col)
        if valor not in niveles:
            razones.append(f"{col}: '{valor}' no es un nivel conocido {niveles}")

    # Regla cruzada: el EDA hallo 139 reservas con 0 adultos, todas con ninios.
    # Una reserva sin ninguna persona no existe.
    adultos = row.get("no_of_adults") or 0
    ninios = row.get("no_of_children") or 0
    if adultos + ninios < 1:
        razones.append("no_of_adults + no_of_children debe ser >= 1: la reserva no tiene huespedes")

    return razones


def required_columns(incluye_target=False):
    cols = list(RAW_COLUMNS)
    if incluye_target:
        cols.append(TARGET)
    return cols
