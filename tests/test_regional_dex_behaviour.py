"""The regional Pokedex editing methods, actually run against a real list widget.

The sibling file `test_regional_dex_readonly.py` checks the WIRING by reading
the source. That cannot show the methods work -- it shows they are connected.
So this file builds a real `ReorderableListWidget`, attaches the real
`MainWindow` methods to a stub holding a real model, and drives them.

Still not a claim about the GUI as a whole: no window is shown, no mouse drag
happens, and Qt's own drop handling is bypassed exactly as the production code
bypasses it. What IS exercised is the thing the repair turns on -- that a
reported drop, a button press and a tick each produce the right ordering and
boundary, and that the list repaints to match.

DO NOT REGRESS
--------------
* Membership is decided by which side of the dividing row an entry lands on.
* Nothing is ever dropped from the ordering. Length is invariant.
* Headers are never read back as Pokemon.
* A refused edit leaves the stored model untouched.
"""
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "core"), os.path.join(_ROOT, "ui")):
    sys.path.insert(0, _p)

try:
    from PyQt6.QtWidgets import QApplication
    _QT = True
except Exception:
    _QT = False

pytestmark = pytest.mark.skipif(not _QT, reason="PyQt6 not available")

if _QT:
    _APP = QApplication.instance() or QApplication([])

NAMES = ["ALPHA", "BETA", "GAMMA", "DELTA", "EPSILON", "ZETA"]
COUNT = 4                      # first four are in the dex


class _Dex:
    def __init__(self, data):
        self.data = data


class _Source:
    def __init__(self, data):
        self.data = {"pokedex": _Dex(data)}

    def get_species_by_dex_constant(self, const):
        return const.replace("NATIONAL_DEX_", "SPECIES_") if const else ""

    def get_species_data(self, species, key, *a):
        if key == "name":
            return species.replace("SPECIES_", "").title()
        return None


class _Ui:
    pass


@pytest.fixture
def win():
    """A stub carrying the REAL methods under test."""
    import mainwindow as mw
    from ui.custom_widgets.reorderable_list import ReorderableListWidget

    MW = mw.MainWindow

    class _Win:
        HOENN_CONST_ROLE = MW.HOENN_CONST_ROLE
        ROW_KIND_ROLE = MW.ROW_KIND_ROLE
        ROW_ENTRY = MW.ROW_ENTRY
        ROW_HEADER = MW.ROW_HEADER
        ROW_BOUNDARY = MW.ROW_BOUNDARY

        _hoenn_model = MW._hoenn_model
        _hoenn_dex_error = MW._hoenn_dex_error
        _regional_row_label = MW._regional_row_label
        _make_regional_header = MW._make_regional_header
        _populate_regional_dex_list = MW._populate_regional_dex_list
        _apply_regional_dex_editable = MW._apply_regional_dex_editable
        _set_hoenn_model = MW._set_hoenn_model
        _read_regional_list = MW._read_regional_list
        _on_regional_rows_moved = MW._on_regional_rows_moved
        _selected_regional_entry = MW._selected_regional_entry
        _regional_dex_add = MW._regional_dex_add
        _regional_dex_remove = MW._regional_dex_remove
        _regional_dex_hint = MW._regional_dex_hint

        def __init__(self):
            self._loading_depth = 0
            self.modified = []
            self.hints = []
            self.ui = _Ui()
            self.ui.list_pokedex_regional = ReorderableListWidget()
            self.source_data = _Source({
                "hoenn_order": [
                    {"hoenn_constant": f"HOENN_DEX_{n}",
                     "dex_constant": f"NATIONAL_DEX_{n}"} for n in NAMES
                ],
                "regional_count": COUNT,
            })

        def setWindowModified(self, v):
            self.modified.append(v)

        def _species_list_icon(self, species):
            from PyQt6.QtGui import QIcon
            return QIcon()

        def statusBar(self):
            raise RuntimeError("no status bar in this stub")

    w = _Win()
    w._populate_regional_dex_list()
    return w


def _consts(w):
    order, count = w._hoenn_model()
    return [e["hoenn_constant"] for e in order], count


def _rows(w):
    lst = w.ui.list_pokedex_regional
    return [(lst.item(i).data(w.ROW_KIND_ROLE),
             lst.item(i).data(w.HOENN_CONST_ROLE))
            for i in range(lst.count())]


def _select(w, const):
    lst = w.ui.list_pokedex_regional
    for i in range(lst.count()):
        if lst.item(i).data(w.HOENN_CONST_ROLE) == const:
            lst.setCurrentRow(i)
            return i
    raise AssertionError(f"{const} is not in the list")


# ── the list reflects the model ─────────────────────────────────────────────

def test_the_list_shows_every_entry_plus_two_headers(win):
    rows = _rows(win)
    entries = [c for k, c in rows if k == win.ROW_ENTRY]
    assert entries == [f"HOENN_DEX_{n}" for n in NAMES]
    assert sum(1 for k, _ in rows if k != win.ROW_ENTRY) == 2


def test_the_divider_sits_at_the_boundary(win):
    rows = _rows(win)
    kinds = [k for k, _ in rows]
    before = sum(1 for k, _ in rows[:kinds.index(win.ROW_BOUNDARY)]
                 if k == win.ROW_ENTRY)
    assert before == COUNT


def test_building_the_list_does_not_mark_the_project_modified(win):
    """It is built on load too."""
    assert win.modified == []


# ── reading the list back ───────────────────────────────────────────────────

def test_reading_back_an_untouched_list_returns_the_same_model(win):
    order, count = win._read_regional_list()
    assert [e["hoenn_constant"] for e in order] == \
        [f"HOENN_DEX_{n}" for n in NAMES]
    assert count == COUNT


def test_headers_are_not_read_back_as_pokemon(win):
    order, _ = win._read_regional_list()
    assert all(e.get("hoenn_constant") for e in order)
    assert len(order) == len(NAMES)


# `_read_regional_list` is what the SAVE path uses as its last check that what
# is on screen matches what is about to be written. A deliberate mutation --
# making it ignore the dividing row -- was caught by only ONE test, which is
# too thin for a function standing between the user and their engine files.
# These cover its boundary handling directly.

def test_the_save_readback_puts_the_boundary_at_the_divider(win):
    """Not at the end, and not at a fixed number."""
    _, count = win._read_regional_list()
    rows = _rows(win)
    kinds = [k for k, _ in rows]
    above = sum(1 for k, _ in rows[:kinds.index(win.ROW_BOUNDARY)]
                if k == win.ROW_ENTRY)
    assert count == above
    assert count != len(NAMES), "a boundary at the end would not be read"


def test_the_save_readback_follows_the_divider_when_it_moves(win):
    """Drag something across, then read back: the boundary must have moved."""
    before = win._read_regional_list()[1]
    row = _select(win, "HOENN_DEX_ZETA")
    win._on_regional_rows_moved([row], 1)
    after = win._read_regional_list()[1]
    assert after == before + 1


def test_the_save_readback_matches_what_a_drag_stored(win):
    """The two must never disagree -- that is the whole point of one model."""
    win._on_regional_rows_moved([1], win.ui.list_pokedex_regional.count())
    stored_order, stored_count = win._hoenn_model()
    read_order, read_count = win._read_regional_list()
    assert [e["hoenn_constant"] for e in read_order] == \
        [e["hoenn_constant"] for e in stored_order]
    assert read_count == stored_count


def test_the_save_readback_refuses_an_unknown_row(win):
    """Rather than writing an entry the engine has no identity for."""
    lst = win.ui.list_pokedex_regional
    row = _select(win, "HOENN_DEX_GAMMA")
    lst.item(row).setData(win.HOENN_CONST_ROLE, "HOENN_DEX_INTRUDER")
    assert win._read_regional_list() is None


def test_the_save_readback_refuses_if_an_entry_went_missing(win):
    lst = win.ui.list_pokedex_regional
    lst.takeItem(_select(win, "HOENN_DEX_GAMMA"))
    assert win._read_regional_list() is None


def test_a_refused_readback_leaves_the_stored_model_alone(win):
    before = _consts(win)
    lst = win.ui.list_pokedex_regional
    lst.takeItem(_select(win, "HOENN_DEX_GAMMA"))
    assert win._read_regional_list() is None
    assert _consts(win) == before


# ── a drop across the divider changes membership ────────────────────────────

def test_dragging_below_the_line_removes_from_the_dex(win):
    """ALPHA (widget row 1) dragged to the very end."""
    win._on_regional_rows_moved([1], win.ui.list_pokedex_regional.count())
    consts, count = _consts(win)
    assert count == COUNT - 1
    assert consts[-1] == "HOENN_DEX_ALPHA"
    assert len(consts) == len(NAMES), "nothing may be deleted"


def test_dragging_above_the_line_adds_to_the_dex(win):
    """ZETA, the last excluded entry, dragged to the top."""
    row = _select(win, "HOENN_DEX_ZETA")
    win._on_regional_rows_moved([row], 1)
    consts, count = _consts(win)
    assert count == COUNT + 1
    assert consts[0] == "HOENN_DEX_ZETA"
    assert len(consts) == len(NAMES)


def test_dragging_within_a_section_leaves_membership_alone(win):
    before_count = _consts(win)[1]
    row = _select(win, "HOENN_DEX_DELTA")
    win._on_regional_rows_moved([row], 1)
    consts, count = _consts(win)
    assert count == before_count
    assert consts[0] == "HOENN_DEX_DELTA"


def test_a_drag_marks_the_project_modified(win):
    win._on_regional_rows_moved([1], win.ui.list_pokedex_regional.count())
    assert True in win.modified


def test_a_drag_repaints_the_list_to_match_the_model(win):
    win._on_regional_rows_moved([1], win.ui.list_pokedex_regional.count())
    consts, count = _consts(win)
    rows = _rows(win)
    entries = [c for k, c in rows if k == win.ROW_ENTRY]
    assert entries == consts
    kinds = [k for k, _ in rows]
    before = sum(1 for k, _ in rows[:kinds.index(win.ROW_BOUNDARY)]
                 if k == win.ROW_ENTRY)
    assert before == count


def test_dragging_a_header_does_nothing(win):
    before = _consts(win)
    win._on_regional_rows_moved([0], 3)      # row 0 is the first header
    assert _consts(win) == before


def test_a_drag_while_loading_is_ignored(win):
    before = _consts(win)
    win._loading_depth = 1
    win._on_regional_rows_moved([1], 6)
    assert _consts(win) == before


# ── the buttons ─────────────────────────────────────────────────────────────

def test_remove_moves_it_below_the_line_and_keeps_it(win):
    _select(win, "HOENN_DEX_BETA")
    win._regional_dex_remove()
    consts, count = _consts(win)
    assert count == COUNT - 1
    assert "HOENN_DEX_BETA" in consts
    assert consts[count] == "HOENN_DEX_BETA", "lands at the top of the rest"
    assert len(consts) == len(NAMES)


def test_add_moves_it_above_the_line(win):
    _select(win, "HOENN_DEX_EPSILON")
    win._regional_dex_add()
    consts, count = _consts(win)
    assert count == COUNT + 1
    assert consts[COUNT] == "HOENN_DEX_EPSILON", "lands at the end of the dex"


def test_add_then_remove_returns_to_the_start(win):
    before = _consts(win)
    _select(win, "HOENN_DEX_EPSILON")
    win._regional_dex_add()
    _select(win, "HOENN_DEX_EPSILON")
    win._regional_dex_remove()
    assert _consts(win) == before


def test_adding_one_already_in_the_dex_changes_nothing(win):
    before = _consts(win)
    _select(win, "HOENN_DEX_ALPHA")
    win._regional_dex_add()
    assert _consts(win) == before


def test_removing_one_already_out_changes_nothing(win):
    before = _consts(win)
    _select(win, "HOENN_DEX_ZETA")
    win._regional_dex_remove()
    assert _consts(win) == before


def test_pressing_a_button_with_a_header_selected_is_harmless(win):
    before = _consts(win)
    win.ui.list_pokedex_regional.setCurrentRow(0)
    win._regional_dex_add()
    win._regional_dex_remove()
    assert _consts(win) == before


def test_the_hint_survives_a_missing_status_bar(win):
    """The stub raises from statusBar(); a hint must not take the app down."""
    win.ui.list_pokedex_regional.setCurrentRow(0)
    win._regional_dex_add()          # would raise if the hint were unguarded


# ── the derived view is kept in step ────────────────────────────────────────

def test_the_derived_list_tracks_the_boundary(win):
    _select(win, "HOENN_DEX_BETA")
    win._regional_dex_remove()
    data = win.source_data.data["pokedex"].data
    derived = [e["hoenn_constant"] for e in data["regional_dex"]]
    consts, count = _consts(win)
    assert derived == consts[:count]


# ── no model means no editing ───────────────────────────────────────────────

def test_an_unreadable_dex_turns_editing_off(win):
    from PyQt6.QtWidgets import QAbstractItemView
    win.source_data.data["pokedex"].data = {
        "hoenn_dex_error": "the Hoenn section could not be read"
    }
    win._populate_regional_dex_list()
    lst = win.ui.list_pokedex_regional
    assert lst.dragDropMode() == QAbstractItemView.DragDropMode.NoDragDrop
    assert lst.dragEnabled() is False
    assert not [1 for i in range(lst.count())
                if lst.item(i).data(win.ROW_KIND_ROLE) == win.ROW_ENTRY]


def test_the_reason_is_shown_to_the_user(win):
    win.source_data.data["pokedex"].data = {
        "hoenn_dex_error": "the Hoenn section could not be read"
    }
    win._populate_regional_dex_list()
    lst = win.ui.list_pokedex_regional
    text = " ".join(lst.item(i).text() for i in range(lst.count()))
    assert "could not be read" in text


def test_editing_with_no_model_does_not_crash_or_invent_one(win):
    win.source_data.data["pokedex"].data = {"hoenn_dex_error": "nope"}
    win._populate_regional_dex_list()
    win._regional_dex_add()
    win._regional_dex_remove()
    win._on_regional_rows_moved([0], 1)
    assert win._hoenn_model() is None
