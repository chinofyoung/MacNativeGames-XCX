# Xenoblade Chronicles X: the shaders in the RomFS

Research gate for the shared shader list (`docs/superpowers/specs/2026-10-24-shared-shader-list-design.md`). The question:
can a build find every shader's control section and code in the player's own RomFS, and hash them to the key the
runtime uses? Prototype: `games/xcx/scripts/xcx_shaders.py` (`inventory()`). This page names paths, formats, offsets and
counts only. No game bytes are copied.

**Verdict: GO.** 1,497 of 1,499 known keys (99.87%) are recovered from the RomFS, byte for byte, with the runtime's hash.

## Ground truth

| Source | Keys | Stages |
|---|---|---|
| `XCX_build/build/ios-fill/rt-totk-native-v4-unverified/*.code` (the variants `fill_shaders.py` translated for the phone's misses; the pulled request JSONs were in a temporary folder that is now gone, and each `.code` is the request's `code_hex`) | 181 (201 variants) | 136 ps / 65 vs variants |
| Mac cache `~/Library/Application Support/MacNativeGames/XCX/shader-cache/rt-totk-native-v4-unverified/*.code` | 1,318 (1,521 variants) | 861 ps / 660 vs variants |
| The phone's `shader-requests/` | not pulled (no device connected for this task) | |

The two sets do not overlap (1,499 keys in all). Neither has a compute shader. A `.code` file is what the runtime
writes for the translator: `code[skip:control+0x704]`, with `skip` 0x30 for graphics and 0 for compute
(`totk_shader_rt.m` `totk_rt_variant`).

## The runtime's key

`nvn_mtl.m` `prog_get`: key = SHA-256(control[:ctl_key_len] + code[:control+0x704]). `ctl_key_len` is the word at
control+0x20 when it is in 0x7cc..0x87f, else 0x880. In this game the word is **0x850**, so the key covers exactly the
0x850-byte control record and nothing past it. The stage is control+0x714 (0 vertex, 1 fragment, 5 compute). The
game binds through `nvnProgramSetShadersExt` (`hle_nvn.c`), which gives the same control and data pointers.

## Where the shaders are

The RomFS holds 53 files. Everything except the movies (`movie/*.webm`) and the sound banks (`sound/*.pck`) is in **`sts.ard`**
(10,219,528,992 bytes), and **`sts.arh`** (1,677,200 bytes) indexes it.

- **`sts.arh`** has magic `arh2` and a u32 count (104,824) at +4. The u32 at +8 (0x10) is where the table starts. Each
  16-byte row is <u64 path hash, u32 stored size, u32 unpacked size or 0>. The table holds no names and no offsets. The
  rows are in the order the entries sit in `sts.ard`, and each entry starts 16-byte aligned. The last entry ends exactly
  at the end of `sts.ard`.
- **An entry** is either an **xbc1** frame or stored raw (11,563 entries, among them `DRSM` model streams, `LAHD`
  layouts and `BDAT` tables). xbc1 layout: magic, u32 type (1 zlib with 37,435 entries, 3 Zstandard with 55,826), u32
  unpacked size, u32 packed size, u32 hash, a 0x1c-byte name (only textures fill it), and data at +0x30. 41 entries (the
  large map files, up to 21 MB) hold more xbc1 frames after the first. The arh row's stored size covers all of them.
- **Shader packages** (magic `HCPS`, version 0x3f2, the Monolith "SPCH" format) are found:
  - standalone (including the four `/monolib/shader/shd_*.wishp` that `main` names);
  - inside effects (`efb0`), `EVPA`, models (`DMXM`) and map files;
  - inside the xbc1 streams of the raw `DRSM` model stream files.

  Every shader's first source is one xbc1 level down from the arh entry. All of the following contain HCPS packages at
  some depth: the raw entry itself, the xbc1 frames it holds, and the frames inside those. The inventory visits each
  level up to depth 3.
- **Package header** (offsets from the `HCPS`): `+0x08` program-list offset/count, `+0x18` program section
  offset/size, **`+0x20` code block offset/size**, **`+0x28` control block offset/size**, then the string section.
  - **Control block:** consecutive 0x850-byte records. Each record starts with 0x98761234, has version 1 at +4 and the
    size 0x850 at +0x20.
  - **Code block:** one code per control, in the same order. Each code starts 0x100-aligned after the previous one.
    Graphics code begins with the 0x30-byte NVN header (magic 0x12345678), followed by the SPH. Compute code has no
    header and begins with its first instruction.
  - control+0x700 is the c1 (constant) offset in the code. control+0x704 is the code length, header included. The key
    is SHA-256 of the record and the code, with no rewriting (unlike Breath of the Wild's `bound_control`).
- **Packages for another GPU**: 8,873 packages have control records of version
  **0xc9**. These records are 0xd8 bytes and carry no size word at +0x20. The code in these packages has no NVN header and
  uses 128-bit instruction words (not Maxwell's 64-bit words). Not one of them holds a 0x12345678 code. They are never
  bound on this runtime, and the inventory skips them by the version at control+4.
- Also seen but not containers of NVN code: `OCPS` (version 0x3e9) markers next to some HCPS, and the 4 cases where the
  four letters `HCPS` appear by chance in packed or float data (the 0x3f2 version check discards them).

## Measurement

`python3.14 games/xcx/scripts/xcx_shaders.py XCX_build/extracted/<v> --check XCX_build/build/ios-fill --check "<Mac cache>"`
(Python 3.14 is needed for `compression.zstd`. On 3.10 the script now stops with a message. Before that check existed,
it silently missed every Zstandard entry.)

```
found 12429 shaders in 6818 files (cs 3, ps 8126, vs 4298, None 2) in 18 s; 8873 packages for another GPU skipped, 0 unreadable
XCX_build/build/ios-fill: found 12429 shaders in 6818 files; ground truth 181 keys, recovered 181 (100.0%)
.../shader-cache/rt-totk-native-v4-unverified: found 12429 shaders in 6818 files; ground truth 1318 keys, recovered 1316 (99.8%)
```

- **v200 and v101** give the same result: the same 12,429 keys from the same sources. The 2.0.0 update does not touch
  the packages.
- **Time:** 18 s wall (80 s CPU) on an 8-core M1 with the RomFS in the page cache. All 104,824 entries are read and
  every xbc1 frame is unpacked, about 10 GB.
- **Files:** 6,818 arh entries hold at least one package with shaders. The shaders, deduplicated, total 24 MiB of code
  (256 to 26,112 bytes each).
- **Where each shader was first found:**

  | First source | Shaders |
  |---|---|
  | map files | 6,018 |
  | `DRSM` streams | 3,534 |
  | standalone packages | 1,939 |
  | `efb0` | 600 |
  | `EVPA` | 252 |
  | `DMXM` | 76 |
  | other | 10 |

- **Matching:** "recovered" means the key computed from the on-disk bytes equals the ground-truth key, and the code
  after the runtime's skip equals the `.code` file. No key matched with different code.
- **Not recovered:** two Mac-cache keys, `58e2e00d24c6…` (464 bytes) and `5d36d4144f49…` (208 bytes). I searched for
  each one's whole code through every xbc1 depth of every entry, and neither code is anywhere in the RomFS. Their source
  is unknown (a runtime-made program, or data outside `sts.ard`). Two keys out of 1,499 does not affect the verdict.
- **Order of discovery** (for the record): decoding only each entry's first xbc1 frame found 6,175 shaders (about 37%).
  Following the extra frames of the map files and the DRSM streams brought the count to 11,975. Accepting compute code
  without the NVN header (one bad pair had been dropping the whole package) brought it to 12,429.

## Stage

The stage is knowable from the files: control+0x714 for every shader.

| Stage | Shaders |
|---|---|
| 0 vertex (`vs`) | 4,298 |
| 1 fragment (`ps`) | 8,126 |
| 5 compute (`cs`) | 3, all in one standalone package; thread group sizes at control+0x748 are 16x1x1 and 16x16x1 |
| 2 geometry | 2, listed with stage `None`; the runtime has no chain for them and would stop if one were bound |

The compute keys cannot be checked against ground truth, because no compute shader has been met yet.

## Build-time translation (prepare_shader_seed.py)

`games/xcx/scripts/prepare_shader_seed.py` translates every shader found here for the default bind state, then the
shared list's bind states for keys of the selected version, into `<work>/build/shader-prep/rt-<chain>` (the runtime
cache layout). Measured on 2026-10-24 against v200 with the empty list, 8-core M1, RomFS cached:

| Run | Wall time | Result |
|---|---|---|
| First (inventory + 12,427 translations, 8 services) | 62 s (about 19 s inventory) | 12,427 default variants, 0 failed (vs 4,298, ps 8,126, cs 3) |
| Unchanged inputs | 0.8 s | `reused` |
| List grown by 2 rows (1 of this version, 1 not) | 20 s | 1 new variant translated, 12,427 from the translation cache |

- Output: 37,282 files, 239 MB (`_b.metal`, `_b.json` and `.variants` per shader, plus `seed-manifest.json`).
- MSL size: 238 bytes to 188 KB, median 6.5 KB. No variant uses `double` or an unsupported GLSL.std.450 call, so all
  of them can go to an iPhone.
- No translator reported `compare_slots` or wildcard (`*`) deps for the default bind state, so the runtime's
  exact-match `deps_match` picks these variants.
