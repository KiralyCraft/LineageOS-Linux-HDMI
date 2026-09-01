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

    def test_tracer_identifies_property_operations_and_values(self):
        tracer = (ROOT / "native/drm-trace/drmtrace.c").read_text()
        self.assertIn('case DRM_IOCTL_MODE_GETPROPBLOB: return "MODE_GETPROPBLOB";', tracer)
        self.assertIn('"blob=%u length=%u data=0x%llx"', tracer)
        self.assertIn('"connector=%u prop=%u value=%llu"', tracer)
        self.assertIn('"OBJECT_PROPERTY"', tracer)
        self.assertIn('"request=0x%lx arg=0x%llx"', tracer)

    def test_xorg_only_suppresses_dynamically_identified_autorefresh_write(self):
        tracer = (ROOT / "native/drm-trace/drmtrace.c").read_text()
        agent = (ROOT / "native/agent/main.cpp").read_text()
        variable = "HDMI_LOS_SUPPRESS_AUTOREFRESH_SETPROPERTY"
        self.assertIn('strncmp(value.name, "autorefresh", sizeof(value.name)) == 0', tracer)
        self.assertIn("value->prop_id == property_id", tracer)
        self.assertIn('"SUPPRESSED_AUTOREFRESH_SETPROPERTY"', tracer)
        self.assertNotIn("prop_id == 53", tracer)
        self.assertIn(f'setenv("{variable}", "1", 1);', agent)
        preflight, spawn = agent.split("pid_t spawn_xorg(int lease_fd)", 1)
        self.assertNotIn(variable, preflight)
        self.assertIn(variable, spawn)


if __name__ == "__main__":
    unittest.main()
