# PorySuite-Z

A PyQt6-based graphical editor for **pokefirered** decomp projects. PorySuite-Z lets you edit game data (species, items, moves, trainers, and more) through a visual interface instead of hand-editing source files, then builds the ROM directly.

All edits are written back into the project's canonical `src/` and `include/` files so `make` builds stay stable.

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

## Editor Tabs

PorySuite-Z has seven tabs along the left side of the main window:

### Pokemon

Full species editor with three sub-tabs:

- **Info** -- Species name, Dex number, category, description, types, abilities (including hidden), held items, gender ratio, egg groups/cycles, catch rate, friendship, growth rate, EXP yield, flags (Legendary, Mythical, etc.)
- **Stats** -- Base stats (HP/ATK/DEF/SP.ATK/SP.DEF/SPEED) and EV yields
- **Images** -- Front sprite, back sprite, icon, and footprint editors

Evolution chain editor with species, method, and parameter fields.

### Pokedex

National and Regional Dex editors. Add, remove, and reorder entries. Each entry shows a detail panel with classification, height/weight, description, and a size comparison preview (Pokemon sprite overlaid on trainer sprite).

### Items

Searchable item list with a detail editor for each entry: constant name, display name, price, pocket type, item type, hold effect, field/battle use functions, description, and auto-resolved icon previews.

### Moves

Searchable and filterable move list. Detail editor includes: display name, power, accuracy, PP, type (color-coded), category (Physical/Special/Status with Gen 3 auto-calculation), target, effect description with per-line character limits (42 chars x 3 lines), and move flags.

### Starters

Configure the three starter Pokemon. Each slot has: species, level, held item, custom move (optional), and ability selection.

### Trainers

Searchable trainer list grouped by trainer class. Detail editor includes: class, name, trainer pic (with visual preview), encounter music, AI flags, party type, and a full party editor with per-member level, species, held item, moves, and ability.

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
