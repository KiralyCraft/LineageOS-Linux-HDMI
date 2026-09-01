#!/usr/bin/env python3
import hashlib
import json
import pathlib
import sys

info_path = pathlib.Path(sys.argv[1])
build = pathlib.Path(sys.argv[2])
profile_path = pathlib.Path(sys.argv[3])
expected_commit = sys.argv[4]
info = json.loads(info_path.read_text())
profile = json.loads(profile_path.read_text())

assert info["profile"] == profile["name"], "base profile name mismatch"
assert info["source_revisions"] == profile["source"], "base profile source mismatch"

artifacts = {
    "vendor.qti.hardware.display.composer-service":
        build / "composer/vendor.qti.hardware.display.composer-service",
    "libsdmcore.so": build / "composer/libsdmcore.so",
    "libsdmdal.so": build / "composer/libsdmdal.so",
}
for name, path in artifacts.items():
    expected = info["artifacts"][name]
    recorded_commit = expected.get(
        "repository_commit",
        info.get("binary_repository_commit", info["repository_commit"]),
    )
    assert recorded_commit == expected_commit, (
        f"composer base commit mismatch for {name}: expected {expected_commit}, "
        f"found {recorded_commit}"
    )
    assert path.is_file(), f"missing reusable composer artifact: {path}"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert digest == expected["sha256"], f"composer hash mismatch: {name}"
    assert path.stat().st_size == expected["size"], f"composer size mismatch: {name}"

print(f"reusable composer build verified at {expected_commit}")
