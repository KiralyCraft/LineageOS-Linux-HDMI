#!/usr/bin/env python3
"""Update only the broker in the verified BCDEF Magisk package; never install."""
import argparse,hashlib,json,pathlib,shutil,stat,subprocess,tempfile,zipfile
P=pathlib.Path
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 ap=argparse.ArgumentParser(description=__doc__)
 for name in ['source','baseline_zip','build','output']:ap.add_argument(name,type=P)
 a=ap.parse_args();source=a.source.resolve();build=a.build.resolve()
 manifest=json.loads((build/'manifest.json').read_text())
 assert manifest['validation']['host_lifecycle']=='PASS with ASan UBSan and LSan'
 for name,want in manifest['sources'].items():assert sha(source/name)==want,name
 for name,want in manifest['artifacts'].items():assert sha(build/name)==want,name
 commit=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
 assert not subprocess.check_output(['git','-C',str(source),'status','--porcelain','--',*manifest['sources'],
                                    'docs/CONNECTED_RESTART.md','build-support/package-connected-restart.py'],text=True)
 a.output.mkdir(parents=True,exist_ok=False);root=a.output/'module';root.mkdir()
 with zipfile.ZipFile(a.baseline_zip) as z:
  assert z.testzip() is None
  for entry in z.infolist():
   path=P(entry.filename)
   assert not path.is_absolute() and '..' not in path.parts
   assert not stat.S_ISLNK(entry.external_attr>>16)
   z.extract(entry,root)
   if not entry.is_dir():(root/path).chmod((entry.external_attr>>16)&0o777 or 0o644)
 subprocess.run(['sha256sum','--strict','-c','SHA256SUMS'],cwd=root,check=True,stdout=subprocess.DEVNULL)
 original={str(p.relative_to(root)):sha(p) for p in root.rglob('*') if p.is_file()}
 assert (root/'module.prop').read_text().startswith('id=hdmi-los\n')
 for name in ['compatible.ok','diagnostic-only']:(root/name).unlink(missing_ok=True)
 shutil.copy2(build/'hdmi-losd',root/'bin/hdmi-losd');(root/'bin/hdmi-losd').chmod(0o755)
 shutil.copy2(source/'docs/CONNECTED_RESTART.md',root/'README.txt')
 (root/'module.prop').write_text('id=hdmi-los\nname=HDMI BCDEF with connected session restart\n'
  f'version=0.4.1-restart-{commit[:12]}\nversionCode=202610071\nauthor=KiralyCraft\n'
  'description=Restart or switch the Linux runtime without unplugging HDMI; preserve negotiated timing and restore Android on failure.\n')
 info=json.loads((root/'build-info.json').read_text())
 info['artifacts']['hdmi-losd']={'sha256':sha(root/'bin/hdmi-losd'),'repository_commit':commit}
 info.update(packaging_source_commit=commit,connected_restart=dict(build=manifest,installed=False,
  baseline_zip_sha256=sha(a.baseline_zip),physical_test='pending',protocol_version=3,
  preserved=['composer payloads','kernel companion','redesigned APK','BCDEF runtime','CPU power module']))
 (root/'build-info.json').write_text(json.dumps(info,indent=2)+'\n')
 permitted={'bin/hdmi-losd','README.txt','module.prop','build-info.json','SHA256SUMS','compatible.ok','diagnostic-only'}
 for name,want in original.items():
  if name not in permitted:assert sha(root/name)==want,name
 # Magisk deletes installer-only paths after sourcing customize.sh. Runtime
 # checksums cover exactly the payload retained after that cleanup.
 retained=[p for p in sorted(root.rglob('*')) if p.is_file() and p.name!='SHA256SUMS'
  and str(p.relative_to(root)) not in ('customize.sh','README.md','system/placeholder')
  and not p.relative_to(root).parts[0].startswith('.git')]
 (root/'SHA256SUMS').write_text(''.join(f'{sha(p)}  {p.relative_to(root)}\n' for p in retained))
 subprocess.run(['sha256sum','--strict','-c','SHA256SUMS'],cwd=root,check=True,stdout=subprocess.DEVNULL)
 target=a.output/f'hdmi-los-connected-restart-{commit[:12]}-magisk.zip'
 with zipfile.ZipFile(target,'x',compression=zipfile.ZIP_DEFLATED) as z:
  for p in sorted(root.rglob('*')):
   if p.is_file():
    entry=zipfile.ZipInfo(str(p.relative_to(root)),date_time=(2026,10,7,0,0,0));entry.create_system=3
    entry.external_attr=p.stat().st_mode<<16;entry.compress_type=zipfile.ZIP_DEFLATED;z.writestr(entry,p.read_bytes())
 with zipfile.ZipFile(target) as z:assert z.testzip() is None
 with tempfile.TemporaryDirectory() as temp:
  trial=P(temp)
  with zipfile.ZipFile(target) as z:z.extractall(trial)
  for name in ['customize.sh','README.md','system/placeholder']:(trial/name).unlink(missing_ok=True)
  subprocess.run(['sha256sum','--strict','-c','SHA256SUMS'],cwd=trial,check=True,stdout=subprocess.DEVNULL)
  names={line.split('  ',1)[1] for line in (trial/'SHA256SUMS').read_text().splitlines()}
  assert names=={str(p.relative_to(trial)) for p in trial.rglob('*') if p.is_file() and p.name!='SHA256SUMS'}
 (a.output/'SHA256SUMS').write_text(f'{sha(target)}  {target.name}\n')
 print(target)
if __name__=='__main__':main()
