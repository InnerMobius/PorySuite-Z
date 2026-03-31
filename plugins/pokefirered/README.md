# FireRed Plugin

This plugin enables support for the official [pokefirered](https://github.com/pret/pokefirered) repository. It exposes Pokémon species data, graphics and moves in PorySuite.

Select your FireRed repository directly in the launcher. The plugin uses that
repository as-is and expects `src/` and `include/` directories at the repository
root. You do not need to place the repo inside a `pokefirered/` subfolder.

Move descriptions are read from `src/move_descriptions.c`.

Having ``gcc`` or ``clang`` installed lets the plugin preprocess sources for
more reliable parsing. Without either tool the parser falls back to reading the
files directly.

## Generated Data

If `src/data/species.json`, `moves.json`, `items.json`, `pokedex.json`,
`trainers.json` or `starters.json` are missing the plugin will parse the
original source files to recreate them. Species info comes from
`src/data/pokemon/species_info.h`, moves from `src/data/battle_moves.h` and
`src/move_descriptions.c`, items from `src/data/graphics/items.h`, trainers from
`src/data/trainers.h`, the Pokédex from `include/constants/pokedex.h` and the
starter Pokémon from `src/field_specials.c`. Detailed Pokédex entry data is read
from `src/data/pokemon/pokedex_entries.h`. Type and evolution method definitions
are read from `include/constants/pokemon.h` and written to `src/data/constants.json`.
The generated JSON files are saved back into `src/data/` so subsequent launches load
quickly.

After each JSON file is written the plugin prints a message like `Wrote
path/to/file.json` to the console. Seeing these messages confirms that the
headers were parsed successfully and the results were cached.

The Items tab in PorySuite can edit item names, prices and effects. When you
save the project the plugin writes updates back to `items.json` and, upon
exporting C code, rewrites `src/data/graphics/items.h`. Removing
`src/data/items.json` is unnecessary because the extractor always rebuilds it
from `src/data/graphics/items.h`; any `items.json.txt` file is ignored.

The Evolutions tab also supports editing. Evolution data is cached in
`src/data/evolutions.json`. If that file is missing the plugin parses
`src/data/pokemon/evolution.h` to rebuild it. When you save, the updated list is written back
to `evolutions.json` when present, otherwise it falls back to `species.json`.
Selecting another Pokémon reloads its stored evolutions so you always see the
latest data. Method names are pulled from `include/constants/pokemon.h` so
dropdowns show readable labels.

If cached `species.json` entries are missing a `genderRatio` value or use the
wrong one, the plugin reads the header on the next load and fixes the cached
data so gender ratios remain accurate.

## Trainer Editing

Trainer data is stored in `src/data/trainers.json` and written back to
`src/data/trainers.h` when saving.

**AI Flags**: Double-click the AI Flags cell in the Trainers table to open a
checklist of all `AI_SCRIPT_*` constants with plain-English descriptions.

**Renaming a trainer constant**: Double-click the Constant cell (leftmost column)
in the Trainers table. Enter the new name and click OK. On File > Save the
plugin renames the constant in:

- `include/constants/opponents.h`
- `src/data/trainer_parties.h` (including the derived `sParty_*` symbol)
- `src/data/trainers.h`
- `src/data/trainers.json`
- All other `.c`/`.h` references under `src/` and `include/`

The party symbol is derived automatically (e.g.
`TRAINER_RIVAL_OAKS_LAB_BULBASAUR` → `sParty_RivalOaksLabBulbasaur`) so you
only need to supply the new trainer constant name.

## Learnset Editing

The Tutor sub-tab under Pokémon > Moves uses the same Add/Remove table pattern
as the Egg Moves tab. Any move can be added via a dropdown — the list is not
limited to pre-discovered tutor moves.

## Moves Tab

The top-level Moves tab includes a live search bar (searches constant, effect,
and type columns), an **Effect** column, a **Priority** column, and sortable
column headers.

## Evolution Tab

Item-based evolution methods (`EVO_ITEM`, `EVO_TRADE_ITEM`) show an item
dropdown for the parameter. Trade and Friendship methods disable the parameter
field automatically.
