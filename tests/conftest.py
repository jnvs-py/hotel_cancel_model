"""Fixtures compartidas.

El modelo real vive en models/, que esta en .gitignore: en CI no existe. Los
tests entrenan un pipeline diminuto sobre datos sinteticos para no depender de
el y para que la suite tarde segundos.
"""

import numpy as np
import pandas as pd
import pytest

from src.config import CATEGORICAL_LEVELS, NUMERIC_RANGES, TARGET
from src.model import build_pipeline, save_model
from src.schema import FEATURE_ORDER

N = 120


@pytest.fixture
def datos_sinteticos():
    rng = np.random.default_rng(0)
    fila = {}
    for col, (minimo, maximo, _) in NUMERIC_RANGES.items():
        fila[col] = rng.integers(minimo, max(minimo + 1, min(maximo, 5)) + 1, N)
    fila["avg_price_per_room"] = rng.uniform(0, 200, N)
    fila["arrival_month"] = rng.integers(1, 13, N)
    fila["arrival_date"] = rng.integers(1, 29, N)
    fila["no_of_adults"] = rng.integers(1, 4, N)
    for col, niveles in CATEGORICAL_LEVELS.items():
        fila[col] = rng.choice(niveles, N)
    df = pd.DataFrame(fila)
    df["arrival_year"] = 2018
    df["Booking_ID"] = [f"INN{i:05d}" for i in range(N)]
    # Target con senial real: lead_time alto cancela mas.
    df[TARGET] = np.where(df["lead_time"] > 2, "Canceled", "Not_Canceled")
    return df


@pytest.fixture
def fila_valida(datos_sinteticos):
    return datos_sinteticos.iloc[0][FEATURE_ORDER].to_dict()


@pytest.fixture
def modelo_entrenado(tmp_path, datos_sinteticos):
    """Entrena y guarda un artefacto diminuto en un directorio temporal."""
    X = datos_sinteticos[FEATURE_ORDER]
    y = (datos_sinteticos[TARGET] == "Canceled").astype(int)
    pipeline = build_pipeline().fit(X, y)
    save_model(
        pipeline,
        {"temporal": {"roc_auc": 1.0}},
        __file__ and tmp_path / "d.csv" if False else _csv_falso(tmp_path),
        tmp_path,
        0.5,
        len(X),
    )
    return tmp_path


def _csv_falso(tmp_path):
    ruta = tmp_path / "datos.csv"
    ruta.write_text("a,b\n1,2\n", encoding="utf-8")
    return ruta
