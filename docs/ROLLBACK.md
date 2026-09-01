# Rollback and recovery

## While Xorg owns HDMI

Hold both volume buttons for three seconds.  The broker stops the X session and
input bridge before composer revokes the lease.  Agent/composer disconnects and
the 60/65-second timers provide independent release paths.

The diagnostic build also supports an unused three-second lease hold. It
always closes the lease and asks composer to restore Android before returning.

## Android is responsive

Disable or uninstall `HDMI Xorg takeover for LineageOS pdx234` in Magisk, then
reboot.  The three replacements are bind mounts and disappear at reboot; the
vendor partition was never changed.  The uninstall hook also removes the tile
app.

## A module causes boot trouble

Use Magisk safe mode by holding Volume Down during boot, then disable/remove the
`hdmi-los` module and reboot.  From an adb/recovery root shell, the equivalent
recovery is to create `/data/adb/modules/hdmi-los/disable` (or remove only that
module directory) and reboot.  Do not delete all modules or all of `/data/adb`.

No LineageOS partition needs reflashing for this module.  Reflashing the
device's matching original boot/init_boot image is a last-resort recovery for a
damaged Magisk installation itself, not a normal rollback for this patch.  The
module does not alter either image or `/vendor` on disk.

The diagnostic `vendor.display.disable_hw_recovery_dump=0` override is applied
at boot with Magisk `resetprop`; disabling the module and rebooting restores
the ROM's property behavior.
