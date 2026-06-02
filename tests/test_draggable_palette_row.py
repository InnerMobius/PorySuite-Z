"""Verification for ui/draggable_palette_row.py — the palette-row drag
reorder, rewritten from a QDrag / OS drag-and-drop operation to a plain
mouse-grab gesture (press, track moves, act on release).

QDrag depends on the platform drag protocol; it stopped delivering drops
in this environment, so the reorder is now done with ordinary mouse
events only.  This test exercises the real widget against an offscreen
Qt — it needs genuine geometry (mapToGlobal / childAt), so the usual
PyQt-stubbing the other tests use won't work here.

Run directly:  python tests/test_draggable_palette_row.py
"""

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication

from ui.draggable_palette_row import DraggablePaletteRow

_app = QApplication.instance() or QApplication([])


def _make_row():
    row = DraggablePaletteRow(n=16)
    row.set_colors([(i * 15, 64, 128) for i in range(16)])
    row.resize(16 * 30, 40)
    row.show()
    _app.processEvents()
    return row


def _mouse_event(kind, local_pt):
    pt = QPointF(local_pt)
    return QMouseEvent(
        kind, pt, pt,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


class FinishDragTest(unittest.TestCase):
    """`finish_drag` maps a drop coordinate back to a slot index."""

    def test_drop_on_a_swatch_reorders_to_that_index(self):
        row = _make_row()
        seen = []
        row.palette_reordered.connect(lambda f, t: seen.append((f, t)))
        target = row._swatches[7]
        drop = target.mapToGlobal(target.rect().center())
        row.finish_drag(3, drop)
        self.assertEqual(seen, [(3, 7)])
        row.deleteLater()

    def test_drop_back_on_the_origin_does_nothing(self):
        row = _make_row()
        seen = []
        row.palette_reordered.connect(lambda f, t: seen.append((f, t)))
        target = row._swatches[5]
        drop = target.mapToGlobal(target.rect().center())
        row.finish_drag(5, drop)
        self.assertEqual(seen, [])
        row.deleteLater()

    def test_drop_past_the_end_snaps_to_the_last_swatch(self):
        row = _make_row()
        seen = []
        row.palette_reordered.connect(lambda f, t: seen.append((f, t)))
        last = row._swatches[15]
        # A point well to the right of the final swatch.
        far = last.mapToGlobal(QPoint(last.width() + 400, last.height() // 2))
        row.finish_drag(2, far)
        self.assertEqual(seen, [(2, 15)])
        row.deleteLater()


class MouseGrabGestureTest(unittest.TestCase):
    """The full press → move → release gesture, with no QDrag involved."""

    def test_press_move_release_emits_palette_reordered(self):
        row = _make_row()
        seen = []
        row.palette_reordered.connect(lambda f, t: seen.append((f, t)))
        src = row._swatches[2]
        dst = row._swatches[9]

        centre = src.rect().center()
        src.mousePressEvent(
            _mouse_event(QEvent.Type.MouseButtonPress, centre))
        self.assertIsNotNone(src._drag_start)

        # Move well past the 5px click threshold — now it is a drag.
        far = QPoint(centre.x() + 40, centre.y())
        src.mouseMoveEvent(_mouse_event(QEvent.Type.MouseMove, far))
        self.assertTrue(src._dragging)

        # Release at the swatch-2-local point that lands over swatch 9 —
        # the implicit mouse grab keeps the events on swatch 2.
        release_local = src.mapFromGlobal(
            dst.mapToGlobal(dst.rect().center()))
        src.mouseReleaseEvent(
            _mouse_event(QEvent.Type.MouseButtonRelease, release_local))
        self.assertEqual(seen, [(2, 9)])
        self.assertFalse(src._dragging)
        row.deleteLater()

    def test_sub_threshold_wobble_is_not_treated_as_a_drag(self):
        row = _make_row()
        src = row._swatches[4]
        centre = src.rect().center()
        src.mousePressEvent(
            _mouse_event(QEvent.Type.MouseButtonPress, centre))
        # A 2px wobble is below the 5px threshold — not a drag.
        src.mouseMoveEvent(_mouse_event(
            QEvent.Type.MouseMove, QPoint(centre.x() + 2, centre.y())))
        self.assertFalse(src._dragging)
        # (No release here — a non-drag release opens the colour picker,
        # a modal dialog that would block an automated test.)
        row.deleteLater()


if __name__ == "__main__":
    unittest.main(verbosity=2)
