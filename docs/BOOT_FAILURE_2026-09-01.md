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
