#!/usr/bin/env python3
import hashlib
import json
import pathlib
import sys

info_path = pathlib.Path(sys.argv[1])
build = pathlib.Path(sys.argv[2])
expected_commit = sys.argv[3]
info = json.loads(info_path.read_text())

artifacts = {
    "vendor.qti.hardware.display.composer-service":
        build / "composer/vendor.qti.hardware.display.composer-service",
    "libsdmcore.so": build / "composer/libsdmcore.so",
    "libsdmdal.so": build / "composer/libsdmdal.so",
    "hdmi-losd": build / "native/android/hdmi-losd",
    "HdmiLosTile.apk": build / "tile/HdmiLosTile.apk",
    "hdmi-los-agent": build / "native/chroot/bin/hdmi-los-agent",
    "hdmi-input-bridge": build / "native/chroot/bin/hdmi-input-bridge",
    "hdmi-capture-keeper": build / "native/chroot/bin/hdmi-capture-keeper",
}

if not (build / "native/chroot/bin").is_dir():
    artifacts["hdmi-los-agent"] = build / "native/chroot/hdmi-los-agent"
    artifacts["hdmi-input-bridge"] = build / "native/chroot/hdmi-input-bridge"
    artifacts["hdmi-capture-keeper"] = build / "native/chroot/hdmi-capture-keeper"
if "libhdmi-los-drmtrace.so" in info["artifacts"]:
    artifacts["libhdmi-los-drmtrace.so"] = \
        build / "native/chroot/lib/libhdmi-los-drmtrace.so"

for name, path in artifacts.items():
    expected = info["artifacts"][name]
    recorded_commit = expected.get(
        "repository_commit",
        info.get("binary_repository_commit", info["repository_commit"]),
    )
    assert recorded_commit == expected_commit, (
        f"base build commit mismatch for {name}: expected {expected_commit}, "
        f"found {recorded_commit}"
    )
    assert path.is_file(), f"missing reusable artifact: {path}"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert digest == expected["sha256"], f"reusable artifact hash mismatch: {name}"
    assert path.stat().st_size == expected["size"], f"reusable artifact size mismatch: {name}"

print(f"reusable binary build verified at {expected_commit}")
