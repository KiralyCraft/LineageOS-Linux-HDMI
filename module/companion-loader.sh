#!/system/bin/sh
# Called only by the late-start service for a package carrying timing.env.
# Load an additive timing companion, never replace or unload a GPU driver.
MODDIR=${0%/*}
TOYBOX=/system/bin/toybox

fail_companion() {
    printf 'FAIL: timing companion: %s\n' "$1" >&2
    exit 1
}

. "$MODDIR/timing.env" || fail_companion 'missing build identity'
[ "$("$TOYBOX" uname -r)" = "$EXPECTED_RELEASE" ] || fail_companion 'kernel release mismatch'
config_hash="$("$TOYBOX" zcat /proc/config.gz | "$TOYBOX" sha256sum | "$TOYBOX" cut -d ' ' -f 1)"
[ "$config_hash" = "$EXPECTED_CONFIG_SHA256" ] || fail_companion 'kernel configuration mismatch'
(cd "$MODDIR/companion" && "$TOYBOX" sha256sum -c SHA256SUMS) ||
    fail_companion 'payload checksum mismatch'

if [ -d /sys/module/hdmi_companion ]; then
    printf 'Timing companion already loaded; checking identity\n'
else
    "$TOYBOX" insmod "$MODDIR/companion/hdmi_companion.ko" ||
        fail_companion 'ordinary insmod failed'
fi

remaining=20
while [ ! -c /dev/hdmi_companion ] && [ "$remaining" -gt 0 ]; do
    "$TOYBOX" sleep 0.25
    remaining=$((remaining - 1))
done
[ -c /dev/hdmi_companion ] || fail_companion 'device node did not appear within five seconds'
"$MODDIR/companion/hdmi-companion-probe" --timing \
    --expect-release "$EXPECTED_RELEASE" --expect-build "$EXPECTED_BUILD_ID" ||
    fail_companion 'functional ABI or loaded build identity check failed'
printf 'PASS: matching timing companion loaded; no session or vblank reference acquired\n'
