"""Dragging a command moves it. It does not duplicate or destroy one.

Dragging one command onto another duplicated it and deleted a different one:

    @>End                      the row that was dragged
    @>Lock All                 written over by the drop
    @>Lock All                 the original, still where it was
                               ... and the Text command simply gone

The cause is that the editor asked Qt to move the rows and then read the
result back. A QListWidget implements a row drop as overwrite-then-remove
rather than insert-then-remove: the dragged row's contents are written OVER
the row it landed on, the original stays where it was, the list comes back the
same length as before, and `dropMimeData` reports success. The editor then
faithfully copied that wreckage into its own data.

There is a second way in. The trailing "@>" line was draggable and could be
dropped onto, so a drop could land PAST it. Nothing checked that it was still
last, and the length of the list still looked right, so this went unnoticed
until the indentation pass wrote a real command's text over it — at which
point deleting that convincing-looking copy removed the "@>" line and took a
real command's data with it.

So the editor now owns the move: the drop is read only for WHERE it landed,
Qt is refused, the data is reordered, and the widget is repainted from it.
One order instead of two that have to agree.
"""
from __future__ import annotations

import os

import pytest

from ui.custom_widgets.reorderable_list import reorder_rows

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

pytest.importorskip('PyQt6.QtWidgets')

# Qt has to be imported while the module is being imported, not later from
# inside a fixture: the suite swaps each test file's modules in and out around
# its import, and a half-swapped PyQt6 takes the interpreter down with it.
from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

import eventide.ui.event_editor_tab as eet  # noqa: E402


# ── the reorder itself, with no widget anywhere near it ───────────────────

ROWS = ['a', 'b', 'c', 'd']


def test_a_row_moves_to_the_top():
    assert reorder_rows(ROWS, [2], 0)[0] == ['c', 'a', 'b', 'd']


def test_a_row_moves_to_the_bottom():
    assert reorder_rows(ROWS, [0], 4)[0] == ['b', 'c', 'd', 'a']


def test_nothing_is_duplicated_or_lost():
    """The reported bug, as an invariant: a move is a permutation."""
    for src in range(4):
        for dest in range(5):
            out, _ = reorder_rows(ROWS, [src], dest)
            assert sorted(out) == sorted(ROWS)
            assert len(out) == len(ROWS)


def test_dropping_a_row_where_it_already_is_changes_nothing():
    assert reorder_rows(ROWS, [1], 1)[0] == ROWS
    assert reorder_rows(ROWS, [1], 2)[0] == ROWS


def test_several_rows_move_together_and_keep_their_order():
    out, landed = reorder_rows(ROWS, [0, 2], 4)
    assert out == ['b', 'd', 'a', 'c']
    assert landed == [2, 3]


def test_the_moved_rows_are_reported_so_they_stay_selected():
    out, landed = reorder_rows(ROWS, [3], 1)
    assert out == ['a', 'd', 'b', 'c']
    assert [out[i] for i in landed] == ['d']


def test_an_insertion_point_past_the_end_lands_at_the_end():
    assert reorder_rows(ROWS, [0], 99)[0] == ['b', 'c', 'd', 'a']


def test_a_negative_insertion_point_lands_at_the_top():
    assert reorder_rows(ROWS, [2], -5)[0] == ['c', 'a', 'b', 'd']


def test_rows_that_do_not_exist_are_ignored():
    assert reorder_rows(ROWS, [99], 0)[0] == ROWS
    assert reorder_rows(ROWS, [], 0)[0] == ROWS


def test_the_same_row_twice_is_moved_once():
    assert reorder_rows(ROWS, [1, 1], 0)[0] == ['b', 'a', 'c', 'd']


def test_the_original_list_is_not_modified():
    rows = list(ROWS)
    reorder_rows(rows, [0], 3)
    assert rows == ROWS


# ── the editor's own drop handling ────────────────────────────────────────

@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def tab(qapp, tmp_path):
    class _StubMW:
        def __init__(self):
            self.messages = []

        def log_message(self, msg, *a, **k):
            self.messages.append(str(msg))

        def isWindowModified(self):
            return False

        def setWindowModified(self, *a):
            pass

    t = eet.EventEditorTab(_StubMW())
    t._root_dir = tmp_path
    t._loading = False
    return t


def _rows(tab):
    """The commands as the WIDGET shows them, read back independently."""
    out = []
    for i in range(tab._cmd_list.count()):
        data = tab._cmd_list.item(i).data(Qt.ItemDataRole.UserRole)
        if data is not None:
            out.append(data)
    return out


def _load(tab, commands):
    tab._rebuild_cmd_list(commands)


SIMPLE = [('lockall',), ('message', 'T1', 'hi', 'DEF'), ('releaseall',),
          ('end',)]


def test_a_drag_moves_the_command(tab):
    _load(tab, SIMPLE)
    tab._on_rows_move_requested([3], 0)
    assert tab._cmd_tuples[0] == ('end',)
    assert len(tab._cmd_tuples) == 4


def test_a_drag_duplicates_nothing(tab):
    """The reported bug, at the level the user hit it."""
    _load(tab, SIMPLE)
    tab._on_rows_move_requested([3], 0)
    assert sorted(tab._cmd_tuples) == sorted(SIMPLE)


def test_a_drag_destroys_nothing(tab):
    _load(tab, SIMPLE)
    for dest in range(5):
        _load(tab, SIMPLE)
        tab._on_rows_move_requested([3], dest)
        assert len(tab._cmd_tuples) == 4, f'dest={dest}'
        assert sorted(tab._cmd_tuples) == sorted(SIMPLE), f'dest={dest}'


def test_the_widget_and_the_data_still_agree(tab):
    _load(tab, SIMPLE)
    tab._on_rows_move_requested([0], 3)
    assert _rows(tab) == list(tab._cmd_tuples)
    assert tab._cmd_list.count() == len(tab._cmd_tuples) + 1


def test_the_insertion_line_stays_at_the_bottom(tab):
    """A row landing past it is what made a command undeletable."""
    _load(tab, SIMPLE)
    tab._on_rows_move_requested([0], 4)
    last = tab._cmd_list.item(tab._cmd_list.count() - 1)
    assert last.data(Qt.ItemDataRole.UserRole) is None
    assert last.text() == '@>'


def test_the_insertion_line_cannot_be_dragged(tab):
    _load(tab, SIMPLE)
    last = tab._cmd_list.item(tab._cmd_list.count() - 1)
    assert not (last.flags() & Qt.ItemFlag.ItemIsDragEnabled)


def test_dragging_the_insertion_line_does_nothing(tab):
    _load(tab, SIMPLE)
    before = list(tab._cmd_tuples)
    tab._on_rows_move_requested([4], 0)       # the "@>" row
    assert tab._cmd_tuples == before


def test_the_moved_command_is_left_selected(tab):
    _load(tab, SIMPLE)
    tab._on_rows_move_requested([3], 0)
    assert tab._cmd_list.currentRow() == 0
    assert [tab._cmd_list.row(i) for i in tab._cmd_list.selectedItems()] == [0]


def test_a_drag_can_be_undone(tab):
    _load(tab, SIMPLE)
    tab._on_rows_move_requested([3], 0)
    tab._on_undo()
    assert list(tab._cmd_tuples) == SIMPLE


def test_dropping_where_it_already_was_is_not_an_edit(tab):
    """No undo step, and nothing marked unsaved, for a drag that did nothing."""
    _load(tab, SIMPLE)
    tab._clear_undo()
    tab._on_rows_move_requested([1], 1)
    assert tab._undo_stack == []


def test_several_selected_commands_move_together(tab):
    _load(tab, SIMPLE)
    tab._on_rows_move_requested([0, 1], 4)
    assert list(tab._cmd_tuples) == [('releaseall',), ('end',),
                                     ('lockall',),
                                     ('message', 'T1', 'hi', 'DEF')]


# ── a drag obeys the same structural rules as deleting ────────────────────

CHOICE = [('lockall',), ('choice_yesno', 'q'), ('when_yes',), ('setvar', 'A'),
          ('when_no',), ('setvar', 'B'), ('branch_end',), ('end',)]


def test_a_yes_no_block_travels_whole(tab):
    _load(tab, CHOICE)
    tab._on_rows_move_requested([1], 8)
    heads = [c[0] for c in tab._cmd_tuples]
    assert heads == ['lockall', 'end', 'choice_yesno', 'when_yes', 'setvar',
                     'when_no', 'setvar', 'branch_end']


def test_a_marker_inside_a_block_cannot_be_dragged_out(tab):
    _load(tab, CHOICE)
    before = list(tab._cmd_tuples)
    tab._on_rows_move_requested([6], 0)       # the Branch End
    assert tab._cmd_tuples == before
    assert any('Show Choices' in m for m in tab._mw.messages)


def test_dragging_a_block_keeps_it_balanced(tab):
    _load(tab, CHOICE)
    tab._on_rows_move_requested([1], 0)
    heads = [c[0] for c in tab._cmd_tuples]
    assert heads.index('choice_yesno') < heads.index('branch_end')
    assert heads.count('choice_yesno') == 1 and heads.count('branch_end') == 1


# ── deleting an unbalanced block no longer wipes the page ─────────────────

def test_an_unbalanced_block_is_not_deleted_to_the_end_of_the_page(tab):
    """`_choice_block_end` used to fall back to the last command.

    So deleting a Show Choices header with no Branch End deleted everything
    after it, in one keystroke, with nothing said about it.
    """
    _load(tab, [('lockall',), ('choice_yesno', 'q'), ('message', 'T', 'x', 'D'),
                ('end',)])
    assert tab._delete_one_row(1) is False
    assert len(tab._cmd_tuples) == 4
    assert any('Branch End' in m for m in tab._mw.messages)


def test_a_balanced_block_is_still_deleted_whole(tab):
    _load(tab, CHOICE)
    assert tab._delete_one_row(1) is True
    assert [c[0] for c in tab._cmd_tuples] == ['lockall', 'end']


def test_duplicating_a_block_copies_all_of_it(tab):
    """Copying the header alone left two headers and one Branch End."""
    _load(tab, CHOICE)
    tab._cmd_list.setCurrentRow(1)
    tab._on_duplicate()
    heads = [c[0] for c in tab._cmd_tuples]
    assert heads.count('choice_yesno') == 2
    assert heads.count('branch_end') == 2
    assert len(tab._cmd_tuples) == len(CHOICE) + 6


def test_duplicating_a_marker_inside_a_block_is_refused(tab):
    _load(tab, CHOICE)
    tab._cmd_list.setCurrentRow(2)            # When[Yes]
    before = list(tab._cmd_tuples)
    tab._on_duplicate()
    assert tab._cmd_tuples == before


def test_duplicating_an_ordinary_command_still_works(tab):
    _load(tab, SIMPLE)
    tab._cmd_list.setCurrentRow(0)
    tab._on_duplicate()
    assert len(tab._cmd_tuples) == 5
    assert tab._cmd_tuples[0] == tab._cmd_tuples[1] == ('lockall',)


# ── the indentation pass cannot disguise a stray row ─────────────────────

def test_the_indentation_pass_never_writes_over_the_insertion_line(tab):
    """What turned a misalignment into a decoy copy of a real command."""
    _load(tab, CHOICE)
    # Force the misalignment the old code could reach: more commands than
    # rows above the "@>" line.
    tab._cmd_tuples.append(('end',))
    tab._reindent_list()
    texts = [tab._cmd_list.item(i).text()
             for i in range(tab._cmd_list.count())
             if tab._cmd_list.item(i).data(Qt.ItemDataRole.UserRole) is None]
    assert texts == ['@>']


# ── the insertion line is told apart by its data, not its position ───────
#
# Going by position is what turned any desync into a spurious ADD: the normal
# way to write a message is "Add Command -> Text", then double-click the new
# row to type the text in. If that row counted as the "@>" line, the
# double-click added a SECOND Text command instead of opening the first —
# "clicking add text often makes 2 of those boxes".

def test_double_clicking_the_insertion_line_adds_a_command(tab, monkeypatch):
    _load(tab, SIMPLE)
    calls = []
    monkeypatch.setattr(tab, '_on_add_command', lambda: calls.append(1))
    tab._on_edit_command(tab._cmd_list.item(tab._cmd_list.count() - 1))
    assert calls == [1]


def test_double_clicking_a_command_never_adds_one(tab, monkeypatch):
    _load(tab, SIMPLE)
    calls = []
    monkeypatch.setattr(tab, '_on_add_command', lambda: calls.append(1))
    # Editing opens a modal dialog, which would simply sit there forever.
    monkeypatch.setattr(eet._CommandEditDialog, 'exec', lambda self: 0)
    for row in range(len(SIMPLE)):
        tab._on_edit_command(tab._cmd_list.item(row))
    assert calls == []


def test_a_command_row_is_never_mistaken_for_the_insertion_line(tab,
                                                                monkeypatch):
    """Even with the two lists out of step, which is how this surfaced."""
    _load(tab, SIMPLE)
    calls = []
    monkeypatch.setattr(tab, '_on_add_command', lambda: calls.append(1))
    # Editing opens a modal dialog, which would simply sit there forever.
    monkeypatch.setattr(eet._CommandEditDialog, 'exec', lambda self: 0)
    tab._cmd_tuples.pop()            # force the desync
    tab._on_edit_command(tab._cmd_list.item(3))
    assert calls == []


def test_the_insertion_line_is_not_a_selected_command(tab):
    _load(tab, SIMPLE)
    tab._cmd_list.setCurrentRow(tab._cmd_list.count() - 1)
    assert tab._selected_cmd_idx() == -1


def test_a_selected_command_is_reported_by_its_row(tab):
    _load(tab, SIMPLE)
    tab._cmd_list.setCurrentRow(2)
    assert tab._selected_cmd_idx() == 2


def test_nothing_selected_is_not_a_command(tab):
    _load(tab, SIMPLE)
    tab._cmd_list.setCurrentRow(-1)
    assert tab._selected_cmd_idx() == -1


# ═════════════════════════════════════════════════════════════════════════════
# The move route's own list
#
# `get_steps` reads the route straight off this list, so the list is the only
# copy of it -- the same arrangement as the Pokedex lists, covered in
# tests/test_dex_reorder.py. A drop allowed to reorder the rows itself would
# write one step over another and the wrong route would be saved.
# ═════════════════════════════════════════════════════════════════════════════

ROUTE = [('walk_up',), ('walk_down',), ('face_left',), ('step_end',)]


@pytest.fixture
def route(qapp):
    return eet._MoveRouteDialog(ROUTE)


def _steps(dlg):
    """The route as get_steps would hand it back, minus the trailing end."""
    return [m for (m,) in dlg.get_steps()][:-1]


def test_the_route_list_owns_its_drop(route):
    # By contract rather than by class identity: `eventide` is kept across
    # the suite's per-file module worlds while `ui.*` is re-imported, so the
    # editor can legitimately hold a different instance of the same class
    # than this file would import.
    assert type(route._step_list).__name__ == 'ReorderableListWidget'
    assert hasattr(route._step_list, 'rows_move_requested')


def test_the_route_loaded(route):
    assert _steps(route) == ['walk_up', 'walk_down', 'face_left']


def test_dragging_a_step_moves_it(route):
    route._step_list.rows_move_requested.emit([2], 0)
    assert _steps(route) == ['face_left', 'walk_up', 'walk_down']


def test_dragging_a_step_duplicates_nothing_and_loses_nothing(route):
    before = sorted(_steps(route))
    route._step_list.rows_move_requested.emit([2], 0)
    assert sorted(_steps(route)) == before


def test_a_dragged_step_can_be_undone(route):
    # Every other edit in this dialog could be taken back with Ctrl+Z and a
    # drag could not, which was the one edit where it mattered most.
    route._step_list.rows_move_requested.emit([2], 0)
    assert _steps(route) == ['face_left', 'walk_up', 'walk_down']
    route._on_undo()
    assert _steps(route) == ['walk_up', 'walk_down', 'face_left']


def test_a_dragged_step_can_be_redone(route):
    route._step_list.rows_move_requested.emit([2], 0)
    route._on_undo()
    route._on_redo()
    assert _steps(route) == ['face_left', 'walk_up', 'walk_down']


def test_dropping_a_step_where_it_already_is_costs_no_undo(route):
    route._step_list.rows_move_requested.emit([1], 1)
    assert route._undo == []
    assert _steps(route) == ['walk_up', 'walk_down', 'face_left']


def test_the_dragged_step_stays_selected(route):
    route._step_list.rows_move_requested.emit([2], 0)
    assert route._step_list.currentRow() == 0
    assert len(route._step_list.selectedItems()) == 1


def test_several_steps_move_together(route):
    route._step_list.rows_move_requested.emit([0, 1], 3)
    assert _steps(route) == ['face_left', 'walk_up', 'walk_down']
