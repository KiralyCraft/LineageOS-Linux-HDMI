#!/usr/bin/env python3
"""Copy a verified HDMI bundle and replace only its matched Mesa payload."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


parser = argparse.ArgumentParser(description=__doc__)
for name in ['base', 'build', 'mesa-source', 'repository', 'output']:
    parser.add_argument('--' + name, type=Path, required=True)
args = parser.parse_args()
base, build, mesa, repo, out = [getattr(args, name).resolve()
                              for name in ['base', 'build', 'mesa_source', 'repository', 'output']]
assert not out.exists(), out
subprocess.run(['sha256sum', '--strict', '-c', 'SHA256SUMS'], cwd=base,
               stdout=subprocess.DEVNULL, check=True)
result = json.loads((build / 'final-result.json').read_text())
commit = subprocess.check_output(['git', '-C', str(mesa), 'rev-parse', 'HEAD'], text=True).strip()
assert result['commit'] == commit
verification = json.loads((build / 'source-verification.json').read_text())
assert verification['commit'] == commit and not verification['mismatches']
assert 'PASS: production cache' in (build / 'cache-test.log').read_text()
assert 'pass' in (build / 'termux-pacer-test.log').read_text()
for name, expected in result['files'].items():
    if name.startswith('usr/lib/'):
        assert sha(build / 'stage' / name) == expected, name
shutil.copytree(base, out, symlinks=True)
libraries = {'libgallium-26.2.0-devel.so': 'libgallium-26.2.0-devel.so',
             'libGLX_mesa.so.0.0.0': 'libGLX_mesa.so.0',
             'libEGL_mesa.so.0.0.0': 'libEGL_mesa.so.0',
             'libgbm.so.1.0.0': 'libgbm.so.1',
             'dri/libdril_dri.so': 'libdril_dri.so',
             'gbm/dri_gbm.so': 'gbm/dri_gbm.so'}
for name, target in libraries.items():
    shutil.copy2(build / 'stage/usr/lib' / name, out / 'lib/mesa' / target)
assert (out / 'lib/mesa/kgsl_dri.so').samefile(out / 'lib/mesa/libdril_dri.so')
assert b'HDMI_LOS_MESA_PACER_ABI=1' in (out / 'lib/mesa/libgallium-26.2.0-devel.so').read_bytes()
for top in ['android', 'companion', 'bin', 'lib/xorg', 'libexec']:
    for path in (base / top).rglob('*'):
        if path.is_file():
            assert sha(path) == sha(out / path.relative_to(base)), path
assert sha(base / 'run-agent.sh') == sha(out / 'run-agent.sh')
for name in ['final-result.json', 'source-verification.json', 'cache-test.log',
             'termux-pacer-test.log', 'build-final.py']:
    shutil.copy2(build / name, out / 'validation' / ('hdmi-pacer-' + name))
head = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
shutil.rmtree(out / 'source/hdmi')
(out / 'source/hdmi').mkdir()
with subprocess.Popen(['git', '-C', str(repo), 'archive', head], stdout=subprocess.PIPE) as archive:
    subprocess.run(['tar', 'xf', '-', '-C', str(out / 'source/hdmi')], stdin=archive.stdout, check=True)
    archive.stdout.close()
    assert archive.wait() == 0
for reference in ['eff5c2203', 'cc190637b']:
    patch = subprocess.check_output(['git', '-C', str(mesa), 'diff', reference, commit])
    (out / 'source/mesa' / ('hdmi-pacer-on-' + reference + '.patch')).write_bytes(patch)
(out / 'run-paced.sh').write_text('''#!/usr/bin/env bash
set -Eeuo pipefail
BUNDLE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if (($# == 0)); then
    printf 'usage: %s command [arguments...]\\n' "$0" >&2
    exit 2
fi
if [[ ${MESA_KGSL_X11_PIPELINE:-0} != 1 ]]; then
    printf 'Run this from the leased HDMI desktop with its graphics environment.\\n' >&2
    exit 2
fi
export LD_LIBRARY_PATH="$BUNDLE/lib/mesa${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export LIBGL_DRIVERS_PATH="$BUNDLE/lib/mesa"
export GBM_BACKENDS_PATH="$BUNDLE/lib/mesa/gbm"
exec "$@"
''')
(out / 'run-paced.sh').chmod(0o755)
(out / 'run-client.sh').symlink_to('run-paced.sh')
report = 'docs/experiments/HDMI-INTERVAL-ZERO-PACER-20261008.md'
readme = f'''HDMI interval-zero pacing candidate, 2026-10-08

Mesa: {commit}; HDMI source: {head}

From an existing HDMI terminal, ./run-client.sh command [arguments...] selects
this folder's matched Mesa without restarting Xorg or changing the desktop.
For Quake, use r_swapInterval 0 and com_maxfps 0, or the 65 FPS control at 60 Hz.
The wrapper inherits DISPLAY and XAUTHORITY and applies only to that application.
Interval-zero HDMI swaps are paced by default. To opt out for one application:
MESA_DRI3_PRESENT_MODE=unpaced ./run-client.sh command [arguments...]
The compatibility name run-paced.sh selects the same libraries and also respects
the opt-out. Interval-one swaps keep their previous policy.

./run-agent.sh remains the complete continuous BCDEF launcher. It retains the
USB/Bluetooth input bridge, CPU session hint, companion, private Xorg and broker.
New HDMI applications use default-on interval-zero pacing with this Mesa build.
Termux:X11's default and capability checks remain unchanged.

The HDMI backend reuses the original Termux pacer with an independent clock and
ledger. Termux's implementation, tests and capability checks are unchanged.
The producer can overlap the preceding presentation. A native completion fence
still gates handing each shared image to Xorg, with real COMPLETE and IDLE
events governing ownership. No fence is treated as an X11 shared-memory fence.
MESA_KGSL_HDMI_PACED_MARGIN_US adjusts the downstream reserve (default 8000 us).
MESA_KGSL_HDMI_PACER_STATS=1 prints occasional aggregate client diagnostics.

See source/hdmi/{report} for 1080p60 results and experimental limits.
Those tests measure software cadence and real flip events where eligible;
physical 4K60, 4K30 with this pacer and live Termux regression are not claimed.

Rollback: use the original application launch command or the previous bundle:
{base}
No Magisk installation is required. Raw measurements use tmpfs and server SSD.
'''
(out / 'README.md').write_text(readme)
(out / 'HDMI-PACER-20261008.md').write_text(readme)
(out / 'ACTIVE-SESSION.md').write_text('This is a complete alternative runtime. The current agent and Xorg remain on the prior bundle. run-client.sh selects the matched default-on client Mesa without restarting them. Existing processes keep their already-loaded libraries.\n')
info = json.loads((out / 'build-info.json').read_text())
info['source_commit'] = head
info['candidate'] += '-interval-zero-pacer'
info['components']['mesa'] = dict(commit=commit, base=result['base'],
                                build='validation/hdmi-pacer-final-result.json')
info['hdmi_interval_zero_pacer'] = dict(opt_out='MESA_DRI3_PRESENT_MODE=unpaced',
    default=True, margin_us=8000, producer_frames=2, commitments=1,
    termux_code_changed=False, report='source/hdmi/' + report)
for name, entry in info['artifacts'].items():
    path = out / name
    if path.is_file():
        entry.update(sha256=sha(path), size=path.stat().st_size)
info['artifacts']['run-paced.sh'] = dict(sha256=sha(out / 'run-paced.sh'), size=(out / 'run-paced.sh').stat().st_size)
info['artifacts']['run-client.sh'] = dict(sha256=sha(out / 'run-client.sh'), size=(out / 'run-client.sh').stat().st_size)
(out / 'build-info.json').write_text(json.dumps(info, indent=2) + '\n')
for script in ['run-agent.sh', 'run-paced.sh']:
    subprocess.run(['bash', '-n', str(out / script)], check=True)
paths = sorted(path for path in out.rglob('*') if path.is_file() and path.name != 'SHA256SUMS')
(out / 'SHA256SUMS').write_text(''.join(sha(path) + '  ' + str(path.relative_to(out)) + '\n' for path in paths))
subprocess.run(['sha256sum', '--strict', '-c', 'SHA256SUMS'], cwd=out,
               stdout=subprocess.DEVNULL, check=True)
print(json.dumps(dict(output=str(out), mesa=commit, source=head, verified_files=len(paths))))
