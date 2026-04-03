## [2026-04-03] — Phase 7: Porymap Integration (Infrastructure)

### Type
New Feature

### Summary
**PorySuite-Z now has full Porymap integration infrastructure** — the bridge between PorySuite-Z's script/data editors and Porymap's visual map editor. When complete, clicking an event in Porymap immediately shows its script in PorySuite-Z's Event Editor, and vice versa.

- **Install pipeline**: Tools > Install Porymap downloads the repo, applies C++ patches (via Python patcher for resilience to upstream changes), installs Qt SDK via aqtinstall, compiles, and deploys the binary — all in a progress dialog
- **Bridge communication**: JS companion script (`porysuite_bridge.mjs`) runs inside Porymap, writes JSON messages to a bridge file. Python `BridgeWatcher` monitors the file with QFileSystemWatcher + debounce and emits Qt signals
- **Launch system**: Writes `porymap.cfg` and `porymap.user.cfg` so Porymap opens to the right project/map. Detects running Porymap and brings to front. Auto-injects bridge script into every project opened
- **Event selection bridge**: Porymap event clicks → Event Editor navigates to map + selects event. Ctrl+E in Porymap → position-based event lookup in PorySuite-Z
- **"Open in Porymap" buttons**: Event Editor toolbar, Maps tab right-click context menu, Layouts tab button
- **Shared file watchers**: Monitors `map.json`, `scripts.inc`, `layouts.json`, `map_groups.json` for external changes (from Porymap saves). Auto-reloads affected editors with debounced detection
- **C++ patcher**: Python script (`apply_patches.py`) adds 11 event/save callbacks, `writeBridgeFile()`, and query functions (`getMapHeader`, `getCurrentTilesets`, `getMapConnections`, `getMapEvents`) to Porymap's scripting engine using search-and-replace (not fragile git patches)

### Files created
- `porymap_bridge/__init__.py` — Package init
- `porymap_bridge/bridge_watcher.py` — QFileSystemWatcher bridge message dispatcher (18 signals)
- `porymap_bridge/porymap_launcher.py` — Launch, config writing, process detection, bridge script injection
- `porymap_bridge/porymap_installer.py` — Clone/patch/build/deploy pipeline with progress dialog
- `porymap_bridge/porysuite_bridge.mjs` — JS companion script for Porymap (26 callbacks + 2 menu actions)
- `porymap_bridge/shared_file_watcher.py` — Watches shared project files for external modifications
- `porymap_patches/apply_patches.py` — Python patcher that modifies Porymap C++ source

### Files modified
- `ui/unified_mainwindow.py` — Tools menu (Install Porymap, Open in Porymap Ctrl+F7), bridge watcher setup, shared file watcher setup, 6 bridge signal handlers
- `eventide/ui/event_editor_tab.py` — "Open in Porymap" toolbar button, bridge API methods (navigate_to_map, select_event_by_bridge, select_event_at_position, reload_current_map)
- `eventide/ui/maps_tab.py` — Right-click context menu with "Open in Porymap" on map items
- `eventide/ui/layouts_tab.py` — "Open in Porymap" button (opens first map using selected layout), added wheelEvent protection to all 4 combo boxes (no-scroll-when-closed rule)

---

## [2026-04-03] — Event Editor: Hidden Item Editor

### Type
New Feature, Bug Fix

### Summary
**Hidden items are now fully editable and creatable** in the Event Editor:

- **Display fix**: Hidden items in the dropdown now show `[Hidden Item] Tiny Mushroom` instead of `[Hidden_Item] bg0`
- **Dedicated editor panel**: Selecting a hidden item swaps the command list for a clean property form with:
  - Searchable **Item** picker (all ITEM_ constants)
  - Searchable **Flag** picker (collection tracking flag)
  - **Quantity** spinner, **X/Y** position, **Elevation**, **Underfoot** (Itemfinder-only) checkbox
  - **Delete** button to remove the hidden item from the map
- **Creation**: "New Script ▾" menu now always shows "New Hidden Item" at the top — works even with no event selected, auto-finds an unused flag
- **Saving**: Changes write directly to map.json `bg_events` array (no script needed)
- **Combo label updates live** — change the item and the dropdown text updates immediately

The existing "Hidden Item Script" template (for custom finditem scripts) was renamed to clarify it's different from data-only hidden items.

### Files modified
- `eventide/ui/event_editor_tab.py`:
  - Added `_HiddenItemPanel` class with load/collect/changed signal
  - Wrapped right panel in `QStackedWidget` (command list vs hidden item editor)
  - `_on_object_changed()`: hidden items show the panel, hide page tabs/conditions
  - `_collect_current()`: reads hidden item fields back to the event dict on save
  - Added `_create_new_hidden_item()`, `_delete_hidden_item()`, `_on_hidden_item_changed()`
  - `_on_new_npc_script()`: "New Hidden Item" at top of menu, always available
  - Renamed old "Hidden Item" template to "Hidden Item Script"

---

## [2026-04-03] — Settings: Live Reload for Event Editor

### Type
Enhancement

### Summary
**All Event Editor settings now apply immediately** when you click OK in the Settings dialog — no restart needed:
- **Event Colors**: Changing any constant type color or command category color instantly recolors the command list
- **Event Tooltips**: Toggling tooltips on/off instantly shows/hides all help tooltips
- Previously, both colors and tooltips were cached at app startup and required a full restart to take effect

### Files modified
- `eventide/ui/event_editor_tab.py` — Added `reload_settings()` method: re-runs `_load_color_settings()`, walks every command list item to recolor with new settings, toggles tooltip visibility via `_apply_tooltip_visibility()`
- `ui/unified_mainwindow.py` — `_open_settings()` calls `reload_settings()` on the event editor after the Settings dialog closes

---

## [2026-04-03] — Event Editor: Tooltips & Toggle Setting

### Type
Enhancement

### Summary
**Comprehensive tooltips** added across the entire Event Editor:
- All main UI controls (Open Map, New Page, Rename, Delete, Save, page tabs, event selector, conditions box, event properties, sprite preview, command list, all toolbar buttons, search bar)
- All ~80 command edit dialog widgets (Message, YesNo, MultiChoice, Flag/Var pickers, Flow controls, Warp, Movement, Trainer, Species, Items, Money, Coins, Buffers, Screen, Sound, Delay, etc.)
- All camera dialog buttons with clear descriptions (including Set Flash Level vs Animate Flash distinction)
- All ~80 commands in the Command Selector palette (hover any button for a description)
- **Settings toggle**: Tools → Settings → Event Editor Tooltips checkbox. Disables all help tooltips when unchecked (applies immediately, no restart needed). Position override tooltips (functional indicators) are unaffected.

### Files modified
- `eventide/ui/event_editor_tab.py` — Added `_CMD_TOOLTIPS` dict, `_EVENT_TOOLTIPS_ENABLED` flag, `_tt()` helper, wrapped all 135 `setToolTip()` calls with `_tt()`
- `ui/dialogs/settingsdialog.py` — Added "Event Editor Tooltips" checkbox in Settings → Editor page

---

## [2026-04-03] — Event Editor: Move Camera Command

### Type
New Feature

### Summary
**Move Camera (Cutscene) command** — a full cutscene camera control tool in the Event Editor command selector. Opens an RMXP-style dialog with 6 category tabs:

- **Pan**: 20 directional pan macros (walk_up, walk_down, walk_left, walk_right, etc.)
- **Slide**: 8 directional slide macros (smooth movement without walk animation)
- **Screen**: Fade in/out, flash level, set weather, do weather, reset weather
- **Effects**: Do field effect, wait field effect, create sprite
- **Timing**: Delay (frames), wait button press, wait state
- **Sound**: Play SE, play fanfare, play music, fade out music, fade in music, play/wait Mon cry

The dialog mixes movement macros (panning) with script commands (fades, sounds) in one sequence. On output, consecutive movement macros get grouped into `applymovement LOCALID_CAMERA` blocks with auto-generated labels (`MapName_CameraMovement_N`). Script commands break the block and appear as standalone lines. The full sequence is wrapped in `special SpawnCameraObject` / `special RemoveCameraObject`.

### Files modified
- `eventide/ui/event_editor_tab.py` — `_CameraMoveRouteDialog` class, "Move Camera (Cutscene)" in command selector Page 2 under new "Camera" category, `_on_add_camera_sequence()` handler for multi-command insertion

---

## [2026-04-03] — Event Editor: Color Scheme, Position Overrides, Script Lookup

### Type
Enhancement, New Feature

### Summary
Three major additions to the Event Editor:

**Color Scheme (fully wired):**
- Command list now color-codes specific functional categories (matching RPG Maker XP style): flow navigation (red), conditionals (amber), Set Move Route blocks (maroon), flag/switch control (purple), items (lime), sound (orange), screen effects (teal), battles (bright red), pokemon (gold)
- Plain structural commands (text, choices, end/return, lock/release) stay default — not everything is colored
- Constants in arguments (FLAG_*, VAR_*, TRAINER_*, etc.) get their type color as fallback
- All colors customisable in Settings → Event Colors
- Default movement color changed from green to maroon (#8b2252) to match RMXP
- Added missing commands to categories: fadedefaultbgm, savebgm, finditem, giveitem, pokemart, waitstate, healplayerteam, and more

**Per-Page Position Overrides:**
- When OnTransition scripts set different X/Y positions for an NPC based on flag/var conditions (e.g. call_if_eq VAR_MAP_SCENE → setobjectxyperm), the Event Editor detects this automatically
- Switching to a condition page whose flag/var matches updates the X/Y spinboxes to the script-defined position
- Spinbox background turns amber with a tooltip naming the source script
- Editing X/Y on an overridden page saves back to the setobjectxyperm command, not map.json
- Works globally for any map

**Script Lookup by Name (Ctrl+Shift+F):**
- Project-wide search across all 5,300+ script labels
- Index built on project load (under 1 second), scans all maps + data/scripts/ + event_scripts.s
- "Find Script" toolbar button + Ctrl+Shift+F shortcut
- Search dialog with real-time filtering, Label/Map/Type columns
- Double-click navigates: loads the map, selects the event, switches to the right page
- Shared scripts show their filename; clicking searches map.json files to find a map that uses them

### Files added
- `eventide/backend/script_index.py` — Project-wide label index (ScriptIndex class)
- `eventide/ui/script_search_dialog.py` — Script search dialog (ScriptSearchDialog)

### Files modified
- `eventide/ui/event_editor_tab.py` — Color scheme wiring, position override system, Find Script button/shortcut/navigation, category command lists expanded
- `ui/dialogs/settingsdialog.py` — Movement default color updated to maroon

---

## [2026-04-02] — Open in Folder buttons + editable item icons

### Type
Enhancement

### Summary
Added "Open in Folder" buttons across four editors, plus an editable icon picker for items:

- **Trainer Class Editor**: "Open File in Folder" button opens the sprite PNG in the OS file manager
- **Pokemon Graphics tab**: "Open Graphics Folder" button opens the species' graphics directory
- **EVENTide sprite preview**: "Open Sprite in Folder" button opens the overworld sprite PNG
- **Items tab**: "Open Icon in Folder" button opens the item icon PNG; new **Icon picker dropdown** lets you change which sprite an item displays (writes to item_icon_table.h on save)

### Files added
- `ui/open_folder_util.py` — cross-platform `open_in_folder()` and `open_folder()` utilities (Windows/macOS/Linux)

### Files modified
- `ui/items_tab_widget.py` — Icon card with sprite picker combo (thumbnails), "Open Icon in Folder" button, full icon parsing and item_icon_table.h writer
- `ui/mainwindow.py` — "Open Graphics Folder" button on Pokemon graphics tab, icon save calls in save pipeline
- `eventide/ui/event_editor_tab.py` — "Open Sprite in Folder" button after SpritePreview

---

## [2026-04-02] — Add Trainer Class Editor

### Type
New Feature

### Summary
New "Trainer Classes" tab alongside the existing Trainers tab. Full class editor with:
- **Editable**: display name (12-char limit with counter), money multiplier, default sprite (dropdown with thumbnails of all 139 trainer pics)
- **Add new classes**: button opens a dialog to create a new trainer class, writes the constant to trainers.h, name to trainer_class_names.h, and money entry to battle_main.c
- **Battle info**: battle BGM category (VS Champion / VS Gym Leader / VS Trainer), victory music type, battle terrain override — color-coded for special classes
- **Encounter music**: most common music among trainers of this class
- **Sprite**: full-size preview with picker, PNG file path
- **Facility classes**: all FACILITY_CLASS constants mapped to this trainer class (used by Battle Tower, Trainer Tower, Union Room)
- **Usage**: how many trainers use this class, with names

Edits saved to trainer_class_names.h, battle_main.c (gTrainerMoneyTable), and trainer_class_lookups.h (gFacilityClassToPicIndex).

### How it works
- Parses trainer class constants, display names, money multipliers, facility class mappings, and sprite paths from C headers
- Maps classes to sprites via facility class lookup tables (gFacilityClassToPicIndex + gFacilityClassToTrainerClass)
- Derives battle BGM, victory music, and terrain from hardcoded switch-statement logic in battle_main.c, pokemon.c, and battle_bg.c
- Computes encounter music by finding the most common music value among trainers of each class
- QTabWidget switcher on the Trainers page lets you flip between "Trainers" and "Trainer Classes"
- Sprite changes update gFacilityClassToPicIndex for all facility classes that map to the edited trainer class
- Adding a new class writes to 3 files: trainers.h (constant), trainer_class_names.h (name), battle_main.c (money table)

### Files added
- `ui/trainer_class_editor.py` — TrainerClassEditor widget, parsers, and header writers

### Files modified
- `ui/mainwindow.py` — QTabWidget wrapper for trainers tab, load/save wiring

---

## [2026-04-02] — Fix items/trainers/moves not saving in unified window

### Type
Bug Fix

### Summary
In PorySuite-Z's unified window, tab widgets are ripped out of PorySuite's `mainTabs` and placed in a stack widget. PorySuite's `previous_main_tab` index never updates, so `update_main_tabs()` during save only flushed whichever tab happened to be selected at init time (usually Pokemon). Items, trainers, moves, starters, and other editors were never flushed during save — their edits were lost.

### What changed
- **`update_save()` now flushes ALL editors unconditionally** instead of relying on `update_main_tabs()` which depends on the broken `previous_main_tab` index
- Explicit calls to: `_flush_pokedex_panel`, `save_items_table`, `save_moves_defs_table`, `_save_trainers_editor`, `save_species_learnset_table`
- Removed duplicate `_flush_pokedex_panel` call that ran later in the save flow

### Files modified
- `ui/mainwindow.py` — replaced `update_main_tabs()` in `update_save` with explicit editor flushes

---

## [2026-04-02] — Credits editor: fix quotes breaking build

### Type
Bug Fix

### Summary
Smart quotes (curly " ") and straight double quotes in credits text caused C build errors because the GBA charmap has no quote character. The `_escape_credits` function now converts all quote types to parentheses. Fixed the existing broken line in strings.c.

### Files modified
- `ui/credits_editor.py` — `_escape_credits` now handles smart quotes, curly quotes, and straight double quotes
- `pokefirered/src/strings.c` — fixed broken gCreditsString_Junichi_Masuda line

---

## [2026-04-02] — Fix category/description edits lost by pokedex panel clobber

### Type
Bug Fix

### Summary
Category and description edits on the Stats page didn't persist because `_flush_pokedex_panel()` ran during save and overwrote species_info with stale values from the Pokedex panel's widgets (which were never updated when the user edited on the Stats page). Also fixed mtime race where header files written after JSON caused unnecessary re-extraction on next startup.

### What changed
- **Pokedex panel sync** — `save_species_data()` now updates the pokedex panel's `f_category` and `f_description` widgets after writing to species_info, so `_flush_pokedex_panel().collect()` reads the correct values
- **JSON mtime touch** — After all header writes, species.json and pokedex.json mtimes are bumped so they're newer than headers, preventing unnecessary re-extraction on next startup

### Files modified
- `ui/mainwindow.py` — panel widget sync in `save_species_data`, mtime touch in `update_save`

---

## [2026-04-02] — Fix processEvents clobber + dirty flag for category/description

### Type
Bug Fix

### Summary
Category edits still didn't persist after the save audit because `update_main_tabs()` called `save_species_data()` a second time AFTER `processEvents()` had pumped queued QTimer callbacks that reset widget values to stale defaults. The second capture overwrote the correct data. Also, editing the category or description fields didn't show the dirty asterisk (*) in the title bar because those widgets had no `textChanged` → `setWindowModified` connection.

### What changed
- **processEvents clobber fix** — `_species_already_captured` flag set after the first `save_species_data()` in `update_save()`. `update_main_tabs()` checks this flag and skips the redundant species save. Flag cleared in `finally` block.
- **Dirty flag for category** — `species_category.textChanged` now connected to `setWindowModified(True)`
- **Dirty flag for description** — `species_description.textChanged` now connected to `setWindowModified(True)`

### Files modified
- `ui/mainwindow.py` — skip flag logic, dirty signal wiring for category and description

---

## [2026-04-02] — Save system audit and structural fix

### Type
Bug Fix / Architecture

### Summary
Full audit of the save system revealed that multiple writers were competing for the same C header files. Direct header writers (species_info.h, pokedex_entries.h, items.h, learnset headers) ran first, then the old plugin pipeline (`parse_to_c_code`) ran ALL plugins in parallel threads and overwrote the same files with potentially stale data. Category edits ("SEED" → "Seed") were lost because `_write_pokedex_entries_header` read from natdex (stale) instead of species_info (authoritative). The save button also wasn't firing at all due to a signal connection issue in the unified window.

### What changed
- **Save button fixed** — `triggered` signal wrapped in lambda to absorb the bool arg PyQt6 passes
- **Save dialog visible** — progress dialog now parents to the top-level window, not the hidden inner window
- **Save always runs** — removed flawed `isWindowModified()` gate that could prevent saves
- **Category/description fix** — `_write_pokedex_entries_header` now reads from `species_info` (what the UI writes to) instead of `natdex` (which was failing to sync)
- **No more double-writes** — `_skip_parse_to_c` flag prevents SpeciesData, PokemonItems, and PokemonMoves plugins from re-writing files that direct writers already handled
- **PokemonMoves split** — battle_moves.h, move_names.h, move_descriptions.c still written by plugin; learnset headers skipped (already handled by `_write_moves_headers`)
- **Credits editor** — character-per-line limits (30 chars, 6 lines) added to title and names fields; preview moved to dedicated right column
- **CLAUDE.md** — added permanent rule: every text entry field must have character-per-line limits with visual feedback

### Files modified
- `ui/unified_mainwindow.py` — save signal fix, dirty-flag gate removal, save flow cleanup
- `ui/mainwindow.py` — `_write_pokedex_entries_header` reads from species_info, `_make_save_dialog` parents to top-level, skip flags set before parse_to_c_code
- `core/pokemon_data.py` — SpeciesData, PokemonItems, PokemonMoves respect `_skip_parse_to_c`
- `ui/credits_editor.py` — DexDescriptionEdit for text limits, 3-column layout with preview
- `docs/DATAFLOW.md` — updated with double-write fix documentation
- `C:\GBA\CLAUDE.md` — text limit rule added to UI/UX section

---

## [2026-04-02] — Phase 4: Codebase cleanup

### Type
Cleanup / Refactor

### Summary
Major cleanup of the project root and removal of the plugin system. The app only supports pokefirered — the plugin abstraction layer, plugin manager, emerald plugin, and plugin selection UI are all gone. The firered data code now lives in a `core/` module. Documentation moved to `docs/`. Junk files, empty folders, and orphaned data deleted. Crashlogs auto-purge on startup (older than 7 days). Root folder went from 40+ items to a clean structure.

### What changed
- **Plugin system removed** — `plugin_abstract/`, `plugins/`, `pluginmanager.py`, `plugininfodialog.py` all deleted
- **New `core/` module** — firered data manager, extractors, refactor service, and utils live here now
- **`mainwindow.py`** — 100+ lines of plugin version negotiation replaced with a single `_core.create_data_manager()` call
- **`newproject.py`** — plugin discovery replaced with hardcoded firered info
- **`projectselector.py`** — plugin selection removed
- **`docs/` folder** — all markdown docs moved here (CHANGELOG, README, TROUBLESHOOTING, DATAFLOW, etc.)
- **`diagnostics.py`** — rescued from PorySuitePyQT6/ folder (was missing from root, mainwindow.py imports it)
- **`crashlog.py`** — added `purge_old_logs()`, called at startup to delete logs older than 7 days
- **`.gitignore`** — cleaned up, removed entries for deleted files
- **Deleted**: `TMP DELETE/`, `Save/`, `PorySuitePyQT6/`, `plugins/pokeemerald_expansion/`, `planned features.txt`, `codex.bat`, `codex.exe`, `Zeldamon.xlsx`, `progress.md`, `renameplan.md` (moved to docs), debug scripts, temp files, empty `tools/` folder, `data/plugins/`

---

## [2026-04-02] — Master plan restructured for Phases 4-7

### Type
Documentation

### Summary
Rewrote UNIFIED_EDITOR_PLAN.md to reflect actual project state. Phases 1-3.5 marked complete. Remaining work reorganized into: Phase 4 (codebase cleanup), Phase 5 (editor completeness — trainer class, credits, edit menu, battle dialogue, EVENTide improvements), Phase 6 (settings & infrastructure), Phase 7 (Porymap integration), Backlog (abilities editor, sound test). Incorporated planned features.txt content into the master plan. Git pull Done button now triggers project refresh on click.

### Files changed
- `UNIFIED_EDITOR_PLAN.md` — Full rewrite with accurate phase status and restructured roadmap
- `mainwindow.py` — Git pull Done button triggers refresh on click (not auto-refresh)
- `unified_mainwindow.py` — Post-pull refresh updates unified status bar and reloads EVENTide

---

## [2026-04-02] — Phase 3.5: VS Seeker rematch system — full tier gate editing

### Type
Enhancement (Phase 3.5 — read + write)

### Summary
The Party tab now shows VS Seeker rematch tiers for rematchable trainers, and the tier gate system is fully editable. A green "VS Seeker Rematch Tiers" section appears at the top of the Party tab with a tier dropdown to browse/switch parties, and an "Edit Tier Gates" button that opens a settings dialog where you can change how many tiers exist and which story flags gate each one. Changes write directly to `vs_seeker.c`. Rematch variant constants are hidden from the trainer list.

### What's new
- **Tier dropdown in Party tab** — appears for rematchable trainers, shows all tiers with inline party summaries. Selecting a tier loads that variant's party.
- **"Edit Tier Gates" button** — opens the Rematch Settings dialog
- **Rematch Settings dialog** — change number of tiers (2-20), pick which FLAG_* constant gates each tier from a searchable dropdown of all flags in your project's flags.h. Saves directly to vs_seeker.c.
- **Dynamic tier labels** — tier names are parsed from the actual FlagGet() calls in `TryGetRematchTrainerIdGivenGameState()`, not hardcoded. Works for any project, not just Kanto.
- **Write support** — rewrites `MAX_REMATCH_PARTIES`, the entire switch statement, and pads/trims all sRematches[] entries to match the new tier count
- **Hidden rematch variants** — TRAINER_X_2+ constants filtered from trainer list
- **Reverse lookup** — any tier constant maps back to its rematch entry
- **SKIP explanation** — SKIP tiers show "same as previous tier" with flag info

### Not yet implemented (coming later)
- Per-trainer tier editing (creating new TRAINER_X_N constants + party data for individual trainers)
- Auto-create trainer entries, party definitions, and constants in opponents.h for new rematch variants

### Files changed
- `ui/trainers_tab_widget.py` — Added `_parse_all_flags()`, `_parse_tier_gate_flags()`, `_rewrite_vs_seeker_tier_gates()`, `_pad_rematch_entries()`, `_flag_to_label()`, `_build_tier_labels()`. Added `_RematchSettingsDialog` class. Added `edit_tier_gates_requested` signal and `_on_edit_tier_gates()` to both panel and widget. `_build_rematch_map()` returns three values (base, any, variants). Tier labels are dynamic. `_parse_rematch_entry()` no longer hardcodes 6-slot padding.
- `mainwindow.py` — Git pull Done button now triggers project refresh on click instead of auto-refreshing. Accepts optional `on_refresh_done` callback.
- `unified_mainwindow.py` — After git pull refresh, updates unified status bar and reloads EVENTide data.

---

## [2026-04-01] — Fix trainers tab empty + false dirty indicator across all navigation

### Type
Bug Fix

### Summary
Three bugs found during Phase 3 testing:

1. **Trainers tab completely empty** — The "Set up battle script" button was wired to a method that didn't exist on the class it lived in. This crashed the trainer panel every time it was built, and a silent `except` swallowed the error. Result: no trainers ever showed up, just "Open a project to edit trainers."

2. **False dirty indicator (`*` in title bar) from any navigation** — Clicking between toolbar pages, switching Pokemon sub-tabs (Stats, Evolutions, etc.), clicking different Pokemon in the species tree, and viewing different trainers all triggered the unsaved changes `*` even though nothing was edited. Root cause: PorySuite's internal code calls `setWindowModified(True)` whenever it populates fields — loading a trainer triggers widget signals, switching Pokemon sub-tabs saves and reloads species data, and all of these fire the dirty flag. The unified window was forwarding all of them.

3. **Jump-to-trainer used wrong data role** — The right-click "Edit Trainer Party" lookup used `item.data(2)` instead of `item.data(Qt.ItemDataRole.UserRole)` (value 256), so it never matched any trainer in the list.

### What changed

**`ui/trainers_tab_widget.py`:**
- Added `setup_battle_requested` signal to `_TrainerDetailPanel` and connected the button to it. Parent widget wires the signal to its handler during `load()`.
- Added `_loading` guard flag to `_TrainerDetailPanel`. Set True during `load()`, checked before emitting `changed`. Prevents the trainer sprite update and field population from firing false dirty signals.

**`unified_mainwindow.py`:**
- Added `_suppress_dirty` flag — blocks dirty propagation during page-switch flush and lazy-load operations.
- Added `_ps_suppress_dirty` flag on PorySuite window — blocks dirty during internal navigation (species tree clicks, Pokemon sub-tab switches).
- Disconnected PorySuite's original `mainTabs.currentChanged` handler (pages are reparented, so the old handler fires spuriously).
- Disconnected and re-wired PorySuite's `tab_pokemon_data.currentChanged` and `tree_pokemon.itemSelectionChanged` signals through suppression wrappers. Direct `disconnect(method)` doesn't work in PyQt6 because bound method identity doesn't match — so we disconnect ALL slots from each signal and reconnect wrapped versions.
- Fixed `item.data(2)` → `item.data(Qt.ItemDataRole.UserRole)` in jump-to-trainer.
- Removed duplicate `_trigger_lazy_load` call from `_switch_page` (was already called by the `currentChanged` signal handler).

---

## [2026-04-01] — Phase 3 complete: Cross-editor navigation and map list sync

### Type
Enhancement (Phase 3)

### Summary
Phase 3 adds deep cross-editor features that let you jump between the data editors (Trainers, Items) and the script editor (Event Editor) without manually switching pages and hunting for entries.

### What's new

**Trainer tab → Event Editor:**
- "Set up battle script in Event Editor" button on the Trainers tab dialogue section. Switches to Event Editor with a status bar hint showing which trainer constant to wire up.

**Event Editor → Data tabs (right-click context menu):**
- Right-click a `trainerbattle_single` command → "Edit Trainer Party (TRAINER_*)" jumps to the Trainers tab with that trainer selected
- Right-click a `giveitem` command → "Edit Item (ITEM_*)" jumps to the Items tab with that item selected

**Maps tab → Event Editor (double-click sync):**
- Double-clicking a map in the Maps tab tree opens it in the Event Editor and switches to that page. No more copy-pasting map names into the Open Map dialog.

**Dirty tracking:**
- Unified `[*]` indicator in the title bar when any editor has unsaved changes
- Save All (Ctrl+S) saves both PorySuite data and EVENTide scripts in one action
- Close prompt catches unsaved changes from either side

### Files changed
- `unified_mainwindow.py` — signal wiring and handler methods for all cross-editor navigation + map sync
- `ui/trainers_tab_widget.py` — added `setup_battle_requested` signal and "Set up battle script" button
- `eventide/ui/event_editor_tab.py` — added `jump_to_trainer`/`jump_to_item` signals, right-click context menu, `open_map_and_select()` public API
- `eventide/ui/maps_tab.py` — added `map_selected` signal on double-click

---

## [2026-04-01] — Fix trainer battle continue script dropdown defaulting to first script instead of blank

### Type
Bug Fix

### Summary
When adding a `trainerbattle_single` command in the Event Editor, the "Continue Script" dropdown started on the first script label in the list instead of blank. This made it look like you had to pick a script, even though the continue script is completely optional — most regular trainers in the game don't have one. Only gym leaders and special story NPCs use continue scripts (to trigger badge scenes, cutscenes, etc.).

### What changed
- **`eventide/ui/event_editor_tab.py`**: `_make_label_combo` now inserts a blank entry at the top of the dropdown and defaults to it when no value is set. Leaving it blank produces a standard 3-parameter `trainerbattle_single` (trainer, intro text, defeat text) — which is what the game uses for all normal trainers.

---

## [2026-04-01] — Fix EVENTide script corruption: duplicate end, lost movement data, CRLF line endings

### Type
Bug Fix

### Summary
EVENTide's save was corrupting scripts.inc in three ways:

1. **Duplicate end/release/lock** — Every script got a second `end` appended after saving, plus `lock` and `release` injected into sub-scripts that shouldn't have them. Root cause: `write_scripts_inc` unconditionally appended `end` without checking if the commands already contained one, and `lines_from_commands` only checked hidden lines (comments/blanks) for existing lock/release, missing the actual commands list.

2. **Lost movement data, macros, and .equ directives** — The save rebuilt scripts.inc from scratch using only event-script labels. Anything not directly assigned to a map event (movement tables, .macro blocks, .equ constants, MapScripts, sub-scripts) was dropped. Root cause: `write_scripts_inc` wrote pages from the UI only, discarding the rest of the file.

3. **CRLF line endings** — All EVENTide file writes (map.json, scripts.inc, text.inc, layouts.json, map_groups.json, region_map_sections.json) used Python's default `open('w')` which produces Windows CRLF on Windows. The build tools (mapjson, gcc) run in MSYS2/Linux and expect Unix LF, causing "unexpected trailing comma" build errors.

### What changed
- **`eventide/backend/eventide_utils.py`**:
  - `write_scripts_inc` now reads the existing file and only replaces label blocks it knows about — movement data, macros, .equ, MapScripts, and all non-event content is preserved untouched
  - New `_render_label_block` helper renders a single label's commands
  - New `_last_nonblank` helper checks if commands already end with end/return/releaseall before appending
  - `lines_from_commands` now checks the actual commands list (not just hidden lines) for lock/lockall/release/releaseall to avoid duplicates
  - Sub-scripts ending with `return` no longer get lock/release/end injected
  - `write_text_inc` and `write_scripts_inc` now use `newline='\n'` for Unix line endings
- **`eventide/ui/event_editor_tab.py`**: map.json write uses `newline='\n'`
- **`eventide/ui/maps_tab.py`**: map.json write uses `newline='\n'`
- **`eventide/backend/map_renamer.py`**: all JSON writes use `newline='\n'`
- **`eventide/backend/layout_renamer.py`**: layouts.json write uses `newline='\n'`
- **`eventide/backend/warp_validator.py`**: map.json write uses `newline='\n'`
- **`eventide/backend/region_map_manager.py`**: sections.json write uses `newline='\n'`

### Also fixed
- **`unified_mainwindow.py`**: Added missing trainers flush when switching away from the Trainers page — party edits were lost because the page-switch handler didn't call `_save_trainers_editor()`
- **`ui/trainers_tab_widget.py`**: New trainer party declarations now append to trainer_parties.h instead of being silently skipped; NUM_TRAINERS is auto-updated when adding trainers; duplicate #define prevention in opponents.h
- **`eventide/ui/event_editor_tab.py`**: Global text labels from `data/text/*.inc` were being dumped into the map's local `text.inc` on save, causing "symbol already defined" build errors. Now only local text labels (and newly created ones) are written back.
- **`eventide/ui/event_editor_tab.py`**: The null script `0x0` (used for objects with no script) was being written as a label `0x0::` in scripts.inc, causing "junk at end of line" build errors. Now skipped during save.

---

## [2026-04-01] — Phase 3: New Add Trainer dialog with class dropdown and auto-defaults

### Type
Enhancement (Phase 3 start)

### Summary
Replaced the broken "Add Trainer" flow. Previously it showed a text input asking for a raw constant name (like `TRAINER_HIKER_BOB`) — no class dropdown, no name field, no defaults. Now it's a proper dialog:

1. **Class dropdown** — shows all trainer classes sorted by display name (e.g. "HIKER (TRAINER_CLASS_HIKER)")
2. **Name field** — enter the trainer's name (e.g. "BOB"), max 10 characters
3. **Live constant preview** — shows what the constant will be as you type (e.g. `TRAINER_HIKER_BOB`), turns red if it already exists
4. **Auto-defaults from class template** — finds the blank-named template trainer for the selected class and copies its encounter music, trainer pic, AI flags, etc. A new HIKER gets HIKER music and HIKER pic automatically
5. **Auto-writes `#define` to opponents.h** — no more "you must manually add this" warning. The new constant with the next available ID is appended automatically

### What changed
- **`ui/trainers_tab_widget.py`**:
  - New `_AddTrainerDialog` class with class dropdown, name field, live preview
  - `_add_trainer()` rewritten to use the dialog
  - New `_find_class_template()` finds blank-named template trainer for a class
  - New `_add_trainer_define()` auto-appends `#define` to opponents.h
  - Added `QDialog`, `QDialogButtonBox` to imports

---

## [2026-04-01] — Fix command blue, $ in message boxes, overflow-only background

### Type
Bug Fix

### Summary
1. **{COMMANDS} now reliably blue** — Switched to QSyntaxHighlighter for foreground (blue), overflow ExtraSelections now only set background (not foreground). The two don't conflict because they control different properties. Blue text shows through any overflow background.
2. **Regular text boxes in EVENTide no longer show `$`** — The "Edit: Text" dialog (msgbox/message commands) now uses GameTextEdit. The `$` terminator is hidden from the user but preserved when saving back.
3. **EVENTide `$` round-trip preserved** — `display_to_eventide()` adds `$` back since EVENTide's internal format keeps it (parse_text_inc preserves it, write_text_inc writes it as-is).

### What changed
- **`ui/game_text_edit.py`** — Restored QSyntaxHighlighter for blue {COMMANDS}. ExtraSelections now only set background. `display_to_eventide()` adds `$` back.
- **`eventide/ui/event_editor_tab.py`** — `_MessageWidget` now uses GameTextEdit with `set_eventide_text()`/`get_eventide_text()`.

---

## [2026-04-01] — Fix text highlighting and apply GameTextEdit to EVENTide

### Type
Bug Fix + Enhancement

### Summary
Fixed three issues with the text editor:
1. **{COMMANDS} now show in blue** — Previously the overflow highlighting (ExtraSelections) painted over the syntax highlighter. Fixed by doing ALL highlighting via ExtraSelections: overflow first, then commands on top. Commands always appear in blue bold regardless of overflow state.
2. **No more false overflow on normal text** — Max lines was set too low (8 for intro, 6 for defeat). Sabrina's intro has 9 display lines, so lines 9+ got orange "extra lines" highlighting. Increased to 20.
3. **EVENTide trainer battle dialog now uses GameTextEdit** — Double-clicking a trainerbattle command in the event editor now shows clean text with line breaks (no `\n` or `$`), character counters, blue {COMMANDS}, and right-click Insert Command menu.

### What changed
- **`ui/game_text_edit.py`** — Removed QSyntaxHighlighter (conflicts with ExtraSelections), moved {COMMAND} highlighting to ExtraSelections pass 2. Added `eventide_to_display()` / `display_to_eventide()` for EVENTide's internal format. Extracted shared `_build_selections()` and `_build_counter_html()` helpers.
- **`ui/trainers_tab_widget.py`** — Increased max_lines from 6-8 to 20 for all dialogue types.
- **`eventide/ui/event_editor_tab.py`** — `_make_text_field()` now uses GameTextEdit with `set_eventide_text()`/`get_eventide_text()` instead of raw QPlainTextEdit with manual escape code display.

---

## [2026-04-01] — Standardised game text editor with character limits and command highlighting

### Type
Enhancement

### Summary
Created a new reusable `GameTextEdit` widget that is the standard for editing any game text in PorySuite-Z. This replaces raw QPlainTextEdit boxes that showed ugly escape codes (`\n`, `$`) and had no character limit enforcement.

**What it does:**
- **Per-line character counter** — colour-coded just like the Pokedex description editor: grey when fine, amber when close to the limit (>85%), red when over
- **36 characters per line** — auto-detected by scanning all vanilla pokefirered text.inc files (526 trainer intro lines, 469 defeat, 1141 post-battle, 6948 NPC lines all max out at 36)
- **{COMMANDS} shown in blue** — text commands like `{PLAYER}`, `{PLAY_BGM}`, `{MUS_ENCOUNTER_GYM_LEADER}` are highlighted in blue and don't count toward the character limit (they compile to binary opcodes, not display characters)
- **Right-click → Insert Command** — organised menu with categories (Variables, Music, Font) to insert text commands without memorising them
- **No raw escape codes** — `\n`, `\p`, `\l` display as actual line breaks; `$` is hidden. When saving, the original escape types are preserved (a `\p` stays `\p`, not converted to `\n`)
- **Overflow highlighting** — characters past the limit get red background, extra lines past the max get orange background

Applied to the Trainers → Dialogue tab immediately. The same widget will be used everywhere else text is edited.

### What changed
- **`ui/game_text_edit.py`** (new) — GameTextEdit widget, _CommandHighlighter, conversion helpers, attach_game_text_ui for existing widgets
- **`ui/trainers_tab_widget.py`** — Dialogue tab now uses GameTextEdit instead of raw QPlainTextEdit

### Vanilla text analysis results
| Category | Lines scanned | Max chars/line | 99th percentile |
|----------|--------------|----------------|-----------------|
| Trainer intro | 526 | 36 | 35 |
| Trainer defeat | 469 | 34 | 34 |
| Trainer post-battle | 1,141 | 36 | 35 |
| NPC dialogue | 6,948 | 37 (2 outliers) | 35 |

---

## [2026-04-01] — Unified Editor: Phase 2 — Shared data, settings, trainer dialogue

### Type
Major Enhancement

### Summary
Three connected upgrades that make the unified editor actually useful beyond just being one window:

1. **Settings dialog rebuilt** — Now has a sidebar with categories (General, Build & Play, Trainer Defaults, Editor, Notifications) instead of one long scrolling list. New settings: build commands, emulator path, which .gba the Play button launches, default trainer dialogue text, startup page preference, log panel visibility, Porymap path.

2. **Shared data layer** — New `shared_data.py` with a `ProjectData` class that both editors can read from. Change signals (`trainers_changed`, `items_changed`, etc.) automatically tell the other side to refresh when you save. Save PorySuite trainers → EVENTide's trainer dropdown updates without reopening.

3. **Trainer tab now shows battle dialogue** — New "Dialogue" tab on the trainer detail panel. When you select a trainer, it searches all map text.inc files for that trainer's battle text (intro, defeat, post-battle). Shows editable text fields grouped by map. Editing the text here writes back to the correct text.inc file on Save. Also added a prize money field.

### What changed
- **`settingsdialog.py`** — Complete rewrite with sidebar categories
- **`shared_data.py`** (new) — ProjectData class with change signals, text.inc search, save coordination
- **`unified_mainwindow.py`** — Now creates ProjectData, wires change signals, Play button reads settings
- **`ui/trainers_tab_widget.py`** — New Dialogue tab with text.inc search/display/edit, prize money field
- **`mainwindow.py`** — Trainer save now also writes dialogue edits back to text.inc

### Files changed
- `settingsdialog.py` (rewritten)
- `shared_data.py` (new)
- `unified_mainwindow.py` (updated)
- `ui/trainers_tab_widget.py` (expanded)
- `mainwindow.py` (updated trainer save)

---

## [2026-04-01] — Unified Editor: Phase 1 — Single window with icon toolbar

### Type
Major Enhancement

### Summary
Merged PorySuite (data editor) and EVENTide (map/script editor) into a single window. Instead of two separate apps with a launcher that makes you pick one, there's now one window with an RPG Maker XP-style icon toolbar across the top. All editors are accessible by clicking their icon in the toolbar. This is the shell — no new features or shared data yet, just everything in one place.

### What changed
- **New file: `unified_mainwindow.py`** — The new main window with icon toolbar, stacked content area, shared log panel, and status bar
- **17 placeholder toolbar icons** in `res/icons/toolbar/` — colored squares for now, user will replace with proper icons
- **Project selector simplified** — Each project now has a single "Open" button instead of separate PorySuite/EVENTide buttons. "New Project" and "Open Plugins Folder" buttons hidden (not needed in unified mode)
- **`app.py` updated** — New `_launch_unified()` method creates both editors, loads data into each, then moves their widgets into the unified window
- **Toolbar layout**: `[Save] [Make] [Make Modern] | [Pokemon] [Pokedex] [Moves] [Items] [Trainers] [Starters] | [Events] [Maps] [Layouts] [Region Map] | [UI] [Config] | [Play]`
- **Save button** saves both PorySuite and EVENTide data at once
- **Make / Make Modern** buttons build the ROM (same as before, now on the toolbar with F5/F6)
- **Play button** launches the .gba file using Windows default program (defaults to pokefirered_modern.gba, falls back to pokefirered.gba)
- **Full menu bar**: File, Edit, View, Tools, Git, Help — all consolidated
- **Page switching** correctly flushes PorySuite data when leaving a tab (items, pokemon, moves, starters, pokedex) and lazy-loads when entering

### Files changed
- `unified_mainwindow.py` (new)
- `res/icons/toolbar/*.png` (17 new placeholder icons)
- `app.py` (added `_launch_unified`)
- `projectselector.py` (single Open button, hid New Project + Plugins buttons)

---

## [2026-04-01] — EVENTide: User guide (FAQ-style documentation)

### Type
Documentation

### Summary
Added `eventide/docs/GUIDE.md` — a plain-English user guide explaining how to use EVENTide alongside PorySuite and Porymap. Covers the full workflow for adding trainers, regular NPCs, editing dialogue, and common troubleshooting questions. Written as FAQ-style instructions since the three tools share a project folder but don't directly communicate, which is confusing without documentation.

---

## [2026-04-01] — Event Editor: Replace "New Quest NPC" with general "New NPC Script" + PorySuite trainer integration

### Type
Enhancement

### Summary
Replaced the oddly-specific "New Quest NPC" button with a general "New NPC Script" button that opens a menu with four template types. The Trainer template now reads PorySuite's `trainers.json` to let you pick from the actual trainer list and auto-names text labels based on the trainer's identity.

### Changed
- **"New NPC Script" button** replaces "New Quest NPC" — opens a menu with 4 options:
  - **Simple Talker**: One-command script, one text label
  - **Trainer**: Opens a trainer picker populated from PorySuite's trainer data. Picks up the trainer's display name and class to auto-name text labels (e.g. picking TRAINER_LASS_IRIS → creates `MtMoon_1F_Text_IrisIntro`, `MtMoon_1F_Text_IrisDefeat`). Intro text gets flavor text from the trainer class. No more TRAINER_PLACEHOLDER.
  - **Item Giver**: Flag-gated give-item (2 pages, auto-picks unused flag)
  - **Flag-gated NPC**: Before/after dialogue based on a flag
- **PorySuite integration**: `_load_porysuite_trainers()` reads `src/data/trainers.json` (written by PorySuite's trainer editor) to get trainer names, classes, and metadata
- **Auto-scaffold on Add Command**: Adding a trainerbattle variant from the command selector auto-creates text labels with placeholder content
- All templates auto-register text labels and script labels for dropdown population

---

## [2026-04-01] — Event Editor: Inline text editing for trainer battle dialogue

### Type
Enhancement

### Summary
The Trainer Battle edit dialog now shows the actual dialogue text below each label dropdown (Intro, Defeat, Victory, etc.). You can read and edit the text right there without having to find it in text.inc manually. When you change the label dropdown, the text area updates to show that label's content. When you edit the text, it updates the in-memory text data so it gets saved along with the next save. Text uses the same `\n`/`\p`/`\l`/`$` control codes as the .inc files.

### Changed
- Added `_make_text_field()` helper that pairs a label combo with a `QPlainTextEdit` showing the resolved text content
- TrainerBattleWidget now shows editable text boxes for Intro, Defeat, Victory, and Not Enough Pokémon fields
- Text edits update `_ALL_SCRIPTS['__texts__']` dict directly, which gets written to `text.inc` on save
- Continue Script field stays as a label-only dropdown (it's a script reference, not text)

---

## [2026-04-01] — Event Editor: Fix trainer battle field alignment and variant-aware widget

### Type
Bug fix

### Summary
Trainer battle commands had all fields shifted by one position — the Trainer field showed an intro text label, the Intro field showed the defeat label, etc. This happened because the widget assumed `parts[0]` was a battle type number, but pokefirered uses named command variants (`trainerbattle_single`, `trainerbattle_double`, etc.) where the type is in the command name itself, not as an argument. The entire TrainerBattleWidget was rewritten to be variant-aware, with different field layouts per variant. The stringizer was also fixed.

### Changed
- `_TrainerBattleWidget` is now variant-aware: adapts its fields based on which command variant it represents
  - `trainerbattle_single`: Trainer, Intro, Defeat, optional Continue Script
  - `trainerbattle_double`: Trainer, Intro, Defeat, Not Enough Pokémon, optional Continue Script
  - `trainerbattle_no_intro`: Trainer, Defeat only
  - `trainerbattle_earlyrival`: Trainer, Flags, Defeat, Victory
- Removed the Type dropdown (type is encoded in the command name, not editable)
- Fixed the stringizer to parse args with Trainer as parts[0] instead of parts[1]
- Command selector now offers Single/Double/No Intro variants instead of generic "Trainer Battle"
- `to_tuple()` outputs the correct variant command name

---

## [2026-04-01] — Event Editor: Find in commands (Ctrl+F)

### Type
Feature

### Summary
Press Ctrl+F or click "Find" to open an inline search bar above the command list. As you type, matching commands are highlighted with a subtle blue background and a count is shown. Use Next/Prev (or Enter) to jump between matches with wrap-around. Close with the × button or press Ctrl+F again.

---

## [2026-04-01] — Event Editor: Drag-to-reorder commands in the list

### Type
Enhancement

### Summary
Commands in the event command list can now be reordered by dragging and dropping. The underlying data (`_cmd_tuples`) syncs automatically after each drop via a custom `_DraggableCommandList` subclass that emits a `rows_reordered` signal. This works alongside the existing Move Up/Down options in the right-click context menu.

---

## [2026-04-01] — Event Editor: Phase 6 command selector — recently used commands

### Type
Enhancement

### Summary
The command selector dialog now shows a "Recent" row at the top with up to 8 recently used commands. Picks are tracked for the session lifetime (no file persistence needed). The recent row is searchable alongside the main grid, and auto-hides when empty or when no recent commands match the search filter.

---

## [2026-04-01] — Event Editor: Phase 5 dropdown conversions

### Type
Enhancement

### Summary
Replaced free-text QLineEdit fields with searchable dropdowns and constrained spinboxes across 8 command widgets, eliminating typo-prone manual entry. All fields now pull from actual project data.

### Changed
- **ConstantsManager** now loads 271 special function names from `data/specials.inc` (sorted, deduplicated, NullFieldSpecial filtered out)
- **Message widget**: Label field → searchable label dropdown (type-ahead from current map's script labels)
- **Trainer Battle widget**: Intro text, Defeat text, and Continue Script fields → searchable label dropdowns
- **Special widget**: Free-text ID field → searchable dropdown of all 271 special function names from the project
- **SpecialVar widget**: NEW dedicated widget (was falling back to generic text field). Now has a VAR_ picker for the destination variable and a searchable specials dropdown for the function name
- **Buffer Species/Item/Move widgets**: Buffer slot free-text → QSpinBox constrained to 0-2
- **PokeMart widget**: Items label field → searchable label dropdown
- **SetObjectMovementType widget**: NEW dedicated widget (was falling back to generic text field). Now has an object local ID dropdown and a MOVEMENT_TYPE_ picker

---

## [2026-04-01] — Event Editor: Right-click context menu on command list

### Type
Feature

### Summary
Right-clicking any command in the event command list now shows a context menu with Edit, Cut, Copy, Paste, Duplicate, Move Up/Down, Insert Command, Delete, and Go To →. Matches RPG Maker XP's command list behavior. Actions are grayed out when not applicable (e.g. Paste disabled with empty clipboard, Move Up disabled on first item).

---

## [2026-04-01] — Region Map: Dungeon layer editing

### Type
Feature

### Summary
The Region Map tab now supports editing both the Map layer (overworld locations) and the Dungeon layer (caves, tunnels, indoor areas like Mt. Moon, Victory Road, Pokemon Tower). Previously only the Map layer was visible and editable — the Dungeon layer was preserved in the file but invisible.

### Changed
- **Layer toggle buttons**: "Map" and "Dungeon" toggle buttons next to the region selector let you switch which layer you're viewing and editing.
- **Backend loads both layers**: `_load_layout` now parses both `[LAYER_MAP]` and `[LAYER_DUNGEON]` sections from the layout .h files, stored in `layouts` and `dungeon_layouts` dicts.
- **Save writes both layers**: `_save_layout_both` replaces both layer sections in the .h file in a single write, with correct line offset calculation after MAP layer replacement.
- **Full section dropdown**: Section combo now shows ALL known MAPSEC constants (from region_map_sections.json), not just what's currently on the grid — so dungeon-specific sections like MAPSEC_MT_MOON can be assigned.
- **Clone/rename/delete propagate**: Region operations now correctly copy/move/remove dungeon layer data alongside the map layer.

---

## [2026-04-01] — EVENTide: Unsaved changes tracking and Save/Discard/Cancel dialog

### Type
Feature

### Summary
EVENTide now tracks unsaved changes and prompts before closing, switching maps, or refreshing — matching PorySuite's save model. All saves are manual (Save button only), no auto-saves to disk.

### Changed
- **Dirty tracking**: Event Editor emits `data_changed` signal on any edit (commands, properties, pages, quest template). Main window marks `[*]` in title bar via Qt's `setWindowModified`.
- **Save/Discard/Cancel on close**: `closeEvent` on EventideMainWindow prompts using `create_unsaved_changes_dialog` from `app_util.py` — same dialog PorySuite uses.
- **Save/Discard/Cancel on map switch**: "Open Map" checks for unsaved changes before loading a different map.
- **Save/Discard/Cancel on refresh**: "Refresh from Disk" (Ctrl+R) checks for unsaved changes before reloading.
- **Modified flag reset**: Cleared after successful save and after loading a project/map from disk.
- **Loading guard**: Property field signal connections (textEdited, valueChanged, currentIndexChanged) are suppressed during field population via `_loading` flag so switching between objects doesn't falsely mark as dirty.

---

## [2026-04-01] — Event Editor: Fix map-switch data corruption (stale index writes)

### Type
Bugfix

### Summary
Switching maps could corrupt object data — e.g. Oak losing his sprite, or showing properties from the previous map. The root cause: when loading a new map, the object combo's signal triggered `_collect_current()` which still had the old map's index, writing stale UI values (empty graphics, wrong script names) into the new map's object list.

### Changed
- **Reset `_current_obj_idx = -1` at the start of `_load_map`**: Prevents `_collect_current()` from writing stale UI data from the previous map into the newly loaded map's objects. This was the cause of Oak's sprite disappearing after switching from another map.

---

## [2026-04-01] — Event Editor: Sprite preview — correct walk animation and frame detection

### Type
Bugfix

### Summary
Fixed two bugs in the sprite preview: walk animation was showing wrong directions (down/up/left instead of walking down) because frames 0,1,2 were used as walk steps when they're actually directional stands. Also fixed 32×32 sprites (Articuno etc.) being cut in half because frame width was hardcoded to 16px.

### Changed
- **Correct walk-down cycle**: 9-frame sheets now animate stand(0) → walk1(3) → stand(0) → walk2(4) — the actual down-walking frames. Previously used frames 0,1,2 which are down-stand, up-stand, left-stand.
- **Dynamic frame width detection**: Square sheets (32×32) display as full single frames. Sheets divisible by height with ≤4 results use height as frame width (64×32 → two 32×32 frames). Everything else uses standard 16px width.
- **3-frame sheets static**: Sprites like Agatha (48×32, 3 directional stands) correctly show as a static down-facing image instead of trying to animate.
- Updated docstring with correct GBA sprite sheet frame layout documentation.

---

## [2026-04-01] — Event Editor: Animated sprite preview

### Type
Feature

### Summary
Replaced the tiny static sprite sheet thumbnail with a proper animated walk-down cycle preview. The sprite is extracted from the GBA sprite sheet, has its background made transparent (GBA palette index 0), scaled up 3× with nearest-neighbor for crisp pixel art, and animated through the walk-down cycle at ~3 FPS.

### Changed
- **SpritePreview widget rewritten**: Now extracts individual frames from the horizontal sprite strip instead of showing the whole sheet scaled down. Handles varying frame sizes across all GBA overworld sprites.
- **GBA transparency**: Manually converts palette index 0 (top-left pixel color) to transparent alpha, matching the `_load_gba_sprite()` approach from PorySuite's species tab. Qt doesn't reliably handle tRNS in 4-bit indexed PNGs.
- **3× nearest-neighbor scaling**: Pixel art stays crisp at larger size instead of getting blurry from bilinear interpolation.
- **Walk cycle animation**: 4-frame cycle (stand → walk1 → stand → walk2) using QTimer at 333ms intervals.
- **Larger display**: Preview area increased from 48-96px to 64-128px height with rounded border.

### Files Changed
- `eventide/ui/widgets.py` — Complete rewrite of `SpritePreview` class with frame extraction, transparency, animation timer

---

## [2026-04-01] — Event Editor: Quest template, page rename, unused flag finder

### Type
Feature

### Summary
Three workflow tools that make it practical to create new scripts from scratch in the editor: a "New Quest NPC" button that scaffolds a complete 3-state quest with auto-picked unused flags, a "Rename" button for script label tabs, and a "Find Unused Flag" button that scans the entire project and tells you which flags are available.

### Added
- **New Quest NPC template**: Creates a 3-page script (give quest → give reward → generic thanks) with placeholder text, a reward item, and two auto-selected unused flags. All fields are editable via double-click after creation.
- **Rename Page**: Renames a page tab's script label. Also updates any goto/call commands in other pages that reference the old label — so renaming `_Sub2` to `_QuestReward` automatically fixes the goto that points to it.
- **Find Unused Flag**: Scans all scripts.inc files and C source for FLAG_ references, then reports which FLAG_UNUSED_* constants aren't taken. Shows the next available flag and a list of the first 10.

### Files Changed
- `eventide/ui/event_editor_tab.py` — `_on_rename_page()`, `_replace_label_in_cmd()`, `_on_find_unused_flag()`, `_on_new_quest_template()`, new buttons in left panel

---

## [2026-04-01] — Event Editor: Give Pokemon UX fix, Go To navigation, trainer battle variants

### Type
Feature / Fix

### Summary
Give Pokemon dialog now explains that MOVE_NONE means the game auto-fills moves from the species' learnset at the given level. Fixed a bug where setting move 1 and move 3 but not 2 would silently drop move 3. Stringizer now shows held item and moves (or "default by level") on separate lines.

### Changed
- **Give Pokemon dialog**: Added tooltip and note explaining MOVE_NONE = game auto-fills from learnset
- **Give Pokemon stringizer**: Now shows held item and moves on continuation lines. Shows "(default by level)" when all moves are MOVE_NONE.

### Fixed
- **Give Pokemon move output**: If you set moves 1 and 3 but left 2 as MOVE_NONE, the old code skipped MOVE_NONE entries and only output move 1. Now outputs all 4 move slots when any custom move is set, since pokefirered expects all 4 positional args.

### Files Changed
- `eventide/ui/event_editor_tab.py` — `_GiveMonWidget` tooltip/note, `to_tuple()` outputs all 4 moves when any are custom, stringizer multi-line display

---

## [2026-04-01] — Event Editor: Go To navigation, trainer battle variants, all event types

### Type
Feature

### Summary
Added "Go To →" button that follows goto/call/trainerbattle targets to their destination script. Fixed trainer battle parser to properly recognize variant commands (trainerbattle_single, trainerbattle_no_intro, trainerbattle_earlyrival). Trainer battles now display intro/defeat dialogue labels and continue script inline. Self-verification runs after every map load.

### Changed
- **Go To → button**: Select any goto, call, conditional branch, or trainer battle command with a continue script, click "Go To →", and the editor navigates directly to that target — switching page tabs or even switching to a different event if needed. If the target isn't loaded as a page yet, it gets dynamically added.
- **Trainer battle variants**: Parser now correctly handles `trainerbattle_single`, `trainerbattle_no_intro`, `trainerbattle_earlyrival`, `trainerbattle_double` as distinct commands instead of lumping the variant name into the args string.
- **Trainer battle display**: Shows intro text label, defeat text label, and continue script label on separate lines below the trainer name. If text content is available, shows the first line of actual dialogue in quotes.
- **Self-verification**: `_verify_loaded_events()` runs after every map load and logs warnings about missing scripts, orphan labels, empty maps, and count mismatches.

### Files Changed
- `eventide/ui/event_editor_tab.py` — `_on_goto_target()`, `_extract_goto_target()`, trainer battle stringizer update, category/widget factory updates for variants
- `eventide/backend/eventide_utils.py` — Trainer battle parser now splits on whitespace to preserve variant command name
- `eventide/CLAUDECONTEXT.md` — New: context doc for future threads
- `eventide/TROUBLESHOOTING.md` — New: common issues and solutions

---

## [2026-04-01] — Event Editor: Load all event types, Event Editor tab first

### Type
Feature

### Summary
Event Editor now loads ALL event types from map.json — not just NPCs. Triggers (coord_events like Oak's approach script), signs (bg_events), and map scripts (on-transition/on-frame) all appear in the Object dropdown with type labels. The Event Editor tab is now the leftmost/default tab in EVENTide, since event editing is the app's primary purpose.

### Changed
- **All event types loaded**: Object dropdown now shows entries from object_events (`[NPC]`), coord_events (`[Trigger]`), bg_events (`[Sign]`), and map_scripts (`[MapScript]`). Previously only object_events were loaded, which meant Oak's scripts (on coord_events) showed blank.
- **Event Editor tab is now first**: Moved to leftmost position and opens by default when EVENTide launches, ahead of Maps, Layouts, and Region Map tabs.
- **Properties panel adapts**: NPC events show full editable properties (ID, position, graphic). Triggers and signs show position/type read-only. Map scripts hide spatial properties entirely.
- **Save preserves event types**: On save, events are split back into their correct map.json arrays (object_events, coord_events, bg_events). Map script entries are synthetic and only saved to scripts.inc.

### Fixed
- **Oak's script blank/0x0**: Oak's object_event has `script: "0x0"` because his real scripts are on coord_events (OakTriggerLeft, OakTriggerRight). These are now loaded and visible.
- **Invisible triggers**: Step-on trigger tiles (coord_events) were completely invisible. Now shown with `[Trigger]` prefix.
- **Missing signs**: Sign scripts (bg_events) were not loaded. Now shown with `[Sign]` prefix.
- **No map scripts**: On-transition and on-frame scripts that run when entering a map were invisible. Now shown as `[MapScript]` entries.

### Files Changed
- `eventide/mainwindow.py` — Reordered tabs: Event Editor first, then Maps, Layouts, Region Map
- `eventide/ui/event_editor_tab.py` — `_load_map()` now iterates all 4 event types; `_on_object_changed()` adapts properties panel per event type; `_collect_current()` only writes back editable NPC properties; `_on_save()` splits events back to correct arrays

---

## [2026-04-01] — Event Editor: RMXP-style text list, sub-label tabs, double-click editing

### Type
Feature / Rewrite

### Summary
Complete visual overhaul of the Event Editor to match RPG Maker XP's event editor style. Commands now display as a flat text list with one line per command (e.g. `@>Text: Hello world`, `@>Conditional Goto: If [VAR] == 2 → Label`), instead of inline form widgets. Double-clicking a command opens a popup edit dialog. When a script has `goto`/`call` targets, those sub-labels automatically appear as page tabs so you can click through and edit the entire script chain — not just the entry point. Message text is now loaded project-wide.

### Changed
- **Command list display**: Replaced scroll area + inline widget stack with a QListWidget showing RMXP-style stringized text. Each command is one line starting with `@>`. Empty `@>` at bottom for insertion.
- **Edit paradigm**: Double-click a command to open a popup dialog (`_CommandEditDialog`) with the parameter widget. The list itself is read-only display text — just like RMXP.
- **Stringizer**: New `_stringize()` function converts command tuples to human-readable display strings (e.g. `@>Set Flag: FLAG_GOT_STARTER`, `@>Wild Battle: SPECIES_PIKACHU Lv.5`)
- **Sub-label page tabs**: When loading a script, all goto/call targets are automatically collected and shown as clickable tabs (e.g. SignLady → tabs for SignLadyDone, SignLadyGoReadSign, SignLadyStartShowSign, etc.). Users can navigate the entire script chain.
- **Color coding**: Regular commands display in default text color (white in dark mode). Only jump commands (blue) and conditionals (amber) get subtle color — not entire lines painted red.
- **Command selector**: Rewritten with 2-column button grid, tabs labeled "1" / "2" / "3" matching RMXP's Event Commands dialog.
- **Save updated**: Each page tab saves under its own label (not numbered _Page2/_Page3), so sub-labels are written back correctly to scripts.inc.
- **Left panel**: "Pages:" label renamed to "Script Labels:" with scroll buttons for many tabs.

### Fixed
- **Empty message text boxes**: Added `parse_all_texts()` that searches both map-local `text.inc` AND `data/text/*.inc` (sign_lady, fame_checker, etc.).
- **Sub-scripts inaccessible**: Goto/call targets were visible in the command list but couldn't be navigated to or edited. Now they're automatic page tabs. Fixed bug where `parse_script_pages` was intercepting scripts before `_build_script_pages` could collect sub-labels — now always uses the unified label collection.
- **Unreadable red text**: All conditional lines were painted red, making them hard to read. Now only jump targets (blue) and conditionals (amber) get subtle color.
- **Commands displayed in reverse order**: `_add_list_item` used "insert before empty @> line" logic during initial page load, but the empty line didn't exist yet — causing every command to insert at position 0, reversing the list. Fixed by using `addItem` (append) during page load.
- **Movement routes display inline**: `applymovement` now looks up the movement label and shows all steps with `$>` prefixes (e.g. `$>Move Up`, `$>Move Right`) matching RMXP's "Set Move Route" display. Falls back to label name for external/common movements.
- **Message text shows in full**: Multi-line messages display with RMXP continuation format (`: :` prefix on continuation lines) instead of truncating to 50 chars. Trailing `$` markers stripped.

### Files Changed
- `eventide/ui/event_editor_tab.py` — Stringizer, _CommandEditDialog, QListWidget display, `_build_script_pages()` for sub-label collection, rewritten action handlers, command selector 2-column grid, color scheme fix
- `eventide/backend/eventide_utils.py` — `parse_all_texts()`, optional `texts` parameter on parsers, `write_scripts_inc` supports new per-label format

---

## [2026-04-01] — Event Editor: Scroll-wheel guard on all dropdowns

### Type
Bugfix

### Summary
Combo boxes and spin boxes inside the event editor command widgets no longer change value when the mouse wheel scrolls over them. You must click a dropdown to give it focus before the scroll wheel does anything. This prevents accidentally changing script values while scrolling through the event list. Uses the same scroll guard system already in PorySuite's main UI.

### Fixed
- **Accidental value changes while scrolling**: Every QComboBox, QSpinBox, and ConstantPicker in command rows is now guarded — scroll wheel is ignored unless the widget has focus (user clicked it)

### Files Changed
- `eventide/ui/event_editor_tab.py` — Import `install_scroll_guard_recursive`, call it in `_CommandRow.__init__`

---

## [2026-04-01] — Event Editor: Real dropdowns, proper conditionals, clean spacing

### Type
Feature / Bugfix

### Summary
Complete overhaul of the Event Editor to be a real visual editor instead of manual text entry. Every field that references a project constant, object, flag, variable, script label, or movement now uses a searchable dropdown populated from actual project data. Conditional commands (goto_if_eq, call_if_set, etc.) are now properly parsed and displayed with dedicated widgets instead of garbled text. No-arg commands display as compact header-only bars. Consistent spacing across all command rows.

### Fixed
- **Conditional commands were broken**: `goto_if_eq VAR, VALUE, LABEL` was parsed as generic `goto_if` with garbled args, showing "Condition: SN_LADY, Goto: 2" instead of the actual variable and target. Now each variant (goto_if_eq, goto_if_set, goto_if_unset, call_if_eq, etc.) has its own parser entry and dedicated widget.
- **Missing commands**: textcolor, specialvar, setworldmapflag, setobjectmovementtype, famechecker, signmsg, normalmsg, copyobjectxytoperm, message (standalone), map_script, .equ, .byte — all now properly parsed instead of being silently dropped.
- **UI spacing random/ugly**: Added consistent 4px spacing between command rows, 4px container margins. No-arg commands (lock, end, return, faceplayer, etc.) now display as compact single-line header bars without wasted empty body panels.

### Changed
- **Conditional widgets**: Replaced broken `_GotoIfWidget`/`_CallIfWidget` with 4 proper widgets: `_GotoIfCompareWidget` (var dropdown + comparison operator + value + label dropdown), `_GotoIfFlagWidget` (flag dropdown + set/unset + label dropdown), `_CallIfCompareWidget`, `_CallIfFlagWidget`
- **Object fields → dropdowns**: ApplyMovement target, WaitMovement, RemoveObject, AddObject, ShowObject, HideObject, TurnObject, SetObjectXY — all object ID fields now use searchable dropdowns populated from the current map's object_events
- **Movement fields → dropdowns**: ApplyMovement movement field now shows movement labels from the current script file plus common movement constants
- **Label fields → dropdowns**: Goto, Call, and all conditional → label fields now use searchable dropdowns populated from all script labels in the current file
- **Map fields → dropdowns**: ShowObject/HideObject map field uses ConstantPicker with MAP_* constants
- **No-arg widget compaction**: 17 no-arg widgets marked `_header_only=True` — they render as just a colored header bar with no body panel
- **Writer updated**: New conditional tuple formats are written back correctly as `goto_if_eq VAR, VALUE, LABEL` etc.
- **Command selector updated**: "Conditional Goto"/"Conditional Call" replaced with specific entries: "If Variable → Goto", "If Flag → Goto", "If Variable → Call", "If Flag → Call"

### Files Changed
- `eventide/backend/eventide_utils.py` — Parser rewrite for 16 conditional variants, 12 new command parsers, writer support for new tuple formats
- `eventide/ui/event_editor_tab.py` — 4 new conditional widgets, module-level context system, dropdown conversions for all object/label/movement fields, header-only no-arg display, consistent spacing

---

## [2026-03-31] — Fix: Dark mode readability across all EVENTide UI

### Type
Bugfix

### Summary
Fixed white-on-white unreadable text in dark mode. All `setStyleSheet()` calls in EVENTide UI files were cascading into child input widgets (QComboBox, QLineEdit, QSpinBox, QPlainTextEdit), overriding their native dark palette. Scoped every stylesheet with `#objectName` CSS selectors so borders and backgrounds only apply to the intended container widget, letting all form inputs keep their OS dark theme colors.

### Fixed
- **_CommandRow header bar**: Scoped to `#cmdHeader` — colored header no longer bleeds into child widgets
- **_CommandRow inner widget border**: Scoped to `#cmdParams` — the border stays on the parameter container, QComboBox/QLineEdit/etc. inside keep native dark palette
- **_CommandRow selection highlight**: Scoped to `#cmdRow` — blue selection border doesn't cascade to children
- **SpritePreview**: Changed from hardcoded `#222`/`#444` to `palette(base)`/`palette(mid)` for theme-aware rendering
- **Region Map placeholder**: Changed from hardcoded `#222`/`#888` to `palette(base)`/`palette(dark)`
- **Mainwindow git status label**: Scoped to `#git_status_bar` with `palette(dark)` color

### Root Cause
In Qt, calling `widget.setStyleSheet("border: ...")` without a CSS selector applies that style to the widget AND all its descendants. When the parent command container got a stylesheet, every dropdown, text field, and spinner inside it stopped using the OS dark palette and reverted to default light styling — producing white backgrounds with white (dark-mode) text.

### Files Changed
- `eventide/ui/event_editor_tab.py` — Scoped all 3 stylesheet calls in `_CommandRow` with `#objectName` selectors
- `eventide/ui/widgets.py` — SpritePreview uses `palette()` instead of hardcoded hex colors
- `eventide/ui/region_map_tab.py` — Placeholder label uses `palette()` references
- `eventide/mainwindow.py` — Git status label scoped with object name selector

---

## [2026-04-01] — Phase 5+: Color-coded headers, PokeMart builder, copy/paste, 75+ widgets

### Type
Feature

### Summary
Added RPG Maker-style colored command header bars showing category + friendly name at a glance. Each command is wrapped in a `_CommandRow` with a color-coded header (blue=dialogue, purple=flags/vars, red=flow control, green=movement, orange=sound, teal=screen, etc.). Built the PokeMart list builder widget with add/remove item rows. Added copy/cut/paste for commands that works across pages and objects. Added CopyVar, CompareVarToVar, Waitstate, SetMetatile widgets.

### Added
- **_CommandRow wrapper**: Every command widget is now wrapped in a `_CommandRow` that displays a colored header bar with a bullet (◆) and the command's friendly name. 10 category colors for visual grouping.
- **PokéMart list builder** (`_PokeMartWidget`): Editable list of item pickers for shop inventory, add/remove rows, label field.
- **Copy/Cut/Paste buttons**: Class-level clipboard that persists across page and object changes. Copy stores the command tuple, Paste creates a new widget from it at the insertion point.
- **New widgets**: `_CopyVarWidget` (two var pickers), `_CompareVarToVarWidget`, `_WaitstateWidget`, `_SetMetatileWidget` (x/y + tile + impassable checkbox)
- **Parser additions**: pokemart and setmetatile command parsing
- **Writer additions**: pokemart label output

### Changed
- **Command display**: All commands now show a colored header bar before their parameter fields, making the command list scannable at a glance without reading parameter details.
- **Command selector**: Added Copy Variable, Compare Var to Var, Wait State, Set Metatile, PokéMart to the appropriate pages.

### Files Changed
- `eventide/ui/event_editor_tab.py` — _CommandRow wrapper, 5 new widgets, copy/paste, category colors
- `eventide/backend/eventide_utils.py` — pokemart + setmetatile parser/writer support

---

## [2026-04-01] — Phase 3-5: Expanded parser, 65+ widgets, command reordering

### Type
Feature

### Summary
Expanded the script parser to handle every major command type with proper tuple output. Added 15+ more specialized command widgets (doors, decorations, pokemon pics, party checks, message wait/close). Built command selection + reordering (click to select, move up/down, duplicate). Refactored the parser to eliminate code duplication — only one `_parse_script_lines` implementation now.

### Added
- **Command selection**: Click any command widget to select it (blue highlight). Selected command determines insert position for new commands.
- **Move Up / Move Down buttons**: Reorder commands within a page by moving the selected command up or down.
- **Duplicate button**: Copy the selected command with all its current parameter values.
- **New command widgets**: `_OpenDoorWidget`, `_CloseDoorWidget`, `_WaitDoorAnimWidget`, `_AddDecorationWidget`, `_RemoveDecorationWidget`, `_GetPartySizeWidget`, `_CheckPlayerGenderWidget`, `_WaitMessageWidget`, `_CloseMessageWidget`, `_SetMonMoveWidget`, `_ShowMonPicWidget`, `_HideMonPicWidget`
- **Command selector additions**: Doors section on Page 2, expanded Pokemon section on Page 3 (set move, party size, gender check, mon pics), Decorations section on Page 3

### Changed
- **Script parser** (`eventide_utils.py`): Expanded `_parse_script_lines` to handle all warp variants, trainer battles, all flag/var ops, all item ops (with quantity), weather, screen effects, doors, decorations, buffers, money/coins, respawn, and 30+ no-arg commands. Refactored `parse_scripts_inc` to delegate to `_parse_script_lines` instead of duplicating its logic.
- **Script writer** (`eventide_utils.py`): Updated `lines_from_commands` to handle warp variants (all 5 types), simplified sound output (just constant name), and proper multi-arg positional tuples for warps and movements.

### Files Changed
- `eventide/backend/eventide_utils.py` — Parser expansion + refactor, writer updates
- `eventide/ui/event_editor_tab.py` — 15+ new widgets, command selection/reorder/duplicate, updated command selector pages

---

## [2026-04-01] — Phase 1+2: ConstantsManager, searchable pickers, 50+ command widgets

### Type
Feature

### Summary
Built the constants infrastructure and full visual script editor for EVENTide Phase 1 and Phase 2. Every dropdown in the event editor now pulls from the project's actual header files — items, species, moves, flags, vars, trainers, music, SFX, weather, maps, heal locations. The command selector dialog is reorganized into 3 tabbed pages (RPG Maker XP style) with a search bar. Over 50 command types now have specialized widgets with appropriate pickers instead of raw text fields.

### Added
- **ConstantsManager** (`eventide/backend/constants_manager.py`): Centralized loader that reads all `include/constants/` headers — ITEMS, SPECIES, MOVES, FLAGS, VARS, MUSIC, SFX, TRAINERS, WEATHER, HEAL_LOCATIONS, MAP_CONSTANTS, MAP_NAMES, MOVEMENT_TYPES, OBJECT_GFX, DECORATIONS. Also provides static lists for trainer battle types, message box types, compare operators, directions, and fade types.
- **ConstantPicker widget** (`eventide/ui/widgets.py`): Searchable QComboBox with type-ahead filtering (MatchContains). Shows pretty names like `Poke Ball  (ITEM_POKE_BALL)` but returns raw constants. Used by every command widget that needs a constant dropdown.
- **MapPicker widget** (`eventide/ui/widgets.py`): Combined map name combo + X/Y spinners for warp destinations.
- **SpritePreview widget** (`eventide/ui/widgets.py`): Reusable sprite display label.
- **50+ specialized command widgets** (`eventide/ui/event_editor_tab.py`):
  - **Dialogue & Logic**: Message (34-char counter + type dropdown), Yes/No, Multi-Choice, SetFlag/ClearFlag/CheckFlag (flag picker), SetVar/AddVar/SubVar (var picker + value), CompareVarToValue, GotoIf/CallIf (condition + label), Call, Goto, End, Return, Special, WaitButtonPress
  - **World & Characters**: Warp (5 types, map picker), ApplyMovement, WaitMovement, Remove/Add/Show/Hide Object, FacePlayer, TurnObject (direction dropdown), SetObjectXY, Lock/Release, FadeScreen (type dropdown), FadeScreenSpeed, PlaySE (SFX picker), PlayFanfare/PlayBGM (music picker), FadeOut/FadeInBGM, SetWeather (weather picker), DoWeather, ResetWeather, Delay (frame spinner with seconds display), SetFlashLevel, PlayMonCry (species picker)
  - **Battles & System**: TrainerBattle (type dropdown + trainer picker + text labels), WildBattle (species picker + level + shiny), GiveMon (species + level + item + 4 move pickers), GiveEgg (species picker), Give/Remove/CheckItem (item picker + qty), Add/Remove/CheckMoney, Add/RemoveCoins, SetRespawn (heal location picker), CheckPartyMove (move picker), BufferSpecies/Item/Move (slot + picker)
- **3-page command selector dialog**: Page 1 = Dialogue & Logic, Page 2 = World & Characters, Page 3 = Battles & System. Each page has grouped sections with bold headers. Search bar filters across all pages.

### Files Changed
- `eventide/backend/constants_manager.py` — New: centralized constants loader
- `eventide/ui/widgets.py` — New: ConstantPicker, MapPicker, SpritePreview
- `eventide/ui/event_editor_tab.py` — Full rewrite: 50+ widgets, 3-page selector
- `eventide/README.md` — Updated status

---

## [2026-03-31] — Event Editor rebuild, EVENTide docs

### Type
Feature

### Summary
Rebuilt the Event Editor from scratch with an object-centric architecture and specialized per-command widgets. Added EVENTide's own docs folder with command reference files and updated path resolution so the app finds them without relying on the pokefirered project root.

### Added
- **EVENTide docs** (`eventide/docs/eventide_whitelist.md`, `eventide/docs/script_commands.md`): Shipped command whitelist and FireRed bytecode reference inside EVENTide itself, copied from ProjectZeldamon.
- **Docs path resolution** (`eventide/backend/eventide_utils.py`): `_EVENTIDE_DOCS` constant checks `eventide/docs/` first before falling back to the project root. Both `_load_friendly_commands()` and `load_command_categories()` use it.
- **Specialized command widgets** (`eventide/ui/event_editor_tab.py`): `_MessageWidget`, `_WarpWidget`, `_GiveItemWidget`, `_WildBattleWidget`, `_ApplyMovementWidget`, `_CallWidget`, `_SoundWidget`, `_GenericWidget` — each with `to_tuple()` and `friendly_name()`.
- **Command selector dialog**: `_CommandSelectorDialog` with categorized tabs built from the whitelist, returns the raw command name.
- **Object properties panel**: Shows local_id, x, y, script label, graphics_id with sprite preview for the selected object event.
- **Page tabs per object**: Each object event's pages appear as tabs with scrollable command widget areas.

### Changed
- **Event Editor architecture** (`eventide/ui/event_editor_tab.py`): Complete rewrite. Now object-centric — combo selects object from `map.json` `object_events`, each object has pages of commands rendered as specialized widgets instead of plain text list items.
- **Save pipeline**: `_collect_current()` reads widget state back into data, writes `scripts.inc` + `text.inc` + `map.json` on save.

### Files Changed
- `eventide/docs/eventide_whitelist.md` — New: command whitelist with friendly labels
- `eventide/docs/script_commands.md` — New: FireRed bytecode command reference
- `eventide/backend/eventide_utils.py` — Added `_EVENTIDE_DOCS` path, updated doc loaders
- `eventide/ui/event_editor_tab.py` — Full rebuild with specialized widgets
- `eventide/README.md` — Updated status

---

## [2026-03-31] — Inter-tab signals, Region Map graphics, script writer

### Type
Feature / Fix

### Summary
Three improvements: tabs now auto-refresh each other after mutations, the Region Map editor renders actual tileset graphics matching the original TriforceGUI visual style, and the Event Editor can write scripts.inc back to disk.

### Added
- **Inter-tab refresh signals** (`eventide/mainwindow.py`, all tab files): Each tab emits `data_changed` after mutations. Maps changes refresh Region Map + Layouts; Layout changes refresh Maps; Region Map changes refresh Maps.
- **Script writer** (`eventide/backend/eventide_utils.py`): `lines_from_commands()` converts command tuples back to assembly lines with auto lock/release insertion. `merge_hidden_lines()` preserves comments and directives. `write_scripts_inc()` orchestrates full save with page labels and conditions.
- **Full Event Editor save** (`eventide/ui/event_editor_tab.py`): Save button now writes both scripts.inc and text.inc instead of text.inc only.

### Fixed
- **Region Map rendering** (`eventide/ui/region_map_tab.py`): Replaced colored boxes with the actual tileset image rendered by `build_region_map_image()`. Uses `RegionMapView` class matching TriforceGUI — transparent grid overlay, semi-transparent black grid lines, red selection rectangle, cell sizes derived from pixmap dimensions, no text labels cluttering the grid.

### Files Changed
- `eventide/mainwindow.py` — Inter-tab signal wiring
- `eventide/ui/maps_tab.py` — Added `data_changed` signal
- `eventide/ui/layouts_tab.py` — Added `data_changed` signal
- `eventide/ui/region_map_tab.py` — Full rewrite with tileset rendering
- `eventide/ui/event_editor_tab.py` — Full save (scripts.inc + text.inc)
- `eventide/backend/eventide_utils.py` — Added `lines_from_commands`, `merge_hidden_lines`, `write_scripts_inc`
- `eventide/README.md` — Updated status

---

## [2026-03-31] — EVENTide tabs wired to backends

### Type
Feature

### Summary
Connected all four EVENTide tab widgets to their backend modules. Every button and combo box in every tab now drives real backend logic instead of showing placeholder text.

### Changed
- **Maps tab** (`eventide/ui/maps_tab.py`): Tree populates from `map_groups.json` with section/layout columns, search filtering, all 8 buttons wired to `MapRenamer`, warp check/clean wired to `WarpValidator`.
- **Layouts tab** (`eventide/ui/layouts_tab.py`): Layout combo from `layouts.json`, tileset combos from `graphics.h` parsing, rename/delete/clean layouts via `LayoutRenamer`, apply tilesets, rename secondary tilesets via `TilesetRenamer`.
- **Region Map tab** (`eventide/ui/region_map_tab.py`): Clickable color-coded grid via `QGraphicsScene`, region selector from `RegionMapManager.list_regions()`, section assignment by clicking cells, save/reload grid, clone/rename/delete regions, rename sections.
- **Event Editor tab** (`eventide/ui/event_editor_tab.py`): Open Map dialog listing all maps with `scripts.inc`, parse scripts into command tuples and pages via `eventide_utils`, display with friendly labels, add/delete/reorder commands, add/delete pages, save `text.inc`.

### Files Changed
- `eventide/ui/maps_tab.py` — Full rewrite with backend wiring
- `eventide/ui/layouts_tab.py` — Full rewrite with backend wiring
- `eventide/ui/region_map_tab.py` — Full rewrite with backend wiring
- `eventide/ui/event_editor_tab.py` — Full rewrite with backend wiring
- `eventide/README.md` — Updated status

---

## [2026-03-31] — EVENTide sister app scaffolding

### Type
Feature

### Summary
Created EVENTide as a sister app to PorySuite within the PorySuite-Z launcher. EVENTide handles map/world management and event editing — functionality ported from TriforceGUI and ProjectZeldamon's EVENTide. Both apps launch from the same project selector and can cross-launch each other.

### Added
- **EVENTide app** (`eventide/`): New sister app with 4-tab layout (Maps, Layouts & Tilesets, Region Map, Event Editor), shared log panel, full Git menu matching PorySuite's interface.
- **Dual-launch project selector** (`projectselector.py`): Each saved project now shows two buttons — "PorySuite" and "EVENTide" — instead of a single clickable label.
- **Cross-launch** (`app.py`, `mainwindow.py`, `eventide/mainwindow.py`): File > Open in EVENTide (from PorySuite) and File > Open in PorySuite (from EVENTide) open the same project in the other app.
- **Backend modules** (`eventide/backend/`): Ported from TriforceGUI — map_renamer, layout_renamer, tileset_renamer, warp_validator, region_map_manager. Ported from ProjectZeldamon — eventide_utils. All use `root_dir` parameter instead of hardcoded paths.
- **Shared file utilities** (`eventide/backend/file_utils.py`): `replace_in_file` and `replace_repo_wide` for repo-wide text replacements.

### Files Changed
- `app.py` — Dual-app launch logic, cross-launch signal wiring
- `projectselector.py` — Two-button project rows
- `mainwindow.py` — Added `open_in_eventide_signal` and "Open in EVENTide" File menu action
- `eventide/mainwindow.py` (new)
- `eventide/ui/maps_tab.py` (new)
- `eventide/ui/layouts_tab.py` (new)
- `eventide/ui/region_map_tab.py` (new)
- `eventide/ui/event_editor_tab.py` (new)
- `eventide/backend/map_renamer.py` (new)
- `eventide/backend/layout_renamer.py` (new)
- `eventide/backend/tileset_renamer.py` (new)
- `eventide/backend/warp_validator.py` (new)
- `eventide/backend/region_map_manager.py` (new)
- `eventide/backend/eventide_utils.py` (new)
- `eventide/backend/file_utils.py` (new)

---

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

