# Connected HDMI session restart

This candidate updates the `hdmi-los` broker. The verified installed BCDEF
composer payloads, kernel companion, redesigned Android app and bundled runtime
are preserved byte for byte. `hdmi-los-power` remains a separate module.
The new broker is compatible with the existing Downloads-based agents and APK.

Install the candidate ZIP manually in Magisk and reboot once. A subsequent
Linux desktop restart does not require an Android reboot or unplugging HDMI.
Physical connected restart validation is pending. Native compilation and
socket-based lifecycle tests run on the authorized build server.

From the chroot, use `scripts/hdmi-control.sh` in this repository:

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
