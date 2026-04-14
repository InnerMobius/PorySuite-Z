"""
ui/image_indexer_tab.py
GBA Image Indexer — quantize PNGs to 16 or 256 GBA-compatible colors,
reorder palettes, set transparent/background color, export indexed PNGs
and JASC .pal files.

Sub-tab of the Tilemap Editor page.

Uses the same PaletteSwatchRow used by species graphics, trainers, etc.
No external dependencies beyond PyQt6 + numpy (already in requirements.txt).
"""
from __future__ import annotations

import os

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QFileDialog,
    QGroupBox, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QRadioButton, QSplitter, QVBoxLayout, QWidget,
)

from core.gba_image_utils import (
    quantize_image, remap_to_palette,
    move_color_to_index, swap_palette_entries,
    export_indexed_png, export_palette, get_image_info,
    gba_clamp_palette,
)
from ui.graphics_tab_widget import PaletteSwatchRow
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


# ── Image preview widget ────────────────────────────────────────────────────

class _ImagePreview(QLabel):
    """Zoomable image preview."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(200, 200)
        self.setStyleSheet("background-color: #1a1a1a; border: 1px solid #333;")
        self._pixmap: QPixmap | None = None

    def set_image(self, img: QImage | QPixmap | None):
        if img is None:
            self.clear()
            self._pixmap = None
            return
        if isinstance(img, QImage):
            # Convert indexed to ARGB for display
            if img.format() == QImage.Format.Format_Indexed8:
                img = img.convertToFormat(QImage.Format.Format_ARGB32)
            self._pixmap = QPixmap.fromImage(img)
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
    reorder palette, export indexed PNG and .pal files.

    Uses PaletteSwatchRow from the species graphics tab — click any swatch
    to open the GBA-clamped colour picker (same as every other editor).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._source_path: str = ""
        self._source_img: QImage | None = None
        self._indexed_img: QImage | None = None
        self._palette: list[tuple[int, int, int]] = []
        self._selected_idx: int = -1
        self._swap_mode: bool = False
        self._swap_first: int = -1
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

        # Palette display — uses the same PaletteSwatchRow as species/trainers
        pal_group = QGroupBox("Palette")
        pal_group.setStyleSheet(_GROUP_SS)
        pal_inner = QVBoxLayout(pal_group)
        pal_inner.setSpacing(4)

        pal_desc = QLabel(
            "Click any swatch to edit its colour (GBA 15-bit clamped). "
            "Index 0 is the transparent/background color on GBA."
        )
        pal_desc.setWordWrap(True)
        pal_desc.setStyleSheet(_NOTE_SS)
        pal_inner.addWidget(pal_desc)

        # We use one row for 16-color mode. For 256 we stack up to 16 rows.
        self._pal_rows: list[PaletteSwatchRow] = []
        self._pal_rows_container = QVBoxLayout()
        self._pal_rows_container.setSpacing(2)
        self._add_palette_row()  # Start with one row (16 colors)
        pal_inner.addLayout(self._pal_rows_container)

        # Palette action buttons
        pal_btns = QHBoxLayout()
        pal_btns.setSpacing(4)

        self._move_bg_btn = QPushButton("Set as BG (index 0)")
        self._move_bg_btn.setToolTip(
            "Move the selected color to palette index 0 (background/transparent). "
            "Select a swatch first by clicking it, then click this button."
        )
        self._move_bg_btn.setEnabled(False)
        self._move_bg_btn.clicked.connect(self._move_to_bg)
        pal_btns.addWidget(self._move_bg_btn)

        self._swap_btn = QPushButton("Swap with...")
        self._swap_btn.setToolTip(
            "Swap two palette entries. Click this, then click two swatches."
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

    # ── Palette row management ───────────────────────────────────────────

    def _add_palette_row(self) -> PaletteSwatchRow:
        row = PaletteSwatchRow()
        row.colors_changed.connect(self._on_palette_edited)
        self._pal_rows.append(row)
        self._pal_rows_container.addWidget(row)
        return row

    def _set_palette_display(self, palette: list[tuple[int, int, int]]):
        """Update the swatch rows to show the given palette."""
        n_rows_needed = max(1, (len(palette) + 15) // 16)

        # Add/remove rows as needed
        while len(self._pal_rows) < n_rows_needed:
            self._add_palette_row()
        while len(self._pal_rows) > n_rows_needed:
            row = self._pal_rows.pop()
            row.setParent(None)
            row.deleteLater()

        # Fill colours
        for row_i, row in enumerate(self._pal_rows):
            start = row_i * 16
            chunk = palette[start:start + 16]
            while len(chunk) < 16:
                chunk.append((0, 0, 0))
            row.set_colors(chunk)

    def _read_palette_from_rows(self) -> list[tuple[int, int, int]]:
        """Read current palette from all swatch rows."""
        result = []
        for row in self._pal_rows:
            result.extend(row.colors())
        return result

    def _on_palette_edited(self):
        """A swatch was clicked and edited via the colour picker."""
        if self._indexed_img is None:
            return
        new_pal = self._read_palette_from_rows()
        max_colors = 16 if self._color_16_rb.isChecked() else 256
        self._palette = new_pal[:max_colors]

        # Update the indexed image's colour table
        self._rebuild_image_palette()
        self._result_preview.set_image(self._indexed_img)
        self._pal_status.setText("Palette colour edited")
        self._enable_export(True)

    def _rebuild_image_palette(self):
        """Apply self._palette to the indexed QImage's colour table."""
        if self._indexed_img is None:
            return
        if self._indexed_img.format() != QImage.Format.Format_Indexed8:
            return
        ct = []
        for i, (r, g, b) in enumerate(self._palette):
            alpha = 0 if i == 0 else 255  # Index 0 = transparent
            ct.append((alpha << 24) | (r << 16) | (g << 8) | b)
        while len(ct) < 256:
            ct.append(0xFF000000)
        self._indexed_img.setColorTable(ct)

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
            QMessageBox.warning(self, "Load Error", f"Could not open image:\n{path}")
            return

        self._source_path = path
        self._source_img = img
        self._indexed_img = None
        self._palette = []

        # Show info
        info = get_image_info(path)
        w, h = info.get("width", 0), info.get("height", 0)
        cc = info.get("color_count", 0)
        cc_str = f"{cc} colors" if cc >= 0 else "many colors"
        is_idx = info.get("is_indexed", False)
        mode = info.get("mode", "?")

        self._info_label.setText(
            f"{os.path.basename(path)}  |  {w}x{h}  |  {mode}  |  {cc_str}"
        )

        # Show original preview
        self._orig_preview.set_image(img)
        self._result_preview.set_image(None)

        # If already indexed with <=256 colors, auto-load its palette
        if is_idx:
            self._auto_load_indexed(img, cc)

        self._quantize_btn.setEnabled(True)
        self._load_pal_btn.setEnabled(True)

    def _auto_load_indexed(self, img: QImage, cc: int):
        """Auto-load palette from an already-indexed image."""
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
            self._set_palette_display(palette)
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
            self._set_palette_display(colors)
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

    def _move_to_bg(self):
        """Move the last-edited swatch's colour to index 0."""
        # We need the user to tell us which index. For now, prompt.
        from PyQt6.QtWidgets import QInputDialog
        max_idx = len(self._palette) - 1
        idx, ok = QInputDialog.getInt(
            self, "Set as Background",
            f"Enter the palette index (1–{max_idx}) to move to index 0:",
            1, 1, max_idx,
        )
        if not ok or self._indexed_img is None:
            return

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            new_img, new_pal = move_color_to_index(
                self._indexed_img, self._palette, idx, 0,
            )
            self._indexed_img = new_img
            self._palette = new_pal
            self._set_palette_display(new_pal)
            self._result_preview.set_image(new_img)
            self._pal_status.setText(
                f"Moved color from index {idx} to index 0 (background)"
            )
        except Exception as e:
            QMessageBox.warning(self, "Reorder Error", str(e))
        finally:
            QApplication.restoreOverrideCursor()

    def _start_swap(self):
        """Swap two palette entries by index."""
        from PyQt6.QtWidgets import QInputDialog
        max_idx = len(self._palette) - 1
        a, ok_a = QInputDialog.getInt(
            self, "Swap Palette Entries",
            f"First index (0–{max_idx}):", 0, 0, max_idx,
        )
        if not ok_a or self._indexed_img is None:
            return
        b, ok_b = QInputDialog.getInt(
            self, "Swap Palette Entries",
            f"Second index (0–{max_idx}):", 1, 0, max_idx,
        )
        if not ok_b or a == b:
            return

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            new_img, new_pal = swap_palette_entries(
                self._indexed_img, self._palette, a, b,
            )
            self._indexed_img = new_img
            self._palette = new_pal
            self._set_palette_display(new_pal)
            self._result_preview.set_image(new_img)
            self._pal_status.setText(f"Swapped index {a} with index {b}")
        except Exception as e:
            QMessageBox.warning(self, "Swap Error", str(e))
        finally:
            QApplication.restoreOverrideCursor()

    # ── Export ───────────────────────────────────────────────────────────

    def _enable_export(self, enabled: bool):
        self._export_png_btn.setEnabled(enabled)
        self._export_pal_btn.setEnabled(enabled)
        self._export_both_btn.setEnabled(enabled)
        self._move_bg_btn.setEnabled(enabled)
        self._swap_btn.setEnabled(enabled)

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
            self._pal_status.setText(f"Saved {base}_indexed.png + {base}.pal")
        else:
            parts = []
            if not ok_png:
                parts.append("PNG export failed")
            if not ok_pal:
                parts.append(".pal export failed")
            QMessageBox.warning(self, "Export Error", "\n".join(parts))
