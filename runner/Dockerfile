FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends g++ python3 python3-venv ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.11.31 /uv /bin/uv

WORKDIR /opt/runner

COPY pyproject.toml uv.lock ./
RUN UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/opt/runner/.venv \
    uv sync --frozen --no-dev --python /usr/bin/python3

COPY app.py ./

RUN groupadd --gid 10001 runner \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin runner

ENV TMPDIR=/tmp PYTHONDONTWRITEBYTECODE=1

USER 10001:10001

EXPOSE 8101

CMD ["/opt/runner/.venv/bin/uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8101"]
