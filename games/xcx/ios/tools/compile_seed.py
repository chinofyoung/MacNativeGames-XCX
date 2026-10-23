#!/usr/bin/env python3
"""Compile the shader seed's MSL into device metallibs and refresh its manifest.

Restored from the original project's games/totk/ios/tools/compile_seed.py. For Xenoblade Chronicles X the seed is the set
of shader variants the Mac has already translated (build_ipa.py collect_seed); it may be empty, and then there is nothing
to compile. Without Xcode's Metal toolchain (xcodebuild -downloadComponent MetalToolchain) nothing is compiled either:
the seed keeps its MSL sources and the device compiles each one the first time it is used (nvn_mtl.m shader_load), which
is slower but draws the same.

usage: compile_seed.py <seed folder> <sdk: iphoneos | iphonesimulator>
"""
import concurrent.futures, hashlib, json, os, shutil, subprocess, sys
from pathlib import Path

# The options of totk_precise_opts() in nvn_mtl.m: the translated shaders need exactly rounded float arithmetic. The
# offline compiler defaults to fast math ("Default is 'fast'"), which breaks their float integer-division idioms.
METAL_FLAGS=['-fmetal-math-mode=safe']


def metal_version(sdk):
    """The offline Metal compiler's version text, or None when the Metal toolchain is not installed."""
    r=subprocess.run(['xcrun','-sdk',sdk,'metal','--version'],capture_output=True,text=True)
    return r.stdout if r.returncode==0 else None


def main():
  seed=Path(sys.argv[1]).resolve(); sdk=sys.argv[2]
  manifest=json.loads((seed/'seed-manifest.json').read_text())
  if manifest.get('chain') is None: raise SystemExit('invalid shader seed manifest')
  files=sorted(n for n in manifest.get('files',{}) if n.endswith('_b.metal'))
  if not files:
      print('the shader seed is empty: no iOS metallibs to compile'); return
  version=metal_version(sdk)
  if version is None:
      manifest['ios_metallibs']=0; manifest['ios_metal_flags']='none'
      (seed/'seed-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
      print(f'no Metal toolchain (xcodebuild -downloadComponent MetalToolchain): the {len(files)} seed shaders stay MSL '
            'source and the device compiles each one on first use'); return
  # A library is kept by the hash of its source, the flags and the compiler (<seed>.ios-metallibs/): most of them are the
  # same in the next build.
  cache=seed.with_name(seed.name+'.ios-metallibs'); cache.mkdir(exist_ok=True)
  reused=[0]
  def compile_one(name):
    src=seed/name; air=seed/(name+'.air'); lib=seed/(name.removesuffix('.metal')+'.metallib')
    key=hashlib.sha256(src.read_bytes()+'\0'.join([sdk,version,*METAL_FLAGS]).encode()).hexdigest()
    kept=cache/(key+'.metallib')
    if kept.is_file():
        lib.unlink(missing_ok=True); shutil.copyfile(kept,lib); reused[0]+=1
    else:
        r=subprocess.run(['xcrun','-sdk',sdk,'metal',*METAL_FLAGS,'-c',str(src),'-o',str(air)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        if r.returncode:   # the device then compiles the MSL source itself, as for a shader without a library
            air.unlink(missing_ok=True); return None
        subprocess.run(['xcrun','-sdk',sdk,'metallib',str(air),'-o',str(lib)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        air.unlink()
        tmp=cache/(key+f'.{os.getpid()}.tmp'); shutil.copyfile(lib,tmp); os.replace(tmp,kept)
    return lib.name,hashlib.sha256(lib.read_bytes()).hexdigest()
  results=[]; failed=0
  with concurrent.futures.ThreadPoolExecutor(max_workers=min(8,os.cpu_count() or 4)) as pool:
    for i,result in enumerate(pool.map(compile_one,files),1):
        if result: results.append(result)
        else: failed+=1
        if i%100==0: print(f'compiled {i}/{len(files)} iOS metallibs',flush=True)
  for name,digest in results: manifest['files'][name]=digest
  manifest['ios_metallibs']=len(results)
  manifest['ios_metal_flags']=' '.join(METAL_FLAGS)   # build_ipa.py reuses the libraries only when these match
  (seed/'seed-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
  print(f'compiled {len(results)} iOS metallibs ({reused[0]} from {cache.name})'
        + (f'; {failed} did not compile offline and stay MSL source' if failed else ''))

if __name__=='__main__': main()
