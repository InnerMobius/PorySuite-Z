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
- **94 command widgets**: Searchable constant pickers, context-aware dropdowns, scroll-wheel guarded. The 3-page command palette is laid out everyday-use-first — page 1 has lock/faceplayer/applymovement/trainerbattle/message/giveitem/setflag/wildbattle at the top.
- **Written for people who don't write code**: every command carries a one-sentence description, shown at the top
  of its edit dialog and in a hint strip at the bottom of the command picker that follows the mouse. Every field
  has a tooltip. Comparisons read **is exactly / is NOT / is less than / is at least / is at most / is more than**
  rather than `==` `!=` `<` `>=` `<=` `>`.
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

Porymap is NOT bundled — PorySuite downloads, updates and bridges to it. Never edit Porymap to make a
feature work: build it on the PorySuite side or go through the bridge. Integration that required changing
Porymap would break on the user's next Porymap update and would not exist on anyone else's install.

- Event Editor exposes bridge API methods so Porymap can remotely navigate to maps, select events, and trigger reloads
- Maps tab: right-click context menu with "Open in Porymap"
- Layouts tab: "Open in Porymap" button opens the first map using the selected layout

**The protocol, both directions:**

| Direction | File | Carries |
|---|---|---|
| Porymap → PorySuite | `porysuite_bridge.json`, watched by `BridgeWatcher` | `map_opened`, `sync_request`, `event_selected`, `edit_request` |
| PorySuite → Porymap | `porysuite_command.json`, polled by patched Porymap | `openMap` only |

Both sides of the channel, plus the `onMapSaved` / `onEventSelected` callbacks and the CLI project argument,
come from `porymap_patches/apply_patches.py`, which patches Porymap's C++ at install time by anchor string.

**Porymap save → EVENTide: handled, and seamless.** The `SharedFileWatcher` sees `map.json` / `scripts.inc`
change and calls `reload_current_map()`. With no unsaved edits that reloads silently, re-fingerprints, and
restores the event and page the user was on. With unsaved edits the user gets exactly one Save/Discard/Cancel
prompt.

**EVENTide save → Porymap: deliberately left to Porymap (decided 2026-10-24).** Nothing is sent on save, so
Porymap learns from its own file watcher and shows its "file has changed on disk, reload?" prompt
(`porymap_src/src/mainwindow.cpp`, the `fileWatcherWarning`). That is a prompt, not data loss, and the user
chose to leave it there rather than extend the patch set. **Do not add a `reloadMap` command action for this.**
Every patch is another C++ anchor that can drift when Porymap updates upstream, and the gap it closes is one
dialog. If this is ever revisited, test whether sending the existing `openMap` for the already-open map makes
Porymap re-read it, before adding a new patch.

### Pickers instead of typing
Nothing in a command dialog has to be typed as a raw constant, bar one deliberate exception.

- **Value fields** (Set Variable, Add/Sub, both compares, both conditional compares) offer every enum-style
  constant **your project** defines, read out of `include/constants/`, with the prefix stripped for reading and
  the raw constant kept underneath. A project that invents its own families of constants gets them listed like
  any other; nothing in EVENTide knows the name of a single game constant. Plain numbers and TRUE/FALSE sit at
  the top of the list, and anything can still be typed if it is not offered.
- **"Which character?" fields** (all ten object commands) list *This event*, *The player*, and then every
  character on the map by who they are and what sprite they use — `Joe — Man`, `Item ball — Potion` — rather
  than a bare local id. Wait for Movement's `0` entry reads *Everyone who is moving*, which is what it means.
- **Buffer Number** offers your variables. **Buffer String** offers the text this map already has.
  **Multi-Choice** offers your `MULTICHOICE_*` option lists. **Set Metatile** offers your `METATILE_*` tiles.
- **An id or constant EVENTide doesn't recognise is written back exactly as you typed it.** Nothing is quietly
  respelled, so a hand-written script keeps its own wording after a round trip through the editor.
- The one field left as free text is the fallback editor for commands that have no dialog of their own. It takes
  the arguments verbatim, the way the script file spells them, and its tooltip says so.

### Picking a sprite by looking at it
Both places that choose an overworld graphic — the NPC's **Graphic** field and the **Change Graphic** command —
have a **Browse…** button beside them. It opens a searchable grid of every `OBJ_EVENT_GFX_*` the project defines,
drawn as thumbnails, with the selected tile's raw constant shown underneath so what reaches the script is never a
mystery. A constant the project defines but ships no picture for is still listed; it is a valid choice, it just
has no tile.

Thumbnails are the first frame of each sheet, rendered through `core.sprite_render` (never a flat `QPixmap`).
Overworld palettes live apart from the PNG, keyed by palette tag, so the gallery shows the sheet's own baked
palette rather than an unsaved palette edit from the Overworld Graphics tab.

### Choosing a map
**Graphic is the first row of Event Properties**, and the animated sprite preview sits in that row rather than
at the bottom of the column, with **Open PNG** and **OW Graphics** beneath the picker. The preview is fixed at
70x72 and scales anything larger down to fit, so a 64x64 sheet cannot stretch the row.

The **Map** box sits on the Event Editor's own toolbar — no dialog. Type any part of a name and the list
narrows; press Enter or pick from the drop-down to open it. It matches anywhere in the name, not just the start,
so "cave" finds a map whose name ends in one. The wheel does nothing unless the list is open, so scrolling the
page over it cannot swap your map. Cancel an unsaved-changes prompt and the box snaps back to the map you are
really editing.

### Templates that include a message
A `message` command saved in a **My Templates** entry can leave its label blank. On Save, EVENTide names it
`<Map>_Text_<n>`, picking a number that is not already taken, and writes the text into the map's `text.inc`.
That means one template can be applied to several events on the same map without their text labels colliding —
each gets its own, and each is edited in the normal Show Message dialog with the usual line-limit counter.

Templates can also carry `$UNUSED_FLAG$`, which is swapped for a real spare flag each time the template is
applied.

### Script creation
- **New Script templates**: NPC Scripts, Signs, Map Scripts, Standard Wrappers, Field Objects, Item Balls
- **New Hidden Item**: Creates a data-only hidden item (no script) with auto-found unused flag
- **Hidden Item Editor**: Dedicated property panel for hidden items (item, flag, quantity, position, elevation, underfoot)
- **Find Unused Flag**: Scans the project for available `FLAG_UNUSED_*` constants

### Command list interaction
- **Keyboard**: `Del` delete, `Ctrl+C` / `Ctrl+X` / `Ctrl+V` copy / cut / paste, `Ctrl+D` duplicate,
  `Alt+Up` / `Alt+Down` move, `Ctrl+Z` undo, `Ctrl+Shift+Z` (or `Ctrl+Y`) redo, `Ctrl+F` find.
- **Select several commands** with Ctrl-click or Shift-click. Delete, Copy and Cut act on the whole selection;
  structural rows (label markers, the inner When / Branch-End markers) are skipped rather than breaking a block.
- **Undo / redo**: 50 levels, covering add, edit, delete, duplicate, paste, move and drag-reorder. The history
  belongs to the page you are looking at and resets when you switch page, event or map.
- **Drag-to-reorder**: InternalMove drag-drop
- **Right-click context menu**: Edit, Cut, Copy, Paste, Duplicate, Move Up/Down, Insert, Delete, Go To →
- **Command selector**: 3-page tabbed dialog with search bar, a "Recent" row, and a heading over every section.
  Page 1 leads with an **Everyday** shortcut (Show Message, Give Item, Set Flag, If Flag, Set Move Route, Wait
  for Movement, Play Sound Effect, Change Graphic); the commands in it also appear under their own headings
  further down. Lock/Release sit at the bottom, since EVENTide adds them for you. Hovering a button shows its
  description in the strip along the bottom.
- **Give Item vs Add Item**: *Give Item* (`giveitem`) plays the jingle and prints "You got the X!"; *Add Item*
  (`additem`) puts it in the bag in silence, for when the script says something of its own.
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
