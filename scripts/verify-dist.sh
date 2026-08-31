#!/usr/bin/env bash
set -Eeuo pipefail

PROFILE=${1:-current-install}
ROOT=$(git rev-parse --show-toplevel)
DIST=$ROOT/dist
ZIP=$DIST/hdmi-los-$PROFILE-magisk.zip
TAR=$DIST/hdmi-los-$PROFILE-chroot.tar.gz
INFO=$DIST/hdmi-los-$PROFILE-build-info.json

test -f "$ZIP" && test -f "$TAR" && test -f "$INFO" && test -f "$DIST/SHA256SUMS"
(cd "$DIST" && sha256sum -c SHA256SUMS)
unzip -t "$ZIP" >/dev/null
tar -tzf "$TAR" >/dev/null
python "$ROOT/tests/check-package.py" "$ZIP" "$TAR" "$INFO" \
    "$ROOT/profiles/$PROFILE.json" "$(git -C "$ROOT" rev-parse HEAD)"

tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT
unzip -q "$ZIP" -d "$tmp/module"
tar -xzf "$TAR" -C "$tmp" --one-top-level=chroot

for script in "$tmp/module/"*.sh "$tmp/chroot/run-agent.sh"; do
    bash -n "$script"
done
file "$tmp/module/bin/hdmi-losd" "$tmp/module/vendor/bin/hw/"* \
    "$tmp/module/vendor/lib64/"* "$tmp/chroot/bin/"*
for binary in "$tmp/module/bin/hdmi-losd" "$tmp/module/vendor/bin/hw/"* \
    "$tmp/module/vendor/lib64/"* "$tmp/chroot/bin/"*; do
    readelf -h "$binary" | grep -q 'AArch64'
done

printf 'Offline verification: PASS (nothing installed or executed on Android)\n'

