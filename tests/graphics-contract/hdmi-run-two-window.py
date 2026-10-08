from pathlib import Path
import os,subprocess,sys,shutil
b=Path('/tmp/hdmi-app-pacing-20261008');pids=[r.split(None,1)[0] for r in subprocess.check_output(['ps','-eo','pid=,args='],text=True).splitlines() if len(r.split(None,1))==2 and r.split(None,1)[1].startswith('/usr/bin/lxsession -s LXDE')];assert len(pids)==1
env=dict(s.decode().split('=',1) for s in Path('/proc/'+pids[0]+'/environ').read_bytes().split(b'\0') if b'=' in s);auth=b/'Xauthority';shutil.copyfile(env['XAUTHORITY'],auth);os.chown(auth,4000,4000);auth.chmod(0o600);env['XAUTHORITY']=str(auth);env.pop('LD_PRELOAD',None)
subprocess.run(['python3',str(b/'move-two-windows.py'),str(b/(sys.argv[1]+'.json'))],env=env,user=4000,group=4000,extra_groups=[],check=True,timeout=30)
