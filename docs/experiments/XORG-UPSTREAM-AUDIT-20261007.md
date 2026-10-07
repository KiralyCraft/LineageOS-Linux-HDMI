# Upstream Xorg migration assessment

Source inspection on 2026-10-07 supports moving the experimental stack to the
Xorg 26.1 release line, separately from the already built consumer-release/queue
candidate. This is an assessment, not a completed migration or hardware result.

The current private server reports 1.21.1.24. Official source archives inspected:

- [21.1.25](https://www.x.org/releases/individual/xserver/xorg-server-21.1.25.tar.xz),
  the newer maintenance release. Its inspected presentation code still lacks the
  modesetting TearFree implementation and explicit Present release-syncobj path.
- [26.0.99.903](https://www.x.org/releases/individual/xserver/xorg-server-26.0.99.903.tar.xz),
  the 26.1 release candidate. SHA256:
  `e5a7e3d568e1dcf98f305d21f5e5a88dece7a7bb5ac70eb64b2759ca20ade4a0`.
  Earlier RC2 was also inspected; findings below were checked against RC3.

## What an upgrade removes and what it does not

| Current patch or feature | Finding in 26.0.99.903 | Migration implication |
| --- | --- | --- |
| 0003 upstream TearFree backport | TearFree already present in modesetting | Drop the 1,943-line backport and use upstream's implementation. |
| 0001 wait-fence callback disarming | `present_wait_fence_triggered()` still calls re-execute without first disarming the callback | Carry the targeted fix pending upstream resolution. |
| 0004 TearFree region cleanup | The inspected empty-region return still lacks `RegionUninit()` | Review and retain the needed cleanup hunks. |
| 0005 asynchronous TearFree | `drmmode_copy_damage()` still ends with `glamor.finish()` | Updating alone does not remove this GPU-completion wait. Port the asynchronous lifecycle. |
| 0006 eligible-copy blit and E diagnostics | `glamor_copy.c` retains the shader-copy implementation | Port and validate separately; do not infer a faster copy backend from the version number. |
| 0007 companion deferred presenter | No integration with our private companion | Remains device-specific integration. |
| 0010 copied-buffer release | Explicit release-syncobj branch exists, but the ordinary branch still flushes and calls `present_pixmap_idle()` | The existing client path still needs safe consumer completion. A protocol migration is separate work. |

The explicit-sync support is real upstream code, not something we need to invent.
In `present/present_execute.c:126`, a release syncobj receives a fence from the
backend's `flush_fenced` callback. However, modesetting's `present_screen_info`
registers only `flush`, and glamor's DRI3 screen info remains version 2, without
an `import_syncobj` backend. DRI3 import checks for interface version 4 and the
callback. Consequently, that generic support is not a ready-to-use replacement
for our legacy Present/KGSL path simply by upgrading the Xorg binary.

This does not establish that KGSL can never participate in the standardized
protocol. A future integration should evaluate the DRM timeline object provider,
native sync-file import/export and Mesa negotiation end to end. Prefer upstream
interfaces when they cover the required ownership semantics.

## Recommended migration boundary

Use a separate `feature/xorg-26-rebase` development branch and a separate Downloads
bundle. Start from a pinned upstream release/RC and rebuild the server, glamor,
modesetting and required input modules as a compatible set on the build server.
The old build script's frozen generated configuration and object/archive reuse
are unsuitable for a major-version rebase; use a clean Meson build.

Remove patches already supplied upstream, then port the small platform adapters
and the remaining asynchronous state machines. Preserve the existing USB/BT
input, lease compatibility, Android coexistence and CPU-policy behavior. Test
the same presentation workload against the retained 21.1 candidate before
promotion. Do not switch the waiting agent or restart HDMI as part of this audit.

The new release/queue implementation is custom integration of established
ownership rules, not a new synchronization protocol. Updating reduces the
maintenance burden, but does not by itself fix the outstanding observed symptoms.

## Additional validation of the prepared 21.1 candidate

After packaging, the two-process shared-image test ran using the new bundle's
Mesa libraries on FD740 and passed 60 pixel checks, including 3840x2160 and
alignment-edge sizes. The producer did not map/read back the shared image.
The consumer completed before each next producer frame, so this is shared-image
visibility validation only: it does not exercise the new Xorg release callback,
asynchronous buffer reuse, physical scanout, OpenJK or resizing acceptance.
Evidence remains in `.local/presentation-fixes-20261007/shared-image.json`.
