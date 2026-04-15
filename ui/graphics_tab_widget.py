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

from PyQt6.QtCore import Qt, QSize, pyqtSignal, QTimer
from PyQt6.QtGui import (
    QColor, QPainter, QPixmap, QImage, QFont, QMouseEvent, QIntValidator,
    QRegularExpressionValidator,
)
from PyQt6.QtCore import QRegularExpression
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QSpinBox,
    QCheckBox, QComboBox, QGroupBox, QPushButton, QColorDialog, QFrame,
    QSizePolicy, QDialog, QDialogButtonBox, QLineEdit, QFileDialog,
    QRadioButton, QMessageBox, QButtonGroup, QScrollArea,
)

from ui.graphics_data import (
    GraphicsDataCache, species_pal_paths, icon_palette_pal_path,
    species_slug_from_const,
)
from ui.palette_utils import read_jasc_pal, write_jasc_pal, clamp_to_gba
from ui.draggable_palette_row import DraggablePaletteRow
from core.gba_image_utils import reorder_palette, export_indexed_png


Color = Tuple[int, int, int]


def _reskin_indexed_png(path: str, palette: List[Color]) -> Optional[QPixmap]:
    """Recolour an indexed-palette PNG using a new 16-colour palette.

    Assumes the PNG's palette order matches the species' .pal file (true for
    pokefirered sprites since both are generated from the same source).

    Returns None on any failure; caller should fall back to the original.
    """
    try:
        img = QImage(path)
        if img.isNull():
            return None
        if img.format() != QImage.Format.Format_Indexed8:
            img = img.convertToFormat(QImage.Format.Format_Indexed8)
        # Preserve the ORIGINAL alpha channel on every index. pokefirered
        # sprites rely on GBA palette index 0 being transparent, which the
        # PNG encodes via tRNS — Qt loads that alpha into the colour table.
        # We only swap R/G/B, never touch alpha.
        ct = list(img.colorTable())
        for i, (r, g, b) in enumerate(palette[:16]):
            if i >= len(ct):
                ct.append((0xFF << 24) | (r << 16) | (g << 8) | b)
            else:
                alpha = ct[i] & 0xFF000000
                ct[i] = alpha | (r << 16) | (g << 8) | b
        img.setColorTable(ct)
        return QPixmap.fromImage(img)
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

        # Icon palette storage (per-palette-slot, shared across all species)
        # { 0: [16 tuples], 1: [...], 2: [...] }
        self._icon_palettes: Dict[int, List[Color]] = {}
        self._icon_pal_dirty: set[int] = set()

        # Reordered front/back PNGs awaiting save.
        # { species: { "front": (path, QImage), "back": (path, QImage) } }
        # Populated by _on_palette_reordered; consumed by flush_to_disk.
        self._pending_reindexed_pngs: Dict[str, Dict[str, Tuple[str, QImage]]] = {}
        # Cache of post-reorder QImage for live preview (keyed by current
        # species' role: "front" / "back"). Cleared on species switch.
        self._live_indexed: Dict[str, QImage] = {}

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
            "Normal Palette  (drag to reorder — drop on first slot = transparent)",
            self._normal_row,
        ))

        # Shiny palette — same drag-reorder behaviour. Reordering either
        # the normal or shiny row applies the SAME order to the other
        # palette + reindexes the front/back PNGs, keeping shiny visually
        # correct in-game.
        self._shiny_row = DraggablePaletteRow()
        right.addWidget(self._wrap(
            "Shiny Palette  (drag to reorder — drop on first slot = transparent)",
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
            "Click Save to write to disk."
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
        # Live-preview cache is per-species — drop it when switching.
        # If this species has unsaved reindexed PNGs, reload them so the
        # preview keeps showing the post-reorder pixels.
        self._live_indexed = {}
        pending = self._pending_reindexed_pngs.get(species, {})
        for role, (_p, img) in pending.items():
            self._live_indexed[role] = img
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
            # If user has shiny preview on, re-skin sprites with shiny pal
            if self._preview_shiny:
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

    def _on_any_palette_changed(self) -> None:
        """Re-render preview sprites when the currently-visible palette
        is being edited, so live colour tweaks show up immediately."""
        self._refresh_preview_sprites()

    def _refresh_preview_sprites(self) -> None:
        """Apply normal or shiny palette to the stored source sprites
        and push them into the BattleScenePreview widget."""
        palette: Optional[List[Color]] = None
        if self._current_species and self._current_species in self._palettes:
            key = "shiny" if self._preview_shiny else "normal"
            palette = self._palettes[self._current_species].get(key)
        if not palette:
            self._preview.set_front_pixmap(self._front_src)
            self._preview.set_back_pixmap(self._back_src)
            return

        def _recolour(role: str, path: str, fallback: Optional[QPixmap]) -> Optional[QPixmap]:
            # If the user reordered the palette, the on-disk PNG has the
            # OLD pixel order — use the in-memory reindexed QImage instead
            # so preview matches what will be saved.
            live = self._live_indexed.get(role)
            if live is not None:
                try:
                    img = QImage(live)  # copy so we don't mutate cache
                    if img.format() != QImage.Format.Format_Indexed8:
                        img = img.convertToFormat(QImage.Format.Format_Indexed8)
                    # Build colour table from scratch — alpha=0 ONLY at
                    # slot 0 (the "BG"/transparent slot), 255 elsewhere.
                    # Don't preserve alpha-by-position from the previous
                    # state; that breaks chained reorders where the
                    # previous transparent slot should now be opaque.
                    ct = []
                    for i, (r, g, b) in enumerate(palette[:16]):
                        a = 0 if i == 0 else 255
                        ct.append((a << 24) | (r << 16) | (g << 8) | b)
                    while len(ct) < 256:
                        ct.append(0xFF000000)
                    img.setColorTable(ct)
                    # Convert to ARGB32 so alpha actually punches through
                    # in the preview QPixmap (Format_Indexed8 + tRNS-style
                    # alpha is unreliable through QPixmap.fromImage).
                    return QPixmap.fromImage(
                        img.convertToFormat(QImage.Format.Format_ARGB32)
                    )
                except Exception:
                    pass
            if path:
                return _reskin_indexed_png(path, palette) or fallback
            return fallback

        self._preview.set_front_pixmap(
            _recolour("front", self._front_src_path, self._front_src)
        )
        self._preview.set_back_pixmap(
            _recolour("back", self._back_src_path, self._back_src)
        )

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

    def _set_thumb(self, lbl: QLabel, path: str) -> None:
        if not path or not os.path.exists(path):
            lbl.clear()
            return
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

    def _load_pix(self, path: str) -> Optional[QPixmap]:
        if not path or not os.path.exists(path):
            return None
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
        self._palettes.setdefault(
            self._current_species, {"normal": [], "shiny": []}
        )["normal"] = self._normal_row.colors()
        self._palette_dirty.add(self._current_species)
        self._mark_modified()
        # Live recolour preview if currently showing normal
        if not self._preview_shiny:
            self._refresh_preview_sprites()

    def _on_shiny_changed(self) -> None:
        if self._loading or not self._current_species:
            return
        self._palettes.setdefault(
            self._current_species, {"normal": [], "shiny": []}
        )["shiny"] = self._shiny_row.colors()
        self._palette_dirty.add(self._current_species)
        self._mark_modified()
        # Live recolour preview if currently showing shiny
        if self._preview_shiny:
            self._refresh_preview_sprites()

    def _on_palette_reordered(self, source: str, from_idx: int, to_idx: int) -> None:
        """User dragged a swatch in the normal OR shiny row.

        Normal and shiny palettes are independent — each owns its own
        order. Only the normal palette is the one the PNG pixels are
        indexed against.

        Normal drag → reorder normal.pal + remap front/back PNG pixels
                     so the image looks the same and slot 0 (transparent)
                     points to the user's chosen colour. Shiny .pal is
                     left alone (shiny visual will shift as a side effect
                     of the PNG remap; that's an engine constraint).

        Shiny drag → reorder shiny.pal only. PNG is untouched. Normal
                    .pal is untouched. Shiny visual shifts because slot N
                    in shiny.pal now holds a different colour.
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
        normal_pal = list(self._palettes[sp].get("normal") or [(0, 0, 0)] * n)
        shiny_pal = list(self._palettes[sp].get("shiny") or [(0, 0, 0)] * n)
        while len(normal_pal) < n:
            normal_pal.append((0, 0, 0))
        while len(shiny_pal) < n:
            shiny_pal.append((0, 0, 0))

        new_order = list(range(n))
        new_order.remove(from_idx)
        new_order.insert(to_idx, from_idx)

        if source == "normal":
            # Remap the front + back indexed PNGs so pixels still point
            # to the same colours — only which slot holds which colour
            # (and therefore which pixels are transparent) changes.
            cached_pending = self._pending_reindexed_pngs.get(sp, {})
            for role, src_path in (
                ("front", self._front_src_path),
                ("back", self._back_src_path),
            ):
                if not src_path:
                    continue
                if role in cached_pending:
                    img = cached_pending[role][1]
                else:
                    img = QImage(src_path)
                    if img.isNull():
                        continue
                    if img.format() != QImage.Format.Format_Indexed8:
                        img = img.convertToFormat(QImage.Format.Format_Indexed8)
                try:
                    new_img, _ = reorder_palette(img, normal_pal, new_order)
                except Exception:
                    continue
                self._pending_reindexed_pngs.setdefault(sp, {})[role] = (
                    src_path, new_img,
                )
                self._live_indexed[role] = new_img

            new_normal = [normal_pal[old] for old in new_order]
            self._palettes[sp]["normal"] = new_normal
            self._loading = True
            try:
                self._normal_row.set_colors(new_normal)
            finally:
                self._loading = False
        else:
            # Shiny drag: reorder shiny.pal only. No PNG remap, no
            # touching of the normal row.
            new_shiny = [shiny_pal[old] for old in new_order]
            self._palettes[sp]["shiny"] = new_shiny
            self._loading = True
            try:
                self._shiny_row.set_colors(new_shiny)
            finally:
                self._loading = False

        self._palette_dirty.add(sp)
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
        self._icon_palettes[idx] = self._icon_rows[idx].colors()
        self._icon_pal_dirty.add(idx)
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
            "The preview has been updated. Click File → Save to\n"
            "write the .pal file to disk.",
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
            or self._palette_dirty or self._icon_pal_dirty
            or self._pending_reindexed_pngs
        )

    def flush_to_disk(self) -> tuple[int, list[str]]:
        """Write all pending changes. Called by mainwindow save pipeline."""
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
            # Reindexed front/back PNGs — write each as an indexed PNG
            # whose colour table reflects the (already-saved) normal palette
            # with index 0 marked transparent.
            for sp, role_map in list(self._pending_reindexed_pngs.items()):
                pal = self._palettes.get(sp, {}).get("normal") or [(0, 0, 0)] * 16
                for role, (path, img) in role_map.items():
                    try:
                        if export_indexed_png(img, pal, path, transparent_index=0):
                            total_ok += 1
                        else:
                            all_errors.append(f"png-{role}:{sp}")
                    except Exception:
                        all_errors.append(f"png-{role}:{sp}")
            self._pending_reindexed_pngs.clear()
            # Icon palettes
            for idx in list(self._icon_pal_dirty):
                path = icon_palette_pal_path(self._project_root, idx)
                if write_jasc_pal(path, self._icon_palettes.get(idx, [])):
                    total_ok += 1
                else:
                    all_errors.append(f"icon-pal:{idx}")
            self._icon_pal_dirty.clear()
        return total_ok, all_errors
