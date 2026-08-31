#!/system/bin/sh

MODDIR=${0%/*}
STATE=/data/adb/hdmi-los
LOGDIR=$STATE/logs
MARKER=$MODDIR/compatible.ok

[ -r "$MARKER" ] || exit 0
mkdir -p "$LOGDIR"
chmod 0700 "$STATE" "$LOGDIR"

rotate_log() {
  file=$1
  [ -f "$file" ] || return 0
  size="$(wc -c < "$file" 2>/dev/null)"
  [ "${size:-0}" -lt 1048576 ] || mv -f "$file" "$file.previous"
}

rotate_log "$LOGDIR/broker.log"
"$MODDIR/bin/hdmi-losd" daemon >>"$LOGDIR/broker.log" 2>&1 &
BROKER_PID=$!

# The QS tile is an ordinary signed APK.  Install/update it only after Android
# package services are available; no permissions are granted automatically.
until [ "$(getprop sys.boot_completed)" = 1 ]; do
  kill -0 "$BROKER_PID" 2>/dev/null || exit 1
  sleep 2
done

apk_hash="$(sha256sum "$MODDIR/apk/HdmiLosTile.apk" | awk '{print $1}')"
installed_hash="$(cat "$STATE/tile.sha256" 2>/dev/null)"
if [ "$apk_hash" != "$installed_hash" ] || ! pm path dev.kiraly.hdmilos >/dev/null 2>&1; then
  if pm install -r "$MODDIR/apk/HdmiLosTile.apk" >>"$LOGDIR/tile-install.log" 2>&1; then
    printf '%s\n' "$apk_hash" > "$STATE/tile.sha256"
  fi
fi

wait "$BROKER_PID"

