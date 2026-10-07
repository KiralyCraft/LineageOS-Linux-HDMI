#!/usr/bin/env bash
set -Eeuo pipefail
SOURCE=${1:?source checkout}
OUT=${2:?new output directory}
NDK=/bigdata/android-sdk/ndk/29.0.14206865/toolchains/llvm/prebuilt/linux-x86_64/bin
[[ ! -e $OUT ]] || { printf 'Output already exists\n' >&2; exit 1; }
mkdir -p "$OUT/android" "$OUT/host"
mkdir "$OUT/android-fixture"
flags=(-std=gnu17 -O2 -Wall -Wextra -Werror -I"$SOURCE/native/power" -I"$SOURCE/native/common")
"$NDK/aarch64-linux-android35-clang" "${flags[@]}" -fPIC -shared -fvisibility=hidden \
  "$SOURCE/native/power/guard.c" -pthread -ldl -llog -o "$OUT/android/libhdmi_los_power_guard.so"
for component in monitor launcher; do
  "$NDK/aarch64-linux-android35-clang" "${flags[@]}" -fPIE -pie \
    "$SOURCE/native/power/$component.c" -o "$OUT/android/hdmi-power-$component"
done
"$NDK/aarch64-linux-android35-clang" "${flags[@]}" -fPIC -shared \
  "$SOURCE/native/power/fake-client.c" -pthread -o "$OUT/android-fixture/libqti-perfd-client.so"
"$NDK/aarch64-linux-android35-clang" "${flags[@]}" -fPIE -pie \
  "$SOURCE/native/power/lookup-probe.c" -ldl -o "$OUT/android-fixture/lookup-probe"
host=(-std=gnu17 -O1 -g -Wall -Wextra -Werror -fsanitize=address,undefined -DHDMI_POWER_TEST \
      -I"$SOURCE/native/power" -I"$SOURCE/native/common")
cc "${host[@]}" -fPIC -shared "$SOURCE/native/power/guard.c" -pthread -ldl -o "$OUT/host/guard-test.so"
cc "${host[@]}" -fPIC -shared "$SOURCE/native/power/fake-client.c" -pthread -o "$OUT/host/libqti-perfd-client.so"
cc "${host[@]}" "$SOURCE/native/power/host-client.c" -ldl -o "$OUT/host/host-client"
cc "${host[@]}" "$SOURCE/native/power/monitor.c" -o "$OUT/host/monitor-test"
python3 "$SOURCE/tests/power-guard-test.py" "$OUT/host"
readelf -h "$OUT/android/hdmi-power-monitor" | grep -q AArch64
readelf -Ws "$OUT/android/libhdmi_los_power_guard.so" | grep -q ' dlsym$'
sha256sum "$OUT/android/"*
python3 - "$SOURCE" "$OUT" "$NDK" <<'PY'
import hashlib, json, pathlib, subprocess, sys
source, out = map(pathlib.Path, sys.argv[1:3])
def checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
paths = sorted((source / 'native/power').glob('*.c')) + [
    source / 'native/power/lease.h', source / 'native/common/hdmi_los_protocol.h',
    source / 'tests/power-guard-test.py', source / 'build-support/build-power-guard.sh']
manifest = dict(schema=1, target='aarch64-linux-android35',
    toolchain=subprocess.check_output([sys.argv[3] + '/aarch64-linux-android35-clang', '--version'], text=True),
    sources={str(p.relative_to(source)): checksum(p) for p in paths},
    artifacts={str(p.relative_to(out)): checksum(p) for directory in ('android', 'android-fixture')
               for p in sorted((out / directory).iterdir())},
    validation=dict(host_lifecycle='PASS with ASan and UBSan', native_runtime='not installed'))
(out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
PY
