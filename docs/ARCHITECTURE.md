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
3. Pause the existing external `HWCDisplay`, acknowledge that phase, and issue
   the same primary-display refresh used by Qualcomm's stock display-status
   path. The internal DSI display is never part of the lease.
4. Create a DRM lease containing exactly those three objects and pass its file
   descriptor to the root broker.
5. On release, revoke the lease, detach any lessee plane state, reset the DAL's
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

Diagnostic Xorg loads `libhdmi-los-drmtrace.so`. Before every DRM ioctl, the
library sends a structured record through the agent to the Android broker.
The broker appends and `fdatasync`s that record before acknowledging both hops;
the ioctl is not issued without that acknowledgement. Atomic object/property
tuples and legacy CRTC connector ids are recorded separately. Xorg is first
executed with `-version` and the same preload before composer is paused, so a
setuid or suppressed-preload configuration fails closed without touching the
external display. The agent does not accept RandR dimensions as proof of
scanout: it correlates the first enabling `SETCRTC` for the leased objects,
keeps a duplicate lease fd, and requires `GETCRTC` to report Xorg's framebuffer
at Android's exact timing before starting LXDE. Any later qualifying commit
failure terminates the session.

The tracer's only modeset compatibility substitution is an exact same-mode
fallback. After a legacy `SETCRTC` returns `EINVAL`, and only when connector,
CRTC, coordinates, and full timing equal the currently active mode, it submits
a page flip to the requested framebuffer and verifies it with `GETCRTC`.

Lease readiness is gated by the composer-owned `HWCDisplay` power and pause
state.  It deliberately does not use `HWDeviceDRM::active_`: that legacy DAL
member is never maintained in this source tree and remains false even while an
external CRTC and primary plane are actively scanning out.

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
