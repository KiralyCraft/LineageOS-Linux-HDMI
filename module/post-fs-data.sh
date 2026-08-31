#!/system/bin/sh

# Keep Magic Mount disabled.  An unconditional /vendor overlay could load an
# ABI-incompatible composer immediately after a Lineage update.  Instead, this
# script verifies the untouched files and then bind-mounts the three artifacts.
MODDIR=${0%/*}
STATE=/data/adb/hdmi-los
LOGDIR=$STATE/logs
MARKER=$MODDIR/compatible.ok
MOUNTED=$STATE/mounted-this-boot.list

mkdir -p "$LOGDIR"
chmod 0700 "$STATE" "$LOGDIR"
exec >>"$LOGDIR/gate.log" 2>&1

printf '\n[%s] profile gate starting\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
rm -f "$MARKER" "$MOUNTED"

. "$MODDIR/profile.env" || exit 0

fail_closed() {
  printf 'FAIL CLOSED: %s\n' "$1"
  if [ -s "$MOUNTED" ]; then
    while IFS= read -r target; do
      [ -n "$target" ] && umount "$target" 2>/dev/null
    done < "$MOUNTED"
  fi
  rm -f "$MARKER" "$MOUNTED"
  exit 0
}

check_prop() {
  actual="$(getprop "$1")"
  [ "$actual" = "$2" ] || fail_closed "$1 expected '$2', found '$actual'"
}

check_prop ro.product.device "$EXPECTED_DEVICE"
check_prop ro.lineage.version "$EXPECTED_LINEAGE"
check_prop ro.build.id "$EXPECTED_BUILD_ID"
check_prop ro.build.version.sdk "$EXPECTED_SDK"
check_prop ro.build.version.security_patch "$EXPECTED_SECURITY_PATCH"

# post-fs-data normally runs before class hal.  Refuse to replace executable
# mappings if this ROM has started composer unusually early.
if pidof vendor.qti.hardware.display.composer-service >/dev/null 2>&1; then
  fail_closed 'composer service was already running'
fi

while IFS='|' read -r expected mode owner group context target relative; do
  case "$expected" in ''|'#'*) continue ;; esac
  source=$MODDIR/$relative
  [ -f "$target" ] || fail_closed "missing original target $target"
  [ -f "$source" ] || fail_closed "missing module payload $relative"
  actual="$(sha256sum "$target" | awk '{print $1}')"
  [ "$actual" = "$expected" ] || fail_closed "original hash mismatch for $target ($actual)"

  patched="$(awk -F '|' -v p="$relative" '$1 == p { print $2; exit }' \
      "$MODDIR/patched-checksums.list")"
  [ -n "$patched" ] || fail_closed "missing payload manifest entry for $relative"
  actual="$(sha256sum "$source" | awk '{print $1}')"
  [ "$actual" = "$patched" ] || fail_closed "payload hash mismatch for $relative"

  chmod "$mode" "$source" || fail_closed "chmod failed for $relative"
  chown "$owner:$group" "$source" || fail_closed "chown failed for $relative"
  chcon "$context" "$source" || fail_closed "chcon failed for $relative"
done < "$MODDIR/original-checksums.list"

while IFS='|' read -r expected mode owner group context target relative; do
  case "$expected" in ''|'#'*) continue ;; esac
  source=$MODDIR/$relative
  mount -o bind "$source" "$target" || fail_closed "bind mount failed for $target"
  printf '%s\n' "$target" >> "$MOUNTED"
  actual="$(sha256sum "$target" | awk '{print $1}')"
  patched="$(awk -F '|' -v p="$relative" '$1 == p { print $2; exit }' \
      "$MODDIR/patched-checksums.list")"
  [ "$actual" = "$patched" ] || fail_closed "mounted hash mismatch for $target"
done < "$MODDIR/original-checksums.list"

printf '%s\n' "$PROFILE_NAME" > "$MARKER"
chmod 0600 "$MARKER"
printf 'PASS: exact build and all payloads verified; composer overlay enabled\n'
