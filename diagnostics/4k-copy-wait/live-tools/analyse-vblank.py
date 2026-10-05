import re,json,pathlib,sys
base=pathlib.Path(sys.argv[1]) if len(sys.argv)>1 else pathlib.Path(__file__).resolve().parent
runs=json.loads((base/'vblank-reference.json').read_text())
blocks=[];copies=[];block=None
for line in (base/'Xorg-after.log').read_text().splitlines():
 m=re.match(r'\[\s*([0-9.]+)\].*HDMI_(LATENCY|COPY) (.*)',line)
 if not m:continue
 t=float(m[1]);d={k:(int(v) if v.isdigit() else v) for k,v in re.findall(r'(\w+)=([^ ]+)',m[3])}
 if m[2]=='LATENCY':
  if 'interval_us' in d:
   block=dict(d,end=t,start=t-d['interval_us']/1e6,metrics={});blocks.append(block)
  elif block is not None:block['metrics'][d['metric']]=d
 elif 'interval_us' in d:copies.append(dict(d,end=t,start=t-d['interval_us']/1e6))
 elif 'root_overlap_temp_attempts' in d:
  if copies:copies[-1]['root_overlap_temp_attempts']=d['root_overlap_temp_attempts']
res=[]
for p in runs['phases']:
 p['end_monotonic']=p['start_monotonic']+p['seconds']
 b=[b for b in blocks if b['start']>=p['start_monotonic'] and b['end']<=p['end_monotonic']]
 c=[c for c in copies if c['start']>=p['start_monotonic'] and c['end']<=p['end_monotonic']]
 out={'phase':p['phase'],'full_windows':len(b),'seconds':sum(z['interval_us'] for z in b)/1e6,'copies':sum(z['copies'] for z in b),'pixels':sum(z['pixels'] for z in b),'repeat_msc':sum(z['repeat_msc'] for z in b),'metrics':{},'glamor':c}
 for name in ['copy_submit','finish','flip_queue','flip_inflight','callback_gap','damage_to_copy','callback_minus_ust']:
  ds=[z['metrics'][name] for z in b];n=sum(x['n'] for x in ds)
  out['metrics'][name]={'n':n,'mean_ms':sum(x['total_us'] for x in ds)/max(1,n)/1000,'per_window_p95_bound_ms':[x['p95_bound_us']/1000 for x in ds],'max_ms':max([x['max_us']/1000 for x in ds],default=0),'total_ms':sum(x['total_us'] for x in ds)/1000}
 res.append(out)
 print(json.dumps({**out,'glamor':[{k:v for k,v in c.items() if k in ['attempts','n','pixels','root_overlap_temp_attempts']} for c in out['glamor']]}))
(base/'vblank-analysis.json').write_text(json.dumps(res,indent=2))
