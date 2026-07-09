"""
ui/tile_anim_viewer.py
Tile Animation Editor -- AnimEdit-inspired full editor for GBA tile animations.

Layout:
  Header:  [Tileset: ____v] [Animation: _v] [+ Add] [- Remove] | [Play/Pause] Speed:[slider] 100% | [Open in Explorer] | status
  Splitter Left (~260px fixed):
    Properties (QGroupBox) -- editable spinboxes for speed, start tile, etc.
    Palette (QGroupBox) -- slot dropdown, 16 editable swatches, import/export
    Info (QGroupBox) -- read-only animation metadata
  Splitter Right (stretches):
    Preview area (AnimPreviewWidget centered in scroll area, zoom buttons, frame scrubber)
    Frame Thumbnails (compact horizontal strip at 2x, fixed height)
    Tile Grid (current frame decomposed into 8x8 cells)
    Frame Actions (Save Image / Load Image / Add Frame / Delete Frame)

Uses core/tileset_anim_data.py to parse/write animation definitions.
"""

from __future__ import annotations

import os
import shutil
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import Qt, QSize, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QColor, QImage, QPainter, QPen, QPixmap, qRgba,
)
from PyQt6.QtWidgets import (
    QButtonGroup, QCheckBox, QColorDialog, QComboBox, QDialog,
    QDialogButtonBox, QFileDialog, QFormLayout, QFrame, QGroupBox,
    QHBoxLayout, QInputDialog, QLabel, QMenu, QMessageBox, QPushButton,
    QScrollArea, QSizePolicy, QSlider, QSpinBox, QSplitter, QVBoxLayout,
    QWidget,
)

from core.tileset_anim_data import (
    TileAnimation, DoorAnimation, FieldEffectAnimation,
    parse_tileset_anims, parse_door_anims, parse_field_effect_anims,
    parse_tilesets_from_headers, load_tileset_palettes,
    write_timing_to_source, write_start_tile_to_source,
    write_tile_amount_to_source, write_phase_to_source,
    write_counter_max_to_source,
    add_frame_to_anim, remove_frame_from_anim,
    add_animation_to_tileset, remove_animation_from_tileset,
    rename_animation,
)
from ui.open_folder_util import open_folder, open_in_folder
from ui.palette_utils import Color, clamp_to_gba, read_jasc_pal, write_jasc_pal


# ---------------------------------------------------------------------------
#  No-scroll combo and spin -- MANDATORY for all combo/spin in this file
# ---------------------------------------------------------------------------

class _NoScrollCombo(QComboBox):
    """QComboBox that ignores wheel events when the popup isn't showing."""
    def wheelEvent(self, event):
        if self.view().isVisible():
            super().wheelEvent(event)
        else:
            event.ignore()


class _NoScrollSpin(QSpinBox):
    """QSpinBox that ignores wheel events when not focused."""
    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class _HexSpinBox(QSpinBox):
    """QSpinBox that displays values in hex (0x1A0) like Porymap.
    Ignores wheel when not focused."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPrefix("0x")

    def textFromValue(self, value: int) -> str:
        return f"{value:X}"

    def valueFromText(self, text: str) -> int:
        clean = text.replace("0x", "").replace("0X", "").strip()
        try:
            return int(clean, 16)
        except ValueError:
            return 0

    def validate(self, text: str, pos: int):
        from PyQt6.QtGui import QValidator
        clean = text.replace("0x", "").replace("0X", "").strip()
        if not clean:
            return QValidator.State.Intermediate, text, pos
        try:
            val = int(clean, 16)
            if self.minimum() <= val <= self.maximum():
                return QValidator.State.Acceptable, text, pos
            return QValidator.State.Intermediate, text, pos
        except ValueError:
            return QValidator.State.Invalid, text, pos

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


# ---------------------------------------------------------------------------
#  Palette Swatch -- clickable single color
# ---------------------------------------------------------------------------

SWATCH_SIZE = 18


class PaletteSwatch(QLabel):
    """Single clickable color swatch with GBA 15-bit clamping."""
    color_changed = pyqtSignal(int, tuple)  # (index, (r,g,b))

    def __init__(self, index: int, color: Color = (0, 0, 0), parent=None):
        super().__init__(parent)
        self._index = index
        self._color = color
        self.setFixedSize(SWATCH_SIZE, SWATCH_SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFrameShape(QFrame.Shape.Box)
        self._apply_color()

    def color(self) -> Color:
        return self._color

    def set_color(self, color: Color, emit: bool = False):
        self._color = clamp_to_gba(*color)
        self._apply_color()
        if emit:
            self.color_changed.emit(self._index, self._color)

    def _apply_color(self):
        r, g, b = self._color
        from PyQt6.QtGui import QPalette
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor(r, g, b))
        self.setPalette(pal)
        self.setAutoFillBackground(True)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        r, g, b = self._color
        initial = QColor(r, g, b)
        top = self.window()
        dlg = QColorDialog(initial, top)
        dlg.setWindowTitle(f"Palette color {self._index}")
        dlg.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog, True)
        if dlg.exec():
            chosen = dlg.currentColor()
            new_color = clamp_to_gba(chosen.red(), chosen.green(), chosen.blue())
            self._color = new_color
            self._apply_color()
            self.color_changed.emit(self._index, new_color)


class PaletteSwatchRow(QWidget):
    """Horizontal row of up to 16 color swatches."""
    colors_changed = pyqtSignal()

    def __init__(self, count: int = 16, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)
        self._swatches: List[PaletteSwatch] = []
        for i in range(count):
            sw = PaletteSwatch(i)
            sw.color_changed.connect(self._on_swatch_changed)
            self._swatches.append(sw)
            layout.addWidget(sw)
        layout.addStretch()

    def _on_swatch_changed(self, idx: int, color: Color):
        self.colors_changed.emit()

    def set_colors(self, colors: List[Color]):
        for i, sw in enumerate(self._swatches):
            if i < len(colors):
                sw.set_color(colors[i])
            else:
                sw.set_color((0, 0, 0))

    def colors(self) -> List[Color]:
        return [sw.color() for sw in self._swatches]


# ---------------------------------------------------------------------------
#  AnimPreviewWidget -- animated playback with QTimer
# ---------------------------------------------------------------------------

class AnimPreviewWidget(QWidget):
    """Large animated preview with tile-column wrapping.

    Frames are decomposed into *units* and re-arranged into a grid of
    *columns* units wide.  The unit is either 8x8 (raw tile) or 16x16
    (metatile) depending on the ``metatile_lock`` flag.  When metatile
    mode is on, the 2x2 blocks of 8x8 tiles travel together so they
    can't be split across rows.
    """

    frame_changed = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._frames: List[QPixmap] = []
        self._frame_order: List[int] = []
        self._current: int = 0
        self._scale: int = 4
        self._frame_w: int = 16
        self._frame_h: int = 16
        self._metatile: bool = True  # True = 16x16 units, False = 8x8
        # layout in *unit* columns/rows
        self._unit_cols: int = 1
        self._unit_rows: int = 1
        self._total_units: int = 1
        self._playing: bool = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)
        self._interval_ms: int = 267
        self.setMinimumSize(64, 64)

    @property
    def _unit_px(self) -> int:
        return 16 if self._metatile else 8

    def set_frames(self, frames: List[QPixmap], frame_order: List[int],
                   frame_w: int, frame_h: int, interval_ms: float):
        self._frames = frames
        self._frame_order = frame_order
        self._frame_w = frame_w
        self._frame_h = frame_h
        self._interval_ms = max(16, int(interval_ms))
        self._current = 0
        self._recalc_units_from_frame()
        self._update_size()
        self.update()

    def _recalc_units_from_frame(self):
        """Reset unit grid to match native frame dimensions."""
        u = self._unit_px
        self._unit_cols = max(1, self._frame_w // u)
        self._unit_rows = max(1, self._frame_h // u)
        self._total_units = self._unit_cols * self._unit_rows

    def set_metatile(self, on: bool):
        """Switch between 16x16 metatile units and 8x8 tile units."""
        if on == self._metatile:
            return
        self._metatile = on
        self._recalc_units_from_frame()
        self._update_size()
        self.update()

    def is_metatile(self) -> bool:
        return self._metatile

    def set_scale(self, s: int):
        self._scale = max(1, min(16, s))
        self._update_size()
        self.update()

    def set_unit_columns(self, cols: int):
        """Set number of unit columns for display wrapping."""
        cols = max(1, min(self._total_units, cols))
        self._unit_cols = cols
        self._unit_rows = (self._total_units + cols - 1) // cols
        self._update_size()
        self.update()

    def unit_cols(self) -> int:
        return self._unit_cols

    def unit_rows(self) -> int:
        return self._unit_rows

    def total_units(self) -> int:
        return self._total_units

    def native_unit_cols(self) -> int:
        """Unit columns from the raw frame PNG width."""
        return max(1, self._frame_w // self._unit_px)

    def _update_size(self):
        u = self._unit_px
        w = self._unit_cols * u * self._scale + 8
        h = self._unit_rows * u * self._scale + 8
        self.setFixedSize(max(64, w), max(64, h))

    def play(self):
        self._playing = True
        self._timer.start(self._interval_ms)

    def stop(self):
        self._playing = False
        self._timer.stop()

    def is_playing(self) -> bool:
        return self._playing

    def set_frame(self, idx: int):
        if 0 <= idx < len(self._frame_order):
            self._current = idx
            self.frame_changed.emit(idx)
            self.update()

    def current_frame(self) -> int:
        return self._current

    def _advance(self):
        if not self._frame_order:
            return
        self._current = (self._current + 1) % len(self._frame_order)
        self.frame_changed.emit(self._current)
        self.update()

    def paintEvent(self, event):
        if not self._frames or not self._frame_order:
            p = QPainter(self)
            p.fillRect(self.rect(), QColor(34, 34, 34))
            p.setPen(QColor(100, 100, 100))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "No animation")
            p.end()
            return

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        p.fillRect(self.rect(), QColor(34, 34, 34))

        frame_idx = self._frame_order[self._current]
        if 0 <= frame_idx < len(self._frames):
            pm = self._frames[frame_idx]
            u = self._unit_px
            native_cols = max(1, self._frame_w // u)

            # If layout matches native frame dimensions, just draw scaled
            if self._unit_cols == native_cols:
                sw = self._frame_w * self._scale
                sh = self._frame_h * self._scale
                x = (self.width() - sw) // 2
                y = (self.height() - sh) // 2
                p.drawPixmap(x, y, sw, sh, pm)
            else:
                # Decompose into units and re-arrange
                us = u * self._scale  # display size per unit
                total_w = self._unit_cols * us
                total_h = self._unit_rows * us
                ox = (self.width() - total_w) // 2
                oy = (self.height() - total_h) // 2
                unit_idx = 0
                for src_row in range(max(1, self._frame_h // u)):
                    for src_col in range(native_cols):
                        if unit_idx >= self._total_units:
                            break
                        dst_col = unit_idx % self._unit_cols
                        dst_row = unit_idx // self._unit_cols
                        sx = src_col * u
                        sy = src_row * u
                        dx = ox + dst_col * us
                        dy = oy + dst_row * us
                        p.drawPixmap(dx, dy, us, us,
                                     pm, sx, sy, u, u)
                        unit_idx += 1

        p.end()


# ---------------------------------------------------------------------------
#  FilmstripWidget -- compact horizontal frame thumbnail strip (fixed 2x)
# ---------------------------------------------------------------------------

class FilmstripWidget(QWidget):
    """Horizontal strip of animation frames with selection highlight."""

    frame_clicked = pyqtSignal(int)
    frame_right_clicked = pyqtSignal(int, object)  # (index, QPoint globalPos)

    SCALE = 2  # fixed 2x scale for thumbnails

    def __init__(self, parent=None):
        super().__init__(parent)
        self._frames: List[QPixmap] = []
        self._frame_order: List[int] = []
        self._current: int = 0
        self._frame_w: int = 16
        self._frame_h: int = 16
        self.setMinimumHeight(20)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

    def set_frames(self, frames: List[QPixmap], frame_order: List[int],
                   frame_w: int, frame_h: int):
        self._frames = frames
        self._frame_order = frame_order
        self._frame_w = frame_w
        self._frame_h = frame_h
        self._current = 0
        self._update_size()
        self.update()

    def set_current(self, idx: int):
        self._current = idx
        self.update()

    def _update_size(self):
        if not self._frame_order:
            self.setFixedSize(100, 40)
            return
        n = len(self._frame_order)
        sw = self._frame_w * self.SCALE
        sh = self._frame_h * self.SCALE
        total_w = n * (sw + 4) + 4
        total_h = sh + 24
        self.setFixedSize(max(100, total_w), max(40, total_h))

    def _idx_at_x(self, x: float) -> int:
        sw = self._frame_w * self.SCALE
        idx = int((x - 4) / (sw + 4))
        if 0 <= idx < len(self._frame_order):
            return idx
        return -1

    def paintEvent(self, event):
        if not self._frames or not self._frame_order:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)

        sw = self._frame_w * self.SCALE
        sh = self._frame_h * self.SCALE

        for seq_idx, frame_idx in enumerate(self._frame_order):
            x = 4 + seq_idx * (sw + 4)
            y = 2

            p.fillRect(x, y, sw, sh, QColor(34, 34, 34))

            if 0 <= frame_idx < len(self._frames):
                pm = self._frames[frame_idx]
                p.drawPixmap(x, y, sw, sh, pm)

            if seq_idx == self._current:
                pen = QPen(QColor(100, 200, 255), 2)
                p.setPen(pen)
                p.drawRect(x, y, sw, sh)

            p.setPen(QColor(180, 180, 180))
            label = f"F{frame_idx}"
            p.drawText(x, y + sh + 14, label)

        p.end()

    def mousePressEvent(self, event):
        if not self._frame_order:
            return
        idx = self._idx_at_x(event.position().x())
        if idx >= 0:
            self._current = idx
            if event.button() == Qt.MouseButton.LeftButton:
                self.frame_clicked.emit(idx)
            elif event.button() == Qt.MouseButton.RightButton:
                self.frame_right_clicked.emit(idx, event.globalPosition().toPoint())
            self.update()


# ---------------------------------------------------------------------------
#  TileGridWidget -- current frame decomposed into 8x8 tile cells
# ---------------------------------------------------------------------------

class TileGridWidget(QWidget):
    """Shows the current frame decomposed into 8x8-pixel tiles in a grid.

    Each tile cell shows a hex VRAM address label (matching Porymap convention).
    Supports both grid (original layout) and horizontal strip layouts.
    """

    CELL_SIZE = 28  # display size per 8x8 tile cell (3.5x scale)
    LABEL_H = 12    # height for hex label below each cell

    tile_selected = pyqtSignal(int)  # emits VRAM tile offset

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap: Optional[QPixmap] = None
        self._frame_w: int = 0
        self._frame_h: int = 0
        self._selected_cell: int = -1
        self._cols: int = 0
        self._rows: int = 0
        self._base_tile: int = 0   # VRAM start tile for labeling
        self._horizontal: bool = False  # False=grid, True=horizontal strip
        self.setMinimumSize(48, 48)

    def set_base_tile(self, base: int):
        """Set the VRAM start tile offset for hex labeling."""
        self._base_tile = base
        self.update()

    def set_horizontal(self, h: bool):
        """Toggle horizontal strip layout vs grid."""
        self._horizontal = h
        self._recalc_size()
        self.update()

    def set_frame(self, pixmap: Optional[QPixmap], frame_w: int, frame_h: int):
        self._pixmap = pixmap
        self._frame_w = frame_w
        self._frame_h = frame_h
        self._cols = max(1, frame_w // 8)
        self._rows = max(1, frame_h // 8)
        self._selected_cell = -1
        self._recalc_size()
        self.update()

    def _recalc_size(self):
        if self._cols == 0 or self._rows == 0:
            self.setFixedSize(48, 48)
            return
        cs = self.CELL_SIZE
        lh = self.LABEL_H
        total_tiles = self._cols * self._rows
        if self._horizontal:
            # All tiles in one row
            w = total_tiles * (cs + 2) + 4
            h = cs + lh + 6
        else:
            # Original grid layout
            w = self._cols * (cs + 2) + 4
            h = self._rows * (cs + lh + 2) + 4
        self.setFixedSize(max(48, w), max(48, h))

    def paintEvent(self, event):
        if not self._pixmap or self._frame_w == 0 or self._frame_h == 0:
            p = QPainter(self)
            p.fillRect(self.rect(), QColor(34, 34, 34))
            p.setPen(QColor(100, 100, 100))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No frame")
            p.end()
            return

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        p.fillRect(self.rect(), QColor(34, 34, 34))

        src_img = self._pixmap.toImage()
        cs = self.CELL_SIZE
        lh = self.LABEL_H
        total = self._cols * self._rows
        font = p.font()
        font.setPixelSize(9)
        p.setFont(font)

        for idx in range(total):
            if self._horizontal:
                draw_col = idx
                draw_row = 0
            else:
                draw_col = idx % self._cols
                draw_row = idx // self._cols

            # Source position in the original image grid
            src_col = idx % self._cols
            src_row = idx // self._cols

            sx = src_col * 8
            sy = src_row * 8
            tile_img = src_img.copy(sx, sy, 8, 8)
            tile_pm = QPixmap.fromImage(tile_img)

            dx = 2 + draw_col * (cs + 2)
            dy = 2 + draw_row * (cs + lh + 2)
            p.drawPixmap(dx, dy, cs, cs, tile_pm)

            # Hex tile label below cell
            vram_tile = self._base_tile + idx
            p.setPen(QColor(140, 140, 140))
            p.drawText(dx, dy + cs, cs, lh,
                       Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                       f"0x{vram_tile:X}")

            # Highlight selected cell
            if idx == self._selected_cell:
                pen = QPen(QColor(80, 160, 255), 2)
                p.setPen(pen)
                p.drawRect(dx, dy, cs, cs)

        p.end()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        cs = self.CELL_SIZE + 2
        lh = self.LABEL_H
        total = self._cols * self._rows

        if self._horizontal:
            col = int((event.position().x() - 2) / cs)
            if 0 <= col < total:
                self._selected_cell = col
                self.tile_selected.emit(self._base_tile + col)
                self.update()
        else:
            col = int((event.position().x() - 2) / cs)
            row = int((event.position().y() - 2) / (cs + lh))
            if 0 <= col < self._cols and 0 <= row < self._rows:
                idx = row * self._cols + col
                if idx < total:
                    self._selected_cell = idx
                    self.tile_selected.emit(self._base_tile + idx)
                    self.update()


# ---------------------------------------------------------------------------
#  Collapsible Section
# ---------------------------------------------------------------------------

class _CollapsibleSection(QWidget):
    """A section with a clickable header that collapses/expands its content.

    When collapsed the widget's maximum height shrinks to just the header
    bar so the parent layout reclaims all the freed space immediately.
    """

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self._expanded = True
        self._toggle_btn = QPushButton(f"\u25bc {title}")
        self._toggle_btn.setStyleSheet(
            "QPushButton { text-align: left; border: none; padding: 2px 4px; "
            "color: #ccc; font-weight: bold; font-size: 11px; background: #333; }"
            "QPushButton:hover { background: #444; }")
        self._toggle_btn.setFixedHeight(22)
        self._toggle_btn.clicked.connect(self._toggle)
        self._title = title

        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._toggle_btn)
        layout.addWidget(self._content)

    def set_content_layout(self, content_layout):
        """Replace the content area's layout."""
        QWidget().setLayout(self._content.layout())  # clear old
        self._content.setLayout(content_layout)

    def _toggle(self):
        self._expanded = not self._expanded
        self._content.setVisible(self._expanded)
        arrow = "\u25bc" if self._expanded else "\u25b6"
        self._toggle_btn.setText(f"{arrow} {self._title}")
        if self._expanded:
            self.setMaximumHeight(16777215)  # Qt default max
            self.setSizePolicy(QSizePolicy.Policy.Preferred,
                               QSizePolicy.Policy.Preferred)
        else:
            self.setFixedHeight(self._toggle_btn.height())
            self.setSizePolicy(QSizePolicy.Policy.Preferred,
                               QSizePolicy.Policy.Fixed)


# ---------------------------------------------------------------------------
#  Add Animation Dialog
# ---------------------------------------------------------------------------

class _AddAnimDialog(QDialog):
    """Dialog for adding a new animation to a tileset."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add New Animation")
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.anim_name_edit = QWidget()
        # Use a QLineEdit
        from PyQt6.QtWidgets import QLineEdit
        self.anim_name = QLineEdit()
        self.anim_name.setPlaceholderText("e.g. waterfall")
        form.addRow("Animation Name:", self.anim_name)

        self.start_tile_spin = _HexSpinBox()
        self.start_tile_spin.setRange(0, 1023)
        self.start_tile_spin.setValue(0)
        form.addRow("Start Tile:", self.start_tile_spin)

        self.tile_amount_spin = _NoScrollSpin()
        self.tile_amount_spin.setRange(1, 128)
        self.tile_amount_spin.setValue(4)
        form.addRow("Tile Amount:", self.tile_amount_spin)

        self.divisor_spin = _NoScrollSpin()
        self.divisor_spin.setRange(1, 256)
        self.divisor_spin.setValue(16)
        form.addRow("Speed (divisor):", self.divisor_spin)

        self._png_paths: List[str] = []
        self._png_label = QLabel("No files selected")
        btn_pick = QPushButton("Select Frame Images...")
        btn_pick.setToolTip(
            "Pick the frame pictures for this animation.\n"
            "Indexed PNG or indexed BMP (legacy AnimEdit tools). BMPs are\n"
            "converted to indexed PNGs automatically, keeping their palette.")
        btn_pick.clicked.connect(self._pick_pngs)
        png_row = QHBoxLayout()
        png_row.addWidget(btn_pick)
        png_row.addWidget(self._png_label, 1)
        form.addRow("Initial Frames:", png_row)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _pick_pngs(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select Frame Images", "",
            "Images (*.png *.bmp);;PNG Images (*.png);;BMP Images (*.bmp)")
        if paths:
            self._png_paths = sorted(paths)
            self._png_label.setText(f"{len(paths)} file(s) selected")

    def get_values(self) -> dict:
        return {
            "anim_name": self.anim_name.text().strip(),
            "start_tile": self.start_tile_spin.value(),
            "tile_amount": self.tile_amount_spin.value(),
            "divisor": self.divisor_spin.value(),
            "png_paths": list(self._png_paths),
        }


# ===========================================================================
#  Main Editor Widget
# ===========================================================================

class TileAnimEditorWidget(QWidget):
    """Full tile animation editor with AnimEdit-inspired layout.

    Public interface:
        modified = pyqtSignal()
        set_project(project_dir: str)
    """

    modified = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._project_dir: str = ""

        # Parsed data
        self._all_tilesets: List[dict] = []  # from parse_tilesets_from_headers
        self._animations: List[TileAnimation] = []
        self._anims_by_tileset: Dict[str, List[TileAnimation]] = {}

        # Current selection
        self._current_anim: Optional[TileAnimation] = None
        self._frame_pixmaps: List[QPixmap] = []
        self._frame_images: List[QImage] = []  # raw indexed images
        self._frame_w: int = 16
        self._frame_h: int = 16
        self._palette_colors: List[Color] = []
        self._all_palettes: List[List[Color]] = []  # 16 palette slots

        # State flags
        self._loading: bool = False
        self._dirty: bool = False

        self._build_ui()

    # ------------------------------------------------------------------
    #  Public API
    # ------------------------------------------------------------------

    def set_project(self, project_dir: str):
        self._project_dir = project_dir
        self._load_data()

    # ------------------------------------------------------------------
    #  UI Construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        # ================================================================
        #  HEADER ROW
        # ================================================================
        header = QHBoxLayout()
        header.setSpacing(6)

        header.addWidget(QLabel("Tileset:"))
        self._tileset_combo = _NoScrollCombo()
        self._tileset_combo.setMinimumWidth(180)
        self._tileset_combo.currentIndexChanged.connect(self._on_tileset_selected)
        header.addWidget(self._tileset_combo)

        header.addWidget(QLabel("Animation:"))
        self._anim_combo = _NoScrollCombo()
        self._anim_combo.setMinimumWidth(180)
        self._anim_combo.currentIndexChanged.connect(self._on_anim_selected)
        header.addWidget(self._anim_combo, 1)

        self._btn_add_anim = QPushButton("+ Add")
        self._btn_add_anim.setFixedWidth(55)
        self._btn_add_anim.setToolTip("Add a new animation to the selected tileset")
        self._btn_add_anim.clicked.connect(self._add_animation)
        header.addWidget(self._btn_add_anim)

        self._btn_remove_anim = QPushButton("- Remove")
        self._btn_remove_anim.setFixedWidth(65)
        self._btn_remove_anim.setToolTip("Remove the selected animation")
        self._btn_remove_anim.clicked.connect(self._remove_animation)
        header.addWidget(self._btn_remove_anim)

        self._btn_rename_anim = QPushButton("Rename")
        self._btn_rename_anim.setFixedWidth(65)
        self._btn_rename_anim.setToolTip(
            "Rename the selected animation everywhere — its source symbols\n"
            "(sTilesetAnims_*, QueueAnimTiles_*), its INCBIN frame paths, and\n"
            "its frame folder. The tileset prefix is kept.")
        self._btn_rename_anim.clicked.connect(self._rename_animation)
        header.addWidget(self._btn_rename_anim)

        self._btn_cleanup_frames = QPushButton("Clean Up…")
        self._btn_cleanup_frames.setFixedWidth(80)
        self._btn_cleanup_frames.setToolTip(
            "Find and delete leftover frame pictures that no animation uses\n"
            "anymore (from deleted frames or removed animations).")
        self._btn_cleanup_frames.clicked.connect(self._cleanup_frames)
        header.addWidget(self._btn_cleanup_frames)

        sep1 = QLabel("|")
        sep1.setStyleSheet("color: #555;")
        header.addWidget(sep1)

        self._btn_play = QPushButton("\u25b6 Play")
        self._btn_play.setFixedWidth(70)
        self._btn_play.clicked.connect(self._toggle_play)
        header.addWidget(self._btn_play)

        header.addWidget(QLabel("Speed:"))
        self._speed_slider = QSlider(Qt.Orientation.Horizontal)
        self._speed_slider.setRange(10, 400)
        self._speed_slider.setValue(100)
        self._speed_slider.setFixedWidth(100)
        self._speed_slider.setToolTip("Playback speed (% of real GBA speed)")
        self._speed_slider.valueChanged.connect(self._on_speed_changed)
        header.addWidget(self._speed_slider)
        self._speed_label = QLabel("100%")
        self._speed_label.setFixedWidth(36)
        header.addWidget(self._speed_label)

        sep2 = QLabel("|")
        sep2.setStyleSheet("color: #555;")
        header.addWidget(sep2)

        self._btn_open_folder = QPushButton("Open in Explorer")
        self._btn_open_folder.setToolTip("Open the frame directory in your file manager")
        self._btn_open_folder.clicked.connect(self._open_in_explorer)
        header.addWidget(self._btn_open_folder)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #888; font-size: 11px;")
        header.addWidget(self._status_label)

        root.addLayout(header)

        # ================================================================
        #  MAIN BODY: LEFT (properties, fixed) | RIGHT (preview + frames)
        # ================================================================
        # A draggable splitter so the user can widen the left panel (its content
        # — palette swatches, Import/Bake buttons — is wider than the old fixed
        # 310px and was being clipped with no way to resize).
        main_hbox = QSplitter(Qt.Orientation.Horizontal)
        main_hbox.setChildrenCollapsible(False)

        # ==============================================================
        #  LEFT PANEL (resizable via the splitter; scrolls if made narrow)
        # ==============================================================
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        # Show a horizontal scrollbar when the panel is narrower than its content
        # instead of clipping it (the "buttons cut off, can't click" bug).
        left_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        left_scroll.setMinimumWidth(220)

        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(4, 4, 4, 4)
        left_layout.setSpacing(6)

        # -- Properties group --
        props_group = QGroupBox("Properties")
        props_layout = QVBoxLayout(props_group)
        props_layout.setContentsMargins(6, 6, 6, 6)
        props_layout.setSpacing(4)

        # Speed (divisor)
        speed_row = QHBoxLayout()
        speed_row.setSpacing(4)
        speed_row.addWidget(QLabel("Speed (divisor):"))
        self._divisor_spin = _NoScrollSpin()
        self._divisor_spin.setRange(1, 256)
        self._divisor_spin.setToolTip(
            "Vblank ticks between frame advances.\n"
            "Lower = faster. 1 = 60fps, 16 = ~3.75 fps.")
        self._divisor_spin.valueChanged.connect(self._on_divisor_changed)
        speed_row.addWidget(self._divisor_spin)
        props_layout.addLayout(speed_row)

        self._fps_ms_label = QLabel("= 3.8 fps (267 ms)")
        self._fps_ms_label.setStyleSheet("color: #aaa; font-size: 11px; margin-left: 4px;")
        props_layout.addWidget(self._fps_ms_label)

        # Start Tile
        start_row = QHBoxLayout()
        start_row.setSpacing(4)
        start_row.addWidget(QLabel("Start Tile:"))
        self._start_tile_spin = _HexSpinBox()
        self._start_tile_spin.setRange(0, 1023)
        self._start_tile_spin.setToolTip(
            "VRAM tile index where this animation writes to.\n"
            "Shown in hex to match Porymap's Tileset Editor.")
        self._start_tile_spin.valueChanged.connect(
            lambda _v: (self._mark_props_dirty(), self._update_range_label()))
        start_row.addWidget(self._start_tile_spin)
        props_layout.addLayout(start_row)

        # Tile Amount
        amount_row = QHBoxLayout()
        amount_row.setSpacing(4)
        amount_row.addWidget(QLabel("Tile Amount:"))
        self._tile_amount_spin = _NoScrollSpin()
        self._tile_amount_spin.setRange(1, 128)
        self._tile_amount_spin.setToolTip("Number of 8x8 tiles per frame")
        self._tile_amount_spin.valueChanged.connect(
            lambda _v: (self._mark_props_dirty(), self._update_range_label()))
        amount_row.addWidget(self._tile_amount_spin)
        props_layout.addLayout(amount_row)

        # Phase
        phase_row = QHBoxLayout()
        phase_row.setSpacing(4)
        phase_row.addWidget(QLabel("Phase:"))
        self._phase_spin = _NoScrollSpin()
        self._phase_spin.setRange(0, 255)
        self._phase_spin.setToolTip("Timer phase offset (0 = no offset)")
        self._phase_spin.valueChanged.connect(
            lambda _v: self._mark_props_dirty())
        phase_row.addWidget(self._phase_spin)
        props_layout.addLayout(phase_row)

        # Counter Max
        cmax_row = QHBoxLayout()
        cmax_row.setSpacing(4)
        cmax_row.addWidget(QLabel("Counter Max:"))
        self._counter_max_spin = _NoScrollSpin()
        self._counter_max_spin.setRange(1, 65535)
        self._counter_max_spin.setToolTip("Total animation cycle length")
        self._counter_max_spin.valueChanged.connect(
            lambda _v: self._mark_props_dirty())
        cmax_row.addWidget(self._counter_max_spin)
        props_layout.addLayout(cmax_row)

        # VRAM tile-range read-out + collision warning. This animation writes
        # `tile amount` tiles starting at `start tile`, so it occupies
        # [start, start+amount). If that range overlaps another animation in the
        # SAME tileset, both fight over those tiles in-game (flicker / "extra
        # frames"). This label makes the range visible and turns red on overlap.
        self._range_label = QLabel("")
        self._range_label.setWordWrap(True)
        self._range_label.setStyleSheet("font-size: 11px; margin-left: 4px;")
        props_layout.addWidget(self._range_label)

        left_layout.addWidget(props_group)

        # -- Palette group --
        pal_group = QGroupBox("Palette")
        pal_layout = QVBoxLayout(pal_group)
        pal_layout.setContentsMargins(6, 6, 6, 6)
        pal_layout.setSpacing(4)

        slot_row = QHBoxLayout()
        slot_row.setSpacing(4)
        slot_row.addWidget(QLabel("Slot:"))
        self._palette_slot_combo = _NoScrollCombo()
        for i in range(16):
            self._palette_slot_combo.addItem(f"{i:02d}")
        self._palette_slot_combo.currentIndexChanged.connect(
            self._on_palette_slot_changed)
        slot_row.addWidget(self._palette_slot_combo)
        slot_row.addStretch()
        pal_layout.addLayout(slot_row)

        self._pal_row = PaletteSwatchRow(16)
        self._pal_row.colors_changed.connect(self._on_palette_changed)
        pal_layout.addWidget(self._pal_row)

        pal_btns = QHBoxLayout()
        pal_btns.setSpacing(4)
        btn_import_pal = QPushButton("Import .pal")
        btn_import_pal.setToolTip("Import a JASC .pal file")
        btn_import_pal.clicked.connect(self._import_pal)
        pal_btns.addWidget(btn_import_pal)

        btn_export_pal = QPushButton("Export .pal")
        btn_export_pal.setToolTip("Export the current palette as JASC .pal")
        btn_export_pal.clicked.connect(self._export_pal)
        pal_btns.addWidget(btn_export_pal)

        btn_import_png_pal = QPushButton("Import from PNG")
        btn_import_png_pal.setToolTip("Extract palette from an indexed PNG")
        btn_import_png_pal.clicked.connect(self._import_palette_from_png)
        pal_btns.addWidget(btn_import_png_pal)

        btn_import_png_manual = QPushButton("Import PNG Manually")
        btn_import_png_manual.setToolTip(
            "Open the manual palette picker on any PNG.\n"
            "Pick which colours land in which slot and set the BG slot."
        )
        btn_import_png_manual.clicked.connect(
            self._import_palette_from_png_manual)
        pal_btns.addWidget(btn_import_png_manual)

        pal_layout.addLayout(pal_btns)

        # Bake the CURRENT palette into the frame image files on disk. The app
        # renders the live/edited palette, but each frame PNG keeps whatever
        # palette was last baked in — so an external editor (GIMP) shows the OLD
        # colours. This writes the live palette into every frame so they match.
        btn_bake = QPushButton("Bake Palette → Frame Images")
        btn_bake.setToolTip(
            "Write the CURRENT palette into every frame image on disk.\n"
            "The app shows the live/edited palette, but the PNG files keep the\n"
            "palette last baked into them — so opening a frame in GIMP shows the\n"
            "old colours. Click this to make the files match what you see here.")
        btn_bake.clicked.connect(self._bake_palette_to_frames)
        pal_layout.addWidget(btn_bake)
        left_layout.addWidget(pal_group)

        # -- Info group --
        info_group = QGroupBox("Info")
        info_layout = QVBoxLayout(info_group)
        info_layout.setContentsMargins(6, 6, 6, 6)
        info_layout.setSpacing(2)

        self._info_labels: Dict[str, QLabel] = {}
        info_fields = [
            ("anim_name", "Animation"),
            ("tileset_name", "Tileset"),
            ("tileset_type", "Type"),
            ("frames", "Frames"),
            ("frame_size", "Frame Size"),
            ("tiles_frame", "Tiles / Frame"),
            ("src_symbol", "Source Symbol"),
            ("init_func", "Init Function"),
            ("frame_dir", "Frame Directory"),
        ]
        for key, label_text in info_fields:
            row = QHBoxLayout()
            row.setSpacing(4)
            lbl = QLabel(f"{label_text}:")
            lbl.setStyleSheet("color: #888; font-size: 11px;")
            lbl.setFixedWidth(90)
            val = QLabel("\u2014")
            val.setStyleSheet("color: #ddd; font-size: 11px;")
            val.setWordWrap(True)
            val.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse)
            self._info_labels[key] = val
            row.addWidget(lbl)
            row.addWidget(val, 1)
            info_layout.addLayout(row)

        left_layout.addWidget(info_group)
        left_layout.addStretch()

        left_scroll.setWidget(left_widget)
        main_hbox.addWidget(left_scroll)

        # ==============================================================
        #  RIGHT PANEL (stretches)
        # ==============================================================
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(4)

        # -- Preview area (stretch=2) --
        preview_container = QWidget()
        preview_vbox = QVBoxLayout(preview_container)
        preview_vbox.setContentsMargins(0, 0, 0, 0)
        preview_vbox.setSpacing(4)

        self._preview = AnimPreviewWidget()
        self._preview.frame_changed.connect(self._on_preview_frame_changed)
        preview_scroll = QScrollArea()
        preview_scroll.setWidget(self._preview)
        preview_scroll.setWidgetResizable(False)
        preview_scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview_scroll.setStyleSheet("background: #222;")
        preview_scroll.setMaximumHeight(420)
        self._preview_scroll = preview_scroll
        preview_vbox.addWidget(preview_scroll, 1)

        # Controls row: Zoom dropdown + 16x16 checkbox + W/H layout spinners
        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(6)

        ctrl_row.addWidget(QLabel("Zoom:"))
        self._zoom_combo = _NoScrollCombo()
        for z in (1, 2, 4, 8, 16):
            self._zoom_combo.addItem(f"{z}x", z)
        self._zoom_combo.setCurrentIndex(2)  # default 4x
        self._zoom_combo.setFixedWidth(60)
        self._zoom_combo.currentIndexChanged.connect(self._on_zoom_changed)
        ctrl_row.addWidget(self._zoom_combo)

        ctrl_row.addSpacing(8)
        self._metatile_check = QCheckBox("16\u00d716")
        self._metatile_check.setChecked(True)
        self._metatile_check.setToolTip(
            "When checked, W counts 16\u00d716 metatiles (keeps 2\u00d72 "
            "tile blocks together).\nWhen unchecked, W counts 8\u00d78 tiles.")
        self._metatile_check.toggled.connect(self._on_metatile_toggled)
        ctrl_row.addWidget(self._metatile_check)

        ctrl_row.addSpacing(8)
        ctrl_row.addWidget(QLabel("W:"))
        self._preview_w_spin = _NoScrollSpin()
        self._preview_w_spin.setRange(1, 256)
        self._preview_w_spin.setValue(1)
        self._preview_w_spin.setFixedWidth(52)
        self._preview_w_spin.setToolTip("Display width (in metatiles or tiles)")
        self._preview_w_spin.valueChanged.connect(self._on_preview_w_changed)
        ctrl_row.addWidget(self._preview_w_spin)

        ctrl_row.addWidget(QLabel("H:"))
        self._preview_h_label = QLabel("1")
        self._preview_h_label.setFixedWidth(30)
        self._preview_h_label.setToolTip("Display height (auto-calculated)")
        ctrl_row.addWidget(self._preview_h_label)

        ctrl_row.addSpacing(8)
        self._btn_wrap_reset = QPushButton("Reset")
        self._btn_wrap_reset.setToolTip("Reset W to match the original frame layout")
        self._btn_wrap_reset.setFixedWidth(48)
        self._btn_wrap_reset.clicked.connect(self._on_preview_wrap_reset)
        ctrl_row.addWidget(self._btn_wrap_reset)

        ctrl_row.addStretch()
        preview_vbox.addLayout(ctrl_row)

        # Frame scrubber: [<] [slider] [>] "3 / 8"
        scrub_row = QHBoxLayout()
        scrub_row.setSpacing(4)
        scrub_row.addWidget(QLabel("Frame:"))
        self._btn_prev_frame = QPushButton("<")
        self._btn_prev_frame.setFixedWidth(28)
        self._btn_prev_frame.clicked.connect(self._prev_frame)
        scrub_row.addWidget(self._btn_prev_frame)

        self._frame_slider = QSlider(Qt.Orientation.Horizontal)
        self._frame_slider.setRange(0, 0)
        self._frame_slider.valueChanged.connect(self._on_frame_slider)
        scrub_row.addWidget(self._frame_slider, 1)

        self._btn_next_frame = QPushButton(">")
        self._btn_next_frame.setFixedWidth(28)
        self._btn_next_frame.clicked.connect(self._next_frame)
        scrub_row.addWidget(self._btn_next_frame)

        self._frame_pos_label = QLabel("0 / 0")
        self._frame_pos_label.setFixedWidth(50)
        self._frame_pos_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scrub_row.addWidget(self._frame_pos_label)
        preview_vbox.addLayout(scrub_row)

        right_layout.addWidget(preview_container, 2)

        # -- Frame Thumbnails (collapsible) --
        self._filmstrip_group = _CollapsibleSection("Frame Thumbnails")
        filmstrip_inner = QVBoxLayout()
        filmstrip_inner.setContentsMargins(0, 0, 0, 0)
        filmstrip_inner.setSpacing(0)

        self._filmstrip = FilmstripWidget()
        self._filmstrip.frame_clicked.connect(self._on_filmstrip_click)
        self._filmstrip.frame_right_clicked.connect(self._on_filmstrip_right_click)

        filmstrip_scroll = QScrollArea()
        filmstrip_scroll.setWidget(self._filmstrip)
        filmstrip_scroll.setWidgetResizable(False)
        filmstrip_scroll.setStyleSheet("background: #222;")
        filmstrip_scroll.setFixedHeight(80)
        filmstrip_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._filmstrip_scroll = filmstrip_scroll
        filmstrip_inner.addWidget(filmstrip_scroll)
        self._filmstrip_group.set_content_layout(filmstrip_inner)

        right_layout.addWidget(self._filmstrip_group, 0)

        # -- Tile Grid (collapsible) --
        self._tile_grid_group = _CollapsibleSection("Tile Grid")
        tile_grid_inner = QVBoxLayout()
        tile_grid_inner.setContentsMargins(4, 4, 4, 4)
        tile_grid_inner.setSpacing(4)

        tile_grid_toolbar = QHBoxLayout()
        tile_grid_toolbar.setSpacing(4)
        self._btn_tile_horizontal = QPushButton("Horizontal")
        self._btn_tile_horizontal.setCheckable(True)
        self._btn_tile_horizontal.setToolTip("Toggle between grid and horizontal strip layout")
        self._btn_tile_horizontal.toggled.connect(self._toggle_tile_grid_layout)
        tile_grid_toolbar.addWidget(self._btn_tile_horizontal)
        tile_grid_toolbar.addStretch()
        tile_grid_inner.addLayout(tile_grid_toolbar)

        self._tile_grid = TileGridWidget()
        tile_grid_scroll = QScrollArea()
        tile_grid_scroll.setWidget(self._tile_grid)
        tile_grid_scroll.setWidgetResizable(False)
        tile_grid_scroll.setStyleSheet("background: #222;")
        tile_grid_inner.addWidget(tile_grid_scroll)
        self._tile_grid_group.set_content_layout(tile_grid_inner)

        right_layout.addWidget(self._tile_grid_group, 1)

        # -- Frame Actions bar --
        frame_actions = QHBoxLayout()
        frame_actions.setSpacing(4)

        btn_save_img = QPushButton("Save Image")
        btn_save_img.setToolTip("Export the current frame as a PNG file")
        btn_save_img.clicked.connect(self._save_frame_image)
        frame_actions.addWidget(btn_save_img)

        btn_load_img = QPushButton("Load Image")
        btn_load_img.setToolTip("Replace the current frame with a new PNG")
        btn_load_img.clicked.connect(self._replace_frame)
        frame_actions.addWidget(btn_load_img)

        btn_add_frame = QPushButton("Add Frame")
        btn_add_frame.setToolTip("Add a new frame PNG to the animation")
        btn_add_frame.clicked.connect(self._add_frame)
        frame_actions.addWidget(btn_add_frame)

        btn_del_frame = QPushButton("Delete Frame")
        btn_del_frame.setToolTip("Remove the selected frame from C source")
        btn_del_frame.clicked.connect(self._delete_frame)
        frame_actions.addWidget(btn_del_frame)

        self._btn_resize_frames = QPushButton("Resize Frames")
        self._btn_resize_frames.setToolTip(
            "Change the pixel size of EVERY frame in this animation\n"
            "(e.g. 16×32 → 8×8). Tile Amount is updated to match.")
        self._btn_resize_frames.clicked.connect(self._resize_frames)
        frame_actions.addWidget(self._btn_resize_frames)

        frame_actions.addStretch()
        right_layout.addLayout(frame_actions)

        main_hbox.addWidget(right_widget)
        # The preview side takes the extra space; the left panel keeps its size.
        main_hbox.setStretchFactor(0, 0)
        main_hbox.setStretchFactor(1, 1)
        main_hbox.setSizes([320, 900])

        root.addWidget(main_hbox, 1)

    # ------------------------------------------------------------------
    #  Data Loading
    # ------------------------------------------------------------------

    def _load_data(self):
        """Parse tilesets and animations from project."""
        self._all_tilesets = parse_tilesets_from_headers(self._project_dir)
        self._animations = parse_tileset_anims(self._project_dir)

        # Group animations by tileset name
        self._anims_by_tileset = {}
        for anim in self._animations:
            key = anim.tileset_name
            if key not in self._anims_by_tileset:
                self._anims_by_tileset[key] = []
            self._anims_by_tileset[key].append(anim)

        self._populate_tileset_combo()

    def _populate_tileset_combo(self):
        """Fill tileset combo. Tilesets WITH animations listed first."""
        self._tileset_combo.blockSignals(True)
        self._tileset_combo.clear()

        # Split into those with and without animations
        with_anims = []
        without_anims = []
        for ts in self._all_tilesets:
            if ts["name"].lower() in self._anims_by_tileset or \
               ts["dir_name"] in self._anims_by_tileset:
                with_anims.append(ts)
            else:
                without_anims.append(ts)

        self._tileset_order: List[Optional[dict]] = []

        # Add tilesets with animations
        for ts in with_anims:
            ts_type = "secondary" if ts["is_secondary"] else "primary"
            label = f"{ts['name']} ({ts_type})"
            self._tileset_combo.addItem(label)
            self._tileset_order.append(ts)

        # Separator
        if with_anims and without_anims:
            self._tileset_combo.addItem("\u2500\u2500 No animations \u2500\u2500")
            idx = self._tileset_combo.count() - 1
            model = self._tileset_combo.model()
            model.item(idx).setEnabled(False)
            self._tileset_order.append(None)

        # Add tilesets without animations
        for ts in without_anims:
            ts_type = "secondary" if ts["is_secondary"] else "primary"
            label = f"{ts['name']} ({ts_type})"
            self._tileset_combo.addItem(label)
            self._tileset_order.append(ts)

        self._tileset_combo.blockSignals(False)

        total = len(self._animations)
        self._status_label.setText(
            f"{total} animation{'s' if total != 1 else ''} across "
            f"{len(with_anims)} tileset{'s' if len(with_anims) != 1 else ''}")

        # Select first tileset (should have animations)
        if self._tileset_order:
            self._tileset_combo.setCurrentIndex(0)
            self._on_tileset_selected(0)

    def _on_tileset_selected(self, idx: int):
        """Tileset changed -- populate animation combo."""
        if idx < 0 or idx >= len(self._tileset_order):
            return
        ts = self._tileset_order[idx]
        if ts is None:
            return  # separator

        self._anim_combo.blockSignals(True)
        self._anim_combo.clear()

        # Find animations for this tileset
        # Try both the raw name and dir_name as keys
        ts_name_lower = ts["name"].lower()
        ts_dir = ts["dir_name"]
        anims = (self._anims_by_tileset.get(ts_dir, []) or
                 self._anims_by_tileset.get(ts_name_lower, []))

        # Also check original tileset_name on anims
        if not anims:
            for key, anim_list in self._anims_by_tileset.items():
                if anim_list and anim_list[0].tileset_name.lower().replace("_", "") == ts_name_lower.lower().replace("_", ""):
                    anims = anim_list
                    break

        self._current_tileset_anims: List[TileAnimation] = anims

        if anims:
            for i, anim in enumerate(anims):
                self._anim_combo.addItem(f"{i}: {anim.name.replace('_', ' ').title()}")
        else:
            self._anim_combo.addItem("(no animations)")
            idx0 = 0
            model = self._anim_combo.model()
            model.item(idx0).setEnabled(False)

        self._anim_combo.blockSignals(False)

        # Load tileset palettes
        ts_type = "secondary" if ts["is_secondary"] else "primary"
        self._all_palettes = load_tileset_palettes(
            self._project_dir, ts_dir, ts_type)

        # Select first animation
        if anims:
            self._anim_combo.setCurrentIndex(0)
            self._on_anim_selected(0)
        else:
            self._clear_display()

        # Enable/disable add/remove/rename buttons
        self._btn_add_anim.setEnabled(True)
        self._btn_remove_anim.setEnabled(bool(anims))
        self._btn_rename_anim.setEnabled(bool(anims))
        self._btn_resize_frames.setEnabled(bool(anims))

    def _on_anim_selected(self, idx: int):
        """Animation changed -- load and display it."""
        if not hasattr(self, '_current_tileset_anims'):
            return
        if idx < 0 or idx >= len(self._current_tileset_anims):
            return

        self._preview.stop()
        self._btn_play.setText("\u25b6 Play")

        anim = self._current_tileset_anims[idx]
        self._current_anim = anim

        # Load frame PNGs
        self._frame_pixmaps = []
        self._frame_images = []
        self._frame_w, self._frame_h = 16, 16

        for frame in anim.frames:
            self._load_frame_png(frame.png_path)

        # Determine palette slot
        pal_slot = anim.palette_hint if anim.palette_hint >= 0 else 0

        # Apply selected palette to frames for rendering
        self._loading = True
        self._palette_slot_combo.setCurrentIndex(pal_slot)
        self._loading = False

        if self._all_palettes and pal_slot < len(self._all_palettes):
            self._palette_colors = list(self._all_palettes[pal_slot])
        else:
            self._extract_palette_from_frames()

        self._loading = True
        self._pal_row.set_colors(self._palette_colors)
        self._loading = False

        # Re-render frames with selected palette
        self._rerender_frames_with_palette()

        # Set property spinners
        self._loading = True
        self._divisor_spin.setValue(anim.divisor)
        self._start_tile_spin.setValue(anim.dest_tile)
        self._tile_amount_spin.setValue(anim.tile_count)
        self._phase_spin.setValue(anim.phase)
        self._counter_max_spin.setValue(anim.counter_max if anim.counter_max else 0)
        self._update_fps_label(anim.divisor)
        self._loading = False

        # Set up preview
        speed_pct = self._speed_slider.value() / 100.0
        base_ms = anim.frame_duration_ms
        effective_ms = max(16, int(base_ms / speed_pct)) if speed_pct > 0 else int(base_ms)

        self._preview.set_frames(
            self._frame_pixmaps, anim.frame_order,
            self._frame_w, self._frame_h, effective_ms)

        # Update W/H spinners to match frame's native unit layout
        native_cols = self._preview.native_unit_cols()
        self._loading = True
        self._preview_w_spin.setRange(1, max(1, self._preview.total_units()))
        self._preview_w_spin.setValue(native_cols)
        self._preview_h_label.setText(str(self._preview.unit_rows()))
        self._loading = False

        self._filmstrip.set_frames(
            self._frame_pixmaps, anim.frame_order,
            self._frame_w, self._frame_h)

        # Frame slider
        n_frames = len(anim.frame_order)
        self._frame_slider.setRange(0, max(0, n_frames - 1))
        self._frame_slider.setValue(0)
        self._frame_pos_label.setText(f"1 / {n_frames}" if n_frames else "0 / 0")

        # Tile grid -- show first frame with correct VRAM base tile
        self._tile_grid.set_base_tile(anim.dest_tile)
        if self._frame_pixmaps:
            self._tile_grid.set_frame(
                self._frame_pixmaps[anim.frame_order[0] if anim.frame_order else 0],
                self._frame_w, self._frame_h)
        else:
            self._tile_grid.set_frame(None, 0, 0)

        # Info panel
        self._update_info_panel(anim)

        # VRAM tile-range read-out + overlap check (must run after spinboxes set)
        self._update_range_label()

        # Auto-play
        self._preview.play()
        self._btn_play.setText("\u23f8 Pause")

    def _clear_display(self):
        """Clear all display widgets when no animation is selected."""
        self._current_anim = None
        self._frame_pixmaps = []
        self._frame_images = []
        self._preview.set_frames([], [], 16, 16, 267)
        self._filmstrip.set_frames([], [], 16, 16)
        self._tile_grid.set_frame(None, 0, 0)
        self._frame_slider.setRange(0, 0)
        self._frame_pos_label.setText("0 / 0")
        for lbl in self._info_labels.values():
            lbl.setText("\u2014")
        if hasattr(self, "_range_label"):
            self._range_label.setText("")

    def _load_frame_png(self, png_path: str):
        """Load a single frame PNG and append to frame lists."""
        if os.path.isfile(png_path):
            img = QImage(png_path)
            if not img.isNull():
                self._frame_w = img.width()
                self._frame_h = img.height()
                self._frame_images.append(img)
                self._frame_pixmaps.append(QPixmap.fromImage(img))
                return
        # Placeholder for missing/broken frames
        placeholder = QImage(16, 16, QImage.Format.Format_Indexed8)
        placeholder.fill(0)
        self._frame_images.append(placeholder)
        pm = QPixmap(16, 16)
        pm.fill(QColor(255, 0, 255))
        self._frame_pixmaps.append(pm)

    def _extract_palette_from_frames(self):
        """Extract palette from the first frame's color table as fallback."""
        self._palette_colors = []
        if self._frame_images:
            img = self._frame_images[0]
            ct = img.colorTable()
            if ct:
                for entry in ct[:16]:
                    r = (entry >> 16) & 0xFF
                    g = (entry >> 8) & 0xFF
                    b = entry & 0xFF
                    self._palette_colors.append(clamp_to_gba(r, g, b))
        while len(self._palette_colors) < 16:
            self._palette_colors.append((0, 0, 0))

    def _render_frame_with_palette(self, img: QImage,
                                   palette: List[Color]) -> QPixmap:
        """Render an indexed image with a specific palette applied."""
        rendered = QImage(img)
        ct = []
        for i, (r, g, b) in enumerate(palette[:16]):
            ct.append(qRgba(r, g, b, 0 if i == 0 else 255))
        # Pad color table if image has more entries
        while len(ct) < rendered.colorCount():
            ct.append(qRgba(0, 0, 0, 255))
        if ct:
            rendered.setColorTable(ct)
        return QPixmap.fromImage(rendered)

    def _rerender_frames_with_palette(self):
        """Re-render all frame pixmaps using the current palette."""
        new_pixmaps = []
        for img in self._frame_images:
            if img.format() == QImage.Format.Format_Indexed8 and self._palette_colors:
                new_pixmaps.append(
                    self._render_frame_with_palette(img, self._palette_colors))
            else:
                new_pixmaps.append(QPixmap.fromImage(img))
        self._frame_pixmaps = new_pixmaps

    def _update_info_panel(self, anim: TileAnimation):
        """Update the Info group labels."""
        self._info_labels["anim_name"].setText(anim.display_name)
        self._info_labels["tileset_name"].setText(
            anim.tileset_name.replace("_", " ").title())
        self._info_labels["tileset_type"].setText(
            anim.tileset_type.title())

        n_frames = len(anim.frames)
        self._info_labels["frames"].setText(str(n_frames))

        fw = getattr(self, "_frame_w", 0) or 0
        fh = getattr(self, "_frame_h", 0) or 0
        if fw and fh:
            self._info_labels["frame_size"].setText(f"{fw} \u00d7 {fh} px")
            derived_tiles = (fw // 8) * (fh // 8)
            note = ""
            if anim.tile_count and derived_tiles != anim.tile_count:
                note = f"  (Tile Amount set to {anim.tile_count})"
            self._info_labels["tiles_frame"].setText(
                f"{derived_tiles} ({fw // 8}\u00d7{fh // 8} grid){note}")
        else:
            self._info_labels["frame_size"].setText("\u2014")
            self._info_labels["tiles_frame"].setText("\u2014")

        self._info_labels["src_symbol"].setText(
            f"sTilesetAnims_{anim.anim_id}" if anim.anim_id else "\u2014")
        self._info_labels["init_func"].setText(anim.init_func or "\u2014")
        self._info_labels["frame_dir"].setText(anim.anim_dir or "\u2014")

    def _overlapping_anims(self, dest: int, count: int):
        """Return a list of (other_anim, first, last) for every OTHER animation
        in the current tileset whose VRAM tile range overlaps [dest, dest+count).

        Two animations sharing any tile fight over it every game frame — the
        classic "the animation is messed up / shows extra frames in-game" bug.
        """
        results = []
        cur = self._current_anim
        others = getattr(self, "_current_tileset_anims", []) or []
        a0, a1 = dest, dest + max(1, count) - 1
        for other in others:
            if other is cur:
                continue
            oc = other.tile_count or 1
            b0, b1 = other.dest_tile, other.dest_tile + oc - 1
            if a0 <= b1 and b0 <= a1:            # ranges intersect
                lo, hi = max(a0, b0), min(a1, b1)
                results.append((other, lo, hi))
        return results

    def _update_range_label(self):
        """Refresh the VRAM tile-range read-out and colour it red on a clash."""
        if not hasattr(self, "_range_label"):
            return
        if self._loading or not self._current_anim:
            self._range_label.setText("")
            return
        dest = self._start_tile_spin.value()
        count = self._tile_amount_spin.value()
        last = dest + max(1, count) - 1
        base = (f"Uses VRAM tiles <b>0x{dest:X}–0x{last:X}</b> "
                f"({dest}–{last}, {count} tile{'s' if count != 1 else ''}).")

        # Warnings, most important first. VRAM tile numbering is CONTINUOUS:
        # primary tileset = 0x000..0x27F, secondary continues 0x280..0x3FF
        # (NUM_TILES_IN_PRIMARY = 640). An animation SUPPLIES its own tiles to
        # VRAM, so any slot in the tileset's half of VRAM is valid — it is NOT
        # limited to the static tiles.png count.
        NUM_TILES_IN_PRIMARY = 640
        ttype = getattr(self._current_anim, "tileset_type", "")
        if ttype == "secondary" and dest < NUM_TILES_IN_PRIMARY:
            warns.append(
                f"⚠ This is a <b>secondary</b> tileset — its tiles start at "
                f"<b>0x280</b> (640). 0x{dest:X} is in the primary tileset's "
                f"range, so this won't point at the right tiles in-game.")
        if last > 1023:
            warns.append(
                f"⚠ Range runs past the last VRAM tile (0x3FF). "
                f"{count} tiles from 0x{dest:X} overflows — lower the start tile "
                f"or the tile amount.")
        clashes = self._overlapping_anims(dest, count)
        if clashes:
            names = ", ".join(
                f"{o.name.replace('_', ' ').title()} "
                f"(0x{lo:X}–0x{hi:X})" for o, lo, hi in clashes)
            warns.append(
                f"⚠ Overlaps: {names}. Both animations will fight over those "
                f"tiles in-game — move this one to a free tile.")

        if warns:
            self._range_label.setStyleSheet(
                "font-size: 11px; margin-left: 4px; color: #ff5555;")
            self._range_label.setText(base + "<br>" + "<br>".join(warns))
        else:
            self._range_label.setStyleSheet(
                "font-size: 11px; margin-left: 4px; color: #888;")
            self._range_label.setText(base)

    def _update_fps_label(self, divisor: int):
        """Update the fps/ms display label."""
        if divisor > 0:
            fps = 60.0 / divisor
            ms = (divisor / 60.0) * 1000.0
            self._fps_ms_label.setText(f"= {fps:.1f} fps ({ms:.0f} ms)")
        else:
            self._fps_ms_label.setText("= 0 fps")

    # ------------------------------------------------------------------
    #  Playback Controls
    # ------------------------------------------------------------------

    def _toggle_play(self):
        if self._preview.is_playing():
            self._preview.stop()
            self._btn_play.setText("\u25b6 Play")
        else:
            self._preview.play()
            self._btn_play.setText("\u23f8 Pause")

    def _on_speed_changed(self, value: int):
        self._speed_label.setText(f"{value}%")
        if self._current_anim:
            speed_pct = value / 100.0
            base_ms = self._current_anim.frame_duration_ms
            effective_ms = max(16, int(base_ms / speed_pct)
                               if speed_pct > 0 else int(base_ms))
            self._preview._interval_ms = effective_ms
            if self._preview.is_playing():
                self._preview._timer.setInterval(effective_ms)

    def _on_zoom_changed(self, idx: int):
        z = self._zoom_combo.currentData()
        if z is not None:
            self._preview.set_scale(z)

    def _on_metatile_toggled(self, checked: bool):
        """Switch between 16x16 metatile and 8x8 tile units."""
        self._preview.set_metatile(checked)
        # Update W spinner to match new unit size
        self._loading = True
        native = self._preview.native_unit_cols()
        self._preview_w_spin.setRange(1, max(1, self._preview.total_units()))
        self._preview_w_spin.setValue(native)
        self._preview_h_label.setText(str(self._preview.unit_rows()))
        self._loading = False

    def _on_preview_w_changed(self, cols: int):
        if self._loading:
            return
        self._preview.set_unit_columns(cols)
        self._preview_h_label.setText(str(self._preview.unit_rows()))

    def _on_preview_wrap_reset(self):
        """Reset W to the native frame width."""
        native = self._preview.native_unit_cols()
        self._preview_w_spin.setValue(native)

    def _on_preview_frame_changed(self, idx: int):
        """Preview advanced a frame -- sync filmstrip and slider."""
        self._filmstrip.set_current(idx)
        self._frame_slider.blockSignals(True)
        self._frame_slider.setValue(idx)
        self._frame_slider.blockSignals(False)

        n = len(self._current_anim.frame_order) if self._current_anim else 0
        self._frame_pos_label.setText(f"{idx + 1} / {n}")

        # Update tile grid with current frame
        self._update_tile_grid_for_frame(idx)

    def _on_frame_slider(self, value: int):
        """User dragged frame slider -- stop playback, show frame."""
        self._preview.stop()
        self._btn_play.setText("\u25b6 Play")
        self._preview.set_frame(value)
        self._filmstrip.set_current(value)
        n = len(self._current_anim.frame_order) if self._current_anim else 0
        self._frame_pos_label.setText(f"{value + 1} / {n}")
        self._update_tile_grid_for_frame(value)

    def _prev_frame(self):
        val = self._frame_slider.value()
        if val > 0:
            self._preview.stop()
            self._btn_play.setText("\u25b6 Play")
            self._frame_slider.setValue(val - 1)

    def _next_frame(self):
        val = self._frame_slider.value()
        if val < self._frame_slider.maximum():
            self._preview.stop()
            self._btn_play.setText("\u25b6 Play")
            self._frame_slider.setValue(val + 1)

    def _on_filmstrip_click(self, idx: int):
        self._preview.stop()
        self._btn_play.setText("\u25b6 Play")
        self._preview.set_frame(idx)
        self._frame_slider.blockSignals(True)
        self._frame_slider.setValue(idx)
        self._frame_slider.blockSignals(False)
        n = len(self._current_anim.frame_order) if self._current_anim else 0
        self._frame_pos_label.setText(f"{idx + 1} / {n}")
        self._update_tile_grid_for_frame(idx)

    def _on_filmstrip_right_click(self, idx: int, pos):
        """Context menu on filmstrip frame."""
        if not self._current_anim:
            return
        anim = self._current_anim
        frame_idx = anim.frame_order[idx] if idx < len(anim.frame_order) else -1
        if frame_idx < 0 or frame_idx >= len(anim.frames):
            return
        frame = anim.frames[frame_idx]

        menu = QMenu(self)
        act_open = menu.addAction(f"Open frame {frame_idx} in Explorer")
        act_replace = menu.addAction(f"Replace frame {frame_idx} PNG...")
        menu.addSeparator()
        act_add = menu.addAction("Add new frame...")
        act_delete = None
        if len(anim.frames) > 1:
            act_delete = menu.addAction(f"Remove frame {frame_idx} from C source")

        chosen = menu.exec(pos)
        if chosen == act_open:
            if os.path.isfile(frame.png_path):
                open_in_folder(frame.png_path)
        elif chosen == act_replace:
            self._replace_specific_frame(frame_idx)
        elif chosen == act_add:
            self._add_frame()
        elif act_delete and chosen == act_delete:
            self._delete_specific_frame(frame_idx)

    def _update_tile_grid_for_frame(self, seq_idx: int):
        """Update tile grid to show the frame at sequence index."""
        if not self._current_anim or not self._frame_pixmaps:
            return
        anim = self._current_anim
        if seq_idx < 0 or seq_idx >= len(anim.frame_order):
            return
        frame_idx = anim.frame_order[seq_idx]
        if 0 <= frame_idx < len(self._frame_pixmaps):
            self._tile_grid.set_frame(
                self._frame_pixmaps[frame_idx],
                self._frame_w, self._frame_h)

    def _toggle_tile_grid_layout(self, horizontal: bool):
        """Toggle the tile grid between grid and horizontal strip layout."""
        self._tile_grid.set_horizontal(horizontal)
        self._btn_tile_horizontal.setText("Grid" if horizontal else "Horizontal")

    # ------------------------------------------------------------------
    #  Property Editing (divisor, start tile, etc.)
    # ------------------------------------------------------------------

    def _on_divisor_changed(self, value: int):
        if self._loading:
            return
        self._update_fps_label(value)
        # Update live preview speed
        if self._current_anim:
            speed_pct = self._speed_slider.value() / 100.0
            base_ms = (value / 60.0) * 1000.0
            effective_ms = max(16, int(base_ms / speed_pct)) if speed_pct > 0 else int(base_ms)
            self._preview._interval_ms = effective_ms
            if self._preview.is_playing():
                self._preview._timer.setInterval(effective_ms)
            self._mark_props_dirty()

    def _mark_props_dirty(self):
        """Mark that property spinners have been changed (pending flush)."""
        if self._loading or not self._current_anim:
            return
        self._dirty = True
        self.modified.emit()

    def has_unsaved_changes(self) -> bool:
        """Check if there are pending property changes not yet written."""
        if not self._dirty or not self._current_anim:
            return False
        anim = self._current_anim
        return (self._divisor_spin.value() != anim.divisor
                or self._start_tile_spin.value() != anim.dest_tile
                or self._tile_amount_spin.value() != anim.tile_count
                or self._phase_spin.value() != anim.phase
                or self._counter_max_spin.value() != (anim.counter_max or 0))

    def flush_to_disk(self) -> tuple:
        """Write pending property changes to C source.

        Returns ``(ok_count, error_list)`` matching the flush_to_disk
        convention used by other PorySuite editors.
        """
        if not self._current_anim or not self._project_dir:
            return (0, [])

        anim = self._current_anim
        new_div = self._divisor_spin.value()
        new_start = self._start_tile_spin.value()
        new_amount = self._tile_amount_spin.value()
        new_phase = self._phase_spin.value()
        new_cmax = self._counter_max_spin.value()

        ok = 0
        errors: list[str] = []

        if new_div != anim.divisor:
            if write_timing_to_source(self._project_dir, anim, new_div):
                anim.divisor = new_div
                ok += 1
            else:
                errors.append("speed divisor")

        if new_start != anim.dest_tile:
            if write_start_tile_to_source(self._project_dir, anim, new_start):
                anim.dest_tile = new_start
                ok += 1
            else:
                errors.append("start tile")

        if new_amount != anim.tile_count:
            if write_tile_amount_to_source(self._project_dir, anim, new_amount):
                anim.tile_count = new_amount
                ok += 1
            else:
                errors.append("tile amount")

        if new_phase != anim.phase:
            if write_phase_to_source(self._project_dir, anim, new_phase):
                anim.phase = new_phase
                ok += 1
            else:
                errors.append("phase")

        if new_cmax != (anim.counter_max or 0):
            if write_counter_max_to_source(self._project_dir, anim, new_cmax):
                anim.counter_max = new_cmax
                ok += 1
            else:
                errors.append("counter max")

        if ok > 0 or not errors:
            self._dirty = False

        # Last-chance collision warning: if this animation's tile range now
        # overlaps another in the same tileset, both will fight over those
        # tiles in-game. Warn loudly on the explicit save.
        clashes = self._overlapping_anims(anim.dest_tile, anim.tile_count or 1)
        if clashes:
            names = "\n".join(
                f"  • {o.name.replace('_', ' ').title()} "
                f"(tiles 0x{lo:X}–0x{hi:X})" for o, lo, hi in clashes)
            QMessageBox.warning(
                self, "Animations Overlap In VRAM",
                f"'{anim.display_name}' uses tiles "
                f"0x{anim.dest_tile:X}–0x{anim.dest_tile + (anim.tile_count or 1) - 1:X}, "
                f"which overlaps:\n\n{names}\n\n"
                f"Two animations sharing tiles fight over them every frame — "
                f"in-game this looks like flickering or extra frames. Give this "
                f"animation its own Start Tile so its range doesn't overlap the "
                f"others.")

        return (ok, errors)

    # ------------------------------------------------------------------
    #  Palette Operations
    # ------------------------------------------------------------------

    def _on_palette_slot_changed(self, slot: int):
        """User changed palette slot dropdown."""
        if self._loading or not self._current_anim:
            return
        if slot < 0 or slot >= len(self._all_palettes):
            return

        self._palette_colors = list(self._all_palettes[slot])
        self._loading = True
        self._pal_row.set_colors(self._palette_colors)
        self._loading = False

        # Re-render frames with new palette
        self._rerender_frames_with_palette()
        self._refresh_display()

    def _on_palette_changed(self):
        """User edited a palette swatch -- apply to all frame PNGs."""
        if self._loading or not self._current_anim:
            return
        new_colors = self._pal_row.colors()
        self._palette_colors = new_colors
        self._apply_palette_to_frames(new_colors)

    def _apply_palette_to_frames(self, colors: List[Color]):
        """Write the palette into all frame PNGs for this animation."""
        if not self._current_anim:
            return

        # Build Qt color table
        ct = []
        for i, (r, g, b) in enumerate(colors[:16]):
            if i == 0:
                ct.append(qRgba(r, g, b, 0))  # index 0 = transparent
            else:
                ct.append(qRgba(r, g, b, 255))

        anim = self._current_anim

        for i, frame in enumerate(anim.frames):
            if not os.path.isfile(frame.png_path):
                continue
            img = QImage(frame.png_path)
            if img.isNull():
                continue
            if img.format() != QImage.Format.Format_Indexed8:
                continue
            img.setColorTable(ct)
            img.save(frame.png_path, "PNG")
            if i < len(self._frame_images):
                self._frame_images[i] = img
                self._frame_pixmaps[i] = QPixmap.fromImage(img)

        # Also update the palette slot .pal file
        slot = self._palette_slot_combo.currentIndex()
        if slot >= 0 and self._current_anim:
            ts = self._current_anim
            pal_dir = os.path.join(
                self._project_dir, "data", "tilesets",
                ts.tileset_type, ts.tileset_name, "palettes")
            pal_path = os.path.join(pal_dir, f"{slot:02d}.pal")
            if os.path.isdir(pal_dir):
                write_jasc_pal(pal_path, colors)
                # Update cached palette
                if slot < len(self._all_palettes):
                    self._all_palettes[slot] = list(colors)

        self._refresh_display()
        self._dirty = True
        self.modified.emit()

    def _bake_palette_to_frames(self):
        """Explicitly write the current palette into every frame image on disk.

        The preview renders the live/edited palette, but each frame PNG keeps
        the palette last baked into it, so an external editor shows stale
        colours. This forces them into sync.
        """
        if not self._current_anim:
            QMessageBox.information(self, "Bake Palette",
                                    "Select an animation first.")
            return
        colors = self._pal_row.colors()
        frames = self._current_anim.frames
        indexed = non_indexed = 0
        for frame in frames:
            if not os.path.isfile(frame.png_path):
                continue
            img = QImage(frame.png_path)
            if img.isNull():
                continue
            if img.format() == QImage.Format.Format_Indexed8:
                indexed += 1
            else:
                non_indexed += 1
        # _apply_palette_to_frames writes the color table into each indexed frame
        # PNG (and the slot .pal), then refreshes + marks dirty.
        self._apply_palette_to_frames(colors)
        msg = (f"Baked the current palette into {indexed} frame image(s).\n\n"
               f"Opening them in an external editor (e.g. GIMP) now shows these "
               f"colours.")
        if non_indexed:
            msg += (f"\n\nSkipped {non_indexed} frame(s) that aren't "
                    f"palette-indexed (nothing to bake into a full-colour "
                    f"image).")
        QMessageBox.information(self, "Bake Palette", msg)

    @staticmethod
    def _convert_to_indexed_png(src_path: str, dest_png: str) -> bool:
        """Convert any image file to an indexed PNG, keeping its OWN palette.

        Used when creating a NEW animation from frame files: unlike replacing a
        frame in an existing animation (which remaps onto the tab's live
        palette), a new animation has no palette yet, so the source's own
        indexed colour table becomes the animation's palette. An indexed .bmp
        (legacy AnimEdit tooling) is read by Qt as Format_Indexed8 and saved
        straight through, preserving slot order. A source Qt reads as full
        colour is quantised to an indexed image as a best effort. Returns False
        only if the file can't be read at all."""
        img = QImage(src_path)
        if img.isNull():
            return False
        if img.format() != QImage.Format.Format_Indexed8:
            img = img.convertToFormat(QImage.Format.Format_Indexed8)
        return bool(img.save(dest_png, "PNG"))

    def _write_frame_from_source(self, src_path: str, dest_png: str) -> bool:
        """Copy/convert a chosen image file into a frame's indexed PNG.

        A ``.png`` is copied verbatim (keeps its exact bytes + embedded
        palette). A ``.bmp`` (legacy tile-anim tools used indexed BMPs) is
        REMAPPED onto the tab's current palette by matching each pixel's colour
        to the nearest palette slot — see :meth:`_remap_image_to_palette` for
        why raw indices can't be trusted. Returns False on a load failure."""
        ext = os.path.splitext(src_path)[1].lower()
        if ext == ".png":
            shutil.copy2(src_path, dest_png)
            return True
        img = QImage(src_path)
        if img.isNull():
            return False
        return self._remap_image_to_palette(img, dest_png)

    def _remap_image_to_palette(self, img: QImage, dest_png: str) -> bool:
        """Write *img* into *dest_png* as an indexed PNG whose indices are the
        tab's CURRENT palette slots, chosen by matching each pixel's colour.

        Why not keep the source's own indices? A legacy indexed BMP stores the
        same colours but often in a DIFFERENT slot order than this tileset's
        palette (and some legacy BMP variants Qt reads as RGB, dropping indices
        entirely). Keeping the raw indices then renders every colour against the
        wrong slot — the scrambled import. Matching by COLOUR instead lands each
        pixel on the right slot regardless of the source's index order, so the
        frame comes in looking exactly like it should under the live palette."""
        pal = list(self._palette_colors or [])
        if not pal:
            # No palette loaded — best-effort straight indexed save.
            if img.format() != QImage.Format.Format_Indexed8:
                img = img.convertToFormat(QImage.Format.Format_Indexed8)
            img.save(dest_png, "PNG")
            return True
        pal = (pal + [(0, 0, 0)] * 16)[:16]

        # Exact-match table on GBA-quantised (5-bit) colour, so an 8-bit source
        # colour like 255 matches a 5-bit palette 248; nearest as the fallback.
        def _q(c):
            return c & 0xF8
        exact = {}
        for i, (r, g, b) in enumerate(pal):
            exact.setdefault((_q(r), _q(g), _q(b)), i)

        out = QImage(img.width(), img.height(), QImage.Format.Format_Indexed8)
        out.setColorTable([
            qRgba(r, g, b, 0 if i == 0 else 255)
            for i, (r, g, b) in enumerate(pal)
        ])
        for y in range(img.height()):
            for x in range(img.width()):
                px = img.pixel(x, y)
                r, g, b = (px >> 16) & 0xFF, (px >> 8) & 0xFF, px & 0xFF
                idx = exact.get((_q(r), _q(g), _q(b)))
                if idx is None:
                    best, bd = 0, 1 << 30
                    for i, (pr, pg, pb) in enumerate(pal):
                        d = (pr - r) ** 2 + (pg - g) ** 2 + (pb - b) ** 2
                        if d < bd:
                            bd, best = d, i
                    idx = best
                out.setPixel(x, y, idx)
        out.save(dest_png, "PNG")
        return True

    def _import_pal(self):
        """Import a JASC .pal file and apply to all frames."""
        if not self._current_anim:
            return
        start_dir = self._current_anim.anim_dir or ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Import JASC Palette", start_dir, "JASC Palette (*.pal)")
        if not path:
            return

        colors = read_jasc_pal(path, max_colors=16)
        if not colors:
            QMessageBox.warning(self, "Import Failed",
                                f"Could not read palette from:\n{path}")
            return

        self._loading = True
        self._pal_row.set_colors(colors)
        self._loading = False

        self._palette_colors = colors
        self._apply_palette_to_frames(colors)

        QMessageBox.information(
            self, "Palette Imported",
            f"Loaded {min(16, len(colors))} colors from:\n"
            f"{os.path.basename(path)}\n\n"
            f"Applied to {self._current_anim.frame_count} frames.")

    def _export_pal(self):
        """Export the current palette as a JASC .pal file."""
        if not self._palette_colors:
            return
        start_dir = self._current_anim.anim_dir if self._current_anim else ""
        path, _ = QFileDialog.getSaveFileName(
            self, "Export JASC Palette",
            os.path.join(start_dir, "palette.pal"),
            "JASC Palette (*.pal)")
        if not path:
            return

        ok = write_jasc_pal(path, self._palette_colors)
        if ok:
            QMessageBox.information(
                self, "Palette Exported",
                f"Saved {len(self._palette_colors)} colors to:\n{path}")
        else:
            QMessageBox.warning(self, "Export Failed",
                                f"Could not write palette to:\n{path}")

    def _import_palette_from_png(self):
        """Auto-extract palette from an indexed PNG and apply to all frames."""
        self._do_import_palette_from_png(manual=False)

    def _import_palette_from_png_manual(self):
        """Open the manual palette picker on any PNG and apply to all frames."""
        self._do_import_palette_from_png(manual=True)

    def _do_import_palette_from_png(self, manual: bool):
        if not self._current_anim:
            return
        start_dir = self._current_anim.anim_dir or ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Select PNG" if manual else "Select Indexed PNG",
            start_dir, "PNG Images (*.png)")
        if not path:
            return

        if manual:
            from ui.dialogs.manual_palette_pick_dialog import (
                pick_palette_manually_from_path,
            )
            colors = pick_palette_manually_from_path(
                path, target_colors=16, parent=self,
            )
            if colors is None:
                return
            n_used = sum(1 for c in colors if c != (0, 0, 0))
        else:
            img = QImage(path)
            if img.isNull():
                QMessageBox.warning(self, "Import Failed",
                                    f"Could not load image:\n{path}")
                return

            if img.format() != QImage.Format.Format_Indexed8:
                QMessageBox.warning(
                    self, "Not an Indexed PNG",
                    "This PNG is not in indexed (palette) mode.\n\n"
                    "The image must be saved as an indexed-color PNG\n"
                    "(8-bit, up to 16 colors) so its embedded palette\n"
                    "can be extracted — or use 'Import PNG Manually'.")
                return

            ct = img.colorTable()
            if not ct:
                QMessageBox.warning(self, "Empty Palette",
                                    "The PNG has no color table entries.")
                return

            colors: List[Color] = []
            for entry in ct[:16]:
                r = (entry >> 16) & 0xFF
                g = (entry >> 8) & 0xFF
                b = entry & 0xFF
                colors.append(clamp_to_gba(r, g, b))
            while len(colors) < 16:
                colors.append((0, 0, 0))
            n_used = min(16, len(ct))

        self._loading = True
        self._pal_row.set_colors(colors)
        self._loading = False

        self._palette_colors = colors
        self._apply_palette_to_frames(colors)

        QMessageBox.information(
            self, "Palette Imported",
            f"Extracted {n_used} colors from:\n"
            f"{os.path.basename(path)}\n\n"
            f"Applied to {self._current_anim.frame_count} frames.")

    # ------------------------------------------------------------------
    #  Add / Remove Animation
    # ------------------------------------------------------------------

    def _add_animation(self):
        """Add a new animation to the currently selected tileset."""
        if not self._project_dir:
            return

        ts_idx = self._tileset_combo.currentIndex()
        if ts_idx < 0 or ts_idx >= len(self._tileset_order):
            return
        ts = self._tileset_order[ts_idx]
        if ts is None:
            return

        dlg = _AddAnimDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        vals = dlg.get_values()
        if not vals["anim_name"]:
            QMessageBox.warning(self, "Invalid Name",
                                "Animation name cannot be empty.")
            return
        if not vals["png_paths"]:
            QMessageBox.warning(self, "No Frames",
                                "Select at least one frame image "
                                "(indexed PNG or BMP).")
            return

        # The backend copies each frame file verbatim as N.png, so anything
        # that isn't already a PNG (e.g. a legacy indexed .bmp) must be
        # converted to an indexed PNG first — preserving the source's OWN
        # palette, since a brand-new animation has no palette to match against.
        import tempfile
        frame_paths = []
        tmp_files = []
        convert_failed = None
        for p in vals["png_paths"]:
            if os.path.splitext(p)[1].lower() == ".png":
                frame_paths.append(p)
                continue
            tmp = os.path.join(
                tempfile.gettempdir(),
                os.path.splitext(os.path.basename(p))[0] + "_animnew.png")
            if not self._convert_to_indexed_png(p, tmp):
                convert_failed = p
                break
            tmp_files.append(tmp)
            frame_paths.append(tmp)

        if convert_failed:
            for t in tmp_files:
                try:
                    os.remove(t)
                except OSError:
                    pass
            QMessageBox.warning(
                self, "Import Failed",
                f"Could not read this frame image:\n{convert_failed}\n\n"
                f"Use an indexed PNG or a 16-colour indexed BMP.")
            return

        ts_type = "secondary" if ts["is_secondary"] else "primary"
        result = add_animation_to_tileset(
            self._project_dir,
            tileset_name=ts["dir_name"],
            tileset_type=ts_type,
            anim_name=vals["anim_name"],
            start_tile=vals["start_tile"],
            tile_amount=vals["tile_amount"],
            divisor=vals["divisor"],
            frame_png_paths=frame_paths,
        )

        for t in tmp_files:
            try:
                os.remove(t)
            except OSError:
                pass

        if result:
            self._dirty = True
            self.modified.emit()
            self._load_data()
            # Try to re-select the tileset
            for i, t in enumerate(self._tileset_order):
                if t and t["dir_name"] == ts["dir_name"]:
                    self._tileset_combo.setCurrentIndex(i)
                    break
            QMessageBox.information(
                self, "Animation Added",
                f"New animation '{vals['anim_name']}' added to "
                f"{ts['name']}.\n\n"
                f"tileset_anims.c updated. Rebuild the ROM to see it in-game.")
        else:
            QMessageBox.warning(
                self, "Add Failed",
                "Could not add the animation. Check that tileset_anims.c\n"
                "is accessible and the tileset name is correct.")

    def _remove_animation(self):
        """Remove the currently selected animation."""
        if not self._current_anim or not self._project_dir:
            return
        anim = self._current_anim

        reply = QMessageBox.question(
            self, "Remove Animation",
            f"Remove '{anim.display_name}' from tileset_anims.c?\n\n"
            f"This will remove all C source references.\n"
            f"PNG files will NOT be deleted.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return

        ok = remove_animation_from_tileset(self._project_dir, anim)
        if ok:
            self._dirty = True
            self.modified.emit()
            ts_idx = self._tileset_combo.currentIndex()
            self._load_data()
            if ts_idx < self._tileset_combo.count():
                self._tileset_combo.setCurrentIndex(ts_idx)
            QMessageBox.information(
                self, "Animation Removed",
                f"'{anim.display_name}' removed from tileset_anims.c.\n"
                f"PNG files were left in place.\n"
                f"Rebuild the ROM to see the change in-game.")
        else:
            QMessageBox.warning(
                self, "Remove Failed",
                "Could not remove the animation from tileset_anims.c.\n"
                "The C source may have been modified in an unexpected way.")

    def _cleanup_frames(self):
        """Open the cleanup dialog to find/delete orphaned frame PNGs."""
        if not self._project_dir:
            return
        from ui.dialogs.tile_anim_cleanup_dialog import TileAnimCleanupDialog
        dlg = TileAnimCleanupDialog(self._project_dir, parent=self)
        dlg.exec()
        # Reload in case whole animation folders were removed.
        ts_idx = self._tileset_combo.currentIndex()
        anim_idx = self._anim_combo.currentIndex()
        self._load_data()
        if ts_idx < self._tileset_combo.count():
            self._tileset_combo.setCurrentIndex(ts_idx)
        if anim_idx < self._anim_combo.count():
            self._anim_combo.setCurrentIndex(anim_idx)

    def _rename_animation(self):
        """Rename the currently selected animation everywhere."""
        if not self._current_anim or not self._project_dir:
            return
        anim = self._current_anim
        ts_pretty = anim.tileset_name.replace("_", " ").title()

        new_name, ok = QInputDialog.getText(
            self, "Rename Animation",
            f"New name for '{anim.display_name}':\n\n"
            f"Use letters, numbers and spaces only. The tileset it belongs to\n"
            f"('{ts_pretty}') stays the same — only this animation's own name\n"
            f"changes. Its source symbols and frame folder are renamed to match.",
            text=anim.name.replace("_", " "))
        if not ok or not new_name.strip():
            return

        result = rename_animation(self._project_dir, anim, new_name.strip())
        if result is None:
            QMessageBox.warning(
                self, "Rename Failed",
                "Could not rename the animation.\n\n"
                "The new name may be empty, unchanged, or already used by "
                "another animation in this tileset. Pick a different name and "
                "try again.")
            return

        new_id = result.anim_id
        self._dirty = True
        self.modified.emit()

        ts_idx = self._tileset_combo.currentIndex()
        self._load_data()
        if ts_idx < self._tileset_combo.count():
            self._tileset_combo.setCurrentIndex(ts_idx)

        # Re-select the renamed animation by its new source id.
        anims = getattr(self, "_current_tileset_anims", [])
        for i, a in enumerate(anims):
            if a.anim_id == new_id:
                self._anim_combo.setCurrentIndex(i)
                self._on_anim_selected(i)
                break

        QMessageBox.information(
            self, "Animation Renamed",
            f"Renamed to '{result.display_name}'.\n\n"
            f"Its source code and frame folder were updated to match.\n"
            f"Rebuild the ROM to see the change in-game.")

    # ------------------------------------------------------------------
    #  Frame Operations
    # ------------------------------------------------------------------

    def _save_frame_image(self):
        """Export current frame's PNG to user-chosen location."""
        if not self._current_anim:
            return
        anim = self._current_anim
        seq_idx = self._preview.current_frame()
        if seq_idx < 0 or seq_idx >= len(anim.frame_order):
            return
        frame_idx = anim.frame_order[seq_idx]
        if frame_idx < 0 or frame_idx >= len(anim.frames):
            return

        src_path = anim.frames[frame_idx].png_path
        if not os.path.isfile(src_path):
            QMessageBox.warning(self, "File Not Found",
                                f"Source frame not found:\n{src_path}")
            return

        save_dir = (getattr(self, "_last_import_dir", "")
                    or os.path.dirname(src_path))
        dest, _ = QFileDialog.getSaveFileName(
            self, "Save Frame Image",
            os.path.join(save_dir, f"frame_{frame_idx}.png"),
            "PNG Images (*.png)")
        if not dest:
            return
        self._last_import_dir = os.path.dirname(dest)

        try:
            shutil.copy2(src_path, dest)
            QMessageBox.information(self, "Saved",
                                    f"Frame saved to:\n{dest}")
        except Exception as e:
            QMessageBox.warning(self, "Save Failed", f"Could not save:\n{e}")

    def _sync_tile_amount_after_import(self) -> str:
        """After importing a frame, make Tile Amount match the frame's real
        size.  A GBA tile is 8×8, so a W×H-pixel frame holds
        (W/8)×(H/8) tiles.  When the newly imported frame is a different size
        (e.g. the user swapped in a bigger image), the animation's Tile Amount
        must follow or the engine copies the wrong number of tiles.  Writes the
        new value to tileset_anims.c and updates the spin box.  Returns a short
        plain-English note describing the change (empty string if nothing
        changed).
        """
        anim = self._current_anim
        if not anim or not self._project_dir:
            return ""
        fw = getattr(self, "_frame_w", 0) or 0
        fh = getattr(self, "_frame_h", 0) or 0
        if fw < 8 or fh < 8:
            return ""
        derived = (fw // 8) * (fh // 8)
        if derived < 1 or derived == anim.tile_count:
            return ""
        old = anim.tile_count
        if not write_tile_amount_to_source(self._project_dir, anim, derived):
            return ""
        anim.tile_count = derived
        self._loading = True
        self._tile_amount_spin.setValue(derived)
        self._loading = False
        self._update_info_panel(anim)
        return (f"\n\nThe image is {fw}×{fh}, so Tile Amount was updated "
                f"from {old} to {derived} tiles to match.")

    # ------------------------------------------------------------------
    #  Resize whole animation
    # ------------------------------------------------------------------

    @staticmethod
    def _resize_indexed_image(img: QImage, new_w: int, new_h: int,
                              method: str) -> QImage:
        """Return a new W×H indexed image from *img*, preserving palette slots.

        ``method='scale'`` stretches/squishes the picture to the new size using
        nearest-neighbour sampling (no blur, keeps pixel-art crisp and keeps the
        exact palette indices). ``method='crop'`` keeps every pixel where it is,
        cutting off anything past the new size and padding new area with slot 0.
        """
        if img.format() != QImage.Format.Format_Indexed8:
            img = img.convertToFormat(QImage.Format.Format_Indexed8)
        sw, sh = img.width(), img.height()
        ct = img.colorTable()
        out = QImage(new_w, new_h, QImage.Format.Format_Indexed8)
        if ct:
            out.setColorTable(ct)
        out.fill(0)
        if method == "crop":
            for dy in range(min(new_h, sh)):
                for dx in range(min(new_w, sw)):
                    out.setPixel(dx, dy, img.pixelIndex(dx, dy))
        else:  # scale (nearest-neighbour)
            for dy in range(new_h):
                sy = min(sh - 1, (dy * sh) // new_h) if new_h else 0
                for dx in range(new_w):
                    sx = min(sw - 1, (dx * sw) // new_w) if new_w else 0
                    out.setPixel(dx, dy, img.pixelIndex(sx, sy))
        return out

    def _resize_frames(self):
        """Resize EVERY frame of the current animation to a new pixel size."""
        if not self._current_anim or not self._project_dir:
            QMessageBox.information(self, "Resize Frames",
                                    "Select an animation first.")
            return
        anim = self._current_anim
        cur_w = getattr(self, "_frame_w", 0) or 8
        cur_h = getattr(self, "_frame_h", 0) or 8

        dlg = QDialog(self)
        dlg.setWindowTitle("Resize Animation Frames")
        form = QFormLayout(dlg)

        intro = QLabel(
            f"Every frame of '{anim.display_name}' is currently "
            f"{cur_w}×{cur_h} pixels.\n"
            f"Choose the new size below. GBA tiles are 8×8, so width and "
            f"height\nstep in eights.")
        intro.setWordWrap(True)
        form.addRow(intro)

        w_spin = _NoScrollSpin()
        w_spin.setRange(8, 256)
        w_spin.setSingleStep(8)
        w_spin.setValue(cur_w if cur_w >= 8 else 8)
        form.addRow("New width (px):", w_spin)

        h_spin = _NoScrollSpin()
        h_spin.setRange(8, 256)
        h_spin.setSingleStep(8)
        h_spin.setValue(cur_h if cur_h >= 8 else 8)
        form.addRow("New height (px):", h_spin)

        method_combo = _NoScrollCombo()
        method_combo.addItem("Scale — stretch the picture to the new size", "scale")
        method_combo.addItem("Crop / Pad — keep pixels in place, cut or add space", "crop")
        form.addRow("How:", method_combo)

        tiles_lbl = QLabel("")
        tiles_lbl.setStyleSheet("color:#aaa;font-size:11px;")
        form.addRow("Tiles per frame:", tiles_lbl)

        def _update_tiles():
            w8 = w_spin.value() // 8
            h8 = h_spin.value() // 8
            tiles_lbl.setText(f"{w8 * h8}  ({w8}×{h8} grid)")
        w_spin.valueChanged.connect(lambda _v: _update_tiles())
        h_spin.valueChanged.connect(lambda _v: _update_tiles())
        _update_tiles()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        new_w = (w_spin.value() // 8) * 8
        new_h = (h_spin.value() // 8) * 8
        method = method_combo.currentData()
        if new_w < 8 or new_h < 8:
            return
        if new_w == cur_w and new_h == cur_h:
            QMessageBox.information(
                self, "Resize Frames",
                "The new size matches the current size — nothing to do.")
            return

        done = failed = 0
        for frame in anim.frames:
            path = frame.png_path
            if not os.path.isfile(path):
                failed += 1
                continue
            img = QImage(path)
            if img.isNull():
                failed += 1
                continue
            try:
                out = self._resize_indexed_image(img, new_w, new_h, method)
                if not out.save(path, "PNG"):
                    failed += 1
                    continue
                done += 1
            except Exception:
                failed += 1

        if done == 0:
            QMessageBox.warning(
                self, "Resize Failed",
                "None of the frame images could be resized — they may be "
                "missing or locked by another program.")
            return

        # Point Tile Amount at the new tile count and write it to the source.
        new_tiles = (new_w // 8) * (new_h // 8)
        wrote_amount = write_tile_amount_to_source(
            self._project_dir, anim, new_tiles)
        if wrote_amount:
            anim.tile_count = new_tiles

        self._dirty = True
        self.modified.emit()

        # Reload so the preview, tile grid and Info panel pick up the new size.
        ts_idx = self._tileset_combo.currentIndex()
        anim_idx = self._anim_combo.currentIndex()
        self._load_data()
        if ts_idx < self._tileset_combo.count():
            self._tileset_combo.setCurrentIndex(ts_idx)
        if anim_idx < self._anim_combo.count():
            self._anim_combo.setCurrentIndex(anim_idx)
            self._on_anim_selected(anim_idx)

        msg = (f"Resized {done} frame(s) to {new_w}×{new_h}.\n"
               f"Tile Amount set to {new_tiles} tiles per frame.")
        if not wrote_amount:
            msg += ("\n\n(Couldn't update Tile Amount in the source "
                    "automatically — set it by hand in the properties.)")
        if failed:
            msg += f"\n\n{failed} frame(s) could not be resized and were skipped."
        msg += "\n\nRebuild the ROM to see the change in-game."
        QMessageBox.information(self, "Frames Resized", msg)

    def _replace_frame(self):
        """Replace the currently selected frame's PNG."""
        if not self._current_anim:
            return
        anim = self._current_anim
        seq_idx = self._preview.current_frame()
        if seq_idx < 0 or seq_idx >= len(anim.frame_order):
            return
        frame_idx = anim.frame_order[seq_idx]
        self._replace_specific_frame(frame_idx)

    def _replace_specific_frame(self, frame_idx: int):
        """Replace a specific frame by index."""
        if not self._current_anim:
            return
        anim = self._current_anim
        if frame_idx < 0 or frame_idx >= len(anim.frames):
            return

        frame = anim.frames[frame_idx]
        # Remember the folder the user last browsed to (they're usually replacing
        # several frames from the SAME external folder) instead of resetting to
        # the project's frame folder every time.
        start_dir = (getattr(self, "_last_import_dir", "")
                     or (os.path.dirname(frame.png_path) if frame.png_path else ""))

        path, _ = QFileDialog.getOpenFileName(
            self, f"Replace Frame {frame_idx}",
            start_dir,
            "Images (*.png *.bmp);;PNG Images (*.png);;BMP Images (*.bmp)")
        if not path:
            return
        self._last_import_dir = os.path.dirname(path)

        img = QImage(path)
        if img.isNull():
            QMessageBox.warning(self, "Import Failed",
                                f"Could not load image:\n{path}")
            return

        if img.width() != self._frame_w or img.height() != self._frame_h:
            new_tiles = (img.width() // 8) * (img.height() // 8)
            reply = QMessageBox.question(
                self, "Different Size",
                f"The new image is {img.width()}\u00d7{img.height()}, but the "
                f"existing frames are {self._frame_w}\u00d7{self._frame_h}.\n\n"
                f"That's fine \u2014 the tool will resize this animation to the new "
                f"image and set Tile Amount to {new_tiles} tiles automatically.\n\n"
                f"For the animation to look right in-game, every frame should be "
                f"the same size, so plan to re-import the other frames at "
                f"{img.width()}\u00d7{img.height()} too.\n\n"
                f"Import this frame now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return

        try:
            if not self._write_frame_from_source(path, frame.png_path):
                raise RuntimeError("could not read the source image")
        except Exception as e:
            QMessageBox.warning(self, "Replace Failed",
                                f"Could not import file:\n{e}")
            return

        self._dirty = True
        self.modified.emit()
        self._on_anim_selected(self._anim_combo.currentIndex())
        size_note = self._sync_tile_amount_after_import()

        QMessageBox.information(
            self, "Frame Replaced",
            f"Frame {frame_idx} replaced with:\n{os.path.basename(path)}"
            f"{size_note}")

    def _add_frame(self):
        """Add a new frame PNG to the animation."""
        if not self._current_anim or not self._project_dir:
            return

        anim = self._current_anim
        start_dir = getattr(self, "_last_import_dir", "") or (anim.anim_dir or "")

        path, _ = QFileDialog.getOpenFileName(
            self, "Add New Frame", start_dir,
            "Images (*.png *.bmp);;PNG Images (*.png);;BMP Images (*.bmp)")
        if not path:
            return
        self._last_import_dir = os.path.dirname(path)

        img = QImage(path)
        if img.isNull():
            QMessageBox.warning(self, "Import Failed",
                                f"Could not load image:\n{path}")
            return

        if img.width() != self._frame_w or img.height() != self._frame_h:
            new_tiles = (img.width() // 8) * (img.height() // 8)
            reply = QMessageBox.question(
                self, "Different Size",
                f"The new image is {img.width()}\u00d7{img.height()}, but the "
                f"existing frames are {self._frame_w}\u00d7{self._frame_h}.\n\n"
                f"That's fine \u2014 the tool will resize this animation to the new "
                f"image and set Tile Amount to {new_tiles} tiles automatically.\n\n"
                f"For the animation to look right in-game, every frame should be "
                f"the same size, so plan to re-import the other frames at "
                f"{img.width()}\u00d7{img.height()} too.\n\n"
                f"Add this frame now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return

        reply = QMessageBox.question(
            self, "Add Frame",
            f"Add this image as a new frame to:\n"
            f"{anim.display_name}\n\n"
            f"This will:\n"
            f"  1. Copy the PNG to the frame directory\n"
            f"  2. Add INCBIN + array entry to tileset_anims.c\n\n"
            f"Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return

        # A BMP source (legacy anim tools) is converted to an indexed PNG in a
        # temp file first, since the backend expects a .png to INCBIN.
        add_src = path
        _tmp_png = None
        if os.path.splitext(path)[1].lower() != ".png":
            import tempfile
            _tmp_png = os.path.join(
                tempfile.gettempdir(),
                os.path.splitext(os.path.basename(path))[0] + "_anim.png")
            if not self._write_frame_from_source(path, _tmp_png):
                QMessageBox.warning(self, "Import Failed",
                                    f"Could not read the source image:\n{path}")
                return
            add_src = _tmp_png

        result = add_frame_to_anim(self._project_dir, anim, add_src)
        if _tmp_png and os.path.isfile(_tmp_png):
            try:
                os.remove(_tmp_png)
            except OSError:
                pass
        if result:
            self._dirty = True
            self.modified.emit()
            old_ts = self._tileset_combo.currentIndex()
            old_anim = self._anim_combo.currentIndex()
            self._load_data()
            if old_ts < self._tileset_combo.count():
                self._tileset_combo.setCurrentIndex(old_ts)
            if old_anim < self._anim_combo.count():
                self._anim_combo.setCurrentIndex(old_anim)
                self._on_anim_selected(old_anim)
            size_note = self._sync_tile_amount_after_import()
            QMessageBox.information(
                self, "Frame Added",
                f"New frame added:\n{os.path.basename(result)}{size_note}\n\n"
                f"tileset_anims.c updated. Rebuild to see the change.")
        else:
            QMessageBox.warning(
                self, "Add Failed",
                "Could not add the new frame. Check that the animation\n"
                "directory and tileset_anims.c are accessible.")

    def _delete_frame(self):
        """Delete the currently selected frame."""
        if not self._current_anim:
            return
        anim = self._current_anim
        seq_idx = self._preview.current_frame()
        if seq_idx < 0 or seq_idx >= len(anim.frame_order):
            return
        frame_idx = anim.frame_order[seq_idx]
        self._delete_specific_frame(frame_idx)

    def _delete_specific_frame(self, frame_idx: int):
        """Delete a specific frame by index."""
        if not self._current_anim or not self._project_dir:
            return
        anim = self._current_anim
        if frame_idx < 0 or frame_idx >= len(anim.frames):
            return
        if len(anim.frames) <= 1:
            QMessageBox.information(
                self, "Can't Delete",
                "Can't remove the last frame. An animation needs at least one.")
            return

        frame = anim.frames[frame_idx]
        reply = QMessageBox.question(
            self, "Delete Frame",
            f"Remove frame {frame_idx} from tileset_anims.c?\n\n"
            f"File: {os.path.basename(frame.png_path)}\n\n"
            f"The PNG file will NOT be deleted -- only the C source\n"
            f"references are removed.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return

        ok = remove_frame_from_anim(self._project_dir, anim, frame_idx)
        if ok:
            self._dirty = True
            self.modified.emit()
            old_ts = self._tileset_combo.currentIndex()
            old_anim = self._anim_combo.currentIndex()
            self._load_data()
            if old_ts < self._tileset_combo.count():
                self._tileset_combo.setCurrentIndex(old_ts)
            if old_anim < self._anim_combo.count():
                self._anim_combo.setCurrentIndex(old_anim)
                self._on_anim_selected(old_anim)
            QMessageBox.information(
                self, "Frame Removed",
                f"Frame {frame_idx} removed from tileset_anims.c.\n"
                f"The PNG file was left in place.\n"
                f"Rebuild the ROM to see the change in-game.")
        else:
            QMessageBox.warning(
                self, "Delete Failed",
                "Could not remove the frame from tileset_anims.c.\n"
                "The C source may have been modified in an unexpected way.")

    # ------------------------------------------------------------------
    #  Open in Explorer
    # ------------------------------------------------------------------

    def _open_in_explorer(self):
        if not self._current_anim:
            return
        anim_dir = self._current_anim.anim_dir
        if anim_dir and os.path.isdir(anim_dir):
            open_folder(anim_dir)
        else:
            QMessageBox.information(
                self, "No Directory",
                "No frame directory found for this animation.")

    # ------------------------------------------------------------------
    #  Display Refresh
    # ------------------------------------------------------------------

    def _refresh_display(self):
        """Refresh filmstrip and preview with current pixmaps."""
        if not self._current_anim:
            return

        anim = self._current_anim
        speed_pct = self._speed_slider.value() / 100.0
        base_ms = anim.frame_duration_ms
        effective_ms = max(16, int(base_ms / speed_pct)) if speed_pct > 0 else int(base_ms)

        was_playing = self._preview.is_playing()

        self._preview.set_frames(
            self._frame_pixmaps, anim.frame_order,
            self._frame_w, self._frame_h, effective_ms)

        self._filmstrip.set_frames(
            self._frame_pixmaps, anim.frame_order,
            self._frame_w, self._frame_h)

        if was_playing:
            self._preview.play()

        # Update tile grid
        seq_idx = self._preview.current_frame()
        self._update_tile_grid_for_frame(seq_idx)
