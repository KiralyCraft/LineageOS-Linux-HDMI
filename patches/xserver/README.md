# Private Xorg 21.1.24 pair

Accelerated mode uses a private Xorg binary and `libglamoregl.so` built from
the same Xorg 21.1.24 tree. Keeping the pair private avoids changing the
chroot's package-managed Xorg installation and guarantees that the Present
core and glamor DRI3 behavior match.

The pinned base is Xorg commit
`65d790bd208ec380b196eb98f144abb0b32e334d` (`xorg-server-21.1.24`). Apply the
patches in lexical order:

```sh
git clone --branch xorg-server-21.1.24 --depth 1 \
  https://gitlab.freedesktop.org/xorg/xserver.git xserver-hdmi
cd xserver-hdmi
git apply ../LineageOS-Linux-HDMI/patches/xserver/*.patch
meson setup build --prefix=/usr -Dxorg=true -Dglamor=true
ninja -C build hw/xfree86/Xorg hw/xfree86/glamor_egl/libglamoregl.so
```

Build for the same AArch64 userspace and package versions as the target
chroot. Install only these private files in the HDMI runtime:

```text
libexec/Xorg
lib/xorg/modules/libglamoregl.so
```

The agent launches the private binary only for `kgsl-kms-bridge` and gives it
`lib/xorg/modules,/usr/lib/xorg/modules` as the module search path. The safe
ShadowFB mode continues to launch `/usr/lib/Xorg`.

Patch 1 fixes the wait-fence callback lifetime used by the KGSL native-fence
path. Patch 2 backports current upstream glamor behavior: give DRI3 clients the
render node when one is available and use the primary node only as a fallback.
