"""
ui/pokedex_detail_panel.py
Pokédex entry detail panel — shows all fields from pokedex.json
for the currently selected national dex entry.
"""

from __future__ import annotations

import os

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QDoubleSpinBox, QFormLayout, QFrame, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QSpinBox, QVBoxLayout, QWidget,
)


# ── height/weight unit conversions (mirror pokefirered's pokedex display) ─────
def _dm_to_ftin(dm: int) -> tuple[int, int]:
    """Decimeters -> (feet, inches), matching DexScreen_PrintMonHeight."""
    tenths = 10000 * int(dm) // 254          # tenths of an inch
    if tenths % 10 >= 5:
        tenths += 10                         # round to the nearest inch
    feet = tenths // 120
    inches = (tenths - feet * 120) // 10
    return feet, inches


def _ftin_to_dm(feet: int, inches: int) -> int:
    """(feet, inches) -> decimeters (1 in = 0.254 dm)."""
    return round((int(feet) * 12 + int(inches)) * 254 / 1000)


def _hg_to_lbs(hg: int) -> float:
    """Hectograms -> lbs rounded to 0.1, matching DexScreen_PrintMonWeight."""
    hundredths = int(hg) * 100000 // 4536      # game's integer hundredths-of-lb
    return round(hundredths / 10) / 10         # -> nearest 0.1 lb


def _lbs_to_hg(lbs: float) -> int:
    """lbs -> hectograms (1 lb = 4.536 hg)."""
    return round(float(lbs) * 4536 / 1000)


# ── size-comparison derivation constants (see docs/POKEDEX_SIZE_FACTS.md) ──────
# THE MODEL (scale drives height):
#   The monster's Pokémon Scale is the ONE user input. Height is a READ-ONLY output
#   derived live from it. The player is a FIXED 4'7" (14 dm) sprite: it stays full
#   size while the monster still fits the 64px box, and SHRINKS + drops only once the
#   user grows the monster PAST the box. A short monster (small art) just grows and
#   never shrinks the player — the player shrinks solely because the box can't hold a
#   bigger monster, so the whole scene scales down and the shorter player with it.
#
# On-screen art height = OCCUPIED art px × 256 / scale (GBA affine scale is INVERSE:
# 256 = natural size; a bigger scale number draws SMALLER). So:
#   monster on-screen  = mon_occ    × 256 / pokemonScale
#   player  on-screen  = player_occ × 256 / trainerScale
# Measured anchor (docs/POKEDEX_SIZE_FACTS.md): the player front pic art is 55 px
# tall and the player is 14 dm ⇒ the height any monster reads as is
#   height_dm = 14 × (monster on-screen px) / (player_occ px at full size).
_NAT_SCALE           = 256    # native GBA affine scale (art drawn 1:1)
_BOX_PX              = 64      # OAM frame: art bigger than this clips, so it's the limit
_PLAYER_GROUND_C     = 24     # engine ground line G≈128 screen-Y, minus sprite y=104
_PLAYER_FEET_K       = 26     # Link's foot texture row ≈58 of 64 (row - 32)
_PLAYER_HEIGHT_DM    = 14     # fixed 4'7" reference player (never changes)
_TRAINER_SCALE_FLOOR = 256    # player drawn full-size while the monster is at/under natural
_SCALE_CEIL          = 65535  # u16 ceiling for either scale
_PLAYER_MIN_DISP_PX  = 10     # giant clamp: player never shrinks below ~10px on screen
# trainerOffset = 24 - 26 × 256 / trainerScale — regressed from the vanilla
# gPokedexEntries table (RMSE 1.31 over 26 mons); the player is grounded on a fixed
# engine line so its offset is a function of its own scale alone.
_PLAYER_OCC_PX_FALLBACK = 55  # measured red/leaf front pic occupied height
_MON_OCC_PX_FALLBACK    = 64  # if a mon PNG can't be measured, assume full frame

from ui.dex_description_edit import DexDescriptionEdit
from core.sprite_render import load_sprite_pixmap
from core.sprite_palette_bus import get_bus as _get_palette_bus, CAT_POKEMON

# ── shared card style (same palette as items / species panels) ───────────────

_CARD_SS = """
QGroupBox {
    font-weight: bold;
    font-size: 10px;
    border: 1px solid #383838;
    border-radius: 6px;
    margin-top: 10px;
    padding-top: 6px;
    background-color: #252525;
    color: #cccccc;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 5px;
    color: #777777;
    font-size: 9px;
    text-transform: uppercase;
    letter-spacing: 1px;
}
"""

_FIELD_SS = """
QLineEdit, QSpinBox, QPlainTextEdit {
    background-color: #1e1e1e;
    border: 1px solid #3a3a3a;
    border-radius: 4px;
    padding: 4px 7px;
    color: #e0e0e0;
    font-size: 12px;
    selection-background-color: #1565c0;
}
QLineEdit:focus, QSpinBox:focus, QPlainTextEdit:focus {
    border: 1px solid #1976d2;
}
QSpinBox::up-button, QSpinBox::down-button {
    background-color: #333333; border: none; width: 16px;
}
"""

_COUNTER_SS = "color: #555555; font-size: 10px; font-family: 'Courier New';"


def _card(title: str) -> tuple[QGroupBox, QFormLayout]:
    box = QGroupBox(title)
    box.setStyleSheet(_CARD_SS)
    form = QFormLayout()
    form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
    form.setContentsMargins(12, 6, 12, 12)
    form.setHorizontalSpacing(14)
    form.setVerticalSpacing(9)
    box.setLayout(form)
    return box, form


def _lbl(text: str) -> QLabel:
    l = QLabel(text)
    l.setStyleSheet("color: #777777; font-size: 11px;")
    return l


def _load_gba_sprite(path: str) -> QPixmap | None:
    """
    Load a 4-bit indexed GBA PNG and make its background transparent.

    Qt does not reliably apply the tRNS chunk for 4-bit indexed PNGs, so the
    background colour (palette index 0, always at pixel 0,0) stays opaque.
    We strip it manually: convert to ARGB32, read the top-left pixel's RGB,
    then zero out every pixel whose RGB matches.  GBA artists never reuse the
    background colour inside the sprite art, so this is safe.
    """
    if not path or not os.path.isfile(path):
        return None
    img = QImage(path)
    if img.isNull():
        return None
    img = img.convertToFormat(QImage.Format.Format_ARGB32)
    # palette index 0 colour (the transparent background)
    bg_rgb = img.pixel(0, 0) & 0x00FFFFFF
    for iy in range(img.height()):
        for ix in range(img.width()):
            if (img.pixel(ix, iy) & 0x00FFFFFF) == bg_rgb:
                img.setPixel(ix, iy, 0x00000000)
    return QPixmap.fromImage(img)


# Occupied-height cache, keyed by PNG path. A palette re-colour never changes which
# pixels are the index-0 background, so the occupied height can't change without the
# PNG art itself changing (which reloads the entry and re-measures).
_OCC_HEIGHT_CACHE: dict[str, int] = {}


def _measure_occupied_height(path: str | None, *, fallback: int) -> int:
    """Occupied (non-background) bounding-box HEIGHT in px of the FIRST 64×64 frame of
    a 4bpp indexed GBA front pic. Background = palette index 0 == the top-left pixel's
    colour (GBA art never reuses the bg colour inside the sprite). Multi-frame sheets
    (64×128 etc.) are handled by scanning only rows 0..63. Returns *fallback* when the
    file is missing / unreadable / entirely background. Cached per path."""
    if not path:
        return fallback
    cached = _OCC_HEIGHT_CACHE.get(path)
    if cached is not None:
        return cached
    if not os.path.isfile(path):
        return fallback
    img = QImage(path)
    if img.isNull():
        return fallback
    img = img.convertToFormat(QImage.Format.Format_ARGB32)
    w = min(64, img.width())
    h = min(64, img.height())
    if w <= 0 or h <= 0:
        return fallback
    bg = img.pixel(0, 0) & 0x00FFFFFF
    top = bot = None
    for iy in range(h):
        for ix in range(w):
            if (img.pixel(ix, iy) & 0x00FFFFFF) != bg:
                if top is None:
                    top = iy
                bot = iy
                break
    if top is None:
        return fallback              # nothing but background → safe fallback
    occ = bot - top + 1
    _OCC_HEIGHT_CACHE[path] = occ
    return occ


class _SizePreview(QWidget):
    """
    Re-implementation of the in-game Pokédex size-comparison box.

    Coordinate math derived directly from pokefirered/src/sprite.c and
    pokedex_screen.c:

      CreateMonPicSprite(…, x=40, y=104)  → Pokémon sprite
      CreateTrainerPicSprite(…, x=80, y=104) → Trainer sprite

      CalcCenterToCornerVec for 64×64 square sprite:
          centerToCornerVecX = centerToCornerVecY = -32

      oam.top_left = (sprite.x - 32,  sprite.y + y2_offset - 32)
        Pokémon OAM TL in game space: (8,  72 + pokemonOffset)
        Trainer  OAM TL in game space: (48, 72 + trainerOffset)

    Canvas mapping (game → canvas): canvas_y = game_y - 60
        Pokémon OAM TL in canvas: (8,  12 + pokemonOffset)
        Trainer  OAM TL in canvas: (48, 12 + trainerOffset)

    GBA affine matrix element a = scale_value is an INVERSE scale:
        visible_size = 64 × (256 / scale_value)
        content is centered within the 64×64 OAM bounding box.

    draw_top_left = oam_canvas_tl + (64 - visible_size) / 2
    """

    # Canvas: game OAM positions (8 and 48) + 64px sprite + small margins
    _W           = 128
    _H           = 96
    _GAME_Y_OFF  = 60   # subtract from game-space y to get canvas y
    # Sprite x positions in game space; OAM TL = sprite_x - 32
    _POKE_X      = 40   # → OAM left = 8
    _TRAIN_X     = 80   # → OAM left = 48
    _SPRITE_Y    = 104  # both sprites share this game-space y centre
    # Engine ground line: game-space Y≈128 (where Link's feet land at native
    # scale) → canvas Y = 128 - _GAME_Y_OFF(60) = 68. The user plants each mon's
    # feet on THIS line with pokemonScale + pokemonOffset.
    _GROUND_Y    = 68

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self._W, self._H)
        self._trainer_pm: QPixmap | None = None
        self._pokemon_pm: QPixmap | None = None
        self._poke_scale   = 256
        self._poke_offset  = 0
        self._train_scale  = 256
        self._train_offset = 0

    # ── public API ────────────────────────────────────────────────────────────

    def set_trainer(self, path: str | None) -> None:
        self._trainer_pm = _load_gba_sprite(path)
        self.update()

    def set_pokemon(self, path: str | None) -> None:
        self._pokemon_pm = _load_gba_sprite(path)
        self.update()

    def set_values(self, poke_scale: int, poke_offset: int,
                   train_scale: int, train_offset: int) -> None:
        self._poke_scale   = max(poke_scale,  1)
        self._poke_offset  = poke_offset
        self._train_scale  = max(train_scale, 1)
        self._train_offset = train_offset
        self.update()

    # ── paint ─────────────────────────────────────────────────────────────────

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setClipRect(self.rect())

        p.fillRect(self.rect(), QColor("#1a1a1a"))

        # Subtle ground reference line
        p.setPen(QPen(QColor("#3a3a3a"), 1))
        p.drawLine(0, self._GROUND_Y, self._W, self._GROUND_Y)

        # Pokémon (left, x=40 in game → OAM left=8)
        self._draw_sprite(p, self._pokemon_pm,
                          oam_left=self._POKE_X  - 32,
                          poke_offset=self._poke_offset,
                          scale=self._poke_scale)

        # Trainer (right, x=80 in game → OAM left=48)
        self._draw_sprite(p, self._trainer_pm,
                          oam_left=self._TRAIN_X - 32,
                          poke_offset=self._train_offset,
                          scale=self._train_scale)

        p.end()

    def _draw_sprite(self, p: QPainter, pm: QPixmap | None,
                     oam_left: int, poke_offset: int, scale: int) -> None:
        if pm is None or pm.isNull():
            return

        # GBA affine: scale value is INVERSE — larger value = smaller sprite
        vis = 64.0 * (256.0 / scale)

        # OAM bounding-box top-left in canvas space
        oam_top = (self._SPRITE_Y + poke_offset - 32) - self._GAME_Y_OFF

        # Visible content is centred inside the 64×64 OAM box
        margin = (64.0 - vis) / 2.0
        dx = int(oam_left + margin)
        dy = int(oam_top  + margin)
        sz = max(1, int(vis))

        p.drawPixmap(dx, dy, sz, sz, pm)


class PokedexDetailPanel(QWidget):
    """
    Displays and allows editing of every field in a Pokédex entry.

    Signals:
        changed — emitted on any field edit
    """

    changed = pyqtSignal()
    play_cry_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._loading   = False
        self._dex_const: str | None = None
        self._trainer_sprite_path: str | None = None
        # Bus-aware sprite state. We remember the species const so a live
        # palette edit on another tab (Graphics / Trainer Graphics) can
        # re-render our header sprite without reloading the whole entry.
        self._species_const: str | None = None
        self._sprite_path: str | None = None
        self._project_root: str = ""
        # Measured occupied art height (px) of the current mon front pic and the
        # player front pic — the real sprite footprints that drive auto sizing.
        self._mon_occ_px: int    = _MON_OCC_PX_FALLBACK
        self._player_occ_px: int = _PLAYER_OCC_PX_FALLBACK
        self.setStyleSheet(_FIELD_SS)
        self._build()
        # Subscribe to palette edits — the bus emits (category, key).
        _get_palette_bus().palette_changed.connect(self._on_palette_changed)

    # ── build ────────────────────────────────────────────────────────────────

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 18)
        root.setSpacing(10)

        # ── Header: sprite + name + constants ────────────────────────────────
        hdr = QHBoxLayout()
        hdr.setSpacing(14)

        self._sprite_lbl = QLabel()
        self._sprite_lbl.setFixedSize(64, 64)
        self._sprite_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sprite_lbl.setStyleSheet("background: transparent; border: none;")
        hdr.addWidget(self._sprite_lbl)

        name_block = QVBoxLayout()
        name_block.setSpacing(3)

        self._hdr_name = QLabel("—")
        f = QFont(); f.setPointSize(15); f.setBold(True)
        self._hdr_name.setFont(f)
        self._hdr_name.setStyleSheet("color: #ffffff; background: transparent;")
        name_block.addWidget(self._hdr_name)

        self._hdr_dex = QLabel("")
        self._hdr_dex.setStyleSheet(
            "color: #888888; font-size: 11px; background: transparent;"
        )
        name_block.addWidget(self._hdr_dex)

        self._hdr_const = QLabel("")
        self._hdr_const.setStyleSheet(
            "color: #555555; font-family: 'Courier New'; font-size: 10px; background: transparent;"
        )
        name_block.addWidget(self._hdr_const)
        name_block.addStretch(1)

        hdr.addLayout(name_block)
        hdr.addStretch(1)

        self._play_cry_btn = QPushButton("\u25B6 Play Cry")
        self._play_cry_btn.setToolTip(
            "Play this species' cry sample\n"
            "(sound/direct_sound_samples/cries/*.wav)"
        )
        self._play_cry_btn.setFixedWidth(90)
        self._play_cry_btn.clicked.connect(self.play_cry_requested.emit)
        hdr.addWidget(self._play_cry_btn, 0, Qt.AlignmentFlag.AlignTop)

        root.addLayout(hdr)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background-color: #2e2e2e; border: none; max-height: 1px;")
        root.addWidget(sep)

        # ── Description card ──────────────────────────────────────────────────
        desc_card, desc_form = _card("Pokédex Entry")

        self.f_description = DexDescriptionEdit(max_chars_per_line=42, max_lines=3)
        self.f_description.setMinimumHeight(90)
        self.f_description.setMaximumHeight(130)
        self.f_description.setFont(QFont("Courier New", 10))
        self.f_description.setPlaceholderText("Pokédex flavor text…")
        self.f_description.setLineWrapMode(
            self.f_description.LineWrapMode.NoWrap
        )
        desc_form.addRow(self.f_description)

        # Character-count row below the text area
        self._desc_counter = QLabel("")
        self._desc_counter.setStyleSheet(_COUNTER_SS)
        self.f_description.set_counter_label(self._desc_counter)
        desc_form.addRow(self._desc_counter)

        root.addWidget(desc_card)

        # ── Identity card ─────────────────────────────────────────────────────
        id_card, id_form = _card("Identity")

        self.f_category = QLineEdit()
        self.f_category.setMaxLength(11)   # u8 categoryName[12] null-terminated = 11 usable chars
        self.f_category.setPlaceholderText("e.g. SEED")
        self._cat_counter = QLabel("0/11")
        self._cat_counter.setStyleSheet(_COUNTER_SS)
        self._cat_counter.setToolTip(
            "Characters used / character limit\n"
            "(PokedexEntry.categoryName is u8[12] null-terminated = 11 usable chars)"
        )
        def _update_cat_counter(text):
            used = len(text)
            self._cat_counter.setText("{0}/11".format(used))
            self._cat_counter.setStyleSheet(
                _COUNTER_SS + " color: #cc3333;" if used >= 11 else _COUNTER_SS
            )
        self.f_category.textChanged.connect(_update_cat_counter)
        cat_row = QHBoxLayout()
        cat_row.setContentsMargins(0, 0, 0, 0)
        cat_row.setSpacing(6)
        cat_row.addWidget(self.f_category)
        cat_row.addWidget(self._cat_counter)
        id_form.addRow(_lbl("Category"), cat_row)

        # Height: shown as feet'inches" (stored as decimeters). This is a READ-ONLY
        # OUTPUT — it is derived live from the Pokémon Scale below (how big the
        # monster's sprite is drawn versus the fixed 4'7" player) and is NEVER typed
        # in. Grow the monster ⇒ this climbs; shrink it ⇒ this drops.
        self.f_height_ft = QSpinBox(); self.f_height_ft.setRange(0, 99)
        self.f_height_ft.setSuffix(" ft")
        self.f_height_in = QSpinBox(); self.f_height_in.setRange(0, 11)
        self.f_height_in.setSuffix(" in")
        for w in (self.f_height_ft, self.f_height_in):
            w.setReadOnly(True)
            w.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
            w.setToolTip(
                "Read-only: this monster's height is derived from the Pokémon Scale "
                "below (how big its sprite is drawn next to the fixed 4'7\" player). "
                "Set the size by eye with the Pokémon Scale and this updates live.")
        self._height_conv = QLabel("")
        self._height_conv.setStyleSheet("color: #666; font-size: 10px;")
        self._height_auto = QLabel("(auto)")
        self._height_auto.setStyleSheet("color: #555; font-size: 9px; font-style: italic;")
        height_row = QHBoxLayout()
        height_row.setSpacing(6)
        height_row.addWidget(self.f_height_ft)
        height_row.addWidget(self.f_height_in)
        height_row.addWidget(self._height_auto)
        height_row.addWidget(self._height_conv)
        height_row.addStretch(1)
        id_form.addRow(_lbl("Height"), height_row)

        # Weight: the game shows lbs to one decimal (stored as hectograms).
        self.f_weight_lbs = QDoubleSpinBox()
        self.f_weight_lbs.setRange(0, 9999.9)
        self.f_weight_lbs.setDecimals(1)
        self.f_weight_lbs.setSuffix(" lbs")
        self._weight_conv = QLabel("")
        self._weight_conv.setStyleSheet("color: #666; font-size: 10px;")
        weight_row = QHBoxLayout()
        weight_row.setSpacing(6)
        weight_row.addWidget(self.f_weight_lbs)
        weight_row.addWidget(self._weight_conv)
        weight_row.addStretch(1)
        id_form.addRow(_lbl("Weight"), weight_row)

        root.addWidget(id_card)

        # ── Scale / Offset card ───────────────────────────────────────────────
        scale_card, scale_form = _card("Sprite Scale & Offset")

        self.f_pokemon_scale  = QSpinBox(); self.f_pokemon_scale.setRange(1, 65535)
        self.f_pokemon_offset = QSpinBox(); self.f_pokemon_offset.setRange(-128, 127)
        self.f_trainer_scale  = QSpinBox(); self.f_trainer_scale.setRange(0, 65535)
        self.f_trainer_offset = QSpinBox(); self.f_trainer_offset.setRange(-128, 127)
        # The player scale + offset are always derived from Height (the player is a
        # fixed 4'7" character) — never user-set, so both are read-only.
        self.f_trainer_scale.setReadOnly(True)
        self.f_trainer_offset.setReadOnly(True)
        self.f_trainer_scale.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.f_trainer_offset.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.f_pokemon_scale.setToolTip(
            "THE size control. How big THIS monster's sprite is drawn next to the "
            "fixed 4'7\" player (GBA scale is inverse: SMALLER number = BIGGER "
            "sprite; 256 = natural art size). Set it by eye — the Height above "
            "updates live from it. Grow the monster past the player and the player "
            "sprite shrinks and drops (vanilla behaviour).")
        self.f_pokemon_offset.setToolTip(
            "Moves ONLY this mon's sprite up/down to plant its feet on the ground "
            "line in the preview. Each mon's art sits differently, so this is "
            "hand-set per species. It never moves the player sprite or the Height.")
        self.f_trainer_scale.setToolTip(
            "Read-only: the player is a fixed 4'7\" character. Its sprite stays full "
            "size while the monster is at/under its natural size, and shrinks once "
            "you grow the monster bigger — driven entirely by the Pokémon Scale, "
            "never set by hand.")
        self.f_trainer_offset.setToolTip(
            "Read-only: the player's grounding offset follows its own scale, so it "
            "shifts down as the player shrinks. Independent of the monster.")

        scale_form.addRow(_lbl("Pokémon Scale"),  self.f_pokemon_scale)
        scale_form.addRow(_lbl("Pokémon Offset"), self.f_pokemon_offset)
        scale_form.addRow(_lbl("Trainer Scale"),  self.f_trainer_scale)
        scale_form.addRow(_lbl("Trainer Offset"), self.f_trainer_offset)

        # Live size-comparison preview
        self._size_preview = _SizePreview()
        preview_row = QHBoxLayout()
        preview_row.addStretch(1)
        preview_row.addWidget(self._size_preview)
        preview_row.addStretch(1)
        scale_form.addRow(preview_row)

        root.addWidget(scale_card)

        # ── Wild Encounters card ──────────────────────────────────────────────
        enc_card, enc_form = _card("Wild Encounters")
        self._enc_list = QLabel("Not found in the wild")
        self._enc_list.setWordWrap(True)
        self._enc_list.setTextFormat(Qt.TextFormat.RichText)
        self._enc_list.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._enc_list.setStyleSheet(
            "color: #c0c0c0; font-size: 11px; "
            "padding: 4px; background: transparent;"
        )
        enc_form.addRow(self._enc_list)
        root.addWidget(enc_card)
        self._enc_card = enc_card

        root.addStretch(1)

        # ── signals ────────────────────────────────────────────────────────────
        self.f_description.textChanged.connect(self._emit)
        self.f_category.textChanged.connect(self._emit)
        self.f_weight_lbs.valueChanged.connect(self._emit)
        self.f_weight_lbs.valueChanged.connect(self._update_weight_conv)
        # SCALE drives everything. The Pokémon Scale is the single size input; the
        # Offset only re-grounds the mon. Either one recomputes the derived Height,
        # the player scale + offset, and the preview. Height ft/in and Trainer
        # Scale/Offset are read-only outputs — they are only written programmatically
        # (blockSignals) and never feed back in.
        self.f_pokemon_scale.valueChanged.connect(self._recompute_from_scale)
        self.f_pokemon_offset.valueChanged.connect(self._recompute_from_scale)

    # ── helpers ───────────────────────────────────────────────────────────────

    def _emit(self, *_):
        if not self._loading:
            self.changed.emit()

    def _update_height_conv(self, *_):
        dm = _ftin_to_dm(self.f_height_ft.value(), self.f_height_in.value())
        self._height_conv.setText(f"= {dm} dm ({dm / 10:.1f} m)")

    def _update_weight_conv(self, *_):
        hg = _lbs_to_hg(self.f_weight_lbs.value())
        self._weight_conv.setText(f"= {hg} hg ({hg / 10:.1f} kg)")

    # ── SCALE ⇒ height + player. The Pokémon Scale (the spinbox) is the single size
    #    DRIVER the user sets by eye. Everything else is derived from it:
    #      • monster on-screen art = mon_occ × 256 / drawnScale (bigger monster = LOWER
    #        scale). The monster GROWS as you lower the scale — up to filling the 64px
    #        box. Its art is never drawn past the box (that clips).
    #      • the PLAYER stays full-size (trainerScale 256) while the monster still fits
    #        the box. Only once the user pushes the monster PAST the box does the player
    #        shrink (and drop) so the monster reads as taller — a SHORT monster never
    #        shrinks the player.
    #      • height_dm = 14 × (monster on-screen) / (player on-screen at full size).
    #    Confirmed against pokedex_screen.c — see docs/POKEDEX_SIZE_FACTS.md.

    @staticmethod
    def _trainer_offset_for_scale(tscale: int) -> int:
        """Player grounding offset for a given trainer scale — the ONE formula.
        Derived from vanilla: the player is grounded on a fixed engine ground line,
        so its offset is a pure function of its own scale (it drops as it shrinks)
        and never involves the monster's pokemonOffset."""
        toff = round(_PLAYER_GROUND_C - _PLAYER_FEET_K * 256.0 / max(int(tscale), 1))
        return max(-128, min(127, toff))

    def _scale_hard_min(self) -> int:
        """Smallest Pokémon-Scale the spinbox allows. Below this the player would be
        shrunk past the ~10px giant floor, so there's nothing more to show. In the
        box-fit model that scale is 4 × mon_occ × 10 / player_occ."""
        player_occ = max(self._player_occ_px, 1)
        mon_occ = max(self._mon_occ_px, 1)
        return max(1, -(-4 * mon_occ * _PLAYER_MIN_DISP_PX // player_occ))

    def _derive(self, scale: int) -> tuple[int, int, int, int]:
        """Turn the Pokémon-Scale driver into (drawnScale, trainerScale, trainerOffset,
        height_dm). This is the whole model in one place.

        Let intended = mon_occ × 256 / scale (the monster's wanted on-screen height):
          intended ≤ 64  → monster fits the box: drawnScale = scale, player FULL (256).
          intended > 64  → monster pinned at the box (drawnScale = mon_occ × 4, 64px) and
                           the player shrinks by the overflow: trainerScale = 256 ×
                           intended / 64.

        A SHORT monster (small art) therefore keeps the player full and just grows; the
        player only shrinks once the monster is pushed bigger than the whole box. The
        height is one continuous, monotonic curve height = 14 × mon_occ × 256 /
        (player_occ × scale) in both regimes — no pop at the crossover.
        """
        scale = max(int(scale), 1)
        player_occ = max(self._player_occ_px, 1)
        mon_occ = max(self._mon_occ_px, 1)
        intended = mon_occ * 256.0 / scale
        if intended <= _BOX_PX:
            drawn  = scale                      # monster grows with the scale, still fits
            tscale = _TRAINER_SCALE_FLOOR       # player full-size
        else:
            drawn  = max(1, round(mon_occ * 256.0 / _BOX_PX))   # monster pinned at the box
            tscale = round(_TRAINER_SCALE_FLOOR * intended / _BOX_PX)  # player shrinks
        # Giant clamp: the player never shrinks below ~10px on screen.
        tcap = int(player_occ * 256 / _PLAYER_MIN_DISP_PX)
        tscale = max(_TRAINER_SCALE_FLOOR, min(tscale, tcap, _SCALE_CEIL))
        toff = self._trainer_offset_for_scale(tscale)
        # Height from the ACTUAL drawn sizes (so the stored scales reproduce it on reload).
        height_dm = max(1, round(_PLAYER_HEIGHT_DM * mon_occ * tscale /
                                 (player_occ * drawn)))
        return drawn, tscale, toff, height_dm

    def _recompute_from_scale(self, *_):
        """Re-derive the read-only Height + player scale/offset + preview from the
        current Pokémon Scale. Called on every Scale / Offset edit and on load."""
        scale = max(int(self.f_pokemon_scale.value()), 1)
        poff  = int(self.f_pokemon_offset.value())
        drawn, tscale, toff, height_dm = self._derive(scale)
        ft, inch = _dm_to_ftin(height_dm)
        # Write the derived outputs without re-firing their signals.
        for w, v in ((self.f_trainer_scale, tscale),
                     (self.f_trainer_offset, toff),
                     (self.f_height_ft, ft),
                     (self.f_height_in, inch)):
            w.blockSignals(True); w.setValue(v); w.blockSignals(False)
        self._update_height_conv()
        # The preview draws the monster at the CAPPED (drawn) scale, not the raw driver,
        # so its art can never exceed the 64px box.
        self._size_preview.set_values(drawn, poff, tscale, toff)
        if not self._loading:
            self._emit()

    # ── public API ────────────────────────────────────────────────────────────

    def set_description_limits(self, max_chars_per_line: int, max_lines: int = 3) -> None:
        """Update the per-line and per-entry character limits (call after project load)."""
        self.f_description.set_limits(max_chars_per_line, max_lines)

    def set_project_root(self, root: str) -> None:
        """Set the pokefirered repo root so the trainer sprite can be found."""
        self._project_root = root or ""
        trainer_path = os.path.join(
            root, "graphics", "trainers", "front_pics", "red_front_pic.png"
        )
        self._trainer_sprite_path = trainer_path
        self._player_occ_px = _measure_occupied_height(
            trainer_path, fallback=_PLAYER_OCC_PX_FALLBACK)
        # The Scale floor is player-driven (giant clamp), so refresh it now that we
        # know the real player footprint.
        self.f_pokemon_scale.setMinimum(self._scale_hard_min())
        self._size_preview.set_trainer(trainer_path)

    def set_sprite(self, png_path: str | None,
                   species_const: str | None = None) -> None:
        """Render the header sprite re-indexed through the live palette.

        If *species_const* is supplied we resolve the palette through
        :mod:`core.sprite_palette_bus` — RAM-first (unsaved edits),
        disk-fallback. The palette may be mid-edit on the Graphics tab
        and not yet on disk; the bus gives us that edit.

        Without a species const we degrade to a flat :class:`QPixmap`
        load so callers that don't know the species still see something.
        """
        self._sprite_path = png_path
        self._species_const = species_const
        # Re-measure the mon's real art footprint. The Scale is never allowed to
        # enlarge the monster past natural (drawnScale ≥ 256), so the art can't clip
        # the box regardless of Scale; the spinbox floor is the player giant-clamp.
        self._mon_occ_px = _measure_occupied_height(
            png_path, fallback=_MON_OCC_PX_FALLBACK)
        self.f_pokemon_scale.setMinimum(self._scale_hard_min())
        if not png_path or not os.path.isfile(png_path):
            self._sprite_lbl.setPixmap(QPixmap())
            self._size_preview.set_pokemon(png_path)
            return
        pal = None
        if species_const and self._project_root:
            pal = _get_palette_bus().ensure_pokemon_palette(
                self._project_root, species_const, "normal",
            )
        pm = load_sprite_pixmap(png_path, pal)
        self._sprite_lbl.setPixmap(pm or QPixmap())
        self._size_preview.set_pokemon(png_path)

    # ── bus subscription ─────────────────────────────────────────────────────

    def _on_palette_changed(self, category: str, key: str) -> None:
        """Re-render the header sprite when OUR species' palette changes."""
        if category != CAT_POKEMON:
            return
        if not self._species_const or not self._sprite_path:
            return
        # Key is "<SPECIES_CONST>:<kind>"; normal is what the dex shows.
        if key != f"{self._species_const}:normal":
            return
        try:
            pal = _get_palette_bus().get_pokemon_palette(
                self._species_const, "normal",
            )
            if pal:
                pm = load_sprite_pixmap(self._sprite_path, pal)
                if pm is not None:
                    self._sprite_lbl.setPixmap(pm)
        except Exception:
            pass

    def load_entry(self, entry: dict, species_name: str = "",
                   sprite_path: str | None = None,
                   species_const: str | None = None):
        """Populate all fields from a pokedex.json entry dict.

        *species_const* (SPECIES_*) is used to resolve the live palette
        through the SpritePaletteBus so unsaved Graphics-tab edits show
        up on the dex card immediately.
        """
        self._loading = True
        try:
            self._dex_const = entry.get("dex_constant", "")

            self._hdr_name.setText(species_name or self._dex_const or "—")
            dex_num = entry.get("dex_num", "")
            self._hdr_dex.setText(f"#{dex_num:0>4}" if isinstance(dex_num, int) else "")
            self._hdr_const.setText(self._dex_const or "")
            self.set_sprite(sprite_path, species_const=species_const)
            self._species_const = species_const

            self.f_description.setPlainText(entry.get("descriptionText") or "")
            self.f_category.setText(entry.get("categoryName") or "")

            w = int(entry.get("weight", 0) or 0)
            self.f_weight_lbs.setValue(_hg_to_lbs(w))
            self._update_weight_conv()

            # DELIBERATELY IGNORE entry["height"] — every monster carries a stale
            # inherited height that means nothing. The Height shown is ALWAYS derived
            # live from the sprite scales below.
            #
            # Reconstruct the Pokémon-Scale DRIVER from BOTH stored scales so the
            # loaded state is a fixed point of the model — no "pop" on the first edit,
            # and vanilla entries round-trip. Height is preserved because
            #   height ∝ mon_occ × trainerScale / pokemonScale
            # and driver = 256 × pokemonScale / trainerScale reproduces that ratio.
            st_pscale = max(int(entry.get("pokemonScale", 256) or 256), 1)
            st_tscale = max(int(entry.get("trainerScale", 256) or 256), 1)
            if st_tscale <= _TRAINER_SCALE_FLOOR:
                driver = st_pscale                       # short mon: player was full-size
            else:                                        # tall mon: fold player-shrink back in
                driver = max(1, round(_NAT_SCALE * st_pscale / st_tscale))
            self.f_pokemon_scale.setValue(driver)
            self.f_pokemon_offset.setValue(int(entry.get("pokemonOffset", 0) or 0))

            # Derive Height + player scale/offset + preview from the driver. (The
            # spinbox valueChanged may or may not have fired above depending on whether
            # the value changed, so call it explicitly.)
            self._recompute_from_scale()

        finally:
            self._loading = False

    def set_encounters(self, records: list) -> None:
        """Set the wild encounter display from a list of EncounterRecord.

        Each record has: location, method, min_level, max_level, slot_count.
        """
        if not records:
            self._enc_list.setText(
                '<span style="color: #777; font-style: italic;">'
                'Not found in the wild</span>'
            )
            return

        # Method icons (text-based, no actual graphics needed)
        _METHOD_COLORS = {
            "Grass":      "#4caf50",
            "Surfing":    "#42a5f5",
            "Rock Smash": "#8d6e63",
            "Old Rod":    "#78909c",
            "Good Rod":   "#5c6bc0",
            "Super Rod":  "#7e57c2",
        }

        lines = []
        for r in records:
            color = _METHOD_COLORS.get(r.method, "#999")
            if r.min_level == r.max_level:
                lvl = f"Lv {r.min_level}"
            else:
                lvl = f"Lv {r.min_level}\u2013{r.max_level}"

            line = (
                f'<span style="color: {color};">\u25CF</span> '
                f'<b>{r.location}</b> \u2014 '
                f'<span style="color: {color};">{r.method}</span>'
                f' <span style="color: #888;">({lvl})</span>'
            )
            lines.append(line)

        self._enc_list.setText("<br>".join(lines))

    def collect(self, base: dict) -> dict:
        """Return updated copy of *base* with current field values.

        The stored monster scale is the DRAWN (capped) scale — never the raw driver —
        so it can never draw the art past the 64px box (clip) in-game. The player
        scale/offset and the height are the derived read-only values. Saving persists
        the derived height, which becomes the new stored (correct) value.
        """
        d = dict(base)
        drawn, tscale, toff, _height_dm = self._derive(self.f_pokemon_scale.value())
        d["descriptionText"] = self.f_description.toPlainText()
        d["categoryName"]    = self.f_category.text()
        d["height"]          = _ftin_to_dm(self.f_height_ft.value(), self.f_height_in.value())
        d["weight"]          = _lbs_to_hg(self.f_weight_lbs.value())
        d["pokemonScale"]    = drawn
        d["pokemonOffset"]   = self.f_pokemon_offset.value()
        d["trainerScale"]    = tscale
        d["trainerOffset"]   = toff
        return d

    def clear(self):
        self._loading = True
        try:
            self._dex_const = None
            self._species_const = None
            self._hdr_name.setText("—")
            self._hdr_dex.setText("")
            self._hdr_const.setText("")
            self._sprite_lbl.setPixmap(QPixmap())
            self.f_description.clear()
            self.f_category.clear()
            self.f_height_ft.setValue(0)
            self.f_height_in.setValue(0)
            self.f_weight_lbs.setValue(0)
            self.f_pokemon_scale.setMinimum(self._scale_hard_min())
            self.f_pokemon_scale.setValue(256)
            self.f_pokemon_offset.setValue(0)
            self.f_trainer_scale.setValue(256)   # read-only; player derived from Scale
            self.f_trainer_offset.setValue(-2)
            self._height_conv.setText("")
            self._weight_conv.setText("")
            # Drop the stale mon footprint so it can't leak into the next derivation;
            # the player pic is project-scoped and stays measured.
            self._mon_occ_px = _MON_OCC_PX_FALLBACK
            self._size_preview.set_pokemon(None)
            self._enc_list.setText(
                '<span style="color: #777; font-style: italic;">'
                'Not found in the wild</span>'
            )
        finally:
            self._loading = False
