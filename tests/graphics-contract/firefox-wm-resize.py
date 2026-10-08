#!/usr/bin/env python3
"""Resize an isolated hardware Firefox diagnostic window and capture output.

Launch the actual Firefox ELF with --no-remote --profile /tmp/<private-profile>,
--marionette and --remote-allow-system-access, using the inherited HDMI Mesa
libraries and this directory's firefox-webgl.html. The profile must select a
private Marionette port (default 2829). This script verifies its process and
window ownership, restores input, and leaves the browser running for the caller
to close. Committed framebuffer captures are software observations, not optical
scanout proof. Output must reside in RAM-backed /tmp or /dev/shm.
"""
import argparse,math,ctypes as C,json,pathlib,subprocess,time,os,sys
from xroot_capture import RootCapture
from committed_framebuffer import Scanout
from wm_resize import WMResize
from marionette_client import Marionette
ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('output',type=pathlib.Path);ap.add_argument('--browser-pid',type=int,required=True);ap.add_argument('--method',choices=['wm','direct'],default='wm');ap.add_argument('--seconds',type=float,default=15);ap.add_argument('--circle',action='store_true');ap.add_argument('--port',type=int,default=2829);args=ap.parse_args()
out=args.output.resolve();assert any(out.is_relative_to(pathlib.Path(p)) for p in ['/tmp','/dev/shm']),'Use RAM for output'
assert 0<args.seconds<=60 and os.geteuid()==0
command=pathlib.Path('/proc/'+str(args.browser_pid)+'/cmdline').read_bytes().split(b'\0');assert b'--no-remote' in command and b'--profile' in command
profile=pathlib.Path(command[command.index(b'--profile')+1].decode()).resolve();assert profile.is_relative_to(pathlib.Path('/tmp')),'Only an isolated RAM browser profile is allowed'
out.mkdir();uid=pathlib.Path('/proc/'+str(args.browser_pid)).stat().st_uid;os.chown(out,uid,uid)
assert os.environ.get('DISPLAY') and os.environ.get('XAUTHORITY'),'Inherit the live desktop display and a private authority file'
V,I,U,L=C.c_void_p,C.c_int,C.c_uint,C.c_ulong;X=C.CDLL('libX11.so.6')
def bind(n,r,t):
 f=getattr(X,n);f.restype,f.argtypes=r,t;return f
d=bind('XOpenDisplay',V,[C.c_char_p])(None);assert d;root=bind('XDefaultRootWindow',L,[V])(d);screen=bind('XDefaultScreen',I,[V])(d)
sw=bind('XDisplayWidth',I,[V,I])(d,screen);sh=bind('XDisplayHeight',I,[V,I])(d,screen);sync=bind('XSync',I,[V,I]);resize=bind('XResizeWindow',I,[V,L,U,U])
ids=[int(x) for x in subprocess.check_output(['xdotool','search','--name','HDMI WM resize WebGL diagnostic'],env=os.environ,text=True).splitlines()];assert len(ids)==1,ids;window=ids[0]
props=subprocess.check_output(['xprop','-id',hex(window),'WM_PROTOCOLS','_NET_WM_SYNC_REQUEST_COUNTER','_NET_WM_PID'],env=os.environ,text=True);assert '_NET_WM_SYNC_REQUEST' in props;assert any(line.startswith('_NET_WM_PID(') and line.split('=')[-1].strip()==str(args.browser_pid) for line in props.splitlines()),'The diagnostic window must belong to the isolated browser'
cap=RootCapture(d,X,root,sw,sh);
rr,pp,kids,n=L(),L(),C.POINTER(L)(),U();assert cap.query(d,window,C.byref(rr),C.byref(pp),C.byref(kids),C.byref(n));children=[kids[i] for i in range(n.value)];cap.free(C.cast(kids,V));child=max(children,key=lambda c:cap.bounds(c)[2]*cap.bounds(c)[3]);
scanout=Scanout();m=Marionette(args.port);m.call('WebDriver:NewSession',{'capabilities':{'alwaysMatch':{}}})
wm=None;records=[]
try:
 resize(d,window,640,480);sync(d,0)
 deadline=time.monotonic()+3
 while cap.bounds(window)[2:]!=[640,480] and time.monotonic()<deadline:time.sleep(.01)
 assert cap.bounds(window)[2:]==[640,480],cap.bounds(window)
 time.sleep(.4)
 before=m.call('WebDriver:ExecuteScript',{'script':'window.framesLog=[];return {time:performance.now(),inner:[innerWidth,innerHeight],frame:frameNumber};','args':[],'newSandbox':False,'sandbox':None})['value']
 if args.method=='wm':wm=WMResize(d,X,root,screen,window,cap.bounds);wm.start()
 start=time.monotonic();next_capture=start;last=None;drag_finished=False
 while time.monotonic()-start<args.seconds:
  now=time.monotonic();age=now-start
  if args.circle and age<12:
   size=[960+int(320*math.cos(age*math.pi/2)),720+int(240*math.sin(age*math.pi/2))]
  elif age<4:size=[640+int(age/4*640),480+int(age/4*480)]
  elif age<8:size=[1280-int((age-4)/4*640),960-int((age-4)/4*480)]
  elif age<12:size=[640,480] if int((age-8)*20)%2==0 else [1280,960]
  else:size=[960,720]
  if size!=last:
   if wm and not drag_finished:request=wm.move(*size)
   else:
    t=time.monotonic_ns();resize(d,window,*size);sync(d,0);request=dict(ns=t,requested=size,actual=cap.bounds(window),request_ms=(time.monotonic_ns()-t)/1e6)
   last=size
  else:request=None
  if wm and age>=12 and not drag_finished:wm.finish();drag_finished=True
  row=dict(age=age,ns=time.monotonic_ns(),requested=size,actual=cap.bounds(window),child_actual=cap.bounds(child),request=request)
  if now>=next_capture:
   scanout.align();frame=cap.frame(window);bounds=cap.bounds(frame);client=cap.bounds(window);x,y,w,h=bounds;x1,y1=max(x,0),max(y,0);x2,y2=min(x+w,sw),min(y+h,sh)
   row['capture']=scanout.read(x1,y1,x2-x1,y2-y1,dict(age=age,requested=size,frame_bounds=bounds,client_bounds=client,egl_child_bounds=cap.bounds(child)));row['capture']['geometry_stable']=bounds==cap.bounds(frame) and client==cap.bounds(window);next_capture=now+.2
  records.append(row);time.sleep(max(0,.05-(time.monotonic()-now)))
 after=m.call('WebDriver:ExecuteScript',{'script':'return {time:performance.now(),inner:[innerWidth,innerHeight],frame:frameNumber,frames:framesLog};','args':[],'newSandbox':False,'sandbox':None})['value']
 (out/'result.json').write_text(json.dumps(dict(method=args.method,circle=args.circle,browser_pid=args.browser_pid,screen=[sw,sh],window=window,egl_child=child,properties=props,records=records,before=before,after=after,physical_optical_capture=False,capture_helper=os.environ.get('HDMI_FRAMEBUFFER_HELPER')),indent=2)+'\n')
 print(json.dumps(dict(method=args.method,request_batches=len(records),captures=len(scanout.records),raf_frames=len(after['frames']))),flush=True)
finally:
 if wm:wm.close()
 m.close();scanout.close();scanout.save(out/'scanout');bind('XCloseDisplay',I,[V])(d)
