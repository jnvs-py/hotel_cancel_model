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
| `GET /metrics` | Metricas Prometheus (RED + ML). El scrape no se cuenta como prediccion. |
| `GET /app` | Formulario de demostracion (frontend autocontenido, mismo origen). |
| `GET /` | Redirige a `/app`. |

## Docker

```bash
docker build --platform linux/amd64 -t hito1-reservas:local .
docker run --rm -p 8000:8000 -e PORT=8000 hito1-reservas:local
```

El modelo se **entrena en la etapa de build** del Dockerfile (nunca en runtime):
la imagen se construye desde un clon limpio del repo, sin pasos previos, y es
determinista por `SEED`. El runtime solo recibe el artefacto ya entrenado, asi
que los datos y las dependencias de entrenamiento no llegan a la imagen final.
Para inyectar la version que deriva CI:

```bash
docker build --platform linux/amd64 \
  --build-arg MODEL_VERSION="0.1.0+abc1234" --build-arg FINGERPRINT="..." \
  -t hito1-reservas:local .
```

## Despliegue en Render

El servicio esta desplegado en Render (plan Free) con el formulario y la API en
la misma URL:

| | |
|---|---|
| **Formulario** | <https://hotel-cancel-model.onrender.com/app> |
| **Raiz** | <https://hotel-cancel-model.onrender.com> (redirige al formulario) |
| **API (Swagger)** | <https://hotel-cancel-model.onrender.com/docs> |
| **Health / metricas** | `/health`, `/ready`, `/metrics` |

El contenedor es el mismo `Dockerfile` de este repo: Render lo construye desde
un clon limpio de `main`, entrena el modelo en la etapa de build e inyecta el
SHA del commit como version (`0.1.0+e32b687` en el primer despliegue).

### Configuracion del servicio

| Ajuste | Valor |
|---|---|
| Runtime | Docker |
| Plan | Free (512 MB RAM, se duerme tras 15 min sin trafico) |
| Health Check Path | `/health` |
| Auto-Deploy | `After CI Checks Pass` (si el CI falla, no se despliega) |
| Environment | `MODEL_NAME=hotel-cancel`, `LOG_LEVEL=INFO` |

**Arranque en frio:** con el plan Free el contenedor se duerme y la primera
peticion tarda 30-90 s. Para una demo o sustentacion, abrir la URL unos minutos
antes y hacer un `curl /health` de calentamiento.

**Rollback:** desde el dashboard (Deploys -> Rollback) o con el CLI:
`render deploys` y `render logs`.

## Versionamiento del modelo

La version NO se edita a mano: la deriva CI en cada entrenamiento como
`{version de pyproject.toml}+{sha corto de git}` (ej. `0.1.0+a1b2c3d`). En
local sin la variable el artefacto queda marcado `0.1.0-dev`.

La `metadata.json` ademas trae un `fingerprint`: hash de todo lo que afecta al
entrenamiento (codigo en `src/`, dataset, `pyproject.toml` y `uv.lock`).

El workflow `.github/workflows/release.yml` corre en cada push a `main`:

1. Gate de calidad completo (lint + tests)
2. Entrena, construye la imagen versionada y le hace smoke test
3. Compara el fingerprint contra el ultimo release: si no cambio, no publica nada
4. Si cambio, publica un GitHub Release `model-0.1.0-a1b2c3d` con
   `pipeline.joblib` y `metadata.json` adjuntos

Para forzar un release nuevo basta un commit que toque codigo o datos.

## Observabilidad

> Guia completa de revision (logs, metricas y agregaciones, con comandos listos
> para copiar): **[docs/OBSERVABILIDAD_GUIA.md](docs/OBSERVABILIDAD_GUIA.md)**.

Cada peticion deja evidencia sin entrar al contenedor. Los eventos van a
**stdout como una linea JSON** (nunca a archivo: el disco del contenedor se
pierde) y el `X-Request-ID` entrante se propaga al log, al header de la
respuesta y al cuerpo del 500.

```bash
docker run --rm -p 8000:8000 -e PORT=8000 hito1-reservas:local
curl -s localhost:8000/metrics | grep ml_requests_total
```

Esquema del evento `prediction` (Contrato A completo en `src/observability.py`):

| Campo | Ejemplo | Para que |
|---|---|---|
| `timestamp`, `level`, `event` | `2026-10-17T19:18:36.820+00:00`, `INFO`, `prediction` | Ordenar y filtrar |
| `request_id` | `ddb7766f82c34b8aa684d6a229b963ad` | Correlacionar log, header, error y etiqueta real |
| `model_version` | `0.1.0+55ca619` | Que modelo respondio |
| `features_hash` | `392492b92cea206f` | Auditar sin guardar el payload; sha256 canonico, nunca `hash()` |
| `prediction`, `probability`, `threshold` | `Not_Canceled`, `0.0923`, `0.67` | Distribucion de salidas |
| `latency_ms`, `stage_ms` | `8.4`, `{"to_frame":0.7,"predict":7.5,"postprocess":0.0}` | Cuello de botella por etapa |
| `status` | `success` / `error` | Tasa de error desde los logs |

Eventos: `prediction`, `prediction_features` (valores del contrato, entrada del
job de drift), `model_loaded`, `input_invalid` (WARNING por campo),
`prediction_failed`, `model_load_failed` (CRITICAL), `unhandled_error`.

**Niveles:** `LOG_LEVEL` (default `INFO`). Un 422 es `WARNING`; un fallo de
carga del artefacto es `CRITICAL` y deja `/ready` en 503.

**Metricas** (`/metrics`): `ml_requests_total`, `ml_request_latency_seconds`,
`ml_stage_latency_seconds`, `ml_prediction_score`, `ml_predictions_total`,
`ml_input_invalid_total`, `ml_model_loaded`. Los labels son de baja
cardinalidad (version, status, stage, label, field): el `request_id` y el
`features_hash` van al log, nunca a una metrica.

**Sin secretos ni PII:** nunca se registran la `API_KEY` futura, cabeceras
crudas ni el cuerpo crudo de la peticion. El evento `prediction_features`
registra por valor las **16 columnas del contrato** (es la entrada del job de
drift); el dataset no contiene PII y `PII_FIELDS` en `src/observability.py` es
el interruptor para excluir cualquier columna sensible que se agregue.

**Retencion:** 30 dias de eventos `prediction` en la plataforma de logs. Los
eventos `prediction_features` (16 campos del contrato, ~300 bytes) son la
parte mas voluminosa: si el volumen lo justifica, se muestrean o se acorta su
retencion. Los reportes de drift que justifican una decision se citan en el
CHANGELOG.

**Costo:** reproducible con `uv run python scripts/medir_overhead.py [iteraciones]`,
que compara el pipeline solo contra el pipeline instrumentado (sin el stack
HTTP). Medido: p99 **+0.6 ms** sobre un pipeline de ~7-9 ms; presupuesto del
proyecto 5 ms. El script sale con codigo 1 si se pasa del presupuesto.

## Monitoreo del modelo

Un modelo puede degradarse sin dejar de responder 200. El monitoreo compara
la produccion contra `models/baseline.json`, que se congela en cada
entrenamiento con los **bins de la ventana de validacion** (si se recalcularan
en produccion, el PSI mediria ruido).

```bash
uv run python -m src.monitoring.drift --actual <ventana.csv> [--minimo 500]
```

- **PSI** por feature (principal) + **KS / Jensen-Shannon** (complemento).
  Umbrales: `< 0.1` estable, `0.1-0.2` vigilar, `> 0.2` drift.
- **Calidad de entrada antes que drift:** nulos al alza (> 5 pp), feature
  constante (> 99%), fuera del rango historico, categoria nueva creciendo.
- **Ventana minima 500 filas**: por debajo el reporte dice `insuficiente`,
  nunca un numero.
- Salidas: `reports/drift/<fecha>.json` y `.md` (no se versionan), un evento
  `drift_report` para metricas derivadas de logs, y **exit 1** si hay drift.
- **Prioriza por importancia:** la metadata del modelo trae
  `feature_importance` (permutation importance). En este dataset manda
  `lead_time` (~26%), seguido de `no_of_special_requests` (~14%) y
  `avg_price_per_room` (~11%). Drift en una feature con importancia 0 es un
  dato, no una alerta.
- El drift corre **en batch**, nunca dentro de `/predict`.

**Frecuencia propuesta:** diaria sobre la ventana de 24 h.

**Politica de reentrenamiento:** una alerta de drift **no reentrena sola**.
Abre ticket o dispara un reentrenamiento aprobado que pasa por contrato,
evaluacion y nuevo `baseline.json` antes de reemplazar el modelo vigente.

**Hallazgo del propio monitoreo:** una ventana del periodo de test ya marca
drift contra el baseline de validacion. Es exactamente la deriva temporal que
documenta el EDA (2,37% en dic-2017 a 46,55% en ago-2018): el job la detecta
en vez de esconderla.

Alertas y runbook: `monitoring/alert_rules.yml` y `monitoring/RUNBOOK.md`.
Panel operativo: `monitoring/dashboard.json` (5 secciones que son 5 preguntas:
esta vivo, es rapido, falla, como se distribuyen las predicciones, hay drift).

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
  observability.py  logging JSON, request_id y metricas Prometheus
  monitoring/drift.py  job de drift (PSI/KS) contra el baseline
reports/eda.md       hallazgos y decisiones (se versiona)
reports/drift/       reportes del job de drift (no se versionan)
models/              pipeline.joblib + metadata.json + baseline.json (NO se versionan)
monitoring/          alert_rules.yml, RUNBOOK.md, dashboard.json
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
