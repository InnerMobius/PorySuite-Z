# PorySuite-Z v0.0.6b — Release Notes

**Released:** 2026-04-18  
**Previous release:** v0.0.59b

---

## Highlights

This release is a large infrastructure and editor overhaul covering four major areas: a new process-wide sprite rendering pipeline that keeps palettes in sync across all tabs, a full Overworld Graphics editor, deep Trainers section improvements, and Starters tab expansion. Dozens of bug fixes and dirty-state/F5 reliability improvements are included throughout.

---

## Sprite Rendering Pipeline — Cross-Tab Palette Sync

**Problem:** Editing a sprite palette in the Species, Trainer, or Overworld Graphics tabs never updated the same sprite shown elsewhere in the app (Pokédex cards, Starters preview, Items list, Trainers list, species tree, Info panel). All viewer sites used a flat `QPixmap(path)` that ignored the separate `.pal` file on disk and any unsaved RAM edits.

**Fix:** Two new shared modules enforce a single rendering path for every sprite in the app:

- **`core/sprite_palette_bus.py`** — process-wide singleton that holds RAM-first palette state for all five sprite categories (Pokémon, trainer pic, item icon, icon palette, overworld). Emits `palette_changed(category, key)` on every editor mutation so viewer tabs can invalidate and redraw. Provides `ensure_*` helpers that read from RAM first and fall back to the `.pal` file on disk.
- **`core/sprite_render.py`** — `load_sprite_pixmap(png_path, palette)` is now the only sanctioned way to convert a sprite PNG to a `QPixmap`. Loads as indexed-8 colour, swaps the colour table, preserves slot-0 transparency.

All sprite-showing tabs — Pokédex detail panel, Items list, Trainers list, Trainer Classes editor, species tree, Info panel, Starters preview — subscribe to the bus and invalidate their caches on palette edits. Every palette editor tab (Species Graphics, Trainer Graphics, Overworld Graphics) pushes to the bus on every mutation.

**Result:** Editing a palette in any graphics tab immediately updates every other view of that sprite, without saving and without restarting.

---

## Overworld Graphics Tab — Full Rewrite

The Overworld Graphics tab has been rebuilt to match the feature level of the Species and Trainer Graphics tabs:

- **Palette bus integration:** Reads RAM-first on load; every mutation pushes to the bus so the rest of the app sees the change immediately.
- **DraggablePaletteRow:** Palette swatches now support drag-to-reorder and right-click "Index as Background" (pixel+palette lockstep swap). Import .pal button added.
- **Dirty flags:** Palette group box turns amber on any palette edit; thumbnail cards in the grid get an amber border when their palette tag is dirty; sidebar dot and title bar asterisk light up correctly. All cleared on save.
- **Two-pass save:** Pass 1 writes dirty `.pal` files; pass 2 writes remapped PNG pixel data via indexed PNG export.
- **F5/Refresh reliability:** Three layers of fixes ensure that pressing F5 fully reverts all in-memory palette edits, clears all amber markers, and rebuilds the grid from disk — regardless of whether the cache rebuild encountered errors.

---

## Trainer Graphics Tab — Grid View + Import

- **Card grid:** The old single-pic dropdown was replaced with a scrollable thumbnail grid. Every trainer pic is visible as a card; dirty cards show an amber border and dot overlay; the selected card gets a blue border. A live search filter narrows the grid by name or `TRAINER_PIC_*` constant.
- **Import PNG as Sprite:** New button allows replacing a trainer sprite entirely (pixel data + colour table) with an imported indexed PNG. The import lives in RAM until save; F5 reverts it.
- **QSplitter layout:** The body is now split with a draggable divider instead of fixed widths, so the panel resizes correctly at non-maximized window sizes. Sprite preview enlarged to 192×192.
- **Palette swatch rendering fix:** `DragSwatch` was using `QPalette.setColor()`, which Qt silently overrides when any ancestor widget has an active stylesheet. All three graphics editor tabs were showing all-black swatches. Fixed by switching to `setStyleSheet("background-color: rgb(...)")`.

---

## Trainer Classes — Rename Support

Added a **Rename…** button to the Trainer Classes tab. Uses the shared rename dialog (auto-derives the `TRAINER_CLASS_*` constant from the typed display name). The new `refactor_service.rename_trainer_class()` method drives the rename across `opponents.h`, `trainers.h`, `battle_main.c`, `trainer_class_names.h`, `data/trainers.json`, scripts, and maps. The `FACILITY_CLASS_*` enum is treated as separate and is not touched.

---

## Trainers Tab — Bug Fixes and Improvements

- **Tier persistence:** VS Seeker rematch tier selections now round-trip through save/load.
- **Trainer class pic lookup:** Class-level Trainer Pic combo correctly resolves on load.
- **F5 discard:** F5 on the Trainers tab discards all in-memory changes and reloads from disk, including rematch data.
- **Flag labels:** VS Seeker tier dropdowns show friendly labels from `porysuite_labels.json` where available. The previously-broken live-widget walker was removed and replaced with a clean stateless disk loader.
- **Validation:** Added data-integrity checks; fixed two latent bugs in dirty-flag wiring.
- **Header sprite lookup and scaling:** Trainer header sprites now resolve and scale correctly in list view.

---

## Starters Tab — Shiny Chance + Pokéball

Two new configurable fields per starter:

- **Shiny Chance (0.00–100.00 %):** Generates a `Random() % 10000 < threshold` guard in `CB2_GiveStarter()` that patches the lower 16 bits of the starter personality so the Gen 3 shiny XOR check passes against the player OTID/SID. The upper 16 bits (nature) are preserved. 0 % generates no code. 100 % = always shiny.
- **Pokéball:** Generates `SetMonData(&gPlayerParty[0], MON_DATA_POKEBALL, &starterBall)` with the chosen `ITEM_*` constant. "Game Default" generates no call. Ball names resolve from the project item data so custom ball names display correctly.

Additional Starters tab fixes:

- **Dirty dot fixed:** Editing a starter now lights up the Starters sidebar dot instead of the Pokémon dot.
- **Amber groupbox:** Editing any field in a starter groupbox tints it amber. Clears on save.
- **Species display names:** Starter combos show display names instead of raw constants.
- **Dead Ability row removed:** The ability combo and checkbox were non-functional. Removed from UI, extractor, codegen, and save paths.

---

## Abilities Tab

- **Add Ability dialog — template support:** The Add Ability dialog now includes Battle Effect Template and Field Effect Template pickers. A new ability can be created with a full template at creation time. Templates stay in RAM on OK and only write C on Save.
- **Dirty row on new abilities:** Newly added abilities paint amber in the list like any other unsaved entry.
- **Hail fix:** `weather_switchin` with Hail was silently emitting Rain C code. Fixed. Also fixed: the SnowWarning activation script was not inserting the required `extern` declaration into `include/battle_scripts.h`, causing a compile error.
- **Template audit — 7 build-break fixes:** All ~60 templates in `core/ability_effect_templates.py` were audited. Seven had latent build-breaks (undeclared variables at injection site, wrong local names, unreachable injections, missing braces). All fixed. A new `_remove_marker_block` helper was added to cleanly strip marker-comment-plus-braced-block shapes on remove and re-apply.
- **`egg_hatch_speed` template fix:** The injected block referenced the outer function loop variable `i` before it was declared at the injection point. Rewrote to declare its own variables inside the block.

---

## Pokédex Tab

- **Drag-reorder dirty flag:** Dragging Pokédex entries to reorder them now marks the affected row range amber and lights the sidebar dot and title bar asterisk.
- **Regional dex save:** Regional dex selections are now saved correctly on F5 and project close.

---

## Image Indexer / Pokémon Graphics Tab

- **Right-click "Index as Background":** Right-clicking any swatch offers "Index as Background" — a pixel+palette lockstep swap that moves the target colour to index 0 (transparent slot) and remaps pixels to match.
- **Palette drag:** Dragging swatches in the Image Indexer now matches the Pokémon Graphics tab behaviour (palette-only swap, no pixel remap).
- **Load .pal — three-path dispatcher:** Importing a .pal file now uses a least-lossy strategy: exact match → shift alignment → best-effort channel match.
- **Transparent-colour auto-prompt removed:** The auto-prompt was corrupting sprites by rearranging the palette without updating pixel data. Removed.

---

## Infrastructure and Reliability

- **Version comparator fix:** `parse_version` treated the patch segment as an integer, making `59 > 6` true and causing the updater to report v0.0.59b as newer than 0.0.6b. Fixed to treat the patch as a decimal fraction so `0.0.50b … 0.0.59b → 0.0.6b → 0.0.61b` compares correctly.
- **F5 / Refresh contract:** The required pattern for any tab with in-memory dirty state is now documented in `CLAUDE.md`: stop timers, immediately clear grid with `setParent(None)`, clear in-memory dirty state, reset visual dirty state, guard `_rebuild_grid()` with `_loading = True/False`. Tabs that read their own C sources independently must have an explicit `load()` call in `_refresh_project()`.
- **`_refresh_project()` reliability:** `rebuild_caches()` is wrapped in `try/except` so the cleanup phase — including `_clear_all_dirty_markers()` — always runs on F5 even when cache parsing encounters errors.

---

## Files Changed (Summary)

**New files:**
- `core/sprite_palette_bus.py`
- `core/sprite_render.py`

**Modified files:**
- `core/ability_effect_templates.py`
- `core/app_info.py`
- `core/pokemon_data.py`
- `core/pokemon_data_extractor.py`
- `core/refactor_service.py`
- `core/updater.py`
- `ui/abilities_tab_widget.py`
- `ui/draggable_palette_row.py`
- `ui/graphics_data.py`
- `ui/graphics_tab_widget.py`
- `ui/items_tab_widget.py`
- `ui/mainwindow.py`
- `ui/overworld_graphics_tab.py`
- `ui/pokedex_detail_panel.py`
- `ui/trainer_class_editor.py`
- `ui/trainer_graphics_tab.py`
- `ui/trainers_tab_widget.py`
- `ui/unified_mainwindow.py`
- `CLAUDE.md`
