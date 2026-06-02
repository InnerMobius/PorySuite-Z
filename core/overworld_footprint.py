"""Overworld sprite collision footprint — per-GraphicsInfo solid mask.

Background
==========

A composite overworld sprite in vanilla pokefirered blocks ONLY its
single anchor tile — the player walks through the rest of the sprite's
body even when the art clearly shows obstruction.  This module is the
data layer for an opt-in multi-cell footprint per ``OBJ_EVENT_GFX_*``:
the user paints which 8x8-pixel cells of the sprite art are solid, and
at runtime every painted cell BOTH blocks the player AND triggers the
object's interaction script when the player presses A facing it.

Scope is PURE DATA.  This module ships:

- ``Footprint``           — the per-sprite dataclass.
- ``empty_footprint``     — construct a footprint sized to a sprite.
- ``serialize_footprints``/``parse_footprints`` — C ↔ Python.
- ``parse_project_footprints`` — read a project's existing footprints
                            (empty list when the file doesn't exist).

The engine hook (collision + interaction patcher) and the editor
dialog live in their own modules.  Sprites with NO entry in this
module's output table behave exactly like vanilla — a single 1-tile
block at the anchor coords.

On-disk layout
==============

``src/data/object_events/object_event_footprints.h`` — created lazily,
edited only by PorySuite.  Per-sprite blocks sit inside sentinel
fences so the writer can refresh one entry without touching the rest;
the lookup table at the bottom is regenerated in full on every save::

    // PORYSUITE-GEN BEGIN footprint OBJ_EVENT_GFX_KING_ZORA
    static const u8 sFootprintCells_KingZora[] = {
        /* row 0 */ 0, 0, 1, 1, 0, 0,
        ...
    };
    static const struct ObjectFootprint sFootprint_KingZora = {
        .width = 6,
        .height = 6,
        .cells = sFootprintCells_KingZora,
    };
    // PORYSUITE-GEN END footprint OBJ_EVENT_GFX_KING_ZORA

    // PORYSUITE-GEN BEGIN table
    const struct ObjectFootprint *const gObjectEventFootprints[] = {
        [OBJ_EVENT_GFX_KING_ZORA] = &sFootprint_KingZora,
        ...
    };
    // PORYSUITE-GEN END table

Sparse entries are intentional — any ``OBJ_EVENT_GFX_*`` not listed in
the lookup table defaults to ``NULL`` under C's array-init rules, and
the engine treats ``NULL`` as "no footprint, vanilla single-tile
behaviour."

CRITICAL — array MUST be sized ``[NUM_OBJ_EVENT_GFX]``
======================================================

C99 sizes a designated-initializer array to ``max(designator) + 1`` when
no explicit size is given.  If we emit::

    const struct ObjectFootprint *const gObjectEventFootprints[] = {
        [OBJ_EVENT_GFX_SCHULE] = &sFootprint_Schule,
    };

…and ``OBJ_EVENT_GFX_SCHULE = 159`` is the highest-numbered footprint,
the array is sized to 160 slots (indexes 0..159).  Any ``OBJ_EVENT_GFX_*``
with a numeric value >= 160 (e.g. ``OBJ_EVENT_GFX_SCARECROW = 160``) then
indexes off the end — undefined behaviour.  The engine reads garbage
memory as a ``struct ObjectFootprint *``, dereferences it for
``->width`` / ``->height`` / ``->cells``, and does tens of thousands of
bogus AABB checks per frame (lag + phantom collision walls + softlocks).

The fix is forcing the explicit size ``[NUM_OBJ_EVENT_GFX]`` so EVERY
graphics ID — populated or not, present at patch time or added later —
has a defined slot that's NULL by default.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import List, Sequence


# One footprint cell covers an 8x8-pixel square of the sprite art.
CELL_PX = 8


# Relative path of the header PorySuite owns.
_FOOTPRINT_HEADER_REL = os.path.join(
    "src", "data", "object_events", "object_event_footprints.h",
)


# Sentinel fences — must stay byte-stable so the parser and the
# engine patcher can both lock onto them.
_BEGIN_FENCE = "// PORYSUITE-GEN BEGIN footprint "
_END_FENCE = "// PORYSUITE-GEN END footprint "
_TABLE_BEGIN = "// PORYSUITE-GEN BEGIN table"
_TABLE_END = "// PORYSUITE-GEN END table"


# ─────────────────────────────────────────────────────────── dataclass ──

@dataclass
class Footprint:
    """One sprite's collision footprint.

    ``cells`` is row-major: ``cells[row][col]`` is True iff the 8x8
    pixel square at ``[col*8 .. col*8+8) x [row*8 .. row*8+8)`` of the
    sprite art blocks the player AND triggers the object's
    interaction script when the player presses A while facing it.

    The grid origin matches the sheet PNG's top-left (0, 0).  ``width``
    / ``height`` are in cells, not pixels — a 16x32-pixel sprite is
    2x4 cells, a 48x48 is 6x6, a 128x64 is 16x8.
    """

    gfx_const: str
    width: int
    height: int
    cells: List[List[bool]] = field(default_factory=list)

    def __post_init__(self):
        # Coerce ``cells`` to exactly height rows of width cells so
        # the writer doesn't have to defend against ragged input.
        # Existing values are preserved where they fit.
        new_cells: List[List[bool]] = []
        for r in range(self.height):
            if r < len(self.cells):
                src = list(self.cells[r])
            else:
                src = []
            if len(src) < self.width:
                src = src + [False] * (self.width - len(src))
            elif len(src) > self.width:
                src = src[: self.width]
            new_cells.append([bool(v) for v in src])
        self.cells = new_cells

    # ── queries ───────────────────────────────────────────────────────
    @property
    def solid_count(self) -> int:
        return sum(1 for row in self.cells for v in row if v)

    @property
    def is_empty(self) -> bool:
        return self.solid_count == 0

    def is_solid(self, col: int, row: int) -> bool:
        if 0 <= row < self.height and 0 <= col < self.width:
            return bool(self.cells[row][col])
        return False

    # ── mutations ─────────────────────────────────────────────────────
    def set_cell(self, col: int, row: int, solid: bool) -> None:
        if 0 <= row < self.height and 0 <= col < self.width:
            self.cells[row][col] = bool(solid)


def empty_footprint(
    gfx_const: str, frame_w_px: int, frame_h_px: int,
) -> Footprint:
    """Return a Footprint sized to a sprite's frame dimensions, all clear.

    Raises ``ValueError`` if the frame size isn't a positive multiple
    of ``CELL_PX`` — a partial 8x8 cell makes no sense at the footprint
    granularity.
    """
    if frame_w_px <= 0 or frame_h_px <= 0:
        raise ValueError(
            f"Frame size must be positive (got {frame_w_px}x{frame_h_px}).")
    if frame_w_px % CELL_PX or frame_h_px % CELL_PX:
        raise ValueError(
            f"Frame size {frame_w_px}x{frame_h_px}px is not a multiple of "
            f"{CELL_PX}px — cannot build a cell-aligned footprint.")
    return Footprint(
        gfx_const=gfx_const,
        width=frame_w_px // CELL_PX,
        height=frame_h_px // CELL_PX,
    )


# ───────────────────────────────────────────────────────────── pascalize ──

def _pascalize_gfx_const(gfx_const: str) -> str:
    """``OBJ_EVENT_GFX_KING_ZORA`` -> ``KingZora`` for use in C symbols.

    Falls back to the raw constant (just the prefix stripped) when the
    input doesn't follow the prefix convention, so a project that has
    renamed/added graphics constants still gets a stable symbol name.
    """
    name = gfx_const
    if name.startswith("OBJ_EVENT_GFX_"):
        name = name[len("OBJ_EVENT_GFX_"):]
    parts = [part for part in name.split("_") if part]
    if not parts:
        return name or "Unknown"
    return "".join(part.capitalize() for part in parts)


# ─────────────────────────────────────────────────────────────── codegen ──

def encode_footprint_block(fp: Footprint) -> str:
    """Return the C source block for a single footprint.

    The block is fenced with PORYSUITE-GEN sentinels keyed by
    ``fp.gfx_const`` so a future save can replace one footprint in
    place without touching anything else in the file.  ``cells`` is
    emitted one byte per cell (0 = open, 1 = solid) row-major so the
    engine can read it with ``cells[row * width + col]``.
    """
    name = _pascalize_gfx_const(fp.gfx_const)
    cells_sym = f"sFootprintCells_{name}"
    info_sym = f"sFootprint_{name}"

    lines = [f"{_BEGIN_FENCE}{fp.gfx_const}"]
    lines.append(f"static const u8 {cells_sym}[] = {{")
    for r in range(fp.height):
        row_vals = ", ".join(
            "1" if fp.cells[r][c] else "0" for c in range(fp.width)
        )
        lines.append(f"    /* row {r} */ {row_vals},")
    lines.append("};")
    lines.append(f"static const struct ObjectFootprint {info_sym} = {{")
    lines.append(f"    .width = {fp.width},")
    lines.append(f"    .height = {fp.height},")
    lines.append(f"    .cells = {cells_sym},")
    lines.append("};")
    lines.append(f"{_END_FENCE}{fp.gfx_const}")
    return "\n".join(lines)


def serialize_footprints(footprints: Sequence[Footprint]) -> str:
    """Render the full ``object_event_footprints.h`` file content.

    Footprints are emitted in the given order — callers usually sort
    by ``gfx_const`` first for stable diffs.  Empty footprints
    (no solid cells) are SKIPPED on purpose: defining them in the
    lookup table would change nothing at runtime, and emitting their
    block would leave orphan data once a user cleared the last cell
    and saved.
    """
    nonempty = [fp for fp in footprints if not fp.is_empty]
    parts: List[str] = [
        "// Generated by PorySuite-Z.  Do not edit by hand — re-run the",
        "// Edit Collision Footprint... dialog instead.  Per-footprint",
        "// blocks are fenced by PORYSUITE-GEN sentinels; the lookup",
        "// table at the bottom is rebuilt in full on every save.",
        "",
        '#include "object_footprint.h"',
        '#include "constants/event_objects.h"',
        "",
    ]
    for fp in nonempty:
        parts.append(encode_footprint_block(fp))
        parts.append("")

    parts.append(_TABLE_BEGIN)
    # CRITICAL: the array MUST be sized [NUM_OBJ_EVENT_GFX].  Without an
    # explicit size, C sizes the array to max(designator) + 1, which is
    # smaller than NUM_OBJ_EVENT_GFX whenever the highest-numbered sprite
    # has no footprint.  The engine indexes this array by graphicsId on
    # every collision/interaction check; a too-short array reads off the
    # end into garbage memory and dereferences it as a footprint struct —
    # phantom collision walls, lag, softlocks.  See the module docstring
    # for the full failure mode.  ``NUM_OBJ_EVENT_GFX`` is the standard
    # pokefirered constant exported from include/constants/event_objects.h.
    parts.append(
        "const struct ObjectFootprint *const "
        "gObjectEventFootprints[NUM_OBJ_EVENT_GFX] = {"
    )
    for fp in nonempty:
        info_sym = f"sFootprint_{_pascalize_gfx_const(fp.gfx_const)}"
        parts.append(f"    [{fp.gfx_const}] = &{info_sym},")
    parts.append("};")
    parts.append(_TABLE_END)
    parts.append("")  # trailing newline
    return "\n".join(parts)


# ─────────────────────────────────────────────────────────────── parsing ──

_FENCE_BLOCK_RE = re.compile(
    r"^// PORYSUITE-GEN BEGIN footprint (\S+)\s*$"
    r"(?P<body>.*?)"
    r"^// PORYSUITE-GEN END footprint \1\s*$",
    re.DOTALL | re.MULTILINE,
)
# The ``\1`` backreference forces the END fence's gfx_const to match
# the BEGIN's, so a half-rewritten file can never be misread as a
# complete block.

_WIDTH_RE = re.compile(r"\.width\s*=\s*(\d+)")
_HEIGHT_RE = re.compile(r"\.height\s*=\s*(\d+)")
_CELLS_RE = re.compile(
    r"sFootprintCells_\w+\[\]\s*=\s*\{(.*?)\};", re.DOTALL,
)


def parse_footprints(text: str) -> List[Footprint]:
    """Extract every fenced footprint block from a header file's text.

    Returns the footprints in the order they appear.  Blocks whose
    interior cannot be parsed (corrupt or hand-edited) are SKIPPED so
    one bad entry can't crash a save — the affected sprite simply
    behaves as "no footprint" until re-saved through the dialog.
    """
    out: List[Footprint] = []
    for m in _FENCE_BLOCK_RE.finditer(text):
        gfx_const = m.group(1).strip()
        body = m.group("body")
        wm = _WIDTH_RE.search(body)
        hm = _HEIGHT_RE.search(body)
        cm = _CELLS_RE.search(body)
        if not (wm and hm and cm):
            continue
        width = int(wm.group(1))
        height = int(hm.group(1))
        if width <= 0 or height <= 0:
            continue
        # Strip C comments (/* row N */) and read every integer literal.
        raw = re.sub(r"/\*.*?\*/", "", cm.group(1), flags=re.DOTALL)
        nums = [int(tok) for tok in re.findall(r"-?\d+", raw)]
        if len(nums) != width * height:
            continue
        cells = [
            [bool(nums[r * width + c]) for c in range(width)]
            for r in range(height)
        ]
        out.append(Footprint(
            gfx_const=gfx_const,
            width=width,
            height=height,
            cells=cells,
        ))
    return out


def footprint_header_path(project_root: str) -> str:
    """Absolute path of the project's footprint header."""
    return os.path.join(project_root, _FOOTPRINT_HEADER_REL)


def parse_project_footprints(project_root: str) -> List[Footprint]:
    """Read every footprint defined in the project's footprint header.

    Returns ``[]`` when the file doesn't exist yet — a fresh project
    that hasn't authored any footprints.  Never raises.
    """
    path = footprint_header_path(project_root)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return []
    return parse_footprints(text)
