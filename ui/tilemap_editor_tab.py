"""
ui/tilemap_editor_tab.py
Tilemap Editor — open, view, edit, and save GBA .bin tilemap files.

Features:
- Open any .bin tilemap from the project's graphics/ directory
- Auto-discovers matching tile sheet (.png) and palettes (.pal)
- Renders the tilemap with correct palettes and tile flips
- Tile picker: select tiles from the tile sheet to paint
- Click/drag to place tiles on the tilemap
- Palette and flip controls per-tile
- Grid overlay toggle
- Zoom in/out
- Save back to .bin
"""

from __future__ import annotations

import os
from typing import Optional

from PyQt6.QtCore import Qt, QPoint, QRect, QSize, pyqtSignal
from PyQt6.QtGui import (
    QColor, QImage, QPainter, QPen, QPixmap, QWheelEvent,
)
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QGroupBox, QHBoxLayout,
    QLabel, QMenu,
    QMessageBox, QPushButton, QScrollArea, QSpinBox, QSplitter,
    QTabWidget, QToolBar, QVBoxLayout, QWidget,
)


TILE_PX = 8


class _NoScrollCombo(QComboBox):
    """QComboBox that ignores wheel events when the popup isn't showing."""
    def wheelEvent(self, event):
        if self.view().isVisible():
            super().wheelEvent(event)
        else:
            event.ignore()


# ═══════════════════════════════════════════════════════════════════════════════
#  Palette Editor — visual palette slot viewer with import/export
# ═══════════════════════════════════════════════════════════════════════════════


SWATCH = 14  # pixels per color swatch


class PaletteEditorWidget(QWidget):
    """Shows palette slots as color swatch rows with import/export.

    Only shows slots that are loaded or referenced by the tilemap — no wasted
    rows for empty unused slots. Right-click any slot for import/export options.
    """

    palette_changed = pyqtSignal()  # emitted when any palette slot changes

    def __init__(self, parent=None):
        super().__init__(parent)
        self._palette_set = None
        self._pals_used: set = set()  # palette indices the tilemap uses
        self._selected_slot = 0
        self._visible_slots: list = list(range(16))  # which slots to draw
        self.setMinimumWidth(16 * SWATCH + 60)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

    def set_palette_set(self, ps):
        self._palette_set = ps
        self._rebuild_visible()
        self.update()

    def set_pals_used(self, used: set):
        self._pals_used = used
        self._rebuild_visible()
        self.update()

    def palette_set(self):
        return self._palette_set

    def _rebuild_visible(self):
        """Only show slots that are loaded or used by the tilemap."""
        visible = set(self._pals_used)
        if self._palette_set:
            for s in range(min(16, self._palette_set.palette_count())):
                if self._palette_set.is_slot_loaded(s):
                    visible.add(s)
        self._visible_slots = sorted(visible) if visible else [0]
        row_h = SWATCH + 2
        needed = len(self._visible_slots) * row_h + 4
        self.setFixedHeight(needed)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        sw = SWATCH
        label_w = 28  # width for "P0" label

        for draw_row, slot in enumerate(self._visible_slots):
            y = draw_row * (sw + 2) + 2
            # Slot label
            used = slot in self._pals_used
            loaded = (self._palette_set and
                      self._palette_set.is_slot_loaded(slot))

            if used and loaded:
                p.setPen(QColor(200, 200, 200))
            elif used and not loaded:
                p.setPen(QColor(255, 100, 100))  # red = needed but missing
            elif loaded:
                p.setPen(QColor(140, 140, 140))
            else:
                p.setPen(QColor(80, 80, 80))

            p.drawText(0, y, label_w, sw,
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                        f"P{slot:X}")

            # Color swatches
            for ci in range(16):
                x = label_w + 4 + ci * sw
                if (self._palette_set and loaded
                        and slot < self._palette_set.palette_count()):
                    r, g, b = self._palette_set.palettes[slot][ci]
                    color = QColor(r, g, b)
                else:
                    color = QColor(30, 30, 30)

                p.fillRect(x, y, sw - 1, sw - 1, color)

                # Outline for transparency slot (index 0)
                if ci == 0:
                    p.setPen(QPen(QColor(100, 100, 100), 1))
                    p.drawRect(x, y, sw - 2, sw - 2)

        p.end()

    def _slot_at_y(self, y: int) -> int:
        row = max(0, (y - 2) // (SWATCH + 2))
        if row < len(self._visible_slots):
            return self._visible_slots[row]
        return self._visible_slots[-1] if self._visible_slots else 0

    def _color_at(self, pos) -> tuple:
        """Return (slot, color_index) for a position, or (slot, -1)."""
        slot = self._slot_at_y(pos.y())
        x = pos.x() - 32  # label_w(28) + 4
        if x >= 0:
            ci = x // SWATCH
            if 0 <= ci < 16:
                return (slot, ci)
        return (slot, -1)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._selected_slot = self._slot_at_y(event.pos().y())
            self.update()

    def mouseDoubleClickEvent(self, event):
        """Double-click a swatch to edit its color."""
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if not self._palette_set:
            return
        slot, ci = self._color_at(event.pos())
        if ci < 0:
            return
        if not self._palette_set.is_slot_loaded(slot):
            return
        old_r, old_g, old_b = self._palette_set.palettes[slot][ci]
        from PyQt6.QtWidgets import QColorDialog
        color = QColorDialog.getColor(
            QColor(old_r, old_g, old_b), self,
            f"Edit Slot {slot} Color {ci}")
        if not color.isValid():
            return
        # GBA 15-bit clamping
        r = (color.red() >> 3) << 3
        g = (color.green() >> 3) << 3
        b = (color.blue() >> 3) << 3
        self._palette_set.palettes[slot][ci] = (r, g, b)
        self.update()
        self.palette_changed.emit()

    def _show_context_menu(self, pos):
        slot = self._slot_at_y(pos.y())
        self._selected_slot = slot

        menu = QMenu(self)
        loaded = (self._palette_set and
                  self._palette_set.is_slot_loaded(slot))

        import_act = menu.addAction(f"Import .pal to Slot {slot}...")
        export_act = menu.addAction(f"Export Slot {slot} as .pal...")
        export_act.setEnabled(loaded)
        menu.addSeparator()
        extract_act = menu.addAction(f"Extract from PNG to Slot {slot}...")
        menu.addSeparator()
        export_all = menu.addAction("Export All Loaded as .pal files...")

        action = menu.exec(self.mapToGlobal(pos))
        if action == import_act:
            self._import_pal(slot)
        elif action == export_act:
            self._export_pal(slot)
        elif action == extract_act:
            self._extract_from_png(slot)
        elif action == export_all:
            self._export_all_pals()

    def _import_pal(self, slot: int):
        if not self._palette_set:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, f"Import Palette for Slot {slot}",
            "", "JASC Palette (*.pal);;All files (*)")
        if not path:
            return
        from ui.palette_utils import read_jasc_pal
        colors = read_jasc_pal(path)
        if not colors:
            return
        if len(colors) > 16:
            # 256-color .pal — split into sub-palettes and fill all slots
            for sub in range(0, len(colors), 16):
                idx = sub // 16
                if idx >= 16:
                    break
                chunk = colors[sub:sub + 16]
                while len(chunk) < 16:
                    chunk.append((0, 0, 0))
                self._palette_set.set_palette_at(idx, chunk)
        else:
            self._palette_set.set_palette_at(slot, colors)
        self.palette_changed.emit()
        self.update()

    def _export_pal(self, slot: int):
        if not self._palette_set or not self._palette_set.is_slot_loaded(slot):
            return
        path, _ = QFileDialog.getSaveFileName(
            self, f"Export Palette Slot {slot}",
            f"palette_{slot}.pal",
            "JASC Palette (*.pal)")
        if not path:
            return
        from ui.palette_utils import write_jasc_pal
        colors = self._palette_set.palettes[slot]
        write_jasc_pal(path, colors)

    def _extract_from_png(self, slot: int):
        if not self._palette_set:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, f"Extract Palette from PNG for Slot {slot}",
            "", "PNG images (*.png);;All files (*)")
        if not path:
            return
        img = QImage(path)
        if img.isNull():
            QMessageBox.warning(self, "Error", f"Cannot load: {path}")
            return
        ct = img.colorTable()
        if not ct:
            QMessageBox.warning(self, "Error", "Not an indexed image")
            return

        if len(ct) > 16:
            # 8bpp image — extract all sub-palettes at once
            for sub in range(min(16, (len(ct) + 15) // 16)):
                start = sub * 16
                chunk = ct[start:start + 16]
                colors = []
                for c in chunk:
                    r = (c >> 16) & 0xFF
                    g = (c >> 8) & 0xFF
                    b = c & 0xFF
                    colors.append((r, g, b))
                while len(colors) < 16:
                    colors.append((0, 0, 0))
                self._palette_set.set_palette_at(sub, colors)
        else:
            # 4bpp image — extract to the selected slot
            colors = []
            for c in ct[:16]:
                r = (c >> 16) & 0xFF
                g = (c >> 8) & 0xFF
                b = c & 0xFF
                colors.append((r, g, b))
            while len(colors) < 16:
                colors.append((0, 0, 0))
            self._palette_set.set_palette_at(slot, colors)

        self.palette_changed.emit()
        self.update()

    def _export_all_pals(self):
        if not self._palette_set:
            return

        menu = QMenu(self)
        act_separate = menu.addAction("Export as separate 16-color .pal files...")
        act_combined = menu.addAction("Export as single 256-color .pal file...")
        action = menu.exec(self.mapToGlobal(self.rect().center()))

        if action == act_separate:
            dir_path = QFileDialog.getExistingDirectory(
                self, "Export All Palettes to Directory")
            if not dir_path:
                return
            from ui.palette_utils import write_jasc_pal
            count = 0
            for slot in range(min(16, self._palette_set.palette_count())):
                if self._palette_set.is_slot_loaded(slot):
                    path = os.path.join(dir_path, f"palette_{slot:02d}.pal")
                    write_jasc_pal(path, self._palette_set.palettes[slot])
                    count += 1
            QMessageBox.information(
                self, "Export", f"Exported {count} palette(s) to {dir_path}")

        elif action == act_combined:
            path, _ = QFileDialog.getSaveFileName(
                self, "Export Combined 256-Color Palette",
                "palette_256.pal",
                "JASC Palette (*.pal)")
            if not path:
                return
            from ui.palette_utils import write_jasc_pal
            all_colors = self._palette_set.get_flat_colors()
            write_jasc_pal(path, all_colors)
            QMessageBox.information(
                self, "Export", f"Exported 256-color palette to {path}")


# ═══════════════════════════════════════════════════════════════════════════════
#  Tilemap Canvas — the main editable view
# ═══════════════════════════════════════════════════════════════════════════════


class TilemapCanvas(QWidget):
    """Renders and allows editing of a tilemap."""

    tile_clicked = pyqtSignal(int, int)    # col, row — left button
    tile_hovered = pyqtSignal(int, int)    # col, row
    tile_eyedrop = pyqtSignal(int, int)    # col, row — right button
    stroke_finished = pyqtSignal()         # left-button release — commit to undo
    # Shift+right-drag rubber-band: emits the inclusive (c0, r0, c1, r1) of
    # the selected rectangle on release. The tab uses this to capture a
    # multi-tile stamp from the canvas.
    region_selected = pyqtSignal(int, int, int, int)
    # Middle-click on a cell triggers a flood fill from that origin.
    fill_requested = pyqtSignal(int, int)  # col, row

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rendered: Optional[QImage] = None
        self._zoom = 2
        self._show_grid = True
        self._tilemap = None  # Tilemap
        self._sheet = None    # TileSheet
        self._palettes = None # PaletteSet
        self._tile_offset = 0
        self._painting = False
        self._paint_callback = None  # fn(col, row) called on paint
        # Shift+right-drag rubber-band state. ``_selecting`` is True
        # between press and release; the rectangle is drawn live in the
        # paint event from ``_sel_anchor`` to ``_sel_cursor``. On
        # release we emit ``region_selected`` and clear the state.
        self._selecting = False
        self._sel_anchor: tuple[int, int] = (0, 0)
        self._sel_cursor: tuple[int, int] = (0, 0)
        # Hover ghost: when the parent has a multi-tile stamp active,
        # we draw a faint outline at the would-be paint position so
        # the user can see what's about to land where. Set by the tab
        # via ``set_hover_stamp_size``.
        self._hover_col = -1
        self._hover_row = -1
        self._hover_stamp_w = 1
        self._hover_stamp_h = 1
        self.setMouseTracking(True)
        self.setMinimumSize(64, 64)

    def set_data(self, tilemap, sheet, palettes=None, tile_offset: int = 0):
        self._tilemap = tilemap
        self._sheet = sheet
        self._palettes = palettes
        self._tile_offset = tile_offset
        self._refresh()

    def set_zoom(self, z: int):
        self._zoom = max(1, min(8, z))
        self._update_size()
        self.update()

    def zoom(self) -> int:
        return self._zoom

    def set_show_grid(self, show: bool):
        self._show_grid = show
        self.update()

    def set_paint_callback(self, fn):
        self._paint_callback = fn

    def _refresh(self):
        if self._tilemap and self._sheet:
            from core.tilemap_data import render_tilemap
            self._rendered = render_tilemap(
                self._tilemap, self._sheet, self._palettes,
                tile_offset=self._tile_offset)
        else:
            self._rendered = None
        self._update_size()
        self.update()

    def refresh_tile(self, col: int, row: int):
        """Re-render a single tile and update display."""
        if not self._tilemap or not self._sheet or not self._rendered:
            return
        from core.tilemap_data import (
            _recolor_tile, _recolor_tile_8bpp, _recolor_tile_8bpp_attr,
            build_flat_color_table,
        )
        entry = self._tilemap.get(col, row)

        # Apply tile offset
        local_idx = entry.tile_index - self._tile_offset
        if self._tile_offset > 0 and (local_idx < 0 or local_idx >= self._sheet.tile_count):
            return  # Tile belongs to a different sheet
        idx = local_idx if self._tile_offset > 0 else entry.tile_index

        tile_img = self._sheet.get_tile_image(idx, entry.hflip, entry.vflip)

        use_palettes = (
            self._palettes is not None
            and self._palettes.palette_count() > 0
            and self._sheet.image.format() == QImage.Format.Format_Indexed8
        )
        if use_palettes and tile_img.format() == QImage.Format.Format_Indexed8:
            if self._sheet.is_8bpp:
                # Region-map style: 8bpp PNG holds multiple sub-palettes
                # baked side-by-side, but the GBA actually renders this
                # as 4bpp with the .bin entry's attr-palette selecting
                # which sub-palette to use. Render to MATCH the GBA so
                # the editor canvas is WYSIWYG. If only one palette is
                # loaded, fall through to flat 8bpp (true 256-color BG mode).
                if self._palettes.palette_count() > 1:
                    tile_img = _recolor_tile_8bpp_attr(
                        tile_img, entry.palette, self._palettes)
                else:
                    flat_ct = build_flat_color_table(self._palettes)
                    tile_img = _recolor_tile_8bpp(tile_img, flat_ct)
            else:
                tile_img = _recolor_tile(tile_img, entry.palette, self._palettes)

        painter = QPainter(self._rendered)
        # Use Source mode so transparent pixels overwrite (erase) old content
        # instead of alpha-blending on top of the previous tile
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.drawImage(col * TILE_PX, row * TILE_PX, tile_img)
        painter.end()
        self.update()

    def _update_size(self):
        if self._rendered:
            w = self._rendered.width() * self._zoom
            h = self._rendered.height() * self._zoom
            self.setFixedSize(w, h)
        else:
            self.setFixedSize(256, 160)

    def _tile_at(self, pos: QPoint):
        if not self._tilemap:
            return -1, -1
        col = pos.x() // (TILE_PX * self._zoom)
        row = pos.y() // (TILE_PX * self._zoom)
        if 0 <= col < self._tilemap.width and 0 <= row < self._tilemap.height:
            return col, row
        return -1, -1

    # -- Events --

    def paintEvent(self, event):
        if not self._rendered:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        z = self._zoom
        p.drawImage(
            QRect(0, 0, self._rendered.width() * z, self._rendered.height() * z),
            self._rendered,
        )

        if self._show_grid and self._tilemap:
            p.setPen(QPen(QColor(255, 255, 255, 40), 1))
            tw = self._tilemap.width
            th = self._tilemap.height
            tile_z = TILE_PX * z
            for c in range(tw + 1):
                p.drawLine(c * tile_z, 0, c * tile_z, th * tile_z)
            for r in range(th + 1):
                p.drawLine(0, r * tile_z, tw * tile_z, r * tile_z)

        # Hover ghost — a thin outline showing where the active stamp
        # would land if the user clicked here. Drawn for stamps of any
        # size including 1×1, so the user always sees the paint
        # target. Drawn beneath the rubber-band rectangle (which is
        # higher-priority feedback) but above the grid.
        if (self._hover_col >= 0 and self._tilemap
                and not self._selecting):
            tile_z = TILE_PX * z
            sw, sh = self._hover_stamp_w, self._hover_stamp_h
            # Clip to map bounds so the outline doesn't extend past
            # the canvas edge.
            cw = min(sw, self._tilemap.width - self._hover_col)
            ch = min(sh, self._tilemap.height - self._hover_row)
            if cw > 0 and ch > 0:
                p.setPen(QPen(QColor(255, 255, 255, 120), 1,
                              Qt.PenStyle.DashLine))
                p.drawRect(
                    self._hover_col * tile_z,
                    self._hover_row * tile_z,
                    cw * tile_z, ch * tile_z,
                )

        # Rubber-band rectangle while shift+right-dragging to grab a
        # multi-tile stamp out of the canvas. Solid yellow border so
        # it's visually distinct from the picker's selected-tile
        # highlight (also yellow but on a different widget).
        if self._selecting and self._tilemap:
            tile_z = TILE_PX * z
            c0, r0 = self._sel_anchor
            c1, r1 = self._sel_cursor
            lo_c, hi_c = min(c0, c1), max(c0, c1)
            lo_r, hi_r = min(r0, r1), max(r0, r1)
            p.setPen(QPen(QColor(255, 220, 0), 2))
            p.drawRect(
                lo_c * tile_z, lo_r * tile_z,
                (hi_c - lo_c + 1) * tile_z,
                (hi_r - lo_r + 1) * tile_z,
            )

        p.end()

    def set_hover_stamp_size(self, w: int, h: int) -> None:
        """Tell the canvas the parent's active stamp size, so the hover
        ghost can outline the right region under the cursor. Pass 1, 1
        for single-tile state."""
        self._hover_stamp_w = max(1, int(w))
        self._hover_stamp_h = max(1, int(h))
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            col, row = self._tile_at(event.pos())
            if col >= 0:
                self._painting = True
                self.tile_clicked.emit(col, row)
                if self._paint_callback:
                    self._paint_callback(col, row)
        elif event.button() == Qt.MouseButton.RightButton:
            col, row = self._tile_at(event.pos())
            if col < 0:
                return
            # Shift+right-click+drag = rubber-band region selection
            # for grabbing a multi-tile stamp out of the current
            # tilemap. Plain right-click (no shift) stays as the
            # single-tile eyedrop, preserving existing behaviour.
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self._selecting = True
                self._sel_anchor = (col, row)
                self._sel_cursor = (col, row)
                self.update()
            else:
                self.tile_eyedrop.emit(col, row)
        elif event.button() == Qt.MouseButton.MiddleButton:
            # Middle-click — flood-fill from this cell. The tab
            # decides what to fill with (always the current SINGLE
            # tile per the agreed UX, never the multi-tile stamp).
            col, row = self._tile_at(event.pos())
            if col >= 0:
                self.fill_requested.emit(col, row)

    def mouseMoveEvent(self, event):
        col, row = self._tile_at(event.pos())
        if col >= 0:
            self.tile_hovered.emit(col, row)
            # Track hover for the stamp-ghost overlay. Only repaint
            # when the cell actually changed — repainting on every
            # pixel-grain mouse move costs visible CPU on big maps.
            if (col, row) != (self._hover_col, self._hover_row):
                self._hover_col, self._hover_row = col, row
                self.update()
        if self._painting and event.buttons() & Qt.MouseButton.LeftButton:
            if col >= 0 and self._paint_callback:
                self._paint_callback(col, row)
        if (self._selecting
                and event.buttons() & Qt.MouseButton.RightButton):
            if col >= 0:
                if (col, row) != self._sel_cursor:
                    self._sel_cursor = (col, row)
                    self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self._painting:
                self._painting = False
                # Tell the tab the drag is over so it can commit the stroke
                # to the undo stack as ONE entry (not one per cell painted).
                self.stroke_finished.emit()
        elif event.button() == Qt.MouseButton.RightButton:
            if self._selecting:
                self._selecting = False
                c0, r0 = self._sel_anchor
                c1, r1 = self._sel_cursor
                # Normalise to (top-left, bottom-right) inclusive.
                lo_c, hi_c = min(c0, c1), max(c0, c1)
                lo_r, hi_r = min(r0, r1), max(r0, r1)
                self.region_selected.emit(lo_c, lo_r, hi_c, hi_r)
                self.update()

    def leaveEvent(self, event):
        # Clear hover state when the cursor leaves the canvas so the
        # stamp ghost doesn't linger over an empty hover position.
        if self._hover_col >= 0:
            self._hover_col = -1
            self._hover_row = -1
            self.update()
        super().leaveEvent(event)

    def wheelEvent(self, event: QWheelEvent):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta > 0:
                self.set_zoom(self._zoom + 1)
            elif delta < 0:
                self.set_zoom(self._zoom - 1)
            event.accept()
        else:
            event.ignore()
            super().wheelEvent(event)


# ═══════════════════════════════════════════════════════════════════════════════
#  Tile Picker — select tiles from the tile sheet
# ═══════════════════════════════════════════════════════════════════════════════


class TilePickerWidget(QWidget):
    """Displays the tile sheet and lets user pick a tile index."""

    tile_selected = pyqtSignal(int)  # tile index — single-click pick
    # Shift+right-drag rubber-band: emits the inclusive (c0, r0, c1, r1)
    # rect of the selected sheet region so the parent tab can build a
    # multi-tile stamp from those source tiles.
    region_selected = pyqtSignal(int, int, int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sheet = None
        self._palettes = None
        self._zoom = 2
        self._selected = 0
        self._pal_idx = 0
        # Rubber-band selection state — same shape as the canvas.
        self._selecting = False
        self._sel_anchor: tuple[int, int] = (0, 0)
        self._sel_cursor: tuple[int, int] = (0, 0)
        self.setMouseTracking(True)
        self.setMinimumSize(64, 64)

    def set_sheet(self, sheet, palettes=None):
        self._sheet = sheet
        self._palettes = palettes
        self._selected = 0
        self._recolored_img = None  # cached recolored sheet image
        self._update_size()
        self.update()

    def set_palette_index(self, idx: int):
        self._pal_idx = idx
        self.update()

    def set_zoom(self, z: int):
        self._zoom = max(1, min(4, z))
        self._update_size()
        self.update()

    def selected_tile(self) -> int:
        return self._selected

    def _update_size(self):
        if self._sheet:
            w = self._sheet.image.width() * self._zoom
            h = self._sheet.image.height() * self._zoom
            self.setFixedSize(max(w, 64), max(h, 64))
        else:
            self.setFixedSize(128, 128)

    def paintEvent(self, event):
        if not self._sheet:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        z = self._zoom
        img = self._sheet.image

        # Draw the tile sheet
        if (self._palettes and self._palettes.palette_count() > 0
                and img.format() == QImage.Format.Format_Indexed8):
            if self._sheet.is_8bpp:
                # 8bpp: apply full 256-color table
                from core.tilemap_data import (
                    _recolor_tile_8bpp, build_flat_color_table,
                )
                flat_ct = build_flat_color_table(self._palettes)
                recolored = _recolor_tile_8bpp(img, flat_ct)
            else:
                # 4bpp: recolor with selected sub-palette
                from core.tilemap_data import _recolor_tile
                recolored = _recolor_tile(img, self._pal_idx, self._palettes)
            p.drawImage(
                QRect(0, 0, img.width() * z, img.height() * z),
                recolored,
            )
        else:
            p.drawImage(
                QRect(0, 0, img.width() * z, img.height() * z),
                img,
            )

        # Highlight selected tile
        tw = self._sheet.tiles_wide
        sel_col = self._selected % tw
        sel_row = self._selected // tw
        tile_z = TILE_PX * z
        p.setPen(QPen(QColor(255, 255, 0), 2))
        p.drawRect(sel_col * tile_z, sel_row * tile_z, tile_z, tile_z)

        # Grid
        p.setPen(QPen(QColor(255, 255, 255, 30), 1))
        for c in range(self._sheet.tiles_wide + 1):
            p.drawLine(c * tile_z, 0, c * tile_z, self._sheet.tiles_high * tile_z)
        for r in range(self._sheet.tiles_high + 1):
            p.drawLine(0, r * tile_z, self._sheet.tiles_wide * tile_z, r * tile_z)

        # Rubber-band rectangle while shift+right-dragging.
        if self._selecting:
            c0, r0 = self._sel_anchor
            c1, r1 = self._sel_cursor
            lo_c, hi_c = min(c0, c1), max(c0, c1)
            lo_r, hi_r = min(r0, r1), max(r0, r1)
            p.setPen(QPen(QColor(255, 220, 0), 2))
            p.drawRect(
                lo_c * tile_z, lo_r * tile_z,
                (hi_c - lo_c + 1) * tile_z,
                (hi_r - lo_r + 1) * tile_z,
            )

        p.end()

    def _cell_at(self, pos) -> tuple[int, int]:
        """Return (col, row) for the given pixel position, clamped to
        the sheet's grid. Returns (-1, -1) if no sheet is loaded or
        the cell is past the sheet bounds."""
        if not self._sheet:
            return -1, -1
        z = self._zoom
        col = int(pos.x()) // (TILE_PX * z)
        row = int(pos.y()) // (TILE_PX * z)
        if col < 0 or row < 0:
            return -1, -1
        if col >= self._sheet.tiles_wide or row >= self._sheet.tiles_high:
            return -1, -1
        return col, row

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._sheet:
            col, row = self._cell_at(event.pos())
            if col < 0:
                return
            idx = row * self._sheet.tiles_wide + col
            if 0 <= idx < self._sheet.tile_count:
                self._selected = idx
                self.tile_selected.emit(idx)
                self.update()
        elif (event.button() == Qt.MouseButton.RightButton
              and self._sheet
              and event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            # Shift+right-drag in the picker grabs a rectangular region
            # of the sheet as a multi-tile stamp.
            col, row = self._cell_at(event.pos())
            if col >= 0:
                self._selecting = True
                self._sel_anchor = (col, row)
                self._sel_cursor = (col, row)
                self.update()

    def mouseMoveEvent(self, event):
        if (self._selecting
                and event.buttons() & Qt.MouseButton.RightButton):
            col, row = self._cell_at(event.pos())
            if col >= 0 and (col, row) != self._sel_cursor:
                self._sel_cursor = (col, row)
                self.update()

    def mouseReleaseEvent(self, event):
        if (event.button() == Qt.MouseButton.RightButton
                and self._selecting):
            self._selecting = False
            c0, r0 = self._sel_anchor
            c1, r1 = self._sel_cursor
            lo_c, hi_c = min(c0, c1), max(c0, c1)
            lo_r, hi_r = min(r0, r1), max(r0, r1)
            self.region_selected.emit(lo_c, lo_r, hi_c, hi_r)
            self.update()


# ═══════════════════════════════════════════════════════════════════════════════
#  Main Tab Widget
# ═══════════════════════════════════════════════════════════════════════════════


class TilemapEditorTab(QWidget):
    """Full tilemap editor page for the unified toolbar."""

    modified = pyqtSignal()
    tilemap_saved = pyqtSignal(str)   # absolute path of the saved .bin

    def __init__(self, parent=None):
        super().__init__(parent)
        self._project_dir = ""
        self._tilemap = None
        self._sheet = None
        self._palettes = None
        self._current_tile = 0
        self._current_pal = 0
        self._hflip = False
        self._vflip = False
        self._dirty = False
        # Palette edits go through the right-side editor and live in
        # `self._palettes`. Mark this true when ANY slot changes so save
        # writes the palette back to its source file(s) AND bakes the
        # new colours into the tile sheet PNG (so opening the .png in
        # GIMP shows the colours that the editor / game render with).
        self._palette_dirty = False
        self._tool = "paint"  # "paint" or "pick"
        self._tile_offset = 0  # VRAM tile offset for current sheet
        self._last_open_dir = ""  # remembers last Open dialog folder

        # ── Undo/redo (Ctrl+Z / Ctrl+Y) ─────────────────────────────────────
        # A drag-paint stroke is a single undo step. _current_stroke
        # captures the OLD entry of every cell touched during the active
        # drag — committed to _undo_stack on mouse release. Cells already
        # in _current_stroke aren't re-recorded (so dragging back over
        # the same cell doesn't lose the original-original entry).
        self._undo_stack: list[dict] = []  # list[{(col,row): old_TileEntry}]
        self._redo_stack: list[dict] = []
        self._current_stroke: dict = {}
        self._undo_limit = 100             # cap to bound memory

        # ── Multi-tile stamp state ──────────────────────────────────────────
        # The "active stamp" is the rectangle of tiles a left-click
        # paints in one go. By default it's 1×1 — a single tile
        # described by ``_current_tile / _current_pal / _hflip /
        # _vflip``. Shift+right-drag in the canvas OR the picker
        # captures a larger rectangle into ``_stamp_grid``, which
        # ``_paint_tile`` then uses to stamp multiple cells per click.
        # Single-tile picks (left-click in picker, plain right-click
        # eyedrop on the canvas) reset the stamp back to 1×1.
        self._stamp_grid: list[list] = []  # row-major list of TileEntry
        self._stamp_w: int = 1             # width in tiles
        self._stamp_h: int = 1             # height in tiles

        self._build_ui()
        self._install_undo_shortcuts()

    def set_project(self, project_dir: str):
        self._project_dir = project_dir
        if hasattr(self, '_anim_viewer'):
            self._anim_viewer.set_project(project_dir)
        # Palette Baker is project-aware too — pass the root through so
        # its scanner can walk graphics/. The tab's load() also doubles
        # as its F5-clean reset (cancels in-flight scans, clears dirty
        # state, re-kicks the audit).
        if hasattr(self, '_palette_baker'):
            self._palette_baker.load(project_dir)

    # ── UI Construction ──────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # -- Tab widget: Tilemap Editor + Tile Animations --
        self._tab_widget = QTabWidget()
        root.addWidget(self._tab_widget)

        # -- Tab 0: Tilemap Editor --
        editor_page = QWidget()
        editor_layout = QVBoxLayout(editor_page)
        editor_layout.setContentsMargins(4, 4, 4, 4)
        editor_layout.setSpacing(4)
        self._tab_widget.addTab(editor_page, "Tilemap Editor")

        # -- Tab 1: Tile Animations --
        from ui.tile_anim_viewer import TileAnimEditorWidget
        self._anim_viewer = TileAnimEditorWidget()
        self._tab_widget.addTab(self._anim_viewer, "Tile Animations")

        # -- Tab 2: Image Indexer --
        try:
            from ui.image_indexer_tab import ImageIndexerWidget
            self._image_indexer = ImageIndexerWidget()
            self._tab_widget.addTab(self._image_indexer, "Image Indexer")
        except Exception as e:
            print(f"[ImageIndexer] Failed to load: {e}")
            import traceback
            traceback.print_exc()

        # -- Tab 3: Palette Baker --
        # Re-bakes canonical .pal palettes into stale PNG color tables
        # across the project. Different from Image Indexer (which
        # converts non-indexed sources INTO indexed form picking a new
        # palette as it goes); this tab takes ALREADY-INDEXED PNGs
        # whose embedded colour table has drifted from their canonical
        # .pal neighbour and rewrites the colour table to match —
        # pixel indices are never touched.
        try:
            from ui.palette_baker_tab import PaletteBakerTab
            self._palette_baker = PaletteBakerTab()
            self._tab_widget.addTab(self._palette_baker, "Palette Baker")
        except Exception as e:
            print(f"[PaletteBaker] Failed to load: {e}")
            import traceback
            traceback.print_exc()

        # ── Build the tilemap editor inside editor_page ──────────────────────

        # -- Toolbar --
        tb = QToolBar()
        tb.setIconSize(QSize(20, 20))

        self._btn_open = QPushButton("Open Tilemap...")
        self._btn_open.clicked.connect(self._open_file)
        tb.addWidget(self._btn_open)

        self._btn_save = QPushButton("Save")
        self._btn_save.setEnabled(False)
        self._btn_save.clicked.connect(self._save_file)
        tb.addWidget(self._btn_save)

        self._btn_reveal = QPushButton("Open in Folder")
        self._btn_reveal.setToolTip(
            "Reveal the current tilemap, tile sheet, and palette files in "
            "your OS file manager. Useful for editing the .png in an "
            "external image editor.")
        self._btn_reveal.setEnabled(False)
        self._btn_reveal.clicked.connect(self._on_reveal_in_folder)
        tb.addWidget(self._btn_reveal)

        self._btn_autofix = QPushButton("Auto-Fix Palettes")
        self._btn_autofix.setToolTip(
            "Scan every tile in this tilemap and set its sub-palette bits "
            "to match the dominant 16-color range in the tile's pixel "
            "data. Use this after upgrading PorySuite-Z if a tilemap was "
            "saved before the multi-palette fix and now renders with "
            "wrong colors. One undo step. No-op for true 4bpp / "
            "single-palette 8bpp sheets.")
        self._btn_autofix.setEnabled(False)
        self._btn_autofix.clicked.connect(self._on_autofix_palettes)
        tb.addWidget(self._btn_autofix)

        self._btn_apply_pal = QPushButton("Bake Palette into External PNG…")
        self._btn_apply_pal.setToolTip(
            "NOTE: To save palette edits for the CURRENT tilemap, just "
            "press 'Save' — palette changes are written back to the "
            ".pal file(s) and baked into the tile sheet PNG automatically.\n\n"
            "This button is for a DIFFERENT workflow: take an unrelated "
            "external PNG (e.g. the original artwork you made this "
            "tilemap from) and bake the editor's current palette into "
            "its color table, then save as a new PNG.\n\n"
            "Step 1 opens a file picker so you can choose which external "
            "PNG to recolour. Step 2 asks where to save the recoloured "
            "copy (the original is never modified).\n\n"
            "If the PNG is already indexed, pixel indices are kept "
            "exactly — only the color table changes, so pixel-art stays "
            "pixel-perfect. If it's RGB, pixels are remapped to the "
            "nearest colour in the current palette (lossy)."
        )
        self._btn_apply_pal.setEnabled(False)
        self._btn_apply_pal.clicked.connect(self._on_apply_palette_to_png)
        tb.addWidget(self._btn_apply_pal)

        tb.addSeparator()

        # Tile sheet selector
        tb.addWidget(QLabel(" Tile Sheet: "))
        self._sheet_combo = _NoScrollCombo()
        self._sheet_combo.setMinimumWidth(200)
        self._sheet_combo.currentIndexChanged.connect(self._on_sheet_changed)
        tb.addWidget(self._sheet_combo)

        self._btn_reveal_sheet = QPushButton("Open Sheet")
        self._btn_reveal_sheet.setToolTip(
            "Reveal the currently-selected tile sheet (.png) in your OS "
            "file manager. Use this to open the sheet in an external "
            "image editor (GIMP, Aseprite, etc.). When you save the "
            ".png and return to PorySuite, the editor reloads it next "
            "time you change sheets or reload the tilemap.")
        self._btn_reveal_sheet.setEnabled(False)
        self._btn_reveal_sheet.clicked.connect(self._on_reveal_sheet_in_folder)
        tb.addWidget(self._btn_reveal_sheet)

        tb.addSeparator()

        # Zoom
        tb.addWidget(QLabel(" Zoom: "))
        self._zoom_spin = QSpinBox()
        self._zoom_spin.setRange(1, 8)
        self._zoom_spin.setValue(2)
        self._zoom_spin.valueChanged.connect(self._on_zoom_changed)
        tb.addWidget(self._zoom_spin)

        # Grid toggle
        self._grid_check = QCheckBox("Grid")
        self._grid_check.setChecked(True)
        self._grid_check.toggled.connect(self._on_grid_toggled)
        tb.addWidget(self._grid_check)

        tb.addSeparator()

        # Tilemap dimensions — live visual REWRAP. Scrubbing W or H
        # re-flows the existing entries across a different row stride
        # in real time so you can see what the data looks like at
        # different widths. Total entry count is preserved; the OTHER
        # axis auto-recalculates to fit. To actually change the tile
        # count (truncate/pad) use the Resize… button.
        tb.addWidget(QLabel(" W: "))
        self._width_spin = QSpinBox()
        self._width_spin.setRange(1, 128)
        self._width_spin.setValue(32)
        self._width_spin.setToolTip(
            "Tilemap width — live visual re-wrap. Scrub to see the "
            "same entries at a different row stride; height auto-"
            "recalculates to fit. No data is lost. To actually "
            "change the tile COUNT (truncate or pad), use Resize…")
        self._width_spin.valueChanged.connect(self._on_dimensions_changed)
        tb.addWidget(self._width_spin)

        tb.addWidget(QLabel(" H: "))
        self._height_spin = QSpinBox()
        self._height_spin.setRange(1, 128)
        self._height_spin.setValue(20)
        self._height_spin.setToolTip(
            "Tilemap height — live visual re-wrap. Scrub to see the "
            "same entries at a different row count; width auto-"
            "recalculates to fit. No data is lost. To actually "
            "change the tile COUNT (truncate or pad), use Resize…")
        self._height_spin.valueChanged.connect(self._on_dimensions_changed)
        tb.addWidget(self._height_spin)

        self._btn_resize = QPushButton("Resize…")
        self._btn_resize.setToolTip(
            "Change the tilemap's tile COUNT — pick new width and "
            "height, the result is exactly W*H tiles. Shrinking "
            "truncates entries past the new bounds (with a confirm "
            "if any are painted); growing pads with blank tiles. "
            "Use this for partial-screen UIs that need a specific "
            "tilemap size.")
        self._btn_resize.setEnabled(False)
        self._btn_resize.clicked.connect(self._on_resize)
        tb.addWidget(self._btn_resize)

        tb.addSeparator()

        # Tile offset — adjusts which VRAM index maps to tile 0 in the sheet
        tb.addWidget(QLabel(" Tile Offset: "))
        self._offset_spin = QSpinBox()
        self._offset_spin.setRange(0, 1023)
        self._offset_spin.setValue(0)
        self._offset_spin.setToolTip(
            "VRAM tile offset: tilemap index X maps to sheet tile (X - offset).\n"
            "Use this when a tile sheet loads at a non-zero VRAM position."
        )
        self._offset_spin.valueChanged.connect(self._on_offset_changed)
        tb.addWidget(self._offset_spin)

        editor_layout.addWidget(tb)

        # -- Main splitter: canvas left, right panel --
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._main_splitter = splitter

        # Left: tilemap canvas in scroll area
        self._canvas = TilemapCanvas()
        self._canvas.tile_clicked.connect(self._on_canvas_click)
        self._canvas.tile_hovered.connect(self._on_canvas_hover)
        self._canvas.tile_eyedrop.connect(self._on_canvas_eyedrop)
        self._canvas.stroke_finished.connect(self._on_stroke_finished)
        self._canvas.region_selected.connect(self._on_canvas_region_selected)
        self._canvas.fill_requested.connect(self._on_canvas_fill_requested)
        self._canvas.set_paint_callback(self._paint_tile)

        canvas_scroll = QScrollArea()
        canvas_scroll.setWidget(self._canvas)
        canvas_scroll.setWidgetResizable(False)
        canvas_scroll.setMinimumWidth(200)
        splitter.addWidget(canvas_scroll)

        # ── Right panel ─────────────────────────────────────────────────
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(4, 0, 4, 0)
        right_layout.setSpacing(2)

        # -- Compact controls bar: Tool + Tile info in one row --
        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(6)

        # Tool buttons (compact)
        self._btn_paint = QPushButton("Paint")
        self._btn_paint.setCheckable(True)
        self._btn_paint.setChecked(True)
        self._btn_paint.setFixedWidth(50)
        self._btn_paint.clicked.connect(lambda: self._set_tool("paint"))
        self._btn_pick = QPushButton("Pick")
        self._btn_pick.setCheckable(True)
        self._btn_pick.setFixedWidth(40)
        self._btn_pick.setToolTip("Eyedropper — click tilemap to pick tile")
        self._btn_pick.clicked.connect(lambda: self._set_tool("pick"))
        ctrl_row.addWidget(self._btn_paint)
        ctrl_row.addWidget(self._btn_pick)

        # Separator
        sep = QLabel("|")
        sep.setStyleSheet("color: #555;")
        ctrl_row.addWidget(sep)

        # Tile index
        ctrl_row.addWidget(QLabel("Tile:"))
        self._tile_idx_label = QLabel("0")
        self._tile_idx_label.setStyleSheet("font-weight: bold;")
        self._tile_idx_label.setMinimumWidth(24)
        ctrl_row.addWidget(self._tile_idx_label)

        # Palette
        ctrl_row.addWidget(QLabel("Pal:"))
        self._pal_spin = QSpinBox()
        self._pal_spin.setRange(0, 15)
        self._pal_spin.setFixedWidth(46)
        self._pal_spin.valueChanged.connect(self._on_pal_changed)
        ctrl_row.addWidget(self._pal_spin)

        # Flip checkboxes
        self._hflip_check = QCheckBox("H")
        self._hflip_check.setToolTip("Horizontal flip")
        self._hflip_check.toggled.connect(self._on_hflip_changed)
        self._vflip_check = QCheckBox("V")
        self._vflip_check.setToolTip("Vertical flip")
        self._vflip_check.toggled.connect(self._on_vflip_changed)
        ctrl_row.addWidget(self._hflip_check)
        ctrl_row.addWidget(self._vflip_check)

        # Tile preview (compact, inline)
        self._tile_preview = QLabel()
        self._tile_preview.setFixedSize(32, 32)
        self._tile_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._tile_preview.setStyleSheet(
            "background: #222; border: 1px solid #555;")
        ctrl_row.addWidget(self._tile_preview)

        # Stamp badge — shows "Stamp: 3×4" when a multi-tile region is
        # active. Reads "Stamp: 1×1" by default. Hidden when no
        # tilemap is loaded (kept simple — the badge always reflects
        # the live stamp size, no special "no map" state).
        self._stamp_badge = QLabel("Stamp: 1×1")
        self._stamp_badge.setToolTip(
            "Active stamp size. Shift+right-click+drag in the canvas "
            "or the tile sheet to grab a multi-tile region. Single-"
            "click a tile in the sheet (or right-click a tile on the "
            "canvas) to reset to 1×1.")
        self._stamp_badge.setStyleSheet(
            "QLabel { color: #888; padding: 2px 6px; "
            "border: 1px solid #444; border-radius: 3px; "
            "background: #2a2a2a; }")
        ctrl_row.addWidget(self._stamp_badge)

        ctrl_row.addStretch()
        right_layout.addLayout(ctrl_row)

        # -- Tile sheet picker — THE FOCUS of the right panel --
        self._picker = TilePickerWidget()
        self._picker.tile_selected.connect(self._on_tile_picked)
        self._picker.region_selected.connect(self._on_picker_region_selected)

        picker_scroll = QScrollArea()
        picker_scroll.setWidget(self._picker)
        picker_scroll.setWidgetResizable(False)
        right_layout.addWidget(picker_scroll, 1)  # stretch=1, takes all space

        # -- Palette bar (compact, at bottom) --
        # Header row with source combo and action buttons
        pal_header = QHBoxLayout()
        pal_header.setSpacing(4)
        pal_header.setContentsMargins(0, 4, 0, 0)
        self._pal_header_label = QLabel("Palettes")
        self._pal_header_label.setStyleSheet(
            "font-weight: bold; color: #aaa; font-size: 11px;")
        pal_header.addWidget(self._pal_header_label)

        self._pal_source_combo = _NoScrollCombo()
        self._pal_source_combo.addItem("Auto .pal", "pal")
        self._pal_source_combo.addItem("PNG colors", "png")
        self._pal_source_combo.setFixedWidth(100)
        self._pal_source_combo.setToolTip(
            "Palette source:\n"
            "  Auto .pal — load from .pal files in the tilemap's directory\n"
            "  PNG colors — extract from the tile sheet image's color table")
        self._pal_source_combo.currentIndexChanged.connect(
            self._on_pal_source_changed)
        pal_header.addWidget(self._pal_source_combo)

        pal_header.addStretch()

        # Import / Export buttons (visible)
        btn_import = QPushButton("Import .pal")
        btn_import.setFixedWidth(80)
        btn_import.setToolTip("Import a JASC .pal file (16 or 256 colors)")
        btn_import.clicked.connect(self._on_import_pal_clicked)
        pal_header.addWidget(btn_import)

        btn_export = QPushButton("Export .pal")
        btn_export.setFixedWidth(80)
        btn_export.setToolTip("Export palettes as .pal file(s)")
        btn_export.clicked.connect(self._on_export_pal_clicked)
        pal_header.addWidget(btn_export)

        right_layout.addLayout(pal_header)

        # Palette swatches — only shows loaded/used slots, auto-sizes height
        self._pal_editor = PaletteEditorWidget()
        self._pal_editor.palette_changed.connect(self._on_palette_edited)
        right_layout.addWidget(self._pal_editor)  # no stretch, fixed height

        # -- Status --
        self._status = QLabel("No tilemap loaded")
        self._status.setStyleSheet("color: #888; font-size: 11px;")
        right_layout.addWidget(self._status)

        right.setMinimumWidth(250)
        splitter.addWidget(right)

        # Canvas gets more space; both sides have minimums so neither
        # can be crushed when dragging the splitter on small windows.
        splitter.setStretchFactor(0, 3)   # canvas expands more
        splitter.setStretchFactor(1, 2)   # right panel expands less
        splitter.setSizes([600, 400])

        editor_layout.addWidget(splitter, 1)

    # ── File operations ──────────────────────────────────────────────────────

    def _open_file(self):
        # Use last-opened directory if available, else graphics/
        if self._last_open_dir and os.path.isdir(self._last_open_dir):
            start_dir = self._last_open_dir
        elif self._project_dir:
            gfx = os.path.join(self._project_dir, "graphics")
            start_dir = gfx if os.path.isdir(gfx) else self._project_dir
        else:
            start_dir = ""

        path, _ = QFileDialog.getOpenFileName(
            self, "Open Tilemap",
            start_dir,
            "Tilemap files (*.bin);;All files (*)",
        )
        if not path:
            return

        self._last_open_dir = os.path.dirname(path)
        self._load_tilemap(path)

    def _load_tilemap(self, bin_path: str,
                      sheet_override: str = "",
                      palette_override: str = ""):
        """Open a .bin tilemap. Auto-discovers .png + .pal/.gbapal in the
        same directory unless explicit overrides are passed (used by
        cross-tab nav from Region Map, which knows the exact sheet/palette
        to use even when name-matching auto-discovery wouldn't pick them).
        """
        from core.tilemap_data import (
            Tilemap, TileSheet, PaletteSet, discover_assets,
            get_tilemap_dim_pref,
        )

        # Look up the user's last-saved (W, H) for this .bin in PorySuite's
        # per-project cache. Without this, Tilemap.from_file falls back to
        # _guess_width which picks 32 for any 32-divisible count — losing
        # custom widths the user set with the Resize dialog.
        pref_w = 0
        pref = get_tilemap_dim_pref(self._project_dir, bin_path)
        if pref is not None:
            pref_w = pref[0]

        try:
            tilemap = Tilemap.from_file(bin_path, width=pref_w)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Cannot load tilemap:\n{e}")
            return

        # If the cache had a height pref AND it doesn't match what
        # from_file inferred, prefer the cached height — but only if the
        # entry count actually fits it. Avoids accidentally truncating
        # if the file shrank under our cache.
        if pref is not None:
            pref_h = pref[1]
            if pref_h > 0 and pref_w * pref_h == len(tilemap.entries):
                tilemap.height = pref_h

        self._tilemap = tilemap

        # Update dimension spinners (block signals to avoid re-layout)
        self._width_spin.blockSignals(True)
        self._height_spin.blockSignals(True)
        self._width_spin.setValue(tilemap.width)
        self._height_spin.setValue(tilemap.height)
        self._width_spin.blockSignals(False)
        self._height_spin.blockSignals(False)

        # Auto-discover assets, then apply explicit overrides on top.
        assets = discover_assets(bin_path)
        if sheet_override:
            assets.best_sheet = sheet_override
            if sheet_override not in assets.tile_sheets:
                assets.tile_sheets.insert(0, sheet_override)
        if palette_override:
            assets.best_pals = [palette_override]
            if palette_override not in assets.pal_files:
                assets.pal_files.insert(0, palette_override)

        # Populate sheet combo
        self._sheet_combo.blockSignals(True)
        self._sheet_combo.clear()
        for s in assets.tile_sheets:
            self._sheet_combo.addItem(os.path.basename(s), s)
        # Select best sheet
        if assets.best_sheet:
            for i in range(self._sheet_combo.count()):
                if self._sheet_combo.itemData(i) == assets.best_sheet:
                    self._sheet_combo.setCurrentIndex(i)
                    break
        self._sheet_combo.blockSignals(False)

        # Load tile sheet
        self._sheet = None
        if assets.best_sheet:
            try:
                self._sheet = TileSheet.from_file(assets.best_sheet)
            except Exception:
                pass

        # Load palettes — prefer name-matching .pal files, otherwise
        # use PNG's own color table (almost always the correct palette
        # when there's no dedicated .pal file for this tilemap)
        self._palettes = None
        if assets.best_pals:
            try:
                self._palettes = PaletteSet.from_pal_files(assets.best_pals)
            except Exception:
                pass
        # Fallback: extract palette from tile sheet image
        if (not self._palettes or self._palettes.palette_count() == 0) and self._sheet:
            self._palettes = PaletteSet.from_indexed_image(self._sheet.image)
            # Set palette source combo to "PNG colors" when using fallback
            self._pal_source_combo.blockSignals(True)
            idx = self._pal_source_combo.findText("PNG colors")
            if idx >= 0:
                self._pal_source_combo.setCurrentIndex(idx)
            self._pal_source_combo.blockSignals(False)

        # Reset tile offset
        self._tile_offset = 0
        self._offset_spin.blockSignals(True)
        self._offset_spin.setValue(0)
        self._offset_spin.blockSignals(False)

        # Reset palette source combo
        self._pal_source_combo.blockSignals(True)
        self._pal_source_combo.setCurrentIndex(0)
        self._pal_source_combo.blockSignals(False)

        # Update canvas
        self._canvas.set_data(self._tilemap, self._sheet, self._palettes)
        self._picker.set_sheet(self._sheet, self._palettes)

        # 8bpp + single palette = true 256-color BG mode (palette bits
        # ignored by hardware). 8bpp + multi-palette = region-map style
        # (.png holds multiple sub-palettes baked, but the GBA renders
        # 4bpp with attr-palette selection — the spinner DOES matter).
        # 4bpp = always relevant.
        is_8bpp = self._sheet and self._sheet.is_8bpp
        multi_pal = (
            self._palettes is not None
            and self._palettes.palette_count() > 1
        )
        spinner_meaningful = (not is_8bpp) or multi_pal
        self._pal_spin.setEnabled(spinner_meaningful)
        if is_8bpp and not multi_pal:
            self._pal_spin.setToolTip(
                "Palette slot is ignored in true 256-color BG mode.\n"
                "All 256 colors are used directly from the full palette."
            )
        elif is_8bpp and multi_pal:
            self._pal_spin.setToolTip(
                "Sub-palette index for this tile (region-map style).\n"
                "The .png stores multiple 16-color sub-palettes; the GBA\n"
                "selects which one via the .bin entry's attr-palette bits.\n"
                "Picking a tile from the picker auto-detects the right slot."
            )
        else:
            self._pal_spin.setToolTip("")

        self._btn_save.setEnabled(True)
        self._btn_reveal.setEnabled(True)
        # Sheet-reveal only enabled when there's actually a sheet path to show.
        self._btn_reveal_sheet.setEnabled(bool(assets.best_sheet))
        # Auto-Fix only does useful work for 8bpp + multi-palette sheets.
        autofix_useful = bool(
            self._sheet and self._sheet.is_8bpp
            and self._palettes and self._palettes.palette_count() > 1
        )
        self._btn_autofix.setEnabled(autofix_useful)
        # Apply-palette-to-PNG only useful when there's a palette loaded.
        self._btn_apply_pal.setEnabled(
            self._palettes is not None
            and self._palettes.palette_count() > 0
        )
        # Resize requires a loaded tilemap.
        self._btn_resize.setEnabled(self._tilemap is not None)
        self._dirty = False
        self._palette_dirty = False
        # Loading a different file invalidates the undo/redo history —
        # those steps reference cells in the previous tilemap.
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._current_stroke.clear()
        # Multi-tile stamp doesn't survive a project / tilemap reload
        # either — its tile entries reference indices and palettes
        # specific to whatever tilemap was loaded before.
        self._reset_stamp_to_single()

        fname = os.path.basename(bin_path)
        parent = os.path.basename(os.path.dirname(bin_path))
        sheet_name = os.path.basename(assets.best_sheet) if assets.best_sheet else "none"

        # Show which palette indices are actually used by this tilemap
        pals_used = set()
        max_idx = 0
        for e in tilemap.entries:
            pals_used.add(e.palette)
            if e.tile_index > max_idx:
                max_idx = e.tile_index

        is_8bpp = self._sheet and self._sheet.is_8bpp
        bpp_mode = "8bpp" if is_8bpp else "4bpp"

        # Palette info: show loaded slot count and .pal file count
        loaded_slots = self._palettes.loaded_slot_count() if self._palettes else 0
        pal_file_count = len(assets.best_pals)
        if is_8bpp:
            pal_info = f"256-color palette ({loaded_slots} sub-palettes from {pal_file_count} .pal)"
        else:
            pal_info = f"{loaded_slots} palette(s) from {pal_file_count} .pal"

        self._status.setText(
            f"{parent}/{fname} — {tilemap.width}x{tilemap.height} tiles"
            f" — Sheet: {sheet_name} ({bpp_mode})"
            f" — {pal_info}"
            f" — Max tile: {max_idx}"
        )

        self._update_pal_editor()

    def _save_file(self):
        if not self._tilemap or not self._tilemap.source_path:
            return
        try:
            self._tilemap.save()
            # Persist W/H to the project's PorySuite cache so the next
            # load doesn't fall back to _guess_width (which would lose
            # any user-set custom width). The .bin format itself has no
            # header; this is the only way to remember the choice.
            try:
                from core.tilemap_data import set_tilemap_dim_pref
                set_tilemap_dim_pref(
                    self._project_dir, self._tilemap.source_path,
                    self._tilemap.width, self._tilemap.height,
                )
            except Exception:
                pass
            # Flush palette edits too — write back to .pal/.gbapal sources
            # AND bake into the tile sheet PNG so GIMP shows current colors.
            pal_msg = ""
            if self._palette_dirty:
                wrote_to, errs = self._flush_palette_edits()
                if wrote_to:
                    pal_msg = f" + palette ({', '.join(wrote_to)})"
                if errs:
                    QMessageBox.warning(
                        self, "Palette Save",
                        "Some palette files couldn't be written:\n"
                        + "\n".join(errs))
            self._dirty = False
            self._palette_dirty = False
            self._status.setText(
                self._status.text().split(" — Saved")[0]
                + f" — Saved!{pal_msg}")
            self.tilemap_saved.emit(self._tilemap.source_path)
        except Exception as e:
            QMessageBox.warning(self, "Save Error", str(e))

    def _flush_palette_edits(self) -> tuple[list[str], list[str]]:
        """Write the in-memory palette back to its source file(s) AND
        bake the colors into the current tile sheet PNG.

        Sources written:
          - Every path in `self._palettes.source_paths` — `.pal` files get
            JASC text format, `.gbapal` files get raw BGR555 binary.
            Multi-palette files (>1 sub-palette loaded) are written as a
            flat 256-color sequence; single-palette files get just 16.
          - The current tile sheet PNG (if Format_Indexed8 on disk) gets
            its color table rewritten so the file opens in GIMP with the
            colors the user sees in the editor.

        Returns (paths_written_basenames, error_messages).
        """
        import os as _os
        from core.tilemap_data import _write_gbapal_file
        from ui.palette_utils import write_jasc_pal

        wrote: list[str] = []
        errors: list[str] = []
        if not self._palettes:
            return wrote, errors

        # Build the flat color list. Multi-palette PaletteSet returns 16
        # sub-palettes of 16 each; for single-palette sources we collapse
        # to just the one populated slot.
        n_loaded = self._palettes.loaded_slot_count() if hasattr(
            self._palettes, 'loaded_slot_count') else self._palettes.palette_count()
        # Flat color list spanning every loaded slot, padded internally.
        flat_colors = self._palettes.get_flat_colors() if hasattr(
            self._palettes, 'get_flat_colors') else []
        if not flat_colors and self._palettes.palette_count() > 0:
            # Fallback — build flat manually
            flat_colors = []
            for i in range(self._palettes.palette_count()):
                pal = self._palettes.palettes[i]
                flat_colors.extend(pal[:16])
                while len(flat_colors) % 16 != 0:
                    flat_colors.append((0, 0, 0))

        # Decide how many colors to write based on how many sub-palettes
        # were loaded. Multi-palette source -> write all loaded slots.
        # Single-palette source -> write 16.
        if n_loaded > 1:
            write_count = min(n_loaded * 16, len(flat_colors))
        else:
            write_count = min(16, len(flat_colors))
        out_colors = flat_colors[:write_count]

        # Decide whether the source paths are a split-palette set
        # (multiple files, one 16-color sub-palette per file — the
        # textbox1.pal / textbox2.pal pattern) or a single combined
        # source. When split, write each file with ITS slice of the
        # flat color list (file N gets colors [N*16 : (N+1)*16]).
        # Writing the same flat list to every file would overwrite
        # textbox1.pal with all 32 colors AND textbox2.pal with all
        # 32 colors — both wrong, since the build's `cat textbox1
        # textbox2 > textbox.gbapal` step would then produce a 64-
        # color result the engine doesn't expect.
        source_paths = list(self._palettes.source_paths or [])
        is_split = (
            len(source_paths) >= 2
            and n_loaded >= len(source_paths)
            and all(
                _os.path.splitext(p)[1].lower() == ".pal"
                for p in source_paths
            )
        )

        # Write each source palette file.
        for slot_idx, path in enumerate(source_paths):
            if not path:
                continue
            if is_split:
                # File N gets palette N (16 colors). The flat list is
                # already in palette-major order (slot 0 colors, then
                # slot 1, …) — slice [slot_idx*16 : slot_idx*16+16].
                file_colors = flat_colors[
                    slot_idx * 16 : slot_idx * 16 + 16
                ]
                # Pad to 16 in case the in-memory palette had fewer
                # colors (defensive — shouldn't happen but cheap to
                # guard).
                while len(file_colors) < 16:
                    file_colors.append((0, 0, 0))
            else:
                # Single combined source — write the full flat list.
                file_colors = list(out_colors)
            try:
                ext = _os.path.splitext(path)[1].lower()
                if ext == ".gbapal":
                    if _write_gbapal_file(path, file_colors):
                        wrote.append(_os.path.basename(path))
                    else:
                        errors.append(f"{_os.path.basename(path)}: write failed")
                else:
                    if write_jasc_pal(path, file_colors):
                        wrote.append(_os.path.basename(path))
                    else:
                        errors.append(f"{_os.path.basename(path)}: write failed")
            except Exception as exc:
                errors.append(f"{_os.path.basename(path)}: {exc}")

        # Bake into the tile sheet PNG. Pixel indices are untouched —
        # only the color table is rewritten so opening the file in GIMP
        # shows the new colors. Refuses non-Indexed8 PNGs (would otherwise
        # produce an RGB PNG that breaks gbagfx during the build).
        sheet_path = ""
        if self._sheet and getattr(self._sheet, 'source_path', ""):
            sheet_path = self._sheet.source_path
        else:
            idx = self._sheet_combo.currentIndex()
            sheet_path = self._sheet_combo.itemData(idx) if idx >= 0 else ""
        if sheet_path and _os.path.isfile(sheet_path):
            try:
                from PyQt6.QtGui import QImage
                from core.gba_image_utils import export_indexed_png
                disk_img = QImage(sheet_path)
                if not disk_img.isNull():
                    if disk_img.format() != QImage.Format.Format_Indexed8:
                        disk_img = disk_img.convertToFormat(
                            QImage.Format.Format_Indexed8)
                    if disk_img.format() == QImage.Format.Format_Indexed8:
                        # For multi-palette sheets, bake the flat 256-color
                        # table. For single-palette sheets, just the 16.
                        ct_colors = (out_colors
                                     if len(out_colors) > 16 or n_loaded > 1
                                     else out_colors[:16])
                        if export_indexed_png(disk_img, ct_colors, sheet_path,
                                              transparent_index=-1):
                            wrote.append(_os.path.basename(sheet_path))
                        else:
                            errors.append(
                                f"{_os.path.basename(sheet_path)}: "
                                f"refused (not indexed)"
                            )
            except Exception as exc:
                errors.append(f"{_os.path.basename(sheet_path)}: {exc}")

        return wrote, errors

    def _on_reveal_in_folder(self):
        """Open the OS file manager with the current tilemap selected."""
        if not self._tilemap or not self._tilemap.source_path:
            return
        from ui.open_folder_util import open_in_folder
        if not open_in_folder(self._tilemap.source_path):
            QMessageBox.warning(
                self, "Open in Folder",
                f"Could not open folder for:\n{self._tilemap.source_path}")

    def _on_apply_palette_to_png(self):
        """Bake the editor's current palette into an external PNG.

        Two paths depending on the source PNG:
          - Already Indexed8: keep pixel indices exactly, just replace
            the color table (lossless — pixel-art stays pixel-art).
          - RGB: remap pixels to nearest colour in the current palette
            (lossy — produces noise on continuous-tone sources, but
            preserves intent for indexed-style art that happened to be
            saved as RGB).

        Result PNG opens in GIMP with the editor's colours as its swatches.
        """
        if not self._palettes or self._palettes.palette_count() == 0:
            QMessageBox.warning(
                self, "Bake Palette into External PNG",
                "No palette loaded — open a tilemap first or import a "
                ".pal so there's a palette to apply.")
            return

        # Build the flat color list from the current palette set.
        if hasattr(self._palettes, 'get_flat_colors'):
            flat = self._palettes.get_flat_colors()
        else:
            flat = []
            for i in range(self._palettes.palette_count()):
                pal = self._palettes.palettes[i]
                flat.extend(pal[:16])
                while len(flat) % 16 != 0:
                    flat.append((0, 0, 0))
        n_loaded = (
            self._palettes.loaded_slot_count()
            if hasattr(self._palettes, 'loaded_slot_count')
            else self._palettes.palette_count()
        )
        n_colors = max(16, n_loaded * 16)
        n_colors = min(n_colors, len(flat))
        out_pal = list(flat[:n_colors])

        # Pick source PNG.
        # NOTE: the system "Open" button on the dialog is misleading —
        # we're not loading a palette FROM the PNG, we're choosing which
        # external PNG to RECOLOUR with the editor's current palette.
        # The dialog title spells this out so users don't think they're
        # picking a source-of-truth.
        start_dir = self._last_open_dir or self._project_dir or ""
        src_path, _ = QFileDialog.getOpenFileName(
            self, "Step 1 of 2 — Choose external PNG to recolour with current palette",
            start_dir,
            "PNG Images (*.png);;All files (*)",
        )
        if not src_path:
            return

        from PyQt6.QtGui import QImage
        src = QImage(src_path)
        if src.isNull():
            QMessageBox.warning(
                self, "Bake Palette into External PNG",
                f"Couldn't read the PNG:\n{src_path}")
            return

        # Choose path: if source is indexed, keep pixel values; else remap.
        if src.format() == QImage.Format.Format_Indexed8:
            # Bake-only — pixel indices unchanged, color table replaced.
            from core.gba_image_utils import export_indexed_png
            indexed = src
            mode_label = "color table replaced (pixel indices preserved)"
        else:
            # RGB or RGBA → remap to nearest colour in the palette.
            from core.gba_image_utils import remap_to_palette, export_indexed_png
            try:
                indexed = remap_to_palette(src, out_pal, dither=False)
            except Exception as exc:
                QMessageBox.warning(
                    self, "Bake Palette into External PNG",
                    f"Remap failed:\n{exc}")
                return
            mode_label = "pixels remapped to nearest palette colour"

        # Pick destination — default to suggesting `<source>_palette.png`
        # so we don't surprise the user by overwriting their original.
        import os as _os
        base, ext = _os.path.splitext(src_path)
        suggest = f"{base}_palette.png"
        dst_path, _ = QFileDialog.getSaveFileName(
            self, "Step 2 of 2 — Save recoloured PNG as…",
            suggest,
            "PNG Images (*.png);;All files (*)",
        )
        if not dst_path:
            return
        if not dst_path.lower().endswith(".png"):
            dst_path += ".png"

        try:
            ok = export_indexed_png(indexed, out_pal, dst_path,
                                    transparent_index=-1)
        except Exception as exc:
            QMessageBox.warning(
                self, "Bake Palette into External PNG",
                f"Save failed:\n{exc}")
            return
        if not ok:
            QMessageBox.warning(
                self, "Bake Palette into External PNG",
                "Save refused — the resulting image wasn't indexed. "
                "This is a safety check (RGB PNGs break the gbagfx "
                "build step).")
            return

        QMessageBox.information(
            self, "Bake Palette into External PNG",
            f"Wrote <code>{_os.path.basename(dst_path)}</code> "
            f"with {len(out_pal)} colour entries.<br>"
            f"<small>{mode_label}</small><br><br>"
            f"Open it in GIMP and you'll see the editor's current "
            f"palette as the file's swatches.")

    def _on_reveal_sheet_in_folder(self):
        """Open the OS file manager with the currently-selected tile
        sheet (.png) selected — so the user can edit it externally."""
        idx = self._sheet_combo.currentIndex()
        sheet_path = self._sheet_combo.itemData(idx) if idx >= 0 else ""
        if not sheet_path:
            return
        from ui.open_folder_util import open_in_folder
        if not open_in_folder(sheet_path):
            QMessageBox.warning(
                self, "Open Sheet in Folder",
                f"Could not open folder for:\n{sheet_path}")

    def _on_autofix_palettes(self):
        """Bulk-repair tile palette bits from the tile artwork.

        For each tilemap entry, run detect_tile_palette on the tile's
        pixel data and rewrite the entry's palette bits to match. Useful
        when a tilemap was saved before the multi-palette renderer landed
        — those entries have palette=0 stored regardless of which
        sub-palette the artwork was actually drawn with.

        Wrapped as one undo step so the user can revert with Ctrl+Z if
        the result isn't what they wanted.
        """
        if not self._tilemap or not self._sheet or not self._palettes:
            return
        if not self._sheet.is_8bpp or self._palettes.palette_count() <= 1:
            QMessageBox.information(
                self, "Auto-Fix Palettes",
                "This tilemap doesn't need a fix — it's not an 8bpp + "
                "multi-palette sheet (the only case where stored palette "
                "bits can drift from the artwork).")
            return

        from core.tilemap_data import detect_tile_palette
        from PyQt6.QtWidgets import QMessageBox as _MB

        # Pre-flight count so the user knows what they're committing to.
        # Build a stroke dict of OLD entries for cells that will change
        # — single undo step covers the whole pass.
        proposed: dict = {}  # (col,row) -> new palette index
        old_entries: dict = {}
        cache: dict = {}     # tile_index -> detected palette (memoize)
        for row in range(self._tilemap.height):
            for col in range(self._tilemap.width):
                entry = self._tilemap.get(col, row)
                idx = entry.tile_index
                if idx in cache:
                    detected = cache[idx]
                else:
                    try:
                        local_idx = idx - self._tile_offset
                        if local_idx < 0 or local_idx >= self._sheet.tile_count:
                            cache[idx] = entry.palette
                            continue
                        tile_img = self._sheet.get_tile_image(local_idx, False, False)
                        detected = detect_tile_palette(tile_img)
                    except Exception:
                        cache[idx] = entry.palette
                        continue
                    cache[idx] = detected
                if detected != entry.palette:
                    proposed[(col, row)] = detected
                    old_entries[(col, row)] = self._copy_entry(entry)

        if not proposed:
            _MB.information(
                self, "Auto-Fix Palettes",
                "No changes needed — every tile's stored palette already "
                "matches its dominant artwork range.")
            return

        reply = _MB.question(
            self, "Auto-Fix Palettes",
            f"{len(proposed)} tile cell(s) have a stored palette that "
            f"doesn't match the artwork's dominant sub-palette range.\n\n"
            f"Auto-fix all of them in one undo step?")
        if reply != _MB.StandardButton.Yes:
            return

        from core.tilemap_data import TileEntry
        for (col, row), new_pal in proposed.items():
            entry = self._tilemap.get(col, row)
            new_entry = TileEntry(
                tile_index=entry.tile_index,
                hflip=entry.hflip,
                vflip=entry.vflip,
                palette=new_pal,
            )
            self._tilemap.set(col, row, new_entry)
            self._canvas.refresh_tile(col, row)

        # Commit as one undo entry.
        self._undo_stack.append(old_entries)
        if len(self._undo_stack) > self._undo_limit:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        self._dirty = True
        self.modified.emit()
        self._status.setText(
            self._status.text().split(" — ")[0]
            + f" — Auto-fixed {len(proposed)} tile palette(s)")

    def has_unsaved_changes(self) -> bool:
        # The Palette Baker sub-tab tracks its own dirty state (right-
        # panel palette edits). Roll its dirty into ours so the
        # unified save pipeline picks it up automatically.
        baker_dirty = (
            hasattr(self, '_palette_baker')
            and self._palette_baker.has_unsaved_changes()
        )
        return self._dirty or self._palette_dirty or baker_dirty

    def flush_to_disk(self) -> tuple:
        """Save the tilemap .bin file AND any palette edits.

        Palette flush writes back to every source `.pal`/`.gbapal` the
        palette was loaded from AND bakes the new colors into the tile
        sheet PNG's color table so opening the .png in GIMP shows the
        colors the editor / game render with.
        """
        if not self._tilemap or not self._tilemap.source_path:
            return (0, [])
        if not self._dirty and not self._palette_dirty:
            return (0, [])
        ok = 0
        errors: list[str] = []
        try:
            self._tilemap.save()
            # Persist W/H so the next load doesn't lose the user's
            # chosen layout (see _save_file for full reasoning).
            try:
                from core.tilemap_data import set_tilemap_dim_pref
                set_tilemap_dim_pref(
                    self._project_dir, self._tilemap.source_path,
                    self._tilemap.width, self._tilemap.height,
                )
            except Exception:
                pass
            ok += 1
            self.tilemap_saved.emit(self._tilemap.source_path)
        except Exception as e:
            errors.append(f"tilemap: {e}")
        if self._palette_dirty:
            wrote, perrs = self._flush_palette_edits()
            ok += len(wrote)
            errors.extend(f"palette {e}" for e in perrs)
        # Palette Baker — flush its right-panel dirty palette if any.
        if hasattr(self, '_palette_baker') \
                and self._palette_baker.has_unsaved_changes():
            try:
                bok, berrs = self._palette_baker.flush_to_disk()
                ok += bok
                errors.extend(f"palette-baker: {e}" for e in berrs)
            except Exception as e:
                errors.append(f"palette-baker: {e}")
        self._dirty = False
        self._palette_dirty = False
        return (ok, errors)

    def _on_sheet_changed(self, idx: int):
        if idx < 0:
            return
        path = self._sheet_combo.itemData(idx)
        if not path:
            return
        from core.tilemap_data import TileSheet, PaletteSet
        try:
            self._sheet = TileSheet.from_file(path)
        except Exception:
            return

        # If palette source is "png", re-extract from new sheet
        source = self._pal_source_combo.itemData(
            self._pal_source_combo.currentIndex())
        if source == "png":
            self._palettes = PaletteSet.from_indexed_image(self._sheet.image)

        # Re-extract palette from new sheet if no .pal files loaded
        if not self._palettes or self._palettes.palette_count() == 0:
            self._palettes = PaletteSet.from_indexed_image(self._sheet.image)

        # Update palette spinner availability based on bpp mode
        is_8bpp = self._sheet.is_8bpp
        self._pal_spin.setEnabled(not is_8bpp)
        if is_8bpp:
            self._pal_spin.setToolTip(
                "Palette slot is ignored in 8bpp mode.\n"
                "All 256 colors are used directly from the full palette."
            )
        else:
            self._pal_spin.setToolTip("")

        self._refresh_canvas()
        self._picker.set_sheet(self._sheet, self._palettes)
        self._update_pal_editor()

    # ── Editing ──────────────────────────────────────────────────────────────

    def _set_tool(self, tool: str):
        self._tool = tool
        self._btn_paint.setChecked(tool == "paint")
        self._btn_pick.setChecked(tool == "pick")

    def _eyedrop_tile(self, col: int, row: int):
        """Pick the tile at (col, row) and load it as the current paint
        tile — sets tile index, palette, hflip, vflip, and refreshes the
        picker selection + preview. Resets any active multi-tile stamp
        back to 1×1 (a single-tile pick is a single-tile commitment)."""
        if not self._tilemap:
            return
        entry = self._tilemap.get(col, row)
        self._current_tile = entry.tile_index
        self._current_pal = entry.palette
        self._hflip = entry.hflip
        self._vflip = entry.vflip
        self._update_tile_info()
        self._picker._selected = entry.tile_index
        self._picker.update()
        self._reset_stamp_to_single()

    def _on_canvas_eyedrop(self, col: int, row: int):
        """Right-click handler — eyedrop regardless of current tool mode."""
        self._eyedrop_tile(col, row)

    # ── Stamp helpers ─────────────────────────────────────────────────────

    def _current_single_entry(self):
        """Build a TileEntry for the toolbar's current single-tile state
        (tile index, palette, flips). Used both as the 1×1 stamp and as
        the fill replacement."""
        from core.tilemap_data import TileEntry
        return TileEntry(
            tile_index=self._current_tile,
            hflip=self._hflip,
            vflip=self._vflip,
            palette=self._current_pal,
        )

    def _reset_stamp_to_single(self) -> None:
        """Drop the multi-tile stamp back to 1×1 from the toolbar's
        current single-tile state. Idempotent — safe to call any time
        a single-tile action runs (picker click, eyedrop, pal/flip
        toggle, project reload)."""
        self._stamp_grid = [[self._current_single_entry()]]
        self._stamp_w = 1
        self._stamp_h = 1
        self._update_stamp_badge()
        if hasattr(self, '_canvas'):
            self._canvas.set_hover_stamp_size(1, 1)

    def _update_stamp_badge(self) -> None:
        """Update the toolbar badge label to reflect the active stamp
        size. Bolder amber styling when multi-tile so the user notices
        they're in stamp mode."""
        if not hasattr(self, '_stamp_badge'):
            return
        self._stamp_badge.setText(f"Stamp: {self._stamp_w}×{self._stamp_h}")
        if self._stamp_w == 1 and self._stamp_h == 1:
            self._stamp_badge.setStyleSheet(
                "QLabel { color: #888; padding: 2px 6px; "
                "border: 1px solid #444; border-radius: 3px; "
                "background: #2a2a2a; }")
        else:
            self._stamp_badge.setStyleSheet(
                "QLabel { color: #ffb74d; padding: 2px 6px; "
                "border: 1px solid #ffb74d; border-radius: 3px; "
                "background: #2a2a2a; font-weight: bold; }")

    def _on_canvas_region_selected(
            self, c0: int, r0: int, c1: int, r1: int) -> None:
        """Shift+right-drag in the canvas: capture the rectangular
        region of the current tilemap as the active stamp. Each cell's
        tile index, palette, and flips are copied verbatim — pasting
        the stamp later reproduces the source region exactly."""
        if not self._tilemap:
            return
        self._stamp_grid = []
        for r in range(r0, r1 + 1):
            row_entries = []
            for c in range(c0, c1 + 1):
                src = self._tilemap.get(c, r)
                row_entries.append(self._copy_entry(src))
            self._stamp_grid.append(row_entries)
        self._stamp_w = c1 - c0 + 1
        self._stamp_h = r1 - r0 + 1
        self._update_stamp_badge()
        self._canvas.set_hover_stamp_size(self._stamp_w, self._stamp_h)

    def _on_picker_region_selected(
            self, c0: int, r0: int, c1: int, r1: int) -> None:
        """Shift+right-drag in the picker: build a stamp from a
        rectangular region of the source tile sheet. Each cell uses the
        toolbar's current palette / flips (the sheet has no per-tile
        flip flags), with the tile index taken from the sheet grid.
        """
        if not self._sheet:
            return
        from core.tilemap_data import TileEntry
        tw = self._sheet.tiles_wide
        self._stamp_grid = []
        for r in range(r0, r1 + 1):
            row_entries = []
            for c in range(c0, c1 + 1):
                tile_idx = r * tw + c
                # Fall back to MOVE-equivalent of "off-sheet" — empty
                # entries get a 0-index entry. In practice the picker
                # rejects out-of-bounds picks in _cell_at().
                if tile_idx < 0 or tile_idx >= self._sheet.tile_count:
                    row_entries.append(TileEntry(
                        tile_index=0, hflip=False,
                        vflip=False, palette=self._current_pal))
                else:
                    row_entries.append(TileEntry(
                        tile_index=tile_idx,
                        hflip=self._hflip, vflip=self._vflip,
                        palette=self._current_pal))
            self._stamp_grid.append(row_entries)
        self._stamp_w = c1 - c0 + 1
        self._stamp_h = r1 - r0 + 1
        self._update_stamp_badge()
        self._canvas.set_hover_stamp_size(self._stamp_w, self._stamp_h)
        # Update the "current tile" + picker selection to the stamp's
        # top-left so the toolbar stays consistent. Doesn't reset the
        # stamp — that only happens on a single-tile pick (left-click
        # in the picker), not on a stamp grab.
        top_left = self._stamp_grid[0][0]
        self._current_tile = top_left.tile_index
        self._picker._selected = top_left.tile_index
        self._picker.update()
        self._update_tile_info()

    def _on_canvas_fill_requested(self, col: int, row: int) -> None:
        """Middle-click on the canvas: 4-connected flood-fill from
        (col, row) replacing every connected cell whose tile_index +
        flips + palette match the clicked cell with the current
        SINGLE tile. The active multi-tile stamp is intentionally
        ignored — fill always uses single-tile state to avoid
        producing wallpaper-pattern artifacts at region boundaries.
        Counts as one undo step.
        """
        if not self._tilemap:
            return
        target = self._tilemap.get(col, row)
        replacement = self._current_single_entry()
        # No-op if the click cell already has the replacement state —
        # nothing to fill, and we'd just dirty the file.
        if self._entries_equal(target, replacement):
            return
        # BFS so we don't blow the recursion stack on big maps.
        from collections import deque
        visited: set[tuple[int, int]] = set()
        queue: deque[tuple[int, int]] = deque()
        queue.append((col, row))
        # Snapshot for undo as we go. This whole fill is one stroke,
        # committed via _on_stroke_finished after the BFS completes.
        old_entries: dict[tuple[int, int], object] = {}
        w, h = self._tilemap.width, self._tilemap.height
        while queue:
            c, r = queue.popleft()
            if (c, r) in visited:
                continue
            visited.add((c, r))
            if c < 0 or r < 0 or c >= w or r >= h:
                continue
            cell = self._tilemap.get(c, r)
            if not self._entries_equal(cell, target):
                continue
            old_entries[(c, r)] = self._copy_entry(cell)
            self._tilemap.set(c, r, replacement)
            self._canvas.refresh_tile(c, r)
            queue.append((c + 1, r))
            queue.append((c - 1, r))
            queue.append((c, r + 1))
            queue.append((c, r - 1))
        if not old_entries:
            return
        # Commit the fill as a single undo entry — re-using the
        # existing undo stack the same way a paint stroke does.
        self._undo_stack.append(old_entries)
        if len(self._undo_stack) > self._undo_limit:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        self._dirty = True
        self.modified.emit()

    def _paint_tile(self, col: int, row: int):
        """Called when canvas is clicked/dragged in paint mode.

        For a 1×1 stamp this places one tile, the same as the original
        single-tile behaviour. For a multi-tile stamp (W×H), the stamp
        is anchored at (col, row) and writes one TileEntry per stamp
        cell that falls inside the tilemap bounds (cells past the
        right or bottom edge are silently clipped). All written cells
        belong to the same drag stroke and commit as a single undo
        entry on mouse release.
        """
        if not self._tilemap:
            return

        if self._tool == "pick":
            # Pick-tool mode (left-click eyedrop) — same as right-click eyedrop.
            self._eyedrop_tile(col, row)
            return

        # Paint mode: stamp the active grid anchored at (col, row).
        # When the stamp is 1×1 (the default), this is identical to
        # the original single-tile paint path.
        if not self._stamp_grid:
            self._stamp_grid = [[self._current_single_entry()]]
        for sr, row_entries in enumerate(self._stamp_grid):
            for sc, src_entry in enumerate(row_entries):
                tc = col + sc
                tr = row + sr
                if tc >= self._tilemap.width or tr >= self._tilemap.height:
                    continue
                if tc < 0 or tr < 0:
                    continue
                # Record the OLD entry once per cell per stroke (a drag
                # may revisit the same cell; we only keep the entry
                # that was there before the WHOLE stroke started).
                if (tc, tr) not in self._current_stroke:
                    old = self._tilemap.get(tc, tr)
                    self._current_stroke[(tc, tr)] = self._copy_entry(old)
                # No-op when nothing would change at this cell.
                existing = self._tilemap.get(tc, tr)
                if self._entries_equal(existing, src_entry):
                    continue
                self._tilemap.set(tc, tr, self._copy_entry(src_entry))
                self._canvas.refresh_tile(tc, tr)
                self._dirty = True
        self.modified.emit()

    # ── Undo / redo ──────────────────────────────────────────────────────────

    @staticmethod
    def _copy_entry(entry):
        from core.tilemap_data import TileEntry
        return TileEntry(
            tile_index=entry.tile_index,
            hflip=entry.hflip,
            vflip=entry.vflip,
            palette=entry.palette,
        )

    @staticmethod
    def _entries_equal(a, b) -> bool:
        return (a.tile_index == b.tile_index and a.hflip == b.hflip
                and a.vflip == b.vflip and a.palette == b.palette)

    def _install_undo_shortcuts(self):
        from PyQt6.QtGui import QShortcut, QKeySequence
        from PyQt6.QtCore import Qt as _Qt
        # Ctrl+Z = undo, Ctrl+Y AND Ctrl+Shift+Z = redo.
        QShortcut(QKeySequence.StandardKey.Undo, self,
                  activated=self._undo,
                  context=_Qt.ShortcutContext.WidgetWithChildrenShortcut)
        QShortcut(QKeySequence.StandardKey.Redo, self,
                  activated=self._redo,
                  context=_Qt.ShortcutContext.WidgetWithChildrenShortcut)
        # QKeySequence.Redo on Windows is Ctrl+Y; on Mac/Linux it's
        # Ctrl+Shift+Z. Add an explicit Ctrl+Y too so it works everywhere.
        QShortcut(QKeySequence("Ctrl+Y"), self,
                  activated=self._redo,
                  context=_Qt.ShortcutContext.WidgetWithChildrenShortcut)

    def _on_stroke_finished(self):
        """Called after the user releases the mouse from a paint drag.
        Commit the stroke (old entries for every modified cell) to the
        undo stack as ONE step. Clear the redo stack — new edits
        invalidate the redo history."""
        if not self._current_stroke:
            return
        # Filter out cells where the stroke didn't actually change anything
        # (e.g. user clicked a cell and the new entry matched the old).
        actual = {}
        for (col, row), old_entry in self._current_stroke.items():
            current = self._tilemap.get(col, row)
            if not self._entries_equal(current, old_entry):
                actual[(col, row)] = old_entry
        self._current_stroke = {}
        if not actual:
            return
        self._undo_stack.append(actual)
        if len(self._undo_stack) > self._undo_limit:
            self._undo_stack.pop(0)
        self._redo_stack.clear()

    def _apply_step(self, step: dict) -> dict:
        """Apply a step (dict of {(col,row): TileEntry}) to the tilemap and
        return the inverse (entries that WERE there before this step ran).
        Used by both undo and redo."""
        inverse = {}
        for (col, row), entry in step.items():
            current = self._tilemap.get(col, row)
            inverse[(col, row)] = self._copy_entry(current)
            self._tilemap.set(col, row, entry)
            self._canvas.refresh_tile(col, row)
        return inverse

    def _undo(self):
        if not self._undo_stack:
            self._status.setText(
                self._status.text().split(" — ")[0] + " — Nothing to undo")
            return
        step = self._undo_stack.pop()
        inverse = self._apply_step(step)
        self._redo_stack.append(inverse)
        self._dirty = True
        self.modified.emit()
        self._status.setText(
            self._status.text().split(" — ")[0]
            + f" — Undo ({len(step)} cell(s))")

    def _redo(self):
        if not self._redo_stack:
            self._status.setText(
                self._status.text().split(" — ")[0] + " — Nothing to redo")
            return
        step = self._redo_stack.pop()
        inverse = self._apply_step(step)
        self._undo_stack.append(inverse)
        self._dirty = True
        self.modified.emit()
        self._status.setText(
            self._status.text().split(" — ")[0]
            + f" — Redo ({len(step)} cell(s))")

    def _on_canvas_click(self, col: int, row: int):
        """Show info for clicked tile."""
        if not self._tilemap:
            return
        entry = self._tilemap.get(col, row)
        self._status.setText(
            self._status.text().split(" — Tile")[0]
            + f" — Tile ({col},{row}): idx={entry.tile_index}"
            f" pal={entry.palette}"
            f" {'H' if entry.hflip else ''}"
            f" {'V' if entry.vflip else ''}"
        )

    def _on_canvas_hover(self, col: int, row: int):
        pass  # Could show coords in status

    def _on_tile_picked(self, idx: int):
        # Single-click in the picker is a single-tile commitment —
        # collapses any active multi-tile stamp back to 1×1.
        self._current_tile = idx
        # When the sheet is 8bpp + multi-palette (region-map style), the
        # picker tile's pixel values directly encode which sub-palette it
        # was baked from. Auto-detect that sub-palette so painting carries
        # the right attr-palette over to the tilemap entry — no manual
        # spinner-twiddling per tile.
        if (self._sheet and self._sheet.is_8bpp
                and self._palettes is not None
                and self._palettes.palette_count() > 1):
            try:
                from core.tilemap_data import detect_tile_palette
                tile_img = self._sheet.get_tile_image(idx, False, False)
                detected = detect_tile_palette(tile_img)
                if detected != self._current_pal:
                    self._current_pal = detected
                    self._pal_spin.blockSignals(True)
                    self._pal_spin.setValue(detected)
                    self._pal_spin.blockSignals(False)
                    self._picker.set_palette_index(detected)
            except Exception:
                pass
        self._update_tile_info()
        self._reset_stamp_to_single()

    def _on_pal_changed(self, val: int):
        # Toggling pal/H/V is a single-tile action (it conceptually
        # only applies to one tile); collapse the stamp accordingly so
        # the painted result matches the toolbar state the user just
        # changed.
        self._current_pal = val
        self._picker.set_palette_index(val)
        self._update_tile_preview()
        self._reset_stamp_to_single()

    def _on_hflip_changed(self, checked: bool):
        self._hflip = checked
        self._update_tile_preview()
        self._reset_stamp_to_single()

    def _on_vflip_changed(self, checked: bool):
        self._vflip = checked
        self._update_tile_preview()
        self._reset_stamp_to_single()

    def _on_zoom_changed(self, val: int):
        self._canvas.set_zoom(val)

    def _on_grid_toggled(self, checked: bool):
        self._canvas.set_show_grid(checked)

    def _on_offset_changed(self, val: int):
        self._tile_offset = val
        self._refresh_canvas()

    def _on_pal_source_changed(self, idx: int):
        """Switch between .pal file palettes and tile sheet embedded palette."""
        if not self._sheet:
            return
        source = self._pal_source_combo.itemData(idx)
        if source == "png":
            from core.tilemap_data import PaletteSet
            self._palettes = PaletteSet.from_indexed_image(self._sheet.image)
        else:
            # Reload from .pal files
            self._reload_pal_files()
        self._refresh_canvas()
        self._picker.set_sheet(self._sheet, self._palettes)
        self._update_pal_editor()

    def _on_palette_edited(self):
        """Called when the palette editor widget changes a palette slot."""
        self._refresh_canvas()
        self._picker.set_sheet(self._sheet, self._palettes)
        # Mark palette dirty so save() will write the palette back to
        # disk AND bake into the sheet PNG. Also set the file-dirty flag
        # so the toolbar dot lights up + Save All picks this up.
        self._palette_dirty = True
        self._dirty = True
        self.modified.emit()

    def _on_import_pal_clicked(self):
        """Import a .pal file — choose 16-color (to a slot) or 256-color (all slots)."""
        if not self._palettes:
            from core.tilemap_data import PaletteSet
            self._palettes = PaletteSet()

        menu = QMenu(self)
        act_16 = menu.addAction("Import 16-color .pal to a slot...")
        act_256 = menu.addAction("Import 256-color .pal (fills all slots)...")
        action = menu.exec(self.cursor().pos())
        if not action:
            return

        path, _ = QFileDialog.getOpenFileName(
            self, "Import JASC Palette",
            "", "JASC Palette (*.pal);;All files (*)")
        if not path:
            return
        from ui.palette_utils import read_jasc_pal
        colors = read_jasc_pal(path)
        if not colors:
            QMessageBox.warning(self, "Error", "Could not read palette file.")
            return

        if action == act_256:
            # Fill all sub-palette slots from a 256-color file
            for sub in range(0, min(len(colors), 256), 16):
                idx = sub // 16
                chunk = colors[sub:sub + 16]
                while len(chunk) < 16:
                    chunk.append((0, 0, 0))
                self._palettes.set_palette_at(idx, chunk)
        else:
            # 16-color import — pick which slot
            slot, ok = self._ask_palette_slot()
            if not ok:
                return
            self._palettes.set_palette_at(slot, colors[:16])

        # Track the imported file as a palette source so the next Save
        # writes the user's edits back to THIS file (instead of skipping
        # the palette flush silently — which is what happened when the
        # tilemap's palette came from the sheet PNG's color table and
        # the user expected their imported .pal to become the new source).
        if self._palettes.source_paths is None:
            self._palettes.source_paths = []
        if path not in self._palettes.source_paths:
            self._palettes.source_paths.append(path)

        self._refresh_canvas()
        self._picker.set_sheet(self._sheet, self._palettes)
        self._update_pal_editor()

        # Mark dirty so Ctrl+S persists this. Without these three lines
        # the palette only lives in RAM — restart the app and the imported
        # palette is gone, replaced by whatever was baked into the PNG.
        self._palette_dirty = True
        self._dirty = True
        self.modified.emit()

    def _ask_palette_slot(self):
        """Ask the user which palette slot (0-15) to import into."""
        from PyQt6.QtWidgets import QInputDialog
        slot, ok = QInputDialog.getInt(
            self, "Palette Slot",
            "Import to slot (0-15):", 0, 0, 15)
        return slot, ok

    def _on_export_pal_clicked(self):
        """Export palettes — offers separate .pal files or combined 256-color."""
        if not self._palettes or self._palettes.loaded_slot_count() == 0:
            QMessageBox.information(
                self, "Export", "No palettes loaded to export.")
            return
        menu = QMenu(self)
        act_separate = menu.addAction("Export as separate 16-color .pal files...")
        act_combined = menu.addAction("Export as single 256-color .pal file...")
        action = menu.exec(self.cursor().pos())
        if action == act_separate:
            dir_path = QFileDialog.getExistingDirectory(
                self, "Export All Palettes to Directory")
            if not dir_path:
                return
            from ui.palette_utils import write_jasc_pal
            count = 0
            for slot in range(min(16, self._palettes.palette_count())):
                if self._palettes.is_slot_loaded(slot):
                    path = os.path.join(dir_path, f"palette_{slot:02d}.pal")
                    write_jasc_pal(path, self._palettes.palettes[slot])
                    count += 1
            QMessageBox.information(
                self, "Export", f"Exported {count} palette(s) to {dir_path}")
        elif action == act_combined:
            path, _ = QFileDialog.getSaveFileName(
                self, "Export Combined 256-Color Palette",
                "palette_256.pal",
                "JASC Palette (*.pal)")
            if not path:
                return
            from ui.palette_utils import write_jasc_pal
            all_colors = self._palettes.get_flat_colors()
            write_jasc_pal(path, all_colors)
            QMessageBox.information(
                self, "Export", f"Exported 256-color palette to {path}")

    def _reload_pal_files(self):
        """Reload palettes from .pal files associated with the current tilemap."""
        if not self._tilemap or not self._tilemap.source_path:
            return
        from core.tilemap_data import PaletteSet, discover_assets
        assets = discover_assets(self._tilemap.source_path)
        if assets.best_pals:
            self._palettes = PaletteSet.from_pal_files(assets.best_pals)
        else:
            self._palettes = PaletteSet()

    def _refresh_canvas(self):
        """Re-render the canvas with current tile offset and palettes."""
        if self._tilemap and self._sheet:
            self._canvas.set_data(
                self._tilemap, self._sheet, self._palettes,
                tile_offset=self._tile_offset)

    def _update_pal_editor(self):
        """Update the visual palette editor widget."""
        pals_used = set()
        if self._tilemap:
            for e in self._tilemap.entries:
                pals_used.add(e.palette)
        self._pal_editor.set_pals_used(pals_used)
        self._pal_editor.set_palette_set(self._palettes)

        # Update header label
        is_8bpp = self._sheet and self._sheet.is_8bpp
        if is_8bpp:
            self._pal_header_label.setText("Palettes (256-color)")
        else:
            self._pal_header_label.setText("Palettes (16-color)")

    def _on_dimensions_changed(self):
        """Live visual REWRAP — scrubbing W or H reflows the same entries
        across a new row stride, with the OTHER axis auto-recalculating
        to fit. Total entry count is preserved. No data is lost.

        Use case: an opened .bin came in with the wrong auto-detected
        width and the visual is jumbled. Drag W and watch the canvas
        re-flow until it looks right.

        For changing the tile COUNT (truncate or pad to a specific size),
        the Resize… button is the explicit operation.

        DO NOT regress: an earlier version of this method padded
        ``entries`` up to ``new_w * new_h`` on every spinner change
        (via ceiling-division of the total). Scrubbing W from 64 to 65
        and back to 64 ratcheted the entry count higher each round
        trip — and ``Tilemap.save()`` then wrote those surplus entries,
        producing a `.bin` larger than the declared dimensions. At
        runtime ``CopyToBgTilemapBuffer``'s LZ decompressor overflowed
        BG0's tilemap buffer and corrupted WRAM, crashing on entry to
        battle. The fix here is to leave ``entries`` untouched on
        rewrap — only ``width`` and ``height`` change. The data list
        keeps exactly the count it had on load, so the round trip is
        truly lossless. Cells past the entry list (when ``new_w *
        new_h > len(entries)``) read as default ``TileEntry`` via
        ``Tilemap.get`` — same visual outcome the old "pad with
        blanks" path produced, without mutating the entries list.
        """
        if not self._tilemap:
            return
        new_w = self._width_spin.value()
        new_h = self._height_spin.value()
        if new_w == self._tilemap.width and new_h == self._tilemap.height:
            return

        # Total entry count — preserved across the rewrap.
        total = len(self._tilemap.entries)

        # Whichever spinner the user moved drives; the other recalculates
        # to the smallest height/width that COULD fit the existing
        # entries at the new stride. Ceiling division — same as before.
        if new_w != self._tilemap.width:
            new_h = max(1, (total + new_w - 1) // new_w)
            self._height_spin.blockSignals(True)
            self._height_spin.setValue(new_h)
            self._height_spin.blockSignals(False)
        elif new_h != self._tilemap.height:
            new_w = max(1, (total + new_h - 1) // new_h)
            self._width_spin.blockSignals(True)
            self._width_spin.setValue(new_w)
            self._width_spin.blockSignals(False)

        # Update the dimensions ONLY. Do not mutate ``entries`` — the
        # rewrap is lossless and reversible because the entry list is
        # the source of truth, not the W/H product. ``Tilemap.save``
        # writes exactly ``width * height * 2`` bytes regardless.
        self._tilemap.width = new_w
        self._tilemap.height = new_h
        self._refresh_canvas()

    def _on_resize(self):
        """Explicit RESIZE — change the tilemap's actual tile count.

        Pops a small dialog with W and H inputs (defaulting to current
        dimensions). Result is exactly W*H entries:
          - Shrink → truncate entries past the new bounds. If any of
            the dropped tiles aren't blank, confirm before committing.
          - Grow → pad with blank tiles at the end (top-left content
            preserved).

        Driving use case: partial-screen UIs (smaller dialogue boxes,
        custom HUD strips) that need the .bin saved at a specific size
        the engine reads. Different operation from the live rewrap on
        the toolbar W/H spinners — that one preserves entry count.
        """
        if not self._tilemap:
            return
        from PyQt6.QtWidgets import (
            QDialog, QDialogButtonBox, QFormLayout, QSpinBox, QLabel,
        )

        old_w = self._tilemap.width
        old_h = self._tilemap.height
        old_count = len(self._tilemap.entries)

        dlg = QDialog(self)
        dlg.setWindowTitle("Resize Tilemap")
        form = QFormLayout(dlg)
        info = QLabel(
            f"Currently <b>{old_w}×{old_h}</b> = {old_count} tiles.<br>"
            f"Pick the new dimensions. Shrinking truncates; growing "
            f"pads with blank tiles. Top-left content is preserved."
        )
        info.setWordWrap(True)
        form.addRow(info)
        w_spin = QSpinBox()
        w_spin.setRange(1, 1024)
        w_spin.setValue(old_w)
        h_spin = QSpinBox()
        h_spin.setRange(1, 1024)
        h_spin.setValue(old_h)
        live = QLabel("")
        live.setStyleSheet("color: #aaa;")

        def _refresh_live():
            new_total = w_spin.value() * h_spin.value()
            delta = new_total - old_count
            if delta == 0:
                live.setText(f"Result: {new_total} tiles (same total)")
            elif delta > 0:
                live.setText(
                    f"Result: {new_total} tiles "
                    f"(+{delta} blank tiles padded)"
                )
            else:
                live.setText(
                    f"<span style='color:#e88;'>Result: {new_total} "
                    f"tiles ({-delta} dropped — will confirm if any "
                    f"are painted)</span>"
                )
        w_spin.valueChanged.connect(_refresh_live)
        h_spin.valueChanged.connect(_refresh_live)
        _refresh_live()

        form.addRow("Width (tiles):", w_spin)
        form.addRow("Height (tiles):", h_spin)
        form.addRow(live)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new_w = w_spin.value()
        new_h = h_spin.value()
        if new_w == old_w and new_h == old_h:
            return

        from core.tilemap_data import TileEntry

        old_entries = list(self._tilemap.entries)
        new_count = new_w * new_h

        # Build new grid: copy overlapping top-left rectangle, pad rest.
        new_entries: list = [TileEntry() for _ in range(new_count)]
        copy_w = min(new_w, old_w)
        copy_h = min(new_h, old_h)
        for r in range(copy_h):
            for c in range(copy_w):
                src = r * old_w + c
                dst = r * new_w + c
                if src < old_count:
                    new_entries[dst] = old_entries[src]

        # Confirm if shrinking would drop painted tiles.
        if new_count < old_count:
            default = TileEntry()
            def _is_default(e):
                return (e.tile_index == default.tile_index
                        and e.hflip == default.hflip
                        and e.vflip == default.vflip
                        and e.palette == default.palette)
            dropped_painted = False
            for idx in range(old_count):
                old_r, old_c = divmod(idx, old_w)
                if old_r < copy_h and old_c < copy_w:
                    continue
                if not _is_default(old_entries[idx]):
                    dropped_painted = True
                    break
            if dropped_painted:
                reply = QMessageBox.warning(
                    self, "Resize Tilemap",
                    f"Shrinking {old_w}×{old_h} → {new_w}×{new_h} will "
                    f"discard painted tiles outside the new bounds.\n\n"
                    f"Continue?",
                    QMessageBox.StandardButton.Ok
                    | QMessageBox.StandardButton.Cancel,
                )
                if reply != QMessageBox.StandardButton.Ok:
                    return

        self._tilemap.width = new_w
        self._tilemap.height = new_h
        self._tilemap.entries = new_entries

        # Sync toolbar spinners to the new dimensions, suppressing their
        # rewrap handler (we just resized — no rewrap needed).
        self._width_spin.blockSignals(True)
        self._height_spin.blockSignals(True)
        self._width_spin.setValue(new_w)
        self._height_spin.setValue(new_h)
        self._width_spin.blockSignals(False)
        self._height_spin.blockSignals(False)

        self._dirty = True
        self.modified.emit()
        self._refresh_canvas()
        self._status.setText(
            self._status.text().split(" — ")[0]
            + f" — Resized to {new_w}×{new_h} ({new_count} tiles)"
        )

    def _update_tile_info(self):
        self._tile_idx_label.setText(str(self._current_tile))
        self._pal_spin.blockSignals(True)
        self._pal_spin.setValue(self._current_pal)
        self._pal_spin.blockSignals(False)
        self._hflip_check.blockSignals(True)
        self._hflip_check.setChecked(self._hflip)
        self._hflip_check.blockSignals(False)
        self._vflip_check.blockSignals(True)
        self._vflip_check.setChecked(self._vflip)
        self._vflip_check.blockSignals(False)
        self._update_tile_preview()

    def _update_tile_preview(self):
        if not self._sheet:
            return
        tile = self._sheet.get_tile_image(
            self._current_tile, self._hflip, self._vflip)
        if (self._palettes and self._palettes.palette_count() > 0
                and tile.format() == QImage.Format.Format_Indexed8):
            if self._sheet.is_8bpp:
                from core.tilemap_data import (
                    _recolor_tile_8bpp, build_flat_color_table,
                )
                flat_ct = build_flat_color_table(self._palettes)
                tile = _recolor_tile_8bpp(tile, flat_ct)
            else:
                from core.tilemap_data import _recolor_tile
                tile = _recolor_tile(tile, self._current_pal, self._palettes)
        # Scale up for preview (matches the 32x32 inline preview widget)
        scaled = tile.scaled(
            32, 32,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        self._tile_preview.setPixmap(QPixmap.fromImage(scaled))
