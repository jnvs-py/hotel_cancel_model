# syntax=docker/dockerfile:1
# Plantilla u_docker_ml. Ajusta: version de Python, ruta del modulo de la app.
# Construir SIEMPRE con: docker build --platform linux/amd64 -t <nombre>:local .

# ---------- Etapa 1: dependencias ----------
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

# El modelo ya viene entrenado: es un artefacto de build, no se entrena aqui.
COPY --chown=appuser:appuser models/ ./models/

USER appuser

EXPOSE 8000

# Ajusta la ruta si tu API no esta en /health.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,os,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','8000')+'/health').status==200 else 1)"

# Forma 'sh -c' a proposito: en la forma exec pura ${PORT} NO se expande.
CMD ["sh", "-c", "uvicorn src.api:app --host 0.0.0.0 --port ${PORT:-8000}"]
