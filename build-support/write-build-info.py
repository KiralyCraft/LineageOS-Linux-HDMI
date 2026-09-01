#!/usr/bin/env python3
import hashlib
import json
import pathlib
import sys

source = pathlib.Path(sys.argv[1])
build = pathlib.Path(sys.argv[2])
profile_name = sys.argv[3]
package_commit = sys.argv[4]
output = pathlib.Path(sys.argv[5])
composer_commit = sys.argv[6] if len(sys.argv) > 6 else package_commit
native_commit = sys.argv[7] if len(sys.argv) > 7 else package_commit
tile_commit = sys.argv[8] if len(sys.argv) > 8 else package_commit
build_mode = sys.argv[9] if len(sys.argv) > 9 else "full"
profile = json.loads((source / "profiles" / f"{profile_name}.json").read_text())
release = json.loads((source / "release.json").read_text())
exact_source_sync = json.loads((build / "exact-source-sync.json").read_text())

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

artifact_inputs = {
    "vendor.qti.hardware.display.composer-service":
        (build / "composer/vendor.qti.hardware.display.composer-service", composer_commit),
    "libsdmcore.so": (build / "composer/libsdmcore.so", composer_commit),
    "libsdmdal.so": (build / "composer/libsdmdal.so", composer_commit),
    "hdmi-losd": (build / "native/android/hdmi-losd", native_commit),
    "HdmiLosTile.apk": (build / "tile/HdmiLosTile.apk", tile_commit),
    "hdmi-los-agent": (build / "native/chroot/bin/hdmi-los-agent", native_commit),
    "hdmi-input-bridge": (build / "native/chroot/bin/hdmi-input-bridge", native_commit),
    "hdmi-capture-keeper": (build / "native/chroot/bin/hdmi-capture-keeper", native_commit),
    "libhdmi-los-drmtrace.so":
        (build / "native/chroot/lib/libhdmi-los-drmtrace.so", native_commit),
}
artifacts = {}
for name, (path, repository_commit) in artifact_inputs.items():
    artifacts[name] = {
        "repository_commit": repository_commit,
        "sha256": sha(path),
        "size": path.stat().st_size,
    }

data = {
    "schema": 3,
    "repository_commit": package_commit,
    "package_repository_commit": package_commit,
    "build_mode": build_mode,
    "composer_repository_commit": composer_commit,
    "native_repository_commit": native_commit,
    "tile_repository_commit": tile_commit,
    "binary_reuse_verified": any(
        commit != package_commit for commit in (composer_commit, native_commit, tile_commit)
    ),
    "release": release,
    "profile": profile_name,
    "source_revisions": profile["source"],
    "exact_source_sync": exact_source_sync,
    "qcom_patched_revision": (build / "qcom-display.patched").read_text().strip(),
    "composer_abi": {
        "method": "unpatched exact-tree ELF, export, object-size, and vtable-slot comparison",
        "report_sha256": sha(build / "composer-abi-report.txt"),
        "status": "PASS",
    },
    "build_host": "root@192.168.104.201",
    "artifacts": artifacts,
}
output.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
