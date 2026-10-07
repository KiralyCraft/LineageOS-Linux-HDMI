#!/usr/bin/env python3
"""Observe a user-driven browser workload without moving windows or reading pixels.

XSync measures server request turnaround, not physical presentation or browser
frame rate. Thread wait channels are sampled states, not measured wait durations.
GPU telemetry includes other clients. No tracing attachment or GPU ioctl is used.
"""
import argparse
from collections import Counter
import ctypes as C
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import time


def pids(name):
    result = subprocess.run(['pgrep', '-x', name], capture_output=True, text=True)
    return [int(p) for p in result.stdout.split()]


def stat(path):
    raw = path.read_text()
    fields = raw[raw.rfind(')') + 2:].split()
    return {'state': fields[0], 'cpu_ticks': int(fields[11]) + int(fields[12])}


def frequency_residency():
    result = {}
    path = Path('/sys/class/kgsl/kgsl-3d0/devfreq/trans_stat')
    try:
        for line in path.read_text().splitlines():
            if ':' not in line:
                continue
            left, right = line.split(':', 1)
            frequency = left.strip().lstrip('*').strip()
            if frequency.isdigit() and right.split():
                result[frequency] = int(right.split()[-1])
    except (OSError, ValueError):
        pass
    return result


def read_value(name):
    try:
        return int((Path('/sys/class/kgsl/kgsl-3d0/devfreq') / name).read_text())
    except (OSError, ValueError):
        return None


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)] if ordered else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--label', required=True)
    parser.add_argument('--seconds', type=float, default=30)
    parser.add_argument('--interval-ms', type=float, default=100)
    parser.add_argument('--firefox-pid', type=int)
    args = parser.parse_args()
    assert 1 <= args.seconds <= 60 and 50 <= args.interval_ms <= 1000
    assert not args.output.exists()
    firefox = [p for p in pids('firefox')
               if str((Path('/proc') / str(p) / 'exe').readlink()).startswith('/usr/lib/firefox/')]
    if args.firefox_pid:
        assert args.firefox_pid in firefox
        firefox = [args.firefox_pid]
    assert len(firefox) == 1, 'Select the Linux Firefox process explicitly'
    firefox = firefox[0]
    servers = [p for p in pids('Xorg')
               if 'hdmi-termux-' in str((Path('/proc') / str(p) / 'exe').readlink())]
    assert len(servers) == 1, 'Expected one active leased Xorg'
    xorg = servers[0]
    environment = dict(item.split('=', 1) for item in
                       (Path('/proc') / str(firefox) / 'environ').read_bytes().decode().split('\0')
                       if '=' in item)
    for key in ['DISPLAY', 'XAUTHORITY']:
        assert environment.get(key), key
        os.environ[key] = environment[key]

    rows = [line.split(None, 2) for line in subprocess.check_output(
        ['ps', '-eo', 'pid=,ppid=,comm='], text=True).splitlines()]
    descendants = {firefox}
    while True:
        added = {int(pid) for pid, parent, _ in rows if int(parent) in descendants}
        if added.issubset(descendants):
            break
        descendants.update(added)
    processes = descendants | {xorg}
    threads = {}
    initial = {}
    for pid in sorted(processes):
        root = Path('/proc') / str(pid)
        try:
            initial[pid] = stat(root / 'stat')['cpu_ticks']
            for task in (root / 'task').iterdir():
                name = (task / 'comm').read_text().strip()
                if int(task.name) == pid or 'render' in name.lower() or 'composit' in name.lower():
                    threads[f'{pid}:{task.name}'] = {'path': task, 'name': name}
        except (FileNotFoundError, ProcessLookupError):
            continue
    assert len(threads) <= 64, 'Unexpectedly large thread sample set'
    X = C.CDLL('libX11.so.6')
    X.XOpenDisplay.restype, X.XOpenDisplay.argtypes = C.c_void_p, [C.c_char_p]
    for name in ['XNoOp', 'XCloseDisplay']:
        getattr(X, name).argtypes = [C.c_void_p]
        getattr(X, name).restype = C.c_int
    X.XSync.argtypes, X.XSync.restype = [C.c_void_p, C.c_int], C.c_int
    display = X.XOpenDisplay(None)
    assert display, 'Inherited Firefox display unavailable'
    samples, turnaround, before = [], [], frequency_residency()
    start = time.monotonic()
    signal.alarm(math.ceil(args.seconds) + 10)  # Bound this diagnostic process only.
    try:
        print('READY label=' + args.label + ' seconds=' + str(args.seconds), flush=True)
        while time.monotonic() - start < args.seconds:
            began = time.monotonic()
            X.XNoOp(display)
            X.XSync(display, 0)
            elapsed = (time.monotonic() - began) * 1000
            turnaround.append(elapsed)
            sample = {'seconds': began - start, 'xsync_ms': elapsed,
                      'gpu_frequency_hz': read_value('cur_freq'),
                      'gpu_load': read_value('gpu_load'), 'threads': {}}
            for tid, record in threads.items():
                try:
                    values = stat(record['path'] / 'stat')
                    values['wchan'] = (record['path'] / 'wchan').read_text().strip()
                    values['schedstat'] = [int(x) for x in (record['path'] / 'schedstat').read_text().split()]
                    sample['threads'][tid] = values
                except (FileNotFoundError, ProcessLookupError):
                    pass
            samples.append(sample)
            time.sleep(max(0, args.interval_ms / 1000 - (time.monotonic() - began)))
    finally:
        signal.alarm(0)
        X.XCloseDisplay(display)
    after, cpu = frequency_residency(), {}
    ticks = os.sysconf('SC_CLK_TCK')
    for pid, prior in initial.items():
        try:
            cpu[str(pid)] = (stat(Path('/proc') / str(pid) / 'stat')['cpu_ticks'] - prior) / ticks
        except (FileNotFoundError, ProcessLookupError):
            pass
    waits = {}
    for tid, record in threads.items():
        waits[tid] = {'name': record['name'], 'sampled_wait_channels': dict(Counter(
            s['threads'][tid]['wchan'] for s in samples if tid in s['threads']))}
    result = {'schema': 1, 'label': args.label, 'seconds': time.monotonic() - start,
              'firefox_pid': firefox, 'xorg_pid': xorg,
              'runtime': str((Path('/proc') / str(xorg) / 'exe').readlink()),
              'sample_count': len(samples), 'interval_ms': args.interval_ms,
              'xsync_ms': {'p50': percentile(turnaround, .50), 'p95': percentile(turnaround, .95),
                           'p99': percentile(turnaround, .99), 'max': max(turnaround, default=None)},
              'process_cpu_seconds': cpu, 'thread_wait_samples': waits,
              'gpu_frequency_residency_ms': {k: after[k] - value for k, value in before.items()
                                             if k in after and after[k] >= value},
              'samples': samples,
              'limitations': ['XSync is server request turnaround, not displayed FPS.',
                              'Thread inventory is fixed at capture start; later threads are not included.',
                              'Wait channels and GPU frequency are sampled states.',
                              'GPU residency and load include Android and other GPU clients.',
                              'The label describes requested user activity; it does not verify that activity.']}
    args.output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    if os.geteuid() == 0:
        parent = args.output.parent.stat()
        os.chown(args.output, parent.st_uid, parent.st_gid)
    print(json.dumps({k: result[k] for k in ['label', 'sample_count', 'seconds', 'xsync_ms']}), flush=True)


if __name__ == '__main__':
    main()
