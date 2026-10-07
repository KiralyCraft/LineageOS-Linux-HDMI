#!/usr/bin/env python3
"""Build a restartable comparison folder; never installs or starts a session."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


def sha(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f,'sha256').hexdigest()


def configure_launcher(text):
    assert '--present-release' not in text
    text=text.replace('TEARFREE_COMPLETION=async\n','TEARFREE_COMPLETION=async\nPRESENT_RELEASE=fence\nMESA_QUEUE=low-latency\n',1)
    text=text.replace('[--no-timeout|--timeout]', '[--present-release fence|finish|legacy] [--mesa-queue low-latency|fifo] [--no-timeout|--timeout]',1)
    anchor='        --capture)'
    assert anchor in text
    text=text.replace(anchor,'''        --present-release)
            (($# >= 2)) || { usage; exit 2; }
            PRESENT_RELEASE=$2
            shift 2
            ;;
        --mesa-queue)
            (($# >= 2)) || { usage; exit 2; }
            MESA_QUEUE=$2
            shift 2
            ;;
'''+anchor,1)
    anchor='if ((EUID != 0)); then'
    text=text.replace(anchor,'''[[ $PRESENT_RELEASE == fence || $PRESENT_RELEASE == finish || $PRESENT_RELEASE == legacy ]] || { usage; exit 2; }
[[ $MESA_QUEUE == low-latency || $MESA_QUEUE == fifo ]] || { usage; exit 2; }
if [[ $PRESENT_RELEASE != legacy && $XORG_ACCEL != kgsl-kms-bridge ]]; then
    printf 'Consumer release fencing requires --xorg-accel kgsl-kms-bridge\\n' >&2
    exit 2
fi
'''+anchor,1)
    anchor='    if ((NO_TIMEOUT)); then'
    text=text.replace(anchor,'    args+=(--present-release "$PRESENT_RELEASE" --mesa-queue "$MESA_QUEUE")\n'+anchor,1)
    anchor='# Select experiments explicitly; inherited shell variables cannot mix profiles.'
    text=text.replace(anchor,'''export HDMI_LOS_PRESENT_RELEASE="$PRESENT_RELEASE"
export MESA_KGSL_HDMI_QUEUE="$MESA_QUEUE"
if [[ $PRESENT_RELEASE != legacy ]]; then
    LC_ALL=C grep -aFq 'HDMI_LOS_XORG_RELEASE_ABI=1' "$BUNDLE/lib/xorg/modules/drivers/modesetting_drv.so" &&
    LC_ALL=C grep -aFq 'present_set_copy_release' "$BUNDLE/libexec/Xorg" || {
        printf 'Missing matched Xorg consumer-release support\\n' >&2; exit 1;
    }
fi
if [[ $MESA_QUEUE == low-latency ]]; then
    LC_ALL=C grep -aFq 'HDMI_LOS_MESA_QUEUE_ABI=1' "$BUNDLE/lib/mesa/libgallium-26.2.0-devel.so" || {
        printf 'Missing matched Mesa queue support\\n' >&2; exit 1;
    }
fi
printf 'HDMI presentation: release=%s queue=%s\\n' "$PRESENT_RELEASE" "$MESA_QUEUE" >&2

'''+anchor,1)
    return text


def verify(root):
    for line in (root/'SHA256SUMS').read_text().splitlines():
        digest,name=line.split('  ',1)
        assert sha(root/name)==digest,name


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    for name in ('base','mesa-build','mesa-source','xorg-build','repository','output'):
        ap.add_argument('--'+name,type=Path,required=True)
    a=ap.parse_args()
    base,mb,ms,xb,repo,out=(getattr(a,n).resolve() for n in ('base','mesa_build','mesa_source','xorg_build','repository','output'))
    assert not out.exists()
    verify(base)
    old=json.loads((base/'build-info.json').read_text())
    mesa=json.loads((mb/'result.json').read_text());xorg=json.loads((xb/'result.json').read_text())
    commit=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
    assert mesa['commit']==subprocess.check_output(['git','-C',str(ms),'rev-parse','HEAD'],text=True).strip()
    assert not subprocess.check_output(['git','-C',str(ms),'diff','HEAD','--','src'],text=True)
    for name,digest in xorg['artifacts'].items():assert sha(xb/name)==digest,name
    for name,digest in mesa['files'].items():assert sha(mb/'stage'/name)==digest,name
    shutil.copytree(base,out,symlinks=True)
    # Preserve earlier acceptance evidence as historical, not current validation.
    (out/'validation').rename(out/'validation-before-presentation-fix')
    (out/'validation').mkdir()
    for src,dst in [('Xorg','libexec/Xorg'),('libglamoregl.so','lib/xorg/modules/libglamoregl.so'),('modesetting_drv.so','lib/xorg/modules/drivers/modesetting_drv.so')]:
        shutil.copy2(xb/src,out/dst)
    libs={'libgallium-26.2.0-devel.so':'libgallium-26.2.0-devel.so','libGLX_mesa.so.0.0.0':'libGLX_mesa.so.0','libEGL_mesa.so.0.0.0':'libEGL_mesa.so.0','libgbm.so.1.0.0':'libgbm.so.1','dri/libdril_dri.so':'libdril_dri.so','gbm/dri_gbm.so':'gbm/dri_gbm.so'}
    for src,dst in libs.items():shutil.copy2(mb/'stage/usr/lib'/src,out/'lib/mesa'/dst)
    (out/'run-agent.sh').write_text(configure_launcher((base/'run-agent.sh').read_text()))
    subprocess.run(['bash','-n',str(out/'run-agent.sh')],check=True)
    for file,marker in [('libexec/Xorg',b'present_set_copy_release'),('lib/xorg/modules/drivers/modesetting_drv.so',b'HDMI_LOS_XORG_RELEASE_ABI=1'),('lib/xorg/modules/libglamoregl.so',b'HDMI_LOS_XORG_COPY_ABI=1'),('lib/mesa/libgallium-26.2.0-devel.so',b'HDMI_LOS_MESA_QUEUE_ABI=1')]:
        assert marker in (out/file).read_bytes(),file
    for name in ('bin/hdmi-los-agent','bin/hdmi-input-bridge','companion/hdmi_companion.ko'):
        assert sha(out/name)==sha(base/name),name
    assert (out/'lib/mesa/kgsl_dri.so').samefile(out/'lib/mesa/libdril_dri.so')
    for src,dst in [(mb/'result.json','mesa-build.json'),(mb/'source-verification.json','mesa-source-verification.json'),(xb/'result.json','xorg-build.json')]:
        shutil.copy2(src,out/'validation'/dst)
    for name in ('queue-unit.log','release-unit.log'):
        shutil.copy2(mb/name,out/'validation'/name)
    # Retain old native/kernel archives; add the exact current graphics sources.
    with (out/'source/mesa/queue-on-e04c1852d.patch').open('wb') as f:
        subprocess.run(['git','-C',str(ms),'diff','e04c1852d',mesa['commit']],stdout=f,check=True)
    with (out/'source/mesa/experiment-on-daa6e56de.patch').open('wb') as f:
        subprocess.run(['git','-C',str(ms),'diff','daa6e56de',mesa['commit']],stdout=f,check=True)
    hdmi=out/'source/hdmi'
    shutil.rmtree(hdmi)
    hdmi.mkdir()
    with subprocess.Popen(['git','-C',str(repo),'archive',commit], stdout=subprocess.PIPE) as archive:
        subprocess.run(['tar','-xf','-','-C',str(hdmi)],stdin=archive.stdout,check=True)
        assert archive.wait()==0
    (out/'source/xorg-built-series').write_text(''.join(p['name']+'\n' for p in xorg['patches']))
    for p in xorg['patches']:
        assert sha(repo/'patches/xserver'/p['name'])==p['sha256']
    (out/'source/mesa/README.md').write_text('Mesa '+mesa['commit']+'\nThe baseline/coexistence patches are retained. experiment-on-daa6e56de.patch includes C/D, resize cache and queue policy. queue-on-e04c1852d.patch is only the newest queue change.\n')
    info=old
    info.update(schema=5,candidate='BCDEF-consumer-release-and-queue',source_commit=commit,default_profile='BCDEF',experimental=True)
    info['components']['mesa'].update(commit=mesa['commit'],build='validation/mesa-build.json')
    info['components']['xorg'].update(source_commit=commit,build='validation/xorg-build.json',compiled_units=xorg['compiled_units'])
    info['validation']={'offline_tests':'PASS; see queue-unit.log and release-unit.log','physical_display_tested':False,'gpu_visibility_tested':False,'installed':False,'display_restarted':False}
    info['comparison']={'base_bundle':str(base),'E_copy_abi':1,'default_release':'fence','default_queue':'low-latency','kernel_native_input_unchanged':True,'new_magisk_module_required':False}
    changed=[]
    for relative,entry in info['artifacts'].items():
        path=out/relative;digest=sha(path)
        if digest!=entry['sha256']:changed.append(relative)
        entry.update(sha256=digest,size=path.stat().st_size)
    info['changed_compiled_artifacts']=[n for n in changed if n!='run-agent.sh']
    (out/'build-info.json').write_text(json.dumps(info,indent=2)+'\n')
    (out/'control-info.json').write_text(json.dumps(info['comparison'],indent=2)+'\n')
    (out/'README.md').write_text('''BCDEF consumer-release and queue comparison, 2026-10-07

Run ./run-agent.sh (BCDEF, continuous session, both fixes enabled).
Wait for the normal unplug/arm/reconnect/Mirror sequence when switching agents.
This folder is prepared for testing; it has NOT been validated on HDMI.
No Magisk installation or kernel change is needed.

Controls:
  ./run-agent.sh --mesa-queue fifo                   # old queue policy
  ./run-agent.sh --present-release finish           # blocking diagnostic
  ./run-agent.sh --present-release legacy           # old release behavior
  ./run-agent.sh --candidate BCDF                   # E disabled

The default release=fence delays IdleNotify until Xorg GPU copy completion.
It preserves the separate Present completion path and direct-scanout retirement.
The default queue=low-latency caps ordinary interval-zero frames at two across
resize generations, preserves FIFO, and submits those frames without future
MSC targets. Interval-controlled scheduling remains unchanged. No accepted
frames are discarded. Explicit MSC/force-copy calls bypass the new queue policy;
this change does not add support for those APIs to the existing HDMI pipeline.
The newer Mesa resize cache is included. E remains the original ABI 1 path.

source/xorg-built-series is authoritative for the compiled patch selection:
0001 through 0007 plus 0010. The full repository also contains E ABI 2 diagnostic
patches; these were NOT compiled into this comparison. Prior acceptance data
is under validation-before-presentation-fix and does not validate this build.

Rollback: use the preserved bcdef-controls-20261005 folder after normal HDMI
release/unplug. The native broker, companion and USB/Bluetooth bridge are
unchanged. SHA256SUMS covers the complete folder.
''')
    files=sorted(p for p in out.rglob('*') if p.is_file() and p!=out/'SHA256SUMS')
    (out/'SHA256SUMS').write_text(''.join(sha(p)+'  '+str(p.relative_to(out))+'\n' for p in files))
    verify(out)
    print(json.dumps({'output':str(out),'mesa':mesa['commit'],'hdmi':commit,'files':len(files),'physical_tested':False},indent=2))

if __name__=='__main__':main()
