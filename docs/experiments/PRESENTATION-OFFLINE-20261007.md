# Offline audit: glxgears resize replay and OpenJK rubberbanding

## Runtime identity and report

The user confirmed that the symptoms occurred in the morning's 4K60 session,
before the CPU power work, using:

`hdmi-termux-vblank-min-coexist-usb-bt-bcdef-controls-20261005`

The saved 2026-10-07 06:35:52 UTC audit independently identifies that bundle,
Mesa `170a56fa943cc475472d170157efe58d7dd9b8c1`, asynchronous TearFree, the kernel
presenter, and E ABI 1. Its Gallium hash was rechecked offline:
`71de3ba44d40e56e784759e34e1ef3eb877a5e30be2b202b2905495cfd45a491`.
It does **not** contain the newer resize cache at `e04c1852d`.

The user reports that glxgears' outer window reached its new size while the
animated 3D viewport subsequently followed the resize in slow motion. OpenJK
rubberbanded despite solid reported FPS, unlike before BCDEF. The launcher in
`~/Games/OpenJK/run_sp_windowed.sh` sets custom window dimensions, disables
flares, requests `com_maxfps 65`, and sets `r_swapInterval 0`. The user's saved
zsh history includes the 1920x1080 windowed invocation on DISPLAY=:1.

Follow-up clarification: OpenJK repeatedly jumped backward to an earlier image
and then rapidly returned to the current rendering. This is not merely a report
of hesitation followed by catching up. Treat stale content and presentation
ordering as correctness suspects, independently of the resize backlog.

The plain E-diagnostic launcher defaults to BCDF, but that is not the runtime
in this report. Directory modification time is not a reliable version selector.
No HDMI lease, application, affinity, graphics setting, or module was changed
by this offline audit. The CPU guard's later success does not establish smooth
presentation in the earlier session.

## 1. Resize replay has a credible application-event mechanism

Installed `mesa-utils` is 9.0.0-7. The upstream
[9.0.0 glxgears source](https://gitlab.freedesktop.org/mesa/demos/-/blob/mesa-demos-9.0.0/src/xdemos/glxgears.c)
handles ConfigureNotify by changing viewport/projection, but an Expose event
returns DRAW and ends the event-draining loop for that frame. It can therefore
draw after each resize/expose pair even when a newer size is already queued.
The animation continues during those draws. This fits the reported distinction
between the already-resized outer window and the changing animated viewport.

An offline fixture compiled the production event-loop body against deterministic
X/GL stubs. With 120 queued Configure/Expose pairs it drew 120 times; with the
same 120 Configure events followed by only one Expose it drew once. This is an
illustration of the actual control flow, **not** proof that the phone received
that exact event sequence or a measurement of its replay duration. Distribution
patches and the runtime event stream were not captured.

The reported Mesa version can amplify that backlog: `hdmi_pipe_present()` holds
`p->lock` while `hdmi_pipe_generation()` destroys idle generations, creates three
pixmaps, waits for checked X requests, and imports their GPU allocations. The
presentation-event worker also needs that lock. Resizing can thus delay buffer
admission and notification processing even though GPU completion is asynchronous.
The three-generation bound limits image memory; it does not bound the Xlib
Configure/Expose backlog.

This allocation/retirement serialization is already addressed in `e04c1852d` by
a separate allocation connection, allocation outside the event lock, and cached
size generations with deferred destruction. Continuous unique sizes still incur
allocation costs. Its prepared Mesa-only comparison retains the exact E ABI 1,
Xorg, native and kernel components:

`hdmi-termux-vblank-min-coexist-usb-bt-bcdef-mesa-resize-20261007`

That is the first clean comparison for this resize symptom; it is not yet a
verified fix. Do not globally discard X11 resize events or override application
viewport commands to conceal delayed handling.

## 2. C/D changed the behavior of an unsynchronized producer

The older bridge's interval-zero path skips a swap when its capture slot is
unavailable, before doing the bridge transfer. The first frame and interval-
controlled swaps have different admission rules. See
[`dri3_shm_bridge_present()`](https://github.com/KiralyCraft/mesa-for-android-container/blob/170a56fa943cc475472d170157efe58d7dd9b8c1/src/gallium/frontends/dri/loader_dri3_helper.c#L806).

The C/D pipeline instead waits for a free slot regardless of swap interval and
keeps accepted frames in FIFO order. Three slots per generation and three
generations bound storage, but there is no separate shallow latency budget.
An older frame waiting for its producer fence prevents later ready frames from
being submitted. This preserves order but can increase frame age and couple
an interval-zero game's swap call to display service delays.

The production admission/submission fragments were compiled with stubs on the
build server. A saturated interval-zero generation called its condition wait.
With an oldest producer pending and eight later frames ready, no later frame
was submitted. A shuffled slot/generation layout submitted all ready frames
in accepted order. These tests establish policy, not real GPU fence behavior,
physical frame order, or the cause of OpenJK's visual symptom.

Both old and new bridges retain the initial/re-prime MSC lead of two refreshes;
it is **not a new BCDEF invention**. The new pipeline also applies future MSC
targets when the interval is zero. With last-completed MSC 100, target 102, and
nine ready fixture slots, submission used targets 102 through 110 while setting
PresentOptionAsync. Three slots at a fixed size imply a smaller bound; the
nine-slot fixture represents multiple live resize generations, not normal
steady-state OpenJK occupancy.

In the pinned Xorg,
[`present_get_target_msc()`](https://github.com/XQuartz/xorg-server/blob/65d790bd208ec380b196eb98f144abb0b32e334d/present/present.c#L143)
returns a future requested target before considering the async option. Async
therefore does not turn such requests into immediate presentation. This is a
source-confirmed source of scheduling lead. It alone does not prove that the
regression is caused by that lead, since the old bridge used it too.

The launcher's requested 65 FPS and interval zero also do not promise even
physical 60 Hz motion. The same launcher worked better before BCDEF, so this
setting is a useful controlled test axis, not grounds to blame the application
or declare the regression expected. Solid game FPS is not a display-cadence or
frame-age measurement.

## 3. Consumer completion is a stronger correctness lead

The matching E ABI 1 Xorg build source was inspected on the build server at
`hdmi-bcdef-xorg-20261005/output/src`. There is a reachable windowed-copy path
without a GPU-completion dependency back to the producer:

1. `present_check_flip()` first asks the modesetting driver whether a flip is
   possible. If the driver accepts but a subsequent window geometry/clip check
   rejects the flip, the reason can remain `PRESENT_FLIP_REASON_UNKNOWN`.
   A windowed game with its software cursor hidden is a plausible way to reach
   this path; the actual OpenJK reason was not captured.
2. `present_execute_copy()` submits `present_copy_region()`, calls the driver's
   flush hook, then sends IdleNotify. That flush reaches
   `glamor_block_handler()`, which calls `glFlush()`, not a completion wait.
3. With an ordinary copy reason, `present_execute()` does not take the TearFree
   fake-flip completion branch and can send CompleteNotify immediately afterward.
   The TearFree-tagged branch has additional completion bookkeeping, so this
   finding must not be generalized to every copy or every idle event.
4. Mesa's `hdmi_pipe_events()` frees a presentation slot when both notifications
   have arrived. Its producer can then overwrite that imported DMA-BUF without
   any dependency on Xorg's still-running read of it.

The KGSL submission code supplies an explicit input fence when one was supplied
by its own context; this pipeline receives no Xorg read-completion fence.
KGSL's BO implicit-sync preparation is a no-op. A signaled application resolve
fence protects **producer-to-consumer** use, but does not protect the opposite
**consumer-to-next-producer** transition.

This is a source-level missing synchronization contract in a reachable path,
not a live reproduction of the reported backward jumps. Reusing storage while
another context reads it can produce incorrect contents even with FIFO Present
requests. It deserves investigation before declaring the game symptom a latency
issue. The older bridge also relied on idle notification, so this is not proven
to have originated in C/D; synchronous desktop work or different scheduling
could previously have reduced its visibility.

Source anchors: pinned upstream
[`present_execute_copy()`](https://github.com/XQuartz/xorg-server/blob/65d790bd208ec380b196eb98f144abb0b32e334d/present/present_execute.c),
the local [TearFree Present changes](../../patches/xserver/0003-modesetting-backport-upstream-tearfree.patch),
and pinned Mesa
[`hdmi_pipe_events()`](https://github.com/KiralyCraft/mesa-for-android-container/blob/170a56fa943cc475472d170157efe58d7dd9b8c1/src/gallium/frontends/dri/loader_dri3_hdmi_pipeline.h)
and [KGSL submission](https://github.com/KiralyCraft/mesa-for-android-container/blob/170a56fa943cc475472d170157efe58d7dd9b8c1/src/freedreno/drm/kgsl/kgsl_ringbuffer_sp.c).

The robust correction is to gate release/reuse on actual successful Xorg GPU
read completion, preferably by exporting a native fence after the copy and
deferring idle notification through the server event loop (or transporting an
explicit consumer fence). Keep completion semantics separate, retain the pixmap
and event identity through destruction, and preserve the scanout-retirement rule
for direct flips. A narrowly scoped finish-before-idle diagnostic could test
the hypothesis, but restoring a steady-state Xorg CPU wait is not the intended
production solution. Reducing queue depth alone does not close this dependency.

## 4. What remains unproven

- No offline probe has reproduced old images being physically shown after newer
  ones. Logical FIFO submission does not prove correct GPU visibility or scanout.
- E ABI 1 has earlier recorded copy correctness concerns. A BCDF comparison is
  still needed to separate pixel-content faults from C/D queue behavior.
- F has one busy request per presenter session and Xorg keeps distinct copy/
  flip-pending states. This is not evidence of an unlimited kernel frame queue.
  It does not establish ideal fence-ready-to-flip latency.
- The 100 ms worker poll timeout is a diagnostic target if XCB-buffered events
  lose a socket wakeup, not an observed 100 ms stall in this workload. A similar
  special/general-event draining pattern already exists in the old bridge.
- Maps was helped by avoiding Android's display-off CPU limits according to the
  user. Remaining browser pacing/copy cost has not been measured in a matched
  trace with the new CPU module and is separate from the solved CPU availability.

## Engineering and validation order

1. Prioritize a numbered-frame shared-buffer test of consumer completion:
   compare normal release with fence-gated release, recording producer serial,
   buffer identity, Xorg copy submission/completion, and idle notification.
   Keep the image non-fullscreen and compare cursor visible/hidden. An E-off
   control separately tests copy contents. Detect backward image IDs, not merely
   FIFO request IDs. No such GPU/display test ran during this offline audit.
2. Compare the exact morning bundle against the prepared Mesa-only resize
   bundle, at fixed 4K60, with the same window sizes and CPU policy. Record the
   latest WM size, latest app Configure event/viewport, current render-buffer
   generation, and accepted/submitted/completed serials. This distinguishes
   application event replay from old presentation buffers.
3. Keep C/D while comparing BCDEF with BCDF to isolate E. Use numbered frames
   with changing patterns; observe whether frame IDs ever go backward. X-root
   screenshots can inspect contents but do not certify physical scanout order.
4. Make interval-zero admission a deliberate low-latency policy. Bound the
   number/age of pending frames independently of allocation capacity; avoid
   assigning future-refresh deadlines unnecessarily. If replacing obsolete
   unsent frames, retain producer fences and storage until safe, and preserve
   correct completion accounting. Never reclaim a displayed or busy image early.
   Preserve interval-controlled FIFO semantics and explicit MSC requests.
5. Instrument producer/resolve readiness, slot waiting, Present submission,
   completion mode/MSC, and actual frame age. Compare OpenJK with its unchanged
   launcher and an explicit interval-one override as a separate experiment.
   Do not lower the synchronous +2 lead blindly: the old bridge previously
   needed copy overlap to avoid a 30 FPS serialization path.

No production graphics patch or new runtime was deployed in this audit. The
next work should close the consumer-completion contract, then target admission,
resize churn and measured frame age while keeping hardware acceleration intact.

## Reproducible offline probes

`tests/graphics-contract/presentation-offline.py` extracts the production
control-flow fragments, supplies deterministic resource/event fixtures, and
builds with ASan/UBSan. Run **on the build server**, with Mesa source and the
upstream glxgears 9.0.0 source supplied as arguments. It requires no display.
[Probe results](PRESENTATION-OFFLINE-20261007.json) include source hashes and
scope limits. These are diagnostic expectations for the inspected policy,
not a test claiming that the latency behavior is desirable.
