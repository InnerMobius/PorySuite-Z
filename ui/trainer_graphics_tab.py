"""Trainer Graphics tab — palette viewer/editor + import for trainer sprites.

Sits alongside "Trainers" and "Trainer Classes" as a third tab in the
trainers section.  Lets the user:

  - Select a trainer pic from a dropdown
  - See the front sprite rendered with its current palette
  - Edit individual palette colours via clickable swatches
  - Import a palette from an indexed PNG (extracts the colour table)
  - Open the palettes folder in the OS file browser

Palette changes are held in-memory until File → Save, which calls
``flush_to_disk()``.
"""

from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QGroupBox,
    QPushButton, QFileDialog, QMessageBox, QRadioButton, QButtonGroup,
    QFrame, QSizePolicy,
)

from ui.palette_utils import read_jasc_pal, write_jasc_pal, clamp_to_gba

# Reuse the palette swatch row + reskin helper from the Pokemon graphics tab
from ui.graphics_tab_widget import PaletteSwatchRow, _reskin_indexed_png

Color = Tuple[int, int, int]


# ── Helpers ─────────────────────────────────────────────────────────────────

def _pal_path_from_png(png_path: str) -> str:
    """Derive the .pal path from a trainer front-pic PNG path.

    front_pics/aqua_leader_archie_front_pic.png
      → palettes/aqua_leader_archie.pal
    """
    folder = os.path.dirname(png_path)                     # .../front_pics
    parent = os.path.dirname(folder)                       # .../trainers
    base = os.path.basename(png_path)                      # xxx_front_pic.png
    slug = base.replace("_front_pic.png", "")              # xxx
    return os.path.join(parent, "palettes", f"{slug}.pal")


# ═════════════════════════════════════════════════════════════════════════════
# Main widget
# ═════════════════════════════════════════════════════════════════════════════

class TrainerGraphicsTab(QWidget):
    """Trainer sprite palette viewer / editor / importer."""

    modified = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._project_root: str = ""
        self._pic_map: Dict[str, str] = {}       # {TRAINER_PIC_*: png_path}
        self._pic_keys: List[str] = []            # sorted list of constants
        self._current_pic: Optional[str] = None
        self._current_png_path: str = ""
        self._loading = False

        # In-memory palette cache: {TRAINER_PIC_*: [16 Color tuples]}
        self._palettes: Dict[str, List[Color]] = {}
        self._palette_dirty: set[str] = set()

        self._build_ui()

    # ────────────────────────────────────────────────────────── build UI ──
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(12)

        # ── Trainer pic selector ────────────────────────────────────────
        sel_row = QHBoxLayout()
        sel_row.setSpacing(8)
        sel_row.addWidget(QLabel("Trainer Pic:"))
        self._pic_combo = QComboBox()
        self._pic_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._pic_combo.setToolTip(
            "Select a TRAINER_PIC_* constant to view and edit its palette."
        )
        # Prevent accidental scroll-wheel changes (per project rules)
        self._pic_combo.wheelEvent = lambda e: e.ignore()
        sel_row.addWidget(self._pic_combo, 1)
        outer.addLayout(sel_row)

        # ── Two-column body: left = sprite preview, right = palette tools ──
        body = QHBoxLayout()
        body.setSpacing(16)

        # LEFT: sprite preview
        preview_group = QGroupBox("Sprite Preview")
        pg = QVBoxLayout(preview_group)
        pg.setContentsMargins(8, 16, 8, 8)
        pg.setSpacing(8)

        self._sprite_lbl = QLabel()
        self._sprite_lbl.setFixedSize(128, 160)
        self._sprite_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sprite_lbl.setStyleSheet(
            "background: #111; border: 1px solid #333;"
        )
        pg.addWidget(self._sprite_lbl, 0, Qt.AlignmentFlag.AlignHCenter)

        self._open_folder_btn = QPushButton("Open Palettes Folder")
        self._open_folder_btn.setToolTip(
            "Open the trainer palettes directory in your OS file browser."
        )
        pg.addWidget(self._open_folder_btn)
        pg.addStretch(1)
        body.addWidget(preview_group, 0)

        # RIGHT: palette swatches + import
        right = QVBoxLayout()
        right.setSpacing(10)

        # Palette swatches
        self._pal_row = PaletteSwatchRow()
        right.addWidget(self._wrap("Palette (16 colours)", self._pal_row))

        # Import from PNG
        import_group = QGroupBox("Import Palette from PNG")
        ig = QVBoxLayout(import_group)
        ig.setContentsMargins(8, 16, 8, 8)
        ig.setSpacing(6)

        self._import_btn = QPushButton("Select Indexed PNG…")
        self._import_btn.setToolTip(
            "Pick an indexed (palette-mode) PNG and import its\n"
            "colour table into this trainer's .pal file."
        )
        ig.addWidget(self._import_btn)
        right.addWidget(import_group)

        right.addStretch(1)
        body.addLayout(right, 1)

        outer.addLayout(body, 1)

        # ── Wire signals ────────────────────────────────────────────────
        self._pic_combo.currentIndexChanged.connect(self._on_pic_changed)
        self._pal_row.colors_changed.connect(self._on_palette_edited)
        self._open_folder_btn.clicked.connect(self._open_palettes_folder)
        self._import_btn.clicked.connect(self._import_palette_from_png)

    def _wrap(self, title: str, inner: QWidget) -> QGroupBox:
        g = QGroupBox(title)
        gl = QVBoxLayout(g)
        gl.setContentsMargins(8, 14, 8, 8)
        gl.addWidget(inner)
        return g

    # ────────────────────────────────────────────────────────── loading ──
    def load(self, project_root: str, pic_map: Dict[str, str]) -> None:
        """Load trainer pic data. Called when a project is opened."""
        self._project_root = project_root
        self._pic_map = dict(pic_map)
        self._pic_keys = sorted(pic_map.keys())
        self._palettes.clear()
        self._palette_dirty.clear()
        self._current_pic = None

        self._loading = True
        try:
            self._pic_combo.blockSignals(True)
            self._pic_combo.clear()
            for key in self._pic_keys:
                # Display a friendly name: TRAINER_PIC_LASS → Lass
                label = key.replace("TRAINER_PIC_", "").replace("_", " ").title()
                self._pic_combo.addItem(f"{label}  ({key})", key)
            self._pic_combo.blockSignals(False)
            if self._pic_keys:
                self._pic_combo.setCurrentIndex(0)
                self._on_pic_changed(0)
        finally:
            self._loading = False

    def select_pic(self, pic_const: str) -> None:
        """Programmatically switch to a specific TRAINER_PIC_* constant."""
        idx = self._pic_combo.findData(pic_const)
        if idx >= 0:
            self._pic_combo.setCurrentIndex(idx)

    # ────────────────────────────────────────────────────────── handlers ──
    def _on_pic_changed(self, index: int) -> None:
        if index < 0 or index >= len(self._pic_keys):
            self._sprite_lbl.clear()
            self._sprite_lbl.setText("?")
            return

        pic_const = self._pic_combo.currentData()
        if not pic_const:
            return
        self._current_pic = pic_const
        png_path = self._pic_map.get(pic_const, "")
        self._current_png_path = png_path

        # Load palette from cache or disk
        if pic_const not in self._palettes:
            pal_path = _pal_path_from_png(png_path) if png_path else ""
            colors = read_jasc_pal(pal_path) if pal_path else []
            if not colors:
                colors = [(0, 0, 0)] * 16
            self._palettes[pic_const] = colors

        # Update swatch row
        self._loading = True
        try:
            self._pal_row.set_colors(self._palettes[pic_const])
        finally:
            self._loading = False

        # Render sprite with palette
        self._refresh_sprite()

    def _on_palette_edited(self) -> None:
        if self._loading or not self._current_pic:
            return
        self._palettes[self._current_pic] = self._pal_row.colors()
        self._palette_dirty.add(self._current_pic)
        self._refresh_sprite()
        if not self._loading:
            self.modified.emit()

    def _refresh_sprite(self) -> None:
        """Re-render the sprite preview using the current palette."""
        if not self._current_png_path or not os.path.isfile(self._current_png_path):
            self._sprite_lbl.clear()
            self._sprite_lbl.setText("No sprite")
            return

        palette = self._palettes.get(self._current_pic)
        pix = None
        if palette:
            pix = _reskin_indexed_png(self._current_png_path, palette)
        if pix is None:
            pix = QPixmap(self._current_png_path)
        if pix is None or pix.isNull():
            self._sprite_lbl.clear()
            self._sprite_lbl.setText("?")
            return

        # Scale up to fit the preview label (nearest-neighbour for pixel art)
        scaled = pix.scaled(
            self._sprite_lbl.width(), self._sprite_lbl.height(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        self._sprite_lbl.setPixmap(scaled)

    def _open_palettes_folder(self) -> None:
        if not self._project_root:
            return
        folder = os.path.join(self._project_root, "graphics", "trainers", "palettes")
        if not os.path.isdir(folder):
            folder = os.path.join(self._project_root, "graphics", "trainers")
        if os.path.isdir(folder):
            try:
                from ui.open_folder_util import open_folder
                open_folder(folder)
            except Exception:
                try:
                    os.startfile(folder)  # type: ignore[attr-defined]
                except Exception:
                    pass

    def _import_palette_from_png(self) -> None:
        """Extract palette from an indexed PNG and load it."""
        if not self._current_pic:
            QMessageBox.information(
                self, "No Trainer Pic Selected",
                "Select a trainer pic first, then import a palette.",
            )
            return

        # Default to this trainer's palettes folder
        start_dir = ""
        if self._current_png_path:
            pal_path = _pal_path_from_png(self._current_png_path)
            candidate = os.path.dirname(pal_path)
            if os.path.isdir(candidate):
                start_dir = candidate
        if not start_dir:
            start_dir = self._project_root or ""

        path, _ = QFileDialog.getOpenFileName(
            self, "Select Indexed PNG",
            start_dir,
            "PNG Images (*.png)",
        )
        if not path:
            return

        img = QImage(path)
        if img.isNull():
            QMessageBox.warning(
                self, "Import Failed",
                f"Could not load image:\n{path}",
            )
            return

        if img.format() != QImage.Format.Format_Indexed8:
            QMessageBox.warning(
                self, "Not an Indexed PNG",
                "This PNG is not in indexed (palette) mode.\n\n"
                "The image must be saved as an indexed-colour PNG\n"
                "(8-bit, 16 colours) so its embedded palette can be\n"
                "extracted. Convert it in your image editor first.",
            )
            return

        ct = img.colorTable()
        if len(ct) < 1:
            QMessageBox.warning(self, "Empty Palette", "The PNG has no colour table entries.")
            return

        colors: List[Color] = []
        for entry in ct[:16]:
            r = (entry >> 16) & 0xFF
            g = (entry >> 8) & 0xFF
            b = entry & 0xFF
            colors.append(clamp_to_gba(r, g, b))
        while len(colors) < 16:
            colors.append((0, 0, 0))

        # Apply
        self._palettes[self._current_pic] = colors
        self._palette_dirty.add(self._current_pic)

        self._loading = True
        try:
            self._pal_row.set_colors(colors)
        finally:
            self._loading = False

        self._refresh_sprite()
        self.modified.emit()

        QMessageBox.information(
            self, "Palette Imported",
            f"Loaded {len(ct[:16])} colours from:\n"
            f"{os.path.basename(path)}\n\n"
            f"Applied to: {self._current_pic}\n\n"
            "The preview has been updated. Click File → Save to\n"
            "write the .pal file to disk.",
        )

    # ────────────────────────────────────────────────────────── save ──
    def has_unsaved_changes(self) -> bool:
        return bool(self._palette_dirty)

    def flush_to_disk(self) -> tuple[int, list[str]]:
        """Write all dirty palettes. Called by mainwindow save pipeline."""
        ok = 0
        errors: list[str] = []
        for pic_const in list(self._palette_dirty):
            png_path = self._pic_map.get(pic_const, "")
            if not png_path:
                errors.append(f"trainer-pal:{pic_const} (no png path)")
                continue
            pal_path = _pal_path_from_png(png_path)
            colors = self._palettes.get(pic_const)
            if colors and write_jasc_pal(pal_path, colors):
                ok += 1
            else:
                errors.append(f"trainer-pal:{pic_const}")
        self._palette_dirty.clear()
        return ok, errors
