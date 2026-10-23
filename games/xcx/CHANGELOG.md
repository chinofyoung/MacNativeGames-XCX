# Xenoblade Chronicles X: Definitive Edition port: work log

A record of how this port is being made, for anyone who continues it or tries the same with another game. Each entry
says what was found, what was done and why, what went wrong on the way, and how it was checked. Newest entries go at
the bottom of [Log](#log); [Status](#status) is kept current.

Conventions: dates are absolute; "verified" means a check with a recorded result, never "it looked right"; commits are
named by hash and subject. Game data, keys and saves are never committed (see `.gitignore` in this folder).

## Status

| Stage | Goal | State |
|---|---|---|
| 0 | Feasibility: does the game fit the TotK / Bayonetta 3 architecture? | done 2026-10-23 |
| 1 | Code and data out of the dump; code rewritten for Apple silicon | done 2026-10-23 |
| 2 | A runtime target with the Bayonetta-only parts compiled out, booting to `nnMain` | done 2026-10-23 |
| 3 | Follow every unknown service / command / NVN call until the first `nvnQueuePresentTexture` | done 2026-10-23 (title screen; menus answer input) |
| 4 | A picture, audio (Wwise), input, saves | in progress: controllable gameplay and battle with characters drawn; saves write; key 2 interpolates (too slow on an M1) |
| 5 | Performance, shaders, packaging as an app | in progress: `make app` builds a double-clickable app that continues the save with the shader cache reused; clear sinking cuts GPU time per frame on an M1 from about 20 to about 17 ms (2026-10-23) |

## Log

### 2026-10-23: stage 0, feasibility

Inputs: one XCI (`Xenoblade Chronicles X Definitive Edition.xci`, 15.97 GB) and `prod.keys` from the same Switch.
The full study, with every command, is `nsw/XCX_build/FEASIBILITY.md` (outside the repository, next to the game file);
its tools are in `nsw/XCX_build/tools/`. Nothing was booted. Verdict: go, with caveats.

Findings, each read from the dump:

- **The XCI holds base and update.** The secure partition has 8 NCAs, 1 common ticket, 1 cert: base
  `0100453019AA8000` v0 and update 1.0.1 `0100453019AA8800` v65536. Both Program NCAs carry the base title ID. No DLC,
  one program per title (Bayonetta 3's update had a second program). The XCI's update partition carries a system
  update, a source of shared fonts.
- **The base is complete**: ExeFS and RomFS stored, not sparse, not compressed. The update has a full ExeFS and a BKTR
  RomFS. Only `main` differs between the two ExeFS; `sdk`, `subsdk0`, `rtld` are byte-identical. **1.0.1 is the target.**
- **Same module layout as TotK and Bayonetta 3**: `main`, `sdk`, `subsdk0`, `rtld`, `main.npdm`. SDK 19.3.2 (nnSdk, NVN,
  movie). `main` (build ID `3F2425864CF226844511065F77AA3E36FB5FC33F`) links NPLN 1.32.1 with gRPC, OpenSSL, WebRTC
  and libcurl, Havok Physics 2023.2 and Wwise 2021.1.10. DT_RELR, which the loader already handles.
- **Graphics are NVN** only. Of 535 names main loads, 430 are implemented in Bayonetta 3's copy of the layer and 7
  are deliberate no-ops; 97 are missing (two-thirds getters or debug; the rest: integer clears, downsample / resolve,
  ReportValue, tessellation levels, viewport swizzles, sample shading, transform feedback, GL interop).
- **The npdm's 49 services and syscall mask equal TotK 1.4.3's.** Service-backed imports no host answers today:
  `nifm` (2), `friends` (11), `account` (OpenUser and others), the network clock, `ssl` Context, `nsd` ResolveEx,
  `ngc` profanity filter, `caps:su` screenshots, swkbd text check, `nn::err::NplnContext`. `main` uses `nn::os`
  fibers, which neither earlier game did.
- **Data**: 53 RomFS files: `sts.ard` (10.2 GB) + `sts.arh`, 5 Wwise `.pck`, 46 VP9 `.webm` movies.
- **The stock rewriter stops on `main`** with `KeyError: 24149652` (`0x1707e94`): capstone ends the per-function scan at
  the inline trap word `0xe7ffdefe`, and two `br x18` jump tables sit past such words. A copy of `patch_nso.py` that
  scans past undecodable words rewrote all three modules; `sdk` and `subsdk0` came out byte-identical to the stock
  tool's. Verified with that copy: `structck` OK for all three (0 violations; a moved branch fails), `equiv` 36,697
  sites, 0 divergent, 900 of 900 mutation controls caught.

### 2026-10-23: stage 1, code and data

**Layout.** `games/xcx` is a fork of `games/by3`: `runtime/`, `app/`, `third_party/` (`d2r`, `d2r_gfx`, `licenses`),
`prebuilt/`, `scripts/`, `Makefile`, `requirements.txt`, `.gitignore`, copied unchanged and then renamed where a name
is involved: the work folder default `XCX_build`, the binary `xcx-native`, the app `Xenoblade Chronicles X.app`
(`org.macnativegames.xcx`, Info.plist `XCXWorkFolder`, launcher env `XCX_WORK`, its own Application Support folder and
log names), the version folder `v101` in the Makefile and launcher. The runtime's logic is Bayonetta 3's, untouched
(only the work folder named in one error message); stage 2 retargets it. `.gitignore` ignores `XCX_build/`, `mods/` and
the game's data formats.

The work folder is `nsw/XCX_build` (next to the XCI, as `TOTK_build` is next to TotK's dump), passed as `WORK=`. It
now has the Bayonetta layout: `extracted/v101/{exefs,romfs,elf}`, `rewritten/v101`. Stage 0's stock rewrite moved
from `rewritten/` to `rewritten_stock/`; the other stage-0 folders stay where `FEASIBILITY.md` names them.

**Rewrite.** The stage-0 fix is now in `third_party/d2r/patch_nso.py`: `disasm_through_traps()` continues capstone
past an undecodable word, leaves the word in place (an undefined encoding that traps on both platforms) and counts it.
`main` skips 5 such words inside x18 functions (all `e7ffdefe`), `subsdk0` 1 (`33e425af`), `sdk` none.
`third_party/d2r/structck.py` writes its one-section aarch64 ELF in Python instead of calling `llvm-objcopy`, which
Xcode does not ship; `LLVM_BIN` points at Xcode's `llvm-objdump`. Both changes are listed in `third_party/d2r/README.md`.

| Module | Sites | Kinds | Undecodable words skipped | Time |
|---|---|---|---|---|
| sdk | 15,078 | 14,614 plain, 171 cond, 55 pcrel, 8 br, 4 windows, 2 + 3 tpidr read / write, 123 tpidrro, 40 cntpct, 58 svc; 98 gates | 0 | 17 s |
| subsdk0 | 9,787 | 9,634 plain, 131 cond, 17 pcrel, 5 br | 1 | 9 s |
| main | 11,930 | 11,083 plain, 167 cond, 100 pcrel, 5 br, 1 spset, 11 windows, 563 tpidr read | 5 | 52 s |

The two previously hidden jump tables (`0x1707e94`, `0x1707f20`, width 256) are in `main.sites.tsv`. The one `spset`
site (`mov sp, x18` at `main+0xaeda04`) is the case Bayonetta 3 added `site_spset` for, handled unchanged.

Verified:

- The `sdk` and `subsdk0` output (`patched.bin`, `fixups`, `gates`, `sites`, `cuts`, `obs`, `params`) is byte-identical
  to a fresh run of the stock `games/by3/third_party/d2r/patch_nso.py` and to stage 0's stock run. Only
  `build_src.sha256` differs, since it hashes the rewriter's own source.
- `main`'s output is byte-identical to stage 0's run with the tool copy: two rewrites give the same bytes.
- `structck.py` (fresh cache, so `llvm-objdump` really ran on the new ELF): OK for all three, 0 violations, 0 failed
  items. Independent `llvm-objdump` denominators: sdk 15,086, subsdk0 9,787, main 11,938.
- `structck.py` negative control: in a copy of `main.patched.bin`, the trampoline branch of the first site
  (`0x1104c`, `b` `0x151f3ca9` → `0x151f3caa`, target +4) gives `STRUCT main FAIL … 失败项=2` ("failed items: 2").
- `equiv.py`, seed 1, 8 jobs:

| Module | Sites | Trials (effective) | Divergent | Mutation controls | Time |
|---|---|---|---|---|---|
| sdk | 14,980 (+98 gates checked structurally) | 45,963 (40,725) | **0** | 300 / 300 caught | 14 s |
| subsdk0 | 9,787 | 29,861 (26,630) | **0** | 300 / 300 | 8 s |
| main | 11,930 | 36,970 (32,375) | **0** | 300 / 300 | 20 s |

  Total 36,697 sites, 0 divergent, 900 of 900 mutations caught; every site had at least 2 effective trials. The hidden
  jump tables had 2 of 2 effective trials each, the spset site 3 of 3.

**Data.** `scripts/merge_romfs.py` (adapted from Bayonetta 3's) finds both Program NCAs through the content metas:
every Meta NCA's PFS0 is read with its hash table and blocks verified, the Application meta names the base Program and
the Patch meta (required to be v65536) names the update Program. The base layer is plain AES-CTR (the script refuses a
sparse or compressed base); the update layers are unchanged from Bayonetta 3's: AES-CTR-Ex table, then the indirect
table over the base section and the update data. The update's IVFC header covers the merged image, so a wrong layer
fails a block hash. The image is streamed straight to `extracted/v101/romfs/romfs.bin`; no separate base or patch
image was ever written.

Verified:

- All six IVFC levels: 1 + 1 + 1 + 4 + 1,709 hash blocks and **874,727 of 874,727** data blocks. 53 files
  (`sts.ard` 10.22 GB, `sts.arh`, 5 `.pck` 3.16 GB, 46 `.webm` 0.95 GB), 14,331,516,488 bytes, SHA-256 `12c1bf46…`,
  re-hashed from disk. 14.28 GB come from the base, 79.8 MB from the update. Merge time 34 s.
- `scripts/romfs_control.py`: one encrypted byte flipped as it is read from the XCI, in the first update-data range of
  the data level (NCA offset `0x1a87a63`, merged `0xb3b6893`) fails with `IVFC level 5 block 9785 failed`, the
  predicted block; one in the first base range (`0x1ae8163`, merged `0x1ad0163`) fails at block 0, as predicted.
  `ROMFS CONTROL OK 2/2 caught`.
- `scripts/extract_code.py` takes the ExeFS of the Program the Patch meta 1.0.1 names
  (`4e7bdfd67782f0ffd791758053a1accb.nca`), with the FS header, hash table and every block verified. Its five files are
  byte-identical to stage 0's extraction (`xcx_inventory.py`, built on `games/totk/scripts/extract_exefs.py`).
  `--program base` gives the five base v0 files, identical to stage 0's too.
- `scripts/prepare_modules.py` wrote `extracted/v101/elf` (main, sdk, subsdk0, rtld; segment hashes verified).
- `assets/expected-v1.0.1.json` pins the ExeFS, the three rewrites and the RomFS (size, SHA-256, file count, bytes from
  base and update) with both Program NCAs.

Pitfalls, so nobody repeats them:

- The XCI has two Program NCAs with the same title ID, so "the Program of title X" is ambiguous. Choose through the
  content metas, never by order or size.
- `structck.py` caches the `llvm-objdump` lines by image hash (`DM_VCACHE`, default `~/dm-work/build/cache/dm/verify`).
  A cached run does not exercise the disassembler; the stage-1 run used a new cache folder.
- Run the repository's Python with `PYTHONDONTWRITEBYTECODE=1` when importing other games' scripts, or `__pycache__`
  lands in their folders.
- In this shell `sort` is an alias and `find` a shell function; use `/usr/bin/sort` and `/usr/bin/find`.

Logs: `nsw/XCX_build/logs/stage1/`. Commits: none; this checkout is not a git repository.

Open questions carried forward: whether SDK-19 NVN struct layouts match `nvn.h`; the shader binaries' GPU code version;
the heap and pool the game requests at boot; whether `nn::os` fibers work on the host's per-thread state; the audio
renderer revision Wwise 2021.1.10 requests.

### 2026-10-23: stage 2, the game reaches `nnMain`

**What changed in the fork** (the runtime was Bayonetta 3's, unchanged, after stage 1):

- `runtime/game_version.h`: XCX 1.0.1, main build ID `3F2425864CF226844511065F77AA3E36FB5FC33F`, version folder `v101`,
  title `0100453019AA8000` (save ownership in `fs_host.c`, `svcGetInfo` 18 in `services.c`). Still no game address
  on purpose: any leftover TotK or Bayonetta `GAME_ADDR` is a compile error, and none turned up.
- `scripts/prepare_runtime.py`: `v101` and `assets/expected-v1.0.1.json`. All three XCX modules have the same DT_INIT
  shape: the constructor-state load is `adrp x19` at +0x10, `ldr` at +0x14 (Bayonetta 3: x20, +0x10 / +0x18); the
  decoder added for Bayonetta 3 reads it without change. `scripts/prepare_patches.py` (exefs mods) takes `mods/1.0.1`
  and checks main 1.0.1's build ID; none are known for this game yet.
- Bayonetta names out: `BY3_PATCHES`, `BY3_DISPLAY`, `BY3_FULLSCREEN`, `BY3_AUDIO`, `BY3_HUD_RENDER` are now `XCX_*`
  (runtime, launcher, the graphics layer's HUD); window and menu titles; `runtime_by3_override` is
  `runtime_xcx_override`; `nn::oe::ExecuteProgram` stops with "Xenoblade Chronicles X has only one" program.
- Kept from Bayonetta 3 on purpose, each to be measured: `DM_POOL_BUFFER_MAX_MIB=2048`, `DM_PRESENT_HZ=60` with
  `TOTK_UNCAPPED=0`, `DM_PRESERVE_INVARIANCE=1`, `DM_NVN_GPU_CODE_MAX_MINOR=31`. The application pool stays the retail
  `0xcd500000` and the heap cap 4 GiB (see "memory" below).
- `scripts/symbolize.py` takes the work folder from `XCX_WORK` (the default `games/xcx/XCX_build` does not exist here).
- System fonts: a read-only copy of TotK's verified extraction (`nsw/TOTK_build/totk/extracted/firmware/shared-fonts`,
  the same six fonts any firmware carries) in `XCX_build/extracted/firmware/shared-fonts`; `make prepare` loads them.

Verified (`nsw/XCX_build/logs/stage2/`):

- `make prepare`: 3 modules, 206,670 relocations, main PLT probe at `0x1742240`, 53 RomFS files, fonts loaded; every
  input checked against `assets/expected-v1.0.1.json`. `make runtime` builds `xcx-native` with `-Werror`.
- `xcx-native rewritten/v101 probe`: pass. Three modules mapped (main 926 symbols, subsdk0 10,395, sdk 23,007), 4,000
  calls main PLT -> sdk -> host timer on 4 threads. The first XCX code to run on the Mac.
- `TOTK_HEADLESS=1 TOTK_VERIFY_STARTUP=1 xcx-native rewritten/v101 boot`: the original `nn::init::Start` ran the SDK
  startup, the RomFS (14,331,516,488 bytes) was verified, every constructor finished with state 2 (main 1,998, subsdk0
  45, sdk 87 init_array entries), and the original `nnMain` ran (`[totk] ENTER nnMain`) until its first IPC, `fsp-srv`.

**Memory.** `nninitStartup` reserves a 60 GiB address region and then maps 1 MiB pages into it until
`MapPhysicalMemory` fails (`0x10801` at `0xc7a01000` mapped, i.e. the whole pool minus the images): the game sizes its
own allocator to the pool and never sets a heap (`heap 0`). That is the expected end condition, not an error. It makes
3,200 `AllocateMemoryPages` lines in every boot log.

### 2026-10-23: stage 3, from `nnMain` to the title screen

Method as Bayonetta 3's: boot headless until the game needs a window, then with one; read where the host stops; answer
that one thing; repeat. Runs have a time limit (a `perl` alarm; macOS has no `timeout`). `scripts/symbolize.py` names
sdk frames; the game's own and the SDK's code are read with capstone from the original images.

Answered, in order of appearance:

| What stopped the host | Where | Answer |
|---|---|---|
| `sm: service "fsp-srv"` | `nn::fs::SetAllocator` -> `IsEnabledAccessLog` | `fsp-srv` over IPC (`fs_host.c`) with exactly the handshake: `SetCurrentProcess` (1) and `GetGlobalAccessLogMode` (1005) = 0. A first guess of 1006 was wrong; the host named 1005. Every file system still goes through the overridden functions |
| `fsp-srv` command 51 | `nn::fs::IsSaveDataExisting(Uid)` (one argument) | override by name: the game's own save data of the local account (`fs_host.c`) |
| `std::__throw_system_error` in `system_clock::now()` | sdk `clock_gettime(CLOCK_REALTIME)` | The SDK's startup points the C library's realtime clock at `nn::time::GetStandardUserSystemClockCurrentTimespec`, which asks the time service the host replaces. Both `...StandardUser...` and `...AdjustableUser...CurrentTimespec` are answered from the host clock (`time_host.c`, `TOTK_TIME_OFFSET` honoured). Found by following the function pointer at `sdk+0xb87018` and its one writer (`sdk+0x91154`) |
| null `IApplicationFunctions` in `nn::oe::EnterExitRequestHandlingSection` | | Enter/Leave as no-ops, ported from the TotK host's Breath of the Wild answer (the window handles exit) (`applet_host.c`); with them `ListAllUsers`, `GetLastOpenedUser`, `OpenUser(Uid)` for the one local account (`account.c`), which stage 0 listed |
| segfault in `GetTouchScreenStates<16>` | | override: one state, no touches, returns 1 (`input.c`) |
| `MapAliasStack` failed -> abort in `ThreadManager::CreateThread` | a thread whose 8 KiB stack is a static array in main's `.bss` (`main+0x278c000`) | `svcMapMemory` (the host's `stack_move`) accepts a module's read-write data as source, as Horizon does, besides heap and alias memory (`services.c`) |
| `stencil-plane texture sampling is not implemented` | `nvnTextureBuilderSetDepthStencilMode(STENCIL)` | kept in the texture description (one spare bit of `nvn_texdesc`); the replay's default view of such a texture is the same `X32_Stencil8` view Bayonetta 3's `nvnTextureViewSetDepthStencilMode` makes; the builder and texture getters return it (`hle_nvn.c`, `totk_nvn_ext.c`, `nvn_mtl.m`, `nvn_tex.h`) |
| `sm: service "bsd:s"`, then `"bsd:a"` | `nn::socket::detail::Initialize` | the SDK asks for `bsd:s` and `bsd:a` first and takes `bsd:u` when sm refuses. sm now refuses those two with `ResultNotAllowed` (`0x1015`), as for an application whose npdm does not grant them (`ipc.c`). The TIPC reply must keep its moved-handle slot (handle 0): the SDK's inlined `sm::GetServiceHandle` reads the result after it, and the first try without it read result 0 and a null handle (`0xe401` abort). `bsd:u` stays offline (ENETDOWN) |
| segfault in `nn::oe::GetPseudoDeviceId` | from the title screen, after the first A press | a per-profile random UUID kept in `user-data/pseudo-device.id` (`applet_host.c`) |
| `sm: service "prepo:u"` | `nn::prepo::AddSessionId`, the first menu choice | `prepo:u` over IPC with `GetSystemSessionId` (10400): one random value per start (`system_services.c`); the report itself is kept local by the existing `PlayReport::Save` override |

Shader tools: `build/shader-service/totk_xlate` from `prebuilt/` (SHA-256 checked by `make`), SPIRV-Cross built with the
Makefile's flags from a copy of `nsw/TOTK_build/src/SPIRV-Cross`, which is at the pinned commit `aa217ae` with a clean
tree (read with `git rev-parse` / `status` only; nothing was cloned or checked out).

**Result: the first frame is presented 0.33-0.64 s after start** (`[totk-boot] 0.637 first presented frame`, NVN window
2 images, 1920x1080, format 0x25); frame 6 is black (`docs/screenshots/xcx-stage3-first-frame.png`). A few seconds later
the title scene is drawn: dark clouds over the coast with the White Whale's silhouette and the loading indicator
(`docs/screenshots/xcx-stage3-title.png`, frame 126). About 2,000 draws a frame, 30 fps (present p50 33.3 ms), GPU
about 11 ms a frame on an M1.

Checked at the title (the user asked for the state, not for more):

- **Input: yes.** Keyboard J (A) and Return (+) through `TOTK_INPUT_FILE` lead through two menus: a centred dialog,
  then a menu panel over a blurred view of Mira's cliffs (`docs/screenshots/xcx-stage3-menu-after-input.png`). It ran
  110 s with eight key presses without a stop (`logs/stage3/i3.log`).
- **Audio: yes, as data.** Wwise opens `audren:u` and `audout:u`; AudioOut runs 48 kHz stereo PCM16 into Core Audio.
  `XCX_AUDIO_LEVEL=1` (new diagnostic in `audio_out.c`) logs the peak of each 5 s: 5,528 to 10,253 of 32,767 on the
  title, 17,466 in the window with the key press. Not listened to.
- **Fibers: the SDK's own.** `nn::os` fibers need no host code: `SwitchToFiber` / `DispatchFiberContext` save and load
  callee-saved registers and `sp` in plain arm64 (no `x18`, no SVC), the current fiber is a TLS value read through the
  rewritten `tpidrro_el0` sites (3 in `SwitchToFiber`), and fiber stacks are mapped with `svcMapMemory` through the
  same `StackGuardManager` path as thread stacks. Evidence that they run: the `GetPseudoDeviceId` crash's guest
  backtrace ended in `nn::os::detail::CallFiberFunction` (`sdk+0x143af8`), i.e. the game's code ran on a fiber.

Seen, not handled (stage 4):

- Text is missing everywhere (menus show their frames and highlight, no glyphs), and the log reports
  `【红】带索引的四边形未实现` ("indexed quads not implemented") twice: the likeliest cause.
- The lower half of the title and menu backgrounds is black, and the title's ground is flat-shaded.
- The GPU code version gate (`DM_NVN_GPU_CODE_MAX_MINOR=31`) was not measured for this game.

Pitfalls:

- `TOTK_GPU_ALLOW_SUBMIT=1` is needed for any picture, as for Bayonetta 3.
- A `TOTK_INPUT_FILE` command with a trailing space (`close `) is refused and stops the runtime.
- Two `shot` commands queued while the game is loading are both served by the next presented frame (same frame number).

Logs: `nsw/XCX_build/logs/stage3/`. Captures: `nsw/XCX_build/shots/`. Commits: none; this checkout is not a git repository.


### 2026-10-23: stage 4, indexed quads

Stage 3 left the menus without text and named the likeliest cause: `【红】带索引的四边形未实现` ("indexed quads not
implemented"), i.e. the graphics layer dropped every indexed draw with primitive 7 (quads). Non-indexed quads were
already drawn through a shared 0,1,2,0,2,3 index buffer; the indexed path had no answer.

**Answer** (`third_party/d2r_gfx/gfx/nvn/nvn_mtl.m`, the indexed branch of the draw encoder, line 10108): Metal has no
quads, so each group of 4 indices (a,b,c,d) becomes the triangles a,b,c / a,c,d, written as a 32-bit index list into
the submit's arena (at most 4 MiB a draw, else a red line). All three NVN index sizes are read; the all-ones index of
the source size restarts (a partial quad is dropped), as on the other index paths; base vertex and instancing pass
through unchanged. An index buffer still being written by pending GPU work is not converted (red line). One log line
per vertex shader names the draws that take the path. `DM_EXP_NO_INDEXED_QUADS=1` restores the old drop, for
measurement only.

Verified (`nsw/XCX_build/logs/stage4/`, input files and captures in `nsw/XCX_build/shots/stage4/`, driven by
`nsw/XCX_build/tools/run4.sh` with the same schedule: J at 22 s, Return at 32 s, J at 44 s, captures at 20/30/42/55 s):

- `q2` (fix on): the path is taken by at least 32 vertex shaders (the log stops naming them at 32), first in frame 10;
  every logged draw is one quad, 4 indices (27 of them 16-bit, 5 8-bit) -> 6 triangle indices. No `【红】` line in the
  log. Text renders in the menus; the title's black lower half and flat-shaded ground are gone. At 42 s the run is in
  character creation ("Modify Avatar / Gender Selection") over a lit 3D field; at 55 s on its "Eyes" page.
- `q3off` (`DM_EXP_NO_INDEXED_QUADS=1`, same input): the stage-3 picture: at 42 s a centred dialog with its frame and
  highlight and no text.
- Captures: `docs/screenshots/xcx-stage4-quads-title-before.png` / `-after.png` (20 s, `q3off_a20` / `q2_a20`),
  `docs/screenshots/xcx-stage4-quads-menu-before.png` / `-after.png` (42 s, `q3off_c42` / `q2_c42`).

The missing text, the black lower half and the flat ground had the one cause; which draws behind the lower half are
quads was not traced.

### 2026-10-23: stage 4, character creation to the first battle

Priority set by the user for this part: a correct picture first; audio is left for later, saves only noted. Runs were
driven live: `nsw/XCX_build/tools/live4.sh NAME LIMIT` starts the game with an empty input file that commands are appended
to while it runs (each capture looked at before the next input). The user also played in several of these windows with
a DualSense; the logs say so (`[input] user input (controller)`), and the name entry and the first steps in Primordia were
theirs.

| What stopped the host or broke the picture | Where | Answer |
|---|---|---|
| `sm: service "ngc:u" not implemented` (`logs/stage4/r2.log`) | `nn::ngc::ProfanityFilter::Initialize(void*, unsigned long, bool)` -> `nn::ngc::detail::CreateServiceByHipc`, at the name entry that follows character creation | `runtime/system_services.c` (`ngc_mask`, override table): main imports five `ProfanityFilter` names; `Initialize` and `SetMaskMode` return success, `MaskProfanityWordsInText(int*, char*, uint32)` sets the count to 0 and leaves the text as typed (offline there is no word list). The SDK's constructor and destructor still run. Verified: `logs/stage4/r3.log`, the name "Chino" is accepted and the game goes on to Primordia and auto-saves |
| Every character missing (avatar in character creation, player and Elma in the field; only the blob shadow drawn) | The skinning pass: draws under `nvnCommandBufferSetRasterizerDiscard(true)` (7,550 calls in 1.9 s of play, `hletrace`) whose vertex shader writes the bone-matrix storage buffer (`e2573156`, a `vertex_id` shader; also `326267ec`, `8ece1e30`). The graphics layer skipped every discard draw (TotK's never had an effect), so the 192,000-byte buffer the skinned vertex shaders read (`b2001614` reads matrix `weight index * 48` from it) stayed `f3f3f3f3` and zeros and every skinned vertex went to one point | `third_party/d2r_gfx/gfx/nvn/nvn_mtl.m`: a discard draw whose vertex shader writes a storage buffer (`prog_vs_stores`) is drawn with a pipeline without rasterization (`PSO_RDISCARD`, `nvn_pso_skey.h`; `pso_desc` sets `rasterizationEnabled = NO`, no fragment function, and `vs_fn_void` builds the void-returning variant Metal requires); after each one `draw()` puts a vertex -> vertex/fragment barrier in the encoder (`enc_r_barrier`, new `RS_BARRIER` in `nvn_render_stream.h`). Other discard draws are still skipped |

How the skinning cause was found, in order (so the method can be reused):

- The G-buffer (`texdump` of a gameplay frame, `shots/stage4/td2/t37200.png`, albedo) has the world and no character:
  the characters were not drawn at all, not lost in lighting.
- `passdump 1` (`logs/stage4/r3.log`): a 3,090-vertex depth pre-pass draw with position + `RG16UI` weight index (a
  character mesh) reads storage buffer `0x715dfc13d0` (192,000 bytes) whose CPU view has 4 non-zero words of 48,000.
- `DM_NVN_WATCH_SB=b2001614:0` (`logs/stage4/w1.log`): no CPU write to that buffer in a minute of play, so the GPU must
  fill it; the shader cache's reflection has three vertex shaders with a writable storage buffer, and `hletrace` shows
  the game toggling `SetRasterizerDiscard`; `draw_inner` returned early for every such draw.
- The first attempt (rasterization off, shader unchanged) failed in Metal: "RasterizationEnabled is false but the vertex
  shader's return type is not void" (`logs/stage4/s1.log`); hence `vs_fn_void`.

Verified (`logs/stage4/s2.log`): `rasterizer discard: drawing vertex shader e2573156d116223e (it writes storage
buffers), frame 137`, no `【红】` line, no stop. Continue from the auto-save: the player character and Elma are drawn
and lit, and the first battle (Origin Blatta) shows both characters, the enemy, damage numbers and effects. The GPU time
of the long frames the log reports (`[长帧]`) has a median of 21.6 ms before (`w1`, 85 frames) and 24.5 ms after (`s2`, 211
frames, including the battle); not a like-for-like measurement.

Captures (`docs/screenshots/`): `xcx-stage4-avatar-gameplay-before.png` (`w1_a`, old build) /
`xcx-stage4-avatar-gameplay-after.png` (`s2_a`), same spot after Continue; `xcx-stage4-avatar-creation-before.png`
(`r2_a`, character creation without the avatar); `xcx-stage4-battle.png` (`s2_c`).

Red lines and stub hits: none in any stage-4 run after the quads fix (`r1`, `r2`, `r3`, `w1`, `s2`: 0 `【红】` lines;
the one line containing 红 is the start-up count of the 99 unimplemented NVN names). An unimplemented NVN function stops
the host on its first call, and none did: no stub was hit up to the first battle. The one stop was `ngc:u` above.

Saves: **they write.** The first auto-save after the name entry made `user-data/save/<uid>/sts/game/{gamedataa,
gamedataa.tmb, avatarmakedata}` (880 KiB), and Continue on the next start loads it into Primordia.

Visual defects seen, not yet fixed or not yet checked:

- The opening cutscene's White Whale is a black silhouette with a rim light against the stars
  (`shots/stage4/r1_b_title.png`, `r3_c.png`). Not rechecked since the skinning fix; it may be correct (its dark side) or
  the same cause.
- Character creation was not recaptured after the fix (no after-picture of the avatar yet); a New Game run is needed.
- Not looked at yet: day-time lighting, water, cities (New Los Angeles), Skells, menus beyond character creation.

Pitfalls:

- The live window takes keyboard and controller input from the user; key 2 toggles MetalFX frame interpolation
  (`r1.log`: `mfx_interp = 1`), so a capture can be of a different mode than intended. Check the log for `[input]` lines.
- `texdump` dumps the frame after the command; on a 2D screen (the "LOGIN ID" movie) the G-buffer targets are rightly
  black. Take it in a 3D scene.
- `DM_NVN_GPU_CODE_MAX_MINOR` was not measured for this game.

Logs: `nsw/XCX_build/logs/stage4/`. Captures: `nsw/XCX_build/shots/stage4/`. Commits: none; this checkout is not a git
repository.

### 2026-10-23: stage 4, MetalFX frame interpolation (key 2)

Key 2 ("30+30") made no frame: the statistics line counted every game frame as missing all four inputs
(`[mfx] 至帧 …：插值帧 0 / 游戏帧 M …（画面 M 相机 M 深度 M UI M）`, `logs/stage4/s2.log`). The interpolator
(`third_party/d2r_gfx/gfx/nvn/totk_mfx.m`) is game-independent; what feeds it (`nvn_mtl.m`) recognised TOTK's passes,
shaders and camera block, and none of them match Xenoblade Chronicles X. The XCX inputs are now chosen at build time:
the Makefile compiles the graphics layer with `-DGAME_XCX`, and `nvn_obj.h` (line 201) sets the XCX variants of the
macros the hook-up already used (`MFX_UI_DRAW`, `MFX_CAM_SLOT` / `MFX_CAM_SIZE`) plus two new ones
(`MFX_IS_CANVAS`, `MFX_DEPTH_FMT_OK`). Without the define, the TOTK expressions are as before; both builds compile.

What each input is in XCX, found from the pass dump of a gameplay frame (`logs/stage4/r3.log`, frame 5541) and the shader
cache:

| Input | XCX | How it was found |
|---|---|---|
| Depth | The G-buffer depth, texture 378, 1920x1080 **D32F with stencil** (Metal format 260), depth test LESS (not reversed) | Pass 467423, 7 colour targets + depth: the existing rule ("three or more colour targets and a depth of their size") finds it once the format is allowed. MetalFX takes depth only, so `totk_mfx.m` copies the depth plane into a D32F texture of its own each interpolated frame (texture -> buffer with `MTLBlitOptionDepthFromDepthStencil`, buffer -> texture, in the interpolation's own command buffer) |
| Camera | The 768-byte scene block at **vertex uniform 0** of the G-buffer pass (the `c3` buffer of the vertex shaders), all row-major: view 3x4 at float 0, projection at 12, view-projection at 28, inverse view 3x4 at 44 (camera position at 47 / 51 / 55) | `075ba413` (a G-buffer vertex shader) multiplies the position by `c3[7..10]`; `TOTK_MFX_CAMDUMP` (new diagnostic) logged the whole block for 1,264 frames (`m1.log`). The projection is OpenGL's (clip z -1..1, which the translator maps to Metal's 0..1, as for TOTK), not reversed, finite far (near 0.5 / far 40000 in the field; 0.8 in some scenes), aspect 16:9, vertical field of view 40 degrees, and no jitter (row 0 / 1 z terms 0 in every logged frame). `xcx_mfx_camera` (`nvn_mtl.m` line 9203) moves the block into TOTK's layout (view-projection to 12, projection to 28; near / far / aspect / field of view computed from the projection). The block is not bound for the pass's first draws (sky, stencil), so the XCX path looks on to the first draw that binds it |
| Scene | The presented RGBA8 texture after its first draw: the frame ends with one pass into the swapchain texture whose first draw is the full-screen composite of the scene (vertex shader `d245cc0c`), and every later draw into a presented texture is UI | Passes 467490 / 467501 write the swapchain texture directly; there is no float canvas between the scene and the UI as in TOTK. So the canvas is any presented texture (`mfx_is_swap`, line 3679, from the list `uw_note_present` keeps) and the scene is copied before its second draw (`MFX_UI_DRAW` counts the draws, `canvas_draws`) |
| UI | Every draw into a presented texture after the composite: field HUD (minimap, quest marker and distance, enemy level labels, hint panel), battle HUD (party panel, enemy bar, arts palette, battle dialogue) | A rule instead of a shader list: the composite is one draw, so the list would only restate "everything after it". The UI layer (attachment 7) is attached to every later pass into a presented texture as for TOTK |

The canvas is display-encoded RGBA8 rather than TOTK's linear float, so `totk_mfx.m` interpolates and outputs it in that
encoding (`out_format`, line 138: RGBA8 in, RGBA8 out, no sRGB conversion). The UI layer is blended with the game's own
factors in that same encoding, so MetalFX's composite of it matches the game's.

Verified:

- Interpolated frames: in gameplay every game frame gets one. `m5.log` (key 2 pressed by the input file in the field):
  `插值帧 897 / 游戏帧 900 …；未插：缺输入 2（画面 2 相机 0 深度 0 UI 2）`, the 2 being the frame the mode came on;
  `m4.log` (key 2 pressed by the user): 1,797 of 1,800, the same 2 missing. Misses before that (`m2.log`, 342 by frame
  1200, not growing after) are the title screen's 2D frames and loading screens, where there is no G-buffer.
- Motion vectors: `TOTK_MFX_DUMP` while the camera turns (`m3.log`, 5.2 degrees a game frame). Check (scratch script):
  `prev(x + motion(x))` against `cur(x)` on a grid of the scene copies, mean absolute difference of the RGB sum: no warp
  27.0 / 27.1 / 26.9 / 23.7 / 21.4, warped by the motion 10.5 / 11.2 / 10.7 / 10.5 / 7.7 (dump pairs 10 / 20 / 30 / 60 /
  90), y negated 21.2 / 21.9 / 21.5 / 18.8 / 17.8, both negated 28.3 / 28.1 / 27.3 / 25.2 / 23.0. The background lines up
  with the motion as given, and the sign and y convention TOTK uses hold for XCX (`TOTK_MFX_YFLIP` not needed). The
  camera orbits the player, so the motion spreads (`TOTK_MFX_MVLOG`: median 44 px, p10 -38, p90 105 px a frame).
- The HUD: in the interpolated frame (`docs/screenshots/xcx-stage4-mfx-interpolated.png`, dump pair 20) the minimap,
  hint panel, labels and quest marker are sharp and at the same place as in the game frame that follows it
  (`xcx-stage4-mfx-game-frame.png`); the overlapping level labels top left are in the game frame too (two enemies). Battle
  HUD with interpolation on: `xcx-stage4-mfx-battle-on.png` (`m5_e_battle`). Motion texture: `xcx-stage4-mfx-motion.png`
  (128 + motion in red / green). All dump PNGs of pair 20: `nsw/XCX_build/shots/stage4/mfx20/`; the raw dump folder
  (0.9 GB) was deleted after reading.
- Colours: the interpolated frame has the game frame's tone (the RGBA8 path is not converted twice).
- No `【红】` line in `m1`..`m6`. Camera cuts: the existing rule (8 units or 25 degrees in a game frame) did not fire in
  these runs (`镜头切换 0`); a cut was not provoked, so that is not checked for XCX.

**Cost: on this Mac (MacBook Air, M1, 8-core GPU) the overlay does not reach 30+30.** Same spot, scripted (`m6.log`,
`TOTK_FRAMEGAP=1`, a new diagnostic: interval between game presents every 150 frames): interpolation off, p50 33.2 /
mean 33.2 ms (30.1 game fps) in every window; 30+30, p50 33.4-36.5 / p90 50-68 / mean 37.5-43.1 ms (23.2-26.7 game fps);
off again, back to 30.1. The FPS box shows `29` off and `23+23` to `26+25` on (`xcx-stage4-mfx-field-off.png` /
`-field-on.png`, `m5`), `25+24` in battle. MetalFX GPU time per interpolation (statistics line): p50 12.3-17.2 / p95
15-23 ms. That is MetalFX itself, not the hook-up: TOTK's probe (`games/totk/tests/metalfx_interp/probe.m`, built and run
on this Mac on an idle GPU) measures 11.2-11.7 ms per interpolated 1920x1080 frame (5.7 ms at 1280x720, 19.6 ms at
2560x1440). The interpolator runs at the game's 1920x1080, not the window's size (`frame interpolator: depth / motion
1920x1080 -> 1920x1080`). The game alone takes about 2/3 of the GPU at 30 fps (load line `GPU 95% (68%)`); 11-15 ms more
does not fit in 33 ms, so the game slows to about 24 fps. On this machine key 2 is a loss (fewer real frames); it needs
a faster GPU.

Known limits:

- Motion comes from the camera only. Characters and enemies moving on their own (the player running, Elma, the Blattas)
  ghost in the interpolated frames; so do rain streaks. The G-buffer has an RG16F target (texture 370, cleared to
  0,0,0.5,0.5) that may be the game's own velocity buffer; not looked at.
- The UI layer is this frame's UI over a half-step scene, so labels anchored in the world (enemy levels) are half a frame
  of camera motion ahead of their enemy in the interpolated frame.

Logs: `logs/stage4/m1.log` (scene block dump), `m2` (first frames made, motion log), `m3` (dumps), `m4` (the user's
window), `m5` (scripted field / turn / battle, `tools/sch_mfx.txt`), `m6` (on / off frame gap, `tools/sch_gap.txt`).
Captures: `nsw/XCX_build/shots/stage4/m*_*.png`, `docs/screenshots/xcx-stage4-mfx-*.png`. Code: `Makefile` line 19,
`third_party/d2r_gfx/gfx/nvn/nvn_obj.h` 201, `nvn_mtl.m` 3676-3679, 4301-4305, 9167-9215, 9943, 12541, 12569-12595,
`totk_mfx.m` 12-15, 39, 138-163, 176, 218, 278-310.

### 2026-10-23: stage 5, the app

Goal: a double-clickable `Xenoblade Chronicles X.app` that starts, plays and keeps the player's progress without a
terminal. It follows Bayonetta 3's app design (`make app`): a small launcher bundle that names the work folder in
Info.plist (`XCXWorkFolder`) and `exec`s `<work>/build/xcx-native` from there, so none of the game data (the 14.3 GB
`romfs.bin`, which holds `sts.ard`) is copied or cloned. TotK's self-contained bundle (root `build_app.py`, game data
cloned into the app, Metal cache warmed for the app's own path) is the later publishing step and was not done here.

| What | Where | Why |
|---|---|---|
| The app | `nsw/XCX_build/Xenoblade Chronicles X.app` (`APP ?= $(WORK)/$(APP_NAME)`; it used to be built into the current folder, i.e. this source tree), 2.6 MB on disk: launcher, Info.plist, the icon from the game's control data, ad-hoc signature (`codesign --force --sign -`, `codesign --verify --deep --strict` passes) | Nothing large in it, so no clone is needed; `make install` copies it to /Applications or ~/Applications (now with `APP_NAME`, as `APP` is a path) |
| Saves, account, pseudo device id, pipeline list | `~/Library/Application Support/MacNativeGames/XCX/profile` (`TOTK_PROFILE_DIR`, as Bayonetta 3's app) | On the first start without `profile/account.uid` the launcher **copies** `<work>/user-data/{account.uid, pseudo-device.id, pso_user.tsv, save/}` there (`seed_profile`, `app/launcher.m`) and logs each copy; never overwrites, never touches the originals. Copy rather than pointing the app at `user-data/`: the app keeps its profile where a Mac app keeps one, the terminal runs keep theirs, and a test run can never write into the player's game (or the reverse). The same account id means the same save folder name |
| Shader cache | `<work>/build/shader-cache/rt-totk-native-v4-unverified` (7,663 entries, unchanged location) | The runtime puts it next to the binary (`totk_shader_rt.m` `root_dir()`), and the app runs that same binary, so the app and `make boot` share one cache and the same path for Metal's own cache |
| RomFS verification record, game slot, input file | `~/Library/Application Support/MacNativeGames/XCX/` (`.romfs-verified-v1`, `game-slot/`, `input.txt`) | The first app start hashes the RomFS once (its first frame came at 9.8 s instead of 0.35 s) and records it there |
| Log | `~/Library/Logs/MacNativeGames/xcx.log` (the previous run: `xcx.previous.log`) | |

Launcher defaults (`app/launcher.m`), set so a double-click gives the picture the stage-4 runs had by hand:

- `TOTK_GPU_ALLOW_SUBMIT=1` (the one switch every stage-4 run set), plus Bayonetta 3's isolated GPU paths
  `TOTK_GPU_WIDEN`, `TOTK_GPU_BLOCKRT`, `TOTK_GPU_MIDCLEAR` as before (each one otherwise stops the host the first time
  its path is reached; none was reached in stage 4) and `DM_PRESENT_LINK_FALLBACK=1`.
- `TOTK_GPU_BUDGET_MB` is no longer set to Bayonetta 3's 5000: the runtime's default (40% of memory, 6,553 MB on this
  16 GB Mac, logged as `显存预算 6553 MB`) is what every stage-4 run used.
- The compact HUD: `TOTK_HUD` is unset at launch (never `corners`). MetalFX frame interpolation off at launch
  (`TOTK_MFX` unset; key 2 still toggles it): on this M1 it was a net loss (stage-4 interpolation entry).
- The presets are now this game's: "Original (30 fps, window)" (the default) and "Original (30 fps, full screen)", both
  without MetalFX spatial upscaling, which has not been checked for this game (before: Bayonetta 3's five presets, the
  default one with the 60 fps patch and MetalFX). The MetalFX setting is also off when no choice is saved. The 60 / 120
  fps, fixed-900p, LOD and grain choices stay in the window; their exefs patches do not exist for this game yet, and the
  window says so. Bayonetta 3's warning about its LOD patch is gone.
- `XCX_PLAY=1` (new, for scripted checks): Play at once with the saved settings. Pressing Play by script was not
  possible here: `osascript` has no assistive access on this Mac (`-1719`).

Build (from `games/xcx`; the runtime was rebuilt because the Makefile changed, same sources):

```sh
N=$(cd ../../nsw && pwd)   # absolute path of the folder holding the XCI, the keys and XCX_build
make app WORK=../../nsw/XCX_build PY=$N/XCX_build/venv/bin/python GAME="$N/Xenoblade Chronicles X Definitive Edition.xci" KEYS=$N/prod.keys
```

`GAME` and `KEYS` must be absolute: the icon recipe runs from `scripts/`. Free space before and after: 16 GB.

Verified (`nsw/XCX_build/tools/app5.sh NAME LIMIT`: `open --env XCX_PLAY=1 --env TOTK_FS_LOG=1` on the app, then the
app's own input file, hard time limit; logs `nsw/XCX_build/logs/stage5/`):

- `a1` (first start, 480 s limit): the launcher copied the four profile items (`[launcher] profile: copied ...`), hashed
  the RomFS once, `host save storage: ~/Library/Application Support/MacNativeGames/XCX/profile/save/c88ba527...`, first
  frame at 9.8 s. The user played with a DualSense during this run (`[input] user input (controller)`: Continue, key 2
  through every interpolation mode), so it only shows the app working end to end, not the scripted path; its capture is
  `nsw/XCX_build/shots/stage5/a1_63s_user_continue.png` (Primordia, from the copied save).
- `a2` (420 s limit, scripted): `RomFS unchanged; reused full SHA256 verification record`, first frame at 0.35 s;
  title at 50 s with **Continue** offered and selected (`docs/screenshots/xcx-stage5-app-title.png`); J (A) at 55 s; at
  95 s the player and Elma in Primordia with the field HUD (`docs/screenshots/xcx-stage5-app-continue.png`); `close` at
  115 s, `window closed; graceful guest shutdown pending` and the process gone within a second, as in the stage-4 runs.
  Up to the user's first input (after the 95 s capture) interpolation stayed off and the game ran at 30 fps (frame time
  p50 33.24 ms in every 300-frame window). The 110 s capture was taken after the user switched interpolation on and was
  moved to `nsw/XCX_build/shots/stage5/a2_110s_field_user_interp_on.png`.
- Shader cache reused: 0 `[shader] translated` lines in `a1` and `a2` (the log names the first 8 translations of a run),
  and every one of the 102 long-frame lines of `a2` says `新建管线 0 条 + 着色器库 0 个` (no new pipeline, no new library).
- MetalFX upscaling and output both logged off; GPU budget 6,553 MB; no `【红】` line; no `FATAL`.
- The originals are unchanged: SHA-256 of every file in `<work>/user-data` before and after both runs
  (`logs/stage5/user-data-before.sha256`). In the app's profile only `pso_user.tsv` changed (9 lines appended in `a1`,
  the pipelines of the interpolation modes the user switched through); the save files are byte-identical to the
  originals (no save was made in these runs).

Left for the root `build_app.py` (not done): a self-contained app with the RomFS and code cloned in (APFS
`clonefile`, as TotK's), a bundled shader seed and Metal cache warm-up for the bundle's own binary path, seeding the
player's profile from a work folder that will not exist on another Mac, and the 60 fps / resolution patches the
settings window still lists.


### 2026-10-23: stage 5, GPU time on an M1 (clear sinking)

Goal: less GPU time per frame on the MacBook Air (M1, 8-core GPU) without changing the picture. The game renders
1920x1080 at 30 fps and used about 20 ms of GPU per frame, so MetalFX interpolation (11-17 ms, stage 4) did not fit.

**Benchmark** (`nsw/XCX_build/tools/`, logs `nsw/XCX_build/logs/stage5/`, captures `nsw/XCX_build/shots/stage5/`):

- `bench5.sh NAME SCHEDULE [ENV=...]` runs the game from the work folder like `run4.sh`. Every run gets a fresh copy of
  `user-data` as its profile (`bench5-profile`), so neither the player's saves in `user-data` nor the app's profile are
  ever written. Per-frame timing goes to `NAME.tsv` (`DM_NVN_TSV`, timing rows only); `@gpu_ms` is the frame's summed
  command-buffer GPU time, the number the `计时` line reports as GPU. It runs `build/xcx-native-exp`, never the default
  binary the app launches. `build_exp.sh` builds that binary and puts `build/xcx-native` back to
  `build/xcx-native.default`.
- `sch_bench5.txt`: title (window 30-48 s), Continue (J), the field standing still (96-106 s), the camera turning
  right and left (107-116 s), then R (target) and A: the battle with the Pebble Blatta (124-150 s). `bench5_stats.py`
  gives p50 / p90 / mean per window and counts `[input] user input` lines.
- `ab5.sh NAME FLAG` runs `sch_ab5.txt`, which switches a run-time flag (`flag NAME 0|1`) on and off in the same process:
  6 s windows on the title, 5 s in the field, 4 s in battle. `ab_stats.py` gives each window's GPU median, the mean per
  state and the difference of each "on" window against its two neighbours. Run to run, the same scene varies by
  1-1.5 ms (rain, enemies, the time of day, other load on the Mac; one baseline run, `fb3`, measured 24 ms in the field).
  The windows of one process vary by about 0.3 ms, so every effect below comes from the A/B runs.
  - Pitfall: the title scene's GPU load has its own 6 s cycle. 3 s windows locked onto it and showed effects that
    were not there, so title windows are one whole cycle long and use the mean.
  - Pitfall: `@t_s` counts from the runtime's clock start, which comes 0-2 s after the driver's. `ab_stats.py` fits
    the shift from the submission thread's CPU time, which the flag changes at once.
- `pass_struct.py LOG OFF ON` compares the render passes of two dumped frames (`passdump`). `imgdiff.m`
  (`tools/bin/imgdiff`) gives the mean pixel difference of two captures.
- The user played in several of these windows (`[input] user input`; `s0`, `s2`, `ab6`, `ab7`, `fb1`, `fb2`). Those
  runs' measurements are void and are not used below; `fb1` and `fe1` were also closed early.

**Where the GPU time went** (`flag pass_gpu 2` with `TOTK_PASS_GPU_FILE`: GPU time of each encoder; `p1`, field frame
2781, 90 render encoders, 22.8 ms):

- The G-buffer (seven RGBA8 / RG16F colour targets and a D32S8 depth, 1920x1080) was drawn in five pieces of one
  pass. They are separated by a 1024x1024 depth pass of skinned character meshes, which looks like a per-character
  shadow. The first two pieces cost 3.5 ms each; every piece loads and stores all eight attachments.
- Before that, each of the eight G-buffer targets was cleared in a pass of its own: 0.15-0.30 ms each, 1.6-1.9 ms in
  all. These passes only exist in the translation. The game sets one target, clears it, sets the next... and the
  layer lands each pending clear in a clear-only pass when the targets change. On a tile-based GPU such a pass writes
  the whole target to memory, and the G-buffer pass then reads it all back.
- Measurement-only switches (each short run against a short baseline of the same day):
  - `TOTK_EXP_NOLOAD=1` (every Load becomes DontCare; the picture is wrong) took the frame from about 20 to 15 ms, so
    attachment loads cost about 5 ms.
  - `TOTK_BARRIER_NARROW=2` gave no gain.
  - Dropping every barrier (`TOTK_BARRIER_START=0 TOTK_BARRIER_END=0`, races) made the summed command-buffer time go
    up, not down: overlapping command buffers are counted twice, so this metric cannot size the barriers.
  - The whole `DM_NVN_TBDR=1` suite was 3.5 ms slower (23.6-24.4 ms). Of its rewrites, only the clear-only pass hoist
    and the same-target continuation fired.
  - The rasterizer-discard skinning draws from stage 4 run 56 a frame, each followed by a barrier. A same-process A/B
    without those barriers (`ab3`) showed no difference (field +0.2, battle -0.3 ms), so they are not a cost worth
    removing.
  - First-draw load elision (the TBDR suite's coverage proof alone): the proof never holds for this game's full-screen
    passes (`why=2`: the position is not a plain vertex attribute), so it saved nothing and was removed again.
  - `flag exp_gload 1`, which skips the G-buffer pieces' loads, was a measurement only and makes a wrong picture. The
    user saw its artifacts while playing in that test window (`ab6`). It was dropped and is not in the source.

**Clear sinking** (`third_party/d2r_gfx/gfx/nvn/nvn_mtl.m` 8247-8430, `#ifdef GAME_XCX`; hooks at 1054, 1955, 2359,
4382, 11210, 11237-11238). Neither TotK nor Bayonetta 3 builds any of it.

- What it does: at `SetRenderTargets`, a full clear that is still pending is not landed in a clear-only pass. It moves
  to the next pass that attaches the same texture level / layer, which then starts with a Clear load action instead of
  a Load. When that pass starts, the attachment holds the clear value either way.
- The proof (`csink_scan`): scan this submission forward in replay order, through calls, the replay's own call stack
  and the remaining handles. Until the first draw with the texture attached:
  - no draw or dispatch may read or write a texture whose pool memory overlaps it. Reads and writes are found through
    the program's reflection and the bound texture / image slots; the layer binds only what the reflection declares.
  - no attachment, clear, copy or texture clear may overlap it. Overlap is by memory, so other NVN texture objects on
    the same storage (storage-shared Metal textures) count.
  - every program met must already be resolved.
- Anything not proven keeps its clear-only pass. A sunk clear not taken by the time the replay has passed that draw (a
  skipped draw), or by the end of the submission, is landed then in a clear-only pass of its own, still before any
  access.
- A clear that failed its proof is not scanned again for 30 frames. Skipping a scan only keeps the old pass, so this is
  safe, and it cut the extra CPU time on the submission thread from about 1.5 to about 0.5 ms a frame.
- `XCX_CLEAR_SINK=0` or `flag clear_sink 0` turns it off. Every 900 frames a `[clear-sink]` line counts the tries, the
  moved clears, those taken by their pass and those landed alone, and why the rest were not proven. In `ab5`: 54,000
  moved, all taken by their pass, none landed alone; not proven: about 3,000 for reaching the end of the submission,
  about 1,000 for a read, about 150 for an unresolved program.
- In the frame: 107 -> 94 passes on the title, 92 -> 80 in the field, 101 -> 89 in battle. The first G-buffer piece
  went from 3.5 ms (loading 8 attachments) to 1.4 ms (clearing them), and the clear-only passes are gone (`p2`).

GPU ms per frame, same process, flag off / on (mean of window medians; paired = each "on" window against its
neighbours):

| Run | Title | Field | Battle |
|---|---|---|---|
| `ab4` | 19.55 / 17.62 (paired -1.96) | 20.36 / 17.75 (-2.50, range -1.92..-3.23) | 19.45 / 16.70 (-2.56, range -2.18..-3.30) |
| `ab8` | 22.31 / 19.22 (-2.99) | 21.43 / 19.20 (-2.32, range -1.77..-3.39) | 22.08 / 18.94 (-2.99, range -1.52..-4.22) |
| `ab2` (field, battle; its title windows hit the 6 s cycle) | | 20.20 / 17.50 (-2.64) | 19.92 / 17.20 (-2.69) |

Separate runs of the whole schedule (`bench5.sh`; only windows without user input), GPU p50 / p90 ms:

| | Title | Field | Turn | Battle |
|---|---|---|---|---|
| before (`b3`, `fb2` title, `fb3`) | 19.95 / 21.80, 19.75 / 20.56, 20.26 / 23.56 | 21.46 / 23.12, 24.13 / 31.70 | 21.31 / 23.57, 25.05 / 30.40 | 20.53 / 22.34, 24.51 / 33.56 |
| after (`fe2`, `fe3`) | 17.61 / 19.45, 17.85 / 18.30 | 16.80 / 18.43, 16.78 / 19.07 | 16.25 / 18.54, 15.81 / 17.27 | 16.64 / 18.10, 16.64 / 18.69 |

The game alone now takes about 17 ms of the 33.3 ms frame at 30 fps, where it took about 20 ms. That leaves about
16 ms of headroom, where there was about 13. Frame pacing is unchanged (frame time p50 33.2 ms in every run).

Picture checks:

- Pass structure (`st1`, `tools/pass_struct.py`): a frame dumped with the flag off is rewritten by moving each
  clear-only pass's clears into the next pass that loads the same texture. The result is compared with a frame dumped
  with the flag on: attachments, load actions and clear values per pass.
  - Battle: 89 of 89 passes identical.
  - Field: 78 of 80. The other two are a G-buffer piece and a depth pass that vary from frame to frame in both states.
  - Title: 90 of 93 / 94. The others are double-buffered textures (444 / 445) and the swapchain (1 / 2) alternating,
    plus one extra G-buffer piece.
- Captures of 4 consecutive frames in each state (`st1_*_off_*`, `st1_*_on_*`). The mean pixel difference between
  consecutive frames (rain, animation) is the same in both states: title 0.21 / 0.21, field 1.11 / 0.98, battle
  1.16 / 1.06 (0-255). Side by side: `docs/screenshots/xcx-stage5-perf-clearsink-{title,field,battle}-{off,on}.png`.
- No `【红】` line in any run with clear sinking.
- Not checked: New Game / character creation, cities, Skells, cutscenes, interpolation (key 2) with it on.

**`DM_NVN_GPU_CODE_MAX_MINOR`** (open since stage 2): the game never asks for it. With `DM_NVN_LOG=1`, `g1log` lists
every `nvnDeviceGetInteger` the game makes: 0, 1, 0xa, 0xf, 0x10, 0x11, 0x12, 0x38, 0x51 and 0x5c, never 0x30
(GLSLC max GPU code minor). With 14 instead of 31 (`x4gate14`): the same shaders (0 new translations, 0 new
pipelines), the same picture and GPU time within noise (title 19.96 / field 19.90 against 19.90 / 19.77). The setting
in `system_services.c` is inert for this game.

**Left, with estimates:**

- The G-buffer drawn in pieces around the character-shadow depth pass: after clear sinking, the second piece still loads
  all eight attachments (3.3 ms with 1,830 draws), and the first stores them. Drawing the shadow pass's few draws after
  the G-buffer (or before it) would save one load and one store of about 70 MB, roughly 1-2 ms. The proof needs the
  bone-matrix buffer that the skinning draws write in between, and the per-frame uniform updates (the TotK pass
  deferral refuses those). Not attempted.
- The lighting and post-processing passes (1.6, 0.9, 0.9, 0.9 ms...) are mostly the game's own work. Their full-screen
  first draws cannot be proven to cover the target (above).
- Twelve 64x64 passes (exposure / luminance) cost 0.74 ms, two of them about 0.5 ms with a single draw. Their shaders
  were not looked at.
- About 80 render passes a frame, each starting and ending with a full barrier (the layer's Metal 4 rule). Their fixed
  cost was not separated (see above).

**60 fps or interpolation on this M1:** still no. At about 17 ms of GPU per game frame, 60 fps would need about
16.7 ms with no CPU or present overhead: within reach of one or two more fixes like this one, but not there. 30+30
interpolation needs 17 + 11-17 ms (MetalFX alone, stage 4), i.e. 28-34 ms of the 33.3 ms. The low end of that range
fits, the high end does not; it was not re-measured.

Logs: `nsw/XCX_build/logs/stage5/` (`b*` baselines, `s*` short baselines, `x*` measurement-only switches, `p*` pass
profiles, `ab*` same-process A/B, `st1` pass structure, `fb*` / `fe*` final runs). Commits: none; this checkout is not a
git repository.

### 2026-10-23: local folders moved

The `nsw/` paths in the entries above are the old layout. The personal files (XCI, `prod.keys`, `title.keys`, `fw/`,
`fw.zip`) moved from `nsw/` to `personal/`, and the work folder from `nsw/XCX_build/` to `XCX_build/`, both at the
repository root and ignored by git (`nsw/` is gone; the ignore line stays for old checkouts). Moved with `mv`, nothing
copied or deleted. Since the runtime and the app hold absolute paths of the work folder, these were redone from the new
place:

- `make prepare runtime WORK=../../XCX_build PY=<abs>/XCX_build/venv/bin/python`: `shared_fonts_config.h` held the old
  font paths, so the binary changed (`build/xcx-native` sha256 `fbc19c8e...`, was `fecb7e29...`; same sources, clear
  sinking).
- `make app WORK=../../XCX_build PY=... GAME=<abs>/personal/<XCI> KEYS=<abs>/personal/prod.keys`: Info.plist's
  `XCXWorkFolder` names the new folder.
- The venv's `bin/` scripts and `pyvenv.cfg` had the old path written into them and were pointed at the new one;
  `venv/bin/python` itself worked unchanged.

Checked with `XCX_build/tools/app5.sh` (150 s limit, log `XCX_build/logs/stage5/m1_move.log`): the profile in
Application Support was not seeded again, the RomFS record was reused (no re-hash), the shader cache was reused (0 new
shaders), the save loaded into Primordia, no failure lines, and the profile's ten files are byte-identical after the
run. The run also logged real keyboard input from the user.
