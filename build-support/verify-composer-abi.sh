#!/usr/bin/env bash
set -Eeuo pipefail

# readelf symbol names contain punctuation whose collation differs between
# developer shells.  sort, comm, and join must all use the same byte ordering.
export LC_ALL=C

BASELINE=${1:?baseline artifact directory}
PATCHED=${2:?patched artifact directory}
LLVM_READOBJ=${3:-${LLVM_READOBJ:-}}
SCRATCH=$(mktemp -d /tmp/hdmi-los-abi.XXXXXXXX)
trap 'rm -rf -- "$SCRATCH"' EXIT

if [[ -z $LLVM_READOBJ ]]; then
    LLVM_READOBJ=$(command -v llvm-readobj || true)
fi
[[ -n $LLVM_READOBJ && -x $LLVM_READOBJ ]] || {
    printf 'ABI check failed: llvm-readobj is required for vtable verification\n' >&2
    exit 2
}

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

exported_object_sizes() {
    readelf --dyn-syms --wide "$1" |
        awk '$1 ~ /^[0-9]+:$/ && $7 != "UND" && $4 == "OBJECT" &&
             ($5 == "GLOBAL" || $5 == "WEAK") && $8 != "" {
                 print $8 "|" $3
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

    exported_object_sizes "$old" > "$SCRATCH/$artifact.old.objects"
    exported_object_sizes "$new" > "$SCRATCH/$artifact.new.objects"
    join -t '|' -j 1 "$SCRATCH/$artifact.old.objects" "$SCRATCH/$artifact.new.objects" |
        awk -F '|' '$2 != $3 { print $1 "|" $2 "|" $3 }' \
        > "$SCRATCH/$artifact.changed-objects"
    if test -s "$SCRATCH/$artifact.changed-objects"; then
        printf 'ABI check failed: patched %s changes existing exported object sizes:\n' \
            "$artifact" >&2
        sed 's/^/  /' "$SCRATCH/$artifact.changed-objects" >&2
        exit 1
    fi

    old_count=$(wc -l < "$SCRATCH/$artifact.old.exports")
    new_count=$(wc -l < "$SCRATCH/$artifact.new.exports")
    printf '%s: ELF/dynamic contract unchanged; exports baseline=%s patched=%s missing=0\n' \
        "$artifact" "$old_count" "$new_count"
done

python "$(dirname "$0")/verify-vtable-abi.py" "$BASELINE" "$PATCHED" "$LLVM_READOBJ"
printf 'composer ABI compatibility: PASS\n'
