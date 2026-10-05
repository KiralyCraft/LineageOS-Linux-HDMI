#!/usr/bin/env bash
set -Eeuo pipefail
SOURCE=${1:?HDMI source snapshot}
KERNEL_SOURCE=${2:?pinned kernel checkout}
KERNEL_BUILD=${3:?matching kernel build output}
INPUT=${4:?captured running-kernel inputs}
BUILD=${5:?isolated timing output}
SOURCE_COMMIT=${6:?full HDMI source commit}
CLANG_DIR=/bigdata/hdmi-los-build/cache/lineage-22.2-display/prebuilts/clang/host/linux-x86/clang-r536225
PAHOLE=/bigdata/hdmi-los-build/cache/lineage-22.2-display/prebuilts/kernel-build-tools/linux-x86/bin/pahole
NDK_DIR=/bigdata/android-sdk/ndk/29.0.14206865/toolchains/llvm/prebuilt/linux-x86_64
[[ $(uname -m) == x86_64 ]]
[[ $SOURCE_COMMIT =~ ^[0-9a-f]{40}$ ]]
[[ $(git -C "$SOURCE" rev-parse HEAD) == "$SOURCE_COMMIT" ]]
[[ $(git -C "$KERNEL_SOURCE" rev-parse HEAD) == d00ba216ccda5d4fcc0d864729ae69d5b63d860c ]]
cmp "$INPUT/running.config" "$KERNEL_BUILD/.config"
python3 "$SOURCE/build-support/verify-kernel-autoconf.py" \
    "$KERNEL_BUILD/running-autoconf.h" "$KERNEL_BUILD/include/generated/autoconf.h"
test -s "$KERNEL_BUILD/Module.symvers"
mkdir -p "$BUILD/kmod-src" "$BUILD/bin" "$BUILD/output"
python3 - "$SOURCE" "$KERNEL_BUILD" "$INPUT" "$CLANG_DIR" "$PAHOLE" "$BUILD" "$SOURCE_COMMIT" <<'PY'
import hashlib, json, pathlib, subprocess, sys
source, kernel, inputs, clang, pahole, build = map(pathlib.Path, sys.argv[1:7])
def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
    return h.hexdigest()
files=['kernel/hdmi_companion/session.c','kernel/hdmi_companion/uapi.h',
       'kernel/hdmi_companion/Kbuild','kernel/hdmi_companion/presenter.c',
       'kernel/hdmi_companion/presenter.h','native/companion-probe/main.c',
       'build-support/build-companion-timing.sh','build-support/verify-companion-probe.py']
identity={'schema':1,'stage':'experimental timing guard and restricted fence-driven presenter',
          'source_commit':sys.argv[7], 'kernel_commit':'d00ba216ccda5d4fcc0d864729ae69d5b63d860c',
          'vendor_modules_commit':'ec2e039129f2b8f93fdfe62a8c6a595efb63d496',
          'kernel_release':(inputs/'kernel-release').read_text().strip(),
          'runtime_config_sha256':digest(inputs/'running.config'),
          'runtime_kheaders_sha256':digest(inputs/'kheaders.tar.xz'),
          'kernel_symvers_sha256':digest(kernel/'Module.symvers'),
          'installed_msm_sha256':digest(inputs/'installed-msm_drm.ko'),
          'compiler':subprocess.check_output([str(clang/'bin/clang'),'--version'],text=True).strip(),
          'compiler_sha256':digest(clang/'bin/clang'), 'pahole_sha256':digest(pahole),
          'source_files':{name:digest(source/name) for name in files},
          'on_device_load_test':'pending; not installed or loaded'}
identity['build_id']=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
(build/'identity.json').write_text(json.dumps(identity,indent=2)+'\n')
(build/'kmod-src/build-identity.h').write_text('#define HDMI_COMPANION_BUILD_ID "'+identity['build_id']+'"\n')
(build/'build-id').write_text(identity['build_id']+'\n')
PY
cp "$SOURCE/kernel/hdmi_companion/"{Kbuild,session.c,presenter.c,presenter.h,uapi.h} "$BUILD/kmod-src/"
export PATH="$CLANG_DIR/bin:/usr/bin:/bin"
export KBUILD_BUILD_USER=root KBUILD_BUILD_HOST=c8e1ea4e9ef0 KBUILD_BUILD_VERSION=1
export KBUILD_BUILD_TIMESTAMP='Sun Jun 8 00:30:34 UTC 2025'
make -C "$KERNEL_SOURCE" O="$KERNEL_BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 \
    PAHOLE="$PAHOLE" M="$BUILD/kmod-src" HDMI_COMPANION_FUNCTIONAL=1 W=1 modules
"$NDK_DIR/bin/aarch64-linux-android35-clang" -std=gnu17 -O2 -fPIE -pie \
    -Wall -Wextra -Werror "$SOURCE/native/companion-probe/main.c" -o "$BUILD/bin/hdmi-companion-probe"
python3 "$SOURCE/build-support/verify-companion-probe.py" \
    "$BUILD/kmod-src/hdmi_companion.ko" "$INPUT/installed-msm_drm.ko" \
    "$KERNEL_BUILD/Module.symvers" "$SOURCE/kernel/hdmi_companion/uapi.h" \
    "$(cat "$INPUT/kernel-release")" "$(cat "$BUILD/build-id")" \
    --timing --output "$BUILD/output/abi-report.json"
cp "$BUILD/kmod-src/hdmi_companion.ko" "$BUILD/output/"
cp "$BUILD/bin/hdmi-companion-probe" "$BUILD/output/"
cp "$BUILD/identity.json" "$BUILD/output/manifest.json"
(cd "$BUILD/output" && sha256sum abi-report.json manifest.json hdmi_companion.ko hdmi-companion-probe > SHA256SUMS)
printf 'Timing guard built, not installed or loaded: %s\n' "$BUILD/output"
