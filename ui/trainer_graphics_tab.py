"""Trainer Graphics tab — sprite + palette viewer/editor for trainer pics.

Sits alongside "Trainers" and "Trainer Classes" as a third tab in the
trainers section.  Lets the user:

  - Browse every trainer pic as a thumbnail card in a scrollable grid
  - Click a card to load its sprite + palette into the editor on the right
  - Edit palette colours via clickable swatches (same widget as Pokemon)
  - Drag-reorder palette slots (palette-only swap — pixels untouched)
  - Right-click a swatch → "Index as Background" to make that colour
    the transparent slot (pixel + palette swap, lockstep)
  - Import a palette from an indexed PNG or a JASC .pal file
  - Save the current sprite as an indexed PNG or the palette as a .pal

Cards with unsaved edits show an amber border + a dirty dot overlay.

Palette + PNG changes are held in-memory until File → Save (calls
``flush_to_disk()``). The per-button "Save Sprite PNG" / "Save .pal"
actions write directly to a user-chosen location outside the save
pipeline — handy for exporting a sprite to share or diff.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox,
    QPushButton, QFileDialog, QMessageBox,
    QSizePolicy, QScrollArea, QGridLayout, QLineEdit, QSplitter,
    QDialog, QDialogButtonBox, QFormLayout, QSpinBox,
)

from ui.palette_utils import read_jasc_pal, write_jasc_pal, clamp_to_gba

# Reuse reskin helper from the Pokemon graphics tab
from ui.graphics_tab_widget import _reskin_indexed_png
# Use the same drag-reorderable swatch widget as the Pokemon tab so the
# palette-editing feel is identical across both graphics screens.
from ui.draggable_palette_row import DraggablePaletteRow
from core.gba_image_utils import (
    swap_palette_entries, export_indexed_png, export_palette,
)
# Shared PNG→.pal path helper (single source of truth in graphics_data).
# Aliased to the previous local name so existing call sites don't churn.
from ui.graphics_data import trainer_pal_path_from_png as _pal_path_from_png
from core.sprite_palette_bus import get_bus as _get_palette_bus
from core.sprite_render import load_sprite_pixmap as _load_sprite_pixmap

Color = Tuple[int, int, int]


# ── Helpers ─────────────────────────────────────────────────────────────────

def _friendly_pic_name(pic_const: str) -> str:
    """Turn TRAINER_PIC_COOLTRAINER_M → 'Cooltrainer M'."""
    return pic_const.replace("TRAINER_PIC_", "").replace("_", " ").title()


# ── Card widget ─────────────────────────────────────────────────────────────

class _PicCard(QPushButton):
    """One tile in the scrollable grid: thumbnail + label + dirty indicator.

    Acts as a toggleable button so the currently-selected card stays
    visibly highlighted. Dirty state (unsaved palette edits for this
    pic) is rendered as an amber border + dot overlay.
    """

    THUMB_SIZE = 72
    CARD_W = 110
    CARD_H = 128

    def __init__(self, pic_const: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.pic_const = pic_const
        self._dirty = False
        self._selected = False
        self.setCheckable(True)
        self.setFixedSize(self.CARD_W, self.CARD_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        # Sprite thumbnail area
        self._thumb = QLabel()
        self._thumb.setFixedSize(self.THUMB_SIZE, self.THUMB_SIZE + 16)
        self._thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb.setStyleSheet("background: transparent; border: none;")
        layout.addWidget(self._thumb, 0, Qt.AlignmentFlag.AlignHCenter)

        # Friendly name (wraps to two lines max)
        self._name_lbl = QLabel(_friendly_pic_name(pic_const))
        self._name_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._name_lbl.setWordWrap(True)
        self._name_lbl.setStyleSheet(
            "background: transparent; border: none; "
            "color: #ccc; font-size: 10px;"
        )
        self._name_lbl.setFixedHeight(26)
        layout.addWidget(self._name_lbl)

        # Dirty dot overlay — sits on the thumbnail's top-right corner.
        self._dot = QLabel("\u25CF", self)
        self._dot.setStyleSheet(
            "background: transparent; color: #ffb74d; "
            "font-size: 13px; font-weight: bold;"
        )
        self._dot.setFixedSize(14, 14)
        self._dot.hide()
        # Anchored manually — resizeEvent keeps it pinned.
        self._dot.move(self.CARD_W - 18, 2)

        self._apply_style()

    def set_thumbnail(self, pix: QPixmap | None) -> None:
        if pix is None or pix.isNull():
            self._thumb.clear()
            self._thumb.setText("?")
            self._thumb.setStyleSheet(
                "background: transparent; color: #555; "
                "border: none; font-size: 18px;"
            )
            return
        scaled = pix.scaled(
            self._thumb.width(), self._thumb.height(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        self._thumb.setStyleSheet("background: transparent; border: none;")
        self._thumb.setPixmap(scaled)

    def set_dirty(self, dirty: bool) -> None:
        if dirty == self._dirty:
            return
        self._dirty = dirty
        self._dot.setVisible(dirty)
        self._apply_style()

    def set_selected(self, selected: bool) -> None:
        if selected == self._selected:
            return
        self._selected = selected
        self.setChecked(selected)
        self._apply_style()

    def _apply_style(self) -> None:
        """Card style depends on (selected, dirty) — selection wins for
        border colour but dirty still shows its amber dot."""
        if self._selected and self._dirty:
            border = "#ffb74d"
            bg = "#332a1a"
        elif self._selected:
            border = "#5a8cc5"
            bg = "#1a2636"
        elif self._dirty:
            border = "#ffb74d"
            bg = "#242016"
        else:
            border = "#333"
            bg = "#1a1a1a"
        self.setStyleSheet(
            f"QPushButton {{ background: {bg}; border: 1px solid {border}; "
            f"border-radius: 4px; padding: 0; text-align: center; }} "
            f"QPushButton:hover {{ background: #2a2a2a; border-color: #555; }}"
        )


# ═════════════════════════════════════════════════════════════════════════════
# Main widget
# ═════════════════════════════════════════════════════════════════════════════

class _AddTrainerPicDialog(QDialog):
    """Pick a PNG + name a new trainer pic.

    The dialog auto-derives the constant, the C symbol, and the snake_case
    base filename from the PNG's basename so the user typically only has
    to pick a file and click OK. All three derived names remain editable
    in case the auto-derivation isn't what they want.
    """

    def __init__(self, project_root: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Trainer Pic")
        self.setMinimumWidth(520)
        self._project_root = project_root
        self._png_path = ""

        outer = QVBoxLayout(self)
        outer.setSpacing(10)

        intro = QLabel(
            "Register a new trainer pic by picking an indexed PNG. The\n"
            "PNG, palette, INCBIN entries, table rows, and constant define\n"
            "are all wired up automatically — no hand-editing of C source.")
        intro.setStyleSheet("color: #aaa; font-size: 11px;")
        intro.setWordWrap(True)
        outer.addWidget(intro)

        # PNG picker row
        png_row = QHBoxLayout()
        self._png_edit = QLineEdit()
        self._png_edit.setReadOnly(True)
        self._png_edit.setPlaceholderText("Click Browse… to pick an indexed PNG")
        png_row.addWidget(self._png_edit, 1)
        self._browse_btn = QPushButton("Browse…")
        self._browse_btn.clicked.connect(self._on_browse)
        png_row.addWidget(self._browse_btn)
        outer.addLayout(png_row)

        # Auto-derived name fields
        form = QFormLayout()
        form.setSpacing(8)

        self._const_edit = QLineEdit()
        self._const_edit.setPlaceholderText(
            "Auto-filled from the PNG filename")
        form.addRow("Constant:", self._const_edit)

        self._symbol_edit = QLineEdit()
        self._symbol_edit.setPlaceholderText(
            "Auto-filled from the PNG filename")
        form.addRow("C symbol:", self._symbol_edit)

        self._base_edit = QLineEdit()
        self._base_edit.setPlaceholderText(
            "Auto-filled from the PNG filename")
        form.addRow("Base filename:", self._base_edit)

        # Battle-screen positioning. Underlying struct is `MonCoords`
        # (vanilla pokefirered shares this 2-field struct between mon and
        # trainer pics — the engine names it "Mon" but it works for both).
        # The user just sees "Sprite size" and "Y offset" — much clearer
        # than the struct name for someone who's never read the engine.
        coord_row = QHBoxLayout()
        self._size_spin = QSpinBox()
        self._size_spin.setRange(1, 16)
        self._size_spin.setValue(8)
        self._size_spin.setToolTip(
            "Power-of-2 sprite dimension code. 8 = 64×64 pixels, which\n"
            "is what virtually every vanilla trainer pic uses. Only\n"
            "change this if the sprite is a non-standard size.")
        self._yoff_spin = QSpinBox()
        self._yoff_spin.setRange(-32, 32)
        self._yoff_spin.setValue(1)
        self._yoff_spin.setToolTip(
            "Vertical pixel nudge applied during the battle intro\n"
            "animation. 1 matches almost every vanilla pic. Increase\n"
            "to push the sprite up; decrease to push it down.")
        coord_row.addWidget(QLabel("Sprite size:"))
        coord_row.addWidget(self._size_spin)
        coord_row.addSpacing(16)
        coord_row.addWidget(QLabel("Y offset:"))
        coord_row.addWidget(self._yoff_spin)
        coord_row.addStretch(1)
        form.addRow("Position:", coord_row)
        outer.addLayout(form)

        hint = QLabel(
            "Defaults of size 8, Y offset 1 match almost every vanilla\n"
            "pic. Tweak only if the sprite is a non-standard size or\n"
            "needs a different on-screen position during battle intro.")
        hint.setStyleSheet("color: #666; font-size: 10px;")
        hint.setWordWrap(True)
        outer.addWidget(hint)

        # OK / Cancel
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self)
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("Add")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        outer.addWidget(btns)

    def _on_browse(self) -> None:
        # Default to graphics/trainers/front_pics so the user lands in
        # the right folder if their PNG is already there.
        start = ""
        if self._project_root:
            start = os.path.join(
                self._project_root, "graphics", "trainers", "front_pics")
            if not os.path.isdir(start):
                start = self._project_root
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Trainer Pic PNG", start, "PNG Images (*.png)")
        if not path:
            return

        # Validate it's indexed before accepting.
        img = QImage(path)
        if img.isNull():
            QMessageBox.warning(
                self, "Invalid PNG", f"Could not load:\n{path}")
            return
        if img.format() != QImage.Format.Format_Indexed8:
            QMessageBox.warning(
                self, "Not Indexed",
                "The PNG must be 8-bit indexed (palette mode) with up to "
                "16 colours.\nOpen it in GIMP → Image → Mode → Indexed "
                "with 16 colours, then export and try again.")
            return

        self._png_path = path
        self._png_edit.setText(path)

        # Auto-derive the three names. Only fill fields the user hasn't
        # already typed into so manual edits aren't clobbered.
        from core.trainer_pic_registry import derive_names_from_filename
        const, sym, base = derive_names_from_filename(path)
        if not self._const_edit.text():
            self._const_edit.setText(const)
        if not self._symbol_edit.text():
            self._symbol_edit.setText(sym)
        if not self._base_edit.text():
            self._base_edit.setText(base)

    def values(self) -> dict:
        return {
            "png_path": self._png_path,
            "constant": self._const_edit.text().strip(),
            "symbol": self._symbol_edit.text().strip(),
            "base_name": self._base_edit.text().strip(),
            "coord_size": self._size_spin.value(),
            "coord_y_offset": self._yoff_spin.value(),
        }


class TrainerGraphicsTab(QWidget):
    """Trainer sprite palette viewer / editor / importer."""

    modified = pyqtSignal()

    # Rough target column count at the default window size; QGridLayout
    # re-flows when the scroll area resizes. 5 cols leaves enough room
    # on the right for the palette + import/export button stack without
    # the right panel feeling cramped.
    _GRID_COLS = 5

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

        # In-memory sprite images for pics whose pixels got remapped by
        # "Index as Background" (right-click on a swatch). Populated
        # lazily — plain palette edits and drag-reorder don't touch
        # pixel data so they don't enter this cache.
        self._sprite_imgs: Dict[str, QImage] = {}
        self._sprite_png_dirty: set[str] = set()

        # Grid cards keyed by pic const for quick dirty-state updates.
        self._cards: Dict[str, _PicCard] = {}

        self._build_ui()

    # ────────────────────────────────────────────────────────── build UI ──
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(10)

        # ── Header with search + info ───────────────────────────────────
        header = QHBoxLayout()
        header.setSpacing(8)
        title = QLabel("Trainer Graphics")
        title.setStyleSheet(
            "font-size: 14px; font-weight: bold; color: #e0e0e0;"
        )
        header.addWidget(title)
        header.addSpacing(12)

        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Filter by name or constant…")
        self._search_edit.setToolTip(
            "Type part of a trainer pic name (e.g. 'cool') or the\n"
            "TRAINER_PIC_* constant suffix to narrow the grid."
        )
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.setFixedWidth(220)
        self._search_edit.setStyleSheet(
            "background: #222; border: 1px solid #333; color: #ddd; "
            "padding: 3px 6px; border-radius: 3px;"
        )
        self._search_edit.textChanged.connect(self._on_search_changed)
        header.addWidget(self._search_edit)

        # "+ Add Trainer Pic" sits next to the search box — it's a
        # global action (creates a NEW pic) so it belongs at the top
        # alongside list-level controls, not in the right-hand editor
        # panel where the per-pic Import/Export buttons live.
        self._add_pic_btn = QPushButton("+ Add Trainer Pic…")
        self._add_pic_btn.setToolTip(
            "Register a new trainer pic in the project from an indexed\n"
            "PNG. Wires up the four C source files (constants, INCBIN\n"
            "decls, coords table, sprite table, palette table) plus a\n"
            ".pal sibling — atomically, with byte-equality guards."
        )
        header.addWidget(self._add_pic_btn)
        header.addStretch(1)

        self._count_lbl = QLabel("")
        self._count_lbl.setStyleSheet("color: #777; font-size: 10px;")
        header.addWidget(self._count_lbl)
        outer.addLayout(header)

        # ── Two-column body: left = card grid, right = selected-pic editor
        # QSplitter so the user can drag the divider to give whichever side
        # more room, and so neither side gets clipped when the window is
        # not maximized. Previous fixed-width right panel would vanish off
        # the edge of a non-maximized window.
        body = QSplitter(Qt.Orientation.Horizontal)
        body.setChildrenCollapsible(False)
        body.setHandleWidth(6)

        # LEFT: scrollable card grid
        self._grid_scroll = QScrollArea()
        self._grid_scroll.setWidgetResizable(True)
        self._grid_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._grid_scroll.setStyleSheet("background: #161616;")
        self._grid_host = QWidget()
        self._grid_host.setStyleSheet("background: #161616;")
        self._grid_layout = QGridLayout(self._grid_host)
        self._grid_layout.setContentsMargins(6, 6, 6, 6)
        self._grid_layout.setHorizontalSpacing(6)
        self._grid_layout.setVerticalSpacing(6)
        self._grid_scroll.setWidget(self._grid_host)
        # Minimum wide enough for ~3 cards so the grid stays usable when
        # the user drags the splitter handle left; the actual grid reflow
        # is driven by QGridLayout on resize.
        self._grid_scroll.setMinimumWidth(380)
        body.addWidget(self._grid_scroll)

        # RIGHT: selected pic — sprite preview + palette + import/export
        right = QVBoxLayout()
        right.setSpacing(10)
        right.setContentsMargins(4, 4, 4, 4)

        # Selected pic label + dirty dot
        sel_header = QHBoxLayout()
        sel_header.setSpacing(6)
        self._sel_lbl = QLabel("(no pic selected)")
        self._sel_lbl.setStyleSheet(
            "font-size: 12px; font-weight: bold; color: #ccc;"
        )
        self._sel_lbl.setWordWrap(True)
        sel_header.addWidget(self._sel_lbl, 1)
        self._dirty_dot = QLabel("\u25CF")
        self._dirty_dot.setFixedWidth(14)
        self._dirty_dot.setStyleSheet(
            "color: #ffb74d; font-size: 14px; font-weight: bold;"
        )
        self._dirty_dot.setToolTip("This pic has unsaved palette edits.")
        self._dirty_dot.hide()
        sel_header.addWidget(self._dirty_dot)
        right.addLayout(sel_header)

        # Sprite preview — bigger than before (was 128×160) so the whole
        # trainer pic is clearly visible instead of a squinty thumbnail.
        preview_group = QGroupBox("Sprite Preview")
        pg = QVBoxLayout(preview_group)
        pg.setContentsMargins(8, 16, 8, 8)
        pg.setSpacing(8)
        self._sprite_lbl = QLabel()
        self._sprite_lbl.setFixedSize(192, 192)
        self._sprite_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sprite_lbl.setStyleSheet(
            "background: #111; border: 1px solid #333;"
        )
        pg.addWidget(self._sprite_lbl, 0, Qt.AlignmentFlag.AlignHCenter)
        right.addWidget(preview_group)

        # Palette swatches (drag-reorderable, right-click for Index-as-BG).
        # Full panel width — swatches get plenty of room to drag.
        self._pal_row = DraggablePaletteRow()
        right.addWidget(self._wrap(
            "Palette  (drag to reorder  ·  right-click for Index as Background)",
            self._pal_row,
        ))

        # Import + Export groups side-by-side — halves the vertical stack
        # and mirrors how actions are grouped on the Pokemon graphics tab.
        action_row = QHBoxLayout()
        action_row.setSpacing(8)

        import_group = QGroupBox("Import")
        ig = QVBoxLayout(import_group)
        ig.setContentsMargins(8, 16, 8, 8)
        ig.setSpacing(6)

        self._import_sprite_btn = QPushButton("Import PNG as Sprite…")
        self._import_sprite_btn.setToolTip(
            "Pick a PNG and REPLACE the trainer's sprite image with\n"
            "it (pixels AND palette).  An indexed 8-bit PNG (≤16\n"
            "colours) is used directly; any other PNG automatically\n"
            "opens the manual palette picker so you can choose the\n"
            "16 colours and remap the image.  The on-disk sprite is\n"
            "not overwritten until you click File → Save on the toolbar."
        )
        ig.addWidget(self._import_sprite_btn)

        self._import_png_btn = QPushButton("Import Palette from PNG…")
        self._import_png_btn.setToolTip(
            "Pick an indexed (palette-mode) PNG and import ONLY its\n"
            "colour table into this trainer's palette. Pixel indices\n"
            "in the sprite are preserved."
        )
        ig.addWidget(self._import_png_btn)

        self._import_png_manual_btn = QPushButton("Import Palette Manually…")
        self._import_png_manual_btn.setToolTip(
            "Open the manual palette picker on any PNG.\n"
            "Pick which colours land in which slot, set the BG slot,\n"
            "reorder freely.  The chosen palette is loaded into this\n"
            "trainer's palette; pixel indices in the sprite are preserved."
        )
        ig.addWidget(self._import_png_manual_btn)

        self._import_pal_btn = QPushButton("Import .pal File…")
        self._import_pal_btn.setToolTip(
            "Pick a JASC .pal file and load its 16 colours into\n"
            "this trainer's palette. Existing pixel indices in the\n"
            "sprite are preserved — only the colour table changes.\n"
            "Click Save on the toolbar to commit to disk."
        )
        ig.addWidget(self._import_pal_btn)
        action_row.addWidget(import_group, 1)

        export_group = QGroupBox("Export / Save")
        eg = QVBoxLayout(export_group)
        eg.setContentsMargins(8, 16, 8, 8)
        eg.setSpacing(6)

        self._export_png_btn = QPushButton("Save Sprite as PNG…")
        self._export_png_btn.setToolTip(
            "Save the CURRENT sprite (pixels + palette) as an\n"
            "indexed PNG to a file you pick. Useful for exporting\n"
            "a recoloured trainer to share or diff.\n"
            "This does NOT replace the trainer's on-disk PNG — use\n"
            "File → Save on the toolbar for the normal save pipeline."
        )
        eg.addWidget(self._export_png_btn)

        self._export_pal_btn = QPushButton("Save Palette as .pal…")
        self._export_pal_btn.setToolTip(
            "Save the CURRENT palette as a JASC .pal file to a\n"
            "location you pick. 16 colours, GBA-clamped."
        )
        eg.addWidget(self._export_pal_btn)

        # Folder shortcut keeps the Export column balanced in height with
        # the Import column (3 buttons each) — and puts the folder action
        # near the other file-export actions where it belongs.
        self._open_folder_btn = QPushButton("Open Palettes Folder")
        self._open_folder_btn.setToolTip(
            "Open the trainer palettes directory in your OS file browser."
        )
        eg.addWidget(self._open_folder_btn)
        action_row.addWidget(export_group, 1)

        right.addLayout(action_row)
        right.addStretch(1)

        right_host = QWidget()
        right_host.setLayout(right)
        # Minimum width covers 192px preview + group padding + side-by-side
        # buttons without clipping. Splitter stretch factor lets the grid
        # take extra when the window is big.
        right_host.setMinimumWidth(440)
        right_host.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding,
        )
        body.addWidget(right_host)

        # Grid grows faster than the editor when the window widens; both
        # sides keep their minimum widths so no panel gets squeezed out.
        body.setStretchFactor(0, 3)
        body.setStretchFactor(1, 2)
        body.setSizes([820, 460])

        outer.addWidget(body, 1)

        # ── Wire signals ────────────────────────────────────────────────
        self._pal_row.colors_changed.connect(self._on_palette_edited)
        self._pal_row.palette_reordered.connect(self._on_palette_reordered)
        self._pal_row.swatch_set_as_bg.connect(self._on_set_swatch_as_bg)
        self._open_folder_btn.clicked.connect(self._open_palettes_folder)
        self._add_pic_btn.clicked.connect(self._on_add_trainer_pic)
        self._import_sprite_btn.clicked.connect(self._import_sprite_from_png)
        self._import_png_btn.clicked.connect(self._import_palette_from_png)
        self._import_png_manual_btn.clicked.connect(
            self._import_palette_from_png_manual)
        self._import_pal_btn.clicked.connect(self._import_palette_from_pal)
        self._export_png_btn.clicked.connect(self._export_sprite_png)
        self._export_pal_btn.clicked.connect(self._export_palette_file)

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
        self._current_png_path = ""
        self._dirty_dot.hide()
        self._sel_lbl.setText("(no pic selected)")
        self._sprite_lbl.clear()

        self._loading = True
        try:
            self._rebuild_grid()
            # Auto-select the first card so the right-hand editor is
            # populated on load, just like the dropdown version did.
            if self._pic_keys:
                self._select_card(self._pic_keys[0])
        finally:
            self._loading = False

    def _rebuild_grid(self) -> None:
        """Drop and recreate every card. Called on load + on filter change."""
        # Clear existing cards
        while self._grid_layout.count() > 0:
            item = self._grid_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._cards.clear()

        needle = self._search_edit.text().strip().lower() if hasattr(self, "_search_edit") else ""
        filtered: List[str] = []
        for key in self._pic_keys:
            if needle:
                hay = (key.lower() + " " + _friendly_pic_name(key).lower())
                if needle not in hay:
                    continue
            filtered.append(key)

        cols = self._GRID_COLS
        for i, key in enumerate(filtered):
            card = _PicCard(key)
            card.clicked.connect(lambda _=False, k=key: self._select_card(k))
            # Render thumbnail through the palette bus so the correct .pal
            # colours are shown (RAM-first, disk-fallback) rather than
            # whatever the PNG happens to have baked in.
            png_path = self._pic_map.get(key, "")
            pix = None
            if png_path and os.path.isfile(png_path):
                try:
                    palette = _get_palette_bus().ensure_trainer_palette_from_png(
                        png_path, pic_const=key
                    )
                    pix = _load_sprite_pixmap(png_path, palette)
                except Exception:
                    pix = QPixmap(png_path)
            card.set_thumbnail(pix)
            card.set_dirty(key in self._palette_dirty)
            card.set_selected(key == self._current_pic)
            row, col = divmod(i, cols)
            self._grid_layout.addWidget(card, row, col)
            self._cards[key] = card

        # Fill trailing row so cards don't stretch
        trailing_row = (len(filtered) + cols - 1) // cols
        self._grid_layout.setRowStretch(trailing_row, 1)

        # Update count label
        if needle:
            self._count_lbl.setText(
                f"{len(filtered)} of {len(self._pic_keys)} shown"
            )
        else:
            self._count_lbl.setText(f"{len(self._pic_keys)} trainer pics")

    def _on_search_changed(self, _: str) -> None:
        self._rebuild_grid()

    def select_pic(self, pic_const: str) -> None:
        """Programmatically switch to a specific TRAINER_PIC_* constant."""
        if pic_const in self._cards:
            self._select_card(pic_const)

    def _broadcast_palette(self, pic_const: str,
                           colors: List[Color]) -> None:
        """Push a trainer palette to the cross-tab SpritePaletteBus.

        Stores under BOTH the TRAINER_PIC_* constant and the PNG path
        so viewer tabs can hit a cache entry whichever key they have
        in hand (trainers-tab rows carry pic_const; the trainer class
        editor sometimes has only the PNG path).
        """
        bus = _get_palette_bus()
        bus.set_trainer_palette(pic_const, colors)
        png_path = self._pic_map.get(pic_const, "")
        if png_path:
            bus.set_palette("trainer_pic", png_path, colors)

    def _select_card(self, pic_const: str) -> None:
        """Swap selection to the given card and load its palette into the
        right-hand editor."""
        if not pic_const:
            return

        # Update card selection states
        prev = self._current_pic
        if prev and prev in self._cards:
            self._cards[prev].set_selected(False)
        if pic_const in self._cards:
            self._cards[pic_const].set_selected(True)

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

        # Update swatch row (loading guard prevents edit echo)
        self._loading = True
        try:
            self._pal_row.set_colors(self._palettes[pic_const])
        finally:
            self._loading = False

        # Refresh preview + labels
        self._sel_lbl.setText(f"{_friendly_pic_name(pic_const)}  ({pic_const})")
        self._refresh_sprite()
        self._dirty_dot.setVisible(pic_const in self._palette_dirty)

    # ────────────────────────────────────────────────────────── handlers ──
    def _on_palette_edited(self) -> None:
        if self._loading or not self._current_pic:
            return
        colors = self._pal_row.colors()
        self._palettes[self._current_pic] = colors
        self._palette_dirty.add(self._current_pic)
        self._broadcast_palette(self._current_pic, colors)
        self._refresh_sprite()
        self._dirty_dot.show()
        # Reflect on the card too so the grid shows the amber border.
        card = self._cards.get(self._current_pic)
        if card is not None:
            card.set_dirty(True)
            # Re-render its thumbnail with the new palette so the visual
            # state of the card matches the live preview.
            pix = _reskin_indexed_png(self._current_png_path, self._palettes[self._current_pic])
            if pix is None and self._current_png_path:
                pix = QPixmap(self._current_png_path)
            card.set_thumbnail(pix)
        if not self._loading:
            self.modified.emit()

    def _on_palette_reordered(self, from_idx: int, to_idx: int) -> None:
        """User dragged a swatch — swap slots ``from_idx`` and ``to_idx``
        in the palette. Pixels keep their index values (palette-only swap),
        so the rendered colour at each pixel changes. Matches the Pokemon
        graphics tab's reorder behaviour."""
        if self._loading or not self._current_pic:
            return
        n = 16
        if from_idx == to_idx or not (0 <= from_idx < n) or not (0 <= to_idx < n):
            return
        pal = list(self._palettes.get(self._current_pic) or [(0, 0, 0)] * n)
        while len(pal) < n:
            pal.append((0, 0, 0))
        pal[from_idx], pal[to_idx] = pal[to_idx], pal[from_idx]
        self._palettes[self._current_pic] = pal
        self._loading = True
        try:
            self._pal_row.set_colors(pal)
        finally:
            self._loading = False
        self._palette_dirty.add(self._current_pic)
        self._broadcast_palette(self._current_pic, pal)
        self._refresh_sprite()
        self._dirty_dot.show()
        card = self._cards.get(self._current_pic)
        if card is not None:
            card.set_dirty(True)
            pix = _reskin_indexed_png(self._current_png_path, pal)
            if pix is None and self._current_png_path:
                pix = QPixmap(self._current_png_path)
            card.set_thumbnail(pix)
        self.modified.emit()

    def _ensure_sprite_image_loaded(self, pic_const: str) -> None:
        """Lazy-load this trainer pic's PNG as an indexed QImage. Only
        the right-click Index-as-Background path needs pixel access —
        everything else just drives `_reskin_indexed_png`."""
        if pic_const in self._sprite_imgs:
            return
        png_path = self._pic_map.get(pic_const, "")
        if not png_path or not os.path.isfile(png_path):
            return
        img = QImage(png_path)
        if img.isNull():
            return
        if img.format() != QImage.Format.Format_Indexed8:
            img = img.convertToFormat(QImage.Format.Format_Indexed8)
        self._sprite_imgs[pic_const] = img

    def _on_set_swatch_as_bg(self, slot: int) -> None:
        """Right-click → "Index as Background": make the clicked colour
        the transparent slot. Swap pixel values ``slot`` ↔ ``0`` in the
        sprite PNG, then swap palette[0] ↔ palette[slot] so the rendered
        image is unchanged except the clicked colour is now transparent.

        This is the only path on this tab that mutates PNG pixel data.
        """
        if self._loading or not self._current_pic:
            return
        if slot <= 0 or slot >= 16:
            return

        sp = self._current_pic
        self._ensure_sprite_image_loaded(sp)
        img = self._sprite_imgs.get(sp)
        if img is None:
            QMessageBox.information(
                self, "No Sprite PNG",
                "This trainer pic has no on-disk PNG to remap.\n"
                "The palette-only swap has still been applied — if you\n"
                "later add the PNG, re-run Index as Background.",
            )

        n = 16
        pal = list(self._palettes.get(sp) or [(0, 0, 0)] * n)
        while len(pal) < n:
            pal.append((0, 0, 0))

        if img is not None:
            try:
                new_img, _ = swap_palette_entries(img, pal, slot, 0)
                self._sprite_imgs[sp] = new_img
                self._sprite_png_dirty.add(sp)
            except Exception as e:
                QMessageBox.warning(
                    self, "Index as Background Error",
                    f"Failed to remap sprite pixels:\n{e}",
                )
                return

        # Lockstep palette swap so the rendered image doesn't visibly
        # shift — slot 0 is the transparent slot by convention.
        pal[0], pal[slot] = pal[slot], pal[0]
        self._palettes[sp] = pal

        self._loading = True
        try:
            self._pal_row.set_colors(pal)
        finally:
            self._loading = False

        self._palette_dirty.add(sp)
        self._broadcast_palette(sp, pal)
        self._refresh_sprite()
        self._dirty_dot.show()
        card = self._cards.get(sp)
        if card is not None:
            card.set_dirty(True)
            # Card thumb shows the NEW transparent state — regenerate.
            pix = _reskin_indexed_png(self._current_png_path, pal)
            if pix is None and self._current_png_path:
                pix = QPixmap(self._current_png_path)
            card.set_thumbnail(pix)
        self.modified.emit()

    def _refresh_sprite(self) -> None:
        """Re-render the sprite preview using the current palette.

        If the pic has an in-memory remapped QImage (from an Index-as-BG
        operation), render that through the current palette instead of
        re-reading the on-disk PNG — otherwise the preview would show
        the stale on-disk pixels until save.
        """
        if not self._current_pic:
            self._sprite_lbl.clear()
            self._sprite_lbl.setText("No sprite")
            return

        palette = self._palettes.get(self._current_pic)
        pix: QPixmap | None = None

        in_mem_img = self._sprite_imgs.get(self._current_pic)
        if in_mem_img is not None and palette:
            # Render the in-memory indexed image with the live palette.
            from core.gba_image_utils import _rebuild_color_table
            try:
                rebuilt = _rebuild_color_table(in_mem_img, palette, 0)
                pix = QPixmap.fromImage(rebuilt)
            except Exception:
                pix = None

        if pix is None:
            if not self._current_png_path or not os.path.isfile(self._current_png_path):
                self._sprite_lbl.clear()
                self._sprite_lbl.setText("No sprite")
                return
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

    def _on_add_trainer_pic(self) -> None:
        """Open the Add Trainer Pic dialog and run the registry patch on Apply.

        Cross-cutting effect: this is the only PorySuite-Z action that
        modifies the PROJECT'S TRAINER REGISTRY (constants + table files
        + INCBIN list), so on success we have to refresh both the trainer
        graphics grid (to show the new card) and propagate the new pic_map
        to other tabs that depend on it (Trainers tab uses the same map
        for its pic-picker).
        """
        if not self._project_root:
            QMessageBox.information(
                self, "No Project",
                "Open a project first.")
            return

        dlg = _AddTrainerPicDialog(self._project_root, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        v = dlg.values()
        if not v["png_path"]:
            QMessageBox.warning(self, "Missing PNG", "Pick an indexed PNG first.")
            return
        if not v["constant"].startswith("TRAINER_PIC_"):
            QMessageBox.warning(
                self, "Bad Constant",
                "Constant must start with TRAINER_PIC_.")
            return
        if not v["symbol"]:
            QMessageBox.warning(
                self, "Bad Symbol",
                "C symbol cannot be empty.")
            return
        if not v["base_name"]:
            QMessageBox.warning(
                self, "Bad Base Name",
                "Base filename cannot be empty.")
            return

        from core.trainer_pic_registry import add_trainer_pic
        result = add_trainer_pic(
            project_root=self._project_root,
            source_png_path=v["png_path"],
            constant=v["constant"],
            symbol=v["symbol"],
            base_name=v["base_name"],
            coord_size=v["coord_size"],
            coord_y_offset=v["coord_y_offset"],
        )
        if not result.success:
            QMessageBox.critical(
                self, "Add Trainer Pic Failed", result.error)
            return

        # Refresh the pic_map and grid. The map is owned by the Trainers
        # tab, so re-run its parser to pick up the new entry, then push
        # the refreshed map into our load() to rebuild the grid.
        try:
            from ui.trainers_tab_widget import _parse_trainer_pic_map
            new_map = _parse_trainer_pic_map(self._project_root)
            self.load(self._project_root, new_map)
            # Auto-select the just-added card so the user lands on it.
            if v["constant"] in self._pic_map:
                self._select_card(v["constant"])
        except Exception:
            # Best-effort refresh — even if the in-tab refresh fails,
            # the on-disk patch was successful.
            pass

        QMessageBox.information(
            self, "Trainer Pic Added",
            f"{v['constant']} (id {result.pic_id}) registered.\n\n"
            f"Files modified:\n"
            f"  • graphics/trainers/front_pics/{v['base_name']}_front_pic.png\n"
            f"  • graphics/trainers/palettes/{v['base_name']}.pal\n"
            f"  • src/data/graphics/trainers.h\n"
            f"  • src/data/trainer_graphics/front_pic_tables.h\n"
            f"  • include/constants/trainers.h\n\n"
            f"Build the project to compile the new INCBIN entries — the "
            f"PorySuite-Z preview already shows them, but the game ROM "
            f"won't include them until you `make modern` (or your build "
            f"target of choice).")

    def _import_sprite_from_png(self) -> None:
        """Replace the current trainer's sprite image (pixels + palette)
        with a user-picked indexed PNG.

        Unlike "Import Palette from PNG" (which only grabs the colour
        table), this loads the full PNG into the in-memory sprite cache
        and marks the pic as having dirty pixel data AND a dirty palette.
        The on-disk PNG + .pal are only overwritten when the user clicks
        File → Save on the toolbar — so the action is reversible up until
        that point via undo (reload the project without saving).

        If the picked PNG is already a project-format indexed PNG (8-bit,
        ≤16 colours) it's used directly.  If it ISN'T — an RGB PNG, or
        indexed with too many colours — the manual palette picker opens
        so the user can choose/order 16 colours and the image is remapped
        to them.  Same flow the Overworld editor uses for non-indexed
        imports; no PNG is rejected outright.
        """
        if not self._current_pic:
            QMessageBox.information(
                self, "No Trainer Pic Selected",
                "Click a card in the grid first, then import a sprite.",
            )
            return

        # Default to this trainer's front_pics folder so the file dialog
        # opens where the user most likely has the replacement PNG sitting.
        start_dir = ""
        if self._current_png_path:
            candidate = os.path.dirname(self._current_png_path)
            if os.path.isdir(candidate):
                start_dir = candidate
        if not start_dir:
            start_dir = self._project_root or ""

        path, _ = QFileDialog.getOpenFileName(
            self, "Select PNG to Replace Sprite",
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

        colors: List[Color] = []
        ct = img.colorTable() if img.format() == QImage.Format.Format_Indexed8 else []
        # An indexed PNG with ≤16 distinct colour-table entries is already
        # in project format — use it directly with no extra dialog.
        if (img.format() == QImage.Format.Format_Indexed8
                and ct and len(set(ct)) <= 16):
            for entry in ct[:16]:
                r = (entry >> 16) & 0xFF
                g = (entry >> 8) & 0xFF
                b = entry & 0xFF
                colors.append(clamp_to_gba(r, g, b))
            while len(colors) < 16:
                colors.append((0, 0, 0))
        else:
            # Not a project-format indexed PNG (RGB, or indexed with too
            # many colours) — open the manual palette picker so the user
            # chooses/orders 16 colours, then remap the image to them.
            # Identical to the Overworld editor's non-indexed import flow.
            from ui.dialogs.manual_palette_pick_dialog import (
                import_image_manually_from_path,
            )
            result = import_image_manually_from_path(
                path, target_colors=16, parent=self,
            )
            if result is None:
                return  # user cancelled the picker
            colors, img = result
            colors = list(colors)

        # Apply: in-memory sprite + palette both get replaced, and both
        # dirty sets are flagged so flush_to_disk writes PNG + .pal.
        sp = self._current_pic
        self._sprite_imgs[sp] = img
        self._palettes[sp] = colors
        self._sprite_png_dirty.add(sp)
        self._palette_dirty.add(sp)
        self._broadcast_palette(sp, colors)

        self._loading = True
        try:
            self._pal_row.set_colors(colors)
        finally:
            self._loading = False

        self._refresh_sprite()
        self._dirty_dot.show()
        card = self._cards.get(sp)
        if card is not None:
            card.set_dirty(True)
            # Re-render the card's thumb from the new in-memory image.
            from core.gba_image_utils import _rebuild_color_table
            try:
                rebuilt = _rebuild_color_table(img, colors, 0)
                card.set_thumbnail(QPixmap.fromImage(rebuilt))
            except Exception:
                card.set_thumbnail(QPixmap(path))
        self.modified.emit()

        QMessageBox.information(
            self, "Sprite Imported",
            f"Loaded sprite from:\n{os.path.basename(path)}\n\n"
            f"Applied to: {sp}\n\n"
            "The preview shows the new sprite. Click File → Save to\n"
            "overwrite the trainer's on-disk PNG and .pal with this\n"
            "imported image + palette.",
        )

    def _import_palette_from_png(self) -> None:
        """Auto-extract palette from an indexed PNG and load it."""
        self._do_palette_import_from_png(manual=False)

    def _import_palette_from_png_manual(self) -> None:
        """Open the manual palette picker on any PNG."""
        self._do_palette_import_from_png(manual=True)

    def _do_palette_import_from_png(self, manual: bool) -> None:
        if not self._current_pic:
            QMessageBox.information(
                self, "No Trainer Pic Selected",
                "Click a card in the grid first, then import a palette.",
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
            self, "Select PNG" if manual else "Select Indexed PNG",
            start_dir,
            "PNG Images (*.png)",
        )
        if not path:
            return

        remapped_img = None
        if manual:
            from ui.dialogs.manual_palette_pick_dialog import (
                import_image_manually_from_path,
            )
            result = import_image_manually_from_path(
                path, target_colors=16, parent=self,
            )
            if result is None:
                return
            colors, remapped_img = result
            n_used = sum(1 for c in colors if c != (0, 0, 0))
        else:
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
                    "extracted — or use 'Import Palette Manually…' to\n"
                    "pick colours from any PNG.",
                )
                return

            ct = img.colorTable()
            if len(ct) < 1:
                QMessageBox.warning(self, "Empty Palette",
                                    "The PNG has no colour table entries.")
                return

            colors: List[Color] = []
            for entry in ct[:16]:
                r = (entry >> 16) & 0xFF
                g = (entry >> 8) & 0xFF
                b = entry & 0xFF
                colors.append(clamp_to_gba(r, g, b))
            while len(colors) < 16:
                colors.append((0, 0, 0))
            n_used = min(len(ct), 16)

        # Manual mode: overwrite the trainer's source PNG with the
        # remapped indexed image so the on-disk sprite matches the new
        # palette.  Auto mode preserves pixel indices.
        if manual and remapped_img is not None and self._current_png_path:
            try:
                from ui.dialogs.manual_palette_pick_dialog import (
                    save_remapped_image,
                )
                if not save_remapped_image(
                        remapped_img, colors, self._current_png_path):
                    QMessageBox.warning(
                        self, "Image Save Failed",
                        f"Palette loaded into the editor, but the "
                        f"remapped PNG couldn't be written to:\n"
                        f"{self._current_png_path}",
                    )
            except Exception as exc:
                QMessageBox.warning(
                    self, "Image Save Failed",
                    f"Could not save the remapped image:\n{exc}",
                )

        # Apply
        self._palettes[self._current_pic] = colors
        self._palette_dirty.add(self._current_pic)
        self._broadcast_palette(self._current_pic, colors)

        self._loading = True
        try:
            self._pal_row.set_colors(colors)
        finally:
            self._loading = False

        self._refresh_sprite()
        self._dirty_dot.show()
        card = self._cards.get(self._current_pic)
        if card is not None:
            card.set_dirty(True)
            pix = _reskin_indexed_png(self._current_png_path, colors)
            if pix is None and self._current_png_path:
                pix = QPixmap(self._current_png_path)
            card.set_thumbnail(pix)
        self.modified.emit()

        QMessageBox.information(
            self, "Palette Imported",
            f"Loaded {n_used} colours from:\n"
            f"{os.path.basename(path)}\n\n"
            f"Applied to: {self._current_pic}\n\n"
            "The preview has been updated. Click File → Save to\n"
            "write the .pal file to disk.",
        )

    def _import_palette_from_pal(self) -> None:
        """Load colours from a JASC .pal file into the current trainer's
        palette. Pixel indices are untouched — only the colour table
        changes. Mirrors the Pokemon graphics tab's "Import .pal File"
        action."""
        if not self._current_pic:
            QMessageBox.information(
                self, "No Trainer Pic Selected",
                "Click a card in the grid first, then import a .pal file.",
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
            self, "Select JASC .pal File",
            start_dir,
            "JASC Palette Files (*.pal);;All Files (*)",
        )
        if not path:
            return

        colors = read_jasc_pal(path)
        if not colors:
            QMessageBox.warning(
                self, "Import Failed",
                f"Could not read a JASC palette from:\n{path}\n\n"
                "The file must be a JASC-PAL 0100 format with 16 RGB lines.",
            )
            return

        # Pad / clamp to 16 GBA-safe entries.
        colors = [clamp_to_gba(*c) for c in colors[:16]]
        while len(colors) < 16:
            colors.append((0, 0, 0))

        self._palettes[self._current_pic] = colors
        self._palette_dirty.add(self._current_pic)
        self._broadcast_palette(self._current_pic, colors)

        self._loading = True
        try:
            self._pal_row.set_colors(colors)
        finally:
            self._loading = False

        self._refresh_sprite()
        self._dirty_dot.show()
        card = self._cards.get(self._current_pic)
        if card is not None:
            card.set_dirty(True)
            pix = _reskin_indexed_png(self._current_png_path, colors)
            if pix is None and self._current_png_path:
                pix = QPixmap(self._current_png_path)
            card.set_thumbnail(pix)
        self.modified.emit()

        QMessageBox.information(
            self, "Palette Imported",
            f"Loaded 16 colours from:\n{os.path.basename(path)}\n\n"
            f"Applied to: {self._current_pic}\n\n"
            "Click File → Save to write the trainer's .pal file to disk.",
        )

    def _export_sprite_png(self) -> None:
        """Save the current sprite + current palette as an indexed PNG to
        a user-chosen location. Uses the in-memory remapped QImage if the
        user has run Index-as-BG; otherwise reads the on-disk PNG and
        applies the current palette to its colour table."""
        if not self._current_pic or not self._current_png_path:
            QMessageBox.information(
                self, "No Trainer Pic Selected",
                "Click a card in the grid first, then use Save Sprite.",
            )
            return
        palette = self._palettes.get(self._current_pic)
        if not palette:
            QMessageBox.information(
                self, "No Palette",
                "This trainer pic has no palette loaded yet.",
            )
            return

        # Prefer in-memory image (may include pending Index-as-BG pixel
        # swaps); fall back to re-reading from disk.
        img = self._sprite_imgs.get(self._current_pic)
        if img is None:
            if not os.path.isfile(self._current_png_path):
                QMessageBox.warning(
                    self, "Export Failed",
                    f"Sprite PNG not found on disk:\n{self._current_png_path}",
                )
                return
            img = QImage(self._current_png_path)
            if img.isNull():
                QMessageBox.warning(
                    self, "Export Failed",
                    f"Could not load sprite PNG:\n{self._current_png_path}",
                )
                return
            if img.format() != QImage.Format.Format_Indexed8:
                img = img.convertToFormat(QImage.Format.Format_Indexed8)

        # Default output filename matches the source basename + "_edited"
        default = ""
        base = os.path.splitext(os.path.basename(self._current_png_path))[0]
        base_dir = os.path.dirname(self._current_png_path) or self._project_root or ""
        default = os.path.join(base_dir, f"{base}_edited.png") if base else ""

        path, _ = QFileDialog.getSaveFileName(
            self, "Save Indexed PNG", default,
            "PNG Images (*.png);;All Files (*)",
        )
        if not path:
            return

        if export_indexed_png(img, palette, path, transparent_index=0):
            QMessageBox.information(
                self, "Sprite Saved",
                f"Wrote indexed PNG:\n{path}\n\n"
                "This is a standalone export — the trainer's on-disk\n"
                "sprite (the one the game loads) is not affected until\n"
                "you click File → Save on the toolbar.",
            )
        else:
            QMessageBox.warning(self, "Export Failed", f"Could not save PNG to:\n{path}")

    def _export_palette_file(self) -> None:
        """Save the current palette as a JASC .pal file to a user-chosen
        location. 16 colours, GBA-clamped."""
        if not self._current_pic:
            QMessageBox.information(
                self, "No Trainer Pic Selected",
                "Click a card in the grid first, then use Save Palette.",
            )
            return
        palette = self._palettes.get(self._current_pic)
        if not palette:
            QMessageBox.information(
                self, "No Palette",
                "This trainer pic has no palette loaded yet.",
            )
            return

        default = ""
        if self._current_png_path:
            base = os.path.splitext(os.path.basename(self._current_png_path))[0]
            base = base.replace("_front_pic", "")
            base_dir = os.path.dirname(_pal_path_from_png(self._current_png_path))
            if base:
                default = os.path.join(base_dir or "", f"{base}_edited.pal")

        path, _ = QFileDialog.getSaveFileName(
            self, "Save JASC .pal", default,
            "JASC Palette Files (*.pal);;All Files (*)",
        )
        if not path:
            return

        if export_palette(palette, path):
            QMessageBox.information(
                self, "Palette Saved",
                f"Wrote JASC .pal:\n{path}\n\n"
                "This is a standalone export — the trainer's on-disk\n"
                ".pal file is not affected until you click File → Save.",
            )
        else:
            QMessageBox.warning(self, "Export Failed", f"Could not save .pal to:\n{path}")

    # ────────────────────────────────────────────────────────── save ──
    def has_unsaved_changes(self) -> bool:
        return bool(self._palette_dirty) or bool(self._sprite_png_dirty)

    def flush_to_disk(self) -> tuple[int, list[str]]:
        """Write all dirty palettes AND dirty sprite PNGs.

        Called by mainwindow save pipeline. Two passes:

        1. Palettes — every pic in ``_palette_dirty`` gets its .pal
           rewritten from the in-memory colour list.
        2. Sprite PNGs — every pic in ``_sprite_png_dirty`` gets its
           indexed PNG rewritten (used by Index-as-Background, which
           remapped pixel indices in memory).

        Only pics that were written successfully are removed from their
        dirty set — failures stay dirty so a subsequent save will retry
        them instead of silently dropping the edit.
        """
        ok = 0
        errors: list[str] = []

        # ── Pass 1: palettes ────────────────────────────────────────────
        # For every dirty pic:
        #   a) write the .pal (game runtime)
        #   b) bake the same palette into the PNG's embedded color table
        #      so opening the PNG in GIMP shows current colours instead
        #      of stale ones from the original PNG.
        pal_wrote: list[str] = []
        for pic_const in list(self._palette_dirty):
            png_path = self._pic_map.get(pic_const, "")
            if not png_path:
                errors.append(f"trainer-pal:{pic_const} (no png path)")
                continue
            pal_path = _pal_path_from_png(png_path)
            colors = self._palettes.get(pic_const)
            if not (colors and write_jasc_pal(pal_path, colors)):
                errors.append(f"trainer-pal:{pic_const}")
                continue
            ok += 1
            pal_wrote.append(pic_const)

            # Bake the new palette into the PNG. Pixel indices stay the
            # same — only the color table is rewritten. export_indexed_png
            # refuses non-Indexed8 input (would otherwise produce an RGB
            # PNG that breaks gbagfx during the build), so we guarantee
            # Indexed8 by loading from disk + converting if needed.
            img = self._sprite_imgs.get(pic_const)
            if img is None or img.format() != QImage.Format.Format_Indexed8:
                if os.path.isfile(png_path):
                    disk_img = QImage(png_path)
                    if not disk_img.isNull():
                        if disk_img.format() != QImage.Format.Format_Indexed8:
                            disk_img = disk_img.convertToFormat(
                                QImage.Format.Format_Indexed8)
                        img = disk_img
            if img is not None and not img.isNull() \
                    and img.format() == QImage.Format.Format_Indexed8:
                try:
                    if export_indexed_png(img, colors, png_path,
                                          transparent_index=0):
                        # PNG now in sync with the .pal — drop any
                        # leftover remap-dirty flag.
                        self._sprite_png_dirty.discard(pic_const)
                    else:
                        errors.append(
                            f"trainer-png-bake:{pic_const} "
                            f"(refused — not indexed)"
                        )
                except Exception as exc:
                    errors.append(f"trainer-png-bake:{pic_const} ({exc})")
        for pic_const in pal_wrote:
            self._palette_dirty.discard(pic_const)

        # ── Pass 2: sprite PNGs (from Index-as-Background remaps) ───────
        sprite_wrote: list[str] = []
        for pic_const in list(self._sprite_png_dirty):
            png_path = self._pic_map.get(pic_const, "")
            img = self._sprite_imgs.get(pic_const)
            pal = self._palettes.get(pic_const)
            if not png_path or img is None or not pal:
                errors.append(f"trainer-png:{pic_const}")
                continue
            try:
                if export_indexed_png(img, pal, png_path, transparent_index=0):
                    ok += 1
                    sprite_wrote.append(pic_const)
                else:
                    errors.append(f"trainer-png:{pic_const}")
            except Exception as e:
                errors.append(f"trainer-png:{pic_const} ({e})")
        for pic_const in sprite_wrote:
            self._sprite_png_dirty.discard(pic_const)

        # ── Refresh card dirty flags + the per-field dot ────────────────
        for pic_const in set(pal_wrote) | set(sprite_wrote):
            card = self._cards.get(pic_const)
            if card is not None:
                still_dirty = (
                    pic_const in self._palette_dirty
                    or pic_const in self._sprite_png_dirty
                )
                card.set_dirty(still_dirty)

        if self._current_pic is not None:
            still = (
                self._current_pic in self._palette_dirty
                or self._current_pic in self._sprite_png_dirty
            )
            self._dirty_dot.setVisible(still)
        else:
            self._dirty_dot.hide()
        return ok, errors
