"""Finding 0h: a failed EVENTide save destroyed the editor's own state.

The save built map.json's event arrays by popping two keys straight off the
LIVE objects:

    obj.pop('_pages', None)
    etype = obj.pop('_event_type', 'object')

Those are the editor's bookkeeping. `_pages` holds the parsed script pages the
UI renders; `_event_type` decides which map.json array an event belongs to.
When the write afterwards failed, the live objects stayed stripped — the
editor unusable until reloaded, and a retry would have misfiled every event as
an object because its type was already gone.

DO NOT REGRESS:
  * Serialise DETACHED COPIES. Nothing in the serialisation may mutate
    `self._objects`. A failed save must cost the user the write, not the
    editor state as well.
  * local_id repairs are applied to live objects only AFTER a successful
    write. Applying them first leaves the editor carrying changes that never
    reached disk — the same defect in miniature.
  * map_script entries are synthetic and must stay out of map.json.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eventide.ui.event_editor_tab import EventEditorTab  # noqa: E402


class _Stub:
    _serialise_events_for_map_json = \
        EventEditorTab._serialise_events_for_map_json

    def __init__(self, objects):
        self._objects = objects

    @staticmethod
    def _normalize_local_id(value):
        # Mirrors the real contract: a usable identifier, or None.
        value = str(value)
        return value.upper() if value and not value[0].isdigit() else None


def _objects():
    return [
        {"script": "A", "_event_type": "object", "_pages": [{"commands": []}],
         "local_id": "npc_one", "x": 1},
        {"script": "B", "_event_type": "coord", "_pages": [{"commands": []}]},
        {"script": "C", "_event_type": "bg", "_pages": [{"commands": []}]},
        {"script": "D", "_event_type": "map_script", "_pages": []},
    ]


def test_serialising_does_not_touch_the_live_objects():
    """The reported defect, asserted directly."""
    objs = _objects()
    before = [dict(o) for o in objs]
    _Stub(objs)._serialise_events_for_map_json()
    assert objs == before, "serialisation mutated the live editor objects"


def test_the_live_objects_keep_their_pages_and_type():
    objs = _objects()
    _Stub(objs)._serialise_events_for_map_json()
    for o in objs:
        assert "_pages" in o, "_pages was stripped from a live object"
        assert "_event_type" in o, "_event_type was stripped from a live object"


def test_the_written_records_exclude_the_bookkeeping_keys():
    objs = _objects()
    obj_ev, coord_ev, bg_ev, _ids = _Stub(objs)._serialise_events_for_map_json()
    for record in obj_ev + coord_ev + bg_ev:
        assert "_pages" not in record
        assert "_event_type" not in record


def test_events_land_in_the_right_arrays():
    objs = _objects()
    obj_ev, coord_ev, bg_ev, _ids = _Stub(objs)._serialise_events_for_map_json()
    assert [r["script"] for r in obj_ev] == ["A"]
    assert [r["script"] for r in coord_ev] == ["B"]
    assert [r["script"] for r in bg_ev] == ["C"]


def test_map_script_entries_are_not_written_to_map_json():
    objs = _objects()
    obj_ev, coord_ev, bg_ev, _ids = _Stub(objs)._serialise_events_for_map_json()
    written = [r["script"] for r in obj_ev + coord_ev + bg_ev]
    assert "D" not in written


def test_local_id_is_normalised_in_the_record_but_not_yet_live():
    objs = _objects()
    obj_ev, _c, _b, ids = _Stub(objs)._serialise_events_for_map_json()
    assert obj_ev[0]["local_id"] == "NPC_ONE"
    # the live object still carries the original until the write succeeds
    assert objs[0]["local_id"] == "npc_one"
    assert ids == [(objs[0], "NPC_ONE")]


def test_an_unusable_local_id_is_dropped_from_the_record_only():
    objs = [{"script": "A", "_event_type": "object", "local_id": "001"}]
    obj_ev, _c, _b, ids = _Stub(objs)._serialise_events_for_map_json()
    assert "local_id" not in obj_ev[0]
    assert objs[0]["local_id"] == "001"
    assert ids == [(objs[0], None)]


def test_the_save_path_uses_the_detached_serialiser():
    """And it must be wired, not merely available."""
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(
        sys.modules[EventEditorTab.__module__]))
    # no live-object pop of the bookkeeping keys anywhere
    offenders = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "pop"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value in ("_pages", "_event_type")):
            offenders.append(node.lineno)
    assert not offenders, (
        "a live object's bookkeeping is still popped at line(s) %s" % offenders)
