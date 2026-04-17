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

        # Enemy (front) sprite — pokefirered draws the 64x64 frame
        # CENTERED on sBattlerCoords, plus y_offset pushes it DOWN,
        # minus enemy elevation pushes it UP.
        if self._front_pix and not self._front_pix.isNull():
            fw = self._front_pix.width()
            fh = self._front_pix.height()
            frame_top = (self.ENEMY_CY - fh // 2
                         + self._front_y_off - self._enemy_elevation)
            frame_left = self.ENEMY_CX - fw // 2
            p.drawPixmap(frame_left * s, frame_top * s,
                         fw * s, fh * s, self._front_pix)

        # Player (back) sprite — same frame-center rule, back y_offset
        # pushes DOWN.
        if self._back_pix and not self._back_pix.isNull():
            bw = self._back_pix.width()
            bh = self._back_pix.height()
            frame_top = (self.PLAYER_CY - bh // 2 + self._back_y_off)
            frame_left = self.PLAYER_CX - bw // 2
            p.drawPixmap(frame_left * s, frame_top * s,
                         bw * s, bh * s, self._back_pix)

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

        self._import_png_btn = QPushButton("Select Indexed PNG...")
        self._import_png_btn.setToolTip(
            "Pick an indexed (palette-mode) PNG and import its color\n"
            "table into the Normal or Shiny .pal file for this species.\n"
            "The PNG's embedded palette is extracted — the image itself\n"
            "is not modified or copied."
        )
        ig_import.addWidget(self._import_png_btn)

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
        """Extract palette from an indexed PNG and load it as Normal or Shiny."""
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
            self, "Select Indexed PNG",
            start_dir,
            "PNG Images (*.png)",
        )
        if not path:
            return

        # Read the PNG and extract its color table
        img = QImage(path)
        if img.isNull():
            QMessageBox.warning(
                self, "Import Failed",
                f"Could not load image:\n{path}",
            )
            return

        # Must be an indexed (palette-mode) image
        from PyQt6.QtGui import QImage as _QI
        if img.format() != _QI.Format.Format_Indexed8:
            QMessageBox.warning(
                self, "Not an Indexed PNG",
                "This PNG is not in indexed (palette) mode.\n\n"
                "The image must be saved as an indexed-color PNG\n"
                "(8-bit, 16 colors) so its embedded palette can be\n"
                "extracted. Convert it in your image editor first.",
            )
            return

        ct = img.colorTable()
        if len(ct) < 1:
            QMessageBox.warning(
                self, "Empty Palette",
                "The PNG has no color table entries.",
            )
            return

        # Extract up to 16 colors, GBA-clamp each one
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

        # Refresh the battle scene preview so the user sees it immediately
        show_shiny = self._preview_shiny
        if (is_shiny and show_shiny) or (not is_shiny and not show_shiny):
            self._refresh_preview_sprites()

        self._mark_modified()

        QMessageBox.information(
            self, "Palette Imported",
            f"Loaded {len(ct[:16])} colors from:\n"
            f"{os.path.basename(path)}\n\n"
            f"Applied to: {target_label} palette\n"
            f"Species: {self._current_species}\n\n"
            "The palette's order was preserved as-is. If the transparent\n"
            "slot is in the wrong position, drag the correct colour onto\n"
            "slot 0 in the palette row.\n\n"
            "Click File → Save to write the .pal file to disk.",
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
        # Species palettes
        if self._project_root:
            for sp in list(self._palette_dirty):
                npath, spath = species_pal_paths(self._project_root, sp)
                pal = self._palettes.get(sp, {})
                if pal.get("normal") and write_jasc_pal(npath, pal["normal"]):
                    total_ok += 1
                else:
                    all_errors.append(f"pal-normal:{sp}")
                if pal.get("shiny") and write_jasc_pal(spath, pal["shiny"]):
                    total_ok += 1
                else:
                    all_errors.append(f"pal-shiny:{sp}")
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
