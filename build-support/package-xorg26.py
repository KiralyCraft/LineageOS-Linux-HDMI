#!/usr/bin/env python3
"""Package Xorg 26 over a preserved release/queue comparison bundle; no install."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


def sha(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def verify(root):
    for line in (root / 'SHA256SUMS').read_text().splitlines():
        digest, name = line.split('  ', 1)
        if sha(root / name) != digest:
            raise RuntimeError(f'bundle checksum mismatch: {name}')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    for name in ('base', 'build', 'archives', 'repository', 'output'):
        ap.add_argument('--' + name, type=Path, required=True)
    a = ap.parse_args()
    base, build, archives, repo, out = (getattr(a, n).resolve() for n in
                                      ('base', 'build', 'archives', 'repository', 'output'))
    if out.exists():
        ap.error('refusing to overwrite existing bundle')
    verify(base)
    result = json.loads((build / 'result.json').read_text())
    stage = build / 'stage'
    for name, digest in result['files'].items():
        if sha(stage / name) != digest:
            raise RuntimeError(f'build artifact mismatch: {name}')
    for p in result['patches']:
        if sha(repo / 'patches/xserver26' / p['name']) != p['sha256']:
            raise RuntimeError('patch/build mismatch')
    info = json.loads((base / 'build-info.json').read_text())
    if info['components']['mesa']['commit'] != '557306b5c488632a9771346d3f12d23117c9cf5b':
        raise RuntimeError('expected the matched resize/queue Mesa baseline')
    commit = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    shutil.copytree(base, out, symlinks=True)
    (out / 'validation').rename(out / 'validation-before-xorg26')
    (out / 'validation').mkdir()
    shutil.copy2(stage / 'usr/bin/Xorg', out / 'libexec/Xorg')
    shutil.rmtree(out / 'lib/xorg/modules')
    shutil.copytree(stage / 'usr/lib/xorg/modules', out / 'lib/xorg/modules', symlinks=True)
    for name in ('libglamoregl.so', 'extensions/libglx.so', 'drivers/modesetting_drv.so', 'input/evdev_drv.so'):
        if not (out / 'lib/xorg/modules' / name).is_file():
            raise RuntimeError(f'incomplete private module set: {name}')
    # Copy the current Downloads launcher, preserving all accepted runtime controls.
    launcher = (base / 'run-agent.sh').read_text()
    anchor = 'if ((EUID != 0)); then'
    if launcher.count(anchor) != 1:
        raise RuntimeError('unexpected launcher')
    check = '''# Xorg 26 must use its own extension and input modules as well as glamor.
for module in extensions/libglx.so input/evdev_drv.so; do
    [[ -f "$BUNDLE/lib/xorg/modules/$module" ]] || {
        printf 'Missing matched Xorg 26 module: %s\\n' "$module" >&2
        exit 1
    }
done
'''
    (out / 'run-agent.sh').write_text(launcher.replace(anchor, check + anchor))
    subprocess.run(['bash', '-n', str(out / 'run-agent.sh')], check=True)
    # Everything outside the X server/modules and launcher remains byte-identical.
    for top in ('bin', 'lib/mesa', 'lib/mesa-baseline', 'companion'):
        for p in (base / top).rglob('*'):
            if p.is_file() and sha(p) != sha(out / p.relative_to(base)):
                raise RuntimeError(f'unexpected component change: {p}')
    for p in (base / 'lib').glob('*.so'):
        if sha(p) != sha(out / p.relative_to(base)):
            raise RuntimeError('native interposer changed')
    shutil.copy2(build / 'result.json', out / 'validation/xorg26-build.json')
    for name in ('testlog.json', 'testlog.txt'):
        shutil.copy2(build / 'build/meson-logs' / name, out / 'validation' / name)
    shutil.copy2(build / 'build/meson-info/intro-dependencies.json', out / 'validation/dependencies.json')
    source = out / 'source/hdmi'
    shutil.rmtree(source)
    source.mkdir()
    with subprocess.Popen(['git', '-C', str(repo), 'archive', commit], stdout=subprocess.PIPE) as archive:
        subprocess.run(['tar', '-xf', '-', '-C', str(source)], stdin=archive.stdout, check=True)
        if archive.wait():
            raise RuntimeError('git archive failed')
    upstream = out / 'source/xorg26-upstream'
    upstream.mkdir()
    for item in result['sources'].values():
        p = archives / item['url'].rsplit('/', 1)[1]
        if sha(p) != item['sha256']:
            raise RuntimeError('upstream archive mismatch')
        shutil.copy2(p, upstream / p.name)
    (out / 'source/xorg-built-series').write_text(''.join('patches/xserver26/' + p['name'] + '\n' for p in result['patches']))
    info.update(schema=6, candidate='BCDEF-Xorg26-consumer-release-and-queue', source_commit=commit)
    info['components']['xorg'] = {'version': result['sources']['xserver']['version'],
        'source_commit': commit, 'build': 'validation/xorg26-build.json',
        'compiled_units': result['compiled_units'], 'clean_build': True,
        'evdev': result['sources']['evdev']['version']}
    info['validation'] = {'build_and_upstream_tests': 'PASS; see validation',
        'physical_display_tested': False, 'installed': False, 'display_restarted': False}
    info['comparison'].update(base_bundle=str(base), xorg_only_rebase=True,
        kernel_native_input_unchanged=True, xorg_input_module_rebuilt=True)
    # Preserve paths for the unchanged Mesa build's evidence after archiving validation.
    info['components']['mesa']['build'] = 'validation-before-xorg26/mesa-build.json'
    changed = []
    for relative, entry in info['artifacts'].items():
        p = out / relative
        digest = sha(p)
        if digest != entry['sha256']:
            changed.append(relative)
        entry.update(sha256=digest, size=p.stat().st_size)
    for p in (out / 'lib/xorg/modules').rglob('*'):
        if p.is_file():
            info['artifacts'][str(p.relative_to(out))] = {'sha256': sha(p), 'size': p.stat().st_size}
    info['changed_compiled_artifacts'] = sorted(set(changed) | {
        str(p.relative_to(out)) for p in (out / 'lib/xorg/modules').rglob('*') if p.is_file()})
    (out / 'build-info.json').write_text(json.dumps(info, indent=2) + '\n')
    (out / 'control-info.json').write_text(json.dumps(info['comparison'], indent=2) + '\n')
    (out / 'README.md').write_text('''Xorg 26.1 RC3 migration candidate, 2026-10-07

Launch ./run-agent.sh after the agreed HDMI release/unplug/arm procedure.
Defaults: BCDEF, continuous, E ABI 1, release=fence, queue=low-latency.
This is a separate experimental bundle; HDMI acceptance is still pending.
No Magisk update is needed. The native agent, USB/Bluetooth fan-in, kernel
companion, CPU policy and matched Mesa 557306b5c are preserved.

Xorg, glamor, modesetting, GLX and evdev are rebuilt together. The input bridge
is unchanged; its Xorg evdev consumer is rebuilt for the new server headers.
Upstream supplies TearFree and the render-node preference. Six targeted patches
retain async TearFree, E, companion presentation and consumer release fencing.
source/xorg-built-series identifies the actual Xorg 26 patches. The historical
21.1 sources/patches in source/hdmi remain for old build recipes only.

Controls are retained: --candidate BCDF, --present-release finish|legacy,
--mesa-queue fifo. Change one control at a time during comparison.

Rollback: the preserved bcdef-release-queue-20261007 folder provides the same
Mesa and controls with Xorg 21.1.24. Use it after the normal HDMI release/unplug
procedure. Older acceptance logs in validation-before-* do not validate Xorg 26.
SHA256SUMS covers the complete folder; source archives and patches are included.
''')
    files = sorted(p for p in out.rglob('*') if p.is_file() and p != out / 'SHA256SUMS')
    (out / 'SHA256SUMS').write_text(''.join(sha(p) + '  ' + str(p.relative_to(out)) + '\n' for p in files))
    verify(out)
    print(json.dumps({'output': str(out), 'source_commit': commit, 'files': len(files), 'hardware_tested': False}, indent=2))


if __name__ == '__main__':
    main()
