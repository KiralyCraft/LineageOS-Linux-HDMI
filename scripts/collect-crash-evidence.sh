#!/usr/bin/env bash
set -Eeuo pipefail

SERIAL=${1:-}
DESTINATION=${2:-}
if [[ -z $SERIAL || -z $DESTINATION ]]; then
    printf 'usage: %s ADB_SERIAL NEW_DESTINATION_DIRECTORY\n' "$0" >&2
    exit 2
fi
[[ ! -e $DESTINATION ]] || {
    printf 'refusing to merge evidence into existing path: %s\n' "$DESTINATION" >&2
    exit 1
}
mkdir -p -- "$DESTINATION"

ADB=(adb -s "$SERIAL")
"${ADB[@]}" get-state >/dev/null
"${ADB[@]}" devices -l >"$DESTINATION/adb-devices.txt"
"${ADB[@]}" shell getprop >"$DESTINATION/getprop.txt" 2>&1 || true
"${ADB[@]}" shell cat /proc/uptime >"$DESTINATION/uptime.txt" 2>&1 || true
"${ADB[@]}" shell cat /proc/cmdline >"$DESTINATION/cmdline.txt" 2>&1 || true
"${ADB[@]}" shell cat /proc/modules >"$DESTINATION/modules.txt" 2>&1 || true
"${ADB[@]}" shell cat /proc/1/mountinfo >"$DESTINATION/mountinfo.txt" 2>&1 || true

stream_root_tree() {
    local absolute=$1 label=$2 relative=${1#/} archive
    archive=$DESTINATION/$label.tar
    if "${ADB[@]}" shell su -c "test -e '$absolute'" >/dev/null 2>&1; then
        "${ADB[@]}" exec-out su -c "tar -C / -cf - '$relative'" >"$archive"
        tar -tf "$archive" >"$DESTINATION/$label.contents.txt"
    fi
}

if "${ADB[@]}" shell 'test -f /tmp/recovery.log' >/dev/null 2>&1; then
    printf 'recovery\n' >"$DESTINATION/device-mode.txt"
    "${ADB[@]}" pull /tmp/recovery.log "$DESTINATION/recovery.log" >/dev/null 2>&1 || true
    "${ADB[@]}" pull /sys/fs/pstore "$DESTINATION/pstore" >/dev/null 2>&1 || true
    "${ADB[@]}" shell dmesg >"$DESTINATION/dmesg.txt" 2>&1 || true
else
    printf 'normal\n' >"$DESTINATION/device-mode.txt"
    "${ADB[@]}" logcat -b all -d -v threadtime >"$DESTINATION/logcat-all.txt" 2>&1 || true
    "${ADB[@]}" exec-out su -c dmesg >"$DESTINATION/dmesg.txt" 2>&1 || true
    stream_root_tree /data/adb/hdmi-los/logs module-logs
    stream_root_tree /data/misc/recovery recovery-persisted
    stream_root_tree /data/vendor/display/hw_recovery display-hw-recovery
    stream_root_tree /sys/fs/pstore pstore
fi

(cd "$DESTINATION" && sha256sum -- * 2>/dev/null | sort > SHA256SUMS) || true
printf 'Evidence captured without modifying the device: %s\n' "$DESTINATION"
