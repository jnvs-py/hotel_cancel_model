"""Tests del job de drift: PSI/KS, calidad de entrada, ventana minima y CLI.

Cubre el Contrato D y el AC-008: detecta el drift simulado y NO lo marca
sobre una muestra del propio entrenamiento.
"""

import json

import numpy as np
import pandas as pd
import pytest

import src.config as config
from src.model import build_baseline
from src.monitoring.drift import (
    MIN_ROWS,
    OTHER,
    compare_categorical,
    compare_numeric,
    drift_report,
    main,
    psi,
)


def _ventana(rng, n=2000, desplazamiento_lead_time=0.0, categorias_nuevas=False):
    leads = rng.normal(100, 30, n).clip(0, None) + desplazamiento_lead_time
    return pd.DataFrame(
        {
            "lead_time": np.round(leads, 3),
            "no_of_special_requests": rng.integers(0, 5, n),
            "room_type_reserved": rng.choice(
                ["Room_Type 9", "Room_Type 8"]
                if categorias_nuevas
                else ["Room_Type 1", "Room_Type 2"],
                n,
            ),
            "probability": rng.uniform(0, 1, n),
        }
    )


@pytest.fixture
def baseline_sintetico():
    """Baseline congelado a partir de una distribucion conocida."""
    rng = np.random.default_rng(7)
    X = _ventana(rng, n=1000).drop(columns=["probability"])
    return build_baseline(
        X,
        rng.uniform(0, 1, 1000),
        version="0.0.0-test",
        dataset_hash="hash-de-prueba",
        metric={"threshold": 0.5, "f1": 0.6},
        threshold=0.5,
    )


# ---------------------------------------------------------------------------
# PSI
# ---------------------------------------------------------------------------


def test_psi_es_cero_para_la_misma_distribucion():
    referencia = np.array([0.2, 0.3, 0.5])
    assert psi(referencia, referencia) < 1e-6


def test_psi_es_alto_para_una_distribucion_desplazada():
    referencia = np.array([0.7, 0.2, 0.1])
    actual = np.array([0.1, 0.2, 0.7])
    assert psi(referencia, actual) > 0.2


def test_psi_no_es_infinito_con_un_bin_vacio():
    """Un bin de produccion en cero no debe dar log(0): epsilon lo evita."""
    resultado = psi(np.array([0.5, 0.5, 0.0]), np.array([1.0, 0.0, 0.0]))
    assert np.isfinite(resultado)


# ---------------------------------------------------------------------------
# Comparaciones por tipo de feature
# ---------------------------------------------------------------------------


def test_ventana_igual_al_baseline_no_marca_drift(baseline_sintetico):
    """AC-008: la misma distribucion NO es drift."""
    reporte = drift_report(baseline_sintetico, _ventana(np.random.default_rng(11)))
    assert reporte["status"] == "ok"
    assert reporte["features"]["lead_time"]["status"] == "ok"


def test_ventana_desplazada_marca_drift(baseline_sintetico):
    """AC-008: desplazar la media 2 sigma es drift significativo."""
    ventana = _ventana(np.random.default_rng(7), desplazamiento_lead_time=60.0)
    reporte = drift_report(baseline_sintetico, ventana)
    assert reporte["status"] == "drift"
    assert reporte["features"]["lead_time"]["psi"] > 0.2
    assert reporte["features"]["lead_time"]["status"] == "drift"


def test_categoria_nueva_cae_en_otra(baseline_sintetico):
    """Una categoria no vista se cuenta como __otra__, no revienta."""
    resultado = compare_categorical(
        baseline_sintetico["features"]["room_type_reserved"],
        pd.Series(["Room_Type 99"] * 100 + ["Room_Type 1"] * 100),
    )
    assert resultado["new_category_rate"] == pytest.approx(0.5, abs=0.01)
    assert OTHER in baseline_sintetico["features"]["room_type_reserved"]["props"]


def test_feature_constante_se_reporta_como_problema_de_calidad(baseline_sintetico):
    """Sensor o join roto: lo detecta el chequeo de calidad, no el PSI solo."""
    ventana = _ventana(np.random.default_rng(7))
    ventana["lead_time"] = 100.0
    reporte = drift_report(baseline_sintetico, ventana)
    assert reporte["features"]["lead_time"]["constant"] is True
    assert "feature constante" in " ".join(reporte["features"]["lead_time"]["calidad"])


def test_nulos_al_alza_se_reportan_como_calidad(baseline_sintetico):
    ventana = _ventana(np.random.default_rng(7))
    ventana.loc[ventana.index[:200], "lead_time"] = np.nan
    resultado = compare_numeric(baseline_sintetico["features"]["lead_time"], ventana["lead_time"])
    assert resultado["null_rate"] > resultado["null_rate_ref"]


def test_ventana_pequenia_reporta_insuficiente(baseline_sintetico):
    """AC-008: con pocas filas no hay numero, hay 'insuficiente'."""
    reporte = drift_report(baseline_sintetico, _ventana(np.random.default_rng(7), n=100))
    assert reporte["status"] == "insuficiente"
    assert reporte["min_rows"] == MIN_ROWS
    assert "features" not in reporte


def test_el_reporte_ordena_por_importancia(baseline_sintetico):
    """La skill manda vigilar primero lo que al modelo le importa."""
    importancia = {"lead_time": 0.45, "no_of_special_requests": 0.2, "room_type_reserved": 0.0}
    reporte = drift_report(
        baseline_sintetico, _ventana(np.random.default_rng(11)), importancia=importancia
    )
    assert reporte["orden_por_importancia"][0] == "lead_time"
    assert reporte["features"]["lead_time"]["importancia"] == 0.45


def test_el_reporte_lleva_la_version_del_baseline(baseline_sintetico):
    reporte = drift_report(baseline_sintetico, _ventana(np.random.default_rng(11)))
    assert reporte["model_version"] == "0.0.0-test"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_falla_con_drift_y_escribe_los_reportes(
    tmp_path, monkeypatch, baseline_sintetico, capsys
):
    monkeypatch.setattr(config, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(config, "REPORTS_DIR", tmp_path / "reports")
    (tmp_path / "baseline.json").write_text(json.dumps(baseline_sintetico), encoding="utf-8")
    (tmp_path / "metadata.json").write_text(
        json.dumps({"feature_importance": {"lead_time": 0.45}}), encoding="utf-8"
    )
    # La ventana "ok" no debe fallar
    ok_csv = tmp_path / "ok.csv"
    _ventana(np.random.default_rng(11)).to_csv(ok_csv, index=False)
    assert main(["--actual", str(ok_csv)]) == 0
    # La desplazada si
    drift_csv = tmp_path / "drift.csv"
    _ventana(np.random.default_rng(7), desplazamiento_lead_time=60.0).to_csv(drift_csv, index=False)
    codigo = main(["--actual", str(drift_csv)])

    assert codigo == 1
    reportes = sorted((tmp_path / "reports" / "drift").glob("*.json"))
    assert len(reportes) == 2
    assert [(tmp_path / "reports" / "drift" / f"{p.stem}.md").exists() for p in reportes]
    evento = json.loads(
        [linea for linea in capsys.readouterr().out.splitlines() if "drift_report" in linea][-1]
    )
    assert evento["event"] == "drift_report"
    assert evento["prioritarias_en_drift"] == ["lead_time"]


def test_cli_avisa_si_no_hay_baseline(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(config, "REPORTS_DIR", tmp_path / "reports")
    ok_csv = tmp_path / "ok.csv"
    _ventana(np.random.default_rng(11)).to_csv(ok_csv, index=False)
    assert main(["--actual", str(ok_csv)]) == 2
    assert "No existe" in capsys.readouterr().out
