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
