#!/usr/bin/env python3
import json
import pathlib
import sys

profile = json.loads(pathlib.Path(sys.argv[1]).read_text())
module = pathlib.Path(sys.argv[2])
device = profile["device"]

def quote(value):
    return "'" + str(value).replace("'", "'\\''") + "'"

env = {
    "PROFILE_NAME": profile["name"],
    "EXPECTED_DEVICE": device["ro.product.device"],
    "EXPECTED_LINEAGE": device["ro.lineage.version"],
    "EXPECTED_BUILD_ID": device["ro.build.id"],
    "EXPECTED_SDK": device["ro.build.version.sdk"],
    "EXPECTED_SECURITY_PATCH": device["ro.build.version.security_patch"],
}
(module / "profile.env").write_text("".join(f"{key}={quote(value)}\n" for key, value in env.items()))

lines = ["# sha256|mode|uid|gid|selinux_context|absolute_target|module_relative_source\n"]
for artifact in profile["artifacts"]:
    path = artifact["path"]
    relative = path.lstrip("/")
    lines.append("|".join([
        artifact["sha256"], artifact["mode"], str(artifact["uid"]), str(artifact["gid"]),
        artifact["selinux"], path, relative,
    ]) + "\n")
(module / "original-checksums.list").write_text("".join(lines))

prop = (module / "module.prop").read_text().splitlines()
version_code = int("".join(filter(str.isdigit, profile["captured_utc"][:10])))
prop = [f"version=0.1-{profile['name']}" if line.startswith("version=")
        else f"versionCode={version_code}" if line.startswith("versionCode=")
        else line for line in prop]
(module / "module.prop").write_text("\n".join(prop) + "\n")
