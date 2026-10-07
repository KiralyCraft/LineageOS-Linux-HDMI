#!/system/bin/sh
. "$MODPATH/profile.env" || abort '! Missing build profile'
[ "$ARCH" = arm64 ] || abort '! This module requires arm64'
[ "$API" = "$EXPECTED_SDK" ] || abort '! Android API mismatch'
while IFS='|' read -r property expected; do
  [ "$(getprop "$property")" = "$expected" ] || abort "! Build mismatch: $property"
done < "$MODPATH/property-checks.list"
(cd "$MODPATH" && sha256sum -c SHA256SUMS) || abort '! Payload checksum mismatch'
while IFS='|' read -r expected target; do
  source=$target
  # Updating an already-active CPU module must verify its unchanged stock
  # service, not mistake the installed launcher for the original executable.
  if [ "$target" = /vendor/bin/hw/android.hardware.power-service-qti ] &&
      [ -f /vendor/bin/hw/android.hardware.power-service-qti.hdmi-stock ]; then
    source=/vendor/bin/hw/android.hardware.power-service-qti.hdmi-stock
  fi
  [ "$(sha256sum "$source" | cut -d ' ' -f 1)" = "$expected" ] ||
    abort "! Original vendor file mismatch: $target"
done < "$MODPATH/original-checksums.list"
set_perm_recursive "$MODPATH" 0 0 0755 0644
for script in customize.sh post-fs-data.sh service.sh uninstall.sh; do
  set_perm "$MODPATH/$script" 0 0 0755
done
set_perm "$MODPATH/bin/hdmi-power-monitor" 0 0 0755
set_perm "$MODPATH/bin/hdmi-power-launcher" 0 2000 0755 u:object_r:hal_power_default_exec:s0
set_perm "$MODPATH/system/vendor/bin/hw/android.hardware.power-service-qti.hdmi-stock" 0 2000 0755 u:object_r:hal_power_default_exec:s0
set_perm "$MODPATH/system/vendor/lib64/libhdmi_los_power_guard.so" 0 0 0644 u:object_r:same_process_hal_file:s0
ui_print '- Exact stock PowerHAL and vendor CPU profile verified'
ui_print '- Separate additive module; existing HDMI module remains installed'
ui_print '- Nothing is started during installation. Activation requires reboot.'
