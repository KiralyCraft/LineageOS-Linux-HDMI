#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=$(git rev-parse --show-toplevel)
FIXTURE=$ROOT/tests/fixtures/mountinfo.txt

# shellcheck source=../module/mount-utils.sh
. "$ROOT/module/mount-utils.sh"

options=$(mountinfo_options_for_target /debug_ramdisk/.magisk/modules "$FIXTURE")
[[ $options == ro,relatime ]]
mount_options_are_read_only "$options"
mount_options_allow_exec "$options"

options=$(mountinfo_options_for_target /debug_ramdisk/.magisk/modules-noexec "$FIXTURE")
! mount_options_allow_exec "$options"

options=$(mountinfo_options_for_target /debug_ramdisk/.magisk/modules-nosuid "$FIXTURE")
! mount_options_allow_exec "$options"

options=$(mountinfo_options_for_target /vendor/bin/hw/composer "$FIXTURE")
mount_options_have "$options" nosuid
! mount_options_allow_exec "$options"

! mountinfo_options_for_target /missing "$FIXTURE" >/dev/null

script=$(<"$ROOT/module/post-fs-data.sh")
[[ $script == *'mount -o bind "$bind_source" "$target"'* ]]
[[ $script == *'mount -o remount,bind,ro "$bind_source" "$target"'* ]]
[[ $script != *'mount -o bind "$source" "$target"'* ]]
[[ $script == *'.magisk/modules'* ]]

service=$(<"$ROOT/module/service.sh")
[[ $service == *'pm install -r "$MODDIR/apk/HdmiLosTile.apk" >/dev/null 2>&1'* ]]
[[ $service != *'pm install -r "$MODDIR/apk/HdmiLosTile.apk" >>'* ]]
[[ $service == *'pm path dev.kiraly.hdmilos >/dev/null 2>&1'* ]]

printf 'mount option and bind-source tests: PASS\n'
