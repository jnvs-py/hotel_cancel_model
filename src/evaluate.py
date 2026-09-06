"""Metricas. accuracy no se reporta sola: con 67/33 un modelo trivial la gana."""

import numpy as np
from sklearn.metrics import (
    average_precision_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)


def evaluate(y_true, proba, threshold):
    y_pred = (proba >= threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(y_true, y_pred).ravel()
    return {
        "roc_auc": float(roc_auc_score(y_true, proba)),
        "pr_auc": float(average_precision_score(y_true, proba)),
        "precision": float(precision_score(y_true, y_pred, zero_division=0)),
        "recall": float(recall_score(y_true, y_pred, zero_division=0)),
        "f1": float(f1_score(y_true, y_pred, zero_division=0)),
        "threshold": float(threshold),
        "confusion": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
        "tasa_base": float(np.mean(y_true)),
    }


def choose_threshold(y_true, proba):
    """Umbral que maximiza F1.

    Es un default explicito, no 0,5 por inercia. Con los costos reales del
    hotel (cuanto cuesta perseguir una reserva que no se iba a cancelar contra
    perder una que si) este criterio se reemplaza por el de negocio.
    """
    candidatos = np.linspace(0.05, 0.95, 91)
    scores = [f1_score(y_true, (proba >= t).astype(int), zero_division=0) for t in candidatos]
    return round(float(candidatos[int(np.argmax(scores))]), 4)
