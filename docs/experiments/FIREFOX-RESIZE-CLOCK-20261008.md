# Firefox resizing and display clock, 2026-10-08

The isolated Firefox tests used WebRender and Freedreno FD740, with EGL and
hardware WebGL confirmed through Firefox's graphics diagnostics. The generic
renderer string in page-facing information does not establish software rendering.
All cases here used Mesa R13 and the continuous 3840x2160@30 leased Xorg desktop.
Android Firefox and the user's normal Firefox profile were not modified.

The automated workload grows and shrinks the client at 20 request batches/s,
alternates abruptly between 640x480 and 1280x960 for four seconds, then settles.
It captures the DRM committed framebuffer every 0.2 seconds and checks changing
four-quadrant frame patterns. These captures are software observations, not
optical scanout measurements. XSync turnaround is server request processing,
not displayed FPS. Sampling can alias the abrupt resize cycle; the following
counts must not be used to rank small differences between variants.

| Variant | Correctly classified captures | Unclassified | Page animation callbacks |
| --- | ---: | ---: | ---: |
| Bare Xorg, before compositor | 62 | 13 | 840 |
| XRender compositor | 61 | 14 | 829 |
| Bare Xorg, after compositor | 58 | 17 | 842 |
| Older accelerated bridge | 58 | 17 | 718 |
| Ordinary shadow Present | 55 | 20 | 786 |
| Logical-region trace | 64 | 11 | 831 |
| Firefox buffer age disabled | 56 | 19 | 820 |
| Firefox full repaint | 56 | 19 | 834 |
| Continuous circular resize | 74 | 1 | 837 |
| Native display clock, fixed trace probe | 59 | 16 | 454 |

Every gradual grow/shrink and settled sample passed in these cases. The circle
test's single unclassified sample was the initial instant after the first size
change; all subsequent samples passed. Sampled frame identifiers advanced with
no duplicates or backwards jumps. Abrupt changes still expose old/clipped images.
The region trace found both old-sized and correctly sized outgoing Present
images near those captures; request timing alone does not identify the image
actually consumed by Xorg. A valid bare trace covered the complete workload:
1,013 asynchronous Present requests, all target MSC zero. This is a different
path from EGL preserved-buffer swaps.

The user subsequently observed transient pink or black background during the
native resize diagnostics, and purple duck.ai page background during vertical
Firefox resizing. Pink is the diagnostic's deliberately visible underlying
background. Earlier checks at quadrant centers and settled screenshots cannot
exclude exposed edges or short repaint gaps. Retain that user observation as an
open issue; the preservation fix does not establish that browser resizing is fixed.

Adding picom v12.5 (`a456d438d0a681365d90a8adbf0941cede41aaa9`), using XRender
through Xorg glamor with no shadows/fades/blur, did not resolve the clipping.
Grow/shrink request p95 rose from 11.48/11.71 ms to 15.54/13.24 ms in the first
controlled comparison. Picom was built on the build server, passed 11 unit tests,
ran temporarily from RAM and was stopped. It was not installed or enabled at login.

Firefox's default animation clock ran around 55-59 callbacks/s despite Xrandr
and GDK both identifying 30 Hz. The default X11 software clock applies a roughly
59 Hz floor in [gfxPlatformGtk.cpp](https://github.com/mozilla-firefox/firefox/blob/main/gfx/thebes/gfxPlatformGtk.cpp).
An isolated profile enabling `gfx.x11.glx_sgi_video_sync` used a GLX vsync thread
while retaining EGL WebRender and FD740. It produced about 30 callbacks/s:
mean interval 33.334 ms, p95 34.52 ms, maximum 37.92 ms. Forty-one unique NotifyMSC
events in its overlapping trace advanced by one at mean UST spacing 33.333325 ms.
Firefox's nominal target rate remained 60; that field is not its measured cadence.
The 45-second trace checkpoint covered only about two seconds of activity after
browser startup, so it cannot support full-workload correlation. No global
Firefox clock preference has been changed and clipping persisted with this clock.

The trace helper also had an independent vfork ownership bug: a spawned child
cleared its parent's output state in shared memory. Moving the PID guard before
the atomic exchange fixed it. The native AArch64 fixture produced zero bytes
before and two intact viewport records afterwards; it needs no GPU or X server.
QEMU's builder execution did not reproduce the original vfork behavior.
`frame-pacing-probe.c` now also records logical XFixes region dimensions without
round trips or readback. `firefox-wm-resize.py`, `marionette_client.py` and
`firefox-webgl.html` provide the owned-process, private-profile test workload.

Raw screenshots and traces remain in RAM or verified build-server SSD archives.
Private browser profiles were removed only after their owned browsers exited.
The JSON records case hashes and archive location. Physical 4K60, whole-viewport
resize exposure, Maps/Teams and periodic game/desktop pacing remain open.
