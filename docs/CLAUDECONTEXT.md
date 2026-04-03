# PorySuite-Z — Claude Context for New Threads

## What is this project?
PorySuite-Z is a unified editor for pokefirered decomp ROM hacking projects. It combines two previously separate tools:
- **PorySuite** — edits Pokemon data (species, items, moves, trainers, starters, pokedex, config)
- **EVENTide** — edits maps, scripts, events, layouts, region maps, and text

## Current state (2026-04-03)
- **Phases 1–6 complete**: Unified window, shared data, cross-editor features, VS Seeker rematch system, all data editors, settings
- **Phase 5E complete**: All EVENTide improvements done — Move Camera, Tooltips, Live Settings Reload, Hidden Item Editor
- **Phase 7 in progress**: Porymap Integration — infrastructure complete (bridge, launcher, installer, file watchers, C++ patcher). Remaining: actual build testing, wiring up additional save-callback hooks

## Key architecture
- `unified_mainwindow.py` — The main window. Creates hidden PorySuite + EVENTide windows, pulls their widgets into a QStackedWidget with RPG Maker XP-style icon toolbar
- `mainwindow.py` — The original PorySuite window (8600+ lines). Still has all data logic
- `eventide/mainwindow.py` — The original EVENTide window. Still has all map/script logic
- `app.py` — Launcher. `_launch_unified()` creates both windows, loads data, creates UnifiedMainWindow
- `projectselector.py` — Project picker. Single "Open" button per project

## Data layer
- PorySuite: `source_data` (PokemonDataManager via plugin system, JSON cache of C headers)
- EVENTide: `ConstantsManager` (class-level attrs parsed from C headers) + `eventide_utils` (text.inc/scripts.inc parsing)
- Unified save triggers both save paths; dirty tracking shows `[*]` in title bar

## Editor tabs (PorySuite side)
| Tab | Sub-tabs / Features |
|-----|-------------------|
| Pokemon | Info, Stats, Images — full species editor with evolution chains |
| Pokedex | National/Regional dex editors with detail panel |
| Items | Searchable list, detail editor, **editable icon picker** (writes item_icon_table.h), "Open Icon in Folder" |
| Moves | Searchable/filterable list, detail editor with effect descriptions |
| Trainers | **Trainers** sub-tab (party editor, rematch tiers) + **Trainer Classes** sub-tab (name/money/sprite editing, add new classes, battle info, facility classes) |
| Starters | Three starter slots with species, level, item, moves |
| UI | Name Pools, Location Names, Key Strings |
| Config | config.mk and config.h editors |

## Editor tabs (EVENTide side)
| Tab | Features |
|-----|---------|
| Event Editor | RMXP-style event editing with pages, conditions box, commands, color coding, sprite preview, position overrides, cross-references, script lookup |
| Maps | Map renaming, group management, warp validation |
| Layouts | Layout rename/delete, tileset assignment |
| Region Map | Visual editor for Kanto + Sevii regions |

## Phase 5E — EVENTide Improvements — COMPLETE

### Event Editor features
- **RMXP Conditions Box**: Editable flag/var conditions above Event Properties (flag picker + is ON/OFF, var picker + operator + value)
- **Numbered Page Tabs**: 1, 2, 3 instead of script label names
- **Set Move Route Rework**: Auto-scaffold movement labels, RMXP-style Move Route Editor popup with 6 category tabs
- **Color Scheme**: RMXP-style coloring — flow (red), conditionals (amber), movement (maroon), flags (purple), items (lime), sound (orange), screen (teal), battles (red), pokemon (gold). Customisable in Settings → Event Colors.
- **Position Overrides**: X/Y spinboxes update per condition page based on OnTransition scripts' setobjectxyperm commands
- **Cross-References**: Clickable notes when other scripts modify an NPC's position, with X/Y coordinates shown inline
- **Set Flag → Page Linking**: setflag/setvar commands show "→ activates Page N" and Go To jumps there
- **Script Lookup** (Ctrl+Shift+F): Project-wide search across 5,300+ labels, navigates to map/event/page
- **External Script Resolution**: Recursive loading of shared scripts from data/scripts/
- **Display Overhaul**: Human-readable names for all constants (trainers, items, species, moves, flags, vars)
- **Script Templates**: New Script menu with NPC, Sign, Map Script, Standard Wrapper, and Field Object templates
- **Constant Label Manager**: Standalone toolbar page for flag/var labels (porysuite_labels.json)
- **Move Camera Command**: Cutscene camera tool with 6 tabs (Pan, Slide, Screen, Effects, Timing, Sound). Auto-generates applymovement LOCALID_CAMERA blocks wrapped in SpawnCameraObject/RemoveCameraObject.
- **Comprehensive Tooltips**: Descriptive hover tooltips on all main UI controls, all command edit dialogs, all camera dialog buttons, and all ~80 commands in the command selector palette. Toggleable via Settings → Event Editor Tooltips.
- **Live Settings Reload**: All Event Editor settings (colors, tooltips) apply immediately when the Settings dialog is closed — no restart needed. `reload_settings()` on EventEditorTab handles reloading and recoloring.
- **Hidden Item Editor**: Selecting a hidden_item bg_event swaps the command list for a dedicated property panel (item picker, flag picker, quantity, position, elevation, underfoot). New Hidden Item creation via New Script menu. Delete button. Combo label updates live.

### Other Phase 5 features
- Trainer Class Editor, Open in Folder buttons, editable item icons (all complete)

## Phase 7 — Porymap Integration (working, polish remaining)

PorySuite-Z integrates Porymap as a companion map/tile editor. The two apps communicate via a JSON bridge file, command polling, and shared file watchers, behaving like one tool.

**Architecture:**
- `porymap_bridge/` — Python modules for bridge communication, launching, installing
- `porymap_patches/apply_patches.py` — Python patcher adds 11 callbacks + 5 query functions + `openMap` + `readCommandFile` + CLI arg handling to Porymap's C++ source
- `porysuite_bridge.mjs` — JS companion script runs inside Porymap, handles all callbacks, writes bridge messages, and polls for PorySuite commands every 500ms
- `SharedFileWatcher` — monitors `map.json`, `scripts.inc`, `layouts.json`, `map_groups.json` for external changes

**What's working:**
- Install pipeline (Tools > Install Porymap) with compile progress streaming
- "Open in Porymap" opens to the correct map (CLI args + QDir::cleanPath fix)
- If Porymap is already running, PorySuite sends a command file and Porymap switches maps (bidirectional sync via readCommandFile + bridge polling)
- Duplicate window prevention (Win32 window enumeration + bring-to-front)
- Bridge watcher (18 signals), launcher with config writing, bridge script auto-injection
- C++ patcher survives `git reset --hard` (Install Porymap re-applies all patches)

**What's remaining:** Auto-sync on map selection (map_selected signal → auto-send to Porymap), Ctrl+E handler cleanup, additional callback wire-up (event creation/deletion/movement), stock Porymap fallback testing.

## Standardised text editing
- `ui/game_text_edit.py` — `GameTextEdit` widget for all game text editing
- `ui/dex_description_edit.py` — `DexDescriptionEdit` for item/dex descriptions
- 36 chars/line for dialogue, per-line colour-coded counter, overflow highlighting
- {COMMANDS} highlighted in blue, don't count toward limit

## Save pipeline (simplified)
1. `save_species_data()` — capture current species UI → memory
2. `save_items_table()` + `save_icon_changes()` — items + icon table
3. `_save_trainer_classes()` — trainer class names, money, sprite mappings
4. `save_data()` — write JSON caches
5. Direct header writers — species_info.h, pokedex_entries.h, items.h, learnsets, etc.
6. `parse_to_c_code()` — trainers.h, battle_moves.h (old pipeline, skipped for files handled by direct writers)

## File locations
- Project root: `C:\GBA\porysuite`
- Test copy: `C:\GBA\porysuite\pokefirered` (never touch `C:\GBA\pokefirered`)
- Read-only reference: `C:\GBA\READONLYREFERENCE\pokefirered`
- Settings INI: `C:\GBA\porysuite\data\settings.ini`
- Toolbar icons: `C:\GBA\porysuite\res\icons\toolbar\`

## Important rules
- Always explain in plain English, no jargon
- Stop and ask when uncertain
- Test instructions must be precise and step-by-step
- Never touch `C:\GBA\pokefirered` during porysuite work
- Log all changes in CHANGELOG.md
- Document changes in related .md files
- QComboBox must never scroll on wheel when closed (Chrome Remote Desktop safety)
- No hardcoded game-specific labels — user's hack is Zelda/Hyrule themed
