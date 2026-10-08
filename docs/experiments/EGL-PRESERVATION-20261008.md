# EGL preserved back buffers, 2026-10-08

Follow-up: the R13 results below establish settled-image correctness only.
Immediate checks subsequently exposed lost contents in the first render-buffer
allocations. R14 retains the same render buffer, following the ordinary loader.
See [the immediate validation report](EGL-PRESERVATION-IMMEDIATE-20261008.md).

The HDMI pipeline skipped the ordinary DRI3 loader's copy-back bookkeeping when
an EGL surface requested `EGL_BUFFER_PRESERVED`. Once rapid presentation rotated
the private render buffers, drawing only the changed rectangle left stale tiles
from an older frame. Slow tests that reused a single buffer hid the bug.

Mesa `08a3ec46412f3a2dbb708cecd07bb82ca85384d9` records the just-rendered back
buffer as `cur_blit_source` before advancing the ring. The existing loader then
preserves its contents through its GPU copy-back path. No producer or consumer
fence, native completion, or Present COMPLETE/IDLE requirement was removed.
Ordinary buffer-age clients continue repairing their own accumulated damage.

The independent test uses sixteen different tiles and changes one per frame.
It compares the displayed contents against a CPU-maintained scene. In burst
mode it submits 240 swaps without extra per-frame XSync or resize requests,
then checks all sixteen tiles after each thirtieth swap. These are eight settled
X root image checks, not 240 individual image checks or optical scanout proof.

| Case | Swaps | Images checked | Images correct |
| --- | ---: | ---: | ---: |
| Old preserved implementation, burst | 240 | 8 | 0 |
| Corrected preserved implementation, burst | 240 | 8 | 8 |
| Corrected ordinary buffer-age implementation, burst | 240 | 8 | 8 |
| Corrected preserved implementation without buffer-age queries | 240 | 8 | 8 |
| Corrected EGL full repaint and damage-aware resize regression | 24 | 24 | 24 |
| Corrected GLX, 4x MSAA and swap-interval transition regression | 24 | 24 | 24 |

`tests/graphics-contract/egl-partial-repaint.py` contains the reproducible partial
repaint test. It uses the inherited hardware HDMI environment and checks every
swap's EGL/GL result, in addition to the sampled images. `--preserved --burst
--no-age-query` ensures preservation does not rely on query side effects.

The matched Mesa stack was built on `root@192.168.104.201`; source blob and artifact
hashes match. The production allocation/cache fixture passed with sanitizers.
Builder device-dependent tests skipped because the builder has no KGSL device;
the graphics regressions above ran on FD740 on the phone at physical 4K30.
The accompanying JSON records artifact hashes, test counts, and limitations.

This fix is independent of Firefox's remaining abrupt resize clipping. The
valid earlier browser trace contains ordinary asynchronous Present requests
with target MSC zero, rather than preserved swaps. Parent/child geometry differs
occasionally, but clipping also appears when their dimensions agree. The later
R13 browser checkpoint ended before resize automation and cannot establish
resize timing. A future capture must overlap the actual activity.

The optional trace checkpoint in `frame-pacing-probe.c` keeps recording in RAM
and writes once after `HDMI_FRAME_TRACE_SECONDS` (1 through 300). Launch the
actual Firefox ELF when preloading it; a shell wrapper's trace file survives
exec with the same PID and prevents the application's O_EXCL open.

Raw screenshots, traces, private browser profiles and intermediate libraries
remain in RAM or the build server's SSD. Only source, compact reports and final
restartable bundles are persisted on the phone's SD card. Physical 4K60,
browser resize correctness, Maps/Teams interaction, and intermittent pacing
tails remain open.
