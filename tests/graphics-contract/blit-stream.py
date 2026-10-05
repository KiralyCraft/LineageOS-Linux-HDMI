#!/usr/bin/env python3
"""Offscreen mixed upload/draw/copy ordering regression. No X or modeset.

Based on copy-paths.py; checks a root image and two alternating destinations
without a pre-copy GPU wait. Results are correctness evidence, not timings.
"""
import ctypes as C,json,os,pathlib,select,time,faulthandler,sys
faulthandler.enable()
output=pathlib.Path(sys.argv[1]); assert not output.exists()
shared_fourcc=0x34325241 if os.environ.get('HDMI_TEST_FORMAT','argb')=='argb' else 0x34325258
upload_mode=os.environ.get('HDMI_TEST_UPLOAD','direct')
assert upload_mode in ('direct','atlas')
upload_bgra=os.environ.get('HDMI_TEST_UPLOAD_BGRA','1')=='1'
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
context_kind=os.environ.get('HDMI_TEST_CONTEXT','core')
assert context_kind in ('core','compat')
context_attrs=(I*7)(0x3098,3,0x30fb,1,0x30fd,1,0x3038) if context_kind=='core' else (I*1)(0x3038)
context=bind(lib,'eglCreateContext',V,[V,V,V,C.POINTER(I)])(d,None,None,context_attrs);assert context
assert bind(lib,'eglMakeCurrent',B,[V,V,V,V])(d,None,None,context)
getstr=ext('glGetString',C.c_char_p,[U]);renderer=getstr(0x1F01).decode();glversion=getstr(0x1F02).decode();
get_integer=ext('glGetIntegerv',None,[U,C.POINTER(I)]);num_ext=I();get_integer(0x821d,C.byref(num_ext))
getstr_i=ext('glGetStringi',C.c_char_p,[U,U]);gl_ext=' '.join(getstr_i(0x1F03,i).decode() for i in range(num_ext.value))
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
  bo=bo_create(dev,3840,2160,shared_fourcc,5);assert bo,'GBM renderonly allocation failed';dma_fd=bo_fd(bo);assert dma_fd>=0
  attrs=(I*13)(0x3057,3840,0x3056,2160,0x3271,shared_fourcc,0x3272,dma_fd,0x3273,0,0x3274,bo_stride(bo),0x3038)
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
source_linear=image(True);source_private=image(False);destination=image(True);finish()
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
# Deliberately distinct patterns, changing frame identifiers and poisoned
# destinations. No producer readback occurs before the GPU copy and fence.
blit=gl('glBlitFramebuffer',None,[I]*8+[U,U])
scissor=gl('glScissor',None,[I]*4);enable=gl('glEnable',None,[U]);disable=gl('glDisable',None,[U])
read=gl('glReadPixels',None,[I,I,I,I,U,U,V])
def shader_copy(src,dst,boxes):
 fb_bind(0x8D40,dst['fbo']);viewport(0,0,3840,2160);tex_bind(0x0DE1,src['tex'])
 for x,y,w,h in boxes:
  vertices=(C.c_float*12)(x,y,x+w,y,x+w,y+h,x,y,x+w,y+h,x,y+h)
  buffer(0x8892,C.sizeof(vertices),vertices,0x88E0);draw(0x0004,0,6)
def blit_copy(src,dst,boxes):
 fb_bind(0x8CA8,src['fbo']);fb_bind(0x8CA9,dst['fbo'])
 for x,y,w,h in boxes:blit(x,y,x+w,y+h,x,y,x+w,y+h,0x4000,0x2600)
def wait_native():
 sync=create_sync(d,0x3144,(I*1)(0x3038));assert sync
 flush();fd=dup(d,sync);destroy_sync(d,sync);assert fd>=0
 try:
  poll=select.poll();poll.register(fd,select.POLLIN);ready=poll.poll(3000)
  assert ready and ready[0][1]==select.POLLIN,ready
 finally:os.close(fd)

second_destination=image(True)
tex_upload=gl('glTexSubImage2D',None,[U,I,I,I,I,I,U,U,V])
pixel_store=gl('glPixelStorei',None,[U,I])
background=(13,19,29)
title_h=31
frames_per_burst=5
checks=0
failures=[]

def clear_rect(dst,rect,rgb):
 fb_bind(0x8D40,dst['fbo']);enable(0x0C11);scissor(*rect)
 clear_color(*(v/255 for v in rgb),1);clear(0x4000);disable(0x0C11)

def title_color(frame,x,y):
 # Fine detail and frame identity, unlike constant-color initialization.
 return ((frame*37+x*17+y*3)%251,(frame*23+x*5+y*11)%251,(frame*13+x*7+y*19)%251)

# Prepare CPU patterns before GPU bursts so Python generation does not idle
# the GPU between the producer upload and subsequent frame operations.
title_pixels={}
for frame in range(1,31):
 w=[800,1001,1279][frame%3]
 pixels=bytes(v for py in range(title_h) for px in range(w) for v in (*(title_color(frame,px,py)[::-1] if upload_bgra else title_color(frame,px,py)),255))
 title_pixels[frame]=C.create_string_buffer(pixels)

def upload_title(frame,rect):
 x,y,w,h=rect
 data=title_pixels[frame]
 target=source_linear if upload_mode=='direct' else source_private
 tex_bind(0x0DE1,target['tex']);pixel_store(0x0CF5,4);pixel_store(0x0CF2,0)
 tex_upload(0x0DE1,0,x,y,w,h,0x80E1 if upload_bgra else 0x1908,0x8367 if upload_bgra else 0x1401,data)
 if upload_mode=='atlas':shader_copy(source_private,source_linear,[rect])

def validate(image,last,phase):
 global checks
 frame,x,y,w,h=last
 fb_bind(0x8D40,image['fbo']);bad=[]
 points=[(1,1,background),(3838,2158,background),
         (x+5,y+title_h+5,((frame*53)%251,71,149)),
         (x+w-1,y+title_h+h-1,((frame*53)%251,71,149))]
 for px in [0,1,7,31,w//2,w-2,w-1]:
  for py in [0,1,7,title_h//2,title_h-2,title_h-1]:
   points.append((x+px,y+py,title_color(frame,px,py)))
 for px,py,want in points:
  got=(C.c_ubyte*4)();read(px,py,1,1,0x1908,0x1401,got);checks+=1
  if any(abs(got[i]-want[i])>1 for i in range(3)):
   bad.append({'point':[px,py],'expected':want,'actual':list(got)})
 if bad:failures.append({'phase':phase,'frame':frame,'image':'root' if image is source_linear else 'destination','bad_pixels':bad})
 return len(bad)

results={'renderer':renderer,'gl_version':glversion,'context':context_kind,'mesa':loaded_mesa,'physical_display_tested':False,'upload':upload_mode,'upload_bgra_rev':upload_bgra,'fourcc':hex(shared_fourcc),'cases':[]}
try:
 # Both paths alternate render and copy work in the same context. Repeated
 # uploads modify the sampled source; later root writes test read retirement.
 for backend,copy in [('shader',shader_copy),('blit',blit_copy),('mixed',None)]:
  for dirty in ['full','fragmented']:
   for prewait in [False,True]:
    case={'backend':backend,'damage':dirty,'pre_copy_finish':prewait,'bursts':0}
    start_checks=checks;start_failures=len(failures)
    for target in [source_linear,destination,second_destination]:clear_rect(target,(0,0,3840,2160),background)
    previous=None;pending=[[],[]]
    for burst in range(6):
     slots={}
     for j in range(frames_per_burst):
      frame=1+burst*frames_per_burst+j
      x,y=[(120,150),(1300,800),(120,150)][frame%3]
      w=[800,1001,1279][frame%3];h=401
      if previous:clear_rect(source_linear,previous,background)
      rect=(x,y,w,title_h+h)
      clear_rect(source_linear,(x,y+title_h,w,h),((frame*53)%251,71,149))
      upload_title(frame,(x,y,w,title_h))
      damage=[rect]+([previous] if previous else [])
      for history in pending:history.extend(damage)
      boxes=[(0,0,3840,2160)] if dirty=='full' else pending[frame%2]
      if dirty=='fragmented':
       boxes=[(bx,by,bw,min(31,bh)) for bx,by,bw,bh in boxes]+[(bx,by+min(31,bh),bw,bh-min(31,bh)) for bx,by,bw,bh in boxes if bh>31]
      target=[destination,second_destination][frame%2]
      pending[frame%2]=[]
      if prewait:finish()
      operation=(shader_copy if frame%3 else blit_copy) if backend=='mixed' else copy
      operation(source_linear,target,boxes)
      slots[frame%2]=(frame,x,y,w,h)
      previous=rect
     wait_native()
     assert get_error()==0,(backend,dirty,prewait)
     # Source readback is after the destination was copied and fenced, never
     # between source drawing and its consuming copy.
     for slot,last in slots.items():validate([destination,second_destination][slot],last,case)
     validate(source_linear,slots[frame%2],case)
     case['bursts']+=1
    case['pixel_checks']=checks-start_checks;case['failure_records']=len(failures)-start_failures
    results['cases'].append(case);print(json.dumps(case),flush=True)
 results['checks']=checks;results['failures']=failures
 output.write_text(json.dumps(results,indent=2)+'\n')
 if failures:raise AssertionError(f'{len(failures)} image checks failed; see {output}')
finally:
 finish()
 for entry in resources:
  gl('glDeleteFramebuffers',None,[I,C.POINTER(U)])(1,C.byref(entry['fbo']));gl('glDeleteTextures',None,[I,C.POINTER(U)])(1,C.byref(entry['tex']))
  if entry['eglimage']:destroy_image(d,entry['eglimage'])
  if entry['bo']:bo_destroy(entry['bo'])
 bind(lib,'eglMakeCurrent',B,[V,V,V,V])(d,None,None,None);bind(lib,'eglDestroyContext',B,[V,V])(d,context);bind(lib,'eglTerminate',B,[V])(d);destroy(dev);os.close(fd)
