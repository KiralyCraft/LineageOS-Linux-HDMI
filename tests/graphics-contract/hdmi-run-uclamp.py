#!/usr/bin/env python3
"""Run an owned HDMI workload with inherited per-task utilization clamps."""

import argparse
import ctypes
import json
import os
import platform
from pathlib import Path


class SchedAttr(ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_uint32),
        ("policy", ctypes.c_uint32),
        ("flags", ctypes.c_uint64),
        ("nice", ctypes.c_int32),
        ("priority", ctypes.c_uint32),
        ("runtime", ctypes.c_uint64),
        ("deadline", ctypes.c_uint64),
        ("period", ctypes.c_uint64),
        ("util_min", ctypes.c_uint32),
        ("util_max", ctypes.c_uint32),
    ]


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--min", type=int, required=True)
parser.add_argument("command", nargs=argparse.REMAINDER)
args = parser.parse_args()
if not 0 <= args.min <= 1024:
    parser.error("--min must be between 0 and 1024")
command = args.command
if command and command[0] == "--":
    command = command[1:]
if not command:
    parser.error("a workload command is required")
if platform.machine() != "aarch64" or os.geteuid() != 0:
    raise SystemExit("this live-kernel probe requires a root AArch64 process")

attr = SchedAttr()
attr.size = ctypes.sizeof(attr)
# KEEP_POLICY | KEEP_PARAMS | UTIL_CLAMP_MIN
attr.flags = 0x08 | 0x10 | 0x20
attr.util_min = args.min
libc = ctypes.CDLL(None, use_errno=True)
libc.syscall.restype = ctypes.c_long
if libc.syscall(274, 0, ctypes.byref(attr), 0) != 0:
    error = ctypes.get_errno()
    raise OSError(error, os.strerror(error))

print(
    json.dumps(
        {
            "workload_uclamp_min": args.min,
            "inherited_by_command": command,
            "effective": [
                line.strip()
                for line in Path("/proc/self/sched").read_text().splitlines()
                if "uclamp" in line
            ],
        }
    ),
    flush=True,
)
os.execvp(command[0], command)
