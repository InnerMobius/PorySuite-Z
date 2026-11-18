"""Face Player + Release All leaves the NPC frozen, and EVENTide says so.

Engine facts this rests on (read 2026-11-17, src/scrcmd.c + event_object_movement.c):
faceplayer gives the talked-to NPC a held movement; while a held movement is
attached the NPC's movement type never runs; ScrCmd_release clears it on that
NPC, ScrCmd_releaseall clears it only on the player. So a page with Face Player
that lets go with Release All strands the NPC (Kakariko Carpenter1).

The owner asked for an unobtrusive warning and for the Release command not to
be called "Release Player". The engine itself is NOT to be changed for this.
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


def _obj(script):
    return {"type": "object", "graphics_id": "OBJ_EVENT_GFX_BALDING_MAN",
            "x": 1, "y": 1, "elevation": 3,
            "movement_type": "MOVEMENT_TYPE_WALK_IN_PLACE_RIGHT",
            "movement_range_x": 0, "movement_range_y": 0,
            "trainer_type": "TRAINER_TYPE_NONE",
            "trainer_sight_or_berry_tree_id": "0",
            "script": script, "flag": "0"}


MAP_JSON = {
    "object_events": [_obj("Town_Carpenter"), _obj("Town_Good")],
    "coord_events": [
        {"type": "trigger", "x": 3, "y": 3, "elevation": 0,
         "var": "VAR_TEMP_1", "var_value": "0",
         "script": "Town_Trigger"},
    ],
}

SCRIPT = (
    'Town_Carpenter::\n'
    '\tlockall\n'
    '\tfaceplayer\n'
    '\tmsgbox Town_Text_1, MSGBOX_DEFAULT\n'
    '\treleaseall\n'
    '\tend\n'
    '\n'
    'Town_Good::\n'
    '\tlockall\n'
    '\tfaceplayer\n'
    '\tmsgbox Town_Text_1, MSGBOX_DEFAULT\n'
    '\trelease\n'
    '\tend\n'
    '\n'
    'Town_Trigger::\n'
    '\tlockall\n'
    '\tfaceplayer\n'
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
    (d / 'text.inc').write_text(
        'Town_Text_1::\n\t.string "Hi.$"\n', encoding='utf-8')

    class StubMW:
        def log_message(self, *a, **k):
            pass

        def isWindowModified(self):
            return False

    t = eet.EventEditorTab(StubMW())
    t._root_dir = tmp_path
    t._load_map(d)
    return t


def _show(tab, script):
    for i, o in enumerate(tab._objects):
        if o.get('script') == script:
            tab._on_object_changed(i)
            return
    raise AssertionError(f'{script} not on the test map')


def _heads(tab):
    return [str(c[0]) for c in tab._cmd_tuples]


# ── The rule itself ─────────────────────────────────────────────────────────

@pytest.mark.parametrize('cmds, frozen', [
    ([('lockall',), ('faceplayer',), ('releaseall',), ('end',)], True),
    ([('lock',), ('faceplayer',), ('releaseall',), ('end',)], True),
    ([('lockall',), ('faceplayer',), ('release',), ('end',)], False),
    ([('lockall',), ('message', 'T', 'hi', 'MSGBOX_DEFAULT'),
      ('releaseall',), ('end',)], False),          # no Face Player: fine
    # One branch letting go with Release All is still a frozen NPC.
    ([('lockall',), ('faceplayer',), ('release',), ('releaseall',)], True),
    ([], False),
    (None, False),
])
def test_rule(cmds, frozen):
    assert eet.release_all_leaves_npc_frozen(cmds) is frozen


# ── In the editor ───────────────────────────────────────────────────────────

def test_the_warning_shows_for_the_carpenter_case(tab):
    _show(tab, 'Town_Carpenter')
    assert _heads(tab)[:2] == ['lockall', 'faceplayer']
    assert tab.release_warn_lbl.text() == eet.RELEASE_ALL_WARNING


def test_no_warning_when_the_page_uses_release(tab):
    _show(tab, 'Town_Good')
    assert tab.release_warn_lbl.text() == ''


def test_swapping_to_release_clears_it_live(tab):
    _show(tab, 'Town_Carpenter')
    i = _heads(tab).index('releaseall')
    cmds = list(tab._cmd_tuples)
    cmds[i] = ('release',)
    tab._rebuild_cmd_list(cmds)
    assert tab.release_warn_lbl.text() == ''


def test_deleting_face_player_clears_it(tab):
    _show(tab, 'Town_Carpenter')
    tab._cmd_list.clearSelection()
    tab._cmd_list.setCurrentRow(_heads(tab).index('faceplayer'))
    tab._on_del_command()
    assert 'faceplayer' not in _heads(tab)
    assert tab.release_warn_lbl.text() == ''


def test_it_follows_the_selected_event(tab):
    _show(tab, 'Town_Carpenter')
    assert tab.release_warn_lbl.text()
    _show(tab, 'Town_Good')
    assert tab.release_warn_lbl.text() == ''
    _show(tab, 'Town_Carpenter')
    assert tab.release_warn_lbl.text()


def test_only_people_get_it_not_triggers(tab):
    for i, o in enumerate(tab._objects):
        if o.get('_event_type') == 'coord':
            tab._on_object_changed(i)
            break
    else:
        raise AssertionError('no trigger on the test map')
    assert 'releaseall' in _heads(tab) and 'faceplayer' in _heads(tab)
    assert tab.release_warn_lbl.text() == ''


def test_the_warning_is_quiet(tab):
    """Small amber text in the properties form: no dialog, no row colour."""
    ss = tab.release_warn_lbl.styleSheet()
    assert 'font-size:10px' in ss and '#d9a441' in ss


# ── Names ───────────────────────────────────────────────────────────────────

def test_release_is_not_called_release_player():
    w = eet._ReleaseWidget('release')
    assert w.friendly_name() == 'Release'
    assert eet._ReleaseWidget('releaseall').friendly_name() == 'Release All'
    pairs = [p for _, grp in eet._PAGE_1_COMMANDS for p in grp
             if p[1] in ('release', 'releaseall')]
    assert ('Release', 'release') in pairs
    assert ('Release All', 'releaseall') in pairs
    import inspect
    assert 'Release Player' not in inspect.getsource(eet)


# ── EVENTide's own Cutscene Talker template ─────────────────────────────────
# It used to write lockall / faceplayer / message / releaseall, which is the
# exact pattern that freezes the NPC. Owner, 2026-11-18: "Yes, change it".

def test_cutscene_talker_template_ends_with_release(tab):
    _show(tab, 'Town_Good')
    idx = tab._current_obj_idx
    obj = tab._objects[idx]
    tab._create_npc_cutscene_talker(obj, 'Town_NewCutscene')
    cmds = obj['_pages'][0]['commands']
    heads = [str(c[0]) for c in cmds]
    assert heads[:2] == ['lockall', 'faceplayer']   # still freezes everyone
    assert heads[-1] == 'release'
    assert 'releaseall' not in heads
    assert not eet.release_all_leaves_npc_frozen(cmds)
    tab._display_object(idx)
    assert tab.release_warn_lbl.text() == ''
