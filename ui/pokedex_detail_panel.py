"""
ui/pokedex_detail_panel.py
Pokédex entry detail panel — shows all fields from pokedex.json
for the currently selected national dex entry.
"""

from __future__ import annotations

import os

from PyQt6.QtCore import Qt, pyqtSignal, QSize
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QFormLayout, QFrame, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from ui.dex_description_edit import DexDescriptionEdit

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
    # Approximate floor line (trainer bottom at scale=256, offset=0: y=74)
    _GROUND_Y    = 80

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
        self.setStyleSheet(_FIELD_SS)
        self._build()

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

        # Height: stored in dm (decimeters); show converted label
        self.f_height = QSpinBox()
        self.f_height.setRange(0, 9999)
        self.f_height.setSuffix(" dm")
        self.f_height.setToolTip("Height in decimeters (10 dm = 1 m)")
        self._height_conv = QLabel("")
        self._height_conv.setStyleSheet("color: #666; font-size: 10px;")
        height_row = QHBoxLayout()
        height_row.setSpacing(8)
        height_row.addWidget(self.f_height)
        height_row.addWidget(self._height_conv)
        id_form.addRow(_lbl("Height"), height_row)

        # Weight: stored in hg (hectograms); show converted label
        self.f_weight = QSpinBox()
        self.f_weight.setRange(0, 99999)
        self.f_weight.setSuffix(" hg")
        self.f_weight.setToolTip("Weight in hectograms (10 hg = 1 kg)")
        self._weight_conv = QLabel("")
        self._weight_conv.setStyleSheet("color: #666; font-size: 10px;")
        weight_row = QHBoxLayout()
        weight_row.setSpacing(8)
        weight_row.addWidget(self.f_weight)
        weight_row.addWidget(self._weight_conv)
        id_form.addRow(_lbl("Weight"), weight_row)

        root.addWidget(id_card)

        # ── Scale / Offset card ───────────────────────────────────────────────
        scale_card, scale_form = _card("Sprite Scale & Offset")

        self.f_pokemon_scale  = QSpinBox(); self.f_pokemon_scale.setRange(0, 65535)
        self.f_pokemon_offset = QSpinBox(); self.f_pokemon_offset.setRange(-128, 127)
        self.f_trainer_scale  = QSpinBox(); self.f_trainer_scale.setRange(0, 65535)
        self.f_trainer_offset = QSpinBox(); self.f_trainer_offset.setRange(-128, 127)

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
        for w in (self.f_height, self.f_weight,
                  self.f_pokemon_scale, self.f_pokemon_offset,
                  self.f_trainer_scale, self.f_trainer_offset):
            w.valueChanged.connect(self._emit)
        self.f_height.valueChanged.connect(self._update_height_conv)
        self.f_weight.valueChanged.connect(self._update_weight_conv)
        for w in (self.f_pokemon_scale, self.f_pokemon_offset,
                  self.f_trainer_scale, self.f_trainer_offset):
            w.valueChanged.connect(self._update_preview)

    # ── helpers ───────────────────────────────────────────────────────────────

    def _emit(self, *_):
        if not self._loading:
            self.changed.emit()

    def _update_preview(self, *_) -> None:
        self._size_preview.set_values(
            self.f_pokemon_scale.value(),
            self.f_pokemon_offset.value(),
            self.f_trainer_scale.value(),
            self.f_trainer_offset.value(),
        )

    def _update_height_conv(self, dm: int):
        m = dm / 10
        ft = dm * 0.0328084
        self._height_conv.setText(f"= {m:.1f} m  /  {ft:.1f} ft")

    def _update_weight_conv(self, hg: int):
        kg = hg / 10
        lb = hg * 0.220462
        self._weight_conv.setText(f"= {kg:.1f} kg  /  {lb:.1f} lb")

    # ── public API ────────────────────────────────────────────────────────────

    def set_description_limits(self, max_chars_per_line: int, max_lines: int = 3) -> None:
        """Update the per-line and per-entry character limits (call after project load)."""
        self.f_description.set_limits(max_chars_per_line, max_lines)

    def set_project_root(self, root: str) -> None:
        """Set the pokefirered repo root so the trainer sprite can be found."""
        trainer_path = os.path.join(
            root, "graphics", "trainers", "front_pics", "red_front_pic.png"
        )
        self._trainer_sprite_path = trainer_path
        self._size_preview.set_trainer(trainer_path)

    def set_sprite(self, png_path: str | None):
        if png_path and os.path.isfile(png_path):
            pm = QPixmap(png_path)
            self._sprite_lbl.setPixmap(pm)
        else:
            self._sprite_lbl.setPixmap(QPixmap())
        self._size_preview.set_pokemon(png_path)

    def load_entry(self, entry: dict, species_name: str = "",
                   sprite_path: str | None = None):
        """Populate all fields from a pokedex.json entry dict."""
        self._loading = True
        try:
            self._dex_const = entry.get("dex_constant", "")

            self._hdr_name.setText(species_name or self._dex_const or "—")
            dex_num = entry.get("dex_num", "")
            self._hdr_dex.setText(f"#{dex_num:0>4}" if isinstance(dex_num, int) else "")
            self._hdr_const.setText(self._dex_const or "")
            self.set_sprite(sprite_path)

            self.f_description.setPlainText(entry.get("descriptionText") or "")
            self.f_category.setText(entry.get("categoryName") or "")

            h = entry.get("height", 0) or 0
            self.f_height.setValue(int(h))
            self._update_height_conv(int(h))

            w = entry.get("weight", 0) or 0
            self.f_weight.setValue(int(w))
            self._update_weight_conv(int(w))

            self.f_pokemon_scale.setValue(int(entry.get("pokemonScale", 256) or 256))
            self.f_pokemon_offset.setValue(int(entry.get("pokemonOffset", 0) or 0))
            self.f_trainer_scale.setValue(int(entry.get("trainerScale", 256) or 256))
            self.f_trainer_offset.setValue(int(entry.get("trainerOffset", 0) or 0))

        finally:
            self._loading = False

        self._update_preview()

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
        """Return updated copy of *base* with current field values."""
        d = dict(base)
        d["descriptionText"] = self.f_description.toPlainText()
        d["categoryName"]    = self.f_category.text()
        d["height"]          = self.f_height.value()
        d["weight"]          = self.f_weight.value()
        d["pokemonScale"]    = self.f_pokemon_scale.value()
        d["pokemonOffset"]   = self.f_pokemon_offset.value()
        d["trainerScale"]    = self.f_trainer_scale.value()
        d["trainerOffset"]   = self.f_trainer_offset.value()
        return d

    def clear(self):
        self._loading = True
        try:
            self._dex_const = None
            self._hdr_name.setText("—")
            self._hdr_dex.setText("")
            self._hdr_const.setText("")
            self._sprite_lbl.setPixmap(QPixmap())
            self.f_description.clear()
            self.f_category.clear()
            self.f_height.setValue(0)
            self.f_weight.setValue(0)
            self.f_pokemon_scale.setValue(256)
            self.f_pokemon_offset.setValue(0)
            self.f_trainer_scale.setValue(256)
            self.f_trainer_offset.setValue(0)
            self._height_conv.setText("")
            self._weight_conv.setText("")
            self._size_preview.set_pokemon(None)
            self._enc_list.setText(
                '<span style="color: #777; font-style: italic;">'
                'Not found in the wild</span>'
            )
        finally:
            self._loading = False
