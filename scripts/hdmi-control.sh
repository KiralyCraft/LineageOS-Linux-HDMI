#!/usr/bin/env bash
set -Eeuo pipefail
if (($# == 0)); then set -- status; fi
(($# == 1)) || { printf 'usage: %s [status|pause|resume|restart|disarm]\n' "$0" >&2; exit 2; }
case $1 in status|pause|resume|restart|disarm) ;; *) exit 2 ;; esac
if ((EUID != 0)); then exec sudo -- "$0" "$@"; fi
exec nsenter --target 1 --mount --root --wd=/ -- /system/bin/sh -c \
    'exec /data/adb/modules/hdmi-los/bin/hdmi-losd "$@"' hdmi-control "$@"
