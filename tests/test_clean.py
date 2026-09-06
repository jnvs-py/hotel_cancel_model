"""Los dos contratos: tolerante en entrenamiento, estricto en inferencia."""

import pytest

from src.clean import clean_prediction, clean_training
from src.config import TARGET


def test_entrenamiento_descarta_fecha_inexistente(datos_sinteticos):
    datos_sinteticos.loc[0, ["arrival_month", "arrival_date"]] = [2, 29]
    with pytest.warns(UserWarning, match="descarto"):
        limpio = clean_training(datos_sinteticos)
    assert len(limpio) == len(datos_sinteticos) - 1


def test_entrenamiento_falla_si_falta_una_columna(datos_sinteticos):
    with pytest.raises(ValueError, match="Faltan columnas"):
        clean_training(datos_sinteticos.drop(columns=["lead_time"]))


def test_entrenamiento_falla_con_dataset_vacio(datos_sinteticos):
    with pytest.raises(ValueError, match="no tiene filas"):
        clean_training(datos_sinteticos.iloc[0:0])


def test_entrenamiento_falla_si_el_target_pierde_una_clase(datos_sinteticos):
    datos_sinteticos[TARGET] = "Canceled"
    with pytest.raises(ValueError, match="una sola clase"):
        clean_training(datos_sinteticos)


def test_inferencia_acepta_fila_valida(fila_valida):
    assert clean_prediction(fila_valida)["lead_time"] == fila_valida["lead_time"]


def test_inferencia_falla_si_falta_un_campo(fila_valida):
    del fila_valida["lead_time"]
    with pytest.raises(ValueError, match="Faltan campos"):
        clean_prediction(fila_valida)


def test_inferencia_nombra_la_columna_culpable(fila_valida):
    with pytest.raises(ValueError, match="lead_time"):
        clean_prediction(dict(fila_valida, lead_time=99999))
