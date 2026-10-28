"""One list of everything wrong with a map.

None of the failures this collects says anything in game. A trigger that can
never fire simply does nothing. A scene arranged to replay just replays. A
command missing a value the build needs fails hours later, in an assembler
message naming a macro and a line number. Each check already reported in the
one dialog it belonged to, so the only way to find any of them was to open
every event and look.

The checks themselves are tested where they live; what matters here is that
the scan runs them over the whole map, reads UNSAVED edits, ranks by how badly
it breaks, and points at the right event and step.
"""
from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

pytest.importorskip('PyQt6.QtWidgets')

from PyQt6.QtWidgets import QApplication  # noqa: E402

import eventide.ui.event_editor_tab as eet  # noqa: E402


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


MAP = {
    'name': 'Woods',
    'object_events': [
        {'graphics_id': 'OBJ_EVENT_GFX_MAN', 'x': 1, 'y': 1,
         'script': 'Woods_EventScript_Postman'},
    ],
    'coord_events': [
        # Backwards: fires on 1, and its own script sets 1. Can never run.
        {'type': 'trigger', 'x': 19, 'y': 18, 'elevation': 0,
         'var': 'VAR_0x402A', 'var_value': '1',
         'script': 'Woods_EventScript_Broken'},
        # Nothing switches it off, so it replays for ever.
        {'type': 'trigger', 'x': 20, 'y': 18, 'elevation': 0,
         'var': 'VAR_0x402B', 'var_value': '0',
         'script': 'Woods_EventScript_Repeats'},
        # Correct: fires on the starting value, script sets something else.
        {'type': 'trigger', 'x': 21, 'y': 18, 'elevation': 0,
         'var': 'VAR_0x402C', 'var_value': '0',
         'script': 'Woods_EventScript_Fine'},
    ],
}

SCRIPTS = """Woods_EventScript_Broken::
lockall
setvar VAR_0x402A, 1
releaseall
end

Woods_EventScript_Repeats::
lockall
msgbox Woods_Text_1, MSGBOX_DEFAULT
releaseall
end

Woods_EventScript_Fine::
lockall
setvar VAR_0x402C, 1
releaseall
end

Woods_EventScript_Postman::
lockall
applymovement 1, Woods_Movement_1
releaseall
end

Woods_Movement_1::
walk_left
step_end
"""

# A SECOND script naming the same movement, so it is genuinely shared. One
# script naming a movement twice is not sharing — that is one scene, and the
# shared-movement check says so deliberately.
SCRIPTS = SCRIPTS.replace(
    'Woods_EventScript_Fine::\nlockall\n',
    'Woods_EventScript_Fine::\nlockall\napplymovement 1, Woods_Movement_1\n')


@pytest.fixture
def tab(qapp, tmp_path, monkeypatch):
    for name in ('information', 'warning', 'critical', 'question'):
        monkeypatch.setattr(eet.QMessageBox, name,
                            staticmethod(lambda *a, **k: None), raising=False)

    class _StubMW:
        def log_message(self, *a, **k):
            pass

        def isWindowModified(self):
            return False

    d = tmp_path / 'data' / 'maps' / 'Woods'
    d.mkdir(parents=True)
    (d / 'map.json').write_text(json.dumps(MAP), encoding='utf-8')
    (d / 'scripts.inc').write_text(SCRIPTS, encoding='utf-8')
    (d / 'text.inc').write_text('', encoding='utf-8')

    t = eet.EventEditorTab(_StubMW())
    t._root_dir = tmp_path
    t._load_map(d)
    return t


def _texts(problems):
    return ' | '.join(p['text'] for p in problems)


# ── it finds each kind ────────────────────────────────────────────────────

def test_a_trigger_that_can_never_fire_is_an_error(tab):
    found = [p for p in tab.scan_problems()
             if 'Woods_EventScript_Broken' in p['event'] or
             'Broken' in p['event']]
    assert found and found[0]['severity'] == 'error'


def test_a_trigger_that_never_stops_is_a_warning(tab):
    found = [p for p in tab.scan_problems() if 'Repeats' in p['event']]
    assert found and found[0]['severity'] == 'warning'


def test_a_correct_trigger_condition_is_not_reported(tab):
    """It does share a movement, which is a note — but its CONDITION is fine."""
    assert not [p for p in tab.scan_problems()
                if 'Fine' in p['event'] and p['severity'] in ('error', 'warning')]


def test_the_message_explains_rather_than_just_naming(tab):
    assert 'never switches itself off' in _texts(tab.scan_problems())


# ── ranking ───────────────────────────────────────────────────────────────

def test_errors_come_before_warnings(tab):
    sev = [p['severity'] for p in tab.scan_problems()]
    assert sev == sorted(sev, key=lambda s: {'error': 0, 'warning': 1,
                                             'note': 2}.get(s, 3))


# ── it reads what is on screen, not what is on disk ───────────────────────

def test_an_unsaved_fix_stops_being_reported(tab):
    """Fixing something must clear it without saving first."""
    idx = [i for i, o in enumerate(tab._objects)
           if o.get('var') == 'VAR_0x402A'][0]
    tab._display_object(idx)
    tab._objects[idx]['var_value'] = '0'
    tab.trigger_val_combo.set_value('0')
    assert not [p for p in tab.scan_problems()
                if p['event_idx'] == idx and p['severity'] == 'error']


def test_an_unsaved_break_is_reported(tab):
    """And breaking something must show up without saving either."""
    idx = [i for i, o in enumerate(tab._objects)
           if o.get('var') == 'VAR_0x402C'][0]
    tab._display_object(idx)
    tab._objects[idx]['var_value'] = '1'
    assert [p for p in tab.scan_problems()
            if p['event_idx'] == idx and p['severity'] == 'error']


# ── it points somewhere useful ────────────────────────────────────────────

def test_every_problem_names_an_event_that_exists(tab):
    for p in tab.scan_problems():
        assert 0 <= p['event_idx'] < len(tab._objects)


def test_a_shared_movement_is_reported_against_both_scenes(tab):
    shared = [p for p in tab.scan_problems()
              if 'Woods_Movement_1' in p['text']]
    assert len(shared) == 2
    assert {p['severity'] for p in shared} == {'note'}


def test_a_command_problem_points_at_the_command(tab):
    shared = [p for p in tab.scan_problems() if p['cmd_idx'] is not None]
    assert shared
    assert all(p['cmd_idx'] >= 0 for p in shared)


def test_jumping_selects_the_event_and_the_step(tab):
    problems = [p for p in tab.scan_problems() if p['cmd_idx'] is not None]
    if not problems:
        pytest.skip('no step-level problem in this fixture')
    p = problems[0]
    tab._jump_to_problem(p)
    assert tab.obj_combo.currentIndex() == p['event_idx']
    assert tab._cmd_list.currentRow() == p['cmd_idx']


def test_a_clean_map_reports_nothing(qapp, tmp_path, monkeypatch):
    for name in ('information', 'warning', 'critical', 'question'):
        monkeypatch.setattr(eet.QMessageBox, name,
                            staticmethod(lambda *a, **k: None), raising=False)

    class _StubMW:
        def log_message(self, *a, **k):
            pass

        def isWindowModified(self):
            return False

    d = tmp_path / 'data' / 'maps' / 'Quiet'
    d.mkdir(parents=True)
    (d / 'map.json').write_text(json.dumps({
        'name': 'Quiet',
        'coord_events': [
            {'type': 'trigger', 'x': 1, 'y': 1, 'elevation': 0,
             'var': 'VAR_0x402C', 'var_value': '0',
             'script': 'Quiet_EventScript_Once'},
        ],
    }), encoding='utf-8')
    (d / 'scripts.inc').write_text(
        'Quiet_EventScript_Once::\nlockall\nsetvar VAR_0x402C, 1\n'
        'releaseall\nend\n', encoding='utf-8')
    (d / 'text.inc').write_text('', encoding='utf-8')

    t = eet.EventEditorTab(_StubMW())
    t._root_dir = tmp_path
    t._load_map(d)
    assert t.scan_problems() == []
