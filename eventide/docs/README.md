# EVENTide

The map/world management and event editing side of PorySuite-Z. EVENTide's editors (EVENTide, Maps, Layouts, Region Map) are toolbar pages inside the unified PorySuite-Z window — not a standalone app.

## Toolbar Pages

| Page | Purpose |
|------|---------|
| **EVENTide** | RMXP-style visual script editor — the main editing surface |
| **Maps** | Two sub-tabs: **Map Manager** (map renaming, group management, section renaming, move/delete maps, warp validation) and **Layouts & Tilesets** (layout renaming/deletion, orphan cleanup, tileset reassignment) |
| **Region Map** | Visual region map editor — staged MAPSEC assignment, region create/clone/rename/delete with engine codegen (`src/region_map.c` rewritten between marker comments), external-reference scan, "Open in Tilemap Editor" cross-tab nav |

## Event Editor

The centrepiece. A visual script editor modeled after RPG Maker XP's event editor.

### Core features
- **All event types**: Loads NPCs, triggers, signs, and map scripts from map.json + scripts.inc
- **RMXP-style command list**: `@>Text: Hello world` format. Double-click to edit. `@>` insertion line at bottom.
- **Numbered page tabs**: 1, 2, 3 (matching RMXP) for condition-based script branching
- **Editable conditions box**: Flag/var conditions above Event Properties with searchable pickers
- **▶ Preview (Play Pokémon Cry)**: The `playmoncry` command widget has a Preview button that plays the selected species' cry from `sound/direct_sound_samples/cries/*.wav`.
- **▶ Preview / ■ Stop / 🔊 Open (Sound commands)**: The `playbgm`, `playse`, and `playfanfare` command widgets have three integration buttons. ▶ renders and plays the selected song in the background without leaving the Event Editor. ■ stops playback. 🔊 switches to the Sound Editor tab and selects the song.
- **93 command widgets**: Searchable constant pickers, context-aware dropdowns, scroll-wheel guarded. The 3-page command palette is laid out everyday-use-first — page 1 has lock/faceplayer/applymovement/trainerbattle/message/giveitem/setflag/wildbattle at the top.
- **Display overhaul**: Human-readable names for trainers, items, species, moves, flags, vars
- **Message render modes**: the message dialog's Render dropdown picks *Normal* (`.string` + `msgbox` — the full show /
  wait / button / close routine), *Braille* (`.braille` + `braillemessage`), or *Text Only* (the bare `message`
  command — puts the words on screen and nothing else, so the script decides what happens next; use it before a
  multichoice, which needs the window left open). The MSGBOX-type combo is disabled in the two non-Normal modes
  because neither command takes a type.
- **Warp ID**: warp commands expose the engine's optional warp-ID argument. `(none)` writes the shorter
  `warp MAP, x, y` form; a number writes `warp MAP, warpId, x, y`.
- **Full save**: Writes scripts.inc + text.inc + map.json

### Color scheme (RMXP-style)
Only specific categories get colored — plain text/choices/structure stays default:
- Flow (goto/call) → red | Conditionals → amber | Set Move Route → maroon
- Flags/vars → purple | Items → lime | Sound → orange
- Screen effects → teal | Battles → bright red | Pokemon → gold
- All customisable in Settings → Event Colors (changes apply immediately)

### Movement editing
- **Set Move Route**: Maroon block with inline `$>` steps. Auto-scaffolds new movement labels.
- **Move Route Editor**: RMXP-style popup with 6 category tabs and all 177 movement macros from `asm/macros/movement.inc`
- **Move Camera (Cutscene)**: Full camera control dialog for cutscene sequences. 6 tabs: Pan, Slide, Screen, Effects, Timing, Sound. Mixes movement macros (panning) with script commands (fades, shaking, sound) in one sequence. Auto-generates `applymovement LOCALID_CAMERA` blocks wrapped in SpawnCameraObject/RemoveCameraObject.

### Position intelligence
- **Position overrides**: X/Y spinboxes update per condition page based on OnTransition `setobjectxyperm` commands. Amber background + tooltip shows the source script.
- **Cross-references**: Clickable notes when other scripts modify an NPC's position, with actual coordinates shown inline.

### Navigation
- **Go To →**: Follows goto/call/trainer battle targets across pages and events. Also available as a button directly inside command edit dialogs — any popup for call/goto/call_if_*/goto_if_* commands shows Go To alongside OK/Cancel.
- **Set Flag → Page linking**: `setflag`/`setvar` commands show "→ activates Page N", Go To jumps there
- **Script Lookup** (Ctrl+Shift+F): Project-wide search across 5,300+ labels. Navigates to map/event/page.
- **Find in commands** (Ctrl+F): Inline search with highlight, Next/Prev, wrap-around
- **External script resolution**: Recursively loads shared scripts from `data/scripts/` and `event_scripts.s`
- **Open in Porymap**: Toolbar button launches Porymap focused on the current map

### Porymap bridge
- Event Editor exposes bridge API methods so Porymap can remotely navigate to maps, select events, and trigger reloads
- Maps tab: right-click context menu with "Open in Porymap"
- Layouts tab: "Open in Porymap" button opens the first map using the selected layout

### Script creation
- **New Script templates**: NPC Scripts, Signs, Map Scripts, Standard Wrappers, Field Objects, Item Balls
- **New Hidden Item**: Creates a data-only hidden item (no script) with auto-found unused flag
- **Hidden Item Editor**: Dedicated property panel for hidden items (item, flag, quantity, position, elevation, underfoot)
- **Find Unused Flag**: Scans the project for available `FLAG_UNUSED_*` constants

### Command list interaction
- **Drag-to-reorder**: InternalMove drag-drop
- **Right-click context menu**: Edit, Cut, Copy, Paste, Duplicate, Move Up/Down, Insert, Delete, Go To →
- **Command selector**: 3-page tabbed dialog with search bar and "Recent" row
- **Tooltips**: Descriptive hover tooltips on all controls, edit dialogs, and command palette. Toggleable in Settings.

## Architecture

- **`ui/event_editor_tab.py`** — Main editor (~12,400 lines). Stringizer, command widgets, edit dialogs, page building, load/save, color scheme, position overrides, cross-references, hidden item editor, tooltips.
- **`ui/script_search_dialog.py`** — Project-wide script label search dialog (Ctrl+Shift+F).
- **`backend/script_index.py`** — Label index. Scans all scripts.inc + data/scripts/ on project load (~5,300 labels, <1 second).
- **`backend/eventide_utils.py`** — Script parser, text parser, save writers.
- **`backend/constants_manager.py`** — Loads all project constants from C header files. Supports live push of new `OBJ_EVENT_GFX_*` constants from the Overworld Graphics tab via cross-tab sync.
- **`backend/map_renamer.py`** — Map rename, group/section management, orphan cleanup.
- **`ui/layouts_tab.py`** — Layouts & Tilesets sub-tab (embedded in Maps tab). Layout rename/delete, orphan cleanup, tileset assignment.
- **`backend/layout_renamer.py`** — Layout rename/delete, orphan cleanup, tileset assignment.
- **`backend/tileset_renamer.py`** — Secondary tileset renaming with repo-wide reference updates.
- **`backend/warp_validator.py`** — Find and clean invalid warp destinations.
- **`backend/region_map_manager.py`** — Region map grid operations, clone/rename/delete, image rendering.
- **`backend/file_utils.py`** — Shared text file utilities.
- **`ui/widgets.py`** — Reusable widgets: `ConstantPicker`, `MapPicker`, `SpritePreview`.

## Save model

All saves are manual — only the Save button (or Ctrl+S) writes to disk. `_collect_current()` syncs UI fields to memory when switching between objects/pages but never writes to disk. Save/Discard/Cancel dialog on close, map switch, or refresh. External file watchers (Porymap saves, other tools modifying `map.json`/`scripts.inc`) also trigger a Save/Discard/Cancel prompt before clobbering unsaved edits — see `reload_current_map(force=False)`.

**Save will not write over a file that changed underneath it.** `_load_map` and `_resolve_external_scripts` record a content hash of every file they read into `_disk_state`; `_on_save` calls `_changed_on_disk()` before any writer runs and, if anything differs, offers Reload from disk / Save anyway / Cancel (default Cancel). This is deliberately NOT delegated to the file watcher — a watcher is a best-effort notification that can be missed, and its own prompt offers Save, which lands back in `_on_save`. The check belongs at the point of writing and is keyed on what was actually read. See the 2026-10-24 `docs/BUGS.md` entry before changing it.

**What a save is allowed to touch.** The map's own `scripts.inc` / `text.inc` / `map.json`, and — only when their
commands actually differ from what was read off disk — the shared files holding external labels the map references
(`data/scripts/*.inc`, `data/event_scripts.s`). Resolving a shared label so the editor can DISPLAY it is not editing
it; `_save_external_scripts` compares against `_external_baseline` and skips everything unchanged.

**What the writer is NOT allowed to do.** `lines_from_commands` emits exactly what the command list holds. It does not
add a `lock` it thinks is missing, does not terminate a data table, and does not terminate a block that fell through
into the next label in the original file. Comments and blank lines are replayed through `merge_hidden_lines`.
`_parse_script_lines` matches on the first token (`head == 'name'`), never on a prefix. All of this is guarded by
`tests/test_eventide_script_roundtrip.py` — see the five 2026-10-24 entries in `docs/BUGS.md` for what happened the
last time any of it was not true.

## Docs
- `GUIDE.md` — User guide for the PorySuite/Porymap/EVENTide workflow
- `script_commands.md` — Full FireRed bytecode reference
- `TROUBLESHOOTING.md` — EVENTide-specific troubleshooting

## Test project
`porysuite/pokefirered` is the test copy. Never touch `C:\GBA\pokefirered` during porysuite work.
