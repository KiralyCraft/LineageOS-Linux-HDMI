#!/usr/bin/env python3
"""Package a Mesa-only comparison, preserving the base Xorg/native stack."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify(root):
    records = (root / 'SHA256SUMS').read_text().splitlines()
    for record in records:
        expected, relative = record.split('  ', 1)
        assert sha(root / relative) == expected, relative
    return len(records)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--mesa-bundle', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    base, mesa, out = (p.resolve() for p in (args.base, args.mesa_bundle, args.output))
    assert not out.exists(), 'Output must be a new directory'
    assert base != mesa
    assert not out.is_relative_to(base) and not out.is_relative_to(mesa)
    counts = {'base': verify(base), 'mesa_bundle': verify(mesa)}
    original = json.loads((base / 'build-info.json').read_text())
    donor = json.loads((mesa / 'build-info.json').read_text())
    assert b'HDMI_LOS_MESA_RESIZE_ABI=1' in (mesa / 'lib/mesa/libgallium-26.2.0-devel.so').read_bytes()
    assert b'HDMI_LOS_XORG_COPY_ABI=1' in (base / 'lib/xorg/modules/libglamoregl.so').read_bytes()

    def ignore_old_mesa(directory, names):
        return ['mesa'] if Path(directory) == base / 'lib' else []

    shutil.copytree(base, out, symlinks=True, ignore=ignore_old_mesa)
    shutil.copytree(mesa / 'lib/mesa', out / 'lib/mesa', symlinks=True)
    shutil.copytree(mesa / 'source/mesa', out / 'source/mesa', dirs_exist_ok=True)
    prior = out / 'validation/prior-mesa-170a56fa9'
    prior.mkdir()
    for name in ['mesa-build.json', 'mesa-source-verification.json', 'mesa-source-tree.txt',
                 'copy-paths-final.json', 'shared-image-matched-final.json']:
        old = out / 'validation' / name
        if old.exists():
            old.rename(prior / name)
        new = mesa / 'validation' / name
        if new.exists():
            shutil.copy2(new, out / 'validation' / name)
    for name in ['mesa-cache-unit.log', 'source-tests-resize.log']:
        shutil.copy2(mesa / 'validation' / name, out / 'validation' / name)

    info = original
    info.update(schema=4, candidate='BCDEF-Mesa-resize-comparison', default_profile='BCDEF')
    info['components']['mesa'] = donor['components']['mesa']
    changed = []
    for relative, entry in info['artifacts'].items():
        path = out / relative
        digest = sha(path)
        if digest != entry['sha256']:
            changed.append(relative)
        entry.update(sha256=digest, size=path.stat().st_size)
    assert changed and all(name.startswith('lib/mesa/') for name in changed), changed
    for name in ['libexec/Xorg', 'lib/xorg/modules/libglamoregl.so',
                 'lib/xorg/modules/drivers/modesetting_drv.so', 'run-agent.sh',
                 'bin/hdmi-los-agent', 'bin/hdmi-input-bridge', 'companion/hdmi_companion.ko']:
        assert sha(out / name) == sha(base / name), name
    assert (out / 'lib/mesa/kgsl_dri.so').samefile(out / 'lib/mesa/libdril_dri.so')
    info['changed_compiled_artifacts'] = changed
    info['validation'] = {
        'package_integrity': 'PASS', 'mesa_resize_abi': 1, 'xorg_copy_abi': 1,
        'new_combination_physically_tested': False, 'installed': False,
        'display_restarted': False,
        'mesa_component_tests': 'Inherited donor results; see validation files. These do not validate this combined runtime.'}
    info['comparison'] = {
        'base_bundle': str(base), 'mesa_donor': str(mesa),
        'base_mesa_commit': '170a56fa943cc475472d170157efe58d7dd9b8c1',
        'mesa_commit': donor['components']['mesa']['commit'],
        'xorg_native_kernel_unchanged': True,
        'live_session_untouched': True, 'input_fan_in_retained': 'USB/Bluetooth',
        'launcher_unchanged': True}
    (out / 'build-info.json').write_text(json.dumps(info, indent=2) + '\n')
    (out / 'control-info.json').write_text(json.dumps(info['comparison'], indent=2) + '\n')
    (out / 'README.md').write_text('''HDMI BCDEF Mesa-only resize comparison

Run ./run-agent.sh --candidate BCDEF --no-timeout --capture none.
The launcher defaults to BCDEF and continuous mode, matching the base runtime.
Use the broker's unplug, arm, reconnect and Mirror procedure when switching.

This bundle changes Mesa from 170a56fa9 to e04c1852d. Xorg (including the
E-enabled ABI 1 blit path), broker, companion and USB/Bluetooth input remain
byte-for-byte identical to the controls bundle. The complete matched Mesa
directory is copied together. The actual changed compiled files are recorded
in build-info.json; the launcher is also unchanged.

The Mesa update retains a bounded three-size cache and moves allocation/import
and generation destruction outside the presentation event-processing lock.
The combination in this folder has not been tested on the physical display.
It is a comparison candidate, not a confirmed fix for Firefox corruption or
Maps stutter. This folder was prepared without installing or restarting anything.

source/mesa contains the exact Mesa patches, including resize-on-170a56fa9.patch.
source/hdmi and the retained older archives describe the unchanged native/Xorg
base. validation/prior-mesa-170a56fa9 retains the old Mesa evidence; the current
Mesa validation files come from the donor build. Do not interpret old acceptance
documents copied from the base as acceptance of this combination.

For rollback, launch the preserved bcdef-controls-20261005 folder after the
normal HDMI release and unplug procedure. SHA256SUMS covers this folder.
''')
    paths = sorted(p for p in out.rglob('*') if p.is_file() and p != out / 'SHA256SUMS')
    (out / 'SHA256SUMS').write_text(''.join(sha(p) + '  ' + str(p.relative_to(out)) + '\n' for p in paths))
    print(json.dumps({'bundle': str(out), 'files_verified': verify(out),
                      'inputs_verified': counts, 'changed_compiled_artifacts': changed,
                      'mesa_commit': info['components']['mesa']['commit'],
                      'physical_display_tested': False}, indent=2))


if __name__ == '__main__':
    main()
