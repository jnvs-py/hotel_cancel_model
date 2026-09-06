"""Orquestacion: el split temporal es la decision estructural del proyecto."""

import pandas as pd

from src.clean import clean_training
from src.config import SPLIT_DATE, VAL_DATE
from src.train import _xy, split_temporal


def test_split_temporal_devuelve_tres_ventanas_en_orden(datos_sinteticos):
    limpio = clean_training(datos_sinteticos)
    train, val, test = split_temporal(limpio)
    assert len(train) + len(val) + len(test) == len(limpio)
    if len(train) and len(val):
        assert train["arrival_full_date"].max() < pd.Timestamp(VAL_DATE)
        assert val["arrival_full_date"].min() >= pd.Timestamp(VAL_DATE)
    if len(val) and len(test):
        assert val["arrival_full_date"].max() < pd.Timestamp(SPLIT_DATE)
        assert test["arrival_full_date"].min() >= pd.Timestamp(SPLIT_DATE)


def test_las_ventanas_no_se_solapan(datos_sinteticos):
    limpio = clean_training(datos_sinteticos)
    train, val, test = split_temporal(limpio)
    indices = set(train.index) | set(val.index) | set(test.index)
    assert len(indices) == len(limpio)


def test_xy_usa_el_orden_congelado_de_features(datos_sinteticos):
    from src.schema import FEATURE_ORDER

    X, y = _xy(clean_training(datos_sinteticos))
    assert list(X.columns) == FEATURE_ORDER
    assert set(y.unique()) <= {0, 1}
