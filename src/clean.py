"""Los dos contratos: entrenamiento tolerante, inferencia estricta.

Confundirlos es el error clasico. Con el estricto en entrenamiento pierdes
medio dataset por tres filas raras; con el tolerante en la API, el cliente
manda basura, la imputas y devuelves una prediccion con cara de seria.
"""

import warnings

from src.config import TARGET
from src.schema import arrival_dates, invalid_reasons, required_columns


def clean_training(df):
    """Tolerante: descarta filas invalidas con warning.

    Falla duro solo ante fallas ESTRUCTURALES: columna ausente, sin filas, o
    todas las filas invalidas (eso no es un dato sucio, es el dataset equivocado).
    """
    faltantes = [c for c in required_columns(incluye_target=True) if c not in df.columns]
    if faltantes:
        raise ValueError(f"Faltan columnas obligatorias: {faltantes}. Llegaron: {list(df.columns)}")
    if df.empty:
        raise ValueError("El dataset no tiene filas.")

    n_inicial = len(df)
    df = df.copy()

    fechas = arrival_dates(df)
    n_fechas_malas = int(fechas.isna().sum())
    df = df.loc[fechas.notna()].copy()
    df["arrival_full_date"] = fechas.loc[fechas.notna()]

    validas = df.apply(lambda fila: not invalid_reasons(fila), axis=1)
    n_rango_malo = int((~validas).sum())
    df = df.loc[validas]

    descartadas = n_fechas_malas + n_rango_malo
    if descartadas:
        warnings.warn(
            f"clean_training descarto {descartadas} de {n_inicial} filas "
            f"({n_fechas_malas} con fecha inexistente, {n_rango_malo} fuera de contrato).",
            stacklevel=2,
        )

    if df.empty:
        raise ValueError(
            f"Las {n_inicial} filas violan el contrato. Revisa src/config.py: "
            "probablemente los rangos no corresponden a estos datos."
        )
    if TARGET in df.columns and df[TARGET].nunique() < 2:
        raise ValueError(f"El target '{TARGET}' quedo con una sola clase tras la limpieza.")
    return df


def clean_prediction(row):
    """Estricta: cualquier violacion del contrato es un error, no un descarte.

    Devuelve la fila lista para el pipeline. Los mensajes nombran la columna
    culpable y el valor recibido.
    """
    faltantes = [c for c in required_columns() if c not in row]
    if faltantes:
        raise ValueError(f"Faltan campos obligatorios: {faltantes}")

    razones = invalid_reasons(row)
    if razones:
        raise ValueError("; ".join(razones))
    return {c: row[c] for c in required_columns()}
