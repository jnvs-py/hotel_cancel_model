# syntax=docker/dockerfile:1
# Plantilla u_docker_ml. Ajusta: version de Python, ruta del modulo de la app.
# Construir SIEMPRE con: docker build --platform linux/amd64 -t <nombre>:local .
#
# El modelo se ENTRENA EN LA ETAPA DE BUILD, nunca en runtime. Asi la imagen es
# autocontenida: se construye desde un clon limpio del repo (Render, cualquier
# registro) sin pasos previos. El runtime solo recibe el artefacto ya entrenado.

# ---------- Etapa 1: dependencias + entrenamiento ----------
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Las dependencias primero: esta capa se cachea y no se rehace al cambiar codigo.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Ahora si el codigo del proyecto.
COPY src/ ./src/
RUN uv sync --frozen --no-dev

# Datos y entrenamiento. Determinista por SEED: el mismo par (codigo, datos)
# produce el mismo artefacto, que es justo lo que mide el fingerprint de CI.
COPY data/ ./data/

# Los build args entran por ENV para que la expansion la haga el shell y no el
# frontend de BuildKit (que expande los ARG dentro de RUN y vuelve confuso el log).
ARG MODEL_VERSION=""
ARG FINGERPRINT=""
# Render exporta RENDER_GIT_COMMIT y traduce sus env vars a build args, pero
# Docker solo acepta el valor si el ARG esta declarado: con esto la imagen
# desplegada en Render queda versionada como {pyproject}+{sha del commit}.
ARG RENDER_GIT_COMMIT=""
ENV MODEL_VERSION_IN="${MODEL_VERSION}" \
    FINGERPRINT_IN="${FINGERPRINT}"

# Ojo: se llama al python del venv, NO a `uv run`. `uv run` re-sincroniza el
# entorno y reinstala el grupo dev (matplotlib, ruff, pytest...) dentro del
# build, engordando el .venv que se copia a la imagen final.
# LOKY_MAX_CPU_COUNT=1 evita que joblib intente levantar un pool de procesos.
RUN BASE=$(grep -m1 '^version' pyproject.toml | cut -d '"' -f2) \
    && SHA=$(printf '%s' "${RENDER_GIT_COMMIT:-local}" | cut -c1-7) \
    && VERSION="${MODEL_VERSION_IN:-${BASE}+${SHA}}" \
    && echo "entrenando el modelo ${VERSION}" \
    && MODEL_VERSION="$VERSION" FINGERPRINT="${FINGERPRINT_IN}" LOKY_MAX_CPU_COUNT=1 \
       .venv/bin/python -m src.train \
    && echo "modelo entrenado en build: ${VERSION}"

# ---------- Etapa 2: runtime ----------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

RUN useradd --create-home --uid 1000 appuser

WORKDIR /app

# Solo el entorno resuelto y el codigo. Nada de uv, compiladores ni caches.
COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv
COPY --chown=appuser:appuser src/ ./src/

# El artefacto viene YA ENTRENADO de la etapa de build: en runtime no se entrena.
COPY --from=builder --chown=appuser:appuser /app/models ./models/

# Formulario de demostracion servido en /app (mismo origen que la API).
COPY --chown=appuser:appuser static/ ./static/

USER appuser

EXPOSE 8000

# Ajusta la ruta si tu API no esta en /health.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,os,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','8000')+'/health').status==200 else 1)"

# Forma 'sh -c' a proposito: en la forma exec pura ${PORT} NO se expande.
CMD ["sh", "-c", "uvicorn src.api:app --host 0.0.0.0 --port ${PORT:-8000}"]
