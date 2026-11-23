"""Regression guard: the overflowing-dialogue repair must be accurate and surgical.

WHY THIS EXISTS. Fixing EVENTide's writer (2026-11-22, see
`tests/test_eventide_multipage_dialogue.py`) stopped NEW dialogue being written
in a shape the engine cannot draw, but it did nothing for the bytes already on
disk. 13 texts in this project's maps were already broken, and the owner is not
going to open every map to find out which. `eventide.backend.dialogue_repair`
is the tool doing that job: it works out which texts the engine cannot draw and
rewrites only those.

The two risks this file guards are opposite and both bad:

* **Missing a broken text** -- the owner clicks Repair, is told everything is
  fine, and the dialogue still does not show.
* **Touching anything it was not asked to** -- these are the owner's own map
  files. A repair that reformats a whole file, reorders labels, drops comments
  or alters a single WORD of dialogue is worse than the bug it fixes.

So every test here checks one of: the detector agrees with the engine's line-slot
model; the repair changes break codes and nothing else; everything the repair was
not asked about comes back byte-identical.

This also guards the writer. `repair` renders through `text_body_lines`, and
every repair is verified by re-reading the file, so a writer that went back to
the old "every break is a newline" behaviour makes the repair report FAILURE
instead of silently writing the same broken bytes. Measured, not assumed:
reverting `text_body_lines` to the flat encoder fails
`test_repair_fixes_it_and_a_rescan_is_clean`,
`test_repair_changes_break_codes_and_not_one_word` and
`test_repair_does_not_touch_an_unaffected_file` (plus three in
`test_eventide_multipage_dialogue.py`). `scan` keeps working in that case --
it asks `encode_gba_linebreaks` directly -- which is why the writer needs its
own guards here and not just a detector test.
"""
import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from eventide.backend.dialogue_repair import (  # noqa: E402
    overflows, scan, repair,
)

BS = chr(92)
NL = BS + 'n'
SC = BS + 'l'
PG = BS + 'p'


def write_map(root, map_name, body):
    d = root / 'data' / 'maps' / map_name
    d.mkdir(parents=True, exist_ok=True)
    p = d / 'text.inc'
    p.write_text(body, encoding='utf-8', newline='')
    return p


# -- the detector ------------------------------------------------------------

def test_overflows_agrees_with_the_engine_slot_model():
    # two lines fit
    assert not overflows('one' + NL + 'two$')
    # a third reached by a newline does not
    assert overflows('one' + NL + 'two' + NL + 'three$')
    # ...but reached by a scroll it does
    assert not overflows('one' + NL + 'two' + SC + 'three$')
    # a new page resets the slot
    assert not overflows('one' + NL + 'two' + PG + 'three' + NL + 'four$')
    # two newlines in a row bury the line after them just as deep
    assert overflows('one' + NL + NL + 'two$')
    # a scroll does not advance the slot, so a later newline still counts
    assert overflows('a' + NL + 'b' + SC + 'c' + NL + 'd$')
    # empty text and a single line are never overflow
    assert not overflows('')
    assert not overflows('just one line$')


# -- scan --------------------------------------------------------------------

def test_scan_finds_the_third_line_shape(tmp_path):
    """The owner's `kakariko_house6` bug, planted in a throwaway map."""
    write_map(tmp_path, 'SomeHouse',
              'SomeHouse_Text_1::\n'
              f'    .string "line one{NL}"\n'
              f'    .string "line two{NL}"\n'
              '    .string "line three$"\n')
    found = scan(tmp_path)
    assert [f.label for f in found] == ['SomeHouse_Text_1']
    assert SC in found[0].repaired
    assert not overflows(found[0].repaired)


def test_scan_finds_the_double_newline_shape(tmp_path):
    write_map(tmp_path, 'SomeGym',
              'SomeGym_Text_Intro::\n'
              f'    .string "Only ladies here.{NL}{NL}So there!$"\n')
    found = scan(tmp_path)
    assert [f.label for f in found] == ['SomeGym_Text_Intro']
    assert found[0].repaired == 'Only ladies here.' + PG + 'So there!$'


def test_scan_ignores_text_the_engine_can_already_draw(tmp_path):
    write_map(tmp_path, 'FineHouse',
              'FineHouse_Text_1::\n'
              f'    .string "line one{NL}"\n'
              f'    .string "line two{SC}"\n'
              f'    .string "line three{PG}"\n'
              '    .string "a new page$"\n')
    assert scan(tmp_path) == []


def test_scan_leaves_braille_alone(tmp_path):
    write_map(tmp_path, 'BrailleRoom',
              'BrailleRoom_Text_1::\n'
              f'    .braille "ABC{NL}DEF{NL}GHI$"\n')
    assert scan(tmp_path) == []


def test_scan_only_looks_at_map_dialogue(tmp_path):
    """`data/text/*.inc` draws in windows of other heights (the controls guide
    is 6 tiles, the new-game intro textbox 15), where three lines are legal."""
    d = tmp_path / 'data' / 'text'
    d.mkdir(parents=True)
    (d / 'new_game_intro.inc').write_text(
        'gControlsGuide_Text_DPad::\n'
        f'    .string "one{NL}two{NL}three$"\n', encoding='utf-8', newline='')
    assert scan(tmp_path) == []


def test_scan_on_a_project_with_no_maps_is_quiet(tmp_path):
    assert scan(tmp_path) == []


# -- repair ------------------------------------------------------------------

def test_repair_fixes_it_and_a_rescan_is_clean(tmp_path):
    write_map(tmp_path, 'SomeHouse',
              'SomeHouse_Text_1::\n'
              f'    .string "line one{NL}"\n'
              f'    .string "line two{NL}"\n'
              '    .string "line three$"\n')
    done, failed = repair(scan(tmp_path))
    assert done == ['SomeHouse_Text_1']
    assert failed == []
    assert scan(tmp_path) == []


def test_repair_changes_break_codes_and_not_one_word(tmp_path):
    words = ['Long ago, Hylians lived in the', 'clouds were companions',
             'with these large birds.']
    p = write_map(tmp_path, 'SomeHouse',
                  'SomeHouse_Text_1::\n'
                  f'    .string "{words[0]}{NL}"\n'
                  f'    .string "{words[1]}{NL}"\n'
                  f'    .string "{words[2]}$"\n')
    repair(scan(tmp_path))
    after = p.read_text(encoding='utf-8')
    for w in words:
        assert w in after, f'lost wording: {w}'
    assert SC in after


def test_repair_leaves_every_other_label_and_comment_byte_identical(tmp_path):
    body = (
        '@ A comment the owner wrote by hand.\n'
        '\n'
        'SomeHouse_Text_Fine::\n'
        f'    .string "two lines{NL}is fine$"\n'
        '\n'
        '@ Another comment.\n'
        'SomeHouse_Text_Broken::\n'
        f'    .string "one{NL}two{NL}three$"\n'
        '\n'
        'SomeHouse_Text_AlsoFine::\n'
        '    .string "short$"\n'
    )
    p = write_map(tmp_path, 'SomeHouse', body)
    repair(scan(tmp_path))
    after = p.read_text(encoding='utf-8')
    # everything that was not the broken label survives, in order
    for frag in ('@ A comment the owner wrote by hand.',
                 '@ Another comment.',
                 f'.string "two lines{NL}is fine$"',
                 '.string "short$"'):
        assert frag in after, f'lost: {frag!r}'
    assert after.index('SomeHouse_Text_Fine') < after.index('SomeHouse_Text_Broken')
    assert after.index('SomeHouse_Text_Broken') < after.index('SomeHouse_Text_AlsoFine')


def test_repair_does_not_touch_an_unaffected_file(tmp_path):
    good = write_map(tmp_path, 'FineHouse',
                     'FineHouse_Text_1::\n'
                     f'    .string "two lines{NL}only$"\n')
    write_map(tmp_path, 'BadHouse',
              'BadHouse_Text_1::\n'
              f'    .string "one{NL}two{NL}three$"\n')
    before = good.read_bytes()
    done, failed = repair(scan(tmp_path))
    assert done == ['BadHouse_Text_1'] and failed == []
    assert good.read_bytes() == before


def test_repair_preserves_crlf_line_endings(tmp_path):
    p = write_map(tmp_path, 'SomeHouse',
                  'SomeHouse_Text_1::\r\n'
                  f'    .string "one{NL}"\r\n'
                  f'    .string "two{NL}"\r\n'
                  '    .string "three$"\r\n')
    repair(scan(tmp_path))
    raw = p.read_bytes()
    assert raw.count(b'\r\n') == raw.count(b'\n'), raw


def test_repair_of_nothing_is_a_no_op(tmp_path):
    assert repair([]) == ([], [])
