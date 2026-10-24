#!/usr/bin/env python3
"""Prepare Xenoblade Chronicles X's shaders from the player's own verified RomFS, without a played cache.

Every NVN shader in the RomFS is found (xcx_shaders.py inventory, docs/shaders-in-romfs.md) and translated for the
default bind state; then every bind state the shared shader list (assets/shader_list.tsv, shader_list.py) names for a
shader of this game version is translated too, so an iPhone or iPad, which cannot translate while playing, carries
what players have met. A listed shader the RomFS does not hold (the other game version's) is skipped. The 2 geometry
shaders have no translation chain and are left out.

The output is the runtime's shader cache layout (<variant>_b.metal and _b.json per variant, <shader>.variants per
shader), which build_app.py's iOS seed reads like the Mac's own cache. It holds game-derived data and belongs in the
work folder (<work>/build/shader-prep/rt-<chain>), never in the repo. Beside it: prep-stamp.json (what the last
preparation was made from: an unchanged RomFS, list, translator and script return at once), rt-<chain>.translations
(each job's result, so a grown list translates only its new rows) and rt-<chain>.partial while it runs.

Ported from the original project's games/totk/scripts/prepare_shader_seed.py (prepare, plan, TranslationCache,
translation_request, reusable_seed, builtin_serves); XCX needs no startup packages, locators, dictionaries or rewritten
control sections: every shader found is translated for the default bind state (the original's all_default).
usage: prepare_shader_seed.py --romfs <work>/extracted/<v> --output <dir> --translator <totk_xlate>
                              --spirv-cross <spirv-cross> --version <v> [--list <shader_list.tsv>]
"""
import argparse
import concurrent.futures
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shader_list import ShaderList   # noqa: E402
from xcx_shaders import inventory     # noqa: E402

GAME = Path(__file__).resolve().parents[1]   # games/xcx


def chain():
    """The translation chain the runtime names its cache after (third_party/d2r_gfx/gfx/nvn/dmsh_chain.h)."""
    for line in (GAME/'third_party/d2r_gfx/gfx/nvn/dmsh_chain.h').read_text().splitlines():
        if line.startswith('#define DMSH_CHAIN'):
            return line.split('"')[1]
    raise RuntimeError('DMSH_CHAIN not found')


CHAIN = chain()
MIN_SHADERS = 12000   # of the 12,427 translatable shaders of v101 / v200; fewer means the RomFS or translator is wrong
ANSWERS = dict(tex={}, img={}, attr={}, fout={})
DEFAULTS = dict(tex='2d', img={}, attr='float', fout='float')
SCHEMA = 1


def suffix(answers):
    return hashlib.sha256(json.dumps(answers, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:12]


SUFFIX = suffix(ANSWERS)


def deps_match(deps, answers):
    """Whether a variant translated with deps serves these answers (the original's totk_shader_rt.m deps_match). "*" /
    "*_depth": any texture kind, without / with a depth format; an unanswered vertex input is unbound."""
    for kind, default in DEFAULTS.items():
        have = answers.get(kind, {})
        for slot, want in deps.get(kind, {}).items():
            if slot not in have and kind == 'attr':
                continue
            got = have.get(slot, default)
            if want in ('*', '*_depth'):
                if not isinstance(got, str) or got.endswith('_depth') != (want == '*_depth'):
                    return False
            elif got != want:
                return False
    return True


def answers_for(deps):
    """The bind state to translate a listed row with: its deps, a "*" slot answered as the plain 2D texture it stands for."""
    answers = {**ANSWERS, **deps}
    answers['tex'] = {slot: {'*': '2d', '*_depth': '2d_depth'}.get(kind, kind) for slot, kind in answers['tex'].items()}
    return answers


def digest(data):
    return hashlib.sha256(data).hexdigest()


def romfs_sha256(romfs_dir):
    """The verified RomFS's SHA-256 from index.json, in <dir> or <dir>/romfs (extract_romfs.py writes the latter)."""
    for d in (Path(romfs_dir), Path(romfs_dir)/'romfs'):
        if (d/'index.json').is_file():
            index = json.loads((d/'index.json').read_text())
            if index.get('status') != 'pass':
                raise ValueError('RomFS has not passed integrity verification')
            if not re.fullmatch('[0-9a-f]{64}', str(index.get('romfs_sha256'))):
                raise ValueError('index.json has no RomFS hash')
            return index['romfs_sha256']
    raise FileNotFoundError(f'no index.json in {romfs_dir} or {romfs_dir}/romfs')


def file_identity(path):
    """What changes whenever a file's bytes may have: device, inode, size, modification and change time."""
    s = os.stat(path)
    return [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns]


# <output>.verified.json: each output file's identity and the SHA-256 it had when it was last checked, so an unchanged
# preparation does not read every file again.
def verified_digests(output):
    """name -> SHA-256 for the files of output whose identity is the one recorded with it."""
    try:
        record = json.loads(output.with_name(output.name+'.verified.json').read_text())
        if record.get('schema') != 1:
            return {}
        known = {}
        for name, entry in record['files'].items():
            try:
                if Path(name).name == name and entry[:-1] == file_identity(output/name):
                    known[name] = entry[-1]
            except OSError:
                pass
        return known
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}


def record_verified(output, files):
    """Record the identity of output's files with the digests they were just checked or written with."""
    record = dict(schema=1, files={name: file_identity(output/name)+[sha] for name, sha in sorted(files.items())})
    path = output.with_name(output.name+'.verified.json')
    path.with_name(path.name+'.tmp').write_text(json.dumps(record, separators=(',', ':')))
    path.with_name(path.name+'.tmp').replace(path)


def stamp_path(output):
    return output.parent/'prep-stamp.json'


def reusable_seed(output, identity):
    """The result of the last preparation when it was made from the same inputs (prep-stamp.json) and its files are
    still the ones it wrote (seed-manifest.json); None when it must be prepared again."""
    try:
        stamp = json.loads(stamp_path(output).read_text())
        manifest = json.loads((output/'seed-manifest.json').read_text())
        if stamp.get('identity') != identity or manifest.get('identity') != identity:
            return None
        files = manifest['files']
        known = verified_digests(output)
        if not all((output/name).is_file() and (known.get(name) or digest((output/name).read_bytes())) == sha
                   for name, sha in files.items()):
            return None
        if len(known) != len(files):
            record_verified(output, files)
        return stamp['result']
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def translation_request(variant, stage, control, code, answers, code_path):
    """What the translator is asked for a job (the guest code written to code_path), as the runtime asks it
    (totk_rt_variant). A cached translation depends on this function's source (TranslationCache identity), not on the
    rest of this script: the variant name already names the shader's control and code and the bind state."""
    c1, length = struct.unpack_from('<II', control, 0x700)
    code_path.write_bytes(code[0 if stage == 'cs' else 0x30:length])
    request = dict(variant=variant, stage=stage, code=str(code_path.resolve()), c1=code[c1:length].hex(),
                   depth_minus_w=True, **answers)
    if stage == 'cs':
        request['local'] = list(struct.unpack_from('<III', control, 0x748))
    return request


class TranslationCache:
    """Each translation job's result by variant name (<key>-<suffix of its bind state>) in <folder>: <variant>_b.metal and
    _b.json as the service wrote them (hard links to the output's own files, which nothing writes again), or
    <variant>.failed with the service's error. cache.json names what a result depends on besides the job (the chain, the
    translator, SPIRV-Cross, translation_request); a different identity empties the folder. Entries that no job of the
    last preparation asked for are removed after it. digests.json keeps the SHA-256 of each file, so the seed manifest
    does not read thousands of unchanged files again."""

    def __init__(self, folder, identity):
        self.folder = folder
        try:
            same = json.loads((folder/'cache.json').read_text()) == identity
        except (OSError, ValueError):
            same = False
        if not same:
            shutil.rmtree(folder, ignore_errors=True)
            folder.mkdir(parents=True)
            (folder/'cache.json').write_text(json.dumps(identity))
        try:
            self.digests = json.loads((folder/'digests.json').read_text()) if same else {}
        except (OSError, ValueError):
            self.digests = {}

    def take(self, variant, staging):
        """The service's reply for a job translated before (its files linked into staging), or None. A staging file that
        is already the cached one (the previous output's, kept by prepare) is left as it is."""
        failed = self.folder/(variant+'.failed')
        if failed.is_file():
            return dict(ok=False, error=json.loads(failed.read_text()).get('error'))
        files = [variant+'_b.metal', variant+'_b.json']
        try:
            cached = [os.stat(self.folder/name).st_ino for name in files]
        except FileNotFoundError:
            return None
        for name, inode in zip(files, cached):
            try:
                if os.stat(staging/name).st_ino == inode:
                    continue
                (staging/name).unlink()
            except FileNotFoundError:
                pass
            os.link(self.folder/name, staging/name)
        return dict(ok=True)

    def put(self, variant, reply, staging):
        if not reply.get('ok'):
            (self.folder/(variant+'.failed')).write_text(json.dumps(dict(error=reply.get('error'))))
            return
        for name in (variant+'_b.metal', variant+'_b.json'):
            (self.folder/name).unlink(missing_ok=True)
            os.link(staging/name, self.folder/name)
            self.digests[name] = digest((staging/name).read_bytes())

    def digest(self, name, path):
        """The SHA-256 of a cached file that path is a link to (take / put made it one)."""
        if name not in self.digests:
            self.digests[name] = digest(path.read_bytes())
        return self.digests[name]

    def prune(self, variants):
        for p in self.folder.iterdir():
            for end in ('_b.metal', '_b.json', '.failed'):
                if p.name.endswith(end) and p.name[:-len(end)] not in variants:
                    p.unlink()
                    self.digests.pop(p.name, None)
        (self.folder/'digests.json').write_text(json.dumps(self.digests, separators=(',', ':')))


def builtin_variants(seed, key):
    """The variants a seed made from the game code alone for a shader: its default bind state, and with compare slots
    (shadow maps) the one with them bound to depth textures; [] when the seed has no default variant of it. A seed
    prepared with the shared list also holds listed variants of the shader; those are not built in."""
    index = Path(seed)/f'{key}.variants'
    try:
        rows = {r['variant']: r for r in json.loads(index.read_text()) if isinstance(r, dict) and 'variant' in r}
    except (OSError, ValueError):
        return []
    default = rows.get(key+'-'+SUFFIX)
    if not default:
        return []
    try:
        refl = json.loads((Path(seed)/f'{key}-{SUFFIX}_b.json').read_text())
    except (OSError, ValueError):
        refl = {}
    found = [default]
    if refl.get('compare_slots'):
        depth = answers_for({'tex': {str(s): '*_depth' for s in refl['compare_slots']}})
        if rows.get(key+'-'+suffix(depth)):
            found.append(rows[key+'-'+suffix(depth)])
    return found


def builtin_serves(seed, key, deps):
    """Whether the seed's built-in variants serve a shared-list row (key, deps), as the runtime would pick them: such a
    row is a default variant, which every build makes by itself (the list keeps non-default ones)."""
    answers = answers_for(json.loads(deps) if isinstance(deps, str) else deps)
    return any(deps_match(v.get('deps', {}), answers) for v in builtin_variants(seed, key))


def plan(shaders, listed):
    """Translation jobs (key, answers, variant, why): the default bind state for every shader found, then each bind state
    the shared list names for a shader found in this game version ('listed'; listed: (key, canonical deps) pairs). A
    listed state a variant made before already serves is not translated (prepare), and one whose translation gives the
    deps of an earlier variant is dropped, since only the translator knows a shader's deps. A default translation that
    compare-samples textures (shadow maps) adds a job with those slots bound to depth textures ('compare', prepare)."""
    jobs = [(key, ANSWERS, key+'-'+SUFFIX, 'default') for key in sorted(shaders)]
    for key, deps in sorted(listed):
        if key in shaders:
            answers = answers_for(json.loads(deps))
            jobs.append((key, answers, key+'-'+suffix(answers), 'listed'))
    return jobs


def summary_line(result):
    return (f"shader prep: found {result['shaders_found']}, default {result['default_translated']}, listed "
            f"{result['listed_rows']} (matched {result['listed_matched']}, translated {result['listed_translated']}, "
            f"other version {result['skipped_other_version']})" + (', reused' if result['reused'] else ''))


def prepare(romfs_dir, output, translator, spirv_cross, version, shader_list=None):
    """Translate every shader of the RomFS for the default bind state and the shared list's bind states for those found,
    into output (<work>/build/shader-prep/rt-<chain>). shader_list: the list file to use (None or a missing or empty
    file: the defaults only). Returns {shaders_found, default_translated, listed_rows, listed_matched, listed_translated,
    skipped_other_version, reused, variants, failed}; raises ValueError when fewer than MIN_SHADERS shaders translate."""
    romfs_dir, output, translator, spirv_cross = map(Path, (romfs_dir, output, translator, spirv_cross))
    shared = ShaderList.load(shader_list) if shader_list else ShaderList()
    identity = dict(schema=SCHEMA, chain=CHAIN, version=version, source_romfs_sha256=romfs_sha256(romfs_dir),
                    shader_list_sha256=shared.digest(), translator_sha256=digest(translator.read_bytes()),
                    spirv_cross_sha256=digest(spirv_cross.read_bytes()),
                    preparation_sha256=digest(Path(__file__).read_bytes()))
    old = reusable_seed(output, identity)
    if old is not None:
        return dict(old, reused=True)
    started = time.monotonic()
    stamp_path(output).unlink(missing_ok=True)
    found = inventory(romfs_dir)
    shaders = {key: s for key, s in found.items() if s.stage is not None}   # geometry: no translation chain
    listed = [(row[1], row[2]) for row in shared.rows() if row[0] == 'V']
    matched = sum(1 for key, _ in listed if key in shaders)
    print(f'found {len(shaders)} translatable shaders ({len(found) - len(shaders)} without a translation chain); '
          f'{matched} of {len(listed)} listed bind states are for shaders of this game version', flush=True)
    # The previous output becomes the staging folder: most of its files are this run's too (links into the translation
    # cache), so they stay where they are. Without its manifest it is not an output until this run writes one.
    staging = output.with_name(output.name+'.partial')
    if output.is_dir():
        if staging.exists():
            shutil.rmtree(staging)
        output.rename(staging)
    staging.mkdir(parents=True, exist_ok=True)
    (staging/'seed-manifest.json').unlink(missing_ok=True)
    translations = dict(schema=SCHEMA, chain=CHAIN, translator_sha256=identity['translator_sha256'],
                        spirv_cross_sha256=identity['spirv_cross_sha256'],
                        request_sha256=digest(inspect.getsource(translation_request).encode()))
    cache = TranslationCache(output.with_name(output.name+'.translations'), translations)
    jobs = plan(shaders, listed)
    # One translator service per worker. A shader's jobs (its default bind state, then its listed ones) run in order on
    # one service, because whether a listed job is translated and kept depends on that shader's earlier results; the
    # files each job writes are named after its own shader.
    by_key = {}
    for index, job in enumerate(jobs):
        by_key.setdefault(job[0], []).append(index)
    outcome = [None]*len(jobs)   # ('ok', variant) / ('failed', record) / None (served already or a duplicate of deps)
    derived = {}   # key -> results of the jobs a translation added (its compare-sampled slots bound to depth textures)
    lock = threading.Lock()
    pending = iter(by_key.items())
    done = [0, 0, 0]   # completed, failed, of them taken from the translation cache
    stop = threading.Event()   # a service failed: the others stop after their current shader

    def worker():
        process = None   # started at this worker's first job the translation cache does not hold
        try:
            while not stop.is_set():
                with lock:
                    item = next(pending, None)
                if item is None:
                    return
                key, indices = item
                stage, control, code, source = shaders[key][1:]
                mine = {}   # canonical deps -> variant: the shader's .variants rows
                queue = [(index, *jobs[index][1:]) for index in indices]
                while queue:
                    index, answers, variant, why = queue.pop(0)
                    if why != 'default' and any(deps_match(json.loads(d), answers) for d in mine):
                        continue   # a variant made already serves this bind state, as the runtime would pick it
                    reply = cache.take(variant, staging)
                    reused = reply is not None
                    if not reused:
                        if process is None:
                            process = subprocess.Popen([str(translator), '--service', '--spirv-cross', str(spirv_cross),
                                                        '--out', str(staging), '--chain', CHAIN],
                                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
                        code_path = staging/(variant+'.code')
                        request = translation_request(variant, stage, control, code, answers, code_path)
                        process.stdin.write(json.dumps(request)+'\n')
                        process.stdin.flush()
                        response = process.stdout.readline()
                        if not response:
                            raise ValueError(f'shader translation service exited unexpectedly at {variant}')
                        reply = json.loads(response)
                        code_path.unlink(missing_ok=True)
                        cache.put(variant, reply, staging)
                    if reply.get('ok'):
                        refl = json.loads((staging/(variant+'_b.json')).read_text())
                        if refl['chain'] != CHAIN or refl['stage'] != stage:
                            raise ValueError(f'prepared shader identity mismatch: {variant}')
                        deps = json.dumps(refl['deps'], sort_keys=True, separators=(',', ':'))
                        if deps in mine:   # same deps as a variant already made: keep one
                            continue
                        mine[deps] = variant
                        result = ('ok', variant)
                        if why == 'default' and refl.get('compare_slots'):   # its shadow maps bound too: next
                            depth = answers_for({'tex': {str(s): '*_depth' for s in refl['compare_slots']}})
                            queue.insert(0, (None, depth, key+'-'+suffix(depth), 'compare'))
                    else:
                        result = ('failed', dict(key=key, variant=variant, why=why, stage=stage, source=source,
                                                 error=reply.get('error')))
                    with lock:
                        if index is None:
                            derived.setdefault(key, []).append(result)
                        else:
                            outcome[index] = result
                        done[0 if reply.get('ok') else 1] += 1
                        done[2] += reused
                        if (done[0]+done[1]) % 1000 == 0:
                            print(f'translated {done[0]}/{len(jobs)}, failed {done[1]}, {done[2]} from the translation '
                                  f'cache; {time.monotonic()-started:.0f} s', flush=True)
                if mine:   # the shader's index, once all its jobs are done; the default variant first
                    rows = [dict(variant=v, deps=json.loads(d)) for d, v in mine.items()]
                    (staging/(key+'.variants')).write_text(json.dumps(rows, sort_keys=True, separators=(',', ':')))
        except BaseException:
            stop.set()
            raise
        finally:
            if process is not None:
                process.stdin.close()
                process.wait()
                process.stdout.close()

    workers = max(1, min(os.cpu_count() or 4, len(by_key)))
    with concurrent.futures.ThreadPoolExecutor(workers) as pool:
        try:
            for future in [pool.submit(worker) for _ in range(workers)]:
                future.result()
        except BaseException:   # Ctrl-C here, or a worker failed: the others stop after their current shader
            stop.set()
            raise
    whys = [job[3] for job in jobs]
    results = list(zip(whys, outcome)) + [('compare', r) for key in sorted(derived) for r in derived[key]]
    completed = [r[1] for _, r in results if r and r[0] == 'ok']
    failed = [r[1] for _, r in results if r and r[0] == 'failed']
    default = sum(1 for why, r in results if why == 'default' and r and r[0] == 'ok')
    for record in failed[:10]:
        print(f"failed {record['variant']} ({record['why']}, {record['source']}): {record['error']}", flush=True)
    if len(failed) > 10:
        print(f'... {len(failed) - 10} more failures (seed-manifest.json "failed")', flush=True)
    if default < MIN_SHADERS:
        raise ValueError(f'only {default} shaders translated; at least {MIN_SHADERS} are required')
    # Only completed variants are kept: a failed translation can leave MSL behind before its reflection.
    wanted = set()
    for variant in completed:
        wanted.update([variant+'_b.metal', variant+'_b.json', variant.split('-')[0]+'.variants'])
    for p in staging.iterdir():
        if p.name not in wanted:
            p.unlink(missing_ok=True)
    linked = {variant+end for variant in completed for end in ('_b.metal', '_b.json')}
    result = dict(shaders_found=len(shaders), default_translated=default, listed_rows=len(listed), listed_matched=matched,
                  listed_translated=sum(1 for why, r in results if why == 'listed' and r and r[0] == 'ok'),
                  skipped_other_version=len(listed) - matched, reused=False, variants=len(completed),
                  failed=len(failed))
    files = {p.name: cache.digest(p.name, p) if p.name in linked else digest(p.read_bytes())
             for p in sorted(staging.iterdir())}
    manifest = dict(schema=SCHEMA, chain=CHAIN, identity=identity, shaders=len(completed), result=result,
                    seconds=round(time.monotonic()-started, 1), failed=failed, files=files)
    (staging/'seed-manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    files['seed-manifest.json'] = digest((staging/'seed-manifest.json').read_bytes())
    if output.exists():
        shutil.rmtree(output)
    staging.rename(output)
    cache.prune({variant for _, _, variant, _ in jobs} |
                {r[1] if r[0] == 'ok' else r[1]['variant'] for results in derived.values() for r in results})
    record_verified(output, {name: sha for name, sha in files.items() if name != 'seed-manifest.json'})
    stamp = stamp_path(output)
    stamp.with_name(stamp.name+'.tmp').write_text(json.dumps(dict(identity=identity, result=result), indent=2)+'\n')
    stamp.with_name(stamp.name+'.tmp').replace(stamp)
    print(f'{len(completed)} shader variants in {output}, {len(failed)} failed; {done[2]} taken from the translation '
          f'cache, {done[0]+done[1]-done[2]} translated in {time.monotonic()-started:.0f} s', flush=True)
    return result


def main():
    p = argparse.ArgumentParser(description='Translate the shaders of the selected RomFS (and the shared list\'s bind '
                                'states for them) into the runtime shader cache layout.')
    p.add_argument('--romfs', type=Path, required=True, help='<work>/extracted/<v101|v200>')
    p.add_argument('--output', type=Path, required=True, help='<work>/build/shader-prep/rt-<chain>')
    p.add_argument('--translator', type=Path, required=True, help='build/shader-service/totk_xlate')
    p.add_argument('--spirv-cross', type=Path, required=True, help='build/spirv-cross/spirv-cross')
    p.add_argument('--version', required=True, help='v101 or v200')
    p.add_argument('--list', type=Path, help='the shared shader list (default: none, the default bind states only)')
    a = p.parse_args()
    result = prepare(a.romfs.resolve(), a.output.resolve(), a.translator.resolve(), a.spirv_cross.resolve(), a.version,
                     a.list.resolve() if a.list else None)
    print(summary_line(result), flush=True)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, OSError) as e:
        print(f'ERROR: {e}', file=sys.stderr)
        sys.exit(1)
