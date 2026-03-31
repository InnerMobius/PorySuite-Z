# Troubleshooting PorySuite

This guide covers a few common setup issues and how to reset the application if something goes wrong. It is written for newcomers so technical terms are kept to a minimum.

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

## Potential Improvements

Future versions of PorySuite may offer clearer error messages and an option in the UI to reset the configuration.
