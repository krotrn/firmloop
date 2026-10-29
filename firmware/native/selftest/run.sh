#!/bin/sh
# Firmware native self-tests (tasks 1.6-1.8). Runs inside the runner image:
#   docker compose run --rm --no-deps runner sh /repo/firmware/native/selftest/run.sh
# Compiles at test time into a fresh dir under /work (tmpfs, exec); the container is read-only.
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
NATIVE=$HERE/..
LIB=$NATIVE/../lib/FirmLoop/src
B=$(mktemp -d /work/selftest.XXXXXX)
trap 'rm -rf "$B"' EXIT

CXXFLAGS="-std=c++17 -O1 -pipe -Wall -Wextra -Werror=return-type -Werror=uninitialized \
  -fno-exceptions -fno-rtti -fstack-protector-strong -I$LIB -I$NATIVE"

echo "== build fake_hal + harness"
g++ $CXXFLAGS -c "$NATIVE/fake_hal.cpp" -o "$B/fake_hal.o"
g++ $CXXFLAGS -c "$NATIVE/harness_main.cpp" -o "$B/harness_main.o"

echo "== unit_check (fl_json + fake_hal)"
g++ $CXXFLAGS "$HERE/unit_check.cpp" "$NATIVE/fake_hal.cpp" "$HERE/channels.generated.cpp" \
  -lm -o "$B/unit_check"
"$B/unit_check"

echo "== core_check (fl_core: ESP32 runtime logic)"
g++ $CXXFLAGS "$HERE/core_check.cpp" "$NATIVE/fake_hal.cpp" "$HERE/channels.generated.cpp" -lm -o "$B/core_check"
"$B/core_check"

echo "== harness cases"
python3 "$HERE/check.py" "$B"
