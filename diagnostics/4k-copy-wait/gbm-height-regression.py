#!/usr/bin/env python3
"""Offscreen hardware regression: never modesets or submits a scanout buffer.
Run with the candidate LD_LIBRARY_PATH/LIBGL_DRIVERS_PATH/GBM_BACKENDS_PATH.
Requires access to /dev/dri/card0 and /dev/kgsl-3d0.
"""
import ctypes as C,json,os,time
assert os.environ.get('FD_KGSL_RENDERONLY')=='1'
assert os.environ.get('FD_KGSL_ENABLE_DMABUF')=='1'
g=C.CDLL(os.environ['LD_LIBRARY_PATH']+'/libgbm.so.1',use_errno=True)
g.gbm_create_device.argtypes=[C.c_int];g.gbm_create_device.restype=C.c_void_p
g.gbm_device_destroy.argtypes=[C.c_void_p]
g.gbm_bo_create.argtypes=[C.c_void_p,C.c_uint32,C.c_uint32,C.c_uint32,C.c_uint32];g.gbm_bo_create.restype=C.c_void_p
g.gbm_bo_destroy.argtypes=[C.c_void_p]
for name in ['gbm_bo_get_width','gbm_bo_get_height','gbm_bo_get_stride']:
 f=getattr(g,name);f.argtypes=[C.c_void_p];f.restype=C.c_uint32
g.gbm_bo_get_fd.argtypes=[C.c_void_p];g.gbm_bo_get_fd.restype=C.c_int
fd=os.open('/dev/dri/card0',os.O_RDWR|os.O_CLOEXEC);dev=g.gbm_create_device(fd)
assert dev,'Cannot create GBM device'
failed=[];rows=[]
try:
 for fmt in ['XR24','AR24']:
  for usage in [5,21]:
   # Four consecutive heights exercise every remainder; fixed 4K is a control.
   for w,h in [(1627,h) for h in range(1188,1193)]+[(1690,h) for h in range(1288,1293)]+[(3840,2160)]:
    C.set_errno(0);start=time.monotonic();bo=g.gbm_bo_create(dev,w,h,int.from_bytes(fmt.encode(),'little'),usage)
    row={'format':fmt,'usage':usage,'width':w,'height':h,'success':bool(bo),'errno':C.get_errno(),'ms':1000*(time.monotonic()-start)}
    if bo:
     try:
      row['logical_dimensions_preserved']=(g.gbm_bo_get_width(bo),g.gbm_bo_get_height(bo))==(w,h)
      export=g.gbm_bo_get_fd(bo);row['export_success']=export>=0
      if export>=0:os.close(export)
      if not row['logical_dimensions_preserved'] or not row['export_success']:failed.append(row)
     finally:g.gbm_bo_destroy(bo)
    else:failed.append(row)
    rows.append(row)
finally:g.gbm_device_destroy(dev);os.close(fd)
print(json.dumps({'tested':len(rows),'failed':len(failed),'results':rows},indent=2))
raise SystemExit(bool(failed))
