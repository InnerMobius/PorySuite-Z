"""Script Lookup dialog — search for script labels across the entire project.

Ctrl+Shift+F or the "Find Script" toolbar button opens this dialog.
Type to filter, double-click or Enter to navigate to the result.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QKeyEvent, QShortcut, QKeySequence
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLineEdit, QTreeWidget,
    QTreeWidgetItem, QLabel, QPushButton, QHeaderView,
)

from eventide.backend.script_index import ScriptIndex, ScriptLocation


class ScriptSearchDialog(QDialog):
    """Project-wide script label search dialog."""

    # Emitted when the user picks a result: (label, map_name_or_None)
    navigate_requested = pyqtSignal(str, object)

    def __init__(self, index: ScriptIndex, parent=None):
        super().__init__(parent)
        self._index = index
        self.setWindowTitle('Find Script')
        self.setMinimumSize(700, 500)
        self.resize(750, 550)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)

        # ── Search bar ────────────────────────────────────────────────
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel('Search:'))
        self._search = QLineEdit()
        self._search.setPlaceholderText(
            'Type a script label name (e.g. "SignLady" or "OakTrigger")...')
        self._search.setClearButtonEnabled(True)
        search_row.addWidget(self._search, 1)
        lay.addLayout(search_row)

        # ── Results tree ──────────────────────────────────────────────
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(['Label', 'Map / Source', 'Type'])
        self._tree.setRootIsDecorated(False)
        self._tree.setAlternatingRowColors(True)
        self._tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        header = self._tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        lay.addWidget(self._tree, 1)

        # ── Status + buttons ─────────────────────────────────────────
        bottom = QHBoxLayout()
        self._status = QLabel(f'{index.count} labels indexed')
        bottom.addWidget(self._status)
        bottom.addStretch()
        btn_go = QPushButton('Go To')
        btn_go.setDefault(True)
        btn_go.clicked.connect(self._accept_selection)
        btn_close = QPushButton('Close')
        btn_close.clicked.connect(self.reject)
        bottom.addWidget(btn_go)
        bottom.addWidget(btn_close)
        lay.addLayout(bottom)

        # ── Wiring ────────────────────────────────────────────────────
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(100)
        self._debounce.timeout.connect(self._do_search)

        self._search.textChanged.connect(self._on_text_changed)
        self._tree.itemDoubleClicked.connect(self._on_double_click)
        self._tree.itemActivated.connect(self._on_double_click)

        self._search.setFocus()

    # ── Search logic ──────────────────────────────────────────────────

    def _on_text_changed(self, _text: str):
        self._debounce.start()

    def _do_search(self):
        query = self._search.text().strip()
        self._tree.clear()

        if not query:
            self._status.setText(f'{self._index.count} labels indexed')
            return

        results = self._index.search(query, limit=200)
        for loc in results:
            item = QTreeWidgetItem()
            item.setText(0, loc.label)

            if loc.map_name:
                item.setText(1, loc.map_name)
            else:
                # Shared script — show filename
                item.setText(1, loc.source_file.name)

            type_labels = {
                'map': 'Map Script',
                'shared': 'Shared',
                'event_scripts': 'Event Scripts',
            }
            item.setText(2, type_labels.get(loc.source_type, loc.source_type))

            # Dim shared/event_scripts slightly
            if loc.source_type != 'map':
                for col in range(3):
                    item.setForeground(col, QColor('#888888'))

            item.setData(0, Qt.ItemDataRole.UserRole, loc)
            self._tree.addTopLevelItem(item)

        n = len(results)
        suffix = '' if n < 200 else '+'
        self._status.setText(f'{n}{suffix} result{"s" if n != 1 else ""} found')

        if results:
            self._tree.setCurrentItem(self._tree.topLevelItem(0))

    # ── Navigation ────────────────────────────────────────────────────

    def _accept_selection(self):
        item = self._tree.currentItem()
        if not item:
            return
        loc: ScriptLocation = item.data(0, Qt.ItemDataRole.UserRole)
        if loc:
            self.navigate_requested.emit(loc.label, loc.map_name)
            self.accept()

    def _on_double_click(self, item: QTreeWidgetItem, _col: int = 0):
        loc: ScriptLocation = item.data(0, Qt.ItemDataRole.UserRole)
        if loc:
            self.navigate_requested.emit(loc.label, loc.map_name)
            self.accept()

    def keyPressEvent(self, event: QKeyEvent):
        """Enter in search box or tree accepts selection."""
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self._tree.currentItem():
                self._accept_selection()
                return
        super().keyPressEvent(event)
