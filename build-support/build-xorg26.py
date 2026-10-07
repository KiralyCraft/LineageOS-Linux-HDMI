#!/usr/bin/env python3
"""Clean native ARM Xorg/evdev build; run in the remote build container only.

Uses checksum-pinned official archives and patches/xserver26/series. Never
installs into the container or phone; all installation is staged under output.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile


def sha(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def run(argv, **kwargs):
    print('RUN', argv, flush=True)
    subprocess.run(argv, check=True, **kwargs)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('repository', type=Path)
    ap.add_argument('archives', type=Path)
    ap.add_argument('output', type=Path)
    ap.add_argument('--jobs', type=int, default=8)
    a = ap.parse_args()
    repo, archives, out = (p.resolve() for p in (a.repository, a.archives, a.output))
    if platform.machine() != 'aarch64':
        ap.error('use the ARM build container on the build server')
    if out.exists():
        ap.error('refusing to overwrite an existing build')
    out.mkdir(parents=True)
    sources = json.loads((repo / 'build-support/xorg26-sources.json').read_text())
    extracted = {}
    for name, info in sources.items():
        archive = archives / info['url'].rsplit('/', 1)[1]
        if sha(archive) != info['sha256']:
            raise RuntimeError(f'source hash mismatch: {name}')
        with tarfile.open(archive) as t:
            roots = {Path(m.name).parts[0] for m in t.getmembers()}
            if len(roots) != 1:
                raise RuntimeError('unexpected archive layout')
            t.extractall(out / 'src', filter='data')
        extracted[name] = out / 'src' / roots.pop()
    src = extracted['xserver']
    patches = []
    for name in (repo / 'patches/xserver26/series').read_text().splitlines():
        if not name or name.startswith('#'):
            continue
        p = repo / 'patches/xserver26' / name
        run(['patch', '--batch', '--fuzz=0', '-p1', '-d', str(src), '-i', str(p)])
        patches.append({'name': name, 'sha256': sha(p)})
    build, stage = out / 'build', out / 'stage'
    options = ['--prefix=/usr', '--libdir=lib', '--buildtype=debugoptimized',
               '-Doptimization=2', '-Db_lto=false', '-Dxorg=true', '-Dglamor=true',
               '-Dxvfb=true', '-Dxephyr=false', '-Dxnest=false', '-Dxwin=false',
               '-Dxquartz=false', '-Dsuid_wrapper=false', '-Dlibunwind=false',
               '-Ddocs=false', '-Ddevel-docs=false', '-Ddocs-pdf=false',
               '-Dxkb_dir=/usr/share/X11/xkb', '-Dxkb_bin_dir=/usr/bin']
    run(['meson', 'setup', str(build), str(src), *options])
    run(['ninja', '-C', str(build), '-j', str(a.jobs)])
    run(['meson', 'test', '-C', str(build), '--print-errorlogs', '--num-processes', str(a.jobs)])
    run(['meson', 'install', '-C', str(build), '--destdir', str(stage)])
    # Build evdev against THIS server's generated SDK and ABI, never host headers.
    env = os.environ.copy()
    env['PKG_CONFIG_PATH'] = str(stage / 'usr/lib/pkgconfig')
    env['PKG_CONFIG_SYSROOT_DIR'] = str(stage)
    # Sysrooting all dependencies would hide ordinary system headers/libraries.
    # Rewrite only the staged SDK prefix and use the normal system dependency set.
    pc = stage / 'usr/lib/pkgconfig/xorg-server.pc'
    pc.write_text(pc.read_text().replace('prefix=/usr', 'prefix=' + str(stage / 'usr')))
    env.pop('PKG_CONFIG_SYSROOT_DIR')
    eb = out / 'evdev-build'
    eb.mkdir()
    env['CFLAGS'] = '-O2 -g'
    run([str(extracted['evdev'] / 'configure'), '--prefix=/usr', '--libdir=/usr/lib',
         '--with-xorg-module-dir=/usr/lib/xorg/modules'], cwd=eb, env=env)
    run(['make', '-j', str(a.jobs)], cwd=eb, env=env)
    run(['make', 'install', 'DESTDIR=' + str(stage)], cwd=eb, env=env)
    run(['python3', str(repo / 'tests/graphics-contract/xorg-copy-release-unit.py'),
         str(src / 'present/present_execute.c')])
    for file, symbols in [
        (stage / 'usr/bin/Xorg', ['present_set_copy_release', 'present_drain_copy_releases']),
        (stage / 'usr/lib/xorg/modules/libglamoregl.so',
         ['glamor_egl_native_fence_supported', 'glamor_egl_export_native_fence'])]:
        exported = subprocess.check_output(['nm', '-D', str(file)], text=True)
        for symbol in symbols:
            if not any(line.endswith(' T ' + symbol) for line in exported.splitlines()):
                raise RuntimeError(f'missing exported symbol: {symbol}')
    for relative, markers in {
        'usr/lib/xorg/modules/drivers/modesetting_drv.so':
            ['HDMI_LOS_XORG_ASYNC_ABI=1', 'HDMI_LOS_XORG_RELEASE_ABI=1', 'HDMI_LOS_XORG_PRESENTER_ABI=1'],
        'usr/lib/xorg/modules/libglamoregl.so': ['HDMI_LOS_XORG_COPY_ABI=1'],
    }.items():
        for marker in markers:
            if marker.encode() not in (stage / relative).read_bytes():
                raise RuntimeError(f'missing enabled feature: {marker}')
    files = {str(p.relative_to(stage)): sha(p) for p in sorted(stage.rglob('*')) if p.is_file()}
    result = {'sources': sources, 'patches': patches, 'meson_options': options,
              'files': files, 'compiled_units': len(json.loads((build / 'compile_commands.json').read_text())),
              'compiler': subprocess.check_output(['cc', '--version'], text=True).splitlines()[0],
              'clean_build': True, 'copy_abi': 1, 'hardware_tested': False}
    (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print('BUILD_COMPLETE', json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
