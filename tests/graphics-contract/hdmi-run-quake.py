import argparse,ctypes as C,json,os,pathlib,shutil,subprocess,time,signal
ap=argparse.ArgumentParser();ap.add_argument('--refresh',type=int,default=0);ap.add_argument('--pacer-stats',action='store_true');ap.add_argument('--present-mode',choices=['unpaced','paced']);ap.add_argument('--paced-margin-us');ap.add_argument('--mesa-build');ap.add_argument('--paced-queue');ap.add_argument('--paced-lead');ap.add_argument('name');ap.add_argument('--interval',default='0');ap.add_argument('--cap',default='333');ap.add_argument('--fullscreen',default='0');ap.add_argument('--width',default='1920');ap.add_argument('--height',default='1080');ap.add_argument('--seconds',type=int,default=75);ap.add_argument('--maximize',action='store_true');ap.add_argument('--noborder',choices=['0','1'],default='0');ap.add_argument('--settle-seconds',type=float,default=0);a=ap.parse_args()
assert not a.maximize or a.fullscreen=='0','Maximization requires a windowed game'
base=pathlib.Path('/tmp/hdmi-app-pacing-20261008');out=base/a.name;assert not out.exists();out.mkdir();home=out/'home';(home/'baseq3/demos').mkdir(parents=True)
shutil.copyfile('/home/kiraly/.q3a/baseq3/q3config.cfg',home/'baseq3/q3config.cfg');shutil.copyfile('/home/kiraly/.q3a/baseq3/demos/demo0000.dm_71',home/'baseq3/demos/demo0000.dm_71')
pids=[r.split(None,1)[0] for r in subprocess.check_output(['ps','-eo','pid=,args='],text=True).splitlines() if len(r.split(None,1))==2 and r.split(None,1)[1].startswith('/usr/bin/lxsession -s LXDE')];assert len(pids)==1
env=dict(s.decode().split('=',1) for s in pathlib.Path('/proc/'+pids[0]+'/environ').read_bytes().split(b'\0') if b'=' in s)
auth=out/'Xauthority';shutil.copyfile(env['XAUTHORITY'],auth);auth.chmod(0o600)
for p in [out,*out.rglob('*')]:os.chown(p,4000,4000)
env.update(XAUTHORITY=str(auth),LD_PRELOAD=str(base/'frame-pacing-probe.so'),HDMI_FRAME_TRACE=str(out/'trace.bin'),MESA_KGSL_X11_BRIDGE_STATS='1',XDG_CACHE_HOME=str(base/'cache'),MESA_SHADER_CACHE_DIR=str(base/'cache/mesa'))
if a.mesa_build:
 stage=base/a.mesa_build/'stage/usr/lib';assert stage.is_dir()
 env.update(LD_LIBRARY_PATH=str(stage),LIBGL_DRIVERS_PATH=str(stage/'dri'),GBM_BACKENDS_PATH=str(stage/'gbm'))
if a.pacer_stats:env['MESA_KGSL_HDMI_PACER_STATS']='1'
if a.present_mode is not None:env['MESA_DRI3_PRESENT_MODE']=a.present_mode
if a.paced_margin_us is not None:env['MESA_KGSL_HDMI_PACED_MARGIN_US']=a.paced_margin_us
if a.paced_queue is not None:env['MESA_KGSL_HDMI_PACED_QUEUE']=a.paced_queue
if a.paced_lead is not None:env['MESA_KGSL_HDMI_PACED_LEAD']=a.paced_lead
args=['/home/kiraly/Games/ioQuake3_New/ioquake3']
for k,v in [('fs_homepath',str(home)),('r_mode','-1'),('r_customwidth',a.width),('r_customheight',a.height),('r_fullscreen',a.fullscreen),('r_displayRefresh',str(a.refresh)),('r_noborder',a.noborder),('r_swapInterval',a.interval),('com_maxfps',a.cap),('cl_renderer','opengl2'),('s_initsound','0'),('cl_motd','0'),('net_enabled','0'),('com_introplayed','1'),('timedemo','0'),('nextdemo','quit')]:args+=['+set',k,v]
args+=['+demo','demo0000']
if a.maximize:args[1:1]=['+set','r_allowResize','1']
def maximize_owned_window(pid):
 deadline=time.monotonic()+8
 while time.monotonic()<deadline:
  found=subprocess.run(['xdotool','search','--onlyvisible','--pid',str(pid)],env=env,capture_output=True,text=True)
  ids=[int(v) for v in found.stdout.split()] if found.returncode==0 else []
  if len(ids)==1:break
  time.sleep(.05)
 else:raise RuntimeError('No unique visible window for the owned game')
 window=ids[0];old_auth=os.environ.get('XAUTHORITY');os.environ['XAUTHORITY']=str(auth)
 from wm_resize import Message,Event
 from xroot_capture import RootCapture
 X=C.CDLL('libX11.so.6');V,I,L=C.c_void_p,C.c_int,C.c_ulong
 def bind(name,ret,types):
  f=getattr(X,name);f.restype,f.argtypes=ret,types;return f
 display=bind('XOpenDisplay',V,[C.c_char_p])(env['DISPLAY'].encode());assert display
 try:
  root=bind('XDefaultRootWindow',L,[V])(display);screen=bind('XDefaultScreen',I,[V])(display)
  sw=bind('XDisplayWidth',I,[V,I])(display,screen);sh=bind('XDisplayHeight',I,[V,I])(display,screen)
  cap=RootCapture(display,X,root,sw,sh);atom=bind('XInternAtom',L,[V,C.c_char_p,I]);event=Event();event.message=Message(type=33,display=display,window=window,message_type=atom(display,b'_NET_WM_STATE',0),format=32)
  event.message.data.l[:]=(1,atom(display,b'_NET_WM_STATE_MAXIMIZED_VERT',0),atom(display,b'_NET_WM_STATE_MAXIMIZED_HORZ',0),1,0)
  assert bind('XSendEvent',I,[V,L,I,C.c_long,C.POINTER(Event)])(display,root,0,(1<<20)|(1<<19),C.byref(event));bind('XSync',I,[V,I])(display,0)
  time.sleep(.4);properties=subprocess.check_output(['xprop','-id',hex(window),'_NET_WM_STATE','_NET_WM_PID'],env=env,text=True)
  assert '_NET_WM_STATE_MAXIMIZED_VERT' in properties and '_NET_WM_STATE_MAXIMIZED_HORZ' in properties,properties
  return dict(window=window,client_bounds=cap.bounds(window),properties=properties,initial_render_size=[int(a.width),int(a.height)])
 finally:
  bind('XCloseDisplay',I,[V])(display)
  if old_auth is None:os.environ.pop('XAUTHORITY',None)
  else:os.environ['XAUTHORITY']=old_auth
def close_owned_window(pid):
 # SDL handles WM_DELETE_WINDOW in its normal event loop. SIGTERM can interrupt
 # a Mesa critical section and deadlock Quake's signal-handler shutdown.
 found=subprocess.run(['xdotool','search','--onlyvisible','--pid',str(pid)],env=env,capture_output=True,text=True)
 ids=[int(v) for v in found.stdout.split()] if found.returncode==0 else []
 if len(ids)!=1:raise RuntimeError('No unique owned game window to close')
 from wm_resize import Message,Event
 old_auth=os.environ.get('XAUTHORITY');os.environ['XAUTHORITY']=str(auth)
 X=C.CDLL('libX11.so.6');V,I,L=C.c_void_p,C.c_int,C.c_ulong
 def bind(name,ret,types):
  f=getattr(X,name);f.restype,f.argtypes=ret,types;return f
 display=bind('XOpenDisplay',V,[C.c_char_p])(env['DISPLAY'].encode())
 if not display:raise RuntimeError('Cannot connect to game display')
 try:
  atom=bind('XInternAtom',L,[V,C.c_char_p,I]);event=Event()
  event.message=Message(type=33,display=display,window=ids[0],message_type=atom(display,b'WM_PROTOCOLS',0),format=32)
  event.message.data.l[:]=(atom(display,b'WM_DELETE_WINDOW',0),0,0,0,0)
  assert bind('XSendEvent',I,[V,L,I,C.c_long,C.POINTER(Event)])(display,ids[0],0,0,C.byref(event))
  bind('XSync',I,[V,I])(display,0)
 finally:
  bind('XCloseDisplay',I,[V])(display)
  if old_auth is None:os.environ.pop('XAUTHORITY',None)
  else:os.environ['XAUTHORITY']=old_auth

start=time.monotonic()
maximized=None
with (out/'stdout.log').open('w') as log:
 child=subprocess.Popen(args,cwd='/home/kiraly/Games/ioQuake3_New',env=env,user=4000,group=4000,extra_groups=[],stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
 try:
  if a.settle_seconds:time.sleep(a.settle_seconds)
  if a.maximize:maximized=maximize_owned_window(child.pid)
  code=child.wait(timeout=a.seconds);timed_out=False
 except subprocess.TimeoutExpired:
  timed_out=True
  try:
   close_owned_window(child.pid)
   code=child.wait(timeout=10)
  except BaseException:
   child.kill();child.wait(timeout=10);raise
 except BaseException:
  child.terminate();child.wait(timeout=10);raise
result=dict(pacer_stats=a.pacer_stats,present_mode=a.present_mode,paced_margin_us=a.paced_margin_us,mesa_build=a.mesa_build,paced_queue=a.paced_queue,paced_lead=a.paced_lead,name=a.name,returncode=code,timed_out=timed_out,seconds=time.monotonic()-start,interval=a.interval,cap=a.cap,fullscreen=a.fullscreen,geometry=[int(a.width),int(a.height)],probe='memory buffered; no Present override',shutdown='WM_DELETE_WINDOW on timeout',source_demo='/home/kiraly/.q3a/baseq3/demos/demo0000.dm_71',original_config_changed=False,physical_scanout_tested=False)
result['refresh_requested']=a.refresh
result['maximize']=maximized
result['noborder']=a.noborder
result['settle_seconds']=a.settle_seconds
(out/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result),flush=True)
for line in (out/'stdout.log').read_text(errors='replace').splitlines():
 if any(k in line for k in ['GL_RENDERER','Finished demo','timedemo','HDMI pipeline submitted=','HDMI resize generations=','ERROR:','Error:','fatal']):print(line)
raise SystemExit(code)
