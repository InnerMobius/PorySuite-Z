## [2026-04-13] — Phase 10A: Tilemap Editor — 8bpp, UI Rework, Paint Fix

### Type
Fix / Feature

### Summary
Added full 8bpp (256-color) tilemap rendering support. The title screen logo and other 8bpp tilemaps now render correctly — auto-detects 4bpp vs 8bpp from PNG color table size. In 8bpp mode, the full 256-color palette is applied per GBA hardware behavior (palette bits ignored). Fixed `read_jasc_pal` truncating 256-entry .pal files to 16 colors. Fixed `from_pal_files` not splitting 256-color files into sub-palettes. Fixed `discover_assets` loading unrelated sibling .pal files when a 256-color name-match exists (was showing "19 palettes" for title screen instead of 16). Fixed paint tool not erasing tiles (QPainter alpha compositing let old tiles show through transparent pixels — switched to CompositionMode_Source). Reworked right panel layout: compact control bar (Paint/Pick/Tile/Pal/H/V all in one row), tile sheet as the dominant area, palette editor auto-sizes to only show loaded/used slots. Added visible Import .pal and Export .pal buttons with 16-color and 256-color modes. Palette header shows "256-color" or "16-color" mode. Status bar shows accurate palette info (slot count, .pal file count).

### Files Changed
- core/tilemap_data.py — `TileSheet.is_8bpp`, `PaletteSet.get_flat_colors()`, `build_flat_color_table()`, `_recolor_tile_8bpp()`, `render_tilemap()` 8bpp path, `from_pal_files()` 256-color splitting, `discover_assets()` smart 256-color .pal detection
- ui/tilemap_editor_tab.py — 8bpp rendering everywhere, CompositionMode_Source paint fix, compact control row, auto-sizing PaletteEditorWidget (only visible slots), Import/Export .pal buttons with 16/256 mode menus, palette slot picker dialog, dynamic palette header label
- ui/palette_utils.py — `read_jasc_pal()` reads full 256 entries, `write_jasc_pal()` writes 256 when >16 colors

---

## [2026-04-13] — Phase 10A: Tilemap Editor — Palette Fallback & Controls

### Type
Fix

### Summary
Fixed critical rendering regression where battle backgrounds and multi-palette tilemaps rendered as all-black. Fixed width/height spinner destroying tilemap data by truncating entries instead of re-wrapping rows. Root cause: `ensure_slots(16)` filled empty palette slots with black, then `_recolor_tile` used those black palettes instead of falling back to the PNG's actual colors. Most GBA 4bpp tile sheets have only 1 palette (16 colors) but tilemaps reference multiple slots because the game loads the same palette into different VRAM positions at runtime. Fix: `PaletteSet` now tracks which slots have real data (`_loaded_slots`). `_recolor_tile` falls back to palette 0 for unloaded slots. Verified all 10 battle terrain types render correctly. Also added Tile Offset spinbox, Palette Slots panel with manual loading, and palette source toggle.

### Files Changed
- core/tilemap_data.py — `_loaded_slots` tracking, `_recolor_tile` palette-0 fallback, `render_tilemap()` tile_offset param, `set_palette_at`/`ensure_slots`/`is_slot_loaded` methods
- ui/tilemap_editor_tab.py — Visual `PaletteEditorWidget` showing 16 palette slots as color swatch rows (red label = needed but missing, right-click any slot to import/export .pal or extract from PNG, "Export All" to directory), tile offset spinbox, palette source toggle, width/height re-wrap fix, _NoScrollCombo

---

## [2026-04-13] — Phase 10A: Tilemap Editor — Initial Build

### Type
Feature

### Summary
New Tilemap Editor toolbar page for opening, viewing, editing, and saving GBA `.bin` tilemap files. Opens any tilemap from the project's `graphics/` directory via file dialog. Auto-discovers matching tile sheet (`.png`) and palettes (`.pal`) from the same directory, parent directory, or `palettes/` subdirectory. When no exact name match exists, picks the largest PNG (most likely the main tile sheet). Renders tilemaps with correct palette recoloring, tile flips, and transparency (color index 0 = transparent). Supports 4bpp (16-color) and 8bpp (256-color) tile sheets.

**Editor features:**
- Tilemap canvas with zoom (1x-8x, Ctrl+scroll), grid overlay toggle
- Paint tool: select a tile from the tile sheet picker, click/drag to place on the tilemap
- Eyedropper tool: click tilemap to pick tile index, palette, and flip flags
- Tile picker panel: click tiles from the tile sheet, palette-aware preview
- Per-tile controls: palette selector (0-15), H-flip, V-flip checkboxes
- Tile sheet dropdown: switch between discovered PNGs
- Tilemap dimension override (W/H spinners) for re-interpreting non-standard sizes
- Save back to `.bin`

**Auto-discovery strategy:**
1. Same directory, same base name `.png` → best match (e.g. `kanto.bin` → `kanto.png`)
2. Parent directory same base name (for nested dirs like `firered/border_bg.bin` → `../border_bg.png`)
3. Fallback: largest PNG in directory (e.g. `kanto.bin` → `region_map.png` in same dir)
4. All `.pal` files from same dir, `palettes/` subdir, and parent dir
5. Palette extracted from tile sheet image if no `.pal` files found

### Files Changed
- core/tilemap_data.py — NEW: tilemap reader/writer, tile sheet loader, palette set, renderer, auto-discovery
- ui/tilemap_editor_tab.py — NEW: full tilemap editor with canvas, tile picker, paint/eyedropper tools
- ui/unified_mainwindow.py — Tilemap Editor toolbar button and stack page wiring
- res/icons/toolbar/tilesets.png — NEW: toolbar icon

---

## [2026-04-13] — Phase 9: Pokédex Habitat/Area Display — COMPLETE + Audit

### Type
Feature + Fix

### Summary
Added a "Wild Encounters" card to the Pokédex detail panel that shows every location where a species can be found in the wild, with encounter method, level range, and color-coded method dots. Built a new encounter data parser (`core/encounter_data.py`) that reads `wild_encounters.json` and cross-references map data and region map sections to build a reverse species→locations lookup. Fully compatible with custom maps, swapped regions, and non-vanilla hacks — no hardcoded map names or region data. Handles FireRed/LeafGreen dual encounter groups, multi-floor dungeon merging (e.g. Icefall Cave Entrance/1F/B1F/Back → one entry), and fishing rod sub-groups (Old Rod/Good Rod/Super Rod). Species with no wild encounters show "Not found in the wild." Re-parses on F5 refresh.

**Audit findings (2 bugs fixed):** (1) `_camel_to_spaced()` broke floor designations — "B1F" became "B1 F", "SSAnne" became "S S Anne". Rewrote with regex to handle CamelCase, acronyms, and floor numbers correctly. (2) `slot_count` doubled by FR/LG variant merging — single-phase `+=` summed across versions. Replaced with two-phase merge: Phase 1 sums within each encounter table (keyed by `base_label`), Phase 2 takes `max` across floors/versions. Verified merge matches game behavior: game's Pokedex area screen groups by MAPSEC (region_map_section), and all floors of a dungeon share the same MAPSEC.

### Files Changed
- core/encounter_data.py — NEW: encounter parser with reverse species lookup, map name resolution, fishing rod splitting, two-phase merge, _camel_to_spaced regex rewrite
- ui/pokedex_detail_panel.py — Wild Encounters card with color-coded method dots (Grass/Surfing/Rock Smash/Old Rod/Good Rod/Super Rod)
- ui/mainwindow.py — Encounter DB loading in load_data(), wired to both detail panel update paths

---

## [2026-04-13] — Abilities Editor: Second Audit — 6 Codegen Bugs Fixed

### Type
Fix

### Summary
Deep code audit traced every `generate_battle_code()` path and `detect_battle_effect()` path. Found and fixed 6 bugs: (1) Cute Charm generated confusion code instead of infatuation — now produces correct `STATUS2_INFATUATED_WITH` code. (2) Serene Grace wrote to const ROM data `gBattleMoves[]` — now uses a local variable. (3) `block_specific_stat` used non-existent `STAT_CHANGE_IS_NEGATIVE` constant — rewrote to match pokefirered's actual `ChangeStatBuffs()` pattern. (4) `weather_speed` targeted `battle_util.c` but speed doubling is in `battle_main.c`. (5) `pressure` targeted `battle_main.c` but `PressurePPLose()` is in `battle_util.c`. (6) Keen Eye/Hyper Cutter detected as generic `block_stat_reduction` instead of `block_specific_stat` — added `statId ==` check before generic pattern. All verified: 20/20 key detection tests pass, all codegen produces valid pokefirered-compatible C.

### Files Changed
- core/ability_effect_templates.py — 6 codegen fixes, 1 detection order fix

---

## [2026-04-09] — Abilities Editor: 100% Coverage (52 Templates, 74/74 Detection)

### Type
Feature + Fix

### Summary
Expanded the abilities editor from 31 to **52 battle templates** — every single vanilla pokefirered ability (74/74) now has an editable template instead of showing "no editable template." Added 21 new templates: Weather Suppress (Cloud Nine/Air Lock), Shed Skin (random status cure), Truant, Sound Block (Soundproof), Color Change, Synchronize (status pass), Suction Cups (block forced switch), Sticky Hold (block item theft), Shield Dust (block secondary effects), Lightning Rod (redirect type), Serene Grace (double effect chance), Hustle (attack boost + accuracy penalty), Marvel Scale (status defense boost), Early Bird (faster sleep), Liquid Ooze (drain reversal), Plus/Minus (combo boost), Damp (block explosion), Contact Flinch (Stench Gen5+), Trace (copy ability), Forecast (form change), Block Specific Stat (Keen Eye/Hyper Cutter). Fixed detection false positives from context bleed in pokemon.c (Guts/Hustle/Marvel Scale/Plus/Minus are all within 10 lines). Added name-based fallbacks for abilities implemented in assembly scripts rather than C code.

### What Changed
- **Ability Effect Templates** (`core/ability_effect_templates.py`): 21 new battle templates with full code generation. New parameter choice lists (SHED_SKIN_CHANCE, FULL_STAT, REDIRECT_TYPE, COMBO_PARTNER, BOOST_STAT). Detection rewritten with unified pokemon.c section using per-line tight context. Name-based fallback detection for abilities in assembly files. Case-block detection expanded for Shed Skin, Synchronize, Color Change, Sound Block, Damp, Forecast, Trace, Truant.

### Files Changed
- core/ability_effect_templates.py — 21 new templates, detection for all 74 vanilla abilities

---

## [2026-04-09] — Abilities Editor: Full Audit + Detection False Positive Fixes

### Type
Fix

### Summary
Comprehensive audit of all 31 battle templates + 8 field templates. Fixed 7 code generation bugs: `intimitatedMon` typo (4 occurrences), Trick Room/Tailwind using non-existent Gen 4+ constants (rewritten for gBattleStruct), incomplete stat→variable mapping in stat_double (only ATK→"attack", all others defaulted to "spAttack"), intimidate template ignoring stat parameter, save pipeline missing parameter-only changes, new abilities not synced to data layer, type_change_boost damage applied at wrong timing. Fixed 6 detection false positives: Compound Eyes bleeding into Sand Veil (radius-based context too wide), Swift Swim detected as prevent_escape, Shadow Tag/Arena Trap detected as type_trap(FLYING), Chlorophyll detected as weather_speed with wrong weather, Thick Fat undetected (regex used `moveType` but pokemon.c uses `type`). Replaced `_get_nearby_block(200)` with tight per-line scanning (±5 lines per occurrence). Verified all 30+ vanilla abilities now correctly detected.

### What Changed
- **Ability Effect Templates** (`core/ability_effect_templates.py`): Rewrote `detect_battle_effect()` to use per-line context. Fixed `intimitatedMon` → `intimidatedMon`. Fixed stat_double mapping. Fixed intimidate stat parameter. Fixed type_change_boost split into two files. Fixed Trick Room/Tailwind constants. Fixed Thick Fat regex.
- **Abilities Tab** (`ui/abilities_tab_widget.py`): Fixed save pipeline parameter comparison. Added known-effect fallback label cleanup in clear().
- **Main Window** (`ui/mainwindow.py`): Added add_ability()/delete handling for new/removed abilities in save pipeline.

### Files Changed
- core/ability_effect_templates.py — detection rewrite, 7 code generation fixes, 6 false positive fixes
- ui/abilities_tab_widget.py — save pipeline param comparison fix
- ui/mainwindow.py — new ability sync to data layer

---

## [2026-04-09] — Abilities Editor: Advanced Templates (Pixilate, Dual Intimidate, Trick Room)

### Type
Feature

### Summary
Added 4 new advanced ability templates for creating abilities that don't exist in vanilla pokefirered: (1) **Move Type Change + Boost** (Pixilate/Aerilate/Refrigerate) — converts moves of one type to another with configurable power boost. User picks source type, target type, and boost %. Uses `dynamicMoveType` mechanism. (2) **Dual Stat Intimidate** — lowers TWO foe stats on switch-in (e.g. "Scare" lowers Attack + Sp. Attack). (3) **Switch-In Field Effect** — sets Trick Room or Tailwind on switch-in, like how Drizzle sets rain. (4) **Multi-Type Resist** — halves damage from two types (like Thick Fat but user picks both types). Also added `POWER_BOOST_CHOICES` and `DUAL_STAT_CHOICES` parameter lists. Detection expanded to find these patterns in existing C source if someone already added them manually.

### What Changed
- **Ability Effect Templates** (`core/ability_effect_templates.py`): 4 new battle templates (type_change_boost, intimidate_dual, switchin_field_effect, multi_type_resist). New parameter choice lists (POWER_BOOST_CHOICES, DUAL_STAT_CHOICES). Code generation for all 4. Detection patterns for all 4 in existing source code.
- **Abilities Tab** (`ui/abilities_tab_widget.py`): Extended battle categories dict for new template descriptions.

### Files Changed
- core/ability_effect_templates.py — 4 new templates + code gen + detection
- ui/abilities_tab_widget.py — extended categories

---

## [2026-04-09] — Abilities Detection Overhaul + Dirty Flags + Starters + Rematch

### Type
Fix + Feature

### Summary
**Abilities editor completely overhauled.** (1) Fixed two-layer initialization bug: `self.local_util` was created 230 lines AFTER the abilities editor needed it, so `repo_root()` always failed silently and no ability ever had effects detected. Moved initialization to right after `self.project_info`. (2) **Expanded detection from 13 to 26+ battle templates.** Previously only detected abilities with `case ABILITY_XXX:` blocks in battle_util.c. Now also scans battle_script_commands.c and pokemon.c for inline checks — covers Sand Veil, Sturdy, Battle Armor, Huge Power, Thick Fat, Clear Body, Inner Focus, Compound Eyes, Guts, Swift Swim, Shadow Tag, Wonder Guard, Rock Head, Pressure, Natural Cure, and more. (3) **Added "Known Effect" fallback labels.** When detection can't match an editable template, but the ability has a known effect in the hardcoded category database, a green info label shows what the ability does instead of "(none)". (4) All new templates also available when creating new abilities — users can create abilities with any of these effect patterns. Also includes: structural dirty flag fix, starter ability combos, VS Seeker rematch button, instrument loop dirty marking.

### What Changed
- **Ability Effect Templates** (`core/ability_effect_templates.py`): Added `_get_nearby_block()` helper for inline pattern scanning. Expanded `detect_battle_effect()` with inline detection for 14 new patterns (ohko_prevention, evasion_weather, stat_double, type_resist_halve, block_stat_reduction, block_flinch, accuracy_boost, guts_boost, weather_speed, prevent_escape, natural_cure, pressure, wonder_guard, recoil_immunity). Added code generation for all new templates.
- **Abilities Tab** (`ui/abilities_tab_widget.py`): Added `lbl_battle_known` and `lbl_field_known` fallback labels that show the hardcoded effect description when detection returns no editable template. Visible as green info boxes below the dropdown.
- **Main Window** (`ui/mainwindow.py`): Moved `self.local_util = LocalUtil(self.project_info)` to right after `self.project_info` is set — fixes initialization order that broke ALL ability detection. Structural dirty-marking loop, starter ability combos.
- **Unified Main Window** (`ui/unified_mainwindow.py`): `_on_child_modified()` suppress flag checks.
- **Instruments Tab** (`ui/instruments_tab.py`): `_mark_loop_dirty()` in loop handlers.
- **Trainers Tab** (`ui/trainers_tab_widget.py`): "Add to VS Seeker Rematch Table" button.

### Files Changed
- core/ability_effect_templates.py — 14 new battle templates + inline detection + code generation
- ui/abilities_tab_widget.py — known-effect fallback labels, clear() cleanup
- ui/mainwindow.py — local_util init order fix, structural dirty loop, starter abilities
- ui/unified_mainwindow.py — dirty suppress checks
- ui/instruments_tab.py — loop dirty marking
- ui/trainers_tab_widget.py — rematch table button

---

## [2026-04-09] — Phantom Dirty Flag Fix + .s Timestamp Protection (register_song)

### Type
Fix

### Summary
Two bugs fixed: (1) **Phantom "Unsaved Changes" dialog** appeared on close/refresh even after saving. Two root causes: (a) PorySuite's deferred items loader fired after the dirty-flag override, re-dirtying on startup — fixed with deferred dirty clear 200ms after load. (b) Save pipeline's `_refresh_gfx_combo()` repopulated EVENTide's gfx dropdown, firing `currentIndexChanged` → `_mark_dirty()`, and EVENTide's dirty flag was only cleared if a map was loaded — fixed with `blockSignals()` and unconditional dirty clear. (2) **mus_evil.s repeatedly wiped to 0-track skeleton** by mid2agb. Root cause: `register_song()` in `midi_importer.py` appends a line to midi.cfg but did NOT touch existing .s files afterward. This made every .s file stale relative to midi.cfg, so the next `make` ran mid2agb on all songs — and songs with placeholder .mid files got wiped to empty garbage. Fixed by adding an .s-touching loop after the midi.cfg append. Also added defense-in-depth: `_on_make()` touches all .s files before delegating to build.

### What Changed
- **Unified Main Window** (`ui/unified_mainwindow.py`): Deferred dirty clear after load/refresh. EVENTide `setWindowModified(False)` now runs unconditionally after save. `_on_make()` touches .s files before build.
- **Event Editor** (`eventide/ui/event_editor_tab.py`): `_refresh_gfx_combo()` blocks signals on gfx_combo during clear/repopulate to prevent false dirty marking.
- **MIDI Importer** (`core/sound/midi_importer.py`): `register_song()` now touches ALL .s files after appending to midi.cfg — the missing third layer of timestamp protection that caused recurring mus_evil wipes.

### Files Changed
- ui/unified_mainwindow.py — deferred dirty clear, unconditional EVENTide dirty clear, pre-build .s touch
- eventide/ui/event_editor_tab.py — blockSignals in _refresh_gfx_combo
- core/sound/midi_importer.py — register_song() .s-touching loop after midi.cfg append

---

## [2026-04-08] — Instrument Import UX Overhaul + Save Fix

### Type
Fix + Feature

### Summary
Three major fixes to instrument import workflow: (1) Import buttons moved from the hidden details panel to the left panel below the instrument list — always visible when a project is loaded. (2) Importing a WAV or .psinst now actually creates a usable instrument: if a DirectSound slot is selected it assigns there; otherwise asks which voicegroup and replaces a filler slot. No more orphan .bin files. (3) Instrument edits from the Instruments tab now properly mark voicegroups dirty so Save actually writes them to disk. Previously, editing ADSR/sample/pan in the Instruments tab would never persist — the voicegroups tab didn't know anything changed.

### What Changed
- **Instruments Tab** (`ui/instruments_tab.py`): Moved Import WAV + Import Instrument buttons to left panel. Added `_assign_sample_to_slot()` helper that finds a filler slot in a user-chosen voicegroup. `_apply_to_all_copies()` now calls `mark_voicegroups_dirty()` on the voicegroups tab. Added `_voicegroups_tab_ref` cross-link.
- **Voicegroups Tab** (`ui/voicegroups_tab.py`): Added `mark_voicegroups_dirty(names)` public method for external dirty tracking.
- **Sound Editor Tab** (`ui/sound_editor_tab.py`): Wires `_instruments_tab._voicegroups_tab_ref` after both tabs are created.

### Files Changed
- ui/instruments_tab.py — button relocation, _assign_sample_to_slot, dirty tracking
- ui/voicegroups_tab.py — mark_voicegroups_dirty()
- ui/sound_editor_tab.py — cross-link wiring

---

## [2026-04-08] — Instrument Export/Import (.psinst Presets)

### Type
Feature

### Summary
Added Export Instrument and Import Instrument buttons to the Instruments tab. Users can save a complete instrument configuration (sample audio, loop settings, ADSR envelope, base key, pan) as a .psinst file and load it into another project. The .psinst format is a zip containing a JSON manifest and the raw .bin sample file. On import, the .bin is copied to the target project's `sound/direct_sound_samples/`, registered in `direct_sound_data.inc`, and all instrument settings are applied to the selected slot. Handles name conflicts gracefully (offers to reuse existing samples).

### What Changed
- **Instruments Tab** (`ui/instruments_tab.py`): Added `_on_export_instrument()` and `_on_import_instrument()` handler methods. Export reads instrument settings + .bin file into a zip. Import extracts the .bin, registers it, and applies all settings via `_apply_to_all_copies()`.

### Files Changed
- ui/instruments_tab.py — export/import handlers for .psinst format

---

## [2026-04-08] — Sample Loop Controls in Instruments Tab

### Type
Feature

### Summary
Added loop toggle and loop point editor to the Instruments tab. Users can now enable/disable sample looping and set the exact loop start point for any DirectSound instrument. Essential for sustained instruments (organ, strings, winds) — without looping, samples play once and stop, so long notes go silent. The controls update the .bin file header directly (status flag 0x4000 for loop enable, loopStart byte offset).

### What Changed
- **Instruments Tab** (`ui/instruments_tab.py`): Added QCheckBox "Loop" toggle, QSpinBox loop start point, and percentage label. Added `_on_loop_toggled()` and `_on_loop_start_changed()` handler methods that rewrite the .bin file via `_write_gba_bin()`. Display code populates controls from sample header on instrument selection. Added QCheckBox to imports.

### Files Changed
- ui/instruments_tab.py — loop toggle UI, handlers, QCheckBox import

---

## [2026-04-08] — Protect .s Files from mid2agb Overwrite on Build

### Type
Fix (critical data loss)

### Summary
Running `make` after editing a song through the piano roll could silently wipe the .s file. pokefirered's Makefile has a `%.s: %.mid midi.cfg` rule — if EITHER the .mid OR midi.cfg is newer than the .s, mid2agb regenerates the .s. For tool-edited songs, the .mid is a 26-byte placeholder, so mid2agb produces an empty 0-track skeleton. The critical trigger was **midi.cfg as a dependency** — any tool write to midi.cfg (song delete, rename, import, cleanup) made ALL .s files stale, causing the next build to overwrite every song. Fixed with two layers: (1) `write_midi_cfg()` touches all .s files after rewriting midi.cfg, and (2) every .s-writing path backdates the .mid by 2 seconds.

### What Changed
- **Song Table Manager** (`core/sound/song_table_manager.py`): `write_midi_cfg()` now touches all .s files in the midi directory after writing, so they're newer than midi.cfg.
- **Song Writer** (`core/sound/song_writer.py`): `save_song_file()` backdates the .mid after writing the .s.
- **MIDI Import Dialog** (`ui/dialogs/midi_import_dialog.py`): `_postprocess_structure()` no_loop and custom modes backdate .mid. Added `_backdate_mid()` helper.
- **S File Import** (`ui/dialogs/s_file_import_dialog.py`): Strengthened — now backdates .mid by 2 seconds.
- **Song Replace** (`ui/sound_editor_tab.py`): Same — backdates .mid after writing .s.

### Files Changed
- core/sound/song_table_manager.py — touch all .s after write_midi_cfg()
- core/sound/song_writer.py — backdate .mid in save_song_file()
- ui/dialogs/midi_import_dialog.py — backdate .mid in post-processor + _backdate_mid()
- ui/dialogs/s_file_import_dialog.py — backdate .mid after import
- ui/sound_editor_tab.py — backdate .mid after replace

---

## [2026-04-08] — Fix Orphaned Song Registrations + Auto-Cleanup

### Type
Fix

### Summary
Deleting songs or having failed MIDI imports could leave orphaned entries in `song_table.inc`, `songs.h`, and `midi.cfg` — .s file gone but registrations still present, causing "undefined reference" build errors. Two fixes: (1) failed-import cleanup now deletes the correct file (was using original filename instead of label-based name). (2) New `cleanup_orphaned_songs()` function runs automatically when the Sound Editor loads — finds MUS_* entries with no .s file and removes them from all three config files.

### What Changed
- **MIDI Importer** (`core/sound/midi_importer.py`): Fixed failed-import cleanup to use `label + ".mid"` instead of `os.path.basename(midi_path)`. The file is saved as the label name, not the original — cleanup was deleting the wrong path.
- **Song Table Manager** (`core/sound/song_table_manager.py`): Added `cleanup_orphaned_songs()` — scans for MUS_* entries whose .s files are missing, removes them from all three config files, re-indexes remaining entries, and cleans up .mid/.o artifacts.
- **Sound Editor** (`ui/sound_editor_tab.py`): `load_project()` now runs `cleanup_orphaned_songs()` on every load. Orphans are cleaned silently with a log message.

### Files Changed
- core/sound/midi_importer.py — failed-import cleanup filename fix
- core/sound/song_table_manager.py — cleanup_orphaned_songs() function
- ui/sound_editor_tab.py — auto-cleanup on load

---

## [2026-04-08] — Default MIDI Import to "No Loop" Mode (Strip Labels)

### Type
Fix

### Summary
MIDI import defaulted to "Automatic" mode which kept mid2agb's internal PATT/PEND subroutine labels. Users clicking through without changing the mode got cluttered Song Structure panels full of pattern labels they didn't create. Changed the default to "No Loop (clean)" — the post-processor strips all PATT/PEND/GOTO/labels and produces clean linear tracks.

### What Changed
- **MIDI Import Dialog** (`ui/dialogs/midi_import_dialog.py`): Default `_structure_mode` changed from `'automatic'` to `'no_loop'`. Structure page now auto-applies the No Loop preset on first visit. Mode label updated to "No Loop (clean)" with description.

### Files Changed
- ui/dialogs/midi_import_dialog.py — default mode + auto-preset

---

## [2026-04-08] — Fix GM Voicegroup Missing Drum/Percussion Samples

### Type
Fix

### Summary
"Generate GM" voicegroup was missing all drum kit sounds (kick, snare, hi-hat, triangle, cymbals, toms, clap, cowbell, etc.). The mapping table only had ethnic percussion, and DirectSound samples that lost their GM slot competition were silently dropped.

### What Changed
- **GM Voicegroup Generator** (`core/sound/gm_voicegroup.py`): Added 21 drum kit samples to `_SAMPLE_TO_GM` mapping (kick→117, snare→118, triangle→114, clap→119, etc.). Changed the fill pass to include unplaced DirectSound samples before square/noise variants. Overflow past 128 slots limited to DirectSound and programmable wave instruments only.

### Files Changed
- core/sound/gm_voicegroup.py — drum mapping + unplaced DS sample recovery

---

## [2026-04-08] — Fix MIDI Import for Type 0 MIDIs and Remaining Meta Events

### Type
Fix

### Summary
castle.mid and castle2.mid failed to import because they are Type 0 MIDIs (single track, all channels merged) — mid2agb expects Type 1 (one track per channel). Also changed the meta event cleaning from a blocklist to an allowlist — the blocklist missed `track_name` events which also caused "failed to read event text" errors.

### What Changed
- **MIDI Importer** (`core/sound/midi_importer.py`): Changed meta event stripping from blocklist (`_BAD_META`) to allowlist (`_KEEP_META` = set_tempo, time_signature, end_of_track). Added `_split_type0_to_type1()` function that converts Type 0 MIDIs to Type 1 by splitting channel messages into separate tracks with a conductor track for global tempo/time sig events.

### Files Changed
- core/sound/midi_importer.py — allowlist meta cleaning + Type 0→1 conversion

---

## [2026-04-08] — MIDI Import "No Loop" Properly Strips All Structure

### Type
Fix

### Summary
MIDI import "No Loop" mode now fully linearizes the .s file — removes all PATT/PEND subroutine calls, GOTO loops, and internal pattern labels generated by mid2agb. Previously it only stripped GOTO commands, leaving PATT/PEND structure and internal labels (like `mus_name_3_007`) intact. The fix uses the same parse→flatten→rewrite pipeline as the piano roll save.

### What Changed
- **MIDI Import Dialog** (`ui/dialogs/midi_import_dialog.py`): Rewrote `_postprocess_structure()` for `no_loop` mode. Now parses the .s file, flattens all tracks (expanding PATT subroutines), deduplicates control events, and rewrites as clean linear tracks ending with FINE.

### Files Changed
- ui/dialogs/midi_import_dialog.py — no_loop post-processor rewrite

---

## [2026-04-08] — Fix Loop Tick Drift and Label Name Revert

### Type
Fix

### Summary
Two save/reload bugs fixed: (1) Loop section labels drifted by 2 ticks on every save/reload cycle (192→190) because WAIT commands were emitted after the label line instead of before it. (2) User's label name (e.g. "intro") was replaced by auto-generated `mus_evil_1_B1` because the save pipeline never consulted the Song Structure panel.

### What Changed
- **Song Writer** (`core/sound/song_writer.py`): Moved gap WAIT calculation above the LABEL check in the timeline emit loop. WAITs now precede the label in the output so the parser reads the correct tick position on reload.
- **Piano Roll Window** (`ui/piano_roll_window.py`): `_sync_notes_to_song()` now reads the loop label from the Song Structure panel via `get_loop_label()` first, falling back to parsed file label, then auto-generated `_B1` only as last resort.
- **Song Structure Panel** (`ui/piano_roll_structure.py`): Added `get_loop_label()` method that returns the user's chosen section name from the GOTO target.

### Files Changed
- core/sound/song_writer.py — WAIT before LABEL in emit loop
- ui/piano_roll_window.py — structure panel label priority in save
- ui/piano_roll_structure.py — get_loop_label() method

---

## [2026-04-08] — Piano Roll UX: Scroll Direction, Middle-Click Zoom, Song Structure Cleanup

### Type
Fix / Feature

### Summary
Three piano roll fixes: (1) Scroll wheel now scrolls horizontally through the timeline by default instead of vertically. (2) Middle-click drag zooms horizontally, anchored to cursor position. (3) Song Structure "Loop Back" dropdown no longer shows internal PATT subroutine labels — only real user-created sections.

### What Changed
- **Piano Roll Widget** (`ui/piano_roll_widget.py`): Installed event filter on scroll area + viewport to intercept wheel events. Plain scroll = horizontal, Shift+scroll = vertical, Ctrl+scroll = zoom. Added `_Mode.ZOOM_H` for middle-click drag zoom with cursor anchoring via `zoom_changed` signal and viewport-relative scroll adjustment.
- **Song Structure Panel** (`ui/piano_roll_structure.py`): `load_from_song()` now collects PATT targets first and excludes them from the section label list. Only genuine section labels appear in the Loop Back / Pattern Call dropdowns.

### Files Changed
- ui/piano_roll_widget.py — scroll direction fix, middle-click zoom, zoom_changed signal, eventFilter
- ui/piano_roll_structure.py — PATT target filtering in label collection

---

## [2026-04-08] — Fix Control Event Explosion from PATT Flattening

### Type
Fix

### Summary
Piano roll save was exploding control events (PAN, BEND) when saving songs that use PATT subroutines. A song with 18 PAN commands became 1,829 after save because PATT flattening duplicated every control event per subroutine call, and these duplicates were never filtered. Added deduplication at both load time and save time. Restored MUS_EVIL from MUS_TEST.

### What Changed
- **Piano Roll Widget** (`ui/piano_roll_widget.py`): `load_song_data()` now deduplicates control events by (tick, type, value) after PATT flattening. Prevents duplicated PAN/BEND/VOL events from entering the canvas.
- **Song Writer** (`core/sound/song_writer.py`): `notes_to_track_commands()` now deduplicates incoming control events by (tick, cmd, value) before building the timeline. Belt-and-suspenders defense against control event explosion.
- **MUS_EVIL** (`porysuite/pokefirered/sound/songs/midi/mus_evil.s`): Restored from MUS_TEST with label replacement. Identical music content, no loop structure (user will add loops manually).

### Files Changed
- ui/piano_roll_widget.py — control event dedup at load time
- core/sound/song_writer.py — control event dedup at save time
- porysuite/pokefirered/sound/songs/midi/mus_evil.s — restored from mus_test

---

## [2026-04-08] — Piano Roll Loop Playback, Save Persistence, Dirty Flag, UX Polish

### Type
Fix / UX

### Summary
Fixed piano roll loop playback (cursor ignored loop region), loop save not persisting through save/reload, phantom dirty flag on close after Sound Editor work, and several piano roll UX improvements (double-click to place notes, Ctrl+Z undo, default snap 1/16, sidebar click behavior).

### What Changed
- **Realtime Sequencer** (`core/sound/realtime_sequencer.py`): Loop wrap was working but using wrong loop_end value (max across all tracks = 1343 instead of track 0's 1151). Debug logging added (set_loop, play, periodic tick, loop wrap, callback errors) for future diagnostics.
- **Piano Roll Window** (`ui/piano_roll_window.py`): After loading song data and Song Structure panel, the canvas loop region is now overridden with track 0's `get_flattened_loop_info()` values — this is the structural authority. Previously the canvas used the max across ALL tracks, producing a loop_end much later than where audible notes end. Git pull / F5 refresh now reloads Sound Editor and Credits Editor data.
- **Song Writer** (`core/sound/song_writer.py`): Loop GOTO is now inserted into the timeline at the correct tick position (not appended after all notes). The emit loop stops when it hits a GOTO or FINE — notes/events past the loop end are excluded. Previously, if the user set loop back to tick 960 but notes extended to tick 1151, the GOTO was placed at 1151 instead of 960, so the edit was lost on reload.
- **Unified Main Window** (`ui/unified_mainwindow.py`): `setWindowModified(False)` now always runs after save, not just when a sub-component reported changes. Previously the Sound Editor could emit `modified()` for actions already persisted to disk (e.g. .s import), leaving a phantom dirty flag that triggered "Unsaved Changes" on close even after saving.
- **.s File Import** (`ui/dialogs/s_file_import_dialog.py`): Reimport/overwrite allowed when file already exists — shows amber confirmation instead of blocking red error.
- **Piano Roll Widget** (`ui/piano_roll_widget.py`): Default snap changed from 1/4 to 1/16. Default duration changed from 24 to 6. Single-click on empty space deselects instead of placing a note (double-click places). Ctrl+Z undo stack added. Ctrl+E opens note properties. Loop start aggregation changed from min to max (longest intro).
- **Piano Roll Window** (`ui/piano_roll_window.py`): Sidebar track click now sets active track instead of filtering visibility. Snap combo default set to 1/16.

### Files Changed
- core/sound/realtime_sequencer.py — loop wrap debug logging, float midi_note keysplit fix
- core/sound/song_writer.py — GOTO timeline insertion, emit loop stops at GOTO/FINE
- core/sound/track_renderer.py — fractional BEND pitch
- core/sound/audio_engine.py — float midi_note, keysplit int() fix
- ui/unified_mainwindow.py — always clear dirty after save, Sound Editor refresh on git pull
- ui/piano_roll_window.py — track 0 loop authority, sidebar track selection, snap default
- ui/piano_roll_widget.py — double-click note placement, undo, snap/duration defaults, loop_start max
- ui/dialogs/s_file_import_dialog.py — reimport/overwrite support

---

## [2026-04-07] — Fractional Pitch Bending (GBA MidiKeyToFreq Interpolation)

### Type
Fix / Audio Quality

### Summary
Both audio renderers (Songs tab and Piano Roll) now produce smooth sub-semitone pitch bends matching real GBA hardware. Previously all pitch bends were quantized to whole semitones due to missing fineAdjust interpolation in MidiKeyToFreq and integer truncation in the renderers. Also fixed the Piano Roll's pan formula to match the GBA's linear crossfade.

### What Changed
- **Audio Engine** (`core/sound/audio_engine.py`): `_midi_key_to_freq()` now accepts fractional MIDI keys and interpolates between adjacent GBA scale table entries — matching the hardware's `fineAdjust` parameter. All render functions (`render_directsound`, `render_square_wave`, `render_programmable_wave`, `render_noise`, `render_instrument`) accept float midi_note.
- **Track Renderer** (`core/sound/track_renderer.py`): BEND pitch calculation now passes fractional midi_note (float) to the audio engine instead of truncating to int. A BEND of 0.7 semitones actually produces a 0.7 semitone pitch shift instead of rounding to 0 or 1.
- **Realtime Sequencer** (`core/sound/realtime_sequencer.py`): BEND offset passed as float instead of rounded integer. Pan formula changed from simple `pan/127` to GBA's linear crossfade `(127-signed_pan)/255, (signed_pan+128)/255` for consistency with the track renderer.

### Files Changed
- core/sound/audio_engine.py — fractional pitch interpolation in MidiKeyToFreq, float midi_note throughout
- core/sound/track_renderer.py — fractional BEND pitch (no int truncation)
- core/sound/realtime_sequencer.py — fractional BEND pitch, GBA pan formula
- docs/SOUND_ENGINE_DEBUG_LOG.md — documented fix #15

---

## [2026-04-08] — Piano Roll Save Pipeline Fixes, BEND Editing, MIDI Import Dropdowns

### Type
Fix / Feature

### Summary
Three critical piano roll save bugs fixed: PATT subroutine corruption (songs with pattern calls were destroyed on save), VOL/TEMPO double-evaluation (volume halved every save cycle), and BEND state incorrectly reset on loop wrap. New Note Properties dialog for editing pitch bend and control events per-note. MIDI import instrument mapping page upgraded from number spinners to named dropdowns.

### What Changed
- **Song Writer** (`core/sound/song_writer.py`): PATT/PEND subroutines are now stripped on save and written as fully linear tracks — you can't unflatten edited notes back into subroutines, so the save no longer tries to. Added `_raw_vol()` and `_raw_tempo()` reverse-evaluation helpers so VOL and TEMPO values round-trip correctly (parser evaluates `127*mvl/mxv` to 89, writer now recovers 127 instead of writing `89*mvl/mxv`). All five VOL write sites and two TEMPO write sites fixed.
- **Realtime Sequencer** (`core/sound/realtime_sequencer.py`): Removed incorrect BEND reset to 0.0 on loop wrap. The real GBA M4A engine carries BEND state through GOTO loops — the Songs tab renderer already did this correctly.
- **Piano Roll Widget** (`ui/piano_roll_widget.py`): Right-click on a note now shows a context menu (Edit Note Properties / Delete) instead of instant-deleting. New `NotePropertiesDialog` class: shows note info summary, editable table of control events (BEND, BENDR, VOL, PAN) at that note's position with Add/Delete buttons. Edits update the canvas's control events, push to sequencer for immediate playback, and are included in the save pipeline.
- **Piano Roll Window** (`ui/piano_roll_window.py`): Save pipeline now uses canvas-edited control events (from Note Properties dialog) instead of only the original file's events. BEND/BENDR/VOL/PAN events from the canvas replace the originals for each track. Debug logging added to `voice_debug.log` for instrument/volume/pan values during save.
- **MIDI Import Dialog** (`ui/dialogs/midi_import_dialog.py`): Instrument mapping page replaced QSpinBox with QComboBox showing "0: Acoustic Grand Piano" style labels, color-coded (red for empty/filler, green for real instruments). Auto-match and voice remap collection updated.

### Files Changed
- core/sound/song_writer.py — PATT stripping, VOL/TEMPO reverse evaluation
- core/sound/realtime_sequencer.py — removed BEND reset on loop wrap
- ui/piano_roll_widget.py — right-click context menu, NotePropertiesDialog
- ui/piano_roll_window.py — canvas control events in save, debug logging
- ui/dialogs/midi_import_dialog.py — instrument mapping dropdowns
- docs/BUGS.md — created, tracks all known bugs with status

---

## [2026-04-07] — Song Writer Optimizations, Song Deletion Fix, Piano Roll UX

### Type
Fix / Optimization / UX

### Summary
Three song writer optimizations reduce .s file output size and fix a silent note truncation bug. Song deletion now properly cleans up all related files. Piano roll track sidebar widened so Mute/Solo buttons aren't hidden behind scrollbar. BPM spinbox widened for readability. Song Structure panel defaults to cursor position for all add operations.

### What Changed
- **Song Writer** (`core/sound/song_writer.py`): Notes longer than 96 ticks now correctly generate TIE + EOT commands instead of being silently truncated to N96 (was a bug — long notes got cut short). Redundant control commands (VOL/PAN/MOD/BEND/etc. set to the same value twice) are now filtered out in both `notes_to_track_commands()` and `_write_track_linear()`. New `_format_tie()` and `_format_eot()` helpers. Both `_write_track_raw` and `_write_track_linear` handle TIE/EOT command types. Assembly output verified with arm-none-eabi-as on 4 test songs.
- **Song Deletion** (`core/sound/song_table_manager.py`): `delete_song()` now deletes `.mid` files alongside `.s` files (previously left orphan .mid that caused build failures). Also cleans up `.o` build artifacts in both `build/firered_modern` and `build/firered` directories. `write_song_table()` now includes the required `dummy_song_header` footer block (was missing — could corrupt song_table.inc on any write).
- **Piano Roll Tracks** (`ui/piano_roll_tracks.py`): Sidebar widened from 220px to 240px so Mute (M) and Solo (S) buttons aren't clipped behind the vertical scrollbar. Track row right margin tightened (6→4px).
- **Piano Roll Window** (`ui/piano_roll_window.py`): BPM spinbox widened from 65px to 85px for readability. Cursor tick position now forwarded to Song Structure panel via `set_cursor_tick()` on ruler click and during playback.
- **Song Structure Panel** (`ui/piano_roll_structure.py`): All four "Add" dialogs (Section, Loop Back, Pattern Call, End Song) now default to the current cursor position instead of start/end of song.
- **Build fix**: Cleaned up stale `mus_graveyard` entries from song_table.inc and midi.cfg in test project (orphan from incomplete song deletion).

### Files Changed
- core/sound/song_writer.py — TIE/EOT generation, redundant control filtering, long note fix
- core/sound/song_table_manager.py — delete .mid + .o files, write dummy_song_header footer
- ui/piano_roll_tracks.py — sidebar width 220→240, right margin 6→4
- ui/piano_roll_window.py — BPM spinbox width 65→85, cursor tick forwarding to structure panel
- ui/piano_roll_structure.py — all add dialogs default to cursor position

---

## [2026-04-07] — Generate GM Fix, Song Export/Replace, GM Slot Trimming

### Type
Fix / Feature

### Summary
Fixed the Generate GM button crash (broken by a renamed dict key in the coverage report). Added "Export .s File..." and "Replace with .s File..." to the song right-click context menu — users can now back up a song and replace it with music from another .s file while keeping the same constant and registration. GM voicegroup generator no longer pads to 128 slots — it stops at the last real instrument, so a voicegroup with instruments only up to slot 80 will have 81 slots instead of 128.

### What Changed
- **Voicegroups Tab** (`ui/voicegroups_tab.py`): Fixed `_on_generate_gm()` — was reading `report['total_samples']` but the key was renamed to `total_ds_samples`. Updated confirmation and success dialog text to match new behavior (filler, not square wave fallback).
- **GM Voicegroup Generator** (`core/sound/gm_voicegroup.py`): Voicegroup size is now trimmed to only include slots up to the highest real instrument. No empty trailing slots.
- **Sound Editor Tab** (`ui/sound_editor_tab.py`): Added "Export .s File..." (saves a copy) and "Replace with .s File..." (overwrites music data, rewrites labels to match existing constant, keeps registration) to the song context menu.

### Files Changed
- ui/voicegroups_tab.py — fixed dict key, updated dialog text
- core/sound/gm_voicegroup.py — trimmed voicegroup to last real instrument slot
- ui/sound_editor_tab.py — export and replace song methods

---

## [2026-04-07] — Save & Git Confirmation Dialogs

### Type
UX

### Summary
Added confirmation dialogs before destructive actions. File → Save / Ctrl+S now asks "Save all changes to disk?" before writing. Piano roll save button warns that it writes the .s file directly to disk (unlike most PorySuite edits which stay in memory until File → Save). Git Push and Pull confirmations are now non-suppressible (no "Don't show again" checkbox) and use explicit language about what data will be overwritten or lost.

### What Changed
- **Unified Main Window** (`ui/unified_mainwindow.py`): `_on_save_all()` shows a confirmation dialog before saving, explaining that edits will be written to C source, assembly, and JSON files.
- **Piano Roll Window** (`ui/piano_roll_window.py`): `_on_save()` shows a confirmation dialog explaining that this writes the .s file immediately — unlike most edits which wait for File → Save.
- **Main Window** (`ui/mainwindow.py`): Git Pull confirmation reworded — warns "Pulling will overwrite your local files" and "Any work you haven't committed will be permanently lost." Git Push confirmation reworded — warns "Pushing will overwrite the remote with your local commits" and "If you have broken or incomplete work, it will be pushed too." Both changed from suppressible `maybe_exec()` to non-suppressible `QMessageBox.question()`, default button set to Cancel.
- **Suppress Dialog** (`core/suppress_dialog.py`): Removed `git_pull_confirm` and `git_push_confirm` from the suppressible registry (these should never be skippable).

### Files Changed
- ui/unified_mainwindow.py — pre-save confirmation dialog
- ui/piano_roll_window.py — pre-save confirmation with direct-write warning
- ui/mainwindow.py — git push/pull confirmations reworded, made non-suppressible
- core/suppress_dialog.py — removed git keys from suppressible registry

---

## [2026-04-07] — Sound Editor: Song Rename/Delete, Piano Roll Fixes, GM VG Dedup, .s Import Polish

### Type
Fix / Feature

### Summary
Four fixes from user testing: (1) .s file import dialog now asks for a display name instead of a raw constant — auto-derives MUS_/SE_ constant with a type selector dropdown. (2) Right-click context menu on the Songs list with "Rename Song..." and "Delete Song". Rename updates all 3 config files + renames .s file + rewrites internal labels. Delete checks for cross-references in project source and warns before removing. (3) Piano roll save/reload corruption fixed — `flatten_track_commands` was called with `loop_count=1` which duplicated loop content on every save→reload cycle; changed to `loop_count=0`. Multi-select drag fixed (was only moving the grabbed note). Ctrl+A now respects track filter (selects only the current track's notes in single-track view). (4) GM voicegroup generator no longer floods with 80+ identical square wave fallbacks — empty slots get silent filler instead, and all unique non-DirectSound instruments (square waves, programmable waves, noise) from existing voicegroups are included.

### What Changed
- **Song Table Manager** (`core/sound/song_table_manager.py`): Added `rename_song()`, `delete_song()`, and `find_song_references()` functions.
- **Sound Editor Tab** (`ui/sound_editor_tab.py`): Right-click context menu on song tree with Rename/Delete. Rename asks for display name, derives constant, validates, updates everything. Delete checks references, shows warning, re-indexes remaining songs.
- **Piano Roll Widget** (`ui/piano_roll_widget.py`): Changed `loop_count=1` to `loop_count=0` in `load_song_data()`. Added `_drag_originals` dict for multi-select drag. Fixed `select_all()` to filter by `_track_index`.
- **.s Import Dialog** (`ui/dialogs/s_file_import_dialog.py`): Changed from constant input to display name input with MUS_/SE_ prefix selector and auto-derived constant preview.
- **GM Voicegroup Generator** (`core/sound/gm_voicegroup.py`): Added `_catalog_all_instruments()` to collect all unique non-filler instruments. Empty GM slots now get silent filler instead of playable square wave fallbacks. Non-DirectSound instruments (square, prog wave, noise) placed in appropriate GM slots.
- **Voicegroups Tab** (`ui/voicegroups_tab.py`): Added "Rename" button and double-click rename for voicegroup friendly labels.

### Files Changed
- core/sound/song_table_manager.py — rename_song(), delete_song(), find_song_references()
- core/sound/gm_voicegroup.py — _catalog_all_instruments(), deduplicated GM generator, filler for empty slots
- ui/sound_editor_tab.py — song context menu (rename/delete)
- ui/piano_roll_widget.py — loop_count fix, multi-select drag, track-filtered select_all
- ui/dialogs/s_file_import_dialog.py — display name input with constant derivation
- ui/voicegroups_tab.py — rename button + double-click rename

---

## [2026-04-07] — Sound Editor: Import Song .s File from Another Project

### Type
Feature

### Summary
New "Import .s..." button on the Songs tab lets users import a pre-compiled .s assembly song file from another pokefirered project. A 3-page wizard walks through file selection (with full song preview — tracks, tempo, voicegroup, loop info), voicegroup compatibility checking (warns if the source voicegroup doesn't exist and lets you pick an alternative), and automatic registration in song_table.inc, songs.h, and midi.cfg. Labels and voicegroup references are rewritten automatically to match the target project. Completes Sound Editor Phase 9.

### What Changed
- **SFileImportDialog** (`ui/dialogs/s_file_import_dialog.py`): NEW — 3-page wizard dialog. Page 0: file picker + song preview (parsed via `song_parser`) + constant name input with live validation + player type selector. Page 1: voicegroup compatibility check with green/amber status, dropdown to remap, editable reverb/volume/priority. Page 2: progress bar + result summary. Worker thread copies .s file, rewrites labels/voicegroup, calls `register_song()`.
- **Sound Editor Tab** (`ui/sound_editor_tab.py`): Added "Import .s..." button alongside "Import MIDI...", `_open_s_import()` and `_on_s_imported()` methods.
- **Sound Editor Roadmap** (`docs/SOUND_EDITOR_PLAN.md`): Phase 9 marked COMPLETE. Steps 9.1 (export) and 9.2 (QoL) marked NOT NEEDED.
- **Unified Editor Plan** (`docs/UNIFIED_EDITOR_PLAN.md`): Sound Editor status updated to Phases 1-9 COMPLETE. Added Phase 9 (Pokedex Habitat/Area Display) to roadmap.

### Files Changed
- ui/dialogs/s_file_import_dialog.py — NEW: Import Song .s wizard dialog
- ui/sound_editor_tab.py — added Import .s button + handler methods
- docs/SOUND_EDITOR_PLAN.md — Phase 9 completion, 9.1/9.2 marked not needed
- docs/UNIFIED_EDITOR_PLAN.md — status update + Phase 9 Pokedex habitat roadmap entry

---

## [2026-04-07] — Piano Roll: Song Structure Panel, Save Button, Instrument Dropdown, VG Labels

### Type
Feature

### Summary
Major piano roll usability improvements. Song Structure panel on the right side shows the song's structural commands (sections, loops, pattern calls, end markers) in plain English — users can add, remove, and edit these to control how the song plays back. Save button added to the toolbar with Ctrl+S shortcut. Instrument selector changed from a tiny number spinner to a full dropdown showing instrument names. Voicegroup selector now shows friendly labels (auto-generated from song usage or user-renamed), with a new "Auto-Label" button in the Voicegroups tab. Labels are stored in PorySuite's cache — source code is never touched.

### What Changed
- **Song Structure Panel** (`ui/piano_roll_structure.py`): NEW — Right-side panel showing structural commands with friendly names (Section = LABEL, Loop Back = GOTO, Play Pattern = PATT, End Pattern = PEND, End Song = FINE). Color-coded list (green/blue/orange/red). Click to seek, double-click to edit. Add/Remove/Edit buttons with helpful tooltips. `get_loop_region()` returns loop start/end from structure items. `structure_changed` signal syncs loop region to sequencer.
- **Save Button** (`ui/piano_roll_window.py`): Save button on toolbar + Ctrl+S shortcut. Calls `save_to_disk()` directly — no need to go back to main window File → Save.
- **Instrument Dropdown** (`ui/piano_roll_tracks.py`): Replaced QSpinBox (tiny up/down arrows) with QComboBox showing all 128 slots with instrument names (e.g. "0: Sc88Pro Square Wave", "38: Sc88Pro Organ2"). Repopulates when voicegroup changes.
- **Voicegroup Friendly Labels** (`core/sound/voicegroup_labels.py`): NEW — JSON label mapping stored per-project in PorySuite's cache dir. "Auto-Label" button in Voicegroups tab generates names from song usage (e.g. "Encounter Rocket", "Cycling + 4 more"). Pencil button in piano roll for individual rename. Labels show in VG dropdowns as "Friendly Name (voicegroupNNN)". VG dropdown refreshes from live data on open (picks up newly created GM voicegroups).
- **Voicegroups Tab** (`ui/voicegroups_tab.py`): "Auto-Label" button added to toolbar. VG tree shows friendly labels inline ("VG 013 — Encounter Rocket"). Labels loaded on `load_data()`, shared with piano roll via same dict reference.

### Files Changed
- ui/piano_roll_structure.py — NEW: Song Structure panel
- core/sound/voicegroup_labels.py — NEW: VG label management (load/save/generate/rename)
- ui/piano_roll_window.py — Save button, Ctrl+S, structure panel wiring, VG label passthrough
- ui/piano_roll_tracks.py — Instrument dropdown (was spinner), VG rename button, live VG refresh
- ui/voicegroups_tab.py — Auto-Label button, labels in VG tree
- ui/sound_editor_tab.py — VG labels loaded on project open, passed to piano roll + voicegroups tab

---

## [2026-04-07] — Abilities Editor: Visual Battle & Field Effect Editor

### Type
Feature

### Summary
The Abilities Editor's read-only "Battle & Field Effects" card is now a full visual editor. Users pick an effect category from a dropdown (Status Immunity, Contact Status Infliction, Type Absorb, Weather, Stat Boost, etc.) and configure parameters (which status, which type, which stat, chance percentage, HP fraction). The editor writes real C code to the correct source files (battle_util.c, wild_encounter.c, pokemon.c, battle_script_commands.c, battle_main.c) on save. Detects existing effects automatically by parsing the C source.

### What Changed
- **Ability Effect Templates** (`core/ability_effect_templates.py`): NEW — 13 battle effect templates (status immunity, contact status, type absorb HP, type absorb power boost, weather switch-in, end-of-turn stat boost, intimidate, contact recoil, pinch type boost, type immunity, weather recovery, type trap, crit prevention) and 8 field effect templates (encounter rate halve, encounter rate double, type encounter rate boost, post-battle item pickup, guaranteed wild escape, faster egg hatching, nature sync, gender attract). Each template defines configurable parameters, C code generation, and auto-detection from existing source. Value maps for all GBA types, stats, statuses, weather conditions. Field templates that don't exist natively in pokefirered (egg hatching, nature sync, gender attract, type encounters) generate and insert the code when assigned.
- **Abilities Tab Widget** (`ui/abilities_tab_widget.py`): Replaced read-only `QGroupBox("Battle & Field Effects (read-only)")` with two editable sections — Battle Effect and Field Effect. Each has a category `QComboBox`, dynamic parameter widgets that rebuild per-category, and a monospace code preview showing the generated C. `AbilityDetailPanel.load()` now auto-detects existing effects via `detect_all_effects()`. `save_current()` stores effect config. New `apply_effect_changes()` method writes C code on save, with old-code removal and new-code insertion.
- **Main Window** (`ui/mainwindow.py`): `save_abilities_editor()` now calls `apply_effect_changes()` after writing ability names/descriptions, logging results.

### Files Changed
- core/ability_effect_templates.py — NEW: template registry, detection, code gen, file manipulation
- ui/abilities_tab_widget.py — Effect editor UI, save integration
- ui/mainwindow.py — Effect save pipeline hook

---

## [2026-04-07] — Abilities Editor (Phase 8A) — COMPLETE

### Type
Feature

### Summary
Full Abilities Editor added to the PorySuite toolbar. Browse, search, add, duplicate, rename, and delete abilities with live species usage cross-references. Battle and field effect code copying when creating new abilities. Rename uses the same RefactorService pattern as moves/items/trainers/species. Species names in the usage table now display real project data (no fabrication).

### What Changed
- **Abilities Tab Widget** (`ui/abilities_tab_widget.py`): Searchable ability browser (left panel) with detail editor (right panel). Identity section: display name (12-char limit with counter/color feedback), constant name (read-only, renamed via Rename button), ID (read-only). Description editor with 52-char limit and overflow highlighting. Battle & Field Info card showing parsed effect categories from C source with "Open in Editor" buttons. Species Usage table with slot info and double-click cross-navigation to the Pokemon tab. Add New Ability dialog with auto-derived constant from display name, separate Battle Effect and Field Effect dropdowns (filtered to abilities that actually have each type), preview labels showing what code will be copied. Duplicate pre-fills source ability in effect dropdowns. Delete with safety scan (blocks if species are using the ability, warns about C code references).
- **Add Dialog — Effect Copying**: `scan_ability_battle_effects()` and `scan_ability_field_effects()` parse C source files to find which abilities have battle case blocks or field effect checks. `copy_battle_effects()` duplicates case blocks in battle_util.c. `copy_field_effects()` adds `|| ability == NEW` to existing if-chains in wild_encounter.c. `build_inline_ref_summary()` lists inline references the user needs to handle manually.
- **Refactor Service** (`core/refactor_service.py`): Added `rename_ability()` — generates preview of ABILITY_* constant + sXxxDescription variable renames across all source files, queues pending op. `apply_pending()` handles the `rename_ability` op: search-and-replace constant + description variable, update display name in gAbilityNames, update abilities.json key.
- **Rename Dialog** (`ui/custom_widgets/rename_dialog.py`): Added "Ability" to `_NAME_LIMITS` (12 chars).
- **Main Window** (`ui/mainwindow.py`): `_on_ability_rename()` rewritten as a full RenameDialog handler matching the move/item rename pattern. `load_abilities_editor()` passes `project_root` to the tab widget.
- **Species name fix**: `_get_species_usage()` no longer transforms species names — displays them exactly as stored in species.json (was fabricating title-cased names via `.replace("_", " ").title()`).

### Files Changed
- ui/abilities_tab_widget.py — Full editor with add/duplicate/rename/delete, effect copying, species usage
- core/refactor_service.py — rename_ability() method + apply_pending handler
- ui/custom_widgets/rename_dialog.py — Ability name limit added
- ui/mainwindow.py — Ability rename handler, project_root passthrough

---

## [2026-04-07] — Piano Roll UI Overhaul: Fixed Ruler & Compact Toolbar

### Type
UI Fix

### Summary
The piano roll toolbar was so crowded that controls were getting clipped off-screen, and the timeline ruler was invisible because it scrolled away with the note grid. Fixed both: the ruler is now a separate widget pinned above the scroll area (always visible), and the toolbar is streamlined — track selector changed from tabs to a dropdown, song info moved to the status bar.

### What Changed
- **Piano Roll Widget** (`ui/piano_roll_widget.py`): NEW `RulerWidget` class — a separate fixed-height widget that draws measure numbers, beat ticks, and the red playback triangle. Lives above the scroll area so it never scrolls vertically. Syncs horizontally with the piano roll via `set_scroll_offset()`. Click-and-drag scrubbing on the ruler to set playback position. `PianoRollWidget` changed from `QScrollArea` to `QWidget` composite (ruler row + scroll area in a `QVBoxLayout`). Ruler row includes a spacer for the piano key column and a spacer for the vertical scrollbar. Canvas no longer draws the ruler or handles ruler clicks — `_RULER_HEIGHT` offset removed from canvas coordinate math. Added `set_playback_tick()` wrapper that updates both canvas and ruler.
- **Piano Roll Window** (`ui/piano_roll_window.py`): Track selector changed from `QTabBar` (took huge horizontal space with 7+ tracks) to a compact `QComboBox` (140px). Song info label moved from toolbar to status bar. Removed `QTabBar` import. All `_track_tabs` references replaced with `_track_combo`. All `canvas.set_playback_tick()` calls changed to `_piano_roll.set_playback_tick()` so both canvas and ruler update together.

### Files Changed
- ui/piano_roll_widget.py — RulerWidget class, PianoRollWidget restructured, canvas ruler code removed
- ui/piano_roll_window.py — Compact toolbar, track dropdown, status bar info

---

## [2026-04-07] — Piano Roll Round-Trip Editing (Step 8.6)

### Type
Feature

### Summary
Piano roll edits now save properly through PorySuite's standard save pipeline — edits stay in RAM, the main window's dirty flag lights up, and File → Save writes the .s file. Loop points, mid-song control changes, and structural commands are all preserved. Enhanced timeline ruler with drag-to-scrub and red triangle position handle.

### What Changed
- **Song Writer** (`core/sound/song_writer.py`): NEW — Converts SongData back to valid .s assembly. `notes_to_track_commands()` generates NOTE+WAIT pairs with loop support (LABEL+GOTO), preserves mid-song control changes (VOL/PAN/MOD/BEND/TEMPO) from original tracks at their tick positions, and keeps structural commands (PATT/PEND/GOTO/LABEL/FINE). Running-status optimization (omits unchanged pitch/velocity/duration). `write_song()` produces the full .s file. `save_song_file()` writes to disk.
- **Piano Roll Window** (`ui/piano_roll_window.py`): Follows PorySuite save pattern — NO standalone save button. Emits `modified` signal that propagates to main window dirty flag. `save_to_disk()` called by `_on_save_all()`. `has_unsaved_changes()` for pipeline check. Close confirmation warns edits will save with File → Save. Title bar shows [modified].
- **Sound Editor Tab** (`ui/sound_editor_tab.py`): Connects `piano_roll.modified` → `self.modified.emit()` to propagate dirty flag.
- **Unified Main Window** (`ui/unified_mainwindow.py`): `_on_save_all()` now checks piano roll for unsaved changes and calls `save_to_disk()`.
- **Piano Roll Widget** (`ui/piano_roll_widget.py`): Ruler expanded to 32px with gradient, beat ticks, red triangle playback handle, drag-to-scrub.

### Files Changed
- core/sound/song_writer.py — NEW: song-to-assembly writer
- ui/piano_roll_window.py — Save pipeline integration, modified signal, dirty tracking
- ui/sound_editor_tab.py — Piano roll modified signal connection
- ui/unified_mainwindow.py — Piano roll added to _on_save_all
- ui/piano_roll_widget.py — Enhanced ruler/timeline

---

## [2026-04-07] — Piano Roll Ruler Scrub & Position Marker

### Type
Enhancement

### Summary
Added visible playback position marker (red triangle) on the piano roll ruler and drag-to-scrub — click and drag anywhere on the ruler to move the playback position, like a standard DAW timeline. Previously the ruler only responded to single clicks, which was easy to miss.

### What Changed
- **Piano Roll Widget** (`ui/piano_roll_widget.py`): Added `RULER_SCRUB` drag mode — clicking the ruler enters scrub mode, dragging continuously updates playback position. Red downward-pointing triangle drawn on ruler at current playback tick. Releases scrub mode on mouse up.
- **Piano Roll Window** (`ui/piano_roll_window.py`): Updated Play button tooltip to mention drag-to-scrub.

### Files Changed
- ui/piano_roll_widget.py — Ruler scrub drag mode, position triangle marker
- ui/piano_roll_window.py — Updated tooltip

---

## [2026-04-07] — Piano Roll Tempo Fix & Song Structure Flattening

### Type
Bug Fix / Performance

### Summary
Fixed two major playback issues: (1) inconsistent tempo (lagging/speeding up) caused by rendering notes inside the audio callback, and (2) songs with PATT/PEND/GOTO structure (like Game Corner) not showing their full intro-then-loop sequence.

### What Changed
- **Real-Time Sequencer** (`core/sound/realtime_sequencer.py`): Note rendering moved to a background worker thread. The audio callback now ONLY does lightweight mixing of already-rendered voices. Notes are queued as `_RenderRequest` objects and rendered by `_render_worker()` on a daemon thread. This eliminates buffer underruns that caused tempo wobble.
- **Audio Engine** (`core/sound/audio_engine.py`): Vectorized the looping sample resample in `render_directsound`. The old Python for-loop (65k iterations per note) is replaced with numpy array operations — roughly 10-50x faster.
- **Piano Roll Widget** (`ui/piano_roll_widget.py`): `load_song_data` now calls `flatten_track_commands(loop_count=1)` before extracting notes. Songs with PATT subroutine calls and GOTO loops now show their full structure — intro sections, pattern expansions, and loop bodies are all visible.

### Files Changed
- core/sound/realtime_sequencer.py — Background render thread, render queue, callback only mixes
- core/sound/audio_engine.py — Vectorized looping sample interpolation
- ui/piano_roll_widget.py — Flatten PATT/PEND/GOTO in load_song_data

---

## [2026-04-07] — Piano Roll Playback Bug Fixes (Live Updates)

### Type
Bug Fix

### Summary
Fixed all critical playback bugs found in the piano roll audit. Swapping voicegroups or instruments during playback now works correctly — the sequencer is updated in-place instead of being destroyed and silently not recreated. Track volume and pan sliders are now connected to the sequencer. Pressing pause/resume when the sequencer hasn't been created yet starts playback instead of doing nothing. The sequencer is properly stopped before being discarded anywhere in the code.

### What Changed
- **Piano Roll Window** (`ui/piano_roll_window.py`):
  - `_on_voicegroup_changed`: now calls `sequencer.update_voicegroup()` instead of setting sequencer to None (which broke playback)
  - `_on_track_instrument`: now calls `sequencer.set_track_instrument()` instead of destroying sequencer
  - NEW `_on_track_volume`: connected to sidebar's track_volume signal, calls `sequencer.set_track_volume()`
  - NEW `_on_track_pan`: connected to sidebar's track_pan signal, calls `sequencer.set_track_pan()`
  - `_on_pause_resume`: calls `_on_play()` instead of returning silently when sequencer is None
  - `_reload_tracks` and `closeEvent`: now call `sequencer.stop()` before setting sequencer to None
- **Real-Time Sequencer** (`core/sound/realtime_sequencer.py`): Already had `update_voicegroup()`, `set_track_volume()`, `set_track_pan()`, `set_track_instrument()` methods added in previous session

### Files Changed
- ui/piano_roll_window.py — Fixed 7 bugs: voicegroup swap, instrument swap, volume/pan wiring, pause/resume edge case, cleanup on destroy

---

## [2026-04-07] — Piano Roll Controls & Scroll Guards

### Type
Enhancement

### Summary
Fleshed out the piano roll track sidebar with proper controls: voicegroup selector, per-track instrument (VOICE) picker with live name lookup, Mute All / Unmute All / Clear Solo batch buttons. Added scroll guards to every slider and spinner in the piano roll to prevent accidental changes when scrolling the page.

### What Changed
- **Track sidebar** (`ui/piano_roll_tracks.py`): Voicegroup combo (top of sidebar), per-track instrument QSpinBox with live name label, Mute All / Unmute All / Clear Solo buttons, scroll guards on all vol/pan sliders and instrument spinners
- **Piano roll window** (`ui/piano_roll_window.py`): Scroll guards on zoom X, zoom Y, and volume sliders. Wired up voicegroup_changed, track_instrument, and mute_solo_changed signals. Voicegroup change updates all instrument names and recreates sequencer.
- **New helper** `get_instrument_names()` in piano_roll_tracks.py: returns 128 friendly names for any voicegroup

### Files Changed
- ui/piano_roll_tracks.py — Voicegroup combo, instrument picker, batch mute/solo, scroll guards
- ui/piano_roll_window.py — Scroll guards on toolbar sliders, new signal handlers

---

## [2026-04-06] — GM Voicegroup Generator (Step 8.5)

### Type
Enhancement

### Summary
Added a GM (General MIDI) Voicegroup Generator that scans all 77 voicegroups, catalogs all 89 unique DirectSound samples, and builds a 128-slot voicegroup mapped to GM program numbers (0-127). Matched slots use real instruments from the project; unmatched slots fall back to a square wave. Accessible from both the Voicegroups tab toolbar and the MIDI Import dialog.

### What Changed
- **GM Voicegroup Generator** (`core/sound/gm_voicegroup.py`): NEW — Scans all voicegroups, catalogs 89 unique DirectSound samples, maps them to GM program numbers (0-127) by SC-88 Pro/SD-90 name matching. Creates a 128-slot voicegroup with real instruments for matched slots and square wave fallback for unmatched. Includes a coverage report function showing how many GM slots have real instruments.
- **Voicegroups Tab** (`ui/voicegroups_tab.py`): Added "Generate GM" button to the toolbar. Creates the GM voicegroup and adds it to the project.
- **MIDI Import Dialog** (`ui/dialogs/midi_import_dialog.py`): Added "Generate GM" button to the voicegroup selection page. MIDI import auto-detects an existing GM voicegroup and pre-selects it.

### Files Changed
- core/sound/gm_voicegroup.py — NEW: GM voicegroup generator with sample scanning, GM mapping, and coverage reporting
- ui/voicegroups_tab.py — Added "Generate GM" toolbar button
- ui/dialogs/midi_import_dialog.py — Added "Generate GM" button + auto-select existing GM voicegroup

---

## [2026-04-07] — Real-Time Sequencer for Piano Roll (Step 8.4)

### Type
Enhancement

### Summary
Piano roll now uses a real-time sequencer — notes are synthesized on-the-fly as the cursor crosses them on the timeline. No pre-rendering of the full song. Pause anywhere, edit notes, resume instantly. Click the ruler to seek. Edits take effect immediately because there's no buffer to re-render.

### What Changed
- **Real-Time Sequencer** (`core/sound/realtime_sequencer.py`): NEW — Real-time audio engine that plays piano roll notes as the cursor reaches them. Each note is rendered individually and mixed into live audio output via sounddevice. Supports play/pause/resume/seek with no latency. 32-voice polyphony limit, per-track mute/solo/volume/pan, loop region support.
- **Piano Roll Window** (`ui/piano_roll_window.py`): Rewritten to use RealtimeSequencer instead of pre-rendering. Play starts instantly from any position. Pause/resume preserves position. Edits push updated notes to the sequencer with no re-render step. Click ruler to seek.
- **Piano Roll Widget** (`ui/piano_roll_widget.py`): Added `ruler_clicked` signal — clicking the ruler bar seeks to that tick position.

### Files Changed
- core/sound/realtime_sequencer.py — NEW: real-time note-by-note sequencer
- ui/piano_roll_window.py — Rewritten to use real-time sequencer
- ui/piano_roll_widget.py — Added ruler_clicked signal for click-to-seek

---

## [2026-04-07] — Piano Roll Editing & Track Management (Steps 8.2 + 8.3)

### Type
Enhancement

### Summary
Added full note editing to the piano roll and a track management sidebar. Users can now place, move, resize, and delete notes visually, select groups of notes with box-select or Ctrl+click, copy/paste, and manage tracks (add, remove, duplicate, mute, solo, volume, pan).

### What Changed
- **Piano Roll Editing (Step 8.2)** (`ui/piano_roll_widget.py`): Click empty space to place a new note (snapped to grid), drag right after placing to extend duration, click-drag an existing note body to move it (time + pitch), drag the right edge of a note to resize its duration, right-click a note to delete it. Shift+drag draws a selection box selecting all notes inside, Ctrl+click toggles selection on individual notes, Delete/Backspace deletes selected notes, Ctrl+A select all, Escape deselect, Ctrl+C copy, Ctrl+V paste at playback position. Selected notes get yellow highlight outline. Velocity shown as thin bar at bottom of each note. Snap grid options: 1/4 beat, 1/8, 1/16, 1/32, Free (dropdown in toolbar). Cursor changes: crosshair on empty space, open hand on note body, resize arrow on right edge.
- **Track Management (Step 8.3)** (`ui/piano_roll_tracks.py`): NEW — 220px track sidebar panel on the left side of the piano roll. Per-track rows showing color swatch, track name, instrument number + name from voicegroup, channel, note count. Volume slider per track (0-127, reads initial VOL command), pan slider per track (0-127, 64=center, reads initial PAN command). Mute (M) and Solo (S) toggle buttons with colored highlight when active. Click a track row to select it and switch the piano roll to that track. Add track (+) creates new empty track with default VOICE command, Remove track (-) with confirmation dialog if track has notes, Duplicate track (Dup) deep copies all commands and notes. Instrument names looked up from voicegroup data when available.

### Files Changed
- ui/piano_roll_widget.py — Added note placement, movement, resizing, deletion, selection (box/individual/all), copy/paste, velocity display, cursor changes
- ui/piano_roll_tracks.py — NEW: Track sidebar panel with per-track controls (volume, pan, mute, solo, instrument info, add/remove/duplicate)

---

## [2026-04-06] — Read-Only Piano Roll View (Step 8.1)

### Type
Enhancement

### Summary
Added a read-only piano roll view that opens from the Songs tab. Users can now visualize any song as colored note bars on a DAW-style grid, with per-track tabs, zoom controls, and a playback cursor.

### What Changed
- **Piano Roll Widget** (`ui/piano_roll_widget.py`): NEW — Custom QPainter-based canvas inside a QScrollArea. Mini piano keyboard on the left sidebar (all 128 MIDI notes), measure ruler at top with measure numbers, grid lines at measure/beat/sub-beat levels (bright/medium/dim), darker rows for black keys, colored note bars per track (velocity affects opacity), loop region highlight (green tint), playback cursor (red vertical line), zoom via Ctrl+Wheel (horizontal) and Ctrl+Shift+Wheel (vertical), hover shows note name in status bar.
- **Piano Roll Window** (`ui/piano_roll_window.py`): NEW — Standalone QMainWindow hosting the piano roll. Track tab bar ("All Tracks" plus one tab per track showing channel and instrument), horizontal and vertical zoom sliders in toolbar, grid snap selector combo box (scroll-guarded per project rules), song info label (label, BPM, notes, tracks, loop info), status bar with hovered note name, keyboard shortcuts (Ctrl+0 reset zoom, Ctrl+/- zoom in/out), Reset Zoom button.
- **Sound Editor Tab** (`ui/sound_editor_tab.py`): Added "Piano Roll" button next to Play/Stop in transport controls. Opens PianoRollWindow for the currently selected song. Button enables/disables based on whether a parsed song is selected.

### Files Changed
- ui/piano_roll_widget.py — NEW: QPainter-based piano roll canvas with keyboard, ruler, grid, notes, loop highlight, zoom
- ui/piano_roll_window.py — NEW: Standalone window with track tabs, zoom sliders, grid snap, song info
- ui/sound_editor_tab.py — Added "Piano Roll" button to transport controls

---

## [2026-04-06] — Added mido to Setup Wizard Dependencies

### Type
Fix

### Summary
The `mido` library (used by the MIDI Import wizard) was listed in requirements.txt but missing from the setup wizard's dependency checker. Users who didn't have mido installed would see no entry for it on the setup screen and no install button.

### What Changed
- **Setup wizard** (`core/programsetup.py`): Added `mido` to the dependency list under "App / Editor" with check, description, and pip install button.

### Files Changed
- core/programsetup.py — Added mido dependency entry

---

## [2026-04-06] — Song Structure Page Rebuilt as Section Sequencer (Step 7.3)

### Type
Enhancement

### Summary
Rebuilt the Song Structure page (Page 3) of the MIDI Import wizard from 4 simple radio buttons into a full section sequencer. Users can now define named sections, arrange them in a custom play order, and set a loop start position.

### What Changed
- **Section Sequencer (Step 7.3 rebuild):** The Song Structure page now lets users define named sections by specifying start/end measure numbers, arrange sections in a custom play order (drag/reorder), and set which position in the play order starts the loop (everything before the loop point is the intro that plays once, everything from that point onward repeats). Quick presets (Automatic, Loop All, No Loop) cover simple cases. The post-processor translates custom structure into GOTO/PATT/PEND assembly commands in the .s file.
- **MidiFileInfo expanded:** `core/sound/midi_importer.py` `MidiFileInfo` now includes `total_measures`, `time_sig_num`, `time_sig_den` computed from the MIDI file, so the structure page can display measure ranges accurately.

### Files Changed
- ui/dialogs/midi_import_dialog.py — Rebuilt Song Structure page (Page 3) from radio buttons to section sequencer with drag-reorder and loop point
- core/sound/midi_importer.py — Added total_measures, time_sig_num, time_sig_den to MidiFileInfo

---

## [2026-04-06] — Phase 7 MIDI Import Complete (Steps 7.2 + 7.3)

### Type
Enhancement

### Summary
Completed the remaining two steps of the MIDI Import wizard: instrument mapping and song structure/loop points. The wizard is now a full 5-page flow.

### What Changed
- **Instrument Mapping page (Step 7.2):** Per-track grid showing MIDI instrument → voicegroup slot mapping. Each row has the GM instrument name, a slot spinner (0-127), and a live label showing what instrument is in that voicegroup slot (green text) or "filler / empty" (red). Auto-Match by Name button uses word overlap scoring. After mid2agb converts the file, `_postprocess_voice_remap()` rewrites VOICE commands for any remapped tracks.
- **Song Structure page (Step 7.3):** Initial version with 4 loop mode presets (later rebuilt as full section sequencer — see entry above).
- **Step indicator:** Top of the dialog shows the current step in the 5-page flow.
- **Dialog now receives voicegroup data** from the Sound Editor so instrument names can be displayed in the mapping page.

### Files Changed
- ui/dialogs/midi_import_dialog.py — Rewrote as 5-page wizard (was 3 pages), added mapping grid, structure page, voice remap post-processor
- ui/sound_editor_tab.py — Pass voicegroup_data to MidiImportDialog constructor

---

## [2026-04-06] — Phase 7 MIDI Import, Phase 6 Complete, Dynamic Palettes Fix

### Type
Enhancement + Fix

### Summary
Three areas of work: (1) Built the MIDI Import wizard (Phase 7, Steps 7.1 + 7.4) — users can now import .mid files as new GBA songs from within PorySuite. (2) Completed the remaining Phase 6 items — constants sync, Porymap integration (already handled by file watching), and audio output device selector in Settings. (3) Fixed a bug where replacing the pokefirered folder would leave a stale marker file, making PorySuite think dynamic palettes were still applied when they weren't — causing broken overworld palette colors.

### What Changed
- **MIDI Import Dialog** (`ui/dialogs/midi_import_dialog.py`): 3-page wizard — pick a .mid file (see tracks, tempo, duration), choose voicegroup + settings (volume, reverb, priority), then import. Runs mid2agb on a background thread and registers the new song in song_table.inc, songs.h, and midi.cfg.
- **MIDI Importer backend** (`core/sound/midi_importer.py`): Full GM instrument name table (128 entries), MIDI parser via `mido`, mid2agb runner, song registration pipeline, constant name validation.
- **Import MIDI button**: Added to Songs tab left panel. Opens the wizard. After import, reloads the song list and selects the new song.
- **Constants sync**: Added "sound" to the page set that triggers ConstantsManager.refresh() when switching to EVENTide. New/removed songs now appear in EVENTide dropdowns automatically.
- **Audio output mode selector**: New dropdown in Settings > Sound Editor — Stereo or Mono. Stereo preserves left/right panning like the GBA. Mono mixes both channels together. Both Songs tab and Instruments tab preview respect the setting.
- **Dynamic palettes detection fix**: `is_dowp_enabled()` now checks the actual C source code for DOWP patch signatures instead of just looking for a marker file. If the source doesn't match the marker (e.g. folder was replaced), the stale marker is cleaned up and the button re-enables.
- **Scroll guards**: Added `install_scroll_guard()` to all QComboBox widgets in the Settings dialog (6 combos — previously unprotected).
- **New dependency**: `mido>=1.3` added to requirements.txt.

### Files Changed
- core/dynamic_ow_pal_patch.py — Rewrote `is_dowp_enabled()` to verify source code, not just marker file
- core/sound/midi_importer.py — NEW: MIDI import pipeline
- core/sound/audio_engine.py — Added `mono` parameter to `AudioPlayer.play()` for stereo/mono output mode
- ui/sound_editor_tab.py — Added "Import MIDI..." button, `_open_midi_import()`, `_on_midi_imported()`
- ui/dialogs/midi_import_dialog.py — NEW: MIDI Import wizard dialog
- ui/dialogs/settingsdialog.py — Added audio device selector, scroll guards on all combos
- ui/instruments_tab.py — Pass output device setting to AudioPlayer
- ui/unified_mainwindow.py — Added "sound" to pages that trigger ConstantsManager refresh
- requirements.txt — Added mido>=1.3
- docs/SOUND_EDITOR_PLAN.md — Phase 6 marked COMPLETE, Phase 7 Steps 7.1+7.4 marked COMPLETE

---

## [2026-04-06] — EVENTide Command Widget Bug Fixes (savebgm, healplayerteam, playmoncry, shared sub-label save, local_id)

### Type
Fix

### Summary
Fixed five bugs in EVENTide's event editor command widgets and save pipeline discovered during Sound Editor integration testing. The savebgm widget was missing its required song argument (build error). The healplayerteam widget emitted a nonexistent macro instead of `special HealPlayerParty`. The playmoncry widget discarded the mode parameter. Scripts sharing sub-labels via `goto` could overwrite each other during save. Objects without a `local_id` field got an empty one injected, causing a build error.

### What's Fixed
- **savebgm missing song argument**: `_SaveBgmWidget` was `_header_only = True`, returning just `('savebgm',)`. The GBA macro requires `savebgm song:req`. Added a ConstantPicker for MUS_* constants so the widget returns `('savebgm', song_constant)`. Factory updated to pass the parsed song arg.
- **healplayerteam not a real macro**: Widget returned `('healplayerteam',)` which doesn't exist as a GBA assembly macro. Changed to return `('special', 'HealPlayerParty')`. Factory maps `special HealPlayerParty` → `_HealPlayerTeamWidget()`.
- **playmoncry mode discarded**: Widget always hardcoded mode to `0`, dropping the original value. Now accepts and preserves the `mode` parameter via constructor.
- **Shared sub-label save overwrite**: When multiple events share a script sub-label via `goto` (e.g. three triggers all referencing `RivalBattle`), the save loop processed events in order, so the last event's stale copy overwrote the user's edit. Fixed by reordering the save loop so the currently-selected event is processed last (its edits win).
- **Empty local_id build error**: `_collect_current()` wrote `obj['local_id'] = ""` to objects that originally had no `local_id` field. The build tool rejected the empty value. Now only writes `local_id` if the text is non-empty. Removed the bogus field from Oak's Lab map.json.

### Files Changed
- eventide/ui/event_editor_tab.py — Fixed _SaveBgmWidget, _HealPlayerTeamWidget, _PlayMonCryWidget, shared sub-label save ordering, local_id guard
- pokefirered/data/maps/PalletTown/scripts.inc — Fixed `savebgm` → `savebgm MUS_PALLET`
- pokefirered/data/maps/PalletTown_ProfessorOaksLab/map.json — Removed bogus empty `local_id` from Aide1

---

## [2026-04-06] — EVENTide Sound Preview: Stay-on-Page, Stop Button, Song Looping

### Type
Enhancement

### Summary
Three user-facing improvements to the Sound Editor ↔ EVENTide integration. Preview playback no longer switches tabs (stays on the Event Editor). A dedicated ■ Stop button was added to all sound command widgets. Songs now loop during preview instead of playing once and stopping.

### What's New
- **Preview stays on page**: Clicking ▶ Play on a playbgm/playse/playfanfare widget renders and plays the song in the background without switching to the Sound Editor tab. Previously it switched tabs, losing your place in the event editor.
- **■ Stop button**: All three sound command widgets (playbgm, playse, playfanfare) now have a ■ Stop button that stops audio playback without leaving the Event Editor.
- **Song looping**: Songs now render with configurable loop iterations (default 2) via the `sound/loop_count` setting. Previously played the intro section and stopped after one pass.

### Files Changed
- eventide/ui/event_editor_tab.py — Added ■ Stop buttons, _stop_preview_cb callback
- ui/sound_editor_tab.py — preview_song_by_constant() renders in background without tab switch, loop_count from settings
- ui/unified_mainwindow.py — _sound_preview_song no longer switches pages, added _sound_stop_preview
- ui/dialogs/settingsdialog.py — Loop count setting in Sound Editor page

---

## [2026-04-06] — Sound Editor Phase 6: Integration with PorySuite-Z

### Type
Feature

### Summary
Wired the Sound Editor into the rest of the app. EVENTide's playbgm, playse, and playfanfare command widgets now have Preview (▶) and Open in Sound Editor (🔊) buttons. F8 keyboard shortcut jumps to the Sound Editor. Save pipeline expanded to cover song table, songs.h, and midi.cfg when modified. New "Sound Editor" page in Settings with preview volume, loop count, and sample import defaults. Also fixed the Voicegroups save dropping the `.include "sound/cry_tables.inc"` line that caused build failures.

### What's New
- **EVENTide Preview buttons**: ▶ Play and ■ Stop buttons on playbgm, playse, and playfanfare command widgets — plays/stops the selected song without leaving the Event Editor
- **Open in Sound Editor**: 🔊 button on the same widgets — switches to the Sound Editor and selects that song
- **F8 shortcut**: Press F8 from anywhere to jump to the Sound Editor
- **Save pipeline**: song_table.inc, songs.h, and midi.cfg now save when modified (via `_dirty` flag on SongTableData)
- **Sound Settings page**: New page in Settings dialog with preview volume, loop count, and auto-downsample rate
- **Cry tables fix**: Voicegroups save now preserves `.include` lines from the original file (was dropping `cry_tables.inc` causing linker errors)
- **Song looping**: Songs now render with 2 loop iterations by default (configurable in Settings). Previously played the intro and stopped.
- **Public API**: `preview_song_by_constant()`, `select_song_by_constant()`, `stop_preview()`, `is_playing` on SoundEditorTab for cross-editor use

### Files Changed
- ui/sound_editor_tab.py — Added `preview_song_by_constant()`, `select_song_by_constant()`, `song_table` property
- eventide/ui/event_editor_tab.py — Added preview/open buttons to _PlaySEWidget, _PlayFanfareWidget, _PlayBGMWidget; module-level callbacks
- ui/unified_mainwindow.py — Wired Sound Editor ↔ EVENTide callbacks, expanded save pipeline, F8 shortcut
- ui/dialogs/settingsdialog.py — New "Sound Editor" settings page
- ui/voicegroups_tab.py — Fixed save_to_disk() to preserve .include lines
- porysuite/pokefirered/sound/voice_groups.inc — Restored missing cry_tables.inc include

---

## [2026-04-06] — Sample Import Size Warning & Downsample Option

### Type
Enhancement

### Summary
When importing a WAV sample, the editor now shows the file's size, sample rate, and duration upfront. If the WAV's sample rate is higher than GBA-typical (13379 Hz), it offers downsample options so you don't accidentally fill the ROM with a 44 kHz recording when 13 kHz sounds identical on GBA hardware.

### What's New
- **Import size preview**: Before importing, shows estimated GBA size (e.g. "129.2 KB at 44100 Hz")
- **Downsample menu**: For high-rate WAVs, offers choices like "Keep original (44100 Hz) — 129.2 KB", "Downsample to 22050 Hz — 64.6 KB", "Downsample to 13379 Hz — 26.1 KB"
- **Low-rate pass-through**: WAVs already at GBA-friendly rates (≤13379 Hz) get a simple confirmation with size info
- **`peek_wav_info()` utility**: New function in sample_loader.py reads WAV metadata without converting — returns rate, channels, duration, estimated GBA size
- **`target_rate` parameter**: `import_wav_as_sample()` now accepts a target rate and resamples during import if specified

### Files Changed
- ui/instruments_tab.py — Rewrote `_on_import_sample()` with size/rate dialog and downsample options
- core/sound/sample_loader.py — Added `peek_wav_info()`, added `target_rate` param to `import_wav_as_sample()`

---

## [2026-04-06] — Sound Editor Phase 5: Voicegroups Tab

### Type
Feature

### Summary
Built the full Voicegroups Tab — browse all 77 voicegroups, view/edit all 128 instrument slots per voicegroup, change instrument types (swap square waves for samples, etc.), assign samples/waveforms, edit all parameters (ADSR, base key, pan, duty, sweep, period), add/clone/delete voicegroups, and save to disk. Also fixed "No Map Loaded" error on save when no EVENTide map was open, and added sample rate resampling on Replace so pitch stays correct.

### What's New
- **Voicegroup Browser**: Left panel lists all voicegroups with real (non-filler) slot counts and song usage counts. Search filter. Tooltips show which songs use each voicegroup.
- **128-Slot Grid**: All slots shown with type badge, friendly name, and detail summary. Filter dropdown: All / Non-Filler / Samples / Square / Prog. Wave / Noise / Keysplit. Filler slots greyed out.
- **Voice Type Changer**: Dropdown to change any slot's instrument type (all 13 editable voice types). Type-specific fields show/hide automatically. Defaults assigned when switching types.
- **Sample Picker**: Dropdown of all DirectSoundWaveData samples with info line (rate, length, loop). Appears for DirectSound slots.
- **Wave Picker**: Dropdown of all programmable wave samples. Appears for Prog. Wave slots.
- **Keysplit Editor**: Target voicegroup picker + keysplit table picker. Table combo hidden for keysplit_all.
- **Full Parameter Editor**: Base key spinner, pan spinner, duty cycle dropdown, sweep spinner, period dropdown, ADSR sliders with auto-scaling (CGB vs DirectSound ranges).
- **Voicegroup Management**: Add new (128 filler slots), Clone (deep copy all slots), Delete (blocked if songs reference it).
- **Copy Instrument From VG**: Right-click a slot → copy that slot's instrument definition from another voicegroup.
- **Go to Instrument**: Right-click a slot → jump to the Instruments tab with that instrument selected.
- **Cross-Reference**: "Songs Using This Voicegroup" section shows all songs referencing the selected VG.
- **Save to Disk**: Writes full `voice_groups.inc` with correct assembly syntax for all 15 voice types. Dirty tracking per-voicegroup.
- **Save Pipeline**: Voicegroup changes saved via File → Save alongside all other editors.
- **Sample Resampling on Replace**: Replace now auto-resamples WAV to match original sample rate + preserves loop flag.
- **"No Map Loaded" fix**: Save no longer shows EVENTide error popup when no map is open.
- **Scroll guards**: QSlider added to scroll guard types — all sliders app-wide now require click-focus before scroll wheel changes values.

### Files Changed
- ui/voicegroups_tab.py — NEW: Full Voicegroups Tab (browser, editor, management, save)
- ui/sound_editor_tab.py — Added Voicegroups sub-tab, wired data loading and sample sharing
- ui/unified_mainwindow.py — Voicegroup save in pipeline, EVENTide save guard
- core/sound/sample_loader.py — Added _resample_linear(), updated replace_sample_from_wav() with resampling + loop preservation
- ui/custom_widgets/scroll_guard.py — Added QSlider to guarded types
- ui/instruments_tab.py — Scroll guards on ADSR sliders, updated Replace tooltip

---

## [2026-04-06] — Sound Editor: Piano Octave Controls & Go to Instrument

### Type
Enhancement

### Summary
Added octave shift controls to the piano keyboard (was stuck at C3-B5, now you can shift up/down freely) and a right-click "Go to Instrument" option on the Songs tab track list that jumps you directly to that instrument in the Instruments tab.

### What's New
- **Piano octave shift**: Arrow buttons (◀/▶) flanking the keyboard shift it up or down one octave at a time. Range label updates to show the current span (e.g. "C5 – B7"). Clamped to MIDI 0-127.
- **Right-click "Go to Instrument"**: In the Songs tab, right-click any track to see a "Go to Instrument" option. Clicking it switches to the Instruments tab and highlights that instrument. Works via identity-key matching so it finds the right deduplicated entry even across shared voicegroups.
- **`select_instrument()` public API**: New method on InstrumentsTab for programmatic instrument selection by voicegroup name + slot index.

### Files Changed
- ui/instruments_tab.py — Added `set_start_octave()`, `start_octave` property, octave buttons, range label, `_on_octave_down/up`, `_update_piano_range_label`, `select_instrument()` method
- ui/sound_editor_tab.py — Added QMenu/QAction imports, track context menu with "Go to Instrument", stored voicegroup/slot data on track tree items

---

## [2026-04-06] — Sound Editor: Tooltips & Guidance Pass

### Type
Enhancement

### Summary
Added comprehensive tooltips and guidance text across the entire Instruments Tab. Every interactive control now has a tooltip explaining what it does in plain English — especially important for the Import button which explains WAV file requirements (must be mono, any bit depth, any sample rate, shorter is better for GBA).

### What's New
- **Import button tooltip**: Full requirements — mono only, supported bit depths (8/16/24/32-bit int, 32/64-bit float), typical GBA sample rates (8000–22050 Hz), note about GBA memory limits, loop point detection
- **Export button tooltip**: Explains output format (8-bit mono WAV)
- **Replace button tooltip**: Clarifies it keeps the label and all references
- **Delete button tooltip**: Explains what gets removed (.bin file + .inc entry)
- **ADSR slider tooltips**: Each slider now explains what the parameter controls and shows both scale ranges (synth 0–7 vs sample 0–255)
- **ADSR group box tooltip**: Plain English explanation of the volume envelope concept
- **Duty Cycle tooltip**: Describes each percentage and its sound character
- **Sweep tooltip**: Explains the pitch slide effect (square_1 only)
- **Period tooltip**: Describes white noise vs metallic noise
- **Pan tooltip**: Expanded with full range description
- **Play button tooltip**: Explains what it does and mentions the piano alternative
- **Piano keyboard tooltip + hint label**: "Click any key to preview at that pitch (C3–B5)"
- **Search box tooltip**: Explains it filters across all type groups
- **Instrument Details group tooltip**: Notes that edits apply to all shared copies
- **Used By group tooltip**: Explains cross-voicegroup sharing

### Files Changed
- ui/instruments_tab.py — Added/improved tooltips on all interactive controls, added piano hint label

---

## [2026-04-06] — Sound Editor Phase 4.4: Sample Management

### Type
Feature

### Summary
Added sample management features to the Instruments Tab — export samples to WAV, import new samples from WAV (using wav2agb), replace existing sample audio, and delete unused samples with reference checking. Also added a UI hint explaining that Base Key doesn't change pitch for sample-based instruments.

### What's New
- **Export to WAV**: Save any DirectSound sample as a standard .wav file (converts signed 8-bit GBA format to unsigned 8-bit WAV)
- **Import from WAV**: Pick a .wav file, name it, and wav2agb converts it to GBA .bin format — automatically added to `direct_sound_data.inc` and the sample library
- **Replace sample**: Swap a sample's audio with a new .wav file while keeping the same label and all voicegroup references intact (backs up the original during conversion)
- **Delete sample**: Remove an unused sample — checks all voicegroups first and blocks deletion if any instrument still references it
- **Reference checker**: `get_sample_references()` finds every voicegroup slot that uses a given sample
- **Base Key hint**: When viewing a sample instrument, a note explains that pitch comes from the sample file itself, not the Base Key value

### Files Changed
- core/sound/sample_loader.py — Added: `export_sample_to_wav()`, `import_wav_as_sample()`, `replace_sample_from_wav()`, `delete_sample()`, `get_sample_references()`, `_find_wav2agb()`, `_remove_inc_entry()`
- ui/instruments_tab.py — Added: Export WAV / Replace / Delete buttons on sample instruments, Import New Sample button always visible, Base Key hint label, QFileDialog/QMessageBox/QInputDialog imports

### Notes
- wav2agb is expected in the project at `tools/wav2agb/wav2agb.exe` (Windows) or `tools/wav2agb/wav2agb` (Linux)
- Replace creates a `.bak` backup of the original .bin during conversion, cleaned up on success or restored on failure
- Delete only works for unreferenced samples — the UI shows which voicegroups are blocking deletion
- Programmable wave import (visual editor) deferred to a later phase

---

## [2026-04-06] — Sound Editor Phase 4: Instruments Tab

### Type
Feature

### Summary
Added the Instruments Tab to the Sound Editor — a browsable list of all 7,841 instruments across 77 voicegroups with filtering, detail view, ADSR envelope visualization, and a clickable 3-octave piano keyboard for previewing any instrument at any pitch.

### What's New
- **Sub-tab bar**: Sound Editor now has "Songs" and "Instruments" sub-tabs (was a flat Songs-only page)
- **Instrument browser**: Scrollable list showing every instrument in every voicegroup — columns for name, type, voicegroup number, and slot index
- **Three filters**: Filter by voicegroup, by type (Sample / Square Wave / Prog. Wave / Noise / Keysplit), or search by name
- **Detail panel**: Shows instrument type, base key, pan, voice macro, and type-specific info (sample name + rate + loop for DirectSound; duty cycle + sweep for square waves; waveform label for prog. wave; period for noise; target voicegroup for keysplits)
- **ADSR envelope display**: Visual curve showing the Attack / Decay / Sustain / Release shape, with numeric readouts (auto-detects CGB 0-7/0-15 vs DirectSound 0-255 scale)
- **"Used By" cross-reference**: Shows which other voicegroups contain the same sample or synth definition
- **Piano keyboard preview**: Click any key on a 3-octave piano (C3–B5) to hear the instrument at that pitch. Also a "Play Note" button for middle C. Renders in a background thread so the UI stays responsive.

### Files Changed
- ui/sound_editor_tab.py — Restructured: added QTabWidget with "Songs" and "Instruments" sub-tabs, passes voicegroup/sample data to instruments tab, wires modified signal
- ui/instruments_tab.py — **New file**: InstrumentsTab widget (grouped unique-instrument browser, editable detail panel with ADSR sliders, type-specific controls, cross-voicegroup edit propagation), PianoKeyboard widget, ADSRDisplay widget

### Notes
- All 18 existing audio tests still pass
- 144 unique instruments shown (deduplicated from 7,841 total slots, filler removed)
- Grouped by type: Samples (89), Square Waves (32), Prog. Waves (11), Noise (2), Keysplits (10)
- Preview playback lazy-loads sample data on first use (same pattern as Songs tab)
- Editing an instrument updates every copy across all voicegroups that share it
- File writing (voice_groups.inc) deferred to Phase 6 save pipeline

---

## [2026-04-06] — Sound Editor: XCMD Parser Fix, Fixed-Pitch Instruments, Drum Overflow

### Type
Bugfix

### Summary
Fixed three critical audio bugs: XCMD continuation lines creating phantom key-shift commands (affected ~85 songs), `_alt` voice types ignoring the fixed-pitch flag (406 percussion instruments pitch-shifted when they shouldn't be), and keysplit_all overflow making drum tracks silent when note indices exceeded voicegroup bounds.

### Problems Fixed
1. **XCMD continuation lines misparse** — Multi-line XCMD instructions (`.byte xIECL, 8` on a continuation line) fell through to the running-status handler. `xIECL` resolved to integer 9, then was emitted as the last control command (usually KEYSH), creating phantom KEYSH=9/8 that shifted all subsequent notes up 8-16 semitones. This broke mus_gym, mus_encounter_gym_leader, and ~83 other songs. Fixed by recognizing XCMD sub-command tokens before the running-status handler.
2. **_alt voice types (TONEDATA_TYPE_FIX) investigated** — Initially assumed 406 CGB _alt instruments needed fixed pitch. Applied blanket fix that caused REGRESSION (monotone flat sounds). After tracing GBA source: TONEDATA_TYPE_FIX only means "fixed pitch" for DirectSound channels (already handled by `no_resample`). For CGB channels it only does minor frequency rounding for anti-aliasing — pitch still varies normally. Fix REVERTED. CGB _alt instruments now correctly pitch-shift.
3. **keysplit_all overflow** — GBA stores voicegroups contiguously, so keysplit_all (which indexes by MIDI note) can overflow past the target voicegroup into the next one. voicegroup001 has 29 slots; drum note 40 should reach voicegroup002[11] (orchestra_snare) but our parser returned None (silent). Fixed by adding `get_instrument_overflow()` that chains into subsequent voicegroups.

### Files Changed
- core/sound/song_parser.py — Added XCMD sub-command token recognition (xIECV, xIECL) to skip continuation lines
- core/sound/audio_engine.py — Added TONEDATA_TYPE_FIX check (fixed-pitch for _alt instruments); updated keysplit_all to use overflow lookup
- core/sound/voicegroup_parser.py — Added `get_instrument_overflow()` method to VoicegroupData for GBA-style contiguous memory indexing

### Notes
- All 18 tests pass
- mus_gym: drum track now renders (peak 0.38 vs silence), no more phantom KEYSH
- mus_encounter_gym_leader: no more phantom KEYSH=16, notes at correct pitch

---

## [2026-04-06] — Sound Editor: Audio Accuracy Fixes (Volume, Pitch, Pan, Parser)

### Type
Bugfix

### Summary
Fixed multiple audio accuracy bugs in the Sound Editor's rendering engine. Songs now play with correct volume balance, proper pitch (no more wrong-key instruments), correct stereo panning matching GBA hardware, and no missing notes from unparsed running-status bytes.

### Problems Fixed
1. **Double velocity + wrong master volume** — Velocity was applied twice (once in track_renderer, again in audio_engine). The song's `mvl` constant was also multiplied in again, but it's already baked into VOL command values in the source (written as `VOL, n*mvl/mxv`). Combined effect: some instruments were at ~18% of correct volume.
2. **Fake CGB volume reduction factors** — Square waves (×0.3), programmable waves (×0.25), and noise (×0.2) had arbitrary reduction multipliers that don't exist on GBA hardware. The GBA runs the same volume pipeline for all channel types.
3. **Constant-power panning instead of GBA linear crossfade** — `apply_pan` used `cos/sin` but GBA uses a simple linear split: `left = (127 - pan) / 255`, `right = (pan + 128) / 255`.
4. **Per-track panning instead of per-note** — Only the first PAN command per track was used for the entire track. Mid-song PAN changes were tracked in state but never applied. Now each note is panned at the current pan value.
5. **BEND command not subtracting C_V** (prior session) — GBA's `ply_bend` subtracts 64 before storing. With BENDR=12 (Lavender Town), this caused +6 semitone offset on all notes.
6. **Running status bytes dropped** (prior session) — Bare integer values on `.byte` lines (VOL running status) were stored as unused CONTINUATION commands, causing notes to play at VOL=0 (silent). Fixed by tracking `last_control_cmd`.

### Files Changed
- core/sound/audio_engine.py — Removed ×0.3/×0.25/×0.2 CGB volume factors; changed `apply_pan` from constant-power to GBA linear crossfade
- core/sound/track_renderer.py — Removed double velocity application; removed master_volume multiplication; `render_track` now returns stereo with per-note panning; `render_song` simplified (no more per-track pan pass)
- tests/test_track_renderer.py — Updated for stereo return from `render_track`

### Notes
- All 18 tests pass
- Rendered test WAVs: mus_victory_trainer (30.6s), mus_vs_wild (77s), mus_vs_trainer (185s), mus_gym (68s), mus_lavender (158s)

---

## [2026-04-06] — Sound Editor Phase 3 (Start): Songs Tab Integrated into PorySuite-Z

### Type
Feature

### Summary
Added the Sound Editor as a new toolbar page in PorySuite-Z. Users can now browse all 347 songs, view details (voicegroup, tempo, tracks, loop points), and play songs directly in the app using the project's actual instruments.

### Files Changed
- ui/sound_editor_tab.py (new) — Full Songs browser with playback controls
- ui/unified_mainwindow.py (modified) — Added "Sound Editor" toolbar button and page, data loading on project open
- res/icons/toolbar/sound.png (new) — Music note toolbar icon

### Notes
- Song list supports filtering by type (BGM/SE) and text search
- Clicking a song parses its .s file on demand and shows: voicegroup, track count, tempo, reverb, volume, loop points
- Track list shows per-track info: MIDI channel, note count, first instrument name
- Play button renders the song in a background thread then streams via sounddevice
- Transport: Play/Pause/Resume/Stop with volume slider and time display

---

## [2026-04-06] — Sound Editor Phase 2: Audio Playback Engine

### Type
Feature

### Summary
Built the complete audio rendering pipeline for the Sound Editor. Can now render any song from the project to playable audio using the actual instruments — DirectSound samples with pitch shifting and looping, square wave synthesis, programmable wave synthesis, noise generation, ADSR envelopes, stereo panning, and reverb.

### Files Changed
- core/sound/audio_engine.py (new) — All synthesis: DirectSound PCM, square wave, prog. wave, noise, ADSR, reverb, panning, AudioPlayer
- core/sound/track_renderer.py (new) — Track state machine, tick timing, song mixer, instrument preview
- tests/test_audio_engine.py (new) — 14 unit + integration tests
- tests/test_track_renderer.py (new) — 4 integration tests (full song renders)
- tests/test_playback.py (new) — End-to-end WAV output test
- docs/SOUND_EDITOR_PLAN.md (updated) — Phase 2 marked COMPLETE

### Notes
- mus_cycling (8 tracks, 34.6s) renders in ~1.9s, mus_pallet (6 tracks, 44s) in ~2.6s
- New dependencies: sounddevice 0.5.5, numpy 2.4.4
- Phase 3 (Songs Tab UI) is next

---

## [2026-04-06] — Sound Editor Phase 1: Core Parsing Engine

### Type
Feature

### Summary
Built the complete parsing backend for the Sound Editor — reads all music data from the pokefirered project into Python data structures. This is the foundation for the full music editor that will be integrated into PorySuite-Z.

### Files Changed
- core/sound/__init__.py (new)
- core/sound/sound_constants.py (new) — GBA M4A bytecode constants, note names, voice types
- core/sound/song_parser.py (new) — .s song file parser (tracks, commands, loop points)
- core/sound/song_table_manager.py (new) — song_table.inc + songs.h + midi.cfg reader/writer
- core/sound/voicegroup_parser.py (new) — voice_groups.inc + keysplit_tables.inc parser
- core/sound/sample_loader.py (new) — .bin sample loader with GBA WaveData header decoding
- docs/SOUND_EDITOR_PLAN.md (new) — full 9-phase roadmap for the Sound Editor

### Notes
- Tested against live pokefirered data: 347 songs, 77 voicegroups, 89 instrument samples, 5 keysplit tables
- Discovered .bin sample format uses fixed-point frequency (Hz = raw >> 10) and 0x4000 status flag for looping
- Phase 2 (audio playback engine) is next

---

## [2026-04-06] — Overworld Editor Overhaul: Sprite-First UI, Animation Fixes, Add New Sprites, Cross-Tab Sync

### Type
Feature / Enhancement

### Summary
Major overhaul of the Overworld Graphics editor across multiple areas:

**UI restructured to sprite-first layout:**
- Left panel now shows all sprites in a searchable, filterable thumbnail grid
  (category dropdown + search bar) instead of grouping by palette pool.
- Right panel split: sprite sheet + animation preview on top, palette editor
  on bottom. Clicking any sprite in the grid loads its detail and palette.
- Blue highlight on the selected sprite in the grid.

**Animation type-aware previews (fixed):**
- **Fishing sprites:** Now show all 4 directional rod animations (South, West,
  North, East=West mirrored) instead of a single Cast/Hold/Reel view.
- **Surf sprites:** Two-row display — Row 1 shows static directional poses,
  Row 2 shows the surf run cycle (stand→step1→stand→step2 per direction).
  Second row only appears when the sheet has 12+ frames.
- **VS Seeker sprites:** Shows the actual raise animation sequence
  (0→1→5→6→7→8→6→1→0) instead of incorrectly treating it as a walk cycle.
- Animation speed matched to GBA (~150ms per frame, up from 333ms).

**Add New Overworld Sprite:**
- "+ Add New Sprite…" button in the left panel opens a dialog to create a new
  overworld sprite from a PNG sheet.
- Auto-detects frame size, name, and palette from the PNG.
- Choose animation type (Walk Cycle, Static, Player-style), category
  (People, Pokemon, Misc), and palette assignment.
- Palette choice: "Create new from PNG" (if DOWP enabled) or pick from 4 NPC
  palette slots (Blue/Pink/Green/White).
- Writes all 6 C header/source files automatically: event_objects.h,
  object_event_graphics.h, object_event_pic_tables.h,
  object_event_graphics_info.h, object_event_graphics_info_pointers.h,
  event_object_movement.c.
- New `OBJ_EVENT_GFX_` constant is pushed into EVENTide's ConstantsManager
  immediately — the Event Editor's graphic dropdown picks up new sprites
  without needing a save or refresh.

**Palette reassignment:**
- "Assign to" dropdown + Apply button in the Palette section lets you change
  which palette a sprite uses. Modifies the C source directly.

**Dynamic Overworld Palettes (DOWP):**
- One-way patch button to enable per-sprite palettes in the C engine.
- Modifies 5 source files (event_object_movement.c, field_effect.c,
  field_effect_helpers.c, event_object_movement.h, field_effect.h).
- Status indicator shows whether DOWP is active.

**Cross-tab sync (Overworld → EVENTide):**
- New `gfx_constants_changed` signal on the overworld tab.
- Wired through `UnifiedMainWindow` to call `_refresh_eventide_constants()`.
- Event editor's `refresh_gfx_constants()` repopulates the graphic dropdown.
- Same pattern as trainers/items/species cross-tab sync.

**Other improvements:**
- "Show in Folder" button opens Explorer with the sprite PNG selected
  (`explorer /select,...`) instead of just opening the folder.
- Overworld GFX toolbar button added to unified window.
- App opens maximized reliably (QTimer.singleShot workaround).

**Project Display Name setting:**
- New "Project" section at the top of Settings → General page.
- "Display Name" field controls what shows in the launcher and window title.
- Persisted to both `projects.json` and per-project `config.json`.

### Files modified
- `ui/overworld_graphics_tab.py` — restructured to sprite-first layout,
  animation type dispatching (surf/fish/VS Seeker/inanimate/destroy),
  two-row surf preview, Add New Sprite dialog, palette reassignment,
  Show in Folder, cross-tab GFX signal
- `core/overworld_sprite_creator.py` — **new file**, backend for adding new
  overworld sprites (all 6 C file modifications)
- `core/dynamic_ow_pal_patch.py` — **new file**, DOWP patch engine
- `ui/unified_mainwindow.py` — overworld tab in toolbar, GFX signal wiring,
  project name save, maximize fix, DOWP constants refresh
- `ui/dialogs/settingsdialog.py` — project display name field
- `eventide/ui/event_editor_tab.py` — `_refresh_gfx_combo()` and
  `refresh_gfx_constants()` public method for cross-tab sync
- `eventide/ui/widgets.py` — animation speed 333ms → 150ms
- `app.py` — maximize fix (QTimer.singleShot)
- `res/icons/toolbar/overworld.png` — **new file**, toolbar icon


## [2026-04-06] — Overworld Graphics Tab (Initial)

### Type
Feature

### Summary
**New top-level "Overworld GFX" tab in the main toolbar**, providing a full
overworld sprite and palette management interface.

Features:
- **Left panel — Palette pool list:** Shows all shared overworld palettes
  (Player Red, NPC Blue, NPC Pink, NPC Green, NPC White, Seagallop, SS Anne,
  etc.) with a sprite count beside each pool. Selecting a pool shows its
  editable 16-colour `PaletteSwatchRow`. "Import from PNG" extracts colours
  from an indexed PNG and GBA-clamps to 15-bit. "Open Folder" opens the
  palettes directory in the OS file manager.
- **Right panel — Sprite browser:** Category filter dropdown
  (All / Players & NPCs / Pokemon / Objects & Items) plus a search box.
  Scrollable grid of sprite thumbnails rendered with the current palette —
  all sprites sharing the selected palette update live when a colour is edited.
- **Sprite detail area (bottom of right panel):** Click any sprite to see:
  - Full sprite sheet (PNG scaled up)
  - 4-direction walk animation (Down, Left, Up, Right) with proper frame
    extraction: 9-frame sheets get a full walk cycle
    (stand → step1 → stand → step2) per direction; right direction is left
    mirrored (matching GBA engine); 3-frame sheets show directional stands;
    single-frame sprites display as static in all directions
  - Sprite info: name, constant, frame dimensions, palette tag
  - "Open Sprite Folder" button
- **Proper INCBIN chain resolution** — sprites are resolved by following the
  full C header chain: `object_event_graphics_info_pointers.h` →
  `object_event_graphics_info.h` → `.images = sPicTable_*` →
  `object_event_pic_tables.h` → `gObjectEventPic_*` →
  `object_event_graphics.h` → INCBIN path → PNG. Falls back to simple slug
  matching if the chain doesn't resolve. This fixed 7 sprites that had
  filename mismatches (e.g. PUSHABLE_BOULDER → strength_boulder.png,
  METEORITE → birth_island_stone.png). Total sprites: 152 (up from 145).
- **Accurate frame detection for walk animation** — the 4-direction walk
  preview now uses the actual frame dimensions from ObjectEventGraphicsInfo
  (width/height fields) instead of guessing from the sheet. This fixes
  32×32 bike sprites (288×32 sheets) that were incorrectly detected as
  18 frames of 16px instead of 9 frames of 32px.
- **Debounced grid refresh** — when editing palette swatches, the sprite grid
  no longer rebuilds on every single click. The selected sprite detail
  updates immediately while the full grid refresh is debounced to 400ms.
  This prevents UI stutter when rapidly editing colours in a pool with
  50+ sprites.
- **Data sources:** Parsed from C headers — `object_event_graphics_info.h`,
  `object_event_graphics_info_pointers.h`, `object_event_pic_tables.h`,
  `object_event_graphics.h`, `event_object_movement.c`, `event_objects.h`.
  10 palette pools detected, 152 sprites mapped in testing.
- **Save:** Palette changes saved on File → Save via `flush_to_disk()`.

### Files modified
- `ui/overworld_graphics_tab.py` — **new file**, `OverworldGraphicsTab` widget
  with palette pool list, sprite browser, detail view, animation preview,
  import, and save logic.
- `ui/mainwindow.py` — imports `OverworldGraphicsTab`, creates instance as a
  top-level tab "Overworld GFX", loads on project open, saves via
  `_save_overworld_graphics()` in all three save paths.

### Testing
1. Open a project. You should see a new "Overworld GFX" tab in the main
   toolbar.
2. Click "Overworld GFX" — the left panel should list palette pools with
   sprite counts (e.g. "Player Red (3)", "NPC Blue (28)").
3. Click a palette pool — its 16-colour swatch row should appear below. Click
   any swatch to open the colour picker, change a colour, and confirm — all
   sprite thumbnails in the right panel that share that palette should update
   immediately.
4. Use the category dropdown to filter sprites (e.g. "Pokemon"). The grid
   should show only matching sprites.
5. Type in the search box — the grid should filter as you type.
6. Click any sprite thumbnail — the bottom detail area should show the full
   sprite sheet, 4-direction walk animation, and sprite info.
7. Click "Import from PNG" — pick an indexed PNG — the palette swatches and
   all linked sprite thumbnails should update.
8. File → Save — the palette `.pal` file on disk should contain the new
   colours.


## [2026-04-06] — Trainer Graphics Tab

### Type
Feature

### Summary
**New "Graphics" sub-tab added to the Trainers section**, sitting alongside the
existing "Trainers" and "Trainer Classes" tabs. This gives trainers the same
palette-editing workflow that Pokemon already had.

Features:
- Dropdown to select any TRAINER_PIC_* constant (friendly display names shown,
  e.g. "Lass", "Ranger"). Combo box wheel-scroll disabled per project rules.
- 128×160 sprite preview rendered with the current palette applied via
  `_reskin_indexed_png` — changes are visible immediately.
- Editable 16-colour palette swatch row (reuses `PaletteSwatchRow` from the
  Pokemon Graphics tab).
- "Import Palette from PNG" button — extracts the colour table from an indexed
  PNG, GBA-clamps to 15-bit, and applies it to the palette. File picker
  defaults to the trainer's palettes folder (`graphics/trainers/palettes/`).
- "Open Palettes Folder" button for quick OS file-manager access.
- Palette edits refresh the sprite preview immediately.
- Changes saved on File → Save via `flush_to_disk()` which writes JASC-PAL
  `.pal` files.

### Files modified
- `ui/trainer_graphics_tab.py` — **new file**, `TrainerGraphicsTab` widget with
  all sprite preview, palette swatch, import, and save logic.
- `ui/mainwindow.py` — imports `TrainerGraphicsTab`, creates instance, connects
  `modified` signal, adds it as the 3rd tab in the trainers tab switcher, loads
  it with `pic_map` from the trainers editor on project load, saves via
  `_save_trainer_graphics()` in all three save paths.

### Testing
1. Open a project and go to the Trainers toolbar page.
2. You should see three sub-tabs at the top: "Trainers", "Trainer Classes",
   and "Graphics".
3. Click "Graphics".
4. Use the dropdown to pick a trainer pic (e.g. "Lass") — a 128×160 sprite
   preview should appear with its current palette.
5. Click any colour swatch in the palette row — the colour picker should open.
   Pick a new colour, close the picker — the sprite preview should update
   immediately.
6. Click "Import Palette from PNG", pick an indexed PNG from the palettes
   folder — the palette swatches and sprite preview should update.
7. Try scrolling the mouse wheel over the dropdown without clicking it open
   first — the value must NOT change (wheel-scroll is blocked).
8. File → Save — the `.pal` file on disk should contain the new palette.


---

_Older entries (before 2026-04-06) were trimmed on 2026-04-09 to keep this file readable._
