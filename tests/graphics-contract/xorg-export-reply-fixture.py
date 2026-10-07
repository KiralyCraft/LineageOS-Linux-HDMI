import argparse
from pathlib import Path
import subprocess,tempfile
ap=argparse.ArgumentParser();ap.add_argument('xorg_source',type=Path);a=ap.parse_args()
root=Path(__file__).resolve().parent
production='\n'.join(l for l in (a.xorg_source/'dri3/dri3_fd_reply.c').read_text().splitlines() if not l.startswith('#include'))
with tempfile.TemporaryDirectory(prefix='hdmi-export-fixture-') as t:
 p=Path(t);(p/'test.c').write_text((root/'xorg-export-reply-fixture.c').read_text().replace('/* PRODUCTION */',production))
 subprocess.run(['gcc','-std=gnu11','-g','-O1','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-omit-frame-pointer',str(p/'test.c'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True,timeout=15)
