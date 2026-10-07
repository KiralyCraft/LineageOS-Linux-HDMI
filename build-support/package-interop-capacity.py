#!/usr/bin/env python3
"""Package matched KGSL producer-ready Xorg exports and bounded Mesa capacity."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify_bundle(root):
    for line in (root / 'SHA256SUMS').read_text().splitlines():
        digest, name = line.split('  ', 1)
        assert sha(root / name) == digest, name


def verify_build(build):
    result = json.loads((build / 'result.json').read_text())
    for name, digest in result['files'].items():
        assert sha(build / 'stage' / name) == digest, name
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    for name in ('base', 'mesa_build', 'xorg_build', 'mesa_source', 'repository', 'output'):
        ap.add_argument('--' + name.replace('_', '-'), type=Path, required=True)
    a = ap.parse_args()
    base, mesa, xorg, source, repo, out = (getattr(a, name).resolve() for name in
        ('base', 'mesa_build', 'xorg_build', 'mesa_source', 'repository', 'output'))
    assert not out.exists() and not out.is_relative_to(base)
    verify_bundle(base)
    mr, xr = verify_build(mesa), verify_build(xorg)
    commit = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    assert commit == mr['commit']
    assert not subprocess.check_output(['git', '-C', str(source), 'diff', 'HEAD', '--', 'src'], text=True)
    verification = json.loads((mesa / 'source-verification.json').read_text())
    assert verification['files'] > 10000 and not verification['mismatches']
    assert 'PASS: production cache' in (mesa / 'cache-test.log').read_text()
    assert 'PASS: production deferred export' in (xorg / 'host-export-test.log').read_text()
    assert 'PASS: production deferred export' in (xorg / 'final-validation.log').read_text()
    for patch in xr['patches']:
        assert sha(repo / 'patches/xserver26' / patch['name']) == patch['sha256']
    assert any(p['name'].startswith('0011-') for p in xr['patches'])
    repository_commit = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    shutil.copytree(base, out, symlinks=True)
    libraries = {'libgallium-26.2.0-devel.so': 'libgallium-26.2.0-devel.so',
        'libGLX_mesa.so.0.0.0': 'libGLX_mesa.so.0', 'libEGL_mesa.so.0.0.0': 'libEGL_mesa.so.0',
        'libgbm.so.1.0.0': 'libgbm.so.1', 'dri/libdril_dri.so': 'libdril_dri.so',
        'gbm/dri_gbm.so': 'gbm/dri_gbm.so'}
    for name, target in libraries.items():
        shutil.copy2(mesa / 'stage/usr/lib' / name, out / 'lib/mesa' / target)
    assert (out / 'lib/mesa/kgsl_dri.so').samefile(out / 'lib/mesa/libdril_dri.so')
    shutil.copy2(xorg / 'stage/usr/bin/Xorg', out / 'libexec/Xorg')
    shutil.rmtree(out / 'lib/xorg/modules')
    shutil.copytree(xorg / 'stage/usr/lib/xorg/modules', out / 'lib/xorg/modules', symlinks=True)
    for name, marker in [('lib/xorg/modules/libglamoregl.so', b'HDMI_LOS_XORG_EXPORT_ABI=1'),
                          ('lib/mesa/libgallium-26.2.0-devel.so', b'HDMI_LOS_MESA_CAPACITY_ABI=1')]:
        assert marker in (out / name).read_bytes(), name
    launcher = (base / 'run-agent.sh').read_text()
    anchor = """printf 'HDMI experimental candidate: %s\\n' "$CANDIDATE" >&2"""
    assert launcher.count(anchor) == 1
    control = """
# The private HDMI pipeline requires producer-ready Xorg allocation replies.
# Retain an independent control for the exact-size comparison.
export MESA_KGSL_HDMI_RESIZE_CAPACITY=${MESA_KGSL_HDMI_RESIZE_CAPACITY:-1}
unset MESA_KGSL_HDMI_ALLOC_READBACK_CONTROL
if [[ ${MESA_KGSL_X11_PIPELINE:-0} == 1 ]]; then
    LC_ALL=C grep -aFq 'HDMI_LOS_XORG_EXPORT_ABI=1' "$BUNDLE/lib/xorg/modules/libglamoregl.so" &&
    LC_ALL=C grep -aFq 'dri3_has_fd_export_fence' "$BUNDLE/libexec/Xorg" || {
        printf 'Missing matched producer-ready Xorg exports\\n' >&2; exit 1;
    }
fi
"""
    (out / 'run-agent.sh').write_text(launcher.replace(anchor, anchor + '\n' + control))
    subprocess.run(['bash', '-n', str(out / 'run-agent.sh')], check=True)
    # Native lease/input integration, installed companion and prior launch controls survive.
    for top in ('bin', 'android', 'companion', 'lib/mesa-baseline'):
        for p in (base / top).rglob('*'):
            if p.is_file():
                assert sha(p) == sha(out / p.relative_to(base)), p
    for p in (base / 'lib').glob('*.so'):
        assert sha(p) == sha(out / p.relative_to(base)), p
    for name in ('result.json', 'source-verification.json', 'cache-test.log'):
        shutil.copy2(mesa / name, out / 'validation' / ('interop-mesa-' + name))
    for name in ('result.json', 'final-validation.log', 'host-export-test.log'):
        shutil.copy2(xorg / name, out / 'validation' / ('interop-xorg-' + name))
    for name, parent in [('interop-on-c31019a88.patch', 'c31019a88'),
                         ('experiment-on-daa6e56de.patch', 'daa6e56de'),
                         ('stack-on-cc190637b.patch', 'cc190637b')]:
        with (out / 'source/mesa' / name).open('wb') as stream:
            subprocess.run(['git', '-C', str(source), 'diff', parent, commit], stdout=stream, check=True)
    shutil.rmtree(out / 'source/hdmi')
    (out / 'source/hdmi').mkdir()
    with subprocess.Popen(['git', '-C', str(repo), 'archive', repository_commit], stdout=subprocess.PIPE) as archive:
        subprocess.run(['tar', '-xf', '-', '-C', str(out / 'source/hdmi')], stdin=archive.stdout, check=True)
        assert archive.wait() == 0
    (out / 'source/xorg-built-series').write_text(''.join('patches/xserver26/' + p['name'] + '\n' for p in xr['patches']))
    env = os.environ.copy()
    env.pop('LD_PRELOAD', None)
    env['LD_LIBRARY_PATH'] = str(out / 'lib/mesa')
    version = subprocess.run([str(out / 'libexec/Xorg'), '-version'], env=env, capture_output=True, text=True, check=True)
    (out / 'validation/interop-xorg-version.txt').write_text(version.stdout + version.stderr)
    dependencies = []
    for p in [out / 'libexec/Xorg', *sorted((out / 'lib/xorg/modules').rglob('*.so'))]:
        result = subprocess.run(['ldd', str(p)], env=env, capture_output=True, text=True, check=True)
        assert 'not found' not in result.stdout + result.stderr, p
        dependencies.append(str(p.relative_to(out)) + '\n' + result.stdout + result.stderr)
    (out / 'validation/interop-runtime-dependencies.txt').write_text('\n'.join(dependencies))
    info = json.loads((out / 'build-info.json').read_text())
    info.update(schema=7, candidate='BCDEF-Xorg26-KGSL-export-order-resize-capacity', source_commit=repository_commit)
    info['components']['mesa'] = dict(commit=commit, base=mr['base'], build='validation/interop-mesa-result.json')
    info['components']['xorg'] = dict(version=xr['sources']['xserver']['version'], source_commit=repository_commit,
        build='validation/interop-xorg-result.json', compiled_units=xr['compiled_units'], evdev=xr['sources']['evdev']['version'])
    info['interop_capacity'] = dict(base_bundle=str(base), resize_capacity_default=True,
        export_capability='0x08000000', initialization='native-fence-gated standard DRI3 replies',
        physical_validation='pending', magisk_redeployment_required=False)
    info['validation'] = dict(build_checks='PASS; device-dependent builder tests skipped',
        runtime_dependencies='PASS', physical_display_tested=False, deployed=False)
    for name, entry in info['artifacts'].items():
        if (out / name).is_file():
            entry.update(sha256=sha(out / name), size=(out / name).stat().st_size)
    for p in (out / 'lib/xorg/modules').rglob('*'):
        if p.is_file():
            info['artifacts'][str(p.relative_to(out))] = dict(sha256=sha(p), size=p.stat().st_size)
    (out / 'build-info.json').write_text(json.dumps(info, indent=2) + '\n')
    (out / 'INTEROP-CAPACITY.md').write_text("""KGSL/Xorg buffer handoff and resize capacity candidate, 2026-10-08

Launch ./run-agent.sh from this directory. Defaults remain BCDEF, continuous,
async TearFree, consumer release fencing, low-latency queue, hardware rendering,
USB/Bluetooth input and the existing loaded kernel companion.

Xorg holds each KGSL pixmap export reply until its native completion fence
succeeds. Only the allocating connection is paused; Xorg's event loop services
other clients. This closes the GPU initialization race without pixel readback
or pretending KGSL publishes implicit DMA-BUF producer fences.

Mesa keeps shared resolve destinations in 128-pixel capacity buckets. Application
render targets and logical viewport stay exact. Each Present carries a logical
valid/update region. Fullscreen storage remains exact for flip eligibility.
Three cached generations and a 512 MiB backing-size budget remain enforced.
MESA_KGSL_HDMI_RESIZE_CAPACITY=0 ./run-agent.sh selects exact-size storage.
No diagnostic allocation readback is enabled by the launcher.

Both Mesa and Xorg/modules were built on root@192.168.104.201. Validation records
build checks separately from live software captures and physical user reports.
source/mesa contains the exact complete patches on the preserved vblank-minimal
baseline; source/hdmi contains the private Xorg series and USB/Bluetooth work.

No new Magisk installation is needed for graphics. Native broker 0.4.4 is included
but its separate staged Magisk ZIP still requires manual installation if the
phone currently runs 0.4.3. A same-agent connected restart avoids that version's
paused-registration defect. Preserve the previous Downloads folder for rollback.
""")
    (out / 'README.md').write_text((out / 'INTEROP-CAPACITY.md').read_text())
    files = sorted(p for p in out.rglob('*') if p.is_file() and p.name != 'SHA256SUMS')
    (out / 'SHA256SUMS').write_text(''.join(sha(p) + '  ' + str(p.relative_to(out)) + '\n' for p in files))
    verify_bundle(out)
    print(json.dumps(dict(output=str(out), mesa_commit=commit, hdmi_commit=repository_commit,
                         files=len(files), physical_validation='pending'), indent=2))


if __name__ == '__main__':
    main()
