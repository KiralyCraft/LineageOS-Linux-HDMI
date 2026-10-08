#!/usr/bin/env python3
"""Regression: restarted Present serials cannot cross worker/window streams."""
import json
import pathlib
import struct
import subprocess
import tempfile

analyzer = pathlib.Path(__file__).with_name('hdmi-analyze-trace.py')
records = []


def record(ms, event, tid, values):
    at = ms * 1_000_000
    records.append(struct.pack('<QQ5qII', at, at, *values, event, tid))


# Serial 1 repeats in another worker, then after drawable recreation in the
# original worker. A global dictionary matches the first completion to the
# last request, producing a negative latency and false cross-window MSC gaps.
record(10, 7, 11, [1, 0, 100, 0x100, 0])
record(20, 8, 11, [1, 0, 0, 1_000_000, 100])
record(30, 7, 12, [1, 0, 1000, 0x200, 0])
record(40, 8, 12, [1, 0, 0, 2_000_000, 1000])
record(50, 7, 11, [1, 0, 500, 0x300, 0])
record(60, 8, 11, [1, 0, 0, 3_000_000, 500])
record(70, 7, 11, [2, 0, 501, 0x300, 0])
record(80, 8, 11, [2, 0, 0, 3_016_667, 501])
# Main XCB subscription sees duplicate events but sent no private request.
record(81, 8, 99, [2, 0, 0, 3_016_667, 501])

with tempfile.TemporaryDirectory(prefix='hdmi-trace-test-') as directory:
    trace = pathlib.Path(directory) / 'trace.bin'
    trace.write_bytes(struct.pack('<4Q', 0x31305046494d4448, 64, len(records), 0)
                      + b''.join(records))
    result = json.loads(subprocess.check_output(
        ['python3', str(analyzer), str(trace), '--warmup', '0'], text=True))
    present = result['present']
    assert present['completed'] == 4
    assert len(present['streams']) == 3
    assert present['msc_delta'] == {'1': 1}
    assert present['target_error_msc'] == {'0': 4}
    assert present['request_to_event']['mean_ms'] == 10
    assert present['request_to_event']['max_ms'] == 10

print('PASS: restarted serials, independent workers/windows, duplicate events')
