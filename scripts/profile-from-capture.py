#!/usr/bin/env python3
import datetime
import json
import pathlib
import sys
import xml.etree.ElementTree as ET

name = sys.argv[1]
props = dict(line.rstrip("\n").split("=", 1) for line in pathlib.Path(sys.argv[2]).read_text().splitlines())
artifact_lines = pathlib.Path(sys.argv[3]).read_text().splitlines()
manifest = ET.parse(sys.argv[4]).getroot()
output = pathlib.Path(sys.argv[5])

by_path = {p.get("path", p.get("name", "")): p for p in manifest.findall("project")}
source_paths = {
    "qcom_display_revision": "hardware/qcom-caf/sm8550/display",
    "frameworks_base_revision": "frameworks/base",
    "system_core_revision": "system/core",
    "device_pdx234_revision": "device/sony/pdx234",
    "device_sm8550_common_revision": "device/sony/sm8550-common",
    "kernel_revision": "kernel/sony/sm8550",
    "kernel_modules_revision": "kernel/sony/sm8550-modules",
    "kernel_devicetrees_revision": "kernel/sony/sm8550-devicetrees",
}
source = {"branch": "lineage-22.2", "qcom_display_path": source_paths["qcom_display_revision"]}
for key, path in source_paths.items():
    source[key] = by_path[path].get("revision")

artifacts = []
for line in artifact_lines:
    path, sha, build_id, mode, uid, gid, context = line.split("|", 6)
    artifacts.append({"path": path, "sha256": sha, "build_id": build_id,
                      "mode": mode.zfill(4), "uid": int(uid), "gid": int(gid),
                      "selinux": context})

data = {
    "schema": 1,
    "name": name,
    "captured_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "device": {
        "ro.product.device": props["ro.product.device"],
        "lineage_device": "pdx234",
        "ro.lineage.version": props["ro.lineage.version"],
        "ro.build.id": props["ro.build.id"],
        "ro.build.version.sdk": props["ro.build.version.sdk"],
        "ro.build.version.security_patch": props["ro.build.version.security_patch"],
    },
    "source": source,
    "artifacts": artifacts,
    "patchset": "qcom-display/v1",
    "timeout_seconds": 60,
    "composer_failsafe_seconds": 65,
}
output.write_text(json.dumps(data, indent=2) + "\n")

