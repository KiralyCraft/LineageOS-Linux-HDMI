# Connected restart lifecycle review and correction

The first live connected restart stopped Linux, balanced timing-reference
ownership (generation 5, gets=1, puts=1), and restored Android at 4K30. Acquisition
then failed because the broker treated a phase acknowledgement as a timing
report. Commit a466390 changed that validation to dedicated STATUS requests.
Version 0.4.3 (`6d19c959b8c4`) subsequently passed one live connected restart
at 3840x2160@30 with HDMI left plugged in. The user confirmed the Linux desktop
returned normally. The restart command completed in 4281.18 ms; its correlated
STOP acknowledgement took 2041 ms. Xorg PID changed from 10774 to 12441, and the
old process was gone. Timing generation 5 stopped with gets=1/puts=1; generation
6 started valid with gets=1/puts=0. Both sides used the matching Xorg 26 Downloads
runtime with FD740 acceleration, asynchronous TearFree, release fences, E ABI 1,
and the kernel presenter. The follow-up broker status remained active at 4K30
in continuous mode, with no startup/restore failure or TearFree flip failure in
the test logs. The desktop was left running.

This validates one connected restart at 4K30. It does not validate 4K60, repeated
restart stress, live fault injection, changing runtime while paused, or physical
USB/Bluetooth input in this test. Those remain separate acceptance checks.
Raw before/after snapshots and logs were kept on RAM-backed /tmp, not the SD card.
The packaged manifests remain immutable records of their pre-deployment status.

An offline review then reproduced four other defects with unprivileged host
fixtures and identified a fifth in the startup failure path:

| Defect | Previous evidence | Corrected behavior |
| --- | --- | --- |
| Reusing poll readiness after command handling | The command drained a queued event; the next read waited for the receive timeout and returned invalid | Dispatch one readiness snapshot, then poll again; keep watchdog checks after dispatch |
| Losing unplug/replug during the initial pause query | Pause returned OK with a resumable token after observing both events | Compare disconnect generation against the running session, including the command's preliminary STATUS query |
| Blocking stream read defeats deadline | A 40 ms wait accepted a split READY at 222 ms | Nonblocking incremental framing; enforce the original deadline and preserve partial bytes for later STOP |
| Waiting on a zombie as if still running | A child with default SIGTERM handling consumed 2011 ms | Observe exit without reaping, finish process-group cleanup, then reap the pinned child |
| Early revoke on failed startup | Source sent STOP and immediately stopped timing/released the lease | Share normal acknowledged stop/restore; keep the originating failure separate from cleanup replies |

`build-support/build-connected-restart.sh SOURCE BUILD` builds the Android
broker and Linux ARM64 agent on the build server. Its host fixtures use ASan,
UBSan and default leak detection, run as `nobody`, and operate on socket pairs
and disposable child processes. They do not open Android input/DRM devices.

The complete transaction fixture replaces only Android startup prerequisites
and the authorized command entry. It executes the production poll dispatch,
pause, three stable-mode samples, agent preparation, all three composer acquire
phases with phase-only acknowledgements and separate timing replies, lease-FD
transport, agent startup, and normal stop. It injects failed agent preparation,
failed acquire at every phase, failed Xorg startup, unplug during startup, and
unplug/replug before or during the pause snapshot. Each stop deliberately sends
an old failure and wrong-ID READY, withholds the real STOP acknowledgement, and
checks that no composer RELEASE arrives prematurely.

Additional tests cover split messages across a deadline, EOF, a missing STOP
acknowledgement, expired session deadlines during teardown, final READY plus
HUP, prompt and already-exited children, descendants surviving their leader,
and TERM-resistant children requiring KILL. The fixture does not model real
GPU work, vblank reference ownership, composer locks, or physical scanout.
Those remain live acceptance checks, with explicit user readiness before restart.

Packaging verifies the build-source and artifact hashes. It updates the broker
and agent in the embedded runtime and copies the latest Xorg 26 Downloads
bundle with the new agent. It verifies other original payloads byte for byte,
including each runtime's graphics, launcher, input bridge, companion and APK.
Magisk installation and display restart are not performed by packaging.
