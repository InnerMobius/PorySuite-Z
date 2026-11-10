"""Delete Event has to finish, and has to stop offering what it deleted.

`_on_delete_event` ended with a block reading `old_label` and `new_label` —
names from the RENAME path, which do not exist in it. Nothing in the function
ever set them, so it could never have worked: every Delete Event removed the
event from the map, removed the row from the dropdown, scheduled the script
block for removal, logged it… and then raised

    NameError: name 'old_label' is not defined

on its way out. The deletion stood and the exception surfaced on top of it, so
the state was half-applied every time, and the one thing those lines were meant
to do — take the dead label out of the module-level list that fills every
goto / call dropdown — never happened. A deleted script stayed on offer, and
picking it writes a jump to a label that is about to stop existing.

The guard here is deliberately the plainest possible: delete an event and
require that the call RETURNS. An exception after the mutation is worse than
one before it, because the user is left with a changed map and a crash.
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
        {'graphics_id': 'OBJ_EVENT_GFX_MAN', 'x': 11, 'y': 18, 'elevation': 3,
         'local_id': 'LOCALID_WOODS_POSTMAN', 'flag': '0',
         'movement_type': 'MOVEMENT_TYPE_NONE',
         'script': 'Woods_EventScript_Postman'},
        {'graphics_id': 'OBJ_EVENT_GFX_WOMAN', 'x': 4, 'y': 7, 'elevation': 3,
         'local_id': 'LOCALID_WOODS_LADY', 'flag': '0',
         'movement_type': 'MOVEMENT_TYPE_NONE',
         'script': 'Woods_EventScript_Lady'},
    ],
    'coord_events': [],
    'bg_events': [],
}

SCRIPTS = """\
Woods_MapScripts::
\t.byte 0

Woods_EventScript_Postman::
\tmsgbox Woods_Text_1, MSGBOX_DEFAULT
\tend

Woods_EventScript_Lady::
\tmsgbox Woods_Text_2, MSGBOX_DEFAULT
\tend
"""

TEXTS = """\
Woods_Text_1:
\t.string "Hello.$"

Woods_Text_2:
\t.string "Hi.$"
"""


@pytest.fixture
def tab(qapp, tmp_path, monkeypatch):
    for name in ('information', 'warning', 'critical'):
        monkeypatch.setattr(eet.QMessageBox, name,
                            staticmethod(lambda *a, **k: None), raising=False)
    # The user says yes to the confirmation — otherwise nothing under test runs.
    monkeypatch.setattr(
        eet.QMessageBox, 'question',
        staticmethod(lambda *a, **k: eet.QMessageBox.StandardButton.Yes))

    class _StubMW:
        # `project_dir` is not decoration. The save path asks the main window
        # where the project is so it can check whether anything still points
        # at a label before stripping its block (finding 0j), and a window
        # that cannot say keeps every block — which is the right call, and
        # would quietly turn the disk assertions below into no-ops.
        project_dir = str(tmp_path)

        def __init__(self):
            self.logs = []

        def log_message(self, msg, *a, **k):
            self.logs.append(msg)

        def isWindowModified(self):
            return False

    d = tmp_path / 'data' / 'maps' / 'Woods'
    d.mkdir(parents=True)
    (d / 'map.json').write_text(json.dumps(MAP), encoding='utf-8')
    (d / 'scripts.inc').write_text(SCRIPTS, encoding='utf-8')
    (d / 'text.inc').write_text(TEXTS, encoding='utf-8')

    t = eet.EventEditorTab(_StubMW())
    t._root_dir = tmp_path
    t._load_map(d)
    return t


def _select(tab, label: str) -> int:
    idx = next(i for i, o in enumerate(tab._objects)
               if o.get('script') == label)
    tab._current_obj_idx = idx
    return idx


# ── it finishes ──────────────────────────────────────────────────────────

def test_deleting_an_event_does_not_raise(tab):
    """THE regression. Before the fix this raised NameError every time."""
    _select(tab, 'Woods_EventScript_Postman')
    tab._on_delete_event()   # must simply return


def test_the_event_is_gone(tab):
    _select(tab, 'Woods_EventScript_Postman')
    tab._on_delete_event()
    assert all(o.get('script') != 'Woods_EventScript_Postman'
               for o in tab._objects)


def test_the_other_event_is_untouched(tab):
    _select(tab, 'Woods_EventScript_Postman')
    tab._on_delete_event()
    assert any(o.get('script') == 'Woods_EventScript_Lady'
               for o in tab._objects)


def test_deleting_two_in_a_row_works(tab):
    """A half-applied first delete used to leave the editor in a state the
    second one could not be driven from."""
    _select(tab, 'Woods_EventScript_Postman')
    tab._on_delete_event()
    _select(tab, 'Woods_EventScript_Lady')
    tab._on_delete_event()
    assert [o for o in tab._objects if o.get('_event_type') == 'object'] == []


# ── and stops offering what it deleted ───────────────────────────────────

def test_the_deleted_label_leaves_the_dropdown_list(tab):
    """What the broken block was for.

    `_SCRIPT_LABELS` fills every goto / call picker. Leaving a deleted script
    in it offers the user a jump to a label that is about to stop existing,
    and the project then does not link.
    """
    assert 'Woods_EventScript_Postman' in eet._SCRIPT_LABELS
    _select(tab, 'Woods_EventScript_Postman')
    tab._on_delete_event()
    assert 'Woods_EventScript_Postman' not in eet._SCRIPT_LABELS


def test_a_label_still_used_elsewhere_stays_on_offer(tab):
    """Shared scripts are common — several triggers goto one battle script.

    Deleting one holder must not withdraw a label the others still jump to.
    """
    shared = 'Woods_EventScript_Shared'
    for obj in tab._objects:
        if obj.get('_event_type') == 'object':
            obj['_pages'] = [{'commands': [('end',)], '_label': shared,
                              '_conditions': []}]
    tab._register_labels([shared])
    _select(tab, 'Woods_EventScript_Postman')
    tab._on_delete_event()
    assert shared in eet._SCRIPT_LABELS


def test_the_surviving_events_labels_are_still_on_offer(tab):
    _select(tab, 'Woods_EventScript_Postman')
    tab._on_delete_event()
    assert 'Woods_EventScript_Lady' in eet._SCRIPT_LABELS


# ── the deletion still reaches disk ──────────────────────────────────────

def test_the_entry_script_block_is_kept_as_an_orphan(tab, tmp_path):
    """Documenting what actually happens, not what you would guess.

    Finding 0j made the save refuse to strip a label anything on disk still
    points at: an orphaned block is harmless, an undefined reference stops the
    project linking. The check runs BEFORE map.json is rewritten, so the copy
    on disk still lists the event being deleted — its own script therefore
    looks referenced and is kept every time.

    So a deleted NPC's script block stays in scripts.inc, unreferenced. That
    is the safe direction of the two, and the editor says so in its log rather
    than leaving it silent. It is recorded in BUGS.md as an open question
    (whether the 0j check belongs after the map.json write) and deliberately
    NOT changed here, because getting it wrong re-opens a link failure.
    """
    _select(tab, 'Woods_EventScript_Postman')
    tab._on_delete_event()
    tab._on_save()
    text = (tmp_path / 'data' / 'maps' / 'Woods'
            / 'scripts.inc').read_text(encoding='utf-8')
    assert 'Woods_EventScript_Postman::' in text
    assert 'Woods_EventScript_Lady::' in text
    assert any('still referenced' in m for m in tab._mw.logs), (
        'the user was not told the block was kept')


def test_the_event_is_dropped_from_map_json_on_save(tab, tmp_path):
    _select(tab, 'Woods_EventScript_Postman')
    tab._on_delete_event()
    tab._on_save()
    data = json.loads((tmp_path / 'data' / 'maps' / 'Woods'
                       / 'map.json').read_text(encoding='utf-8'))
    scripts = [o.get('script') for o in data['object_events']]
    assert 'Woods_EventScript_Postman' not in scripts
    assert 'Woods_EventScript_Lady' in scripts
