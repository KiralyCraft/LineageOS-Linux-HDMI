#!/usr/bin/env bash
set -Eeuo pipefail

MODE=${1:-}
PROFILE=${2:-current-install}
SERVER=${3:-root@192.168.104.201}
BASE_COMMIT=${4:-}
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
    zip|port|repackage|reuse-composer)
        ;;
    *)
        printf 'usage: %s preflight|zip|port|repackage|reuse-composer PROFILE SERVER [BASE_COMMIT]\n' "$0" >&2
        exit 2
        ;;
esac

[[ $PROFILE =~ ^[a-z0-9][a-z0-9._-]*$ ]] || { printf 'invalid profile name\n' >&2; exit 2; }
test -f "$ROOT/profiles/$PROFILE.json"

if [[ $MODE == repackage || $MODE == reuse-composer ]]; then
    [[ $BASE_COMMIT =~ ^[0-9a-f]{40}$ ]] || {
        printf '%s requires a full 40-character BASE_COMMIT\n' "$MODE" >&2
        exit 2
    }
    git -C "$ROOT" cat-file -e "$BASE_COMMIT^{commit}"
    while IFS= read -r changed; do
        if [[ $MODE == repackage ]]; then
            case "$changed" in
                module/*|native/agent/run-agent.sh|docs/*|tests/*|README.md|\
                release.json|Makefile|\
                scripts/remote-build.sh|scripts/verify-dist.sh|\
                scripts/collect-crash-evidence.sh|\
                build-support/remote-entry.sh|build-support/package.sh|\
                build-support/write-build-info.py|build-support/reuse-build.sh|\
                build-support/verify-reused-build.py)
                    ;;
                *)
                    printf 'Refusing binary reuse: binary-producing path changed: %s\n' \
                        "$changed" >&2
                    exit 1
                    ;;
            esac
        else
            case "$changed" in
                .gitmodules|native/*|android/tile/*|module/*|docs/*|tests/*|README.md|\
                release.json|Makefile|scripts/remote-build.sh|scripts/verify-dist.sh|\
                scripts/collect-crash-evidence.sh|build-support/remote-entry.sh|\
                build-support/package.sh|build-support/build-native.sh|\
                build-support/build-tile.sh|build-support/write-build-info.py|\
                build-support/package-gpu-stack.sh|\
                build-support/reuse-composer.sh|\
                build-support/verify-reused-composer.py|\
                build-support/verify-reused-build.py|\
                patches/qcom-display/v1/README.md|\
                patches/qcom-display/v1/optional/*|\
                patches/xserver/*|scripts/analyze-cadence.py|\
                scripts/capture-loop.sh|third_party/mesa-for-android-container|\
                third_party/xserver)
                    ;;
                *)
                    printf 'Refusing composer reuse: composer-producing path changed: %s\n' \
                        "$changed" >&2
                    exit 1
                    ;;
            esac
        fi
    done < <(git -C "$ROOT" diff --name-only "$BASE_COMMIT" HEAD)
fi

if ! git -C "$ROOT" diff --quiet -- || ! git -C "$ROOT" diff --cached --quiet --; then
    printf 'Refusing to build an uncommitted tracked tree. Commit the intended source first.\n' >&2
    exit 1
fi

COMMIT=$(git -C "$ROOT" rev-parse HEAD)
WORK=$REMOTE_ROOT/work/$COMMIT
ARCHIVE=$(mktemp --tmpdir hdmi-los-source.XXXXXXXX.tar.gz)
trap 'rm -f -- "$ARCHIVE"' EXIT

ssh -o BatchMode=yes -- "$SERVER" bash -s -- "$WORK" <<'REMOTE'
set -Eeuo pipefail
work=$1
case "$work" in /bigdata/hdmi-los-build/work/*) ;; *) exit 2 ;; esac
rm -rf -- "$work/source" "$work/incoming.tar" "$work/incoming.tar.gz"
mkdir -p "$work/source"
REMOTE

git -C "$ROOT" archive --format=tar.gz --output="$ARCHIVE" HEAD
uploaded=false
for attempt in 1 2 3 4 5; do
    if rsync -a --partial --timeout=45 \
        -e 'ssh -o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3' \
        -- "$ARCHIVE" "$SERVER:$WORK/incoming.tar.gz"; then
        uploaded=true
        break
    fi
    printf 'source upload attempt %d failed; retrying with a new SSH connection\n' "$attempt" >&2
done
[[ $uploaded == true ]] || { printf 'source upload failed after 5 attempts\n' >&2; exit 1; }
ssh -o BatchMode=yes -- "$SERVER" bash -s -- "$WORK" <<'REMOTE'
set -Eeuo pipefail
work=$1
case "$work" in /bigdata/hdmi-los-build/work/*) ;; *) exit 2 ;; esac
test -s "$work/incoming.tar.gz"
tar -xzf "$work/incoming.tar.gz" -C "$work/source"
rm -f -- "$work/incoming.tar.gz"
REMOTE

ssh -o BatchMode=yes -- "$SERVER" \
    "$WORK/source/build-support/remote-entry.sh" "$MODE" "$PROFILE" "$COMMIT" \
    "$WORK" "${BASE_COMMIT:-$COMMIT}"

if [[ $MODE == zip || $MODE == repackage || $MODE == reuse-composer ]]; then
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
