# PorySuite-Z Unified Editor — Master Plan

## Goal

Merge PorySuite (data editor) and EVENTide (script editor) into a single window with an RPG Maker XP-style toolbar. One app, one window, all editors accessible from icon buttons. Universal save model — edit freely, save when ready, prompt on close.

---

## Current State (2026-04-03)

Phases 1 through 6 are **complete**. Phase 5E (EVENTide Improvements) is **complete** — all features done including Move Camera Command, Comprehensive Tooltips, Live Settings Reload, and Hidden Item Editor. **Phase 7 — Porymap Integration** is functional — install, launch, bidirectional map sync all working. Polish remaining.

**13 toolbar pages are live:** Pokemon, Pokedex, Moves, Items, Trainers, Starters, Credits, Event Editor, Maps, Layouts & Tilesets, Region Map, UI Settings, Config.

---

## Completed Phases

### Phase 1: Unified Window Shell — COMPLETE

Everything that was two separate apps is now one window with a toolbar.

- `unified_mainwindow.py` — single window with menu bar, icon toolbar, stacked widget
- RPG Maker XP-style icon toolbar (32x32 icons, tooltips, group separators)
- Save, Make, Make Modern, Play buttons on toolbar
- QStackedWidget replaces tab bars — toolbar icons switch pages
- All PorySuite editors (Pokemon, Pokedex, Moves, Items, Trainers, Starters, Credits, UI, Config) in stacked widget
- All EVENTide editors (Event Editor, Maps, Layouts, Region Map) in stacked widget
- Shared log panel at bottom
- Status bar with git branch info
- Simplified launcher — single "Open" button per project, no plugin selection
- Play button launches .gba in emulator (configurable)
- Dirty tracking across all editors — title bar `[*]` when anything unsaved

### Phase 2: Shared Data & Settings — COMPLETE

- Unified save/dirty tracking — Ctrl+S saves both PorySuite and EVENTide data
- Close prompt catches unsaved changes from both sides
- Git integration (pull, push, commit) works from unified window
- Git pull Done button triggers full project refresh (PorySuite + EVENTide + status bar)
- Settings dialog exists for build commands and play/launch config
- Full shared data layer with change signals deferred — not needed since editors reload from disk on tab switch

### Phase 3: Cross-Editor Features — COMPLETE

All planned cross-editor bridges are working.

- "Set up battle script" button on Trainers tab → switches to Event Editor with status bar hint
- Right-click trainerbattle/giveitem commands in Event Editor → jump to Trainers/Items tab
- Context menu on Event Editor command list: "Edit Trainer Party", "Edit Item"
- Double-click map in Maps tab → opens in Event Editor
- Unified save/dirty tracking with title bar indicator
- Ctrl+S saves both editors, close prompt catches both sides

### Phase 3.5: VS Seeker Rematch System — COMPLETE

Full read and write support for the rematch tier system.

- Tier dropdown in Party tab for rematchable trainers
- Party switching between tiers
- Rematch map display
- Dynamic tier labels parsed from FlagGet() calls in vs_seeker.c (not hardcoded)
- Hidden rematch variant constants (TRAINER_X_2+ filtered from main trainer list)
- Reverse lookup — any tier constant maps to its rematch entry
- SKIP tier handling with explanatory text
- "Edit Tier Gates" button → Rematch Settings dialog
- Change tier count (2-20), pick gate flags from all flags in flags.h
- Writes directly to vs_seeker.c: MAX_REMATCH_PARTIES, switch statement, sRematches entries
- NoScrollComboBox/NoScrollSpinBox throughout (wheel events blocked when closed)

**How rematches work (no per-trainer variant system needed):**
The rematch system is tier-based, not per-trainer. Each tier has its own VS Seeker flag. When a new tier/flag is added, all trainers default to SKIP for that tier. If a trainer already has unused rematch constants (e.g. TRAINER_YOUNGSTER_BEN_3), those map to a tier slot. If a trainer doesn't have a variant for a tier, it stays SKIP'd. You enable specific trainers for specific tiers as needed — there's no reason to auto-generate per-trainer variant constants.

### Phase 4: Codebase Cleanup — COMPLETE

The root folder is organized, the plugin system is gone, and files are in proper subfolders.

- Plugin system fully removed — `core/` module replaces plugin_abstract + plugins/pokefirered, no discovery mechanism, only pokefirered supported
- Python source files organized into `ui/`, `core/`, `eventide/backend/`, `eventide/ui/` subfolders
- Documentation files moved to `docs/` folder
- Settings file (`settings.ini`) and Settings dialog (`ui/dialogs/settingsdialog.py`) in place
- Launcher simplified — no plugin selection
- Crashlog auto-cleanup runs on every startup (`app.py` lines 17-19) — reads `keep_days` and `max_size_mb` from settings.ini, purges old files by age, then enforces size cap by deleting oldest first. Settings dialog has dropdowns to configure both values.

### Phase 5: Editor Completeness — COMPLETE

All existing editors are finished.

**Trainer Class Editor:**
- New "Trainer Classes" sub-tab alongside existing Trainers tab (QTabWidget switcher, same pattern as Pokedex National/Regional)
- Searchable class list with sprite thumbnails (107 classes)
- Editable: display name (12-char limit with counter), money multiplier, default sprite (dropdown with thumbnails of all 139 trainer pics)
- Add new classes: button opens dialog, writes constant to trainers.h, name to trainer_class_names.h, money to battle_main.c
- Read-only battle info: battle BGM category, victory music type, battle terrain override — color-coded for special classes (Champion, Gym Leader, Elite Four)
- Encounter music: most common music among trainers of each class
- Facility class listing: all FACILITY_CLASS constants mapped to each trainer class
- Usage count: how many trainers use each class, with names
- Sprite changes write to trainer_class_lookups.h (updates gFacilityClassToPicIndex for all facility classes mapped to the edited class)
- File: `ui/trainer_class_editor.py`

**"Open in Folder" Buttons:**
- Trainer Class Editor: opens trainer sprite PNG in OS file manager
- Pokemon Graphics tab: opens species' graphics directory
- EVENTide sprite preview: opens overworld sprite PNG
- Items tab: opens item icon PNG
- Cross-platform utility: `ui/open_folder_util.py` (Windows Explorer, macOS Finder, Linux xdg-open)

**Editable Item Icons:**
- Items tab has a new "Icon" card with a sprite picker dropdown (all gItemIcon_* symbols with 24x24 thumbnails)
- Changing an icon updates the header preview live and writes to `item_icon_table.h` on save
- Auto-selects matching palette when icon changes
- Full parse of item_icon_table.h + graphics/items.h for icon/palette symbol mapping

**Credits Editor:**
- Standalone toolbar page (7th PorySuite page), integrated into unified_mainwindow.py
- Parses credits text entries from `src/strings.c` and credits script from `src/credits.c`
- Editable list of credits entries (role + names pairs)
- Save writes changes back to source files
- File: `ui/credits_editor.py`

**Trainer Battle Dialogue:**
- Intro text, defeat text, post-battle text — editable in Trainers tab
- Searches all map text.inc files for trainer's dialogue labels
- Read from / write to text.inc files with full round-trip
- Default dialogue templates configurable in Settings dialog (trainer_defaults section)

### Phase 6: Settings & Infrastructure — COMPLETE

**Settings System (5 tabs):**
- `settings.ini` persistence with full config sections
- Settings dialog at `ui/dialogs/settingsdialog.py`
- **General tab:** diagnostics, crashlog retention (keep_days + max_size_mb dropdowns), autosave toggle
- **Build & Play tab:** make command, make modern command, .gba path, emulator path, build environment
- **Trainer Defaults tab:** default intro/defeat/post-battle text templates, prize money multiplier
- **Editor tab:** startup page, log panel visibility, Porymap path
- **Notifications tab:** re-enable suppressed dialogs
- Missing/corrupted INI = sensible defaults, never crash

---

## Upcoming Phases

### Phase 5E: EVENTide Improvements — COMPLETE

All EVENTide-specific feature work lives here. These are enhancements to the script/event editing side of PorySuite-Z.

**Constant Label Manager — COMPLETE (page + data):**

Standalone toolbar page for assigning friendly labels to flags and vars. Game code is never changed — labels stored in `porysuite_labels.json` per project.

- Standalone toolbar page with icon, integrated into unified_mainwindow.py
- Filter dropdown (All/Flags/Vars), search bar, scrollable constant list
- Editable label and notes fields per constant
- Save/load to `porysuite_labels.json` in project root
- Ctrl+S saves labels alongside all other editors
- Close prompt catches unsaved label changes
- Refresh (F5) reloads label data
- `select_constant(name)` API for jump-to linking
- `labels_changed` signal for Event Editor integration
- File: `ui/label_manager.py`

**Event Editor Display Overhaul — COMPLETE:**

The Event Editor command list must show human-readable display names instead of raw constants everywhere. Every constant that has a display name uses it; flags/vars use Label Manager labels or auto-generated friendly names. Everything is color-coded by type so you can tell at a glance what's what.

How it works:
- A centralized display name resolver (`_resolve_display_name()`) converts any constant to its best available friendly name
- **Trainers:** show "Class Name" (e.g. "Sage Rauru") using the actual class and trainer name from the project data — whatever the user has set in the Trainers tab
- **Items:** show the item's display name from the project data (e.g. "Master Sword")
- **Species:** show the species display name from the project data (e.g. "PIKACHU" becomes whatever it's named in the Pokemon tab — could be "DickButt" if renamed)
- **Moves:** show the move display name
- **Flags:** show Label Manager label if set, otherwise auto-generate from constant name (strip prefix, title-case, replace underscores)
- **Vars:** same as flags
- **Script labels:** stay as-is (they're already readable)
- All display names are color-coded by type — colors configurable in Settings
- Right-click context menu on any command with a constant reference offers jump actions:
  - Flag/var → jump to Label Manager
  - Trainer → jump to Trainer editor
  - Item → jump to Item editor
  - Script label → jump to that script (already works for goto/call)
- The underlying save format is unchanged — raw constants are always written to script files

What `_stringize()` changes look like:
- Before: `@>Set Flag: FLAG_GOT_TM39_FROM_BROCK`
- After: `@>Set Flag: Got Tm39 From Brock` (with flag-colored text)
- Before: `@>Trainer Battle: TRAINER_BROCK`
- After: `@>Trainer Battle: Gym Leader Brock` (with trainer-colored text)
- Before: `@>Give Item: ITEM_POTION, 5`
- After: `@>Give Item: Potion ×5` (with item-colored text)
- Before: `@>Wild Battle: SPECIES_PIKACHU Lv.15`
- After: `@>Wild Battle: Pikachu Lv.15` (with species-colored text)

**External Script Resolution — COMPLETE:**

Item ball scripts and other shared scripts (from `data/scripts/*.inc`) are now loaded and displayed correctly in the Event Editor. Previously, scripts that lived outside the map's own `scripts.inc` showed as empty/broken events.

- `_resolve_external_scripts()` searches `data/scripts/*.inc` AND `data/event_scripts.s` for missing script labels
- **Recursive resolution**: after finding external scripts, scans their commands for goto/call/map_script targets and resolves those too (follows the full dependency chain — e.g. map event → `Common_EventScript_UnionRoomAttendant` in `event_scripts.s` → `call CableClub_EventScript_UnionRoomAttendant` in `cable_club.inc`)
- Also extracts `map_script` targets so map initialization scripts from external files are loaded
- On-demand resolution in "Go To →" button: if a target isn't loaded, tries to find it before giving up
- External labels tracked separately (`_external_script_labels`) so save doesn't duplicate them into the map's `scripts.inc`
- Save-back support: editing an external script writes changes to its original file
- All 40 shared script files in `data/scripts/` are searchable (cable_club.inc, trainers.inc, field_moves.inc, pkmn_center_nurse.inc, pc.inc, etc.)
- Item ball events shown as `[Item] Potion` instead of `[NPC] 3` in the event combo
- `finditem` command handled in stringizer, color coding, and context menu
- New "Item Ball" template in NPC Script menu for creating new item ball scripts
- New item ball scripts saved to `data/scripts/item_ball_scripts.inc`

**Movement route display** (RPG Maker XP style):
- Movement steps shown inline under the `@>Set Move Route: Player` header
- Each step on its own line with `$>` prefix: `$>Move Left`, `$>Turn Down`, `$>Wait: 4 frame(s)`
- Target shows "Player" or the NPC's object name instead of raw constant

**UI Layout Restructure — COMPLETE:**

The Event Editor layout has been restructured to match RPG Maker XP's event editor. Key changes:

- Page control buttons (New Page, Rename, Delete Page) moved to top toolbar row alongside New Script and Find Unused Flag
- Script label tabs (page tabs) positioned directly below the toolbar, above the main content area
- Save button moved to top toolbar row for easy access
- Main splitter flipped: command list is now the PRIMARY left panel (3:1 ratio), event properties are the secondary right panel
- Event selector, properties (ID, position, script, graphic), and sprite preview are in a compact right-side panel
- "New Script" button expanded with dropdown menu organized into categories: NPC Scripts, Signs & BG Events, Map Scripts, Standard Wrappers, Field Objects

**Script Template System — COMPLETE:**

The "New NPC Script" button has been expanded into a full "New Script" template system with organized categories:

- **NPC Scripts:** Simple Talker, Trainer, Item Giver (flag-gated), Flag-gated NPC
- **Signs & BG Events:** Simple Sign (MSGBOX_SIGN), Hidden Item (finditem + flag)
- **Map Scripts:** Door Warp (with door animation), Cave Warp (direct warp)
- **Standard Wrappers:** Nurse (callstd STD_POKEMON_CENTER_NURSE), PC (callstd STD_PC), Mart (pokemart with item list)
- **Field Objects:** Cut Tree, Rock Smash, Strength Boulder
- **Context-aware:** Item Ball template shows first when the selected object uses `OBJ_EVENT_GFX_ITEM_BALL`

**Set Move Route Rework — COMPLETE:**

Renamed "Apply Movement" to "Set Move Route" everywhere. Removed redundant "Move NPC" (setobjectxy) from the command selector. The command now includes:
- **Auto-scaffolding:** When you add a new Set Move Route command, it auto-creates a unique movement label (e.g. `PalletTown_Movement_1`) with a default `face_player` + `step_end`, plus sets the target to `OBJ_EVENT_ID_PLAYER`. Ready to edit immediately.
- **"Edit Steps..." button** in the edit dialog opens an RMXP-style Move Route Editor popup:
  - **Left panel:** Ordered list of movement steps with Delete, Up, Down, and Clear All buttons
  - **Right panel:** Categorized button tabs (Move, Jump, Turn/Face, Slide/Glide, Special, Run/Spin) with all 177 movement macros from `asm/macros/movement.inc`
- Steps display with `$>` prefix like RMXP's move route list
- Saves modified movement data back to scripts.inc (movement labels preserved with `step_end` terminator)
- `_modified_movements` dict tracks which movement labels were edited, included in save pipeline

**RMXP-style Conditions Box — COMPLETE:**

Replaced the condition text banner with a fully editable Conditions GroupBox matching RPG Maker XP:
- **Position:** Above Event Properties on the left panel (matches RMXP layout order)
- **Flag row:** Checkbox + searchable flag picker (all project flags) + "is ON"/"is OFF" dropdown
- **Variable row:** Checkbox + searchable var picker + operator dropdown (==, !=, <, >=, <=, >) + value spinbox
- **Editable:** Check a box, pick a flag/variable, set the condition — creates or modifies the condition page's `goto_if_set` / `goto_if_eq` etc. Uncheck to remove the condition.
- Flag and Variable are mutually exclusive (checking one unchecks the other, matching RMXP)
- Populated from the page's `_condition_cmd` when switching pages

**Numbered Page Tabs — COMPLETE:**

Page tabs now display as numbered 1, 2, 3 (matching RMXP) instead of showing script label names. The tab numbers correspond to the page index.

**Script Lookup by Name — COMPLETE:**

Project-wide script label search. Indexes every label (lines ending with `::`) across all 425+ map `scripts.inc` files, 39 shared script files in `data/scripts/`, and `data/event_scripts.s` — roughly 5,300 labels total. Index builds on project load in under a second.

- **Toolbar button:** "Find Script" next to "Find Unused Flag" on the Event Editor toolbar
- **Keyboard shortcut:** Ctrl+Shift+F opens the search dialog from anywhere in the Event Editor
- **Search dialog:** Type to filter in real time (100ms debounce), results show Label, Map/Source, and Type columns
- **Navigation:** Double-click or Enter loads the map and selects the event that owns the label. Finds labels by direct script match, page label match, or inline sub-label match.
- **Shared scripts:** Labels in `data/scripts/*.inc` are shown with the filename. If clicked, the tool searches `map.json` files to find a map that references it and navigates there.
- **Result sorting:** Prefix matches first, then contains matches, alphabetical within each group
- Files: `eventide/backend/script_index.py` (index), `eventide/ui/script_search_dialog.py` (dialog)

**Move Camera Command — COMPLETE:**

Full cutscene camera control tool. "Move Camera (Cutscene)" in the command selector (Page 2, Camera category) opens an RMXP-style dialog with 6 tabs:
- **Pan** (20 directional walk macros), **Slide** (8 smooth slide macros)
- **Screen** (fade, flash, weather), **Effects** (field effects, sprites)
- **Timing** (delay, wait button, wait state), **Sound** (SE, fanfare, BGM, mon cries)

Mixes movement macros and script commands in one sequence. Output auto-splits movement macros into `applymovement LOCALID_CAMERA` blocks with generated labels (`MapName_CameraMovement_N`). Wrapped in `SpawnCameraObject` / `RemoveCameraObject`. All commands inserted at once into the command list.

**Comprehensive Tooltips — COMPLETE:**

Descriptive hover tooltips on all Event Editor controls, command edit dialogs, camera dialog buttons, and command selector palette (~80 commands). All tooltips respect a Settings toggle (Tools → Settings → Event Editor Tooltips checkbox) that applies immediately — no restart needed. Position override tooltips (functional indicators) are unaffected. Uses `_tt()` at construction time and `_apply_tooltip_visibility()` for live toggling.

**Hidden Item Editor — COMPLETE:**

Hidden items (`hidden_item` bg_events in map.json) now have a dedicated property editor panel. When a hidden item is selected, the command list swaps out for a clean form with Item picker, Flag picker, Quantity, X/Y Position, Elevation, and Underfoot (Itemfinder-only) checkbox. New hidden items can be created from the New Script menu (always available, auto-finds unused flag). Delete button removes them from the map. Combo dropdown shows `[Hidden Item] Tiny Mushroom` instead of `[Hidden_Item] bg0`. All changes save to map.json — no script needed.

**Event Editor Color Scheme — COMPLETE (Settings + Wiring):**

RMXP-style: specific functional categories get their own color, plain structural stuff (text, choices, branches) stays default white. Each colored category is customisable in Settings → Event Colors.

What gets colored:
1. **Flow navigation** (goto, call) → flow color (#c0392b red) — RMXP's "Jump to Label"
2. **Conditionals** (goto_if_*, call_if_*) → amber (#e8a838)
3. **Set Move Route block** (applymovement + inline steps) → maroon (#8b2252) — matches RMXP exactly. `waitmovement` stays plain.
4. **Flag/switch control** (setflag, clearflag, setvar, addvar, etc.) → flag_var color (#8e44ad purple) — RMXP's "Control Switches"
5. **Item commands** (additem, finditem, giveitem, pokemart, money, coins) → item color (#2ecc71 lime) — RMXP's "Change Items"
6. **Sound** (playbgm, playse, fanfares, fadedefaultbgm, savebgm) → sound color (#d35400 orange)
7. **Screen effects** (fadescreen, weather, delay, waitstate) → screen color (#16a085 teal)
8. **Battles** (trainerbattle variants, wildbattle) → battle color (#e74c3c bright red)
9. **Pokemon** (givemon, giveegg, party checks, healplayerteam) → pokemon color (#f39c12 gold)
10. **Label markers** (inline sub-labels) → orange (#f39c12)
11. **Constant-type fallback** — any command referencing a known constant (FLAG_*, VAR_*, TRAINER_*, ITEM_*, SPECIES_*, MOVE_*) gets the constant's type color

What stays plain (matching RMXP): dialogue/text, choices, end/return, lock/release, system/buffer commands, and anything without a specific category above.

Settings integration:
- "Event Colors" page in Settings dialog with color picker buttons for all constant types and command categories
- "Reset All to Defaults" button restores factory colors
- Colors saved to settings.ini under `event_colors/` and `event_cat_colors/` keys
- Loaded on startup via `_load_color_settings()` and live-reloaded via `reload_settings()` when the Settings dialog closes — changes apply immediately
- All FLAG_* and VAR_* constants get colored even when excluded from ConstantsManager (prefix fallback in `_const_type`)

**Object Position Cross-References — COMPLETE:**

When viewing an NPC object event, the editor scans all loaded scripts for `setobjectxyperm`, `setobjectxy`, and `setobjectmovementtype` commands that reference the same local ID. If found, a clickable note appears below Event Properties:
- "Position also set by: SetSignLadyPos, MoveSignLadyToRouteEntrance"
- Clicking any label navigates to that script in the command list
- Bridges the gap between the NPC's base position (map.json) and runtime repositioning (OnTransition scripts)

**Set Flag / Set Var → Condition Page Linking — COMPLETE:**

- `setflag` commands that match a condition page's `goto_if_set` now show `→ activates Page N` inline in the command list
- `setvar` commands that match a `goto_if_eq` condition page show the same annotation
- The "Go To →" button on a `setflag`/`clearflag`/`setvar` command jumps directly to the condition page that checks it
- Makes the implicit flag/var → condition page connection visible and navigable

**Per-Page Position Overrides — COMPLETE:**

When an OnTransition script sets different X/Y positions for an NPC based on flag/var conditions (e.g., `call_if_eq VAR_MAP_SCENE_..., 0, SetPos` → `setobjectxyperm`), the Event Editor automatically detects this:
- Switching to a condition page whose flag/var matches an OnTransition override updates the X/Y spinboxes to show the script-defined position instead of the base map.json position
- The spinbox background turns amber (#3a3a20) to visually indicate an override is active
- Tooltip shows which script set the position (e.g., "Position set by script: PalletTown_EventScript_SetSignLadyPos")
- Editing X/Y on an overridden page saves back to the `setobjectxyperm` command in the script, not to map.json
- Works globally for any map — scans all loaded scripts, not just specific patterns

### Phase 7: Porymap Integration — IN PROGRESS

PorySuite-Z fully integrates Porymap (the visual map/tile editor) as a companion tool. Users get one seamless workflow: paint tiles and place events in Porymap, edit scripts and data in PorySuite-Z. Map sync and event selection bridge the two apps so they behave like one tool.

**Approach:** PorySuite-Z downloads the Porymap source repo, runs a patch script that adds event-aware callbacks and a bridge API to the existing JS scripting engine, then builds the patched binary. The install script handles downloading Qt build tools and compiling — the user just clicks "Install Porymap" and waits. No fork, no manual steps.

**Status (2026-04-03):** Core integration working end-to-end. Install pipeline builds patched Porymap with compile progress streaming. "Open in Porymap" opens the correct map via CLI args (with QDir::cleanPath fix for Windows backslash normalization). Bidirectional sync works — if Porymap is already running, PorySuite writes a command file and the bridge script polls + calls `map.openMap()`. Duplicate window prevention via Win32 API. Remaining: auto-sync on map selection, Ctrl+E handler, additional callback hooks (event create/delete/move), stock Porymap fallback testing.

---

#### Data Overlap Analysis — What Both Apps Touch

Both apps read/write the same pokefirered project. Understanding exactly where they overlap determines every bridge point.

**Files BOTH apps read/write (conflict zone — needs file watchers + reload prompts):**

| File | Porymap Does | PorySuite-Z Does |
|------|-------------|-----------------|
| `data/maps/<name>/map.json` | Edits events (placement, graphics, position), map header (song, weather, type), warp destinations, connections | Edits events (scripts, conditions, hidden items), reads header for display |
| `data/maps/<name>/scripts.inc` | Reads script labels for event display | Full script editing — commands, dialogue, conditions, movement routes |
| `data/maps/<name>/text.inc` | Doesn't edit directly | Reads/writes dialogue text for trainers, signs, NPCs |
| `data/maps/map_groups.json` | Full map group management (create, rename, reorder) | Map rename, group management, warp validation |
| `data/layouts/layouts.json` | Full layout management (create, rename, tileset assignment, dimensions) | Layout rename, tileset assignment display |
| `src/data/region_map/region_map_sections.json` | Region map visual editor | Region map visual editor |
| `include/constants/flags.h` | Reads for event conditions | Reads for flag dropdowns + Label Manager |
| `include/constants/vars.h` | Reads for event conditions | Reads for var dropdowns + Label Manager |
| `include/constants/items.h` | Reads for hidden item events | Reads/writes for Items editor |
| `include/constants/species.h` | Reads for wild encounters | Reads/writes for Pokemon editor |
| `include/constants/songs.h` | Reads for map header song picker | Reads for event sound commands |
| `include/constants/weather.h` | Reads for map header/weather triggers | Reads for event weather commands |
| `include/constants/event_objects.h` | Reads for event graphic picker | Reads for sprite display in Event Editor |
| `include/constants/event_object_movement.h` | Reads for movement type picker | Reads for movement route editor |

**Files ONLY Porymap touches (PorySuite-Z should read but not write):**

| File | What Porymap Does |
|------|------------------|
| `data/layouts/<id>/blockdata.bin` | Tile painting — the actual map grid |
| `data/layouts/<id>/border.bin` | Border tile data |
| `data/tilesets/primary/<name>/` | Primary tileset tiles, metatiles, palettes |
| `data/tilesets/secondary/<name>/` | Secondary tileset tiles, metatiles, palettes |
| `src/data/tilesets/headers.h` | Tileset C declarations |
| `src/data/tilesets/graphics.h` | Tile graphics pointers |
| `src/data/tilesets/metatiles.h` | Metatile composition data |
| `src/data/wild_encounters.json` | Wild encounter tables per map |
| `src/data/heal_locations.json` | Pokemon Center respawn points |
| `include/constants/metatile_labels.h` | Metatile label constants |
| `include/constants/metatile_behaviors.h` | Metatile behavior bits |
| `include/constants/heal_locations.h` | Heal location constants |

**Files ONLY PorySuite-Z touches (Porymap never reads these):**

| File | What PorySuite-Z Does |
|------|----------------------|
| `src/data/pokemon/species_info.h` | Species stats, types, abilities, graphics |
| `src/data/pokemon/pokedex_entries.h` | Pokedex category + description |
| `src/data/pokemon/level_up_learnsets.h` | Level-up moves |
| `src/data/pokemon/tmhm_learnsets.h` | TM/HM compatibility |
| `src/data/pokemon/tutor_learnsets.h` | Move tutor compatibility |
| `src/data/pokemon/egg_moves.h` | Egg move lists |
| `src/data/items.json` + `.h` | Item data (price, description, hold effect) |
| `src/data/moves.json` | Move data (power, accuracy, PP, effects) |
| `src/data/trainers.json` | Trainer parties, classes, dialogue |
| `src/data/trainer_parties.h` | Trainer party structs |
| `src/data/text/trainer_class_names.h` | Trainer class display names |
| `src/data/starters.json` | Starter Pokemon configuration |
| `src/data/evolutions.json` | Evolution chains |
| `src/credits.c` + `src/strings.c` | Credits sequence |
| `config.mk` + `include/config.h` | Build configuration |
| `src/data/vs_seeker.c` | VS Seeker rematch tiers |

---

#### Bridge Points — Every Click That Should Cross Apps

**From Porymap → PorySuite-Z (via patched callbacks + JS bridge):**

| User Action in Porymap | What Happens in PorySuite-Z |
|------------------------|---------------------------|
| Click an Object Event (NPC) | Event Editor selects that NPC, shows its script, conditions, dialogue |
| Click a Warp event | Event Editor shows the warp, "Go To" highlights the destination map |
| Click a Trigger event | Event Editor selects the trigger, shows its script |
| Click a Sign/BG event | Event Editor selects the sign, shows its text |
| Click a Hidden Item | Event Editor shows the Hidden Item Editor panel (item, flag, quantity) |
| Open a different map | Event Editor navigates to that map automatically |
| Switch to Events tab | PorySuite-Z switches to Event Editor page |
| Create a new event | Event Editor reloads map data, new event appears in dropdown |
| Delete an event | Event Editor reloads, removed from dropdown |
| Move/drag an event | Event Editor updates the position display for that event |
| Edit map header (song, weather) | PorySuite-Z refreshes any displayed header info |
| Edit wild encounters | PorySuite-Z knows encounters changed (for future encounter editor) |
| Edit a connection | PorySuite-Z's Maps tab refreshes connection data |
| Save in Porymap | PorySuite-Z detects changed files, prompts to reload affected editors |

**From PorySuite-Z → Porymap (via config writing + launch):**

| User Action in PorySuite-Z | What Happens in Porymap |
|---------------------------|------------------------|
| "Open in Porymap" on Event Editor | Porymap launches/focuses on that map |
| Right-click map in Maps tab → "Open in Porymap" | Porymap opens that map |
| Right-click layout in Layouts tab → "Open in Porymap" | Porymap opens that layout |
| Save scripts/events in PorySuite-Z | Porymap detects file changes, prompts to reload |
| Rename a map in Maps tab | Porymap detects map_groups.json change, reloads |
| Edit region map sections | Porymap's region map editor reloads |
| Trainer → "Show on Map" (future) | Porymap opens the trainer's map, highlights position |
| Warp validator finds broken warp | "Open in Porymap" jumps to the warp's map position |

**Shared file watchers (both directions):**

| File Pattern | Watcher On | Action |
|-------------|-----------|--------|
| `data/maps/*/map.json` | Both apps | Prompt "External change detected, reload?" |
| `data/maps/*/scripts.inc` | Both apps | Same prompt |
| `data/maps/*/text.inc` | PorySuite-Z | Reload dialogue text |
| `data/maps/map_groups.json` | Both apps | Reload map list |
| `data/layouts/layouts.json` | Both apps | Reload layout list |
| `src/data/region_map/*.json` | Both apps | Reload region map |
| `src/data/wild_encounters.json` | PorySuite-Z | Reload if future encounter editor exists |
| `include/constants/*.h` | PorySuite-Z | Reload constant dropdowns (flags, vars, items, species) |

---

#### 7A: Install & Launch System — COMPLETE

**Tools → Install Porymap:**
- One-click install from PorySuite-Z's Tools menu
- Progress dialog walks through each step:
  1. Clone Porymap source from GitHub into `porysuite/porymap_src/` (or pull if already cloned)
  2. Apply patch files from `porysuite/porymap_patches/` to the source (adds callbacks, bridge API, context menus)
  3. Download Qt build tools if not present (Qt 5.14+ SDK via aqtinstall — Python package, ~500MB one-time download to `porysuite/qt_sdk/`)
  4. Compile the patched source with qmake + make (progress bar during build)
  5. Copy resulting binary + Qt DLLs to `porysuite/porymap/` (the clean runtime folder)
  6. Register `porysuite_bridge.mjs` in the active project's `porymap.user.cfg` under `custom_scripts`
- If patches fail to apply (Porymap updated upstream), show error with instructions to report
- "Update Porymap" re-pulls source, re-applies patches, re-builds
- Build cache: skip compilation if source hasn't changed since last build

**Tools → Open in Porymap (Ctrl+F7):**
- Launches Porymap from `porysuite/porymap/porymap.exe`
- Before launch, writes the current project path into Porymap's `porymap.cfg` as most recent project so Porymap opens directly to it
- Also writes the current map name into `porymap.user.cfg` so Porymap opens to the right map
- If Porymap is already running, brings it to front (detect via process name)
- Grayed out if Porymap not installed (shows "Install Porymap first" tooltip)

**Auto-Setup on Project Open:**
- Every time PorySuite-Z opens a project, it checks that `porymap.user.cfg` has the bridge script registered
- If missing (new project, or user deleted config), auto-injects the bridge script path
- This is the "parasite" — every project automatically gets PorySuite integration in Porymap

**File layout:**
```
porysuite/
  porymap/                        ← Compiled patched binary + Qt runtime (what the user launches)
    porymap.exe
    *.dll (Qt runtime)
    resources/
  porymap_src/                    ← Porymap source repo (cloned from GitHub, patches applied)
    src/
    include/
    porymap.pro
    ...
  porymap_bridge/                 ← Our integration layer (Python + JS)
    porysuite_bridge.mjs          ← JS companion script (runs inside Porymap)
    bridge_watcher.py             ← PorySuite-Z module that reads bridge messages
    porymap_launcher.py           ← Launch, config writing, install/update logic
    porymap_installer.py          ← Clone, patch, build, deploy pipeline
  porymap_patches/                ← Python patcher applied to Porymap source before building
    apply_patches.py              ← Search-and-replace patcher (resilient to upstream line changes)
                                     Patches: scripting.h, scripting.cpp, scriptutility.h,
                                     apiutility.cpp, mainwindow.cpp — adds 11 callbacks,
                                     writeBridgeFile, getMapHeader, getCurrentTilesets,
                                     getMapConnections, getMapEvents
  qt_sdk/                         ← Qt build tools (downloaded by installer, ~500MB)
    bin/
    lib/
    ...
```

---

#### 7B: Porymap Source Patches (What We Change) — COMPLETE (patcher written, needs build test)

Small, surgical additions to Porymap's existing scripting engine. All changes follow the exact same patterns as Porymap's 15 existing callbacks. Applied via `porymap_patches/apply_patches.py` (Python search-and-replace, not fragile git diffs).

**New Event Callbacks (added to `scripting.h` / `scripting.cpp`):**
- `OnEventSelected(eventType, eventIndex, scriptLabel, x, y)` — fires when user clicks/selects any event on the map
- `OnEventCreated(eventType, eventIndex)` — fires when user places a new event
- `OnEventDeleted(eventType, eventIndex)` — fires when user removes an event
- `OnEventMoved(eventType, eventIndex, oldX, oldY, newX, newY)` — fires when user drags an event to a new position

**New Map/Layout Callbacks:**
- `OnMapSaved(mapName)` — fires after Porymap writes map.json (so PorySuite-Z knows to reload without polling)
- `OnLayoutSaved(layoutId)` — fires after layout blockdata/border is saved
- `OnConnectionChanged(mapName, direction, targetMap)` — fires when user adds/edits/removes a connection
- `OnWildEncountersSaved(mapName)` — fires after wild_encounters.json is written
- `OnHealLocationChanged(mapName, x, y)` — fires when heal location is added/moved
- `OnMapHeaderChanged(mapName, property, value)` — fires when song, weather, type, etc. changes in the header editor
- `OnTilesetChanged(primaryTileset, secondaryTileset)` — fires when tileset assignment changes for the current layout

**New JS Query Functions (added to `scriptutility.h` / `apiutility.cpp`):**

Event queries:
- `utility.getSelectedEventType()` — returns "Object", "Warp", "Trigger", "Sign", "HiddenItem", etc.
- `utility.getSelectedEventIndex()` — returns the index within its event group
- `utility.getSelectedEventScript()` — returns the script label of the selected event
- `utility.getSelectedEventPosition()` — returns `{x, y}` of the selected event
- `utility.getEventCount(type)` — how many events of each type on the current map
- `utility.getEventData(type, index)` — returns full event JSON (graphic, position, script, elevation, movement type)

Map/layout queries:
- `utility.getCurrentMapName()` — returns the currently open map name
- `utility.getCurrentLayoutId()` — returns the current layout ID
- `utility.getMapHeader()` — returns `{song, weather, type, battleScene, location, showLocationName, allowRunning, allowBiking, allowEscaping, floorNumber, requiresFlash}`
- `utility.getMapConnections()` — returns array of `{direction, targetMap, offset}`
- `utility.getMapEvents(type)` — returns array of all events of given type with full data
- `utility.getWarpDestination(warpIndex)` — returns `{destMap, destWarp}` for a warp event

Wild encounter queries:
- `utility.getWildEncounterGroups()` — returns list of encounter group names for current map
- `utility.getWildEncounterData(groupName)` — returns species/level data for a group

Tileset queries:
- `utility.getCurrentTilesets()` — returns `{primary, secondary}` tileset labels

Bridge communication:
- `utility.writeBridgeFile(content)` — writes a string to `porysuite_bridge.json` in the project root (dedicated bridge communication, no log abuse)

**New UI Elements (added to Porymap's interface):**
- Right-click context menu on events: "Edit Script in PorySuite-Z" (calls the bridge)
- Right-click context menu on events: "Edit Trainer in PorySuite-Z" (for trainer battle events, sends trainer constant)
- Right-click context menu on hidden items: "Edit Item in PorySuite-Z" (sends item constant)
- Optional: PorySuite-Z icon button on Porymap's toolbar

**Where the callbacks get wired in Porymap's source:**
- `OnEventSelected` → `Editor::selectMapEvent()` and `MainWindow::updateSelectedEvents()`
- `OnEventCreated` → the event creation handlers in `Editor`
- `OnEventDeleted` → `Editor::deleteSelectedEvents()`
- `OnEventMoved` → the event drag/drop handlers in `Editor`
- `OnMapSaved` → `MainWindow::save()` after successful write
- `OnLayoutSaved` → layout save path in `Project::saveLayout()`
- `OnConnectionChanged` → connection add/edit/delete handlers in `MainWindow`
- `OnWildEncountersSaved` → wild encounter save path
- `OnHealLocationChanged` → heal location event handlers
- `OnMapHeaderChanged` → each setter in `MapHeaderForm` (song, weather, type, etc.)
- `OnTilesetChanged` → `MainWindow::setPrimaryTileset()` and `setSecondaryTileset()`

Estimated total: ~200-250 lines of C++ additions, all following the existing callback pattern.

---

#### 7C: JS Bridge Script (`porysuite_bridge.mjs`) — COMPLETE

The companion script that runs inside Porymap and communicates with PorySuite-Z:

```javascript
// Runs inside Porymap's JS engine. Bridges ALL Porymap activity to PorySuite-Z.
let currentMap = "";
let currentProject = "";
let lastHoverX = 0;
let lastHoverY = 0;

// === Project lifecycle ===
export function onProjectOpened(projectPath) {
    currentProject = projectPath;
    // Register PorySuite-Z actions in Porymap's Tools menu
    utility.registerAction("editInPorySuite", "Edit in PorySuite-Z", "Ctrl+E");
    utility.registerAction("syncToPorySuite", "Sync Map to PorySuite-Z", "Ctrl+Shift+E");
    writeBridge({type: "project_opened", project: projectPath});
}

export function onProjectClosed(projectPath) {
    writeBridge({type: "project_closed"});
}

// === Map navigation ===
export function onMapOpened(mapName) {
    currentMap = mapName;
    // Send full map context on open — header, tilesets, connections
    let header = utility.getMapHeader();
    let tilesets = utility.getCurrentTilesets();
    let connections = utility.getMapConnections();
    writeBridge({type: "map_opened", map: mapName, header: header,
                 tilesets: tilesets, connections: connections});
}

export function onMainTabChanged(oldTab, newTab) {
    // Tabs: 0=Map, 1=Events, 2=Header, 3=Connections, 4=WildPokemon
    writeBridge({type: "tab_changed", tab: newTab});
}

export function onBlockHoverChanged(x, y) {
    lastHoverX = x;
    lastHoverY = y;
}

// === Event callbacks (from our patches) ===
export function onEventSelected(eventType, eventIndex, scriptLabel, x, y) {
    writeBridge({
        type: "event_selected",
        map: currentMap,
        eventType: eventType,
        eventIndex: eventIndex,
        script: scriptLabel,
        x: x,
        y: y
    });
}

export function onEventCreated(eventType, eventIndex) {
    writeBridge({type: "event_created", map: currentMap, eventType: eventType, eventIndex: eventIndex});
}

export function onEventDeleted(eventType, eventIndex) {
    writeBridge({type: "event_deleted", map: currentMap, eventType: eventType, eventIndex: eventIndex});
}

export function onEventMoved(eventType, eventIndex, oldX, oldY, newX, newY) {
    writeBridge({type: "event_moved", map: currentMap, eventType: eventType, eventIndex: eventIndex,
                 oldX: oldX, oldY: oldY, newX: newX, newY: newY});
}

// === Map data callbacks (from our patches) ===
export function onMapSaved(mapName) {
    writeBridge({type: "map_saved", map: mapName});
}

export function onLayoutSaved(layoutId) {
    writeBridge({type: "layout_saved", layout: layoutId});
}

export function onConnectionChanged(mapName, direction, targetMap) {
    writeBridge({type: "connection_changed", map: mapName, direction: direction, target: targetMap});
}

export function onWildEncountersSaved(mapName) {
    writeBridge({type: "wild_encounters_saved", map: mapName});
}

export function onHealLocationChanged(mapName, x, y) {
    writeBridge({type: "heal_location_changed", map: mapName, x: x, y: y});
}

export function onMapHeaderChanged(mapName, property, value) {
    writeBridge({type: "header_changed", map: mapName, property: property, value: value});
}

export function onTilesetChanged(primaryTileset, secondaryTileset) {
    writeBridge({type: "tileset_changed", map: currentMap, primary: primaryTileset, secondary: secondaryTileset});
}

// === Existing Porymap callbacks we also forward ===
export function onTilesetUpdated(tilesetName) {
    writeBridge({type: "tileset_updated", tileset: tilesetName});
}

export function onMapResized(oldWidth, oldHeight, delta) {
    writeBridge({type: "map_resized", map: currentMap, oldWidth: oldWidth, oldHeight: oldHeight, delta: delta});
}

// === User-triggered actions (registered in Tools menu) ===
export function editInPorySuite() {
    // Ctrl+E — send current hover position so PorySuite-Z can look up the event
    writeBridge({type: "edit_request", map: currentMap, x: lastHoverX, y: lastHoverY});
}

export function syncToPorySuite() {
    // Ctrl+Shift+E — full map sync without specific event
    let header = utility.getMapHeader();
    let tilesets = utility.getCurrentTilesets();
    let connections = utility.getMapConnections();
    writeBridge({type: "sync_request", map: currentMap, header: header,
                 tilesets: tilesets, connections: connections});
}

// === Bridge writer ===
function writeBridge(data) {
    data.timestamp = Date.now();
    utility.writeBridgeFile(JSON.stringify(data));
}
```

---

#### 7D: PorySuite-Z Bridge Watcher (`bridge_watcher.py`) — COMPLETE

Python module that runs in PorySuite-Z, watching the bridge file for messages from Porymap.

**How it works:**
- Uses `QFileSystemWatcher` on `{project_root}/porysuite_bridge.json`
- Reads the file on every change, parses the JSON message
- Ignores messages older than 2 seconds (stale bridge file from previous session)
- Emits Qt signals that PorySuite-Z editors connect to

**Signals emitted and what they trigger:**

Map & Navigation:
- `map_opened(str mapName, dict header, dict tilesets, list connections)` → Event Editor navigates to map, displays header info, shows connection context
- `tab_changed(int tabIndex)` → PorySuite-Z switches to matching page (0=Map→Event Editor, 1=Events→Event Editor, 2=Header→Maps tab, 3=Connections→Maps tab, 4=WildPokemon→future encounters)
- `sync_requested(str mapName, dict header, dict tilesets, list connections)` → Full refresh of all PorySuite-Z editors for that map

Event Editing:
- `event_selected(str mapName, str eventType, int eventIndex, str scriptLabel, int x, int y)` → Event Editor selects that exact event in the dropdown, shows its script/conditions
- `event_created(str mapName, str eventType, int eventIndex)` → Event Editor reloads map.json, selects the new event, offers to create a script template
- `event_deleted(str mapName, str eventType, int eventIndex)` → Event Editor reloads, adjusts selection
- `event_moved(str mapName, str eventType, int eventIndex, int oldX, int oldY, int newX, int newY)` → Event Editor updates position spinboxes, refreshes position override display
- `edit_requested(str mapName, int x, int y)` → Event Editor looks up which event is at (x,y) in map.json and selects it

Map Data Changes:
- `map_saved(str mapName)` → PorySuite-Z reloads map.json for that map — events, header, warps, everything
- `layout_saved(str layoutId)` → Layouts tab refreshes if visible
- `connection_changed(str mapName, str direction, str targetMap)` → Maps tab refreshes connection display, warp validator re-checks
- `wild_encounters_saved(str mapName)` → Future encounters editor reloads
- `heal_location_changed(str mapName, int x, int y)` → Informational update
- `header_changed(str mapName, str property, str value)` → Updates any displayed header info (song name in status bar, weather in properties)
- `tileset_changed(str mapName, str primary, str secondary)` → Layouts tab refreshes tileset display
- `tileset_updated(str tilesetName)` → Layouts tab refreshes if showing that tileset
- `map_resized(str mapName, int oldWidth, int oldHeight, dict delta)` → Event Editor refreshes position bounds

**Connection to PorySuite-Z editors:**
- `unified_mainwindow.py` creates the BridgeWatcher on project load, connects signals to all editors
- Event Editor gets: event_selected, event_created, event_deleted, event_moved, edit_requested, map_opened, map_saved
- Maps tab gets: map_opened, connection_changed, map_saved
- Layouts tab gets: layout_saved, tileset_changed, tileset_updated
- Region Map tab gets: (future — if Porymap region map changes are tracked)
- Trainers tab gets: event_selected (when selected event is a trainerbattle, auto-navigate to that trainer)

---

#### 7E: PorySuite-Z → Porymap Direction — COMPLETE

PorySuite-Z can't write to the bridge file (Porymap reads the bridge, not the other way). Instead, PorySuite-Z communicates by: (1) writing Porymap's config files before launching it, and (2) relying on Porymap's built-in file-change detection for shared project files.

**"Open in Porymap" buttons — everywhere a map/layout appears:**

| Location in PorySuite-Z | Button/Action | What It Does |
|-------------------------|---------------|-------------|
| Event Editor toolbar | "Open in Porymap" button | Opens current map in Porymap |
| Event Editor event dropdown | Right-click → "Show in Porymap" | Opens the map Porymap is already on (focuses window) |
| Maps tab map list | Right-click → "Open in Porymap" | Opens selected map in Porymap |
| Maps tab warp validator | "Open in Porymap" next to broken warp | Opens the warp's source map |
| Layouts tab layout list | Right-click → "Open in Porymap" | Opens a map that uses this layout |
| Trainers tab | "Show on Map" button (future) | Opens the map where this trainer appears |
| Region Map tab | Right-click section → "Open in Porymap" | Opens the map for that region section |

All of these:
1. Write the target map name to `porymap.user.cfg` (recent_map key)
2. Write the project path to `porymap.cfg` (recent project)
3. Launch `porymap.exe` (or bring to front if running)
4. Porymap opens to that map

**File watchers for shared data (PorySuite-Z detects Porymap saves):**

| File Pattern | What Changed | PorySuite-Z Response |
|-------------|-------------|---------------------|
| `data/maps/*/map.json` | Events, warps, header, connections | Prompt "Porymap updated [MapName]. Reload events?" → Yes reloads Event Editor for that map |
| `data/maps/map_groups.json` | Map list, groups | Prompt → reload Maps tab map tree |
| `data/layouts/layouts.json` | Layout list, tileset assignments | Prompt → reload Layouts tab |
| `src/data/wild_encounters.json` | Encounter tables | Prompt → reload if future encounter editor exists |
| `src/data/heal_locations.json` | Heal spawn points | Informational log message |
| `src/data/region_map/*.json` | Region map sections | Prompt → reload Region Map tab |
| `include/constants/*.h` | Any constant changes | Reload constant caches (flags, vars, items, species dropdowns) |

**Smart reload behavior:**
- If PorySuite-Z has unsaved changes to the SAME file Porymap just changed, show a merge prompt: "Both PorySuite-Z and Porymap have changes to PalletTown/map.json. Keep PorySuite-Z changes / Load Porymap changes / Cancel"
- If PorySuite-Z has no unsaved changes to that file, auto-reload silently (configurable in Settings)
- Suppress reload prompts while PorySuite-Z itself is saving (to avoid reacting to its own writes)
- Debounce file watcher signals (100ms) to batch rapid multi-file saves

**Bridge watcher also handles the reverse notification:**
- When the bridge says `map_saved`, that's Porymap telling us it just wrote — we reload immediately without prompting (we know Porymap is the source)
- File watcher prompts are the fallback for when the bridge isn't running (Porymap launched independently)

---

#### 7F: Real User Workflows

**Workflow 1 — Editing an NPC script from the map:**
1. User has Porymap open, painting tiles on PalletTown
2. Switches to Events tab, clicks on an NPC
3. `onEventSelected` fires → bridge tells PorySuite-Z
4. PorySuite-Z's Event Editor jumps to PalletTown, selects that exact NPC
5. User edits the script (dialogue, conditions, trainer battle)
6. Saves in PorySuite-Z → writes scripts.inc
7. Porymap detects change → reloads

**Workflow 2 — Placing a new event and scripting it:**
1. User creates a new object event in Porymap, places it at (8, 14)
2. `onEventCreated` fires → bridge tells PorySuite-Z
3. PorySuite-Z reloads map data, sees the new event
4. User presses Ctrl+E or clicks the new event in Porymap
5. PorySuite-Z selects the new (empty) event
6. User picks a script template (NPC Talker, Trainer, Item Giver, etc.)
7. Saves → Porymap reloads → event now has a script

**Workflow 3 — Starting from PorySuite-Z:**
1. User is editing trainers in PorySuite-Z, wants to see where a trainer is on the map
2. Clicks "Open in Porymap" on the Event Editor
3. Porymap launches/focuses on the correct map
4. User can see the trainer's position and surrounding tiles

**Workflow 4 — Passive sync (no clicks needed):**
1. User is browsing maps in Porymap, just exploring
2. Every time they open a new map, PorySuite-Z silently follows along
3. Event Editor is always showing the same map Porymap has open
4. When the user needs to edit a script, PorySuite-Z is already there

**Workflow 5 — Trainer battle from map:**
1. User clicks a trainer object event in Porymap
2. `onEventSelected` fires with scriptLabel = "Route1_EventScript_TrainerBugCatcher"
3. PorySuite-Z's Event Editor selects that event, sees it has a `trainerbattle` command
4. Right-click context menu shows "Edit Trainer Party" → switches to Trainers tab, selects that trainer
5. User edits the trainer's party, dialogue, prize money
6. Saves → scripts.inc + trainers data updated
7. Porymap reloads → trainer event is up to date

**Workflow 6 — Hidden item placement:**
1. User adds a new Hidden Item bg_event in Porymap at (12, 7)
2. `onEventCreated` fires → bridge tells PorySuite-Z
3. PorySuite-Z reloads map data, shows the Hidden Item Editor panel
4. User picks the item from the Items dropdown, sets the flag, sets quantity
5. Saves → map.json updated with item/flag/quantity
6. Porymap reloads → hidden item has correct data

**Workflow 7 — Warp setup:**
1. User places a warp event in Porymap, sets destination map + warp ID
2. `onEventSelected` fires → PorySuite-Z shows the warp in Event Editor
3. User can see the warp destination, click "Go To" to navigate to the target map
4. Or click "Open in Porymap" to see the destination map in Porymap

**Workflow 8 — Map connections (map joins another map at its edge):**
1. User adds or edits a connection in Porymap (e.g., Route1 connects to PalletTown going south)
2. `onConnectionChanged` fires → PorySuite-Z's Maps tab updates connection display
3. Warp validator re-runs to check all connections are consistent

**Workflow 9 — Layout/tileset changes:**
1. User changes a map's tileset in Porymap (switches secondary tileset)
2. `onTilesetChanged` fires → PorySuite-Z's Layouts tab refreshes
3. If user edits metatiles in Porymap's tileset editor and saves, `onTilesetUpdated` fires
4. PorySuite-Z logs the change (tilesets are Porymap's domain, PorySuite-Z just tracks it)

**Workflow 10 — Wild encounters:**
1. User edits wild encounter tables in Porymap for Route1
2. Saves → `onWildEncountersSaved` fires → PorySuite-Z knows encounters changed
3. If PorySuite-Z has a species tab open showing "where is this species found?", it refreshes
4. Future: full encounter editor in PorySuite-Z with "Open encounter table in Porymap" button

---

#### Phase 7 Build Steps

1. **7A — Install & Launch** (Tools menu, download + patch + build logic, config writing, project auto-setup)
2. **7B — Source Patches** (write the C++ patch files, apply to Porymap source, test compilation)
3. **7C — JS Bridge Script** (the companion .mjs file that runs inside Porymap)
4. **7D — Bridge Watcher** (PorySuite-Z Python module for receiving and dispatching bridge messages)
5. **7E — Reverse Direction** (Open in Porymap buttons everywhere, file watchers, smart reload prompts)
6. **7F — Polish & Testing** (error handling, edge cases, Porymap not running, stale bridge file, both apps saving simultaneously)

Development can be staged: test 7A/7C/7D with stock Porymap first (existing callbacks like `onMapOpened` and `onMainTabChanged` work without patches). Then apply patches for the event/map callbacks that don't exist yet.

### Backlog (do when ready)

**Abilities Editor (LOWEST priority):**
- New toolbar page (icon already exists)
- Edit ability data table: name, description, slot assignment
- Rename abilities with the same refactor system used for species/trainers
- For new abilities: editor makes it clear the user must write the C behavior code manually, shows where the code lives (`src/battle_util.c`, ability handlers), or have an AI agent generate it
- Does NOT attempt to visually edit C battle logic

**Sound Test / Cry Preview:**
- Pokemon and Pokedex tabs: button to preview the cry audio
- Event Editor: preview any SFX when setting up event nodes
- Uses Qt's QMediaPlayer for playback

---

## Architecture

### Menu Structure

```
File
  Open Project...
  Recent Projects  >
  ----------
  Save All          Ctrl+S
  ----------
  Open Project Folder...
  ----------
  Quit              Ctrl+Q

Edit
  Rename Entity...

View
  Pokemon
  Pokedex
  Moves
  Items
  Trainers
  Starters
  Credits
  ----------
  Event Editor
  Maps
  Layouts
  Region Map
  Labels
  ----------
  UI Settings
  Config
  ----------
  Toggle Log Panel

Tools
  Make              F5
  Make Modern       F6
  Play              F9
  ----------
  Open Terminal
  ----------
  Settings...

Git
  Git Panel...      Ctrl+Shift+G
  ----------
  Pull
  Push
  Commit...         Ctrl+Shift+K

Help
  User Guide
  About
```

### Icon Toolbar Layout

```
[Save] [Make] [Make Modern] | [Pokemon] [Pokedex] [Moves] [Items] [Trainers] [Starters] [Credits] | [Events] [Maps] [Layouts] [Region Map] [Labels] | [UI] [Config] [Settings] | [Play]
```

### Unified Window

- `unified_mainwindow.py` — QMainWindow with menu bar, toolbar, QStackedWidget, log panel, status bar
- Toolbar icons switch stacked widget pages
- Save/Make/Play are action buttons (don't switch pages)
- Play button at far right with green arrow

### Data Flow

- PorySuite editors read/write via `source_data` (JSON cache + C header parsing)
- EVENTide editors read/write via `constants_manager` + script parser
- Constant Label Manager reads/writes `labels.json` — display only, never modifies game source
- Unified save triggers both save paths
- Dirty tracking: both sides report unsaved state, title bar shows `[*]`

### Key Files

| File | Role |
|------|------|
| `unified_mainwindow.py` | Main window, toolbar, menu, stacked widget |
| `mainwindow.py` | PorySuite data editor (demoted to widget provider) |
| `eventide/mainwindow.py` | EVENTide script editor (demoted to widget provider) |
| `app.py` | App entry point, launches unified window |
| `projectselector.py` | Project list, single Open button per project |
| `ui/trainers_tab_widget.py` | Trainers editor with rematch tiers and battle dialogue |
| `ui/trainer_class_editor.py` | Trainer class editor — parsers, writers, UI widget |
| `ui/items_tab_widget.py` | Items editor with icon picker and icon table writer |
| `ui/credits_editor.py` | Credits sequence editor |
| `ui/open_folder_util.py` | Cross-platform "Open in Folder" utility |
| `ui/dialogs/settingsdialog.py` | Settings dialog (5 tabs) |
| `core/crashlog.py` | Crashlog management and auto-cleanup |
| `ui/*.py` | Individual editor tab widgets |
| `eventide/ui/*.py` | EVENTide editor tab widgets |

---

## Risk Notes

| Risk | Mitigation |
|------|-----------|
| Label Manager adds a display layer over raw constants | Labels are strictly display-only, stored in a separate file. If `labels.json` is missing or corrupt, everything falls back to raw constant names. Zero risk to game code. |
| Color-coded constants in Event Editor could be hard to read | Make colors user-configurable in Settings. Provide sensible defaults but let users pick what works on their monitor. |
| Apply Movement rework changes how users build scripts | Keep the old data working — any existing applymovement commands in saved scripts must still load and display correctly after the rework. |
| Porymap integration requires understanding Porymap's codebase | Phase 7 — by then the foundation is solid and we can focus entirely on IPC |

---

## Design Principles

- **No hardcoded game-specific labels.** Parse everything from the project's source. The user's hack is set in Hyrule with Zelda creatures — never assume Kanto, Pokemon, or vanilla pokefirered.
- **Dropdowns never scroll when closed.** QComboBox must be physically opened before wheel events change the value. User works via Chrome Remote Desktop with two-finger scrolling.
- **Plain English in all UI text and documentation.** No jargon, no struct names in user-facing strings.
- **Edit freely, save when ready.** All changes stay in memory until explicit save. Refresh discards unsaved work.
- **Display names everywhere, raw constants nowhere.** Wherever a constant appears in the UI, show the friendly name. The underlying code stays untouched.
