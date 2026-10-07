#!/system/bin/sh
# Magic Mount adds two new vendor filenames only. The existing service is
# overlaid manually, only after the exact build and all payloads pass this gate.
MODDIR=${0%/*}
STATE=/data/adb/hdmi-los-power
MARKER=$MODDIR/compatible.ok
TARGET=/vendor/bin/hw/android.hardware.power-service-qti
MOUNTED=0
mkdir -p "$STATE"
chmod 0700 "$STATE"
exec >>"$STATE/gate.log" 2>&1
printf '\n[%s] CPU guard gate starting\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
rm -f "$MARKER"
fail_closed() {
  printf 'FAIL CLOSED: %s\n' "$1"
  [ "$MOUNTED" = 0 ] || umount "$TARGET"
  rm -f "$MARKER"
  exit 0
}
. "$MODDIR/mount-utils.sh" || fail_closed 'missing mount utilities'
while IFS='|' read -r property expected; do
  [ "$(getprop "$property")" = "$expected" ] || fail_closed "build mismatch: $property"
done < "$MODDIR/property-checks.list"
(cd "$MODDIR" && sha256sum -c SHA256SUMS) || fail_closed 'payload checksum mismatch'
while IFS='|' read -r expected target; do
  [ "$(sha256sum "$target" | cut -d ' ' -f 1)" = "$expected" ] ||
    fail_closed "original vendor file mismatch: $target"
done < "$MODDIR/original-checksums.list"
pidof android.hardware.power-service-qti >/dev/null 2>&1 &&
  fail_closed 'PowerHAL already running; no live service replacement'
MAGISK_TMP="$(magisk --path 2>/dev/null)"
case "$MAGISK_TMP" in /*) ;; *) fail_closed 'invalid Magisk path' ;; esac
MIRROR=$MAGISK_TMP/.magisk/modules
BIND_ROOT=$MIRROR/hdmi-los-power
options="$(mountinfo_options_for_target "$MIRROR")" || fail_closed 'missing module mirror'
mount_options_are_read_only "$options" && mount_options_allow_exec "$options" ||
  fail_closed 'module mirror must be read-only and executable'
for relative in bin/hdmi-power-launcher \
    system/vendor/bin/hw/android.hardware.power-service-qti.hdmi-stock \
    system/vendor/lib64/libhdmi_los_power_guard.so; do
  [ -f "$BIND_ROOT/$relative" ] || fail_closed "missing mirrored $relative"
  [ "$(stat -c '%d:%i' "$MODDIR/$relative")" = "$(stat -c '%d:%i' "$BIND_ROOT/$relative")" ] ||
    fail_closed "mirror inode mismatch: $relative"
done
chown 0:2000 "$MODDIR/bin/hdmi-power-launcher" \
    "$MODDIR/system/vendor/bin/hw/android.hardware.power-service-qti.hdmi-stock" || fail_closed 'chown'
chmod 0755 "$MODDIR/bin/hdmi-power-launcher" \
    "$MODDIR/system/vendor/bin/hw/android.hardware.power-service-qti.hdmi-stock" || fail_closed 'chmod'
chcon u:object_r:hal_power_default_exec:s0 "$MODDIR/bin/hdmi-power-launcher" \
    "$MODDIR/system/vendor/bin/hw/android.hardware.power-service-qti.hdmi-stock" || fail_closed 'service label'
chcon u:object_r:same_process_hal_file:s0 \
    "$MODDIR/system/vendor/lib64/libhdmi_los_power_guard.so" || fail_closed 'library label'
mount -o bind "$BIND_ROOT/bin/hdmi-power-launcher" "$TARGET" || fail_closed 'launcher bind'
MOUNTED=1
mount -o remount,bind,ro "$BIND_ROOT/bin/hdmi-power-launcher" "$TARGET" || fail_closed 'read-only remount'
options="$(mountinfo_options_for_target "$TARGET")" || fail_closed 'launcher mount flags'
mount_options_are_read_only "$options" && mount_options_allow_exec "$options" || fail_closed 'launcher mount access'
[ "$(sha256sum "$TARGET" | cut -d ' ' -f 1)" = "$(sha256sum "$MODDIR/bin/hdmi-power-launcher" | cut -d ' ' -f 1)" ] ||
  fail_closed 'mounted launcher checksum'
[ "$(stat -c '%a|%u|%g' "$TARGET")" = '755|0|2000' ] || fail_closed 'launcher metadata'
[ "$(ls -Zd "$TARGET" | awk '{print $1}')" = u:object_r:hal_power_default_exec:s0 ] || fail_closed 'launcher label'
printf 'compatible\n' > "$MARKER"
chmod 0600 "$MARKER"
printf 'PASS: gated launcher enabled; stock PowerHAL unchanged\n'
