"""Finding 0j: a delete you UNDID still broke the build.

`_deleted_script_labels` is a per-session set that survives F5. Delete an
event, press F5 to revert it, then save: map.json came back from disk still
pointing at the script, but the label's block was stripped anyway. The link
then failed with

    undefined reference to 'PewterCity_EventScript_Lass'
    make: *** [Makefile:389: pokefirered_modern.elf] Error 1

and nothing in that message points back at the deleted event.

The repair refuses to strip a definition anything still refers to.

DO NOT REGRESS:
  * The reference scan is deliberately BROAD. Over-reporting a reference
    leaves a harmless orphan block; under-reporting one removes a definition
    something still points at and the project stops linking. Those errors are
    not equally bad — lean to keeping.
  * A label's OWN definition line is not a reference to itself, or nothing
    would ever be cleaned up.
  * Legitimate orphan cleanup must still happen: a label nothing references
    is still removed.
  * When the project root cannot be established, keep EVERYTHING. Guessing
    here is what the defect did.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eventide.ui.event_editor_tab import (  # noqa: E402
    _labels_referenced_in_project,
)

LABEL = "PewterCity_EventScript_Lass"
ORPHAN = "PewterCity_EventScript_Unused"


def _project(tmp_path, *, map_json_refs=True, script_ref=False):
    maps = tmp_path / "data" / "maps" / "PewterCity"
    maps.mkdir(parents=True)
    (maps / "scripts.inc").write_text(
        "%s::\n\tend\n\n%s::\n\tend\n" % (LABEL, ORPHAN), encoding="utf-8")
    refs = ('{"object_events": [{"script": "%s"}]}' % LABEL
            if map_json_refs else '{"object_events": []}')
    (maps / "map.json").write_text(refs, encoding="utf-8")
    if script_ref:
        other = tmp_path / "data" / "maps" / "Viridian"
        other.mkdir(parents=True)
        (other / "scripts.inc").write_text(
            "Viridian_EventScript_Sign::\n\tgoto %s\n" % ORPHAN,
            encoding="utf-8")
    return tmp_path


def test_a_label_map_json_still_points_at_is_kept(tmp_path):
    root = _project(tmp_path)
    used = _labels_referenced_in_project(root, {LABEL, ORPHAN})
    assert LABEL in used, "the label map.json references was not protected"


def test_a_label_nothing_references_is_not_kept(tmp_path):
    """Legitimate orphan cleanup must still happen."""
    root = _project(tmp_path)
    used = _labels_referenced_in_project(root, {LABEL, ORPHAN})
    assert ORPHAN not in used


def test_a_label_another_script_calls_is_kept(tmp_path):
    root = _project(tmp_path, script_ref=True)
    used = _labels_referenced_in_project(root, {ORPHAN})
    assert ORPHAN in used, "a goto from another map was not seen"


def test_a_definition_line_is_not_a_reference_to_itself(tmp_path):
    """Otherwise nothing would ever be cleaned up."""
    root = _project(tmp_path, map_json_refs=False)
    used = _labels_referenced_in_project(root, {LABEL})
    assert LABEL not in used


def test_no_labels_means_no_work(tmp_path):
    root = _project(tmp_path)
    assert _labels_referenced_in_project(root, set()) == set()
    assert _labels_referenced_in_project(root, None) == set()


def test_a_missing_root_keeps_everything(tmp_path):
    assert _labels_referenced_in_project(None, {LABEL}) == set()


def test_the_reset_is_a_named_operation():
    """The set must be cleared by one dedicated call at a verified
    transition, not by three unrelated attribute assignments scattered
    through the load paths."""
    from eventide.ui.event_editor_tab import EventEditorTab
    assert hasattr(EventEditorTab, "reset_deleted_script_labels")
    assert hasattr(EventEditorTab, "_labels_still_referenced")


def test_the_reset_actually_clears_the_set():
    """Existence is not behaviour. The first version of this file asserted
    only that the method EXISTS, which would have passed against a method
    that did nothing."""
    from eventide.ui.event_editor_tab import EventEditorTab

    class Stub:
        reset_deleted_script_labels = EventEditorTab.reset_deleted_script_labels

    s = Stub()
    s._deleted_script_labels = {LABEL, ORPHAN}
    s.reset_deleted_script_labels()
    assert s._deleted_script_labels == set()


def test_a_successful_map_load_invokes_the_reset():
    """And the reset must be WIRED, not merely available. A helper nothing
    calls fixes nothing — this asserts the real load path invokes it, at the
    END of the load rather than on entering it."""
    import inspect
    from eventide.ui.event_editor_tab import EventEditorTab

    src = inspect.getsource(EventEditorTab._load_map)
    assert "reset_deleted_script_labels()" in src, \
        "_load_map never clears the staged deletions"
    # It must come AFTER the state has been replaced — the disk snapshot is
    # the last thing a successful load records.
    assert src.index("_remember_disk_state") < \
        src.index("reset_deleted_script_labels()"), \
        "the reset runs before the load has finished replacing state"


def test_liveness_failure_keeps_every_label(tmp_path, monkeypatch):
    """The untested branch. If the reference scan blows up, the handler must
    not raise again, must KEEP the labels, and must not treat the failure as
    proof that deletion is safe."""
    from eventide.ui.event_editor_tab import EventEditorTab

    class Mw:
        project_dir = str(tmp_path)

    class Stub:
        _labels_still_referenced = EventEditorTab._labels_still_referenced

        def __init__(self):
            self._mw = Mw()

    monkeypatch.setattr(
        "eventide.ui.event_editor_tab._labels_referenced_in_project",
        lambda *a, **k: (_ for _ in ()).throw(OSError("scan exploded")))

    kept = Stub()._labels_still_referenced({LABEL, ORPHAN})
    assert kept == {LABEL, ORPHAN}, \
        "a failed liveness scan must keep everything, not delete it"


def test_an_unknown_project_root_keeps_every_label():
    from eventide.ui.event_editor_tab import EventEditorTab

    class Mw:
        pass

    class Stub:
        _labels_still_referenced = EventEditorTab._labels_still_referenced

        def __init__(self):
            self._mw = Mw()

    assert Stub()._labels_still_referenced({LABEL}) == {LABEL}
