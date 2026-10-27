FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.11.31 /uv /bin/uv

WORKDIR /opt/device

COPY pyproject.toml uv.lock ./
RUN UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/opt/device/.venv \
    uv sync --frozen --no-dev

COPY app.py ./

ENV PYTHONDONTWRITEBYTECODE=1

EXPOSE 8103

CMD ["/opt/device/.venv/bin/uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8103"]
