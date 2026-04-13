# PorySuite-Z Unified Editor — Master Plan

## Goal

Merge PorySuite (data editor) and EVENTide (script editor) into a single window with an RPG Maker XP-style toolbar. One app, one window, all editors accessible from icon buttons. Universal save model — edit freely, save when ready, prompt on close.

---

## Current State (2026-04-13)

Phases 1 through 6 are **complete**. **Phase 9 — Pokédex Habitat/Area Display — COMPLETE**: Wild Encounters card on detail panel shows per-species locations with color-coded method dots, level ranges, fishing rod sub-groups, and multi-floor merging. Fully compatible with custom maps and non-vanilla hacks. Phase 5E (EVENTide Improvements) is **complete** — all features done including Move Camera Command, Comprehensive Tooltips, Live Settings Reload, and Hidden Item Editor. **Phase 7 — Porymap Integration** is functional — install, launch, bidirectional map sync, auto-sync on map switch, Go To button in command dialogs all working. Polish remaining. **Sound Editor Phases 1-9 — COMPLETE** including Piano Roll with Song Structure panel, Save button, instrument dropdown, voicegroup friendly labels, and .s file import from other projects. **Abilities Editor (Phase 8A) — COMPLETE** — overhauled 2026-04-09 with **52 battle templates** achieving **74/74 detection of all vanilla abilities**. Every ability now shows an editable template, not a "no editable template" fallback. Templates cover: status immunity, contact effects, type absorb, weather, stat boost, intimidate, pinch boosts, type immunity, weather recovery, type trap, crit prevention, OHKO prevention, evasion weather, stat double, type resist, block stat/flinch, accuracy boost, guts, weather speed, prevent escape, natural cure, pressure, wonder guard, recoil immunity, type change+boost (Pixilate), dual intimidate, Trick Room/Tailwind, multi-type resist, weather suppress, shed skin, truant, sound block, color change, synchronize, suction cups, sticky hold, shield dust, lightning rod, serene grace, hustle, marvel scale, early bird, liquid ooze, plus/minus, damp, contact flinch, trace, forecast, block specific stat. **Save & Git confirmation dialogs** added — File → Save, piano roll save, and git push/pull all require explicit confirmation before proceeding. **Song Writer Optimizations — COMPLETE**: TIE/EOT for long notes, redundant control filtering, proper song deletion cleanup. **ROM Diagnostics Tab — COMPLETE**: ROM size, EWRAM/IWRAM usage, section breakdown, build info. **Piano Roll Save Pipeline Fixes (2026-04-08) — COMPLETE**: PATT subroutine corruption fixed (stripped to linear on save), VOL/TEMPO double-evaluation fixed (reverse-evaluation helpers), BEND loop reset fixed. **Note Properties Dialog — COMPLETE**: Right-click note editing for BEND/control events. **MIDI Import Dropdowns — COMPLETE**: Named instrument dropdowns replace number spinners. **Piano Roll Loop & Save Fixes (2026-04-08) — COMPLETE**: Loop playback used wrong loop_end (max across all tracks), fixed to use track 0's flattened value. GOTO save placed at wrong tick when notes existed past loop end, fixed with timeline insertion. Phantom dirty flag on close after Sound Editor save, fixed. **Piano Roll UX Polish (2026-04-08) — COMPLETE**: Double-click note placement (prevents accidental), Ctrl+Z undo, default snap 1/16, sidebar track sets active instead of filtering, .s reimport/overwrite, git pull refreshes Sound Editor. **Fractional Pitch Bending (2026-04-08) — COMPLETE**: Sub-semitone GBA MidiKeyToFreq interpolation in both renderers, keysplit int() fix, GBA linear pan crossfade. **Piano Roll Control Event & Scroll Fixes (2026-04-08) — COMPLETE**: Control event explosion dedup (PATT flattening duplicated PAN/BEND), PATT label filtering in Song Structure, loop tick drift fix (WAIT before LABEL), label name preservation from Song Structure panel, horizontal scroll by default (wheel), middle-click zoom anchored to cursor. **MIDI Import Robustness (2026-04-08) — COMPLETE**: No Loop mode uses parse→flatten→rewrite pipeline, meta event allowlist cleaning, Type 0→1 MIDI conversion, default import mode changed to "No Loop (clean)". **GM Voicegroup Drum Fix (2026-04-08) — COMPLETE**: 21 drum samples added to mapping, unplaced DirectSound samples recovered into empty slots. **Orphaned Song Registration Cleanup (2026-04-08) — COMPLETE**: Auto-cleanup on project load detects and removes MUS_* entries with missing .s files from all config files. Failed MIDI import cleanup fixed (wrong filename). **Phantom Dirty Flag Fix (2026-04-09) — COMPLETE**: "Unsaved Changes" dialog no longer appears on close/refresh after saving. Root causes: deferred items loader re-dirtying on startup, and gfx_combo repopulation during save marking EVENTide dirty. Fixed with deferred dirty clear, blockSignals during combo refresh, and unconditional EVENTide dirty clear after save. **Pre-Build .s Protection (2026-04-09) — COMPLETE**: `_on_make()` touches all .s files before every build as defense-in-depth against mid2agb overwriting tool-edited songs. **Phase 8B Dirty Flag & Editor Fixes (2026-04-09) — COMPLETE**: Structural dirty-marking loop covers all UI widgets. Abilities effect detection fixed (was using wrong `local_util`). Starter ability combo boxes enabled and saved. "Add to VS Seeker Rematch Table" button with auto map detection. Instrument loop dirty marking. `_on_child_modified()` suppression. **Phase 10A — Tilemap Editor + Palette Editor — COMPLETE**: Full tilemap viewer/editor with 4bpp/8bpp auto-detection, paint/eyedropper tools, zoom/grid, palette-aware rendering, tile offset for VRAM mapping, visual palette editor with JASC .pal 16/256-color import/export, compact UI with auto-sizing palette display. Core: `core/tilemap_data.py`, UI: `ui/tilemap_editor_tab.py`. **Phase 10B — Tile Animation Editor (AnimEdit-Style) — COMPLETE**: Full AnimEdit-style rework. Navigation by Tileset + Animation Number (68 tilesets from headers.h). ALL properties editable and saved to C source: divisor, start tile (hex), tile amount, phase, counter max. Palette integration with 16 .pal file loading, editable swatches, import/export (shared with Porymap). Add/Remove Animation with full C source wiring. Side-by-side layout, display size (1x-8x), frame scrubber, tile grid with hex VRAM addresses and horizontal toggle. Covers all 77 animations (8 tileset BG, 32 door, 37 field effect). Core: `core/tileset_anim_data.py`, UI: `ui/tile_anim_viewer.py`. Remaining: 10C pixel editor.

**18 toolbar pages are live:** Pokemon, Pokedex, Moves, Items, Trainers, Starters, Credits, Overworld GFX, Abilities, Sound Editor, Diagnostics, Tilemap Editor, Event Editor, Maps, Layouts & Tilesets, Region Map, UI (Text Content), Config.

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
- **Live cross-editor state bridge (2026-04-05)**: Event Editor continuously syncs its in-memory script state (`_cmd_tuples` → active page dict → `_all_scripts[label]`) on every `_mark_dirty()` call. The Trainers → Dialogue tab reads the Event Editor's live state alongside disk `.inc` files, tags live entries `(live — unsaved edits)`, and shows Settings-driven default dialogue for newly-added trainers in a self-clearing `(Pending)` group box. Trainer battle dialog text edits flow through a shared `_ALL_SCRIPTS['__texts__']` reference (previously a copy that silently dropped edits at save time).
- **Trainer class name live push (2026-04-05)**: `TrainerClassEditor.class_name_edited(const, name)` signal pushes pending class-name renames into the sibling Trainers editor on every keystroke. `TrainersTabWidget.apply_class_name()` updates the in-memory class-names dict and refreshes the list, class dropdown, and detail header.
- **Stale-state guards (2026-04-05)**: (1) `_load_trainers_editor` skips `trainer_class_editor.load()` when it has pending dirty edits, preserving user work across tab switches. (2) `EventEditorTab.reload_current_map(force=False)` prompts Save/Discard/Cancel before clobbering unsaved edits on watcher-driven reloads. (3) `ConstantsManager.refresh()` re-reads all header files when switching from a PorySuite page into an EVENTide page, so item/flag/var/trainer renames reach EVENTide dropdowns without a project reload.

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
- **General tab:** diagnostics, crashlog retention (keep_days + max_size_mb dropdowns)
- **Build & Play tab:** make command, make modern command, .gba path, emulator path, build environment
- **Trainer Defaults tab:** default intro/defeat/post-battle text templates, prize money multiplier
- **Editor tab:** startup page, log panel visibility
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

### Phase 7: Porymap Integration — COMPLETE

PorySuite-Z fully integrates Porymap (the visual map/tile editor) as a companion tool. Users get one seamless workflow: paint tiles and place events in Porymap, edit scripts and data in PorySuite-Z. Map sync and event selection bridge the two apps so they behave like one tool.

**Approach:** PorySuite-Z downloads the Porymap source repo, runs a patch script that adds event-aware callbacks and a bridge API to the existing JS scripting engine, then builds the patched binary. The install script handles downloading Qt build tools and compiling — the user just clicks "Install Porymap" and waits. No fork, no manual steps.

**Status (2026-04-05):** All integration complete. Install pipeline builds patched Porymap with compile progress streaming. Install drops a `.psinstalled` marker so the launcher can degrade gracefully for stock Porymap. "Open in Porymap" opens the correct map via CLI args. Bidirectional map sync, event click→select, Ctrl+E edit-at-cursor with window raise + feedback logging, event create/delete/move lifecycle callbacks, Go To button in command edit dialogs all working. Anti-echo dedup, duplicate-window prevention, JS bridge command-polling backoff all in place.

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

### Phase 8A: Abilities Editor — COMPLETE

Full editor for ability data — names, descriptions, constants, species cross-references, with visual battle and field effect editing. Built and completed 2026-04-07. Significantly exceeds original plan: the "read-only" battle/field info section was replaced with a full visual editor backed by 13 battle effect templates and 8 field effect templates (`core/ability_effect_templates.py`). Users pick an effect category from a dropdown and configure parameters (type, status, stat, weather, chance, HP fraction); the editor detects existing effects by parsing C source, shows live code preview, and writes correct C code to the right files on save. Field templates include features not natively in pokefirered (egg hatching speed, nature sync, gender attract, type encounter boost) — the editor generates and inserts the necessary C code when assigned. Also includes: battle/field effect copying in the Add dialog, auto-derived constant names, full RefactorService rename, DexDescriptionEdit on all text fields. Species usage table shows real project data (no name fabrication). Everything lives in PorySuite's own codebase — no changes are made to the loaded project until the user saves.

**Source files touched by abilities in pokefirered:**

| File | What It Stores |
|------|---------------|
| `include/constants/abilities.h` | `#define ABILITY_*` constants (78 abilities, IDs 0–77), `ABILITIES_COUNT` |
| `src/data/text/abilities.h` | `gAbilityNames[78][13]` (display names, 12-char max) + `gAbilityDescriptionPointers[78]` (description strings, ~50 chars practical max — 52-byte buffer on GBA summary screen) |
| `src/data/pokemon/species_info.h` | `.abilities = {ABILITY_X, ABILITY_Y}` per species (2 slots, no hidden ability in Gen 3 struct) |
| `src/battle_util.c` | `AbilityBattleEffects()` — switch/case battle behavior (editor writes/replaces case blocks) |
| `src/wild_encounter.c` | Field effects — encounter rate, type encounter, nature sync, gender attract (editor inserts code) |
| `src/battle_script_commands.c` | Pickup item table, type immunity checks, status prevention (editor writes inline code) |
| `src/pokemon.c` | `CalculateBaseDamage()` — pinch type boosts (editor inserts if-statements) |
| `src/battle_main.c` | Flee prevention (traps), guaranteed escape, speed modifiers (editor inserts code) |
| `src/daycare.c` | Egg hatch speed (editor inserts ability check if assigned) |
| `src/battle_ai_switch_items.c` | AI checks for absorbing/blocking abilities (not edited by templates) |
| `src/pokemon_summary_screen.c` | Displays ability name (12 chars) + description (52-byte buffer, 29-tile-wide window) |
| `include/battle_main.h` | `ABILITY_NAME_LENGTH = 12` |

**UI Layout:**

Left panel — Ability browser:
- Searchable list of all abilities sorted by ID
- Each row: ID number + display name
- Filter bar at top
- "+ Add New Ability" button at bottom

Right panel — Detail view (4 sections):

1. **Identity** (top)
   - Constant name (read-only — renamed via Rename button which triggers RefactorService across all files)
   - Display name (editable, 12-char limit with counter + color feedback per project rules)
   - ID number (read-only)

2. **Description** (middle)
   - Text edit with character limit feedback (52 chars max, counter label, overflow highlighting per project rules)
   - This is what shows on the in-game Pokemon Summary screen

3. **Battle Effect Editor**
   - Category dropdown with 31 templates organized into groups:
     - **Original 13**: Status Immunity, Contact Status Infliction, Type Absorb (HP), Type Absorb (Power Boost), Weather on Switch-In, End-of-Turn Stat Boost, Intimidate, Contact Recoil, Low-HP Type Power Boost, Type Immunity, Weather HP Recovery, Type Trap, Critical Hit Prevention
     - **Inline-detected 14**: OHKO Prevention (Sturdy), Evasion Weather (Sand Veil), Stat Double (Huge Power/Pure Power), Type Resist Halve, Block Stat Reduction (Clear Body), Block Flinch (Inner Focus), Accuracy Boost (Compound Eyes), Guts Boost, Weather Speed (Swift Swim/Chlorophyll), Prevent Escape (Shadow Tag/Arena Trap), Natural Cure, Pressure, Wonder Guard, Recoil Immunity (Rock Head)
     - **Advanced 4**: Move Type Change + Boost (Pixilate/Aerilate/Refrigerate), Dual Stat Intimidate ("Scare"), Switch-In Field Effect (Trick Room/Tailwind), Multi-Type Resist (Thick Fat)
   - Dynamic parameter widgets per category (which status/type/stat/weather, chance %, HP fraction, power boost %, dual stat selection)
   - Live C code preview showing what will be written to source files
   - Auto-detects existing effect by parsing C source when loading an ability — scans battle_util.c (case blocks), battle_script_commands.c (inline checks), pokemon.c (damage calc), and battle_main.c (flee/speed) using tight per-line context (±5 lines) to avoid false positives
   - **"Known Effect" fallback labels**: When detection can't match an editable template, but the ability has a known effect in the hardcoded category database, a green info label shows a human-readable description instead of "(none)"

4. **Field Effect Editor**
   - Category dropdown with 8 templates: Reduce Encounter Rate, Increase Encounter Rate, Type Encounter Rate Boost (pick type), Post-Battle Item Pickup (pick chance), Guaranteed Wild Escape, Faster Egg Hatching, Nature Sync, Gender Attract
   - Dynamic parameter widgets where applicable
   - Templates for features not natively in pokefirered (egg hatch, nature sync, gender attract, type encounters) generate and insert the C code when assigned

5. **Species Usage** (bottom)

   - Table: Species Name | Slot (Primary / Secondary)
   - Double-click → jumps to that species in the Pokemon tab (cross-editor link)
   - Count header: "Used by X species (Y as primary, Z as secondary)"
   - Yellow warning if ability is assigned to zero species

**Add New Ability:**
- Dialog: constant name (auto-prefixed `ABILITY_`), display name (12-char limit), description (52-char limit)
- Writes: new `#define` in `abilities.h`, new name + description entries in `data/text/abilities.h`, bumps `ABILITIES_COUNT`
- Shows reminder: "New ability has no battle effect. Add behavior in `src/battle_util.c` → `AbilityBattleEffects()`"

**Delete Ability:**
- Safety scan before deletion:
  - Check `species_info.h` for any species using it → block + show which species
  - Grep battle code for hardcoded `ABILITY_*` references → warn that C code references exist
- If safe: remove constant, name, description entries; update `ABILITIES_COUNT`

**Rename Constant:**
- Uses existing RefactorService pattern (same as species/trainers)
- Generates search/replace tokens for `ABILITY_OLD` → `ABILITY_NEW`
- Applies across all files that reference the constant

**Cross-Editor Sync:**
- When ability is renamed/added/deleted, Pokemon tab's ability dropdowns refresh immediately
- Same signal pattern as trainer class name push (`ability_names_changed` signal)
- Name Decapitalizer's existing `_flush_abilities()` path migrates into the Abilities Editor save pipeline (single owner of `abilities.h` writes)

**Save Pipeline (follows Moves editor pattern):**
1. `AbilitiesTabWidget.save_current()` — flush detail panel to internal dict
2. `get_abilities_data()` / `get_descriptions()` — return modified dicts
3. `mainwindow.save_abilities_table()` — write names to `gAbilityNames`, descriptions to description strings + pointer array in `data/text/abilities.h`
4. New constants write to `include/constants/abilities.h`
5. Refactor renames go through `RefactorService`

**What this editor does NOT do:**
- Does not add a third ability slot (Gen 3 struct has `abilities[2]` only — expanding requires struct + save format changes, out of scope)
- Some niche effects (Trace, Forecast, Synchronize status-pass) don't have configurable templates yet — the editor shows the "Known Effect" green label for these so users know the effect exists, but they must be edited in C manually
- Rock Head is implemented in battle scripts (.s assembly file), not C — detected via hardcoded category lookup, not source scanning
- Complex multi-file effects (e.g. Hustle modifies both attack and accuracy in different files) are handled for the primary file but may need manual review for secondary locations

**Files created/modified:**

| File | Action |
|------|--------|
| `ui/abilities_tab_widget.py` | **Created** — main editor widget (browser, detail panel, effect editors, add/delete/rename) |
| `core/ability_effect_templates.py` | **Created** — 52 battle + 8 field effect templates (74/74 vanilla ability detection), inline + case-block + name-based detection across 4 C files, code generation, file manipulation |
| `core/refactor_service.py` | **Modified** — added `rename_ability()` + apply_pending handler |
| `ui/custom_widgets/rename_dialog.py` | **Modified** — added Ability to name limits |
| `ui/mainwindow.py` | **Modified** — ability rename handler, save pipeline with effect code writing |
| `ui/unified_mainwindow.py` | **Modified** — added Abilities page to toolbar + stacked widget |
| `core/pokemon_data_base.py` | **Modified** — added setters for ability name/description |
| `core/pokemon_data_extractor.py` | **Modified** — extract descriptions |

**Sound Test / Cry Preview:**
- **Pokemon tab + Pokedex tab — COMPLETE (2026-04-05)**: "▶ Play Cry" button added to both tabs. Plays `sound/direct_sound_samples/cries/<slug>.wav` for the currently selected species via the shared `ui/audio_player.py` `AudioPlayer` (QMediaPlayer + QAudioOutput). Shows a clear "no cry sample found, expected path: …" warning for custom Fakemon without a cry file.
- **Event Editor `playmoncry` — COMPLETE (2026-04-05)**: species picker in the Play Pokémon Cry command widget now has a "▶ Preview" button right next to it.
- **Sound Editor (2026-04-07) — Phases 1-9 COMPLETE (all steps)**: Full GBA M4A audio engine built in Python. Parses song .s files, voicegroups, samples, and renders all 347 songs using the project's actual instruments. Songs Tab with browse/filter/play. Instruments Tab with 144 unique instruments, editable detail panel, piano keyboard with octave shift, sample management (export/import/replace with auto-resampling/delete). Phase 5: Voicegroups Tab — browse all 77 voicegroups, full 128-slot editor, add/clone/delete with reference checking, saves to `voice_groups.inc`. Phase 6 COMPLETE: EVENTide integration (▶ Preview, ■ Stop, 🔊 Open in Sound Editor), F8 shortcut, save pipeline, Sound Settings (volume, loop count, auto-downsample, stereo/mono output mode), constants sync (Sound Editor → EVENTide refresh), Porymap reads songs.h directly. Phase 7 COMPLETE: MIDI Import wizard — 5-page flow: file picker with track preview, voicegroup + settings, per-track instrument mapping (GM → VG slot with auto-match), song structure (full section sequencer — define named sections by measure range, drag/reorder play order, set loop start position; quick presets for simple cases; post-processor generates GOTO/PATT/PEND assembly), mid2agb conversion + registration. `MidiFileInfo` includes `total_measures`, `time_sig_num`, `time_sig_den`. **Phase 8 Steps 8.1-8.5 COMPLETE**: Step 8.1 — Read-only piano roll view with playback. Step 8.2 — Full note editing (place, move, resize, delete, selection, copy/paste, snap grid, cursors). Step 8.3 — Track management sidebar with per-track volume/pan/mute/solo, add/remove/duplicate. Step 8.4 — Real-time sequencer: notes synthesized on-the-fly as cursor crosses them (no pre-rendering), play/pause/resume/seek, ruler drag-to-scrub with red triangle position marker, 32-voice polyphony, per-track mute/solo/volume/pan. Background render thread (note rendering off audio callback for stable tempo), vectorized looping sample resample, PATT/PEND/GOTO flattening for full song structure. All playback bugs fixed (2026-04-07): voicegroup and instrument swaps update live sequencer in-place, track volume/pan sliders wired, pause/resume edge cases handled. Step 8.5 — GM Voicegroup Generator: scans all voicegroups, maps 89 samples to GM program numbers by SC-88 Pro/SD-90 name matching, builds 128-slot GM voicegroup, "Generate GM" button in Voicegroups tab and MIDI Import dialog, auto-detect existing GM voicegroup. Step 8.6 COMPLETE — Round-trip editing: `core/sound/song_writer.py` converts piano roll notes back to .s assembly, follows PorySuite save pipeline (modified signal → File → Save), preserves PATT/GOTO structure. UI overhaul: ruler extracted to fixed `RulerWidget` above scroll area (always visible, drag-to-scrub), toolbar compacted (track tabs → combo box, song info → status bar). **Orphaned Song Cleanup + .s Overwrite Protection (2026-04-08) — COMPLETE**: `cleanup_orphaned_songs()` auto-runs on Sound Editor load — removes MUS_* entries with no .s file from all config files + stray .mid files. `write_midi_cfg()` touches all .s files after writing (midi.cfg is a Makefile dependency for ALL .s files). Every .s write path backdates .mid by 2 seconds. Critical invariant: .s must always be newer than both .mid and midi.cfg. **Sample Loop Controls (2026-04-08) — COMPLETE**: Instruments tab has loop toggle + seconds-based loop point editor with draggable waveform + hold-to-sustain piano preview. Reads/writes .bin file headers directly. Sample data lazy-loads on tab switch. **Instrument Export/Import (2026-04-08) — COMPLETE**: `.psinst` preset format (zip with JSON + .bin). Export/Import buttons in left panel (always visible). Import creates real instruments in voicegroups (replaces filler slots). Handles existing sample conflicts by replacing audio. **Instrument Save Pipeline Fix (2026-04-08) — COMPLETE**: Instruments tab now marks voicegroups dirty via cross-link so edits actually persist on save. PorySuite `isWindowModified` flag cleared after save. **WAV Import/Replace Rate Picker (2026-04-08) — COMPLETE**: Rate/size dialog always shown (was hidden for low-rate WAVs), offers 8000 Hz tier, size warning for large samples. Replace lets user choose rate instead of force-matching original. **Duplicate Loop Label Fix (2026-04-08) — COMPLETE**: Per-track unique assembly labels (prefixed with track name). Song Structure panel strips prefixes for display. Fixes "symbol already defined" assembler error. See `docs/SOUND_EDITOR_PLAN.md` for full roadmap.

**Palette Importer (Graphics tab) — COMPLETE (2026-04-05):**
- **Location**: "Import Palette from PNG" group box in the right column of the Graphics tab, between the Shiny Palette swatches and the Icon Palette section.
- **UI**: Normal/Shiny radio buttons (Normal default) + "Select Indexed PNG..." button.
- **Flow**: User picks an indexed PNG → tool extracts color table (up to 16 colors) → GBA-clamps each to 15-bit → loads into selected palette slot (Normal or Shiny). Swatches and battle preview refresh immediately.
- **Validation**: Checks `Format_Indexed8`; shows clear error if the PNG is not indexed.
- **Save**: Palette marked dirty → written to `.pal` file on File → Save via existing `flush_to_disk()` pipeline.
- **File**: `ui/graphics_tab_widget.py`.

**Name Decapitalizer (Edit menu) — COMPLETE (2026-04-05):**
- **Menu item**: **Edit → Name Decapitalizer…**
- **Categories (all 7 delivered)**: species names, move names, item names, trainer names, trainer class names, ability names, UI key strings. Dialogue scripts deliberately not touched (user manages those by hand).
- **Safety guard**: only strings whose alphabetic characters are ≥70% uppercase are offered for conversion, so mixed-case custom names are left alone automatically.
- **Smart Title Case**: first letter of each word cap'd, rest lower; filler words (of/the/and/in/on/to/at/for/by/or/as/vs/de/la/du/nor/but) lowercased mid-string; roman numerals II–XV and XX kept upper; skip-list tokens kept upper; `TM42`/`HM03` style keeps the letter prefix upper. Apostrophes and hyphens preserved (`FARFETCH'D → Farfetch'd`, `ROCK-HARD → Rock-Hard`).
- **Skip-list**: user-editable text box in the dialog, persisted in `settings.ini` under `[NameDecapitalizer]/skip_list`. Seeded with HM, TM, PP, HP, EXP, PC, LV, STR, DEF, ATK, SPE, SPA, SPD, SP, OK, OT, ID, VS, AI, IV, EV, KO, CPU, NPC, TV, PS.
- **UX**: category checkboxes + skip-list editor → Scan Project → preview table (every row shows Category/constant, Original, Proposed with a checkbox) → untick rows you want to keep → Apply Checked.
- **Writes**: species/moves/items/trainers/trainer-classes/UI-strings go through the existing in-memory setters and are committed on File → Save. Ability names are written directly to `src/data/text/abilities.h` at apply time (PorySuite doesn't own abilities.h through its save pipeline). Species names are written directly to `src/data/text/species_names.h` at apply time via `_write_species_names_header()` (final bug fix — previously only the Rename tool wrote this file, so decapitalized species names never persisted to the ROM). Items list + trainer-class list refresh live after apply.
- **Module**: `ui/name_decapitalizer.py`; Edit-menu entry in `ui/unified_mainwindow.py`.

**Trainer Graphics Tab — COMPLETE (2026-04-06):**
- **Location**: New "Graphics" sub-tab alongside "Trainers" and "Trainer Classes" in the trainers section (3rd tab in `_trainers_tab_switcher`).
- **Sprite preview**: Dropdown to select any TRAINER_PIC_* constant (friendly display names like "Lass"). 128×160 sprite preview rendered with the current palette applied via `_reskin_indexed_png`. Combo box wheel-scroll disabled per project rules.
- **Palette editing**: Editable 16-colour palette swatch row (reuses `PaletteSwatchRow` from the Pokemon Graphics tab). Colour changes refresh the sprite preview immediately.
- **Import Palette from PNG**: Extracts colour table from an indexed PNG, GBA-clamps each colour to 15-bit, applies to the palette. File picker defaults to `graphics/trainers/palettes/`.
- **Open Palettes Folder**: Button opens the trainer palettes directory in the OS file manager.
- **Save**: Palette changes saved on File → Save via `flush_to_disk()` which writes JASC-PAL `.pal` files.
- **New file**: `ui/trainer_graphics_tab.py` (`TrainerGraphicsTab` widget).
- **Main window wiring**: `ui/mainwindow.py` — imports `TrainerGraphicsTab`, creates instance, connects `modified` signal, adds as 3rd tab, loads with `pic_map` from trainers editor on project load, saves via `_save_trainer_graphics()` in all three save paths.

**Overworld Graphics Editor — COMPLETE (2026-04-06):**
- **Location**: Top-level "Overworld GFX" tab in the main toolbar with dedicated toolbar icon.
- **Left panel — Sprite browser**: Category filter dropdown + search bar, scrollable thumbnail grid of all 152 sprites, "+ Add New Sprite…" button, Dynamic OW Palettes status/enable button.
- **Right panel — Detail + palette**: Sprite sheet view with animation-type-aware preview, palette editor with "Assign to" reassignment dropdown, Import from PNG, "Show in Folder".
- **Animation types** (parsed from `.anims` field in GraphicsInfo):
  - Walk/Bike/Nurse/HoOh: standard 4-direction walk cycle (stand→step1→stand→step2)
  - Surf: two-row display — Row 1: static directional poses, Row 2: surf run cycle (frames 3-11)
  - Fishing: 4-directional rod animation (South/West/North/East)
  - VS Seeker: single raise animation sequence (0→1→5→6→7→8→6→1→0)
  - Inanimate: single static frame
  - Destroy (CutTree/RockSmash): sequential frame playback
  - Field Move: walk cycle (player holding arm out)
- **Add New Sprite**: Dialog with PNG file chooser, auto-detect frame size/name/palette, animation type picker, category picker, palette choice (DOWP: create new from PNG; non-DOWP: pick from 4 NPC slots). Writes all 6 C headers automatically. New constant pushed to EVENTide's ConstantsManager immediately via `gfx_constants_changed` signal — no save/refresh needed.
- **Dynamic OW Palettes (DOWP)**: One-way patch button modifies 5 C source files to enable per-sprite palettes. Status indicator shows active/inactive.
- **Palette reassignment**: "Assign to" dropdown + Apply button changes a sprite's palette tag in the C source directly.
- **Cross-tab sync**: `gfx_constants_changed` → `UnifiedMainWindow._refresh_eventide_constants()` → `EventEditorTab.refresh_gfx_constants()` repopulates the graphic dropdown. Same pattern as trainers/items/species.
- **Files**: `ui/overworld_graphics_tab.py`, `core/overworld_sprite_creator.py` (new), `core/dynamic_ow_pal_patch.py` (new).

**Project Display Name — COMPLETE (2026-04-06):**
- New "Project" section at top of Settings → General page.
- "Display Name" field controls what shows in the launcher and window title bar.
- Persisted to both `projects.json` (launcher) and per-project `config.json`.
- Does not rename files — cosmetic only.

---

### Phase 8B: Dirty Flag & Editor Fixes — COMPLETE

Seven bugs reported and fixed 2026-04-09.

**Bug 1: _on_child_modified() phantom dirty** — FIXED. Added suppress flag checks.
**Bug 2: Abilities effect detection completely broken** — FIXED. `self.source_data.local_util` doesn't exist — was silently failing, so `project_root` was always empty and detection never ran. Fixed to use `self.local_util`.
**Bug 3: Add to Rematch Table** — FIXED. New button on Party tab auto-detects trainer's map from scripts.inc, writes sRematches[] entry to vs_seeker.c.
**Bug 4: Starter ability combo boxes** — FIXED. Enabled, populated, loaded, saved.
**Bug 5: Structural dirty marking** — FIXED. Single loop connects ALL QComboBox/QSpinBox/QSlider/QLineEdit/QTextEdit widgets in the UI form. Covers 21+ previously unwired widgets (stats, EVs, held items, types, egg groups, catch rate, etc.).
**Bug 6: Instrument loop dirty** — FIXED. `_mark_loop_dirty()` helper in all loop handlers.

---

### Phase 9: Pokedex Habitat / Area Display — COMPLETE

Wild Encounters card on the Pokédex detail panel shows every location where a species can be found, with color-coded encounter method dots and level ranges.

**What was built:**
- `core/encounter_data.py` — reverse species→locations parser. Reads `wild_encounters.json`, resolves map names via `data/maps/*/map.json` + `region_map_sections.json`, falls back to folder name or MAP_ constant cleanup for custom maps
- Color-coded method dots: Grass (green), Surfing (blue), Rock Smash (brown), Old Rod/Good Rod/Super Rod (grey/indigo/purple)
- Fishing sub-groups split by rod type from the `groups` field definition
- Multi-floor dungeons and FR/LG variants merge into single entries per (location, method)
- "Not found in the wild" message for gift/trade/evolution-only species
- Re-parses on F5 refresh (encounter DB reloaded in `load_data()`)
- Graphical map overlay deferred (region map dot highlighting)

**What it does NOT do:**
- Does not edit encounter tables — that stays in Porymap's Wild Encounters tab
- Does not duplicate Porymap's per-map encounter editor

---

### Phase 10: Tileset, Metatile & Tile Animation Editor

A visual editor for the parts of tileset workflow that Porymap doesn't cover — building metatiles from raw tiles, editing palettes, painting tilemaps, and creating/editing tile animations.

#### 10A — Tilemap Editor + Palette Editor — COMPLETE

**Tilemap Editor — COMPLETE:**
- Open any `.bin` tilemap from the project's `graphics/` directory via file dialog
- Auto-discovers tile sheet (`.png`) and palettes (`.pal`) from same directory, parent directory, or `palettes/` subdirectory
- Smart tile sheet selection: reads max tile index from tilemap, scores PNGs by tile coverage and width
- **4bpp and 8bpp support**: auto-detects from PNG color table size (>16 colors = 8bpp). 8bpp tiles use full 256-color palette, palette bits ignored (matches GBA hardware). 4bpp tiles use per-tile 16-color sub-palette selection.
- Smart .pal discovery: when a name-matching .pal file has 256 colors, only loads that one (doesn't pile on unrelated sibling .pal files)
- Tilemap canvas with zoom (1x-8x, Ctrl+scroll), grid overlay toggle
- Paint tool: select tile from tile sheet picker, click/drag to place on tilemap (uses CompositionMode_Source so transparent tiles properly erase)
- Eyedropper tool: click tilemap to pick tile index, palette, and flip flags
- Tile picker panel: click tiles from the tile sheet, palette-aware rendering (8bpp uses full 256-color table)
- Per-tile controls: palette selector (0-15, disabled in 8bpp mode), H-flip, V-flip checkboxes
- Compact control bar: Paint/Pick/Tile/Pal/H/V all in one row, tile sheet dominates right panel
- Tile sheet dropdown: switch between all discovered PNGs in the directory
- Tilemap dimension re-wrap: changing W auto-recalculates H to keep all entries (and vice versa) — never truncates data
- Tile Offset spinbox: adjusts VRAM base offset so tilemap indices map correctly to the current sheet
- Save back to `.bin`
- Status bar shows: file path, dimensions, sheet name, 4bpp/8bpp mode, palette info (loaded slots + .pal file count), max tile index

**Visual Palette Editor — COMPLETE:**
- Auto-sizing: only shows loaded or tilemap-used palette slots (no wasted rows for empty slots)
- Color-coded slot labels: white = loaded + used, red = needed but missing, grey = loaded but unused
- Palette source toggle: "Auto .pal" or "PNG colors"
- Smart palette fallback: `_recolor_tile` uses palette 0 for unloaded slots (handles single-palette PNGs)
- `PaletteSet` tracks `_loaded_slots` to distinguish real palettes from padding
- Right-click any slot for context menu: Import .pal, Export .pal, Extract from PNG, Export All
- **Visible Import/Export buttons** with mode selection: 16-color (to specific slot) or 256-color (fills all slots)
- Supports both 16-color and 256-color JASC-PAL read/write
- Header shows "Palettes (256-color)" or "Palettes (16-color)" based on sheet type
- All combo boxes use `_NoScrollCombo` per project rules

**Files:** `core/tilemap_data.py`, `ui/tilemap_editor_tab.py`, `ui/palette_utils.py`, `ui/unified_mainwindow.py`, `res/icons/toolbar/tilesets.png`

**Metatile Builder (not yet started):**
- Display `tiles.png` as a grid of 8x8 tiles with selectable palette overlay
- Drag tiles into a 2x2 metatile builder — assign tile index, palette (0-15), horizontal/vertical flip per slot
- Read/write `metatiles.bin` (4 x u16 per metatile: tile_index 10 bits + flip_h 1 bit + flip_v 1 bit + palette 4 bits)
- Read/write `metatile_attributes.bin` (u32 per metatile: behavior 9 bits, terrain 5 bits, encounter type 3 bits, layer type 2 bits)
- Metatile list with visual preview — click to edit, reorder, add, delete
- Attribute editor panel: behavior dropdown (parsed from `constants/metatile_behaviors.h`), terrain type, encounter type, layer type
- Support both primary and secondary tilesets

**Palette Editor:**
- Display all 16 palettes as color swatch rows (16 colors each)
- Click swatch → Qt color picker with 15-bit GBA color clamping (reuse `palette_utils.py` from Graphics tab)
- Read `.pal` (PaintShop Pro format) → edit → write back
- Live preview: changing a palette instantly updates the tile and metatile displays
- Import/export palette as `.pal` or raw `.gbapal`

**Tilemap Viewer/Editor:**
- Load `data/layouts/*/map.bin` — display the map grid as placed metatiles
- Each cell is u16: metatile ID (bits 0-9) + collision (bits 10-11) + elevation (bits 12-15)
- Paint metatiles onto the map grid — pick from the metatile list, click/drag to place
- Collision and elevation overlay toggles
- Does NOT replace Porymap's full map editor — this is a lightweight tilemap tool for quick metatile placement and visual verification
- Read/write `map.bin` directly

**Data formats (all well-documented, straightforward binary):**
- `tiles.png` — standard indexed PNG, parsed by Qt's QImage
- `metatiles.bin` — 8 bytes per metatile, no compression
- `metatile_attributes.bin` — 4 bytes per metatile, bit-packed
- `palettes/*.pal` — PaintShop Pro text format (16 colors, 15-bit RGB)
- `data/layouts/*/map.bin` — raw u16 grid

#### 10B — Tile Animation Editor (AnimEdit-Style) — COMPLETE

Complete AnimEdit-style tile animation editor. Navigation by Tileset + Animation Number (68 tilesets from headers.h). Covers all 77 animations across three source systems: 8 tileset BG, 32 door, 37 field effect.

**Three Parsers in `core/tileset_anim_data.py` — COMPLETE:**
- `parse_tileset_anims()` — parses `src/tileset_anims.c` dynamically: INCBIN_U16 frame paths, TILE_OFFSET_4BPP destinations, timer divisor/phase timing, AppendTilesetAnimToBuffer tile counts, counter max values, init function names. CamelCase-to-snake_case conversion matches C names to filesystem directories. 8 animations detected.
- `parse_door_anims()` — parses `src/field_door.c`: door animation frame sequences, spritesheet paths, per-door timing. `DoorAnimation` dataclass. 32 animations detected.
- `parse_field_effect_anims()` — parses `include/constants/object_event_graphics.h` + `src/data/field_effect_objects.h`: field effect spritesheet paths, frame dimensions, animation sequences. `FieldEffectAnimation` dataclass. 37 animations detected.
- `parse_tilesets_from_headers()` — parses `src/data/tilesets/headers.h` for all 68 tilesets with callback detection
- `load_tileset_palettes()` — loads all 16 .pal files per tileset with GBA 15-bit clamping
- `parse_palette_hints()` — extracts `// palette: tileset NN` comments from tileset_anims.c

**AnimEdit-Style Editor — COMPLETE:**
- Tileset dropdown (68 tilesets, animated-first sorting) + Animation Number dropdown ("0: Flower", "1: Water")
- Side-by-side splitter layout (410px left panel for controls, right panel for preview/grid)
- All properties editable with save to C source: Speed/Divisor, Start Tile (hex 0x1A0 matching Porymap), Tile Amount, Phase, Counter Max
- Palette slot selector (00-15) loading tileset .pal files, editable color swatches with GBA 15-bit clamping, import/export .pal (shared with Porymap)
- Display size buttons (1x/2x/4x/8x)
- Frame scrubber (prev/slider/next) for manual stepping through animation sequence
- Filmstrip thumbnail strip showing all frames
- Tile Grid decomposition into 8×8 cells with hex VRAM addresses (matching Porymap convention), horizontal/grid layout toggle
- Add New Animation (+) — creates full C source wiring: INCBIN, frame array, QueueAnimTiles, dispatch call, Init function, headers.h callback
- Remove Animation (−) — cleanly strips all C source references
- Animated preview with speed slider
- Info panel with animation metadata

**Palette Editor (all types) — COMPLETE:**
- **Palette display** — 16 clickable swatches per palette slot, GBA 15-bit clamping
- **Palette color picker** — click any swatch to edit; changes written back to .pal files
- **Import .pal** — load JASC .pal file
- **Export .pal** — save current palette as JASC .pal file

**Tileset-Only Editor Features — COMPLETE:**
- **All property editing** — divisor, start tile (hex), tile amount, phase, counter max — saved to C source with confirmation dialog
- **Replace Frame** — swap a frame's PNG (validates dimensions, warns on mismatch)
- **Add Frame** — import PNG as next numbered frame, add INCBIN + array entry to C source
- **Delete Frame** — remove from C source (INCBIN + array entry); PNG left for manual cleanup
- Confirmation dialogs on all destructive actions

**Door / Field Effect Features — COMPLETE:**
- Read-only frame management (frames derived from spritesheets, not individually editable)
- **Open spritesheet in Explorer** — opens source spritesheet file in OS file manager

**UI Widgets:**
- `_HexSpinBox` — QSpinBox with hex display/input (0x prefix, hex validation)
- `_NoScrollCombo` / `_NoScrollSpin` — wheel-safe controls per project UI rules
- `PaletteSwatch` / `PaletteSwatchRow` — editable color swatch grid
- `AnimPreviewWidget` — QTimer-driven animated playback
- `TileGridWidget` — 8×8 tile decomposition with hex VRAM labels, horizontal/grid toggle, tile_selected signal
- `_AddAnimDialog` — new animation wizard (name, hex start tile, tile amount, divisor, PNG picker)

**Files:** `core/tileset_anim_data.py` (three parsers + full write pipeline), `ui/tile_anim_viewer.py` (complete rewrite — `TileAnimEditorWidget`), `ui/tilemap_editor_tab.py` (tab integration)

#### 10C — Tile Pixel Editor

Animation Creator is now part of 10B (Add New Animation button with full C source wiring).

**Tile Pixel Editor:**
- Paint 8x8 tiles with palette constraints (4bpp = 16 colors from selected palette)
- Pencil, fill, eyedropper, mirror tools
- Shared between: tileset tile editing, animation frame painting, metatile preview touch-up
- Palette-aware: only colors from the assigned palette are available
- Write back to `tiles.png` (tileset editing) or frame PNGs (animation editing)

#### What Porymap already covers (NOT duplicated):
- Map event placement (warps, NPCs, items, triggers)
- Wild encounter table editing
- Map header/connection editing
- Border metatile editing
- Full map navigation and multi-map workflows

#### Toolbar integration:
- New "Tilesets" toolbar icon between existing Layouts & Tilesets and Region Map
- Metatile Builder, Palette Editor, Tilemap Viewer as sub-tabs within the page
- Animation Viewer/Editor as a separate sub-tab or a dock panel

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
  Overworld GFX
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
[Save] [Make] [Make Modern] | [Pokemon] [Pokedex] [Moves] [Items] [Trainers] [Starters] [Credits] [Overworld GFX] | [Events] [Maps] [Layouts] [Region Map] [Labels] | [UI] [Config] [Settings] | [Play]
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
