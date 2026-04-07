# PorySuite-Z

A unified PyQt6 editor for **pokefirered** decomp projects, combining data editing (species, items, moves, trainers) and event/script editing (NPCs, triggers, signs, map scripts) in one window with an RPG Maker XP-style toolbar.

All edits are written back into the project's canonical `src/`, `include/`, and `data/` files so `make` builds stay stable.

---

> **WARNING: This application is developed with heavy use of AI-assisted coding. While functional, it may contain bugs that can corrupt or break your project files. ALWAYS keep backups of your decomp project (use git!) and test thoroughly after every editing session. You are responsible for verifying that your project still compiles and behaves correctly. The authors are not responsible for any lost or damaged work.**

---

## Getting Started

### Requirements

- Python 3.10+
- PyQt6
- A [pokefirered](https://github.com/pret/pokefirered) decomp project
- WSL1/MSYS2 build environment (devkitPro, agbcc)

### Installation

```bash
pip install -r requirements.txt
```

Launch with `LaunchPorySuite.bat` or:

```bash
python app.py
```

### First Launch

On first run, the **Project Selector** window appears. Use **Open Existing Project** to point PorySuite at your pokefirered project directory. The built-in **Setup Wizard** (Settings > Build Environment) can install MSYS2, devkitPro, agbcc, and the required build tools automatically.

---

## Editor Pages

PorySuite-Z has 13 toolbar pages accessible from the RPG Maker XP-style icon toolbar:

### Pokemon

Full species editor with three sub-tabs:

- **Info** -- Species name, Dex number, category, description, types, abilities (including hidden), held items, gender ratio, egg groups/cycles, catch rate, friendship, growth rate, EXP yield, flags (Legendary, Mythical, etc.)
- **Stats** -- Base stats (HP/ATK/DEF/SP.ATK/SP.DEF/SPEED) and EV yields
- **Images** -- Front sprite, back sprite, icon, and footprint editors. **"Open Graphics Folder"** button opens the species' graphics directory for manual editing.

Evolution chain editor with species, method, and parameter fields.

### Pokedex

National and Regional Dex editors. Add, remove, and reorder entries. Each entry shows a detail panel with classification, height/weight, description, and a size comparison preview (Pokemon sprite overlaid on trainer sprite).

### Items

Searchable item list with a detail editor for each entry: constant name, display name, price, pocket type, item type, hold effect, field/battle use functions, description, and auto-resolved icon previews. Includes an **editable icon picker** -- change which sprite an item displays by picking from a dropdown of all available icons with thumbnails. Changes are saved to `item_icon_table.h`. An **"Open Icon in Folder"** button opens the current icon's PNG in your OS file manager for easy editing.

### Moves

Searchable and filterable move list. Detail editor includes: display name, power, accuracy, PP, type (color-coded), category (Physical/Special/Status with Gen 3 auto-calculation), target, effect description with per-line character limits (42 chars x 3 lines), and move flags.

### Starters

Configure the three starter Pokemon. Each slot has: species, level, held item, custom move (optional), and ability selection.

### Trainers

Two sub-tabs on the Trainers page:

- **Trainers** -- Searchable trainer list grouped by trainer class. Detail editor includes: class, name, trainer pic (with visual preview), encounter music, AI flags, party type, and a full party editor with per-member level, species, held item, moves, and ability. VS Seeker rematch tier support with dynamic tier labels.
- **Trainer Classes** -- Searchable class list with sprite thumbnails. Edit class display name (12-character limit), prize money multiplier, and default sprite (dropdown with thumbnails of all trainer pics). Create new classes with a button that writes to three files. View battle info (BGM category, victory music, terrain override), encounter music, facility class mappings, and usage counts. "Open File in Folder" button for the sprite PNG.

### UI (Text Content)

Three sub-tabs for editing in-game text:

- **Name Pools** -- Player and rival name suggestions from `new_game_intro.inc`
- **Location Names** -- Region map section names
- **Key Strings** -- Miscellaneous game strings from `src/strings.c`

### Config

Edit build configuration (`config.mk`) and game defines (`include/config.h`). Makefile variables and C preprocessor `#define` values are organized into collapsible section cards with toggle support.

### Event Editor

RMXP-style visual script editor. See `eventide/docs/README.md` for full details. Key features:
- All event types (NPCs, triggers, signs, hidden items, map scripts) with numbered condition pages
- Hidden item editor — dedicated property panel for data-only hidden items (no script needed)
- RMXP-style color scheme (customisable), conditions box, Set Move Route editor
- Position overrides from OnTransition scripts, cross-reference links
- Script Lookup (Ctrl+Shift+F) — project-wide search across 5,300+ labels
- 84+ command widgets, drag-to-reorder, right-click context menu, Go To navigation
- Go To button in command edit dialogs — double-click a call/goto/conditional and navigate directly to the target script
- Plain English display names for all constants (flags, vars, weather, sounds, fade types)
- Move Camera cutscene tool — pan, fade, shake, weather, sound, timing in one dialog

### Maps

Map renaming, group management, section renaming, move/delete maps, warp validation.

### Layouts & Tilesets

Layout renaming/deletion, orphan cleanup, tileset reassignment, secondary tileset renaming.

### Region Map

Visual region map editor with actual tileset graphics as background. Section assignment, region clone/rename/delete. Supports all 4 FireRed regions (Kanto + 3 Sevii). Dual layer (Map + Dungeon).

---

## Menus

### File

| Action | Shortcut |
|--------|----------|
| Open Project | Ctrl+O |
| Recent Projects | -- |
| Save | Ctrl+S |

### Project

| Action | Shortcut |
|--------|----------|
| Export to Patch (.bps) | Ctrl+Shift+E |
| Make (Build ROM) | Ctrl+M |
| Make Modern | -- |

### Tools

| Action | Shortcut |
|--------|----------|
| Install Porymap | -- |
| Open in Porymap | Ctrl+F7 |
| Open Terminal | Ctrl+T |
| Rename Species | -- |
| Open Crashlogs Folder | -- |
| Git (submenu) | -- |
| Settings | -- |

#### Git Submenu

- **Git Panel** -- A dedicated window for managing origin/upstream remotes, pulling, committing (with file selection and message input), pushing, branching, and stash operations.
- Quick actions: Pull from upstream, Pull from origin, Push to origin, Commit, Configure, Status, New Branch, Stash, Pop Stash, Log

---

## Settings

Accessible from Tools > Settings:

- **Advanced Diagnostics** -- Verbose internal logging for types/gender parsing (off by default)
- **Autosave** -- Experimental auto-save (not yet active)
- **Notifications** -- Re-enable previously suppressed dialogs
- **Build Environment** -- Open the Setup Wizard to install/verify build tools
- **Event Colors** -- Customise colors for constant types (flags, vars, trainers, items, species, moves) and command categories (dialogue, flow, movement, sound, screen, battle, pokemon, items, system). Changes apply immediately.
- **Event Editor Tooltips** -- Toggle descriptive hover tooltips in the Event Editor on/off (on by default, applies immediately)

---

## Porymap Integration

PorySuite-Z integrates [Porymap](https://github.com/huderlem/porymap) as a companion visual map editor. Edit tiles and place events in Porymap, edit scripts and data in PorySuite-Z — the two apps communicate bidirectionally.

### Setup

1. Go to **Tools > Install Porymap**
2. The installer clones the Porymap source, downloads the Qt SDK, applies PorySuite-Z patches, compiles, and deploys — all automatically
3. Progress shows which file is being compiled so it doesn't appear hung

### Usage

- **Open in Porymap** (Ctrl+F7) — opens Porymap to whatever map you're editing in PorySuite-Z
- If Porymap is already running, it switches to the requested map instead of opening a second window
- Clicking an event in Porymap updates PorySuite-Z's Event Editor to show that event's script
- Maps/Layouts tabs have right-click "Open in Porymap" context menus
- Shared file watchers detect when Porymap saves a map and offer to reload in PorySuite-Z

### What gets patched

The installer adds event callbacks, a bridge API, and CLI argument handling to Porymap's scripting engine via `porymap_patches/apply_patches.py`. This is a search-and-replace patcher (not fragile git patches) that survives upstream Porymap updates. The patched binary lives in `porymap/` (not committed to git — built locally).

---

## How Edits Are Saved

PorySuite-Z reads from and writes back to the original pokefirered source files:

- Edits modify only the relevant fields in existing file structures (`.field = value` blocks, enum entries, etc.)
- Whitespace, comments, field order, and formatting are preserved
- If a required source file is missing or the layout is ambiguous, the save aborts with an error and no files are changed
- Species graphics cloning prompts you to pick a template when adding new species or items

---

## Build

Build your ROM directly from PorySuite:

- **Project > Make** (Ctrl+M) -- Standard build
- **Project > Make Modern** -- Modern build variant
- **Project > Export to Patch** (Ctrl+Shift+E) -- Generate a `.bps` patch file

Build output streams in real time in-app.

---

## License

Original [PorySuite](https://github.com/jschoeny/PorySuite) by jschoeny.
