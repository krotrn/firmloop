FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends g++ python3 python3-venv ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.11.31 /uv /bin/uv

WORKDIR /opt/runner

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/opt/runner/.venv \
    uv sync --frozen --no-dev --python /usr/bin/python3

# Harness objects baked at build time (ARCHITECTURE 8.2). Headers stay at /opt/firmloop/src so the
# runtime compile of generated code uses the same headers as the baked .o files (D-061).
COPY --from=firmware lib/FirmLoop/src/ /opt/firmloop/src/
COPY --from=firmware native/fake_hal.h native/fake_hal.cpp native/harness_main.cpp /opt/harness-src/
RUN mkdir -p /opt/harness \
    && for f in fake_hal harness_main; do \
        g++ -std=c++17 -O1 -pipe -Wall -Wextra -Werror=return-type -Werror=uninitialized \
            -fno-exceptions -fno-rtti -fstack-protector-strong \
            -I/opt/firmloop/src -I/opt/harness-src \
            -c /opt/harness-src/$f.cpp -o /opt/harness/$f.o; \
    done \
    && chmod -R a+rX /opt/harness /opt/firmloop

COPY app.py native.py ./

RUN groupadd --gid 10001 runner \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin runner

ENV TMPDIR=/tmp PYTHONDONTWRITEBYTECODE=1

USER 10001:10001

EXPOSE 8101

CMD ["/opt/runner/.venv/bin/uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8101"]
