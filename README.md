# Xenoblade Chronicles X: Definitive Edition, native on Apple silicon

An early, work-in-progress native Mac port of your own copy of **Xenoblade Chronicles X: Definitive Edition 1.0.1**.
The port lives in [`games/xcx`](games/xcx).

This repository is a fork of [MacNativeGames](https://github.com/ly05010419/MacNativeGames). It builds on that project's
native ports of Tears of the Kingdom, Breath of the Wild and Bayonetta 3 and on their shared runtime, and is released
under the same MIT license. This fork holds only the Xenoblade Chronicles X port; the other games are in the original
repository.

## What it is

It is not an emulator. The game's own ARM64 code runs directly on Apple silicon:

- The code modules are rewritten ahead of time, only where macOS requires it, then loaded and relocated.
- The Switch system services the game asks for are answered by a small host.
- The game's NVN graphics calls are rebuilt on Metal. Its Maxwell shaders are translated to Metal shaders the first time
  they are used.

No CPU is emulated: there is no interpreter, JIT or dynamic binary translation. The port started from Bayonetta 3's
port in the original repository, and `games/xcx` keeps its own copy of the runtime, rewriter, graphics layer and scripts.
Besides `games/xcx`, the repository holds only the vendored Ryujinx shader translator sources (`vendor/ryujinx`) that
the prebuilt shader translator is built from.

## Legal

**You must own the game.** Use this project only with a copy of Xenoblade Chronicles X: Definitive Edition that you
bought, and with the keys dumped from your own Nintendo Switch. We do not help anyone obtain keys or game files.

This is an independent, non-commercial project. It is not affiliated with, endorsed by, or sponsored by Nintendo or
Monolith Soft. Nintendo, Nintendo Switch and Xenoblade Chronicles are trademarks of Nintendo.

The repository contains no game content and distributes none: no game program, art, text, audio, shaders, keys or
saves. Everything the game needs is made on your own Mac from your own files. What the repository does hold is the code
of the port and the information needed to check your files (the hashes in
[`games/xcx/assets/expected-v1.0.1.json`](games/xcx/assets/expected-v1.0.1.json)).

The app and the work folder you build hold or use the game, so they are for your own use only. **Never share the built
app, the work folder or anything in it.**

## Status

Early. The game reaches the title screen, character creation and gameplay in Primordia:

- Characters are drawn and lit, in the field and in battle (the first battle was played).
- The field and battle HUD and menu text are drawn.
- Saves write, and Continue loads them.
- A controller (a DualSense was used) and the Mac keyboard both work.
- `make app` builds a double-clickable app with a settings window.

Only those areas have been checked so far. Tested on a MacBook Air with M1 (8-core GPU, 16 GB), where the game runs at
its own 30 fps and uses about 17 ms of GPU time per frame.

| Stage | Goal | State |
|---|---|---|
| 0 | Feasibility | done |
| 1 | Code and data out of your dump, code rewritten for Apple silicon | done |
| 2 | The runtime boots the game to its `nnMain` | done |
| 3 | First frame and the title screen | done |
| 4 | Picture, audio, input, saves | in progress |
| 5 | Performance, shaders, the app | in progress |

The full work log, with what was found, what was changed and how each step was checked, is in
[`games/xcx/CHANGELOG.md`](games/xcx/CHANGELOG.md).

## Requirements

- A Mac with Apple silicon and **macOS 26** or later.
- Xcode (for `clang`, `llvm-objdump` and the Metal frameworks).
- [Homebrew](https://brew.sh) with `opus` and `libvpx`: `brew install opus libvpx`.
- `git`: the build fetches SPIRV-Cross at a pinned commit.
- Python 3 with the packages in [`games/xcx/requirements.txt`](games/xcx/requirements.txt), plus `unicorn` for the
  rewrite check `equiv.py`. Unicorn is only used to check the rewritten code; it is not part of the port.
- One **XCI** that holds both the base game `0100453019AA8000` (v0) and update 1.0.1 `0100453019AA8800` (v65536) with its
  common ticket. Other versions and other packages have not been tried. `title.keys` is not needed.
- Your `prod.keys`.
- The six Switch system fonts (`shared-fonts`, the same in every firmware). The game's own files do not carry them.
  The port was tested with the fonts a Tears of the Kingdom build of the original project extracted; a separate font
  step for this game does not exist yet.
- Free disk space for the work folder: the merged game data alone is 14.3 GB.

## Build and run

There is no one-command build yet. For now the port is built with the steps below, all run from `games/xcx`.

The examples keep the XCI and `prod.keys` in `personal/` and the work folder in `XCX_build/`, both at the repository
root, where git ignores them. Any other place works; keep the work folder out of the repository's tracked files.

### 1. Set up

```sh
cd games/xcx
python3 -m venv ../../XCX_build/venv
../../XCX_build/venv/bin/pip install -r requirements.txt unicorn
PY=../../XCX_build/venv/bin/python
WORK=../../XCX_build
XCI="../../personal/Xenoblade Chronicles X Definitive Edition.xci"
KEYS=../../personal/prod.keys
```

### 2. Extract and rewrite the code, extract the data

```sh
$PY scripts/extract_code.py "$XCI" --keys $KEYS --output $WORK/extracted/v101/exefs
$PY scripts/merge_romfs.py "$XCI" --keys $KEYS --output $WORK/extracted/v101/romfs
for m in sdk subsdk0 main; do
  DM_RULESET=native DM_PRIVATE_GATES=0 $PY third_party/d2r/patch_nso.py $WORK/extracted/v101/exefs/$m $WORK/rewritten/v101 $m
done
$PY scripts/prepare_modules.py $WORK/extracted/v101/exefs --output $WORK/extracted/v101/elf
```

Every block of the code and of the merged RomFS is verified against the game's own hashes as it is read.

The rewrite can be checked with the tools the port was verified with:

```sh
export LLVM_BIN="$(dirname "$(xcrun -f llvm-objdump)")" DM_VCACHE=$WORK/cache/verify_v101
for m in sdk subsdk0 main; do
  $PY third_party/d2r/structck.py $WORK/rewritten/v101 $m $WORK/extracted/v101/exefs/$m
  $PY third_party/d2r/equiv.py $WORK/rewritten/v101 $m $WORK/extracted/v101/exefs/$m --seed 1 --jobs 8
done
```

### 3. Add the system fonts

Put the six `.bfttf` fonts in `$WORK/extracted/firmware/shared-fonts`. From a Tears of the Kingdom work folder of the
original project, for example:

```sh
mkdir -p $WORK/extracted/firmware
cp -R <TotK work folder>/totk/extracted/firmware/shared-fonts $WORK/extracted/firmware/
```

### 4. Build the runtime and the app

```sh
make prepare WORK=$WORK PY=$PY   # tables, RomFS index, fonts; every input checked against assets/expected-v1.0.1.json
make app WORK=$WORK PY=$PY GAME="$PWD/$XCI" KEYS="$PWD/$KEYS"
make install WORK=$WORK          # optional: copy the app to /Applications or ~/Applications
```

`make app` also builds the runtime (`make runtime`) and the shader tools (`make shader-tools`). `GAME` and `KEYS` must be
absolute paths; they are used only for the app's icon, taken from the game's own data (without them the app has no
icon). The app is written to `$WORK/Xenoblade Chronicles X.app` and is about 3 MB.

The shader translator comes ready-made (`games/xcx/prebuilt/macos-arm64/totk_xlate`, checked against its SHA-256), so
the build needs no .NET. Its sources are in `games/xcx/tools/totk_xlate` and `vendor/ryujinx`; see
[`games/xcx/prebuilt/macos-arm64/README.md`](games/xcx/prebuilt/macos-arm64/README.md) for how to rebuild it.

### 5. Play

Double-click `Xenoblade Chronicles X.app` in Finder, or `open` it. A settings window comes first; **Play** starts the
game.

- The default preset is "Original (30 fps, window)"; "Original (30 fps, full screen)" is the other one. Both run the
  game's own 30 fps without MetalFX upscaling.
- The window also lists 60 / 120 fps and a fixed 900p resolution. Their code patches do not exist for this game yet,
  and the window says so.
- The app does not contain the game. It runs `build/xcx-native` from the work folder named when it was built, so the
  work folder must stay where it is.
- The first start checks the whole RomFS once (the first frame takes about 10 s); later starts reuse that record.

The game can also be run from a terminal in the work folder: `make boot WORK=$WORK` from `games/xcx`, which writes its
log to `$WORK/logs/boot.log`. See [`games/xcx/README.md`](games/xcx/README.md) for the switches a terminal run needs.

## Controls

Controllers that macOS recognizes are given to the game as one Switch controller; a DualSense was used in testing.
The Mac keyboard uses physical key positions of a US layout:

| Key | Switch | | Key | Switch |
|---|---|---|---|---|
| W A S D | left stick | | arrow keys | right stick |
| J | A | | K, Space | B |
| I | X | | U | Y |
| Q / E | L / R | | Option / Shift | ZL / ZR |
| F T H G | d-pad left / up / right / down | | Z / C | left / right stick press |
| Return | + | | Tab, Esc | − |

Extra keys:

- **1** shows or hides the on-screen overlay.
- **2** cycles MetalFX frame interpolation: off, 30+30, half the refresh rate, unlimited. It works, but on an M1 it costs
  more than it gains: with 30+30 the game itself drops to about 23-27 fps. It is off at start.
- Keys **3** and **4** come from the Tears of the Kingdom runtime (picture mode and aspect ratio) and have not been
  checked with this game.

## Where things are

- Saves, account and pseudo device id: `~/Library/Application Support/MacNativeGames/XCX/profile`. On its first start the
  app copies them from `$WORK/user-data` (the profile of terminal runs) if that exists, so it continues from the same
  save. The originals are not changed, and after that the two are separate.
- RomFS check record and the input file for diagnostic commands: `~/Library/Application Support/MacNativeGames/XCX/`.
- Shader cache: `$WORK/build/shader-cache/`, shared by the app and terminal runs.
- Log: `~/Library/Logs/MacNativeGames/xcx.log` (the run before: `xcx.previous.log`). Terminal runs with `make boot`:
  `$WORK/logs/boot.log`.

## Known issues and what has not been checked

- **Audio** is produced (the game's Wwise output reaches Core Audio with measured, non-silent levels) but has not
  been listened to.
- **Not checked yet:** cities (New Los Angeles), Skells, cutscenes, day-time lighting, water, menus beyond character
  creation, and a New Game through character creation with the current build.
- In the opening cutscene the White Whale is a black silhouette with a rim light. It has not been rechecked since the
  character fix; it may be correct or a rendering bug.
- Frame interpolation (key 2): besides its cost on an M1, characters and enemies moving on their own and rain streaks
  ghost in the generated frames, and labels attached to enemies are half a frame ahead. Camera cuts have not been tested.
- MetalFX spatial upscaling has not been checked with this game and is off in the presets.
- No online features: the network is offline. The name filter accepts any name.
- About a hundred NVN functions the game imports are not implemented. One that is called stops the game with its name in
  the log; none was reached up to the first battle.
- The app is not self-contained: it needs the work folder on the same Mac. There is no one-command build yet.

## How it works

The general method is described for Tears of the Kingdom in the original
[MacNativeGames](https://github.com/ly05010419/MacNativeGames) repository (`docs/how-it-works.md` there); the same
architecture applies here. What was changed for this game, and why, is in
[`games/xcx/CHANGELOG.md`](games/xcx/CHANGELOG.md) and [`games/xcx/README.md`](games/xcx/README.md).

## Acknowledgements

- **[MacNativeGames](https://github.com/ly05010419/MacNativeGames)** (MIT): the runtime, rewriter, graphics layer and
  app this port is built from.
- **[d2r-mobile](https://github.com/jhaizhou-ops/d2r-mobile)** (MIT) developed this way of running Switch games natively.
- **Ryujinx** (MIT): its shader translator (`vendor/ryujinx`), compiled ahead of time into
  `games/xcx/prebuilt/macos-arm64/totk_xlate`.
- **[SPIRV-Cross](https://github.com/KhronosGroup/SPIRV-Cross)** (Apache-2.0), fetched at a pinned commit and compiled
  during the build.
- **libopus** and **libvpx** (BSD) from Homebrew: audio and the game's movies.

## License

MIT (see [LICENSE](LICENSE)). Third-party code keeps its own license: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
Contributions: [CONTRIBUTING.md](CONTRIBUTING.md).
