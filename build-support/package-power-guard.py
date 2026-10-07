#!/usr/bin/env python3
"""Package built CPU guard artifacts and the verified stock PowerHAL, never install.

The private output contains a device vendor binary and must not be committed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_installed_layout(archive_path):
    """Check the ZIP after Magisk 29's documented installer cleanup.

    customize.sh is sourced during installation and then deleted. README.md,
    .git* and system/placeholder are also installer-only paths. README.txt is
    deliberately retained. Runtime checks must still cover every retained file.
    """
    with tempfile.TemporaryDirectory(prefix='hdmi-power-install-layout-') as directory:
        root = Path(directory)
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(root)
        for path in [root / 'customize.sh', root / 'README.md',
                     root / 'system/placeholder', *root.glob('.git*')]:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
        subprocess.run(['sha256sum', '--strict', '-c', 'SHA256SUMS'], cwd=root,
                       check=True, stdout=subprocess.DEVNULL)
        retained = {str(p.relative_to(root)) for p in root.rglob('*')
                    if p.is_file() and p.name != 'SHA256SUMS'}
        covered = {line.split('  ', 1)[1] for line in (root / 'SHA256SUMS').read_text().splitlines()}
        assert covered == retained, 'Runtime checksum list must cover every retained file'
        assert (root / 'README.txt').is_file()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('source', type=Path)
    ap.add_argument('build', type=Path)
    ap.add_argument('stock_powerhal', type=Path)
    ap.add_argument('output', type=Path)
    a = ap.parse_args()
    source, build = a.source.resolve(), a.build.resolve()
    manifest = json.loads((build / 'manifest.json').read_text())
    assert manifest['target'] == 'aarch64-linux-android35'
    assert manifest['validation']['host_lifecycle'] == 'PASS with ASan and UBSan'
    for relative, expected in manifest['sources'].items():
        assert digest(source / relative) == expected, f'Stale build source: {relative}'
    for relative, expected in manifest['artifacts'].items():
        assert digest(build / relative) == expected, f'Artifact mismatch: {relative}'
    checks = dict(line.split('|') for line in
                  (source / 'module-power/original-checksums.list').read_text().splitlines())
    original = '/vendor/bin/hw/android.hardware.power-service-qti'
    assert digest(a.stock_powerhal) == next(k for k, v in checks.items() if v == original)
    commit = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    paths = ['native/power', 'module-power', 'tests/power-guard-test.py',
             'build-support/build-power-guard.sh', 'build-support/package-power-guard.py',
             'module/mount-utils.sh', 'docs/HDMI_CPU_POWER.md', 'tests/power-package-test.py']
    assert not subprocess.check_output(
        ['git', '-C', str(source), 'status', '--porcelain', '--', *paths], text=True), 'Commit CPU guard sources first'
    a.output.mkdir(parents=True, exist_ok=False)
    module = a.output / 'module'
    shutil.copytree(source / 'module-power', module)
    shutil.copy2(source / 'module/mount-utils.sh', module / 'mount-utils.sh')
    shutil.copy2(source / 'docs/HDMI_CPU_POWER.md', module / 'README.txt')
    (module / 'bin').mkdir()
    shutil.copy2(build / 'android/hdmi-power-monitor', module / 'bin/hdmi-power-monitor')
    shutil.copy2(build / 'android/hdmi-power-launcher', module / 'bin/hdmi-power-launcher')
    libdir = module / 'system/vendor/lib64'
    libdir.mkdir(parents=True)
    shutil.copy2(build / 'android/libhdmi_los_power_guard.so', libdir / 'libhdmi_los_power_guard.so')
    bindir = module / 'system/vendor/bin/hw'
    bindir.mkdir(parents=True)
    shutil.copy2(a.stock_powerhal, bindir / 'android.hardware.power-service-qti.hdmi-stock')
    (bindir / 'android.hardware.power-service-qti.hdmi-stock').chmod(0o755)
    for path in (module / 'bin').iterdir():
        path.chmod(0o755)
    for path in module.glob('*.sh'):
        path.chmod(0o755)
    manifest.update(source_commit=commit, magisk_id='hdmi-los-power', installed=False,
                    original_powerhal_sha256=digest(a.stock_powerhal),
                    validation={**manifest['validation'],
                                'magisk_installed_layout_checksums': 'PASS after installer cleanup',
                                'physical_hdmi_screen_off': 'pending manual install and test'},
                    behavior=dict(lease_expiry_ms=1500, core_floor_duration_ms=1000,
                                  performance_min_cores=4, prime_min_cores=1,
                                  frequency_minimum='unchanged', frequency_maximum='unchanged',
                                  thermal_policy='unchanged', broker_and_graphics_stack='unchanged'))
    (module / 'build-info.json').write_text(json.dumps(manifest, indent=2) + '\n')
    info = module / 'module.prop'
    info.write_text(info.read_text().replace('version=0.1.2\n', f'version=0.1.2-{commit[:12]}\n'))
    # Magisk removes customize.sh after sourcing it. Verify the persisted
    # runtime, rather than referencing an installer-only file on every boot.
    (module / 'SHA256SUMS').write_text(''.join(
        f'{digest(p)}  {p.relative_to(module)}\n' for p in sorted(module.rglob('*'))
        if p.is_file() and p.relative_to(module) != Path('customize.sh')))
    subprocess.run(['sha256sum', '--strict', '-c', 'SHA256SUMS'], cwd=module,
                   check=True, stdout=subprocess.DEVNULL)
    target = a.output / f'hdmi-los-cpu-power-{commit[:12]}-magisk.zip'
    with zipfile.ZipFile(target, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(module.rglob('*')):
            if not path.is_file():
                continue
            assert not path.is_symlink()
            item = zipfile.ZipInfo(str(path.relative_to(module)), date_time=(2026, 10, 7, 0, 0, 0))
            item.create_system = 3
            item.external_attr = path.stat().st_mode << 16
            item.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(item, path.read_bytes())
    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None
        assert not any(p in archive.namelist() for p in ('skip_mount', 'system/vendor/bin/hw/android.hardware.power-service-qti'))
        assert archive.read('system/vendor/bin/hw/android.hardware.power-service-qti.hdmi-stock') == a.stock_powerhal.read_bytes()
    verify_installed_layout(target)
    (a.output / 'SHA256SUMS').write_text(f'{digest(target)}  {target.name}\n')
    print(target)


if __name__ == '__main__':
    main()
