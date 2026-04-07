"""Parser + writer for pokefirered graphics data tables.

Handles:
  * gMonFrontPicCoords[] / gMonBackPicCoords[]
      src/data/pokemon_graphics/{front,back}_pic_coordinates.h
      Entries look like:
          [SPECIES_X] =
          {
              .size = MON_COORDS_SIZE(40, 40),
              .y_offset = 16,
          },

  * gEnemyMonElevation[]
      src/data/pokemon_graphics/enemy_mon_elevation.h
      Sparse list:
          [SPECIES_X] = 8,
      Missing species = 0.

  * gMonIconPaletteIndices[]
      src/pokemon_icon.c
      Dense list:
          [SPECIES_X] = 0|1|2,
"""

from __future__ import annotations

import os
import re
from typing import Dict, Optional


# ── File paths relative to project root ─────────────────────────────────────

FRONT_COORDS_REL = "src/data/pokemon_graphics/front_pic_coordinates.h"
BACK_COORDS_REL = "src/data/pokemon_graphics/back_pic_coordinates.h"
ELEVATION_REL = "src/data/pokemon_graphics/enemy_mon_elevation.h"
ICON_PAL_IDX_REL = "src/pokemon_icon.c"


# ── Regex patterns ──────────────────────────────────────────────────────────

_COORD_BLOCK_RE = re.compile(
    r"\[(?P<sp>SPECIES_[A-Z0-9_]+)\]\s*=\s*\{\s*"
    r"\.size\s*=\s*MON_COORDS_SIZE\(\s*(?P<w>\d+)\s*,\s*(?P<h>\d+)\s*\)\s*,\s*"
    r"\.y_offset\s*=\s*(?P<y>-?\d+)\s*,?\s*\}",
    re.DOTALL,
)

_ELEVATION_RE = re.compile(
    r"\[(?P<sp>SPECIES_[A-Z0-9_]+)\]\s*=\s*(?P<v>\d+)\s*,",
)

_ICON_IDX_RE = re.compile(
    r"\[(?P<sp>SPECIES_[A-Z0-9_]+)\]\s*=\s*(?P<v>\d+)\s*,",
)


# ── Readers ─────────────────────────────────────────────────────────────────

def _read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def parse_pic_coords(path: str) -> Dict[str, dict]:
    """Return { 'SPECIES_XXX': {'width': w, 'height': h, 'y_offset': y} }."""
    text = _read_text(path)
    out: Dict[str, dict] = {}
    for m in _COORD_BLOCK_RE.finditer(text):
        out[m.group("sp")] = {
            "width": int(m.group("w")),
            "height": int(m.group("h")),
            "y_offset": int(m.group("y")),
        }
    return out


def parse_enemy_elevation(path: str) -> Dict[str, int]:
    """Return { 'SPECIES_XXX': elevation } for species with nonzero values."""
    text = _read_text(path)
    out: Dict[str, int] = {}
    for m in _ELEVATION_RE.finditer(text):
        out[m.group("sp")] = int(m.group("v"))
    return out


def parse_icon_palette_indices(path: str) -> Dict[str, int]:
    """Return { 'SPECIES_XXX': 0|1|2 }.

    Scans src/pokemon_icon.c for the block beginning with
    'const u8 gMonIconPaletteIndices[] ='.
    """
    text = _read_text(path)
    # Locate the table by anchor text and then parse from there.
    anchor = text.find("gMonIconPaletteIndices")
    if anchor < 0:
        return {}
    # Find opening brace after the anchor
    brace = text.find("{", anchor)
    end = text.find("};", brace)
    if brace < 0 or end < 0:
        return {}
    body = text[brace:end]
    out: Dict[str, int] = {}
    for m in _ICON_IDX_RE.finditer(body):
        out[m.group("sp")] = int(m.group("v"))
    return out


# ── Writers ─────────────────────────────────────────────────────────────────

def update_pic_coord_y_offset(path: str, species: str, new_y: int) -> bool:
    """Rewrite the y_offset for a single species entry in a pic_coords file.

    Keeps the .size(w,h) intact. Returns True on success.
    """
    text = _read_text(path)
    if not text:
        return False
    # Build a specific pattern for this species
    pat = re.compile(
        r"(\[" + re.escape(species) + r"\]\s*=\s*\{\s*"
        r"\.size\s*=\s*MON_COORDS_SIZE\(\s*\d+\s*,\s*\d+\s*\)\s*,\s*"
        r"\.y_offset\s*=\s*)-?\d+(\s*,?\s*\})",
        re.DOTALL,
    )
    new_text, n = pat.subn(lambda m: f"{m.group(1)}{new_y}{m.group(2)}", text)
    if n == 0:
        return False
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(new_text)
        return True
    except Exception:
        return False


def update_enemy_elevation(path: str, species: str, new_val: int) -> bool:
    """Update (or add, or remove-when-zero) an elevation entry for species.

    The table is sparse — a missing entry means 0.  If new_val == 0 we
    remove the species' line; otherwise we insert or update it.
    """
    text = _read_text(path)
    if not text:
        return False
    # Try to replace an existing entry first
    pat = re.compile(
        r"(\[" + re.escape(species) + r"\]\s*=\s*)\d+(\s*,)"
    )
    m = pat.search(text)
    if m:
        if new_val == 0:
            # Strip the whole line
            line_start = text.rfind("\n", 0, m.start()) + 1
            line_end = text.find("\n", m.end())
            if line_end < 0:
                line_end = len(text)
            new_text = text[:line_start] + text[line_end + 1:]
        else:
            new_text = pat.sub(lambda mm: f"{mm.group(1)}{new_val}{mm.group(2)}", text, count=1)
    else:
        if new_val == 0:
            return True  # nothing to do
        # Insert before the closing '};' of the array
        end_brace = text.rfind("};")
        if end_brace < 0:
            return False
        insertion = f"    [{species}] = {new_val},\n"
        new_text = text[:end_brace] + insertion + text[end_brace:]
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(new_text)
        return True
    except Exception:
        return False


def update_icon_palette_index(path: str, species: str, new_idx: int) -> bool:
    """Rewrite the palette index for a species in gMonIconPaletteIndices[]."""
    text = _read_text(path)
    if not text:
        return False
    # The pattern needs to only match inside the gMonIconPaletteIndices block.
    anchor = text.find("gMonIconPaletteIndices")
    if anchor < 0:
        return False
    brace = text.find("{", anchor)
    end = text.find("};", brace)
    if brace < 0 or end < 0:
        return False
    before, body, after = text[:brace], text[brace:end], text[end:]
    pat = re.compile(
        r"(\[" + re.escape(species) + r"\]\s*=\s*)\d+(\s*,)"
    )
    new_body, n = pat.subn(
        lambda m: f"{m.group(1)}{int(new_idx) & 0xff}{m.group(2)}",
        body,
        count=1,
    )
    if n == 0:
        return False
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(before + new_body + after)
        return True
    except Exception:
        return False


# ── Convenience loader for the whole dataset ────────────────────────────────

class GraphicsDataCache:
    """In-memory cache of all graphics tables for a project.

    Loaded once, mutated in memory, flushed to disk via save_all().
    """

    def __init__(self, project_root: str):
        self.root = project_root
        self.front: Dict[str, dict] = {}
        self.back: Dict[str, dict] = {}
        self.elevation: Dict[str, int] = {}
        self.icon_idx: Dict[str, int] = {}
        self._dirty_front: set[str] = set()
        self._dirty_back: set[str] = set()
        self._dirty_elev: set[str] = set()
        self._dirty_icon: set[str] = set()

    def load(self) -> None:
        self.front = parse_pic_coords(os.path.join(self.root, FRONT_COORDS_REL))
        self.back = parse_pic_coords(os.path.join(self.root, BACK_COORDS_REL))
        self.elevation = parse_enemy_elevation(os.path.join(self.root, ELEVATION_REL))
        self.icon_idx = parse_icon_palette_indices(os.path.join(self.root, ICON_PAL_IDX_REL))

    def get_front_y(self, species: str) -> int:
        return int(self.front.get(species, {}).get("y_offset", 0))

    def get_back_y(self, species: str) -> int:
        return int(self.back.get(species, {}).get("y_offset", 0))

    def get_elevation(self, species: str) -> int:
        return int(self.elevation.get(species, 0))

    def get_icon_idx(self, species: str) -> int:
        return int(self.icon_idx.get(species, 0))

    def set_front_y(self, species: str, value: int) -> None:
        entry = self.front.setdefault(species, {"width": 64, "height": 64, "y_offset": 0})
        if entry.get("y_offset") != value:
            entry["y_offset"] = int(value)
            self._dirty_front.add(species)

    def set_back_y(self, species: str, value: int) -> None:
        entry = self.back.setdefault(species, {"width": 64, "height": 64, "y_offset": 0})
        if entry.get("y_offset") != value:
            entry["y_offset"] = int(value)
            self._dirty_back.add(species)

    def set_elevation(self, species: str, value: int) -> None:
        value = int(value)
        current = self.elevation.get(species, 0)
        if current != value:
            if value == 0:
                self.elevation.pop(species, None)
            else:
                self.elevation[species] = value
            self._dirty_elev.add(species)

    def set_icon_idx(self, species: str, value: int) -> None:
        value = max(0, min(2, int(value)))
        if self.icon_idx.get(species) != value:
            self.icon_idx[species] = value
            self._dirty_icon.add(species)

    def has_pending_changes(self) -> bool:
        return bool(
            self._dirty_front or self._dirty_back
            or self._dirty_elev or self._dirty_icon
        )

    def save_all(self) -> tuple[int, list[str]]:
        """Flush pending changes to disk.  Returns (ok_count, errors)."""
        errors: list[str] = []
        ok = 0
        # Front coords
        path = os.path.join(self.root, FRONT_COORDS_REL)
        for sp in list(self._dirty_front):
            if update_pic_coord_y_offset(path, sp, self.front[sp]["y_offset"]):
                ok += 1
            else:
                errors.append(f"front:{sp}")
        self._dirty_front.clear()
        # Back coords
        path = os.path.join(self.root, BACK_COORDS_REL)
        for sp in list(self._dirty_back):
            if update_pic_coord_y_offset(path, sp, self.back[sp]["y_offset"]):
                ok += 1
            else:
                errors.append(f"back:{sp}")
        self._dirty_back.clear()
        # Elevation
        path = os.path.join(self.root, ELEVATION_REL)
        for sp in list(self._dirty_elev):
            if update_enemy_elevation(path, sp, self.elevation.get(sp, 0)):
                ok += 1
            else:
                errors.append(f"elev:{sp}")
        self._dirty_elev.clear()
        # Icon palette index
        path = os.path.join(self.root, ICON_PAL_IDX_REL)
        for sp in list(self._dirty_icon):
            if update_icon_palette_index(path, sp, self.icon_idx.get(sp, 0)):
                ok += 1
            else:
                errors.append(f"icon:{sp}")
        self._dirty_icon.clear()
        return ok, errors


# ── Path helpers for per-species palette files ──────────────────────────────

def species_slug_from_const(species: str) -> str:
    if species.upper().startswith("SPECIES_"):
        return species[len("SPECIES_"):].lower()
    return species.lower()


def species_pal_paths(project_root: str, species: str) -> tuple[str, str]:
    """Return (normal_pal_path, shiny_pal_path) for a species constant."""
    slug = species_slug_from_const(species)
    base = os.path.join(project_root, "graphics", "pokemon", slug)
    return os.path.join(base, "normal.pal"), os.path.join(base, "shiny.pal")


def icon_palette_pal_path(project_root: str, idx: int) -> str:
    idx = max(0, min(2, int(idx)))
    return os.path.join(
        project_root, "graphics", "pokemon", "icon_palettes",
        f"icon_palette_{idx}.pal",
    )
