# Hito 1 - Prediccion de cancelacion de reservas de hotel

API que predice si una reserva de hotel sera cancelada, empaquetada en Docker y
lista para desplegar en la nube.

Dataset: [`ahsan81/hotel-reservations-classification-dataset`](https://www.kaggle.com/datasets/ahsan81/hotel-reservations-classification-dataset)
(Kaggle, CC BY 4.0). 36.275 reservas, 19 columnas.

## Instalacion

Con UV (recomendado):

```bash
uv sync
uv run python -m src.train          # entrena y guarda models/
uv run uvicorn src.api:app --reload
```

Sin UV:

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m src.train
uvicorn src.api:app --reload
```

Documentacion interactiva en <http://127.0.0.1:8000/docs>.

## Uso

```bash
curl -X POST http://127.0.0.1:8000/predict \
  -H "Content-Type: application/json" \
  -d @tests/fixtures/request_ejemplo.json
```

```json
{"prediction": "Not_Canceled", "probability": 0.0923, "threshold": 0.67,
 "model_version": "0.1.0", "request_id": "dc1c9d6a-..."}
```

O con el cliente incluido: `uv run python -m src.client`.

| Endpoint | Que hace |
|---|---|
| `GET /health` | Liveness. Responde 200 sin tocar el modelo. |
| `GET /ready` | Readiness. 503 si el artefacto no cargo. |
| `POST /predict` | La prediccion. |
| `GET /model-info` | Version, metricas, features y umbral del modelo cargado. |

## Docker

```bash
uv run python -m src.train                              # el modelo es artefacto de BUILD
docker build --platform linux/amd64 -t hito1-reservas:local .
docker run --rm -p 8000:8000 -e PORT=8000 hito1-reservas:local
```

## Estructura

```
src/
  config.py    rutas, seed, rangos validos y niveles: unica fuente de verdad
  schema.py    contrato de datos y features derivadas
  data.py      carga del CSV crudo
  clean.py     clean_training (tolerante) y clean_prediction (estricta)
  eda.py       analisis exploratorio reproducible
  model.py     Pipeline de sklearn + save_model / load_model
  evaluate.py  metricas y eleccion de umbral
  train.py     orquestacion end-to-end
  api.py       FastAPI
  client.py    cliente de demostracion
reports/eda.md   hallazgos y decisiones (se versiona)
models/          artefacto entrenado (NO se versiona)
```

## Datos y columnas

13 numericas y 3 categoricas entran al modelo. Los rangos validos estan en
`src/config.py` y se derivaron del EDA.

| Columna | Tipo | Rango / niveles |
|---|---|---|
| `lead_time` | int | 0 a 443 dias |
| `avg_price_per_room` | float | 0 a 540 euros |
| `no_of_adults` / `no_of_children` | int | 0 a 4 / 0 a 10 |
| `no_of_weekend_nights` / `no_of_week_nights` | int | 0 a 7 / 0 a 17 |
| `arrival_month` / `arrival_date` | int | 1 a 12 / 1 a 31 |
| `required_car_parking_space`, `repeated_guest` | int | 0 o 1 |
| `no_of_previous_cancellations` | int | 0 a 13 |
| `no_of_previous_bookings_not_canceled` | int | 0 a 58 |
| `no_of_special_requests` | int | 0 a 5 |
| `type_of_meal_plan` | texto | 4 niveles |
| `room_type_reserved` | texto | 7 niveles |
| `market_segment_type` | texto | 5 niveles |

Derivada dentro del Pipeline: `es_cortesia` (precio igual a 0).

## Resultados

Evaluado con split temporal, que es lo que pasara en produccion:

| Split | ROC-AUC | PR-AUC | F1 | Precision | Recall |
|---|---|---|---|---|---|
| **Temporal (honesto)** | **0,8884** | 0,8521 | 0,6209 | 0,9383 | 0,4640 |
| Aleatorio (optimista) | 0,9468 | 0,9138 | 0,7800 | 0,9136 | 0,6806 |

El split aleatorio infla el ROC-AUC en 0,058. Se reportan los dos a proposito.

## Decisiones

**Split temporal en vez de aleatorio.** La tasa de cancelacion pasa de 14,75% en
2017 a 36,71% en 2018. Un split aleatorio mezcla el futuro en el entrenamiento y
da una metrica que no se sostiene en produccion. Hay tres ventanas cronologicas:
entrenamiento, validacion (hasta 2018-09-01) y test.

**El umbral se elige en validacion, nunca en test.** Elegirlo mirando el test
daba F1 0,747; el numero honesto es 0,621. El criterio actual es maximizar F1,
que es un default explicito: con los costos reales del hotel (perseguir una
reserva que no se iba a cancelar contra perder una que si) se reemplaza por el
criterio de negocio.

**`accuracy` no se reporta.** Con 67/33, un modelo que siempre responde
"no cancela" acierta el 67,24% sin aprender nada.

**`arrival_year` se descarta como feature.** Correlaciona con el target, pero solo
toma dos valores; en produccion llega 2019 y el modelo nunca lo vio. Se conserva
unicamente para construir el split.

**Precio 0 no es un faltante.** Las 545 reservas con precio 0 son de cortesia
(1,10% de cancelacion contra 32,76% general). Imputarlas con la mediana borraria
la senial mas limpia del dataset. Se conservan con la bandera `es_cortesia`.

**Imputacion con mediana, dentro del Pipeline.** Ocho columnas tienen sesgo
fuerte. El `fit` solo ve el train, por construccion: no hay fuga del test.

**Dos contratos de datos, no uno.** Entrenamiento tolerante (descarta las 37
filas con fecha 2018-02-29, que no existe, y avisa); inferencia estricta (422
nombrando el campo). Aplicar el estricto al entrenamiento tira datos utiles;
aplicar el tolerante a la API produce predicciones sobre basura.

**`handle_unknown="ignore"` en el encoder.** Una categoria nueva en produccion
seria un 500 sin eso.

**El CSV se versiona.** La licencia CC BY 4.0 lo permite, y evita depender de
credenciales de Kaggle en CI y en el build de Docker.

**El modelo es artefacto de build.** Se entrena antes del `docker build` y se
copia. Entrenar dentro del build lo vuelve lento, no reproducible y obliga a
meter el dataset en la imagen.

## Calidad

```bash
uv run ruff check . && uv run ruff format --check .
uv run pytest --cov=src --cov-fail-under=80
```

60 tests, 90% de cobertura.

---

*Construido con el agente `u_cientifico_datos`.*
