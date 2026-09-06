"""El contrato: cada regla que escribimos merece un test que la provoque."""

import pandas as pd
import pytest

from src.config import CATEGORICAL_LEVELS
from src.schema import (
    FEATURE_ORDER,
    add_derived_features,
    arrival_dates,
    derived_feature_names,
    invalid_reasons,
)


def test_fila_valida_no_tiene_razones(fila_valida):
    assert invalid_reasons(fila_valida) == []


def test_rechaza_fuera_de_rango(fila_valida):
    razones = invalid_reasons(dict(fila_valida, lead_time=-3))
    assert any("lead_time" in r and "fuera de rango" in r for r in razones)


def test_rechaza_categoria_desconocida(fila_valida):
    razones = invalid_reasons(dict(fila_valida, room_type_reserved="Room_Type 99"))
    assert any("room_type_reserved" in r for r in razones)


def test_rechaza_reserva_sin_huespedes(fila_valida):
    razones = invalid_reasons(dict(fila_valida, no_of_adults=0, no_of_children=0))
    assert any("huespedes" in r for r in razones)


def test_rechaza_valor_ausente(fila_valida):
    razones = invalid_reasons(dict(fila_valida, lead_time=None))
    assert any("falta el valor" in r for r in razones)


def test_bandera_cortesia_marca_precio_cero(fila_valida):
    df = pd.DataFrame([dict(fila_valida, avg_price_per_room=0.0)])
    assert add_derived_features(df)["es_cortesia"].iloc[0] == 1


def test_bandera_cortesia_no_marca_precio_normal(fila_valida):
    df = pd.DataFrame([dict(fila_valida, avg_price_per_room=95.0)])
    assert add_derived_features(df)["es_cortesia"].iloc[0] == 0


def test_fecha_inexistente_queda_nat():
    df = pd.DataFrame({"arrival_year": [2018], "arrival_month": [2], "arrival_date": [29]})
    assert arrival_dates(df).isna().all()


def test_nombres_derivados_agregan_una_columna():
    salida = derived_feature_names(None, ["a", "b"])
    assert salida == ["a", "b", "es_cortesia"]


@pytest.mark.parametrize("col", list(CATEGORICAL_LEVELS))
def test_categoricas_estan_en_el_orden_de_features(col):
    assert col in FEATURE_ORDER
