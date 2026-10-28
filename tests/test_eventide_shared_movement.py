"""Copying an event must not quietly make two events share one move route.

A movement is a NAMED list of steps in the script file, shared by every command
that points at its name. The editor presents it as "the steps this command
does", which is a different thing — so duplicating an event copies the NAME, the
copy and the original walk the same steps, and editing either one rewrites both
with nothing on screen to say so.

That is exactly what happened duplicating a map's intro trigger: the new trigger
looked independent and silently shared three movements with the original, and
there was no way in the editor to give the copy its own.

Fixtures are built in memory; no game source is read.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

pytest.importorskip('PyQt6.QtWidgets')

from PyQt6.QtWidgets import QApplication  # noqa: E402

import eventide.ui.event_editor_tab as eet  # noqa: E402


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def scripts(monkeypatch):
    """Two events duplicated from each other, sharing their movements."""
    data = {
        'Woods_EventScript_IntroLeft': [
            ('applymovement', 'OBJ_EVENT_ID_PLAYER', 'Woods_Movement_3'),
            ('applymovement', '8', 'Woods_Movement_4'),
        ],
        'Woods_EventScript_IntroRight': [
            ('applymovement', 'OBJ_EVENT_ID_PLAYER', 'Woods_Movement_3'),
            ('applymovement', '8', 'Woods_Movement_4'),
        ],
        'Woods_EventScript_Postman': [
            ('applymovement', '8', 'Woods_Movement_9'),
        ],
        'Woods_Movement_3': [('walk_left',), ('step_end',)],
        'Woods_Movement_4': [('walk_right',), ('step_end',)],
        'Woods_Movement_9': [('face_player',), ('step_end',)],
    }
    monkeypatch.setattr(eet, '_ALL_SCRIPTS', data)
    monkeypatch.setattr(eet, '_MOVEMENT_LABELS',
                        ['Woods_Movement_3', 'Woods_Movement_4',
                         'Woods_Movement_9'])
    return data


# ── knowing a movement is shared ──────────────────────────────────────────

def test_a_shared_movement_names_every_user(scripts):
    assert eet._movement_users('Woods_Movement_4') == [
        'Woods_EventScript_IntroLeft', 'Woods_EventScript_IntroRight']


def test_a_movement_used_once_is_not_shared(scripts):
    assert len(eet._movement_users('Woods_Movement_9')) == 1


def test_an_unused_movement_has_no_users(scripts):
    assert eet._movement_users('Woods_Movement_404') == []


def test_a_blank_label_is_not_a_user(scripts):
    assert eet._movement_users('') == []


def test_a_script_naming_it_twice_counts_once(scripts):
    scripts['Woods_EventScript_Twice'] = [
        ('applymovement', '8', 'Woods_Movement_4'),
        ('applymovement', '9', 'Woods_Movement_4'),
    ]
    assert eet._movement_users('Woods_Movement_4').count(
        'Woods_EventScript_Twice') == 1


# ── saying so on the command ──────────────────────────────────────────────

def test_the_command_says_when_its_steps_are_shared(qapp, scripts):
    w = eet._ApplyMovementWidget('8', 'Woods_Movement_4')
    assert w._shared_lbl.text() == 'shared by 2'


def test_the_command_stays_quiet_when_they_are_not(qapp, scripts):
    w = eet._ApplyMovementWidget('8', 'Woods_Movement_9')
    assert w._shared_lbl.text() == ''


def test_the_note_follows_the_chosen_movement(qapp, scripts):
    w = eet._ApplyMovementWidget('8', 'Woods_Movement_9')
    w.move_combo.setCurrentText('Woods_Movement_4')
    assert w._shared_lbl.text() == 'shared by 2'


# ── minting a private copy ────────────────────────────────────────────────

def test_a_copy_is_numbered_alongside_its_siblings(scripts):
    assert eet._fresh_movement_label('Woods_Movement_4') == 'Woods_Movement_1'


def test_a_copy_never_takes_a_name_already_in_use(scripts):
    scripts['Woods_Movement_1'] = [('step_end',)]
    scripts['Woods_Movement_2'] = [('step_end',)]
    assert eet._fresh_movement_label('Woods_Movement_4') == 'Woods_Movement_5'


def test_an_oddly_named_movement_still_gets_a_copy(scripts):
    """A movement that is not <Map>_Movement_<n> must still be copyable."""
    out = eet._fresh_movement_label('Common_Movement_WalkInPlace')
    assert out.startswith('Common_Movement_WalkInPlace_Copy')
    assert out not in scripts


def test_the_copy_starts_identical_to_what_was_shared(qapp, scripts):
    """Splitting off a copy must not change what the scene does."""
    new = eet._fresh_movement_label('Woods_Movement_4')
    eet._ALL_SCRIPTS[new] = list(eet._ALL_SCRIPTS['Woods_Movement_4'])
    assert eet._ALL_SCRIPTS[new] == [('walk_right',), ('step_end',)]
    assert eet._ALL_SCRIPTS[new] is not eet._ALL_SCRIPTS['Woods_Movement_4']


def test_editing_the_copy_leaves_the_original_alone(qapp, scripts):
    new = eet._fresh_movement_label('Woods_Movement_4')
    eet._ALL_SCRIPTS[new] = list(eet._ALL_SCRIPTS['Woods_Movement_4'])
    eet._ALL_SCRIPTS[new].append(('walk_right',))
    assert eet._ALL_SCRIPTS['Woods_Movement_4'] == [
        ('walk_right',), ('step_end',)]


# ── pasting a duplicated scene ────────────────────────────────────────────
#
# Duplicating an event means copying its script and pasting it. The script
# names its movements, so without this the duplicate walks the ORIGINAL's
# steps. Duplicating a scene almost never means "and share the movements".

class _Tab:
    """Just enough of the tab for the paste-time rewrite."""
    def __init__(self):
        self._all_scripts = dict(eet._ALL_SCRIPTS)

    _privatise_pasted_movements = eet.EventEditorTab._privatise_pasted_movements


@pytest.fixture
def tab(scripts):
    return _Tab()


def test_a_pasted_movement_gets_its_own_copy(tab):
    out, renamed = tab._privatise_pasted_movements(
        [('applymovement', '8', 'Woods_Movement_4')])
    assert renamed['Woods_Movement_4'] != 'Woods_Movement_4'
    assert out[0][2] == renamed['Woods_Movement_4']


def test_the_copy_has_the_same_steps(tab):
    _out, renamed = tab._privatise_pasted_movements(
        [('applymovement', '8', 'Woods_Movement_4')])
    new = renamed['Woods_Movement_4']
    assert eet._ALL_SCRIPTS[new] == [('walk_right',), ('step_end',)]


def test_the_original_is_untouched(tab):
    tab._privatise_pasted_movements(
        [('applymovement', '8', 'Woods_Movement_4')])
    assert eet._ALL_SCRIPTS['Woods_Movement_4'] == [
        ('walk_right',), ('step_end',)]


def test_each_movement_in_the_scene_gets_its_own(tab):
    out, renamed = tab._privatise_pasted_movements([
        ('applymovement', 'OBJ_EVENT_ID_PLAYER', 'Woods_Movement_3'),
        ('applymovement', '8', 'Woods_Movement_4'),
    ])
    assert len(set(renamed.values())) == 2
    assert out[0][2] != out[1][2]


def test_one_movement_used_twice_in_the_scene_stays_one(tab):
    """Two commands sharing a movement are one scene — they keep sharing."""
    out, renamed = tab._privatise_pasted_movements([
        ('applymovement', '8', 'Woods_Movement_4'),
        ('applymovement', '9', 'Woods_Movement_4'),
    ])
    assert out[0][2] == out[1][2]
    assert len(renamed) == 1


def test_the_shared_library_is_left_alone(tab):
    """Common_Movement_* is meant to be shared by everything."""
    out, renamed = tab._privatise_pasted_movements(
        [('applymovement', '8', 'Common_Movement_FacePlayer')])
    assert out[0][2] == 'Common_Movement_FacePlayer'
    assert renamed == {}


def test_a_movement_this_map_never_heard_of_is_left_alone(tab):
    """Nothing to copy, and no original to protect."""
    out, renamed = tab._privatise_pasted_movements(
        [('applymovement', '8', 'Elsewhere_Movement_1')])
    assert out[0][2] == 'Elsewhere_Movement_1'
    assert renamed == {}


def test_other_commands_pass_through_untouched(tab):
    cmds = [('lockall',), ('msgbox', 'Woods_Text_1'), ('releaseall',)]
    out, renamed = tab._privatise_pasted_movements(list(cmds))
    assert out == cmds
    assert renamed == {}


def test_the_target_is_preserved(tab):
    out, _r = tab._privatise_pasted_movements(
        [('applymovement', 'OBJ_EVENT_ID_PLAYER', 'Woods_Movement_4')])
    assert out[0][1] == 'OBJ_EVENT_ID_PLAYER'


def test_the_copy_is_queued_to_be_written(tab):
    """A new movement nobody writes out is a build error waiting."""
    _out, renamed = tab._privatise_pasted_movements(
        [('applymovement', '8', 'Woods_Movement_4')])
    new = renamed['Woods_Movement_4']
    assert tab._modified_movements[new] == [('walk_right',), ('step_end',)]
