#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE=${1:?source}
BUILD=${2:?build}
OUTPUT=${3:?output}
PROFILE=${4:?profile}
COMMIT=${5:?commit}
STAGE=$BUILD/package
MODULE=$STAGE/module
CHROOT=$STAGE/chroot

rm -rf -- "$STAGE" "$OUTPUT"
mkdir -p "$MODULE/bin" "$MODULE/apk" "$MODULE/vendor/bin/hw" "$MODULE/vendor/lib64" \
    "$MODULE/docs" "$CHROOT/bin" "$CHROOT/config" "$OUTPUT"
cp -a "$SOURCE/module/." "$MODULE/"
python "$SOURCE/build-support/render-module.py" "$SOURCE/profiles/$PROFILE.json" "$MODULE"
cp "$BUILD/native/android/hdmi-losd" "$MODULE/bin/"
cp "$BUILD/tile/HdmiLosTile.apk" "$MODULE/apk/"
cp "$BUILD/composer/vendor.qti.hardware.display.composer-service" "$MODULE/vendor/bin/hw/"
cp "$BUILD/composer/libsdmcore.so" "$BUILD/composer/libsdmdal.so" "$MODULE/vendor/lib64/"
cp "$SOURCE/docs/MANUAL_TEST.md" "$SOURCE/docs/ROLLBACK.md" "$MODULE/docs/"

chmod 0755 "$MODULE/bin/hdmi-losd" "$MODULE/"*.sh
chmod 0755 "$MODULE/vendor/bin/hw/vendor.qti.hardware.display.composer-service"
chmod 0644 "$MODULE/vendor/lib64/"*.so "$MODULE/apk/"*.apk

: > "$MODULE/patched-checksums.list"
for relative in \
    vendor/bin/hw/vendor.qti.hardware.display.composer-service \
    vendor/lib64/libsdmcore.so vendor/lib64/libsdmdal.so; do
    printf '%s|%s\n' "$relative" "$(sha256sum "$MODULE/$relative" | awk '{print $1}')" \
        >> "$MODULE/patched-checksums.list"
done

python "$SOURCE/build-support/write-build-info.py" \
    "$SOURCE" "$BUILD" "$PROFILE" "$COMMIT" "$MODULE/build-info.json"

cp "$BUILD/native/chroot/"* "$CHROOT/bin/"
cp "$SOURCE/native/agent/run-agent.sh" "$CHROOT/"
cp "$SOURCE/native/agent/agent.conf.example" "$CHROOT/config/"
cp "$MODULE/build-info.json" "$CHROOT/"
chmod 0755 "$CHROOT/bin/"* "$CHROOT/run-agent.sh"

module_zip=$OUTPUT/hdmi-los-$PROFILE-magisk.zip
chroot_tar=$OUTPUT/hdmi-los-$PROFILE-chroot.tar.gz
(cd "$MODULE" && zip -X -9 -r "$module_zip" .)
tar --sort=name --mtime='UTC 2026-08-31' --owner=0 --group=0 --numeric-owner \
    -C "$CHROOT" -czf "$chroot_tar" .
cp "$MODULE/build-info.json" "$OUTPUT/hdmi-los-$PROFILE-build-info.json"
cp "$BUILD/tile/apksigner.txt" "$OUTPUT/hdmi-los-$PROFILE-apksigner.txt"
(cd "$OUTPUT" && sha256sum -- hdmi-los-* | sort > SHA256SUMS)
