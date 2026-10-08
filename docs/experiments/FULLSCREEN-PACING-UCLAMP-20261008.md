# Fullscreen 4K30 pacing and session utilization clamp

## Result

The occasional fullscreen `glxgears` hiccup was a client scheduling delay, not
a missed display completion or a slow KGSL job. With LXPanel active, Mesa's
submission thread was runnable for about 4.97 ms before the scheduler ran it.
The associated GPU job then completed in about 3.39 ms. X Present nevertheless
reported a direct flip on every refresh with consecutive MSC values.

Pausing LXPanel removed almost all of the long swap-return intervals, and the
user reported that the fullscreen animation looked smooth at 30 Hz. A scoped
`uclamp.min=512` hint on the test process produced an even lower measured tail
without stopping the panel. Moving the test into Android's `top-app` cgroup made
the tail worse because that cgroup still had `cpu.uclamp.min=0`.

This result supports a session-scoped scheduler hint for Xorg, the desktop and
their descendants. It does not support globally changing Android cpusets or
keeping a fixed CPU frequency.

## Controlled results

All tests used the same 3840x2160 at 30 Hz leased-Xorg session, the R14 Mesa and
Xorg payload, fullscreen `glxgears`, a hidden cursor, a five-second warmup and
Direct Flip Present completions.

| Configuration | Swap-return interval p95 | p99 | Maximum | Present MSC deltas |
|---|---:|---:|---:|---|
| Panel active, normal scheduler attributes | 35.506 ms | 38.716 ms | 41.886 ms | 1666 of 1666 were +1 |
| LXPanel paused | 35.335 ms | 36.865 ms | 41.867 ms | 1065 of 1065 were +1 |
| Android `top-app` cgroup | 36.103 ms | 39.679 ms | 47.190 ms | 1068 of 1068 were +1 |
| `uclamp.min=256` | 34.647 ms | 37.434 ms | 40.063 ms | 1066 of 1066 were +1 |
| `uclamp.min=512` | 34.331 ms | 36.197 ms | 38.235 ms | 1068 of 1068 were +1 |

The mean swap-return interval remained 33.333 ms in every case. The improvement
is in the latency tail rather than average frame rate. The user visually judged
only the panel-paused control in this group and reported it smooth; a physical
visual comparison of the packaged `uclamp.min=512` session remains required.

## Correlated scheduler and KGSL evidence

The panel-active ftrace capture recorded this sequence for one delayed frame:

```text
46839.044776  glxgears:sq0 becomes runnable
46839.049742  glxgears:sq0 is scheduled                 4.966 ms later
46839.049840  KGSL submits context 31 timestamp 413
46839.053230  KGSL retires context 31 timestamp 413     3.390 ms after submit
46839.053802  KGSL delivers the completion-fence event
```

LXPanel and Xorg ran repeatedly while the Mesa submission thread waited,
primarily on CPUs 0 through 2. This separates the pre-submission CPU delay from
GPU execution and later fence notification.

## Implementation

`run-agent.sh` now defaults `HDMI_LOS_SESSION_UCLAMP_MIN` to 512 and accepts
`--session-uclamp-min 0..1024`. The native agent applies the value before
executing Xorg and before dropping privileges and executing LXDE. Threads and
child applications inherit it normally. Setting it to zero disables the hint.

This mechanism is separate from the HDMI CPU power guard. The power guard makes
the performance cores and their normal frequency range available while the
phone panel is off. The utilization clamp tells the scheduler that the leased
desktop has a latency-sensitive demand floor. It does not force a frequency,
pin a CPU, change Android cgroups, or survive after the session processes exit.

The live kernel accepted the 56-byte `sched_attr` ABI with
`SCHED_FLAG_UTIL_CLAMP_MIN`, and a forked child inherited the effective value.
The rebuilt ARM64 agent rejects values above 1024. The launcher can be rolled
back without changing the binary by setting `HDMI_LOS_SESSION_UCLAMP_MIN=0`.

## Limits

This experiment establishes a clean 4K30 result. It does not establish physical
4K60 behavior, browser smoothness, or the optical result of the packaged
session-wide value. Thermal and vendor power limits remain authoritative.
