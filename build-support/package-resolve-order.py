#!/usr/bin/env python3
"""Copy the latest HDMI runtime and replace its complete matched Mesa build."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    for name in ['base', 'build', 'mesa_source', 'output']:
        ap.add_argument('--'+name.replace('_','-'), type=Path, required=True)
    a = ap.parse_args()
    base, build, source, output = (getattr(a, name).resolve() for name in ['base','build','mesa_source','output'])
    assert not output.exists() and not output.is_relative_to(base)
    subprocess.run(['sha256sum','--strict','-c','SHA256SUMS'],cwd=base,check=True,stdout=subprocess.DEVNULL)
    manifest = json.loads((build/'result.json').read_text())
    verification = json.loads((build/'source-verification.json').read_text())
    assert not verification['mismatches'] and verification['files'] > 10000
    assert 'PASS: production cache' in (build/'cache-test.log').read_text()
    commit = subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
    assert commit == manifest['commit']
    assert not subprocess.check_output(['git','-C',str(source),'diff','HEAD','--','src'],text=True)
    for name, expected in manifest['files'].items():
        assert sha(build/'stage'/name) == expected, name
    original = {str(p.relative_to(base)):sha(p) for p in base.rglob('*') if p.is_file()}
    shutil.copytree(base, output, symlinks=True)
    libraries = {'libgallium-26.2.0-devel.so':'libgallium-26.2.0-devel.so',
                 'libGLX_mesa.so.0.0.0':'libGLX_mesa.so.0', 'libEGL_mesa.so.0.0.0':'libEGL_mesa.so.0',
                 'libgbm.so.1.0.0':'libgbm.so.1', 'dri/libdril_dri.so':'libdril_dri.so',
                 'gbm/dri_gbm.so':'gbm/dri_gbm.so'}
    for name, target in libraries.items():
        shutil.copy2(build/'stage/usr/lib'/name, output/'lib/mesa'/target)
    assert (output/'lib/mesa/kgsl_dri.so').samefile(output/'lib/mesa/libdril_dri.so')
    assert b'drawable_prepare' in (output/'lib/mesa/libgallium-26.2.0-devel.so').read_bytes()
    for name in ['result.json','source-verification.json','cache-test.log']:
        shutil.copy2(build/name,output/'validation'/('resolve-order-'+name))
    for name, parent in [('resolve-order-on-557306b5c.patch','557306b5c'),
                         ('experiment-on-daa6e56de.patch','daa6e56de'),
                         ('stack-on-cc190637b.patch','cc190637b')]:
        with (output/'source/mesa'/name).open('wb') as f:
            subprocess.run(['git','-C',str(source),'diff',parent,commit],stdout=f,check=True)
    info = json.loads((output/'build-info.json').read_text())
    info['components']['mesa'] = dict(commit=commit, base='557306b5c488632a9771346d3f12d23117c9cf5b',
                                    build='validation/resolve-order-result.json')
    info['resolve_order'] = dict(mesa_commit=commit, copied_from=str(base),
        compiled_on='root@192.168.104.201', physical_validation='pending',
        change='Resolve after drawable vertices/MSAA/HUD preparation and before one final fence flush',
        preserved=['Xorg 26','broker/agent 0.4.3 compatibility','companion','USB/Bluetooth input','launcher/profile'])
    for name, entry in info['artifacts'].items():
        entry.update(sha256=sha(output/name), size=(output/name).stat().st_size)
    (output/'build-info.json').write_text(json.dumps(info,indent=2)+'\n')
    (output/'RESOLVE-ORDER.md').write_text('''HDMI BCDEF Mesa resolve ordering candidate

Run ./run-agent.sh from this directory. The existing launcher and its BCDEF,
continuous-session, E ABI 1, release-fence and low-latency defaults are retained.
The matching broker 0.4.3 supports pause/resume with HDMI left connected.

Mesa c31019a886f955f516eb2c7af2ceb9279ee43d20 moves the integrated image copy
into the normal drawable pre-flush callback, after deferred vertices, bitmap
work, MSAA resolve and HUD drawing. One outgoing fence covers rendering and
the final copy into shared storage. No per-frame CPU GPU-completion wait is
added. Worker-context and Termux:X11 paths retain their existing flush behavior.

This corrects the source ordering exposed by the deferred-draw test. Live test
results are recorded separately; the build manifest remains a pre-deployment
record. It does not establish that Firefox/Maps latency is solved. Unique-size
resizing also has substantial allocation/import costs requiring separate work.

source/mesa contains the exact delta and complete patches on the preserved
vblank-minimal and prior HDMI baseline. Other native/kernel sources and private
Xorg modules come from the copied runtime. The prior Downloads folder is the
rollback runtime. No Magisk redeployment is needed for this Mesa change.
''')
    allowed = {'build-info.json','SHA256SUMS','source/mesa/experiment-on-daa6e56de.patch'}
    for name, expected in original.items():
        if not name.startswith('lib/mesa/') and name not in allowed:
            assert sha(output/name) == expected, name
    subprocess.run(['bash','-n',str(output/'run-agent.sh')],check=True)
    paths = [p for p in sorted(output.rglob('*')) if p.is_file() and p.relative_to(output).as_posix()!='SHA256SUMS']
    (output/'SHA256SUMS').write_text(''.join(f'{sha(p)}  {p.relative_to(output)}\n' for p in paths))
    subprocess.run(['sha256sum','--strict','-c','SHA256SUMS'],cwd=output,check=True,stdout=subprocess.DEVNULL)
    print(output)


if __name__ == '__main__':
    main()
