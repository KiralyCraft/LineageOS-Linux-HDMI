# Manual installation and first test

This is an experimental graphics-stack replacement.  `make zip` and
`make verify` are offline checks only; they do not install, reboot, connect a
display, or start Xorg.

Do not use release 0.1 or the ZIP whose SHA-256 is
`e0a0dfec34a8cbd3395c0c4b8aa3583230e09835ab752cafdbdc8aad036c5ff9`.
That package shifted proprietary Qualcomm/Sony C++ vtables and failed before
the Lineage boot animation.  These instructions apply to release 0.2 or later.

## Before installation

1. Keep the HDMI capture card connected so you can observe the external output.
2. Ensure the phone is stable and the dock is not cycling between charging and
   OTG power.
3. Keep both physical volume buttons accessible.  There is no proximity-sensor
   escape path.
4. In this repository, run `make verify`.  Preserve `dist/SHA256SUMS` with the
   two artifacts.
5. Extract the returned chroot bundle somewhere stable in this Arch filesystem,
   for example:

   ```sh
   mkdir -p /home/kiraly/Downloads/hdmi-los-runtime
   tar -xzf dist/hdmi-los-current-install-chroot.tar.gz \
       -C /home/kiraly/Downloads/hdmi-los-runtime
   ```

## Install, but do not take over yet

If the failed 0.1 module is still present and disabled, remove it in Magisk and
complete one stock boot before installing the corrected package. This avoids
carrying its safe-mode `disable` marker or zero-filled interrupted-boot logs
into the corrected installation.

Copy `dist/hdmi-los-current-install-magisk.zip` to Android-visible storage and
select it manually in the Magisk app.  The installer must show that the exact
Lineage version was accepted.  Reboot manually.

After boot, inspect the gate before proceeding:

```sh
sudo -n nsenter -t 1 -m -- /system/bin/su -c \
  'cat /data/adb/hdmi-los/logs/gate.log; cat /data/adb/modules/hdmi-los/compatible.ok'
```

The last gate line must begin with `PASS`, and the marker must say
`current-install`.  A missing marker or any `FAIL CLOSED` line means stop; the
original vendor files were left in use.

The module installs the signed `HDMI Xorg` tile app after Android finishes
booting.  It grants no Android permissions.  Add its tile to Quick Settings.

## First bounded takeover

1. Start the chroot agent from a terminal you can still reach:

   ```sh
   cd /home/kiraly/Downloads/hdmi-los-runtime
   sudo -n ./run-agent.sh --capture auto
   ```

   `--capture auto` keeps the MacroSilicon capture stream active when its
   `534d:2109` video node is present; use `--capture none` otherwise.

2. Wake the Bluetooth mouse and keyboard.  The bridge recognizes the recorded
   input names `ASUS MD100 Mouse` and `BT Keyboard` and reconnects when either
   device wakes again.
3. Connect HDMI and approve Android mirroring.  The compositor needs a live
   external display object before it can lease that display.
4. Open the diagnostics activity.  It must say `Ready`, not `Unavailable`.
5. Tap the `HDMI Xorg` tile once.  Do not tap repeatedly while it says
   `Starting Xorg`.
6. Confirm from the capture feed that only `DP-1` changes to LXDE and that mouse
   and keyboard input reach Xorg.  The internal phone UI should remain Android.
7. End the first run early by holding Volume Up and Volume Down together for at
   least three seconds.  Confirm Android mirroring returns.  If untouched, the
   broker forces the same transition at 60 seconds and composer has a 65-second
   backstop.

Logs are in `/data/adb/hdmi-los/logs/` on Android and `/run/hdmi-los/` in the
chroot.  Do not retry after a freeze or unexpected reboot; preserve those logs
and the previous boot's pstore/ramoops first.
