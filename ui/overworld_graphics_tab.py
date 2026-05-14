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
from PyQt6.QtGui import QImage, QPixmap, QColor, QIcon
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QGroupBox,
    QPushButton, QFileDialog, QMessageBox, QListWidget, QListWidgetItem,
    QScrollArea, QGridLayout, QFrame, QSplitter, QSizePolicy, QLineEdit,
    QDialog, QFormLayout, QSpinBox, QDialogButtonBox, QTabWidget,
    QCheckBox,
)

from ui.palette_utils import read_jasc_pal, write_jasc_pal, clamp_to_gba
from ui.draggable_palette_row import DraggablePaletteRow
from core.sprite_palette_bus import get_bus as _get_palette_bus, CAT_OVERWORLD
from core.gba_image_utils import swap_palette_entries, export_indexed_png

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
    """One shared palette with its tag, file paths, and list of sprites.

    `pal_path` and `gbapal_path` are BOTH tracked: the JASC text file
    PorySuite's UI reads from and edits, plus the binary .gbapal the
    project's build pipeline INCBINs.  Save writes both atomically so
    they can never diverge — keeping the .gbapal binary in lockstep
    with the .pal sibling is what makes palette edits actually reach
    the in-game rendering (previously only the .pal got updated, so
    the build kept using stale colours).
    """
    __slots__ = (
        "tag_name", "display_name", "pal_path", "gbapal_path", "sprites",
    )

    def __init__(self, tag_name: str, pal_path: str, gbapal_path: str = ""):
        self.tag_name = tag_name
        self.pal_path = pal_path
        self.gbapal_path = gbapal_path
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
    # 1. Palette tag → symbol → file path chain.
    # The INCBIN entry in object_event_graphics.h gives us the binary
    # .gbapal path (the canonical anchor — that's what the build reads).
    # The JASC .pal sibling lives in the same dir with `.pal` extension;
    # we ALWAYS register it (whether or not the file exists yet), and
    # the project-open self-heal below creates any missing siblings from
    # their existing .gbapal binaries so subsequent saves stay on the
    # JASC track.
    tag_to_symbol = _parse_palette_table(root)
    symbol_to_relpath = _parse_pal_symbol_to_path(root)

    from core.overworld_palette_io import (
        pal_sibling_for_gbapal, ensure_pal_sibling,
    )

    tag_to_pal: Dict[str, str] = {}
    tag_to_gbapal: Dict[str, str] = {}
    for tag, sym in tag_to_symbol.items():
        rel = symbol_to_relpath.get(sym, "")
        if not rel:
            continue
        abs_gbapal = os.path.join(root, rel)
        if not os.path.isfile(abs_gbapal):
            # Symbol references a .gbapal that doesn't exist on disk —
            # nothing to register or heal.  This is the case for tags
            # whose source files haven't been built yet (fresh clone).
            continue
        tag_to_gbapal[tag] = abs_gbapal
        tag_to_pal[tag] = pal_sibling_for_gbapal(abs_gbapal)
        # Project-open self-heal: if the .pal sibling is missing OR the
        # .gbapal has been corrupted by the prior save-path bug
        # (JASC text written into the binary file), repair both files
        # so subsequent saves and builds stay consistent.  Garbage-free
        # — only touches palettes that actually exist on disk.
        try:
            ensure_pal_sibling(abs_gbapal)
        except Exception:
            pass

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
        pools_by_tag[tag] = PalettePool(
            tag, path, gbapal_path=tag_to_gbapal.get(tag, ""),
        )

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

def _apply_palette_to_image(img: QImage, palette: List[Color]) -> Optional[QPixmap]:
    """Apply a 16-colour palette to an already-loaded indexed QImage and return a QPixmap."""
    try:
        ct = list(img.colorTable())
        for i, (r, g, b) in enumerate(palette[:16]):
            if i >= len(ct):
                ct.append((0xFF << 24) | (r << 16) | (g << 8) | b)
            else:
                alpha = ct[i] & 0xFF000000
                ct[i] = alpha | (r << 16) | (g << 8) | b
        img2 = img.copy()
        img2.setColorTable(ct)
        return QPixmap.fromImage(img2)
    except Exception:
        return None


def _reskin_overworld(png_path: str, palette: List[Color]) -> Optional[QPixmap]:
    """Recolour an overworld sprite sheet using a 16-colour palette."""
    try:
        img = QImage(png_path)
        if img.isNull():
            return None
        if img.format() != QImage.Format.Format_Indexed8:
            img = img.convertToFormat(QImage.Format.Format_Indexed8)
        return _apply_palette_to_image(img, palette)
    except Exception:
        return None


def _reskin_overworld_img(img: QImage, palette: List[Color]) -> Optional[QPixmap]:
    """Recolour an already-loaded indexed QImage (e.g. from Index-as-BG remap)."""
    return _apply_palette_to_image(img, palette)


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
            self._pal_combo.addItem(
                "Pick palette manually (indexer)…", "NEW_MANUAL",
            )
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
                "to create a new palette from it — or pick "
                "'Pick palette manually (indexer)…' to remap any PNG."
            )
            return

        # Manual pick — open the indexer dialog on the chosen PNG.  The
        # user picks/orders 16 colours; we save those as the new palette
        # AND remap the source PNG to that palette so the saved image
        # uses the new indices.  Indexed-source PNGs auto-load their
        # existing palette as the initial result so the user just needs
        # to confirm / tweak slot order.
        if pal_data == "NEW_MANUAL":
            from ui.dialogs.manual_palette_pick_dialog import (
                import_image_manually_from_path,
            )
            result = import_image_manually_from_path(
                self._png_path, target_colors=16, parent=self,
            )
            if result is None:
                # User cancelled the picker — don't close the Add dialog.
                return
            colors, remapped_img = result
            self._palette_colors = list(colors)
            # Save the remapped indexed PNG over the source the dialog
            # was pointing at, so when create_overworld_sprite copies
            # the PNG into the project it brings the new pixel indices
            # along with the new palette.  Garbage-free: if save fails
            # the source PNG is unchanged.
            try:
                from ui.dialogs.manual_palette_pick_dialog import (
                    save_remapped_image,
                )
                if not save_remapped_image(
                        remapped_img, colors, self._png_path):
                    QMessageBox.warning(
                        self, "Save Failed",
                        "Couldn't write the remapped PNG.  The palette "
                        "you picked is loaded in memory but the source "
                        "image on disk is unchanged.  Check folder "
                        "permissions and retry, or use 'Create new "
                        "palette from PNG' instead.",
                    )
                    return
            except Exception as exc:
                QMessageBox.warning(
                    self, "Save Failed",
                    f"Couldn't remap the image:\n{exc}",
                )
                return
            # Refresh the preview so the user sees the remapped image
            # before they confirm the OK that closes the dialog.
            pix = QPixmap(self._png_path)
            if not pix.isNull():
                self._preview_lbl.setPixmap(pix.scaled(
                    self._preview_lbl.width(), self._preview_lbl.height(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.FastTransformation,
                ))

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


# ── Field Effect Sprites ─────────────────────────────────────────────────────

class FieldEffectEntry:
    """One field effect or misc sprite."""
    __slots__ = ("name", "png_path", "pal_path", "category")

    def __init__(self, name: str, png_path: str,
                 pal_path: Optional[str], category: str) -> None:
        self.name = name
        self.png_path = png_path
        self.pal_path = pal_path  # None if no .pal file exists
        self.category = category


def _scan_field_effect_sprites(root: str) -> List[FieldEffectEntry]:
    """Scan the project's non-NPC overworld sprite directories for PNGs.

    Covers:
      - ``graphics/field_effects/pics/`` — the classic field effects
        (grass rustle, sparkles, dust, splashes, shadows, etc.).
      - ``graphics/misc/`` — global misc sprites (confetti, egg hatch,
        emoticons, etc.).
      - Sprite templates declared in ``field_effects/field_effect_objects.h``
        whose pic table references a ``gObjectEventPic_*`` symbol
        (cross-boundary case — the template lives with field effects but
        the sprite asset lives in ``graphics/object_events/pics/``).
        Without this catch the surf blob (and any future similarly-defined
        sprite) would be invisible to BOTH the NPC Sprites tab (no
        GraphicsInfo entry, no ``OBJ_EVENT_GFX_*`` constant) and the
        Field Effect Sprites tab (PNG lives in the wrong folder for the
        directory scan).
    """
    entries: List[FieldEffectEntry] = []
    seen_paths: set[str] = set()

    dirs = [
        (os.path.join(root, "graphics", "field_effects", "pics"), "Field Effects"),
        (os.path.join(root, "graphics", "misc"), "Misc"),
    ]
    for dirpath, category in dirs:
        if not os.path.isdir(dirpath):
            continue
        for fname in sorted(os.listdir(dirpath)):
            if not fname.lower().endswith(".png"):
                continue
            png = os.path.join(dirpath, fname)
            pal = png[:-4] + ".pal"
            entries.append(FieldEffectEntry(
                name=fname[:-4],
                png_path=png,
                pal_path=pal if os.path.isfile(pal) else None,
                category=category,
            ))
            seen_paths.add(os.path.normcase(os.path.normpath(png)))

    # Cross-boundary sweep: pick up sprites whose template is in
    # field_effect_objects.h but whose pic asset lives in
    # graphics/object_events/pics/. Currently just surf_blob; this loop
    # finds any future entries automatically.
    pic_paths = _parse_pic_symbol_to_path(root)
    feo_path = os.path.join(root, "src", "data", "field_effects", "field_effect_objects.h")
    if os.path.isfile(feo_path):
        try:
            with open(feo_path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            text = ""
        pic_ref_pat = re.compile(
            r"static const struct SpriteFrameImage sPicTable_(\w+)\[\][^;]*?(gObjectEventPic_\w+)",
            re.DOTALL,
        )
        for tm in pic_ref_pat.finditer(text):
            template_name = tm.group(1)
            pic_symbol = tm.group(2)
            rel_4bpp = pic_paths.get(pic_symbol, "")
            if not rel_4bpp.endswith(".4bpp"):
                continue
            png_rel = rel_4bpp[:-len(".4bpp")] + ".png"
            png_abs = os.path.join(root, png_rel.replace("/", os.sep))
            if not os.path.isfile(png_abs):
                continue
            normed = os.path.normcase(os.path.normpath(png_abs))
            if normed in seen_paths:
                continue
            pal = png_abs[:-4] + ".pal"
            entries.append(FieldEffectEntry(
                name=template_name,
                png_path=png_abs,
                pal_path=pal if os.path.isfile(pal) else None,
                category="Field Effects",
            ))
            seen_paths.add(normed)

    return sorted(entries, key=lambda e: (e.category, e.name.lower()))


class _RebakeFromTagDialog(QDialog):
    """Pick an OBJ_EVENT_PAL_TAG_* to bake into a Field Effect sprite.

    Each tag is shown as a list row with a 16-swatch palette preview as
    the row icon. The icon is rendered to a small QPixmap on the fly
    rather than persisted, so this dialog has no caching / lifecycle
    concerns — it's a one-shot picker.
    """

    def __init__(self, sprite_name: str,
                 tag_palettes: List[Tuple[str, List[Color]]],
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Re-bake from Palette Tag")
        self.setMinimumSize(520, 420)

        self._tag_palettes = tag_palettes
        self._chosen_tag: Optional[str] = None
        self._chosen_colors: Optional[List[Color]] = None

        layout = QVBoxLayout(self)

        info = QLabel(
            f"Pick a palette tag to bake into <b>{sprite_name}</b>.\n"
            "Live colours from the Pokemon / NPC tab take priority over\n"
            "the on-disk .pal / .gbapal — so unsaved palette edits in that\n"
            "tab are honoured here too.")
        info.setStyleSheet("color: #aaa; font-size: 11px;")
        info.setWordWrap(True)
        layout.addWidget(info)

        self._list = QListWidget()
        self._list.setIconSize(QSize(192, 16))
        self._list.setSpacing(2)
        layout.addWidget(self._list, 1)

        for tag, colors in tag_palettes:
            icon = self._build_swatch_icon(colors)
            item = QListWidgetItem(icon, tag)
            self._list.addItem(item)

        # Double-click accepts; single-click just selects.
        self._list.itemDoubleClicked.connect(
            lambda *_: self.accept())

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self)
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("Apply")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    @staticmethod
    def _build_swatch_icon(colors: List[Color]) -> QIcon:
        """Render a 16-color palette as a 192×16 pixmap (12 px per swatch)."""
        from PyQt6.QtGui import QPainter
        pix = QPixmap(192, 16)
        pix.fill(QColor(0, 0, 0))
        painter = QPainter(pix)
        try:
            for i, (r, g, b) in enumerate(colors[:16]):
                painter.fillRect(i * 12, 0, 12, 16, QColor(r, g, b))
        finally:
            painter.end()
        return QIcon(pix)

    def accept(self) -> None:
        row = self._list.currentRow()
        if 0 <= row < len(self._tag_palettes):
            tag, colors = self._tag_palettes[row]
            self._chosen_tag = tag
            self._chosen_colors = list(colors)
        super().accept()

    def chosen_tag(self) -> Optional[str]:
        return self._chosen_tag

    def chosen_colors(self) -> Optional[List[Color]]:
        return self._chosen_colors


class FieldEffectSpritesTab(QWidget):
    """Editor for field effect sprites — exclamation marks, music notes, emoticons, etc.

    These live in graphics/field_effects/pics/ and graphics/misc/.  Most have
    no separate .pal file; their palette is baked into the PNG.  When a .pal
    file does exist, edits are written there.  When there is no .pal file,
    saving bakes the new palette back into the PNG via export_indexed_png.
    """

    modified = pyqtSignal()

    _DIRTY_SS = (
        "QGroupBox { border: 1px solid #ffb74d; border-radius: 4px; }"
        "QGroupBox::title { color: #ffb74d; }"
    )
    _BUS_PREFIX = "fe:"

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._project_root = ""
        self._entries: List[FieldEffectEntry] = []
        self._current: Optional[FieldEffectEntry] = None
        self._loading = False

        self._palettes: Dict[str, List[Color]] = {}       # key = _bus_key(entry)
        self._palette_dirty: set[str] = set()
        self._sprite_imgs: Dict[str, QImage] = {}          # key = png_path
        self._sprite_png_dirty: set[str] = set()

        self._build_ui()
        _get_palette_bus().palette_changed.connect(self._on_bus_palette_changed)

    # ── helpers ──────────────────────────────────────────────────────────────

    def _bus_key(self, entry: FieldEffectEntry) -> str:
        return self._BUS_PREFIX + entry.png_path

    def _collect_overworld_tag_palettes(self) -> List[Tuple[str, List[Color]]]:
        """Return [(OBJ_EVENT_PAL_TAG_*, 16-color list), ...] sorted by tag.

        Source priority for each tag's colors:
          1. Live bus state (in-memory edits from the Pokemon/NPC tab,
             possibly unsaved) — `_get_palette_bus().get_overworld_palette(tag)`.
          2. The .pal sibling of the .gbapal file referenced by the tag's
             palette symbol in object_event_graphics.h. Easier to read than
             the binary .gbapal and PorySuite-Z keeps the two in sync.
          3. The .gbapal binary itself via `_read_gbapal_file`.

        Tags whose palette can't be resolved at all are dropped from the
        list so the user doesn't see an empty / black entry.
        """
        if not self._project_root:
            return []
        tag_to_symbol = _parse_palette_table(self._project_root)
        sym_to_path = _parse_pal_symbol_to_path(self._project_root)
        bus = _get_palette_bus()

        result: List[Tuple[str, List[Color]]] = []
        for tag in sorted(tag_to_symbol.keys()):
            colors = bus.get_overworld_palette(tag)
            if not colors:
                sym = tag_to_symbol.get(tag, "")
                rel = sym_to_path.get(sym, "")
                if rel:
                    abs_pal = os.path.join(
                        self._project_root, rel[:-len(".gbapal")] + ".pal")
                    abs_gbapal = os.path.join(self._project_root, rel)
                    if os.path.isfile(abs_pal):
                        loaded = read_jasc_pal(abs_pal)
                        if loaded:
                            colors = list(loaded)
                    if not colors and os.path.isfile(abs_gbapal):
                        from core.tilemap_data import _read_gbapal_file
                        loaded = _read_gbapal_file(abs_gbapal)
                        if loaded:
                            colors = list(loaded)
            if not colors:
                continue
            # Pad / clamp to 16 entries so the swatch row renders cleanly.
            while len(colors) < 16:
                colors.append((0, 0, 0))
            result.append((tag, colors[:16]))
        return result

    def _get_palette(self, entry: FieldEffectEntry) -> List[Color]:
        """Return palette from bus, RAM, .pal file, or baked PNG colour table."""
        key = self._bus_key(entry)
        if key in self._palettes:
            return self._palettes[key]

        bus_colors = _get_palette_bus().get_overworld_palette(key)
        if bus_colors:
            self._palettes[key] = bus_colors
            return bus_colors

        if entry.pal_path and os.path.isfile(entry.pal_path):
            colors = read_jasc_pal(entry.pal_path)
            if colors:
                while len(colors) < 16:
                    colors.append((0, 0, 0))
                self._palettes[key] = colors[:16]
                return self._palettes[key]

        if entry.png_path and os.path.isfile(entry.png_path):
            img = QImage(entry.png_path)
            if not img.isNull():
                if img.format() != QImage.Format.Format_Indexed8:
                    img = img.convertToFormat(QImage.Format.Format_Indexed8)
                ct = img.colorTable()
                colors: List[Color] = []
                for c in ct[:16]:
                    colors.append(clamp_to_gba((c >> 16) & 0xFF,
                                               (c >> 8) & 0xFF,
                                               c & 0xFF))
                while len(colors) < 16:
                    colors.append((0, 0, 0))
                self._palettes[key] = colors
                return self._palettes[key]

        self._palettes[key] = [(0, 0, 0)] * 16
        return self._palettes[key]

    def _filtered_entries(self) -> List[FieldEffectEntry]:
        cat = self._fe_cat_combo.currentData() or "all"
        search = self._fe_search.text().strip().lower()
        result = self._entries
        if cat != "all":
            result = [e for e in result if e.category == cat]
        if search:
            result = [e for e in result if search in e.name.lower()]
        return result

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(2)
        splitter.setStyleSheet("QSplitter::handle { background: #2e2e2e; }")

        # ── LEFT: sprite list ─────────────────────────────────────────────────
        left = QWidget()
        left.setMinimumWidth(220)
        left.setMaximumWidth(380)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(8, 8, 8, 8)
        lv.setSpacing(6)

        cat_row = QHBoxLayout()
        cat_row.setSpacing(6)
        cat_row.addWidget(QLabel("Category:"))
        self._fe_cat_combo = QComboBox()
        self._fe_cat_combo.wheelEvent = lambda e: e.ignore()
        self._fe_cat_combo.addItem("All", "all")
        self._fe_cat_combo.addItem("Field Effects", "Field Effects")
        self._fe_cat_combo.addItem("Misc", "Misc")
        cat_row.addWidget(self._fe_cat_combo, 1)
        lv.addLayout(cat_row)

        self._fe_search = QLineEdit()
        self._fe_search.setPlaceholderText("Search sprites…")
        lv.addWidget(self._fe_search)

        self._fe_count_lbl = QLabel("")
        self._fe_count_lbl.setStyleSheet("color: #888; font-size: 10px;")
        lv.addWidget(self._fe_count_lbl)

        self._fe_list = QListWidget()
        self._fe_list.setIconSize(QSize(40, 40))
        self._fe_list.setSpacing(2)
        lv.addWidget(self._fe_list, 1)

        splitter.addWidget(left)

        # ── RIGHT: detail + palette ───────────────────────────────────────────
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(8, 8, 8, 8)
        rv.setSpacing(8)

        sheet_group = QGroupBox("Sprite Sheet")
        sg = QVBoxLayout(sheet_group)
        sg.setContentsMargins(8, 14, 8, 8)
        self._fe_sheet_lbl = QLabel("Select a sprite")
        self._fe_sheet_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._fe_sheet_lbl.setMinimumHeight(100)
        self._fe_sheet_lbl.setStyleSheet("background: #111; border: 1px solid #333;")
        sg.addWidget(self._fe_sheet_lbl)

        self._fe_sheet_info = QLabel("")
        self._fe_sheet_info.setStyleSheet("color: #888; font-size: 11px;")
        self._fe_sheet_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._fe_sheet_info.setWordWrap(True)
        sg.addWidget(self._fe_sheet_info)

        fe_btn_row = QHBoxLayout()
        self._fe_open_folder_btn = QPushButton("Show in Folder")
        self._fe_open_folder_btn.setToolTip(
            "Open the folder containing this sprite and select it.")
        fe_btn_row.addWidget(self._fe_open_folder_btn)
        fe_btn_row.addStretch(1)
        sg.addLayout(fe_btn_row)
        rv.addWidget(sheet_group, 1)

        pal_frame = QGroupBox("Palette")
        self._fe_pal_frame = pal_frame
        pf = QVBoxLayout(pal_frame)
        pf.setContentsMargins(8, 14, 8, 8)
        pf.setSpacing(6)

        self._fe_pal_info = QLabel("Select a sprite to view its palette")
        self._fe_pal_info.setStyleSheet("color: #aaa; font-size: 11px;")
        self._fe_pal_info.setWordWrap(True)
        pf.addWidget(self._fe_pal_info)

        self._fe_pal_row = DraggablePaletteRow()
        pf.addWidget(self._fe_pal_row)

        fe_pal_btns = QHBoxLayout()
        self._fe_import_png_btn = QPushButton("Import Palette from PNG…")
        self._fe_import_png_btn.setToolTip(
            "Extract palette from an indexed PNG and apply it to this sprite.")
        self._fe_import_manual_btn = QPushButton("Import Manually…")
        self._fe_import_manual_btn.setToolTip(
            "Open the manual palette picker on a PNG.\n"
            "You choose which colours land in which slot, set the\n"
            "BG/transparent slot, and reorder freely.  Works on any\n"
            "PNG (indexed or full-colour).")
        self._fe_import_pal_btn = QPushButton("Import from .pal…")
        self._fe_import_pal_btn.setToolTip(
            "Load a JASC .pal file and apply it to this sprite.")
        self._fe_rebake_tag_btn = QPushButton("Re-bake from Palette Tag…")
        self._fe_rebake_tag_btn.setToolTip(
            "Replace this sprite's palette with the live colours of an\n"
            "OBJ_EVENT_PAL_TAG_* (Pokemon / NPC tab). Use this after editing\n"
            "the player or NPC palette to update field-effect sprites that\n"
            "share that palette tag at runtime, so GIMP shows the same\n"
            "colours the game will render with.")
        self._fe_browse_folder_btn = QPushButton("Open Folder")
        self._fe_browse_folder_btn.setToolTip(
            "Open the field_effects/pics folder.")
        fe_pal_btns.addWidget(self._fe_import_png_btn)
        fe_pal_btns.addWidget(self._fe_import_manual_btn)
        fe_pal_btns.addWidget(self._fe_import_pal_btn)
        fe_pal_btns.addWidget(self._fe_rebake_tag_btn)
        fe_pal_btns.addWidget(self._fe_browse_folder_btn)
        fe_pal_btns.addStretch(1)
        pf.addLayout(fe_pal_btns)

        rv.addWidget(pal_frame, 0)

        splitter.addWidget(right)
        splitter.setSizes([280, 620])
        outer.addWidget(splitter)

        # ── Wire signals ──────────────────────────────────────────────────────
        self._fe_pal_row.colors_changed.connect(self._on_palette_edited)
        self._fe_pal_row.palette_reordered.connect(self._on_palette_reordered)
        self._fe_pal_row.swatch_set_as_bg.connect(self._on_set_swatch_as_bg)
        self._fe_list.currentRowChanged.connect(self._on_list_selection_changed)
        self._fe_cat_combo.currentIndexChanged.connect(self._rebuild_list)
        self._fe_search.textChanged.connect(self._rebuild_list)
        self._fe_import_png_btn.clicked.connect(self._import_palette_from_png)
        self._fe_import_manual_btn.clicked.connect(self._import_palette_from_png_manual)
        self._fe_import_pal_btn.clicked.connect(self._import_palette_from_pal)
        self._fe_rebake_tag_btn.clicked.connect(self._rebake_from_palette_tag)
        self._fe_browse_folder_btn.clicked.connect(self._open_sprite_folder)
        self._fe_open_folder_btn.clicked.connect(self._open_current_sprite_folder)

    # ── loading ───────────────────────────────────────────────────────────────

    def load(self, project_root: str) -> None:
        """Scan field effect sprite folders and populate the list."""
        self._project_root = project_root
        self._palettes.clear()
        self._palette_dirty.clear()
        self._sprite_imgs.clear()
        self._sprite_png_dirty.clear()
        self._current = None

        self._fe_pal_frame.setStyleSheet("")
        self._fe_pal_info.setText("Select a sprite to view its palette")
        self._fe_sheet_lbl.clear()
        self._fe_sheet_lbl.setText("Select a sprite")
        self._fe_sheet_info.setText("")
        self._fe_pal_row.set_colors([(0, 0, 0)] * 16)

        self._entries = _scan_field_effect_sprites(project_root)

        self._loading = True
        try:
            self._rebuild_list()
        finally:
            self._loading = False

    # ── list management ───────────────────────────────────────────────────────

    def _rebuild_list(self) -> None:
        self._fe_list.blockSignals(True)
        self._fe_list.clear()

        entries = self._filtered_entries()
        self._fe_count_lbl.setText(f"{len(entries)} sprites")

        for entry in entries:
            key = self._bus_key(entry)
            palette = self._get_palette(entry)

            icon = QIcon()
            if entry.png_path and os.path.isfile(entry.png_path):
                pix = _reskin_overworld(entry.png_path, palette)
                if pix and not pix.isNull():
                    h = pix.height()
                    w = min(h, pix.width())
                    if pix.width() > w:
                        pix = pix.copy(0, 0, w, h)
                    scaled = pix.scaled(
                        40, 40,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.FastTransformation,
                    )
                    icon = QIcon(scaled)

            item = QListWidgetItem(icon, entry.name)
            item.setData(Qt.ItemDataRole.UserRole, entry.png_path)
            if key in self._palette_dirty:
                item.setBackground(QColor("#3d2e00"))
            self._fe_list.addItem(item)

        # Restore selection
        if self._current:
            for i, e in enumerate(entries):
                if e.png_path == self._current.png_path:
                    self._fe_list.setCurrentRow(i)
                    break

        self._fe_list.blockSignals(False)

    # ── detail view ───────────────────────────────────────────────────────────

    def _on_list_selection_changed(self, row: int) -> None:
        entries = self._filtered_entries()
        if row < 0 or row >= len(entries):
            return
        entry = entries[row]
        self._current = entry
        self._show_detail(entry)
        self._load_palette_panel(entry)

    def _load_palette_panel(self, entry: FieldEffectEntry) -> None:
        key = self._bus_key(entry)
        palette = self._get_palette(entry)

        if entry.pal_path:
            pal_info = f"Palette file: {os.path.basename(entry.pal_path)}  (saves to .pal)"
        else:
            pal_info = "No .pal file — palette baked into PNG on save"
        self._fe_pal_info.setText(pal_info)

        if key in self._palette_dirty:
            self._fe_pal_frame.setStyleSheet(self._DIRTY_SS)
        else:
            self._fe_pal_frame.setStyleSheet("")

        self._loading = True
        try:
            self._fe_pal_row.set_colors(palette)
        finally:
            self._loading = False

    def _show_detail(self, entry: FieldEffectEntry) -> None:
        palette = self._get_palette(entry)

        pix = None
        remapped = self._sprite_imgs.get(entry.png_path)
        if palette and remapped:
            pix = _reskin_overworld_img(remapped, palette)
        elif palette and entry.png_path:
            pix = _reskin_overworld(entry.png_path, palette)

        if pix and not pix.isNull():
            scale = max(1, min(4, self._fe_sheet_lbl.width() // max(1, pix.width())))
            scaled = pix.scaled(
                pix.width() * scale, pix.height() * scale,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
            self._fe_sheet_lbl.setPixmap(scaled)
        else:
            self._fe_sheet_lbl.clear()
            self._fe_sheet_lbl.setText("No sprite")

        if entry.png_path and os.path.isfile(entry.png_path):
            img = QImage(entry.png_path)
            size_str = f"{img.width()}×{img.height()}px" if not img.isNull() else "?"
        else:
            size_str = "?"
        self._fe_sheet_info.setText(
            f"{entry.name}  |  {entry.category}  |  {size_str}"
        )

    # ── palette editing ───────────────────────────────────────────────────────

    def _on_palette_edited(self) -> None:
        if self._loading or not self._current:
            return
        key = self._bus_key(self._current)
        colors = self._fe_pal_row.colors()
        self._palettes[key] = colors
        self._palette_dirty.add(key)
        _get_palette_bus().set_overworld_palette(key, colors)
        self._fe_pal_frame.setStyleSheet(self._DIRTY_SS)
        self.modified.emit()
        self._show_detail(self._current)
        self._rebuild_list()

    def _on_palette_reordered(self, from_idx: int, to_idx: int) -> None:
        if self._loading or not self._current:
            return
        n = 16
        if from_idx == to_idx or not (0 <= from_idx < n) or not (0 <= to_idx < n):
            return
        key = self._bus_key(self._current)
        pal = list(self._palettes.get(key) or [(0, 0, 0)] * n)
        while len(pal) < n:
            pal.append((0, 0, 0))
        pal[from_idx], pal[to_idx] = pal[to_idx], pal[from_idx]
        self._palettes[key] = pal
        self._palette_dirty.add(key)
        _get_palette_bus().set_overworld_palette(key, pal)
        self._fe_pal_frame.setStyleSheet(self._DIRTY_SS)
        self._loading = True
        try:
            self._fe_pal_row.set_colors(pal)
        finally:
            self._loading = False
        self.modified.emit()
        self._show_detail(self._current)
        self._rebuild_list()

    def _on_set_swatch_as_bg(self, slot: int) -> None:
        if self._loading or not self._current:
            return
        if slot <= 0 or slot >= 16:
            return
        entry = self._current
        key = self._bus_key(entry)
        pal = list(self._palettes.get(key) or [(0, 0, 0)] * 16)
        while len(pal) < 16:
            pal.append((0, 0, 0))

        png_key = entry.png_path
        if png_key not in self._sprite_imgs:
            if entry.png_path and os.path.isfile(entry.png_path):
                img = QImage(entry.png_path)
                if not img.isNull():
                    if img.format() != QImage.Format.Format_Indexed8:
                        img = img.convertToFormat(QImage.Format.Format_Indexed8)
                    self._sprite_imgs[png_key] = img

        img = self._sprite_imgs.get(png_key)
        if img is not None:
            try:
                new_img, _ = swap_palette_entries(img, pal, slot, 0)
                self._sprite_imgs[png_key] = new_img
                self._sprite_png_dirty.add(png_key)
            except Exception as exc:
                QMessageBox.warning(
                    self, "Index as Background Error",
                    f"Failed to remap sprite pixels:\n{exc}",
                )
                return
        else:
            QMessageBox.information(
                self, "No Sprite PNG",
                "This sprite has no on-disk PNG to remap.\n"
                "The palette-only swap has still been applied.",
            )

        pal[0], pal[slot] = pal[slot], pal[0]
        self._palettes[key] = pal
        self._loading = True
        try:
            self._fe_pal_row.set_colors(pal)
        finally:
            self._loading = False
        self._palette_dirty.add(key)
        _get_palette_bus().set_overworld_palette(key, pal)
        self._fe_pal_frame.setStyleSheet(self._DIRTY_SS)
        self.modified.emit()
        self._show_detail(self._current)
        self._rebuild_list()

    def _on_bus_palette_changed(self, category: str, key: str) -> None:
        if category != CAT_OVERWORLD or not key.startswith(self._BUS_PREFIX):
            return
        self._palettes.pop(key, None)
        self._rebuild_list()
        if self._current and self._bus_key(self._current) == key:
            pal = self._get_palette(self._current)
            self._loading = True
            try:
                self._fe_pal_row.set_colors(pal)
            finally:
                self._loading = False
            self._show_detail(self._current)

    # ── import ────────────────────────────────────────────────────────────────

    def _import_palette_from_png(self) -> None:
        """Auto-extract: read the PNG's existing 16-colour palette table."""
        self._do_palette_import_from_png(manual=False)

    def _import_palette_from_png_manual(self) -> None:
        """Manual pick: choose which colours go in which slots."""
        self._do_palette_import_from_png(manual=True)

    def _do_palette_import_from_png(self, manual: bool) -> None:
        if not self._current:
            QMessageBox.information(self, "No Sprite", "Select a sprite first.")
            return
        start = os.path.dirname(self._current.png_path) if self._current.png_path else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Select PNG" if manual else "Select Indexed PNG",
            start, "PNG Images (*.png)",
        )
        if not path:
            return

        remapped_img = None
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
            n_colors_used = sum(1 for c in colors if c != (0, 0, 0))
        else:
            img = QImage(path)
            if img.isNull():
                QMessageBox.warning(self, "Import Failed",
                                    f"Could not load:\n{path}")
                return
            if img.format() != QImage.Format.Format_Indexed8:
                QMessageBox.warning(
                    self, "Not an Indexed PNG",
                    "Convert to indexed-colour PNG (8-bit, 16 colours) "
                    "in your image editor first — or use 'Import "
                    "Manually…' to pick colours from any PNG.",
                )
                return
            ct = img.colorTable()
            colors: List[Color] = []
            for c in ct[:16]:
                colors.append(clamp_to_gba(
                    (c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF))
            while len(colors) < 16:
                colors.append((0, 0, 0))
            n_colors_used = min(len(ct), 16)

        # Manual mode: overwrite the field-effect's source PNG with the
        # remapped indexed image so the on-disk graphic matches the
        # newly-imported palette.  Auto mode preserves pixel indices.
        if manual and remapped_img is not None and self._current.png_path:
            try:
                from ui.dialogs.manual_palette_pick_dialog import (
                    save_remapped_image,
                )
                if not save_remapped_image(
                        remapped_img, colors, self._current.png_path):
                    QMessageBox.warning(
                        self, "Image Save Failed",
                        f"Palette loaded into the editor, but the "
                        f"remapped PNG couldn't be written to:\n"
                        f"{self._current.png_path}",
                    )
            except Exception as exc:
                QMessageBox.warning(
                    self, "Image Save Failed",
                    f"Could not save the remapped image:\n{exc}",
                )

        key = self._bus_key(self._current)
        self._palettes[key] = colors
        self._palette_dirty.add(key)
        _get_palette_bus().set_overworld_palette(key, colors)
        self._loading = True
        try:
            self._fe_pal_row.set_colors(colors)
        finally:
            self._loading = False
        self._fe_pal_frame.setStyleSheet(self._DIRTY_SS)
        self._show_detail(self._current)
        self._rebuild_list()
        self.modified.emit()
        QMessageBox.information(
            self, "Palette Imported",
            f"Loaded {n_colors_used} colours from {os.path.basename(path)}.\n"
            "Click File → Save to write the changes.",
        )

    def _import_palette_from_pal(self) -> None:
        if not self._current:
            QMessageBox.information(self, "No Sprite", "Select a sprite first.")
            return
        start = os.path.dirname(self._current.png_path) if self._current.png_path else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Select JASC Palette", start,
            "Palette Files (*.pal);;All Files (*)",
        )
        if not path:
            return
        colors = read_jasc_pal(path)
        if not colors:
            QMessageBox.warning(
                self, "Import Failed",
                f"Could not read a valid JASC palette from:\n{path}",
            )
            return
        while len(colors) < 16:
            colors.append((0, 0, 0))
        colors = [clamp_to_gba(r, g, b) for r, g, b in colors[:16]]

        key = self._bus_key(self._current)
        self._palettes[key] = colors
        self._palette_dirty.add(key)
        _get_palette_bus().set_overworld_palette(key, colors)
        self._loading = True
        try:
            self._fe_pal_row.set_colors(colors)
        finally:
            self._loading = False
        self._fe_pal_frame.setStyleSheet(self._DIRTY_SS)
        self._show_detail(self._current)
        self._rebuild_list()
        self.modified.emit()
        QMessageBox.information(
            self, "Palette Imported",
            f"Loaded 16 colours from {os.path.basename(path)}.\n"
            "Click File → Save to write the changes.",
        )

    # ── folder helpers ────────────────────────────────────────────────────────

    def _open_current_sprite_folder(self) -> None:
        if self._current and self._current.png_path:
            fp = self._current.png_path
            if os.path.isfile(fp):
                try:
                    import subprocess
                    subprocess.Popen(["explorer", "/select,", os.path.normpath(fp)])
                except Exception:
                    self._open_folder(os.path.dirname(fp))
            else:
                self._open_folder(os.path.dirname(fp))

    def _rebake_from_palette_tag(self) -> None:
        """Bake an OBJ_EVENT_PAL_TAG_* palette into the current FE sprite.

        Solves the cross-tab editing gap: changing the player palette in
        the Pokemon/NPC tab updates the player's PNGs, but field-effect
        PNGs that share that palette tag at runtime keep their old baked
        colours. Their PNG indices are still correct (the game renders
        fine), but GIMP shows stale colours, making them hard to edit.

        This action lets the user pick a tag, fetches that tag's current
        live palette (preferring in-memory bus state over disk so even
        unsaved Overworld edits propagate), and bakes it into the FE
        sprite's PNG color table. Pixel indices are untouched.
        """
        if not self._current:
            QMessageBox.information(
                self, "No Sprite Selected",
                "Select a Field Effect sprite in the list first.")
            return
        if not self._project_root:
            return

        tag_palettes = self._collect_overworld_tag_palettes()
        if not tag_palettes:
            QMessageBox.information(
                self, "No Palette Tags",
                "Couldn't find any OBJ_EVENT_PAL_TAG_* palettes.\n\n"
                "Open the Pokemon / NPC sub-tab first so Overworld\n"
                "palettes are loaded into the shared bus, then try again.")
            return

        dlg = _RebakeFromTagDialog(
            self._current.name, tag_palettes, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        chosen_tag = dlg.chosen_tag()
        chosen_colors = dlg.chosen_colors()
        if not chosen_tag or not chosen_colors:
            return

        # Apply: update RAM palette, push to bus, mark dirty, refresh
        # swatch row, bake into PNG via the existing flush-on-save path.
        # Pushing to the bus matches the pattern used by `_on_palette_edited`
        # so any other subscriber reading this FE entry's palette via the
        # bus picks up the new colours immediately.
        entry = self._current
        key = self._bus_key(entry)
        self._palettes[key] = list(chosen_colors)
        self._palette_dirty.add(key)
        _get_palette_bus().set_overworld_palette(key, list(chosen_colors))
        self._fe_pal_frame.setStyleSheet(self._DIRTY_SS)
        self._fe_pal_row.set_colors(list(chosen_colors), emit=False)
        # Trigger sheet-view repaint with the new palette so the user
        # sees the change immediately, before saving.
        self._show_detail(entry)
        self.modified.emit()
        QMessageBox.information(
            self, "Palette Applied",
            f"Loaded '{chosen_tag}' into {entry.name}.\n\n"
            "Save (Ctrl+S) to bake the new palette into the PNG\n"
            "and write the .pal file. The pixel indices stay as-is —\n"
            "only the colour table changes.")

    def _open_sprite_folder(self) -> None:
        folder = os.path.join(self._project_root, "graphics", "field_effects", "pics")
        if not os.path.isdir(folder):
            folder = os.path.join(self._project_root, "graphics")
        self._open_folder(folder)

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

    # ── save ──────────────────────────────────────────────────────────────────

    def has_unsaved_changes(self) -> bool:
        return bool(self._palette_dirty) or bool(self._sprite_png_dirty)

    def flush_to_disk(self) -> tuple[int, list[str]]:
        """Write dirty palettes to .pal files or bake them into PNGs."""
        ok = 0
        errors: list[str] = []

        # Build key→entry lookup
        key_to_entry: Dict[str, FieldEffectEntry] = {
            self._bus_key(e): e for e in self._entries
        }

        # Pass 1 — palette changes (write .pal or bake into PNG)
        for key in list(self._palette_dirty):
            entry = key_to_entry.get(key)
            if not entry:
                errors.append(f"fe-pal:{key} (no entry)")
                continue
            colors = self._palettes.get(key)
            if not colors:
                errors.append(f"fe-pal:{entry.name} (no palette in RAM)")
                continue

            wrote_pal = False
            if entry.pal_path:
                if write_jasc_pal(entry.pal_path, colors):
                    wrote_pal = True
                    ok += 1
                else:
                    errors.append(f"fe-pal:{entry.name}")
                    continue

            # If this field effect is covered by the dynamic palette
            # refactor (e.g. shadows), also write the .gbapal binary
            # the build reads via INCBIN. Without this, palette edits
            # in PorySuite would update the PNG preview but NOT reach
            # the game — the build would keep using the .gbapal from
            # whatever was extracted at DOWP apply time.
            try:
                from core import field_effect_palette_refactor as _fer
                from core.tilemap_data import _write_gbapal_file as _wgp
                _refactor = _fer.find_refactor_for_png(
                    os.path.relpath(entry.png_path, self._project_root).replace("\\", "/")
                ) if entry.png_path and self._project_root else None
                if _refactor:
                    _gbapal_abs = _fer.gbapal_path_for_refactor(self._project_root, _refactor)
                    if _wgp(_gbapal_abs, list(colors)):
                        ok += 1
            except Exception as _e:
                errors.append(f"fe-gbapal:{entry.name} ({_e})")

            # Always bake into the PNG — whether or not there's a separate
            # .pal file. This way opening the PNG in GIMP shows the
            # current colours, matching what the game renders.
            # export_indexed_png refuses non-indexed input (RGB PNGs
            # break the gbagfx build step), so we MUST guarantee
            # Format_Indexed8 here.
            img = self._sprite_imgs.get(entry.png_path)
            if (img is None or img.format() != QImage.Format.Format_Indexed8) \
                    and entry.png_path and os.path.isfile(entry.png_path):
                disk_img = QImage(entry.png_path)
                if not disk_img.isNull():
                    if disk_img.format() != QImage.Format.Format_Indexed8:
                        disk_img = disk_img.convertToFormat(
                            QImage.Format.Format_Indexed8)
                    img = disk_img
            if img is None or img.isNull() \
                    or img.format() != QImage.Format.Format_Indexed8:
                errors.append(f"fe-png:{entry.name} (cannot load as indexed)")
                continue
            try:
                export_indexed_png(img, colors, entry.png_path)
                self._palette_dirty.discard(key)
                self._sprite_png_dirty.discard(entry.png_path)
                if not wrote_pal:
                    # Counted as a save only when there's no .pal sibling
                    # (the .pal write already incremented ok above).
                    ok += 1
            except Exception as exc:
                errors.append(f"fe-png:{entry.name} ({exc})")

        # Pass 2 — Index-as-BG pixel remaps not already written above
        png_to_entry: Dict[str, FieldEffectEntry] = {e.png_path: e for e in self._entries}
        for png_path in list(self._sprite_png_dirty):
            entry = png_to_entry.get(png_path)
            img = self._sprite_imgs.get(png_path)
            if not entry or img is None:
                errors.append(f"fe-png:{png_path} (no data)")
                continue
            key = self._bus_key(entry)
            pal = self._palettes.get(key, [(0, 0, 0)] * 16)
            try:
                export_indexed_png(img, pal, png_path)
                self._sprite_png_dirty.discard(png_path)
                ok += 1
            except Exception as exc:
                errors.append(f"fe-png:{entry.name} ({exc})")

        if not self._palette_dirty and not self._sprite_png_dirty:
            self._fe_pal_frame.setStyleSheet("")
            self._rebuild_list()

        return ok, errors


# ── NPC sprite category filters ───────────────────────────────────────────────

CATEGORY_FILTERS = [
    ("all", "All"),
    ("people", "Players & NPCs"),
    ("pokemon", "Pokemon"),
    ("misc", "Objects & Items"),
]


class _DOWPApplyDialog(QDialog):
    """Confirmation dialog for enabling DOWP with tint sliders and risk warnings."""

    def __init__(self, risks: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Enable Dynamic Overworld Palettes")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Risk warnings
        if risks:
            warn_box = QGroupBox("⚠  Compatibility warnings")
            warn_box.setStyleSheet(
                "QGroupBox { border: 1px solid #cc8800; border-radius: 4px; "
                "color: #ffb74d; font-weight: bold; padding-top: 8px; }"
            )
            wl = QVBoxLayout(warn_box)
            for r in risks:
                lbl = QLabel(r)
                lbl.setWordWrap(True)
                lbl.setStyleSheet("color: #ffb74d; font-size: 10px;")
                wl.addWidget(lbl)
            layout.addWidget(warn_box)

        # What this does
        info = QLabel(
            "Enables dynamic palette loading for all overworld sprites:\n"
            "  • Each sprite loads its palette on demand when it appears on screen\n"
            "  • Sprites are no longer locked to the 4 shared NPC palette slots\n"
            "  • Up to 16 unique palettes can be active simultaneously\n"
            "  • Water reflections are tinted automatically using the values below\n\n"
            "Files modified:  src/event_object_movement.c  •  src/field_effect.c\n"
            "  src/field_effect_helpers.c  •  include/event_object_movement.h\n"
            "  include/field_effect.h"
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #aaa; font-size: 10px;")
        layout.addWidget(info)

        # Tint sliders
        tint_box = QGroupBox("Water reflection tint  (added to each colour channel, 0–15)")
        tl = QFormLayout(tint_box)
        self._spin_r = QSpinBox(); self._spin_r.setRange(0, 15); self._spin_r.setValue(5)
        self._spin_g = QSpinBox(); self._spin_g.setRange(0, 15); self._spin_g.setValue(5)
        self._spin_b = QSpinBox(); self._spin_b.setRange(0, 15); self._spin_b.setValue(10)
        for spin in (self._spin_r, self._spin_g, self._spin_b):
            spin.wheelEvent = lambda e: e.ignore()
        self._preview = QLabel()
        self._preview.setFixedSize(48, 24)
        self._preview.setToolTip("Sample mid-grey colour before (left) and after (right) tinting")

        tl.addRow("Red offset:",   self._spin_r)
        tl.addRow("Green offset:", self._spin_g)
        tl.addRow("Blue offset:",  self._spin_b)
        tl.addRow("Preview:", self._preview)
        layout.addWidget(tint_box)

        for spin in (self._spin_r, self._spin_g, self._spin_b):
            spin.valueChanged.connect(self._refresh_preview)
        self._refresh_preview()

        # Backup confirmation
        self._confirm_chk = QPushButton("☐  I have a git commit or backup of my project")
        self._confirm_chk.setCheckable(True)
        self._confirm_chk.setStyleSheet(
            "QPushButton { text-align:left; background:#222; border:1px solid #555; "
            "padding:6px; border-radius:3px; color:#aaa; }"
            "QPushButton:checked { border-color:#6a6; color:#8f8; }"
        )
        self._confirm_chk.toggled.connect(self._on_confirm_toggled)
        layout.addWidget(self._confirm_chk)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Apply Patch")
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

    def _refresh_preview(self):
        r0, g0, b0 = 16, 16, 16   # sample mid-grey (GBA 5-bit space)
        r1 = min(31, r0 + self._spin_r.value())
        g1 = min(31, g0 + self._spin_g.value())
        b1 = min(31, b0 + self._spin_b.value())
        # Scale to 8-bit for display
        def s(v): return (v << 3) | (v >> 2)
        from PyQt6.QtGui import QPixmap as _QP, QPainter as _QPA, QColor as _QC
        pm = _QP(48, 24)
        p = _QPA(pm)
        p.fillRect(0, 0, 24, 24, _QC(s(r0), s(g0), s(b0)))
        p.fillRect(24, 0, 24, 24, _QC(s(r1), s(g1), s(b1)))
        p.end()
        self._preview.setPixmap(pm)

    def _on_confirm_toggled(self, checked: bool):
        self._confirm_chk.setText(
            "☑  I have a git commit or backup of my project" if checked
            else "☐  I have a git commit or backup of my project"
        )
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(checked)

    def tint_values(self) -> tuple:
        return self._spin_r.value(), self._spin_g.value(), self._spin_b.value()


class _EmoteReviewDialog(QDialog):
    """Multi-select review dialog for the emote-upgrade sweep.

    Lists every sprite whose PNG has unused frame(s) beyond what its
    current anim table references.  Each row shows a thumbnail of the
    unused frame rendered with the sprite's current palette, so the
    user can see what pose they're about to wire up.

    Project-agnostic — works on any pokefirered fork.  The candidates
    are passed in; the dialog doesn't reach into any tab state.
    """

    def __init__(
        self,
        project_root: str,
        candidates: List,  # List[EmoteCandidate]
        palette_resolver,  # callable: tag -> List[Color]
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Wire Up the Extra Frame — Emote / VS-Seeker")
        self.setMinimumWidth(640)
        self.setMinimumHeight(480)
        self._candidates = candidates
        self._palette_resolver = palette_resolver
        self._project_root = project_root
        self._checkboxes: List[QCheckBox] = []
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        info = QLabel(
            "<p>Every sprite below has a frame on disk that isn't being "
            "used by its current animation table.</p>"
            "<p>Wiring it up creates an <b>ANIM_EMOTE</b> animation "
            "state pointing at that frame — usable in scripts via "
            "<code>objectevent_emote</code> (added in a follow-up "
            "release) and also serving as the fallback target for "
            "VS-seeker dispatch.</p>"
            "<p>Pick which sprites to upgrade. Each thumbnail shows the "
            "currently-unused frame rendered with that sprite's "
            "palette.</p>"
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #ccc; font-size: 11px;")
        layout.addWidget(info)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        container = QWidget()
        grid = QGridLayout(container)
        grid.setContentsMargins(4, 4, 4, 4)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)

        # Resolve graphics-info data once (palette tag per info_name) so
        # we can look up palettes without a full project re-scan.
        from ui.overworld_graphics_tab import _parse_graphics_info as _gpi
        info_data = _gpi(self._project_root)

        for row, cand in enumerate(self._candidates):
            cb = QCheckBox()
            cb.setChecked(True)
            self._checkboxes.append(cb)
            grid.addWidget(cb, row, 0)

            # Thumbnail of the first unused frame
            thumb = QLabel()
            thumb.setFixedSize(40, 40)
            thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            thumb.setStyleSheet(
                "background: #1a1a1a; border: 1px solid #333;"
            )
            info = info_data.get(cand.info_name, {})
            tag = info.get("paletteTag", "")
            palette = self._palette_resolver(tag) if tag else None
            pix = self._render_thumbnail(cand, palette)
            if pix:
                thumb.setPixmap(pix.scaled(
                    36, 36,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.FastTransformation,
                ))
            grid.addWidget(thumb, row, 1)

            label = QLabel(
                f"<b>{cand.info_name}</b>  "
                f"<span style='color:#888'>"
                f"{cand.frame_w}×{cand.frame_h}  "
                f"+{cand.extra_frames} unused frame"
                f"{'s' if cand.extra_frames != 1 else ''}"
                f"</span>"
            )
            label.setStyleSheet("font-size: 11px;")
            grid.addWidget(label, row, 2)

        grid.setColumnStretch(2, 1)
        container.setLayout(grid)
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)

        # Bulk-select buttons + dialog buttons
        bar = QHBoxLayout()
        all_btn = QPushButton("Select All")
        all_btn.clicked.connect(lambda: [
            cb.setChecked(True) for cb in self._checkboxes
        ])
        bar.addWidget(all_btn)
        none_btn = QPushButton("Deselect All")
        none_btn.clicked.connect(lambda: [
            cb.setChecked(False) for cb in self._checkboxes
        ])
        bar.addWidget(none_btn)
        bar.addStretch(1)
        layout.addLayout(bar)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
        )
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn:
            ok_btn.setText("Upgrade Selected")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _render_thumbnail(
        self, cand, palette: Optional[List[Color]],
    ) -> Optional[QPixmap]:
        if not cand.png_path or not os.path.isfile(cand.png_path):
            return None
        try:
            img = QImage(cand.png_path)
            if img.isNull():
                return None
            if img.format() != QImage.Format.Format_Indexed8:
                img = img.convertToFormat(QImage.Format.Format_Indexed8)
            pix = (_apply_palette_to_image(img, palette)
                   if palette else QPixmap.fromImage(img))
            if pix is None or pix.isNull():
                return None
            # Crop to the first unused frame.
            fw, fh = cand.frame_w, cand.frame_h
            idx = cand.frames_used
            x = (idx * fw) % max(1, pix.width())
            y = ((idx * fw) // max(1, pix.width())) * fh
            if x + fw <= pix.width() and y + fh <= pix.height():
                return pix.copy(x, y, fw, fh)
            return pix
        except Exception:
            return None

    def selected_info_names(self) -> List[str]:
        return [
            self._candidates[i].info_name
            for i, cb in enumerate(self._checkboxes)
            if cb.isChecked()
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

    # Amber stylesheet applied to the Palette groupbox when the current palette
    # has unsaved edits — matches the dirty-highlight pattern in other GFX tabs.
    _DIRTY_SS = (
        "QGroupBox { border: 1px solid #ffb74d; border-radius: 4px; }"
        "QGroupBox::title { color: #ffb74d; }"
    )

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
        # In-memory remapped sprite images: {gfx_const: QImage}
        # Populated only by the Index-as-Background path (pixel remaps).
        self._sprite_imgs: Dict[str, QImage] = {}
        self._sprite_png_dirty: set[str] = set()
        # Tag → .pal file path (JASC text) — what the editor UI reads and
        # writes for palette edits.  Always set even if the file doesn't
        # exist yet; the save path creates it via the paired write.
        self._pal_paths: Dict[str, str] = {}
        # Tag → .gbapal file path (binary) — what the build INCBINs.
        # Save writes this alongside .pal so the two formats stay locked
        # in sync.  Without this, .pal edits never reach in-game rendering.
        self._gbapal_paths: Dict[str, str] = {}
        # Reverse lookup: tag → PalettePool
        self._pools_by_tag: Dict[str, PalettePool] = {}

        # Debounce timer for sprite list refresh during rapid palette edits
        self._list_refresh_timer = QTimer(self)
        self._list_refresh_timer.setSingleShot(True)
        self._list_refresh_timer.setInterval(400)
        self._list_refresh_timer.timeout.connect(self._refresh_visible_thumbnails)

        self._build_ui()

        # Subscribe to bus so cross-tab palette edits invalidate our cache.
        _get_palette_bus().palette_changed.connect(self._on_bus_palette_changed)

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

        # Amber notice strip — surfaces sprites with unused 10th frames
        # detected by the emote scan.  Hidden by default; only shown
        # after `load_project` runs the scan and finds candidates.
        # See `_refresh_emote_notice` for the trigger logic.
        self._emote_notice_widget = QWidget()
        self._emote_notice_widget.setStyleSheet(
            "QWidget { background: #3d2e00; border: 1px solid #ffb74d; "
            "border-radius: 4px; }"
        )
        notice_layout = QHBoxLayout(self._emote_notice_widget)
        notice_layout.setContentsMargins(8, 6, 8, 6)
        notice_layout.setSpacing(8)
        self._emote_notice_label = QLabel("")
        self._emote_notice_label.setStyleSheet(
            "color: #ffb74d; background: transparent; border: none; "
            "font-size: 11px;"
        )
        self._emote_notice_label.setWordWrap(True)
        notice_layout.addWidget(self._emote_notice_label, 1)
        self._emote_review_btn = QPushButton("Review & Upgrade…")
        self._emote_review_btn.setStyleSheet(
            "QPushButton { background: #5a4400; color: #ffe0a0; "
            "border: 1px solid #ffb74d; padding: 4px 10px; "
            "border-radius: 3px; font-weight: bold; }"
            "QPushButton:hover { background: #7a5a00; }"
        )
        self._emote_review_btn.clicked.connect(self._open_emote_review_dialog)
        notice_layout.addWidget(self._emote_review_btn, 0)
        self._emote_notice_widget.setVisible(False)
        lv.addWidget(self._emote_notice_widget, 0)

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

        # Delete-sprite button.  Lives directly under "Add New Sprite"
        # so the two destructive-ish actions sit together visually.
        # Always present in the layout; greys out when no sprite is
        # selected.  Click pops a multi-step confirmation before any
        # source file is touched — refusing to silently destroy data
        # is part of the project-agnostic safety contract.
        self._delete_sprite_btn = QPushButton("Delete Selected Sprite…")
        self._delete_sprite_btn.setStyleSheet(
            "QPushButton { background: #4d2222; color: #ffa0a0; "
            "border: 1px solid #804040; padding: 6px; border-radius: 3px; "
            "font-weight: bold; }"
            "QPushButton:hover { background: #6d3232; }"
            "QPushButton:disabled { background: #2a2222; color: #663333; "
            "border-color: #4a3030; }"
        )
        self._delete_sprite_btn.setToolTip(
            "Remove the currently-selected overworld sprite from the "
            "project — deletes its GraphicsInfo, pointer entry, "
            "OBJ_EVENT_GFX_ #define, pic table, and (when uniquely "
            "owned) its palette tag + .png/.gbapal/.pal files. Shows "
            "exactly what will be touched before doing anything."
        )
        self._delete_sprite_btn.setEnabled(False)
        self._delete_sprite_btn.clicked.connect(self._delete_selected_sprite)
        lv.addWidget(self._delete_sprite_btn)

        # Dynamic Overworld Palettes buttons
        self._dowp_btn = QPushButton("Enable Dynamic Palettes…")
        self._dowp_btn.setToolTip(
            "Apply the Dynamic Overworld Palettes patch to this project.\n"
            "Allows every overworld sprite to have its own unique palette\n"
            "instead of being locked to 4 shared NPC palette slots.\n\n"
            "This is a reversible change — the Disable button restores the originals."
        )
        self._dowp_btn.setStyleSheet(
            "QPushButton { background: #2a4d2a; color: #8f8; border: 1px solid #4a8a4a; "
            "padding: 6px; border-radius: 3px; font-weight: bold; }"
            "QPushButton:hover { background: #3a6d3a; }"
            "QPushButton:disabled { background: #333; color: #888; border-color: #555; }"
        )
        self._dowp_btn.clicked.connect(self._enable_dynamic_palettes)
        lv.addWidget(self._dowp_btn)

        self._dowp_disable_btn = QPushButton("Disable Dynamic Palettes…")
        self._dowp_disable_btn.setToolTip(
            "Reverse the Dynamic Overworld Palettes patch and restore the\n"
            "original source files. Requires a clean build afterwards."
        )
        self._dowp_disable_btn.setStyleSheet(
            "QPushButton { background: #4d2a2a; color: #f88; border: 1px solid #8a4a4a; "
            "padding: 6px; border-radius: 3px; font-weight: bold; }"
            "QPushButton:hover { background: #6d3a3a; }"
        )
        self._dowp_disable_btn.setVisible(False)
        self._dowp_disable_btn.clicked.connect(self._disable_dynamic_palettes)
        lv.addWidget(self._dowp_disable_btn)

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
        self._pal_frame = pal_frame  # held for amber dirty highlight
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

        # Drag-reorderable swatches with right-click Index-as-Background,
        # matching the palette editing feel of the Trainer/Species GFX tabs.
        self._pal_row = DraggablePaletteRow()
        pf.addWidget(self._pal_row)

        pal_btn_row = QHBoxLayout()
        pal_btn_row.setSpacing(6)
        self._import_btn = QPushButton("Import Palette from PNG…")
        self._import_btn.setToolTip(
            "Extract palette from an indexed PNG and apply it to\n"
            "this sprite's palette. If the palette is shared, all\n"
            "sprites using it will be affected (or, under DOWP, you'll\n"
            "be offered the option to fork a new per-sprite palette)."
        )
        self._import_manual_btn = QPushButton("Import Manually…")
        self._import_manual_btn.setToolTip(
            "Open the manual palette picker on a PNG.\n"
            "You choose which colours land in which slot, set the\n"
            "BG/transparent slot, and reorder freely.  Works on any\n"
            "PNG (indexed or full-colour)."
        )
        self._import_pal_btn = QPushButton("Import from .pal…")
        self._import_pal_btn.setToolTip(
            "Load a JASC .pal file (16 RGB colours) and apply it\n"
            "to this sprite's palette."
        )
        self._open_folder_btn = QPushButton("Open Palettes Folder")
        self._open_folder_btn.setToolTip("Open the overworld palettes directory.")
        pal_btn_row.addWidget(self._import_btn)
        pal_btn_row.addWidget(self._import_manual_btn)
        pal_btn_row.addWidget(self._import_pal_btn)
        pal_btn_row.addWidget(self._open_folder_btn)
        pal_btn_row.addStretch(1)
        pf.addLayout(pal_btn_row)

        rv.addWidget(pal_frame, 0)

        # Per-sprite emote upgrade section.  Empty placeholder by default;
        # populated by `_show_sprite_detail` when the selected sprite is
        # a candidate per the project-open scan.  Hidden when not.
        self._emote_per_sprite_container = QWidget()
        self._emote_per_sprite_container.setVisible(False)
        _epsc_layout = QVBoxLayout(self._emote_per_sprite_container)
        _epsc_layout.setContentsMargins(0, 0, 0, 0)
        _epsc_layout.setSpacing(0)
        rv.addWidget(self._emote_per_sprite_container, 0)

        splitter.addWidget(right)
        splitter.setSizes([340, 660])

        # Wrap the NPC sprite content and field effect tab in a QTabWidget
        npc_container = QWidget()
        npc_layout = QVBoxLayout(npc_container)
        npc_layout.setContentsMargins(0, 0, 0, 0)
        npc_layout.addWidget(splitter)

        self._ow_tabs = QTabWidget()
        self._ow_tabs.addTab(npc_container, "NPC Sprites")

        self._fe_tab = FieldEffectSpritesTab()
        self._fe_tab.modified.connect(self.modified)
        self._ow_tabs.addTab(self._fe_tab, "Field Effect Sprites")

        outer.addWidget(self._ow_tabs)

        # ── Wire signals ────────────────────────────────────────────────
        self._pal_row.colors_changed.connect(self._on_palette_edited)
        self._pal_row.palette_reordered.connect(self._on_palette_reordered)
        self._pal_row.swatch_set_as_bg.connect(self._on_set_swatch_as_bg)
        self._import_btn.clicked.connect(self._import_palette_from_png)
        self._import_manual_btn.clicked.connect(self._import_palette_from_png_manual)
        self._import_pal_btn.clicked.connect(self._import_palette_from_pal)
        self._open_folder_btn.clicked.connect(self._open_palettes_folder)
        self._open_sprite_folder_btn.clicked.connect(self._open_current_sprite_folder)
        self._cat_combo.currentIndexChanged.connect(self._rebuild_grid)
        self._search.textChanged.connect(self._rebuild_grid)

    # ────────────────────────────────────────────────────────── loading ──
    def load(self, project_root: str) -> None:
        """Parse C headers and populate the sprite browser."""
        # Kill any pending debounce refresh so a stale timer can't re-dirty
        # the grid after we clear the dirty state below.
        self._list_refresh_timer.stop()

        # Unconditionally evict every existing thumbnail widget from the grid
        # BEFORE anything that could throw.  Old amber-bordered widgets carry
        # embedded CSS that Qt will keep rendering at their last position until
        # the C++ object is actually destroyed.  setParent(None) is immediate —
        # unlike deleteLater() it removes the widget from the screen right now,
        # so even if the rebuild below fails the user sees an empty grid, not
        # stale amber thumbnails.
        while self._grid_layout.count():
            item = self._grid_layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)

        self._project_root = project_root
        self._pools, self._all_sprites = build_overworld_data(project_root)
        self._palettes.clear()
        self._palette_dirty.clear()
        self._sprite_imgs.clear()
        self._sprite_png_dirty.clear()
        self._pal_paths.clear()
        self._gbapal_paths.clear()
        self._pools_by_tag.clear()
        self._current_sprite = None
        # Delete button is meaningless without a selected sprite.
        if hasattr(self, "_delete_sprite_btn"):
            self._delete_sprite_btn.setEnabled(False)

        # Reset ALL right-panel visual state to clean.  Without this the
        # palette-frame amber highlight, stale swatch colours, and stale sprite
        # info persist visually after F5 even though the in-memory dirty sets
        # have been cleared.  Mirrors what TrainerGraphicsTab.load() does
        # (clears _dirty_dot, resets _sel_lbl, clears _sprite_lbl).
        self._pal_frame.setStyleSheet("")
        self._pal_info_lbl.setText("Select a sprite to view its palette")
        self._sheet_lbl.clear()
        self._sheet_lbl.setText("Select a sprite")
        self._sheet_info_lbl.setText("")
        # Reset swatch row to all-black — safe because set_colors uses emit=False
        # so colors_changed / _on_palette_edited never fires here.
        self._pal_row.set_colors([(0, 0, 0)] * 16)

        # Check DOWP status
        self._update_dowp_status()

        # Cache palette paths and pools
        for pool in self._pools:
            self._pal_paths[pool.tag_name] = pool.pal_path
            if pool.gbapal_path:
                self._gbapal_paths[pool.tag_name] = pool.gbapal_path
            self._pools_by_tag[pool.tag_name] = pool

        # Build flat sorted sprite list
        self._sorted_sprites = sorted(
            self._all_sprites.values(), key=lambda s: s.display_name
        )

        # Build the grid under _loading guard so any signal that incidentally
        # fires during thumbnail construction is treated as a load-time event
        # and doesn't emit modified or mark anything dirty.
        self._loading = True
        try:
            self._rebuild_grid()
        finally:
            self._loading = False

        # Load field effect sprites tab
        self._fe_tab.load(project_root)

        # Emote / VS-seeker frame-9 sweep.  For projects whose vanilla NPC
        # PNGs already carry a 10th frame the engine doesn't currently
        # reference, surface a notice so the user can wire it up with one
        # click.  Project-agnostic: this only flags sprites whose
        # current Standard-family anim table doesn't already cover the
        # extra frames.  A project that's hand-rolled its anim tables
        # (like the one this is being tested against) gets zero flagged
        # and the notice strip stays hidden.
        try:
            from core.anim_table_upgrade import scan_emote_candidates
            self._emote_report = scan_emote_candidates(project_root)
        except Exception as exc:
            self._emote_report = None
            try:
                from logging import getLogger
                getLogger("OverworldGraphics").warning(
                    "Emote scan failed: %s", exc, exc_info=True,
                )
            except Exception:
                pass
        self._refresh_emote_notice()

    def _refresh_emote_notice(self) -> None:
        """Show/hide the amber notice strip based on the scan result.

        Called after `load_project` finishes and again after any upgrade
        completes (the post-upgrade scan should show fewer candidates so
        the notice text updates or disappears).
        """
        if not hasattr(self, "_emote_notice_widget"):
            return  # UI not built yet
        report = getattr(self, "_emote_report", None)
        if not report or not report.candidates:
            self._emote_notice_widget.setVisible(False)
            return
        n = len(report.candidates)
        self._emote_notice_label.setText(
            f"⚠ {n} sprite{'s' if n != 1 else ''} have an unused 10th "
            f"frame in their PNG that isn't wired to any animation."
        )
        self._emote_notice_widget.setVisible(True)

    def _open_emote_review_dialog(self) -> None:
        """Open the multi-select review dialog for emote candidates."""
        report = getattr(self, "_emote_report", None)
        if not report or not report.candidates:
            return
        dlg = _EmoteReviewDialog(
            self._project_root,
            report.candidates,
            self._get_palette_for_sprite_by_tag,
            parent=self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        selected = dlg.selected_info_names()
        if not selected:
            return
        self._run_emote_upgrade_batch(selected)

    def _get_palette_for_sprite_by_tag(self, tag: str) -> List[Color]:
        """Public-ish helper so the review dialog can fetch a palette
        for thumbnail rendering without reaching into private state."""
        if tag in self._palettes:
            return self._palettes[tag]
        bus_colors = _get_palette_bus().get_overworld_palette(tag)
        if bus_colors:
            return bus_colors
        gbapal_path = self._gbapal_paths.get(tag, "")
        if gbapal_path:
            from core.overworld_palette_io import read_palette_pair
            loaded = read_palette_pair(gbapal_path)
            if loaded:
                return loaded
        return [(0, 0, 0)] * 16

    def _run_emote_upgrade_batch(self, info_names: List[str]) -> None:
        """Run `upgrade_sprite_to_emote` for each selected sprite,
        collect outcomes, and reload the tab so the new state shows.
        """
        from core.anim_table_upgrade import upgrade_sprite_to_emote
        ok_count = 0
        applied_all: List[str] = []
        errors_all: List[Tuple[str, str]] = []
        for info_name in info_names:
            try:
                success, applied, errors = upgrade_sprite_to_emote(
                    self._project_root, info_name,
                )
            except Exception as exc:
                errors_all.append((info_name, f"unexpected: {exc}"))
                continue
            applied_all.extend(applied)
            if success:
                ok_count += 1
            else:
                for e in errors:
                    errors_all.append((info_name, e))

        summary_lines = [
            f"Upgraded {ok_count} of {len(info_names)} sprites."
        ]
        if errors_all:
            summary_lines.append("")
            summary_lines.append("Errors:")
            for info_name, e in errors_all[:10]:
                summary_lines.append(f"  • {info_name}: {e}")
            if len(errors_all) > 10:
                summary_lines.append(
                    f"  … and {len(errors_all) - 10} more (see log)"
                )
        summary_lines.append("")
        summary_lines.append(
            "Run Make Modern (Ctrl+Shift+M) to rebuild — the new "
            "anim slot is now usable in scripts as ANIM_EMOTE."
        )

        QMessageBox.information(
            self, "Emote Upgrade Complete",
            "\n".join(summary_lines),
        )

        # Reload so the survey updates and per-sprite detail panels
        # reflect the new state.
        self.load(self._project_root)

    def _build_per_sprite_emote_section(self, entry: SpriteEntry) -> Optional[QWidget]:
        """Return a widget showing the 10th-frame thumbnail + an Upgrade
        button for `entry`, OR None if this sprite isn't a candidate.

        Called from `_show_sprite_detail` so the section appears only
        for sprites that the scan flagged.
        """
        report = getattr(self, "_emote_report", None)
        if not report:
            return None
        cand = next(
            (c for c in report.candidates if c.info_name == entry.info_name),
            None,
        )
        if not cand:
            return None

        box = QGroupBox("Extra Frame Available")
        box.setStyleSheet(
            "QGroupBox { border: 1px solid #ffb74d; border-radius: 4px; "
            "margin-top: 6px; padding-top: 10px; }"
            "QGroupBox::title { color: #ffb74d; subcontrol-origin: margin; "
            "left: 8px; padding: 0 4px; }"
        )
        layout = QVBoxLayout(box)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        info_lbl = QLabel(
            f"This sprite's PNG has {cand.frames_on_disk} frames. "
            f"Frames 0–{cand.frames_used - 1} are wired to the standard "
            f"walk cycle. Frame {cand.frames_used} is on disk but unused."
        )
        info_lbl.setWordWrap(True)
        info_lbl.setStyleSheet("color: #ccc; font-size: 11px;")
        layout.addWidget(info_lbl)

        # Frame-N thumbnail (N = first unused frame index).
        thumb = QLabel()
        thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumb.setFixedSize(64, 64)
        thumb.setStyleSheet("background: #1a1a1a; border: 1px solid #333;")
        palette = self._get_palette_for_sprite(entry)
        pix = self._render_frame_thumbnail(
            cand.png_path, palette, cand.frames_used,
            cand.frame_w, cand.frame_h,
        )
        if pix:
            thumb.setPixmap(pix.scaled(
                60, 60,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation,
            ))
        layout.addWidget(thumb, 0, Qt.AlignmentFlag.AlignCenter)

        btn = QPushButton("Wire frame as emote / VS-seeker pose")
        btn.setToolTip(
            "Adds an ANIM_EMOTE animation state that plays this frame.\n"
            "The slot also serves as the fallback for VS-seeker dispatch."
        )
        btn.clicked.connect(
            lambda _checked, name=entry.info_name: (
                self._run_emote_upgrade_batch([name])
            )
        )
        layout.addWidget(btn)

        return box

    def _render_frame_thumbnail(
        self,
        png_path: str,
        palette: Optional[List[Color]],
        frame_idx: int,
        frame_w: int,
        frame_h: int,
    ) -> Optional[QPixmap]:
        """Render a single frame from a sprite sheet with the given
        palette — used by the emote review dialog and per-sprite panel.
        """
        if not png_path or not os.path.isfile(png_path):
            return None
        try:
            img = QImage(png_path)
            if img.isNull():
                return None
            if img.format() != QImage.Format.Format_Indexed8:
                img = img.convertToFormat(QImage.Format.Format_Indexed8)
            if palette:
                pix = _apply_palette_to_image(img, palette)
            else:
                pix = QPixmap.fromImage(img)
            if pix is None or pix.isNull():
                return None
            # crop to the requested frame
            x = (frame_idx * frame_w) % max(1, pix.width())
            y = ((frame_idx * frame_w) // max(1, pix.width())) * frame_h
            if x + frame_w <= pix.width() and y + frame_h <= pix.height():
                return pix.copy(x, y, frame_w, frame_h)
            return pix
        except Exception:
            return None

    def _get_palette_for_sprite(self, entry: SpriteEntry) -> Optional[List[Color]]:
        """Get the palette for a sprite — RAM-first via the bus, then disk.

        The bus holds any unsaved edits made in this tab or another tab.  We
        must check it before hitting the .pal file so the viewer always shows
        the current in-RAM state rather than a stale on-disk copy.
        """
        tag = entry.palette_tag
        if tag not in self._palettes:
            # 1) Check bus — another tab (or a previous edit in this tab) may
            #    have already pushed a palette for this tag.
            bus_colors = _get_palette_bus().get_overworld_palette(tag)
            if bus_colors:
                self._palettes[tag] = bus_colors
            else:
                # 2) Fall back to disk.  Use the paired-IO read so we
                #    pick up whichever of (.pal, .gbapal) is the current
                #    representation — the helper prefers the JASC sibling
                #    when present and falls back to the binary file
                #    (including the corrupt-by-prior-bug case where the
                #    .gbapal contains JASC text from the broken save
                #    path that this release fixes).
                gbapal_path = self._gbapal_paths.get(tag, "")
                colors: List[Color] = []
                if gbapal_path:
                    from core.overworld_palette_io import read_palette_pair
                    loaded = read_palette_pair(gbapal_path)
                    if loaded:
                        colors = loaded
                if not colors:
                    # Legacy fallback for the rare case where _gbapal_paths
                    # has no entry but _pal_paths does (e.g. ad-hoc tag
                    # added by a non-project-open code path).
                    pal_path = self._pal_paths.get(tag, "")
                    if pal_path:
                        colors = read_jasc_pal(pal_path)
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
        _get_palette_bus().set_overworld_palette(tag, colors)
        self._pal_frame.setStyleSheet(self._DIRTY_SS)
        self.modified.emit()
        # Refresh the selected sprite detail immediately
        self._show_sprite_detail(self._current_sprite)
        # Defer full grid refresh so shared-palette thumbnails update
        if not self._list_refresh_timer.isActive():
            self._list_refresh_timer.start(400)

    def _on_palette_reordered(self, from_idx: int, to_idx: int) -> None:
        """User dragged a swatch — swap slots in the palette.
        Pixels keep their index values so only colour assignment changes."""
        if self._loading or not self._current_sprite:
            return
        n = 16
        if from_idx == to_idx or not (0 <= from_idx < n) or not (0 <= to_idx < n):
            return
        tag = self._current_sprite.palette_tag
        pal = list(self._palettes.get(tag) or [(0, 0, 0)] * n)
        while len(pal) < n:
            pal.append((0, 0, 0))
        pal[from_idx], pal[to_idx] = pal[to_idx], pal[from_idx]
        self._palettes[tag] = pal
        self._palette_dirty.add(tag)
        _get_palette_bus().set_overworld_palette(tag, pal)
        self._pal_frame.setStyleSheet(self._DIRTY_SS)
        self._loading = True
        try:
            self._pal_row.set_colors(pal)
        finally:
            self._loading = False
        self.modified.emit()
        self._show_sprite_detail(self._current_sprite)
        if not self._list_refresh_timer.isActive():
            self._list_refresh_timer.start(400)

    def _on_set_swatch_as_bg(self, slot: int) -> None:
        """Right-click → Index as Background: make slot 0 the transparent slot.
        Swaps pixel values slot↔0 in the sprite PNG and swaps palette entries
        lockstep — the rendered image is unchanged but slot 0 is now transparent.
        This is the only path that mutates PNG pixel data."""
        if self._loading or not self._current_sprite:
            return
        if slot <= 0 or slot >= 16:
            return
        entry = self._current_sprite
        tag = entry.palette_tag
        n = 16
        pal = list(self._palettes.get(tag) or [(0, 0, 0)] * n)
        while len(pal) < n:
            pal.append((0, 0, 0))

        # Load the QImage for this sprite if we don't have it in RAM yet.
        gfx_key = entry.gfx_const
        if gfx_key not in self._sprite_imgs:
            if entry.png_path and os.path.isfile(entry.png_path):
                img = QImage(entry.png_path)
                if not img.isNull():
                    if img.format() != QImage.Format.Format_Indexed8:
                        img = img.convertToFormat(QImage.Format.Format_Indexed8)
                    self._sprite_imgs[gfx_key] = img

        img = self._sprite_imgs.get(gfx_key)
        if img is not None:
            try:
                new_img, _ = swap_palette_entries(img, pal, slot, 0)
                self._sprite_imgs[gfx_key] = new_img
                self._sprite_png_dirty.add(gfx_key)
            except Exception as exc:
                QMessageBox.warning(
                    self, "Index as Background Error",
                    f"Failed to remap sprite pixels:\n{exc}",
                )
                return
        else:
            QMessageBox.information(
                self, "No Sprite PNG",
                "This sprite has no on-disk PNG to remap.\n"
                "The palette-only swap has still been applied.",
            )

        # Lockstep palette swap — slot 0 convention is transparency.
        pal[0], pal[slot] = pal[slot], pal[0]
        self._palettes[tag] = pal
        self._loading = True
        try:
            self._pal_row.set_colors(pal)
        finally:
            self._loading = False
        self._palette_dirty.add(tag)
        _get_palette_bus().set_overworld_palette(tag, pal)
        self._pal_frame.setStyleSheet(self._DIRTY_SS)
        self.modified.emit()
        self._show_sprite_detail(self._current_sprite)
        if not self._list_refresh_timer.isActive():
            self._list_refresh_timer.start(400)

    def _on_bus_palette_changed(self, category: str, key: str) -> None:
        """Another tab pushed a palette update to the bus.
        If it's an overworld palette we have cached, evict the cache entry
        so the next render call picks up the fresh value from the bus."""
        if category != CAT_OVERWORLD:
            return
        # Evict local cache so _get_palette_for_sprite re-reads from bus.
        self._palettes.pop(key, None)
        # Debounce a thumbnail refresh — don't spam on rapid edits.
        if not self._list_refresh_timer.isActive():
            self._list_refresh_timer.start(400)
        # If the currently-displayed sprite uses this tag, refresh detail too.
        if (self._current_sprite and
                self._current_sprite.palette_tag == key):
            palette = self._get_palette_for_sprite(self._current_sprite)
            self._loading = True
            try:
                if palette:
                    self._pal_row.set_colors(palette)
            finally:
                self._loading = False
            self._show_sprite_detail(self._current_sprite)

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
        is_dirty = entry.palette_tag in self._palette_dirty
        if is_selected:
            border_color = "#1565c0"
        elif is_dirty:
            border_color = "#ffb74d"
        else:
            border_color = "#333"
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

        # Always render through the palette — never load the raw PNG directly,
        # because the PNG's baked-in colour table may be stale relative to
        # whatever the user (or another tab) has edited in RAM.
        # If the gfx_const has a remapped QImage from an Index-as-BG op, use
        # that as the source so unsaved pixel changes are visible immediately.
        pix = None
        remapped_img = self._sprite_imgs.get(entry.gfx_const)
        if palette and remapped_img:
            pix = _reskin_overworld_img(remapped_img, palette)
        elif palette and entry.png_path:
            pix = _reskin_overworld(entry.png_path, palette)

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
        # Enable the Delete button now that a sprite is selected.
        if hasattr(self, "_delete_sprite_btn"):
            self._delete_sprite_btn.setEnabled(True)
        # Rebuild grid to update selection highlight
        self._rebuild_grid()

    def _delete_selected_sprite(self) -> None:
        """Delete the currently-selected overworld sprite from the project.

        Two-stage confirmation: shows a preview of every source file
        the operation will touch + every file that will be deleted,
        then asks for an explicit Yes before mutating anything.  The
        backend (`delete_overworld_sprite`) is atomic per file and
        garbage-free (no .bak/.tmp left behind).
        """
        entry = self._current_sprite
        if entry is None or not self._project_root:
            return

        # Pre-flight: show the user exactly what's about to happen.
        # We display the sprite's display name, gfx_const, png path,
        # palette tag, and any other sprites that share its pic table /
        # palette tag (those won't be touched).
        info_name = entry.info_name
        pic_neighbours = sum(
            1 for s in self._all_sprites.values()
            if s is not entry and getattr(s, "info_name", None)
            and s.info_name != info_name
            # Heuristic: shared pic_table → same image asset
            # (we can't import the parsed pic_table mapping here cheaply;
            # the backend re-parses for correctness)
        )
        pool = self._pools_by_tag.get(entry.palette_tag)
        shared_pal = len(pool.sprites) - 1 if pool else 0

        msg = (
            f"<b>Delete overworld sprite '{entry.display_name}'</b>"
            f" ({entry.gfx_const})?<br><br>"
            f"This is reversible only by re-creating the sprite from "
            f"scratch.<br><br>"
            f"<b>Will be removed from source:</b><br>"
            f"&nbsp;&nbsp;• <code>gObjectEventGraphicsInfo_{info_name}</code> "
            f"block<br>"
            f"&nbsp;&nbsp;• Its pointer-table entry + forward declaration<br>"
            f"&nbsp;&nbsp;• <code>{entry.gfx_const}</code> #define "
            f"(other gfx constants will be renumbered down by 1)<br>"
            f"&nbsp;&nbsp;• Its <code>sPicTable_*</code> block "
            f"(if no other sprite uses it)<br>"
            f"&nbsp;&nbsp;• Its <code>gObjectEventPic_*</code> INCBIN "
            f"(if no other pic table references it)<br>"
        )
        if shared_pal > 0:
            msg += (
                f"<br><b>Palette '{entry.palette_tag}' is shared with "
                f"{shared_pal} other sprite(s) — it will be left "
                f"alone.</b><br>"
            )
        else:
            msg += (
                f"<br><b>Palette '{entry.palette_tag}' is unique to "
                f"this sprite — its #define, "
                f"<code>sObjectEventSpritePalettes</code> entry, INCBIN, "
                f"and <code>.gbapal</code>/<code>.pal</code> files will "
                f"also be removed.</b><br>"
            )
        msg += (
            f"<br><b>On-disk files removed</b> "
            f"(only if not referenced elsewhere):<br>"
            f"&nbsp;&nbsp;• <code>{os.path.relpath(entry.png_path, self._project_root)}</code><br>"
            f"&nbsp;&nbsp;• The matching <code>.4bpp</code> build "
            f"artefact, <code>.gbapal</code>, and <code>.pal</code>"
        )

        box = QMessageBox(self)
        box.setWindowTitle("Delete Sprite")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(msg)
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.Cancel,
        )
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return

        try:
            from core.overworld_sprite_creator import delete_overworld_sprite
            success, applied, errors = delete_overworld_sprite(
                self._project_root, info_name, delete_files=True,
            )
        except Exception as exc:
            QMessageBox.critical(
                self, "Delete Failed",
                f"Couldn't delete sprite — operation aborted:\n\n{exc}",
            )
            return

        if not success or errors:
            err_lines = "\n".join(f"  • {e}" for e in errors)
            apl_lines = "\n".join(f"  + {a}" for a in applied) or "  (none)"
            QMessageBox.warning(
                self, "Delete Partially Completed",
                f"Some steps failed.  The project may be in a partial "
                f"state — review the source changes before rebuilding.\n\n"
                f"Applied:\n{apl_lines}\n\nErrors:\n{err_lines}",
            )
            # Reload anyway so the UI reflects whatever did land.
            self.load(self._project_root)
            return

        QMessageBox.information(
            self, "Sprite Deleted",
            f"Removed '{entry.display_name}' ({entry.gfx_const}).\n\n"
            + ("Changes:\n" + "\n".join(f"  + {a}" for a in applied)
               if applied else "")
            + "\n\nClick Make Modern (Ctrl+Shift+M) to rebuild.",
        )
        # Reload the tab so the grid + pools rebuild without this sprite.
        self.load(self._project_root)

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

        # Reflect dirty state on the palette groupbox BEFORE populating
        # swatches.  setStyleSheet() on a parent triggers a full CSS
        # re-evaluation of all children; calling it after set_colors()
        # would clear the freshly-set swatch backgrounds.  With the
        # DragSwatch CSS-based _refresh() this is now a belt-and-braces
        # guard rather than strictly necessary, but the order still matters.
        if tag in self._palette_dirty:
            self._pal_frame.setStyleSheet(self._DIRTY_SS)
        else:
            self._pal_frame.setStyleSheet("")

        self._loading = True
        try:
            if palette:
                self._pal_row.set_colors(palette)
        finally:
            self._loading = False

    def _show_sprite_detail(self, entry: SpriteEntry) -> None:
        """Show large sprite sheet + animation for the selected sprite."""
        palette = self._get_palette_for_sprite(entry)

        # Large sheet view — render through RAM palette, never raw QPixmap.
        # Prefer the in-memory remapped image if this sprite had an
        # Index-as-BG operation (pixel data different from on-disk PNG).
        pix = None
        remapped_img = self._sprite_imgs.get(entry.gfx_const)
        if palette and remapped_img:
            pix = _reskin_overworld_img(remapped_img, palette)
        elif palette and entry.png_path:
            pix = _reskin_overworld(entry.png_path, palette)

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

        # Per-sprite emote upgrade section — shown only when the scan
        # flagged THIS sprite as a candidate.  Built fresh on each
        # selection so the thumbnail uses the current palette.
        self._refresh_per_sprite_emote_section(entry)

    def _refresh_per_sprite_emote_section(self, entry: SpriteEntry) -> None:
        """Populate (or hide) the per-sprite emote upgrade section."""
        container = getattr(self, "_emote_per_sprite_container", None)
        if container is None:
            return
        # Clear out any previous widget
        layout = container.layout()
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        # Build a fresh section if this sprite is a candidate
        section = self._build_per_sprite_emote_section(entry)
        if section is None:
            container.setVisible(False)
            return
        layout.addWidget(section)
        container.setVisible(True)

    def _import_palette_from_png(self) -> None:
        """Import a 16-colour palette from an indexed PNG.

        When DOWP is enabled AND the current sprite shares its palette
        with other sprites, the user is offered three choices:

          1. **Apply to this sprite only** (default under DOWP): fork a
             new unique palette tag for this sprite alone, leaving the
             other sharing sprites untouched.  This is the natural DOWP
             workflow — that's what DOWP buys you, per-sprite palettes
             without the 4-slot ceiling.
          2. **Apply to all N sharing sprites**: legacy behaviour —
             rewrite the shared palette data, every sprite that points
             at the tag is updated.
          3. **Cancel** — back out without changes.

        When DOWP is off, or when the palette isn't shared, the old
        2-option flow is preserved (no fork option exposed; the fork
        operation only makes sense under DOWP).
        """
        self._do_palette_import_from_png(manual=False)

    def _import_palette_from_png_manual(self) -> None:
        """Same as `_import_palette_from_png` but routes the source PNG
        through the shared manual indexer first so the user can pick
        which colours land in which slot (and slot 0 = BG/transparent).
        """
        self._do_palette_import_from_png(manual=True)

    def _do_palette_import_from_png(self, manual: bool) -> None:
        if not self._current_sprite:
            QMessageBox.information(
                self, "No Sprite Selected",
                "Select a sprite first, then import a palette for it.",
            )
            return

        tag = self._current_sprite.palette_tag
        pool = self._pools_by_tag.get(tag)
        shared_count = len(pool.sprites) if pool else 0

        # ── Fork-vs-apply-all decision (only relevant under DOWP) ─────
        # We collect this BEFORE the file picker so the user can cancel
        # without being asked to pick a file first.
        dowp_on = False
        try:
            from core.dynamic_ow_pal_patch import is_dowp_enabled
            dowp_on = is_dowp_enabled(self._project_root)
        except Exception:
            dowp_on = False

        fork_mode = False  # True = forge a new unique tag for this sprite
        if pool and shared_count > 1:
            if dowp_on:
                # Three-way dialog: fork / apply-to-all / cancel.
                box = QMessageBox(self)
                box.setWindowTitle("Shared Palette")
                box.setIcon(QMessageBox.Icon.Question)
                box.setText(
                    f"This sprite's palette ({pool.display_name}) is "
                    f"shared by {shared_count} sprites."
                )
                box.setInformativeText(
                    "Dynamic Overworld Palettes is enabled, so you can "
                    "give this sprite its own unique palette without "
                    "affecting the others.\n\n"
                    "How would you like to apply the imported palette?"
                )
                btn_fork = box.addButton(
                    "Apply to this sprite only",
                    QMessageBox.ButtonRole.AcceptRole,
                )
                btn_all = box.addButton(
                    f"Apply to all {shared_count} sharing sprites",
                    QMessageBox.ButtonRole.AcceptRole,
                )
                btn_cancel = box.addButton(QMessageBox.StandardButton.Cancel)
                box.setDefaultButton(btn_fork)
                box.exec()
                clicked = box.clickedButton()
                if clicked is btn_fork:
                    fork_mode = True
                elif clicked is btn_all:
                    fork_mode = False
                else:
                    return  # Cancel
            else:
                # DOWP off — legacy two-option dialog.
                ret = QMessageBox.question(
                    self, "Shared Palette",
                    f"This sprite's palette ({pool.display_name}) is "
                    f"shared by\n{shared_count} sprites. Importing will "
                    f"change all of them.\n\n"
                    "Continue?\n\n"
                    "(Tip: enable Dynamic Overworld Palettes to get a "
                    "per-sprite option here.)",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if ret != QMessageBox.StandardButton.Yes:
                    return

        # ── File picker ───────────────────────────────────────────────
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

        # ── Extract palette colours (and, in manual mode, remap the
        #    source PNG to those colours so the image is replaced
        #    consistent with the new palette — same behaviour the
        #    standalone Image Indexer tab provides) ─────────────────────
        remapped_img: Optional[QImage] = None
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
            img = QImage(path)
            if img.isNull():
                QMessageBox.warning(self, "Import Failed",
                                    f"Could not load:\n{path}")
                return
            if img.format() != QImage.Format.Format_Indexed8:
                QMessageBox.warning(
                    self, "Not an Indexed PNG",
                    "This PNG is not in indexed (palette) mode.\n\n"
                    "Convert it to an indexed-colour PNG (8-bit, 16 colours)\n"
                    "in your image editor first — or use 'Import Manually…' "
                    "to remap a non-indexed PNG into the palette.",
                )
                return

            ct = img.colorTable()
            if len(ct) < 1:
                QMessageBox.warning(self, "Empty Palette",
                                    "No colour table entries.")
                return

            colors = []
            for c_entry in ct[:16]:
                r = (c_entry >> 16) & 0xFF
                g = (c_entry >> 8) & 0xFF
                b = c_entry & 0xFF
                colors.append(clamp_to_gba(r, g, b))
            while len(colors) < 16:
                colors.append((0, 0, 0))

        # ── Manual mode: write the remapped PNG over the sprite's own
        #    source PNG.  Auto mode never touches the image because the
        #    user is opting into "palette only — preserve pixel indices".
        if manual and remapped_img is not None:
            self._save_remapped_sprite_png(remapped_img, colors)

        # ── Apply: either fork a new tag or rewrite the shared one ────
        if fork_mode:
            self._apply_palette_fork(colors)
        else:
            self._apply_palette_to_tag(tag, colors)

    def _save_remapped_sprite_png(
        self, remapped: QImage, palette: List[Color],
    ) -> None:
        """Overwrite the current sprite's source PNG with a remapped
        indexed image.  Surfaces a clear error dialog on failure so the
        user knows the palette landed but the image didn't.
        """
        if not self._current_sprite or not self._current_sprite.png_path:
            return
        dest = self._current_sprite.png_path
        try:
            from ui.dialogs.manual_palette_pick_dialog import save_remapped_image
            ok = save_remapped_image(remapped, palette, dest)
        except Exception as exc:
            ok = False
            QMessageBox.warning(
                self, "Image Save Failed",
                f"Couldn't save the remapped image to:\n{dest}\n\n{exc}",
            )
            return
        if not ok:
            QMessageBox.warning(
                self, "Image Save Failed",
                f"Couldn't save the remapped image to:\n{dest}\n\n"
                "The palette was loaded into the editor but the PNG on "
                "disk was not updated.  Check folder permissions.",
            )

    def _apply_palette_to_tag(self, tag: str, colors: List[Color]) -> None:
        """Existing in-memory + bus update for the (possibly shared) tag."""
        self._palettes[tag] = colors
        self._palette_dirty.add(tag)
        _get_palette_bus().set_overworld_palette(tag, colors)

        self._loading = True
        try:
            self._pal_row.set_colors(colors)
        finally:
            self._loading = False

        self._pal_frame.setStyleSheet(self._DIRTY_SS)
        self._show_sprite_detail(self._current_sprite)
        self._rebuild_grid()
        self.modified.emit()

    def _apply_palette_fork(self, colors: List[Color]) -> None:
        """Run the engine refactor: forge a new palette tag, write its
        .gbapal, register it in the engine palette array, and rewrite
        this sprite's `.paletteTag` to point at the new tag.

        On success, reloads the project's overworld data so the UI
        immediately reflects the new pool layout (the current sprite
        now lives in a new 1-sprite pool, freshly created).
        """
        if not self._project_root or not self._current_sprite:
            return

        info_name = self._current_sprite.info_name
        old_tag = self._current_sprite.palette_tag

        try:
            from core.overworld_palette_fork import fork_palette_for_sprite
            success, applied, errors, new_tag = fork_palette_for_sprite(
                self._project_root,
                info_name,
                colors,
            )
        except Exception as e:
            QMessageBox.critical(
                self, "Fork Failed",
                f"Could not create a new palette tag for "
                f"{self._current_sprite.display_name}:\n\n{e}",
            )
            return

        if not success or not new_tag:
            err_str = "\n".join(f"  • {e}" for e in errors)
            QMessageBox.warning(
                self, "Fork Partially Applied",
                f"Some steps failed while creating a new palette tag.\n\n"
                f"Applied:\n" + "\n".join(f"  + {a}" for a in applied)
                + (f"\n\nErrors:\n{err_str}" if errors else ""),
            )
            # Even on partial success the disk may have stale state;
            # reload to surface whatever DID land.
            self.load(self._project_root)
            return

        # Push palette into the bus immediately so other tabs / icon
        # caches see the new colours before the reload completes.
        try:
            _get_palette_bus().set_overworld_palette(new_tag, colors)
        except Exception:
            pass

        # Brief plain-English summary so the user knows what happened.
        QMessageBox.information(
            self, "Palette Forked",
            f"{self._current_sprite.display_name} now uses its own "
            f"palette tag:\n  {new_tag}\n\n"
            f"The other sprites that previously shared "
            f"{old_tag} are unchanged.\n\n"
            f"Click Make Modern (Ctrl+Shift+M) to rebuild.",
        )
        # Reload so the UI rebuilds its pools / sprite list around the
        # new tag.  The sprite list re-selects by gfx_const so the user
        # stays on the same sprite after reload.
        target_gfx = self._current_sprite.gfx_const
        self.load(self._project_root)
        try:
            for entry in self._all_sprites.values():
                if entry.gfx_const == target_gfx:
                    self._on_sprite_clicked(entry)
                    break
        except Exception:
            pass

    def _import_palette_from_pal(self) -> None:
        """Import a JASC .pal file and apply it to the selected sprite's palette."""
        if not self._current_sprite:
            QMessageBox.information(
                self, "No Sprite Selected",
                "Select a sprite first, then import a palette for it.",
            )
            return

        tag = self._current_sprite.palette_tag
        pool = self._pools_by_tag.get(tag)

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

        start_dir = os.path.join(
            self._project_root, "graphics", "object_events", "palettes"
        ) if self._project_root else ""
        if not os.path.isdir(start_dir):
            start_dir = self._project_root or ""

        path, _ = QFileDialog.getOpenFileName(
            self, "Select JASC Palette", start_dir, "Palette Files (*.pal);;All Files (*)",
        )
        if not path:
            return

        colors = read_jasc_pal(path)
        if not colors:
            QMessageBox.warning(
                self, "Import Failed",
                f"Could not read a valid 16-colour JASC palette from:\n{path}",
            )
            return

        while len(colors) < 16:
            colors.append((0, 0, 0))
        colors = [clamp_to_gba(r, g, b) for r, g, b in colors[:16]]

        self._palettes[tag] = colors
        self._palette_dirty.add(tag)
        _get_palette_bus().set_overworld_palette(tag, colors)

        self._loading = True
        try:
            self._pal_row.set_colors(colors)
        finally:
            self._loading = False

        self._pal_frame.setStyleSheet(self._DIRTY_SS)
        self._show_sprite_detail(self._current_sprite)
        self._rebuild_grid()
        self.modified.emit()

        affected = len(pool.sprites) if pool else 1
        QMessageBox.information(
            self, "Palette Imported",
            f"Loaded 16 colours from:\n"
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

        # Determine palette settings.  Both "NEW" (auto-extract from
        # indexed PNG) and "NEW_MANUAL" (manual indexer pick) flow into
        # the same create_overworld_sprite path — the only difference
        # is HOW dlg.palette_colors got populated.  Manual mode also
        # remapped the source PNG before reaching this code, so
        # create_overworld_sprite's PNG copy step picks up the new
        # pixel indices automatically.
        pal_choice = dlg.palette_choice
        create_new = pal_choice in ("NEW", "NEW_MANUAL")
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
            f"  Palette: "
            f"{('New custom palette (manual pick)' if pal_choice == 'NEW_MANUAL' else 'New custom palette' if create_new else pal_choice)}"
            f"\n\n"
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
        """Update the DOWP buttons and status label based on patch state."""
        try:
            from core.dynamic_ow_pal_patch import is_dowp_enabled
        except ImportError:
            self._dowp_btn.setVisible(False)
            self._dowp_disable_btn.setVisible(False)
            self._dowp_status.setText("")
            return

        if is_dowp_enabled(self._project_root):
            self._dowp_btn.setVisible(False)
            self._dowp_disable_btn.setVisible(True)
            self._dowp_status.setText(
                "✓ Dynamic OW palettes enabled. Each sprite uses its own palette. "
                "Up to 16 unique palettes on screen at once."
            )
            self._dowp_status.setStyleSheet("color: #6a6; font-size: 10px;")
        else:
            self._dowp_btn.setVisible(True)
            self._dowp_disable_btn.setVisible(False)
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
            from core.dynamic_ow_pal_patch import is_dowp_enabled, apply_dowp_patch, scan_dowp_risks
        except ImportError:
            QMessageBox.critical(self, "Error",
                "Could not load the dynamic palette patch module.")
            return

        if is_dowp_enabled(self._project_root):
            # Project shows DOWP markers, but some patches may have failed earlier.
            # Offer a re-apply pass for repair — the patcher is idempotent, so
            # already-applied sites will be skipped.
            reply = QMessageBox.question(
                self,
                "Re-apply Dynamic Palettes?",
                "Dynamic Overworld Palettes appear to already be active in this project.\n\n"
                "Re-applying will verify each patch site and repair any that didn't apply "
                "cleanly the first time. Sites that are already patched will be skipped.\n\n"
                "Note: reflection tint values can only be set on the first apply. If the "
                "tint block is already patched, re-apply will leave it as-is.\n\n"
                "Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            tint_r, tint_g, tint_b = 5, 5, 10
        else:
            risks = scan_dowp_risks(self._project_root)
            dlg = _DOWPApplyDialog(risks, parent=self)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            tint_r, tint_g, tint_b = dlg.tint_values()

        success, applied_list, failed_list = apply_dowp_patch(
            self._project_root, tint_r=tint_r, tint_g=tint_g, tint_b=tint_b
        )

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

    def _disable_dynamic_palettes(self) -> None:
        """Reverse the DOWP patch after user confirmation."""
        if not self._project_root:
            return

        try:
            from core.dynamic_ow_pal_patch import remove_dowp_patch
        except ImportError:
            QMessageBox.critical(self, "Error",
                "Could not load the dynamic palette patch module.")
            return

        ret = QMessageBox.warning(
            self,
            "Disable Dynamic Overworld Palettes?",
            "This will reverse the Dynamic Overworld Palettes patch and restore\n"
            "the original source files.\n\n"
            "Files that will be restored:\n"
            "  • src/event_object_movement.c\n"
            "  • src/field_effect.c\n"
            "  • src/field_effect_helpers.c\n"
            "  • include/event_object_movement.h\n"
            "  • include/field_effect.h\n\n"
            "After disabling, do a full clean rebuild (make clean && make modern).\n\n"
            "Make sure you have a git commit or backup first.\n\n"
            "Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return

        success, reverted_list, failed_list = remove_dowp_patch(self._project_root)
        self._update_dowp_status()

        if success:
            detail = "\n".join(f"  ✓ {r}" for r in reverted_list)
            QMessageBox.information(self, "Dynamic Palettes Disabled",
                f"Patch successfully reversed.\n\n{detail}\n\n"
                f"Run a full clean rebuild to complete the removal.")
        else:
            applied_str = "\n".join(f"  ✓ {r}" for r in reverted_list) if reverted_list else "  (none)"
            failed_str = "\n".join(f"  ✗ {f}" for f in failed_list)
            QMessageBox.warning(self, "Partial Reversal",
                f"Some parts could not be automatically reversed.\n"
                f"Your source files may have been modified after the patch was applied.\n\n"
                f"Reverted:\n{applied_str}\n\n"
                f"Failed:\n{failed_str}\n\n"
                f"Search for '// DOWP' in the listed files to find and remove remaining changes.")

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
        return (bool(self._palette_dirty) or bool(self._sprite_png_dirty)
                or self._fe_tab.has_unsaved_changes())

    def flush_to_disk(self) -> tuple[int, list[str]]:
        """Write all dirty palettes to .pal files and remapped sprites to PNG."""
        ok = 0
        errors: list[str] = []

        # Pass 1 — NPC palette files. Each dirty palette tag gets:
        #   a) .pal rewritten from in-memory colors (game-side runtime)
        #   b) NEW: every sprite PNG that USES this palette tag has its
        #      embedded color table rebuilt to match. Without this step
        #      the PNG keeps whatever colors were originally baked into
        #      it and opening the .png in GIMP shows stale colors that
        #      don't match what the game now renders. The PNG's pixel
        #      INDICES are untouched — only the color table changes.
        baked_pngs: set[str] = set()
        for tag in list(self._palette_dirty):
            pal_path = self._pal_paths.get(tag, "")
            gbapal_path = self._gbapal_paths.get(tag, "")
            if not pal_path or not gbapal_path:
                errors.append(f"overworld-pal:{tag} (no path)")
                continue
            colors = self._palettes.get(tag)
            if not colors:
                errors.append(f"overworld-pal:{tag} (no colours)")
                continue
            # Write BOTH formats atomically.  The .pal (JASC text) is
            # what PorySuite's UI reads on reload; the .gbapal (binary)
            # is what the project's build pipeline INCBINs.  Writing
            # only one made the two formats drift out of sync and the
            # build kept using the old colours even when the editor
            # showed the new ones.
            from core.overworld_palette_io import write_palette_pair
            ok_gba, ok_pal = write_palette_pair(gbapal_path, pal_path, colors)
            if not (ok_gba and ok_pal):
                missing = []
                if not ok_gba:
                    missing.append(".gbapal")
                if not ok_pal:
                    missing.append(".pal")
                errors.append(
                    f"overworld-pal:{tag} (failed to write {', '.join(missing)})"
                )
                continue
            self._palette_dirty.discard(tag)
            ok += 1

            # Bake into every sprite PNG that references this palette tag.
            for gfx_key, entry in self._all_sprites.items():
                if entry.palette_tag != tag or not entry.png_path:
                    continue
                if entry.png_path in baked_pngs:
                    continue
                img = self._sprite_imgs.get(gfx_key)
                # If the in-memory copy isn't Indexed8, fall back to a
                # fresh load-and-convert from disk. export_indexed_png
                # refuses non-indexed input (would otherwise produce an
                # RGB PNG that breaks the gbagfx build step), so we MUST
                # guarantee indexed format before passing it through.
                if img is None or img.format() != QImage.Format.Format_Indexed8:
                    if not os.path.isfile(entry.png_path):
                        continue
                    disk_img = QImage(entry.png_path)
                    if disk_img.isNull():
                        continue
                    if disk_img.format() != QImage.Format.Format_Indexed8:
                        disk_img = disk_img.convertToFormat(
                            QImage.Format.Format_Indexed8)
                    img = disk_img
                try:
                    if export_indexed_png(img, colors, entry.png_path):
                        baked_pngs.add(entry.png_path)
                        # PNG just got the matching color table — clear
                        # any remap-dirty flag on it too; the disk file
                        # is now in sync with our in-memory state.
                        self._sprite_png_dirty.discard(gfx_key)
                    else:
                        errors.append(
                            f"overworld-png-bake:{gfx_key} "
                            f"(refused — image not indexed)"
                        )
                except Exception as exc:
                    errors.append(f"overworld-png-bake:{gfx_key} ({exc})")

        # Pass 2 — remapped PNG pixel data (Index-as-Background ops).
        # Anything still in _sprite_png_dirty had pixel-INDEX changes
        # that Pass 1's color-table-only bake didn't cover.
        for gfx_key in list(self._sprite_png_dirty):
            entry = self._all_sprites.get(gfx_key)
            img = self._sprite_imgs.get(gfx_key)
            if entry is None or img is None or not entry.png_path:
                errors.append(f"overworld-png:{gfx_key} (no sprite data)")
                continue
            pal = self._palettes.get(entry.palette_tag, [(0, 0, 0)] * 16)
            try:
                export_indexed_png(img, pal, entry.png_path)
                self._sprite_png_dirty.discard(gfx_key)
                ok += 1
            except Exception as exc:
                errors.append(f"overworld-png:{gfx_key} ({exc})")

        # Clear amber highlight if everything is clean now.
        if not self._palette_dirty and not self._sprite_png_dirty:
            self._pal_frame.setStyleSheet("")
            # Rebuild grid to clear amber thumbnail borders.
            self._rebuild_grid()

        # Pass 3 — field effect sprites
        fe_ok, fe_errors = self._fe_tab.flush_to_disk()
        ok += fe_ok
        errors.extend(fe_errors)

        return ok, errors

    # ── Public navigation API ────────────────────────────────────────────────

    def select_sprite_by_gfx_const(self, gfx_const: str) -> bool:
        """Switch to the NPC Sprites sub-tab and select the sprite for gfx_const.

        Returns True if the sprite was found and selected, False if not found.
        """
        entry = self._all_sprites.get(gfx_const)
        if not entry:
            return False

        # Switch to NPC Sprites sub-tab (index 0)
        self._ow_tabs.setCurrentIndex(0)

        # Set category filter to match the sprite's category so it's visible
        for i in range(self._cat_combo.count()):
            if self._cat_combo.itemData(i) == entry.category:
                self._cat_combo.blockSignals(True)
                self._cat_combo.setCurrentIndex(i)
                self._cat_combo.blockSignals(False)
                break

        # Clear any search text so the sprite isn't hidden by a filter
        self._search.blockSignals(True)
        self._search.clear()
        self._search.blockSignals(False)

        # Rebuild grid now that filters are updated, then select the sprite
        self._rebuild_grid()
        self._on_sprite_clicked(entry)
        return True
