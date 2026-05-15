"""
ui/draggable_palette_row.py

Reusable palette-row widget with drag-to-reorder + click-to-edit.
Originally lived inside ui/image_indexer_tab.py; extracted for reuse by
the Pokemon Graphics, Trainer Graphics and Overworld editors.

Public API mirrors the simpler PaletteSwatchRow in ui/graphics_tab_widget.py:
  - set_colors(list[(r,g,b)])
  - colors() -> list[(r,g,b)]
  - signal: colors_changed              (compatible with PaletteSwatchRow)

Plus the new reorder + context-menu behaviours:
  - signal: palette_reordered(int from_idx, int to_idx)
  - signal: swatch_set_as_bg(int slot)   # from right-click menu

Drop a swatch onto index 0 to mark it as the "BG" / transparent slot.
Or right-click any non-zero swatch and pick "Index as Background" to
request the same thing via menu.

The widget itself does NOT remap any image pixels.  Callers wire:
  - palette_reordered   → palette-only swap (no pixel remap)
  - swatch_set_as_bg    → pixel + palette swap via
                          core.gba_image_utils.swap_palette_entries,
                          because a BG promotion has to remap which
                          pixel values are transparent on disk.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal, QMimeData, QPoint
from PyQt6.QtGui import (
    QColor, QDrag, QFont, QMouseEvent, QPainter, QPixmap,
)
from PyQt6.QtWidgets import (
    QColorDialog, QHBoxLayout, QLabel, QMenu, QWidget,
)

from ui.palette_utils import clamp_to_gba


SWATCH_SZ = 22


class DragSwatch(QLabel):
    """Palette swatch: click to edit colour, drag to reorder, right-click
    to promote to the transparent slot ("Index as Background")."""

    color_changed = pyqtSignal(int, tuple)   # (index, (r,g,b))
    drop_received = pyqtSignal(int, int)     # (from_index, to_index)
    set_as_bg_requested = pyqtSignal(int)    # (index) — right-click menu

    def __init__(self, index: int, parent=None):
        super().__init__(parent)
        self._index = index
        self._color: tuple[int, int, int] = (0, 0, 0)
        self.setFixedSize(SWATCH_SZ, SWATCH_SZ)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # NOTE: do NOT call setAutoFillBackground(True) here.
        # We control the background via setStyleSheet (CSS), not QPalette.
        # QPalette-based backgrounds are silently overridden by Qt's CSS
        # engine whenever any ancestor widget has a stylesheet engaged —
        # even an empty one.  setStyleSheet on the widget itself always
        # wins, so we own the background regardless of parent state.
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
        # CSS background-color takes priority over QPalette and is immune to
        # parent stylesheet re-evaluations.  Do NOT use setPalette() here.
        self.setStyleSheet(f"background-color: rgb({r},{g},{b});")

    def _refresh_tooltip(self):
        r, g, b = self._color
        if self._index == 0:
            extra = "Click to edit  •  Drag to reorder"
        else:
            extra = (
                "Click to edit  •  Drag to reorder\n"
                "Right-click: Index as Background"
            )
        self.setToolTip(
            f"Index {self._index}: ({r}, {g}, {b})\n{extra}"
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

        # Live GBA-quantized preview alongside Qt's own preview.  The
        # GBA stores 5 bits per channel (32 levels, multiples of 8), so
        # picks like #F9C890 round-trip back to #F8C890 with no visible
        # change.  Without showing the rounded-down value, users assume
        # the picker is broken when their fine-tuned tweaks evaporate.
        gba_preview = QLabel()
        gba_preview.setMinimumSize(48, 24)
        gba_preview.setStyleSheet(
            "border: 1px solid #555; background: rgb({}, {}, {});".format(
                *clamp_to_gba(r, g, b)
            )
        )
        gba_label = QLabel(
            "GBA preview (rounded to 5-bit channels — what actually saves):"
        )
        gba_label.setWordWrap(True)
        gba_hex = QLabel(f"#{r:02X}{g:02X}{b:02X}")
        gba_hex.setStyleSheet("color: #aaa; font-family: monospace;")
        # Push the previews into the dialog's layout, at the bottom.
        layout = dlg.layout()
        if layout is not None:
            row = QHBoxLayout()
            row.addWidget(gba_preview)
            row.addWidget(gba_hex)
            row.addStretch(1)
            layout.addWidget(gba_label)
            layout.addLayout(row)

        def _refresh_gba_preview(qc):
            gr, gg, gb = clamp_to_gba(qc.red(), qc.green(), qc.blue())
            gba_preview.setStyleSheet(
                f"border: 1px solid #555; background: rgb({gr}, {gg}, {gb});"
            )
            gba_hex.setText(f"#{gr:02X}{gg:02X}{gb:02X}")

        dlg.currentColorChanged.connect(_refresh_gba_preview)

        if dlg.exec() == QColorDialog.DialogCode.Accepted:
            qc = dlg.currentColor()
            if qc.isValid():
                new = clamp_to_gba(qc.red(), qc.green(), qc.blue())
                if new != self._color:
                    self._color = new
                    self._refresh()
                    self._refresh_tooltip()
                    self.color_changed.emit(self._index, new)
                elif (qc.red(), qc.green(), qc.blue()) != self._color:
                    # The user picked SOMETHING different from the
                    # current swatch, but it quantizes to the same GBA
                    # value.  Tell them why their change appears to do
                    # nothing — better than silently swallowing the
                    # input and leaving them to guess.
                    from PyQt6.QtWidgets import QMessageBox
                    raw = (qc.red(), qc.green(), qc.blue())
                    QMessageBox.information(
                        top, "GBA palette resolution",
                        f"The color you picked (#{raw[0]:02X}{raw[1]:02X}{raw[2]:02X}) "
                        f"rounds down to #{new[0]:02X}{new[1]:02X}{new[2]:02X} "
                        "in the GBA's 15-bit palette format — the same as "
                        "the current swatch.\n\n"
                        "GBA palettes only have 32 distinct levels per "
                        "channel (multiples of 8: 0, 8, 16, …, 248).  "
                        "Pick a colour that differs by at least 8 in one "
                        "channel to register a change."
                    )

    # right-click menu — "Index as Background"
    def contextMenuEvent(self, event):
        menu = QMenu(self)
        act = menu.addAction("Index as Background")
        if self._index == 0:
            # Already the transparent slot by convention — no-op disabled.
            act.setEnabled(False)
            act.setText("Index as Background  (already BG)")
        act.triggered.connect(
            lambda: self.set_as_bg_requested.emit(self._index)
        )
        menu.exec(event.globalPos())

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
    swatch_set_as_bg = pyqtSignal(int)         # (slot) — right-click menu

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
            s.set_as_bg_requested.connect(self._on_set_as_bg)
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

    def _on_set_as_bg(self, slot: int):
        self.swatch_set_as_bg.emit(slot)
