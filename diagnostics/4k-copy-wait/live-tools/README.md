# Reproducing the 2026-10-05 measurements

These Python/ctypes tools measure this Xperia's existing leased Xorg and FD740 Mesa. They do not compile native code, restart Xorg or change the output mode. They are supplemental diagnostics, not production launchers. The report is [LIVE-20261005.md](../LIVE-20261005.md), and the published aggregate data is [live-20261005-summary.json](../live-20261005-summary.json).

## Reparse the recorded data without accessing the display

`recorded/` contains only aggregate `HDMI_LATENCY`/`HDMI_COPY` log lines and compact phase metadata. Original per-request samples and full local logs remain in `~/Downloads/hdmi-latency-20261005/`. Both parsers select only complete diagnostic intervals inside each phase; the reference parser excludes the post-movement wait for the queued event.

Copy `recorded/` to a writable scratch directory before running the parsers, which write their analysis JSON beside the inputs:

```sh
cp -a recorded /tmp/hdmi-recorded-analysis
python analyse-unlocked.py /tmp/hdmi-recorded-analysis
python analyse-vblank.py /tmp/hdmi-recorded-analysis
```

## Collect another visible movement run

Copy the chosen scripts into a fresh output directory and execute from there. These scripts write beside themselves, so do not run them in an evidence archive. They require `konsole`, `xdotool`, `xprop`, the inherited HDMI Openbox environment, and permission to read the desktop/Xorg process environment and logs.

`two-unlocked-move.py` creates two 900x900 Konsole windows, runs one/two/one movement phases, periodically checks for xsecurelock, records root-coordinate samples, and closes only its own process groups. XSync times are request acknowledgement, not displayed-frame latency. Only one Xorg server is supported; the script refuses to select implicitly when multiple servers exist.

`vblank-reference.py` additionally requires access to `/dev/dri/card0`. It targets **CRTC 235 on this device** and queues one relative event 1200 refreshes ahead. Use only with the expected HDMI CRTC active, a connected display and an unlocked desktop. The event is bounded, and the script waits for and drains it. Interruptions or unplugging can invalidate the reference experiment. This is not a suggested persistent workaround. The published script adds lock guards, elapsed-time phase bounds and FD cleanup to the original measured script; those changes do not retroactively validate the discarded locked runs.

## Offscreen Mesa copy/fence test

`copy-benchmark.py` allocates four 4K textures, checks pixels, and records shader-copy and completion timings. It never connects to X or modesets. It must use a **matched complete Mesa build**. In particular, include the private GBM backend path; private `libgbm.so.1` alone can still load the system backend. The script checks loaded Mesa paths before EGL context creation and refuses system/private mixing. System GLVND dispatch and non-Mesa dependencies are expected.

Use an environment copied from the running Xorg for `LD_LIBRARY_PATH`, `LIBGL_DRIVERS_PATH`, `GBM_BACKENDS_PATH`, `MESA_LOADER_DRIVER_OVERRIDE`, `FD_FORCE_KGSL`, `FD_KGSL_ENABLE_DMABUF`, and `FD_KGSL_RENDERONLY`. Preserve optional EGL vendor selection if the session defines it. Root/device permissions may be needed; sudo commonly removes loader variables, so set them in a launcher **after** privilege transition and exec a fresh Python interpreter. Do not preload the broker tracer for this offscreen test.

For the measured bundle the private paths were:

```text
LD_LIBRARY_PATH=<bundle>/lib/mesa
LIBGL_DRIVERS_PATH=<bundle>/lib/mesa
GBM_BACKENDS_PATH=<bundle>/lib/mesa/gbm
MESA_LOADER_DRIVER_OVERRIDE=kgsl
FD_FORCE_KGSL=1
FD_KGSL_ENABLE_DMABUF=1
FD_KGSL_RENDERONLY=1
```

The default matrix includes 60 glFinish samples plus ten fence samples per configuration. `HDMI_COPY_FENCE_ONLY=1` runs the full-4K fence breakdown with 30 samples per configuration, writing `copy-fence-breakdown.json`. The GPU's normal power policy is retained; before/after clock snapshots do not prove a constant clock throughout the run. The temporary private texture's layout is not independently verified.

Both movement and offscreen tests retain live background desktop activity. They cannot measure physical HDMI FPS or keyboard-to-photon latency.
