import json
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class DiagnosticContractTests(unittest.TestCase):
    def test_protocol_v2_and_probe_opcodes_are_pinned(self):
        protocol = (ROOT / "native/common/hdmi_los_protocol.h").read_text()
        self.assertIn("#define HDMI_LOS_VERSION 1u", protocol)
        self.assertIn("#define HDMI_LOS_BROKER_VERSION 2u", protocol)
        self.assertIn("HDMI_LOS_OP_PROBE = 6", protocol)
        self.assertIn("HDMI_LOS_OP_AGENT_PROGRESS = 22", protocol)
        self.assertIn("HDMI_LOS_OP_AGENT_PROGRESS_ACK = 23", protocol)
        tile = (ROOT / "android/tile/app/src/main/java/dev/kiraly/hdmilos/BrokerClient.java").read_text()
        self.assertIn("private static final short VERSION = 2", tile)

    def test_diagnostic_release_fails_safe(self):
        release = json.loads((ROOT / "release.json").read_text())
        self.assertEqual(release["version"], "0.2.5-diagnostic.1")
        self.assertEqual(release["version_code"], 20260906)
        self.assertTrue((ROOT / "module/diagnostic-only").is_file())
        broker = (ROOT / "native/broker/main.cpp").read_text()
        self.assertIn("diagnostic build: use a root probe command", broker)
        post_fs = (ROOT / "module/post-fs-data.sh").read_text()
        self.assertIn("resetprop -n vendor.display.disable_hw_recovery_dump 0", post_fs)

    def test_phone_side_capture_is_opt_in(self):
        runner = (ROOT / "native/agent/run-agent.sh").read_text()
        self.assertIn("CAPTURE=none", runner)
        self.assertIn("workstation-powered capture", runner)

    def test_tracer_is_packaged_from_chroot_lib(self):
        package = (ROOT / "build-support/package.sh").read_text()
        build = (ROOT / "build-support/build-native.sh").read_text()
        self.assertIn('cp "$BUILD/native/chroot/lib/"* "$CHROOT/lib/"', package)
        self.assertIn("libhdmi-los-drmtrace.so", build)


if __name__ == "__main__":
    unittest.main()
