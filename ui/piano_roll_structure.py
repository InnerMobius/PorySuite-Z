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

What the panel edits is what a save writes. The loop: set, move or remove it
here (or with the cursor buttons); double-click its lines to type new ticks.
The patterns: the build makes them by itself from repeats in the notes, and
removing them tells the build not to (midi.cfg -N); the song sounds the same,
with the repeats written out in full. It cannot skip just one repeat, so
removing a pattern line removes them all. Clear All does both. Every change
goes through the window, into the undo history and into the next save.
The song's end is where its music stops; it is shortened with Remove Time.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, QEvent, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget,
    QListWidgetItem, QAbstractItemView, QDialog, QDialogButtonBox, QFormLayout,
    QSpinBox, QMessageBox, QMenu, QToolTip,
)

from ui.custom_widgets.scroll_guard import install_scroll_guard


_KIND_TEXT = {
    'loop_start': 'Loop starts',
    'loop_end': 'Loop ends (jumps back)',
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
    def __init__(self, start: int, end: int, limit: int, where, parent=None,
                 focus: str = 'start'):
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
        form.addRow("Loop ends at:", self._end)
        form.addRow(self._hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self._start.valueChanged.connect(self._update_hint)
        self._end.valueChanged.connect(self._update_hint)
        self._update_hint()
        box = self._end if focus == 'end' else self._start
        box.setFocus()
        box.selectAll()

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
    #: True: the build makes patterns of the song's repeats; False: it writes
    #: them out in full. Sounds the same either way.
    patterns_requested = pyqtSignal(bool)
    #: Clear All: no loop and no patterns, as one undo step.
    clear_requested = pyqtSignal()
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
        # Whether the build makes patterns (None: the song has no midi.cfg
        # line, so there is nothing to switch).
        self._patterns_on: Optional[bool] = None
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
        note = QLabel("Times are when each part plays.\nClick an entry to jump there.\n"
                      "Double-click the loop to change it.\n"
                      "Delete or right-click removes a line.")
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
        self._list.itemDoubleClicked.connect(
            lambda item: self._edit_loop_at(self._list.row(item)))
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_list_menu)
        self._list.installEventFilter(self)
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
        self._patterns_label = QLabel()
        self._patterns_label.setWordWrap(True)
        self._patterns_label.setStyleSheet("color: #dca050; font-size: 10px;")
        layout.addWidget(self._patterns_label)
        self._btn_clear = QPushButton("Clear All")
        self._btn_clear.setToolTip(
            "Remove the loop and the patterns. The song sounds the same apart from "
            "no longer looping. Ctrl+Z takes it back.")
        self._btn_clear.clicked.connect(self._on_clear_all)
        layout.addWidget(self._btn_clear)

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

    def set_patterns(self, on: Optional[bool]) -> None:
        """Whether the build makes patterns of the song's repeats; None when
        the song has no midi.cfg line to say so."""
        self._patterns_on = on
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
        shown_patterns = [] if self._patterns_on is False else st['patterns']
        entries = [(t, 'pattern', self._friendly(n)) for t, n in shown_patterns]
        entries += [(t, 'section', self._friendly(n)) for t, n in st['sections']]
        start, end = self._loop
        if start is not None and end is not None:
            entries += [(start, 'loop_start', ''), (end, 'loop_end', '')]
            self._loop_label.setText(f"Loop: {self.where(start)} → {self.where(end)}")
            self._btn_remove.setEnabled(True)
        else:
            self._loop_label.setText("No loop: the song plays once.")
            self._btn_remove.setEnabled(False)
        if self._patterns_on is False:
            self._patterns_label.setText(
                "Patterns off: repeats are written out in full (sounds the same). "
                "Right-click to turn back on.")
        elif self._patterns_on and not st['patterns']:
            self._patterns_label.setText(
                "Patterns on: the build looks for repeats when the song is saved.")
        else:
            self._patterns_label.setText("")
        self._patterns_label.setVisible(bool(self._patterns_label.text()))
        self._btn_clear.setEnabled(start is not None
                                   or (self._patterns_on is True and bool(st['patterns'])))
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

    # ── editing the list: only the loop's lines are saved data ────────
    def _row_kind(self, row: int):
        return self._entries[row][1] if 0 <= row < len(self._entries) else None

    def _why_fixed(self, kind) -> str:
        if kind == 'end':
            return ("This is where the song's music stops, so it can't be removed. "
                    "Use Remove Time to shorten the song.")
        if kind == 'pattern':
            return ("This song has no line in midi.cfg, so the build's patterns "
                    "can't be switched off for it.")
        return ("This marker is written in the song's own file; it is not made from "
                "the notes, so it can't be removed here.")

    def _pattern_count(self) -> int:
        return len(self._structure.get('patterns') or [])

    def _confirm_remove_patterns(self) -> bool:
        n = self._pattern_count()
        answer = QMessageBox.question(
            self, "Remove Patterns",
            f"The build makes all {n} pattern line{'s' if n != 1 else ''} by itself, from "
            "repeats in the notes, and it can't skip just one.\n\n"
            "Remove them all? The song sounds exactly the same: the repeats are "
            "written out in full instead (it takes a little more space in the ROM).\n\n"
            "Saved with the song. Ctrl+Z takes it back.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes)
        return answer == QMessageBox.StandardButton.Yes

    def _on_clear_all(self):
        self.clear_requested.emit()

    def _edit_loop_at(self, row: int):
        kind = self._row_kind(row)
        if kind not in ('loop_start', 'loop_end'):
            return
        start, end = self._loop
        dlg = _LoopDialog(start, end, self._limit(), self.where, self,
                          focus='end' if kind == 'loop_end' else 'start')
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.loop_requested.emit(*dlg.values())

    def _remove_at(self, row: int):
        """A loop has two ends; removing either removes the loop. The build
        makes every pattern line, so removing one removes them all."""
        kind = self._row_kind(row)
        if kind in ('loop_start', 'loop_end'):
            self.loop_requested.emit(None, None)
        elif kind == 'pattern' and self._patterns_on:
            if self._confirm_remove_patterns():
                self.patterns_requested.emit(False)
        elif kind is not None:
            rect = self._list.visualItemRect(self._list.item(row))
            QToolTip.showText(self._list.viewport().mapToGlobal(rect.center()),
                              self._why_fixed(kind), self._list)

    def eventFilter(self, obj, event):
        if (obj is self._list and event.type() == QEvent.Type.KeyPress
                and event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace)):
            self._remove_at(self._list.currentRow())
            return True
        return super().eventFilter(obj, event)

    def _on_list_menu(self, pos):
        item = self._list.itemAt(pos)
        if item is None:
            return
        row = self._list.row(item)
        self._list.setCurrentRow(row)
        kind = self._row_kind(row)
        menu = QMenu(self)
        jump = menu.addAction("Jump Here")
        edit = remove = patterns_back = clear = None
        if kind in ('loop_start', 'loop_end'):
            edit = menu.addAction("Change Loop…")
            remove = menu.addAction("Remove Loop")
        elif kind == 'pattern' and self._patterns_on:
            remove = menu.addAction(f"Remove All Patterns ({self._pattern_count()})")
        else:
            why = menu.addAction("Can't remove: " + (
                "the music stops here (use Remove Time)" if kind == 'end'
                else "no midi.cfg line for this song" if kind == 'pattern'
                else "written in the song's own file"))
            why.setEnabled(False)
        menu.addSeparator()
        if self._patterns_on is False:
            patterns_back = menu.addAction("Let the Build Make Patterns Again")
        clear = menu.addAction("Clear All")
        clear.setEnabled(self._btn_clear.isEnabled())
        chosen = menu.exec(self._list.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen is jump:
            self.seek_to_tick.emit(self._entries[row][0])
        elif chosen is edit:
            self._edit_loop_at(row)
        elif chosen is remove:
            self._remove_at(row)
        elif chosen is patterns_back:
            self.patterns_requested.emit(True)
        elif chosen is clear:
            self._on_clear_all()

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
