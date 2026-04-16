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

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QFileDialog, QGridLayout,
    QGroupBox, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QRadioButton, QScrollArea, QSpinBox, QSplitter,
    QVBoxLayout, QWidget,
)

from core.gba_image_utils import (
    quantize_image, remap_to_palette, swap_palette_entries,
    export_indexed_png, export_palette, get_image_info,
    gba_clamp_palette, get_quantize_candidates,
    QMODE_BALANCED, QMODE_SMOOTH, QMODE_PRESERVE_RARE, QMODE_MANUAL,
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

# ── Manual colour pick dialog ────────────────────────────────────────────────

class _ManualPickDialog(QDialog):
    """Shows ~24 candidate colours as clickable swatches.  User checks/unchecks
    to choose which ones to keep.  Live count label and small preview."""

    def __init__(self, candidates: list[tuple[int, int, int]],
                 target: int, source_img: QImage, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Pick {target} colours from {len(candidates)} candidates")
        self.setMinimumWidth(500)
        self._target = target
        self._candidates = candidates
        self._source_img = source_img
        self._checks: list[QCheckBox] = []
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        info = QLabel(
            f"The image was analysed and {len(self._candidates)} distinct colour "
            f"groups were found.  Check the {self._target} you want to keep.  "
            f"Unchecked colours will be mapped to the nearest checked colour."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self._count_label = QLabel("")
        self._count_label.setStyleSheet("font-weight: bold; font-size: 12px;")
        layout.addWidget(self._count_label)

        # Grid of swatches + checkboxes
        grid_widget = QWidget()
        grid = QGridLayout(grid_widget)
        grid.setSpacing(4)

        cols = 8
        for i, (r, g, b) in enumerate(self._candidates):
            row = i // cols
            col = i % cols

            cell = QVBoxLayout()
            cell.setSpacing(1)

            swatch = QLabel()
            swatch.setFixedSize(32, 32)
            swatch.setStyleSheet(
                f"background-color: rgb({r},{g},{b}); border: 1px solid #555;"
            )
            swatch.setToolTip(f"({r}, {g}, {b})")
            cell.addWidget(swatch, alignment=Qt.AlignmentFlag.AlignCenter)

            cb = QCheckBox()
            cb.setChecked(i < self._target)  # Pre-check the first N
            cb.toggled.connect(self._update_count)
            self._checks.append(cb)
            cell.addWidget(cb, alignment=Qt.AlignmentFlag.AlignCenter)

            grid.addLayout(cell, row, col)

        scroll = QScrollArea()
        scroll.setWidget(grid_widget)
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(300)
        layout.addWidget(scroll)

        # Preview
        self._preview = QLabel()
        self._preview.setFixedHeight(120)
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setStyleSheet("background: #1a1a1a; border: 1px solid #333;")
        layout.addWidget(self._preview)

        # Buttons
        btn_row = QHBoxLayout()
        select_all = QPushButton("Select All")
        select_all.clicked.connect(lambda: self._set_all(True))
        btn_row.addWidget(select_all)
        select_none = QPushButton("Select None")
        select_none.clicked.connect(lambda: self._set_all(False))
        btn_row.addWidget(select_none)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._update_count()

    def _set_all(self, checked: bool):
        for cb in self._checks:
            cb.setChecked(checked)

    def _update_count(self):
        n = sum(1 for cb in self._checks if cb.isChecked())
        color = "#66ff66" if n == self._target else (
            "#ffaa00" if n > self._target else "#ff6666"
        )
        self._count_label.setText(
            f'<span style="color:{color}">{n} / {self._target} selected</span>'
        )
        # Update preview
        self._update_preview()

    def _update_preview(self):
        selected = self.selected_colors()
        if not selected or self._source_img is None:
            return
        try:
            preview_img = remap_to_palette(self._source_img, selected, dither=False)
            argb = preview_img.convertToFormat(QImage.Format.Format_ARGB32)
            pm = QPixmap.fromImage(argb)
            scaled = pm.scaled(
                self._preview.width() - 4, self._preview.height() - 4,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
            self._preview.setPixmap(scaled)
        except Exception:
            pass

    def selected_colors(self) -> list[tuple[int, int, int]]:
        return [
            self._candidates[i]
            for i, cb in enumerate(self._checks) if cb.isChecked()
        ]


# ── Draggable palette swatch ────────────────────────────────────────────────
#
# DragSwatch + DraggablePaletteRow live in ui/draggable_palette_row.py so
# the Pokemon Graphics, Trainer Graphics and Overworld editors can reuse
# them. Local aliases preserve the original private names so the rest of
# this file is unchanged.

from ui.draggable_palette_row import (
    DraggablePaletteRow as _SharedDraggablePaletteRow,
)


class _DraggablePaletteRow(_SharedDraggablePaletteRow):
    """Local alias bridging the legacy `color_edited` signal name to the
    shared row's `colors_changed`."""
    color_edited = pyqtSignal()

    def __init__(self, n: int = 16, parent=None):
        super().__init__(n=n, parent=parent)
        self.colors_changed.connect(self.color_edited.emit)


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
            argb = img.convertToFormat(QImage.Format.Format_ARGB32)
            if not self._show_transparent:
                # Force all pixels fully opaque so index 0 shows its colour
                w, h = argb.width(), argb.height()
                bpl = argb.bytesPerLine()
                ptr = argb.bits()
                ptr.setsize(h * bpl)
                buf = np.frombuffer(ptr, dtype=np.uint8).reshape(h, bpl)
                # ARGB32 on little-endian: bytes are B, G, R, A
                buf[:, 3:w * 4:4] = 255  # set alpha channel to 255
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
        self._color_16_rb = QRadioButton("16")
        self._color_16_rb.setChecked(True)
        self._color_16_rb.setToolTip("4bpp — standard for sprites and tiles")
        self._color_16_rb.toggled.connect(self._on_target_radio_changed)
        top_bar.addWidget(self._color_16_rb)

        self._color_256_rb = QRadioButton("256")
        self._color_256_rb.setToolTip("8bpp — for backgrounds with many colors")
        self._color_256_rb.toggled.connect(self._on_target_radio_changed)
        top_bar.addWidget(self._color_256_rb)

        self._color_custom_rb = QRadioButton("Custom:")
        self._color_custom_rb.setToolTip(
            "Any number of colors (2-256). Useful when building a larger "
            "palette piece by piece — e.g. 37 colors for a castle, 45 for sky"
        )
        self._color_custom_rb.toggled.connect(self._on_target_radio_changed)
        top_bar.addWidget(self._color_custom_rb)

        self._color_custom_spin = QSpinBox()
        self._color_custom_spin.setRange(2, 256)
        self._color_custom_spin.setValue(32)
        self._color_custom_spin.setFixedWidth(60)
        self._color_custom_spin.setEnabled(False)
        self._color_custom_spin.setToolTip("Number of palette colors (2-256)")
        self._color_custom_spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # Wheel-scroll protection
        self._color_custom_spin.wheelEvent = lambda e: (
            QSpinBox.wheelEvent(self._color_custom_spin, e)
            if self._color_custom_spin.hasFocus() else e.ignore()
        )
        top_bar.addWidget(self._color_custom_spin)

        self._dither_cb = QCheckBox("Dither")
        self._dither_cb.setChecked(False)
        self._dither_cb.setToolTip(
            "Floyd-Steinberg dithering — smoother gradients. "
            "Turn off for pixel-art style."
        )
        top_bar.addWidget(self._dither_cb)

        top_bar.addWidget(QLabel("Mode:"))
        self._mode_combo = QComboBox()
        self._mode_combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # Wheel-scroll protection: only scroll when combo is focused (clicked)
        self._mode_combo.wheelEvent = lambda e: (
            QComboBox.wheelEvent(self._mode_combo, e)
            if self._mode_combo.hasFocus() else e.ignore()
        )
        self._mode_combo.addItem("Balanced", QMODE_BALANCED)
        self._mode_combo.addItem("Smooth Gradients", QMODE_SMOOTH)
        self._mode_combo.addItem("Preserve Rare Colors", QMODE_PRESERVE_RARE)
        self._mode_combo.addItem("Manual Pick", QMODE_MANUAL)
        self._mode_combo.setToolTip(
            "Balanced — fair representation of all unique colours\n"
            "Smooth Gradients — preserves subtle shading (pixel-weighted)\n"
            "Preserve Rare Colors — keeps unique colours even if they cover few pixels\n"
            "Manual Pick — choose which colours to keep from a larger candidate set"
        )
        top_bar.addWidget(self._mode_combo)

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
            "Load an existing JASC .pal file and apply it to the working\n"
            "image.\n\n"
            "Three paths are tried in order, least-lossy first:\n"
            "  1. If the image is already indexed (from an auto-loaded\n"
            "     indexed PNG or a previous Quantize), pixel indices are\n"
            "     kept exactly and the new palette's colours are slotted\n"
            "     in position-by-position — NSE2 behaviour. Loading\n"
            "     shiny.pal on a normal-indexed sprite gives the correct\n"
            "     shiny look.\n"
            "  2. If the image is RGB but has no more unique colours than\n"
            "     the target palette can hold, each unique colour is\n"
            "     auto-assigned to a slot (in first-appearance order) and\n"
            "     the loaded palette is applied slot-by-slot. Lossless.\n"
            "  3. Otherwise (RGB with too many colours, or Dither on\n"
            "     remap checked), a closest-colour remap is used. Lossy.\n\n"
            "The status line below the palette names which path ran."
        )
        self._load_pal_btn.setEnabled(False)
        self._load_pal_btn.clicked.connect(self._load_and_remap_palette)
        bar2.addWidget(self._load_pal_btn)

        self._remap_dither_cb = QCheckBox("Dither on remap")
        self._remap_dither_cb.setToolTip(
            "Force a closest-colour remap (with dithering) when loading a\n"
            ".pal file, even if the image is already indexed. Only useful\n"
            "when the loaded palette is genuinely different from the one\n"
            "the image was indexed with and you want a best-visual-match."
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

    # ── Target color count ───────────────────────────────────────────────

    def _get_max_colors(self) -> int:
        """Return the target palette size from the radio buttons / spinbox."""
        if self._color_16_rb.isChecked():
            return 16
        elif self._color_256_rb.isChecked():
            return 256
        else:
            return self._color_custom_spin.value()

    def _on_target_radio_changed(self, checked: bool):
        """Enable/disable the custom spinbox when the Custom radio is toggled."""
        self._color_custom_spin.setEnabled(self._color_custom_rb.isChecked())

    def _set_target_radio(self, n: int):
        """Set the target radio/spinbox to match a given color count."""
        if n == 16:
            self._color_16_rb.setChecked(True)
        elif n == 256:
            self._color_256_rb.setChecked(True)
        else:
            self._color_custom_rb.setChecked(True)
            self._color_custom_spin.setValue(max(2, min(256, n)))

    # ── Palette row helpers ──────────────────────────────────────────────

    def _add_pal_row(self) -> _DraggablePaletteRow:
        row = _DraggablePaletteRow(16)
        row.color_edited.connect(self._on_palette_color_edited)
        row.palette_reordered.connect(self._on_palette_reordered)
        row.swatch_set_as_bg.connect(self._on_set_swatch_as_bg)
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
        max_c = self._get_max_colors()
        self._palette = self._read_palette_from_rows()[:max_c]
        self._rebuild_image_palette()
        self._refresh_result_preview()
        self._pal_status.setText("Palette colour edited")

    def _on_palette_reordered(self, from_idx: int, to_idx: int):
        """User dragged swatch from_idx and dropped it on to_idx.

        PALETTE-ONLY SWAP — matches the Pokemon Graphics tab exactly.
        Only the two entries in ``self._palette`` are swapped; the indexed
        image's pixel values are NEVER touched.  What changes visually is
        WHICH colour shows at each slot — a pixel whose stored value is
        ``from_idx`` will now display the colour previously at ``to_idx``,
        and vice versa.  ``_rebuild_image_palette`` pushes the new colour
        table onto ``_indexed_img`` so the preview picks it up.

        Dropping onto slot 0 makes the dragged colour the transparent slot
        (pokefirered convention: slot 0 is tRNS).
        """
        if self._indexed_img is None or not self._palette:
            return
        if from_idx == to_idx or from_idx < 0 or to_idx < 0:
            return
        # Defensive: make sure _palette is long enough to cover both drag
        # indices. _set_palette_display always shows a multiple of 16
        # swatches, so a drag from a padded swatch can land here with
        # an index beyond a short palette — pad with black rather than
        # IndexError below.
        need = max(from_idx, to_idx) + 1
        if len(self._palette) < need:
            self._palette = list(self._palette) + \
                [(0, 0, 0)] * (need - len(self._palette))
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            pal = list(self._palette)
            pal[from_idx], pal[to_idx] = pal[to_idx], pal[from_idx]
            self._palette = pal
            # Push the new colour table onto the indexed image.  Pixel
            # indices are untouched; only the colour table changes.
            self._rebuild_image_palette()
            self._set_palette_display(pal)
            self._refresh_result_preview()
            if to_idx == 0:
                self._pal_status.setText(
                    f"Swapped slot {from_idx} ↔ slot 0 (BG — transparent)"
                )
            else:
                self._pal_status.setText(
                    f"Swapped slot {from_idx} ↔ slot {to_idx}"
                )
        except Exception as e:
            import traceback
            QMessageBox.warning(
                self, "Reorder Error",
                f"{e}\n\n{traceback.format_exc()}",
            )
        finally:
            QApplication.restoreOverrideCursor()

    def _on_set_swatch_as_bg(self, slot: int):
        """Right-click → "Index as Background" on palette slot ``slot``.

        Unlike the drag-reorder (palette-only swap), this operation is a
        pixel+palette swap — pixels stored as value ``slot`` become value
        ``0`` and vice versa, and palette entries ``0`` and ``slot`` trade
        places. Net visible result: whichever colour the user right-clicked
        is now transparent; whatever was transparent before is now showing
        as that colour at the old slot. The saved Indexed PNG has slot 0
        as the tRNS colour by convention, so this is how you pick "which
        colour region of the image becomes transparent".
        """
        if self._indexed_img is None or not self._palette:
            return
        if slot <= 0:
            return  # slot 0 itself is already BG
        # Defensive pad — never crash if a padded-black swatch was clicked.
        need = slot + 1
        if len(self._palette) < need:
            self._palette = list(self._palette) + \
                [(0, 0, 0)] * (need - len(self._palette))
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            new_img, new_pal = swap_palette_entries(
                self._indexed_img, self._palette, slot, 0,
            )
            self._indexed_img = new_img
            self._palette = new_pal
            self._set_palette_display(new_pal)
            self._refresh_result_preview()
            self._pal_status.setText(
                f"Indexed slot {slot} as background "
                f"(swapped with slot 0 — transparent on save)"
            )
        except Exception as e:
            import traceback
            QMessageBox.warning(
                self, "Index as Background Error",
                f"{e}\n\n{traceback.format_exc()}",
            )
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
        # Use exact color count — 16/256 go to their presets, others go custom
        target = min(cc, 256) if cc > 0 else 16
        colors = []
        for c in ct[:target]:
            r = (c >> 16) & 0xFF
            g = (c >> 8) & 0xFF
            b = c & 0xFF
            colors.append(clamp_to_gba(r, g, b))
        # Pad _palette to the full visual swatch count (always a multiple of 16,
        # at minimum 16) so that every on-screen swatch corresponds to a real
        # palette entry. Without this, dragging from a "padded black" swatch
        # past the real palette length would raise ValueError in the reorder
        # helpers (indices beyond len(palette) aren't valid).
        display_target = max(target, 16)
        if display_target % 16:
            display_target = ((display_target // 16) + 1) * 16
        while len(colors) < display_target:
            colors.append((0, 0, 0))
        self._palette = colors
        self._indexed_img = img.copy()
        self._rebuild_image_palette()
        self._set_palette_display(colors)
        self._refresh_result_preview()
        self._pal_status.setText(f"Loaded existing {cc}-color palette")
        self._enable_export(True)
        self._set_target_radio(target)

    # ── Quantize ─────────────────────────────────────────────────────────

    def _do_quantize(self):
        if self._source_img is None:
            return
        max_colors = self._get_max_colors()
        dither = self._dither_cb.isChecked()
        mode = self._mode_combo.currentData() or QMODE_BALANCED

        # Manual Pick: show candidate dialog first
        manual_palette = None
        if mode == QMODE_MANUAL:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                candidates = get_quantize_candidates(
                    self._source_img,
                    n_candidates=max(max_colors + 8, 24),
                    gba_clamp=True,
                )
            except Exception as e:
                QApplication.restoreOverrideCursor()
                QMessageBox.warning(self, "Candidate Error", str(e))
                return
            QApplication.restoreOverrideCursor()

            dlg = _ManualPickDialog(
                candidates, max_colors, self._source_img, parent=self,
            )
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            manual_palette = dlg.selected_colors()
            if not manual_palette:
                QMessageBox.warning(self, "No Colors", "No colours were selected.")
                return

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            indexed, palette = quantize_image(
                self._source_img, max_colors, dither, gba_clamp=True,
                mode=mode, manual_palette=manual_palette,
            )
            self._indexed_img = indexed
            self._palette = palette
            self._set_palette_display(palette)
            self._refresh_result_preview()
            mode_name = self._mode_combo.currentText()
            self._pal_status.setText(
                f"Quantized to {len(palette)} GBA-safe colors "
                f"({mode_name}, {'dithered' if dither else 'no dither'})"
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
        max_colors = self._get_max_colors()
        colors = read_jasc_pal(path, max_colors)
        if not colors:
            QMessageBox.warning(self, "Load Error", "Could not read .pal file")
            return
        colors = gba_clamp_palette(colors[:max_colors])
        while len(colors) < max_colors:
            colors.append((0, 0, 0))
        dither = self._remap_dither_cb.isChecked()

        # Three ways to apply a .pal to the working image.  We always try
        # the least-lossy one that applies, and fall back to closest-colour
        # remap only when nothing else fits.
        #
        # 1. Slot-preserving on existing indexed layout.
        #    If we already have an indexed working image (from auto-load
        #    of an indexed PNG, or from a previous Quantize), we keep its
        #    pixel indices untouched and swap the colour table to the
        #    loaded palette.  Pixel-value N renders as loaded_pal[N] —
        #    this is NSE2 behaviour and is what makes "load shiny.pal on
        #    a normal-indexed sprite" produce a correct shiny.
        #
        # 2. Slot-preserving on an RGB source with few unique colours.
        #    If the source is RGB but has no more unique colours than the
        #    target palette can hold, we first build an indexed version
        #    by assigning each unique source colour to a slot in first-
        #    appearance order, then apply the loaded palette as colour
        #    table.  No colour information is lost — every pixel keeps a
        #    1:1 identity, the loaded palette is applied in full, and the
        #    user can drag-reorder after if the auto-assigned slot order
        #    isn't what they want.
        #
        # 3. Closest-colour remap (the legacy path).
        #    Used when the source is RGB with more unique colours than
        #    the target, or when the user explicitly ticks "Dither on
        #    remap".  Lossy by nature.
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            status_detail: str
            if not dither and self._working_image_is_indexed():
                # Case 1: slot-preserving on existing indexed layout.
                # If the original source was indexed and we haven't done
                # a destructive quantize, rebase on the source so repeat
                # Load .pal calls are idempotent.
                if (self._indexed_img is None
                        and self._source_img.format()
                        == QImage.Format.Format_Indexed8):
                    self._indexed_img = self._source_img.copy()
                self._palette = colors
                self._rebuild_image_palette()
                status_detail = "slot-preserving swap"
            elif not dither and self._try_auto_index_rgb(colors, max_colors):
                # Case 2: success — _try_auto_index_rgb updated
                # _indexed_img + _palette in place.
                status_detail = "slot-preserving swap (RGB auto-indexed)"
            else:
                # Case 3: closest-colour remap.
                self._indexed_img = remap_to_palette(
                    self._source_img, colors, dither,
                )
                self._palette = colors
                status_detail = (
                    "closest-color, dither" if dither else "closest-color"
                )
            self._set_palette_display(colors)
            self._refresh_result_preview()
            self._pal_status.setText(
                f"Loaded {os.path.basename(path)} "
                f"({len(colors)} colors, {status_detail})"
            )
            self._enable_export(True)
        except Exception as e:
            import traceback
            QMessageBox.warning(
                self, "Remap Error",
                f"{e}\n\n{traceback.format_exc()}",
            )
        finally:
            QApplication.restoreOverrideCursor()

    def _working_image_is_indexed(self) -> bool:
        """Return True if there's an existing indexed layout to swap onto.

        That's either the current working indexed image (from a prior
        auto-load or Quantize) or a fresh indexed source PNG we haven't
        processed yet.
        """
        if (self._indexed_img is not None
                and self._indexed_img.format()
                == QImage.Format.Format_Indexed8):
            return True
        if (self._source_img is not None
                and self._source_img.format()
                == QImage.Format.Format_Indexed8):
            return True
        return False

    def _try_auto_index_rgb(
        self, colors: list[tuple[int, int, int]], max_colors: int,
    ) -> bool:
        """Build an indexed working image from an RGB source that has no
        more unique colours than the target palette can hold.

        Unique colours are assigned to slots in first-appearance order
        (top-to-bottom, left-to-right raster scan), then the loaded
        palette is set as the colour table.  Returns True on success;
        False means "too many unique colours, caller should fall back to
        closest-colour remap" and leaves state untouched.
        """
        from core.gba_image_utils import (
            _qimage_to_rgb_array, _indexed_array_to_qimage,
        )
        try:
            rgb, _alpha = _qimage_to_rgb_array(self._source_img)
        except Exception:
            return False
        h, w, _ = rgb.shape
        # Pack RGB to a single uint32 so numpy.unique can work 1D.
        packed = (
            (rgb[:, :, 0].astype(np.uint32) << 16)
            | (rgb[:, :, 1].astype(np.uint32) << 8)
            | rgb[:, :, 2].astype(np.uint32)
        ).reshape(-1)
        uniq, first_idx = np.unique(packed, return_index=True)
        if len(uniq) > max_colors:
            return False
        # Sort by first-appearance so slot order matches raster scan of
        # the original image — deterministic and intuitive.
        order = np.argsort(first_idx)
        sorted_uniq = uniq[order]
        # Assign each unique packed colour to a slot.
        indices = np.zeros_like(packed, dtype=np.uint8)
        for slot, packed_val in enumerate(sorted_uniq):
            indices[packed == int(packed_val)] = slot
        indices = indices.reshape(h, w)
        new_img = _indexed_array_to_qimage(indices, colors, transparent_index=0)
        self._indexed_img = new_img
        self._palette = colors
        return True

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
