# CHANGELOG

## [0.4.0] - 2026-10-18

### Agregado

- **Frontend de demostracion** en `/app`: formulario autocontenido (HTML+CSS+JS,
  18 KB, sin CDN ni build step) con las 5 secciones de los 16 campos del
  contrato, validacion cruzada local (dia real del mes), defaults del fixture,
  resultado con insignia, barra de probabilidad, umbral, `request_id` y latencia,
  y manejo de 422/503/500. La raiz `/` redirige al formulario.
- **Despliegue en Render**: <https://hotel-cancel-model.onrender.com> con
  auto-deploy condicionado al CI (`After CI Checks Pass`), health check en
  `/health` y plan Free. El modelo se entrena en la etapa de build del
  Dockerfile y la version se deriva del SHA del commit (`{pyproject}+{sha}`).
- El smoke de CI verifica el formulario (`/app` responde HTML sin redirect, los
  16 campos estan, la raiz redirige).

### Corregido

- **El build se colgaba en Render** (20 min sin avanzar): `permutation_importance`
  con `n_jobs=-1` levanta un pool de procesos con loky, y en el sandbox de build
  de Render (0 cores fisicos detectables) los workers no arrancan. Ahora
  `n_jobs=1`: el entrenamiento completo tarda 6 s y no vale arriesgar el cuelgue.
- **`uv run` reinstalaba el grupo dev dentro del build** (matplotlib, ruff,
  pytest), engordando el `.venv` que se copia a la imagen final. Ahora se llama
  directo al python del venv: la imagen baja de 701 MB a 691 MB y el build de
  45 s a 34 s.

## [0.3.1] - 2026-10-18

### Corregido

- **La imagen no se podia construir desde un clon limpio del repo.** El
  `Dockerfile` copiaba `models/` del contexto, pero `models/` esta en
  `.gitignore`: en GitHub Actions funcionaba (el workflow entrenaba antes de
  construir) y en Render fallaba con `"/models": not found`. Ahora el modelo se
  entrena **en la etapa de build** (`builder`), con `data/` en el contexto y
  sin dependencias de desarrollo; la etapa de runtime sigue recibiendo solo el
  artefacto entrenado. Build verificado desde cero: 45 s, prediccion identica.

### Agregado

- El `Dockerfile` acepta `--build-arg MODEL_VERSION` y `--build-arg FINGERPRINT`
  para inyectar la version que deriva CI; sin argumentos cae a
  `{pyproject}+{RENDER_GIT_COMMIT|local}`, de modo que Render tambien queda
  versionado aunque no se configure nada.
- Los workflows `ci.yml` y `release.yml` pasan esos build args, y el smoke de CI
  ya no entrena fuera de la imagen (era trabajo duplicado).

## [0.3.0] - 2026-10-17

Observabilidad y monitoreo del modelo (Fase 9 del agente, ejecutada paso a paso
segun `docs/PLAN_OBSERVABILIDAD.md`).

### Agregado

- `src/observability.py`: logging JSON a stdout, `request_id` correlacionable,
  hash canonico de features (sha256, nunca `hash()`) y las 7 metricas RED + ML.
- API instrumentada: `X-Request-ID` en el header de toda respuesta,
  `configure_logging` en el lifespan, niveles con significado (422 `WARNING`,
  fallo de carga `CRITICAL`) y los eventos `prediction`, `prediction_features`,
  `input_invalid`, `prediction_failed`, `model_load_failed`, `unhandled_error`.
- `GET /metrics` en formato Prometheus, con labels de baja cardinalidad.
- `models/baseline.json`: perfiles por feature, bins congelados en validacion,
  histograma de probabilidades y la metrica de validacion, junto a
  `model_version` y `dataset_hash`.
- `feature_importance` (permutation importance) en `metadata.json`: el job de
  drift vigila primero lo que al modelo le importa.
- `src/monitoring/drift.py`: job por lotes con PSI (principal), KS /
  Jensen-Shannon (complemento), chequeos de calidad de entrada, orden por
  importancia, reporte `.json` + `.md` y exit code 1 con drift. Ventana corta
  reporta `insuficiente`.
- `monitoring/`: 6 alertas con `for:` y runbook por alerta, dashboard operativo
  (5 secciones que son 5 preguntas) y la verificacion post-despliegue (canary).
- README: secciones **Observabilidad** y **Monitoreo del modelo**.
- 37 tests nuevos (observabilidad 20, drift 13, baseline 4): **99 casos en
  total** (93 funciones, algunos parametrizados), 92% de cobertura.

### Revisión adversarial (subagente independiente, diff `b293de1..0cf8db8`)

| Hallazgo | Severidad | Corrección |
|---|---|---|
| Cardinalidad ilimitada en el label `field` de `ml_input_invalid_total`: con `extra="forbid"` el cliente elige el nombre del campo y cada nombre inventado creaba una serie nueva en Prometheus (memoria sin techo desde input no confiable, sin auth) | **crítica** | Whitelist de los 16 campos del contrato; lo demás cae en `field="extra"`. Test que lo fija |
| `/metrics` respondía 307 hacia `/metrics/`: el gate de CI (`curl` sin `-L`) quedaba en rojo y los scrapers tenían que seguir el redirect | importante | Ruta directa con `generate_latest()` en vez de montar un subapp; test que exige 200 sin redirect |
| `models/baseline.json` no era JSON válido (RFC 8259): `-Infinity` en los bins abiertos, rechazado por cualquier consumidor que no sea Python | importante | Extremos abiertos como `null`, reconstruidos en el job; test con `parse_constant` estricto |
| La alerta `DriftDeEntrada` apuntaba a `ml_feature_psi`, que no tenía productor: nunca podía disparar | importante | Publicación opcional en Pushgateway (`--pushgateway` / `PUSHGATEWAY_URL`) y comentario explícito de las dos vías (gauge o métricas derivadas de logs) |
| El README prometía no registrar el payload, pero `prediction_features` registra los 16 campos por valor | importante | README alineado con el comportamiento, `PII_FIELDS` documentado como interruptor y decisión de retención explícita |
| Al arreglar los bins, los dos extremos `null` se reconstruían como `-inf` (histograma no monótono) | bug propio | Se distingue primero de último; los tests de drift existentes lo cazaron |
| El PSI de la distribución de predicciones no afectaba al estado ni al exit code | menor | Ahora cuenta como señal de drift (era el Contrato D) |
| `print()` para el evento `drift_report`, contra el catálogo de anti-patrones | menor | Emitido por el logger con el formatter JSON del servicio |
| AC-011 y AC-012 sin evidencia reproducible | menor | Gate en CI que asevera la predicción de referencia (0.0923 / umbral 0.67) y `scripts/medir_overhead.py`, que falla si el overhead p99 pasa de 5 ms |
| `scipy` en el grupo `monitoring` es redundante (llega como transitiva de scikit-learn) | menor | Se conserva explícito (el job no depende de un accidente transitivo) con el comentario corregido |

### Errores encontrados

| Error | Causa | Solucion |
|---|---|---|
| Dos eventos con el mismo nombre `prediction_failed` por cada 500 | El handler interno del pipeline y la red de seguridad global registraban el mismo evento | El global pasa a `unhandled_error`: sin doble conteo en las metricas derivadas de logs. Lo cazo el test de AC-002 |
| Dos corridas del job de drift en el mismo segundo se sobreescribian | El sello del reporte tenia precision de segundo | Sello con microsegundos (`%Y%m%dT%H%M%S%fZ`). Lo cazo el test del CLI |
| AC-012 parecia incumplir (13,3 ms de overhead) | La medicion incluia el stack HTTP del `TestClient`, no la instrumentacion | Medicion aislada (pipeline vs pipeline + instrumentacion): **+0,64 ms p99**, dentro del presupuesto de 5 ms |
| `capfd`/`capsys` no capturaban los eventos JSON en los tests | `configure_logging()` reemplaza los handlers del root y el `StreamHandler` queda atado al stream del arranque | El test instala su propio handler despues del lifespan y formatea con la misma clase: se prueba el esquema real |
| Una ventana del periodo de test marcaba drift en la verificacion | No es un bug: es la deriva temporal que el EDA documento | Se verifica con una ventana del mismo regimen (validacion) y el hallazgo se documenta en README y RUNBOOK |

### Mutaciones del plan (protocolo de mutacion, ver Revision Log del plan)

| Mutacion | Razon |
|---|---|
| `feature_importance` en `metadata.json` (extension del Contrato C) | La skill `u_monitoreo_drift` exige priorizar el drift por importancia del modelo; sin esto habria que mantener el ranking a mano |
| Fixtures de drift generados en el test en vez de CSVs versionados | Evita ~150 KB de datos en el repo y mantiene los tests deterministicos desde la distribucion sintetica |

## [0.2.0] - 2026-09-07

### Agregado

- Versionamiento del modelo gestionado por CI: `save_model` lee
  `MODEL_VERSION` del entorno (CI la deriva como `{pyproject}+{sha}`) con
  fallback `0.1.0-dev` en local.
- Campo `fingerprint` en la metadata: hash de codigo, datos y lock. Es la
  identidad completa de lo que produjo el artefacto.
- Workflow `release.yml`: en cada push a `main` entrena, construye la imagen
  versionada, le hace smoke test y publica un GitHub Release con el joblib y
  la metadata adjuntos. Dedup por fingerprint: sin cambio en codigo o datos,
  no publica nada.
- Las notas del release incluyen metricas temporales y matriz de confusion
  generadas desde la metadata.

### Cambiado

- `src/api.py` consume la version via `model_version()` en vez de la constante
  fija `MODEL_VERSION = "0.1.0"` de `src/model.py:28`.

## [0.1.0] - 2026-09-06

### Agregado

- EDA reproducible (`src/eda.py`) con figuras y `reports/eda.md`.
- Contrato de datos en dos versiones: `clean_training` tolerante y
  `clean_prediction` estricta.
- Pipeline de sklearn con todo el preprocesamiento dentro, incluida la bandera
  derivada `es_cortesia`.
- Entrenamiento con tres ventanas temporales y artefacto versionado con
  metadata (`models/metadata.json`).
- API FastAPI con `/health`, `/ready`, `/predict` y `/model-info`.
- 60 tests, 90% de cobertura. Lint con ruff. CI en GitHub Actions.
- Dockerfile multi-stage y `.dockerignore`.

### Errores encontrados

| Error | Causa | Solucion |
|---|---|---|
| `ValueError: The output generated by func have different column names than the ones provided by get_feature_names_out` al entrenar | `FunctionTransformer(feature_names_out="one-to-one")` promete que la salida tiene las mismas columnas que la entrada, pero `add_derived_features` agrega `es_cortesia` | Funcion `derived_feature_names` que declara los nombres reales de salida |
| F1 de 0,747 sospechosamente alto | El umbral se elegia maximizando F1 sobre el propio conjunto de test | Tercera ventana temporal de validacion; el umbral se elige ahi y el test no se toca hasta el reporte. F1 honesto: 0,621 |
| `Pandas4Warning` en cada ejecucion del EDA | `select_dtypes("object")` esta deprecado en pandas 3, que tipa el texto como `str` | Helper `_text_columns` con `pd.api.types.is_string_dtype`, que funciona en pandas 2 y 3 |
| `umbral: 0.6699999999999999` en la respuesta de la API | Coma flotante de `np.linspace` propagada a la metadata | `round(..., 4)` en `choose_threshold` |
| Ruff: `PD901` no existe | La regla fue removida de ruff | Se quito de la lista de `ignore` en `pyproject.toml` |

### Docker

Se instalo Docker Engine nativo en WSL2 (`docker.io`, sin Docker Desktop) y se
verifico el contenedor:

- Imagen construida con `docker build --platform linux/amd64`: **701 MB**. Por
  encima del objetivo generico de <400 MB de la skill `u_docker_ml`, pero
  explicado por el stack: el `.venv` con pandas + scikit-learn + scipy + numpy
  pesa 398 MB por si solo, y la base `python:3.12-slim` aporta otros ~150 MB
  comprimidos. No hay grasa que recortar sin cambiar de libreria de modelado.
- Contenedor probado en un puerto no default (`PORT=9123`) para confirmar que
  no esta hardcodeado a 8000: `/health`, `/ready` y `/model-info` responden
  correctamente y `/predict` devuelve exactamente la misma prediccion que en
  local (`Not_Canceled`, probabilidad 0,0923, umbral 0,67).
- El proceso corre como `appuser` (uid 1000), no como root.

### Pendiente

- Despliegue en la nube: requiere autenticacion interactiva del proveedor. Los
  apendices por proveedor (Cloud Run, Azure Container Apps, AWS App Runner)
  estan listos en `~/.claude/agents/refs/u_cientifico_datos/`.

---

*Construido con el agente `u_cientifico_datos`.*
