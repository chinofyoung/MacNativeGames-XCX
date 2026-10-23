# Xenoblade Chronicles X: Definitive Edition (work in progress)

An experiment: run Xenoblade Chronicles X: Definitive Edition natively the way the original MacNativeGames project's
`games/by3` runs Bayonetta 3. It starts from that code (rewriter, loader, Horizon host, NVN on Metal) and keeps its own
copies of all of it in this folder (`runtime/`, `third_party/d2r`, `third_party/d2r_gfx`, `prebuilt/`, `tools/`,
`scripts/`); the other games are not part of this repository. **It reaches the title screen, character creation and
gameplay in Primordia** (stages 4 and 5, in progress): characters, text and the HUD are drawn, the first battle plays,
saves write and Continue loads them, a controller and the keyboard work, and the root `build_app.py` turns the
player's XCI into a self-contained, double-clickable app in one command (`make app` builds a development app).
Only those areas have been checked so far (on an M1 MacBook Air, at the game's 30 fps). The stage sections below say
how each step was reached; the user-facing summary is the repository's [README](../../README.md).

Players build with one command from the repository root, `python3 build_app.py <game.xci> -k <prod.keys>` (see the
[README](../../README.md#build-and-run)); it runs the steps below and this folder's Makefile targets, checks each one, and
packages a self-contained app. The steps below are the developer path. The shader translator in `prebuilt/` is built
from `tools/totk_xlate` and the repository's `vendor/ryujinx` (see `prebuilt/macos-arm64/README.md`).

How the work was done, what was found and what went wrong on the way: [CHANGELOG.md](CHANGELOG.md).

You must own the game: use your own XCI and the `prod.keys` dumped from your own Switch. Nothing from the game is in
this repository; `XCX_build/` (the work folder) and `mods/` are ignored by git.

## Inputs

- One XCI holding base `0100453019AA8000` (v0) and update 1.0.1 `0100453019AA8800` (v65536) with its common ticket.
  `title.keys` is not needed: the base Program NCA uses the key area and the update's title key comes from that ticket.
- Or the base from a base NSP (or a base-only XCI) and the update from its own NSP: `extract_code.py` and
  `merge_romfs.py` take `--update <update.nsp>`, and `--title-keys <title.keys>` for an NSP without a common ticket (a
  personalized ticket is then passed over). NSPs carry no system update, so the fonts come from `--firmware`.
- Both Program NCAs carry the base title ID. The scripts choose each one through its content meta: the Application
  meta names the base Program, the Patch meta 1.0.1 names the update Program.
- The base is complete (not sparse, not compressed), unlike Bayonetta 3's. The code comes from the update, whose RomFS
  is a BKTR patch over the base: 79.8 MB of the merged image come from the update, 14.28 GB from the base.

## Stage 1 (done)

From `games/xcx`, with a Python environment made from `requirements.txt` in this folder plus `unicorn` for `equiv.py`
(`PY=<that python>`). The game file and keys are in `personal/` and the work folder is `XCX_build/`, both at the
repository root (ignored by git); the commands pass the work folder as `WORK`:

```sh
WORK=../../XCX_build
XCI="../../personal/Xenoblade Chronicles X Definitive Edition.xci"
KEYS=../../personal/prod.keys
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
# System fonts (the game's partitions have none): from the system update the XCI carries (or --firmware <NCA folder>)
$PY scripts/extract_shared_fonts.py --keys $KEYS --xci "$XCI" --output $WORK/extracted/firmware/shared-fonts
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
(repository root; screenshots show game imagery and stay local, ignored by git). Each gap the game reached, and how it
was answered, is in the changelog's stage-3 entry. At stage 3 text was not drawn and parts of the picture were wrong;
stage 4 fixed both (indexed quads, in the changelog).

## The app (stage 5)

Two kinds of app share one launcher (`app/launcher.m`) and one bundle description (`scripts/app_bundle.py`: name,
identifier, Info.plist and the files a self-contained app carries).

**Self-contained (`build_app.py` at the repository root).** The game folder is inside the app,
`Contents/Resources/xcx`, laid out like a work folder: `build/xcx-native`, the shader tools
(`build/spirv-cross/spirv-cross`, `build/shader-service/totk_xlate`), `rewritten/v101/*.patched.bin`,
`extracted/v101/romfs/runtime-index.bin` and `extracted/firmware/shared-fonts`. The 14.3 GB `romfs.bin` is a data
bundle, `Contents/Helpers/GameData-v101.bundle`, signed once in the work folder (`build/data-bundles`) and cloned in;
`extracted/v101/romfs/romfs.bin` is a link to it. Everything is an APFS clone of the work folder's files, so the app
costs no extra space on the same volume, and it no longer needs the work folder. The launcher runs in this mode when
`Contents/Resources/xcx/build/xcx-native` is there (and `XCX_WORK` is not set). Everything the game writes goes to the
user's Library, so the signed bundle stays read-only:

- the shader cache to `~/Library/Application Support/MacNativeGames/XCX/shader-cache/rt-<chain>` (`TOTK_SHADER_CACHE`,
  as Tears of the Kingdom's app does),
- the RomFS check record to `.../XCX/app-romfs-record/` (the record names the image's file identity; the app's image
  is another file than the work folder's, so the two kinds of app keep separate records instead of replacing each
  other's and hashing 14 GB again in turn),
- the fonts are read from inside the app (`TOTK_SHARED_FONTS`), not from the work folder path compiled into the binary.

The app's profile is not seeded from a work folder (it has none). `--check` (run once by the build) only checks that the
game binary is in the app and that its version matches Info.plist, so macOS's first-run scan happens during the build.

**The window (`build_app.py --gui`).** `builder/` holds a small SwiftUI app (`BuilderApp.swift`, `BuildModel.swift`,
`ContentView.swift`, `TestHook.swift`, `Info.plist`) that `build_app.py --gui` compiles with `xcrun swiftc` (no Xcode
project) into `~/Library/Caches/MacNativeGames/xcx-builder/<hash>/XCX Builder.app`. The build is ad-hoc signed and
compiled again only when the sources or the compiler change. The app runs `build_app.py --progress json` (one JSON event
a line on stdout, the human log on stderr; SIGTERM cancels cleanly), and `--check-requirements --progress json` for
its prerequisites panel. `TestHook.swift` is for scripted checks only. With `XCX_BUILDER_TEST_*` set, it fills in the
fields, starts or cancels a build, writes its state to a file, and takes screenshots of its own window. It is inactive
otherwise.

**Development (`make app`).** After stages 1-3, from `games/xcx`, with the same `WORK` and `PY` (`GAME` and `KEYS`
absolute, for the icon):

```sh
make app WORK=$WORK PY=$PY GAME="$PWD/$XCI" KEYS="$PWD/$KEYS"   # -> $WORK/Xenoblade Chronicles X.app (about 3 MB)
make install WORK=$WORK                                         # optional: a copy in /Applications or ~/Applications
```

Launch it like any Mac app: double-click `XCX_build/Xenoblade Chronicles X.app` in Finder (or `open` it). A
settings window comes first; Play starts the game with the switches the terminal runs set by hand
(`TOTK_GPU_ALLOW_SUBMIT=1` and the rest), the game's own 30 fps, the compact HUD, MetalFX upscaling and frame
interpolation off (key 2 still toggles interpolation; on an M1 it costs real frames). The development app does not hold
the game: it runs `build/xcx-native` from the work folder it names in its Info.plist, so the work folder must stay
where it was when the app was built.

- Progress: `~/Library/Application Support/MacNativeGames/XCX/profile` (account, saves, pseudo device id, pipeline
  list), for both kinds of app. On its first start the development app copies them from `$WORK/user-data`, so Continue
  goes on from the terminal runs' save; the originals are not changed. After that the two are separate.
- Shader cache: `$WORK/build/shader-cache/rt-*`, shared with `make boot`.
- Log: `~/Library/Logs/MacNativeGames/xcx.log` (both kinds; its `[launcher]` line says which kind ran and where the
  RomFS image is). Commands for the running game (`shot <path>.png`, `close`, ...) can be appended to
  `~/Library/Application Support/MacNativeGames/XCX/input.txt`; `open --env XCX_PLAY=1 <app>` skips the settings window,
  and `--env TOTK_SLOT_OWNER=<name>` marks a scripted run so that a game the user starts takes the game slot over.

Checked 2026-10-23: the title offers Continue and it loads the save into Primordia, with no shader translated and no
pipeline built (`docs/screenshots/xcx-stage5-app-title.png`, `xcx-stage5-app-continue.png`; details in the changelog).
The self-contained app was checked the same way, also after being moved out of the work folder (changelog, "one-command
build").

## What the game uses

- NVN (SDK 19.3.2). Of the 535 NVN names main loads, 430 are implemented in this folder's layer and 7 are intentional
  no-ops; 97 are not, most of them getters. Unimplemented ones abort with their name when called.
- Wwise 2021.1.10 audio, Havok Physics 2023.2 (CPU only), Nintendo's online SDK (NPLN 1.32.1 with gRPC, OpenSSL,
  WebRTC and libcurl: needs offline answers for `nifm`, `friends`, `account`, `ssl`, `nsd`, the network clock, and the
  new `ngc` profanity filter and `caps:su` screenshots), `nn::os` fibers, movies as VP9 WebM (46 files), data in one
  10.2 GB `sts.ard` archive with its `sts.arh` header, sound in 5 Wwise `.pck` files.

## Next

Stage 4 and 5 are in progress; the changelog's Status table is kept current. Not checked yet: listening to the audio,
cities (New Los Angeles), Skells, cutscenes (the opening's White Whale is a black silhouette, not rechecked), day-time
lighting, water, menus beyond character creation, and a New Game with the current build. Then: the rest of the online
SDK's offline answers as the game reaches them, the 60 fps and resolution patches the settings window lists, a startup
shader seed with Metal cache warm-up for the app, and up-front shader translation, which the iPhone / iPad build needs
to draw.

## iPhone and iPad (experimental)

`ios/` holds the iOS app, restored from the original project's `games/totk/ios` and adapted for this game.
`build_app.py --platform ios` runs `ios/tools/build_ipa.py` after the shared steps. It works like this:

1. The Mac runtime exports each module's patched text (`build/xcx-native rewritten/v101 export-ios`, `runtime/loader.c`).
2. `ios/tools/gen_embed.py` places the text, data and patch area of `main`, `sdk` and `subsdk0` in the executable's own
   segments (`__TOTKX` r-x, `__TOTK<module>W` rw), bound by SHA-256 in `ios_embed.h`. iOS cannot run code written at
   run time, so the loader remaps these segments instead (`TARGET_OS_IPHONE` in `loader.c`).
3. `ios/tools/build_deps.py` builds Opus and libvpx for the device with CMake.
4. `ios/tools/build_ipa.py collect_seed` gathers a shader seed from the Mac's caches. It only reads them.
5. xcodegen turns `ios/project.yml` into an Xcode project. The project has the Makefile's source list
   (`frontend_ios.m` in place of `frontend.m`, `loader.c` again with `-Dmain=totk_host_main`, and the graphics layer
   with `-DGAME_XCX`).
6. `xcodebuild -sdk iphoneos` builds the app, then the build packs the IPA.

Everything is generated under `<work>/build/ios*`.

The app (`ios/Sources/main.m`) sets the same runtime switches as `app/launcher.m`. The game image lives in
Documents/GameData. The runtime reads the index and the image from one folder (`TOTK_ROMFS`), so the app copies its index
there, and the Mac runtime is unchanged.

Open issues for a picture: up-front shader translation (every variant a draw can need, as the original project's seed,
for which `fill_shaders.py` is only a stopgap); the memory footprint on a phone (not measured); the audio output restart
after an interruption (`runtime_audio_output_restart` does not exist in this runtime; `main.m` has a stand-in that only
logs); on-device testing of input, saves and the RomFS check. `ios/tools/shader_contrib_watch.py` is a stub, because
there is no shared shader list.
