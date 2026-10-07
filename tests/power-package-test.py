#!/usr/bin/env python3
"""Reproduce the original boot checksum failure and verify its correction.

Uses private ZIP copies and temporary directories; never modifies Android.
"""
import argparse
import importlib.util
from pathlib import Path
import subprocess


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('original_zip', type=Path)
    ap.add_argument('corrected_zip', type=Path)
    args = ap.parse_args()
    source = Path(__file__).resolve().parents[1] / 'build-support/package-power-guard.py'
    spec = importlib.util.spec_from_file_location('power_package', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        module.verify_installed_layout(args.original_zip)
    except subprocess.CalledProcessError:
        pass
    else:
        raise AssertionError('Original package should fail after Magisk removes customize.sh')
    module.verify_installed_layout(args.corrected_zip)
    print('PASS: original boot checksum bug reproduced; corrected installed layout fully verified')


if __name__ == '__main__':
    main()
