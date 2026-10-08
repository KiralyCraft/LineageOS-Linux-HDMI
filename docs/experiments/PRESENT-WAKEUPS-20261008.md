KGSL Present wakeups and cross-client repaint scheduling, 2026-10-08

The baseline continuous desktop is the prior interop-capacity bundle, Mesa
065604e4b, Xorg 26.0.99.903 plus the producer-ready export patch. The output is
3840x2160 at 30 Hz. New Mesa builds are selected only for owned test clients.
Raw traces, images, Xauthority copies and temporary game profiles remain in
RAM-backed /tmp. This report does not establish physical panel cadence or 4K60.

A native memory-buffered probe records swap calls, Present requests and Complete
and Idle notifications. Analysis selects only the private Present worker stream;
the main connection receives duplicate notifications. Five warmup seconds are
excluded. Copy completion is kept distinct from displayed scanout and retirement.

The recurring 100 ms fullscreen stall has a confirmed client event-routing cause.
The kernel delivered DRM flip sequence 432037 at 15205.826326 s; Xorg woke the
Mesa Present worker at 15205.826534 s. The worker ran immediately, slept again,
and observed the completion at 15205.926807 s, 100.651 ms after the event UST.
A general xcb_poll_for_event call after draining the special queue can read newly
arrived Present data into that special queue, emptying the socket before poll.
The worker then has a queued event but no readable socket and sleeps to timeout.
The real XCB reproducer in tests/graphics-contract/xcb-special-queue.c demonstrates
this using PresentNotifyMSC on the running server. A queued-only general drain
leaves unread socket data available. Mesa d0ba21fcb applies that change to both
the C/D worker and the older SHM bridge, without shortening timeouts.

After the routing fix, a 180-second hidden-cursor fullscreen run produced 5266
actual flip completions after warmup: all 5265 adjacent MSC deltas were +1.
The worst event timestamp age was 8.14 ms; none exceeded 20 ms. Mean Present
request-to-event latency was 27.01 ms. This is DRM/Xorg evidence, not an optical
measurement of the panel.

There is a second, independent issue in the maximized copy-to-desktop path.
The corrected animated glxgears event loop and matched Mesa 85c8dfd02 were used
for every comparison below. The window was maximized by the real window manager,
with the cursor visible and no screenshots taken during timing.

| Future-frame bound / MSC lead / panel | Completed frames | Adjacent MSC deltas | Mean request-to-event |
| --- | ---: | --- | ---: |
| 1 / 1 / active | 1321 | 1274 +1, 46 +2 | 28.02 ms |
| 2 / 1 / active | 1368 | 1367 +1 | 57.22 ms |
| 2 / 2 / active | 1367 | 1366 +1 | 54.95 ms |
| 1 / 1 / temporarily stopped | 617 | 616 +1 | 26.94 ms |

The first three runs lasted 50 seconds; the panel control lasted 25 seconds.
LXPanel was temporarily SIGSTOP'd and always SIGCONT'd in finally; it was not
closed or reconfigured. Its normal monitor plugins remained enabled afterwards.
The skips in the first run occur almost exactly once per second. Fresh completion
USTs rule out the previously fixed delayed notification. The shorter ftrace
reproduction has the same signature: 16 +2 deltas and 433 +1 after warmup.

During one missed update the previous frame completed at MSC 528173. The producer's
last native fence callback occurred at 18410.369619 s; Present was sent at
18410.369683 s. Xorg's copy completed at 18410.373842 s, well before vblank 528174
at 18410.392822 s, but another root update had already been submitted. A subsequent
copy at 18410.393438 s completed at 18410.397556 s, and the client notification
followed vblank 528175. This supports cross-client damage splitting into separate
TearFree snapshots, rather than slow render completion or delayed KGSL signaling.
Disabling panel activity removed the periodic misses; increasing the client queue
masked them at the cost of about one refresh of additional latency.

Patch 0012 therefore tests a frame-timed asynchronous TearFree repaint. A server
notification timer collects damage before the immutable GPU-copy snapshot. The
next deadline is anchored to the last real flip UST and the current mode period,
with an 8 ms copy/submission reserve. No sleep blocks Xorg request handling; copy
pending and submitted-flip states, native completion fencing, FIFO ordering and
COMPLETE+IDLE retirement remain intact. Stale, future or absent USTs do not seed
a timeline. Teardown cancels the timer before releasing CRTC resources. The
control HDMI_LOS_TEARFREE_REPAINT_US=0 restores immediate-copy scheduling.

The scheduling model follows the established repaint-window approach documented
and implemented in Weston's [compositor](https://cgit.freedesktop.org/wayland/weston/tree/libweston/compositor.c).
It is newly integrated here into the leased modesetting/KGSL backend; it is not
an assertion that an upstream Xorg release already includes this implementation.
The initial 8 ms reserve must be assessed at each claimed output/workload; 4K60
has not been tested. Native production-function tests cover timeline rejection,
quantization, a single timer and readiness consumption. Hardware results follow
once this matched private Xorg has been started.
