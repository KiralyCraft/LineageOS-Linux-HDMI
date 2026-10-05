#!/usr/bin/env python3
"""Two-process KGSL visibility/lifetime test. No X connection or display commits.

The producer never maps or reads the shared image. Only the consumer reads its
own private copy after waiting for the producer's final native completion fence.
"""
import ctypes as C
import json, os, select, subprocess, sys, pathlib
I=C.c_int; U=C.c_uint; V=C.c_void_p; F=C.c_float
E=C.CDLL('libEGL.so.1'); G=C.CDLL(os.environ['LD_LIBRARY_PATH']+'/libgbm.so.1')
def bind(lib,name,ret,args):
 f=getattr(lib,name);f.restype=ret;f.argtypes=args;return f
address=bind(E,'eglGetProcAddress',V,[C.c_char_p])
def fn(name,ret,args):
 p=address(name.encode());assert p,name;return C.CFUNCTYPE(ret,*args)(p)
card=os.open('/dev/dri/card0',os.O_RDWR|os.O_CLOEXEC)
dev=bind(G,'gbm_create_device',V,[I])(card);assert dev
D=fn('eglGetPlatformDisplayEXT',V,[U,V,C.POINTER(I)])(0x31D7,dev,None);assert D
assert bind(E,'eglInitialize',U,[V,C.POINTER(I),C.POINTER(I)])(D,None,None)
assert bind(E,'eglBindAPI',U,[U])(0x30A2)
config=V();count=I()
assert bind(E,'eglChooseConfig',U,[V,C.POINTER(I),C.POINTER(V),I,C.POINTER(I)])(D,(I*5)(0x3033,0,0x3040,8,0x3038),C.byref(config),1,C.byref(count)) and count.value
ctx=bind(E,'eglCreateContext',V,[V,V,V,C.POINTER(I)])(D,config,None,(I*1)(0x3038));assert ctx
assert bind(E,'eglMakeCurrent',U,[V,V,V,V])(D,None,None,ctx)
renderer=fn('glGetString',C.c_char_p,[U])(0x1F01).decode();assert 'FD740' in renderer
loaded=sorted({l.split()[-1] for l in pathlib.Path('/proc/self/maps').read_text().splitlines() if 'libgallium' in l or 'libEGL_mesa' in l or 'dri_gbm' in l})
assert loaded and all(not p.startswith('/usr/lib/') for p in loaded)
texgen=fn('glGenTextures',None,[I,C.POINTER(U)]);texbind=fn('glBindTexture',None,[U,U])
fbgen=fn('glGenFramebuffers',None,[I,C.POINTER(U)]);fbbind=fn('glBindFramebuffer',None,[U,U])
attach=fn('glFramebufferTexture2D',None,[U,U,U,U,I]);fbcheck=fn('glCheckFramebufferStatus',U,[U])
image_create=fn('eglCreateImageKHR',V,[V,V,U,V,C.POINTER(I)])
image_target=fn('glEGLImageTargetTexture2DOES',None,[U,V]);blit=fn('glBlitFramebuffer',None,[I]*8+[U,U])
clear=fn('glClear',None,[U]);color=fn('glClearColor',None,[F]*4);scissor=fn('glScissor',None,[I]*4)
enable=fn('glEnable',None,[U]);disable=fn('glDisable',None,[U]);read=fn('glReadPixels',None,[I]*4+[U,U,V])
resources=[]
def image(w,h,fd=None,stride=None):
 t=U();f=U();eglimage=None;texgen(1,C.byref(t));texbind(0x0DE1,t)
 if fd is None:fn('glTexImage2D',None,[U,I,I,I,I,I,U,U,V])(0x0DE1,0,0x8058,w,h,0,0x1908,0x1401,None)
 else:
  assert os.fstat(fd).st_size>=stride*h
  attrs=(I*13)(0x3057,w,0x3056,h,0x3271,0x34325258,0x3272,fd,0x3273,0,0x3274,stride,0x3038)
  eglimage=image_create(D,None,0x3270,None,attrs);assert eglimage
  image_target(0x0DE1,eglimage)
 param=fn('glTexParameteri',None,[U,U,I])
 for key,val in [(0x2801,0x2600),(0x2800,0x2600),(0x2802,0x812F),(0x2803,0x812F)]:param(0x0DE1,key,val)
 fbgen(1,C.byref(f));fbbind(0x8D40,f);attach(0x8D40,0x8CE0,0x0DE1,t,0);assert fbcheck(0x8D40)==0x8CD5
 resources.append((t,f,eglimage));return f

def pattern(frame,q):return tuple((frame*k+q*j+11)%256 for k,j in [(37,61),(71,43),(29,97)])
def paint(f,w,h,frame):
 fbbind(0x8D40,f);enable(0x0C11)
 for q,(x,y,cw,ch) in enumerate([(0,0,w//2,h//2),(w//2,0,w-w//2,h//2),(0,h//2,w//2,h-h//2),(w//2,h//2,w-w//2,h-h//2)]):
  scissor(x,y,cw,ch);color(*(v/255 for v in pattern(frame,q)),1);clear(0x4000)
 disable(0x0C11)
def copy(src,dst,w,h):
 fbbind(0x8CA8,src);fbbind(0x8CA9,dst);disable(0x0C11);blit(0,0,w,h,0,0,w,h,0x4000,0x2600)
def native_fence():
 sync=fn('eglCreateSyncKHR',V,[V,U,C.POINTER(I)])(D,0x3144,(I*1)(0x3038));assert sync
 fn('glFlush',None,[])();fd=fn('eglDupNativeFenceFDANDROID',I,[V,V])(D,sync)
 fn('eglDestroySyncKHR',U,[V,V])(D,sync);assert fd>=0;return fd
try:
 if sys.argv[1]=='consumer':
  w,h,fd,stride,acquire,frame=map(int,sys.argv[2:]);source=image(w,h,fd,stride);target=image(w,h)
  poll=select.poll();poll.register(acquire,select.POLLIN);ready=poll.poll(3000);assert ready and ready[0][1]==select.POLLIN
  copy(source,target,w,h);assert fn('glGetError',U,[])()==0, 'consumer GL blit error';fbbind(0x8D40,target)
  for x,y in [(0,0),(w-1,0),(0,h-1),(w-1,h-1),(w//2,h//2)]:
   pixel=(C.c_ubyte*4)();read(x,y,1,1,0x1908,0x1401,pixel);q=(x>=w//2)+2*(y>=h//2)
   assert all(abs(pixel[i]-pattern(frame,q)[i])<=1 for i in range(3)),(w,h,frame,x,y,list(pixel),pattern(frame,q))
  print(json.dumps({'size':[w,h],'frame':frame,'checks':5,'renderer':renderer,'allocation_size':os.fstat(fd).st_size}))
 else:
  output=pathlib.Path(sys.argv[1]);assert not output.exists();results=[]
  create=bind(G,'gbm_bo_create',V,[V,U,U,U,U]);getfd=bind(G,'gbm_bo_get_fd',I,[V]);getstride=bind(G,'gbm_bo_get_stride',U,[V]);destroy=bind(G,'gbm_bo_destroy',None,[V])
  for w,h in [(65,63),(257,193),(1919,1079),(3840,2160)]:
   bo=create(dev,w,h,0x34325258,5);assert bo,(w,h)
   fd=getfd(bo);stride=getstride(bo);assert fd>=0
   source=image(w,h);shared=image(w,h,fd,stride)
   try:
    for frame in [1,7,19]:
     paint(source,w,h,frame);paint(shared,w,h,frame+31);copy(source,shared,w,h);assert fn('glGetError',U,[])()==0, 'producer GL blit error';acquire=native_fence()
     try:
      child=subprocess.run([sys.executable,__file__,'consumer',str(w),str(h),str(fd),str(stride),str(acquire),str(frame)],pass_fds=(fd,acquire),capture_output=True,text=True,timeout=20)
      assert child.returncode==0,(child.stdout,child.stderr)
      results.append(json.loads(child.stdout.strip().splitlines()[-1]))
     finally:os.close(acquire)
   finally:os.close(fd);destroy(bo)
  output.write_text(json.dumps({'checks':sum(r['checks'] for r in results),'producer_read_shared':False,'physical_scanout_tested':False,'renderer':renderer,'loaded':loaded,'cases':results},indent=2)+'\n')
finally:
 fn('glFinish',None,[])()
 for t,f,eglimage in resources:
  fn('glDeleteFramebuffers',None,[I,C.POINTER(U)])(1,C.byref(f));fn('glDeleteTextures',None,[I,C.POINTER(U)])(1,C.byref(t))
  if eglimage:fn('eglDestroyImageKHR',U,[V,V])(D,eglimage)
 bind(E,'eglMakeCurrent',U,[V,V,V,V])(D,None,None,None);bind(E,'eglDestroyContext',U,[V,V])(D,ctx);bind(E,'eglTerminate',U,[V])(D)
 bind(G,'gbm_device_destroy',None,[V])(dev);os.close(card)
