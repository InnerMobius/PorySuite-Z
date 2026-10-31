"""Finding 0c: staged Maps edits vanished on close, even after clicking Save.

Section reassignments and new region-map entries are held in
`_pending_sections` / `_pending_rms` until Save. Staging one emitted NOTHING,
so the unified window never marked the Maps page dirty, the close prompt never
asked, and the work was lost without warning.

The repair adds a DEDICATED signal rather than reusing `data_changed`.

DO NOT REGRESS:
  * `staged_changed` must stay separate from `data_changed`. `data_changed`
    is also connected to `EventideMainWindow._on_maps_changed`, which reloads
    the Region Map and Layouts tabs from the project — so reporting "there is
    a staged edit here" through it would discard THOSE editors' pending work
    as a side effect.
  * Every path that clears the staged set must go through
    `_refresh_save_state()`, not set the button directly, or the window's
    dirty marker stays lit after an F5 or a successful save.
"""
import ast
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _maps_tab():
    from eventide.ui.maps_tab import MapsTab
    return MapsTab


def test_a_dedicated_staged_signal_exists():
    assert hasattr(_maps_tab(), "staged_changed")


def test_it_is_not_the_same_signal_as_data_changed():
    MapsTab = _maps_tab()
    assert MapsTab.staged_changed is not MapsTab.data_changed


def test_refresh_save_state_emits_it():
    """Staging must announce itself. It used to announce nothing."""
    src = inspect.getsource(_maps_tab()._refresh_save_state)
    assert "staged_changed.emit()" in src


def test_refresh_save_state_does_not_emit_data_changed():
    """Reusing data_changed would reload sibling editors and discard their
    pending edits — the cure being worse than the disease."""
    src = inspect.getsource(_maps_tab()._refresh_save_state)
    assert "data_changed.emit" not in src


def test_every_clear_path_refreshes_the_save_state():
    """Otherwise the dirty marker survives an F5 or a successful save.

    Asked of the parser: find every function that clears the staged sets and
    confirm it also calls _refresh_save_state().
    """
    import eventide.ui.maps_tab as mod
    tree = ast.parse(inspect.getsource(mod))

    def _clears_pending(fn):
        for n in ast.walk(fn):
            if (isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "clear"
                    and isinstance(n.func.value, ast.Attribute)
                    and n.func.value.attr in ("_pending_sections",
                                              "_pending_rms")):
                return True
        return False

    def _calls_refresh(fn):
        for n in ast.walk(fn):
            if (isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "_refresh_save_state"):
                return True
        return False

    offenders = [fn.name for fn in ast.walk(tree)
                 if isinstance(fn, ast.FunctionDef)
                 and _clears_pending(fn) and not _calls_refresh(fn)]
    assert not offenders, (
        "these clear the staged edits without refreshing the dirty marker: %s"
        % offenders)


def test_the_window_subscribes_to_the_staged_signal():
    """A signal nothing listens to reports nothing."""
    import ui.unified_mainwindow as uw
    src = inspect.getsource(uw.UnifiedMainWindow.setup_pages)
    assert "staged_changed.connect" in src, \
        "the unified window never subscribes to staged Maps edits"
