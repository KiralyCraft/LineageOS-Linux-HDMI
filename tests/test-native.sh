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
    -I"$ROOT/native/common" "$ROOT/native/drm-trace/drmtrace.c" -ldl \
    -o "$TEMP/libhdmi-los-drmtrace.so"
gcc -std=gnu17 -O2 -fPIE -pie -Wall -Wextra -Werror \
    -I"$ROOT/native/common" "$ROOT/native/drm-trace/selftest.c" \
    -o "$TEMP/drmtrace-selftest"

if pkg-config --exists sdl2 gl; then
    gcc -std=c11 -O2 -fPIE -pie -Wall -Wextra -Werror \
        "$ROOT/native/probes/sdl-frame-pacing.c" \
        $(pkg-config --cflags --libs sdl2 gl) \
        -o "$TEMP/sdl-frame-pacing"
    "$TEMP/sdl-frame-pacing" --help >/dev/null
fi

if pkg-config --exists x11 xtst xfixes; then
    gcc -std=c11 -O2 -fPIE -pie -Wall -Wextra -Werror \
        "$ROOT/native/probes/x11-cursor-stress.c" \
        $(pkg-config --cflags --libs x11 xtst xfixes) \
        -o "$TEMP/x11-cursor-stress"
    "$TEMP/x11-cursor-stress" --help >/dev/null
fi

"$TEMP/drmtrace-selftest" "$TEMP/libhdmi-los-drmtrace.so"
if "$TEMP/hdmi-losd" probe invalid >/dev/null 2>&1; then
    printf 'invalid broker probe unexpectedly succeeded\n' >&2
    exit 1
else
    test $? = 2
fi
printf 'native diagnostic builds and CLI tests: PASS\n'
