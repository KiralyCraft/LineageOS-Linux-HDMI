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

    def test_candidate_release_selects_atomic_tile_takeover(self):
        release = json.loads((ROOT / "release.json").read_text())
        self.assertEqual(release["version"], "0.2.7-candidate.1")
        self.assertEqual(release["version_code"], 20260908)
        self.assertFalse((ROOT / "module/diagnostic-only").exists())
        broker = (ROOT / "native/broker/main.cpp").read_text()
        toggle = broker.split("if (request.opcode == HDMI_LOS_OP_TOGGLE)", 1)[1]
        toggle = toggle.split("} else if (request.opcode == HDMI_LOS_OP_PROBE)", 1)[0]
        self.assertIn("Start(HDMI_LOS_PROBE_XORG_ATOMIC", toggle)
        self.assertNotIn("Start(HDMI_LOS_PROBE_XORG_LEGACY", toggle)
        post_fs = (ROOT / "module/post-fs-data.sh").read_text()
        self.assertIn("resetprop -n vendor.display.disable_hw_recovery_dump 0", post_fs)

    def test_phone_side_capture_is_opt_in(self):
        runner = (ROOT / "native/agent/run-agent.sh").read_text()
        self.assertIn("CAPTURE=none", runner)
        self.assertIn("workstation-powered capture", runner)

    def test_continuous_mode_is_explicit_and_renews_composer_watchdog(self):
        protocol = (ROOT / "native/common/hdmi_los_protocol.h").read_text()
        broker = (ROOT / "native/broker/main.cpp").read_text()
        agent = (ROOT / "native/agent/main.cpp").read_text()
        runner = (ROOT / "native/agent/run-agent.sh").read_text()
        composer_patch = (
            ROOT / "patches/qcom-display/v1/0009-composer-support-renewable-continuous-leases.patch"
        ).read_text()

        self.assertIn("#define HDMI_LOS_FLAG_CONTINUOUS 0x80000000u", protocol)
        self.assertIn("constexpr int kSessionSeconds = 60;", broker)
        self.assertIn("constexpr int kComposerHeartbeatSeconds = 20;", broker)
        self.assertIn("active_ && deadline_ms_ > 0", broker)
        self.assertIn("ComposerRequest(HDMI_LOS_OP_PING", broker)
        self.assertIn("HDMI_LOS_FLAG_CONTINUOUS", broker)
        self.assertIn('strcmp(argv[i], "--no-timeout") == 0', agent)
        self.assertIn("registration.flags = g_no_timeout", agent)
        self.assertIn("NO_TIMEOUT=0", runner)
        self.assertIn("--no-timeout", runner)
        self.assertIn("continuous_lease_", composer_patch)
        self.assertIn(
            "deadline_ms_ = BootTimeMs() + kComposerFailsafeSeconds * 1000",
            composer_patch,
        )
        self.assertNotIn("explicit no-timeout lease", composer_patch)

    def test_lxde_uses_a_chroot_local_temp_directory(self):
        agent = (ROOT / "native/agent/main.cpp").read_text()
        spawn = agent.split("pid_t spawn_lxde()", 1)[1]
        spawn = spawn.split("bool xorg_ready()", 1)[0]
        self.assertIn('setenv("TMPDIR", "/tmp", 1);', spawn)

    def test_kgsl_glamor_is_explicit_and_safe_mode_remains_default(self):
        agent = (ROOT / "native/agent/main.cpp").read_text()
        runner = (ROOT / "native/agent/run-agent.sh").read_text()
        self.assertIn("bool g_kgsl_glamor = false;", agent)
        self.assertIn("bool g_kgsl_kms_bridge = false;", agent)
        self.assertIn('strcmp(value, "kgsl-glamor") == 0', agent)
        self.assertIn('strcmp(value, "kgsl-kms-bridge") == 0', agent)
        self.assertIn('setenv("MESA_LOADER_DRIVER_OVERRIDE", "kgsl", 1);', agent)
        self.assertIn('setenv("FD_FORCE_KGSL", "1", 1);', agent)
        self.assertIn('setenv("FD_KGSL_ENABLE_DMABUF", "1", 1);', agent)
        self.assertIn('setenv("FD_KGSL_USE_KMS_DUMB", "1", 1);', agent)
        self.assertIn('setenv("FD_KGSL_KMS_DEVICE", "/dev/dri/card0", 1);', agent)
        self.assertIn('setenv("MESA_KGSL_X11_SHM_BRIDGE", "1", 1);', agent)
        self.assertIn('setenv("FD_MESA_DEBUG", "notile,noubwc", 1);', agent)
        self.assertIn('setenv("LD_LIBRARY_PATH", mesa.c_str(), 1);', agent)
        self.assertIn('/lib/mesa', agent)
        self.assertIn('g_kgsl_glamor ? "glamor" : "none"', agent)
        self.assertIn('g_kgsl_glamor ? "false"', agent)
        self.assertIn('Option \\"PreferredMode\\" \\"1920x1080\\"', agent)
        self.assertIn('Modes \\"1920x1080\\"', agent)
        self.assertIn("XORG_ACCEL=safe", runner)
        self.assertIn("SESSION=lxde", runner)
        self.assertIn("--xorg-accel safe|kgsl-glamor|kgsl-kms-bridge", runner)
        self.assertIn("--session lxde|none", runner)

        xorg_spawn = agent.split("pid_t spawn_xorg(int lease_fd)", 1)[1]
        xorg_spawn = xorg_spawn.split("pid_t spawn_lxde()", 1)[0]
        lxde_spawn = agent.split("pid_t spawn_lxde()", 1)[1]
        lxde_spawn = lxde_spawn.split("bool xorg_ready()", 1)[0]
        self.assertIn("configure_gpu_environment(true);", xorg_spawn)
        self.assertIn("configure_gpu_environment(false);", lxde_spawn)

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

    def test_xorg_only_suppresses_connector_property_snapshot_noops(self):
        tracer = (ROOT / "native/drm-trace/drmtrace.c").read_text()
        agent = (ROOT / "native/agent/main.cpp").read_text()
        variable = "HDMI_LOS_SUPPRESS_CONNECTOR_PROPERTY_NOOPS"
        self.assertIn("value.obj_type == DRM_MODE_OBJECT_CONNECTOR", tracer)
        self.assertIn("hdmi_los_property_cache_is_noop", tracer)
        self.assertIn('"SUPPRESSED_CONNECTOR_NOOP"', tracer)
        self.assertNotIn("prop_id == 53", tracer)
        self.assertNotIn('"autorefresh"', tracer)
        self.assertIn(f'setenv("{variable}", "1", 1);', agent)
        preflight, spawn = agent.split("pid_t spawn_xorg(int lease_fd)", 1)
        self.assertNotIn(variable, preflight)
        self.assertIn(variable, spawn)

    def test_xorg_ignores_unsupported_bitmask_properties(self):
        tracer = (ROOT / "native/drm-trace/drmtrace.c").read_text()
        agent = (ROOT / "native/agent/main.cpp").read_text()
        variable = "HDMI_LOS_IGNORE_XORG_BITMASK_PROPERTIES"
        self.assertIn('dlsym(RTLD_NEXT, "drmModeGetProperty")', tracer)
        self.assertIn("DRM_MODE_PROP_BITMASK", tracer)
        self.assertIn('"IGNORED_XORG_BITMASK"', tracer)
        self.assertIn(f'setenv("{variable}", "1", 1);', agent)
        preflight, spawn = agent.split("pid_t spawn_xorg(int lease_fd)", 1)
        self.assertNotIn(variable, preflight)
        self.assertIn(variable, spawn)

    def test_xorg_ignores_write_only_pointer_properties(self):
        tracer = (ROOT / "native/drm-trace/drmtrace.c").read_text()
        agent = (ROOT / "native/agent/main.cpp").read_text()
        variable = "HDMI_LOS_IGNORE_XORG_POINTER_PROPERTIES"
        self.assertIn('strcmp(property->name, "RETIRE_FENCE") == 0', tracer)
        self.assertIn('"IGNORED_XORG_POINTER"', tracer)
        self.assertIn(f'setenv("{variable}", "1", 1);', agent)
        preflight, spawn = agent.split("pid_t spawn_xorg(int lease_fd)", 1)
        self.assertNotIn(variable, preflight)
        self.assertIn(variable, spawn)

    def test_composer_leases_the_crtc_fixed_primary_plane(self):
        patch = (ROOT / "patches/qcom-display/v1/0008-sdm-lease-the-CRTC-fixed-primary-plane.patch").read_text()
        added = "\n".join(
            line[1:] for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        self.assertIn("drmModeGetResources(dev_fd_)", patch)
        self.assertIn("drm_resources->crtcs[i] == token_.crtc_id", patch)
        self.assertIn("primary_index == crtc_index", patch)
        self.assertIn("fixed-primary-plane", patch)
        self.assertNotIn("plane->crtc_id == token_.crtc_id &&", added)


if __name__ == "__main__":
    unittest.main()
