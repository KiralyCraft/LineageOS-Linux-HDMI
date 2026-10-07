Application pacing and resize evidence, 2026-10-08

The continuous 4K30 desktop uses the prior interop-capacity runtime, Mesa
065604e4b. Experimental Mesa was selected only for owned test processes through
matched library paths. The original game, configuration and recorded demo were
preserved; copies and all raw traces/captures live in RAM-backed /tmp.

ioQuake3_New with its recorded demo0000.dm_71 was run with the original graphics
configuration, renderer opengl2, 1920x1080 windowed, sound disabled. The native
memory-buffered probe records SDL swaps, Present requests and completion events.
It does not change Present flags or fences. SDL loads GL/X11 libraries locally;
the probe must link -Wl,--no-as-needed -lGL -lX11 -lxcb-present so RTLD_NEXT
resolves the actual providers. The initial unresolved-provider attempt exited
126 and is excluded. Probe records are written only at termination.

Main and private XCB connections both subscribe to completion events. Cadence
analysis uses the Present worker's stream, avoiding duplicate counts and apparent
backwards MSCs created by combining delivery timestamps from both subscribers.

| Windowed demo, interval/cap and queue policy | Mean request to completion | MSC changes |
| --- | ---: | --- |
| Async, cap 333, current runtime | 0.44 ms | 7682 same-MSC, 1385 +1 |
| Async, cap 30, current runtime | 0.62 ms | 26 same-MSC, 1321 +1, 13 +2 |
| Vsync, no software cap, original queue | 93.04 ms | 1365 +1, no skips |
| Vsync, future budget two, original +2 lead | 59.62 ms | 1380 +1, no skips |
| Vsync, future budget one, +1 lead | 26.32 ms | 1362 +1, no skips |

Copy completion is not proof of physical presentation. The original synchronized
queue is smooth in this server metric but adds approximately three refreshes of
latency. The software-capped asynchronous run drifts against the 30 Hz display.
No game setting was saved to the original profile.

Mesa 347f3aa50 adds opt-in MESA_KGSL_HDMI_PACED_QUEUE (0 legacy, 1-3 bound) and
MESA_KGSL_HDMI_PACED_LEAD (1/2). Accepted frames remain FIFO; no fence is removed
and no old frame is discarded. Defaults remain legacy 0/+2 during investigation.
The controls affect ordinary interval-one swaps in the low-latency HDMI profile.

A direct fullscreen flip test exposed a necessary lifetime distinction. With
an initial implementation counting completed-but-not-idle scanout as pending,
the budget-one run submitted only one frame, then could not submit its replacement.
The active framebuffer becomes idle only when replaced. Mesa 6ff65620e therefore
separates pending presentation from protected storage: completed scanout does not
count as future work, but its slot and generation remain pinned until IDLE.
An interval-zero request also must wait only for older scheduled presentation to
complete, allowing it to replace that active buffer. Production-function policy
tests cover both paths and stop/error escape with ASan/UBSan.

After that fix, hidden-cursor fullscreen glxgears produced 615 direct-flip
completions after warmup: 613 adjacent +1 MSC deltas and one +4 delta. Typical
request-to-completion was 27 ms. The one approximately 133 ms gap remains
unexplained and requires Xorg/kernel-side correlation before promoting the pacer.
A separate large visible-cursor stock run had 1215 adjacent +1 copy completions,
with a maximum application interval of 38.67 ms; severe recurring stutters were
not established by that 45-second 4K30 run. This does not settle the 4K60 report.

Stock glxgears still processed obsolete ConfigureNotify/Expose events: 146 events
pending and obsolete viewport swaps continuing 3.13 seconds after resizing stopped.
The animated event loop exits at each Expose and generates more draw opportunities
than a synchronized 30 Hz renderer can service during a 20 Hz resize workload.
The correct application fix drains events while animation is active, but retains
its DRAW break while paused. Merely removing that break would prevent paused
redraws. The corrected binary drained its backlog within 39 ms. A separate 'a'
key test produced zero idle paused swaps, two paused resize redraws, updated its
viewport to 1000x750, and resumed animation. It has not replaced /usr/bin/glxgears.

The scripts in tests/graphics-contract/hdmi-* record the current RAM harness and
are path-specific diagnostics. No browser/Teams performance acceptance or broad
physical-display smoothness claim is made. Next priorities are tracing the direct
flip gap, validating interval transitions on real scanout, and reducing eager
three-image allocation at resize boundaries. The full desktop usability goal
remains active.
