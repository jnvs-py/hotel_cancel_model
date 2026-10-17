# Runbook — API de cancelacion de reservas

Una seccion por alerta de `monitoring/alert_rules.yml`. Si una alerta no tiene
una accion concreta aqui, se borra la alerta.

**Responsable de guardia:** el autor del proyecto (Nicolas Serrato).
**Canal de escalado:** repositorio `jnvs-py/hotel_cancel_model` (issue).
**Donde mirar:**
- Logs: stdout del servicio (plataforma de logs en la nube; en local
  `docker logs hito1-ngrok`). Cada linea es un JSON con `event`.
- Metricas: `/metrics` (Prometheus) en local; en la nube, metricas derivadas
  de los eventos `prediction`.
- Modelo servido: `GET /model-info` (version, fingerprint, umbral, metricas).
- Drift: `reports/drift/<fecha>.md` del ultimo job.

**Consulta base para correlacionar:** filtrar logs por `request_id`, o por
`model_version` + `status="error"`.

---

## ModeloNoCargado

- **Significa:** el artefacto no cargo al arrancar; `/ready` responde 503 y no
  entra trafico. `/health` sigue en 200 a proposito.
- **Confirmar:** buscar el evento `CRITICAL` `model_load_failed` en los logs
  del ultimo arranque; el campo `error` trae el motivo exacto.
- **Causas tipicas:** `models/pipeline.joblib` ausente en la imagen, version
  de scikit-learn distinta a la de `metadata.json`, artefacto corrupto.
- **Accion:** volver a la revision anterior (rollback del contenedor) y abrir
  ticket. No parchear en caliente.

## TasaDeErrorAlta

- **Significa:** mas del 2% de `/predict` termina en 500.
- **Confirmar:** logs `level=ERROR event=prediction_failed` (fallo del
  pipeline) y `event=unhandled_error` (cualquier otro). Agrupar por la primera
  linea de `exc_info`.
- **Causas tipicas:** despliegue reciente, una categoria nueva que el encoder
  no conoce, dependencia caida.
- **Accion:** si coincide con un despliegue, rollback. Si no, tomar 3
  `request_id`, reproducir en local con el mismo payload y el mismo
  `model_version`. Nota: el contrato estricto ya rechaza lo invalido con 422,
  asi que un 500 aqui es siempre un bug.

## LatenciaP99Alta

- **Significa:** p99 de `/predict` por encima de 1s durante 10 minutos.
- **Confirmar:** panel de latencia por etapa (`ml_stage_latency_seconds`):
  `to_frame`, `predict` o `postprocess`. El evento `prediction` trae `stage_ms`.
- **Causas tipicas:** arranques en frio (min-instances en 0), CPU saturada,
  modelo nuevo mas pesado.
- **Accion:** revisar CPU/memoria y replicas; subir el minimo de instancias si
  son arranques en frio. Referencia medida: pipeline ~8 ms, instrumentacion
  < 1 ms.

## InputsInvalidos

- **Significa:** un cliente esta mandando datos que el contrato rechaza (422).
- **Confirmar:** `ml_input_invalid_total` por `field`; logs
  `event=input_invalid` con la lista `fields`.
- **Causas tipicas:** cambio en el sistema que llama (campo renombrado, unidad
  distinta, mes/dia imposible).
- **Accion:** contactar al cliente con el `request_id` de ejemplo. No relajar
  el contrato sin revisarlo con quien entreno el modelo.

## DistribucionDePrediccionesCambio

- **Significa:** la proporcion de `Canceled` predichos se alejo mas de 10
  puntos del baseline (0,4617).
- **Confirmar:** comparar con el reporte de drift mas reciente; revisar si
  cambio la mezcla de reservas (canal, precio, mes).
- **Accion:** ticket de revision. Si hay etiquetas recientes, calcular la
  metrica real (F1 con umbral 0.67) antes de decidir. No reentrenar a ciegas.

## DriftDeEntrada

- **Significa:** PSI > 0.2 en una feature frente al baseline de entrenamiento.
- **Confirmar:** abrir `reports/drift/<fecha>.md`; mirar el estado por feature
  y la columna de calidad. Distinguir cambio real del mundo vs. bug en el
  pipeline de datos (nulos, constante, fuera de rango, categoria nueva).
- **Contexto del dataset:** el EDA ya documento deriva temporal fuerte
  (cancelacion 2,37% en dic-2017 a 46,55% en ago-2018). Una ventana del
  periodo de test YA marca drift contra el baseline de validacion: eso es
  esperado, no un incidente.
- **Accion:** ticket. Reentrenar solo tras validar con la misma evaluacion y
  el mismo contrato; sube `model_version`, regenera `baseline.json` y se
  documenta en el CHANGELOG. Nunca reemplazo automatico.

---

## Verificacion post-despliegue (canary)

Cuando exista URL publica (Fase 8 del plan del agente), tras cada despliegue:

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://<url>/health     # 200
curl -s https://<url>/ready | python -m json.tool                  # ready: true
curl -s -X POST https://<url>/predict -H "Content-Type: application/json" \
  -d @tests/fixtures/request_ejemplo.json                          # misma prediccion
```

Ventana de observacion: 30 minutos. Criterios de regresion: `/health` distinto
de 200, `/ready` en false, latencia p99 por encima de 2x el baseline local, o
cualquier 500.
