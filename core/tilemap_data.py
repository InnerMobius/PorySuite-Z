"""
core/tilemap_data.py
GBA tilemap reader/writer and tile sheet loader for the Tilemap Editor.

Tilemap format: array of u16 entries
    bits 0-9:   tile index (0-1023)
    bit  10:    horizontal flip
    bit  11:    vertical flip
    bits 12-15: palette number (0-15)

Tile sheets: indexed PNG images where each 8x8 pixel block is one tile.
Palettes: JASC-PAL files (16 colors each), loaded via palette_utils.
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from PyQt6.QtGui import QImage, QPainter, qRgb, qRgba


TILE_PX = 8  # pixels per tile side


# ─── Data structures ──────────────────────────────────────────────────────────


@dataclass
class TileEntry:
    """One cell in a tilemap."""
    tile_index: int = 0   # 0-1023
    hflip: bool = False
    vflip: bool = False
    palette: int = 0      # 0-15

    def to_u16(self) -> int:
        v = self.tile_index & 0x3FF
        if self.hflip:
            v |= 1 << 10
        if self.vflip:
            v |= 1 << 11
        v |= (self.palette & 0xF) << 12
        return v

    @staticmethod
    def from_u16(val: int) -> "TileEntry":
        return TileEntry(
            tile_index=val & 0x3FF,
            hflip=bool((val >> 10) & 1),
            vflip=bool((val >> 11) & 1),
            palette=(val >> 12) & 0xF,
        )


@dataclass
class Tilemap:
    """A 2D grid of tile entries loaded from a .bin file."""
    width: int = 32       # in tiles
    height: int = 20      # in tiles
    entries: List[TileEntry] = field(default_factory=list)
    source_path: str = ""

    @staticmethod
    def from_file(path: str, width: int = 0) -> "Tilemap":
        """Load a tilemap .bin file.

        If width is 0, auto-detect: try 32 first (standard GBA),
        then infer from file size.
        """
        with open(path, "rb") as f:
            data = f.read()

        count = len(data) // 2
        if count == 0:
            return Tilemap(source_path=path)

        entries = []
        for val in struct.unpack(f"<{count}H", data):
            entries.append(TileEntry.from_u16(val))

        # Auto-detect dimensions
        if width <= 0:
            width = _guess_width(count)
        height = max(1, (count + width - 1) // width)

        # Pad if needed (partial last row)
        while len(entries) < width * height:
            entries.append(TileEntry())

        return Tilemap(
            width=width, height=height,
            entries=entries, source_path=path,
        )

    def save(self, path: str = "") -> None:
        """Write the tilemap back to a .bin file."""
        target = path or self.source_path
        if not target:
            raise ValueError("No path specified")
        data = struct.pack(
            f"<{len(self.entries)}H",
            *(e.to_u16() for e in self.entries),
        )
        with open(target, "wb") as f:
            f.write(data)
        if not path:
            self.source_path = target

    def get(self, col: int, row: int) -> TileEntry:
        idx = row * self.width + col
        if 0 <= idx < len(self.entries):
            return self.entries[idx]
        return TileEntry()

    def set(self, col: int, row: int, entry: TileEntry) -> None:
        idx = row * self.width + col
        if 0 <= idx < len(self.entries):
            self.entries[idx] = entry

    def pixel_size(self) -> Tuple[int, int]:
        return (self.width * TILE_PX, self.height * TILE_PX)


def _guess_width(entry_count: int) -> int:
    """Guess tilemap width from entry count."""
    # Common GBA tilemap sizes
    known = {
        640: 32,    # 32x20 = GBA screen
        1024: 32,   # 32x32
        2048: 64,   # 64x32
        4096: 64,   # 64x64
    }
    if entry_count in known:
        return known[entry_count]
    # Default: 32 wide if divisible, else find best fit
    if entry_count % 32 == 0:
        return 32
    if entry_count % 30 == 0:
        return 30
    return 32


# ─── Tile sheet (PNG indexed image) ──────────────────────────────────────────


@dataclass
class TileSheet:
    """A tile sheet loaded from an indexed PNG."""
    image: QImage           # the raw indexed image
    tiles_wide: int = 0     # number of tile columns
    tiles_high: int = 0     # number of tile rows
    tile_count: int = 0
    source_path: str = ""
    is_8bpp: bool = False   # True if >16 colors (256-color mode)

    @staticmethod
    def from_file(path: str) -> "TileSheet":
        img = QImage(path)
        if img.isNull():
            raise FileNotFoundError(f"Cannot load image: {path}")
        tw = max(1, img.width() // TILE_PX)
        th = max(1, img.height() // TILE_PX)
        # Detect 8bpp: indexed image with >16 colors in the color table
        ct = img.colorTable()
        is_8bpp = len(ct) > 16 if ct else False
        return TileSheet(
            image=img,
            tiles_wide=tw,
            tiles_high=th,
            tile_count=tw * th,
            source_path=path,
            is_8bpp=is_8bpp,
        )

    def get_tile_image(
        self, index: int,
        hflip: bool = False,
        vflip: bool = False,
    ) -> QImage:
        """Extract a single 8x8 tile, optionally flipped."""
        if index < 0 or index >= self.tile_count:
            # Return transparent tile for out-of-range
            blank = QImage(TILE_PX, TILE_PX, QImage.Format.Format_ARGB32)
            blank.fill(qRgba(0, 0, 0, 0))
            return blank

        col = index % self.tiles_wide
        row = index // self.tiles_wide
        tile = self.image.copy(
            col * TILE_PX, row * TILE_PX, TILE_PX, TILE_PX,
        )

        if hflip:
            tile = tile.mirrored(True, False)
        if vflip:
            tile = tile.mirrored(False, True)

        return tile


# ─── Palette set ──────────────────────────────────────────────────────────────


Color = Tuple[int, int, int]  # (r, g, b) 0-255


def _read_gbapal_file(path: str) -> List[Color]:
    """Read a raw binary .gbapal file and return its colors as 8-bit RGB.

    GBA palette format: each color is 2 bytes little-endian, encoding
    a 15-bit BGR555 value: 0bBBBBBGGGGGRRRRR. Each 5-bit channel scales
    to 8-bit by left-shift 3.
    Total file size: 32 bytes per 16-color palette, up to 512 bytes for
    a full 256-color (16-palette) file.
    """
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return []
    colors: List[Color] = []
    for i in range(0, len(data) - 1, 2):
        val = data[i] | (data[i + 1] << 8)
        r = (val & 0x1F) << 3
        g = ((val >> 5) & 0x1F) << 3
        b = ((val >> 10) & 0x1F) << 3
        colors.append((r, g, b))
    return colors


@dataclass
class PaletteSet:
    """Up to 16 palettes of 16 colors each."""
    palettes: List[List[Color]] = field(default_factory=list)
    source_paths: List[str] = field(default_factory=list)
    _loaded_slots: set = field(default_factory=set)  # Slots with real data

    def get_color(self, pal_idx: int, color_idx: int) -> Color:
        if 0 <= pal_idx < len(self.palettes):
            pal = self.palettes[pal_idx]
            if 0 <= color_idx < len(pal):
                return pal[color_idx]
        return (0, 0, 0)

    def get_qcolor(self, pal_idx: int, color_idx: int) -> QColor:
        r, g, b = self.get_color(pal_idx, color_idx)
        return QColor(r, g, b)

    def palette_count(self) -> int:
        return len(self.palettes)

    def set_palette_at(self, slot: int, colors: List[Color]) -> None:
        """Load a palette into a specific slot (0-15), expanding if needed."""
        while len(self.palettes) <= slot:
            self.palettes.append([(0, 0, 0)] * 16)
        self.palettes[slot] = colors[:16]
        while len(self.palettes[slot]) < 16:
            self.palettes[slot].append((0, 0, 0))
        self._loaded_slots.add(slot)

    def ensure_slots(self, count: int = 16) -> None:
        """Ensure at least `count` palette slots exist (fills empty with black).
        Does NOT mark new slots as loaded — they're placeholders."""
        while len(self.palettes) < count:
            self.palettes.append([(0, 0, 0)] * 16)

    def is_slot_loaded(self, slot: int) -> bool:
        """Check if a palette slot has real data (not just a placeholder)."""
        return slot in self._loaded_slots

    def loaded_slot_count(self) -> int:
        """Number of slots with real palette data."""
        return len(self._loaded_slots)

    def get_flat_colors(self) -> List[Color]:
        """Return all palettes flattened into a single 256-entry color list.

        Used for 8bpp rendering where pixel values index directly into
        a flat 256-color table (palettes[0] = indices 0-15,
        palettes[1] = indices 16-31, etc.)
        """
        flat: List[Color] = []
        for slot in range(min(16, len(self.palettes))):
            flat.extend(self.palettes[slot][:16])
            # Pad sub-palette to 16 if short
            while len(flat) % 16 != 0:
                flat.append((0, 0, 0))
        # Pad to 256 total
        while len(flat) < 256:
            flat.append((0, 0, 0))
        return flat[:256]

    @staticmethod
    def from_pal_files(paths: List[str]) -> "PaletteSet":
        """Load palettes from a list of .pal or .gbapal files.

        Routes by extension: `.gbapal` is raw binary GBA palette format
        (each color = 2 bytes little-endian, 5-bit RGB), `.pal` is JASC
        text format. Both 16-color and 256-color files are handled — 256
        colors split into 16 sub-palettes.
        """
        palettes = []
        loaded = set()
        for p in paths:
            ext = os.path.splitext(p)[1].lower()
            if ext == ".gbapal":
                colors = _read_gbapal_file(p)
            else:
                from ui.palette_utils import read_jasc_pal
                colors = read_jasc_pal(p)
            if not colors:
                continue
            if len(colors) > 16:
                # Multi-palette file — split into 16-color sub-palettes.
                # region_map.gbapal is the canonical example: 256 colors
                # = 16 palettes of 16, each tile picks one via attr bits.
                for i in range(0, len(colors), 16):
                    chunk = colors[i:i + 16]
                    while len(chunk) < 16:
                        chunk.append((0, 0, 0))
                    loaded.add(len(palettes))
                    palettes.append(chunk)
            else:
                loaded.add(len(palettes))
                palettes.append(colors)
        return PaletteSet(palettes=palettes, source_paths=list(paths),
                          _loaded_slots=loaded)

    @staticmethod
    def from_indexed_image(img: QImage) -> "PaletteSet":
        """Extract palettes from an indexed QImage's color table.

        The image's color table may have up to 256 entries.
        We split them into groups of 16 to form palettes.
        """
        ct = img.colorTable()
        if not ct:
            return PaletteSet()

        # Split color table into 16-color palettes
        palettes = []
        loaded = set()
        for i in range(0, len(ct), 16):
            chunk = ct[i:i + 16]
            pal = []
            for c in chunk:
                r = (c >> 16) & 0xFF
                g = (c >> 8) & 0xFF
                b = c & 0xFF
                pal.append((r, g, b))
            while len(pal) < 16:
                pal.append((0, 0, 0))
            loaded.add(len(palettes))
            palettes.append(pal)
        return PaletteSet(palettes=palettes, _loaded_slots=loaded)


# ─── Rendering ────────────────────────────────────────────────────────────────


def render_tilemap(
    tilemap: Tilemap,
    sheet: TileSheet,
    palette_set: Optional[PaletteSet] = None,
    tile_offset: int = 0,
) -> QImage:
    """Render a full tilemap to a QImage using the tile sheet and palettes.

    If palette_set is provided and the tile sheet is indexed, tiles are
    recolored using the palette specified in each tilemap entry. Otherwise,
    tiles are drawn as-is from the PNG.

    For 8bpp tile sheets (>16 colors), the full 256-color palette is applied
    to each tile — pixel values index directly into the flat color table,
    and the palette bits in the tilemap entry are ignored (matching GBA
    hardware behavior in 256-color BG mode).

    tile_offset: The VRAM tile offset where this sheet is loaded.
    Tilemap indices between tile_offset and tile_offset+sheet.tile_count
    map to tiles 0..N in the sheet. Indices outside that range render blank.
    """
    pw, ph = tilemap.pixel_size()
    result = QImage(pw, ph, QImage.Format.Format_ARGB32)
    result.fill(qRgba(0, 0, 0, 255))

    use_palettes = (
        palette_set is not None
        and palette_set.palette_count() > 0
        and sheet.image.format() == QImage.Format.Format_Indexed8
    )

    # Pre-build the 8bpp flat color table once for the whole render
    flat_ct_8bpp = None
    if use_palettes and sheet.is_8bpp:
        flat_ct_8bpp = build_flat_color_table(palette_set)

    painter = QPainter(result)
    for row in range(tilemap.height):
        for col in range(tilemap.width):
            entry = tilemap.get(col, row)

            # Apply tile offset: the tilemap index is a VRAM index.
            # Subtract offset to get the local index within this sheet.
            local_idx = entry.tile_index - tile_offset
            if tile_offset > 0 and (local_idx < 0 or local_idx >= sheet.tile_count):
                # Tile belongs to a different sheet — render as dark grey
                continue

            tile_img = sheet.get_tile_image(
                local_idx if tile_offset > 0 else entry.tile_index,
                entry.hflip, entry.vflip,
            )

            # Recolor with palette if applicable
            if use_palettes and tile_img.format() == QImage.Format.Format_Indexed8:
                if sheet.is_8bpp and flat_ct_8bpp:
                    tile_img = _recolor_tile_8bpp(tile_img, flat_ct_8bpp)
                else:
                    tile_img = _recolor_tile(tile_img, entry.palette, palette_set)

            painter.drawImage(col * TILE_PX, row * TILE_PX, tile_img)

    painter.end()
    return result


def _recolor_tile(
    tile: QImage,
    pal_idx: int,
    palette_set: PaletteSet,
) -> QImage:
    """Replace the color table of an indexed tile with a specific palette.

    If the requested palette slot isn't loaded (no real data), falls back
    to palette 0. This handles the common case where a 4bpp PNG has only
    one palette but the tilemap references multiple palette slots — the
    game loads the same palette into different VRAM slots at runtime, but
    the tile artwork was drawn with the PNG's single embedded palette.
    """
    recolored = QImage(tile)

    # Determine which palette to actually use
    effective_pal = pal_idx
    if not palette_set.is_slot_loaded(pal_idx):
        # Fall back to palette 0 if the requested slot has no real data
        effective_pal = 0
        if not palette_set.is_slot_loaded(0):
            return recolored  # No palettes at all, return as-is

    if effective_pal < palette_set.palette_count():
        new_ct = []
        pal = palette_set.palettes[effective_pal]
        for i in range(min(16, len(pal))):
            r, g, b = pal[i]
            # Color index 0 is transparent on GBA
            if i == 0:
                new_ct.append(qRgba(r, g, b, 0))
            else:
                new_ct.append(qRgb(r, g, b))
        recolored.setColorTable(new_ct)
    return recolored


def build_flat_color_table(palette_set: PaletteSet) -> List[int]:
    """Build a flat 256-entry Qt color table from a PaletteSet.

    Returns a list of qRgb/qRgba values suitable for setColorTable().
    Index 0 is transparent (matching GBA behavior).
    """
    flat_colors = palette_set.get_flat_colors()
    ct = []
    for i, (r, g, b) in enumerate(flat_colors):
        if i == 0:
            ct.append(qRgba(r, g, b, 0))
        else:
            ct.append(qRgb(r, g, b))
    return ct


def _recolor_tile_8bpp(
    tile: QImage,
    flat_ct: List[int],
) -> QImage:
    """Replace the color table of an 8bpp tile with a flat 256-entry table.

    In GBA 256-color BG mode, each pixel's 8-bit value indexes directly
    into the full 256-entry palette. The palette bits in the tilemap entry
    are ignored by hardware.
    """
    recolored = QImage(tile)
    # Extend or trim the flat color table to match what the tile needs
    ct_size = max(len(tile.colorTable()), 256)
    ct = list(flat_ct[:ct_size])
    while len(ct) < ct_size:
        ct.append(qRgb(0, 0, 0))
    recolored.setColorTable(ct)
    return recolored


def _recolor_tile_8bpp_attr(
    tile: QImage,
    attr_pal: int,
    palette_set: PaletteSet,
) -> QImage:
    """Render an 8bpp-stored tile as if it were a 4bpp GBA tile with
    per-entry palette selection (the region-map case).

    Region-map graphics on FireRed are 4bpp on the GBA but the .png is
    stored as 8bpp because multiple sub-palettes' worth of baked colors
    are needed for editing. The .bin tilemap entry's palette bits ARE
    used by hardware — the engine selects sub-palette `attr_pal` and
    renders pixels by their LOW 4 BITS only.

    This function builds a 256-entry color table where index P maps to
    sub-palette `attr_pal` color `(P % 16)`. Result: the displayed tile
    matches what the GBA actually draws for any given attr_pal.

    If `attr_pal` isn't loaded, falls back to palette 0.
    """
    effective_pal = attr_pal
    if not palette_set.is_slot_loaded(effective_pal):
        effective_pal = 0
    pal: List[Color]
    if effective_pal < palette_set.palette_count():
        pal = list(palette_set.palettes[effective_pal])
    else:
        pal = []
    while len(pal) < 16:
        pal.append((0, 0, 0))
    ct: List[int] = []
    for p in range(256):
        r, g, b = pal[p % 16]
        # Color index 0 of any sub-palette is transparent on GBA.
        if (p % 16) == 0:
            ct.append(qRgba(r, g, b, 0))
        else:
            ct.append(qRgb(r, g, b))
    recolored = QImage(tile)
    recolored.setColorTable(ct)
    return recolored


def detect_tile_palette(tile: QImage) -> int:
    """Inspect an 8bpp tile's pixel values and guess which 16-color
    sub-palette its colors were baked from. Returns the palette index
    (0-15) corresponding to the sub-palette range that contains the
    most non-zero pixels.

    Used by the Tilemap Editor to auto-set the attr_pal when the user
    picks a tile from a multi-palette 8bpp sheet — so painting feels
    natural (pick a purple tile, paint, GBA renders it purple).
    """
    if tile.format() != QImage.Format.Format_Indexed8:
        return 0
    counts = [0] * 16
    w, h = tile.width(), tile.height()
    for y in range(h):
        for x in range(w):
            p = tile.pixelIndex(x, y)
            if p == 0:
                continue  # transparent in every sub-palette
            counts[(p // 16) & 0x0F] += 1
    if not any(counts):
        return 0
    return counts.index(max(counts))


# ─── Auto-discovery ───────────────────────────────────────────────────────────


def _pick_best_sheet(bin_path: str, sheets: List[str]) -> str:
    """Pick the best tile sheet for a tilemap by checking tile coverage.

    Reads the tilemap to find the max tile index, then picks the smallest
    sheet that has enough tiles. This avoids choosing a large unrelated
    sprite sheet (like rival.png with 72 tiles) over the correct smaller
    sheet (like menu.png with 48 tiles) when the tilemap only uses 16 tiles.
    """
    # Read max tile index from the bin file
    max_idx = 0
    try:
        with open(bin_path, "rb") as f:
            data = f.read()
        for i in range(0, len(data), 2):
            if i + 1 < len(data):
                val = data[i] | (data[i + 1] << 8)
                idx = val & 0x3FF
                if idx > max_idx:
                    max_idx = idx
    except OSError:
        pass

    # Find sheets that have enough tiles, prefer the smallest sufficient one
    candidates = []
    for s in sheets:
        try:
            img = QImage(s)
            if img.isNull():
                continue
            tile_count = (img.width() // TILE_PX) * (img.height() // TILE_PX)
            if tile_count > max_idx:
                candidates.append((tile_count, s))
        except Exception:
            continue

    if candidates:
        # Score candidates: prefer sheets that look like tile sheets
        # (wider images = typical tile sheet layout) over small sprites.
        # Among wide sheets, prefer closer tile count to what's needed.
        def score(item):
            tile_count, path = item
            img = QImage(path)
            width = img.width() if not img.isNull() else 0
            # Primary: prefer wider images (tile sheets are typically 128px+)
            # Secondary: among similar widths, prefer moderate tile count
            return (width, -abs(tile_count - max_idx * 2))
        candidates.sort(key=score, reverse=True)
        return candidates[0][1]

    # Fallback: largest file
    return max(sheets, key=lambda p: os.path.getsize(p))


@dataclass
class TilemapAssets:
    """Discovered assets for a tilemap .bin file."""
    bin_path: str
    tile_sheets: List[str]    # candidate .png files
    pal_files: List[str]      # candidate .pal files
    best_sheet: str = ""      # best-guess tile sheet
    best_pals: List[str] = field(default_factory=list)


def discover_assets(bin_path: str) -> TilemapAssets:
    """Find tile sheet and palette files related to a .bin tilemap.

    Search strategy:
    1. Same directory, same base name .png → best match
    2. Same directory, all .png files → candidates
    3. Parent directory .png files → candidates (for nested dirs like firered/)
    4. Same directory + palettes/ subdir for .pal files
    5. Same directory .pal files
    """
    dir_path = os.path.dirname(bin_path)
    base_name = os.path.splitext(os.path.basename(bin_path))[0]
    parent_dir = os.path.dirname(dir_path)

    sheets: List[str] = []
    pals: List[str] = []
    best_sheet = ""

    # -- Tile sheets --

    # Same dir, same base name
    same_name_png = os.path.join(dir_path, base_name + ".png")
    if os.path.isfile(same_name_png):
        best_sheet = same_name_png
        sheets.append(same_name_png)

    # All PNGs in same dir
    try:
        for f in sorted(os.listdir(dir_path)):
            fp = os.path.join(dir_path, f)
            if f.lower().endswith(".png") and os.path.isfile(fp) and fp not in sheets:
                sheets.append(fp)
    except OSError:
        pass

    # Parent dir PNGs (for nested like firered/border_bg.bin → ../border_bg.png)
    if parent_dir and parent_dir != dir_path:
        try:
            for f in sorted(os.listdir(parent_dir)):
                fp = os.path.join(parent_dir, f)
                if f.lower().endswith(".png") and os.path.isfile(fp) and fp not in sheets:
                    # Prioritize same base name from parent
                    if os.path.splitext(f)[0] == base_name and not best_sheet:
                        best_sheet = fp
                    sheets.append(fp)
        except OSError:
            pass

    if not best_sheet and sheets:
        # No exact name match — pick the best-fitting tile sheet.
        # Read the tilemap to find the max tile index used, then pick the
        # smallest sheet that has enough tiles to cover it (avoids picking
        # a huge unrelated sprite sheet over a small but correct tile sheet).
        best_sheet = _pick_best_sheet(bin_path, sheets)

    # -- Palettes --

    def _is_pal_file(fname: str) -> bool:
        # Accept JASC .pal and raw GBA .gbapal both.
        f = fname.lower()
        return f.endswith(".pal") or f.endswith(".gbapal")

    # Same dir
    try:
        for f in sorted(os.listdir(dir_path)):
            fp = os.path.join(dir_path, f)
            if _is_pal_file(f) and os.path.isfile(fp):
                pals.append(fp)
    except OSError:
        pass

    # palettes/ subdir
    pal_subdir = os.path.join(dir_path, "palettes")
    if os.path.isdir(pal_subdir):
        try:
            for f in sorted(os.listdir(pal_subdir)):
                fp = os.path.join(pal_subdir, f)
                if _is_pal_file(f) and os.path.isfile(fp) and fp not in pals:
                    pals.append(fp)
        except OSError:
            pass

    # Parent dir
    if parent_dir and parent_dir != dir_path:
        try:
            for f in sorted(os.listdir(parent_dir)):
                fp = os.path.join(parent_dir, f)
                if _is_pal_file(f) and os.path.isfile(fp) and fp not in pals:
                    pals.append(fp)
        except OSError:
            pass

    # Best palette guess: only auto-load .pal files that name-match the
    # tilemap (e.g. textbox.bin → textbox.pal, textbox1.pal).  Non-matching
    # .pal files (like bug.pal, sky.pal sitting in the same directory) should
    # NOT be loaded by default — the PNG's own color table is almost always
    # the correct palette for those cases.  The user can always import .pal
    # files manually.
    matching_pals = []
    for p in pals:
        pname = os.path.splitext(os.path.basename(p))[0]
        if pname == base_name or pname.startswith(base_name):
            matching_pals.append(p)

    # If a name-matching palette has more than 16 colors, it covers
    # multiple palette slots on its own — don't load any others.
    def _read_colors(p: str) -> list:
        if p.lower().endswith(".gbapal"):
            try:
                return _read_gbapal_file(p)
            except Exception:
                return []
        from ui.palette_utils import read_jasc_pal
        return read_jasc_pal(p)

    best_pals = matching_pals
    if matching_pals:
        first_colors = _read_colors(matching_pals[0])
        if len(first_colors) > 16:
            best_pals = matching_pals[:1]
    else:
        # No name-matching palette. Auto-pick a multi-palette file in the
        # same directory if there's exactly one — this is the canonical
        # "shared palette across many tilemaps" case (region_map.gbapal
        # for every <region>.bin in graphics/region_map/).
        multi_pal_candidates = []
        for p in pals:
            if os.path.dirname(p) != dir_path:
                continue  # only consider same-dir files
            if len(_read_colors(p)) > 16:
                multi_pal_candidates.append(p)
        if len(multi_pal_candidates) == 1:
            best_pals = multi_pal_candidates

    return TilemapAssets(
        bin_path=bin_path,
        tile_sheets=sheets,
        pal_files=pals,
        best_sheet=best_sheet,
        best_pals=best_pals,
    )
