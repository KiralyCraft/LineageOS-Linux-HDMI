# Architecture and invariants

The Android composer remains the DRM master.  The patch adds a private,
root-only control endpoint inside the existing Qualcomm composer process.  For
one connected pluggable display it performs this staged sequence:

1. Identify the connector and current CRTC. Resolve the CRTC's fixed primary
   plane by matching its DRM resource index to SDE's primary-plane construction
   order; the primary-type plane Android currently scans out is not necessarily
   the CRTC's `crtc->primary` object.
2. Release the global pluggable-display handler lock. The non-Android takeover
   state defers hotplug teardown, while the per-display sequence lock pins each
   short operation without blocking a driver callback on the global lock.
3. Pause updates on the existing external `HWCDisplay` without powering off
   its negotiated CRTC timing. The internal DSI display is never part of the
   lease.
4. Under Qualcomm's global SurfaceFlinger command-sequence lock, refresh the
   CRTC-fixed primary selection. Atomically detach and sanitize every plane on
   the external CRTC, then create a DRM lease containing the connector, CRTC,
   and detached fixed primary plane.
5. On release, revoke the lease, detach the lessee primary plane, reset the DAL's
   cached CRTC/plane state, and resume the same `HWCDisplay` object.

The broker fsyncs a persistent transition breadcrumb before and after agent
preparation, composer preparation, pause, lease creation, and Xorg startup.
Composer rolls back a prepared or paused partial transition when the broker
disconnects or a phase fails; its independent 65-second deadline begins with
the first preparation phase.

Broker protocol version 3 adds persistent mode selection and explicit arm,
disarm, and waiting states. Composer protocol version 2 reports physical
connection, lease readiness, and active width/height/refresh while keeping the
wire packet fixed at 160 bytes. `lease-hold` stops after receiving the lease
and closes it three seconds later without starting the chroot agent.
`xorg-legacy` and `xorg-atomic` remain root-only comparison probes. The
production tile arms the legacy path because the installed Sony kernel
intentionally rejects Xorg's atomic-client opt-in.

The tile APK does not invoke `su`. It connects as its ordinary Android UID to
the broker's abstract `hdmi-los-broker-v1` Unix stream socket. The broker reads
the peer credentials from the accepted socket and permits only root or the UID
that Android currently assigns to `dev.kiraly.hdmilos`; the SELinux policy
grants that app domain only the narrow socket connection needed for this path.

When armed, the broker atomically journals Android's prior global preferred
display mode under `/data/adb/hdmi-los`, applies the selected preset through
`cmd display`, and polls composer at 250 ms. It requires three stable samples
of the requested mode, physical connection, lease readiness, and agent
presence. If HDMI was already connected at the wrong timing it requests a
replug instead of changing the live display. Disarm, release, failure, and
broker startup all replay the recovery journal.

Xorg loads `libhdmi-los-drmtrace.so` as a compatibility and startup-verification
interposer. Until takeover is independently verified, every DRM ioctl is sent
as a structured record through the agent to the Android broker. The broker
appends and `fdatasync`s each record before acknowledging both hops; the ioctl
is not issued without that acknowledgement. Atomic object/property tuples and
legacy CRTC connector ids are recorded separately. Xorg is first executed with
`-version` and the same preload before composer is paused, so a setuid or
suppressed-preload configuration fails closed without touching the external
display. The agent does not accept RandR dimensions as proof of scanout: it
correlates the first enabling `SETCRTC` for the leased objects, keeps a
duplicate lease fd, and requires `GETCRTC` to report Xorg's framebuffer at
Android's exact timing before starting LXDE.

After that proof and an independent Xorg process check, the default
`--drm-trace startup` acknowledgement moves the interposer into steady state.
Routine page-flip, vblank, dirty-framebuffer, and cursor ioctls then go directly
to DRM without two broker round trips and two durable log syncs per call. The
interposer remains loaded, its connector-property and same-mode compatibility
rules remain active, and every later `SETCRTC` remains durably traced and
fail-closed. `--drm-trace full` retains per-ioctl tracing for diagnosis, but its
synchronous logging overhead invalidates performance measurements.

The tracer's only modeset compatibility substitution is an exact same-mode
fallback. After a legacy `SETCRTC` returns `EINVAL`, and only when connector,
CRTC, coordinates, and full timing equal the currently active mode, it submits
a page flip to the requested framebuffer and verifies it with `GETCRTC`.

Lease readiness is gated by the composer-owned `HWCDisplay` power and pause
state.  It deliberately does not use `HWDeviceDRM::active_`: that legacy DAL
member is never maintained in this source tree and remains false even while an
external CRTC and primary plane are actively scanning out.

The final handoff runs under Qualcomm composer's global command-sequence lock.
It enumerates the complete DRM plane set and selects the CRTC-fixed primary
plus every plane still assigned to the external CRTC. One atomic request
detaches those planes, clears their source/destination geometry, and resets
supported Qualcomm scaler and exclusion-rectangle state. Composer submits the
request with `TEST_ONLY`, commits it synchronously, and reads every property
back before creating the lease. The connector and CRTC timing remain active.
This prevents both stale Android overlays above Xorg and inherited private
scaler state without a delay, retry, or hard-coded object id.

The default lease deliberately contains no cursor overlay. The paired optional
composer and Xorg cursor patches can lease and drive an idle compatible overlay
for diagnosis, but live testing showed that synchronous plane updates still
stall presentation during pointer motion. They are omitted from the normal
patch series and do not fix the cadence problem merely by rendering the cursor
on a distinct plane.

The remaining software-cursor slowdown is an interaction between upstream
Xorg and this downstream DRM implementation. Xorg 21.1.24 probes `DIRTYFB`,
registers root-pixmap damage when the probe succeeds, and calls
`drmModeDirtyFB()` for every accumulated damaged region. A visible Xorg cursor
also increments `sprites_visible`, which makes modesetting's Present path
reject page flips and fall back to copying into that damaged root pixmap. Sony's
SM8550 framebuffer installs `drm_atomic_helper_dirtyfb` as its dirty callback;
the kernel documents that helper as deliberately blocking and implements each
call as a synchronous atomic commit. Pointer motion therefore both removes the
fast flip path and adds blocking KMS work.

This also explains why the optional overlay is not the final design. Direct
syscall tracing found its individual legacy plane ioctls completing in a few
milliseconds; the seconds-long delay was client-side `XFlush` backpressure over
many synchronous updates, not one 2.85-second `drmModeSetPlane()` syscall. A
proper cursor path must coalesce cursor state and composite or commit it at the
output cadence without damaging the root pixmap. Termux:X11 demonstrates the
relevant architecture: its X cursor hooks update separate cursor state and wake
the renderer, which blends a cursor texture into the rendered output before
the swap. That is the model for the next implementation, rather than disabling
kernel synchronization or adding timers.

A candidate-15 capture confirms the consequence at the physical output. With
the FD740 bridge and identical 120 Hz pointer motion, hiding the cursor produced
60.034 active HDMI updates per second with no skipped or torn source frames.
Showing it left the client near 60 FPS but reduced HDMI to 56.637 active updates
per second, skipped 114 source frames, and made all 1,135 decoded updated frames
disagree between the top and bottom Gray-code counters. The problem is thus
presentation eligibility and scanout coherence, not raw application rendering
or input-injection speed.

Primary source references:

- [Xorg modesetting root damage](https://gitlab.freedesktop.org/xorg/xserver/-/blob/xorg-server-21.1.24/hw/xfree86/drivers/modesetting/driver.c)
- [Xorg modesetting Present flip checks](https://gitlab.freedesktop.org/xorg/xserver/-/blob/xorg-server-21.1.24/hw/xfree86/drivers/modesetting/present.c)
- [pdx234 framebuffer dirty callback](https://github.com/LineageOS/android_kernel_sony_sm8550-modules/blob/ec2e039129f2b8f93fdfe62a8c6a595efb63d496/qcom/opensource/display-drivers/msm/msm_fb.c)
- [exact Lineage kernel dirty helper](https://github.com/LineageOS/android_kernel_sony_sm8550/blob/d00ba216ccda5d4fcc0d864729ae69d5b63d860c/drivers/gpu/drm/drm_damage_helper.c)
- [Termux:X11 cursor renderer](https://github.com/termux/termux-x11/blob/9df8b767645aa0d0a2f2576767449df55b41962f/lorie/src/main/cpp/lorie/renderer.cpp)

The composer independently revokes after 65 seconds or when its broker
disconnects. In an explicitly registered continuous session, the broker omits
its normal 60-second deadline and renews the composer's watchdog every 20
seconds. The lease can therefore remain active while both processes are
responsive, without losing the composer-side recovery path if the broker
wedges. A leased-display unplug is queued to the composer server rather than
running DRM teardown on the HWC uevent thread. The broker stops Xorg first;
composer forces release after five seconds if that cleanup does not arrive.
Secure-display entry still revokes immediately. SurfaceFlinger hotplug handling
is deferred only from resource preparation through restoration.

Lease bookkeeping lives in a private sidecar map in the DAL translation unit,
not in `HWDeviceDRM`.  Its state is erased on reset and DAL teardown.  This keeps
the object layout and all pre-existing adjustment thunks compatible with the
installed proprietary display extension.  Every build proves that property
against an unpatched exact-tree baseline before packaging.

Lease calls also stay outside the existing `DisplayInterface` and `HWInterface`
virtual tables. Namespace-level bridge functions enter non-virtual methods on
`DisplayBase` and `HWDeviceDRM`; no existing base or derived virtual slot is
added, removed, or moved. The ABI gate decodes Android packed relocations and
compares every existing exported vtable's size and slot identity.

The Magisk module has `skip_mount`; it never unconditionally overlays `/vendor`.
At `post-fs-data` it compares five build properties and SHA-256 hashes of all
three untouched files.  Only a complete match permits early bind mounts.  The
runtime broker also requires the gate marker.

When the optional `diagnostic-only` package marker is present, Magisk
`resetprop` requests Qualcomm display hardware-recovery dumps before composer
startup and the broker refuses probes if that property is no longer `0`. The
candidate package omits the marker and does not change that property.

The chroot agent creates two stable uinput devices for Xorg and hot-grabs only
the configured Bluetooth mouse and keyboard.  The Android broker separately
grabs only the two physical volume-key devices.  Losing either volume device,
holding both keys for three seconds or losing either control connection stops
Xorg before asking composer to resume Android. The launcher defaults to a
renewed continuous lease; `--timeout` restores the fixed 60-second broker
deadline. Both modes preserve all other recovery paths.

No code in this repository writes a boot, init_boot, vendor, system, or vbmeta
partition.
