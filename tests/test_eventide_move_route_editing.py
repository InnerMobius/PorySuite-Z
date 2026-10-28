"""Editing a move route should work like every other list.

A route is mostly runs of repeated steps — six Move Rights, a wait, a jump —
so the commonest edit is "several more of those, and move this block up". The
list offered one selection at a time, no copy, no paste and no undo, which made
that the slowest possible edit.

It also has to say, in this window, when the steps belong to more than one
scene. The note on the command behind it and the question asked before it opens
are both out of sight by the time somebody is actually changing steps, and this
is the window where the damage happens.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

pytest.importorskip('PyQt6.QtWidgets')

from PyQt6.QtWidgets import QApplication, QLabel  # noqa: E402

import eventide.ui.event_editor_tab as eet  # noqa: E402


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


STEPS = [('walk_right',), ('walk_right',), ('walk_left',),
         ('face_player',), ('step_end',)]


@pytest.fixture
def dlg(qapp):
    d = eet._MoveRouteDialog([s for s in STEPS], None)
    d._step_list.clearSelection()
    return d


def _macros(d):
    return [m for _t, m in d._state()]


def _select(d, *rows):
    d._step_list.clearSelection()
    for r in rows:
        d._step_list.item(r).setSelected(True)


# ── saying when the steps are not yours alone ─────────────────────────────

def _banner(d):
    return [w.text() for w in d.findChildren(QLabel) if 'shared' in w.text()]


def test_shared_steps_are_announced_in_this_window(qapp):
    d = eet._MoveRouteDialog(list(STEPS), None,
                             shared_with=['Woods_A', 'Woods_B'])
    assert _banner(d)
    assert 'changes them too' in _banner(d)[0]


def test_the_banner_names_the_other_scenes(qapp):
    d = eet._MoveRouteDialog(list(STEPS), None,
                             shared_with=['Woods_A', 'Woods_B'])
    assert 'Woods_A' in _banner(d)[0]


def test_steps_used_once_get_no_banner(qapp):
    d = eet._MoveRouteDialog(list(STEPS), None, shared_with=['Woods_A'])
    assert not _banner(d)


def test_no_sharing_information_means_no_banner(qapp):
    assert not _banner(eet._MoveRouteDialog(list(STEPS), None))


def test_the_command_note_has_no_tooltip_when_nothing_is_shared(qapp,
                                                                monkeypatch):
    """An empty label still has a hover area, so a tooltip set unconditionally
    claimed sharing on commands that had none."""
    monkeypatch.setattr(eet, '_ALL_SCRIPTS', {
        'Woods_Script': [('applymovement', '8', 'Woods_Movement_1')],
        'Woods_Movement_1': [('walk_left',), ('step_end',)],
    })
    monkeypatch.setattr(eet, '_MOVEMENT_LABELS', ['Woods_Movement_1'])
    w = eet._ApplyMovementWidget('8', 'Woods_Movement_1')
    assert w._shared_lbl.text() == ''
    assert w._shared_lbl.toolTip() == ''


def test_the_command_note_gets_its_tooltip_when_shared(qapp, monkeypatch):
    monkeypatch.setattr(eet, '_ALL_SCRIPTS', {
        'Woods_A': [('applymovement', '8', 'Woods_Movement_1')],
        'Woods_B': [('applymovement', '8', 'Woods_Movement_1')],
        'Woods_Movement_1': [('walk_left',), ('step_end',)],
    })
    monkeypatch.setattr(eet, '_MOVEMENT_LABELS', ['Woods_Movement_1'])
    w = eet._ApplyMovementWidget('8', 'Woods_Movement_1')
    assert w._shared_lbl.text() == 'shared by 2'
    assert 'copy' in w._shared_lbl.toolTip()


# ── selecting more than one ───────────────────────────────────────────────

def test_several_steps_can_be_selected(dlg):
    _select(dlg, 0, 2)
    assert dlg._rows() == [0, 2]


def test_with_nothing_selected_it_uses_the_current_row(dlg):
    dlg._step_list.setCurrentRow(1)
    dlg._step_list.clearSelection()
    assert dlg._rows() == [1]


# ── copy, cut, paste ──────────────────────────────────────────────────────

def test_copied_steps_paste_after_the_selection(dlg):
    before = _macros(dlg)
    _select(dlg, 0, 1)
    dlg._on_copy()
    dlg._on_paste_steps()
    assert _macros(dlg) == before[:2] + ['walk_right', 'walk_right'] + before[2:]


def test_paste_leaves_the_new_steps_selected(dlg):
    _select(dlg, 0)
    dlg._on_copy()
    dlg._on_paste_steps()
    assert dlg._rows() == [1]


def test_cut_removes_what_it_copied(dlg):
    before = _macros(dlg)
    _select(dlg, 0, 1)
    dlg._on_cut()
    assert _macros(dlg) == before[2:]
    dlg._on_paste_steps()
    assert _macros(dlg)[:2] == before[2:3] + ['walk_right']


def test_pasting_nothing_does_nothing(dlg):
    before = _macros(dlg)
    dlg._clipboard = []
    dlg._on_paste_steps()
    assert _macros(dlg) == before


# ── deleting and reordering a selection ───────────────────────────────────

def test_delete_removes_every_selected_step(dlg):
    _select(dlg, 0, 2)
    dlg._on_delete()
    assert _macros(dlg) == ['walk_right', 'face_player']


def test_a_block_moves_up_together_and_keeps_its_order(dlg):
    _select(dlg, 2, 3)
    dlg._on_move_up()
    assert _macros(dlg) == ['walk_right', 'walk_left', 'face_player',
                            'walk_right']
    assert dlg._rows() == [1, 2]


def test_a_block_moves_down_together_and_keeps_its_order(dlg):
    _select(dlg, 0, 1)
    dlg._on_move_down()
    assert _macros(dlg) == ['walk_left', 'walk_right', 'walk_right',
                            'face_player']
    assert dlg._rows() == [1, 2]


def test_a_block_at_the_top_will_not_move_up(dlg):
    before = _macros(dlg)
    _select(dlg, 0, 1)
    dlg._on_move_up()
    assert _macros(dlg) == before


def test_a_block_at_the_bottom_will_not_move_down(dlg):
    before = _macros(dlg)
    _select(dlg, 2, 3)
    dlg._on_move_down()
    assert _macros(dlg) == before


# ── undo and redo ─────────────────────────────────────────────────────────

def test_undo_puts_back_a_delete(dlg):
    before = _macros(dlg)
    _select(dlg, 0, 1)
    dlg._on_delete()
    dlg._on_undo()
    assert _macros(dlg) == before


def test_undo_puts_back_a_paste(dlg):
    before = _macros(dlg)
    _select(dlg, 0)
    dlg._on_copy()
    dlg._on_paste_steps()
    dlg._on_undo()
    assert _macros(dlg) == before


def test_redo_reapplies_it(dlg):
    _select(dlg, 0, 1)
    dlg._on_delete()
    after = _macros(dlg)
    dlg._on_undo()
    dlg._on_redo()
    assert _macros(dlg) == after


def test_undo_with_nothing_to_undo_is_harmless(dlg):
    before = _macros(dlg)
    dlg._on_undo()
    dlg._on_undo()
    assert _macros(dlg) == before


def test_a_new_edit_clears_the_redo_trail(dlg):
    _select(dlg, 0)
    dlg._on_delete()
    dlg._on_undo()
    _select(dlg, 1)
    dlg._on_delete()
    assert dlg._redo == []


def test_clear_all_can_be_undone(dlg):
    before = _macros(dlg)
    dlg._on_clear()
    assert _macros(dlg) == []
    dlg._on_undo()
    assert _macros(dlg) == before


# ── what comes out at the end ─────────────────────────────────────────────

def test_the_route_still_ends_with_step_end(dlg):
    _select(dlg, 0)
    dlg._on_copy()
    dlg._on_paste_steps()
    assert dlg.get_steps()[-1] == ('step_end',)
