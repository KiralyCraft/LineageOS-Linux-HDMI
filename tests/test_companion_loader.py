"""Exercise boot-loader fail-closed gates without root or display changes."""
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[1]


class LoaderTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.calls = self.root / 'calls'
        self.config = self.root / 'config'
        self.config.write_text('CONFIG_DRM=y\n')
        self.loaded = self.root / 'loaded'
        companion = self.root / 'companion'
        companion.mkdir()
        (companion / 'hdmi_companion.ko').write_bytes(b'test artifact')
        self.query = companion / 'hdmi-companion-probe'
        self.query.write_text(f'#!/bin/sh\necho query >> "{self.calls}"\n'
                              f'[ ! -f "{self.root}/query-fails" ]\n')
        self.query.chmod(0o755)
        (companion / 'SHA256SUMS').write_text(''.join(
            hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + p.name + '\n'
            for p in sorted(companion.iterdir())))
        (self.root / 'timing.env').write_text(
            "EXPECTED_RELEASE='test-release'\nEXPECTED_BUILD_ID='test-build'\n"
            f"EXPECTED_CONFIG_SHA256='{hashlib.sha256(self.config.read_bytes()).hexdigest()}'\n")
        toybox = self.root / 'toybox'
        toybox.write_text(f'''#!/bin/sh
command=$1
shift
case "$command" in
  uname) echo test-release ;;
  zcat) cat "{self.config}" ;;
  sha256sum|cut) exec "$command" "$@" ;;
  insmod) echo insmod >> "{self.calls}"; [ ! -f "{self.root}/insmod-fails" ] ;;
  *) exit 9 ;;
esac
''')
        toybox.chmod(0o755)
        # Redirect only the test copy's platform boundaries. Production still
        # uses fixed Android/kernel paths and the real identity query binary.
        script = (SOURCE / 'module/companion-loader.sh').read_text()
        script = script.replace('TOYBOX=/system/bin/toybox', f'TOYBOX="{toybox}"')
        script = script.replace('/sys/module/hdmi_companion', str(self.loaded))
        script = script.replace('/dev/hdmi_companion', '/dev/null')
        self.script = self.root / 'companion-loader.sh'
        self.script.write_text(script)

    def run_loader(self):
        result = subprocess.run(['sh', str(self.script)], capture_output=True, text=True)
        calls = self.calls.read_text().splitlines() if self.calls.exists() else []
        return result.returncode, calls

    def test_missing_module_loads_once_then_queries(self):
        self.assertEqual(self.run_loader(), (0, ['insmod', 'query']))

    def test_already_loaded_module_only_queries(self):
        self.loaded.mkdir()
        self.assertEqual(self.run_loader(), (0, ['query']))

    def test_wrong_loaded_identity_fails_without_replacement(self):
        self.loaded.mkdir()
        (self.root / 'query-fails').touch()
        code, calls = self.run_loader()
        self.assertNotEqual(code, 0)
        self.assertEqual(calls, ['query'])

    def test_changed_kernel_config_cannot_load(self):
        self.config.write_text('CONFIG_DRM=n\n')
        code, calls = self.run_loader()
        self.assertNotEqual(code, 0)
        self.assertEqual(calls, [])

    def test_changed_payload_cannot_load(self):
        self.query.write_text('#!/bin/sh\nexit 0\n')
        code, calls = self.run_loader()
        self.assertNotEqual(code, 0)
        self.assertEqual(calls, [])

    def test_insmod_failure_cannot_query(self):
        (self.root / 'insmod-fails').touch()
        code, calls = self.run_loader()
        self.assertNotEqual(code, 0)
        self.assertEqual(calls, ['insmod'])


if __name__ == '__main__':
    unittest.main()
