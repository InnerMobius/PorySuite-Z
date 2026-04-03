# PorySuite Species Data Flow

Reference doc for how species/pokemon data moves through the system.
Last updated: 2026-04-02

---

## The Big Picture

```
C Headers (species_info.h, pokedex_entries.h, pokedex_text_fr.h)
    ↕  extracted on first load or refresh
JSON Cache (species.json, pokedex.json)
    ↕  loaded into memory
In-Memory Dicts (self.source_data.data["species_data"].data)
    ↕  read/written by UI
Qt Widgets (spinboxes, combos, text fields)
```

Data always flows through these four layers. Problems happen when a layer
gets out of sync with the others.

---

## 1. Where Species Data Lives

### In memory

```
self.source_data                          ← PokemonDataManager
  .data                                   ← dict of data objects
    ["species_data"]                      ← SpeciesData instance
      .data                               ← the actual species dict
        ["SPECIES_BULBASAUR"]
          ["species_info"]                ← stats, name, types, etc.
            ["speciesName"]  = "Bulbasaur"
            ["categoryName"] = "Seed"
            ["description"]  = "A strange seed..."
            ["baseHP"]       = 45
            ["types"]        = ["TYPE_GRASS", "TYPE_POISON"]
            ["abilities"]    = ["ABILITY_OVERGROW", "ABILITY_NONE"]
            ...30+ more fields
          ["pokedex"]                     ← dex-specific data (from pokedex_entries.h)
            ["categoryName"] = "Seed"
            ["descriptionText"] = "A strange seed..."
            ["height"] = 7
            ["weight"] = 69
          ["dex_num"]    = 1
          ["dex_constant"] = "NATIONAL_DEX_BULBASAUR"
          ["name"]       = "Bulbasaur"
          ["forms"]      = {}             ← alternate forms, each with own species_info
    ["pokedex"]                           ← Pokedex instance
      .data
        ["national_dex"]  = [{species, dex_num, categoryName, ...}, ...]
        ["regional_dex"]  = [...]
```

### On disk

| File | What's in it | When written |
|------|-------------|--------------|
| `src/data/species.json` | Full species dict (species_info + pokedex + forms) | On Save, on Refresh |
| `src/data/pokemon/species_info.h` | C struct with stats, types, abilities, name, category, desc | On Save (parse_to_c_code) |
| `src/data/pokemon/pokedex_entries.h` | Dex entries with categoryName, height, weight, desc symbol | On Save (parse_to_c_code) |
| `src/data/pokemon/pokedex_text_fr.h` | Description strings (FireRed version) | On Save (parse_to_c_code) |
| `src/data/pokemon/pokedex_text_lg.h` | Description strings (LeafGreen version) | On Save (parse_to_c_code) |

---

## 2. Load Flow (Opening a Project)

```
load_data(project_info)                              mainwindow.py:2146
  → Create PokemonDataManager                        mainwindow.py:2287
    → For each data class (SpeciesData, Pokedex, ...):
      → SpeciesDataExtractor.extract_data()          pokemon_data_extractor.py:783
        → Try loading species.json (if exists and not stale)
        → If stale or missing: parse C headers → write fresh JSON
        → Mirror pokedex categoryName/description into species_info
      → Result stored in SpeciesData.data
  → Populate tree_pokemon with species list
  → Populate combo boxes (types, abilities, items, etc.)
```

**"Stale" means**: any C header file is newer than the JSON file (by mtime).

---

## 3. Species Switch Flow (Clicking a Species)

```
User clicks "Rocktite" in tree
  → update_tree_pokemon()                            mainwindow.py:3857
    → save_species_data(previous_species)            mainwindow.py:3875
      (captures UI widget values for OLD species into memory)
    → update_data(new_species)                       mainwindow.py:3895
      (populates UI widgets from memory for NEW species)
    → _refresh_pokedex_display(new_species)          mainwindow.py:3897
      (updates dex panel without triggering saves/reloads)
```

**save_species_data does NOT write to disk.** It only updates the in-memory
dict and sets `pending_changes = True` + marks window modified.

---

## 4. Save Flow (Ctrl+S)

```
update_save()                                        mainwindow.py
  → save_species_data(current_species)
    (final capture of UI values before anything else happens)
  → save_items_table() + items_editor.save_icon_changes()
    (flush item edits + write icon changes to item_icon_table.h)
  → _save_trainer_classes()
    (flush trainer class edits → write names/money/sprite headers)
  → Show progress dialog
  → save_data(parse_headers=False)
    → PokemonDataManager.save()
      → Write species.json, pokedex.json, moves.json, trainers.json, items.json, etc.
  → Direct header writers (bypass old plugin pipeline):
    → _write_species_info_header()     species_info.h      (stats, types, abilities, etc.)
    → _write_pokedex_entries_header()   pokedex_entries.h   (category name)
    → _write_pokedex_text_header()      pokedex_text_fr.h   (description text)
    → _write_moves_headers()            5 learnset files     (level-up, TM/HM, tutor, egg)
    → _write_items_header()             items.h              (all item data)
  → New-move writers (only run when moves were added this session):
    → _write_new_move_constants()      moves.h              (#define + MOVES_COUNT bump)
    → _write_new_move_names()          move_names.h         (display name entry)
    → _write_new_move_descriptions()   move_descriptions.c  (const + pointer table entry)
    → _write_new_move_animations()     battle_anim_scripts.s (.4byte in animation table)
  → source_data.parse_to_c_code()      trainers.h, battle_moves.h
  → Apply rename operations (if any)
  → If renames applied: reload from disk
  → Force-reload Trainers and Moves tabs from freshly written data
```

The direct writers read each C header, find the relevant blocks by regex,
patch only the changed fields, and write back with Unix line endings.
They bypass the old plugin pipeline's ReadSourceFile/WriteSourceFile
wrappers which silently fail due to the SOURCE_PREFIX = "source/" path bug.

---

## 5. Refresh Flow (F5)

```
_refresh_project()                                   mainwindow.py:734
  → rebuild_caches()                                 mainwindow.py:7076
    → _clear_plugin_cache_files()                    mainwindow.py:7133
      → Stash categoryName/description/speciesName from species.json
      → DELETE species.json, pokedex.json, etc.
    → source_data.rebuild_caches()
      → For each extractor: re-parse C headers → write fresh JSON
    → load_data(project_info)
      → Repopulate everything from fresh JSON
    → _restore_species_edits()
      → Re-apply stashed values if extractor missed them
```

---

## 6. Every Field in save_species_data

These are all the UI widgets that get read when switching species or saving.
Each one maps to a key in `species_info`.

### Identity
| Widget | species_info key | Save method |
|--------|-----------------|-------------|
| `species_name` (text) | `speciesName` | update_if_needed |
| `species_category` (text) | `categoryName` | _dex_aware_set |
| `species_description` (text) | `description` | _dex_aware_set |

### Base Stats
| Widget | species_info key | Save method |
|--------|-----------------|-------------|
| `base_hp` (spinbox) | `baseHP` | update_if_needed |
| `base_atk` (spinbox) | `baseAttack` | update_if_needed |
| `base_def` (spinbox) | `baseDefense` | update_if_needed |
| `base_speed` (spinbox) | `baseSpeed` | update_if_needed |
| `base_spatk` (spinbox) | `baseSpAttack` | update_if_needed |
| `base_spdef` (spinbox) | `baseSpDefense` | update_if_needed |

### Types & Abilities
| Widget | species_info key | Save method |
|--------|-----------------|-------------|
| `type1` (combo) | `types[0]` | update_if_needed (2-item list) |
| `type2` (combo) | `types[1]` | update_if_needed (2-item list) |
| `ability1` (combo) | `abilities[0]` | Custom (ID conversion) |
| `ability2` (combo) | `abilities[1]` | Custom (ID conversion) |
| `ability_hidden` (combo) | `abilities[2]` | Custom (ID conversion) |

### EV Yields
| Widget | species_info key | Save method |
|--------|-----------------|-------------|
| `evs_hp` (spinbox) | `evYield_HP` | update_if_needed |
| `evs_atk` (spinbox) | `evYield_Attack` | update_if_needed |
| `evs_def` (spinbox) | `evYield_Defense` | update_if_needed |
| `evs_speed` (spinbox) | `evYield_Speed` | update_if_needed |
| `evs_spatk` (spinbox) | `evYield_SpAttack` | update_if_needed |
| `evs_spdef` (spinbox) | `evYield_SpDefense` | update_if_needed |

### Breeding & Growth
| Widget | species_info key | Save method |
|--------|-----------------|-------------|
| `catch_rate` (spinbox) | `catchRate` | update_if_needed |
| `exp_yield` (spinbox) | `expYield` | update_if_needed |
| `gender_ratio` (slider) | `genderRatio` | Custom (% → 0-254 or 255) |
| `held_item_common` (combo) | `itemCommon` | update_if_needed |
| `held_item_rare` (combo) | `itemRare` | update_if_needed |
| `egg_cycles` (spinbox) | `eggCycles` | update_if_needed |
| `egg_group_1` (combo) | `eggGroups[0]` | update_if_needed (2-item list) |
| `egg_group_2` (combo) | `eggGroups[1]` | update_if_needed (2-item list) |
| `exp_growth_rate` (combo) | `growthRate` | update_if_needed |
| `base_friendship` (spinbox) | `friendship` | update_if_needed |

### Flags (from species_flags list widget)
| Flag | species_info key | Effect |
|------|-----------------|--------|
| NO_FLIP | `noFlip` | "TRUE"/"FALSE" |
| GENDERLESS | `genderRatio` | Sets to 255 |
| UNBREEDABLE | `eggGroups` | Sets to ["EGG_GROUP_UNDISCOVERED"] |
| STARTER | `starters` (global) | Add/remove from starters list |
| IN_NATDEX | national_dex (pokedex) | Add/remove dex entry |
| IN_REGDEX | regional_dex (pokedex) | Add/remove dex entry |

### Complex Fields
| Widget | species_info key | Save method |
|--------|-----------------|-------------|
| Evolution tree | `evolutions` | Custom (tree → list of dicts) |
| Learnset table | moves data | save_species_learnset_table() |

---

## 7. How update_if_needed vs _dex_aware_set Work

### update_if_needed (most fields)
```python
def update_if_needed(key, ui_value):
    current = get_species_info(species, key)   # with fallback
    if current != ui_value:
        set_species_info(species, key, ui_value)
        updated = True
```
Simple comparison. Works for stats, types, etc.

### _dex_aware_set (categoryName and description only)
```python
def _dex_aware_set(attr, val):
    fallback = get_species_info(species, attr)  # checks species_info, then pokedex
    raw = species_info.get(attr)                # ONLY species_info, no fallback

    if val != fallback:        → WRITE (user changed it)
    elif raw is None and val:  → WRITE (not stored yet, mirror from pokedex)
    elif raw != val:           → WRITE (stored value outdated)
```
Extra logic needed because categoryName/description can come from the pokedex
fallback even when not in species_info. Without the `raw is None` check, values
that match the pokedex would never get written to species_info, meaning
parse_to_c_code would never write them to headers.

### get_species_info fallback chain
```python
get_species_info(species, "categoryName"):
  1. Try: data[species]["species_info"]["categoryName"]
  2. If missing: look up in pokedex national_dex entries
  3. Return value or ""
```

---

## 8. What the Direct Writers Patch in Headers

### _write_species_info_header() → species_info.h
For each species with in-memory edits, patches the `[SPECIES_XXX] = { ... }` block:
- `.speciesName = _("Name")`
- `.baseHP`, `.baseAttack`, `.baseDefense`, `.baseSpeed`, `.baseSpAttack`, `.baseSpDefense`
- `.types = { TYPE_X, TYPE_Y }`
- `.catchRate`, `.expYield`
- `.evYield_HP` through `.evYield_SpDefense`
- `.itemCommon`, `.itemRare` (item constants)
- `.genderRatio` — converts numeric 0-255 back to C macros (PERCENT_FEMALE, MON_GENDERLESS, etc.)
- `.eggCycles`, `.friendship`
- `.growthRate` (constant)
- `.eggGroups = { GROUP1, GROUP2 }` — duplicates single-element lists for two-slot format
- `.abilities = { ABILITY_X, ABILITY_Y }` — converts numeric IDs back to constants via get_ability_by_id()
- `.safariZoneFleeRate`, `.bodyColor`, `.noFlip`

Walks lines in the header, detects blocks by `[SPECIES_` prefix, accumulates
block lines, patches matching `.field = value` lines, writes back.

### _write_pokedex_entries_header() → pokedex_entries.h
Patches `.categoryName = _("...")` inside `[NATIONAL_DEX_XXX] = { ... }` blocks.
Reads from **species_info** (the authoritative source that save_species_data writes to),
NOT from the natdex list (which can fall out of sync).

### _write_pokedex_text_header() → pokedex_text_fr.h
Replaces `const u8 gXxxPokedexText[] = _(...)` definitions with the
current description text, formatted as multi-line C strings.

### _write_moves_headers() → 5 learnset files
- **level_up_learnsets.h** — `static const struct LevelUpMove Xxx[] = { ... };`
- **level_up_learnset_pointers.h** — pointer table entries
- **tmhm_learnsets.h** — `[SPECIES_XXX] = TMHM_LEARNSET(...)`
- **tutor_learnsets.h** — `[SPECIES_XXX] = TUTOR(...) | TUTOR(...) ...`
- **egg_moves.h** — `egg_moves(SPECIES_XXX, MOVE_A, MOVE_B, ...)`

### _write_items_header() → items.h
Regenerates the full items.h from items.json. Items.h uses a positional
array (no designated initializers) so order must match the constants
in include/constants/items.h exactly.

### _save_trainer_classes() → 3 files
Called before the main save pipeline. Flushes trainer class editor edits and writes:
- `src/data/text/trainer_class_names.h` — display names (gTrainerClassNames)
- `src/battle_main.c` — prize money table (gTrainerMoneyTable)
- `src/data/pokemon/trainer_class_lookups.h` — sprite mappings (gFacilityClassToPicIndex)

### items_editor.save_icon_changes() → item_icon_table.h
Writes pending icon picker changes to `src/data/item_icon_table.h`.
Each entry is `[ITEM_CONST] = {gItemIcon_Symbol, gItemIconPalette_Symbol}`.
Only modified entries are rewritten (regex replacement in-place).

### source_data.parse_to_c_code() (old pipeline, still used for)
- **trainers.h** — trainer struct data
- **battle_moves.h** — move definitions
- **move_names.h** — in-place patching of move display names
- **move_descriptions.c** — in-place patching of move descriptions

**Important**: Plugins whose files are already handled by direct writers
(SpeciesData, PokemonItems, PokemonMoves learnsets) are skipped via the
`_skip_parse_to_c` flag set during update_save. This prevents double-writes
and race conditions from the threaded plugin pipeline.

---

## 9. Known Gotchas

1. **Pokedex fallback hides missing data**: `get_species_info("categoryName")`
   returns a value even if species_info doesn't have it (falls back to pokedex).
   This makes `update_if_needed` think nothing changed.

2. **species_info.h doesn't have all fields in vanilla**: categoryName and
   description are NOT in species_info.h blocks in vanilla pokefirered.
   They live in pokedex_entries.h and pokedex_text files. The extractor
   mirrors them into species_info after extraction.

3. **Two pokedex text files**: pokedex_text_fr.h (FireRed) and
   pokedex_text_lg.h (LeafGreen) both define the same symbols.
   Reader order matters — fr.h is read LAST so it wins.
   Both files must be updated during save.

4. **processEvents during save**: parse_to_c_code runs in threads and
   pumps Qt events while waiting. This can trigger UI callbacks that
   reset widget values. save_species_data is called BEFORE the dialog
   opens to avoid this.

5. **Refresh deletes JSON**: F5 deletes species.json before re-extracting
   from headers. User edits that only exist in JSON (not yet written to
   headers) would be lost. A stash/restore mechanism preserves them.

6. **set_species_info doesn't set pending_changes**: The caller must
   decide whether to mark the data as changed. _dex_aware_set's
   "mirror from pokedex" path intentionally does NOT set updated=True
   to avoid false "unsaved changes" dialogs.

7. **Double-write prevention (2026-04-02)**: Before this fix, both the
   direct header writers AND `parse_to_c_code` wrote to the same files
   (species_info.h, pokedex_entries.h, items.h, learnset headers). The
   plugin pipeline ran in parallel threads AFTER the direct writers,
   potentially overwriting them with stale data. Now the plugins that
   overlap with direct writers are skipped via `_skip_parse_to_c = True`
   set in `update_save()` before calling `parse_to_c_code()`.

8. **Category was reading from wrong source**: Before fix, the category
   writer read from natdex (pokedex national_dex list) instead of
   species_info. The natdex sync from save_species_data was silently
   failing, so edits never reached the file. Now reads directly from
   species_info, the same dict that `set_species_info()` writes to.

9. **Pokedex panel clobber (2026-04-02)**: `_flush_pokedex_panel()` in
   `update_save()` reads category/description from the Pokedex panel's
   widgets and writes them back to species_info. If the user edited on the
   Stats page (not the Pokedex page), the panel widgets still hold the old
   value, overwriting the correct edit. Fix: `save_species_data()` now
   syncs the panel widgets after writing to species_info.

10. **Header mtime vs JSON mtime**: Header files are written AFTER
    species.json during save, making them "newer". On next startup,
    `should_extract()` sees stale JSON and triggers unnecessary
    re-extraction. Fix: JSON files are re-touched after header writes.
