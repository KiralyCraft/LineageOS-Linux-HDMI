#!/system/bin/sh

ui_print "- HDMI Xorg takeover: checking the installed Lineage build"

. "$MODPATH/profile.env" || abort "! missing build profile"

[ "$ARCH" = arm64 ] || abort "! arm64 is required (found: $ARCH)"
[ "$API" = "$EXPECTED_SDK" ] || abort "! Android API $EXPECTED_SDK is required (found: $API)"

check_prop() {
  actual="$(getprop "$1")"
  [ "$actual" = "$2" ] || abort "! $1 mismatch: expected '$2', found '$actual'"
}

check_prop ro.product.device "$EXPECTED_DEVICE"
check_prop ro.lineage.version "$EXPECTED_LINEAGE"
check_prop ro.build.id "$EXPECTED_BUILD_ID"
check_prop ro.build.version.sdk "$EXPECTED_SDK"
check_prop ro.build.version.security_patch "$EXPECTED_SECURITY_PATCH"

if [ -f "$MODPATH/timing.env" ]; then
  . "$MODPATH/timing.env" || abort "! missing timing companion identity"
  [ "$(uname -r)" = "$EXPECTED_RELEASE" ] || abort "! companion kernel release mismatch"
  config_hash="$(zcat /proc/config.gz | sha256sum | cut -d ' ' -f 1)"
  [ "$config_hash" = "$EXPECTED_CONFIG_SHA256" ] || abort "! companion kernel configuration mismatch"
  (cd "$MODPATH/companion" && sha256sum -c SHA256SUMS) || abort "! companion payload mismatch"
fi

set_perm_recursive "$MODPATH" 0 0 0755 0644
set_perm "$MODPATH/bin/hdmi-losd" 0 0 0755
set_perm "$MODPATH/customize.sh" 0 0 0755
set_perm "$MODPATH/post-fs-data.sh" 0 0 0755
set_perm "$MODPATH/service.sh" 0 0 0755
set_perm "$MODPATH/uninstall.sh" 0 0 0755
if [ -f "$MODPATH/timing.env" ]; then
  set_perm "$MODPATH/companion-loader.sh" 0 0 0755
  set_perm "$MODPATH/companion/hdmi-companion-probe" 0 0 0755
  ui_print "- Matching timing companion accepted; normal insmod runs on the next boot"
  ui_print "- No display session is started during installation"
fi

ui_print "- Exact Lineage version accepted"
ui_print "- Original vendor hashes will be checked again on every boot"
ui_print "- With HDMI unplugged, tap the tile to arm; connect and accept Mirror to start Xorg"
