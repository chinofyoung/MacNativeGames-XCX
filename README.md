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
- `python3 build_app.py` turns your XCI into a self-contained, double-clickable app with a settings window, in one
  command (`make app` builds a development app for work on the port).

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
- Xcode (for `clang`, `llvm-objdump` and the Metal frameworks), or at least the Xcode command line tools plus Xcode's
  `llvm-objdump`.
- [Homebrew](https://brew.sh) with `opus`, `libvpx` and Python 3.14: `brew install opus libvpx python@3.14`.
- `git` (part of the command line tools): the build fetches SPIRV-Cross at a pinned commit. The first build also needs
  network access for the build's Python packages ([`games/xcx/requirements.txt`](games/xcx/requirements.txt), plus
  `unicorn` for the rewrite check `equiv.py`; Unicorn only checks the rewritten code and is not part of the port).
- Your game, base `0100453019AA8000` (v0) and update 1.0.1 `0100453019AA8800` (v65536), as either
  - one **XCI** holding both, as the game card does (this is what the port was tested with), or
  - a base **NSP** (or a base-only XCI) and the 1.0.1 update NSP. NSPs are decrypted with their common ticket; for an
    NSP without one, pass your `title.keys` (`--title-keys`).
- Your `prod.keys`.
- The six Switch system fonts (`shared-fonts`). The game's own partitions do not carry them, but the XCI's system update
  does, and the build takes them from there. For NSPs, pass your firmware's NCA files with `--firmware <folder>`.
- About 16 GB free for the work folder (the merged game data alone is 14.3 GB). On the same volume the app costs no
  extra space: its game data are APFS clones of the work folder's.

## Build and run

The examples keep the XCI and `prod.keys` in `personal/` at the repository root, where git ignores them. Any other
place works; keep game files, work folders and apps out of the repository's tracked files (`XCX_build/` and any
`XCX_build_*/` at the root are ignored too).

### The window (easiest)

From the repository root:

```sh
python3 build_app.py --gui
```

This opens **Xenoblade Chronicles X Builder**, a small native window:

- Choose your game file (the XCI, or a base NSP and then the 1.0.1 update NSP), your `prod.keys`, and optionally
  `title.keys` and a firmware folder.
- The output defaults to `Xenoblade Chronicles X.app` next to the game file. Pick *iPhone and iPad (experimental)* to
  build the IPA instead (see below). *Advanced* chooses another work folder.
- The *Prerequisites* panel checks this Mac and lists anything missing, with the command that installs it.
- **Build App** shows each step with its time and the log. **Cancel** stops cleanly, and the next build continues from
  the last finished step. At the end, **Open App** or **Show in Finder**. If the build stops, the window names the step
  and the file.

The window remembers your choices. It never shows or logs key values. The first time, `--gui` compiles the window from
[`games/xcx/builder`](games/xcx/builder) with `xcrun swiftc` into `~/Library/Caches/MacNativeGames/xcx-builder` (a few
seconds). The Xcode command line tools are enough, and nothing ready-made is downloaded. The window runs the same
`build_app.py` as the command below.

### One command

From the repository root:

```sh
python3 build_app.py "personal/Xenoblade Chronicles X Definitive Edition.xci" -k personal/prod.keys
```

This makes the work folder `personal/XCX_build` and the app `personal/Xenoblade Chronicles X.app`; `--work <folder>`
and `-o <path>.app` choose other places.

For NSPs: `python3 build_app.py <base.nsp> -k prod.keys --update <update 1.0.1.nsp> --firmware <firmware folder>`
(add `--title-keys title.keys` for NSPs without a common ticket).

- `python3 build_app.py --help` lists every option. `python3 build_app.py --check-requirements` (add `--platform ios`
  for the IPA) checks this Mac without building anything and prints a fix for anything missing.
- The build checks the Mac first (macOS version, Apple silicon, the developer tools, Homebrew's `opus` and `libvpx`,
  Python 3.14), then recognises the game by the title IDs and versions inside the files, never by their names: another
  game, the update given as the game, a base-only XCI without `--update`, or another version stops it with a message.
- Every step is checked against [`games/xcx/assets/expected-v1.0.1.json`](games/xcx/assets/expected-v1.0.1.json), so a
  damaged dump stops at the first wrong file: the 1.0.1 code (every ExeFS block verified), the rewrite for Apple silicon
  and its two checks (structure, and semantics in an emulator), the system fonts, the merged RomFS (all six IVFC levels),
  the shader translator's SHA-256 and SPIRV-Cross's pinned commit. Key values are never printed.
- Each step is timed. A second run reuses what is already done and correct and takes seconds.
- The app is **self-contained**: the code, fonts and game data are inside it (the 14.3 GB RomFS as APFS clones, so no
  extra space on the same volume), it is ad-hoc signed, and it can be moved, also to `/Applications`. The work folder
  can be deleted afterwards; keep it to rebuild in seconds.

The shader translator comes ready-made (`games/xcx/prebuilt/macos-arm64/totk_xlate`, checked against its SHA-256), so
the build needs no .NET. Its sources are in `games/xcx/tools/totk_xlate` and `vendor/ryujinx`; see
[`games/xcx/prebuilt/macos-arm64/README.md`](games/xcx/prebuilt/macos-arm64/README.md) for how to rebuild it.

### Play

Double-click `Xenoblade Chronicles X.app` in Finder, or `open` it. A settings window comes first; **Play** starts the
game.

- The default preset is "Original (30 fps, window)"; "Original (30 fps, full screen)" is the other one. Both run the
  game's own 30 fps without MetalFX upscaling.
- The window also lists 60 / 120 fps and a fixed 900p resolution. Their code patches do not exist for this game yet,
  and the window says so.
- The first start checks the whole RomFS once (the first frame takes about 10 s); later starts reuse that record.
- Shaders are translated the first time the game meets them and kept in the shader cache, so the first minutes of a new
  install stutter more than later ones.

### Developer build (make)

`build_app.py` runs the targets of [`games/xcx/Makefile`](games/xcx/Makefile) (`prepare`, `runtime`, `shader-tools`,
`launcher`, `icon`). For work on the port they can be run by hand, from `games/xcx`, after the extraction steps. A work
folder made by `build_app.py` already holds everything up to `make prepare`.

```sh
cd games/xcx
python3.14 -m venv ../../XCX_build/venv
../../XCX_build/venv/bin/pip install -r requirements.txt unicorn
PY=../../XCX_build/venv/bin/python
WORK=../../XCX_build
XCI="../../personal/Xenoblade Chronicles X Definitive Edition.xci"
KEYS=../../personal/prod.keys
$PY scripts/extract_code.py "$XCI" --keys $KEYS --output $WORK/extracted/v101/exefs
$PY scripts/merge_romfs.py "$XCI" --keys $KEYS --output $WORK/extracted/v101/romfs
for m in sdk subsdk0 main; do
  DM_RULESET=native DM_PRIVATE_GATES=0 $PY third_party/d2r/patch_nso.py $WORK/extracted/v101/exefs/$m $WORK/rewritten/v101 $m
done
$PY scripts/prepare_modules.py $WORK/extracted/v101/exefs --output $WORK/extracted/v101/elf
$PY scripts/extract_shared_fonts.py --keys $KEYS --xci "$XCI" --output $WORK/extracted/firmware/shared-fonts
make prepare WORK=$WORK PY=$PY   # tables, RomFS index, fonts; every input checked against assets/expected-v1.0.1.json
make app WORK=$WORK PY=$PY GAME="$PWD/$XCI" KEYS="$PWD/$KEYS"
```

The extraction scripts take `--update <update.nsp>` (and `--title-keys`) for a base NSP; `extract_shared_fonts.py`
takes `--firmware <folder>` instead of `--xci`. The rewrite checks are in [`games/xcx/README.md`](games/xcx/README.md).

`make app` builds a **development app** (about 3 MB) at `$WORK/Xenoblade Chronicles X.app`: it does not contain the
game but runs `build/xcx-native` from the work folder its Info.plist names, so the work folder must stay where it is.
`GAME` and `KEYS` must be absolute paths; they are used only for the app's icon. `make install WORK=$WORK` copies it to
`/Applications` or `~/Applications`. The game can also be run from a terminal in the work folder: `make boot WORK=$WORK`
from `games/xcx`, which writes its log to `$WORK/logs/boot.log`. See [`games/xcx/README.md`](games/xcx/README.md) for
the switches a terminal run needs.

### iPhone and iPad (experimental)

`--platform ios` builds an unsigned IPA for sideloading. **It is experimental, and most of the picture is missing.** An
iPhone or iPad cannot translate the game's shaders while it plays (there is no translator process on iOS), and this port
does not translate them ahead of time yet. The IPA carries only the shader variants your Mac has already translated
while you played the Mac app. Every other draw is skipped. The iOS app was built and its IPA checked, but it has not been
run on a device. The iOS Simulator cannot run it: the graphics layer uses Metal 4, which the Simulator SDK does not have.

Requirements:

- Everything the Mac build needs (above), plus Xcode 26 or later with the iOS SDK, and `brew install xcodegen cmake`.
  The first build downloads and compiles Opus and libvpx for iOS, so it needs network access.
- iOS / iPadOS 26 on an iPhone 14 or newer (A15 or later), or an iPad on iPadOS 26 with an A15 chip or later. These
  are the original project's requirements and have not been tested with this game.
- Optional: Xcode's Metal toolchain (`xcodebuild -downloadComponent MetalToolchain`). With it the build compiles the
  carried shaders for the device. Without it they stay Metal source, and the device compiles each one the first time it
  is used.

Build (steps 1-6 are the Mac build's and reuse the same work folder):

```sh
python3 build_app.py "<game folder>/Xenoblade Chronicles X Definitive Edition.xci" -k <prod.keys> --platform ios
```

This writes `Xenoblade Chronicles X.ipa` next to the game file (`-o` picks another `.ipa` path). The IPA is about
40 MB, because it holds the game code, the RomFS index, the system fonts and the carried shaders, but not the 14.3 GB
game image. `--ios-seed-cache <folder>` adds another Mac shader cache to carry. By default the build carries the
cache in `~/Library/Application Support/MacNativeGames/XCX/shader-cache` and the work folder's own caches.

Install it with your own Apple ID or a sideloading tool:

- With Xcode and a cable: add your Apple ID in Xcode › Settings › Accounts (a free Personal Team works), connect and
  trust the device, then run
  `python3 games/xcx/ios/tools/install_device.py "Xenoblade Chronicles X.ipa" --device "<device name>" --game-data <work folder>/extracted/v101/romfs/romfs.bin`.
  It signs the app as `org.macnativegames.xcx.<team>`, installs it, and copies the game image once into the app's
  Documents/GameData in 1 GiB parts. Later installs skip the copy while the device holds the same image. A free Apple ID
  signs for 7 days and a paid membership for a year. Run the command again to renew; your saves are kept. On first use,
  trust your Apple ID under Settings › General › VPN & Device Management.
- With another sideloading tool: install the IPA and keep its entitlement (`increased-memory-limit`). Then get the game
  data onto the device: in Finder, select the device, open Files, and drag
  `<work folder>/extracted/v101/romfs/romfs.bin` onto Xenoblade Chronicles X. The app moves it into its GameData folder
  on the next start. If the game starts before the copy is complete, it says so and waits for the next start.

The game data stays in the app's Documents when you reinstall or update the app. It is excluded from iCloud backups. The
first start hashes it once against the build's checksum and keeps a record. Saves and the shader cache are also in
Documents. The log is `log.txt` there, which you can read in the Files app (On My iPhone › Xenoblade Chronicles X).

Shaders the device misses are listed in `shader-list-contribution.txt` and saved as requests in Documents. With the
device connected, `python3 games/xcx/ios/tools/fill_shaders.py --work <work folder> --device <id>` translates them on
the Mac and sends them to the device, and later IPA builds carry them too. Like the rest of the iOS path, this tool has
not been run against a device.

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

- Saves, account and pseudo device id: `~/Library/Application Support/MacNativeGames/XCX/profile`, for both kinds of
  app. On its first start a development app copies them from `$WORK/user-data` (the profile of terminal runs) if that
  exists, so it continues from the same save. The originals are not changed, and after that the two are separate.
- Shader cache: the app's in `~/Library/Application Support/MacNativeGames/XCX/shader-cache/`, so the signed app stays
  read-only; a development app's in `$WORK/build/shader-cache/`, shared with terminal runs.
- RomFS check record and the input file for diagnostic commands: `~/Library/Application Support/MacNativeGames/XCX/`
  (the app's record in its `app-romfs-record/` folder).
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
- There is no startup shader seed or Metal cache warm-up yet (the original project ships both for Tears of the Kingdom):
  a new install translates each shader the first time it is drawn.
- The NSP path (a base NSP with an update NSP) has been checked with an update NSP only; the port was played from the
  XCI.
- The iPhone / iPad build (`--platform ios`) is experimental and untested on a device. Most of its picture is missing until
  shaders are translated ahead of time; see [iPhone and iPad](#iphone-and-ipad-experimental).

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
