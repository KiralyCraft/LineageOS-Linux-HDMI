# HDMI drawable preparation and resolve ordering, October 7

Mesa `c31019a886f955f516eb2c7af2ceb9279ee43d20` corrects Candidate D's
ordering of deferred drawing and the copy into Xorg-owned shared storage.
The active runtime is
`~/Downloads/hdmi-termux-vblank-min-coexist-usb-bt-bcdef-xorg26-resolve-order-20261007`.
Its private Xorg 26, native agent, companion, USB/Bluetooth input and launcher
were copied from the preceding connected-restart bundle. Only the matched
Mesa build and its source/build metadata changed. The desktop runs continuously
at 3840x2160@30; the user confirmed Linux returned after the switch.

## Confirmed ordering defect

The integrated resolve previously called `dri2_blit_image()` before normal
drawable preparation. A later flush submitted deferred compatibility-profile
vertices; the outgoing fence covered completed GPU commands but could not
repair the incorrect command order. MSAA preparation also belongs before the
shared-image transfer.

The fix inserts the transfer into the existing drawable pre-flush callback,
after pending vertices, MSAA resolve, HUD and back-buffer resource preparation.
It then prepares the shared destination and performs one final end-of-frame
flush and native-fence export. The resolve itself does not recursively flush.
Normal DRI/Termux:X11 callers retain their existing flush behavior. No public
loader vtable or drawable ABI was changed, and no per-frame CPU completion
wait or application-specific viewport override was added.

The diagnostic clears its buffer to a poison color, issues an immediate-mode
quad with a changing frame color, then swaps without an intervening query,
state change or explicit flush. It drains all X events, uses actual geometry,
and checks nine interior pixels in each settled root screenshot.

| Path | Full 8-frame result | Frames 2-8 |
|---|---|---|
| Previous integrated resolve, Mesa `557306b5c` | 0/8 | All retain poison color |
| Previous integrated resolve + diagnostic application `glFlush` | 7/8 | All correct |
| Previous worker-context resolve | 7/8 | All correct |
| New integrated resolve, no application flush | 7/8 | All correct |
| Previous integrated resolve, 4x MSAA | 0/8 | Incorrect |
| New integrated resolve, 4x MSAA, no application flush | 7/8 | All correct |

**The first captured viewport is black in the controls and the corrected
candidate.** Waiting for MapNotify before creating drawable attachments did
not eliminate it. This remains a separate startup-content issue; these are
not 8/8 passing tests. The checks cover viewport pixels, not titlebars or
physical scanout. Screenshots synchronize Xorg readback and therefore are
correctness probes, not latency or displayed-FPS measurements.

## Resize cost remains a separate performance issue

The unique-size workload performs ten seconds of fixed/resize/fixed drawing,
with no screenshots or GPU readback. It drains X events, checks actual window
geometry and targets 30 submissions per second. The earlier glxgears replay
finding remains separate: its own Expose-driven event backlog can lag behind
the window geometry. See `GLXGEARS-EVENT-BACKLOG-20261007.md`.

| Single run | Unique sizes | Resize swap mean | Resize swap p95 | Allocation/import total |
|---|---:|---:|---:|---:|
| Previous Mesa, diagnostic `glFlush` | 152 | 26.37 ms | 52.35 ms | 3.87 s / 147 generations |
| New Mesa, diagnostic `glFlush` | 179 | 17.00 ms | 20.72 ms | 2.93 s / 178 generations |
| New Mesa, ordinary swap | 179 | 16.00 ms | 20.38 ms | 2.61 s / 180 generations |

The explicit-flush controls use the same test behavior; the ordinary new path
does not need that workaround for correct settled contents. These are single
sequential runs with different actual size sequences and uncontrolled power
residency, not an interleaved statistical A/B or proof of physical frame rate.
The lower request-side durations are encouraging, but allocation/import
remains dominant during unique-size resize. Reusing capacity across logical
sizes is a separate optimization and is not implemented by this patch.

The instrumentation now separates `drawable_prepare`, callback enqueue
(`resolve_submit`) and final fence export. A microsecond enqueue duration is
not the copy's GPU execution time. Native-fence readiness also includes
notification/scheduling delay and must not be called pure GPU execution.

## Build and deployment

Compilation ran only on `root@192.168.104.201`, under
`/bigdata/mesa-sync-build/package-combined/hdmi-resolve-order-20261007`.
The source blob verification passed, the matched Gallium/GLX/EGL/GBM/DRI
components were packaged together, and the production retirement/cache
fixture passed with ASan/UBSan. Hardware-dependent KGSL tests were skipped on
the builder. Gallium SHA256 is
`e07735ce309bcf8540c9e883fcd22997eaf55f8fa2a7842656b1ce29019c6859`.

Captures, logs and complete test records stay in RAM-backed
`/tmp/hdmi-graphics-resume-20261007`; the committed JSON contains compact
results and source identities. The runtime was staged in RAM and copied once
to Downloads with complete checksum verification. Its prior folder is the
rollback runtime. This Mesa change needs no Magisk installation.

The first paused runtime replacement exposed an independent broker 0.4.3
registration defect: Status reported WAITING and omitted the accepted agent's
CONTINUOUS capability. The replacement `--no-timeout` agent rejected the ACK,
so Android retained the output. Disarming and using the existing root
diagnostic Start restored Linux with the new Mesa, without unplug or reboot.
Broker 0.4.4 explicitly acknowledges accepted registration capabilities and
has a host regression test using the real AcceptClient handler. The corrected
module must still be installed manually before validating paused replacement.

## Qualcomm/PR96 review follow-up

The supplied Qualcomm review supports retaining explicit dependencies and
submission storage lifetime. It does not make successful DMA-BUF fence export
proof that KGSL producer dependencies were published into that reservation
object. Native completion also includes kernel event-worker notification,
which is distinct from GPU execution.

The current merged source already marks failed Present fence waits as failed
and leaves the X wait fence untriggered; the supplied observation about the old
PR head triggering after failure must not be reapplied as if still current.
The DMA-BUF-export capability proxy and accepted-submit/failed-fence-export
cleanup in `kgsl_ringbuffer_sp.c` remain separate audit targets. This ordering
patch does not claim to fix either, or to resolve Firefox/Maps performance.
