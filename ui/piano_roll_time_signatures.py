"""Time-signature editor for the Piano Roll.

A song's time signatures decide where its bars fall: mid2agb lays the song
out in bars from them (bar comments, where long waits split, where pattern
compression starts). They do not change what plays. The list is edited by
BAR, the way musicians count; positions are converted to playback ticks with
the same rule the build uses (``midi_exporter.bar_starts_for``).
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QSpinBox, QComboBox, QDialogButtonBox, QMessageBox, QHeaderView,
)

from ui.custom_widgets.scroll_guard import install_scroll_guard

_DENOMINATORS = (1, 2, 4, 8, 16, 32)


def ticks_to_bars(signatures: list) -> list:
    """[(bar number, num, den)] for [(tick, num, den)]."""
    from core.sound.midi_exporter import bar_starts_for
    sigs = sorted(signatures)
    out = []
    for i, (t, num, den) in enumerate(sigs):
        bars = bar_starts_for(sigs[:i], t)
        out.append((bars.index(t) + 1 if t in bars else len(bars) + 1, num, den))
    return out


def bars_to_ticks(entries: list) -> list:
    """[(tick, num, den)] for [(bar number, num, den)], bar 1 = tick 0."""
    from core.sound.midi_exporter import bar_starts_for
    out = []
    for bar, num, den in sorted(entries):
        bars = bar_starts_for(out, 96 * 64 * max(1, bar))
        out.append((bars[bar - 1], num, den))
    return out


class TimeSignatureDialog(QDialog):
    def __init__(self, signatures: list, total_ticks: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Time Signatures")
        self.resize(380, 320)
        lay = QVBoxLayout(self)
        note = QLabel("Each row starts a time signature at a bar. Bars before the\n"
                      "first row are 4/4. This changes where bars fall, not what plays.")
        note.setStyleSheet("color: #999;")
        lay.addWidget(note)
        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["From bar", "Beats", "Beat value"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self._table, 1)
        row = QHBoxLayout()
        add = QPushButton("Add")
        add.clicked.connect(lambda: self._add_row(self._next_bar(), 4, 4))
        row.addWidget(add)
        rem = QPushButton("Remove")
        rem.clicked.connect(self._remove_row)
        row.addWidget(rem)
        row.addStretch()
        lay.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self._result = list(signatures)
        self._initial = ticks_to_bars(signatures)
        for bar, num, den in self._initial:
            self._add_row(bar, num, den)

    def _next_bar(self) -> int:
        bars = [self._table.cellWidget(r, 0).value() for r in range(self._table.rowCount())]
        return (max(bars) + 4) if bars else 1

    def _add_row(self, bar: int, num: int, den: int):
        r = self._table.rowCount()
        self._table.insertRow(r)
        b = QSpinBox()
        b.setRange(1, 9999)
        b.setValue(bar)
        n = QSpinBox()
        n.setRange(1, 64)
        n.setValue(num)
        d = QComboBox()
        for v in _DENOMINATORS:
            d.addItem(str(v), v)
        d.setCurrentIndex(max(0, d.findData(den)))
        for w in (b, n, d):
            install_scroll_guard(w)
        self._table.setCellWidget(r, 0, b)
        self._table.setCellWidget(r, 1, n)
        self._table.setCellWidget(r, 2, d)

    def _remove_row(self):
        r = self._table.currentRow()
        if r >= 0:
            self._table.removeRow(r)

    def entries(self) -> list:
        return [(self._table.cellWidget(r, 0).value(), self._table.cellWidget(r, 1).value(),
                 self._table.cellWidget(r, 2).currentData())
                for r in range(self._table.rowCount())]

    def _accept(self):
        from core.sound.midi_exporter import valid_time_signature
        entries = self.entries()
        if entries == self._initial:
            self.accept()           # unchanged: keep the song's list exactly
            return
        bars = [e[0] for e in entries]
        if len(bars) != len(set(bars)):
            QMessageBox.warning(self, "Time Signatures", "Two rows start at the same bar.")
            return
        for bar, num, den in entries:
            if not valid_time_signature(num, den):
                QMessageBox.warning(
                    self, "Time Signatures",
                    f"{num}/{den} (bar {bar}) cannot be laid out by the game's build: a bar "
                    f"must be a whole number of ticks (96 x beats / beat value).")
                return
        self._result = bars_to_ticks(entries)
        self.accept()

    def signatures(self) -> list:
        return list(self._result)
