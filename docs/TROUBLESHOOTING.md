# Troubleshooting PorySuite-Z

This guide covers common setup issues and how to reset the application if something goes wrong. Written in plain English.

## Unified Editor (Phase 1+2) Known Issues

### The toolbar icons are colored squares
That's expected — they're placeholders. Replace them with your own 32x32 PNG files in `res/icons/toolbar/`. The filenames are: save.png, make.png, make_modern.png, pokemon.png, pokedex.png, moves.png, items.png, trainers.png, starters.png, events.png, maps.png, layouts.png, regionmap.png, ui.png, config.png, play.png.

### Dynamic Palettes says "Active" but sprites have broken colors
This happens when you replaced the pokefirered folder (e.g. with a fresh copy) after the Dynamic OW Palettes patch was applied. The old marker file (`.porysuite_dowp_enabled`) stayed behind from the previous folder, so PorySuite thought the patch was still applied. **Fixed in 2026-04-06**: PorySuite now checks the actual C source code for DOWP signatures instead of relying on the marker file. If the source is unpatched, the stale marker is automatically removed and the "Enable Dynamic Palettes" button re-enables. Just restart PorySuite after replacing the folder and the button will be available again.

### The trainer Dialogue tab says "No battle dialogue found"
This happens when a trainer has no dialogue in disk `.inc` files, no pending in-RAM dialogue, and no live edits from the Event Editor. Normally you won't see this:
- **New trainers** automatically get default Intro/Defeat/Post-battle text seeded into RAM as a `(Pending — not yet placed on a map)` entry the moment you add them. Default text comes from Settings → Trainer Defaults.
- **Trainers placed on a map via the Event Editor** show up as `(live — unsaved edits)` here as soon as you add the trainerbattle command — no save needed.
- **Trainers placed on a map and saved** read from the map's `text.inc` using labels that contain the trainer's camel-case name (e.g. `PalletTown_Text_HikerFatManIntro`) or match the labels listed on the `trainerbattle` command in scripts.inc.

If you still see "No battle dialogue found," check that the trainer's constant is actually referenced somewhere — use the search (Ctrl+Shift+F) from the Event Editor tab to find `trainerbattle TRAINER_YOUR_CONSTANT`.

### My trainer class name/money/sprite edit disappeared
Fixed (2026-04-05). Previously, switching to another tab and back to the Trainers tab silently re-loaded trainer class data from disk and discarded any pending edits. Now the class editor preserves its dirty state across tab switches. If you still see this happen, save first before switching away, or report it.

### Name Decapitalizer changed species names in the UI but the ROM still showed ALL CAPS

Fixed (2026-04-05). The decapitalizer was updating the in-memory `speciesName` field and the PorySuite tree widget, so the app looked correct — but the compiled ROM still showed ALL CAPS names (BULBASAUR, CHARMANDER, etc.). The root cause was that the game reads species display names from `src/data/text/species_names.h` (`gSpeciesNames[]`), and that file was never written by the decapitalizer. Only the individual Rename tool (`core/refactor_service.py`) ever touched it. Fix: added `_write_species_names_header()` to `ui/name_decapitalizer.py` — on Apply, it patches `species_names.h` directly. Species names now persist to the ROM without needing a separate save step.

### External changes from Porymap wiped my unsaved work
Fixed (2026-04-05). Previously, if Porymap saved a map (or any external tool modified `map.json`/`scripts.inc`) while you had unsaved Event Editor edits, the file watcher silently reloaded from disk and your edits were gone. Now you get a Save / Discard / Cancel dialog when an external change is detected during unsaved edits.

### My external edits to src/credits.c got wiped by PorySuite
Fixed (2026-04-05). The Credits editor now takes a snapshot of `src/credits.c` and `src/strings.c` modification times when you load the tab. If either file is edited outside PorySuite-Z before you hit Save, you get a Save / Discard / Cancel dialog instead of a silent overwrite.

### Ctrl+E in Porymap doesn't bring PorySuite to the foreground
Fixed (2026-04-05). Pressing Ctrl+E (or clicking an event in Porymap) now raises the PorySuite-Z window and logs which event was picked (or "no event within 2 tiles" if the cursor wasn't on an event).

### PorySuite-Z complains about Porymap command files or log errors every half-second
This means the Porymap install is missing the patches PorySuite-Z expects. The installer normally drops a `.psinstalled` marker file next to `porymap.exe`. If that marker is missing, PorySuite-Z treats Porymap as "stock" and avoids commands it can't handle. To fix, re-run Tools → Install Porymap to rebuild with patches.

### An item (or flag/var/trainer) I renamed in PorySuite doesn't show up in the Event Editor dropdowns
Switch to an EVENTide page (Event Editor / Maps / Layouts / Region Map) from any PorySuite page — the constants cache refreshes on that switch. If the rename hasn't been saved to the header files yet, you still need to Save-All first. In-memory-only renames that haven't touched disk require a dedicated bridge that isn't implemented yet.

### Dialogue text shows {PLAY_BGM} and other commands in blue
That's intentional — those are text commands that are part of the game's text system, not script leakage. `{PLAY_BGM}{MUS_ENCOUNTER_GYM_LEADER}` sets the battle music. `{PLAYER}` inserts the player's name. They're highlighted blue to distinguish them from regular text. Right-click in a text box to insert commands from a menu.

### Character counter shows amber/red on dialogue text
The GBA text box can display 36 characters per line. The counter turns amber when you're past 85% (31+ chars) and red when you exceed 36. Characters inside `{COMMANDS}` don't count toward the limit — they compile to binary opcodes, not visible text.

### Play button says "No .gba file found"
You need to build the ROM first. Click Make or Make Modern, or press Ctrl+M / Ctrl+Shift+M. Once the build finishes, the Play button will work. You can change which .gba file it launches in Settings > Build & Play.

### Changes in PorySuite don't show in EVENTide dropdowns
Save first (Ctrl+S). The shared data layer sends a signal to refresh EVENTide's constants after saving. If it still doesn't show, use File > Refresh (F5) to reload everything from disk.

### New overworld sprite doesn't appear in the Event Editor's graphic dropdown
New sprites added via the Overworld Graphics tab push their `OBJ_EVENT_GFX_*` constant directly into the Event Editor's dropdown through cross-tab sync — no save or refresh needed. If the sprite still doesn't appear, switch to any EVENTide page and back to trigger a constants refresh, or use File > Refresh (F5).

### Deleting a song still causes build failures
Fixed (2026-04-07). Previously, deleting a song from PorySuite only removed the `.s` file but left the `.mid` placeholder behind. The Makefile's wildcard found the orphan `.mid`, tried to build from it, and failed. Now `delete_song()` also removes the `.mid` file and any `.o` build artifacts. Additionally, `write_song_table()` was missing the required `dummy_song_header` footer — this could corrupt `song_table.inc` on any write operation. Fixed.

### Piano roll notes longer than 4 beats get cut short
Fixed (2026-04-07). Notes longer than 96 ticks (4 beats at 24 ticks/beat) were silently truncated to N96 — the maximum single-note duration in GBA M4A. Now the song writer generates TIE + EOT commands for long notes, which is exactly what mid2agb does. Notes sustain for their full drawn duration.

### Piano roll Mute/Solo buttons hidden behind scrollbar
Fixed (2026-04-07). The track sidebar was 220px wide, and when the vertical scrollbar appeared (6+ tracks), it covered the M and S buttons on each track row. Sidebar widened to 240px.

### Piano roll save corrupts songs with PATT subroutines (broken structure, missing notes, wrong instruments)
Fixed (2026-04-08). Songs using PATT/PEND subroutine calls (mid2agb's way of compressing repeated sections) were destroyed by the piano roll save — notes went missing, instruments changed, dead code appeared after FINE. Root cause: the save tried to insert flattened notes back into the PATT structure, which is impossible. Fix: PATT/PEND are stripped on save and the track is written as a clean linear sequence. The music content is identical, just represented differently (explicit repetitions instead of subroutine calls).

### Piano roll save halves volume every time you save
Fixed (2026-04-08). Volume dropped from 90 to 63 to 44 on successive saves. The parser evaluated `127*mvl/mxv` to get the byte value (89), but the writer wrote `89*mvl/mxv` — applying the multiplier again. Same issue with TEMPO. Fix: reverse-evaluation helpers recover the original raw value before writing.

### Piano roll BEND effects stop working after the song loops
Fixed (2026-04-08). The piano roll sequencer was resetting pitch bend state to zero every time the playback looped. The real GBA engine doesn't do this — BEND state carries through GOTO loops. Now the piano roll matches the GBA behavior.

### Settings dialog looks different
The settings dialog was rebuilt with a sidebar. All your old settings (diagnostics, notification preferences) are still there under the same INI keys. Autosave and Porymap path settings were removed (they were dead code that never did anything).

---

## Standard FireRed Project Layout

PorySuite expects a FireRed game project to follow the layout used by the official [pokefirered](https://github.com/pret/pokefirered) repository. The most important folders are at the **repository root**:

- `src/`
- `include/`

Before launching PorySuite, ensure your project has generated the real item headers (`src/data/items.h` in current pokefirered, or `src/data/graphics/items.h` in older forks). Run
your build system (e.g. `make`) if neither header exists; the Items tab will log a warning and stay empty until one of them is present.
After editing items, always run `pytest` so the FireRed write-back tests confirm the header and JSON stayed in sync.

If a required canonical source file is missing or unreadable when you try to perform an action (save items/species, write Pokédex text, etc.), PorySuite shows a blocking message and aborts the operation without changing any files. Restore the missing file(s) in your repo first, then retry. This protects downstream `make` builds that expect specific files and formats.

A valid `project.json` or `config.json` file is also required. This file must contain at least `plugin_identifier` and `plugin_version` so PorySuite knows which plugin to load.

## User Data Directory

PorySuite saves its project list and downloaded plugins inside the user data directory. On Windows this is `%LOCALAPPDATA%\PorySuite`, so the project list lives at `%LOCALAPPDATA%\PorySuite\projects.json`. Removing the entire `PorySuite` folder clears the saved project list and any locally installed plugins.

**Steps to clear saved data:**

1. Close PorySuite if it is running.
2. Delete the `PorySuite` folder in your user data directory (for Windows that is `%LOCALAPPDATA%\PorySuite`).
3. Restart PorySuite.

## Rerunning the Setup Wizard

If you want to run the setup wizard again with the FireRed plugin, remove `project.json` or `config.json` from your game project. Launch PorySuite and select the project again. The wizard will appear and you can choose the FireRed plugin.

If you see a dialog stating **"Invalid Project Root"** after opening a project, the selected folder is missing the required `src/` or `include/` directories. Use the **Run Setup Wizard** button in that dialog to pick the correct folder or recreate the project layout.

## Full Reset Checklist

1. Close PorySuite if it is running.
2. Delete the `%LOCALAPPDATA%\PorySuite` directory to clear saved projects and downloaded plugins.
3. In your game project folder delete `project.json` or `config.json`.
4. Delete `src\\data\\*.json` in your game project before reopening to ensure headers are re-parsed.
5. Restart PorySuite and open the project again.
6. The FireRed plugin will recreate any missing `src/data/*.json` files.
7. If the Items tab is still empty, confirm your project actually contains `src/data/items.h` or the legacy `src/data/graphics/items.h`. PorySuite will never synthesize a replacement header; it will log a warning and leave the table empty until one of the real files exists.
`constants.json` now includes type and evolution method data parsed from `pokemon.h`. Delete this file--or choose **Tools > Rebuild Caches**--if method names fail to load. The editor automatically rebuilds the file when its `types` or `evolution_types` sections are missing.
`PokemonConstants` load before evolutions, so type and method lists should
appear as soon as the project opens.

## General Troubleshooting Tips

- Double‑check that you opened the correct folder as the project root.
- Make sure `src/`, `include/` and your `project.json`/`config.json` file all exist.
- Watch the console window for messages about missing files or paths.
- Decline the "Apply C Header Changes" prompt if you want to skip saving; choosing No now leaves headers and JSON untouched so you can keep working without writing to disk.
- If the app reports a missing canonical source file (via a blocking dialog), restore the file in your repo and rerun the action. The editor does not synthesize substitutes and will not proceed with partial data.

Formatting Preservation Guarantees (FireRed)
-------------------------------------------
- Items: Saving only changes values on existing `.field = value` lines in `items.h`/`graphics/items.h`. Whitespace, comments, and blank lines are preserved. New fields/blocks replicate local indentation/commas.
- Learnsets: Saving modifies only the species’ entries in `level_up_learnsets.h`, `level_up_learnset_pointers.h`, `tmhm_learnsets.h`, `tutor_learnsets.h`, and `egg_moves.h`. Comments/spacing remain intact; additions/removals copy local formatting.
- Abilities: `include/constants/abilities.h` is not modified by the editor; `ABILITIES_COUNT` and `#endif` remain as-is.
- If the preview lists a header you do not recognize, verify the file actually exists before saving. The editor now scans alternate FireRed paths and only writes the real file, but missing headers still need to be restored manually.
- The Output panel now mirrors the launcher console, so review it for missing-file warnings or repo root mismatches during saves.

## Header files not found / blank panes

The FireRed plugin expects header files such as
`src/data/pokemon/species_info.h`, `moves.h` and `pokedex_entries.h`.
It uses `util.repo_root()` to locate the project root. If this function
returns the PorySuite folder instead of your game project—something that can
happen after a fresh install—every `open()` call fails quietly. As a result the
Species, Moves and Dex panes appear empty.

1. Open a project and confirm the Species/Moves/Dex panes are empty.
2. Run the provided Python snippet in a console to print `repo_root`:

   ```bash
   python - <<PY
   import plugins.pokefirered.pokemon_data_extractor as pde
   from pathlib import Path
   print("repo_root =", pde.util.repo_root())
   PY
   ```

   If the output path ends with `PorySuitePyQT6` instead of the project folder, the issue is present.
3. After selecting a project the console prints `repo_root confirmed:` or `repo_root mismatch:`.
   Ensure the printed path matches the directory you opened.
4. Choose **Tools > Rebuild Caches** to regenerate the plugin-managed cache files (species, moves, trainers, etc.) without touching authoring files such as `items.json`.
5. Relaunch PorySuite and reopen the project. The panes should populate when the root path is correct.

For the Moves pane specifically, ensure the selected species has entries under
`"species_moves"` in `src/data/moves.json`.
For the Moves pane specifically, FireRed projects now rebuild `species_moves` automatically from the headers when the cache is missing. If the table is still empty after reload, confirm the resolved `repo_root` points at your project and that the header files exist.

If the move dropdowns are empty or TM/HM lists are missing, run **Tools > Rebuild Caches** so moves.json and the header-derived learnsets are regenerated before reopening the project. Automated coverage in `tests/test_cache_tools.py` exercises this path, so a failure after rebuilding usually indicates missing header files or an incorrect project root.
> **Side-effects to watch for**
> 
> - Older extractor code with hard‑coded `"src"`/`"data"` prefixes may create doubled paths after fixing the root.
> - Stale JSON caches can mask whether headers are re-parsed—always clear them before retesting.
> - Extractor errors appear in the same console window after selecting a project, not on initial launch.

- **Plugin version mismatch**: If the UI remains blank after correcting the root path, ensure that `plugin_version` in `project.json` matches the FireRed plugin located under `%LOCALAPPDATA%\PorySuite\plugins`.

## Repo-root Self-Healing

If you accidentally open a nested folder the FireRed plugin climbs up the
directory tree looking for a valid `project.json` or `config.json`. When this
happens the console prints something like:

```
repo_root mismatch: selected C:/games/pokefirered/src resolved C:/games/pokefirered
```

The plugin will continue loading from the resolved path. If headers still do
not appear, delete `src/data/*.json` in your project and reopen it so the caches
are rebuilt from the correct root.

### Sprites fail to load

Check that `src/data/species_graphics.json` exists and that the referenced PNG
files are present in `graphics/pokemon/`. The FireRed plugin derives graphic
constants from the species name when none are stored. Numeric placeholders in
`species.json` are ignored so blank values fall back to this automatic
behaviour. Missing files or a misnamed folder will still result in blank
images. When a PNG cannot be found the console prints something like
``Image file for SPECIES_CONSTANT not found: /full/path/pic.png`` so you know
which file to restore.
If the PNG exists but can't be read the console prints something like
``Failed to load image file /full/path/pic.png for SPECIES_CONSTANT`` so
you know the file is corrupt and needs replacing. When the image loads
successfully, the chosen file URL is printed at debug level so you can
verify which asset is displayed. Image paths are normalized first so
backslashes appear as forward slashes on Windows systems.

All Pokémon data tabs refresh automatically when you pick a different
species in the tree. If the Graphics pane still looks outdated after
editing stats or abilities, switch to another sub-tab and back again to
force a repaint.

### Pokédex description formatting

If saved descriptions in `pokedex_text_fr.h` look misaligned:
- The editor now enforces the per‑line character limit detected from the
  header (usually 42 characters before the `\n` token) and matches the visual
  width to that limit.
- Edits are written using vanilla multi‑line formatting:
  
  `const u8 gXxxPokedexText[] = _(`
  `    "Line 1\n"`
  `    "Line 2\n"`
  `    "Last line");`
- If you still see mismatches, use **Tools > Rebuild Caches** and retry the
  save, then inspect `crashlogs/*.jsonl` for any write errors.

### Regional/National Dex flags are greyed out

The editor shows Dex flags based on `include/constants/pokedex.h` and disables
toggling because the engine uses the NATIONAL_DEX enum for ordering and
`KANTO_DEX_COUNT` for the Regional cut‑off. To change them in‑game, edit
`include/constants/pokedex.h` (reorder NATIONAL_DEX or adjust `KANTO_DEX_COUNT`)
and then use **Tools > Rebuild Caches**.

### HOENN_DEX_* undeclared or Make failures after editing Pokédex

Older editor behavior could overwrite `include/constants/pokedex.h`, removing content
that other files depend on and causing errors like ``'HOENN_DEX_OLD_UNOWN_B' undeclared``
or non‑constant array initializers in `src/pokemon.c`. This has been fixed:

- The editor now patches `pokedex.h` in place, updating only the `NATIONAL_DEX` enum body
  while preserving every other byte (includes, macros such as `KANTO_DEX_COUNT`, comments,
  blank lines). No fabricated `hoenn_dex.h` is created or required.
- If the enum block cannot be uniquely located or the layout is ambiguous, the save aborts
  with a blocking message and leaves all files untouched.

If you still encounter these errors after using an older version of the editor:
- Restore a clean `include/constants/pokedex.h` from your upstream FireRed repository.
- Reopen the project and save again; the in‑place patcher will preserve the file structure.

### Missing graphics for newly added species/items

If Make fails after adding a new species or item due to missing graphics:
- Save your project. The editor detects newly added entries and prompts you to clone assets from an existing template (e.g., copy Squirtle for Piplup, copy Potion for a new item).
- After confirming, the editor copies only image files under `graphics/**` to the correct new paths and shows a summary of created assets so you can edit them manually.
- No headers are created by this step. If you skipped the prompt or canceled it, run Save again and accept the cloning step, or copy assets manually into the expected `graphics/pokemon/<slug>` or `graphics/items/` paths.

### Evolutions not saving

Evolution data is cached in `src/data/evolutions.json`. Changes are written to
this file when it exists, otherwise they fall back to `src/data/species.json`.
Saving now updates `src/data/pokemon/species_info.h` and
`src/data/pokemon/evolution.h` automatically so edits appear in the original
headers without exporting.
If new rows
disappear after reopening the project, confirm the relevant file is writable and
use **Tools > Rebuild Caches** to force regeneration. Changing species in
the editor reloads the list so you can verify the saved data. Method names
    are pulled from `pokemon.h`; delete `constants.json` or choose **Tools > Rebuild Caches** if they do not appear. The editor now recreates the file automatically when its `types` or `evolution_types` sections are missing.
Editing a row no longer replaces the stored method constant so subsequent selections show the correct entry.

If `evolutions.json` is missing it will be regenerated by parsing
`src/data/pokemon/evolution.h` on startup so evolutions remain editable.

If Snorlax still shows `CHESTO_BERRY` as a held item, clear caches and rebuild
them so `species.json` picks up the new `ITEM_LEFTOVERS` values.

If single‑type Pokémon like Pikachu display the wrong second type, remove
`src/data/species.json` and choose **Tools > Rebuild Caches**. The FireRed
plugin will regenerate the file from `species_info.h` so the second slot uses
`TYPE_NONE`.

Upstream FireRed ships with `src/data/items.json`, and the extractor reads that
file first. It only parses `src/data/graphics/items.h` when the JSON is missing
or invalid. PorySuite now backs up `src/data/graphics/items.h` and regenerates
it from the JSON when saving. The bundled `pokefirered` reference may omit this
JSON, but vanilla `pokefirered` does not. If the JSON is absent or non-JSON it
is rebuilt from `src/data/graphics/items.h` so held-item dropdowns repopulate
automatically. If the header is missing a warning is logged and the Items tab
remains empty until `src/data/graphics/items.h` is restored.

Deleting `src/data/moves.json` works the same way for the Moves tab, forcing the
plugin to rebuild move data and species learnsets from the C sources.
If `items.json` contains an `"items"` array or is a list, it is converted to a
dictionary automatically on load to avoid crashes.

### Wrong types or items

If Pokémon appear with incorrect types or held items—for example Pikachu shows
as Water-type—`species.json` is stale. Run **Tools > Rebuild Caches** to
regenerate the file and restore the correct data. The species extractor now
compares cached `types` against `species_info.h` and overwrites mismatched or
`TYPE_NONE` entries. Set `PORYSUITE_REBUILD_ON_TYPE_MISMATCH=1` to rebuild
species caches automatically when these mismatches are detected.
If editing a species' type or ability seems to change another species when you
switch selections, make sure you're on the latest version. The FireRed species
extractor now copies default `types` and `abilities` lists so each Pokémon keeps
its own values. Run `pytest` and verify
`test_species_default_lists_are_independent` passes if issues persist.

Gender ratios can become outdated in a similar way. Rebuilding caches or simply
reopening the project updates `species.json` from `species_info.h` so each
Pokémon shows the correct ratio.

### Pokédex description not updating in-game

- The game reads localized Pokédex text from `src/data/pokemon/pokedex_text_fr.h`
  and uses description symbols referenced in
  `src/data/pokemon/pokedex_entries.h`.
- When you edit a species’ category or description in the Info tab and Save,
  PorySuite rewrites `.categoryName` in `pokedex_entries.h` and replaces the
  string for that species’ description symbol in `pokedex_text_fr.h`.
- If changes don’t appear:
  - Confirm both files are writable and part of your project (not the bundled
    read‑only `pokefirered/` reference).
  - Check the log for lines like `Updated pokedex_text_fr.h: gFooPokedexText`.
  - Ensure you didn’t introduce or rely on a non‑existent `pory_text.h`—the
    FireRed base uses `pokedex_text_fr.h`.

## Capturing Console Output

PorySuite records all console output (stdout and stderr) and Qt messages per
session inside the `crashlogs/` folder next to the app. Every run creates:
- `porysuite_YYYYMMDD_HHMMSS.log` (formatted text log)
- `porysuite_YYYYMMDD_HHMMSS.jsonl` (one JSON object per line)

When reporting issues you can share the `.jsonl` file for exact message text
and timestamps—even for non‑fatal warnings that don’t crash the app.

### Trainer edits not appearing

Trainer data is cached in `src/data/trainers.json`. Saving the project
rewrites this file and `src/data/trainers.h`. If changes do not show up,
confirm both files are writable and rebuild caches.

### Trainer AI Flags column shows the same flag for every trainer

`AI_SCRIPT_CHECK_BAD_MOVE` (the first AI flag) is set on every FireRed trainer by default — this is correct game data, not a display bug. To view or edit a trainer's AI flags, **double-click the AI Flags cell**. A checklist dialog appears with all 9 `AI_SCRIPT_*` flags that have real code in pokefirered, each with a plain-English description. Hover over the cell to see a tooltip listing what each active flag does. Flags without engine code (Smart Switching, Roaming, Safari, First Battle) have been removed — they either don't exist in pokefirered or are set by the engine automatically, not through trainer data.

### Renaming a trainer causes build errors (`TRAINER_* undeclared`)

If a trainer constant is renamed inside `trainers.json` without updating `opponents.h` and `trainer_parties.h`, the compiler will report undeclared identifiers. To rename a trainer correctly:

1. Open the **Trainers** tab.
2. **Double-click the Constant column** (leftmost column) of the trainer row.
3. Enter the new constant name and click OK.
4. Choose **File > Save**.

The rename writes to all required files:
- `include/constants/opponents.h` — `#define TRAINER_*`
- `src/data/trainer_parties.h` — `sParty_*` symbol declarations and usages
- `src/data/trainers.h` — struct key and `.party` field
- `src/data/trainers.json` — JSON key

If you have already manually edited `trainers.json` and the build is now broken, restore the mismatched constants in `opponents.h` and `trainer_parties.h` from git, then use the double-click rename flow to perform the rename correctly.

### Building the ROM — MSYS2 environment

Use **Project > Make (Build ROM)** (Ctrl+M) or **Project > Make Modern** (Ctrl+Shift+M) to open an MSYS2 terminal and build the ROM. If the build fails immediately, check:

1. **MSYS2 not installed at `C:\msys64`** — Install from https://www.msys2.org/ (the standalone release; NOT the one inside `C:\devkitPro\msys2`).
2. **Host tools missing** — Run `make tools` inside the MSYS2 MINGW64 shell to compile `preproc`, `gbagfx`, `scaninc`, etc. These must be Windows `.exe` files. If Linux ELF tool binaries exist (no extension) next to the `.exe` files from a prior WSL build, delete the extension-less ones: `rm tools/gbagfx/gbagfx tools/preproc/preproc ...` etc.
3. **devkitARM shims from WSL interop** — If `C:\devkitPro\devkitARM\bin\arm-none-eabi-gcc.exe` is a tiny shell script calling `/mnt/c/...` paths, it was created by WSL interop and will fail from MSYS2. Fix it to call the actual versioned binary: `exec "/c/devkitPro/devkitARM/bin/arm-none-eabi-gcc-15.1.0.exe" "$@"`. Same fix needed for `arm-none-eabi-as.exe` → `arm-none-eabi/bin/as.exe` and `arm-none-eabi-ar.exe` → `arm-none-eabi/bin/ar.exe`.
4. **libpng missing in MSYS2** — Install with `pacman -S mingw-w64-x86_64-libpng` in the MSYS2 shell.

> **WSL vs MSYS2 distinction**: WSL produces Linux ELF binaries that cannot call Windows executables (devkitARM, gbafix). MSYS2 produces Windows PE binaries that work with devkitARM. Always build pokefirered from MSYS2.

### `src/data/battle_moves.h` — missing array declaration

If you see build errors like `expected identifier or '(' before '[' token` at lines in `battle_moves.h`, the outer array declaration is missing. The file must start with:

```c
const struct BattleMove gBattleMoves[MOVES_COUNT] =
{
```

PorySuite's moves writer can regenerate this file without that header. If the file was overwritten, restore it with `git checkout HEAD -- src/data/battle_moves.h` in your pokefirered directory, then resave your move edits. Also ensure `src/pokemon.c` contains `#include "data/battle_moves.h"` near the top — PorySuite's pre-save validation now checks for its absence rather than its presence.

### `src/data/graphics/items.h` — INCBIN data only

`src/data/graphics/items.h` must contain **only** INCBIN graphics lines (one per item). The item struct definitions (`[ITEM_NONE] = { .name = ...}`) belong in `src/data/items.h` which is included by `src/item.c`. If PorySuite appended item struct data to `graphics/items.h`, the file will cause build errors at file scope. Strip everything from the first `[ITEM_` line onward; keep only lines like:

```c
static const u8 sItemIcon_Potion[] = INCBIN_U8("graphics/items/potion/icon.4bpp");
```

Restore from git if needed: `git checkout HEAD -- src/data/graphics/items.h`.

### GCC 15.1.0 `-Wattribute-alias` error in `pokemon.o`

Newer devkitARM toolchains (GCC 15.1.0) treat `-Wattribute-alias` as an error by default. pokefirered's `GetBoxMonData2` alias has a pre-existing type mismatch that triggers this. The pokefirered Makefile now includes a per-file override:

```makefile
$(C_BUILDDIR)/pokemon.o: CFLAGS += -Wno-attribute-alias
```

If this line is missing from your Makefile, add it after the `$(C_BUILDDIR)/%.o` rule block.

### Rename Species — what gets updated

When you rename a species via **Tools > Rename Species…**, File > Save applies
changes to the following locations:

**Name / Pokédex files (shown in the preview dialog):**
- `src/data/text/species_names.h` — display name and constant
- `src/data/pokemon/pokedex_entries.h` — species block header and `.description` symbol
- `src/data/pokemon/pokedex_text_fr.h` — description symbol definition

**JSON cache files:**
- `src/data/species.json` — top-level species key, `speciesName`, and form names
- `src/data/pokedex.json` — `species` and `dex_constant` entries
- `src/data/evolutions.json` — top-level species key and any `targetSpecies` references
- `src/data/moves.json` — species key inside `species_moves`
- `data/species_graphics.json` — graphic symbol keys (e.g. `gMonFrontPic_*`) and path strings
- `data/starters.json` — starter `species` field if the renamed Pokémon is a starter
- `data/pokedex.json` — `NATIONAL_DEX_*` entry in the national dex list

**Source token sweep (across all `.c`/`.h` in `src/` and `include/`):**
- `SPECIES_*`, `NATIONAL_DEX_*`, `gMonFrontPic_*`, `gMonBackPic_*`, `gMonIcon_*`,
  `gMonFootprint_*`, `gMon*PokedexText`, `CRY_*`, `graphics/pokemon/<slug>`,
  CamelCase and UPPER variants of the name

The confirmation dialog shows the count of name/Pokédex/cache changes; the
source sweep is additional and logged to the crash log after Save completes.

**NOT swept — must be updated manually:**

The rename sweep does **not** touch `data/maps/**/*.inc` or `data/scripts/**/*.inc` map-event script files. After renaming a starter species, search those files for and update:

| Pattern | Example file |
|---|---|
| `SPECIES_<OLD>` | `data/maps/PalletTown_ProfessorOaksLab/scripts.inc` |
| `FLAG_HIDE_<OLD>_BALL` | `data/maps/PalletTown_ProfessorOaksLab/events.inc` |
| `TRAINER_RIVAL_*_<OLD>` | `data/maps/Route22/scripts.inc`, `data/maps/SSAnne_2F_Corridor/scripts.inc`, etc. |
| `TRAINER_CHAMPION_FIRST_<OLD>` | `data/maps/PokemonLeague_ChampionsRoom/scripts.inc` |
| `TRAINER_CHAMPION_REMATCH_<OLD>` | `data/maps/PokemonLeague_ChampionsRoom/scripts.inc` |
| `cleartrainerflag TRAINER_*_<OLD>` | `data/scripts/hall_of_fame.inc` |
| `sParty_*<Old>` / `sParty_*<OLD>` | `src/data/trainer_parties.h` (already swept), `src/data/trainers.h` (already swept) |

Missed references in `.inc` files will cause linker errors (`undefined reference to TRAINER_CHAMPION_FIRST_BULBASAUR` etc.) when you try to build.


### Scroll wheel changing values without clicking

All combo boxes and spin boxes across the app are protected by a scroll guard.
You must click a dropdown or number field before the scroll wheel will change
its value. If you still see accidental changes from scrolling, the widget may
have been created dynamically after the guard was installed — report the
specific tab and field.

### Move rename — changing only capitalization

Renaming "POUND" to "Pound" changes the display name but keeps the same
constant (MOVE_POUND). This is supported — the rename dialog detects that
the display name changed and updates it in memory and JSON even when the
constant stays the same. If the list doesn't update after a display-name-only
rename, save and refresh (F5).

### Move effect field shows an unknown constant

The Effect field in the Moves tab is a standard dropdown listing all known
effect constants. If a move in your data uses an effect that is not in the
built-in list, the dropdown adds it automatically so nothing is lost. That
extra entry may not have engine code backing it — check your source files
if you see an unfamiliar constant.

### New move doesn't appear in-game after adding

When you add a move via Add Move or Duplicate Move, PorySuite must write five
separate files on Save for the move to exist in the ROM. If any file is missing
or unwritable, the save will warn you. Check that all five files exist in your
project: `include/constants/moves.h`, `src/data/battle_moves.h`,
`src/data/text/move_names.h`, `src/move_descriptions.c`, and
`data/battle_anim_scripts.s`. If the animation file is missing, the move will
compile but crash when used in battle (no animation entry).

### Effects vs animations — what's the difference?

The **Effect** field (EFFECT_HIT, EFFECT_BURN_HIT, etc.) controls what the move
does mechanically in battle — damage, status conditions, stat changes. The
**Animation** field controls what plays visually on screen when the move is used.
They are completely independent systems. You can have a fire-type move that uses
EFFECT_BURN_HIT (chance to burn) but plays the Pound animation, or vice versa.
When duplicating a move, both the effect and animation are copied from the source.

### Move display name is too long

FireRed's MOVE_NAME_LENGTH is 12 characters. If you enter a longer name in the
Add Move or Duplicate Move dialog, it will be truncated to 12 characters when
written to `move_names.h`. The dialog enforces this limit.

## Porymap Integration

### Install Porymap appears to hang during compile

The first install (or any install after the source is reset) does a full compile of ~200 C++ files, which takes several minutes. The progress dialog now shows which file is being compiled and a running count. If it still appears stuck for more than 10 minutes, check `aqtinstall.log` in the porysuite folder for download errors, or `crashlogs/` for build failures.

### Porymap opens but shows the wrong map (Battle Colosseum 2P)

This was fixed. The root cause was a Windows backslash vs Qt forward-slash mismatch — CLI args use `C:\GBA\...` but Qt normalizes paths to `C:/GBA/...`, causing `ParseUtil::pathWithRoot()` to double-prepend the project path. All map.json files became unfindable, so Porymap fell back to the first alphabetical map.

If this happens again after a reinstall:
1. Go to Tools > Install Porymap to rebuild (the patch is now correct)
2. Close and restart both PorySuite and Porymap
3. Click "Open in Porymap" — it should open to the map you're editing

### "Open in Porymap" does nothing

Check that Porymap is installed (Tools > Install Porymap). The button is greyed out with a tooltip if Porymap isn't found. If the button is active but nothing happens, check the Output panel for error messages from the launcher.

### Porymap opens a second window instead of switching maps

The launcher detects running Porymap windows by title. If the window title doesn't contain "porymap" (case-insensitive), detection fails. This can happen if Porymap is minimized to the system tray. Bring Porymap to the foreground manually and try again, or close the extra window.

### Map switching feels slow or bounces back to a previous map

This was caused by two issues (both fixed):
1. **Lag**: The auto-sync feature was checking if Porymap was running by spawning a `tasklist.exe` subprocess on every map switch, which blocked the UI for ~4 seconds. Now it just writes a small command file (near-instant).
2. **Echo loop**: PorySuite sends "switch to map X" → Porymap switches → Porymap tells PorySuite "I'm now on map X" → PorySuite sends "switch to map X" again. Fixed with a dual dedup system (flag + last-map tracking) that breaks the loop.

If you still see bouncing, check that you're running the latest code — older versions don't have the anti-echo logic.

### Porymap loses patches after reinstall

This is expected. The Install Porymap flow runs `git reset --hard` to ensure a clean source tree, then re-applies all patches via `apply_patches.py`. If a patch fails to apply (check the Output panel for errors), the binary will be missing features like `openMap` or `readCommandFile`. Report the specific error — it usually means an upstream Porymap change moved the anchor string the patcher looks for.

### EVENTide: Script changes not saving (shared sub-label overwrite)
Fixed (2026-04-06). When multiple events share a script sub-label via `goto` (for example, three trigger events that all jump to the same `RivalBattle` label), editing one event's copy of that shared label and saving could lose the edit. The save loop processed events in order, and the last event's stale copy of the shared label overwrote the user's edit. Fixed: the currently-selected event is now processed last during save, so its edits always win.

### EVENTide: "Value for 'local_id' cannot be empty" build error
Fixed (2026-04-06). Switching between events in the editor could inject an empty `"local_id": ""` field into objects that originally had no `local_id` (like the three unnamed lab assistants in Oak's Lab). The build tool rejects empty values for this field. Fixed: the editor only writes `local_id` when the field has actual text. If your map.json already has bogus empty `local_id` fields, remove them manually or re-save the map in PorySuite-Z.

### EVENTide: "Missing value for required parameter 'song' of macro 'savebgm'" build error
Fixed (2026-04-06). The `savebgm` command widget was outputting just `savebgm` with no song argument. The GBA macro requires `savebgm MUS_SOMETHING`. Fixed: the widget now has a song picker dropdown and includes the selected constant in its output.

### EVENTide: "healplayerteam" assembler error
Fixed (2026-04-06). The Heal Player Team command widget was emitting `healplayerteam` which isn't a real GBA assembly macro. The correct output is `special HealPlayerParty`. Fixed in the widget's output.

### Voice groups save causes "undefined reference to gCryTable" linker error
Fixed (2026-04-06). The Voicegroups Tab's `save_to_disk()` was writing the full `voice_groups.inc` file from scratch, dropping any `.include` lines that existed between voicegroup blocks (like `.include "sound/cry_tables.inc"`). Without that include, the linker couldn't find the cry table symbols. Fixed: the save function now reads the existing file first, records which `.include` lines appear after which voicegroup, and re-inserts them at the correct positions when writing.

---

## Sound Editor

### Songs sound wrong — wrong pitch, missing notes, volume too loud/quiet
Multiple audio accuracy bugs were fixed on 2026-04-06. If you're hearing:
- **Wrong key / pitches off**: The BEND command and MidiKeyToFreq pitch calculation are now GBA-accurate. If a specific song still sounds off, report which song and which instrument sounds wrong.
- **Missing notes or silent sections**: Running status bytes (bare integers that repeat the last control command like VOL) are now parsed correctly. Previously they were silently dropped, causing volume to stay at 0.
- **Some instruments way too loud or quiet compared to in-game**: The volume pipeline was fixed — velocity is now applied once (not twice), the song's master volume constant is no longer double-applied (it's already baked into VOL command values), and CGB instruments (square waves, programmable waves, noise) no longer have arbitrary volume reduction factors.
- **Sounds disappear in mono playback**: Per-note stereo panning now uses GBA's linear crossfade instead of constant-power. No phase cancellation occurs when channels are summed.

### Song playback has a piercing high-pitched sound
Some songs (e.g., mus_victory_trainer) have square wave notes at very high MIDI pitches (96-98). These are present in the original GBA data but may sound harsher in our renderer than on actual hardware due to differences in the output filtering. This is a known limitation.

### Songs sound shifted up by several semitones / shrill high pitch
Fixed (2026-04-06). XCMD instructions in GBA assembly span multiple `.byte` lines. The parser only handled the first line — the continuation line (e.g., `.byte xIECL, 8`) was misinterpreted as a running-status command, creating phantom KEYSH commands that shifted all subsequent notes up by 8-16 semitones. This affected roughly 85 songs. Fixed by recognizing XCMD sub-command tokens (xIECV, xIECL) and skipping them.

### Drum tracks are silent
Fixed (2026-04-06). The GBA stores voicegroups back-to-back in memory. Drum kits using `keysplit_all` index directly by MIDI note number, which can overflow past the target voicegroup's bounds into the next one (e.g., note 40 in a 29-instrument voicegroup reads slot 11 of the next voicegroup). The parser was returning "not found" for overflow indices. Fixed by chaining into subsequent voicegroups.

---

## Potential Improvements

Future versions of PorySuite may offer clearer error messages and an option in the UI to reset the configuration.
