"""
ui/draggable_palette_row.py

Reusable palette-row widget with drag-to-reorder + click-to-edit.
Originally lived inside ui/image_indexer_tab.py; extracted for reuse by
the Pokemon Graphics, Trainer Graphics and Overworld editors.

Public API mirrors the simpler PaletteSwatchRow in ui/graphics_tab_widget.py:
  - set_colors(list[(r,g,b)])
  - colors() -> list[(r,g,b)]
  - signal: colors_changed              (compatible with PaletteSwatchRow)

Plus the new reorder behaviour:
  - signal: palette_reordered(int from_idx, int to_idx)

Drop a swatch onto index 0 to mark it as the "BG" / transparent slot.
The widget itself does NOT remap any image pixels — callers wire the
palette_reordered signal to reorder_palette() / move_color_to_index()
from core.gba_image_utils for the indexed PNGs they own.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal, QMimeData, QPoint
from PyQt6.QtGui import (
    QColor, QDrag, QFont, QMouseEvent, QPainter, QPixmap,
)
from PyQt6.QtWidgets import (
    QColorDialog, QHBoxLayout, QLabel, QWidget,
)

from ui.palette_utils import clamp_to_gba


SWATCH_SZ = 22


class DragSwatch(QLabel):
    """Palette swatch: click to edit colour, drag to reorder."""

    color_changed = pyqtSignal(int, tuple)   # (index, (r,g,b))
    drop_received = pyqtSignal(int, int)     # (from_index, to_index)

    def __init__(self, index: int, parent=None):
        super().__init__(parent)
        self._index = index
        self._color: tuple[int, int, int] = (0, 0, 0)
        self.setFixedSize(SWATCH_SZ, SWATCH_SZ)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAutoFillBackground(True)
        self.setAcceptDrops(True)
        self._drag_start: QPoint | None = None
        self._refresh_tooltip()
        self._refresh()

    @property
    def index(self) -> int:
        return self._index

    @index.setter
    def index(self, v: int):
        self._index = v
        self._refresh_tooltip()

    def color(self) -> tuple[int, int, int]:
        return self._color

    def set_color(self, c: tuple[int, int, int], emit: bool = False):
        c = clamp_to_gba(*c)
        if c != self._color:
            self._color = c
            self._refresh()
            self._refresh_tooltip()
            if emit:
                self.color_changed.emit(self._index, c)

    # painting
    def _refresh(self):
        r, g, b = self._color
        p = self.palette()
        p.setColor(self.backgroundRole(), QColor(r, g, b))
        self.setPalette(p)

    def _refresh_tooltip(self):
        r, g, b = self._color
        self.setToolTip(
            f"Index {self._index}: ({r}, {g}, {b})\n"
            f"Click to edit  •  Drag to reorder"
        )

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setPen(QColor("#555"))
        p.drawRect(0, 0, SWATCH_SZ - 1, SWATCH_SZ - 1)
        if self._index == 0:
            p.setPen(QColor("#ff6666"))
            p.setFont(QFont("Arial", 7, QFont.Weight.Bold))
            p.drawText(2, 13, "BG")
        p.end()

    # click vs drag
    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.pos()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and self._drag_start is not None:
            delta = event.pos() - self._drag_start
            if delta.manhattanLength() < 5:
                self._open_picker()
        self._drag_start = None
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._drag_start is None:
            return
        if (event.pos() - self._drag_start).manhattanLength() < 5:
            return
        drag = QDrag(self)
        mime = QMimeData()
        mime.setText(str(self._index))
        drag.setMimeData(mime)
        pm = QPixmap(SWATCH_SZ, SWATCH_SZ)
        pm.fill(QColor(*self._color))
        drag.setPixmap(pm)
        drag.setHotSpot(QPoint(SWATCH_SZ // 2, SWATCH_SZ // 2))
        self._drag_start = None
        drag.exec(Qt.DropAction.MoveAction)

    def _open_picker(self):
        r, g, b = self._color
        top = self.window()
        dlg = QColorDialog(QColor(r, g, b), top)
        dlg.setWindowTitle(f"Palette index {self._index}")
        dlg.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog)
        for lbl in dlg.findChildren(QLabel):
            if lbl.text().rstrip(":").strip().upper() in ("HTML", "&HTML"):
                lbl.setText("Hex:")
        if dlg.exec() == QColorDialog.DialogCode.Accepted:
            qc = dlg.currentColor()
            if qc.isValid():
                new = clamp_to_gba(qc.red(), qc.green(), qc.blue())
                if new != self._color:
                    self._color = new
                    self._refresh()
                    self._refresh_tooltip()
                    self.color_changed.emit(self._index, new)

    # drop target
    def dragEnterEvent(self, event):
        if event.mimeData().hasText():
            event.acceptProposedAction()

    def dropEvent(self, event):
        try:
            src = int(event.mimeData().text())
        except (ValueError, TypeError):
            return
        if src != self._index:
            self.drop_received.emit(src, self._index)
        event.acceptProposedAction()


class DraggablePaletteRow(QWidget):
    """Row of DragSwatch widgets — supports drag-reorder.

    Drop-in replacement for PaletteSwatchRow with the same colors_changed
    signal + set_colors/colors API, plus palette_reordered(from, to).
    """
    colors_changed = pyqtSignal()              # any swatch colour edited
    palette_reordered = pyqtSignal(int, int)   # (from, to)

    def __init__(self, n: int = 16, parent=None):
        super().__init__(parent)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(2)
        self._swatches: list[DragSwatch] = []
        for i in range(n):
            s = DragSwatch(i)
            s.color_changed.connect(self._on_color_changed)
            s.drop_received.connect(self._on_drop)
            self._swatches.append(s)
            self._layout.addWidget(s)
        self._layout.addStretch(1)

    def set_colors(self, colors: list[tuple[int, int, int]]):
        for i, s in enumerate(self._swatches):
            c = colors[i] if i < len(colors) else (0, 0, 0)
            s.set_color(c, emit=False)

    def colors(self) -> list[tuple[int, int, int]]:
        return [s.color() for s in self._swatches]

    def count(self) -> int:
        return len(self._swatches)

    def _on_color_changed(self, idx: int, color: tuple):
        self.colors_changed.emit()

    def _on_drop(self, src: int, dst: int):
        self.palette_reordered.emit(src, dst)
