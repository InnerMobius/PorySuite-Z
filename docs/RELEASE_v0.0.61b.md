# PorySuite-Z v0.0.61b — Release Notes

**Released:** 2026-04-19  
**Previous release:** v0.0.6b

---

## Highlights

This is a targeted stability release focused on a data-corruption bug in the species rename pipeline. Two defensive layers were added so the bug cannot recur, plus a handful of smaller fixes to the sound editor, Credits tab, and Trainer Graphics thumbnails.

---

## Species Rename — Cascade Corruption Fixed

**Problem:** Renaming a species to a name that contains the old name as a substring corrupted every downstream reference. Two confirmed report paths:

- `Octo → Octorock` produced a ghost `SPECIES_OCTOROCKROCK` entry at the tail of `species.json` holding the original species' data, plus a blank `SPECIES_OCTOROCK` default entry synthesized by the parser for the orphaned constant.
- `Fairy Quee → FairyQueen` (the prior "Fairy Queen" display name had been clamped to 10 chars on an earlier save) produced the same class of corruption: mixed-case graphics symbols (`gMonFrontPic_Octorockrock` alongside `gMonPalette_Octorock`), an orphaned dex entry with a blank name, and a duplicate tail entry carrying the edits the user wanted to keep.

**Root cause:** `apply_pending()` ran token replacements sequentially (`for a, b in tokens: self._search_and_replace(a, b, ...)`) using plain substring matching (`if old in ln`). When the new name contained the old name as a substring, later tokens re-matched the OUTPUT of earlier tokens. Walking through `Octo → Octorock`:

1. Token `SPECIES_OCTO → SPECIES_OCTOROCK` rewrote the constant cleanly.
2. The later token `OCTO → OCTOROCK` then scanned the file and found `OCTO` **inside** the just-written `SPECIES_OCTOROCK`, rewriting it to `SPECIES_OCTOROCKROCK`.
3. Same path for the camelcase pair: `"speciesName": "Octorock"` got matched and rewritten to `"Octorockrock"`.
4. Graphics symbols hit by two successive tokens ended up double-mangled; symbols hit by only one stayed single. That produced the mixed state visible in the rename preview.
5. With the JSON key now `SPECIES_OCTOROCKROCK`, the species loader re-encountered `SPECIES_OCTOROCK` in `include/constants/species.h` without a matching JSON entry and synthesized a blank default — the "first Pokemon blank" symptom.

**Fix — two defensive layers:**

1. **New `_multi_search_and_replace()` in `core/refactor_service.py`.** Scans each source file once with a single compiled regex built from `re.escape` + `|` alternation over all old keys, sorted longest-first. `re.sub` consumes each match and advances past the replacement, so a rewritten substring cannot be re-matched by any later token in the same sweep. Longer patterns win at overlap, so `SPECIES_OCTO` beats `OCTO` at the same position. `apply_pending()` species branch now calls this helper in place of the cascading per-token loop.
2. **Anchored key/path matching in `_rename_in_species_graphics_json()`.** Symbol keys like `gMonFrontPic_Pika` are now rewritten only when the key ends with `_<old_camel>`; path segments like `graphics/pokemon/pika/front.png` are only rewritten when a segment exactly equals `<old_slug>`. This closes the sibling-species collision case: renaming `Pika → Pikachu` while an unrelated `Pikachu` species already exists no longer mangles the sibling's `gMonFrontPic_Pikachu` into `gMonFrontPic_Pikachuchu`.

**Important caveat for already-damaged projects:** This fix prevents future corruption. It does NOT auto-repair projects that were damaged by the previous behavior. If a project already contains ghost entries (e.g. a duplicate `SPECIES_FAIRYQUEEN` at the tail of `species.json` alongside an orphaned `SPECIES_FAIRY_QUEE`), the recommended recovery is to `git checkout` to a commit before the broken renames and redo those renames on the patched build. Manual cleanup — deleting the tail duplicate from `species.json`, removing the orphan constant from `include/constants/species.h`, removing its row from `src/data/text/species_names.h`, and removing its block from `pokedex_entries.h` and the national dex — is possible but error-prone.

---

## Piano Roll — Tick Dialogs Unclamped to 16-bit Ceiling

All five tick-position input dialogs in the song structure panel (Edit Position, Section, Loop Back, Pattern Call, End Song) were clamping to `_total_ticks` — i.e. the last note tick in the song — so the user could not place a GOTO, loop boundary, or end-song marker past the final note even when the track legitimately needed one there. All five dialogs now cap at `65535`, the actual GBA 16-bit tick ceiling.

---

## Credits Editor — Dirty-State Wiring

The Credits editor now participates in the unified dirty-state system:

- Emits `modified` on every edit and `saved` on successful write, wired to the main window's sidebar dot and title-bar asterisk.
- Individual list rows tint amber (`#3d2e00`) when their entry has unsaved edits; rebuild-safe via a `_dirty_symbols` set that survives `_populate_list()` redraws.
- Drag-reorder tints every row (reorder affects the full list ordering).
- Delete marks the section dirty but does not falsely tint remaining rows — they weren't edited.
- Save clears the dirty set and removes amber tinting on all rows.

---

## Trainer Graphics — Thumbnail Palette

Trainer Graphics grid thumbnails were using a flat `QPixmap(path)` that ignored the separate `.pal` file and any unsaved RAM palette edits. Thumbnails now render through the palette bus (`ensure_trainer_palette_from_png` + `load_sprite_pixmap`), so the grid shows the correct colors from disk immediately on load and stays in sync with RAM edits.

---

## Files Changed

**Modified:**
- `core/app_info.py` — version bump.
- `core/refactor_service.py` — new `_multi_search_and_replace()`; `apply_pending()` species branch uses it; `_rename_in_species_graphics_json()` switched to anchored key/path matching.
- `ui/credits_editor.py` — `modified`/`saved` signals; amber row dirty tracking; `_dirty_symbols` set; delete/save handlers.
- `ui/piano_roll_structure.py` — five tick-position dialogs unclamped to 65535.
- `ui/trainer_graphics_tab.py` — grid thumbnails rendered through palette bus.
- `ui/unified_mainwindow.py` — Credits editor dirty signals wired to the unified dirty system.
