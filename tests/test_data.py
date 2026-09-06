"""Carga de datos: los errores tienen que decir que hacer."""

import pytest

from src.data import load_raw


def test_falla_indicando_como_descargar(tmp_path):
    with pytest.raises(FileNotFoundError, match=r"kaggle\.com"):
        load_raw(tmp_path / "no_existe.csv")


def test_falla_si_el_csv_esta_vacio(tmp_path):
    ruta = tmp_path / "vacio.csv"
    ruta.write_text("a,b\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no tiene filas"):
        load_raw(ruta)


def test_lee_un_csv_valido(tmp_path):
    ruta = tmp_path / "ok.csv"
    ruta.write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
    assert len(load_raw(ruta)) == 2
