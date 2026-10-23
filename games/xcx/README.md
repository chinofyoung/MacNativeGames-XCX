# Xenoblade Chronicles X: Definitive Edition (work in progress)

An experiment: run Xenoblade Chronicles X: Definitive Edition natively the way `games/by3` runs Bayonetta 3. It starts
from that code (rewriter, loader, Horizon host, NVN on Metal) and keeps its own copies of all of it in this folder
(`runtime/`, `third_party/d2r`, `third_party/d2r_gfx`, `prebuilt/`, `scripts/`), so changes here never affect another
game and nothing here reads `games/by3` or `games/totk`. **It reaches the title screen** (stage 3): the runtime is
retargeted to this game, boots it, draws the title scene, plays its sound and answers the keyboard in the first menus.
Text is not drawn yet and parts of the picture are wrong; that is stage 4.

How the work was done, what was found and what went wrong on the way: [CHANGELOG.md](CHANGELOG.md).

You must own the game: use your own XCI and the `prod.keys` dumped from your own Switch. Nothing from the game is in
this repository; `XCX_build/` (the work folder) and `mods/` are ignored by git.

## Inputs

- One XCI holding base `0100453019AA8000` (v0) and update 1.0.1 `0100453019AA8800` (v65536) with its common ticket.
  `title.keys` is not needed: the base Program NCA uses the key area and the update's title key comes from that ticket.
- Both Program NCAs carry the base title ID. The scripts choose each one through its content meta: the Application
  meta names the base Program, the Patch meta 1.0.1 names the update Program.
- The base is complete (not sparse, not compressed), unlike Bayonetta 3's. The code comes from the update, whose RomFS
  is a BKTR patch over the base: 79.8 MB of the merged image come from the update, 14.28 GB from the base.

## Stage 1 (done)

From `games/xcx`, with a Python environment made from `requirements.txt` in this folder plus `unicorn` for `equiv.py`
(`PY=<that python>`). The work folder sits next to the game file, as `TOTK_build` does, so the commands pass it as
`WORK`:

```sh
WORK=../../nsw/XCX_build
XCI="../../nsw/Xenoblade Chronicles X Definitive Edition.xci"
KEYS=../../nsw/prod.keys
$PY scripts/extract_code.py "$XCI" --keys $KEYS --output $WORK/extracted/v101/exefs
$PY scripts/merge_romfs.py "$XCI" --keys $KEYS --output $WORK/extracted/v101/romfs
$PY scripts/romfs_control.py "$XCI" --keys $KEYS          # negative control: two flipped bytes must be caught
for m in sdk subsdk0 main; do
  DM_RULESET=native DM_PRIVATE_GATES=0 $PY third_party/d2r/patch_nso.py $WORK/extracted/v101/exefs/$m $WORK/rewritten/v101 $m
done
$PY scripts/prepare_modules.py $WORK/extracted/v101/exefs --output $WORK/extracted/v101/elf
export LLVM_BIN="$(dirname "$(xcrun -f llvm-objdump)")" DM_VCACHE=$WORK/cache/verify_v101
for m in sdk subsdk0 main; do
  $PY third_party/d2r/structck.py $WORK/rewritten/v101 $m $WORK/extracted/v101/exefs/$m
  $PY third_party/d2r/equiv.py $WORK/rewritten/v101 $m $WORK/extracted/v101/exefs/$m --seed 1 --jobs 8
done
```

`assets/expected-v1.0.1.json` pins every resulting hash. `make` targets take the same folder: `make ... WORK=$WORK`.

| Check | Result |
|---|---|
| ExeFS (main, sdk, subsdk0, rtld, main.npdm) | FS header, hash table and every block verified; the program is the one the Patch meta 1.0.1 names; identical to the stage-0 extraction |
| Merged RomFS (complete base + BKTR update) | all six IVFC levels: 874,727 / 874,727 data blocks; 53 files, 14.3 GB; a flipped byte in update data and one in base data are both caught at the predicted block |
| Rewrite structure (`structck.py`) | OK for all three modules, 0 violations; a copy with one trampoline branch moved by +4 fails |
| Rewrite semantics (`equiv.py`, emulator, random states) | 36,697 sites, 0 divergent; all 900 mutation controls caught |

The rewrite needed one scan fix and no new block kind. `main` has 188 inline trap words `0xe7ffdefe`
(`cbz w0, +8; .inst 0xe7ffdefe`, an assert idiom). capstone stops a linear scan at the first word it cannot decode,
so two `br x18` jump tables (`main+0x1707e94`, `main+0x1707f20`) were never registered and the rewrite stopped with a
`KeyError`. `patch_nso.py` now scans past such words and leaves them as they are. The `sdk` and `subsdk0` output is
byte-identical to the stock rewriter's. `structck.py` writes its one-section ELF itself instead of calling
`llvm-objcopy`, which Xcode does not ship; `LLVM_BIN` only needs `llvm-objdump`.

## Stages 2 and 3: build and run (done)

After stage 1, from `games/xcx`, with the same `WORK` and `PY`:

```sh
# System fonts (the game's partitions have none): the six fonts any firmware carries, e.g. TotK's verified extraction
mkdir -p $WORK/extracted/firmware && cp -R ../../nsw/TOTK_build/totk/extracted/firmware/shared-fonts $WORK/extracted/firmware/
make prepare WORK=$WORK PY=$PY       # tables, RomFS index, fonts config; every input checked against assets/expected-v1.0.1.json
make runtime WORK=$WORK              # $WORK/build/xcx-native
make shader-tools WORK=$WORK         # the shader translator and SPIRV-Cross the graphics layer starts on first use
cd $WORK
./build/xcx-native rewritten/v101 probe                                   # three modules, cross-module calls
TOTK_HEADLESS=1 TOTK_VERIFY_STARTUP=1 ./build/xcx-native rewritten/v101 boot   # to nnMain and on until a window is needed
TOTK_GPU_ALLOW_SUBMIT=1 ./build/xcx-native rewritten/v101 boot 2> logs/boot.log # with a window: the title screen
```

The run is from the work folder: `extracted/`, `rewritten/`, `build/` and the profile (`user-data/`: account, saves,
pseudo device id) are relative to it. `make boot` does the same and writes `logs/boot.log`. Keyboard (Bayonetta 3's map): J = A, K or Space
= B, I = X, U = Y, Return = +, Tab or Escape = -; the rest is in `runtime/input.c`.
Diagnostics: `TOTK_INPUT_FILE=<file>` takes lines appended while the game runs (`key <Mac keycode> <ms>`,
`shot <path>.png`, `close`); `XCX_AUDIO_LEVEL=1` logs the output's peak level every 5 s; `XCX_AUDIO=0` keeps a silent
sink. `scripts/symbolize.py` (with `XCX_WORK=<work folder>`) names the sdk functions of a host failure's call chain.

| Check | Result |
|---|---|
| Probe | pass: main 926 / subsdk0 10,395 / sdk 23,007 symbols mapped, 4,000 calls main -> sdk -> host on 4 threads |
| Boot to `nnMain` | RomFS verified, every constructor state 2 (main 1,998, subsdk0 45, sdk 87 init_array entries), original `nnMain` entered |
| First frame | `first presented frame` at 0.33-0.64 s; title scene from about 4 s; about 2,000 draws a frame at 30 fps |
| Input, sound | the first menus follow the keyboard; AudioOut output peaks 5,500-17,500 of 32,767 at the title |

Screenshots: `docs/screenshots/xcx-stage3-first-frame.png`, `xcx-stage3-title.png`, `xcx-stage3-menu-after-input.png`
(repository root). Each gap the game reached, and how it was answered, is in the changelog's stage-3 entry.

## The app (stage 5)

After stages 1-3, from `games/xcx`, with the same `WORK` and `PY` (`GAME` and `KEYS` absolute, for the icon):

```sh
make app WORK=$WORK PY=$PY GAME="$PWD/$XCI" KEYS="$PWD/$KEYS"   # -> $WORK/Xenoblade Chronicles X.app (about 3 MB)
make install WORK=$WORK                                         # optional: a copy in /Applications or ~/Applications
```

Launch it like any Mac app: double-click `nsw/XCX_build/Xenoblade Chronicles X.app` in Finder (or `open` it). A
settings window comes first; Play starts the game with the switches the terminal runs set by hand
(`TOTK_GPU_ALLOW_SUBMIT=1` and the rest), the game's own 30 fps, the compact HUD, MetalFX upscaling and frame
interpolation off (key 2 still toggles interpolation; on an M1 it costs real frames). The app does not hold the game: it
runs `build/xcx-native` from the work folder it names in its Info.plist, so the work folder must stay where it was when
the app was built.

- Progress: `~/Library/Application Support/MacNativeGames/XCX/profile` (account, saves, pseudo device id, pipeline
  list). On its first start the app copies them from `$WORK/user-data`, so Continue goes on from the terminal runs'
  save; the originals are not changed. After that the two are separate.
- Shader cache: `$WORK/build/shader-cache/rt-*`, shared with `make boot`.
- Log: `~/Library/Logs/MacNativeGames/xcx.log`. Commands for the running game (`shot <path>.png`, `close`, ...) can be
  appended to `~/Library/Application Support/MacNativeGames/XCX/input.txt`; `open --env XCX_PLAY=1 <app>` skips the
  settings window.

Checked 2026-10-23: the title offers Continue and it loads the save into Primordia, with no shader translated and no
pipeline built (`docs/screenshots/xcx-stage5-app-title.png`, `xcx-stage5-app-continue.png`; details in the changelog).

## What the game uses

- NVN (SDK 19.3.2). Of the 535 NVN names main loads, 430 are implemented in this folder's layer and 7 are intentional
  no-ops; 97 are not, most of them getters. Unimplemented ones abort with their name when called.
- Wwise 2021.1.10 audio, Havok Physics 2023.2 (CPU only), Nintendo's online SDK (NPLN 1.32.1 with gRPC, OpenSSL,
  WebRTC and libcurl: needs offline answers for `nifm`, `friends`, `account`, `ssl`, `nsd`, the network clock, and the
  new `ngc` profanity filter and `caps:su` screenshots), `nn::os` fibers, movies as VP9 WebM (46 files), data in one
  10.2 GB `sts.ard` archive with its `sts.arh` header, sound in 5 Wwise `.pck` files.

## Next

Stage 4: a correct picture (text: indexed quads are not implemented in the graphics layer; the black lower half and
flat-shaded ground of the title), then play past the title: saves, movies, the rest of the online SDK's offline
answers as the game reaches them.
