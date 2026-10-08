# Immediate-preservation runtime deployed, 2026-10-08

The continuous leased 3840x2160@30 desktop now runs from:

`~/Downloads/hdmi-termux-vblank-min-coexist-usb-bt-bcdef-xorg26-preserved-retain-20261008`

Launch its `./run-agent.sh` for future sessions. It contains matched Mesa
`eff5c2203`, private Xorg 26 and modules, the vblank-minimal/coexist baseline,
BCDEF, USB/Bluetooth input, companion/native payload, source patches, launcher
and validation manifests. The preceding `...xorg26-preserved-20261008` is the
rollback bundle. No Magisk or kernel update was installed.

The same agent was paused/resumed with HDMI connected. Its original resolve-order
path is a read-only bind onto this persistent folder in its private namespace;
reboot removes that binding. Xorg restarted as PID 22751. Loaded executable,
Gallium, EGL, GBM, glamor and modesetting hashes match the bundle. The broker
reports continuous Linux ownership with no timeout.

After restarting, inherited-library tests passed all 240 immediate fixed-size
preservation checks and all 24 defined-scene resize checks, both without
buffer-age queries. The immediate diagnostic uses post-swap client readback;
it establishes preservation correctness, not performance or optical cadence.
This supersedes R13's incomplete startup preservation. Browser resize exposure,
including the user's pink/black and duck.ai purple-background observations,
remains an independent open investigation.
