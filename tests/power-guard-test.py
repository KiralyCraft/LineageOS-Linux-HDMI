#!/usr/bin/env python3
"""Exercise the real interposer and monitor against a private fake broker/client.

Run on the build host, as root, after compiling the host fixtures. No device
power settings are read or written. The lease property is an atomic test file.
"""
import argparse
import json
import os
from pathlib import Path
import select
import socket
import struct
import subprocess
import tempfile
import threading
import time


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('build', type=Path)
    a = ap.parse_args()
    build = a.build.resolve()
    assert os.geteuid() == 0, 'Peer credential test requires root on the build server'
    with tempfile.TemporaryDirectory(prefix='hdmi-power-test-') as directory:
        root = Path(directory)
        lease = root / 'lease'
        lease.write_text('0')
        name = 'hdmi-power-test-' + str(os.getpid())
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind('\0' + name); server.listen(); server.settimeout(.1)
        condition = {'mode': 'inactive', 'stop': False}

        def serve():
            while not condition['stop']:
                try:
                    peer, _ = server.accept()
                except socket.timeout:
                    continue
                with peer:
                    packet = bytearray()
                    while len(packet) < 160:
                        chunk = peer.recv(160 - len(packet))
                        if not chunk:
                            break
                        packet.extend(chunk)
                    if len(packet) != 160:
                        continue
                    response = bytearray(160)
                    request = struct.unpack_from('<IHHI', packet)
                    struct.pack_into('<IHHIiI', response, 0, 0x48444d49, 3, 0x8001, request[3], 0,
                                     2 if condition['mode'] != 'inactive' else 0)
                    # Real STATUS does not populate connector/CRTC IDs.
                    struct.pack_into('<I', response, 40, 0x100)
                    struct.pack_into('<III', response, 56, 3840, 2160, 60000)
                    if condition['mode'] == 'invalid':
                        struct.pack_into('<H', response, 4, 99)
                    if condition['mode'] == 'wrong-opcode':
                        struct.pack_into('<H', response, 6, 0x8000)
                    if condition['mode'] == 'mirror':
                        struct.pack_into('<I', response, 16, 0)
                    if condition['mode'] == 'no-mode':
                        struct.pack_into('<I', response, 56, 0)
                    if condition['mode'] == 'closed':
                        continue
                    if condition['mode'] == 'blocked':
                        time.sleep(.35)
                        continue
                    try:
                        # STREAM replies can be fragmented independently of
                        # the fixed-size protocol message.
                        peer.sendall(response[:31]); time.sleep(.005)
                        if condition['mode'] == 'truncated':
                            continue
                        peer.sendall(response[31:])
                    except BrokenPipeError:
                        pass

        thread = threading.Thread(target=serve); thread.start()
        env = dict(os.environ, HDMI_POWER_TEST_LEASE_FILE=str(lease), HDMI_POWER_TEST_SOCKET=name)
        monitor_log = (root / 'monitor.log').open('w')
        client_log = (root / 'client.log').open('w')
        monitor = subprocess.Popen([str(build / 'monitor-test')], env=env,
                                   stdout=monitor_log, stderr=monitor_log)
        # The ASan runtime must precede a preloaded, instrumented interposer.
        asan = subprocess.check_output(['cc', '-print-file-name=libasan.so'], text=True).strip()
        client_env = dict(env, LD_PRELOAD=asan + ':' + str(build / 'guard-test.so'))
        client = subprocess.Popen([str(build / 'host-client'), str(build / 'libqti-perfd-client.so')],
                                  env=client_env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=client_log, text=True, bufsize=1)

        def reply():
            assert select.select([client.stdout], [], [], 5)[0], 'Fixture response timed out'
            value = client.stdout.readline().strip()
            assert value, 'Fixture exited; inspect test log'
            return value

        def command(value):
            client.stdin.write(value + '\n'); client.stdin.flush()
            result = reply()
            return json.loads(result) if value == 'state' else result

        def eventually(predicate, limit=3):
            end = time.monotonic() + limit
            while time.monotonic() < end:
                state = command('state')
                if predicate(state):
                    return state
                time.sleep(.03)
            raise AssertionError(state)

        try:
            assert reply() == 'READY'
            check = subprocess.check_output([str(build / 'monitor-test'), '--check'], env=env, text=True)
            assert 'broker_reply_valid=1 active_lease=0' in check
            # PowerHAL may send its first ON before PerfHAL is ready. The
            # retry must retain ON's type=-1 even before any OFF event exists.
            command('reject-hint')
            command('on')
            assert command('state')['mode'] == -1
            command('accept')
            eventually(lambda s: s['mode'] == 1 and not s['floor'])
            calls = command('state')['on_calls']
            time.sleep(.25)
            assert command('state')['on_calls'] == calls, 'Successful startup retry kept repeating'
            command('off')
            eventually(lambda s: s['mode'] == 0 and not s['floor'])
            condition['mode'] = 'active'
            eventually(lambda s: s['mode'] == 1 and s['floor'])
            command('off')
            assert command('state')['mode'] == 1, 'Screen-off request escaped active HDMI policy'
            eventually(lambda s: s['mode'] == 1 and s['floor'])
            time.sleep(.5)
            assert command('state')['floor'], 'Core vote was lost after a display-state transition'
            command('on')
            eventually(lambda s: s['mode'] == 1 and s['floor'])
            command('off')
            eventually(lambda s: s['mode'] == 1 and s['floor'])
            command('other')
            assert command('state')['other'] == 0x1080, 'Unrelated hint changed'
            condition['mode'] = 'inactive'
            eventually(lambda s: s['mode'] == 0 and not s['floor'])
            # Unplug while dozing must not require another framework screen event.
            condition['mode'] = 'active'
            eventually(lambda s: s['mode'] == 1 and s['floor'])
            condition['mode'] = 'blocked'
            eventually(lambda s: s['mode'] == 0 and not s['floor'])
            condition['mode'] = 'invalid'
            eventually(lambda s: s['mode'] == 0 and not s['floor'])
            time.sleep(.4)
            assert command('state')['mode'] == 0, 'Invalid active lease was accepted'
            for mode in ['wrong-opcode', 'mirror', 'no-mode', 'closed', 'truncated']:
                condition['mode'] = 'active'
                eventually(lambda s: s['mode'] == 1 and s['floor'])
                condition['mode'] = mode
                eventually(lambda s: s['mode'] == 0 and not s['floor'])
            condition['mode'] = 'active'
            eventually(lambda s: s['mode'] == 1 and s['floor'])
            monitor.kill(); monitor.wait(timeout=3)
            began = time.monotonic()
            eventually(lambda s: s['mode'] == 0 and not s['floor'])
            assert time.monotonic() - began < 2, 'Expired monitor lease lingered'
            # Malformed or implausibly distant deadlines never authorize a boost.
            for value in ['garbage', '-1', '999999999999999999999999999', '999999999999999']:
                lease.write_text(value); time.sleep(.15)
                assert command('state')['mode'] == 0
            lease.write_text(str(int(time.clock_gettime(time.CLOCK_BOOTTIME) * 1000) + 1000))
            eventually(lambda s: s['mode'] == 1 and s['floor'])
            command('on')
            lease.write_text('0')
            eventually(lambda s: s['mode'] == 1 and not s['floor'])
            command('off')
            assert command('state')['mode'] == 0
            command('reject')
            lease.write_text(str(int(time.clock_gettime(time.CLOCK_BOOTTIME) * 1000) + 1000))
            eventually(lambda s: s['mode'] == 1 and not s['floor'])
            lease.write_text('0')
            eventually(lambda s: s['mode'] == 0 and not s['floor'])
            command('reject-hint')
            lease.write_text(str(int(time.clock_gettime(time.CLOCK_BOOTTIME) * 1000) + 1000))
            time.sleep(.2)
            assert command('state')['mode'] == 0 and not command('state')['floor']
            command('accept')
            eventually(lambda s: s['mode'] == 1 and s['floor'])
            lease.write_text('0')
            eventually(lambda s: s['mode'] == 0 and not s['floor'])
            client.stdin.close(); assert client.wait(timeout=5) == 0
            print('PASS: dynamic lookup, initial ON failure before first OFF, exact hint type/retry, core vote renewal after display reset, hint forwarding, asleep unplug, awake unplug, broker timeout/closure, fragmented/truncated replies, wrong opcode/version, Android mirror, no active mode, monitor death/expiry, malformed lease, rejected core request, failed hint retry and normal exit')
        finally:
            if monitor.poll() is None:
                monitor.terminate(); monitor.wait(timeout=3)
            if client.poll() is None:
                client.terminate(); client.wait(timeout=3)
            condition['stop'] = True; thread.join(timeout=2); server.close()
            monitor_log.close(); client_log.close()
            for log in ['monitor.log', 'client.log']:
                print(log + ':\n' + (root / log).read_text())


if __name__ == '__main__':
    main()
