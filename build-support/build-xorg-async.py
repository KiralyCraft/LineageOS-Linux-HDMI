#!/usr/bin/env python3
"""Inside the ARM build container, reuse the frozen O2 build configuration.

Rebuild the complete modesetting and glamor modules from the pinned source and
ordered patch series. Never change the previous build or its source/objects.
"""
import argparse
import concurrent.futures
import hashlib
import json
import pathlib
import shlex
import shutil
import subprocess


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repository', type=pathlib.Path)
    parser.add_argument('output', type=pathlib.Path)
    parser.add_argument('--base', type=pathlib.Path,
                        default=pathlib.Path('/build/xserver-tearfree-c16/src'))
    parser.add_argument('--build', type=pathlib.Path,
                        default=pathlib.Path('/build/xserver-perf-o2-20261002/build'))
    parser.add_argument('--series', type=pathlib.Path, help='Explicit ordered patch list')
    parser.add_argument('--copy-abi', type=int, choices=[1, 2], default=2)
    args = parser.parse_args()
    repo, output, base, build = (p.resolve() for p in
                                (args.repository, args.output, args.base, args.build))
    if output.exists():
        parser.error('refusing to overwrite an existing build')
    output.mkdir(parents=True)
    src = output / 'src'
    shutil.copytree(repo / 'third_party/xserver', src, ignore=shutil.ignore_patterns('.git'))
    patches = []
    for line in (args.series or repo / 'patches/xserver/series').read_text().splitlines():
        name = line.strip()
        if not name or name.startswith('#'):
            continue
        patch = repo / 'patches/xserver' / name
        subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-d', str(src),
                        '-i', str(patch)], check=True)
        patches.append({'name': name, 'sha256': digest(patch)})
    commands_path = build / 'compile_commands.json'
    commands = json.loads(commands_path.read_text())
    tasks, objmap = [], {}
    for command in commands:
        oldsrc = (build / command['file']).resolve()
        if not oldsrc.is_relative_to(base):
            continue
        relative = oldsrc.relative_to(base)
        selected = (relative.is_relative_to('glamor') or
                    relative.is_relative_to('hw/xfree86/drivers/modesetting') or
                    relative.is_relative_to('hw/xfree86/glamor_egl') or
                    relative.is_relative_to('present'))
        if not selected:
            if digest(src / relative) != digest(oldsrc):
                raise RuntimeError(f'unrebuilt source differs: {relative}')
            continue
        argv = shlex.split(command['command'])
        if '-O2' not in argv:
            raise RuntimeError(f'not the expected O2 build: {relative}')
        oldout = argv[argv.index('-o') + 1]
        out = output / 'objects' / oldout
        out.parent.mkdir(parents=True, exist_ok=True)
        objmap[oldout] = str(out)
        argv[argv.index('-o') + 1] = str(out)
        argv[argv.index('-c') + 1] = str(src / relative)
        for flag in ('-MF', '-MQ'):
            if flag in argv:
                argv[argv.index(flag) + 1] = str(out) + ('.d' if flag == '-MF' else '')
        # Redirect only source includes. Keep the frozen generated configuration
        # headers and original system-library search paths from the build.
        for i, value in enumerate(argv):
            if value.startswith('-I') and value != '-I':
                path = (build / value[2:]).resolve()
                if path.is_relative_to(base):
                    argv[i] = '-I' + str(src / path.relative_to(base))
        tasks.append(argv)
    if len(tasks) < 40:
        raise RuntimeError('incomplete matched-module compile selection')
    (output / 'compile-commands.json').write_text(json.dumps(tasks, indent=2) + '\n')

    def compile_one(argv):
        subprocess.run(argv, cwd=build, check=True)
        print('COMPILED', argv[argv.index('-c') + 1], flush=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(compile_one, tasks))
    archives = {}
    for old_archive in ('glamor/libglamor.a', 'present/liblibxserver_present.a'):
        archive = output / pathlib.Path(old_archive).name
        members = subprocess.check_output(['ar', 't', str(build / old_archive)],
                                          text=True).splitlines()
        replacement_members = []
        for member in members:
            relative = str(pathlib.Path(member).relative_to(build))
            if relative not in objmap:
                raise RuntimeError(f'unrebuilt archive member: {relative}')
            replacement_members.append(objmap[relative])
        subprocess.run(['ar', 'rcs', str(archive), *replacement_members], check=True)
        archives[old_archive] = str(archive)
    outputs = {}
    for target in ('hw/xfree86/drivers/modesetting/modesetting_drv.so',
                   'hw/xfree86/glamor_egl/libglamoregl.so', 'hw/xfree86/Xorg'):
        raw = subprocess.check_output(['ninja', '-C', str(build), '-t', 'commands', target],
                                      text=True).strip().splitlines()[-1]
        argv = shlex.split(raw)
        out = output / pathlib.Path(target).name
        argv[argv.index('-o') + 1] = str(out)
        argv = [objmap.get(value, archives.get(value, value))
                for value in argv]
        subprocess.run(argv, cwd=build, check=True)
        outputs[out.name] = digest(out)
    # The new entry points must be exported, not just present in local symbols.
    symbols = subprocess.check_output(['nm', '-D', str(output / 'libglamoregl.so')], text=True)
    for symbol in ('glamor_egl_native_fence_supported', 'glamor_egl_export_native_fence') + (('glamor_copy_tearfree',) if args.copy_abi == 2 else ()):
        if not any(line.endswith(' T ' + symbol) for line in symbols.splitlines()):
            raise RuntimeError(f'missing exported native-fence entry point: {symbol}')
    if b'HDMI_LOS_XORG_ASYNC_ABI=1' not in (output / 'modesetting_drv.so').read_bytes():
        raise RuntimeError('missing modesetting ABI marker')
    if f'HDMI_LOS_XORG_COPY_ABI={args.copy_abi}'.encode() not in (output / 'libglamoregl.so').read_bytes():
        raise RuntimeError('missing restricted-copy ABI marker')
    symbols = subprocess.check_output(['nm', '-D', str(output / 'Xorg')], text=True)
    for symbol in ('present_set_copy_release', 'present_drain_copy_releases'):
        if not any(line.endswith(' T ' + symbol) for line in symbols.splitlines()):
            raise RuntimeError(f'missing exported Present entry point: {symbol}')
    if b'HDMI_LOS_XORG_RELEASE_ABI=1' not in (output / 'modesetting_drv.so').read_bytes():
        raise RuntimeError('missing consumer-release ABI marker')
    result = {'base': str(base), 'configuration': str(build), 'optimization': 'O2',
              'compile_commands_sha256': digest(commands_path), 'patches': patches,
              'artifacts': outputs, 'live_tested': False, 'compiled_units': len(tasks)}
    (output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print('BUILD_COMPLETE', json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
