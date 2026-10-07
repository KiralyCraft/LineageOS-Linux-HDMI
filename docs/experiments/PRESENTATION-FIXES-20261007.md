# Combined consumer-release and queue candidate

This candidate implements the follow-up to
[PRESENTATION-OFFLINE-20261007.md](PRESENTATION-OFFLINE-20261007.md). It is prepared
for a combined hardware test, not accepted as a fix for OpenJK's backward jumps.
The reported morning runtime was the E ABI 1 BCDEF controls bundle at 4K60,
before the CPU-policy changes. No display session was restarted for this build.

## Xorg: finish consuming before releasing

`0010-present-fence-gated-copy-release.patch` adds a private registration hook
between the matched modesetting module and Present. In fence mode, each Present
copy exports the glamor context's native GPU fence and retains its source pixmap,
serial, and Sync idle-fence wrapper. It sends IdleNotify only after successful
fence readiness. Present completion continues through the existing path; direct
flip retirement is unchanged. Completion and safe buffer reuse remain distinct.

The pending release record is independent of the Present vblank's lifetime.
Window destruction clears its window pointer; the retained pixmap and optional
Sync wrapper survive until the fence finishes. Screen teardown drains outstanding
GPU work before unregistering readiness and releasing references. Export,
registration, timeout, and error-signaled fences fail closed; there is no fallback
that declares unfinished work safe. The five-second timeout ends Xorg rather
than releasing a buffer after an unknown GPU outcome.

`--present-release fence` is the new bundle default. `finish` is an explicitly
blocking diagnostic; `legacy` is the original behavior for controlled comparisons.
Normal operation uses event-loop readiness rather than a CPU completion wait.
Fence export still has CPU submission cost; a performance gain is not assumed.
The private Xorg binary must be replaced along with both modules because the
new Present code and exported hooks live in Xorg itself.

## Mesa: bound frame backlog separately from cached allocation memory

Mesa commit `557306b5c488632a9771346d3f12d23117c9cf5b`, on
`feature/hdmi-bcdef-candidates`, adds `MESA_KGSL_HDMI_QUEUE=low-latency|fifo`.
The ordinary interval-zero low-latency path admits at most two outstanding frames
across all resize generations, counts completed-but-not-idle slots as owned, and
submits without an artificial future-MSC target. Accepted frames remain FIFO;
none are dropped, overwritten, or assigned synthetic completion. When switching
from scheduled presentation to interval zero, it waits for earlier scheduled
slots to retire before issuing a target-zero request.

Positive/adaptive interval and explicit scheduling/force-copy requests do not use
this new policy. The pre-existing HDMI pipeline's limitations for explicit MSC
and force-copy APIs are unchanged; this patch does not claim to implement those
APIs. The `fifo` control retains the old queue behavior. The newer bounded resize
cache at `e04c1852d` is included in both controls. Two frames is an initial bounded
policy, not an empirically optimal queue size. Backpressure can reduce the game's
reported FPS while improving freshness; the hardware test must measure both.

## Reference implementations inspected

The matching minimal Termux:X11 source at
[`5fc8f28`](https://github.com/KiralyCraft/termux-x11/tree/5fc8f28)
contains the important ownership rule already: `Renderer::applyPendingGpuCopies()`
(and the redraw path) acknowledges GPU copies only after an EGL fence wait.
Its Present patch keeps the offloaded copy pending until the renderer's completed
serial is observed, then releases the source. The HDMI implementation uses native
fence readiness instead of Termux's shared copy queue and renderer wakeup protocol.
We did not copy Termux's best-effort stalled-renderer/requeue-failure recovery into
this path: the new release record never signals success on an unknown outcome.

Online primary references checked on 2026-10-07:

- [Android synchronization framework](https://source.android.com/docs/core/graphics/sync):
  acquire fences cover prior writes; release fences cover the consumer's reads
  before the producer may overwrite. Present fences have a different meaning.
- [Weston explicit-sync client implementation](https://cgit.freedesktop.org/wayland/weston/diff/?id=a95bb6f7e554c1a7ae52e87391f86944a3ba9461):
  retains a release-fence FD per buffer and waits/imports that dependency before
  rendering into the returned buffer. This offers a later optimization: carrying
  a consumer fence back for GPU-side waiting, rather than waiting to send idle.
- [Wayland buffer-sharing architecture](https://wayland.freedesktop.org/docs/book/Architecture.html):
  producer/consumer ownership can transfer asynchronously using synchronization
  objects. That protocol is not directly usable by our pinned KGSL/Xorg pair.

These support the synchronization design, not measured equivalence on another
phone. Some Xwayland upstream pages were inaccessible; no uninspected Xwayland
implementation is claimed as validation. There was no suitable verified drop-in
patch for this leased-Xorg stack in the sources reviewed.

## Build and offline checks

All native compilation ran on `root@192.168.104.201`. Mesa sources were verified
against 12,793 Git tree entries. Xorg rebuilt all 55 selected compilation units
including the complete Present and glamor archives, relinked Xorg and both private
modules, and verified the exported release/fence symbols. The compiled Xorg series
is **0001 through 0007 plus 0010**, preserving the original E ABI 1 implementation.
The E ABI 2 patches 0008/0009 remain separate diagnostic options in the repository.

- ASan/UBSan lifecycle fixture: pending release survives vblank/pixmap-resource
  destruction; destroyed windows lose callback pointers; teardown drains; already
  signaled fences release once; errors, unsignaled readiness and timeout never
  notify idle. This compiles actual release helpers with stubbed X/fence resources.
- ASan/UBSan queue fixture: preserved interval-one FIFO/targets, target-zero
  submission, cross-generation admission bound, interval-transition ordering,
  unfinished-producer ordering, and stop/fault escape. This compiles the actual
  policy/submission fragments with stubbed resources.
- Existing repository Python suite: 58 tests passed after adding patch 0010 to
  the expected ordered series. Full patch series applies with zero fuzz.
- Native Mesa device tests were **skipped** on the build host because KGSL is not
  exposed there; they are not hardware passes.

No GPU-visibility, physical scanout, 4K60 latency, resize or OpenJK acceptance is
claimed by these tests. Offline fixtures cannot establish cross-process cache
visibility. The existing native broker, kernel companion and USB/Bluetooth input
artifacts are preserved; a new Magisk installation is not needed.

## Live test when HDMI is available

Start the combined BCDEF candidate with both defaults. First check numbered
pattern frames for backwards content and corruption, with the cursor visible and
hidden; then glxgears resizing and the unchanged windowed OpenJK launcher. Keep
output mode, CPU policy and window sizes fixed. Record the actual Present mode,
release behavior, frame age and physical outcome separately from application FPS.
Use `--mesa-queue fifo`, `--present-release finish`, and `--candidate BCDF` as
separate controls if needed. `legacy` restores the suspect release behavior and
is only a regression control. Do not introduce all controls at once.

The user requested a pause before restarting HDMI. Leave existing agents/apps
alone until that confirmation. Prior bundle folders remain available for rollback.

## Prepared combined folder

`~/Downloads/hdmi-termux-vblank-min-coexist-usb-bt-bcdef-release-queue-20261007`

The launcher is copied from the preserved Downloads comparison bundle and gains
only explicit release/queue controls and their capability checks. It defaults to
BCDEF, continuous mode, consumer fences and the low-latency interval-zero queue.
`build-support/package-presentation-fixes.py` reproduces the package, checks input
and output hashes, and includes the matched Mesa frontend/Gallium/GBM libraries,
Xorg binary/modules, exact graphics patches and historical native/kernel archives.
