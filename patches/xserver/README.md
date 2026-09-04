# Private Xorg 21.1.24 pair

Accelerated mode uses a private Xorg binary and `libglamoregl.so` built from
the same Xorg 21.1.24 tree. Keeping the pair private avoids changing the
chroot's package-managed Xorg installation and guarantees that the Present
core and glamor DRI3 behavior match.

The pinned base is Xorg commit
`65d790bd208ec380b196eb98f144abb0b32e334d` (`xorg-server-21.1.24`). Apply the
default series in its declared order:

```sh
git clone --branch xorg-server-21.1.24 --depth 1 \
  https://gitlab.freedesktop.org/xorg/xserver.git xserver-hdmi
cd xserver-hdmi
while IFS= read -r patch; do
  git apply "../LineageOS-Linux-HDMI/patches/xserver/$patch"
done < ../LineageOS-Linux-HDMI/patches/xserver/series
meson setup build --prefix=/usr -Dxorg=true -Dglamor=true
ninja -C build hw/xfree86/Xorg hw/xfree86/glamor_egl/libglamoregl.so
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

`optional/0003-modesetting-support-an-overlay-plane-cursor.patch` is not in
the default series. It must be paired with the Qualcomm composer patch under
`patches/qcom-display/v1/optional/` and selected with `--overlay-cursor`. The
experiment rendered the cursor on a distinct plane, but synchronous legacy
plane updates still reduced 120 Hz pointer-motion rendering to 27-31 FPS and
caused multi-second stalls. It is retained for diagnosis, not recommended as a
performance fix.
