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
    QToolBar, QVBoxLayout, QWidget,
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
    """Shows 16 palette slots as color swatch rows with import/export.

    Each slot is a row of 16 colored squares. Slots used by the current
    tilemap are highlighted. Right-click any slot for import/export options.
    """

    palette_changed = pyqtSignal()  # emitted when any palette slot changes

    def __init__(self, parent=None):
        super().__init__(parent)
        self._palette_set = None
        self._pals_used: set = set()  # palette indices the tilemap uses
        self._selected_slot = 0
        self.setMinimumWidth(16 * SWATCH + 60)
        self.setMinimumHeight(16 * (SWATCH + 2) + 4)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

    def set_palette_set(self, ps):
        self._palette_set = ps
        self.update()

    def set_pals_used(self, used: set):
        self._pals_used = used
        self.update()

    def palette_set(self):
        return self._palette_set

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        sw = SWATCH
        label_w = 36  # width for "P00:" label

        for slot in range(16):
            y = slot * (sw + 2) + 2
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
        return max(0, min(15, (y - 2) // (SWATCH + 2)))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._selected_slot = self._slot_at_y(event.pos().y())
            self.update()

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
        if colors:
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


# ═══════════════════════════════════════════════════════════════════════════════
#  Tilemap Canvas — the main editable view
# ═══════════════════════════════════════════════════════════════════════════════


class TilemapCanvas(QWidget):
    """Renders and allows editing of a tilemap."""

    tile_clicked = pyqtSignal(int, int)  # col, row
    tile_hovered = pyqtSignal(int, int)  # col, row

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
        from core.tilemap_data import _recolor_tile
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
            tile_img = _recolor_tile(tile_img, entry.palette, self._palettes)

        painter = QPainter(self._rendered)
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

        p.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            col, row = self._tile_at(event.pos())
            if col >= 0:
                self._painting = True
                self.tile_clicked.emit(col, row)
                if self._paint_callback:
                    self._paint_callback(col, row)

    def mouseMoveEvent(self, event):
        col, row = self._tile_at(event.pos())
        if col >= 0:
            self.tile_hovered.emit(col, row)
        if self._painting and event.buttons() & Qt.MouseButton.LeftButton:
            if col >= 0 and self._paint_callback:
                self._paint_callback(col, row)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._painting = False

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

    tile_selected = pyqtSignal(int)  # tile index

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sheet = None
        self._palettes = None
        self._zoom = 2
        self._selected = 0
        self._pal_idx = 0
        self.setMouseTracking(True)
        self.setMinimumSize(64, 64)

    def set_sheet(self, sheet, palettes=None):
        self._sheet = sheet
        self._palettes = palettes
        self._selected = 0
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
            # Recolor with selected palette for preview
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

        p.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._sheet:
            z = self._zoom
            col = event.pos().x() // (TILE_PX * z)
            row = event.pos().y() // (TILE_PX * z)
            idx = row * self._sheet.tiles_wide + col
            if 0 <= idx < self._sheet.tile_count:
                self._selected = idx
                self.tile_selected.emit(idx)
                self.update()


# ═══════════════════════════════════════════════════════════════════════════════
#  Main Tab Widget
# ═══════════════════════════════════════════════════════════════════════════════


class TilemapEditorTab(QWidget):
    """Full tilemap editor page for the unified toolbar."""

    modified = pyqtSignal()

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
        self._tool = "paint"  # "paint" or "pick"
        self._tile_offset = 0  # VRAM tile offset for current sheet
        self._build_ui()

    def set_project(self, project_dir: str):
        self._project_dir = project_dir

    # ── UI Construction ──────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

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

        tb.addSeparator()

        # Tile sheet selector
        tb.addWidget(QLabel(" Tile Sheet: "))
        self._sheet_combo = _NoScrollCombo()
        self._sheet_combo.setMinimumWidth(200)
        self._sheet_combo.currentIndexChanged.connect(self._on_sheet_changed)
        tb.addWidget(self._sheet_combo)

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

        # Tilemap dimensions
        tb.addWidget(QLabel(" W: "))
        self._width_spin = QSpinBox()
        self._width_spin.setRange(1, 128)
        self._width_spin.setValue(32)
        self._width_spin.valueChanged.connect(self._on_dimensions_changed)
        tb.addWidget(self._width_spin)

        tb.addWidget(QLabel(" H: "))
        self._height_spin = QSpinBox()
        self._height_spin.setRange(1, 128)
        self._height_spin.setValue(20)
        self._height_spin.valueChanged.connect(self._on_dimensions_changed)
        tb.addWidget(self._height_spin)

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

        root.addWidget(tb)

        # -- Main splitter: canvas left, picker right --
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: tilemap canvas in scroll area
        self._canvas = TilemapCanvas()
        self._canvas.tile_clicked.connect(self._on_canvas_click)
        self._canvas.tile_hovered.connect(self._on_canvas_hover)
        self._canvas.set_paint_callback(self._paint_tile)

        canvas_scroll = QScrollArea()
        canvas_scroll.setWidget(self._canvas)
        canvas_scroll.setWidgetResizable(False)
        canvas_scroll.setMinimumWidth(300)
        splitter.addWidget(canvas_scroll)

        # Right panel: tile picker + controls
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(4, 0, 4, 0)

        # -- Tool mode --
        tool_group = QGroupBox("Tool")
        tool_lay = QHBoxLayout(tool_group)
        self._btn_paint = QPushButton("Paint")
        self._btn_paint.setCheckable(True)
        self._btn_paint.setChecked(True)
        self._btn_paint.clicked.connect(lambda: self._set_tool("paint"))
        self._btn_pick = QPushButton("Pick (Eyedropper)")
        self._btn_pick.setCheckable(True)
        self._btn_pick.clicked.connect(lambda: self._set_tool("pick"))
        tool_lay.addWidget(self._btn_paint)
        tool_lay.addWidget(self._btn_pick)
        right_layout.addWidget(tool_group)

        # -- Current tile info --
        info_group = QGroupBox("Selected Tile")
        info_lay = QVBoxLayout(info_group)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Index:"))
        self._tile_idx_label = QLabel("0")
        self._tile_idx_label.setStyleSheet("font-weight: bold;")
        row1.addWidget(self._tile_idx_label)
        row1.addStretch()
        info_lay.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Palette:"))
        self._pal_spin = QSpinBox()
        self._pal_spin.setRange(0, 15)
        self._pal_spin.valueChanged.connect(self._on_pal_changed)
        row2.addWidget(self._pal_spin)
        row2.addStretch()
        info_lay.addLayout(row2)

        row3 = QHBoxLayout()
        self._hflip_check = QCheckBox("H-Flip")
        self._hflip_check.toggled.connect(self._on_hflip_changed)
        self._vflip_check = QCheckBox("V-Flip")
        self._vflip_check.toggled.connect(self._on_vflip_changed)
        row3.addWidget(self._hflip_check)
        row3.addWidget(self._vflip_check)
        row3.addStretch()
        info_lay.addLayout(row3)

        # Preview of selected tile
        self._tile_preview = QLabel()
        self._tile_preview.setFixedSize(64, 64)
        self._tile_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._tile_preview.setStyleSheet(
            "background: #222; border: 1px solid #555;")
        info_lay.addWidget(self._tile_preview)

        right_layout.addWidget(info_group)

        # -- Tile picker --
        picker_group = QGroupBox("Tile Sheet")
        picker_lay = QVBoxLayout(picker_group)

        self._picker = TilePickerWidget()
        self._picker.tile_selected.connect(self._on_tile_picked)

        picker_scroll = QScrollArea()
        picker_scroll.setWidget(self._picker)
        picker_scroll.setWidgetResizable(False)
        picker_lay.addWidget(picker_scroll)
        right_layout.addWidget(picker_group, 1)

        # -- Palette editor --
        pal_group = QGroupBox("Palettes (right-click slot to import/export .pal)")
        pal_lay = QVBoxLayout(pal_group)

        # Palette source toggle
        pal_src_row = QHBoxLayout()
        pal_src_row.addWidget(QLabel("Source:"))
        self._pal_source_combo = _NoScrollCombo()
        self._pal_source_combo.addItem("Auto (.pal files)", "pal")
        self._pal_source_combo.addItem("Tile sheet colors", "png")
        self._pal_source_combo.currentIndexChanged.connect(self._on_pal_source_changed)
        pal_src_row.addWidget(self._pal_source_combo)
        pal_lay.addLayout(pal_src_row)

        # Visual palette editor in scroll area
        self._pal_editor = PaletteEditorWidget()
        self._pal_editor.palette_changed.connect(self._on_palette_edited)
        pal_scroll = QScrollArea()
        pal_scroll.setWidget(self._pal_editor)
        pal_scroll.setWidgetResizable(False)
        pal_scroll.setMinimumHeight(100)
        pal_lay.addWidget(pal_scroll)

        right_layout.addWidget(pal_group)

        # -- Status --
        self._status = QLabel("No tilemap loaded")
        self._status.setStyleSheet("color: #888; font-size: 11px;")
        right_layout.addWidget(self._status)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

        root.addWidget(splitter, 1)

    # ── File operations ──────────────────────────────────────────────────────

    def _open_file(self):
        start_dir = ""
        if self._project_dir:
            gfx = os.path.join(self._project_dir, "graphics")
            if os.path.isdir(gfx):
                start_dir = gfx
            else:
                start_dir = self._project_dir

        path, _ = QFileDialog.getOpenFileName(
            self, "Open Tilemap",
            start_dir,
            "Tilemap files (*.bin);;All files (*)",
        )
        if not path:
            return

        self._load_tilemap(path)

    def _load_tilemap(self, bin_path: str):
        from core.tilemap_data import (
            Tilemap, TileSheet, PaletteSet, discover_assets,
        )

        try:
            tilemap = Tilemap.from_file(bin_path)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Cannot load tilemap:\n{e}")
            return

        self._tilemap = tilemap

        # Update dimension spinners (block signals to avoid re-layout)
        self._width_spin.blockSignals(True)
        self._height_spin.blockSignals(True)
        self._width_spin.setValue(tilemap.width)
        self._height_spin.setValue(tilemap.height)
        self._width_spin.blockSignals(False)
        self._height_spin.blockSignals(False)

        # Auto-discover assets
        assets = discover_assets(bin_path)

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

        # Load palettes
        self._palettes = None
        if assets.best_pals:
            try:
                self._palettes = PaletteSet.from_pal_files(assets.best_pals)
            except Exception:
                pass
        # Fallback: extract palette from tile sheet image
        if (not self._palettes or self._palettes.palette_count() == 0) and self._sheet:
            self._palettes = PaletteSet.from_indexed_image(self._sheet.image)

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

        self._btn_save.setEnabled(True)
        self._dirty = False

        fname = os.path.basename(bin_path)
        parent = os.path.basename(os.path.dirname(bin_path))
        sheet_name = os.path.basename(assets.best_sheet) if assets.best_sheet else "none"
        pal_count = self._palettes.palette_count() if self._palettes else 0

        # Show which palette indices are actually used by this tilemap
        pals_used = set()
        max_idx = 0
        for e in tilemap.entries:
            pals_used.add(e.palette)
            if e.tile_index > max_idx:
                max_idx = e.tile_index

        self._status.setText(
            f"{parent}/{fname} — {tilemap.width}x{tilemap.height} tiles"
            f" — Sheet: {sheet_name}"
            f" — {pal_count} palette(s)"
            f" — Uses pals: {sorted(pals_used)}"
            f" — Max tile: {max_idx}"
        )

        self._update_pal_editor()

    def _save_file(self):
        if not self._tilemap or not self._tilemap.source_path:
            return
        try:
            self._tilemap.save()
            self._dirty = False
            self._status.setText(
                self._status.text().split(" — Saved")[0] + " — Saved!")
        except Exception as e:
            QMessageBox.warning(self, "Save Error", str(e))

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

        self._refresh_canvas()
        self._picker.set_sheet(self._sheet, self._palettes)
        self._update_pal_editor()

    # ── Editing ──────────────────────────────────────────────────────────────

    def _set_tool(self, tool: str):
        self._tool = tool
        self._btn_paint.setChecked(tool == "paint")
        self._btn_pick.setChecked(tool == "pick")

    def _paint_tile(self, col: int, row: int):
        """Called when canvas is clicked/dragged in paint mode."""
        if not self._tilemap:
            return

        if self._tool == "pick":
            # Eyedropper: pick tile from tilemap
            entry = self._tilemap.get(col, row)
            self._current_tile = entry.tile_index
            self._current_pal = entry.palette
            self._hflip = entry.hflip
            self._vflip = entry.vflip
            self._update_tile_info()
            self._picker._selected = entry.tile_index
            self._picker.update()
            return

        # Paint mode: place current tile
        from core.tilemap_data import TileEntry
        entry = TileEntry(
            tile_index=self._current_tile,
            hflip=self._hflip,
            vflip=self._vflip,
            palette=self._current_pal,
        )
        self._tilemap.set(col, row, entry)
        self._canvas.refresh_tile(col, row)
        self._dirty = True

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
        self._current_tile = idx
        self._update_tile_info()

    def _on_pal_changed(self, val: int):
        self._current_pal = val
        self._picker.set_palette_index(val)
        self._update_tile_preview()

    def _on_hflip_changed(self, checked: bool):
        self._hflip = checked
        self._update_tile_preview()

    def _on_vflip_changed(self, checked: bool):
        self._vflip = checked
        self._update_tile_preview()

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

    def _on_dimensions_changed(self):
        """Re-interpret the tilemap with new width/height.

        Changing W or H re-wraps the same flat entry data — the total
        entry count stays the same, only the row stride changes. When W
        changes, H is auto-recalculated to fit all entries (and vice versa).
        """
        if not self._tilemap:
            return
        new_w = self._width_spin.value()
        new_h = self._height_spin.value()
        if new_w == self._tilemap.width and new_h == self._tilemap.height:
            return

        from core.tilemap_data import TileEntry

        # Total entry count from the original file — never changes
        total = len(self._tilemap.entries)

        # Determine which spinner the user actually changed by comparing
        # to the current tilemap dimensions
        if new_w != self._tilemap.width:
            # Width changed → recalculate height to fit all entries
            new_h = max(1, (total + new_w - 1) // new_w)
            self._height_spin.blockSignals(True)
            self._height_spin.setValue(new_h)
            self._height_spin.blockSignals(False)
        elif new_h != self._tilemap.height:
            # Height changed → recalculate width to fit all entries
            new_w = max(1, (total + new_h - 1) // new_h)
            self._width_spin.blockSignals(True)
            self._width_spin.setValue(new_w)
            self._width_spin.blockSignals(False)

        # Pad with empty tiles if new grid is larger than entry count
        new_count = new_w * new_h
        entries = list(self._tilemap.entries)
        while len(entries) < new_count:
            entries.append(TileEntry())

        self._tilemap.width = new_w
        self._tilemap.height = new_h
        self._tilemap.entries = entries[:new_count]
        self._refresh_canvas()

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
            from core.tilemap_data import _recolor_tile
            tile = _recolor_tile(tile, self._current_pal, self._palettes)
        # Scale up for preview
        scaled = tile.scaled(
            64, 64,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        self._tile_preview.setPixmap(QPixmap.fromImage(scaled))
