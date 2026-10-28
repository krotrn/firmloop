FROM python:3.12-slim-bookworm

ARG ARDUINO_CLI_VERSION=1.5.1
ARG ESP32_CORE_VERSION=3.3.12

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN set -eu; \
    base="https://github.com/arduino/arduino-cli/releases/download/v${ARDUINO_CLI_VERSION}"; \
    tarball="arduino-cli_${ARDUINO_CLI_VERSION}_Linux_64bit.tar.gz"; \
    mkdir /tmp/dl && cd /tmp/dl; \
    curl -fsSL --retry 5 --retry-all-errors --connect-timeout 15 --max-time 300 -O "${base}/${tarball}"; \
    curl -fsSL --retry 5 --retry-all-errors --connect-timeout 15 --max-time 300 -O "${base}/${ARDUINO_CLI_VERSION}-checksums.txt"; \
    grep " ${tarball}\$" "${ARDUINO_CLI_VERSION}-checksums.txt" | sha256sum -c -; \
    tar -xzf "${tarball}" -C /usr/local/bin arduino-cli; \
    cd / && rm -rf /tmp/dl

# Toolchain lives in the image (not a volume) at fixed, world-readable paths.
ENV ARDUINO_DIRECTORIES_DATA=/opt/arduino15 \
    ARDUINO_DIRECTORIES_DOWNLOADS=/opt/arduino15/staging \
    ARDUINO_DIRECTORIES_USER=/opt/arduino-user \
    ARDUINO_BOARD_MANAGER_ADDITIONAL_URLS=https://espressif.github.io/arduino-esp32/package_esp32_index.json

# Install the core (retried: the toolchain download is flaky), then drop tools
# not needed to build/flash the plain ESP32 target (other chips, gdb, openocd).
# Kept: esp-x32 (xtensa toolchain), esp32-libs, esptool_py, mkspiffs, mklittlefs.
# library_index.json is kept: manifest libraries (later task) need it for lib install.
RUN set -eu; \
    arduino-cli core update-index; \
    for i in 1 2 3; do \
        arduino-cli core install "esp32:esp32@${ESP32_CORE_VERSION}" && break; \
        [ "$i" -lt 3 ] || exit 1; \
        echo "core install failed (try $i), retrying" >&2; sleep 5; \
    done; \
    rm -rf /opt/arduino15/staging; \
    cd /opt/arduino15/packages/esp32/tools; \
    rm -rf esp-rv32 esp32s2-libs esp32s3-libs esp32c3-libs esp32c5-libs esp32c6-libs \
        esp32h2-libs esp32p4-libs esp32p4_es-libs riscv32-esp-elf-gdb xtensa-esp-elf-gdb \
        openocd-esp32; \
    echo "tools after cleanup:"; ls -1 .; \
    mkdir -p /opt/arduino-user; \
    chmod -R a+rX /opt/arduino15 /opt/arduino-user

# TODO(later task): install manifest libraries here (arduino-cli lib install ...).

COPY --from=ghcr.io/astral-sh/uv:0.11.31 /uv /bin/uv

WORKDIR /opt/builder
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/opt/builder/.venv \
    uv sync --frozen --no-dev
COPY app.py ./

EXPOSE 8102
CMD ["/opt/builder/.venv/bin/uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8102"]
