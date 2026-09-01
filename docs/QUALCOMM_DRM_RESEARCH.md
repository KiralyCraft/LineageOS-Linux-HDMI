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
