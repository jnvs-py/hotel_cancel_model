# Plan de implementacion: observabilidad y monitoreo

**Estado:** Draft (pendiente de revision adversarial)
**Revision:** 2 (reestructurado de informe a plan de implementacion SDD + subagentes)
**Preparado para:** ejecucion por subagentes cold-start
**Aprobacion requerida antes de trabajo riesgoso:** No — el plan es aditivo; ver
la tabla de rollback por paso en la seccion de riesgo.

## Revision Log

| Rev | Fecha | Cambio | Razon |
|---|---|---|---|
| 1 | 2026-09-12 | Informe de observabilidad | Diagnostico y plan por etapas |
| 2 | 2026-09-12 | Reescritura como plan SDD + subagent-driven | Pedido del usuario: spec primero, pasos cold-executable, revision adversarial |

## Como leer este plan

- **Seccion 1 (SPEC):** la fuente de verdad. Nada se implementa que no este aqui.
  Los criterios son observables (`AC-NNN`) y cada uno nombra su verificacion.
- **Seccion 2 (Grafo):** que depende de que y que puede correr en paralelo.
- **Seccion 3 (Pasos):** cada paso es **cold-executable**: un subagente nuevo,
  sin contexto previo, puede ejecutarlo leyendo solo su bloque. Incluye brief,
  tareas, verificacion, criterio de salida y rollback.
- **Seccion 4 (Revision adversarial):** el gate que se corre antes de dar un
  paso por bueno, con el catalogo de anti-patrones de este dominio.
- **Seccion 5 (Mutacion):** como cambiar el plan sin perder el rastro.
- **Anexo A:** skills aplicables y que aporta cada una (revision pedida).

Convenciones del proyecto que todo paso respeta: `uv` para dependencias,
`ruff` limpio, cobertura >= 80%, ASCII puro en `print()` y mensajes de log,
firma `u_cientifico_datos` en commits.

---

# 1. SPEC

## 1.1 Goal

Cada prediccion servida por la API deja evidencia operativa y auditable (logs
JSON con `request_id`, metricas RED y de ML, baseline del modelo, job de drift
por lotes y alertas con runbook), de modo que se pueda responder sin entrar al
contenedor: esta vivo, es rapido, falla, que modelo respondio y si sus
predicciones siguen siendo buenas.

## 1.2 In scope

- Logs estructurados JSON a stdout con esquema unico y estable.
- `request_id` correlacionable entre log, header de respuesta y cuerpo de error.
- `/metrics` en formato Prometheus con metricas RED y de ML.
- `models/baseline.json` generado en cada entrenamiento.
- Job batch de drift con reporte por feature y metricas publicables.
- Alertas con umbrales y `for:`, mas runbook por alerta.
- Panel minimo operativo (dashboard JSON reproducible).
- Secciones de README, tests y gates de CI para todo lo anterior.

## 1.3 Out of scope (anti-alcance)

- Montar ELK + Prometheus + Grafana + Evidently + OpenTelemetry el dia 1.
- Calcular drift dentro del request.
- Reentrenamiento automatico disparado por una alerta.
- Implementar la `API_KEY` del plan de seguridad (plan separado).
- El despliegue en la nube en si (Fase 8); este plan solo prepara su
  instrumentacion y su verificacion post-despliegue.
- Guardar los eventos de prediccion en archivos locales o CSV.

## 1.4 Assumptions

- El dataset de reservas **no contiene PII**, por lo que las features del
  contrato pueden registrarse por valor en el evento de auditoria. Si en el
  futuro se agrega una feature con PII, debe entrar al hash y no al log.
- La plataforma de destino no hace scrape de `/metrics`; las metricas
  operativas de produccion se derivaran de logs. `/metrics` se mantiene para
  local y el docker compose de demo.
- Existe un consumidor que puede enviar `X-Request-ID`; si no lo envia, el
  servicio genera uno.

## 1.5 Dependencies

- `prometheus-client` (nueva dependencia de runtime: la importa la API).
- `scipy` en el grupo `monitoring` (job de drift, fuera de la imagen de la API).
- Plataforma de logs con retencion configurable para la etapa de produccion.

## 1.6 Context — hechos verificados del repositorio

| Pilar | Estado actual | Evidencia |
|---|---|---|
| Logs estructurados | Ausentes: `logging` basico en texto plano | `src/api.py:22,46,51,164` |
| `request_id` | Parcial: se genera por request, no viaja en log ni header | `src/api.py:162,167,213` |
| Metricas | No existe `/metrics` ni `prometheus-client` | `pyproject.toml` |
| Tiempos por etapa | Ausentes | - |
| Auditoria por prediccion | Ausente | - |
| Baseline del modelo | No existe `models/baseline.json` | `models/` |
| Drift | Ausente; no existe `src/monitoring/` | - |
| Alertas y runbook | Ausentes; no existe `monitoring/` | - |
| Niveles con significado | Parcial: sin `WARNING` para 422 ni `CRITICAL` para fallo de carga | `src/api.py:51` |
| PII en logs | No aplica: el dataset no tiene PII | `data/hotel_reservations.csv` |

Puntos de anclaje para los pasos: `src/api.py:42-53` (lifespan),
`src/api.py:160-168` (handler de errores), `src/api.py:198-214` (`/predict`),
`src/model.py:66` (`save_model`), `src/train.py:44-97` (`main`).

Importancia de features ya medida (permutation importance sobre test): se usa
para priorizar el drift.

| Prioridad | Feature | Peso |
|---|---|---|
| 1 | `lead_time` | 45.1% |
| 2 | `no_of_special_requests` | 20.2% |
| 3 | `avg_price_per_room` | 9.9% |
| 4 | `market_segment_type` | 9.4% |
| 5 | `arrival_month` | 8.7% |
| ultima | resto (incluye `no_of_previous_*`, peso 0) | < 2% |

## 1.7 Risk Review

| Area | Aplica | Manejo requerido |
|---|---|---|
| Seguridad / privacidad | Si | Nunca loguear `API_KEY`, el authtoken de ngrok, cabeceras completas ni tracebacks en la respuesta. AC-003 lo fija con test. |
| Datos persistentes | Si | `baseline.json` es aditivo: `load_model` no lo necesita, el artefacto actual sigue funcionando. Rollback = borrar el archivo. |
| Efectos externos / costo | Si | Volumen de logs y retencion se declaran en README; el job de drift corre en batch, no en request. |
| Compatibilidad de API | Si | Agregar el header `X-Request-ID` es aditivo; el cuerpo de exito no cambia. AC-011 protege la prediccion de referencia. |
| Rendimiento | Si | La instrumentacion no debe agregar latencia perceptible (presupuesto: < 5 ms p99 adicionales). AC-012 lo mide. |
| UX / accesibilidad | No | No hay interfaz de usuario nueva; el dashboard es para operador interno. |

## 1.8 Contratos (invariantes tecnicas)

### Contrato A — Esquema de log

Una linea JSON por evento. Campos minimos:

| Campo | Ejemplo | Nota |
|---|---|---|
| `timestamp` | `2026-09-12T15:04:05.123Z` | UTC, ISO 8601 |
| `level` | `INFO` | `LOG_LEVEL`, default `INFO` |
| `event` | `prediction` | Nombre estable (no una frase) |
| `request_id` | UUID4 o header entrante | Correlaciona log/metrica/error/etiqueta |
| `model_name` | `hotel-cancel` | - |
| `model_version` | `0.1.0+55ca619` | Ya derivado por CI |
| `features_hash` | sha256[:16] | Canonico: `sort_keys=True` |
| `prediction` | `Not_Canceled` | - |
| `probability` | `0.0923` | Distribucion de scores |
| `threshold` | `0.67` | Reconstruir la decision |
| `latency_ms` | `12.4` | Percentiles desde logs |
| `stage_ms` | `{"validate":0.3,"predict":11.6}` | Cuello de botella |
| `status` | `success` / `error` | Tasa de error |

Eventos definidos: `prediction` (por request), `prediction_features` (solo las
features del contrato, para el job de drift), `model_loaded` (arranque),
`prediction_failed` (ERROR con `exc_info`), `model_load_failed` (CRITICAL).

### Contrato B — Metricas

| Metrica | Tipo | Labels | Responde |
|---|---|---|---|
| `ml_requests_total` | Counter | `model_version`, `status` | RPS y tasa de error |
| `ml_request_latency_seconds` | Histogram | `model_version` | p50/p90/p99 |
| `ml_stage_latency_seconds` | Histogram | `stage` | Etapa lenta |
| `ml_prediction_score` | Histogram | `model_version` | Distribucion de probabilidades |
| `ml_predictions_total` | Counter | `model_version`, `label` | Proporcion de clases |
| `ml_input_invalid_total` | Counter | `field` | Campo que mas se rompe (422) |
| `ml_model_loaded` | Gauge | `model_version` | 1/0 |

Buckets: `0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1`.
Prohibido como label: `request_id`, `features_hash`, valores de features.

### Contrato C — Baseline (`models/baseline.json`)

- Por feature numerica: n, media, desviacion, min, max, p01/p10/p50/p90/p99,
  proporcion de nulos, **bordes de bins (deciles del entrenamiento)** y
  proporcion por bin.
- Por feature categorica: proporcion de cada nivel del contrato + `__otra__` +
  nulos.
- Predicciones: histograma de `probability` sobre validacion y tasa de clase
  positiva.
- `model_version`, `dataset_hash` y la metrica de validacion de `metadata.json`.
- Los bins se congelan en el entrenamiento.

### Contrato D — Reporte de drift

- CLI: `uv run python -m src.monitoring.drift --actual <ruta.csv> [--minimo 500]`.
- Salida: `reports/drift/<fecha>.json` y `.md` con, por feature: chequeos de
  calidad, PSI, estadistico KS/JS, estado (`ok`/`vigilar`/`drift`/`insuficiente`).
- Umbrales PSI: `< 0.1` estable, `0.1-0.2` vigilar, `> 0.2` drift.
- Ventana menor al minimo => `insuficiente`, nunca un numero.
- Metricas publicables: `ml_feature_psi{feature=...}` y `ml_prediction_psi`.
- Exit code distinto de cero si hay drift significativo.

## 1.9 Criterios de aceptacion

### AC-001: Una linea JSON por prediccion
- **Escenario:** API arriba, POST valido a `/predict`.
- **Accion:** enviar la peticion.
- **Esperado:** exactamente un evento `prediction` en stdout, JSON parseable,
  con todos los campos del Contrato A.
- **Verificacion:** `tests/test_observability.py::test_prediction_emite_log_json`.
- **Prioridad:** Required.

### AC-002: `request_id` correlacionable
- **Escenario:** peticion con header `X-Request-ID` propio.
- **Accion:** POST a `/predict` y forzar un 500.
- **Esperado:** el mismo id aparece en el log, en el header de la respuesta y
  en el cuerpo del 500. Sin header entrante, se genera uno y se devuelve.
- **Must not:** exponer traceback en la respuesta.
- **Verificacion:** `test_request_id_se_propaga`, `test_error_500_tiene_request_id_sin_traceback`.
- **Prioridad:** Required.

### AC-003: Sin secretos en los logs
- **Escenario:** ejecucion de prueba con `API_KEY` definida (simulada).
- **Accion:** POST a `/predict`.
- **Esperado:** ninguna linea de log contiene el valor de `API_KEY`, el
  authtoken de ngrok ni el contenido crudo de cabeceras.
- **Verificacion:** `test_log_no_contiene_secretos`.
- **Prioridad:** Required.

### AC-004: `features_hash` canonico y estable
- **Escenario:** el mismo diccionario de features con distinto orden de claves.
- **Accion:** calcular el hash.
- **Esperado:** identico valor; distinto entre procesos (no depende de `PYTHONHASHSEED`).
- **Must not:** usar `hash()` de Python.
- **Verificacion:** `test_features_hash_estable`.
- **Prioridad:** Required.

### AC-005: `/metrics` expone el Contrato B
- **Escenario:** API arriba tras un `/predict`.
- **Accion:** GET `/metrics`.
- **Esperado:** presencia de `ml_requests_total`, `ml_request_latency_seconds`,
  `ml_prediction_score`, `ml_predictions_total`, `ml_model_loaded`.
- **Must not:** labels de alta cardinalidad.
- **Verificacion:** `test_metrics_expone_contadores`.
- **Prioridad:** Required.

### AC-006: Probes y scrape no contaminan la senial
- **Escenario:** llamadas a `/health`, `/ready`, `/metrics`.
- **Accion:** observar logs y contadores.
- **Esperado:** no se cuenta como trafico de prediccion ni se loguea a INFO.
- **Verificacion:** test de `/metrics` count sin cambios tras probes.
- **Prioridad:** Important.

### AC-007: Baseline congelado por entrenamiento
- **Escenario:** `uv run python -m src.train`.
- **Accion:** inspeccionar `models/baseline.json`.
- **Esperado:** existe, lleva el `model_version` del artefacto y los bins del
  entrenamiento; cargar el modelo no lo requiere.
- **Must not:** recalcular bins con datos de produccion.
- **Verificacion:** `test_baseline_se_escribe_con_model_version`.
- **Prioridad:** Required.

### AC-008: El job de drift detecta lo que debe y calla lo que no
- **Escenario:** ventana con una feature desplazada 2 sigma vs una muestra del
  propio entrenamiento, y una ventana de 100 filas.
- **Accion:** correr el job en los tres casos.
- **Esperado:** desplazado => `drift` (PSI > 0.2) y exit code != 0; muestra del
  entrenamiento => `ok` y exit 0; 100 filas => `insuficiente`.
- **Verificacion:** `tests/test_drift.py` (6 tests del Contrato D).
- **Prioridad:** Required.

### AC-009: Alertas accionables
- **Escenario:** revision de `monitoring/`.
- **Accion:** abrir `alert_rules.yml` y `RUNBOOK.md`.
- **Esperado:** las 6 alertas con `for:` y una seccion de runbook por alerta
  (significado, confirmacion, accion, responsable).
- **Verificacion:** revision manual contra la tabla del paso S6.
- **Prioridad:** Required.

### AC-010: Documentacion operable
- **Escenario:** un operador nuevo lee el README.
- **Accion:** buscar observabilidad y monitoreo.
- **Esperado:** esquema de log, retencion, como consultar, umbrales de drift,
  frecuencia del job y politica de reentrenamiento.
- **Verificacion:** revision manual + AC-009.
- **Prioridad:** Required.

### AC-011: El contrato de inferencia no cambia
- **Escenario:** API con la instrumentacion completa.
- **Accion:** POST al fixture `tests/fixtures/request_ejemplo.json`.
- **Esperado:** misma prediccion de referencia (`Not_Canceled`, 0.0923, umbral 0.67)
  y el mismo conjunto de campos de respuesta mas el header nuevo.
- **Must not:** alterar `prediction`, `probability`, `threshold`, `model_version`.
- **Verificacion:** test existente de contrato + smoke de CI.
- **Prioridad:** Required.

### AC-012: La instrumentacion no degrada la latencia
- **Escenario:** 200 predicciones secuenciales con y sin instrumentacion.
- **Accion:** medir p99 en ambos casos.
- **Esperado:** diferencia por debajo de 5 ms en p99.
- **Verificacion:** script de medicion local documentado en el paso S3.
- **Prioridad:** Important.

## 1.10 Invariantes verificadas despues de CADA paso

1. `uv run ruff check .` en verde.
2. `uv run pytest --cov=src --cov-fail-under=80` en verde.
3. `/predict` devuelve la prediccion de referencia (AC-011).
4. Ningun log ni respuesta contiene secretos (AC-003).

---

# 2. Grafo de dependencias y paralelismo

```
S1 observability core + deps ──┬──> S2 API instrumentada ──> S3 /metrics ──┬──> S7 dashboard
                               │                                          ├──> S9 gates de CI
                               └────────────────┐                         │
S4 baseline en entrenamiento ──> S5 job de drift ┴──> S6 alertas + runbook ┴──> S8 docs/README

Fase 8 (nube, ya pendiente) + S2/S3 ──> S10 verificacion post-despliegue
```

| Paso | Deps | Paralelizable con | Archivos que toca | Tier |
|---|---|---|---|---|
| S1 Fundacion de observabilidad | — | S4 | `src/observability.py`, `pyproject.toml`, `requirements.txt`, `tests/test_observability.py` | fuerte |
| S2 API instrumentada | S1 | — | `src/api.py`, `tests/test_observability.py` | default |
| S3 `/metrics` | S2 | S5 | `src/api.py`, `src/observability.py`, `tests/test_observability.py` | default |
| S4 Baseline en entrenamiento | — | S1 | `src/model.py`, `src/train.py`, `tests/test_model.py` | fuerte |
| S5 Job de drift | S1, S4 | S3 | `src/monitoring/*`, `tests/test_drift.py`, `.gitignore` | fuerte |
| S6 Alertas + runbook | S3, S5 | S7, S9 | `monitoring/alert_rules.yml`, `monitoring/RUNBOOK.md` | default |
| S7 Dashboard | S3 | S6, S9 | `monitoring/dashboard.json` | default |
| S8 README y CHANGELOG | S2, S3, S5, S6 | — | `README.md`, `CHANGELOG.md` | default |
| S9 Gates de CI | S3, S5 | S6, S7 | `.github/workflows/*` | default |
| S10 Verificacion post-despliegue | Fase 8, S2, S3 | — | `docs/`, `monitoring/RUNBOOK.md` | default |

**Conflicto detectado y resuelto:** S3 y S5 son paralelizables pero ambos
querian tocar `pyproject.toml`. Se resolvio moviendo **todas** las
dependencias nuevas a S1, de modo que S3 y S5 no tocan ese archivo.

**Oleadas de ejecucion:** A: `S1 || S4` -> B: `S2` (y `S5` en cuanto S1+S4) ->
C: `S3` -> D: `S6 || S7 || S9` -> E: `S8` -> F: `S10`.

---

# 3. Pasos

Cada paso es un PR de tamano acotado, con verificacion ejecutable y rollback
propio. El brief de contexto es autocontenido: no exige leer pasos previos.

## S1 — Fundacion de observabilidad y dependencias

**Context brief.** No existe ninguna infraestructura de observabilidad. La
plantilla de referencia esta en
`~/.agents/skills/u_observabilidad_ml/templates/observability.py` (197 lineas):
`JsonFormatter`, `configure_logging`, `features_hash`, `safe_features`,
`new_request_id`, `stage`, `stages_ms`. Este paso la adapta y agrega las
dependencias para que S3 y S5 no vuelvan a tocar `pyproject.toml`.

**Archivos:** `src/observability.py` (nuevo), `pyproject.toml`,
`requirements.txt`, `tests/test_observability.py` (nuevo).

**Tareas**
1. Crear `src/observability.py` adaptando la plantilla: `JsonFormatter`,
   `configure_logging()` idempotente con `LOG_LEVEL`, `features_hash` con
   `sort_keys=True`, `new_request_id`, `stage(name)` con `time.perf_counter()`.
2. `pyproject.toml`: `+prometheus-client` en `dependencies`; nuevo grupo
   `monitoring = ["scipy"]`.
3. Regenerar `requirements.txt` con `uv export --no-dev --format requirements-txt`.
4. Escribir los tests unitarios de AC-004 (hash estable) y del formatter
   (una linea JSON valida, nivel y campos extra).

**Verificacion**

```bash
uv run ruff check . && uv run ruff format --check .
uv run pytest tests/test_observability.py -q
uv run python -c "from src.observability import features_hash; \
  print(features_hash({'a':1,'b':2}) == features_hash({'b':2,'a':1}))"   # True
```

**Criterio de salida:** los tests unitarios pasan; `features_hash` es estable;
`import src.observability` no requiere la API; cobertura global >= 80%.

**Rollback:** revertir el commit; ningun modulo en uso importa aun
`src/observability.py`.

**Tier:** fuerte (define los contratos A y B en codigo).

## S2 — API instrumentada: logs, `request_id` y niveles

**Context brief.** La API vive en `src/api.py`. El `lifespan`
(`src/api.py:42-53`) carga el modelo y ya registra `model_loaded` en texto
plano; el handler de errores esta en `src/api.py:160-168`; `/predict` en
`src/api.py:198-214`. Hoy el `request_id` se genera por request pero no sale
del cuerpo de la respuesta. Este paso conecta `src/observability.py` (S1) sin
cambiar la logica de prediccion.

**Archivos:** `src/api.py`, `tests/test_observability.py` (se amplia).

**Tareas**
1. Llamar `configure_logging()` en el `lifespan`, antes de cargar el modelo.
2. Middleware que lee `X-Request-ID` o genera uno (`new_request_id`) y lo
   devuelve en el header de **toda** respuesta.
3. `logger.critical` cuando el artefacto no carga (`src/api.py:51`).
4. `logger.warning` en los 422 (hoy no se registran) incluyendo el campo culpable.
5. Evento `prediction` (Contrato A) al final del handler exitoso, con
   `stage("validate")` y `stage("predict")`, mas el evento
   `prediction_features` solo con las columnas del contrato.
6. Silenciar o convertir a JSON el access log de uvicorn; excluir `/health`,
   `/ready` y `/metrics` del log de trafico.
7. Tests: AC-001, AC-002, AC-003 (`test_prediction_emite_log_json`,
   `test_request_id_se_propaga`, `test_error_500_tiene_request_id_sin_traceback`,
   `test_log_no_contiene_secretos`).

**Verificacion**

```bash
uv run pytest tests/test_observability.py tests/test_api.py -q
uv run uvicorn src.api:app --port 8001 &   # capturar stdout
curl -s -X POST localhost:8001/predict -H "Content-Type: application/json" \
  -d @tests/fixtures/request_ejemplo.json >/dev/null
# la ultima linea de stdout debe ser JSON con event=prediction y todos los campos
```

**Criterio de salida:** AC-001, AC-002, AC-003 en verde; el cuerpo de exito de
`/predict` no cambio (AC-011); ningun log con secretos.

**Rollback:** revertir el commit; la instrumentacion es aditiva y no altera la
ruta de inferencia.

**Tier:** default.

## S3 — `/metrics` con metricas RED y de ML

**Context brief.** Ya existe `stage()` acumulando en `stage_ms` (S1) y el
evento `prediction` (S2). Este paso agrega las 7 metricas del Contrato B,
las observa en `/predict` y expone `/metrics` excluyendolo del log.

**Archivos:** `src/observability.py` (metricas), `src/api.py` (observacion y
endpoint), `tests/test_observability.py`.

**Tareas**
1. Definir las 7 metricas del Contrato B con buckets explicitos.
2. Observar: contadores y histogramas en `/predict`; `ml_input_invalid_total`
   con el campo culpable en los 422; `ml_model_loaded` en el `lifespan`.
3. Exponer `GET /metrics` y excluirlo del log de trafico (AC-006).
4. Test AC-005 y el de AC-006.
5. Medir AC-012 con un script local de 200 peticiones (con y sin
   instrumentacion) y anotar p99 en el PR.

**Verificacion**

```bash
uv run pytest tests/test_observability.py -q
curl -s localhost:8000/metrics | grep -E "ml_requests_total|ml_prediction_score|ml_model_loaded"
```

**Criterio de salida:** AC-005 y AC-006 en verde; AC-012 dentro de presupuesto
(< 5 ms p99).

**Rollback:** revertir el commit; `/metrics` desaparece y la instrumentacion
previa (S2) sigue funcionando.

**Tier:** default.

## S4 — Baseline del modelo en el entrenamiento *(paralelo con S1-S3)*

**Context brief.** `save_model` (`src/model.py:66`) escribe `pipeline.joblib` y
`metadata.json`. El entrenamiento (`src/train.py:44-97`) ya calcula
`pipeline.predict_proba(Xva)` en `src/train.py:61`, que es la fuente de la
distribucion de probabilidades del baseline. Este paso es **aditivo**: no toca
la carga del modelo ni el contrato de inferencia.

**Archivos:** `src/model.py`, `src/train.py`, `tests/test_model.py`.

**Tareas**
1. `save_model` escribe tambien `models/baseline.json` (Contrato C): conteos,
   cuantiles, nulos, **deciles del set de validacion como bordes de bins**,
   proporciones por categoria mas `__otra__`, histograma de `probability` y
   tasa de clase positiva, `model_version`, `dataset_hash` y la metrica de
   validacion.
2. `train.py` pasa las probabilidades de validacion y los datos necesarios.
3. Tests: AC-007 (`test_baseline_se_escribe_con_model_version`) y que
   `load_model` siga funcionando sin el baseline (compatibilidad).

**Verificacion**

```bash
uv run pytest tests/test_model.py tests/test_train.py -q
uv run python -m src.train
uv run python -c "import json; b=json.load(open('models/baseline.json')); \
  print(b['model_version'], len(b['features']))"
```

**Criterio de salida:** AC-007 en verde; `load_model` no requiere el baseline;
el artefacto y la metadata siguen intactos en contrato.

**Rollback:** borrar `models/baseline.json` y revertir el commit; nada lo
consume todavia.

**Tier:** fuerte (define el Contrato C, interfaz del job de drift).

## S5 — Job de drift por lotes

**Context brief.** El equipo de datos necesita responder si las predicciones
siguen siendo buenas. Detalle completo en la skill
`~/.agents/skills/u_monitoreo_drift` y su plantilla
`templates/drift.py` (217 lineas). Las features a priorizar estan en la tabla
de importancia de la seccion 1.6. Este paso corre **fuera** del request.

**Archivos:** `src/monitoring/__init__.py`, `src/monitoring/drift.py` (nuevos),
`tests/test_drift.py` (nuevo), `.gitignore` (`+reports/drift/`),
`tests/fixtures/ventana_drift.csv` y `tests/fixtures/ventana_ok.csv`.

**Tareas**
1. CLI `--actual <csv> [--minimo 500]` con los chequeos de calidad de entrada
   (nulos, constante > 99%, fuera de rango, `__otra__` creciente).
2. PSI sobre bins del baseline (epsilon `1e-4`) y KS para numericas /
   Chi-cuadrado o JS para categoricas; estado por feature segun umbrales.
3. Salidas `reports/drift/<fecha>.json` y `.md`; metricas publicables
   `ml_feature_psi{feature=...}`; exit code != 0 si hay drift.
4. Fixtures: una ventana con `lead_time` desplazada 2 sigma y una muestra del
   propio entrenamiento; tests AC-008 (6 tests del Contrato D).

**Verificacion**

```bash
uv run pytest tests/test_drift.py -q
uv run python -m src.monitoring.drift --actual tests/fixtures/ventana_drift.csv; echo "exit=$?"   # != 0
uv run python -m src.monitoring.drift --actual tests/fixtures/ventana_ok.csv;   echo "exit=$?"   # 0
```

**Criterio de salida:** AC-008 en verde; el reporte marca `drift` solo cuando
corresponde y `insuficiente` con ventana corta.

**Rollback:** revertir el commit; no hay consumidores en produccion.

**Tier:** fuerte (logica estadistica y decisiones de umbral).

## S6 — Alertas con runbook

**Context brief.** Los nombres de metricas estan fijados en el Contrato B
(S3) y `ml_feature_psi` en el Contrato D (S5). Plantillas en
`~/.agents/skills/u_observabilidad_ml/templates/{alert_rules.yml,RUNBOOK.md}`.

**Archivos:** `monitoring/alert_rules.yml`, `monitoring/RUNBOOK.md` (nuevos).

**Tareas**
1. Las 6 alertas con `for:`: modelo no cargado (critica), tasa de error > 2%
   (critica), p99 > 1 s (alta), 422 > 10% (media), deriva de prediccion
   (media), `ml_feature_psi > 0.2` en feature prioritaria (media, abre ticket).
2. Una seccion de runbook por alerta: significado, como confirmarla, que hacer,
   responsable.
3. Nota explicita: el drift **no reentrena solo**.

**Verificacion:** revision manual contra AC-009; `yq`/`python -c yaml.safe_load`
valida el YAML.

**Criterio de salida:** AC-009 en verde.

**Rollback:** revertir el commit; no hay consumidores automatizados.

**Tier:** default.

## S7 — Dashboard operativo *(paralelo con S6 y S9)*

**Context brief.** Seguir la skill `dashboard-builder`: partir de **preguntas
de operador**, no del layout; cortar paneles de vanidad. Preguntas de este
servicio: esta vivo, es rapido, falla, cuantos inputs invalidos, como se
distribuyen las predicciones, hay drift.

**Archivos:** `monitoring/dashboard.json` (nuevo).

**Tareas**
1. Secciones: overview (RPS, tasa de error, `ml_model_loaded`), performance
   (p50/p90/p99 y por etapa), entradas invalidas por campo, predicciones
   (score y proporcion de clases), drift por feature.
2. Titulos, unidades y umbrales; variables para filtrar por `model_version`.
3. JSON valido y rango de tiempo por defecto sensato.

**Verificacion:** validacion de JSON + revision contra la checklist de
`dashboard-builder` (sin paneles de vanidad); comparar con la tabla del
Contrato B para no inventar metricas.

**Criterio de salida:** un panel por pregunta; ninguna metrica fuera del
Contrato B.

**Rollback:** borrar el archivo.

**Tier:** default.

## S8 — Documentacion operable

**Context brief.** El README ya tiene secciones (`Instalacion`, `Uso`, `Docker`,
`Versionamiento del modelo`, `Estructura`, `Datos y columnas`, `Resultados`,
`Decisiones`, `Calidad`). Faltan las dos que exige la Fase 9.

**Archivos:** `README.md`, `CHANGELOG.md`.

**Tareas**
1. Seccion **"Observabilidad"**: esquema de log, `X-Request-ID`, retencion
   (30 dias de `prediction` propuesto), `/metrics`, donde mirar.
2. Seccion **"Monitoreo del modelo"**: baseline, job de drift, umbrales PSI,
   frecuencia, politica de reentrenamiento (manual, aprobado, con las mismas
   fases).
3. CHANGELOG: entrada con la tabla de errores encontrados que surja de la
   implementacion.

**Verificacion:** relectura contra AC-010 y los Contratos A-D; los comandos
citados se copian y se ejecutan tal cual.

**Criterio de salida:** AC-010 en verde.

**Rollback:** revertir el commit (solo documentacion).

**Tier:** default.

## S9 — Gates de CI y job programado *(paralelo con S6 y S7)*

**Context brief.** `ci.yml` tiene los jobs `calidad` y `docker`, cuyo smoke test
hoy llama a `/predict` con el fixture. `release.yml` publica releases con dedup
por fingerprint (no tocar esa logica). Este paso agrega el gate de
observabilidad y la ejecucion programada del job de drift.

**Archivos:** `.github/workflows/ci.yml`, `.github/workflows/drift.yml` (nuevo).

**Tareas**
1. En `ci.yml`: tras el smoke test, afirmar `/metrics` con
   `grep ml_requests_total` (AC-005) y que el log contenga un `event=prediction`.
2. Nuevo `drift.yml` con `schedule` (diario) y `workflow_dispatch` que corre el
   job sobre un fixture desplazado y **falla** si no detecta drift (prueba que
   la deteccion sigue viva).
3. Verificar el efecto en `release.yml`: su fingerprint usa
   `git ls-files src data pyproject.toml uv.lock`, asi que **cualquier** archivo
   bajo `src/` (incluido `src/monitoring/`) y el `pyproject.toml` cambiado
   haran que el fingerprint cambie y se publique un release nuevo. Es el
   comportamiento correcto: el modelo se reentrena y gana un `baseline.json`.
   Lo que NO entra al fingerprint es `monitoring/` (nivel raiz), `docs/` ni
   `README.md`, de modo que S6, S7, S8 y S10 no publican release.

**Verificacion:** push a rama y observar los 3 workflows; validar YAML.

**Criterio de salida:** CI verde con el nuevo gate; `drift.yml` corre y detecta
el drift del fixture.

**Rollback:** revertir el commit; los workflows viejos quedan intactos.

**Tier:** default.

## S10 — Verificacion post-despliegue *(cuando ocurra la Fase 8)*

**Context brief.** Cuando exista URL publica (Fase 8), la observabilidad debe
confirmarse en produccion, no solo en local. Aplicar `canary-watch` sobre la
URL y las metricas derivadas de logs del proveedor.

**Archivos:** `monitoring/RUNBOOK.md` (seccion de post-deploy), `docs/`.

**Tareas**
1. Chequeo de canary sobre la URL publica: `/health`, `/ready`, `/docs`,
   `/predict` con el fixture, y latencia contra el baseline local.
2. Configurar en el proveedor las metricas derivadas de logs (RPS, error,
   percentiles) desde los eventos `prediction`.
3. Anotar en el runbook el comando de canary y la ventana de observacion.
4. Registrar el URL y la ventana en el CHANGELOG.

**Verificacion:** canary en verde; las metricas derivadas aparecen en la
consola del proveedor.

**Criterio de salida:** reporte de canary sin regresiones y metricas visibles.

**Rollback:** N/A (verificacion, no cambio de codigo).

**Tier:** default.

---

# 4. Revision adversarial (gate por paso)

Antes de dar un paso por terminado, un subagente **con modelo fuerte y contexto
fresco** revisa el diff contra esta checklist. El revisor no ejecuta el paso:
audita.

## Checklist

1. **Completitud:** todos los AC del paso estan cubiertos por un test o por
   evidencia manual declarada.
2. **Contratos:** el codigo respeta los Contratos A-D sin CAMBIARLES el nombre
   de un campo.
3. **Dependencias:** el paso no agrego dependencias fuera de S1; nada del
   runtime quedo en el grupo `dev`.
4. **Compatibilidad:** `/predict` devuelve exactamente la prediccion de
   referencia; el contrato de exito no cambio de campos.
5. **Seguridad:** ningun log con secretos, cabeceras crudas ni PII.
6. **Anti-patrones:** ninguno del catalogo de abajo.
7. **Invariantes:** los 4 invariantes de 1.10 verificados con salida pegada.

## Catalogo de anti-patrones de este dominio

- Calcular drift (PSI) dentro del request.
- Usar `hash()` de Python para auditoria (aleatorizado por proceso).
- Etiquetas de alta cardinalidad en metricas (`request_id`, `features_hash`).
- Escribir logs a archivo (`FileHandler`) o usar `print()` para eventos.
- Recalcular los bins del baseline con datos de produccion.
- Reentrenar automaticamente al detectar drift.
- Instrumentar o loguear `/health`, `/ready`, `/metrics` como trafico.
- Dejar el access log de uvicorn en texto plano mezclado con el JSON.
- Cambiar el nombre de un campo de log entre versiones.
- Registrar el payload crudo de features completo en cada request.

---

# 5. Protocolo de mutacion del plan

Cualquier cambio de alcance se registra en el Revision Log con su razon.

| Operacion | Cuando | Como |
|---|---|---|
| **Dividir** un paso | El PR resulto grande o con dos contratos | `S5` -> `S5a`/`S5b`, actualizar el grafo |
| **Insertar** un paso | Aparecio un requisito nuevo | Nuevo id, declarar deps y archivos, revisar conflictos |
| **Saltar** un paso | No aplica al proyecto hoy | Marcar `skipped` con razon y fecha |
| **Reordenar** | Cambio de prioridad del negocio | Actualizar grafo y oleadas |
| **Abandonar** | El objetivo cambio | Marcar `abandoned`; no borrar el registro |

---

# 6. Definition of Done

1. AC-001 a AC-012 verificados con evidencia (test o revision manual declarada).
2. Los 4 invariantes de 1.10 verificados con salida pegada.
3. Revision adversarial sin hallazgos criticos abiertos.
4. CI verde con el gate de observabilidad y el job de drift programado.
5. README con las dos secciones nuevas (AC-010).
6. CHANGELOG con la tabla de errores encontrados durante la implementacion.

---

# Anexo A — Skills aplicables (revision pedida)

## Incorporadas a este plan

| Skill | Que aporta | Donde se refleja |
|---|---|---|
| `blueprint` | Pasos cold-executable, grafo de deps, deteccion de paralelismo, tier de modelo, revision adversarial, protocolo de mutacion | Secciones 2, 3, 4, 5 |
| `intent-driven-development` | Especificacion con criterios observables `AC-NNN` y verificacion nombrada | Seccion 1 (SPEC) |
| `u_observabilidad_ml` | Pilares, esquema de log, reglas duras (stdout, cardinalidad), plantillas | Contratos A/B, S1-S3, S6 |
| `u_monitoreo_drift` | Baseline, PSI/KS, ventana minima, politica de reentrenamiento, plantilla | Contratos C/D, S4-S5 |
| `dashboard-builder` | Preguntas de operador primero, cortar paneles de vanidad, checklist de calidad | S7 |
| `canary-watch` | Verificacion de URL desplegada con umbrales critical/warning | S10 |
| `tdd` / `tdd-workflow` | Tests primero por paso, cobertura como gate | Tareas y verificacion de cada paso |
| `code-review` / `santa-method` | Verificacion adversarial independiente | Seccion 4 |
| `python-testing` | Fixtures, parametrizacion, mocking para `caplog` | S1, S2, S5 |
| `u_calidad_python` | Gate de lint/cobertura en CI | S9, invariantes 1.10 |
| `security-review` + `docs/PLAN_SEGURIDAD.md` | Coherencia con la futura `API_KEY` (no loguear secretos) | AC-003 |

## Candidatas para despues (no bloquean este plan)

| Skill | Cuando usarla |
|---|---|
| `to-issues` | Convertir los pasos en issues rastreables con corte vertical |
| `plan-orchestrate` / `orch-pipeline` | Ejecutar el plan como cadenas de agentes con gates |
| `python-patterns` / `backend-patterns` / `coding-standards` | Convenciones finas dentro de `src/observability.py` |
| `verification-loop` / `production-audit` | Auditoria final de readiness con evidencia local |
| `ai-regression-testing` | Estrategia de regresion en modo sandbox sin base de datos |
| `handoff` / `strategic-compact` / `context-budget` | Continuidad entre sesiones al ejecutar paso a paso |
| `latency-critical-systems` | Si el SLO de latencia pasa a ser critico (ajuste de buckets) |
| `e2e-testing` / `browser-qa` | Verificacion de la UI de `/docs` en navegador |

---

*Rectificado para el Hito 1. Estructura de `blueprint` (pasos cold-executable +
revision adversarial) y criterios de `intent-driven-development`, sobre el
contenido tecnico de `u_observabilidad_ml` y `u_monitoreo_drift`.*
