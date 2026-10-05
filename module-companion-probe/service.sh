#!/system/bin/sh
MODDIR=${0%/*}
LOG=$MODDIR/probe.log
RESULT=$MODDIR/probe.result
TOYBOX=/system/bin/toybox

fail_probe() {
    printf 'FAIL: %s\n' "$1" >> "$LOG"
    printf 'FAIL\n' > "$RESULT"
    exit 1
}

: > "$LOG"
printf 'PENDING\n' > "$RESULT"
. "$MODDIR/probe.env" || fail_probe 'missing probe identity'
[ "$("$TOYBOX" uname -r)" = "$EXPECTED_RELEASE" ] || fail_probe 'kernel release mismatch'
config_hash="$("$TOYBOX" zcat /proc/config.gz | "$TOYBOX" sha256sum | "$TOYBOX" cut -d ' ' -f 1)"
[ "$config_hash" = "$EXPECTED_CONFIG_SHA256" ] || fail_probe 'kernel configuration mismatch'

if [ -d /sys/module/hdmi_companion_probe ]; then
    printf 'Probe already loaded; checking its identity\n' >> "$LOG"
else
    "$TOYBOX" insmod "$MODDIR/kmod/hdmi_companion_probe.ko" >> "$LOG" 2>&1 || \
        fail_probe 'module load failed; inspect probe.log and kernel log'
fi
# Android's ueventd may create the misc-device node after insmod returns.
remaining=20
while [ ! -c /dev/hdmi_companion_probe ] && [ "$remaining" -gt 0 ]; do
    "$TOYBOX" sleep 0.25
    remaining=$((remaining - 1))
done
[ -c /dev/hdmi_companion_probe ] || fail_probe 'misc-device node did not appear within five seconds'
"$MODDIR/bin/hdmi-companion-probe" --expect-release "$EXPECTED_RELEASE" \
    --expect-build "$EXPECTED_BUILD_ID" >> "$LOG" 2>&1 || \
    fail_probe 'query or negative ioctl test failed'
printf 'PASS\n' > "$RESULT"
