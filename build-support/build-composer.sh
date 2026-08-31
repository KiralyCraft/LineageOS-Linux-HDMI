#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE=${1:?source}
BUILD=${2:?build}
PROFILE=${3:?profile}
CACHE=${4:?cache}
TREE=$CACHE/lineage-22.2-display
OUT=$BUILD/soong-out
COMPOSER_OUT=$BUILD/composer
PROFILE_JSON=$SOURCE/profiles/$PROFILE.json
EXACT_MANIFEST=$SOURCE/manifests/$PROFILE.xml
FILTERED_MANIFEST=$BUILD/exact-display-manifest.xml

test -f "$EXACT_MANIFEST"
mkdir -p "$TREE" "$COMPOSER_OUT"

python "$SOURCE/build-support/filter-manifest.py" "$EXACT_MANIFEST" "$FILTERED_MANIFEST"
python "$SOURCE/build-support/sync-exact-manifest.py" "$FILTERED_MANIFEST" "$TREE" 8
cp "$TREE/.hdmi-los-exact-manifest.json" "$BUILD/exact-source-sync.json"

revision=$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["source"]["qcom_display_revision"])' "$PROFILE_JSON")
patchset=$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["patchset"])' "$PROFILE_JSON")
release=$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["device"]["ro.build.id"].split(".", 1)[0].lower())' "$PROFILE_JSON")
[[ $release =~ ^[a-z0-9_]+$ ]]
DISPLAY=$TREE/hardware/qcom-caf/sm8550/display
git -C "$DISPLAY" am --abort >/dev/null 2>&1 || true
git -C "$DISPLAY" reset --hard "$revision"
git -C "$DISPLAY" clean -fdx
git -C "$DISPLAY" config user.name hdmi-los-builder
git -C "$DISPLAY" config user.email hdmi-los@localhost
while IFS= read -r patch; do
    case "$patch" in ''|'#'*) continue ;; esac
    git -C "$DISPLAY" am "$SOURCE/patches/$patchset/$patch"
done < "$SOURCE/patches/$patchset/series"

rm -rf -- "$TREE/device/hdmi/pdx234" "$OUT" "$COMPOSER_OUT"/*
mkdir -p "$TREE/device/hdmi/pdx234"
cp -a "$SOURCE/build-support/product/." "$TREE/device/hdmi/pdx234/"

(
    # Android's envsetup/lunch functions intentionally probe unset shell
    # variables and are not compatible with nounset.  Keep every other strict
    # setting, and confine this relaxation to the Android build subshell.
    set +u
    cd "$TREE"
    export OUT_DIR="$OUT"
    export BUILD_USERNAME=hdmi-los
    export BUILD_HOSTNAME=ResearchVM
    export ALLOW_MISSING_DEPENDENCIES=true
    source build/envsetup.sh
    lunch "hdmi_pdx234-${release}-userdebug"
    m -j8 vendor.qti.hardware.display.composer-service libsdmcore libsdmdal
) 2>&1 | tee "$BUILD/composer-build.log"

PRODUCT=$OUT/target/product/hdmi_pdx234
cp "$PRODUCT/vendor/bin/hw/vendor.qti.hardware.display.composer-service" "$COMPOSER_OUT/"
cp "$PRODUCT/vendor/lib64/libsdmcore.so" "$PRODUCT/vendor/lib64/libsdmdal.so" "$COMPOSER_OUT/"
for artifact in "$COMPOSER_OUT/"*; do
    readelf -h "$artifact" | grep -q 'AArch64'
    readelf -n "$artifact" > "$artifact.notes.txt"
done
