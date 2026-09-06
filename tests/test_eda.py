"""El EDA es codigo, no celdas: tiene que poder volver a correr sin intervencion."""

import re

import src.eda as eda


def test_eda_completo_corre_y_genera_figuras(monkeypatch, tmp_path, datos_sinteticos, capsys):
    monkeypatch.setattr(eda, "load_raw", lambda: datos_sinteticos)
    monkeypatch.setattr(eda, "FIGURES_DIR", tmp_path)
    eda.main()

    figuras = sorted(p.name for p in tmp_path.glob("*.png"))
    assert figuras == [
        "01_target_balance.png",
        "02_lead_time.png",
        "03_cancelacion_por_mes.png",
    ]


def test_salida_del_eda_es_ascii_puro(monkeypatch, tmp_path, datos_sinteticos, capsys):
    """Un caracter no-ASCII truena con UnicodeEncodeError en terminales cp1252."""
    monkeypatch.setattr(eda, "load_raw", lambda: datos_sinteticos)
    monkeypatch.setattr(eda, "FIGURES_DIR", tmp_path)
    eda.main()
    salida = capsys.readouterr().out
    no_ascii = re.findall(r"[^\x00-\x7F]", salida)
    assert not no_ascii, f"caracteres no ASCII en la salida: {set(no_ascii)}"


def test_estructura_detecta_el_identificador(datos_sinteticos, capsys):
    hallazgos = eda.summary_structure(datos_sinteticos)
    assert "Booking_ID" in hallazgos["identificadores"]
    assert hallazgos["constantes"] == ["arrival_year"]


def test_target_balance_suma_uno(datos_sinteticos, capsys):
    assert abs(eda.target_balance(datos_sinteticos).sum() - 1.0) < 1e-9


def test_columnas_de_texto_no_incluyen_numericas(datos_sinteticos):
    texto = eda._text_columns(datos_sinteticos)
    assert "type_of_meal_plan" in texto
    assert "lead_time" not in texto
