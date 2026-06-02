"""Battle Animations tab — Phase 1 (sprite browse + palette editing).

Layout (mirrors the Overworld Graphics tab, with an internal sub-tab
shell so the timeline editor can slot in later):

    Battle Anims
    ├── Sprites          (built — this phase)
    │     left   : search + reflowing thumbnail grid
    │     center : battle-scene preview (BG + mons + the anim sprite,
    │              frame-cycled) + raw sheet view + frame info
    │     right  : palette editor (swatch row + Import from PNG /
    │              Manually / .pal) with the correct 16-colour count
    └── Move Animations  (stub — Phase 3+: the timeline editor)

Everything routes through the shared sprite pipeline:
``core.battle_anim_data`` for the model, ``core.sprite_render`` for
rendering, and ``core.sprite_palette_bus`` (``CAT_BATTLE_ANIM``) for
RAM-first palettes + cross-tab propagation.  Compression is build-side:
the editor reads/writes the uncompressed ``.png`` + ``.pal`` / ``.gbapal``
source; gbagfx rebuilds the ``.lz`` artefacts.

Dirty/F5 contract (per project rules): ``load()`` fully resets in-memory
AND visual dirty state; per-sprite amber card borders (Pattern B) plus an
amber palette groupbox frame (Pattern C) both track ``_palette_dirty``.
"""

from __future__ import annotations

import os
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import Qt, QObject, QEvent, QTimer, QSize, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPixmap
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QGroupBox, QScrollArea, QGridLayout, QFrame, QSplitter, QSizePolicy,
    QTabWidget, QMessageBox, QFileDialog, QListWidget, QListWidgetItem,
    QComboBox, QSpinBox, QDialog, QDialogButtonBox, QFormLayout,
)

from core.battle_anim_data import parse_battle_anim_sprites, BattleAnimSprite
from core.battle_anim_script import (
    parse_move_anim_table, parse_anim_scripts, resolve_timeline,
    parse_move_names, move_display_name, parse_sound_effects,
    rewrite_script_command, format_command,
    KIND_SOUND, KIND_SPRITE, KIND_TASK, KIND_DELAY, KIND_GFX, KIND_CONTROL,
)
from core.sprite_render import load_sprite_pixmap
from core.sprite_palette_bus import get_bus as _get_palette_bus, CAT_BATTLE_ANIM
from core.overworld_palette_io import write_palette_pair
from ui.draggable_palette_row import DraggablePaletteRow
from ui.palette_utils import read_jasc_pal, clamp_to_gba

Color = Tuple[int, int, int]

_DIRTY_SS = "QGroupBox { border: 1px solid #ffb74d; border-radius: 4px; margin-top: 6px; padding-top: 10px; }"
_CARD_W, _CARD_H = 76, 80

# Timeline row styling per command kind: (glyph, hex colour).
_KIND_STYLE = {
    KIND_SOUND:   ("\U0001F50A", "#e8a44a"),   # 🔊 amber
    KIND_SPRITE:  ("✨",     "#5fd0e0"),    # ✨ cyan
    KIND_TASK:    ("⚙",     "#b08cff"),    # ⚙ purple
    KIND_DELAY:   ("⏱",     "#888888"),    # ⏱ grey
    KIND_GFX:     ("\U0001F5BC", "#7cbb5e"),    # 🖼 green
    KIND_CONTROL: ("▸",     "#9a9a9a"),    # ▸ dim
}
_KIND_DEFAULT = ("·", "#777777")           # · default

# Sound-preview hooks — set by unified_mainwindow.setup_pages() so the
# sound edit dialog can audition an SE_* through the Sound Editor (the
# same module-callback pattern EVENTide's playse ▶ button uses).  Left
# None in standalone contexts (the ▶ button then no-ops gracefully).
_preview_sound_cb = None   # Callable[[str], bool]  — constant -> play it
_stop_sound_cb = None      # Callable[[], None]      — stop any preview

# Item role holding the SE_* constant on any sound row (used by the
# timeline "▶ Preview Sound" button — works on read-only inlined rows
# too, since previewing doesn't edit).
_SOUND_ROLE = Qt.ItemDataRole.UserRole + 1


# ───────────────────────────────────────── grid reflow event filter ──

class _GridResizeFilter(QObject):
    """Reflow the sprite grid's columns when its scroll viewport width
    changes (vertical-only scroll, no horizontal scrollbar) — same
    pattern the Overworld GFX grid uses."""

    def __init__(self, tab: "BattleAnimTab"):
        super().__init__(tab)
        self._tab = tab

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Resize:
            w = obj.width()
            if abs(w - self._tab._grid_last_width) > 2:
                self._tab._grid_last_width = w
                self._tab._grid_refresh_timer.start(150)
        return False


# ─────────────────────────────────────────────────── sprite card ──

class _AnimCard(QWidget):
    """Clickable thumbnail card with its own dirty (amber) border —
    Pattern B.  Selected + dirty are independent; a selected dirty card
    shows amber, not blue."""

    clicked = pyqtSignal(str)  # emits the sprite tag

    def __init__(self, tag: str, name: str, pix: Optional[QPixmap]):
        super().__init__()
        self._tag = tag
        self._selected = False
        self._dirty = False
        self.setFixedSize(_CARD_W, _CARD_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        v = QVBoxLayout(self)
        v.setContentsMargins(2, 2, 2, 2)
        v.setSpacing(1)
        self._thumb = QLabel()
        self._thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb.setFixedHeight(52)
        if pix is not None and not pix.isNull():
            scaled = pix.scaled(48, 48, Qt.AspectRatioMode.KeepAspectRatio,
                                Qt.TransformationMode.FastTransformation)
            self._thumb.setPixmap(scaled)
        v.addWidget(self._thumb)
        lbl = QLabel(name)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setWordWrap(True)
        lbl.setStyleSheet("font-size: 9px; color: #ccc;")
        v.addWidget(lbl)
        self._restyle()

    def _restyle(self):
        if self._dirty:
            border = "#ffb74d"
        elif self._selected:
            border = "#1565c0"
        else:
            border = "#333"
        self.setStyleSheet(
            f"QWidget {{ background: #222; border: 1px solid {border}; "
            f"border-radius: 3px; }} QWidget:hover {{ border-color: #1565c0; }}"
        )

    def set_selected(self, sel: bool):
        self._selected = bool(sel)
        self._restyle()

    def set_dirty(self, dirty: bool):
        self._dirty = bool(dirty)
        self._restyle()

    def set_thumbnail(self, pix: Optional[QPixmap]):
        if pix is not None and not pix.isNull():
            self._thumb.setPixmap(pix.scaled(
                48, 48, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation))

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._tag)


# ─────────────────────────────────────────────────────── the tab ──

class BattleAnimTab(QWidget):
    """Top-level Battle Animations tab."""

    modified = pyqtSignal()

    def __init__(self, res_dir: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._res_dir = res_dir
        self._project_root: Optional[str] = None
        self._loading = False

        # Model + per-sprite state.
        self._sprites: Dict[str, BattleAnimSprite] = {}      # tag -> sprite
        self._order: List[str] = []                          # tags, sorted by name
        self._cards: Dict[str, _AnimCard] = {}               # tag -> card
        self._palettes: Dict[str, List[Color]] = {}          # tag -> live colours
        self._palette_dirty: set = set()                     # tags with unsaved palette
        self._current: Optional[str] = None                  # selected tag
        self._gfx_groups: Dict[str, List[str]] = {}          # gfx_symbol -> [tags] sharing the image

        # Move-animation (timeline) state.
        self._move_table: List[str] = []                    # script labels by move idx
        self._scripts: Dict[str, list] = {}                 # label -> [Command]
        self._move_names: Dict[str, str] = {}               # MOVE_CONST -> project name
        self._move_current: Optional[str] = None            # selected script label
        self._scripts_text: str = ""                        # raw battle_anim_scripts.s
        self._scripts_dirty: bool = False                   # unsaved timeline edits?
        self._sound_effects: List[str] = []                 # SE_* constants for picker

        # Frame-cycle preview state.
        self._frames: List[QPixmap] = []
        self._frame_idx = 0
        self._frame_timer = QTimer(self)
        self._frame_timer.setInterval(160)
        self._frame_timer.timeout.connect(self._advance_frame)

        # Grid reflow plumbing.
        self._grid_last_width = 0
        self._grid_refresh_timer = QTimer(self)
        self._grid_refresh_timer.setSingleShot(True)
        self._grid_refresh_timer.timeout.connect(self._rebuild_grid)

        self._build_ui()

    # ── UI construction ──────────────────────────────────────────────

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._subtabs = QTabWidget()
        outer.addWidget(self._subtabs)
        self._subtabs.addTab(self._build_sprites_tab(), "Sprites")
        self._subtabs.addTab(self._build_move_anim_tab(), "Move Animations")

    def _build_move_anim_tab(self) -> QWidget:
        page = QWidget()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(6, 6, 6, 6)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        pl.addWidget(splitter, 1)

        # ── LEFT: searchable move list ──
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        self._move_search = QLineEdit()
        self._move_search.setPlaceholderText("Search moves…")
        self._move_search.textChanged.connect(lambda _t: self._rebuild_move_list())
        lv.addWidget(self._move_search)
        self._move_count_lbl = QLabel("")
        self._move_count_lbl.setStyleSheet("color: #888; font-size: 10px;")
        lv.addWidget(self._move_count_lbl)
        self._move_list = QListWidget()
        self._move_list.setStyleSheet(
            "QListWidget { background: #191919; border: none; }"
            "QListWidget::item:selected { background: #1565c0; }")
        self._move_list.currentItemChanged.connect(self._on_move_selected)
        lv.addWidget(self._move_list, 1)
        splitter.addWidget(left)

        # ── CENTER: battle-scene preview (own instance) ──
        center = QWidget()
        cv = QVBoxLayout(center)
        prev_box = QGroupBox("Battle Scene Preview")
        pv = QVBoxLayout(prev_box)
        from ui.graphics_tab_widget import BattleScenePreview
        self._move_preview = BattleScenePreview(self._res_dir)
        pv.addWidget(self._move_preview, 0, Qt.AlignmentFlag.AlignHCenter)
        self._move_prev_lbl = QLabel("Select a move")
        self._move_prev_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._move_prev_lbl.setStyleSheet("color: #aaa; font-size: 11px;")
        pv.addWidget(self._move_prev_lbl)
        cv.addWidget(prev_box)
        cv.addStretch(1)
        splitter.addWidget(center)

        # ── RIGHT: timeline ──
        right = QWidget()
        rv = QVBoxLayout(right)
        tl_box = QGroupBox("Animation Timeline")
        tv = QVBoxLayout(tl_box)
        self._tl_header = QLabel(
            "Select a move to see its animation script — sounds, sprite "
            "spawns, delays, and visual effects in play order.")
        self._tl_header.setWordWrap(True)
        self._tl_header.setStyleSheet("color: #aaa; font-size: 11px;")
        tv.addWidget(self._tl_header)
        self._timeline = QListWidget()
        self._timeline.setStyleSheet(
            "QListWidget { background: #141414; border: 1px solid #2e2e2e; "
            "font-family: Consolas, monospace; font-size: 11px; }")
        self._timeline.itemDoubleClicked.connect(self._on_timeline_double_click)
        tv.addWidget(self._timeline, 1)
        # Preview the selected sound row through the Sound Editor.
        tl_btn_row = QHBoxLayout()
        self._tl_play_btn = QPushButton("▶  Preview Sound")
        self._tl_play_btn.setToolTip(
            "Play the selected sound row's effect through the Sound Editor.")
        self._tl_play_btn.clicked.connect(self._preview_selected_sound)
        self._tl_stop_btn = QPushButton("⏹")
        self._tl_stop_btn.setFixedWidth(36)
        self._tl_stop_btn.setToolTip("Stop preview")
        self._tl_stop_btn.clicked.connect(
            lambda: _stop_sound_cb and _stop_sound_cb())
        tl_btn_row.addWidget(self._tl_play_btn)
        tl_btn_row.addWidget(self._tl_stop_btn)
        tl_btn_row.addStretch(1)
        tv.addLayout(tl_btn_row)
        rv.addWidget(tl_box)
        splitter.addWidget(right)

        splitter.setSizes([280, 420, 360])
        return page

    def _build_sprites_tab(self) -> QWidget:
        page = QWidget()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(6, 6, 6, 6)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        pl.addWidget(splitter, 1)

        # ── LEFT: search + reflowing grid ──
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search battle-anim sprites…")
        self._search.textChanged.connect(lambda _t: self._rebuild_grid())
        lv.addWidget(self._search)
        self._count_lbl = QLabel("")
        self._count_lbl.setStyleSheet("color: #888; font-size: 10px;")
        lv.addWidget(self._count_lbl)

        self._grid_scroll = QScrollArea()
        self._grid_scroll.setWidgetResizable(True)
        self._grid_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._grid_scroll.setStyleSheet("background: #1a1a1a;")
        self._grid_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._grid_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._grid_container = QWidget()
        self._grid_layout = QGridLayout(self._grid_container)
        self._grid_layout.setContentsMargins(4, 4, 4, 4)
        self._grid_layout.setSpacing(6)
        self._grid_scroll.setWidget(self._grid_container)
        lv.addWidget(self._grid_scroll, 1)
        self._grid_scroll.viewport().installEventFilter(
            _GridResizeFilter(self))
        splitter.addWidget(left)

        # ── CENTER: battle-scene preview + sheet + frame info ──
        center = QWidget()
        cv = QVBoxLayout(center)
        prev_box = QGroupBox("Battle Scene Preview")
        pv = QVBoxLayout(prev_box)
        # Imported here (not at module top) so a headless import of this
        # module doesn't drag in the Pokemon graphics tab eagerly.
        from ui.graphics_tab_widget import BattleScenePreview
        self._preview = BattleScenePreview(self._res_dir)
        pv.addWidget(self._preview, 0, Qt.AlignmentFlag.AlignHCenter)
        self._frame_lbl = QLabel("Select a sprite")
        self._frame_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._frame_lbl.setStyleSheet("color: #aaa; font-size: 11px;")
        pv.addWidget(self._frame_lbl)
        cv.addWidget(prev_box)

        sheet_box = QGroupBox("Sprite Sheet")
        sv = QVBoxLayout(sheet_box)
        self._sheet_lbl = QLabel("Select a sprite")
        self._sheet_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sheet_lbl.setStyleSheet("background: #111; padding: 6px;")
        self._sheet_lbl.setMinimumHeight(80)
        sv.addWidget(self._sheet_lbl)
        self._info_lbl = QLabel("")
        self._info_lbl.setStyleSheet("color: #888; font-size: 10px;")
        self._info_lbl.setWordWrap(True)
        sv.addWidget(self._info_lbl)
        # Shared-image signifier: when this sprite's PNG is used by other
        # sprites (same gfx, different palette), show them as hop links.
        self._shared_lbl = QLabel("")
        self._shared_lbl.setWordWrap(True)
        self._shared_lbl.setStyleSheet(
            "QLabel { color: #e8a44a; font-size: 10px; background: #2a2410; "
            "border: 1px solid #5a4a1a; border-radius: 3px; padding: 4px; }")
        self._shared_lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self._shared_lbl.linkActivated.connect(self._on_shared_link)
        self._shared_lbl.setVisible(False)
        sv.addWidget(self._shared_lbl)
        # Sprite-level actions: open the PNG's folder, or replace the image.
        sheet_btn_row = QHBoxLayout()
        self._show_folder_btn = QPushButton("Show in Folder")
        self._show_folder_btn.setToolTip(
            "Open the folder containing this sprite's PNG, with the file selected.")
        self._show_folder_btn.clicked.connect(self._show_in_folder)
        self._replace_img_btn = QPushButton("Replace Image…")
        self._replace_img_btn.setToolTip(
            "Replace this sprite's PNG with a new image (e.g. one exported from\n"
            "the Overworld editor).  Opens the manual colour picker to index it\n"
            "to 16 colours, writes the new PNG, and loads its palette.  The\n"
            "build recompresses the .4bpp.lz automatically.")
        self._replace_img_btn.clicked.connect(self._replace_image)
        for b in (self._show_folder_btn, self._replace_img_btn):
            b.setEnabled(False)
            sheet_btn_row.addWidget(b)
        sheet_btn_row.addStretch(1)
        sv.addLayout(sheet_btn_row)
        cv.addWidget(sheet_box)
        cv.addStretch(1)
        splitter.addWidget(center)

        # ── RIGHT: palette editor ──
        right = QWidget()
        rv = QVBoxLayout(right)
        self._pal_frame = QGroupBox("Palette")
        pf = QVBoxLayout(self._pal_frame)
        self._pal_info = QLabel("Select a sprite to view its palette")
        self._pal_info.setStyleSheet("color: #aaa; font-size: 11px;")
        self._pal_info.setWordWrap(True)
        pf.addWidget(self._pal_info)
        self._pal_row = DraggablePaletteRow()
        self._pal_row.colors_changed.connect(self._on_palette_edited)
        pf.addWidget(self._pal_row)

        btn_row = QHBoxLayout()
        self._imp_png_btn = QPushButton("Import Palette from PNG…")
        self._imp_png_btn.clicked.connect(lambda: self._import_palette(False))
        self._imp_manual_btn = QPushButton("Import Manually…")
        self._imp_manual_btn.clicked.connect(lambda: self._import_palette(True))
        self._imp_pal_btn = QPushButton("Import from .pal…")
        self._imp_pal_btn.clicked.connect(self._import_pal_file)
        for b in (self._imp_png_btn, self._imp_manual_btn, self._imp_pal_btn):
            b.setEnabled(False)
            btn_row.addWidget(b)
        pf.addLayout(btn_row)
        rv.addWidget(self._pal_frame)
        rv.addStretch(1)
        splitter.addWidget(right)

        splitter.setSizes([300, 420, 300])
        return page

    # ── load / F5 reset contract ─────────────────────────────────────

    def load(self, project_root: str):
        """(Re)load from disk. Fully resets in-memory AND visual dirty
        state — F5 calls this, so it must leave nothing stale."""
        self._grid_refresh_timer.stop()
        self._frame_timer.stop()
        self._project_root = project_root

        # In-memory reset.
        self._palettes.clear()
        self._palette_dirty.clear()
        self._cards.clear()
        self._current = None
        self._frames = []
        self._frame_idx = 0
        self._move_current = None

        # Parse model.
        try:
            sprites = parse_battle_anim_sprites(project_root)
        except Exception:
            sprites = []
        self._sprites = {s.tag: s for s in sprites}
        self._order = [s.tag for s in sorted(
            sprites, key=lambda s: (s.display_name.lower(), s.tag))]

        # Group sprites that SHARE an image (same gfx symbol = same PNG).
        # Replacing the image of one hits them all; the detail panel
        # surfaces the group + lets the user hop between them.
        groups: Dict[str, List[str]] = defaultdict(list)
        for s in sprites:
            groups[s.gfx_symbol].append(s.tag)
        # Sort each group's tags by display name for stable hop links.
        self._gfx_groups = {
            g: sorted(tags, key=lambda t: self._sprites[t].display_name.lower())
            for g, tags in groups.items()
        }

        # Parse move-animation table + scripts + the project's move names,
        # and hold the raw script file text for in-place edits.
        self._scripts_dirty = False
        try:
            self._move_table = parse_move_anim_table(project_root)
            self._scripts = parse_anim_scripts(project_root)
            self._move_names = parse_move_names(project_root)
            self._sound_effects = parse_sound_effects(project_root)
            self._scripts_text = self._read_scripts_text(project_root)
        except Exception:
            self._move_table, self._scripts, self._move_names = [], {}, {}
            self._sound_effects, self._scripts_text = [], ""

        # Visual reset of the detail/right panel.
        self._pal_frame.setStyleSheet("")
        self._pal_info.setText("Select a sprite to view its palette")
        self._loading = True
        try:
            self._pal_row.set_colors([(0, 0, 0)] * 16)
        finally:
            self._loading = False
        self._sheet_lbl.clear()
        self._sheet_lbl.setText("Select a sprite")
        self._info_lbl.setText("")
        self._shared_lbl.setVisible(False)
        self._shared_lbl.clear()
        self._frame_lbl.setText("Select a sprite")
        self._preview.set_anim_pixmap(None)
        for b in (self._imp_png_btn, self._imp_manual_btn, self._imp_pal_btn,
                  self._show_folder_btn, self._replace_img_btn):
            b.setEnabled(False)

        self._loading = True
        try:
            self._rebuild_grid()
        finally:
            self._loading = False

        # Reset + rebuild the Move Animations sub-tab.
        self._move_preview.set_anim_pixmap(None)
        self._move_prev_lbl.setText("Select a move")
        # Sound preview is available only when the Sound Editor wired the
        # callback (unified launcher).  In standalone contexts it's None.
        _have_preview = _preview_sound_cb is not None
        self._tl_play_btn.setEnabled(_have_preview)
        self._tl_stop_btn.setEnabled(_have_preview)
        if not _have_preview:
            self._tl_play_btn.setToolTip(
                "Sound preview unavailable (Sound Editor not loaded).")
        self._tl_header.setText(
            "Select a move to see its animation script — sounds, sprite "
            "spawns, delays, and visual effects in play order.")
        self._timeline.clear()
        self._rebuild_move_list()

    # ── grid ─────────────────────────────────────────────────────────

    def _rebuild_grid(self):
        # Clear existing.
        while self._grid_layout.count():
            item = self._grid_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._cards.clear()

        needle = self._search.text().strip().lower()
        tags = self._order
        if needle:
            tags = [t for t in tags
                    if needle in self._sprites[t].display_name.lower()
                    or needle in t.lower()]
        self._count_lbl.setText(f"{len(tags)} sprites")

        col_count = max(1, (self._grid_scroll.viewport().width() - 20)
                        // (_CARD_W + 6))
        row = col = 0
        for tag in tags:
            sprite = self._sprites[tag]
            pix = self._thumb_pixmap(sprite)
            card = _AnimCard(tag, sprite.display_name, pix)
            card.clicked.connect(self._on_card_clicked)
            if tag == self._current:
                card.set_selected(True)
            if tag in self._palette_dirty:
                card.set_dirty(True)
            self._cards[tag] = card
            self._grid_layout.addWidget(card, row, col)
            col += 1
            if col >= col_count:
                col = 0
                row += 1
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding,
                             QSizePolicy.Policy.Expanding)
        self._grid_layout.addWidget(spacer, row + 1, 0, 1, col_count)

    def _thumb_pixmap(self, sprite: BattleAnimSprite) -> Optional[QPixmap]:
        if not sprite.png_exists:
            return None
        pal = self._palette_for(sprite)
        frames = self._slice_frames(sprite, pal)
        return frames[0] if frames else None

    # ── selection + detail ───────────────────────────────────────────

    def _on_card_clicked(self, tag: str):
        if self._current and self._current in self._cards:
            self._cards[self._current].set_selected(False)
        self._current = tag
        if tag in self._cards:
            self._cards[tag].set_selected(True)
        self._show_detail(tag)

    def _on_shared_link(self, tag: str):
        """Hop to another sprite that shares the current image."""
        self._select_sprite_by_tag(tag)

    def _select_sprite_by_tag(self, tag: str):
        """Select a sprite by tag, clearing the search filter first so its
        card is guaranteed present, then scrolling to + highlighting it."""
        if tag not in self._sprites:
            return
        if self._search.text():
            self._loading = True
            try:
                self._search.clear()
            finally:
                self._loading = False
            self._rebuild_grid()
        if self._current and self._current in self._cards:
            self._cards[self._current].set_selected(False)
        self._current = tag
        card = self._cards.get(tag)
        if card is not None:
            card.set_selected(True)
            self._grid_scroll.ensureWidgetVisible(card)
        self._show_detail(tag)

    def _show_detail(self, tag: str):
        sprite = self._sprites.get(tag)
        if sprite is None:
            return
        pal = self._palette_for(sprite)

        self._loading = True
        try:
            self._pal_row.set_colors(pal + [(0, 0, 0)] * (16 - len(pal)))
        finally:
            self._loading = False

        # Palette frame amber reflects this sprite's dirty state.
        self._pal_frame.setStyleSheet(
            _DIRTY_SS if tag in self._palette_dirty else "")
        self._pal_info.setText(
            f"{sprite.display_name}   ({sprite.tag})\n"
            f"Palette: {os.path.basename(sprite.pal_path) or '(shared / from PNG)'}"
        )
        for b in (self._imp_png_btn, self._imp_manual_btn, self._imp_pal_btn,
                  self._show_folder_btn, self._replace_img_btn):
            b.setEnabled(True)

        # Shared-image signifier + hop links.
        others = [t for t in self._gfx_groups.get(sprite.gfx_symbol, [])
                  if t != tag]
        if others:
            links = "  ".join(
                f'<a href="{t}" style="color:#ffd27a;">'
                f'{self._sprites[t].display_name}</a>'
                for t in others)
            self._shared_lbl.setText(
                f"🔗 <b>Shared image</b> ({len(others) + 1} sprites use "
                f"<code>{sprite.gfx_symbol.replace('gBattleAnimSpriteGfx_','')}</code>, "
                f"each with its own palette). Editing the image affects all of "
                f"them. Jump to: {links}")
            self._shared_lbl.setVisible(True)
        else:
            self._shared_lbl.clear()
            self._shared_lbl.setVisible(False)

        # Sheet view.
        if sprite.png_exists:
            sheet = load_sprite_pixmap(sprite.png_path, pal)
            if sheet and not sheet.isNull():
                scaled = sheet.scaled(
                    sheet.width() * 2, sheet.height() * 2,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.FastTransformation)
                self._sheet_lbl.setPixmap(scaled)
            img = QImage(sprite.png_path)
            self._info_lbl.setText(
                f"PNG: {os.path.basename(sprite.png_path)}  ·  "
                f"{img.width()}×{img.height()}px  ·  "
                f"VRAM {sprite.vram_size} bytes  ·  gfx {sprite.gfx_symbol}")
        else:
            self._sheet_lbl.clear()
            self._sheet_lbl.setText("PNG not found on disk")
            self._info_lbl.setText(
                f"Expected: {sprite.png_path}\n(build the project once, "
                f"or the gfx is generated rather than a PNG)")

        # Frame-cycle preview on the battle scene.
        self._frames = self._slice_frames(sprite, pal)
        self._frame_idx = 0
        if self._frames:
            self._preview.set_anim_pixmap(self._frames[0])
            n = len(self._frames)
            self._frame_lbl.setText(
                f"{n} frame{'s' if n != 1 else ''} (approx)"
                + ("  ·  cycling" if n > 1 else ""))
            if n > 1:
                self._frame_timer.start()
            else:
                self._frame_timer.stop()
        else:
            self._preview.set_anim_pixmap(None)
            self._frame_lbl.setText("No preview available")
            self._frame_timer.stop()

    def _advance_frame(self):
        if not self._frames:
            return
        self._frame_idx = (self._frame_idx + 1) % len(self._frames)
        self._preview.set_anim_pixmap(self._frames[self._frame_idx])

    # ── palette resolution + frame slicing ───────────────────────────

    def _palette_for(self, sprite: BattleAnimSprite) -> List[Color]:
        """RAM (live edit) → bus (disk) → PNG embedded table."""
        if sprite.tag in self._palettes:
            return self._palettes[sprite.tag]
        bus_pal = _get_palette_bus().ensure_battle_anim_palette(
            sprite.tag, sprite.pal_path)
        if bus_pal:
            return bus_pal
        # Fall back to the PNG's own colour table.
        if sprite.png_exists:
            img = QImage(sprite.png_path)
            ct = img.colorTable()
            if ct:
                cols = [clamp_to_gba((c >> 16) & 0xFF, (c >> 8) & 0xFF,
                                     c & 0xFF) for c in ct[:16]]
                while len(cols) < 16:
                    cols.append((0, 0, 0))
                return cols
        return [(0, 0, 0)] * 16

    def _slice_frames(self, sprite: BattleAnimSprite,
                      palette: List[Color]) -> List[QPixmap]:
        """Render the sheet through *palette*, then slice into frames.

        Approximate (Phase 1): infer square frames from the sheet's
        aspect — a horizontal strip of N square frames, a vertical strip,
        or a single frame.  Exact frame size comes from the sprite
        template's OAM in a later phase."""
        if not sprite.png_exists:
            return []
        sheet = load_sprite_pixmap(sprite.png_path, palette)
        if sheet is None or sheet.isNull():
            return []
        w, h = sheet.width(), sheet.height()
        if w <= 0 or h <= 0:
            return []
        if w > h and w % h == 0:
            fw = fh = h
            n = w // fw
            return [sheet.copy(i * fw, 0, fw, fh) for i in range(n)]
        if h > w and h % w == 0:
            fw = fh = w
            n = h // fh
            return [sheet.copy(0, i * fh, fw, fh) for i in range(n)]
        return [sheet]

    # ── palette editing ──────────────────────────────────────────────

    def _on_palette_edited(self):
        if self._loading or not self._current:
            return
        sprite = self._sprites.get(self._current)
        if sprite is None:
            return
        colors = self._pal_row.colors()
        self._palettes[sprite.tag] = colors
        self._palette_dirty.add(sprite.tag)
        _get_palette_bus().set_battle_anim_palette(sprite.tag, colors)
        # Visual dirty: palette frame + this card's amber border.
        self._pal_frame.setStyleSheet(_DIRTY_SS)
        if sprite.tag in self._cards:
            self._cards[sprite.tag].set_dirty(True)
        # Re-render preview/sheet/thumbnail with the new colours.
        self._refresh_current_render()
        self.modified.emit()

    def _refresh_current_render(self):
        if not self._current:
            return
        sprite = self._sprites.get(self._current)
        if sprite is None:
            return
        pal = self._palettes.get(sprite.tag) or self._palette_for(sprite)
        self._frames = self._slice_frames(sprite, pal)
        self._frame_idx = 0
        if self._frames:
            self._preview.set_anim_pixmap(self._frames[0])
            if sprite.tag in self._cards:
                self._cards[sprite.tag].set_thumbnail(self._frames[0])
        if sprite.png_exists:
            sheet = load_sprite_pixmap(sprite.png_path, pal)
            if sheet and not sheet.isNull():
                self._sheet_lbl.setPixmap(sheet.scaled(
                    sheet.width() * 2, sheet.height() * 2,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.FastTransformation))

    def _load_colors_into_row(self, colors: List[Color]):
        self._loading = True
        try:
            self._pal_row.set_colors(colors + [(0, 0, 0)] * (16 - len(colors)))
        finally:
            self._loading = False
        # Treat an import as an edit.
        self._on_palette_edited()

    def _show_in_folder(self):
        if not self._current:
            return
        sprite = self._sprites.get(self._current)
        if sprite is None or not sprite.png_path:
            return
        import subprocess
        try:
            if os.path.isfile(sprite.png_path):
                subprocess.Popen(
                    ["explorer", "/select,", os.path.normpath(sprite.png_path)])
            else:
                folder = os.path.dirname(sprite.png_path)
                if os.path.isdir(folder):
                    try:
                        os.startfile(folder)  # type: ignore[attr-defined]
                    except Exception:
                        pass
        except Exception:
            pass

    def _replace_image(self):
        """Replace the sprite's PNG with a user-picked image, indexed to 16
        colours via the shared manual picker.  Writes the new PNG immediately
        (the build recompresses the .4bpp.lz) and loads its palette into the
        editor (saved to .pal/.gbapal on the next Save)."""
        if not self._current:
            return
        sprite = self._sprites.get(self._current)
        if sprite is None or not sprite.png_path:
            return
        # Warn when this image is SHARED — the new PNG replaces the gfx
        # for every sprite using it (each keeps its own palette).
        others = [t for t in self._gfx_groups.get(sprite.gfx_symbol, [])
                  if t != sprite.tag]
        if others:
            names = ", ".join(self._sprites[t].display_name for t in others)
            ret = QMessageBox.warning(
                self, "Shared Image",
                f"<b>{sprite.display_name}</b> shares its image with "
                f"{len(others)} other sprite(s):<br><br>{names}<br><br>"
                f"Replacing the image changes the artwork for <b>all</b> of "
                f"them (each keeps its own palette). Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel)
            if ret != QMessageBox.StandardButton.Yes:
                return
        start = os.path.dirname(sprite.png_path)
        path, _ = QFileDialog.getOpenFileName(
            self, "Pick replacement image", start, "PNG Images (*.png)")
        if not path:
            return
        from ui.dialogs.manual_palette_pick_dialog import (
            import_image_manually_from_path, save_remapped_image)
        result = import_image_manually_from_path(
            path, target_colors=16, parent=self)
        if result is None:
            return
        colors, remapped_img = result
        # Write the remapped indexed PNG over the sprite's source PNG.
        try:
            ok = save_remapped_image(remapped_img, colors, sprite.png_path)
        except Exception as exc:
            ok = False
            QMessageBox.warning(
                self, "Replace Image",
                f"Couldn't write the image to:\n{sprite.png_path}\n\n{exc}")
            return
        if not ok:
            QMessageBox.warning(
                self, "Replace Image",
                f"Couldn't write the image to:\n{sprite.png_path}")
            return
        # Load the new palette into the row (marks palette dirty so the
        # .pal/.gbapal sidecar is written on Save) and re-render from the
        # freshly-written PNG.
        self._load_colors_into_row(colors)
        self._show_detail(sprite.tag)
        QMessageBox.information(
            self, "Replace Image",
            f"Replaced {os.path.basename(sprite.png_path)}.\n\n"
            "Click File → Save to write the palette, then Make / Make Modern "
            "to rebuild (the build recompresses the .4bpp.lz from the PNG).")

    def _import_palette(self, manual: bool):
        if not self._current:
            return
        sprite = self._sprites[self._current]
        start = os.path.dirname(sprite.png_path) if sprite.png_path else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Select PNG", start, "PNG Images (*.png)")
        if not path:
            return
        if manual:
            from ui.dialogs.manual_palette_pick_dialog import (
                import_image_manually_from_path)
            result = import_image_manually_from_path(
                path, target_colors=16, parent=self)
            if result is None:
                return
            colors, _img = result
            self._load_colors_into_row(list(colors))
            return
        img = QImage(path)
        if img.isNull():
            QMessageBox.warning(self, "Import Failed", f"Could not load:\n{path}")
            return
        if img.format() != QImage.Format.Format_Indexed8:
            QMessageBox.warning(
                self, "Not an Indexed PNG",
                "This PNG isn't indexed (8-bit, 16 colours).\n"
                "Convert it first, or use 'Import Manually…' to remap any PNG.")
            return
        ct = img.colorTable()
        colors = [clamp_to_gba((c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF)
                  for c in ct[:16]]
        while len(colors) < 16:
            colors.append((0, 0, 0))
        self._load_colors_into_row(colors)

    def _import_pal_file(self):
        if not self._current:
            return
        sprite = self._sprites[self._current]
        start = os.path.dirname(sprite.pal_path or sprite.png_path or "")
        path, _ = QFileDialog.getOpenFileName(
            self, "Select .pal", start, "JASC Palette (*.pal)")
        if not path:
            return
        colors = read_jasc_pal(path, 16)
        if not colors:
            QMessageBox.warning(self, "Import Failed",
                                f"No colours read from:\n{path}")
            return
        self._load_colors_into_row(colors)

    # ── Move Animations (timeline viewer) ────────────────────────────

    def _rebuild_move_list(self):
        """Populate the move list (alphabetical by display name), filtered
        to moves that actually have a parsed animation script."""
        self._move_list.blockSignals(True)
        self._move_list.clear()
        needle = self._move_search.text().strip().lower()
        rows = []
        for idx, label in enumerate(self._move_table):
            if label not in self._scripts:
                continue
            name = move_display_name(label, self._move_names)
            if needle and needle not in name.lower() and needle not in label.lower():
                continue
            rows.append((name, label, idx))
        rows.sort(key=lambda r: (r[0].lower(), r[1]))
        for name, label, idx in rows:
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, label)
            item.setToolTip(f"{label}   (move #{idx})")
            self._move_list.addItem(item)
            if label == self._move_current:
                self._move_list.setCurrentItem(item)
        self._move_count_lbl.setText(f"{len(rows)} moves")
        self._move_list.blockSignals(False)

    def _on_move_selected(self, current, _previous):
        if current is None:
            return
        label = current.data(Qt.ItemDataRole.UserRole)
        if not label:
            return
        self._move_current = label
        self._populate_timeline(label)

    def _populate_timeline(self, label: str):
        """Resolve the move's script into a flat timeline and render it as
        an icon-tagged, colour-coded, depth-indented command list."""
        self._timeline.clear()
        timeline = resolve_timeline(self._scripts, label, inline_calls=True)
        name = move_display_name(label, self._move_names)
        n_sound = sum(1 for c in timeline if c.kind == KIND_SOUND)
        n_spr = sum(1 for c in timeline if c.kind == KIND_SPRITE)
        self._tl_header.setText(
            f"<b>{name}</b>  ({label})<br>"
            f"{len(timeline)} steps · {n_sound} sound(s) · {n_spr} sprite spawn(s)"
            f"  —  indented rows are shared sub-scripts (read-only).<br>"
            f"Rows marked ✎ (sounds &amp; delays on this move) are editable — "
            f"<b>double-click</b> to change.")
        # Track the index within the move's OWN script (depth-0 commands
        # only), so an editable row maps back to a precise rewrite target.
        own_idx = -1
        for cmd in timeline:
            if cmd.depth == 0:
                own_idx += 1
            glyph, colour = _KIND_STYLE.get(cmd.kind, _KIND_DEFAULT)
            indent = "    " * cmd.depth
            # Editable = a sound or delay on the move's OWN script (depth 0).
            # Inlined shared-subroutine rows (depth>0) stay read-only so an
            # edit can't silently change every move that calls them.
            editable = (cmd.depth == 0 and cmd.kind in (KIND_SOUND, KIND_DELAY))
            suffix = "   ✎" if editable else ""
            item = QListWidgetItem(f"{indent}{glyph}  {cmd.summary}{suffix}")
            item.setForeground(QColor(colour))
            tip = cmd.raw
            if editable:
                tip += "\n(double-click to edit)"
            elif cmd.depth > 0 and cmd.kind in (KIND_SOUND, KIND_DELAY):
                tip += "\n(shared sub-script — edit it on the move it belongs to)"
            item.setToolTip(tip)
            if editable:
                item.setData(Qt.ItemDataRole.UserRole, (label, own_idx, cmd.kind))
            # Any sound row (incl. inlined) carries its SE_* for preview.
            if cmd.kind == KIND_SOUND and cmd.args:
                item.setData(_SOUND_ROLE, cmd.args[0])
            self._timeline.addItem(item)
        # Show the move's primary sprite on the battle scene (first gfx load).
        self._update_move_preview(timeline)

    def _preview_selected_sound(self):
        """Play the selected timeline row's sound (if it is one) through the
        Sound Editor — read-only audition, works on inlined rows too."""
        item = self._timeline.currentItem()
        if item is None:
            return
        se = item.data(_SOUND_ROLE)
        if not se:
            self._move_prev_lbl.setText(
                "Select a 🔊 sound row, then press Preview.")
            return
        if _preview_sound_cb is not None:
            _preview_sound_cb(se)

    def _on_timeline_double_click(self, item: QListWidgetItem):
        """Edit a depth-0 sound or delay command in place."""
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return  # not an editable row
        label, own_idx, kind = data
        cmds = self._scripts.get(label, [])
        if own_idx >= len(cmds):
            return
        cmd = cmds[own_idx]
        if kind == KIND_DELAY:
            new_cmd = self._edit_delay_dialog(cmd)
        elif kind == KIND_SOUND:
            new_cmd = self._edit_sound_dialog(cmd)
        else:
            return
        if new_cmd is None or new_cmd == cmd.raw:
            return
        # Rewrite the .s text in place, then re-parse + refresh.
        new_text = rewrite_script_command(
            self._scripts_text, label, own_idx, new_cmd)
        if new_text is None:
            QMessageBox.warning(
                self, "Edit Timeline",
                "Couldn't locate that command in the script source — "
                "no change made.")
            return
        self._scripts_text = new_text
        self._scripts_dirty = True
        # Re-parse so the in-memory model + timeline reflect the edit.
        self._reparse_scripts_from_text()
        self._populate_timeline(label)
        self.modified.emit()

    def _edit_delay_dialog(self, cmd) -> Optional[str]:
        cur = 0
        if cmd.args:
            try:
                cur = int(cmd.args[0], 0)
            except ValueError:
                cur = 0
        dlg = QDialog(self)
        dlg.setWindowTitle("Edit Delay")
        form = QFormLayout(dlg)
        spin = QSpinBox()
        spin.setRange(0, 255)
        spin.setValue(max(0, min(255, cur)))
        spin.setSuffix(" frames")
        spin.wheelEvent = lambda e: e.ignore()
        form.addRow("Wait:", spin)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        return format_command(cmd.name, [str(spin.value())])

    def _edit_sound_dialog(self, cmd) -> Optional[str]:
        dlg = QDialog(self)
        dlg.setWindowTitle("Edit Sound")
        form = QFormLayout(dlg)
        se_combo = QComboBox()
        se_combo.setEditable(True)   # editable = type-to-search
        se_combo.addItems(self._sound_effects or [])
        cur_se = cmd.args[0] if cmd.args else ""
        if cur_se:
            i = se_combo.findText(cur_se)
            if i >= 0:
                se_combo.setCurrentIndex(i)
            else:
                se_combo.setEditText(cur_se)
        # Sound row + ▶ preview / ⏹ stop buttons (audition through the
        # Sound Editor, just like EVENTide's playse picker).
        se_row = QHBoxLayout()
        se_row.addWidget(se_combo, 1)
        btn_play = QPushButton("▶")
        btn_play.setFixedWidth(30)
        btn_play.setToolTip("Preview this sound effect")
        btn_play.clicked.connect(
            lambda: _preview_sound_cb and _preview_sound_cb(
                se_combo.currentText().strip()))
        btn_stop = QPushButton("⏹")
        btn_stop.setFixedWidth(30)
        btn_stop.setToolTip("Stop preview")
        btn_stop.clicked.connect(lambda: _stop_sound_cb and _stop_sound_cb())
        if _preview_sound_cb is None:
            btn_play.setEnabled(False)
            btn_play.setToolTip("Sound preview unavailable (Sound Editor not loaded)")
            btn_stop.setEnabled(False)
        se_row.addWidget(btn_play)
        se_row.addWidget(btn_stop)
        form.addRow("Sound:", se_row)
        # Pan field only when the opcode carries a pan arg.
        pan_combo = None
        if len(cmd.args) > 1:
            pan_combo = QComboBox()
            pan_combo.setEditable(True)
            pan_combo.addItems(["SOUND_PAN_TARGET", "SOUND_PAN_ATTACKER", "0"])
            cur_pan = cmd.args[1]
            j = pan_combo.findText(cur_pan)
            if j >= 0:
                pan_combo.setCurrentIndex(j)
            else:
                pan_combo.setEditText(cur_pan)
            form.addRow("Pan:", pan_combo)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        new_se = se_combo.currentText().strip()
        if not new_se:
            return None
        new_args = list(cmd.args)
        new_args[0] = new_se
        if pan_combo is not None and len(new_args) > 1:
            new_args[1] = pan_combo.currentText().strip() or new_args[1]
        return format_command(cmd.name, new_args)

    def _read_scripts_text(self, project_root: str) -> str:
        """Read the raw battle_anim_scripts.s text (held for in-place edits)."""
        from core.battle_anim_script import scripts_path
        path = scripts_path(project_root)
        if not os.path.isfile(path):
            return ""
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                return f.read()
        except OSError:
            return ""

    def _reparse_scripts_from_text(self):
        """Re-derive the in-memory script model from the (edited) .s text
        so the timeline + move list reflect edits without touching disk."""
        from core.battle_anim_script import parse_scripts_text
        self._scripts = parse_scripts_text(self._scripts_text)

    def _update_move_preview(self, timeline):
        """Approximate context: drop the first sprite the move loads onto
        the battle scene (read-only viewer — not a faithful playback)."""
        tag = ""
        for cmd in timeline:
            if cmd.kind == KIND_GFX and cmd.name == "loadspritegfx" and cmd.args:
                tag = cmd.args[0]
                break
        sprite = self._sprites.get(tag) if tag else None
        if sprite is not None and sprite.png_exists:
            pal = self._palette_for(sprite)
            frames = self._slice_frames(sprite, pal)
            if frames:
                self._move_preview.set_anim_pixmap(frames[0])
                self._move_prev_lbl.setText(
                    f"First loaded sprite: {sprite.display_name}  ({tag})")
                return
        self._move_preview.set_anim_pixmap(None)
        self._move_prev_lbl.setText(
            "No dedicated sprite to preview (effect uses tasks / mon sprites).")

    # ── save ─────────────────────────────────────────────────────────

    def has_unsaved_changes(self) -> bool:
        return bool(self._palette_dirty) or self._scripts_dirty

    def flush_to_disk(self) -> Tuple[int, List[str]]:
        """Write every dirty palette to its ``.gbapal`` + ``.pal`` source,
        and the edited battle_anim_scripts.s if a timeline edit is pending.
        Returns ``(saved_count, errors)``.  The build recompresses the
        ``.gbapal.lz`` artefact from the ``.gbapal`` we write here."""
        saved = 0
        errors: List[str] = []
        # Timeline (script) edits: write the full file text back.
        if self._scripts_dirty and self._scripts_text and self._project_root:
            try:
                from core.battle_anim_script import scripts_path
                path = scripts_path(self._project_root)
                with open(path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(self._scripts_text)
                self._scripts_dirty = False
                saved += 1
            except Exception as exc:
                errors.append(f"battle-anim-script: write failed ({exc})")
        for tag in list(self._palette_dirty):
            sprite = self._sprites.get(tag)
            colors = self._palettes.get(tag)
            if sprite is None or not colors:
                self._palette_dirty.discard(tag)
                continue
            # Derive both palette paths from the sprite's resolved base.
            base = sprite.pal_path
            if base.endswith(".pal"):
                base = base[: -len(".pal")]
            elif base.endswith(".gbapal"):
                base = base[: -len(".gbapal")]
            elif sprite.png_path.endswith(".png"):
                base = sprite.png_path[: -len(".png")]
            else:
                errors.append(f"battle-anim:{tag} (no palette path)")
                continue
            gbapal_path = base + ".gbapal"
            pal_path = base + ".pal"
            try:
                ok_gba, ok_pal = write_palette_pair(gbapal_path, pal_path,
                                                    colors)
                if ok_gba and ok_pal:
                    self._palette_dirty.discard(tag)
                    if tag in self._cards:
                        self._cards[tag].set_dirty(False)
                    saved += 1
                else:
                    missing = []
                    if not ok_gba:
                        missing.append(".gbapal")
                    if not ok_pal:
                        missing.append(".pal")
                    errors.append(
                        f"battle-anim:{tag} (failed {', '.join(missing)})")
            except Exception as exc:
                errors.append(f"battle-anim:{tag} ({exc})")
        if not self._palette_dirty:
            self._pal_frame.setStyleSheet("")
        return saved, errors
