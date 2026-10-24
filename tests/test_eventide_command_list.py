"""The command list has to behave like a list.

Before this, the only way to delete a command was the Delete button, the only
way to copy one was the Copy button, there was no undo anywhere in the editor,
and the list was SingleSelection so no operation could ever touch more than one
row.

Everything here drives the real widget on a throwaway map.
"""
from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

pytest.importorskip('PyQt6.QtWidgets')

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QAbstractItemView, QApplication  # noqa: E402

import eventide.ui.event_editor_tab as eet  # noqa: E402


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


MAP_JSON = {
    "object_events": [
        {"type": "object", "graphics_id": "OBJ_EVENT_GFX_ITEM_BALL",
         "x": 1, "y": 1, "elevation": 3,
         "movement_type": "MOVEMENT_TYPE_NONE",
         "movement_range_x": 1, "movement_range_y": 1,
         "trainer_type": "TRAINER_TYPE_NONE",
         "trainer_sight_or_berry_tree_id": "0",
         "script": "Town_EventScript_Thing", "flag": "0"},
    ]
}

SCRIPT = (
    'Town_EventScript_Thing::\n'
    '\tlockall\n'
    '\tplayse SE_SELECT\n'
    '\tsetflag FLAG_UNUSED_0x4A7\n'
    '\treleaseall\n'
    '\tend\n'
)


@pytest.fixture
def tab(qapp, tmp_path, monkeypatch):
    for name in ('information', 'warning', 'critical', 'question'):
        monkeypatch.setattr(eet.QMessageBox, name,
                            staticmethod(lambda *a, **k: None), raising=False)
    d = tmp_path / 'data' / 'maps' / 'Town'
    d.mkdir(parents=True)
    (d / 'map.json').write_text(json.dumps(MAP_JSON), encoding='utf-8')
    (d / 'scripts.inc').write_text(SCRIPT, encoding='utf-8')
    (d / 'text.inc').write_text('', encoding='utf-8')

    class StubMW:
        def log_message(self, *a, **k):
            pass

        def isWindowModified(self):
            return False

    t = eet.EventEditorTab(StubMW())
    t._root_dir = tmp_path
    t._load_map(d)
    return t


def heads(tab):
    return [c[0] for c in tab._cmd_tuples]


def select(tab, rows):
    tab._cmd_list.clearSelection()
    for r in rows:
        tab._cmd_list.item(r).setSelected(True)
    tab._cmd_list.setCurrentRow(rows[-1])


# ═══════════════════════════════════════════════════════════════════════════
# Selecting more than one thing
# ═══════════════════════════════════════════════════════════════════════════

def test_several_commands_can_be_selected(tab):
    """SingleSelection meant Ctrl-click and Shift-click did nothing."""
    assert (tab._cmd_list.selectionMode()
            == QAbstractItemView.SelectionMode.ExtendedSelection)


def test_the_trailing_insertion_line_is_not_a_command(tab):
    """The list ends with an empty "@>" drop target. Select-all then Delete
    must not try to delete it."""
    tab._cmd_list.selectAll()
    rows = tab._selected_cmd_rows()
    assert rows == list(range(len(tab._cmd_tuples)))
    assert len(rows) == tab._cmd_list.count() - 1


def test_deleting_a_multi_selection_removes_all_of_them(tab):
    before = heads(tab)
    select(tab, [1, 2])          # playse + setflag
    tab._on_del_command()
    assert heads(tab) == [before[0], before[3], before[4]]


def test_deleting_highest_first_does_not_remove_the_wrong_rows(tab):
    """Removing low indices first shifts the ones still to go, so a naive
    loop deletes neighbours instead."""
    select(tab, [0, 2])          # lockall + setflag, with playse between
    tab._on_del_command()
    assert heads(tab) == ['playse', 'releaseall', 'end']


def test_copying_a_multi_selection_keeps_all_of_them(tab):
    select(tab, [1, 2])
    tab._on_copy()
    assert eet.EventEditorTab._clipboard == [('playse', 'SE_SELECT'),
                                             ('setflag', 'FLAG_UNUSED_0x4A7')]


def test_cut_removes_what_it_copied(tab):
    select(tab, [1, 2])
    tab._on_cut()
    assert len(eet.EventEditorTab._clipboard) == 2
    assert heads(tab) == ['lockall', 'releaseall', 'end']


def test_a_multi_selection_pastes_back_in_order(tab):
    select(tab, [1, 2])
    tab._on_cut()
    select(tab, [0])
    tab._on_paste()
    assert heads(tab) == ['lockall', 'playse', 'setflag', 'releaseall', 'end']


def test_an_old_single_tuple_clipboard_still_pastes(tab):
    """The clipboard used to hold one bare tuple. Anything that still sets it
    that way must not break paste."""
    eet.EventEditorTab._clipboard = ('waitstate',)
    select(tab, [0])
    tab._on_paste()
    assert heads(tab)[1] == 'waitstate'


# ═══════════════════════════════════════════════════════════════════════════
# Undo / redo
# ═══════════════════════════════════════════════════════════════════════════

def test_undo_puts_back_a_deleted_command(tab):
    before = heads(tab)
    select(tab, [1])
    tab._on_del_command()
    assert heads(tab) != before
    tab._on_undo()
    assert heads(tab) == before


def test_undo_puts_back_a_multi_delete(tab):
    before = heads(tab)
    select(tab, [1, 2])
    tab._on_del_command()
    tab._on_undo()
    assert heads(tab) == before


def test_redo_reapplies_what_undo_took_back(tab):
    select(tab, [1])
    tab._on_del_command()
    after_delete = heads(tab)
    tab._on_undo()
    tab._on_redo()
    assert heads(tab) == after_delete


def test_undo_walks_back_several_steps(tab):
    before = heads(tab)
    select(tab, [1])
    tab._on_del_command()
    select(tab, [1])
    tab._on_del_command()
    assert len(heads(tab)) == len(before) - 2
    tab._on_undo()
    tab._on_undo()
    assert heads(tab) == before


def test_a_fresh_edit_drops_the_redo_branch(tab):
    """Undo, then do something else: the thing you undid must not come back
    on a later redo."""
    select(tab, [1])
    tab._on_del_command()
    tab._on_undo()
    select(tab, [2])
    tab._on_del_command()
    n = len(heads(tab))
    tab._on_redo()
    assert len(heads(tab)) == n, 'redo resurrected an abandoned branch'


def test_undo_on_an_empty_history_does_nothing(tab):
    before = heads(tab)
    tab._on_undo()
    tab._on_undo()
    assert heads(tab) == before


def test_history_does_not_leak_between_pages(tab):
    """An undo replayed onto a different page's commands would be silent
    corruption, so showing a page starts a fresh history."""
    select(tab, [1])
    tab._on_del_command()
    assert tab._undo_stack
    tab._display_page({'commands': [('end',)], '_label': 'X'})
    assert tab._undo_stack == []
    before = heads(tab)
    tab._on_undo()
    assert heads(tab) == before


def test_the_list_widget_matches_the_data_after_undo(tab):
    """The visible rows and _cmd_tuples must not drift apart."""
    select(tab, [1, 2])
    tab._on_del_command()
    tab._on_undo()
    shown = [tab._cmd_list.item(i).data(Qt.ItemDataRole.UserRole)
             for i in range(tab._cmd_list.count() - 1)]
    assert shown == tab._cmd_tuples
    assert tab._cmd_list.item(tab._cmd_list.count() - 1).text() == '@>'


# ═══════════════════════════════════════════════════════════════════════════
# The structural rules multi-delete has to keep
# ═══════════════════════════════════════════════════════════════════════════

def test_a_label_marker_survives_a_delete_that_includes_it(tab):
    """Label markers are structural. Selecting everything and pressing Delete
    must not take them out."""
    tab._cmd_tuples.insert(0, ('_label_marker', 'Town_EventScript_Thing'))
    tab._rebuild_cmd_list(tab._cmd_tuples)
    tab._cmd_list.selectAll()
    tab._on_del_command()
    assert heads(tab) == ['_label_marker']
