#!/usr/bin/env python3
"""Package offline experimental artifacts and an exact Candidate B rollback.

Never installs a module, loads a .ko, or accesses the running display. Android
vendor components and the verified redesigned APK come from the immutable B
package, and their hashes are checked before and after copying.
"""
import argparse, hashlib, importlib.util, json, pathlib, shutil, subprocess, tarfile
P=pathlib.Path
spec=importlib.util.spec_from_file_location('package_b',P(__file__).with_name('package-candidate-b.py'))
helpers=importlib.util.module_from_spec(spec);spec.loader.exec_module(helpers)
def sha(p):
 with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def main():
 ap=argparse.ArgumentParser(description=__doc__)
 ap.add_argument('source',type=P);ap.add_argument('baseline_module',type=P)
 ap.add_argument('runtime',type=P);ap.add_argument('output',type=P)
 a=ap.parse_args();source,baseline,runtime=[p.resolve() for p in (a.source,a.baseline_module,a.runtime)]
 info=json.loads((runtime/'build-info.json').read_text())
 identity=json.loads((runtime/'companion/manifest.json').read_text())
 old=json.loads((baseline/'build-info.json').read_text())
 helpers.verify_sums(runtime);helpers.verify_sums(runtime/'companion')
 assert info['experimental'] and not info['validation']['physical_display_tested']
 assert info['validation']['repository_tests']=='PASS'
 assert info['validation']['copy_pixel_checks']==3744 and info['validation']['shared_image_checks']==60
 assert identity['kernel_commit']=='d00ba216ccda5d4fcc0d864729ae69d5b63d860c'
 assert info['components']['mesa']['commit'].startswith('ab345293d')
 vendor={}
 for line in (baseline/'patched-checksums.list').read_text().splitlines():
  relative,expected=line.split('|');assert relative.startswith('vendor/') and '..' not in P(relative).parts
  assert sha(baseline/relative)==expected
  vendor[relative]=expected
 assert len(vendor)==3
 # Preserve the redesigned, already-verified APK exactly; no app replacement.
 assert sha(baseline/'apk/HdmiLosTile.apk')==old['artifacts']['HdmiLosTile.apk']['sha256']
 a.output.mkdir(parents=True,exist_ok=False)
 module=a.output/'module';rollback=a.output/'rollback'
 shutil.copytree(baseline,module);shutil.copytree(baseline,rollback)
 for root in (module,rollback):
  for name in ('compatible.ok','diagnostic-only'):(root/name).unlink(missing_ok=True)
 shutil.copy2(runtime/'android/hdmi-losd',module/'bin/hdmi-losd')
 shutil.rmtree(module/'companion');shutil.copytree(runtime/'companion',module/'companion')
 for name in ('service.sh','customize.sh','companion-loader.sh'):
  shutil.copy2(source/'module'/name,module/name);(module/name).chmod(0o755)
 (module/'timing.env').write_text(''.join(f"{key}='{value}'\n" for key,value in [
  ('EXPECTED_RELEASE',identity['kernel_release']),('EXPECTED_CONFIG_SHA256',identity['runtime_config_sha256']),('EXPECTED_BUILD_ID',identity['build_id'])]))
 commit=info['components']['kernel']['source_commit'];label='bcdef-'+commit[:12]
 (module/'module.prop').write_text('id=hdmi-los\nname=HDMI experimental BCDEF graphics\nversion=0.4-'+label+'\nversionCode=20261005\nauthor=KiralyCraft\ndescription=Experimental additive timing/presenter companion and matched BCDEF runtime. Live testing pending.\n')
 shutil.rmtree(module/'runtime');(module/'runtime').mkdir()
 archive=module/'runtime'/f'{label}.tar.gz'
 with tarfile.open(archive,'w:gz',dereference=False) as f:f.add(runtime,arcname='candidate-bcdef')
 previous=module/'previous-build-info.json';shutil.copy2(baseline/'build-info.json',previous)
 packaged=dict(old);packaged.update(experimental=True,companion=identity,runtime_archive=str(archive.relative_to(module)),bcdef_runtime=info,
  preserved_vendor_payloads=vendor,magisk_installed=False,boot_activation='pending',packaging_source_commit=info['source_commit'])
 packaged['artifacts']={**old['artifacts'],'hdmi-losd':{'sha256':sha(module/'bin/hdmi-losd'),'repository_commit':info['components']['native']['source_commit']}}
 (module/'build-info.json').write_text(json.dumps(packaged,indent=2)+'\n')
 for root in (module,rollback):
  for relative,expected in vendor.items():assert sha(root/relative)==expected
  assert sha(root/'apk/HdmiLosTile.apk')==sha(baseline/'apk/HdmiLosTile.apk')
  helpers.write_sums(root)
 candidate=a.output/f'hdmi-los-{label}-magisk.zip';back=a.output/f'hdmi-los-{label}-candidate-b-rollback.zip'
 helpers.make_zip(module,candidate);helpers.make_zip(rollback,back)
 result={'candidate':{'name':candidate.name,'sha256':sha(candidate)},'rollback':{'name':back.name,'sha256':sha(back)},'installed':False,'kernel_loaded':False}
 (a.output/'package-manifest.json').write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps(result,indent=2))
if __name__=='__main__':main()
