#!/usr/bin/env bash
# Correctness-only controls; wait for the normal unplug/arm handshake first.
set -Eeuo pipefail
if (($# < 1)); then
    printf 'usage: %s finish-before|finish-after|verify [run-agent options]\n' "$0" >&2
    exit 2
fi
case $1 in
    finish-before|finish-after|verify) ;;
    *) printf 'Unknown E diagnostic: %s\n' "$1" >&2; exit 2 ;;
esac
if ((EUID != 0)); then
    # Set the diagnostic after sudo so environment filtering cannot turn a
    # diagnostic run into an ordinary E-on run without anyone noticing.
    exec sudo -n -- "$0" "$@"
fi
BUNDLE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
LC_ALL=C grep -aFq 'HDMI_LOS_E_DIAGNOSTIC_ABI=1' \
    "$BUNDLE/lib/xorg/modules/libglamoregl.so" || {
    printf 'The matched E diagnostic glamor module is missing\n' >&2
    exit 1
}
export HDMI_LOS_E_DIAGNOSTIC=$1
shift
printf 'E diagnostic: %s; synchronous correctness test, not a performance test\n' \
    "$HDMI_LOS_E_DIAGNOSTIC" >&2
exec "$BUNDLE/run-agent.sh" --candidate BCDEF --timeout --capture none "$@"
