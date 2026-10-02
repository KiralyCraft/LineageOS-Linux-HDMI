#!/usr/bin/env python3
"""Inside ARM build container: rebuild affected modules using exact O2 commands.
Preserves the source, objects, libraries and binaries of both earlier builds.
"""
import concurrent.futures,hashlib,json,pathlib,shlex,shutil,subprocess
base=pathlib.Path('/build/xserver-tearfree-c16/src');build=pathlib.Path('/build/xserver-perf-o2-20261002/build');d=pathlib.Path('/build/xserver-latency-20261002')
assert not d.exists(),'Refusing to overwrite existing diagnostic build'
d.mkdir();src=d/'src'
for rel in ['hw/xfree86/drivers/modesetting','glamor']:
 shutil.copytree(base/rel,src/rel)
(src/'include').mkdir()
patchdir=pathlib.Path('/build/hdmi-performance-20261002')
for name in ['xorg-tearfree-region-cleanup.patch','xorg-latency-diagnostics.patch']:
 subprocess.run(['patch','--batch','-p1','-d',str(src),'-i',str(patchdir/name)],check=True)
commands=json.loads((build/'compile_commands.json').read_text());tasks=[];objmap={}
for c in commands:
 oldsrc=(build/c['file']).resolve()
 if oldsrc.parent==base/'hw/xfree86/drivers/modesetting' or oldsrc==base/'glamor/glamor_copy.c':
  relative=oldsrc.relative_to(base);args=shlex.split(c['command']);oldout=args[args.index('-o')+1];out=d/'objects'/oldout
  out.parent.mkdir(parents=True,exist_ok=True);objmap[oldout]=str(out)
  args[args.index('-o')+1]=str(out);args[args.index('-c')+1]=str(src/relative)
  for flag in ['-MF','-MQ']:
   if flag in args:args[args.index(flag)+1]=str(out)+('.d' if flag=='-MF' else '')
  # Prepend only the new independent measurement header; relative includes
  # in copied driver source resolve to its matching copied private headers.
  args.insert(args.index('-c'),'-I'+str(src/'include'))
  tasks.append(args)
(d/'compile-commands.json').write_text(json.dumps(tasks,indent=2))
def compile_one(args):
 subprocess.run(args,cwd=build,check=True)
 print('COMPILED',args[args.index('-c')+1],flush=True)
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(compile_one,tasks))
# Preserve the original glamor archive, replacing only the rebuilt member.
archive=d/'libglamor-full.a'
copyobj=objmap['glamor/libglamor.a.p/glamor_copy.c.o']
members=subprocess.check_output(['ar','t',str(build/'glamor/libglamor.a')],text=True).splitlines()
members=[copyobj if pathlib.Path(m).name=='glamor_copy.c.o' else m for m in members]
subprocess.run(['ar','rcs',str(archive)]+members,check=True)
outputs={}
for target in ['hw/xfree86/drivers/modesetting/modesetting_drv.so','hw/xfree86/glamor_egl/libglamoregl.so']:
 raw=subprocess.check_output(['ninja','-C',str(build),'-t','commands',target],text=True).strip().splitlines()[-1]
 args=shlex.split(raw);out=d/pathlib.Path(target).name
 args[args.index('-o')+1]=str(out)
 args=[objmap.get(a,str(archive) if a=='glamor/libglamor.a' else a) for a in args]
 subprocess.run(args,cwd=build,check=True)
 outputs[out.name]=hashlib.sha256(out.read_bytes()).hexdigest()
shutil.copy2(build/'hw/xfree86/Xorg',d/'Xorg');outputs['Xorg']=hashlib.sha256((d/'Xorg').read_bytes()).hexdigest()
(d/'result.json').write_text(json.dumps({'base':'xserver-tearfree-c16','optimization':'O2','patches':['xorg-tearfree-region-cleanup.patch','xorg-latency-diagnostics.patch'],'artifacts':outputs,'live_tested':False,'compiled_units':len(tasks)},indent=2))
print('BUILD_COMPLETE',json.dumps(outputs),flush=True)
