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
import logging
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

# File logger — battle-anim playback runs on a timer in the live app where a
# crash leaves no console output, so progress is written to battle_anim.log
# (next to the other module logs) to make any hard crash diagnosable.
_log = logging.getLogger("BattleAnim")
if not _log.handlers:
    try:
        _h = logging.FileHandler(
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "battle_anim.log"),
            mode="a", encoding="utf-8")
        _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        _log.addHandler(_h)
        _log.setLevel(logging.DEBUG)
        _log.propagate = False
    except OSError:
        pass

from PyQt6.QtCore import Qt, QObject, QEvent, QTimer, QSize, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPixmap
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QGroupBox, QScrollArea, QGridLayout, QFrame, QSplitter, QSizePolicy,
    QTabWidget, QMessageBox, QFileDialog, QListWidget, QListWidgetItem,
    QComboBox, QSpinBox, QDialog, QDialogButtonBox, QFormLayout, QSlider,
    QToolButton, QStackedWidget,
)

from core.battle_anim_data import (
    parse_battle_anim_sprites, parse_anim_frame_sizes, parse_template_tags,
    parse_template_callbacks, classify_anim_callbacks,
    MOTION_STATIC_TARGET, MOTION_STATIC_ATTACKER, MOTION_ON_MON_POS,
    MOTION_LINEAR_TO_TARGET, MOTION_ARC_TO_TARGET, MOTION_INVISIBLE,
    MOTION_UNKNOWN,
    BattleAnimSprite)
from core.battle_anim_script import (
    parse_move_anim_table, parse_anim_scripts, resolve_timeline,
    parse_move_names, move_display_name, parse_sound_effects,
    rewrite_script_command, format_command,
    insert_script_command, delete_script_command, move_script_command,
    parse_createsprite, format_createsprite, CreateSpriteCmd,
    parse_createvisualtask, format_createvisualtask,
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

    def __init__(self, tag: str, name: str, pix: Optional[QPixmap],
                 shared: bool = False, share_tip: str = ""):
        super().__init__()
        self._tag = tag
        self._selected = False
        self._dirty = False
        self.setFixedSize(_CARD_W, _CARD_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if shared:
            self.setToolTip(share_tip or "Shares its image with other sprites")
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
        # 🔗 prefix marks a shared image at a glance in the grid.
        lbl = QLabel(("🔗 " if shared else "") + name)
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
        self._frame_sizes: Dict[str, Tuple[int, int]] = {}   # tag -> (fw, fh) from sprite templates

        # Move-animation (timeline) state.
        self._move_table: List[str] = []                    # script labels by move idx
        self._scripts: Dict[str, list] = {}                 # label -> [Command]
        self._move_names: Dict[str, str] = {}               # MOVE_CONST -> project name
        self._move_current: Optional[str] = None            # selected script label
        self._scripts_text: str = ""                        # raw battle_anim_scripts.s
        self._scripts_dirty: bool = False                   # unsaved timeline edits?
        self._sound_effects: List[str] = []                 # SE_* constants for picker
        self._template_tags: Dict[str, str] = {}            # template symbol -> ANIM_TAG
        self._tpl_callbacks: Dict[str, str] = {}            # template symbol -> callback symbol
        self._callback_arch: Dict[str, str] = {}            # callback symbol -> MOTION_*
        self._cur_timeline: list = []                       # resolved Commands of selected move
        self._dirty_moves: set = set()                      # labels with unsaved edits (amber)

        # Composite-preview / layer-scrubber state (Move Animations tab).
        self._layer_frames: List[QPixmap] = []              # frames of selected layer sprite
        self._layer_idx = 0
        self._layer_tag = ""                                # selected layer's ANIM_TAG
        self._layer_timer = QTimer(self)
        self._layer_timer.setInterval(160)
        self._layer_timer.timeout.connect(self._advance_layer_frame)

        # Whole-move playback state.  Walks the resolved timeline by its
        # delays, spawning sprite layers + firing sounds in order, and
        # cycling each live layer's frames — an approximate dry-run of the
        # move (real motion/lifetimes are computed by the engine in C).
        self._playing = False
        self._in_play_step = False  # re-entrancy guard (sound cb can pump events)
        self._play_idx = 0
        self._play_wait = 0        # ticks remaining on the current delay
        self._play_tick = 0
        self._last_sound_tick = -100   # throttle rapid/overlapping sound previews
        self._play_layers: list = []   # [tag, cx, cy, subpri, frame_idx]
        self._play_frame_cache: Dict[str, List[QPixmap]] = {}  # pre-baked at start
        self._play_direction = "player"  # "player" = player attacks; "enemy" = enemy attacks
        self._play_timer = QTimer(self)
        self._play_timer.setInterval(16)   # ~60 fps, so delays read game-speed
        self._play_timer.timeout.connect(self._play_step)

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
        # Stop whole-move playback when the user leaves the Move Animations
        # sub-tab, so the timer never runs against a hidden / stale view.
        self._subtabs.currentChanged.connect(
            lambda _i: self._stop_play_move())

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

        # ── CENTER: battle-scene preview (layered composite) ──
        center = QWidget()
        cv = QVBoxLayout(center)
        prev_box = QGroupBox("Battle Scene Preview")
        pv = QVBoxLayout(prev_box)
        from ui.graphics_tab_widget import BattleScenePreview
        self._move_preview = BattleScenePreview(self._res_dir)
        pv.addWidget(self._move_preview, 0, Qt.AlignmentFlag.AlignHCenter)
        # Direction toggle — battle anims are played from BOTH sides:
        # "Player attacks" = ANIM_ATTACKER at back/player position, ANIM_TARGET at front/enemy.
        # "Enemy attacks"  = ANIM_ATTACKER at front/enemy position, ANIM_TARGET at back/player.
        dir_row = QHBoxLayout()
        from PyQt6.QtWidgets import QButtonGroup, QRadioButton
        self._dir_player_rb = QRadioButton("Player attacks ▶")
        self._dir_enemy_rb  = QRadioButton("◀ Enemy attacks")
        self._dir_player_rb.setChecked(True)
        self._dir_group = QButtonGroup(self)
        self._dir_group.addButton(self._dir_player_rb, 0)
        self._dir_group.addButton(self._dir_enemy_rb,  1)
        self._dir_player_rb.toggled.connect(lambda _: self._on_direction_changed())
        dir_row.addStretch(1)
        dir_row.addWidget(self._dir_player_rb)
        dir_row.addWidget(self._dir_enemy_rb)
        dir_row.addStretch(1)
        pv.addLayout(dir_row)
        self._move_prev_lbl = QLabel("Select a move")
        self._move_prev_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._move_prev_lbl.setStyleSheet("color: #aaa; font-size: 11px;")
        self._move_prev_lbl.setWordWrap(True)
        pv.addWidget(self._move_prev_lbl)
        cv.addWidget(prev_box)

        # Layer / frame scrubber — drives the SELECTED sprite layer's own
        # frame cycle so you can step through (or play) its frames.
        scrub_box = QGroupBox("Selected Sprite — Frames")
        sv = QVBoxLayout(scrub_box)
        self._layer_lbl = QLabel("Select a sprite-spawn row to inspect its frames.")
        self._layer_lbl.setWordWrap(True)
        self._layer_lbl.setStyleSheet("color: #aaa; font-size: 11px;")
        sv.addWidget(self._layer_lbl)
        scrub_row = QHBoxLayout()
        self._layer_play = QToolButton()
        self._layer_play.setText("▶")
        self._layer_play.setToolTip("Play this sprite's frame cycle")
        self._layer_play.clicked.connect(self._toggle_layer_play)
        self._layer_play.setEnabled(False)
        scrub_row.addWidget(self._layer_play)
        self._layer_slider = QSlider(Qt.Orientation.Horizontal)
        self._layer_slider.setMinimum(0)
        self._layer_slider.setMaximum(0)
        self._layer_slider.setEnabled(False)
        self._layer_slider.valueChanged.connect(self._on_layer_scrub)
        scrub_row.addWidget(self._layer_slider, 1)
        self._layer_frame_lbl = QLabel("—")
        self._layer_frame_lbl.setFixedWidth(64)
        self._layer_frame_lbl.setStyleSheet("color: #ccc; font-size: 11px;")
        scrub_row.addWidget(self._layer_frame_lbl)
        sv.addLayout(scrub_row)
        self._layer_edit_btn = QPushButton("Edit this sprite (image + palette) ▸")
        self._layer_edit_btn.setToolTip(
            "Jump to the Sprites tab to edit this sprite's image and palette.")
        self._layer_edit_btn.clicked.connect(self._edit_selected_layer_sprite)
        self._layer_edit_btn.setEnabled(False)
        sv.addWidget(self._layer_edit_btn)
        cv.addWidget(scrub_box)
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

        # ── Edit toolbar: add / edit / delete / reorder commands ──
        edit_row = QHBoxLayout()
        edit_row.setSpacing(4)

        def _mk(text, tip, slot, width=34):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.setFixedWidth(width)
            b.clicked.connect(slot)
            b.setEnabled(False)
            edit_row.addWidget(b)
            return b

        self._tl_add_btn = _mk("＋", "Add a new command after the selected row",
                               self._tl_add, 60)
        self._tl_add_btn.setText("＋ Add")
        self._tl_edit_btn = _mk("✎", "Edit the selected command", self._tl_edit)
        self._tl_del_btn = _mk("🗑", "Delete the selected command", self._tl_delete)
        self._tl_up_btn = _mk("▲", "Move the selected command earlier",
                              lambda: self._tl_move(-1))
        self._tl_down_btn = _mk("▼", "Move the selected command later",
                                lambda: self._tl_move(+1))
        edit_row.addStretch(1)
        tv.addLayout(edit_row)

        self._timeline = QListWidget()
        self._timeline.setStyleSheet(
            "QListWidget { background: #141414; border: 1px solid #2e2e2e; "
            "font-family: Consolas, monospace; font-size: 11px; }")
        self._timeline.itemDoubleClicked.connect(self._on_timeline_double_click)
        self._timeline.currentRowChanged.connect(self._on_timeline_row_changed)
        tv.addWidget(self._timeline, 1)
        # Preview the selected sound row through the Sound Editor.
        tl_btn_row = QHBoxLayout()
        # ▶ Play Move — runs the whole animation in sequence with delays
        self._tl_play_move_btn = QPushButton("▶  Play Move")
        self._tl_play_move_btn.setToolTip(
            "Simulate the full animation: sprites layer in order, sounds fire, "
            "delays advance time.  Positions approximate.")
        self._tl_play_move_btn.clicked.connect(self._toggle_play_move)
        self._tl_play_move_btn.setEnabled(False)
        # ▶ Preview Sound — fires the selected row's SE through the Sound Editor
        self._tl_play_btn = QPushButton("▶  Sound")
        self._tl_play_btn.setToolTip(
            "Play the selected sound row's effect through the Sound Editor.")
        self._tl_play_btn.clicked.connect(self._preview_selected_sound)
        self._tl_stop_btn = QPushButton("⏹")
        self._tl_stop_btn.setFixedWidth(36)
        self._tl_stop_btn.setToolTip("Stop sound preview")
        self._tl_stop_btn.clicked.connect(
            lambda: _stop_sound_cb and _stop_sound_cb())
        tl_btn_row.addWidget(self._tl_play_move_btn)
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

        # Exact per-frame sizes resolved from sprite templates (OAM).
        try:
            self._frame_sizes = parse_anim_frame_sizes(project_root)
        except Exception:
            self._frame_sizes = {}

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
        self._dirty_moves.clear()
        self._cur_timeline = []
        try:
            self._move_table = parse_move_anim_table(project_root)
            self._scripts = parse_anim_scripts(project_root)
            self._move_names = parse_move_names(project_root)
            self._sound_effects = parse_sound_effects(project_root)
            self._scripts_text = self._read_scripts_text(project_root)
            self._template_tags = parse_template_tags(project_root)
            self._tpl_callbacks = parse_template_callbacks(project_root)
            self._callback_arch = classify_anim_callbacks(project_root)
        except Exception:
            self._move_table, self._scripts, self._move_names = [], {}, {}
            self._sound_effects, self._scripts_text = [], ""
            self._template_tags = {}
            self._tpl_callbacks = {}
            self._callback_arch = {}

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
        self._stop_play_move()
        self._layer_timer.stop()
        self._layer_frames = []
        self._layer_idx = 0
        self._layer_tag = ""
        self._reset_layer_scrubber()
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
        self._update_edit_buttons()
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
            group = self._gfx_groups.get(sprite.gfx_symbol, [])
            shared = len(group) > 1
            share_tip = ""
            if shared:
                names = ", ".join(self._sprites[t].display_name
                                  for t in group if t != tag)
                share_tip = f"Shares its image with: {names}"
            card = _AnimCard(tag, sprite.display_name, pix,
                             shared=shared, share_tip=share_tip)
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
            exact = sprite.tag in self._frame_sizes
            if exact:
                fw, fh = self._frame_sizes[sprite.tag]
                qual = f"{fw}×{fh}"
            else:
                qual = "approx"
            self._frame_lbl.setText(
                f"{n} frame{'s' if n != 1 else ''} ({qual})"
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
        # EXACT frame size from the sprite template's OAM, when resolved —
        # slice the sheet into a row-major grid of fw×fh frames.
        fw, fh = self._frame_sizes.get(sprite.tag, (0, 0))
        if fw > 0 and fh > 0 and w % fw == 0 and h % fh == 0:
            cols, rows = w // fw, h // fh
            if cols * rows >= 1:
                return [sheet.copy(c * fw, r * fh, fw, fh)
                        for r in range(rows) for c in range(cols)]
        # Fallback: approximate square-frame inference (tag had no
        # resolvable template, or the sheet doesn't divide evenly).
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
            # Per-item amber for moves with unsaved timeline edits (Pattern A).
            if label in self._dirty_moves:
                item.setBackground(QColor("#3d2e00"))
            self._move_list.addItem(item)
            if label == self._move_current:
                self._move_list.setCurrentItem(item)
        self._move_count_lbl.setText(f"{len(rows)} moves")
        self._move_list.blockSignals(False)

    def _mark_move_dirty(self, label: str):
        """Tint the given move's row amber (unsaved edits)."""
        for i in range(self._move_list.count()):
            it = self._move_list.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == label:
                it.setBackground(QColor("#3d2e00"))
                return

    def _on_move_selected(self, current, _previous):
        if current is None:
            return
        label = current.data(Qt.ItemDataRole.UserRole)
        if not label:
            return
        self._stop_play_move()
        self._move_current = label
        self._populate_timeline(label)

    def _populate_timeline(self, label: str, select_own_idx: Optional[int] = None):
        """Resolve the move's script into a flat timeline and render it as
        an icon-tagged, colour-coded, depth-indented command list.

        Every depth-0 row carries ``(label, own_idx, kind, depth)`` so the
        edit toolbar can target it; inlined (depth>0) rows carry
        ``own_idx = -1`` and stay read-only.  ``select_own_idx`` re-selects
        a specific own-command row after a structural edit (so focus stays
        on what the user just changed)."""
        self._timeline.blockSignals(True)
        self._timeline.clear()
        timeline = resolve_timeline(self._scripts, label, inline_calls=True)
        self._cur_timeline = timeline
        name = move_display_name(label, self._move_names)
        n_sound = sum(1 for c in timeline if c.kind == KIND_SOUND)
        n_spr = sum(1 for c in timeline if c.kind == KIND_SPRITE)
        dirty = (" — <span style='color:#ffb74d'>unsaved edits</span>"
                 if label in self._dirty_moves else "")
        self._tl_header.setText(
            f"<b>{name}</b>  ({label}){dirty}<br>"
            f"{len(timeline)} steps · {n_sound} sound(s) · {n_spr} sprite spawn(s)"
            f"  —  indented rows are shared sub-scripts (read-only).<br>"
            f"Select a row, then use the toolbar to add / edit / delete / "
            f"reorder.  Double-click a row to edit it.")
        own_idx = -1
        select_row = 0
        for cmd in timeline:
            if cmd.depth == 0:
                own_idx += 1
            glyph, colour = _KIND_STYLE.get(cmd.kind, _KIND_DEFAULT)
            indent = "    " * cmd.depth
            # Every depth-0 row is editable now (add/edit/delete/reorder).
            # Inlined shared-subroutine rows (depth>0) stay read-only so an
            # edit can't silently change every move that calls them.
            own_editable = (cmd.depth == 0)
            suffix = "   ✎" if own_editable else ""
            item = QListWidgetItem(f"{indent}{glyph}  {cmd.summary}{suffix}")
            item.setForeground(QColor(colour))
            tip = cmd.raw
            if own_editable:
                tip += "\n(double-click to edit; toolbar to add / delete / reorder)"
            elif cmd.depth > 0:
                tip += "\n(shared sub-script — edit it on the move it belongs to)"
            item.setToolTip(tip)
            item.setData(Qt.ItemDataRole.UserRole,
                         (label, own_idx if cmd.depth == 0 else -1,
                          cmd.kind, cmd.depth))
            # Any sound row (incl. inlined) carries its SE_* for preview.
            if cmd.kind == KIND_SOUND and cmd.args:
                item.setData(_SOUND_ROLE, cmd.args[0])
            if (select_own_idx is not None and cmd.depth == 0
                    and own_idx == select_own_idx):
                select_row = self._timeline.count()
            self._timeline.addItem(item)
        self._timeline.blockSignals(False)
        has_timeline = bool(self._timeline.count())
        self._tl_play_move_btn.setEnabled(has_timeline)
        if has_timeline:
            self._timeline.setCurrentRow(
                min(select_row, self._timeline.count() - 1))
        else:
            self._update_edit_buttons()
            self._update_move_composite()

    # ── timeline editing (add / edit / delete / reorder) ──────────────

    def _on_timeline_row_changed(self, row: int):
        """Selection moved: refresh edit-button enablement, the layered
        composite (spawns through this row) and the frame scrubber.
        Suppressed during whole-move playback — the player drives the row
        cursor itself and triggers its own composite rendering."""
        self._update_edit_buttons()
        if self._playing:
            return   # playback owns the cursor; don't fight it
        # Load the selected row's sprite into the frame scrubber, if it's a
        # sprite-spawn (createsprite) or gfx-load row.
        tag = ""
        if 0 <= row < len(self._cur_timeline):
            cmd = self._cur_timeline[row]
            if cmd.name == "createsprite":
                cs = parse_createsprite(cmd)
                if cs:
                    tag = self._template_tags.get(cs.template, "")
            elif cmd.name in ("loadspritegfx", "unloadspritegfx") and cmd.args:
                tag = cmd.args[0]
        self._load_layer_scrubber(tag)
        self._update_move_composite()

    def _update_edit_buttons(self):
        """Enable/disable the edit toolbar from the current selection."""
        has_move = bool(self._move_current)
        self._tl_add_btn.setEnabled(has_move)
        item = self._timeline.currentItem()
        data = item.data(Qt.ItemDataRole.UserRole) if item else None
        depth0 = bool(data) and data[3] == 0
        self._tl_edit_btn.setEnabled(depth0)
        self._tl_del_btn.setEnabled(depth0)
        if depth0:
            label, own_idx = data[0], data[1]
            n = len(self._scripts.get(label, []))
            self._tl_up_btn.setEnabled(own_idx > 0)
            self._tl_down_btn.setEnabled(own_idx < n - 1)
        else:
            self._tl_up_btn.setEnabled(False)
            self._tl_down_btn.setEnabled(False)

    def _selected_depth0(self):
        """Return ``(label, own_idx)`` for the selected depth-0 row, else
        ``None``."""
        item = self._timeline.currentItem()
        data = item.data(Qt.ItemDataRole.UserRole) if item else None
        if data and data[3] == 0:
            return data[0], data[1]
        return None

    def _apply_structural_edit(self, new_text: Optional[str], label: str,
                               select_own_idx: Optional[int]) -> bool:
        """Commit an in-memory edit of the script text, re-parse, repopulate
        the timeline (restoring focus), and mark everything dirty."""
        if new_text is None:
            QMessageBox.warning(
                self, "Edit Timeline",
                "Couldn't locate that command in the script source — "
                "no change made.")
            return False
        self._scripts_text = new_text
        self._scripts_dirty = True
        self._dirty_moves.add(label)
        self._reparse_scripts_from_text()
        self._mark_move_dirty(label)
        self._populate_timeline(label, select_own_idx=select_own_idx)
        self.modified.emit()
        return True

    def _tl_add(self):
        """Insert a new command after the selected row (or at the end of
        the move's own script)."""
        label = self._move_current
        if not label:
            return
        sel = self._selected_depth0()
        at_idx = (sel[1] + 1) if sel else len(self._scripts.get(label, []))
        new_cmd = self._command_editor_dialog(label, None)
        if not new_cmd:
            return
        new_text = insert_script_command(
            self._scripts_text, label, at_idx, new_cmd)
        self._apply_structural_edit(new_text, label, select_own_idx=at_idx)

    def _tl_edit(self):
        sel = self._selected_depth0()
        if not sel:
            return
        label, own_idx = sel
        cmds = self._scripts.get(label, [])
        if own_idx >= len(cmds):
            return
        cmd = cmds[own_idx]
        new_cmd = self._command_editor_dialog(label, cmd)
        if new_cmd is None or new_cmd == cmd.raw:
            return
        new_text = rewrite_script_command(
            self._scripts_text, label, own_idx, new_cmd)
        self._apply_structural_edit(new_text, label, select_own_idx=own_idx)

    def _tl_delete(self):
        sel = self._selected_depth0()
        if not sel:
            return
        label, own_idx = sel
        cmds = self._scripts.get(label, [])
        if own_idx >= len(cmds):
            return
        raw = cmds[own_idx].raw
        if QMessageBox.question(
                self, "Delete Command",
                f"Delete this command from {label}?\n\n    {raw}",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        new_text = delete_script_command(self._scripts_text, label, own_idx)
        new_count = len(cmds) - 1
        sel_idx = min(own_idx, new_count - 1) if new_count > 0 else None
        self._apply_structural_edit(new_text, label, select_own_idx=sel_idx)

    def _tl_move(self, delta: int):
        sel = self._selected_depth0()
        if not sel:
            return
        label, own_idx = sel
        new_text = move_script_command(
            self._scripts_text, label, own_idx, delta)
        if new_text is None:
            return  # at an edge
        self._apply_structural_edit(
            new_text, label, select_own_idx=own_idx + delta)

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
        """Double-click a depth-0 row → open the command editor."""
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data or data[3] != 0:
            return  # inlined / read-only row
        self._tl_edit()

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

    # ── whole-move playback ───────────────────────────────────────────
    # Walks the resolved timeline by delay counts at 60 fps (1 tick ≈ 1 GBA
    # frame) — spawns sprite layers, fires sounds in order, cycles frames.
    # Positions are approximate (same caveat as the static composite).

    def _toggle_play_move(self):
        if self._playing:
            self._stop_play_move()
        else:
            self._start_play_move()

    def _start_play_move(self):
        """Begin the whole-move animation playback sequence.

        Pre-bakes all sprite frame lists so the render loop never hits disk.
        Stops the layer-scrubber timer so it doesn't fight the playback timer.
        """
        timeline = self._cur_timeline
        if not timeline:
            return
        # Stop anything that might conflict.
        self._layer_timer.stop()
        self._layer_play.setText("▶")
        # Pre-bake frame pixmaps for every createsprite tag in this timeline.
        self._play_frame_cache = {}
        for cmd in timeline:
            if cmd.name == "createsprite":
                cs = parse_createsprite(cmd)
                if cs:
                    tag = self._template_tags.get(cs.template, "")
                    if tag and tag not in self._play_frame_cache:
                        sprite = self._sprites.get(tag)
                        if sprite and sprite.png_exists:
                            frames = self._slice_frames(
                                sprite, self._palette_for(sprite))
                            if frames:
                                self._play_frame_cache[tag] = frames
        self._playing = True
        self._play_idx = 0
        self._play_wait = 0
        self._play_tick = 0
        self._play_layers = []
        self._tl_play_move_btn.setText("⏹  Stop")
        # Process the first batch of commands immediately.
        self._play_step()
        self._play_timer.start()

    def _stop_play_move(self):
        """Stop playback and reset the button label."""
        was_playing = self._playing
        self._play_timer.stop()
        self._playing = False
        self._play_layers = []
        self._tl_play_move_btn.setText("▶  Play Move")
        # Reset preview back to the static composite for the current row.
        # Guard against re-entrancy from the composite touching widgets.
        if was_playing:
            _log.debug("playback stopped at idx=%s tick=%s",
                       self._play_idx, self._play_tick)
        self._update_move_composite()

    def hideEvent(self, ev):
        """Stop the playback timer whenever the tab is hidden (project
        close, window switch) so it never fires against a dead view."""
        self._stop_play_move()
        super().hideEvent(ev)

    def _play_step(self):
        """Advance one 60-fps tick of the animation playback.

        Re-entrancy guarded (the sound callback can pump the Qt event loop,
        which would otherwise let the timer fire this slot again mid-tick and
        corrupt the layer list — a likely live-only crash).  Wrapped in
        try/except with a logged traceback so a Python error stops the timer
        cleanly instead of taking down the app.
        """
        if self._in_play_step:
            return
        self._in_play_step = True
        try:
            self._play_step_inner()
        except Exception:
            _log.exception("play_step crashed at idx=%s tick=%s",
                           getattr(self, "_play_idx", "?"),
                           getattr(self, "_play_tick", "?"))
            self._stop_play_move()
        finally:
            self._in_play_step = False

    def _fire_play_sound(self, se: str):
        """Fire a timeline sound during playback, throttled + guarded so a
        burst of sounds can't overwhelm / crash the audio backend."""
        if _preview_sound_cb is None or not se:
            return
        if self._play_tick - self._last_sound_tick < 3:
            return   # throttle overlapping previews (~20/sec max)
        self._last_sound_tick = self._play_tick
        try:
            _preview_sound_cb(se)
        except Exception:
            _log.exception("sound preview failed for %s", se)

    def _play_step_inner(self):
        timeline = self._cur_timeline
        if not timeline:
            self._stop_play_move()
            return

        self._play_tick += 1   # global frame clock (drives motion + cycling)
        # Cycle each live layer's frames (~15 fps) using the pre-baked cache.
        if self._play_tick % 4 == 0:
            for L in self._play_layers:
                frames = self._play_frame_cache.get(L[0], [])
                if len(frames) > 1:
                    L[5] = (L[5] + 1) % len(frames)   # L[5] = frame_idx

        # Mid-delay: just keep rendering (sprites keep moving toward target).
        if self._play_wait > 0:
            self._play_wait -= 1
            self._render_play_composite()
            return

        # Consume commands greedily until delay / terminal.
        while self._play_idx < len(timeline):
            cmd = timeline[self._play_idx]
            self._play_idx += 1

            # Advance the timeline cursor (signals suppressed during playback
            # so _on_timeline_row_changed won't fight us).
            row = self._play_idx - 1
            if row < self._timeline.count():
                self._timeline.blockSignals(True)
                self._timeline.setCurrentRow(row)
                self._timeline.blockSignals(False)
                self._timeline.scrollToItem(self._timeline.item(row))

            if cmd.name == "delay" and cmd.args:
                try:
                    n = int(cmd.args[0], 0)
                except ValueError:
                    n = 1
                self._play_wait = max(1, n)
                self._render_play_composite()
                return

            if cmd.name in ("end", "return"):
                self._render_play_composite()   # show final state
                self._stop_play_move()
                return

            if cmd.name == "createsprite":
                cs = parse_createsprite(cmd)
                if cs:
                    tag = self._template_tags.get(cs.template, "")
                    if tag and tag in self._play_frame_cache:
                        arch, start, end, dur, vis, flip = self._layer_geometry(cs)
                        if vis:
                            subpri = self._int_or(cs.subpriority)
                            # [tag,start,end,dur,spawn,frame_idx,arch,subpri,flip]
                            self._play_layers.append(
                                [tag, start, end, dur, self._play_tick, 0,
                                 arch, subpri, flip])

            if cmd.kind == KIND_SOUND and cmd.args:
                self._fire_play_sound(cmd.args[0])

        # Fell off the end without end/return — stop.
        self._render_play_composite()
        self._stop_play_move()

    @staticmethod
    def _apply_flip(pix: QPixmap, flip: bool) -> QPixmap:
        """Return a horizontally-mirrored copy when ``flip`` (side-dependent
        ST_OAM_HFLIP), else the pixmap unchanged."""
        if not flip:
            return pix
        from PyQt6.QtGui import QTransform
        return pix.transformed(QTransform().scale(-1, 1))

    def _render_play_composite(self):
        """Paint the current _play_layers onto the battle preview, each at
        its interpolated position for the current tick.  Uses pre-baked
        frames (no disk access).  Ordered by subpriority (lower on top)."""
        from PyQt6.QtGui import QPainter as _QPainter
        if not self._play_layers:
            self._move_preview.set_anim_pixmap(None)
            return
        P = self._move_preview
        canvas = QPixmap(P.CANVAS_W, P.CANVAS_H)
        canvas.fill(QColor(0, 0, 0, 0))
        painter = _QPainter(canvas)
        for L in sorted(self._play_layers, key=lambda L: -L[7]):  # subpri
            tag, start, end, dur, spawn, fidx, arch, _sp, flip = L
            frames = self._play_frame_cache.get(tag, [])
            if not frames:
                continue
            pix = self._apply_flip(
                frames[max(0, min(fidx, len(frames) - 1))], flip)
            cx, cy = self._interp_pos(start, end, dur, self._play_tick - spawn, arch)
            painter.drawPixmap(cx - pix.width() // 2,
                               cy - pix.height() // 2, pix)
        painter.end()
        self._move_preview.set_anim_pixmap(canvas, P.CANVAS_W // 2,
                                           P.CANVAS_H // 2)

    # ── layered composite preview ─────────────────────────────────────

    @staticmethod
    def _int_or(s, default=0):
        """Parse a C-style int literal (incl. negatives / 0x), else default
        (used for best-effort x/y/subpriority — many args are constants we
        can't evaluate without the engine)."""
        try:
            return int(str(s).strip(), 0)
        except (ValueError, TypeError):
            return default

    def _on_direction_changed(self):
        """Player/Enemy direction toggle: rebuild the composite + restart playback."""
        self._play_direction = "player" if self._dir_player_rb.isChecked() else "enemy"
        if self._playing:
            # Restart so the pre-baked positions use the new direction.
            self._stop_play_move()
            self._start_play_move()
        else:
            self._update_move_composite()

    def _anim_battlers(self):
        """Return ``((atk_cx,atk_cy),(tgt_cx,tgt_cy))`` for the current
        direction toggle.

        Battle animations run from BOTH sides — the same gBattleAnims_Moves
        entry is used whether the player or the enemy attacks; the engine
        binds attacker/target at runtime.  The toggle shows both cases:
          Player attacks → attacker = back/player (72,80), target = enemy (176,40)
          Enemy attacks  → attacker = front/enemy (176,40), target = player (72,80)
        """
        P = self._move_preview
        player = (P.PLAYER_CX, P.PLAYER_CY)
        enemy = (P.ENEMY_CX, P.ENEMY_CY)
        return (player, enemy) if self._play_direction == "player" else (enemy, player)

    def _archetype_for_template(self, template: str) -> str:
        """Motion archetype (MOTION_*) for a createsprite template, via its
        C callback.  Unrecognised callbacks → MOTION_UNKNOWN."""
        cb = self._tpl_callbacks.get(template, "")
        return self._callback_arch.get(cb, MOTION_UNKNOWN)

    # A few callbacks add a fixed pixel offset beyond the args (read from
    # source).  {callback: (dx_toward_target, dy)} — dx is signed toward the
    # target (matching the engine's side-dependent x handling).
    _CALLBACK_EXTRA_OFFSET = {
        "AnimCurseNail": (24, 0),   # nail sits 24px out from the attacker
    }

    def _layer_geometry(self, cs):
        """Resolve a createsprite into ``(archetype, start_xy, end_xy,
        duration, visible, flip)`` using the sprite callback's motion
        archetype + direction-aware battler anchors.

        Grounded in the engine's shared helpers: ``+arg0`` x-offset points
        from attacker toward target (SetAnimSpriteInitialXOffset); the
        common callbacks anchor at attacker/target and either sit still,
        ride to the target (arg2/arg3 end-offset over arg4 frames), or arc.
        ``flip`` mirrors the sprite horizontally — attacker-anchored sprites
        are H-flipped when the attacker is on the player side (back sprite),
        matching the engine's side-dependent ST_OAM_HFLIP (e.g. Curse nail).
        Positions are approximate — the long tail of bespoke callbacks falls
        back to a static placement at the declared battler.
        """
        atk, tgt = self._anim_battlers()
        xdir = 1 if tgt[0] >= atk[0] else -1
        a = [self._int_or(x) for x in cs.args]

        def arg(i):
            return a[i] if i < len(a) else 0

        cb = self._tpl_callbacks.get(cs.template, "")
        ex, ey = self._CALLBACK_EXTRA_OFFSET.get(cb, (0, 0))
        player_attacks = (self._play_direction == "player")

        arch = self._archetype_for_template(cs.template)
        if arch == MOTION_INVISIBLE:
            return (arch, (0, 0), (0, 0), 0, False, False)
        if arch in (MOTION_LINEAR_TO_TARGET, MOTION_ARC_TO_TARGET):
            start = (atk[0] + xdir * (arg(0) + ex), atk[1] + arg(1) + ey)
            end = (tgt[0] + xdir * arg(2), tgt[1] + arg(3))
            return (arch, start, end, max(1, arg(4)), True, player_attacks)
        if arch == MOTION_ON_MON_POS:
            on_attacker = (arg(2) == 0)
            anchor = atk if on_attacker else tgt
            pos = (anchor[0] + xdir * (arg(0) + ex), anchor[1] + arg(1) + ey)
            return (arch, pos, pos, 0, True, on_attacker and player_attacks)
        if arch == MOTION_STATIC_ATTACKER:
            pos = (atk[0] + xdir * (arg(0) + ex), atk[1] + arg(1) + ey)
            return (arch, pos, pos, 0, True, player_attacks)
        if arch == MOTION_STATIC_TARGET:
            pos = (tgt[0] + xdir * (arg(0) + ex), tgt[1] + arg(1) + ey)
            return (arch, pos, pos, 0, True, False)
        # UNKNOWN → static at the createsprite's declared battler (best guess).
        on_attacker = cs.battler.strip() in ("ANIM_ATTACKER", "ANIM_ATK_PARTNER")
        anchor = atk if on_attacker else tgt
        pos = (anchor[0] + xdir * (arg(0) + ex), anchor[1] + arg(1) + ey)
        return (MOTION_UNKNOWN, pos, pos, 0, True, on_attacker and player_attacks)

    def _interp_pos(self, start, end, dur, t, arch):
        """Interpolated position at tick ``t`` into a moving layer's life
        (linear, plus a parabolic rise for arc archetypes)."""
        if dur <= 0 or start == end:
            return start
        import math
        frac = max(0.0, min(1.0, t / dur))
        x = start[0] + (end[0] - start[0]) * frac
        y = start[1] + (end[1] - start[1]) * frac
        if arch == MOTION_ARC_TO_TARGET:
            y -= math.sin(math.pi * frac) * 24.0
        return (int(round(x)), int(round(y)))

    def _first_frame_for_tag(self, tag: str) -> Optional[QPixmap]:
        sprite = self._sprites.get(tag)
        if not sprite or not sprite.png_exists:
            return None
        frames = self._slice_frames(sprite, self._palette_for(sprite))
        return frames[0] if frames else None

    def _frame_for_tag(self, tag: str, idx: int) -> Optional[QPixmap]:
        sprite = self._sprites.get(tag)
        if not sprite or not sprite.png_exists:
            return None
        frames = self._slice_frames(sprite, self._palette_for(sprite))
        if not frames:
            return None
        return frames[max(0, min(idx, len(frames) - 1))]

    def _update_move_composite(self):
        """Static composite of every sprite spawned from the script start
        through the SELECTED timeline row, placed via its motion archetype
        (moving sprites shown at their END/impact point, static ones in
        place), layered by subpriority.  Invisible utility sprites (palette
        fades, mon-movers) are skipped; visual tasks are counted in the
        caption since they animate the mon directly and aren't drawn here."""
        from PyQt6.QtGui import QPainter
        timeline = self._cur_timeline
        through = self._timeline.currentRow()
        if through < 0:
            through = len(timeline) - 1
        layers = []          # (subpri, cx, cy, pixmap, tag)
        names = []
        n_tasks = n_hidden = n_approx = 0
        for i, cmd in enumerate(timeline[:through + 1] if timeline else []):
            if cmd.name in ("createvisualtask", "createsoundtask"):
                n_tasks += 1
                continue
            if cmd.name != "createsprite":
                continue
            cs = parse_createsprite(cmd)
            if not cs:
                continue
            tag = self._template_tags.get(cs.template, "")
            arch, start, end, dur, vis, flip = self._layer_geometry(cs)
            if not vis:
                n_hidden += 1
                continue
            # Selected row uses the scrubbed frame; others frame 0.
            if i == through and tag and tag == self._layer_tag:
                pix = self._frame_for_tag(tag, self._layer_idx)
            else:
                pix = self._first_frame_for_tag(tag)
            if pix is None:
                continue
            pix = self._apply_flip(pix, flip)
            # Representative position: impact (end) for moving sprites,
            # resting place for static ones.
            x, y = end if dur > 0 else start
            if arch == MOTION_UNKNOWN:
                n_approx += 1
            subpri = self._int_or(cs.subpriority)
            layers.append((subpri, x, y, pix, tag))
            names.append(self._sprites[tag].display_name)

        if not layers:
            self._move_preview.set_anim_pixmap(None)
            if n_tasks:
                self._move_prev_lbl.setText(
                    f"No drawn sprites yet — this move runs {n_tasks} visual "
                    f"task(s) that animate the mon directly (shake / flash / "
                    f"palette / background), which aren't reproduced here.")
            else:
                self._move_prev_lbl.setText(
                    "No sprite spawns up to here.")
            return

        # Higher subpriority draws first (further back); lower on top.
        layers.sort(key=lambda L: -L[0])
        P = self._move_preview
        canvas = QPixmap(P.CANVAS_W, P.CANVAS_H)
        canvas.fill(QColor(0, 0, 0, 0))
        painter = QPainter(canvas)
        for _sp, cx, cy, pix, _tag in layers:
            painter.drawPixmap(cx - pix.width() // 2,
                               cy - pix.height() // 2, pix)
        painter.end()
        self._move_preview.set_anim_pixmap(canvas, P.CANVAS_W // 2,
                                           P.CANVAS_H // 2)
        uniq = []
        for n in names:
            if n not in uniq:
                uniq.append(n)
        caption = (f"{len(layers)} sprite layer(s): "
                   + ", ".join(uniq[:5]) + ("…" if len(uniq) > 5 else ""))
        extra = []
        if n_tasks:
            extra.append(f"{n_tasks} visual task(s) (mon shake/flash, not drawn)")
        if n_approx:
            extra.append(f"{n_approx} ≈ approximate position")
        if extra:
            caption += "  ·  " + "  ·  ".join(extra)
        self._move_prev_lbl.setText(caption)

    # ── selected-sprite frame scrubber ────────────────────────────────

    def _reset_layer_scrubber(self):
        self._layer_timer.stop()
        self._layer_tag = ""
        self._layer_frames = []
        self._layer_idx = 0
        self._layer_slider.blockSignals(True)
        self._layer_slider.setMaximum(0)
        self._layer_slider.setValue(0)
        self._layer_slider.setEnabled(False)
        self._layer_slider.blockSignals(False)
        self._layer_play.setEnabled(False)
        self._layer_play.setText("▶")
        self._layer_edit_btn.setEnabled(False)
        self._layer_frame_lbl.setText("—")
        self._layer_lbl.setText(
            "Select a sprite-spawn row to inspect its frames.")

    def _load_layer_scrubber(self, tag: str):
        """Point the frame scrubber at the given sprite's frames."""
        self._layer_timer.stop()
        self._layer_play.setText("▶")
        sprite = self._sprites.get(tag) if tag else None
        frames = (self._slice_frames(sprite, self._palette_for(sprite))
                  if sprite and sprite.png_exists else [])
        if not frames:
            self._reset_layer_scrubber()
            return
        self._layer_tag = tag
        self._layer_frames = frames
        self._layer_idx = 0
        n = len(frames)
        self._layer_slider.blockSignals(True)
        self._layer_slider.setMaximum(n - 1)
        self._layer_slider.setValue(0)
        self._layer_slider.setEnabled(n > 1)
        self._layer_slider.blockSignals(False)
        self._layer_play.setEnabled(n > 1)
        self._layer_edit_btn.setEnabled(True)
        self._layer_frame_lbl.setText(f"1 / {n}")
        exact = " (exact)" if tag in self._frame_sizes else " (approx)"
        self._layer_lbl.setText(
            f"<b>{sprite.display_name}</b>  ({tag}) — {n} frame(s){exact}")

    def _on_layer_scrub(self, value: int):
        self._layer_idx = value
        n = len(self._layer_frames)
        if n:
            self._layer_frame_lbl.setText(f"{value + 1} / {n}")
        self._update_move_composite()

    def _toggle_layer_play(self):
        if self._layer_timer.isActive():
            self._layer_timer.stop()
            self._layer_play.setText("▶")
        elif len(self._layer_frames) > 1:
            self._layer_timer.start()
            self._layer_play.setText("⏸")

    def _advance_layer_frame(self):
        n = len(self._layer_frames)
        if n <= 1:
            self._layer_timer.stop()
            return
        self._layer_idx = (self._layer_idx + 1) % n
        self._layer_slider.blockSignals(True)
        self._layer_slider.setValue(self._layer_idx)
        self._layer_slider.blockSignals(False)
        self._layer_frame_lbl.setText(f"{self._layer_idx + 1} / {n}")
        self._update_move_composite()

    def _edit_selected_layer_sprite(self):
        """Jump to the Sprites sub-tab and select this layer's sprite so
        the user can edit its image + palette."""
        if not self._layer_tag:
            return
        self._subtabs.setCurrentIndex(0)
        self._select_sprite_by_tag(self._layer_tag)

    # ── command editor dialog (add / edit any opcode) ─────────────────

    # Display label -> opcode, for the command-type chooser.
    _CMD_TYPES = [
        ("Spawn sprite", "createsprite"),
        ("Play sound (with pan)", "playsewithpan"),
        ("Play sound", "playse"),
        ("Wait / delay", "delay"),
        ("Load sprite graphics", "loadspritegfx"),
        ("Unload sprite graphics", "unloadspritegfx"),
        ("Visual task", "createvisualtask"),
        ("Wait for visuals to finish", "waitforvisualfinish"),
        ("End animation", "end"),
        ("Raw command…", "__raw__"),
    ]

    def _command_editor_dialog(self, label: str, cmd) -> Optional[str]:
        """Add (``cmd=None``) or edit a single command.  Returns the new
        command source string, or ``None`` if cancelled.

        A command-type chooser drives a stacked set of field panels:
        createsprite gets a real sprite picker + battler + layer + x/y;
        sounds reuse the existing sound editor; everything else has a
        focused panel, with a raw-text fallback for any opcode."""
        dlg = QDialog(self)
        dlg.setWindowTitle("Edit Command" if cmd else "Add Command")
        dlg.setMinimumWidth(440)
        outer = QVBoxLayout(dlg)

        type_combo = QComboBox()
        for disp, _op in self._CMD_TYPES:
            type_combo.addItem(disp)
        type_combo.wheelEvent = lambda e: e.ignore()
        trow = QFormLayout()
        trow.addRow("Command type:", type_combo)
        outer.addLayout(trow)

        stack = QStackedWidget()
        outer.addWidget(stack)

        # Map opcode -> (build_panel() -> (widget, getter)).
        panels = {}

        def add_panel(op, widget, getter):
            idx = stack.addWidget(widget)
            panels[op] = (idx, getter)

        # — createsprite —
        cs_w = QWidget()
        cs_f = QFormLayout(cs_w)
        cs_tpl = QComboBox()
        cs_tpl.setEditable(True)
        cs_tpl.addItems(sorted(self._template_tags.keys()))
        cs_tpl.wheelEvent = lambda e: e.ignore()
        cs_battler = QComboBox()
        cs_battler.addItems(["ANIM_TARGET", "ANIM_ATTACKER",
                             "ANIM_ATK_PARTNER", "ANIM_DEF_PARTNER"])
        cs_battler.wheelEvent = lambda e: e.ignore()
        cs_subpri = QSpinBox()
        cs_subpri.setRange(0, 127)
        cs_subpri.setValue(2)
        cs_subpri.wheelEvent = lambda e: e.ignore()
        cs_args = QLineEdit()
        cs_args.setPlaceholderText("e.g.  20, 0, -16, 24, 20, 1")
        cs_prev = QLabel()
        cs_prev.setFixedHeight(40)
        cs_prev.setStyleSheet("color:#7cc; font-size:10px;")

        def _cs_preview():
            tag = self._template_tags.get(cs_tpl.currentText().strip(), "")
            if tag and tag in self._sprites:
                cs_prev.setText(f"→ {self._sprites[tag].display_name}  ({tag})")
            else:
                cs_prev.setText("→ (template not recognised — raw is fine too)")
        cs_tpl.currentTextChanged.connect(lambda _t: _cs_preview())
        cs_f.addRow("Sprite template:", cs_tpl)
        cs_f.addRow("Anchored to:", cs_battler)
        cs_f.addRow("Layer (subpriority):", cs_subpri)
        cs_f.addRow("Args (x, y, …):", cs_args)
        cs_f.addRow("", cs_prev)

        def cs_get():
            tpl = cs_tpl.currentText().strip()
            if not tpl:
                return None
            args = [a.strip() for a in cs_args.text().split(",") if a.strip()]
            return format_createsprite(CreateSpriteCmd(
                template=tpl, battler=cs_battler.currentText().strip(),
                subpriority=str(cs_subpri.value()), args=args))
        add_panel("createsprite", cs_w, cs_get)

        # — loadspritegfx / unloadspritegfx (shared tag picker) —
        def make_gfx_panel(op):
            w = QWidget()
            f = QFormLayout(w)
            combo = QComboBox()
            combo.setEditable(True)
            combo.addItems(sorted(self._sprites.keys()))
            combo.wheelEvent = lambda e: e.ignore()
            f.addRow("Sprite tag:", combo)
            return w, combo, (lambda: (f"{op} {combo.currentText().strip()}"
                                       if combo.currentText().strip() else None))
        lg_w, lg_combo, lg_get = make_gfx_panel("loadspritegfx")
        add_panel("loadspritegfx", lg_w, lg_get)
        ug_w, ug_combo, ug_get = make_gfx_panel("unloadspritegfx")
        add_panel("unloadspritegfx", ug_w, ug_get)

        # — createvisualtask —
        vt_w = QWidget()
        vt_f = QFormLayout(vt_w)
        vt_addr = QLineEdit()
        vt_addr.setPlaceholderText("AnimTask_…")
        vt_pri = QSpinBox()
        vt_pri.setRange(0, 15)
        vt_pri.setValue(2)
        vt_pri.wheelEvent = lambda e: e.ignore()
        vt_args = QLineEdit()
        vt_args.setPlaceholderText("optional args, comma-separated")
        vt_f.addRow("Task function:", vt_addr)
        vt_f.addRow("Priority:", vt_pri)
        vt_f.addRow("Args:", vt_args)

        def vt_get():
            addr = vt_addr.text().strip()
            if not addr:
                return None
            from core.battle_anim_script import CreateVisualTaskCmd
            args = [a.strip() for a in vt_args.text().split(",") if a.strip()]
            return format_createvisualtask(CreateVisualTaskCmd(
                addr=addr, priority=str(vt_pri.value()), args=args))
        add_panel("createvisualtask", vt_w, vt_get)

        # — no-field control opcodes —
        for op, msg in (("waitforvisualfinish",
                         "Pauses the script until all visual tasks finish."),
                        ("end", "Ends the move's animation.")):
            w = QWidget()
            v = QVBoxLayout(w)
            lbl = QLabel(msg)
            lbl.setWordWrap(True)
            lbl.setStyleSheet("color:#aaa;")
            v.addWidget(lbl)
            v.addStretch(1)
            add_panel(op, w, (lambda o=op: o))

        # — raw fallback —
        raw_w = QWidget()
        raw_f = QFormLayout(raw_w)
        raw_edit = QLineEdit()
        raw_edit.setPlaceholderText("opcode arg1, arg2, …")
        raw_f.addRow("Raw command:", raw_edit)
        add_panel("__raw__", raw_w, lambda: raw_edit.text().strip() or None)

        # Sound + delay panels defer to their dedicated dialogs on accept
        # (sound = SE picker with preview; delay = frames spinbox).  Show a
        # small hint panel here so the chooser stays consistent.
        for op, hint in (
                ("playsewithpan", "Press OK to choose the sound effect (with preview)."),
                ("playse", "Press OK to choose the sound effect (with preview)."),
                ("delay", "Press OK to set the wait duration in frames.")):
            w = QWidget()
            v = QVBoxLayout(w)
            lbl = QLabel(hint)
            lbl.setWordWrap(True)
            lbl.setStyleSheet("color:#aaa;")
            v.addWidget(lbl)
            v.addStretch(1)
            add_panel(op, w, (lambda o=op: o))   # placeholder; real value via dedicated dlg

        def _switch(disp_idx):
            op = self._CMD_TYPES[disp_idx][1]
            stack.setCurrentIndex(panels[op][0])
        type_combo.currentIndexChanged.connect(_switch)

        # Pre-fill from the command being edited.
        sound_seed = cmd if (cmd and cmd.kind == KIND_SOUND) else None
        delay_seed = cmd if (cmd and cmd.kind == KIND_DELAY) else None
        if cmd:
            op = cmd.name
            disp_idx = next((i for i, (_d, o) in enumerate(self._CMD_TYPES)
                             if o == op), None)
            if op == "createsprite":
                cs = parse_createsprite(cmd)
                if cs:
                    i = cs_tpl.findText(cs.template)
                    if i >= 0:
                        cs_tpl.setCurrentIndex(i)
                    else:
                        cs_tpl.setEditText(cs.template)
                    j = cs_battler.findText(cs.battler)
                    if j >= 0:
                        cs_battler.setCurrentIndex(j)
                    else:
                        cs_battler.setEditText(cs.battler)
                    cs_subpri.setValue(self._int_or(cs.subpriority, 2))
                    cs_args.setText(", ".join(cs.args))
            elif op in ("loadspritegfx", "unloadspritegfx"):
                combo = lg_combo if op == "loadspritegfx" else ug_combo
                if cmd.args:
                    k = combo.findText(cmd.args[0])
                    if k >= 0:
                        combo.setCurrentIndex(k)
                    else:
                        combo.setEditText(cmd.args[0])
            elif op == "createvisualtask":
                t = parse_createvisualtask(cmd)
                if t:
                    vt_addr.setText(t.addr)
                    vt_pri.setValue(self._int_or(t.priority, 2))
                    vt_args.setText(", ".join(t.args))
            elif op == "delay":
                pass  # handled by the dedicated dialog on accept
            elif op in ("playse", "playsewithpan"):
                pass  # handled by the dedicated dialog on accept
            elif disp_idx is None:
                # Unknown opcode → raw editor seeded with the raw line.
                disp_idx = len(self._CMD_TYPES) - 1
                raw_edit.setText(cmd.raw)
            if disp_idx is not None:
                type_combo.setCurrentIndex(disp_idx)
                _switch(disp_idx)
        else:
            type_combo.setCurrentIndex(0)
            _switch(0)
        _cs_preview()

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        outer.addWidget(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None

        op = self._CMD_TYPES[type_combo.currentIndex()][1]
        # Sounds + delay defer to their dedicated dialogs (preview / spinbox).
        if op in ("playse", "playsewithpan"):
            seed = sound_seed
            if seed is None:
                # Build a minimal seed command of the right shape.
                from core.battle_anim_script import Command
                args = (["SE_SELECT", "SOUND_PAN_TARGET"]
                        if op == "playsewithpan" else ["SE_SELECT"])
                seed = Command(name=op, args=args, kind=KIND_SOUND)
            return self._edit_sound_dialog(seed)
        if op == "delay":
            seed = delay_seed
            if seed is None:
                from core.battle_anim_script import Command
                seed = Command(name="delay", args=["1"], kind=KIND_DELAY)
            return self._edit_delay_dialog(seed)
        getter = panels[op][1]
        return getter()

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
                # Clear per-move amber markers + refresh the live header.
                self._dirty_moves.clear()
                for i in range(self._move_list.count()):
                    self._move_list.item(i).setBackground(QColor(0, 0, 0, 0))
                if self._move_current:
                    self._populate_timeline(self._move_current)
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
