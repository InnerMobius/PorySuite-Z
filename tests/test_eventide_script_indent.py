"""Finding 0g: saving a script stripped the tab from every command line.

Every command literal in `lines_from_commands` was emitted flush-left, so a
NO-OP parse-and-write-back flattened the whole file. Measured on real maps:
PewterCity 656 tab-indented command lines to 0, PalletTown 373 to 0,
ViridianCity 155 to 0.

Not cosmetic. It inflated a real 98-line edit into a 239-line diff (2.4x),
which defeats `git diff` as the way to tell tool churn from the user's own
edits — precisely when that matters, during recovery from a bad map
operation. It had also already re-flattened two maps the user repaired by hand.

The project's own convention, counted across 12 real `scripts.inc` files:
323 tab-indented command lines against 1 flush-left.

DO NOT REGRESS:
  * LABELS stay flush-left. `Kakariko_MapScripts::` was untouched in the
    original diff while every command under it moved — that asymmetry IS the
    convention, and indenting labels would be a new defect, not a fix.
  * Assembler directives (`.macro`, `.endm`) and comments (`@`) stay put.
  * A line that is already indented must not be indented twice.
  * One tab per emitted element is sufficient: measured across 453 of 453
    maps and 17,642 elements, ZERO had an embedded newline. If that ever
    changes, this needs to indent each physical line instead.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eventide.backend.eventide_utils import (  # noqa: E402
    _indent_script_lines, lines_from_commands,
)


def test_commands_are_indented():
    out = _indent_script_lines(["lock\n", "release\n", "end\n"])
    assert out == ["\tlock\n", "\trelease\n", "\tend\n"]


def test_labels_stay_flush_left():
    out = _indent_script_lines(["Kakariko_MapScripts::\n", "lock\n"])
    assert out[0] == "Kakariko_MapScripts::\n"
    assert out[1] == "\tlock\n"


def test_already_indented_lines_are_not_indented_twice():
    out = _indent_script_lines(["\tlock\n", "    release\n"])
    assert out == ["\tlock\n", "    release\n"]


def test_blank_lines_are_untouched():
    assert _indent_script_lines(["\n", "   \n"]) == ["\n", "   \n"]


def test_directives_and_comments_are_untouched():
    out = _indent_script_lines([".macro foo\n", "@ a comment\n", ".endm\n"])
    assert out == [".macro foo\n", "@ a comment\n", ".endm\n"]


def test_the_emitter_itself_indents_its_commands():
    """The real writer, not just the helper."""
    lines = lines_from_commands(
        [("lock", ("lock", "")), ("release", ("release", ""))], texts={})
    commands = [ln for ln in lines if ln.strip()]
    assert commands, "the emitter produced nothing to check"
    for ln in commands:
        if ln.strip().endswith("::"):
            assert not ln.startswith("\t"), "a label was indented: %r" % ln
        else:
            assert ln.startswith("\t"), "a command was left flush-left: %r" % ln


def test_the_emitter_indents_the_nop_filler():
    lines = lines_from_commands([], texts={})
    assert lines == ["\tnop\n"]


def test_every_emitted_element_is_one_line():
    """A per-element tab is only sufficient while elements are single lines."""
    lines = lines_from_commands(
        [("lock", ("lock", "")), ("end", ("end", ""))], texts={})
    for ln in lines:
        assert ln.count("\n") <= 1, "multi-line element: %r" % ln


# ─────────────────────────────────────────────────────────────────────────────
# Table terminators (added when EVENTide learned to CREATE a map script table)
#
# The blanket "directives stay put" rule above is right for `.equ` / `.set` /
# `.macro`, and was wrong for the DATA terminators that close a table. Counted
# across the project's 453 scripts.inc files:
#
#   .byte   449 indented : 4 flush        .equ  0 indented : 34 flush
#   .2byte  224 indented : 0 flush        .set  0 indented :  5 flush
#
# It was invisible until the editor could write a table of its own, because
# `.byte 0` closes the outer table and `.2byte 0` the inner one — so every
# table EVENTide wrote had its terminator shoved to column 0, which is the
# diff noise this whole module exists to stop.
# ─────────────────────────────────────────────────────────────────────────────

def test_a_table_terminator_is_indented():
    assert _indent_script_lines([".byte 0\n"]) == ["\t.byte 0\n"]


def test_an_inner_table_terminator_is_indented():
    assert _indent_script_lines([".2byte 0\n"]) == ["\t.2byte 0\n"]


def test_a_whole_map_script_table_comes_out_consistently():
    out = _indent_script_lines([
        "Woods_MapScripts::\n",
        "map_script MAP_SCRIPT_ON_TRANSITION, Woods_OnTransition\n",
        ".byte 0\n",
    ])
    assert out == [
        "Woods_MapScripts::\n",
        "\tmap_script MAP_SCRIPT_ON_TRANSITION, Woods_OnTransition\n",
        "\t.byte 0\n",
    ]


def test_equ_stays_flush_left():
    """0 indented : 34 flush in the real tree — it belongs at the top of the
    file, like a label, not inside a block."""
    assert _indent_script_lines([".equ FOO, VAR_TEMP_2\n"]) \
        == [".equ FOO, VAR_TEMP_2\n"]


def test_set_stays_flush_left():
    assert _indent_script_lines([".set FOO, 1\n"]) == [".set FOO, 1\n"]


def test_an_unmeasured_directive_is_left_exactly_where_it_was():
    """Being wrong by leaving a line alone costs nothing; being wrong by
    moving one costs a line of somebody's diff."""
    assert _indent_script_lines([".incbin \"x.bin\"\n"]) \
        == [".incbin \"x.bin\"\n"]


def test_an_already_indented_terminator_is_not_doubled():
    assert _indent_script_lines(["\t.byte 0\n"]) == ["\t.byte 0\n"]
