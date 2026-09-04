#!/usr/bin/env bash
set -Eeuo pipefail

MESA_BUILD=${1:?Mesa build directory}
XSERVER_BUILD=${2:?Xorg build directory}
OUTPUT=${3:?output tar.gz}
STAGE=$(mktemp -d)
trap 'rm -rf -- "$STAGE"' EXIT

gallium=("$MESA_BUILD"/src/gallium/targets/dri/libgallium-*.so)
((${#gallium[@]} == 1)) || {
    printf 'expected exactly one Mesa DRI target, found %d\n' "${#gallium[@]}" >&2
    exit 1
}

mesa_files=(
    "${gallium[0]}"
    "$MESA_BUILD/src/glx/libGLX_mesa.so.0.0.0"
    "$MESA_BUILD/src/egl/libEGL_mesa.so.0.0.0"
)
dril="$MESA_BUILD/src/gallium/targets/dril/libdril_dri.so"
gbm="$MESA_BUILD/src/gbm/libgbm.so.1.0.0"
gbm_backend="$MESA_BUILD/src/gbm/backends/dri/dri_gbm.so"
xorg="$XSERVER_BUILD/hw/xfree86/Xorg"
glamor="$XSERVER_BUILD/hw/xfree86/glamor_egl/libglamoregl.so"
modesetting="$XSERVER_BUILD/hw/xfree86/drivers/modesetting/modesetting_drv.so"

for file in "${mesa_files[@]}" "$dril" "$gbm" "$gbm_backend" \
    "$xorg" "$glamor" "$modesetting"; do
    [[ -f $file ]] || { printf 'missing GPU stack component: %s\n' "$file" >&2; exit 1; }
    readelf -h "$file" | grep -q 'AArch64' || {
        printf 'GPU stack component is not AArch64: %s\n' "$file" >&2
        exit 1
    }
done
for file in "${mesa_files[@]}"; do
    LC_ALL=C grep -aFq 'HDMI_LOS_MESA_BRIDGE_ABI=5' "$file" || {
        printf 'Mesa component does not carry ABI 5: %s\n' "$file" >&2
        exit 1
    }
done
LC_ALL=C grep -aFq '__driDriverGetExtensions_kgsl' "$dril" || {
    printf 'Mesa DRIL target does not export the KGSL driver entry point: %s\n' "$dril" >&2
    exit 1
}
LC_ALL=C grep -aFq 'gbm_create_device' "$gbm" || {
    printf 'Mesa GBM library has no GBM entry point: %s\n' "$gbm" >&2
    exit 1
}
LC_ALL=C grep -aFq 'gbmint_get_backend' "$gbm_backend" || {
    printf 'Mesa GBM backend has no backend entry point: %s\n' "$gbm_backend" >&2
    exit 1
}
LC_ALL=C grep -aFq "${gallium[0]##*/}" "$gbm_backend" || {
    printf 'Mesa GBM backend does not require the matched Gallium build: %s\n' \
        "$gbm_backend" >&2
    exit 1
}

install -d "$STAGE/lib/mesa/gbm" "$STAGE/lib/xorg/modules/drivers" "$STAGE/libexec"
install -m 0644 "${gallium[0]}" "$STAGE/lib/mesa/${gallium[0]##*/}"
install -m 0644 "${mesa_files[1]}" "$STAGE/lib/mesa/libGLX_mesa.so.0"
install -m 0644 "${mesa_files[2]}" "$STAGE/lib/mesa/libEGL_mesa.so.0"
install -m 0644 "$dril" "$STAGE/lib/mesa/libdril_dri.so"
ln -s libdril_dri.so "$STAGE/lib/mesa/kgsl_dri.so"
install -m 0644 "$gbm" "$STAGE/lib/mesa/libgbm.so.1"
install -m 0644 "$gbm_backend" "$STAGE/lib/mesa/gbm/dri_gbm.so"
install -m 0755 "$xorg" "$STAGE/libexec/Xorg"
install -m 0644 "$glamor" "$STAGE/lib/xorg/modules/libglamoregl.so"
install -m 0644 "$modesetting" \
    "$STAGE/lib/xorg/modules/drivers/modesetting_drv.so"

mkdir -p -- "$(dirname -- "$OUTPUT")"
tar --sort=name --mtime='UTC 2026-09-03' --owner=0 --group=0 --numeric-owner \
    -C "$STAGE" -czf "$OUTPUT" .
sha256sum -- "$OUTPUT"
