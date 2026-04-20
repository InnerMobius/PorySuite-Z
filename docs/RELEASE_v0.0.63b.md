# PorySuite-Z v0.0.63b

> pokefirered projects only.

A follow-up to v0.0.62b focused on eliminating the phantom "your working tree is dirty" state users saw after every upstream pull, plus a cluster of related cleanup bugs uncovered during testing. Two files — `.gitignore` and `src/data/items.json` — were being touched by PorySuite's own auto-maintenance paths even when the user hadn't edited anything, which made `git checkout` refuse to switch branches and made every `git status` look like the user had uncommitted work. Both paths now leave the working tree alone. The Git panel also gained in-app Discard and Delete buttons so working-tree cleanup no longer requires dropping to a terminal, picked up a fix for a porcelain-path parse bug that mangled the first file's name in every commit/discard flow, and is now wide enough that the section descriptions no longer clip. The in-app Make pipeline learned to purge stale compiled song objects whose voicegroups were removed upstream (a build-breaking regression where upstream pulls left behind compiled `.o` files referencing voicegroups that no longer existed), and a cross-cut dirty-wiring bug that caused any build-log message to mark the currently-selected Pokémon species as edited has been eliminated.

## What's New in 0.0.63b

### Git Hygiene — `.gitignore` Is No Longer Auto-Touched

PorySuite's Porymap bridge was appending three entries — `porysuite_bridge.json`, `porysuite_command.json`, `porymap.user.cfg` — to the project's tracked `.gitignore` on every project open. The intent was correct (those IPC scratch files should never be committed) but the mechanism was wrong: upstream's `.gitignore` doesn't have those lines, so every `git pull` from upstream reverted them, PorySuite re-appended them on the next project open, and now `git status` reported `.gitignore` as modified forever. "Switch to Branch" refused to run; "Stash Changes" kept filling up with phantom stashes of identical content; users were forced to look at a dirty tree they never wrote to.

`ensure_bridge_gitignored` now writes to `.git/info/exclude` — git's dedicated file for *local-only* ignore rules. It is never tracked, never committed, never appears in any diff, and is never affected by `git pull` or `git checkout`. If the tracked `.gitignore` still contains the auto-added block from a previous build, the exact sentinel-wrapped block is scrubbed out once on project open so the phantom diff disappears. User-added lines adjacent to the block are left alone — only the three entries under the exact `# PorySuite bridge files (auto-added, do not commit)` sentinel are removed, and only if they match the three known entries.

### Items.json — Format Parity With Upstream

`src/data/items.json` stayed `modified:` in `git status` after every upstream pull, even when the user hadn't touched a single item. Three format mismatches between PorySuite's writers and upstream's on-disk format were producing phantom diffs on every save and every re-extraction:

- **Save path used `ensure_ascii=False`.** Upstream's `items.json` ships ASCII-escaped unicode (`"POK\u00e9 BALL"`); PorySuite's save path wrote the literal UTF-8 character (`"POKé BALL"`). Every Items-tab save flipped `\u00e9` → `é` across the entire file. Fixed by switching to `ensure_ascii=True` in `core/pokemon_data.py`.
- **Extractor path used `indent=4`.** Upstream's `items.json` (and every other `src/data/*.json`) is indented with two spaces. PorySuite's `_write_json` helper wrote four, so every re-extraction produced a whole-file reformat diff. Fixed by switching to `indent=2, ensure_ascii=True` in `core/pokemon_data_extractor.py::_write_json`.
- **`_load_json` had an mtime-based staleness check.** If any source header (`src/data/items.h`, `src/data/graphics/items.h`, etc.) had a newer mtime than the JSON, the JSON was discarded and the header was re-parsed. A `git pull` from upstream bumps every touched file's mtime to "now" — so every pull forced a re-extraction, and the re-extraction emitted content that differed from upstream's (four-space indent, deduplicated `ITEM_NONE` placeholder rows). Fixed by removing the staleness check entirely: if the JSON exists and parses, it is trusted. Re-extraction now only runs when the JSON is missing or corrupt. Users who want to force a re-extraction can delete the JSON manually (via the Git panel's new Discard / Delete buttons, or an OS file delete).

With the format mismatches fixed, the extractor no longer produces a diff against upstream on a fresh pull, and the save pipeline's existing byte-match guard (`core/pokemon_data.py`) correctly detects "no real change" and skips the write entirely.

A fourth issue was identified after testing with a completely clean post-pull working tree: `items.json` still became `modified:` after pressing F5 (Refresh). Root cause traced through `PokemonDataManager.rebuild_caches` (`core/pokemon_data_base.py`) and `ItemsData._ensure_map` (`core/pokemon_data.py`):

- `rebuild_caches` assigns `data_obj.data = result` and `data_obj.original_data = copy(result)`. For items, `result` is the upstream-shaped dict `{"items": [...]}`.
- `data_obj.save()` then fires on every data object. `ItemsData.save()` calls `_ensure_map()`, which transforms `self.data` from the wrapped `{"items": [...]}` form into the internal dict keyed by `ITEM_*` constants — but left `self.original_data` in the wrapped form.
- `save()`'s diff check compared the normalized dict against the wrapped dict. They were never equal, so `items.json` was rewritten on every F5, even when the on-disk JSON was already byte-identical to what the extractor had just loaded.

`_ensure_map` now normalizes both `self.data` and `self.original_data` through the same transformer, so the save-path diff check always compares like-for-like. The transformation logic is factored into a static `_normalize_items` helper that accepts the wrapped form, a bare list, or an already-normalized dict and returns the internal keyed-by-const representation.

### Dead-Code Cleanup

The first two rounds of this fix added scaffolding that the third round made redundant. That scaffolding has been removed in full:

- `_load_json`'s `source_headers=` parameter is gone. The helper no longer needs the list of source-header paths because the mtime-based staleness check it fed is gone.
- Every caller inside `core/pokemon_data_extractor.py` (abilities, items, trainers, constants, starters, moves, pokedex, evolutions) has been updated to call `_load_json(path)` with no second argument. Dead local lists that built `source_headers` for those callers have been deleted.
- `_write_json`'s belt-and-suspenders byte-match guard and `os.utime()` mtime bump have been removed. With the staleness check gone, the re-extraction path is reached only when the JSON is missing or corrupt, so a guard against no-op rewrites no longer has anything to guard against.

### Git Panel — Porcelain-Path Parsing Bug

Fixed a parsing bug that mangled the first file's path whenever `git status --porcelain` was used to populate a file list. The `_git_run` helper calls `.strip()` on the full command output, which eats the leading space of the first line's XY status column. `git status --porcelain` outputs ` M src/data/items.json` (leading space is significant — it represents the empty "staged" column). After the outer `.strip()` the line became `M src/data/items.json`, and the fixed-position parser `raw[3:]` sliced off the first character of the path, producing `rc/data/items.json` instead of `src/data/items.json`. Any attempt to act on that file through the panel's Discard button — or to stage it through the Commit dialogs in the main window and the EVENTide commit pipeline, which both used the same parse — routed to a non-existent path and failed with `pathspec 'rc/...' did not match any file(s) known to git`.

All three sites now use `str.split(None, 1)`, which is tolerant of leading whitespace and handles every porcelain format (`" M path"`, `"M  path"`, `"MM path"`, `"?? path"`) consistently.

### Build Pipeline — Stale Song Object Cleanup

`make` (and `make MODERN=1`) was failing with `undefined reference to voicegroup013` after an upstream pull. voicegroup013 is defined in a previous PorySuite-generated sound project but is not present in upstream's `sound/voice_groups.inc`. The pre-build sweep added in v0.0.62b already wipes stale `.s` files from `sound/songs/midi/` whose voicegroup isn't in `voice_groups.inc`, so the user's `.s` files had been correctly removed — but the matching compiled `.o` files in `build/firered_modern/sound/songs/midi/` remained. Make saw `.o` newer than `.mid`, decided the song was up to date, and the linker pulled in the stale `.o` with `voicegroup013` baked in.

`_prune_stale_song_s_files` now runs a second pass: for every `.o` in every `build/*/sound/songs/midi/` directory whose matching `.s` has already been removed, the compiled object is opened, scanned for a `voicegroup*` symbol, and deleted if that voicegroup isn't in `voice_groups.inc`. Any `.o` that is deleted during either pass forces Make to re-compile the song from the `.mid` + current `midi.cfg` on the next build, which produces a fresh `.s` pointing at a currently-valid voicegroup. The `.mid` file itself is never deleted — the musical data is kept so the song can rebuild; only the transient `.s` and `.o` outputs that carry stale voicegroup references are purged.

Cleanup activity is logged before every build so the user can see exactly which files were removed and which voicegroups they referenced.

### Dirty Marking — Log Output No Longer Flags Bulbasaur

Pressing Make (or Make Modern) was lighting up the Pokémon Data tab's currently-selected species as "edited" — an amber row in the species tree, an amber dot on the Pokémon sidebar icon, and the title-bar asterisk. Nothing had been edited; the build had just printed cleanup and status lines to the in-app log.

Root cause: the main window's startup walks every widget on the form and auto-wires a generic "mark the current species dirty" handler to every text-changing signal. That loop was picking up `logOutput` — the display-only build log at the bottom of the window — alongside the real editable fields. Every `self.log("...")` call appended text to `logOutput`, `textChanged` fired, and the handler treated it as if the user had typed in a species field.

Fixed by excluding `logOutput` from the auto-wiring loop. An audit of every other tab widget (Trainers, Trainer Graphics, Trainer Classes, Items, Abilities, Moves, Pokédex, Sound Editor, Text Editor, Instruments, Voicegroups, Credits, Overworld Graphics, Species Graphics, Label Manager, Game Text Edit, Dex Description Edit) confirmed that no other file uses the same structural `dir(self.ui)` loop — every tab wires its widgets explicitly one-by-one — so this was a single-site bug, not a pattern.

### Git Panel — In-App Discard & Delete Buttons

The Commit section now has two new buttons so users never need to drop to a terminal to clean up their working tree.

**🗑 Discard Checked Changes** sits directly under the "Modified files" list. Tick any number of tracked files, click the button, confirm, and each one is reverted to its last committed state via `git checkout --`. Batched 40 paths at a time to stay under the Windows command-line length cap. Failed files (permissions, locked by another process) are reported back in a dialog with the full list. This is the button path for wiping a phantom `items.json` or `.gitignore` modification that PorySuite's own auto-maintenance paths may have left behind in older builds.

**🗑 Delete Checked Untracked** sits directly under the "New untracked files" list. Tick any stray build artifacts, stale test exports, or accidental drops, click the button, confirm, and each one is removed from disk with `os.remove` (or `shutil.rmtree` for directories). Again, failures are reported back with the full list.

Both buttons show a confirmation dialog with the full list of affected paths (up to 20 shown inline; the rest summarized as `… and N more`). There is no undo — the confirmation is the last checkpoint.

### Git Panel — Width

Minimum width bumped from 620 to 880 pixels; default size from 660×800 to 960×820. Section descriptions (Push / Commit / Branches / Stash) were clipping their left margin behind the scrollbar, hiding the first few characters of every long line. The new width keeps the full paragraph visible without a horizontal scroll. Users who want the panel narrower can still resize below 880 — it just won't fit every explanation cleanly at that point.

<!--
  ▼ MORE ENTRIES WILL BE ADDED BELOW AS 0.0.63b DEVELOPMENT CONTINUES ▼
  Keep new sections in the same H3 style.  When the release is cut:
    - Update the opening paragraph above to mention new headline changes.
    - Consolidate the full list of file changes into "Files of Note".
    - Move any open items into "Known Limitations".
  Do NOT remove the AI Disclosure / Beta software footers below.
-->

## Files of Note

- Updated: `porymap_bridge/porymap_launcher.py` — `ensure_bridge_gitignored` rewritten to target `.git/info/exclude`; legacy-block scrubbing on the tracked `.gitignore` for users migrating from prior builds.
- Updated: `core/pokemon_data.py` — items save path now serializes with `ensure_ascii=True` to match upstream's ASCII-escaped unicode (`"POK\u00e9 BALL"`). `ItemsData._ensure_map` now normalizes both `self.data` AND `self.original_data` through a shared static `_normalize_items` helper, so the save-path diff check always compares like-for-like regardless of who populated `original_data` (bootstrap `__init__` vs. F5 `rebuild_caches`).
- Updated: `core/pokemon_data_extractor.py` — `_load_json`'s mtime-based staleness check and its `source_headers=` parameter removed; if the JSON exists and parses, it is trusted. `_write_json` now serializes with `indent=2, ensure_ascii=True` to match upstream format. All eight extractor callers (abilities, items, trainers, constants, starters, moves, pokedex, evolutions) updated to drop the now-dead `source_headers` argument, along with the local list expressions that built it.
- Updated: `ui/dialogs/git_panel.py` — scroll-to-top on showEvent so the panel always opens at the Status section; minimum width 620 → 880, default size 660×800 → 960×820; added `🗑 Discard Checked Changes` and `🗑 Delete Checked Untracked` buttons to the Commit section with confirmation dialogs; porcelain parse bug fixed (`str.split(None, 1)` replaces `raw[3:]` — leading-space eater that mangled the first file's path).
- Updated: `ui/mainwindow.py` — porcelain parse bug fixed in the main-window commit dialog (same `str.split(None, 1)` fix as the Git panel). `_prune_stale_song_s_files` extended to also delete stale `.o` files in every `build/*/sound/songs/midi/` directory whose matching `.s` was previously pruned and whose voicegroup is no longer in `sound/voice_groups.inc`. Startup dirty-wiring loop now excludes `logOutput` (the build log panel is display-only; its text changes were triggering species dirty marking on every log message).
- Updated: `eventide/mainwindow.py` — porcelain parse bug fixed in EVENTide's commit pipeline.
- Updated: `core/app_info.py` — VERSION bump to `0.0.63b` *(pending — bumped on release)*.

## Known Limitations

- **Pre-existing dirty state is not auto-cleared.** Projects that already have `items.json` or `.gitignore` modifications from a prior build still show them as uncommitted. Use Switch to Branch → pick the current branch → **🗑 Discard & Switch** to wipe those two specific files in-app, or commit the changes if you intend to keep them. Future project opens will stay clean.
- **`.git/info/exclude` is per-clone.** If you clone the project onto a second machine, PorySuite will re-seed the bridge entries on first open there. That's intentional — each clone maintains its own local exclude list.

---

> **AI Disclosure:** As with previous releases, the vast majority of this code was written with Claude. The human developer (InnerMobius) directs architecture, tests every change, and confirms behaviour. If AI-assisted code is a dealbreaker, this isn't your project.

> **Beta software.** Always keep backups of your decomp project (use git!) and test thoroughly after every editing session.
