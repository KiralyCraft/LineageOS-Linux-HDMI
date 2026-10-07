#!/system/bin/sh
MODDIR=${0%/*}
STATE=/data/adb/hdmi-los-power
[ -r "$MODDIR/compatible.ok" ] || exit 0
mkdir -p "$STATE"
chmod 0700 "$STATE"
setprop vendor.hdmi_los.cpu_lease 0
# Rotate on boot only; no synchronous per-frame logging.
[ ! -f "$STATE/monitor.log" ] || mv -f "$STATE/monitor.log" "$STATE/monitor.previous.log"
exec "$MODDIR/bin/hdmi-power-monitor" >>"$STATE/monitor.log" 2>&1
