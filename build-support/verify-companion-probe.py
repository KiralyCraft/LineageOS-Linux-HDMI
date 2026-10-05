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

    def symbols(self, index):
        row = self.headers[index]
        if row[9] != 24 or row[5] % 24 or row[6] >= len(self.headers):
            raise ValueError("invalid symbol table")
        names = self.section_data(row[6])
        return [(self.string(names, entry[0]), entry[1] >> 4, entry[3])
                for entry in struct.iter_unpack("<IBBHQQ", self.section_data(index))]

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

    def calls_to(self, targets):
        calls = set()
        for index, row in enumerate(self.headers):
            if row[1] != 4:  # SHT_RELA
                continue
            if row[9] != 24 or row[5] % 24:
                raise ValueError("invalid relocation table")
            symbols = self.symbols(row[6])
            for _, information, _ in struct.iter_unpack("<QQq", self.section_data(index)):
                # R_AARCH64_JUMP26 and R_AARCH64_CALL26.
                if (information & 0xffffffff) in (282, 283):
                    name = symbols[information >> 32][0]
                    if name in targets:
                        calls.add(name)
        return sorted(calls)


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
    calls = probe.calls_to(set(required))
    if calls:
        raise ValueError(f"query-only probe calls functional imports: {calls}")
    return {
        "schema": 1,
        "result": "PASS",
        "module_sha256": sha256(module),
        "installed_msm_sha256": sha256(installed),
        "symbol_versions_sha256": sha256(symvers),
        "vermagic": info["vermagic"],
        "build_id": expected_build,
        "required_imports": required,
        "all_probe_imports": sorted(imports),
        "versioned_probe_symbols": checked,
        "installed_msm_matching_kernel_symbols": reference_checked,
        "probe_symbols_shared_with_installed_msm": shared,
        "functional_import_calls": calls,
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
