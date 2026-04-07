"""Song Structure Panel for the Piano Roll.

Right-side panel showing the song's structural commands in plain English:
- Sections (labels / markers)
- Loop Back points (GOTO)
- Pattern Calls (PATT/PEND subroutines)
- End Song (FINE)

Users can add, remove, reorder, and edit these to control how
the song plays back — intro sections, loop bodies, reusable patterns.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QComboBox, QSpinBox,
    QGroupBox, QMessageBox, QInputDialog, QAbstractItemView,
)

from ui.custom_widgets.scroll_guard import install_scroll_guard


# Friendly names for the assembly commands
_CMD_LABELS = {
    'LABEL':  'Section',
    'GOTO':   'Loop Back',
    'PATT':   'Play Pattern',
    'PEND':   'End Pattern',
    'FINE':   'End Song',
}

_CMD_COLORS = {
    'LABEL':  QColor(80, 180, 80),      # green
    'GOTO':   QColor(80, 140, 255),      # blue
    'PATT':   QColor(220, 160, 80),      # orange
    'PEND':   QColor(220, 160, 80, 140), # faded orange
    'FINE':   QColor(255, 80, 80),       # red
}

_CMD_DESCRIPTIONS = {
    'LABEL':  'Marks a named position in the song that can be jumped to.',
    'GOTO':   'Jumps back to a Section marker — creates a loop.',
    'PATT':   'Calls a reusable pattern section, then returns here.',
    'PEND':   'Marks the end of a reusable pattern section.',
    'FINE':   'Stops the song completely.',
}


class StructureItem:
    """One structural command in the song."""

    def __init__(self, cmd: str, tick: int, label: str = '',
                 target: str = '', track_index: int = 0):
        self.cmd = cmd          # LABEL, GOTO, PATT, PEND, FINE
        self.tick = tick        # flattened tick position
        self.label = label      # this item's label name (for LABEL)
        self.target = target    # target label name (for GOTO, PATT)
        self.track_index = track_index


class SongStructurePanel(QWidget):
    """Right-side panel for viewing and editing song structure."""

    structure_changed = pyqtSignal()  # emitted when user edits structure
    seek_to_tick = pyqtSignal(int)    # user wants to jump to a tick

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFixedWidth(220)
        self._items: list[StructureItem] = []
        self._labels: list[str] = []   # available section names
        self._total_ticks: int = 0
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        header = QLabel("Song Structure")
        header.setFont(QFont("", 10, QFont.Weight.Bold))
        header.setStyleSheet("color: #ccc;")
        layout.addWidget(header)

        # ── Structure list ──
        self._list = QListWidget()
        self._list.setStyleSheet("""
            QListWidget {
                background: #1e1e22;
                border: 1px solid #444;
                font-size: 11px;
            }
            QListWidget::item {
                padding: 4px 6px;
                border-bottom: 1px solid #333;
            }
            QListWidget::item:selected {
                background: #2a3a5a;
            }
        """)
        self._list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self._list.itemClicked.connect(self._on_item_clicked)
        self._list.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self._list, 1)

        # ── Add buttons ──
        add_group = QGroupBox("Add")
        add_layout = QVBoxLayout(add_group)
        add_layout.setContentsMargins(4, 8, 4, 4)
        add_layout.setSpacing(3)

        btn_section = QPushButton("+ Section")
        btn_section.setToolTip(
            "Add a named marker at a tick position.\n"
            "Other commands can jump to this section.")
        btn_section.clicked.connect(self._add_section)
        add_layout.addWidget(btn_section)

        btn_loop = QPushButton("+ Loop Back")
        btn_loop.setToolTip(
            "Add a jump-back point that loops to a Section.\n"
            "Everything between the Section and this point repeats.")
        btn_loop.clicked.connect(self._add_loop_back)
        add_layout.addWidget(btn_loop)

        btn_pattern = QPushButton("+ Pattern Call")
        btn_pattern.setToolTip(
            "Insert a call to a reusable pattern.\n"
            "Plays that pattern's notes, then comes back here.")
        btn_pattern.clicked.connect(self._add_pattern_call)
        add_layout.addWidget(btn_pattern)

        btn_end = QPushButton("+ End Song")
        btn_end.setToolTip("Add an end-of-song marker. Song stops here.")
        btn_end.clicked.connect(self._add_end_song)
        add_layout.addWidget(btn_end)

        layout.addWidget(add_group)

        # ── Remove button ──
        btn_row = QHBoxLayout()
        self._btn_remove = QPushButton("Remove")
        self._btn_remove.setToolTip("Remove the selected structure item.")
        self._btn_remove.clicked.connect(self._remove_selected)
        btn_row.addWidget(self._btn_remove)

        self._btn_edit = QPushButton("Edit")
        self._btn_edit.setToolTip(
            "Edit the selected item (change target, tick, name).")
        self._btn_edit.clicked.connect(self._on_item_double_clicked)
        btn_row.addWidget(self._btn_edit)
        layout.addLayout(btn_row)

    # ── Public API ──

    def load_from_song(self, song_data, total_ticks: int):
        """Extract structural commands from all tracks and display them."""
        self._items.clear()
        self._labels.clear()
        self._total_ticks = total_ticks

        # Collect all labels first
        for ti, track in enumerate(song_data.tracks):
            for cmd in track.commands:
                if cmd.cmd == 'LABEL' and cmd.target_label:
                    if cmd.target_label not in self._labels:
                        self._labels.append(cmd.target_label)

        # Only show structure from track 0 (all tracks should mirror)
        if song_data.tracks:
            track = song_data.tracks[0]
            for cmd in track.commands:
                if cmd.cmd in ('LABEL', 'GOTO', 'PATT', 'PEND', 'FINE'):
                    item = StructureItem(
                        cmd=cmd.cmd,
                        tick=cmd.tick,
                        label=cmd.target_label or '',
                        target=cmd.target_label or '',
                        track_index=0,
                    )
                    self._items.append(item)

        self._refresh_list()

    def get_structure_items(self) -> list[StructureItem]:
        """Return the current structure for saving."""
        return list(self._items)

    def get_loop_region(self) -> tuple[int | None, int | None]:
        """Return (loop_start_tick, loop_end_tick) from the structure.

        Finds the first GOTO and its target LABEL to determine the loop.
        """
        # Find first GOTO
        goto_item = None
        for item in self._items:
            if item.cmd == 'GOTO':
                goto_item = item
                break
        if goto_item is None:
            return (None, None)

        # Find the target label
        for item in self._items:
            if item.cmd == 'LABEL' and item.label == goto_item.target:
                return (item.tick, goto_item.tick)

        return (None, None)

    # ── List display ──

    def _refresh_list(self):
        self._list.clear()
        # Sort by tick position
        sorted_items = sorted(self._items, key=lambda x: x.tick)
        self._items = sorted_items

        for item in self._items:
            friendly = _CMD_LABELS.get(item.cmd, item.cmd)
            color = _CMD_COLORS.get(item.cmd, QColor(180, 180, 180))

            if item.cmd == 'LABEL':
                text = f"  {friendly}: \"{item.label}\"\n  at tick {item.tick}"
            elif item.cmd == 'GOTO':
                text = (f"  {friendly} → \"{item.target}\"\n"
                        f"  at tick {item.tick}")
            elif item.cmd == 'PATT':
                text = (f"  {friendly}: \"{item.target}\"\n"
                        f"  at tick {item.tick}")
            elif item.cmd == 'PEND':
                text = f"  End Pattern\n  at tick {item.tick}"
            elif item.cmd == 'FINE':
                text = f"  End Song\n  at tick {item.tick}"
            else:
                text = f"  {item.cmd} at tick {item.tick}"

            li = QListWidgetItem(text)
            li.setForeground(color)
            li.setData(Qt.ItemDataRole.UserRole, id(item))
            self._list.addItem(li)

    def _get_selected_item(self) -> StructureItem | None:
        row = self._list.currentRow()
        if row < 0 or row >= len(self._items):
            return None
        return self._items[row]

    # ── Click handlers ──

    def _on_item_clicked(self, li: QListWidgetItem):
        item = self._get_selected_item()
        if item:
            self.seek_to_tick.emit(item.tick)

    def _on_item_double_clicked(self, *_args):
        item = self._get_selected_item()
        if not item:
            return
        self._edit_item(item)

    def _edit_item(self, item: StructureItem):
        """Edit an existing structure item."""
        if item.cmd == 'LABEL':
            name, ok = QInputDialog.getText(
                self, "Edit Section",
                "Section name:",
                text=item.label)
            if ok and name.strip():
                old_name = item.label
                item.label = name.strip()
                # Update any GOTOs/PATTs that reference the old name
                for other in self._items:
                    if other.target == old_name:
                        other.target = item.label

        elif item.cmd in ('GOTO', 'PATT'):
            if not self._labels:
                QMessageBox.information(
                    self, "No Sections",
                    "Add a Section first before setting a target.")
                return
            targets = list(self._labels)
            current = targets.index(item.target) if item.target in targets else 0
            target, ok = QInputDialog.getItem(
                self, f"Edit {_CMD_LABELS[item.cmd]}",
                "Jump to section:", targets, current, False)
            if ok:
                item.target = target

        # All items: edit tick position
        tick, ok = QInputDialog.getInt(
            self, "Edit Position",
            "Tick position:", item.tick, 0, self._total_ticks)
        if ok:
            item.tick = tick

        self._refresh_list()
        self.structure_changed.emit()

    # ── Add commands ──

    def _add_section(self):
        name, ok = QInputDialog.getText(
            self, "New Section",
            "Section name (e.g. 'Intro', 'Chorus', 'Loop'):")
        if not ok or not name.strip():
            return

        tick, ok = QInputDialog.getInt(
            self, "Section Position",
            "Place at tick:", 0, 0, max(1, self._total_ticks))
        if not ok:
            return

        label_name = name.strip().replace(' ', '_')
        self._items.append(StructureItem(
            cmd='LABEL', tick=tick, label=label_name))
        if label_name not in self._labels:
            self._labels.append(label_name)
        self._refresh_list()
        self.structure_changed.emit()

    def _add_loop_back(self):
        if not self._labels:
            QMessageBox.information(
                self, "No Sections",
                "You need to add a Section first.\n\n"
                "A Loop Back jumps to a Section — so create the\n"
                "Section where you want the loop to start, then\n"
                "add the Loop Back where you want it to repeat from.")
            return

        target, ok = QInputDialog.getItem(
            self, "Loop Back Target",
            "Loop back to which section?",
            self._labels, 0, False)
        if not ok:
            return

        tick, ok = QInputDialog.getInt(
            self, "Loop Back Position",
            "Place Loop Back at tick:", self._total_ticks,
            0, max(1, self._total_ticks))
        if not ok:
            return

        self._items.append(StructureItem(
            cmd='GOTO', tick=tick, target=target))
        self._refresh_list()
        self.structure_changed.emit()

    def _add_pattern_call(self):
        if not self._labels:
            QMessageBox.information(
                self, "No Sections",
                "You need to add a Section first.\n\n"
                "A Pattern Call plays the notes from a Section,\n"
                "then returns to continue from here.")
            return

        target, ok = QInputDialog.getItem(
            self, "Pattern to Play",
            "Play which section's pattern?",
            self._labels, 0, False)
        if not ok:
            return

        tick, ok = QInputDialog.getInt(
            self, "Pattern Call Position",
            "Insert Pattern Call at tick:", 0,
            0, max(1, self._total_ticks))
        if not ok:
            return

        self._items.append(StructureItem(
            cmd='PATT', tick=tick, target=target))
        self._refresh_list()
        self.structure_changed.emit()

    def _add_end_song(self):
        tick, ok = QInputDialog.getInt(
            self, "End Song Position",
            "End the song at tick:", self._total_ticks,
            0, max(1, self._total_ticks))
        if not ok:
            return

        self._items.append(StructureItem(
            cmd='FINE', tick=tick))
        self._refresh_list()
        self.structure_changed.emit()

    def _remove_selected(self):
        item = self._get_selected_item()
        if not item:
            return

        friendly = _CMD_LABELS.get(item.cmd, item.cmd)
        reply = QMessageBox.question(
            self, "Remove",
            f"Remove this {friendly} at tick {item.tick}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return

        # If removing a label, also remove from the label list
        if item.cmd == 'LABEL' and item.label in self._labels:
            self._labels.remove(item.label)

        self._items.remove(item)
        self._refresh_list()
        self.structure_changed.emit()
