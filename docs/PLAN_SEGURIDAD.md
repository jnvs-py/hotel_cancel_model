# Plan de seguridad de la API (pendiente de implementar)

> Escrito el 2026-09-07, antes del despliegue en la nube. NO implementa
> codigo: es la especificacion para ejecutar cuando se toque el deploy.
> Assessment verificado el dia de la escritura contra `src/` y el lock.

## Estado actual (lo que NO hay que tocar)

Verificado el 2026-09-07 con secret scan, revision de codigo y lock:

| Aspecto | Estado | Donde |
|---|---|---|
| Validacion de entrada | Estricta: rangos de `NUMERIC_RANGES`, enums `Literal`, `extra="forbid"`, validador cruzado de fechas. Campo mal escrito = 422, no prediccion basura | `src/api.py:69-138` |
| Errores | Tracebacks solo al log; respuesta tipada con `request_id`. Nunca filtra estructura interna | `src/api.py:160-168` |
| Secretos | Cero en el repo (scan limpio). Todo por variables de entorno | - |
| Contenedor | Corre como `appuser` uid 1000, no root | `Dockerfile:31,42` |
| Dependencias | Al dia, sin CVEs conocidos: fastapi 0.141.1, pydantic 2.13.5, starlette 1.6.0, scikit-learn 1.9.0 | `uv.lock` |
| Datos | Sin PII (reservas de hotel, sin nombres/emails). Superficie de privacidad minima | `data/hotel_reservations.csv` |
| Carga defensiva | El artefacto verifica su version de sklearn antes de servir; artefacto corrupto = error claro, no prediccion basura | `src/model.py:94-100` |
| HTTPS | Lo termina la plataforma al desplegar (Cloud Run / App Runner / Container Apps son HTTPS por defecto). Nada que hacer en el codigo | - |

## Gaps, por severidad

| # | Gap | Severidad | Nota |
|---|---|---|---|
| 1 | Cero autenticacion: cualquiera con la URL llama `/predict` 24/7 | Alta | Unico gap critico. Resuelto con la API key de abajo |
| 2 | `/model-info` expone threshold, fingerprint y metricas sin filtro | Baja | Inteligencia gratuita para un atacante. Se protege igual que `/predict` |
| 3 | `/docs` y `/openapi.json` publicos | Aceptado | Decision de diseno: es la vitrina de la demo (ver abajo) |
| 4 | Sin rate limiting | Baja | Lo cubre la plataforma (Cloud Run quotas/concurrency nativas). NO agregar `slowapi`: una dependencia mas para un beneficio que el proveedor ya da |
| 5 | CORS ausente | No-action | SIN `CORSMiddleware` un navegador de terceros NO puede abusarla desde JS. Solo agregar si aparece un frontend web |

## Implementacion: API key por header

### Comportamiento esperado

- `os.environ["API_KEY"]` definido -> `/predict` y `/model-info` exigen header
  `X-API-Key` con ese valor. `/health` y `/ready` quedan ABIERTOS: los
  orquestadores de la nube (probes de liveness/readiness) no mandan
  credenciales y si exigieran la key, la plataforma reiniciaria el contenedor.
- `os.environ["API_KEY"]` ausente -> chequeo desactivado. Local, tests y CI
  actuales no se rompen. La key se activa SOLO definiendo la variable en la
  plataforma el dia del deploy.
- `/docs` queda publica y con security scheme: aparece el candado en Swagger
  UI y el usuario pega la key para probar desde ahi.

### Cambios por archivo

1. `src/api.py`
   - Dependencia nueva `requerir_api_key` (FastAPI `Header` + `Security`);
     comparacion con `secrets.compare_digest` (constante-time, no `==`).
   - 401 si falta el header; 403 si el valor no coincide.
   - Aplicar en `predict` (`src/api.py:197`) y `model_info`
     (`src/api.py:189`). NO en `health`/`ready`.
   - Registrar `APIKeyHeader` de `fastapi.security` al crear la app
     (`src/api.py:56`) para el candado en `/docs`.
2. `tests/test_api.py`
   - 3 tests nuevos con `monkeypatch.setenv("API_KEY", "test-key")`:
     401 sin header, 403 con key errada, 200 con key correcta.
   - Los tests existentes siguen pasando sin definir la variable.
3. `.github/workflows/ci.yml` y `.github/workflows/release.yml`
   - El smoke test que llama a `/predict` y al chequeo de `/model-info`
     debe enviar `-H "X-API-Key: test-key"` y arrancar el contenedor con
     `-e API_KEY=test-key`. Es una key de demo, no un secreto real: va en
     el workflow, NO en GitHub Secrets.
4. `docs/` + `README.md` + `CHANGELOG.md`
   - Seccion "Seguridad" en el README: como definir `API_KEY` en cada
     plataforma y como probar con curl.
   - Entrada en el CHANGELOG.

### Verificacion

- `uv run ruff check .` y `uv run pytest` (63 -> ~66 tests, cobertura >= 80%).
- Local con `API_KEY=demo` definido: curl sin header = 401, con header = 200.
- Local sin `API_KEY`: sin header = 200 (comportamiento actual intacto).
- Push y CI en verde.

## Decisiones ya tomadas (no re-discutir)

| Decision | Por que |
|---|---|
| API key por header, no auth nativa de nube (IAM de Cloud Run, etc.) | Portabilidad: funciona igual en Google/Azure/AWS y el companiero puede llamarla con un simple curl |
| API key estatica por variable de entorno | Suficiente para un hito academico; rotacion = cambiar la variable en la plataforma |
| `/docs` publica | Es la demo del proyecto; el schema OpenAPI ya describe un contrato publico. El riesgo real lo cubre la key en `/predict` |
| Rate limiting por la plataforma | `slowapi` es una dependencia extra que duplica lo que Cloud Run ya ofrece |
| Sin `CORSMiddleware` | Su ausencia ES la proteccion; se agrega solo si hay frontend web |
| Key de demo en los workflows, no en Secrets | No hay ningun secreto real que proteger en CI; el proyecto no habla con servicios pagos |

## Criterio de activacion

El dia del despliegue en la nube (item pendiente del CHANGELOG), definir
`API_KEY` en la plataforma y comunicar la clave solo a quien llame la API.
Sin la variable definida, la API queda publica: eso es explicito y debe
ser una decision consciente, no un olvido.
