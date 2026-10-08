import ctypes as C,pathlib,time,json,math,statistics,argparse
ap=argparse.ArgumentParser();ap.add_argument('output',type=pathlib.Path);a=ap.parse_args()
X=C.CDLL('libX11.so.6');V,I,U,L=C.c_void_p,C.c_int,C.c_uint,C.c_ulong
def b(n,r,types):f=getattr(X,n);f.restype=r;f.argtypes=types;return f
d=b('XOpenDisplay',V,[C.c_char_p])(None);assert d
root=b('XDefaultRootWindow',L,[V])(d)
class Attributes(C.Structure):_fields_=[('background_pixmap',L),('background_pixel',L),('border_pixmap',L),('border_pixel',L),('bit_gravity',I),('win_gravity',I),('backing_store',I),('backing_planes',L),('backing_pixel',L),('save_under',I),('event_mask',C.c_long),('do_not_propagate_mask',C.c_long),('override_redirect',I),('colormap',L),('cursor',L)]
create=b('XCreateSimpleWindow',L,[V,L,I,I,U,U,U,L,L]);attr=b('XChangeWindowAttributes',I,[V,L,L,C.POINTER(Attributes)]);move=b('XMoveWindow',I,[V,L,I,I]);mapw=b('XMapWindow',I,[V,L]);sync=b('XSync',I,[V,I]);destroy=b('XDestroyWindow',I,[V,L]);name=b('XStoreName',I,[V,L,C.c_char_p]);wins=[];rows=[]
try:
 for i,color in enumerate([0x205080,0x806020]):
  w=create(d,root,40+1600*i,100+900*i,1100,700,1,0xffffff,color);assert w;wins.append(w);attr(d,w,1<<9,C.byref(Attributes(override_redirect=1)));name(d,w,b'HDMI owned 2D movement probe');mapw(d,w)
 sync(d,0);time.sleep(.5);start=time.monotonic();deadline=start
 while time.monotonic()-start<20:
  t=time.monotonic();elapsed=t-start
  for i,w in enumerate(wins):move(d,w,40+1600*i+int(180*(1+math.sin(elapsed*2+i))),100+900*i+int(80*(1+math.cos(elapsed*2+i))))
  before=time.monotonic_ns();sync(d,0);after=time.monotonic_ns();rows.append({'relative_seconds':elapsed,'turnaround_ms':(after-before)/1e6});deadline+=.02;time.sleep(max(0,deadline-time.monotonic()))
finally:
 for w in wins:destroy(d,w)
 sync(d,0);b('XCloseDisplay',I,[V])(d)
values=sorted(r['turnaround_ms'] for r in rows);summary={'windows':2,'size_each':[1100,700],'batches':len(rows),'seconds':time.monotonic()-start,'turnaround_ms':{'mean':statistics.mean(values),'p95':values[math.ceil(.95*len(values))-1],'p99':values[math.ceil(.99*len(values))-1],'max':max(values)},'physical_scanout_tested':False,'records':rows};a.output.write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps({k:v for k,v in summary.items() if k!='records'}))
