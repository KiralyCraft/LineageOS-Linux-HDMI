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
chroot bundle are separate artifacts and must come from the same build.

## Current status

Two Xorg paths have been tested on the target device:

| Mode | Rendering and presentation | Status |
| --- | --- | --- |
| `safe` (default) | Atomic KMS, a dumb scanout buffer, ShadowFB, and software GL | Visible LXDE; bounded takeovers restore Android |
| `kgsl-kms-bridge` (opt-in) | Native Freedreno/KGSL, zero-copy Xorg scanout, and one MIT-SHM copy per swapped accelerated drawable | Visible 1920x1080 LXDE and `glxgears` at about 55-57 FPS |

The accelerated path still needs a copy for each GL window swap because this
downstream Qualcomm stack renders correct pixels in the client but Xorg sees a
black image when it imports the same KGSL dma-buf in another context. It does
not continuously copy the full screen. The exact investigation and tested
environment are documented in [GPU acceleration](docs/GPU.md).

This remains research-quality software. A successful source build does not
prove that another phone, ROM build, dock, display, or proprietary composer
combination is safe.

## How the takeover works

```text
Android owns internal + external displays
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

The Android composer remains the DRM master. The broker records each takeover
boundary durably, and Xorg's DRM ioctls are traced before execution. The
internal display is never included in the lease. See
[Architecture and invariants](docs/ARCHITECTURE.md) for the full design.

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
repository to tested commit
[`89da2771`](https://github.com/KiralyCraft/mesa-for-android-container/commit/89da27716279aed04c09884b79c86f15db72427d).
The HDMI build does not compile the submodule automatically. The resulting
private `libgallium-*.so` must be placed below `lib/mesa/` in the chroot runtime
bundle before the accelerated agent mode will start.

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
3. Start the foreground agent inside the mounted chroot.
4. Add and tap the **HDMI Xorg** Quick Settings tile.
5. End the session with the volume-key escape or wait for automatic restore.

Start the default safe session with:

```sh
cd <hdmi-los-runtime>
sudo -n ./run-agent.sh --capture none
```

Start the tested accelerated session with:

```sh
cd <hdmi-los-runtime>
sudo -n ./run-agent.sh \
  --capture none \
  --xorg-accel kgsl-kms-bridge \
  --session lxde
```

No takeover starts merely because the module or agent is present. The tile or
an explicit root diagnostic command initiates it.

## Safety and recovery

- Hold Volume Up and Volume Down together for three seconds to restore Android.
- The agent forces restoration after 60 seconds; the composer has an
  independent 65-second backstop.
- Broker or agent disconnect, HDMI unplug, and secure-display entry also force
  lease release.
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
