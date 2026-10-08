#!/usr/bin/env python3
"""Preserve the verified HDMI runtime and update its matched Mesa/pacing code."""
import argparse,hashlib,json,shutil,subprocess
from pathlib import Path

def sha(p):
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

ap=argparse.ArgumentParser(description=__doc__)
for name in ['base','build','source','repository','gears','xorg_build','output']:
    ap.add_argument('--'+name.replace('_','-'),type=Path,required=True)
a=ap.parse_args()
base,build,source,repo,gears,xorg,out=(getattr(a,n).resolve() for n in ['base','build','source','repository','gears','xorg_build','output'])
assert not out.exists()
subprocess.run(['sha256sum','--strict','-c','SHA256SUMS'],cwd=base,check=True,stdout=subprocess.DEVNULL)
manifest=json.loads((build/'result.json').read_text())
commit=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
assert commit==manifest['commit']
verification=json.loads((build/'source-verification.json').read_text());assert not verification['mismatches']
assert 'PASS: production cache' in (build/'cache-test.log').read_text()
for name,want in manifest['files'].items():assert sha(build/'stage'/name)==want,name
shutil.copytree(base,out,symlinks=True)
libraries={'libgallium-26.2.0-devel.so':'libgallium-26.2.0-devel.so','libGLX_mesa.so.0.0.0':'libGLX_mesa.so.0','libEGL_mesa.so.0.0.0':'libEGL_mesa.so.0','libgbm.so.1.0.0':'libgbm.so.1','dri/libdril_dri.so':'libdril_dri.so','gbm/dri_gbm.so':'gbm/dri_gbm.so'}
for name,target in libraries.items():shutil.copy2(build/'stage/usr/lib'/name,out/'lib/mesa'/target)
assert (out/'lib/mesa/kgsl_dri.so').samefile(out/'lib/mesa/libdril_dri.so')
assert b'HDMI_LOS_MESA_PACED_QUEUE_ABI=1' in (out/'lib/mesa/libgallium-26.2.0-devel.so').read_bytes()
for top in ['android','companion','bin','lib/xorg','libexec']:
    for p in (base/top).rglob('*'):
        if p.is_file():assert sha(p)==sha(out/p.relative_to(base)),p
xr=json.loads((xorg/'result.json').read_text())
for name,want in xr['files'].items():assert sha(xorg/'stage'/name)==want,name
for patch in xr['patches']:assert sha(repo/'patches/xserver26'/patch['name'])==patch['sha256']
assert any(p['name'].startswith('0012-') for p in xr['patches'])
assert 'PASS: production repaint' in (xorg/'host-repaint-test.log').read_text()
shutil.copy2(xorg/'stage/usr/bin/Xorg',out/'libexec/Xorg')
shutil.rmtree(out/'lib/xorg/modules')
shutil.copytree(xorg/'stage/usr/lib/xorg/modules',out/'lib/xorg/modules',symlinks=True)
assert b'HDMI_LOS_XORG_REPAINT_ABI=1' in (out/'lib/xorg/modules/drivers/modesetting_drv.so').read_bytes()
for name in ['result.json','host-repaint-test.log','final-validation.log']:
    shutil.copy2(xorg/name,out/'validation'/('pacing-xorg-'+name))
(out/'source/xorg-built-series').write_text(''.join('patches/xserver26/'+p['name']+'\n' for p in xr['patches']))
shutil.copy2(gears,out/'bin/glxgears')
launcher=(out/'run-agent.sh').read_text()
paced = 'PACED_QUEUE=${MESA_KGSL_HDMI_PACED_QUEUE:-1}'
if paced in launcher:
    for required in [paced, 'PACED_LEAD=${MESA_KGSL_HDMI_PACED_LEAD:-1}',
                     'REPAINT_US=${HDMI_LOS_TEARFREE_REPAINT_US:-8000}',
                     '--paced-queue "$PACED_QUEUE"', '--repaint-us "$REPAINT_US"']:
        assert launcher.count(required) == 1, required
else:
    default='RESIZE_CAPACITY=${MESA_KGSL_HDMI_RESIZE_CAPACITY:-1}'
    assert launcher.count(default)==1
    launcher=launcher.replace(default,default+'\nPACED_QUEUE=${MESA_KGSL_HDMI_PACED_QUEUE:-1}\nPACED_LEAD=${MESA_KGSL_HDMI_PACED_LEAD:-1}\nREPAINT_US=${HDMI_LOS_TEARFREE_REPAINT_US:-8000}')
    launcher=launcher.replace('[--resize-capacity 0|1]','[--resize-capacity 0|1] [--paced-queue 0|1|2|3] [--paced-lead 1|2] [--repaint-us 0..20000]')
    anchor='        --resize-capacity)'
    assert launcher.count(anchor)==1
    launcher=launcher.replace(anchor,'''        --paced-queue|--paced-lead|--repaint-us)
                (($# >= 2)) || { usage; exit 2; }
                if [[ $1 == --paced-queue ]]; then PACED_QUEUE=$2
                elif [[ $1 == --paced-lead ]]; then PACED_LEAD=$2
                else REPAINT_US=$2; fi
                shift 2
                ;;
    '''+anchor)
    anchor='if ((EUID != 0)); then'
    assert launcher.count(anchor)==1
    launcher=launcher.replace(anchor,'[[ $PACED_QUEUE =~ ^[0-3]$ && $PACED_LEAD =~ ^[1-2]$ && $REPAINT_US =~ ^(0|[1-9][0-9]{0,4})$ && $REPAINT_US -le 20000 ]] || { usage; exit 2; }\n'+anchor)
    anchor='args+=(--present-release "$PRESENT_RELEASE" --mesa-queue "$MESA_QUEUE" --resize-capacity "$RESIZE_CAPACITY")'
    assert launcher.count(anchor)==1
    launcher=launcher.replace(anchor,anchor+'\n    args+=(--paced-queue "$PACED_QUEUE" --paced-lead "$PACED_LEAD" --repaint-us "$REPAINT_US")')
    anchor='export MESA_KGSL_HDMI_RESIZE_CAPACITY="$RESIZE_CAPACITY"'
    assert launcher.count(anchor)==1
    launcher=launcher.replace(anchor,anchor+'\nexport MESA_KGSL_HDMI_PACED_QUEUE="$PACED_QUEUE" MESA_KGSL_HDMI_PACED_LEAD="$PACED_LEAD" HDMI_LOS_TEARFREE_REPAINT_US="$REPAINT_US"')
lazy_default = 'LAZY_SLOTS=${MESA_KGSL_HDMI_LAZY_SLOTS:-1}'
if lazy_default not in launcher:
    anchor='REPAINT_US=${HDMI_LOS_TEARFREE_REPAINT_US:-8000}'
    assert launcher.count(anchor)==1
    launcher=launcher.replace(anchor,anchor+'\n'+lazy_default)
    launcher=launcher.replace('[--resize-capacity 0|1]', '[--resize-capacity 0|1] [--lazy-slots 0|1]')
    anchor='        --resize-capacity)'
    assert launcher.count(anchor)==1
    launcher=launcher.replace(anchor,"""        --lazy-slots)
            (($# >= 2)) || { usage; exit 2; }
            LAZY_SLOTS=$2
            shift 2
            ;;
"""+anchor)
    anchor='if ((EUID != 0)); then'
    assert launcher.count(anchor)==1
    launcher=launcher.replace(anchor,'[[ $LAZY_SLOTS == 0 || $LAZY_SLOTS == 1 ]] || { usage; exit 2; }\n'+anchor)
    anchor='args+=(--paced-queue "$PACED_QUEUE" --paced-lead "$PACED_LEAD" --repaint-us "$REPAINT_US")'
    assert launcher.count(anchor)==1
    launcher=launcher.replace(anchor,anchor+'\n    args+=(--lazy-slots "$LAZY_SLOTS")')
    anchor='export MESA_KGSL_HDMI_RESIZE_CAPACITY="$RESIZE_CAPACITY"'
    assert launcher.count(anchor)==1
    launcher=launcher.replace(anchor,anchor+'\nexport MESA_KGSL_HDMI_LAZY_SLOTS="$LAZY_SLOTS"')
assert b'HDMI_LOS_MESA_FRONTEND_FLUSH_ABI=1' in (out/'lib/mesa/libgallium-26.2.0-devel.so').read_bytes()
assert b'HDMI_LOS_MESA_SLOT_ABI=1' in (out/'lib/mesa/libgallium-26.2.0-devel.so').read_bytes()
(out/'run-agent.sh').write_text(launcher)
for name in ['result.json','source-verification.json','cache-test.log']:
    shutil.copy2(build/name,out/'validation'/('pacing-mesa-'+name))
for name,parent in [('demand-on-85c8dfd02.patch','85c8dfd02'),('pacing-on-065604e4b.patch','065604e4b'),('experiment-on-daa6e56de.patch','daa6e56de'),('stack-on-cc190637b.patch','cc190637b')]:
    with (out/'source/mesa'/name).open('wb') as f:subprocess.run(['git','-C',str(source),'diff',parent,commit],stdout=f,check=True)
head=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
shutil.rmtree(out/'source/hdmi');(out/'source/hdmi').mkdir()
with subprocess.Popen(['git','-C',str(repo),'archive',head],stdout=subprocess.PIPE) as p:
    subprocess.run(['tar','xf','-','-C',str(out/'source/hdmi')],stdin=p.stdout,check=True);assert p.wait()==0
info=json.loads((out/'build-info.json').read_text())
info.update(source_commit=head,candidate='BCDEF-Xorg26-frontend-flush-shallow-pacing-demand-images')
info['components']['mesa']=dict(commit=commit,base=manifest['base'],build='validation/pacing-mesa-result.json')
info['components']['xorg']=dict(version=xr['sources']['xserver']['version'],source_commit=head,build='validation/pacing-xorg-result.json',compiled_units=xr['compiled_units'],evdev=xr['sources']['evdev']['version'])
info['frontend_flush']=dict(api=1,egl='normalized drawable and ancillary flags',glx='caller flags preserved',resolve='frontend callback after drawable preparation; one native fence',rejected_swap='negative result; error reported once')
info['demand_allocation']=dict(default=True,control='--lazy-slots 0',initial_images=1,maximum_images_per_generation=3,maximum_generations=3,actual_backing_limit_bytes=512*1024*1024,retirement='native completion plus Present COMPLETE and IDLE')
info['pacing']=dict(repaint_reserve_us=8000,synchronized_pending_default=1,reprime_msc_lead=1,storage_retirement='complete plus idle',legacy_control='--paced-queue 0 --paced-lead 2',special_events='queued-only general drain; single socket reader',physical_4k60_tested=False)
for name,entry in info['artifacts'].items():
    p=out/name
    if p.is_file():entry.update(sha256=sha(p),size=p.stat().st_size)
info['artifacts']['bin/glxgears']=dict(sha256=sha(out/'bin/glxgears'),size=(out/'bin/glxgears').stat().st_size)
(out/'build-info.json').write_text(json.dumps(info,indent=2)+'\n')
(out/'PACING-20261008.md').write_text('''KGSL HDMI pacing candidate, 2026-10-08

Launch ./run-agent.sh from this folder after stopping the existing agent and
following the normal unplug/arm procedure. Defaults remain BCDEF, continuous,
async TearFree, hardware blits, consumer release fencing and the timing companion.
USB/Bluetooth input, kernel companion, native broker and companion are preserved. The matched private Xorg additionally
collects desktop damage until a frame-timed repaint deadline with an 8 ms reserve.
It uses real flip timestamps and the current mode period; native GPU fences and
DRM completion still control image readiness and ownership. --repaint-us 0 disables
that scheduling policy for comparison; --repaint-us accepts 0 through 20000.
EGL and GLX now normalize the integrated resolve through their own frontend\nflush adapter. EGL's zero loader flags become drawable/ancillary flags; GLX\nretains caller flags. The final native fence still covers rendering and resolve.\nRejected HDMI swaps return failure to EGL instead of reporting successful paint.\n\nShared destinations now begin with one image. Additional images are initialized only\nwhen every existing image is still owned. Existing complete-plus-idle retirement\nand the 512 MiB actual backing limit remain enforced. --lazy-slots 0 selects\nthe three-image eager control. This value survives sudo escalation. This change\nreduces allocation cost; it does not claim seamless active resizing.\n\nThe complete Mesa and private Xorg stacks were rebuilt together on root@192.168.104.201.

The low-latency profile now permits one pending synchronized frame. Displayed
scanout remains pinned until IDLE but does not block producing its replacement.
Normal interval-zero swaps retain their original bound. No producer fence, consumer
fence or FIFO ordering is removed. The fifo profile preserves the older policy.
The --paced-queue 0 --paced-lead 2 controls restore the prior synchronized policy
and survive sudo escalation. Queue bounds 1-3 and MSC lead 1-2 remain independent.

Queued-only general XCB drains prevent reading a new Present completion into
its special queue just before sleeping on an emptied socket. This fixed the
observed 100 ms notification stall without reducing poll timeouts.

The bundled bin/glxgears drains animated events while preserving paused redraws.
A matching /usr/local/bin/glxgears was installed; /usr/bin/glxgears remains intact.
The source patch is in source/hdmi/patches/mesa-demos.

All raw trace, screenshot and temporary game profiles remain in RAM-backed /tmp.
Game settings and demo assets were not changed. See the source experiment reports
for measured scope; 4K60 and broad browser usability remain unverified.

Rollback uses the prior interop-capacity Downloads folder and its matched Mesa.
No Magisk install is needed for this graphics update. Do not run two agents at once.
''')
(out/'README.md').write_text((out/'PACING-20261008.md').read_text())
(out/'ACTIVE-SESSION.md').write_text('''This folder is a self-contained candidate. Current runtime selection and rollback
are recorded by the deployment script in RAM and in the final report. The existing
agent may still name the original resolve-order folder through a reversible bind
mount. Reboot removes that mount. Keep the source and original folders intact.
''')
subprocess.run(['bash','-n',str(out/'run-agent.sh')],check=True)
files=sorted(p for p in out.rglob('*') if p.is_file() and p!=out/'SHA256SUMS')
(out/'SHA256SUMS').write_text(''.join(sha(p)+'  '+str(p.relative_to(out))+'\n' for p in files))
subprocess.run(['sha256sum','--strict','-c','SHA256SUMS'],cwd=out,check=True,stdout=subprocess.DEVNULL)
print(json.dumps(dict(output=str(out),mesa_commit=commit,hdmi_commit=head),indent=2))
