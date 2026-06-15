"""Unit tests for the append-species codegen insertion helpers.

These drive the three insertion primitives on small synthetic C-like strings —
no engine files needed — and assert placement + idempotency. The full pipeline
is build-verified separately (a Deoxys Defense form compiles + links via WSL).
"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "core"))  # import as top-level (skip core/__init__)

import pytest  # noqa: E402

import append_species as A  # noqa: E402


def test_insert_after_line():
    t = "before\n    ANCHOR thing\nafter\n"
    out = A._insert_after_line(t, r"ANCHOR ", "    INSERTED\n")
    assert out == "before\n    ANCHOR thing\n    INSERTED\nafter\n"


def test_insert_after_line_missing_raises():
    with pytest.raises(RuntimeError):
        A._insert_after_line("nothing here\n", r"NOPE", "x\n")


def test_insert_first_entry_is_first_and_idempotent():
    t = "const u16 *const tbl[] =\n{\n    [A] = aaa,\n    [B] = bbb,\n};\n"
    out = A._insert_first_entry(t, "tbl", "[Z] = zzz,")
    assert "[Z] = zzz," in out
    # inserted as the FIRST element (designated indices are order-independent)
    assert out.index("[Z] = zzz,") < out.index("[A] = aaa,")
    # idempotent — re-inserting changes nothing
    assert A._insert_first_entry(out, "tbl", "[Z] = zzz,") == out


def test_insert_first_entry_handles_brace_on_next_line():
    t = "Type arr[SIZE]\n=\n{\n    [A] = 1,\n};\n"
    out = A._insert_first_entry(t, "arr", "[Z] = 9,")
    assert out.index("[Z] = 9,") < out.index("[A] = 1,")


def test_insert_after_block():
    t = ("    [SPECIES_X] =\n    {\n        .a = 1,\n    },\n"
         "    [SPECIES_Y] =\n    {\n        .a = 2,\n    },\n")
    out = A._insert_after_block(t, r"\[SPECIES_X\] =\n", "    [SPECIES_NEW] = {},\n")
    assert "[SPECIES_NEW]" in out
    # placed after the X block's close, before the Y block
    assert out.index("[SPECIES_NEW]") > out.index(".a = 1,")
    assert out.index("[SPECIES_NEW]") < out.index("[SPECIES_Y]")


def test_upsert_first_entry_replaces_stale_value():
    t = "tbl[] = {\n    [K] = OLD,\n    [M] = keep,\n};\n"
    out = A._upsert_first_entry(t, "tbl", "[K] = NEW,")
    assert "[K] = NEW," in out and "OLD" not in out
    assert out.count("[K]") == 1            # replaced, not duplicated
    assert "[M] = keep," in out


def test_remove_indexed_entry_single_and_multiline():
    # a multi-line tutor-style value must be removed whole, neighbours kept
    t = ("arr = {\n    [A] = 1,\n    [B] = TUTOR(X)\n         | TUTOR(Y),\n"
         "    [C] = 3,\n};\n")
    out = A._remove_indexed_entry(t, "B")
    assert "[B]" not in out and "TUTOR(Y)" not in out
    assert "[A] = 1," in out and "[C] = 3," in out


def test_remove_indexed_entry_all_matches():
    t = "x = {\n    [Z] = a,\n    [Z] = b,\n    [Y] = c,\n};\n"
    out = A._remove_indexed_entry(t, "Z", count=0)
    assert "[Z]" not in out and "[Y] = c," in out


def test_remove_block_not_fooled_by_inner_brace():
    # the inline .types = { .. } brace must NOT end the block early
    t = ("    [SPECIES_X] =\n    {\n        .baseHP = 1,\n"
         "        .types = { T1, T2 },\n    },\n"
         "    [SPECIES_Y] =\n    {\n        .baseHP = 2,\n    },\n")
    out = A._remove_block(t, "SPECIES_X")
    assert "[SPECIES_X]" not in out and ".baseHP = 1," not in out
    assert "[SPECIES_Y]" in out and ".baseHP = 2," in out


def test_remove_line():
    t = "    SPECIES_SPRITE(A, x),\n    SPECIES_SPRITE(B, y),\n"
    out = A._remove_line(t, r"SPECIES_SPRITE\(A,")
    assert "SPECIES_SPRITE(A" not in out and "SPECIES_SPRITE(B, y)," in out


def test_fc_table_name():
    assert A._fc_table_name("SPECIES_DEOXYS") == "sDeoxysFormChangeTable"
    assert A._fc_table_name("SPECIES_DEOXYS_DEFENSE") == "sDeoxysDefenseFormChangeTable"


def test_read_form_changes(tmp_path):
    d = tmp_path / "src" / "data" / "pokemon"
    d.mkdir(parents=True)
    (d / "form_change_tables.h").write_text(
        '#include "constants/form_change_types.h"\n\n'
        "static const struct FormChange sDeoxysFormChangeTable[] = {\n"
        "    {FORM_CHANGE_ITEM_HOLD, SPECIES_DEOXYS_DEFENSE, ITEM_POTION},\n"
        "    {FORM_CHANGE_WEATHER, SPECIES_DEOXYS_ATTACK, WEATHER_DROUGHT},\n"
        "    {FORM_CHANGE_END},\n};\n", encoding="utf-8")
    out = A.read_form_changes(str(tmp_path), "SPECIES_DEOXYS")
    assert out == [
        {"method": "FORM_CHANGE_ITEM_HOLD", "target": "SPECIES_DEOXYS_DEFENSE",
         "param": "ITEM_POTION"},
        {"method": "FORM_CHANGE_WEATHER", "target": "SPECIES_DEOXYS_ATTACK",
         "param": "WEATHER_DROUGHT"},
    ]
    # a species with no table → empty
    assert A.read_form_changes(str(tmp_path), "SPECIES_BULBASAUR") == []
