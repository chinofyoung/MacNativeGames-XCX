# Contributing

Thanks for helping. This page says how the Xenoblade Chronicles X port works and what a pull request needs. The port
lives in [`games/xcx`](games/xcx); read its [README](games/xcx/README.md) and [work log](games/xcx/CHANGELOG.md) first.
The log's Status table and its newest entries say what is being worked on and what has not been checked yet.

## Legal: no game content, ever

- Never commit game content: no game program or code, RomFS files, art, text, audio, movies, translated shaders or
  libraries, keys or saves. Screenshots show game imagery and stay local (`docs/screenshots/` is ignored by git).
- Keep game files, keys and work folders where git ignores them (`nsw/` at the repository root, or `XCX_build/` and
  `mods/` in `games/xcx`). Check `git status` before every commit.
- The repository is MIT. Write your own code. Do not copy GPL code or code from other mods; reading them for ideas is
  fine. Third-party code you bring in keeps its license, and its source and license go into
  [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
- We do not help anyone obtain keys or game files.

## How the port works, and what it must not become

- **The game's own ARM64 code runs natively on Apple silicon.** It is rewritten ahead of time, loaded and relocated,
  host calls go through gates, system services are answered by a small host (HLE), and NVN graphics are rebuilt on
  Metal.
- **No CPU emulation.** No interpreter, JIT, dynamic binary translation or CPU emulator (Unicorn, QEMU, the CPU backends
  of Ryujinx or yuzu). Do not copy Horizon services, IPC or schedulers from an emulator. An emulator may be used outside
  the port as a reference or a check (the rewrite check `equiv.py` compares rewritten and original code in Unicorn; comparing a
  picture or confirming a behavior is also fine); it is never part of the port.
- **Fix the platform, not the symptom.** ABI, TLS, loading, memory, threads, input, files and saves, audio, NVN on Metal
  and the app life cycle come first. A platform bug is fixed in the platform layer, not hidden behind a patch in game
  code. When the host stops on something the game asks for, answer that one thing the way Horizon would, and record in
  the work log what stopped, where, and how it was answered.
- **Game patches for features are welcome** (frame rate, resolution). Every patch checks the original instruction at its
  address first and stops with an error when it differs; it never writes to an unchecked place. Exefs patches for
  1.0.1 go in `games/xcx/mods/1.0.1/<name>/exefs/` and are checked against main 1.0.1's build ID
  (`make patches`).
- **The game is only ever rebuilt from the original files.** The scripts start from the unmodified ExeFS and RomFS in
  the player's XCI, apply the rules declared in the repository, and check every input and output against
  `games/xcx/assets/expected-v1.0.1.json` and `runtime/game_version.h`. Never patch an already rewritten binary or edit
  bytes by hand. When you add or change a build step, record what it used and produced in the existing checks rather
  than in a new mechanism.
- **Change game logic as little as possible.** Do not change the player's or the world's state to make something pass.
  Gameplay changes are separate, optional features that can be switched off.

## Repository layout

- The port is `games/xcx`, with its own copy of the runtime, graphics layer, rewriter and scripts (forked from the
  original MacNativeGames repository, which keeps the other games). The only other code is `vendor/ryujinx`, the
  Ryujinx shader translator sources the shader translator is built from.
- The shader translator ships ready-made in `games/xcx/prebuilt/macos-arm64` (checked against its SHA-256 by
  `make shader-tools`). Its sources are `games/xcx/tools/totk_xlate` and `vendor/ryujinx`; rebuild it with
  `games/xcx/tools/totk_xlate/publish_native.sh` (needs the .NET 10 SDK) only when you change them.
- Changes to the graphics layer that only this game needs sit behind `GAME_XCX` (the Makefile defines it), which keeps
  them apart from the code inherited from the other games' ports.
- The NVN layer maps NVN semantics one by one (resources, shaders, state, draws, synchronization, presentation) and
  contains no camera, scene or gameplay decisions. Game-specific handling sits apart, says why it exists, is behind a
  switch, and is never needed for the game to run.

## Picture quality and performance

- **The picture must be correct before it is fast.** Missing objects, black blocks, wrong geometry or changed gameplay
  are never an acceptable price for frames.
- **No loss of picture quality or effects.** A performance change must leave the picture as it was; only
  floating-point differences of the kind the original already has are acceptable. Show it: compare captures or the pass
  structure of the same scene with the change off and on.
- **Measure.** Decide what to optimize from measurements of this game, not from another project's list of
  optimizations. Use existing tools first: Instruments (Metal System Trace, Time Profiler, Game Performance), Xcode GPU
  frame capture, the Metal HUD (`MTL_HUD_ENABLED=1`) and the runtime's own diagnostic switches. Prefer an on / off
  comparison in the same process; runs of the same scene vary by more than many effects. Write your own timer only for
  what the tools cannot show, and say what it measures.
- **An optimization without an immediate frame-rate gain can still be right.** Explain when it applies, what work it
  removes and what it adds. Report the saving in that work, the added cost and the whole-frame change separately; do
  not call a local saving a frame-rate gain.
- Unfinished or faulty experiments stay switched off by default until they are fixed.

## Pull requests

- **One topic per pull request.** Unrelated features, debug code and reformatting go elsewhere. Do not change existing
  keys, defaults or verified hashes (`expected-v1.0.1.json`, `game_version.h`) unless that is the point of the pull
  request.
- **Build what you touched.** `make runtime` builds with `-Werror`; build the app (`make app`) when you change the
  runtime, the graphics layer or the launcher. If you change the shader translator or its patches, commit the rebuilt
  `totk_xlate` and its `totk_xlate.sha256` together and say how you checked that it translates as before. If you could
  not build something, say so.
- **Say how you tested:** built only, or played on a Mac. Name the Mac, and where in the game you were. A build that
  passes does not replace playing the game.
- **Add an entry to [`games/xcx/CHANGELOG.md`](games/xcx/CHANGELOG.md)** for any change to how the game runs: what was
  found, what was done and why, what went wrong on the way, and how it was checked. "Verified" means a check with a
  recorded result, never "it looked right". Update the Status table when a stage changes.
- **Diagnostics are off by default,** switched on by an environment variable or an input-file command.
- **Commit messages:** `<type>: <description>`, with type one of feat, fix, refactor, docs, test, chore, perf.
