#!/usr/bin/env python3
"""Reject changes to pre-existing exported C++ vtable slots."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ARTIFACTS = (
    "vendor.qti.hardware.display.composer-service",
    "libsdmcore.so",
    "libsdmdal.so",
)
RELOCATION_RE = re.compile(r"^\s*0x([0-9A-Fa-f]+)\s+(R_\S+)\s+(\S+)")


def command(*args: str | Path) -> str:
    return subprocess.check_output([str(arg) for arg in args], text=True)


def exported_vtables(path: Path) -> dict[str, tuple[int, int]]:
    result: dict[str, tuple[int, int]] = {}
    for line in command("readelf", "--dyn-syms", "--wide", path).splitlines():
        fields = line.split()
        if (
            len(fields) >= 8
            and fields[0].endswith(":")
            and fields[3] == "OBJECT"
            and fields[4] in {"GLOBAL", "WEAK"}
            and fields[6] != "UND"
            and fields[7].startswith("_ZTV")
        ):
            result[fields[7]] = (int(fields[1], 16), int(fields[2], 10))
    return result


def relocation_slots(path: Path, llvm_readobj: Path) -> dict[int, tuple[str, ...]]:
    result: dict[int, tuple[str, ...]] = {}
    output = command(llvm_readobj, "--relocations", path)
    for line in output.splitlines():
        match = RELOCATION_RE.match(line)
        if not match:
            continue
        offset = int(match.group(1), 16)
        relocation_type = match.group(2)
        symbol = match.group(3)
        # Local/hidden function addresses are encoded as RELATIVE addends and
        # naturally move as code is added.  Preserve the slot kind, while named
        # relocations must continue to name the exact same virtual function.
        if relocation_type.endswith("_RELATIVE"):
            result[offset] = (relocation_type,)
        else:
            result[offset] = (relocation_type, symbol)
    return result


def compare_artifact(baseline: Path, patched: Path, llvm_readobj: Path) -> list[str]:
    failures: list[str] = []
    old_tables = exported_vtables(baseline)
    new_tables = exported_vtables(patched)
    old_relocations = relocation_slots(baseline, llvm_readobj)
    new_relocations = relocation_slots(patched, llvm_readobj)

    for name, (old_address, old_size) in sorted(old_tables.items()):
        if name not in new_tables:
            failures.append(f"{baseline.name}: missing vtable {name}")
            continue
        new_address, new_size = new_tables[name]
        if old_size != new_size:
            failures.append(
                f"{baseline.name}: vtable {name} size changed {old_size}->{new_size}"
            )
            continue
        for relative_offset in range(0, old_size, 8):
            old_slot = old_relocations.get(old_address + relative_offset)
            new_slot = new_relocations.get(new_address + relative_offset)
            if old_slot != new_slot:
                failures.append(
                    f"{baseline.name}: vtable {name}+0x{relative_offset:x} "
                    f"changed {old_slot!r}->{new_slot!r}"
                )

    if not failures:
        print(
            f"{baseline.name}: existing vtables={len(old_tables)}; "
            "sizes and relocation slots unchanged"
        )
    return failures


def main() -> int:
    if len(sys.argv) != 4:
        print(
            "usage: verify-vtable-abi.py BASELINE_DIR PATCHED_DIR LLVM_READOBJ",
            file=sys.stderr,
        )
        return 2
    baseline_dir = Path(sys.argv[1])
    patched_dir = Path(sys.argv[2])
    llvm_readobj = Path(sys.argv[3])
    if not llvm_readobj.is_file():
        print(f"missing llvm-readobj: {llvm_readobj}", file=sys.stderr)
        return 2

    failures: list[str] = []
    for artifact in ARTIFACTS:
        failures.extend(
            compare_artifact(
                baseline_dir / artifact,
                patched_dir / artifact,
                llvm_readobj,
            )
        )
    if failures:
        print("vtable ABI check failed:", file=sys.stderr)
        for failure in failures[:100]:
            print(f"  {failure}", file=sys.stderr)
        if len(failures) > 100:
            print(f"  ... and {len(failures) - 100} more", file=sys.stderr)
        return 1
    print("vtable ABI compatibility: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
