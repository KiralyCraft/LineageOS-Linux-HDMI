import argparse,json,os,pathlib,shutil,subprocess,time,signal
ap=argparse.ArgumentParser();ap.add_argument('--mesa-build');ap.add_argument('--paced-queue');ap.add_argument('--paced-lead');ap.add_argument('name');ap.add_argument('--interval',default='0');ap.add_argument('--cap',default='333');ap.add_argument('--fullscreen',default='0');ap.add_argument('--width',default='1920');ap.add_argument('--height',default='1080');ap.add_argument('--seconds',type=int,default=75);a=ap.parse_args()
base=pathlib.Path('/tmp/hdmi-app-pacing-20261008');out=base/a.name;assert not out.exists();out.mkdir();home=out/'home';(home/'baseq3/demos').mkdir(parents=True)
shutil.copyfile('/home/kiraly/.q3a/baseq3/q3config.cfg',home/'baseq3/q3config.cfg');shutil.copyfile('/home/kiraly/.q3a/baseq3/demos/demo0000.dm_71',home/'baseq3/demos/demo0000.dm_71')
pids=[r.split(None,1)[0] for r in subprocess.check_output(['ps','-eo','pid=,args='],text=True).splitlines() if len(r.split(None,1))==2 and r.split(None,1)[1].startswith('/usr/bin/lxsession -s LXDE')];assert len(pids)==1
env=dict(s.decode().split('=',1) for s in pathlib.Path('/proc/'+pids[0]+'/environ').read_bytes().split(b'\0') if b'=' in s)
auth=out/'Xauthority';shutil.copyfile(env['XAUTHORITY'],auth);auth.chmod(0o600)
for p in [out,*out.rglob('*')]:os.chown(p,4000,4000)
env.update(XAUTHORITY=str(auth),LD_PRELOAD=str(base/'frame-pacing-probe.so'),HDMI_FRAME_TRACE=str(out/'trace.bin'),MESA_KGSL_X11_BRIDGE_STATS='1',XDG_CACHE_HOME=str(base/'cache'))
if a.mesa_build:
 stage=base/a.mesa_build/'stage/usr/lib';assert stage.is_dir()
 env.update(LD_LIBRARY_PATH=str(stage),LIBGL_DRIVERS_PATH=str(stage/'dri'),GBM_BACKENDS_PATH=str(stage/'gbm'))
if a.paced_queue is not None:env['MESA_KGSL_HDMI_PACED_QUEUE']=a.paced_queue
if a.paced_lead is not None:env['MESA_KGSL_HDMI_PACED_LEAD']=a.paced_lead
args=['/home/kiraly/Games/ioQuake3_New/ioquake3']
for k,v in [('fs_homepath',str(home)),('r_mode','-1'),('r_customwidth',a.width),('r_customheight',a.height),('r_fullscreen',a.fullscreen),('r_swapInterval',a.interval),('com_maxfps',a.cap),('cl_renderer','opengl2'),('s_initsound','0'),('cl_motd','0'),('net_enabled','0'),('com_introplayed','1'),('timedemo','0'),('nextdemo','quit')]:args+=['+set',k,v]
args+=['+demo','demo0000']
start=time.monotonic()
with (out/'stdout.log').open('w') as log:
 child=subprocess.Popen(args,cwd='/home/kiraly/Games/ioQuake3_New',env=env,user=4000,group=4000,extra_groups=[],stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
 try:code=child.wait(timeout=a.seconds);timed_out=False
 except subprocess.TimeoutExpired:timed_out=True;child.terminate();code=child.wait(timeout=10)
result=dict(mesa_build=a.mesa_build,paced_queue=a.paced_queue,paced_lead=a.paced_lead,name=a.name,returncode=code,timed_out=timed_out,seconds=time.monotonic()-start,interval=a.interval,cap=a.cap,fullscreen=a.fullscreen,geometry=[int(a.width),int(a.height)],probe='memory buffered; no Present override',source_demo='/home/kiraly/.q3a/baseq3/demos/demo0000.dm_71',original_config_changed=False,physical_scanout_tested=False)
(out/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result),flush=True)
for line in (out/'stdout.log').read_text(errors='replace').splitlines():
 if any(k in line for k in ['GL_RENDERER','Finished demo','timedemo','HDMI pipeline submitted=','HDMI resize generations=','ERROR:','Error:','fatal']):print(line)
raise SystemExit(code if not timed_out else 0)
