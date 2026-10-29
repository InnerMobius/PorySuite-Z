"""A label can only be defined once, and text.inc owns the map's dialogue.

"Copy as Script" renders an event AND its text, on purpose, so the copy is
self-contained enough to paste into a file or a wiki. Pasting it back into the
editor is the one place that is wrong: the map already defines those labels in
text.inc, so defining them again stops the build dead.

    data/maps/Faron/text.inc:8: Error: symbol `Faron_Text_3' is already defined

Worse, `write_scripts_inc` preserves anything it was not asked to rewrite, so
once a duplicate lands it survives every later save — a plain reload-and-save
does not clear it, which was measured on the real map.

Two halves here: paste stops creating them, and saving removes any already on
disk.
"""
from __future__ import annotations

import os

import pytest

from eventide.backend.eventide_utils import strip_duplicate_text_blocks

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


SCRIPTS = """Woods_EventScript_Intro::
lockall
msgbox Woods_Text_1, MSGBOX_DEFAULT
releaseall
end


Woods_Text_1::
    .string "Hello!$"


Woods_Text_2::
    .string "Line one\\n"
    .string "Line two$"


Woods_Movement_1::
walk_left
step_end
"""


@pytest.fixture
def scripts_inc(tmp_path):
    p = tmp_path / 'scripts.inc'
    p.write_text(SCRIPTS, encoding='utf-8')
    return p


def _labels(p):
    import re
    return re.findall(r'^([A-Za-z0-9_]+)::', p.read_text(encoding='utf-8'),
                      re.M)


# ── removing what text.inc already owns ───────────────────────────────────

def test_a_duplicate_text_block_is_removed(scripts_inc):
    removed = strip_duplicate_text_blocks(scripts_inc, {'Woods_Text_1'})
    assert removed == ['Woods_Text_1']
    assert 'Woods_Text_1' not in _labels(scripts_inc)


def test_a_multi_line_text_block_is_removed_whole(scripts_inc):
    strip_duplicate_text_blocks(scripts_inc, {'Woods_Text_2'})
    body = scripts_inc.read_text(encoding='utf-8')
    assert 'Line one' not in body and 'Line two' not in body


def test_several_at_once(scripts_inc):
    removed = strip_duplicate_text_blocks(
        scripts_inc, {'Woods_Text_1', 'Woods_Text_2'})
    assert sorted(removed) == ['Woods_Text_1', 'Woods_Text_2']


# ── leaving everything else exactly alone ─────────────────────────────────

def test_the_script_itself_survives(scripts_inc):
    strip_duplicate_text_blocks(scripts_inc, {'Woods_Text_1', 'Woods_Text_2'})
    assert 'Woods_EventScript_Intro' in _labels(scripts_inc)
    assert 'msgbox Woods_Text_1' in scripts_inc.read_text(encoding='utf-8')


def test_the_reference_to_the_text_is_kept(scripts_inc):
    """Only the DEFINITION goes; the msgbox still points at text.inc's copy."""
    strip_duplicate_text_blocks(scripts_inc, {'Woods_Text_1'})
    assert 'msgbox Woods_Text_1' in scripts_inc.read_text(encoding='utf-8')


def test_movements_are_untouched(scripts_inc):
    strip_duplicate_text_blocks(scripts_inc, {'Woods_Text_1', 'Woods_Text_2'})
    assert 'Woods_Movement_1' in _labels(scripts_inc)
    assert 'walk_left' in scripts_inc.read_text(encoding='utf-8')


def test_a_text_label_text_inc_does_not_own_is_left(scripts_inc):
    """Only labels text.inc defines are duplicates. Others belong here."""
    removed = strip_duplicate_text_blocks(scripts_inc, {'Woods_Text_1'})
    assert 'Woods_Text_2' not in removed
    assert 'Woods_Text_2' in _labels(scripts_inc)


def test_a_label_holding_commands_is_never_removed(tmp_path):
    """A script that happens to share a name is not a text block."""
    p = tmp_path / 'scripts.inc'
    p.write_text('Woods_Text_1::\nlockall\nreleaseall\nend\n',
                 encoding='utf-8')
    assert strip_duplicate_text_blocks(p, {'Woods_Text_1'}) == []
    assert 'Woods_Text_1' in _labels(p)


# ── never do anything on a guess ──────────────────────────────────────────

def test_nothing_owned_means_nothing_touched(scripts_inc):
    before = scripts_inc.read_text(encoding='utf-8')
    assert strip_duplicate_text_blocks(scripts_inc, set()) == []
    assert scripts_inc.read_text(encoding='utf-8') == before


def test_a_file_with_no_duplicates_is_not_rewritten(scripts_inc):
    before = scripts_inc.read_text(encoding='utf-8')
    assert strip_duplicate_text_blocks(scripts_inc, {'Elsewhere_Text_9'}) == []
    assert scripts_inc.read_text(encoding='utf-8') == before


def test_a_missing_file_is_harmless(tmp_path):
    assert strip_duplicate_text_blocks(tmp_path / 'nope.inc', {'A'}) == []


# ── paste no longer brings definitions in ─────────────────────────────────

@pytest.fixture
def tab(tmp_path, monkeypatch):
    pytest.importorskip('PyQt6.QtWidgets')
    from PyQt6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    import eventide.ui.event_editor_tab as eet

    class _StubMW:
        def log_message(self, *a, **k):
            pass

    t = eet.EventEditorTab.__new__(eet.EventEditorTab)
    t._mw = _StubMW()
    return t


def test_pasted_text_lines_are_dropped(tab):
    out = tab._drop_pasted_text_definitions([
        ('lockall',),
        ('message', 'Woods_Text_1', '', 'MSGBOX_DEFAULT'),
        ('.string', '"Hello!$"'),
        ('releaseall',),
    ])
    assert ('.string', '"Hello!$"') not in out
    assert len(out) == 3


def test_the_message_command_still_points_at_the_label(tab):
    out = tab._drop_pasted_text_definitions([
        ('message', 'Woods_Text_1', '', 'MSGBOX_DEFAULT'),
        ('.string', '"Hello!$"'),
    ])
    assert out == [('message', 'Woods_Text_1', '', 'MSGBOX_DEFAULT')]


def test_braille_definitions_are_dropped_too(tab):
    out = tab._drop_pasted_text_definitions([('.braille', '"dots$"')])
    assert out == []


def test_an_ordinary_paste_is_unchanged(tab):
    cmds = [('lockall',), ('applymovement', '8', 'Woods_Movement_1'),
            ('releaseall',)]
    assert tab._drop_pasted_text_definitions(list(cmds)) == cmds
