"""Trainer Back Sprites tab — edit the trainer BACK pics + their throw anim.

Sits alongside "Trainers", "Trainer Classes", and "Graphics" (front pics) as a
fourth tab in the trainers section. Back pics are the over-the-shoulder trainer
sprites shown during battle; unlike the single-frame front pics they are
MULTI-FRAME vertical strips (64 x 64*N) — frame 0 is the idle pose and frames
1..N-1 are the wind-up-and-throw animation.

The tab mirrors the front Graphics tab (card grid, PNG import with the manual
palette picker, draggable palette swatches, in-memory edits flushed on Save) and
adds:

  - frame-0 thumbnails / preview (the strip is cropped to one 64x64 frame)
  - an animated THROW preview: a Play button cycles the real anim frames at the
    game's own timings, plus a slider to step through frames manually
  - "+ Add Back Sprite" which registers a brand-new back pic across the C source
    (constant, INCBINs, the three tables, and the throw-anim table)

Cards with unsaved edits show an amber border + dot, same as the front tab.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox, QPushButton,
    QFileDialog, QMessageBox, QSizePolicy, QScrollArea, QGridLayout,
    QLineEdit, QSplitter, QSlider, QDialog, QDialogButtonBox, QFormLayout,
    QSpinBox,
)

from ui.palette_utils import read_jasc_pal, write_jasc_pal, clamp_to_gba
from ui.graphics_tab_widget import _reskin_indexed_png
from ui.draggable_palette_row import DraggablePaletteRow
from core.gba_image_utils import export_indexed_png
from core.sprite_palette_bus import get_bus as _get_palette_bus
from ui.trainer_graphics_tab import _PicCard
import core.trainer_back_pic_registry as backreg

Color = Tuple[int, int, int]

FRAME_H = 64   # one back-pic frame is 64x64; the strip is 64 x 64*N


def _friendly_back_name(const: str) -> str:
    """TRAINER_BACK_PIC_OLD_MAN -> 'Old Man'."""
    return const.replace("TRAINER_BACK_PIC_", "").replace("_", " ").title()


# ── Add-back-pic dialog ─────────────────────────────────────────────────────

class _AddBackPicDialog(QDialog):
    """Pick a back-pic PNG strip + name a new back sprite. The frame count is
    auto-detected from the PNG height (must be a multiple of 64)."""

    def __init__(self, project_root: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Back Sprite")
        self.setMinimumWidth(540)
        self._project_root = project_root
        self._png_path = ""
        self._frames = 0
        self._remapped_img: Optional[QImage] = None
        self._remapped_palette: Optional[List[Color]] = None

        outer = QVBoxLayout(self)
        outer.setSpacing(10)
        intro = QLabel(
            "Register a new trainer back sprite. Pick a 64-wide PNG strip whose\n"
            "height is a multiple of 64 — frame 0 (top) is the idle pose, the\n"
            "rest are the throw animation. If the PNG isn't already indexed with\n"
            "≤16 colours the manual palette picker opens so you can choose /\n"
            "order the colours. The PNG, palette, INCBINs, the three back-pic\n"
            "tables, a default throw-anim, and the constant are all wired up —\n"
            "no hand-editing of C source.")
        intro.setStyleSheet("color: #aaa; font-size: 11px;")
        intro.setWordWrap(True)
        outer.addWidget(intro)

        png_row = QHBoxLayout()
        self._png_edit = QLineEdit()
        self._png_edit.setReadOnly(True)
        self._png_edit.setPlaceholderText("Click Browse… to pick a back-pic PNG strip")
        png_row.addWidget(self._png_edit, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._on_browse)
        png_row.addWidget(browse)
        outer.addLayout(png_row)

        self._frames_lbl = QLabel("Frames: —")
        self._frames_lbl.setStyleSheet("color: #9bd; font-size: 11px;")
        outer.addWidget(self._frames_lbl)

        form = QFormLayout()
        form.setSpacing(8)
        self._const_edit = QLineEdit()
        self._const_edit.setPlaceholderText("Auto-filled from the PNG filename")
        form.addRow("Constant:", self._const_edit)
        self._symbol_edit = QLineEdit()
        self._symbol_edit.setPlaceholderText("Auto-filled from the PNG filename")
        form.addRow("C symbol:", self._symbol_edit)
        self._base_edit = QLineEdit()
        self._base_edit.setPlaceholderText("Auto-filled from the PNG filename")
        form.addRow("Base filename:", self._base_edit)

        coord_row = QHBoxLayout()
        self._size_spin = QSpinBox()
        self._size_spin.setRange(1, 16)
        self._size_spin.setValue(8)
        self._size_spin.setToolTip(
            "Sprite dimension code. 8 = 64x64 per frame (every vanilla back pic).")
        self._yoff_spin = QSpinBox()
        self._yoff_spin.setRange(-32, 32)
        self._yoff_spin.setValue(4)
        self._yoff_spin.setToolTip(
            "Vertical pixel nudge in battle. 4-5 matches the vanilla back pics.")
        coord_row.addWidget(QLabel("Sprite size:"))
        coord_row.addWidget(self._size_spin)
        coord_row.addSpacing(16)
        coord_row.addWidget(QLabel("Y offset:"))
        coord_row.addWidget(self._yoff_spin)
        coord_row.addStretch(1)
        form.addRow("Position:", coord_row)
        outer.addLayout(form)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self)
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("Add")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        outer.addWidget(btns)

    def _on_browse(self) -> None:
        start = ""
        if self._project_root:
            start = os.path.join(
                self._project_root, "graphics", "trainers", "back_pics")
            if not os.path.isdir(start):
                start = self._project_root
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Back Pic PNG", start, "PNG Images (*.png)")
        if not path:
            return
        img = QImage(path)
        if img.isNull():
            QMessageBox.warning(self, "Invalid PNG", f"Could not load:\n{path}")
            return
        if img.width() != FRAME_H or img.height() % FRAME_H != 0 or img.height() < FRAME_H:
            QMessageBox.warning(
                self, "Wrong Size",
                f"A back-pic strip must be {FRAME_H}px wide and a multiple of "
                f"{FRAME_H}px tall (one 64x64 frame per row).\n\n"
                f"This image is {img.width()}x{img.height()}.")
            return
        self._frames = img.height() // FRAME_H
        self._frames_lbl.setText(
            f"Frames: {self._frames}  (frame 0 = idle, {self._frames - 1} throw frame(s))")

        self._remapped_img = None
        self._remapped_palette = None
        ct = (img.colorTable()
              if img.format() == QImage.Format.Format_Indexed8 else [])
        if not (img.format() == QImage.Format.Format_Indexed8
                and ct and len(set(ct)) <= 16):
            from ui.dialogs.manual_palette_pick_dialog import (
                import_image_manually_from_path)
            result = import_image_manually_from_path(path, target_colors=16, parent=self)
            if result is None:
                return
            palette, remapped = result
            self._remapped_img = remapped
            self._remapped_palette = list(palette)

        self._png_path = path
        self._png_edit.setText(path)
        const, sym, base = backreg.derive_back_names_from_filename(path)
        if not self._const_edit.text():
            self._const_edit.setText(const)
        if not self._symbol_edit.text():
            self._symbol_edit.setText(sym)
        if not self._base_edit.text():
            self._base_edit.setText(base)

    def values(self) -> dict:
        return {
            "png_path": self._png_path,
            "frames": self._frames,
            "constant": self._const_edit.text().strip(),
            "symbol": self._symbol_edit.text().strip(),
            "base_name": self._base_edit.text().strip(),
            "coord_size": self._size_spin.value(),
            "coord_y_offset": self._yoff_spin.value(),
            "remapped_img": self._remapped_img,
            "remapped_palette": self._remapped_palette,
        }


# ── Main tab ────────────────────────────────────────────────────────────────

class TrainerBackSpriteTab(QWidget):
    """Back-pic sprite + palette editor with an animated throw preview."""

    modified = pyqtSignal()
    _GRID_COLS = 5

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._project_root = ""
        self._entries: List[backreg.BackPicEntry] = []
        self._by_const: Dict[str, backreg.BackPicEntry] = {}
        self._current: Optional[str] = None
        self._loading = False

        self._palettes: Dict[str, List[Color]] = {}      # const -> 16 colours
        self._palette_dirty: set[str] = set()
        self._sprite_imgs: Dict[str, QImage] = {}         # const -> imported strip
        self._sprite_png_dirty: set[str] = set()
        self._cards: Dict[str, _PicCard] = {}

        self._frame = 0                                   # displayed frame index
        self._throw_sched: List[int] = []                 # per-tick frame indices
        self._play_pos = 0
        self._play_timer = QTimer(self)
        self._play_timer.setInterval(16)                  # ~60 fps (GBA tick)
        self._play_timer.timeout.connect(self._on_play_tick)

        self._build_ui()

    # ── UI ──────────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(8)
        title = QLabel("Trainer Back Sprites")
        title.setStyleSheet("font-size: 14px; font-weight: bold; color: #e0e0e0;")
        header.addWidget(title)
        header.addSpacing(12)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter by name…")
        self._search.setClearButtonEnabled(True)
        self._search.setFixedWidth(200)
        self._search.setStyleSheet(
            "background: #222; border: 1px solid #333; color: #ddd; "
            "padding: 3px 6px; border-radius: 3px;")
        self._search.textChanged.connect(lambda _: self._rebuild_grid())
        header.addWidget(self._search)
        self._add_btn = QPushButton("+ Add Back Sprite…")
        self._add_btn.setToolTip(
            "Register a new trainer back sprite from a PNG strip.\n"
            "Wires up the constant, INCBINs, the three back-pic tables,\n"
            "and a default throw animation — atomically, no hand-editing.")
        self._add_btn.clicked.connect(self._on_add_back_sprite)
        header.addWidget(self._add_btn)
        header.addStretch(1)
        self._count_lbl = QLabel("")
        self._count_lbl.setStyleSheet("color: #777; font-size: 10px;")
        header.addWidget(self._count_lbl)
        outer.addLayout(header)

        body = QSplitter(Qt.Orientation.Horizontal)
        body.setChildrenCollapsible(False)
        body.setHandleWidth(6)

        # LEFT: card grid
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
        self._grid_scroll.setMinimumWidth(380)
        body.addWidget(self._grid_scroll)

        # RIGHT: editor
        right = QVBoxLayout()
        right.setSpacing(10)
        right.setContentsMargins(4, 4, 4, 4)

        sel_header = QHBoxLayout()
        sel_header.setSpacing(6)
        self._sel_lbl = QLabel("(no back sprite selected)")
        self._sel_lbl.setStyleSheet("font-size: 12px; font-weight: bold; color: #ccc;")
        self._sel_lbl.setWordWrap(True)
        sel_header.addWidget(self._sel_lbl, 1)
        self._dirty_dot = QLabel("●")
        self._dirty_dot.setFixedWidth(14)
        self._dirty_dot.setStyleSheet("color: #ffb74d; font-size: 14px; font-weight: bold;")
        self._dirty_dot.hide()
        sel_header.addWidget(self._dirty_dot)
        right.addLayout(sel_header)

        preview_group = QGroupBox("Throw Animation Preview")
        pg = QVBoxLayout(preview_group)
        pg.setContentsMargins(8, 16, 8, 8)
        pg.setSpacing(8)
        self._sprite_lbl = QLabel()
        self._sprite_lbl.setFixedSize(192, 192)
        self._sprite_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sprite_lbl.setStyleSheet("background: #111; border: 1px solid #333;")
        pg.addWidget(self._sprite_lbl, 0, Qt.AlignmentFlag.AlignHCenter)

        ctl = QHBoxLayout()
        ctl.setSpacing(8)
        self._play_btn = QPushButton("▶ Play")
        self._play_btn.setToolTip("Play the wind-up-and-throw animation (loops until stopped).")
        self._play_btn.clicked.connect(self._toggle_play)
        ctl.addWidget(self._play_btn)
        self._frame_slider = QSlider(Qt.Orientation.Horizontal)
        self._frame_slider.setMinimum(0)
        self._frame_slider.setMaximum(0)
        self._frame_slider.setToolTip("Step through the back-pic frames (0 = idle).")
        self._frame_slider.valueChanged.connect(self._on_slider)
        ctl.addWidget(self._frame_slider, 1)
        self._frame_lbl = QLabel("0 / 0")
        self._frame_lbl.setStyleSheet("color: #999; font-size: 10px;")
        self._frame_lbl.setFixedWidth(48)
        ctl.addWidget(self._frame_lbl)
        pg.addLayout(ctl)
        right.addWidget(preview_group)

        self._pal_row = DraggablePaletteRow()
        right.addWidget(self._wrap(
            "Palette  (drag to reorder  ·  right-click for Index as Background)",
            self._pal_row))

        import_group = QGroupBox("Import / Save")
        ig = QVBoxLayout(import_group)
        ig.setContentsMargins(8, 16, 8, 8)
        ig.setSpacing(6)
        self._import_btn = QPushButton("Import PNG Strip as Sprite…")
        self._import_btn.setToolTip(
            "Replace this back sprite's art with a PNG strip (64 wide, height a\n"
            "multiple of 64). Indexed ≤16-colour PNGs are used directly; any\n"
            "other PNG opens the manual palette picker. Saved on File → Save.")
        self._import_btn.clicked.connect(self._import_strip)
        ig.addWidget(self._import_btn)
        self._import_pal_btn = QPushButton("Import .pal File…")
        self._import_pal_btn.setToolTip(
            "Load a JASC .pal's 16 colours into this back sprite's palette.")
        self._import_pal_btn.clicked.connect(self._import_pal)
        ig.addWidget(self._import_pal_btn)
        self._export_pal_btn = QPushButton("Save Palette as .pal…")
        self._export_pal_btn.clicked.connect(self._export_pal)
        ig.addWidget(self._export_pal_btn)
        right.addWidget(import_group)
        right.addStretch(1)

        right_host = QWidget()
        right_host.setLayout(right)
        right_host.setMinimumWidth(440)
        right_host.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        body.addWidget(right_host)
        body.setStretchFactor(0, 3)
        body.setStretchFactor(1, 2)
        body.setSizes([760, 460])
        outer.addWidget(body, 1)

        self._pal_row.colors_changed.connect(self._on_palette_edited)
        self._pal_row.palette_reordered.connect(self._on_palette_reordered)

    def _wrap(self, title: str, inner: QWidget) -> QGroupBox:
        g = QGroupBox(title)
        gl = QVBoxLayout(g)
        gl.setContentsMargins(8, 14, 8, 8)
        gl.addWidget(inner)
        return g

    # ── loading ─────────────────────────────────────────────────────────────
    def load(self, project_root: str) -> None:
        """(Re)load back pics from the project. Called on open / F5 / after add."""
        self._play_timer.stop()
        self._project_root = project_root
        self._entries = backreg.parse_back_pics(project_root) if project_root else []
        self._by_const = {e.constant: e for e in self._entries}
        self._palettes.clear()
        self._palette_dirty.clear()
        self._sprite_imgs.clear()
        self._sprite_png_dirty.clear()
        self._current = None
        self._frame = 0
        self._dirty_dot.hide()
        self._sel_lbl.setText("(no back sprite selected)")
        self._sprite_lbl.clear()
        self._loading = True
        try:
            self._pal_row.set_colors([(0, 0, 0)] * 16)
            self._rebuild_grid()
            if self._entries:
                self._select(self._entries[0].constant)
        finally:
            self._loading = False

    def _rebuild_grid(self) -> None:
        while self._grid_layout.count() > 0:
            item = self._grid_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._cards.clear()
        needle = self._search.text().strip().lower() if hasattr(self, "_search") else ""
        shown = 0
        for i, e in enumerate(self._entries):
            if needle and needle not in (e.constant.lower() + " "
                                         + _friendly_back_name(e.constant).lower()):
                continue
            card = _PicCard(e.constant)
            card._name_lbl.setText(_friendly_back_name(e.constant))
            card.clicked.connect(lambda _=False, k=e.constant: self._select(k))
            card.set_thumbnail(self._frame_pixmap(e.constant, 0))
            card.set_dirty(e.constant in self._palette_dirty
                           or e.constant in self._sprite_png_dirty)
            self._grid_layout.addWidget(card, shown // self._GRID_COLS,
                                        shown % self._GRID_COLS)
            self._cards[e.constant] = card
            shown += 1
        self._count_lbl.setText(f"{shown} / {len(self._entries)} back sprites")

    # ── frame rendering ─────────────────────────────────────────────────────
    def _full_pixmap(self, const: str) -> Optional[QPixmap]:
        """The whole strip, reskinned with the live palette (RAM image if the
        sprite was imported, else the on-disk PNG)."""
        e = self._by_const.get(const)
        if e is None:
            return None
        palette = self._palettes.get(const)
        img = self._sprite_imgs.get(const)
        if img is not None and palette:
            from core.gba_image_utils import _rebuild_color_table
            try:
                return QPixmap.fromImage(_rebuild_color_table(img, palette, 0))
            except Exception:
                pass
        if e.png_path and os.path.isfile(e.png_path):
            if palette:
                pix = _reskin_indexed_png(e.png_path, palette)
                if pix is not None:
                    return pix
            return QPixmap(e.png_path)
        return None

    def _frame_pixmap(self, const: str, frame: int) -> Optional[QPixmap]:
        """One 64x64 frame cropped from the strip (palette-reskinned)."""
        full = self._full_pixmap(const)
        if full is None or full.isNull():
            return None
        y = frame * FRAME_H
        if 0 <= y and y + FRAME_H <= full.height():
            return full.copy(0, y, full.width(), FRAME_H)
        return full.copy(0, 0, full.width(), min(FRAME_H, full.height()))

    def _frame_count(self, const: str) -> int:
        e = self._by_const.get(const)
        return max(1, e.frames) if e else 1

    # ── selection ───────────────────────────────────────────────────────────
    def select_back_pic(self, const: str) -> None:
        if const in self._cards:
            self._select(const)

    def _select(self, const: str) -> None:
        if not const or const not in self._by_const:
            return
        self._play_timer.stop()
        self._play_btn.setText("▶ Play")
        prev = self._current
        if prev and prev in self._cards:
            self._cards[prev].set_selected(False)
        if const in self._cards:
            self._cards[const].set_selected(True)
        self._current = const
        e = self._by_const[const]

        if const not in self._palettes:
            colors = read_jasc_pal(e.pal_path) if e.pal_path else []
            if not colors:
                colors = [(0, 0, 0)] * 16
            self._palettes[const] = colors

        self._loading = True
        try:
            self._pal_row.set_colors(self._palettes[const])
        finally:
            self._loading = False

        # throw schedule: flatten (frame_idx, dur) into a per-tick frame list.
        n = self._frame_count(const)
        sched: List[int] = []
        for idx, dur in (e.throw_anim or []):
            if 0 <= idx < n:
                sched.extend([idx] * max(1, dur))
        if not sched:
            sched = list(range(n)) or [0]
        self._throw_sched = sched
        self._play_pos = 0

        self._frame = 0
        self._loading = True
        try:
            self._frame_slider.setMaximum(max(0, n - 1))
            self._frame_slider.setValue(0)
        finally:
            self._loading = False

        self._sel_lbl.setText(
            f"{_friendly_back_name(const)}  ({const})  ·  {n} frames")
        self._refresh_preview()
        self._dirty_dot.setVisible(
            const in self._palette_dirty or const in self._sprite_png_dirty)

    # ── preview playback ────────────────────────────────────────────────────
    def _refresh_preview(self) -> None:
        if not self._current:
            self._sprite_lbl.clear()
            self._sprite_lbl.setText("No sprite")
            return
        pix = self._frame_pixmap(self._current, self._frame)
        n = self._frame_count(self._current)
        self._frame_lbl.setText(f"{self._frame} / {max(0, n - 1)}")
        if pix is None or pix.isNull():
            self._sprite_lbl.clear()
            self._sprite_lbl.setText("?")
            return
        self._sprite_lbl.setPixmap(pix.scaled(
            self._sprite_lbl.width(), self._sprite_lbl.height(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation))

    def _toggle_play(self) -> None:
        if not self._current:
            return
        if self._play_timer.isActive():
            self._play_timer.stop()
            self._play_btn.setText("▶ Play")
        else:
            self._play_pos = 0
            self._play_timer.start()
            self._play_btn.setText("⏸ Stop")

    def _on_play_tick(self) -> None:
        if not self._current or not self._throw_sched:
            self._play_timer.stop()
            return
        self._play_pos = (self._play_pos + 1) % len(self._throw_sched)
        self._frame = self._throw_sched[self._play_pos]
        self._loading = True                  # move the slider without re-entry
        try:
            self._frame_slider.setValue(self._frame)
        finally:
            self._loading = False
        self._refresh_preview()

    def _on_slider(self, value: int) -> None:
        if self._loading:
            return
        # A manual drag stops playback and pins the frame.
        if self._play_timer.isActive():
            self._play_timer.stop()
            self._play_btn.setText("▶ Play")
        self._frame = int(value)
        self._refresh_preview()

    # ── palette editing ─────────────────────────────────────────────────────
    def _broadcast(self, const: str, colors: List[Color]) -> None:
        bus = _get_palette_bus()
        try:
            bus.set_trainer_palette(const, colors)
        except Exception:
            pass
        e = self._by_const.get(const)
        if e and e.png_path:
            bus.set_palette("trainer_pic", e.png_path, colors)

    def _after_palette_change(self, const: str, colors: List[Color]) -> None:
        self._palettes[const] = colors
        self._palette_dirty.add(const)
        self._broadcast(const, colors)
        self._refresh_preview()
        self._dirty_dot.show()
        card = self._cards.get(const)
        if card is not None:
            card.set_dirty(True)
            card.set_thumbnail(self._frame_pixmap(const, 0))
        if not self._loading:
            self.modified.emit()

    def _on_palette_edited(self) -> None:
        if self._loading or not self._current:
            return
        self._after_palette_change(self._current, self._pal_row.colors())

    def _on_palette_reordered(self, from_idx: int, to_idx: int) -> None:
        if self._loading or not self._current:
            return
        if from_idx == to_idx or not (0 <= from_idx < 16) or not (0 <= to_idx < 16):
            return
        pal = list(self._palettes.get(self._current) or [(0, 0, 0)] * 16)
        while len(pal) < 16:
            pal.append((0, 0, 0))
        pal[from_idx], pal[to_idx] = pal[to_idx], pal[from_idx]
        self._loading = True
        try:
            self._pal_row.set_colors(pal)
        finally:
            self._loading = False
        self._after_palette_change(self._current, pal)

    # ── import / export ─────────────────────────────────────────────────────
    def _import_strip(self) -> None:
        if not self._current:
            QMessageBox.information(self, "No Back Sprite",
                                    "Select a back sprite first.")
            return
        e = self._by_const[self._current]
        start = os.path.dirname(e.png_path) if e.png_path else (self._project_root or "")
        path, _ = QFileDialog.getOpenFileName(
            self, "Select PNG Strip", start, "PNG Images (*.png)")
        if not path:
            return
        img = QImage(path)
        if img.isNull():
            QMessageBox.warning(self, "Import Failed", f"Could not load:\n{path}")
            return
        if img.width() != FRAME_H or img.height() % FRAME_H != 0 or img.height() < FRAME_H:
            QMessageBox.warning(
                self, "Wrong Size",
                f"A back-pic strip must be {FRAME_H}px wide and a multiple of "
                f"{FRAME_H}px tall.\nThis image is {img.width()}x{img.height()}.")
            return
        colors: List[Color] = []
        ct = img.colorTable() if img.format() == QImage.Format.Format_Indexed8 else []
        if img.format() == QImage.Format.Format_Indexed8 and ct and len(set(ct)) <= 16:
            for entry in ct[:16]:
                colors.append(clamp_to_gba((entry >> 16) & 0xFF,
                                           (entry >> 8) & 0xFF, entry & 0xFF))
            while len(colors) < 16:
                colors.append((0, 0, 0))
        else:
            from ui.dialogs.manual_palette_pick_dialog import import_image_manually_from_path
            result = import_image_manually_from_path(path, target_colors=16, parent=self)
            if result is None:
                return
            colors, img = result
            colors = list(colors)

        sp = self._current
        new_frames = img.height() // FRAME_H
        self._sprite_imgs[sp] = img
        self._palettes[sp] = colors
        self._sprite_png_dirty.add(sp)
        self._palette_dirty.add(sp)
        # frame count may have changed with the new strip — update the entry.
        e.frames = new_frames
        self._broadcast(sp, colors)
        self._loading = True
        try:
            self._pal_row.set_colors(colors)
            self._frame_slider.setMaximum(max(0, new_frames - 1))
            self._frame_slider.setValue(0)
        finally:
            self._loading = False
        self._frame = 0
        self._refresh_preview()
        self._dirty_dot.show()
        card = self._cards.get(sp)
        if card is not None:
            card.set_dirty(True)
            card.set_thumbnail(self._frame_pixmap(sp, 0))
        self.modified.emit()
        QMessageBox.information(
            self, "Sprite Imported",
            f"Loaded {new_frames}-frame strip for {_friendly_back_name(sp)}.\n"
            "Use Play to preview the throw. Click File → Save to write it "
            "to disk.\n\nNote: the throw timing comes from the move's existing "
            "anim table; if your new strip has a different frame count the "
            "animation may need its timings adjusted in back_pic_anims.h.")

    def _import_pal(self) -> None:
        if not self._current:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Select .pal", self._project_root or "", "JASC Palette (*.pal)")
        if not path:
            return
        colors = read_jasc_pal(path)
        if not colors:
            QMessageBox.warning(self, "Import Failed", "Could not read .pal.")
            return
        while len(colors) < 16:
            colors.append((0, 0, 0))
        self._loading = True
        try:
            self._pal_row.set_colors(colors[:16])
        finally:
            self._loading = False
        self._after_palette_change(self._current, colors[:16])

    def _export_pal(self) -> None:
        if not self._current:
            return
        colors = self._palettes.get(self._current)
        if not colors:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save .pal", f"{self._current.lower()}.pal", "JASC Palette (*.pal)")
        if path:
            write_jasc_pal(path, colors)

    # ── add new back sprite ─────────────────────────────────────────────────
    def _on_add_back_sprite(self) -> None:
        if not self._project_root:
            QMessageBox.information(self, "No Project", "Open a project first.")
            return
        dlg = _AddBackPicDialog(self._project_root, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        v = dlg.values()
        if not v["png_path"] or v["frames"] < 1:
            QMessageBox.warning(self, "Missing PNG", "Pick a valid PNG strip first.")
            return
        if not v["constant"].startswith("TRAINER_BACK_PIC_"):
            QMessageBox.warning(self, "Bad Constant",
                                "Constant must start with TRAINER_BACK_PIC_.")
            return
        if not v["symbol"] or not v["base_name"]:
            QMessageBox.warning(self, "Bad Name", "C symbol / base filename required.")
            return

        import tempfile
        src = v["png_path"]
        tmp = ""
        if v["remapped_img"] is not None and v["remapped_palette"]:
            fd, tmp = tempfile.mkstemp(suffix=".png", prefix="porysuite_backpic_")
            os.close(fd)
            if not export_indexed_png(v["remapped_img"], v["remapped_palette"], tmp,
                                      transparent_index=0):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                QMessageBox.critical(self, "Add Failed",
                                     "Could not write the remapped image.")
                return
            src = tmp
        try:
            res = backreg.add_back_pic(
                project_root=self._project_root, source_png_path=src,
                constant=v["constant"], symbol=v["symbol"], base_name=v["base_name"],
                frames=v["frames"], coord_size=v["coord_size"],
                coord_y_offset=v["coord_y_offset"])
        finally:
            if tmp:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        if not res.success:
            QMessageBox.critical(self, "Add Back Sprite Failed", res.error)
            return
        self.load(self._project_root)
        if v["constant"] in self._by_const:
            self._select(v["constant"])
        self.modified.emit()
        QMessageBox.information(
            self, "Back Sprite Added",
            f"{v['constant']} (id {res.pic_id}, {res.frames} frames) registered.\n\n"
            "Files modified:\n"
            f"  • graphics/trainers/back_pics/{v['base_name']}_back_pic.png\n"
            f"  • graphics/trainers/palettes/{v['base_name']}_back_pic.pal\n"
            "  • src/data/graphics/trainers.h\n"
            "  • src/data/trainer_graphics/back_pic_tables.h\n"
            "  • src/data/trainer_graphics/back_pic_anims.h\n"
            "  • include/constants/trainers.h\n\n"
            "Build the project (Make / Make Modern) to compile the new entries.")

    # ── save ─────────────────────────────────────────────────────────────────
    def has_unsaved_changes(self) -> bool:
        return bool(self._palette_dirty) or bool(self._sprite_png_dirty)

    def flush_to_disk(self) -> tuple[int, list[str]]:
        """Write dirty palettes (.pal) and imported strips (PNG). Mirrors the
        front Graphics tab: bake the live palette into the PNG's colour table so
        the on-disk strip and the .pal stay in sync."""
        ok = 0
        errors: list[str] = []
        for const in list(self._palette_dirty):
            e = self._by_const.get(const)
            if e is None or not e.pal_path:
                errors.append(f"backpic-pal:{const} (no path)")
                continue
            colors = self._palettes.get(const)
            if not (colors and write_jasc_pal(e.pal_path, colors)):
                errors.append(f"backpic-pal:{const}")
                continue
            ok += 1
            # bake into the PNG
            img = self._sprite_imgs.get(const)
            if (img is None or img.format() != QImage.Format.Format_Indexed8) \
                    and e.png_path and os.path.isfile(e.png_path):
                disk = QImage(e.png_path)
                if not disk.isNull():
                    img = (disk if disk.format() == QImage.Format.Format_Indexed8
                           else disk.convertToFormat(QImage.Format.Format_Indexed8))
            if img is not None and not img.isNull() \
                    and img.format() == QImage.Format.Format_Indexed8 and e.png_path:
                try:
                    if export_indexed_png(img, colors, e.png_path, transparent_index=0):
                        self._sprite_png_dirty.discard(const)
                    else:
                        errors.append(f"backpic-png-bake:{const}")
                except Exception as exc:
                    errors.append(f"backpic-png-bake:{const} ({exc})")
            self._palette_dirty.discard(const)

        for const in list(self._sprite_png_dirty):
            e = self._by_const.get(const)
            img = self._sprite_imgs.get(const)
            pal = self._palettes.get(const)
            if e is None or not e.png_path or img is None or not pal:
                errors.append(f"backpic-png:{const}")
                continue
            try:
                if export_indexed_png(img, pal, e.png_path, transparent_index=0):
                    ok += 1
                    self._sprite_png_dirty.discard(const)
                else:
                    errors.append(f"backpic-png:{const}")
            except Exception as exc:
                errors.append(f"backpic-png:{const} ({exc})")

        for const, card in self._cards.items():
            card.set_dirty(const in self._palette_dirty or const in self._sprite_png_dirty)
        if self._current is not None:
            self._dirty_dot.setVisible(
                self._current in self._palette_dirty
                or self._current in self._sprite_png_dirty)
        else:
            self._dirty_dot.hide()
        return ok, errors
