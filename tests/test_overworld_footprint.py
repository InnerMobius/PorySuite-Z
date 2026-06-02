"""Tests for ``core/overworld_footprint.py`` — the per-sprite collision
footprint data model + serialiser + parser.

The footprint module is PURE (stdlib only).  It is loaded directly with
``importlib`` so the test never touches ``core/__init__.py``, which
otherwise pulls in the whole PyQt + data-layer stack.

Run directly:   python tests/test_overworld_footprint.py
Via pytest:     python -m pytest tests/test_overworld_footprint.py
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_THIS_DIR, ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _load_footprint():
    """Load ``core/overworld_footprint.py`` in isolation (no package init).

    ``core/__init__.py`` eagerly imports the data manager + PyQt; none
    of that is needed for the pure-data footprint module, and pulling
    it in would require a configured PorySuite project on disk just to
    run these tests.
    """
    path = os.path.join(_ROOT, "core", "overworld_footprint.py")
    spec = importlib.util.spec_from_file_location(
        "overworld_footprint", path,
    )
    module = importlib.util.module_from_spec(spec)
    # Register before exec: @dataclass resolves cls.__module__ via
    # sys.modules, so the module must be findable there as it runs.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


fp_mod = _load_footprint()
Footprint = fp_mod.Footprint
empty_footprint = fp_mod.empty_footprint
encode_footprint_block = fp_mod.encode_footprint_block
footprint_header_path = fp_mod.footprint_header_path
parse_footprints = fp_mod.parse_footprints
parse_project_footprints = fp_mod.parse_project_footprints
serialize_footprints = fp_mod.serialize_footprints
CELL_PX = fp_mod.CELL_PX


# ───────────────────────────────────────────── empty + shape coercion ──

def test_empty_footprint_sizes_to_pixel_frame():
    fp = empty_footprint("OBJ_EVENT_GFX_TEST", 16, 32)
    assert fp.width == 2
    assert fp.height == 4
    assert fp.is_empty
    assert fp.solid_count == 0
    assert len(fp.cells) == 4
    assert all(len(row) == 2 for row in fp.cells)


def test_empty_footprint_rejects_non_multiple_of_cell():
    with pytest.raises(ValueError):
        empty_footprint("OBJ_EVENT_GFX_TEST", 12, 16)
    with pytest.raises(ValueError):
        empty_footprint("OBJ_EVENT_GFX_TEST", 16, 0)
    with pytest.raises(ValueError):
        empty_footprint("OBJ_EVENT_GFX_TEST", -8, 16)


def test_ragged_input_is_coerced_to_rectangle():
    # If a caller hands in mismatched rows/columns, __post_init__
    # pads + truncates rather than blowing up later in codegen.
    fp = Footprint(
        gfx_const="OBJ_EVENT_GFX_TEST",
        width=3,
        height=2,
        cells=[[True], [False, True, True, True]],
    )
    assert len(fp.cells) == 2
    assert all(len(row) == 3 for row in fp.cells)
    assert fp.cells[0] == [True, False, False]
    assert fp.cells[1] == [False, True, True]


# ─────────────────────────────────────────────────────────── mutations ──

def test_set_and_is_solid_round_trip():
    fp = empty_footprint("OBJ_EVENT_GFX_TEST", 16, 16)
    fp.set_cell(0, 0, True)
    fp.set_cell(1, 1, True)
    assert fp.is_solid(0, 0)
    assert fp.is_solid(1, 1)
    assert not fp.is_solid(1, 0)
    assert fp.solid_count == 2
    assert not fp.is_empty


def test_set_cell_out_of_range_is_silently_ignored():
    fp = empty_footprint("OBJ_EVENT_GFX_TEST", 16, 16)
    fp.set_cell(99, 99, True)
    fp.set_cell(-1, -1, True)
    fp.set_cell(0, 99, True)
    assert fp.solid_count == 0
    assert not fp.is_solid(99, 99)


# ───────────────────────────────────────────────────────── codegen ──

def test_encode_footprint_block_round_trips():
    fp = empty_footprint("OBJ_EVENT_GFX_KING_ZORA", 48, 48)
    # Plus-shaped footprint at the centre.
    for c, r in [(2, 2), (3, 2), (2, 3), (3, 3)]:
        fp.set_cell(c, r, True)
    block = encode_footprint_block(fp)

    # Sentinels + symbol names look right.
    assert "PORYSUITE-GEN BEGIN footprint OBJ_EVENT_GFX_KING_ZORA" in block
    assert "PORYSUITE-GEN END footprint OBJ_EVENT_GFX_KING_ZORA" in block
    assert "sFootprintCells_KingZora" in block
    assert "sFootprint_KingZora" in block
    assert ".width = 6," in block
    assert ".height = 6," in block

    # Round-trip the block back through the parser.
    parsed = parse_footprints(block)
    assert len(parsed) == 1
    rt = parsed[0]
    assert rt.gfx_const == "OBJ_EVENT_GFX_KING_ZORA"
    assert rt.width == 6 and rt.height == 6
    assert rt.solid_count == 4
    assert rt.is_solid(2, 2) and rt.is_solid(3, 3)
    assert not rt.is_solid(0, 0)


def test_serialize_footprints_skips_empty():
    empty = empty_footprint("OBJ_EVENT_GFX_EMPTY", 16, 32)
    real = empty_footprint("OBJ_EVENT_GFX_REAL", 16, 32)
    real.set_cell(0, 0, True)
    out = serialize_footprints([empty, real])
    assert "OBJ_EVENT_GFX_EMPTY" not in out
    assert "OBJ_EVENT_GFX_REAL" in out
    # Lookup table contains the real one; nothing else.
    assert "[OBJ_EVENT_GFX_REAL] = &sFootprint_Real," in out
    assert "[OBJ_EVENT_GFX_EMPTY]" not in out


def test_serialize_then_parse_full_file_round_trip():
    fps = [
        empty_footprint("OBJ_EVENT_GFX_A", 16, 32),
        empty_footprint("OBJ_EVENT_GFX_B", 48, 48),
    ]
    fps[0].set_cell(0, 3, True)
    fps[1].set_cell(2, 2, True)
    fps[1].set_cell(3, 3, True)
    text = serialize_footprints(fps)
    parsed = parse_footprints(text)
    assert len(parsed) == 2
    assert parsed[0].gfx_const == "OBJ_EVENT_GFX_A"
    assert parsed[0].is_solid(0, 3)
    assert parsed[1].gfx_const == "OBJ_EVENT_GFX_B"
    assert parsed[1].solid_count == 2


def test_lookup_table_uses_sparse_designated_init():
    # The lookup table must use [OBJ_EVENT_GFX_*] = ... entries so the
    # rest of the array zero-inits to NULL (the engine's "no footprint"
    # signal).  A plain positional initializer would silently pin
    # footprints to the wrong gfx indices.
    fp = empty_footprint("OBJ_EVENT_GFX_TEST", 16, 16)
    fp.set_cell(0, 0, True)
    text = serialize_footprints([fp])
    assert "[OBJ_EVENT_GFX_TEST] = &sFootprint_Test," in text


def test_lookup_table_explicit_size_prevents_out_of_range_reads():
    # REGRESSION: a previous serializer omitted the explicit array size,
    # so C sized the array to max(designator) + 1.  Any OBJ_EVENT_GFX_*
    # with a numeric value HIGHER than the highest-footprint sprite then
    # indexed off the end -- garbage memory dereferenced as a footprint
    # struct, lag + phantom collision walls + softlocks.
    #
    # The fix forces the explicit size NUM_OBJ_EVENT_GFX so every
    # graphics ID has a defined slot (NULL unless explicitly assigned).
    # Real-world reproducer: OBJ_EVENT_GFX_SCHULE = 159 (had footprint),
    # OBJ_EVENT_GFX_SCARECROW = 160 (no footprint) -- without the
    # explicit size the engine indexed slot 160 in a 160-slot array.
    fp = empty_footprint("OBJ_EVENT_GFX_TEST", 16, 16)
    fp.set_cell(0, 0, True)
    text = serialize_footprints([fp])
    # MUST contain the explicit size; without it the bug is back.
    assert (
        "const struct ObjectFootprint *const "
        "gObjectEventFootprints[NUM_OBJ_EVENT_GFX] = {"
    ) in text, (
        "lookup table is missing [NUM_OBJ_EVENT_GFX] -- "
        "any graphics ID higher than the max footprint will OOB-read"
    )
    # And it MUST NOT regress to the unsized form.
    assert (
        "const struct ObjectFootprint *const gObjectEventFootprints[] = {"
    ) not in text


def test_empty_footprint_list_still_emits_sized_array():
    # A project with no footprints yet (or all footprints cleared) still
    # needs the lookup table sized so any future graphicsId lookup is NULL.
    text = serialize_footprints([])
    assert (
        "const struct ObjectFootprint *const "
        "gObjectEventFootprints[NUM_OBJ_EVENT_GFX] = {"
    ) in text


# ─────────────────────────────────────────────────────────── parser ──

def test_parser_skips_corrupt_blocks():
    text = """
// PORYSUITE-GEN BEGIN footprint OBJ_EVENT_GFX_GOOD
static const u8 sFootprintCells_Good[] = {
    /* row 0 */ 1, 0,
    /* row 1 */ 0, 1,
};
static const struct ObjectFootprint sFootprint_Good = {
    .width = 2,
    .height = 2,
    .cells = sFootprintCells_Good,
};
// PORYSUITE-GEN END footprint OBJ_EVENT_GFX_GOOD

// PORYSUITE-GEN BEGIN footprint OBJ_EVENT_GFX_BAD
// width/height/cells missing -- block is malformed
// PORYSUITE-GEN END footprint OBJ_EVENT_GFX_BAD
"""
    parsed = parse_footprints(text)
    # The good one survives; the corrupt one is silently dropped.
    assert len(parsed) == 1
    assert parsed[0].gfx_const == "OBJ_EVENT_GFX_GOOD"
    assert parsed[0].is_solid(0, 0)
    assert parsed[0].is_solid(1, 1)


def test_parser_rejects_cell_count_mismatch():
    # If the cells[] array doesn't match width*height, drop the block --
    # don't silently truncate or pad to a wrong shape.
    text = """
// PORYSUITE-GEN BEGIN footprint OBJ_EVENT_GFX_WRONG
static const u8 sFootprintCells_Wrong[] = {
    /* row 0 */ 1, 0,
};
static const struct ObjectFootprint sFootprint_Wrong = {
    .width = 2,
    .height = 2,
    .cells = sFootprintCells_Wrong,
};
// PORYSUITE-GEN END footprint OBJ_EVENT_GFX_WRONG
"""
    assert parse_footprints(text) == []


def test_parser_requires_end_fence_to_match_begin():
    # A mismatched END fence must not close a BEGIN with a different
    # name -- half-rewritten files can't be misread as complete.
    text = """
// PORYSUITE-GEN BEGIN footprint OBJ_EVENT_GFX_A
static const u8 sFootprintCells_A[] = { 1 };
static const struct ObjectFootprint sFootprint_A = {
    .width = 1, .height = 1, .cells = sFootprintCells_A,
};
// PORYSUITE-GEN END footprint OBJ_EVENT_GFX_B
"""
    assert parse_footprints(text) == []


# ─────────────────────────────────────────────────────── project I/O ──

def test_parse_project_footprints_missing_file_returns_empty(tmp_path):
    assert parse_project_footprints(str(tmp_path)) == []


def test_parse_project_footprints_round_trips_real_file(tmp_path):
    fp = empty_footprint("OBJ_EVENT_GFX_TEST", 32, 32)
    fp.set_cell(0, 0, True)
    fp.set_cell(3, 3, True)
    text = serialize_footprints([fp])
    header = footprint_header_path(str(tmp_path))
    os.makedirs(os.path.dirname(header), exist_ok=True)
    with open(header, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)

    parsed = parse_project_footprints(str(tmp_path))
    assert len(parsed) == 1
    assert parsed[0].gfx_const == "OBJ_EVENT_GFX_TEST"
    assert parsed[0].is_solid(0, 0)
    assert parsed[0].is_solid(3, 3)
    assert parsed[0].solid_count == 2


def test_cell_px_is_eight():
    # Engineering invariant: changing this would invalidate every
    # stored footprint and the engine collision hook.  Lock it.
    assert CELL_PX == 8
