# Connected HDMI session restart

This candidate updates the `hdmi-los` broker and chroot agent. The verified
composer payloads, kernel companion, redesigned Android app, USB/Bluetooth bridge
and graphics libraries are preserved byte for byte. `hdmi-los-power` remains a
separate module. Both the embedded runtime and the copied Downloads runtime get
the agent fix; each retains its existing Mesa/Xorg build and launcher.
The new broker is compatible with the existing Downloads-based agents and APK.
Version 0.4.3 includes the corrected acquisition acknowledgement handling from
0.4.2 and the subsequent offline lifecycle review fixes:

- Startup failures use the same correlated STOP acknowledgement as normal
  shutdown, before stopping timing and releasing the composer lease. A failed
  stop disconnects the agent and explicitly reports forced recovery. The
  original startup error survives cleanup.
- Each main-loop dispatch consumes one readiness snapshot, then re-polls before
  processing another descriptor. Watchdogs are still checked after each dispatch.
- Disconnect generations belong to the active session, so an unplug/replug
  during the initial status query also cancels connected restart. Mode recovery
  during startup is deferred until Xorg and its lease have been stopped.
- Agent reads are nonblocking and framed across command timeouts. Partial or
  late replies cannot extend a command deadline or masquerade as a STOP ACK.
- Child cleanup observes exit with waitid/WNOWAIT instead of polling kill(pid,0)
  on zombies. The leader remains unreaped until remaining group members have
  been terminated, preventing PID/group reuse during cleanup.

Host fixtures run a complete pause/resume through the production event dispatch
and inject faults at agent preparation, every composer acquire phase, and Xorg
startup. They test delayed STOP acknowledgement, stale/failed replies, partial
stream framing, disconnects before/during the snapshot and during startup,
prompt process exit, surviving descendants, and forced termination. Android
startup gates are replaced only in the host fixture; real kernel/composer/input
and physical scanout validation remain pending.

The first 0.4.1 live pause released its timing reference (gets=1, puts=1), restored
Android and retained connected 4K30. Re-acquisition failed because phase replies
do not contain timing; dedicated STATUS queries now supply it.

Version 0.4.3 (`6d19c959b8c4`) passed a user-authorized connected restart at
3840x2160@30 on October 7. Linux returned normally, confirmed by the user. The
command completed in 4.28 seconds, Xorg received a new PID, generation 5 balanced
its timing reference (gets=1/puts=1), and generation 6 started valid. Acceleration,
asynchronous TearFree, E ABI 1 and the kernel presenter were active afterwards.
The continuous desktop remained running. This validates one 4K30 restart;
4K60, repeated restarts, runtime replacement and live failure injection remain
untested. See `docs/experiments/CONNECTED-RESTART-20261007.md`.

Install the candidate ZIP manually in Magisk and reboot once. A subsequent
Linux desktop restart does not require an Android reboot or unplugging HDMI.
One connected restart at 4K30 is physically confirmed. Native compilation and
socket-based lifecycle tests run on the authorized build server.

Use the new Downloads copy named
`hdmi-termux-vblank-min-coexist-usb-bt-bcdef-xorg26-restart-20261007` for the agent
cleanup fix. Its run-agent.sh retains BCDEF, E ABI 1, asynchronous TearFree,
fence release, low-latency Mesa queue, and continuous-session defaults. The
previous Downloads folder stays available for rollback. Installing Magisk does
not replace an agent in an existing Downloads directory.

From the chroot, use `scripts/hdmi-control.sh` in this repository (or
`./hdmi-control.sh` in the new Downloads copy):

```sh
./scripts/hdmi-control.sh status
./scripts/hdmi-control.sh restart
```

`restart` closes the Linux desktop and its applications, returns HDMI to Android
briefly, and starts the same registered agent's runtime. Save application work
before this deliberate operation. It does not automatically retry a crash.

To select another prepared Downloads bundle:

1. Run `./scripts/hdmi-control.sh pause` and wait for success.
2. Stop the old run-agent launcher and start the new bundle's run-agent.sh.
3. Run `./scripts/hdmi-control.sh resume` within five minutes.

`pause` waits for a request-correlated agent stop acknowledgement, stops the
timing/presenter session, releases the lease and input grabs, and verifies the
composer returned to Android. It retains the negotiated mode and its recovery
journal. `resume` requires three stable samples at that exact timing, then uses
the existing verified prepare/pause/create/start handshake with a fresh lease
and companion generation. It checks timing during every lease-acquire phase.
The resumed session inherits continuous/bounded behavior from the registered
agent. Its normal shutdown restores the saved preferred mode.

These operations are root-only and cannot specify a mode. Unplugging, a changed
timing, missing readiness, expiry, or a failed stop/start cancels the token;
Android recovery remains the fallback. An absent replacement agent leaves the
successful pause available until its deadline. `disarm` cancels it explicitly.
Normal arming and preset changes still require the existing unplug procedure.
The volume-key escape and composer watchdog remain active during Linux use.

Rollback: install the retained `hdmi-los-bcdef-12df706428a0-magisk.zip` and reboot.
It restores the previously installed broker and removes connected commands.
The same Downloads Xorg/Mesa bundles remain usable in either case.
