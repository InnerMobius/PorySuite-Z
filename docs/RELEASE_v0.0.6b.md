# PorySuite-Z v0.0.6b — Palette Hotfix

## What changed

Sprite viewers across the app now reflect palette edits in real time, without saving or reopening the project.

### Fix: Cross-tab palette propagation

All sprite-displaying tabs (Pokedex, Starters, Items list, Trainers list, Trainer Class editor, species tree, Info panel) previously loaded sprites with a flat `QPixmap(path)`, which reads the baked-in color table from the PNG and ignores the separate `.pal` file entirely. Palette edits made in the Species Graphics, Trainer Graphics, or Overworld Graphics editors were invisible to every other tab until a full save and reopen.

**Fixed with two new shared modules:**

- **`core/sprite_palette_bus.py`** — process-wide palette cache and change-notification bus. Editor tabs push on every mutation; viewer tabs pull (RAM-first, disk-fallback) and subscribe to a signal that fires on every edit.
- **`core/sprite_render.py`** — single entry point (`load_sprite_pixmap`) for rendering any 4bpp indexed PNG with a supplied palette. Handles color-table swap, slot-0 transparency, and an alpha-preserve variant for overworld sprites.

All 11 affected files migrated. Editors push to the bus on every palette mutation path (swatch edit, reorder, background swap, import). Viewers subscribe, invalidate their caches, and repaint immediately.

### Dead code removed

- Duplicate `_pal_path_from_png` in `trainer_graphics_tab.py` replaced with the shared helper from `graphics_data.py`.

### Files touched

`core/sprite_palette_bus.py` (new), `core/sprite_render.py` (new), `ui/graphics_data.py`, `ui/graphics_tab_widget.py`, `ui/trainer_graphics_tab.py`, `ui/overworld_graphics_tab.py`, `ui/pokedex_detail_panel.py`, `ui/mainwindow.py`, `ui/items_tab_widget.py`, `ui/trainer_class_editor.py`, `ui/trainers_tab_widget.py`, `core/app_info.py`, `CLAUDE.md`, `docs/BUGS.md`, `docs/CHANGELOG.md`.
