#!/usr/bin/env bash
set -Eeuo pipefail

MODE=${1:-}
PROFILE=${2:-current-install}
SERVER=${3:-root@192.168.104.201}
ROOT=$(git rev-parse --show-toplevel)
REMOTE_ROOT=/bigdata/hdmi-los-build

case $MODE in
    preflight)
        exec ssh -o BatchMode=yes -- "$SERVER" bash -s -- "$REMOTE_ROOT" <<'REMOTE'
set -Eeuo pipefail
root=$1
printf 'host: '; uname -n
printf 'architecture: '; uname -m
df -h / /bigdata
printf 'cpus: '; nproc
printf 'memory: '; awk '/MemTotal/ {printf "%.1f GiB\n", $2/1024/1024}' /proc/meminfo
for tool in git python docker java keytool zip unzip rsync sha256sum; do
    command -v "$tool" >/dev/null || { printf 'missing: %s\n' "$tool"; exit 1; }
done
test -x /bigdata/android-sdk/ndk/29.0.14206865/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android35-clang++
test -x /bigdata/android-sdk/build-tools/36.0.0/apksigner
test -d /bigdata/android-sdk/platforms/android-36
mkdir -p "$root" "$root/cache" "$root/work" "$root/signing"
printf 'preflight: PASS\n'
REMOTE
        ;;
    clean)
        ssh -o BatchMode=yes -- "$SERVER" bash -s -- "$REMOTE_ROOT" <<'REMOTE'
set -Eeuo pipefail
root=$1
[ "$root" = /bigdata/hdmi-los-build ] || exit 2
if [ -d "$root/work" ]; then
    find "$root/work" -mindepth 1 -maxdepth 1 -type d -mtime +2 -print -exec rm -rf -- {} +
fi
printf 'Only stale work directories below %s/work were removed. Caches and signing keys were preserved.\n' "$root"
REMOTE
        ;;
    zip|port)
        ;;
    *)
        printf 'usage: %s preflight|zip|port PROFILE SERVER\n' "$0" >&2
        exit 2
        ;;
esac

[[ $PROFILE =~ ^[a-z0-9][a-z0-9._-]*$ ]] || { printf 'invalid profile name\n' >&2; exit 2; }
test -f "$ROOT/profiles/$PROFILE.json"

if ! git -C "$ROOT" diff --quiet -- || ! git -C "$ROOT" diff --cached --quiet --; then
    printf 'Refusing to build an uncommitted tracked tree. Commit the intended source first.\n' >&2
    exit 1
fi

COMMIT=$(git -C "$ROOT" rev-parse HEAD)
WORK=$REMOTE_ROOT/work/$COMMIT

ssh -o BatchMode=yes -- "$SERVER" bash -s -- "$WORK" <<'REMOTE'
set -Eeuo pipefail
work=$1
case "$work" in /bigdata/hdmi-los-build/work/*) ;; *) exit 2 ;; esac
rm -rf -- "$work/source" "$work/incoming.tar"
mkdir -p "$work/source"
REMOTE

git -C "$ROOT" archive --format=tar HEAD | \
    ssh -o BatchMode=yes -- "$SERVER" "tar -xf - -C '$WORK/source'"

ssh -o BatchMode=yes -- "$SERVER" \
    "$WORK/source/build-support/remote-entry.sh" "$MODE" "$PROFILE" "$COMMIT" "$WORK"

if [[ $MODE == zip ]]; then
    mkdir -p "$ROOT/dist" "$ROOT/.local/signing"
    rsync -a --delete --exclude '.keep' -- "$SERVER:$WORK/output/" "$ROOT/dist/"
    if ssh -o BatchMode=yes -- "$SERVER" test -f "$REMOTE_ROOT/signing/hdmi-los.jks"; then
        rsync -a --chmod=F600 -- "$SERVER:$REMOTE_ROOT/signing/hdmi-los.jks" \
            "$SERVER:$REMOTE_ROOT/signing/password" "$ROOT/.local/signing/"
        chmod 0600 "$ROOT/.local/signing/"*
    fi
    printf 'Returned artifacts:\n'
    (cd "$ROOT/dist" && sha256sum -- * | sort)
fi

