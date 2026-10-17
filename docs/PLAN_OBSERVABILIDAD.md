# Plan de observabilidad y monitoreo del modelo

> Informe escrito el 2026-09-12. NO implementa codigo: es la especificacion
> para ejecutarse, siguiendo la **Fase 9** del agente `u_cientifico_datos` y
> las skills `u_observabilidad_ml` (capa de sistema) y `u_monitoreo_drift`
> (capa de modelo).

## Por que existe esta fase

Un modelo desplegado puede **fallar en silencio**: sigue respondiendo `200 OK`
mientras sus predicciones pierden calidad. Desplegar no es terminar; terminar
es poder contestar, con datos y sin entrar al contenedor:

1. Esta vivo?
2. Es rapido?
3. Falla?
4. Que modelo respondio esta peticion?
5. Siguen siendo buenas sus predicciones?

Hoy el proyecto responde (4) parcialmente (via `/model-info` y `model_version`
en la respuesta) y ninguna de las otras con evidencia recolectada.

## Diagnostico: que tiene hoy el proyecto

| Pilar | Estado actual | Evidencia |
|---|---|---|
| Logs estructurados | **No.** `logging` basico en texto plano, sin `timestamp`/`event`/campos | `src/api.py:22,46,51,164` |
| `request_id` | **Parcial.** Se genera por request pero no viaja en logs ni en header; solo en el cuerpo de la respuesta y del 500 | `src/api.py:162,167,213` |
| Metricas | **No existe** `/metrics` ni `prometheus-client` | `pyproject.toml` (deps) |
| Tiempos por etapa | **No.** No hay medicion de `validate`/`predict`/`postprocess` | - |
| Auditoria (traza por prediccion) | **No.** No se emite evento persistente por prediccion | - |
| Baseline del modelo | **No existe** `models/baseline.json` | `models/` solo tiene `pipeline.joblib` + `metadata.json` |
| Drift | **No existe** job ni modulo | `src/monitoring/` no existe |
| Alertas y runbook | **No existen** | `monitoring/` no existe |
| Niveles de log con significado | **Parcial.** Se usa `info`/`error` pero sin `WARNING` para 422 ni `CRITICAL` para fallo de carga | `src/api.py:46,51` |
| PII en logs | **N/A (ventaja).** El dataset de reservas no contiene PII: no hay nombres, correos ni documentos | `data/hotel_reservations.csv` |

**Ventaja estructural de este proyecto:** al no haber PII, las features del
contrato pueden registrarse como valores (no solo como hash) en el evento de
auditoria, lo que hace el job de drift directo, sin capa de anonimizacion.

## Brechas contra la Fase 9

| Requisito de la fase | Brecha |
|---|---|
| Una linea JSON por evento a stdout, con esquema completo | Falta `src/observability.py` y cablearlo en el `lifespan` |
| `request_id` en log + header + cuerpo de error | Falta el header `X-Request-ID` y su propagacion al log |
| RED + metricas de ML en `/metrics` | Falta todo |
| `models/baseline.json` escrito en cada entrenamiento | Falta en `src/model.py`/`src/train.py` |
| Job batch de drift con reporte y estado por feature | Falta `src/monitoring/drift.py` y `reports/drift/` |
| Alertas con `for:` y un runbook por alerta | Faltan `monitoring/alert_rules.yml` y `monitoring/RUNBOOK.md` |
| Secciones "Observabilidad" y "Monitoreo del modelo" en README | Faltan |
| Tests de logging, hash, request_id, metricas, PSI | Falta `tests/test_observability.py` y `tests/test_drift.py` |

Nota de secuencia: la Fase 5 del agente pide que la API **nazca instrumentada**.
Este proyecto la construyo sin instrumentar, asi que esta fase se aplica de
forma retroactiva. Es deuda tecnica consciente, no un defecto de diseno.

---

## Arquitectura propuesta

```
                          +-------------------------------+
   POST /predict  ------->|  src/api.py                   |
   X-Request-ID?          |  lifespan: configure_logging  |
                          |  + stage("validate"/"predict")|
                          +------+---------------+--------+
                                 |               |
                    stdout: JSON por evento      +--> /metrics (Prometheus)
                                 |                      |
                     +-----------+-----------+          |
                     |  plataforma de logs   |          |
                     |  (cloud o local)      |          |
                     +-----------+-----------+          |
                                 |                      |
                                 v                      v
                     +----------------------------------------+
                     | job batch: src/monitoring/drift.py     |
                     | lee ventana de produccion + baseline    |
                     | -> reports/drift/<fecha>.{json,md}      |
                     | -> metricas ml_feature_psi publicables  |
                     +----------------------------------------+
                                        ^
                     models/baseline.json (congelado en entrenamiento)
```

**Regla dura:** el drift se calcula en batch, fuera del request. Calcular PSI
dentro de `/predict` agrega latencia y no tiene sentido estadistico con una
sola fila.

---

## Etapa 1 - Logs JSON + request_id (dia 1, sin infraestructura)

### Archivos

1. **`src/observability.py`** (nuevo) — base en la plantilla
   `~/.agents/skills/u_observabilidad_ml/templates/observability.py`:
   - `JsonFormatter` que emite una linea JSON por evento
   - `configure_logging()` idempotente, nivel por `LOG_LEVEL` (default `INFO`)
   - `features_hash(features)` con `hashlib.sha256(json.dumps(..., sort_keys=True))`
     — **nunca** `hash()` de Python (aleatorizado por proceso)
   - `new_request_id(incoming)` que respeta el header entrante
   - Context manager `stage(name)` con `time.perf_counter()` (monotono)
   - `stages_ms()` para volcar `stage_ms` al log
2. **`src/api.py`**:
   - Llamar `configure_logging()` **dentro del `lifespan`** (`src/api.py:42-53`)
   - Middleware que lee/crea `X-Request-ID` y lo devuelve en el header de
     **toda** respuesta
   - `logger.warning` en los 422 (hoy no se registran) y `logger.critical`
     si el artefacto no carga (`src/api.py:51` pasa de `error` a `critical`)
   - Silenciar el access log de uvicorn o convertirlo a JSON (si no, dos
     formatos mezclados en el mismo stream)
   - No instrumentar `/health`, `/ready`, `/metrics`: el scrape y los probes
     ahogarian la senial

### Esquema de log (el mismo en todos los eventos del servicio)

| Campo | Ejemplo en este proyecto | Para que |
|---|---|---|
| `timestamp` | `2026-09-12T15:04:05.123Z` (UTC ISO 8601) | Ordenar, unir con etiquetas reales |
| `level` | `INFO` | Filtrar |
| `event` | `prediction` | Nombre estable, no una frase |
| `request_id` | UUID4 o el `X-Request-ID` entrante | Unir log <-> metrica <-> 500 <-> etiqueta real |
| `model_name` | `hotel-cancel` | Identificar el servicio |
| `model_version` | `0.1.0+55ca619` | Saber que modelo respondio (ya derivado por CI) |
| `features_hash` | sha256[:16] de las 16 features | Auditar sin exponer el payload completo |
| `prediction` | `Not_Canceled` | Distribucion de salidas |
| `probability` | `0.0923` | Distribucion de scores (primera senial de drift) |
| `threshold` | `0.67` | Reconstruir la decision |
| `latency_ms` | `12.4` | p50/p90/p99 desde logs si no hay Prometheus |
| `stage_ms` | `{"validate":0.3,"predict":11.6}` | Cuello de botella por etapa |
| `status` | `success` / `error` | Tasa de error |

### Niveles concretos en este proyecto

| Nivel | Cuando |
|---|---|
| `INFO` | `prediction` exitosa; `model_loaded` en el arranque |
| `WARNING` | 422 por contrato violado (el campo culpable va en el evento); `avg_price_per_room` en el borde del rango |
| `ERROR` | Excepcion en `/predict` (500), con `exc_info=True` |
| `CRITICAL` | El artefacto no carga (`/ready` en 503) |

### Que nunca va al log

- El authtoken de ngrok, la futura `API_KEY`, cabeceras completas
- El array crudo de features **en cada request**; se registran las features
  del contrato solo en el evento separado `prediction_features` que alimenta
  el drift (aqui no hay PII, asi que es viable)
- Tracebacks en la respuesta HTTP: solo en el log

**Retencion propuesta:** 30 dias de eventos `prediction` en la plataforma de
logs; los reportes de drift que justifican una decision se citan en el
CHANGELOG y se versionan en `reports/`.

---

## Etapa 2 - `/metrics` (RED + ML)

Agregar `prometheus-client` a `dependencies` (lo importa la API, no es dev).

| Metrica | Tipo | Labels (baja cardinalidad) | Responde |
|---|---|---|---|
| `ml_requests_total` | Counter | `model_version`, `status` | RPS y tasa de error |
| `ml_request_latency_seconds` | Histogram | `model_version` | p50/p90/p99 |
| `ml_stage_latency_seconds` | Histogram | `stage` | Que etapa es lenta |
| `ml_prediction_score` | Histogram | `model_version` | Distribucion de probabilidades |
| `ml_predictions_total` | Counter | `model_version`, `label` | Proporcion de clases predichas |
| `ml_input_invalid_total` | Counter | `field` | Que campo del contrato se rompe mas (422) |
| `ml_model_loaded` | Gauge | `model_version` | 1/0 |

**Buckets** alineados al SLO local observado (prediccion ~12 ms):
`0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1`.

**Prohibido como label:** `request_id`, `features_hash`, `Booking_ID`, valores
de features. Alta cardinalidad va a los logs.

**Nota de plataforma:** Cloud Run / Container Apps / App Runner no hacen
scrape. Para la primera version basta la opcion 1 de la skill: **metricas
derivadas de logs** (cada evento `prediction` ya lleva `latency_ms` y
`status`). `/metrics` se expone igual para local y el docker compose de demo.

---

## Etapa 3 - Alertas y runbook

**`monitoring/alert_rules.yml`** (plantilla de la skill) con estos umbrales
iniciales, a ajustar con el negocio:

| Alerta | Condicion | `for:` | Severidad |
|---|---|---|---|
| `ModeloNoCargado` | `ml_model_loaded == 0` | 2 min | critica |
| `TasaDeErrorAlta` | `5xx / total > 2%` | 5 min | critica |
| `LatenciaAlta` | `p99 > 1 s` | 10 min | alta |
| `InputsInvalidosAltos` | `422 / total > 10%` | 15 min | media |
| `DerivaDePrediccion` | proporcion de `Canceled` fuera de [baseline +/- 10 pp] | 1 h | media |
| `DriftDeEntrada` | `ml_feature_psi > 0.2` en feature prioritaria | 1 h | media, abre ticket |

**`monitoring/RUNBOOK.md`**: una seccion por alerta con significado, como
confirmarla, que hacer y responsable. Una alerta sin accion se borra.

Regla: el drift **no reentrena solo**. Abre ticket o dispara un reentrenamiento
que pasa por todas las fases (contrato, evaluacion, nuevo baseline, despliegue)
y se compara contra el modelo vigente.

---

## Etapa 4 - Baseline y job de drift

### 4.1 `models/baseline.json` en el entrenamiento

Extender `save_model` (`src/model.py:59`) para escribir, junto al artefacto:

- **Numericas** (por feature del contrato): n, media, desviacion, min, max,
  cuantiles p01/p10/p50/p90/p99, proporcion de nulos, **bordes de bins**
  (deciles del entrenamiento) y proporcion de cada bin
- **Categoricas**: proporcion de cada nivel del contrato + `__otra__` + nulos
- **Predicciones**: histograma de `probability` sobre el set de validacion y
  tasa de clase positiva
- **Metrica de validacion** (la misma de `metadata.json`) y el mismo
  `model_version` + `dataset_hash`

**Los bins se fijan en el entrenamiento.** Recalcularlos en produccion haria
que el PSI mida ruido.

### 4.2 `src/monitoring/drift.py` (nuevo, plantilla de la skill)

```
uv run python -m src.monitoring.drift --actual <ventana.csv> [--minimo 500]
```

- Lee la ventana de produccion (export del log `prediction_features` o CSV)
- **Calidad de entrada primero**: nulos, feature constante (> 99% un valor),
  fuera de rango, categorias nuevas (`__otra__`), saltos de unidad
- **Drift por feature**: PSI (principal) + KS para numericas / Chi-cuadrado o
  JS para categoricas; epsilon `1e-4` en los bins vacios
- **Umbrales PSI**: `< 0.1` estable - `0.1-0.2` vigilar - `> 0.2` drift
- **Ventana minima 500 filas**; por debajo el reporte dice `insuficiente`
- Salida: `reports/drift/<fecha>.json` + `.md` con estado por feature
  (`ok` / `vigilar` / `drift` / `insuficiente`), metricas publicables
  `ml_feature_psi{feature=...}` y **exit code != 0** si hay drift
- Dependencias (`scipy`, y `evidently` si se usa) en un grupo aparte
  `monitoring`, **no** en la imagen que sirve predicciones

### 4.3 Priorizacion por importancia (ya medida)

El drift no pesa igual en todas las features. Orden de vigilancia segun la
importancia por permutacion medida sobre la ventana de test:

| Prioridad | Feature | Peso |
|---|---|---|
| 1 | `lead_time` | 45.1% |
| 2 | `no_of_special_requests` | 20.2% |
| 3 | `avg_price_per_room` | 9.9% |
| 4 | `market_segment_type` | 9.4% |
| 5 | `arrival_month` | 8.7% |
| ultima | resto (incluidas `no_of_previous_*`, peso 0) | < 2% |

Drift en una feature con peso 0 es un dato, no una alerta.

### 4.4 Frecuencia y politica

| Aspecto | Propuesta |
|---|---|
| Frecuencia del job | Diario (cron / Cloud Scheduler / `schedule` de GitHub Actions) sobre la ventana de 24 h |
| Ventana minima | 500 filas; si no, `insuficiente` (no un numero) |
| Umbral de alerta | PSI > 0.2 en features prioridad 1-5 |
| Reentrenamiento | Manual, aprobado, pasando por todas las fases; sube `model_version`, regenera `baseline.json` y se cita en el CHANGELOG |
| Monitoreo del job | Si el job no corrio, eso tambien es una alerta (el drift no visto es drift) |

---

## Etapa 5 - Performance con etiquetas reales

Cuando existan etiquetas (`booking_status` real de reservas maduras):

1. Unir por `request_id` (por eso el log de auditoria lo lleva)
2. Calcular la **misma metrica** de `metadata.json` (F1 con umbral 0.67, ROC-AUC)
   por ventana y por `model_version`
3. Comparar contra validacion con margen acordado (ej. caida > 5 pp en F1)
4. Respetar el retraso de las etiquetas: reportar solo ventanas maduras

---

## Tests a agregar

`tests/test_observability.py`:

```python
def test_prediction_emite_log_json(client, caplog): ...        # esquema completo
def test_log_no_contiene_secretos(client, caplog): ...         # ni API_KEY ni authtoken
def test_features_hash_estable(): ...                          # mismo input -> mismo hash
def test_request_id_se_propaga(client): ...                    # header entrante = log = respuesta
def test_error_500_tiene_request_id_sin_traceback(client): ...
def test_metrics_expone_contadores(client): ...                # ml_requests_total tras /predict
```

`tests/test_drift.py`:

```python
def test_psi_cero_para_misma_distribucion(): ...
def test_psi_alto_para_distribucion_desplazada(): ...          # desplazar 2 sigma -> PSI > 0.2
def test_psi_no_es_infinito_con_bin_vacio(): ...
def test_categoria_nueva_cae_en_otra(): ...
def test_ventana_pequenia_reporta_insuficiente(): ...
def test_baseline_se_escribe_con_model_version(): ...
```

---

## Impacto en infraestructura existente

| Componente | Cambio |
|---|---|
| `pyproject.toml` | `+prometheus-client` en `dependencies`; grupo `monitoring` con `scipy` (y `evidently` si se adopta) |
| `.github/workflows/ci.yml` | El smoke test puede afirmar `/metrics` tras un `/predict` |
| `.gitignore` | `+reports/drift/` (salvo el reporte que justifique una decision) |
| `Dockerfile` | Sin cambios: el logging a stdout ya es la invariante 6 |
| `requirements.txt` | Regenerar con `uv export` (respaldo sin UV) |

---

## Anti-alcance (lo que NO se hace)

- No montar ELK + Prometheus + Grafana + Evidently + OpenTelemetry el dia 1
- No calcular drift dentro del request
- No usar labels de alta cardinalidad en metricas
- No escribir logs a archivo (el contenedor no persiste disco)
- No reentrenar automaticamente por una alerta de drift

---

## Criterio de terminado (de la Fase 9)

1. Cada `/predict` emite **una** linea JSON con el esquema completo y sin secretos
2. El `request_id` aparece en log, header de respuesta y cuerpo del 500
3. `/metrics` expone las metricas RED y de ML
4. `models/baseline.json` existe y se regenera en cada entrenamiento
5. `uv run python -m src.monitoring.drift` detecta un drift simulado y **no**
   lo marca sobre una muestra del propio entrenamiento
6. Hay `monitoring/alert_rules.yml` y un runbook por alerta
7. El README tiene las secciones **"Observabilidad"** y **"Monitoreo del modelo"**

---

*Escrito para el Hito 1 siguiendo el agente `u_cientifico_datos` (Fase 9) y las
skills `u_observabilidad_ml` y `u_monitoreo_drift`.*
