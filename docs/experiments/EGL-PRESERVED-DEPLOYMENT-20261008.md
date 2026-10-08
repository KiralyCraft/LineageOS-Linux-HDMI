# Preserved EGL runtime deployed, 2026-10-08

The continuous 3840x2160@30 desktop now uses the complete matched runtime:

`~/Downloads/hdmi-termux-vblank-min-coexist-usb-bt-bcdef-xorg26-preserved-20261008`

Use that folder's `./run-agent.sh` for future sessions. It includes Mesa
`08a3ec464`, cc190637b vblank-minimal/coexist baseline, HDMI BCDEF, USB/Bluetooth,
private Xorg 26 and its matching modules, companion/native payload, launcher,
source patches and validation manifests. The preceding `...xorg26-egl-20261008`
folder is the rollback runtime. No Magisk or kernel update was installed.

The existing agent was paused and resumed with HDMI connected. Its original
resolve-order path remains a read-only bind mount onto this new persistent
folder in the private chroot namespace; underlying originals remain intact.
Reboot removes the bind mount. Xorg restarted as PID 22454. Its loaded Gallium,
EGL, GLX, GBM, glamor, modesetting and executable hashes match the final bundle.
The broker reports continuous Linux ownership with no timeout.

A post-restart inherited-library test submitted 240 preserved partial swaps,
without buffer-age queries or extra per-frame round trips. All EGL/GL calls
succeeded; all eight sampled images passed all sixteen tile checks. The test
verifies settled X root images, not every presented frame or optical scanout.
The companion JSON records hashes, restart commands and the exact runtime.
The EGL preservation fix does not explain ordinary asynchronous Firefox resize
clipping; that investigation continues. Raw data remains in RAM-backed /tmp.
