"""Aiming a command at a character on the map.

A move route, a "face player", a graphic change — all of them have to name
WHICH character they act on, and the thing a script wants there is the object's
local id. The Event Editor offers a dropdown of the map's characters so nobody
has to know those numbers.

It was offering nothing. The dropdown held its two fixed entries, "this event"
and "the player", and not one of the map's actual characters however many it
had, because the code asked each object for a `local_id` field that a map.json
does not carry: the id is the object's POSITION in the list, which the build
writes out as `object_event 1`, `object_event 2` and so on.

With nothing to pick from, aiming a move route at a character meant typing
something into the box, and the name that looks right — `OBJ_EVENT_GFX_MAN` —
is the GRAPHICS constant, not an object id. On a map with eight objects that
addresses object 25, so the character simply never moved, with nothing to say
why.

Everything here runs against a map tree invented in a temp directory.
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


# The shape of a real outdoor map: a run of scenery, then the characters that
# matter. The man is LAST, so a bug that stops at the first object or mishandles
# ordering shows up here rather than passing by luck.
OBJECTS = [
    {'graphics_id': 'OBJ_EVENT_GFX_CUT_TREE', 'x': 21, 'y': 6, 'script': 'NULL'},
    {'graphics_id': 'OBJ_EVENT_GFX_CUT_TREE', 'x': 20, 'y': 7, 'script': 'NULL'},
    {'graphics_id': 'OBJ_EVENT_GFX_CUT_TREE', 'x': 40, 'y': 25, 'script': 'NULL'},
    {'graphics_id': 'OBJ_EVENT_GFX_CUT_TREE', 'x': 39, 'y': 26, 'script': 'NULL'},
    {'graphics_id': 'OBJ_EVENT_GFX_CUT_TREE', 'x': 39, 'y': 29, 'script': 'NULL'},
    {'graphics_id': 'OBJ_EVENT_GFX_CUT_TREE', 'x': 40, 'y': 30, 'script': 'NULL'},
    {'graphics_id': 'OBJ_EVENT_GFX_ITEM_BALL', 'x': 8, 'y': 6,
     'script': 'Woods_EventScript_ItemBall1'},
    {'graphics_id': 'OBJ_EVENT_GFX_MAN', 'x': 11, 'y': 18,
     'script': 'Woods_EventScript_Postman'},
]


def _write_map(root, name, objects):
    d = root / 'data' / 'maps' / name
    d.mkdir(parents=True)
    (d / 'map.json').write_text(
        json.dumps({'name': name, 'object_events': objects}), encoding='utf-8')
    (d / 'scripts.inc').write_text('', encoding='utf-8')
    (d / 'text.inc').write_text('', encoding='utf-8')
    return d


@pytest.fixture
def loaded(qapp, tmp_path, monkeypatch):
    """An Event Editor with a map open, returning its object choices."""
    for name in ('information', 'warning', 'critical', 'question'):
        monkeypatch.setattr(eet.QMessageBox, name,
                            staticmethod(lambda *a, **k: None), raising=False)

    class _StubMW:
        def log_message(self, *a, **k):
            pass

        def isWindowModified(self):
            return False

    def load(objects):
        map_dir = _write_map(tmp_path, 'Woods', objects)
        tab = eet.EventEditorTab(_StubMW())
        tab._root_dir = tmp_path
        tab._load_map(map_dir)
        return list(eet._OBJECT_CHOICES or [])

    return load


def test_every_character_on_the_map_is_offered(loaded):
    choices = loaded(OBJECTS)
    assert len(choices) == len(OBJECTS) + 2, (
        'expected the two fixed entries plus one per object, got '
        f'{[label for _, label in choices]}')


def test_the_fixed_entries_stay_first(loaded):
    choices = loaded(OBJECTS)
    assert [raw for raw, _ in choices[:2]] == ['0', 'LOCALID_PLAYER']


def test_local_id_is_the_position_in_the_list(loaded):
    """What the build writes as `object_event N` is what a script must name."""
    ids = [raw for raw, _ in loaded(OBJECTS)[2:]]
    assert ids == ['1', '2', '3', '4', '5', '6', '7', '8']


def test_the_last_character_gets_the_last_id(loaded):
    """The man is object 8 of 8; a move route aimed at him must say 8."""
    choices = loaded(OBJECTS)
    man = [raw for raw, label in choices if 'Man' in label]
    assert man == ['8']


def test_characters_are_named_by_their_sprite_not_their_number(loaded):
    labels = [label for _, label in loaded(OBJECTS)]
    assert 'Object 8 — Man' in labels
    assert 'Object 7 — Item Ball' in labels


def test_a_fork_that_does_store_local_id_is_honoured(loaded):
    """Some forks write the id out explicitly; believe it over the position."""
    objects = [dict(o) for o in OBJECTS]
    objects[7]['local_id'] = 42
    ids = [raw for raw, _ in loaded(objects)[2:]]
    assert ids[-1] == '42'
    assert ids[:-1] == ['1', '2', '3', '4', '5', '6', '7']


def test_a_map_with_no_characters_still_offers_the_fixed_entries(loaded):
    assert [raw for raw, _ in loaded([])] == ['0', 'LOCALID_PLAYER']


def test_the_picker_hands_back_the_raw_id_a_script_needs(loaded, qapp):
    """Choosing "Object 8 — Man" must write 8, not the label and not a sprite."""
    loaded(OBJECTS)
    picker = eet._ObjectPicker('8')
    assert picker.selected_object() == '8'
    assert 'Man' in picker.currentText()


def test_an_id_typed_by_hand_is_preserved_not_silently_changed(loaded, qapp):
    """An id from another map, or a constant, is still written back as typed —
    including the wrong one somebody reached for when the list was empty."""
    loaded(OBJECTS)
    picker = eet._ObjectPicker('OBJ_EVENT_GFX_MAN')
    assert picker.selected_object() == 'OBJ_EVENT_GFX_MAN'
