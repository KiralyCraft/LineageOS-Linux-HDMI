#!/usr/bin/env bash
set -Eeuo pipefail

MODE=${1:?mode}
PROFILE=${2:?profile}
COMMIT=${3:?commit}
WORK=${4:?work}
SOURCE=$WORK/source
BUILD=$WORK/build
OUTPUT=$WORK/output
REMOTE_ROOT=/bigdata/hdmi-los-build

[[ $PROFILE =~ ^[a-z0-9][a-z0-9._-]*$ ]]
[[ $COMMIT =~ ^[0-9a-f]{40}$ ]]
[[ $WORK == "$REMOTE_ROOT/work/$COMMIT" ]]

mkdir -p "$BUILD" "$OUTPUT" "$REMOTE_ROOT/cache" "$REMOTE_ROOT/signing"
rm -rf -- "$OUTPUT"/*

"$SOURCE/build-support/port-patches.sh" "$SOURCE" "$BUILD" "$PROFILE"
if [[ $MODE == port ]]; then
    printf 'Patch port check passed for %s at %s\n' "$PROFILE" "$COMMIT"
    exit 0
fi

[[ $MODE == zip ]]
"$SOURCE/build-support/build-composer.sh" "$SOURCE" "$BUILD" "$PROFILE" "$REMOTE_ROOT/cache"
"$SOURCE/build-support/build-native.sh" "$SOURCE" "$BUILD"
"$SOURCE/build-support/build-tile.sh" "$SOURCE" "$BUILD" \
    "$REMOTE_ROOT/signing" "$PROFILE"
"$SOURCE/build-support/package.sh" "$SOURCE" "$BUILD" "$OUTPUT" "$PROFILE" "$COMMIT"
