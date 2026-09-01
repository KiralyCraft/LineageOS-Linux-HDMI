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
| Leased Xorg `:1`, modesetting glamor plus native KGSL | native Freedreno `FD740` | yes | DRI3 works, but Qualcomm KMS rejects the scanout framebuffer; HDMI stays black |

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

The installed Mesa comes from the custom
[`fix/kgsl-present-wait-fence`](https://github.com/KiralyCraft/mesa-for-android-container/tree/fix/kgsl-present-wait-fence)
branch. Its relevant custom commits are:

```text
91f7e8c6 freedreno/kgsl: retain merged submits through GPU command ioctl
a3eb373e dri3: bridge native render fences to Present
```

The latter patch exports a native Freedreno render fence and attaches it as an
X Present wait fence through Mesa's DRI3 loader and GLX/EGL DRI3 paths. That is
the appropriate synchronization fix once Xorg exposes a working DRI3 screen,
but the stable takeover currently initializes GLX with `DRISWRAST` and does
not enter those DRI3 drawable and Present paths. The custom patch therefore
cannot repair presentation in the present ShadowFB configuration.

The X server's 2D rendering remains software-based. llvmpipe is currently the
only GLX path verified to produce visible application pixels on the leased
display.

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

The next useful target is a hybrid path, not another direct-glamor attempt:
retain the known-good dumb framebuffer and ShadowFB modeset, add screen-level
DRI3 import for the native KGSL client's linear dma-bufs, wait on the Mesa
Present fence, and copy the completed pixels into ShadowFB. A CPU copy is the
safest first implementation; a GPU-assisted copy can be evaluated only after
the buffer and fence contract is proven. This preserves the already-tested KMS
scanout object instead of asking SDE to scan out a KGSL sharing buffer.

## Reproducing the checks

For Termux:X11, run from an interactive `kiraly` shell:

```sh
DISPLAY=:0 glxinfo -B
DISPLAY=:0 glxgears -info
```

During an active leased-Xorg session, this command verifies GPU context
creation and command execution, but its window is currently black:

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
start an interactive login shell. Do not make Zink the LXDE default while its
windows fail to present. The next accelerated candidate must either expose a
working screen-level DRI3/Present path without replacing the KMS scanout
buffer, or implement an explicit copy/presentation bridge into ShadowFB. The
direct glamor experiment proves that merely enabling DRI3 is insufficient.
Enabling glamor, DRI3, or the Freedreno DDX changes the X server/KMS path itself
and requires the full crash-evidence protocol before it can replace the stable
ShadowFB configuration.

## Starting the takeover as `kiraly`

From a terminal inside the mounted chroot:

```sh
cd /home/kiraly/Downloads/hdmi-los-runtime
./run-agent.sh --capture none
```

`run-agent.sh` automatically re-executes itself through passwordless
`sudo -n`; it is expected to remain in the foreground. Leave that terminal
open, then tap the `HDMI Xorg` Quick Settings tile on Android. Pressing
`Ctrl-C` stops the waiting chroot agent. The tile, volume-button escape, and
60/65-second deadlines restore Android.

The `0.2.5-diagnostic.1` module disables ordinary tile activation. With that
older diagnostic package installed, start the foreground agent as above and
trigger the takeover separately from an Android root shell:

```sh
/data/adb/modules/hdmi-los/bin/hdmi-losd probe xorg-atomic
```

Release `0.2.6-candidate.1` enables the tile and selects the same atomic probe
mode without requiring that separate root command.
