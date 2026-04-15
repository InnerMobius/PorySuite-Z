# PorySuite-Z v0.0.56b

> pokefirered projects only.

This is the first release of the **Post-Release Code Audit** triggered by community feedback on v0.0.55b. Focus is on correctness, dirty-flag honesty, and replacing fragile UI signals with clearer ones.

## What's New in 0.0.56b

### Pokemon Graphics — Drag-to-Reorder Palettes

- The Normal Palette and Shiny Palette rows on the Pokémon Graphics sub-tab are now **drag-to-reorder** in addition to click-to-edit.
- Drop a colour onto the leftmost slot ("BG") to make it the transparent index. The front and back PNGs are reindexed automatically so the image still looks correct, only which colour is treated as transparent changes.
- Normal and Shiny have **independent orderings**. Dragging in the Normal row remaps the PNG and reorders normal.pal; dragging in the Shiny row reorders shiny.pal only.
- Reindexed PNGs are written on next File → Save alongside the .pal files. The battle-scene preview shows the post-reorder result live.
- New **Import .pal File** button next to Import Palette from PNG, sharing the same Normal / Shiny radio selector.

### Reliable Unsaved-Edit Indicators

- Replaced the title-bar `*`-only "modified" signal with **amber row coloring** on the species tree, Pokédex lists, Moves list, and Items list, plus an **8×8 amber dot** composited on the sidebar tab icon for any tab that has unsaved sections.
- Multiple logical sections can share a single sidebar tab — the dot stays lit until **all** of them are clean, and disappears the moment a Save (or Refresh) clears them.
- Added a custom `QStyledItemDelegate` so the amber colour is visible even on lists that override foreground/background via Qt stylesheets.

### F5 / File → Refresh Now Actually Discards Edits

- Refresh used to wipe the title-bar `*` and amber markers but leave the **values** in panel widgets (height, weight, stats, etc.) untouched, which made it easy to think edits had been reverted when they really hadn't.
- Refresh now resets every backing data object from its `original_data` snapshot, blanks the Moves widget's in-memory state, and force-reloads the currently-selected species panel **and** the currently-selected Pokédex entry so the displayed values visibly snap back to disk.
- Programmatic loads no longer fight the user — `_flush_pokedex_panel` short-circuits during refresh-discard and during loading guards, so stale widget values can't overwrite the freshly reset data.

### Dirty-Flag Audit (Phase 1 – Started)

- Wired Species, Pokédex, Moves, and Items editors through the unified `_loading_depth` re-entrant counter. Programmatic load / refresh / populate calls can no longer accidentally mark the project modified.
- Moves tab specifically: `MovesTabWidget.data_changed` now routes through a single `_on_move_edited` slot that respects the loading guard, marks the row amber, and lights the tab dot.
- Pokédex / Abilities tab mapping fix — editing the Pokédex panel used to light the dot on the Pokémon tab. Each section now maps to its own dedicated sidebar button.

### Porymap Bridge Cleanup (Audit Phase 0)

- Removed 3 dead C++ Q_INVOKABLE getters from the porymap patches (`getMapHeader`, `getCurrentTilesets`, `getMapConnections`) — they were never read by the bridge JS.
- Pruned 9 unused JS callback handlers and 5 unused Python bridge signals.
- Added an `_check_anchor()` uniqueness guard to `apply_patches.py` so a fuzzy patch anchor can't silently land in the wrong place.
- The bridge is now 5 callbacks, 5 signals, 2 user actions, 1 command — every line traceable end-to-end to a consumer.

## Bug Fixes

- F5 Refresh no longer leaves edited Pokédex height/weight values stuck on screen.
- Moves list rows now actually colour amber on edit (previously the QSS stylesheet swallowed the foreground colour).
- Loading the Moves tab on a fresh project no longer marks the first move dirty.
- Pokémon sub-tabs (Stats, Evolutions, Learnsets, Graphics) now correctly mark the species dirty on edit and clean on save.
- Title-bar `*` no longer appears spuriously after switching tabs on a clean project.

## Files of Note

- New: `ui/draggable_palette_row.py` — shared `DragSwatch` + `DraggablePaletteRow` widget extracted from the Image Indexer for reuse across editors.
- Updated: `ui/graphics_tab_widget.py`, `ui/mainwindow.py`, `ui/unified_mainwindow.py`, `core/app_info.py` (version bump).

## Known Limitations

- A normal-side palette drag will visibly shift how the **shiny** preview looks until the user reorders shiny too. This is an unavoidable consequence of the engine recolouring normal-indexed PNG pixels at runtime; the only fix would be to lockstep the two pals (rejected — the user wanted independent orderings).
- Trainer Graphics and Overworld Graphics tabs still use the old click-only palette row. The shared widget is in place; wiring those tabs is the next step.
- Phase 2 (save-pipeline correctness) and later audit phases not yet started — see `docs/POST_RELEASE_AUDIT_PLAN.md`.

---

> **AI Disclosure:** As with previous releases, the vast majority of this code was written with Claude. The human developer (InnerMobius) directs architecture, tests every change, and confirms behaviour. If AI-assisted code is a dealbreaker, this isn't your project.

> **Beta software.** Always keep backups of your decomp project (use git!) and test thoroughly after every editing session.
