import json
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class DiagnosticContractTests(unittest.TestCase):
    def test_protocol_v3_mode_arming_and_composer_v2_are_pinned(self):
        protocol = (ROOT / "native/common/hdmi_los_protocol.h").read_text()
        self.assertIn("#define HDMI_LOS_VERSION 2u", protocol)
        self.assertIn("#define HDMI_LOS_BROKER_VERSION 3u", protocol)
        self.assertIn("HDMI_LOS_OP_PROBE = 6", protocol)
        self.assertIn("HDMI_LOS_OP_SET_MODE = 7", protocol)
        self.assertIn("HDMI_LOS_OP_ARM = 8", protocol)
        self.assertIn("HDMI_LOS_OP_DISARM = 9", protocol)
        self.assertIn("HDMI_LOS_OP_HOTPLUG = 10", protocol)
        self.assertIn("HDMI_LOS_OP_AGENT_PROGRESS = 22", protocol)
        self.assertIn("HDMI_LOS_OP_AGENT_PROGRESS_ACK = 23", protocol)
        self.assertIn("uint32_t requested_refresh_millihz;", protocol)
        self.assertIn("uint32_t active_refresh_millihz;", protocol)
        self.assertIn("char detail[92];", protocol)
        tile = (ROOT / "android/tile/app/src/main/java/dev/kiraly/hdmilos/BrokerClient.java").read_text()
        self.assertIn("private static final short VERSION = 3", tile)
        connect = "socket.connect(new LocalSocketAddress(SOCKET, LocalSocketAddress.Namespace.ABSTRACT))"
        self.assertLess(tile.index(connect), tile.index("socket.setSoTimeout(40000)"))

    def test_candidate_release_arms_a_legacy_takeover(self):
        release = json.loads((ROOT / "release.json").read_text())
        self.assertEqual(release["version"], "0.2.8-candidate.6")
        self.assertEqual(release["version_code"], 20260916)
        self.assertFalse((ROOT / "module/diagnostic-only").exists())
        broker = (ROOT / "native/broker/main.cpp").read_text()
        toggle = broker.split("if (request.opcode == HDMI_LOS_OP_TOGGLE)", 1)[1]
        toggle = toggle.split("} else if (request.opcode == HDMI_LOS_OP_SET_MODE)", 1)[0]
        self.assertIn("Arm(request, &detail)", toggle)
        self.assertNotIn("Start(HDMI_LOS_PROBE_XORG_ATOMIC", toggle)
        self.assertIn("Start(HDMI_LOS_PROBE_XORG_LEGACY", broker)
        post_fs = (ROOT / "module/post-fs-data.sh").read_text()
        self.assertIn("resetprop -n vendor.display.disable_hw_recovery_dump 0", post_fs)

    def test_arming_journals_and_restores_android_preferred_mode(self):
        broker = (ROOT / "native/broker/main.cpp").read_text()
        self.assertIn("/data/adb/hdmi-los/preferred-mode.journal", broker)
        self.assertIn('"get-user-preferred-display-mode"', broker)
        self.assertIn('"set-user-preferred-display-mode"', broker)
        self.assertIn('"clear-user-preferred-display-mode"', broker)
        self.assertIn("write_atomic_text(kModeJournal", broker)
        self.assertIn('RecoverPreferredMode("broker startup")', broker)
        self.assertIn("preference_applied_", broker)
        self.assertIn("unplug HDMI so the preferred mode can be set safely", broker)
        self.assertIn("kModeStableSamples = 3", broker)
        self.assertIn("kModeMismatchMs = 5000", broker)

    def test_required_unplug_preserves_the_armed_request(self):
        broker = (ROOT / "native/broker/main.cpp").read_text()
        event_loop = broker.split("int Run()", 1)[1].split("private:", 1)[0]
        preserve = broker.split("void PreserveArmAcrossDisconnect()", 1)[1]
        preserve = preserve.split("void AdvanceArmed()", 1)[0]
        self.assertIn("ComposerHotplug::kDisconnected", event_loop)
        self.assertIn("else if (armed_) PreserveArmAcrossDisconnect();", event_loop)
        self.assertNotIn('Disarm("external display disconnected")', event_loop)
        self.assertIn("composer_disconnect_pending_ = false;", preserve)
        self.assertIn("replug_required_ = false;", preserve)
        self.assertIn("next_mode_poll_ms_ = 0;", preserve)

    def test_agent_requires_a_real_scanout_commit(self):
        agent = (ROOT / "native/agent/main.cpp").read_text()
        tracer = (ROOT / "native/drm-trace/drmtrace.c").read_text()
        self.assertIn("observe_scanout_record", agent)
        self.assertIn("g_scanout_connector_seen", agent)
        self.assertIn("verify_scanout()", agent)
        self.assertIn("current.fb_id != g_scanout_fb", agent)
        self.assertIn("same_mode_timing(current.mode, g_android_mode)", agent)
        self.assertIn('setenv("HDMI_LOS_SAME_MODE_PAGEFLIP_FALLBACK", "1", 1);', agent)
        self.assertIn("try_same_mode_pageflip", tracer)
        self.assertIn("DRM_IOCTL_MODE_PAGE_FLIP", tracer)
        self.assertIn('"SETCRTC_PAGEFLIP_FALLBACK"', tracer)
        self.assertIn("saved_errno == EINVAL", tracer)

    def test_composer_unplug_is_not_torn_down_on_the_uevent_thread(self):
        patch = (
            ROOT / "patches/qcom-display/v1/0010-composer-report-mode-and-defer-unplug-cleanup.patch"
        ).read_text()
        added = "\n".join(
            line[1:] for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        self.assertIn("NotifyHotplug", added)
        self.assertIn("BootTimeMs() + 5000", added)
        self.assertIn("status->active_refresh_millihz", added)
        self.assertNotIn('ReleaseHdmiLease("external display unplugged")', added)

    def test_composer_keeps_the_last_frame_active_during_acquire(self):
        patch = (
            ROOT / "patches/qcom-display/v1/0011-composer-keep-last-frame-active-for-lease.patch"
        ).read_text()
        added = "\n".join(
            line[1:] for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        self.assertIn("ToggleScreenUpdates(false)", added)
        self.assertIn("ToggleScreenUpdates(true)", added)
        self.assertNotIn("SetDisplayStatus(HWCDisplay::kDisplayStatusPause)", added)
        self.assertIn("last frame remains active", added)

    def test_composer_synchronizes_hwc_power_state_during_release(self):
        patch_name = "0012-composer-synchronize-power-state-after-lease.patch"
        patch = (
            ROOT / "patches/qcom-display/v1" / patch_name
        ).read_text()
        series = (ROOT / "patches/qcom-display/v1/series").read_text().splitlines()
        added = "\n".join(
            line[1:] for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        self.assertEqual(series[-1], patch_name)
        pause = "display->SetDisplayStatus(HWCDisplay::kDisplayStatusPause)"
        resume = "display->SetDisplayStatus(HWCDisplay::kDisplayStatusResume)"
        self.assertIn(pause, added)
        self.assertIn(resume, added)
        self.assertLess(added.index(pause), added.index(resume))
        self.assertIn("int pause_result", added)
        self.assertIn("int resume_result", added)
        self.assertIn("ToggleScreenUpdates(true)", added)

    def test_mesa_bridge_uses_a_fenced_completion_driven_worker(self):
        mesa = ROOT / "third_party/mesa-for-android-container"
        header = (mesa / "src/gallium/frontends/dri/loader_dri3_helper.h").read_text()
        source = (mesa / "src/gallium/frontends/dri/loader_dri3_helper.c").read_text()
        self.assertIn("LOADER_DRI3_SHM_BRIDGE_SLOTS 3", header)
        self.assertIn("thrd_create(&state->thread", source)
        self.assertIn("xcb_poll_for_special_event", source)
        self.assertIn("XCB_PRESENT_EVENT_MASK_COMPLETE_NOTIFY", source)
        self.assertIn("XCB_PRESENT_EVENT_MASK_IDLE_NOTIFY", source)
        self.assertIn("xcb_present_pixmap", source)
        self.assertIn("sync_wait(fence_fd, -1)", source)
        self.assertIn("poll(fds, ARRAY_SIZE(fds), -1)", source)
        self.assertIn("flush_drawable_with_fence_fd", source)
        self.assertIn("draw->swap_interval == 0 ? 4 : 3", source)
        self.assertIn("MESA_KGSL_X11_BRIDGE_STATS", source)
        self.assertIn("LOADER_DRI3_SHM_BRIDGE_ABI", source)
        self.assertIn('HDMI_LOS_MESA_BRIDGE_ABI=3', header)
        self.assertNotIn("nanosleep(", source)
        self.assertNotIn("usleep(", source)
        bridge = source.split("static bool\ndri3_shm_bridge_present(", 1)[1].split(
            "struct loader_dri3_present_sync", 1
        )[0]
        self.assertNotIn("xcb_request_check", bridge)

        runner = (ROOT / "native/agent/run-agent.sh").read_text()
        self.assertIn("HDMI_LOS_MESA_BRIDGE_ABI=3", runner)
        self.assertIn("grep -aFq", runner)
        self.assertIn("stale or incompatible", runner)
        self.assertIn("libGLX_mesa.so.0", runner)
        self.assertIn("libEGL_mesa.so.0", runner)

        glx = (mesa / "src/glx/dri3_glx.c").read_text()
        egl = (
            mesa / "src/egl/drivers/dri2/platform_x11_dri3.c"
        ).read_text()
        self.assertIn("LOADER_DRI3_SHM_BRIDGE_ABI", glx)
        self.assertIn("LOADER_DRI3_SHM_BRIDGE_ABI", egl)

    def test_phone_side_capture_is_opt_in(self):
        runner = (ROOT / "native/agent/run-agent.sh").read_text()
        self.assertIn("CAPTURE=none", runner)
        self.assertIn("workstation-powered capture", runner)

    def test_continuous_mode_is_default_and_renews_composer_watchdog(self):
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
        self.assertIn("NO_TIMEOUT=1", runner)
        self.assertIn("--no-timeout", runner)
        self.assertIn("--timeout", runner)
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

    def test_kgsl_bridge_is_default_and_safe_mode_remains_available(self):
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
        self.assertIn('setenv("FD_MESA_DEBUG", "noubwc", 1);', agent)
        self.assertIn('setenv("LD_LIBRARY_PATH", mesa.c_str(), 1);', agent)
        self.assertIn('/lib/mesa', agent)
        self.assertIn('g_kgsl_glamor ? "glamor" : "none"', agent)
        self.assertIn('g_kgsl_glamor ? "false"', agent)
        self.assertIn("DRM_IOCTL_MODE_GETCONNECTOR", agent)
        self.assertIn("DRM_IOCTL_MODE_GETCRTC", agent)
        self.assertIn("same_mode_timing(crtc.mode, advertised_mode)", agent)
        self.assertIn('Modeline \\"%s\\" %.3f', agent)
        self.assertIn('Option \\"PreferredMode\\" \\"%s\\"', agent)
        self.assertIn('Modes \\"%s\\"', agent)
        self.assertIn('"hdmi-los-android-current"', agent)
        self.assertNotIn('PreferredMode\\" \\"1920x1080', agent)
        self.assertIn("XORG_ACCEL=kgsl-kms-bridge", runner)
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

    def test_mesa_changes_can_reuse_only_the_composer(self):
        remote_build = (ROOT / "scripts/remote-build.sh").read_text()
        repackage, composer_reuse = remote_build.split(
            "if [[ $MODE == repackage ]]", 1
        )[1].split("else", 1)
        self.assertNotIn("third_party/mesa-for-android-container", repackage)
        self.assertIn("third_party/mesa-for-android-container", composer_reuse)

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
