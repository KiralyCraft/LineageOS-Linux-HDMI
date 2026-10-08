#!/usr/bin/env python3
"""Check an owned vertical Firefox pattern test at the viewport's edges.

Use firefox-webgl.html?overlay=0 and a complete firefox-wm-resize.py --vertical
capture in RAM. Calibrate the viewport from initial native window geometry and
the page's dimensions; this helper currently requires a device pixel ratio of
one. Exclude changing capture geometry and the synthetic corner-resize cursor.
All image observations come from the committed framebuffer, not optical output.
"""
from pathlib import Path
import sys,json,struct,zlib,collections,hashlib
p=Path(sys.argv[1]).resolve();assert p.is_relative_to(Path('/tmp')) or p.is_relative_to(Path('/dev/shm'))
source=sys.argv[2] if len(sys.argv)>2 else 'scanout';assert source in ['scanout','root']
work=json.loads((p/'result.json').read_text());assert work['complete'] and work['vertical']
initial=work.get('initial_client_bounds')
if initial is None:
 # Older vertical captures started from a settled 960x720 window. Their
 # first request has the same size and supplies the missing calibration.
 initial=work['records'][0]['actual'];assert initial[2:]==[960,720]
child=work['initial_child_bounds'];assert child[2]==work['before']['inner'][0],'Device pixel ratio must be one'
left,top=child[0]-initial[0],child[1]-initial[1];right=initial[0]+initial[2]-child[0]-child[2];bottom=initial[1]+initial[3]-child[1]-child[3]
assert min(left,top,right,bottom)>=0
chrome=child[3]-work['before']['inner'][1];metadata_path=p/source/'captures.json';metadata=json.loads(metadata_path.read_text());rows=[]
if source=='root':
 metadata=[dict(file=r['file'],geometry_stable=r['geometry_stable'],crop=r['root_crop'],details=dict(client_bounds=r['client_bounds'],egl_child_bounds=[],age=r['metadata']['age'])) for r in metadata]
for capture in metadata:
 data=(p/source/capture['file']).read_bytes();pos=8;encoded=bytearray()
 while pos<len(data):
  n=struct.unpack_from('!I',data,pos)[0];kind=data[pos+4:pos+8];payload=data[pos+8:pos+8+n];pos+=n+12
  if kind==b'IHDR':pw,ph=struct.unpack_from('!2I',payload)
  if kind==b'IDAT':encoded.extend(payload)
 raw=zlib.decompress(encoded);stride=pw*3+1;assert len(raw)==stride*ph
 cx,cy,cw,ch=capture['details']['client_bounds']; child=capture['details']['egl_child_bounds']; x,y,w,h=cx+left,cy+top+chrome,cw-left-right,ch-top-bottom-chrome;fx,fy,_,_=capture['crop'];x-=fx;y-=fy
 if not capture['geometry_stable'] or x<0 or y<0 or x+w>pw or y+h>ph:
  rows.append(dict(file=capture['file'],age=capture['details']['age'],geometry_stable=False,central_error=999,frame_mod192=None,edge_bad=8,pink_pixels=0,black_pixels=0,excluded='geometry changed during capture'));continue
 def pixel(px,py):
  assert 0<=px<pw and 0<=py<ph,(px,py,pw,ph)
  start=py*stride+1+px*3;return list(raw[start:start+3])
 center=[pixel(x+int(w*xx),y+int(h*yy)) for xx,yy in [(.25,.25),(.75,.25),(.25,.75),(.75,.75)]]
 def color(serial,q):return [32+(serial*f+q*41)%192 for f in [37,71,29]]
 error,serial=min((max(abs(a-b) for rgb,q in zip(center,[2,3,0,1]) for a,b in zip(rgb,color(s,q))),s) for s in range(192))
 points=[(int(w*.25),2),(int(w*.75),2),(2,int(h*.25)),(w-3,int(h*.25)),(2,int(h*.75)),(w-3,int(h*.75)),(int(w*.25),h-3),(int(w*.75),h-3)]
 edge=[]
 for px,py in points:
  q=(2 if py<h//2 else 0)+(1 if px>=w//2 else 0);rgb=pixel(x+px,y+py);edge.append(dict(position=[px,py],rgb=rgb,expected=color(serial,q),error=max(abs(a-b) for a,b in zip(rgb,color(serial,q)))))
 pink=black=0
 for yy in range(y+2,y+h-2):
  # The synthetic pointer remains at the bottom-right resize corner. Its
  # black/white glyph is expected and is not unpainted application content.
  end=x+w-64 if yy>=y+h-64 else x+w-2
  rgb=raw[yy*stride+1+(x+2)*3:yy*stride+1+end*3];pink+=rgb.count(bytes([229,17,173]));black+=rgb.count(b'\0\0\0')
 rows.append(dict(file=capture['file'],age=capture['details']['age'],geometry_stable=capture['geometry_stable'],central_error=error,frame_mod192=serial,edge_bad=sum(e['error']>1 for e in edge),pink_pixels=pink,black_pixels=black,viewport=[x,y,w,h],edge=edge))
summary=dict(case=p.name,observation_source=source,complete=work['complete'],captures=len(rows),stable_geometry=sum(r['geometry_stable'] for r in rows),chrome_height=chrome,insets=[left,top,right,bottom],central_correct=sum(r['central_error']<=1 for r in rows),edge_correct=sum(r['central_error']<=1 and not r['edge_bad'] for r in rows),pink_frames=sum(r['pink_pixels']>0 for r in rows),black_frames=sum(r['black_pixels']>0 for r in rows),large_black_strips=sum(r['black_pixels']>1000 and r['edge_bad']>0 for r in rows),physical_optical_capture=False,metadata_sha256=hashlib.sha256(metadata_path.read_bytes()).hexdigest(),records=rows)
(p/('root-edge-summary.json' if source=='root' else 'edge-summary.json')).write_text(json.dumps(summary,indent=2)+'\n');print({k:v for k,v in summary.items() if k!='records'});print('Background exposures:',[(r['file'],round(r['age'],2),r['pink_pixels'],r['black_pixels']) for r in rows if r['pink_pixels'] or r['black_pixels']])
