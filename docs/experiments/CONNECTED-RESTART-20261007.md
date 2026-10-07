# Connected restart lifecycle review and correction

The first live connected restart stopped Linux, balanced timing-reference
ownership (generation 5, gets=1, puts=1), and restored Android at 4K30. Acquisition
then failed because the broker treated a phase acknowledgement as a timing
report. Commit a466390 changed that validation to dedicated STATUS requests.
Physical restart acceptance of the revised implementation is still pending.

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
