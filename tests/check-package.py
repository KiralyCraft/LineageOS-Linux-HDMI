#!/usr/bin/env python3
import hashlib
import json
import pathlib
import sys
import tarfile
import zipfile

zip_path, tar_path, info_path, profile_path = map(pathlib.Path, sys.argv[1:5])
expected_commit = sys.argv[5]
expected_composer_commit = sys.argv[6] if len(sys.argv) > 6 else None
required = {
    "module.prop", "customize.sh", "post-fs-data.sh", "service.sh",
    "uninstall.sh", "mount-utils.sh", "skip_mount", "sepolicy.rule", "profile.env",
    "original-checksums.list", "patched-checksums.list", "build-info.json",
    "diagnostic-only",
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
    post_fs_data = archive.read("post-fs-data.sh").decode()
    assert 'mount -o bind "$bind_source" "$target"' in post_fs_data
    assert 'mount -o remount,bind,ro "$bind_source" "$target"' in post_fs_data
    assert 'mount -o bind "$source" "$target"' not in post_fs_data
    assert ".magisk/modules" in post_fs_data
    assert "vendor.display.disable_hw_recovery_dump 0" in post_fs_data

    zip_artifacts = {
        "vendor.qti.hardware.display.composer-service":
            "vendor/bin/hw/vendor.qti.hardware.display.composer-service",
        "libsdmcore.so": "vendor/lib64/libsdmcore.so",
        "libsdmdal.so": "vendor/lib64/libsdmdal.so",
        "hdmi-losd": "bin/hdmi-losd",
        "HdmiLosTile.apk": "apk/HdmiLosTile.apk",
    }
    for artifact, member in zip_artifacts.items():
        payload = archive.read(member)
        expected = embedded["artifacts"][artifact]
        assert hashlib.sha256(payload).hexdigest() == expected["sha256"]
        assert len(payload) == expected["size"]

with tarfile.open(tar_path, "r:gz") as archive:
    names = archive.getnames()
    for member in archive.getmembers():
        path = pathlib.PurePosixPath(member.name)
        assert not path.is_absolute() and ".." not in path.parts, f"unsafe tar path: {member.name}"
        assert not member.issym() and not member.islnk(), f"link in tar: {member.name}"
    assert any(name.endswith("bin/hdmi-los-agent") for name in names)
    assert any(name.endswith("run-agent.sh") for name in names)
    tar_artifacts = {
        "hdmi-los-agent": "bin/hdmi-los-agent",
        "hdmi-input-bridge": "bin/hdmi-input-bridge",
        "hdmi-capture-keeper": "bin/hdmi-capture-keeper",
        "libhdmi-los-drmtrace.so": "lib/libhdmi-los-drmtrace.so",
    }
    for artifact, suffix in tar_artifacts.items():
        member = next(item for item in archive.getmembers() if item.name.endswith(suffix))
        payload = archive.extractfile(member).read()
        expected = embedded["artifacts"][artifact]
        assert hashlib.sha256(payload).hexdigest() == expected["sha256"]
        assert len(payload) == expected["size"]

info = json.loads(info_path.read_text())
profile = json.loads(profile_path.read_text())
release = json.loads((profile_path.parent.parent / "release.json").read_text())
assert info == embedded, "returned and embedded build-info differ"
assert info["schema"] == 3
assert info["repository_commit"] == expected_commit, "artifact is not from current commit"
assert info["package_repository_commit"] == expected_commit
assert info["build_mode"] in {"full", "repackage", "reuse-composer"}
for group in ("composer", "native", "tile"):
    assert len(info[f"{group}_repository_commit"]) == 40
assert info["binary_reuse_verified"] == any(
    info[f"{group}_repository_commit"] != info["package_repository_commit"]
    for group in ("composer", "native", "tile")
)
if expected_composer_commit:
    assert info["composer_repository_commit"] == expected_composer_commit
group_for_artifact = {
    "vendor.qti.hardware.display.composer-service": "composer",
    "libsdmcore.so": "composer",
    "libsdmdal.so": "composer",
    "HdmiLosTile.apk": "tile",
    "hdmi-losd": "native",
    "hdmi-los-agent": "native",
    "hdmi-input-bridge": "native",
    "hdmi-capture-keeper": "native",
    "libhdmi-los-drmtrace.so": "native",
}
for artifact, group in group_for_artifact.items():
    assert info["artifacts"][artifact]["repository_commit"] == \
        info[f"{group}_repository_commit"]
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
