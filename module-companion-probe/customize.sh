#!/system/bin/sh
ui_print "- HDMI companion: query-only kernel compatibility probe"
. "$MODPATH/probe.env" || abort "! missing probe identity"
[ "$ARCH" = arm64 ] || abort "! arm64 is required"
[ "$(uname -r)" = "$EXPECTED_RELEASE" ] || abort "! kernel release mismatch"
config_hash="$(zcat /proc/config.gz | sha256sum | cut -d ' ' -f 1)"
[ "$config_hash" = "$EXPECTED_CONFIG_SHA256" ] || abort "! kernel configuration mismatch"
set_perm_recursive "$MODPATH" 0 0 0755 0644
set_perm "$MODPATH/customize.sh" 0 0 0755
set_perm "$MODPATH/service.sh" 0 0 0755
set_perm "$MODPATH/bin/hdmi-companion-probe" 0 0 0755
ui_print "- Matching kernel accepted; probe runs during the next boot"
ui_print "- Existing HDMI module, drivers, and launcher remain untouched"
ui_print "- Result: /data/adb/modules/hdmi_companion_probe/probe.result"
