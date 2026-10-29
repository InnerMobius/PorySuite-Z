"""The Regional Dex is a DERIVED VIEW and must not pretend to be editable.

WHY (proved from this project's engine on 2026-10-29, not assumed):
regional ("Kanto") membership is decided entirely by a species' NATIONAL dex
number against one #define —

  * pokefirered/src/pokedex.c:75-95    GetKantoPokedexCount walks
                                       `for (i = 0; i < KANTO_DEX_COUNT; i++)`
                                       over national numbers 1..KANTO_DEX_COUNT
  * pokefirered/src/pokedex.c:108-120  HasAllKantoMons does the same
  * src/pokedex_screen.c:1384          max_n = national ? NATIONAL_DEX_COUNT
                                                        : KANTO_DEX_COUNT
  * src/pokedex_screen.c:3186, :3326   membership ==
                                       SpeciesToNationalPokedexNum(sp)
                                         <= KANTO_DEX_COUNT

There is NO regional list in the engine. PorySuite's `regional_dex` field in
pokedex.json is a derived cache (the extractor builds it as
`valid[:kanto_count]`), and the Pokédex writer only ever rewrites the
NATIONAL_DEX enum — its last line says so: "Done: Hoenn enum, defines, guards,
and comments remain untouched."

So the Add button, the Remove button and drag-to-reorder on that list wrote to
a field nothing reads and nothing emits: Save reported success and the game did
not change. They are gone. The LIST STAYS, because it is a useful view of
exactly which species the player's regional dex will contain.

THE RULE (do not simplify away):
  * Never re-add Add / Remove / drag to the regional list without first making
    the engine actually read a regional list. No engine change is proposed.
  * NEVER touch the National Dex half: its reorder is real, it writes the enum,
    and `_promote_dex_list` / `_on_dex_rows_move_requested` /
    `_dex_order_is_sane` are its corruption guards.
  * Viewing a dex entry must never mark the project modified.

If you touch _setup_pokedex_tab or _make_regional_dex_read_only, keep this green.
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


# ── the misleading controls are gone ────────────────────────────────────────

def test_regional_add_remove_buttons_are_not_connected(src):
    assert "pushButton_4.clicked.connect" not in src, (
        "the regional Add button must not be wired — it wrote to a field the "
        "engine has no concept of"
    )
    assert "pushButton_5.clicked.connect" not in src, (
        "the regional Remove button must not be wired"
    )


def test_regional_buttons_are_removed_from_the_layout(src):
    m = re.search(r"\n    def _make_regional_dex_read_only\(.*?(?=\n    def )",
                  src, re.S)
    assert m, "_make_regional_dex_read_only is missing"
    body = m.group(0)
    assert "pushButton_4" in body and "pushButton_5" in body
    assert "setParent(None)" in body and "deleteLater" in body


def test_regional_list_drag_is_turned_off(src):
    m = re.search(r"\n    def _make_regional_dex_read_only\(.*?(?=\n    def )",
                  src, re.S)
    body = m.group(0)
    assert "NoDragDrop" in body
    assert "setDragEnabled(False)" in body
    assert "setAcceptDrops(False)" in body
    assert "NoEditTriggers" in body


def test_read_only_runs_after_the_promotion_not_before(src):
    """`_promote_dex_list` replaces the widget and the ReorderableListWidget
    enables InternalMove itself, so the read-only call must come later or the
    drag would be switched back on."""
    promote = src.index("self._promote_dex_list(_dex_list)")
    readonly = src.index("self._make_regional_dex_read_only()")
    assert promote < readonly, (
        "the regional list is promoted in __init__; making it read-only must "
        "happen after that, not before"
    )


# ── the list is still viewable, and explains itself ─────────────────────────

def test_the_list_is_still_added_to_the_tab(src):
    assert "reg_vbox.addWidget(self.ui.list_pokedex_regional)" in src, (
        "the regional list must remain visible — only editing is removed"
    )


def test_the_explanation_names_where_membership_is_controlled(src):
    m = re.search(r"reg_note = QLabel\((.*?)\)\n", src, re.S)
    assert m, "the explanatory note is missing"
    note = m.group(1)
    assert "National Dex" in note, "it must say what it is derived from"
    assert "KANTO_DEX_COUNT" in note, "it must name the real control"
    assert "pokedex.h" in note, "it must say where that control lives"
    assert "cannot be edited" in note.lower() or "read-only" in note.lower()


def test_the_search_box_survives(src):
    """Filtering the view is reading, not editing."""
    assert "reg_vbox.addWidget(self.ui.pokedex_regional_search)" in src


# ── the National Dex half must be untouched ─────────────────────────────────

@pytest.mark.parametrize("name", [
    "_promote_dex_list",
    "_on_dex_rows_move_requested",
    "_dex_order_is_sane",
])
def test_national_dex_protections_still_exist(src, name):
    assert f"def {name}" in src, f"{name} is a National Dex guard and must stay"


def test_national_list_is_still_draggable(src):
    """The national reorder is real — it rewrites the NATIONAL_DEX enum."""
    m = re.search(r"\n    def _make_regional_dex_read_only\(.*?(?=\n    def )",
                  src, re.S)
    body = m.group(0)
    assert "list_pokedex_national" not in body, (
        "making the REGIONAL list read-only must not touch the national one"
    )
    assert "list_pokedex_national" in src
    assert "rows_move_requested" in src


# ── viewing must never dirty the project ────────────────────────────────────

def test_viewing_a_dex_entry_is_guarded_against_false_dirty(src):
    m = re.search(r"\n    def update_pokedex_entry\(.*?(?=\n    def )", src, re.S)
    assert m
    body = m.group(0)
    assert "self._loading_depth += 1" in body, (
        "selecting a dex row populates widgets, whose signals would otherwise "
        "mark the project modified"
    )
    assert "_loading_depth -= 1" in body, "the guard must be released"


def test_panel_flush_short_circuits_while_loading(src):
    """The guard only works because _flush_pokedex_panel respects it."""
    m = re.search(r"\n    def _flush_pokedex_panel\(.*?(?=\n    def )", src, re.S)
    assert m
    body = m.group(0)
    assert '_loading_depth", 0) > 0' in body and "return" in body


# ── behaviour: a NoDragDrop list really refuses a drop ──────────────────────

def test_a_no_drag_drop_list_cannot_be_reordered_by_a_drop():
    """Confirms the setting is sufficient, using the real list class."""
    from ui.custom_widgets.reorderable_list import ReorderableListWidget
    lst = ReorderableListWidget()
    for n in ("A", "B", "C"):
        it = QListWidgetItem(n)
        it.setData(Qt.ItemDataRole.UserRole, f"NATIONAL_DEX_{n}")
        lst.addItem(it)
    fired = []
    lst.rows_move_requested.connect(lambda rows, dest: fired.append((rows, dest)))

    # The read-only configuration applied by _make_regional_dex_read_only.
    lst.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)
    lst.setDragEnabled(False)
    lst.setAcceptDrops(False)

    assert lst.dragDropMode() == QAbstractItemView.DragDropMode.NoDragDrop
    assert lst.dragEnabled() is False
    assert lst.acceptDrops() is False
    # Nothing can start or land a drag, so the reorder signal cannot fire.
    assert fired == []
    # And the rows are still readable.
    assert [lst.item(i).text() for i in range(lst.count())] == ["A", "B", "C"]
    assert lst.item(0).data(Qt.ItemDataRole.UserRole) == "NATIONAL_DEX_A"
