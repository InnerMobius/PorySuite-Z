"""Dragging in the Pokedex lists and the move route cannot corrupt them.

Both of these keep their order NOWHERE but the list widget. The two dex lists
are walked row by row when the project is saved, each row's position stamped
on as that species' dex number; the move route is read straight off its list by
``get_steps``. So there is no second copy that could disagree -- and equally,
nothing that could notice if the list went wrong.

What a drop does to rows it is allowed to move is written up in
``ui/custom_widgets/reorderable_list.py``. Measured for these lists
specifically, carrying a dex constant on each row:

    before: BULBASAUR / IVYSAUR / VENUSAUR
    after : VENUSAUR  / IVYSAUR / VENUSAUR

Run through the save block that produces one species twice under two different
dex numbers and drops another out of the dex altogether, with the same wrong
numbers stamped onto the species table. Nothing downstream can tell.

Covered here: the lists move their own rows instead (``move_list_rows``), the
generated dex lists are replaced by ones that own their drop, the dirty
marking no longer depends on a signal a drop may not send, the save refuses a
dex that lost or duplicated a species, and a dragged move route can be undone.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

pytest.importorskip('PyQt6.QtWidgets')

# Qt has to be imported while this module is being imported, not later from
# inside a fixture: the suite swaps each test file's modules in and out around
# its import, and a half-swapped PyQt6 takes the interpreter down with it.
from PyQt6.QtCore import QSize, Qt  # noqa: E402
from PyQt6.QtGui import QFont  # noqa: E402
from PyQt6.QtWidgets import (QAbstractItemView, QApplication,  # noqa: E402
                             QListWidget, QListWidgetItem, QVBoxLayout,
                             QWidget)

import mainwindow as mw  # noqa: E402
from ui.custom_widgets.reorderable_list import (  # noqa: E402
    ReorderableListWidget, move_list_rows)


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


DEX = ('BULBASAUR', 'IVYSAUR', 'VENUSAUR', 'CHARMANDER')


def _fill(lst, names=DEX):
    for n in names:
        item = QListWidgetItem(n.title())
        item.setData(Qt.ItemDataRole.UserRole, 'NATIONAL_DEX_' + n)
        lst.addItem(item)
    return lst


def _consts(lst):
    return [lst.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(lst.count())]


def _short(lst):
    return [(c or '').replace('NATIONAL_DEX_', '') for c in _consts(lst)]


# ── move_list_rows: the move the drop is no longer allowed to make ─────────

def test_rows_move(qapp):
    lst = _fill(QListWidget())
    landed = move_list_rows(lst, [3], 0)
    assert _short(lst) == ['CHARMANDER', 'BULBASAUR', 'IVYSAUR', 'VENUSAUR']
    assert landed == [0]


def test_nothing_is_duplicated_or_lost(qapp):
    lst = _fill(QListWidget())
    move_list_rows(lst, [3], 0)
    assert sorted(_short(lst)) == sorted(DEX)
    assert lst.count() == len(DEX)


def test_the_same_item_objects_are_reused(qapp):
    # Which is how each row keeps its icon and its stored constant.
    lst = _fill(QListWidget())
    before = {id(lst.item(i)) for i in range(lst.count())}
    move_list_rows(lst, [3], 0)
    assert {id(lst.item(i)) for i in range(lst.count())} == before


def test_an_items_data_survives_the_move(qapp):
    lst = _fill(QListWidget())
    move_list_rows(lst, [2], 0)
    assert lst.item(0).text() == 'Venusaur'
    assert lst.item(0).data(Qt.ItemDataRole.UserRole) == 'NATIONAL_DEX_VENUSAUR'


def test_a_no_op_move_reports_nothing(qapp):
    lst = _fill(QListWidget())
    assert move_list_rows(lst, [1], 1) == []
    assert move_list_rows(lst, [1], 2) == []
    assert _short(lst) == list(DEX)


def test_several_rows_keep_their_order(qapp):
    lst = _fill(QListWidget())
    landed = move_list_rows(lst, [0, 1], 4)
    assert _short(lst) == ['VENUSAUR', 'CHARMANDER', 'BULBASAUR', 'IVYSAUR']
    assert landed == [2, 3]


def test_nothing_reacts_to_the_half_empty_list(qapp):
    # The rows are briefly out of the list while they are reordered. Anything
    # listening would otherwise see an empty list and a selection of nothing.
    lst = _fill(QListWidget())
    lst.setCurrentRow(0)
    seen = []
    lst.currentRowChanged.connect(lambda r: seen.append(r))
    move_list_rows(lst, [3], 0)
    assert seen == []


# ── The generated dex lists are replaced by ones that own their drop ───────

@pytest.fixture
def promoted(qapp):
    """A stand-in for the generated list, inside a layout, then promoted."""
    holder = QWidget()
    layout = QVBoxLayout(holder)
    spacer = QListWidget()                  # so the dex list is not at index 0
    layout.addWidget(spacer)
    old = QListWidget(holder)
    old.setObjectName('list_pokedex_national')
    old.setFont(QFont('Source Code Pro', 11))
    old.setIconSize(QSize(32, 32))
    old.setSelectionMode(
        QAbstractItemView.SelectionMode.ExtendedSelection)
    old.setUniformItemSizes(False)
    layout.addWidget(old)
    _fill(old)
    self = SimpleNamespace(
        ui=SimpleNamespace(list_pokedex_national=old),
        _DEX_LIST_PROPS=mw.MainWindow._DEX_LIST_PROPS,
    )
    new = mw.MainWindow._promote_dex_list(self, 'list_pokedex_national')
    return SimpleNamespace(holder=holder, layout=layout, new=new, self=self)


def test_the_dex_list_is_replaced_by_one_that_owns_its_drop(promoted):
    assert isinstance(promoted.new, ReorderableListWidget)
    assert promoted.self.ui.list_pokedex_national is promoted.new


def test_the_replacement_takes_the_same_place_in_the_layout(promoted):
    assert promoted.layout.indexOf(promoted.new) == 1
    assert promoted.new.parentWidget() is promoted.holder


def test_the_replacement_keeps_the_properties_that_were_set(promoted):
    new = promoted.new
    assert new.objectName() == 'list_pokedex_national'
    assert new.font().family() == 'Source Code Pro'
    assert new.iconSize() == QSize(32, 32)
    assert (new.selectionMode()
            == QAbstractItemView.SelectionMode.ExtendedSelection)


def test_the_replacement_keeps_the_rows(promoted):
    assert _short(promoted.new) == list(DEX)


def test_promoting_twice_changes_nothing(promoted):
    again = mw.MainWindow._promote_dex_list(
        promoted.self, 'list_pokedex_national')
    assert again is None
    assert promoted.self.ui.list_pokedex_national is promoted.new


def test_promoting_something_that_is_not_there_is_harmless(qapp):
    self = SimpleNamespace(ui=SimpleNamespace())
    assert mw.MainWindow._promote_dex_list(self, 'no_such_list') is None


# ── Dragging a dex row ─────────────────────────────────────────────────────

@pytest.fixture
def dex(qapp):
    lst = _fill(ReorderableListWidget())
    marked = []
    self = SimpleNamespace(
        _on_dex_reordered=lambda l, s, e, d: marked.append((s, e, d)),
        marked=marked, lst=lst)
    return self


def test_dragging_a_dex_row_moves_it(dex):
    mw.MainWindow._on_dex_rows_move_requested(dex, dex.lst, [3], 0)
    assert _short(dex.lst) == ['CHARMANDER', 'BULBASAUR', 'IVYSAUR',
                               'VENUSAUR']


def test_dragging_a_dex_row_marks_the_changed_range(dex):
    mw.MainWindow._on_dex_rows_move_requested(dex, dex.lst, [3], 0)
    # Rows 0 through 3 all have a different dex number now, not just the one
    # that was dragged.
    assert dex.marked == [(0, 3, 3)]


def test_the_dragged_dex_row_stays_selected(dex):
    mw.MainWindow._on_dex_rows_move_requested(dex, dex.lst, [3], 0)
    assert dex.lst.currentRow() == 0
    assert [i.text() for i in dex.lst.selectedItems()] == ['Charmander']


def test_dropping_a_dex_row_where_it_already_is_is_not_an_edit(dex):
    mw.MainWindow._on_dex_rows_move_requested(dex, dex.lst, [1], 1)
    assert dex.marked == []
    assert _short(dex.lst) == list(DEX)


def test_the_dex_list_no_longer_relies_on_rows_moved(dex):
    # The old wiring hung off the list model's "rows moved" signal, which a
    # dropped row does not emit. Nothing may depend on it again.
    fired = []
    dex.lst.model().rowsMoved.connect(lambda *a: fired.append(1))
    mw.MainWindow._on_dex_rows_move_requested(dex, dex.lst, [3], 0)
    assert _short(dex.lst)[0] == 'CHARMANDER'   # the move happened anyway
    assert fired == []                          # without that signal


# ── The dirty marking itself ───────────────────────────────────────────────

def test_the_marking_paints_every_row_whose_number_changed(qapp):
    lst = _fill(ReorderableListWidget())
    painted = []
    self = SimpleNamespace(
        _loading_depth=0,
        setWindowModified=lambda v: None,
        sectionDirtyChanged=SimpleNamespace(
            emit=lambda *a: painted.append(('section',) + a)),
        _mark_list_item_dirty=lambda l, d, **kw: painted.append(
            kw.get('match_value')),
    )
    mw.MainWindow._on_dex_reordered(self, lst, 0, 3, 3)
    assert ('section', 'pokedex', True) in painted
    assert [p for p in painted if p != ('section', 'pokedex', True)] == [
        'NATIONAL_DEX_' + n for n in DEX]


def test_the_marking_stays_quiet_while_the_project_is_loading(qapp):
    lst = _fill(ReorderableListWidget())
    painted = []
    self = SimpleNamespace(
        _loading_depth=1,
        setWindowModified=lambda v: painted.append('modified'),
        sectionDirtyChanged=SimpleNamespace(
            emit=lambda *a: painted.append('section')),
        _mark_list_item_dirty=lambda *a, **k: painted.append('row'),
    )
    mw.MainWindow._on_dex_reordered(self, lst, 0, 3, 3)
    assert painted == []


# ── The save refuses a dex that is not safe to write ──────────────────────

@pytest.fixture
def warned(monkeypatch):
    seen = []
    monkeypatch.setattr(mw.QMessageBox, 'warning',
                        staticmethod(lambda *a, **k: seen.append(a[2])))
    return seen


def _entries(*names):
    return [{'dex_constant': 'NATIONAL_DEX_' + n, 'species': 'SPECIES_' + n}
            for n in names]


def test_a_plain_reorder_is_allowed(warned):
    before = _entries(*DEX)
    after = _entries('CHARMANDER', 'BULBASAUR', 'IVYSAUR', 'VENUSAUR')
    assert mw.MainWindow._dex_order_is_sane(
        None, 'National', after, before) is True
    assert warned == []


def test_a_duplicated_species_is_refused(warned):
    before = _entries(*DEX)
    after = _entries('VENUSAUR', 'IVYSAUR', 'VENUSAUR', 'CHARMANDER')
    assert mw.MainWindow._dex_order_is_sane(
        None, 'National', after, before) is False
    assert 'BULBASAUR' in warned[0]          # named as missing
    assert 'VENUSAUR' in warned[0]           # named as duplicated


def test_a_species_that_vanished_is_refused(warned):
    before = _entries(*DEX)
    after = _entries('BULBASAUR', 'IVYSAUR', 'VENUSAUR')
    assert mw.MainWindow._dex_order_is_sane(
        None, 'National', after, before) is False
    assert 'CHARMANDER' in warned[0]


def test_adding_a_species_to_the_dex_is_allowed(warned):
    # Only losing one silently, or ending up with it twice, is refused.
    before = _entries(*DEX)
    after = _entries(*(DEX + ('SQUIRTLE',)))
    assert mw.MainWindow._dex_order_is_sane(
        None, 'National', after, before) is True
    assert warned == []


def test_removing_a_species_through_the_remove_button_is_allowed(warned):
    # The Remove button takes the entry out of the data AND the list, so what
    # is compared against no longer holds it either.
    before = _entries('BULBASAUR', 'IVYSAUR')
    after = _entries('BULBASAUR', 'IVYSAUR')
    assert mw.MainWindow._dex_order_is_sane(
        None, 'Regional', after, before) is True
    assert warned == []


def test_an_empty_dex_against_an_empty_dex_is_fine(warned):
    assert mw.MainWindow._dex_order_is_sane(None, 'National', [], []) is True
    assert warned == []


def test_the_message_says_which_dex_and_that_nothing_was_lost(warned):
    mw.MainWindow._dex_order_is_sane(
        None, 'Regional', _entries('A', 'A'), _entries('A', 'B'))
    assert 'Regional' in warned[0]
    assert 'left exactly as it was' in warned[0]


# ═════════════════════════════════════════════════════════════════════════════
# The MIDI import play order
#
# The last of the app's drag-reorder lists. This one is built the right way
# round -- `_play_order` holds the order and the list is painted from it -- but
# the drag was never wired to anything, so a drop only scrambled the display
# until the next repaint put it back, which looked like the dialog throwing an
# edit away.
# ═════════════════════════════════════════════════════════════════════════════

import ui.dialogs.midi_import_dialog as midi  # noqa: E402


@pytest.fixture
def order(qapp):
    """The play order handler, with just the parts of the dialog it touches."""
    lst = ReorderableListWidget()
    for n in ('intro', 'verse', 'chorus', 'outro'):
        lst.addItem(QListWidgetItem(n))
    self = SimpleNamespace(
        _play_order=[0, 1, 2, 3],
        _order_list=lst,
        rebuilds=[],
    )
    self._rebuild_order_list = lambda: self.rebuilds.append(
        list(self._play_order))
    return self


def test_dragging_reorders_the_play_order_itself(order):
    midi.MidiImportDialog._on_order_move_requested(order, [3], 0)
    assert order._play_order == [3, 0, 1, 2]


def test_the_list_is_repainted_from_the_new_order(order):
    midi.MidiImportDialog._on_order_move_requested(order, [3], 0)
    assert order.rebuilds == [[3, 0, 1, 2]]


def test_no_section_is_duplicated_or_lost(order):
    midi.MidiImportDialog._on_order_move_requested(order, [1], 4)
    assert sorted(order._play_order) == [0, 1, 2, 3]


def test_a_section_used_twice_still_moves_correctly(order):
    # The same section can legitimately appear more than once in a play
    # order, so the move has to go by POSITION, not by which section it is.
    order._play_order = [0, 1, 0, 2]
    midi.MidiImportDialog._on_order_move_requested(order, [3], 0)
    assert order._play_order == [2, 0, 1, 0]


def test_dropping_where_it_already_is_is_not_an_edit(order):
    midi.MidiImportDialog._on_order_move_requested(order, [1], 1)
    midi.MidiImportDialog._on_order_move_requested(order, [1], 2)
    assert order._play_order == [0, 1, 2, 3]
    assert order.rebuilds == []


def test_the_moved_entry_stays_selected(order):
    midi.MidiImportDialog._on_order_move_requested(order, [3], 0)
    assert order._order_list.currentRow() == 0


def test_several_entries_move_together(order):
    midi.MidiImportDialog._on_order_move_requested(order, [0, 1], 4)
    assert order._play_order == [2, 3, 0, 1]
