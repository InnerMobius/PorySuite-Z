"""
ui/image_indexer_tab.py
GBA Image Indexer — quantize PNGs to 16 or 256 GBA-compatible colors,
reorder palettes, set transparent/background color, export indexed PNGs
and JASC .pal files.

Sub-tab of the Tilemap Editor page.
"""
from __future__ import annotations

import os
from typing import Optional

from PIL import Image

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QImage, QPixmap, QPainter
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog,
    QFrame, QGroupBox, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QRadioButton, QScrollArea, QSizePolicy,
    QSlider, QSpinBox, QSplitter, QVBoxLayout, QWidget,
)

from core.gba_image_utils import (
    quantize_image, remap_to_palette, reorder_palette,
    move_color_to_index, swap_palette_entries,
    export_indexed_png, export_palette, get_image_info,
    gba_clamp_palette, find_closest_color,
)
from ui.palette_utils import clamp_to_gba, read_jasc_pal, write_jasc_pal


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


# ── Palette swatch widget (clickable, reorderable) ──────────────────────────

class _PaletteSwatch(QWidget):
    """Single clickable color swatch for the indexer palette."""
    clicked = pyqtSignal(int)  # emits index

    def __init__(self, index: int, parent=None):
        super().__init__(parent)
        self.index = index
        self._color = QColor(0, 0, 0)
        self.setFixedSize(24, 24)
        self.setToolTip(f"Index {index}")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_color(self, r: int, g: int, b: int):
        self._color = QColor(r, g, b)
        self.setToolTip(
            f"Index {self.index}: ({r}, {g}, {b})"
        )
        self.update()

    def color_tuple(self) -> tuple[int, int, int]:
        return (self._color.red(), self._color.green(), self._color.blue())

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(0, 0, 24, 24, self._color)
        # Border
        p.setPen(QColor("#555555"))
        p.drawRect(0, 0, 23, 23)
        # Index number
        if self.index == 0:
            p.setPen(QColor("#ff6666"))
            p.setFont(QFont("Arial", 7, QFont.Weight.Bold))
            p.drawText(2, 12, "BG")
        p.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.index)


class _PaletteGrid(QWidget):
    """Grid of palette swatches with selection."""
    color_selected = pyqtSignal(int)  # emits selected index

    def __init__(self, max_colors: int = 16, parent=None):
        super().__init__(parent)
        self._max_colors = max_colors
        self._selected = -1
        self._swatches: list[_PaletteSwatch] = []
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(1)
        self._build(max_colors)

    def _build(self, n: int):
        # Clear existing
        for s in self._swatches:
            s.setParent(None)
            s.deleteLater()
        self._swatches.clear()

        # Wrap in a flow-like layout (rows of 16)
        if hasattr(self, '_inner'):
            self._inner.setParent(None)
            self._inner.deleteLater()

        self._inner = QWidget()
        inner_layout = QVBoxLayout(self._inner)
        inner_layout.setContentsMargins(0, 0, 0, 0)
        inner_layout.setSpacing(1)

        row_layout = None
        for i in range(n):
            if i % 16 == 0:
                row_layout = QHBoxLayout()
                row_layout.setContentsMargins(0, 0, 0, 0)
                row_layout.setSpacing(1)
                inner_layout.addLayout(row_layout)
            s = _PaletteSwatch(i)
            s.clicked.connect(self._on_click)
            self._swatches.append(s)
            if row_layout:
                row_layout.addWidget(s)

        if row_layout:
            row_layout.addStretch(1)
        inner_layout.addStretch(1)

        # Replace layout content
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
        self._layout.addWidget(self._inner)

    def set_palette(self, colors: list[tuple[int, int, int]]):
        n = len(colors)
        if n != len(self._swatches):
            self._build(n)
        for i, (r, g, b) in enumerate(colors):
            if i < len(self._swatches):
                self._swatches[i].set_color(r, g, b)

    def get_palette(self) -> list[tuple[int, int, int]]:
        return [s.color_tuple() for s in self._swatches]

    def selected_index(self) -> int:
        return self._selected

    def _on_click(self, idx: int):
        self._selected = idx
        self.color_selected.emit(idx)
        # Visual highlight
        for s in self._swatches:
            s.setStyleSheet(
                "border: 2px solid #1565c0;" if s.index == idx
                else ""
            )


# ── Image preview widget ────────────────────────────────────────────────────

class _ImagePreview(QLabel):
    """Zoomable image preview with checkerboard background for transparency."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(200, 200)
        self.setStyleSheet("background-color: #1a1a1a; border: 1px solid #333;")
        self._pixmap: QPixmap | None = None

    def set_image(self, img: Image.Image | None):
        if img is None:
            self.clear()
            self._pixmap = None
            return

        # Convert PIL → QPixmap
        if img.mode == "P":
            img = img.convert("RGBA")
        elif img.mode != "RGBA":
            img = img.convert("RGBA")

        data = img.tobytes("raw", "RGBA")
        qimg = QImage(data, img.width, img.height, QImage.Format.Format_RGBA8888)
        self._pixmap = QPixmap.fromImage(qimg)
        self._update_display()

    def _update_display(self):
        if self._pixmap is None:
            return
        # Scale to fit the label, maintaining aspect ratio
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
    reorder palette, export indexed PNG and .pal files.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._source_path: str = ""
        self._source_img: Image.Image | None = None
        self._indexed_img: Image.Image | None = None
        self._palette: list[tuple[int, int, int]] = []
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        # ── Description ─────────────────────────────────────────────────
        desc = QLabel(
            "Load any PNG image and convert it to GBA-compatible indexed format. "
            "Quantize to 16 colors (4bpp) or 256 colors (8bpp), rearrange palette "
            "entries, set the background/transparent color, and export the result."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(_NOTE_SS)
        root.addWidget(desc)

        # ── Top controls bar ────────────────────────────────────────────
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
            "Floyd-Steinberg dithering — creates a smoother look "
            "by mixing nearby colors. Turn off for pixel-art style."
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

        # ── Remap to existing palette bar ───────────────────────────────
        remap_bar = QHBoxLayout()
        remap_bar.setSpacing(6)

        self._load_pal_btn = QPushButton("Load .pal...")
        self._load_pal_btn.setToolTip(
            "Load an existing JASC .pal file and remap the image to use "
            "those exact colors (closest-color matching)"
        )
        self._load_pal_btn.setEnabled(False)
        self._load_pal_btn.clicked.connect(self._load_and_remap_palette)
        remap_bar.addWidget(self._load_pal_btn)

        self._remap_dither_cb = QCheckBox("Dither on remap")
        self._remap_dither_cb.setToolTip(
            "Apply Floyd-Steinberg dithering when remapping to an existing palette"
        )
        remap_bar.addWidget(self._remap_dither_cb)

        remap_bar.addStretch(1)
        root.addLayout(remap_bar)

        # ── Splitter: previews left, palette right ──────────────────────
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

        # Right: palette + controls
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(4, 0, 0, 0)
        right_layout.setSpacing(6)

        # Palette display
        pal_group = QGroupBox("Palette")
        pal_group.setStyleSheet(_GROUP_SS)
        pal_inner = QVBoxLayout(pal_group)
        pal_inner.setSpacing(4)

        pal_desc = QLabel(
            "Click a color to select it. Use the buttons below to "
            "rearrange. Index 0 (marked BG) is the transparent/background color."
        )
        pal_desc.setWordWrap(True)
        pal_desc.setStyleSheet(_NOTE_SS)
        pal_inner.addWidget(pal_desc)

        self._pal_grid = _PaletteGrid(16)
        pal_inner.addWidget(self._pal_grid)

        # Palette action buttons
        pal_btns = QHBoxLayout()
        pal_btns.setSpacing(4)

        self._move_bg_btn = QPushButton("Set as BG (index 0)")
        self._move_bg_btn.setToolTip(
            "Move the selected color to palette index 0 (background/transparent)"
        )
        self._move_bg_btn.setEnabled(False)
        self._move_bg_btn.clicked.connect(self._move_to_bg)
        pal_btns.addWidget(self._move_bg_btn)

        self._swap_btn = QPushButton("Swap with...")
        self._swap_btn.setToolTip(
            "Swap the selected color with another palette entry"
        )
        self._swap_btn.setEnabled(False)
        self._swap_btn.clicked.connect(self._start_swap)
        pal_btns.addWidget(self._swap_btn)

        pal_btns.addStretch(1)
        pal_inner.addLayout(pal_btns)

        self._pal_status = QLabel("")
        self._pal_status.setStyleSheet("color: #888; font-size: 10px;")
        pal_inner.addWidget(self._pal_status)

        right_layout.addWidget(pal_group)

        # Export buttons
        export_group = QGroupBox("Export")
        export_group.setStyleSheet(_GROUP_SS)
        export_inner = QVBoxLayout(export_group)
        export_inner.setSpacing(4)

        self._export_png_btn = QPushButton("Save Indexed PNG...")
        self._export_png_btn.setToolTip(
            "Save the indexed image as a PNG with the current palette"
        )
        self._export_png_btn.setEnabled(False)
        self._export_png_btn.clicked.connect(self._export_png)
        export_inner.addWidget(self._export_png_btn)

        self._export_pal_btn = QPushButton("Save Palette as .pal...")
        self._export_pal_btn.setToolTip(
            "Save the current palette as a JASC .pal file "
            "(compatible with Porymap and other GBA tools)"
        )
        self._export_pal_btn.setEnabled(False)
        self._export_pal_btn.clicked.connect(self._export_pal)
        export_inner.addWidget(self._export_pal_btn)

        self._export_both_btn = QPushButton("Save Both...")
        self._export_both_btn.setToolTip(
            "Save indexed PNG and .pal file to the same folder"
        )
        self._export_both_btn.setEnabled(False)
        self._export_both_btn.clicked.connect(self._export_both)
        export_inner.addWidget(self._export_both_btn)

        right_layout.addWidget(export_group)
        right_layout.addStretch(1)

        splitter.addWidget(right_widget)
        splitter.setSizes([600, 300])

        # Palette selection handler
        self._pal_grid.color_selected.connect(self._on_palette_select)
        self._swap_mode = False
        self._swap_first = -1

    # ── Load image ───────────────────────────────────────────────────────

    def _load_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load PNG Image", "",
            "PNG Images (*.png);;All Files (*)",
        )
        if not path:
            return

        try:
            img = Image.open(path)
        except Exception as e:
            QMessageBox.warning(self, "Load Error", f"Could not open image:\n{e}")
            return

        self._source_path = path
        self._source_img = img
        self._indexed_img = None
        self._palette = []

        # Show info
        info = get_image_info(path)
        mode = info.get("mode", "?")
        w, h = info.get("width", 0), info.get("height", 0)
        cc = info.get("color_count", 0)
        cc_str = f"{cc} colors" if cc >= 0 else "many colors"
        is_idx = info.get("is_indexed", False)

        self._info_label.setText(
            f"{os.path.basename(path)}  |  {w}x{h}  |  {mode}  |  "
            f"{'Indexed' if is_idx else 'RGB'}  |  {cc_str}"
        )

        # Show original preview
        self._orig_preview.set_image(img)
        self._result_preview.set_image(None)

        # If already indexed with <=16 or <=256 colors, auto-load its palette
        if is_idx and cc <= 256:
            self._auto_load_indexed(img, cc)

        self._quantize_btn.setEnabled(True)
        self._load_pal_btn.setEnabled(True)

    def _auto_load_indexed(self, img: Image.Image, cc: int):
        """Auto-load palette from an already-indexed image."""
        pal = img.getpalette()
        if not pal:
            return

        target = 16 if cc <= 16 else 256
        colors = []
        for i in range(min(target, cc)):
            r, g, b = pal[i * 3], pal[i * 3 + 1], pal[i * 3 + 2]
            colors.append(clamp_to_gba(r, g, b))
        while len(colors) < target:
            colors.append((0, 0, 0))

        self._palette = colors
        self._indexed_img = img.copy() if img.mode == "P" else img.quantize(target)
        self._pal_grid.set_palette(colors)
        self._result_preview.set_image(self._indexed_img)
        self._pal_status.setText(f"Loaded existing {cc}-color palette from image")
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
            self._pal_grid.set_palette(palette)
            self._result_preview.set_image(indexed)
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
            self._pal_grid.set_palette(colors)
            self._result_preview.set_image(remapped)
            self._pal_status.setText(
                f"Remapped to {os.path.basename(path)} palette "
                f"({len(colors)} colors, closest-color matching)"
            )
            self._enable_export(True)
        except Exception as e:
            QMessageBox.warning(self, "Remap Error", str(e))
        finally:
            QApplication.restoreOverrideCursor()

    # ── Palette reordering ───────────────────────────────────────────────

    def _on_palette_select(self, idx: int):
        if self._swap_mode:
            self._complete_swap(idx)
        else:
            self._move_bg_btn.setEnabled(
                idx > 0 and self._indexed_img is not None
            )
            self._swap_btn.setEnabled(self._indexed_img is not None)

    def _move_to_bg(self):
        idx = self._pal_grid.selected_index()
        if idx <= 0 or self._indexed_img is None:
            return

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            new_img, new_pal = move_color_to_index(
                self._indexed_img, self._palette, idx, 0,
            )
            self._indexed_img = new_img
            self._palette = new_pal
            self._pal_grid.set_palette(new_pal)
            self._result_preview.set_image(new_img)
            self._pal_status.setText(
                f"Moved color from index {idx} to index 0 (background)"
            )
        except Exception as e:
            QMessageBox.warning(self, "Reorder Error", str(e))
        finally:
            QApplication.restoreOverrideCursor()

    def _start_swap(self):
        idx = self._pal_grid.selected_index()
        if idx < 0 or self._indexed_img is None:
            return
        self._swap_mode = True
        self._swap_first = idx
        self._pal_status.setText(
            f"Click another color to swap with index {idx}..."
        )
        self._swap_btn.setText("Cancel swap")
        self._swap_btn.clicked.disconnect()
        self._swap_btn.clicked.connect(self._cancel_swap)

    def _complete_swap(self, idx_b: int):
        self._swap_mode = False
        self._swap_btn.setText("Swap with...")
        self._swap_btn.clicked.disconnect()
        self._swap_btn.clicked.connect(self._start_swap)

        if idx_b == self._swap_first or self._indexed_img is None:
            self._pal_status.setText("Swap cancelled")
            return

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            new_img, new_pal = swap_palette_entries(
                self._indexed_img, self._palette,
                self._swap_first, idx_b,
            )
            self._indexed_img = new_img
            self._palette = new_pal
            self._pal_grid.set_palette(new_pal)
            self._result_preview.set_image(new_img)
            self._pal_status.setText(
                f"Swapped index {self._swap_first} with index {idx_b}"
            )
        except Exception as e:
            QMessageBox.warning(self, "Swap Error", str(e))
        finally:
            QApplication.restoreOverrideCursor()

    def _cancel_swap(self):
        self._swap_mode = False
        self._swap_btn.setText("Swap with...")
        self._swap_btn.clicked.disconnect()
        self._swap_btn.clicked.connect(self._start_swap)
        self._pal_status.setText("Swap cancelled")

    # ── Export ───────────────────────────────────────────────────────────

    def _enable_export(self, enabled: bool):
        self._export_png_btn.setEnabled(enabled)
        self._export_pal_btn.setEnabled(enabled)
        self._export_both_btn.setEnabled(enabled)

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
            self._pal_status.setText(f"Saved indexed PNG to {os.path.basename(path)}")
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
            self._pal_status.setText(f"Saved palette to {os.path.basename(path)}")
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
            self._pal_status.setText(
                f"Saved {base}_indexed.png + {base}.pal"
            )
        else:
            parts = []
            if not ok_png:
                parts.append("PNG export failed")
            if not ok_pal:
                parts.append(".pal export failed")
            QMessageBox.warning(self, "Export Error", "\n".join(parts))
