#!/usr/bin/env python3
"""Offscreen GBM/EGL capability check, no display modesetting or X connection."""
import ctypes as C,os,json,select,time
V=C.c_void_p;I=C.c_int;U=C.c_uint;B=C.c_uint
lib=C.CDLL('libEGL.so.1');gbm=C.CDLL(os.environ['LD_LIBRARY_PATH']+'/libgbm.so.1')
def bind(lib,name,ret,args):
 f=getattr(lib,name);f.restype=ret;f.argtypes=args;return f
addr=bind(lib,'eglGetProcAddress',V,[C.c_char_p])
def ext(name,ret,args):
 p=addr(name.encode());assert p,name;return C.CFUNCTYPE(ret,*args)(p)
create=bind(gbm,'gbm_create_device',V,[I]);destroy=bind(gbm,'gbm_device_destroy',None,[V])
fd=os.open('/dev/dri/card0',os.O_RDWR|os.O_CLOEXEC);dev=create(fd);assert dev
getdisplay=ext('eglGetPlatformDisplayEXT',V,[U,V,C.POINTER(I)])
d=getdisplay(0x31D7,dev,None);assert d
initialize=bind(lib,'eglInitialize',B,[V,C.POINTER(I),C.POINTER(I)]);a=I();b=I();assert initialize(d,C.byref(a),C.byref(b))
query=bind(lib,'eglQueryString',C.c_char_p,[V,I]);extensions=query(d,0x3055).decode()
assert bind(lib,'eglBindAPI',B,[U])(0x30A2)
choose=bind(lib,'eglChooseConfig',B,[V,C.POINTER(I),C.POINTER(V),I,C.POINTER(I)])
attrs=(I*5)(0x3033,0,0x3040,8,0x3038);config=V();count=I();assert choose(d,attrs,C.byref(config),1,C.byref(count)) and count.value
context=bind(lib,'eglCreateContext',V,[V,V,V,C.POINTER(I)])(d,config,None,(I*1)(0x3038));assert context
assert bind(lib,'eglMakeCurrent',B,[V,V,V,V])(d,None,None,context)
getstr=ext('glGetString',C.c_char_p,[U]);renderer=getstr(0x1F01).decode();glversion=getstr(0x1F02).decode();gl_ext=getstr(0x1F03).decode()
r={'renderer':renderer,'gl_version':glversion,'egl_native_fence':'EGL_ANDROID_native_fence_sync' in extensions,'egl_fence':'EGL_KHR_fence_sync' in extensions,'texture_barrier':'GL_NV_texture_barrier' in gl_ext,'tile_raster_order':'GL_MESA_tile_raster_order' in gl_ext}
assert 'FD740' in renderer
# Submit a real offscreen 4K clear before the fence.
tex=U();fbo=U()
ext('glGenTextures',None,[I,C.POINTER(U)])(1,C.byref(tex));ext('glBindTexture',None,[U,U])(0x0DE1,tex)
ext('glTexImage2D',None,[U,I,I,I,I,I,U,U,V])(0x0DE1,0,0x8058,3840,2160,0,0x1908,0x1401,None)
ext('glGenFramebuffers',None,[I,C.POINTER(U)])(1,C.byref(fbo));ext('glBindFramebuffer',None,[U,U])(0x8D40,fbo)
ext('glFramebufferTexture2D',None,[U,U,U,U,I])(0x8D40,0x8CE0,0x0DE1,tex,0)
assert ext('glCheckFramebufferStatus',U,[U])(0x8D40)==0x8CD5
ext('glClearColor',None,[C.c_float]*4)(.2,.4,.6,1);ext('glClear',None,[U])(0x4000)
if r['egl_native_fence']:
 start=time.monotonic();sync=ext('eglCreateSyncKHR',V,[V,U,C.POINTER(I)])(d,0x3144,(I*1)(0x3038));assert sync
 ext('glFlush',None,[])();fence=ext('eglDupNativeFenceFDANDROID',I,[V,V])(d,sync);assert fence>=0
 ext('eglDestroySyncKHR',B,[V,V])(d,sync)
 r['create_flush_export_ms']=1000*(time.monotonic()-start)
 poll=select.poll();poll.register(fence,select.POLLIN);start=time.monotonic();events=poll.poll(3000)
 r['poll_ms']=1000*(time.monotonic()-start);r['poll_events']=events
 assert events and events[0][1]&select.POLLIN
 os.close(fence)
print(json.dumps(r,indent=2))
bind(lib,'eglMakeCurrent',B,[V,V,V,V])(d,None,None,None)
bind(lib,'eglDestroyContext',B,[V,V])(d,context);bind(lib,'eglTerminate',B,[V])(d);destroy(dev);os.close(fd)
