import argparse,os,pathlib,shutil,subprocess
ap=argparse.ArgumentParser();ap.add_argument('--pacer-stats',action='store_true');ap.add_argument('--present-mode',choices=['unpaced','paced']);ap.add_argument('--vblank-mode',choices=['0','1','2','3']);ap.add_argument('--paced-margin-us');ap.add_argument('--maximize',action='store_true');ap.add_argument('--fullscreen',action='store_true');ap.add_argument('--hide-cursor',action='store_true');ap.add_argument('--pause-check',action='store_true');ap.add_argument('name');ap.add_argument('--binary',default='/usr/bin/glxgears');ap.add_argument('--static',action='store_true');ap.add_argument('--captures',action='store_true');ap.add_argument('--drm-captures',action='store_true');ap.add_argument('--seconds',default='25');ap.add_argument('--mesa-build');ap.add_argument('--integrated');ap.add_argument('--binding-diagnostic',action='store_true');ap.add_argument('--lazy-slots');ap.add_argument('--framebuffer-helper');ap.add_argument('--framebuffer-period-ns',type=int);ap.add_argument('--wm-resize',action='store_true');ap.add_argument('--background-pixel');ap.add_argument('--paced-queue');ap.add_argument('--paced-lead');a=ap.parse_args()
base=pathlib.Path('/tmp/hdmi-app-pacing-20261008');pids=[r.split(None,1)[0] for r in subprocess.check_output(['ps','-eo','pid=,args='],text=True).splitlines() if len(r.split(None,1))==2 and r.split(None,1)[1].startswith('/usr/bin/lxsession -s LXDE')];assert len(pids)==1
env=dict(s.decode().split('=',1) for s in pathlib.Path('/proc/'+pids[0]+'/environ').read_bytes().split(b'\0') if b'=' in s)
auth=base/'Xauthority';shutil.copyfile(env['XAUTHORITY'],auth);os.chown(auth,4000,4000);auth.chmod(0o600)
env.update(XAUTHORITY=str(auth),LD_PRELOAD=str(base/'frame-pacing-probe.so'),MESA_KGSL_X11_BRIDGE_STATS='1',XDG_CACHE_HOME=str(base/'cache'),MESA_SHADER_CACHE_DIR=str(base/'cache/mesa'))
if a.mesa_build:
 stage=base/a.mesa_build/'stage/usr/lib';env.update(LD_LIBRARY_PATH=str(stage),LIBGL_DRIVERS_PATH=str(stage/'dri'),GBM_BACKENDS_PATH=str(stage/'gbm'))
if a.integrated is not None:env['MESA_KGSL_X11_INTEGRATED_RESOLVE']=a.integrated
if a.binding_diagnostic:env['MESA_KGSL_HDMI_BINDING_DIAGNOSTIC']='1'
if a.lazy_slots is not None:env['MESA_KGSL_HDMI_LAZY_SLOTS']=a.lazy_slots
if a.pacer_stats:env['MESA_KGSL_HDMI_PACER_STATS']='1'
if a.present_mode is not None:env['MESA_DRI3_PRESENT_MODE']=a.present_mode
if a.vblank_mode is not None:env['vblank_mode']=a.vblank_mode
if a.paced_margin_us is not None:env['MESA_KGSL_HDMI_PACED_MARGIN_US']=a.paced_margin_us
if a.paced_queue is not None:env['MESA_KGSL_HDMI_PACED_QUEUE']=a.paced_queue
if a.paced_lead is not None:env['MESA_KGSL_HDMI_PACED_LEAD']=a.paced_lead
args=['python3',str(pathlib.Path(__file__).with_name('hdmi-gears-workload.py')),str(base/a.name),'--binary',a.binary,'--seconds',a.seconds]
for flag in ['static','maximize','fullscreen','hide_cursor','pause_check','wm_resize']:
 if getattr(a,flag):args.append('--'+flag.replace('_','-'))
if a.captures:args.append('--captures')
if a.drm_captures:args.append('--drm-captures')
if a.framebuffer_helper is not None:env['HDMI_FRAMEBUFFER_HELPER']=a.framebuffer_helper
if a.framebuffer_period_ns is not None:env['HDMI_FRAMEBUFFER_PERIOD_NS']=str(a.framebuffer_period_ns)
if a.background_pixel is not None:args.extend(['--background-pixel',a.background_pixel])
r=subprocess.run(args,env=env,**({} if a.drm_captures else {'user':4000,'group':4000,'extra_groups':[]}),timeout=float(a.seconds)+90);raise SystemExit(r.returncode)
