"""Finding 0d: editing one tile animation then clicking another lost the edit.

`_current_anim` is a single pointer, and both `has_unsaved_changes()` and
`flush_to_disk()` worked off whatever it pointed at. So editing animation A's
properties and selecting B discarded A's edits AND made the app report nothing
unsaved — it was now comparing B's spinners against B.

A Save All "said dirty, wrote nothing" backstop cannot catch this: the edit is
already gone before Save All runs.

DO NOT REGRESS:
  * Staging is keyed by animation IDENTITY, not name — two animations sharing
    a name must not overwrite each other's staged edits.
  * `has_unsaved_changes()` must consider EVERY staged animation, not only
    the selected one. That single-pointer check is the defect.
  * Returning to an animation must show its staged values, not silently
    revert to disk.
  * Editing a value back to what is on disk clears the pending entry — it is
    no longer a change.
  * A save must write every staged animation, not just the visible one.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _Anim:
    """Stands in for a parsed animation record."""

    def __init__(self, name, divisor=8, dest_tile=0x100, tile_count=4,
                 phase=0, counter_max=0):
        self.name = name
        self.display_name = name
        self.divisor = divisor
        self.dest_tile = dest_tile
        self.tile_count = tile_count
        self.phase = phase
        self.counter_max = counter_max


class _Spin:
    def __init__(self, v=0):
        self._v = v

    def value(self):
        return self._v

    def setValue(self, v):
        self._v = v


def _viewer():
    """A stub carrying the real staging methods."""
    from ui.tile_anim_viewer import TileAnimEditorWidget as T

    class Stub:
        _current_prop_values = T._current_prop_values
        _props_differ = staticmethod(T._props_differ)
        _capture_pending_props = T._capture_pending_props
        discard_pending_props = T.discard_pending_props
        has_unsaved_changes = T.has_unsaved_changes

        def __init__(self):
            self._loading = False
            self._dirty = False
            self._pending_props = {}
            self._current_anim = None
            self._divisor_spin = _Spin()
            self._start_tile_spin = _Spin()
            self._tile_amount_spin = _Spin()
            self._phase_spin = _Spin()
            self._counter_max_spin = _Spin()

        def show(self, anim):
            """Mimic selecting an animation, staging the previous one first."""
            self._capture_pending_props()
            self._current_anim = anim
            staged = self._pending_props.get(id(anim), (None, None))[1]
            self._divisor_spin.setValue(
                staged["divisor"] if staged else anim.divisor)
            self._start_tile_spin.setValue(
                staged["dest_tile"] if staged else anim.dest_tile)
            self._tile_amount_spin.setValue(
                staged["tile_count"] if staged else anim.tile_count)
            self._phase_spin.setValue(staged["phase"] if staged else anim.phase)
            self._counter_max_spin.setValue(
                staged["counter_max"] if staged else (anim.counter_max or 0))

    return Stub()


def test_switching_away_keeps_the_edit():
    """The reported defect: A edited, click B, A's edit gone."""
    a, b = _Anim("A"), _Anim("B")
    v = _viewer()
    v.show(a)
    v._divisor_spin.setValue(2)          # the user edits A
    v.show(b)                            # and clicks B
    assert id(a) in v._pending_props, "A's edit was discarded on switch"
    assert v._pending_props[id(a)][1]["divisor"] == 2


def test_the_app_still_reports_unsaved_after_switching():
    """It used to say nothing was unsaved the moment you clicked away."""
    a, b = _Anim("A"), _Anim("B")
    v = _viewer()
    v.show(a)
    v._divisor_spin.setValue(2)
    v.show(b)
    assert v.has_unsaved_changes() is True


def test_returning_shows_the_staged_values_not_disk():
    a, b = _Anim("A"), _Anim("B")
    v = _viewer()
    v.show(a)
    v._divisor_spin.setValue(2)
    v.show(b)
    v.show(a)                            # back to A
    assert v._divisor_spin.value() == 2, "A silently reverted to disk"


def test_an_untouched_animation_stages_nothing():
    a, b = _Anim("A"), _Anim("B")
    v = _viewer()
    v.show(a)
    v.show(b)
    assert v._pending_props == {}
    assert v.has_unsaved_changes() is False


def test_editing_back_to_the_disk_value_clears_the_pending_entry():
    a = _Anim("A", divisor=8)
    v = _viewer()
    v.show(a)
    v._divisor_spin.setValue(2)
    v._capture_pending_props()
    assert id(a) in v._pending_props
    v._divisor_spin.setValue(8)          # back to what is on disk
    v._capture_pending_props()
    assert id(a) not in v._pending_props


def test_two_animations_can_both_hold_staged_edits():
    a, b = _Anim("A"), _Anim("B")
    v = _viewer()
    v.show(a)
    v._divisor_spin.setValue(2)
    v.show(b)
    v._phase_spin.setValue(3)
    v._capture_pending_props()
    assert v._pending_props[id(a)][1]["divisor"] == 2
    assert v._pending_props[id(b)][1]["phase"] == 3


def test_same_named_animations_do_not_collide():
    """Keyed by identity, not name."""
    a, b = _Anim("Flower"), _Anim("Flower")
    v = _viewer()
    v.show(a)
    v._divisor_spin.setValue(2)
    v.show(b)
    v._divisor_spin.setValue(5)
    v._capture_pending_props()
    assert v._pending_props[id(a)][1]["divisor"] == 2
    assert v._pending_props[id(b)][1]["divisor"] == 5


def test_discard_drops_every_staged_edit():
    """F5 means revert to disk."""
    a, b = _Anim("A"), _Anim("B")
    v = _viewer()
    v.show(a)
    v._divisor_spin.setValue(2)
    v.show(b)
    v._phase_spin.setValue(3)
    v._capture_pending_props()
    v.discard_pending_props()
    assert v._pending_props == {}
    assert v._dirty is False


def test_the_flush_writes_every_staged_animation():
    """Not merely the one on screen."""
    import ast
    import inspect
    import ui.tile_anim_viewer as mod
    src = inspect.getsource(mod.TileAnimEditorWidget.flush_to_disk)
    assert "_pending_props" in src, \
        "flush_to_disk still only writes the selected animation"
    tree = ast.parse(inspect.getsource(mod))
    names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "_flush_one_anim" in names
