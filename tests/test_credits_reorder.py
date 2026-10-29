"""Dragging a credits entry moves it, in the list and in the game.

Three separate faults met in the Credits editor's drag-to-reorder, all of them
from matching entries to the credits script BY POSITION instead of by name.

1. The reorder was handed to Qt and the editor listened for the list model's
   "rows moved" signal to copy the new order back. A dropped row never emits
   that signal -- only ``dataChanged`` -- so the handler never ran, and the
   list could end up showing an order the data did not have. The shared
   ``ReorderableListWidget`` now refuses the drop and the editor makes the
   move itself; what Qt does to rows it is allowed to move is written up in
   ``ui/custom_widgets/reorderable_list.py``.

2. Even with the order copied across, the drag changed nothing in the game.
   What plays, and in what order, is the SCRIPT; the text table is addressed
   by name and saving rewrites the names to match the table, so reordering the
   table alone cancelled itself out. Each entry's script command now travels
   with it, while the map changes, cameos and blank pauses stay put.

3. Durations were found by counting PRINT commands against the entry's row
   number. The script contains blank DUMMY pauses that are not entries, so
   from the first pause onward every entry showed another entry's duration and
   edits landed on the wrong line of the script. Removing an entry deleted the
   wrong line for the same reason.

The shared list widget's own behaviour is covered more fully in
``test_eventide_command_reorder.py``; the two tests for it at the bottom here
are the ones that matter to this editor.
"""
from __future__ import annotations

import os
import re

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

pytest.importorskip('PyQt6.QtWidgets')

# Qt has to be imported while this module is being imported, not later from
# inside a fixture: the suite swaps each test file's modules in and out around
# its import, and a half-swapped PyQt6 takes the interpreter down with it.
from PyQt6.QtCore import QPointF  # noqa: E402
from PyQt6.QtWidgets import QApplication, QListWidgetItem  # noqa: E402

import credits_editor as ce  # noqa: E402
from ui.custom_widgets.reorderable_list import ReorderableListWidget  # noqa: E402


STRINGS_C = '''#include "global.h"

ALIGNED(4) const u8 gCreditsString_Director[] = _("\\n\\nDirector\\n\\n\\n");
ALIGNED(4) const u8 gCreditsString_Ada[] = _("\\n\\n\\nAda\\n\\n");
ALIGNED(4) const u8 gCreditsString_Programmers[] = _("\\n\\nProgrammers\\n\\n\\n");
ALIGNED(4) const u8 gCreditsString_Grace[] = _("\\n\\n\\nGrace\\n\\n");
ALIGNED(4) const u8 gCreditsString_Artists[] = _("\\n\\nArtists\\n\\n\\n");
ALIGNED(4) const u8 gCreditsString_Hedy[] = _("\\n\\n\\nHedy\\n\\n");
ALIGNED(4) const u8 gCreditsString_Testers[] = _("\\n\\nTesters\\n\\n\\n");
ALIGNED(4) const u8 gCreditsString_Alan[] = _("\\n\\n\\nAlan\\n\\n");
'''

# The blank DUMMY print and the two non-print commands are the point of this
# fixture: they are what the old counting logic tripped over, and what must
# stay exactly where it is when entries move around them.
CREDITS_C = '''#include "global.h"

enum CreditsString
{
    CREDITS_STRING_DIRECTOR = 0,
    CREDITS_STRING_PROGRAMMERS,
    CREDITS_STRING_ARTISTS,
    CREDITS_STRING_TESTERS,
    CREDITS_STRING_DUMMY
};

static const struct CreditsTextHeader sCreditsTexts[] = {
    { gCreditsString_Director, gCreditsString_Ada, FALSE },
    { gCreditsString_Programmers, gCreditsString_Grace, FALSE },
    { gCreditsString_Artists, gCreditsString_Hedy, FALSE },
    { gCreditsString_Testers, gCreditsString_Alan, FALSE },
    { gString_Dummy, gString_Dummy, FALSE }
};

static const struct CreditsScrcmd sCreditsScript[] = {
    CREDITS_MAPNEXT(ROUTE23, 16),
    CREDITS_PRINT(DIRECTOR, 300),
    CREDITS_PRINT(PROGRAMMERS, 301),
    CREDITS_PRINT(DUMMY, 60),
    CREDITS_MON(TEKTITE),
    CREDITS_PRINT(ARTISTS, 302),
    CREDITS_PRINT(TESTERS, 303),
    CREDITS_THEENDGFX(THE_END, 120),
    CREDITS_WAITBUTTON(60)
};
'''


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def project(tmp_path):
    src = tmp_path / 'src'
    src.mkdir()
    (src / 'strings.c').write_text(STRINGS_C, encoding='utf-8')
    (src / 'credits.c').write_text(CREDITS_C, encoding='utf-8')
    return tmp_path


@pytest.fixture
def ed(qapp, project):
    w = ce.CreditsEditorWidget()
    w.load_project(str(project))
    return w


def _titles(w):
    """The entry order, by name, as the editor holds it."""
    return [ce._enum_name(e.title_symbol) for e in w._entries]


def _rows(w):
    """What the LIST is showing, read back independently of the data."""
    return [w._entry_list.item(i).text()
            for i in range(w._entry_list.count())]


def _script(w):
    """The script as (command, parameter, duration) triples."""
    return [(c.cmd_type, c.param, c.duration) for c in w._script]


def _saved_script(project):
    """The script order as it was actually written to credits.c."""
    text = (project / 'src' / 'credits.c').read_text(encoding='utf-8')
    body = re.search(r'sCreditsScript\[\]\s*=\s*\{(.*?)\};', text,
                     re.DOTALL).group(1)
    return [line.strip().rstrip(',')
            for line in body.splitlines() if line.strip()]


def _saved(project, array):
    text = (project / 'src' / 'credits.c').read_text(encoding='utf-8')
    body = re.search(array + r'.*?\{(.*?)\};', text, re.DOTALL).group(1)
    return [line.strip().rstrip(',')
            for line in body.splitlines() if line.strip()]


# ── The fixture itself, so a later failure is not a fixture problem ─────────

def test_the_project_loads(ed):
    assert _titles(ed) == ['DIRECTOR', 'PROGRAMMERS', 'ARTISTS', 'TESTERS']
    assert len(_rows(ed)) == 4
    assert ed.has_unsaved_changes() is False


def test_the_list_is_the_widget_that_refuses_qts_drop(ed):
    assert isinstance(ed._entry_list, ReorderableListWidget)


# ── Durations belong to the entry, not to a position ───────────────────────

def test_each_entry_reports_its_own_duration(ed):
    # ARTISTS and TESTERS sit after the blank DUMMY pause, which is exactly
    # where counting PRINT commands used to start reading the wrong line:
    # ARTISTS fell through to the default and TESTERS showed ARTISTS' 302.
    assert [ed._duration_for_entry(e) for e in ed._entries] == [
        300, 301, 302, 303]


def test_an_entry_the_script_never_prints_falls_back_to_the_default(ed):
    ed._entries[2].title_symbol = 'gCreditsString_Unprinted'
    assert ed._duration_for_entry(ed._entries[2]) == ce._DEFAULT_DURATION


def test_editing_a_duration_writes_to_that_entrys_own_command(ed):
    ed._entry_list.setCurrentRow(2)             # ARTISTS
    ed._duration_spin.setValue(999)
    assert _script(ed) == [
        ('MAPNEXT', 'ROUTE23', 16),
        ('PRINT', 'DIRECTOR', 300),
        ('PRINT', 'PROGRAMMERS', 301),
        ('PRINT', 'DUMMY', 60),
        ('MON', 'TEKTITE', 0),
        ('PRINT', 'ARTISTS', 999),
        ('PRINT', 'TESTERS', 303),
        ('THEENDGFX', 'THE_END', 120),
        ('WAITBUTTON', '', 60),
    ]


def test_selecting_an_entry_shows_its_own_duration(ed):
    ed._entry_list.setCurrentRow(3)             # TESTERS
    assert ed._duration_spin.value() == 303


# ── The drag ───────────────────────────────────────────────────────────────

def test_dragging_moves_the_entry(ed):
    ed._entry_list.rows_move_requested.emit([3], 0)
    assert _titles(ed) == ['TESTERS', 'DIRECTOR', 'PROGRAMMERS', 'ARTISTS']


def test_dragging_duplicates_nothing_and_loses_nothing(ed):
    before = sorted(_titles(ed))
    ed._entry_list.rows_move_requested.emit([3], 0)
    assert sorted(_titles(ed)) == before
    assert len(ed._entries) == 4


def test_the_list_still_matches_the_data_after_a_drag(ed):
    ed._entry_list.rows_move_requested.emit([0], 4)
    rows = _rows(ed)
    assert len(rows) == len(ed._entries)
    assert [r.split('  —  ')[0] for r in rows] == [
        e.title_text.strip().replace('\n', ' ') for e in ed._entries]


def test_the_moved_entry_stays_selected(ed):
    ed._entry_list.rows_move_requested.emit([3], 0)
    assert ed._entry_list.currentRow() == 0
    assert ed._duration_spin.value() == 303       # TESTERS', not DIRECTOR's


def test_a_drag_marks_the_editor_dirty(ed):
    ed._entry_list.rows_move_requested.emit([3], 0)
    assert ed.has_unsaved_changes() is True
    assert ed._save_btn.isEnabled() is True


def test_dropping_an_entry_where_it_already_is_changes_nothing(ed):
    before = _titles(ed)
    ed._entry_list.rows_move_requested.emit([1], 1)
    ed._entry_list.rows_move_requested.emit([1], 2)
    assert _titles(ed) == before
    assert ed.has_unsaved_changes() is False


def test_a_drag_tints_every_row(ed):
    ed._entry_list.rows_move_requested.emit([3], 0)
    assert len(ed._dirty_symbols) == 4


# ── The script follows the list ────────────────────────────────────────────

def test_the_entrys_command_moves_with_it(ed):
    ed._entry_list.rows_move_requested.emit([3], 0)
    prints = [(p, d) for t, p, d in _script(ed)
              if t == 'PRINT' and p != 'DUMMY']
    assert prints == [('TESTERS', 303), ('DIRECTOR', 300),
                      ('PROGRAMMERS', 301), ('ARTISTS', 302)]


def test_the_pacing_of_the_sequence_stays_where_it_is(ed):
    ed._entry_list.rows_move_requested.emit([3], 0)
    kinds = [(i, t) for i, (t, _p, _d) in enumerate(_script(ed))
             if t != 'PRINT']
    assert kinds == [(0, 'MAPNEXT'), (4, 'MON'),
                     (7, 'THEENDGFX'), (8, 'WAITBUTTON')]


def test_the_blank_pause_stays_where_it_is(ed):
    ed._entry_list.rows_move_requested.emit([3], 0)
    assert _script(ed)[3] == ('PRINT', 'DUMMY', 60)


def test_a_duration_edited_after_a_drag_still_lands_on_the_right_entry(ed):
    ed._entry_list.rows_move_requested.emit([3], 0)   # TESTERS to the top
    ed._entry_list.setCurrentRow(0)
    ed._duration_spin.setValue(555)
    by_name = {p: d for t, p, d in _script(ed) if t == 'PRINT'}
    assert by_name['TESTERS'] == 555
    assert by_name['DIRECTOR'] == 300


def test_a_command_naming_no_entry_is_left_alone(ed):
    # A project may print something the text table does not list. Moving it
    # would be guesswork, so it keeps its slot.
    ed._script.insert(2, ce.ScriptCommand('PRINT', 'GHOST', 42))
    ed._entry_list.rows_move_requested.emit([3], 0)
    assert _script(ed)[2] == ('PRINT', 'GHOST', 42)


def test_moving_several_entries_keeps_their_order(ed):
    ed._entry_list.rows_move_requested.emit([0, 1], 4)
    assert _titles(ed) == ['ARTISTS', 'TESTERS', 'DIRECTOR', 'PROGRAMMERS']
    prints = [p for t, p, _d in _script(ed)
              if t == 'PRINT' and p != 'DUMMY']
    assert prints == ['ARTISTS', 'TESTERS', 'DIRECTOR', 'PROGRAMMERS']


# ── Saving, and coming back ────────────────────────────────────────────────

def test_the_new_order_reaches_the_game(ed, project):
    ed._entry_list.rows_move_requested.emit([3], 0)
    ed._on_save()
    assert _saved_script(project) == [
        'CREDITS_MAPNEXT(ROUTE23, 16)',
        'CREDITS_PRINT(TESTERS, 303)',
        'CREDITS_PRINT(DIRECTOR, 300)',
        'CREDITS_PRINT(DUMMY, 60)',
        'CREDITS_MON(TEKTITE)',
        'CREDITS_PRINT(PROGRAMMERS, 301)',
        'CREDITS_PRINT(ARTISTS, 302)',
        'CREDITS_THEENDGFX(THE_END, 120)',
        'CREDITS_WAITBUTTON(60)',
    ]


def test_the_saved_names_still_line_up_with_the_saved_table(ed, project):
    # The script addresses the table by name, so the enum and the table have
    # to stay in the same order as each other or every entry prints as some
    # other entry.
    ed._entry_list.rows_move_requested.emit([3], 0)
    ed._on_save()
    enum = [e.split(' =')[0].replace('CREDITS_STRING_', '')
            for e in _saved(project, 'enum CreditsString')]
    table = [row.split(',')[0].lstrip('{ ').replace('gCreditsString_', '').upper()
             for row in _saved(project, 'sCreditsTexts')]
    assert enum == ['TESTERS', 'DIRECTOR', 'PROGRAMMERS', 'ARTISTS', 'DUMMY']
    assert table == ['TESTERS', 'DIRECTOR', 'PROGRAMMERS', 'ARTISTS',
                     'GSTRING_DUMMY']


def test_the_order_survives_a_reload(ed, project):
    ed._entry_list.rows_move_requested.emit([3], 0)
    ed._on_save()
    ed.load_project(str(project))
    assert _titles(ed) == ['TESTERS', 'DIRECTOR', 'PROGRAMMERS', 'ARTISTS']
    assert [ed._duration_for_entry(e) for e in ed._entries] == [
        303, 300, 301, 302]


def test_saving_clears_the_dirty_marks(ed):
    ed._entry_list.rows_move_requested.emit([3], 0)
    ed._on_save()
    assert ed.has_unsaved_changes() is False
    assert ed._dirty_symbols == set()


# ── Removing an entry takes its own command with it ────────────────────────

def test_removing_an_entry_removes_its_own_command(ed, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, 'question',
                        staticmethod(lambda *a, **k:
                                     QMessageBox.StandardButton.Yes))
    ed._entry_list.setCurrentRow(2)             # ARTISTS, after the pause
    ed._on_remove_entry()
    assert _titles(ed) == ['DIRECTOR', 'PROGRAMMERS', 'TESTERS']
    prints = [p for t, p, _d in _script(ed) if t == 'PRINT']
    assert prints == ['DIRECTOR', 'PROGRAMMERS', 'DUMMY', 'TESTERS']


# ── The shared widget, where this editor depends on it ─────────────────────

def test_dropping_on_a_rows_middle_does_not_send_it_to_the_end(qapp):
    # The drop indicator reads as "on" a row when the pointer is near its
    # middle. Treating that as "no row" is what let a dragged row land past
    # the end of the list.
    lw = ReorderableListWidget()
    for name in ('a', 'b', 'c'):
        lw.addItem(QListWidgetItem(name))
    rect = lw.visualItemRect(lw.item(1))

    class _Event:
        def position(self):
            return QPointF(float(rect.center().x()), float(rect.center().y()))

    assert lw._drop_row(_Event()) == 1


def test_dropping_past_the_last_row_appends(qapp):
    lw = ReorderableListWidget()
    for name in ('a', 'b', 'c'):
        lw.addItem(QListWidgetItem(name))

    class _Event:
        def position(self):
            return QPointF(5.0, 10_000.0)       # far below every row

    assert lw._drop_row(_Event()) == 3
