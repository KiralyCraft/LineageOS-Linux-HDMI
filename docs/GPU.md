# GPU acceleration on the chroot displays

This note records results from the Xperia 1 V (`pdx234`, Adreno 740) through
2026-09-03. It distinguishes the Termux:X11 display from the Xorg server that
owns the external display through a DRM lease. They use the same chroot Mesa
installation but expose different rendering interfaces to clients.

## Installed GPU paths

Android exposes all of the relevant nodes to the chroot:

```text
/dev/kgsl-3d0
/dev/dri/card0       (msm_drm)
/dev/dri/renderD128
```

The chroot has Mesa 26.2.0, the DRI loader shim, the Turnip Vulkan ICD, and the
Freedreno Xorg DDX. A headless `vulkaninfo --summary` sees:

```text
deviceName = Turnip Adreno (TM) 740
driverName = turnip Mesa driver
```

The interactive `kiraly` shell sets these in `.bashrc`:

```sh
export MESA_LOADER_DRIVER_OVERRIDE=kgsl
export TU_DEBUG=noconform
```

They are below the interactive-shell guard in `.bashrc`. A non-interactive
`runuser -l kiraly -c ...` does not receive them. A normal interactive login
does.

## Live result matrix

| Display and environment | Renderer | Accelerated | Result |
| --- | --- | --- | --- |
| Termux:X11 `:0`, interactive `kiraly` login | native Freedreno `FD740` | yes | Works |
| Leased Xorg `:1`, default LXDE environment | Mesa llvmpipe | no | Works, but CPU-rendered |
| Leased Xorg `:1`, `GALLIUM_DRIVER=zink` | Zink over Turnip Adreno 740 | context/commands use the GPU | Fails to present: the application window stays black |
| Leased Xorg `:1`, interactive-login `MESA_LOADER_DRIVER_OVERRIDE=kgsl` | none | no | Loader cannot retrieve the device; the client disconnects |
| Leased Xorg `:1`, modesetting glamor plus native KGSL, without the allocation bridge | native Freedreno `FD740` | yes | DRI3 works, but Qualcomm KMS rejects the scanout framebuffer; HDMI stays black |
| Leased Xorg `:1`, adaptive bridge (default) | native Freedreno `FD740` | yes | Visible LXDE and `glxgears`; uncapped 300x300 `glxgears` runs at GPU speed while the newest completed image is independently paced to the display |
| Leased Xorg `:1`, direct renderonly client buffers | native Freedreno `FD740` | rendering only | GL readback is correct but Xorg sees a black client window |
| Leased Xorg `:1`, integrated shadow-present candidate | native Freedreno `FD740` | ARM build passes | Private tiled/UBWC render image plus same-context GPU resolve to a persistent KMS buffer; live validation remains required |

The software `glxgears` run measured approximately 40 and 22 FPS. The Zink
over Turnip run reported approximately 420-500 FPS, but those swap/FPS reports
are misleading: root-window XWD captures showed a completely black client
area while the surrounding LXDE desktop and window decorations remained
visible. The same capture procedure showed colored gears with llvmpipe. Zink
therefore proves GPU context creation and command submission here, not working
presentation to the Xorg drawable.

The exact native-Freedreno login test succeeded on Termux:X11 `:0`:

```text
OpenGL vendor string: freedreno
OpenGL renderer string: FD740
Accelerated: yes
```

The same login environment against leased Xorg `:1` reported:

```text
MESA-LOADER: failed to retrieve device information
XIO: fatal IO error ... on X server ":1"
```

Only the GL client disconnected. Xorg and the takeover remained healthy.

## Why the leased display differs

The known-safe takeover deliberately configures Xorg with:

```text
Driver "modesetting"
Option "AccelMethod" "none"
Option "PageFlip" "false"
Option "ShadowFB" "true"
Option "Atomic" "true"
Option "SWcursor" "true"
```

The Xorg log confirms that glamor is disabled, the screen is not DRI2-capable,
and GLX initializes the `DRISWRAST` provider. The DRI3 extension exists at the
server level, but this screen does not provide the screen-level DRI3 interface
needed by the KGSL loader. This is why copying the interactive-login KGSL
override to `:1` does not reproduce Termux:X11's native Freedreno path.

Setting only `GALLIUM_DRIVER=zink` makes GLX identify the renderer as Zink over
Turnip/KGSL and execute GPU work, but it does not make the result visible on
this ShadowFB screen. `LIBGL_KOPPER_DRI2=true` confirms the missing interface:
Mesa reports that DRI3 is required for presentation and then fails to create a
swapchain. `LIBGL_KOPPER_DISABLE=true` still leaves the client area black. Do
not add `MESA_LOADER_DRIVER_OVERRIDE=kgsl` to the `safe` ShadowFB mode: that
override is valid only with the matched accelerated Xorg/renderonly stack.

The installed Mesa source is pinned by the optional
[`third_party/mesa-for-android-container`](../third_party/mesa-for-android-container)
submodule at commit `64b6af2f` on the
[`fix/kgsl-leased-screen`](https://github.com/KiralyCraft/mesa-for-android-container/tree/fix/kgsl-leased-screen)
branch. Its relevant custom commits are:

```text
91f7e8c6 freedreno/kgsl: retain merged submits through GPU command ioctl
a3eb373e dri3: bridge native render fences to Present
89da2771 freedreno/kgsl: bridge KMS scanout and X11 presentation
2788d8df dri3: pipeline the KGSL X11 SHM bridge
4c72c7a4 dri3: decouple KGSL rendering from SHM presentation
3ce48e02 dri3: wait on KGSL bridge events without polling
6c30ef8c freedreno/kgsl: scope translated handles to KMS scanout
1200a245 dri3: add adaptive GPU bridge for UHD X11
f897e810 dri3: use renderonly PRIME for leased KGSL displays
f4cb22e8 loader: retain the DRI3 fd for KGSL renderonly
d2c5ddbd freedreno/kgsl: recognize downstream msm_drm KMS
295495c7 dri3: select KGSL fd for bridge clients
7d248aaf freedreno/kgsl: separate render and KMS fds
64b6af2f dri3: add KGSL shadow-present buffers
```

The separate `fix/kgsl-present-wait-fence` line exports a native Freedreno
render fence and attaches it as an X Present wait fence through Mesa's DRI3
loader and GLX/EGL paths. Termux:X11 commit `fc534e514d7a` fixes the matching
server-side Present callback lifetime. The leased-screen SHM bridge does not
give a native wait fence to Xorg: its worker waits the KGSL fence before it
maps the selected client image and submits an already-complete SHM pixmap.
That server-side wait-fence bug is therefore not on this bridge's hot path.

In the safe ShadowFB configuration the X server's 2D rendering remains
software-based. The old adaptive bridge below is the verified
native-Freedreno baseline; the integrated shadow-present path is the current
opt-in performance candidate.

## Native KGSL glamor diagnostic

A bounded Xorg-only probe on 2026-09-02 replaced the safe screen options with:

```text
Option "AccelMethod" "glamor"
Option "ShadowFB" "false"
Option "PageFlip" "false"
Option "Atomic" "true"
```

Both Xorg and the test client received:

```sh
MESA_LOADER_DRIVER_OVERRIDE=kgsl
FD_FORCE_KGSL=1
FD_KGSL_ENABLE_DMABUF=1
```

This successfully reached the intended rendering stack. Xorg reported:

```text
glamor X acceleration enabled on FD740
glamor initialized
[DRI2] DRI driver: kgsl
Initializing extension Present
Initializing extension DRI3
AIGLX: Loaded and initialized kgsl
```

`glxinfo -B` used DRI3, reported direct rendering, native Freedreno `FD740`,
and `Accelerated: yes`. Native-KGSL `glxgears` ran at approximately 57 FPS.

The result still could not be scanned out. Xorg repeatedly logged:

```text
failed to add fb -6
modeset(0): failed to set mode: No such device or address
```

An XWD root capture taken while `glxgears` was running was entirely black.
The important boundary is therefore below DRI3/Present: downstream Qualcomm
SDE KMS does not accept the KGSL/dma-heap buffer that stock glamor tries to
register as its scanout framebuffer. Disabling page flips does not avoid that
initial framebuffer registration. The phone remained stable and the normal
timeout restored Android.

## Renderonly and integrated shadow presentation

The bridge proved that KMS-owned buffers are accepted by both KGSL and SDE.
Mesa's direct renderonly experiment then allocated those buffers through the
ordinary DRI3 screen and rendered into them in the application context:

```text
Xorg DRI3 opens /dev/dri/renderD128 for the client
  -> Freedreno retains it as an explicit KMS control/cache fd
  -> Freedreno opens /dev/kgsl-3d0 for allocation and submission
  -> renderonly allocates a linear KMS image and imports it into KGSL
  -> the client renders directly and exports the image plus native fence
```

The fd ownership is now unambiguous: `fd_device_fd()` remains the KGSL
submission fd, while the screen cache and renderonly use a separate control-fd
accessor. This prevents DRM synchronization, reset, and performance calls from
accidentally targeting the display fd.

The direct client path is not usable on this device. A live solid-color probe
and `glxgears` rendered correct pixels when read back in the application, but
Xorg received black contents despite the explicit Present wait fence. Direct
mode remains available only to reproduce that boundary.

The opt-in shadow path keeps the standard DRI3/Present lifecycle while
separating the two conflicting layouts:

```text
application renders into private tiled/UBWC KGSL image
  -> same application context resolves into persistent linear renderonly image
  -> one native fence covers rendering and resolve
  -> X Present receives only the linear image and wait fence
  -> Present Idle releases the buffer for reuse
```

Only Mesa-created window back buffers use this path. Imported pixmaps,
texture-from-pixmap, fake front buffers, and externally owned images keep their
existing behavior. Allocation or native-fence failure is reported instead of
silently falling back to the known-black direct path. There is no polling,
timer, private X connection, per-frame allocation, CPU readback, or global
`notile`/`noubwc` override in shadow mode. The first implementation uses a
full-surface resolve; damage-aware resolves require proven buffer-age handling
and are deferred.

Three Xorg 21.1.24 backports are required and are stored under
[`patches/xserver`](../patches/xserver):

- glamor chooses the DRM render node for DRI3 clients, matching current
  upstream Xorg, rather than reopening the primary node and attempting legacy
  DRM authentication through a lease;
- the Present wait-fence callback is disarmed before re-execution, matching the
  lifetime fix already used by Termux:X11;
- upstream modesetting TearFree and its complete follow-up correctness series
  coalesce root damage into alternating scanout buffers and flip at vblank.

The similarly numbered patch under `patches/xserver/optional/` is an
experiment, not a required backport. It must be paired with the optional
Qualcomm composer cursor patch and explicitly enabled with
`run-agent.sh --overlay-cursor`.

## Cursor-motion presentation bottleneck

A deterministic XTest probe separated raw input, window-manager behavior, and
cursor composition. Under the original full-session trace, the adaptive GPU
bridge with a visible Xorg software cursor held about 58 FPS while stationary,
but 120 Hz cursor motion reduced it to 32-35 FPS. The same motion with the
cursor hidden recovered to 58-59 FPS, while injection submit time remained
below 3.1 ms. This established that the cursor-dependent Xorg path triggers the
slowdown, but the later startup-only trace test below supersedes those client
FPS numbers.

The downstream SM8550 SDE driver registers Primary and Overlay planes only and
does not implement legacy `cursor_set`, `cursor_set2`, or `cursor_move` CRTC
callbacks. Candidate 14 tested an opt-in cursor on a separately leased overlay.
It rendered correctly, yet 120 Hz pointer motion reduced `glxgears` further to
27-31 FPS. A nominal 15-second cursor trace took 36.9 seconds to submit. The
2.85-second outlier was later localized to client-side `XFlush` backpressure,
not one kernel `drmModeSetPlane` call; direct syscall timings were generally a
few milliseconds. A distinct plane therefore does not by itself fix the
cursor-motion presentation problem.

The default is again `SWcursor true`, with no cursor overlay in the composer
lease. The paired patches remain under `optional/` and `--overlay-cursor` exists
only for diagnosis. Nonblocking atomic plane updates would normally avoid
waiting in a legacy plane ioctl, but this downstream kernel rejects Xorg's
atomic-client capability request, so forcing `Atomic true` is not a validated
solution on this device.

The exact Xorg and kernel sources expose the remaining mechanism. Modesetting
probes `drmModeDirtyFB()` at screen initialization and, when supported,
registers a Damage object on the root pixmap. Every accumulated root damage is
then sent through `DIRTYFB`. A visible software cursor also makes
`ms_present_check_flip()` return false through `sprites_visible`, converting a
fullscreen Present flip into a copy to the root pixmap. Sony's `msm_fb.c`
assigns `drm_atomic_helper_dirtyfb` to the framebuffer dirty callback, and the
exact Lineage 5.15 helper is explicitly blocking and finishes with a
synchronous atomic commit. Cursor save/restore damage therefore competes with
the application's presentation and rate-limits the X server.

Termux:X11 is a useful architectural comparison rather than a drop-in patch.
Its cursor sprite callbacks update separate shared cursor state and wake its
renderer; the renderer samples the root buffer, blends the cursor texture, and
swaps the combined result. That keeps cursor motion out of Xorg root damage and
ties it to one output render cadence. Reproducing that property for leased KMS
requires a real output compositor or an equivalent coalesced host-owned cursor
commit. Simply suppressing `DIRTYFB`, allowing flips beneath an uncomposited
cursor, or adding a timer would trade correctness for benchmark numbers.

The startup tracer was also a separate multiplier. Full tracing performs two
agent/broker round trips and two durable log syncs around every DRM ioctl. A
same-session A/B changed uncapped `glxgears` from roughly 52-79 FPS to
503-609 FPS when that relay was disabled, but did not make the synchronous
overlay cursor smooth. The default is therefore `--drm-trace startup`: trace
fail-closed until scanout is verified, then bypass routine steady-state ioctls
while retaining compatibility shims and tracing every later `SETCRTC`.
`--drm-trace full` remains a diagnostic mode whose timing results are invalid.

Candidate 15 then repeated the controlled fullscreen test with the exact
agent-provided FD740 bridge environment and a continuously open 60 FPS capture
path. Hidden-cursor pointer motion produced 59.994 client FPS and 60.034 active
HDMI updates per second with zero skips, discontinuities, or top/bottom counter
disagreement. Making the cursor visible did not reduce average client
throughput (60.055 FPS), but p99 client frame time rose from 18.402 to
33.382 ms. HDMI showed 56.637 active updates per second, 114 skipped source
frames, 16 discontinuities, 36 ms p99 holds, and 1,135 torn updates out of
1,135 decoded updates. This replaces the earlier full-trace FPS number as the
performance baseline: cursor visibility primarily breaks flip eligibility and
scanout cadence, while full tracing had additionally throttled the client.

## Upstream TearFree correction

Candidate 16 backports Xorg's official modesetting TearFree implementation and
its correctness follow-ups to the pinned 21.1.24 server. TearFree allocates two
additional scanout buffers per CRTC, copies accumulated root damage into the
next buffer, and submits one vblank-paced flip. This is the upstream mechanism
for presenting a composited root pixmap coherently when a visible software
cursor makes a client's direct Present flip ineligible. It does not suppress
`DIRTYFB`, fabricate Present completion, weaken a fence, or introduce a timer.

The complete backport includes the public pixmap/rotation helpers, generic
shadow and flip queues, Present's TearFree awareness, vblank coalescing and
ordering, accurate Present completion timing, error cleanup, unflip before
modeset, and default enablement. The exact leased Qualcomm DRM device accepted
the universal-planes capability, and Xorg logged `TearFree: enabled` while
Glamor and `PageFlip` remained enabled.

The 1280x720@60 live A/B used the same FD740 bridge, fullscreen Gray-code probe,
120 Hz deterministic pointer path, and continuously open 60 FPS capture stream
as the candidate-15 baseline. The hidden-cursor control produced 59.992 client
FPS and 60.022 active capture updates/s, with no duplicates, skips,
discontinuities, or torn frames. With the software cursor visible, the client
produced 59.968 FPS, 17.875 ms swap-time p99, and 18.486 ms frame-time p99. The
capture produced 59.926 active updates/s, two duplicate frames, no source
skips, no discontinuities, and no torn frames. Cursor submission remained
nonblocking at 1.860 ms worst case.

This fixes the measured presentation-cadence problem without changing the
private Mesa bridge. The optional overlay-plane cursor remains a diagnostic
only; it is neither enabled nor required by TearFree.

Bridge clients ask `x11_dri3_open()` for KGSL because their completed images
are copied independently. Shadow and direct clients retain Xorg's DRI3 DRM fd,
enable `FD_KGSL_RENDERONLY=1`, and open KGSL internally for rendering. The
Freedreno screen uses Mesa's `struct renderonly` and
`renderonly_create_kms_dumb_buffer_for_resource()` for the exported image.

The exact 1280x720 KMS allocation probe passed with pitch 5120, dma-buf export,
and KGSL import. The matched ABI-5 Mesa DRI target, GLX frontend, and EGL
frontend compile successfully for AArch64. Live visibility, workload
performance, long-running stability, and unplug/replug restoration remain the
acceptance gates for shadow mode; the adaptive bridge stays the default.

The first live cutover exposed one downstream-kernel naming difference before
scanout: `drmGetVersion()` reports `msm_drm` on this pdx234 kernel, whereas
mainline MSM reports `msm`. The initial renderonly gate accepted only the
mainline name, so it left renderonly disabled and KMS correctly rejected the
unrelated KGSL handle with `ENXIO` at `MODE_ADDFB2`. Commit `d2c5ddbd` accepts
both kernel names. The watchdog restored Android normally; no invalid
framebuffer reached `SETCRTC`. The corrected build reached direct-client live
testing and established the black cross-context result described above without
destabilizing Xorg.

### Tiling, UBWC, and linear scanout

Do not set `FD_MESA_DEBUG=notile` in the normal takeover environment. In
Freedreno it disables tiling for every internal buffer. The verified adaptive
bridge sets `FD_MESA_DEBUG=noubwc` only because its small-surface CPU readback
must map completed images reliably. Shadow and direct modes deliberately unset
that override, so the private shadow render target can retain tiling and UBWC.
The `kiraly` login environment does not restore either override. Mesa's
[Freedreno documentation](https://docs.mesa3d.org/drivers/freedreno.html)
describes Adreno as primarily a tile-mode renderer and treats layout overrides
as diagnostic controls.

A native Freedreno `FD740` surfaceless check on 2026-09-03 used Mesa's
`peglgears`, modified only to accept a pbuffer size. `FD_MESA_DEBUG=layout`
reported the default 1920x1080 RGB565 target as stride 3840, aligned height
1088, and UBWC. Adding `notile` changed the same target to aligned height 1080
and linear. The flag therefore has the intended layout effect on this device;
it is not ignored.

Interleaved five-second samples gave these averages:

| Pbuffer | Default | `notile` | Difference |
| --- | ---: | ---: | ---: |
| 1920x1080 | 17,148 FPS | 17,227 FPS | +0.46% |
| 3840x2160 | 5,290 FPS | 5,280 FPS | -0.18% |

Those differences are smaller than the run-to-run spread. Gears is a simple
submission-heavy workload, so this establishes that globally disabling tiling
has no measurable benefit here; it does not establish the cost for a textured,
bandwidth-heavy application. It should remain a diagnostic flag, not a
performance default. The earlier 1280x720 `glmark2 build` result is more useful
for that boundary: rendering directly into Xorg-owned linear KMS-dumb buffers
scored 688 FPS versus 764 FPS with a tiled render target and presentation copy,
about 10% slower.

There are two separate layout decisions in the shadow-present candidate:

- ordinary private textures and render targets retain Freedreno's default
  tiling and UBWC choices;
- a buffer that must be scanned out is allocated by KMS as a dumb buffer and
  is necessarily linear, independently of `FD_MESA_DEBUG`.

The second point is the likely remaining performance constraint at high
resolution. It cannot be fixed by enabling or disabling `notile`. The shadow
candidate now implements the appropriate Mesa-side design: a tiled GPU render
resource plus a same-context GPU resolve into its linear renderonly scanout
resource immediately before the fence-producing flush. Mesa's
[renderonly contract](https://gitlab.freedesktop.org/mesa/mesa/-/blob/main/src/gallium/auxiliary/renderonly/renderonly.h)
explicitly provides for this kind of resolve. It avoids the old CPU/SHM path,
keeps synchronization inside the graphics stack, and does not require a
polling or timer bridge. An upstream
[MSM/KMS discussion](https://lore.gitlab.freedesktop.org/drm-ai-reviews/review-overall-20260304-msm-restore-ioctls-v1-1-b28f9231fcd2%40oss.qualcomm.com/T/)
also recommends allocating from the render GPU and importing into KMS instead
of rendering into a dumb buffer. That would be preferable here, but this
downstream SDE stack has already rejected ordinary KGSL/dma-heap client
allocations as framebuffers.

The tiling matrix itself produced no new KGSL fault, hang, timeout, or reset.
Three `CP: AHB bus error` messages at uptime 28103 predated it and correlate
with a separate test that loaded the candidate client libraries against
Termux:X11; the same instant also logged SELinux denials for Termux:X11
`xshmfence` memfds. That test did not request `notile` or `noubwc`. Xorg and
SurfaceFlinger survived, but candidate libraries should remain paired with the
private HDMI Xorg rather than injected into the running Termux:X11 server for
further measurements.

## Previous adaptive allocation and presentation bridge

The bridge was implemented and tested live on 2026-09-02 and 2026-09-03. That
Mesa implementation ended at commit `1200a245` on
[`fix/kgsl-leased-screen`](https://github.com/KiralyCraft/mesa-for-android-container/tree/fix/kgsl-leased-screen),
directly based on `91f7e8c6`. The `fix/kgsl-present-wait-fence` branch ends at
that base and intentionally does not contain the KMS/X11 bridge.

The KMS capability probe in `native/probes/kms-kgsl-zero-copy.c` established
this supported allocation chain:

```text
SDE KMS dumb allocation
  -> PRIME dma-buf export
  -> KGSL import
  -> SDE KMS framebuffer
```

It added an explicit `FD_KGSL_USE_KMS_DUMB=1` allocation mode. It created the
shared storage with KMS, imported the dma-buf into KGSL for rendering, and
translated the KGSL BO back to a GEM handle on Mesa's KMS control fd when GBM
asks for the scanout handle. The agent set this mode only in Xorg. This made
the glamor-rendered root desktop visible with no `failed to add fb` errors, so
Xorg's final scanout remains zero-copy.

The GEM-handle translation is likewise gated by `FD_KGSL_USE_KMS_DUMB`.
Ordinary KGSL clients retain the established `fd_bo_handle()` allocation-id
behavior; merely having the KMS callback installed must not turn a native,
non-exportable KGSL BO into handle zero.

An ordinary client DRI3 pixmap was still black. This was tested with normal
dma-heap and KMS-dumb client allocations, DRI2 fallback, `glFinish`, and forced
linear/no-UBWC layouts. The diagnostic client read back the expected
`255,0,26,255` pixel locally in every case, while Xorg saw black. The same
window was visible with llvmpipe. The remaining fault is therefore the
cross-context KGSL dma-buf consumption on this downstream stack, not rendering,
mode setting, or the Present completion fence.

The chroot's system Xorg 21.1.24 retained the repeated Present wait-fence
callback bug fixed in
[`fc534e51`](https://github.com/KiralyCraft/termux-x11/commit/fc534e514d7ad4851d0aca3357495816260e4549).
The SHM bridge does not expose its native KGSL fence to Xorg: its worker waits
locally and submits only a completed SHM pixmap. Therefore that Xorg bug is not
on that bridge path. The current private Xorg applies the same fix before using
the standard DRI3 wait-fence route.

For that boundary, Mesa provides the opt-in
`MESA_KGSL_X11_SHM_BRIDGE=1` fallback. KGSL still renders the application.
Commit `4c72c7a4` moves readback and X submission to a persistent worker. Each
producer swap exports the native KGSL fence, then an interval-zero producer
drops superseded frames before mapping them. The worker waits the selected
fence, copies only that completed image into one of three shared pixmaps, and
submits it with X Present. Present Complete and Idle events release each slot;
there is no sleep, polling timer, or guessed buffer lifetime. Synchronized
swaps fill the three Present slots with distinct future-MSC requests and are
backpressured only when all slots are busy. The initial Present establishes the
server MSC, and subsequent requests retain one refresh of scheduling lead so a
full-surface resolve does not race the immediately following vblank. Uncapped
rendering rotates through four KGSL back buffers and keeps running independently
of the display refresh.

EGL damage rectangles are currently treated conservatively and GLX swaps copy
the full drawable. Set `MESA_KGSL_X11_BRIDGE_STATS=1` for periodic produced,
presented, dropped, server-skipped, flip/copy/suboptimal-mode, and byte
statistics. The agent uses
`FD_MESA_DEBUG=noubwc`: UBWC must remain disabled for reliable CPU-visible
readback, but ordinary Freedreno tiling remains enabled.

The agent also enables `MESA_KGSL_X11_GPU_BRIDGE=1`. Mesa now attempts the GPU
path at every drawable size. Xorg allocates three KMS-compatible presentation
pixmaps, exports them through DRI3, and the worker imports them into KGSL. The
application continues rendering into fast tiled client buffers; only a
selected completed frame is GPU-blitted into an Xorg-owned display slot. A
synchronized fullscreen Present leaves that pixmap flip-eligible; interval zero
adds only the standard Present `ASYNC` option. This removes CPU readback and the
MIT-SHM server copy without returning to the broken direction where Xorg
imports an ordinary client KGSL allocation. If allocation or import fails,
Mesa falls back to forced-copy MIT-SHM. A nonzero
`MESA_KGSL_X11_GPU_BRIDGE_MIN_PIXELS` remains available for diagnostic A/B
tests, but is no longer the default because small forced-copy surfaces cannot
provide tear-free 60 Hz presentation.

This client presentation step is not zero-copy. It is narrower than ShadowFB:
there is no continuous full-screen copy, and non-GL desktop content stays on
Xorg's GPU-backed root. Only an accelerated drawable is copied when that
client swaps. The initial live result was:

```text
OpenGL vendor:   freedreno
OpenGL renderer: FD740
Resolution:      1920x1080
glxgears:        55.233 and 57.364 FPS over two five-second samples
```

Both the solid-color GL probe and the gears were visible in root-window XWD
captures. Three bounded takeover runs restored Android normally, and the
device did not reboot or crash.

### Measured presentation cadence

The deterministic fullscreen SDL probe and a continuously open MacroSilicon
MJPEG capture established the presentation bottleneck directly at 1280x720 at
60 Hz.
The card remained configured for 60 FPS throughout. A controlled 60-Hz source
produced more than 50 distinct captured images per second even while the old X
Present path skipped requests, disproving the tentative assumption that the
card could capture only 30 unique frames per second.

The old synchronized bridge waited for each frame's CompleteNotify before the
application could begin its next frame. It produced 30.0 FPS and twice stopped
permanently waiting for a Present event. Moving the wait to the next swap still
produced 30.0 FPS: beginning a full-surface copy only after CompleteNotify was
too late for `complete_msc + 1` on this stack. Queueing three future-MSC slots
removed that serialization and completed a 70-second run, but a forced Present
copy still updated the scanout mid-frame.

With Xorg-owned GPU slots made flip-eligible, the same 50-second run measured:

```text
Mesa:    2985 frames / 50.003 s = 59.697 FPS
         p95 frame time 17.237 ms; p99 17.954 ms
Present: all reported completions were flips; 0 copies; 0 skips
Capture: 59.695 unique updates/s; 0 source skips; 0 torn frames
         median hold 16 ms; p95/p99 20 ms
```

One 164-174 ms outlier appeared in both the client and capture streams, but it
did not start a persistent stall and did not affect the p99. The accepted
mechanism is therefore Present flip/idle backpressure over persistent
Xorg-owned buffers, not software pacing or an elapsed-time recovery rule.

The asynchronous worker changes what the `glxgears` number means. A 300x300
swap-interval-zero client no longer blocks at about 60 FPS: live samples ranged
from roughly 1.1k to 2.0k FPS depending on device state, versus roughly 1.4k to
2.4k FPS on Termux:X11 in the corresponding runs. The remaining bridge cost on
this microbenchmark was about 7-22%, with GPU DVFS and device state producing
substantial run-to-run variation. The external screen still receives only the
newest completed image at its real refresh rate. Experiments that omitted the
per-swap native fence, used an unexported internal fence, or deferred the flush
reduced throughput or let the KGSL queue run far ahead; the explicit
exported-fence lifecycle is retained. In a same-state 2026-09-03 A/B,
pre-dropping swaps before fence export reduced leased-Xorg `glxgears` from
1165-1200 FPS to 960-1125 FPS, depending on the replacement flush path.

### Workload and resolution cost

A follow-up comparison on 2026-09-03 used `glmark2` because a fast, small
`glxgears` window deliberately drops almost every swap before the bridge maps
it. This can hide the cost that matters to a full-screen application. The same
private Mesa build, immediate swap mode, 24-bit depth visual, and drawable size
were used on Termux:X11 and leased Xorg:

| Drawable and scene | Termux:X11 | Leased Xorg bridge | Reported process CPU busy |
| --- | ---: | ---: | ---: |
| 1280x720 `bump:high-poly` | 752 FPS | 638-639 FPS | 20% vs 25% |
| 1280x720 `build` | 848 FPS | 772-859 FPS | 20% vs 26-28% |
| 3840x2160 `build` | 217 FPS | 230 FPS | 14% vs 24% |

The 1280x720 bridge copied approximately 150-200 MiB/s. The 4K bridge run
copied about 11.1 GiB during the eight-second scene, or roughly 1.5 GiB/s,
while presenting only the newest completed surfaces. The application FPS did
not collapse because the fence wait, readback, memory copy, and X request are
on the persistent worker rather than the application's main thread. The extra
CPU work and memory/cache traffic remain real, however, and scale directly
with drawable area. This is a credible contributor to the observed 4K cursor
and window stutter even when an uncapped FPS counter remains high.

The 4K number above used an oversized drawable clipped by the existing
1280x720 X screen. It prices 4K rendering and the bridge copy without risking a
live HDMI mode change; it does not by itself validate physical 4K scanout.
The worker retains row-sized copies: replacing them with one contiguous 4K
`memcpy` reduced the otherwise identical `build` result from 227 to 193 FPS.
Although reported CPU busy fell from 26% to 23%, the larger burst increased
contention with the GPU enough to lose 15% of application throughput.
The real application mentioned in the
[upstream discussion](https://github.com/lfdevs/mesa-for-android-container/pull/96#issuecomment-5507168374)
was an offer from another tester, not a downloadable test artifact, so
`glmark2` is the reproducible local substitute. That tester later measured the
wait-fence patch at a 48-61% loss in the `glmark2` scenes but within the normal
0.70 FPS run-to-run spread in the CPU-heavy 2005 game. This is why the bridge
decision uses both pixel-copy measurements and a workload matrix rather than a
small `glxgears` number alone.

The GPU destination experiment produced a clear size-dependent crossover on
the same live session. These are paired eight-second `build` samples unless a
different scene is named:

| Drawable | CPU/MIT-SHM | Xorg-owned GPU slots | Result |
| --- | ---: | ---: | --- |
| 1280x720 `bump:high-poly` | 633 FPS | 599 FPS | CPU 5% faster |
| 1920x1080 `build` | 510 FPS | 465 FPS | CPU 9% faster |
| 2560x1440 `build` | 345 FPS | 299 FPS | CPU 13% faster |
| 3200x1800 `build` | 249 FPS | 242 FPS | approximately even |
| 3840x2160 `build` | 224-231 FPS | 252-269 FPS | GPU 12-16% faster |

A direct zero-copy variant in which applications rendered into Xorg-owned
KMS-dumb buffers was visibly correct, but scored 688 versus 764 FPS in the
1280x720 `build` scene. Those linear scanout buffers are poor render targets,
so the retained path uses them only as presentation slots. A dma-heap GPU slot
behind MIT-SHM scored 209 versus 224 FPS at 4K because it still required Xorg's
SHM copy. The retained Xorg-owned GPU slots are the first variant that improved
the resolution-sensitive 4K case. Although the CPU path won some uncapped small
window microbenchmarks, the capture-backed cadence test showed that it cannot
be the default: forced copies tore under synchronized 60 Hz presentation.

### Private Mesa ABI set

`struct loader_dri3_drawable` is allocated by the GLX/EGL frontend and used by
the DRI target. The bridge adds fields to that structure, so copying only a new
`libgallium` beside the system `libGLX_mesa` is not safe. That mixed deployment
caused intermittent startup crashes in `driQueryOptionb`—up to 7 crashes in 40
short launches—because the newer DRI code wrote beyond the older frontend's
allocation. Two matched-build stress runs each completed 80 of 80 launches
without a crash.

The runtime therefore requires this complete set from one Mesa build:

```text
lib/mesa/libgallium-<matching-version>.so
lib/mesa/libGLX_mesa.so.0
lib/mesa/libEGL_mesa.so.0
lib/mesa/libdril_dri.so
lib/mesa/kgsl_dri.so -> libdril_dri.so
lib/mesa/libgbm.so.1
lib/mesa/gbm/dri_gbm.so
```

The frontend/Gallium trio carries `HDMI_LOS_MESA_BRIDGE_ABI=5`; the DRIL target
exports `__driDriverGetExtensions_kgsl`; and the GBM backend has a direct
dependency on the same uniquely named Gallium build. `run-agent.sh` checks
these contracts
before starting accelerated mode and refuses a missing, stale, or mixed set.
The agent sets Mesa's standard `LIBGL_DRIVERS_PATH` and `GBM_BACKENDS_PATH` to
the private directories. The private `libgbm.so.1` is selected through
`LD_LIBRARY_PATH`, preventing either the KGSL driver or the dynamically loaded
GBM backend from resolving to the chroot's system Mesa. Candidate 14 first
exposed the missing DRIL target; after adding it, the core showed that system
`dri_gbm.so` still loaded system Gallium alongside the private Gallium. That
mixed process crashed in Mesa state-tracker context creation before Xorg could
initialize the display.
Accelerated mode additionally requires the matched private Xorg at
`libexec/Xorg` and its glamor module at
`lib/xorg/modules/libglamoregl.so`. The system Xorg remains available to the
`safe` diagnostic mode.

## External display mode

The broker selects a mode before HDMI connection; 1080p60 is the default, with
native/automatic and 4K60 as experimental choices. It journals the prior
Android preference and applies the choice through `cmd display`. Once Android
has connected and stabilized at that mode, the agent reads the leased CRTC's
active timing and verifies that the connector still advertises it. It writes
that exact timing to Xorg as `hdmi-los-android-current`.

This matters when the monitor and Android support more than one timing. If the
Xorg `PreferredMode` and `Modes` entries force a different resolution while a
Qualcomm connector topology property still describes Android's active mode,
the downstream KMS driver can reject `MODE_SETCRTC` with `EINVAL`, leaving the
external display black. Omitting both entries would avoid the hard-coded mode,
but would let Xorg independently choose from EDID instead of reliably keeping
Android's current resolution and refresh timing. Failure to read the exact
active mode now aborts takeover and restores Android.

RandR dimensions alone are no longer accepted as readiness. The agent
correlates the traced enabling `SETCRTC`, keeps a duplicate lease fd, and uses
`GETCRTC` to prove that the expected CRTC scans out Xorg's framebuffer at the
same full timing. The installed Sony kernel rejects Xorg's atomic-client
capability, so the operational path is legacy. If an exact same-mode legacy
`SETCRTC` alone returns `EINVAL`, the tracer may substitute a page flip, but
only after matching connector, CRTC, coordinates, and every timing field; the
same `GETCRTC` proof still applies.

The first mode-safe 1080p validation showed why retaining the old scanout is
required: powering the pluggable display off during lease acquisition left the
CRTC with the correct mode but `fb_id=0`, so the guarded page flip correctly
refused to run. Release `0.2.8-candidate.4` instead pauses HWC updates while
leaving Android's last committed external framebuffer active. Abort paths
resume updates directly. After a completed lease it revokes and resets the DRM
lease, then explicitly transitions Qualcomm HWC Off and On before refreshing
SurfaceFlinger. This prevents the raw CRTC reset from being followed by a
same-state no-op resume and an unsignaled external-display retire fence.

The first live test of this behavior inherited the Dell P2723QE's Android mode
of 3840x2160 at 60 Hz. Xorg generated a 533.250 MHz Modeline, RandR reported
3840x2160 as current, and the traced `MODE_SETCRTC` returned success. The
physical display initially showed mostly the blue desktop background with only
part of the session apparent, then the complete LXDE desktop became visible.
An XWD root capture during the run contained the full 3840x2160 wallpaper,
icons, and panel. This confirms correct mode inheritance while leaving 4K
first-paint latency and accelerated performance to be measured separately.

## Reproducing the checks

For Termux:X11, run from an interactive `kiraly` shell:

```sh
DISPLAY=:0 glxinfo -B
DISPLAY=:0 glxgears -info
```

`Xvfb` can be used to compare headless client rendering when the HDMI lease is
unavailable, but it cannot validate this presentation path: without a real DRM
control fd it cannot exercise renderonly scanout allocation, DRI3 buffer
sharing, or Present fences. Treat a headless number only as a KGSL rendering
baseline, not as evidence that leased HDMI presentation works.

During a safe ShadowFB leased-Xorg session, this command verifies GPU context
creation and command execution, but its window is black because that mode has
no presentation bridge:

```sh
DISPLAY=:1 \
XAUTHORITY=/run/hdmi-los/Xauthority \
GALLIUM_DRIVER=zink \
glxinfo -B
```

To pin the tested Vulkan ICD explicitly for diagnosis:

```sh
DISPLAY=:1 \
XAUTHORITY=/run/hdmi-los/Xauthority \
GALLIUM_DRIVER=zink \
VK_DRIVER_FILES=/usr/share/vulkan/icd.d/freedreno_icd.aarch64.json \
glxgears -info
```

The current agent starts LXDE directly with `dbus-run-session`; it does not
start an interactive login shell. `kgsl-kms-bridge` supplies the KGSL and
presentation variables explicitly, so programs launched from that LXDE
session inherit the working configuration. The direct glamor experiment still
proves that merely enabling DRI3 is insufficient. Safe ShadowFB remains
available as an explicit fallback.

## Starting the takeover as `kiraly`

From a terminal inside the mounted chroot:

```sh
cd /home/kiraly/Downloads/hdmi-los-runtime
./run-agent.sh
```

`run-agent.sh` automatically re-executes itself through passwordless
`sudo -n`; it is expected to remain in the foreground. Leave that terminal
open, then tap the `HDMI Xorg` Quick Settings tile on Android. Pressing
`Ctrl-C` stops the waiting chroot agent. The no-argument command defaults to
no phone-side capture, `kgsl-kms-bridge`, the verified adaptive client bridge,
LXDE, and a renewable continuous lease. The tile, volume-button escape, failed
heartbeat, disconnect, and Xorg exit still restore Android.

The default requires the matched private Xorg pair plus `libgallium-*.so`,
`libGLX_mesa.so.0`, and `libEGL_mesa.so.0` below `lib/mesa/`.
Its full equivalent command is:

```sh
cd /home/kiraly/Downloads/hdmi-los-runtime
./run-agent.sh --capture none --xorg-accel kgsl-kms-bridge \
  --client-present bridge --session lxde --no-timeout
```

The runner deliberately rejects this mode if a private Xorg component or Mesa
library is missing, or if Mesa carries the wrong bridge ABI. Use `--timeout` to
restore the bounded 60-second session deadline.
Use `--client-present shadow` to test the integrated tiled/UBWC-to-linear GPU
resolve. Use `--client-present direct` only to reproduce the known-black
direct renderonly path. Both require `--xorg-accel kgsl-kms-bridge`; the runner
rejects incompatible combinations rather than silently changing modes.
Use `--xorg-accel safe` for the software-rendered ShadowFB fallback; these can
be combined as `./run-agent.sh --xorg-accel safe --timeout` during staged
safety testing.

The `0.2.5-diagnostic.1` module disables ordinary tile activation. With that
older diagnostic package installed, start the foreground agent as above and
trigger the takeover separately from an Android root shell:

```sh
/data/adb/modules/hdmi-los/bin/hdmi-losd probe xorg-atomic
```

Release `0.2.8-candidate.4` makes the tile an arm/disarm control, defaults its
stored preset to 1080p60, waits for three stable composer mode samples, and uses
the legacy scanout path with strict trace-plus-`GETCRTC` verification. The
accelerated renewable session remains the no-argument launcher default.
