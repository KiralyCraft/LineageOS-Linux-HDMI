#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE=${1:?source}
DEST_BUILD=${2:?destination build}
PROFILE=${3:?profile}
BASE_COMMIT=${4:?base commit}
REMOTE_ROOT=${5:?remote root}
BASE_WORK=$REMOTE_ROOT/work/$BASE_COMMIT
BASE_BUILD=$BASE_WORK/build
BASE_OUTPUT=$BASE_WORK/output
BASE_INFO=$BASE_OUTPUT/hdmi-los-$PROFILE-build-info.json
DEST_COMMIT=${DEST_BUILD%/build}
DEST_COMMIT=${DEST_COMMIT##*/}

[[ $BASE_COMMIT =~ ^[0-9a-f]{40}$ ]]
[[ $BASE_WORK == "$REMOTE_ROOT/work/$BASE_COMMIT" ]]
[[ $DEST_COMMIT =~ ^[0-9a-f]{40}$ ]]
[[ $DEST_BUILD == "$REMOTE_ROOT/work/$DEST_COMMIT/build" ]]
[[ $DEST_BUILD != "$BASE_BUILD" ]]
test -f "$BASE_INFO"
test -f "$BASE_OUTPUT/SHA256SUMS"
(cd "$BASE_OUTPUT" && sha256sum -c SHA256SUMS)
python "$SOURCE/build-support/verify-reused-composer.py" \
    "$BASE_INFO" "$BASE_BUILD" "$SOURCE/profiles/$PROFILE.json" "$BASE_COMMIT"

rm -rf -- "$DEST_BUILD"
mkdir -p "$DEST_BUILD"
cp -a "$BASE_BUILD/composer" "$DEST_BUILD/"
cp -a "$BASE_BUILD/exact-source-sync.json" "$BASE_BUILD/qcom-display.patched" \
    "$BASE_BUILD/composer-abi-report.txt" "$DEST_BUILD/"

python "$SOURCE/build-support/verify-reused-composer.py" \
    "$BASE_INFO" "$DEST_BUILD" "$SOURCE/profiles/$PROFILE.json" "$BASE_COMMIT"
