#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE=${1:?source}
BUILD=${2:?build}
OUT=$BUILD/native
NDK=/bigdata/android-sdk/ndk/29.0.14206865/toolchains/llvm/prebuilt/linux-x86_64/bin
IMAGE=lfdevs/archlinuxarm:base-devel

rm -rf -- "$OUT"
mkdir -p "$OUT/android" "$OUT/chroot"

"$NDK/aarch64-linux-android35-clang++" -std=c++20 -O2 -fPIE -pie -pthread \
    -static-libstdc++ -Wall -Wextra -Werror \
    -I"$SOURCE/native/common" "$SOURCE/native/broker/main.cpp" \
    -o "$OUT/android/hdmi-losd"

docker run --rm --platform linux/arm64 \
    -v "$SOURCE:/source:ro" -v "$OUT/chroot:/output" "$IMAGE" bash -lc '
set -Eeuo pipefail
g++ -std=c++20 -O2 -fPIE -pie -pthread -Wall -Wextra -Werror \
  -I/source/native/common /source/native/agent/main.cpp -o /output/hdmi-los-agent
gcc -std=gnu17 -O2 -fPIE -pie -Wall -Wextra -Werror \
  /source/native/input-bridge/main.c -o /output/hdmi-input-bridge
gcc -std=gnu17 -O2 -fPIE -pie -Wall -Wextra -Werror \
  /source/native/capture-keeper/main.c -o /output/hdmi-capture-keeper
/output/hdmi-los-agent --help >/dev/null 2>&1 || test $? = 2
/output/hdmi-input-bridge >/dev/null 2>&1 || test $? = 2
/output/hdmi-capture-keeper >/dev/null 2>&1 || test $? = 2
'

file "$OUT/android/hdmi-losd" "$OUT/chroot/"*
readelf -h "$OUT/android/hdmi-losd" | grep -q 'AArch64'
for binary in "$OUT/chroot/"*; do readelf -h "$binary" | grep -q 'AArch64'; done

