## [2026-04-13] — Tilemap Editor Fixes + Tile Animation Editor Polish

### Type
Fix / Feature

### Summary
**Tile Animation Editor:** Collapsible Frame Thumbnails and Tile Grid sections now truly collapse to zero height (size policy changes to Fixed when collapsed, reclaims space for preview). Removed splitter gap between left and right panels — replaced with fixed-width left panel + stretching right panel via plain QHBoxLayout. Removed "Save All to C Source" button — property changes now follow the app's normal save pipeline (mark dirty → File → Save writes to tileset_anims.c via flush_to_disk()). All 5 property spinners connected to dirty-marking.

**Tilemap Editor:** Double-click any palette color swatch to edit it with a color picker (GBA 15-bit clamped). File dialog now remembers the last-opened directory within a session instead of always resetting to graphics/. Palette auto-discovery now only loads name-matching .pal files by default (e.g. solarbeam.bin → solarbeam.pal) — non-matching .pal files in the same directory are no longer auto-loaded, so PNG colors (almost always correct) are used as the default when there's no dedicated .pal file. When falling back to PNG colors, the palette source combo auto-switches to show "PNG colors". Tilemap .bin save now integrated into File → Save pipeline (flush_to_disk + has_unsaved_changes). Paint actions emit modified() to mark the window dirty.

### Files Changed
- ui/tile_anim_viewer.py — _CollapsibleSection uses setFixedHeight/setSizePolicy on collapse, splitter replaced with QHBoxLayout, save button removed, flush_to_disk()/has_unsaved_changes() added, all property spinners wired to _mark_props_dirty()
- ui/tilemap_editor_tab.py — PaletteEditorWidget.mouseDoubleClickEvent with QColorDialog + GBA clamping, _last_open_dir for file dialog memory, flush_to_disk()/has_unsaved_changes() added, modified.emit() on paint
- ui/unified_mainwindow.py — Tile animation + tilemap save wired into _on_save_all(), modified signals connected to setWindowModified
- core/tilemap_data.py — discover_assets() only auto-loads name-matching .pal files (non-matching excluded from best_pals)

---

## [2026-04-13] — Phase 10B: Tile Animation Editor — AnimEdit-Style Rework

### Type
Feature / Rework

### Summary
Complete rewrite of the Tile Animation Editor to match the AnimEdit binary hacking tool's workflow. Navigation is now by Tileset + Animation Number (68 tilesets from headers.h, animated-first sorting). ALL animation properties are editable and save back to C source: Speed/Divisor, Start Tile (hex, matching Porymap convention like 0x1A0), Tile Amount, Phase, Counter Max. Palette integration loads all 16 tileset .pal files with GBA 15-bit clamping, editable swatches that write back to .pal files (shared with Porymap). Add New Animation creates full C source wiring (INCBIN, frame array, QueueAnimTiles, dispatch, Init, headers.h callback). Remove Animation cleanly strips all references. Side-by-side splitter layout (410px left panel for controls, right panel for preview/grid). Display size control (1x/2x/4x/8x). Frame scrubber with prev/slider/next for manual stepping. Tile Grid shows 8×8 tile decomposition with hex VRAM addresses. Horizontal/grid toggle for tile layout. Dynamic discovery — works on any pokefirered project.

### Files Changed
- core/tileset_anim_data.py — NEW: `parse_tilesets_from_headers()` (68 tilesets from headers.h), `parse_palette_hints()`, `write_start_tile_to_source()`, `write_tile_amount_to_source()`, `write_phase_to_source()`, `write_counter_max_to_source()`, `add_animation_to_tileset()` (full C source wiring), `remove_animation_from_tileset()`, `load_tileset_palettes()`. TileAnimation gains `palette_hint` and `dispatch_func` fields.
- ui/tile_anim_viewer.py — COMPLETE REWRITE: `TileAnimEditorWidget` replaces old viewer. New widgets: `_HexSpinBox` (hex display/input), `PaletteSwatch`/`PaletteSwatchRow` (editable color swatches), `AnimPreviewWidget`, `TileGridWidget` (hex VRAM labels, horizontal toggle), `_AddAnimDialog`. Tileset+animation navigation, property editing panel, palette slot selector with import/export, filmstrip, frame scrubber, tile grid with layout toggle, add/remove animation buttons.
- ui/tilemap_editor_tab.py — Updated import to TileAnimEditorWidget

---

## [2026-04-13] — Phase 10B: Tile Animation Editor — All 77 Animations

### Type
Feature

### Summary
Full Tile Animation Editor tab alongside the Tilemap Editor. Three complete parsers cover **all GBA animation systems** in pokefirered — 77 animations total (8 tileset, 32 door, 37 field effect), all discovered dynamically from source with no hardcoded names.

**Three animation categories** in a grouped dropdown:
- **Tileset BG Animations (8)** — Parsed from `src/tileset_anims.c`. INCBIN_U16 frame PNGs with timing from dispatch functions. Full editing: replace/add/delete frames, save timing to C source, palette editing.
- **Door Animations (32)** — Parsed from `src/field_door.c`. INCBIN_U8 spritesheets split into 3 frames. Shows metatile ID, sound type (normal/sliding), door size (1x1/1x2), palette numbers. Palette editing supported.
- **Field Effect Animations (37)** — Parsed from `object_event_graphics.h` + `field_effect_objects.h`. Spritesheets split by `overworld_frame` dimensions. Shows ANIMCMD_FRAME sequences where available (duration ticks per frame). Includes tall grass, dust, splashes, shadows, footprints, bubbles, arrows, and more.

**Viewer features**: Filmstrip (sequence order, ping-pong for tileset, ANIMCMD order for field effects), animated preview at correct GBA timing, speed slider (10%-400%), preview/filmstrip scale controls, 16-field info panel adapts to each animation type.

**Editor features**: Open in Explorer (frame directory for tilesets, spritesheet file for doors/field effects). Palette display with clickable color swatches and GBA 15-bit clamping — works across all types. Import .pal, Export .pal, Import palette from PNG. Timer divisor editing (tileset anims only). Frame management (tileset anims only — doors/field effects use spritesheets edited externally). Right-click context menu. Confirmation dialogs on all destructive actions.

### Files Changed
- core/tileset_anim_data.py — Three parsers: `parse_tileset_anims()` (8 BG anims from tileset_anims.c), `parse_door_anims()` (32 doors from field_door.c), `parse_field_effect_anims()` (37 effects from object_event_graphics.h + field_effect_objects.h). `DoorAnimation` and `FieldEffectAnimation` dataclasses. Writer functions for tileset timing/frames.
- ui/tile_anim_viewer.py — Grouped dropdown with category separators. `_split_spritesheet()` splits door/field effect PNGs into individual frames. `AnimInfoPanel.set_animation()` adapts display fields to each type. Type-aware palette editing (per-frame PNGs for tilesets, single spritesheet for doors/field effects). Guards on tileset-only operations (timing save, frame add/delete/replace).
- ui/tilemap_editor_tab.py — QTabWidget wrapper: "Tilemap Editor" + "Tile Animations" tabs.

---

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

_Older entries (before 2026-04-08) were trimmed on 2026-04-13 to keep this file readable._
