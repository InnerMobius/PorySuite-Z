"""Undo / redo for the Piano Roll — one history for every kind of edit.

Each entry is a complete snapshot of what the editor holds, taken just before
an edit: the canvas notes and drawn controls, the loop, which tracks exist and
in what order (as the track OBJECTS themselves, so a track keeps its identity
through removals, re-adds and moves), each track's commands with their values
(instrument, volume, pan, modulation, tempo ...), and the song-wide settings.
Undo puts a snapshot back; the state it replaces goes onto the redo list.

Snapshots rather than per-operation "reverse" code: every edit path — mouse
edits on the canvas, sidebar sliders, track operations, loop changes, lanes —
is undone the same way, and a new edit path cannot forget to provide its own
reverse.

The history is cleared when the song is (re)loaded or saved: a save re-reads
the song from disk, and a snapshot of the commands as they were before it
would no longer match the file (the save would rightly refuse it).
"""

from __future__ import annotations

from typing import Callable, Optional


class EditHistory:
    """Undo / redo stacks of snapshots made by *capture* and put back by
    *restore*. *same(a, b)* tells whether two snapshots hold the same edit
    state, so a checkpoint after which nothing changed (a click that moved
    nothing) is skipped by undo instead of costing the user a keypress."""

    def __init__(self, capture: Callable[[], object], restore: Callable[[object], None],
                 same: Callable[[object, object], bool], limit: int = 100):
        self._capture = capture
        self._restore = restore
        self._same = same
        self._limit = limit
        self._undo: list = []
        self._redo: list = []
        self._merge_key = None
        self.on_change: Optional[Callable[[], None]] = None

    def checkpoint(self, merge_key=None) -> None:
        """Call just BEFORE an edit. Repeated checkpoints with the same
        *merge_key* in a row (a slider being dragged, a spin box being spun)
        become one undo step."""
        if merge_key is not None and merge_key == self._merge_key and self._undo:
            self._redo.clear()
            self._changed()
            return
        self._merge_key = merge_key
        self._undo.append(self._capture())
        if len(self._undo) > self._limit:
            del self._undo[0]
        self._redo.clear()
        self._changed()

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> bool:
        return self._step(self._undo, self._redo)

    def redo(self) -> bool:
        return self._step(self._redo, self._undo)

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()
        self._merge_key = None
        self._changed()

    def _step(self, source: list, target: list) -> bool:
        self._merge_key = None
        current = self._capture()
        while source:
            state = source.pop()
            if self._same(state, current):
                continue            # a checkpoint after which nothing changed
            target.append(current)
            self._restore(state)
            self._changed()
            return True
        self._changed()
        return False

    def _changed(self) -> None:
        if self.on_change is not None:
            self.on_change()
