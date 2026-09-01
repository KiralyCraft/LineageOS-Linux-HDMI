#!/usr/bin/env python3
import hashlib
import json
import pathlib
import sys
import tarfile
import zipfile

zip_path, tar_path, info_path, profile_path = map(pathlib.Path, sys.argv[1:5])
expected_commit = sys.argv[5]
required = {
    "module.prop", "customize.sh", "post-fs-data.sh", "service.sh",
    "uninstall.sh", "skip_mount", "sepolicy.rule", "profile.env",
    "original-checksums.list", "patched-checksums.list", "build-info.json",
    "bin/hdmi-losd", "apk/HdmiLosTile.apk",
    "vendor/bin/hw/vendor.qti.hardware.display.composer-service",
    "vendor/lib64/libsdmcore.so", "vendor/lib64/libsdmdal.so",
}

with zipfile.ZipFile(zip_path) as archive:
    names = archive.namelist()
    assert len(names) == len(set(names)), "duplicate ZIP paths"
    for name in names:
        path = pathlib.PurePosixPath(name)
        assert not path.is_absolute() and ".." not in path.parts, f"unsafe ZIP path: {name}"
        mode = archive.getinfo(name).external_attr >> 16
        assert not (mode & 0o170000) == 0o120000, f"symlink in ZIP: {name}"
    assert required <= set(names), sorted(required - set(names))
    embedded = json.loads(archive.read("build-info.json"))

with tarfile.open(tar_path, "r:gz") as archive:
    names = archive.getnames()
    for member in archive.getmembers():
        path = pathlib.PurePosixPath(member.name)
        assert not path.is_absolute() and ".." not in path.parts, f"unsafe tar path: {member.name}"
        assert not member.issym() and not member.islnk(), f"link in tar: {member.name}"
    assert any(name.endswith("bin/hdmi-los-agent") for name in names)
    assert any(name.endswith("run-agent.sh") for name in names)

info = json.loads(info_path.read_text())
profile = json.loads(profile_path.read_text())
release = json.loads((profile_path.parent.parent / "release.json").read_text())
assert info == embedded, "returned and embedded build-info differ"
assert info["repository_commit"] == expected_commit, "artifact is not from current commit"
assert info["profile"] == profile["name"]
assert info["release"] == release
assert info["source_revisions"] == profile["source"]
exact = info["exact_source_sync"]
assert len(exact["projects"]) >= 1138, "unexpected exact source project count"
assert all(len(revision) == 40 for revision in exact["projects"].values())
assert "prebuilts/module_sdk/art" in exact["projects"]
assert "vendor/lineage" in exact["projects"]
for project in profile["source"]["proprietary_projects"]:
    assert exact["projects"][project["path"]] == project["revision"]
qcom_path = profile["source"]["qcom_display_path"]
assert exact["projects"][qcom_path] == profile["source"]["qcom_display_revision"]
print("package structure and provenance: PASS")
