# Mode-safe takeover candidate installation and test

Release `0.2.8-candidate.6` keeps the Quick Settings tile as an arm/disarm
control and adds the asynchronous KGSL presentation path. The accelerated
runtime now requires a matched private `libgallium`, `libGLX_mesa`, and
`libEGL_mesa` set carrying bridge ABI 3; the launcher refuses a partial or
stale set. It also leaves Freedreno tiling enabled and disables only UBWC for
the bridge. Smaller accelerated surfaces use CPU-visible MIT-SHM slots;
UHD-sized surfaces use GPU blits into Xorg-owned KMS-compatible slots.

Candidate 5 changed the Quick Settings tile from an immediate
takeover button to an arm/disarm control. The default preset is 1920x1080 at
60 Hz. Arm with HDMI unplugged, then connect HDMI and accept Android's Mirror
prompt. The broker starts Xorg only after the actual Android mode and lease
readiness remain stable for three samples. Protocol-v1 and protocol-v2 chroot
agents are rejected. Candidate 5 also connects the APK's local broker socket
before setting its timeout; Android creates a `LocalSocket` lazily, so the old
ordering made the diagnostics activity and Quick Settings tile report
`socket not created` without ever contacting the broker.

During acquisition, composer pauses Android updates without powering the
external pipeline off, so the exact same-mode handoff can page-flip from the
last Android framebuffer. On release, composer performs a tracked HWC Off-to-On
transition after resetting the lease so Qualcomm's power state and fences match
the hardware before SurfaceFlinger resumes. A failed start still restores
Android automatically.

## Candidate-4 validation record

Live testing on the XQ-DQ72/pdx234 on 2026-09-02 established these results:

- Three consecutive unused three-second lease cycles restored Android
  mirroring and left SurfaceFlinger responsive.
- A bounded `safe` LXDE takeover displayed correctly at Android's negotiated
  1280x720 at 60 Hz mode. Its mandatory 60-second timeout stopped Xorg,
  performed the tracked HWC Off-to-On transition, and restored mirroring
  without a reboot or SurfaceFlinger stall.
- A default `kgsl-kms-bridge` session displayed LXDE and remained leased beyond
  the composer's 65-second watchdog, confirming continuous renewal.
- Unplugging HDMI while that accelerated X session was displayed caused the
  broker to begin recovery immediately and complete it in about 2.1 seconds.
  The phone stayed up, SurfaceFlinger remained responsive, Xorg exited, and
  the continuous agent returned to its ready state.
- The 1080p60 preference did not change the MacroSilicon sink's active Android
  mode from 1280x720 at 60 Hz. The broker correctly rejected the mismatch;
  selecting `native` while unplugged allowed the exact negotiated mode to be
  leased instead.

These results validate the candidate-4 release fix and unplug recovery, but do
not replace the remaining repetition, volume-chord, tile-stop, and evidence
checks in the success criteria below.

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
   `dist/hdmi-los-current-install-chroot.tar.gz`. Do not mix an older agent
   with this module.
6. For `kgsl-kms-bridge`, place the three matched ABI-3 Mesa libraries below
   the bundle's `lib/mesa/`. Do not reuse the system GLX/EGL frontends with the
   private DRI target.
7. Verify `/data/adb/hdmi-los/logs/gate.log`, `compatible.ok`, and the three
   read-only executable composer bind mounts.

## Gate 1: ordinary Android mirroring

Approve Android mirroring and exercise both displays before leasing. Do not
start the chroot agent yet. If the phone, dock, or capture signal resets, stop:
this is a power/PD/HPD problem rather than an Xorg ioctl problem.

## Gate 2: unused lease only

With Android mirroring active, run from an Android root shell:

```sh
/data/adb/modules/hdmi-los/bin/hdmi-losd probe lease-hold
```

The broker pauses the external Android display, creates the DRM lease, holds
the unused fd for three seconds, closes it, and restores Android. Run this
exactly three times. Do not proceed to Xorg if any cycle resets.

## Gate 3: armed, traced Xorg takeover

Disconnect HDMI. Long-press the `HDMI Xorg` tile, select **1080p60
(default)**, then tap the tile once. Its subtitle must say that it is armed and
waiting. Start the matching chroot bundle without phone-side capture:

```sh
cd /home/kiraly/Downloads/hdmi-los-runtime
./run-agent.sh --capture none --xorg-accel safe --session lxde --timeout
```

The equivalent root commands for preset selection and arming are:

```sh
/data/adb/modules/hdmi-los/bin/hdmi-losd mode 1080p60
/data/adb/modules/hdmi-los/bin/hdmi-losd arm
```

Connect HDMI and accept **Mirror**. Do not toggle again: Xorg should start
automatically after the mode settles. If HDMI was already connected at any
mode, the tile deliberately waits for an unplug before applying the preference;
it never changes a live external mode while arming.

Every DRM ioctl is synced to `/data/adb/hdmi-los/logs/broker.log` before it is
issued. If LXDE appears, verify the internal Android display, mouse, and
keyboard. Confirm that `/run/hdmi-los/xrandr.txt` reports 1920x1080 and that
the agent log contains `verified Xorg framebuffer`. Readiness requires both a
successful traced `SETCRTC` and a leased `GETCRTC` showing Xorg's framebuffer
at Android's exact timing. Hold both volume buttons for three seconds to
restore Android.

If the phone resets, do not retry. In Lineage Recovery, capture evidence with:

```sh
scripts/collect-crash-evidence.sh SERIAL NEW_DESTINATION_DIRECTORY
```

The recovery pass preserves `/tmp/recovery.log` and `/sys/fs/pstore`. Run the
collector again into a second new directory after normal boot to preserve
module logs, `/data/misc/recovery`, and Qualcomm display recovery dumps.

## Gate 4: production mode and diagnostic comparisons

The armed production path uses legacy Xorg KMS. This Sony kernel intentionally
rejects Xorg's atomic client-capability request, so `Atomic true` is not a
workaround. `probe xorg-atomic` remains only for collecting a comparison trace.

The production tracer has one narrow compatibility fallback. If the first
legacy `SETCRTC` returns `EINVAL`, it substitutes a page flip only when the
requested connector, CRTC, coordinates, and full mode timing exactly equal the
currently active leased mode. The agent then verifies that framebuffer through
`GETCRTC`. Any other error or mismatch aborts and restores Android.

Experimental presets can be selected only while disarmed:

```sh
/data/adb/modules/hdmi-los/bin/hdmi-losd mode native
/data/adb/modules/hdmi-los/bin/hdmi-losd mode 2160p60
```

Native accepts whichever active mode Android negotiates. 4K60 is known to be
slower and remains experimental. Always disarm, unplug, select the new preset,
arm, and reconnect; never switch the Android external mode during a lease.

## Gate 5: renewable continuous session

After one bounded success, stop the foreground agent, disarm and unplug, then
restart it with the no-argument operational default and arm the tile again:

```sh
cd /home/kiraly/Downloads/hdmi-los-runtime
./run-agent.sh
```

This is equivalent to `--capture none --xorg-accel kgsl-kms-bridge --session
lxde --no-timeout`. Connect HDMI, accept Mirror, and verify that broker status
reports continuous watchdog mode. End one short cycle with the volume chord,
one by tapping the tile, and one by unplugging HDMI. Android must recover after
all three. The unplug cycle must show broker-first Xorg shutdown rather than an
HWC uevent-thread teardown.

Continuous mode removes only the fixed broker deadline. It still requires both
physical volume inputs and the suspend blocker. The broker renews the composer
watchdog every 20 seconds; failed renewal, either control connection closing,
Xorg exit, HDMI unplug, or secure-display entry restores Android.

## Success criteria

A candidate is not accepted after one successful start. It requires three
unused lease cycles, three complete Xorg takeovers, volume-chord restore, tile
restore, safe HDMI unplug restoration, responsive internal Android UI, working
input, and no new pstore, display-recovery, or broker error evidence.
