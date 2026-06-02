"""Edit Collision Footprint dialog — paint the 8x8 solid mask over a
sprite's art and return an updated ``Footprint``.

The dialog is the Phase 7c surface for the per-sprite collision
footprint feature.  The caller (the Overworld Graphics tab) renders
the sprite's first frame with the correct palette and passes the
QPixmap in; the dialog handles the grid overlay, drag-paint, and the
quick Clear / Fill actions, then returns the modified Footprint on
``Accepted``.

The widget works on a COPY of the incoming footprint so Cancel is
non-destructive — the tab keeps the original state until the user
explicitly OKs.

Two grids are drawn on the canvas: thin lines mark the 8x8 cell
boundaries (the unit the user paints in), thick lines mark the 16x16
tile grid (what the engine actually checks against — every 8x8 cell
that overlaps a tile makes that tile solid in the engine hook).
"""

from __future__ import annotations

from typing import Optional, Tuple

from PyQt6.QtCore import QPoint, QRect, QSize, Qt
from PyQt6.QtGui import (
    QColor,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.overworld_footprint import Footprint, empty_footprint


# Visual constants — kept here so the dialog file is self-contained.
_OVERLAY_SOLID = QColor(255, 64, 64, 130)
_OVERLAY_HOVER = QColor(255, 255, 255, 70)
_GRID_CELL = QColor(0, 0, 0, 140)
_GRID_TILE = QColor(0, 0, 0, 230)


# Aim for a roughly 480x480-px canvas across the typical sprite range.
# Very large sprites still get a usable canvas at the lower bound.
_TARGET_CANVAS_PX = 480
_MIN_CELL_SCALE = 16
_MAX_CELL_SCALE = 32


def _choose_cell_scale(width_cells: int, height_cells: int) -> int:
    """Pick a per-cell pixel scale that keeps the canvas a reasonable size."""
    longest = max(width_cells, height_cells, 1)
    return max(_MIN_CELL_SCALE,
               min(_MAX_CELL_SCALE, _TARGET_CANVAS_PX // longest))


class _FootprintCanvas(QWidget):
    """Scaled sprite preview with a paintable 8x8 cell grid.

    Drag-paint behaviour: on press, the clicked cell toggles, and the
    *new* value becomes the brush — every cell the user passes over
    while holding the button down is set to that value.  Lets the user
    fill a row by clicking-and-dragging across it, or wipe one the
    same way.
    """

    def __init__(
        self,
        sheet_pix: QPixmap,
        footprint: Footprint,
        frame_w_px: int,
        frame_h_px: int,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._sheet = sheet_pix
        self._fp = footprint
        self._fw = frame_w_px
        self._fh = frame_h_px
        self._scale = _choose_cell_scale(footprint.width, footprint.height)
        self._drag_value: Optional[bool] = None
        self._hover: Optional[Tuple[int, int]] = None
        self.setFixedSize(QSize(
            footprint.width * self._scale,
            footprint.height * self._scale,
        ))
        self.setMouseTracking(True)

    # ── public API ────────────────────────────────────────────────────

    def footprint(self) -> Footprint:
        return self._fp

    def set_all(self, solid: bool) -> None:
        for r in range(self._fp.height):
            for c in range(self._fp.width):
                self._fp.set_cell(c, r, solid)
        self.update()

    # ── geometry helpers ──────────────────────────────────────────────

    def _cell_rect(self, col: int, row: int) -> QRect:
        s = self._scale
        return QRect(col * s, row * s, s, s)

    def _cell_at(self, pos: QPoint) -> Optional[Tuple[int, int]]:
        if pos.x() < 0 or pos.y() < 0:
            return None
        c = pos.x() // self._scale
        r = pos.y() // self._scale
        if 0 <= c < self._fp.width and 0 <= r < self._fp.height:
            return int(c), int(r)
        return None

    # ── painting ──────────────────────────────────────────────────────

    def paintEvent(self, ev: QPaintEvent) -> None:  # noqa: N802
        p = QPainter(self)
        try:
            self._paint(p)
        finally:
            p.end()

    def _paint(self, p: QPainter) -> None:
        # Sprite art (first frame) — pixelated up-scale; no smoothing.
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        src = QRect(0, 0, self._fw, self._fh)
        dst = QRect(0, 0, self.width(), self.height())
        p.drawPixmap(dst, self._sheet, src)

        # Solid cells get a red wash so it reads as "this is a wall".
        for r in range(self._fp.height):
            for c in range(self._fp.width):
                if self._fp.cells[r][c]:
                    p.fillRect(self._cell_rect(c, r), _OVERLAY_SOLID)

        # Hover hint.
        if self._hover is not None:
            c, r = self._hover
            p.fillRect(self._cell_rect(c, r), _OVERLAY_HOVER)

        # Cell grid (thin) — every 8x8 boundary.
        p.setPen(QPen(_GRID_CELL, 1))
        for c in range(self._fp.width + 1):
            x = c * self._scale
            p.drawLine(x, 0, x, self.height())
        for r in range(self._fp.height + 1):
            y = r * self._scale
            p.drawLine(0, y, self.width(), y)

        # Tile grid (thick) — every 16x16 boundary, i.e. every 2nd cell line.
        p.setPen(QPen(_GRID_TILE, 2))
        for c in range(0, self._fp.width + 1, 2):
            x = c * self._scale
            p.drawLine(x, 0, x, self.height())
        for r in range(0, self._fp.height + 1, 2):
            y = r * self._scale
            p.drawLine(0, y, self.width(), y)

    # ── mouse ─────────────────────────────────────────────────────────

    def mousePressEvent(self, ev: QMouseEvent) -> None:  # noqa: N802
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        cell = self._cell_at(ev.position().toPoint())
        if cell is None:
            return
        c, r = cell
        # Toggle the pressed cell.  The new value is the drag brush
        # so dragging across more cells sets them ALL to this value
        # (no flicker between solid and open mid-stroke).
        new = not self._fp.cells[r][c]
        self._fp.set_cell(c, r, new)
        self._drag_value = new
        self.update()

    def mouseMoveEvent(self, ev: QMouseEvent) -> None:  # noqa: N802
        cell = self._cell_at(ev.position().toPoint())
        if cell != self._hover:
            self._hover = cell
            self.update()
        if cell is None or self._drag_value is None:
            return
        c, r = cell
        if self._fp.cells[r][c] != self._drag_value:
            self._fp.set_cell(c, r, self._drag_value)
            self.update()

    def mouseReleaseEvent(self, ev: QMouseEvent) -> None:  # noqa: N802
        self._drag_value = None

    def leaveEvent(self, ev) -> None:  # noqa: N802
        if self._hover is not None:
            self._hover = None
            self.update()


class FootprintEditorDialog(QDialog):
    """Modal editor for one sprite's collision footprint.

    Parameters
    ----------
    gfx_const : str
        ``OBJ_EVENT_GFX_*`` constant used in the dialog title and as
        the returned footprint's key.
    sheet_pix : QPixmap
        The sprite sheet rendered with the live palette.  The dialog
        crops to the first frame using ``(frame_w_px, frame_h_px)``.
    frame_w_px, frame_h_px : int
        Frame dimensions in pixels.  Must both be positive multiples
        of 8; ``empty_footprint`` raises ``ValueError`` otherwise.
    initial : Footprint | None
        Existing footprint to start from.  ``None`` starts with an
        empty grid sized to the sprite.
    """

    def __init__(
        self,
        gfx_const: str,
        sheet_pix: QPixmap,
        frame_w_px: int,
        frame_h_px: int,
        initial: Optional[Footprint] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Edit Collision Footprint — {gfx_const}")
        self.setModal(True)

        # Work on a copy of ``initial`` so Cancel is non-destructive.
        working = empty_footprint(gfx_const, frame_w_px, frame_h_px)
        if initial is not None:
            for r in range(min(initial.height, working.height)):
                for c in range(min(initial.width, working.width)):
                    if initial.is_solid(c, r):
                        working.set_cell(c, r, True)

        v = QVBoxLayout(self)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(10)

        # Help / warnings — concise; the dialog title carries the const.
        help_lbl = QLabel(
            "Click cells to paint where the sprite blocks the player.  "
            "Each painted cell BOTH blocks movement AND triggers the "
            "object's interaction script when the player presses A "
            "facing it.\n\n"
            "Thin lines are 8×8-pixel cells (the resolution you paint in).  "
            "Thick lines are the 16×16 tile grid the engine actually checks "
            "against — any cell that overlaps a tile makes that tile solid "
            "in-game.\n\n"
            "Warnings: a footprint over a doorway will trap the player; "
            "collision moves with a walking sprite; cells that fall off "
            "the map are silently ignored at runtime."
        )
        help_lbl.setWordWrap(True)
        help_lbl.setStyleSheet("color: #aaa; font-size: 11px;")
        v.addWidget(help_lbl)

        # Canvas — centred so small sprites don't sit jammed against the
        # left edge.
        canvas_row = QHBoxLayout()
        canvas_row.addStretch(1)
        self._canvas = _FootprintCanvas(
            sheet_pix, working, frame_w_px, frame_h_px, parent=self,
        )
        canvas_row.addWidget(self._canvas)
        canvas_row.addStretch(1)
        v.addLayout(canvas_row)

        # Quick actions.
        action_row = QHBoxLayout()
        action_row.setSpacing(6)
        clear_btn = QPushButton("Clear All")
        clear_btn.setToolTip(
            "Empty every cell — sprite returns to vanilla 1-tile collision."
        )
        clear_btn.clicked.connect(lambda: self._canvas.set_all(False))
        fill_btn = QPushButton("Fill All")
        fill_btn.setToolTip(
            "Mark every cell solid — the whole sprite body blocks."
        )
        fill_btn.clicked.connect(lambda: self._canvas.set_all(True))
        action_row.addWidget(clear_btn)
        action_row.addWidget(fill_btn)
        action_row.addStretch(1)
        v.addLayout(action_row)

        # OK / Cancel.
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def footprint(self) -> Footprint:
        """Return the edited footprint.  Call only after ``accept()``."""
        return self._canvas.footprint()
