# Session scheduling and KGSL fence notifications at 1080p60

The adapter's supported test modes are 4K at the exact Android value 30.000002 Hz
and 1080p at 60.000004 Hz. The latter was selected before Linux takeover. The
monitor required a physical replug; a working live mode switch is not claimed.
Linux then ran at 1920x1080, a 148.5 MHz pixel clock and 60 Hz refresh while the
phone remained Dozing. No Magisk module or kernel driver was replaced.

The runtime retains Mesa `eff5c2203`, Xorg 26, BCDEF, the vblank-minimal/coexist
baseline and USB/Bluetooth input. Xorg and the LXDE process tree have effective
`uclamp.min=512`. The test launcher separately sets that value on owned clients
because its root shell is not a descendant of LXDE.

## Application pacing

Five warmup seconds are excluded. Present events are counted only in the
private worker stream, excluding the duplicate subscription. These are
completion and swap-return measurements, not optical or input-to-screen latency.

| Workload | Swap-return p95 | p99 | Maximum | Adjacent completion MSC deltas |
|---|---:|---:|---:|---|
| Fullscreen glxgears, client hint 0 | 18.432 ms | 19.662 ms | 25.111 ms | 2134 × +1 |
| Fullscreen glxgears, client hint 512 | 17.220 ms | 17.723 ms | 23.747 ms | 2135 × +1 |
| Maximized glxgears, hint 512 | 17.634 ms | 18.052 ms | 33.817 ms | 1824 × +1; 4 × +2 |
| Fullscreen ioQuake3, interval 1, hint 512 | 18.115 ms | 18.809 ms | 19.723 ms | 2733 × +1 |

Fullscreen tests used Direct Flip completions. The maximized test used Copy
completions through the desktop and TearFree path. Quake used the recorded demo,
the OpenGL2 FD740 renderer and a copied configuration in RAM; the user's config
and assets were preserved. It finished normally after about 51 seconds.

The client hint reduces the fullscreen swap-return tail with the existing
one-pending-frame and +1 MSC policy. It is not a change to swap interval, fence
ownership, layout, buffer reuse or display timing. A repeatable optical result
and interaction latency comparison remain separate acceptance checks.

## Kernel callback delay

One late Copy completion in the first trace had this sequence:

```text
48706.984437  Xorg GPU copy submitted, context 28 timestamp 9550
48706.986498  GPU copy retired
48706.992058  intended refresh boundary
48706.992442  native-fence callback delivered
48707.008724  actual completion UST
```

The copy itself took about 2.06 ms, but native notification arrived another
5.94 ms after retirement. The first 40-second trace overwrote early events on
some CPUs; only its later complete region was used for that correlation.

A shorter 18-second trace added `kgsl-events` wake and schedule events and had
zero overruns on every CPU. It showed the event worker becoming runnable at
49085.536575, running at 49085.543227 and delivering the native-fence callback
at 49085.543244. Thus 6.652 ms was spent runnable before execution. The worker
allowed CPUs 0-7 but usually ran on CPUs 0-2 with effective `uclamp.min=0`.

## Temporary kernel-worker control

A bounded diagnostic saved the worker's original attributes, applied a hint of
512, and restored zero in `finally`. It did not become a runtime default.

| Same traced workload | Worker hint 0 | Worker hint 512 |
|---|---:|---:|
| Worker wake-to-run p99 | 1.443 ms | 0.123 ms |
| Maximum wake-to-run | 6.652 ms | 1.903 ms |
| Native-fence notification p99 | 1.702 ms | 0.471 ms |
| Maximum notification delay | 6.834 ms | 2.355 ms |
| Completion intervals | 776 × +1; 18 × +2 | 812 × +1 |

With the hint the worker ran mainly on CPU 3. The traced result establishes that
CPU scheduling contributes to delayed KGSL native-fence delivery. It does not
prove the hint solves ordinary desktop pacing: an additional untraced control
recorded 2115 × +1 and 9 × +2, versus 1824 × +1 and 4 × +2 in the earlier
untraced baseline. Durations differed and the observations are not sufficient
to promote the worker hint. Its original zero value was verified after both
controls, and the desktop remains running.

The pinned kernel source and matching `Module.symvers` export
`sched_setattr_nocheck` as GPL. A kernel companion could therefore own a scoped
scheduler vote through its session FD without replacing KGSL. That is only an
integration option; no companion scheduling vote was built or installed here.
It requires repeatable untraced improvement and lifetime/cleanup validation.

## Relation to the Termux vblank-minimal pacer

The original Termux policy remains in the matched Mesa sources. It requires the
Lorie private wait-fence, vblank-completion and frame-timeline capabilities.
Ordinary leased Xorg does not advertise those, so the HDMI tests use the HDMI
pipeline's separate queue and MSC policy. Preserving the older patches does not
mean the Lorie pacer is active here. Both paths retain real completion fences and
server timing; enabling the Lorie policy blindly is not a supported HDMI fix.

## Artifacts and remaining work

The JSON report includes exact summaries and hashes. Raw traces stay in `/tmp`
and are copied with checksum verification to the build server SSD under
`/bigdata/mesa-sync-build/live-evidence/20261008-session-scheduling`. Xauthority
and temporary game profiles are excluded from that archive. Owned ftrace
instances are removed and diagnostic windows exit; the continuous 1080p60
desktop remains active.

Remaining work is to correlate untraced Copy-path misses with repaint timing,
GPU readiness and kernel-presenter submission without the broad scheduler-trace
overhead. No physical 4K60 claim is made for this adapter.
