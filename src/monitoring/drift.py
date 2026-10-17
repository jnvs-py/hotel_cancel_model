"""Drift de una ventana de produccion contra el baseline del modelo.

Se corre FUERA del request, en batch: el PSI de una sola fila no tiene sentido
estadistico y calcularlo en /predict agregaria latencia.

    uv run python -m src.monitoring.drift --actual <ruta.csv> [--minimo 500]

Salidas: reports/drift/<fecha>.{json,md}, un evento JSON `drift_report` en
stdout para las metricas derivadas de logs, y exit code 1 si hay drift.

Salida de consola en ASCII puro.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
from scipy.spatial.distance import jensenshannon

from src.observability import configure_logging, logger

EPS = 1e-4
MIN_ROWS = 500
PSI_VIGILAR = 0.1
PSI_DRIFT = 0.2
OTHER = "__otra__"

# Chequeos de calidad de entrada (antes que el drift): muchas "degradaciones"
# son datos rotos, no el mundo cambiando.
NULL_DELTA_LIMIT = 0.05
OUT_OF_RANGE_LIMIT = 0.10
NEW_CATEGORY_LIMIT = 0.05
CONSTANT_RATE = 0.99


# ---------------------------------------------------------------------------
# Estadisticos
# ---------------------------------------------------------------------------


def psi(referencia: np.ndarray, actual: np.ndarray) -> float:
    """Population Stability Index. Epsilon evita el log(0) con un bin vacio."""
    ref = np.asarray(referencia, dtype=float) + EPS
    act = np.asarray(actual, dtype=float) + EPS
    ref, act = ref / ref.sum(), act / act.sum()
    return float(np.sum((act - ref) * np.log(act / ref)))


def _estado(valor: float) -> str:
    if valor > PSI_DRIFT:
        return "drift"
    if valor > PSI_VIGILAR:
        return "vigilar"
    return "ok"


def compare_numeric(perfil: dict, serie: pd.Series) -> dict:
    """PSI sobre los bins CONGELADOS del baseline + KS como complemento."""
    limpio = serie.dropna().astype(float)
    if not limpio.size:
        return {"status": "sin_datos"}
    # Los extremos abiertos viajan como null (JSON valido): se reconstruyen aqui.
    crudos = perfil["bin_edges"]
    bordes = np.array(
        [(-np.inf if i == 0 else np.inf) if b is None else float(b) for i, b in enumerate(crudos)]
    )
    conteos, _ = np.histogram(limpio, bins=bordes)
    actual = conteos / max(conteos.sum(), 1)
    valor = psi(perfil["bin_props"], actual)
    cuantiles = perfil["quantiles"]
    ks = stats.ks_2samp(perfil["sample"], limpio) if perfil.get("sample") and limpio.size else None
    top = limpio.value_counts(normalize=True).iloc[0]
    return {
        "psi": round(valor, 4),
        "ks_d": round(float(ks.statistic), 4) if ks is not None else None,
        "null_rate": round(float(serie.isna().mean()), 4),
        "null_rate_ref": perfil["null_rate"],
        "out_of_p01_p99": round(
            float(((limpio < cuantiles["p01"]) | (limpio > cuantiles["p99"])).mean()), 4
        ),
        "constant": bool(top > CONSTANT_RATE),
        "mean": round(float(limpio.mean()), 4),
        "mean_ref": perfil["mean"],
        "status": _estado(valor),
    }


def compare_categorical(perfil: dict, serie: pd.Series) -> dict:
    """PSI sobre las proporciones del contrato; lo no visto cae en __otra__."""
    referencia = perfil["props"]
    valores = serie.dropna().astype(str).where(lambda x: x.isin(referencia.keys()), OTHER)
    proporciones = valores.value_counts(normalize=True)
    claves = list(referencia.keys())
    ref_v = np.array([referencia[clave] for clave in claves])
    act_v = np.array([proporciones.get(clave, 0.0) for clave in claves])
    valor = psi(ref_v, act_v)
    return {
        "psi": round(valor, 4),
        "js": round(float(jensenshannon(ref_v + EPS, act_v + EPS)), 4),
        "null_rate": round(float(serie.isna().mean()), 4),
        "null_rate_ref": perfil["null_rate"],
        "new_category_rate": round(float(proporciones.get(OTHER, 0.0)), 4),
        "status": _estado(valor),
    }


def _calidad(resultado: dict) -> list[str]:
    """Chequeos de calidad de entrada: lo que paso el contrato pero es raro."""
    avisos = []
    if resultado.get("null_rate", 0) > resultado.get("null_rate_ref", 0) + NULL_DELTA_LIMIT:
        avisos.append("nulos al alza")
    if resultado.get("constant"):
        avisos.append("feature constante (>99% un valor): sensor o join roto")
    if resultado.get("out_of_p01_p99", 0) > OUT_OF_RANGE_LIMIT:
        avisos.append("fuera del rango historico")
    if resultado.get("new_category_rate", 0) > NEW_CATEGORY_LIMIT:
        avisos.append("categoria nueva creciendo")
    return avisos


def drift_report(
    baseline: dict,
    actual: pd.DataFrame,
    importancia: dict | None = None,
    minimo: int = MIN_ROWS,
) -> dict:
    """Compara la ventana contra el baseline. Ventana corta = insuficiente."""
    if len(actual) < minimo:
        return {"status": "insuficiente", "rows": len(actual), "min_rows": minimo}

    importancia = importancia or {}
    caracteristicas: dict[str, dict] = {}
    for columna, perfil in baseline["features"].items():
        if columna not in actual.columns:
            caracteristicas[columna] = {"status": "ausente"}
            continue
        resultado = (
            compare_numeric(perfil, actual[columna])
            if perfil["kind"] == "numeric"
            else compare_categorical(perfil, actual[columna])
        )
        resultado["calidad"] = _calidad(resultado)
        resultado["importancia"] = round(float(importancia.get(columna, 0.0)), 6)
        caracteristicas[columna] = resultado

    orden = sorted(caracteristicas, key=lambda c: -caracteristicas[c].get("importancia", 0.0))
    en_drift = [c for c, r in caracteristicas.items() if r.get("status") == "drift"]
    en_vigilar = [c for c, r in caracteristicas.items() if r.get("status") == "vigilar"]
    en_drift_prioritarias = [c for c in en_drift if caracteristicas[c].get("importancia", 0) > 0]

    reporte = {
        "model_version": baseline["model_version"],
        "generated_at": datetime.now(UTC).isoformat(),
        "rows": len(actual),
        "status": "drift" if en_drift else ("vigilar" if en_vigilar else "ok"),
        "features_en_drift": en_drift,
        "features_en_drift_prioritarias": en_drift_prioritarias,
        "features_en_vigilar": en_vigilar,
        "orden_por_importancia": orden,
        "features": caracteristicas,
    }
    if "probability" in actual.columns:
        prediccion = compare_numeric(baseline["prediction"], actual["probability"])
        reporte["prediction"] = prediccion
        # La deriva de la distribucion de PREDICCIONES es la senal mas barata:
        # cuenta para el estado general y para el exit code.
        if prediccion.get("status") == "drift" and reporte["status"] != "drift":
            reporte["status"] = "drift"
            reporte["features_en_drift"] = [*en_drift, "prediction"]
        elif prediccion.get("status") == "vigilar" and reporte["status"] == "ok":
            reporte["status"] = "vigilar"
    return reporte


# ---------------------------------------------------------------------------
# Reporte y CLI
# ---------------------------------------------------------------------------


def _escribir_markdown(reporte: dict, destino: Path) -> None:
    lineas = [
        f"# Reporte de drift — {reporte['model_version']}",
        "",
        f"- Generado: {reporte['generated_at']}",
        f"- Filas de la ventana: {reporte['rows']}",
        f"- Estado general: **{reporte['status']}**",
        "",
    ]
    if reporte["status"] == "insuficiente":
        lineas.append(
            f"Ventana por debajo del minimo ({reporte['min_rows']} filas): no se reporta numero."
        )
        destino.write_text("\n".join(lineas) + "\n", encoding="utf-8")
        return

    prioritarias = reporte["features_en_drift_prioritarias"]
    if prioritarias:
        lineas.append(f"Features prioritarias en drift: {', '.join(prioritarias)}.")
        lineas.append("")
    lineas += [
        "| Feature | Importancia | PSI | KS D | Estado | Calidad |",
        "|---|---|---|---|---|---|",
    ]
    for columna in reporte["orden_por_importancia"]:
        resultado = reporte["features"][columna]
        calidad = ", ".join(resultado.get("calidad", [])) or "-"
        ks = resultado.get("ks_d")
        lineas.append(
            f"| {columna} | {resultado.get('importancia', 0):.4f} | "
            f"{resultado.get('psi', '')} | {ks if ks is not None else ''} | "
            f"{resultado.get('status', '')} | {calidad} |"
        )
    lineas += [
        "",
        "Umbrales PSI: < 0.1 estable - 0.1-0.2 vigilar - > 0.2 drift.",
        "",
        "Decision: el drift NO reentrena solo. Abre ticket o dispara un",
        "reentrenamiento aprobado, que pasa por contrato, evaluacion y nuevo",
        "baseline antes de reemplazar el modelo vigente.",
    ]
    destino.write_text("\n".join(lineas) + "\n", encoding="utf-8")


def _publicar_en_pushgateway(reporte: dict, url: str) -> None:
    """Publica ml_feature_psi y ml_prediction_psi si hay Pushgateway.

    Sin Pushgateway (lo normal: la plataforma no hace scrape) la senal viaja
    en el evento `drift_report` y las metricas se derivan de los logs.
    """
    from prometheus_client import CollectorRegistry, Gauge, push_to_gateway

    registro = CollectorRegistry()
    por_feature = Gauge(
        "ml_feature_psi", "PSI por feature vs baseline", ["feature"], registry=registro
    )
    for columna, resultado in reporte.get("features", {}).items():
        if resultado.get("psi") is not None:
            por_feature.labels(feature=columna).set(float(resultado["psi"]))
    de_prediccion = Gauge(
        "ml_prediction_psi", "PSI de la distribucion de predicciones", registry=registro
    )
    psi_prediccion = reporte.get("prediction", {}).get("psi")
    if psi_prediccion is not None:
        de_prediccion.set(float(psi_prediccion))
    try:
        push_to_gateway(url, job="hito1-drift", registry=registro)
        print(f"Metricas publicadas en {url}")
    except Exception as exc:
        print(f"No se pudo publicar en el Pushgateway ({exc}): la senal queda en el evento JSON")


def _leer(ruta: Path) -> pd.DataFrame:
    if ruta.suffix == ".parquet":
        return pd.read_parquet(ruta)
    if ruta.suffix == ".jsonl":
        return pd.read_json(ruta, lines=True)
    return pd.read_csv(ruta)


def main(argv=None) -> int:
    from src.config import MODELS_DIR, REPORTS_DIR

    parser = argparse.ArgumentParser(description="Drift de una ventana contra el baseline")
    parser.add_argument("--actual", type=Path, required=True, help="ventana de produccion (csv)")
    parser.add_argument("--baseline", type=Path, default=MODELS_DIR / "baseline.json")
    parser.add_argument("--metadata", type=Path, default=MODELS_DIR / "metadata.json")
    parser.add_argument("--minimo", type=int, default=MIN_ROWS)
    parser.add_argument("--salida", type=Path, default=REPORTS_DIR / "drift")
    parser.add_argument(
        "--pushgateway",
        default=os.environ.get("PUSHGATEWAY_URL"),
        help="URL del Pushgateway para publicar ml_feature_psi (opcional)",
    )
    args = parser.parse_args(argv)
    configure_logging()

    if not args.baseline.exists():
        print(f"No existe {args.baseline}. Entrena primero: genera el baseline junto al artefacto.")
        return 2

    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    importancia = {}
    if args.metadata.exists():
        importancia = json.loads(args.metadata.read_text(encoding="utf-8")).get(
            "feature_importance", {}
        )

    reporte = drift_report(baseline, _leer(args.actual), importancia, minimo=args.minimo)

    args.salida.mkdir(parents=True, exist_ok=True)
    # Microsegundos en el sello: dos corridas en el mismo segundo no se pisan.
    sello = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    (args.salida / f"{sello}.json").write_text(json.dumps(reporte, indent=2), encoding="utf-8")
    _escribir_markdown(reporte, args.salida / f"{sello}.md")

    # Evento en el MISMO formato JSON del servicio: es la entrada de las
    # metricas derivadas de logs y de la alerta DriftDeEntrada.
    logger.info(
        "drift_report",
        extra={
            "model_version": reporte.get("model_version"),
            "status": reporte["status"],
            "feature_psi": {
                columna: resultado.get("psi")
                for columna, resultado in reporte.get("features", {}).items()
            },
            "prioritarias_en_drift": reporte.get("features_en_drift_prioritarias", []),
        },
    )

    if args.pushgateway:
        _publicar_en_pushgateway(reporte, args.pushgateway)

    print(f"Estado general: {reporte['status']} -> {args.salida}/{sello}.json")
    return 1 if reporte["status"] == "drift" else 0


if __name__ == "__main__":
    sys.exit(main())
