"""Real X11 EGL/GLES3 buffer age and preserved partial repaint test.

No client readback or finish is used before presentation. Owns only its window;
X root captures validate settled image contents, not physical display cadence.
"""
import collections
import argparse,ctypes as C,importlib.util,json,pathlib,time,sys
from xroot_capture import RootCapture
checks_spec=importlib.util.spec_from_file_location('checks',pathlib.Path(__file__).with_name('resize-pipeline.py'));checks=importlib.util.module_from_spec(checks_spec);checks_spec.loader.exec_module(checks)
screenshot_samples=checks.screenshot_samples

from ctypes import c_void_p as V,c_int as I,c_uint as U,c_ulong as L
spec=importlib.util.spec_from_file_location('p',pathlib.Path(__file__).with_name('leased-pattern.py'));p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('output',type=pathlib.Path);ap.add_argument('--preserved',action='store_true');ap.add_argument('--burst',action='store_true');ap.add_argument('--no-age-query',action='store_true');a=ap.parse_args()
if a.no_age_query and not a.preserved:ap.error('--no-age-query requires --preserved')
a.output.mkdir()
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
geteglerr=bind(E,'eglGetError',U,[])
getproc=bind(E,'eglGetProcAddress',V,[C.c_char_p]);f=getproc(b'eglSwapBuffersWithDamageKHR');assert f;damageswap=C.CFUNCTYPE(U,V,V,C.POINTER(I),I)(f)
query_surface=bind(E,'eglQuerySurface',U,[V,V,I,C.POINTER(I)]);surface_attrib=bind(E,'eglSurfaceAttrib',U,[V,V,I,I]);
if a.preserved:assert surface_attrib(ed,surface,0x3093,0x3094),hex(geteglerr())
cap=RootCapture(d,X,root,sw,sh);rows=[];result={'renderer':renderer,'egl':[major.value,minor.value],'depth':vi.contents.depth,'with_damage':True,'physical_scanout_tested':False,'records':rows,'preserved':a.preserved,'partial_damage':True,'no_age_query':a.no_age_query}

scene=[[32+(i*f+19)%192 for f in [37,71,29]] for i in range(16)];history=[];last_size=None
try:
 sizes=[(640,480)]*8+[(641,481),(768,512),(769,513),(895,639),(640,480),(769,513)]+[(800,600)]*10
 if a.burst:sizes=[(800,600)]*240
 for serial,(width,height) in enumerate(sizes,1):
  if not a.burst or serial==1:
   resize(d,win,width,height);sync(d,0);deadline=time.monotonic()+2
   while cap.bounds(win)[2:]!=[width,height] and time.monotonic()<deadline:time.sleep(.005)
   assert cap.bounds(win)[2:]==[width,height]
  while pending(d):next_event(d,C.byref(event))
  age=I(1 if a.preserved else 0)
  if not a.no_age_query:assert query_surface(ed,surface,0x313d,C.byref(age)),hex(geteglerr())
  changed=(serial*7)%16;scene[changed]=[32+(serial*f+11)%192 for f in [37,71,29]];history.append(changed)
  full=serial==1 or (width,height)!=last_size or (not a.preserved and age.value==0)
  repair=set(range(16)) if full else ({changed} if a.preserved else set(history[max(0,serial-age.value):serial]))
  viewport(0,0,width,height);enable(0xc11);rectangles=[]
  for index in sorted(repair):
   x=index%4;y=index//4;x0=x*width//4;x1=(x+1)*width//4;y0=y*height//4;y1=(y+1)*height//4;scissor(x0,y0,x1-x0,y1-y0);clear_color(*(c/255 for c in scene[index]),1);clear(0x4000);rectangles.extend([x0,y0,x1-x0,y1-y0])
  disable(0xc11);start=time.monotonic_ns();ok=damageswap(ed,surface,(I*len(rectangles))(*rectangles),len(rectangles)//4);swapms=(time.monotonic_ns()-start)/1e6;eglerr=geteglerr();glerr=geterr()
  if a.burst and serial%30!=0:
   rows.append(dict(serial=serial,geometry=[width,height],buffer_age=age.value,swap_ok=int(ok),egl_error=hex(eglerr),gl_error=glerr,full_repaint=full,passed=None,swap_ms=swapms));last_size=(width,height);continue
  time.sleep(.2)
  rec=cap.capture(win,f'age-{serial}');cx,cy,cw,ch=rec['client_bounds'];rx,ry,_,_=rec['root_crop'];points=[(cx-rx+int(cw*(x+.5)/4),cy-ry+int(ch*(y+.5)/4)) for y in range(4) for x in range(4)];pixels=screenshot_samples(cap.images[-1][1],points);expected=[scene[(3-y)*4+x][:] for y in range(4) for x in range(4)];matches=[all(abs(a-b)<=1 for a,b in zip(c,e)) for c,e in zip(pixels,expected)];passed=bool(ok and eglerr==0x3000 and glerr==0 and rec['geometry_stable'] and all(matches));rows.append(dict(serial=serial,geometry=[width,height],buffer_age=age.value,full_repaint=full,repaired=sorted(repair),swap_ok=int(ok),egl_error=hex(eglerr),gl_error=glerr,swap_ms=swapms,expected=expected,pixels=pixels,bad_tiles=[i for i,v in enumerate(matches) if not v],passed=passed));last_size=(width,height)
finally:
 make(ed,None,None,None);bind(E,'eglDestroySurface',U,[V,V])(ed,surface);bind(E,'eglDestroyContext',U,[V,V])(ed,ctx);bind(E,'eglTerminate',U,[V])(ed);bind(X,'XDestroyWindow',I,[V,L])(d,win);bind(X,'XFreeColormap',I,[V,L])(d,cmap);bind(X,'XFree',I,[V])(C.cast(vi,V));bind(X,'XCloseDisplay',I,[V])(d);cap.save(a.output/'frames');(a.output/'result.json').write_text(json.dumps(result,indent=2)+'\n')
checked=[r for r in rows if r['passed'] is not None];passed=sum(r['passed'] for r in checked);failed_swaps=[r['serial'] for r in rows if not r['swap_ok'] or r['egl_error']!='0x3000' or r['gl_error']!=0];print(json.dumps(dict(frames=len(rows),checked=len(checked),passed=passed,preserved=a.preserved,ages=dict(collections.Counter(r['buffer_age'] for r in rows)),failed_swaps=failed_swaps,bad=[(r['serial'],r['bad_tiles']) for r in rows if r['passed'] is False])),flush=True);raise SystemExit(0 if passed==len(checked) and not failed_swaps else 1)
