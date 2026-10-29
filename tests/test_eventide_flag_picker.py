"""The Event Editor's flag picker offers every genuinely free flag.

THE BUG (reported 2026-10-25). "Find Unused Flag" matched on the NAME
``FLAG_UNUSED_*``. Vanilla FireRed labels only nine slots that way -- and all
nine are the spare tail of the hidden-item block -- while hundreds of ordinary
free slots are named ``FLAG_0x###``. The editor therefore behaved as though the
project had almost no flags left when it had hundreds, and the few it did hand
out came from a block the engine addresses by slot number.

The picker now takes its candidates from the project-wide scan in
``var_flag_manager``: a slot is offered only when nothing in src, data, include
or asm refers to it. Two live checks stay layered on top, because the scan is a
snapshot: ``map.json`` (a flag can be an event PROPERTY, in no script at all)
and the in-memory objects (two templates applied before a save must not be
handed the same flag).
"""
import json
import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

pytest.importorskip('PyQt6.QtWidgets')

from PyQt6.QtWidgets import QApplication  # noqa: E402

import eventide.ui.event_editor_tab as eet  # noqa: E402
from eventide.backend import var_flag_manager as vf  # noqa: E402


FLAGS_H = """#ifndef GUARD_CONSTANTS_FLAGS_H
#define GUARD_CONSTANTS_FLAGS_H
#define TEMP_FLAGS_START 0x0
#define FLAG_TEMP_1      (TEMP_FLAGS_START + 0x01)
#define TEMP_FLAGS_END   0x1F
#define FLAG_0x020               0x020
#define FLAG_0x021               0x021
#define FLAG_0x022               0x022
#define FLAG_0x023               0x023
#define FLAG_ENGINE_ONLY         0x024
#define FLAG_HIDE_SHOPKEEPER     0x025
#define FLAG_HIDDEN_ITEMS_START 1000
#define FLAG_HIDDEN_ITEM_CAVE_POTION (FLAG_HIDDEN_ITEMS_START + 0)
#define FLAG_UNUSED_0x3E9        0x3E9
#define TRAINER_FLAGS_START      0x500
#define TRAINER_FLAGS_END        0x7FF
#define SYS_FLAGS                0x800
#define FLAG_BADGE01_GET         (SYS_FLAGS + 0x20)
#define FLAGS_COUNT (FLAG_BADGE01_GET + 1)
#endif
"""

MAP_JSON = {
    "object_events": [
        {"type": "object", "graphics_id": "OBJ_EVENT_GFX_ITEM_BALL",
         "x": 8, "y": 4, "elevation": 0,
         "movement_type": "MOVEMENT_TYPE_NONE",
         "movement_range_x": 0, "movement_range_y": 0,
         "trainer_type": "TRAINER_TYPE_NONE",
         "trainer_sight_or_berry_tree_id": "0",
         "script": "Cave_EventScript_ItemBall1", "flag": "0"},
    ],
    "warp_events": [], "coord_events": [], "bg_events": [],
}

SCRIPT = """Cave_MapScripts::
.byte 0


Cave_EventScript_ItemBall1::
setflag FLAG_HIDE_SHOPKEEPER
end
"""


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def tab(qapp, tmp_path, monkeypatch):
    for name in ('information', 'warning', 'critical', 'question'):
        monkeypatch.setattr(eet.QMessageBox, name,
                            staticmethod(lambda *a, **k: None), raising=False)
    vf.invalidate_cache()

    inc = tmp_path / 'include' / 'constants'
    inc.mkdir(parents=True)
    (inc / 'flags.h').write_text(FLAGS_H, encoding='utf-8')
    (inc / 'vars.h').write_text('#define VARS_START 0x4000\n', encoding='utf-8')

    src = tmp_path / 'src'
    src.mkdir()
    (src / 'engine.c').write_text(
        'void f(void) { FlagSet(FLAG_ENGINE_ONLY); }\n', encoding='utf-8')

    d = tmp_path / 'data' / 'maps' / 'Cave'
    d.mkdir(parents=True)
    (d / 'map.json').write_text(json.dumps(MAP_JSON), encoding='utf-8')
    (d / 'scripts.inc').write_text(SCRIPT, encoding='utf-8')
    (d / 'text.inc').write_text('', encoding='utf-8')

    # A chest template: its script never calls setflag, so the flag can only
    # come from the event property.
    (tmp_path / 'eventide_page_templates.json').write_text(json.dumps({
        'Chest': {
            'commands': [['call', 'Std_ChestOpen'], ['end']],
            'properties': {'graphics_id': 'OBJ_EVENT_GFX_ITEM_BALL',
                           'flag': '$UNUSED_FLAG$'},
        },
    }), encoding='utf-8')

    class StubMW:
        def log_message(self, *a, **k):
            pass

        def isWindowModified(self):
            return False

    t = eet.EventEditorTab(StubMW())
    t._root_dir = tmp_path
    t._load_map(d)
    yield t, tmp_path, d
    vf.invalidate_cache()


def test_plain_numbered_slots_are_offered(tab):
    """The widening: FLAG_0x### slots used to be invisible to the picker."""
    t, _, _ = tab
    found = t._find_unused_flags(4)
    assert found == ['FLAG_0x020', 'FLAG_0x021', 'FLAG_0x022', 'FLAG_0x023']


def test_an_engine_referenced_flag_is_never_offered(tab):
    """Scanning only data/maps, as the picker used to, would have missed this."""
    t, _, _ = tab
    assert 'FLAG_ENGINE_ONLY' not in t._find_unused_flags(100)


def test_flags_the_engine_addresses_by_number_are_never_offered(tab):
    """Hidden-item, trainer, system and temporary slots stay out of the pool."""
    t, _, _ = tab
    offered = set(t._find_unused_flags(100))
    for name in ('FLAG_HIDDEN_ITEM_CAVE_POTION', 'FLAG_TEMP_1',
                 'FLAG_BADGE01_GET'):
        assert name not in offered, name


def test_the_spare_tail_past_the_last_hidden_item_is_not_offered(tab):
    """FireRed leaves spare slots after the last named hidden item. They are
    unreferenced and would work, but they are the ground the engine grows into
    as FLAG_HIDDEN_ITEMS_START + index, so a template must not take one on its
    own. The old name-matching picker handed out nothing BUT these."""
    t, _, _ = tab
    offered = t._find_unused_flags(100)
    assert 'FLAG_UNUSED_0x3E9' not in offered
    assert offered[0] == 'FLAG_0x020'


def test_a_flag_used_only_in_a_script_is_not_offered(tab):
    t, _, _ = tab
    assert 'FLAG_HIDE_SHOPKEEPER' not in t._find_unused_flags(100)


def test_a_flag_assigned_in_memory_is_not_offered_again(tab):
    """Two templates applied before a save must not share one flag."""
    t, _, _ = tab
    first = t._find_unused_flags(1)[0]
    t._objects[0]['flag'] = first
    assert first not in t._find_unused_flags(5)


def test_a_flag_written_into_map_json_is_not_offered_again(tab):
    """The snapshot is layered with a live re-read, so a saved map counts."""
    t, _, d = tab
    taken = t._find_unused_flags(1)[0]
    data = json.loads((d / 'map.json').read_text(encoding='utf-8'))
    data['object_events'][0]['flag'] = taken
    (d / 'map.json').write_text(json.dumps(data), encoding='utf-8')
    assert taken not in t._find_unused_flags(5)


def test_a_chest_template_lands_on_a_general_region_slot(tab):
    """The point of the safe pool: applying the Chest template must not dip
    into the hidden-item block, which is all the old picker ever offered."""
    t, root, _ = tab
    obj = t._objects[0]
    assert obj['flag'] == '0'

    t._apply_page_template(obj, 'Cave_EventScript_ItemBall1', 'Chest')

    assigned = obj['flag']
    assert assigned.startswith('FLAG_0x')
    slot = vf.resolve_values(root, vf.FLAGS_HEADER)[assigned]
    regions = vf.flag_regions(vf.resolve_values(root, vf.FLAGS_HEADER))
    assert regions['general_start'] <= slot <= regions['general_end']
    assert obj['graphics_id'] == 'OBJ_EVENT_GFX_ITEM_BALL'


def test_the_pool_is_larger_than_the_old_name_match_found(tab):
    """Matching on FLAG_UNUSED_* found exactly the one slot that should never
    have been offered. The real pool is the four general-region slots."""
    t, _, _ = tab
    offered = t._find_unused_flags(1000)
    assert offered == ['FLAG_0x020', 'FLAG_0x021', 'FLAG_0x022', 'FLAG_0x023']
    assert not any(f.startswith('FLAG_UNUSED_') for f in offered)
