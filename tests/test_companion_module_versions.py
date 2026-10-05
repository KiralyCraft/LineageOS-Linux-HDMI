import importlib.util
import pathlib
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
