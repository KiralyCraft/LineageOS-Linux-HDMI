#!/usr/bin/env python3
"""Build-host unit checks of the patched production TearFree blit helper.
Pass the patched Xserver source directory as the only argument. No display.
"""
from pathlib import Path
import subprocess
import sys
import tempfile
source = (Path(sys.argv[1]) / 'glamor/glamor_copy.c').read_text()
start = source.index('Bool\nglamor_copy_tearfree(')
end = source.index('/*\n * Copy from GPU to GPU', start)
fixture = Path(__file__).with_suffix('.c').read_text()
with tempfile.TemporaryDirectory(prefix='tearfree-blit-') as directory:
    d = Path(directory)
    (d/'test.c').write_text(fixture.replace('/* PRODUCTION */', source[start:end]))
    subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-g','-O1',
                    '-fsanitize=address,undefined', str(d/'test.c'),'-o',str(d/'test')],check=True)
    subprocess.run([str(d/'test')],check=True,timeout=30)
