#!/usr/bin/env bash
set -Eeuo pipefail

NAME=${1:?profile name}
ROOT=$(git rev-parse --show-toplevel)
[[ $NAME =~ ^[a-z0-9][a-z0-9._-]*$ ]] || { printf 'invalid profile name\n' >&2; exit 2; }
[[ ! -e $ROOT/profiles/$NAME.json && ! -e $ROOT/manifests/$NAME.xml ]] || {
    printf 'profile already exists; refusing to overwrite it\n' >&2; exit 1;
}

tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT
android=(sudo -n nsenter -t 1 -m --)

"${android[@]}" /system/bin/toybox cp /product/etc/build-manifest.xml /data/local/tmp/hdmi-los-manifest.xml
sudo -n cp /proc/1/root/data/local/tmp/hdmi-los-manifest.xml "$tmp/manifest.xml"
"${android[@]}" /system/bin/toybox rm /data/local/tmp/hdmi-los-manifest.xml
sudo -n chown "$(id -u):$(id -g)" "$tmp/manifest.xml"

for key in ro.product.device ro.lineage.version ro.build.id ro.build.version.sdk \
    ro.build.version.security_patch; do
    printf '%s=%s\n' "$key" "$("${android[@]}" /system/bin/getprop "$key")" >> "$tmp/properties"
done

for path in /vendor/bin/hw/vendor.qti.hardware.display.composer-service \
    /vendor/lib64/libsdmcore.so /vendor/lib64/libsdmdal.so; do
    host=/proc/1/root$path
    hash=$(sudo -n sha256sum "$host" | awk '{print $1}')
    build_id=$(sudo -n readelf -n "$host" | awk '/Build ID:/ {print $3; exit}')
    stat_fields=$(sudo -n stat -c '%a|%u|%g' "$host")
    context=$("${android[@]}" /system/bin/toybox ls -Z "$path" | awk '{print $1}')
    printf '%s|%s|%s|%s|%s\n' "$path" "$hash" "$build_id" "$stat_fields" "$context" \
        >> "$tmp/artifacts"
done

python "$ROOT/scripts/profile-from-capture.py" "$NAME" "$tmp/properties" \
    "$tmp/artifacts" "$tmp/manifest.xml" "$tmp/profile.json"
mv "$tmp/profile.json" "$ROOT/profiles/$NAME.json"
mv "$tmp/manifest.xml" "$ROOT/manifests/$NAME.xml"
printf 'Captured %s. Review and commit both files before port/build.\n' "$NAME"

