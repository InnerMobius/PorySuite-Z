# PorySuite-Z v0.0.61b

> pokefirered projects only.

A targeted stability release. The headline is a data-corruption bug in the species rename pipeline that produced ghost entries like `SPECIES_OCTOROCKROCK` at the tail of `species.json`, orphaned dex rows with blank names, and mixed-case graphics symbols (`gMonFrontPic_Octorockrock` alongside `gMonPalette_Octorock`) whenever a rename's new name contained the old name as a substring. Two defensive layers close the bug so it cannot recur. Also in this build: the piano roll tick-position dialogs no longer refuse to go past the last note, the Credits editor participates in the unified dirty system, and Trainer Graphics grid thumbnails render through the palette bus.

## What's New in 0.0.61b

### Species Rename — Cascade Corruption Fixed

Renaming a species to a name that contains the old name as a substring corrupted every downstream reference. Two confirmed trigger paths:

- `Octo → Octorock` produced a ghost `SPECIES_OCTOROCKROCK` entry at the tail of `species.json` holding the original species' data, plus a blank `SPECIES_OCTOROCK` default entry synthesized by the parser for the orphaned constant.
- `Fairy Quee → FairyQueen` (the earlier "Fairy Queen" display name had been clamped to 10 chars on a prior save) produced the same class of corruption: mixed-case graphics symbols, an orphaned dex entry with a blank name, and a duplicate tail entry carrying the recent edits.

**Root cause.** `apply_pending()` ran token replacements sequentially (`for a, b in tokens: self._search_and_replace(a, b, ...)`) using plain substring matching (`if old in ln`). When the new name contained the old name as a substring, later tokens re-matched the OUTPUT of earlier tokens. Walking through `Octo → Octorock`:

1. Token `SPECIES_OCTO → SPECIES_OCTOROCK` rewrote the constant cleanly.
2. The later token `OCTO → OCTOROCK` then scanned the file and found `OCTO` inside the just-written `SPECIES_OCTOROCK`, rewriting it to `SPECIES_OCTOROCKROCK`.
3. Same path for the camelcase pair: `"speciesName": "Octorock"` got matched and rewritten to `"Octorockrock"`.
4. Graphics symbols hit by two successive tokens ended up double-mangled; symbols hit by only one stayed single. That produced the mixed state visible in the rename preview.
5. With the JSON key now `SPECIES_OCTOROCKROCK`, the species loader re-encountered `SPECIES_OCTOROCK` in `include/constants/species.h` without a matching JSON entry and synthesized a blank default — the "first Pokemon blank" symptom.

**Two defensive layers close this for good.**

- **Atomic multi-token regex sweep (`_multi_search_and_replace`).** Each source file is scanned once with a single compiled regex built from `re.escape` + `|` alternation over all old keys, sorted longest-first. `re.sub` consumes each match and advances past the replacement, so a rewritten substring cannot be re-matched by any later token in the same sweep. Longer patterns win at overlap, so `SPECIES_OCTO` beats `OCTO` at the same position. The species branch of `apply_pending()` uses this helper in place of the cascading per-token loop.
- **Anchored key/path matching in `_rename_in_species_graphics_json`.** Symbol keys like `gMonFrontPic_Pika` are only rewritten when the key ends with `_<old_camel>`; path segments like `graphics/pokemon/pika/front.png` are only rewritten when a segment exactly equals `<old_slug>`. This closes the sibling-species collision case: renaming `Pika → Pikachu` while an unrelated `Pikachu` species already exists no longer mangles the sibling's `gMonFrontPic_Pikachu` into `gMonFrontPic_Pikachuchu`.

The net result: renames where the new name contains the old name — `Pika → Pikachu`, `Fire → Firebird`, `Octo → Octorock` — are now safe. Longest-first alternation and single-pass substitution are the properties that make it safe, and both are locked in by code comments plus the DO-NOT-regress entry in the bug tracker.

**Caveat for already-damaged projects.** This fix prevents future corruption. It does NOT auto-repair projects that were damaged by the previous behaviour. If a project already contains ghost entries (e.g. a duplicate `SPECIES_FAIRYQUEEN` at the tail of `species.json` alongside an orphaned `SPECIES_FAIRY_QUEE`), the cleanest recovery is to `git checkout` to a commit before the broken renames and redo those renames on the patched build. Manual cleanup — deleting the tail duplicate from `species.json`, removing the orphan constant from `include/constants/species.h`, removing its row from `src/data/text/species_names.h`, removing its block from `pokedex_entries.h`, and removing it from the national dex order — is possible but error-prone.

### Piano Roll — Tick Dialogs Unclamped to 16-bit Ceiling

All five tick-position input dialogs in the song structure panel (Edit Position, Section, Loop Back, Pattern Call, End Song) were clamping to `_total_ticks` — i.e. the last note tick in the song — so a GOTO, loop boundary, or end-song marker could not be placed past the final note even when the track legitimately needed one there. All five dialogs now cap at `65535`, the actual GBA 16-bit tick ceiling. Sound effects with short note ranges but long loop windows (the low-HP heartbeat is the canonical example) can now have their loop target placed in the empty space past the last note, which is where the original pokefirered `.s` files put them.

### Credits Editor — Dirty-State Wiring

The Credits editor now participates in the unified dirty-state system:

- Emits `modified` on every edit and `saved` on successful write, wired to the main window's sidebar dot and title-bar asterisk.
- Individual list rows tint amber (`#3d2e00`) when their entry has unsaved edits. The dirty set survives `_populate_list()` rebuilds, so reorder and delete operations don't lose the tint state.
- Drag-reorder tints every row (reorder affects the full list ordering, so every row is now dirty).
- Delete marks the section dirty but does not falsely tint remaining rows — those weren't edited.
- Save clears the dirty set and removes amber tinting on all rows in one pass.

### Trainer Graphics — Thumbnail Palette

Trainer Graphics grid thumbnails were using a flat `QPixmap(path)` that ignored the separate `.pal` file and any unsaved RAM palette edits — the same bug pattern the v0.0.6b sprite-render rewrite was supposed to kill everywhere in the app. The grid was missed. Thumbnails now render through the palette bus (`ensure_trainer_palette_from_png` + `load_sprite_pixmap`), so the grid shows the correct colors from disk immediately on load and stays in sync with cross-tab palette edits.

## Files of Note

- Updated: `core/refactor_service.py` — new `_multi_search_and_replace()`; species branch of `apply_pending()` uses it in place of the per-token loop; `_rename_in_species_graphics_json()` switched to anchored key/path matching.
- Updated: `ui/piano_roll_structure.py` — five tick-position dialogs unclamped from `_total_ticks` to `65535`.
- Updated: `ui/credits_editor.py` — `modified`/`saved` signals, `_dirty_symbols` set, amber row tinting on edit/reorder/save.
- Updated: `ui/unified_mainwindow.py` — Credits editor's `modified`/`saved` signals wired to `sectionDirtyChanged` and `setWindowModified`.
- Updated: `ui/trainer_graphics_tab.py` — grid thumbnails rendered through the palette bus.
- Updated: `core/app_info.py` — VERSION bump to `0.0.61b`.

## Known Limitations

- **Pre-existing corruption is not auto-healed.** Projects that ran a damaging rename on an earlier build still contain the ghost entries. See the caveat under "Species Rename" above for recovery options.
- **Camelcase substring matches in unrelated identifiers.** The atomic-sweep fix stops self-cascade within a single rename and stops sibling-species collision in `species_graphics.json`. It does not yet enforce word boundaries on the camelcase token in the general source-tree sweep — so renaming `Pika → Foo` while an unrelated identifier like `MyPikaThing` happens to exist in `.c` / `.h` / `.inc` will still rewrite that identifier's substring. This has not been observed in the wild; tightening to word boundaries is a follow-up task that needs to handle underscore-adjacent identifiers (`gMonFrontPic_Pika`) without false negatives.
- **Rename dialog already enforces the 10-char display limit.** The "Fairy Queen → Fairy Quee" truncation that led Saten into the second rename pre-dates a field that already has `setMaxLength(10)` plus a live counter. If you're loading a project whose display names were clamped by an earlier build, the shortened names persist on disk; fix them in the rename dialog.
- No other editor behaviour is changed from v0.0.6b. Everything that already worked still works.

---

> **AI Disclosure:** As with previous releases, the vast majority of this code was written with Claude. The human developer (InnerMobius) directs architecture, tests every change, and confirms behaviour. If AI-assisted code is a dealbreaker, this isn't your project.

> **Beta software.** Always keep backups of your decomp project (use git!) and test thoroughly after every editing session.
