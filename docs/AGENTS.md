PorySuite uses only PyQt6.

Install dependencies using `pip install -r requirements.txt`.

There are no runtime build steps.

All Qt resources and UI files are precompiled Python modules.

No dynamic Qt resource/UI conversion is supported.

Do not use or reintroduce `.ui`, `.qrc`, or `pyqt6-tools`.

Always keep this file and the README up to date when the project changes.

Run `pytest` before submitting changes to ensure all tests pass.

Never create new data or header files when saving. Every edit must write back into the project's existing sources (for example `src/data/items.h`, `src/data/items.json`, `pokedex_text_fr.h`). If a required file is missing, rebuild the caches from the canonical headers instead of generating a PorySuite-specific stub.

Do not roll back or delete tracked project files unless a maintainer provides explicit multi-step confirmation for that exact file. Always work forward within the current tree so the team does not lose existing progress.

Format Fidelity & Missing Sources
---------------------------------
- Source of truth: All editor reads must come from the real project sources (the canonical FireRed headers/JSON in `src/` and `include/`). All writes must go back to those same files in their native formats. Do not introduce alternate files, schemas, or formats that could break `make`.
- No format drift: When saving, preserve the existing on-disk format and layout (e.g., the current `items.json` shape; header field structure and indentation). Patch fields in place instead of rewriting files into a different structure.
- Blocking on missing sources: If any required canonical file for the current operation is missing or unreadable, the editor must show a blocking message to the user and abort the action without changing anything. This applies to items as well as species, moves, evolutions, and Pokédex text. Do not auto-generate substitutes or continue with partial data.
- Caches are secondary: Cache files inside `src/data/*.json` may be rebuilt from canonical headers via the Tools actions when missing. This does not override the blocking rule for primary, canonical sources needed for a save/write-back.

Formatting‑Preserving Write‑Back (FireRed)
-----------------------------------------
- Items: In‑place patching for `src/data/items.h` (preferred) or legacy `src/data/graphics/items.h` updates only the right‑hand side of `.field = value` entries. Whitespace, comments, ordering, and blank separation lines are preserved. Adding fields/blocks copies local indentation and comma rules. If a header layout is unrecognized, the write is aborted (no reflow).
- Learnsets: In‑place patchers for `level_up_learnset_pointers.h`, `level_up_learnsets.h`, `tmhm_learnsets.h`, `tutor_learnsets.h`, and `egg_moves.h` modify only the species’ entries. New lines/tokens copy local formatting; comments and spacing are preserved. Ambiguous layouts abort with a blocking message.
- Abilities: For FireRed, write‑back to `include/constants/abilities.h` is disabled. The editor never rewrites this file; `ABILITIES_COUNT`, `#endif`, comments, and blank lines remain byte‑for‑byte intact until an in‑place abilities editor exists.
- Non‑canonical overlays are removed: The plugin never creates `pory_species.h` or any other non‑vanilla headers.
- Pokédex header (`include/constants/pokedex.h`): Now patched in place. Saves update only the body of the `NATIONAL_DEX` enum (values/order) and preserve every other byte of the file (includes, macros such as `KANTO_DEX_COUNT`, comments, blank lines). If the enum block cannot be uniquely located or the layout is ambiguous, the write is aborted with a blocking message (no reflow and no regeneration). No fabricated `hoenn_dex.h` is ever created or referenced.

Asset Creation Workflow (Explicit Prompt)
----------------------------------------
- New species/items may require image assets for Make to succeed. The editor never fabricates headers, but it now offers a guided asset‑clone prompt at manual save time:
  - On detecting newly added species/items in memory, a dialog prompts the user to select an existing template (e.g., copy Squirtle’s graphics for new Piplup; copy Potion’s icon for a new item).
  - The editor clones only the asset files inside the project (species: `graphics/pokemon/<slug>`; items: under `graphics/items/`) and names the destination exactly like vanilla conventions (folder/filenames derived from the new display name/constant).
  - After copying, a summary dialog lists all created files so the user can edit them manually.
- No data or header files are created by this flow. Only images under `graphics/**` are added, and only with explicit user confirmation.

Current Status
--------------
- The editor is hardwired for pokefirered. The plugin system is still present but the direct writers bypass it for all critical save operations.
- Direct header writers handle: species stats (species_info.h), pokedex category (pokedex_entries.h), pokedex description (pokedex_text_fr.h), learnsets (5 header files), items (items.h), move constants (moves.h), move names (move_names.h), move descriptions (move_descriptions.c), and animation table (battle_anim_scripts.s). Trainers and battle move struct data still use the old pipeline's parse_to_c_code.
- New moves can be added or duplicated from the Moves tab. PorySuite writes all five required files on Save so the ROM builds without manual header editing.
- Pokédex write‑back patches `include/constants/pokedex.h` in place (NATIONAL_DEX enum only). If the layout is unrecognized, saves are blocked.
- Asset clone prompts for new species/items are implemented. Images are copied only on explicit confirmation at save time.
- Scroll wheel protection is installed on all combo boxes and spin boxes app-wide.
- Species/move/item/trainer rename all queue through RefactorService and apply on Save.

Edits always remain in memory until the user runs **File > Save** or accepts the save prompt. The Reset buttons only discard unsaved in-memory changes and never fetch data from upstream repositories.



Quick Start for Maintainers

- Read the â€œQuick Resumeâ€ section at the top of `progress.md` first. It tracks the active phase, whatâ€™s done, whatâ€™s next, and exact files touched. This avoids re-reading the full docs and reduces context usage.

The main window exposes extra utilities under the **Project** and **Tools** menus.

**Project menu:**

* **Make (Build ROM)** (Ctrl+M) — opens an MSYS2 MINGW64 terminal in the pokefirered
  directory with devkitARM on PATH and runs `make`. Requires MSYS2 at `C:\msys64`.

* **Make Modern** (Ctrl+Shift+M) — same as above but runs `make modern` for GCC/devkitARM builds.

**Tools menu:**

* **Rename Entity...** uses the FireRed plugin’s `RefactorService` to rename

  constants across source files. **Note:** The sweep covers `src/` and `include/`
  `.c`/`.h` files and JSON caches. It does NOT sweep `data/maps/**/*.inc` or
  `data/scripts/**/*.inc`. After renaming a starter species, manually check those
  files for `SPECIES_<OLD>`, `FLAG_HIDE_<OLD>_BALL`, `TRAINER_RIVAL_*_<OLD>`,
  `TRAINER_CHAMPION_*_<OLD>`, and `cleartrainerflag` references.

* **Rebuild Caches** forces all extractors to regenerate the `src/data/*.json`

  files immediately.

* **Clear Caches on Next Load** deletes those JSON files so they are rebuilt

  when the project is next opened.

* **Open Crashlogs Folder** opens the per-session `crashlogs/` directory to view

  the human-readable `.log` and machine-readable `.jsonl` files for debugging.

Plugins load from `platformdirs.user_data_path(APP_NAME, AUTHOR)/plugins` first.

If no plugins exist there, the bundled `plugins/` directory is used.

Passing the `debug` argument forces loading only from the local folder.

The `pokefirered/` directory bundled with this repo is a read-only reference

for the FireRed plugin. It may be removed in future releases, so projects should

use their own copy at the project root.

PokÃ©dex descriptions come from `src/data/pokemon/pokedex_text_fr.h` (and related

localized files). When you edit a speciesâ€™ category or description in the UI

and Save, the FireRed plugin writes those changes back to

`pokedex_entries.h` and `pokedex_text_fr.h`. Do not introduce any

`pory_text.h` file â€” it is not part of vanilla pokefirered.

The editor enforces the perâ€‘line character limit (autoâ€‘detected from

`pokedex_text_fr.h`, typically 42) and sizes the input box accordingly, so

entered text matches onâ€‘disk formatting exactly.

Note: File > Save saves the current species before any UI refresh to prevent

Info tab description/category edits from being overwritten.
Declining the "Apply C Header Changes" prompt now cancels the save and leaves all project files untouched.
The preview only lists headers that already exist in the project; missing files are skipped instead of being created.
We now scan the project for the canonical learnset headers (level-up/TM/tutor/egg) before saving so the UI never generates alternate paths.
Canonical FireRed targets: `src/data/level_up_learnset_pointers.h`, `src/data/level_up_learnsets.h`, `src/data/tmhm_learnsets.h`, `src/data/tutor_learnsets.h`, and `src/data/egg_moves.h`. Anything else is skipped and logged.
Console stdout/stderr are mirrored into the Output pane so launch logs remain visible in-app.

`get_species_image_path` verifies that image files are readable before

returning a URL. Unreadable PNGs log ``Failed to load image file <path> for

<CONSTANT>`` and yield ``None``.

All console output (stdout/stderr) and Qt messages are captured per session in

`crashlogs/` as both a humanâ€‘readable `.log` and a JSONâ€‘lines `.jsonl` file.

Use these when reporting errors that donâ€™t crash the app.

Items are editable via the Items tab. The extractor loads `src/data/items.json` when it exists and preserves whatever structure that file used (dictionary, list, or an `"items"` wrapper). When the JSON is missing or invalid it now parses the real game headers -- `src/data/items.h` when present, otherwise `src/data/graphics/items.h` -- using a brace-balanced scanner that tolerates nested structs, comments, and multi-line fields. If neither header exists the UI logs a warning and leaves the table empty until the project provides one; the tool never invents placeholder files.

Saving writes the normalized data back into the existing JSON shape and updates the live header in place, so renames, prices, and hold effects propagate to the real sources without creating new files. The automated `parse_to_c_code` path is still failing the write-back tests, so treat this as in-progress and run pytest after editing items.

The Pokemon > Moves sub-tab now lists only the moves the selected species can learn. Add or remove learnset entries with the buttons below the table, choose moves from the dropdown, switch methods (Level/TM/HM/Tutor/Egg), and edit level/TM data with the appropriate widgets.
Use the Reset button to discard unsaved edits for the currently selected species from any Pokemon sub-tab.
The standalone **Moves** main tab still exposes global move definitions (name, power, accuracy, description).

Species learnsets rebuild from the FireRed headers when species_moves is absent in moves.json, so the per-species table populates even on fresh projects.
TM, HM, and tutor entries are now imported alongside level-up data, so the in-app learnset always mirrors the headers after a rebuild.
Saved learnsets are automatically sorted to match the vanilla ordering when you leave the PokÃ©mon tab or rebuild caches.


memory until you save.

Trainers are editable via a table on the Trainers tab. Saving writes `trainers.json` and regenerates `trainers.h` when parsing to C code.

**Trainer AI Flags**: Double-clicking the AI Flags cell (col 5) opens a checklist dialog with all 13 `AI_SCRIPT_*` flags and plain-English descriptions. Hovering shows a tooltip of active flags.

**Trainer rename**: Double-clicking the Constant cell (col 0) opens a rename dialog. On Save, `RefactorService.rename_trainer` sweeps all `.c`/`.h` files under `src/` and `include/`, and also updates `trainers.json`, `opponents.h`, and `trainer_parties.h` (including the derived `sParty_*` symbol). Never manually edit just the JSON key without going through the rename dialog — it will leave `opponents.h` and `trainer_parties.h` out of sync and break the build.

If `items.json` contains an `"items"` array or is a plain list, the FireRed plugin converts it

into a dictionary automatically when loading so older files do not cause crashes.

`constants.json` is rebuilt from `include/constants/pokemon.h` when missing or

incomplete so type and evolution method names always load in the editor. Evolution method

constants are parsed and displayed by name so new rows use the correct values.

Evolution tree items store the method constant in column 1 so editing a row

preserves the underlying value.

`PokemonDataManager` now registers `PokemonConstants` before evolutions so these

definitions are available when combo boxes are populated.

Type combo boxes map constant strings to their indices when populated so

`update_data` can resolve names like `TYPE_NONE` even if the numeric values

change. Missing constants fall back to index `0`.

- Flags (FireRed):

  - No Flip â†’ `species_info.noFlip` (TRUE/FALSE) in `species_info.h`.

  - Genderless â†’ `genderRatio = 255` in `species_info.h`.

  - Egg Group: Undiscovered â†’ `eggGroups = [EGG_GROUP_UNDISCOVERED, ...]`.

  - Starter â†’ `src/data/starters.json` membership.

- Dex flags are readâ€‘only and reflect `include/constants/pokedex.h` (NATIONAL_DEX order and `KANTO_DEX_COUNT`).

- Species extraction now copies default list values for `types` and

  `abilities`, so editing one species no longer alters another when switching

  between them in the main window. Run `pytest` to ensure

  `test_species_default_lists_are_independent` passes.

The Evolutions tab lets you add or delete rows directly in the UI. Evolution

data is cached in `src/data/evolutions.json`. If that file is missing the

FireRed plugin parses `src/data/pokemon/evolution.h` to rebuild it. Saving writes

the current list back to `evolutions.json` when present, otherwise the data

falls back to `species.json`. Changing species reloads its stored evolutions so

you always see the correct rows.

Saving now regenerates both `species_info.h` and `evolution.h` so stats and

evolution edits persist in the game's source files immediately.

EV yield spinboxes in the Stats tab are capped at `0â€“3` to match Gen 3.

- Snorlax now defaults to `ITEM_LEFTOVERS` for both held-item slots after caches are rebuilt.

- Outdated `species.json` can show PokÃ©mon with the wrong types or held items.

  Use **Tools > Rebuild Caches** to regenerate the file and fix the data.

- Species extraction now synchronizes `types` with `species_info.h`, overwriting

  mismatches or `TYPE_NONE` entries. Set the environment variable

  `PORYSUITE_REBUILD_ON_TYPE_MISMATCH=1` to rebuild species caches

  automatically when type mismatches are detected.

With every suggested task, update AGENTS.md, readme, changelog and troubleshooting.md where necessary!

**Build environment notes (Windows):**

- ROM builds require MSYS2 (standalone, at `C:\msys64`) + devkitARM (`C:\devkitPro\devkitARM`).
- WSL interop shims in `devkitARM/bin/` (tiny shell scripts calling `/mnt/c/...` paths) break MSYS2 builds. Replace them with scripts calling `/c/devkitPro/...` MSYS2-style paths.
- Linux ELF host tool binaries from a WSL `make tools` run compete with Windows `.exe` binaries. Delete the extension-less ELF files from `tools/*/` if a matching `.exe` exists.
- `src/data/battle_moves.h` must include the `const struct BattleMove gBattleMoves[MOVES_COUNT] =` outer declaration. If PorySuite regenerates this file, verify the declaration is present or restore from git.
- `src/data/graphics/items.h` must contain only INCBIN graphics lines. Item struct data (`[ITEM_*] = {...}`) belongs in `src/data/items.h` via `src/item.c`.
- GCC 15.1.0 treats `-Wattribute-alias` mismatches as errors. The pokefirered Makefile includes `$(C_BUILDDIR)/pokemon.o: CFLAGS += -Wno-attribute-alias` to suppress the pre-existing mismatch in `pokemon.c`.

ABOVE ALL, THIS IS AN EDITING SUITE FOR POKEMON DISASSEMBLY GBA GAMES. THIS MEANS IT NEEDS TO BE ABLE TO EDIT DATA AS WELL AS DISPLAY IT!



**Codex & Automation Policy**

- **No Reset Without Consent:** Automated agents (including Codex) MUST NOT reset, hardâ€‘reset, or modify tracked repository files without first receiving explicit verbal communication and two separate verbal confirmations from a project maintainer.

- **Distribution / Venv Policy:** This program is intended to be distributed to others for modifying/editing pokefirered. A project virtual environment is a developer convenience and should not be required for end users. `requirements.txt` must be satisfied by explicit installation (for example: `python -m pip install -r requirements.txt`); the launcher should not silently create or force a venv for users.

