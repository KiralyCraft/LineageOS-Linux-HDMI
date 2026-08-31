#!/usr/bin/env python3
"""Combine a captured Lineage manifest with pinned proprietary projects."""

from __future__ import annotations

import json
import pathlib
import re
import sys
import xml.etree.ElementTree as ET


SHA1 = re.compile(r"[0-9a-f]{40}")


def fail(message: str) -> None:
    raise SystemExit(message)


if len(sys.argv) != 4:
    fail(f"usage: {sys.argv[0]} CAPTURED_MANIFEST PROFILE_JSON OUTPUT")

manifest_path = pathlib.Path(sys.argv[1])
profile_path = pathlib.Path(sys.argv[2])
output_path = pathlib.Path(sys.argv[3])
root = ET.parse(manifest_path).getroot()
profile = json.loads(profile_path.read_text())

projects = root.findall("project")
paths = {project.get("path", project.get("name", "")) for project in projects}
if len(paths) != len(projects):
    fail("captured manifest contains duplicate project paths")
for project in projects:
    revision = project.get("revision", "")
    if not SHA1.fullmatch(revision):
        fail(f"captured project is not pinned: {project.get('path', project.get('name'))}")

source = profile.get("source", {})
display_path = source.get("qcom_display_path")
display_revision = source.get("qcom_display_revision")
display_project = next(
    (project for project in projects if project.get("path", project.get("name")) == display_path),
    None,
)
if display_project is None or display_project.get("revision") != display_revision:
    fail("profile display revision does not match the captured manifest")

proprietary = source.get("proprietary_projects")
if not isinstance(proprietary, list) or not proprietary:
    fail("profile has no pinned proprietary_projects")

remote_name = "hdmi-los-proprietary"
if root.find(f"remote[@name='{remote_name}']") is None:
    root.insert(0, ET.Element("remote", name=remote_name, fetch="https://github.com"))

for item in proprietary:
    name = item.get("name", "")
    path = item.get("path", "")
    revision = item.get("revision", "")
    pure_path = pathlib.PurePosixPath(path)
    if (
        not name
        or pure_path.is_absolute()
        or not pure_path.parts
        or ".." in pure_path.parts
        or not SHA1.fullmatch(revision)
    ):
        fail(f"invalid proprietary project entry: {item!r}")
    if path in paths:
        fail(f"proprietary project path collides with captured manifest: {path}")
    ET.SubElement(
        root,
        "project",
        name=name,
        path=path,
        remote=remote_name,
        revision=revision,
        groups="hdmi-los-proprietary",
    )
    paths.add(path)

ET.indent(root, space="  ")
output_path.parent.mkdir(parents=True, exist_ok=True)
ET.ElementTree(root).write(output_path, encoding="utf-8", xml_declaration=True)
print(f"prepared {len(paths)} exact projects, including {len(proprietary)} proprietary projects")
