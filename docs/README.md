# PorySuite-Z

A unified PyQt6 editor for **pokefirered** decomp projects. Data editing (species, items, moves, trainers, abilities), event/script editing (NPCs, triggers, signs, map scripts), sound editing (songs, instruments, voicegroups, piano roll), overworld sprite editing, and Porymap integration — all in one window with an RPG Maker XP-style toolbar.

All edits are written back into the project's canonical `src/`, `include/`, and `data/` files so `make` builds stay stable.

---

> **WARNING: This application is developed with heavy use of AI-assisted coding. While functional, it may contain bugs that can corrupt or break your project files. ALWAYS keep backups of your decomp project (use git!) and test thoroughly after every editing session. You are responsible for verifying that your project still compiles and behaves correctly. The authors are not responsible for any lost or damaged work.**

---

## Getting Started

### Requirements

- Python 3.10+
- A [pokefirered](https://github.com/pret/pokefirered) decomp project

That's it. PorySuite handles the rest -- the built-in **Setup Wizard** installs PyQt6, all Python dependencies, MSYS2, devkitPro, agbcc, and the required build tools automatically on first run.

### Installation

```bash
pip install -r requirements.txt
```

Or just launch it and let the Setup Wizard handle dependencies:

```bash
python app.py
```

You can also use `LaunchPorySuite.bat` on Windows.

### First Launch

On first run, the **Project Selector** window appears. Use **Open Existing Project** to point PorySuite at your pokefirered project directory. If build tools aren't detected, the Setup Wizard will walk you through installing everything.

---

## Editor Pages

PorySuite-Z has 16 toolbar pages accessible from the RPG Maker XP-style icon toolbar:

### Pokemon

Full species editor with three sub-tabs:

- **Info** -- Species name, Dex number, category, description, types, abilities (including hidden), held items, gender ratio, egg groups/cycles, catch rate, friendship, growth rate, EXP yield, flags (Legendary, Mythical, etc.)
- **Stats** -- Base stats (HP/ATK/DEF/SP.ATK/SP.DEF/SPEED) and EV yields
- **Graphics** -- Battle scene preview (front/back sprites over background with shadow), Player Y/Enemy Y/Enemy Altitude, Normal and Shiny palette editors (16-swatch rows with color picker), Import Palette from PNG, Menu Icon with animated preview and palette index selector, footprint preview, Open Graphics Folder

Evolution chain editor with species, method, and parameter fields. Play Cry button for audio preview.

### Pokedex

National and Regional Dex editors. Add, remove, and reorder entries. Each entry shows a detail panel with classification, height/weight, description, and a size comparison preview (Pokemon sprite overlaid on trainer sprite). Play Cry button.

### Items

Searchable item list with a detail editor for each entry: constant name, display name, price, pocket type, item type, hold effect, field/battle use functions, description, and auto-resolved icon previews. Includes an **editable icon picker** -- change which sprite an item displays by picking from a dropdown of all available icons with thumbnails. Changes are saved to `item_icon_table.h`. An **"Open Icon in Folder"** button opens the current icon's PNG in your OS file manager for easy editing.

### Moves

Searchable and filterable move list. Detail editor includes: display name, power, accuracy, PP, type (color-coded), category (Physical/Special/Status with Gen 3 auto-calculation), target, effect description with per-line character limits (42 chars x 3 lines), and move flags.

### Trainers

Three sub-tabs:

- **Trainers** -- Searchable trainer list grouped by trainer class. Detail editor includes: class, name, trainer pic (with visual preview), encounter music, AI flags, party type, and a full party editor with per-member level, species, held item, moves, and ability. VS Seeker rematch tier support with dynamic tier labels.
- **Trainer Classes** -- Searchable class list with sprite thumbnails. Edit class display name (12-character limit), prize money multiplier, and default sprite (dropdown with thumbnails of all trainer pics). Create new classes with a button that writes to three files. View battle info, encounter music, facility class mappings, and usage counts.
- **Graphics** -- Trainer sprite preview (128x160), editable 16-color palette swatch row, Import Palette from PNG (extracts color table from indexed PNG, GBA-clamps to 15-bit), Open Palettes Folder button.

### Starters

Configure the three starter Pokemon. Each slot has: species, level, held item, custom move (optional), and ability selection.

### Credits

Visual credits editor. Edit the scrolling end credits text with line-by-line character limits and color coding.

### Overworld GFX

Sprite-first overworld editor:

- **Left panel** -- Category filter, search bar, scrollable thumbnail grid of all sprites, "+ Add New Sprite..." button, Dynamic OW Palettes (DOWP) status/enable button
- **Right panel** -- Sprite sheet view with animation-type-aware preview (walk cycles, surf, fishing, VS Seeker, inanimate, destroy sequences), palette editor with "Assign to" dropdown for palette reassignment, Import from PNG, "Show in Folder"
- **Add New Sprite** -- Dialog auto-detects frame size/name/palette from PNG, writes all 6 C headers automatically, pushes new constant to EVENTide immediately
- **DOWP patch** -- One-click patch to enable per-sprite palettes (patches 5 C source files)

### Abilities

Searchable ability browser with detail panel:

- Display name (12-char limit with counter), constant name (read-only, renamed via Rename button), description (52-char limit with overflow highlighting)
- **Visual Battle Effect Editor** -- Pick a category (Status Immunity, Contact Status, Type Absorb, Weather, Stat Boost, Intimidate, Contact Recoil, Pinch Type Boost, Type Immunity, Weather Recovery, Type Trap, Crit Prevention) and configure parameters. Shows live C code preview. Writes real C code to the correct source files on save.
- **Visual Field Effect Editor** -- Pick a category (Encounter Rate, Type Encounters, Pickup, Guaranteed Escape, Faster Hatching, Nature Sync, Gender Attract) and configure parameters.
- Species usage table with double-click cross-navigation to Pokemon tab
- Add, duplicate, rename, and delete abilities

### Sound Editor

Full GBA M4A sound engine built in Python. Four sub-tabs:

- **Songs** -- Browse, filter, and play all songs. Right-click context menu: Rename, Replace with .s File, Export .s File, Delete. Import MIDI and Import .s buttons.
- **Instruments** -- 144 unique instruments grouped by type (Samples, Square Waves, Prog. Waves, Noise, Keysplits). Editable ADSR, base key, pan, duty cycle. 3-octave piano keyboard preview. Sample management: export/import WAV, replace, delete with reference checking.
- **Voicegroups** -- Browse all voicegroups with slot counts and song usage. Full 128-slot editor. Add, clone, delete. Generate GM button creates a General MIDI voicegroup mapped to real instruments.
- **Piano Roll** -- Full note editor with real-time sequencer playback. Click to place notes, drag to move/resize, box selection, copy/paste. Track sidebar with volume/pan/mute/solo per track. Song Structure panel showing sections, loops, and patterns. Snap grid (1/4, 1/8, 1/16, 1/32, free). Save writes .s file directly.

**MIDI Import Wizard** -- 5-page flow: file picker with track preview, voicegroup + settings, per-track instrument mapping (GM to VG slot with auto-match), song structure sequencer (define sections, arrange play order, set loop point), conversion + registration.

### Event Editor

RMXP-style visual script editor. Key features:
- All event types (NPCs, triggers, signs, hidden items, map scripts) with numbered condition pages
- Hidden item editor -- dedicated property panel for data-only hidden items (no script needed)
- RMXP-style color scheme (customizable), conditions box, Set Move Route editor
- Position overrides from OnTransition scripts, cross-reference links
- Script Lookup (Ctrl+Shift+F) -- project-wide search across 5,300+ labels
- 84+ command widgets, drag-to-reorder, right-click context menu, Go To navigation
- Go To button in command edit dialogs -- double-click a call/goto/conditional and navigate directly to the target script
- Plain English display names for all constants (flags, vars, weather, sounds, fade types)
- Move Camera cutscene tool -- pan, fade, shake, weather, sound, timing in one dialog
- Sound preview buttons on playbgm/playse/playfanfare commands (plays in background without switching tabs)

### Maps

Map renaming, group management, section renaming, move/delete maps, warp validation.

### Layouts & Tilesets

Layout renaming/deletion, orphan cleanup, tileset reassignment, secondary tileset renaming.

### Region Map

Visual region map editor with actual tileset graphics as background. Section assignment, region clone/rename/delete. Supports all 4 FireRed regions (Kanto + 3 Sevii). Dual layer (Map + Dungeon).

### UI (Text Content)

Three sub-tabs for editing in-game text:

- **Name Pools** -- Player and rival name suggestions from `new_game_intro.inc`
- **Location Names** -- Region map section names
- **Key Strings** -- Miscellaneous game strings from `src/strings.c`

### Config

Edit build configuration (`config.mk`) and game defines (`include/config.h`). Makefile variables and C preprocessor `#define` values are organized into collapsible section cards with toggle support.

---

## Menus

### File

| Action | Shortcut |
|--------|----------|
| Open Project | Ctrl+O |
| Recent Projects | -- |
| Save (with confirmation) | Ctrl+S |

### Edit

| Action | Description |
|--------|-------------|
| Name Decapitalizer | Batch-convert ALL-CAPS names to Smart Title Case across 7 categories (species, moves, items, trainers, trainer classes, abilities, UI strings). Preview table, editable skip-list, per-row control. |

### Project

| Action | Shortcut |
|--------|----------|
| Export to Patch (.bps) | Ctrl+Shift+E |
| Make (Build ROM) | Ctrl+M |
| Make Modern | Ctrl+Shift+M |
| Play | F9 |

### Tools

| Action | Shortcut |
|--------|----------|
| Install Porymap | -- |
| Open in Porymap | Ctrl+F7 |
| Sound Editor | F8 |
| Open Terminal | Ctrl+T |
| Rename Species | -- |
| Open Crashlogs Folder | -- |
| Settings | -- |

### Git

| Action | Shortcut |
|--------|----------|
| Git Panel | Ctrl+Shift+G |
| Pull from Upstream | Ctrl+Shift+L |
| Push to Origin | Ctrl+Shift+U |
| Commit | Ctrl+Shift+K |

All git push and pull operations show a confirmation dialog warning about data that will be overwritten. These cannot be suppressed.

---

## Settings

Accessible from Tools > Settings:

- **General** -- Project display name
- **Advanced Diagnostics** -- Verbose internal logging for types/gender parsing (off by default)
- **Notifications** -- Re-enable previously suppressed dialogs
- **Build Environment** -- Open the Setup Wizard to install/verify build tools
- **Event Colors** -- Customize colors for constant types and command categories. Changes apply immediately.
- **Event Editor Tooltips** -- Toggle descriptive hover tooltips on/off (on by default)
- **Sound** -- Preview volume, loop count, auto-downsample rate, stereo/mono output mode

---

## Porymap Integration

PorySuite-Z integrates [Porymap](https://github.com/huderlem/porymap) as a companion visual map editor. Edit tiles and place events in Porymap, edit scripts and data in PorySuite-Z -- the two apps communicate bidirectionally.

### Setup

1. Go to **Tools > Install Porymap**
2. The installer clones the Porymap source, downloads the Qt SDK, applies PorySuite-Z patches, compiles, and deploys -- all automatically
3. Progress shows which file is being compiled so it doesn't appear hung

### Usage

- **Open in Porymap** (Ctrl+F7) -- opens Porymap to whatever map you're editing in PorySuite-Z
- If Porymap is already running, it switches to the requested map instead of opening a second window
- Clicking an event in Porymap updates PorySuite-Z's Event Editor to show that event's script
- Maps/Layouts tabs have right-click "Open in Porymap" context menus
- Shared file watchers detect when Porymap saves a map and offer to reload in PorySuite-Z

### What gets patched

The installer adds event callbacks, a bridge API, and CLI argument handling to Porymap's scripting engine via `porymap_patches/apply_patches.py`. This is a search-and-replace patcher (not fragile git patches) that survives upstream Porymap updates. The patched binary lives in `porymap/` (not committed to git -- built locally).

---

## How Edits Are Saved

PorySuite-Z reads from and writes back to the original pokefirered source files:

- File > Save shows a confirmation dialog before writing
- Edits modify only the relevant fields in existing file structures (`.field = value` blocks, enum entries, etc.)
- Whitespace, comments, field order, and formatting are preserved
- If a required source file is missing or the layout is ambiguous, the save aborts with an error and no files are changed
- Piano roll saves write the .s assembly file directly (not deferred to File > Save)
- Sound editor changes (voicegroups, song table) are written through the File > Save pipeline

---

## Build

Build your ROM directly from PorySuite:

- **Project > Make** (Ctrl+M) -- Standard build
- **Project > Make Modern** (Ctrl+Shift+M) -- Modern build variant
- **Project > Export to Patch** (Ctrl+Shift+E) -- Generate a `.bps` patch file
- **Project > Play** (F9) -- Launch the built ROM

Build output streams in real time in-app.

---

## License

Original [PorySuite](https://github.com/jschoeny/PorySuite) by jschoeny.

[Porymap](https://github.com/huderlem/porymap) by huderlem is a separate project and is **not included** in this repository. PorySuite-Z's optional installer clones and builds Porymap from its own GitHub repo on your machine. We do not distribute Porymap or any of its code.
