"""
label_manager.py — Constant Label Manager for PorySuite-Z

Standalone toolbar page for assigning friendly, readable labels to flags,
vars, and other constants. Labels are display-only — they never touch game
source code. Stored in a project-level ``labels.json`` file.

The Event Editor uses these labels to show friendly names with color-coded
formatting instead of raw ALL_CAPS_CONSTANTS.
"""

import json
import os

from PyQt6.QtCore import Qt, pyqtSignal, QSize
from PyQt6.QtGui import QFont, QColor, QIcon
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QPushButton, QLineEdit, QTextEdit, QPlainTextEdit,
    QListWidget, QListWidgetItem, QGroupBox, QFrame,
    QComboBox, QSplitter, QMessageBox, QScrollArea,
    QSizePolicy, QAbstractItemView, QTabBar,
)


# ─── Helpers ────────────────────────────────────────────────────────────────

_LABELS_FILENAME = "porysuite_labels.json"

_TYPE_FLAG = "flag"
_TYPE_VAR = "var"

_TYPE_COLORS = {
    _TYPE_FLAG: "#2ecc71",   # green badge
    _TYPE_VAR:  "#3498db",   # blue badge
}


def _make_type_badge(label_type: str) -> str:
    """Return a short uppercase tag for display in the list."""
    return label_type.upper()


# ─── Widget ─────────────────────────────────────────────────────────────────

class LabelManagerWidget(QWidget):
    """Standalone page for managing friendly labels on flags and vars."""

    labels_changed = pyqtSignal()
    modified = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._project_dir = ""
        self._labels: dict[str, dict] = {}  # constant_name -> {label, notes, color}
        self._all_constants: list[tuple[str, str]] = []  # (name, type)
        self._dirty = False
        self._build_ui()

    # ─── UI construction ────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        # Title
        title = QLabel("Label Manager")
        title.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        root.addWidget(title)

        subtitle = QLabel(
            "Assign friendly names to flags and vars. Labels are saved per "
            "project and only affect how constants are displayed — the game "
            "code is never changed.")
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: #888; margin-bottom: 8px;")
        root.addWidget(subtitle)

        # Main splitter: list on left, detail on right
        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter, stretch=1)

        # ── Left panel: filter + constant list ──────────────────────────────
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        # Filter tabs (All / Flags / Vars)
        filter_row = QHBoxLayout()
        self._filter_combo = QComboBox()
        self._filter_combo.addItems(["All", "Flags", "Vars"])
        self._filter_combo.currentIndexChanged.connect(self._apply_filter)
        # Block scroll wheel when closed (CLAUDE.md rule)
        self._filter_combo.wheelEvent = lambda e: e.ignore()
        filter_row.addWidget(QLabel("Show:"))
        filter_row.addWidget(self._filter_combo)
        filter_row.addStretch()

        # Counts label
        self._counts_label = QLabel("")
        self._counts_label.setStyleSheet("color: #888;")
        filter_row.addWidget(self._counts_label)

        left_layout.addLayout(filter_row)

        # Search bar
        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText("Search constants or labels...")
        self._search_box.setClearButtonEnabled(True)
        self._search_box.textChanged.connect(self._apply_filter)
        left_layout.addWidget(self._search_box)

        # Constant list
        self._const_list = QListWidget()
        self._const_list.setAlternatingRowColors(True)
        self._const_list.currentItemChanged.connect(self._on_selection_changed)
        left_layout.addWidget(self._const_list)

        splitter.addWidget(left)

        # ── Right panel: detail editor ──────────────────────────────────────
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 0, 0, 0)

        # Constant name (read-only)
        self._detail_name = QLabel("Select a constant from the list")
        self._detail_name.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        self._detail_name.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self._detail_name.setWordWrap(True)
        right_layout.addWidget(self._detail_name)

        self._detail_type_badge = QLabel("")
        self._detail_type_badge.setStyleSheet(
            "font-weight: bold; padding: 2px 8px; border-radius: 4px;")
        right_layout.addWidget(self._detail_type_badge)

        right_layout.addSpacing(12)

        # Label field
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._label_edit = QLineEdit()
        self._label_edit.setPlaceholderText("Friendly name for this constant...")
        self._label_edit.setMaxLength(80)
        self._label_edit.textChanged.connect(self._on_label_edited)
        self._label_edit.setEnabled(False)
        form.addRow("Label:", self._label_edit)

        # Notes field
        self._notes_edit = QPlainTextEdit()
        self._notes_edit.setPlaceholderText(
            "Optional notes — what this flag/var is for, where it's used...")
        self._notes_edit.setMaximumHeight(100)
        self._notes_edit.textChanged.connect(self._on_notes_edited)
        self._notes_edit.setEnabled(False)
        form.addRow("Notes:", self._notes_edit)

        right_layout.addLayout(form)

        right_layout.addStretch()

        # Save / discard buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._save_btn = QPushButton("Save Labels")
        self._save_btn.setEnabled(False)
        self._save_btn.clicked.connect(self._save_labels)
        btn_row.addWidget(self._save_btn)
        right_layout.addLayout(btn_row)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)

        # Status bar at bottom
        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #888; margin-top: 4px;")
        root.addWidget(self._status_label)

    # ─── Project loading ────────────────────────────────────────────────────

    def load_project(self, project_dir: str):
        """Load constants from the project and any saved labels."""
        self._project_dir = project_dir
        self._load_constants()
        self._load_labels()
        self._apply_filter()
        self._update_counts()

    def _load_constants(self):
        """Pull flag and var constants from ConstantsManager."""
        self._all_constants = []
        try:
            from eventide.backend.constants_manager import ConstantsManager
            for name in ConstantsManager.FLAGS:
                self._all_constants.append((name, _TYPE_FLAG))
            for name in ConstantsManager.VARS:
                self._all_constants.append((name, _TYPE_VAR))
        except Exception:
            pass
        self._all_constants.sort(key=lambda x: x[0])

    def _load_labels(self):
        """Load labels from the project's labels.json file."""
        self._labels = {}
        path = self._labels_path()
        if not path or not os.path.isfile(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "labels" in data:
                self._labels = data["labels"]
        except Exception:
            pass

    def _labels_path(self) -> str:
        """Path to the project's labels.json file."""
        if not self._project_dir:
            return ""
        return os.path.join(self._project_dir, _LABELS_FILENAME)

    # ─── Saving ─────────────────────────────────────────────────────────────

    def _save_labels(self):
        """Write labels to the project's labels.json file."""
        path = self._labels_path()
        if not path:
            return

        # Strip out empty labels (no label and no notes)
        clean = {}
        for const_name, entry in self._labels.items():
            label = entry.get("label", "").strip()
            notes = entry.get("notes", "").strip()
            if label or notes:
                clean[const_name] = {"label": label, "notes": notes}

        data = {"version": 1, "labels": clean}
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            self._dirty = False
            self._save_btn.setEnabled(False)
            self._status_label.setText(
                f"Saved {len(clean)} label(s) to {_LABELS_FILENAME}")
            self.labels_changed.emit()
        except Exception as e:
            QMessageBox.warning(self, "Save Error",
                                f"Could not save labels:\n{e}")

    def has_unsaved_changes(self) -> bool:
        return self._dirty

    # ─── Filtering and display ──────────────────────────────────────────────

    def _apply_filter(self):
        """Rebuild the list based on current filter and search text."""
        self._const_list.blockSignals(True)
        self._const_list.clear()

        filter_idx = self._filter_combo.currentIndex()
        search = self._search_box.text().strip().lower()

        for const_name, const_type in self._all_constants:
            # Type filter
            if filter_idx == 1 and const_type != _TYPE_FLAG:
                continue
            if filter_idx == 2 and const_type != _TYPE_VAR:
                continue

            # Search filter — match against constant name and label
            label_text = self._labels.get(const_name, {}).get("label", "")
            if search:
                if (search not in const_name.lower()
                        and search not in label_text.lower()):
                    continue

            # Build display text — show user label, or auto-generated name
            auto_name = const_name
            for prefix in ('FLAG_', 'VAR_'):
                if const_name.startswith(prefix):
                    auto_name = const_name[len(prefix):].replace('_', ' ').title()
                    break
            if label_text:
                display = f"{const_name}  —  {label_text}"
            else:
                display = f"{const_name}  —  ({auto_name})"

            item = QListWidgetItem(display)
            item.setData(Qt.ItemDataRole.UserRole, const_name)
            item.setData(Qt.ItemDataRole.UserRole + 1, const_type)

            # Type badge color as left margin indicator
            color = _TYPE_COLORS.get(const_type, "#888")
            item.setForeground(QColor(color))

            self._const_list.addItem(item)

        self._const_list.blockSignals(False)
        self._update_counts()

    def _update_counts(self):
        """Update the counts label showing how many constants are visible."""
        total = len(self._all_constants)
        visible = self._const_list.count()
        labeled = sum(1 for v in self._labels.values()
                      if v.get("label", "").strip())
        self._counts_label.setText(
            f"{visible} shown / {total} total · {labeled} labeled")

    # ─── Detail panel ───────────────────────────────────────────────────────

    def _on_selection_changed(self, current, previous):
        """Update the detail panel when a different constant is selected."""
        if current is None:
            self._detail_name.setText("Select a constant from the list")
            self._detail_type_badge.setText("")
            self._label_edit.setEnabled(False)
            self._label_edit.clear()
            self._notes_edit.setEnabled(False)
            self._notes_edit.clear()
            return

        const_name = current.data(Qt.ItemDataRole.UserRole)
        const_type = current.data(Qt.ItemDataRole.UserRole + 1)

        self._detail_name.setText(const_name)

        # Type badge
        badge_color = _TYPE_COLORS.get(const_type, "#888")
        self._detail_type_badge.setText(_make_type_badge(const_type))
        self._detail_type_badge.setStyleSheet(
            f"font-weight: bold; padding: 2px 8px; border-radius: 4px; "
            f"background: {badge_color}; color: white;")

        # Load existing label data
        entry = self._labels.get(const_name, {})

        # Auto-generate a placeholder name from the constant
        # (strip prefix, replace underscores, title-case)
        auto_name = const_name
        for prefix in ('FLAG_', 'VAR_'):
            if const_name.startswith(prefix):
                auto_name = const_name[len(prefix):].replace('_', ' ').title()
                break

        self._label_edit.blockSignals(True)
        self._label_edit.setText(entry.get("label", ""))
        self._label_edit.setPlaceholderText(f"Auto: {auto_name}")
        self._label_edit.setEnabled(True)
        self._label_edit.blockSignals(False)

        self._notes_edit.blockSignals(True)
        self._notes_edit.setPlainText(entry.get("notes", ""))
        self._notes_edit.setEnabled(True)
        self._notes_edit.blockSignals(False)

    def _current_const_name(self) -> str:
        """Return the constant name for the currently selected list item."""
        item = self._const_list.currentItem()
        if item is None:
            return ""
        return item.data(Qt.ItemDataRole.UserRole) or ""

    def _on_label_edited(self, text: str):
        """User changed the label text for the selected constant."""
        name = self._current_const_name()
        if not name:
            return
        if name not in self._labels:
            self._labels[name] = {}
        self._labels[name]["label"] = text
        self._mark_dirty()

        # Update the list item display to show the new label
        item = self._const_list.currentItem()
        if item:
            if text.strip():
                item.setText(f"{name}  —  {text}")
            else:
                item.setText(name)

    def _on_notes_edited(self):
        """User changed the notes for the selected constant."""
        name = self._current_const_name()
        if not name:
            return
        if name not in self._labels:
            self._labels[name] = {}
        self._labels[name]["notes"] = self._notes_edit.toPlainText()
        self._mark_dirty()

    def _mark_dirty(self):
        if not self._dirty:
            self.modified.emit()
        self._dirty = True
        self._save_btn.setEnabled(True)

    # ─── Public API ─────────────────────────────────────────────────────────

    def select_constant(self, name: str):
        """Find and select a constant by name, scrolling to it."""
        for i in range(self._const_list.count()):
            item = self._const_list.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) == name:
                self._const_list.setCurrentItem(item)
                self._const_list.scrollToItem(
                    item, QAbstractItemView.ScrollHint.PositionAtCenter)
                return
        # Not visible — might be filtered out. Reset filters and try again.
        self._filter_combo.setCurrentIndex(0)
        self._search_box.clear()
        self._apply_filter()
        for i in range(self._const_list.count()):
            item = self._const_list.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) == name:
                self._const_list.setCurrentItem(item)
                self._const_list.scrollToItem(
                    item, QAbstractItemView.ScrollHint.PositionAtCenter)
                return

    def get_labels(self) -> dict[str, dict]:
        """Return a copy of the current label data."""
        return dict(self._labels)
