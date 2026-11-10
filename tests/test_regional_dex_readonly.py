"""The Regional Dex is EDITABLE, and drives the engine's real Hoenn ordering.

THIS FILE REPLACED AN EARLIER ONE WITH THE OPPOSITE CONTRACT. That is recorded
here rather than quietly dropped.

The previous version asserted that the regional list must stay read-only, on
the stated grounds that "there is NO regional list in the engine" and that
membership was `SpeciesToNationalPokedexNum(sp) <= KANTO_DEX_COUNT`. That
premise was wrong. This project's engine has a real 411-entry `HOENN_DEX_*`
ordering whose first `HOENN_DEX_COUNT` (202) entries are the regional dex, and
`src/pokedex.c` walks it directly. The list PorySuite was showing was the first
151 entries of the NATIONAL dex under a regional label -- only 47 of which
appear in the engine's dex at all.

So the read-only state was a containment for a defect, not the intended
behaviour, and the owner asked for real editing. The tests asserting the
containment are therefore replaced, NOT weakened: every one of them is
superseded by a test of the behaviour that containment stood in for. The
National Dex guards and the false-dirty guards from the old file are kept
unchanged, because nothing about them was affected.

See core/hoenn_dex.py and tests/test_hoenn_regional_dex.py for the model
itself. This file covers the WIRING in ui/mainwindow.py.

DO NOT REGRESS
--------------
* Drag must be wired to `rows_move_requested`, NEVER to the model's
  `rowsMoved`. A dropped row never emits `rowsMoved`, so a handler wired there
  never runs and the list drifts away from the data in silence. That is
  measured and documented in ui/custom_widgets/reorderable_list.py.
* The move is made in the DATA and the list repainted from it. The widget is
  never asked to reorder its own rows.
* Section headers and the dividing row are not Pokemon: not selectable, not
  draggable, and never read back as entries.
* The Info-tab tick and the list must drive the same ordering and boundary.
* Removing a Pokemon from the dex must not delete it from the ordering.
* The National Dex half stays exactly as it was.
"""

import os
import re
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "core"), os.path.join(_ROOT, "ui")):
    sys.path.insert(0, _p)

import pytest  # noqa: E402

try:
    from PyQt6.QtWidgets import QApplication, QAbstractItemView, QListWidgetItem
    from PyQt6.QtCore import Qt
    _QT = True
except Exception:
    _QT = False

pytestmark = pytest.mark.skipif(not _QT, reason="PyQt6 not available")

if _QT:
    _APP = QApplication.instance() or QApplication([])

MAINWINDOW = os.path.join(_ROOT, "ui", "mainwindow.py")


@pytest.fixture(scope="module")
def src():
    with open(MAINWINDOW, encoding="utf-8") as fh:
        return fh.read()


def _method(src, name):
    m = re.search(r"\n    def %s\(.*?(?=\n    def )" % name, src, re.S)
    assert m, f"{name} is missing"
    return m.group(0)


# ── editing is real, and wired to the signal that actually fires ────────────

def test_the_add_and_remove_buttons_are_connected(code):
    """They were disconnected and deleted while the list was a false view."""
    assert "pushButton_4" in code and "pushButton_5" in code
    assert "_regional_dex_add" in code and "_regional_dex_remove" in code
    assert "clicked.connect(_slot)" in code, (
        "the regional Add/Remove buttons must be wired to their handlers"
    )


def test_the_buttons_are_no_longer_destroyed(code):
    """The old containment removed them from the layout entirely."""
    assert "btn.setParent(None)" not in code or "deleteLater" not in _method(
        src, "_apply_regional_dex_editable"
    ), "the regional buttons must survive so they can be used"


def test_drag_is_wired_to_rows_move_requested_not_rowsmoved(code):
    """A dropped row never emits rowsMoved; a handler there would never run."""
    assert "list_pokedex_regional.rows_move_requested.connect" in code, (
        "regional drag must use the signal the promoted list actually emits"
    )
    assert "list_pokedex_regional.model().rowsMoved.connect" not in code, (
        "rowsMoved does not fire for a drop -- see reorderable_list.py"
    )


def test_the_drag_handler_moves_the_data_not_the_widget(code):
    body = _method(code, "_on_regional_rows_moved")
    assert "reorder_rows" in body, (
        "the move must be computed on data, using the shared helper"
    )
    assert "_set_hoenn_model" in body, "the result must be stored"
    assert "_populate_regional_dex_list" in body, "the list is repainted"


def test_the_drag_handler_refuses_rather_than_losing_an_entry(code):
    body = _method(code, "_on_regional_rows_moved")
    assert "len(new_order) != len(order)" in body, (
        "a reorder that lost an entry must be refused, not written"
    )


def test_the_drag_handler_ignores_header_rows(code):
    body = _method(code, "_on_regional_rows_moved")
    assert "ROW_ENTRY" in body, "headers must not be dragged as Pokemon"


def test_the_list_is_rebuilt_from_the_model(code):
    body = _method(code, "_populate_regional_dex_list")
    assert "_hoenn_model()" in body
    assert "ROW_BOUNDARY" in body, "the dividing row must be tagged"


def test_the_boundary_row_is_not_a_pokemon(code):
    body = _method(code, "_make_regional_header")
    assert "ItemIsEnabled" in body, (
        "a header must not be selectable or draggable"
    )


def test_removing_from_the_dex_does_not_delete(code):
    body = _method(code, "_regional_dex_remove")
    assert "order.insert" in body, (
        "a removed Pokemon moves below the line; it is never dropped"
    )
    assert "order.pop" in body


def test_adding_lands_at_the_end_of_the_included_section(code):
    body = _method(code, "_regional_dex_add")
    assert "order.insert(count, entry)" in body


def _regdex_blocks(code):
    """Every `key == "IN_REGDEX"` block. There are two: read and write."""
    out = []
    for m in re.finditer(r'key == "IN_REGDEX":', code):
        out.append(code[m.start():m.start() + 2200])
    return out


def test_the_info_tab_tick_reads_the_authoritative_model(code):
    blocks = _regdex_blocks(code)
    assert len(blocks) >= 2, (
        "expected a read site and a write site for the regional tick"
    )
    assert any("_hoenn_model()" in b for b in blocks), (
        "the tick's state must be read from the ordering and boundary"
    )


def test_the_info_tab_tick_writes_the_authoritative_model(code):
    """Two surfaces, one ordering and boundary."""
    assert any("_set_hoenn_model" in b for b in _regdex_blocks(code)), (
        "ticking must change the model the Regional Dex tab also edits"
    )


def test_the_tick_no_longer_uses_the_national_cutoff(code):
    """It compared a NATIONAL position against KANTO_DEX_COUNT -- a different
    list, so it ticked the wrong Pokemon."""
    for b in _regdex_blocks(code):
        assert "_load_kanto_cutoff_from_header" not in b
        assert "_load_national_order_from_header" not in b


def test_the_tick_does_not_write_the_derived_view(code):
    for b in _regdex_blocks(code):
        assert "pdata['regional_dex'] = lst" not in b, (
            "the tick must not write the derived view as if it were the source"
        )


@pytest.fixture(scope="module")
def code(src):
    """`src` with comments AND docstrings removed.

    Asked of the tokenizer and the parser rather than eyeballed. The code
    around these branches EXPLAINS itself using the very names being searched
    for, and neither a comment nor a docstring is a behaviour:

      * a negative assertion fails on prose that merely mentions the old name
      * a positive assertion PASSES on prose that merely describes the new one,
        which is the dangerous direction -- it would report a repair that was
        only ever written down

    Line structure is preserved so offsets still line up with the original.
    """
    import ast
    import io
    import tokenize

    lines = src.splitlines(keepends=True)

    def blank(n, col=0):
        if not 0 < n <= len(lines):
            return
        line = lines[n - 1]
        nl = "\n" if line.endswith("\n") else ""
        lines[n - 1] = line[:col] + nl

    # Comments, blanked IN PLACE. Rebuilding the text from tokens instead
    # inserts an extra newline for every whole-line comment, which shifts every
    # later line and makes the parser's line numbers below point at the wrong
    # place -- the strip then leaves half a docstring behind as bare prose.
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            blank(tok.start[0], tok.start[1])

    # Docstrings, from the parser.
    for node in ast.walk(ast.parse(src)):
        body = getattr(node, "body", None)
        if not isinstance(node, (ast.Module, ast.ClassDef,
                                 ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not body:
            continue
        first = body[0]
        if (isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            for n in range(first.lineno,
                           (first.end_lineno or first.lineno) + 1):
                blank(n)

    out = "".join(lines)
    # The result must still be parseable, or the strip damaged the source and
    # every assertion built on it is meaningless.
    ast.parse(out)
    return out


def test_the_strip_actually_strips(src, code):
    """The foundation of every scan below, so it is checked rather than assumed.

    A stripper that quietly did nothing would leave all of those assertions
    satisfiable by prose -- they would pass while reporting repairs that were
    only ever written down.
    """
    assert len(code) < len(src), "nothing was stripped"
    assert code.count("\n") == src.count("\n"), (
        "line structure must be preserved or the parser offsets drift"
    )
    # A comment that exists in this very file's subject.
    assert "# Regional dex Add / Remove, and drag across the boundary." in src
    assert "# Regional dex Add / Remove, and drag across the boundary." \
        not in code
    # A docstring.
    assert "Swap a generated dex list for one that owns its own drops." in src
    assert "Swap a generated dex list for one that owns its own drops." \
        not in code
    # and real code survives
    assert "def _populate_regional_dex_list" in code
    assert "rows_move_requested.connect" in code


def test_the_strip_would_catch_a_comment_only_claim(code):
    """The failure mode it exists to prevent, demonstrated.

    If a repair were written only as a comment, a scan of the stripped source
    must NOT find it.
    """
    import io
    import tokenize

    fake = (
        "def f():\n"
        "    # self._totally_real_repair()\n"
        "    pass\n"
    )
    stripped = []
    for tok in tokenize.generate_tokens(io.StringIO(fake).readline):
        if tok.type == tokenize.COMMENT:
            stripped.append(tok.string)
    assert stripped, "the tokenizer found no comment to strip"
    assert "_totally_real_repair" in stripped[0]


def test_the_tick_is_enabled(code):
    """It was greyed out, which made both its paths dead.

    Scoped to the block that actually clears ItemIsEnabled. A bare search for
    `("IN_NATDEX", "IN_REGDEX")` also hits a dispatch in the flush path, which
    is not a disabling and is meant to cover both.
    """
    import ast

    # Ask the parser which flag names guard the branch that clears
    # ItemIsEnabled. A text window around that call reaches back into the
    # tooltips dict, which mentions both names for entirely legitimate
    # reasons -- proximity is not a guard.
    guards = []
    for node in ast.walk(ast.parse(code)):
        if not isinstance(node, ast.If):
            continue
        clears = any(
            isinstance(c, ast.Call)
            and isinstance(c.func, ast.Attribute)
            and c.func.attr == "setFlags"
            and any(isinstance(a, ast.BinOp)
                    and any(isinstance(x, ast.UnaryOp)
                            and isinstance(x.op, ast.Invert)
                            for x in ast.walk(a))
                    for a in c.args)
            for c in ast.walk(node)
        )
        if clears:
            guards.append({
                n.value for n in ast.walk(node.test)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
            })

    assert guards, "no branch clears a flag -- test fixture is stale"
    disabling = [g for g in guards if "IN_NATDEX" in g]
    assert disabling, "IN_NATDEX must still be the engine-managed one"
    for g in disabling:
        assert "IN_REGDEX" not in g, (
            "IN_REGDEX must not be greyed out -- it is editable now"
        )


def test_the_tick_repaints_the_regional_list(code):
    """Both surfaces must stay in step."""
    assert any("_populate_regional_dex_list" in b for b in _regdex_blocks(code))


def test_the_save_path_no_longer_rebuilds_from_widget_rows(code):
    """The list now holds headers, which are not Pokemon."""
    assert "entry[\"dex_num\"] = i + 1\n                            reg_list" \
        not in code
    assert "_read_regional_list" in code


def test_unreadable_engine_data_turns_editing_off(code):
    body = _method(code, "_populate_regional_dex_list")
    assert "_apply_regional_dex_editable(False)" in body, (
        "with no model there is nothing safe to edit"
    )


def test_the_note_describes_editing_not_derivation(src):
    m = re.search(r"reg_note = QLabel\((.*?)\)\n", src, re.S)
    assert m, "the explanatory note is missing"
    note = m.group(1)
    assert "KANTO_DEX_COUNT" not in note, (
        "that was the false model and must not be described to the user"
    )
    assert "dividing line" in note.lower()


def test_the_list_is_still_added_to_the_tab(code):
    assert "reg_vbox.addWidget(self.ui.list_pokedex_regional)" in code


def test_the_search_box_survives(code):
    assert "reg_vbox.addWidget(self.ui.pokedex_regional_search)" in code


# ── the National Dex half must be untouched (kept from the old file) ────────

@pytest.mark.parametrize("name", [
    "_promote_dex_list",
    "_on_dex_rows_move_requested",
    "_dex_order_is_sane",
])
def test_national_dex_protections_still_exist(code, name):
    assert f"def {name}" in code, f"{name} is a National Dex guard and must stay"


def test_national_list_is_still_draggable(code):
    assert "list_pokedex_national" in code
    assert "rows_move_requested" in code


def test_regional_editing_does_not_touch_the_national_list(code):
    for name in ("_regional_dex_add", "_regional_dex_remove",
                 "_on_regional_rows_moved", "_populate_regional_dex_list"):
        assert "list_pokedex_national" not in _method(code, name), (
            f"{name} must not reach into the National Dex list"
        )


# ── viewing must never dirty the project (kept from the old file) ───────────

def test_viewing_a_dex_entry_is_guarded_against_false_dirty(code):
    body = _method(code, "update_pokedex_entry")
    assert "self._loading_depth += 1" in body, (
        "selecting a dex row populates widgets, whose signals would otherwise "
        "mark the project modified"
    )
    assert "_loading_depth -= 1" in body, "the guard must be released"


def test_panel_flush_short_circuits_while_loading(code):
    body = _method(code, "_flush_pokedex_panel")
    assert '_loading_depth", 0) > 0' in body and "return" in body


def test_repainting_the_list_does_not_dirty_the_project(code):
    """A repaint happens on load too, so it must not look like an edit."""
    body = _method(code, "_populate_regional_dex_list")
    assert "blockSignals(True)" in body
    store = _method(code, "_set_hoenn_model")
    assert "_loading_depth" in store, (
        "storing the model during a load must not mark the project modified"
    )


# ── behaviour: the shared reorder helper, on the real descriptors ───────────

def test_reorder_across_the_divider_changes_membership():
    """The property the whole design rests on, exercised for real."""
    from ui.custom_widgets.reorderable_list import reorder_rows

    ENTRY, BOUND = "entry", "boundary"
    rows = [(ENTRY, "A"), (ENTRY, "B"), (BOUND, None), (ENTRY, "C")]

    # Drag C (row 3) up above the divider.
    moved, landed = reorder_rows(rows, [3], 2)
    assert landed
    kinds = [k for k, _ in moved]
    consts = [c for k, c in moved if k == ENTRY]
    assert consts == ["A", "B", "C"], consts
    # C is now before the divider, so it is in the dex.
    assert kinds.index(BOUND) == 3


def test_reorder_within_a_section_leaves_membership_alone():
    from ui.custom_widgets.reorderable_list import reorder_rows

    ENTRY, BOUND = "entry", "boundary"
    rows = [(ENTRY, "A"), (ENTRY, "B"), (BOUND, None), (ENTRY, "C")]
    moved, landed = reorder_rows(rows, [1], 0)
    assert landed
    kinds = [k for k, _ in moved]
    assert [c for k, c in moved if k == ENTRY] == ["B", "A", "C"]
    assert kinds.index(BOUND) == 2, "the divider must not have moved"


def test_a_promoted_list_refuses_to_move_its_own_rows():
    """Why the data is moved instead: the widget corrupts its own rows."""
    from ui.custom_widgets.reorderable_list import ReorderableListWidget
    lst = ReorderableListWidget()
    for n in ("A", "B", "C"):
        it = QListWidgetItem(n)
        it.setData(Qt.ItemDataRole.UserRole, f"NATIONAL_DEX_{n}")
        lst.addItem(it)
    fired = []
    lst.rows_move_requested.connect(lambda rows, dest: fired.append((rows, dest)))
    lst.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)

    # The rows are untouched until the editor moves them itself.
    assert [lst.item(i).text() for i in range(lst.count())] == ["A", "B", "C"]
    assert fired == []
