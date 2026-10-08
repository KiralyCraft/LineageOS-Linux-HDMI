# EGL frontend flush integration, 2026-10-08

Mesa `a426a484523b6d8440adb3bbd1de8a4bd0e27502` corrects a confirmed missing
frontend boundary in the same-context HDMI resolve. GLX supplies drawable flush
flags to the common swap function. EGL supplies zero, and its ordinary frontend
adapter converts those to drawable plus ancillary flags. The integrated resolve
had bypassed that adapter and called the DRI callback flush directly. Its zero
flags rejected the callback; the pipeline then returned a zero SBC which EGL
misinterpreted as a successful swap.

Each frontend now has a callback-aware native-fence flush adapter. EGL uses its
normal drawable/ancillary flags, GLX preserves caller flags, and the shared
loader performs the existing after-drawable callback and fence export. Rendering
and the final resolve remain in one context and one outgoing native fence.
Rejected HDMI swaps now return -1; a failed drawable logs its fault once rather
than reporting thousands of successful-looking swaps. No GPU dependency, buffer
retirement condition or CPU rendering fallback was removed or introduced.

The matched Gallium, EGL, GLX, GBM and DRI frontend payload was built on
`root@192.168.104.201`. Every tracked source blob and staged artifact was verified.
The production cache fixture passed with sanitizers; hardware-dependent builder
tests were skipped because that host has no device. The JSON records exact hashes.

## Hardware evidence

The new `egl-resolve-pattern.py` uses an actual X11 EGL/GLES3 window, 32-bit visual,
changing quadrant patterns, real resizes and settled X root captures. It performs
no client readback or finish before swapping. Repeated dimensions cross the
128-pixel capacity boundaries. Checks cover four independent colors and geometry.

| Case | Reported successful swaps | Correct image checks |
|---|---:|---:|
| Deployed `eecab7336`, ordinary EGL | 24/24 | 0/24 |
| New frontend adapter, ordinary EGL | 24/24 | 24/24 |
| New frontend adapter, damage-aware EGL | 24/24 | 24/24 |
| New frontend adapter, local-scope trace helper | 24/24 | 24/24 |
| New frontend adapter, GLX 4x MSAA and interval transitions | 24/24 | 24/24 |

Before the fix the EGL window remained its distinct magenta background, with zero
pipeline submissions. After it there were 24 genuine submissions/completions and
24 integrated resolves. These are settled software image checks, not physical
scanout capture or a proof of seamless active resizing.

The ioQuake3 demo completed normally with the new matched stack. Excluding warmup,
1,380 Present completions had all 1,379 adjacent MSC deltas +1. Request-to-event
mean was 27.43 ms, p99 28.99 ms. Original config/demo remained unchanged; temporary
profiles and traces stayed in RAM.

## Real browser and interactive resizing

The isolated browser uses a RAM profile and a local WebGL pattern page. The user's
profile and Android Firefox were not touched. The test copies the three graphics
settings already present in the normal user profile: hardware WebRender, forced
acceleration, and X11 EGL. Hardware WebRender is confirmed by Troubleshoot data,
with FD740, GLES 3.2, and the exact new Mesa SHA. A fresh unconfigured profile
instead classified FD740 as `mesa/software-unknown` and selected software
WebRender. That diagnostic default is not evidence of the normal user's settings.

With the corrected EGL adapter, Firefox visibly painted its full UI and the
changing WebGL page. Teardown recorded 14,050 submissions/completions and integrated
resolves, zero worker-context copies, and no rejected HDMI Presents. The test then
resized it through Openbox's standard `_NET_WM_MOVERESIZE` path, using bounded
synthetic pointer motion and restoring pointer/focus afterwards. Firefox advertises
both `_NET_WM_SYNC_REQUEST` counters; Openbox supports that protocol.

The 15-second workload grows for four seconds, shrinks for four, alternates between
640x480 and 1280x960 at 20 Hz for four, then settles at 960x720. Every 200 ms it
samples the owned window crop from an independent committed framebuffer.

The corrected sampler classified all 40 gradual grow/shrink samples and all 15
settled samples as a consistent four-quadrant frame. During the extreme size-jump
phase, 20/20 samples were unclassified; inspection shows clipped older/smaller
geometry and exposed black extent. A direct-resize control classified 69/75, with
six unclassified samples around abrupt transitions. These sparse phase-dependent
samples are not physical FPS or comparable blank-frame rates. No claim that all
resize corruption is solved is warranted. Root/display geometry coordination and
partial repaint/age handling still need matched traces.

The content RAF loop recorded 836 callbacks in the interactive case and 811 in
the direct case. These are content callbacks, not displayed frame cadence. The
first preliminary page reset its canvas every frame; that case was superseded by
a persistent-canvas correction and is not used for performance claims.

The corrected normal glxgears also ran via the real WM resize path. It still
produces resize background samples: 18 among 53 early stable-geometry samples.
It does not implement the sync-request protocol and leaves ForgetGravity, so the
previous background finding is not limited to the direct XResizeWindow controller.
Do not force global bit gravity or suppress exposure semantics as a visual fix.

## Corrections to the diagnostics

The initial committed sampler mapped only the crop with a nonzero mmap offset.
The pinned MSM implementation [uses the address relative to the VMA, ignoring
its page offset](https://raw.githubusercontent.com/LineageOS/android_kernel_sony_sm8550-modules/ec2e039129f2b8f93fdfe62a8c6a595efb63d496/qcom/opensource/display-drivers/msm/msm_gem.c).
It therefore sampled the wrong origin for a centered Firefox window. The helper
now maps from zero and reads only the requested crop. Prior gears comparison
captures all had crop origin (0,0) and are unaffected. The earlier centered-browser
committed images are explicitly discarded; their X root capture independently
showed the genuine EGL paint rejection.

The original LD_PRELOAD tracer could also terminate the isolated browser with
status 126 when a locally loaded Mesa/XCB library was not visible to RTLD_NEXT.
That was a diagnostic loader failure, not a Mesa crash. The updated helper resolves
within already loaded library handles when needed, keeps separate process trace
files, avoids writing inherited fork buffers, and dumps on fast process exit.
Its local-scope EGL regression passed 24/24 images. The accepted browser visual
run had no interposer loaded; do not attribute the failed traced run to Mesa.

All screenshots, raw traces and private profiles remain under RAM-backed `/tmp`.
Only source, compact reports and final runtime bundles are persisted to SD. No
Magisk module, kernel driver, Android browser or original game configuration was
changed. Physical 4K60, full desktop responsiveness and browser Maps/Teams remain
unverified. The next presentation work must keep the native completion handoff
and validate partial-paint/resize history rather than hide the exposed regions.
