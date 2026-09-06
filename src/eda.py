"""Analisis exploratorio reproducible.

Regla: cada hallazgo tiene que terminar en una decision. Este modulo se
puede volver a correr entero con `uv run python -m src.eda`.

Salida por consola en ASCII puro: nada de flechas unicode ni palomitas,
que truenan con UnicodeEncodeError en terminales cp1252.
"""

import matplotlib

matplotlib.use("Agg")  # backend sin ventana: plt.show() bloquea en CI y en Docker

import matplotlib.pyplot as plt
import pandas as pd

from src.config import FIGURES_DIR, ID_COLUMN, TARGET
from src.data import load_raw


def _text_columns(df):
    """Columnas de texto.

    pandas 3 las tipa como 'str' y pandas 2 como 'object'; select_dtypes("object")
    funciona en ambos pero emite Pandas4Warning. is_string_dtype cubre los dos.
    """
    return [c for c in df.columns if pd.api.types.is_string_dtype(df[c])]


def _titulo(texto):
    print("\n" + "=" * 70)
    print(texto)
    print("=" * 70)


def summary_structure(df):
    """Pregunta 1: que representa una fila, y que columnas son inutilizables."""
    _titulo("1. ESTRUCTURA")
    print(f"filas: {len(df)}  columnas: {df.shape[1]}")
    print(f"filas duplicadas (todas las columnas): {df.duplicated().sum()}")
    sin_id = df.drop(columns=[ID_COLUMN])
    print(f"duplicadas ignorando {ID_COLUMN}: {sin_id.duplicated().sum()}")

    nun = df.nunique().sort_values()
    constantes = nun[nun == 1].index.tolist()
    identificadores = nun[nun == len(df)].index.tolist()
    print(f"\ncolumnas constantes (nunique == 1): {constantes or 'ninguna'}")
    print(f"identificadores (nunique == n_filas): {identificadores or 'ninguno'}")

    print("\ncardinalidad por columna:")
    for col, n in nun.items():
        print(f"  {col:38} {n:>6}  {df[col].dtype}")
    return {"constantes": constantes, "identificadores": identificadores}


def target_balance(df):
    """Pregunta 2: cual es el target y como esta balanceado."""
    _titulo("2. TARGET")
    prop = df[TARGET].value_counts(normalize=True)
    for etiqueta, p in prop.items():
        print(f"  {etiqueta:20} {p:6.2%}  ({df[TARGET].value_counts()[etiqueta]} filas)")
    mayoritaria = prop.max()
    print(f"\nclasificador trivial (siempre la clase mayoritaria): {mayoritaria:.2%} de accuracy")
    print("=> accuracy NO es una metrica honesta aqui. Usar ROC-AUC y PR-AUC.")
    return prop


def missing_report(df):
    """Pregunta: faltantes, incluidos los disfrazados."""
    _titulo("3. FALTANTES")
    nulos = (df.isna().mean() * 100).sort_values(ascending=False)
    con_nulos = nulos[nulos > 0]
    print("nulos declarados (isna):", "ninguno" if con_nulos.empty else "")
    for col, p in con_nulos.items():
        print(f"  {col:38} {p:5.2f}%")

    print("\nfaltantes disfrazados (isna no los ve):")
    for col in df.select_dtypes("number").columns:
        if col == TARGET:
            continue
        ceros = (df[col] == 0).sum()
        negativos = (df[col] < 0).sum()
        if ceros or negativos:
            print(f"  {col:38} ceros={ceros:>6}  negativos={negativos:>4}")
    centinelas = {"SIN DATO", "-", "N/A", "NA", "?", ""}
    for col in _text_columns(df):
        encontrados = set(df[col].astype(str).unique()) & centinelas
        if encontrados:
            print(f"  {col:38} centinelas de texto: {encontrados}")


def numeric_profile(df):
    """Pregunta 4: rango valido de cada numerica. Alimenta src/schema.py."""
    _titulo("4. NUMERICAS: rangos observados")
    num = df.select_dtypes("number")
    desc = num.describe().T[["min", "25%", "50%", "75%", "max", "mean", "std"]]
    print(desc.to_string(float_format=lambda x: f"{x:10.2f}"))
    print("\nasimetria (>1 o <-1 = sesgo fuerte: usar mediana como imputador):")
    for col, s in num.skew().sort_values(ascending=False).items():
        marca = "  <-- sesgo fuerte" if abs(s) > 1 else ""
        print(f"  {col:38} {s:7.2f}{marca}")


def categorical_profile(df):
    """Cardinalidad y colas raras de las categoricas."""
    _titulo("5. CATEGORICAS: niveles")
    for col in _text_columns(df):
        if col in (ID_COLUMN, TARGET):
            continue
        vc = df[col].value_counts(normalize=True)
        print(f"\n{col}  ({len(vc)} niveles)")
        for nivel, p in vc.items():
            raro = "  <-- cola rara (<1%)" if p < 0.01 else ""
            print(f"  {nivel!s:28} {p:6.2%}{raro}")


def bivariate_vs_target(df):
    """Pregunta 5 y leakage: que se asocia al target."""
    _titulo("6. RELACION CON EL TARGET")
    y = (df[TARGET] == "Canceled").astype(int)

    print("numericas: correlacion puntual con Canceled=1")
    corr = df.select_dtypes("number").corrwith(y).sort_values(key=abs, ascending=False)
    for col, c in corr.items():
        alarma = "  <-- SOSPECHOSO: revisar leakage" if abs(c) > 0.8 else ""
        print(f"  {col:38} {c:6.3f}{alarma}")

    print("\ncategoricas: tasa de cancelacion por nivel")
    for col in _text_columns(df):
        if col in (ID_COLUMN, TARGET):
            continue
        tab = pd.crosstab(df[col], df[TARGET], normalize="index")["Canceled"].sort_values()
        print(f"\n  {col}")
        for nivel, tasa in tab.items():
            print(f"    {nivel!s:28} {tasa:6.2%}")

    print("\nredundancia entre predictoras (|corr| > 0.7):")
    c = df.select_dtypes("number").corr().abs()
    encontrado = False
    for i in range(len(c)):
        for j in range(i + 1, len(c)):
            if c.iloc[i, j] > 0.7:
                print(f"  {c.index[i]} ~ {c.columns[j]}: {c.iloc[i, j]:.3f}")
                encontrado = True
    if not encontrado:
        print("  ninguna")


def temporal_check(df):
    """Pregunta 5: hay estructura temporal? Decide split aleatorio vs temporal."""
    _titulo("7. ESTRUCTURA TEMPORAL")
    fecha = pd.to_datetime(
        dict(year=df["arrival_year"], month=df["arrival_month"], day=df["arrival_date"]),
        errors="coerce",
    )
    invalidas = fecha.isna().sum()
    print(f"fechas de llegada invalidas (no existen en el calendario): {invalidas}")
    if invalidas:
        malas = df.loc[fecha.isna(), ["arrival_year", "arrival_month", "arrival_date"]]
        print("  combinaciones invalidas encontradas:")
        for combo, n in malas.value_counts().items():
            print(f"    {combo[0]}-{combo[1]:02d}-{combo[2]:02d}  x{n}")

    print(f"\nrango de fechas: {fecha.min().date()} a {fecha.max().date()}")
    print("\ntasa de cancelacion por mes de llegada:")
    tmp = df.assign(_f=fecha).dropna(subset=["_f"])
    por_mes = tmp.groupby(tmp["_f"].dt.to_period("M"))[TARGET].apply(
        lambda s: (s == "Canceled").mean()
    )
    for periodo, tasa in por_mes.items():
        barra = "#" * int(tasa * 50)
        print(f"  {periodo}  {tasa:6.2%}  {barra}")
    return fecha


def plot_findings(df, fecha):
    """Pocas figuras, cada una responde una pregunta escrita."""
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(5, 3.5))
    df[TARGET].value_counts().plot.bar(ax=ax, color=["#4c72b0", "#c44e52"])
    ax.set_title("Balance del target")
    ax.set_ylabel("reservas")
    fig.savefig(FIGURES_DIR / "01_target_balance.png", dpi=120, bbox_inches="tight")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(6, 3.5))
    for etiqueta, grupo in df.groupby(TARGET):
        ax.hist(grupo["lead_time"], bins=50, alpha=0.6, label=etiqueta)
    ax.set_title("lead_time por estado de la reserva")
    ax.set_xlabel("dias entre reserva y llegada")
    ax.legend()
    fig.savefig(FIGURES_DIR / "02_lead_time.png", dpi=120, bbox_inches="tight")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(6, 3.5))
    tmp = df.assign(_f=fecha).dropna(subset=["_f"])
    serie = tmp.groupby(tmp["_f"].dt.to_period("M"))[TARGET].apply(
        lambda s: (s == "Canceled").mean()
    )
    serie.plot(ax=ax, marker="o")
    ax.set_title("Tasa de cancelacion por mes")
    ax.set_ylabel("proporcion cancelada")
    fig.savefig(FIGURES_DIR / "03_cancelacion_por_mes.png", dpi=120, bbox_inches="tight")
    plt.close(fig)

    print(f"\nfiguras guardadas en {FIGURES_DIR}")


def main():
    df = load_raw()
    summary_structure(df)
    target_balance(df)
    missing_report(df)
    numeric_profile(df)
    categorical_profile(df)
    bivariate_vs_target(df)
    fecha = temporal_check(df)
    plot_findings(df, fecha)


if __name__ == "__main__":
    main()
