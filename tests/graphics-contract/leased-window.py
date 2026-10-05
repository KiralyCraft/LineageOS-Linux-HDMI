#!/usr/bin/env python3
"""Visible GLX presentation/resize smoke test on an already active leased X.

Uses the inherited client environment. Never arms, modesets, stops a lease, or
closes another application's window. Swap timing is not physical display FPS.
"""
import ctypes as C
import json, os, pathlib, sys, time

output=pathlib.Path(sys.argv[1]);assert not output.exists()
V=C.c_void_p;I=C.c_int;U=C.c_uint;L=C.c_ulong
X=C.CDLL('libX11.so.6');G=C.CDLL('libGL.so.1')
def bind(lib,name,ret,args):
 f=getattr(lib,name);f.restype=ret;f.argtypes=args;return f
class Visual(C.Structure):
 _fields_=[('visual',V),('id',L),('screen',I),('depth',I),('klass',I),('red',L),('green',L),('blue',L),('colormap_size',I),('bits',I)]
class Attributes(C.Structure):
 _fields_=[('background_pixmap',L),('background_pixel',L),('border_pixmap',L),('border_pixel',L),('bit_gravity',I),('win_gravity',I),('backing_store',I),('backing_planes',L),('backing_pixel',L),('save_under',I),('event_mask',C.c_long),('do_not_propagate',C.c_long),('override_redirect',I),('colormap',L),('cursor',L)]
d=bind(X,'XOpenDisplay',V,[C.c_char_p])(None);assert d,'Inherited display unavailable'
screen=bind(X,'XDefaultScreen',I,[V])(d)
root=bind(X,'XRootWindow',L,[V,I])(d,screen)
choose=bind(G,'glXChooseVisual',C.POINTER(Visual),[V,I,C.POINTER(I)])
visual=choose(d,screen,(I*9)(4,5,8,8,9,8,10,8,0));assert visual
colormap=bind(X,'XCreateColormap',L,[V,L,V,I])(d,root,visual.contents.visual,0)
attrs=Attributes(colormap=colormap,event_mask=(1<<15)|(1<<17))
window=bind(X,'XCreateWindow',L,[V,L,I,I,U,U,U,I,U,V,L,C.POINTER(Attributes)])(d,root,120,150,800,600,0,visual.contents.depth,1,visual.contents.visual,(1<<13)|(1<<11),C.byref(attrs));assert window
bind(X,'XStoreName',I,[V,L,C.c_char_p])(d,window,b'BCDEF automated GPU presentation and resize test')
context=bind(G,'glXCreateContext',V,[V,C.POINTER(Visual),V,I])(d,visual,None,1);assert context
make=bind(G,'glXMakeCurrent',I,[V,L,V]);assert make(d,window,context)
bind(X,'XMapWindow',I,[V,L])(d,window)
sync=bind(X,'XSync',I,[V,I]);sync(d,0)
renderer=bind(G,'glGetString',C.c_char_p,[U])(0x1f01).decode();assert 'FD740' in renderer,renderer
assert bind(G,'glXIsDirect',I,[V,V])(d,context)
loaded=sorted({line.split()[-1] for line in pathlib.Path('/proc/self/maps').read_text().splitlines() if any(s in line for s in ['libgallium','libGLX_mesa','libEGL_mesa'])})
assert loaded and all(not p.startswith('/usr/lib/') for p in loaded),loaded
clear_color=bind(G,'glClearColor',None,[C.c_float]*4);clear=bind(G,'glClear',None,[U]);viewport=bind(G,'glViewport',None,[I,I,I,I])
swap=bind(G,'glXSwapBuffers',None,[V,L]);move=bind(X,'XMoveResizeWindow',I,[V,L,I,I,U,U]);error=bind(G,'glGetError',U,[])
result={'renderer':renderer,'loaded':loaded,'window':int(window),'note':'Swap-call timings do not establish physical display cadence or latency.','phases':[]}
try:
 for phase in ['fixed','resize-move','fixed-after']:
  start=time.monotonic();samples=[];frames=0;sizes=set()
  while time.monotonic()-start<5:
   if phase=='resize-move':
    width,height=[(800,600),(1001,701),(1279,719)][(frames//10)%3]
    if frames%10==0:move(d,window,120+(frames%30)*70,150+(frames%20)*20,width,height);sync(d,0)
   else:width,height=800,600
   if frames==0:move(d,window,120,150,width,height);sync(d,0)
   sizes.add((width,height));viewport(0,0,width,height)
   clear_color((frames%60)/60,0.2 if phase=='resize-move' else 0.7,0.35,1);clear(0x4000)
   before=time.monotonic();swap(d,window);samples.append((time.monotonic()-before)*1000)
   assert error()==0,(phase,frames)
   frames+=1;time.sleep(max(0,1/30-(time.monotonic()-before)))
  ordered=sorted(samples)
  entry={'phase':phase,'submitted_frames':frames,'seconds':time.monotonic()-start,'sizes':sorted(sizes),'swap_p50_ms':ordered[len(ordered)//2],'swap_p95_ms':ordered[int(len(ordered)*.95)],'swap_max_ms':ordered[-1]}
  result['phases'].append(entry);print(json.dumps(entry),flush=True)
finally:
 bind(G,'glFinish',None,[])()
 make(d,0,None)
 bind(G,'glXDestroyContext',None,[V,V])(d,context)
 bind(X,'XDestroyWindow',I,[V,L])(d,window)
 bind(X,'XFreeColormap',I,[V,L])(d,colormap)
 bind(X,'XFree',I,[V])(C.cast(visual,V))
 bind(X,'XCloseDisplay',I,[V])(d)
 output.write_text(json.dumps(result,indent=2)+'\n')
