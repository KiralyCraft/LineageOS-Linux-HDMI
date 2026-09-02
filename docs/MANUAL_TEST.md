# Atomic takeover candidate installation and test

Release `0.2.7-candidate.1` enables the Quick Settings tile and selects the
traced atomic+ShadowFB Xorg path. Bounded mode retains the 60/65-second
automatic restore timers. An explicit `--no-timeout` agent instead renews the
composer watchdog every 20 seconds so a healthy session can continue. The
root-only diagnostic probes remain available, and the old protocol-v1 chroot
agent is rejected.

Do not use release 0.1, 0.2, 0.2.3, or any earlier takeover ZIP. Do not repeat
a probe after a freeze or reset until all available evidence has been copied.

## Topology and installation

1. Power the MacroSilicon capture card from the workstation. Connect its HDMI
   input to the phone dock, but do not connect its USB cable to the phone.
2. Keep the phone charged and both physical volume buttons accessible.
3. Build and verify the matching artifacts:

   ```sh
   make server-preflight
   make zip
   make verify
   ```

4. Install `dist/hdmi-los-current-install-magisk.zip` in Magisk and reboot.
5. Replace the complete prior chroot bundle with
   `dist/hdmi-los-current-install-chroot.tar.gz`. Do not mix a protocol-v1
   agent with this module.
6. Verify `/data/adb/hdmi-los/logs/gate.log`, `compatible.ok`, and the three
   read-only executable composer bind mounts. The candidate omits the
   `diagnostic-only` marker and does not override the ROM's display-recovery
   property.

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

## Gate 3: traced atomic Xorg takeover

Start the matching chroot bundle without phone-side capture:

```sh
cd /home/kiraly/Downloads/hdmi-los-runtime
sudo -n ./run-agent.sh --capture none
```

Add the `HDMI Xorg` Quick Settings tile and tap it. The equivalent root-only
diagnostic command is:

```sh
/data/adb/modules/hdmi-los/bin/hdmi-losd probe xorg-atomic
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

## Gate 4: legacy comparison, only for diagnosis

The selected atomic mode changes these Xorg options from the legacy probe:

```text
Option "Atomic" "true"
Option "ShadowFB" "true"
```

It retains software rendering, disabled DRI page flips, and `SWcursor`. Use
`probe xorg-legacy` only to compare a regression; on this device its initial
legacy modeset returns `EINVAL` and produces a black capture.

## Gate 5: renewable continuous session

Do not test continuous mode until all bounded success criteria below pass.
Stop the foreground agent, restart it with the explicit option, then use the
tile normally:

```sh
cd /home/kiraly/Downloads/hdmi-los-runtime
sudo -n ./run-agent.sh --capture none --no-timeout
```

For the native KGSL bridge, add the already documented
`--xorg-accel kgsl-kms-bridge --session lxde` options to that command. Verify
that the broker status reports `continuous watchdog mode`, leave Xorg active
for at least 90 seconds, and confirm it remains responsive beyond the former
60/65-second limits. End the test with the volume chord, then repeat once and
end it by tapping the tile. Android mirroring must recover both times.

Continuous mode removes only the fixed broker deadline. It still requires both
physical volume inputs and the suspend blocker. The broker renews the composer
watchdog every 20 seconds; failed renewal, either control connection closing,
Xorg exit, HDMI unplug, or secure-display entry restores Android.

## Success criteria

A production configuration is not selected after one successful start. It
requires ten minutes of stable Android mirroring, three unused lease cycles,
three complete Xorg takeovers, volume-chord restore, the 60-second timeout,
safe HDMI unplug restoration, responsive internal Android UI, working input,
and no pstore, display-recovery, or broker error evidence.

The optional continuous gate additionally requires a responsive session beyond
90 seconds followed by successful volume-chord and tile restoration. Passing
the bounded gates remains mandatory even when continuous operation is desired.
