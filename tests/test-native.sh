#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=$(git rev-parse --show-toplevel)
TEMP=$(mktemp -d)
trap 'rm -rf -- "$TEMP"' EXIT

g++ -std=c++20 -O2 -fPIE -pie -pthread -Wall -Wextra -Werror \
    -I"$ROOT/native/common" "$ROOT/native/broker/main.cpp" \
    -o "$TEMP/hdmi-losd"
g++ -std=c++20 -O2 -fPIE -pie -pthread -Wall -Wextra -Werror \
    -I"$ROOT/native/common" "$ROOT/native/agent/main.cpp" \
    -o "$TEMP/hdmi-los-agent"
gcc -std=gnu17 -O2 -fPIC -shared -Wall -Wextra -Werror \
    -I"$ROOT/native/common" "$ROOT/native/drm-trace/drmtrace.c" \
    -o "$TEMP/libhdmi-los-drmtrace.so"
gcc -std=gnu17 -O2 -fPIE -pie -Wall -Wextra -Werror \
    -I"$ROOT/native/common" "$ROOT/native/drm-trace/selftest.c" \
    -o "$TEMP/drmtrace-selftest"

"$TEMP/drmtrace-selftest" "$TEMP/libhdmi-los-drmtrace.so"
if "$TEMP/hdmi-losd" probe invalid >/dev/null 2>&1; then
    printf 'invalid broker probe unexpectedly succeeded\n' >&2
    exit 1
else
    test $? = 2
fi
printf 'native diagnostic builds and CLI tests: PASS\n'
