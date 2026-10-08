import argparse,ctypes as C,json,pathlib,subprocess,time,os,sys
from xroot_capture import RootCapture
ap=argparse.ArgumentParser();ap.add_argument('output',type=pathlib.Path);ap.add_argument('--binary',default='/usr/bin/glxgears');ap.add_argument('--maximize',action='store_true');ap.add_argument('--fullscreen',action='store_true');ap.add_argument('--hide-cursor',action='store_true');ap.add_argument('--pause-check',action='store_true');ap.add_argument('--static',action='store_true');ap.add_argument('--captures',action='store_true');ap.add_argument('--drm-captures',action='store_true');ap.add_argument('--wm-resize',action='store_true');ap.add_argument('--background-pixel',type=lambda s:int(s,0));ap.add_argument('--seconds',type=float,default=30);a=ap.parse_args();a.output.mkdir();
V,I,U,L=C.c_void_p,C.c_int,C.c_uint,C.c_ulong;X=C.CDLL('libX11.so.6')
def bind(n,r,types):
 f=getattr(X,n);f.restype=r;f.argtypes=types;return f
d=bind('XOpenDisplay',V,[C.c_char_p])(None);assert d;root=bind('XDefaultRootWindow',L,[V])(d);screen=bind('XDefaultScreen',I,[V])(d)
sw=bind('XDisplayWidth',I,[V,I])(d,screen);sh=bind('XDisplayHeight',I,[V,I])(d,screen)
query=bind('XQueryTree',I,[V,L,C.POINTER(L),C.POINTER(L),C.POINTER(C.POINTER(L)),C.POINTER(U)]);fetch=bind('XFetchName',I,[V,L,C.POINTER(C.c_char_p)]);free=bind('XFree',I,[V]);sync=bind('XSync',I,[V,I]);resize=bind('XResizeWindow',I,[V,L,U,U]);name=bind('XStoreName',I,[V,L,C.c_char_p])
def windows():
 found={};todo=[(root,0)]
 while todo:
  w,depth=todo.pop();text=C.c_char_p()
  if fetch(d,w,C.byref(text)) and text:
   found[w]=text.value.decode(errors='replace');free(C.cast(text,V))
  rr,pp,kids,n=L(),L(),C.POINTER(L)(),U()
  if depth<4 and query(d,w,C.byref(rr),C.byref(pp),C.byref(kids),C.byref(n)):
   todo.extend((kids[i],depth+1) for i in range(n.value))
   if kids:free(C.cast(kids,V))
 return found
before=windows();env=os.environ.copy();env['HDMI_FRAME_TRACE']=str(a.output/'trace.bin')
if a.drm_captures:os.chown(a.output,4000,4000)
log=(a.output/'stdout.log').open('w');child=subprocess.Popen([a.binary,'-geometry','640x480']+(['-fullscreen'] if a.fullscreen else []),env=env,stdout=log,stderr=subprocess.STDOUT,**({'user':4000,'group':4000,'extra_groups':[]} if a.drm_captures else {}))
cap=RootCapture(d,X,root,sw,sh);record=[];keys=[];hidden=False;scanout=None;wm=None;wm_cleanup=None
if a.drm_captures:
 assert os.geteuid()==0
 from committed_framebuffer import Scanout
 scanout=Scanout()
class MessageData(C.Union):_fields_=[('l',L*5),('b',C.c_char*20)]
class Message(C.Structure):_fields_=[('type',I),('serial',L),('send_event',I),('display',V),('window',L),('message_type',L),('format',I),('data',MessageData)]
class Key(C.Structure):_fields_=[('type',I),('serial',L),('send_event',I),('display',V),('window',L),('root',L),('subwindow',L),('time',L),('x',I),('y',I),('x_root',I),('y_root',I),('state',U),('keycode',U),('same_screen',I)]
class Event(C.Union):_fields_=[('message',Message),('key',Key),('pad',L*24)]
send=bind('XSendEvent',I,[V,L,I,C.c_long,C.POINTER(Event)])
def key_a(window):
 atom=bind('XStringToKeysym',L,[C.c_char_p])(b'a');code=bind('XKeysymToKeycode',C.c_ubyte,[V,L])(d,atom)
 e=Event();e.key=Key(type=2,display=d,window=window,root=root,keycode=code,same_screen=1)
 assert send(d,window,0,0,C.byref(e));sync(d,0);keys.append(time.monotonic_ns())

try:
 deadline=time.monotonic()+8;window=None
 while time.monotonic()<deadline:
  new=[w for w,title in windows().items() if w not in before and 'glxgears' in title.lower()]
  if len(new)==1:window=new[0];break
  assert child.poll() is None;time.sleep(.05)
 assert window
 name(d,window,b'HDMI gears pacing / resize test');sync(d,0)
 if a.background_pixel is not None:
  assert 0<=a.background_pixel<=0xffffff
  bind('XSetWindowBackground',I,[V,L,L])(d,window,a.background_pixel);sync(d,0)
 if a.maximize:
  atom=bind('XInternAtom',L,[V,C.c_char_p,I]);e=Event();e.message=Message(type=33,display=d,window=window,message_type=atom(d,b'_NET_WM_STATE',0),format=32)
  e.message.data.l[:]=(1,atom(d,b'_NET_WM_STATE_MAXIMIZED_VERT',0),atom(d,b'_NET_WM_STATE_MAXIMIZED_HORZ',0),1,0)
  assert send(d,root,0,(1<<20)|(1<<19),C.byref(e));sync(d,0)
 if a.hide_cursor:
  F=C.CDLL('libXfixes.so.3');major,minor=I(5),I(0);F.XFixesQueryVersion.argtypes=[V,C.POINTER(I),C.POINTER(I)];assert F.XFixesQueryVersion(d,C.byref(major),C.byref(minor))
  F.XFixesHideCursor.argtypes=[V,L];F.XFixesShowCursor.argtypes=[V,L];F.XFixesHideCursor(d,root);sync(d,0);hidden=True
 time.sleep(.5)
 if a.wm_resize:
  assert not (a.static or a.maximize or a.fullscreen or a.pause_check)
  from wm_resize import WMResize
  wm=WMResize(d,X,root,screen,window,cap.bounds);wm_cleanup=wm;wm.start()
 start=time.monotonic();last=None;next_capture=start
 stop_time=None
 while time.monotonic()-start<a.seconds:
  t=time.monotonic();age=t-start
  if a.pause_check and age>=13 and len(keys)==0:key_a(window)
  if a.pause_check and age>=17 and len(keys)==1:key_a(window)
  if a.maximize or a.fullscreen:size=None
  elif a.static:size=(sw-20,sh-100)
  elif age<4:size=(640+int(age/4*640),480+int(age/4*480))
  elif age<8:size=(1280-int((age-4)/4*640),960-int((age-4)/4*480))
  elif age<12:size=(640,480) if int((age-8)*20)%2==0 else (1280,960)
  else:size=(1000,750) if a.pause_check and 14.5<=age<17 else (960,720)
  if size!=last:
   if wm:wm.move(*size)
   else:resize(d,window,*size);sync(d,0)
   last=size
   if wm and age>=12:wm.finish();wm=None
   if age>=12 and stop_time is None:stop_time=t
  row={'relative_seconds':age,'ns':time.monotonic_ns(),'requested':size}
  if (a.captures or a.drm_captures) and t>=next_capture:
   if a.drm_captures:
    scanout.align();frame=cap.frame(window);bounds=cap.bounds(frame);client=cap.bounds(window);x,y,w,h=bounds
    x1,y1=max(x,0),max(y,0);x2,y2=min(x+w,sw),min(y+h,sh)
    row['capture']=scanout.read(x1,y1,x2-x1,y2-y1,{'age':age,'requested':size,'frame_bounds':bounds,'client_bounds':client})
    row['capture']['geometry_stable']=bounds==cap.bounds(frame) and client==cap.bounds(window)
   else:row['capture']=cap.capture(window,str(len(record)),{'age':age,'requested':size})
   next_capture=t+.2
  record.append(row);time.sleep(max(0,.05-(time.monotonic()-t)))
finally:
 if wm_cleanup:wm_cleanup.close()
 if hidden:F.XFixesShowCursor(d,root);sync(d,0)
 child.terminate()
 try:code=child.wait(timeout=10)
 except subprocess.TimeoutExpired:child.kill();code=child.wait(timeout=5)
 log.close()
 if cap.images:cap.save(a.output/'frames')
 if scanout:
  scanout.close();scanout.save(a.output/'scanout')
 (a.output/'result.json').write_text(json.dumps({'code':code,'binary':a.binary,'wm_resize':a.wm_resize,'background_pixel':a.background_pixel,'screen':[sw,sh],'static':a.static,'maximize':a.maximize,'fullscreen':a.fullscreen,'cursor_hidden':a.hide_cursor,'pause_key_ns':keys,'resize_stopped_ns':int(stop_time*1e9) if stop_time else None,'records':record,'physical_scanout_tested':False},indent=2)+'\n')
 bind('XCloseDisplay',I,[V])(d)
print(json.dumps({'records':len(record),'captured':len(scanout.records) if scanout else len(cap.records),'code':code}),flush=True)
