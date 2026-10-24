"""Regression guard: EVENTide must not save over a file something else changed.

THE BUG (found 2026-10-24, reproduced headlessly before the fix). The Event Editor
parses a whole map into memory and rewrites it wholesale on Save. If anything else
touched those files in the meantime, Save wrote the editor's older copy straight over
it and the other change was gone, with no warning and nothing in the log.

Reproduced exactly: load a map, have something else rewrite `scripts.inc` (the case
that surfaced it was Tools > Repair Map Script Damage fixing a `msgbox` back to a
`message`), then press Save in EVENTide -- the repair was silently undone.

It is not only the repair tool. Porymap saves, a git pull or checkout, a text editor,
or a second PorySuite window all hit the same path.

WHY THE EXISTING FILE WATCHER IS NOT THE FIX, and must not be trusted as one:
  * A watcher is a best-effort notification. It can be missed while the app is
    backgrounded, on a network or VM filesystem, or when two writes land inside its
    debounce window.
  * The watcher's own "External Changes Detected" prompt offers *Save*, which calls
    straight into `_on_save()` -- so the notification path led back to the clobber.
  * Nothing guarantees the watcher is running at all for a given project.

THE RULE: `_load_map` and `_resolve_external_scripts` record a content hash of every
file they READ, and `_on_save` re-checks those hashes before any writer runs. The
check is based on what was actually read, not on having been told, and it sits at the
point of writing where it cannot be bypassed.

Content hash, not mtime+size: a same-size edit inside one filesystem timestamp tick is
precisely what a staleness check must not miss.

If you touch `_on_save`, `_load_map`, `_changed_on_disk` or `_remember_disk_state`,
keep this green.
"""

import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, 'core'), os.path.join(_ROOT, 'ui')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

pytest.importorskip('PyQt6.QtWidgets', reason='needs PyQt6')

from PyQt6.QtWidgets import QApplication  # noqa: E402

from eventide.backend.eventide_utils import file_fingerprint  # noqa: E402


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


MAP_JSON = {
    "id": "MAP_TOWN", "name": "Town", "layout": "LAYOUT_TOWN",
    "music": "MUS_PALLET", "region_map_section": "MAPSEC_PALLET_TOWN",
    "requires_flash": False, "weather": "WEATHER_NONE",
    "map_type": "MAP_TYPE_TOWN", "allow_cycling": True,
    "allow_escaping": False, "allow_running": True, "show_map_name": True,
    "floor_number": 0, "battle_scene": "MAP_BATTLE_SCENE_NORMAL",
    "connections": [], "warp_events": [], "coord_events": [], "bg_events": [],
    "object_events": [{
        "graphics_id": "OBJ_EVENT_GFX_BOY", "x": 5, "y": 5, "elevation": 3,
        "movement_type": "MOVEMENT_TYPE_FACE_DOWN", "movement_range_x": 0,
        "movement_range_y": 0, "trainer_type": "TRAINER_TYPE_NONE",
        "trainer_sight_or_berry_tree_id": "0",
        "script": "Town_EventScript_Boy", "flag": "0",
        "local_id": "LOCALID_TOWN_BOY"}],
}

ORIGINAL = ('Town_EventScript_Boy::\n\tlock\n\tfaceplayer\n'
            '\tmsgbox Town_Text_Hi\n\trelease\n\tend\n')
# What a repair (or Porymap, or the user in an editor) would make it.
EXTERNAL = ('Town_EventScript_Boy::\n\tlock\n\tfaceplayer\n'
            '\tmessage Town_Text_Hi\n\twaitmessage\n\trelease\n\tend\n')


class _StubMainWindow:
    """Just enough main window for the tab, with no dialogs."""

    def __init__(self):
        self.modified = False
        self.messages = []

    def log_message(self, msg):
        self.messages.append(msg)

    def isWindowModified(self):
        return self.modified

    def setWindowModified(self, value):
        self.modified = value


@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch):
    """Stop any QMessageBox opening for real.

    Without this a test that reaches the full save path HANGS on the success
    dialog instead of failing, which is the worst outcome for a regression
    guard — a hang reads as an infrastructure problem, not as the bug.
    """
    import eventide.ui.event_editor_tab as mod
    for name in ('information', 'warning', 'critical', 'question'):
        monkeypatch.setattr(mod.QMessageBox, name,
                            staticmethod(lambda *a, **k: None), raising=False)


@pytest.fixture
def tab(tmp_path, qapp):
    """A loaded Event Editor tab over a throwaway one-map project."""
    from eventide.ui.event_editor_tab import EventEditorTab

    d = tmp_path / 'data' / 'maps' / 'Town'
    d.mkdir(parents=True)
    (d / 'map.json').write_text(json.dumps(MAP_JSON), encoding='utf-8')
    (d / 'scripts.inc').write_text(ORIGINAL, encoding='utf-8')
    (d / 'text.inc').write_text('Town_Text_Hi::\n\t.string "Hi$"\n',
                                encoding='utf-8')

    t = EventEditorTab(_StubMainWindow())
    t._root_dir = tmp_path
    t._load_map(d)
    # Never let a real dialog open during a test; each test sets the answer.
    t._confirm_overwrite_external = lambda stale: False
    t._answered = None
    return t


def _scripts(tmp_path):
    return (tmp_path / 'data' / 'maps' / 'Town'
            / 'scripts.inc').read_text(encoding='utf-8')


# ─────────────────────────────────────────────────────────────────────────────
# The fingerprint itself
# ─────────────────────────────────────────────────────────────────────────────

def test_fingerprint_is_content_not_timestamp(tmp_path):
    """A same-SIZE edit must register. mtime+size could miss this one."""
    p = tmp_path / 'f.inc'
    p.write_text('msgbox A\n', encoding='utf-8')
    before = file_fingerprint(p)
    p.write_text('msgbox B\n', encoding='utf-8')   # same length
    assert file_fingerprint(p) != before


def test_fingerprint_of_a_missing_file_is_none(tmp_path):
    assert file_fingerprint(tmp_path / 'nope.inc') is None


def test_deleting_a_file_counts_as_changed(tmp_path):
    p = tmp_path / 'f.inc'
    p.write_text('x\n', encoding='utf-8')
    before = file_fingerprint(p)
    p.unlink()
    assert file_fingerprint(p) != before


# ─────────────────────────────────────────────────────────────────────────────
# Detection
# ─────────────────────────────────────────────────────────────────────────────

def test_load_records_every_file_it_read(tab, tmp_path):
    names = {p.name for p in tab._disk_state}
    assert {'map.json', 'scripts.inc', 'text.inc'} <= names


def test_nothing_is_stale_straight_after_a_load(tab):
    assert tab._changed_on_disk() == []


def test_an_external_edit_is_detected(tab, tmp_path):
    (tmp_path / 'data' / 'maps' / 'Town' / 'scripts.inc').write_text(
        EXTERNAL, encoding='utf-8')
    assert [p.name for p in tab._changed_on_disk()] == ['scripts.inc']


def test_an_external_map_json_edit_is_detected(tab, tmp_path):
    """Porymap's territory — warps live in map.json, which EVENTide rewrites."""
    p = tmp_path / 'data' / 'maps' / 'Town' / 'map.json'
    data = json.loads(p.read_text(encoding='utf-8'))
    data['warp_events'] = [{'x': 20, 'y': 82, 'elevation': 0,
                            'dest_map': 'MAP_TOWN', 'dest_warp_id': '0'}]
    p.write_text(json.dumps(data), encoding='utf-8')
    assert [q.name for q in tab._changed_on_disk()] == ['map.json']


# ─────────────────────────────────────────────────────────────────────────────
# The actual bug: Save must not clobber
# ─────────────────────────────────────────────────────────────────────────────

def test_save_does_not_clobber_an_external_edit(tab, tmp_path):
    """THE regression. Before the fix this assertion failed."""
    (tmp_path / 'data' / 'maps' / 'Town' / 'scripts.inc').write_text(
        EXTERNAL, encoding='utf-8')
    tab._confirm_overwrite_external = lambda stale: False   # user cancels
    tab._on_save()
    assert _scripts(tmp_path) == EXTERNAL


def test_save_asks_before_overwriting(tab, tmp_path):
    asked = []
    (tmp_path / 'data' / 'maps' / 'Town' / 'scripts.inc').write_text(
        EXTERNAL, encoding='utf-8')
    tab._confirm_overwrite_external = lambda stale: asked.append(stale) or False
    tab._on_save()
    assert len(asked) == 1
    assert [p.name for p in asked[0]] == ['scripts.inc']


def test_save_anyway_still_works_when_the_user_insists(tab, tmp_path):
    """The guard must be answerable, not a wall."""
    (tmp_path / 'data' / 'maps' / 'Town' / 'scripts.inc').write_text(
        EXTERNAL, encoding='utf-8')
    tab._confirm_overwrite_external = lambda stale: True
    tab._on_save()
    assert 'message Town_Text_Hi' not in _scripts(tmp_path)


def test_an_untouched_save_is_not_blocked(tab, tmp_path):
    """No external change means no prompt — the guard must stay out of the way."""
    asked = []
    tab._confirm_overwrite_external = lambda stale: asked.append(stale) or False
    tab._on_save()
    assert asked == []


# ─────────────────────────────────────────────────────────────────────────────
# The guard must not get in the way of the Porymap integration
#
# PorySuite and Porymap are meant to work as one app: save in Porymap and the
# editor just goes with it. The watcher calls reload_current_map(), which with
# no unsaved edits reloads silently. These pin that the guard stays out of it.
# ─────────────────────────────────────────────────────────────────────────────

def test_a_porymap_save_is_absorbed_silently(tab, tmp_path, monkeypatch):
    """No unsaved edits: the editor takes the change with no dialog at all."""
    seen = []
    import eventide.ui.event_editor_tab as mod
    for name in ('information', 'warning', 'critical', 'question'):
        monkeypatch.setattr(mod.QMessageBox, name,
                            staticmethod(lambda *a, _n=name, **k: seen.append(_n)))
    (tmp_path / 'data' / 'maps' / 'Town' / 'scripts.inc').write_text(
        EXTERNAL, encoding='utf-8')

    tab._mw.modified = False
    tab.reload_current_map()            # what the file watcher calls

    assert seen == []                             # nothing interrupted the user
    assert tab._changed_on_disk() == []           # fingerprint refreshed
    assert _scripts(tmp_path) == EXTERNAL         # the outside change survived


def test_saving_after_an_absorbed_change_does_not_prompt(tab, tmp_path):
    """And the next Save must be clean — a refreshed snapshot means no warning."""
    (tmp_path / 'data' / 'maps' / 'Town' / 'scripts.inc').write_text(
        EXTERNAL, encoding='utf-8')
    tab._mw.modified = False
    tab.reload_current_map()

    asked = []
    tab._confirm_overwrite_external = lambda stale: asked.append(stale) or False
    tab._on_save()
    assert asked == []


def test_external_prompt_does_not_ask_twice(tab, tmp_path, monkeypatch):
    """With unsaved edits, answering 'Save' must not raise a SECOND dialog.

    The watcher's own prompt already asked the question. Asking again in
    _on_save would be the same decision twice, and its default is Cancel —
    so an Enter press would drop the save the user just asked for.
    """
    import eventide.ui.event_editor_tab as mod
    (tmp_path / 'data' / 'maps' / 'Town' / 'scripts.inc').write_text(
        EXTERNAL, encoding='utf-8')
    tab._mw.modified = True
    monkeypatch.setattr(
        mod.QMessageBox, 'question',
        staticmethod(lambda *a, **k: mod.QMessageBox.StandardButton.Save))

    second = []
    tab._confirm_overwrite_external = lambda stale: second.append(stale) or False
    tab.reload_current_map()

    assert second == [], 'the user was asked the same question twice'
    # and the save actually happened rather than being silently dropped
    assert 'message Town_Text_Hi' not in _scripts(tmp_path)


def test_external_ack_is_keyword_only(tab):
    """`clicked` hands a bool to its slot; a positional ack would disable the
    check every time the Save button is pressed."""
    import inspect
    sig = inspect.signature(tab._on_save)
    assert sig.parameters['external_ack'].kind is inspect.Parameter.KEYWORD_ONLY


def test_snapshot_is_refreshed_by_a_reload(tab, tmp_path):
    """After taking the external version, Save must not keep warning."""
    d = tmp_path / 'data' / 'maps' / 'Town'
    (d / 'scripts.inc').write_text(EXTERNAL, encoding='utf-8')
    assert tab._changed_on_disk()
    tab._load_map(d)
    assert tab._changed_on_disk() == []


def test_snapshot_does_not_leak_between_maps(tab, tmp_path):
    """Opening another map must not carry the first one's files along."""
    d2 = tmp_path / 'data' / 'maps' / 'Other'
    d2.mkdir(parents=True)
    data = dict(MAP_JSON, id='MAP_OTHER', name='Other')
    (d2 / 'map.json').write_text(json.dumps(data), encoding='utf-8')
    (d2 / 'scripts.inc').write_text('Other_EventScript_A::\n\tend\n',
                                    encoding='utf-8')
    tab._load_map(d2)
    assert all('Other' in str(p.parent) for p in tab._disk_state)
