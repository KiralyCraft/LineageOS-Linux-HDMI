# Matched EGL frontend runtime deployed, 2026-10-08

The continuous 3840x2160@30 leased desktop now uses:

`~/Downloads/hdmi-termux-vblank-min-coexist-usb-bt-bcdef-xorg26-egl-20261008`

Launch that folder's `./run-agent.sh` for future sessions. It contains the complete
vblank-minimal Mesa stack on cc190637b, HDMI BCDEF work, USB/Bluetooth input,
matched private Xorg 26 modules, native/companion payload and exact source patches.
Mesa is `a426a4845`. EGL and GLX now use frontend-specific callback flush adapters
so the same-context resolve is covered by a valid native fence.

The existing agent was paused and resumed while HDMI remained connected. Its
original resolve-order path is a read-only bind mount onto the new persistent
folder in the private chroot namespace. Original underlying files remain intact;
reboot removes the bind mount. Agent PID 8827 continues; Xorg restarted as 20911
and LXDE as 21014. Actual loaded Gallium/EGL/GBM and Xorg module hashes matched
the bundle. The broker reports Linux ownership in continuous mode with no timeout.
CPU7 remains online. No Magisk install, kernel replacement or Android browser
change occurred. The preceding demand-allocation folder is the rollback runtime.

A post-restart hardware EGL/GLES3 damage-aware test passed 24/24 changing-pattern
image checks using the inherited desktop library environment. Build manifests,
source verification, comparative EGL regression results, real-browser screenshots
and remaining limitations are described in EGL-FRONTEND-20261008.md. Raw images,
traces and private profiles remain in RAM; only code/reports and final bundles
were written to SD. Abrupt resize clipping, occasional long tails, Maps/Teams and
physical 4K60 remain open. The desktop is left running continuously.
