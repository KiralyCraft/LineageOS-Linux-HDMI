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
quantization, a single timer and readiness consumption. Live software results are recorded below.


The matched Xorg was built on root@192.168.104.201. Upstream tests: five PASS,
one optional XTS SKIP. Symbol/SDK validation, copy-release lifetime/error fixtures,
export-reply lifetime/error fixtures and the production repaint timing/state tests
passed. The native host fixture also passed with ASan/UBSan/LSan. An initial
source-reconstruction attempt found a missing 0011 patch in the older builder
checkout; that attempt did not reach compilation. The exact patch was supplied
and the successful build reconstructed and compared the complete patched tree.

The enabled 50-second maximized run produced 1367 copy completions after warmup,
all 1366 adjacent MSC changes +1. Request-to-event mean was 26.96 ms, p99 27.38 ms,
maximum 27.58 ms. Swap-end intervals were 33.33 ms mean and 35.30 ms maximum.
LXPanel stayed active. Both the new Xorg code and normal client Mesa were used.

A same-Mesa, same-Xorg-binary control temporarily mounted the previous complete
modesetting module over the new module, with Xorg stopped on either side of the
switch. Both are built against the same Xorg SDK and existing glamor/native
interfaces. The control restores the prior immediate-copy implementation; no
other runtime component changed. Its 25-second run reproduced 20 +2 MSC gaps
among 595 adjacent intervals, all at the periodic-update phase. Request-to-event
p99 returned to 57.28 ms and swap intervals reached 67.25 ms. The new module was
then restored and the desktop resumed successfully. The following 180-second enabled run produced 5266 completions: 5264 adjacent
MSC changes +1 and one isolated +2. Request-to-event mean stayed 26.95 ms, p99
27.41 ms. The periodic once-per-second defect did not return, but the isolated
miss is retained as an unresolved tail, rather than a zero-skip claim.

A separate owned two-window 2D movement test uses two 1100x700 colored windows,
50 request batches/s and XSync turnaround, with no client GL rendering. Before
and after each produced 1000 batches in 20 seconds. Mean turnaround was 1.91 ->
1.73 ms, p95 3.16 -> 2.99 ms and p99 4.77 -> 3.56 ms. This is a request-processing
check, not a measured display latency improvement or physical FPS claim.


The recorded ioQuake3 demo completed normally with the same 1920x1080 windowed
profile, interval one and no software frame cap. All 1361 adjacent completion
MSC changes after warmup were +1; mean request-to-event was 27.48 ms, p99 29.00 ms.
The earlier original-depth comparison was 93.04 ms. No original configuration
or game asset changed. These are server/DRM timings rather than physical panel
latency. The new default path was used, without queue or MSC environment overrides.

Image validation on the matched runtime passed 24/24 changing patterned resize
frames with interval 1/0 transitions and 12/12 near/full-screen hidden-cursor
transitions. Sources and destinations use distinct changing patterns; geometry
and multiple image positions are checked. These readback/capture checks are
separate from performance runs. Lazy allocation of the three generation slots,
4K60 validation and broad Firefox/Maps/Teams usability remain outstanding.


The corrected normal glxgears command was also resized with the same 20 Hz
waveform and 0.2-second X-root capture schedule. It left at most eight X events
pending; one already-issued old-geometry swap finished 22.07 ms after the final
resize, then viewport swaps matched 960x720. The earlier stock loop continued
obsolete viewport swaps for 3.13 seconds. Final settled images show the correctly
scaled animated gears, and 96 captures remain in RAM.

There are black intermediate root snapshots during rapid resizing: a sampled
color grid found 19 candidates in the new 96-capture run and 26 in the older
112-capture corrected-loop control. None occurred after resizing stopped. These
are snapshots of the root drawable, which can be cleared by Configure before
client repaint and before the TearFree scanout snapshot. Capture requests are
also intrusive GPU readbacks. Consequently these images neither prove physical
black flashes nor establish perfectly seamless physical resizing. The viewport
backlog is fixed, while front-scanout image verification and resource-allocation
tails remain separate work.
