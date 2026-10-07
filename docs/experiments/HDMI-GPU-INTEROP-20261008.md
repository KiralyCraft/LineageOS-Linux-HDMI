KGSL buffer initialization ordering and resize capacity, 2026-10-08

A confirmed producer-ordering gap was found at Xorg -> Mesa allocation handoff.
Xorg's glamor_make_pixmap_exportable copies the original pixmap into new GBM
storage before exporting it. KGSL does not publish that GPU copy as an implicit
DMA-BUF dependency. Mesa could receive the FD and overwrite the allocation before
Xorg's initialization finished. The deployed kernel does not implement the
DMA_BUF_IOCTL_EXPORT_SYNC_FILE / IMPORT_SYNC_FILE ioctls.

The diagnostic one-pixel readback control serialized this initialization and
changed the exact-size resize-pattern result from 11/24 to 24/24 passing frames.
The capacity build with the old Xorg passed 20/24 without that control; its four
failures were newly allocated buffers. Capacity plus readback passed 24/24.
Those controls identified missing ordering; readback is not a runtime fix.

The matched fix keeps the standard DRI3 export reply private until a native fence
from the exporting glamor context succeeds. Pending exports hold their reply and
FDs, IgnoreClient only on the allocating connection, and use SetNotifyFd with the
normal event loop. A copy is not passed off unfinished after a timeout or error.
Client loss and screen shutdown cancel jobs. A per-screen 128-job bound and a
5-second recovery deadline limit pending state. No standard X Sync FD is replaced
with a native sync-file FD. Native fence export can still wait for CPU submission
readiness; GPU execution completion is asynchronous on Xorg's main thread.

Private Present capability 0x08000000 advertises this contract per screen. Mesa
requires it for the C/D pipeline, except when the explicitly requested diagnostic
readback control is used against the old server. The Termux:X11 pacer bits are
not advertised: the live response was 0x08000000, with pacer bits zero.

Mesa commit 065604e4b9b333ac4bf3494f9f604364878d1ca6 also retains shared presentation
storage in 128-pixel capacity buckets. Private application render targets and
viewport dimensions remain exact. Slots store their logical dimensions and
Present carries the corresponding XFixes valid/update region. Both private XCB
connections negotiate XFixes first. Full-screen allocation remains exact.
The three-generation, three-slot and 512 MiB backing-size budgets remain intact.

Unattended software tests ran on the real KGSL/Freedreno FD740 device, with a
3840x2160@30 leased Xorg desktop, BCDEF including E, and native-fence readiness.
The private Xorg 26.1 RC3, glamor, modesetting, GLX and evdev set was built and
validated together on root@192.168.104.201. Upstream tests: five passed, optional
XTS skipped. Production export-reply and cache fixtures passed ASan/UBSan;
export cleanup also passed native-host LeakSanitizer. On-phone native KGSL BO
handle and all eight device-selection tests passed.

| Software correctness case | Passed |
| --- | ---: |
| Capacity resize, changing quadrant/frame patterns | 24/24 |
| Exact-size resize, same patterns | 24/24 |
| Capacity resize with 4x MSAA | 24/24 |
| Capacity resize with worker-context copy instead of integrated resolve | 24/24 |
| Deferred immediate-mode draw before swap | 8/8 |
| Full-screen / near-full-screen 4K allocation boundaries | 12/12 |

All 116 captured frames passed sampled color/geometry checks, including the
initial frame. The diagnostic allocation readback and extra glFlush control
were disabled. Captures read the X root after settling; they do not validate
physical scanout cadence or end-to-end latency. First-frame and resized-frame
screenshots were visually inspected as well; their viewports and title bars
were intact. Raw images and logs remain in RAM-backed /tmp.

A first timing series was excluded because XSync did not establish that the WM
had applied each requested size. The corrected benchmark waits for actual
geometry, then draws. It uses no screenshots/readback and targets 30 submissions
per second. Every recorded geometry equaled its request. Controls were run in
exact -> capacity -> capacity -> exact order on the same server/Mesa build.

| Unique resize phase, two runs each | Exact storage | Capacity storage |
| --- | ---: | ---: |
| Mean configure + drawing/swap, ms | 20.11 / 21.48 | 14.42 / 14.16 |
| p95 configure + drawing/swap, ms | 22.96 / 27.89 | 30.73 / 34.33 |
| Mean swap call, ms | 11.95 / 13.60 | 3.49 / 3.74 |
| Generations during entire run | 182 / 182 | 14 / 14 |
| Allocation/import total, seconds | 1.881 / 2.172 | 0.297 / 0.308 |

The mean work drops substantially, but capacity-boundary allocations are more
expensive individually. In these runs the total resize p95 was worse with
capacity; this must not be described as a general tail-latency improvement.
The 5 ms WM-settling polling also contributes to measured configure time.
No thermal/governor matching or statistically broad study was performed.
These are software request/call timings, not displayed FPS. The exact-size
control remains available as MESA_KGSL_HDMI_RESIZE_CAPACITY=0.

The candidate is experimental. The earlier stock-glxgears Expose/event replay
finding remains separate from this initialization race and from deferred-draw
ordering. No 4K60, Firefox/Maps or OpenJK acceptance was performed here.
The next resize optimization should move generation allocation out of the
interactive path or remove the initialization transfer through an explicit
allocator contract; increasing a bucket size merely to hide p95 spikes is not
an architectural remedy.

Native broker, installed Magisk module, input bridge, loaded companion and CPU
policy were not changed during this test. A reversible read-only bind mount and
the same agent's connected restart selected the matched graphics runtime.
The original Downloads bundle is preserved under that mount for rollback.
A self-contained Downloads runtime includes complete Mesa patches on the
vblank-minimal baseline, private Xorg patches, USB/Bluetooth code, launcher,
matched binaries and checksum manifests. Broker 0.4.4 remains separately staged
for manual installation; the current same-agent restart works with 0.4.3.
