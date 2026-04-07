# EVENTide — Context for New Threads

## What is EVENTide?
A visual event/script editor for pokefirered decomp projects, modeled after RPG Maker XP's event editor. Part of the PorySuite family — launched from PorySuite-Z's project selector.

## Key Architecture Decisions

### All Event Types Load
The editor loads ALL event types from map.json, not just object_events:
- **object_events** → NPCs (shown as `[NPC]` in dropdown)
- **coord_events** → Step-on triggers (shown as `[Trigger]`)
- **bg_events** → Signs (shown as `[Sign]`) and hidden items (shown as `[Hidden Item]` with item name). Hidden items swap the command list for a dedicated property editor panel.
- **map_scripts** → On-transition/on-frame scripts from scripts.inc (shown as `[MapScript]`)

### Porymap Integration
The Event Editor has bridge API methods for Porymap communication:
- `navigate_to_map(map_name)` — loads a map if not already loaded
- `select_event_by_bridge(event_type, event_index, script_label)` — selects an event by script label or type+index matching
- `select_event_at_position(x, y)` — Manhattan distance search for closest event
- `reload_current_map()` — reloads from disk after Porymap saves
- "Open in Porymap" button on the toolbar launches Porymap at the current map

This was a critical fix. Previously only object_events loaded, which meant Oak's scripts in Pallet Town were invisible (his real scripts are on coord_events, not on his object_event which has `script: "0x0"`).

### RMXP-Style Display
Commands show as a flat text list (`@>Text: Hello world`), NOT inline form widgets. Double-click opens an edit dialog popup. This matches RPG Maker XP's visual style.

### Sub-Label Page Tabs
When a script has `goto`/`call` targets, those labels become clickable tabs. BFS from the entry label collects all reachable sub-labels.

### Text Loading is Project-Wide
`parse_all_texts()` searches map-local `text.inc` AND `data/text/*.inc` (sign_lady, fame_checker, etc.).

### Self-Verification
After every map load, `_verify_loaded_events()` checks for:
- Scripts referenced but not found in scripts.inc
- Orphaned labels nobody references
- Empty maps with no events
- Count mismatches between map.json and loaded data
All warnings appear in the log panel.

## Important Files
- `eventide/ui/event_editor_tab.py` — Main editor (~9200+ lines). Has the stringizer, 82+ command widgets, edit dialogs, page building, load/save, color scheme, position overrides, cross-references, script lookup, hidden item editor, tooltips.
- `eventide/ui/script_search_dialog.py` — Project-wide script label search dialog (Ctrl+Shift+F).
- `eventide/backend/script_index.py` — Project-wide label index (ScriptIndex class). Scans all scripts.inc + data/scripts/ for labels on project load.
- `eventide/backend/eventide_utils.py` — Script parser, text parser, save writers.
- `eventide/backend/constants_manager.py` — Loads all project constants from header files.
- `eventide/mainwindow.py` — Tab setup. Event Editor is the leftmost/default tab.

## Navigation
- **Go To → button**: Follows goto/call/conditional/trainerbattle targets to their destination script. Works across page tabs and across events. Also available directly in command edit dialogs — any popup for a label-referencing command (call, goto, call_if_*, goto_if_*) has a Go To button that saves edits and navigates.
- `_extract_goto_target()` pulls the target label from any navigable command type.
- **Find in commands** (Ctrl+F): Inline search bar with highlight, Next/Prev, match count, wrap-around.

## Command List Interaction
- **Drag-to-reorder**: `_DraggableCommandList` subclass enables InternalMove drag-drop. `rows_reordered` signal → `_on_rows_moved()` rebuilds `_cmd_tuples` from item data.
- **Right-click context menu**: Edit, Cut, Copy, Paste, Duplicate, Move Up/Down, Insert, Delete, Go To →
- **Command selector**: 3-page tabbed dialog with "Recent" row (last 8 commands, session-persistent via `_RECENT_COMMANDS` module-level list).
- If a target exists in `_all_scripts` but wasn't loaded as a page tab, it gets dynamically added.

## Trainer Battles
- The parser preserves variant names: `trainerbattle_single`, `trainerbattle_no_intro`, `trainerbattle_earlyrival`, `trainerbattle_double`, plus `trainerbattle_rematch` and `trainerbattle_rematch_double` for VS Seeker rematches.
- The stringizer shows intro/defeat text labels and continue script on separate lines.
- The `__texts__` key in `_ALL_SCRIPTS` gives the stringizer access to text content for inline preview.

## Cross-Editor Live Bridge (2026-04-05)
The Event Editor exposes its in-memory state so other tabs (Trainers → Dialogue, etc.) can see unsaved edits without requiring a save.

- **`_sync_live_script_state()`** — called from `_mark_dirty()` on every script mutation. Commits `_cmd_tuples` → active page dict, then mirrors every page of the current event into `self._all_scripts[label]`. Idempotent.
- **`_ALL_SCRIPTS` (module-level)** — same dict object as `self._all_scripts` (aliased at map-load time on line ~6556). Other tabs import this and read from it.
- **`_ALL_SCRIPTS['__texts__']`** — same dict reference as `self._texts` (NOT a copy). Trainer battle widget text edits write into this dict directly, and they flow back to `self._texts` → `write_text_inc()` on save.
- **`_ALL_SCRIPTS['__texts_map__']`** — map folder name the texts belong to. Consumers use this to disambiguate "which map's texts are these?"
- **Universal mutation hook**: all 37 `_mark_dirty()` call sites trigger sync automatically — add/delete/edit/move/duplicate/paste/cut/drag-drop/template-install/camera-sequence all covered.

## Inline Text Editing
- `_make_text_field(label_combo, texts, label_text)` pairs a label dropdown with a QPlainTextEdit
- When user picks a label, the text box shows that label's content from `_ALL_SCRIPTS['__texts__']`
- When user edits text, it writes back to the dict via `_on_text_edited` closure
- Display format: `\n` → `\\n` + visual newline, `\p` → `\\p` + visual newline, `\l` and `$` shown as-is
- TrainerBattleWidget uses this for Intro/Defeat/Victory/NotEnough fields
- The dict is the same one used by `write_text_inc()` on save, so edits persist

## Trainer Battle Widget
- Variant-aware: command name (`trainerbattle_single`, `_double`, `_no_intro`, `_earlyrival`) determines type
- No Type dropdown — type is the command itself
- Each variant has different field layouts matching the pokefirered macro args
- `_single`: TRAINER, INTRO, DEFEAT [, CONTINUE]
- `_double`: TRAINER, INTRO, DEFEAT, NOT_ENOUGH [, CONTINUE]
- `_no_intro`: TRAINER, DEFEAT
- `_earlyrival`: TRAINER, FLAGS, DEFEAT, VICTORY

## Color Scheme (RMXP-style)
- Only specific categories get colored, everything else stays plain white (matching RMXP):
  - Flow (goto/call) → red, Conditionals → amber, Movement block → maroon (#8b2252), Flags/vars → purple, Items → lime, Sound → orange, Screen → teal, Battles → bright red, Pokemon → gold
- Constants in args (FLAG_*, VAR_*, TRAINER_*, etc.) get type color as fallback
- `_apply_cmd_color()` handles all coloring logic
- `_category_for_cmd()` classifies commands into categories
- `_load_color_settings()` reads custom colors from settings.ini on import and on live reload
- All colors customisable in Settings → Event Colors (applied immediately, no restart)
- `reload_settings()` on EventEditorTab re-runs color loading and recolors the command list

## Position Overrides (per condition page)
- `_build_position_overrides()` scans OnTransition scripts for conditional branches leading to setobjectxyperm
- `_pos_overrides` dict maps `(local_id, var_or_flag, value) → (x, y, source_label)`
- `_get_position_for_page()` matches a page's condition against overrides
- `_apply_position_override()` updates X/Y spinboxes with amber background + tooltip
- `_save_position_to_script()` writes X/Y edits back to the script command, not map.json
- `_current_pos_override_source` tracks which script owns the active override

## Cross-References
- `_update_xref()` scans all scripts for setobjectxyperm/setobjectxy targeting the current NPC
- Shows clickable HTML links with actual X/Y coordinates
- `_on_xref_clicked()` navigates to the source script (3-tier search: direct, inline, BFS)

## Script Lookup (Ctrl+Shift+F)
- `ScriptIndex` in `eventide/backend/script_index.py` — indexes 5,300+ labels on project load
- `ScriptSearchDialog` in `eventide/ui/script_search_dialog.py` — real-time filtering, navigate on double-click
- `_navigate_to_script_label()` loads the target map and selects the right event/page

## Phase 5 Dropdown Conversions (Complete)
- **ConstantsManager.SPECIALS**: 271 special function names loaded from `data/specials.inc` (sorted, NullFieldSpecial filtered)
- Message/TrainerBattle/PokeMart label fields use `_make_label_combo()` — searchable dropdown of script labels
- Special/SpecialVar use searchable specials dropdown with type-ahead filtering
- Buffer slot fields (BufferSpecies/Item/Move) use QSpinBox constrained 0-2
- SetObjectMovementType has a dedicated widget: object local ID combo + MOVEMENT_TYPE_ picker
- All dropdowns are editable — user can still type custom values not in the list

## Script Creation Tools
- **New Script** button with organized template categories:
  - **NPC Scripts:** Simple Talker, Trainer, Item Giver (flag-gated), Flag-gated NPC
  - **Signs & BG Events:** Simple Sign (MSGBOX_SIGN), Hidden Item Script (finditem with custom script)
  - **Map Scripts:** Door Warp, Cave Warp
  - **Standard Wrappers:** Nurse, PC, Mart
  - **Field Objects:** Cut Tree, Rock Smash, Strength Boulder
  - **Item Ball** template (context-aware — shows first for item ball objects)
  - **New Hidden Item** (always at top of menu — creates a data-only hidden_item bg_event, auto-finds unused flag)
- **Hidden Item Editor**: `_HiddenItemPanel` with item/flag/qty/position/elevation/underfoot fields. Shown via `QStackedWidget` when a hidden_item is selected (replaces command list). `_collect_current()` writes fields back to the event dict. Delete button with confirmation.
- **Set Move Route**: Auto-scaffolds movement labels with face_player + step_end. "Edit Steps..." opens RMXP-style Move Route Editor dialog with 6 category tabs and all 177 movement macros.
- **Move Camera (Cutscene)**: Full camera control dialog with 6 tabs (Pan, Slide, Screen, Effects, Timing, Sound). Mixes movement macros and script commands. Output auto-generates `applymovement LOCALID_CAMERA` blocks with `MapName_CameraMovement_N` labels, wrapped in SpawnCameraObject/RemoveCameraObject. Multi-command insertion into the command list.
- **RMXP Conditions Box**: Editable flag/var conditions above Event Properties (checkbox + searchable picker + operator/state). Flag and Variable are mutually exclusive.
- **Find Script** (Ctrl+Shift+F): Project-wide label search across all maps and shared scripts.
- **Find Unused Flag**: Scans all `scripts.inc` files + `src/*.c` for `FLAG_` references, reports which `FLAG_UNUSED_*` constants are free.
- **Rename Page**: Renames a page tab's `_label` and updates all goto/call references.
- **Set Flag → Page Linking**: setflag/setvar commands show "→ activates Page N" inline. Go To jumps to the condition page.
- `_register_text(label, content)` adds to both `self._texts` and `_ALL_SCRIPTS['__texts__']`
- `_register_labels(labels)` adds to `_SCRIPT_LABELS` for dropdown population
- **PorySuite integration**: `_load_porysuite_trainers()` reads `src/data/trainers.json` (743 trainers with names, classes, parties). Used by the Trainer template to populate a picker and auto-name text labels.

## Tooltips
- Comprehensive hover tooltips on all main UI controls, command edit dialogs (~30 widget types), camera dialog buttons, and command selector palette (~80 commands)
- `_CMD_TOOLTIPS` dict maps raw command names to descriptions; `_MOV_TOOLTIPS` maps camera movement macros
- `_tt(tip)` helper returns tip or empty string based on `_EVENT_TOOLTIPS_ENABLED` flag
- Setting: Tools → Settings → Event Editor Tooltips checkbox (defaults on, applies immediately on OK)
- Position override tooltips (functional indicators showing script source) are NOT wrapped — always visible

## Sprite Preview
- `SpritePreview` in `eventide/ui/widgets.py` shows an animated walk-down cycle for 9+ frame sheets, static for smaller ones.
- **"Open Sprite in Folder" button** below the preview opens the current sprite's PNG in the OS file manager (via `ui/open_folder_util.py`). The current sprite path is stored in `_current_sprite_path` and updated in `_update_sprite()`.
- GBA sprite sheets are horizontal strips with varying dimensions:
  - 144×32 / 160×32: standard 9-10 frame walk sheets (16px wide frames)
  - 48×32: 3-frame directional stands (Agatha etc.) — static display
  - 32×32: single square frame (Articuno etc.) — static display
  - 64×32: 2 square frames (nurse, ho_oh) — static display
- **9-frame layout**: [0] down-stand, [1] up-stand, [2] left-stand, [3] down-walk1, [4] down-walk2, [5] up-walk1, [6] up-walk2, [7] left-walk1, [8] left-walk2
- Walk-down animation cycle: stand(0) → walk1(3) → stand(0) → walk2(4). Frames 1 and 2 are NOT walk frames — they're up-stand and left-stand.
- Frame width detection: square sheets use full width; sheets divisible by height with ≤4 frames use height as width; everything else uses 16px.
- GBA transparency: palette index 0 (color of top-left pixel) is manually converted to transparent alpha. Qt doesn't handle tRNS in 4-bit indexed PNGs reliably.
- Scaled 3× with nearest-neighbor (`FastTransformation`) to keep pixel art crisp.
- `ConstantsManager.OBJECT_GFX_PATHS` maps `OBJ_EVENT_GFX_*` constants to PNG paths, searching people/ → pokemon/ → misc/.

## Save Model (No Auto-Save)
- **All saves are manual** — only the Save button writes to disk. No auto-saves.
- `_collect_current()` syncs UI fields to in-memory `_objects` when switching between objects/pages. This is in-memory only, NOT a disk write. It's needed so edits aren't lost when switching between objects.
- `_on_save()` calls `_collect_current()`, then writes scripts.inc + text.inc + map.json to disk.
- **Dirty tracking**: `EventEditorTab.data_changed` signal → `setWindowModified(True)` on main window. Title bar shows `[*]` when dirty.
- **Save/Discard/Cancel dialog**: Shown on close, map switch, and refresh. Uses `app_util.create_unsaved_changes_dialog()` — same as PorySuite.
- `_loading` flag suppresses dirty marking during field population (so switching objects doesn't falsely mark dirty).
- `_current_obj_idx = -1` is set at the start of `_load_map` to prevent `_collect_current` from corrupting new map data with stale UI values.
- **Watcher-safe reload**: `reload_current_map(force=False)` is the reload entry point used by the Porymap bridge and SharedFileWatcher. If `isWindowModified()` is True and `force` is False, it shows a Save/Discard/Cancel dialog instead of silently clobbering unsaved edits. Pass `force=True` only for intentional reloads where the caller already handled dirty-state.
- **ConstantsManager.refresh()**: Added so switching back to EVENTide from a PorySuite page re-reads header files. Items/flags/vars/trainers renamed and saved on the PorySuite side appear in dropdowns without a project reload.
- **Cross-tab GFX sync**: When a new overworld sprite is added in the Overworld Graphics tab, `OverworldGraphicsTab.gfx_constants_changed` signal fires → `UnifiedMainWindow._refresh_eventide_constants()` → `ConstantsManager.load()` + `EventEditorTab.refresh_gfx_constants()`. The new `OBJ_EVENT_GFX_*` constant is also pushed directly into `ConstantsManager.OBJECT_GFX` and `OBJECT_GFX_PATHS` in-memory, so the Event Editor's graphic dropdown updates immediately without a project reload or save.

## Sound Editor Integration (2026-04-06)
- **▶ Preview / ■ Stop / 🔊 Open buttons**: `_PlayBGMWidget`, `_PlaySEWidget`, and `_PlayFanfareWidget` all have three integration buttons. Preview renders and plays the selected song in the background WITHOUT switching tabs. Stop halts playback. Open switches to the Sound Editor and selects the song.
- **Module-level callbacks**: `_preview_song_cb`, `_open_in_sound_editor_cb`, `_stop_preview_cb` are set by `unified_mainwindow.py` during `setup_pages()`. This avoids threading parent references through widget constructors.
- **Constants sync**: Switching from the Sound Editor page to any EVENTide page triggers `ConstantsManager.refresh()`, so new/removed songs appear in song dropdowns automatically (no restart needed).
- **Fixed savebgm widget**: Was `_header_only = True` returning `('savebgm',)`. Now has a ConstantPicker for MUS_* constants and returns `('savebgm', song_constant)`.
- **Fixed healplayerteam widget**: Was returning `('healplayerteam',)` (nonexistent macro). Now returns `('special', 'HealPlayerParty')`.
- **Fixed playmoncry mode**: Widget now preserves the `mode` parameter instead of hardcoding `0`.
- **Shared sub-label save fix**: When multiple events share a script sub-label via `goto`, the save loop reorders `self._objects` so the currently-selected event is processed LAST (its sub-labels win over stale copies from other events).
- **Empty local_id guard**: `_collect_current()` only writes `local_id` to the object dict if the text field is non-empty. Prevents injecting `"local_id": ""` into objects that never had one.

## Known Patterns / Gotchas
- `_ALL_SCRIPTS` is a module-level dict so the stringizer can look up movement steps without dependency injection.
- `_objects` is a unified list mixing all event types. Each entry has `_event_type` ('object', 'coord', 'bg', 'map_script'). On save, they're split back into the correct arrays.
- `_event_type` and `_pages` are internal keys (prefixed with `_`) that get stripped before writing back to map.json.
- Map scripts are synthetic entries — they exist in scripts.inc but have no map.json representation, so they're not written back during save.
- Objects with `script: "0x0"` are real entries (like Oak) whose scripts live elsewhere (usually coord_events). Don't skip them — show them with an empty page.

## Region Map Tab
- Supports all 4 FireRed regions: Kanto, Sevii 1-2-3, Sevii 4-5, Sevii 6-7.
- **Two layers per region**: `LAYER_MAP` (overworld) and `LAYER_DUNGEON` (caves/tunnels). Toggle buttons in the UI switch between them.
- Layout files: `src/data/region_map/region_map_layout_*.h` — C arrays with `[LAYER_MAP]` and `[LAYER_DUNGEON]` sections.
- Grid is 22×15 cells per region (`MAP_WIDTH` × `MAP_HEIGHT`).
- `_save_layout_both()` writes both layers in one pass with line-offset correction after MAP layer replacement.
- Section coordinates (x, y, width, height) are auto-calculated from the MAP layer grid only — dungeon layer doesn't affect coordinates.
- Clone/rename/delete all propagate both layers.
- `region_map_sections.json` has 178 section IDs. All are shown in the section dropdown regardless of which layer is active.

## Test Project
`porysuite/pokefirered` is the test copy. Never touch `C:\GBA\pokefirered` during porysuite work.
