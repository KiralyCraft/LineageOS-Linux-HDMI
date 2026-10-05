# Candidate B: standalone timing companion and asynchronous TearFree

The first candidate retains the October 2 Mesa allocation/bridge architecture,
USB/Bluetooth fan-in and installed composer plane reservations. It adds a broker
owned timing session and native-fence-driven Xorg TearFree. It does not replace
KGSL, MSM/SDE or the installed kernel.

## Standalone testing

The assembled folder is
`~/Downloads/hdmi-termux-vblank-min-coexist-usb-bt-timing-async-20261005`.
All C/C++ and kernel builds run on `root@192.168.104.201`.

`./load-companion.sh` verifies kernel release and artifact hashes, loads the
matching `.ko` with ordinary `insmod`, and checks its exact build identity. If
already loaded, it checks that identity without replacing or unloading it.
The query includes invalid-interface/invalid-lease tests and creates no genuine
session. This operation can run while the old HDMI session is active.

**Unplug HDMI and confirm before running the broker handoff or new launcher.**
`./switch-broker.sh candidate` refuses while DP-1 is connected. It starts the new
Android broker from `/data/local/tmp`, leaving Magisk's broker file untouched.
The installed service is a one-shot broker invocation, not a restart loop.

Then launch `./run-agent.sh --timeout` for the first bounded test and use the
existing Android arm/connect procedure. After validating the test, launch
`./run-agent.sh` for the usual continuous session. The normal defaults require
the timing companion and asynchronous TearFree. The launcher does not load or
install a module implicitly.

For comparison, the same matched Xorg supports
`--tearfree-completion sync`; `--timing-guard off` is a separate compatibility
switch. Do not change the live lease's mode during comparisons.

Rollback after unplugging: run `./switch-broker.sh rollback`, then launch the
original October 2 folder's `run-agent.sh`. The companion can stay loaded and
inactive. Normal `rmmod hdmi_companion` is optional only after sessions have
stopped; there is no forced unload. Reboot also returns to Magisk's original
broker. No Magisk install or reboot is part of this standalone test.

## Ownership and correctness

Only the broker owns the companion session FD. It validates a genuine external
DRM lease/object set, obtains one vblank reference, and waits for advancing
count/timestamp accounting before delivering the lease to the X server. Failure
restores Android. STOP/final FD release, invalid lease, mode/power changes,
startup timeout and suspend all drop the owned reference. A 500 ms liveness
check supplements explicit broker teardown; it is not per-frame renewal and
does not consume Xorg's DRM event queue.

Xorg's pending copy is separate from its pending DRM flip. Damage is snapshotted
and new damage accumulates independently. The same glamor context submits the
copy and exports its completion fence. The server handles requests while that
fence is outstanding. Only successful readiness queues a real flip/event.
Disable, modeset, VT loss and shadow destruction cancel callbacks and drain GPU
use before releasing the destination. Teardown/startup can still wait; the change
targets the steady-state TearFree GPU completion wait.

Fence export can still wait for CPU-side submission readiness. The optional
`HDMI_ASYNC` summaries in `/run/hdmi-los/Xorg.1.log` record copy/export duration,
fence-to-callback duration and damaged rectangle area. Fence-to-callback duration
includes event-loop dispatch latency; it is not pure GPU execution time.
Timeout, an error-completed fence or a failed flip terminates the candidate
instead of displaying unfinished storage; agent/broker teardown restores Android.

## Validation boundary

The exact kernel sources/configuration, symbol versions and CFI checks passed.
The functional module passed standalone load, ABI/error-path queries and unload.
The new Xorg modules compiled together from 45 units using the frozen O2 build,
and their native-fence exports were checked. Native tests exercise capability
rejection, failed creation/enable, invalid generation, startup timeout, session
invalidation and idempotent cleanup. Existing input/tracer tests also passed.

**A real timing session and asynchronous HDMI presentation are still pending
the user-approved unplug/restart test.** This build does not yet establish fresh
live scheduling, improved latency, physical 4K cadence, or all kernel lifecycle
cases. Magisk packaging for production follows these live gates. Persistent
Mesa bridge generations, same-context resolve, copy backends and a kernel
presenter remain later candidates.
