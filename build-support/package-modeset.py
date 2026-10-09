#!/usr/bin/env python3
"""Package a verified complete runtime with a matching broker, preserving device payloads."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import stat
import subprocess
import tarfile
import zipfile


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def check(root):
    subprocess.run(['sha256sum', '--strict', '-c', 'SHA256SUMS'], cwd=root,
                   stdout=subprocess.DEVNULL, check=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    for name in ('baseline', 'runtime', 'output'):
        ap.add_argument('--' + name, type=Path, required=True)
    args = ap.parse_args()
    runtime = args.runtime.resolve()
    check(runtime)
    info = json.loads((runtime / 'build-info.json').read_text())
    fix = info['modeset_lifecycle']
    commit = fix['source_commit']
    assert fix['live_roundtrips_passed'] > 0
    assert len(commit) == 40 and all(c in '0123456789abcdef' for c in commit)
    args.output.mkdir(exist_ok=False, parents=True)
    root = args.output / 'module'
    root.mkdir()
    with zipfile.ZipFile(args.baseline) as z:
        assert z.testzip() is None
        for entry in z.infolist():
            path = Path(entry.filename)
            assert not path.is_absolute() and '..' not in path.parts
            assert not stat.S_ISLNK(entry.external_attr >> 16)
            z.extract(entry, root)
            if not entry.is_dir():
                (root / path).chmod((entry.external_attr >> 16) & 0o777 or 0o644)
    check(root)
    original = {str(p.relative_to(root)): sha(p) for p in root.rglob('*') if p.is_file()}
    module_info = json.loads((root / 'build-info.json').read_text())
    old = module_info['runtime_archive']
    assert Path(old).parts[0] == 'runtime' and '..' not in Path(old).parts
    new = f'runtime/bcdef-modeset-{commit[:12]}.tar.gz'
    with tarfile.open(root / new, 'w:gz') as tar:
        tar.add(runtime, arcname=runtime.name)
    (root / old).unlink()
    shutil.copy2(runtime / 'android/hdmi-losd', root / 'bin/hdmi-losd')
    (root / 'bin/hdmi-losd').chmod(0o755)
    for name in ('compatible.ok', 'diagnostic-only'):
        (root / name).unlink(missing_ok=True)
    (root / 'module.prop').write_text(
        'id=hdmi-los\nname=HDMI BCDEF with fullscreen mode transitions\n'
        f'version=0.4.5-modeset-{commit[:12]}\nversionCode=202610095\nauthor=KiralyCraft\n'
        'description=Matched Xorg and broker lifecycle for exclusive fullscreen resolution changes.\n')
    (root / 'README.txt').write_text(
        'Use the embedded modeset runtime with this broker. It includes USB/Bluetooth, '
        'BCDEF and default-on HDMI interval-zero Mesa pacing.\n'
        'The kernel companion, composer and redesigned Android app are unchanged.\n'
        'Timing is renewed from kernel accounting; watchdog expiry aborts, never authorizes presentation.\n')
    module_info.update(runtime_archive=new, bcdef_runtime=info, modeset_lifecycle=fix,
                       packaging_source_commit=commit, native_repository_commit=commit,
                       package_repository_commit=commit, repository_commit=commit,
                       magisk_installed=False)
    for name, relative in [('hdmi-losd', 'android/hdmi-losd'),
                           ('hdmi-los-agent', 'bin/hdmi-los-agent'),
                           ('libhdmi-los-drmtrace.so', 'lib/libhdmi-los-drmtrace.so')]:
        payload = runtime / relative
        module_info['artifacts'][name] = dict(sha256=sha(payload),
                                             size=payload.stat().st_size,
                                             repository_commit=commit)
    (root / 'build-info.json').write_text(json.dumps(module_info, indent=2) + '\n')
    allowed = {'bin/hdmi-losd', 'module.prop', 'README.txt', 'build-info.json',
               'SHA256SUMS', 'compatible.ok', 'diagnostic-only', old}
    for name, expected in original.items():
        if name not in allowed:
            assert sha(root / name) == expected, name
    excluded = {'SHA256SUMS', 'customize.sh', 'README.md', 'system/placeholder'}
    (root / 'SHA256SUMS').write_text(''.join(
        f'{sha(p)}  {p.relative_to(root)}\n' for p in sorted(root.rglob('*'))
        if p.is_file() and str(p.relative_to(root)) not in excluded))
    check(root)
    target = args.output / f'hdmi-los-modeset-{commit[:12]}-magisk.zip'
    with zipfile.ZipFile(target, 'x', compression=zipfile.ZIP_DEFLATED) as z:
        for p in sorted(root.rglob('*')):
            if p.is_file(): z.write(p, str(p.relative_to(root)))
    with zipfile.ZipFile(target) as z: assert z.testzip() is None
    (args.output / 'SHA256SUMS').write_text(f'{sha(target)}  {target.name}\n')
    print(target)


if __name__ == '__main__':
    main()
