"""Carga del dataset crudo."""

import pandas as pd

from src.config import DATA_PATH


def load_raw(path=None):
    """Lee el CSV crudo sin transformar nada.

    Falla con un mensaje que dice como conseguir el archivo, no con un
    traceback de pandas.
    """
    path = path or DATA_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"No existe {path}. Descarga el dataset con:\n"
            "  curl -sL -o data.zip "
            "'https://www.kaggle.com/api/v1/datasets/download/"
            "ahsan81/hotel-reservations-classification-dataset'\n"
            "  unzip data.zip -d data/ && mv 'data/Hotel Reservations.csv' "
            f"{path}"
        )
    df = pd.read_csv(path)
    if df.empty:
        raise ValueError(f"{path} no tiene filas.")
    return df
