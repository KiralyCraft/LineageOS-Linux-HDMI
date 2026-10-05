#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE=${1:?HDMI source snapshot}
KERNEL_SOURCE=${2:?pinned kernel checkout}
KERNEL_BUILD=${3:?matching kernel build output}
INPUT=${4:?captured running-kernel inputs}
BUILD=${5:?isolated probe output}
SOURCE_COMMIT=${6:?full HDMI source commit}
LINEAGE=/bigdata/hdmi-los-build/cache/lineage-22.2-display
CLANG_DIR=$LINEAGE/prebuilts/clang/host/linux-x86/clang-r536225
PAHOLE=$LINEAGE/prebuilts/kernel-build-tools/linux-x86/bin/pahole
NDK_DIR=/bigdata/android-sdk/ndk/29.0.14206865/toolchains/llvm/prebuilt/linux-x86_64

[[ $(uname -m) == x86_64 ]] || {
    printf 'Run this build on root@192.168.104.201, not on the phone.\n' >&2; exit 1;
}
[[ $SOURCE_COMMIT =~ ^[0-9a-f]{40}$ ]]
[[ $(git -C "$KERNEL_SOURCE" rev-parse HEAD) == d00ba216ccda5d4fcc0d864729ae69d5b63d860c ]]
cmp "$INPUT/running.config" "$KERNEL_BUILD/.config"
cmp "$KERNEL_BUILD/running-autoconf.h" "$KERNEL_BUILD/include/generated/autoconf.h"
test -s "$KERNEL_BUILD/Module.symvers"
mkdir -p "$BUILD/kmod-src" "$BUILD/bin" "$BUILD/output" "$BUILD/tests"

python3 - "$SOURCE" "$KERNEL_BUILD" "$INPUT" "$CLANG_DIR" "$PAHOLE" \
    "$BUILD" "$SOURCE_COMMIT" <<'PY'
import hashlib, json, pathlib, subprocess, sys
source, kernel, inputs, clang, pahole, build = map(pathlib.Path, sys.argv[1:7])
commit = sys.argv[7]
def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''): h.update(block)
    return h.hexdigest()
identity = {
    'schema': 1,
    'stage': 'query-only compatibility probe',
    'source_commit': commit,
    'kernel_release': (inputs / 'kernel-release').read_text().strip(),
    'kernel_commit': 'd00ba216ccda5d4fcc0d864729ae69d5b63d860c',
    'vendor_modules_commit': 'ec2e039129f2b8f93fdfe62a8c6a595efb63d496',
    'runtime_config_sha256': digest(inputs / 'running.config'),
    'runtime_kheaders_sha256': digest(inputs / 'kheaders.tar.xz'),
    'kernel_symvers_sha256': digest(kernel / 'Module.symvers'),
    'installed_msm_sha256': digest(inputs / 'installed-msm_drm.ko'),
    'compiler': subprocess.check_output([str(clang / 'bin/clang'), '--version'], text=True).strip(),
    'compiler_sha256': digest(clang / 'bin/clang'),
    'pahole_version': subprocess.check_output([str(pahole), '--version'], text=True).strip(),
    'pahole_sha256': digest(pahole),
    'source_files': {name: digest(source / name) for name in [
        'kernel/hdmi_companion/probe.c', 'kernel/hdmi_companion/uapi.h',
        'kernel/hdmi_companion/Kbuild', 'native/companion-probe/main.c',
        'build-support/build-companion-probe.sh', 'build-support/verify-companion-probe.py']},
    'configuration_changed': False,
    'signature': 'unsigned vendor-style module; no signature enforcement changes',
    'on_device_load_test': 'pending; not installed or loaded',
}
build_id = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
identity['build_id'] = build_id
(build / 'identity.json').write_text(json.dumps(identity, indent=2) + '\n')
(build / 'kmod-src/build-identity.h').write_text(
    '#define HDMI_COMPANION_BUILD_ID "' + build_id + '"\n')
(build / 'build-id').write_text(build_id + '\n')
PY

cp "$SOURCE/kernel/hdmi_companion/"{Kbuild,probe.c,uapi.h} "$BUILD/kmod-src/"
export PATH="$CLANG_DIR/bin:/usr/bin:/bin"
export KBUILD_BUILD_USER=root KBUILD_BUILD_HOST=c8e1ea4e9ef0 KBUILD_BUILD_VERSION=1
export KBUILD_BUILD_TIMESTAMP='Sun Jun 8 00:30:34 UTC 2025'
make -C "$KERNEL_SOURCE" O="$KERNEL_BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 \
    PAHOLE="$PAHOLE" M="$BUILD/kmod-src" W=1 modules

"$NDK_DIR/bin/aarch64-linux-android35-clang" -std=gnu17 -O2 -fPIE -pie \
    -Wall -Wextra -Werror "$SOURCE/native/companion-probe/main.c" \
    -o "$BUILD/bin/hdmi-companion-probe"
gcc -std=gnu17 -O2 -fPIE -pie -Wall -Wextra -Werror \
    "$SOURCE/native/companion-probe/main.c" -o "$BUILD/tests/hdmi-companion-probe-host"
"$BUILD/tests/hdmi-companion-probe-host" --help
if "$BUILD/tests/hdmi-companion-probe-host" --device /dev/null > "$BUILD/tests/non-device.log" 2>&1; then
    printf 'A non-probe device was unexpectedly accepted\n' >&2; exit 1
else
    [[ $? == 1 ]]
fi
readelf -h "$BUILD/bin/hdmi-companion-probe" | grep -q 'AArch64'
readelf -l "$BUILD/bin/hdmi-companion-probe" | grep -q '/system/bin/linker64'

BUILD_ID=$(cat "$BUILD/build-id")
RELEASE=$(cat "$INPUT/kernel-release")
python3 "$SOURCE/build-support/verify-companion-probe.py" \
    "$BUILD/kmod-src/hdmi_companion_probe.ko" "$INPUT/installed-msm_drm.ko" \
    "$KERNEL_BUILD/Module.symvers" "$SOURCE/kernel/hdmi_companion/uapi.h" \
    "$RELEASE" "$BUILD_ID" --output "$BUILD/output/abi-report.json"

MODULE=$BUILD/package
mkdir -p "$MODULE/bin" "$MODULE/kmod"
cp -a "$SOURCE/module-companion-probe/." "$MODULE/"
cp "$BUILD/kmod-src/hdmi_companion_probe.ko" "$MODULE/kmod/"
cp "$BUILD/bin/hdmi-companion-probe" "$MODULE/bin/"
cp "$BUILD/identity.json" "$MODULE/manifest.json"
cp "$BUILD/output/abi-report.json" "$MODULE/abi-report.json"
cp "$SOURCE/docs/COMPANION_PROBE.md" "$MODULE/README.md"
python3 - "$BUILD/identity.json" "$MODULE/probe.env" <<'PY'
import json, pathlib, sys
identity = json.loads(pathlib.Path(sys.argv[1]).read_text())
values = {'EXPECTED_RELEASE': identity['kernel_release'],
          'EXPECTED_CONFIG_SHA256': identity['runtime_config_sha256'],
          'EXPECTED_BUILD_ID': identity['build_id']}
assert all("'" not in value and '\n' not in value for value in values.values())
pathlib.Path(sys.argv[2]).write_text(''.join(f"{key}='{value}'\n" for key, value in values.items()))
PY
chmod 0755 "$MODULE/bin/hdmi-companion-probe" "$MODULE/"*.sh
chmod 0644 "$MODULE/kmod/hdmi_companion_probe.ko"
(cd "$MODULE" && find . -type f ! -name SHA256SUMS -print0 | sort -z | \
    xargs -0 sha256sum > SHA256SUMS)
ZIP=$BUILD/output/hdmi-companion-probe-$SOURCE_COMMIT-magisk.zip
test ! -e "$ZIP" || { printf 'Artifact already exists; choose a new output directory.\n' >&2; exit 1; }
(cd "$MODULE" && zip -X -9 -q -r "$ZIP" .)
cp "$BUILD/identity.json" "$BUILD/output/manifest.json"
(cd "$BUILD/output" && sha256sum abi-report.json manifest.json ./*.zip > SHA256SUMS)
printf 'Probe artifact: %s\nNo module was installed or loaded.\n' "$ZIP"
