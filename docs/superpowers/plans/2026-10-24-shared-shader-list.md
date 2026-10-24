# Shared Shader List Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every build translates, from the player's own game files, the shaders the community has met, and players share
new ones through a GitHub issue that is checked and merged automatically.

**Architecture:** A research task first proves the build can find each shader's bytes in Xenoblade X's RomFS (go/no-go).
Then: a list module and file (`shader_list.py`, `assets/shader_list.tsv`), a build step that finds and translates listed
(and all default) shader variants into a prepared cache that the IPA and the Mac app use, a downloader that fetches the
newest list, collection and a share prompt in the game, and an issue form plus a GitHub Actions workflow that validates,
rate-limits, merges and undoes submissions. Ported from the TotK implementation on `upstream/main`.

**Tech Stack:** Python 3 (stdlib only for scripts the workflow runs; `unittest`), Objective-C (launcher, runtime
headers), SwiftUI (builder window), GitHub Actions + `gh`, the existing translator `build/shader-service/totk_xlate`.

**Spec:** `docs/superpowers/specs/2026-10-24-shared-shader-list-design.md`

## Global Constraints

- No game data in the repo or in merged submissions: list rows are identifiers only (shader keys, canonical deps, flags,
  locators). Translated output lives in the work folder or the player's Library, never in the repo.
- Issue content is untrusted: never executed, never interpolated into a shell command; read from the event JSON file in
  Python. The workflow may only add or remove rows in `games/xcx/assets/shader_list.tsv` and its log.
- Abuse limits: one accepted submission per GitHub account per UTC day; account at least 7 days old; at most 2,000 new
  rows per submission; a one-command undo per issue number.
- Builds download the list from `https://raw.githubusercontent.com/chinofyoung/XC-Native/main/games/xcx/assets/shader_list.tsv`,
  validate it, cache it, and fall back to the cached copy, then the in-repo copy.
- A build with an empty or missing list, no network, or no matching rows behaves exactly as today. An unchanged list on
  an unchanged game adds under 2 s to a rebuild.
- Two game versions: 1.0.1 (`v101`) and 2.0.0 (`v200`). Rows of either may be in the list; a build uses only keys found
  in the selected version's RomFS.
- Mac contribution file: `~/Library/Application Support/MacNativeGames/XCX/shader-list-contribution.txt`. iOS: the app's
  Documents (already set in `ios/Sources/main.m`).
- Version 1 is shader rows (`V`, plus `L` if Task 1 needs locators). `R`/`B`/`S`/`C` rows are accepted by the parser and
  validator but not produced or used yet (phase 2, a separate plan).
- Public guide: `docs/sharing-shaders.md`, standalone (absolute links only), no jargon a player must understand.
- Do NOT run state-changing git commands (no commit, branch, stash, checkout, push). The owner commits. No worktrees.
- Write like the surrounding code: the existing scripts' docstring style, naming and comment density; docs in the
  README's plain style, ~120-char lines, no em dashes.
- Tests: `python3.14 -m unittest discover -s games/xcx/tests -v` (create `games/xcx/tests/`). Stdlib only. Local `python3` is 3.10 and lacks `compression.zstd`; always use `python3.14` (the build's interpreter). GitHub Actions uses setup-python 3.14.

## Review Focus

- A downloaded list that is HTML (captive portal), truncated, empty or has bad rows: the build must use the valid rows or
  fall back, never fail and never translate garbage. (Task 5 tests.)
- An issue body pasted with Markdown code fences, CRLF line endings, a BOM, or an attachment link instead of text: rows
  must still be found; anything else ignored. (Task 8 tests.)
- The same issue edited or reopened, or the same rows sent twice: rows are not duplicated and the daily limit is not
  charged twice. (Task 8 tests.)
- Rows for the other game version, or keys whose bytes are not found: skipped silently with a count in the log, never an
  error. (Task 3 tests.)
- Two submissions processed at the same moment: no lost rows and no push conflict (workflow `concurrency`, Task 8).

---

### Task 1: Research gate: find Xenoblade X's shaders in the RomFS

Go/no-go. Nothing after this task starts until the orchestrator reviews the findings and amends Tasks 3 and 4.

**Files:**
- Create: `games/xcx/docs/shaders-in-romfs.md` (findings)
- Create: `games/xcx/scripts/xcx_shaders.py` (prototype inventory, kept if GO)
- Read: `games/xcx/scripts/extract_romfs.py`, `romfs_control.py`, `merge_romfs.py` (how `romfs.bin` + `runtime-index.bin`
  are read); `third_party/d2r_gfx/gfx/nvn/totk_shader_rt.m` (how the runtime computes the shader key and what the
  control section is); upstream `games/totk/scripts/prepare_shader_seed.py` (`bnsh_shaders`, `sharcb_shaders`,
  `inventory`, `located`, `bound_control`); upstream `docs/shader-list.md` (locator rows)

**Interfaces:**
- Produces: `inventory(romfs_dir: Path) -> dict[str, Shader]` in `games/xcx/scripts/xcx_shaders.py`, where
  `Shader = namedtuple('Shader', 'key stage control code source')`, `key` the runtime's 64-hex shader key, `stage` one
  of `'vs' 'ps' 'cs'` (or `None` if not knowable from the files), `control`/`code` bytes, `source` a RomFS path.
  `romfs_dir` is `XCX_build/extracted/<v101|v200>/`.

- [ ] **Step 1: Collect ground truth.** Gather known shader keys with their guest code: every `*.json` request under
  `XCX_build/build/ios-fill/` and the phone's `shader-requests/` if present (fields `variant`, `stage`, `code_hex`), plus
  keys of the Mac cache `~/Library/Application Support/MacNativeGames/XCX/shader-cache/rt-<chain>/` (`<key>-<deps>_b.*`).
  Record counts in the findings.
- [ ] **Step 2: Find where the bytes live.** For a sample of requests, search the RomFS for their code bytes; identify
  the containing files, their container format (BNSH? Monolith archive? compressed?), and whether the bytes on disk hash
  to the runtime key exactly as `totk_shader_rt.m` computes it.
- [ ] **Step 3: Write `inventory()`** for the format found (format-aware discovery preferred; if shaders sit in
  unparseable places, a locator approach as upstream's `L` rows).
- [ ] **Step 4: Measure.** Run: `python3.14 games/xcx/scripts/xcx_shaders.py XCX_build/extracted/v200 --check XCX_build/build/ios-fill`
  printing `found N shaders in M files; ground truth K keys, recovered R (P%)`. Repeat for `v101` if extracted.
- [ ] **Step 5: Verdict in the findings doc.** GO if ≥ 95% of ground-truth keys are recovered with matching hashes,
  else NO-GO with what blocks it. Include: formats, file count, shader count, time taken, and whether stage is knowable.
  On NO-GO, stop and report; do not start Task 3.

### Task 2: List module and the list file

Independent of Task 1; may run in parallel with it.

**Files:**
- Create: `games/xcx/scripts/shader_list.py` (port of upstream `games/totk/scripts/shader_list.py`, Xenoblade X only)
- Create: `games/xcx/assets/shader_list.tsv` (header only)
- Create: `games/xcx/tests/test_shader_list.py`

**Interfaces:**
- Produces, in `games/xcx/scripts/shader_list.py`:
  - `KEY`, `VARIANT` regexes as upstream; `MAX_DEPS_BYTES = 4096`.
  - `canonical(deps: dict) -> str` (compact JSON, keys sorted by code point, only `tex img attr fout`).
  - `parse_row(line: str) -> tuple | None`: a valid row as a tuple of its fields, `None` for anything else (comments,
    blank, malformed, unknown kind, deps not canonical or over `MAX_DEPS_BYTES`). Accepts `V L S C R B` kinds.
  - `class ShaderList`: `load(path) -> ShaderList`, `rows() -> list[tuple]`, `add(rows) -> list[tuple]` (returns the rows
    that were new), `remove(rows) -> int`, `save(path)` (header kept, `V` rows sorted, others in insertion order),
    `shaders() -> dict[str, list[dict]]` (key -> list of deps), `digest() -> str` (SHA-256 of the saved text).
  - `HEADER = '# Xenoblade Chronicles X shader list v1 (games/xcx/scripts/shader_list.py; docs/sharing-shaders.md). Identifiers only, never game data.\n'`

- [ ] **Step 1: Write failing tests** in `test_shader_list.py`: `test_parse_valid_v_row` (a `V\t<64 hex>\t{"attr":{},"fout":{},"img":{},"tex":{}}` line parses);
  `test_rejects_non_canonical_deps` (same deps with spaces or unsorted keys -> `None`); `test_rejects_bad_key` (63 hex,
  uppercase, non-hex -> `None`); `test_rejects_unknown_kind_and_junk` (`X\t...`, `<html>`, `rm -rf /` -> `None`);
  `test_crlf_and_bom_tolerated` (`'﻿V\t...\r\n'` parses); `test_add_returns_only_new` (adding the same row twice
  returns it once); `test_save_sorts_v_rows_and_keeps_header`; `test_remove_counts`; `test_digest_stable`.
- [ ] **Step 2: Run** `python3.14 -m unittest games/xcx/tests/test_shader_list.py -v`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement** by porting upstream, dropping BotW/TotK headers and aliases; write the header-only list file.
- [ ] **Step 4: Run** the same command. Expected: all PASS.

### Task 3: Build-time preparation (find and translate)

Starts after Task 1 is GO. The orchestrator fills in the locating details from Task 1's findings before dispatch.

**Amended after Task 1 (GO, 99.87%; details in `games/xcx/docs/shaders-in-romfs.md`):**
- `inventory(romfs_dir, workers=None)` uses a spawn `ProcessPoolExecutor`: call it only under `if __name__ == '__main__':`
  (tests monkeypatch it). It accepts `extracted/<v>/` or its `romfs/` subfolder. ~18 s warm.
- Skip shaders with `stage is None` (2 geometry shaders; the runtime has no chain for them). 12,427 translatable.
- Reuse upstream `translation_request(variant, stage, control, code, answers, code_path)` and `suffix(answers)`
  unchanged; `depth_minus_w=True` is correct for XCX. No `bound_control`, no startup packages, no zstd dictionaries:
  every shader is default-bind-state (`all_default=True`).
- RomFS identity: `romfs_sha256` from `extracted/<v>/romfs/index.json` (do not hash `sts.ard`, 10 GB).
- Seed floor `MIN_SHADERS = 12000` (refuse a seed with fewer translated defaults, as upstream's 8,000 for TotK).
- Expose `builtin_serves(seed, key, deps) -> bool` (upstream) for Task 9.

**Files:**
- Create: `games/xcx/scripts/prepare_shader_seed.py` (port of upstream's `prepare`, `plan`, `TranslationCache`,
  `translation_request`, `reusable_seed`, `builtin_serves`, using Task 1's `inventory` instead of TotK's containers)
- Create: `games/xcx/tests/test_prepare_shader_seed.py`

**Interfaces:**
- Consumes: `inventory(romfs_dir)` (Task 1), `ShaderList` (Task 2).
- Produces: `prepare(romfs_dir: Path, output: Path, translator: Path, spirv_cross: Path, version: str, shader_list: Path | None) -> dict`
  returning `{'shaders_found', 'default_translated', 'listed_rows', 'listed_matched', 'listed_translated', 'skipped_other_version', 'reused'}`.
  `output` is `<work>/build/shader-prep/rt-<chain>` in the runtime cache layout (`<variant>_b.metal`, `_b.json`, per-shader `.variants`)
  that `ios_seed_caches` and the runtime already read. Chain from `dmsh_chain.h` as `fill_shaders.py chain()` does.
  Reuse: a stamp `output/../prep-stamp.json` of (RomFS identity, list digest, translator digest); same stamp -> return at once.
- Decision: as upstream's `all_default=True`, every shader found is translated for the default bind state, then each
  listed deps for keys found.

- [ ] **Step 1: Write failing tests** with a fake translator (a script that writes a stub `_b.metal`/`_b.json` and records its calls) and a fake
  `inventory` (monkeypatched): `test_translates_default_and_listed`; `test_skips_keys_not_in_romfs` (listed key absent ->
  counted in `skipped_other_version`, no translator call, no error); `test_unchanged_inputs_reuse` (second call makes no
  translator calls and returns `reused=True`); `test_empty_or_missing_list_still_translates_defaults`;
  `test_translator_failure_on_one_shader_continues` (one failing shader is logged and counted, the rest written).
- [ ] **Step 2: Run** `python3.14 -m unittest games/xcx/tests/test_prepare_shader_seed.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `prepare()` and a CLI `prepare_shader_seed.py --romfs <dir> --output <dir> --translator <path> --spirv-cross <path> --version <v> [--list <tsv>]`
  that prints one summary line `shader prep: found N, default T, listed L (matched M, translated X, other version S)`.
  Translate in parallel (`concurrent.futures`, `os.cpu_count()` workers).
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Real run** against `XCX_build` (v200) with the empty list; record time and counts in Task 1's findings doc.
  Expected: thousands of default variants written, no crash; rerun reports `reused`.

### Task 4: Wire preparation into the build (Mac and iOS)

**Files:**
- Modify: `build_app.py` (`STEPS`, a new `step_shader_prep(work, logs) -> Path`, `ios_seed_caches`, `step_app_macos`
  end comment, `IOS_EXPERIMENTAL` text)
- Modify: `games/xcx/builder/BuildModel.swift` only if it hard-codes step names
- Create: `games/xcx/tests/test_build_shader_prep.py`

**Interfaces:**
- Consumes: `prepare()` (Task 3), `fetch_list()` (Task 5; until Task 5 lands, use the in-repo list).
- Produces: step name `'shaders: prepare'` in `STEPS` after `'shader tools'`; `step_shader_prep` returns the prepared
  folder. `ios_seed_caches` puts it first. For the Mac, after the app is installed, copy prepared variants that are
  missing into `~/Library/Application Support/MacNativeGames/XCX/shader-cache/rt-<chain>/` (never overwrite existing
  files), logging `shaders: N new prepared shaders for the Mac cache`.

- [ ] **Step 1: Write failing tests**: `test_step_order_has_shader_prep_after_shader_tools`; `test_ios_seed_caches_puts_prep_first`
  (tmp dirs); `test_mac_copy_never_overwrites` (existing file with different content survives, missing ones copied).
- [ ] **Step 2: Run** `python3.14 -m unittest games/xcx/tests/test_build_shader_prep.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement**; progress via `timed()`/`emit()` like other steps so `--gui` shows it. Update
  `IOS_EXPERIMENTAL` to say shaders the community has met are prepared and others are skipped until shared.
- [ ] **Step 4: Run** tests, then `python3.14 build_app.py` (Mac) twice against the existing work folder. Expected: first run
  shows the new step with counts; second run's step takes under 2 s.
- [ ] **Step 5: Run** `python3.14 build_app.py --platform ios` once. Expected: `shader seed:` line counts include the
  prepared variants.

### Task 5: Download the newest list

Independent of Tasks 1 and 3; may run in parallel.

**Files:**
- Create: `games/xcx/scripts/fetch_shader_list.py`
- Create: `games/xcx/tests/test_fetch_shader_list.py`
- Modify: `build_app.py` (`step_shader_prep` calls it; `--offline` flag skips the fetch)

**Interfaces:**
- Consumes: `parse_row`, `ShaderList` (Task 2).
- Produces: `fetch_list(cache_dir: Path, repo_copy: Path, url: str = LIST_URL, timeout: float = 10, opener=urllib.request.urlopen) -> tuple[Path, str]`
  returning the list to use and its origin, one of `'downloaded' 'cached' 'repo'`. `LIST_URL` is the URL in Global
  Constraints. Accept a download only if it starts with `HEADER`'s first token `# Xenoblade Chronicles X shader list`,
  is under 64 MB, and has no row that `parse_row` rejects other than comments/blank lines (rejected rows are dropped and
  counted); write atomically to `cache_dir/shader_list.tsv`.

- [ ] **Step 1: Write failing tests** with a fake opener: `test_download_ok_is_cached`; `test_html_falls_back_to_cache`;
  `test_truncated_or_empty_falls_back`; `test_network_error_uses_repo_when_no_cache`; `test_bad_rows_dropped_good_kept`;
  `test_timeout_is_passed`.
- [ ] **Step 2: Run** `python3.14 -m unittest games/xcx/tests/test_fetch_shader_list.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement**; `build_app.py` logs `shader list: downloaded (N rows)` / `cached` / `in the project (offline)`.
- [ ] **Step 4: Run** tests. Expected: PASS.

### Task 6: Collection on the Mac and the share prompt

**Files:**
- Modify: `games/xcx/app/launcher.m` (set `TOTK_SHADER_CONTRIB` to the Mac contribution file before the game starts)
- Modify: `third_party/d2r_gfx/gfx/nvn/totk_shader_contrib.h` (threshold and prompt text for this game; keep the file's API)
- Modify: the place the runtime shows the reminder (find `totk_contrib_due` callers; upstream commit `bd59dc9`)
- Modify: `games/xcx/builder/ContentView.swift` + `BuildModel.swift` (a "Share N new shaders" button when the file has
  rows not in the list)
- Create: `games/xcx/scripts/contribution.py` + `games/xcx/tests/test_contribution.py`

**Interfaces:**
- Consumes: `ShaderList`, `parse_row` (Task 2).
- Produces: `new_rows(contribution: Path, shader_list: Path) -> list[tuple]`; `issue_url(rows_count: int, version: str, device: str) -> str`
  returning `https://github.com/chinofyoung/XC-Native/issues/new?template=shader-list.yml&title=Shader%20list%3A%20<N>%20shaders&version=<v>&device=<d>`.
  Prompt rule: first at 50 new rows, then every 500; "Not now" waits for the next threshold, "Never" stops (existing
  `totk_contrib_reminded` / `totk_contrib_never`). Prompt copy: "You found N new shaders. Sharing them lets everyone's
  next build draw these parts of the game. Share now?" Buttons: Share, Not now, Never. Share opens the issue URL and
  shows the file in Finder.

- [ ] **Step 1: Write failing tests** (`test_contribution.py`): `test_new_rows_excludes_listed`; `test_new_rows_ignores_junk_lines`;
  `test_issue_url_encodes_fields`.
- [ ] **Step 2: Run** `python3.14 -m unittest games/xcx/tests/test_contribution.py -v`. Expected: FAIL. Then implement; expected PASS.
- [ ] **Step 3: Implement** the launcher env var and prompt; `make -C games/xcx app WORK=<work>` builds with `-Werror`.
- [ ] **Step 4: Manual check:** run the Mac app, play a minute in an area, confirm the contribution file appears with
  `V` rows; set the threshold to 1 via the existing flag/env used for tests and confirm the prompt and both buttons work.

### Task 7: The iPhone's rows reach the Mac

**Files:**
- Modify: `games/xcx/ios/tools/shader_contrib_watch.py` (replace the stub with `pull --device <id> [--bundle-id <id>]`)
- Modify: `build_app.py` (on `--platform ios` with a device id known, pull before preparing, non-fatal)
- Create: `games/xcx/tests/test_contrib_watch.py`

**Interfaces:**
- Consumes: `devicectl()` pattern from `fill_shaders.py`; `ShaderList`.
- Produces: `pull(device: str, bundle_id: str, store: Path) -> int` copying the device's `Documents/shader-list-contribution.txt`
  and merging its valid rows into the Mac contribution file; returns rows added. CLI prints
  `N new shaders from the iPhone; share them from the builder window or the game's prompt`.

- [ ] **Step 1: Write failing tests** with a fake `devicectl` runner: `test_pull_merges_valid_rows_only`; `test_pull_device_missing_is_reported_not_raised`.
- [ ] **Step 2: Run** `python3.14 -m unittest games/xcx/tests/test_contrib_watch.py -v`. Expected: FAIL; implement; PASS.
- [ ] **Step 3: Real run** against the owner's iPhone (`75337A3D-2FBD-5EC8-8F9D-D5FE12129897`, bundle `org.macnativegames.xcx.gvrm33qnkk`).
  Expected: a row count and a Mac contribution file with the phone's rows.

### Task 8: Issue form, processing script and the auto-merge workflow

**Files:**
- Create: `.github/ISSUE_TEMPLATE/shader-list.yml` (English only; fields: version dropdown `2.0.0`/`1.0.1`, device
  dropdown `Mac`/`iPhone`/`iPad`, file textarea, notes)
- Create: `games/xcx/scripts/process_shader_issue.py`
- Create: `games/xcx/assets/shader_list_log.tsv` (header: `# issue\tuser\tdate_utc\trows_added`)
- Create: `.github/workflows/shader-list.yml`, `.github/workflows/tests.yml`
- Create: `games/xcx/tests/test_process_shader_issue.py`

**Interfaces:**
- Consumes: `parse_row`, `ShaderList` (Task 2).
- Produces:
  - `extract_rows(body: str, fetch=None) -> list[tuple]`: rows from pasted text (code fences, CRLF, BOM tolerated) and
    from attachment links whose host is exactly `github.com` and path starts `/user-attachments/files/` (fetched with a
    5 MB cap and 15 s timeout through `fetch`); every other link ignored.
  - `decide(issue: dict, author_created: datetime, now: datetime, log: Path, shader_list: ShaderList) -> Decision`,
    `Decision = namedtuple('Decision', 'accept rows reason')`. Reasons (exact keys, used for comments): `'new_account'`
    (< 7 days), `'daily_limit'` (another accepted issue by this user on the same UTC day in the log; the same issue
    number does not count against itself), `'too_many'` (> 2,000 new rows), `'nothing_new'`, `'no_rows'`, `'ok'`.
  - `apply(decision, issue, list_path, log_path) -> int` (adds rows, appends one log line; idempotent per issue: an
    issue already in the log adds only rows not yet present and does not add a second log line).
  - `undo(issue_number: int, list_path, log_path, rows_source) -> int`: removes the rows that issue added (recorded in
    `games/xcx/assets/shader_list_issues/<issue>.tsv`, written by `apply`) unless another issue also added them later.
  - CLI: `process_shader_issue.py --event <GITHUB_EVENT_PATH> --author-created <ISO> --out-comment <file>` and
    `process_shader_issue.py --undo <issue>`.
  - Comment text per reason, friendly and plain (e.g. `daily_limit`: "Thanks! You already shared today. Please send
    this again tomorrow and it will be added.").
- Workflow `shader-list.yml`: `on: issues: [opened, edited, reopened]`; runs only when the title starts `Shader list:`
  or the label `shader-list` is set; `permissions: contents: write, issues: write`; `concurrency: shader-list` (no
  cancel); checkout `main` with default token; `gh api users/${{ github.event.issue.user.login }} --jq .created_at`
  into a file, passed by path (the login comes from the event, but use an env var, never inline in `run:` text with the
  body); run `python3 -m unittest discover -s games/xcx/tests`; run the script; if rows were added, commit
  `shader list: N shaders from #<issue> (@<login>)` as `github-actions[bot]` and push to `main` (retry pull-rebase-push
  up to 3 times); comment with the script's comment file; close the issue as completed when accepted or as not planned
  when rejected (except `daily_limit`, which stays open and says to resend tomorrow). Undo: `workflow_dispatch` input
  `issue` runs `--undo` and commits `shader list: undo #<issue>`.
- `tests.yml`: on push and pull_request, `python3 -m unittest discover -s games/xcx/tests -v` on `macos-latest`,
  pinned action SHAs as upstream.

- [ ] **Step 1: Write failing tests**: `test_extract_from_paste_with_fences_crlf_bom`; `test_extract_ignores_foreign_links`
  (gist, other hosts, `github.com/evil/user-attachments` path); `test_extract_attachment_size_cap`;
  `test_new_account_rejected`; `test_daily_limit_same_user_other_issue`; `test_daily_limit_not_charged_for_same_issue_edit`;
  `test_too_many_rows`; `test_nothing_new`; `test_apply_idempotent_on_edit`; `test_undo_removes_only_that_issues_rows`
  (a row also added by a later issue stays); `test_shell_metacharacters_in_body_are_inert` (body with `$(...)`, backticks,
  `; rm` yields no rows and no exception).
- [ ] **Step 2: Run** `python3.14 -m unittest games/xcx/tests/test_process_shader_issue.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the script, form and workflows.
- [ ] **Step 4: Run** tests. Expected: PASS. Run `python3.14 -c "import yaml"` only if available; otherwise check the two
  workflow files with `ruby -ryaml -e 'YAML.load_file(ARGV[0])' <file>` (Ruby ships with macOS). Expected: no error.
- [ ] **Step 5: Note for the owner** (in the task report, not done by the agent): repo Settings › Actions › Workflow
  permissions must allow read and write; if `main` is protected, allow `github-actions[bot]` to push.

### Task 9: Seed the list from what the owner played

**Files:**
- Modify: `games/xcx/scripts/shader_list.py` (subcommand `import --cache <dir> ... --requests <dir> ... --contribution <file> ...`)
- Modify: `games/xcx/assets/shader_list.tsv` (the seeded rows)
- Modify: `games/xcx/tests/test_shader_list.py` (`test_import_from_cache_reads_deps_from_json`, `test_import_skips_default_variants`)

**Interfaces:**
- Consumes: `ShaderList`, `builtin_serves` (Task 3).
- Produces: rows from the Mac cache's `<variant>_b.json` deps, `ios-fill` requests, and contribution files, minus those
  the default bind state serves.

- [ ] **Step 1: Write the two failing tests**, run, expect FAIL; implement; expect PASS.
- [ ] **Step 2: Run** `python3.14 games/xcx/scripts/shader_list.py import --cache "$HOME/Library/Application Support/MacNativeGames/XCX/shader-cache" --requests XCX_build/build/ios-fill --contribution "$HOME/Library/Application Support/MacNativeGames/XCX/shader-list-contribution.txt"`.
  Expected: a row count; the list file contains only identifier rows (spot-check: no line over 4.2 KB, every line passes `parse_row`).

### Task 10: The public guide and repo docs

**Files:**
- Create: `docs/sharing-shaders.md`
- Modify: `README.md` (short "Sharing shaders" section linking the guide; iPhone section updated), `games/xcx/README.md`
  ("Next" and open issues), `games/xcx/CHANGELOG.md` (dated entry), `games/xcx/ios/tools/fill_shaders.py` docstring
  (it is now the stopgap for what the list has not reached)

- [ ] **Step 1: Write the guide** covering, in this order, the seven sections in the spec's "The public guide": what this is;
  what you share and what you don't; your first build; how to share (Mac, iPhone, by hand with file locations); what
  happens after (automatic check, merge, thank-you, everyone's next build); fixing missing parts now (`fill_shaders.py`
  with `--bundle-id` and `xcrun devicectl list devices`); FAQ (legal/safe, GitHub account, why closed: the four rules
  with plain wording, both game versions, not sharing is fine). Absolute links to `https://github.com/chinofyoung/XC-Native`.
- [ ] **Step 2: Check** every command in the guide against the real CLI (`--help` of each script) and every link host.
- [ ] **Step 3: Update** README, game README, CHANGELOG.

### Final: whole-change review and full test run

- [ ] Run `python3.14 -m unittest discover -s games/xcx/tests -v` (all PASS) and `make -C games/xcx runtime WORK=XCX_build` (`-Werror`, builds).
- [ ] Dispatch a reviewer over the whole diff against the spec, with extra attention to the workflow's handling of untrusted input.
- [ ] Report to the owner: what changed, test output, the repo settings to change, and that nothing was committed.
