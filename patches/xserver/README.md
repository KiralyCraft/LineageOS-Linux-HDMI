# Private Xorg 21.1.24 stack

Accelerated mode uses a private Xorg binary and `libglamoregl.so` built from
the same Xorg 21.1.24 tree. Keeping the pair private avoids changing the
chroot's package-managed Xorg installation and guarantees that the Present
core and glamor DRI3 behavior match.

The `third_party/xserver` submodule pins Xorg commit
`65d790bd208ec380b196eb98f144abb0b32e334d` (`xorg-server-21.1.24`). Keep the
submodule pristine and apply the default series in its declared order to a
disposable worktree:

```sh
git submodule update --init --depth 1 third_party/xserver
repo=$(pwd)
git -C third_party/xserver worktree add --detach \
  "$repo/.local/xserver-hdmi" 65d790bd208ec380b196eb98f144abb0b32e334d
cd "$repo/.local/xserver-hdmi"
while IFS= read -r patch; do
  git apply "$repo/patches/xserver/$patch"
done < "$repo/patches/xserver/series"
meson setup build --prefix=/usr -Dxorg=true -Dglamor=true
ninja -C build \
  hw/xfree86/Xorg \
  hw/xfree86/glamor_egl/libglamoregl.so \
  hw/xfree86/drivers/modesetting/modesetting_drv.so
```

Build for the same AArch64 userspace and package versions as the target
chroot. Install only these private files in the HDMI runtime:

```text
libexec/Xorg
lib/xorg/modules/libglamoregl.so
lib/xorg/modules/drivers/modesetting_drv.so
```

The agent launches the private binary only for `kgsl-kms-bridge` and gives it
`lib/xorg/modules,/usr/lib/xorg/modules` as the module search path. The safe
ShadowFB mode continues to launch `/usr/lib/Xorg`.

Patch 1 fixes the wait-fence callback lifetime used by the KGSL native-fence
path. Patch 2 backports current upstream glamor behavior: give DRI3 clients the
render node when one is available and use the primary node only as a fallback.
Patch 3 backports upstream modesetting TearFree and its complete follow-up
correctness series. TearFree keeps two shadow scanout buffers per CRTC, copies
accumulated damage into the next buffer, and flips it at vblank. This restores
coherent output cadence when Xorg's visible software cursor makes direct
Present flips ineligible; it does not suppress `DIRTYFB` or add a timer.
