"""MIDI Events window: every event in a song's .mid, read-only.

Lists what ``core.sound.midi_inspect`` finds — where each event is, which of
the song's tracks it belongs to, and whether the game's build uses it — with
filters. It is for looking (troubleshooting an imported or externally edited
file, checking what another editor left behind). It edits nothing: changes to
the music go through the Piano Roll, whose saves place each edit in the right
MIDI track and channel and check the result.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QAbstractTableModel, QModelIndex, QSortFilterProxyModel
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QLineEdit, QCheckBox,
    QTableView, QHeaderView,
)

from ui.custom_widgets.scroll_guard import install_scroll_guard

_COLUMNS = ("MIDI track", "MIDI tick", "Game tick", "Channel", "Song track",
            "Event", "Values", "What the game does with it")


class _Model(QAbstractTableModel):
    def __init__(self, rows):
        super().__init__()
        self.rows = rows

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(_COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return _COLUMNS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        r = self.rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            c = index.column()
            return (f"{r.chunk}" + (f" — {r.chunk_name}" if r.chunk_name else ""),
                    r.midi_tick, r.game_tick,
                    "" if r.channel is None else r.channel + 1,
                    "" if r.song_track is None else r.song_track + 1,
                    r.kind, r.detail,
                    r.meaning if r.used else f"not used: {r.meaning}")[c]
        if role == Qt.ItemDataRole.ForegroundRole and not r.used:
            return QColor(150, 150, 150)
        if role == Qt.ItemDataRole.UserRole:
            return r
        return None


class _Filter(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self.chunk = None
        self.text = ""
        self.unused_only = False

    def filterAcceptsRow(self, row, parent):
        r = self.sourceModel().rows[row]
        if self.chunk is not None and r.chunk != self.chunk:
            return False
        if self.unused_only and r.used:
            return False
        if self.text:
            hay = f"{r.kind} {r.detail} {r.meaning}".lower()
            if self.text.lower() not in hay:
                return False
        return True


class MidiEventInspector(QDialog):
    def __init__(self, label: str, mid_bytes: bytes, tempo_base: int, parent=None):
        super().__init__(parent)
        from core.sound.midi_inspect import inspect_midi
        self.setWindowTitle(f"MIDI Events — {label}.mid")
        self.resize(980, 560)
        rows = inspect_midi(mid_bytes, tempo_base)
        lay = QVBoxLayout(self)
        unused = sum(1 for r in rows if not r.used)
        lay.addWidget(QLabel(
            f"{len(rows)} events, {unused} of them not used by the game's build (shown grey). "
            "Read-only: PorySuite's saves change only the events an edit is about."))
        bar = QHBoxLayout()
        bar.addWidget(QLabel("MIDI track:"))
        self._chunk = QComboBox()
        install_scroll_guard(self._chunk)
        self._chunk.addItem("All", None)
        for ci in sorted({r.chunk for r in rows}):
            name = next((r.chunk_name for r in rows if r.chunk == ci and r.chunk_name), "")
            self._chunk.addItem(f"{ci}" + (f" — {name}" if name else ""), ci)
        bar.addWidget(self._chunk)
        self._text = QLineEdit()
        self._text.setPlaceholderText("Filter (e.g. controller, tempo, note 60)…")
        bar.addWidget(self._text, 1)
        self._unused = QCheckBox("Only events the game ignores")
        bar.addWidget(self._unused)
        lay.addLayout(bar)
        self._model = _Model(rows)
        self._proxy = _Filter()
        self._proxy.setSourceModel(self._model)
        self._view = QTableView()
        self._view.setModel(self._proxy)
        self._view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._view.horizontalHeader().setStretchLastSection(True)
        self._view.verticalHeader().setVisible(False)
        lay.addWidget(self._view, 1)
        self._chunk.currentIndexChanged.connect(self._refilter)
        self._text.textChanged.connect(self._refilter)
        self._unused.toggled.connect(self._refilter)

    def _refilter(self, *_):
        self._proxy.chunk = self._chunk.currentData()
        self._proxy.text = self._text.text().strip()
        self._proxy.unused_only = self._unused.isChecked()
        self._proxy.invalidateFilter()

    def visible_rows(self) -> list:
        return [self._proxy.data(self._proxy.index(i, 0), Qt.ItemDataRole.UserRole)
                for i in range(self._proxy.rowCount())]
