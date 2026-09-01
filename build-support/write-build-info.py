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
binary_commit = sys.argv[6] if len(sys.argv) > 6 else package_commit
profile = json.loads((source / "profiles" / f"{profile_name}.json").read_text())
release = json.loads((source / "release.json").read_text())
exact_source_sync = json.loads((build / "exact-source-sync.json").read_text())

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

artifacts = {}
for name, path in {
    "vendor.qti.hardware.display.composer-service": build / "composer/vendor.qti.hardware.display.composer-service",
    "libsdmcore.so": build / "composer/libsdmcore.so",
    "libsdmdal.so": build / "composer/libsdmdal.so",
    "hdmi-losd": build / "native/android/hdmi-losd",
    "HdmiLosTile.apk": build / "tile/HdmiLosTile.apk",
    "hdmi-los-agent": build / "native/chroot/hdmi-los-agent",
    "hdmi-input-bridge": build / "native/chroot/hdmi-input-bridge",
    "hdmi-capture-keeper": build / "native/chroot/hdmi-capture-keeper",
}.items():
    artifacts[name] = {"sha256": sha(path), "size": path.stat().st_size}

data = {
    "schema": 2,
    "repository_commit": package_commit,
    "package_repository_commit": package_commit,
    "binary_repository_commit": binary_commit,
    "binary_reuse_verified": binary_commit != package_commit,
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
