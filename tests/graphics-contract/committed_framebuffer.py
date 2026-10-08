"""Read-only committed 4K framebuffer sampling, independent of Xorg events.

Build the C helper on the build server and set HDMI_FRAMEBUFFER_HELPER to its
RAM-backed .so. This is software-state sampling, not optical scanout capture.
The phase alignment defaults to the measured 30 Hz mode; set the real period
through HDMI_FRAMEBUFFER_PERIOD_NS for other refresh rates.
"""
import ctypes as C,os,time,pathlib,json,sys
from xroot_capture import encode_png
class Meta(C.Structure):
 _fields_=[(name,C.c_uint32) for name in ['size','version','framebuffer','format','width','height','pitch','stable']]+[(name,C.c_uint64) for name in ['begin_ns','end_ns','sequence_before','sequence_after','vblank_before_ns','vblank_after_ns']]
class Image:
 def __init__(self,w,h):self.width=w;self.height=h;self.bits_per_pixel=32;self.bytes_per_line=w*4;self.xoffset=0;self.byte_order=0;self.red_mask=0xff0000;self.green_mask=0xff00;self.blue_mask=0xff
class Scanout:
 def __init__(self,helper=None,crtc=235,period_ns=None):
  self.crtc=int(crtc);self.period_ns=int(period_ns or os.environ.get('HDMI_FRAMEBUFFER_PERIOD_NS','33333333'));assert 8000000<=self.period_ns<=100000000
  self.lib=C.CDLL(str(helper or os.environ.get('HDMI_FRAMEBUFFER_HELPER','/tmp/hdmi-scanout-20261008/scanout-read.so')));self.lib.hdmi_scanout_read.argtypes=[C.c_int,C.c_uint32,C.c_uint32,C.c_uint32,C.c_uint32,C.c_uint32,C.c_void_p,C.c_uint64,C.POINTER(Meta)];self.lib.hdmi_scanout_read.restype=C.c_int
  self.fd=os.open('/dev/dri/card0',os.O_RDWR|os.O_CLOEXEC);self.records=[];self.images=[];self.total=0
 def align(self):
  seq,ust=C.c_uint64(),C.c_uint64();self.lib.drmCrtcGetSequence.argtypes=[C.c_int,C.c_uint32,C.POINTER(C.c_uint64),C.POINTER(C.c_uint64)];self.lib.drmCrtcGetSequence.restype=C.c_int
  assert self.lib.drmCrtcGetSequence(self.fd,self.crtc,C.byref(seq),C.byref(ust))==0
  now=time.monotonic_ns();phase=now-ust.value;assert 0<=phase<=100000000
  if phase>5000000:time.sleep(max(0,(ust.value+self.period_ns+2000000-now)/1e9))
 def read(self,x,y,w,h,details=None):
  raw=C.create_string_buffer(w*h*4);meta=Meta(size=C.sizeof(Meta),version=1);ret=self.lib.hdmi_scanout_read(self.fd,self.crtc,x,y,w,h,raw,len(raw),C.byref(meta));record={n:getattr(meta,n) for n,_ in Meta._fields_};record.update(returncode=ret,crop=[x,y,w,h],source='DRM committed external framebuffer; not optical capture',details=details)
  if ret:raise OSError(-ret,'committed framebuffer read')
  record['copy_ms']=(meta.end_ns-meta.begin_ns)/1e6;record['capture_phase_ms']=(meta.begin_ns-meta.vblank_before_ns)/1e6
  name=f'{len(self.records):03d}.png';record['file']=name;self.records.append(record);self.total+=len(raw);assert self.total<=256*1024*1024
  self.images.append((name,Image(w,h),raw.raw));return record
 def save(self,path):
  path=pathlib.Path(path);path.mkdir(mode=0o700)
  for name,image,raw in self.images:
   target=path/name;target.write_bytes(encode_png(image,raw));target.chmod(0o600);os.chown(target,4000,4000)
  (path/'captures.json').write_text(json.dumps(self.records,indent=2)+'\n');os.chown(path/'captures.json',4000,4000);(path/'captures.json').chmod(0o600);os.chown(path,4000,4000);self.images.clear()
 def close(self):os.close(self.fd)
if __name__=='__main__':
 s=Scanout()
 try:print(json.dumps(s.read(100,100,32,32)),flush=True)
 finally:s.close()
