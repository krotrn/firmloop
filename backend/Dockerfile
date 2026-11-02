FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.11.31 /uv /bin/uv

ENV UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_FROZEN=1 \
    UV_COMPILE_BYTECODE=0 \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends libatomic1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# dependencies first (cached layer); dev group included so tests and lint run in this image
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv UV_LINK_MODE=copy uv sync --no-install-project

# pyright runs on node; fetch it once here (world-readable) instead of on every `make lint`.
# Pinned: resolving "latest" breaks whenever nodejs.org lists a version nodeenv can't parse.
ENV PYRIGHT_PYTHON_CACHE_DIR=/opt/pyright \
    PYRIGHT_PYTHON_NODE_VERSION=24.22.2
RUN pyright --version && chmod -R a+rX /opt/pyright
# on PATH so pyright finds node as a global binary (its nodeenv check retries an install otherwise)
ENV PATH="/opt/pyright/pyright-python/nodeenv/bin:$PATH"

COPY src ./src
COPY tests ./tests
COPY scripts ./scripts
RUN --mount=type=cache,target=/root/.cache/uv UV_LINK_MODE=copy uv sync

EXPOSE 8000

CMD ["uvicorn", "firmloop.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "3"]
