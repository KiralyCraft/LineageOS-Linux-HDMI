# Qualcomm DRM lease research notes

These notes record why `0.2.5-diagnostic.1` isolates Xorg instead of replacing
the lease architecture or immediately patching the display kernel module.

## Upstream lease behavior

DRM leases are intended to let the lessee set modes, flip framebuffers, and
control DPMS for only the named resources while the lessor retains the rest of
the display device:

- https://xorg.freedesktop.org/wiki/Events/XDC2017/packard_drm_lease.pdf

At upstream DRM/MSM commit
`140b13475302601368c0cf4e193e66126a49feb3`, the SM8350 HDK expected-failure
list contains cursor stress cases and `kms_lease@lease-uevent`, but not IGT's
real `kms_lease@simple-lease`, set-CRTC, page-flip, or implicit-plane lease
tests:

- https://gitlab.freedesktop.org/drm/msm/-/blob/140b13475302601368c0cf4e193e66126a49feb3/drivers/gpu/drm/ci/xfails/msm-sm8350-hdk-fails.txt
- https://gitlab.freedesktop.org/drm/igt-gpu-tools/-/blob/d822caac6e3589c05fffb63fbfc89134b0f7ccb3/tests/kms_lease.c

Absence from an xfail list is supporting evidence, not proof for this phone:
the Xperia uses Sony/Qualcomm's downstream SDE module rather than upstream
`drivers/gpu/drm/msm`.

## Xorg 21.1.24

The installed Xorg imports `ioctl` dynamically and its `-masterfd` path uses
the supplied fd instead of reopening `kmsdev`. Before its first final modeset,
the modesetting driver queries resources and capabilities, creates a tiny dumb
buffer, adds/removes a framebuffer, reads object properties and planes, and
clears the legacy cursor. Therefore a zero-length Xorg log does not identify
which DRM operation reset the phone.

The previous configuration explicitly selected `Atomic false` and
`ShadowFB false`. Xorg 21.1.24 has an atomic modesetting path and supports
software ShadowFB, so the diagnostic release provides both configurations but
does not select either as the production fix without a durable ioctl boundary:

- https://gitlab.freedesktop.org/xorg/xserver/-/tree/xorg-server-21.1.24/hw/xfree86/drivers/modesetting

## Connector property replay shutdowns

Two identical traced legacy Xorg starts reached the same connector-property
initialization sequence. In both cases, the phone shut down after the before
record for `DRM_IOCTL_MODE_SETPROPERTY` and before its after record. Correlating
the connector property list with Xorg's modesetting property loop identifies
that request as a write of the current value (`0`) to Qualcomm's vendor
`autorefresh` property. The preceding `ext_hdr_properties` blob read completed;
it was not the fatal operation. A first narrow guard successfully suppressed
that call, after which the phone shut down on the next vendor-property replay:
connector `79`, `bl_scale` property `55`, value `1024`. That value also exactly
matched the earlier connector object snapshot.

The diagnostic tracer now caches successful connector object-property
snapshots and, only for Xorg launched by the agent, emulates success when a
legacy connector `SETPROPERTY` exactly replays the cached value. A real value
change, unknown property, or different connector still reaches the kernel.
This avoids boot-specific ids and property-name lists while suppressing the
unsafe RandR initialization no-ops. Each suppression emits
`SUPPRESSED_CONNECTOR_NOOP` before returning success so a live test can prove
that the guard, rather than the kernel driver, handled the request.

That guard let Xorg proceed without resetting the phone, but Xorg then
segfaulted after the `topology_name` property and before issuing an ioctl for
the following `topology_control` property. Qualcomm exposes
`topology_control` as `DRM_MODE_PROP_BITMASK`. Xorg 21.1.24 retains that
property but creates RandR atoms only for range and enum properties; its
property setter later dereferences `p->atoms[0]` for every retained property.
The bitmask entry therefore has a null `atoms` pointer. Current upstream Xorg
still has the same unchecked loop:

- https://gitlab.freedesktop.org/xorg/xserver/-/blob/xorg-server-21.1.24/hw/xfree86/drivers/modesetting/drmmode_display.c

For agent-launched Xorg only, the preload library now wraps libdrm's
`drmModeGetProperty` and returns unsupported bitmask properties as unavailable.
That follows the modesetting driver's existing ignore path, keeps the system
Xorg binary unchanged, and emits `IGNORED_XORG_BITMASK` for verification.

## Fixed primary plane required by legacy SETCRTC

After filtering the bitmask property, Xorg initialized completely and reached
its first framebuffer modeset. With atomic client capability disabled, legacy
`MODE_SETCRTC` returned `EINVAL`. Enabling Xorg's atomic option caused it to set
`DRM_CLIENT_CAP_ATOMIC`; its initial modeset still used legacy `SETCRTC`, but
one cycle succeeded. Later cycles returned `EACCES` while composer logs showed
the selected active primary-type plane changing from `112` to `118` and `115`.

The exact installed kernel source has only one `EACCES` exit at that boundary:
`drm_mode_setcrtc()` requires the lease to hold the fixed
`crtc->primary->base.id`. Qualcomm SDE creates an array of primary planes, then
creates CRTCs from that array in the same index order. Its custom-client path
subsequently makes every plane compatible with every CRTC, so an active
primary-type plane is not proof that it is that CRTC's fixed primary object.

The composer patch now resolves the external CRTC's index in
`drmModeGetResources()` and leases the primary plane at the corresponding
index in `drmModeGetPlaneResources()`. It still rejects the transition if that
fixed plane has been reassigned to another CRTC. This is derived from the
driver's construction contract and does not hardcode runtime DRM object ids.

The same probe exposed two independent agent readiness defects. The root-owned
runtime directory prevented user `kiraly` from reading its Xauthority cookie,
and the installed `xinput` does not accept a `--display` option. The runtime is
now root:`kiraly` mode `0710`, readiness failures are retained in
`xdpyinfo.txt`, and input verification uses inherited `DISPLAY` plus the
configured Xorg device names.

## Write-only retire-fence property

The fixed-plane probe also showed Xorg replaying Qualcomm's `RETIRE_FENCE`
connector property with value `0xffffffff`. This was not the later failed
`SETCRTC`: the clocks place it about two seconds earlier, and Xorg continued
after the kernel returned `EFAULT`. The installed SDE driver treats the
property value as a userspace pointer, while its getter reports `~0` rather
than a reusable value. Consequently the normal RandR property initialization
turns a write-only fence request into an invalid `copy_from_user()` call.

For agent-launched Xorg only, the preload library now returns the exact
`RETIRE_FENCE` property as unavailable and emits `IGNORED_XORG_POINTER`.
`RETIRE_FENCE_OFFSET` remains visible because it is an ordinary scalar. The
existing snapshot no-op guard remains generic; this name-specific filter is
limited to the vendor ABI whose value is demonstrably a userspace pointer.

## Sony downstream SDE

The installed `msm_drm.ko` corresponds to Lineage's Sony SM8550 modules at
`ec2e039129f2b8f93fdfe62a8c6a595efb63d496`. This SDE implementation creates
primary and overlay planes but no dedicated cursor plane; legacy CRTC requests
are translated through DRM atomic helpers. Comparing the newer Lineage 23.2
tree at `6c745046312e86e9924cd1b268abd90d6f3d1018` found no broad lease or
modesetting correction. The one nearby safety change adds a null check in
`sde_crtc_complete_commit`; it is not evidence that this incident reaches that
path.

- https://github.com/LineageOS/android_kernel_sony_sm8550-modules

Do not replace `msm_drm.ko`, add extra leased planes, suppress cursor calls, or
backport that null check without a trace, pstore record, or Qualcomm display
recovery dump that identifies the corresponding path.
