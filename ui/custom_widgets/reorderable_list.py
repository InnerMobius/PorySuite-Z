"""A list the user reorders by dragging rows, without Qt moving the rows.

Two editors let the user drag rows into a new order — the EVENTide command
list and the Credits entry list — and both used to hand the move to Qt and
then read the result back out of the widget. That is the part that goes wrong,
so both now share the pieces here.

What a QListWidget actually does with a dropped row, measured against the
project's own Qt (6.10.0) rather than taken from the documentation:

    before:  Director / Programmers / Artists
    model.dropMimeData(model.mimeData([index(2)]), MoveAction, row=0, col=-1)
        -> True
    after:   Artists / Programmers / Artists

The dragged row's contents are written OVER the row it landed on, the original
is left exactly where it was, the list comes back the same length, and the call
reports success. One row is duplicated and a different one is destroyed, with
nothing raised anywhere. Only ``dataChanged`` is emitted — ``rowsMoved`` never
fires — so a handler connected to ``rowsMoved`` to keep the data in step never
runs at all, and the widget and the data drift apart in silence. Both column
values a view can pass (-1 and 0) behave the same way.

QListWidget also has its own drop handler for drags that start in the same
widget, which can move rows properly, and falls through to the path above when
it declines. Which of the two a real drag takes cannot be settled from a test:
``QDropEvent.source()`` is only set while a drag is genuinely in flight, so a
synthetic event can never reach the first path.

That ambiguity is the reason for the approach rather than an obstacle to it.
The editor reads the drop only to learn WHERE the rows were dropped, refuses
the event, and makes the move in its own data — so the outcome no longer
depends on which path Qt would have taken.
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QAbstractItemView, QListWidget


def reorder_rows(rows, sources, dest: int):
    """Move the rows at *sources* so they land at insertion point *dest*.

    Returns ``(new_rows, landed)`` — the reordered list, and the indices the
    moved rows ended up at so a caller can keep them selected. *dest* is an
    insertion point in the ORIGINAL numbering: 0 is before the first row,
    ``len(rows)`` is after the last. The moved rows keep their relative order.

    Doing the reorder here, on the data, and repainting the widget from the
    result means there is only ever one order — instead of two that have to be
    kept in agreement, which is the arrangement that let a drag duplicate one
    row and lose another. See this module's own docstring for what a list
    widget does to its rows when it is asked to reorder them itself.
    """
    n = len(rows)
    src = sorted({s for s in sources if 0 <= s < n})
    if not src:
        return list(rows), []
    dest = max(0, min(dest, n))
    moving = [rows[i] for i in src]
    taken = set(src)
    rest = [r for i, r in enumerate(rows) if i not in taken]
    # Rows lifted out from above the insertion point pull it up with them.
    at = max(0, min(dest - sum(1 for s in src if s < dest), len(rest)))
    return rest[:at] + moving + rest[at:], list(range(at, at + len(moving)))


def move_list_rows(lst, rows, dest: int) -> list:
    """Move rows inside *lst* itself, and return where they landed.

    For the lists whose ORDER IS THE WIDGET -- the two Pokedex lists and the
    move route steps keep no second copy of it, and are read row by row when
    the project is saved. There is nothing to reorder but the widget, so the
    rows are moved by taking the very same ``QListWidgetItem`` objects out and
    putting them back in the new order. They keep their text, their stored
    value and their icon, because they are the same objects; and because the
    move is done here rather than by the drop, no row is ever written over.

    Signals are held while the rows are out, so nothing reacts to the
    half-empty list on the way through. The caller sets the selection
    afterwards, which fires once.
    """
    n = lst.count()
    order, landed = reorder_rows(list(range(n)), rows, dest)
    if order == list(range(n)):
        return []                       # dropped where it already was
    lst.blockSignals(True)
    try:
        items = [lst.takeItem(0) for _ in range(n)]
        for i in order:
            lst.addItem(items[i])
    finally:
        lst.blockSignals(False)
    return landed


class ReorderableListWidget(QListWidget):
    """A list whose rows are reordered by its owner, not by Qt.

    The drop is read only for WHERE the rows were dropped; the event is then
    refused, which stops Qt touching any row, and ``rows_move_requested`` asks
    the owner to make the move in its own data and repaint the list from it.

    Refusing the drop does the second necessary job too: Qt only deletes the
    rows a drag started from when the drag finishes with a move action, so
    answering with ``IgnoreAction`` is what keeps the dragged rows alive.
    """

    #: (source rows, insertion point), both in the list's current numbering.
    rows_move_requested = pyqtSignal(object, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)

    def _drop_row(self, event) -> int:
        """The insertion point the drop indicator was showing.

        A drop with no row under it means the empty space past the last row,
        which is the one case that appends. Dropping ONTO a row counts as
        dropping above it, so landing on a row's middle can never silently
        send it to the end of the list instead -- a row arriving past the end
        is how a list with a trailing insertion line got one placed after it.
        """
        index = self.indexAt(event.position().toPoint())
        if not index.isValid():
            return self.count()
        if (self.dropIndicatorPosition()
                == QAbstractItemView.DropIndicatorPosition.BelowItem):
            return index.row() + 1
        return index.row()

    def dropEvent(self, event):
        rows = sorted(self.row(i) for i in self.selectedItems())
        dest = self._drop_row(event)
        # Refusing the drop is what keeps Qt from writing over a row here and
        # from deleting the dragged ones once the drag ends.
        event.setDropAction(Qt.DropAction.IgnoreAction)
        event.accept()
        if rows:
            self.rows_move_requested.emit(rows, dest)
