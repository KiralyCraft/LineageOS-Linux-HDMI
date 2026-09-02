# Rollback and recovery

## While Xorg owns HDMI

Hold both volume buttons for three seconds.  The broker stops the X session and
input bridge before composer revokes the lease. Agent/composer disconnects
remain independent release paths. The default continuous launcher omits the
60-second deadline but must renew the composer watchdog every 20 seconds;
failed renewal still restores Android. Passing `--timeout` restores the fixed
60/65-second deadlines.

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

The candidate package does not override
`vendor.display.disable_hw_recovery_dump`. A package carrying the optional
`diagnostic-only` marker applies that override with Magisk `resetprop`; disabling
such a module and rebooting restores the ROM's property behavior.
