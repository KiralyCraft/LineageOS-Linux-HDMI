#!/usr/bin/env bash
set -Eeuo pipefail

BUNDLE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
CAPTURE=none
RUNTIME=/run/hdmi-los
CAPTURE_PID=0
AGENT_PID=0
XORG_ACCEL=kgsl-kms-bridge
CLIENT_PRESENT=bridge
SESSION=lxde
NO_TIMEOUT=1

usage() {
    printf 'usage: %s [--capture auto|none|/dev/videoN] [--xorg-accel safe|kgsl-glamor|kgsl-kms-bridge] [--client-present bridge|shadow|direct] [--session lxde|none] [--no-timeout|--timeout]\n' "$0" >&2
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
        --client-present)
            (($# >= 2)) || { usage; exit 2; }
            CLIENT_PRESENT=$2
            shift 2
            ;;
        --no-timeout)
            NO_TIMEOUT=1
            shift
            ;;
        --timeout)
            NO_TIMEOUT=0
            shift
            ;;
        *)
            usage
            exit 2
            ;;
    esac
done

[[ $XORG_ACCEL == safe || $XORG_ACCEL == kgsl-glamor || \
   $XORG_ACCEL == kgsl-kms-bridge ]] || { usage; exit 2; }
[[ $SESSION == lxde || $SESSION == none ]] || { usage; exit 2; }
[[ $CLIENT_PRESENT == bridge || $CLIENT_PRESENT == shadow || \
   $CLIENT_PRESENT == direct ]] || { usage; exit 2; }
if [[ $XORG_ACCEL != kgsl-kms-bridge && $CLIENT_PRESENT != bridge ]]; then
    printf '%s requires --xorg-accel kgsl-kms-bridge\n' \
        "--client-present $CLIENT_PRESENT" >&2
    exit 2
fi

if ((EUID != 0)); then
    args=(--capture "$CAPTURE" --xorg-accel "$XORG_ACCEL" \
          --client-present "$CLIENT_PRESENT" --session "$SESSION")
    if ((NO_TIMEOUT)); then
        args+=(--no-timeout)
    else
        args+=(--timeout)
    fi
    exec sudo -n -- "$0" "${args[@]}"
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
    printf 'WARNING: KGSL glamor is an isolated diagnostic; prefer kgsl-kms-bridge or safe ShadowFB\n' >&2
elif [[ $XORG_ACCEL == kgsl-kms-bridge ]]; then
    mesa_bridge_abi='HDMI_LOS_MESA_BRIDGE_ABI=5'
    for xorg_file in \
        "$BUNDLE/libexec/Xorg" \
        "$BUNDLE/lib/xorg/modules/libglamoregl.so" \
        "$BUNDLE/lib/xorg/modules/drivers/modesetting_drv.so"; do
        [[ -x $xorg_file || $xorg_file == *.so && -r $xorg_file ]] || {
            printf 'Required private Xorg component is missing: %s\n' "$xorg_file" >&2
            exit 1
        }
    done
    shopt -s nullglob
    gallium_libraries=("$BUNDLE"/lib/mesa/libgallium-*.so)
    shopt -u nullglob
    ((${#gallium_libraries[@]} == 1)) || {
        printf 'Expected exactly one private libgallium below: %s\n' \
            "$BUNDLE/lib/mesa" >&2
        exit 1
    }
    mesa_libraries=(
        "${gallium_libraries[0]}"
        "$BUNDLE/lib/mesa/libGLX_mesa.so.0"
        "$BUNDLE/lib/mesa/libEGL_mesa.so.0"
    )
    for mesa_library in "${mesa_libraries[@]}"; do
        [[ -f $mesa_library ]] || {
            printf 'Required private Mesa library is missing: %s\n' \
                "$mesa_library" >&2
            exit 1
        }
        LC_ALL=C grep -aFq -- "$mesa_bridge_abi" "$mesa_library" || {
            printf 'Private Mesa library is stale or incompatible: %s\n' \
                "$mesa_library" >&2
            printf 'Expected embedded bridge contract: %s\n' "$mesa_bridge_abi" >&2
            exit 1
        }
        if ! runuser -u kiraly -- test -r "$mesa_library"; then
            printf 'Private Mesa library is not accessible to user kiraly: %s\n' \
                "$mesa_library" >&2
            printf 'Ensure the extracted bundle and its parent directories are searchable by kiraly.\n' >&2
            exit 1
        fi
    done
    dri_loader="$BUNDLE/lib/mesa/libdril_dri.so"
    dri_driver="$BUNDLE/lib/mesa/kgsl_dri.so"
    for dri_component in "$dri_loader" "$dri_driver"; do
        [[ -f $dri_component ]] || {
            printf 'Required private Mesa DRI component is missing: %s\n' \
                "$dri_component" >&2
            exit 1
        }
        if ! runuser -u kiraly -- test -r "$dri_component"; then
            printf 'Private Mesa DRI component is not accessible to user kiraly: %s\n' \
                "$dri_component" >&2
            exit 1
        fi
    done
    [[ $dri_driver -ef $dri_loader ]] || {
        printf 'Private KGSL DRI loader does not resolve to the matched libdril_dri.so\n' >&2
        exit 1
    }
    LC_ALL=C grep -aFq -- '__driDriverGetExtensions_kgsl' "$dri_loader" || {
        printf 'Private Mesa DRI loader does not export the KGSL driver entry point: %s\n' \
            "$dri_loader" >&2
        exit 1
    }
    gbm_library="$BUNDLE/lib/mesa/libgbm.so.1"
    gbm_backend="$BUNDLE/lib/mesa/gbm/dri_gbm.so"
    for gbm_component in "$gbm_library" "$gbm_backend"; do
        [[ -f $gbm_component ]] || {
            printf 'Required private Mesa GBM component is missing: %s\n' \
                "$gbm_component" >&2
            exit 1
        }
        if ! runuser -u kiraly -- test -r "$gbm_component"; then
            printf 'Private Mesa GBM component is not accessible to user kiraly: %s\n' \
                "$gbm_component" >&2
            exit 1
        fi
    done
    LC_ALL=C grep -aFq -- 'gbm_create_device' "$gbm_library" || {
        printf 'Private Mesa GBM library has no GBM entry point: %s\n' "$gbm_library" >&2
        exit 1
    }
    LC_ALL=C grep -aFq -- 'gbmint_get_backend' "$gbm_backend" || {
        printf 'Private Mesa GBM backend has no backend entry point: %s\n' "$gbm_backend" >&2
        exit 1
    }
    LC_ALL=C grep -aFq -- "${gallium_libraries[0]##*/}" "$gbm_backend" || {
        printf 'Private Mesa GBM backend does not require the matched Gallium build: %s\n' \
            "$gbm_backend" >&2
        exit 1
    }
    printf 'Using matched private Xorg renderonly scanout with client presentation mode: %s\n' \
        "$CLIENT_PRESENT" >&2
fi

if ((NO_TIMEOUT)); then
    printf 'WARNING: automatic 60-second restore is disabled; keep the volume-key escape accessible\n' >&2
    printf 'The composer watchdog will be renewed while the broker and agent remain healthy\n' >&2
fi

agent_args=(--bundle "$BUNDLE" --xorg-accel "$XORG_ACCEL" \
            --client-present "$CLIENT_PRESENT" --session "$SESSION")
((NO_TIMEOUT)) && agent_args+=(--no-timeout)
"$BUNDLE/bin/hdmi-los-agent" "${agent_args[@]}" >>"$RUNTIME/agent.log" 2>&1 &
AGENT_PID=$!
set +e
wait "$AGENT_PID"
status=$?
set -e
AGENT_PID=0
exit "$status"
