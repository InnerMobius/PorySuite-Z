"""Regression guard: runtime-built learnset row widgets must be scroll-guarded.

THE BUG (found 2026-10-29, fixed 2026-10-29). `install_scroll_guard_recursive(self)`
in `MainWindow.__init__` is a ONE-TIME SWEEP — it only reaches widgets that exist
at that moment. The learnset tables build a `QSpinBox` (level) and an editable
`QComboBox` (move) per ROW, per species, every time a species is selected, long
after the sweep has run. Those were unguarded, so one wheel notch over the
Level-Up list silently changed a level or a move — and
`save_species_learnset_table` reads those widgets back as the ONLY record of the
learnset, so the wrong data went into the game with nothing to notice.

Two rival wheel filters also existed (`ui/mainwindow.py::_combo_wheel_filter`,
`eventide/mainwindow.py::_combo_wheel_filter`). They swallowed the wheel only
while the widget LACKED focus, which `ui/custom_widgets/scroll_guard.py`
documents as the wrong rule. Both were removed.

THE RULE (do not simplify away):
  * A widget built after `__init__` must be guarded AT THE POINT OF CREATION.
    A recursive sweep cannot cover a widget that does not exist yet.
  * Use the SHARED `install_scroll_guard` — never a local focus-based filter.
  * The guard must keep the value AND let the wheel reach the parent, so the
    page still scrolls. That is why it returns True after `event.ignore()`.

If you touch the learnset row builders or scroll_guard.py, keep this green.
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
    from PyQt6.QtWidgets import QApplication, QComboBox, QSpinBox
    from PyQt6.QtCore import Qt, QEvent
    _QT = True
except Exception:
    _QT = False

pytestmark = pytest.mark.skipif(not _QT, reason="PyQt6 not available")

# The QApplication MUST be created at module import time, not inside a test.
# Creating widgets from an app built inside a test function crashes this Qt
# build under pytest (reproduced with a 4-line minimal case). This is the same
# pattern tests/test_draggable_palette_row.py:30 uses.
if _QT:
    _APP = QApplication.instance() or QApplication([])

MAINWINDOW = os.path.join(_ROOT, "ui", "mainwindow.py")
EV_MAINWINDOW = os.path.join(_ROOT, "eventide", "mainwindow.py")


def _method_bodies(path):
    with open(path, encoding="utf-8") as fh:
        lines = fh.readlines()
    starts = []
    for i, ln in enumerate(lines):
        m = re.match(r"^    def (\w+)\s*\(", ln)
        if m:
            starts.append((i, m.group(1)))
    out = {}
    for n, (i, name) in enumerate(starts):
        end = starts[n + 1][0] if n + 1 < len(starts) else len(lines)
        out[name] = "".join(lines[i:end])
    return out


# ── contract: the row builders guard what they create ────────────────────────

@pytest.fixture(scope="module")
def bodies():
    return _method_bodies(MAINWINDOW)


@pytest.mark.parametrize("name", [
    "_level_up_add_row",
    "_egg_add_row",
    "load_species_learnset_table",
])
def test_row_builder_guards_its_widgets(bodies, name):
    assert name in bodies, f"{name} no longer exists"
    body = bodies[name]
    made = len(re.findall(r"Q(?:SpinBox|ComboBox)\(\)", body))
    assert made > 0, f"{name} no longer builds row widgets — update this test"
    assert "_guard_wheel" in body, (
        f"{name} builds {made} value widget(s) at runtime but never calls "
        f"_guard_wheel. The one-time sweep in __init__ cannot reach them."
    )


def test_guard_helper_uses_the_shared_guard(bodies):
    assert "_guard_wheel" in bodies
    body = bodies["_guard_wheel"]
    assert "install_scroll_guard" in body, (
        "_guard_wheel must delegate to the shared guard, not reimplement one"
    )


@pytest.mark.parametrize("path", [MAINWINDOW, EV_MAINWINDOW])
def test_no_focus_based_rival_filter_remains(path):
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    assert "def _combo_wheel_filter" not in src, (
        f"{os.path.basename(path)} still defines the unsafe focus-based wheel "
        f"filter; it must use install_scroll_guard instead"
    )
    assert "Wheel and not obj.hasFocus()" not in src, (
        "a focus-based wheel block is the rule scroll_guard.py rejects"
    )


# ── behaviour: measured, on freshly created widgets ──────────────────────────

# NOTE ON METHOD. Constructing and dispatching a real `QWheelEvent` takes this
# Qt build down when it runs under pytest (reproduced repeatedly; the same code
# is fine in a standalone script). So these tests drive the guard's event filter
# DIRECTLY with a plain `QEvent(QEvent.Type.Wheel)`. That is faithful: the guard
# branches purely on `event.type()`, so this exercises the real decision without
# Qt's wheel-dispatch machinery. The end-to-end value-change behaviour was
# measured separately on 2026-10-29 — unguarded spin 50 -> 49, unguarded combo
# B -> A, guarded unchanged in both, and the event still reaching the container
# (see docs/BUGS.md, pass-2 entry).


def _guard_verdict(widget):
    """(filter_returned, event_was_accepted) for a Wheel event on `widget`."""
    from ui.custom_widgets.scroll_guard import _guard
    ev = QEvent(QEvent.Type.Wheel)
    ev.accept()                      # start accepted, like a real wheel event
    handled = _guard.eventFilter(widget, ev)
    return handled, ev.isAccepted()


@pytest.mark.parametrize("make", [
    lambda: QSpinBox(),
    lambda: QComboBox(),
])
def test_guard_swallows_the_wheel_and_leaves_it_unaccepted(make):
    """Swallowed (so the value cannot change) AND unaccepted (so Qt propagates
    it to the scroll area and the page still scrolls). Both halves matter."""
    from ui.custom_widgets.scroll_guard import install_scroll_guard
    w = make()
    install_scroll_guard(w)
    handled, accepted = _guard_verdict(w)
    assert handled is True, "the guard must consume the wheel event"
    assert accepted is False, (
        "the guard must leave the event UNACCEPTED so Qt carries it to the "
        "parent scroll area — otherwise the page stops scrolling"
    )


def test_guard_does_not_care_about_focus():
    """The removed rival filter only blocked while unfocused. The shared guard
    must block either way, which is the whole reason it replaced them."""
    from ui.custom_widgets.scroll_guard import install_scroll_guard
    w = QSpinBox()
    install_scroll_guard(w)
    w.show()
    QApplication.processEvents()
    assert _guard_verdict(w)[0] is True
    w.setFocus()
    QApplication.processEvents()
    assert _guard_verdict(w)[0] is True, (
        "a FOCUSED value widget must still be guarded"
    )


def test_guard_ignores_non_wheel_events():
    """It must not swallow clicks or keys — picking a value must still work."""
    from ui.custom_widgets.scroll_guard import install_scroll_guard, _guard
    w = QComboBox()
    install_scroll_guard(w)
    for t in (QEvent.Type.MouseButtonPress, QEvent.Type.KeyPress,
              QEvent.Type.FocusIn):
        ev = QEvent(t)
        assert _guard.eventFilter(w, ev) is False, (
            f"{t} must pass through — the guard is wheel-only"
        )


def test_guard_sets_strong_focus():
    """install_scroll_guard also sets StrongFocus; the removed rival filters
    did that by hand at each call site."""
    from ui.custom_widgets.scroll_guard import install_scroll_guard
    w = QComboBox()
    install_scroll_guard(w)
    assert w.focusPolicy() == Qt.FocusPolicy.StrongFocus
