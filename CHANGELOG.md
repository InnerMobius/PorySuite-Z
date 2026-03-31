## [2026-03-31] — Moves fixes, Items dropdowns, Evolution reference, Trainer save guard

### Type
Feature / Fix

### Summary
Multiple fixes for new-move creation (description persistence, animation mapping, ID numbering, build errors), new dropdown menus on the Items tab, an evolution method reference panel, and a guard against trainer data being silently wiped on save.

### Added
- **Evolution method reference panel** (`mainwindow.py`): Scrollable column on the right side of the Evolutions tab with plain-English descriptions of every evolution method — what triggers it, what the parameter means, and FireRed-specific warnings (e.g. no day/night cycle, no contest stats).
- **Items tab: Field Use dropdown** (`ui/items_tab_widget.py`, `ui/constants.py`): Converted from a text entry to a dropdown populated with all 25 `FieldUseFunc_*` / `ItemUseOutOfBattle_*` functions from `item_use.h`. Still editable for custom functions.
- **Items tab: Battle Use Func dropdown** (`ui/items_tab_widget.py`, `ui/constants.py`): Converted from a text entry to a dropdown populated with all 9 `BattleUseFunc_*` / `ItemUseInBattle_*` functions.
- **Items tab: Hold Effect dropdown expanded** (`ui/constants.py`): Updated from 28 entries to all 67 `HOLD_EFFECT_*` constants from `hold_effects.h`. Was missing Leftovers, Lucky Egg, Focus Band, Exp Share, all type-power boosts, and many others.
- **Dropdown arrow indicator** (`ui/items_tab_widget.py`): Added visible down-arrow to editable combo boxes so they're visually distinguishable from text fields.
- **Makefile: `-Wno-attribute-alias` for pokemon.o** (`pokefirered/Makefile`): Suppresses a GCC 15.2.0 warning about intentional function alias type mismatches in vanilla pokefirered code.

### Fixed
- **New move descriptions lost on refresh** (`mainwindow.py`): `load_moves_defs_table` now checks the move data dict's own `"description"` field as a fallback when `get_move_description()` returns nothing. New moves store their description there via `set_move_data`, but the loading code never looked there.
- **New move animation mapping off-by-one** (`mainwindow.py`): The animation table in `battle_anim_scripts.s` has a `Move_COUNT` sentinel entry. New move entries were appended after it, causing the 0-indexed ID mapping to point at the sentinel instead of the real animation. Writer now inserts before the sentinel; reader now skips `Move_COUNT` when counting indices.
- **New move C `#define` IDs wrong** (`mainwindow.py`): JSON stores 1-based IDs but C `#define` values are 0-based. The writer was using JSON IDs directly, producing `#define MOVE_ROLL 356` instead of `355`. Also `MOVES_COUNT` was calculated as `old + count` instead of `highest_id + 1`. Both fixed.
- **Move description double-escaped backslashes** (`plugins/pokefirered/pokemon_data.py`): The description writer was escaping `\` to `\\`, turning `\n` (intended C newline escape) into `\\n` (literal backslash + n). Removed the unnecessary `replace("\\", "\\\\")`.
- **Trainer data silently wiped on save** (`ui/trainers_tab_widget.py`): When `load()` was called again (e.g. switching to/from the Trainers tab), it created a fresh detail panel with empty dropdowns but didn't reset `_current_const`. The next `_flush_current()` would collect blank values from the empty panel and overwrite the real trainer data. Fix: `_current_const` is now reset to `None` before creating a new panel. Also added a guard: if `collect()` returns empty `trainerClass` and `trainerPic` but the existing data has real values, skip the update.
- **`EVO_MODE_*` constants in evolution dropdown** (`mainwindow.py`): `EVO_MODE_NORMAL`, `EVO_MODE_TRADE`, `EVO_MODE_ITEM_USE`, and `EVO_MODE_ITEM_CHECK` are internal engine flags for how `GetEvolutionTargetSpecies` scans the table — not valid evolution methods. Filtered out of the dropdown.

### Files Changed
- `mainwindow.py`
- `ui/items_tab_widget.py`
- `ui/constants.py`
- `ui/trainers_tab_widget.py`
- `plugins/pokefirered/pokemon_data.py`
- `pokefirered/Makefile`

---

## [2026-03-31] — Add New Move / Duplicate Move support

### Type
Feature

### Summary
The Moves tab now supports creating new moves from scratch or duplicating an existing move. PorySuite writes all five required files so the new move compiles and works in-game without manual header editing. The Effect dropdown is also now a proper non-editable dropdown, and an Animation field shows which battle animation each move uses.

### Added
- **Add Move / Duplicate Move buttons** (`ui/moves_tab_widget.py`): Two buttons below the move list. "Add Move" opens a dialog for the new constant name and display name, then creates a blank move entry. "Duplicate Move" copies all stats, flags, description, and animation from the currently selected move into the new entry. The new move gets the next available ID automatically.
- **Animation field on moves editor** (`ui/moves_tab_widget.py`): New read/write dropdown in the Classification card showing the battle animation assigned to each move. Defaults to the source move's animation when duplicating. Lists all animation labels found in `battle_anim_scripts.s` so you can reassign any move to reuse an existing animation.
- **`include/constants/moves.h` patcher** (`mainwindow.py`): Adds the new `#define MOVE_X <id>` line before `MOVES_COUNT` and bumps the count. Handles both new moves and removed moves.
- **`src/data/text/move_names.h` patcher** (`mainwindow.py`): Appends `[MOVE_X] = _("NAME")` entries for newly added moves.
- **`src/move_descriptions.c` patcher** (`mainwindow.py`): Adds both the `const u8 gMoveDescription_X[] = _("...");` definition and the `[MOVE_X - 1] = gMoveDescription_X,` pointer table entry.
- **`data/battle_anim_scripts.s` patcher** (`mainwindow.py`): Appends `.4byte Move_TEMPLATE` to the `gBattleAnims_Moves` pointer table so every new move has a valid animation entry. Reuses the source move's animation by default.

### Fixed
- **Effect field is now a proper dropdown** (`ui/moves_tab_widget.py`): Was an editable combo box with QCompleter autocomplete. Now a standard non-editable dropdown matching Type and Target.

### Files Changed
- `ui/moves_tab_widget.py`
- `mainwindow.py`

---

## [2026-03-30] — Save integrity and performance overhaul

### Type
Fix

### Summary
Opening a project, browsing without making changes, and saving no longer modifies any source files. Previously, every save rewrote all C headers from scratch regardless of whether data changed — introducing cosmetic formatting differences, Windows line endings, and unnecessary file mutations that could interfere with builds. The Pokemon tab species selection is also dramatically faster.

### Fixed
- **Move effect field is now a proper filterable dropdown** (`ui/moves_tab_widget.py`): The Effect field looked and behaved like a plain text box — you could type in any garbage and it would accept it. Now it's a real dropdown with all 214 effects pre-loaded, plus type-to-filter (type "FLINCH" and only matching effects appear). Still searchable, but only valid effect constants can be selected.
- **Consolidated all UI constant pools into single source of truth** (`ui/constants.py`): Types, AI flags, encounter music, party types, move targets, move flags, move effects, item pockets, item types, and hold effects all lived as duplicate lists scattered across `moves_tab_widget.py`, `trainers_tab_widget.py`, `items_tab_widget.py`, and `mainwindow.py` — some with conflicting descriptions (AI flags had two different versions). Now everything imports from one file (`ui/constants.py`). Changing a constant or description in one place updates it everywhere.
- **Scroll wheel no longer changes combo boxes or spin boxes without clicking first** (`ui/custom_widgets/scroll_guard.py`, `mainwindow.py`, `ui/items_tab_widget.py`, `ui/moves_tab_widget.py`, `ui/trainers_tab_widget.py`): Hovering over a dropdown or number box and scrolling would silently change its value — extremely dangerous on things like item type. Now all combo boxes and spin boxes across the entire app require a click (focus) before the scroll wheel does anything.
- **Move rename now works for display-name-only changes** (`mainwindow.py`): Renaming "POUND" to "Pound" was silently rejected because both produce the same constant (MOVE_POUND). The check only compared constants, not display names. Now compares both — if the constant is the same but the display name changed, it updates the name in memory and refreshes the list immediately. Also fixed a bug where the live preview used the wrong keyword argument (`preview_only` instead of `preview`).
- **Cleaned up AI flags in trainer editor** (`ui/trainers_tab_widget.py`, `mainwindow.py`): Audited all AI checkboxes against pokefirered source. Restored all 9 flags that have real AI script code (Check Bad Move, Check Viability, Try To Faint, Setup First Turn, Risky, Prefer Strongest Move, Prefer Baton Pass, Double Battle, HP Aware) — even if no vanilla trainer uses them, they add customizability since the engine code exists. Removed only: Smart Switching (constant doesn't exist in pokefirered), Roaming / Safari (set by engine for wild encounters, not trainer data), First Battle (set by event scripts, not trainer data), Unknown (empty placeholder script with no behaviour).
- **Items table restored and items.h regenerator added** (`mainwindow.py`, `pokefirered/src/data/items.json`): The items.json had become scrambled (308 items instead of 375, only 52 in correct positions, all TMs/HMs missing), causing a game crash ("Jumped to invalid address") when picking up Oak's Parcel. Restored items.json from the clean reference copy. Added `_write_items_header()` direct writer that regenerates `src/data/items.h` from `items.json` on every save — the old plugin pipeline's `_patch_items_header` couldn't handle the positional (non-designated-initializer) format of items.h and silently gave up.
- **Category and description now sync between Stats page and Pokedex tab** (`mainwindow.py`): The stats page and Pokedex tab had separate data stores for category/description that never synced. Editing category on the stats page saved to `species_info` but the Pokedex tab read from `pokedex` data — showing the old value. Now `save_species_data` syncs edits into the pokedex data, and `_flush_pokedex_panel` syncs Pokedex panel edits back into species_info. Also added direct writers for `pokedex_entries.h` (category) and `pokedex_text_fr.h` (description) that bypass the broken plugin pipeline.
- **Pokemon move/learnset edits now persist through save and refresh** (`mainwindow.py`): The plugin pipeline's `parse_to_c_code` for moves used `ReadSourceFile`/`WriteSourceFile` wrappers that silently failed due to the `SOURCE_PREFIX = "source/"` path bug. Added `_write_moves_headers()` in mainwindow.py that directly reads and patches all 5 learnset header files (level_up_learnsets.h, level_up_learnset_pointers.h, tmhm_learnsets.h, tutor_learnsets.h, egg_moves.h) using the same direct-open approach as the species stats and evolution writers.
- **Species rename now updates .mk and sound files** (`plugins/pokefirered/refactor_service.py`): The rename service's text search only scanned `src/`, `include/`, and `data/` directories. Files like `graphics_file_rules.mk` (at project root, references `old_bulbasaur.4bpp`) and `sound/direct_sound_data.inc` / `sound/cry_tables.inc` (reference cry filenames by slug) were never updated, causing build failures after a rename. Added `.mk` files at the project root and `.inc`/`.s` files under `sound/` to the scan spec.
- **Species stats now actually write to C headers on Save** (`plugins/pokefirered/pokemon_data.py`): `parse_to_c_code` used `ReadSourceFile`/`WriteSourceFile` wrappers that silently failed due to path resolution issues with the `SOURCE_PREFIX` system. The header read returned empty, so the method skipped writing — but reported success. Replaced all wrapper calls with direct `open()` using the known project root path, same approach used by the evolution writer which always worked. Also added `SOURCE_PREFIX = ""` to `SpeciesData` class.
- **Stats edits no longer lost when switching tabs** (`mainwindow.py`): Switching between Pokemon sub-tabs (Stats → Moves) or switching main tabs (Pokemon → Items) triggered `refresh_current_species` which reloaded data from disk, overwriting any unsaved widget changes. Now `on_pokemon_tab_changed` and `update_main_tabs` both call `save_species_data` for the current species before the reload happens, so base stats, growth rate, held items, etc. are captured into the in-memory data first.
- **Stats/all species edits now survive Refresh (F5)** (`mainwindow.py`): The stash/restore mechanism that preserves user edits during cache rebuilds was only saving 3 fields (categoryName, description, speciesName). Stats like base stats, growth rate, held items, etc. were being thrown away and replaced with vanilla values. Now stashes the entire species_info dict for every species, and on restore, compares each field against the freshly-extracted vanilla value — any field where the user's edit differs from vanilla gets put back.
- **agbcc dependency check now finds project-local installs** (`programsetup.py`): The dependency checker only looked for agbcc in the app's `data/toolchain/agbcc/bin/` directory, which is only populated by the built-in "Build agbcc" button. If agbcc was already installed inside a project's `tools/agbcc/` folder (which is where pokefirered puts it), the checker showed "✗ Missing" even though builds worked fine. Now also scans registered projects and nearby directories for existing agbcc installations. Shows "✓ Found" with a tooltip noting it was found in a project folder.
- **Save no longer rewrites unchanged files** (`plugin_abstract/pokemon_data.py`): The `should_parse_to_c_code()` method had `return True` hardcoded as its first line, making all the real change-detection logic below it dead code. Every save regenerated every C header file even when nothing changed. Removed the `return True` so the method now checks `pending_changes` — files only get rewritten when you actually edit something.
- **Backup-missing check no longer forces rewrites** (`plugin_abstract/pokemon_data.py`): `should_parse_to_c_code()` also forced a rewrite whenever on-disk backup files were missing (which was always, since backups are stored in-memory). Removed this check so only `pending_changes` drives the decision.
- **Windows line endings no longer injected into source files** (`local_env.py`): On Windows, all source file writes go through `write_file_to_volume()` which was missing `newline="\n"`. Every saved file got Windows line endings (CRLF) instead of Unix (LF), making git report them as modified even though the content was identical. Added `newline="\n"` to this function and all other file-write calls that touch project source files (`plugin_abstract/pokemon_data.py`, `plugins/pokefirered/pokemon_data.py`, `mainwindow.py`).
- **Evolution data no longer written back on every species click** (`mainwindow.py`): `save_species_data()` called `set_evolutions()` unconditionally every time you clicked a different Pokemon, even when the evolution data hadn't changed. This replaced the in-memory data with a rebuilt copy that could differ subtly, triggering a JSON save and C header rewrite. Now only calls `set_evolutions()` when the data actually differs.
- **Items tab no longer corrupts ITEM_NONE on navigate** (`ui/items_tab_widget.py`): The items detail panel's `_flush()` method now checks a `_dirty` flag before collecting widget values. Previously, just clicking the items tab auto-selected ITEM_NONE and `_flush()` would overwrite its JSON data with lossy widget values (e.g. `"????????"` → `""`, `"ITEM_TYPE_BAG_MENU"` → `0`), causing items.json to be rewritten on every save.
- **MOVE_NONE hidden from moves list** (`ui/moves_tab_widget.py`): MOVE_NONE is now skipped in the moves list, matching how ITEM_NONE is already hidden in the items list. Prevents accidental edits to the null move entry.
- **Trainers tab no longer empties trainers.h** (`mainwindow.py`): `_save_trainers_editor()` called `flush()` which returns `{}` when the trainers tab was never visited, then overwrote real trainer data with nothing. Added an early return guard when flush returns empty.
- **Trainer F_TRAINER_FEMALE flag preserved** (`ui/trainers_tab_widget.py`): The `| F_TRAINER_FEMALE` bitwise flag on `encounterMusic_gender` was being stripped when loading trainers and not restored when saving. Now parsed on load and re-appended on collect.
- **Trainer parties ghost struct fix** (`ui/trainers_tab_widget.py`): `_replace_party_declaration` now skips instead of appending when a party symbol isn't found in trainer_parties.h, preventing phantom empty structs.
- **Species data no longer mixed between Pokemon and Pokedex tabs** (`mainwindow.py`): Clicking a Pokemon in the Pokedex tab now syncs `previous_selected_species` so switching back to the Pokemon tab doesn't save the wrong species' widget values over the old selection.

### Changed
- **Pokemon tab loads instantly** (`mainwindow.py`): Removed redundant per-click reads of `species_info.h` (11,000+ lines) from `update_data()`. The file was being read from disk up to 2 times every time you clicked a species — once for types, once for gender ratio — even though this data was already loaded into memory at project open. Also removed a redundant double-call to `update_data()` (called directly, then again via `refresh_current_species()`). Species switching is now near-instant instead of ~5 seconds.
- **Species click no longer cascades into repeated reloads** (`mainwindow.py`): Clicking a species in the Pokemon tree was calling `update_pokedex_entry()` which in turn called `update_data()`, `save_species_data()`, and `_select_species_in_tree()` all over again — causing the same species to load 8+ times per click, freezing the UI. Replaced with a lightweight `_refresh_pokedex_display()` that only updates the pokedex text fields (name, category, description) without triggering any saves, selections, or data reloads.
- **Pokemon moves sub-tab "Remove" button now works** (`mainwindow.py`): The Level-Up, Tutor, and Egg Moves tables use combo boxes and spin boxes as cell widgets. Clicking a combo box or spin box sent the click to that widget, not the table row behind it — so rows never got selected and the old "Remove Selected" did nothing. Changed the remove logic to find the row that currently has focus (whichever dropdown or spinner you last clicked on) instead of relying on table selection. Also renamed the button from "Remove Selected" to just "Remove" since there's no visible selection step needed — just click a row's controls, then hit Remove.
- **Move rename character limit corrected** (`ui/custom_widgets/rename_dialog.py`): The rename dialog was hardcoded to 10 characters (POKEMON_NAME_LENGTH) for all entity types. Now uses the correct limit per type: 10 for Pokemon, 12 for moves (MOVE_NAME_LENGTH), 20 for items.
- **Dex category and description edits now persist** (`mainwindow.py`, `pokemon_data_extractor.py`): Multiple bugs prevented these edits from surviving a save-and-reload cycle. (1) `save_species_data` compared the UI value against `get_species_info()` which falls back to the Pokédex cache — so if the Pokédex already had the same text, the comparison showed "no change" and nothing was stored in `species_info`. Now uses `_dex_aware_set` which also writes values to species_info even when they match the pokedex fallback but aren't stored yet. (2) C headers were written AFTER rename operations which triggered a full reload from disk, discarding the edits. Reordered the save flow so C headers are written BEFORE renames. (3) On Refresh (F5), `_clear_plugin_cache_files` deleted species.json before re-extraction. The extractor then re-built from C headers only, and since categoryName/description live in pokedex_entries.h and pokedex_text_fr.h (not species_info.h in vanilla), they were lost. Now the extractor mirrors categoryName and description from pokedex entries into species_info after extraction. A stash/restore safety net also saves user-edited fields before JSON deletion and restores them after reload. (4) Moved `save_species_data` to run before the save dialog opens, preventing queued Qt timer callbacks from resetting the description widget to fallback values before the save reads it.
- **All species edits now survive Refresh (F5)** (`mainwindow.py`): The plugin pipeline's `parse_to_c_code` for species data was broken at runtime (method not in class dict despite being in source). Instead of continuing to patch the broken pipeline, added a new `_write_species_info_header()` method that directly reads species_info.h, patches each `[SPECIES_XXX]` block with the in-memory data (base stats, types, abilities, catch rate, EV yields, held items, growth rate, egg groups, etc.), and writes it back — same direct-open approach the evolution editor uses. This runs during Save before any other header writes. Handles format conversions: ability numeric IDs back to C constants (ABILITY_OVERGROW), gender ratio integers back to macros (PERCENT_FEMALE/MON_GENDERLESS), single-element egg groups duplicated for the two-slot format. Also fixed pokedex_text_lg.h not being updated during save (only fr.h was written), and reversed the reader order so FireRed text takes priority over LeafGreen.
- **Pokedex description newlines no longer double-escaped** (`plugins/pokefirered/pokemon_data.py`): When saving a species description, the `esc()` helper was escaping all backslashes (`\` → `\\`), turning the C newline escape `\n` into `\\n`. The C compiler then saw a literal backslash character which doesn't exist in the game's text encoding, causing a build error. Also fixed the line suffix from `r"\\n"` to `r"\n"` so each line in the description correctly ends with `\n` instead of `\\n`.
- **Types, egg groups, and abilities now extract both values** (`plugins/pokefirered/pokemon_data_extractor.py`): The regex that parsed `.field = value,` lines from species_info.h used `[^,]+` which stopped at the first comma. For braced fields like `.types = {TYPE_GRASS, TYPE_POISON},`, it only captured `{TYPE_GRASS` — losing the second type entirely. Every Pokémon in the editor showed only one type, one egg group, and one ability. Fixed the regex to treat `{...}` as a single capture group. Affects both the species struct parser and the macro definition parser.
- **Cache staleness detection for all extractors** (`plugins/pokefirered/pokemon_data_extractor.py`): All 8 extractors now pass `source_headers` to `_load_json()` for mtime-based cache invalidation, so editing a header file and reopening the project picks up the changes.
- **MOVE_NONE PP minimum fixed** (`ui/moves_tab_widget.py`): PP spinbox minimum changed from 1 to 0 and load/clear logic updated to allow pp=0 for MOVE_NONE instead of clamping to 1.

### Files Changed
- `plugin_abstract/pokemon_data.py`
- `plugins/pokefirered/pokemon_data.py`
- `plugins/pokefirered/pokemon_data_extractor.py`
- `local_env.py`
- `mainwindow.py`
- `ui/items_tab_widget.py`
- `ui/moves_tab_widget.py`
- `ui/trainers_tab_widget.py`
- `ui/custom_widgets/rename_dialog.py`

### Notes
- The `should_parse_to_c_code()` base class method previously had `return True` as dead-code protection — likely a temporary workaround that was never removed. The real change-detection logic (`pending_changes` flag set only when JSON actually differs from original) was fully implemented but unreachable.
- The line ending fix in `local_env.py` is the single most impactful change — on Windows, every file porysuite saved was getting CRLF line endings, causing git to report all saved files as modified even with zero content changes.
- The species_info.h per-click reads were the main cause of the ~5 second lag when clicking between Pokemon. The data is already synced from the header into JSON at project load time, making the per-click reads redundant.

---

## Unreleased (2026-03-29)
### Added
- **Git Panel window** (`git_panel.py`, `mainwindow.py`): Replaced the expanded Git menu with a dedicated scrollable window (Git → Git Panel…, Ctrl+Shift+G). Every section has a plain-English description explaining what it does and when to use it. Clicking the ⎇ branch label in the status bar also opens the panel. Sections: **Status** (branch, dirty count, ahead/behind, auto-refreshes every 60 s), **Pull** (radio: Upstream or Origin, with live URL labels), **Push** (shows origin host and commit count ready to push), **Commit** (checkbox file list, message field, commits on click), **Branches** (full local branch list, Switch and New Branch buttons), **Stash** (push/pop with stash entry list), **History** (last 10 commits inline, double-click to copy hash, View Full Log button), **Remotes** (Origin + Upstream edit fields with Apply/Save, Saved Remotes quick-switch list with add/remove). The Git menu retains Ctrl+Shift+L (Pull Upstream), Ctrl+Shift+U (Push), and Ctrl+Shift+K (Commit) as quick shortcuts.
- **Full git feature expansion**
  - **Configure Remotes…** — revamped dialog with two separate sections: **Origin** (your fork, sets `git remote set-url origin`) and **Upstream** (the base repo you clean-pull from, stored in app data, defaults to `pret/pokefirered`). Plus the existing saved-remotes quick-switch list. The "Pull from Upstream" item in the Pull submenu now shows and uses this saved URL.
  - **Status…** — shows current branch, changed file list, commits ahead/behind origin, stash count.
  - **Pull → Pull from Upstream** — uses the configured upstream URL (no longer hardcoded to pret). Label updates dynamically to show the hostname. Ctrl+Shift+L still triggers Pull from origin.
  - **Commit…** (Ctrl+Shift+K) — lists all changed files with checkboxes to stage, commit message field, and a Commit button. No terminal needed.
  - **New Branch…** — creates a new branch from HEAD and switches to it immediately.
  - **Stash Changes** — `git stash push --include-untracked`. Shows a warning if nothing to stash.
  - **Pop Stash** — `git stash pop`. Shows a warning if no stash entries exist.
  - **View Log…** — scrollable list of the last 30 commits (hash, date, message, author).
  - **Git status bar** — permanent label in the bottom-right of the window showing `⎇ branch  ✎N  ↑X ↓Y` (branch name, modified file count, commits ahead/behind). Refreshes after every pull, push, commit, stash, branch switch, and on project load.

### Fixed
- **Git → Pull is now a submenu** (`mainwindow.py`): Replaced the single "Pull from Remote" action with a submenu. (1) **⬇ Fresh from GitHub (pret/pokefirered)** — fetches directly from `https://github.com/pret/pokefirered.git` and resets to `FETCH_HEAD`, always gets vanilla upstream regardless of configured origin; (2) **⬇ Pull from origin** — existing behaviour, Ctrl+Shift+L still works; (3) **Local Branches** — dynamically listed each time the menu opens, current branch checkmarked/greyed. Clicking any other branch runs `git checkout <branch>` and refreshes.
- **Upstream pull now runs `git clean -fd` after reset** (`mainwindow.py`): "Pull from Upstream / Fresh from GitHub" now fully replaces the working tree — `git reset --hard` resets tracked files, then `git clean -fd` removes untracked files so nothing is left over from previous edits. PorySuite's own data files are excluded from the wipe (`project.json`, `src/data/*.json`, `temp/`) since they're not part of vanilla pokefirered but are required for PorySuite to function. The confirmation dialog runs the same dry-run first and shows which files will be deleted. Origin pulls are unchanged.
- **Git Pull deletes stale auto-generated files after reset** (`mainwindow.py`): `wild_encounters.h`, `items.h`, `heal_locations.h`, and the map/layout constant headers are generated by `make` from JSON sources but are NOT tracked by git. After a `git reset --hard`, these files kept their old content (e.g. renamed species constants like `SPECIES_ARBOK♀_F`), causing make to fail on the next build even though the JSON source was clean. The pull now deletes all known auto-gen targets after the reset; make will regenerate them fresh from the restored JSON on the next build. The deleted files are listed in the progress window.
- **Git Pull now shows live progress window** (`mainwindow.py`): The pull confirmation was silently suppressed (settings.ini `git_pull_confirm=true`), so the pull ran instantly with no visible info. A non-suppressible progress dialog now always opens when a pull starts, showing the remote URL, the current branch, and live git output line-by-line as it streams from `git fetch` and `git reset`. The Close button is disabled until the operation completes. The user can now see exactly where the pull is coming from and what git is doing.
- **Species rename no longer creates underscores from spaces** (`ui/custom_widgets/rename_dialog.py`): When a display name contained a space (e.g. "Bulba Saur"), it was converted to `SPECIES_BULBA_SAUR` instead of `SPECIES_BULBASAUR`. The `_display_to_suffix` function now removes spaces (joining words together) while still converting dashes to underscores (so "Ho-Oh" → `HO_OH` still works). The `_enforce_upper` method was also patched to strip spaces instead of converting them to underscores. Tooltips updated.

## Unreleased (2026-03-28)
### Added
- **Config tab** (`ui/config_tab_widget.py`, `mainwindow.py`): New **Config** main tab with two group-box sections. "Build Settings (config.mk)" exposes `GAME_VERSION` (FIRERED/LEAFGREEN dropdown), `GAME_REVISION` (0/1 dropdown), `GAME_LANGUAGE` (ENGLISH dropdown), `MODERN` checkbox, `COMPARE` checkbox, and `KEEP_TEMPS` checkbox. "Debug Settings (include/config.h)" exposes `NDEBUG` checkbox (toggling `#define`/`//#define`), `LOG_HANDLER` dropdown (AGB_PRINT/NOCASH_PRINT/MGBA_PRINT), and `PRETTY_PRINT_HANDLER` dropdown (OFF/MINI_PRINTF/LIBC). When `NDEBUG` is checked the log/print combos are greyed out. When `MODERN` is checked a note explains BUGFIX/UBFIX are implied. Saving uses regex-based line replacement in both files; missing variables are appended. Loaded on every `load_data()` call; saved by the main File → Save flow (and via the tab's own "Save Config" button).
- **UI content tab** (`ui/ui_tab_widget.py`, `mainwindow.py`): New **UI** main tab containing three sub-tabs:
  - **Name Pools** — reads `data/text/new_game_intro.inc` and `src/oak_speech.c`. Displays three editable groups (Male Player Names, Female Player Names, Rival Names) by parsing the `sMaleNameChoices[]`, `sFemaleNameChoices[]`, `sRivalNameChoices[]` arrays for label references and then rendering each label as a 7-char-limited `QLineEdit`. Saves by regex-replacing `.string "VALUE"` lines back in the .inc file.
  - **Location Names** — reads `src/data/region_map/region_map_sections.json` (primary) or `src/data/region_map/region_map_entry_strings.h` (fallback). Presents a two-column table (Constant | Display Name, editable, 16-char max). Saves by patching the JSON or .h file in place.
  - **Key Strings** — reads `src/strings.c`. Provides labelled `QLineEdit` / `QPlainTextEdit` widgets for nine known variables (`gText_EggNickname`, `gText_NewGame`, `gText_Continue`, `gText_Boy`, `gText_Girl`, `gText_Kanto`, `gText_National`, `gOakSpeech_Text_WelcomeToTheWorld`, `gOakSpeech_Text_LetsGo`). Multiline fields display GBA escape codes (`\\n`, `\\p`) as-is. Saves by replacing the content between `_("` and `")` for each variable. Loaded on every `load_data()` call; saved by File → Save (and via the tab's own "Save UI Content" button).
- **Git menu** (`mainwindow.py`): New top-level **Git** menu inserted between Project and Tools, replacing the former standalone "Pull from Remote…" File menu item. Contains three actions:
  - **Configure Remote…** — Multi-remote manager dialog. Shows the currently active `origin` URL and branch, lists all saved remotes for the project (persisted in `<app_data>/git_remotes.json` keyed by project directory). Supports adding new remotes (name + URL), removing entries, and switching the active `origin` with one click (`git remote set-url origin <url>` or `git remote add origin <url>`). The active remote is marked with ✓ in the list. Saved list persists across sessions so you can quickly flip between e.g. the pret upstream and your own fork.
  - **Pull from Remote** (Ctrl+Shift+L) — `git fetch origin` → `git reset --hard origin/HEAD` in a background QThread, then auto-calls `_refresh_project()`. Confirmation dialog now explicitly lists all three categories of state that will be lost: uncommitted local file changes, unsaved editor edits, and queued rename operations not yet written to disk. On success, clears `refactor_service.pending`, `setWindowModified(False)`, and all `data_obj.pending_changes` flags before refreshing — this prevents a critical bug where pending renames (e.g. BULBASAUR→OCTO) could be re-applied to the freshly-reset source files on the next Save, creating a header/map.json mismatch that breaks the build.
  - **Push to Remote** (Ctrl+Shift+U) — `git push origin <branch>` in a background QThread. Confirmation dialog shows the ahead-of-origin commit log so you can see what will be pushed. All three actions are disabled until a project is loaded and re-disabled during any in-progress operation to prevent double-clicks.
- **File → Refresh (F5)** (`mainwindow.py`, `ui/ui_mainwindow.py`): Replaces the now-redundant Tools → Rebuild Caches and Tools → Clear Caches on Next Load actions, which have been removed from the Tools menu. Refresh does everything both did, plus the two things they were missing: sprite/icon cache clearing (`_species_icon_cache`, `items_editor._icon_cache`) so swapped PNG files are picked up without restarting, and force-reload of the Trainers and Moves lazy tabs. The dead `clear_caches_next_load` method is removed; `rebuild_caches()` is kept as an internal helper called by `_refresh_project`.
- **Post-save tab refresh** (`mainwindow.py`): After **File → Save**, the Trainers and Moves tabs are now force-reloaded from the freshly written data, the same way Items already was. Previously, those two tabs were only lazy-loaded on tab-switch — meaning if you were already on the Trainers or Moves tab (or had just done a rename), the list would stay stale until you manually switched away and back. The fix adds explicit `_load_trainers_editor()` and `load_moves_defs_table()` calls immediately after `load_data()` in the save handler.
- **Rename scope fix — map.json hidden items** (`plugins/pokefirered/refactor_service.py`): `_search_and_replace` now includes `.json` files under `data/` in its scan. Previously, renaming an item like `ITEM_POTION` left `data/maps/**/map.json` files (which store hidden/field item pickups as `"item": "ITEM_POTION"` string values) untouched, causing a build failure. The `data/` scan is separate from the `src/data/*.json` files handled by `_rename_in_json`, so there is no double-update conflict. This fix applies to all renames — species, trainers, moves, and items.
- **Move rename** (`ui/moves_tab_widget.py`, `mainwindow.py`): A **Rename…** button sits in the Identity card next to the constant name. Clicking it opens a dialog pre-filled with the current constant; entering a new name queues a `rename_move` operation (the same `refactor_service` machinery already used for trainers/species) that on **File → Save** updates `include/constants/moves.h`, `src/data/moves.json`, and every `.c`/`.h` reference across the project. The in-memory moves list is updated immediately so the list reflects the new name without a reload. Reference count shown in the confirmation dialog.
- **Item rename** (`ui/items_tab_widget.py`, `mainwindow.py`): A **✎ Rename…** button in the Items toolbar (next to Reset to Vanilla) opens the same-pattern dialog. On confirm, queues `rename_item` which updates `include/constants/items.h`, `src/data/items.json`, and all source references. The item's `itemId` field and in-memory list entry are updated live.
- **Moves tab — full overhaul** (`ui/moves_tab_widget.py`):
  - **Type dropdown**: Removed `setEditable(True)` — Type is now a pure sealed dropdown, eliminating free-text entry.
  - **Effect dropdown with all 214 effects**: `EFFECT_CHOICES` constant hardcodes every `EFFECT_*` from `include/constants/battle_move_effects.h` sorted alphabetically; `populate_effects()` merges this list with any extras found in the data so no effect is ever missing. Field stays editable for type-to-search filtering.
  - **Move ID field**: Identity card now shows the numeric move ID (read-only) alongside the constant name.
  - **Gen 3 Category label**: Classification card shows Physical / Special / Status, auto-derived from type (Gen 3 uses a type-based split, not a per-move category). Updates live when Type or Power changes.
  - **PP Max hint**: Small label below PP shows the max PP after three PP Ups (base × 1.6), updating as you edit.
  - **Type filter in list panel**: A "All Types" / individual type combo sits above the search bar so the list can be narrowed by type instantly.
  - **Move count label**: Shows `N / total` visible moves below the type filter.
  - **Rich tooltips on list items**: Hovering a move in the list shows type, category, power, PP, and effect in a concise popup.
  - **Flag tooltips**: Each flag checkbox shows its constant name and plain-English description as a tooltip.
  - **Target tooltip**: Target combo explains which battlers the move hits.
  - **Priority tooltip**: Priority spinbox shows a reference guide to common priority brackets.

## Unreleased (2026-03-27)
### Added
- **Modernised Moves tab** (`ui/moves_tab_widget.py`): The global Moves tab is now a panel-based UI (searchable list on the left, scrollable detail panel on the right) matching the style of the Items, Trainers, and Pokédex tabs. Detail panel covers all move fields: Constant (read-only), Power, Accuracy, PP, Priority, Secondary Effect Chance, Type, Effect, Target, Flags (individual checkboxes for each `FLAG_*`), and Description with a 21-char / 4-line limit counter.
- **Pokédex size-comparison preview** (`ui/pokedex_detail_panel.py`): The "Sprite Scale & Offset" card now contains a live 128×96 `_SizePreview` widget that faithfully re-implements the in-game Pokédex size-comparison screen. Coordinate math derived from `src/sprite.c` (`CalcCenterToCornerVec`, `oam.y = sprite.y + y2 + centerToCornerVecY`) and `src/pokedex_screen.c` (Pokémon at game x=40/y=104, Trainer at x=80/y=104). GBA affine scale is INVERSE (`vis_size = 64 × 256/scale`), content centred within the 64×64 OAM bounding box. Transparency fixed via `_load_gba_sprite()`: converts to ARGB32, reads palette-index-0 colour from pixel(0,0), zeroes all matching pixels (Qt does not reliably apply `tRNS` for 4-bit indexed PNGs).
- **Item description character-limit box** (`ui/items_tab_widget.py`): The item description field is now a `DexDescriptionEdit` widget (36 chars / 3 lines) with a per-line counter label, matching the Pokédex description pattern. Load/save correctly converts between the GBA literal `\n` separator and real newlines for display.

## Unreleased (2026-03-26)
### Added
- **Tutor learnset editor**: The Tutor sub-tab under Pokémon > Moves now uses the same Add/Remove table pattern as the Egg Moves tab. Any move can be added via a dropdown; rows can be deleted with the Remove Selected button. Previously the tab was a read-only checkbox list limited to pre-discovered tutor moves.
- **Global Moves tab improvements**: The top-level Moves tab now has a live search/filter bar (searches constant, effect, and type columns), an **Effect** column (replaces the blank Name column), and a **Priority** column. Column headers are clickable for sorting.
- **Evolution parameter dropdowns**: When an item-based evolution method is selected (e.g. `EVO_ITEM`, `EVO_TRADE_ITEM`), the parameter field now shows an item dropdown instead of a plain text box. Trade and Friendship methods correctly disable the parameter field. Root cause fixed: `refresh_evo_param_choices` was checking `"ITEM" in currentText()` (title-case display name) instead of `currentData()` (the raw C constant).
- **Trainer AI flags checklist**: Double-clicking the **AI Flags** column in the Trainers table opens a scrollable checklist dialog listing all 13 `AI_SCRIPT_*` flags with plain-English descriptions. Hovering over the AI Flags cell shows a tooltip listing what each active flag does.
- **Trainer rename**: Double-clicking the **Constant** column (col 0) in the Trainers table opens a rename dialog. On confirm the rename is staged and written to disk on **File > Save**, updating:
  - `include/constants/opponents.h` — the `TRAINER_*` `#define`
  - `src/data/trainer_parties.h` — `sParty_*` symbol declarations and usages
  - `src/data/trainers.h` — struct key and `.party` field reference
  - `src/data/trainers.json` — the JSON key
  - All other `.c`/`.h` references under `src/` and `include/`
- **`RefactorService.rename_trainer`**: New method in `refactor_service.py` that derives the `sParty_*` symbol from the trainer constant (`_trainer_to_party_symbol`), sweeps all C/H sources for both tokens, and updates `trainers.json`. Called automatically by `apply_pending` on Save.

### Fixed
- **Trainer rename → build failures**: Previously renaming a trainer in the JSON key left `opponents.h` and `trainer_parties.h` with the old constant names, causing undeclared-identifier errors at compile time. The new sync logic in `parse_to_c_code` and the new `rename_trainer` method keep all three files in sync.

## Unreleased (2026-03-25)
### Added
- **Make / Make Modern** actions added to the **Project** menu (Ctrl+M / Ctrl+Shift+M). Clicking either opens a new MSYS2 mingw64 terminal in the pokefirered project directory with the devkitARM toolchain on PATH so the ROM builds in the correct environment.
- `LaunchPorySuite.bat` completely rewritten: writes `launch.log` on every run, pauses and displays the log path if the app exits with an error, and redirects stderr so failures are never silently lost.

### Fixed
- **Launch page branding**: window title and label now read "PorySuitePyQT6" instead of "PorySuite"; taskbar entry shows "PorySuitePyQT6" instead of "Python" (`app.setApplicationName`/`setApplicationDisplayName`).
- **Species rename — incorrect change count**: `preview_patch_plan()` now includes JSON cache files (`species_graphics.json`, `starters.json`, `evolutions.json`, `moves.json`) so the confirmation dialog count matches actual changes applied on Save.
- **Species rename — stale display after save**: `refactor_service.py` regex was double-escaping quotes in the display-name substitution (`_("…")` lines), causing the name update to silently fail. Fixed escape sequences.
- **Species rename — JSON cache files not updated on Save**: `apply_pending()` now calls new helpers `_rename_in_evolutions_json`, `_rename_in_moves_json`, `_rename_in_species_graphics_json`, `_rename_in_starters_json` so all cache files are kept in sync.
- **Rebuild Caches empties species name**: root cause was `SpeciesDataExtractor` holding stale cached `_species_header_lines` from before a git reset. Added `reset_cache()` base method to `AbstractPokemonDataExtractor` and called it before each extractor run in `rebuild_caches()`. Also rewrote `rebuild_caches()` with per-extractor error isolation so one failing extractor no longer aborts the rest.
- **clear_caches() wrong path**: was using `LocalUtil(project_info).repo_root()` which can resolve differently from `project_info["dir"]`. Fixed to use `project_info["dir"]` directly, matching what `save()` uses.
- **Pre-save validation wrongly flagged `#include "data/battle_moves.h"`** in `src/pokemon.c` as something to remove. That include is required (defines `gBattleMoves[]`). Validation now detects its *absence* and offers to add it back instead.
- **`src/data/graphics/items.h`**: PorySuite had appended item struct data to this file from an older project state before a fresh pokefirered pull. The stale struct data (5 900+ lines) is now stripped; only the INCBIN graphics lines remain, matching the vanilla file. The items struct data lives in `src/data/items.h` which is correctly included by `src/item.c`.
- **`src/data/battle_moves.h`**: PorySuite's moves writer had regenerated this file without the `const struct BattleMove gBattleMoves[MOVES_COUNT] =` array declaration, breaking the build. Restored from git and documented the expected format.
- **GCC 15.1.0 compatibility**: added `-Wno-attribute-alias` per-file CFLAGS override for `pokemon.o` in the pokefirered Makefile to suppress a `-Wattribute-alias` warning that became an error with the newer devkitARM toolchain.

### Changed
- Removed duplicate/stale `rename_item`/`rename_move` methods from `refactor_service.py` that wrote immediately rather than queuing.
- `rebuild_caches()` in `mainwindow.py` simplified: all monkey-patching removed; calls `source_data.rebuild_caches(self.log)` directly.

## Unreleased
### Known Issues
- Phase 5 - Items Pipeline refactor is unfinished. `pytest` currently fails on `WritebackTest::test_item_writeback`, `test_species_writeback`, `test_starter_writeback`, `ProjectTest::test_edit_starters_updates_sources`, and `SpeciesGraphicsExtractorTest::test_graphics_json_created`. Address these before closing the phase.

### Added
- Learnset editor now supports adding/removing rows, dropdown move selection, and method-specific value widgets so per-species changes stay valid without manual JSON edits.
- Added regression tests (`tests/test_cache_tools.py`) covering Tools > Rebuild Caches and Tools > Clear Caches on Next Load so TM/HM learnsets rebuild while authored files such as `items.json` remain untouched.
- Pokédex edits in the Info tab now write back to engine sources:
  - `.categoryName` fields are updated in `src/data/pokemon/pokedex_entries.h`.
  - Localized description strings are updated in `src/data/pokemon/pokedex_text_fr.h`.
  - Each rewrite logs a concise message to the log output.
  - The description editor enforces the per‑line character limit detected from
    `pokedex_text_fr.h` (typically 42) and sizes the input box to match.
 - Flags: FireRed‑safe flags in the Info tab (No Flip, Genderless, Egg Group: Undiscovered, Starter). Dex flags are displayed but read‑only and reflect `include/constants/pokedex.h`.
 - Pokedex extractor now derives Regional Dex from `KANTO_DEX_COUNT` in `pokedex.h` (defaults to first 151 if missing).
 - Asset cloning prompts: On save, the editor detects newly added species/items and offers to clone graphics from an existing template. Species graphics are copied to `graphics/pokemon/<new-slug>/...`; item graphics are copied under `graphics/items/`. A summary dialog lists created files for manual editing. No headers are created by this step.
 - Pokédex: In‑place patcher for `include/constants/pokedex.h` that updates only the `NATIONAL_DEX` enum body while preserving all other file content. Abort with a blocking message if the enum block cannot be uniquely located (no reflow, no regeneration, no fabricated headers).
### Fixed
- Declining the "Apply C Header Changes" prompt now aborts the save entirely so no headers or JSON files are written.
- Header previews now filter out missing learnset files so the editor never proposes generating new C sources.
- Learned header paths are auto-resolved (including alternate FireRed layouts) before saving, so write-backs always point at the existing sources.
- Console logging now feeds directly into the Output pane so launch messages (from the .bat file or CLI) remain visible while editing.
- Items extractor now reads the real headers (`src/data/items.h` when present, otherwise `src/data/graphics/items.h`) with a brace-balanced parser so nested structs, comments, and multiline fields no longer blank out the Items tab. If neither header exists it logs a warning instead of creating placeholder files.
- Fixed FireRed learnset parsing so the Pokemon > Moves sub-tab populates even when `species_moves` is missing. Level-up entries now come from the C headers instead of returning an empty table.
- TM, HM, and tutor learnsets now rebuild from the FireRed headers so the per-species Moves table shows every method after a cache refresh.
- Removed references to non-existent `pory_text.h`; FireRed uses `pokedex_text_fr.h`.
 - Prevented Make failures from wholesale `pokedex.h` rewrites by switching to values‑only enum patching. No `hoenn_dex.h` is created or required.
- File > Save no longer reverts Pokédex description in the Info tab.
  We now save the current species before any UI refresh to preserve edits,
  and the description persists across sessions.
 - `pokedex_text_fr.h` rewrites now use vanilla multi‑line formatting for
   descriptions instead of a single long line. A prior regex crash was replaced
   by a safe string-scan implementation.
### Changed
- Items save path now preserves the original JSON layout (dict, list, or `"items"` wrapper) and updates the existing header entries in place, so renames and price changes propagate to the real sources without generating new files. The automated `parse_to_c_code` path is still failing the write-back suite, so leave this bullet under review until the tests are green.
 - Pokédex saves now patch `include/constants/pokedex.h` in place instead of regenerating it. Only `NATIONAL_DEX` enum entries are changed; everything else remains byte‑for‑byte intact.
- Pokemon tab Reset button now discards in-memory edits for the current species without fetching upstream data, and the extra button on the Moves sub-tab was removed.
- Saved learnsets are automatically sorted (LEVEL → TM → HM → tutor → egg) so regenerated JSON and headers match vanilla FireRed ordering.
- Tools > Rebuild Caches and Tools > Clear Caches on Next Load now target only plugin caches, leaving `items.json` and other authored JSON intact while clearing FireRed learnset overlays.
 - Renamed “Legendary” flag to “Egg Group: Undiscovered” to reflect the actual engine behavior.
- Flags: synthesize editable flags for plugins without `species_flags` (e.g., PokéFirered). Flags edits are now kept in-memory until explicit save.
# Changelog

All notable changes to this project will be documented in this file.

## Unreleased
### Added
- Moves tab now displays each species' learnable moves and lets you edit
  learn methods using the new `get_species_moves` data-manager helper.
- Option to rebuild species caches automatically when type mismatches are
  detected by setting `PORYSUITE_REBUILD_ON_TYPE_MISMATCH=1`.
### Fixed
- Species extraction now copies default `types` and `abilities` lists so editing
  one species no longer alters another when switching between them in the
  editor.
- Species extraction now synchronizes `types` with `species_info.h`, overwriting
  mismatches or `TYPE_NONE` entries.
- Parsing to C code no longer crashes when expected source files are missing.
- Backup step checks for missing source files with `os.path.isfile` and logs a
  warning instead of raising an exception.
- Saving stats or evolutions now regenerates `species_info.h` and
  `evolution.h` so edits persist in the source headers.
- Added missing `os` import in `pokemon_data.py` to prevent errors when resolving
  species image paths.
- Switching sub-tabs in the Pokémon editor now reloads the selected species so
  the Graphics view repaints correctly.
- Selecting a Pokémon in the tree now refreshes the active data tab so you no
  longer need to open the Pokédex view first.
- `get_species_image_path` now logs the expected image constant and file path
  whenever the PNG is missing.
- `get_species_image_path` now warns when a PNG exists but cannot be read,
  preventing invalid URLs from reaching the UI.
- `get_species_image_path` normalizes image paths before creating Qt URLs so
  backslashes are converted to forward slashes on Windows.
- Parsing `src/data/graphics/items.h` now logs a descriptive error and returns
  `None` when no item entries are collected so callers can handle missing data.
- `ItemsDataExtractor` reads `src/data/items.json` first and only parses
  `src/data/graphics/items.h` when the JSON is missing or invalid.
- `PokemonItems` backs up `src/data/items.json` and regenerates
  `src/data/graphics/items.h` from that JSON when exporting C code.
- Saving an item-based evolution no longer errors when `src/data/graphics/items.h`
  is missing.
- Projects without `items.json` still populate held-item dropdowns after
  extraction.
- Fixed crashes when loading list-based `items.json` by converting the
  `items` array into a dictionary automatically.
- Snorlax now correctly holds `ITEM_LEFTOVERS` in both held-item slots.
- Deleting `src/data/species.json` and rebuilding caches regenerates it from
  `species_info.h`, ensuring Pokémon like Pikachu show `TYPE_NONE` as their
  second type.
- Cached gender ratios in `species.json` are now corrected using
  `species_info.h` when the values differ.
- Removing `src/data/items.json` triggers a rebuild from
  `src/data/graphics/items.h` so held-item dropdowns remain populated.
- `update_data` now reads evolutions using `get_evolutions` so projects with
  `evolutions.json` show their data correctly.
- Clarified that upstream FireRed includes `src/data/items.json` and that
  the extractor uses that JSON as the primary source, parsing
  `src/data/graphics/items.h` only when the JSON is missing or invalid.
- Missing evolution methods were caused by an incomplete `constants.json` and are
  now regenerated automatically.
- Evolution tree items now store the method constant so editing parameters no
  longer replaces it with the display label.
- Selecting an existing evolution no longer triggers unintended edits when the
  combobox values update.
### Added
- New `PokemonEvolutions` data class in the FireRed plugin. Evolution data is
  loaded from `evolutions.json` when available and managed through
  `get_evolutions` and `set_evolutions` helpers.
- `evolutions.json` is now generated from `src/data/pokemon/evolution.h` when
  missing.
- Added `PokemonEvolutionsExtractor` to parse `pokemon/evolution.h` and populate
  `evolutions.json` when rebuilding caches.
- New `PokemonTrainers` data class with table editing. Edits save to
  `trainers.json` and regenerate `trainers.h` when exporting C code.

### Added
- `mainwindow` now logs the full image URL after setting graphics so you can
  verify which file path is in use.
- FireRed plugin output is now forwarded to the log window so initialization
 messages are visible in the UI.
 - Type and evolution method definitions are extracted from
   `include/constants/pokemon.h` when `constants.json` is missing.
- `constants.json` is regenerated when it lacks the `types` or
  `evolution_types` entries.
- Species extraction pads single-type Pokémon with `TYPE_NONE` instead of
  `TYPE_NORMAL`.
  - Items can be edited through a new table on the Items tab. Saving updates
    `items.json` and regenerates `src/data/graphics/items.h`.
- Moves can now be edited through a table on the Moves tab. Saving updates
  `moves.json` and regenerates `battle_moves.h` and `move_descriptions.c`.
- `src/data/items.json` is used as the primary source and is only rebuilt from
  `src/data/graphics/items.h` when missing or invalid.
- Pokémon evolutions can now be added or deleted directly in the editor. Saving
  writes the updated list to `evolutions.json` when that file exists, otherwise
  it falls back to `species.json`. Switching species reloads the list so you see
  the current rows.
- Evolution method constants are parsed from `pokemon.h` so dropdowns show
  readable names.
- Species extraction now recovers missing `genderRatio` values from
  `species_info.h` when loading cached data.

### Documentation
- Clarified in README and TROUBLESHOOTING that stale `species.json` can cause
  wrong types or held items and that **Tools > Rebuild Caches** fixes the issue.
- Clarified that vanilla FireRed stores items in `src/data/items.json` and
  that `src/data/graphics/items.h` is regenerated or used only when that JSON is
  missing, still the template, or not valid JSON.
- Added notes in README, TROUBLESHOOTING and AGENTS that `src/data/graphics/items.h`
  must be generated before launching PorySuite.
- Documented formatting‑preserving write‑back for FireRed:
  - Items headers are patched in place (values only) while preserving whitespace, comments, and blank lines; new fields/blocks copy local formatting; unknown layouts abort.
  - Learnset headers (level/TM/HM/tutor/egg) are modified in place only for the relevant species; additions/removals copy local formatting; no reflow of entire files.
  - Abilities write‑back is disabled; `include/constants/abilities.h` remains byte‑for‑byte intact (including `ABILITIES_COUNT` and `#endif`).
- Clarified “Format Fidelity & Missing Sources”: all edits must read from and write back to canonical sources in their native formats; when a required canonical file is missing or unreadable (including items headers), the editor shows a blocking message and aborts the operation without writing anything. Cache regeneration remains a separate, opt-in action.

### Changed
- `src/data/items.json` remains the primary source. `src/data/graphics/items.h`
  is parsed only when that JSON is missing or invalid.
- Type dropdowns now look up indices by constant name so `TYPE_NONE` selects the
  correct entry even if constant values shift.
- `PokemonDataManager` now registers `PokemonConstants` before evolutions so
  combo boxes populate immediately.
- `PokemonItems` now backs up `src/data/graphics/items.h` instead of the JSON
  source, and file paths use `os.path.join` for cross-platform safety.
- Species extraction caches `species_info.h` so repeated reloads run faster.

## 0.1.4
### Changed
- FireRed plugin version bumped to 0.1.4.

## 0.1.3
### Changed
- FireRed plugin version bumped to 0.1.3.

## 0.1.1
### Added
- Initial support for the official FireRed decompilation through the new **FireRed plugin**.
- Automatic generation of `species.json`, `moves.json` and `items.json` if those files are missing.
- Plugins are loaded first from the user data directory at `platformdirs.user_data_path(APP_NAME, AUTHOR)/plugins` before falling back to the bundled `plugins/` directory.
- The FireRed plugin reads and stores its generated JSON files inside `src/data` of the decomp repository.
- Missing headers now log their absolute path when extraction falls back to cached data.
- Extraction now prints `Wrote <path>` after each JSON file is generated.
- Setup wizard progress is emitted via signals to avoid manipulating widgets from background threads.
- Added `RefactorService` to the FireRed plugin and a **Tools > Rename Entity...**
  action for previewing and applying constant renames across source files.
- FireRed plugin now derives sprite, icon and footprint constants from the
  species name when `species.json` lacks explicit entries, ensuring graphics load
  correctly in the editor.
- Numeric placeholders in `species.json` no longer block this fallback so
  sprites and icons display even when values are set to 0.
## Unreleased
### Added
- Tools: new action `Tools > Open Crashlogs Folder` to quickly open the current
  session’s `crashlogs/` directory.
- FireRed species write-back now emits `src/data/pokemon/species_info/pory_species.h`,
  enabling tests and tooling to see `.baseHP` and related fields in a generated overlay.

### Changed
- Stats tab polish: all EV yield spinboxes (HP/Atk/Def/SpA/SpD/Speed) now cap at
  `3` to match Gen 3 EV yield limits.

