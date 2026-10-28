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

    width_source: str = "guess"   # "explicit" | "engine" | "guess"
    cell_bytes: int = 2           # 1 for a byte-per-cell (affine / packed) map

    @staticmethod
    def from_file(path: str, width: int = 0) -> "Tilemap":
        """Load a tilemap .bin file.

        A `.bin` is headerless, so neither the cell size nor the width is in it.
        Both are asked of the engine source first (see
        `core/tilemap_engine_index`), because both used to be guessed wrong:

          * `item_menu/list.bin` was shown 27 cells wide — the largest exact
            divisor of 216 that is <= 32 — where `item_menu.c` declares
            `LIST_TILES_WIDTH 18` by `LIST_TILES_HEIGHT 12`, so the map appeared
            sheared.
          * the five party-menu slot layouts, the two trade stripe maps and the
            two affine trade maps are `INCBIN_U8` — ONE byte per cell. Read as
            16-bit, `slot_main.bin` reports a highest tile index of 815 with
            palette bits 1-3 where the truth is index 61 and no palette bits, so
            nothing resolved and the map rendered solid black.

        An explicit `width` (the user's own choice, or a saved preference) always
        wins over both.
        """
        with open(path, "rb") as f:
            data = f.read()

        facts = None
        try:
            from core.tilemap_engine_index import facts_for_path
            facts = facts_for_path(path)
        except Exception:        # never let the index stop a file from opening
            facts = None

        cell_bytes = facts.cell_bytes if facts is not None else 2

        if cell_bytes == 1:
            count = len(data)
            entries = [TileEntry(tile_index=b) for b in data]
        else:
            count = len(data) // 2
            entries = [TileEntry.from_u16(v)
                       for v in struct.unpack(f"<{count}H", data[:count * 2])]
        if count == 0:
            return Tilemap(source_path=path, cell_bytes=cell_bytes)

        height = 0
        if width > 0:
            source = "explicit"
        elif facts is not None and facts.declared_dims:
            width, height = facts.declared_dims
            source = "engine"
        else:
            width = _guess_width(count)
            source = "guess"
        if height <= 0:
            height = max(1, (count + width - 1) // width)

        # Do NOT pad entries to width*height. The entry list IS the file's true
        # data; padding it inflates the saved size whenever the guessed width
        # doesn't divide the count (e.g. a 216-entry list displayed 32-wide).
        # Cells past the list (a partial last row) render as a default
        # TileEntry via get(), and save() writes only the real entries.
        return Tilemap(
            width=width, height=height,
            entries=entries, source_path=path,
            width_source=source, cell_bytes=cell_bytes,
        )

    def save(self, path: str = "") -> None:
        """Write the tilemap back to a .bin file.

        Writes EXACTLY ``len(self.entries)`` entries — the file's TRUE data
        count. That equals the original count on load (``from_file`` no longer
        pads) or the count an explicit Resize set; display W/H rewrapping
        (``_on_dimensions_changed``) only re-flows the grid on screen and never
        touches the entry list. So scrubbing the width/height spinners can NOT
        grow or shrink the file — the only way to change the count is Resize.

        Why this matters: the .bin is `INCBIN`'d into a fixed-size array (e.g.
        ``sItemListTilemap[LIST_TILES_WIDTH * LIST_TILES_HEIGHT]``) and/or LZ-
        decompressed into a BG tilemap buffer sized for the declared map. The
        file MUST stay exactly the size the engine expects. The 2026-06 item_menu
        ``list.bin`` corruption was the old ``width * height`` write: a 216-entry
        list auto-detected 32-wide padded to 224 on load, then 18×13=234 after a
        width change, writing 468 bytes into a [18*12=216] array → broken build.
        Writing ``len(entries)`` keeps the file pinned to its real data size.
        """
        target = path or self.source_path
        if not target:
            raise ValueError("No path specified")
        n = len(self.entries)
        if self.cell_bytes == 1:
            # A byte-per-cell map (the party-menu slots, the trade stripe and
            # affine maps) carries ONLY a tile index — no flip bits, no palette
            # bits. Writing `to_u16()` here would double the file and corrupt
            # every one of them, so the cell size `from_file` detected has to be
            # honoured on the way back out.
            data = bytes(e.tile_index & 0xFF for e in self.entries)
        else:
            data = struct.pack(f"<{n}H", *(e.to_u16() for e in self.entries))
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
    """Guess tilemap width from entry count.

    Prefer a width that DIVIDES the count exactly, so width*height == count and
    there's no partial last row. (A non-dividing width — e.g. 32 for a
    216-entry list — used to pad the entry list on load and then grow the file
    on save: the item_menu/list.bin corruption.)
    """
    # Common GBA tilemap sizes
    known = {
        640: 32,    # 32x20 = GBA screen
        1024: 32,   # 32x32
        2048: 64,   # 64x32
        4096: 64,   # 64x64
    }
    if entry_count in known:
        return known[entry_count]
    if entry_count <= 0:
        return 32
    if entry_count % 32 == 0:
        return 32
    if entry_count % 30 == 0:
        return 30
    # Largest exact divisor <= 32 (a sensible GBA BG stride) so the grid has no
    # partial row. The user can still set any width via the spinner (rewrap is
    # display-only and never changes the saved size).
    for w in range(32, 0, -1):
        if entry_count % w == 0:
            return w
    return 32


# ── Per-project tilemap dimension preferences ────────────────────────────────
#
# .bin files are headerless — dimensions are inferred from file size. That
# loses the user's chosen W/H across a save+reload (e.g. user sets 24×8 =
# 192 tiles, saves, reopens, _guess_width(192) returns 32 → file reloads as
# 32×6 with the row stride scrambled). To preserve the user's choice we
# store a tiny JSON map keyed by .bin path → (w, h) inside PorySuite's
# project-cache dir. No project tree pollution; the cache is portable
# per-project (separate caches for separate projects via path hash).

def _dim_cache_path(project_dir: str) -> str:
    """Absolute path to the dimension-pref JSON for this project."""
    if not project_dir:
        return ""
    try:
        from core.app_info import get_cache_dir
    except ImportError:
        return ""
    return os.path.join(get_cache_dir(project_dir), "tilemap_dimensions.json")


def _normalize_bin_key(project_dir: str, bin_path: str) -> str:
    """Project-relative path if possible (so the cache survives moving the
    project folder), else the absolute path as a fallback."""
    if project_dir and bin_path:
        try:
            return os.path.relpath(bin_path, project_dir).replace("\\", "/")
        except ValueError:
            pass
    return bin_path


def get_tilemap_dim_pref(project_dir: str, bin_path: str
                         ) -> Optional[Tuple[int, int]]:
    """Return cached (w, h) for this .bin, or None if no preference is
    stored. Failures (missing file, malformed JSON) return None silently."""
    cache_path = _dim_cache_path(project_dir)
    if not cache_path or not os.path.isfile(cache_path):
        return None
    try:
        import json as _json
        with open(cache_path, "r", encoding="utf-8") as f:
            data = _json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    key = _normalize_bin_key(project_dir, bin_path)
    rec = data.get(key) or data.get(bin_path)
    if not isinstance(rec, dict):
        return None
    try:
        w = int(rec.get("w", 0))
        h = int(rec.get("h", 0))
    except (TypeError, ValueError):
        return None
    if w > 0 and h > 0:
        return (w, h)
    return None


def set_tilemap_dim_pref(project_dir: str, bin_path: str,
                          w: int, h: int) -> None:
    """Record (w, h) for this .bin in the project's PorySuite cache."""
    cache_path = _dim_cache_path(project_dir)
    if not cache_path:
        return
    import json as _json
    data: dict = {}
    if os.path.isfile(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                loaded = _json.load(f)
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, ValueError):
            pass
    key = _normalize_bin_key(project_dir, bin_path)
    data[key] = {"w": int(w), "h": int(h)}
    try:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w", encoding="utf-8", newline="\n") as f:
            _json.dump(data, f, indent=2)
            f.write("\n")
    except OSError:
        pass


# ─── Tile sheet (PNG indexed image) ──────────────────────────────────────────


def _uses_8bit_indices(img: QImage) -> bool:
    """Does this sheet's artwork actually use pixel index values above 15?

    This is the question that matters, and it is NOT the same as either of the
    two things that were tried instead:

      * **the colour table's length** (the original test, `len(ct) > 16`) is
        storage padding, not content. `item_menu/bg.png` carries a 256-entry
        table and uses only indices 0-15, so the editor treated 4bpp artwork as
        256-colour mode and threw the cells' palette bits away.
      * **the compiled asset's name** (`foo.4bpp` vs `foo.8bpp`) describes what
        the engine loads, not how the PNG stores it. `party_menu/bg.png` and
        `region_map.png` both compile to `.4bpp` yet hold indices up to 63 and
        54, because several sub-palettes' worth of colours are baked in so the
        art can be edited. Reading those as 4bpp installs a 16-entry colour
        table under pixels that index past it.

    An index above 15 means the pixel values span sub-palettes, which is exactly
    when the attr path in `apply_palette_to_image` is the correct renderer.
    """
    if img.format() != QImage.Format.Format_Indexed8:
        return False
    try:
        bits = img.constBits()
        if bits is None:
            return False
        bits.setsize(img.sizeInBytes())
        buf = memoryview(bits).cast("B")
        stride = img.bytesPerLine()
        width = img.width()
        for row in range(img.height()):
            start = row * stride
            if max(buf[start:start + width], default=0) > 15:
                return True
        return False
    except Exception:
        # Fall back to the old storage test rather than refusing to load.
        ct = img.colorTable()
        return len(ct) > 16 if ct else False


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
        # Whether the artwork really spans an 8-bit index space — see
        # _uses_8bit_indices for why neither the colour table's length nor the
        # compiled asset's name answers this.
        is_8bpp = _uses_8bit_indices(img)
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


def _write_gbapal_file(path: str, colors: List[Color]) -> bool:
    """Write a list of 8-bit RGB colors as a raw GBA palette (BGR555).

    Mirror of `_read_gbapal_file`. The file size matches the number of
    colors passed in: 2 bytes per colour, no header, no trailing data.
    Channels are clamped to 5 bits via right-shift-3.
    Returns True on success.
    """
    import os as _os
    try:
        _os.makedirs(_os.path.dirname(path) or ".", exist_ok=True)
        buf = bytearray()
        for (r, g, b) in colors:
            r5 = max(0, min(31, int(r) >> 3))
            g5 = max(0, min(31, int(g) >> 3))
            b5 = max(0, min(31, int(b) >> 3))
            val = r5 | (g5 << 5) | (b5 << 10)
            buf.append(val & 0xFF)
            buf.append((val >> 8) & 0xFF)
        with open(path, "wb") as f:
            f.write(bytes(buf))
        return True
    except Exception:
        return False


def write_pal_from_gbapal(gbapal_path: str, pal_path: str) -> bool:
    """Write a committable JASC ``.pal`` source next to a baked ``.gbapal``.

    ``.gbapal`` / ``.4bpp`` / ``.lz`` are git-ignored build artefacts (vanilla
    pokefirered ignores them — they're regenerated from source on every build).
    So a baked palette that ships ONLY as a ``.gbapal`` is dropped by git and
    vanishes on a fresh clone, and the build dies with "Failed to open JASC-PAL
    file" because there's no source to regenerate it from. Writing the ``.pal``
    fixes that: git tracks the ``.pal``, and the build's ``%.gbapal: %.pal``
    rule rebuilds the binary on any machine.

    The ``.pal`` is derived FROM the just-written ``.gbapal`` so the pair is
    always consistent. The 5-bit -> 8-bit channel expansion uses gbagfx's
    ``(c * 255) // 31`` (NOT ``<< 3``) so the build's reverse conversion
    reproduces the byte-identical binary — the round trip is lossless, so a
    re-bake produces no spurious git diff. JASC uses CRLF line endings, matching
    the vanilla ``.pal`` files (e.g. ``ash.pal``). Returns True on success.
    """
    try:
        with open(gbapal_path, "rb") as f:
            data = f.read()
    except OSError:
        return False
    count = len(data) // 2
    if count == 0:
        return False
    lines = ["JASC-PAL", "0100", str(count)]
    for i in range(0, count * 2, 2):
        val = data[i] | (data[i + 1] << 8)
        r = ((val & 0x1F) * 255) // 31
        g = (((val >> 5) & 0x1F) * 255) // 31
        b = (((val >> 10) & 0x1F) * 255) // 31
        lines.append(f"{r} {g} {b}")
    try:
        with open(pal_path, "w", newline="") as f:
            f.write("".join(line + "\r\n" for line in lines))
        return True
    except OSError:
        return False


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


def build_palette_set(assets: "TilemapAssets",
                      sheet: Optional[TileSheet]) -> Optional[PaletteSet]:
    """Turn discovered assets into the PaletteSet the editor should render with.

    One place decides this, because the choice was being made separately in the
    tab's load path, its sheet-changed handler and its palette-source handler,
    and they could disagree about what a tilemap's colours are.

    Order:
      1. `best_pals` in order, into slots 0, 1, 2… — correct for a split palette
         source, where file N IS slot N.
      2. any slot the engine names explicitly (`slot_pals`) placed at that slot,
         filling gaps `best_pals` left rather than overwriting it.
      3. failing both, the sheet PNG's own colour table.
    """
    pset: Optional[PaletteSet] = None
    if assets.best_pals:
        try:
            pset = PaletteSet.from_pal_files(assets.best_pals)
        except Exception:
            pset = None

    if assets.slot_pals:
        if pset is None:
            pset = PaletteSet()
        for slot, path in sorted(assets.slot_pals.items()):
            if pset.is_slot_loaded(slot):
                continue
            try:
                if path.lower().endswith(".gbapal"):
                    colors = _read_gbapal_file(path)
                else:
                    from ui.palette_utils import read_jasc_pal
                    colors = read_jasc_pal(path)
            except Exception:
                continue
            if not colors:
                continue
            # A >16-colour file at slot N covers N, N+1, … in sequence.
            for i in range(0, min(len(colors), (16 - slot) * 16), 16):
                chunk = list(colors[i:i + 16])
                while len(chunk) < 16:
                    chunk.append((0, 0, 0))
                target = slot + i // 16
                if target < 16 and not pset.is_slot_loaded(target):
                    pset.set_palette_at(target, chunk)

    if (pset is None or pset.palette_count() == 0) and sheet is not None:
        pset = PaletteSet.from_indexed_image(sheet.image)
    return pset


def unresolved_slots(tilemap: Tilemap,
                     palette_set: Optional[PaletteSet]) -> List[int]:
    """Palette slots this tilemap's cells name that have no palette loaded.

    `_recolor_tile` silently substitutes palette 0 for these, which is how a
    screen ends up drawn in the wrong colours with nothing to explain it — the
    move relearner's slot-4 cells come out black because palette 0 is black
    there. The editor surfaces this list so the answer is visible instead of
    being a mystery, since for many tilemaps the slot genuinely is not filled by
    the screen that uses them and no amount of further parsing will say what
    belongs there.
    """
    if palette_set is None:
        return []
    used = {e.palette for e in tilemap.entries}
    return sorted(s for s in used if not palette_set.is_slot_loaded(s))


def backdrop_color(palette_set: Optional["PaletteSet"]) -> Color:
    """The colour a background pixel of palette-index 0 actually shows.

    On the GBA, a BG pixel whose palette index is 0 is neither black nor
    transparent — it shows the BACKDROP, which is BG palette 0, colour 0.

    Both render paths MUST take their fill from here, or they disagree about what
    a blank cell looks like. Before this existed, the canvas was filled with
    opaque black while colour 0 was drawn transparent, so every blank cell came
    out pure black: `pokemon_storage/menu.bin` is entirely colour-0 cells and
    rendered as a solid black rectangle even though nothing about the file is
    wrong, `fame_checker/tilemap1.bin` showed 77% black where the game shows
    bright yellow, and `party_menu/bg.bin` 39% black over a sage backdrop.
    """
    if palette_set is not None and palette_set.palette_count() > 0:
        return palette_set.get_color(0, 0)
    # Nothing loaded, so there is no backdrop to show. Opaque black is the least
    # misleading choice — transparent would let the widget background through and
    # read as part of the artwork.
    return (0, 0, 0)


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
    # The GBA backdrop, not black — see backdrop_color(). Cells drawn from colour
    # index 0 are transparent, so whatever is under them is what the user sees.
    result.fill(qRgb(*backdrop_color(palette_set)))

    # Pre-build the flat 256-colour table once for the whole render. Only the
    # genuine-256-colour path consumes it — see recolor_tile_for_entry, which
    # owns the decision about which path a sheet is on.
    flat_ct_8bpp = None
    if (palette_set is not None and palette_set.palette_count() > 0
            and sheet.is_8bpp and palette_set.loaded_slot_count() <= 1):
        flat_ct_8bpp = build_flat_color_table(palette_set)

    painter = QPainter(result)
    for row in range(tilemap.height):
        for col in range(tilemap.width):
            entry = tilemap.get(col, row)

            # Apply tile offset: the tilemap index is a VRAM index.
            # Subtract offset to get the local index within this sheet.
            local_idx = entry.tile_index - tile_offset
            if tile_offset > 0 and (local_idx < 0 or local_idx >= sheet.tile_count):
                # Tile lives in a different sheet — leave the backdrop showing.
                continue

            tile_img = sheet.get_tile_image(
                local_idx if tile_offset > 0 else entry.tile_index,
                entry.hflip, entry.vflip,
            )

            tile_img = apply_palette_to_image(
                tile_img, entry.palette, sheet, palette_set, flat_ct_8bpp)

            painter.drawImage(col * TILE_PX, row * TILE_PX, tile_img)

    painter.end()
    return result


def apply_palette_to_image(
    img: QImage,
    pal_idx: int,
    sheet: TileSheet,
    palette_set: Optional[PaletteSet],
    flat_ct_8bpp: Optional[List[int]] = None,
) -> QImage:
    """Colour one indexed image — a single tile, or a whole sheet — through the
    palette that the GBA would actually use for it.

    This is the ONLY place that decision is made. It used to be made in FOUR
    places that did not agree:

      * `render_tilemap` (full redraw, on load / resize / palette change),
      * the canvas's per-cell repaint after a paint stroke,
      * the tile picker's `paintEvent`, which draws the whole sheet,
      * the selected-tile preview swatch.

    Only the per-cell repaint handled the case where an 8bpp-stored sheet is
    really a 4bpp asset whose cell palette bits pick a sub-palette. The other
    three flattened it to one 256-colour table. So on `party_menu/bg.bin` 40% of
    pixels differed between a full redraw and a paint stroke — a map loaded one
    way, the cell you painted flipped to other colours, and the next redraw
    flipped it back — while the picker and the preview showed a tile in colours
    it would not have once placed. Every caller now comes through here.

    `pal_idx` is the palette the cell (or the picker's selection) names.
    `flat_ct_8bpp` is an optional pre-built table so a full render need not
    rebuild it per cell; it is computed on demand when omitted.
    """
    if (palette_set is None
            or palette_set.palette_count() == 0
            or sheet.image.format() != QImage.Format.Format_Indexed8
            or img.format() != QImage.Format.Format_Indexed8):
        return img

    if not sheet.is_8bpp:
        return _recolor_tile(img, pal_idx, palette_set)

    # An 8bpp-stored sheet is one of two different things, and they look alike
    # on disk:
    #   * a genuine 256-colour background — the pixel value indexes the whole
    #     256-entry palette and hardware ignores the cell's palette bits;
    #   * a 4bpp asset stored as 8bpp so several sub-palettes' worth of baked
    #     colours are available while editing (the region map, party_menu/bg,
    #     whose real engine asset is bg.4bpp) — here hardware DOES use the
    #     cell's palette bits, together with the pixel's low four bits.
    # More than one loaded sub-palette means the second case: those extra
    # sub-palettes only exist so the palette bits have something to select.
    if palette_set.loaded_slot_count() > 1:
        return _recolor_tile_8bpp_attr(img, pal_idx, palette_set)
    if flat_ct_8bpp is None:
        flat_ct_8bpp = build_flat_color_table(palette_set)
    return _recolor_tile_8bpp(img, flat_ct_8bpp)


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

    NOTE: uses ``tile.copy()`` (deep copy) rather than ``QImage(tile)``
    (shallow / implicit-share). Qt's copy-on-write SHOULD detach on
    ``setColorTable`` below, but in PyQt6 there's a documented edge
    case where the color-table mutation doesn't trigger a full detach,
    leaking the new color table back to the source image. That caused
    the "switching from .pal to PNG colors shows wrong colors until
    you toggle the source twice" bug — the picker's per-paint recolor
    was clobbering ``sheet.image.colorTable()``, so the next
    ``PaletteSet.from_indexed_image(sheet.image)`` read back the .pal
    colors instead of the original PNG colors. ``.copy()`` produces an
    independent QImage with its own data buffer; mutations stay local.
    """
    recolored = tile.copy()

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

    See ``_recolor_tile`` for why ``.copy()`` is used instead of
    ``QImage(other)`` — same PyQt6 detach edge case applies.
    """
    recolored = tile.copy()
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
    # Deep copy — see ``_recolor_tile`` for the PyQt6 detach reason.
    recolored = tile.copy()
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
    """Pick a tile sheet for a tilemap that has no same-named one, by coverage.

    Reads the tilemap's highest tile index, then returns the SMALLEST sheet that
    has enough tiles to cover it. That keeps a large unrelated sprite sheet (say
    `rival.png`, 72 tiles) from winning over the correct small one (`menu.png`,
    48 tiles) when the tilemap only reaches index 16.

    The previous implementation claimed to do that and did the opposite: it
    sorted by `(image_width, -abs(tile_count - max_idx * 2))` descending, so it
    took the WIDEST image — the large unrelated sheet the docstring warns about —
    and, among equals, one with roughly twice as many tiles as needed (the factor
    of two was never explained). Worse, no candidate qualifies at all whenever
    the tilemap's indices are inflated, which happens for every byte-per-cell
    `.bin` read as u16; it then fell through to `max(sheets, key=getsize)`, the
    largest file IN BYTES, which says nothing about tile count. That is how
    `party_menu/slot_main.bin` was paired with `pokeball.png` (32 tiles) while
    the `bg.png` it belongs to (64 tiles) sat in the same candidate list.

    When nothing covers the highest index, the most tiles is the most useful
    guess, not the most bytes — and the tilemap probably needs a tile offset or
    is not a u16 tilemap at all. Either way the editor's sheet dropdown is the
    user's escape hatch, so this only has to be a good default.
    """
    max_idx = 0
    try:
        with open(bin_path, "rb") as f:
            data = f.read()
        for i in range(0, len(data) - 1, 2):
            idx = (data[i] | (data[i + 1] << 8)) & 0x3FF
            if idx > max_idx:
                max_idx = idx
    except OSError:
        pass

    # One decode per candidate — `score()` used to re-decode the whole PNG on
    # every sort comparison.
    measured: List[Tuple[int, str]] = []
    for s in sheets:
        try:
            img = QImage(s)
            if img.isNull():
                continue
            measured.append(
                ((img.width() // TILE_PX) * (img.height() // TILE_PX), s))
        except Exception:
            continue

    if not measured:
        return sheets[0] if sheets else ""

    # Sort by path as well so the choice is stable run to run.
    covering = sorted(((n, p) for n, p in measured if n > max_idx))
    if covering:
        return covering[0][1]
    return max(measured, key=lambda item: (item[0], item[1]))[1]


@dataclass
class TilemapAssets:
    """Discovered assets for a tilemap .bin file."""
    bin_path: str
    tile_sheets: List[str]    # candidate .png files
    pal_files: List[str]      # candidate .pal files
    best_sheet: str = ""      # best-guess tile sheet
    best_pals: List[str] = field(default_factory=list)
    slot_pals: dict = field(default_factory=dict)
    """VRAM palette slot -> palette file, where the engine states it.

    `best_pals` is an ordered list that lands in slots 0, 1, 2… in order, which
    is the right model for a split palette source. It is the WRONG model when a
    tilemap's cells name specific slots: the move relearner's cells use slots 0
    and 4, and dropping its one palette into slot 0 leaves slot 4 empty. This
    carries the slot numbers the engine actually names so they can be loaded
    where they belong. See `build_palette_set`.
    """


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

    # What the engine says beats both the name match and the heuristic: the
    # `_Gfx` declared in the same block as this tilemap's `_Tilemap` IS the sheet
    # it indexes into. Guessing from filenames and sizes got this wrong in both
    # directions — `fame_checker/tilemap3.bin` has no name-mate at all and was
    # paired by size with a character portrait instead of the `bg.png` the engine
    # names, and `unused/blue_frame.bin` with an 8-tile `shadow.png` instead of
    # `color_frames.png`.
    engine_sheet = ""
    engine_pals: List[str] = []
    engine_slot_pals: dict = {}
    try:
        from core.tilemap_engine_index import facts_for_path, infer_game_dir
        _facts = facts_for_path(bin_path)
        if _facts is not None:
            _game = infer_game_dir(bin_path)
            for _slot, _pr in _facts.slot_palettes.items():
                _cand = os.path.join(_game, *_pr.split("/"))
                _stem = os.path.splitext(_cand)[0]
                for _alt in (_stem + ".pal", _cand):
                    if os.path.isfile(_alt):
                        engine_slot_pals[_slot] = _alt
                        break
            if _facts.sheet_rel:
                cand = os.path.join(_game, *_facts.sheet_rel.split("/"))
                if os.path.isfile(cand):
                    engine_sheet = cand
            for pr in _facts.palette_rels:
                cand = os.path.join(_game, *pr.split("/"))
                # The authored `.pal` beside a `.gbapal` is the source of truth
                # (the `.gbapal` is a build artifact), so prefer it when present.
                stem = os.path.splitext(cand)[0]
                for alt in (stem + ".pal", cand):
                    if os.path.isfile(alt) and alt not in engine_pals:
                        engine_pals.append(alt)
                        break
    except Exception:            # the index must never stop a tilemap opening
        engine_sheet, engine_pals, engine_slot_pals = "", [], {}

    if engine_sheet:
        best_sheet = engine_sheet
        if engine_sheet not in sheets:
            sheets.insert(0, engine_sheet)
    elif not best_sheet and sheets:
        # The engine does not name a sheet for this one (64 of 285 covered
        # tilemaps sit in a block with no `_Gfx`), so fall back to coverage.
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

    # Pokefirered convention for multi-palette assets: source files are
    # named ``<stem>1.pal``, ``<stem>2.pal``, … (each holding 16 colors,
    # JASC text), and the build pipeline concatenates them into
    # ``<stem>.gbapal`` (binary, 32+ colors) via:
    #   cat <stem>1.gbapal <stem>2.gbapal > <stem>.gbapal
    # The combined file is a BUILD ARTIFACT — the .pal source files
    # are the authoritative sources of truth (committed to git, edited
    # by humans). PorySuite-Z must write back to those .pal files, not
    # to the combined .gbapal — otherwise the next ``make`` regenerates
    # the combined file from the (stale) source .pals and clobbers the
    # editor's changes.
    #
    # Detect the split-source pattern: look for two-or-more JASC .pal
    # files named ``<stem><digit>.pal`` (in stem-numeric order). If
    # found, prefer them over any combined ``<stem>.pal`` /
    # ``<stem>.gbapal``. This is the textbox / region-map / etc.
    # canonical pattern.
    import re as _re
    numbered_pat = _re.compile(
        r"^" + _re.escape(base_name) + r"(\d+)$"
    )

    def _numbered_jasc_pals() -> list:
        """Return matching ``<stem><n>.pal`` files in numeric order, or
        ``[]`` if fewer than two exist (one isn't a "split" pattern,
        it's just a singular file with a digit in its name)."""
        out: list[tuple[int, str]] = []
        for p in matching_pals:
            if not p.lower().endswith(".pal"):
                continue
            stem = os.path.splitext(os.path.basename(p))[0]
            m = numbered_pat.match(stem)
            if m:
                out.append((int(m.group(1)), p))
        if len(out) < 2:
            return []
        out.sort(key=lambda pair: pair[0])
        return [p for _, p in out]

    split_pals = _numbered_jasc_pals()

    def _split_sources_of(pal_path: str) -> list:
        """The authored ``<stem><n>.pal`` sources behind a combined palette.

        Same convention as ``_numbered_jasc_pals`` but keyed on the PALETTE's
        own stem rather than the tilemap's, because the engine often names a
        palette whose stem differs from the ``.bin`` (``summary_screen`` pages
        all share one palette). Fewer than two is not a split pattern.
        """
        pdir = os.path.dirname(pal_path)
        pstem = os.path.splitext(os.path.basename(pal_path))[0]
        pat = _re.compile(r"^" + _re.escape(pstem) + r"(\d+)$")
        found: list[tuple[int, str]] = []
        try:
            for f in os.listdir(pdir):
                if not f.lower().endswith(".pal"):
                    continue
                m = pat.match(os.path.splitext(f)[0])
                if m:
                    found.append((int(m.group(1)), os.path.join(pdir, f)))
        except OSError:
            return []
        if len(found) < 2:
            return []
        found.sort(key=lambda pair: pair[0])
        return [p for _, p in found]

    if split_pals:
        # Multi-file split palette source — write back to each .pal in
        # order on save. The matching ``<stem>.gbapal`` (if present) is
        # a build artifact and stays out of ``best_pals``.
        best_pals = split_pals
    elif engine_pals:
        # The engine names this tilemap's palette in the same block as the
        # tilemap itself, which beats matching on filename — the editor used to
        # find nothing at all for 128 of 297 tilemaps and silently fall back to
        # whatever colours the PNG happened to bake in.
        best_pals = []
        for p in engine_pals:
            best_pals.extend(_split_sources_of(p) or [p])
    elif matching_pals:
        first_colors = _read_colors(matching_pals[0])
        if len(first_colors) > 16:
            best_pals = matching_pals[:1]
        else:
            best_pals = matching_pals
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
        else:
            best_pals = []

    return TilemapAssets(
        bin_path=bin_path,
        tile_sheets=sheets,
        pal_files=pals,
        best_sheet=best_sheet,
        best_pals=best_pals,
        slot_pals=engine_slot_pals,
    )
