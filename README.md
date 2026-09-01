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

The initial 0.1 package caused a pre-animation boot failure because new virtual
methods shifted Qualcomm derived-class vtable slots used by proprietary Sony
display code. Magisk safe mode recovered the phone. Release 0.2 replaces those
hooks with a non-virtual bridge and adds an exact vtable-slot build gate. The
[incident record](docs/BOOT_FAILURE_2026-09-01.md) contains the evidence. The
first 0.2 package also failed before animation: its custom bind mounts bypassed
Magisk's executable module mirror and inherited `nosuid` from `/data`. Release
0.2.1 binds from Magisk's read-only, cleared-`nosuid` mirror instead. Neither
the 0.1 nor the 0.2 ZIP should be installed. Release 0.2.2 also fixes deferred
tile installation by preventing Package Manager from inheriting a log file
descriptor that `system_server` cannot write under SELinux.

Release 0.2.3 reached the first real lease transition, but its composer path
held Qualcomm's global pluggable-display handler lock while synchronously
powering off the external display and creating the DRM lease. The first live
activation ended in a full-device reset and cut OTG dock power; no retained
crash dump identified the exact internal phase. Do not install 0.2.3. Release
0.2.4 limits that global lock to display selection,
performs prepare, pause, and lease creation as separately acknowledged phases,
refreshes SurfaceFlinger after pause as the stock display-status path does, and
durably records every boundary before entering composer or Xorg code.

The first 0.2.4 Xorg start still reset the whole phone after the lease was
successfully delivered. Its Xorg log and lock file remained zero-length, and
the OTG dock lost power with the phone. Release `0.2.5-diagnostic.1` therefore
does not permit normal tile activation. It separates an unused three-second
lease hold from two root-only, synchronously traced Xorg starts. Every DRM
ioctl is durably acknowledged by the Android broker before it enters the
kernel. The capture card must be powered by the workstation, not the phone.
The upstream and downstream comparison is recorded in
[`docs/QUALCOMM_DRM_RESEARCH.md`](docs/QUALCOMM_DRM_RESEARCH.md).

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

For a module-script or documentation-only correction, reuse an already
verified binary build without running the Android/native toolchains:

```sh
make repackage BASE_COMMIT=<full-verified-build-commit>
make verify BASE_COMMIT=<full-verified-build-commit>
```

The repackage command refuses changes outside an explicit packaging/deployment
allowlist and records separate package-source and binary-source commits.

For native/agent diagnostics, reuse only a verified composer while rebuilding
the broker, tracer, chroot tools, and signed tile:

```sh
make reuse-composer BASE_COMMIT=8a9e97430b062ed695f11801a5b251636ba3971a
make verify BASE_COMMIT=8a9e97430b062ed695f11801a5b251636ba3971a
```

This mode refuses any change to the profile, Qualcomm patch series, or
composer build inputs. Build-info schema 3 records the repository commit of
every individual artifact.

Artifacts are copied into `dist/`. The local machine only stores source,
patches, profiles, signing material, and returned artifacts.

The output consists of the Magisk ZIP, a chroot agent/tracer bundle, signed-APK
certificate details, a provenance manifest, and `SHA256SUMS`. No boot or vendor
partition image is built or flashed.

Future builds use `make profile PROFILE=<name>` followed by
`make port PROFILE=<name>`. The captured build manifest pins every source
revision, while package-time rendering changes the installer/version/hash gate
to that profile. Source synchronization fetches those object IDs directly and
has no branch-history fallback. A missing revision, patch conflict, or compile
failure refuses to produce an installable ZIP.

`make profile` also records the installed `libsdmextension.so` checksum, but a
build manifest cannot reveal the proprietary repository commits. Before
`make zip PROFILE=<name>`, add the two reviewed, exact TheMuppets revisions to
that profile's `source.proprietary_projects`; the build checks the extracted
library hash and fails closed if those commits do not match the installed ROM.

The composer build follows the supported Lineage/AOSP shape: it materializes
the complete captured manifest, adds exact proprietary Sony projects, selects
the real `lineage_pdx234` product, and asks `m` for only the composer service and
two SDM libraries. It does not use a reduced synthetic product or suppress
missing dependencies. The current proprietary pins correspond to Sony base
`67.2.A.3.16`; the critical `libsdmextension.so` input is also hash-checked
against the running phone before compilation.

Before applying the patch series, the same exact tree builds an unmodified
composer/SDM baseline. Packaging fails if a patched ELF changes its ELF class,
machine, SONAME, dependency set, drops a baseline dynamic export, changes an
existing exported object's size, or changes the size or relocation identity of
any existing exported C++ vtable slot. This covers both class-layout adjustment
thunks and the derived-vtable shifting that caused the 0.1 boot failure.

## Manual activation outline

The exact staged checklist is in `docs/MANUAL_TEST.md`. Install the matching
ZIP and chroot bundle, establish ten minutes of workstation-powered Android
mirroring, run three root-only lease holds, and only then start one traced Xorg
probe. The diagnostic tile cannot start a takeover, and no takeover starts at
boot.
