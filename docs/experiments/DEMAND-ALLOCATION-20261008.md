# Demand shared-image allocation and resize-background diagnosis, 2026-10-08

Mesa `eecab7336efc3093b3d6d90f1e2e9be9ff73d00a` passed the production cache
fixture with sanitizers and the matched build on `root@192.168.104.201`. The
4K30 device tests below use the deployed frame-timed Xorg 26 runtime and its
KGSL producer-ready allocation exports. The new Mesa is initially selected only
for owned test processes. No Magisk module or Android application was changed.
The accompanying JSON records exact build hashes and measurement summaries.

## Change and ownership

A new cached generation initializes one Xorg-owned shared destination rather
than three. An already initialized free image is always preferred. Only when
all initialized images are still owned does admission add another image, up to
three. Fullscreen replacement can therefore progress while the current scanout
remains pinned. Present COMPLETE and IDLE remain separate requirements for reuse.
Actual padded backing bytes, three generation entries, and the 512 MiB limit
still govern admission and eviction. Retirement remains outside the event lock.
`MESA_KGSL_HDMI_LAZY_SLOTS=0` is the eager comparison control; the new bundle
forwards `--lazy-slots 0|1` through sudo. No CPU render/readback fallback was added.

## Correctness and performance

The changing-pattern test passed 24/24 checks with 4x multisampling and interval
transitions. The large/fullscreen boundary test passed 12/12 with transitions.
Multiple points, distinct frame colors and logical extent were checked after
settling; this is not validation of seamless continuous resizing or optical output.

The same build ran eager, demand, demand, eager, with exact render dimensions,
capacity reuse enabled and identical 30 Hz output. Each run had one second fixed,
six seconds of unique resizes and three seconds fixed. The controller waits for
the WM's actual requested geometry before drawing. These are request/draw/swap
measurements, not displayed FPS. Refresh phase differed between starts.

| Run | Shared images allocated | Allocation calls | Allocation mean | Unique resize draw/swap p95 | Unique resize batches |
|---|---:|---:|---:|---:|---:|
| Eager 1 | 30 | 10 | 19.82 ms | 43.18 ms | 109 |
| Demand 1 | 14 | 14 | 10.02 ms | 24.42 ms | 153 |
| Demand 2 | 14 | 14 | 8.32 ms | 24.61 ms | 178 |
| Eager 2 | 36 | 12 | 21.04 ms | 45.53 ms | 123 |

The ordinary allocation work is reduced. Long tails remain: one eager run reached
836 ms, another 674 ms, and one demand run reached 878 ms; demand is not a cure
for those stalls. Fixed-phase swap blocking also varies with refresh phase.

The 1920x1080 ioQuake3 recorded demo completed normally. After excluding warmup,
1,386 Present completions had 1,385 adjacent MSC deltas of +1. Request-to-event
mean was 27.40 ms and p99 29.02 ms. The original game config/demo were preserved
and a temporary RAM profile was used.

A hidden-cursor full-4K gears run exercised actual Present flip completions: its
first 30-second run had one +8 gap. The previous callback arrived 239 ms after its
UST; this is notification/processing delay evidence, not proof of GPU execution
cost. A separate 40-second repeat had 1,067 measured completions, all 1,066 adjacent
MSCs +1, mean request-to-event 27.03 ms and p99 27.60 ms. Keep the first tail in the
record. This validates protected replacement progress, not universal no-stall output.

## What the blank resize samples represent

The new read-only helper opens its own DRM file, queries only external CRTC 235,
exports the committed linear XR24 framebuffer, maps it read-only, and snapshots
only the owned test window crop. It never reads Xorg's event queue or changes DRM
state. It closes every handle it creates. FB ID, MSC and window geometry are
checked before/after; phase alignment samples early after vblank. Software DRM
state may identify a pending committed framebuffer. This is not optical capture.
The samples also have a fixed 200 ms period and can alias resize/refresh phase.

On the same Mesa build, demand had 15 background-only samples among 48 early,
stable-geometry samples; eager had 21 among 50. Do not interpret these sparse,
phase-dependent counts as displayed blank-frame rates or an allocation cure.

A decisive diagnostic changed only the test window's X11 background to
RGB (36,186,113), leaving GL rendering and presentation unchanged. Eleven early,
stable-geometry samples then contained exactly that green over every sampled
client point, instead of black. No uniformly black samples remained in that
control. It establishes X11 resize/exposure background painting in those frames,
not a lost GL resolve or source/destination mismatch. The gears source selects
black background and leaves bit gravity at ForgetGravity. Pinned Xorg
`mi/miwindow.c` exposes the entire resized window unless bit gravity recovers it.

Optional resolve diagnostics matched both the drawable and bound render image.
A CPU-only preparation experiment (`1bdb10e7c`) did not remove the blank samples
and was reverted (`b3072a10f`). It is not part of the final fix. Binding diagnostics
remain optional and disabled in normal use.

The live Openbox advertises `_NET_WM_SYNC_REQUEST` and its counter, and links the
XSync alarm/counter APIs. The [standard resize synchronization protocol](https://specifications.freedesktop.org/wm/1.5/ar01s06.html)
coordinates a client's completed repaint with the next interactive WM resize.
[Openbox documents this integration](https://openbox.org/help/Upgrading_to_3.4).
The direct `XResizeWindow` stress controller bypasses interactive resize pacing,
and glxgears does not implement that protocol. The next investigation should
compare real WM-driven resizing and supporting clients before changing server
background semantics. Retained-window composition is a separate architectural
option that requires correct KGSL cross-process completion fences. No global
background-clearing change, forced bit gravity or GL-compositor install was made.

## Reproduction and remaining scope

Raw traces, own-window screenshots and private game profiles remain in RAM:
`/tmp/hdmi-app-pacing-20261008`, `/tmp/hdmi-resize-capacity-20261007`, and
`/tmp/hdmi-scanout-20261008`. The JSON includes hashes; raw data are deliberately
not copied to SD. The native capture helper was built only on the server:

```sh
gcc -shared -fPIC -std=c11 -O2 -g -Wall -Wextra -Werror \
  committed-framebuffer.c -o /build/committed-framebuffer.so \
  -I/usr/include/libdrm -ldrm
```

The source is `tests/graphics-contract/committed-framebuffer.c`. Copy its library
to RAM and set `HDMI_FRAMEBUFFER_HELPER`; the controller can select `--drm-captures`
and `--background-pixel 0x24ba71`. The Python helper defaults to the observed
30 Hz period; use `HDMI_FRAMEBUFFER_PERIOD_NS` for another measured mode. It
intentionally rejects unsupported framebuffer layouts and non-4K mode.

Active resizing, unexplained long tails, broad Firefox/Maps usability and physical
4K60 remain open. The safe performance improvement can be promoted independently;
the background experiment is diagnostic evidence, not a shipped visual workaround.
