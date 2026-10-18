# Guia de observabilidad: como revisar logs, metricas y agregaciones

Documento de referencia para la segunda entrega. Explica **que se construyo**,
**donde se mira cada cosa** y **que comandos usar**, con la evidencia ya
ejecutada en local, en CI y en produccion (Render).

Referencias de contexto:

- Plan y contratos: `docs/PLAN_OBSERVABILIDAD.md`
- Codigo de instrumentacion: `src/observability.py`
- Job de drift: `src/monitoring/drift.py`
- Alertas y runbook: `monitoring/alert_rules.yml`, `monitoring/RUNBOOK.md`
- Panel: `monitoring/dashboard.json`
- Medicion de overhead: `scripts/medir_overhead.py`

---

## 1. Las cinco preguntas y donde se responden

| Pregunta | Senal | Donde mirar |
|---|---|---|
| Esta vivo? | `ml_model_loaded`, `/health`, `/ready` | `/metrics`, logs `model_loaded` |
| Es rapido? | `latency_ms`, `stage_ms`, `ml_request_latency_seconds` | logs `prediction`, `/metrics` |
| Falla? | `status`, `ml_requests_total{status="error"}`, `ml_input_invalid_total` | logs, `/metrics` |
| Que modelo respondio? | `model_version`, `fingerprint` | logs, `/model-info`, releases de GitHub |
| Siguen siendo buenas las predicciones? | `baseline.json`, PSI, KS | job de drift, `reports/drift/` |

---

## 2. Inventario de lo construido

| Componente | Archivo | Lineas | Que aporta |
|---|---|---|---|
| Logging JSON + metricas | `src/observability.py` | 156 | Esquema de log (Contrato A) y las 7 metricas (Contrato B) |
| Job de drift | `src/monitoring/drift.py` | 335 | PSI/KS sobre el baseline, calidad de entrada, reporte json+md |
| Baseline del modelo | `models/baseline.json` (generado por `src/model.py`) | - | Referencia congelada en validacion (bins, cuantiles, distribucion) |
| Alertas | `monitoring/alert_rules.yml` | 90 | 6 alertas con `for:` y severidad |
| Runbook | `monitoring/RUNBOOK.md` | 104 | Una seccion por alerta + canary post-despliegue |
| Panel | `monitoring/dashboard.json` | 253 | 12 paneles en 5 secciones (una por pregunta) |
| Formulario | `static/index.html` | 446 | Frontend de demostracion en `/app` |
| Medicion de overhead | `scripts/medir_overhead.py` | 114 | Evidencia reproducible de AC-012 |
| Tests | `tests/test_observability.py`, `tests/test_drift.py` | 22 + 17 | 39 tests de observabilidad y drift |

**Esquema de log (Contrato A)** — una linea JSON por evento a stdout:

```
timestamp, level, event, request_id, model_name, model_version,
features_hash, prediction, probability, threshold, latency_ms,
stage_ms, status
```

Eventos: `model_loaded`, `prediction`, `prediction_features`,
`input_invalid` (WARNING), `prediction_failed` (ERROR),
`unhandled_error` (ERROR), `model_load_failed` (CRITICAL), `drift_report`.

**Metricas (Contrato B)** — labels de baja cardinalidad unicamente:

```
ml_requests_total{model_version,status}        Counter
ml_request_latency_seconds{model_version}      Histogram
ml_stage_latency_seconds{stage}                Histogram
ml_prediction_score{model_version}             Histogram
ml_predictions_total{model_version,label}      Counter
ml_input_invalid_total{field}                  Counter
ml_model_loaded{model_version}                 Gauge
```

---

## 3. Guia: revisar los LOGS

### 3.1 Donde estan

| Entorno | Como acceder |
|---|---|
| Local (Docker) | `docker logs <nombre-del-contenedor>` (todo a stdout, nada a archivo) |
| Local (uvicorn) | la terminal donde corre `uvicorn` |
| **Produccion (Render)** | `render logs --resources srv-dbahsbm1egvs73apb6fg` |
| CI (drift) | GitHub Actions → workflow "Drift programado" |

El CLI de Render ya esta instalado (`~/.local/bin/render`, v2.30.0) y
autenticado. El ID del servicio es `srv-dbahsbm1egvs73apb6fg`.

### 3.2 Comandos listos para copiar

```bash
# Todo el flujo reciente (build + arranque + peticiones)
render logs --resources srv-dbahsbm1egvs73apb6fg --limit 100

# Solo las predicciones
render logs --resources srv-dbahsbm1egvs73apb6fg --limit 200 --text prediction

# Solo los errores de contrato (422) y ver QUE campo se rompe
render logs --resources srv-dbahsbm1egvs73apb6fg --limit 200 --text input_invalid

# Seguir los logs en vivo mientras haces peticiones (ideal para la demo)
render logs --resources srv-dbahsbm1egvs73apb6fg --tail
```

### 3.3 Reconstruir una peticion completa

Cada respuesta trae el header `X-Request-ID` (y el cuerpo tambien lo incluye).
Con ese id se reconstruye la peticion en los logs:

```bash
# 1. Hacer una peticion guardando el request_id
curl -s -D /tmp/cabeceras.txt -X POST https://hotel-cancel-model.onrender.com/predict \
  -H "Content-Type: application/json" \
  -d @tests/fixtures/request_ejemplo.json | python3 -c "import json,sys; print(json.load(sys.stdin)['request_id'])"

# 2. Buscar ese id en los logs de produccion
render logs --resources srv-dbahsbm1egvs73apb6fg --limit 300 --text <request_id>
```

Esto responde: **que respondio el modelo, con que version, en cuanto tiempo y
con que etapas**. La latencia se puede desglosar:

```json
{"event": "prediction", "request_id": "...", "model_version": "0.1.0+b04013b",
 "features_hash": "392492b92cea206f", "prediction": "Not_Canceled",
 "probability": 0.0923, "threshold": 0.67, "latency_ms": 8.834,
 "stage_ms": {"to_frame": 0.964, "predict": 7.771, "postprocess": 0.002},
 "status": "success"}
```

> `latency_ms` es el **computo** del servicio (~8,8 ms). La diferencia con el
> tiempo total que ves en el navegador (~230 ms) es red + Render + TLS.

### 3.4 Niveles y que significa cada uno

| Nivel | Cuando | Ejemplo |
|---|---|---|
| `INFO` | Operacion normal | `prediction`, `model_loaded`, `prediction_features` |
| `WARNING` | Inusual pero manejado | `input_invalid` (422 con el campo culpable) |
| `ERROR` | Falla de la peticion | `prediction_failed`, `unhandled_error` |
| `CRITICAL` | El servicio no puede cumplir su funcion | `model_load_failed` (`/ready` en 503) |

---

## 4. Guia: revisar las METRICAS

### 4.1 El endpoint

```bash
# Local
curl -s localhost:8000/metrics | grep "^ml_"

# Produccion
curl -s https://hotel-cancel-model.onrender.com/metrics | grep "^ml_"
```

### 4.2 Agregaciones listas (PromQL)

Estas consultas funcionan en Prometheus/Grafana y son la base del panel:

| Agregacion | PromQL |
|---|---|
| Peticiones por segundo | `sum(rate(ml_requests_total[5m]))` |
| Tasa de error (500) | `sum(rate(ml_requests_total{status="error"}[5m])) / clamp_min(sum(rate(ml_requests_total[5m])), 1e-9)` |
| Latencia p50 / p90 / p99 | `histogram_quantile(0.99, sum by (le) (rate(ml_request_latency_seconds_bucket[5m])))` |
| Latencia p99 por etapa | `histogram_quantile(0.99, sum by (le, stage) (rate(ml_stage_latency_seconds_bucket[5m])))` |
| 422 por campo | `sum by (field) (rate(ml_input_invalid_total[10m]))` |
| Proporcion de clases predichas | `sum by (label) (rate(ml_predictions_total[1h]))` |
| Distribucion del score (p10/p50/p90) | `histogram_quantile(0.50, sum by (le) (rate(ml_prediction_score_bucket[1h])))` |
| El modelo esta cargado | `max(ml_model_loaded)` |
| Drift por feature (PSI) | `ml_feature_psi` (si se publica al Pushgateway) |

### 4.3 El panel

`monitoring/dashboard.json` es un dashboard de Grafana exportado, con **una
seccion por pregunta de operador**:

1. **Esta vivo?** — modelo cargado, RPS, tasa de error, peticiones por estado
2. **Es rapido?** — p50/p90/p99 y latencia por etapa
3. **Falla?** — 422 por campo
4. **Como se distribuyen las predicciones?** — proporcion de clases, score p10/p50/p90
5. **Hay drift?** — PSI por feature y PSI de la prediccion

Para verlo: Grafana → Dashboards → Import → subir el JSON (necesita un
Prometheus con scrape de `/metrics`; ver "Metricas de produccion" abajo).

### 4.4 En produccion sin Prometheus

Render (plan Free) **no hace scrape** de `/metrics`. Para agregaciones en
produccion hay dos caminos:

```bash
# Opcion 1 (ya funciona): partir de los logs con el CLI.
#   Cada evento `prediction` trae latency_ms y status, suficiente para contar
#   y sacar percentiles con un script.
render logs --resources srv-dbahsbm1egvs73apb6fg --limit 500 --text prediction > /tmp/predicciones.txt

# Opcion 2 (si se quiere Prometheus de verdad): correr Prometheus + Grafana en
#   local apuntando a la URL publica (scrape cada 15s) y abrir el dashboard.
```

> El endpoint `/metrics` SI esta expuesto en produccion y responde 200; solo
> falta quien lo scrapee.

---

## 5. Guia: revisar el MONITOREO DEL MODELO (drift)

### 5.1 El baseline

`models/baseline.json` se regenera en **cada entrenamiento** (tambien dentro
del build de Docker en Render) y congela, por cada feature del contrato:

- numericas: media, desviacion, cuantiles, proporcion de nulos y **bordes de
  bins (deciles de validacion)** con su proporcion
- categoricas: proporcion por nivel del contrato + `__otra__`
- predicciones: histograma de probabilidades y tasa de clase positiva
- `model_version`, `dataset_hash` y la metrica de validacion

Inspeccion rapida:

```bash
uv run python -c "
import json; b = json.load(open('models/baseline.json'))
print('version:', b['model_version'], '| features:', len(b['features']))
print('positive_rate (validacion):', round(b['positive_rate'], 4))
print('bins de lead_time:', len(b['features']['lead_time']['bin_props']))
"
```

### 5.2 El job

```bash
# Ventana suficiente del mismo regimen -> ok
uv run python -m src.monitoring.drift --actual /tmp/ventana_ok.csv; echo "exit=$?"   # 0

# Drift simulado (lead_time +120) -> drift
uv run python -m src.monitoring.drift --actual /tmp/ventana_drift.csv; echo "exit=$?"  # 1

# Ventana real de produccion con 8 peticiones -> insuficiente (no inventa numeros)
uv run python -m src.monitoring.drift --actual /tmp/ventana_produccion.csv; echo "exit=$?"  # 0
```

Estados por feature: `ok` (< 0.1) · `vigilar` (0.1-0.2) · `drift` (> 0.2) ·
`insuficiente` (menos de 500 filas) · `sin_datos` · `ausente`.

### 5.3 Leer el reporte

`reports/drift/<fecha>.md` esta ordenado por **importancia de la feature**, con
la columna de calidad de entrada:

```markdown
| Feature | Importancia | PSI | KS D | Estado | Calidad |
|---|---|---|---|---|---|
| lead_time | 0.2616 | 4.3234 | 0.523 | drift | fuera del rango historico |
| no_of_special_requests | 0.1349 | 0.0044 | 0.0092 | ok | - |
```

La columna **Calidad** es la que distingue "el mundo cambio" de "los datos
estan rotos": nulos al alza, feature constante (>99% un valor), fuera del rango
historico, categoria nueva creciendo.

### 5.4 En CI

El workflow **"Drift programado"** corre **a diario (06:00 UTC)** y tambien
manual (`workflow_dispatch`). Hace dos cosas:

1. Comprueba que una ventana de control **no** marque drift (evita falsos positivos)
2. Comprueba que un drift simulado **si** se detecte (prueba de vida del detector)

### 5.5 Politica de reentrenamiento

El drift **no reentrena solo**. El flujo aprobado: ticket → validar con
etiquetas reales si es cambio del mundo → reentrenar → pasa por contrato y
evaluacion → nuevo `baseline.json` → nueva `model_version` → CHANGELOG.

---

## 6. Evidencia de verificacion (ya ejecutada)

### 6.1 Local

```
uv run ruff check . ..................... All checks passed
uv run pytest (109 casos) ............... 92% de cobertura (umbral 80%)
uv run python scripts/medir_overhead.py . overhead p99 +0.8 ms (presupuesto 5 ms)
uv run python -m src.train .............. pipeline completo, F1 0.6209
drift (mismo regimen) ................... ok / exit 0
drift (lead_time +120) .................. drift / exit 1
drift (produccion, 8 filas) ............. insuficiente / exit 0
```

### 6.2 Produccion (Render, https://hotel-cancel-model.onrender.com)

```
/health /ready /app /docs /metrics ...... 200 los cinco
POST /predict (fixture) ................. Not_Canceled 0.0923 umbral 0.67  (identico a local)
X-Request-ID ............................ propagado a header y cuerpo
POST con lead_time=99999 ................ 422 (contrato estricto)
/metrics ................................ ml_requests_total{status="success"} 3.0
                                          ml_model_loaded{model_version="0.1.0+b04013b"} 1.0
logs .................................... evento `prediction` con latency_ms 8.8 y stage_ms
                                          evento `input_invalid` con fields: ["lead_time"]
model_version ........................... 0.1.0+b04013b (SHA del commit, inyectado por Render)
formulario (navegador) .................. NO CANCELA 9.23% / CANCELA 99.97%
```

### 6.3 CI

```
CI ................. verde (ruff, tests, imagen, /metrics, evento prediction, prediccion de referencia)
Release modelo ..... publica model-0.1.0-<sha> con joblib + metadata; dedup por fingerprint
Drift programado ... verde (cron diario + manual)
```

---

## 7. Los 12 criterios de aceptacion y su evidencia

| AC | Exige | Verificacion |
|---|---|---|
| AC-001 | Una linea JSON por prediccion | `test_prediction_emite_una_linea_json_con_el_esquema` + logs de Render |
| AC-002 | `request_id` correlacionable | `test_request_id_se_propaga...` + `test_error_500_lleva_request_id...` + header en produccion |
| AC-003 | Sin secretos en los logs | `test_los_logs_no_contienen_secretos` |
| AC-004 | `features_hash` canonico | 2 tests (estable por orden de claves, sha256 y no `hash()`) |
| AC-005 | `/metrics` con el Contrato B | `test_metrics_expone_las_metricas_del_contrato` + gate de CI |
| AC-006 | Probes y scrape no contaminan | `test_los_probes_y_el_scrape_no_se_cuentan_como_prediccion` |
| AC-007 | Baseline congelado por entrenamiento | `test_baseline_se_escribe_con_la_version_del_modelo` + JSON estricto |
| AC-008 | El job detecta lo que debe y calla lo que no | 17 tests de `test_drift.py` + corrida con datos reales |
| AC-009 | Alertas accionables | `monitoring/alert_rules.yml` (6 alertas) + `RUNBOOK.md` (manual) |
| AC-010 | Documentacion operable | secciones "Observabilidad" y "Monitoreo del modelo" del README |
| AC-011 | El contrato de inferencia no cambia | gate de CI que asevera 0.0923 / umbral 0.67 + `test_predict_camino_feliz` |
| AC-012 | La instrumentacion no degrada latencia | `scripts/medir_overhead.py`: +0.8 ms p99 (presupuesto 5 ms) |

---

## 8. Hallazgos de la revision (lo que se corrigio)

### 8.1 Revision adversarial (subagente independiente)

| Hallazgo | Severidad | Correccion |
|---|---|---|
| **Cardinalidad ilimitada en el label `field`**: con `extra="forbid"` el cliente elige el nombre del campo y cada nombre inventado creaba una serie nueva en Prometheus | critica | Whitelist de los 16 campos del contrato; lo demas cae en `field="extra"` |
| `/metrics` respondia 307 hacia `/metrics/` y rompia el gate de CI | importante | Ruta directa con `generate_latest()` |
| `baseline.json` no era JSON valido (RFC 8259): `-Infinity` en los bins abiertos | importante | Extremos abiertos como `null` |
| La alerta `DriftDeEntrada` apuntaba a una metrica sin productor | importante | Publicacion opcional en Pushgateway (`--pushgateway`) |
| El README prometia no registrar el payload; el codigo si lo registra | importante | README alineado y `PII_FIELDS` documentado |
| El PSI de la prediccion no afectaba al estado | menor | Ahora cuenta |
| `print()` para el evento de drift | menor | Emitido por el logger JSON |
| AC-011 y AC-012 sin evidencia reproducible | menor | Gate en CI + script de overhead |

### 8.2 Encontrado con datos reales de produccion

| Hallazgo | Como aparecio | Correccion |
|---|---|---|
| **El CLI moria con `KeyError: 'model_version'`** cuando la ventana tenia menos de 500 filas: el early-return de `insuficiente` no llevaba las claves de identidad y el reporte markdown las leia directo | Al correr el job sobre los logs reales de Render (8 peticiones) | El caso `insuficiente` ahora incluye `model_version` y `generated_at`; el markdown es defensivo; test nuevo que cubre CLI + ventana corta |

**Leccion para la sustentacion:** los tests cubrian `drift_report` con ventana
corta y el CLI con ventana larga, pero **no la combinacion CLI + ventana corta**.
El primer dato real lo encontro en 30 segundos.

---

## 9. Limitaciones conocidas (honestas)

| Limitacion | Impacto | Cuando se resuelve |
|---|---|---|
| Sin `API_KEY`: la API publica es abierta | Cualquiera con la URL puede predecir | `docs/PLAN_SEGURIDAD.md` (al haber datos reales) |
| Sin rate limiting | Un cliente podria saturar el plan Free | Lo cubre la plataforma o un middleware |
| Sin Prometheus/Grafana desplegados | Las agregaciones en produccion se derivan de logs | Opcional: Prometheus local apuntando a la URL |
| `prediction_features` registra 16 campos por request | Volumen (~300 B/evento); a 30 dias puede pesar | Muestrear o acortar retencion si crece |
| Retencion de logs: 30 dias (decision, no impuesta) | Sin historico largo sin export | Export a bucket si hace falta |
| `scipy` en el grupo `monitoring` es redundante (llega transitiva por scikit-learn) | Cosmetico | Se deja explicito a proposito |
| Cold start del plan Free (30-90 s) | Primera peticion lenta | Calentar antes de la demo |
| El drift no corre sobre datos de produccion automaticamente | Requiere exportar la ventana de logs | El workflow diario usa una ventana de control |

---

## 10. Resumen de una linea

**Cada prediccion deja una linea JSON con `request_id`, version del modelo,
hash de features, salida, latencia y etapas; las 7 metricas RED+ML se exponen
en `/metrics`; el baseline congela la referencia del entrenamiento y el job de
drift dice si el mundo cambio — todo verificable en `/app`, `/docs`, `/metrics`
y los logs de Render.**
