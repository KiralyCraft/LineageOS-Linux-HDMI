# Exclusive fullscreen modeset lifecycle

## Reproduction

The 2026-10-09 ioQuake3 reproduction started from a 3840x2160@60 HDMI
session and requested exclusive 1920x1080 fullscreen using a copied profile.
Xorg successfully issued the legacy modeset, then
`HDMI_COMPANION_PRESENT` (`0x40304806`) returned `-EACCES` at monotonic
121778.495928 seconds. Xorg raised
`HDMI kernel presenter could not accept deferred flip` and Android was restored.
The earlier saved failure had companion stop reason `MODE_CHANGED` with balanced
vblank-reference gets/puts. Quake reached FD740; no Mesa change is needed to
explain this rejection.

## Fix

The broker retains a reference to its authorized lease for the session lifetime.
A negotiated agent capability forwards structured SETCRTC before/after barriers
through the existing acknowledged trace transport. Opcode 24 carries state
PREPARE=0/BEFORE=1/AFTER=2, the trace sequence as request_id, the target crtc_id, the ioctl
result in status, and SETCRTC fb_id/count_connectors/mode_valid in the otherwise
unused active_width/active_height/active_refresh_millihz fields of this agent-only
message. It does not reinterpret an ordinary display-status response. This remains active under
`--drm-trace startup`; ordinary per-frame ioctls gain no new broker round trips.

Before connector DPMS-off, destroying its scanout buffers, or issuing legacy SETCRTC, Xorg cancels an unsubmitted copy, drains any actual
submitted flip, and closes its old presenter. The explicit prepare hook (exported by the private interposer) and BEFORE acknowledgement retire
the broker's old timing session. After a successful enable, the broker creates
and validates a new kernel timing session for the actual mode before allowing
Xorg to continue. Xorg then binds a new presenter to that generation. A failed
ioctl validates whatever mode actually survived, or fails into normal recovery.

The kernel companion is unchanged. Its mode-change, lease-revocation and native
fence checks remain authoritative. The agent refuses a kernel-presenter runtime
missing the matching Xorg modeset marker, and refuses a broker that does not
acknowledge the new capability. Legacy agents remain supported by the broker.

The two-second deadline is a failure watchdog spanning disable and enable; it
is not a delay before readiness and it never authorizes an unfinished frame.
Repeated barriers cannot extend it. Readiness requires the existing kernel
TIMING_VALID check (advancing accounting and plausible timestamps). Shutdown
acknowledgements never recreate timing. The retained lease is closed on cleanup.

## Earlier boundary found by repetition

The first candidate survived entry into 1080p60 and an explicit return to 4K60,
but a repeat stopped with CRTC_INACTIVE before its SETCRTC BEFORE was processed.
A second candidate added a barrier before scanout-buffer destruction. It passed
one normal game round trip but failed on the next exit. The ioctl trace identified
the earlier operation: connector DPMS-off (property ID 2) completed before the
companion reported CRTC_INACTIVE. No RMFB or SETCRTC occurred between those points.
RMFB remains a potential destructive operation covered by the fix, but it was not
the demonstrated trigger in that capture. The final transaction therefore starts
before DPMS-off as well as buffer destruction and SETCRTC. This is an ordering
fix, not an extension of the watchdog deadline.

The first harness also used SIGTERM, leaving Quake in FBO shutdown. The runner
now requests WM_DELETE_WINDOW through SDL's event loop before considering a
forced cleanup of its own test child. Signal-handler reentrancy is a plausible
explanation of that hang; it is not established by a stack trace.

## Validation

Host tests exercise production barrier ordering, disable/enable, consecutive
transitions, failed-mode rollback, failed timing validation, expired transactions,
wrong CRTC/sequence, startup and teardown. Existing restart, stop, lifecycle,
registration and child-process fixtures are also required with ASan/UBSan.

The final R3 candidate passed three consecutive normal ioQuake3 fullscreen
round trips, 3840x2160@60 -> 1920x1080@60 -> 3840x2160@60, with Xorg PID 9288
surviving throughout. Each game exited with status zero via WM_DELETE_WINDOW.
A refresh change to 4K30, game entry to 1080p60 and return to 4K30 also passed.
The desktop was restored to 4K60 and a connected restart succeeded; a subsequent
game round trip passed on the new Xorg PID 11571. All retired R3 timing sessions
reported USER stop with balanced gets=1/puts=1, rather than CRTC_INACTIVE or
MODE_CHANGED. The 55-second trace contained 15,767 ioctls, including 4,814
classified presentation/modeset operations, with no observed companion ioctl failures.
CPU3 reported 168 overwritten trace events at the beginning of the bounded capture;
this is not a lossless trace of every ioctl. The broker/Xorg logs and normal game
exits independently establish that the sessions survived. Other driver capability
probe failures and interrupted waits were retained in the raw trace.

The additional requested 4K60 -> 1080p30 -> 4K60 transition passed with xrandr.
The same transition was then requested by ioQuake3 using r_displayRefresh=30;
XRandR confirmed the selected 1080p30 mode during the game. See the packaged
live results for its normal exit and restoration checks.

These are automated mode/lifecycle checks, not a new user-confirmed visual or
physical scanout-cadence measurement. The Mesa renderer was FD740. Mesa, the
kernel companion, composer, Android app and CPU-power module were not changed. Raw captures are in RAM-backed `/tmp` and
archived on the build server under
`/bigdata/mesa-sync-build/package-combined/hdmi-modeset-20261009/evidence`.
The reproduction leaves the user's game configuration unchanged.
