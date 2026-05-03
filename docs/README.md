# PorySuite-Z

A unified PyQt6 editor for **pokefirered** decomp projects. Data editing (species, items, moves, trainers, abilities), event/script editing (NPCs, triggers, signs, map scripts), sound editing (songs, instruments, voicegroups, piano roll), overworld sprite editing, and Porymap integration — all in one window with an RPG Maker XP-style toolbar.

All edits are written back into the project's canonical `src/`, `include/`, and `data/` files so `make` builds stay stable.

---

> **AI Disclosure:** The vast majority of this codebase was written with [Claude](https://claude.ai) (Anthropic's AI assistant). The human developer (InnerMobius) directs the architecture, tests all features, and makes final decisions — but Claude writes most of the code. This project originally forked from [jschoeny's PorySuite](https://github.com/jschoeny/PorySuite), but has been almost entirely rewritten — very little of the original code remains. If AI-assisted code is a dealbreaker for you, this isn't your project.

> **This application is in beta.** While functional, it may contain bugs that can corrupt or break your project files. **ALWAYS keep backups of your decomp project** (use git!) and test thoroughly after every editing session. You are responsible for verifying that your project still compiles and behaves correctly. The authors are not responsible for any lost or damaged work.

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

![Launcher](launcher.png)

---

## Editor Pages

PorySuite-Z has 17 toolbar pages accessible from the RPG Maker XP-style icon toolbar:

### Pokemon

Full species editor with three sub-tabs:

- **Info** -- Species name, Dex number, category, description, types, abilities (including hidden), held items, gender ratio, egg groups/cycles, catch rate, friendship, growth rate, EXP yield, flags (Legendary, Mythical, etc.) with front sprite and animated icon preview
- **Stats** -- Base stats (HP/ATK/DEF/SP.ATK/SP.DEF/SPEED) and EV yields
- **Graphics** -- Battle scene preview (front/back sprites over background with shadow), Player Y/Enemy Y/Enemy Altitude, Normal and Shiny palette editors (16-swatch rows with color picker AND **drag-to-reorder** — drop on the leftmost slot to choose which color is transparent; the front/back PNGs are reindexed automatically on save), Import Palette from PNG, **Import .pal File**, Menu Icon with animated preview and palette index selector, footprint preview, Open Graphics Folder

Evolution chain editor with species, method, and parameter fields. Play Cry button for audio preview.

![Pokemon Info](pokemon.png)

![Species Graphics](species%20graphics.png)

### Pokedex

National and Regional Dex editors. Add, remove, and reorder entries. Each entry shows a detail panel with classification, height/weight, description, and a size comparison preview (Pokemon sprite overlaid on trainer sprite). **Wild Encounters card** showing where each species can be found — method type (Grass, Water, Fishing, Rock Smash) with color-coded dots, friendly map names, and level ranges. Data parsed from `wild_encounters.json` with multi-floor merging and fishing rod sub-groups. Play Cry button.

![Pokedex](pokedex.png)

### Moves

Searchable and filterable move list. Detail editor includes: display name, power, accuracy, PP, type (color-coded), category (Physical/Special/Status with Gen 3 auto-calculation), target, effect description with per-line character limits (42 chars x 3 lines), and move flags.

![Moves](moves.png)

### Items

Searchable item list with a detail editor for each entry: constant name, display name, price, pocket type, item type, hold effect, field/battle use functions, description, and auto-resolved icon previews. Includes an **editable icon picker** -- change which sprite an item displays by picking from a dropdown of all available icons with thumbnails. Changes are saved to `item_icon_table.h`. An **"Open Icon in Folder"** button opens the current icon's PNG in your OS file manager for easy editing.

![Items](items.png)

### Abilities

Searchable ability browser with detail panel:

- Display name (12-char limit with counter), constant name (read-only, renamed via Rename button), description (52-char limit with overflow highlighting)
- **Visual Battle Effect Editor** -- Pick a category (Status Immunity, Contact Status, Type Absorb, Weather, Stat Boost, Intimidate, Contact Recoil, Pinch Type Boost, Type Immunity, Weather Recovery, Type Trap, Crit Prevention) and configure parameters. Shows live C code preview. Writes real C code to the correct source files on save.
- **Visual Field Effect Editor** -- Pick a category (Encounter Rate, Type Encounters, Pickup, Guaranteed Escape, Faster Hatching, Nature Sync, Gender Attract) and configure parameters.
- Species usage table with double-click cross-navigation to Pokemon tab
- Add, duplicate, rename, and delete abilities

![Abilities](abilities.png)

### Trainers

Three sub-tabs:

- **Trainers** -- Searchable trainer list grouped by trainer class. Detail editor includes: class, name, trainer pic (with visual preview), encounter music, AI flags, party type, and a full party editor with per-member level, species, held item, moves, and ability. VS Seeker rematch tier support with dynamic tier labels that refresh in-place when you edit a rematch party.
- **Trainer Classes** -- Searchable class list with sprite thumbnails. Edit class display name (12-character limit), prize money multiplier, and default sprite (dropdown with thumbnails of all trainer pics). **Rename...** button writes the new `TRAINER_CLASS_*` constant across source files (opponents.h, trainers.h, battle_main.c, trainer_class_names.h, data/trainers.json, scripts, maps). Create new classes with a button that writes to three files. View battle info, encounter music, facility class mappings, and usage counts. Inline note under the class-level Trainer Pic explains it's scoped to Battle Tower / Trainer Tower / Union Room facility battles only.
- **Graphics** -- Scrollable card grid of every trainer pic (thumbnail + name + `TRAINER_PIC_*` constant) with a live search filter, amber border on unsaved cards, and blue border on the selected card. Right panel has a 192x192 sprite preview, the same drag-to-reorder 16-swatch palette row used on Pokemon Graphics (drop on the leftmost slot to pick the transparent index — the sprite PNG is reindexed automatically on save), **Import PNG as Sprite...** (replaces pixels AND palette), **Import Palette from PNG**, **Import .pal File**, **Save Sprite as PNG**, **Save Palette as .pal**, and **Open Palettes Folder**. The body uses a draggable splitter so the grid and editor can be rebalanced, and both panels stay visible when the window isn't maximized.

![Trainers](trainers.png)

### Starters

Configure the three starter Pokemon. Each slot shows a front sprite preview (updates live when you change species) with type badges, plus: species, level, held item, custom move (optional), **Shiny Chance** (0–100%, generates a personality-based shiny guarantee without touching nature), and **Pokéball** (choose which ball the starter is given in; Game Default leaves the engine's default intact).

![Starters](starters.png)

### Overworld GFX

Sprite-first overworld editor with two sub-tabs:

**NPC Sprites:**
- **Left panel** -- Category filter, search bar, scrollable thumbnail grid of all sprites, "+ Add New Sprite..." button, Dynamic OW Palettes (DOWP) controls
- **Right panel** -- Sprite sheet view with animation-type-aware preview (walk cycles, surf, fishing, VS Seeker, inanimate, destroy sequences), palette editor with "Assign to" dropdown for palette reassignment, Import from PNG, "Show in Folder"
- **Add New Sprite** -- Dialog auto-detects frame size/name/palette from PNG, writes all 6 C headers automatically, pushes new constant to EVENTide immediately
- **DOWP** -- Enable per-sprite palettes (patches 5 C source files). A risk scanner checks for null-palette sprites and warns if the project is near the 16-slot hardware limit before applying. R/G/B tint sliders control the water-reflection palette with a live preview. A red "Disable" button appears when DOWP is active and fully reverses the patch.

**Field Effect Sprites:**
Browse and edit the engine's in-world feedback sprites (exclamation marks, music notes, emoticons, egg hatch, confetti, etc.) from `graphics/field_effects/` and `graphics/misc/`. Same palette-editing toolkit as NPC sprites — drag-to-reorder swatches, Index as Background, import/export. Saving writes a `.pal` file when one exists, or bakes directly into the PNG when it doesn't.

![Overworld GFX](overworld.png)

### Credits

Visual credits editor. Edit the scrolling end credits text with line-by-line character limits and color coding, with a live in-game preview panel.

![Credits](credits.png)

### Sound Editor

Full GBA M4A sound engine built in Python. Four sub-tabs:

> **Note:** The Sound Editor reads `.s` assembly files from `sound/songs/midi/`. These are build artifacts generated by `mid2agb` during compilation -- they don't exist until the project is built at least once. If the song list shows "Build required", run **Project > Make** (Ctrl+M) first, then reopen the Sound Editor.

- **Songs** -- Browse, filter, and play all songs. Tempo, reverb, and master volume are editable directly on the songs list — no need to open the piano roll. Right-click context menu: Rename, Replace with .s File, Export .s File, Delete. Import MIDI and Import .s buttons. Shows "Build required" when `.s` files are missing (e.g. after a fresh git pull).
- **Instruments** -- 144 unique instruments grouped by type (Samples, Square Waves, Prog. Waves, Noise, Keysplits). Editable ADSR with visual curve, base key, pan, duty cycle. 3-octave piano keyboard preview with hold-to-sustain. Sample management: export/import WAV (with rate/size picker), replace, delete with reference checking. Loop toggle and loop point editor with draggable waveform visualization. `.psinst` instrument preset export/import (zip with JSON manifest + sample data).
- **Voicegroups** -- Browse all voicegroups with slot counts and song usage. Full 128-slot editor. Add, clone, delete. Generate GM button creates a General MIDI voicegroup mapped to real instruments (with drum kit support). Friendly label system with auto-label from song usage.
- **Piano Roll** -- Full note editor with real-time GBA-accurate sequencer playback. Double-click to place notes, drag to move/resize, box selection, copy/paste, Ctrl+Z undo. Track sidebar with volume/pan/mute/solo per track. Song Structure panel showing sections, loops, and patterns. Snap grid (1/4, 1/8, 1/16, 1/32, free). Right-click note → Edit Note Properties (BEND/VOL/PAN control events). Scroll wheel = horizontal scroll, Ctrl+wheel = zoom, middle-click drag = zoom. Save writes .s file directly with round-trip fidelity.

**MIDI Import Wizard** -- 5-page flow: file picker with track preview, voicegroup + settings, per-track instrument mapping (GM to VG slot with auto-match and named dropdowns), song structure sequencer (define sections, arrange play order, set loop point with presets), conversion + registration. Handles Type 0 MIDIs (auto-splits to per-channel tracks).

**Import .s File** -- Import songs from other projects. 3-page wizard with voicegroup compatibility check, automatic label rewrite, and registration.

![Sound Editor](sound%20editor.png)

![Piano Roll](piano%20roll.png)

### EVENTide

RMXP-style visual script editor. Key features:
- All event types (NPCs, triggers, signs, hidden items, map scripts) with numbered condition pages
- Hidden item editor -- dedicated property panel for data-only hidden items (no script needed)
- RMXP-style color scheme (customizable via Settings > Event Colors), conditions box, Set Move Route editor with 6 category tabs
- Position overrides from OnTransition scripts, cross-reference links, Set Flag → Page linking
- Script Lookup (Ctrl+Shift+F) -- project-wide search across 5,300+ labels
- 84+ command widgets, drag-to-reorder, right-click context menu, Go To navigation
- Go To button in command edit dialogs -- double-click a call/goto/conditional and navigate directly to the target script
- Plain English display names for all constants (flags, vars, weather, sounds, fade types)
- Move Camera cutscene tool -- pan, slide, screen effects, weather, sound, timing in one 6-tab dialog
- Sound preview buttons on playbgm/playse/playfanfare commands (plays in background without switching tabs), with "Open in Sound Editor" button
- Script templates (NPC, Sign, Map Script, Standard Wrapper, Field Object)
- Comprehensive tooltips on all controls, command dialogs, and command selector palette (toggleable in Settings)

![EVENTide](eventide.png)

![Command Palette](eventidecommands.png)

### Maps

Map and layout management with two sub-tabs:

- **Map Manager** -- Map tree with rename, group management, section renaming, move/delete maps, warp validation
- **Layouts & Tilesets** -- Layout renaming/deletion, orphan cleanup, tileset reassignment, secondary tileset renaming

![Map Manager](map%20manager.png)

![Layout Editor](layout%20editor.png)

### Region Map

Visual region map editor with actual tileset graphics as background. Click a cell, pick a MAPSEC from the strict dropdown, and the assignment stages immediately (cell turns amber until you save). Hover line under the map shows `(x, y)  MAPSEC_NAME` live as the cursor moves.

**Region management — staged in memory, flushed on Ctrl+S:**

- **Create New Region** — fresh blank region (empty MAPSEC grid, blank tilemap). Best for "I want a new dungeon / area to paint from scratch."
- **Clone Region** — copies the source region's tilemap **artwork** under a new name. The MAPSEC grid starts blank — you paint which MAPSECs the new region should own. Two regions can't both claim the same MAPSEC (the engine routes to whichever slot comes later in the enum), so the clone deliberately avoids duplicating the grid.
- **Rename Region** — renames the folder, files, and engine constant. Scans the project for external references to the old `REGIONMAP_<NAME>` constant and warns if the build will fail (so you can update them first).
- **Delete Region** — removes the region everywhere. Same external-reference scan as rename. Refuses to delete the last remaining region. Lists in plain English exactly what'll be removed (layout file, .bin, enum entry, lookup entries, visibility gates) before you confirm.

**Engine codegen** — `src/region_map.c` is rewritten between `// PORYSUITE-REGIONS-START / END` markers on every save with region changes. Eight blocks managed: enum, INCBINs, includes, decompress dispatch, player-region lookup table, player-region detect, get_section dispatch, story-flag visibility gates. First-time edit warning fires before the first staging op so you can back up to git first. Atomic write via temp + rename — no `.bak` files.

**Live name sanitizer** — typing `Hyrule Overworld` becomes `hyrule_overworld` as you type. Reserved C keywords, name collisions, and invalid characters are blocked.

**Open in Tilemap Editor** button jumps to the Tilemap Editor with the current region's `.bin`, `.png`, and `.gbapal` pre-loaded.

![Region Map](regionmap%20editor.png)

### Label Manager

Standalone toolbar page for managing constant labels. Add friendly names and notes to flags, vars, and other constants. Labels stored in `porysuite_labels.json`.

![Label Manager](label%20manager.png)

### Tilesets (Tilemap Editor)

GBA `.bin` tilemap viewer and editor with three sub-tabs:

**Tilemap Editor:**

- **Open any tilemap** from `graphics/` -- auto-discovers matching tile sheet (`.png`) and palettes (`.pal` AND `.gbapal` files). File dialog remembers the last folder you opened from within a session.
- **4bpp and 8bpp support** -- auto-detects color depth from PNG color table size. Title screen logos and other 256-color tilemaps render correctly.
- **Multi-palette 8bpp support (region-map style)** -- when an 8bpp PNG ships alongside a multi-palette `.gbapal` (multiple sub-palettes baked into one 256-color image — like `region_map.gbapal`), the canvas renders in GBA-accurate mode using the per-tile attr palette so what you see matches what the GBA draws. Picking a tile from the picker auto-detects which sub-palette it was baked from and sets the Pal spinner accordingly.
- **Auto-Fix Palettes button** -- bulk-repair pass that scans every tile in the current tilemap and rewrites stored palette bits to match the dominant 16-color range in the tile artwork. Useful after upgrading PorySuite-Z if a tilemap was saved before the multi-palette fix and now renders with wrong colors. One undo step.
- **Rendered preview** with correct palettes, tile flips, and zoom (1-8x) with grid overlay
- **Paint tool** -- click/drag tiles from the tile picker onto the tilemap
- **Eyedropper tool** -- right-click any tile on the canvas to instantly pick it (sets tile index, palette, hflip, vflip). Left-click pick-tool mode also works.
- **Undo/Redo** -- Ctrl+Z / Ctrl+Y (also Ctrl+Shift+Z). A drag-paint counts as ONE undo step regardless of how many cells you swept across. 100-step history.
- **Per-tile controls** -- palette slot, horizontal/vertical flip
- **Tile offset** -- VRAM base address spinner (0-1023) for games that load tile sheets at non-zero offsets
- **Dimension re-wrap** -- changing width auto-recalculates height to keep all tilemap entries (never truncates)
- **Visual palette editor** -- 16 palette slots shown as color swatch rows. **Double-click any color swatch to edit it** with a color picker (GBA 15-bit clamped). Right-click for Import .pal (JASC format), Export .pal, Extract from PNG, Export All. Color-coded slot labels: white = loaded & used, red = needed & missing, grey = loaded & unused
- **Smart palette loading** -- name-matching `.pal` / `.gbapal` files auto-load (e.g. `solarbeam.bin` → `solarbeam.pal`). Falls back to a single multi-palette file in the same dir when no name match exists (the canonical "shared palette across many tilemaps" case, like `region_map.gbapal` for every `<region>.bin`).
- **Open in Folder** (next to Save) reveals the current `.bin` tilemap in your OS file manager. **Open Sheet** (next to the Tile Sheet combo) reveals the currently-selected tile sheet `.png` so you can edit it in an external image editor (GIMP, Aseprite, etc.). When you save the tilemap, the editor broadcasts a `file_saved` signal app-wide, so dependent views (like the Region Map tab) refresh automatically.
- **Palette source toggle** -- "Auto .pal files" (loads from project's palette directory) or "PNG colors" (uses tile sheet's own color table)
- **Save** -- integrated with the app's File > Save pipeline. Tile changes mark the window dirty; saving writes the `.bin` file alongside all other editors.

![Tilemap Editor](tilemap%20editor.png)

**Tile Animation Editor:**

AnimEdit-style tile animation editor covering **all three GBA animation systems** -- 77 animations in vanilla pokefirered, all discovered dynamically from source with no hardcoded names:

- **Navigate by Tileset + Animation Number** -- 68 tilesets parsed from `headers.h`, animated tilesets sorted first. Animations indexed ("0: Flower", "1: Water"). Works on any pokefirered project.
- **All properties editable** -- Speed/Divisor, Start Tile (hex, 0x1A0 matching Porymap), Tile Amount, Phase, Counter Max. Changes follow the app's normal save pipeline (mark dirty, File > Save writes to tileset_anims.c).
- **Palette integration** -- loads all 16 tileset .pal files with GBA 15-bit clamping. Editable color swatches, palette slot selector (00-15), import/export .pal. Palettes are shared with Porymap.
- **Add New Animation** (+) -- creates brand new tileset animation with full C source wiring: INCBIN, frame array, QueueAnimTiles, dispatch, Init, headers.h callback registration.
- **Remove Animation** (-) -- cleanly strips all C source references.
- **Preview controls** -- zoom dropdown (1x through 16x), 16x16 metatile checkbox (keeps 2x2 tile blocks together during wrapping), W/H tile layout controls for wrapping wide animations into a grid. Collapsible Frame Thumbnails and Tile Grid sections that collapse to zero height, giving more room to the preview.
- **Frame Scrubber** -- prev/slider/next for manual stepping through animation frames.
- **Tile Grid** -- current frame decomposed into 8x8 tiles with hex VRAM addresses and base tile display. Toggle between grid and horizontal strip layout.
- **Fixed-panel layout** -- 310px left panel (navigation, properties, palette) + stretching right panel (preview, filmstrip, tile grid). No splitter gap.
- **Animated preview** with speed slider, filmstrip thumbnail strip, info panel.
- **Tileset BG Animations (8)** -- full editing of all properties + frame add/delete/replace.
- **Door Animations (32)** / **Field Effect Animations (37)** -- read-only frame display, open spritesheet in Explorer.

![Tile Animation Editor](tile%20animation%20editor.png)

**GBA Image Indexer:**

Convert any PNG image to GBA-compatible indexed format:

- **Load any PNG** (RGB, RGBA, or already-indexed) -- shows original preview with dimensions, mode, and color count
- **Quantize to 16 or 256 colors** -- 4bpp for sprites/tiles, 8bpp for backgrounds. All output colors clamped to GBA 15-bit BGR555 (multiples of 8)
- **4 quantize modes** -- Balanced (fair to small details, default), Smooth Gradients (preserves subtle shading), Preserve Rare Colors (keeps unique colors even if they cover few pixels), Manual Pick (choose which colors to keep from ~24 candidates with a live preview)
- **Floyd-Steinberg dithering** -- optional, creates smoother gradients. Turn off for pixel-art style. Never forced by any mode
- **Orphan pixel cleanup** -- when dithering is off, a 3×3 majority filter removes scattered single-pixel noise from nearest-color mapping while preserving real edges and detail
- **Drag-and-drop palette reordering** -- drag any swatch to reorder. Drop onto index 0 to set the background/transparent color. All pixel indices remapped automatically
- **Click to edit colors** -- click any swatch to open a color picker (output GBA-clamped)
- **Show Transparent toggle** -- view index 0 as transparent or as its actual color
- **Trim Unused Colors** -- compact 256-color palettes by removing unused and duplicate entries
- **Closest-color remapping** -- load an existing `.pal` file and force the image to use only those exact colors, with optional dithering on remap
- **Convert to Tilemap** -- split the indexed image into 8×8 tiles, deduplicate (including H/V flipped copies), export a `.bin` tilemap + tile sheet PNG + `.pal` file
- **RGBA transparency** -- transparent pixels auto-assigned to index 0
- **Export** -- save indexed PNG, JASC `.pal`, or both to the same folder. Compatible with Porymap, GRIT, and other GBA tools

![Image Indexer](image%20indexer.png)

### Text Editor

Project-wide text browser, editor, and search & replace for all game-visible strings.

- **Tree browser** with 11 collapsible categories: Game UI & Menus, New Game Intro, Location Names, Map Dialogue, Common Scripts, Battle Messages, Teachy TV, Fame Checker, Quest Log, Trainer Class Names, Nature Names
- **Search bar** at the top with match case, whole word, and regex options. Results grouped by category with counts
- **Replace bar** (toggled) -- Replace Selected or Replace All in Results
- **Editor panel** -- GameTextEdit with context-appropriate character limits, file/label header, script cross-references
- **"Open in EVENTide"** button for map dialogue and common script entries -- switches to EVENTide, loads the map, and selects the exact NPC whose script contains that text (searches the full command tree, works regardless of script chain depth)
- **Saved searches** persist across sessions in `porysuite_text_bookmarks.json`. Right-click to rename/delete groups and manage entries
- All parsers are dynamic -- whatever maps, scripts, and text files exist in your project are shown

![Text Editor](text%20editor.png)

### Diagnostics

ROM build diagnostics dashboard. Shows ROM size (progress bars for 16MB and 32MB limits), EWRAM usage (256 KB) and IWRAM usage (32 KB) with color-coded progress bars (green/amber/red), section breakdown (.text, .rodata, .data, .bss from ELF), build type (modern vs legacy), and song/map/species counts. Parses .map and .elf files. Helps catch memory overflows before they become runtime crashes.

![Diagnostics](rom%20diagnostics.png)

### Config

Edit build configuration (`config.mk`) and game defines (`include/config.h`). Makefile variables and C preprocessor `#define` values are organized into collapsible section cards with toggle support.

![Config](config.png)

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

### View

| Action | Description |
|--------|-------------|
| (Editor pages) | Switch to any editor page (same as clicking toolbar icons) |
| Show Log Panel | Toggle the developer log panel at the bottom of the window (hidden by default, persists across sessions) |

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
| Configure Remotes... | -- |

All git push and pull operations show a confirmation dialog warning about data that will be overwritten. These cannot be suppressed.

---

## Settings

Accessible from Tools > Settings:

- **General** -- Project display name
- **Advanced Diagnostics** -- Verbose internal logging for types/gender parsing (off by default)
- **Notifications** -- Re-enable previously suppressed dialogs
- **Build Environment** -- Open the Setup Wizard to install/verify build tools
- **Event Colors** -- Customize colors for constant types and command categories. Changes apply immediately.
- **EVENTide Tooltips** -- Toggle descriptive hover tooltips on/off (on by default)
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
- Clicking an event in Porymap updates PorySuite-Z's EVENTide to show that event's script
- Maps tab has right-click "Open in Porymap" context menus
- Shared file watchers detect when Porymap saves a map and offer to reload in PorySuite-Z

### Version tracking and updates

- **Check for Porymap Updates** (in Tools menu) queries the GitHub Releases API and compares against the installed version
- If Porymap is updated from within Porymap itself (its built-in updater), PorySuite detects that the patched binary was replaced and shows a warning on next project load
- The Tools menu changes to "⚠ Re-patch Porymap..." when patches are detected as missing
- Always update Porymap through PorySuite (Tools → Update Porymap) to keep bridge patches intact

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

## Credits & Acknowledgements

- **Original PorySuite** by [jschoeny](https://github.com/jschoeny/PorySuite) — PorySuite-Z was originally forked from this project, but has been almost entirely rewritten. Very little of the original codebase remains.
- **PorySuite-Z** by [InnerMobius](https://github.com/InnerMobius)
- **Built with Claude** (Anthropic) — the vast majority of the code in this project was written by AI. The human developer directs architecture, tests features, files bugs, and makes all design decisions. Claude writes the code. Every commit in the git history that includes a `Co-Authored-By: Claude` line was AI-assisted.
- [Porymap](https://github.com/huderlem/porymap) by huderlem is a separate project and is **not included** in this repository. PorySuite-Z's optional installer clones and builds Porymap from its own GitHub repo on your machine. We do not distribute Porymap or any of its code.
