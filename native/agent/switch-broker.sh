#!/usr/bin/env bash
# Explicit standalone test handoff. The Magisk installation is never modified.
set -Eeuo pipefail
BUNDLE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
MODE=${1:-}
[[ $MODE == candidate || $MODE == rollback ]] || {
    printf 'usage: %s candidate|rollback (HDMI must be unplugged)\n' "$0" >&2; exit 2;
}
if ((EUID != 0)); then exec sudo -n -- "$0" "$MODE"; fi
[[ $(cat /sys/class/drm/card0-DP-1/status) == disconnected ]] || {
    printf 'Unplug HDMI before switching the broker; the current session was left alone.\n' >&2
    exit 1
}
SOURCE_COMMIT=$(python3 - "$BUNDLE/build-info.json" <<'PY'
import json,sys
print(json.load(open(sys.argv[1]))['candidate_b']['native_source_commit'])
PY
)
[[ $SOURCE_COMMIT =~ ^[0-9a-f]{40}$ ]] || exit 1
PREVIOUS_COMMIT=$(python3 - "$BUNDLE/build-info.json" <<'PY'
import json,sys
print(json.load(open(sys.argv[1]))['candidate_b'].get('previous_native_source_commit', ''))
PY
)
PREVIOUS=
if [[ -n $PREVIOUS_COMMIT ]]; then
    [[ $PREVIOUS_COMMIT =~ ^[0-9a-f]{40}$ ]] || exit 1
    PREVIOUS=/data/local/tmp/hdmi-async-${PREVIOUS_COMMIT:0:12}/hdmi-losd
fi
STAGE=/data/local/tmp/hdmi-async-${SOURCE_COMMIT:0:12}
OLD=/data/adb/modules/hdmi-los/bin/hdmi-losd
NEW=$STAGE/hdmi-losd
if [[ $MODE == candidate ]]; then
    "$BUNDLE/load-companion.sh" --check
    (cd "$BUNDLE/android" && sha256sum --strict -c SHA256SUMS >/dev/null)
    install -d -m 700 "/proc/1/root$STAGE"
    TARGET=$NEW
else
    TARGET=$OLD
fi
# Identify the executable, not just a process name, and refuse an unexpected
# owner. There is no respawning broker in the installed service.sh.
PIDS=()
while read -r pid; do
    [[ -n $pid ]] || continue
    exe=$(readlink "/proc/$pid/exe")
    [[ $exe == "$OLD" || $exe == "$NEW" ||
       ( -n $PREVIOUS && $exe == "$PREVIOUS" ) ]] || {
        printf 'Unexpected HDMI broker executable: %s\n' "$exe" >&2; exit 1;
    }
    PIDS+=("$pid")
done < <(pgrep -x hdmi-losd || true)
((${#PIDS[@]} <= 1)) || { printf 'Multiple HDMI brokers found\n' >&2; exit 1; }
for pid in "${PIDS[@]}"; do
    kill -TERM "$pid"
    for _ in {1..50}; do
        [[ -e /proc/$pid ]] || break
        sleep 0.1
    done
    [[ ! -e /proc/$pid ]] || { printf 'Broker did not stop; handoff aborted\n' >&2; exit 1; }
done
if [[ $MODE == candidate ]]; then
    # Install only after the prior process exited. Replacing a running stage
    # before identifying/stopping it could leave a deleted executable pathname.
    install -m 700 "$BUNDLE/android/hdmi-losd" "/proc/1/root$NEW"
fi
mkdir -p /run/hdmi-los
chmod 700 /run/hdmi-los
nsenter -t 1 -m -- /system/bin/toybox nohup "$TARGET" daemon \
    >>/run/hdmi-los/standalone-broker.log 2>&1 &
pid=$!
printf '%s\n' "$pid" >/run/hdmi-los/standalone-broker.pid
for _ in {1..50}; do
    kill -0 "$pid" 2>/dev/null || break
    if nsenter -t 1 -m -- "$TARGET" status; then
        printf 'Standalone broker handoff complete (%s).\n' "$MODE"
        exit 0
    fi
    sleep 0.1
done
printf 'Broker startup failed; inspect /run/hdmi-los/standalone-broker.log, then use rollback.\n' >&2
exit 1
