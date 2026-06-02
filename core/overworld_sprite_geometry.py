"""Overworld sprite geometry — the size model behind the New Sprite flow.

Replaces the old hardcoded ``_OAM_TABLE`` in ``overworld_sprite_creator.py``.
Given any frame size whose width and height are both multiples of 16, this
module decides whether the frame is a single GBA hardware sprite or a
composite that needs a subsprite table, and computes the exact
decomposition — which hardware pieces, positioned where, reading which
tiles.

GBA OBJ hardware can draw a sprite of at most 64x64, in one of 12 fixed
shapes.  Anything larger — or any non-hardware shape — is built by
compositing several hardware sprites through a ``SubspriteTable``.  See
``docs/OVERWORLD_EDITOR_UPGRADE_PLAN.md`` for the full rationale.

This module is PURE geometry: no file writes, no engine mutation.  The
scan helpers read project headers read-only so callers can tell which
OAM templates / subsprite tables already exist.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import List, Optional, Set, Tuple

# The 12 GBA OBJ hardware sprite shapes, (width, height) in pixels.
HARDWARE_SHAPES: Set[Tuple[int, int]] = {
    (8, 8), (16, 16), (32, 32), (64, 64),     # square
    (16, 8), (32, 8), (32, 16), (64, 32),     # horizontal
    (8, 16), (8, 32), (16, 32), (32, 64),     # vertical
}

# Practical ceiling for an overworld object sprite.  The engine could
# composite bigger, but a 256x256 4bpp frame is 32 KB — the entire OBJ
# VRAM — so anything past this is unusable in practice.
MAX_DIM = 256

# Piece-count threshold past which a size is flagged as OAM-slot-heavy.
_OAM_SLOT_WARN = 16

# VRAM-per-frame threshold (bytes) past which a size is flagged as large.
_VRAM_WARN = 4096

# WxH of every vanilla single-OAM sprite that ships a subsprite table the
# generator can safely reuse.  These tables are 1-piece-per-elevation-tier
# (and carry vanilla's priority-band variants), so a single-OAM sprite of
# one of these shapes keeps its vanilla table.  Vanilla's COMPOSITE tables
# (48x48, 88x32, 96x40, 128x64) are deliberately EXCLUDED: they are
# hand-tuned non-uniform-grid layouts, incompatible with the gbagfx
# metatile decomposition — a composite that bound to one would render
# shredded.  Composites always get a freshly generated ``Ps``-named table.
_REUSABLE_VANILLA_TABLES: Set[Tuple[int, int]] = {
    (16, 16), (16, 32), (32, 32), (64, 32), (64, 64),
}


@dataclass
class SubspritePiece:
    """One hardware sprite within a composite.

    ``x``/``y`` are the offset (in pixels) of the piece's TOP-LEFT
    corner from the composite sprite's center.  This is the convention
    vanilla pokefirered uses in ``object_event_subsprites.h`` and that
    ``AddSubspritesToOamBuffer`` consumes — ``destOam.x = baseX + x``
    where ``destOam.x`` is an OAM top-left coordinate, so for the
    left/top-most piece ``x``/``y`` is ``-W/2`` / ``-H/2``.  (It is NOT
    the piece's centre offset — adding ``piece_w/2`` shifts the whole
    composite half a piece down-right off its placement tile.)
    ``tile_offset`` is the index of the first 8x8 tile this piece reads
    within the frame's tile block.
    """
    x: int
    y: int
    shape_w: int
    shape_h: int
    tile_offset: int


@dataclass
class Decomposition:
    """The full geometry result for one frame size."""
    width: int
    height: int
    is_single_oam: bool
    oam_symbol: str               # gObjectEventBaseOam_* to reference
    piece_w: int                  # uniform piece pixel width
    piece_h: int                  # uniform piece pixel height
    cols: int
    rows: int
    # gObjectEventSpriteOamTables_* the GraphicsInfo's .subspriteTables
    # points at.  A reusable vanilla single-OAM table keeps its vanilla
    # name; everything the generator emits gets a ``Ps`` infix so it can
    # never collide with a vanilla table of the same WxH.
    subsprite_symbol: str = ""
    subsprite_array_symbol: str = ""
    pieces: List[SubspritePiece] = field(default_factory=list)

    @property
    def piece_count(self) -> int:
        return len(self.pieces)

    @property
    def vram_bytes(self) -> int:
        """OBJ VRAM cost of ONE frame (4bpp = width*height/2 bytes)."""
        return self.width * self.height // 2

    @property
    def size_field(self) -> int:
        """Value for the GraphicsInfo ``.size`` field (bytes per frame)."""
        return self.width * self.height // 2

    @property
    def metatile_w(self) -> int:
        """gbagfx ``-mwidth`` value: the uniform piece width in tiles."""
        return self.piece_w // 8

    @property
    def metatile_h(self) -> int:
        """gbagfx ``-mheight`` value: the uniform piece height in tiles."""
        return self.piece_h // 8

    @property
    def tiles_per_frame(self) -> int:
        return (self.width // 8) * (self.height // 8)


# ── validation ──────────────────────────────────────────────────────────

def validate(w: int, h: int) -> Tuple[bool, List[str]]:
    """Return ``(ok, reasons)``.  ``reasons`` is empty when ``ok`` is True.

    The single rule that matters: both dimensions are positive multiples
    of 16 within the practical size ceiling.  (16, not 8: a 16-multiple
    frame is a whole number of map tiles, tiles into 16x16-or-bigger
    hardware pieces, and centres cleanly on its placement tile — an
    8-but-not-16 size can only explode into 8x8 pieces.)  Cost concerns
    (OAM slots, VRAM) are NOT failures — they're surfaced separately by
    ``cost_warnings`` so the dialog can warn without blocking.
    """
    reasons: List[str] = []
    if w <= 0 or h <= 0:
        reasons.append("Width and height must both be positive.")
        return False, reasons
    if w % 16 != 0:
        reasons.append(
            f"Width {w}px is not a multiple of 16 — overworld sprite "
            f"frames must be a whole number of 16px tiles so they tile "
            f"into hardware pieces cleanly and sit centred on their map "
            f"tile.  Pad to {((w // 16) + 1) * 16}px."
        )
    if h % 16 != 0:
        reasons.append(
            f"Height {h}px is not a multiple of 16 — overworld sprite "
            f"frames must be a whole number of 16px tiles so they tile "
            f"into hardware pieces cleanly and sit centred on their map "
            f"tile.  Pad to {((h // 16) + 1) * 16}px."
        )
    if w > MAX_DIM or h > MAX_DIM:
        reasons.append(
            f"Maximum supported dimension is {MAX_DIM}px "
            f"(this frame is {w}x{h})."
        )
    return (not reasons), reasons


def is_hardware_shape(w: int, h: int) -> bool:
    """True when ``w x h`` is one of the 12 GBA hardware sprite shapes."""
    return (w, h) in HARDWARE_SHAPES


# ── decomposition ───────────────────────────────────────────────────────

def _largest_uniform_piece(w: int, h: int) -> Tuple[int, int]:
    """Pick the hardware shape ``(pw, ph)`` that uniformly tiles ``w x h``
    with the fewest pieces.

    ``(8, 8)`` evenly divides any multiple-of-8 size, so a result is
    always found.  Ties on piece count are broken toward the larger
    piece area (better VRAM locality, matches how vanilla hand-tuned
    tables tend to look).
    """
    best: Optional[Tuple[int, int, int, int]] = None  # (count, -area, pw, ph)
    for (pw, ph) in HARDWARE_SHAPES:
        if w % pw == 0 and h % ph == 0:
            count = (w // pw) * (h // ph)
            key = (count, -(pw * ph), pw, ph)
            if best is None or key < best:
                best = key
    if best is None:  # unreachable for multiple-of-8 input
        return 8, 8
    return best[2], best[3]


def _compute_pieces(
    w: int, h: int, pw: int, ph: int,
) -> List[SubspritePiece]:
    """Tile a ``w x h`` frame with a uniform ``cols x rows`` grid of
    ``pw x ph`` pieces and return the positioned ``SubspritePiece`` list.

    Kept as a standalone function so the x/y/tile_offset math can be
    verified directly against vanilla subsprite tables.
    """
    cols = w // pw
    rows = h // ph
    tiles_per_piece = (pw // 8) * (ph // 8)
    pieces: List[SubspritePiece] = []
    for row in range(rows):
        for col in range(cols):
            # x/y are the piece's TOP-LEFT corner relative to the
            # composite centre (col*pw is the piece's left edge; w//2
            # is half the frame).  No +pw/2 — that would make x the
            # piece CENTRE and shove the whole sprite half a piece
            # down-right of its placement tile.
            pieces.append(SubspritePiece(
                x=col * pw - w // 2,
                y=row * ph - h // 2,
                shape_w=pw,
                shape_h=ph,
                tile_offset=(row * cols + col) * tiles_per_piece,
            ))
    return pieces


def decompose(w: int, h: int) -> Decomposition:
    """Compute the full geometry for a ``w x h`` frame.

    Raises ``ValueError`` if the size fails ``validate``.
    """
    ok, reasons = validate(w, h)
    if not ok:
        raise ValueError("; ".join(reasons))

    single = is_hardware_shape(w, h)
    if single:
        pw, ph = w, h
        oam = f"gObjectEventBaseOam_{w}x{h}"
    else:
        pw, ph = _largest_uniform_piece(w, h)
        # Composite sprites set .oam to a dummy 8x8 base — the subsprite
        # table does the real compositing.  This is exactly what vanilla
        # SS Anne (128x64) does.
        oam = "gObjectEventBaseOam_8x8"

    # Subsprite table symbol.  A single-OAM sprite of a shape vanilla
    # ships a grid-compatible table for reuses that table (keeping its
    # elevation priority-band variants).  Everything else — every
    # composite, plus single-OAM shapes vanilla has no table for — gets
    # a PorySuite-generated table under a ``Ps`` name, so a composite can
    # never bind to a vanilla table of the same WxH whose hand-tuned
    # non-grid layout would shred the metatile tile data.
    if single and (w, h) in _REUSABLE_VANILLA_TABLES:
        sub_sym = f"gObjectEventSpriteOamTables_{w}x{h}"
        arr_sym = f"gObjectEventSpriteOamTable_{w}x{h}"
    else:
        sub_sym = f"gObjectEventSpriteOamTables_Ps{w}x{h}"
        arr_sym = f"gObjectEventSpriteOamTable_Ps{w}x{h}"

    pieces = _compute_pieces(w, h, pw, ph)
    return Decomposition(
        width=w, height=h, is_single_oam=single,
        oam_symbol=oam, piece_w=pw, piece_h=ph,
        cols=w // pw, rows=h // ph,
        subsprite_symbol=sub_sym, subsprite_array_symbol=arr_sym,
        pieces=pieces,
    )


# ── cost reporting (for the New Sprite dialog) ──────────────────────────

def cost_warnings(d: Decomposition) -> List[str]:
    """Non-fatal warnings about a decomposition's runtime cost.

    These do NOT block creation — they let the dialog tell the user when
    a size is wasteful so they can choose to pad to something friendlier.
    """
    warns: List[str] = []
    if d.piece_count > _OAM_SLOT_WARN:
        warns.append(
            f"This size composites into {d.piece_count} hardware sprites, "
            f"each using one of the GBA's 128 OAM slots.  Padding to a "
            f"size whose width and height are both divisible by 32 (or 64) "
            f"lets it tile into larger pieces and cuts the slot count "
            f"sharply."
        )
    if d.vram_bytes > _VRAM_WARN:
        warns.append(
            f"Each frame is {d.vram_bytes} bytes of OBJ VRAM.  Large "
            f"sprites can crowd others off busy maps."
        )
    return warns


def describe(d: Decomposition) -> str:
    """One-line human summary of a decomposition, for the dialog."""
    if d.is_single_oam:
        return (
            f"{d.width}x{d.height} — single hardware sprite, "
            f"1 OAM slot, {d.vram_bytes} B VRAM/frame."
        )
    return (
        f"{d.width}x{d.height} — composite of {d.piece_count} "
        f"{d.piece_w}x{d.piece_h} pieces, {d.piece_count} OAM slots, "
        f"{d.vram_bytes} B VRAM/frame."
    )


# ── sprite-sheet frame layout ───────────────────────────────────────────

def detect_frame_size(image_w: int, image_h: int) -> Tuple[int, int]:
    """Best-guess ``(frame_w, frame_h)`` for an imported sprite sheet.

    A horizontal sprite strip is exactly one frame tall, so the frame
    height is the image height.  A square or taller-than-wide image is
    treated as a single frame.  A wider-than-tall image is treated as a
    multi-frame strip and sliced into equal frames — preferring 16-px
    frames (the standard NPC width), then square frames, then
    half-height — taking the first width that divides the sheet evenly.

    A dimension that is not a multiple of 16 is returned unchanged so the
    caller's validation can flag it.
    """
    fw, fh = image_w, image_h
    if image_w % 16 == 0 and image_h % 16 == 0 and image_w > image_h > 0:
        for cand in (16, image_h, image_h // 2):
            if (16 <= cand <= image_w and cand % 16 == 0
                    and image_w % cand == 0 and image_w // cand > 1):
                fw = cand
                break
    return fw, fh


def frame_count_options(image_w: int) -> List[int]:
    """Every frame count that slices an ``image_w``-wide sheet into whole
    frames whose width is a positive multiple of 16, sorted ascending.

    Always includes 1 (the whole image as a single frame).  This is the
    set the New Sprite dialog snaps its Frames control to, so the user
    can never pick a count that leaves a partial or non-16px-aligned
    frame.
    """
    if image_w <= 0:
        return [1]
    return [
        c for c in range(1, image_w // 16 + 1)
        if image_w % c == 0 and (image_w // c) % 16 == 0
    ]


# ── project introspection (read-only) ───────────────────────────────────

def scan_oam_templates(project_root: str) -> Set[Tuple[int, int]]:
    """Return the set of ``(w, h)`` for every ``gObjectEventBaseOam_WxH``
    template defined in the project's ``base_oam.h``.
    """
    path = os.path.join(
        project_root, "src", "data", "object_events", "base_oam.h",
    )
    out: Set[Tuple[int, int]] = set()
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return out
    for m in re.finditer(
        r"gObjectEventBaseOam_(\d+)x(\d+)\s*=", text,
    ):
        out.add((int(m.group(1)), int(m.group(2))))
    return out


def scan_subsprite_tables(project_root: str) -> Set[Tuple[int, int]]:
    """Return the set of ``(w, h)`` for every
    ``gObjectEventSpriteOamTables_WxH[]`` defined in the project's
    ``object_event_subsprites.h``.
    """
    path = os.path.join(
        project_root, "src", "data", "object_events",
        "object_event_subsprites.h",
    )
    out: Set[Tuple[int, int]] = set()
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return out
    for m in re.finditer(
        r"gObjectEventSpriteOamTables_(?:Ps)?(\d+)x(\d+)\s*\[\]", text,
    ):
        out.add((int(m.group(1)), int(m.group(2))))
    return out
