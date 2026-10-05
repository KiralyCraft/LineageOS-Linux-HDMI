#!/usr/bin/env python3
"""Offscreen shader-copy matrix. Allocates buffers; never modesets or connects to X."""
import ctypes as C,json,os,pathlib,select,time,faulthandler,sys
faulthandler.enable()
output=pathlib.Path(sys.argv[1]); assert not output.exists()
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
loaded_mesa=sorted({line.split()[-1] for line in pathlib.Path('/proc/self/maps').read_text().splitlines() if any(s in line for s in ['libgallium','dri_gbm','libgbm','libEGL_mesa'])})
print('LOADED_MESA',json.dumps(loaded_mesa),flush=True)
assert all(not path.startswith('/usr/lib/') for path in loaded_mesa), 'Mixed system/private Mesa refused'
context=bind(lib,'eglCreateContext',V,[V,V,V,C.POINTER(I)])(d,config,None,(I*1)(0x3038));assert context
assert bind(lib,'eglMakeCurrent',B,[V,V,V,V])(d,None,None,context)
getstr=ext('glGetString',C.c_char_p,[U]);renderer=getstr(0x1F01).decode();glversion=getstr(0x1F02).decode();gl_ext=getstr(0x1F03).decode()
r={'renderer':renderer,'gl_version':glversion,'egl_native_fence':'EGL_ANDROID_native_fence_sync' in extensions,'egl_fence':'EGL_KHR_fence_sync' in extensions,'texture_barrier':'GL_NV_texture_barrier' in gl_ext,'tile_raster_order':'GL_MESA_tile_raster_order' in gl_ext}
assert 'FD740' in renderer
assert r['egl_native_fence']
fns={}
def gl(name,ret,args):
 if name not in fns:fns[name]=ext(name,ret,args)
 return fns[name]
finish=gl('glFinish',None,[]);flush=gl('glFlush',None,[])
bo_create=bind(gbm,'gbm_bo_create',V,[V,U,U,U,U]);bo_destroy=bind(gbm,'gbm_bo_destroy',None,[V]);bo_fd=bind(gbm,'gbm_bo_get_fd',I,[V]);bo_stride=bind(gbm,'gbm_bo_get_stride',U,[V])
create_image=ext('eglCreateImageKHR',V,[V,V,U,V,C.POINTER(I)]);destroy_image=ext('eglDestroyImageKHR',B,[V,V]);image_target=gl('glEGLImageTargetTexture2DOES',None,[U,V])
tex_bind=gl('glBindTexture',None,[U,U]);fb_bind=gl('glBindFramebuffer',None,[U,U]);clear=gl('glClear',None,[U]);clear_color=gl('glClearColor',None,[C.c_float]*4)
texgen=gl('glGenTextures',None,[I,C.POINTER(U)]);fbgen=gl('glGenFramebuffers',None,[I,C.POINTER(U)])
texture_parameter=gl('glTexParameteri',None,[U,U,I]);attach=gl('glFramebufferTexture2D',None,[U,U,U,U,I]);check=gl('glCheckFramebufferStatus',U,[U]);viewport=gl('glViewport',None,[I]*4)
resources=[]
def image(linear):
 tex=U();fbo=U();texgen(1,C.byref(tex));tex_bind(0x0DE1,tex);bo=None;eglimage=None
 if linear:
  bo=bo_create(dev,3840,2160,0x34325258,5);assert bo,'GBM renderonly allocation failed';dma_fd=bo_fd(bo);assert dma_fd>=0
  attrs=(I*13)(0x3057,3840,0x3056,2160,0x3271,0x34325258,0x3272,dma_fd,0x3273,0,0x3274,bo_stride(bo),0x3038)
  try:eglimage=create_image(d,None,0x3270,None,attrs);assert eglimage,'DMA-BUF EGL import failed'
  finally:os.close(dma_fd)
  image_target(0x0DE1,eglimage)
 else:gl('glTexImage2D',None,[U,I,I,I,I,I,U,U,V])(0x0DE1,0,0x8058,3840,2160,0,0x1908,0x1401,None)
 for param,val in [(0x2801,0x2600),(0x2800,0x2600),(0x2802,0x812F),(0x2803,0x812F)]:texture_parameter(0x0DE1,param,val)
 fbgen(1,C.byref(fbo));fb_bind(0x8D40,fbo);attach(0x8D40,0x8CE0,0x0DE1,tex,0);assert check(0x8D40)==0x8CD5
 viewport(0,0,3840,2160);clear_color(.25,.5,.75,1);clear(0x4000)
 print('IMAGE_READY',linear,flush=True)
 entry={'tex':tex,'fbo':fbo,'bo':bo,'eglimage':eglimage,'linear':linear};resources.append(entry);return entry
print('CAPABILITIES',json.dumps(r),flush=True)
source_linear=image(True);source_private=image(False);temporary=image(False);destination=image(True);finish()
def shader(kind,code):
 sh=gl('glCreateShader',U,[U])(kind);data=C.c_char_p(code.encode());gl('glShaderSource',None,[U,I,C.POINTER(C.c_char_p),C.POINTER(I)])(sh,1,C.byref(data),None);gl('glCompileShader',None,[U])(sh);ok=I();gl('glGetShaderiv',None,[U,U,C.POINTER(I)])(sh,0x8B81,C.byref(ok))
 if not ok.value:
  log=C.create_string_buffer(4096);gl('glGetShaderInfoLog',None,[U,I,C.POINTER(I),V])(sh,4096,None,log);raise RuntimeError(log.value.decode())
 return sh
print('SHADER_COMPILE',flush=True)
vs=shader(0x8B31,'#version 130\nin vec2 pos;out vec2 uv;void main(){uv=pos/vec2(3840,2160);gl_Position=vec4(uv*2.-1.,0,1);}')
fs=shader(0x8B30,'#version 130\nuniform sampler2D source;in vec2 uv;out vec4 color;void main(){color=texture(source,uv);}')
program=gl('glCreateProgram',U,[])();gl('glAttachShader',None,[U,U])(program,vs);gl('glAttachShader',None,[U,U])(program,fs);gl('glBindAttribLocation',None,[U,U,C.c_char_p])(program,0,b'pos');gl('glLinkProgram',None,[U])(program);ok=I();gl('glGetProgramiv',None,[U,U,C.POINTER(I)])(program,0x8B82,C.byref(ok));assert ok.value
use=gl('glUseProgram',None,[U]);use(program);loc=gl('glGetUniformLocation',I,[U,C.c_char_p])(program,b'source');gl('glUniform1i',None,[I,I])(loc,0)
vao=U();vbo=U();gl('glGenVertexArrays',None,[I,C.POINTER(U)])(1,C.byref(vao));gl('glBindVertexArray',None,[U])(vao);gl('glGenBuffers',None,[I,C.POINTER(U)])(1,C.byref(vbo));gl('glBindBuffer',None,[U,U])(0x8892,vbo);gl('glEnableVertexAttribArray',None,[U])(0);gl('glVertexAttribPointer',None,[U,I,U,B,I,V])(0,2,0x1406,False,0,None)
draw=gl('glDrawArrays',None,[U,I,I]);buffer=gl('glBufferData',None,[U,C.c_ssize_t,V,U]);get_error=gl('glGetError',U,[])
create_sync=ext('eglCreateSyncKHR',V,[V,U,C.POINTER(I)]);dup=ext('eglDupNativeFenceFDANDROID',I,[V,V]);destroy_sync=ext('eglDestroySyncKHR',B,[V,V])
def summary(values):
 v=sorted(values);return {'n':len(v),'mean_ms':sum(v)/len(v),'median_ms':v[len(v)//2],'p95_ms':v[int((len(v)-1)*.95)],'max_ms':v[-1]}
def clock():
 try:return pathlib.Path('/sys/class/kgsl/kgsl-3d0/gpuclk').read_text().strip()
 except OSError:return None

# Deliberately distinct patterns, changing frame identifiers and poisoned
# destinations. No producer readback occurs before the GPU copy and fence.
blit=gl('glBlitFramebuffer',None,[I]*8+[U,U])
scissor=gl('glScissor',None,[I]*4);enable=gl('glEnable',None,[U]);disable=gl('glDisable',None,[U])
read=gl('glReadPixels',None,[I,I,I,I,U,U,V])
poison=(229,17,173)
def pattern(frame,x,y):
 quadrant=(x>=1920)+2*(y>=1080)
 return tuple((frame*k+quadrant*j+11)%256 for k,j in [(37,61),(71,43),(29,97)])
def fill(src,frame):
 fb_bind(0x8D40,src['fbo']);enable(0x0C11)
 for x,y in [(0,0),(1920,0),(0,1080),(1920,1080)]:
  scissor(x,y,1920,1080);clear_color(*(v/255 for v in pattern(frame,x,y)),1);clear(0x4000)
 disable(0x0C11)
def poison_image(dst):
 fb_bind(0x8D40,dst['fbo']);disable(0x0C11);clear_color(*(v/255 for v in poison),1);clear(0x4000)
def shader_copy(src,dst,boxes):
 fb_bind(0x8D40,dst['fbo']);viewport(0,0,3840,2160);tex_bind(0x0DE1,src['tex'])
 for x,y,w,h in boxes:
  vertices=(C.c_float*12)(x,y,x+w,y,x+w,y+h,x,y,x+w,y+h,x,y+h)
  buffer(0x8892,C.sizeof(vertices),vertices,0x88E0);draw(0x0004,0,6)
def blit_copy(src,dst,boxes):
 fb_bind(0x8CA8,src['fbo']);fb_bind(0x8CA9,dst['fbo'])
 for x,y,w,h in boxes:blit(x,y,x+w,y+h,x,y,x+w,y+h,0x4000,0x2600)
def check_pixels(frame,boxes):
 fb_bind(0x8D40,destination['fbo'])
 points=[(1,1),(1919,1079),(1920,1080),(3838,2158),(500,500),(3000,500),(500,1500),(3000,1500)]
 for x,y,w,h in boxes:points.extend([(x,y),(x+w-1,y+h-1),(x+w//2,y+h//2)])
 for x,y in points:
  inside=any(bx<=x<bx+bw and by<=y<by+bh for bx,by,bw,bh in boxes)
  expected=pattern(frame,x,y) if inside else poison
  pixel=(C.c_ubyte*4)();read(x,y,1,1,0x1908,0x1401,pixel)
  assert all(abs(pixel[i]-expected[i])<=1 for i in range(3)), (frame,(x,y),list(pixel),expected)
 return len(points)
def wait_native():
 sync=create_sync(d,0x3144,(I*1)(0x3038));assert sync
 flush();fd=dup(d,sync);destroy_sync(d,sync);assert fd>=0
 try:
  poll=select.poll();poll.register(fd,select.POLLIN);ready=poll.poll(3000)
  assert ready and ready[0][1]==select.POLLIN,ready
 finally:os.close(fd)
results={'renderer':renderer,'mesa':loaded_mesa,'physical_display_tested':False,'cases':[]}
try:
 workloads=[('full',[(0,0,3840,2160)]),('partial',[(99,73,1401,999)]),
            ('fragmented',[(150+x*850,130+y*450,257,193) for y in range(4) for x in range(4)])]
 for backend,copy in [('shader',shader_copy),('blit',blit_copy)]:
  for source_name,src in [('linear',source_linear),('private',source_private)]:
   for name,boxes in workloads:
    submits=[];waits=[];checks=0
    for frame in range(1,13):
     fill(src,frame);poison_image(destination);finish()
     start=time.monotonic();copy(src,destination,boxes);submitted=time.monotonic();wait_native();completed=time.monotonic()
     assert get_error()==0,(backend,source_name,name)
     checks+=check_pixels(frame,boxes)
     submits.append((submitted-start)*1000);waits.append((completed-submitted)*1000)
    result={'backend':backend,'source':source_name,'workload':name,'pixel_checks':checks,'submit':summary(submits),'export_and_wait':summary(waits)}
    results['cases'].append(result);print(json.dumps(result),flush=True)
 output.write_text(json.dumps(results,indent=2)+'\n')
finally:
 finish()
 for entry in resources:
  gl('glDeleteFramebuffers',None,[I,C.POINTER(U)])(1,C.byref(entry['fbo']));gl('glDeleteTextures',None,[I,C.POINTER(U)])(1,C.byref(entry['tex']))
  if entry['eglimage']:destroy_image(d,entry['eglimage'])
  if entry['bo']:bo_destroy(entry['bo'])
 bind(lib,'eglMakeCurrent',B,[V,V,V,V])(d,None,None,None);bind(lib,'eglDestroyContext',B,[V,V])(d,context);bind(lib,'eglTerminate',B,[V])(d);destroy(dev);os.close(fd)
