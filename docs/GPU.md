# GPU acceleration on the chroot displays

This note records live results from the Xperia 1 V (`pdx234`, Adreno 740) on
2026-09-01. It distinguishes the Termux:X11 display from the Xorg server that
owns the external display through a DRM lease. They use the same chroot Mesa
installation but expose different rendering interfaces to clients.

## Installed GPU paths

Android exposes all of the relevant nodes to the chroot:

```text
/dev/kgsl-3d0
/dev/dri/card0       (msm_drm)
/dev/dri/renderD128
```

The chroot has Mesa 26.2.0, the DRI loader shim, the Turnip Vulkan ICD, and the
Freedreno Xorg DDX. A headless `vulkaninfo --summary` sees:

```text
deviceName = Turnip Adreno (TM) 740
driverName = turnip Mesa driver
```

The interactive `kiraly` shell sets these in `.bashrc`:

```sh
export MESA_LOADER_DRIVER_OVERRIDE=kgsl
export TU_DEBUG=noconform
```

They are below the interactive-shell guard in `.bashrc`. A non-interactive
`runuser -l kiraly -c ...` does not receive them. A normal interactive login
does.

## Live result matrix

| Display and environment | Renderer | Accelerated | Result |
| --- | --- | --- | --- |
| Termux:X11 `:0`, interactive `kiraly` login | native Freedreno `FD740` | yes | Works |
| Leased Xorg `:1`, default LXDE environment | Mesa llvmpipe | no | Works, but CPU-rendered |
| Leased Xorg `:1`, `GALLIUM_DRIVER=zink` | Zink over Turnip Adreno 740 | context/commands use the GPU | Fails to present: the application window stays black |
| Leased Xorg `:1`, interactive-login `MESA_LOADER_DRIVER_OVERRIDE=kgsl` | none | no | Loader cannot retrieve the device; the client disconnects |
| Leased Xorg `:1`, modesetting glamor plus native KGSL, without the allocation bridge | native Freedreno `FD740` | yes | DRI3 works, but Qualcomm KMS rejects the scanout framebuffer; HDMI stays black |
| Leased Xorg `:1`, `kgsl-kms-bridge` | native Freedreno `FD740` | yes | Earlier runs produced visible LXDE and 55-57 FPS `glxgears`; candidate 3 proved mode-safe acquisition and exposed a lease-release HWC state mismatch, addressed but not yet hardware-validated in `0.2.8-candidate.4` |

The software `glxgears` run measured approximately 40 and 22 FPS. The Zink
over Turnip run reported approximately 420-500 FPS, but those swap/FPS reports
are misleading: root-window XWD captures showed a completely black client
area while the surrounding LXDE desktop and window decorations remained
visible. The same capture procedure showed colored gears with llvmpipe. Zink
therefore proves GPU context creation and command submission here, not working
presentation to the Xorg drawable.

The exact native-Freedreno login test succeeded on Termux:X11 `:0`:

```text
OpenGL vendor string: freedreno
OpenGL renderer string: FD740
Accelerated: yes
```

The same login environment against leased Xorg `:1` reported:

```text
MESA-LOADER: failed to retrieve device information
XIO: fatal IO error ... on X server ":1"
```

Only the GL client disconnected. Xorg and the takeover remained healthy.

## Why the leased display differs

The known-safe takeover deliberately configures Xorg with:

```text
Driver "modesetting"
Option "AccelMethod" "none"
Option "PageFlip" "false"
Option "ShadowFB" "true"
Option "Atomic" "true"
Option "SWcursor" "true"
```

The Xorg log confirms that glamor is disabled, the screen is not DRI2-capable,
and GLX initializes the `DRISWRAST` provider. The DRI3 extension exists at the
server level, but this screen does not provide the screen-level DRI3 interface
needed by the KGSL loader. This is why copying the interactive-login KGSL
override to `:1` does not reproduce Termux:X11's native Freedreno path.

Setting only `GALLIUM_DRIVER=zink` makes GLX identify the renderer as Zink over
Turnip/KGSL and execute GPU work, but it does not make the result visible on
this ShadowFB screen. `LIBGL_KOPPER_DRI2=true` confirms the missing interface:
Mesa reports that DRI3 is required for presentation and then fails to create a
swapchain. `LIBGL_KOPPER_DISABLE=true` still leaves the client area black.
Do not also set `MESA_LOADER_DRIVER_OVERRIDE=kgsl` on the leased display.

The installed Mesa source is pinned by the optional
[`third_party/mesa-for-android-container`](../third_party/mesa-for-android-container)
submodule at commit `2788d8df` on the
[`fix/kgsl-leased-screen`](https://github.com/KiralyCraft/mesa-for-android-container/tree/fix/kgsl-leased-screen)
branch. Its relevant custom commits are:

```text
91f7e8c6 freedreno/kgsl: retain merged submits through GPU command ioctl
a3eb373e dri3: bridge native render fences to Present
89da2771 freedreno/kgsl: bridge KMS scanout and X11 presentation
2788d8df dri3: pipeline the KGSL X11 SHM bridge
```

The latter patch exports a native Freedreno render fence and attaches it as an
X Present wait fence through Mesa's DRI3 loader and GLX/EGL DRI3 paths. That is
the appropriate synchronization fix once Xorg exposes a working DRI3 screen,
but the stable takeover currently initializes GLX with `DRISWRAST` and does
not enter those DRI3 drawable and Present paths. The custom patch therefore
cannot repair presentation in the present ShadowFB configuration.

In the safe ShadowFB configuration the X server's 2D rendering remains
software-based. The default `kgsl-kms-bridge` configuration described below is
the verified native-Freedreno exception.

## Native KGSL glamor diagnostic

A bounded Xorg-only probe on 2026-09-02 replaced the safe screen options with:

```text
Option "AccelMethod" "glamor"
Option "ShadowFB" "false"
Option "PageFlip" "false"
Option "Atomic" "true"
```

Both Xorg and the test client received:

```sh
MESA_LOADER_DRIVER_OVERRIDE=kgsl
FD_FORCE_KGSL=1
FD_KGSL_ENABLE_DMABUF=1
```

This successfully reached the intended rendering stack. Xorg reported:

```text
glamor X acceleration enabled on FD740
glamor initialized
[DRI2] DRI driver: kgsl
Initializing extension Present
Initializing extension DRI3
AIGLX: Loaded and initialized kgsl
```

`glxinfo -B` used DRI3, reported direct rendering, native Freedreno `FD740`,
and `Accelerated: yes`. Native-KGSL `glxgears` ran at approximately 57 FPS.

The result still could not be scanned out. Xorg repeatedly logged:

```text
failed to add fb -6
modeset(0): failed to set mode: No such device or address
```

An XWD root capture taken while `glxgears` was running was entirely black.
The important boundary is therefore below DRI3/Present: downstream Qualcomm
SDE KMS does not accept the KGSL/dma-heap buffer that stock glamor tries to
register as its scanout framebuffer. Disabling page flips does not avoid that
initial framebuffer registration. The phone remained stable and the normal
timeout restored Android.

## KGSL/KMS allocation and presentation bridge

The bridge was implemented and tested live on 2026-09-02. The custom
Mesa work now ends at commit `2788d8df` on
[`fix/kgsl-leased-screen`](https://github.com/KiralyCraft/mesa-for-android-container/tree/fix/kgsl-leased-screen),
directly based on `91f7e8c6`. The `fix/kgsl-present-wait-fence` branch ends at
that base and intentionally does not contain the KMS/X11 bridge.

The KMS capability probe in `native/probes/kms-kgsl-zero-copy.c` established
this supported allocation chain:

```text
SDE KMS dumb allocation
  -> PRIME dma-buf export
  -> KGSL import
  -> SDE KMS framebuffer
```

Mesa now has an explicit `FD_KGSL_USE_KMS_DUMB=1` allocation mode. It creates
the shared storage with KMS, imports the dma-buf into KGSL for rendering, and
translates the KGSL BO back to a GEM handle on Mesa's KMS control fd when GBM
asks for the scanout handle. The agent sets this mode only in Xorg. This made
the glamor-rendered root desktop visible with no `failed to add fb` errors, so
Xorg's final scanout remains zero-copy.

An ordinary client DRI3 pixmap was still black. This was tested with normal
dma-heap and KMS-dumb client allocations, DRI2 fallback, `glFinish`, and forced
linear/no-UBWC layouts. The diagnostic client read back the expected
`255,0,26,255` pixel locally in every case, while Xorg saw black. The same
window was visible with llvmpipe. The remaining fault is therefore the
cross-context KGSL dma-buf consumption on this downstream stack, not rendering,
mode setting, or the Present completion fence.

For that boundary, Mesa provides the opt-in
`MESA_KGSL_X11_SHM_BRIDGE=1` fallback. KGSL still renders the application. On
swap, Mesa waits for the completed image, maps that one drawable, and submits
it to the X drawable with MIT-SHM. Commit `2788d8df` replaces the checked
per-frame X request with a three-slot staging ring on a private XCB connection.
MIT-SHM completion events release slots, so up to three interval-zero swaps can
remain outstanding without a request/reply stall. Nonzero intervals use Present
MSC notifications for pacing. EGL damage rectangles are conservatively
coalesced; GLX swaps copy the full drawable. Set
`MESA_KGSL_X11_BRIDGE_STATS=1` for periodic frame/copy/wait statistics. The
agent also uses `FD_MESA_DEBUG=notile,noubwc` for bridge clients.

This client presentation step is not zero-copy. It is narrower than ShadowFB:
there is no continuous full-screen copy, and non-GL desktop content stays on
Xorg's GPU-backed root. Only an accelerated drawable is copied when that
client swaps. The initial live result was:

```text
OpenGL vendor:   freedreno
OpenGL renderer: FD740
Resolution:      1920x1080
glxgears:        55.233 and 57.364 FPS over two five-second samples
```

Both the solid-color GL probe and the gears were visible in root-window XWD
captures. Three bounded takeover runs restored Android normally, and the
device did not reboot or crash.

## External display mode

The broker selects a mode before HDMI connection; 1080p60 is the default, with
native/automatic and 4K60 as experimental choices. It journals the prior
Android preference and applies the choice through `cmd display`. Once Android
has connected and stabilized at that mode, the agent reads the leased CRTC's
active timing and verifies that the connector still advertises it. It writes
that exact timing to Xorg as `hdmi-los-android-current`.

This matters when the monitor and Android support more than one timing. If the
Xorg `PreferredMode` and `Modes` entries force a different resolution while a
Qualcomm connector topology property still describes Android's active mode,
the downstream KMS driver can reject `MODE_SETCRTC` with `EINVAL`, leaving the
external display black. Omitting both entries would avoid the hard-coded mode,
but would let Xorg independently choose from EDID instead of reliably keeping
Android's current resolution and refresh timing. Failure to read the exact
active mode now aborts takeover and restores Android.

RandR dimensions alone are no longer accepted as readiness. The agent
correlates the traced enabling `SETCRTC`, keeps a duplicate lease fd, and uses
`GETCRTC` to prove that the expected CRTC scans out Xorg's framebuffer at the
same full timing. The installed Sony kernel rejects Xorg's atomic-client
capability, so the operational path is legacy. If an exact same-mode legacy
`SETCRTC` alone returns `EINVAL`, the tracer may substitute a page flip, but
only after matching connector, CRTC, coordinates, and every timing field; the
same `GETCRTC` proof still applies.

The first mode-safe 1080p validation showed why retaining the old scanout is
required: powering the pluggable display off during lease acquisition left the
CRTC with the correct mode but `fb_id=0`, so the guarded page flip correctly
refused to run. Release `0.2.8-candidate.4` instead pauses HWC updates while
leaving Android's last committed external framebuffer active. Abort paths
resume updates directly. After a completed lease it revokes and resets the DRM
lease, then explicitly transitions Qualcomm HWC Off and On before refreshing
SurfaceFlinger. This prevents the raw CRTC reset from being followed by a
same-state no-op resume and an unsignaled external-display retire fence.

The first live test of this behavior inherited the Dell P2723QE's Android mode
of 3840x2160 at 60 Hz. Xorg generated a 533.250 MHz Modeline, RandR reported
3840x2160 as current, and the traced `MODE_SETCRTC` returned success. The
physical display initially showed mostly the blue desktop background with only
part of the session apparent, then the complete LXDE desktop became visible.
An XWD root capture during the run contained the full 3840x2160 wallpaper,
icons, and panel. This confirms correct mode inheritance while leaving 4K
first-paint latency and accelerated performance to be measured separately.

## Reproducing the checks

For Termux:X11, run from an interactive `kiraly` shell:

```sh
DISPLAY=:0 glxinfo -B
DISPLAY=:0 glxgears -info
```

During a safe ShadowFB leased-Xorg session, this command verifies GPU context
creation and command execution, but its window is black because that mode has
no presentation bridge:

```sh
DISPLAY=:1 \
XAUTHORITY=/run/hdmi-los/Xauthority \
GALLIUM_DRIVER=zink \
glxinfo -B
```

To pin the tested Vulkan ICD explicitly for diagnosis:

```sh
DISPLAY=:1 \
XAUTHORITY=/run/hdmi-los/Xauthority \
GALLIUM_DRIVER=zink \
VK_DRIVER_FILES=/usr/share/vulkan/icd.d/freedreno_icd.aarch64.json \
glxgears -info
```

The current agent starts LXDE directly with `dbus-run-session`; it does not
start an interactive login shell. `kgsl-kms-bridge` supplies the KGSL and
presentation variables explicitly, so programs launched from that LXDE
session inherit the working configuration. The direct glamor experiment still
proves that merely enabling DRI3 is insufficient. Safe ShadowFB remains
available as an explicit fallback.

## Starting the takeover as `kiraly`

From a terminal inside the mounted chroot:

```sh
cd /home/kiraly/Downloads/hdmi-los-runtime
./run-agent.sh
```

`run-agent.sh` automatically re-executes itself through passwordless
`sudo -n`; it is expected to remain in the foreground. Leave that terminal
open, then tap the `HDMI Xorg` Quick Settings tile on Android. Pressing
`Ctrl-C` stops the waiting chroot agent. The no-argument command defaults to
no phone-side capture, `kgsl-kms-bridge`, LXDE, and a renewable continuous
lease. The tile, volume-button escape, failed heartbeat, disconnect, and Xorg
exit still restore Android.

The default requires the patched private `libgallium-*.so` below `lib/mesa/`.
Its full equivalent command is:

```sh
cd /home/kiraly/Downloads/hdmi-los-runtime
./run-agent.sh --capture none --xorg-accel kgsl-kms-bridge --session lxde --no-timeout
```

The runner deliberately rejects this mode if the private Mesa library is
missing. Use `--timeout` to restore the bounded 60-second session deadline.
Use `--xorg-accel safe` for the software-rendered ShadowFB fallback; these can
be combined as `./run-agent.sh --xorg-accel safe --timeout` during staged
safety testing.

The `0.2.5-diagnostic.1` module disables ordinary tile activation. With that
older diagnostic package installed, start the foreground agent as above and
trigger the takeover separately from an Android root shell:

```sh
/data/adb/modules/hdmi-los/bin/hdmi-losd probe xorg-atomic
```

Release `0.2.8-candidate.4` makes the tile an arm/disarm control, defaults its
stored preset to 1080p60, waits for three stable composer mode samples, and uses
the legacy scanout path with strict trace-plus-`GETCRTC` verification. The
accelerated renewable session remains the no-argument launcher default.
