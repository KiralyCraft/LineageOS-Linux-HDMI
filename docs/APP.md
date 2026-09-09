# HDMI Xorg control app

The app talks directly to the existing UID-authorized broker socket. It does
not need a superuser prompt or access to the chroot's filesystem. The broker
still owns mode safety, compatibility checks, leases and recovery.

## Controls

- **Arm HDMI Xorg** explicitly arms the existing broker (protocol v3, opcode 8).
- **Disarm HDMI Xorg** cancels arming (opcode 9).
- **Return to Android** uses the same disarm operation, with confirmation before
  ending an active Linux desktop. Save work before confirming.
- Native, 1080p60 and 4K60 only select a preference. The UI disables these during
  arming, handoff and active sessions. It does not change the broker's default.
- Status refreshes every two seconds while the activity is resumed, with only
  one request in flight. Background polling stops when the activity is paused.
- Command failures remain in a separate message area, even after a successful
  status refresh. **Show diagnostics** and **Copy diagnostics** expose raw state.

The app uses explicit arm/disarm rather than toggle, so a stale screen cannot
invert the user's intended operation. Requests remain subject to the broker's
state and permission checks. Opening the app, refreshing or choosing a mode
never implicitly arms a session.

The vector launcher icon has adaptive foreground/background and themed monochrome
layers. A separate monochrome glyph is used in Quick Settings. Screen content
respects system-bar and cutout insets, supports scrolling and labels selected modes
for accessibility. No runtime UI dependency was added.

## Build and validate

Use `build-support/build-tile.sh` with the existing project signing directory
on the build host. This is an APK-only change; composer and Mesa need no rebuild.
Install the resulting APK as an update signed with the same certificate. Keep
the old APK for rollback. A future full module package includes the updated app
through the normal build-tile/package steps.

In `android/tile`, run Gradle `:app:testDebugUnitTest :app:lintRelease`.
`ControlStateTest` covers idle, missing agent, armed/replug, leased, errors,
transitional states and unknown states. These policy tests do not replace device
testing of the socket or display handoff.

Manual device checks:

1. With HDMI disconnected and the existing session inactive, open the app. Verify
   that no takeover is armed merely by launching or selecting a mode.
2. Press Arm, read the armed guidance and verify the broker's armed flag. Disarm
   and verify that it clears. Mode controls must stay disabled while armed.
3. Change state using Quick Settings; the foreground app should catch up on its
   next refresh. Compare the app with the broker's root `status` command.
4. Check portrait, landscape, larger text, scrolling, system-bar overlap, icon,
   diagnostics expansion and copy. Background/reopen must resume live updates.
5. In an authorized display test, check replug guidance, actual Xorg startup and
   the active-session stop confirmation. Cancel must keep Linux running.
6. With a deliberately unavailable test broker, verify a clear error and disabled
   takeover controls. Never stop a live production broker just for this check.

Design references: [adaptive icons](https://developer.android.com/develop/ui/compose/system/icon_design_adaptive)
and [window insets](https://developer.android.com/develop/ui/views/layout/edge-to-edge).
