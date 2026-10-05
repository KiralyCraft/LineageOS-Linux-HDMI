#!/usr/bin/env bash
# Standalone load/query only. Never touches an HDMI lease or Android service.
set -Eeuo pipefail
BUNDLE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
MODE=${1:-load}
[[ $MODE == load || $MODE == --check || $MODE == --check-compatible ]] || exit 2
if ((EUID != 0)); then exec sudo -n -- "$0" "$MODE"; fi
mapfile -t identity < <(python3 - "$BUNDLE/companion/manifest.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
print(d['kernel_release'])
print(d['build_id'])
print(d['runtime_config_sha256'])
PY
)
[[ ${#identity[@]} == 3 && ${identity[1]} =~ ^[0-9a-f]{64}$ &&
   ${identity[2]} =~ ^[0-9a-f]{64}$ ]] || exit 1
[[ $(uname -r) == "${identity[0]}" ]] || {
    printf 'The companion does not match the running kernel\n' >&2; exit 1;
}
current_config=$(gzip -dc /proc/config.gz | sha256sum)
[[ ${current_config%% *} == "${identity[2]}" ]] || {
    printf 'The companion does not match the running kernel configuration\n' >&2; exit 1;
}
(cd "$BUNDLE/companion" && sha256sum --strict -c SHA256SUMS >/dev/null)
STAGE=/data/local/tmp/hdmi-companion-${identity[1]:0:12}
ANDROID_ROOT=/proc/1/root
install -d -m 700 "$ANDROID_ROOT$STAGE"
install -m 600 "$BUNDLE/companion/hdmi_companion.ko" "$ANDROID_ROOT$STAGE/hdmi_companion.ko"
install -m 700 "$BUNDLE/companion/hdmi-companion-probe" "$ANDROID_ROOT$STAGE/hdmi-companion-probe"
if [[ ! -d /sys/module/hdmi_companion ]]; then
    [[ $MODE == load ]] || {
        printf 'Load the matching companion first with ./load-companion.sh\n' >&2; exit 1;
    }
    nsenter -t 1 -m -- /system/bin/toybox insmod "$STAGE/hdmi_companion.ko"
fi
if [[ $MODE == --check-compatible ]]; then
    # Both entries are checksum-pinned build identities in the runtime manifest.
    probe_result=$(nsenter -t 1 -m -- "$STAGE/hdmi-companion-probe" --timing --expect-release "${identity[0]}")
    printf '%s\n' "$probe_result"
    python3 - "$BUNDLE/compatible-companions.json" "$probe_result" <<'PYCODE'
import json,sys
allowed=json.load(open(sys.argv[1]))['build_ids']
assert any(build in sys.argv[2] for build in allowed), 'Loaded companion build is outside the pinned compatibility set'
PYCODE
else
    nsenter -t 1 -m -- "$STAGE/hdmi-companion-probe" --timing \
        --expect-release "${identity[0]}" --expect-build "${identity[1]}"
fi
printf 'Matching standalone timing companion is loaded. No lease was created.\n'
