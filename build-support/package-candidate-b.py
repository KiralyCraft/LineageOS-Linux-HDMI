#!/usr/bin/env python3
"""Package verified, previously built Candidate B artifacts; never deploy them."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import zipfile


def digest(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def verify_sums(root):
    subprocess.run(['sha256sum', '--strict', '-c', 'SHA256SUMS'], cwd=root,
                   check=True, stdout=subprocess.DEVNULL)


def write_sums(root):
    paths = sorted(p for p in root.rglob('*') if p.is_file() and not p.is_symlink()
                   and p.name != 'SHA256SUMS')
    (root / 'SHA256SUMS').write_text(''.join(
        f'{digest(p)}  {p.relative_to(root)}\n' for p in paths))


def make_zip(root, target):
    with zipfile.ZipFile(target, 'x', compression=zipfile.ZIP_DEFLATED,
                         compresslevel=9) as archive:
        for path in sorted(root.rglob('*')):
            if path.is_dir():
                continue
            relative = str(path.relative_to(root))
            info = zipfile.ZipInfo(relative, date_time=(2026, 10, 5, 0, 0, 0))
            info.create_system = 3
            info.external_attr = path.lstat().st_mode << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            data = os.readlink(path).encode() if path.is_symlink() else path.read_bytes()
            archive.writestr(info, data)
    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('installed_module', type=Path)
    parser.add_argument('runtime', type=Path)
    parser.add_argument('current_apk', type=Path)
    parser.add_argument('apk_verification', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    source, base, runtime = args.source.resolve(), args.installed_module.resolve(), args.runtime.resolve()
    commit = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    assert re.fullmatch('[0-9a-f]{40}', commit)
    assert not subprocess.check_output(['git', '-C', str(source), 'status', '--porcelain'], text=True)
    assert 'id=hdmi-los\n' in (base / 'module.prop').read_text()
    verify_sums(runtime)
    verify_sums(runtime / 'companion')
    info = json.loads((runtime / 'build-info.json').read_text())
    identity = json.loads((runtime / 'companion/manifest.json').read_text())
    previous = json.loads((base / 'build-info.json').read_text())
    for relative, artifact in info['artifacts'].items():
        assert digest(runtime / relative) == artifact['sha256'], relative
    for relative, name in [('bin/hdmi-losd', 'hdmi-losd'),
                           ('apk/HdmiLosTile.apk', 'HdmiLosTile.apk')]:
        assert digest(base / relative) == previous['artifacts'][name]['sha256'], relative
    vendor = {}
    for line in (base / 'patched-checksums.list').read_text().splitlines():
        relative, expected = line.split('|')
        assert relative.startswith('vendor/') and '..' not in Path(relative).parts
        assert digest(base / relative) == expected == previous['artifacts'][Path(relative).name]['sha256']
        vendor[relative] = expected
    assert len(vendor) == 3
    assert info['candidate_b']['mesa_commit'] == 'daa6e56de0e43503426286863848b694a01a95e7'
    assert info['validation']['clean_shutdown'].startswith('PASS')
    assert identity['kernel_commit'] == 'd00ba216ccda5d4fcc0d864729ae69d5b63d860c'
    apk = json.loads(args.apk_verification.read_text())
    assert apk['current_sha256'] == digest(args.current_apk)
    assert apk['previous_sha256'] == digest(base / 'apk/HdmiLosTile.apk')
    assert apk['same_signer'] and apk['current_version_code'] >= apk['previous_version_code']

    # Fail before writing when any artifact/provenance check fails. An output is
    # immutable: select a new directory for a subsequent package revision.
    args.output.mkdir(parents=True, exist_ok=False)
    module, rollback = args.output / 'module', args.output / 'rollback'
    shutil.copytree(base, module)
    shutil.copytree(base, rollback)
    for directory in (module, rollback):
        for name in ('compatible.ok', 'diagnostic-only'):
            (directory / name).unlink(missing_ok=True)
        # Keep the current, signed redesigned application in either package.
        shutil.copy2(args.current_apk, directory / 'apk/HdmiLosTile.apk')
    shutil.copy2(base / 'build-info.json', module / 'previous-build-info.json')
    for name in ('service.sh', 'customize.sh', 'companion-loader.sh'):
        shutil.copy2(source / 'module' / name, module / name)
        (module / name).chmod(0o755)
    shutil.copy2(runtime / 'android/hdmi-losd', module / 'bin/hdmi-losd')
    shutil.copytree(runtime / 'companion', module / 'companion')
    (module / 'companion/hdmi-companion-probe').chmod(0o755)
    values = {'EXPECTED_RELEASE': identity['kernel_release'],
              'EXPECTED_CONFIG_SHA256': identity['runtime_config_sha256'],
              'EXPECTED_BUILD_ID': identity['build_id']}
    assert all(re.fullmatch('[A-Za-z0-9._+-]+', value) for value in values.values())
    (module / 'timing.env').write_text(''.join(f"{key}='{value}'\n" for key, value in values.items()))
    (module / 'module.prop').write_text(
        'id=hdmi-los\nname=HDMI Xorg takeover with session timing\n'
        f'version=0.3-candidate-b-{commit[:12]}\nversionCode=20261005\n'
        'author=KiralyCraft\ndescription=Matched Candidate B timing companion, '
        'asynchronous TearFree runtime and reliable USB/Bluetooth input.\n')
    archive_dir = module / 'runtime'
    archive_dir.mkdir()
    runtime_name = f'candidate-b-{commit[:12]}.tar.gz'
    with tarfile.open(archive_dir / runtime_name, 'w:gz', dereference=False) as archive:
        archive.add(runtime, arcname='candidate-b')
    packaged = dict(previous)
    packaged.update(schema=2, packaging_source_commit=commit,
                    build_mode='verified Candidate B artifact reuse',
                    candidate_b_runtime=info, companion=identity,
                    preserved_vendor_payloads=vendor, apk_verification=apk,
                    runtime_archive=f'runtime/{runtime_name}',
                    magisk_installed=False, boot_activation='not tested')
    packaged['artifacts'] = {**previous['artifacts'],
        'hdmi-losd': {**info['artifacts']['android/hdmi-losd'],
                     'repository_commit': info['candidate_b']['native_source_commit']},
        'HdmiLosTile.apk': {'sha256': digest(args.current_apk),
                           'size': args.current_apk.stat().st_size,
                           'reuse': 'current signed Android app; signer and version verified'}}
    (module / 'build-info.json').write_text(json.dumps(packaged, indent=2) + '\n')
    rollback_info = dict(previous)
    rollback_info['artifacts'] = {**previous['artifacts'], 'HdmiLosTile.apk': packaged['artifacts']['HdmiLosTile.apk']}
    rollback_info['apk_verification'] = apk
    (rollback / 'build-info.json').write_text(json.dumps(rollback_info, indent=2) + '\n')
    readme = (source / 'docs/CANDIDATE_B_STANDALONE.md').read_text()
    (module / 'README.txt').write_text(readme)
    (args.output / 'README.md').write_text(readme)
    for directory in (module, rollback):
        for relative, expected in vendor.items():
            assert digest(directory / relative) == expected
        write_sums(directory)
        verify_sums(directory)
    make_zip(module, args.output / f'hdmi-los-candidate-b-{commit[:12]}-magisk.zip')
    make_zip(rollback, args.output / f'hdmi-los-before-candidate-b-{commit[:12]}-rollback.zip')
    (args.output / 'manifest.json').write_text(json.dumps(packaged, indent=2) + '\n')
    targets = sorted(p for p in args.output.iterdir() if p.is_file())
    (args.output / 'SHA256SUMS').write_text(''.join(f'{digest(p)}  {p.name}\n' for p in targets))
    print(f'Candidate B and rollback ZIPs ready in {args.output}; nothing installed.')


if __name__ == '__main__':
    main()
