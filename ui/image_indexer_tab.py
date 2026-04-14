"""
ui/image_indexer_tab.py
GBA Image Indexer — quantize PNGs to 16 or 256 GBA-compatible colors,
reorder palettes, set transparent/background color, export indexed PNGs
and JASC .pal files.  Image-to-tilemap conversion (8×8 dedup with flips).

Sub-tab of the Tilemap Editor page.

Uses PaletteSwatch (same widget as species/trainer graphics) for colour
editing.  Drag-and-drop reordering of palette entries with full pixel
index remapping.  No external dependencies beyond PyQt6 + numpy.
"""
from __future__ import annotations

import os
import struct

import numpy as np

from PyQt6.QtCore import Qt, pyqtSignal, QMimeData, QPoint
from PyQt6.QtGui import (
    QColor, QDrag, QImage, QMouseEvent, QPainter, QPixmap,
)
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QFileDialog,
    QGroupBox, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QRadioButton, QSplitter, QVBoxLayout, QWidget,
)

from core.gba_image_utils import (
    quantize_image, remap_to_palette,
    move_color_to_index, swap_palette_entries,
    export_indexed_png, export_palette, get_image_info,
    gba_clamp_palette, _qimage_to_rgb_array,
)
from ui.palette_utils import clamp_to_gba, read_jasc_pal


# ── Stylesheets ──────────────────────────────────────────────────────────────

_NOTE_SS = "color: #888888; font-size: 10px; font-style: italic;"

_GROUP_SS = """
QGroupBox {
    font-weight: bold; font-size: 10px;
    border: 1px solid #383838; border-radius: 6px;
    margin-top: 10px; padding-top: 6px;
    background-color: #252525; color: #cccccc;
}
QGroupBox::title {
    subcontrol-origin: margin; subcontrol-position: top left;
    left: 10px; padding: 0 5px; color: #777; font-size: 9px;
}
"""

# ── Draggable palette swatch ────────────────────────────────────────────────

_SWATCH_SZ = 22

class _DragSwatch(QLabel):
    """Palette swatch: click to edit colour, drag to reorder."""

    # Signals emitted to the parent row
    color_changed = pyqtSignal(int, tuple)   # (index, (r,g,b))
    drag_started = pyqtSignal(int)           # source index
    drop_received = pyqtSignal(int, int)     # (from_index, to_index)

    def __init__(self, index: int, parent=None):
        super().__init__(parent)
        self._index = index
        self._color: tuple[int, int, int] = (0, 0, 0)
        self.setFixedSize(_SWATCH_SZ, _SWATCH_SZ)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAutoFillBackground(True)
        self.setAcceptDrops(True)
        self._drag_start: QPoint | None = None
        self._refresh_tooltip()
        self._refresh()

    # -- public api --

    @property
    def index(self) -> int:
        return self._index

    @index.setter
    def index(self, v: int):
        self._index = v
        self._refresh_tooltip()

    def color(self) -> tuple[int, int, int]:
        return self._color

    def set_color(self, c: tuple[int, int, int], emit: bool = False):
        c = clamp_to_gba(*c)
        if c != self._color:
            self._color = c
            self._refresh()
            self._refresh_tooltip()
            if emit:
                self.color_changed.emit(self._index, c)

    # -- painting --

    def _refresh(self):
        r, g, b = self._color
        p = self.palette()
        p.setColor(self.backgroundRole(), QColor(r, g, b))
        self.setPalette(p)

    def _refresh_tooltip(self):
        r, g, b = self._color
        self.setToolTip(
            f"Index {self._index}: ({r}, {g}, {b})\n"
            f"Click to edit  •  Drag to reorder"
        )

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        # Thin border
        p.setPen(QColor("#555"))
        p.drawRect(0, 0, _SWATCH_SZ - 1, _SWATCH_SZ - 1)
        # Index 0 label
        if self._index == 0:
            p.setPen(QColor("#ff6666"))
            from PyQt6.QtGui import QFont
            p.setFont(QFont("Arial", 7, QFont.Weight.Bold))
            p.drawText(2, 13, "BG")
        p.end()

    # -- click to edit --

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.pos()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and self._drag_start is not None:
            # If release without significant drag → open colour picker
            delta = event.pos() - self._drag_start
            if delta.manhattanLength() < 5:
                self._open_picker()
        self._drag_start = None
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._drag_start is None:
            return
        if (event.pos() - self._drag_start).manhattanLength() < 5:
            return
        # Start drag
        drag = QDrag(self)
        mime = QMimeData()
        mime.setText(str(self._index))
        drag.setMimeData(mime)
        # Drag pixmap — colour swatch thumbnail
        pm = QPixmap(_SWATCH_SZ, _SWATCH_SZ)
        pm.fill(QColor(*self._color))
        drag.setPixmap(pm)
        drag.setHotSpot(QPoint(_SWATCH_SZ // 2, _SWATCH_SZ // 2))
        self._drag_start = None
        drag.exec(Qt.DropAction.MoveAction)

    def _open_picker(self):
        r, g, b = self._color
        top = self.window()
        dlg = QColorDialog(QColor(r, g, b), top)
        dlg.setWindowTitle(f"Palette index {self._index}")
        dlg.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog)
        for lbl in dlg.findChildren(QLabel):
            if lbl.text().rstrip(":").strip().upper() in ("HTML", "&HTML"):
                lbl.setText("Hex:")
        if dlg.exec() == QColorDialog.DialogCode.Accepted:
            qc = dlg.currentColor()
            if qc.isValid():
                new = clamp_to_gba(qc.red(), qc.green(), qc.blue())
                if new != self._color:
                    self._color = new
                    self._refresh()
                    self._refresh_tooltip()
                    self.color_changed.emit(self._index, new)

    # -- drop target --

    def dragEnterEvent(self, event):
        if event.mimeData().hasText():
            event.acceptProposedAction()

    def dropEvent(self, event):
        try:
            src = int(event.mimeData().text())
        except (ValueError, TypeError):
            return
        if src != self._index:
            self.drop_received.emit(src, self._index)
        event.acceptProposedAction()


# ── Draggable palette row ───────────────────────────────────────────────────

class _DraggablePaletteRow(QWidget):
    """Row of _DragSwatch widgets — supports drag-reorder.

    Emits palette_reordered(from_idx, to_idx) when a swatch is dropped
    onto a different position.  Emits color_edited() when a colour is
    changed via the picker.
    """
    palette_reordered = pyqtSignal(int, int)  # (from, to)
    color_edited = pyqtSignal()

    def __init__(self, n: int = 16, parent=None):
        super().__init__(parent)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(2)
        self._swatches: list[_DragSwatch] = []
        for i in range(n):
            self._add_swatch(i)
        self._layout.addStretch(1)

    def _add_swatch(self, idx: int) -> _DragSwatch:
        s = _DragSwatch(idx)
        s.color_changed.connect(self._on_color_changed)
        s.drop_received.connect(self._on_drop)
        self._swatches.append(s)
        # Insert before the stretch
        pos = self._layout.count() - 1 if self._layout.count() > 0 else 0
        self._layout.insertWidget(pos, s)
        return s

    def set_colors(self, colors: list[tuple[int, int, int]]):
        for i, s in enumerate(self._swatches):
            c = colors[i] if i < len(colors) else (0, 0, 0)
            s.set_color(c, emit=False)

    def colors(self) -> list[tuple[int, int, int]]:
        return [s.color() for s in self._swatches]

    def count(self) -> int:
        return len(self._swatches)

    def _on_color_changed(self, idx: int, color: tuple):
        self.color_edited.emit()

    def _on_drop(self, src: int, dst: int):
        self.palette_reordered.emit(src, dst)


# ── Image preview widget ────────────────────────────────────────────────────

class _ImagePreview(QLabel):
    """Image preview with optional transparency checkerboard."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(200, 200)
        self.setStyleSheet("background-color: #1a1a1a; border: 1px solid #333;")
        self._pixmap: QPixmap | None = None
        self._show_transparent: bool = False

    @property
    def show_transparent(self) -> bool:
        return self._show_transparent

    @show_transparent.setter
    def show_transparent(self, v: bool):
        self._show_transparent = v
        self._update_display()

    def set_image(self, img: QImage | QPixmap | None):
        if img is None:
            self.clear()
            self._pixmap = None
            return
        if isinstance(img, QImage):
            if self._show_transparent:
                # Render with transparency (index 0 = alpha 0)
                argb = img.convertToFormat(QImage.Format.Format_ARGB32)
                self._pixmap = QPixmap.fromImage(argb)
            else:
                # Force opaque
                argb = img.convertToFormat(QImage.Format.Format_ARGB32)
                self._pixmap = QPixmap.fromImage(argb)
        else:
            self._pixmap = img
        self._update_display()

    def _update_display(self):
        if self._pixmap is None:
            return
        w = self.width() - 4
        h = self.height() - 4
        if w < 1 or h < 1:
            return
        scaled = self._pixmap.scaled(
            w, h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        self.setPixmap(scaled)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_display()


# ── Main Image Indexer Widget ────────────────────────────────────────────────

class ImageIndexerWidget(QWidget):
    """
    GBA Image Indexer — load any PNG, quantize to 16 or 256 GBA colors,
    drag-reorder palette (drop to index 0 = background/transparent),
    trim unused 256-colour entries, export indexed PNG / .pal, and
    convert images to 8×8-tile-deduped tilemaps.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._source_path: str = ""
        self._source_img: QImage | None = None
        self._indexed_img: QImage | None = None
        self._palette: list[tuple[int, int, int]] = []
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        # ── Description
        desc = QLabel(
            "Load any PNG image and convert it to GBA-compatible indexed format. "
            "Quantize to 16 colors (4bpp) or 256 colors (8bpp), drag palette "
            "entries to reorder (drop onto index 0 to set the background/transparent "
            "color), and export the result."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(_NOTE_SS)
        root.addWidget(desc)

        # ── Top controls bar
        top_bar = QHBoxLayout()
        top_bar.setSpacing(6)

        self._load_btn = QPushButton("Load PNG...")
        self._load_btn.setToolTip("Open any PNG image to index")
        self._load_btn.clicked.connect(self._load_image)
        top_bar.addWidget(self._load_btn)

        self._info_label = QLabel("No image loaded")
        self._info_label.setStyleSheet("color: #aaa; font-size: 11px;")
        top_bar.addWidget(self._info_label, 1)

        top_bar.addWidget(QLabel("Target:"))
        self._color_16_rb = QRadioButton("16 colors")
        self._color_16_rb.setChecked(True)
        self._color_16_rb.setToolTip("4bpp — standard for sprites and tiles")
        top_bar.addWidget(self._color_16_rb)

        self._color_256_rb = QRadioButton("256 colors")
        self._color_256_rb.setToolTip("8bpp — for backgrounds with many colors")
        top_bar.addWidget(self._color_256_rb)

        self._dither_cb = QCheckBox("Dither")
        self._dither_cb.setChecked(True)
        self._dither_cb.setToolTip(
            "Floyd-Steinberg dithering — smoother gradients. "
            "Turn off for pixel-art style."
        )
        top_bar.addWidget(self._dither_cb)

        self._quantize_btn = QPushButton("Quantize")
        self._quantize_btn.setToolTip(
            "Reduce the image to the target number of GBA-safe colors"
        )
        self._quantize_btn.setEnabled(False)
        self._quantize_btn.clicked.connect(self._do_quantize)
        top_bar.addWidget(self._quantize_btn)

        root.addLayout(top_bar)

        # ── Second controls bar (remap + trim + tilemap)
        bar2 = QHBoxLayout()
        bar2.setSpacing(6)

        self._load_pal_btn = QPushButton("Load .pal...")
        self._load_pal_btn.setToolTip(
            "Load an existing JASC .pal and remap to those exact colors"
        )
        self._load_pal_btn.setEnabled(False)
        self._load_pal_btn.clicked.connect(self._load_and_remap_palette)
        bar2.addWidget(self._load_pal_btn)

        self._remap_dither_cb = QCheckBox("Dither on remap")
        self._remap_dither_cb.setToolTip(
            "Apply dithering when remapping to an existing palette"
        )
        bar2.addWidget(self._remap_dither_cb)

        self._trim_btn = QPushButton("Trim Unused Colors")
        self._trim_btn.setToolTip(
            "Remove duplicate and unused palette entries, compact the palette "
            "down to only the colours the image actually uses"
        )
        self._trim_btn.setEnabled(False)
        self._trim_btn.clicked.connect(self._trim_palette)
        bar2.addWidget(self._trim_btn)

        self._show_trans_cb = QCheckBox("Show Transparent")
        self._show_trans_cb.setToolTip(
            "Toggle index 0 between transparent and showing its actual colour"
        )
        self._show_trans_cb.toggled.connect(self._toggle_transparency)
        bar2.addWidget(self._show_trans_cb)

        bar2.addStretch(1)

        self._tilemap_btn = QPushButton("Convert to Tilemap...")
        self._tilemap_btn.setToolTip(
            "Split the indexed image into 8×8 tiles, remove duplicates "
            "(including H/V flipped copies), and export a .bin tilemap + "
            "tile sheet PNG"
        )
        self._tilemap_btn.setEnabled(False)
        self._tilemap_btn.clicked.connect(self._convert_to_tilemap)
        bar2.addWidget(self._tilemap_btn)

        root.addLayout(bar2)

        # ── Splitter: previews left, palette + export right
        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter, 1)

        # Left: side-by-side previews
        preview_widget = QWidget()
        preview_layout = QVBoxLayout(preview_widget)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(4)

        preview_row = QHBoxLayout()
        preview_row.setSpacing(4)

        orig_group = QGroupBox("Original")
        orig_group.setStyleSheet(_GROUP_SS)
        orig_inner = QVBoxLayout(orig_group)
        self._orig_preview = _ImagePreview()
        orig_inner.addWidget(self._orig_preview)
        preview_row.addWidget(orig_group)

        result_group = QGroupBox("Indexed Result")
        result_group.setStyleSheet(_GROUP_SS)
        result_inner = QVBoxLayout(result_group)
        self._result_preview = _ImagePreview()
        result_inner.addWidget(self._result_preview)
        preview_row.addWidget(result_group)

        preview_layout.addLayout(preview_row, 1)
        splitter.addWidget(preview_widget)

        # Right: palette + export
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(4, 0, 0, 0)
        right_layout.setSpacing(6)

        # Palette
        pal_group = QGroupBox("Palette")
        pal_group.setStyleSheet(_GROUP_SS)
        pal_inner = QVBoxLayout(pal_group)
        pal_inner.setSpacing(4)

        pal_desc = QLabel(
            "Click a swatch to edit its colour.  Drag a swatch and drop it "
            "onto another position to reorder.  Drop onto index 0 (BG) to "
            "set the transparent/background colour."
        )
        pal_desc.setWordWrap(True)
        pal_desc.setStyleSheet(_NOTE_SS)
        pal_inner.addWidget(pal_desc)

        # Palette rows container (1 row = 16 swatches, up to 16 rows for 256)
        self._pal_rows: list[_DraggablePaletteRow] = []
        self._pal_container = QVBoxLayout()
        self._pal_container.setSpacing(2)
        self._add_pal_row()
        pal_inner.addLayout(self._pal_container)

        self._pal_status = QLabel("")
        self._pal_status.setStyleSheet("color: #888; font-size: 10px;")
        pal_inner.addWidget(self._pal_status)

        right_layout.addWidget(pal_group)

        # Export
        export_group = QGroupBox("Export")
        export_group.setStyleSheet(_GROUP_SS)
        export_inner = QVBoxLayout(export_group)
        export_inner.setSpacing(4)

        self._export_png_btn = QPushButton("Save Indexed PNG...")
        self._export_png_btn.setEnabled(False)
        self._export_png_btn.clicked.connect(self._export_png)
        export_inner.addWidget(self._export_png_btn)

        self._export_pal_btn = QPushButton("Save Palette as .pal...")
        self._export_pal_btn.setEnabled(False)
        self._export_pal_btn.clicked.connect(self._export_pal)
        export_inner.addWidget(self._export_pal_btn)

        self._export_both_btn = QPushButton("Save Both...")
        self._export_both_btn.setEnabled(False)
        self._export_both_btn.clicked.connect(self._export_both)
        export_inner.addWidget(self._export_both_btn)

        right_layout.addWidget(export_group)
        right_layout.addStretch(1)

        splitter.addWidget(right_widget)
        splitter.setSizes([600, 300])

    # ── Palette row helpers ──────────────────────────────────────────────

    def _add_pal_row(self) -> _DraggablePaletteRow:
        row = _DraggablePaletteRow(16)
        row.color_edited.connect(self._on_palette_color_edited)
        row.palette_reordered.connect(self._on_palette_reordered)
        self._pal_rows.append(row)
        self._pal_container.addWidget(row)
        return row

    def _set_palette_display(self, palette: list[tuple[int, int, int]]):
        n_rows = max(1, (len(palette) + 15) // 16)
        while len(self._pal_rows) < n_rows:
            self._add_pal_row()
        while len(self._pal_rows) > n_rows:
            row = self._pal_rows.pop()
            row.setParent(None)
            row.deleteLater()
        for ri, row in enumerate(self._pal_rows):
            start = ri * 16
            chunk = palette[start:start + 16]
            while len(chunk) < 16:
                chunk.append((0, 0, 0))
            row.set_colors(chunk)
            # Update swatch indices to be global
            for si, s in enumerate(row._swatches):
                s.index = start + si

    def _read_palette_from_rows(self) -> list[tuple[int, int, int]]:
        result = []
        for row in self._pal_rows:
            result.extend(row.colors())
        return result

    def _on_palette_color_edited(self):
        """User clicked a swatch and picked a new colour."""
        if self._indexed_img is None:
            return
        max_c = 16 if self._color_16_rb.isChecked() else 256
        self._palette = self._read_palette_from_rows()[:max_c]
        self._rebuild_image_palette()
        self._refresh_result_preview()
        self._pal_status.setText("Palette colour edited")

    def _on_palette_reordered(self, from_idx: int, to_idx: int):
        """User dragged swatch from_idx and dropped it on to_idx."""
        if self._indexed_img is None or not self._palette:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            new_img, new_pal = move_color_to_index(
                self._indexed_img, self._palette, from_idx, to_idx,
            )
            self._indexed_img = new_img
            self._palette = new_pal
            self._set_palette_display(new_pal)
            self._refresh_result_preview()
            if to_idx == 0:
                self._pal_status.setText(
                    f"Moved index {from_idx} to BG (index 0 — transparent)"
                )
            else:
                self._pal_status.setText(
                    f"Moved index {from_idx} → index {to_idx}"
                )
        except Exception as e:
            QMessageBox.warning(self, "Reorder Error", str(e))
        finally:
            QApplication.restoreOverrideCursor()

    def _rebuild_image_palette(self):
        if self._indexed_img is None:
            return
        if self._indexed_img.format() != QImage.Format.Format_Indexed8:
            return
        ct = []
        for i, (r, g, b) in enumerate(self._palette):
            alpha = 0 if i == 0 else 255
            ct.append((alpha << 24) | (r << 16) | (g << 8) | b)
        while len(ct) < 256:
            ct.append(0xFF000000)
        self._indexed_img.setColorTable(ct)

    def _refresh_result_preview(self):
        if self._indexed_img is None:
            return
        self._result_preview.show_transparent = self._show_trans_cb.isChecked()
        self._result_preview.set_image(self._indexed_img)

    # ── Transparency toggle ──────────────────────────────────────────────

    def _toggle_transparency(self, checked: bool):
        self._refresh_result_preview()

    # ── Load image ───────────────────────────────────────────────────────

    def _load_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load PNG Image", "",
            "PNG Images (*.png);;All Files (*)",
        )
        if not path:
            return
        img = QImage(path)
        if img.isNull():
            QMessageBox.warning(self, "Load Error", f"Could not open:\n{path}")
            return

        self._source_path = path
        self._source_img = img
        self._indexed_img = None
        self._palette = []

        info = get_image_info(path)
        w, h = info.get("width", 0), info.get("height", 0)
        cc = info.get("color_count", 0)
        cc_str = f"{cc} colors" if cc >= 0 else "many colors"
        mode = info.get("mode", "?")

        self._info_label.setText(
            f"{os.path.basename(path)}  |  {w}x{h}  |  {mode}  |  {cc_str}"
        )
        self._orig_preview.set_image(img)
        self._result_preview.set_image(None)

        if info.get("is_indexed", False):
            self._auto_load_indexed(img, cc)

        self._quantize_btn.setEnabled(True)
        self._load_pal_btn.setEnabled(True)

    def _auto_load_indexed(self, img: QImage, cc: int):
        ct = img.colorTable()
        if not ct:
            return
        target = 16 if cc <= 16 else 256
        colors = []
        for c in ct[:target]:
            r = (c >> 16) & 0xFF
            g = (c >> 8) & 0xFF
            b = c & 0xFF
            colors.append(clamp_to_gba(r, g, b))
        while len(colors) < target:
            colors.append((0, 0, 0))
        self._palette = colors
        self._indexed_img = img.copy()
        self._rebuild_image_palette()
        self._set_palette_display(colors)
        self._refresh_result_preview()
        self._pal_status.setText(f"Loaded existing {cc}-color palette")
        self._enable_export(True)
        if target == 16:
            self._color_16_rb.setChecked(True)
        else:
            self._color_256_rb.setChecked(True)

    # ── Quantize ─────────────────────────────────────────────────────────

    def _do_quantize(self):
        if self._source_img is None:
            return
        max_colors = 16 if self._color_16_rb.isChecked() else 256
        dither = self._dither_cb.isChecked()

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            indexed, palette = quantize_image(
                self._source_img, max_colors, dither, gba_clamp=True,
            )
            self._indexed_img = indexed
            self._palette = palette
            self._set_palette_display(palette)
            self._refresh_result_preview()
            self._pal_status.setText(
                f"Quantized to {len(palette)} GBA-safe colors"
                f" {'with' if dither else 'without'} dithering"
            )
            self._enable_export(True)
        except Exception as e:
            QMessageBox.warning(self, "Quantize Error", str(e))
        finally:
            QApplication.restoreOverrideCursor()

    # ── Load .pal and remap ──────────────────────────────────────────────

    def _load_and_remap_palette(self):
        if self._source_img is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Load JASC .pal File", "",
            "Palette Files (*.pal);;All Files (*)",
        )
        if not path:
            return
        max_colors = 16 if self._color_16_rb.isChecked() else 256
        colors = read_jasc_pal(path, max_colors)
        if not colors:
            QMessageBox.warning(self, "Load Error", "Could not read .pal file")
            return
        colors = gba_clamp_palette(colors[:max_colors])
        while len(colors) < max_colors:
            colors.append((0, 0, 0))
        dither = self._remap_dither_cb.isChecked()

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            remapped = remap_to_palette(self._source_img, colors, dither)
            self._indexed_img = remapped
            self._palette = colors
            self._set_palette_display(colors)
            self._refresh_result_preview()
            self._pal_status.setText(
                f"Remapped to {os.path.basename(path)} "
                f"({len(colors)} colors, closest-color)"
            )
            self._enable_export(True)
        except Exception as e:
            QMessageBox.warning(self, "Remap Error", str(e))
        finally:
            QApplication.restoreOverrideCursor()

    # ── Trim unused / duplicate colours ──────────────────────────────────

    def _trim_palette(self):
        if self._indexed_img is None or not self._palette:
            return
        if self._indexed_img.format() != QImage.Format.Format_Indexed8:
            return

        from core.gba_image_utils import _qimage_index_array

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            arr = _qimage_index_array(self._indexed_img)
            used_indices = set(np.unique(arr).tolist())
            # Always keep index 0 (BG)
            used_indices.add(0)

            old_pal = list(self._palette)
            n_old = len(old_pal)

            # Build new palette: only used entries, preserving index 0
            new_pal: list[tuple[int, int, int]] = []
            old_to_new = {}
            for i in range(n_old):
                if i in used_indices:
                    old_to_new[i] = len(new_pal)
                    new_pal.append(old_pal[i])

            if len(new_pal) == n_old:
                self._pal_status.setText("No unused colours to trim")
                return

            # Deduplicate colours (merge duplicates to first occurrence)
            seen: dict[tuple[int, int, int], int] = {}
            dedup_pal: list[tuple[int, int, int]] = []
            old_new_to_dedup = {}
            for i, c in enumerate(new_pal):
                if c in seen:
                    old_new_to_dedup[i] = seen[c]
                else:
                    seen[c] = len(dedup_pal)
                    old_new_to_dedup[i] = len(dedup_pal)
                    dedup_pal.append(c)

            # Build full remap table: old_index -> final_index
            final_map = {}
            for old_i, new_i in old_to_new.items():
                final_map[old_i] = old_new_to_dedup[new_i]
            # Unmapped indices go to 0
            lut = np.zeros(max(n_old, 256), dtype=np.uint8)
            for old_i, new_i in final_map.items():
                lut[old_i] = new_i

            new_arr = lut[arr]

            from core.gba_image_utils import _indexed_array_to_qimage
            self._palette = dedup_pal
            self._indexed_img = _indexed_array_to_qimage(new_arr, dedup_pal)
            self._set_palette_display(dedup_pal)
            self._refresh_result_preview()
            removed = n_old - len(dedup_pal)
            self._pal_status.setText(
                f"Trimmed {removed} unused/duplicate entries → "
                f"{len(dedup_pal)} colours"
            )
        except Exception as e:
            QMessageBox.warning(self, "Trim Error", str(e))
        finally:
            QApplication.restoreOverrideCursor()

    # ── Image to Tilemap ─────────────────────────────────────────────────

    def _convert_to_tilemap(self):
        """Split indexed image into 8×8 tiles, deduplicate (with H/V flips),
        and export a .bin tilemap + tile sheet PNG."""
        if self._indexed_img is None or not self._palette:
            return
        if self._indexed_img.format() != QImage.Format.Format_Indexed8:
            QMessageBox.warning(
                self, "Not Indexed",
                "Quantize the image first so it has an indexed palette.",
            )
            return

        from core.gba_image_utils import _qimage_index_array

        arr = _qimage_index_array(self._indexed_img)
        h, w = arr.shape
        if w % 8 != 0 or h % 8 != 0:
            QMessageBox.warning(
                self, "Size Error",
                f"Image dimensions ({w}×{h}) must be multiples of 8 "
                f"for tilemap conversion.",
            )
            return

        cols = w // 8
        rows = h // 8

        # Extract all 8×8 tiles
        tiles: list[np.ndarray] = []
        for ty in range(rows):
            for tx in range(cols):
                tile = arr[ty * 8:(ty + 1) * 8, tx * 8:(tx + 1) * 8].copy()
                tiles.append(tile)

        # Build unique tile set, checking normal + H + V + HV flips
        unique_tiles: list[np.ndarray] = []
        tile_hash: dict[bytes, tuple[int, bool, bool]] = {}  # hash -> (idx, hflip, vflip)
        tilemap_entries: list[tuple[int, bool, bool]] = []  # (tile_idx, hflip, vflip)

        for tile in tiles:
            found = False
            # Check all four flip variants
            for hf in (False, True):
                for vf in (False, True):
                    variant = tile.copy()
                    if hf:
                        variant = np.fliplr(variant)
                    if vf:
                        variant = np.flipud(variant)
                    key = variant.tobytes()
                    if key in tile_hash:
                        ref_idx, ref_hf, ref_vf = tile_hash[key]
                        # Compose flips: if the stored tile was found via
                        # (ref_hf, ref_vf), and our current variant used
                        # (hf, vf), then the entry needs (hf ^ ref_hf, vf ^ ref_vf)
                        tilemap_entries.append((
                            ref_idx,
                            bool(hf ^ ref_hf),
                            bool(vf ^ ref_vf),
                        ))
                        found = True
                        break
                if found:
                    break

            if not found:
                idx = len(unique_tiles)
                unique_tiles.append(tile)
                key = tile.tobytes()
                tile_hash[key] = (idx, False, False)
                tilemap_entries.append((idx, False, False))

        # Ask user where to save
        default_dir = os.path.dirname(self._source_path) if self._source_path else ""
        dir_path = QFileDialog.getExistingDirectory(
            self, "Save Tilemap + Tile Sheet to Folder", default_dir,
        )
        if not dir_path:
            return

        base = "tilemap"
        if self._source_path:
            base = os.path.splitext(os.path.basename(self._source_path))[0]

        bin_path = os.path.join(dir_path, f"{base}.bin")
        sheet_path = os.path.join(dir_path, f"{base}_tiles.png")
        pal_path = os.path.join(dir_path, f"{base}.pal")

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            # Write .bin tilemap — each entry is a 16-bit GBA tilemap word
            # Bits: [9:0] tile index, [10] hflip, [11] vflip, [15:12] palette
            bin_data = bytearray()
            for (tidx, hf, vf) in tilemap_entries:
                val = tidx & 0x3FF
                if hf:
                    val |= 1 << 10
                if vf:
                    val |= 1 << 11
                # Palette 0 by default
                bin_data.extend(struct.pack("<H", val))
            with open(bin_path, "wb") as f:
                f.write(bin_data)

            # Build tile sheet image — arrange unique tiles in a strip
            # (standard: 8 tiles wide, enough rows to fit all)
            sheet_cols = 8
            sheet_rows = max(1, (len(unique_tiles) + sheet_cols - 1) // sheet_cols)
            sheet_w = sheet_cols * 8
            sheet_h = sheet_rows * 8

            sheet_arr = np.zeros((sheet_h, sheet_w), dtype=np.uint8)
            for i, tile in enumerate(unique_tiles):
                ty = (i // sheet_cols) * 8
                tx = (i % sheet_cols) * 8
                sheet_arr[ty:ty + 8, tx:tx + 8] = tile

            from core.gba_image_utils import _indexed_array_to_qimage
            sheet_img = _indexed_array_to_qimage(sheet_arr, self._palette)
            sheet_img.save(sheet_path, "PNG")

            # Also save the palette
            export_palette(self._palette, pal_path)

            self._pal_status.setText(
                f"Tilemap: {len(unique_tiles)} unique tiles from "
                f"{len(tiles)} total ({cols}×{rows}). "
                f"Saved .bin + tiles PNG + .pal"
            )
        except Exception as e:
            QMessageBox.warning(self, "Tilemap Error", str(e))
        finally:
            QApplication.restoreOverrideCursor()

    # ── Export ───────────────────────────────────────────────────────────

    def _enable_export(self, enabled: bool):
        self._export_png_btn.setEnabled(enabled)
        self._export_pal_btn.setEnabled(enabled)
        self._export_both_btn.setEnabled(enabled)
        self._trim_btn.setEnabled(enabled)
        self._tilemap_btn.setEnabled(enabled)

    def _export_png(self):
        if self._indexed_img is None:
            return
        default = ""
        if self._source_path:
            base = os.path.splitext(self._source_path)[0]
            default = f"{base}_indexed.png"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Indexed PNG", default,
            "PNG Images (*.png);;All Files (*)",
        )
        if not path:
            return
        if export_indexed_png(self._indexed_img, self._palette, path):
            self._pal_status.setText(f"Saved: {os.path.basename(path)}")
        else:
            QMessageBox.warning(self, "Export Error", "Failed to save PNG")

    def _export_pal(self):
        if not self._palette:
            return
        default = ""
        if self._source_path:
            base = os.path.splitext(self._source_path)[0]
            default = f"{base}.pal"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save JASC .pal", default,
            "Palette Files (*.pal);;All Files (*)",
        )
        if not path:
            return
        if export_palette(self._palette, path):
            self._pal_status.setText(f"Saved: {os.path.basename(path)}")
        else:
            QMessageBox.warning(self, "Export Error", "Failed to save .pal")

    def _export_both(self):
        if self._indexed_img is None or not self._palette:
            return
        default_dir = os.path.dirname(self._source_path) if self._source_path else ""
        dir_path = QFileDialog.getExistingDirectory(
            self, "Save to Folder", default_dir,
        )
        if not dir_path:
            return
        base = "image"
        if self._source_path:
            base = os.path.splitext(os.path.basename(self._source_path))[0]
        png_path = os.path.join(dir_path, f"{base}_indexed.png")
        pal_path = os.path.join(dir_path, f"{base}.pal")
        ok_png = export_indexed_png(self._indexed_img, self._palette, png_path)
        ok_pal = export_palette(self._palette, pal_path)
        if ok_png and ok_pal:
            self._pal_status.setText(f"Saved {base}_indexed.png + {base}.pal")
        else:
            parts = []
            if not ok_png:
                parts.append("PNG failed")
            if not ok_pal:
                parts.append(".pal failed")
            QMessageBox.warning(self, "Export Error", "\n".join(parts))
