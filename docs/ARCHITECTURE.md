# Architecture and invariants

The Android composer remains the DRM master.  The patch adds a private,
root-only control endpoint inside the existing Qualcomm composer process.  For
one connected pluggable display it performs this staged sequence:

1. Identify the connector, its current CRTC, and the primary plane already
   selected by the DAL.
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

Broker protocol version 2 adds root-only diagnostic probes. The independently
versioned composer socket remains at version 1 so the exact verified 0.2.4
composer can be reused. `lease-hold` stops after receiving the lease and closes
it three seconds later without starting the chroot agent. `xorg-legacy` and
`xorg-atomic` pass the same leased objects to Xorg with explicit legacy/atomic
configuration. The diagnostic module rejects normal tile starts, and its
version-2 broker rejects the older untraced agent.

Diagnostic Xorg loads `libhdmi-los-drmtrace.so`. Before every DRM ioctl, the
library sends a structured record through the agent to the Android broker.
The broker appends and `fdatasync`s that record before acknowledging both hops;
the ioctl is not issued without that acknowledgement. Atomic object/property
tuples and legacy CRTC connector ids are recorded separately. Xorg is first
executed with `-version` and the same preload before composer is paused, so a
setuid or suppressed-preload configuration fails closed without touching the
external display.

Lease readiness is gated by the composer-owned `HWCDisplay` power and pause
state.  It deliberately does not use `HWDeviceDRM::active_`: that legacy DAL
member is never maintained in this source tree and remains false even while an
external CRTC and primary plane are actively scanning out.

The composer independently revokes after 65 seconds or when its broker
disconnects. Unplug and secure-display entry also force release. SurfaceFlinger
hotplug handling is deferred only from resource preparation through restoration.

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

The diagnostic release uses Magisk `resetprop` before composer startup to
request Qualcomm display hardware-recovery dumps. The broker refuses probes if
that property is no longer `0`. This override is diagnostic-only and does not
modify a partition or persist after the module is disabled.

The chroot agent creates two stable uinput devices for Xorg and hot-grabs only
the configured Bluetooth mouse and keyboard.  The Android broker separately
grabs only the two physical volume-key devices.  Losing either volume device,
holding both keys for three seconds, losing either control connection, or
reaching 60 seconds stops Xorg before asking composer to resume Android.

No code in this repository writes a boot, init_boot, vendor, system, or vbmeta
partition.
