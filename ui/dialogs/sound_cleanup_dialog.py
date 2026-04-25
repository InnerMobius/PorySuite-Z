"""
Sound Editor Cleanup Dialog.

Shows orphaned/dead audio data and lets the user delete it.
"""

from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTreeWidget, QTreeWidgetItem,
    QPushButton, QLabel, QMessageBox, QHeaderView,
)
from PyQt6.QtGui import QColor, QFont

_log = logging.getLogger("SoundEditor.Cleanup")

_CAT_LABELS = {
    "bin":        "Orphaned Sample Files (.bin)",
    "inc_entry":  "Broken .inc Entries (file missing)",
    "song":       "Orphaned Song Files (.s / .mid)",
    "voicegroup": "Orphaned Voicegroups",
}

_ENTRY_ROLE = Qt.ItemDataRole.UserRole + 201


class SoundCleanupDialog(QDialog):
    """Dialog for finding and deleting orphaned sound data."""

    def __init__(self, project_root: str, sample_data, parent=None):
        super().__init__(parent)
        self._project_root = project_root
        self._sample_data = sample_data
        self.setWindowTitle("Clean Up Sound Files")
        self.setMinimumSize(720, 500)
        self._build_ui()
        self._run_scan()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Description
        desc = QLabel(
            "The scanner below checks for dead audio data that is no longer "
            "used but still compiled into every ROM build.\n"
            "Select items to delete and click Delete Selected."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #aaa; font-size: 11px;")
        layout.addWidget(desc)

        # Tree
        self._tree = QTreeWidget()
        self._tree.setColumnCount(3)
        self._tree.setHeaderLabels(["Item", "Path", "ROM Space"])
        header = self._tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.setAlternatingRowColors(True)
        self._tree.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self._tree)

        # Footer: total space label
        self._total_label = QLabel("Total recoverable: 0 bytes")
        self._total_label.setStyleSheet("font-size: 11px;")
        layout.addWidget(self._total_label)

        # Buttons
        btn_row = QHBoxLayout()
        self._btn_scan = QPushButton("Scan Again")
        self._btn_scan.clicked.connect(self._run_scan)
        btn_row.addWidget(self._btn_scan)
        btn_row.addStretch()
        self._btn_delete = QPushButton("Delete Selected")
        self._btn_delete.setStyleSheet(
            "QPushButton { background: #8b2020; color: white; }"
            "QPushButton:hover { background: #b02020; }"
            "QPushButton:disabled { background: #444; color: #888; }"
        )
        self._btn_delete.clicked.connect(self._on_delete)
        self._btn_delete.setEnabled(False)
        btn_row.addWidget(self._btn_delete)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _run_scan(self):
        from core.sound.sound_cleanup import (
            scan_orphaned_bins,
            scan_broken_inc_entries,
            scan_orphaned_songs,
            scan_orphaned_voicegroups,
        )

        self._tree.blockSignals(True)
        self._tree.clear()
        self._tree.blockSignals(False)

        results = {
            "bin":        scan_orphaned_bins(self._project_root, self._sample_data),
            "inc_entry":  scan_broken_inc_entries(self._project_root),
            "song":       scan_orphaned_songs(self._project_root),
            "voicegroup": scan_orphaned_voicegroups(self._project_root),
        }

        total_found = sum(len(v) for v in results.values())
        if total_found == 0:
            info = QTreeWidgetItem(["No orphaned data found — your sound directory is clean.", "", ""])
            info.setFlags(Qt.ItemFlag.ItemIsEnabled)
            info.setForeground(0, QColor("#6a6"))
            self._tree.addTopLevelItem(info)
            self._btn_delete.setEnabled(False)
            self._total_label.setText("Total recoverable: 0 bytes")
            return

        self._tree.blockSignals(True)
        try:
            for cat, entries in results.items():
                if not entries:
                    continue
                cat_item = QTreeWidgetItem([_CAT_LABELS[cat], "", ""])
                cat_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
                cat_item.setCheckState(0, Qt.CheckState.Unchecked)
                cat_item.setFont(0, QFont("", -1, QFont.Weight.Bold))
                self._tree.addTopLevelItem(cat_item)

                for entry in entries:
                    path_str = str(entry.file_path) if entry.file_path else "(no file)"
                    # Truncate path for display
                    if len(path_str) > 60:
                        path_str = "\u2026" + path_str[-57:]
                    size_str = _fmt_size(entry.size_bytes)
                    child = QTreeWidgetItem([entry.label, path_str, size_str])
                    child.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
                    child.setCheckState(0, Qt.CheckState.Unchecked)
                    child.setData(0, _ENTRY_ROLE, entry)
                    cat_item.addChild(child)

                cat_item.setExpanded(True)
        finally:
            self._tree.blockSignals(False)

        self._update_total()

    def _on_item_changed(self, item, column):
        if column != 0:
            return

        # Propagate parent -> children
        if item.childCount() > 0:
            self._tree.blockSignals(True)
            state = item.checkState(0)
            for i in range(item.childCount()):
                item.child(i).setCheckState(0, state)
            self._tree.blockSignals(False)
        else:
            # Update parent tri-state
            parent = item.parent()
            if parent:
                self._tree.blockSignals(True)
                checked = sum(
                    1 for i in range(parent.childCount())
                    if parent.child(i).checkState(0) == Qt.CheckState.Checked
                )
                if checked == 0:
                    parent.setCheckState(0, Qt.CheckState.Unchecked)
                elif checked == parent.childCount():
                    parent.setCheckState(0, Qt.CheckState.Checked)
                else:
                    parent.setCheckState(0, Qt.CheckState.PartiallyChecked)
                self._tree.blockSignals(False)

        self._update_total()

    def _update_total(self):
        total = 0
        count = 0
        for i in range(self._tree.topLevelItemCount()):
            cat = self._tree.topLevelItem(i)
            for j in range(cat.childCount()):
                child = cat.child(j)
                if child.checkState(0) == Qt.CheckState.Checked:
                    entry = child.data(0, _ENTRY_ROLE)
                    if entry:
                        total += entry.size_bytes
                        count += 1
        self._total_label.setText(
            f"Selected: {count} item(s), ~{_fmt_size(total)} recoverable"
        )
        self._btn_delete.setEnabled(count > 0)

    def _on_delete(self):
        # Collect checked entries
        selected = []
        for i in range(self._tree.topLevelItemCount()):
            cat = self._tree.topLevelItem(i)
            for j in range(cat.childCount()):
                child = cat.child(j)
                if child.checkState(0) == Qt.CheckState.Checked:
                    entry = child.data(0, _ENTRY_ROLE)
                    if entry:
                        selected.append(entry)

        if not selected:
            return

        total_size = sum(e.size_bytes for e in selected)
        reply = QMessageBox.question(
            self, "Confirm Delete",
            f"This will permanently delete {len(selected)} item(s) "
            f"(~{_fmt_size(total_size)} of ROM space).\n\n"
            f"This cannot be undone. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        from core.sound.sound_cleanup import delete_entries
        errors = delete_entries(selected, self._project_root)

        if errors:
            QMessageBox.warning(
                self, "Some Deletions Failed",
                "The following errors occurred:\n\n" + "\n".join(errors)
            )

        # Rescan to reflect the deletions
        self._run_scan()


def _fmt_size(n: int) -> str:
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} B"
