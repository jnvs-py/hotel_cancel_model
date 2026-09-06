"""Entrenamiento end-to-end: carga -> limpieza -> split -> entrenar -> guardar.

Salida en ASCII puro: nada de simbolos que truenen en una terminal cp1252.
"""

import pandas as pd
from sklearn.model_selection import train_test_split

from src.clean import clean_training
from src.config import (
    DATA_PATH,
    MODELS_DIR,
    POSITIVE_LABEL,
    SEED,
    SPLIT_DATE,
    TARGET,
    TEST_SIZE,
    VAL_DATE,
)
from src.data import load_raw
from src.evaluate import choose_threshold, evaluate
from src.model import build_pipeline, save_model
from src.schema import FEATURE_ORDER


def _xy(df):
    return df[FEATURE_ORDER], (df[TARGET] == POSITIVE_LABEL).astype(int)


def split_temporal(df):
    """Tres ventanas en orden cronologico: train, validacion y test.

    La validacion existe para elegir el umbral sin mirar el test. Entrenar con
    el pasado y evaluar con el futuro es lo que pasa en produccion.
    """
    fecha = df["arrival_full_date"]
    return (
        df[fecha < pd.Timestamp(VAL_DATE)],
        df[(fecha >= pd.Timestamp(VAL_DATE)) & (fecha < pd.Timestamp(SPLIT_DATE))],
        df[fecha >= pd.Timestamp(SPLIT_DATE)],
    )


def main():
    print("Cargando datos...")
    df = clean_training(load_raw())
    print(f"  filas validas: {len(df)}")

    # 1) Split temporal: la evaluacion honesta, por la deriva del EDA.
    tr_t, va_t, te_t = split_temporal(df)
    Xtr, ytr = _xy(tr_t)
    Xva, yva = _xy(va_t)
    Xte, yte = _xy(te_t)
    print(f"\nSplit temporal (validacion {VAL_DATE}, test {SPLIT_DATE}):")
    print(f"  train     : {len(Xtr):>6} filas, tasa de cancelacion {ytr.mean():.2%}")
    print(f"  validacion: {len(Xva):>6} filas, tasa de cancelacion {yva.mean():.2%}")
    print(f"  test      : {len(Xte):>6} filas, tasa de cancelacion {yte.mean():.2%}")

    pipeline = build_pipeline().fit(Xtr, ytr)
    # El umbral se elige en validacion; el test no se toca hasta el reporte.
    umbral = choose_threshold(yva, pipeline.predict_proba(Xva)[:, 1])
    proba_t = pipeline.predict_proba(Xte)[:, 1]
    metricas_temporal = evaluate(yte, proba_t, umbral)

    # 2) Split aleatorio: solo para mostrar cuanto miente respecto al anterior.
    Xr, yr = _xy(df)
    Xtr_r, Xte_r, ytr_r, yte_r = train_test_split(
        Xr, yr, test_size=TEST_SIZE, random_state=SEED, stratify=yr
    )
    proba_r = build_pipeline().fit(Xtr_r, ytr_r).predict_proba(Xte_r)[:, 1]
    metricas_aleatorio = evaluate(yte_r, proba_r, umbral)

    print("\nMetricas (split temporal = la honesta):")
    for nombre, m in (("temporal", metricas_temporal), ("aleatorio", metricas_aleatorio)):
        print(
            f"  {nombre:10} ROC-AUC {m['roc_auc']:.4f}  PR-AUC {m['pr_auc']:.4f}  "
            f"F1 {m['f1']:.4f}  precision {m['precision']:.4f}  recall {m['recall']:.4f}"
        )
    dif = metricas_aleatorio["roc_auc"] - metricas_temporal["roc_auc"]
    print(f"  el split aleatorio infla el ROC-AUC en {dif:+.4f}")
    print(f"\nUmbral elegido en validacion (max F1): {umbral:.2f}")
    c = metricas_temporal["confusion"]
    print(f"  matriz: tn={c['tn']} fp={c['fp']} fn={c['fn']} tp={c['tp']}")

    # El artefacto final se entrena con TODO el historial disponible.
    final = build_pipeline().fit(Xr, yr)
    metadata = save_model(
        final,
        {"temporal": metricas_temporal, "aleatorio": metricas_aleatorio},
        DATA_PATH,
        MODELS_DIR,
        umbral,
        len(Xr),
    )
    print(f"\nArtefacto guardado en {MODELS_DIR}")
    print(f"  version {metadata['model_version']}, sklearn {metadata['sklearn_version']}")
    print(f"  features: {len(metadata['features'])}")


if __name__ == "__main__":
    main()
