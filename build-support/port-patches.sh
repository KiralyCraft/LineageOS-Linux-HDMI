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
lease_fixed_primary=$(sed -n \
    '/DisplayError GetFixedPrimaryPlane/,/}  \/\/ namespace/p' "$lease_source")
grep -q 'drm_resources->crtcs\[i\] == crtc_id' <<<"$lease_fixed_primary" || {
    printf 'patch-port check failed: external CRTC resource index is not resolved\n' >&2
    exit 1
}
grep -q 'primary_index == \*crtc_index' <<<"$lease_fixed_primary" || {
    printf 'patch-port check failed: lease does not use the CRTC fixed primary plane\n' >&2
    exit 1
}
lease_create=$(sed -n \
    '/HWDeviceDRM::CreateExternalDisplayLease/,/HWDeviceDRM::RevokeExternalDisplayLease/p' \
    "$lease_source")
grep -q 'GetFixedPrimaryPlane' <<<"$lease_create" || {
    printf 'patch-port check failed: fixed primary plane is not refreshed at final handoff\n' >&2
    exit 1
}
composer_source=$DISPLAY/composer/hwc_session.cpp
composer_header=$DISPLAY/composer/hdmi_los_protocol.h
for phase in PREPARE PAUSE CREATE; do
    grep -q "HDMI_LOS_ACQUIRE_$phase" "$composer_header" || {
        printf 'patch-port check failed: staged composer acquisition is incomplete\n' >&2
        exit 1
    }
done
grep -q 'HDMI_LOS_FLAG_CONTINUOUS' "$composer_header" || {
    printf 'patch-port check failed: continuous composer watchdog protocol is missing\n' >&2
    exit 1
}
grep -q 'continuous lease watchdog armed' "$DISPLAY/composer/hdmi_lease_server.cpp" || {
    printf 'patch-port check failed: continuous composer watchdog renewal is missing\n' >&2
    exit 1
}
grep -q 'must not retain this global lock and deadlock a driver callback' "$composer_source" || {
    printf 'patch-port check failed: composer hotplug-lock boundary is missing\n' >&2
    exit 1
}
acquire_source=$(sed -n '/int HWCSession::AcquireHdmiLease/,/int HWCSession::ReleaseHdmiLease/p' \
    "$composer_source")
grep -q 'if (phase == HDMI_LOS_ACQUIRE_CREATE) command_lock.lock()' <<<"$acquire_source" || {
    printf 'patch-port check failed: final lease handoff is not serialized with composer commands\n' >&2
    exit 1
}
command_lock_line=$(grep -n 'command_lock(command_seq_mutex_' <<<"$acquire_source" | cut -d: -f1)
lease_lock_line=$(grep -n 'lease_lock(hdmi_lease_mutex_' <<<"$acquire_source" | cut -d: -f1)
if [ -z "$command_lock_line" ] || [ -z "$lease_lock_line" ] ||
   [ "$command_lock_line" -ge "$lease_lock_line" ]; then
    printf 'patch-port check failed: final handoff violates composer-to-lease lock order\n' >&2
    exit 1
fi
grep -q 'ToggleScreenUpdates(false)' <<<"$acquire_source" || {
    printf 'patch-port check failed: lease acquisition does not retain the last scanout\n' >&2
    exit 1
}
if grep -q 'SetDisplayStatus(HWCDisplay::kDisplayStatusPause)' <<<"$acquire_source"; then
    printf 'patch-port check failed: lease acquisition still powers the display off\n' >&2
    exit 1
fi
test "$(git -C "$DISPLAY" rev-parse "$revision")" = "$revision"
printf '%s\n' "$revision" > "$BUILD/qcom-display.base"
git -C "$DISPLAY" rev-parse HEAD > "$BUILD/qcom-display.patched"
