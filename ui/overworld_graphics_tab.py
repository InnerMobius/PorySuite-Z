"""Overworld Graphics tab — palette-centric editor for field object sprites.

Top-level tab in PorySuite-Z showing all overworld sprite palettes and the
sprites that share each palette.  Palette changes preview live across every
affected sprite.

Layout:
  LEFT:   Palette pool list (Player, NPC Blue, NPC Pink, …)
          Editable 16-colour swatch row for selected palette
          Import Palette from PNG / Open Folder buttons

  RIGHT:  Category filter (All / Players / NPCs / Pokemon / Objects)
          Scrollable grid of animated sprite previews using the selected palette
          Click a sprite → large sheet view + 4-direction walk animation below
"""

from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QSize
from PyQt6.QtGui import QImage, QPixmap, QColor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QGroupBox,
    QPushButton, QFileDialog, QMessageBox, QListWidget, QListWidgetItem,
    QScrollArea, QGridLayout, QFrame, QSplitter, QSizePolicy, QLineEdit,
    QDialog, QFormLayout, QSpinBox, QDialogButtonBox,
)

from ui.palette_utils import read_jasc_pal, write_jasc_pal, clamp_to_gba
from ui.graphics_tab_widget import PaletteSwatchRow
from core.sprite_palette_bus import get_bus as _get_palette_bus

Color = Tuple[int, int, int]


# ── C header parsing helpers ────────────────────────────────────────────────

def _parse_palette_tag_defines(root: str) -> Dict[str, int]:
    """Parse OBJ_EVENT_PAL_TAG_* defines → {name: int_value}."""
    path = os.path.join(root, "src", "event_object_movement.c")
    result: Dict[str, int] = {}
    if not os.path.isfile(path):
        return result
    pat = re.compile(r"#define\s+(OBJ_EVENT_PAL_TAG_\w+)\s+(0x[0-9a-fA-F]+|\d+)")
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = pat.search(line)
                if m:
                    result[m.group(1)] = int(m.group(2), 0)
    except OSError:
        pass
    return result


def _parse_palette_table(root: str) -> Dict[str, str]:
    """Parse sObjectEventSpritePalettes → {OBJ_EVENT_PAL_TAG_*: gObjectEventPal_*}.

    Returns mapping from palette tag constant name to the palette data symbol.
    """
    path = os.path.join(root, "src", "event_object_movement.c")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return {}

    # Find the array
    start = text.find("sObjectEventSpritePalettes")
    if start < 0:
        return {}
    brace = text.find("{", start)
    if brace < 0:
        return {}
    # Find matching closing brace
    depth, end = 0, brace
    for i in range(brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    block = text[brace:end + 1]
    # Each entry: {gObjectEventPal_Foo, OBJ_EVENT_PAL_TAG_BAR}
    pat = re.compile(r"\{(gObjectEventPal_\w+),\s*(OBJ_EVENT_PAL_TAG_\w+)\}")
    result: Dict[str, str] = {}
    for m in pat.finditer(block):
        result[m.group(2)] = m.group(1)
    return result


def _parse_pal_symbol_to_path(root: str) -> Dict[str, str]:
    """Parse object_event_graphics.h → {gObjectEventPal_*: relative_gbapal_path}."""
    path = os.path.join(root, "src", "data", "object_events", "object_event_graphics.h")
    if not os.path.isfile(path):
        return {}
    pat = re.compile(r"(gObjectEventPal_\w+)\[\]\s*=\s*INCBIN_U\d+\(\"([^\"]+)\"\)")
    result: Dict[str, str] = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = pat.search(line)
                if m:
                    result[m.group(1)] = m.group(2)
    except OSError:
        pass
    return result


def _parse_graphics_info(root: str) -> Dict[str, dict]:
    """Parse object_event_graphics_info.h → {InfoName: {paletteTag, width, height, inanimate}}.

    InfoName is the suffix after gObjectEventGraphicsInfo_, e.g. "RedNormal".
    """
    path = os.path.join(root, "src", "data", "object_events", "object_event_graphics_info.h")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return {}

    result: Dict[str, dict] = {}
    # Find each gObjectEventGraphicsInfo_<Name> = { ... };
    entry_pat = re.compile(
        r"gObjectEventGraphicsInfo_(\w+)\s*=\s*\{([^;]+)\};", re.DOTALL
    )
    field_pats = {
        "paletteTag": re.compile(r"\.paletteTag\s*=\s*(\w+)"),
        "width": re.compile(r"\.width\s*=\s*(\d+)"),
        "height": re.compile(r"\.height\s*=\s*(\d+)"),
        "inanimate": re.compile(r"\.inanimate\s*=\s*(\w+)"),
        "paletteSlot": re.compile(r"\.paletteSlot\s*=\s*(\w+)"),
        "images": re.compile(r"\.images\s*=\s*(\w+)"),
        "anims": re.compile(r"\.anims\s*=\s*(\w+)"),
    }
    for m in entry_pat.finditer(text):
        name = m.group(1)
        body = m.group(2)
        info: dict = {}
        for key, fp in field_pats.items():
            fm = fp.search(body)
            if fm:
                val = fm.group(1)
                if key in ("width", "height"):
                    try:
                        val = int(val)
                    except ValueError:
                        pass
                elif key == "inanimate":
                    val = val == "TRUE"
                info[key] = val
        result[name] = info
    return result


def _parse_pic_tables(root: str) -> Dict[str, str]:
    """Parse object_event_graphics_info_pointers.h or the main graphics_info.h
    to map OBJ_EVENT_GFX_* → GraphicsInfo name.

    Returns {OBJ_EVENT_GFX_CONST: InfoName}.
    """
    # The pointers file maps [OBJ_EVENT_GFX_*] = &gObjectEventGraphicsInfo_*
    path = os.path.join(
        root, "src", "data", "object_events",
        "object_event_graphics_info_pointers.h"
    )
    if not os.path.isfile(path):
        return {}
    pat = re.compile(
        r"\[(OBJ_EVENT_GFX_\w+)\]\s*=\s*&gObjectEventGraphicsInfo_(\w+)"
    )
    result: Dict[str, str] = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = pat.search(line)
                if m:
                    result[m.group(1)] = m.group(2)
    except OSError:
        pass
    return result


def _parse_pic_symbol_to_path(root: str) -> Dict[str, str]:
    """Parse object_event_graphics.h → {gObjectEventPic_*: relative .4bpp path}.

    Returns symbol → relative path from project root (e.g.
    "gObjectEventPic_RedNormal" → "graphics/object_events/pics/people/red_normal.4bpp").
    """
    path = os.path.join(root, "src", "data", "object_events", "object_event_graphics.h")
    if not os.path.isfile(path):
        return {}
    pat = re.compile(r"(gObjectEventPic_\w+)\[\]\s*=\s*INCBIN_U\d+\(\"([^\"]+)\"\)")
    result: Dict[str, str] = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = pat.search(line)
                if m:
                    result[m.group(1)] = m.group(2)
    except OSError:
        pass
    return result


def _parse_pic_table_to_symbol(root: str) -> Dict[str, str]:
    """Parse object_event_pic_tables.h → {sPicTable_*: gObjectEventPic_*}.

    Each pic table references one gObjectEventPic_* symbol (the first one).
    """
    path = os.path.join(root, "src", "data", "object_events", "object_event_pic_tables.h")
    if not os.path.isfile(path):
        return {}
    result: Dict[str, str] = {}
    # Match: static const ... sPicTable_Foo[] = {
    table_pat = re.compile(r"(sPicTable_\w+)\[\]\s*=\s*\{")
    pic_pat = re.compile(r"overworld_frame\((gObjectEventPic_\w+)")
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return {}

    for tm in table_pat.finditer(text):
        table_name = tm.group(1)
        # Look for the first gObjectEventPic_* inside this table block
        block_start = tm.end()
        # Find closing };
        depth = 1
        block_end = block_start
        for i in range(block_start, min(block_start + 2000, len(text))):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    block_end = i
                    break
        block = text[block_start:block_end]
        pm = pic_pat.search(block)
        if pm:
            result[table_name] = pm.group(1)
    return result


def _find_sprite_pngs(root: str) -> Dict[str, Tuple[str, str]]:
    """Scan graphics/object_events/pics/ → {lowercase_slug: (abs_png_path, category)}.

    Category is 'people', 'pokemon', or 'misc'.
    """
    pics_root = os.path.join(root, "graphics", "object_events", "pics")
    result: Dict[str, Tuple[str, str]] = {}
    for cat in ("people", "pokemon", "misc"):
        cat_dir = os.path.join(pics_root, cat)
        if not os.path.isdir(cat_dir):
            continue
        for fname in os.listdir(cat_dir):
            if fname.endswith(".png"):
                slug = fname[:-4]  # strip .png
                result[slug] = (os.path.join(cat_dir, fname), cat)
    return result


def _resolve_sprite_png(info_name: str, info_data: Dict[str, dict],
                         pic_table_to_sym: Dict[str, str],
                         pic_sym_to_path: Dict[str, str],
                         slug_to_png: Dict[str, Tuple[str, str]],
                         root: str) -> Optional[Tuple[str, str]]:
    """Resolve a GraphicsInfo name to (abs_png_path, category) using the full
    INCBIN chain: InfoName → .images sPicTable_* → gObjectEventPic_* → path.

    Falls back to simple slug matching if the chain doesn't resolve.
    """
    info = info_data.get(info_name, {})
    pic_table = info.get("images")  # e.g. "sPicTable_RedNormal"

    if pic_table:
        pic_sym = pic_table_to_sym.get(pic_table)  # e.g. "gObjectEventPic_RedNormal"
        if pic_sym:
            rel_path = pic_sym_to_path.get(pic_sym)  # e.g. "graphics/.../red_normal.4bpp"
            if rel_path:
                png_path = os.path.join(root, rel_path.replace(".4bpp", ".png"))
                if os.path.isfile(png_path):
                    # Derive category from path
                    if "/people/" in png_path.replace("\\", "/"):
                        cat = "people"
                    elif "/pokemon/" in png_path.replace("\\", "/"):
                        cat = "pokemon"
                    else:
                        cat = "misc"
                    return (png_path, cat)

    # Fallback: simple slug match from the info name
    slug = info_name.lower()
    if slug in slug_to_png:
        return slug_to_png[slug]

    return None


# ── Sprite data model ───────────────────────────────────────────────────────

class SpriteEntry:
    """One overworld sprite with all metadata."""
    __slots__ = (
        "gfx_const", "info_name", "png_path", "category",
        "palette_tag", "width", "height", "inanimate", "anim_table",
    )

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)

    @property
    def display_name(self) -> str:
        return (self.gfx_const or "").replace("OBJ_EVENT_GFX_", "").replace("_", " ").title()


class PalettePool:
    """One shared palette with its tag, file path, and list of sprites."""
    __slots__ = ("tag_name", "display_name", "pal_path", "sprites")

    def __init__(self, tag_name: str, pal_path: str):
        self.tag_name = tag_name
        self.pal_path = pal_path
        # Friendly name: OBJ_EVENT_PAL_TAG_NPC_BLUE → NPC Blue
        self.display_name = (
            tag_name.replace("OBJ_EVENT_PAL_TAG_", "")
            .replace("_", " ").title()
        )
        self.sprites: List[SpriteEntry] = []


def build_overworld_data(root: str) -> Tuple[List[PalettePool], Dict[str, SpriteEntry]]:
    """Parse all C headers and build the complete data model.

    Returns (palette_pools sorted by name, all_sprites dict by gfx_const).
    """
    # 1. Palette tag → symbol → file path chain
    tag_to_symbol = _parse_palette_table(root)
    symbol_to_relpath = _parse_pal_symbol_to_path(root)

    # Build tag → abs .pal path
    tag_to_pal: Dict[str, str] = {}
    for tag, sym in tag_to_symbol.items():
        rel = symbol_to_relpath.get(sym, "")
        if rel:
            # Convert .gbapal path to .pal path
            pal_rel = rel.replace(".gbapal", ".pal")
            abs_path = os.path.join(root, pal_rel)
            if os.path.isfile(abs_path):
                tag_to_pal[tag] = abs_path
            else:
                # Fall back to .gbapal if .pal doesn't exist
                abs_gba = os.path.join(root, rel)
                if os.path.isfile(abs_gba):
                    tag_to_pal[tag] = abs_gba

    # 2. GFX const → info name, and info → palette tag + images
    gfx_to_info = _parse_pic_tables(root)
    info_data = _parse_graphics_info(root)

    # 3. INCBIN chain: sPicTable_* → gObjectEventPic_* → file path
    pic_sym_to_path = _parse_pic_symbol_to_path(root)
    pic_table_to_sym = _parse_pic_table_to_symbol(root)

    # 4. Filesystem slug fallback
    slug_to_png = _find_sprite_pngs(root)

    # 5. Build sprite entries using the full INCBIN chain (with slug fallback)
    all_sprites: Dict[str, SpriteEntry] = {}
    for gfx_const, info_name in gfx_to_info.items():
        png_info = _resolve_sprite_png(
            info_name, info_data, pic_table_to_sym,
            pic_sym_to_path, slug_to_png, root,
        )
        if not png_info:
            continue
        png_path, category = png_info
        info = info_data.get(info_name, {})
        entry = SpriteEntry(
            gfx_const=gfx_const,
            info_name=info_name,
            png_path=png_path,
            category=category,
            palette_tag=info.get("paletteTag", "OBJ_EVENT_PAL_TAG_NONE"),
            width=info.get("width", 16),
            height=info.get("height", 32),
            inanimate=info.get("inanimate", False),
            anim_table=info.get("anims", "sAnimTable_Standard"),
        )
        all_sprites[gfx_const] = entry

    # 5. Build palette pools and assign sprites
    pools_by_tag: Dict[str, PalettePool] = {}
    for tag, path in tag_to_pal.items():
        if "REFLECTION" in tag or tag == "OBJ_EVENT_PAL_TAG_NONE":
            continue  # Skip reflection palettes and NONE
        pools_by_tag[tag] = PalettePool(tag, path)

    for entry in all_sprites.values():
        tag = entry.palette_tag
        if tag in pools_by_tag:
            pools_by_tag[tag].sprites.append(entry)
        elif tag != "OBJ_EVENT_PAL_TAG_NONE":
            # Palette tag not in the table (might be a special one) —
            # create a pool for it if we can find the file
            if tag in tag_to_pal:
                pool = PalettePool(tag, tag_to_pal[tag])
                pool.sprites.append(entry)
                pools_by_tag[tag] = pool

    pools = sorted(pools_by_tag.values(), key=lambda p: p.display_name)
    return pools, all_sprites


# ── Reskin helper (reuse from graphics_tab_widget) ──────────────────────────

def _reskin_overworld(png_path: str, palette: List[Color]) -> Optional[QPixmap]:
    """Recolour an overworld sprite sheet using a 16-colour palette."""
    try:
        img = QImage(png_path)
        if img.isNull():
            return None
        if img.format() != QImage.Format.Format_Indexed8:
            img = img.convertToFormat(QImage.Format.Format_Indexed8)
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


# ── Animation widget — 4-direction walk preview ────────────────────────────

class _DirectionAnimator(QLabel):
    """Single-direction walk cycle animator for one overworld sprite."""

    _SCALE = 3
    _FRAME_MS = 150  # ~GBA speed (8 ticks @ 60fps ≈ 133ms)

    def __init__(self, direction: str, parent=None):
        super().__init__(parent)
        self.direction = direction
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(64, 96)
        self.setStyleSheet("background: #111; border: 1px solid #333;")
        self._frames: list[QPixmap] = []
        self._idx = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def set_frames(self, frames: list[QPixmap]) -> None:
        self._timer.stop()
        self._frames = frames
        self._idx = 0
        if frames:
            self.setPixmap(frames[0])
            if len(frames) > 1:
                self._timer.start(self._FRAME_MS)
        else:
            self.clear()

    def _tick(self):
        if not self._frames:
            return
        self._idx = (self._idx + 1) % len(self._frames)
        self.setPixmap(self._frames[self._idx])

    def stop(self):
        self._timer.stop()


class FourDirectionPreview(QWidget):
    """Shows animated previews for overworld sprites.

    Adapts its display based on the sprite's animation type:
      - Walk (Standard/RedGreenNormal): 4-direction walk cycles
      - Surf: static directional poses
      - Fish: rod cast → hold → reel sequence
      - Inanimate: single static frame
      - CutTree/RockSmashRock: destruction sequence
      - Bike/VSSeeker: same as walk but with wider frames
    """

    _SCALE = 3

    # Maps animation table names → display categories
    _ANIM_TYPE_WALK = {"sAnimTable_Standard", "sAnimTable_RedGreenNormal",
                       "sAnimTable_AcroBike", "sAnimTable_Nurse",
                       "sAnimTable_HoOh"}
    _ANIM_TYPE_VSSEEKER = {"sAnimTable_RedGreenVSSeeker",
                           "sAnimTable_RedGreenVSSeekerBike"}
    _ANIM_TYPE_SURF = {"sAnimTable_RedGreenSurf"}
    _ANIM_TYPE_FISH = {"sAnimTable_RedGreenFish"}
    _ANIM_TYPE_FIELD_MOVE = {"sAnimTable_RedGreenFieldMove"}
    _ANIM_TYPE_DESTROY = {"sAnimTable_CutTree", "sAnimTable_RockSmashRock"}
    _ANIM_TYPE_INANIMATE = {"sAnimTable_Inanimate"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._outer.setSpacing(4)

        self._type_label = QLabel("")
        self._type_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._type_label.setStyleSheet("color: #888; font-size: 10px;")
        self._outer.addWidget(self._type_label)

        self._dir_row = QHBoxLayout()
        self._dir_row.setContentsMargins(0, 0, 0, 0)
        self._dir_row.setSpacing(8)

        self._dirs: list[_DirectionAnimator] = []
        self._dir_labels: list[QLabel] = []
        self._dir_cols: list[QVBoxLayout] = []
        for label in ("Down", "Left", "Up", "Right"):
            col = QVBoxLayout()
            col.setSpacing(2)
            anim = _DirectionAnimator(label)
            lbl = QLabel(label)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("color: #888; font-size: 10px;")
            col.addWidget(anim, 0, Qt.AlignmentFlag.AlignHCenter)
            col.addWidget(lbl, 0, Qt.AlignmentFlag.AlignHCenter)
            self._dir_row.addLayout(col)
            self._dirs.append(anim)
            self._dir_labels.append(lbl)
            self._dir_cols.append(col)

        self._outer.addLayout(self._dir_row)

        # Second row — only shown for surf (run cycle)
        self._row2_label = QLabel("Surf Run")
        self._row2_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._row2_label.setStyleSheet("color: #888; font-size: 10px;")
        self._row2_label.setVisible(False)
        self._outer.addWidget(self._row2_label)

        self._dir_row2 = QHBoxLayout()
        self._dir_row2.setContentsMargins(0, 0, 0, 0)
        self._dir_row2.setSpacing(8)

        self._dirs2: list[_DirectionAnimator] = []
        self._dir_labels2: list[QLabel] = []
        for label in ("Down", "Left", "Up", "Right"):
            col = QVBoxLayout()
            col.setSpacing(2)
            anim = _DirectionAnimator(label)
            anim.setVisible(False)
            lbl = QLabel(label)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("color: #888; font-size: 10px;")
            lbl.setVisible(False)
            col.addWidget(anim, 0, Qt.AlignmentFlag.AlignHCenter)
            col.addWidget(lbl, 0, Qt.AlignmentFlag.AlignHCenter)
            self._dir_row2.addLayout(col)
            self._dirs2.append(anim)
            self._dir_labels2.append(lbl)

        self._outer.addLayout(self._dir_row2)

    def _show_row2(self, visible: bool) -> None:
        """Show or hide the second animation row."""
        self._row2_label.setVisible(visible)
        for d in self._dirs2:
            d.setVisible(visible)
            if not visible:
                d.stop()
                d.set_frames([])
        for lbl in self._dir_labels2:
            lbl.setVisible(visible)

    @staticmethod
    def anim_type_label(anim_table: str) -> str:
        """Human-readable animation type for display."""
        labels = {
            "sAnimTable_Standard": "Walk Cycle",
            "sAnimTable_RedGreenNormal": "Walk Cycle",
            "sAnimTable_AcroBike": "Bike Cycle",
            "sAnimTable_Inanimate": "Static (Inanimate)",
            "sAnimTable_RedGreenSurf": "Surf",
            "sAnimTable_RedGreenFish": "Fishing Animation",
            "sAnimTable_RedGreenFieldMove": "Field Move",
            "sAnimTable_RedGreenVSSeeker": "VS Seeker Walk",
            "sAnimTable_RedGreenVSSeekerBike": "VS Seeker Bike",
            "sAnimTable_CutTree": "Destruction Sequence",
            "sAnimTable_RockSmashRock": "Destruction Sequence",
            "sAnimTable_Nurse": "Walk Cycle (Nurse)",
            "sAnimTable_HoOh": "Flying Animation",
        }
        return labels.get(anim_table, "Animation")

    def load_sprite(self, png_path: str, palette: Optional[List[Color]] = None,
                    frame_w: int = 0, frame_h: int = 0,
                    anim_table: str = "sAnimTable_Standard") -> None:
        """Extract animation frames from a sprite sheet based on its type.

        Parameters:
          png_path:    path to sprite sheet PNG
          palette:     16-colour palette to apply (or None)
          frame_w/h:   frame dimensions from GraphicsInfo
          anim_table:  animation table name from GraphicsInfo
        """
        for d in self._dirs:
            d.stop()
            d.set_frames([])
        self._show_row2(False)

        self._type_label.setText(self.anim_type_label(anim_table))

        if not png_path or not os.path.isfile(png_path):
            return

        img = QImage(png_path)
        if img.isNull():
            return

        # Apply palette if provided
        if palette:
            if img.format() != QImage.Format.Format_Indexed8:
                img = img.convertToFormat(QImage.Format.Format_Indexed8)
            ct = list(img.colorTable())
            for i, (r, g, b) in enumerate(palette[:16]):
                if i >= len(ct):
                    ct.append((0xFF << 24) | (r << 16) | (g << 8) | b)
                else:
                    alpha = ct[i] & 0xFF000000
                    ct[i] = alpha | (r << 16) | (g << 8) | b
            img.setColorTable(ct)

        # Make background transparent (palette index 0 = top-left pixel)
        img = img.convertToFormat(QImage.Format.Format_ARGB32)
        bg_rgb = img.pixel(0, 0) & 0x00FFFFFF
        for iy in range(img.height()):
            for ix in range(img.width()):
                if (img.pixel(ix, iy) & 0x00FFFFFF) == bg_rgb:
                    img.setPixel(ix, iy, 0x00000000)

        h = img.height()
        sheet_w = img.width()
        s = self._SCALE

        # Use provided frame dimensions if available
        if frame_w > 0 and frame_h > 0:
            w = frame_w
        elif sheet_w == h:
            w = sheet_w
        elif sheet_w % h == 0 and h >= 16:
            w = h
        else:
            w = 16

        total = sheet_w // w if w > 0 else 1

        def extract(idx, mirror=False):
            if idx >= total:
                idx = 0
            frame = img.copy(idx * w, 0, w, h)
            if mirror:
                frame = frame.mirrored(True, False)
            scaled = frame.scaled(
                w * s, h * s,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
            return QPixmap.fromImage(scaled)

        def walk_cycle(stand, step1, step2):
            return [stand, step1, stand, step2]

        # Reset direction labels to defaults
        for i, name in enumerate(("Down", "Left", "Up", "Right")):
            self._dir_labels[i].setText(name)

        # ── Dispatch by animation type ──────────────────────────────────

        if anim_table in self._ANIM_TYPE_INANIMATE:
            # Static object — show single frame in first slot only
            self._dirs[0].set_frames([extract(0)])
            for d in self._dirs[1:]:
                d.set_frames([])
            self._dir_labels[0].setText("Static")
            for lbl in self._dir_labels[1:]:
                lbl.setText("")

        elif anim_table in self._ANIM_TYPE_DESTROY:
            # Destruction sequence — play all frames in first slot
            frames = [extract(i) for i in range(total)]
            self._dirs[0].set_frames(frames)
            for d in self._dirs[1:]:
                d.set_frames([])
            self._dir_labels[0].setText("Destroy")
            for lbl in self._dir_labels[1:]:
                lbl.setText("")

        elif anim_table in self._ANIM_TYPE_SURF:
            # Surf — Row 1: static directional poses
            #         Row 2: run cycle (if sheet has run frames)
            # Sheet layout (14 frames at 16×32 for standard surf):
            #   0=S stand, 1=N stand, 2=W stand (E = W mirrored)
            #   3=S run1, 4=S run2, 5=N run1, 6=N run2
            #   7=W run1, 8=W run2 (E run = W mirrored)
            self._dir_labels[0].setText("Down")
            self._dir_labels[1].setText("Left")
            self._dir_labels[2].setText("Up")
            self._dir_labels[3].setText("Right")

            # Row 1: static poses (always shown)
            if total >= 3:
                self._dirs[0].set_frames([extract(0)])                # South
                self._dirs[1].set_frames([extract(2)])                # West
                self._dirs[2].set_frames([extract(1)])                # North
                self._dirs[3].set_frames([extract(2, True)])          # East
            else:
                for d in self._dirs:
                    d.set_frames([extract(0)])

            # Row 2: run cycle (only if sheet has enough frames)
            # Frames 3-5: Down run (stand, step1, step2)
            # Frames 6-8: Up run (stand, step1, step2)
            # Frames 9-11: Left run (stand, step1, step2)
            # Right = Left mirrored
            if total >= 12:
                self._show_row2(True)
                self._row2_label.setText("Surf Run")
                self._dirs2[0].set_frames(walk_cycle(
                    extract(3), extract(4), extract(5)))              # South
                self._dirs2[1].set_frames(walk_cycle(
                    extract(9), extract(10), extract(11)))            # West
                self._dirs2[2].set_frames(walk_cycle(
                    extract(6), extract(7), extract(8)))              # North
                self._dirs2[3].set_frames(walk_cycle(
                    extract(9, True), extract(10, True),
                    extract(11, True)))                               # East

        elif anim_table in self._ANIM_TYPE_FISH:
            # Fishing — 4-directional rod animation
            # Sheet layout (12 frames at frame_w × frame_h):
            #   West: 0,1,2,3  |  North: 4,5,6,7  |  South: 8,9,10,11
            #   East = West mirrored
            if total >= 12:
                self._dirs[0].set_frames(
                    [extract(8), extract(9), extract(10), extract(11)])   # South
                self._dirs[1].set_frames(
                    [extract(0), extract(1), extract(2), extract(3)])     # West
                self._dirs[2].set_frames(
                    [extract(4), extract(5), extract(6), extract(7)])     # North
                self._dirs[3].set_frames(
                    [extract(0, True), extract(1, True),
                     extract(2, True), extract(3, True)])                 # East
            elif total >= 9:
                self._load_walk(extract, walk_cycle, total)
            else:
                for d in self._dirs:
                    d.set_frames([extract(0)])

        elif anim_table in self._ANIM_TYPE_FIELD_MOVE:
            # Field move — same as walk cycle (player holding arm out)
            self._load_walk(extract, walk_cycle, total)

        elif anim_table in self._ANIM_TYPE_VSSEEKER:
            # VS Seeker — single raise animation sequence
            # sAnim_VSSeeker uses frames: 0→1→5→6 (pulse), 7→8 (hold), 6→1→0 (lower)
            # sAnim_VSSeekerBike similar pattern on 32×32 sheet
            self._dir_labels[0].setText("Raise")
            self._dir_labels[1].setText("")
            self._dir_labels[2].setText("")
            self._dir_labels[3].setText("")
            if total >= 9:
                seq = [extract(0), extract(1), extract(5), extract(6),
                       extract(7), extract(8), extract(6), extract(1), extract(0)]
                self._dirs[0].set_frames(seq)
            else:
                self._dirs[0].set_frames([extract(i) for i in range(total)])
            for d in self._dirs[1:]:
                d.set_frames([])

        else:
            # Standard walk / bike / VS Seeker / nurse / HoOh / anything else
            self._load_walk(extract, walk_cycle, total)

    def _load_walk(self, extract, walk_cycle, total):
        """Standard 4-direction walk cycle from a 9-frame sheet."""
        if total >= 9:
            self._dirs[0].set_frames(walk_cycle(
                extract(0), extract(3), extract(4)))  # Down
            self._dirs[1].set_frames(walk_cycle(
                extract(2), extract(7), extract(8)))  # Left
            self._dirs[2].set_frames(walk_cycle(
                extract(1), extract(5), extract(6)))  # Up
            self._dirs[3].set_frames(walk_cycle(
                extract(2, mirror=True),
                extract(7, mirror=True),
                extract(8, mirror=True)))             # Right (mirrored)
        elif total >= 3:
            self._dirs[0].set_frames([extract(0)])         # Down
            self._dirs[1].set_frames([extract(2)])         # Left
            self._dirs[2].set_frames([extract(1)])         # Up
            self._dirs[3].set_frames([extract(2, True)])   # Right
        else:
            frame = extract(0)
            for d in self._dirs:
                d.set_frames([frame])

    def stop(self):
        for d in self._dirs:
            d.stop()
        for d in self._dirs2:
            d.stop()


# ═════════════════════════════════════════════════════════════════════════════
# Main widget
# ═════════════════════════════════════════════════════════════════════════════

# ── New Sprite Dialog ─────────────────────────────────────────────────────

class NewSpriteDialog(QDialog):
    """Dialog for adding a new overworld sprite to the project."""

    def __init__(self, project_root: str, dowp_enabled: bool,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Add New Overworld Sprite")
        self.setMinimumWidth(520)
        self._project_root = project_root
        self._dowp_enabled = dowp_enabled
        self._png_path = ""
        self._palette_colors: Optional[List[Color]] = None

        layout = QVBoxLayout(self)

        form = QFormLayout()
        form.setSpacing(8)

        # PNG file chooser
        png_row = QHBoxLayout()
        self._png_label = QLineEdit()
        self._png_label.setReadOnly(True)
        self._png_label.setPlaceholderText("Select a sprite sheet PNG…")
        png_btn = QPushButton("Browse…")
        png_btn.clicked.connect(self._browse_png)
        png_row.addWidget(self._png_label, 1)
        png_row.addWidget(png_btn)
        form.addRow("Sprite Sheet:", png_row)

        # PNG preview
        self._preview_lbl = QLabel()
        self._preview_lbl.setFixedHeight(80)
        self._preview_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_lbl.setStyleSheet("background: #111; border: 1px solid #333;")
        form.addRow("Preview:", self._preview_lbl)

        # Name
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("e.g. my_npc (lowercase, underscores)")
        form.addRow("Sprite Name:", self._name_edit)

        # Frame dimensions
        dim_row = QHBoxLayout()
        self._frame_w = QSpinBox()
        self._frame_w.setRange(8, 128)
        self._frame_w.setValue(16)
        self._frame_w.setSingleStep(8)
        self._frame_w.setSuffix("px")
        dim_row.addWidget(QLabel("Width:"))
        dim_row.addWidget(self._frame_w)
        self._frame_h = QSpinBox()
        self._frame_h.setRange(8, 128)
        self._frame_h.setValue(32)
        self._frame_h.setSingleStep(8)
        self._frame_h.setSuffix("px")
        dim_row.addWidget(QLabel("Height:"))
        dim_row.addWidget(self._frame_h)
        self._frame_count_lbl = QLabel("")
        self._frame_count_lbl.setStyleSheet("color: #888;")
        dim_row.addWidget(self._frame_count_lbl)
        form.addRow("Frame Size:", dim_row)

        # Animation type
        self._anim_combo = QComboBox()
        self._anim_combo.wheelEvent = lambda e: e.ignore()
        from core.overworld_sprite_creator import ANIM_TABLE_CHOICES
        for value, label in ANIM_TABLE_CHOICES:
            self._anim_combo.addItem(label, value)
        form.addRow("Animation:", self._anim_combo)

        # Category
        self._cat_combo = QComboBox()
        self._cat_combo.wheelEvent = lambda e: e.ignore()
        self._cat_combo.addItem("People / NPCs", "people")
        self._cat_combo.addItem("Pokemon", "pokemon")
        self._cat_combo.addItem("Objects / Misc", "misc")
        form.addRow("Category:", self._cat_combo)

        # Palette choice
        self._pal_combo = QComboBox()
        self._pal_combo.wheelEvent = lambda e: e.ignore()
        if dowp_enabled:
            self._pal_combo.addItem("Create new palette from PNG", "NEW")
        from core.overworld_sprite_creator import NPC_PALETTE_SLOTS
        for tag, slot, name in NPC_PALETTE_SLOTS:
            self._pal_combo.addItem(f"{name} ({tag})", tag)
        form.addRow("Palette:", self._pal_combo)

        layout.addLayout(form)

        # Info label
        self._info_lbl = QLabel("")
        self._info_lbl.setStyleSheet("color: #aaa; font-size: 10px;")
        self._info_lbl.setWordWrap(True)
        layout.addWidget(self._info_lbl)

        # Buttons
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._validate_and_accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        # Wire frame size changes to update count
        self._frame_w.valueChanged.connect(self._update_frame_count)
        self._frame_h.valueChanged.connect(self._update_frame_count)

    def _browse_png(self) -> None:
        start = os.path.join(
            self._project_root, "graphics", "object_events", "pics"
        ) if self._project_root else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Sprite Sheet PNG", start, "PNG Images (*.png)"
        )
        if not path:
            return

        self._png_path = path
        self._png_label.setText(os.path.basename(path))

        # Auto-detect name from filename
        slug = os.path.splitext(os.path.basename(path))[0].lower()
        slug = re.sub(r"[^a-z0-9_]", "_", slug)
        self._name_edit.setText(slug)

        # Show preview
        pix = QPixmap(path)
        if not pix.isNull():
            scaled = pix.scaled(
                self._preview_lbl.width(), self._preview_lbl.height(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
            self._preview_lbl.setPixmap(scaled)

            # Auto-detect frame height from PNG
            if pix.height() in (16, 32, 64):
                self._frame_h.setValue(pix.height())

            # Try to detect frame width
            h = pix.height()
            w = pix.width()
            # Common: 9 frames for walk, 1 frame for inanimate
            for candidate_w in (16, 32, 64):
                if w % candidate_w == 0 and candidate_w <= w:
                    count = w // candidate_w
                    if count in (1, 3, 4, 9, 12, 14):
                        self._frame_w.setValue(candidate_w)
                        break

            # Extract palette from PNG
            img = QImage(path)
            if not img.isNull() and img.format() == QImage.Format.Format_Indexed8:
                ct = img.colorTable()
                self._palette_colors = []
                for entry in ct[:16]:
                    r = (entry >> 16) & 0xFF
                    g = (entry >> 8) & 0xFF
                    b = entry & 0xFF
                    self._palette_colors.append(clamp_to_gba(r, g, b))
                while len(self._palette_colors) < 16:
                    self._palette_colors.append((0, 0, 0))

        self._update_frame_count()

    def _update_frame_count(self) -> None:
        if not self._png_path:
            return
        img = QImage(self._png_path)
        if img.isNull():
            return
        fw = self._frame_w.value()
        count = img.width() // fw if fw > 0 else 0
        self._frame_count_lbl.setText(f"({count} frames)")

    def _validate_and_accept(self) -> None:
        if not self._png_path:
            QMessageBox.warning(self, "Missing PNG", "Please select a sprite sheet PNG.")
            return

        name = self._name_edit.text().strip()
        if not name or not re.match(r"^[a-z][a-z0-9_]*$", name):
            QMessageBox.warning(
                self, "Invalid Name",
                "Sprite name must be lowercase letters, numbers, and underscores.\n"
                "Must start with a letter."
            )
            return

        fw = self._frame_w.value()
        fh = self._frame_h.value()
        if fw % 8 != 0 or fh % 8 != 0:
            QMessageBox.warning(
                self, "Invalid Frame Size",
                "Frame width and height must be multiples of 8."
            )
            return

        img = QImage(self._png_path)
        if img.isNull() or img.width() < fw:
            QMessageBox.warning(self, "Invalid PNG", "PNG is smaller than one frame.")
            return

        pal_data = self._pal_combo.currentData()
        if pal_data == "NEW" and not self._palette_colors:
            QMessageBox.warning(
                self, "No Palette",
                "The PNG must be an indexed-color image (8-bit, 16 colors)\n"
                "to create a new palette from it."
            )
            return

        self.accept()

    # ── Public accessors for the parent to read after accept() ──────────

    @property
    def png_path(self) -> str:
        return self._png_path

    @property
    def sprite_name(self) -> str:
        return self._name_edit.text().strip()

    @property
    def frame_width(self) -> int:
        return self._frame_w.value()

    @property
    def frame_height(self) -> int:
        return self._frame_h.value()

    @property
    def anim_table(self) -> str:
        return self._anim_combo.currentData()

    @property
    def category(self) -> str:
        return self._cat_combo.currentData()

    @property
    def palette_choice(self) -> str:
        return self._pal_combo.currentData()

    @property
    def palette_colors(self) -> Optional[List[Color]]:
        return self._palette_colors


CATEGORY_FILTERS = [
    ("all", "All"),
    ("people", "Players & NPCs"),
    ("pokemon", "Pokemon"),
    ("misc", "Objects & Items"),
]


class OverworldGraphicsTab(QWidget):
    """Sprite-first overworld graphics editor.

    Layout:
      LEFT:   Category filter + search → scrollable sprite list with thumbnails
              DOWP enable button at bottom
      RIGHT:  Top — sprite sheet + animation preview
              Bottom — palette editor for the selected sprite's palette
                       (shared palettes update all sprites using them)
    """

    modified = pyqtSignal()
    gfx_constants_changed = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._project_root: str = ""
        self._pools: List[PalettePool] = []
        self._all_sprites: Dict[str, SpriteEntry] = {}
        self._sorted_sprites: List[SpriteEntry] = []
        self._current_sprite: Optional[SpriteEntry] = None
        self._loading = False

        # In-memory palette cache: {tag_name: [16 Color tuples]}
        self._palettes: Dict[str, List[Color]] = {}
        self._palette_dirty: set[str] = set()
        # Map tag → .pal file path for saving
        self._pal_paths: Dict[str, str] = {}
        # Reverse lookup: tag → PalettePool
        self._pools_by_tag: Dict[str, PalettePool] = {}

        # Debounce timer for sprite list refresh during rapid palette edits
        self._list_refresh_timer = QTimer(self)
        self._list_refresh_timer.setSingleShot(True)
        self._list_refresh_timer.setInterval(400)
        self._list_refresh_timer.timeout.connect(self._refresh_visible_thumbnails)

        self._build_ui()

    def _build_ui(self) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(2)
        splitter.setStyleSheet("QSplitter::handle { background: #2e2e2e; }")

        # ── LEFT PANEL — Sprite browser ─────────────────────────────────
        left = QWidget()
        left.setMinimumWidth(260)
        left.setMaximumWidth(420)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(8, 8, 8, 8)
        lv.setSpacing(6)

        # Category filter
        cat_row = QHBoxLayout()
        cat_row.setSpacing(6)
        cat_row.addWidget(QLabel("Category:"))
        self._cat_combo = QComboBox()
        for key, label in CATEGORY_FILTERS:
            self._cat_combo.addItem(label, key)
        self._cat_combo.wheelEvent = lambda e: e.ignore()
        cat_row.addWidget(self._cat_combo, 1)
        lv.addLayout(cat_row)

        # Search
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search sprites…")
        lv.addWidget(self._search)

        # Sprite count
        self._sprite_count_lbl = QLabel("")
        self._sprite_count_lbl.setStyleSheet("color: #888; font-size: 10px;")
        lv.addWidget(self._sprite_count_lbl)

        # Sprite grid (scrollable thumbnail grid)
        self._grid_scroll = QScrollArea()
        self._grid_scroll.setWidgetResizable(True)
        self._grid_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._grid_scroll.setStyleSheet("background: #1a1a1a;")
        self._grid_container = QWidget()
        self._grid_layout = QGridLayout(self._grid_container)
        self._grid_layout.setContentsMargins(4, 4, 4, 4)
        self._grid_layout.setSpacing(6)
        self._grid_scroll.setWidget(self._grid_container)
        lv.addWidget(self._grid_scroll, 1)

        # Add New Sprite button
        self._add_sprite_btn = QPushButton("+ Add New Sprite…")
        self._add_sprite_btn.setStyleSheet(
            "QPushButton { background: #2a3d5a; color: #8bf; border: 1px solid #4a6a9a; "
            "padding: 6px; border-radius: 3px; font-weight: bold; }"
            "QPushButton:hover { background: #3a5d7a; }"
        )
        self._add_sprite_btn.setToolTip(
            "Add a new overworld sprite to the project.\n"
            "Import a PNG sprite sheet and configure its properties."
        )
        self._add_sprite_btn.clicked.connect(self._add_new_sprite)
        lv.addWidget(self._add_sprite_btn)

        # Dynamic Overworld Palettes button
        self._dowp_btn = QPushButton("Enable Dynamic Palettes…")
        self._dowp_btn.setToolTip(
            "Apply the Dynamic Overworld Palettes patch to this project.\n"
            "Allows every overworld sprite to have its own unique palette\n"
            "instead of being locked to 4 shared NPC palette slots.\n\n"
            "This is a ONE-WAY change — it modifies C source files."
        )
        self._dowp_btn.setStyleSheet(
            "QPushButton { background: #2a4d2a; color: #8f8; border: 1px solid #4a8a4a; "
            "padding: 6px; border-radius: 3px; font-weight: bold; }"
            "QPushButton:hover { background: #3a6d3a; }"
            "QPushButton:disabled { background: #333; color: #888; border-color: #555; }"
        )
        self._dowp_btn.clicked.connect(self._enable_dynamic_palettes)
        lv.addWidget(self._dowp_btn)

        self._dowp_status = QLabel("")
        self._dowp_status.setStyleSheet("color: #888; font-size: 10px;")
        self._dowp_status.setWordWrap(True)
        lv.addWidget(self._dowp_status)

        splitter.addWidget(left)

        # ── RIGHT PANEL — Detail + palette ──────────────────────────────
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(8, 8, 8, 8)
        rv.setSpacing(8)

        # Detail area: sprite sheet + animation (top half)
        detail_splitter = QSplitter(Qt.Orientation.Horizontal)
        detail_splitter.setHandleWidth(2)

        # Large sheet view
        sheet_group = QGroupBox("Sprite Sheet")
        sg = QVBoxLayout(sheet_group)
        sg.setContentsMargins(8, 14, 8, 8)
        self._sheet_lbl = QLabel()
        self._sheet_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sheet_lbl.setMinimumHeight(100)
        self._sheet_lbl.setStyleSheet("background: #111; border: 1px solid #333;")
        self._sheet_lbl.setText("Select a sprite")
        sg.addWidget(self._sheet_lbl)

        self._sheet_info_lbl = QLabel("")
        self._sheet_info_lbl.setStyleSheet("color: #888; font-size: 11px;")
        self._sheet_info_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sheet_info_lbl.setWordWrap(True)
        sg.addWidget(self._sheet_info_lbl)

        sprite_btn_row = QHBoxLayout()
        sprite_btn_row.setSpacing(6)
        self._open_sprite_folder_btn = QPushButton("Show in Folder")
        self._open_sprite_folder_btn.setToolTip(
            "Open the folder containing this sprite's PNG\n"
            "with the file selected in Explorer."
        )
        sprite_btn_row.addWidget(self._open_sprite_folder_btn)
        sprite_btn_row.addStretch(1)
        sg.addLayout(sprite_btn_row)
        detail_splitter.addWidget(sheet_group)

        # Animation preview
        anim_group = QGroupBox("Animation Preview")
        ag = QVBoxLayout(anim_group)
        ag.setContentsMargins(8, 14, 8, 8)
        self._four_dir = FourDirectionPreview()
        ag.addWidget(self._four_dir)
        ag.addStretch(1)
        detail_splitter.addWidget(anim_group)

        detail_splitter.setSizes([400, 350])
        rv.addWidget(detail_splitter, 1)

        # Palette section (bottom half)
        pal_frame = QGroupBox("Palette")
        pf = QVBoxLayout(pal_frame)
        pf.setContentsMargins(8, 14, 8, 8)
        pf.setSpacing(6)

        self._pal_info_lbl = QLabel("Select a sprite to view its palette")
        self._pal_info_lbl.setStyleSheet("color: #aaa; font-size: 11px;")
        self._pal_info_lbl.setWordWrap(True)
        pf.addWidget(self._pal_info_lbl)

        # Palette slot reassignment
        pal_slot_row = QHBoxLayout()
        pal_slot_row.setSpacing(6)
        pal_slot_row.addWidget(QLabel("Assign to:"))
        self._pal_assign_combo = QComboBox()
        self._pal_assign_combo.wheelEvent = lambda e: e.ignore()
        self._pal_assign_combo.setToolTip(
            "Change which palette this sprite uses.\n"
            "Without dynamic palettes: choose from 4 NPC slots.\n"
            "With dynamic palettes: each sprite can have its own."
        )
        pal_slot_row.addWidget(self._pal_assign_combo, 1)
        self._pal_assign_btn = QPushButton("Apply")
        self._pal_assign_btn.setToolTip("Reassign this sprite to the selected palette.")
        self._pal_assign_btn.clicked.connect(self._reassign_palette)
        pal_slot_row.addWidget(self._pal_assign_btn)
        pf.addLayout(pal_slot_row)

        self._pal_row = PaletteSwatchRow()
        pf.addWidget(self._pal_row)

        pal_btn_row = QHBoxLayout()
        pal_btn_row.setSpacing(6)
        self._import_btn = QPushButton("Import Palette from PNG…")
        self._import_btn.setToolTip(
            "Extract palette from an indexed PNG and apply it to\n"
            "this sprite's palette. If the palette is shared, all\n"
            "sprites using it will be affected."
        )
        self._open_folder_btn = QPushButton("Open Palettes Folder")
        self._open_folder_btn.setToolTip("Open the overworld palettes directory.")
        pal_btn_row.addWidget(self._import_btn)
        pal_btn_row.addWidget(self._open_folder_btn)
        pal_btn_row.addStretch(1)
        pf.addLayout(pal_btn_row)

        rv.addWidget(pal_frame, 0)

        splitter.addWidget(right)
        splitter.setSizes([340, 660])
        outer.addWidget(splitter)

        # ── Wire signals ────────────────────────────────────────────────
        self._pal_row.colors_changed.connect(self._on_palette_edited)
        self._import_btn.clicked.connect(self._import_palette_from_png)
        self._open_folder_btn.clicked.connect(self._open_palettes_folder)
        self._open_sprite_folder_btn.clicked.connect(self._open_current_sprite_folder)
        self._cat_combo.currentIndexChanged.connect(self._rebuild_grid)
        self._search.textChanged.connect(self._rebuild_grid)

    # ────────────────────────────────────────────────────────── loading ──
    def load(self, project_root: str) -> None:
        """Parse C headers and populate the sprite browser."""
        self._project_root = project_root
        self._pools, self._all_sprites = build_overworld_data(project_root)
        self._palettes.clear()
        self._palette_dirty.clear()
        self._pal_paths.clear()
        self._pools_by_tag.clear()
        self._current_sprite = None

        # Check DOWP status
        self._update_dowp_status()

        # Cache palette paths and pools
        for pool in self._pools:
            self._pal_paths[pool.tag_name] = pool.pal_path
            self._pools_by_tag[pool.tag_name] = pool

        # Build flat sorted sprite list
        self._sorted_sprites = sorted(
            self._all_sprites.values(), key=lambda s: s.display_name
        )

        # Build the grid
        self._rebuild_grid()

    def _get_palette_for_sprite(self, entry: SpriteEntry) -> Optional[List[Color]]:
        """Get the palette for a sprite, loading from disk/cache as needed."""
        tag = entry.palette_tag
        if tag not in self._palettes:
            pal_path = self._pal_paths.get(tag, "")
            if pal_path:
                colors = read_jasc_pal(pal_path)
            else:
                colors = []
            if not colors:
                colors = [(0, 0, 0)] * 16
            self._palettes[tag] = colors
        return self._palettes.get(tag)

    # ────────────────────────────────────────────────────────── handlers ──
    def _on_palette_edited(self) -> None:
        if self._loading or not self._current_sprite:
            return
        tag = self._current_sprite.palette_tag
        colors = self._pal_row.colors()
        self._palettes[tag] = colors
        self._palette_dirty.add(tag)
        # Broadcast so any future viewer of this palette tag sees the
        # edit live.  Overworld viewers aren't migrated yet but the
        # hook is in place.
        _get_palette_bus().set_overworld_palette(tag, colors)
        self.modified.emit()
        # Refresh the selected sprite detail immediately
        self._show_sprite_detail(self._current_sprite)
        # Defer full grid refresh for shared palette updates
        if not self._list_refresh_timer.isActive():
            self._list_refresh_timer.start(400)

    def _rebuild_grid(self) -> None:
        """Rebuild the sprite thumbnail grid with current filters."""
        # Clear existing grid
        while self._grid_layout.count():
            item = self._grid_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        cat_filter = self._cat_combo.currentData() or "all"
        search_text = self._search.text().strip().lower()

        sprites = self._sorted_sprites
        if cat_filter != "all":
            sprites = [s for s in sprites if s.category == cat_filter]
        if search_text:
            sprites = [
                s for s in sprites
                if search_text in s.display_name.lower()
                or search_text in s.gfx_const.lower()
                or search_text in s.palette_tag.lower()
            ]

        self._sprite_count_lbl.setText(f"{len(sprites)} sprites")

        col_count = max(1, (self._grid_scroll.viewport().width() - 20) // 80)
        row = 0
        col = 0
        for entry in sprites:
            palette = self._get_palette_for_sprite(entry)
            thumb = self._make_thumbnail(entry, palette)
            self._grid_layout.addWidget(thumb, row, col)
            col += 1
            if col >= col_count:
                col = 0
                row += 1

        # Add stretch at the end
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._grid_layout.addWidget(spacer, row + 1, 0, 1, col_count)

    def _refresh_visible_thumbnails(self) -> None:
        """Rebuild grid after a palette edit (debounced)."""
        self._rebuild_grid()

    def _make_thumbnail(self, entry: SpriteEntry,
                        palette: Optional[List[Color]]) -> QWidget:
        """Create a clickable sprite thumbnail for the grid."""
        container = QWidget()
        container.setFixedSize(76, 80)
        is_selected = (self._current_sprite and
                       entry.gfx_const == self._current_sprite.gfx_const)
        border_color = "#1565c0" if is_selected else "#333"
        container.setStyleSheet(
            f"QWidget {{ background: #222; border: 1px solid {border_color}; border-radius: 3px; }}"
            "QWidget:hover { border-color: #1565c0; }"
        )
        container.setCursor(Qt.CursorShape.PointingHandCursor)
        vl = QVBoxLayout(container)
        vl.setContentsMargins(2, 2, 2, 2)
        vl.setSpacing(1)

        # Sprite preview (first frame, scaled)
        lbl = QLabel()
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setFixedSize(68, 56)

        pix = None
        if palette and entry.png_path:
            pix = _reskin_overworld(entry.png_path, palette)
        if pix is None and entry.png_path and os.path.isfile(entry.png_path):
            pix = QPixmap(entry.png_path)

        if pix and not pix.isNull():
            fw = entry.width if isinstance(entry.width, int) else 16
            fh = entry.height if isinstance(entry.height, int) else 32
            if pix.width() > fw:
                frame = pix.copy(0, 0, fw, min(fh, pix.height()))
            else:
                frame = pix
            scaled = frame.scaled(
                lbl.width(), lbl.height(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
            lbl.setPixmap(scaled)

        vl.addWidget(lbl)

        # Name label
        name_lbl = QLabel(entry.display_name[:12])
        name_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name_lbl.setStyleSheet("color: #aaa; font-size: 9px; border: none;")
        vl.addWidget(name_lbl)

        container.mousePressEvent = lambda ev, e=entry: self._on_sprite_clicked(e)
        return container

    def _on_sprite_clicked(self, entry: SpriteEntry) -> None:
        self._current_sprite = entry
        self._show_sprite_detail(entry)
        self._load_sprite_palette(entry)
        # Rebuild grid to update selection highlight
        self._rebuild_grid()

    def _load_sprite_palette(self, entry: SpriteEntry) -> None:
        """Load the selected sprite's palette into the swatch editor."""
        tag = entry.palette_tag
        palette = self._get_palette_for_sprite(entry)

        # Show palette info
        pool = self._pools_by_tag.get(tag)
        if pool:
            shared_count = len(pool.sprites)
            pal_name = pool.display_name
            if shared_count > 1:
                self._pal_info_lbl.setText(
                    f"{pal_name}  ({tag})\n"
                    f"Shared by {shared_count} sprites — editing affects all of them"
                )
            else:
                self._pal_info_lbl.setText(f"{pal_name}  ({tag})")
        else:
            self._pal_info_lbl.setText(f"{tag}")

        # Populate palette assignment combo
        self._pal_assign_combo.blockSignals(True)
        self._pal_assign_combo.clear()
        for p in self._pools:
            label = p.display_name
            self._pal_assign_combo.addItem(label, p.tag_name)
            if p.tag_name == tag:
                self._pal_assign_combo.setCurrentIndex(
                    self._pal_assign_combo.count() - 1
                )
        self._pal_assign_combo.blockSignals(False)

        self._loading = True
        try:
            if palette:
                self._pal_row.set_colors(palette)
        finally:
            self._loading = False

    def _show_sprite_detail(self, entry: SpriteEntry) -> None:
        """Show large sprite sheet + animation for the selected sprite."""
        palette = self._get_palette_for_sprite(entry)

        # Large sheet view
        pix = None
        if palette and entry.png_path:
            pix = _reskin_overworld(entry.png_path, palette)
        if pix is None and entry.png_path:
            pix = QPixmap(entry.png_path)

        if pix and not pix.isNull():
            scale = max(1, min(3, self._sheet_lbl.width() // max(1, pix.width())))
            scaled = pix.scaled(
                pix.width() * scale, pix.height() * scale,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
            self._sheet_lbl.setPixmap(scaled)
        else:
            self._sheet_lbl.clear()
            self._sheet_lbl.setText("No sprite")

        w = entry.width if isinstance(entry.width, int) else "?"
        h = entry.height if isinstance(entry.height, int) else "?"
        anim_type = FourDirectionPreview.anim_type_label(
            getattr(entry, "anim_table", "sAnimTable_Standard")
        )
        self._sheet_info_lbl.setText(
            f"{entry.display_name}  —  {entry.gfx_const}\n"
            f"Frame: {w}×{h}px  |  Palette: {entry.palette_tag}  |  {anim_type}"
        )

        # Animation preview
        fw = entry.width if isinstance(entry.width, int) else 0
        fh = entry.height if isinstance(entry.height, int) else 0
        anim = getattr(entry, "anim_table", "sAnimTable_Standard")
        self._four_dir.load_sprite(entry.png_path, palette, fw, fh, anim)

    def _import_palette_from_png(self) -> None:
        if not self._current_sprite:
            QMessageBox.information(
                self, "No Sprite Selected",
                "Select a sprite first, then import a palette for it.",
            )
            return

        tag = self._current_sprite.palette_tag
        pool = self._pools_by_tag.get(tag)

        # Warn if shared palette
        if pool and len(pool.sprites) > 1:
            ret = QMessageBox.question(
                self, "Shared Palette",
                f"This sprite's palette ({pool.display_name}) is shared by\n"
                f"{len(pool.sprites)} sprites. Importing will change all of them.\n\n"
                "Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ret != QMessageBox.StandardButton.Yes:
                return

        start_dir = ""
        if self._current_sprite.png_path:
            candidate = os.path.dirname(self._current_sprite.png_path)
            if os.path.isdir(candidate):
                start_dir = candidate
        if not start_dir:
            start_dir = os.path.join(
                self._project_root, "graphics", "object_events", "palettes"
            ) if self._project_root else ""
        if not os.path.isdir(start_dir):
            start_dir = self._project_root or ""

        path, _ = QFileDialog.getOpenFileName(
            self, "Select Indexed PNG", start_dir, "PNG Images (*.png)",
        )
        if not path:
            return

        img = QImage(path)
        if img.isNull():
            QMessageBox.warning(self, "Import Failed", f"Could not load:\n{path}")
            return
        if img.format() != QImage.Format.Format_Indexed8:
            QMessageBox.warning(
                self, "Not an Indexed PNG",
                "This PNG is not in indexed (palette) mode.\n\n"
                "Convert it to an indexed-colour PNG (8-bit, 16 colours)\n"
                "in your image editor first.",
            )
            return

        ct = img.colorTable()
        if len(ct) < 1:
            QMessageBox.warning(self, "Empty Palette", "No colour table entries.")
            return

        colors: List[Color] = []
        for c_entry in ct[:16]:
            r = (c_entry >> 16) & 0xFF
            g = (c_entry >> 8) & 0xFF
            b = c_entry & 0xFF
            colors.append(clamp_to_gba(r, g, b))
        while len(colors) < 16:
            colors.append((0, 0, 0))

        self._palettes[tag] = colors
        self._palette_dirty.add(tag)
        _get_palette_bus().set_overworld_palette(tag, colors)

        self._loading = True
        try:
            self._pal_row.set_colors(colors)
        finally:
            self._loading = False

        self._show_sprite_detail(self._current_sprite)
        self._rebuild_grid()
        self.modified.emit()

        affected = len(pool.sprites) if pool else 1
        QMessageBox.information(
            self, "Palette Imported",
            f"Loaded {len(ct[:16])} colours from:\n"
            f"{os.path.basename(path)}\n\n"
            f"Applied to: {tag}\n"
            f"({affected} sprite(s) affected)\n\n"
            "Click File → Save to write the .pal file.",
        )

    def _open_palettes_folder(self) -> None:
        folder = os.path.join(
            self._project_root, "graphics", "object_events", "palettes"
        )
        if not os.path.isdir(folder):
            folder = os.path.join(self._project_root, "graphics", "object_events")
        self._open_folder(folder)

    def _open_current_sprite_folder(self) -> None:
        if self._current_sprite and self._current_sprite.png_path:
            filepath = self._current_sprite.png_path
            if os.path.isfile(filepath):
                self._open_file_selected(filepath)
            else:
                folder = os.path.dirname(filepath)
                self._open_folder(folder)

    def _open_file_selected(self, filepath: str) -> None:
        """Open the containing folder with the file highlighted/selected."""
        import subprocess
        try:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(filepath)])
        except Exception:
            self._open_folder(os.path.dirname(filepath))

    def _open_folder(self, folder: str) -> None:
        if os.path.isdir(folder):
            try:
                from ui.open_folder_util import open_folder
                open_folder(folder)
            except Exception:
                try:
                    os.startfile(folder)  # type: ignore[attr-defined]
                except Exception:
                    pass

    # ──────────────────────────────────────── palette reassignment ──
    def _reassign_palette(self) -> None:
        """Change the palette tag for the currently selected sprite in the C source."""
        if not self._current_sprite or not self._project_root:
            return

        new_tag = self._pal_assign_combo.currentData()
        old_tag = self._current_sprite.palette_tag
        if new_tag == old_tag:
            QMessageBox.information(
                self, "No Change",
                "This sprite already uses that palette."
            )
            return

        info_name = self._current_sprite.info_name
        ret = QMessageBox.question(
            self, "Reassign Palette?",
            f"Change {self._current_sprite.display_name}'s palette from\n"
            f"  {old_tag}\n"
            f"to\n"
            f"  {new_tag}\n\n"
            f"This modifies object_event_graphics_info.h.\n"
            f"Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return

        # Modify the C header
        gi_path = os.path.join(
            self._project_root, "src", "data", "object_events",
            "object_event_graphics_info.h"
        )
        try:
            text = open(gi_path, encoding="utf-8", errors="replace").read()
            # Find this sprite's GraphicsInfo block and replace paletteTag
            pattern = re.compile(
                r"(gObjectEventGraphicsInfo_" + re.escape(info_name)
                + r"\s*=\s*\{[^;]*?\.paletteTag\s*=\s*)"
                + r"(\w+)"
                + r"(,)",
                re.DOTALL,
            )
            new_text, count = pattern.subn(r"\g<1>" + new_tag + r"\3", text)
            if count == 0:
                QMessageBox.warning(
                    self, "Not Found",
                    f"Could not find paletteTag for {info_name} in the source."
                )
                return

            with open(gi_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(new_text)

            # Update in-memory data
            self._current_sprite.palette_tag = new_tag

            # Move sprite between pools
            old_pool = self._pools_by_tag.get(old_tag)
            new_pool = self._pools_by_tag.get(new_tag)
            if old_pool and self._current_sprite in old_pool.sprites:
                old_pool.sprites.remove(self._current_sprite)
            if new_pool:
                new_pool.sprites.append(self._current_sprite)

            # Reload palette and detail
            self._load_sprite_palette(self._current_sprite)
            self._show_sprite_detail(self._current_sprite)
            self._rebuild_grid()

            QMessageBox.information(
                self, "Palette Reassigned",
                f"{self._current_sprite.display_name} now uses {new_tag}.\n\n"
                f"Run 'make modern' to verify the project compiles."
            )
        except Exception as e:
            QMessageBox.critical(
                self, "Error", f"Failed to reassign palette:\n{e}"
            )

    # ────────────────────────────────────────────── add new sprite ──
    def _add_new_sprite(self) -> None:
        """Open the Add New Sprite dialog and create the sprite if confirmed."""
        if not self._project_root:
            QMessageBox.warning(self, "No Project", "No project is loaded.")
            return

        # Check DOWP status for palette options
        dowp = False
        try:
            from core.dynamic_ow_pal_patch import is_dowp_enabled
            dowp = is_dowp_enabled(self._project_root)
        except ImportError:
            pass

        dlg = NewSpriteDialog(self._project_root, dowp, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        # Determine palette settings
        pal_choice = dlg.palette_choice
        create_new = (pal_choice == "NEW")
        pal_tag = None if create_new else pal_choice
        pal_slot = None
        pal_colors = dlg.palette_colors if create_new else None

        if not create_new:
            # Look up slot for the chosen tag
            from core.overworld_sprite_creator import NPC_PALETTE_SLOTS
            for tag, slot, _name in NPC_PALETTE_SLOTS:
                if tag == pal_choice:
                    pal_slot = slot
                    break

        # Confirm before modifying files
        ret = QMessageBox.question(
            self, "Create New Sprite?",
            f"This will add a new overworld sprite to your project:\n\n"
            f"  Name: {dlg.sprite_name}\n"
            f"  Frame: {dlg.frame_width}x{dlg.frame_height}px\n"
            f"  Animation: {dlg.anim_table}\n"
            f"  Category: {dlg.category}\n"
            f"  Palette: {'New custom palette' if create_new else pal_choice}\n\n"
            f"Several C source/header files will be modified.\n"
            f"Make sure you have a backup or git commit first.\n\n"
            f"Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return

        gfx_const = f"OBJ_EVENT_GFX_{dlg.sprite_name.upper()}"

        try:
            from core.overworld_sprite_creator import create_overworld_sprite
            success, applied_list, error_list = create_overworld_sprite(
                root=self._project_root,
                png_source=dlg.png_path,
                sprite_slug=dlg.sprite_name,
                frame_w=dlg.frame_width,
                frame_h=dlg.frame_height,
                anim_table=dlg.anim_table,
                category=dlg.category,
                palette_tag=pal_tag,
                palette_slot=pal_slot,
                create_new_palette=create_new,
                palette_colors=pal_colors,
            )
        except Exception as e:
            QMessageBox.critical(
                self, "Error", f"Failed to create sprite:\n{e}"
            )
            return

        applied_str = "\n".join(f"  + {a}" for a in applied_list) or "  (none)"

        if success:
            QMessageBox.information(
                self, "Sprite Created",
                f"New overworld sprite '{dlg.sprite_name}' was added.\n\n"
                f"Changes:\n{applied_str}\n\n"
                f"Run 'make modern' to verify the project compiles.\n"
                f"The sprite list will refresh now."
            )
            # Push new constant into EVENTide's ConstantsManager immediately
            self._notify_new_gfx_constant(gfx_const, dlg.png_path)
            # Reload to pick up the new sprite
            self.load(self._project_root)
        else:
            err_str = "\n".join(f"  ! {e}" for e in error_list)
            QMessageBox.warning(
                self, "Partially Created",
                f"Some steps failed:\n\n"
                f"Applied:\n{applied_str}\n\n"
                f"Errors:\n{err_str}\n\n"
                f"You may need to fix the remaining entries manually."
            )
            self._notify_new_gfx_constant(gfx_const, dlg.png_path)
            self.load(self._project_root)

    # ──────────────────────────────────────────────── dynamic palettes ──
    def _update_dowp_status(self) -> None:
        """Update the DOWP button and status label based on patch state."""
        try:
            from dynamic_ow_pal_patch import is_dowp_enabled
        except ImportError:
            self._dowp_btn.setVisible(False)
            self._dowp_status.setText("")
            return

        if is_dowp_enabled(self._project_root):
            self._dowp_btn.setEnabled(False)
            self._dowp_btn.setText("Dynamic Palettes Active")
            self._dowp_status.setText(
                "✓ Dynamic OW palettes enabled. Each sprite can use its own "
                "unique palette — no longer limited to the 4 shared NPC slots."
            )
            self._dowp_status.setStyleSheet("color: #6a6; font-size: 10px;")
        else:
            self._dowp_btn.setEnabled(True)
            self._dowp_btn.setText("Enable Dynamic Palettes…")
            self._dowp_status.setText(
                "Standard mode: sprites share 4 NPC palette slots.\n"
                "Enable dynamic palettes to use custom per-sprite palettes."
            )
            self._dowp_status.setStyleSheet("color: #888; font-size: 10px;")

    def _enable_dynamic_palettes(self) -> None:
        """Apply the Dynamic Overworld Palettes patch after user confirmation."""
        if not self._project_root:
            return

        try:
            from dynamic_ow_pal_patch import is_dowp_enabled, apply_dowp_patch
        except ImportError:
            QMessageBox.critical(
                self, "Error",
                "Could not load the dynamic palette patch module.\n"
                "Please ensure dynamic_ow_pal_patch.py is in the core/ folder.",
            )
            return

        if is_dowp_enabled(self._project_root):
            QMessageBox.information(
                self, "Already Enabled",
                "Dynamic Overworld Palettes are already active in this project.",
            )
            return

        # Confirmation dialog with warning
        ret = QMessageBox.warning(
            self,
            "Enable Dynamic Overworld Palettes?",
            "This will modify several C source files in your project to enable\n"
            "the Dynamic Overworld Palettes system.\n\n"
            "What it does:\n"
            "  • Sprites load their palette on demand when spawning\n"
            "  • Each sprite can have its own unique palette\n"
            "  • No longer limited to the 4 shared NPC palette slots\n"
            "  • Reflection palettes are generated automatically\n"
            "  • Up to 16 unique palettes can be on screen at once\n\n"
            "⚠ This is a ONE-WAY change.\n"
            "The patch modifies C source files and cannot be automatically\n"
            "reversed. Make sure you have a backup or git commit first.\n\n"
            "Files that will be modified:\n"
            "  • src/event_object_movement.c\n"
            "  • src/field_effect.c\n"
            "  • src/field_effect_helpers.c\n"
            "  • include/event_object_movement.h\n"
            "  • include/field_effect.h\n\n"
            "Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return

        # Apply the patch
        success, applied_list, failed_list = apply_dowp_patch(self._project_root)

        if success:
            self._update_dowp_status()
            detail = "\n".join(f"  ✓ {a}" for a in applied_list)
            QMessageBox.information(
                self,
                "Dynamic Palettes Enabled",
                f"The patch was applied successfully.\n\n"
                f"Changes made:\n{detail}\n\n"
                f"You should now run 'make modern' to verify the project\n"
                f"still compiles. If there are errors, check the log panel.",
            )
        else:
            applied_str = "\n".join(f"  ✓ {a}" for a in applied_list) if applied_list else "  (none)"
            failed_str = "\n".join(f"  ✗ {f}" for f in failed_list)
            QMessageBox.warning(
                self,
                "Patch Partially Applied",
                f"Some parts of the patch could not be applied.\n"
                f"This usually means your source files have been modified\n"
                f"in ways the patcher didn't expect.\n\n"
                f"Applied:\n{applied_str}\n\n"
                f"Failed:\n{failed_str}\n\n"
                f"You may need to apply the remaining changes manually.\n"
                f"Search for 'DOWP' in the applied files to see what was changed.",
            )
            # Still update status — partial patches are tracked by the marker file
            self._update_dowp_status()

    # ────────────────────────────────────────────────────────── save ──
    def _notify_new_gfx_constant(self, gfx_const: str, png_path: str) -> None:
        """Push a new OBJ_EVENT_GFX constant into ConstantsManager and
        signal EVENTide to refresh its dropdown immediately."""
        try:
            from eventide.backend.constants_manager import ConstantsManager
            if gfx_const not in ConstantsManager.OBJECT_GFX:
                ConstantsManager.OBJECT_GFX.append(gfx_const)
            if png_path:
                from pathlib import Path
                ConstantsManager.OBJECT_GFX_PATHS[gfx_const] = Path(png_path)
        except ImportError:
            pass
        # Signal the unified window to refresh EVENTide
        self.gfx_constants_changed.emit()

    def has_unsaved_changes(self) -> bool:
        return bool(self._palette_dirty)

    def flush_to_disk(self) -> tuple[int, list[str]]:
        """Write all dirty palettes to .pal files."""
        ok = 0
        errors: list[str] = []
        for tag in list(self._palette_dirty):
            pal_path = self._pal_paths.get(tag, "")
            if not pal_path:
                errors.append(f"overworld-pal:{tag} (no path)")
                continue
            colors = self._palettes.get(tag)
            if colors and write_jasc_pal(pal_path, colors):
                ok += 1
            else:
                errors.append(f"overworld-pal:{tag}")
        self._palette_dirty.clear()
        return ok, errors
