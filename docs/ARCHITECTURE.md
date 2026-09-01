# Architecture and invariants

The Android composer remains the DRM master.  The patch adds a private,
root-only control endpoint inside the existing Qualcomm composer process.  For
one connected pluggable display it performs this sequence under the session
lock:

1. Identify the connector, its current CRTC, and the primary plane already
   selected by the DAL.
2. Pause the existing external `HWCDisplay`; the internal DSI display is never
   part of the lease.
3. Create a DRM lease containing exactly those three objects and pass its file
   descriptor to the root broker.
4. On release, revoke the lease, detach any lessee plane state, reset the DAL's
   cached CRTC/plane state, and resume the same `HWCDisplay` object.

Lease readiness is gated by the composer-owned `HWCDisplay` power and pause
state.  It deliberately does not use `HWDeviceDRM::active_`: that legacy DAL
member is never maintained in this source tree and remains false even while an
external CRTC and primary plane are actively scanning out.

The composer independently revokes after 65 seconds or when its broker
disconnects.  Unplug and secure-display entry also force release.  SurfaceFlinger
hotplug notifications are suppressed only while this already-created external
display is leased.

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

The chroot agent creates two stable uinput devices for Xorg and hot-grabs only
the configured Bluetooth mouse and keyboard.  The Android broker separately
grabs only the two physical volume-key devices.  Losing either volume device,
holding both keys for three seconds, losing either control connection, or
reaching 60 seconds stops Xorg before asking composer to resume Android.

No code in this repository writes a boot, init_boot, vendor, system, or vbmeta
partition.
