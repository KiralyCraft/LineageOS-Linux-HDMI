import argparse,collections,json,math,pathlib,struct
ap=argparse.ArgumentParser();ap.add_argument('trace',type=pathlib.Path);ap.add_argument('--warmup',type=float,default=5);a=ap.parse_args()
raw=a.trace.read_bytes();assert len(raw)>=32
magic,size,requested,stop=struct.unpack_from('<4Q',raw);assert magic==0x31305046494d4448 and size==64 and (len(raw)-32)%64==0
rows=[dict(begin=b,end=e,data=[x,y,z,w,v],event=k,tid=t) for b,e,x,y,z,w,v,k,t in struct.iter_unpack('<QQ5qII',raw[32:])];rows.sort(key=lambda x:x['end'])
assert rows
start=min(r['begin'] for r in rows);data=[r for r in rows if r['begin']>=start+int(a.warmup*1e9)]
def stats(v):
 v=sorted(v)
 return dict(count=len(v),mean_ms=sum(v)/len(v),p50_ms=v[len(v)//2],p95_ms=v[math.ceil(len(v)*.95)-1],p99_ms=v[math.ceil(len(v)*.99)-1],max_ms=v[-1]) if v else {}
result={'probe_records':len(rows),'requested_records':requested,'dropped':max(requested-262144,0),'seconds':(max(r['end'] for r in rows)-start)/1e9,'warmup_excluded_seconds':a.warmup,'events':dict(collections.Counter(r['event'] for r in rows)),'physical_scanout_tested':False,'swaps':{},'present':{}}
for event in [1,2]:
 s=[r for r in data if r['event']==event]
 result['swaps'][event]={'call':stats([(r['end']-r['begin'])/1e6 for r in s]),'end_interval':stats([(y['end']-x['end'])/1e6 for x,y in zip(s,s[1:])]),'longest_intervals':[dict(relative_seconds=(y['end']-start)/1e9,ms=(y['end']-x['end'])/1e6) for x,y in sorted(zip(s,s[1:]),key=lambda p:p[1]['end']-p[0]['end'],reverse=True)[:12]]}
requests={r['data'][0]:r for r in rows if r['event']==7};request_threads={r['tid'] for r in rows if r['event']==7};complete=[r for r in data if r['event']==8 and r['data'][1]==0 and r['tid'] in request_threads]
result['present']={'completed':len(complete),'request_thread_ids':sorted(request_threads),'stream':'Present worker only; main XCB subscription receives duplicate notifications','modes':dict(collections.Counter(r['data'][2] for r in complete)),'msc_delta':dict(collections.Counter(y['data'][4]-x['data'][4] for x,y in zip(complete,complete[1:]))),'ust_delta':stats([(y['data'][3]-x['data'][3])/1000 for x,y in zip(complete,complete[1:])]),'request_to_event':stats([(r['end']-requests[r['data'][0]]['end'])/1e6 for r in complete if r['data'][0] in requests]),'request_options':dict(collections.Counter(r['data'][1] for r in data if r['event']==7))}
print(json.dumps(result,indent=2));a.trace.with_suffix('.summary.json').write_text(json.dumps(result,indent=2)+'\n')
