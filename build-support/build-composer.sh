#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE=${1:?source}
BUILD=${2:?build}
PROFILE=${3:?profile}
CACHE=${4:?cache}
TREE=$CACHE/lineage-22.2-display
MANIFEST_REPO=$CACHE/manifest-display
REPO=$CACHE/bin/repo
OUT=$BUILD/soong-out
COMPOSER_OUT=$BUILD/composer
PROFILE_JSON=$SOURCE/profiles/$PROFILE.json
EXACT_MANIFEST=$SOURCE/manifests/$PROFILE.xml

test -f "$EXACT_MANIFEST"
mkdir -p "$CACHE/bin" "$TREE" "$MANIFEST_REPO" "$COMPOSER_OUT"

if [ ! -x "$REPO" ]; then
    python - "$REPO" <<'PY'
import pathlib, sys, urllib.request
url = "https://storage.googleapis.com/git-repo-downloads/repo"
target = pathlib.Path(sys.argv[1])
target.write_bytes(urllib.request.urlopen(url, timeout=60).read())
target.chmod(0o755)
PY
fi

python "$SOURCE/build-support/filter-manifest.py" "$EXACT_MANIFEST" \
    "$MANIFEST_REPO/default.xml"
if [ ! -d "$MANIFEST_REPO/.git" ]; then
    git -C "$MANIFEST_REPO" init -q
    git -C "$MANIFEST_REPO" config user.name hdmi-los-builder
    git -C "$MANIFEST_REPO" config user.email hdmi-los@localhost
fi
git -C "$MANIFEST_REPO" add default.xml
git -C "$MANIFEST_REPO" commit -q --allow-empty -m 'exact installed-build display manifest'

(cd "$TREE" && "$REPO" init -q --depth=1 -u "file://$MANIFEST_REPO" -m default.xml)

(cd "$TREE" && "$REPO" sync --no-tags --no-clone-bundle --optimized-fetch \
    --prune --force-sync --fail-fast -j8)

revision=$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["source"]["qcom_display_revision"])' "$PROFILE_JSON")
patchset=$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["patchset"])' "$PROFILE_JSON")
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
    cd "$TREE"
    export OUT_DIR="$OUT"
    export BUILD_USERNAME=hdmi-los
    export BUILD_HOSTNAME=ResearchVM
    export ALLOW_MISSING_DEPENDENCIES=true
    source build/envsetup.sh
    lunch hdmi_pdx234-userdebug
    m -j8 vendor.qti.hardware.display.composer-service libsdmcore libsdmdal
) 2>&1 | tee "$BUILD/composer-build.log"

PRODUCT=$OUT/target/product/hdmi_pdx234
cp "$PRODUCT/vendor/bin/hw/vendor.qti.hardware.display.composer-service" "$COMPOSER_OUT/"
cp "$PRODUCT/vendor/lib64/libsdmcore.so" "$PRODUCT/vendor/lib64/libsdmdal.so" "$COMPOSER_OUT/"
for artifact in "$COMPOSER_OUT/"*; do
    readelf -h "$artifact" | grep -q 'AArch64'
    readelf -n "$artifact" > "$artifact.notes.txt"
done
