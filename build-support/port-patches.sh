#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE=${1:?source}
BUILD=${2:?build}
PROFILE=${3:?profile}
PROFILE_JSON=$SOURCE/profiles/$PROFILE.json
DISPLAY=$BUILD/qcom-display
CACHE=/bigdata/hdmi-los-build/cache/qcom-display.git

revision=$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["source"]["qcom_display_revision"])' "$PROFILE_JSON")
patchset=$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["patchset"])' "$PROFILE_JSON")

if [ ! -d "$CACHE" ]; then
    git init --bare "$CACHE"
    git -C "$CACHE" remote add origin \
        https://github.com/LineageOS/android_hardware_qcom_display.git
fi
if ! git -C "$CACHE" cat-file -e "$revision^{commit}" 2>/dev/null; then
    git -C "$CACHE" fetch --no-tags --depth=1 origin "$revision"
fi
git -C "$CACHE" update-ref "refs/hdmi-los/$revision" "$revision"

rm -rf -- "$DISPLAY"
git clone --no-checkout --reference-if-able "$CACHE" "$CACHE" "$DISPLAY"
git -C "$DISPLAY" checkout --detach "$revision"
git -C "$DISPLAY" config user.name hdmi-los-builder
git -C "$DISPLAY" config user.email hdmi-los@localhost

while IFS= read -r patch; do
    case "$patch" in ''|'#'*) continue ;; esac
    git -C "$DISPLAY" am "$SOURCE/patches/$patchset/$patch"
done < "$SOURCE/patches/$patchset/series"

git -C "$DISPLAY" diff --check "$revision"..HEAD
if git -C "$DISPLAY" grep -n -E \
    'virtual .*ExternalDisplayLease|ExternalDisplayLease.*override'; then
    printf 'patch-port check failed: HDMI lease hooks must not enter vendor-facing vtables\n' >&2
    exit 1
fi
lease_source=$DISPLAY/sdm/libs/dal/hw_device_drm.cpp
if sed -n '/HWDeviceDRM::PrepareExternalDisplayLease/,/HWDeviceDRM::IsExternalDisplayLeaseConnected/p' \
    "$lease_source" | grep -q -E '\|\|[[:space:]]*!?active_|\|\|[[:space:]]*active_'; then
    printf 'patch-port check failed: HDMI lease lifecycle must not use the unmaintained active_ flag\n' >&2
    exit 1
fi
test "$(git -C "$DISPLAY" rev-parse "$revision")" = "$revision"
printf '%s\n' "$revision" > "$BUILD/qcom-display.base"
git -C "$DISPLAY" rev-parse HEAD > "$BUILD/qcom-display.patched"
