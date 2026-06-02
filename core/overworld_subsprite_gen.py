"""Overworld OAM / subsprite scaffolding generator — Phase 2 of the
Overworld Editor arbitrary-dimensions upgrade.

Given a :class:`Decomposition` from ``overworld_sprite_geometry``, this
module emits the GBA OBJ scaffolding a frame size needs but the project
does not yet have:

* a **base OAM template** in ``base_oam.h`` — for the three single-sprite
  hardware shapes (8x16, 8x32, 32x64) vanilla pokefirered never defined;
* a **subsprite table** in ``object_event_subsprites.h`` — for any size
  that lacks one, whether a composite (48x48, 128x64, 64x96 …) or an
  odd single-OAM shape (8x8, 32x16, 32x64 …).

It is PURE C-text emission.  It never builds, never touches the engine's
behaviour — it only writes data tables the build then compiles.

Idempotency & cleanup
---------------------
Every generated block is fenced by ``PORYSUITE-GEN`` sentinel comments.
That fence is the registry: a size is "ours" iff its fence is present.
So the module is

* **idempotent** — ``ensure_*`` is a no-op when the symbol already
  exists, whether it is vanilla or a previous generation;
* **cleanly removable** — ``remove_*`` deletes only fenced blocks and
  never touches vanilla tables;
* **never destructive** — vanilla code carries no fence, so it is
  invisible to the remover.

This module does NOT decide whether a table is still *needed*.  The
caller (the sprite creator / deleter) owns the "is any sprite still this
size" reference check — exactly as it already does for pic tables and
palettes.  ``remove_*`` here just answers "is this block mine to take?".

See ``docs/OVERWORLD_EDITOR_UPGRADE_PLAN.md``.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import List, Set, Tuple

# ── header locations (relative to project root) ─────────────────────────

_SUBSPRITES_REL = (
    "src", "data", "object_events", "object_event_subsprites.h",
)
_BASE_OAM_REL = (
    "src", "data", "object_events", "base_oam.h",
)

# ── generation fence ────────────────────────────────────────────────────
# Every generated block sits between these two lines.  ``kind`` is
# ``"oam"`` or ``"subsprite"``; ``size`` is ``"WxH"``.

_MARK_OPEN = "// >>> PORYSUITE-GEN overworld-{kind} {size} >>>"
_MARK_CLOSE = "// <<< PORYSUITE-GEN overworld-{kind} {size} <<<"


@dataclass
class GenResult:
    """Outcome of one ``ensure_*`` / ``remove_*`` call.

    ``changed`` is True when a file was written.  ``symbol`` is the C
    symbol the caller can now rely on existing (after ``ensure_*``) or
    that was removed (after ``remove_*``).  ``detail`` is a one-line
    plain-English summary for the creator's user-facing log.
    """
    changed: bool
    symbol: str
    detail: str


# ── file IO ─────────────────────────────────────────────────────────────

def _read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def _atomic_write(path: str, text: str) -> None:
    """Write ``text`` to ``path`` atomically via temp + rename.

    A half-written engine header would break the build, so the new
    content is staged in a sibling ``.tmp`` and swapped in with
    ``os.replace`` (atomic on Win32 and POSIX).  Same discipline the
    sprite creator/deleter uses.
    """
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _block_regex(kind: str, w: int, h: int) -> "re.Pattern[str]":
    """A regex matching one whole fenced block (markers included), plus
    any blank lines hugging it, so removal leaves no gap."""
    size = f"{w}x{h}"
    return re.compile(
        r"\n*"
        + re.escape(_MARK_OPEN.format(kind=kind, size=size))
        + r".*?"
        + re.escape(_MARK_CLOSE.format(kind=kind, size=size))
        + r"\n*",
        re.DOTALL,
    )


# ── C emission ──────────────────────────────────────────────────────────

def _subsprite_block(d) -> str:
    """The full C text for one generated subsprite table.

    Emits a single ``struct Subsprite[]`` piece array followed by a
    six-entry ``struct SubspriteTable[]`` — every slot pointing at that
    one array.  This is the layout vanilla uses for its composite
    sprites (48x48, 96x40, 88x32): the six slots are the engine's
    draw-priority tiers, and a uniform sprite renders identically in
    every tier, so one array serves all six.

    The piece array is emitted *before* the table that references it —
    C needs the definition in scope, and both land in the same file.
    """
    w, h = d.width, d.height
    size = f"{w}x{h}"
    # Symbol names come from the decomposition — a composite (and any
    # generated single-OAM shape) gets a ``Ps``-infixed name so it never
    # collides with a vanilla gObjectEventSpriteOamTables_WxH table.
    arr = d.subsprite_array_symbol
    tbl = d.subsprite_symbol

    out: List[str] = [_MARK_OPEN.format(kind="subsprite", size=size)]
    out.append(
        f"// {d.piece_count} x {d.piece_w}x{d.piece_h} hardware "
        f"piece(s) tiling a {size} frame.  Generated by PorySuite-Z "
        f"— do not hand-edit."
    )
    out.append(f"const struct Subsprite {arr}[] = {{")
    for p in d.pieces:
        shp = f"{p.shape_w}x{p.shape_h}"
        out.append("    {")
        out.append(f"        .x = {p.x},")
        out.append(f"        .y = {p.y},")
        out.append(f"        .shape = SPRITE_SHAPE({shp}),")
        out.append(f"        .size = SPRITE_SIZE({shp}),")
        out.append(f"        .tileOffset = {p.tile_offset},")
        out.append("        .priority = 2,")
        out.append("    },")
    out.append("};")
    out.append("")
    out.append(f"const struct SubspriteTable {tbl}[] = {{")
    for _ in range(6):
        out.append(f"    {{{d.piece_count}, {arr}}},")
    out.append("};")
    out.append(_MARK_CLOSE.format(kind="subsprite", size=size))
    return "\n".join(out)


def _oam_block(w: int, h: int) -> str:
    """The full C text for one generated base OAM template — a plain
    ``struct OamData`` for a single GBA hardware sprite of ``w x h``."""
    size = f"{w}x{h}"
    sym = f"gObjectEventBaseOam_{size}"
    return "\n".join([
        _MARK_OPEN.format(kind="oam", size=size),
        f"// {size} single-hardware-sprite base OAM.  Generated by "
        f"PorySuite-Z — do not hand-edit.",
        f"const struct OamData {sym} = {{",
        f"    .shape = SPRITE_SHAPE({size}),",
        f"    .size = SPRITE_SIZE({size}),",
        "    .priority = 2,",
        "};",
        _MARK_CLOSE.format(kind="oam", size=size),
    ])


# ── ensure: create scaffolding if the project lacks it ──────────────────

def ensure_subsprite_table(root: str, d) -> GenResult:
    """Make sure ``gObjectEventSpriteOamTables_WxH`` exists.

    No-op when the project already defines the table — vanilla
    (16x16, 16x32, 32x32, 48x48, 64x32, 64x64, 88x32, 96x40, 128x64) or
    a previous generation.  Otherwise the table is generated from ``d``
    and appended to ``object_event_subsprites.h``.

    ``d`` is a :class:`Decomposition` from ``overworld_sprite_geometry``.
    """
    w, h = d.width, d.height
    sym = d.subsprite_symbol
    path = os.path.join(root, *_SUBSPRITES_REL)
    text = _read(path)

    # Already defined?  An array definition is `<sym>[]` — match that so
    # a bare mention in a comment never counts as "present".  For a
    # composite this is the ``Ps`` name, which a vanilla WxH table can
    # never satisfy — so a composite always gets its own grid table.
    if re.search(r"\b" + re.escape(sym) + r"\s*\[\]", text):
        return GenResult(False, sym, f"{sym} already present")

    block = _subsprite_block(d)
    _atomic_write(path, text.rstrip("\n") + "\n\n" + block + "\n")
    return GenResult(
        True, sym,
        f"Generated {sym} "
        f"({d.piece_count} x {d.piece_w}x{d.piece_h} pieces)",
    )


def ensure_oam_base(root: str, d) -> GenResult:
    """Make sure the decomposition's base OAM template exists.

    Composite sprites point ``.oam`` at the always-present 8x8 dummy, so
    for them this is a guaranteed no-op.  For a single-OAM frame whose
    hardware shape vanilla never defined a template for (8x16, 8x32,
    32x64), the ``struct OamData`` is generated into ``base_oam.h``.
    """
    sym = d.oam_symbol
    if not d.is_single_oam:
        return GenResult(False, sym, f"composite sprite uses {sym}")

    path = os.path.join(root, *_BASE_OAM_REL)
    text = _read(path)
    # An OamData definition is `<sym> =` — vanilla and generated alike.
    if re.search(r"\b" + re.escape(sym) + r"\s*=", text):
        return GenResult(False, sym, f"{sym} already present")

    block = _oam_block(d.width, d.height)
    _atomic_write(path, text.rstrip("\n") + "\n\n" + block + "\n")
    return GenResult(True, sym, f"Generated {sym}")


def ensure_overworld_geometry(root: str, d) -> List[GenResult]:
    """Ensure both the base OAM template and the subsprite table a
    decomposition needs.  Convenience entry point for the sprite
    creator — returns one :class:`GenResult` per file touched (or
    skipped), in OAM-then-subsprite order."""
    return [ensure_oam_base(root, d), ensure_subsprite_table(root, d)]


# ── remove: take back scaffolding this tool generated ───────────────────

def remove_subsprite_table(root: str, w: int, h: int) -> GenResult:
    """Remove a generated ``gObjectEventSpriteOamTables_WxH``.

    Only ever removes a PorySuite-fenced block.  A vanilla table (no
    fence) is left completely intact — the result reports ``changed`` as
    False so the caller knows nothing was taken.

    The caller is responsible for first confirming no sprite still uses
    this size; this function only answers "is the block mine to remove".
    """
    sym = f"gObjectEventSpriteOamTables_{w}x{h}"
    path = os.path.join(root, *_SUBSPRITES_REL)
    if not os.path.isfile(path):
        return GenResult(False, sym, f"{path} missing")
    text = _read(path)
    m = _block_regex("subsprite", w, h).search(text)
    if not m:
        return GenResult(
            False, sym,
            f"{sym} is not PorySuite-generated — left intact",
        )
    new = text[:m.start()] + "\n" + text[m.end():]
    new = re.sub(r"\n{3,}", "\n\n", new)
    _atomic_write(path, new)
    return GenResult(True, sym, f"Removed generated {sym}")


def remove_oam_base(root: str, w: int, h: int) -> GenResult:
    """Remove a generated ``gObjectEventBaseOam_WxH``.

    As with :func:`remove_subsprite_table`, only PorySuite-fenced blocks
    are touched; vanilla templates are never removed.
    """
    sym = f"gObjectEventBaseOam_{w}x{h}"
    path = os.path.join(root, *_BASE_OAM_REL)
    if not os.path.isfile(path):
        return GenResult(False, sym, f"{path} missing")
    text = _read(path)
    m = _block_regex("oam", w, h).search(text)
    if not m:
        return GenResult(
            False, sym,
            f"{sym} is not PorySuite-generated — left intact",
        )
    new = text[:m.start()] + "\n" + text[m.end():]
    new = re.sub(r"\n{3,}", "\n\n", new)
    _atomic_write(path, new)
    return GenResult(True, sym, f"Removed generated {sym}")


# ── introspection ───────────────────────────────────────────────────────

def _scan(path: str, kind: str) -> Set[Tuple[int, int]]:
    out: Set[Tuple[int, int]] = set()
    try:
        text = _read(path)
    except OSError:
        return out
    for m in re.finditer(
        r">>> PORYSUITE-GEN overworld-" + re.escape(kind)
        + r" (\d+)x(\d+) >>>",
        text,
    ):
        out.add((int(m.group(1)), int(m.group(2))))
    return out


def scan_generated_subsprite_tables(root: str) -> Set[Tuple[int, int]]:
    """``(w, h)`` of every subsprite table PorySuite-Z has generated."""
    return _scan(os.path.join(root, *_SUBSPRITES_REL), "subsprite")


def scan_generated_oam_bases(root: str) -> Set[Tuple[int, int]]:
    """``(w, h)`` of every base OAM template PorySuite-Z has generated."""
    return _scan(os.path.join(root, *_BASE_OAM_REL), "oam")
