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

. "$MODDIR/profile.env" || fail_closed 'could not load profile.env'
. "$MODDIR/mount-utils.sh" || fail_closed 'could not load mount-utils.sh'

check_prop() {
  actual="$(getprop "$1")"
  [ "$actual" = "$2" ] || fail_closed "$1 expected '$2', found '$actual'"
}

check_prop ro.product.device "$EXPECTED_DEVICE"
check_prop ro.lineage.version "$EXPECTED_LINEAGE"
check_prop ro.build.id "$EXPECTED_BUILD_ID"
check_prop ro.build.version.sdk "$EXPECTED_SDK"
check_prop ro.build.version.security_patch "$EXPECTED_SECURITY_PATCH"

if [ -f "$MODDIR/diagnostic-only" ]; then
  command -v resetprop >/dev/null 2>&1 ||
    fail_closed 'diagnostic build requires Magisk resetprop'
  resetprop -n vendor.display.disable_hw_recovery_dump 0 ||
    fail_closed 'could not enable diagnostic display recovery dumps'
  diagnostic_dump="$(getprop vendor.display.disable_hw_recovery_dump)"
  [ "$diagnostic_dump" = 0 ] ||
    fail_closed "display recovery dump property remained '$diagnostic_dump'"
  printf 'PASS: diagnostic display recovery dumps requested before composer start\n'
fi

# post-fs-data normally runs before class hal.  Refuse to replace executable
# mappings if this ROM has started composer unusually early.
if pidof vendor.qti.hardware.display.composer-service >/dev/null 2>&1; then
  fail_closed 'composer service was already running'
fi

MODULE_ID="$(sed -n 's/^id=//p' "$MODDIR/module.prop" | head -n 1)"
case "$MODULE_ID" in
  ''|*[!A-Za-z0-9._-]*) fail_closed "invalid module id '$MODULE_ID'" ;;
esac

MAGISK_TMP="$(magisk --path 2>/dev/null)"
case "$MAGISK_TMP" in
  /*) ;;
  *) fail_closed "magisk --path returned invalid path '$MAGISK_TMP'" ;;
esac

MODULE_MIRROR=$MAGISK_TMP/.magisk/modules
BIND_ROOT=$MODULE_MIRROR/$MODULE_ID
[ -d "$BIND_ROOT" ] || fail_closed "missing Magisk module mirror $BIND_ROOT"

mirror_options="$(mountinfo_options_for_target "$MODULE_MIRROR")" ||
  fail_closed "could not resolve mount flags for $MODULE_MIRROR"
mount_options_are_read_only "$mirror_options" ||
  fail_closed "Magisk module mirror is not read-only ($mirror_options)"
mount_options_allow_exec "$mirror_options" ||
  fail_closed "Magisk module mirror blocks execution ($mirror_options)"

while IFS='|' read -r expected mode owner group context target relative; do
  case "$expected" in ''|'#'*) continue ;; esac
  source=$MODDIR/$relative
  bind_source=$BIND_ROOT/$relative
  [ -f "$target" ] || fail_closed "missing original target $target"
  [ -f "$source" ] || fail_closed "missing module payload $relative"
  [ -f "$bind_source" ] || fail_closed "missing mirrored payload $relative"
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

  actual="$(sha256sum "$bind_source" | awk '{print $1}')"
  [ "$actual" = "$patched" ] ||
    fail_closed "mirrored payload hash mismatch for $relative"
  source_identity="$(stat -c '%d:%i' "$source")" ||
    fail_closed "could not stat module payload $relative"
  mirror_identity="$(stat -c '%d:%i' "$bind_source")" ||
    fail_closed "could not stat mirrored payload $relative"
  [ "$source_identity" = "$mirror_identity" ] ||
    fail_closed "mirrored payload is not the prepared inode for $relative"
done < "$MODDIR/original-checksums.list"

while IFS='|' read -r expected mode owner group context target relative; do
  case "$expected" in ''|'#'*) continue ;; esac
  bind_source=$BIND_ROOT/$relative
  mount -o bind "$bind_source" "$target" || fail_closed "bind mount failed for $target"
  printf '%s\n' "$target" >> "$MOUNTED"
  mount -o remount,bind,ro "$bind_source" "$target" ||
    fail_closed "read-only remount failed for $target"

  target_options="$(mountinfo_options_for_target "$target")" ||
    fail_closed "could not resolve mounted flags for $target"
  mount_options_are_read_only "$target_options" ||
    fail_closed "mounted target is not read-only: $target ($target_options)"
  mount_options_allow_exec "$target_options" ||
    fail_closed "mounted target blocks execution: $target ($target_options)"

  actual="$(sha256sum "$target" | awk '{print $1}')"
  patched="$(awk -F '|' -v p="$relative" '$1 == p { print $2; exit }' \
      "$MODDIR/patched-checksums.list")"
  [ "$actual" = "$patched" ] || fail_closed "mounted hash mismatch for $target"

  expected_mode=${mode#0}
  actual_metadata="$(stat -c '%a|%u|%g' "$target")" ||
    fail_closed "could not stat mounted target $target"
  [ "$actual_metadata" = "$expected_mode|$owner|$group" ] ||
    fail_closed "mounted metadata mismatch for $target ($actual_metadata)"
  actual_context="$(ls -Zd "$target" | awk '{print $1}')" ||
    fail_closed "could not read mounted context for $target"
  [ "$actual_context" = "$context" ] ||
    fail_closed "mounted context mismatch for $target ($actual_context)"
done < "$MODDIR/original-checksums.list"

printf '%s\n' "$PROFILE_NAME" > "$MARKER"
chmod 0600 "$MARKER"
printf 'PASS: exact build and all payloads verified; composer overlay enabled\n'
