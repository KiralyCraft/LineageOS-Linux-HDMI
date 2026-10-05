import ctypes as C,json,math,os,pathlib,subprocess,time,signal,select,struct
base=pathlib.Path(__file__).resolve().parent
e=None
for desktop_pid in subprocess.check_output(['pgrep','-x','openbox'],text=True).split():
 candidate=dict(x.split('=',1) for x in pathlib.Path('/proc/'+desktop_pid+'/environ').read_bytes().decode().split('\0') if '=' in x)
 if '/run/hdmi-los/' in candidate.get('XAUTHORITY',''):e=candidate;break
assert e,'No HDMI desktop environment'
def require_unlocked():
 for pid in subprocess.run(['pgrep','-x','xsecurelock'],text=True,capture_output=True).stdout.split():
  try:le=dict(s.split('=',1) for s in pathlib.Path('/proc/'+pid+'/environ').read_bytes().decode().split('\0') if '=' in s)
  except (FileNotFoundError,PermissionError):continue
  if le.get('DISPLAY')==e['DISPLAY']:raise RuntimeError('The desktop locked; visible benchmark stopped')
require_unlocked()
xorg_pids=subprocess.check_output(['pgrep','-x','Xorg'],text=True).split()
assert len(xorg_pids)==1,'Select the HDMI Xorg explicitly if multiple servers exist'
xorg_pid=xorg_pids[0]
fd=None;d=None
e['QT_LOGGING_RULES']='qt.qpa.backingstore.debug=false;qt.qpa.xcb.debug=false'
active=subprocess.check_output(['xdotool','getactivewindow'],env=e,text=True).strip()
log=open(base/'vblank-reference.log','w')
p=subprocess.Popen(['konsole','--separate','--builtin-profile','-p','Name=HDMI resize diagnostic','-e','sleep','240'],env=e,stdout=log,stderr=log,start_new_session=True)
r={'pid':p.pid,'phases':[],'note':'XSync request turnaround is not paint completion or physical display FPS.'}
try:
 deadline=time.monotonic()+25
 while time.monotonic()<deadline:
  ids=subprocess.run(['xdotool','search','--onlyvisible','--pid',str(p.pid)],env=e,text=True,capture_output=True).stdout.split()
  if ids:break
  time.sleep(.2)
 else:raise RuntimeError('No diagnostic window')
 w=int(ids[-1]);r['window']=w
 subprocess.run(['xdotool','windowmove',str(w),'1950','100','windowsize',str(w),'1400','1000'],env=e,check=True)
 subprocess.run(['xdotool','windowactivate',active],env=e)
 time.sleep(2)
 r['graphics_libraries']=sorted({l.split()[-1] for l in pathlib.Path(f'/proc/{p.pid}/maps').read_text().splitlines() if any(s in l for s in ['gallium','libEGL','libGLX','libQt6Widgets'])})
 x=C.CDLL('libX11.so.6');x.XOpenDisplay.argtypes=[C.c_char_p];x.XOpenDisplay.restype=C.c_void_p
 x.XMoveWindow.argtypes=[C.c_void_p,C.c_ulong,C.c_int,C.c_int];x.XResizeWindow.argtypes=[C.c_void_p,C.c_ulong,C.c_uint,C.c_uint];x.XSync.argtypes=[C.c_void_p,C.c_int];x.XCloseDisplay.argtypes=[C.c_void_p]
 os.environ.update({k:e[k] for k in ['DISPLAY','XAUTHORITY']});d=x.XOpenDisplay(e['DISPLAY'].encode());assert d
 def ticks():
  s=pathlib.Path('/proc/'+xorg_pid+'/stat').read_text().split(') ',1)[1].split();return int(s[11])+int(s[12])
 pathlib.Path(base/'Xorg-before.log').write_bytes(pathlib.Path('/run/hdmi-los/Xorg.1.log').read_bytes())
 drm=C.CDLL('libdrm.so.2',use_errno=True);drm.drmCrtcGetSequence.argtypes=[C.c_int,C.c_uint32,C.POINTER(C.c_uint64),C.POINTER(C.c_uint64)];fd=os.open('/dev/dri/card0',os.O_RDWR|os.O_CLOEXEC);queries=[];drm.drmCrtcQueueSequence.argtypes=[C.c_int,C.c_uint32,C.c_uint32,C.c_uint64,C.POINTER(C.c_uint64),C.c_uint64];hold=None;event=None
 for phase,period in [('move-before',1/60),('move-held-reference',1/60),('move-after',1/60)]:
  require_unlocked()
  x.XResizeWindow(d,w,1400,1000);x.XMoveWindow(d,w,1950,100);x.XSync(d,False);time.sleep(2)
  if phase=='move-held-reference':
   target=C.c_uint64();ret=drm.drmCrtcQueueSequence(fd,235,1,1200,C.byref(target),0x48444d49);hold={'at':time.monotonic(),'target':target.value,'ret':ret,'errno':C.get_errno() if ret else 0};r['hold']=hold;assert ret==0,hold;print('REFERENCE_QUEUED',json.dumps(hold),flush=True)
  pos=pathlib.Path('/run/hdmi-los/Xorg.1.log').stat().st_size;start=time.monotonic();t0=ticks();values=[]
  i=0
  while time.monotonic()-start<18:
   if i%30==0:require_unlocked()
   a=time.monotonic()
   if phase.startswith('resize'):x.XResizeWindow(d,w,1400+round(180*math.sin(i*math.pi/20)),1000+round(120*math.cos(i*math.pi/20)))
   if phase.startswith('move'):x.XMoveWindow(d,w,1950+round(180*math.sin(i*math.pi/60)),150+round(80*math.cos(i*math.pi/60)))
   x.XSync(d,False);values.append((time.monotonic()-a)*1000);time.sleep(max(0,period-(time.monotonic()-a)));i+=1
  seconds=time.monotonic()-start;movement_end=time.monotonic()
  if phase=='move-held-reference':
   if not select.select([fd],[],[],max(0,25-(time.monotonic()-hold['at'])))[0]:raise RuntimeError('Bounded vblank event did not complete')
   raw=os.read(fd,4096);events=[]
   for off in range(0,len(raw),32):
    kind,length,cookie,ns,seq=struct.unpack_from('=IIQqQ',raw,off);events.append({'type':kind,'length':length,'cookie':cookie,'timestamp_ns':ns,'sequence':seq,'received_at':time.monotonic()})
   r['events']=events;print('REFERENCE_RELEASED',json.dumps(events),flush=True)
  with open('/run/hdmi-los/Xorg.1.log') as f:f.seek(pos);delta=f.read()
  s=sorted(values);v={'phase':phase,'seconds':seconds,'start_monotonic':start,'end_monotonic':movement_end,'xorg_cpu_seconds':(ticks()-t0)/os.sysconf('SC_CLK_TCK'),'request_ms':values,'p50_ms':s[len(s)//2],'p95_ms':s[int(len(s)*.95)],'max_ms':max(s),'gbm_failures':delta.count('Failed to make'),'tearfree_failures':delta.count('TearFree flip failed')}
  r['phases'].append(v);print(json.dumps({k:v for k,v in v.items() if k!='request_ms'}),flush=True)
 pathlib.Path(base/'Xorg-after.log').write_bytes(pathlib.Path('/run/hdmi-los/Xorg.1.log').read_bytes())
 os.close(fd);fd=None;r['sequence_queries']=queries
 x.XCloseDisplay(d);d=None
finally:
 if fd is not None:os.close(fd)
 if d:x.XCloseDisplay(d)
 (base/'vblank-reference.json').write_text(json.dumps(r,indent=2))
 if p.poll() is None:
  os.killpg(p.pid,signal.SIGTERM)
  try:p.wait(timeout=5)
  except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL)
 log.close()
 subprocess.run(['xdotool','windowactivate',active],env=e,capture_output=True)
