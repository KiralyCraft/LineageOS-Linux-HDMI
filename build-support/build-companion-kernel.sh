#!/usr/bin/env bash
set -Eeuo pipefail

# This stage reconstructs build output only. It never installs a kernel/module.
KERNEL_SOURCE=${1:?pinned kernel checkout}
BUILD=${2:?isolated kernel output directory}
INPUT=${3:?captured running-kernel inputs}
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
LINEAGE=/bigdata/hdmi-los-build/cache/lineage-22.2-display
CLANG_DIR=$LINEAGE/prebuilts/clang/host/linux-x86/clang-r536225
PAHOLE=$LINEAGE/prebuilts/kernel-build-tools/linux-x86/bin/pahole
EXPECTED_CORE=d00ba216ccda5d4fcc0d864729ae69d5b63d860c
EXPECTED_RELEASE=5.15.176-gd00ba216ccda
JOBS=${HDMI_KERNEL_JOBS:-16}

[[ $(uname -m) == x86_64 ]] || {
    printf 'Run this build on root@192.168.104.201, not on the phone.\n' >&2; exit 1;
}
[[ $JOBS =~ ^[1-9][0-9]*$ && $JOBS -le 32 ]]
[[ $(git -C "$KERNEL_SOURCE" rev-parse HEAD) == "$EXPECTED_CORE" ]]
[[ -z $(git -C "$KERNEL_SOURCE" status --porcelain) ]]
[[ $(cat "$INPUT/kernel-release") == "$EXPECTED_RELEASE" ]]
[[ $($PAHOLE --version) == v1.25 ]]
"$CLANG_DIR/bin/clang" --version | grep -Fq 'Android (12701618,'
"$CLANG_DIR/bin/clang" --version | grep -Fq 'clang version 19.0.1'

export PATH="$CLANG_DIR/bin:/usr/bin:/bin"
export KBUILD_BUILD_USER=root KBUILD_BUILD_HOST=c8e1ea4e9ef0 KBUILD_BUILD_VERSION=1
export KBUILD_BUILD_TIMESTAMP='Sun Jun 8 00:30:34 UTC 2025'
mkdir -p "$BUILD"
if [[ -f $BUILD/.config ]]; then
    cmp "$INPUT/running.config" "$BUILD/.config"
else
    cp "$INPUT/running.config" "$BUILD/.config"
fi
tar -xJOf "$INPUT/kheaders.tar.xz" ./include/generated/autoconf.h \
    > "$BUILD/running-autoconf.h"

kernel_make=(make -C "$KERNEL_SOURCE" O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1
             PAHOLE="$PAHOLE")
"${kernel_make[@]}" olddefconfig
if ! cmp -s "$INPUT/running.config" "$BUILD/.config"; then
    diff -u "$INPUT/running.config" "$BUILD/.config" > "$BUILD/config-difference.patch" || true
    printf 'Effective configuration changed; inspect %s before building.\n' \
        "$BUILD/config-difference.patch" >&2
    exit 1
fi
"${kernel_make[@]}" -j"$JOBS" vmlinux modules
python3 "$SCRIPT_DIR/verify-kernel-autoconf.py" \
    "$BUILD/running-autoconf.h" "$BUILD/include/generated/autoconf.h"
[[ $(cat "$BUILD/include/config/kernel.release") == "$EXPECTED_RELEASE" ]]
test -s "$BUILD/Module.symvers"
printf 'Matching kernel build output and symbol versions: PASS\n'
