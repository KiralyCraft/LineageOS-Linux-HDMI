# Interval-zero HDMI pacing, 2026-10-08

Mesa `107af1757ecac4ce7087e2671c58b5916c2fb146` adds an opt-in HDMI adapter
for the existing Termux interval-zero pacer. The active leased Xorg 26 and its
kernel/native components were not replaced during these tests. The full matched
Mesa set was built on `root@192.168.104.201` and selected only for owned clients.

## Architecture and isolation

The ordinary HDMI pipeline previously sent interval-zero frames with ASYNC and
target MSC zero. The generic Termux pacer requires Lorie-specific capabilities,
which this Xorg does not advertise; its environment setting did not activate
the HDMI backend. Do not fake those capabilities or weaken their checks.

With `MESA_DRI3_PRESENT_MODE=paced`, ordinary interval-zero HDMI swaps now use
the existing pure `loader_dri3_pacer` implementation, with an independent HDMI
clock and frame ledger. The Termux helper, header, unit tests and capability
predicate remain byte-for-byte unchanged. Other swap intervals, unpaced swaps
and explicit OML scheduling retain their existing code paths.

The adapter bounds production to two frames, permits one committed Present,
and uses real completion MSC/UST to select the next refresh and predict the
next production admission. The downstream reserve defaults to 8000 us and can
be varied with `MESA_KGSL_HDMI_PACED_MARGIN_US`. It accounts for the additional
Xorg copy path; it is not a fixed 60 Hz limiter. The clock learns the mode period.

The end-of-swap gate follows **previous** presentation completion. It does not
wait for the current producer's native fence before admitting the next scene.
The worker independently gates Xorg consumption on successful final native
fence readiness. One scene can therefore overlap its predecessor, as in Termux.
Real COMPLETE and IDLE retain distinct lifetime meanings. Resizing or changing
policy resets only timing history; old images and ledger entries remain pinned
until their actual users finish. A timing timeout drops prediction without
declaring unfinished work ready or releasing protected storage.

The first prototype mistakenly waited for current-frame submission, which
also required that frame's GPU completion. Its roughly 5 percent missed-refresh
rate was rejected. Increasing the reserve alone did not correct that dependency.
Changing the gate to the previous commitment restored the intended overlap.

## Matched Quake results at 1080p60

The same recorded demo, renderer, shader cache, output and CPU utilization hint
were used. Tests copied the game profile into tmpfs; original settings/assets
were unchanged. The performance hint was 512 on owned clients and desktop
processes; the vendor KGSL event worker retained its normal hint of zero.

| Test | Swap-return interval p99 | Completion intervals | Missed refresh intervals |
| --- | ---: | ---: | ---: |
| Maximized, interval 1, engine uncapped | 18.391 ms | 2656 | 1 |
| Maximized, interval 0, Mesa paced, engine uncapped | 17.300 ms | 2779 | 0 |
| Maximized, interval 0, Mesa paced, engine cap 65 | 17.344 ms | 2652 | 2 |
| Fullscreen, interval 0, Mesa paced, engine uncapped | 17.178 ms | 2780 | 0 |

These rows exclude eight seconds of startup, separately for each private
Present stream. Maximized clients measured 1920x1054 and retained the actual
NET_WM_STATE maximization flags. Borderless mode avoids the engine recreating
a titled window when its client geometry changes. The engine's resize-triggered
`vid_restart` otherwise loses maximization; context recreation is not a GPU stall.

Maximized completions were Copy mode: they measure presentation scheduling,
not physical displayed FPS or motion-to-photon latency. Fullscreen completions
were Flip mode and every selected interval advanced by one real refresh.
Neither result replaces physical-monitor or scanout-image validation.

The original unpaced interval-zero control produced about 128 frames/s and
many Copy completions in the same MSC. That is unrestricted producer throughput,
not evidence that the monitor displayed 128 distinct images/s.

## Remaining issues and measurement limits

Maximized glxgears with the software cursor visible still exhibited isolated
delays after Present submission, including a
roughly one-second delayed completion after its Present request had already
been submitted. The final paced run had 13 two-refresh intervals and one
61-refresh interval in its selected sample. The early prototype also saw this
long stall. Quake's corrected steady samples did not show it. This issue remains
open; the successful game result must not be generalized to every desktop path.

The subsequent same-size hidden-cursor control had 1357 consecutive one-refresh
intervals, with swap-return p99 16.800 ms and maximum 17.154 ms. It remained Copy
mode, so this was not a switch to fullscreen direct scanout. The control points
to the software-cursor/desktop path as a useful next trace target; a single run
does not establish cursor visibility as the cause of the long tail.

Rapid automated glxgears resizing completed without a pipeline fault or hang,
but geometry changes still have additional cost and this run did not capture
images. It establishes liveness, not absence of artifacts or silky resizing.

This turn did not validate paced 4K30, physical 4K60, Maps, or a live Termux:X11
session using the new binaries. The adapter cannot make the current HDMI
adapter support 4K60. Keep the feature opt-in per application.

The analyzer now matches Present serials to the latest preceding request in
the same worker and keeps cadence separate per window. Serial reuse during
context recreation previously produced negative latency and false MSC gaps.
A synthetic regression covers independent workers, restarted serials/windows
and duplicate events from the main XCB subscription.

## Validation and delivery

- Existing Termux pacer tests passed under ASan/UBSan on the build server.
- Production HDMI cache/lifetime tests plus new policy, previous-commitment,
  mixed-interval and completion-versus-retirement checks passed under ASan/UBSan.
- All 12793 tracked source blobs matched the final Mesa commit before building.
- Six matched Mesa components were rebuilt and hashes verified after transfer.
- The final commit differs from the corrected live prototype only in its
  identification log and Git revision string; final glxgears tests used it.
- Raw traces, temporary profiles and captures use tmpfs; review artifacts are
  archived on the server SSD with profiles and Xauthority excluded.

The self-contained candidate preserves the prior BCDEF agent, private Xorg,
companion and USB/Bluetooth runtime. `run-agent.sh` keeps its existing defaults.
`run-paced.sh` selects its matched client libraries and `MESA_DRI3_PRESENT_MODE=paced`
for one command, inheriting the active HDMI DISPLAY and Xauthority. Use Quake
`r_swapInterval 0` with `com_maxfps 0`, or 65 as the control at 60 Hz. No kernel
or Magisk update is required. Rollback is the original application command or
the preserved prior bundle.

Raw evidence: `/bigdata/mesa-sync-build/live-evidence/20261008-hdmi-pacer`.
The adjacent JSON records the detailed measurements, including rejected
prototype results and separate software/physical validation flags.
