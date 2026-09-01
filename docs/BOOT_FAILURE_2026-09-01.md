# 2026-09-01 pre-animation boot failure

The first `0.1-current-install` package reached `post-fs-data`, passed every
property and original-file hash check, and bind-mounted all three patched
composer artifacts.  The patched composer then failed to bring SurfaceFlinger
far enough to start the Lineage boot animation.  Magisk safe mode recovered the
phone and left the module disabled.

The package's dynamic export check had missed a C++ ABI break.  Six lease
methods had been added as virtual methods to `DisplayInterface` and
`HWInterface`.  Although they were declared after each base destructor, the
Itanium C++ ABI placed them before virtuals introduced by derived classes.  For
example, the old `HWPeripheralDRM` slot for `GetHWPanelMaxBrightness()` became
the slot for `PrepareExternalDisplayLease()`.  Proprietary Sony display code
compiled against the old slot layout could therefore call a function with an
incompatible signature during composer initialization.

Release 0.2 routes lease operations through namespace-level, non-virtual bridge
functions and concrete non-virtual methods.  It also refuses packaging if any
existing exported object changes size or if any existing exported vtable
changes size or relocation-slot identity.  The known-bad 0.1 binaries are an
explicit negative test for that guard.

This correction is build-verified, not a claim of a successful device boot or
live HDMI takeover.  The 0.1 Magisk ZIP with SHA-256
`e0a0dfec34a8cbd3395c0c4b8aa3583230e09835ab752cafdbdc8aad036c5ff9`
must not be installed or re-enabled.

## Release 0.2 deployment failure

The 0.2 ABI correction reached `post-fs-data`, passed its property and hash
gate, and mounted all three corrected artifacts. Composer still exited with
status 127 every five seconds, causing init to restart SurfaceFlinger, zygote,
and system_server indefinitely.

All three targets were direct bind mounts from `/data/adb/modules/hdmi-los`.
Their per-mount flags included `nosuid`, and the kernel audit log rejected the
composer transition with `process2 { nosuid_transition }` and
`execute_no_trans` denials. The executable itself and both libraries matched
the intended e942 build; the failure was the VFS deployment path, not another
composer ABI defect.

Magisk 29 prepares `$MAGISKTMP/.magisk/modules` as a read-only bind-remount of
the module root with `nosuid` cleared, then loads module files from that view.
Release 0.2.1 keeps the conditional `skip_mount` gate but takes its three file
binds from this Magisk mirror. It refuses to enable the overlay unless the
mirror and final targets are read-only, executable, correctly labeled, and
hash-identical to the prepared payloads. It does not remount `/data` and does
not grant `nosuid_transition` in SELinux policy.

The 0.2 ZIP is therefore also unsafe to install or re-enable.
