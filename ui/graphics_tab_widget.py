"""Graphics tab for the Pokemon editor.

Full three-column layout:

  LEFT    : Front sprite, back sprite, icon, footprint thumbnails
  CENTER  : Battle scene preview (BattleBG + sprites + shadow + textbox)
            Spinboxes: Player Y (back y_offset), Enemy Y (front y_offset),
            Enemy Altitude (gEnemyMonElevation), Show Shadow checkbox.
  RIGHT   : Normal palette (16 clickable swatches)
            Shiny palette  (16 clickable swatches)
            Icon palette selector (0/1/2 dropdown) + three editable rows.

All edits live in an in-memory cache (GraphicsDataCache + per-species palette
dicts). Edits mark window dirty.  Call ``flush_to_disk()`` on Save to write.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QRegularExpression
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
from core.gba_image_utils import swap_palette_entries, export_indexed_png
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
        self._show_shadow = True

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

        # Optional battle-animation sprite overlay (used by the Battle
        # Anims tab; the Pokemon Graphics tab leaves this None so its
        # behaviour is unchanged).  Drawn frame-CENTERED on
        # (_anim_cx, _anim_cy), above the mons and below the textbox.
        self._anim_pix: Optional[QPixmap] = None
        self._anim_cx = self.ENEMY_CX
        self._anim_cy = self.ENEMY_CY
        # Battle-animation BACKGROUND (Surf/Cosmic/Sandstorm/...): a full-screen
        # image drawn OVER the battle BG and BEHIND the mons, scrolled by
        # (_anim_bg_x, _anim_bg_y). None = no anim background this move.
        self._anim_bg: Optional[QPixmap] = None
        self._anim_bg_x = 0
        self._anim_bg_y = 0

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

    def set_show_shadow(self, show: bool) -> None:
        self._show_shadow = bool(show)
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

    def set_anim_bg(self, pix: Optional[QPixmap], x: int = 0, y: int = 0) -> None:
        """Set (or clear with None) the battle-animation background, scrolled to
        (x, y). Drawn over the battle BG and behind the mons."""
        self._anim_bg = pix
        self._anim_bg_x = int(x)
        self._anim_bg_y = int(y)
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

    def reset_mon_transforms(self) -> None:
        """Restore both mons to their untransformed, visible draw (end of play)."""
        changed = (self._front_fx != (0, 0, 1.0, 1.0)
                   or self._back_fx != (0, 0, 1.0, 1.0)
                   or not self._front_visible or not self._back_visible
                   or self._front_sink is not None or self._back_sink is not None
                   or self._front_aff is not None or self._back_aff is not None
                   or self._front_clones or self._back_clones)
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
        self.update()

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

        # Background
        if self._bg and not self._bg.isNull():
            p.drawPixmap(
                0, 0, self.CANVAS_W * s, self.CANVAS_H * s, self._bg
            )
        else:
            p.fillRect(self.rect(), QColor(40, 40, 40))

        # Battle-animation background (Surf/Cosmic/Sandstorm/...): drawn OVER
        # the battle BG and BEHIND the mons, tiled with a scroll offset.
        if self._anim_bg is not None and not self._anim_bg.isNull():
            bw, bh = self._anim_bg.width(), self._anim_bg.height()
            if bw > 0 and bh > 0:
                ox = self._anim_bg_x % bw
                oy = self._anim_bg_y % bh
                yy = -oy
                while yy < self.CANVAS_H:
                    xx = -ox
                    while xx < self.CANVAS_W:
                        p.drawPixmap(xx * s, yy * s, bw * s, bh * s, self._anim_bg)
                        xx += bw
                    yy += bh

        # Shadow is created at (enemy_x, enemy_y + 29) per pokefirered
        # (src/battle_gfx_sfx_util.c :: LoadAndCreateEnemyShadowSprites).
        # It's a FIXED position — independent of the sprite frame. The game
        # only shows it when gEnemyMonElevation[species] != 0.
        shadow_visible = (self._show_shadow and self._enemy_elevation > 0
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
                for off_x, off_y, hf, vf in clones:
                    cp = pix
                    if hf or vf:
                        cp = pix.transformed(_QT().scale(-1 if hf else 1,
                                                         -1 if vf else 1))
                    fl = base_cx - cw // 2 + off_x
                    ft = base_cy - ch // 2 + off_y
                    p.drawPixmap(fl * s, ft * s, cw * s, ch * s, cp)

            p.setOpacity(0.45)
            _draw_clones(self._back_pix, self.PLAYER_CX,
                         self.PLAYER_CY + self._back_y_off, self._back_clones)
            _draw_clones(self._front_pix, self.ENEMY_CX,
                         self.ENEMY_CY + self._front_y_off - self._enemy_elevation,
                         self._front_clones)
            p.setOpacity(1.0)

        # Enemy (front) sprite — pokefirered draws the 64x64 frame
        # CENTERED on sBattlerCoords, plus y_offset pushes it DOWN,
        # minus enemy elevation pushes it UP.
        if self._front_pix and not self._front_pix.isNull():
            fw = self._front_pix.width()
            fh = self._front_pix.height()
            frame_top = (self.ENEMY_CY - fh // 2
                         + self._front_y_off - self._enemy_elevation)
            frame_left = self.ENEMY_CX - fw // 2
            if self._front_sink is not None:
                self._paint_mon_sink(p, self._front_pix, frame_left, frame_top,
                                     self._front_sink, s)
            elif self._front_aff is not None:
                self._paint_mon_affine(p, self._front_pix, frame_left, frame_top,
                                       fw, fh, self._front_aff, s)
            elif self._front_visible:
                self._draw_mon(p, self._front_pix, frame_left, frame_top,
                               fw, fh, self._front_fx, s)

        # Player (back) sprite — same frame-center rule, back y_offset
        # pushes DOWN.
        if self._back_pix and not self._back_pix.isNull():
            bw = self._back_pix.width()
            bh = self._back_pix.height()
            frame_top = (self.PLAYER_CY - bh // 2 + self._back_y_off)
            frame_left = self.PLAYER_CX - bw // 2
            if self._back_sink is not None:
                self._paint_mon_sink(p, self._back_pix, frame_left, frame_top,
                                     self._back_sink, s)
            elif self._back_aff is not None:
                self._paint_mon_affine(p, self._back_pix, frame_left, frame_top,
                                       bw, bh, self._back_aff, s)
            elif self._back_visible:
                self._draw_mon(p, self._back_pix, frame_left, frame_top,
                               bw, bh, self._back_fx, s)

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
        self._palette_dirty: set[str] = set()  # species keys

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
        self._icon_frame = 0
        self._icon_timer = QTimer(self)
        self._icon_timer.setInterval(400)  # ~2.5 fps, matches Info tab
        self._icon_timer.timeout.connect(self._tick_icon_anim)
        self._icon_timer.start()
        self._preview_shiny = False  # False=normal, True=shiny

        self._build_ui()

    # ────────────────────────────────────────────────────────── build UI ──
    def _build_ui(self) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(12)

        # ── LEFT COLUMN ── sprite thumbnails ────────────────────────────
        left = QVBoxLayout()
        left.setSpacing(8)

        self._front_thumb = self._make_thumb(64, "Front")
        self._back_thumb = self._make_thumb(64, "Back")
        self._foot_thumb = self._make_thumb(16, "Footprint")

        for group in (self._front_thumb[0], self._back_thumb[0],
                      self._foot_thumb[0]):
            left.addWidget(group)

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
            "Enemy altitude — how many pixels the sprite floats above\n"
            "the platform. Zero for most species, nonzero for flying/hovering.\n"
            "(gEnemyMonElevation[SPECIES])"
        )

        self._show_shadow_cb = QCheckBox("Show Shadow")
        self._show_shadow_cb.setChecked(True)
        self._show_shadow_cb.setToolTip(
            "Preview-only toggle — the actual ROM behaviour is controlled\n"
            "by the front pic coords / battle engine, not a per-species flag."
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
        pos_row.addWidget(self._show_shadow_cb, 1, 2)
        pos_row.addWidget(self._shiny_preview_cb, 1, 3)

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
            "Pick a PNG to import into the Normal or Shiny palette.\n"
            "An indexed (palette-mode) PNG has its colour table\n"
            "extracted directly — the image itself isn't modified.\n"
            "A non-indexed PNG (or one with more than 16 colours)\n"
            "automatically opens the manual palette picker so you\n"
            "can choose the 16 colours and remap the image."
        )
        ig_import.addWidget(self._import_png_btn)

        self._import_png_manual_btn = QPushButton("Import PNG Manually...")
        self._import_png_manual_btn.setToolTip(
            "Open the manual palette picker on a PNG (any format —\n"
            "indexed or full-colour).  You choose which colours land\n"
            "in which slot, mark the BG/transparent slot, reorder\n"
            "freely, and see a live preview of the remap.  The result\n"
            "is loaded into the Normal or Shiny palette (whichever\n"
            "radio is selected above)."
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
        self._show_shadow_cb.toggled.connect(self._preview.set_show_shadow)
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

    def _make_thumb(self, display_size: int, title: str):
        box = QGroupBox(title)
        bl = QVBoxLayout(box)
        bl.setContentsMargins(6, 14, 6, 6)
        bl.setSpacing(4)
        lbl = QLabel()
        lbl.setFixedSize(display_size, display_size)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet("background: #181818; border: 1px solid #333;")
        bl.addWidget(lbl, 0, Qt.AlignmentFlag.AlignHCenter)
        return box, lbl

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
        self._palette_dirty.clear()
        # Refresh the icon rows with loaded palettes
        self._loading = True
        try:
            for i, row in enumerate(self._icon_rows):
                row.set_colors(self._icon_palettes.get(i, [(0, 0, 0)] * 16))
        finally:
            self._loading = False

    def load_species(self, species: str,
                     front_path: str = "", back_path: str = "",
                     icon_path: str = "", footprint_path: str = "") -> None:
        """Load all data + sprites for the given SPECIES_ constant."""
        self._current_species = species
        self._loading = True
        try:
            # Thumbnails (front/back full, footprint full, icon = first frame only)
            self._set_thumb(self._front_thumb[1], front_path)
            self._set_thumb(self._back_thumb[1], back_path)
            self._set_thumb(self._foot_thumb[1], footprint_path)

            # Icon source (animated separately)
            self._icon_src_path = icon_path or ""
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
        palette: Optional[List[Color]] = None
        sp = self._current_species
        if sp and sp in self._palettes:
            key = "shiny" if self._preview_shiny else "normal"
            palette = self._palettes[sp].get(key)
        if not palette:
            self._preview.set_front_pixmap(self._front_src)
            self._preview.set_back_pixmap(self._back_src)
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

        self._preview.set_front_pixmap(front_pix)
        self._preview.set_back_pixmap(back_pix)

        # Keep left-column thumbnails in sync with the palette too
        self._set_thumb(self._front_thumb[1], self._front_src_path, palette)
        self._set_thumb(self._back_thumb[1], self._back_src_path, palette)

    def _load_species_palettes(self, species: str) -> None:
        if not self._project_root:
            return
        if species not in self._palettes:
            npath, spath = species_pal_paths(self._project_root, species)
            self._palettes[species] = {
                "normal": read_jasc_pal(npath) or [(0, 0, 0)] * 16,
                "shiny": read_jasc_pal(spath) or [(0, 0, 0)] * 16,
            }
        pal = self._palettes[species]
        self._normal_row.set_colors(pal["normal"])
        self._shiny_row.set_colors(pal["shiny"])

    def _set_thumb(self, lbl: QLabel, path: str,
                   palette: Optional[List[Color]] = None) -> None:
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
        self._palette_dirty.add(self._current_species)
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
        self._palette_dirty.add(self._current_species)
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

        self._palette_dirty.add(sp)
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
        self._palette_dirty.add(sp)
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
        # may set the combo after building the recolour).
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

    # ────────────────────────────── palette import from indexed PNG ──
    def _import_palette_from_png(self) -> None:
        """Auto-extract palette from an indexed PNG and load it as
        Normal or Shiny."""
        self._do_import_palette_from_png(manual=False)

    def _import_palette_from_png_manual(self) -> None:
        """Open the manual palette picker on ANY PNG (indexed or not)
        and load the chosen palette as Normal or Shiny."""
        self._do_import_palette_from_png(manual=True)

    def _do_import_palette_from_png(self, manual: bool) -> None:
        if not self._current_species:
            QMessageBox.information(
                self, "No Species Selected",
                "Select a species first, then import a palette.",
            )
            return

        # Pick file — default to this species' graphics folder
        start_dir = ""
        if self._project_root and self._current_species:
            slug = species_slug_from_const(self._current_species)
            candidate = os.path.join(
                self._project_root, "graphics", "pokemon", slug
            )
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

        # Update in-memory palette cache
        self._palettes.setdefault(
            self._current_species, {"normal": [], "shiny": []}
        )[key] = colors
        self._palette_dirty.add(self._current_species)

        # Update the swatch row display
        self._loading = True
        try:
            if is_shiny:
                self._shiny_row.set_colors(colors)
            else:
                self._normal_row.set_colors(colors)
        finally:
            self._loading = False

        # Manual mode: also remap+save the source PNG to front.png so
        # the species's sprite art reflects the user's import.  Front is
        # chosen as the default because that's where custom species art
        # almost always targets — the user can import a separate back
        # PNG via the same flow if they have one.  Auto mode never
        # touches the PNG (palette-only — pixel indices preserved).
        image_saved_to: Optional[str] = None
        image_size_warning = ""
        if manual and remapped_img is not None:
            paths = self._sprite_paths.get(self._current_species, {})
            front_path = paths.get("front", "")
            back_path = paths.get("back", "")
            # Prefer the one that matches the source's dimensions.  This
            # auto-routes a back-view PNG to back.png if the user has one
            # of those, while keeping front.png as the default target.
            front_dims = _png_dims(front_path)
            back_dims = _png_dims(back_path)
            src_dims = (remapped_img.width(), remapped_img.height())
            dest_path = ""
            if front_dims and src_dims == front_dims:
                dest_path = front_path
            elif back_dims and src_dims == back_dims:
                dest_path = back_path
            elif front_path:
                # Dimensions don't match either — default to front.png
                # but warn that the on-disk sprite dimensions change.
                dest_path = front_path
                if front_dims and src_dims != front_dims:
                    image_size_warning = (
                        f"\n\nWarning: the imported image is "
                        f"{src_dims[0]}×{src_dims[1]} pixels but the "
                        f"existing front.png is "
                        f"{front_dims[0]}×{front_dims[1]}.  The on-disk "
                        f"front.png now has the new dimensions.  Run "
                        f"Make Modern to rebuild — the build may fail "
                        f"if the project expects a specific frame size."
                    )
            if dest_path:
                try:
                    from ui.dialogs.manual_palette_pick_dialog import (
                        save_remapped_image,
                    )
                    if save_remapped_image(
                            remapped_img, colors, dest_path):
                        image_saved_to = dest_path
                    else:
                        QMessageBox.warning(
                            self, "Image Save Failed",
                            f"Palette loaded, but couldn't write the "
                            f"remapped PNG to:\n{dest_path}",
                        )
                except Exception as exc:
                    QMessageBox.warning(
                        self, "Image Save Failed",
                        f"Could not save the remapped image:\n{exc}",
                    )
            # If we wrote a new sprite PNG, refresh the species's cached
            # front/back QPixmap so the preview reflects it immediately.
            if image_saved_to:
                try:
                    self._sprite_paths.setdefault(
                        self._current_species, {})
                    if image_saved_to == paths.get("front", ""):
                        self._front_src = self._load_pix(image_saved_to)
                        self._preview.set_front_pixmap(self._front_src)
                    elif image_saved_to == paths.get("back", ""):
                        self._back_src = self._load_pix(image_saved_to)
                        self._preview.set_back_pixmap(self._back_src)
                except Exception:
                    pass

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
            f"\nImage written to: "
            f"{os.path.basename(image_saved_to)}"
            if image_saved_to else ""
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
            "Click File → Save to write the .pal file to disk."
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

        # Default to this species' graphics folder
        start_dir = ""
        if self._project_root and self._current_species:
            slug = species_slug_from_const(self._current_species)
            candidate = os.path.join(
                self._project_root, "graphics", "pokemon", slug
            )
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
        self._palette_dirty.add(self._current_species)

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
            or self._palette_dirty
            or self._icon_pal_dirty
            or self._sprite_png_dirty
        )

    def flush_to_disk(self) -> tuple[int, list[str]]:
        """Write all pending changes. Called by mainwindow save pipeline.

        Drag-reorder and palette imports only rewrite the ``.pal`` files —
        pixel data in the front/back PNGs is never touched on those paths.

        The ONLY path on this tab that rewrites PNG pixel data is the
        right-click "Index as Background" operation, which populates
        ``_sprite_png_dirty``.  Species listed there also have entries in
        ``_palette_dirty`` (both pals were lockstep-swapped), so the .pal
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
            for sp in list(self._palette_dirty):
                npath, spath = species_pal_paths(self._project_root, sp)
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
            self._palette_dirty.clear()
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
        return total_ok, all_errors
