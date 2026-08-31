#!/system/bin/sh

# Bind mounts disappear on reboot.  Never try to unmount a live composer file
# during removal; doing so would not change already mapped code and adds risk.
pm uninstall dev.kiraly.hdmilos >/dev/null 2>&1 || true
rm -f /data/adb/hdmi-los/tile.sha256

