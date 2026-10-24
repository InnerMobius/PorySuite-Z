"""core/fulldex_sheet.py

Build a single large PNG contact sheet showing every species in the project —
front sprite, back sprite, menu icon and type badges — laid out in a grid that
fills left-to-right, row by row.

Everything here is parsed from the OPEN PROJECT, never hardcoded:

* the species list, their names and their dex numbers come from the loaded
  project data,
* the sprite PNG paths come from the project's own graphics resolution,
* the palettes come from :mod:`core.sprite_palette_bus` (RAM-first, so an
  unsaved Graphics-tab edit shows up in the sheet),
* the TYPE BADGES are located by reading the project's own
  ``sMenuInfoIcons[]`` table and the INCBIN lines that say which files the
  badge sheet and its palette actually live in. A project that moved its
  badges, renamed the files, added types or re-cut the sheet still exports
  correctly — nothing about FireRed's stock layout is assumed.

Every sprite is rendered through :func:`core.sprite_render.load_sprite_pixmap`,
as the project's sprite-rendering rule requires — never a flat ``QPixmap``.
"""

from __future__ import annotations

import os
import re
from typing import Callable, Dict, List, Optional, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPixmap

from core.gba_font import load_gba_font
from core.sprite_render import load_sprite_pixmap, mon_sheet_frame

Progress = Optional[Callable[[int, int, str], None]]

DEFAULT_COLUMNS = 10
# The range the export dialog offers. Wider than ~15 gives a sheet too wide to
# read comfortably; narrower than 5 makes it absurdly tall.
MIN_COLUMNS = 5
MAX_COLUMNS = 15
# 1 = the game's own pixels, nothing scaled up.
DEFAULT_SCALE = 1
DEFAULT_FILENAME = "fulldex.png"

# Sheet styling — dark so transparent sprite backgrounds read cleanly.
_BG = QColor("#15171a")
_CELL_BG = QColor("#1d2025")
_CELL_BG_ALT = QColor("#212429")
_GRID = QColor("#2c3137")
_TEXT = QColor("#e6e8ea")
_TITLE = QColor("#ffffff")
# Sprite boxes sit on the green normally used as a transparency key, so a
# sprite's own transparent pixels read as "nothing here" rather than blending
# into the sheet. Each box carries a 1px border.
_SPRITE_BG = QColor("#98D0A0")
_BOX_BORDER = QColor("#10131a")


# ═════════════════════════════════════════════════════════════════════════════
# Type badges — located by parsing the project, never hardcoded
# ═════════════════════════════════════════════════════════════════════════════

# `[TYPE_FIRE + 1] = { 32, 12, 0x24 },` in the project's sMenuInfoIcons[].
_ICON_ROW_RE = re.compile(
    r"\[\s*(TYPE_\w+)\s*\+\s*1\s*\]\s*=\s*\{\s*"
    r"(\d+)\s*,\s*(\d+)\s*,\s*(0x[0-9A-Fa-f]+|\d+)\s*\}"
)


def _incbin_path(text: str, symbol: str) -> str:
    """Return the file an ``INCBIN`` line for *symbol* points at ("" if none)."""
    m = re.search(
        re.escape(symbol) + r"\s*\[\s*\]\s*=\s*INCBIN_[A-Z0-9]+\(\s*\"([^\"]+)\"",
        text,
    )
    return m.group(1) if m else ""


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def _badge_palette_symbol(list_menu: str, stem: str) -> str:
    """Return the palette symbol the GAME loads for menu-info blits.

    This must follow the project's runtime behaviour, not the file naming.
    A project is free to re-index its badge sheet against some other palette
    and load that one instead — pokefirered-derived projects commonly do,
    which leaves the conventionally-named palette sitting on disk STALE. The
    menus all load through one function, so read what that function loads.
    """
    # The dedicated loader, if the project still has it.
    m = re.search(r"ListMenuLoadStdPalAt\s*\([^)]*\)\s*\{(.*?)\n\}",
                  list_menu, re.S)
    if m:
        inner = re.search(r"LoadPalette\s*\(\s*(g\w+)", m.group(1))
        if inner:
            return inner.group(1)
    # Otherwise any palette this file loads, preferring one named for the
    # badge sheet's own stem.
    syms = re.findall(r"LoadPalette\s*\(\s*(g\w+)", list_menu)
    for s in syms:
        if s.startswith(stem):
            return s
    return syms[0] if syms else ""


def _find_badge_sources(project_root: str) -> Tuple[str, str, List[str]]:
    """Locate the type-badge sheet PNG and the palette the game draws it with.

    Reads the project's own INCBIN lines rather than assuming a layout. The
    badge tiles are INCBIN'd as ``.4bpp``; the editable source next to it is
    the ``.png`` the build compiles from. Returns ``(png, pal, warnings)``.
    """
    warnings: List[str] = []
    gfx_sym = ""

    # Which symbol holds the badges? The table blits out of it, so read the
    # blitter rather than guessing a name.
    list_menu = _read(os.path.join(project_root, "src", "list_menu.c"))
    m = re.search(r"&?(g\w+)\s*\[\s*sMenuInfoIcons\s*\[", list_menu)
    if m:
        gfx_sym = m.group(1)
    if not gfx_sym:
        warnings.append(
            "Could not tell which graphics array the type badges are blitted "
            "from (src/list_menu.c) — badges will be left out.")
        return "", "", warnings

    stem = gfx_sym[:-4] if gfx_sym.endswith("_Gfx") else gfx_sym
    graphics_c = _read(os.path.join(project_root, "src", "graphics.c"))
    gfx_rel = _incbin_path(graphics_c, gfx_sym)
    if not gfx_rel:
        warnings.append(
            f"No INCBIN found for {gfx_sym} — type badges will be left out.")
        return "", "", warnings

    pal_sym = _badge_palette_symbol(list_menu, stem)
    pal_rel = _incbin_path(graphics_c, pal_sym) if pal_sym else ""
    if not pal_rel:
        warnings.append(
            "Could not tell which palette the game draws the type badges "
            "with — they will use the colours baked into the sheet.")

    png = os.path.join(project_root, *os.path.splitext(gfx_rel)[0].split("/"))
    png += ".png"
    pal = ""
    if pal_rel:
        pal = os.path.join(project_root,
                           *os.path.splitext(pal_rel)[0].split("/")) + ".pal"
    if not os.path.isfile(png):
        warnings.append(
            f"Type badge sheet not found on disk ({png}) — badges left out.")
        return "", "", warnings
    if pal and not os.path.isfile(pal):
        warnings.append(
            f"Type badge palette not found on disk ({pal}) — using the "
            "colours baked into the sheet.")
        pal = ""
    return png, pal, warnings


def parse_type_icons(project_root: str) -> Tuple[Dict[str, QPixmap], List[str]]:
    """Return ``{TYPE_CONST: QPixmap}`` for every type badge in the project.

    The badge sheet is a tiled 4bpp image; ``sMenuInfoIcons[]`` gives each
    badge's size and its offset **in tiles**. Tiles run left-to-right across
    the sheet, so tile ``n`` starts at pixel ``(n % tiles_per_row * 8,
    n // tiles_per_row * 8)``.
    """
    out: Dict[str, QPixmap] = {}
    png, pal_path, warnings = _find_badge_sources(project_root)
    if not png:
        return out, warnings

    palette = []
    if pal_path:
        try:
            from ui.palette_utils import read_jasc_pal
            palette = read_jasc_pal(pal_path, 16) or []
        except Exception:
            palette = []

    sheet = load_sprite_pixmap(png, palette)
    if sheet is None or sheet.isNull() or sheet.width() < 8:
        warnings.append(f"Could not read the type badge sheet ({png}).")
        return out, warnings
    tiles_per_row = max(1, sheet.width() // 8)

    body = _read(os.path.join(project_root, "src", "list_menu.c"))
    try:
        body = body[body.index("sMenuInfoIcons[] ="):]
        body = body[:body.index("};")]
    except ValueError:
        warnings.append(
            "Could not find the sMenuInfoIcons[] table — type badges left out.")
        return out, warnings

    for m in _ICON_ROW_RE.finditer(body):
        const, w, h, off = m.group(1), int(m.group(2)), int(m.group(3)), \
            int(m.group(4), 0)
        x = (off % tiles_per_row) * 8
        y = (off // tiles_per_row) * 8
        if w <= 0 or h <= 0 or x + w > sheet.width() or y + h > sheet.height():
            continue
        out[const] = sheet.copy(x, y, w, h)
    if not out:
        warnings.append(
            "The sMenuInfoIcons[] table held no type badge rows — badges "
            "left out.")
    return out, warnings


# ═════════════════════════════════════════════════════════════════════════════
# Gathering what goes on the sheet
# ═════════════════════════════════════════════════════════════════════════════


def collect_entries(source_data, project_root: str,
                    graphics_cache=None) -> List[dict]:
    """Build the ordered list of species to draw.

    Ordered by the project's own dex number, falling back to a species'
    position in the national dex when its cached number is missing — the same
    rule the species tree uses, so the sheet matches what the app shows.
    """
    entries: List[dict] = []
    try:
        pokemon = source_data.get_pokemon_data() or {}
    except Exception:
        return entries

    natpos: Dict[str, int] = {}
    try:
        for i, e in enumerate(source_data.get_national_dex() or []):
            sp = e.get("species") if isinstance(e, dict) else e
            if sp and sp not in natpos:
                natpos[sp] = i + 1
    except Exception:
        pass

    ordered: List[Tuple[str, int]] = []
    for sp in pokemon.keys():
        try:
            num = source_data.get_species_data(sp, "dex_num")
        except Exception:
            num = None
        if not isinstance(num, int):
            num = natpos.get(sp)
        if isinstance(num, int):
            ordered.append((sp, num))
    ordered.sort(key=lambda t: t[1])

    for sp, num in ordered:
        try:
            name = source_data.get_species_data(sp, "name") or sp
        except Exception:
            name = sp
        try:
            types = source_data.get_species_info(sp, "types") or []
        except Exception:
            types = []
        if not isinstance(types, list):
            types = []
        # A species whose two type slots hold the same type is single-typed —
        # the game shows one badge for it, so the sheet does too.
        seen: List[str] = []
        for t in types:
            if isinstance(t, str) and t not in seen:
                seen.append(t)
        types = seen
        idx = 0
        if graphics_cache is not None:
            try:
                idx = max(0, min(2, graphics_cache.get_icon_idx(sp)))
            except Exception:
                idx = 0
        entries.append({
            "species": sp,
            "name": str(name),
            "dex": num,
            "types": [t for t in types if isinstance(t, str)],
            "icon_pal_idx": idx,
        })
    return entries


def _species_pixmaps(source_data, project_root: str,
                     entry: dict) -> Tuple[Optional[QPixmap],
                                           Optional[QPixmap],
                                           Optional[QPixmap]]:
    """Return (front, back, icon) pixmaps for one entry, any of them None."""
    from core.sprite_palette_bus import get_bus

    sp = entry["species"]
    bus = get_bus()

    def _path(key: str) -> str:
        try:
            return source_data.get_species_image_path(sp, key) or ""
        except Exception:
            return ""

    battle_pal = bus.ensure_pokemon_palette(project_root, sp, "normal")

    def _battle(key: str) -> Optional[QPixmap]:
        path = _path(key)
        if not path or not os.path.isfile(path):
            return None
        pm = load_sprite_pixmap(path, battle_pal)
        if pm is None or pm.isNull():
            return None
        return mon_sheet_frame(pm, 0)

    icon = None
    ipath = _path("iconSprite")
    if ipath and os.path.isfile(ipath):
        ipal = bus.ensure_icon_palette(project_root, entry.get("icon_pal_idx", 0))
        pm = load_sprite_pixmap(ipath, ipal)
        if pm is not None and not pm.isNull() and pm.width() > 0:
            side = min(pm.width(), pm.height())
            icon = pm.copy(0, 0, side, side)

    return _battle("frontPic"), _battle("backPic"), icon


# ═════════════════════════════════════════════════════════════════════════════
# Drawing
# ═════════════════════════════════════════════════════════════════════════════


def _scaled(pm: Optional[QPixmap], factor: int) -> Optional[QPixmap]:
    """Nearest-neighbour upscale, so pixel art stays crisp."""
    if pm is None or pm.isNull() or factor <= 1:
        return pm
    return pm.scaled(pm.width() * factor, pm.height() * factor,
                     Qt.AspectRatioMode.IgnoreAspectRatio,
                     Qt.TransformationMode.FastTransformation)


def _cell_metrics(scale: int, text_h: int) -> dict:
    sprite = 64 * scale
    icon = 32 * scale
    border = scale                      # 1px at 100%
    pad = 4 * scale
    gap = 4 * scale
    sprite_box = sprite + border * 2
    icon_box = icon + border * 2
    return {
        "border": border,
        "sprite_box": sprite_box,
        "icon_box": icon_box,
        "pad": pad,
        "gap": gap,
        "text": text_h,
        "w": sprite_box * 2 + gap + pad * 2,
        "h": (pad * 2 + text_h * 2 + sprite_box
              + max(icon_box, 26 * scale) + gap * 2),
    }


def _draw_boxed(p: QPainter, x: int, y: int, box: int, border: int,
                pm: Optional[QPixmap]) -> None:
    """Draw a bordered, green-backed box with *pm* sitting inside it."""
    p.fillRect(x, y, box, box, _BOX_BORDER)
    inner = box - border * 2
    p.fillRect(x + border, y + border, inner, inner, _SPRITE_BG)
    if pm is None or pm.isNull():
        # Nothing to show — a short dash marks the empty box.
        p.fillRect(x + box // 2 - 3 * border, y + box // 2,
                   6 * border, border, _BOX_BORDER)
        return
    ox = x + border + max(0, (inner - pm.width()) // 2)
    oy = y + border + max(0, inner - pm.height())
    p.drawPixmap(ox, oy, pm)


class _TextDrawer:
    """Draws the sheet's labels with the project's in-game font.

    Falls back to Qt's default font only when the project's font can't be
    read, and says so, rather than silently producing a sheet that doesn't
    look like the game.
    """

    def __init__(self, project_root: str, scale: int):
        self._scale = max(1, scale)
        self._warning = ""
        self._bright = load_gba_font(project_root, fg=(255, 255, 255),
                                     shadow=(0, 0, 0))
        self._plain = load_gba_font(project_root,
                                    fg=(_TEXT.red(), _TEXT.green(),
                                        _TEXT.blue()),
                                    shadow=(0, 0, 0))
        if self._bright is None or self._plain is None:
            self._warning = (
                "Could not read the project's in-game font — labels fall back "
                "to the system font.")

    @property
    def warning(self) -> str:
        return self._warning

    @property
    def height(self) -> int:
        f = self._plain
        return (f.height if f else 12) * self._scale

    def _font(self, bright: bool):
        return self._bright if bright else self._plain

    def measure(self, text: str, bright: bool = False) -> int:
        f = self._font(bright)
        if f is None:
            return len(text) * 6 * self._scale
        return f.measure(text) * self._scale

    def elide(self, text: str, max_width: int, bright: bool = False) -> str:
        f = self._font(bright)
        if f is None:
            return text
        return f.elide(text, max(0, max_width // self._scale))

    def draw(self, painter: QPainter, x: int, y: int, text: str,
             bright: bool = False) -> None:
        f = self._font(bright)
        if f is None:
            painter.setFont(QFont())
            painter.setPen(_TITLE if bright else _TEXT)
            painter.drawText(x, y + self.height, text)
            return
        if self._scale <= 1:
            f.draw(painter, x, y, text)
            return
        pm = f.render(text)
        if pm.isNull():
            return
        painter.drawPixmap(
            x, y,
            pm.scaled(pm.width() * self._scale, pm.height() * self._scale,
                      Qt.AspectRatioMode.IgnoreAspectRatio,
                      Qt.TransformationMode.FastTransformation))


def build_fulldex_sheet(project_root: str, source_data,
                        entries: List[dict],
                        type_icons: Dict[str, QPixmap],
                        columns: int = DEFAULT_COLUMNS,
                        scale: int = DEFAULT_SCALE,
                        progress: Progress = None) -> Tuple[QImage, List[str]]:
    """Render every entry into one big :class:`QImage`.

    Cells fill left-to-right, wrapping every *columns* entries.
    """
    warnings: List[str] = []
    columns = max(1, int(columns))
    scale = max(1, int(scale))
    total = len(entries)
    if total == 0:
        return QImage(), ["No species found in this project."]

    text = _TextDrawer(project_root, scale)
    if text.warning:
        warnings.append(text.warning)

    m = _cell_metrics(scale, text.height)
    rows = (total + columns - 1) // columns
    img = QImage(m["w"] * columns, m["h"] * rows, QImage.Format.Format_ARGB32)
    img.fill(_BG)

    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, False)

    missing = 0
    for i, entry in enumerate(entries):
        if progress is not None:
            progress(i, total, f"Drawing {entry.get('name') or entry['species']}…")

        col = i % columns
        row = i // columns
        cx = col * m["w"]
        cy = row * m["h"]

        p.fillRect(cx, cy, m["w"], m["h"],
                   _CELL_BG if (row % 2 == 0) else _CELL_BG_ALT)
        p.setPen(_GRID)
        p.drawRect(cx, cy, m["w"] - 1, m["h"] - 1)

        front, back, icon = _species_pixmaps(source_data, project_root, entry)
        if front is None and back is None and icon is None:
            missing += 1

        # Header — the dex number. No '#': the game's font has no such glyph,
        # and a missing glyph would just render as a blank.
        dex = entry.get("dex")
        text.draw(p, cx + m["pad"], cy + m["pad"],
                  f"{dex:03d}" if isinstance(dex, int) else "???", bright=True)

        # Battle sprites — front then back, bottom-aligned on a shared line.
        sy = cy + m["pad"] + m["text"] + m["gap"]
        for slot, pm in enumerate((front, back)):
            bx = cx + m["pad"] + slot * (m["sprite_box"] + m["gap"])
            _draw_boxed(p, bx, sy, m["sprite_box"], m["border"],
                        _scaled(pm, scale))

        # Footer row — menu icon, then the type badges, then the name.
        fy = sy + m["sprite_box"] + m["gap"]
        _draw_boxed(p, cx + m["pad"], fy, m["icon_box"], m["border"],
                    _scaled(icon, scale))

        bx = cx + m["pad"] + m["icon_box"] + m["gap"]
        by = fy
        drawn_types = 0
        for tconst in entry.get("types", []):
            badge = type_icons.get(tconst)
            if badge is None or badge.isNull():
                continue
            if drawn_types >= 2:
                break
            spm = _scaled(badge, scale)
            if bx + spm.width() > cx + m["w"] - m["pad"]:
                break
            p.drawPixmap(bx, by, spm)
            by += spm.height() + 2 * scale
            drawn_types += 1

        # Name, trimmed to the cell.
        avail = m["w"] - m["pad"] * 2
        label = text.elide(entry.get("name") or entry["species"], avail)
        text.draw(p, cx + m["pad"], cy + m["h"] - m["pad"] - m["text"], label)

    p.end()
    if progress is not None:
        progress(total, total, "Saving…")
    if missing:
        warnings.append(
            f"{missing} species had no readable sprites and were drawn empty.")
    return img, warnings


# ═════════════════════════════════════════════════════════════════════════════
# One-call entry point
# ═════════════════════════════════════════════════════════════════════════════


def export_fulldex(project_root: str, source_data,
                   graphics_cache=None,
                   out_path: str = "",
                   columns: int = DEFAULT_COLUMNS,
                   scale: int = DEFAULT_SCALE,
                   progress: Progress = None) -> dict:
    """Build and save the full sheet. Returns a result dict.

    Keys: ``path``, ``count``, ``columns``, ``rows``, ``width``, ``height``,
    ``warnings``.
    """
    warnings: List[str] = []
    if progress is not None:
        progress(0, 1, "Reading the project's type graphics…")
    type_icons, tw = parse_type_icons(project_root)
    warnings.extend(tw)

    if progress is not None:
        progress(0, 1, "Collecting species…")
    entries = collect_entries(source_data, project_root, graphics_cache)

    img, bw = build_fulldex_sheet(project_root, source_data, entries,
                                  type_icons, columns=columns, scale=scale,
                                  progress=progress)
    warnings.extend(bw)

    path = out_path or os.path.join(project_root, DEFAULT_FILENAME)
    ok = False
    if not img.isNull():
        try:
            ok = img.save(path, "PNG")
        except Exception:
            ok = False
    if not ok:
        warnings.append(f"Could not write {path}.")

    columns = max(1, int(columns))
    return {
        "path": path if ok else "",
        "count": len(entries),
        "columns": columns,
        "rows": (len(entries) + columns - 1) // columns,
        "width": img.width(),
        "height": img.height(),
        "warnings": warnings,
    }
