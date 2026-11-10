"""Finding item 3: F5 on Trainer Graphics could be undone by the next save.

F5 means "throw away my in-memory edits and reload from disk". This tab's
`load()` reset most of its state but missed two things: `_sprite_png_dirty`
(the record of WHICH sprites were edited) and `_sprite_imgs` (the edited
pixels). Re-confirmed against the committed checkpoint: neither was cleared
anywhere in the file, so they outlived a project switch too.

Together they bite. The leftover record says "these still need writing" and
the leftover images supply the discarded pixels — so pressing F5, clicking a
sprite you had edited, and saving wrote back the edit you had just discarded.
Without re-clicking it you got a quiet save error instead, and the stale entry
never cleared, because entries are only removed on a SUCCESSFUL write.

All three sibling graphics tabs already cleared theirs; this one did not.

DO NOT REGRESS:
  * Both must be cleared, not just the dirty set. The record alone is
    harmless; the images alone are harmless; together they resurrect a
    discarded edit.
  * They belong in the reset block BEFORE the grid is rebuilt, so a load that
    fails partway cannot drop the record while leaving the stale images.
"""
import ast
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load_src():
    import ui.trainer_graphics_tab as mod
    return mod, inspect.getsource(mod.TrainerGraphicsTab.load)


def test_load_clears_the_edited_sprite_record():
    _mod, src = _load_src()
    assert "_sprite_png_dirty.clear()" in src, \
        "F5 leaves the record of which sprites were edited"


def test_load_clears_the_edited_images():
    _mod, src = _load_src()
    assert "_sprite_imgs.clear()" in src, \
        "F5 leaves the edited pixels behind to be re-saved"


def test_both_clears_precede_the_grid_rebuild():
    """A load that fails partway must not drop the record while leaving the
    stale images."""
    _mod, src = _load_src()
    rebuild = src.index("_rebuild_grid")
    assert src.index("_sprite_png_dirty.clear()") < rebuild
    assert src.index("_sprite_imgs.clear()") < rebuild


def test_they_sit_with_the_other_reset_clears():
    _mod, src = _load_src()
    assert src.index("_palette_dirty.clear()") < \
        src.index("_sprite_png_dirty.clear()")


def test_the_dirty_check_reads_what_load_now_clears():
    """If `has_unsaved_changes` consulted something else, clearing these two
    would not settle the tab's dirty state."""
    import ui.trainer_graphics_tab as mod
    fn = getattr(mod.TrainerGraphicsTab, "has_unsaved_changes", None)
    assert fn is not None
    src = inspect.getsource(fn)
    assert "_sprite_png_dirty" in src


def test_the_reset_is_a_statement_not_a_comment():
    """Asked of the parser: a mention in a docstring is not a clear."""
    import ui.trainer_graphics_tab as mod
    tree = ast.parse(inspect.getsource(mod))
    cleared = set()
    for fn in ast.walk(tree):
        if not (isinstance(fn, ast.FunctionDef) and fn.name == "load"):
            continue
        for node in ast.walk(fn):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "clear"
                    and isinstance(node.func.value, ast.Attribute)):
                cleared.add(node.func.value.attr)
    assert {"_sprite_png_dirty", "_sprite_imgs"} <= cleared, cleared
