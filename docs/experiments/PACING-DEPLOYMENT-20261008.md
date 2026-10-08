The 4K30 continuous desktop now uses the verified self-contained folder:

`~/Downloads/hdmi-termux-vblank-min-coexist-usb-bt-bcdef-xorg26-pacing-20261008`

Use its `./run-agent.sh` for future launches, following normal stop/unplug/arm
when replacing the agent. Defaults are BCDEF, hardware KGSL, continuous operation,
one future synchronized frame, +1 MSC lead and an 8 ms frame repaint reserve.
The current agent still names the original resolve-order folder; a read-only bind
mount in the private chroot namespace maps that path to the new persistent folder.
The underlying original files were preserved. Reboot removes the bind mount.
The previous interop-capacity Downloads folder is the rollback runtime.

Exact manifests, matched Mesa patches, the private Xorg series, native companion
and USB/Bluetooth payload, build checks, and the validated glxgears event-loop
patch are included. The normal /usr/local/bin/glxgears uses that corrected loop;
/usr/bin/glxgears and its package remain intact. No Magisk module was installed.
All raw diagnostics, screenshots and temporary game profiles stay in RAM /tmp.

The persistent connected restart passed another 24/24 changing-pattern image
checks. Xorg reports FD740 glamor, async TearFree, producer-ready DRI3 exports,
kernel deferred presentation and the new repaint scheduler. The broker reports
Linux owns 3840x2160 at 30 Hz in continuous mode. The existing highest-core power
guard remains installed, CPU7 is online, and only owned trace instances were
removed. See PRESENT-WAKEUPS-20261008.md for controlled measurements and remaining
limitations: one isolated three-minute cadence miss, active-resize root snapshots,
allocation tails, browser usability and 4K60 are not fully settled.
