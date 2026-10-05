# HDMI companion compatibility probe

This is the first gate for Candidate B. It is a separate Magisk module with ID
`hdmi_companion_probe`; it does not replace or update the existing HDMI module,
control app, kernel, vendor drivers, or chroot runtime.

The probe registers `/dev/hdmi_companion_probe`, mode 0600. Its only ioctl reports
ABI version, build identity, kernel release, and imported-interface bits. It
retains typed references to the required exported kernel functions so the loader
checks their symbol versions. It never invokes those functional interfaces,
opens a graphics device, creates a session, or acquires a vblank reference.

## Installation and result

Install the staged ZIP using Magisk when convenient and reboot. Installation and
boot both require an exact match for kernel release and effective configuration.
The late-start service loads only the new probe module, runs identity and invalid
ioctl checks, and writes:

- `/data/adb/modules/hdmi_companion_probe/probe.result`: `PASS`, `FAIL`, or `PENDING`.
- `/data/adb/modules/hdmi_companion_probe/probe.log`: load/query diagnostics.

Do not use `insmod -f`, change signature/CFI checks, or replace any existing driver.
An unsigned module may add the kernel's normal unsigned-module taint. Successful
loading establishes compatibility of this probe's imports; the functional timing
module still needs its own image, reference, suspend, and teardown validation.

For a manual query from Android root:

```sh
/data/adb/modules/hdmi_companion_probe/bin/hdmi-companion-probe
```

Disable/remove this module in Magisk and reboot to remove the probe. Closing the
query tool releases its file descriptor. The module has no graphics resources to
restore, and no session-stop action unloads it automatically.

## Build and offline verification

Build only on `root@192.168.104.201`. Capture the running `/proc/config.gz`,
`/sys/kernel/kheaders.tar.xz`, kernel release, and installed
`/vendor_dlkm/lib/modules/msm_drm.ko` as read-only inputs. Keep these captures and
generated kernel output outside Git. No captured binary is published.

Use a clean checkout of core revision
`d00ba216ccda5d4fcc0d864729ae69d5b63d860c`, Android Clang `r536225` build
`12701618`, and `pahole 1.25`. The scripts use the existing build-server toolchain:

```sh
bash build-support/build-companion-kernel.sh KERNEL_SOURCE KERNEL_OUTPUT INPUTS
bash build-support/build-companion-probe.sh SOURCE KERNEL_SOURCE KERNEL_OUTPUT INPUTS PROBE_OUTPUT SOURCE_COMMIT
```

The input directory contains `running.config`, `kheaders.tar.xz`, `kernel-release`,
and `installed-msm_drm.ko`. The first command creates build output and
`Module.symvers`; it refuses configuration drift. The second creates the probe,
Android query tool, manifest, ABI report, and ZIP.

The effective `.config` must match byte for byte. Generated `autoconf.h`
definitions must also match, while allowing the leading comment that the
kernel's `gen_kheaders.sh` removes from the runtime header archive.

Offline gates check AArch64 ELF format, exact vermagic, all required imports after
LTO, cross-DSO CFI instrumentation, every probe symbol version, and all installed
DRM import versions available in the rebuilt kernel. A relocation check rejects
direct calls to functional probe imports, including calls through local CFI
thunks. Compiler-generated eight-byte CFI address entries are checked and reported
separately. These are offline checks; the manifest
records on-device loading as pending until the user installs the artifact.

## Next gate

The [2026-10-05 standalone live probe](../diagnostics/companion-probe/LIVE-20261005.md)
passed load, query, negative ioctl checks, and unload without rebooting. This
establishes compatibility of that identifiable query-only build.

After the compatibility gate passes, implement the broker-owned timing session and
fence-driven Xorg TearFree completion. Candidate B preserves the deployed Mesa
buffers and presentation path. Copy/layout optimizations remain subsequent work.
