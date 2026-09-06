"""Metricas y eleccion de umbral."""

import numpy as np

from src.evaluate import choose_threshold, evaluate


def test_evaluate_devuelve_el_contrato_completo():
    y = np.array([0, 0, 1, 1])
    proba = np.array([0.1, 0.2, 0.8, 0.9])
    m = evaluate(y, proba, 0.5)
    assert set(m) == {
        "roc_auc",
        "pr_auc",
        "precision",
        "recall",
        "f1",
        "threshold",
        "confusion",
        "tasa_base",
    }
    assert m["roc_auc"] == 1.0
    assert m["confusion"] == {"tn": 2, "fp": 0, "fn": 0, "tp": 2}
    assert m["tasa_base"] == 0.5


def test_evaluate_no_truena_si_nadie_supera_el_umbral():
    """zero_division=0: con umbral alto la precision es 0, no un error."""
    y = np.array([0, 1, 1, 0])
    m = evaluate(y, np.array([0.1, 0.2, 0.3, 0.05]), 0.99)
    assert m["precision"] == 0.0
    assert m["recall"] == 0.0


def test_choose_threshold_separa_clases_bien_separadas():
    y = np.array([0] * 50 + [1] * 50)
    proba = np.concatenate([np.full(50, 0.1), np.full(50, 0.9)])
    umbral = choose_threshold(y, proba)
    assert 0.1 < umbral <= 0.9


def test_choose_threshold_no_devuelve_0_5_por_inercia():
    """El umbral sale de los datos, no de un default."""
    y = np.array([0] * 90 + [1] * 10)
    proba = np.concatenate([np.linspace(0.0, 0.3, 90), np.linspace(0.3, 0.4, 10)])
    assert choose_threshold(y, proba) != 0.5
