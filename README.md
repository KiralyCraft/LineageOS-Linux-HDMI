# hdmi-los

`hdmi-los` is an experimental, version-locked Magisk module for the Xperia 1 V
(`XQ-DQ72`, LineageOS device `pdx234`). It cooperates with Qualcomm's hardware
composer to pause Android's external display, lease only that display's DRM
connector/CRTC/primary plane to Xorg, and then restore Android without touching
the internal DSI panel.

The initial profile is **only** for
`22.2-20250608-NIGHTLY-pdx234`. The module checks both Android properties and
the SHA-256 hashes of the original composer artifacts before its verified
early-boot bind mounts. Any mismatch fails closed; the module deliberately has
`skip_mount`, so a Lineage update cannot receive an unconditional old `/vendor`
overlay.

## Safety status

This repository was created after two unsafe out-of-band experiments: one Xorg
lease attempt was followed by a hard reset, and a direct DP CRTC release left an
Android graphics fence unsignaled and froze SystemUI. This implementation does
not duplicate or manipulate the composer's DRM state from another process.
Lease creation, cache invalidation, and display resume happen inside the
composer/DAL that owns the state.

It is still experimental. `make zip` performs build-time checks only. It never
installs the module, reboots Android, starts Xorg, or performs a live takeover.

Runtime escape paths are:

- Hold Volume Up and Volume Down together for three seconds.
- A session is forcibly restored after 60 seconds; composer has its own
  65-second backstop.
- Disable/uninstall the Magisk module and reboot to return to the original
  vendor files.
- Magisk safe mode (hold Volume Down during boot) disables all modules.

There is deliberately no proximity-sensor escape.

## Build

All compilation and ZIP assembly happen on `root@192.168.104.201`:

```sh
make server-preflight
make zip
make verify
```

Artifacts are copied into `dist/`. The local machine only stores source,
patches, profiles, signing material, and returned artifacts.

The output consists of the Magisk ZIP, a chroot agent bundle, signed-APK
certificate details, a provenance manifest, and `SHA256SUMS`. No boot or vendor
partition image is built or flashed.

Future builds use `make profile PROFILE=<name>` followed by
`make port PROFILE=<name>`. The captured build manifest pins every source
revision, while package-time rendering changes the installer/version/hash gate
to that profile. Source synchronization fetches those object IDs directly and
has no branch-history fallback. A missing revision, patch conflict, or compile
failure refuses to produce an installable ZIP.

## Manual activation outline

The exact manual test checklist is in `docs/MANUAL_TEST.md`. In short: install
the ZIP in Magisk, reboot, start the chroot agent, accept Android mirroring,
add the `HDMI Xorg` Quick Settings tile, and tap it. No takeover starts at boot.
