#!/usr/bin/env bash
set -Eeuo pipefail

BASELINE=${1:?baseline artifact directory}
PATCHED=${2:?patched artifact directory}
SCRATCH=$(mktemp -d /tmp/hdmi-los-abi.XXXXXXXX)
trap 'rm -rf -- "$SCRATCH"' EXIT

artifacts=(
    vendor.qti.hardware.display.composer-service
    libsdmcore.so
    libsdmdal.so
)

elf_header() {
    readelf --file-header --wide "$1" |
        awk -F: '/^[[:space:]]*(Class|Data|OS\/ABI|ABI Version|Type|Machine):/ {
            key=$1
            value=$2
            sub(/^[[:space:]]+/, "", key)
            sub(/^[[:space:]]+/, "", value)
            print key ":" value
        }'
}

dynamic_contract() {
    readelf --dynamic --wide "$1" |
        awk '/\((NEEDED|SONAME)\)/ { print $2, $NF }' |
        LC_ALL=C sort -u
}

exported_contract() {
    readelf --dyn-syms --wide "$1" |
        awk '$1 ~ /^[0-9]+:$/ && $7 != "UND" &&
             ($5 == "GLOBAL" || $5 == "WEAK") && $8 != "" {
                 print $4 "|" $5 "|" $6 "|" $8
             }' |
        LC_ALL=C sort -u
}

for artifact in "${artifacts[@]}"; do
    old=$BASELINE/$artifact
    new=$PATCHED/$artifact
    test -f "$old"
    test -f "$new"

    elf_header "$old" > "$SCRATCH/$artifact.old.header"
    elf_header "$new" > "$SCRATCH/$artifact.new.header"
    if ! cmp -s "$SCRATCH/$artifact.old.header" "$SCRATCH/$artifact.new.header"; then
        printf 'ABI check failed: ELF identity changed for %s\n' "$artifact" >&2
        diff -u "$SCRATCH/$artifact.old.header" "$SCRATCH/$artifact.new.header" >&2 || true
        exit 1
    fi

    dynamic_contract "$old" > "$SCRATCH/$artifact.old.dynamic"
    dynamic_contract "$new" > "$SCRATCH/$artifact.new.dynamic"
    if ! cmp -s "$SCRATCH/$artifact.old.dynamic" "$SCRATCH/$artifact.new.dynamic"; then
        printf 'ABI check failed: SONAME or DT_NEEDED changed for %s\n' "$artifact" >&2
        diff -u "$SCRATCH/$artifact.old.dynamic" "$SCRATCH/$artifact.new.dynamic" >&2 || true
        exit 1
    fi

    exported_contract "$old" > "$SCRATCH/$artifact.old.exports"
    exported_contract "$new" > "$SCRATCH/$artifact.new.exports"
    test -s "$SCRATCH/$artifact.old.exports"
    comm -23 "$SCRATCH/$artifact.old.exports" "$SCRATCH/$artifact.new.exports" \
        > "$SCRATCH/$artifact.missing"
    if test -s "$SCRATCH/$artifact.missing"; then
        printf 'ABI check failed: patched %s is missing baseline exports:\n' "$artifact" >&2
        sed 's/^/  /' "$SCRATCH/$artifact.missing" >&2
        exit 1
    fi

    old_count=$(wc -l < "$SCRATCH/$artifact.old.exports")
    new_count=$(wc -l < "$SCRATCH/$artifact.new.exports")
    printf '%s: ELF/dynamic contract unchanged; exports baseline=%s patched=%s missing=0\n' \
        "$artifact" "$old_count" "$new_count"
done

printf 'composer ABI compatibility: PASS\n'
