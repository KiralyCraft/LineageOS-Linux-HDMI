#!/usr/bin/env python3
"""Separate KGSL native-fence delivery from event-worker runnable delay."""

import argparse
import collections
import json
import math
import re
from pathlib import Path


def distribution(values):
    values = sorted(values)
    if not values:
        return {}
    return {
        "count": len(values),
        "p50_ms": values[len(values) // 2],
        "p95_ms": values[math.ceil(len(values) * 0.95) - 1],
        "p99_ms": values[math.ceil(len(values) * 0.99) - 1],
        "max_ms": values[-1],
    }


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("trace", type=Path)
parser.add_argument("metadata", type=Path)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
assert args.output.resolve().is_relative_to(Path("/tmp")), "Keep raw analysis in RAM"
metadata = json.loads(args.metadata.read_text())
overruns = {
    cpu: int(re.search(r"overrun: (\d+)", stats)[1])
    for cpu, stats in metadata["buffer_stats"].items()
}
retired = {}
registered = {}
notifications = []
wakeup = None
wake_delays = []
worker_cpus = collections.Counter()
for line in args.trace.open():
    match = re.search(
        r" (\d+\.\d+): (adreno_cmdbatch_retired|kgsl_register_event|kgsl_fire_event): "
        r"ctx=(\d+).*?ts=(\d+)",
        line,
    )
    if match:
        timestamp = float(match[1])
        key = (int(match[3]), int(match[4]))
        if match[2] == "adreno_cmdbatch_retired":
            retired[key] = timestamp
        elif "kgsl_sync_fence_event_cb" in line:
            if match[2] == "kgsl_register_event":
                registered[key] = timestamp
            elif "type=retired" in line and key in retired and key in registered:
                # A dependency registered after retirement must not attribute
                # the earlier idle interval to completion-notification latency.
                readiness = max(retired[key], registered[key])
                notifications.append(
                    {
                        "ctx": key[0],
                        "ts": key[1],
                        "retirement": retired[key],
                        "registration": registered.pop(key),
                        "callback": timestamp,
                        "notification_ms": (timestamp - readiness) * 1000,
                    }
                )
    match = re.search(r" (\d+\.\d+): sched_wakeup: comm=kgsl-events ", line)
    if match:
        wakeup = float(match[1])
    match = re.search(
        r"\[(\d+)\].*? (\d+\.\d+): sched_switch: .*?next_comm=kgsl-events ", line
    )
    if match:
        worker_cpus[int(match[1])] += 1
        if wakeup is not None:
            wake_delays.append((float(match[2]) - wakeup) * 1000)
            wakeup = None

result = {
    "trace": str(args.trace),
    "overruns": overruns,
    "complete_trace": not any(overruns.values()),
    "native_fence_notification": distribution(
        row["notification_ms"] for row in notifications
    ),
    "worker_wake_to_run": distribution(wake_delays),
    "worker_cpus": dict(worker_cpus),
    "longest_notifications": sorted(
        notifications, key=lambda row: row["notification_ms"], reverse=True
    )[:20],
    "physical_scanout_tested": False,
}
args.output.write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps({key: value for key, value in result.items() if key != "longest_notifications"}))
