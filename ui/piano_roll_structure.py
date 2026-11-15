"""Song Structure panel for the Piano Roll.

Shows the song's structure — where the loop starts and loops back, every time
a pattern (a written-once section the song repeats) plays, other section
markers and where the song ends — at the time each one PLAYS, on the same
clock the canvas draws notes on.

It used to list the commands at their position in the song's TEXT, where a
pattern call takes no time. In a song with repeats the loop then appeared too
early (Kakariko: shown at 2879, plays at 3935; 40 of the project's 81 looping
songs were off), and a loop entered there landed too early in the song. Its
Section / Pattern Call / End Song buttons rewrote the song's commands in
memory, but no save ever wrote them: the build makes the song's patterns and
labels from its notes and its loop.

What the panel edits is what a save writes: the loop. Set, move or remove it
here (or with the cursor buttons); the change goes through the window, into
the undo history and into the next save, on the playback clock.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget,
    QListWidgetItem, QAbstractItemView, QDialog, QDialogButtonBox, QFormLayout,
    QSpinBox, QMessageBox,
)

from ui.custom_widgets.scroll_guard import install_scroll_guard


_KIND_TEXT = {
    'loop_start': 'Loop starts',
    'loop_end': 'Loops back to the loop start',
    'pattern': 'Pattern',
    'section': 'Marker',
    'end': 'Song ends',
}
_KIND_COLOR = {
    'loop_start': QColor(80, 140, 255),
    'loop_end': QColor(80, 140, 255),
    'pattern': QColor(220, 160, 80),
    'section': QColor(80, 180, 80),
    'end': QColor(255, 80, 80),
}

TICKS_PER_BEAT = 24     # the M4A / mid2agb clock


class _LoopDialog(QDialog):
    def __init__(self, start: int, end: int, limit: int, where, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Set Loop")
        self._where = where
        form = QFormLayout(self)
        self._start = QSpinBox()
        self._end = QSpinBox()
        for box, value in ((self._start, start), (self._end, end)):
            install_scroll_guard(box)
            box.setRange(0, limit)
            box.setValue(value)
            box.setSuffix(" ticks")
        self._hint = QLabel()
        self._hint.setStyleSheet("color: #999;")
        form.addRow("Loop starts at:", self._start)
        form.addRow("Loops back at:", self._end)
        form.addRow(self._hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self._start.valueChanged.connect(self._update_hint)
        self._end.valueChanged.connect(self._update_hint)
        self._update_hint()

    def _update_hint(self):
        self._hint.setText(f"{self._where(self._start.value())}  →  "
                           f"{self._where(self._end.value())}\n"
                           f"({TICKS_PER_BEAT} ticks = one beat; times are when the music plays)")

    def _accept(self):
        if self._end.value() <= self._start.value():
            QMessageBox.warning(self, "Set Loop",
                                "The loop has to end after it starts.")
            return
        self.accept()

    def values(self) -> tuple[int, int]:
        return self._start.value(), self._end.value()


class SongStructurePanel(QWidget):
    """Right-side panel: the song's structure on the playback clock, and the
    loop controls."""

    #: (start, end) the user asked for; (None, None) removes the loop.
    loop_requested = pyqtSignal(object, object)
    seek_to_tick = pyqtSignal(int)
    time_signatures_requested = pyqtSignal()
    #: (at tick, length): length > 0 inserts that much time, < 0 removes it.
    shift_time_requested = pyqtSignal(int, int)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFixedWidth(220)
        self._entries: list[tuple[int, str, str]] = []   # (tick, kind, name)
        self._structure: dict = {'sections': [], 'patterns': [], 'end': 0}
        self._loop: tuple = (None, None)
        self._cursor_tick = 0
        self._total_ticks = 0
        self._prefix = ''
        self._bar_starts: list[int] = []
        self._build_ui()

    # ── UI ─────────────────────────────────────────────────────────────
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        header = QLabel("Song Structure")
        header.setFont(QFont("", 10, QFont.Weight.Bold))
        header.setStyleSheet("color: #ccc;")
        layout.addWidget(header)
        note = QLabel("Times are when each part plays.\nClick an entry to jump there.")
        note.setStyleSheet("color: #888; font-size: 10px;")
        layout.addWidget(note)

        self._list = QListWidget()
        self._list.setStyleSheet("""
            QListWidget { background: #1e1e22; border: 1px solid #444; font-size: 11px; }
            QListWidget::item { padding: 4px 6px; border-bottom: 1px solid #333; }
            QListWidget::item:selected { background: #2a3a5a; }
        """)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self._list, 1)

        self._loop_label = QLabel()
        self._loop_label.setWordWrap(True)
        self._loop_label.setStyleSheet("color: #8ab4ff;")
        layout.addWidget(self._loop_label)

        self._btn_set = QPushButton("Set Loop…")
        self._btn_set.setToolTip("Type the loop's start and end (when the music plays).")
        self._btn_set.clicked.connect(self._on_set_loop)
        layout.addWidget(self._btn_set)
        row = QHBoxLayout()
        self._btn_start_here = QPushButton("Start Here")
        self._btn_start_here.setToolTip("Move the loop start to the playback cursor.")
        self._btn_start_here.clicked.connect(self._on_start_here)
        row.addWidget(self._btn_start_here)
        self._btn_end_here = QPushButton("End Here")
        self._btn_end_here.setToolTip("Make the song loop back at the playback cursor.")
        self._btn_end_here.clicked.connect(self._on_end_here)
        row.addWidget(self._btn_end_here)
        layout.addLayout(row)
        self._btn_remove = QPushButton("Remove Loop")
        self._btn_remove.setToolTip("The song plays once and stops instead of looping.")
        self._btn_remove.clicked.connect(lambda: self.loop_requested.emit(None, None))
        layout.addWidget(self._btn_remove)

        self._btn_time_sigs = QPushButton("Time Signatures…")
        self._btn_time_sigs.setToolTip("Where bars fall (3/4, 6/8, ...). Does not change what plays.")
        self._btn_time_sigs.clicked.connect(self.time_signatures_requested.emit)
        layout.addWidget(self._btn_time_sigs)

        row2 = QHBoxLayout()
        self._btn_insert_time = QPushButton("Insert Time…")
        self._btn_insert_time.setToolTip(
            "Add silence at the cursor on every track; everything after it moves later.")
        self._btn_insert_time.clicked.connect(lambda: self._ask_shift(+1))
        row2.addWidget(self._btn_insert_time)
        self._btn_remove_time = QPushButton("Remove Time…")
        self._btn_remove_time.setToolTip(
            "Cut a stretch out of every track at the cursor; everything after it moves earlier.")
        self._btn_remove_time.clicked.connect(lambda: self._ask_shift(-1))
        row2.addWidget(self._btn_remove_time)
        layout.addLayout(row2)

    # ── public API ─────────────────────────────────────────────────────
    def set_cursor_tick(self, tick: int):
        self._cursor_tick = max(0, int(tick))

    def set_bar_starts(self, bar_starts: list[int]) -> None:
        """Bar-line positions (playback ticks), for showing "bar 3, beat 2"."""
        self._bar_starts = sorted(bar_starts)
        self._refresh()

    def load_from_song(self, song, total_ticks: int):
        """Read the song's structure (patterns, markers, end) on the playback
        clock. The loop shown is the editor's own (``set_loop``)."""
        from core.sound.midi_exporter import song_structure
        self._total_ticks = total_ticks
        self._structure = song_structure(song)
        self._prefix = (song.tracks[0].label + '_') if song.tracks else ''
        self._refresh()

    def set_loop(self, start, end) -> None:
        self._loop = (start, end)
        self._refresh()

    def where(self, tick: int) -> str:
        """"tick 3935 (bar 41, beat 4)" — bars from the song's time signatures."""
        if self._bar_starts:
            import bisect
            i = bisect.bisect_right(self._bar_starts, tick) - 1
            if i >= 0:
                beat = (tick - self._bar_starts[i]) // TICKS_PER_BEAT + 1
                return f"tick {tick} (bar {i + 1}, beat {beat})"
        return f"tick {tick}"

    # ── internals ──────────────────────────────────────────────────────
    def _friendly(self, label: str) -> str:
        return label[len(self._prefix):] if self._prefix and label.startswith(self._prefix) else label

    def _refresh(self):
        st = self._structure
        entries = [(t, 'pattern', self._friendly(n)) for t, n in st['patterns']]
        entries += [(t, 'section', self._friendly(n)) for t, n in st['sections']]
        start, end = self._loop
        if start is not None and end is not None:
            entries += [(start, 'loop_start', ''), (end, 'loop_end', '')]
            self._loop_label.setText(f"Loop: {self.where(start)} → {self.where(end)}")
            self._btn_remove.setEnabled(True)
        else:
            self._loop_label.setText("No loop: the song plays once.")
            self._btn_remove.setEnabled(False)
        if st['end']:
            entries.append((st['end'], 'end', ''))
        order = {'loop_start': 0, 'section': 1, 'pattern': 2, 'loop_end': 3, 'end': 4}
        self._entries = sorted(entries, key=lambda e: (e[0], order[e[1]]))
        self._list.clear()
        for tick, kind, name in self._entries:
            text = _KIND_TEXT[kind] + (f" \"{name}\" plays" if kind == 'pattern'
                                       else f" \"{name}\"" if name else "")
            item = QListWidgetItem(f"  {text}\n  at {self.where(tick)}")
            item.setForeground(_KIND_COLOR[kind])
            self._list.addItem(item)

    def _on_item_clicked(self, _item):
        row = self._list.currentRow()
        if 0 <= row < len(self._entries):
            self.seek_to_tick.emit(self._entries[row][0])

    def _limit(self) -> int:
        return max(self._total_ticks, self._structure.get('end') or 0, self._cursor_tick) + 96 * 64

    def _on_set_loop(self):
        start, end = self._loop
        if start is None or end is None:
            start = 0
            end = max(self._structure.get('end') or 0, self._cursor_tick, TICKS_PER_BEAT * 4)
        dlg = _LoopDialog(start, end, self._limit(), self.where, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.loop_requested.emit(*dlg.values())

    def _ask_shift(self, sign: int):
        from PyQt6.QtWidgets import QInputDialog
        if self._cursor_tick < 1:
            QMessageBox.warning(self, "Insert / Remove Time",
                                "Put the cursor after the very start first (the start holds "
                                "each track's starting values).")
            return
        what = "insert" if sign > 0 else "remove"
        length, ok = QInputDialog.getInt(
            self, "Insert Time" if sign > 0 else "Remove Time",
            f"Ticks to {what} at {self.where(self._cursor_tick)} "
            f"({TICKS_PER_BEAT} = one beat, {TICKS_PER_BEAT * 4} = a 4/4 bar):",
            TICKS_PER_BEAT * 4, 1, 96 * 256)
        if ok:
            self.shift_time_requested.emit(self._cursor_tick, sign * length)

    def _on_start_here(self):
        start, end = self._loop
        if end is None:
            end = self._structure.get('end') or 0
        if self._cursor_tick >= end:
            QMessageBox.warning(self, "Loop Start",
                                "Put the cursor before the loop's end first.")
            return
        self.loop_requested.emit(self._cursor_tick, end)

    def _on_end_here(self):
        start, _end = self._loop
        if start is None:
            start = 0
        if self._cursor_tick <= start:
            QMessageBox.warning(self, "Loop End",
                                "Put the cursor after the loop's start first.")
            return
        self.loop_requested.emit(start, self._cursor_tick)
