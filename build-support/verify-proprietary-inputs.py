#!/usr/bin/env python3
"""Verify pinned proprietary files after materializing the exact source tree."""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys


if len(sys.argv) != 3:
    raise SystemExit(f"usage: {sys.argv[0]} TREE PROFILE_JSON")

tree = pathlib.Path(sys.argv[1]).resolve()
profile = json.loads(pathlib.Path(sys.argv[2]).read_text())
checks = profile.get("source", {}).get("proprietary_files")
if not isinstance(checks, list) or not checks:
    raise SystemExit("profile has no proprietary file checks")

for item in checks:
    relative = pathlib.PurePosixPath(item.get("path", ""))
    expected = item.get("sha256", "")
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise SystemExit(f"unsafe proprietary file path: {relative}")
    target = tree.joinpath(*relative.parts)
    if not target.is_file():
        raise SystemExit(f"missing proprietary build input: {relative}")
    actual = hashlib.sha256(target.read_bytes()).hexdigest()
    if actual != expected:
        raise SystemExit(
            f"proprietary input mismatch for {relative}: expected {expected}, got {actual}"
        )
    print(f"verified proprietary input {relative}: {actual}")
