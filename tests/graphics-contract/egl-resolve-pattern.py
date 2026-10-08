"""Real X11 EGL/GLES3 swap ordering, resize and damage correctness test.

No client readback or finish is used before presentation. Owns only its window;
X root captures validate settled image contents, not physical display cadence.
"""
import argparse,ctypes as C,importlib.util,json,pathlib,time,sys
from xroot_capture import RootCapture
checks_spec=importlib.util.spec_from_file_location('resize_checks',pathlib.Path(__file__).with_name('resize-pipeline.py'))
checks=importlib.util.module_from_spec(checks_spec);checks_spec.loader.exec_module(checks)
screenshot_samples=checks.screenshot_samples

from ctypes import c_void_p as V,c_int as I,c_uint as U,c_ulong as L
spec=importlib.util.spec_from_file_location('p',pathlib.Path(__file__).with_name('leased-pattern.py'));p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
ap=argparse.ArgumentParser();ap.add_argument('output',type=pathlib.Path);ap.add_argument('--with-damage',action='store_true');a=ap.parse_args();a.output.mkdir()
X,E,G=C.CDLL('libX11.so.6'),C.CDLL('libEGL.so.1'),C.CDLL('libGLESv2.so.2')
def bind(lib,n,r,t):
 f=getattr(lib,n);f.restype,f.argtypes=r,t;return f
d=bind(X,'XOpenDisplay',V,[C.c_char_p])(None);assert d
screen=bind(X,'XDefaultScreen',I,[V])(d);root=bind(X,'XRootWindow',L,[V,I])(d,screen)
sw=bind(X,'XDisplayWidth',I,[V,I])(d,screen);sh=bind(X,'XDisplayHeight',I,[V,I])(d,screen)
ed=bind(E,'eglGetDisplay',V,[V])(d);major,minor=I(),I();assert bind(E,'eglInitialize',U,[V,C.POINTER(I),C.POINTER(I)])(ed,C.byref(major),C.byref(minor))
assert bind(E,'eglBindAPI',U,[U])(0x30a0)
attrs=(I*15)(0x3033,4,0x3040,0x40,0x3024,8,0x3023,8,0x3022,8,0x3021,8,0x3025,0,0x3038);config=V();count=I()
assert bind(E,'eglChooseConfig',U,[V,C.POINTER(I),C.POINTER(V),I,C.POINTER(I)])(ed,attrs,C.byref(config),1,C.byref(count)) and count.value
vid=I();assert bind(E,'eglGetConfigAttrib',U,[V,V,I,C.POINTER(I)])(ed,config,0x302e,C.byref(vid))
match=p.Visual();match.id=vid.value;found=I();vi=bind(X,'XGetVisualInfo',C.POINTER(p.Visual),[V,L,C.POINTER(p.Visual),C.POINTER(I)])(d,1,C.byref(match),C.byref(found));assert vi and found.value
cmap=bind(X,'XCreateColormap',L,[V,L,V,I])(d,root,vi.contents.visual,0);wa=p.Attributes(background_pixel=0xe511ad,colormap=cmap,event_mask=(1<<15)|(1<<17));win=bind(X,'XCreateWindow',L,[V,L,I,I,U,U,U,I,U,V,L,C.POINTER(p.Attributes)])(d,root,400,300,640,480,0,vi.contents.depth,1,vi.contents.visual,(1<<1)|(1<<13)|(1<<11),C.byref(wa));assert win
bind(X,'XStoreName',I,[V,L,C.c_char_p])(d,win,b'HDMI EGL GLES3 resolve ordering diagnostic');bind(X,'XMapRaised',I,[V,L])(d,win)
sync=bind(X,'XSync',I,[V,I]);pending=bind(X,'XPending',I,[V]);next_event=bind(X,'XNextEvent',I,[V,C.POINTER(p.Event)]);resize=bind(X,'XResizeWindow',I,[V,L,U,U]);sync(d,0)
event=p.Event();mapped=False;deadline=time.monotonic()+3
while not mapped and time.monotonic()<deadline:
 while pending(d):
  next_event(d,C.byref(event));mapped|=event.type==19 and event.padding[4]==win
 if not mapped:time.sleep(.01)
assert mapped
none=(I*1)(0x3038);ctxattrs=(I*3)(0x3098,3,0x3038)
surface=bind(E,'eglCreateWindowSurface',V,[V,V,L,C.POINTER(I)])(ed,config,win,none);ctx=bind(E,'eglCreateContext',V,[V,V,V,C.POINTER(I)])(ed,config,None,ctxattrs)
make=bind(E,'eglMakeCurrent',U,[V,V,V,V]);assert surface and ctx and make(ed,surface,surface,ctx)
renderer=bind(G,'glGetString',C.c_char_p,[U])(0x1f01).decode();assert 'FD740' in renderer,renderer
viewport=bind(G,'glViewport',None,[I]*4);clear_color=bind(G,'glClearColor',None,[C.c_float]*4);clear=bind(G,'glClear',None,[U]);scissor=bind(G,'glScissor',None,[I]*4);enable=bind(G,'glEnable',None,[U]);disable=bind(G,'glDisable',None,[U]);geterr=bind(G,'glGetError',U,[])
swap=bind(E,'eglSwapBuffers',U,[V,V]);geteglerr=bind(E,'eglGetError',U,[])
if a.with_damage:
 getproc=bind(E,'eglGetProcAddress',V,[C.c_char_p]);f=getproc(b'eglSwapBuffersWithDamageKHR');assert f;damageswap=C.CFUNCTYPE(U,V,V,C.POINTER(I),I)(f)
cap=RootCapture(d,X,root,sw,sh);rows=[];result={'renderer':renderer,'egl':[major.value,minor.value],'depth':vi.contents.depth,'with_damage':a.with_damage,'physical_scanout_tested':False,'records':rows}
try:
 for serial,(width,height) in enumerate([(639,479),(640,480),(641,481),(767,511),(768,512),(769,513),(895,639),(896,640),(897,641),(800,600),(641,479),(800,600)]*2,1):
  resize(d,win,width,height);sync(d,0);deadline=time.monotonic()+2
  while cap.bounds(win)[2:]!=[width,height] and time.monotonic()<deadline:time.sleep(.005)
  assert cap.bounds(win)[2:]==[width,height]
  while pending(d):next_event(d,C.byref(event))
  viewport(0,0,width,height);clear_color(229/255,17/255,173/255,1);clear(0x4000);enable(0xc11)
  colors=[]
  for q in range(4):
   rgb=[(serial*factor+41+q*41)%192+32 for factor in [37,71,29]];colors.append(rgb);x=q%2;y=q//2;scissor(x*(width//2),y*(height//2),width-width//2 if x else width//2,height-height//2 if y else height//2);clear_color(*(c/255 for c in rgb),1);clear(0x4000)
  disable(0xc11);begin=time.monotonic_ns();ok=damageswap(ed,surface,(I*4)(0,0,width,height),1) if a.with_damage else swap(ed,surface);swapms=(time.monotonic_ns()-begin)/1e6;eglerr=geteglerr();glerr=geterr()
  time.sleep(.2);rec=cap.capture(win,f'egl-{serial}');cx,cy,cw,ch=rec['client_bounds'];rx,ry,_,_=rec['root_crop'];locations=[(.25,.25),(.75,.25),(.25,.75),(.75,.75)];points=[(cx-rx+int(cw*x),cy-ry+int(ch*y)) for x,y in locations];pixels=screenshot_samples(cap.images[-1][1],points);expected=[colors[(1-int(y>=.5))*2+int(x>=.5)] for x,y in locations]
  passed=bool(ok and eglerr==0x3000 and glerr==0 and rec['geometry_stable'] and all(all(abs(a-b)<=1 for a,b in zip(c,e)) for c,e in zip(pixels,expected)));rows.append(dict(serial=serial,geometry=[width,height],swap_ok=int(ok),egl_error=hex(eglerr),gl_error=glerr,swap_ms=swapms,expected=expected,pixels=pixels,passed=passed))
finally:
 make(ed,None,None,None);bind(E,'eglDestroySurface',U,[V,V])(ed,surface);bind(E,'eglDestroyContext',U,[V,V])(ed,ctx);bind(E,'eglTerminate',U,[V])(ed);bind(X,'XDestroyWindow',I,[V,L])(d,win);bind(X,'XFreeColormap',I,[V,L])(d,cmap);bind(X,'XFree',I,[V])(C.cast(vi,V));bind(X,'XCloseDisplay',I,[V])(d);cap.save(a.output/'frames');(a.output/'result.json').write_text(json.dumps(result,indent=2)+'\n')
passed=sum(r['passed'] for r in rows);print(json.dumps(dict(frames=len(rows),passed=passed,swap_true=sum(r['swap_ok'] for r in rows),renderer=renderer,with_damage=a.with_damage)),flush=True);raise SystemExit(0 if passed==len(rows) else 1)
