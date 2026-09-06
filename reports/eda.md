# EDA - Cancelacion de reservas de hotel

Dataset: `ahsan81/hotel-reservations-classification-dataset` (Kaggle, CC BY 4.0).
36.275 filas, 19 columnas. Reproducible con `uv run python -m src.eda`.

## Las cinco preguntas

**1. Que representa una fila?** Una reserva individual, identificada por `Booking_ID`.

**2. Cual es el target y como esta balanceado?** `booking_status`: 67,24% `Not_Canceled`,
32,76% `Canceled`.

**3. Que columnas son inutilizables?** `Booking_ID` (identificador) y `arrival_year`
(no generaliza). Ninguna constante, ningun candidato a leakage.

**4. Cual es el rango valido de cada columna?** Ver `src/config.py`, seccion
`NUMERIC_RANGES`, derivada de los minimos y maximos observados.

**5. Hay estructura temporal?** Si, y es fuerte. Obliga a split temporal.

---

## Hallazgos y decisiones

### Metrica

**Hallazgo:** el target esta 67/33. **Interpretacion:** un clasificador trivial que
siempre responde `Not_Canceled` alcanza 67,24% de accuracy sin aprender nada.
**Decision:** `accuracy` no se reporta como metrica principal. Se usan ROC-AUC y PR-AUC,
y el umbral se elige por costo de negocio, no en 0,5.

### Deriva temporal (el hallazgo mas importante)

**Hallazgo:** la tasa de cancelacion pasa de 2,37% (dic-2017 y ene-2018) a 46,55%
(ago-2018). Por anio: 14,75% en 2017 (6.514 filas) contra 36,71% en 2018 (29.761 filas).
**Interpretacion:** no es ruido, es un cambio de regimen; probablemente un cambio de
politica de cancelacion o de mezcla de canales. Un split aleatorio mezclaria 2018 dentro
del entrenamiento y daria una metrica optimista imposible de sostener en produccion.
**Decision:** split **temporal** por fecha de llegada. Se reportan las dos metricas
(split aleatorio y split temporal) para dejar explicita la diferencia.

### `arrival_year` no se usa como feature

**Hallazgo:** correlaciona 0,180 con la cancelacion, pero solo toma dos valores
(2017, 2018). **Interpretacion:** esa correlacion es la deriva anterior disfrazada de
señal. En produccion llega 2019 y el modelo nunca lo vio. **Decision:** se descarta como
feature; se conserva unicamente para construir el split temporal.

### Fechas que no existen

**Hallazgo:** 37 filas con fecha de llegada `2018-02-29`. 2018 no fue bisiesto.
**Interpretacion:** error de captura, no un patron. **Decision:** contrato de
entrenamiento tolerante (se descartan las 37 filas, 0,1%, con warning); contrato de
inferencia estricto (una fecha imposible devuelve 422). Son los dos contratos separados.

### Precio 0: no es un faltante

**Hallazgo:** 545 filas con `avg_price_per_room = 0`. De esas, 354 son del segmento
`Complementary` (de 391 que existen) y 191 son `Online`. Su tasa de cancelacion es 1,10%
contra 32,76% general. **Interpretacion:** son reservas de cortesia. El cero es
informativo, no un dato perdido: imputarlo con la mediana borraria la señal mas limpia
del dataset. **Decision:** se conservan tal cual y se agrega la bandera binaria
`es_cortesia`.

### Reservas con 0 adultos

**Hallazgo:** 139 filas con `no_of_adults = 0`. **Interpretacion:** las 139 tienen al
menos un niño, asi que no son reservas vacias: el conteo de adultos se cargo mal.
**Decision:** el rango valido admite 0 adultos, pero el contrato agrega una regla
cruzada: `no_of_adults + no_of_children >= 1`.

### Filas identicas

**Hallazgo:** 10.275 filas duplicadas si se ignora `Booking_ID`, en 3.138 grupos; el
grupo mayor tiene 91 filas identicas. **Interpretacion:** con 17 atributos de baja
cardinalidad, dos reservas distintas pueden coincidir en todo; parecen reservas de grupo
cargadas por separado, no un error de carga. Pero si el mismo grupo queda partido entre
train y test, la metrica se infla. **Decision:** no se eliminan. El split temporal las
mantiene del mismo lado, porque comparten fecha de llegada. Riesgo documentado.

### Categorias raras

**Hallazgo:** `Meal Plan 3` (0,01%, ~5 filas), `Room_Type 3` (0,02%), `Aviation` (0,34%).
**Interpretacion:** la tasa de cancelacion de 20% de `Meal Plan 3` sale de 5 casos: no
significa nada. **Decision:** `OneHotEncoder(handle_unknown="ignore")`, obligatorio para
que una categoria nueva en produccion no tumbe el endpoint con un 500.

### Sesgo e imputacion

**Hallazgo:** `no_of_previous_cancellations` (25,2), `lead_time` (1,29) y otras seis
tienen sesgo fuerte. **Decision:** imputador de **mediana** para numericas, dentro del
Pipeline, con `fit` solo sobre train.

### Sin leakage ni redundancia

**Hallazgo:** la correlacion mas alta con el target es `lead_time` (0,439); ninguna
supera 0,8. Ningun par de predictoras supera 0,7 entre si. **Interpretacion:** no hay
columna que sea el target disfrazado ni columnas repetidas. **Decision:** no se descarta
nada por esta via. El poder predictivo tendra que salir del modelo, no de una fuga.

---

## Figuras

`reports/figures/`: `01_target_balance.png`, `02_lead_time.png`,
`03_cancelacion_por_mes.png`. No se versionan (`.gitignore`); se regeneran con
`uv run python -m src.eda`.
