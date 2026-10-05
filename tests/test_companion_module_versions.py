import importlib.util
import pathlib
import struct
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "companion_verifier", ROOT / "build-support/verify-companion-probe.py")
VERIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFIER)


class ModuleVersionTests(unittest.TestCase):
    def test_missing_import_version_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "missing=.*drm_crtc_vblank_get"):
            VERIFIER.compare_versions({"module_layout": 1, "drm_crtc_vblank_get": 2},
                                      {"module_layout": 1})

    def test_mismatched_layout_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "mismatched=.*module_layout"):
            VERIFIER.compare_versions({"module_layout": 1}, {"module_layout": 2})

    def test_external_vendor_dependencies_do_not_hide_core_mismatch(self):
        with self.assertRaisesRegex(ValueError, "mismatched=.*drm_ioctl"):
            VERIFIER.compare_versions({"drm_ioctl": 1, "vendor_only": 3},
                                      {"drm_ioctl": 2}, require_all=False)

    def test_matching_core_versions_are_reported(self):
        self.assertEqual(VERIFIER.compare_versions(
            {"drm_ioctl": 1, "vendor_only": 3}, {"drm_ioctl": 1}, require_all=False),
            ["drm_ioctl"])

    def test_truncated_elf_is_rejected(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "truncated.ko"
            path.write_bytes(b"\x7fELF\x02\x01")
            with self.assertRaises(ValueError):
                VERIFIER.ElfModule(path)


class ProbeBranchTests(unittest.TestCase):
    def probe(self, relocation=282, code=(0xd503245f, 0x14000000),
              thunk_size=8, thunk_name="drm_ioctl.cfi_jt", addend=0,
              extra_call=False, call_via_thunk=False, raw_call_via_thunk=False,
              valid_group=False):
        # Minimal post-link section/symbol model; no compiler or module load.
        elf = object.__new__(VERIFIER.ElfModule)
        elf.sections = {".symtab": 2}
        elf.headers = [(0,) * 10, (0, 1, 4, 0, 0, 16, 0, 0, 4, 0),
                       (0, 2, 0, 0, 0, 0, 0, 0, 8, 24),
                       (0, 4, 0, 0, 0, 24 * (1 + extra_call), 2, 1, 8, 24)]
        symbols = [("drm_ioctl", 1, 0, 0, 0, 2),
                   (thunk_name, 0, 1, 0, thunk_size, 2),
                   ("", 0, 1, 0, 0, 3)]
        records = [(4, relocation, addend)]
        if extra_call:
            records.append((12, (2 << 32 if call_via_thunk else 0) | 283, 0))
        tail = (0xd503245f, 0x14000000) if valid_group else (
            0xd503201f, 0x97fffffd if raw_call_via_thunk else 0x94000000)
        sections = {1: struct.pack("<IIII", *code, *tail),
                    3: b"".join(struct.pack("<QQq", *record) for record in records)}
        elf.symbol_entries = lambda index: symbols
        elf.section_data = lambda index: sections[index]
        return elf

    def test_exact_cfi_address_thunk_is_reported_separately(self):
        self.assertEqual(self.probe().branches_to({"drm_ioctl"}), ([], ["drm_ioctl"]))

    def test_grouped_cfi_symbol_extent_is_validated(self):
        self.assertEqual(self.probe(thunk_size=16, valid_group=True).branches_to({"drm_ioctl"}),
                         ([], ["drm_ioctl"]))

    def test_call_relocation_is_rejected_even_with_thunk_name(self):
        self.assertEqual(self.probe(relocation=283).calls_to({"drm_ioctl"}), ["drm_ioctl"])

    def test_wrong_instructions_size_name_or_addend_cannot_hide_branch(self):
        for arguments in ({"code": (0xd503201f, 0x14000000)}, {"thunk_size": 16},
                          {"thunk_name": "pretend.cfi_jt"}, {"addend": 4}):
            with self.subTest(arguments=arguments):
                self.assertEqual(self.probe(**arguments).calls_to({"drm_ioctl"}), ["drm_ioctl"])

    def test_real_call_is_rejected_alongside_valid_address_thunk(self):
        self.assertEqual(self.probe(extra_call=True).branches_to({"drm_ioctl"}),
                         (["drm_ioctl"], ["drm_ioctl"]))

    def test_call_to_local_thunk_is_also_rejected(self):
        self.assertEqual(self.probe(extra_call=True, call_via_thunk=True).calls_to({"drm_ioctl"}),
                         ["drm_ioctl"])

    def test_linker_resolved_call_to_local_thunk_is_rejected(self):
        self.assertEqual(self.probe(raw_call_via_thunk=True).calls_to({"drm_ioctl"}),
                         ["drm_ioctl"])
