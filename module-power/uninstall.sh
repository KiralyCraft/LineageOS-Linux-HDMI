#!/system/bin/sh
# The monitor also notices the module's remove/disable marker. No vendor
# service is restarted during removal; reboot drops the launcher mount.
setprop vendor.hdmi_los.cpu_lease 0
