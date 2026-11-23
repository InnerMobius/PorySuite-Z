"""My Templates can be INSERTED into a script that is already being worked on.

THE GAP (reported 2026-11-22). A saved template could only be used through
New Script -> My Templates, which replaces the event's whole page list with the
template. There was no way to drop a template into a script mid-edit, so using
one wiped the work already on the page.

WHAT IS GUARDED HERE:
  * Insert puts the template's commands below the selected line and leaves
    every existing command where it was, in order.
  * With nothing selected it goes at the end, and keeps the template's ``end``.
  * Inserted above existing commands, a trailing ``end`` is left out (it would
    stop the script there and kill everything below it).
  * The event's other pages and its properties are untouched.
  * ``$UNUSED_FLAG$`` still becomes a real flag.
  * One Undo takes the whole insert back out.
"""
import json
import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

pytest.importorskip('PyQt6.QtWidgets')

from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

import eventide.ui.event_editor_tab as eet  # noqa: E402
from eventide.backend.constants_manager import ConstantsManager  # noqa: E402


MAP_JSON = {
    "object_events": [
        {"type": "object", "graphics_id": "OBJ_EVENT_GFX_OLD_MAN_1",
         "x": 8, "y": 4, "elevation": 0,
         "movement_type": "MOVEMENT_TYPE_NONE",
         "movement_range_x": 0, "movement_range_y": 0,
         "trainer_type": "TRAINER_TYPE_NONE",
         "trainer_sight_or_berry_tree_id": "0",
         "script": "Cave_EventScript_Man", "flag": "0"},
    ],
    "warp_events": [], "coord_events": [], "bg_events": [],
}

SCRIPT = """Cave_MapScripts::
.byte 0


Cave_EventScript_Man::
lock
faceplayer
release
end
"""

STORE = {
    "Give Potion": [["setvar", "VAR_0x8000, ITEM_POTION"],
                    ["setvar", "VAR_0x8001, 1"],
                    ["callstd", "STD_OBTAIN_ITEM"],
                    ["end"]],
    "Flagged": {"commands": [["setflag", "$UNUSED_FLAG$"], ["end"]],
                "properties": {"graphics_id": "OBJ_EVENT_GFX_ITEM_BALL",
                               "flag": "$UNUSED_FLAG$"}},
}


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def tab(qapp, tmp_path, monkeypatch):
    for name in ('information', 'warning', 'critical'):
        monkeypatch.setattr(eet.QMessageBox, name,
                            staticmethod(lambda *a, **k: None), raising=False)
    monkeypatch.setattr(
        eet.QMessageBox, 'question',
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
        raising=False)
    monkeypatch.setattr(
        ConstantsManager, 'FLAGS',
        [f'FLAG_UNUSED_0x{n:03X}' for n in range(0x4A0, 0x4B0)],
        raising=False)

    d = tmp_path / 'data' / 'maps' / 'Cave'
    d.mkdir(parents=True)
    (d / 'map.json').write_text(json.dumps(MAP_JSON), encoding='utf-8')
    (d / 'scripts.inc').write_text(SCRIPT, encoding='utf-8')
    (d / 'text.inc').write_text('', encoding='utf-8')
    (tmp_path / 'eventide_page_templates.json').write_text(
        json.dumps(STORE, indent=2), encoding='utf-8')

    class StubMW:
        def log_message(self, *a, **k):
            pass

        def isWindowModified(self):
            return False

    t = eet.EventEditorTab(StubMW())
    t._root_dir = tmp_path
    t._load_map(d)
    obj = [o for o in t._objects if o.get('script') == 'Cave_EventScript_Man'][0]
    t._display_object(t._objects.index(obj))
    return t, obj


def _heads(t):
    return [c[0] for c in t._cmd_tuples]


def test_insert_goes_below_the_selection_and_keeps_existing_work(tab):
    t, obj = tab
    assert _heads(t) == ['lock', 'faceplayer', 'release', 'end']
    t._cmd_list.setCurrentRow(1)                     # faceplayer

    t._insert_page_template('Give Potion')

    # Everything that was there survives, in order; the template sits after
    # faceplayer; its closing end is left out because release/end follow.
    assert _heads(t) == ['lock', 'faceplayer',
                         'setvar', 'setvar', 'callstd',
                         'release', 'end']
    assert t._cmd_tuples[2] == ('setvar', 'VAR_0x8000, ITEM_POTION')


def test_insert_with_nothing_selected_appends_and_keeps_end(tab):
    t, _ = tab
    t._cmd_list.setCurrentRow(-1)

    t._insert_page_template('Give Potion')

    assert _heads(t)[:4] == ['lock', 'faceplayer', 'release', 'end']
    assert _heads(t)[4:] == ['setvar', 'setvar', 'callstd', 'end']


def test_insert_leaves_pages_and_properties_alone(tab):
    t, obj = tab
    pages_before = len(obj['_pages'])
    gfx, flag = obj['graphics_id'], obj['flag']
    t._cmd_list.setCurrentRow(0)

    t._insert_page_template('Flagged')

    assert len(obj['_pages']) == pages_before
    assert obj['graphics_id'] == gfx
    assert obj['flag'] == flag
    assert t._cmd_tuples[1][0] == 'setflag'
    assert t._cmd_tuples[1][1].startswith('FLAG_UNUSED_')


def test_one_undo_removes_the_whole_insert(tab):
    t, _ = tab
    t._cmd_list.setCurrentRow(1)
    t._insert_page_template('Give Potion')
    assert len(t._cmd_tuples) == 7

    t._on_undo()

    assert _heads(t) == ['lock', 'faceplayer', 'release', 'end']


def test_insert_survives_collecting_the_page(tab):
    """What the save path reads is the page dict, not the list widget."""
    t, obj = tab
    t._cmd_list.setCurrentRow(1)
    t._insert_page_template('Give Potion')

    t._collect_current_page()

    heads = [c[0] for c in obj['_pages'][0]['commands']]
    assert heads == ['lock', 'faceplayer', 'setvar', 'setvar', 'callstd',
                     'release', 'end']


# ── Apply (New Script -> My Templates) warns before replacing work ───

def _count_questions(monkeypatch, answer):
    asked = []

    def q(*a, **k):
        asked.append(a[1] if len(a) > 1 else '')
        return answer
    monkeypatch.setattr(eet.QMessageBox, 'question', staticmethod(q),
                        raising=False)
    return asked


def test_apply_asks_first_and_no_keeps_the_script(tab, monkeypatch):
    t, obj = tab
    asked = _count_questions(monkeypatch, QMessageBox.StandardButton.No)

    t._apply_page_template(obj, 'Cave_EventScript_Man', 'Give Potion')

    assert asked == ['Replace this script?']
    heads = [c[0] for c in obj['_pages'][0]['commands']]
    assert heads == ['lock', 'faceplayer', 'release', 'end']


def test_apply_yes_still_replaces(tab, monkeypatch):
    t, obj = tab
    _count_questions(monkeypatch, QMessageBox.StandardButton.Yes)

    t._apply_page_template(obj, 'Cave_EventScript_Man', 'Give Potion')

    heads = [c[0] for c in obj['_pages'][0]['commands']]
    assert heads == ['setvar', 'setvar', 'callstd', 'end']


def test_apply_counts_unsaved_edits_on_screen(tab, monkeypatch):
    """Work typed into the list but not yet collected still counts."""
    t, obj = tab
    obj['_pages'][0]['commands'] = [('end',)]    # page dict looks empty...
    t._cmd_list.setCurrentRow(-1)
    t._insert_page_template('Give Potion')           # ...but the list is not
    asked = _count_questions(monkeypatch, QMessageBox.StandardButton.No)

    t._apply_page_template(obj, 'Cave_EventScript_Man', 'Give Potion')

    assert asked == ['Replace this script?']


def test_apply_does_not_ask_for_an_empty_script(tab, monkeypatch):
    """A just-named new script holds nothing worth a prompt."""
    t, obj = tab
    obj['_pages'] = [{'commands': [('end',)], '_label': 'Cave_EventScript_Man',
                      '_short_label': 'Man'}]
    t._display_object(t._objects.index(obj))
    asked = _count_questions(monkeypatch, QMessageBox.StandardButton.No)

    t._apply_page_template(obj, 'Cave_EventScript_Man', 'Give Potion')

    assert asked == []
    heads = [c[0] for c in obj['_pages'][0]['commands']]
    assert heads == ['setvar', 'setvar', 'callstd', 'end']
