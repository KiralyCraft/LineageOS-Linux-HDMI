#!/usr/bin/env python3
"""Package matched broker/agent fixes; preserve graphics, APK and kernel payloads."""
import argparse
import hashlib
import json
import pathlib
import shutil
import stat
import subprocess
import tarfile
import tempfile
import zipfile

P = pathlib.Path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_sums(root):
    subprocess.run(['sha256sum', '--strict', '-c', 'SHA256SUMS'], cwd=root,
                   check=True, stdout=subprocess.DEVNULL)


def write_sums(root, excluded=()):
    paths = [p for p in sorted(root.rglob('*')) if p.is_file()
             and p.relative_to(root).as_posix() not in {'SHA256SUMS', *excluded}]
    (root / 'SHA256SUMS').write_text(''.join(
        f'{sha(p)}  {p.relative_to(root)}\n' for p in paths))
    check_sums(root)


def refresh_runtime(root, source, build, manifest, commit):
    check_sums(root)
    original = {p.relative_to(root).as_posix(): sha(p)
                for p in root.rglob('*') if p.is_file()}
    for name, destination in [('hdmi-los-agent', 'bin/hdmi-los-agent'),
                              ('hdmi-losd', 'android/hdmi-losd')]:
        shutil.copy2(build / name, root / destination)
        (root / destination).chmod(0o755)
    write_sums(root / 'android')
    shutil.copy2(source / 'scripts/hdmi-control.sh', root / 'hdmi-control.sh')
    shutil.copy2(source / 'docs/CONNECTED_RESTART.md', root / 'CONNECTED_RESTART.md')
    validation = root / 'validation'
    validation.mkdir(exist_ok=True)
    shutil.copy2(build / 'manifest.json', validation / 'connected-restart-build.json')
    for name in ['restart-test.log', 'stop-test.log', 'lifecycle-test.log', 'child-test.log']:
        shutil.copy2(build / name, validation / name)
    archive = root / 'source' / f'connected-restart-{commit[:12]}.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        for name in manifest['sources']:
            tar.add(source / name, arcname=name)
    info = json.loads((root / 'build-info.json').read_text())
    changes = ['bin/hdmi-los-agent', 'android/hdmi-losd']
    for name in changes:
        info['artifacts'][name] = dict(sha256=sha(root / name), size=(root / name).stat().st_size)
    info['components']['native']['broker_agent'] = dict(source_commit=commit,
        manifest='validation/connected-restart-build.json', source_archive=str(archive.relative_to(root)))
    info['connected_restart'] = dict(source_commit=commit, physical_test='pending',
        graphics_preserved=True, launcher_preserved=True)
    info['changed_compiled_artifacts'] = sorted(set(info.get('changed_compiled_artifacts', []) + changes))
    (root / 'build-info.json').write_text(json.dumps(info, indent=2) + '\n')
    allowed = {*changes, 'android/SHA256SUMS', 'build-info.json', 'SHA256SUMS'}
    for name, want in original.items():
        if name not in allowed:
            assert sha(root / name) == want, name
    write_sums(root)
    return info


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    for name in ['source', 'baseline_zip', 'build', 'output']:
        ap.add_argument(name, type=P)
    ap.add_argument('--bundle', required=True, type=P, help='Verified Downloads runtime to copy')
    ap.add_argument('--bundle-name', required=True)
    a = ap.parse_args()
    assert P(a.bundle_name).name == a.bundle_name and a.bundle_name not in ('.', '..')
    source, build = a.source.resolve(), a.build.resolve()
    manifest = json.loads((build / 'manifest.json').read_text())
    assert manifest['validation']['host_lifecycle'] == 'PASS with ASan UBSan and LSan'
    for name, want in manifest['sources'].items():
        assert sha(source / name) == want, name
    for name, want in manifest['artifacts'].items():
        assert sha(build / name) == want, name
    commit = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    assert not subprocess.check_output(['git', '-C', str(source), 'status', '--porcelain', '--',
        *manifest['sources'], 'docs/CONNECTED_RESTART.md', 'scripts/hdmi-control.sh',
        'build-support/package-connected-restart.py'], text=True)
    a.output.mkdir(parents=True, exist_ok=False)
    bundle = a.output / a.bundle_name
    check_sums(a.bundle)
    shutil.copytree(a.bundle, bundle, symlinks=True)
    refresh_runtime(bundle, source, build, manifest, commit)

    root = a.output / 'module'
    root.mkdir()
    with zipfile.ZipFile(a.baseline_zip) as z:
        assert z.testzip() is None
        for entry in z.infolist():
            path = P(entry.filename)
            assert not path.is_absolute() and '..' not in path.parts
            assert not stat.S_ISLNK(entry.external_attr >> 16)
            z.extract(entry, root)
            if not entry.is_dir():
                (root / path).chmod((entry.external_attr >> 16) & 0o777 or 0o644)
    check_sums(root)
    original = {p.relative_to(root).as_posix(): sha(p) for p in root.rglob('*') if p.is_file()}
    assert (root / 'module.prop').read_text().startswith('id=hdmi-los\n')
    info = json.loads((root / 'build-info.json').read_text())
    old_archive = info['runtime_archive']
    assert P(old_archive).parts[0] == 'runtime' and '..' not in P(old_archive).parts
    # Keep the embedded runtime's existing graphics too; update only its agent,
    # broker, matching sources and validation metadata. Downloads uses Xorg 26.
    with tempfile.TemporaryDirectory(dir=a.output) as temporary:
        stage = P(temporary)
        with tarfile.open(root / old_archive) as tar:
            tar.extractall(stage, filter='data')
        entries = list(stage.iterdir())
        assert len(entries) == 1 and entries[0].is_dir()
        info['bcdef_runtime'] = refresh_runtime(entries[0], source, build, manifest, commit)
        new_archive = f'runtime/bcdef-restart-{commit[:12]}.tar.gz'
        with tarfile.open(root / new_archive, 'w:gz') as tar:
            tar.add(entries[0], arcname=entries[0].name)
        (root / old_archive).unlink()
    info['runtime_archive'] = new_archive
    for name in ['compatible.ok', 'diagnostic-only']:
        (root / name).unlink(missing_ok=True)
    shutil.copy2(build / 'hdmi-losd', root / 'bin/hdmi-losd')
    (root / 'bin/hdmi-losd').chmod(0o755)
    shutil.copy2(source / 'docs/CONNECTED_RESTART.md', root / 'README.txt')
    (root / 'module.prop').write_text(
        'id=hdmi-los\nname=HDMI BCDEF with connected session restart\n'
        f'version=0.4.3-restart-{commit[:12]}\nversionCode=202610073\nauthor=KiralyCraft\n'
        'description=Connected HDMI restart with acknowledged rollback, disconnect generations and prompt process cleanup.\n')
    for name in ['hdmi-losd', 'hdmi-los-agent']:
        info['artifacts'][name] = dict(sha256=sha(build / name), size=(build / name).stat().st_size,
                                       repository_commit=commit)
    info.update(packaging_source_commit=commit, connected_restart=dict(build=manifest, installed=False,
        baseline_zip_sha256=sha(a.baseline_zip), physical_test='pending', protocol_version=3,
        downloads_bundle=a.bundle_name,
        preserved=['composer payloads', 'kernel companion', 'redesigned APK', 'graphics in each runtime',
                   'USB/Bluetooth bridge', 'separate CPU power module']))
    (root / 'build-info.json').write_text(json.dumps(info, indent=2) + '\n')
    permitted = {'bin/hdmi-losd', 'README.txt', 'module.prop', 'build-info.json', 'SHA256SUMS',
                 'compatible.ok', 'diagnostic-only', old_archive}
    for name, want in original.items():
        if name not in permitted:
            assert sha(root / name) == want, name
    installer_only = ('customize.sh', 'README.md', 'system/placeholder')
    write_sums(root, installer_only)
    target = a.output / f'hdmi-los-connected-restart-{commit[:12]}-magisk.zip'
    with zipfile.ZipFile(target, 'x', compression=zipfile.ZIP_DEFLATED) as z:
        for p in sorted(root.rglob('*')):
            if p.is_file():
                entry = zipfile.ZipInfo(str(p.relative_to(root)), date_time=(2026, 10, 7, 0, 0, 0))
                entry.create_system = 3
                entry.external_attr = p.stat().st_mode << 16
                entry.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(entry, p.read_bytes())
    with zipfile.ZipFile(target) as z:
        assert z.testzip() is None
    with tempfile.TemporaryDirectory(dir=a.output) as temporary:
        trial = P(temporary)
        with zipfile.ZipFile(target) as z:
            z.extractall(trial)
        for name in installer_only:
            (trial / name).unlink(missing_ok=True)
        check_sums(trial)
        names = {line.split('  ', 1)[1] for line in (trial / 'SHA256SUMS').read_text().splitlines()}
        assert names == {str(p.relative_to(trial)) for p in trial.rglob('*')
                         if p.is_file() and p.name != 'SHA256SUMS'}
    (a.output / 'SHA256SUMS').write_text(f'{sha(target)}  {target.name}\n')
    print(target)
    print(bundle)


if __name__ == '__main__':
    main()
