# 4K copy and wait investigation

These are supplemental diagnostic patches for the existing patched Xorg 21.1.24. They are not added to the production xserver patch series. Apply `xorg-tearfree-region-cleanup.patch` followed by `xorg-latency-diagnostics.patch` after the three existing xserver patches. Rebuild all modesetting objects because its private CRTC structure changes, and use matching Xorg/glamor/modesetting artifacts. The supplied build script preserves the original sources and objects and uses the exact O2 build commands on the existing ARM builder.

Set `HDMI_LOS_LATENCY_STATS=1` in the **Xorg** process environment for aggregate reports in `Xorg.1.log`. The default when unset is off. The candidate Downloads launcher sets it after its sudo re-exec. Reports occur about every five seconds of activity; no per-operation logging, GL query, extra fence, or change to flip ordering is added. Logging overhead still requires a disabled/enabled comparison for rigorous timing.

Counters separate damage-copy submission, `glamor.finish`, total copy, successful flip queueing, queue-to-callback, callback intervals, first observed damage-to-copy, and callback-minus-reported-UST. They include exact summed rectangle area, pending-flip skips, failures, repeated/backward MSC, and future UST. The 250-microsecond histogram quantiles are **upper bounds**, with a final overflow bucket bounded by the observed maximum. Copy-total includes finish; do not sum those two measurements. Callback intervals include inactivity and are not physical HDMI frame timestamps. UST age is meaningful only with matching clock domains. The glamor counters report attempted GPU copies, temporary copies, uploads, readbacks and root-to-root temporary-copy attempts; they do not claim completed rendering or full-screen traffic.

The restarted combined bundle has now been measured; see [live validation and timing results](LIVE-20261002.md).

The [2026-10-05 follow-up](LIVE-20261005.md) includes the unlocked one/two/one-window comparison, a bounded-reference experiment confirming stale vblank accounting, and hardware copy/fence costs. [Measurement tools and recorded diagnostic extracts](live-tools/README.md) accompany the compact results. The invalid locked comparisons are explicitly excluded.

## Findings against the deployed build

- The active server already uses FD740 glamor, software cursor, TearFree, and startup-only DRM tracing. The private Termux Present pacer is gated off on this leased Xorg; the HDMI bridge has its own queue scheduling.
- Xorg's event loop calls the damage-aware copy and `glFinish` before queueing a TearFree flip. One flip remains outstanding, so subsequent damage accumulates until completion. Removing the finish without another completion mechanism is incorrect.
- Overlapping root-to-root moves use `glamor_copy_fbo_fbo_temp` when `GL_MESA_tile_raster_order` is absent. The deployed FD740 GBM/OpenGL context advertises `GL_NV_texture_barrier` but not tile raster order. This can add a temporary pixmap and two GPU copy legs before TearFree's final root-to-scanout copy. The live movement test confirms frequent use; see the validation report.
- The normal TearFree-success path avoids the old dirty-FB notification path. Retry/failure paths remain measurable separately.
- Two TearFree routines leave an intersected temporary Region initialized without finalization. Multi-rectangle regions can therefore leak heap storage. The independent cleanup patch finalizes the regions; no quantified claim about its latency impact is made.
- An offscreen hardware test using the deployed Mesa and Xorg's GBM settings exported a native fence after a 4K clear; polling that fence signaled successfully. This establishes the prerequisite for an event-loop-based completion design, not a validated asynchronous TearFree implementation.

## Correct allocation before comparing performance

Mesa commit `daa6e56de` on `fix/kgsl-renderonly-height-padding`, based on the combined coexistence commit `cc190637b`, pads KGSL renderonly backing allocations to four rows while retaining logical dimensions. The old build failed 24 of 44 offscreen GBM allocation/export cases; the patched matched build passed all 44 on the device. This is the source of at least some resize-induced Xorg export failures and CPU-readback fallback. It does not establish that all Qt resize sluggishness has the same cause.

`gbm-height-regression.py` requires the chosen matched Mesa paths and `FD_KGSL_ENABLE_DMABUF=1`, `FD_KGSL_RENDERONLY=1`, `FD_FORCE_KGSL=1`, `MESA_LOADER_DRIVER_OVERRIDE=kgsl`. It only allocates, exports, and frees offscreen buffers; it does not modeset. `egl-fence-probe.py` additionally verifies FD740 OpenGL and native fence completion using an offscreen FBO. Run with device permissions and inherit the relevant session environment.

## Next engineering direction

If clean timing confirms the finish wait dominates, preserve the GPU copy but export its native completion fence and register it with Xorg's existing FD event loop. Queue the legacy page flip only once the fence signals. Keep separate copying/ready/pending-flip states; preserve damage arriving after the snapshot; delay DRI fake-flip completion for content that was not in the snapshot; cancel pending FDs safely on disable, mode change, VT loss and lease release. A missing/failed fence must retain a safe synchronous path. This can avoid blocking Xorg without requiring atomic in-fence support or a Magisk/plane-ownership change. It does not eliminate the copy or guarantee reduced physical latency.

The desktop root is currently scanout-compatible and linear. A private tiled root is another hypothesis, requiring explicit handling of fallback scanout, exports, mode changes and presentation. Neither that design nor an asynchronous wait replacement is enabled by these patches. The Mesa bridge still uses a full-drawable synchronous GPU blit; optimizing its copy requires correct per-slot damage history and source-buffer lifetime.

No kernel uprobes should be used for this investigation: earlier captures triggered ring-buffer and scheduling warnings on this kernel and cannot serve as a clean baseline.
