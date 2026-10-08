from pathlib import Path
import json,subprocess,os,shutil
root=Path('/proc/1/root/sys/kernel/tracing');name='hdmi_copy_gap_20261008';instance=root/'instances'/name
assert os.geteuid()==0 and not instance.exists()
instance.mkdir();record={'instance':str(instance),'events':[],'global_tracing_changed':False}
try:
 (instance/'tracing_on').write_text('0');(instance/'buffer_size_kb').write_text('4096');(instance/'trace_clock').write_text('mono')
 definitions={
 'sched/sched_switch':'prev_comm ~ "Xorg*" || next_comm ~ "Xorg*" || prev_comm ~ "glxgears*" || next_comm ~ "glxgears*"',
 'sched/sched_wakeup':'comm ~ "Xorg*" || comm ~ "glxgears*"',
 'drm/drm_vblank_event':'','drm/drm_vblank_event_delivered':'',
 'kgsl/kgsl_waittimestamp_entry':'','kgsl/kgsl_waittimestamp_exit':'','kgsl/kgsl_fire_event':'','kgsl/kgsl_register_event':'',
 'kgsl/adreno_cmdbatch_submitted':'','kgsl/adreno_cmdbatch_retired':'','kgsl/kgsl_pwrlevel':''}
 for event,filter in definitions.items():
  directory=instance/'events'/event
  if not directory.exists():continue
  if filter:(directory/'filter').write_text(filter)
  (directory/'enable').write_text('1');record['events'].append(event)
 (instance/'tracing_on').write_text('1');(instance/'trace_marker').write_text('HDMI_COPY_GAP_START')
 cmd=['python3','/tmp/hdmi-app-pacing-20261008/run-gears.py','gears-maximized-traced','--binary','/usr/local/bin/glxgears','--maximize','--seconds','20','--mesa-build','mesa-r7']
 run=subprocess.run(cmd,timeout=50);record['child_result']=run.returncode
 (instance/'trace_marker').write_text('HDMI_COPY_GAP_STOP');(instance/'tracing_on').write_text('0')
 record['buffer_stats']={p.parent.name:p.read_text() for p in (instance/'per_cpu').glob('cpu*/stats')}
 out=Path('/tmp/hdmi-app-pacing-20261008/kernel-copy-trace.txt')
 with (instance/'trace').open('rb') as source,out.open('wb') as target:shutil.copyfileobj(source,target)
 Path('/tmp/hdmi-app-pacing-20261008/kernel-copy-trace.json').write_text(json.dumps(record,indent=2)+'\n')
 print(json.dumps({'child':run.returncode,'trace_bytes':out.stat().st_size,'instance':name,'events':record['events']}),flush=True)
finally:
 (instance/'tracing_on').write_text('0');(instance/'events/enable').write_text('0');instance.rmdir()
