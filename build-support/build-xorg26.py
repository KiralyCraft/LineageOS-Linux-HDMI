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
import tempfile


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
    ap.add_argument('--resume', action='store_true',
                    help='Verify source/configuration and resume an interrupted build')
    ap.add_argument('--validate-staged', action='store_true',
                    help='Resume final checks only after successful upstream tests and installation')
    a = ap.parse_args()
    if a.validate_staged:
        a.resume = True
    repo, archives, out = (p.resolve() for p in (a.repository, a.archives, a.output))
    if platform.machine() != 'aarch64':
        ap.error('use the ARM build container on the build server')
    if not shutil.which('xkbcomp') or not Path('/usr/share/X11/xkb/rules/evdev').is_file():
        ap.error('install xorg-xkbcomp and xkeyboard-config before building/testing')
    if out.exists() and not a.resume:
        ap.error('refusing to overwrite an existing build')
    if a.resume and not (out / 'build/meson-info/intro-buildoptions.json').is_file():
        ap.error('resume requires an existing configured build')
    out.mkdir(parents=True, exist_ok=a.resume)
    # On resume, reconstruct every pinned source file in a separate directory
    # and compare it with the compiled tree before reusing any objects.
    verification = tempfile.TemporaryDirectory(prefix='xorg26-verify-') if a.resume else None
    source_root = Path(verification.name) if verification else out / 'src'
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
            t.extractall(source_root, filter='data')
        extracted[name] = source_root / roots.pop()
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
    if a.resume:
        actual = {item['name']: item['value'] for item in
                  json.loads((build / 'meson-info/intro-buildoptions.json').read_text())}
        for option in options:
            key, value = option.removeprefix('--').removeprefix('-D').split('=', 1)
            if str(actual[key]).lower() != value.lower():
                raise RuntimeError(f'resume configuration mismatch: {key}')
        for name, reference in extracted.items():
            existing = out / 'src' / reference.name
            for p in reference.rglob('*'):
                if p.is_file() and sha(p) != sha(existing / p.relative_to(reference)):
                    raise RuntimeError(f'resume source mismatch: {p}')
            extracted[name] = existing
        src = extracted['xserver']
        verification.cleanup()
    else:
        run(['meson', 'setup', str(build), str(src), *options])
    if not a.validate_staged:
        run(['ninja', '-C', str(build), '-j', str(a.jobs)])
        # ARM emulation needs longer finite deadlines than native execution.
        run(['meson', 'test', '-C', str(build), '--print-errorlogs',
             '--timeout-multiplier', '5', '--num-processes', str(a.jobs)])
        run(['meson', 'install', '-C', str(build), '--destdir', str(stage)])
    else:
        tests = [json.loads(line) for line in (build / 'meson-logs/testlog.json').read_text().splitlines()]
        if len(tests) != 6 or any(t['result'] not in ('OK', 'SKIP') for t in tests):
            raise RuntimeError('staged validation requires successful upstream tests')
        # Meson rewrites module RPATHs during installation, so installed bytes
        # need not equal raw build outputs. Restage from the verified build using
        # Meson's own install plan instead of comparing those unlike files.
        run(['meson', 'install', '-C', str(build), '--no-rebuild', '--destdir', str(stage)])
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
    eb.mkdir(exist_ok=a.resume)
    env['CFLAGS'] = '-O2 -g'
    if not a.validate_staged:
        run([str(extracted['evdev'] / 'configure'), '--prefix=/usr', '--libdir=/usr/lib',
             '--with-xorg-module-dir=/usr/lib/xorg/modules'], cwd=eb, env=env)
        run(['make', '-j', str(a.jobs)], cwd=eb, env=env)
    run(['make', 'install', 'DESTDIR=' + str(stage), 'sdkdir=/usr/include/xorg'], cwd=eb, env=env)
    # Upstream's earlier module test cannot include evdev before it is built.
    # Validate the final staged input driver against the same server exports too.
    modules = stage / 'usr/lib/xorg/modules'
    run([str(build / 'hw/xfree86/loader/xorg_symbol_test'),
         str(build / 'hw/xfree86/libxorgserver.so'),
         *[str(modules / p) for p in ('libshadow.so', 'extensions/libglx.so',
           'libglamoregl.so', 'drivers/modesetting_drv.so', 'input/evdev_drv.so')]])
    sanitizer_env = os.environ.copy()
    # LeakSanitizer's thread discovery fails under QEMU user emulation. Keep
    # ASan/UBSan active; also run the fixture natively on the build host for LSan.
    sanitizer_env['ASAN_OPTIONS'] = 'detect_leaks=0:halt_on_error=1'
    run(['python3', str(repo / 'tests/graphics-contract/xorg-copy-release-unit.py'),
         str(src / 'present/present_execute.c')], env=sanitizer_env)
    run(['python3', str(repo / 'tests/graphics-contract/xorg-export-reply-fixture.py'),
         str(src)], env=sanitizer_env)
    for file, symbols in [
        (stage / 'usr/bin/Xorg', ['present_set_copy_release', 'present_drain_copy_releases',
                                 'dri3_set_fd_export_fence']),
        (stage / 'usr/lib/xorg/modules/libglamoregl.so',
         ['glamor_egl_native_fence_supported', 'glamor_egl_export_native_fence'])]:
        exported = subprocess.check_output(['nm', '-D', str(file)], text=True)
        for symbol in symbols:
            if not any(line.endswith(' T ' + symbol) for line in exported.splitlines()):
                raise RuntimeError(f'missing exported symbol: {symbol}')
    for relative, markers in {
        'usr/lib/xorg/modules/drivers/modesetting_drv.so':
            ['HDMI_LOS_XORG_ASYNC_ABI=1', 'HDMI_LOS_XORG_RELEASE_ABI=1', 'HDMI_LOS_XORG_PRESENTER_ABI=1'],
        'usr/lib/xorg/modules/libglamoregl.so': ['HDMI_LOS_XORG_COPY_ABI=1',
                                               'HDMI_LOS_XORG_EXPORT_ABI=1'],
    }.items():
        for marker in markers:
            if marker.encode() not in (stage / relative).read_bytes():
                raise RuntimeError(f'missing enabled feature: {marker}')
    files = {str(p.relative_to(stage)): sha(p) for p in sorted(stage.rglob('*')) if p.is_file()}
    result = {'sources': sources, 'patches': patches, 'meson_options': options,
              'files': files, 'compiled_units': len(json.loads((build / 'compile_commands.json').read_text())),
              'compiler': subprocess.check_output(['cc', '--version'], text=True).splitlines()[0],
              'arm_fixture_sanitizers': 'address,undefined; leak detection disabled under QEMU',
              'clean_build': True, 'copy_abi': 1, 'hardware_tested': False}
    (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print('BUILD_COMPLETE', json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
