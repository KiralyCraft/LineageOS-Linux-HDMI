#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE=${1:?source}
BUILD=${2:?build}
OUT=$BUILD/native
NDK=/bigdata/android-sdk/ndk/29.0.14206865/toolchains/llvm/prebuilt/linux-x86_64/bin
IMAGE=lfdevs/archlinuxarm:base-devel

rm -rf -- "$OUT"
mkdir -p "$OUT/android" "$OUT/chroot/bin" "$OUT/chroot/lib" "$OUT/tests"

"$NDK/aarch64-linux-android35-clang++" -std=c++20 -O2 -fPIE -pie -pthread \
    -static-libstdc++ -Wall -Wextra -Werror \
    -I"$SOURCE/native/common" "$SOURCE/native/broker/main.cpp" \
    -o "$OUT/android/hdmi-losd"

docker run --rm --platform linux/arm64 \
    -v "$SOURCE:/source:ro" -v "$OUT:/output" "$IMAGE" bash -lc '
set -Eeuo pipefail
g++ -std=c++20 -O2 -fPIE -pie -pthread -Wall -Wextra -Werror \
  -I/source/native/common /source/native/agent/main.cpp -o /output/chroot/bin/hdmi-los-agent
gcc -std=gnu17 -O2 -fPIE -pie -Wall -Wextra -Werror \
  /source/native/input-bridge/main.c -o /output/chroot/bin/hdmi-input-bridge
gcc -std=gnu17 -O2 -fPIE -pie -Wall -Wextra -Werror \
  /source/native/capture-keeper/main.c -o /output/chroot/bin/hdmi-capture-keeper
gcc -std=gnu17 -O2 -fPIC -shared -Wall -Wextra -Werror \
  -I/source/native/common /source/native/drm-trace/drmtrace.c \
  -o /output/chroot/lib/libhdmi-los-drmtrace.so
gcc -std=gnu17 -O2 -fPIE -pie -Wall -Wextra -Werror \
  -I/source/native/common /source/native/drm-trace/selftest.c \
  -o /output/tests/drmtrace-selftest
/output/chroot/bin/hdmi-los-agent --help >/dev/null 2>&1 || test $? = 2
/output/chroot/bin/hdmi-input-bridge >/dev/null 2>&1 || test $? = 2
/output/chroot/bin/hdmi-capture-keeper >/dev/null 2>&1 || test $? = 2
/output/tests/drmtrace-selftest /output/chroot/lib/libhdmi-los-drmtrace.so
'

file "$OUT/android/hdmi-losd" "$OUT/chroot/bin/"* "$OUT/chroot/lib/"*
readelf -h "$OUT/android/hdmi-losd" | grep -q 'AArch64'
for binary in "$OUT/chroot/bin/"* "$OUT/chroot/lib/"*; do
  readelf -h "$binary" | grep -q 'AArch64'
done
