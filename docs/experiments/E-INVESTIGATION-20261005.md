# Candidate E titlebar investigation

The matched E-off / E-on / E-off display tests isolate an E-dependent
regression: the user saw blue dots and a temporarily missing titlebar with
E enabled, and clean titlebars in both E-off runs. The viewport was clean in
all three. See [the comparison](E-COMPARISON-4K30-20261005.json).
BCDF with E disabled remains the working baseline. There is no confirmed fix.

## Additional offscreen evidence

`tests/graphics-contract/blit-stream.py` runs on FD740 using the same private
Mesa build, `e04c1852d386046548676e7004cc9855b3e6bd66`, without starting Xorg
or changing a display lease. It uses 3840x2160 ARGB shared allocations, a core
OpenGL context, and fine BGRA/REV title patterns with changing frame identities.
CPU patterns are prepared before GPU bursts to avoid introducing a Python
generation delay between each frame's GPU operations.

The final matrix covers direct texture uploads and uploads through a private
texture plus a shader copy. Each runs shader, blit, and mixed copy paths, full
and fragmented damage, and pre-copy `glFinish` both enabled and disabled.
Two alternating destinations have separate accumulated damage histories.
Native fences are waited at burst boundaries; destination and root pixels are
read only after the copies. The Mesa diagnostic reports actual CP_BLIT emission.

Both final runs passed: **24 cases, 720 generated frames, 19,872 selected pixel
checks**. [Machine-readable results](E-OFFSCREEN-20261005.json) retain every case.
This does not reproduce or overturn the physical failure. In particular,
readback may add synchronization, only selected pixels are checked, and this
is not the actual Openbox/glamor decoration sequence or DRM scanout.

Earlier compatibility-context probes also passed, but the final results above
supersede them for this investigation. These are correctness results, not
performance measurements.

## Source review

The deployed restricted E helper changes only the origin-aligned, unrotated
root-to-TearFree copy. Generic window movement and its overlap-safe temporary
copies still use the shader path. It restores framebuffer bindings and scissor
enablement and rejects incompatible geometry and incomplete framebuffers.

Glamor's CPU upload path uses `glTexSubImage2D`; depth-24/32 desktop GL uses
BGRA with `GL_UNSIGNED_INT_8_8_8_8_REV`. The final test models that combination.
Xorg's allocation is ARGB while KMS treats it as opaque; the tests now include
ARGB rather than relying only on the earlier XRGB allocation.

No missing blit-fence dependency was established by review. In this Mesa,
`handle_rgba_blit` tracks source reads and destination writes and flushes its
batch. `fd_batch_needs_flush` invalidates the previous context fence, and
`batch_flush` records the last batch's fence. Native-fd export either uses an
eligible existing fence or flushes for one; KGSL exports the submission's
timestamp. This is an inspected dependency chain, not proof that the live
fence signals at the right physical instant.

## Prepared live diagnostics

Patch `0009-glamor-tearfree-copy-diagnostics.patch` adds an explicitly opt-in
`HDMI_LOS_E_DIAGNOSTIC` switch. Without it, E's behavior is unchanged. E remains
off in the ordinary launcher. None of these diagnostic modes is a proposed
production fix.

| Mode | Change | What a visual improvement would suggest |
|---|---|---|
| `finish-before` | Finish preceding GL work before the E blit | A producer/copy ordering or submission-boundary interaction worth narrowing further. |
| `finish-after` | Finish the E copy before returning to Xorg | Copy completion, later root updates, or asynchronous handoff is involved. This alone would not identify the faulty layer. |
| `verify` | Compare all RGB pixels in the damaged rectangles on the first and then every 30th eligible E copy | A mismatch identifies a copy-time source/destination discrepancy. A match with visible corruption leaves root contents, unsampled frames, scanout, or synchronization hidden by readback unresolved. |

Verification uses bounded 32-row buffers (under 1 MiB together at 4K). It saves
and restores framebuffer and pixel-pack state and temporarily unbinds a pack
PBO before reading into CPU memory. It logs counts and the first mismatch
coordinate, not images. It does not repair a mismatch with a shader copy.
GL errors retain the existing shader fallback and warning behavior.

These modes intentionally synchronize and change timing. Do not compare their
latency numbers as performance results. Test them separately using the same
corrected `leased-pattern.py` workload, starting with `finish-after`, then
`finish-before` if needed. Preserve the normal E-off control.

No new HDMI session was started during this investigation. No module, broker,
kernel, or Mesa binary change is required. The user must confirm before a
bounded HDMI restart; the diagnostic bundle is not installed or armed by
packaging it.

## Build and validation

The diagnostic Xorg modules were built on `root@192.168.104.201` using the
frozen O2 configuration: 45 source units, all nine patches applied without
fuzz. Mesa, the broker and the companion were not rebuilt. All 55 repository
Python tests pass. The production-helper test passes under ASan/UBSan on the
build host in five modes, including an injected RGB mismatch and non-default
pixel-pack state. An initial attempt inside the emulated ARM container hit
LeakSanitizer's ptrace limitation; that run is not counted as a pass.

The separate bundle is
`~/Downloads/hdmi-termux-vblank-min-coexist-usb-bt-e-diag-20261005`.
Its ordinary `run-agent.sh` still defaults to E-off BCDF. After the required
unplug/arm preparation, `./run-E-diagnostic.sh finish-after` selects the first
proposed control and defaults to a bounded 60-second session. Other modes use
the same wrapper. It sets the diagnostic after sudo and checks the embedded
diagnostic ABI marker to prevent an accidentally ordinary E-on test.

`build-info.json`, `validation/xorg-e-diagnostic-build.json`, the source archive,
and `SHA256SUMS` record the exact packaged sources and binaries. Physical
validation of these diagnostic modes is pending.

## First live diagnostic: finish-after

The bounded 4K30 run verified `mode=finish-after` in Xorg's log and completed
all 600 test frames. Xorg exited normally, Android was restored, and the
companion reference count returned to zero. The user still saw the titlebar
disappear during the early move-only phase; it was present after resizing
in the second half. **The finish-after control failed physical correctness.**
See [the result](E-FINISH-AFTER-4K30-20261005.json).

Thus, waiting for the E copy to finish before returning to Xorg did not prevent
the observed fault. This does not identify which rendering, damage, or copying
operation is wrong. The run had already finished when screenshots were
requested, so there are no screenshots of that occurrence.

## Screenshot follow-up prepared

`leased-pattern.py --screenshot-dir PATH` now takes root-image crops around
the actual window-manager frame. `xroot_capture.py` follows the parent chain
and translates frame/client geometry into root coordinates, rather than
capturing only the client's clean viewport. Captures include:

- The initially mapped window and the fixed reference at each phase.
- An immediate image after each move/resize request.
- Another image after approximately 150 ms of continued rendering, or at
  phase end if the phase finishes first.

Each record includes phase, step, capture duration, root crop, frame/client
geometry and, for settled captures, the rendered frame serial. Immediate
captures can precede window-manager processing; recorded geometry makes that
visible. The helper encodes PNGs in memory, with a 128 MiB compressed-image
budget, and writes them after the rendering workload. It needs no Pillow,
new native build, module change or replacement GPU runtime.

XGetImage can synchronize rendering and temporarily remove a software cursor.
These captures can therefore affect a timing-dependent fault. They inspect
the X desktop image, not the TearFree destination or physical HDMI scanout.
A corrupt root screenshot narrows the issue to desktop contents; a clean
screenshot does not disprove corruption seen on the monitor. Physical cadence
and latency cannot be inferred from this screenshot run.

The PNG encoder and capture geometry pass three tests, including a private
Xvfb test with a reparented client, a distinct frame/title area, movement,
resize, clipping and deferred image saving. The next proposed HDMI test keeps
the same finish-after runtime and adds only capture instrumentation. It waits
for the user's unplug confirmation before arming another bounded session.

## Screenshot follow-up result

The authorized 60-second finish-after run completed at 4K30 and produced 52
root-image crops. The user again reported missing titlebars between move-only
and resize-only. The captures show the same fault. Xorg exited normally,
Android was restored, and the companion reference count returned to zero.
See [the recorded result](E-SCREENSHOTS-4K30-20261005.json) for capture hashes,
geometry and source identities. The actual PNGs and raw logs remain private.

| Capture | Observed decoration | Client size |
|---|---|---|
| `006-move-only-0-settled.png` | Titlebar present before moving away | 800x600 |
| `008-move-only-1-settled.png` | Titlebar absent after moving; client panels intact | 800x600 |
| `010-move-only-2-settled.png` | Titlebar still absent after moving back | 800x600 |
| `018-move-only-6-phase-end.png` | Titlebar absent at the end of move-only | 800x600 |
| `020-resize-only-0-immediate.png` | Titlebar restored at phase entry | 800x600 |
| `021-resize-only-0-settled.png` | Restored titlebar persists | 800x600 |
| `023-resize-only-1-settled.png` | Titlebar present after the first actual resize | 1001x701 |

All selected captures have stable frame/client geometry across their readback.
Six other captures report a geometry change during readback and are not used
for this comparison. PNG CRCs, decompression and dimensions pass for all 52.

The important distinction is that recovery precedes the first actual size
change. At phase entry the harness changes the title and requests the initial
800x600 geometry. A decoration repaint may restore pixels lost during movement,
but these two operations have not yet been isolated. The client redraws its
viewport continuously; the titlebar depends on preserving or repainting window
decoration pixels when the window moves.

This is evidence against an explanation confined solely to final HDMI scanout:
the missing decoration is also visible through X's root-image readback path.
It does not identify the faulty operation, prove that readback itself is free
of faults, or establish a missing fence. Finishing the E copy still does not
prevent the failure. E remains disabled in the ordinary launcher.

Two additional private offscreen probes exercised nonoverlapping root
self-copies using either a texture barrier or a temporary image, with shader
or blit presentation and finish-after enabled. One repainted the old source
with a GPU clear; the other immediately uploaded over it from the CPU. Both
preserved a title pattern while repainting only the client body: 15,088 sampled
pixel checks passed in total. These simplified probes did **not** reproduce
the live fault. They include readback and omit Xorg's actual damage, state and
window-manager sequence, so they do not validate E or exclude a driver bug.

The next focused control is prepared as
`leased-pattern.py --title-only-control --screenshot-dir PATH OUTPUT`.
It holds the title constant through movement, captures the frame before
changing its title, then issues only `XStoreName` at the repair point. It
repeats movement and another title-only update, never requesting a resize
after initial placement. Phase-entry request types and actual geometry are
recorded so recovery can be checked without conflating it with resizing.
This new workload has not yet run on HDMI; it needs the usual user-confirmed
unplug/arm preparation. It uses the existing finish-after diagnostic runtime
and requires no rebuild or module change.

If needed, follow that control with tracing of the actual Xorg
window-preservation path. Another blanket wait is not supported as a fix by
these results. No further HDMI session was started during this analysis.
The 581 frames submitted by the screenshot workload are not a performance
or cadence result.

## Title-only control result and manual session

The title-only run completed at 4K30 with 582 submitted frames and 42 root
captures. The user saw no issues. Inspected images show intact titlebars
after movement and **before** the title-only update. The fault did not
reproduce, so this run does not establish that changing the title repairs it.
Xorg exited normally, Android was restored, and the companion reference count
returned to zero. All PNGs pass CRC, decompression and dimension checks; all
capture geometry is stable and every captured client remains 800x600.
See [the result](E-TITLE-ONLY-4K30-20261006.json).

The comparison also revealed an uncontrolled placement difference: Openbox
put the reference frame at (501,199) in the failing screenshot run and
(1518,808) in the clean title-only run. That changes overlap and exposure
relative to the moving window. The phase-entry request sequence also differs,
so this is not a one-variable comparison and does not validate E generally.

A further opt-in `--move-entry-control` workload is prepared but not run.
It explicitly places both managed windows, keeps the reference clear of the
movement path, and compares neither, title-only, same-size configuration-only,
and both phase-entry requests. It repeats those conditions in reverse order,
records pre-entry images, and refuses unexpected starting geometry. It needs
screenshots and four-second phases, for 36 seconds of rendering. Syntax and
argument parsing were checked; live validation is pending.

The user then requested suspending automated experiments and leaving the
ordinary E-enabled stack available for manual testing without the 60-second
limit. The completed diagnostic agent was replaced while HDMI was disconnected.
The unchanged, checksum-verified resize bundle is launched with:

```sh
cd ~/Downloads/hdmi-termux-vblank-min-coexist-usb-bt-bcdf-resize-20261005
sudo -n env -u HDMI_LOS_E_DIAGNOSTIC ./run-agent.sh --candidate BCDEF --no-timeout --capture none
```

The agent's arguments and environment confirm continuous BCDEF with E blits,
the C/D bridge path and F presenter, and no E diagnostic wait. The broker
acknowledges a continuous agent. No automatic test observer or pattern workload
is running. This is a manual-test selection, not promotion of E to the ordinary
launcher default. No launcher, runtime library or Magisk module was changed.

## Subsequent manual acceptance

The user could not reproduce the titlebar issue in manual 4K30 use and accepts
E for continued testing. Firefox corruption during circular corner resizing and
Maps panning stutter remain separate unresolved observations; Konsole resizing
was clean. Preserve the automated titlebar failures above alongside this manual
result. The next live audit found a different controls bundle at 4K60 with older
Mesa and E ABI 1, so it cannot establish an E ABI 2 fix or 60 Hz acceptance.
See [the Firefox/Maps investigation](FIREFOX-MAPS-20261007.md).
