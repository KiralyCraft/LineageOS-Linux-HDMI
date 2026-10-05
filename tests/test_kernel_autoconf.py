import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "autoconf_verifier", ROOT / "build-support/verify-kernel-autoconf.py")
VERIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFIER)


class KernelAutoconfTests(unittest.TestCase):
    def test_removed_header_comment_preserves_definitions(self):
        config = '#define CONFIG_CFI_CLANG 1\n#define CONFIG_NAME "phone"\n'
        self.assertEqual(VERIFIER.compare(config, '/* generated header */\n' + config), 2)

    def test_changed_configuration_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "changed=.*CONFIG_PAHOLE_VERSION"):
            VERIFIER.compare('#define CONFIG_PAHOLE_VERSION 125\n',
                             '#define CONFIG_PAHOLE_VERSION 131\n')

    def test_missing_and_added_definitions_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "missing=.*CONFIG_CFI_CLANG.*added=.*CONFIG_OTHER"):
            VERIFIER.compare('#define CONFIG_CFI_CLANG 1\n', '#define CONFIG_OTHER 1\n')

    def test_comment_text_in_configuration_strings_is_significant(self):
        with self.assertRaisesRegex(ValueError, "changed=.*CONFIG_NAME"):
            VERIFIER.compare('#define CONFIG_NAME "/* original */"\n',
                             '#define CONFIG_NAME "/* changed */"\n')

    def test_malformed_headers_are_rejected(self):
        for content in ('/* unfinished', '', '#undef CONFIG_CFI_CLANG\n',
                        '#define CONFIG_CFI_CLANG 1\n#define CONFIG_CFI_CLANG 1\n'):
            with self.subTest(content=content), self.assertRaises(ValueError):
                VERIFIER.definitions(content)
