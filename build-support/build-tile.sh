#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE=${1:?source}
BUILD=${2:?build}
SIGNING=${3:?signing}
PROFILE_NAME=${4:?profile}
PROJECT=$SOURCE/android/tile
OUT=$BUILD/tile
SDK=/bigdata/android-sdk
GRADLE=/bigdata/gradle-home/wrapper/dists/gradle-9.7.0-all/5hez1j29szzu41ldlyeeyuiv4/gradle-9.7.0/bin/gradle
PROFILE_JSON=$SOURCE/profiles/$PROFILE_NAME.json
RELEASE_JSON=$SOURCE/release.json
LINEAGE_VERSION=$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["device"]["ro.lineage.version"])' "$PROFILE_JSON")
VERSION_NAME=$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$RELEASE_JSON")
VERSION_CODE=$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["version_code"])' "$RELEASE_JSON")

mkdir -p "$OUT" "$SIGNING"
if [ ! -s "$SIGNING/password" ]; then
    umask 077
    openssl rand -hex 24 > "$SIGNING/password"
fi
if [ ! -s "$SIGNING/hdmi-los.jks" ]; then
    password=$(<"$SIGNING/password")
    keytool -genkeypair -noprompt -keystore "$SIGNING/hdmi-los.jks" \
        -storepass "$password" -keypass "$password" -alias hdmi-los \
        -keyalg RSA -keysize 4096 -validity 10000 \
        -dname 'CN=hdmi-los local module, OU=Local build, O=KiralyCraft'
    chmod 0600 "$SIGNING/password" "$SIGNING/hdmi-los.jks"
fi

rm -rf -- "$PROJECT/app/build" "$OUT"/*
ANDROID_HOME=$SDK ANDROID_SDK_ROOT=$SDK GRADLE_USER_HOME=/bigdata/gradle-home \
    "$GRADLE" --no-daemon -p "$PROJECT" -PhdmiProfile="$PROFILE_NAME" \
        -PhdmiLineage="$LINEAGE_VERSION" -PhdmiVersionName="$VERSION_NAME" \
        -PhdmiVersionCode="$VERSION_CODE" \
        :app:assembleRelease

unsigned=$PROJECT/app/build/outputs/apk/release/app-release-unsigned.apk
aligned=$OUT/HdmiLosTile-aligned.apk
signed=$OUT/HdmiLosTile.apk
"$SDK/build-tools/36.0.0/zipalign" -f -p 4 "$unsigned" "$aligned"
password=$(<"$SIGNING/password")
"$SDK/build-tools/36.0.0/apksigner" sign --ks "$SIGNING/hdmi-los.jks" \
    --ks-key-alias hdmi-los --ks-pass "pass:$password" --key-pass "pass:$password" \
    --out "$signed" "$aligned"
"$SDK/build-tools/36.0.0/apksigner" verify --verbose --print-certs "$signed" \
    > "$OUT/apksigner.txt"
