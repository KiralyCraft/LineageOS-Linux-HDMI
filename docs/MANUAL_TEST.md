# Staged diagnostic installation and test

Release `0.2.5-diagnostic.1` is not a production takeover build. Its Quick
Settings tile cannot start Xorg. The only activation paths are explicit root
commands, and the old protocol-v1 chroot agent is rejected.

Do not use release 0.1, 0.2, 0.2.3, or any earlier takeover ZIP. Do not repeat
a probe after a freeze or reset until all available evidence has been copied.

## Topology and installation

1. Power the MacroSilicon capture card from the workstation. Connect its HDMI
   input to the phone dock, but do not connect its USB cable to the phone.
2. Keep the phone charged and both physical volume buttons accessible.
3. Build and verify the matching artifacts:

   ```sh
   make reuse-composer \
     BASE_COMMIT=8a9e97430b062ed695f11801a5b251636ba3971a
   make verify \
     BASE_COMMIT=8a9e97430b062ed695f11801a5b251636ba3971a
   ```

4. Install `dist/hdmi-los-current-install-magisk.zip` in Magisk and reboot.
5. Replace the complete prior chroot bundle with
   `dist/hdmi-los-current-install-chroot.tar.gz`. Do not mix a protocol-v1
   agent with this module.
6. Verify `/data/adb/hdmi-los/logs/gate.log`, `compatible.ok`, and the three
   read-only executable composer bind mounts. Also require:

   ```sh
   getprop vendor.display.disable_hw_recovery_dump
   ```

   to print `0`, with the same value recorded in `diagnostic.log` after boot.

## Gate 1: ordinary Android mirroring

Approve Android mirroring and use both the internal display and external
capture for ten minutes. Do not start the chroot agent yet. If the phone,
dock, or capture signal resets, stop: this is a power/PD/HPD problem rather
than an Xorg ioctl problem.

## Gate 2: unused lease only

Run from an Android root shell:

```sh
/data/adb/modules/hdmi-los/bin/hdmi-losd probe lease-hold
```

The broker prepares and pauses the external Android display, creates the DRM
lease, holds the unused fd for three seconds, closes it, and restores Android.
Run this exactly three times. Android mirroring and the internal display must
recover after every cycle. Do not proceed to Xorg if any cycle resets.

## Gate 3: one traced legacy Xorg start

Start the matching chroot bundle without phone-side capture:

```sh
cd /home/kiraly/Downloads/hdmi-los-runtime
sudo -n ./run-agent.sh --capture none
```

Then run one root command on Android:

```sh
/data/adb/modules/hdmi-los/bin/hdmi-losd probe xorg-legacy
```

Every DRM ioctl is synced to `/data/adb/hdmi-los/logs/broker.log` before it is
issued. If LXDE appears, verify the internal Android display, mouse, and
keyboard, then hold both volume buttons for three seconds to restore Android.

If the phone resets, do not retry. In Lineage Recovery, capture evidence with:

```sh
scripts/collect-crash-evidence.sh SERIAL NEW_DESTINATION_DIRECTORY
```

The recovery pass preserves `/tmp/recovery.log` and `/sys/fs/pstore`. Run the
collector again into a second new directory after normal boot to preserve
module logs, `/data/misc/recovery`, and Qualcomm display recovery dumps.

## Gate 4: atomic candidate, only when justified

Run `probe xorg-atomic` only if the last durable legacy record identifies a
legacy modeset, cursor, or page-flip boundary. The atomic probe changes only:

```text
Option "Atomic" "true"
Option "ShadowFB" "true"
```

It retains software rendering, disabled DRI page flips, and `SWcursor`. A
failure during resource queries, dumb-buffer creation, or framebuffer
registration must first be reduced to a one-operation probe because atomic
Xorg shares those setup calls.

## Success criteria

A production configuration is not selected after one successful start. It
requires ten minutes of stable Android mirroring, three unused lease cycles,
three complete Xorg takeovers, volume-chord restore, the 60-second timeout,
safe HDMI unplug restoration, responsive internal Android UI, working input,
and no pstore, display-recovery, or broker error evidence.
