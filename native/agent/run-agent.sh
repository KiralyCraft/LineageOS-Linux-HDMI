#!/usr/bin/env bash
set -Eeuo pipefail

BUNDLE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
CAPTURE=none
RUNTIME=/run/hdmi-los
CAPTURE_PID=0
AGENT_PID=0
XORG_ACCEL=safe
SESSION=lxde

usage() {
    printf 'usage: %s [--capture auto|none|/dev/videoN] [--xorg-accel safe|kgsl-glamor] [--session lxde|none]\n' "$0" >&2
}

while (($#)); do
    case $1 in
        --capture)
            (($# >= 2)) || { usage; exit 2; }
            CAPTURE=$2
            shift 2
            ;;
        --xorg-accel)
            (($# >= 2)) || { usage; exit 2; }
            XORG_ACCEL=$2
            shift 2
            ;;
        --session)
            (($# >= 2)) || { usage; exit 2; }
            SESSION=$2
            shift 2
            ;;
        *)
            usage
            exit 2
            ;;
    esac
done

[[ $XORG_ACCEL == safe || $XORG_ACCEL == kgsl-glamor ]] || { usage; exit 2; }
[[ $SESSION == lxde || $SESSION == none ]] || { usage; exit 2; }

if ((EUID != 0)); then
    exec sudo -n -- "$0" --capture "$CAPTURE" --xorg-accel "$XORG_ACCEL" --session "$SESSION"
fi

for required in \
    "$BUNDLE/bin/hdmi-los-agent" \
    "$BUNDLE/bin/hdmi-input-bridge" \
    /usr/lib/Xorg /usr/bin/xauth /usr/bin/xdpyinfo /usr/bin/xrandr \
    /usr/bin/xinput /usr/bin/dbus-run-session /usr/bin/startlxde; do
    [[ -x $required ]] || {
        printf 'Required executable is missing: %s\n' "$required" >&2
        exit 1
    }
done

mkdir -p -- "$RUNTIME"
chmod 700 "$RUNTIME"

stop_child() {
    local pid=${1:-0}
    ((pid > 1)) || return 0
    kill -TERM "$pid" 2>/dev/null || true
    for _ in {1..20}; do
        kill -0 "$pid" 2>/dev/null || return 0
        sleep 0.1
    done
    kill -KILL "$pid" 2>/dev/null || true
}

cleanup() {
    trap - EXIT INT TERM HUP
    stop_child "$AGENT_PID"
    stop_child "$CAPTURE_PID"
}
trap cleanup EXIT INT TERM HUP

usb_id_for_video() {
    local node=$1 current
    current=$(readlink -f "/sys/class/video4linux/${node##*/}/device") || return 1
    while [[ $current != / && -n $current ]]; do
        if [[ -r $current/idVendor && -r $current/idProduct ]]; then
            printf '%s:%s\n' "$(tr '[:upper:]' '[:lower:]' <"$current/idVendor")" \
                "$(tr '[:upper:]' '[:lower:]' <"$current/idProduct")"
            return 0
        fi
        current=${current%/*}
        [[ -n $current ]] || current=/
    done
    return 1
}

find_capture() {
    local class_path node index
    for class_path in /sys/class/video4linux/video*; do
        [[ -e $class_path ]] || continue
        node="/dev/${class_path##*/}"
        [[ -c $node ]] || continue
        [[ $(usb_id_for_video "$node" 2>/dev/null || true) == 534d:2109 ]] || continue
        index=$(<"$class_path/index")
        [[ $index == 0 ]] || continue
        printf '%s\n' "$node"
        return 0
    done
    return 1
}

if [[ $CAPTURE == auto ]]; then
    CAPTURE=$(find_capture 2>/dev/null || true)
    [[ -n $CAPTURE ]] || CAPTURE=none
elif [[ $CAPTURE != none && ! $CAPTURE =~ ^/dev/video[0-9]+$ ]]; then
    usage
    exit 2
fi

if [[ $CAPTURE != none ]]; then
    printf 'WARNING: phone-side capture is diagnostic-only; prefer workstation-powered capture\n' >&2
    printf 'Starting optional HPD/capture keeper on %s\n' "$CAPTURE" >&2
    "$BUNDLE/bin/hdmi-capture-keeper" --device "$CAPTURE" \
        --latest "$RUNTIME/latest.jpg" >>"$RUNTIME/capture.log" 2>&1 &
    CAPTURE_PID=$!
    sleep 1
    if ! kill -0 "$CAPTURE_PID" 2>/dev/null; then
        printf 'Capture keeper failed; continuing without it (see %s/capture.log)\n' "$RUNTIME" >&2
        CAPTURE_PID=0
    fi
fi

if [[ $XORG_ACCEL == kgsl-glamor ]]; then
    printf 'WARNING: KGSL glamor is an isolated diagnostic; safe ShadowFB remains the default\n' >&2
fi

"$BUNDLE/bin/hdmi-los-agent" --bundle "$BUNDLE" \
    --xorg-accel "$XORG_ACCEL" --session "$SESSION" >>"$RUNTIME/agent.log" 2>&1 &
AGENT_PID=$!
set +e
wait "$AGENT_PID"
status=$?
set -e
AGENT_PID=0
exit "$status"
