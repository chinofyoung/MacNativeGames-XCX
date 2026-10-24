# Shared shader list for Xenoblade Chronicles X: design

Date: 2026-10-24. Status: draft for the owner's approval.

## Why

An iPhone or iPad cannot translate the game's shaders while it plays. The IPA carries only the shader variants the
builder's own Mac has translated, so anywhere the player has not been on the Mac, draws are skipped. On 2026-10-24 this
showed up as the player character drawn without their outfit; `ios/tools/fill_shaders.py` fixed it after the fact
(201 variants), but every player would have to repeat that loop for every new area, outfit, enemy and effect. On the Mac
the same gap costs a stall the first time each variant is drawn.

Translating "every shader the game can need" from the game files alone is not possible: the build can find each
shader's code, but a shader is translated per *variant* (the bind state of a draw: texture kinds per slot, attribute
types, output formats), and variants only become known when the game draws. The original MacNativeGames project
(Tears of the Kingdom) solved this with a **shared shader list**: a file of identifiers that players grow together, and
a build that translates the listed variants from each player's own game files. This design brings that to this port and
makes contributing near effortless.

Success: a new player builds once and their first launch, on Mac or iPhone, has the full picture anywhere the community
has already played. Players contribute without knowing how any of it works, and the owner does nothing per
contribution.

## Goals

1. **The list.** One file in the repo naming the shader variants and render pipelines players have met. Identifiers
   only, never game data.
2. **Build-time translation.** `build_app.py` (Mac and `--platform ios`, CLI and `--gui`) reads the list, finds each
   listed shader in the player's own game files, verifies it by hash, translates it, and ships the result in the Mac
   app and in the IPA's shader seed, with the warm-up manifests the runtime already reads (`pso_title.tsv`,
   `pso_manifest.tsv`).
3. **Collection in play.** The game records each variant it met that the list lacks, on Mac and iPhone. Only part of
   this exists: `totk_shader_rt.m` calls `totk_contrib_add` for shaders (`V` rows) and iOS sets `TOTK_SHADER_CONTRIB`
   in `ios/Sources/main.m`, but the Mac launcher never sets it, so the Mac collects nothing today, and render pipeline
   rows (`R`/`B`, upstream's `pso_contribute`) are not recorded at all. The Mac app also has no startup shader seed yet
   (`build_app.py`: "this port has no startup shader seed yet"), so the Mac needs seed import, not only the iPhone.
4. **Prompting the player.** When enough new rows have collected, the player is asked to share them, with a one-step
   way to do it. Mac: from the game/launcher and the builder window. iPhone: the Mac side picks up the device's file
   (the stubbed `ios/tools/shader_contrib_watch.py`) and/or the builder window offers it on the next build.
5. **Submission.** A GitHub issue form for this game. The player attaches or pastes their contribution file.
6. **Automatic merge.** A GitHub Actions workflow handles each submission with no owner involvement: validate, merge new
   rows into the list, land the change on `main` once the checks pass, thank and close the issue. Owner decision:
   auto-merge when checks pass, no manual review.
7. **Public guide.** A standalone Markdown page explaining the whole thing to players, written so the owner can publish
   it on their website as is (see "The public guide" below).
8. **Seed the list** from what the owner has already played: the Mac's translation cache and observed keys, the
   existing contribution files, and the 201 variants in `XCX_build/build/ios-fill/`.

## Non-goals

- Distributing translated shaders, metallibs, pipeline caches or any bytes from the game. Each build makes these from
  the player's own files. (This is the legal line; see anti-goals.)
- Guaranteeing that nothing is ever missing. Variants nobody has met yet are still translated in play on the Mac and
  skipped on iPhone until shared (or filled with `fill_shaders.py`).
- A server, account system or anything beyond GitHub.
- Supporting other games. This repo is Xenoblade X only.

## Anti-goals (would be a failure even if it works)

- **Any game data in the repo or in a submission that gets merged.** The workflow must reject anything that is not a
  well-formed identifier row.
- **The workflow trusting issue content.** Issue text is untrusted input from anyone on the internet. It must never be
  executed, interpolated into a shell command, or allowed to change anything other than adding rows to the list file.
- **Making the player understand shaders.** If the guide or the prompt needs the word "variant" to be followed, it has
  failed.
- **Breaking existing builds.** A build with an empty or missing list, no network, or a game version with no matching
  rows must still work exactly as today.
- **Slowing the normal rebuild path noticeably.** `build_app.py` currently takes 1.7 s on an unchanged rebuild;
  translation of the list must be cached so an unchanged list costs about nothing.

## Constraints and facts the builder needs

- **Prior art to port, not reinvent.** The upstream remote (`upstream/main`, ly05010419/MacNativeGames) has the full
  original implementation for TotK. Read it first:
  - `docs/shader-list.md`: the list format (`V`, `L`, `S`, `C`, `R`, `B` rows, keys, deps, flags), warm-up scope, and
    the contribution flow.
  - `games/totk/scripts/shader_list.py`, `locate_shaders.py`, `prepare_shader_seed.py`, `collect_shader_issues.py`.
  - `games/totk/assets/shader_list.tsv` for what real data looks like.
  - `.github/ISSUE_TEMPLATE/shader-list.yml`, `.github/workflows/tests.yml`.
  - `tests/test_shader_list.py`, `test_shader_seed.py`, `test_seed_digests.py`.
  - In this repo's history: commits `f7948d2`, `bd59dc9`, `d655ee4`, `e452fac` (contribution file location, reminders,
    pipelines in the contribution file, only non-default variants).
  - Commit `6611433` removed `.github/`, `build_app.py`'s TotK parts and `tests/` from this repo; the runtime half was
    kept.
- **The XCX-specific unknown: finding the shaders in the game files.** TotK's build finds shaders in BNSH files, agl
  `.sharcb` packages and the game module, with `L` locator rows for anything else. Xenoblade X is a different engine
  (Monolith), and its RomFS is read here as one `romfs.bin` with an index (`XCX_build/extracted/<v101|v200>/`). How
  XCX stores its shaders is not yet known. The first task of the plan is to find out (the runtime already has the guest
  code of every shader it translates, which can be searched for in the RomFS) and pick between format-aware discovery
  and locator rows recorded at runtime. The shader key (SHA-256 of NVN control section + code, as the runtime computes
  it) is the check that the right bytes were found.
- **Two game versions** (1.0.1 and update 2.0.0) are supported side by side. Keys from both may sit in the list; a build
  uses only those found in the selected version.
- **Existing pieces:** `ios/tools/build_ipa.py collect_seed`, `ios/tools/compile_seed.py` (needs the Metal Toolchain for
  device metallibs, else ships MSL), the translator service `build/shader-service/totk_xlate`, `totk_shader_seed.h`
  (seed import), `fill_shaders.py` (stays as the stopgap for the gap the list does not cover).
- **Platforms:** Mac app and iOS IPA from the same list. The `--gui` builder window must show the same progress and
  prompt as the CLI.
- **Repo and remote:** the public repo is `chinofyoung/XC-Native` on GitHub; issues and the workflow live there.

## Decided by the owner

- The community-list model: players share identifiers, each build translates from its own game files.
- Submissions are merged automatically when the checks pass. No manual review step.
- A Markdown guide for the owner's website covering instructions, the whole process and the explanations.
- **Abuse guards (limits + undo).** The workflow cannot tell a real bind state from an invented one, so it limits
  instead: one accepted submission per GitHub account per day, accounts at least 7 days old, a cap on rows per
  submission, and a one-command undo that removes everything a given issue added (made possible by the audit trail).
  Rejections get a friendly comment saying why, and the guide's FAQ explains the rules.
- **Builds download the newest list.** Each build (CLI and `--gui`) fetches the current list from `main` on GitHub
  (`chinofyoung/XC-Native`), validates it with the same row checks as the workflow, caches it, and falls back to the
  cached or in-repo copy when offline or when the fetch fails. This works for players who cloned and for those who
  downloaded a zip; nobody has to update by hand. The contribution file therefore cannot assume a clone: on the Mac it
  lives in the app's profile folder (`~/Library/Application Support/MacNativeGames/XCX/`) and the prompt opens it in
  Finder.

## Calls made by me (Claude), open to the owner's correction

- Submission goes through a GitHub issue form, as upstream did. It needs a GitHub account; that is accepted as the cost
  of having no server.
- Validation accepts only row kinds that carry identifiers (`V`, `R`, `B`, and `L` if the locating task needs it), each
  matched field by field against strict patterns (hex of fixed lengths, canonical compact JSON for deps with a bounded
  size, known flag letters). Anything else in a submission is dropped and reported in the thank-you comment; a
  submission with no valid new rows is closed with a friendly note. A size cap per submission.
- Rows that the build's built-in default variants already serve are not added (upstream's rule, owner of that repo
  decided 2026-10-22).
- Every automatic merge leaves an audit trail: a commit (or merged PR) on `main` that names the issue number.
- The prompt threshold starts at upstream's value for this stage (every 50 new rows, later 500); the builder may tune.
- The guide lives at `docs/sharing-shaders.md`, and the main `README.md` links to it with a short section.
- **Order and a go/no-go gate.** The first task is research: can the build recover each shader's bytes from the
  player's game files and match the runtime's key? If it cannot (for example the engine generates or patches shader code
  at run time), work stops there and comes back to the owner before goals 3 to 7 are built; shipping collection and
  submission without build-time translation would ask players to share something nothing uses.
- **Shaders first, pipelines later.** Version 1 lists and translates shader variants (`V` rows, plus `L` if locating
  needs them). Render pipeline rows (`R`/`B`) and their runtime recording come in a second phase, after V-only is
  working end to end; the list format and the workflow's validation reserve them from the start so phase 2 needs no
  format change.
- Turning on collection on the Mac (the launcher sets `TOTK_SHADER_CONTRIB` to the profile-folder file) is part of
  version 1.

## The public guide (`docs/sharing-shaders.md`)

Audience: players, not developers. Written to stand alone on the owner's website (no relative links that break outside
GitHub; absolute links to the repo where needed). Plain language, short sections. Must cover:

1. **What this is, in one paragraph:** why the iPhone can be missing parts of the picture, and how sharing fixes it for
   everyone.
2. **What you share and what you don't:** identifiers only; no game files, saves, or personal data. Why that matters
   (each player's build makes the shaders from their own copy of the game).
3. **What happens on your first build:** the build prepares the shaders the community has found; what to expect on Mac
   and on iPhone; why a few things may still be missing at first.
4. **How to share, step by step:** for Mac and for iPhone, from the prompt to the submitted issue, including where the
   file is if they want to do it by hand.
5. **What happens after you share:** the automatic check, the merge, the thank-you, and that their next build (and
   everyone else's) includes it.
6. **Fixing missing parts right now:** `fill_shaders.py` for iPhone, with the bundle-id note.
7. **FAQ:** Is this legal/safe? Do I need a GitHub account? Why was my submission closed? Does it work for both game
   versions? What if I don't want to share?

## Left to the builder

- How the workflow lands the change (a PR with auto-merge enabled vs. a validated direct commit), the token setup, and
  repo settings, as long as the owner does nothing per submission and the audit trail exists.
- Whether issues are processed one by one or batched.
- Format-aware discovery vs. locator rows for XCX (decided by the first research task).
- The exact prompt wording and UI in the launcher and builder window.
- Caching of build-time translation.
- Tests, following the upstream tests and this repo's existing checks.
