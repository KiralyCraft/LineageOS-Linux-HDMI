import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess


parser = argparse.ArgumentParser()
parser.add_argument("name")
mode = parser.add_mutually_exclusive_group(required=True)
mode.add_argument("--maximize", action="store_true")
mode.add_argument("--fullscreen", action="store_true")
parser.add_argument("--hide-cursor", action="store_true")
parser.add_argument("--seconds", type=int, default=25)
parser.add_argument("--binary", required=True)
parser.add_argument("--buffer-kb", type=int, default=2048)
args = parser.parse_args()

root = Path("/proc/1/root/sys/kernel/tracing")
instance = root / "instances" / args.name
output_root = Path("/tmp/hdmi-app-pacing-20261008")
trace_output = output_root / f"kernel-{args.name}-trace.txt"
metadata_output = output_root / f"kernel-{args.name}-trace.json"
runner = Path(__file__).with_name("hdmi-run-gears.py")

assert os.geteuid() == 0
assert args.seconds > 0
assert not instance.exists()
instance.mkdir()
record = {
    "instance": str(instance),
    "events": [],
    "global_tracing_changed": False,
    "workload": args.name,
    "seconds": args.seconds,
    "mode": "fullscreen" if args.fullscreen else "maximized",
}
try:
    (instance / "tracing_on").write_text("0")
    (instance / "buffer_size_kb").write_text(str(args.buffer_kb))
    (instance / "trace_clock").write_text("mono")
    definitions = {
        "sched/sched_switch": (
            'prev_comm ~ "Xorg*" || next_comm ~ "Xorg*" || '
            'prev_comm ~ "glxgears*" || next_comm ~ "glxgears*" || '
            'prev_comm ~ "lxpanel*" || next_comm ~ "lxpanel*" || '
            'prev_comm == "kgsl-events" || next_comm == "kgsl-events"'
        ),
        "sched/sched_wakeup": (
            'comm ~ "Xorg*" || comm ~ "glxgears*" || comm ~ "lxpanel*" || '
            'comm == "kgsl-events"'
        ),
        "drm/drm_vblank_event": "",
        "drm/drm_vblank_event_delivered": "",
        "kgsl/kgsl_waittimestamp_entry": "",
        "kgsl/kgsl_waittimestamp_exit": "",
        "kgsl/kgsl_fire_event": "",
        "kgsl/kgsl_register_event": "",
        "kgsl/adreno_cmdbatch_submitted": "",
        "kgsl/adreno_cmdbatch_retired": "",
        "kgsl/kgsl_pwrlevel": "",
    }
    for event, event_filter in definitions.items():
        directory = instance / "events" / event
        if not directory.exists():
            continue
        if event_filter:
            (directory / "filter").write_text(event_filter)
        (directory / "enable").write_text("1")
        record["events"].append(event)

    (instance / "tracing_on").write_text("1")
    (instance / "trace_marker").write_text("HDMI_PACING_START")
    command = [
        "python3",
        str(runner),
        args.name,
        "--binary",
        args.binary,
        "--seconds",
        str(args.seconds),
        "--fullscreen" if args.fullscreen else "--maximize",
    ]
    if args.hide_cursor:
        command.append("--hide-cursor")
    run = subprocess.run(command, timeout=args.seconds + 30)
    record["child_result"] = run.returncode
    (instance / "trace_marker").write_text("HDMI_PACING_STOP")
    (instance / "tracing_on").write_text("0")
    record["buffer_stats"] = {
        path.parent.name: path.read_text()
        for path in (instance / "per_cpu").glob("cpu*/stats")
    }
    with (instance / "trace").open("rb") as source, trace_output.open("wb") as target:
        shutil.copyfileobj(source, target)
    metadata_output.write_text(json.dumps(record, indent=2) + "\n")
    print(
        json.dumps(
            {
                "child": run.returncode,
                "trace_bytes": trace_output.stat().st_size,
                "instance": args.name,
                "events": record["events"],
            }
        ),
        flush=True,
    )
finally:
    (instance / "tracing_on").write_text("0")
    (instance / "events/enable").write_text("0")
    instance.rmdir()
