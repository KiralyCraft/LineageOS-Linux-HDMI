#!/usr/bin/env python3
"""Add the session utilization-clamp agent and launcher to a verified bundle."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def git_output(repository: Path, *arguments: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(repository), *arguments])


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--base", type=Path, required=True)
parser.add_argument("--repository", type=Path, required=True)
parser.add_argument("--agent", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()

base = args.base.resolve()
repository = args.repository.resolve()
agent = args.agent.resolve()
output = args.output.resolve()

if output.exists():
    raise SystemExit(f"output already exists: {output}")
subprocess.run(
    ["sha256sum", "--strict", "-c", "SHA256SUMS"],
    cwd=base,
    check=True,
    stdout=subprocess.DEVNULL,
)

head = git_output(repository, "rev-parse", "HEAD").decode().strip()
launcher = (base / "run-agent.sh").read_text()
report_md = git_output(
    repository,
    "show",
    f"{head}:docs/experiments/FULLSCREEN-PACING-UCLAMP-20261008.md",
)
report_json = git_output(
    repository,
    "show",
    f"{head}:docs/experiments/FULLSCREEN-PACING-UCLAMP-20261008.json",
)
if "SESSION_UCLAMP_MIN" in launcher or "--session-uclamp-min" in launcher:
    raise SystemExit("base launcher already contains session utilization-clamp controls")

replacements = [
    (
        "MESA_QUEUE=low-latency\n",
        "MESA_QUEUE=low-latency\n"
        "SESSION_UCLAMP_MIN=${HDMI_LOS_SESSION_UCLAMP_MIN:-512}\n",
    ),
    (
        "[--session lxde|none]",
        "[--session lxde|none] [--session-uclamp-min 0..1024]",
    ),
    (
        "        --no-timeout)\n",
        "        --session-uclamp-min)\n"
        "            (($# >= 2)) || { usage; exit 2; }\n"
        "            SESSION_UCLAMP_MIN=$2\n"
        "            shift 2\n"
        "            ;;\n"
        "        --no-timeout)\n",
    ),
    (
        "[[ $MESA_QUEUE == low-latency || $MESA_QUEUE == fifo ]] || { usage; exit 2; }\n",
        "[[ $MESA_QUEUE == low-latency || $MESA_QUEUE == fifo ]] || { usage; exit 2; }\n"
        "[[ $SESSION_UCLAMP_MIN =~ ^[0-9]+$ ]] && ((SESSION_UCLAMP_MIN <= 1024)) || {\n"
        "    printf 'Session utilization clamp must be an integer from 0 through 1024\\n' >&2\n"
        "    exit 2\n"
        "}\n",
    ),
    (
        "          --drm-trace \"$DRM_TRACE\" --timing-guard \"$TIMING_GUARD\" \\\n"
        "          --tearfree-completion \"$TEARFREE_COMPLETION\")\n",
        "          --drm-trace \"$DRM_TRACE\" --timing-guard \"$TIMING_GUARD\" \\\n"
        "          --tearfree-completion \"$TEARFREE_COMPLETION\" \\\n"
        "          --session-uclamp-min \"$SESSION_UCLAMP_MIN\")\n",
    ),
    (
        "printf 'HDMI presentation: release=%s queue=%s\\n' \"$PRESENT_RELEASE\" \"$MESA_QUEUE\" >&2\n",
        "printf 'HDMI presentation: release=%s queue=%s\\n' \"$PRESENT_RELEASE\" \"$MESA_QUEUE\" >&2\n"
        "printf 'HDMI session scheduling: utilization_min=%s/1024\\n' \"$SESSION_UCLAMP_MIN\" >&2\n",
    ),
]
for before, after in replacements:
    if launcher.count(before) != 1:
        raise SystemExit(f"launcher transform anchor count is {launcher.count(before)}: {before!r}")
    launcher = launcher.replace(before, after)

agent_arguments = (
    "            --drm-trace \"$DRM_TRACE\" --timing-guard \"$TIMING_GUARD\" \\\n"
    "            --tearfree-completion \"$TEARFREE_COMPLETION\")\n"
)
if launcher.count(agent_arguments) != 1:
    raise SystemExit("agent argument transform anchor is missing")
launcher = launcher.replace(
    agent_arguments,
    "            --drm-trace \"$DRM_TRACE\" --timing-guard \"$TIMING_GUARD\" \\\n"
    "            --tearfree-completion \"$TEARFREE_COMPLETION\" \\\n"
    "            --session-uclamp-min \"$SESSION_UCLAMP_MIN\")\n",
)
launcher = launcher.encode()
if not agent.is_file():
    raise SystemExit(f"agent does not exist: {agent}")

elf = subprocess.check_output(["readelf", "-h", str(agent)], text=True)
if "AArch64" not in elf or "DYN (Position-Independent Executable file)" not in elf:
    raise SystemExit("agent is not an AArch64 position-independent executable")
if b"session utilization clamp" not in agent.read_bytes():
    raise SystemExit("agent lacks the session utilization-clamp diagnostic")

shutil.copytree(base, output, symlinks=True)
shutil.copy2(agent, output / "bin/hdmi-los-agent")
(output / "run-agent.sh").write_bytes(launcher)
(output / "run-agent.sh").chmod(0o755)
(output / "FULLSCREEN-PACING-UCLAMP-20261008.md").write_bytes(report_md)
(output / "source/session-uclamp-run-agent.sh").write_bytes(launcher)
(output / "validation/FULLSCREEN-PACING-UCLAMP-20261008.json").write_bytes(
    report_json
)

source_tree = output / "source/hdmi"
if source_tree.exists():
    shutil.rmtree(source_tree)
source_tree.mkdir(parents=True)
with subprocess.Popen(
    ["git", "-C", str(repository), "archive", head], stdout=subprocess.PIPE
) as archive:
    subprocess.run(
        ["tar", "xf", "-", "-C", str(source_tree)],
        stdin=archive.stdout,
        check=True,
    )
    if archive.wait() != 0:
        raise SystemExit("git archive failed")
with (output / "source/hdmi-source.tar.gz").open("wb") as archive_file:
    subprocess.run(
        ["git", "-C", str(repository), "archive", "--format=tar.gz", head],
        stdout=archive_file,
        check=True,
    )

for relative in [
    "android",
    "companion",
    "lib",
    "libexec",
    "bin/glxgears",
    "bin/hdmi-capture-keeper",
    "bin/hdmi-input-bridge",
]:
    original = base / relative
    packaged = output / relative
    files = [original] if original.is_file() else [p for p in original.rglob("*") if p.is_file()]
    for original_file in files:
        packaged_file = packaged if original.is_file() else packaged / original_file.relative_to(original)
        if sha256(original_file) != sha256(packaged_file):
            raise SystemExit(f"preserved artifact changed: {original_file}")

subprocess.run(["bash", "-n", str(output / "run-agent.sh")], check=True)
agent_command = [str(output / "bin/hdmi-los-agent"), "--session-uclamp-min", "1025"]
if os.geteuid() != 0:
    agent_command[:0] = ["sudo", "-n"]
invalid = subprocess.run(
    agent_command,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
)
if invalid.returncode != 2 or "invalid session utilization clamp" not in invalid.stderr:
    raise SystemExit("agent command-line validation failed")

agent_hash = sha256(output / "bin/hdmi-los-agent")
validation = {
    "source_commit": head,
    "build_host": "root@192.168.104.201",
    "agent": {
        "sha256": agent_hash,
        "size": (output / "bin/hdmi-los-agent").stat().st_size,
        "architecture": "AArch64 PIE",
        "invalid_value_gate": "PASS",
    },
    "launcher_syntax": "PASS",
    "preserved": [
        "Mesa",
        "Xorg",
        "kernel companion",
        "Android broker",
        "USB and Bluetooth input bridge",
        "glxgears",
    ],
}
(output / "validation/session-uclamp-build.json").write_text(
    json.dumps(validation, indent=2) + "\n"
)

build_info_path = output / "build-info.json"
build_info = json.loads(build_info_path.read_text())
build_info["source_commit"] = head
if not build_info["candidate"].endswith("-session-uclamp"):
    build_info["candidate"] += "-session-uclamp"
build_info["components"]["native"]["source_commit"] = head
build_info["components"]["native"]["session_uclamp"] = {
    "source_commit": head,
    "build_host": "root@192.168.104.201",
    "build": "validation/session-uclamp-build.json",
}
build_info["artifacts"]["bin/hdmi-los-agent"] = {
    "sha256": agent_hash,
    "size": (output / "bin/hdmi-los-agent").stat().st_size,
}
build_info["session_scheduling"] = {
    "default_uclamp_min": 512,
    "control": "--session-uclamp-min 0..1024",
    "environment": "HDMI_LOS_SESSION_UCLAMP_MIN",
    "scope": ["Xorg process tree", "LXDE process tree"],
    "inheritance_probe": "PASS on installed kernel",
    "physical_4k30_control": "smooth with LXPanel paused",
    "packaged_physical_test": "pending",
    "report": "FULLSCREEN-PACING-UCLAMP-20261008.md",
}
build_info["comparison"]["base_bundle"] = str(base)
build_info["comparison"]["mesa_xorg_kernel_input_unchanged"] = True
build_info_path.write_text(json.dumps(build_info, indent=2) + "\n")

readme = output / "README.md"
readme.write_text(
    "Session scheduling update, 2026-10-08\n\n"
    "The launcher defaults to a session-scoped utilization minimum of 512/1024 "
    "for Xorg, LXDE and their descendants. Disable it for comparison with "
    "--session-uclamp-min 0. Mesa, Xorg, the kernel companion and input bridge "
    "are byte-identical to the verified base bundle. See "
    "FULLSCREEN-PACING-UCLAMP-20261008.md for the measurements and limits.\n\n"
    + readme.read_text()
)

checksums = output / "SHA256SUMS"
files = sorted(path for path in output.rglob("*") if path.is_file() and path != checksums)
checksums.write_text(
    "".join(f"{sha256(path)}  {path.relative_to(output)}\n" for path in files)
)
subprocess.run(
    ["sha256sum", "--strict", "-c", "SHA256SUMS"],
    cwd=output,
    check=True,
    stdout=subprocess.DEVNULL,
)

print(
    json.dumps(
        {
            "output": str(output),
            "source_commit": head,
            "agent_sha256": agent_hash,
            "files": len(files),
        },
        indent=2,
    )
)
