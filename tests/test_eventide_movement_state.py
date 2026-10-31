"""Finding 0f: movement edits leaked between maps and survived F5.

`_modified_movements` maps a movement-script LABEL to its edited steps, and
those labels are not unique across maps. On save, every staged entry was
written into whichever map happened to be open:

    for mov_label, mov_steps in self._modified_movements.items():
        save_pages[mov_label] = mov_steps

So an edit made with one map open could be written into another. The dict was
also created lazily in four places and reset in none, so it survived F5 — the
action that is supposed to discard pending edits.

DO NOT REGRESS:
  * The reset belongs at the END of a load that SUCCEEDED, never on entering
    one. A load that fails partway must leave pending edits alone — they are
    still recoverable, and discarding them is a second data loss on top of
    the failure.
  * The owner check is a backstop, not the fix. Keep both: the reset handles
    the normal path, the owner check catches any future route to a save that
    does not go through `_load_map`.
  * Movements staged on the CURRENT map must still be written. A guard that
    drops every movement edit is not a repair.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eventide.ui.event_editor_tab import EventEditorTab  # noqa: E402

STEPS = [("walk_left",), ("step_end",)]


class _Mw:
    def __init__(self):
        self.messages = []

    def log_message(self, m):
        self.messages.append(m)


class _Stub:
    reset_modified_movements = EventEditorTab.reset_modified_movements
    _movements_for_current_map = EventEditorTab._movements_for_current_map

    def __init__(self, map_dir="MapA"):
        self._map_dir = map_dir
        self._mw = _Mw()


def test_the_reset_clears_and_records_the_owner():
    s = _Stub("MapA")
    s._modified_movements = {"Movement_X": STEPS}
    s.reset_modified_movements()
    assert s._modified_movements == {}
    assert s._modified_movements_owner == "MapA"


def test_movements_staged_on_the_current_map_are_written():
    """The guard must not drop legitimate edits."""
    s = _Stub("MapA")
    s.reset_modified_movements()
    s._modified_movements["Movement_X"] = STEPS
    assert s._movements_for_current_map() == {"Movement_X": STEPS}


def test_movements_staged_on_another_map_are_not_written():
    """The reported defect: one map's edit written into another."""
    s = _Stub("MapA")
    s.reset_modified_movements()
    s._modified_movements["Movement_X"] = STEPS
    s._map_dir = "MapB"                      # the user switched maps
    assert s._movements_for_current_map() == {}
    assert any("different map" in m for m in s._mw.messages)


def test_nothing_staged_means_nothing_written():
    s = _Stub("MapA")
    s.reset_modified_movements()
    assert s._movements_for_current_map() == {}
    assert s._mw.messages == []


def test_an_unknown_owner_does_not_block_the_write():
    """Legacy state with no recorded owner must not silently lose edits."""
    s = _Stub("MapA")
    s._modified_movements = {"Movement_X": STEPS}
    s._modified_movements_owner = None
    assert s._movements_for_current_map() == {"Movement_X": STEPS}


def test_the_reset_is_wired_into_a_successful_load():
    """Existence is not behaviour, and a helper nothing calls fixes nothing."""
    import inspect
    src = inspect.getsource(EventEditorTab._load_map)
    assert "reset_modified_movements()" in src, \
        "_load_map never clears staged movement edits"
    assert src.index("_remember_disk_state") < \
        src.index("reset_modified_movements()"), \
        "the reset runs before the load has finished replacing state"


def test_the_save_path_uses_the_ownership_filter():
    """The save must go through the filter, not read the dict directly.

    Asked of the PARSER, not of a substring search. The first version of this
    test grepped the class source for the old loop and failed against the
    docstring above, which quotes it — a comment is not code, and a lexical
    question deserves a lexical tool.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(
        sys.modules[EventEditorTab.__module__]))

    def _iterates_raw_dict(node):
        it = node.iter
        return (isinstance(it, ast.Call)
                and isinstance(it.func, ast.Attribute)
                and it.func.attr == "items"
                and isinstance(it.func.value, ast.Attribute)
                and it.func.value.attr == "_modified_movements")

    offenders = [n.lineno for n in ast.walk(tree)
                 if isinstance(n, ast.For) and _iterates_raw_dict(n)]
    assert not offenders, (
        "the save path still iterates _modified_movements directly at line(s) "
        "%s instead of going through the ownership filter" % offenders)
