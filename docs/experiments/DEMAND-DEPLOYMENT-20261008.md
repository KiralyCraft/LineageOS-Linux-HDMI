# Demand-allocation runtime deployment, 2026-10-08

The connected 3840x2160@30 continuous desktop now uses:

`~/Downloads/hdmi-termux-vblank-min-coexist-usb-bt-bcdef-xorg26-demand-20261008`

Use that folder's `./run-agent.sh` for future launches. It contains matched Mesa
`eecab7336`, the validated private Xorg 26 stack, the complete vblank-minimal Mesa
patch set, BCDEF controls, USB/Bluetooth input, and the existing companion/native
payload. No Magisk install occurred. `--lazy-slots 0` selects the eager comparison.
The preceding `...xorg26-pacing-20261008` folder remains the rollback runtime.

The existing agent was paused and resumed with HDMI still connected. Its old
resolve-order path is a read-only bind mount onto the new persistent folder in
the private chroot namespace; the original underlying folder was preserved.
Reboot removes the bind mount. Xorg PID 23258 and LXDE 23378 loaded matching
persistent libraries; broker status is state 2, no timeout, 4K30 Linux ownership.
The log reports FD740 glamor, TearFree, native export/release fences, frame-timed
repaint, eligible blits and the kernel deferred presenter. CPU7 remains online.
The restart was followed by another 24/24 multisampled changing-pattern checks
with swap-interval transitions using the inherited desktop library environment.

The attached JSON records exact file hashes and the restart result. All raw
diagnostics remain RAM-backed. See DEMAND-ALLOCATION-20261008.md for comparative
measurements and limitations. Long tails, active-resize background exposure,
Firefox/Maps usability and physical 4K60 remain open. The active desktop is left
running continuously for continued investigation.
