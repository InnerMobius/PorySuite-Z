"""Graphics tab for the Pokemon editor.

Full three-column layout:

  LEFT    : Front sprite, back sprite, icon, footprint thumbnails
  CENTER  : Battle scene preview (BattleBG + sprites + shadow + textbox)
            Spinboxes: Player Y (back y_offset), Enemy Y (front y_offset),
            Enemy Altitude (gEnemyMonElevation; >0 also shows the shadow).
  RIGHT   : Normal palette (16 clickable swatches)
            Shiny palette  (16 clickable swatches)
            Icon palette selector (0/1/2 dropdown) + three editable rows.

All edits live in an in-memory cache (GraphicsDataCache + per-species palette
dicts). Edits mark window dirty.  Call ``flush_to_disk()`` on Save to write.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QRegularExpression, QRect
from PyQt6.QtGui import (
    QColor, QPainter, QPixmap, QImage, QMouseEvent,
    QRegularExpressionValidator,
)
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QSpinBox,
    QCheckBox, QComboBox, QGroupBox, QPushButton, QColorDialog, QFrame,
    QDialog, QDialogButtonBox, QLineEdit, QFileDialog,
    QRadioButton, QMessageBox, QButtonGroup, QScrollArea,
)

from ui.graphics_data import (
    GraphicsDataCache, species_pal_paths, icon_palette_pal_path,
    species_slug_from_const,
)
from ui.palette_utils import read_jasc_pal, write_jasc_pal, clamp_to_gba
from ui.draggable_palette_row import DraggablePaletteRow
from core.gba_image_utils import (
    swap_palette_entries, export_indexed_png, _rebuild_color_table,
    remap_to_palette, find_closest_color,
)
from core.sprite_palette_bus import get_bus as _get_palette_bus


Color = Tuple[int, int, int]


def _reskin_indexed_image(img: QImage,
                          palette: List[Color]) -> Optional[QPixmap]:
    """Recolour an in-memory indexed QImage using a new 16-colour palette.

    Same contract as :func:`_reskin_indexed_png` but sources pixel data
    from an already-loaded QImage (e.g. a sprite that has been pixel-
    remapped by an "Index as Background" right-click but hasn't been
    written to disk yet).  Returns None on failure.
    """
    try:
        if img is None or img.isNull():
            return None
        if img.format() != QImage.Format.Format_Indexed8:
            img = img.convertToFormat(QImage.Format.Format_Indexed8)
        # Rebuild the colour table from scratch — alpha=0 at slot 0, 255
        # elsewhere.  See _reskin_indexed_png for the rationale (we do
        # not carry alpha by position; slot 0 is the only tRNS slot).
        ct: List[int] = []
        for i, (r, g, b) in enumerate(palette[:16]):
            a = 0 if i == 0 else 255
            ct.append((a << 24) | (r << 16) | (g << 8) | b)
        while len(ct) < 256:
            ct.append(0xFF000000)
        out = img.copy()
        out.setColorTable(ct)
        return QPixmap.fromImage(
            out.convertToFormat(QImage.Format.Format_ARGB32)
        )
    except Exception:
        return None


def _png_dims(path: str) -> Optional[Tuple[int, int]]:
    """Return `(width, height)` of a PNG without paying the cost of a
    full decode-into-pixmap.  Used by the Manual import flow to pick
    which target (front.png / back.png) to write a remapped sprite to.
    Returns None if the file is missing or unreadable.
    """
    if not path or not os.path.isfile(path):
        return None
    try:
        img = QImage(path)
        if img.isNull():
            return None
        return (img.width(), img.height())
    except Exception:
        return None


def _reskin_indexed_png(path: str, palette: List[Color]) -> Optional[QPixmap]:
    """Recolour an indexed-palette PNG using a new 16-colour palette.

    Assumes the PNG's palette order matches the species' .pal file (true for
    pokefirered sprites since both are generated from the same source), and
    that slot 0 is the GBA transparent slot (encoded via tRNS in the PNG).

    Returns None on any failure; caller should fall back to the original.
    """
    try:
        img = QImage(path)
        if img.isNull():
            return None
        return _reskin_indexed_image(img, palette)
    except Exception:
        return None


# ═════════════════════════════════════════════════════════════════════════════
# Hex colour picker dialog (replaces QColorDialog for palette editing)
# ═════════════════════════════════════════════════════════════════════════════

class HexColorDialog(QDialog):
    """Tiny colour picker: hex input + RGB spinboxes + live preview swatch.

    Values are automatically GBA-clamped (5-bit per channel) on exit.
    Much more obvious than QColorDialog's HTML-labelled hex field.
    """

    def __init__(self, initial: Color, title: str = "Pick Colour",
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)

        r, g, b = clamp_to_gba(*initial)
        self._r, self._g, self._b = r, g, b
        self._updating = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        # Preview swatch
        self._preview = QLabel()
        self._preview.setFixedHeight(40)
        self._preview.setStyleSheet(
            f"background-color: rgb({r},{g},{b}); border: 1px solid #1a1a1a;"
        )
        lay.addWidget(self._preview)

        # Hex row
        hex_row = QHBoxLayout()
        hex_row.addWidget(QLabel("Hex:"))
        self._hex_edit = QLineEdit(self._fmt_hex(r, g, b))
        # Allow optional leading # and up to 6 hex chars
        rx = QRegularExpression(r"^#?[0-9A-Fa-f]{0,6}$")
        self._hex_edit.setValidator(QRegularExpressionValidator(rx))
        self._hex_edit.setMaxLength(7)
        self._hex_edit.setFixedWidth(90)
        self._hex_edit.setToolTip(
            "Hex colour code — 6 digits, optional leading #\n"
            "Example: #E0D0A0 or E0D0A0"
        )
        hex_row.addWidget(self._hex_edit)
        hex_row.addStretch(1)
        lay.addLayout(hex_row)

        # RGB row
        rgb_row = QGridLayout()
        rgb_row.setHorizontalSpacing(8)
        self._r_spin = QSpinBox(); self._r_spin.setRange(0, 255); self._r_spin.setValue(r)
        self._g_spin = QSpinBox(); self._g_spin.setRange(0, 255); self._g_spin.setValue(g)
        self._b_spin = QSpinBox(); self._b_spin.setRange(0, 255); self._b_spin.setValue(b)
        for s in (self._r_spin, self._g_spin, self._b_spin):
            s.setSingleStep(8)  # GBA channels snap to multiples of 8
            s.wheelEvent = lambda e: e.ignore()  # safety for remote desktop
            s.setFixedWidth(64)
        rgb_row.addWidget(QLabel("R:"), 0, 0); rgb_row.addWidget(self._r_spin, 0, 1)
        rgb_row.addWidget(QLabel("G:"), 0, 2); rgb_row.addWidget(self._g_spin, 0, 3)
        rgb_row.addWidget(QLabel("B:"), 0, 4); rgb_row.addWidget(self._b_spin, 0, 5)
        rgb_row.setColumnStretch(6, 1)
        lay.addLayout(rgb_row)

        # Buttons
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

        # Wire signals
        self._hex_edit.textEdited.connect(self._on_hex)
        self._r_spin.valueChanged.connect(self._on_rgb)
        self._g_spin.valueChanged.connect(self._on_rgb)
        self._b_spin.valueChanged.connect(self._on_rgb)

    @staticmethod
    def _fmt_hex(r: int, g: int, b: int) -> str:
        return f"#{r:02X}{g:02X}{b:02X}"

    def _apply(self, r: int, g: int, b: int) -> None:
        r = max(0, min(255, r))
        g = max(0, min(255, g))
        b = max(0, min(255, b))
        self._r, self._g, self._b = r, g, b
        self._preview.setStyleSheet(
            f"background-color: rgb({r},{g},{b}); border: 1px solid #1a1a1a;"
        )

    def _on_hex(self, text: str) -> None:
        if self._updating:
            return
        s = text.strip().lstrip("#")
        if len(s) != 6:
            return
        try:
            r = int(s[0:2], 16); g = int(s[2:4], 16); b = int(s[4:6], 16)
        except ValueError:
            return
        self._updating = True
        try:
            self._r_spin.setValue(r)
            self._g_spin.setValue(g)
            self._b_spin.setValue(b)
            self._apply(r, g, b)
        finally:
            self._updating = False

    def _on_rgb(self, _v: int) -> None:
        if self._updating:
            return
        r = self._r_spin.value()
        g = self._g_spin.value()
        b = self._b_spin.value()
        self._updating = True
        try:
            self._hex_edit.setText(self._fmt_hex(r, g, b))
            self._apply(r, g, b)
        finally:
            self._updating = False

    def color(self) -> Color:
        return clamp_to_gba(self._r, self._g, self._b)

    @classmethod
    def get_color(cls, initial: Color, title: str = "Pick Colour",
                  parent: Optional[QWidget] = None) -> Optional[Color]:
        dlg = cls(initial, title, parent)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            return dlg.color()
        return None


# ═════════════════════════════════════════════════════════════════════════════
# Swatch widgets
# ═════════════════════════════════════════════════════════════════════════════

class PaletteSwatch(QLabel):
    """A single clickable color swatch that pops a QColorDialog."""
    color_changed = pyqtSignal(int, tuple)  # (index, (r,g,b))

    def __init__(self, index: int, color: Color = (0, 0, 0), parent=None):
        super().__init__(parent)
        self._index = index
        self._color: Color = color
        self.setFixedSize(22, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAutoFillBackground(True)
        # Use a QFrame-style thin border via setFrameShape so we don't need
        # a stylesheet (stylesheets cascade into any child dialogs opened
        # with this widget as parent, which would colour the whole picker).
        self.setFrameShape(QFrame.Shape.Box)
        self.setFrameShadow(QFrame.Shadow.Plain)
        self.setLineWidth(1)
        self.setToolTip(
            f"Palette slot {index}\nClick to edit (clamped to GBA 15-bit colour)"
        )
        self._refresh()

    def set_color(self, color: Color, emit: bool = False) -> None:
        color = clamp_to_gba(*color)
        if color != self._color:
            self._color = color
            self._refresh()
            if emit:
                self.color_changed.emit(self._index, color)

    def color(self) -> Color:
        return self._color

    def _refresh(self) -> None:
        r, g, b = self._color
        pal = self.palette()
        pal.setColor(self.backgroundRole(), QColor(r, g, b))
        self.setPalette(pal)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            r, g, b = self._color
            # Parent to the top-level window, not this swatch — keeps any
            # future swatch styling from bleeding into the dialog.
            top = self.window()
            dlg = QColorDialog(QColor(r, g, b), top)
            dlg.setWindowTitle(f"Palette slot {self._index}")
            dlg.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog)
            # Qt's built-in dialog labels its hex field "HTML:" which is
            # confusing. Walk the children and rename every such label.
            for lbl in dlg.findChildren(QLabel):
                if lbl.text().rstrip(":").strip().upper() in ("HTML", "&HTML"):
                    lbl.setText("Hex:")
            if dlg.exec() == QColorDialog.DialogCode.Accepted:
                qc = dlg.currentColor()
                if qc.isValid():
                    new = clamp_to_gba(qc.red(), qc.green(), qc.blue())
                    if new != self._color:
                        self._color = new
                        self._refresh()
                        self.color_changed.emit(self._index, new)
        super().mousePressEvent(event)


class PaletteSwatchRow(QWidget):
    """A horizontal row of 16 PaletteSwatches."""
    colors_changed = pyqtSignal()

    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self._swatches: List[PaletteSwatch] = []
        for i in range(16):
            s = PaletteSwatch(i)
            s.color_changed.connect(self._on_swatch_changed)
            layout.addWidget(s)
            self._swatches.append(s)
        layout.addStretch(1)

    def _on_swatch_changed(self, idx: int, color: Color) -> None:
        self.colors_changed.emit()

    def set_colors(self, colors: List[Color]) -> None:
        for i in range(16):
            c = colors[i] if i < len(colors) else (0, 0, 0)
            self._swatches[i].set_color(c, emit=False)

    def colors(self) -> List[Color]:
        return [s.color() for s in self._swatches]


# ═════════════════════════════════════════════════════════════════════════════
# Battle scene preview
# ═════════════════════════════════════════════════════════════════════════════

class BattleScenePreview(QWidget):
    """Compositing preview: BattleBG + front sprite (enemy) + back sprite
    (player) + optional shadow + textbox overlay.
    """

    # Layout constants (pixel positions on the 240x160 GBA-sized canvas,
    # then scaled 2× for display).
    SCALE = 2
    CANVAS_W = 240
    CANVAS_H = 160

    # Sprite-frame-CENTER coords taken from pokefirered
    # src/battle_anim_mons.c -> sBattlerCoords[0][*] (single battle)
    #   player (back)  = { 72, 80 }
    #   enemy (front) = { 176, 40 }
    ENEMY_CX = 176
    ENEMY_CY = 40
    PLAYER_CX = 72
    PLAYER_CY = 80

    def __init__(self, res_dir: str, parent=None):
        super().__init__(parent)
        self._bg: Optional[QPixmap] = None
        self._textbox: Optional[QPixmap] = None
        self._shadow: Optional[QPixmap] = None
        self._load_assets(res_dir)

        self._front_pix: Optional[QPixmap] = None
        self._back_pix: Optional[QPixmap] = None
        self._front_y_off = 0   # enemy y_offset
        self._back_y_off = 0    # player y_offset
        self._enemy_elevation = 0

        # Per-mon transform (pixel offset + scale) for battle-anim mon-tasks
        # (shake / sway / squeeze).  Identity by default — the Pokemon
        # Graphics tab never touches these, so its rendering is unchanged.
        # (dx, dy, sx, sy): offset in canvas px, scale as a display multiplier.
        self._front_fx = (0, 0, 1.0, 1.0)
        self._back_fx = (0, 0, 1.0, 1.0)
        # Cache of each mon pixmap's lowest opaque row (its "feet" line), keyed
        # by pixmap cacheKey, so a grounded scale (Bulk Up etc.) plants the art
        # bottom instead of lifting it off the textbox.
        self._art_bottom_cache: dict = {}
        # Mon visibility (battle-anim mon-hide: Dig / Fly disappear). True =
        # drawn normally. The Pokemon Graphics tab never sets these.
        self._front_visible = True
        self._back_visible = True
        # Mon "sink" (Dig burrow): the engine hides the mon sprite and wiggles a
        # BG layer it was copied onto. None = no sink; otherwise the number of
        # pixels to descend, drawn clipped at the ground (feet) line so the mon
        # bobs down into the hole. Overrides visibility while active.
        self._front_sink = None
        self._back_sink = None
        # Full OAM affine matrix (mA,mB,mC,mD,dx) for a mon that ROTATES (Horn
        # Drill's bow tilt, etc.) — scale-only can't show a tilt. None = no
        # rotation (use the fx scale/offset path). Pivots at the feet.
        self._front_aff = None
        self._back_aff = None
        # Mon CLONES (Double Team after-images, Quick Attack trail, Minimize, the
        # MetallicShine copy). Each is the ATTACKER's mon pic drawn faded at an
        # OFFSET from the mon's base — through the SAME planting path as the real
        # mon (incl. _back_y_off), so the hip cut stays hidden behind the textbox
        # exactly as it is for the mon. List of (off_x, off_y, hflip, vflip) in
        # canvas px relative to the mon's base centre. Empty = none.
        self._front_clones: list = []
        self._back_clones: list = []
        # Per-mon palette tint (coeff 0..16, BGR555 colour) the engine recorded
        # for the mon's palette slot — hit flashes, status tints, fade-to-colour.
        # (0, 0) = no tint. Applied to the mon pixmap before drawing.
        self._front_tint = (0, 0)
        self._back_tint = (0, 0)
        # Per-mon alpha (0..16) — BLDALPHA when the mon is in blend mode
        # (fade-to/from-invisible: Teleport, Substitute swap, ghost moves). 16 =
        # opaque. Drawn at alpha/16.
        self._front_alpha = 16
        self._back_alpha = 16
        # Per-mon greyscale (SetGreyscaleOrOriginalPalette — Perish Song). The
        # mon's palette is averaged to grey; False = normal colour.
        self._front_gray = False
        self._back_gray = False
        # Per-mon BG-copy SOUL SHADOW (Memento / Role Play). The engine copies the
        # mon onto a BG layer, blackens it, and stretches it via a per-scanline
        # vertical-offset buffer while a WIN0 clip narrows it to a vanishing
        # sliver. None = no shadow; otherwise a dict:
        #   {"baseY": int, "buf": [160 ints], "eva": 0..16, "win0h": (L, R)}
        # Drawn as a black silhouette of the mon, per-scanline-remapped from buf.
        self._front_shadow = None
        self._back_shadow = None
        self._front_distort = None     # per-scanline H warp of the mon (Acid Armor)
        self._back_distort = None      # Dragon Dance waver, etc.
        self._front_mosaic = 0         # MOSAIC pixelation level 0-15 (Transform)
        self._back_mosaic = 0
        self._front_override = None     # swap the mon's pic (Transform morph target)
        self._back_override = None
        self._bg_shake = (0, 0)         # battle-scene offset px (terrain/screen shake)
        self._mon_shake = (0, 0)        # both-mon offset px (sprite-layer gSpriteCoordOffset shake)
        self._mwin = None               # (pix, which, sx, sy, alpha): a scrolling BG
        #   clipped to a mon's silhouette + drawn IN FRONT (Stats Change arrows — the GBA OBJ-window)
        self._screen_tint = None        # (rgb, coeff 1..16): full-screen tint/brighten
        self._scene_tint = None         # (rgb, coeff 1..16): BACKGROUND-only tint, drawn
        #   BEHIND the mons + effect sprites (Moonlight/Morning Sun darken the BG via a
        #   F_PAL_BG palette blend that never touches the OBJ sprites, so the moon/sun and
        #   the Pokemon stay bright over the darkened scene)
        self._screen_invert = False     # full-screen colour inversion (InvertScreenColor)
        #   overlay — Morning Sun's white flash, Eruption's red tint (engine scene blend)

        # Optional battle-animation sprite overlay (used by the Battle
        # Anims tab; the Pokemon Graphics tab leaves this None so its
        # behaviour is unchanged).  Drawn frame-CENTERED on
        # (_anim_cx, _anim_cy), above the mons and below the textbox.
        self._anim_pix: Optional[QPixmap] = None
        self._anim_cx = self.ENEMY_CX
        self._anim_cy = self.ENEMY_CY
        # Effect layers that render BEHIND a mon (Protect shield, etc.). In-game
        # these moves copy the mon to a background layer and give the effect a
        # depth below it, so it sits behind the Pokemon. The engine now reports the
        # background's real depth, so the renderer can tell "behind" (Protect
        # shield, depth 3 > background 2) from "in front" (Swords Dance blade,
        # depth 2). _anim_behind_front draws just before the enemy (front) mon,
        # _anim_behind_back just before the player (back) mon. None when unused.
        self._anim_behind_back: Optional[QPixmap] = None
        self._anim_behind_front: Optional[QPixmap] = None
        # Battle-animation BACKGROUND (Surf/Cosmic/Sandstorm/...): a full-screen
        # image drawn OVER the battle BG and BEHIND the mons, scrolled by
        # (_anim_bg_x, _anim_bg_y). None = no anim background this move.
        self._anim_bg: Optional[QPixmap] = None
        self._anim_bg_x = 0
        self._anim_bg_y = 0
        # Per-scanline HORIZONTAL warp of the anim background (Extrasensory's
        # psychic-BG distortion: the engine writes a per-row REG_BGnHOFS sine via
        # the scanline buffer). None = no warp (flat tiled scroll); else a list of
        # CANVAS_H absolute per-row HOFS values — each row is drawn shifted by its
        # own value, producing the wavy distortion.
        self._anim_bg_distort = None
        # Per-scanline ALPHA of the anim background (Surf's wave: the engine writes
        # a per-row BLDALPHA; the water is drawn at each row's eva/16 opacity, so a
        # rising band is opaque water and the rest is transparent — the scene shows
        # through). None = fully opaque; else a list of CANVAS_H eva values (0..16).
        self._anim_bg_alpha = None

        w = self.CANVAS_W * self.SCALE
        h = self.CANVAS_H * self.SCALE
        self.setFixedSize(w, h)

    def _load_assets(self, res_dir: str) -> None:
        try:
            self._bg = QPixmap(os.path.join(res_dir, "BattleBG.png"))
        except Exception:
            self._bg = None
        try:
            self._textbox = QPixmap(os.path.join(res_dir, "BattleTextBox.png"))
        except Exception:
            self._textbox = None
        try:
            self._shadow = QPixmap(os.path.join(res_dir, "Shadow.png"))
        except Exception:
            self._shadow = None

    # -- setters, each triggers a repaint ------------------------------------
    def set_front_pixmap(self, pix: Optional[QPixmap]) -> None:
        self._front_pix = pix
        self.update()

    def set_back_pixmap(self, pix: Optional[QPixmap]) -> None:
        self._back_pix = pix
        self.update()

    def set_front_y_offset(self, y: int) -> None:
        self._front_y_off = int(y)
        self.update()

    def set_back_y_offset(self, y: int) -> None:
        self._back_y_off = int(y)
        self.update()

    def set_enemy_elevation(self, e: int) -> None:
        self._enemy_elevation = int(e)
        self.update()

    def _art_bottom_row(self, pix: Optional[QPixmap]) -> int:
        """Lowest opaque pixel row of a mon pixmap (its visible "feet" line).
        Cached by cacheKey. Falls back to the frame bottom if fully opaque."""
        if pix is None or pix.isNull():
            return 0
        key = pix.cacheKey()
        cached = self._art_bottom_cache.get(key)
        if cached is not None:
            return cached
        img = pix.toImage()
        h, w = img.height(), img.width()
        bottom = h - 1
        for y in range(h - 1, -1, -1):
            if any(img.pixelColor(x, y).alpha() > 0 for x in range(w)):
                bottom = y
                break
        self._art_bottom_cache[key] = bottom
        return bottom

    def set_mon_transform(self, which: str, dx: int = 0, dy: int = 0,
                          sx: float = 1.0, sy: float = 1.0,
                          ground: bool = False) -> None:
        """Set a per-mon transform used by the Battle Anims tab to reproduce
        mon-acting tasks (shake / sway / squeeze).  ``which`` is "front"
        (enemy) or "back" (player).  Identity (0, 0, 1, 1) restores the
        normal draw.  No-op repaint avoidance: only updates on change.

        ``ground=True`` (mon grow/shrink — Bulk Up, Minimize, ...) plants the
        art's BOTTOM edge so the mon scales up from its feet instead of about
        the frame centre.  Without it, scaling about the centre lifts the art
        bottom off the textbox and exposes the back sprite's hard "hip" cut
        edge.  ``dy`` is then computed from the art bottom (the engine's
        grounding y-offset is replaced); ``dx`` still applies."""
        pix = self._back_pix if which != "front" else self._front_pix
        if ground and pix is not None and (sx != 1.0 or sy != 1.0):
            abr = self._art_bottom_row(pix)
            fh = pix.height()
            # dy that keeps row `abr` fixed when scaling about the frame centre:
            #   dy = (1 - sy) * (abr - fh/2)
            dy = int(round((1.0 - sy) * (abr - fh / 2.0)))
        fx = (int(dx), int(dy), float(sx), float(sy))
        if which == "front":
            if fx == self._front_fx:
                return
            self._front_fx = fx
        else:
            if fx == self._back_fx:
                return
            self._back_fx = fx
        self.update()

    def set_anim_bg(self, pix: Optional[QPixmap], x: int = 0, y: int = 0,
                    distort=None, alpha=None) -> None:
        """Set (or clear with None) the battle-animation background, scrolled to
        (x, y). Drawn over the battle BG and behind the mons. ``distort`` is an
        optional list of CANVAS_H per-row HOFS values (Extrasensory's psychic-BG
        warp); ``alpha`` is an optional list of CANVAS_H per-row eva values 0..16
        (Surf's wave — each row drawn at eva/16 opacity, transparent rows let the
        scene show). When either is set the BG is drawn per-scanline; otherwise the
        flat tiled scroll runs."""
        self._anim_bg = pix
        self._anim_bg_x = int(x)
        self._anim_bg_y = int(y)
        self._anim_bg_distort = list(distort) if distort else None
        self._anim_bg_alpha = list(alpha) if alpha else None
        self.update()

    def set_mon_visible(self, which: str, visible: bool) -> None:
        """Show/hide a mon (battle-anim mon-hide for Dig / Fly)."""
        visible = bool(visible)
        if which == "front":
            if self._front_visible == visible:
                return
            self._front_visible = visible
        else:
            if self._back_visible == visible:
                return
            self._back_visible = visible
        self.update()

    def set_mon_sink(self, which: str, descent) -> None:
        """Dig burrow: ``descent`` pixels to drop the mon, drawn clipped at the
        ground (feet) line so it bobs down into the hole. ``None`` clears the
        sink. Active sink overrides normal visibility."""
        descent = None if descent is None else int(descent)
        if which == "front":
            if self._front_sink == descent:
                return
            self._front_sink = descent
        else:
            if self._back_sink == descent:
                return
            self._back_sink = descent
        self.update()

    def set_mon_affine(self, which: str, mA: int, mB: int, mC: int, mD: int,
                       dx: int = 0, dy: int = 0) -> None:
        """Render a mon through its FULL OAM affine matrix (rotation + scale),
        pivoted at the sprite CENTRE (as the GBA does) with the engine's
        (dx, dy) offset — for a mon that TILTS (Horn Drill's bow). dy is the
        engine's SetBattlerSpriteYOffsetFromRotation grounding offset (downward),
        which hinges the mon and tilts it DOWN under the textbox. None clears."""
        aff = (None if mA is None
               else (int(mA), int(mB), int(mC), int(mD), int(dx), int(dy)))
        if which == "front":
            if aff == self._front_aff:
                return
            self._front_aff = aff
        else:
            if aff == self._back_aff:
                return
            self._back_aff = aff
        self.update()

    def set_mon_clones(self, which: str, clones: list) -> None:
        """Set the attacker's mon CLONES (Double Team after-images etc.).
        ``clones`` is a list of (off_x, off_y, hflip, vflip) offsets in canvas
        px from the mon's base centre; drawn faded through the SAME planted path
        as the real mon, so the hip cut hides behind the textbox identically.
        ``which`` is the attacker side ("back"/"front"). [] clears."""
        clones = list(clones)
        if which == "front":
            if clones == self._front_clones:
                return
            self._front_clones = clones
        else:
            if clones == self._back_clones:
                return
            self._back_clones = clones
        self.update()

    def set_mon_tint(self, which: str, coeff: int, color: int) -> None:
        """Set a mon's palette tint (coeff 0..16 toward BGR555 color) — hit
        flash, status tint, fade-to-colour. (0, 0) clears."""
        t = (int(coeff), int(color))
        if which == "front":
            if t == self._front_tint:
                return
            self._front_tint = t
        else:
            if t == self._back_tint:
                return
            self._back_tint = t
        self.update()

    def set_mon_alpha(self, which: str, alpha: int) -> None:
        """Set a mon's opacity (0..16) — BLDALPHA fade-to/from-invisible. 16 =
        opaque."""
        a = max(0, min(16, int(alpha)))
        if which == "front":
            if a == self._front_alpha:
                return
            self._front_alpha = a
        else:
            if a == self._back_alpha:
                return
            self._back_alpha = a
        self.update()

    def set_mon_gray(self, which: str, gray: bool) -> None:
        """Greyscale a mon (SetGreyscaleOrOriginalPalette — Perish Song)."""
        gray = bool(gray)
        if which == "front":
            if gray == self._front_gray:
                return
            self._front_gray = gray
        else:
            if gray == self._back_gray:
                return
            self._back_gray = gray
        self.update()

    def set_mon_shadow(self, which: str, shadow) -> None:
        """Set a mon's Memento soul-shadow (or None to clear). ``shadow`` is a
        dict {"baseY", "buf" (160 ints), "eva" (0..16), "win0h" (L, R)} from the
        engine's scanline-stretch state."""
        if which == "front":
            if shadow == self._front_shadow:
                return
            self._front_shadow = shadow
        else:
            if shadow == self._back_shadow:
                return
            self._back_shadow = shadow
        self.update()

    def set_mon_distort(self, which: str, offsets) -> None:
        """A mon copied to a BG layer + horizontally warped per-scanline (Acid
        Armor melt, Dragon Dance waver): ``offsets`` is a list of per-screen-row X
        shifts (or None to clear). The mon's OWN pixels, NOT a black shadow."""
        if which == "front":
            self._front_distort = offsets
        else:
            self._back_distort = offsets
        self.update()

    def set_mon_mosaic(self, which: str, level: int) -> None:
        """MOSAIC pixelation level (0-15) for a mon — the engine's REG_OFFSET_MOSAIC
        during a Transform morph. 0 = sharp."""
        level = max(0, min(15, int(level)))
        if which == "front":
            self._front_mosaic = level
        else:
            self._back_mosaic = level

    def set_mon_override(self, which: str, pix) -> None:
        """Swap a mon's drawn pic (Transform's mid-morph species change). None =
        the mon's own pic."""
        ok = pix if (pix is not None and not pix.isNull()) else None
        if which == "front":
            self._front_override = ok
        else:
            self._back_override = ok

    def set_bg_shake(self, dx: int, dy: int) -> None:
        """Offset the battle scene by (dx, dy) px — the terrain/screen shake
        (BG3 scroll from AnimShakeMonOrBattleTerrain). (0,0) = steady."""
        self._bg_shake = (int(dx), int(dy))

    def set_mon_shake(self, dx: int, dy: int) -> None:
        """Offset BOTH battler mons by (dx, dy) px — the sprite-layer screen shake
        (gSpriteCoordOffset from AnimShakeMonOrBattleTerrain — Metal Claw / Dragon
        Claw impact). In-game this jitters every coordOffset-enabled OAM sprite,
        which is the battler mons; effect sprites are not enabled, so they hold
        steady (matching the GBA). (0,0) = steady."""
        self._mon_shake = (int(dx), int(dy))

    def set_mon_window_bg(self, pix=None, which: str = "back", sx: int = 0,
                          sy: int = 0, alpha: float = 0.65) -> None:
        """Draw a scrolling BG (``pix``) CLIPPED to mon ``which``'s silhouette and
        IN FRONT of it — the GBA OBJ-window effect (Stats Change fills the mon
        with scrolling up/down arrows, masked by an invisible mon-copy window).
        ``sx``/``sy`` scroll the pattern; ``alpha`` is the blend. None clears."""
        self._mwin = ((pix, which, int(sx), int(sy), float(alpha))
                      if pix is not None and not pix.isNull() else None)
        self.update()

    def set_screen_tint(self, color_bgr555: int = 0, coeff: int = 0) -> None:
        """Full-screen tint/brighten OVERLAY drawn over the whole scene — the
        engine's dominant scene palette blend (Morning Sun's white flash that
        brightens the screen, Eruption's red tint). ``coeff`` 0 clears."""
        c = max(0, min(16, int(coeff)))
        if c <= 0:
            self._screen_tint = None
        else:
            v = int(color_bgr555) & 0xFFFF
            r = (v & 31) << 3
            g = ((v >> 5) & 31) << 3
            b = ((v >> 10) & 31) << 3
            self._screen_tint = ((r | r >> 5, g | g >> 5, b | b >> 5), c)
        self.update()

    def set_scene_tint(self, color_bgr555: int = 0, coeff: int = 0) -> None:
        """BACKGROUND-only tint drawn BEHIND the mons and effect sprites — for a
        palette blend that targets the background palettes only (Moonlight's dark
        sky, Morning Sun's bright sky). The Pokemon and the move's foreground
        effects (the moon, sparkles) are OBJ sprites the blend never touches, so
        they stay bright over the tinted scene. ``coeff`` 0 clears."""
        c = max(0, min(16, int(coeff)))
        if c <= 0:
            self._scene_tint = None
        else:
            v = int(color_bgr555) & 0xFFFF
            r = (v & 31) << 3
            g = ((v >> 5) & 31) << 3
            b = ((v >> 10) & 31) << 3
            self._scene_tint = ((r | r >> 5, g | g >> 5, b | b >> 5), c)
        self.update()

    def set_screen_invert(self, on: bool) -> None:
        """Full-screen colour INVERSION (AnimTask_InvertScreenColor → InvertPlttBuffer)
        — a true negative-colour flash. Rendered as a white difference-blend so it
        inverts the actual scene instead of faking a tint colour."""
        on = bool(on)
        if getattr(self, "_screen_invert", False) != on:
            self._screen_invert = on
            self.update()

    @staticmethod
    def _mosaic_pixmap(pix, level):
        """GBA MOSAIC: each (level+1)x(level+1) block shows one source pixel —
        downscale then nearest-neighbour upscale back to the original size."""
        if level <= 0 or pix is None or pix.isNull():
            return pix
        f = level + 1
        w, h = pix.width(), pix.height()
        small = pix.scaled(max(1, w // f), max(1, h // f),
                           Qt.AspectRatioMode.IgnoreAspectRatio,
                           Qt.TransformationMode.FastTransformation)
        return small.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio,
                            Qt.TransformationMode.FastTransformation)

    def reset_mon_transforms(self) -> None:
        """Restore both mons to their untransformed, visible draw (end of play)."""
        self._front_distort = None
        self._back_distort = None
        self._front_mosaic = self._back_mosaic = 0
        self._front_override = self._back_override = None
        self._bg_shake = (0, 0)
        self._mon_shake = (0, 0)
        self._mwin = None
        self._screen_tint = None
        self._scene_tint = None
        self._screen_invert = False
        changed = (self._front_fx != (0, 0, 1.0, 1.0)
                   or self._back_fx != (0, 0, 1.0, 1.0)
                   or not self._front_visible or not self._back_visible
                   or self._front_sink is not None or self._back_sink is not None
                   or self._front_aff is not None or self._back_aff is not None
                   or self._front_clones or self._back_clones
                   or self._front_tint != (0, 0) or self._back_tint != (0, 0)
                   or self._front_alpha != 16 or self._back_alpha != 16
                   or self._front_gray or self._back_gray
                   or self._front_shadow is not None or self._back_shadow is not None)
        self._front_fx = (0, 0, 1.0, 1.0)
        self._back_fx = (0, 0, 1.0, 1.0)
        self._front_visible = True
        self._back_visible = True
        self._front_sink = None
        self._back_sink = None
        self._front_aff = None
        self._back_aff = None
        self._front_clones = []
        self._back_clones = []
        self._front_tint = (0, 0)
        self._back_tint = (0, 0)
        self._front_alpha = 16
        self._back_alpha = 16
        self._front_gray = False
        self._back_gray = False
        self._front_shadow = None
        self._back_shadow = None
        if changed:
            self.update()

    def _paint_mon_affine(self, p, pix, frame_left, frame_top, fw, fh, aff, s):
        """Draw a mon through its OAM affine matrix, pivoting at the sprite
        CENTRE (exactly as GBA OAM affine does) with the engine's (dx, dy)
        offset. dy is SetBattlerSpriteYOffsetFromRotation's grounding push
        (downward), so a bow tilt hinges + tilts the mon DOWN — under the
        textbox, which is drawn on top and covers it — instead of lifting the
        hip. The OAM matrix maps screen→texture, so we draw with its inverse."""
        from PyQt6.QtGui import QTransform
        mA, mB, mC, mD, dx, dy = aff
        if any(abs(v) > 4096 for v in (mA, mB, mC, mD)):
            p.drawPixmap(frame_left * s, frame_top * s, fw * s, fh * s, pix)
            return
        oam = QTransform(mA / 256.0, mC / 256.0, mB / 256.0, mD / 256.0, 0.0, 0.0)
        inv, ok = oam.inverted()
        if not ok:
            p.drawPixmap(frame_left * s, frame_top * s, fw * s, fh * s, pix)
            return
        draw_left = frame_left + dx
        draw_top = frame_top + dy
        cx = (draw_left + fw / 2.0) * s
        cy = (draw_top + fh / 2.0) * s
        p.save()
        p.translate(cx, cy)
        p.setTransform(inv, True)
        p.translate(-cx, -cy)
        p.drawPixmap(int(draw_left * s), int(draw_top * s),
                     int(fw * s), int(fh * s), pix)
        p.restore()

    def _paint_mon_sink(self, p, pix, frame_left, frame_top, descent, s) -> None:
        """Draw a mon descending by ``descent`` px, clipped at its ground (feet)
        line, so it sinks into the hole (Dig). The part below the feet line is
        hidden (in the hole); as descent grows the mon vanishes head-last."""
        if pix is None or pix.isNull():
            return
        abr = self._art_bottom_row(pix)
        ground = frame_top + abr + 1          # canvas y of the feet line
        p.save()
        p.setClipRect(0, 0, self.CANVAS_W * s, int(round(ground * s)))
        p.drawPixmap(frame_left * s, (frame_top + descent) * s,
                     pix.width() * s, pix.height() * s, pix)
        p.restore()

    def set_anim_pixmap(self, pix: Optional[QPixmap],
                        cx: Optional[int] = None,
                        cy: Optional[int] = None) -> None:
        """Overlay a battle-animation sprite frame, centered on (cx, cy)
        in 240x160 canvas coords (defaults to the target battler spot).
        Pass ``None`` to clear it."""
        self._anim_pix = pix
        if cx is not None:
            self._anim_cx = int(cx)
        if cy is not None:
            self._anim_cy = int(cy)
        # Clearing the effect overlay (pix is None) also clears the behind-mon
        # layers, so every existing stop/clear site drops them too.
        if pix is None:
            self._anim_behind_back = None
            self._anim_behind_front = None
        self.update()

    def set_anim_behind(self, back_pix: Optional[QPixmap],
                        front_pix: Optional[QPixmap]) -> None:
        """Set the effect layers that render BEHIND the mons. *back_pix* draws
        behind the player (back) mon, *front_pix* behind the enemy (front) mon.
        Full-scene overlays (240x160 canvas coords, origin 0,0). None clears."""
        self._anim_behind_back = back_pix
        self._anim_behind_front = front_pix
        self.update()

    @staticmethod
    def _bgr555_rgb(c):
        """BGR555 (the GBA palette word) → (r, g, b) 8-bit, 5→8 bit expanded."""
        r = (c & 31) << 3
        g = ((c >> 5) & 31) << 3
        b = ((c >> 10) & 31) << 3
        return (r | r >> 5, g | g >> 5, b | b >> 5)

    @staticmethod
    def tint_pixmap(pix, coeff, color):
        """Return a copy of ``pix`` palette-blended toward BGR555 ``color`` by
        ``coeff``/16 — the exact BlendPalette result (out = base*(1-k) + color*k),
        applied ONLY to opaque pixels (SourceAtop leaves transparency untouched).
        ``coeff`` 0 (or a null pix) returns the original. Used for every tint the
        engine records: status flashes, BlendColorCycle, MetallicShine, the dark
        Double-Team blend, fade-to-colour, etc."""
        if coeff <= 0 or pix is None or pix.isNull():
            return pix
        a = min(16, coeff) / 16.0
        r, g, b = BattleScenePreview._bgr555_rgb(color & 0x7FFF)
        out = QPixmap(pix.size())
        out.fill(QColor(0, 0, 0, 0))
        p = QPainter(out)
        p.drawPixmap(0, 0, pix)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceAtop)
        p.fillRect(out.rect(), QColor(r, g, b, int(round(a * 255))))
        p.end()
        return out

    _gray_cache: dict = {}

    @staticmethod
    def gray_pixmap(pix):
        """Desaturate a pixmap to greyscale (the GBA average (r+g+b)/3),
        preserving alpha — for SetGreyscaleOrOriginalPalette (Perish Song greys
        the music notes + the mons as they flip). Cached by pixmap content, so
        a greyed mon held for 100 frames is computed once."""
        if pix is None or pix.isNull():
            return pix
        key = pix.cacheKey()
        cached = BattleScenePreview._gray_cache.get(key)
        if cached is not None:
            return cached
        img = pix.toImage().convertToFormat(QImage.Format.Format_ARGB32)
        for y in range(img.height()):
            for x in range(img.width()):
                c = img.pixelColor(x, y)
                if c.alpha() == 0:
                    continue
                avg = (c.red() + c.green() + c.blue()) // 3
                img.setPixelColor(x, y, QColor(avg, avg, avg, c.alpha()))
        out = QPixmap.fromImage(img)
        if len(BattleScenePreview._gray_cache) > 128:
            BattleScenePreview._gray_cache.clear()
        BattleScenePreview._gray_cache[key] = out
        return out

    _silhouette_cache: dict = {}

    @staticmethod
    def _silhouette_image(pix):
        """Return a QImage of ``pix`` with every opaque pixel forced to BLACK
        (alpha shape preserved) — the FillPalette(RGB_BLACK) silhouette the
        Memento / Role Play shadow tasks paint onto the BG-copied mon. Cached by
        pixmap content."""
        if pix is None or pix.isNull():
            return None
        key = pix.cacheKey()
        cached = BattleScenePreview._silhouette_cache.get(key)
        if cached is not None:
            return cached
        src = pix.toImage().convertToFormat(QImage.Format.Format_ARGB32)
        out = QImage(src.size(), QImage.Format.Format_ARGB32_Premultiplied)
        out.fill(0)
        qp = QPainter(out)
        qp.drawImage(0, 0, src)
        qp.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceAtop)
        qp.fillRect(out.rect(), QColor(0, 0, 0, 255))
        qp.end()
        if len(BattleScenePreview._silhouette_cache) > 64:
            BattleScenePreview._silhouette_cache.clear()
        BattleScenePreview._silhouette_cache[key] = out
        return out

    def _paint_mon_distort(self, p, pix, frame_left, frame_top, fw, fh, offsets, s):
        """Draw a mon copied to a BG layer + HORIZONTALLY warped per scanline
        (Acid Armor melt, Dragon Dance waver): each screen row of the mon is drawn
        shifted by ``offsets[screen_row]``. The mon's own pixels (not a black
        shadow — that's the vertical Memento path)."""
        n = len(offsets)
        for ry in range(fh):
            sy = frame_top + ry
            off = offsets[sy] if 0 <= sy < n else 0
            if off is None:        # this scanline melted off-screen (Acid Armor)
                continue
            p.drawPixmap((frame_left + int(off)) * s, sy * s, fw * s, s,
                         pix, 0, ry, fw, 1)

    def _paint_mon_shadow(self, p, pix, frame_left, frame_top, fw, fh, shadow, s):
        """Draw the Memento soul-shadow: a black silhouette of the mon, vertically
        remapped per the engine's per-scanline VOFS buffer (the upward stretch),
        horizontally clipped to the WIN0 bounds (the narrowing sliver), at
        BLDALPHA-EVA opacity.

        For each GBA screen row y the BG copy shows mon content shifted by
        delta = (signed)buf[y] - baseY; at delta 0 the shadow exactly overlays the
        mon, so the source frame row is (y - frame_top) + delta. Rows whose source
        lands outside [0, fh) draw nothing — that covers the scanline ramp regions
        (which map to a constant off-frame row) and everything above/below the
        stretch, so the silhouette tapers to a streak off the top of the mon."""
        sil = self._silhouette_image(pix)
        if sil is None:
            return
        buf = shadow.get("buf") or []
        if len(buf) < self.CANVAS_H:
            return
        baseY = int(shadow.get("baseY", 0))
        L, R = shadow.get("win0h", (0, self.CANVAS_W))
        L = max(0, min(self.CANVAS_W, int(L)))
        R = max(0, min(self.CANVAS_W, int(R)))
        if R <= L:
            return                       # WIN0 collapsed → shadow has vanished
        eva = max(0, min(16, int(shadow.get("eva", 16))))
        if eva <= 0:
            return
        shimg = QImage(self.CANVAS_W, self.CANVAS_H,
                       QImage.Format.Format_ARGB32_Premultiplied)
        shimg.fill(0)
        qp = QPainter(shimg)
        qp.setClipRect(L, 0, R - L, self.CANVAS_H)   # WIN0 horizontal clip
        for y in range(self.CANVAS_H):
            v = buf[y]
            if v >= 0x8000:
                v -= 0x10000                          # u16 → signed VOFS
            src_row = (y - frame_top) + (v - baseY)
            if 0 <= src_row < fh:
                qp.drawImage(QRect(frame_left, y, fw, 1),
                             sil, QRect(0, src_row, fw, 1))
        qp.end()
        p.setOpacity(eva / 16.0)
        p.drawImage(QRect(0, 0, self.CANVAS_W * s, self.CANVAS_H * s),
                    shimg, QRect(0, 0, self.CANVAS_W, self.CANVAS_H))
        p.setOpacity(1.0)

    @staticmethod
    def _draw_mon(p, pix, frame_left, frame_top, fw, fh, fx, s):
        """Draw a mon frame, applying a per-mon transform (offset + scale).

        The identity transform (0, 0, 1, 1) takes the EXACT original draw path
        so the Pokemon Graphics tab (which never sets a transform) is
        pixel-identical.  A non-identity transform scales the frame about its
        CENTER (the GBA OAM affine center) and shifts it by (dx, dy), matching
        ``AnimTask_ScaleMonAndRestore`` / ``ShakeMon`` / ``SwayMon``."""
        dx, dy, sx, sy = fx
        if dx == 0 and dy == 0 and sx == 1.0 and sy == 1.0:
            p.drawPixmap(frame_left * s, frame_top * s, fw * s, fh * s, pix)
            return
        # Scale about the frame centre, then offset.
        cx = frame_left + fw / 2.0 + dx
        cy = frame_top + fh / 2.0 + dy
        new_w = fw * sx
        new_h = fh * sy
        left = (cx - new_w / 2.0) * s
        top = (cy - new_h / 2.0) * s
        p.drawPixmap(int(round(left)), int(round(top)),
                     int(round(new_w * s)), int(round(new_h * s)), pix)

    # -- paint ---------------------------------------------------------------
    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        s = self.SCALE

        # Background — offset by the terrain/screen shake (BG3 scroll). Drawn a few
        # px oversized + shifted so the shake never exposes a gap at the edges.
        if self._bg and not self._bg.isNull():
            shx, shy = self._bg_shake
            if shx or shy:
                pad = 10
                p.drawPixmap(int((shx - pad) * s), int((shy - pad) * s),
                             (self.CANVAS_W + 2 * pad) * s,
                             (self.CANVAS_H + 2 * pad) * s, self._bg)
            else:
                p.drawPixmap(0, 0, self.CANVAS_W * s, self.CANVAS_H * s, self._bg)
        else:
            p.fillRect(self.rect(), QColor(40, 40, 40))

        # Battle-animation background (Surf/Cosmic/Sandstorm/...): drawn OVER
        # the battle BG and BEHIND the mons, tiled with a scroll offset.
        if self._anim_bg is not None and not self._anim_bg.isNull():
            bw, bh = self._anim_bg.width(), self._anim_bg.height()
            if bw > 0 and bh > 0:
                oy = self._anim_bg_y % bh
                dist = self._anim_bg_distort
                alpha = self._anim_bg_alpha
                if dist is not None or alpha is not None:
                    # Per-scanline path: each screen row drawn as a 1px strip with
                    # its own horizontal offset (Extrasensory warp = dist[y]; else
                    # the flat scroll) and its own opacity (Surf wave = alpha[y]/16;
                    # transparent rows are skipped so the scene shows through).
                    for yy in range(self.CANVAS_H):
                        if alpha is not None:
                            a = (alpha[yy] / 16.0) if yy < len(alpha) else 0.0
                            if a <= 0.0:
                                continue                 # transparent row → scene
                            p.setOpacity(min(1.0, a))
                        src_y = (yy + self._anim_bg_y) % bh
                        ox = ((int(dist[yy]) if (dist and yy < len(dist))
                               else self._anim_bg_x)) % bw
                        xx = -ox
                        while xx < self.CANVAS_W:
                            p.drawPixmap(xx * s, yy * s, bw * s, s,
                                         self._anim_bg, 0, src_y, bw, 1)
                            xx += bw
                    p.setOpacity(1.0)
                else:
                    ox = self._anim_bg_x % bw
                    yy = -oy
                    while yy < self.CANVAS_H:
                        xx = -ox
                        while xx < self.CANVAS_W:
                            p.drawPixmap(xx * s, yy * s, bw * s, bh * s, self._anim_bg)
                            xx += bw
                        yy += bh

        # BACKGROUND-only scene tint (Moonlight dark sky, Morning Sun bright sky):
        # a F_PAL_BG palette blend darkens/brightens the background but NOT the OBJ
        # sprites, so it's drawn HERE — over the background, but BEHIND the shadow,
        # mons, and every effect sprite (the moon, the sun, sparkles), which all
        # stay bright over the tinted scene.
        if self._scene_tint is not None:
            (_sr, _sg, _sb), _sc = self._scene_tint
            p.fillRect(0, 0, self.CANVAS_W * s, self.CANVAS_H * s,
                       QColor(_sr, _sg, _sb, int(255 * _sc / 16)))

        # Shadow is created at (enemy_x, enemy_y + 29) per pokefirered
        # (src/battle_gfx_sfx_util.c :: LoadAndCreateEnemyShadowSprites).
        # It's a FIXED position — independent of the sprite frame. The game
        # shows it exactly when gEnemyMonElevation[species] != 0, so the preview
        # mirrors that: shadow iff the Enemy Altitude is above 0.
        shadow_visible = (self._enemy_elevation > 0
                          and self._shadow and not self._shadow.isNull())
        if shadow_visible:
            sw = self._shadow.width()
            sh = self._shadow.height()
            sx = (self.ENEMY_CX - sw // 2) * s
            sy = (self.ENEMY_CY + 29 - sh // 2) * s
            p.drawPixmap(sx, sy, sw * s, sh * s, self._shadow)

        # Mon CLONES (Double Team after-images, Quick Attack trail, Minimize,
        # MetallicShine copy) — faded, BEHIND the mons and UNDER the textbox,
        # drawn through the SAME planted path as the real mon (same base centre +
        # y_offset) so the back sprite's hip cut stays hidden exactly as it does
        # for the mon. Each offset is relative to the mon's base, in canvas px.
        if self._back_clones or self._front_clones:
            from PyQt6.QtGui import QTransform as _QT

            def _draw_clones(pix, base_cx, base_cy, clones):
                if pix is None or pix.isNull():
                    return
                cw, ch = pix.width(), pix.height()
                for off_x, off_y, hf, vf, cf, col, al, gr in clones:
                    cp = pix
                    if cf > 0:               # the clone's own palette blend
                        cp = self.tint_pixmap(cp, cf, col)   # (Double Team → black)
                    if gr:                   # greyscaled clone
                        cp = self.gray_pixmap(cp)
                    if hf or vf:
                        cp = cp.transformed(_QT().scale(-1 if hf else 1,
                                                        -1 if vf else 1))
                    fl = base_cx - cw // 2 + off_x
                    ft = base_cy - ch // 2 + off_y
                    # The clone's real alpha (its OAM blend mode + BLDALPHA);
                    # Double Team's setalpha 12,8 → ~0.75, not a flat guess.
                    p.setOpacity(max(0.05, min(1.0, al / 16.0)))
                    p.drawPixmap(fl * s, ft * s, cw * s, ch * s, cp)

            _draw_clones(self._back_pix, self.PLAYER_CX,
                         self.PLAYER_CY + self._back_y_off, self._back_clones)
            _draw_clones(self._front_pix, self.ENEMY_CX,
                         self.ENEMY_CY + self._front_y_off - self._enemy_elevation,
                         self._front_clones)
            p.setOpacity(1.0)

        # Effect layer drawn BEHIND the enemy (front) mon (Protect cast by the
        # enemy): full-scene overlay, origin 0,0.
        if self._anim_behind_front and not self._anim_behind_front.isNull():
            p.drawPixmap(0, 0, self.CANVAS_W * s, self.CANVAS_H * s,
                         self._anim_behind_front)

        # Enemy (front) sprite — pokefirered draws the 64x64 frame
        # CENTERED on sBattlerCoords, plus y_offset pushes it DOWN,
        # minus enemy elevation pushes it UP.
        if self._front_pix and not self._front_pix.isNull():
            _fbase = (self._front_override if self._front_override is not None
                      else self._front_pix)   # Transform morph swaps the pic
            fpix = self.tint_pixmap(_fbase, *self._front_tint)
            if self._front_gray:
                fpix = self.gray_pixmap(fpix)
            if self._front_mosaic > 0:         # Transform MOSAIC pixelation
                fpix = self._mosaic_pixmap(fpix, self._front_mosaic)
            fw = fpix.width()
            fh = fpix.height()
            frame_top = (self.ENEMY_CY - fh // 2
                         + self._front_y_off - self._enemy_elevation
                         + self._mon_shake[1])
            frame_left = self.ENEMY_CX - fw // 2 + self._mon_shake[0]
            if self._front_alpha < 16:
                p.setOpacity(self._front_alpha / 16.0)
            if self._front_sink is not None:
                self._paint_mon_sink(p, fpix, frame_left, frame_top,
                                     self._front_sink, s)
            elif self._front_aff is not None:
                self._paint_mon_affine(p, fpix, frame_left, frame_top,
                                       fw, fh, self._front_aff, s)
            elif self._front_distort is not None:
                self._paint_mon_distort(p, fpix, frame_left, frame_top,
                                        fw, fh, self._front_distort, s)
            elif self._front_visible:
                self._draw_mon(p, fpix, frame_left, frame_top,
                               fw, fh, self._front_fx, s)
            if self._front_alpha < 16:
                p.setOpacity(1.0)

        # Effect layer drawn BEHIND the player (back) mon (Protect cast by the
        # player): full-scene overlay, origin 0,0.
        if self._anim_behind_back and not self._anim_behind_back.isNull():
            p.drawPixmap(0, 0, self.CANVAS_W * s, self.CANVAS_H * s,
                         self._anim_behind_back)

        # Player (back) sprite — same frame-center rule, back y_offset
        # pushes DOWN.
        if self._back_pix and not self._back_pix.isNull():
            _bbase = (self._back_override if self._back_override is not None
                      else self._back_pix)   # Transform morph swaps the pic
            bpix = self.tint_pixmap(_bbase, *self._back_tint)
            if self._back_gray:
                bpix = self.gray_pixmap(bpix)
            if self._back_mosaic > 0:          # Transform MOSAIC pixelation
                bpix = self._mosaic_pixmap(bpix, self._back_mosaic)
            bw = bpix.width()
            bh = bpix.height()
            frame_top = (self.PLAYER_CY - bh // 2 + self._back_y_off
                         + self._mon_shake[1])
            frame_left = self.PLAYER_CX - bw // 2 + self._mon_shake[0]
            if self._back_alpha < 16:
                p.setOpacity(self._back_alpha / 16.0)
            if self._back_sink is not None:
                self._paint_mon_sink(p, bpix, frame_left, frame_top,
                                     self._back_sink, s)
            elif self._back_aff is not None:
                self._paint_mon_affine(p, bpix, frame_left, frame_top,
                                       bw, bh, self._back_aff, s)
            elif self._back_distort is not None:
                self._paint_mon_distort(p, bpix, frame_left, frame_top,
                                        bw, bh, self._back_distort, s)
            elif self._back_visible:
                self._draw_mon(p, bpix, frame_left, frame_top,
                               bw, bh, self._back_fx, s)
            if self._back_alpha < 16:
                p.setOpacity(1.0)

        # Mon-windowed scrolling BG (Stats Change arrows): composite the scrolling
        # pattern INTO the affected mon's silhouette and draw it IN FRONT — the GBA
        # OBJ-window effect (an invisible mon-copy window masks the BG to the mon).
        if self._mwin is not None:
            bgpix, which, wsx, wsy, walpha = self._mwin
            if which == "front":
                mon = self._front_override or self._front_pix
                mcx = self.ENEMY_CX + self._mon_shake[0]
                mcy = (self.ENEMY_CY + self._front_y_off
                       - self._enemy_elevation + self._mon_shake[1])
            else:
                mon = self._back_override or self._back_pix
                mcx = self.PLAYER_CX + self._mon_shake[0]
                mcy = self.PLAYER_CY + self._back_y_off + self._mon_shake[1]
            if (mon is not None and not mon.isNull()
                    and not bgpix.isNull()):
                mw, mh = mon.width(), mon.height()
                bw2, bh2 = bgpix.width(), bgpix.height()
                if mw > 0 and mh > 0 and bw2 > 0 and bh2 > 0:
                    result = QPixmap(mw, mh)
                    result.fill(Qt.GlobalColor.transparent)
                    rp = QPainter(result)
                    oy = wsy % bh2
                    ox = wsx % bw2
                    yy = -oy
                    while yy < mh:
                        xx = -ox
                        while xx < mw:
                            rp.drawPixmap(xx, yy, bgpix)
                            xx += bw2
                        yy += bh2
                    # Keep the arrows ONLY where the mon is opaque (the OBJ-window).
                    rp.setCompositionMode(
                        QPainter.CompositionMode.CompositionMode_DestinationIn)
                    rp.drawPixmap(0, 0, mon)
                    rp.end()
                    fl = (mcx - mw // 2) * s
                    ft = (mcy - mh // 2) * s
                    p.setOpacity(max(0.1, min(1.0, walpha)))
                    p.drawPixmap(int(fl), int(ft), mw * s, mh * s, result)
                    p.setOpacity(1.0)

        # Memento / Role Play SOUL SHADOW — a black silhouette of the mon copied
        # to a BG layer (priority 2, drawn in FRONT of the battler sprites the
        # shadow task pushed to priority 3), stretched upward by the scanline
        # buffer and clipped to a narrowing WIN0 sliver. After the mons, under
        # the textbox. Frame box matches each mon's draw box exactly.
        if (self._front_shadow is not None
                and self._front_pix and not self._front_pix.isNull()):
            fw = self._front_pix.width()
            fh = self._front_pix.height()
            ft = (self.ENEMY_CY - fh // 2
                  + self._front_y_off - self._enemy_elevation)
            fl = self.ENEMY_CX - fw // 2
            self._paint_mon_shadow(p, self._front_pix, fl, ft, fw, fh,
                                   self._front_shadow, s)
        if (self._back_shadow is not None
                and self._back_pix and not self._back_pix.isNull()):
            bw = self._back_pix.width()
            bh = self._back_pix.height()
            bt = self.PLAYER_CY - bh // 2 + self._back_y_off
            bl = self.PLAYER_CX - bw // 2
            self._paint_mon_shadow(p, self._back_pix, bl, bt, bw, bh,
                                   self._back_shadow, s)

        # Battle-animation sprite overlay (Battle Anims tab) — frame
        # CENTERED on (_anim_cx, _anim_cy), above the mons, below the
        # textbox.  Approximate placement: real animations move the
        # sprite around via script, but a centered overlay conveys
        # "this effect plays on the target" for the editor preview.
        if self._anim_pix and not self._anim_pix.isNull():
            aw = self._anim_pix.width()
            ah = self._anim_pix.height()
            ax = (self._anim_cx - aw // 2) * s
            ay = (self._anim_cy - ah // 2) * s
            p.drawPixmap(ax, ay, aw * s, ah * s, self._anim_pix)

        # Full-screen tint/brighten OVERLAY over the whole SCENE — Morning Sun's
        # white flash that brightens the screen, Eruption's red tint. Drawn over
        # the bg + mons + effect sprites but UNDER the textbox (so the message
        # stays readable). Alpha = the engine's blend coefficient.
        if self._screen_tint is not None:
            (_tr, _tg, _tb), _tc = self._screen_tint
            p.fillRect(0, 0, self.CANVAS_W * s, self.CANVAS_H * s,
                       QColor(_tr, _tg, _tb, int(255 * _tc / 16)))

        # Full-screen colour INVERSION (InvertScreenColor) — a white difference-blend
        # negates the scene's colours (true inversion, not a fake tint). Over the
        # scene, under the textbox, same as the tint.
        if getattr(self, "_screen_invert", False):
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Difference)
            p.fillRect(0, 0, self.CANVAS_W * s, self.CANVAS_H * s, QColor(255, 255, 255))
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        # Textbox overlay at the bottom
        if self._textbox and not self._textbox.isNull():
            tw = self._textbox.width()
            th = self._textbox.height()
            tx = 0
            ty = (self.CANVAS_H - th) * s
            p.drawPixmap(tx, ty, tw * s, th * s, self._textbox)

        p.end()


# ═════════════════════════════════════════════════════════════════════════════
# Main widget
# ═════════════════════════════════════════════════════════════════════════════

class GraphicsTabWidget(QWidget):
    """Drop-in replacement for the contents of the Pokemon Graphics sub-tab."""

    modified = pyqtSignal()  # anything user-editable was touched

    def __init__(self, res_dir: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._res_dir = res_dir
        self._project_root: Optional[str] = None
        self._data: Optional[GraphicsDataCache] = None
        self._current_species: Optional[str] = None
        self._loading = False  # suppress signals during programmatic load

        # Per-species palette cache (normal + shiny)
        # { species: {'normal': [16 tuples], 'shiny': [16 tuples]} }
        self._palettes: Dict[str, Dict[str, List[Color]]] = {}
        # Which folder each cached palette was loaded from. A base and its
        # forms share one cache key (the base const) but live in different
        # folders; when the folder changes we must reload so a form shows ITS
        # OWN palette instead of the base's stale cached one.
        self._pal_folder: Dict[str, str] = {}
        # Palette dirtiness is tracked per-channel so a SHINY-only change never
        # rewrites the normal front/back PNGs (importing a shiny palette must
        # leave the normal graphics untouched). Normal changes write normal.pal
        # AND re-bake the front/back PNGs (their baked colour table must match);
        # shiny changes write shiny.pal ONLY.
        self._normal_pal_dirty: set[str] = set()   # write normal.pal + bake PNGs
        self._shiny_pal_dirty: set[str] = set()    # write shiny.pal only

        # Per-species raw indexed QImage cache + per-species file paths.
        # Populated lazily when the user triggers "Index as Background"
        # right-click — that is the ONLY path on this tab that touches
        # PNG pixel data.  Cached images live in memory at Format_Indexed8
        # with their original pixel values (possibly post-remap after a
        # right-click).  Preview uses the cached image when present so
        # the remap is visible live; plain palette edits / drag-reorder
        # never read this cache.  On save, any species listed in
        # _sprite_png_dirty has its cached images written back to disk.
        self._sprite_imgs: Dict[str, Dict[str, QImage]] = {}
        self._sprite_paths: Dict[str, Dict[str, str]] = {}
        self._sprite_png_dirty: set[str] = set()

        # Imported icon / footprint graphics awaiting write on Save (these
        # slots aren't covered by the front/back bake path above, so they get
        # their own dirty sets + the icon keeps its imported colour table).
        self._icon_png_dirty: set[str] = set()
        self._footprint_png_dirty: set[str] = set()
        # Raw (un-remapped) artwork for a pending imported icon, kept so the
        # icon can be re-fitted whenever the species' shared icon-palette slot
        # (0/1/2) changes — the icon never owns its palette.
        self._icon_import_src: Dict[str, QImage] = {}

        # Icon palette storage (per-palette-slot, shared across all species)
        # { 0: [16 tuples], 1: [...], 2: [...] }
        self._icon_palettes: Dict[int, List[Color]] = {}
        self._icon_pal_dirty: set[int] = set()

        # Sprite-sheet source pixmaps (for recolour / animation)
        self._front_src: Optional[QPixmap] = None  # full 64x64
        self._back_src: Optional[QPixmap] = None
        self._icon_src: Optional[QPixmap] = None   # full 32x64 (two frames)
        self._icon_src_path: str = ""
        self._icon_recoloured: Optional[QPixmap] = None  # palette-applied
        self._front_src_path: str = ""
        self._back_src_path: str = ""
        self._foot_src_path: str = ""
        self._icon_frame = 0
        self._icon_timer = QTimer(self)
        self._icon_timer.setInterval(400)  # ~2.5 fps, matches Info tab
        self._icon_timer.timeout.connect(self._tick_icon_anim)
        self._icon_timer.start()
        self._preview_shiny = False  # False=normal, True=shiny

        # Folder the user last imported a graphic/palette from. Shared across
        # EVERY import dialog on this tab (front/back/icon/footprint sprites +
        # palette imports) so it "sticks" when swapping normal↔shiny or moving
        # between species — and across app restarts (persisted via QSettings).
        self._last_import_dir: str = ""
        try:
            from PyQt6.QtCore import QSettings as _QS
            saved = _QS("PorySuite", "PorySuiteZ").value(
                "pokemon_graphics/last_import_dir", "")
            if saved and os.path.isdir(str(saved)):
                self._last_import_dir = str(saved)
        except Exception:
            pass

        self._build_ui()

    # ────────────────────────────────────────────────────────── build UI ──
    def _build_ui(self) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(12)

        # ── LEFT COLUMN ── sprite thumbnails ────────────────────────────
        left = QVBoxLayout()
        left.setSpacing(8)

        self._front_thumb = self._make_thumb(64, "Front", "front")
        self._back_thumb = self._make_thumb(64, "Back", "back")
        self._foot_thumb = self._make_thumb(64, "Footprint", "footprint")

        for group in (self._front_thumb[0], self._back_thumb[0],
                      self._foot_thumb[0]):
            left.addWidget(group)

        self._import_set_btn = QPushButton("Import Normal + Shiny Set…")
        self._import_set_btn.setToolTip(
            "Import an artist's normal + shiny PNGs at once. The normal sprite's "
            "pixels become the shared sprite; the shiny palette is auto-mapped "
            "from the shiny PNG pixel-for-pixel, so BOTH display exactly as "
            "drawn — even when the normal and shiny PNGs were indexed separately "
            "and their palettes don't line up.")
        self._import_set_btn.clicked.connect(self._import_sprite_set)
        left.addWidget(self._import_set_btn)

        self._open_folder_btn = QPushButton("Open Graphics Folder")
        self._open_folder_btn.setToolTip(
            "Open this species' folder in your OS file browser"
        )
        left.addWidget(self._open_folder_btn)
        left.addStretch(1)

        outer.addLayout(left, 0)

        # ── CENTER COLUMN ── battle preview + positioning spinboxes ─────
        center = QVBoxLayout()
        center.setSpacing(8)

        preview_group = QGroupBox("Battle Scene Preview")
        pg_layout = QVBoxLayout(preview_group)
        pg_layout.setContentsMargins(8, 16, 8, 8)
        pg_layout.setSpacing(6)
        self._preview = BattleScenePreview(self._res_dir)
        pg_layout.addWidget(self._preview, 0, Qt.AlignmentFlag.AlignHCenter)

        # Positioning row
        pos_row = QGridLayout()
        pos_row.setHorizontalSpacing(10)
        pos_row.setVerticalSpacing(4)

        self._player_y = QSpinBox()
        self._player_y.setRange(-128, 127)
        self._player_y.setToolTip(
            "Player back sprite Y offset.\n"
            "Higher value = sprite drawn lower on the player platform.\n"
            "(gMonBackPicCoords[SPECIES].y_offset)"
        )

        self._enemy_y = QSpinBox()
        self._enemy_y.setRange(-128, 127)
        self._enemy_y.setToolTip(
            "Enemy front sprite Y offset.\n"
            "Higher value = sprite drawn lower on the enemy platform.\n"
            "(gMonFrontPicCoords[SPECIES].y_offset)"
        )

        self._enemy_alt = QSpinBox()
        self._enemy_alt.setRange(0, 255)
        self._enemy_alt.setToolTip(
            "Enemy altitude — how many pixels the sprite floats above the\n"
            "platform, AND whether it casts a battle shadow: any value above 0\n"
            "shows the shadow; 0 = grounded with no shadow. This matches the\n"
            "game exactly (the engine shows the shadow when the elevation is\n"
            "nonzero).  (gEnemyMonElevation[SPECIES])"
        )

        self._shiny_preview_cb = QCheckBox("Preview Shiny")
        self._shiny_preview_cb.setChecked(False)
        self._shiny_preview_cb.setToolTip(
            "Re-skin the preview sprites using the Shiny Palette\n"
            "instead of the Normal Palette. Does not write anything."
        )

        pos_row.addWidget(QLabel("Player Y:"), 0, 0)
        pos_row.addWidget(self._player_y, 0, 1)
        pos_row.addWidget(QLabel("Enemy Y:"), 0, 2)
        pos_row.addWidget(self._enemy_y, 0, 3)
        pos_row.addWidget(QLabel("Enemy Altitude:"), 1, 0)
        pos_row.addWidget(self._enemy_alt, 1, 1)
        pos_row.addWidget(self._shiny_preview_cb, 1, 2, 1, 2)

        pg_layout.addLayout(pos_row)
        center.addWidget(preview_group)
        center.addStretch(1)

        outer.addLayout(center, 1)

        # ── RIGHT COLUMN ── palettes (in a scroll area so they stay
        # visible when the window is narrow) ──────────────────────────
        right_container = QWidget()
        right_container.setMinimumWidth(400)   # 16×22 swatches + spacing + margins
        right = QVBoxLayout(right_container)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(10)

        # Normal palette — draggable so the user can pick which colour
        # is treated as transparent (drop on index 0 = "BG").
        self._normal_row = DraggablePaletteRow()
        right.addWidget(self._wrap(
            "Normal Palette  (drag to reorder — slot 0 is the transparent slot)",
            self._normal_row,
        ))

        # Shiny palette — same drag-reorder behaviour. Reordering either
        # the normal or shiny row applies the SAME order to the other
        # palette + reindexes the front/back PNGs, keeping shiny visually
        # correct in-game.
        self._shiny_row = DraggablePaletteRow()
        right.addWidget(self._wrap(
            "Shiny Palette  (drag to reorder — slot 0 is the transparent slot)",
            self._shiny_row,
        ))

        # Import Palette from PNG button + Normal/Shiny target selector
        import_group = QGroupBox("Import Palette from PNG")
        ig_import = QVBoxLayout(import_group)
        ig_import.setContentsMargins(8, 16, 8, 8)
        ig_import.setSpacing(6)

        target_row = QHBoxLayout()
        target_row.setSpacing(10)
        self._import_normal_rb = QRadioButton("Normal")
        self._import_shiny_rb = QRadioButton("Shiny")
        self._import_normal_rb.setChecked(True)
        self._import_target_group = QButtonGroup(self)
        self._import_target_group.addButton(self._import_normal_rb, 0)
        self._import_target_group.addButton(self._import_shiny_rb, 1)
        target_row.addWidget(QLabel("Target:"))
        target_row.addWidget(self._import_normal_rb)
        target_row.addWidget(self._import_shiny_rb)
        target_row.addStretch(1)
        ig_import.addLayout(target_row)

        self._import_png_btn = QPushButton("Import PNG...")
        self._import_png_btn.setToolTip(
            "Import a palette from a PNG — PALETTE ONLY, the sprite is\n"
            "not replaced. An indexed (palette-mode) PNG has its 16\n"
            "colours read straight into the Normal or Shiny palette.\n"
            "(A non-indexed PNG can't give a clean 16-colour palette, so\n"
            "it falls back to the manual picker, which does save a sprite.)"
        )
        ig_import.addWidget(self._import_png_btn)

        self._import_png_manual_btn = QPushButton("Import PNG Manually...")
        self._import_png_manual_btn.setToolTip(
            "Open the manual palette picker on a PNG (any format —\n"
            "indexed or full-colour).  You choose which colours land\n"
            "in which slot, mark the BG/transparent slot, and reorder\n"
            "freely.  WHAT YOU ARRANGE IS WHAT GETS SAVED: the picked\n"
            "image is remapped to your exact layout and saved as the\n"
            "sprite, together with the palette, on File → Save.\n\n"
            "(For Shiny target this sets shiny.pal only — shiny shares\n"
            "the normal sprite's pixels.)"
        )
        ig_import.addWidget(self._import_png_manual_btn)

        self._import_pal_btn = QPushButton("Import .pal File...")
        self._import_pal_btn.setToolTip(
            "Pick a JASC .pal file and load its 16 colours into the\n"
            "Normal or Shiny palette (whichever radio is selected above).\n"
            "The palette's existing order is preserved as-is — no\n"
            "automatic transparent-slot rearrangement. Click Save to\n"
            "write to disk."
        )
        ig_import.addWidget(self._import_pal_btn)

        right.addWidget(import_group)

        # Icon palette (0/1/2 + editable rows)
        icon_group = QGroupBox("Menu Icon Palette")
        ig = QVBoxLayout(icon_group)
        ig.setContentsMargins(8, 16, 8, 8)
        ig.setSpacing(6)

        sel_row = QHBoxLayout()
        sel_row.setSpacing(8)
        # Animated icon preview (64x64 displayed, 32x32 source per frame)
        self._icon_preview = QLabel()
        self._icon_preview.setFixedSize(64, 64)
        self._icon_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_preview.setStyleSheet(
            "background: #181818; border: 1px solid #333;"
        )
        self._icon_preview.setToolTip(
            "Live preview of this species' menu icon using the selected\n"
            "icon palette. Animates between the two frames."
        )
        sel_row.addWidget(self._icon_preview)
        sel_row.addSpacing(6)
        sel_row.addWidget(QLabel("Palette:"))
        self._icon_pal_combo = QComboBox()
        self._icon_pal_combo.addItems(["0", "1", "2"])
        self._icon_pal_combo.setToolTip(
            "Which of the 3 shared icon palettes this species uses.\n"
            "(gMonIconPaletteIndices[SPECIES])"
        )
        # Prevent accidental wheel scrolling
        self._icon_pal_combo.wheelEvent = lambda e: e.ignore()
        sel_row.addWidget(self._icon_pal_combo)
        sel_row.addStretch(1)
        ig.addLayout(sel_row)

        self._import_icon_btn = QPushButton("Import Menu Icon…")
        self._import_icon_btn.setToolTip(
            "Replace this species' menu icon (mini) with a PNG you pick.\n"
            "The icon is a 32×64 sheet (two stacked 32×32 frames). In-game\n"
            "the icon is coloured by the shared icon palette selected above,\n"
            "so pick a matching palette or edit it below. Saved on File → Save.")
        self._import_icon_btn.clicked.connect(
            lambda _=False: self._import_graphic("icon"))
        ig.addWidget(self._import_icon_btn)

        # Three editable swatch rows
        self._icon_rows: List[PaletteSwatchRow] = []
        for i in range(3):
            row = PaletteSwatchRow()
            self._icon_rows.append(row)
            ig.addWidget(self._wrap(f"Icon Palette {i}", row))

        right.addWidget(icon_group)
        right.addStretch(1)

        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setWidget(right_container)
        right_scroll.setFrameShape(QFrame.Shape.NoFrame)
        right_scroll.setMinimumWidth(416)  # container + scrollbar margin
        outer.addWidget(right_scroll, 0)

        # ── Wire signals ────────────────────────────────────────────────
        self._player_y.valueChanged.connect(self._on_player_y)
        self._enemy_y.valueChanged.connect(self._on_enemy_y)
        self._enemy_alt.valueChanged.connect(self._on_enemy_alt)
        self._shiny_preview_cb.toggled.connect(self._on_shiny_preview_toggled)

        self._normal_row.colors_changed.connect(self._on_normal_changed)
        self._shiny_row.colors_changed.connect(self._on_shiny_changed)
        # Drag-reorder: either row drives a lockstep reorder of BOTH
        # palettes plus a re-index of the front/back PNGs.
        self._normal_row.palette_reordered.connect(
            lambda f, t: self._on_palette_reordered("normal", f, t)
        )
        self._shiny_row.palette_reordered.connect(
            lambda f, t: self._on_palette_reordered("shiny", f, t)
        )
        # Right-click → Index as Background.  Both rows share a single
        # handler because the operation is lockstep on BOTH palettes
        # (the front/back PNGs are shared between normal and shiny, so
        # remapping pixels has to be matched by a lockstep .pal swap on
        # both rows or one render side will go wrong).
        self._normal_row.swatch_set_as_bg.connect(self._on_set_swatch_as_bg)
        self._shiny_row.swatch_set_as_bg.connect(self._on_set_swatch_as_bg)
        self._icon_pal_combo.currentIndexChanged.connect(self._on_icon_idx)
        for i, row in enumerate(self._icon_rows):
            row.colors_changed.connect(
                lambda _i=i: self._on_icon_pal_changed(_i)
            )

        self._open_folder_btn.clicked.connect(self._open_graphics_folder)
        self._import_png_btn.clicked.connect(self._import_palette_from_png)
        self._import_png_manual_btn.clicked.connect(
            self._import_palette_from_png_manual)
        self._import_pal_btn.clicked.connect(self._import_palette_from_pal)

    def _make_thumb(self, display_size: int, title: str, slot: str = ""):
        box = QGroupBox(title)
        bl = QVBoxLayout(box)
        bl.setContentsMargins(6, 14, 6, 6)
        bl.setSpacing(4)
        lbl = QLabel()
        lbl.setFixedSize(display_size, display_size)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet("background: #181818; border: 1px solid #333;")
        bl.addWidget(lbl, 0, Qt.AlignmentFlag.AlignHCenter)
        btn = None
        if slot:
            btn = QPushButton("Import…")
            btn.setToolTip(
                f"Replace this species' {title.lower()} image with a PNG you "
                f"pick.\nAn indexed (≤16-colour) PNG is used directly; any "
                f"other\nPNG opens the colour picker to remap it. Nothing is "
                f"written\nto disk until you click File → Save.")
            btn.clicked.connect(
                lambda _=False, s=slot: self._import_graphic(s))
            bl.addWidget(btn)
        return box, lbl, btn

    def _wrap(self, title: str, inner: QWidget) -> QGroupBox:
        g = QGroupBox(title)
        gl = QVBoxLayout(g)
        gl.setContentsMargins(8, 14, 8, 8)
        gl.addWidget(inner)
        return g

    # ────────────────────────────────────────────────────────── loading ──
    def set_project_root(self, root: str) -> None:
        """Called once on project load.  Loads and caches all C tables."""
        self._project_root = root
        self._data = GraphicsDataCache(root)
        try:
            self._data.load()
        except Exception:
            pass
        # Pre-load the three shared icon palettes
        self._icon_palettes.clear()
        for i in range(3):
            path = icon_palette_pal_path(root, i)
            cols = read_jasc_pal(path)
            if not cols:
                cols = [(0, 0, 0)] * 16
            self._icon_palettes[i] = cols
        self._icon_pal_dirty.clear()
        self._palettes.clear()
        self._pal_folder.clear()
        self._normal_pal_dirty.clear()
        self._shiny_pal_dirty.clear()
        # Drop any pending imported graphics from a previously-open project.
        self._sprite_imgs.clear()
        self._sprite_png_dirty.clear()
        self._icon_png_dirty.clear()
        self._footprint_png_dirty.clear()
        self._icon_import_src.clear()
        # Refresh the icon rows with loaded palettes
        self._loading = True
        try:
            for i, row in enumerate(self._icon_rows):
                row.set_colors(self._icon_palettes.get(i, [(0, 0, 0)] * 16))
        finally:
            self._loading = False

    def load_species(self, species: str,
                     front_path: str = "", back_path: str = "",
                     icon_path: str = "", footprint_path: str = "",
                     frame: int = 0) -> None:
        """Load all data + sprites for the given SPECIES_ constant.

        *frame* selects which stacked frame of the front/back sheet to show — a
        form passes its index (base species = 0)."""
        self._current_species = species
        self._form_frame = int(frame or 0)
        self._loading = True
        try:
            # Thumbnails (front/back = this form's frame, footprint full)
            self._set_thumb(self._front_thumb[1], front_path, frame=self._form_frame)
            self._set_thumb(self._back_thumb[1], back_path, frame=self._form_frame)
            self._set_thumb(self._foot_thumb[1], footprint_path)

            # Icon source (animated separately)
            self._icon_src_path = icon_path or ""
            self._foot_src_path = footprint_path or ""
            self._icon_src = self._load_pix(icon_path)
            self._icon_frame = 0
            # Build the palette-swapped version for the currently-selected
            # icon palette, then paint the first frame into the preview.
            self._rebuild_icon_recolour()
            self._render_icon_frame()

            # Battle preview sprites — store sources, then push to preview
            self._front_src_path = front_path or ""
            self._back_src_path = back_path or ""
            self._front_src = self._load_pix(front_path)
            self._back_src = self._load_pix(back_path)
            self._preview.set_front_pixmap(self._front_src)
            self._preview.set_back_pixmap(self._back_src)
            # Record the PNG paths so a later "Index as Background"
            # right-click knows where to read/write pixel data.
            self._sprite_paths[species] = {
                "front": front_path or "",
                "back": back_path or "",
                "icon": icon_path or "",
                "footprint": footprint_path or "",
            }

            # Spinboxes from data cache
            if self._data:
                back_y = self._data.get_back_y(species)
                front_y = self._data.get_front_y(species)
                elev = self._data.get_elevation(species)
                icon_idx = self._data.get_icon_idx(species)
            else:
                back_y = front_y = elev = 0
                icon_idx = 0
            self._player_y.setValue(back_y)
            self._enemy_y.setValue(front_y)
            self._enemy_alt.setValue(elev)
            self._icon_pal_combo.setCurrentIndex(icon_idx)

            self._preview.set_back_y_offset(back_y)
            self._preview.set_front_y_offset(front_y)
            self._preview.set_enemy_elevation(elev)

            # Normal/Shiny palette for this species
            self._load_species_palettes(species)

            # Now reskin the front/back thumbs + battle preview using the
            # freshly-loaded palette (normal or shiny per toggle).  Without
            # this, the thumbnails and preview show the baked PNG palette
            # which may differ from the authoritative .pal file.
            normal_pal = (self._palettes.get(species) or {}).get("normal")
            if normal_pal:
                self._set_thumb(self._front_thumb[1], front_path, normal_pal)
                self._set_thumb(self._back_thumb[1], back_path, normal_pal)
            self._refresh_preview_sprites()
        finally:
            self._loading = False

    def _tick_icon_anim(self) -> None:
        if not self._icon_src or self._icon_src.isNull():
            return
        self._icon_frame = 1 - self._icon_frame
        self._render_icon_frame()

    def _render_icon_frame(self) -> None:
        """Paint the current icon frame (palette-swapped if possible) into
        the Menu Icon Palette preview label."""
        src = self._icon_recoloured or self._icon_src
        if not src or src.isNull():
            self._icon_preview.clear()
            return
        # Icon sheets are 32x64 (two 32x32 frames stacked).
        frame = src.copy(0, self._icon_frame * 32, 32, 32)
        self._icon_preview.setPixmap(
            frame.scaled(
                self._icon_preview.width(),
                self._icon_preview.height(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
        )

    def _rebuild_icon_recolour(self) -> None:
        """Apply the currently-selected shared icon palette to the icon
        source PNG and cache the result."""
        self._icon_recoloured = None
        if not self._icon_src_path:
            return
        idx = self._icon_pal_combo.currentIndex()
        palette = self._icon_palettes.get(idx)
        if not palette:
            return
        self._icon_recoloured = _reskin_indexed_png(
            self._icon_src_path, palette
        )

    def _on_shiny_preview_toggled(self, checked: bool) -> None:
        self._preview_shiny = checked
        self._refresh_preview_sprites()

    def _refresh_preview_sprites(self) -> None:
        """Apply normal or shiny palette to the stored source sprites
        and push them into the BattleScenePreview widget AND the left-
        column front/back thumbnails.

        When an in-memory sprite image exists (populated by an Index-as-
        Background right-click), the preview uses its remapped pixel data
        instead of re-reading the PNG from disk — so the change is live
        before the user Saves.
        """
        from core.sprite_render import mon_sheet_frame
        palette: Optional[List[Color]] = None
        sp = self._current_species
        if sp and sp in self._palettes:
            key = "shiny" if self._preview_shiny else "normal"
            palette = self._palettes[sp].get(key)
        _frame = getattr(self, "_form_frame", 0)
        if not palette:
            # Show this species/form's frame — a front/back sheet may stack more
            # than one 64x64 frame (e.g. Deoxys = 64x128 = normal + form). A form
            # selects its frame index; base species use frame 0.
            self._preview.set_front_pixmap(mon_sheet_frame(self._front_src, _frame))
            self._preview.set_back_pixmap(mon_sheet_frame(self._back_src, _frame))
            return

        cached = self._sprite_imgs.get(sp or "", {})

        def _recolour(kind: str, path: str,
                      fallback: Optional[QPixmap]) -> Optional[QPixmap]:
            # Prefer the cached in-memory QImage (post-remap) if we have
            # one; otherwise fall back to reading the disk PNG.  Either
            # way slot 0 gets alpha=0 by the reskin helper so transparency
            # punches through.
            img = cached.get(kind)
            if img is not None and not img.isNull():
                return _reskin_indexed_image(img, palette) or fallback
            if path:
                return _reskin_indexed_png(path, palette) or fallback
            return fallback

        front_pix = _recolour("front", self._front_src_path, self._front_src)
        back_pix = _recolour("back", self._back_src_path, self._back_src)

        self._preview.set_front_pixmap(mon_sheet_frame(front_pix, _frame))
        self._preview.set_back_pixmap(mon_sheet_frame(back_pix, _frame))

        # Keep the left-column thumbnails in sync using the SAME recoloured
        # pixmaps as the battle preview (which prefer the in-memory imported
        # image over the stale on-disk PNG). Re-reading the disk PNG here was
        # the bug: after importing a new sprite or editing the palette, the
        # thumbnail showed the old on-disk graphic/colours.
        def _fill_thumb(lbl, pix):
            if pix is None or pix.isNull():
                return
            framed = mon_sheet_frame(pix, _frame)
            t = lbl.width()
            lbl.setPixmap(framed.scaled(
                t, t, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation))
        _fill_thumb(self._front_thumb[1], front_pix)
        _fill_thumb(self._back_thumb[1], back_pix)

    def _pal_paths_for(self, species: str) -> tuple[str, str]:
        """(normal.pal, shiny.pal) for a LOADED species.

        The palette always lives next to the sprite, so derive it from the
        loaded front-sprite folder. For a FORM (which is keyed under its base
        const but has its own graphics folder) this correctly returns the
        FORM's folder — NOT the base's. Only when no sprite path is known do we
        fall back to the base-const-derived path.
        """
        front = (self._sprite_paths.get(species) or {}).get("front", "")
        if front:
            d = os.path.dirname(front)
            return (os.path.join(d, "normal.pal"),
                    os.path.join(d, "shiny.pal"))
        return species_pal_paths(self._project_root, species)

    def _load_species_palettes(self, species: str) -> None:
        if not self._project_root:
            return
        npath, spath = self._pal_paths_for(species)
        folder = os.path.dirname(npath)
        # Reload when the palette folder changes (switching between a base and
        # one of its forms — both keyed by the base const but in different
        # folders) so a form shows ITS OWN palette, not the base's cached one.
        if (species not in self._palettes
                or self._pal_folder.get(species) != folder):
            self._palettes[species] = {
                "normal": read_jasc_pal(npath) or [(0, 0, 0)] * 16,
                "shiny": read_jasc_pal(spath) or [(0, 0, 0)] * 16,
            }
            self._pal_folder[species] = folder
        pal = self._palettes[species]
        self._normal_row.set_colors(pal["normal"])
        self._shiny_row.set_colors(pal["shiny"])

    def _set_thumb(self, lbl: QLabel, path: str,
                   palette: Optional[List[Color]] = None, frame: int = 0) -> None:
        if not path or not os.path.exists(path):
            lbl.clear()
            return
        if palette:
            pix = _reskin_indexed_png(path, palette)
            if pix is None:
                pix = QPixmap(path)
        else:
            pix = QPixmap(path)
        if pix.isNull():
            lbl.clear()
            return
        from core.sprite_render import mon_sheet_frame
        pix = mon_sheet_frame(pix, frame)   # slice to the requested frame
        # Scale to fit
        target = lbl.width()
        scaled = pix.scaled(
            target, target,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        lbl.setPixmap(scaled)

    def _load_pix(self, path: str,
                  palette: Optional[List[Color]] = None) -> Optional[QPixmap]:
        if not path or not os.path.exists(path):
            return None
        if palette:
            pix = _reskin_indexed_png(path, palette)
            if pix is None:
                pix = QPixmap(path)
        else:
            pix = QPixmap(path)
        if pix.isNull():
            return None
        return pix

    # ────────────────────────────────────────────────────────── handlers ──
    def _mark_modified(self) -> None:
        if not self._loading:
            self.modified.emit()

    def _on_player_y(self, v: int) -> None:
        self._preview.set_back_y_offset(v)
        if self._current_species and self._data and not self._loading:
            self._data.set_back_y(self._current_species, v)
            self._mark_modified()

    def _on_enemy_y(self, v: int) -> None:
        self._preview.set_front_y_offset(v)
        if self._current_species and self._data and not self._loading:
            self._data.set_front_y(self._current_species, v)
            self._mark_modified()

    def _on_enemy_alt(self, v: int) -> None:
        self._preview.set_enemy_elevation(v)
        if self._current_species and self._data and not self._loading:
            self._data.set_elevation(self._current_species, v)
            self._mark_modified()

    def _on_normal_changed(self) -> None:
        if self._loading or not self._current_species:
            return
        colors = self._normal_row.colors()
        self._palettes.setdefault(
            self._current_species, {"normal": [], "shiny": []}
        )["normal"] = colors
        self._normal_pal_dirty.add(self._current_species)
        # Broadcast the new palette so viewer tabs (Pokedex, Starters,
        # Info panel, species tree, …) reskin immediately — even though
        # no .pal file has been written yet.
        _get_palette_bus().set_pokemon_palette(
            self._current_species, "normal", colors,
        )
        self._mark_modified()
        # Live recolour preview if currently showing normal
        if not self._preview_shiny:
            self._refresh_preview_sprites()

    def _on_shiny_changed(self) -> None:
        if self._loading or not self._current_species:
            return
        colors = self._shiny_row.colors()
        self._palettes.setdefault(
            self._current_species, {"normal": [], "shiny": []}
        )["shiny"] = colors
        self._shiny_pal_dirty.add(self._current_species)
        _get_palette_bus().set_pokemon_palette(
            self._current_species, "shiny", colors,
        )
        self._mark_modified()
        # Live recolour preview if currently showing shiny
        if self._preview_shiny:
            self._refresh_preview_sprites()

    def _on_palette_reordered(self, source: str, from_idx: int, to_idx: int) -> None:
        """User dragged a swatch in the normal OR shiny row.

        Palette-only SWAP — slot ``from_idx`` and slot ``to_idx`` trade
        places in the .pal file for the dragged row.  The other slots
        are untouched (no insert-shift).  The PNG's pixel indices are
        never modified.

        Normal and shiny palettes are independent; each row only rewrites
        its own .pal file.  Slot 0 is the transparent slot by convention
        (encoded via tRNS in the PNG), so whichever colour ends up at
        slot 0 after the swap becomes the transparent colour on next
        Save.

        Pixels keep their original index values, so swapping a palette
        will visibly change which colour shows on which pixels — this
        matches NSE2 behaviour.  Shiny is unaffected by a Normal drag
        (different .pal, same pixels).
        """
        if self._loading or not self._current_species or not self._project_root:
            return
        sp = self._current_species
        n = 16
        if from_idx == to_idx or not (0 <= from_idx < n) or not (0 <= to_idx < n):
            return

        # Make sure we have palettes loaded
        if sp not in self._palettes:
            self._load_species_palettes(sp)

        key = "normal" if source == "normal" else "shiny"
        pal = list(self._palettes[sp].get(key) or [(0, 0, 0)] * n)
        while len(pal) < n:
            pal.append((0, 0, 0))

        # Pure two-position swap — no shifting of the other slots.
        pal[from_idx], pal[to_idx] = pal[to_idx], pal[from_idx]
        self._palettes[sp][key] = pal

        self._loading = True
        try:
            if source == "normal":
                self._normal_row.set_colors(pal)
            else:
                self._shiny_row.set_colors(pal)
        finally:
            self._loading = False

        # A normal-row reorder must re-bake the front/back PNGs so their colour
        # table matches the new normal.pal order; a shiny-row reorder only
        # rewrites shiny.pal (pixels + normal PNGs untouched).
        if source == "normal":
            self._normal_pal_dirty.add(sp)
        else:
            self._shiny_pal_dirty.add(sp)
        # Push the swapped row to the bus — viewers filter on the kind
        # they're showing.
        _get_palette_bus().set_pokemon_palette(sp, key, pal)
        self._refresh_preview_sprites()
        self._mark_modified()

    # ─────────────────────────────────── Index as Background (right-click) ──
    def _ensure_sprite_images_loaded(self, species: str) -> None:
        """Lazy-load front.png and back.png as indexed QImage in memory.

        Only called from the right-click Index-as-Background path, since
        that is the only operation on this tab that needs pixel-level
        access.  Plain colour edits and drag-reorder go through
        ``_reskin_indexed_png`` which reads directly from disk.
        """
        if species in self._sprite_imgs:
            return
        paths = self._sprite_paths.get(species) or {}
        imgs: Dict[str, QImage] = {}
        for key in ("front", "back"):
            p = paths.get(key, "")
            if not p or not os.path.exists(p):
                continue
            img = QImage(p)
            if img.isNull():
                continue
            if img.format() != QImage.Format.Format_Indexed8:
                img = img.convertToFormat(QImage.Format.Format_Indexed8)
            imgs[key] = img
        self._sprite_imgs[species] = imgs

    def _on_set_swatch_as_bg(self, slot: int) -> None:
        """User right-clicked a swatch → "Index as Background".

        Lockstep pixel+palette swap.  Pixels stored as value ``slot`` in
        the shared front/back PNGs become value ``0`` (transparent on
        render); original value-0 pixels become value ``slot``.  Then
        normal.pal[0] ↔ normal.pal[slot] AND shiny.pal[0] ↔ shiny.pal[slot]
        swap so both rendered views remain visually identical to before
        the remap — except that the clicked colour region is now
        transparent.

        THIS IS THE ONLY PATH ON THIS TAB THAT TOUCHES PNG PIXEL DATA.
        Drag-reorder stays palette-only; colour edits stay palette-only.
        """
        if self._loading or not self._current_species or not self._project_root:
            return
        if slot <= 0:
            return
        n = 16
        if not (0 < slot < n):
            return

        sp = self._current_species

        # Ensure in-memory images and palettes are available.
        self._ensure_sprite_images_loaded(sp)
        if sp not in self._palettes:
            self._load_species_palettes(sp)
        imgs = self._sprite_imgs.get(sp, {})
        if not imgs:
            # No PNG on disk to remap — degrade to palette-only swap so the
            # UI still responds.  This is the fallback branch that should
            # essentially never run for real pokefirered species (they
            # always have at least front.png).
            QMessageBox.information(
                self, "No Sprite PNG",
                "This species has no front.png / back.png on disk to remap."
                "  The palette-only swap has still been applied — if you "
                "later add the PNGs, you may need to re-run Index as "
                "Background.",
            )

        # Remap pixel values 0 ↔ slot in every loaded sprite image.
        # swap_palette_entries returns a new image; we don't need the
        # palette-swap half of its output here because we rebuild both
        # pals ourselves below.  Pass the image's current palette as a
        # placeholder (shape is the only thing that matters to the
        # pixel remap).
        pal = self._palettes[sp]
        normal = list(pal.get("normal") or [(0, 0, 0)] * n)
        shiny = list(pal.get("shiny") or [(0, 0, 0)] * n)
        while len(normal) < n:
            normal.append((0, 0, 0))
        while len(shiny) < n:
            shiny.append((0, 0, 0))

        for key, img in list(imgs.items()):
            try:
                new_img, _ = swap_palette_entries(img, normal, slot, 0)
                imgs[key] = new_img
            except Exception as e:
                import traceback
                QMessageBox.warning(
                    self, "Index as Background Error",
                    f"Failed to remap {key}.png:\n{e}\n\n"
                    f"{traceback.format_exc()}",
                )
                return
        self._sprite_imgs[sp] = imgs

        # Lockstep .pal swap — both rows, so neither rendered view shifts.
        normal[0], normal[slot] = normal[slot], normal[0]
        shiny[0], shiny[slot] = shiny[slot], shiny[0]
        self._palettes[sp]["normal"] = normal
        self._palettes[sp]["shiny"] = shiny

        # Push new colours into the swatch rows.
        self._loading = True
        try:
            self._normal_row.set_colors(normal)
            self._shiny_row.set_colors(shiny)
        finally:
            self._loading = False

        # Mark everything dirty: both .pal files + both PNGs need saving.
        self._normal_pal_dirty.add(sp)
        self._shiny_pal_dirty.add(sp)
        self._sprite_png_dirty.add(sp)
        # Push both rows — the lockstep swap rewrote both .pal rows
        # AND the PNG pixels in RAM. Viewer tabs have no access to the
        # in-memory PNGs so they will still render off disk until Save,
        # but at least the palette they render WITH matches this tab.
        bus = _get_palette_bus()
        bus.set_pokemon_palette(sp, "normal", normal)
        bus.set_pokemon_palette(sp, "shiny", shiny)
        self._refresh_preview_sprites()
        self._mark_modified()

    def _on_icon_idx(self, v: int) -> None:
        # Always rebuild the preview (even during loading, since load_species
        # may set the combo after building the recolour). A species with a
        # pending imported icon re-fits that artwork to the newly-chosen shared
        # palette instead of reading the (stale) on-disk icon.
        if self._current_species in self._icon_import_src:
            self._refresh_imported_icon_preview(self._current_species)
        else:
            self._rebuild_icon_recolour()
            self._render_icon_frame()
        if self._loading or not self._current_species or not self._data:
            return
        self._data.set_icon_idx(self._current_species, v)
        self._mark_modified()

    def _on_icon_pal_changed(self, idx: int) -> None:
        if self._loading:
            return
        colors = self._icon_rows[idx].colors()
        self._icon_palettes[idx] = colors
        self._icon_pal_dirty.add(idx)
        # Broadcast so every species-tree row and every animated-icon
        # client (Info tab, dex card) reskins off the new shared palette.
        _get_palette_bus().set_icon_palette(idx, colors)
        self._mark_modified()
        # If the user edited the palette this species is actually using,
        # re-render the preview live.
        if idx == self._icon_pal_combo.currentIndex():
            self._rebuild_icon_recolour()
            self._render_icon_frame()

    # ─────────────────────────────────────────── shared import folder ──
    def _import_start_dir(self) -> str:
        """Where an import dialog should open: the last folder the user
        imported from (persists across species + normal/shiny), else this
        species' own graphics folder, else the project root."""
        if self._last_import_dir and os.path.isdir(self._last_import_dir):
            return self._last_import_dir
        if self._project_root and self._current_species:
            slug = species_slug_from_const(self._current_species)
            cand = os.path.join(
                self._project_root, "graphics", "pokemon", slug)
            if os.path.isdir(cand):
                return cand
        return self._project_root or ""

    def _pick_import_file(self, title: str, filt: str) -> str:
        """Open a file dialog seeded at the remembered import folder and,
        on a successful pick, remember that file's folder for next time."""
        path, _ = QFileDialog.getOpenFileName(
            self, title, self._import_start_dir(), filt)
        if path:
            self._last_import_dir = os.path.dirname(path)
            try:
                from PyQt6.QtCore import QSettings as _QS
                _QS("PorySuite", "PorySuiteZ").setValue(
                    "pokemon_graphics/last_import_dir", self._last_import_dir)
            except Exception:
                pass
        return path

    def _open_graphics_folder(self) -> None:
        if not self._project_root or not self._current_species:
            return
        slug = species_slug_from_const(self._current_species)
        folder = os.path.join(
            self._project_root, "graphics", "pokemon", slug
        )
        if os.path.isdir(folder):
            try:
                from ui.open_folder_util import open_folder
                open_folder(folder)
            except Exception:
                try:
                    os.startfile(folder)  # type: ignore[attr-defined]
                except Exception:
                    pass

    # ─────────────────────────────── import a full normal+shiny set ──
    def _import_sprite_set(self) -> None:
        """Import an artist's normal + shiny PNGs and reconcile them into
        pokefirered's shared-sprite format: the normal pixels become the shared
        front/back sprite, and the shiny palette is auto-derived from the shiny
        PNG pixel-for-pixel so both render exactly as drawn."""
        if not self._current_species:
            QMessageBox.information(
                self, "No Species Selected",
                "Select a species first, then import its sprite set.")
            return
        sp = self._current_species
        from ui.dialogs.sprite_set_import_dialog import pick_sprite_set
        picks = pick_sprite_set(self._import_start_dir(), parent=self)
        if not picks:
            return
        # Remember the folder for next time.
        for p in picks.values():
            if p:
                self._last_import_dir = os.path.dirname(p)
                try:
                    from PyQt6.QtCore import QSettings as _QS
                    _QS("PorySuite", "PorySuiteZ").setValue(
                        "pokemon_graphics/last_import_dir", self._last_import_dir)
                except Exception:
                    pass
                break

        def _load(key):
            path = picks.get(key, "")
            if not path or not os.path.isfile(path):
                return None
            im = QImage(path)
            return None if im.isNull() else im

        from core.sprite_set_import import build_sprite_set
        result = build_sprite_set(
            _load("front_normal"), _load("front_shiny"),
            _load("back_normal"), _load("back_shiny"))

        # Stage the shared sprites + both palettes (written together on Save).
        imgs = self._sprite_imgs.setdefault(sp, {})
        if result.get("front") is not None:
            imgs["front"] = result["front"]
        if result.get("back") is not None:
            imgs["back"] = result["back"]
        self._palettes.setdefault(sp, {"normal": [], "shiny": []})
        self._palettes[sp]["normal"] = list(result["normal"])
        self._palettes[sp]["shiny"] = list(result["shiny"])
        self._normal_pal_dirty.add(sp)     # writes normal.pal + bakes PNGs
        self._shiny_pal_dirty.add(sp)      # writes shiny.pal
        self._sprite_png_dirty.add(sp)

        # Menu icon (separate shared-palette path) if provided.
        icon_img = _load("icon")
        if icon_img is not None:
            self._apply_imported_icon(sp, picks.get("icon", "icon"), icon_img)

        # Reflect in the swatch rows + previews.
        self._loading = True
        try:
            self._normal_row.set_colors(self._palettes[sp]["normal"])
            self._shiny_row.set_colors(self._palettes[sp]["shiny"])
        finally:
            self._loading = False
        _frame = getattr(self, "_form_frame", 0)
        for kind, thumb in (("front", self._front_thumb),
                            ("back", self._back_thumb)):
            cimg = imgs.get(kind)
            if cimg is not None and not cimg.isNull():
                self._thumb_from_image(
                    thumb[1], cimg, self._palettes[sp]["normal"], frame=_frame)
        self._refresh_preview_sprites()
        self.modified.emit()

        warn = result.get("warnings") or []
        msg = ("Imported the sprite set. The normal sprite reproduces your "
               "normal PNG, and the shiny palette was auto-mapped so the SAME "
               "sprite reproduces your shiny PNG — both display as drawn.\n\n"
               "Toggle Preview Shiny to check, then File → Save.")
        if warn:
            msg += "\n\nNotes:\n• " + "\n• ".join(warn)
        QMessageBox.information(self, "Sprite Set Imported", msg)

    # ───────────────────────────────────── import a sprite GRAPHIC ──
    def _thumb_from_image(self, lbl: QLabel, img: QImage,
                          palette: Optional[List[Color]] = None,
                          frame: int = 0) -> None:
        """Paint an in-memory (not-yet-saved) QImage into a thumbnail label,
        recolouring with *palette* when given. Mirrors _set_thumb but reads
        from memory so an import shows before Save."""
        if img is None or img.isNull():
            return
        from core.sprite_render import mon_sheet_frame
        if palette:
            pix = _reskin_indexed_image(img, palette)
            if pix is None:
                pix = QPixmap.fromImage(img)
        else:
            pix = QPixmap.fromImage(img)
        if pix.isNull():
            return
        pix = mon_sheet_frame(pix, frame)
        target = lbl.width()
        lbl.setPixmap(pix.scaled(
            target, target,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation))

    def _import_graphic(self, slot: str) -> None:
        """Replace one of this species' graphics (front / back / icon /
        footprint) with a user-picked PNG. Nothing is written to disk until
        the user clicks File → Save — the imported image lives in memory and
        flows through flush_to_disk, so it's undoable via a no-save reload."""
        if not self._current_species:
            QMessageBox.information(
                self, "No Species Selected",
                "Select a species first, then import a graphic.")
            return
        sp = self._current_species
        LABELS = {"front": "front sprite", "back": "back sprite",
                  "icon": "menu icon", "footprint": "footprint"}
        # GBA hardware frame sizes (front/back & icon may be vertical multi-
        # frame sheets — height a multiple of the per-frame height).
        FRAME = {"front": (64, 64), "back": (64, 64),
                 "icon": (32, 32), "footprint": (16, 16)}
        label = LABELS[slot]
        ew, eh = FRAME[slot]

        path = self._pick_import_file(
            f"Select {label} PNG", "PNG Images (*.png)")
        if not path:
            return
        img = QImage(path)
        if img.isNull():
            QMessageBox.warning(
                self, "Import Failed", f"Could not load image:\n{path}")
            return

        # Size sanity check — wrong dimensions can break the build, so warn
        # (but let the user override for genuinely custom setups).
        w, h = img.width(), img.height()
        multiframe = slot in ("front", "back", "icon")
        bad = (w != ew) or (h == 0) or (h % eh != 0) or (not multiframe and h != eh)
        if bad:
            per = " per frame (stacked vertically)" if multiframe else ""
            if QMessageBox.question(
                    self, "Unexpected Size",
                    f"The {label} is normally {ew}×{eh}px{per}.\n"
                    f"You picked a {w}×{h}px image.\n\n"
                    "Importing a wrong-sized graphic can break the build. "
                    "Import it anyway?",
                    QMessageBox.StandardButton.Yes
                    | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No
            ) != QMessageBox.StandardButton.Yes:
                return

        if slot == "footprint":
            self._apply_imported_footprint(sp, img)
        elif slot == "icon":
            self._apply_imported_icon(sp, path, img)
        else:
            self._apply_imported_front_back(sp, slot, path, img)

    def _apply_imported_footprint(self, sp: str, img: QImage) -> None:
        """Convert any picked image to a 2-colour footprint (white bg = index
        0, black mark = index 1) and stage it for save."""
        src = img.convertToFormat(QImage.Format.Format_ARGB32)
        w, h = src.width(), src.height()
        idx = QImage(w, h, QImage.Format.Format_Indexed8)
        idx.setColorCount(2)
        idx.setColor(0, 0xFFFFFFFF)   # white background  (matches on-disk)
        idx.setColor(1, 0xFF000000)   # black footprint mark
        for y in range(h):
            for x in range(w):
                px = src.pixel(x, y)
                a = (px >> 24) & 0xFF
                r = (px >> 16) & 0xFF
                g = (px >> 8) & 0xFF
                b = px & 0xFF
                lum = 0.299 * r + 0.587 * g + 0.114 * b
                # transparent or light -> background; dark opaque -> the mark
                idx.setPixel(x, y, 1 if (a >= 128 and lum < 128) else 0)
        self._sprite_imgs.setdefault(sp, {})["footprint"] = idx
        self._footprint_png_dirty.add(sp)
        self._thumb_from_image(self._foot_thumb[1], idx)
        self.modified.emit()
        QMessageBox.information(
            self, "Footprint Imported",
            "The footprint preview is updated. Click File → Save to write it "
            "to the species' footprint.png.")

    def _extract_or_pick_palette(self, path: str, img: QImage):
        """Return (colors, indexed_img) for a picked PNG: use an already
        project-format indexed ≤16-colour image directly, else open the manual
        colour picker to choose/order 16 colours and remap. Returns None if
        the user cancels the picker."""
        ct = (img.colorTable()
              if img.format() == QImage.Format.Format_Indexed8 else [])
        if (img.format() == QImage.Format.Format_Indexed8
                and ct and len(set(ct)) <= 16):
            colors: List[Color] = []
            for entry in ct[:16]:
                colors.append(clamp_to_gba(
                    (entry >> 16) & 0xFF, (entry >> 8) & 0xFF, entry & 0xFF))
            while len(colors) < 16:
                colors.append((0, 0, 0))
            idx_img = img
        else:
            from ui.dialogs.manual_palette_pick_dialog import (
                import_image_manually_from_path,
            )
            result = import_image_manually_from_path(
                path, target_colors=16, parent=self)
            if result is None:
                return None
            colors, idx_img = result
            colors = list(colors)
        if idx_img.format() != QImage.Format.Format_Indexed8:
            idx_img = idx_img.convertToFormat(QImage.Format.Format_Indexed8)
        return colors, idx_img

    @staticmethod
    def _distinct_used_colors(true_img: Optional[QImage]) -> List[Color]:
        """Ordered list of distinct opaque colours used by an ARGB/indexed
        image (skips fully-transparent pixels, i.e. the background)."""
        out: List[Color] = []
        if true_img is None or true_img.isNull():
            return out
        seen = set()
        w, h = true_img.width(), true_img.height()
        for y in range(h):
            for x in range(w):
                px = true_img.pixel(x, y)
                if ((px >> 24) & 0xFF) < 128:
                    continue
                c = ((px >> 16) & 0xFF, (px >> 8) & 0xFF, px & 0xFF)
                if c not in seen:
                    seen.add(c)
                    out.append(c)
        return out

    def _apply_imported_front_back(self, sp: str, slot: str,
                                   path: str, img: QImage) -> None:
        """Import a front or back sprite.

        Front and back share ONE normal palette, so a shared 16-colour palette
        is built that covers BOTH sprites (their combined colours — lossless
        when front+back were authored to fit 16 colours, which is the norm),
        and both sprites are re-indexed onto it. Importing the back therefore
        keeps the front looking right instead of stealing its colours.

        The SHINY palette is deliberately left ALONE — importing a sprite must
        not touch shiny (only the normal channel is marked dirty). Set the shiny
        colours separately with the shiny palette import, after the sprites are
        in place."""
        picked = self._extract_or_pick_palette(path, img)
        if picked is None:
            return
        imported_colors, idx_img = picked
        other = "back" if slot == "front" else "front"

        cur = self._palettes.get(sp) or {}
        old_normal = list(cur.get("normal") or [(0, 0, 0)] * 16)
        while len(old_normal) < 16:
            old_normal.append((0, 0, 0))

        # True-colour views of both sprites (their own colours), so we can
        # union their palettes and remap without depending on stale index order.
        # The OTHER sprite is read via _true_color_sprite, which uses the disk
        # PNG's OWN baked palette when reading from disk — NOT the loaded
        # normal.pal. That matters when the on-disk front/back are desynced from
        # normal.pal (front baked one palette, normal.pal holding another): the
        # old code recoloured the other sprite with normal.pal and pulled the
        # WRONG colours into the shared palette (the "where did these colours
        # come from" bug).
        imported_true = _rebuild_color_table(idx_img, imported_colors, 0)
        other_true = self._true_color_sprite(sp, other)

        # Build the shared palette: slot 0 = the imported sprite's transparent
        # colour, then the imported sprite's colours (it wins on overflow since
        # it's the one being imported), then the other sprite's colours.
        bg = imported_colors[0] if imported_colors else (0, 0, 0)
        shared: List[Color] = [bg]
        seen = {bg}
        for c in (self._distinct_used_colors(imported_true)
                  + self._distinct_used_colors(other_true)):
            if c in seen:
                continue
            if len(shared) >= 16:
                break
            seen.add(c)
            shared.append(c)
        while len(shared) < 16:
            shared.append((0, 0, 0))

        # Re-index BOTH sprites onto the shared palette (bg -> slot 0).
        self._sprite_imgs.setdefault(sp, {})[slot] = remap_to_palette(
            imported_true, shared, bg_transparent=True)
        if other_true is not None:
            self._sprite_imgs[sp][other] = remap_to_palette(
                other_true, shared, bg_transparent=True)

        # Normal channel only — shiny is preserved untouched.
        self._palettes.setdefault(sp, {"normal": [], "shiny": []})["normal"] = shared
        self._normal_pal_dirty.add(sp)
        self._sprite_png_dirty.add(sp)

        self._loading = True
        try:
            self._normal_row.set_colors(shared)
        finally:
            self._loading = False
        _frame = getattr(self, "_form_frame", 0)
        for kind, thumb in (("front", self._front_thumb),
                            ("back", self._back_thumb)):
            cimg = self._sprite_imgs[sp].get(kind)
            if cimg is not None and not cimg.isNull():
                self._thumb_from_image(thumb[1], cimg, shared, frame=_frame)
        self._refresh_preview_sprites()

        self.modified.emit()
        QMessageBox.information(
            self, "Sprite Imported",
            f"Loaded {slot} from:\n{os.path.basename(path)}\n\n"
            f"Front and back share one normal palette, so a combined palette was "
            f"built and the {other} sprite kept its colours. The shiny palette "
            f"was left untouched — set it with the shiny palette import.\n\n"
            f"Click File → Save to write the PNGs + normal.pal.")

    def _true_color_sprite(self, sp: str, slot: str) -> Optional[QImage]:
        """Return a sprite slot as an image showing its REAL colours.

        - In-memory imported image → it's indexed to this species' current
          normal palette, so apply that palette.
        - On-disk PNG → use the PNG's OWN baked colour table (its true colours),
          NOT the loaded normal.pal, so a front/back that's baked out of sync
          with normal.pal is still read correctly.
        Returns None if the slot has no image."""
        cached = (self._sprite_imgs.get(sp) or {}).get(slot)
        if cached is not None and not cached.isNull():
            pal = (self._palettes.get(sp) or {}).get("normal") or [(0, 0, 0)] * 16
            return _rebuild_color_table(cached, pal, 0)
        path = (self._sprite_paths.get(sp) or {}).get(slot, "")
        if path and os.path.isfile(path):
            disk = QImage(path)
            if not disk.isNull():
                return disk   # its own baked colour table = its true colours
        return None

    def _current_indexed_for(self, sp: str, slot: str,
                             fallback_pal: List[Color]):
        """Return the current in-memory Indexed8 QImage for a sprite slot,
        loading it from disk (as Indexed8) when it hasn't been imported yet."""
        cached = (self._sprite_imgs.get(sp) or {}).get(slot)
        if cached is not None and not cached.isNull():
            return cached
        path = (self._sprite_paths.get(sp) or {}).get(slot, "")
        if path and os.path.isfile(path):
            disk = QImage(path)
            if not disk.isNull():
                if disk.format() != QImage.Format.Format_Indexed8:
                    disk = disk.convertToFormat(QImage.Format.Format_Indexed8)
                return disk
        return None

    def _icon_target_slot(self, sp: str) -> int:
        """Which of the three shared icon palettes this species uses. Respects
        a live combo edit for the currently-selected species, else the stored
        gMonIconPaletteIndices value."""
        if sp == self._current_species:
            return self._icon_pal_combo.currentIndex()
        if self._data:
            try:
                return int(self._data.get_icon_idx(sp))
            except Exception:
                pass
        return 0

    def _remapped_imported_icon(self, sp: str):
        """Fit a pending imported icon's raw artwork onto this species' shared
        icon palette. Returns (indexed_img, palette) or (None, None)."""
        src = self._icon_import_src.get(sp)
        if src is None or src.isNull():
            return None, None
        pal = list(self._icon_palettes.get(self._icon_target_slot(sp))
                   or [(0, 0, 0)] * 16)
        rem = remap_to_palette(src, pal, bg_transparent=True)
        if rem.format() != QImage.Format.Format_Indexed8:
            rem = rem.convertToFormat(QImage.Format.Format_Indexed8)
        return rem, pal

    def _refresh_imported_icon_preview(self, sp: str) -> None:
        rem, pal = self._remapped_imported_icon(sp)
        if rem is None:
            return
        self._icon_src = QPixmap.fromImage(_rebuild_color_table(rem, pal, 0))
        self._icon_recoloured = None
        self._icon_frame = 0
        self._render_icon_frame()

    def _apply_imported_icon(self, sp: str, path: str, img: QImage) -> None:
        """Import a menu icon (mini). Icons don't own their palette — every
        species points at ONE of three SHARED icon palettes
        (gMonIconPaletteIndices[species]). The raw artwork is kept and fitted
        onto whichever shared palette this species uses (the 0/1/2 combo),
        never overwriting that shared palette (other species use it too). The
        fit re-runs live if the user picks a different palette slot, and again
        at save — so the on-disk icon.png always matches the slot in effect."""
        self._icon_import_src[sp] = img.copy()
        self._icon_png_dirty.add(sp)
        self._refresh_imported_icon_preview(sp)

        self.modified.emit()
        slot = self._icon_target_slot(sp)
        QMessageBox.information(
            self, "Menu Icon Imported",
            f"Loaded icon from:\n{os.path.basename(path)}\n\n"
            f"The icon was fitted to shared icon palette #{slot} (the one this "
            f"species uses). If the colours look off, pick a different palette "
            f"number above (it re-fits instantly) or edit that palette's "
            f"swatches.\n\nClick File → Save to write the icon.png.")

    # ────────────────────────────── palette import from indexed PNG ──
    def _import_palette_from_png(self) -> None:
        """Auto-extract palette from an indexed PNG and load it as
        Normal or Shiny."""
        self._do_import_palette_from_png(manual=False)

    def _import_palette_from_png_manual(self) -> None:
        """Open the manual palette picker on ANY PNG (indexed or not)
        and load the chosen palette as Normal or Shiny."""
        self._do_import_palette_from_png(manual=True)

    @staticmethod
    def _shiny_map_from(spr_true: QImage, shiny_argb: QImage):
        """Build a normal-colour → shiny-colour map by pixel correspondence
        between a NORMAL sprite (its true colours) and a same-size shiny PNG.
        Returns (map, consistency) where consistency ∈ [0,1] is how cleanly
        each normal colour maps to a single shiny colour (1.0 = perfect, which
        is what you get when the shiny PNG is the same drawing recoloured)."""
        m: Dict[Color, Dict[Color, int]] = {}
        w, h = spr_true.width(), spr_true.height()
        for y in range(h):
            for x in range(w):
                npx = spr_true.pixel(x, y)
                if ((npx >> 24) & 0xFF) < 128:
                    continue   # transparent → background, skip
                nc = ((npx >> 16) & 0xFF, (npx >> 8) & 0xFF, npx & 0xFF)
                spx = shiny_argb.pixel(x, y)
                sc = ((spx >> 16) & 0xFF, (spx >> 8) & 0xFF, spx & 0xFF)
                d = m.setdefault(nc, {})
                d[sc] = d.get(sc, 0) + 1
        total = sum(sum(d.values()) for d in m.values())
        dom = sum(max(d.values()) for d in m.values())
        return m, (dom / total if total else 0.0)

    def _try_import_shiny_from_sprite_png(self, path: str) -> bool:
        """Derive shiny.pal from a shiny sprite PNG by matching it against the
        current normal front/back sprite. Returns True if it handled the
        import (shiny-only, no PNG rewrite); False to fall through to plain
        palette extraction."""
        sp = self._current_species
        if not sp:
            return False
        sh = QImage(path)
        if sh.isNull():
            return False
        sh = sh.convertToFormat(QImage.Format.Format_ARGB32)

        if sp not in self._palettes:
            self._load_species_palettes(sp)
        normal = list((self._palettes.get(sp) or {}).get("normal") or [])
        if not normal:
            return False
        while len(normal) < 16:
            normal.append((0, 0, 0))

        # Try both normal sprites; keep whichever gives the cleanest colour
        # correspondence (front-shiny matches front, back-shiny matches back).
        best = None
        for slot in ("front", "back"):
            spr = self._current_indexed_for(sp, slot, normal)
            if spr is None or spr.isNull():
                continue
            if (spr.width(), spr.height()) != (sh.width(), sh.height()):
                continue
            spr_true = _rebuild_color_table(spr, normal, 0)
            m, consistency = self._shiny_map_from(spr_true, sh)
            if best is None or consistency > best[0]:
                best = (consistency, slot, m)
        if best is None:
            return False   # no same-size normal sprite → not a shiny sprite PNG

        consistency, matched_slot, m = best
        # Accumulate onto the current shiny palette (so importing the front-shiny
        # then the back-shiny both contribute their colours). Start from a copy
        # of normal if there's no shiny yet.
        shiny = list((self._palettes.get(sp) or {}).get("shiny") or list(normal))
        while len(shiny) < 16:
            shiny.append((0, 0, 0))
        updated = 0
        for j, nc in enumerate(normal):
            if nc in m and m[nc]:
                shiny[j] = clamp_to_gba(*max(m[nc], key=m[nc].get))
                updated += 1

        self._palettes.setdefault(sp, {"normal": [], "shiny": []})["shiny"] = shiny
        self._shiny_pal_dirty.add(sp)
        _get_palette_bus().set_pokemon_palette(sp, "shiny", shiny)
        self._loading = True
        try:
            self._shiny_row.set_colors(shiny)
        finally:
            self._loading = False
        if self._preview_shiny:
            self._refresh_preview_sprites()
        self._mark_modified()

        QMessageBox.information(
            self, "Shiny Palette Imported",
            f"Derived the shiny palette from:\n{os.path.basename(path)}\n\n"
            f"Matched it against the {matched_slot} sprite and updated "
            f"{updated} shiny colour(s). The normal graphics were NOT touched. "
            f"Import the other view's shiny PNG too to fill any remaining "
            f"colours.\n\nClick File → Save to write shiny.pal.")
        return True

    def _do_import_palette_from_png(self, manual: bool) -> None:
        if not self._current_species:
            QMessageBox.information(
                self, "No Species Selected",
                "Select a species first, then import a palette.",
            )
            return

        # Pick file — seeded at the remembered import folder (see
        # _import_start_dir), falling back to this species' graphics folder.
        path = self._pick_import_file(
            "Select PNG" if manual else "Select Indexed PNG",
            "PNG Images (*.png)")
        if not path:
            return

        # "Import PNG" (auto) + Shiny target + a sprite-sized PNG → derive the
        # shiny palette by matching the shiny artwork against this species'
        # NORMAL front/back sprite pixel-for-pixel. This is the convenient way
        # to import a shiny from a separately-drawn shiny PNG (whose index order
        # won't match the normal sprite); it writes shiny.pal ONLY.
        #
        # "Import PNG Manually" (manual=True) is NEVER auto-routed — the whole
        # point of that button is to open the manual colour-rearrange picker,
        # for Normal AND Shiny alike, exactly like every other tab. It just
        # targets shiny.pal (and never rewrites the normal PNG) when Shiny is
        # selected.
        if not manual and self._import_shiny_rb.isChecked():
            if self._try_import_shiny_from_sprite_png(path):
                return

        remapped_img = None

        # Auto-route: a PNG that ISN'T a project-format indexed image
        # (RGB, or indexed with >16 distinct colours) can't yield a clean
        # 16-colour palette by table extraction — it needs the manual
        # picker.  Detect that here and flip `manual` on, so the user
        # never hits a "not indexed" rejection.  Same single-action
        # import the Overworld editor offers.
        from PyQt6.QtGui import QImage as _QI
        if not manual:
            _peek = QImage(path)
            if _peek.isNull():
                QMessageBox.warning(
                    self, "Import Failed",
                    f"Could not load image:\n{path}",
                )
                return
            _ct = (_peek.colorTable()
                   if _peek.format() == _QI.Format.Format_Indexed8 else [])
            if not (_ct and len(set(_ct)) <= 16):
                manual = True  # non-indexed / too many colours

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
        else:
            # Already a project-format indexed PNG — extract its colour
            # table directly (the auto-route above guarantees we only
            # land here for valid ≤16-colour indexed images).
            img = QImage(path)
            ct = img.colorTable()
            colors: List[Color] = []
            for entry in ct[:16]:
                r = (entry >> 16) & 0xFF
                g = (entry >> 8) & 0xFF
                b = entry & 0xFF
                colors.append(clamp_to_gba(r, g, b))
            # Pad to 16 if the palette has fewer entries
            while len(colors) < 16:
                colors.append((0, 0, 0))

        # Determine target (Normal or Shiny)
        is_shiny = self._import_shiny_rb.isChecked()
        target_label = "Shiny" if is_shiny else "Normal"
        key = "shiny" if is_shiny else "normal"

        # GBA-clamp the colours ONCE, here, so the .pal write and the front/back
        # PNG bake use identical values. Without this the .pal file clamps to
        # 5-bit (e.g. 255 -> 248) while the baked PNG keeps the raw value, and
        # the two drift apart. (The GBA is 15-bit colour, so this rounding is
        # unavoidable — but it must be applied consistently.)
        colors = [clamp_to_gba(*c) for c in colors[:16]]
        while len(colors) < 16:
            colors.append((0, 0, 0))

        # Update in-memory palette cache
        self._palettes.setdefault(
            self._current_species, {"normal": [], "shiny": []}
        )[key] = colors
        if is_shiny:
            self._shiny_pal_dirty.add(self._current_species)
        else:
            self._normal_pal_dirty.add(self._current_species)

        # Update the swatch row display
        self._loading = True
        try:
            if is_shiny:
                self._shiny_row.set_colors(colors)
            else:
                self._normal_row.set_colors(colors)
        finally:
            self._loading = False

        # MANUAL pick = "what I arranged is what gets saved." The picker remaps
        # the imported PNG to the exact palette layout the user built (and shows
        # in its preview), so we save BOTH that remapped sprite AND the palette.
        # They're staged in memory and written TOGETHER on Save (never an
        # immediate PNG write that could get ahead of the .pal), so the layout
        # shown in the picker is exactly what lands in-game.
        #
        # Shiny stays palette-only even in manual mode — shiny shares the normal
        # sprite's pixels, so only shiny.pal changes.
        #
        # The AUTO "Import PNG" and "Import .pal" buttons never reach this block
        # (remapped_img is None for them), so they remain palette-only — they
        # recolour the existing sprite without replacing it.
        staged_slot = ""
        image_size_warning = ""
        if manual and remapped_img is not None and not is_shiny:
            paths = self._sprite_paths.get(self._current_species, {})
            front_path = paths.get("front", "")
            back_path = paths.get("back", "")
            front_dims = _png_dims(front_path)
            back_dims = _png_dims(back_path)
            src_dims = (remapped_img.width(), remapped_img.height())
            slot = "front"
            if front_dims and src_dims == front_dims:
                slot = "front"
            elif back_dims and src_dims == back_dims:
                slot = "back"
            elif front_dims and src_dims != front_dims:
                image_size_warning = (
                    f"\n\nNote: the image is {src_dims[0]}×{src_dims[1]} but the "
                    f"existing front.png is {front_dims[0]}×{front_dims[1]}; "
                    f"front.png takes the new size on Save.")
            idx = remapped_img
            if idx.format() != QImage.Format.Format_Indexed8:
                idx = idx.convertToFormat(QImage.Format.Format_Indexed8)
            self._sprite_imgs.setdefault(
                self._current_species, {})[slot] = idx
            self._sprite_png_dirty.add(self._current_species)
            staged_slot = slot
            if slot == "front":
                self._front_src_path = front_path
            else:
                self._back_src_path = back_path

        # Refresh the battle scene preview so the user sees it immediately
        show_shiny = self._preview_shiny
        if (is_shiny and show_shiny) or (not is_shiny and not show_shiny):
            self._refresh_preview_sprites()

        self._mark_modified()

        n_used_msg = (
            sum(1 for c in colors if c != (0, 0, 0))
            if manual else min(len(ct), 16)
        )
        image_msg = (
            f"\nThe {staged_slot} sprite was updated to match — it and the "
            f".pal are written together on Save."
            if staged_slot else ""
        )
        QMessageBox.information(
            self, "Palette Imported",
            f"Loaded {n_used_msg} colors from:\n"
            f"{os.path.basename(path)}\n\n"
            f"Applied to: {target_label} palette\n"
            f"Species: {self._current_species}"
            f"{image_msg}\n\n"
            "The palette's order was preserved as-is. If the transparent\n"
            "slot is in the wrong position, drag the correct colour onto\n"
            "slot 0 in the palette row.\n\n"
            "Click File → Save to write everything to disk."
            f"{image_size_warning}",
        )

    def _import_palette_from_pal(self) -> None:
        """Load colours from a JASC .pal file into Normal or Shiny."""
        if not self._current_species:
            QMessageBox.information(
                self, "No Species Selected",
                "Select a species first, then import a .pal file.",
            )
            return

        # Seeded at the remembered import folder (shared with every other
        # import dialog on this tab), falling back to the species folder.
        path = self._pick_import_file(
            "Select JASC .pal File",
            "JASC Palette Files (*.pal);;All Files (*)")
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

        # Pad / clamp to 16 entries
        colors = [clamp_to_gba(*c) for c in colors[:16]]
        while len(colors) < 16:
            colors.append((0, 0, 0))

        # Determine target (Normal or Shiny) from the same radio selector
        # used by the PNG importer.
        is_shiny = self._import_shiny_rb.isChecked()
        target_label = "Shiny" if is_shiny else "Normal"
        key = "shiny" if is_shiny else "normal"

        self._palettes.setdefault(
            self._current_species, {"normal": [], "shiny": []}
        )[key] = colors
        if is_shiny:
            self._shiny_pal_dirty.add(self._current_species)
        else:
            self._normal_pal_dirty.add(self._current_species)

        # Update the swatch row display
        self._loading = True
        try:
            if is_shiny:
                self._shiny_row.set_colors(colors)
            else:
                self._normal_row.set_colors(colors)
        finally:
            self._loading = False

        # Refresh the battle scene preview if this side is currently shown
        show_shiny = self._preview_shiny
        if (is_shiny and show_shiny) or (not is_shiny and not show_shiny):
            self._refresh_preview_sprites()

        self._mark_modified()

        QMessageBox.information(
            self, "Palette Imported",
            f"Loaded {len(colors)} colors from:\n"
            f"{os.path.basename(path)}\n\n"
            f"Applied to: {target_label} palette\n"
            f"Species: {self._current_species}\n\n"
            "Click File → Save to write the .pal file to disk.",
        )

    # ────────────────────────────────────────────────────────── save hook ──
    def has_unsaved_changes(self) -> bool:
        return bool(
            (self._data and self._data.has_pending_changes())
            or self._normal_pal_dirty
            or self._shiny_pal_dirty
            or self._icon_pal_dirty
            or self._sprite_png_dirty
            or self._icon_png_dirty
            or self._footprint_png_dirty
        )

    def flush_to_disk(self) -> tuple[int, list[str]]:
        """Write all pending changes. Called by mainwindow save pipeline.

        Drag-reorder and palette imports only rewrite the ``.pal`` files —
        pixel data in the front/back PNGs is never touched on those paths.

        The ONLY path on this tab that rewrites PNG pixel data is the
        right-click "Index as Background" operation, which populates
        ``_sprite_png_dirty``.  Species listed there also have entries in
        ``_normal_pal_dirty`` (both pals were lockstep-swapped), so the .pal
        writes below stay in sync with the pixel remap.
        """
        total_ok = 0
        all_errors: list[str] = []
        # C tables
        if self._data:
            ok, errs = self._data.save_all()
            total_ok += ok
            all_errors.extend(errs)
        # Species palettes — write .pal files AND bake the NORMAL palette
        # into front/back PNGs so opening the .png in GIMP shows current
        # colours instead of stale ones from the original PNG. (Front and
        # back are indexed PNGs sharing the normal palette by convention.)
        if self._project_root:
            # SHINY-only changes: write shiny.pal and NOTHING else. The normal
            # front/back PNGs are never touched — that's the whole point of
            # importing a shiny palette without disturbing the normal graphics.
            for sp in list(self._shiny_pal_dirty):
                if sp in self._normal_pal_dirty:
                    continue  # handled below (writes both pals)
                _, spath = self._pal_paths_for(sp)
                shiny_colors = (self._palettes.get(sp) or {}).get("shiny")
                if shiny_colors and write_jasc_pal(spath, shiny_colors):
                    total_ok += 1
                else:
                    all_errors.append(f"pal-shiny:{sp}")
            self._shiny_pal_dirty.clear()

            # NORMAL palette changes: write normal.pal (+ shiny.pal if it also
            # changed) AND re-bake the front/back PNGs so their baked colour
            # table matches the new normal.pal.
            for sp in list(self._normal_pal_dirty):
                npath, spath = self._pal_paths_for(sp)
                pal = self._palettes.get(sp, {})
                normal_colors = pal.get("normal")
                if normal_colors and write_jasc_pal(npath, normal_colors):
                    total_ok += 1
                else:
                    all_errors.append(f"pal-normal:{sp}")
                if pal.get("shiny") and write_jasc_pal(spath, pal["shiny"]):
                    total_ok += 1
                else:
                    all_errors.append(f"pal-shiny:{sp}")

                # Bake normal palette into front/back PNGs.
                # export_indexed_png refuses non-Indexed8 input (would
                # otherwise produce an RGB PNG that breaks gbagfx during
                # the build), so guarantee Indexed8 by loading from disk
                # and converting if the in-memory copy isn't indexed.
                if not normal_colors:
                    continue
                paths = self._sprite_paths.get(sp, {})
                imgs = self._sprite_imgs.get(sp, {})
                for key in ("front", "back"):
                    path = paths.get(key, "")
                    if not path:
                        continue
                    img = imgs.get(key)
                    if img is None or img.format() != QImage.Format.Format_Indexed8:
                        if not os.path.isfile(path):
                            continue
                        disk_img = QImage(path)
                        if disk_img.isNull():
                            continue
                        if disk_img.format() != QImage.Format.Format_Indexed8:
                            disk_img = disk_img.convertToFormat(
                                QImage.Format.Format_Indexed8)
                        img = disk_img
                    try:
                        if export_indexed_png(img, normal_colors, path,
                                              transparent_index=0):
                            # Disk PNG now in sync — drop any pending
                            # remap-only flag for this species.
                            pass
                        else:
                            all_errors.append(
                                f"sprite-png-bake-{key}:{sp} "
                                f"(refused — not indexed)")
                    except Exception as exc:
                        all_errors.append(
                            f"sprite-png-bake-{key}:{sp} ({exc})")
                # Pixel-remap flag (Index-as-Background) is handled by
                # Pass 2 below — but if we just baked the new color
                # table, the disk PNG matches our in-memory state, so
                # we can drop the flag preemptively.
                self._sprite_png_dirty.discard(sp)
            self._normal_pal_dirty.clear()
            # Icon palettes
            for idx in list(self._icon_pal_dirty):
                path = icon_palette_pal_path(self._project_root, idx)
                if write_jasc_pal(path, self._icon_palettes.get(idx, [])):
                    total_ok += 1
                else:
                    all_errors.append(f"icon-pal:{idx}")
            self._icon_pal_dirty.clear()
            # Remapped sprite PNGs (from right-click Index as Background).
            # Each species' front/back QImage was pixel-remapped in memory
            # at right-click time; write it back now.  We write the PNG
            # with the NORMAL palette baked into the colour table (that
            # matches pokefirered's build convention — front.png is an
            # indexed PNG whose palette ordering lines up with normal.pal).
            for sp in list(self._sprite_png_dirty):
                imgs = self._sprite_imgs.get(sp, {})
                paths = self._sprite_paths.get(sp, {})
                pal = (self._palettes.get(sp, {}).get("normal")
                       or [(0, 0, 0)] * 16)
                for key in ("front", "back"):
                    img = imgs.get(key)
                    path = paths.get(key, "")
                    if img is None or not path:
                        continue
                    if export_indexed_png(img, pal, path, transparent_index=0):
                        total_ok += 1
                    else:
                        all_errors.append(f"sprite-png-{key}:{sp}")
            self._sprite_png_dirty.clear()

            # Imported menu icons — fit the raw artwork onto the shared icon
            # palette this species uses (respecting any slot change), bake that
            # shared palette into icon.png (index 0 transparent) so the on-disk
            # PNG matches what the game will render.
            for sp in list(self._icon_png_dirty):
                path = (self._sprite_paths.get(sp, {}) or {}).get("icon", "")
                rem, pal = self._remapped_imported_icon(sp)
                if rem is None or not path:
                    continue
                if export_indexed_png(rem, pal, path, transparent_index=0):
                    total_ok += 1
                    self._icon_import_src.pop(sp, None)
                else:
                    all_errors.append(f"icon-png:{sp}")
            self._icon_png_dirty.clear()

            # Imported footprints — 2-colour indexed PNG (white bg = index 0,
            # black mark = index 1). No transparent slot (footprints are opaque
            # black/white and build to 1bpp).
            for sp in list(self._footprint_png_dirty):
                img = (self._sprite_imgs.get(sp, {}) or {}).get("footprint")
                path = (self._sprite_paths.get(sp, {}) or {}).get(
                    "footprint", "")
                if img is None or not path:
                    continue
                if img.format() != QImage.Format.Format_Indexed8:
                    img = img.convertToFormat(QImage.Format.Format_Indexed8)
                if export_indexed_png(img, [(255, 255, 255), (0, 0, 0)], path,
                                      transparent_index=-1):
                    total_ok += 1
                else:
                    all_errors.append(f"footprint-png:{sp}")
            self._footprint_png_dirty.clear()
        return total_ok, all_errors
