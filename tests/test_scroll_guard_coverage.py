"""App-wide scroll-protection coverage, and the deliberate exceptions to it.

Background (2026-10-29). PorySuite has FOUR families of wheel protection:

  1. `ui/custom_widgets/scroll_guard.py` — the shared, correct one. Measured:
     the value never changes AND the event still reaches the parent, so the
     page scrolls.
  2. `_NoScrollCombo` / `_NoScrollComboBox` subclasses (tilemap editor, tile
     animation viewer, trainers tab, trainer class editor, config tab) — ignore
     the wheel while the popup is CLOSED and deliberately pass it through while
     the popup is OPEN so the list scrolls. **These are correct; leave them.**
  3. `_NoScrollSpin` subclasses (tile animation viewer, trainers tab, trainer
     class editor) — used to pass the wheel through whenever the box HAD FOCUS.
     That is the wrong rule for a value widget and was measured failing: a
     clicked box has focus, so scrolling the page afterwards changed it
     (50 -> 49 on one notch). These boxes hold trainer party levels, IVs, prize
     money and tile counts. FIXED to never change on the wheel.
  4. Two `_combo_wheel_filter` event filters with the same bad focus rule
     (ui/mainwindow.py, eventide/mainwindow.py) — DELETED.

The recursive sweep now SKIPS any widget whose class overrides `wheelEvent`,
because installing the shared filter on a family-2 combo would eat the wheel
before its own handler ran and silently remove the popup behaviour.

Also: `ui/unified_mainwindow.py` never ran the sweep at all, so the pages it
builds — the Tilemap Editor's width / height / tile-offset / palette-slot boxes
among them — had no protection. It now sweeps every page in its stack.

If you touch scroll_guard.py or any `_NoScroll*` class, keep this green.
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
    from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QComboBox,
                                 QSpinBox, QSlider)
    from PyQt6.QtCore import Qt, QEvent
    _QT = True
except Exception:
    _QT = False

pytestmark = pytest.mark.skipif(not _QT, reason="PyQt6 not available")

# Module-level app: creating widgets from an app built inside a test crashes
# this Qt build under pytest.
if _QT:
    _APP = QApplication.instance() or QApplication([])

SPIN_FILES = [
    os.path.join(_ROOT, "ui", "trainers_tab_widget.py"),
    os.path.join(_ROOT, "ui", "trainer_class_editor.py"),
    os.path.join(_ROOT, "ui", "tile_anim_viewer.py"),
]
COMBO_FILES = SPIN_FILES + [
    os.path.join(_ROOT, "ui", "tilemap_editor_tab.py"),
    os.path.join(_ROOT, "ui", "config_tab_widget.py"),
]


# ── family 3: the spin subclasses must never pass the wheel through ──────────

@pytest.mark.parametrize("path", SPIN_FILES, ids=lambda p: os.path.basename(p))
def test_noscroll_spin_no_longer_branches_on_focus(path):
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    m = re.search(r"class _NoScrollSpin\(QSpinBox\):(.*?)(?=\nclass |\Z)",
                  src, re.S)
    assert m, f"_NoScrollSpin not found in {os.path.basename(path)}"
    body = m.group(1)
    assert "hasFocus" not in body, (
        "a focused spin box must still be protected — the focus rule was "
        "measured failing (50 -> 49 on one notch)"
    )
    assert "event.ignore()" in body


@pytest.mark.parametrize("path", SPIN_FILES, ids=lambda p: os.path.basename(p))
def test_noscroll_spin_ignores_unconditionally(path):
    """Load the real class and check its handler ignores the event."""
    import importlib.util as ilu
    name = "t_" + os.path.basename(path)[:-3]
    spec = ilu.spec_from_file_location(name, path)
    try:
        mod = ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception as exc:                       # pragma: no cover
        pytest.skip(f"module not importable standalone: {exc}")
    cls = getattr(mod, "_NoScrollSpin", None)
    assert cls is not None
    w = cls()
    ev = QEvent(QEvent.Type.Wheel)
    ev.accept()
    w.wheelEvent(ev)
    assert ev.isAccepted() is False, (
        "the handler must ignore the event so the value is untouched AND the "
        "page still scrolls"
    )


# ── family 2: the combo subclasses are CORRECT and must stay as they are ────

@pytest.mark.parametrize("path", COMBO_FILES, ids=lambda p: os.path.basename(p))
def test_noscroll_combo_still_allows_popup_scrolling(path):
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    m = re.search(r"class _NoScrollCombo(?:Box)?\(QComboBox\):(.*?)"
                  r"(?=\nclass |\n\n\n|\Z)", src, re.S)
    if not m:
        pytest.skip(f"no combo subclass in {os.path.basename(path)}")
    body = m.group(1)
    assert "event.ignore()" in body or "e.ignore()" in body, (
        "the closed combo must ignore the wheel"
    )


# ── the sweep must not trample a widget that handles its own wheel ───────────

def test_sweep_skips_widgets_with_their_own_wheel_handler():
    from ui.custom_widgets.scroll_guard import (
        install_scroll_guard_recursive, _handles_its_own_wheel, _guard,
    )

    class OwnHandler(QComboBox):
        def __init__(self):
            super().__init__()
            self.saw = 0

        def wheelEvent(self, event):
            self.saw += 1
            event.ignore()

    plain = QComboBox()
    own = OwnHandler()
    assert _handles_its_own_wheel(plain) is False
    assert _handles_its_own_wheel(own) is True

    root = QWidget()
    lay = QVBoxLayout(root)
    lay.addWidget(plain)
    lay.addWidget(own)
    install_scroll_guard_recursive(root)

    # The plain one is guarded: the shared filter consumes a Wheel event.
    ev = QEvent(QEvent.Type.Wheel)
    ev.accept()
    assert _guard.eventFilter(plain, ev) is True

    # The self-handling one was left alone, so ITS handler still runs.
    ev2 = QEvent(QEvent.Type.Wheel)
    ev2.accept()
    own.wheelEvent(ev2)
    assert own.saw == 1, "the widget's own deliberate handler must survive"
    assert ev2.isAccepted() is False


@pytest.mark.parametrize("make", [
    lambda: QComboBox(),
    lambda: QSpinBox(),
    lambda: QSlider(Qt.Orientation.Horizontal),
])
def test_sweep_still_guards_plain_value_widgets(make):
    from ui.custom_widgets.scroll_guard import (
        install_scroll_guard_recursive, _guard,
    )
    root = QWidget()
    lay = QVBoxLayout(root)
    w = make()
    lay.addWidget(w)
    install_scroll_guard_recursive(root)
    ev = QEvent(QEvent.Type.Wheel)
    ev.accept()
    assert _guard.eventFilter(w, ev) is True
    assert ev.isAccepted() is False, "page scrolling must still work"


def test_sweep_is_idempotent():
    """It is called again by the unified window, so a second pass must be safe."""
    from ui.custom_widgets.scroll_guard import (
        install_scroll_guard_recursive, _guard,
    )
    root = QWidget()
    lay = QVBoxLayout(root)
    w = QSpinBox()
    lay.addWidget(w)
    install_scroll_guard_recursive(root)
    install_scroll_guard_recursive(root)
    ev = QEvent(QEvent.Type.Wheel)
    ev.accept()
    assert _guard.eventFilter(w, ev) is True


# ── the unified window must actually run the sweep ──────────────────────────

def test_unified_window_sweeps_its_pages():
    path = os.path.join(_ROOT, "ui", "unified_mainwindow.py")
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    assert "install_scroll_guard_recursive" in src, (
        "the unified window builds pages of its own (the Tilemap Editor among "
        "them) and must sweep them; it ran no guard at all before 2026-10-29"
    )
    m = re.search(r"\n    def setup_pages\(.*?(?=\n    # ═)", src, re.S)
    assert m and "install_scroll_guard_recursive" in m.group(0), (
        "the sweep must run in setup_pages, after the pages exist"
    )


# ── the deleted focus-based filters must not come back ──────────────────────

@pytest.mark.parametrize("rel", [
    "ui/mainwindow.py", "eventide/mainwindow.py",
    "ui/trainers_tab_widget.py", "ui/trainer_class_editor.py",
    "ui/tile_anim_viewer.py",
])
def test_no_focus_based_value_protection_anywhere(rel):
    with open(os.path.join(_ROOT, rel), encoding="utf-8") as fh:
        src = fh.read()
    assert "Wheel and not obj.hasFocus()" not in src
    assert "def _combo_wheel_filter" not in src
    # A spin box must never gate the wheel on focus.
    for m in re.finditer(r"class _NoScrollSpin\(QSpinBox\):(.*?)"
                         r"(?=\nclass |\n\n\n|\Z)", src, re.S):
        assert "hasFocus" not in m.group(1), f"{rel} regressed the focus rule"


# ── an intentional wheel use that must NOT be broken ────────────────────────

def test_ctrl_wheel_zoom_on_the_tilemap_canvas_is_untouched():
    """The tilemap canvas zooms on Ctrl+wheel. It is not a combo/spin/slider,
    so the guard never targets it — assert the handler is still there."""
    path = os.path.join(_ROOT, "ui", "tilemap_editor_tab.py")
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    assert "ControlModifier" in src and "set_zoom" in src, (
        "the Ctrl+wheel zoom must survive the scroll-guard work"
    )
