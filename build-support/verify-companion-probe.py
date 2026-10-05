#!/usr/bin/env python3
"""Inspect real module ELF/version data without loading a module."""
import argparse
import hashlib
import json
import re
import struct
from pathlib import Path


class ElfModule:
    def __init__(self, path):
        self.path = Path(path)
        self.data = self.path.read_bytes()
        if len(self.data) < 64 or self.data[:6] != b"\x7fELF\x02\x01":
            raise ValueError(f"{path}: expected ELF64 little endian")
        header = struct.unpack_from("<16sHHIQQQIHHHHHH", self.data)
        if header[1] != 1 or header[2] != 183:
            raise ValueError(f"{path}: expected AArch64 relocatable module")
        offset, entry_size, count, names_index = header[6], header[11], header[12], header[13]
        if entry_size != 64 or not count or names_index >= count:
            raise ValueError("unsupported section header layout")
        if offset + entry_size * count > len(self.data):
            raise ValueError("truncated section headers")
        self.headers = [struct.unpack_from("<IIQQQQIIQQ", self.data, offset + index * 64)
                        for index in range(count)]
        names = self.section_data(names_index)
        self.sections = {self.string(names, row[0]): index
                         for index, row in enumerate(self.headers)}

    @staticmethod
    def string(data, offset):
        if offset >= len(data):
            raise ValueError("string offset outside ELF string table")
        end = data.find(b"\0", offset)
        if end < 0:
            raise ValueError("unterminated ELF string")
        return data[offset:end].decode("ascii")

    def section_data(self, index):
        row = self.headers[index]
        if row[1] == 8:  # SHT_NOBITS does not occupy file storage.
            return b""
        offset, size = row[4], row[5]
        if offset + size > len(self.data):
            raise ValueError("section outside ELF file")
        return self.data[offset:offset + size]

    def symbol_entries(self, index):
        row = self.headers[index]
        if row[9] != 24 or row[5] % 24 or row[6] >= len(self.headers):
            raise ValueError("invalid symbol table")
        names = self.section_data(row[6])
        return [(self.string(names, entry[0]), entry[1] >> 4, entry[3],
                 entry[4], entry[5], entry[1] & 15)
                for entry in struct.iter_unpack("<IBBHQQ", self.section_data(index))]

    def symbols(self, index):
        return [entry[:3] for entry in self.symbol_entries(index)]

    def imported_symbols(self):
        return {name for name, binding, section in self.symbols(self.sections[".symtab"])
                if name and binding in (1, 2) and section == 0}

    def defined_symbols(self):
        return {name for name, _, section in self.symbols(self.sections[".symtab"])
                if name and section != 0}

    def versions(self):
        data = self.section_data(self.sections["__versions"])
        if not data or len(data) % 64:
            raise ValueError("invalid AArch64 module version table")
        versions = {}
        for offset in range(0, len(data), 64):
            crc = struct.unpack_from("<Q", data, offset)[0]
            name = self.string(data[offset + 8:offset + 64], 0)
            if name in versions:
                raise ValueError("duplicate version entry")
            versions[name] = crc
        if "module_layout" not in versions:
            raise ValueError("missing module_layout version")
        return versions

    def modinfo(self):
        result = {}
        for field in self.section_data(self.sections[".modinfo"]).split(b"\0"):
            if b"=" in field:
                key, value = field.decode("ascii").split("=", 1)
                result[key] = value
        return result

    def branches_to(self, targets):
        calls, trampolines = set(), set()
        entries = self.symbol_entries(self.sections[".symtab"])
        thunks = {(section, value): name[:-7]
                  for name, _, section, value, size, kind in entries
                  if name.endswith(".cfi_jt") and kind == 2 and size == 8
                  and name[:-7] in targets}
        for index, row in enumerate(self.headers):
            if row[1] != 4:  # SHT_RELA
                continue
            if row[9] != 24 or row[5] % 24:
                raise ValueError("invalid relocation table")
            symbols = self.symbol_entries(row[6])
            for offset, information, addend in struct.iter_unpack("<QQq", self.section_data(index)):
                # R_AARCH64_JUMP26 and R_AARCH64_CALL26.
                if (information & 0xffffffff) in (282, 283):
                    name, _, section, value, _, _ = symbols[information >> 32]
                    target = name if name in targets else thunks.get((section, value + addend))
                    if not target:
                        continue
                    # Full-LTO CFI emits an eight-byte BTI + B import thunk
                    # when taking a typed function address. Its relocation is
                    # not a call from the probe's init/ioctl/exit code. Check
                    # the actual instructions and symbol extent, not a name
                    # or section-name exemption.
                    origin = thunks.get((row[7], offset - 4))
                    code = self.section_data(row[7])
                    if (name == target == origin and addend == 0 and section == 0
                            and (information & 0xffffffff) == 282 and offset >= 4
                            and code[offset - 4:offset + 4] ==
                            struct.pack("<II", 0xd503245f, 0x14000000)):
                        trampolines.add(target)
                    else:
                        calls.add(target)
        # A local branch can be resolved by the linker without a relocation.
        for section, row in enumerate(self.headers):
            if row[1] != 1 or not row[2] & 4:  # Executable SHT_PROGBITS.
                continue
            code = self.section_data(section)
            for offset in range(0, len(code) - 3, 4):
                instruction = struct.unpack_from("<I", code, offset)[0]
                if instruction & 0x7c000000 != 0x14000000:  # B or BL.
                    continue
                displacement = instruction & 0x03ffffff
                if displacement & 0x02000000:
                    displacement -= 0x04000000
                target = thunks.get((section, offset + displacement * 4))
                if target:
                    calls.add(target)
        return sorted(calls), sorted(trampolines)

    def calls_to(self, targets):
        return self.branches_to(targets)[0]


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_symvers(path):
    result = {}
    for line in Path(path).read_text().splitlines():
        columns = line.split()
        if len(columns) >= 3:
            result[columns[1]] = int(columns[0], 16)
    if not result or "module_layout" not in result:
        raise ValueError("incomplete kernel symbol versions")
    return result


def compare_versions(versions, kernel, require_all=True):
    missing = sorted(set(versions) - set(kernel))
    mismatched = sorted(name for name in versions.keys() & kernel.keys()
                        if versions[name] != kernel[name])
    if mismatched or (require_all and missing):
        raise ValueError(f"symbol versions differ: mismatched={mismatched}, missing={missing}")
    return sorted(versions.keys() & kernel.keys())


def verify(module, installed, symvers, header, expected_release, expected_build):
    probe = ElfModule(module)
    reference = ElfModule(installed)
    kernel = read_symvers(symvers)
    required = re.findall(r"X\(([a-zA-Z_][a-zA-Z0-9_]*),\s*\d+\)", Path(header).read_text())
    if len(required) != 27 or len(set(required)) != 27:
        raise ValueError("unexpected probe import contract")
    imports = probe.imported_symbols()
    if set(required) - imports:
        raise ValueError(f"LTO removed required imports: {sorted(set(required) - imports)}")
    versions = probe.versions()
    if imports - versions.keys():
        raise ValueError(f"unversioned imports: {sorted(imports - versions.keys())}")
    checked = compare_versions(versions, kernel)
    reference_checked = compare_versions(reference.versions(), kernel, require_all=False)
    if len(reference_checked) < 20:
        raise ValueError("insufficient installed-module symbol-version coverage")
    shared = sorted(versions.keys() & reference.versions().keys())
    if "module_layout" not in shared:
        raise ValueError("probe/reference layout was not compared")
    info, reference_info = probe.modinfo(), reference.modinfo()
    if reference_info.get("scmversion") != "gec2e039129f2":
        raise ValueError("installed DRM module does not match the pinned vendor revision")
    if info.get("vermagic") != reference_info.get("vermagic"):
        raise ValueError("probe vermagic differs from installed msm_drm")
    if not info.get("vermagic", "").startswith(expected_release + " "):
        raise ValueError("kernel release mismatch")
    if info.get("version") != expected_build:
        raise ValueError("probe build identity mismatch")
    if info.get("name") != "hdmi_companion_probe" or info.get("license") != "GPL":
        raise ValueError("unexpected module identity/license")
    if "__cfi_check" not in probe.defined_symbols():
        raise ValueError("probe lacks cross-DSO CFI instrumentation")
    calls, trampolines = probe.branches_to(set(required))
    if calls:
        raise ValueError(f"query-only probe calls functional imports: {calls}")
    return {
        "schema": 1,
        "result": "PASS",
        "module_sha256": sha256(module),
        "installed_msm_sha256": sha256(installed),
        "installed_msm_scmversion": reference_info["scmversion"],
        "symbol_versions_sha256": sha256(symvers),
        "vermagic": info["vermagic"],
        "build_id": expected_build,
        "required_imports": required,
        "all_probe_imports": sorted(imports),
        "versioned_probe_symbols": checked,
        "installed_msm_matching_kernel_symbols": reference_checked,
        "probe_symbols_shared_with_installed_msm": shared,
        "functional_import_calls": calls,
        "cfi_import_trampolines": trampolines,
        "cfi_check_present": True,
        "on_device_load_test": "pending; module not installed or loaded",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("module", type=Path)
    parser.add_argument("installed_msm", type=Path)
    parser.add_argument("symbol_versions", type=Path)
    parser.add_argument("uapi_header", type=Path)
    parser.add_argument("expected_release")
    parser.add_argument("expected_build")
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    report = verify(arguments.module, arguments.installed_msm, arguments.symbol_versions,
                    arguments.uapi_header, arguments.expected_release, arguments.expected_build)
    arguments.output.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Module ABI: PASS; {len(report['versioned_probe_symbols'])} probe versions; "
          f"{len(report['installed_msm_matching_kernel_symbols'])} installed DRM versions")


if __name__ == "__main__":
    main()
