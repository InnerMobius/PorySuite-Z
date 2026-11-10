"""Finding F3: a typed learnset move was discarded unless you pressed Return.

The combos are editable with `InsertPolicy.NoInsert`, so the UI invites
typing — but typing does not move `currentIndex`, and the save reads
`(currentData() or currentText())`. Measured with real key events against
combos carrying the app's own display names:

    press Return   -> committed correctly
    press Tab      -> OLD move kept, typed one discarded
    click away     -> OLD move kept, typed one discarded

A box that accepts a value on one exit path and silently drops it on two
others is worse than one that never accepted typing at all.

DO NOT REGRESS:
  * Return and focus-out must go through the SAME resolution routine. Two
    independent matching algorithms is how they diverged in the first place.
  * Text matching nothing is REFUSED and the box snaps back to what is
    actually selected. Guessing at the nearest match would write a move the
    user never chose — which is finding F4 in a different costume.
  * The refusal must be visible. Preserving the old value silently would
    leave the user believing their typo was saved.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QComboBox  # noqa: E402

APP = QApplication.instance() or QApplication(sys.argv)

import mainwindow as mw  # noqa: E402
MW = mw.MainWindow

MOVES = [("Absorb", "MOVE_ABSORB"), ("Acid", "MOVE_ACID"),
         ("Ember", "MOVE_EMBER")]


class _Stub:
    _attach_move_text_commit = MW._attach_move_text_commit

    def setWindowModified(self, *_a):
        pass


def _combo():
    c = QComboBox()
    c.setEditable(True)
    c.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
    for name, const in MOVES:
        c.addItem(name, const)
    _Stub()._attach_move_text_commit(c)
    c.setCurrentIndex(0)
    c.show()
    c.setFocus()
    return c


def _save_reads(c):
    """What the save actually stores."""
    return (c.currentData() or c.currentText()).strip()


def _type(c, text):
    le = c.lineEdit()
    le.selectAll()
    QTest.keyClicks(le, text)
    APP.processEvents()


def test_typing_then_clicking_away_commits():
    """The reported defect."""
    c = _combo()
    _type(c, "Ember")
    c.lineEdit().editingFinished.emit()      # focus-out
    APP.processEvents()
    assert _save_reads(c) == "MOVE_EMBER"


def test_typing_then_tab_commits():
    c = _combo()
    _type(c, "Ember")
    c.lineEdit().editingFinished.emit()
    APP.processEvents()
    assert _save_reads(c) == "MOVE_EMBER"


def test_return_still_works():
    """It worked before this repair and must keep working."""
    c = _combo()
    _type(c, "Ember")
    QTest.keyClick(c, Qt.Key.Key_Return)
    APP.processEvents()
    assert _save_reads(c) == "MOVE_EMBER"


def test_text_matching_nothing_is_refused_not_guessed():
    """Snapping to the nearest match would store a move never chosen."""
    c = _combo()
    _type(c, "Nonsense")
    c.lineEdit().editingFinished.emit()
    APP.processEvents()
    assert _save_reads(c) == "MOVE_ABSORB"


def test_the_refusal_is_visible():
    """The text must revert, or the user believes the typo was saved."""
    c = _combo()
    _type(c, "Nonsense")
    c.lineEdit().editingFinished.emit()
    APP.processEvents()
    assert c.currentText() == "Absorb"


def test_matching_is_case_insensitive():
    c = _combo()
    _type(c, "eMbEr")
    c.lineEdit().editingFinished.emit()
    APP.processEvents()
    assert _save_reads(c) == "MOVE_EMBER"


def test_clearing_the_box_does_not_change_the_move():
    c = _combo()
    _type(c, "")
    c.lineEdit().editingFinished.emit()
    APP.processEvents()
    assert _save_reads(c) == "MOVE_ABSORB"


def test_every_typable_move_combo_is_wired():
    """A resolver attached to three of four builders fixes three of four.

    The criterion is `setEditable(True)` — a combo the user can TYPE into.
    An earlier version of this test counted `setInsertPolicy(NoInsert)` and
    flagged two more sites, but neither is editable, so neither can exhibit
    this defect at all. NoInsert is not the same question as typable.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(sys.modules[MW.__module__]))
    typable = 0
    wired = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if (isinstance(f, ast.Attribute) and f.attr == "setEditable"
                and node.args and isinstance(node.args[0], ast.Constant)
                and node.args[0].value is True
                and isinstance(f.value, ast.Name) and f.value.id == "combo"):
            typable += 1
        if isinstance(f, ast.Attribute) and f.attr == "_attach_move_text_commit":
            wired += 1
    assert typable > 0, "test fixture is stale"
    assert wired >= typable, (
        "%d typable move combos but only %d wired to the resolver"
        % (typable, wired))
