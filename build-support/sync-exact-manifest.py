#!/usr/bin/env python3
"""Materialize a pinned Android manifest without fetching branch history.

The repo tool may fall back to a branch fetch when an old manifest revision is
not the current branch tip.  For large kernel repositories that turns a small,
shallow build input into millions of unrelated objects.  This helper accepts
only immutable 40-character revisions and fetches each revision explicitly.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import threading
from typing import NoReturn
import xml.etree.ElementTree as ET


SHA1 = re.compile(r"[0-9a-f]{40}")
print_lock = threading.Lock()

# These repositories carry complete toolchains for several build hosts.  This
# project builds only on Linux/glibc x86-64; keeping the other host trees in the
# working copy wastes many gigabytes without changing the pinned Git object.
SPARSE_PATHS = {
    "prebuilts/rust": ("bootstrap", "linux-x86", "soong", "tests"),
}


def fail(message: str) -> NoReturn:
    raise SystemExit(message)


def checked_relative(value: str, label: str) -> pathlib.PurePosixPath:
    path = pathlib.PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        fail(f"unsafe {label}: {value!r}")
    return path


def git(
    target: pathlib.Path, *args: str, quiet: bool = False, capture: bool = False
) -> subprocess.CompletedProcess[str]:
    kwargs: dict[str, object] = {
        "check": True,
        "env": {**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        "text": True,
    }
    if quiet:
        kwargs.update(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif capture:
        kwargs.update(stdout=subprocess.PIPE)
    return subprocess.run(["git", "-C", str(target), *args], **kwargs)


def has_commit(target: pathlib.Path, revision: str) -> bool:
    return subprocess.run(
        ["git", "-C", str(target), "cat-file", "-e", f"{revision}^{{commit}}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0


def sync_project(item: dict[str, str]) -> tuple[str, str]:
    target = pathlib.Path(item["target"])
    target.parent.mkdir(parents=True, exist_ok=True)
    legacy_gitdir = pathlib.Path(item["legacy_gitdir"])
    if not (target / ".git").exists() and legacy_gitdir.is_dir():
        target.mkdir(parents=True, exist_ok=True)
        (target / ".git").write_text(
            f"gitdir: {os.path.relpath(legacy_gitdir, target)}\n"
        )
    if subprocess.run(
        ["git", "-C", str(target), "rev-parse", "--git-dir"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode != 0:
        target.mkdir(parents=True, exist_ok=True)
        git(target, "init", "-q")

    revision = item["revision"]
    if not has_commit(target, revision):
        with print_lock:
            print(f"fetch {item['path']} @ {revision[:12]}", flush=True)
        # An explicit object ID is the safety property here.  There is
        # deliberately no branch fallback: a server that rejects the pinned
        # object makes the build fail closed.
        git(
            target,
            "fetch",
            "--depth=1",
            "--no-tags",
            "--no-recurse-submodules",
            "--force",
            item["url"],
            f"{revision}:refs/hdmi-los/exact",
        )
    if not has_commit(target, revision):
        raise RuntimeError(f"fetch did not materialize {item['path']} at {revision}")

    sparse_paths = SPARSE_PATHS.get(item["path"])
    if sparse_paths:
        git(target, "sparse-checkout", "init", "--cone")
        git(target, "sparse-checkout", "set", "--cone", *sparse_paths)

    git(target, "checkout", "-q", "--detach", "--force", revision)
    git(target, "reset", "-q", "--hard", revision)
    git(target, "clean", "-q", "-fdx")
    head = git(target, "rev-parse", "HEAD", capture=True).stdout.strip()
    if head != revision:
        raise RuntimeError(f"wrong checkout for {item['path']}: {head}")
    return item["path"], revision


def replace_link(destination: pathlib.Path, source: pathlib.Path) -> None:
    if destination.is_symlink() or destination.is_file():
        destination.unlink()
    elif destination.exists():
        raise RuntimeError(f"refusing to replace directory link destination: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.symlink_to(os.path.relpath(source, destination.parent))


def replace_copy(destination: pathlib.Path, source: pathlib.Path) -> None:
    if destination.is_symlink():
        destination.unlink()
    elif destination.exists() and not destination.is_file():
        raise RuntimeError(f"refusing to replace non-file copy destination: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def main() -> None:
    if len(sys.argv) not in (3, 4):
        fail(f"usage: {sys.argv[0]} FILTERED_MANIFEST TREE [JOBS]")
    manifest = pathlib.Path(sys.argv[1]).resolve()
    tree = pathlib.Path(sys.argv[2]).resolve()
    jobs = int(sys.argv[3]) if len(sys.argv) == 4 else 8
    if jobs < 1 or jobs > 32:
        fail("JOBS must be between 1 and 32")

    root = ET.parse(manifest).getroot()
    default = root.find("default")
    default_remote = default.get("remote") if default is not None else None
    remotes = {node.get("name"): node.get("fetch") for node in root.findall("remote")}
    if not default_remote:
        fail("manifest has no default remote")

    projects: list[dict[str, str]] = []
    project_nodes: list[tuple[ET.Element, pathlib.PurePosixPath]] = []
    for node in root.findall("project"):
        name = node.get("name", "")
        path = checked_relative(node.get("path", name), "project path")
        revision = node.get("revision", "")
        if not SHA1.fullmatch(revision):
            fail(f"project {path} is not pinned to an exact SHA: {revision!r}")
        remote_name = node.get("remote", default_remote)
        fetch = remotes.get(remote_name)
        if not fetch:
            fail(f"project {path} has unknown remote {remote_name!r}")
        url = f"{fetch.rstrip('/')}/{name.lstrip('/')}"
        projects.append(
            {
                "path": path.as_posix(),
                "target": str(tree.joinpath(*path.parts)),
                "legacy_gitdir": str(
                    tree.joinpath(".repo", "projects", *path.parts).with_suffix(
                        path.suffix + ".git"
                    )
                ),
                "revision": revision,
                "url": url,
            }
        )
        project_nodes.append((node, path))

    tree.mkdir(parents=True, exist_ok=True)
    completed: dict[str, str] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as executor:
        futures = {executor.submit(sync_project, item): item for item in projects}
        for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
            item = futures[future]
            try:
                path, revision = future.result()
            except Exception as error:
                for pending in futures:
                    pending.cancel()
                raise RuntimeError(f"exact sync failed for {item['path']}: {error}") from error
            completed[path] = revision
            with print_lock:
                print(f"[{index}/{len(projects)}] {path}", flush=True)

    # Reproduce the root links/copies that repo normally creates.
    for node, project_path in project_nodes:
        project_root = tree.joinpath(*project_path.parts)
        for child in node:
            if child.tag not in ("linkfile", "copyfile"):
                continue
            source_rel = checked_relative(child.get("src", ""), f"{child.tag} source")
            dest_rel = checked_relative(child.get("dest", ""), f"{child.tag} destination")
            source = project_root.joinpath(*source_rel.parts)
            destination = tree.joinpath(*dest_rel.parts)
            if not source.exists():
                raise RuntimeError(f"missing {child.tag} source: {source}")
            if child.tag == "linkfile":
                replace_link(destination, source)
            else:
                replace_copy(destination, source)

    record = {
        "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "projects": dict(sorted(completed.items())),
    }
    (tree / ".hdmi-los-exact-manifest.json").write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n"
    )
    print(f"exact manifest sync complete: {len(projects)} pinned projects")


if __name__ == "__main__":
    main()
