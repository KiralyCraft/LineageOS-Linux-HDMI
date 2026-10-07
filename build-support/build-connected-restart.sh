#!/usr/bin/env bash
set -Eeuo pipefail
SOURCE=${1:?source}
BUILD=${2:?output}
NDK=/bigdata/android-sdk/ndk/29.0.14206865/toolchains/llvm/prebuilt/linux-x86_64/bin
mkdir -p "$BUILD"
"$NDK/aarch64-linux-android35-clang++" -std=c++20 -O2 -fPIE -pie -pthread \
  -static-libstdc++ -Wall -Wextra -Werror -I"$SOURCE/native/common" \
  "$SOURCE/native/broker/main.cpp" -o "$BUILD/hdmi-losd"
for fixture in restart stop lifecycle; do
  g++ -std=c++20 -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer \
    -pthread -Wall -Wextra -Werror -I"$SOURCE/native/common" \
    "$SOURCE/native/broker/$fixture-selftest.cpp" -o "$BUILD/$fixture-selftest"
  runuser -u nobody -- "$BUILD/$fixture-selftest" >"$BUILD/$fixture-test.log" 2>&1
done
g++ -std=c++20 -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer \
  -Wall -Wextra -Werror "$SOURCE/native/agent/child-selftest.cpp" -o "$BUILD/child-selftest"
runuser -u nobody -- "$BUILD/child-selftest" >"$BUILD/child-test.log" 2>&1
docker run --rm --platform linux/arm64 \
  -v "$SOURCE:/source:ro" -v "$BUILD:/output" lfdevs/archlinuxarm:base-devel bash -lc '
set -Eeuo pipefail
g++ -std=c++20 -O2 -fPIE -pie -pthread -Wall -Wextra -Werror \
  -I/source/native/common /source/native/agent/main.cpp -o /output/hdmi-los-agent
/output/hdmi-los-agent --help >/output/agent-help.txt 2>&1 || test $? = 2
'
readelf -h "$BUILD/hdmi-los-agent" | grep -q AArch64
readelf -h "$BUILD/hdmi-losd" | grep -q AArch64
python3 - "$SOURCE" "$BUILD" <<'PY'
import hashlib,json,pathlib,sys
source,build=map(pathlib.Path,sys.argv[1:])
paths=['native/broker/main.cpp','native/broker/restart-selftest.cpp',
       'native/broker/stop-selftest.cpp','native/broker/lifecycle-selftest.cpp',
       'native/agent/main.cpp','native/agent/child-selftest.cpp',
       'native/common/hdmi_agent_reader.h','native/common/hdmi_child_process.h',
       'native/common/hdmi_los_trace.h','native/common/hdmi_los_protocol.h',
       'native/common/hdmi_connected_restart.h','native/common/hdmi_timing_session.h',
       'kernel/hdmi_companion/uapi.h','build-support/build-connected-restart.sh']
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
manifest=dict(target='aarch64-linux-android35',sources={p:sha(source/p) for p in paths},
              artifacts={p:sha(build/p) for p in ['hdmi-losd','hdmi-los-agent','restart-test.log','stop-test.log','lifecycle-test.log','child-test.log','agent-help.txt']},
              validation=dict(host_lifecycle='PASS with ASan UBSan and LSan',physical_restart='pending'))
(build/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
PY
