"""Templates carry EVENT PROPERTIES, not just script commands.

THE GAP (reported 2026-10-25). A chest built from a saved template reappeared
every time the player re-entered the map. The template reproduced the page's
commands perfectly, but an event is more than its page: the "Hide when flag"
property is what stops a looted chest coming back, and it lives on the event in
``map.json``, not in any script. A commands-only template could not express it.

Chests make this sharp because their script never calls ``setflag`` -- the
engine sets the object's flag itself inside ``removeobject`` -- so the old
"borrow the flag from a setflag command" path had nothing to borrow and left
the field at ``0`` (meaning no flag at all).

WHAT IS GUARDED HERE:
  * A template may carry a whitelisted set of event properties; anything else
    is dropped rather than written into the user's map.json.
  * ``$UNUSED_FLAG$`` resolves in PROPERTIES as well as in commands, and both
    get the SAME flag within one apply.
  * Every apply picks a DIFFERENT flag, including before anything is saved --
    otherwise two chests share a flag and vanish together.
  * A flag already assigned is never overwritten (that would orphan it).
  * The old bare-list template format still loads, still applies, and is still
    written back as a bare list -- saving one template must not reformat a
    user's whole store.
  * The assigned flag survives a save and reopening the map.
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
        {"type": "object", "graphics_id": "OBJ_EVENT_GFX_ITEM_BALL",
         "x": 8, "y": 4, "elevation": 0,
         "movement_type": "MOVEMENT_TYPE_NONE",
         "movement_range_x": 0, "movement_range_y": 0,
         "trainer_type": "TRAINER_TYPE_NONE",
         "trainer_sight_or_berry_tree_id": "0",
         "script": "Cave_EventScript_ItemBall1", "flag": "0"},
        {"type": "object", "graphics_id": "OBJ_EVENT_GFX_ITEM_BALL",
         "x": 2, "y": 9, "elevation": 0,
         "movement_type": "MOVEMENT_TYPE_NONE",
         "movement_range_x": 0, "movement_range_y": 0,
         "trainer_type": "TRAINER_TYPE_NONE",
         "trainer_sight_or_berry_tree_id": "0",
         "script": "Cave_EventScript_ItemBall2", "flag": "0"},
    ],
    "warp_events": [], "coord_events": [], "bg_events": [],
}

SCRIPT = """Cave_MapScripts::
.byte 0


Cave_EventScript_ItemBall1::
end


Cave_EventScript_ItemBall2::
end
"""

# A chest page: no setflag anywhere, which is the whole point.
CHEST_CMDS = [
    ["setvar", "VAR_0x8004, REGITEM_SMALL_KEY"],
    ["call", "Std_ChestOpen"],
    ["message", None, "You found a Small Key!$", "MSGBOX_DEFAULT"],
    ["call", "Std_ChestClose"],
    ["end"],
]

STORE = {
    # Legacy shape: a bare list, exactly as every pre-properties template
    # was written. Must keep working and keep its shape on disk.
    "Old Style": [["lockall"], ["releaseall"], ["end"]],
    # New shape.
    "Chest": {
        "commands": CHEST_CMDS,
        "properties": {"graphics_id": "OBJ_EVENT_GFX_ITEM_BALL",
                       "flag": "$UNUSED_FLAG$"},
    },
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
    # A small pool of spare flags to hand out.
    monkeypatch.setattr(
        ConstantsManager, 'FLAGS',
        [f'FLAG_UNUSED_0x{n:03X}' for n in range(0x4A0, 0x4B0)] + ['FLAG_HIDE_X'],
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
    return t, tmp_path, d


def _obj(t, script):
    for o in t._objects:
        if o.get('script') == script:
            return o
    raise AssertionError(f'no object with script {script}')


# ── format ──────────────────────────────────────────────────────────

def test_both_on_disk_shapes_load(tab):
    t, _, _ = tab
    tpl = t._load_page_templates()
    assert set(tpl) == {'Old Style', 'Chest'}
    assert tpl['Old Style']['commands'][0] == ('lockall',)
    assert tpl['Old Style']['properties'] == {}
    assert tpl['Chest']['properties']['flag'] == '$UNUSED_FLAG$'


def test_saving_keeps_a_commands_only_template_as_a_bare_list(tab):
    """Saving must not reformat the user's existing templates."""
    t, root, _ = tab
    t._save_page_templates(t._load_page_templates())
    raw = json.loads(
        (root / 'eventide_page_templates.json').read_text(encoding='utf-8'))
    assert isinstance(raw['Old Style'], list)
    assert isinstance(raw['Chest'], dict)
    assert raw['Chest']['properties']['flag'] == '$UNUSED_FLAG$'


def test_unknown_properties_are_dropped(tab, root_store=None):
    """A template must not be able to inject arbitrary keys into map.json."""
    t, root, _ = tab
    store = json.loads(
        (root / 'eventide_page_templates.json').read_text(encoding='utf-8'))
    store['Chest']['properties']['x'] = 99
    store['Chest']['properties']['nonsense'] = 'boom'
    (root / 'eventide_page_templates.json').write_text(
        json.dumps(store), encoding='utf-8')
    props = t._load_page_templates()['Chest']['properties']
    assert 'x' not in props and 'nonsense' not in props
    assert 'flag' in props


# ── applying ────────────────────────────────────────────────────────

def test_apply_sets_the_events_hide_when_flag(tab):
    """The reported bug: the chest came back because this stayed at 0."""
    t, _, _ = tab
    obj = _obj(t, 'Cave_EventScript_ItemBall1')
    assert obj['flag'] == '0'

    t._apply_page_template(obj, 'Cave_EventScript_ItemBall1', 'Chest')

    assert obj['flag'].startswith('FLAG_UNUSED_')
    assert obj['graphics_id'] == 'OBJ_EVENT_GFX_ITEM_BALL'
    # and the commands still arrived
    heads = [c[0] for c in obj['_pages'][0]['commands']]
    assert heads == ['setvar', 'call', 'message', 'call', 'end']


def test_each_event_gets_its_own_flag_before_any_save(tab):
    """Two chests sharing a flag would disappear together."""
    t, _, _ = tab
    a = _obj(t, 'Cave_EventScript_ItemBall1')
    b = _obj(t, 'Cave_EventScript_ItemBall2')

    t._apply_page_template(a, 'Cave_EventScript_ItemBall1', 'Chest')
    t._apply_page_template(b, 'Cave_EventScript_ItemBall2', 'Chest')

    assert a['flag'].startswith('FLAG_UNUSED_')
    assert b['flag'].startswith('FLAG_UNUSED_')
    assert a['flag'] != b['flag']


def test_an_existing_real_flag_is_never_overwritten(tab):
    """Reassigning would orphan the old flag in existing save files."""
    t, _, _ = tab
    obj = _obj(t, 'Cave_EventScript_ItemBall1')
    obj['flag'] = 'FLAG_HIDE_X'

    t._apply_page_template(obj, 'Cave_EventScript_ItemBall1', 'Chest')

    assert obj['flag'] == 'FLAG_HIDE_X'


def test_legacy_template_still_applies(tab):
    """A bare-list template keeps working exactly as before."""
    t, _, _ = tab
    obj = _obj(t, 'Cave_EventScript_ItemBall1')

    t._apply_page_template(obj, 'Cave_EventScript_ItemBall1', 'Old Style')

    heads = [c[0] for c in obj['_pages'][0]['commands']]
    assert heads == ['lockall', 'releaseall', 'end']
    assert obj['flag'] == '0'      # no properties, no setflag -> untouched


def test_flag_placeholder_resolves_to_the_same_flag_in_both_places(tab, monkeypatch):
    """A template that sets a flag AND hides behind it uses one flag."""
    t, root, _ = tab
    store = json.loads(
        (root / 'eventide_page_templates.json').read_text(encoding='utf-8'))
    store['Both'] = {
        'commands': [['setflag', '$UNUSED_FLAG$'], ['end']],
        'properties': {'flag': '$UNUSED_FLAG$'},
    }
    (root / 'eventide_page_templates.json').write_text(
        json.dumps(store), encoding='utf-8')

    obj = _obj(t, 'Cave_EventScript_ItemBall1')
    t._apply_page_template(obj, 'Cave_EventScript_ItemBall1', 'Both')

    set_flag = obj['_pages'][0]['commands'][0][1]
    assert set_flag.startswith('FLAG_UNUSED_')
    assert obj['flag'] == set_flag


# ── the flag picker ─────────────────────────────────────────────────

def test_flag_picker_sees_flags_used_as_event_properties(tab):
    """map.json holds "Hide when flag"; scanning only scripts.inc missed it."""
    t, root, d = tab
    taken = t._find_unused_flags(1)[0]
    data = json.loads((d / 'map.json').read_text(encoding='utf-8'))
    data['object_events'][0]['flag'] = taken
    (d / 'map.json').write_text(json.dumps(data), encoding='utf-8')

    assert taken not in t._find_unused_flags(5)


# ── persistence: the check that was asked for ───────────────────────

def test_flag_survives_save_and_reopening_the_map(tab):
    t, _, d = tab
    obj = _obj(t, 'Cave_EventScript_ItemBall1')
    t._current_obj_idx = t._objects.index(obj)

    t._apply_page_template(obj, 'Cave_EventScript_ItemBall1', 'Chest')
    assigned = obj['flag']
    assert assigned.startswith('FLAG_UNUSED_')

    t._on_save()

    # On disk, in the map file the game actually reads.
    on_disk = json.loads((d / 'map.json').read_text(encoding='utf-8'))
    row = [o for o in on_disk['object_events']
           if o.get('script') == 'Cave_EventScript_ItemBall1'][0]
    assert row['flag'] == assigned

    # And after reopening the map in the editor.
    t._load_map(d)
    assert _obj(t, 'Cave_EventScript_ItemBall1')['flag'] == assigned
