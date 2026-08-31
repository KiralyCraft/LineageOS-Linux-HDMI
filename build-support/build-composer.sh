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
BUILD_MANIFEST=$BUILD/exact-build-manifest.xml

test -f "$EXACT_MANIFEST"
mkdir -p "$TREE" "$COMPOSER_OUT"

python "$SOURCE/build-support/prepare-build-manifest.py" \
    "$EXACT_MANIFEST" "$PROFILE_JSON" "$BUILD_MANIFEST"
python "$SOURCE/build-support/sync-exact-manifest.py" "$BUILD_MANIFEST" "$TREE" 8
python "$SOURCE/build-support/verify-proprietary-inputs.py" "$TREE" "$PROFILE_JSON"
cp "$TREE/.hdmi-los-exact-manifest.json" "$BUILD/exact-source-sync.json"

# Builds made before the full-manifest transition installed a synthetic pdx234
# product in this dedicated cache.  It is not a project in the pinned manifest,
# so Git checkout cannot clean it, and Android otherwise sees two BoardConfig
# files for the same TARGET_DEVICE.  Remove only that known legacy cache path.
LEGACY_PRODUCT=$TREE/device/hdmi/pdx234
case "$LEGACY_PRODUCT" in
    "$TREE"/device/hdmi/pdx234) rm -rf -- "$LEGACY_PRODUCT" ;;
    *) printf 'refusing unsafe legacy product path: %s\n' "$LEGACY_PRODUCT" >&2; exit 2 ;;
esac

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

rm -rf -- "$OUT" "$COMPOSER_OUT"/*
# A failed product probe must never make Lineage roomservice mutate this exact
# source set or fetch a moving branch.  Remove any prior fallback record and
# force subsequent probes into read-only dry-run mode.
rm -f -- "$TREE/.repo/local_manifests/roomservice.xml"

build_modules() {
    local log=${1:?log}
    (
        # Android's envsetup/lunch functions intentionally probe unset shell
        # variables and are not compatible with nounset.  Keep every other strict
        # setting, and confine this relaxation to the Android build subshell.
        set +u
        cd "$TREE"
        export OUT_DIR="$OUT"
        export BUILD_USERNAME=hdmi-los
        export BUILD_HOSTNAME=ResearchVM
        export ROOMSERVICE_DRYRUN=true
        source build/envsetup.sh
        lunch "lineage_pdx234-${release}-userdebug"
        m -j8 vendor.qti.hardware.display.composer-service libsdmcore libsdmdal
    ) 2>&1 | tee "$log"
}

PRODUCT=$OUT/target/product/pdx234
BASELINE_OUT=$BUILD/composer-baseline
rm -rf -- "$BASELINE_OUT"
mkdir -p "$BASELINE_OUT"
build_modules "$BUILD/composer-baseline-build.log"
cp "$PRODUCT/vendor/bin/hw/vendor.qti.hardware.display.composer-service" "$BASELINE_OUT/"
cp "$PRODUCT/vendor/lib64/libsdmcore.so" "$PRODUCT/vendor/lib64/libsdmdal.so" "$BASELINE_OUT/"

while IFS= read -r patch; do
    case "$patch" in ''|'#'*) continue ;; esac
    git -C "$DISPLAY" am "$SOURCE/patches/$patchset/$patch"
done < "$SOURCE/patches/$patchset/series"

build_modules "$BUILD/composer-build.log"
cp "$PRODUCT/vendor/bin/hw/vendor.qti.hardware.display.composer-service" "$COMPOSER_OUT/"
cp "$PRODUCT/vendor/lib64/libsdmcore.so" "$PRODUCT/vendor/lib64/libsdmdal.so" "$COMPOSER_OUT/"
for artifact in "$COMPOSER_OUT/"*; do
    readelf -h "$artifact" | grep -q 'AArch64'
    readelf -n "$artifact" > "$artifact.notes.txt"
done
"$SOURCE/build-support/verify-composer-abi.sh" "$BASELINE_OUT" "$COMPOSER_OUT" |
    tee "$BUILD/composer-abi-report.txt"
