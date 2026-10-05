#!/usr/bin/env python3
import ctypes as C,json,math,os,pathlib,signal,subprocess,time
BASE=pathlib.Path(__file__).resolve().parent
env=None
for pid in subprocess.check_output(['pgrep','-x','openbox'],text=True).split():
 e=dict(x.split('=',1) for x in pathlib.Path('/proc/'+pid+'/environ').read_bytes().decode().split('\0') if '=' in x)
 if '/run/hdmi-los/' in e.get('XAUTHORITY',''):env=e;break
def require_unlocked():
 for pid in subprocess.run(['pgrep','-x','xsecurelock'],text=True,capture_output=True).stdout.split():
  try:raw=pathlib.Path('/proc/'+pid+'/environ').read_bytes().decode().split('\0');le=dict(v.split('=',1) for v in raw if '=' in v)
  except (FileNotFoundError,PermissionError):continue
  if le.get('DISPLAY')==env['DISPLAY']:raise RuntimeError('The desktop locked; visible benchmark stopped')
assert env,'No inherited HDMI desktop environment'
require_unlocked()
servers=subprocess.check_output(['pgrep','-x','Xorg'],text=True).split();assert len(servers)==1
server=servers[0];active=subprocess.check_output(['xdotool','getactivewindow'],env=env,text=True).strip()
logfile=pathlib.Path('/run/hdmi-los/Xorg.1.log');result={'phases':[],'windows':[],'note':'XSync acknowledges requests. Counts and timing do not directly measure physical display FPS.'}
procs=[];logs=[];display=None
try:
 for number in [1,2]:
  log=open(BASE/f'two-unlocked-{number}.log','w');logs.append(log)
  p=subprocess.Popen(['konsole','--separate','--builtin-profile','-p',f'Name=HDMI movement diagnostic {number}','-e','sleep','240'],env=env,stdout=log,stderr=log,start_new_session=True);procs.append(p)
  limit=time.monotonic()+25
  while time.monotonic()<limit:
   ids=subprocess.run(['xdotool','search','--onlyvisible','--pid',str(p.pid)],env=env,capture_output=True,text=True).stdout.split()
   if ids:break
   if p.poll() is not None:raise RuntimeError('Diagnostic Konsole exited')
   time.sleep(.2)
  else:raise RuntimeError('Diagnostic window unavailable')
  print('CANDIDATES',p.pid,ids,flush=True)
  for candidate in ids:print(subprocess.check_output(['xprop','-id',candidate,'WM_STATE','WM_CLASS'],env=env,text=True),flush=True)
  w=int(next(candidate for candidate in ids if 'window state' in subprocess.check_output(['xprop','-id',candidate,'WM_STATE'],env=env,text=True))); result['windows'].append({'pid':p.pid,'id':w,'width':900,'height':900,'base_x':1900 if number==1 else 2850,'base_y':200})
  subprocess.run(['xdotool','windowmove',str(w),str(result['windows'][-1]['base_x']),'200','windowsize',str(w),'900','900'],env=env,check=True)
 subprocess.run(['xdotool','windowactivate',active],env=env,capture_output=True)
 os.environ.update({k:env[k] for k in ['DISPLAY','XAUTHORITY']})
 x=C.CDLL('libX11.so.6');x.XOpenDisplay.argtypes=[C.c_char_p];x.XOpenDisplay.restype=C.c_void_p;x.XMoveWindow.argtypes=[C.c_void_p,C.c_ulong,C.c_int,C.c_int];x.XSync.argtypes=[C.c_void_p,C.c_int];x.XCloseDisplay.argtypes=[C.c_void_p]
 x.XDefaultRootWindow.argtypes=[C.c_void_p];x.XDefaultRootWindow.restype=C.c_ulong;x.XInternAtom.argtypes=[C.c_void_p,C.c_char_p,C.c_int];x.XInternAtom.restype=C.c_ulong;x.XSendEvent.argtypes=[C.c_void_p,C.c_ulong,C.c_int,C.c_long,C.c_void_p]
 x.XTranslateCoordinates.argtypes=[C.c_void_p,C.c_ulong,C.c_ulong,C.c_int,C.c_int,C.POINTER(C.c_int),C.POINTER(C.c_int),C.POINTER(C.c_ulong)]
 display=x.XOpenDisplay(env['DISPLAY'].encode());assert display
 root=x.XDefaultRootWindow(display)
 class MsgData(C.Union):_fields_=[('l',C.c_long*5),('b',C.c_char*20)]
 class Msg(C.Structure):_fields_=[('type',C.c_int),('serial',C.c_ulong),('send_event',C.c_int),('display',C.c_void_p),('window',C.c_ulong),('message_type',C.c_ulong),('format',C.c_int),('data',MsgData)]
 class Event(C.Union):_fields_=[('client',Msg),('pad',C.c_long*24)]
 for w in result['windows']:
  event=Event();msg=event.client;msg.type=33;msg.window=w['id'];msg.format=32;msg.message_type=x.XInternAtom(display,b'_NET_WM_STATE',False);msg.data.l[0]=1;msg.data.l[1]=x.XInternAtom(display,b'_NET_WM_STATE_ABOVE',False);msg.data.l[3]=1;x.XSendEvent(display,root,False,(1<<19)|(1<<20),C.byref(event))
 x.XSync(display,False);time.sleep(1)
 def position(w):
  px=C.c_int();py=C.c_int();child=C.c_ulong();x.XTranslateCoordinates(display,w,root,0,0,C.byref(px),C.byref(py),C.byref(child));return [px.value,py.value]
 def cpu():
  a=pathlib.Path('/proc/'+server+'/stat').read_text().split(') ',1)[1].split();return (int(a[11])+int(a[12]))/os.sysconf('SC_CLK_TCK')
 time.sleep(3);(BASE/'Xorg-unlocked-before.log').write_bytes(logfile.read_bytes())
 for phase,moving in [('one-before',1),('two',2),('one-after',1)]:
  for w in result['windows']:x.XMoveWindow(display,w['id'],w['base_x'],w['base_y'])
  x.XSync(display,False);time.sleep(2)
  require_unlocked()
  start=time.monotonic();cpu_start=cpu();pos=logfile.stat().st_size;requests=[];count=0;positions=[]
  while time.monotonic()-start<18:
   if count%30==0:require_unlocked()
   t=time.monotonic();dx=round(55*math.sin(count*math.pi/60));dy=round(90*math.cos(count*math.pi/60))
   for i,w in enumerate(result['windows'][:moving]):
    x.XMoveWindow(display,w['id'],w['base_x']+dx*(1 if i==0 else -1),w['base_y']+dy)
   x.XSync(display,False)
   if count%30==0:positions.append({'at':time.monotonic(),'points':[position(w['id']) for w in result['windows']]})
   requests.append((time.monotonic()-t)*1000);count+=1;time.sleep(max(0,1/60-(time.monotonic()-t)))
  end=time.monotonic();ordered=sorted(requests)
  with logfile.open() as f:f.seek(pos);delta=f.read()
  r={'phase':phase,'moving_windows':moving,'start_monotonic':start,'end_monotonic':end,'seconds':end-start,'requests':count,'requests_per_second':count/(end-start),'xorg_cpu_seconds':cpu()-cpu_start,'sync_p50_ms':ordered[len(ordered)//2],'sync_p95_ms':ordered[int(len(ordered)*.95)],'sync_max_ms':ordered[-1],'gbm_failures':delta.count('Failed to make'),'tearfree_failures':delta.count('TearFree flip failed'),'request_ms':requests,'positions':positions}
  result['phases'].append(r);print(json.dumps({k:v for k,v in r.items() if k not in ['request_ms','positions']}),flush=True);(BASE/'two-unlocked-move.json').write_text(json.dumps(result,indent=2))
 (BASE/'Xorg-unlocked-after.log').write_bytes(logfile.read_bytes())
finally:
 if display:x.XCloseDisplay(display)
 for p in procs:
  if p.poll() is None:
   os.killpg(p.pid,signal.SIGTERM)
   try:p.wait(timeout=3)
   except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()
 for log in logs:log.close()
 subprocess.run(['xdotool','windowactivate',active],env=env,capture_output=True)
 (BASE/'two-unlocked-move.json').write_text(json.dumps(result,indent=2))
