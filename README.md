# LineageOS Linux HDMI

Run a Linux/Xorg desktop on the external USB-C/HDMI display of a Sony Xperia
1 V while Android continues to use the phone's internal display.

This project patches the Qualcomm display composer used by LineageOS so it can
temporarily hand only the external display to Xorg through a DRM lease. It is
an experimental, device- and ROM-specific implementation, not a general
Android desktop-mode application.

> **Warning:** the current profile supports only the Xperia 1 V (`XQ-DQ72`,
> LineageOS device `pdx234`) running
> `22.2-20250608-NIGHTLY-pdx234`. The Magisk module refuses to activate when
> the Android properties or original composer file hashes do not match that
> build. Do not install an old release or bypass the compatibility checks.

## TL;DR: how it works

1. The Magisk module starts a root broker during Android boot. The ordinary
   Quick Settings APK talks to that broker through a private local socket; the
   APK does not request `su` or control the display directly.
2. You start `./run-agent.sh` inside the Linux chroot. The agent registers with
   the broker and waits; starting it alone does not launch Xorg or take over
   HDMI.
3. Before connecting HDMI, select the desired mode and tap the **HDMI Xorg**
   tile to arm the broker. Then connect HDMI and accept Android's **Mirror**
   prompt.
4. Once Android has established a stable external mode, the patched Qualcomm
   composer leases only the external connector, CRTC, and plane to the chroot
   agent. Android continues using the phone's internal display.
5. The agent passes that DRM lease to Xorg, verifies real scanout, and starts
   LXDE. The default accelerated path uses a matched private Mesa GLX/EGL/DRI
   set for Freedreno/KGSL rendering and the Qualcomm-compatible presentation
   bridge; a separate preloaded tracer validates Xorg's DRM operations.
6. Tapping the tile again, unplugging HDMI, stopping the agent, an Xorg or
   watchdog failure, or holding both volume buttons stops Xorg and returns the
   external display to Android.

## What it does

During a takeover, Android keeps rendering the internal DSI panel while the
external connector, CRTC, and primary plane are leased to an X server running
inside a Linux chroot. LXDE, a mouse, and a keyboard can then use the connected
monitor. Stopping the session returns the same display to Android mirroring.

The project consists of:

- a narrowly patched Qualcomm composer and SDM stack;
- a root broker that coordinates the lease and automatic restoration;
- a chroot agent, Xorg DRM tracer, and input bridge;
- a signed Quick Settings tile used to request a takeover;
- exact ROM profiles, build gates, diagnostic probes, and recovery tooling;
- an optional patched Mesa build for native Freedreno/KGSL acceleration.

It does not replace Android, flash a Linux kernel, write a boot or vendor
partition, or start Xorg automatically at boot. The Magisk ZIP and matching
chroot bundle are separate artifacts and must come from the same build. The
operator explicitly arms the feature before connecting HDMI; Xorg starts only
after Android has connected the display, Mirror was accepted, the requested
mode is stable, and the foreground chroot agent is ready.

## Current status

Two Xorg paths have been tested on the target device:

| Mode | Rendering and presentation | Status |
| --- | --- | --- |
| `safe` (diagnostic fallback) | A dumb scanout buffer, ShadowFB, and software GL | `0.2.8-candidate.4` displayed LXDE and completed a bounded 60-second takeover without stalling SurfaceFlinger during release |
| `kgsl-kms-bridge` (default) | Native Freedreno/KGSL, zero-copy Xorg scanout, and an asynchronous MIT-SHM copy of the newest completed accelerated drawable | Visible LXDE and `glxgears` are verified. Uncapped `glxgears` now remains GPU-speed instead of being limited to the display refresh rate; continuous operation and unplug recovery were also validated on the device |

The accelerated path still needs a copy for each GL image delivered to Xorg because this
downstream Qualcomm stack renders correct pixels in the client but Xorg sees a
black image when it imports the same KGSL dma-buf in another context. It does
not continuously copy the full screen. The bridge drops superseded uncapped
frames before readback, waits native KGSL fences in a worker, and uses X Present
completion/idle events rather than a timer. On the 300x300 test workload this
kept application-side throughput within roughly 17-22% of Termux:X11 while the
physical display remained paced by its refresh rate. The exact investigation
and tested environment are documented in [GPU acceleration](docs/GPU.md).

Before HDMI is connected, the broker asks Android to prefer the configured
external mode. The default is 1920x1080 at 60 Hz; native/automatic and 4K60 are
available as experimental choices by long-pressing the tile. The agent then
reads the actual leased CRTC timing and gives Xorg that exact timing. A mode
mismatch, missing lease readiness, failed Xorg `SETCRTC`, or CRTC framebuffer
mismatch fails closed and restores Android's previous global preference.
If HDMI is already connected when armed, the broker waits for an unplug before
applying the preference; it never changes a live external mode while arming.
On the MacroSilicon capture path used for candidate-4 validation, Android still
negotiated 1280x720 at 60 Hz after the 1080p60 preset had been selected. The
broker correctly refused that mismatch. Selecting `native` while unplugged
allowed the next connection to inherit and lease Android's exact 1280x720
timing.

Earlier live testing showed that 3840x2160 can work but starts more slowly and
is noticeably less responsive. A later 1920x1080 run exposed a downstream SDE
quirk: an otherwise identical legacy `SETCRTC` returned `EINVAL`. The current
tracer permits only an exact same-timing page flip as a narrow fallback and
verifies the resulting framebuffer before LXDE is declared ready.

This remains research-quality software. A successful source build does not
prove that another phone, ROM build, dock, display, or proprietary composer
combination is safe.

## How the takeover works

```text
operator selects a mode and arms HDMI Xorg before cable insertion
                  |
                  v
Android connects external display at that mode; operator accepts Mirror
                  |
                  v
patched composer pauses only the external HWC display
                  |
                  v
composer creates a DRM lease for connector + CRTC + primary plane
                  |
                  v
root broker passes the lease to the chroot agent and Xorg :1
                  |
                  v
session ends, times out, disconnects, or receives the volume-key escape
                  |
                  v
composer revokes the lease, repairs cached state, and resumes Android
```

The Android composer remains the DRM master. The broker journals Android's old
preferred-mode setting, records each takeover boundary durably, and Xorg's DRM
ioctls are traced before execution. The internal display is never included in
the lease. See
[Architecture and invariants](docs/ARCHITECTURE.md) for the full design.

The tile and diagnostics activity are deliberately ordinary, unprivileged
Android components. They do not execute `su`, so no Magisk permission prompt is
expected. They connect to the root broker's private local socket, and the
broker accepts commands only when the kernel-reported peer UID matches the
installed `dev.kiraly.hdmilos` package UID (or is root).

## Requirements

- Sony Xperia 1 V `XQ-DQ72` / `pdx234` with the exact supported LineageOS build
- Magisk/root access and working `su`
- a USB-C DisplayPort/HDMI dock and an external display
- an ext4 Linux chroot containing Xorg and LXDE
- a Linux build host with enough space for the captured LineageOS source tree
- access to the exact proprietary Sony inputs recorded by the selected profile

A USB HDMI capture device is useful for development but is not required for a
normal monitor. During testing, power the capture device from the workstation,
not from the phone's OTG dock.

## Source and patched Mesa

Clone the main repository normally for the safe path:

```sh
git clone https://github.com/KiralyCraft/LineageOS-Linux-HDMI.git
cd LineageOS-Linux-HDMI
```

The optional submodule at
`third_party/mesa-for-android-container` documents and pins the Mesa source
used by `kgsl-kms-bridge`. Initialize it when reproducing or changing the
accelerated path:

```sh
git submodule update --init --depth 1 third_party/mesa-for-android-container
```

It tracks the `fix/kgsl-leased-screen` update line and is pinned by this
repository to commit
[`3ce48e02`](https://github.com/KiralyCraft/mesa-for-android-container/commit/3ce48e027e1a84c3b1ad527dda35fbc5c11d87ae).
That branch alone contains the leased-screen KMS/SHM bridge; the separate
`fix/kgsl-present-wait-fence` pull-request branch does not.
The HDMI build does not compile the submodule automatically. The resulting
private `libgallium-*.so`, `libGLX_mesa.so.0`, and `libEGL_mesa.so.0` must all
come from the same completed Mesa build and be placed below `lib/mesa/` in the
chroot runtime bundle before the accelerated agent mode will start. Do not
replace only `libgallium`: GLX and EGL embed the bridge drawable structure and
a mixed set can corrupt memory. The launcher checks the ABI marker in all three
files and fails closed.

## Build and verify

Run the source-level tests locally:

```sh
make test
```

The build automation performs compilation and ZIP assembly on a separate
Linux host. Override the repository's development-server default when using
your own machine:

```sh
make server-preflight SERVER=root@build-host
make zip SERVER=root@build-host
make verify
```

Artifacts are returned to `dist/`:

- `hdmi-los-<profile>-magisk.zip`
- `hdmi-los-<profile>-chroot.tar.gz`
- signed-APK certificate information
- a provenance manifest and `SHA256SUMS`

The build materializes the exact captured LineageOS manifest and proprietary
Sony revisions. It first builds an unmodified baseline, then refuses packaging
if the patch changes incompatible exports, dependencies, object sizes, or
existing C++ vtable slots. A profile or source mismatch fails closed.

For a packaging-only change, or for an agent diagnostic that reuses a verified
composer, use the bounded reuse targets described by `make help`. They require
the full commit ID of the verified binary source.

## Install and run

Follow the staged [manual test checklist](docs/MANUAL_TEST.md); do not skip its
ordinary-mirroring and unused-lease gates. In outline:

1. Install the matching Magisk ZIP and reboot.
2. Deploy the matching chroot bundle without mixing files from older builds.
3. With HDMI unplugged, long-press **HDMI Xorg** to select a mode (1080p60 is
   the default), then tap the tile to arm it.
4. Start the foreground agent inside the mounted chroot.
5. Connect HDMI and accept Android's **Mirror** prompt. The broker starts Xorg
   automatically after three matching mode samples.
6. End the session with the volume-key escape or by tapping the tile again.

The tested accelerated LXDE session is now the launcher default:

```sh
cd <hdmi-los-runtime>
./run-agent.sh
```

That is equivalent to:

```sh
cd <hdmi-los-runtime>
./run-agent.sh \
  --capture none \
  --xorg-accel kgsl-kms-bridge \
  --session lxde \
  --no-timeout
```

No takeover starts merely because the module or agent is present. Tapping the
idle tile arms the requested mode; tapping while armed disarms it, and tapping
while leased stops Xorg and disarms it. The equivalent root commands are:

```sh
/data/adb/modules/hdmi-los/bin/hdmi-losd mode 1080p60
/data/adb/modules/hdmi-los/bin/hdmi-losd arm
/data/adb/modules/hdmi-los/bin/hdmi-losd status
/data/adb/modules/hdmi-los/bin/hdmi-losd disarm
```

Continuous mode removes the broker's fixed 60-second session deadline, but it
does not remove automatic recovery: the broker renews the composer's 65-second
watchdog every 20 seconds. If renewal stops, the agent or broker disconnects,
Xorg exits, HDMI is unplugged, or the volume escape is used, Android is
restored. Use `--timeout` for a bounded 60-second session, and use
`--xorg-accel safe` for the software-rendered ShadowFB fallback.

## Safety and recovery

- Hold Volume Up and Volume Down together for three seconds to restore Android.
- `--timeout` sessions restore after 60 seconds; the composer has an independent
  65-second backstop.
- Default continuous sessions renew that composer backstop every 20 seconds and can
  run indefinitely only while the broker remains healthy.
- Broker or agent disconnect, HDMI unplug, and secure-display entry also force
  lease release. Unplug is queued out of the HWC uevent thread so the broker
  can stop Xorg first; composer retains a five-second forced-release backstop.
- The preferred-mode recovery journal is replayed on broker startup, so a
  broker restart does not leave Android's global display preference changed.
- Disable or uninstall the Magisk module and reboot to restore the original
  vendor files.
- Magisk safe mode (hold Volume Down during boot) disables all modules.
- If the phone resets, stop testing and collect recovery evidence before
  another attempt.

The early releases documented in
[the boot-failure record](docs/BOOT_FAILURE_2026-09-01.md) are unsafe and must
not be installed. The current release metadata and exact compatibility profile
in this repository are authoritative.

## Documentation

- [Manual installation and test gates](docs/MANUAL_TEST.md)
- [Architecture and safety invariants](docs/ARCHITECTURE.md)
- [GPU acceleration and the Mesa bridge](docs/GPU.md)
- [Qualcomm DRM investigation](docs/QUALCOMM_DRM_RESEARCH.md)
- [Rollback procedure](docs/ROLLBACK.md)
- [Historical boot failure](docs/BOOT_FAILURE_2026-09-01.md)
