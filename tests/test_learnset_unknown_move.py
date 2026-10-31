"""Finding F4: a learnset move the project lacks became Absorb.

The load did::

    idx = combo.findData(move_const)
    combo.setCurrentIndex(idx if idx >= 0 else 0)

Index 0 is not a neutral fallback. The option list is
`sorted(moves_map.keys())` with MOVE_NONE inserted only if missing — and it is
never missing — so in this project index 0 is MOVE_ABSORB and MOVE_NONE sits
at 195. A learnset entry naming a move the project no longer has loaded as
Absorb, and the next save wrote Absorb into that species' learnset.

DO NOT REGRESS:
  * Never substitute ANY valid move for an unresolved one. Not index 0, and
    not MOVE_NONE either — the save SKIPS rows whose move is MOVE_NONE, so
    that would turn "wrong move written" into "row silently deleted".
  * The unknown constant must survive the round trip, because the save reads
    `combo.currentData()`.
  * The row must be visibly marked. Preserving the value silently would leave
    the user unable to find what needs fixing.
  * A move the project DOES have must still select normally.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QComboBox  # noqa: E402

APP = QApplication.instance() or QApplication(sys.argv)

import mainwindow as mw  # noqa: E402
MW = mw.MainWindow

# Mirrors the real option list: sorted constants, MOVE_NONE NOT at index 0.
OPTIONS = ["MOVE_ABSORB", "MOVE_ACID", "MOVE_EMBER", "MOVE_NONE",
           "MOVE_TACKLE"]
UNKNOWN = "MOVE_A_MOVE_THIS_PROJECT_LACKS"


class _Stub:
    _UNRESOLVED_MOVE_PREFIX = MW._UNRESOLVED_MOVE_PREFIX
    _select_learnset_move = MW._select_learnset_move
    _note_unresolved_move = MW._note_unresolved_move


def _combo():
    c = QComboBox()
    for m in OPTIONS:
        c.addItem(m.replace("MOVE_", "").title(), m)
    return c


def _save_reads(combo):
    """ui/mainwindow.py — what the save actually stores."""
    return (combo.currentData() or combo.currentText()).strip()


def test_index_zero_is_not_move_none():
    """The premise. If this ever stops holding the defect changes shape."""
    assert OPTIONS[0] != "MOVE_NONE"


def test_a_known_move_still_selects_normally():
    c = _combo()
    assert _Stub()._select_learnset_move(c, "MOVE_EMBER") is None
    assert c.currentData() == "MOVE_EMBER"
    assert _save_reads(c) == "MOVE_EMBER"


def test_an_unknown_move_is_not_replaced_by_absorb():
    c = _combo()
    _Stub()._select_learnset_move(c, UNKNOWN)
    assert c.currentData() != "MOVE_ABSORB"
    assert c.currentData() != OPTIONS[0]


def test_an_unknown_move_is_not_replaced_by_move_none():
    """MOVE_NONE rows are SKIPPED by the save — that would delete the row."""
    c = _combo()
    _Stub()._select_learnset_move(c, UNKNOWN)
    assert c.currentData() != "MOVE_NONE"


def test_the_unknown_constant_survives_the_save_read():
    c = _combo()
    _Stub()._select_learnset_move(c, UNKNOWN)
    assert _save_reads(c) == UNKNOWN


def test_the_unresolved_row_is_visibly_marked():
    c = _combo()
    _Stub()._select_learnset_move(c, UNKNOWN)
    assert c.currentText().startswith(MW._UNRESOLVED_MOVE_PREFIX)
    assert UNKNOWN in c.currentText()


def test_the_unresolved_move_is_reported():
    c = _combo()
    s = _Stub()
    unresolved = s._select_learnset_move(c, UNKNOWN)
    assert unresolved == UNKNOWN
    s._note_unresolved_move("SPECIES_OCTOROK", unresolved)
    assert UNKNOWN in s._unresolved_learnset_moves
    assert "SPECIES_OCTOROK" in s._unresolved_learnset_moves[UNKNOWN]


def test_an_empty_constant_falls_back_to_move_none():
    """An absent value is genuinely 'no move', unlike an unknown one."""
    c = _combo()
    assert _Stub()._select_learnset_move(c, "") is None
    assert c.currentData() == "MOVE_NONE"


def test_no_learnset_builder_falls_back_to_index_zero():
    """Asked of the parser so a quoted example in a docstring cannot match.

    SCOPED to the three learnset row builders. The first version of this test
    scanned the whole module and flagged two unrelated combos — a form
    selector where index 0 IS the base form, and an innate-ability box — which
    would have been a false accusation, and the sort of over-broad check that
    gets a real finding dismissed.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(sys.modules[MW.__module__]))
    builders = {"load_species_learnset_table", "_level_up_add_row",
                "_egg_add_row"}
    offenders = []
    for fn in ast.walk(tree):
        if not (isinstance(fn, ast.FunctionDef) and fn.name in builders):
            continue
        for node in ast.walk(fn):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "setCurrentIndex"
                    and node.args):
                continue
            arg = node.args[0]
            if (isinstance(arg, ast.IfExp)
                    and isinstance(arg.orelse, ast.Constant)
                    and arg.orelse.value == 0):
                offenders.append((fn.name, node.lineno))
    assert not offenders, (
        "a learnset combo still falls back to index 0: %s" % offenders)
