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
import time
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
from PyQt6.QtGui import QColor, QImage, QPixmap, qRgb, qRgba
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
    MOTION_AT_TARGET, MOTION_AT_ATTACKER,
    MOTION_LINEAR_TO_TARGET, MOTION_ARC_TO_TARGET, MOTION_INVISIBLE,
    MOTION_UNKNOWN,
    BattleAnimSprite)
from core.battle_anim_script import (
    parse_move_anim_table, parse_anim_scripts, resolve_timeline,
    find_anim_branches, parse_named_anim_table,
    parse_move_names, move_display_name, parse_sound_effects,
    rewrite_script_command, format_command,
    insert_script_command, delete_script_command, move_script_command,
    parse_createsprite, format_createsprite, CreateSpriteCmd,
    parse_createvisualtask, format_createvisualtask,
    KIND_SOUND, KIND_SPRITE, KIND_TASK, KIND_DELAY, KIND_GFX, KIND_CONTROL,
)
from core.battle_anim_vm import (
    AnimSim, AnimContext, Battler, spawn as vm_spawn, is_ported as vm_is_ported,
    new_sprite as vm_new_sprite, setup_static as vm_setup_static,
    setup_linear as vm_setup_linear, setup_arc as vm_setup_arc,
    SIDE_PLAYER, SIDE_OPPONENT)
from core.battle_anim_tasks import (
    MonTaskSim, TaskCtx, ATTACKER as TASK_ATTACKER, TARGET as TASK_TARGET,
    is_mon_task, is_mon_mover_template)
from core.sprite_render import load_sprite_pixmap
from core.sprite_palette_bus import get_bus as _get_palette_bus, CAT_BATTLE_ANIM
from core.overworld_palette_io import write_palette_pair
from ui.draggable_palette_row import DraggablePaletteRow
from ui.palette_utils import read_jasc_pal, clamp_to_gba

Color = Tuple[int, int, int]

# Battler-selector constants passed as createsprite/task args (their real
# engine values).  Callbacks branch on these (e.g. arg2 == 0 → attacker), so
# parsing them as their values — not 0 — is essential for correct anchoring.
_ANIM_ARG_CONSTS = {
    "ANIM_ATTACKER": 0,
    "ANIM_TARGET": 1,
    "ANIM_ATK_PARTNER": 2,
    "ANIM_DEF_PARTNER": 3,
    "TRUE": 1,
    "FALSE": 0,
}

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
_preview_sound_prepare_cb = None  # Callable[[str], None] — warm the SE PCM cache
_preview_cry_cb = None     # Callable[[str], bool]  — SPECIES_ const -> play cry
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
        # Reference-mon provider (set by the host once species data is loaded):
        self._mon_dirs: Dict[str, str] = {}                 # slug -> graphics/pokemon/<slug> dir
        self._species_name_override: Dict[str, str] = {}    # slug -> project display name
        self._gfx_data = None                               # GraphicsDataCache: per-species y-offsets/elevation
        self._cur_timeline: list = []                       # resolved Commands of selected move
        self._dirty_moves: set = set()                      # labels with unsaved edits (amber)
        self._branch_choice: int = 0                        # choosetwoturnanim variant index
        # Non-move animation tables (status conditions, general, special) —
        # {category: [(display_name, script_label)]}.  "Moves" is built from
        # the move table + gMoveNames separately.
        self._anim_tables: Dict[str, list] = {}
        self._anim_entries: list = []                       # current category's (display, label) entries

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
        self._last_sound_se = ""       # last SE fired (suppress immediate repeats)
        self._play_layers: list = []   # [tag, cx, cy, subpri, frame_idx]
        self._anim_sim = None          # core.battle_anim_vm.AnimSim during playback
        self._mon_task_sim = None      # core.battle_anim_tasks.MonTaskSim (mon shake/sway/squeeze)
        self._wait_visual = False      # blocking on waitforvisualfinish?
        self._play_frame_cache: Dict[str, List[QPixmap]] = {}  # pre-baked at start
        self._play_flip_cache: Dict[str, List[QPixmap]] = {}   # H-flipped frames, baked once
        self._play_direction = "player"  # "player" = player attacks; "enemy" = enemy attacks
        self._play_timer = QTimer(self)
        self._play_timer.setInterval(33)   # ~30 fps render (Remote-Desktop friendly)
        self._play_timer.timeout.connect(self._play_step)
        self._play_last_ms = 0.0           # wall-clock anchor for real-time pacing
        self._visual_wait = 0              # frames spent in a waitforvisualfinish
        # Native engine playback (the real game animation code via WASM).
        self._anim_engine = None           # core.battle_anim_engine.AnimEngine (lazy)
        self._anim_engine_failed = False   # tried + unavailable (don't retry every play)
        self._engine_warned = False        # showed the "engine not installed" hint once
        self._engine_play = False          # this playback is engine-driven
        self._engine_frames: list = []     # precomputed per-frame OAM snapshots
        self._engine_idx = 0               # current frame in _engine_frames
        self._engine_sounds: list = []     # [(frame_idx, SE)] schedule for playback
        self._engine_sound_ptr = 0         # next un-fired sound in _engine_sounds
        self._engine_bgscroll: list = []   # [(x,y)] BG scroll per frame
        self._engine_bg_pix = None         # assembled anim-BG QPixmap for this move
        self._bg_id_map = None             # BG id -> (img,pal,tilemap) paths (lazy)
        self._bg_pix_cache: dict = {}      # BG id -> assembled QPixmap (cache)

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

        # ── LEFT: animation category + searchable list ──
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        # Category: move animations vs the non-move tables (status conditions,
        # general effects, special) — all live in data/battle_anim_scripts.s.
        cat_row = QHBoxLayout()
        cat_row.addWidget(QLabel("Show:"))
        self._anim_cat_combo = QComboBox()
        self._anim_cat_combo.addItems(
            ["Moves", "Status Conditions", "General", "Special"])
        self._anim_cat_combo.wheelEvent = lambda e: e.ignore()
        self._anim_cat_combo.currentIndexChanged.connect(
            lambda _i: self._on_anim_category_changed())
        cat_row.addWidget(self._anim_cat_combo, 1)
        lv.addLayout(cat_row)
        self._move_search = QLineEdit()
        self._move_search.setPlaceholderText("Search…")
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
        # Reference Pokémon — drop real battle sprites onto the scene so you
        # can judge anim placement against the actual mons (player = back
        # sprite at bottom-left, enemy = front sprite at top-right).
        ref_row = QHBoxLayout()
        ref_row.setSpacing(6)
        self._ref_player_combo = QComboBox()
        self._ref_enemy_combo = QComboBox()
        for c in (self._ref_player_combo, self._ref_enemy_combo):
            c.setEnabled(False)
            c.wheelEvent = lambda e: e.ignore()   # no scroll-to-change (RDP safety)
            c.currentIndexChanged.connect(lambda _i: self._apply_ref_mons())
        ref_row.addWidget(QLabel("Player:"))
        ref_row.addWidget(self._ref_player_combo, 1)
        ref_row.addWidget(QLabel("Enemy:"))
        ref_row.addWidget(self._ref_enemy_combo, 1)
        pv.addLayout(ref_row)
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

        # ── Variant picker (choosetwoturnanim — e.g. Curse Ghost vs Stats) ──
        self._variant_row = QWidget()
        _vr = QHBoxLayout(self._variant_row)
        _vr.setContentsMargins(0, 0, 0, 0)
        _vr.addWidget(QLabel("Variant:"))
        self._variant_combo = QComboBox()
        self._variant_combo.wheelEvent = lambda e: e.ignore()
        self._variant_combo.currentIndexChanged.connect(self._on_variant_changed)
        _vr.addWidget(self._variant_combo, 1)
        _vr.addStretch(1)
        self._variant_row.setVisible(False)
        tv.addWidget(self._variant_row)

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
        # Cry moves play the selected mon's cry from sound/.../cries/<slug>.wav —
        # make sure the shared cry player knows this project (the Pokemon tab
        # sets it too, but the user may reach Battle Anims first).
        try:
            from ui.audio_player import get_audio_player
            get_audio_player().set_project_root(project_root)
        except Exception:
            pass
        # Numeric ANIM_TAG_* value → name, so TASK-spawned sprites (Hail,
        # Sandstorm, …) — which carry only the numeric tileTag, no host template
        # index — can be mapped to their gfx and rendered.
        self._tag_by_value = self._parse_tag_values(project_root)
        # RGB_* / RGB(r,g,b) colour constants the blend/fade tasks take as args
        # (RGB_WHITE flash, RGB_BLACK dark blend, …) so tints get the right hue.
        self._rgb_consts = self._parse_rgb_consts(project_root)

        # In-memory reset.
        self._palettes.clear()
        self._palette_dirty.clear()
        self._cards.clear()
        self._current = None
        self._frames = []
        self._frame_idx = 0
        self._move_current = None
        self._branch_choice = 0
        if hasattr(self, "_variant_row"):
            self._variant_row.setVisible(False)

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
            self._anim_tables = {
                "Status Conditions": parse_named_anim_table(
                    project_root, "gBattleAnims_StatusConditions"),
                "General": parse_named_anim_table(
                    project_root, "gBattleAnims_General"),
                "Special": parse_named_anim_table(
                    project_root, "gBattleAnims_Special"),
            }
            self._scripts = parse_anim_scripts(project_root)
            self._move_names = parse_move_names(project_root)
            self._sound_effects = parse_sound_effects(project_root)
            self._scripts_text = self._read_scripts_text(project_root)
            self._template_tags = parse_template_tags(project_root)
            self._tpl_callbacks = parse_template_callbacks(project_root)
            self._callback_arch = classify_anim_callbacks(project_root)
            # Per-species sprite y-offsets + elevation — the SAME data the
            # Pokemon Graphics tab uses to position mons, so the reference
            # mons here sit exactly where they do there (and in-game).
            try:
                from ui.graphics_data import GraphicsDataCache
                self._gfx_data = GraphicsDataCache(project_root)
                self._gfx_data.load()
            except Exception:
                self._gfx_data = None
        except Exception:
            self._move_table, self._scripts, self._move_names = [], {}, {}
            self._sound_effects, self._scripts_text = [], ""
            self._template_tags = {}
            self._tpl_callbacks = {}
            self._callback_arch = {}
            self._anim_tables = {}

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
        # Reset category to Moves on (re)load, then build the entry list.
        if hasattr(self, "_anim_cat_combo"):
            self._anim_cat_combo.blockSignals(True)
            self._anim_cat_combo.setCurrentIndex(0)
            self._anim_cat_combo.blockSignals(False)
        self._refresh_anim_entries()
        self._rebuild_move_list()
        # Reference mons: self-resolve from the project's graphics/pokemon
        # tree on every load / F5 (no host wiring needed), then push to the
        # preview so real battle sprites sit behind the animation.
        self._populate_ref_combos()

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

    def _refresh_anim_entries(self):
        """Build ``self._anim_entries`` = ``[(display_name, script_label)]``
        for the currently-selected category (Moves / Status / General /
        Special), keeping only entries that have a parsed script."""
        cat = (self._anim_cat_combo.currentText()
               if hasattr(self, "_anim_cat_combo") else "Moves")
        entries = []
        if cat == "Moves":
            for label in self._move_table:
                if label in self._scripts:
                    entries.append(
                        (move_display_name(label, self._move_names), label))
        else:
            for name, label in self._anim_tables.get(cat, []):
                if label in self._scripts:
                    entries.append((name, label))
        # Moves read best alphabetical; the small named tables keep their
        # source order (poison, confusion, burn… — meaningful grouping).
        if cat == "Moves":
            entries.sort(key=lambda e: (e[0].lower(), e[1]))
        self._anim_entries = entries

    def _on_anim_category_changed(self):
        self._stop_play_move()
        self._move_current = None
        self._branch_choice = 0
        self._refresh_anim_entries()
        self._rebuild_move_list()
        self._timeline.clear()
        self._update_edit_buttons()

    def _rebuild_move_list(self):
        """Populate the list for the current category, filtered by search."""
        self._move_list.blockSignals(True)
        self._move_list.clear()
        needle = self._move_search.text().strip().lower()
        for name, label in self._anim_entries:
            if needle and needle not in name.lower() and needle not in label.lower():
                continue
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, label)
            item.setToolTip(label)
            # Per-item amber for entries with unsaved timeline edits (Pattern A).
            if label in self._dirty_moves:
                item.setBackground(QColor("#3d2e00"))
            self._move_list.addItem(item)
            if label == self._move_current:
                self._move_list.setCurrentItem(item)
        shown = self._move_list.count()
        cat = self._anim_cat_combo.currentText() if hasattr(self, "_anim_cat_combo") else ""
        self._move_count_lbl.setText(f"{shown} {cat.lower() or 'entries'}")
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
        self._branch_choice = 0          # reset variant on move change
        self._refresh_variant_combo(label)
        self._populate_timeline(label)

    def _refresh_variant_combo(self, label: str):
        """Show a variant picker when the move branches via
        choosetwoturnanim (e.g. Curse's Ghost vs Stats version)."""
        branches = find_anim_branches(self._scripts, label)
        self._variant_combo.blockSignals(True)
        self._variant_combo.clear()
        if len(branches) >= 2:
            for i, target in enumerate(branches):
                self._variant_combo.addItem(f"{i + 1}: {target}", i)
            self._variant_combo.setCurrentIndex(
                min(self._branch_choice, len(branches) - 1))
            self._variant_row.setVisible(True)
        else:
            self._variant_row.setVisible(False)
        self._variant_combo.blockSignals(False)

    def _on_variant_changed(self, idx: int):
        if idx < 0 or not self._move_current:
            return
        self._branch_choice = idx
        self._stop_play_move()
        self._populate_timeline(self._move_current)

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
        timeline = resolve_timeline(self._scripts, label, inline_calls=True,
                                    branch_choice=self._branch_choice)
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

    def _get_anim_engine(self):
        """Lazily load the native animation engine (real game code via WASM).
        Returns None if wasmtime / the artifact isn't installed (the VM path
        then takes over, and P53 will surface a Setup prompt)."""
        if self._anim_engine is not None:
            return self._anim_engine
        if self._anim_engine_failed:
            return None
        try:
            from core.battle_anim_engine import AnimEngine
            self._anim_engine = AnimEngine()
            _log.debug("native anim engine loaded")
            return self._anim_engine
        except Exception as e:
            _log.warning("native anim engine unavailable (%s) — using VM fallback", e)
            self._anim_engine_failed = True
            return None

    def _build_engine_ops(self, timeline) -> list:
        """Convert the resolved timeline into the engine's op list (the same
        commands the game's script interpreter would run)."""
        ops = []
        for c in timeline:
            if c.name == "createsprite":
                cs = parse_createsprite(c)
                if cs:
                    ops.append({"op": "createsprite", "template": cs.template,
                                "battler": self._int_or(cs.battler),
                                "subpriority": self._int_or(cs.subpriority),
                                "args": [self._arg_val(a) for a in cs.args]})
            elif c.name == "createvisualtask":
                vt = parse_createvisualtask(c)
                if vt:
                    args = [self._arg_val(a) for a in vt.args]
                    ops.append({"op": "createvisualtask", "func": vt.addr,
                                "args": args})
                    # A cry sound task (Growl/Roar = PlayDoubleCry, Howl =
                    # PlayCryWithEcho, Metal Sound = PlayCryHighPitch, …):
                    # schedule the selected mon's cry. Every cry move plays the
                    # ATTACKER's cry, so play that.
                    if (vt.addr.startswith("SoundTask_Play")
                            and "Cry" in vt.addr):
                        ops.append({"op": "sound", "se": "__CRY__"})
            elif c.name == "delay":
                ops.append({"op": "delay",
                            "frames": self._int_or(c.args[0]) if c.args else 1})
            elif getattr(c, "kind", None) == KIND_SOUND and c.args:
                # Sound effect at this point in the timeline (playsewithpan etc).
                ops.append({"op": "sound", "se": c.args[0]})
            elif c.name in ("waitforvisualfinish", "waitsound"):
                ops.append({"op": "waitforvisualfinish"})
            elif c.name in ("end", "return"):
                ops.append({"op": "end"})
        return ops

    def _detect_move_bg(self, timeline):
        """The move's animation background id from its first fadetobg / changebg /
        fadetobgfromset, or a "task:<fn>" id for a BG-loading task (Surf). None
        if the move has no background."""
        from core.battle_anim_bg import task_loads_bg
        for c in timeline:
            if c.name in ("fadetobg", "changebg", "fadetobgfromset") and c.args:
                return c.args[0].strip()
            if c.name == "createvisualtask" and c.args and task_loads_bg(c.args[0]):
                return "task:" + c.args[0]
        return None

    def _load_bg_pixmap(self, bg_id: str):
        """Assemble (and cache) the background image for a BG id."""
        # Task-loaded BGs (Surf) depend on attack direction — cache per direction.
        cache_key = bg_id + ("|" + self._play_direction if bg_id.startswith("task:") else "")
        if cache_key in self._bg_pix_cache:
            return self._bg_pix_cache[cache_key]
        pix = None
        try:
            from core.battle_anim_bg import parse_bg_map, assemble_bg, assemble_task_bg
            if bg_id.startswith("task:"):
                pix = assemble_task_bg(self._project_root or "", bg_id[5:],
                                       player_attacks=(self._play_direction == "player"))
            else:
                if self._bg_id_map is None:
                    self._bg_id_map = parse_bg_map(self._project_root or "")
                files = self._bg_id_map.get(bg_id)
                if files:
                    pix = assemble_bg(*files)
        except Exception:
            _log.exception("anim BG assemble failed for %s", bg_id)
            pix = None
        self._bg_pix_cache[cache_key] = pix
        return pix

    def _set_engine_bg(self, idx: int):
        """Push the anim background (scrolled to this frame) to the preview."""
        if self._engine_bg_pix is None:
            self._move_preview.set_anim_bg(None)
            return
        if 0 <= idx < len(self._engine_bgscroll):
            sx, sy = self._engine_bgscroll[idx]
        else:
            sx, sy = 0, 0
        self._move_preview.set_anim_bg(self._engine_bg_pix, sx, sy)

    @staticmethod
    def _oam_scale(m, affine):
        """Display scale from an OAM matrix component (256 = 1.0×). If the value
        is out of a sane range, IGNORE the affine (return 1.0) — a broken affine
        anim (e.g. residual GrowAndShrink garbage) must never explode a sprite to
        an absurd size. Bound: ~0.125×..8× (|m| in 32..2048)."""
        if not affine or not m:
            return 1.0
        a = abs(m)
        if a < 32 or a > 2048:
            return 1.0
        return 256.0 / a

    @staticmethod
    def _affine_transform(mA, mB, mC, mD):
        """Full OAM affine matrix → a QTransform to draw the sprite with, so
        flip (negative scale), scale, AND rotation all render faithfully (Bite's
        flipped jaw, Crunch's rotated teeth, Fly's stretch). The OAM matrix maps
        screen→texture, so we draw with its INVERSE. Returns None for identity
        or a garbage matrix (residual GrowAndShrink: |component| huge) so a
        broken affine can't explode the sprite."""
        if mA == 256 and mB == 0 and mC == 0 and mD == 256:
            return None                       # identity — draw as-is
        if any(abs(v) > 4096 for v in (mA, mB, mC, mD)):
            return None                       # garbage — ignore the affine
        from PyQt6.QtGui import QTransform
        # OAM (screen→texture): QTransform(m11,m12,m21,m22) maps (i,j)->
        # ((mA i + mB j)/256, (mC i + mD j)/256). Drawing needs the inverse.
        oam = QTransform(mA / 256.0, mC / 256.0, mB / 256.0, mD / 256.0, 0.0, 0.0)
        inv, ok = oam.inverted()
        return inv if ok else None

    def _mon_sink_descent(self, which, invisible):
        """Pixels the attacker mon should descend this frame for a Dig-style
        burrow, or None. The mon must be the ATTACKER and currently hidden, and
        its BG layer (player→BG2, enemy→BG1, per GetBattlerSpriteBGPriorityRank)
        must be scrolled negative — that's the engine wiggling the mon-on-BG down
        into the hole. descent = -BGy (BGy goes negative as the mon sinks)."""
        if not invisible:
            return None
        if self._play_direction == "player":
            if which != "back":
                return None
            scroll = getattr(self, "_engine_bg2scroll", None)
        else:
            if which != "front":
                return None
            scroll = getattr(self, "_engine_bgscroll", None)
        idx = self._engine_idx
        if scroll and 0 <= idx < len(scroll):
            y = scroll[idx][1]
            ys = y - 65536 if y >= 32768 else y     # signed
            if ys < 0:
                return -ys                           # descend downward
        return None

    # (shape, size) → (width_tiles, height_tiles). GBA OAM dimensions.
    _OAM_TILE_DIMS = {
        (0, 0): (1, 1), (0, 1): (2, 2), (0, 2): (4, 4), (0, 3): (8, 8),  # square
        (1, 0): (2, 1), (1, 1): (4, 1), (1, 2): (4, 2), (1, 3): (8, 4),  # wide
        (2, 0): (1, 2), (2, 1): (1, 4), (2, 2): (2, 4), (2, 3): (4, 8),  # tall
    }

    def _tiles_path_for(self, sprite):
        """The combined ``.4bpp`` tile sheet for a sprite whose PNG source is
        SPLIT into numbered per-frame files (ice_crystals_0.png, _1.png, … with
        no single ice_crystals.png). The .4bpp is the authoritative combined
        sheet the engine indexes by tile, so we render straight from it. Returns
        "" when there's no usable .4bpp."""
        if not sprite or not sprite.png_path or not sprite.png_path.endswith(".png"):
            return ""
        cand = sprite.png_path[:-4] + ".4bpp"
        return cand if os.path.isfile(cand) else ""

    def _render_4bpp_frame(self, sprite, palette, tile_num, shape, size,
                           hflip, vflip):
        """Render one OAM frame straight from the combined ``.4bpp`` tile stream
        (for split-PNG sprites: ice, spark, mud_sand, …). ``tile_num`` is the
        frame's start tile (the engine's frame-relative tileNum); the OAM
        (shape,size) gives the frame's tile dimensions. Slot 0 is transparent.
        Cached per (tag, tile, shape, size, flip)."""
        key = (sprite.tag, int(tile_num), int(shape), int(size),
               bool(hflip), bool(vflip))
        cached = self._tiles_frame_cache.get(key)
        if cached is not None:
            return cached or None
        data = self._tiles_bytes_cache.get(sprite.tag)
        if data is None:
            path = self._tiles_path_for(sprite)
            try:
                data = open(path, "rb").read() if path else b""
            except Exception:
                data = b""
            self._tiles_bytes_cache[sprite.tag] = data
        Wt, Ht = self._OAM_TILE_DIMS.get((int(shape), int(size)), (1, 1))
        ntiles = len(data) // 32
        if ntiles == 0:
            self._tiles_frame_cache[key] = False
            return None
        img = QImage(Wt * 8, Ht * 8, QImage.Format.Format_Indexed8)
        ctab = [qRgb(c[0], c[1], c[2]) for c in (palette or [])[:16]]
        while len(ctab) < 16:
            ctab.append(qRgb(0, 0, 0))
        ctab[0] = qRgba(0, 0, 0, 0)            # slot 0 = transparent
        img.setColorTable(ctab)
        img.fill(0)
        for ty in range(Ht):
            for tx in range(Wt):
                ti = tile_num + ty * Wt + tx
                if ti < 0 or ti >= ntiles:
                    continue
                base = ti * 32
                for py in range(8):
                    row = base + py * 4
                    for px in range(8):
                        b = data[row + (px >> 1)]
                        pix = (b >> 4) if (px & 1) else (b & 0xF)
                        if pix:
                            img.setPixel(tx * 8 + px, ty * 8 + py, pix)
        pm = QPixmap.fromImage(img)
        if hflip or vflip:
            from PyQt6.QtGui import QTransform
            pm = pm.transformed(QTransform().scale(-1 if hflip else 1,
                                                   -1 if vflip else 1))
        self._tiles_frame_cache[key] = pm
        return pm

    def _render_engine_frame(self, frame):
        """Draw one engine OAM snapshot into the preview: transform the mons
        (shake / sway / squeeze / lunge) and composite the effect sprites at
        their real per-frame positions/frames/flip/scale."""
        from PyQt6.QtGui import QPainter as _QPainter
        P = self._move_preview
        eng = self._anim_engine

        # Mon transforms. The engine ALWAYS puts battler 0 at the back/player
        # coords and battler 1 at the front/enemy coords (engine_reset fixes
        # them there). The Player/Enemy toggle changes who ATTACKS (gBattleAnim
        # Attacker/Target), not which sprite is where — so this mapping is fixed,
        # NOT direction-dependent. (Swapping it by direction sent an attacker's
        # shake to the wrong mon — e.g. the enemy's Destiny Bond shook the
        # player.)
        mon_side = {0: "back", 1: "front"}
        P.reset_mon_transforms()
        for s in frame:
            which = mon_side.get(s.get("isMon", -1))
            if which is None:
                continue
            # Palette tint on the mon (hit flash, status tint, fade-to-colour).
            P.set_mon_tint(which, s.get("blendCoeff", 0), s.get("blendColor", 0))
            # Alpha fade on the mon (fade-to/from-invisible: Teleport, …).
            P.set_mon_alpha(which, s.get("alpha", 16))
            # Greyscale (Perish Song greys the mons as the notes flip).
            P.set_mon_gray(which, bool(s.get("gray", 0)))
            # Dig-style burrow: the engine HIDES the attacker's mon sprite and
            # wiggles a BG layer the mon was copied onto (monbg + DigDownMovement)
            # — so a plain hide loses the sink. If this is the attacker and its
            # BG layer is scrolling, render the mon descending into the hole.
            sink = self._mon_sink_descent(which, bool(s["invisible"]))
            if sink is not None:
                P.set_mon_sink(which, sink)
            else:
                P.set_mon_sink(which, None)
                # Mon hide (Dig / Fly disappear).
                P.set_mon_visible(which, not s["invisible"])
            if s["affineMode"] != 0:
                mA = s["mA"] or 256
                mD = s["mD"] or 256
                if s["mB"] == 0 and s["mC"] == 0:
                    # Pure scale (grow / shrink / squeeze): Bulk Up, Bind, … Use
                    # ground=True so the mon scales up from its FEET (art bottom)
                    # rather than the frame centre — otherwise the grow lifts the
                    # sprite off the textbox and exposes its hard "hip" cut edge.
                    P.set_mon_transform(which, s["x2"], 0,
                                        256.0 / abs(mA), 256.0 / abs(mD),
                                        ground=True)
                else:
                    # Rotation (Horn Drill's bow TILT, …): render the full OAM
                    # matrix pivoted at the sprite centre, WITH the engine's y2
                    # (SetBattlerSpriteYOffsetFromRotation) so the mon hinges and
                    # tilts DOWN under the textbox — never lifting the hip.
                    P.set_mon_affine(which, s["mA"], s["mB"], s["mC"], s["mD"],
                                     s["x2"], s["y2"])
            else:
                # Non-affine: shake / sway / lunge offset (no scale).
                P.set_mon_transform(which, s["x2"], s["y2"], 1.0, 1.0)

        # Mon CLONES: each is a copy of a SPECIFIC battler — NOT always the
        # attacker. Odor Sleuth clones the TARGET (the wiggling silhouette over
        # the defender). The clone's source battler is the one whose reserved
        # palette slot it carries (0 = player/back, 1 = enemy/front; engine_reset
        # gives each a distinct slot), else the nearest battler by position.
        # Render with THAT battler's sprite, offset from its base so the seating
        # (hip behind the textbox) matches. SKIP objMode==2 (WINDOW) sprites:
        # those are masks (MetallicShine's invisible mon copy), never drawn.
        BASES = {0: (72, 80), 1: (176, 40)}
        mon_pos = {s["isMon"]: (s["x"] + s["x2"], s["y"] + s["y2"])
                   for s in frame if s.get("isMon", -1) in (0, 1)}
        clone_lists = {0: [], 1: []}
        for s in frame:
            if (not s.get("isClone") or s.get("invisible")
                    or s.get("objMode") == 2):
                continue
            pal = s.get("paletteNum", 0)
            if pal in (0, 1):
                src = pal
            elif mon_pos:
                cx, cy = s["x"] + s["x2"], s["y"] + s["y2"]
                src = min(mon_pos, key=lambda b: (cx - mon_pos[b][0]) ** 2
                          + (cy - mon_pos[b][1]) ** 2)
            else:
                src = 0
            bx, by = BASES[src]
            clone_lists[src].append(
                (s["x"] + s["x2"] - bx, s["y"] + s["y2"] - by,
                 bool(s["hFlip"]), bool(s["vFlip"]),
                 s.get("blendCoeff", 0), s.get("blendColor", 0),
                 s.get("alpha", 16), bool(s.get("gray", 0))))
        P.set_mon_clones("back", clone_lists[0])
        P.set_mon_clones("front", clone_lists[1])

        # Effect sprites → canvas (lower subpriority drawn on top).
        canvas = QPixmap(P.CANVAS_W, P.CANVAS_H)
        canvas.fill(QColor(0, 0, 0, 0))
        painter = _QPainter(canvas)
        try:
            # Effect sprites: script-created (host template index) OR TASK-
            # spawned (no index, but a real ANIM_TAG_* tileTag — Hail, Sandstorm).
            effects = [s for s in frame
                       if s.get("isMon", -1) == -1
                       and s.get("objMode") != 2          # WINDOW mask, not drawn
                       and (s.get("templateIndex", -1) >= 0
                            or s.get("tileTag", -1) >= 10000)]
            # Draw order = the GBA's: sort key is (oam.priority<<8 | subpriority);
            # a LOWER key is drawn IN FRONT. So draw highest-key first (behind),
            # lowest-key last (on top). Including oam.priority (not just
            # subpriority) is what fixes cross-priority layering.
            for s in sorted(effects, key=lambda s:
                            -(((s.get("priority", 0) & 3) << 8)
                              | (s.get("subpriority", 0) & 0xFF))):
                if s["invisible"]:
                    continue
                ti = s.get("templateIndex", -1)
                name = eng.template_name(ti) if (eng and ti >= 0) else None
                tag = self._template_tags.get(name, "") if name else ""
                if not tag:                       # task-spawned → map by tileTag
                    tag = getattr(self, "_tag_by_value", {}).get(
                        s.get("tileTag", -1), "")
                frames = self._play_frame_cache.get(tag)
                if frames:
                    fw, fh = self._frame_sizes.get(tag, (0, 0))
                    tpf = max(1, (fw // 8) * (fh // 8)) if (fw and fh) else 1
                    fi = s["tileNum"] // tpf
                    fi = max(0, min(fi, len(frames) - 1))
                    pix = self._play_frame_pix(tag, fi, bool(s["hFlip"]),
                                               bool(s["vFlip"]))
                else:
                    # No PNG sheet — render from the combined .4bpp tile stream.
                    # (Sprites whose PNG source is split into numbered frame
                    # files: ice_crystals, ice_cube, spark, mud_sand, flower —
                    # so Ice Beam / Ice Punch / Hail / Spark draw at all.)
                    sprite = self._sprites.get(tag)
                    if sprite is None:
                        continue
                    pal = self._palettes.get(tag) or self._palette_for(sprite)
                    pix = self._render_4bpp_frame(
                        sprite, pal, s["tileNum"], s["shape"], s["size"],
                        bool(s["hFlip"]), bool(s["vFlip"]))
                if pix is None or pix.isNull():
                    continue
                if s["affineMode"] != 0:
                    tf = self._affine_transform(s["mA"], s["mB"], s["mC"], s["mD"])
                    if tf is not None:
                        pix = pix.transformed(tf)   # flip + scale + rotation
                # Palette tint the engine recorded for this sprite's slot
                # (status flash, BlendColorCycle, fade-to-colour, …).
                cf = s.get("blendCoeff", 0)
                if cf > 0:
                    pix = P.tint_pixmap(pix, cf, s.get("blendColor", 0))
                if s.get("gray"):
                    pix = P.gray_pixmap(pix)   # Perish Song greys the notes
                rx, ry = s["x"] + s["x2"], s["y"] + s["y2"]
                # Alpha blend (setalpha / fade): blend-mode sprites are drawn
                # semi-transparent at the engine's BLDALPHA coefficient.
                a = s.get("alpha", 16)
                if a < 16:
                    painter.setOpacity(max(0.0, a / 16.0))
                painter.drawPixmap(int(rx - pix.width() // 2),
                                   int(ry - pix.height() // 2), pix)
                if a < 16:
                    painter.setOpacity(1.0)
        finally:
            painter.end()
        P.set_anim_pixmap(canvas, P.CANVAS_W // 2, P.CANVAS_H // 2)

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
        self._play_flip_cache = {}
        self._tiles_frame_cache = {}   # .4bpp-rendered frames (split-PNG sprites)
        self._tiles_bytes_cache = {}   # raw .4bpp bytes per tag
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
        _log.debug("PLAY '%s': %d createsprite rows, frame-cache tags=%s",
                   self._move_current,
                   sum(1 for c in timeline if c.name == "createsprite"),
                   list(self._play_frame_cache))
        self._play_peak_drawn = 0
        self._playing = True
        self._play_idx = 0
        self._play_wait = 0
        self._play_tick = 0
        self._last_sound_tick = -100   # reset throttle so replays play sound
        self._last_cry_tick = -100     # reset per move, else the 2nd+ cry move
                                       # is wrongly suppressed (one cry, then dead)
        self._last_sound_se = ""
        self._pending_task_wait = 0    # createvisualtask duration owed to waitforvisualfinish
        self._wait_visual = False
        self._visual_wait = 0
        self._play_last_ms = time.monotonic() * 1000.0
        self._play_layers = []
        self._engine_play = False

        # PREFERRED PATH: run the real game animation code (native engine via
        # WASM) and render its per-frame output. Falls back to the approximate
        # VM below only if the engine isn't installed.
        engine = self._get_anim_engine()
        if engine is None and not self._engine_warned:
            self._engine_warned = True
            try:
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.information(
                    self, "Animation engine not installed",
                    "The battle-animation preview plays best with the 'wasmtime' "
                    "package, which runs the game's real animation engine.\n\n"
                    "Open Program Setup (Help → Program Setup) and install it to "
                    "see moves animate correctly. For now, an approximate preview "
                    "is shown.")
            except Exception:
                pass
        if engine is not None:
            self._engine_sounds = []
            self._engine_sound_ptr = 0
            self._engine_bgscroll = []
            self._engine_bg2scroll = []
            bg_id = self._detect_move_bg(timeline)
            self._engine_bg_pix = self._load_bg_pixmap(bg_id) if bg_id else None
            try:
                ops = self._build_engine_ops(timeline)
                self._engine_frames = engine.play_timeline(
                    ops, attacker_is_player=(self._play_direction == "player"),
                    sounds_out=self._engine_sounds,
                    bgscroll_out=self._engine_bgscroll,
                    bg2scroll_out=self._engine_bg2scroll)
            except Exception:
                _log.exception("engine play_timeline failed; falling back to VM")
                self._engine_frames = []
            # Pre-warm the SE PCM cache for every distinct sound this move fires,
            # so each plays INSTANTLY at its frame (in sync) instead of trailing
            # behind a per-call M4A render. Cached across moves/replays, so this
            # only costs anything the first time a given SE is seen.
            if _preview_sound_prepare_cb is not None:
                for _se in {s for _f, s in self._engine_sounds
                            if s and not s.startswith("__CRY__")}:
                    try:
                        _preview_sound_prepare_cb(_se)
                    except Exception:
                        pass
            if self._engine_frames:
                self._engine_play = True
                self._engine_idx = 0
                self._move_preview.reset_mon_transforms()
                self._tl_play_move_btn.setText("⏹  Stop")
                self._play_step()              # render frame 0
                self._play_timer.start()
                return

        # Build the per-frame simulator with direction-aware battlers.
        P = self._move_preview
        player = Battler(P.PLAYER_CX, P.PLAYER_CY, SIDE_PLAYER)
        enemy = Battler(P.ENEMY_CX, P.ENEMY_CY, SIDE_OPPONENT)
        if self._play_direction == "player":
            ctx = AnimContext(attacker=player, target=enemy)
            task_ctx = TaskCtx(attacker_side=SIDE_PLAYER, target_side=SIDE_OPPONENT)
        else:
            ctx = AnimContext(attacker=enemy, target=player)
            task_ctx = TaskCtx(attacker_side=SIDE_OPPONENT, target_side=SIDE_PLAYER)
        self._anim_sim = AnimSim(ctx)
        # Mon-acting tasks (shake / sway / squeeze) transform the drawn mons.
        self._mon_task_sim = MonTaskSim(task_ctx)
        self._move_preview.reset_mon_transforms()
        self._tl_play_move_btn.setText("⏹  Stop")
        # Process the first batch of commands immediately.
        self._play_step()
        self._play_timer.start()

    def _stop_play_move(self):
        """Stop playback and CLEAR the effect overlay.

        When a move's animation ends, the scene must return to just the mons
        (like in-game) — not freeze on the last frame with every sprite still
        on screen.  The static through-row composite is only for manual
        inspection (it reappears when the user clicks a timeline row); it must
        NOT be the end-of-playback state."""
        was_playing = self._playing
        self._play_timer.stop()
        self._playing = False
        self._engine_play = False
        self._engine_frames = []
        self._engine_bg_pix = None
        self._move_preview.set_anim_bg(None)   # clear the anim background
        self._play_layers = []
        self._anim_sim = None
        self._mon_task_sim = None
        self._wait_visual = False
        self._move_preview.reset_mon_transforms()   # mons back to normal
        self._tl_play_move_btn.setText("▶  Play Move")
        if was_playing:
            _log.debug("PLAY END '%s': stopped idx=%s tick=%s peak-sprites-drawn=%s",
                       self._move_current, self._play_idx, self._play_tick,
                       getattr(self, "_play_peak_drawn", 0))
        # Clear the effect sprites; the mons (front/back pixmaps) stay.
        self._move_preview.set_anim_pixmap(None)

    def hideEvent(self, ev):
        """Stop the playback timer whenever the tab is hidden (project
        close, window switch) so it never fires against a dead view."""
        self._stop_play_move()
        super().hideEvent(ev)

    def _play_step(self):
        """Timer handler: advance the sim by the REAL wall-clock elapsed time
        (so playback runs at true GBA speed even when rendering can't keep up
        — e.g. over Chrome Remote Desktop — instead of dragging), then render
        ONCE.  Decoupling sim-time from render-rate is what kills the "slow +
        jittery" feel: the timeline advances on the clock, the scene repaints
        at a steady ~30 fps.

        Re-entrancy guarded (the sound callback can pump the Qt event loop).
        """
        if self._in_play_step:
            return
        self._in_play_step = True
        try:
            now = time.monotonic() * 1000.0
            elapsed = now - self._play_last_ms
            self._play_last_ms = now
            # GBA runs ~59.7 fps (16.74 ms/frame).  Advance that many frames,
            # clamped to 4 so a stall (GC / RDP hitch) can't fast-forward wildly.
            frames = max(1, min(4, int(round(elapsed / 16.74))))
            if self._engine_play:
                # Engine playback: step through the precomputed OAM frames.
                if self._engine_idx == 0:
                    self._render_engine_frame(self._engine_frames[0])
                    self._set_engine_bg(0)
                self._engine_idx += frames
                self._play_tick = self._engine_idx   # drive _fire_play_sound's throttle
                # Fire any sounds scheduled up to the current frame.
                while (self._engine_sound_ptr < len(self._engine_sounds)
                       and self._engine_sounds[self._engine_sound_ptr][0] <= self._engine_idx):
                    self._fire_play_sound(self._engine_sounds[self._engine_sound_ptr][1])
                    self._engine_sound_ptr += 1
                if self._engine_idx >= len(self._engine_frames):
                    self._stop_play_move()     # done — clears overlay (no freeze)
                else:
                    self._render_engine_frame(self._engine_frames[self._engine_idx])
                    self._set_engine_bg(self._engine_idx)
                return
            for _ in range(frames):
                if not self._playing:
                    break
                self._advance_frame()
            if self._playing:
                self._render_play_composite()
        except Exception:
            _log.exception("play_step crashed at idx=%s tick=%s",
                           getattr(self, "_play_idx", "?"),
                           getattr(self, "_play_tick", "?"))
            self._stop_play_move()
        finally:
            self._in_play_step = False

    def _fire_play_sound(self, se: str):
        """Fire a timeline sound during playback, HARD-throttled so a beam of
        repeated effects (Bubble Beam fires SE_M_BUBBLE ~24×) can't flood the
        Sound Editor's audio backend — the cause of the heavy lag (and a
        likely contributor to hard crashes).  We rate-limit overall AND
        suppress immediate repeats of the same effect; the preview only needs
        to convey the sound, not replay it for every particle."""
        if not se:
            return
        # Cry events (Growl / Howl / Hyper Voice / …) → play the SELECTED mon's
        # cry, like the Pokemon tab. "__CRY__" = attacker, "__CRY__T" = target.
        if se.startswith("__CRY__"):
            if _preview_cry_cb is None:
                return
            now = self._play_tick
            if now - getattr(self, "_last_cry_tick", -100) < 30:
                return                          # one cry, not per-particle
            self._last_cry_tick = now
            sp = self._cry_species_const(target=se.endswith("T"))
            if sp:
                try:
                    _preview_cry_cb(sp)
                except Exception:
                    _log.exception("cry preview failed for %s", sp)
            return
        if _preview_sound_cb is None:
            return
        now = self._play_tick
        if now - self._last_sound_tick < 10:
            return                              # global rate cap (~6/sec)
        if se == self._last_sound_se and now - self._last_sound_tick < 40:
            return                              # don't re-trigger the same SE
        self._last_sound_tick = now
        self._last_sound_se = se
        try:
            _preview_sound_cb(se)
        except Exception:
            _log.exception("sound preview failed for %s", se)

    def _advance_frame(self):
        """Advance exactly ONE GBA frame of playback (no rendering — the timer
        handler renders once after advancing all the frames owed this tick)."""
        timeline = self._cur_timeline
        if not timeline or self._anim_sim is None:
            self._stop_play_move()
            return

        self._play_tick += 1
        self._anim_sim.step()           # move/age every live sprite one frame
        if self._mon_task_sim is not None:
            self._mon_task_sim.step()   # advance mon shake/sway/squeeze tasks

        # Mid fixed-delay: just keep simulating.
        if self._play_wait > 0:
            self._play_wait -= 1
            return

        # waitforvisualfinish: hold until the live sprites finish (self-
        # destruct) + any owed visual-task time — the multi-step gate.  The
        # cap is only a safety net against a sprite that never dies; it must
        # be GENEROUS or it truncates real animations (the Confuse Ray spiral
        # runs 61 frames — a 30-frame cap cut it off at half a circle, then
        # `end` cleared the scene).  Static fallbacks self-destruct at ~30
        # frames, so they don't hold it long regardless.
        if self._wait_visual:
            self._visual_wait += 1
            still_busy = (self._anim_sim.active() or self._pending_task_wait > 0
                          or (self._mon_task_sim is not None
                              and self._mon_task_sim.active()))
            if still_busy and self._visual_wait < 120:
                self._pending_task_wait = max(0, self._pending_task_wait - 1)
                return
            self._wait_visual = False

        # Consume commands until the next delay / wait / terminal.
        while self._play_idx < len(timeline):
            cmd = timeline[self._play_idx]
            self._play_idx += 1

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
                return

            if cmd.name in ("end", "return"):
                # `end` ends the SCRIPT, but sprites already on screen keep
                # living + animating until they self-destruct (the engine
                # doesn't yank them).  Many scripts end with sprites still
                # alive (Status_Confusion's 5 ducks live 90 frames after the
                # `end`).  So don't clear instantly — drain to the end of the
                # timeline and let the live sprites finish (the fall-through
                # stop fires once the sim is idle).
                self._play_idx = len(timeline)
                self._wait_visual = True
                self._visual_wait = 0
                return

            if cmd.name in ("waitforvisualfinish", "waitsound"):
                self._wait_visual = True
                self._visual_wait = 0
                return

            if cmd.name in ("createvisualtask", "createsoundtask"):
                self._spawn_play_task(cmd)
                self._pending_task_wait = max(self._pending_task_wait, 16)
                continue

            if cmd.name == "createsprite":
                self._spawn_play_sprite(cmd)

            if cmd.kind == KIND_SOUND and cmd.args:
                self._fire_play_sound(cmd.args[0])

        # Fell off the end without end/return — stop.
        self._stop_play_move()

    def _spawn_play_task(self, cmd):
        """Spawn a ``createvisualtask`` into the mon-task sim if it's a modelled
        mon-acting task (shake / sway / squeeze).  Non-mon tasks (palette
        blends, BG scrolls, gfx loaders, sprite spawners) are ignored — they
        either don't move a mon or their sprites come via ``createsprite``."""
        if self._mon_task_sim is None:
            return
        vt = parse_createvisualtask(cmd)
        if not vt or not is_mon_task(vt.addr):
            return
        args = [self._int_or(a) for a in vt.args]
        self._mon_task_sim.spawn(vt.addr, args)
        _log.debug("mon-task spawn: %s args=%s", vt.addr, args)

    def _push_mon_transforms(self):
        """Map the mon-task sim's per-battler transforms onto the preview's
        front/back mons (direction-aware), and reset any mon with no active
        task.  Called once per rendered tick."""
        P = self._move_preview
        if self._mon_task_sim is None:
            P.reset_mon_transforms()
            return
        xf = self._mon_task_sim.transforms()
        # attacker → its side's widget; target → the other.
        if self._play_direction == "player":
            side = {TASK_ATTACKER: "back", TASK_TARGET: "front"}
        else:
            side = {TASK_ATTACKER: "front", TASK_TARGET: "back"}
        applied = {"front": False, "back": False}
        for battler, fx in xf.items():
            which = side.get(battler)
            if which is None:
                continue
            P.set_mon_transform(which, fx.dx, fx.dy, fx.sx, fx.sy)
            applied[which] = True
        if not applied["front"]:
            P.set_mon_transform("front", 0, 0, 1.0, 1.0)
        if not applied["back"]:
            P.set_mon_transform("back", 0, 0, 1.0, 1.0)

    def _spawn_play_sprite(self, cmd):
        """Spawn a createsprite into the running simulation (faithful motion
        via the VM; skips classified-invisible utility sprites)."""
        # Defensive cap: never let a runaway script pile up unbounded sprites
        # (memory / paint pressure → crash risk).  60 is far above any real
        # move's on-screen count.
        if self._anim_sim is not None and len(self._anim_sim.sprites) >= 60:
            return
        cs = parse_createsprite(cmd)
        if not cs:
            return
        # Mon-mover dummy sprites (lunge / dip) move the MON, not a visible
        # sprite — route them to the mon-task sim instead of spawning a sprite.
        if is_mon_mover_template(cs.template) and self._mon_task_sim is not None:
            args = [self._int_or(a) for a in cs.args]
            self._mon_task_sim.spawn_mover(cs.template, args)
            _log.debug("mon-mover spawn: %s args=%s", cs.template, args)
            return
        tag = self._template_tags.get(cs.template, "")
        if not tag:
            _log.debug("spawn SKIP: %s has no ANIM_TAG", cs.template)
            return
        if tag not in self._play_frame_cache:
            _log.debug("spawn SKIP: %s tag=%s NOT in frame cache (no png?)",
                       cs.template, tag)
            return
        # Skip known invisible utility sprites (palette fades, mon-movers).
        if self._archetype_for_template(cs.template) == MOTION_INVISIBLE:
            _log.debug("spawn SKIP: %s is INVISIBLE archetype", cs.template)
            return
        cb = self._tpl_callbacks.get(cs.template, "")
        # Per-spawn args feed the VM init (only the init reads them).
        self._anim_sim.ctx.args = [self._int_or(a) for a in cs.args]
        on_attacker = cs.battler.strip() in ("ANIM_ATTACKER", "ANIM_ATK_PARTNER")
        subpri = self._int_or(cs.subpriority)
        if vm_is_ported(cb):
            # Faithful hand-ported callback (exact motion).
            sprite = vm_spawn(cb, self._anim_sim.ctx, tag=tag,
                              subpriority=subpri,
                              fallback_battler_is_attacker=on_attacker)
        else:
            # Unported: drive motion from the coarse archetype so it still
            # animates (fly-to-target / arc / on-mon / static) instead of
            # freezing.  Geometry (start/end/dur/flip) comes from the same
            # archetype model the static composite uses.
            arch, start, end, dur, vis, flip = self._layer_geometry(cs)
            if not vis:
                return
            sprite = vm_new_sprite(tag=tag, subpriority=subpri)
            sprite.x, sprite.y = start
            if arch in (MOTION_ARC_TO_TARGET, MOTION_LINEAR_TO_TARGET):
                # Move toward the target — but ONLY if the computed end is on
                # the canvas.  A callback whose args don't match the standard
                # layout (e.g. arg2 = speed, not an x-offset) could otherwise
                # fling the sprite off-screen; fall back to static there.
                if dur <= 0:
                    dur = 20          # default travel time when args omit it
                if -48 <= end[0] <= self._move_preview.CANVAS_W + 48 \
                        and -48 <= end[1] <= self._move_preview.CANVAS_H + 48:
                    if arch == MOTION_ARC_TO_TARGET:
                        vm_setup_arc(sprite, end, dur)
                    else:
                        vm_setup_linear(sprite, end, dur)
                else:
                    vm_setup_static(sprite, 30)
            else:
                vm_setup_static(sprite, 30)   # static/at-mon/on-mon/unknown
            sprite.flip = flip
        if sprite is not None:
            if vm_is_ported(cb):
                # H-flip: attacker-anchored sprites mirror when the attacker
                # is on the player side (engine's side-dependent HFLIP).
                sprite.flip = on_attacker and (self._play_direction == "player")
            self._anim_sim.add(sprite)

    @staticmethod
    def _apply_flip(pix: QPixmap, flip: bool) -> QPixmap:
        """Horizontally mirror a pixmap when ``flip``.  Used by the static
        composite (not a hot loop); playback uses the flip cache instead."""
        if not flip or pix is None or pix.isNull():
            return pix
        from PyQt6.QtGui import QTransform
        return pix.transformed(QTransform().scale(-1, 1))

    def _play_frame_pix(self, tag: str, idx: int, hflip: bool, vflip: bool):
        """Return the cached frame pixmap for a tag, mirrored horizontally
        and/or vertically as requested.  Each (tag, hflip, vflip) variant is
        baked ONCE and cached — re-creating ~25 ``transformed()`` pixmaps
        every frame churns GPU/RAM (a real crash + jitter risk over Remote
        Desktop)."""
        frames = self._play_frame_cache.get(tag)
        if not frames:
            return None
        idx = max(0, min(idx, len(frames) - 1))
        if not hflip and not vflip:
            return frames[idx]
        key = (tag, hflip, vflip)
        fc = self._play_flip_cache.get(key)
        if fc is None:
            from PyQt6.QtGui import QTransform
            tr = QTransform().scale(-1 if hflip else 1, -1 if vflip else 1)
            fc = [f.transformed(tr) for f in frames]
            self._play_flip_cache[key] = fc
        return fc[idx]

    def _render_play_composite(self):
        """Paint the running simulation's live sprites onto the battle
        preview at their real per-frame positions.  Uses pre-baked (and
        flip-cached) frames — no disk access, no per-frame pixmap churn.
        Each sprite draw is guarded so one bad frame can't abort the paint.
        Ordered by subpriority (lower on top)."""
        from PyQt6.QtGui import QPainter as _QPainter
        # Mon-acting tasks (shake / sway / squeeze) transform the drawn mons,
        # independent of whether any anim sprites exist (Bind has none).
        self._push_mon_transforms()
        sim = self._anim_sim
        if sim is None or not sim.sprites:
            self._move_preview.set_anim_pixmap(None)
            return
        P = self._move_preview
        canvas = QPixmap(P.CANVAS_W, P.CANVAS_H)
        canvas.fill(QColor(0, 0, 0, 0))
        painter = _QPainter(canvas)
        drawn = 0
        try:
            for s in sorted(sim.sprites, key=lambda s: -s.subpriority):
                if s.invisible:
                    continue
                frames = self._play_frame_cache.get(s.tag)
                if not frames:
                    continue
                # Sprites that advance their own frame cursor (e.g. the nail)
                # use it; others cycle gently with age.
                idx = s.frame_advance if s.frame_advance else (s.age // 4)
                pix = self._play_frame_pix(s.tag, idx % len(frames),
                                           s.flip, s.flip_v)
                if pix is None or pix.isNull():
                    continue
                painter.drawPixmap(int(s.render_x - pix.width() // 2),
                                   int(s.render_y - pix.height() // 2), pix)
                drawn += 1
        finally:
            painter.end()
        if drawn > getattr(self, "_play_peak_drawn", 0):
            self._play_peak_drawn = drawn
        self._move_preview.set_anim_pixmap(canvas, P.CANVAS_W // 2,
                                           P.CANVAS_H // 2)

    # ── layered composite preview ─────────────────────────────────────

    @staticmethod
    def _int_or(s, default=0):
        """Parse a C-style int literal (incl. negatives / 0x), or a known
        anim constant, else default.

        Battler-selector args are passed as constants, not numbers
        (``ANIM_TARGET`` etc.), and several callbacks branch on them (e.g.
        AnimHitSplatBasic: arg2 == 0 → attacker, else target).  Mapping them
        to their real engine values is why Bite's hit-splat lands on the
        victim instead of the attacker."""
        raw = str(s).strip()
        const = _ANIM_ARG_CONSTS.get(raw)
        if const is not None:
            return const
        try:
            return int(raw, 0)
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _parse_rgb_consts(root):
        """{RGB_* name: BGR555 value} parsed from the project's
        include/constants/rgb.h, plus inline RGB(r,g,b) support. Anim scripts
        pass colours to the blend/fade tasks as these constants (RGB_WHITE for
        a white flash, RGB_BLACK for a dark blend, RGB(13,31,12) for a green
        tint, …); without resolving them the tint colour comes through as 0
        (black) and every flash looks like a fade-to-black. Project-agnostic."""
        import os
        import re
        consts = {"RGB_ALPHA": 1 << 15}
        p = os.path.join(root, "include", "constants", "rgb.h")
        if not os.path.isfile(p):
            return consts
        try:
            text = open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            return consts
        for m in re.finditer(r'#define\s+(RGB_\w+)\s+(.+)', text):
            name, expr = m.group(1), m.group(2).strip()
            mm = re.match(r'RGB\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)', expr)
            if mm:
                r, g, b = (int(x) for x in mm.groups())
                consts[name] = (r | (g << 5) | (b << 10)) & 0xFFFF
                continue
            toks = re.findall(r'RGB_\w+', expr)   # e.g. (RGB_WHITE | RGB_ALPHA)
            if toks and all(t in consts for t in toks):
                v = 0
                for t in toks:
                    v |= consts[t]
                consts[name] = v & 0xFFFF
        return consts

    def _arg_val(self, raw):
        """Resolve an anim-script arg to its numeric value, including colour
        constants the blend/fade tasks use: named RGB_* and inline RGB(r,g,b).
        Falls back to _int_or (numeric / battler-selector constants)."""
        import re
        s = str(raw).strip()
        m = re.match(r'RGB\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)', s)
        if m:
            r, g, b = (int(x) for x in m.groups())
            return (r | (g << 5) | (b << 10)) & 0xFFFF
        rgb = getattr(self, "_rgb_consts", None)
        if rgb and s in rgb:
            return rgb[s]
        return self._int_or(s)

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
            # arg2/arg3 are the end offset ONLY when the callback follows the
            # standard layout (small values, like Ember's -16/24).  Some
            # callbacks put a SPEED or duration there (Confuse Ray bounce:
            # arg2=288), which would fling the sprite off-screen — a value
            # too big to be a pixel offset isn't one, so treat it as the
            # plain target.  Keeps real offsets, fixes the rest.
            e2 = arg(2) if abs(arg(2)) <= 48 else 0
            e3 = arg(3) if abs(arg(3)) <= 48 else 0
            end = (tgt[0] + xdir * e2, tgt[1] + e3)
            # arg4 is the travel duration when present; projectiles that omit
            # it (Bullet Seed: "20, 0") default to a real travel time instead
            # of 1 frame — else the sprite teleports to the target + flickers.
            dur = arg(4) if arg(4) > 1 else 24
            return (arch, start, end, dur, True, player_attacks)
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
        if arch == MOTION_AT_TARGET:        # raw target coord, no arg offset
            return (arch, tgt, tgt, 0, True, False)
        if arch == MOTION_AT_ATTACKER:      # raw attacker coord, no arg offset
            return (arch, atk, atk, 0, True, player_attacks)
        # UNKNOWN → the engine auto-creates battle-anim sprites at the TARGET
        # centre; a callback that doesn't reposition (or only does
        # ``sprite->x += arg``, like AnimBite) leaves them there.  So default
        # to the TARGET, NOT the createsprite's declared anim_battler (that
        # arg controls binding/subpriority, not the spawn point).  This is why
        # e.g. Bite's teeth belong on the victim, not the biter.
        pos = (tgt[0] + xdir * (arg(0) + ex), tgt[1] + arg(1) + ey)
        return (MOTION_UNKNOWN, pos, pos, 0, True, False)

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

    # ── reference Pokémon (real battle sprites behind the anim) ────────

    def set_species_provider(self, species_pairs, resolver) -> None:
        """Optional host hook: supply project display names so the reference
        dropdowns show the project's own species names (respecting renames).

        Sprite resolution itself is self-contained (the tab scans
        ``graphics/pokemon/`` directly in :meth:`load`), so the preview shows
        mons even if this is never called — but when it is, the combo labels
        use the project names instead of folder slugs.  ``resolver`` is kept
        for backward compatibility and no longer required."""
        # Map folder slug → project display name (SPECIES_CHARIZARD → charizard).
        self._species_name_override = {}
        for const, name in (species_pairs or []):
            slug = const.replace("SPECIES_", "").lower()
            if slug and name:
                self._species_name_override[slug] = name
        self._populate_ref_combos()

    def _populate_ref_combos(self):
        """Fill the reference-mon dropdowns by scanning the project's
        ``graphics/pokemon/`` tree (self-contained — no host wiring needed)."""
        if not hasattr(self, "_ref_player_combo") or not self._project_root:
            return
        from core.battle_mon_ref import list_mon_sprites, mon_display_name
        mons = list_mon_sprites(self._project_root)
        self._mon_dirs = {slug: d for slug, d in mons}
        prev_player = self._ref_player_combo.currentData()
        prev_enemy = self._ref_enemy_combo.currentData()
        for combo in (self._ref_player_combo, self._ref_enemy_combo):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("(none)", "")
            for slug, _d in mons:
                name = self._species_name_override.get(slug) or mon_display_name(slug)
                combo.addItem(name, slug)
            combo.setEnabled(bool(mons))
            combo.blockSignals(False)
        # Restore the prior pick, else default both to the first real species
        # so the user sees mons immediately (a concrete reference beats empty).
        def _restore(combo, prev, default_idx):
            combo.blockSignals(True)
            idx = combo.findData(prev) if prev else -1
            combo.setCurrentIndex(idx if idx >= 0 else default_idx)
            combo.blockSignals(False)
        default = 1 if len(mons) >= 1 else 0
        _restore(self._ref_player_combo, prev_player, default)
        _restore(self._ref_enemy_combo, prev_enemy, default)
        self._apply_ref_mons()

    def _ref_mon_pixmap(self, slug: str, view: str) -> Optional[QPixmap]:
        """Load a species' front/back battle sprite with its real palette
        (RAM-first via the bus, ``.gbapal`` fallback), or None.  ``view`` is
        ``"front"`` (enemy) or ``"back"`` (player)."""
        if not slug or not self._project_root:
            return None
        d = self._mon_dirs.get(slug)
        if not d:
            return None
        from core.battle_mon_ref import mon_sprite_path, mon_palette
        path = mon_sprite_path(d, view)
        if not path:
            return None
        try:
            pal = mon_palette(self._project_root, slug, d)
            return load_sprite_pixmap(path, pal) if pal else QPixmap(path)
        except Exception:
            _log.exception("reference mon render failed: %s %s", slug, view)
            return None

    @staticmethod
    def _mon_const(slug: str) -> str:
        """Folder slug → SPECIES_ const (charizard → SPECIES_CHARIZARD)."""
        return ("SPECIES_" + slug.replace("-", "_").upper()) if slug else ""

    @staticmethod
    def _parse_tag_values(root: str) -> Dict[int, str]:
        """{numeric ANIM_TAG_* value: name} from constants/battle_anim.h, so a
        sprite's runtime tileTag (a number) maps back to a gfx tag name."""
        out: Dict[int, str] = {}
        try:
            import re
            p = os.path.join(root, "include", "constants", "battle_anim.h")
            txt = open(p, encoding="utf-8", errors="replace").read()
            m = re.search(r"#define\s+ANIM_SPRITES_START\s+(\d+)", txt)
            base = int(m.group(1)) if m else 10000
            for mm in re.finditer(
                    r"#define\s+(ANIM_TAG_\w+)\s*\(\s*ANIM_SPRITES_START\s*\+\s*(\d+)\s*\)",
                    txt):
                out[base + int(mm.group(2))] = mm.group(1)
        except Exception:
            pass
        return out

    def _cry_species_const(self, target: bool = False) -> str:
        """SPECIES_ const of the mon whose cry a cry-move should play. The
        attacker by default (Growl / Howl / Hyper Voice are the user's cry);
        ``target=True`` for the target's side. Honours the Player/Enemy attack
        direction + the reference-mon dropdowns."""
        if not hasattr(self, "_ref_player_combo"):
            return ""
        player_attacks = (self._play_direction == "player")
        use_player = player_attacks ^ target   # attacker side, flipped for target
        combo = self._ref_player_combo if use_player else self._ref_enemy_combo
        slug = combo.currentData()
        return self._mon_const(slug) if slug else ""

    def _apply_ref_mons(self):
        """Push the chosen reference mons onto the battle-scene preview
        (player → back sprite, enemy → front sprite), positioned with the
        SAME per-species y-offsets + elevation the Pokemon Graphics tab uses
        so the mons sit exactly where they do there / in-game."""
        if not hasattr(self, "_ref_player_combo"):
            return
        pslug = self._ref_player_combo.currentData()
        eslug = self._ref_enemy_combo.currentData()
        self._move_preview.set_back_pixmap(self._ref_mon_pixmap(pslug, "back"))
        self._move_preview.set_front_pixmap(self._ref_mon_pixmap(eslug, "front"))
        gd = self._gfx_data
        if gd is not None:
            try:
                self._move_preview.set_back_y_offset(
                    gd.get_back_y(self._mon_const(pslug)) if pslug else 0)
                self._move_preview.set_front_y_offset(
                    gd.get_front_y(self._mon_const(eslug)) if eslug else 0)
                self._move_preview.set_enemy_elevation(
                    gd.get_elevation(self._mon_const(eslug)) if eslug else 0)
            except Exception:
                pass

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
