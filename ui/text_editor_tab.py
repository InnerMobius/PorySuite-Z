"""
ui/text_editor_tab.py
Text Editor tab — replaces the old UI Settings tab with a full-featured
project-wide text browser, search & replace, and editor.

Tree-based navigation with collapsible categories, search bar at the top,
GameTextEdit in the right panel with context-appropriate character limits.
"""
from __future__ import annotations

import json
import os
from functools import partial
from typing import Optional

from PyQt6.QtCore import pyqtSignal, Qt, QEvent, QTimer
from PyQt6.QtGui import QColor, QFont, QIcon
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu,
    QInputDialog, QMessageBox, QPlainTextEdit, QPushButton,
    QScrollArea, QSizePolicy, QSplitter, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from core.text_index import TextEntry, TextIndex
from ui.game_text_edit import GameTextEdit, inc_to_display, display_to_inc


# ── Stylesheets ──────────────────────────────────────────────────────────────

_TREE_SS = """
QTreeWidget {
    background-color: #1a1a1a;
    color: #d0d0d0;
    border: none;
    font-size: 12px;
    outline: none;
}
QTreeWidget::item {
    padding: 3px 4px;
    border: none;
}
QTreeWidget::item:selected {
    background-color: #1565c0;
    color: #ffffff;
}
QTreeWidget::item:hover:!selected {
    background-color: #2a2a2a;
}
QTreeWidget::branch {
    background: transparent;
}
"""

_SEARCH_SS = """
QLineEdit {
    background-color: #1e1e1e;
    border: 1px solid #3a3a3a;
    border-radius: 4px;
    padding: 5px 8px;
    color: #e0e0e0;
    font-size: 12px;
    selection-background-color: #1565c0;
}
QLineEdit:focus { border: 1px solid #1976d2; }
"""

_PANEL_SS = """
QLabel { color: #cccccc; }
"""

_HEADER_SS = """
    background-color: #252525;
    border: 1px solid #383838;
    border-radius: 6px;
    padding: 8px 10px;
"""

_NOTE_SS = "color: #888888; font-size: 10px; font-style: italic;"
_DIRTY_DOT = "\u2022"  # bullet character for modified indicator
_BOOKMARKS_FILE = "porysuite_text_bookmarks.json"


# ── Category definitions for the tree ────────────────────────────────────────

# Each tuple: (category_key, display_name, description, icon_hint)
# The tree builds top-level items from these; subcategories come from the index.
_CATEGORY_ORDER: list[tuple[str, str, str]] = [
    ("game_ui",         "Game UI & Menus",
     "Start menu labels, PC interface text, bag UI, battle UI, gender prompts"),
    ("new_game",        "New Game Intro",
     "Professor speech, intro pages, player/rival name pools"),
    ("location_names",  "Location Names",
     "Region map section names displayed on the Town Map"),
    ("map_dialogue",    "Map Dialogue",
     "NPC dialogue, signs, and text from every map in your project"),
    ("common_scripts",  "Common Scripts",
     "Shared script text used across multiple maps"),
    ("battle_messages", "Battle Messages",
     "In-battle text: attack announcements, status messages, results"),
    ("teachy_tv",       "Teachy TV",
     "Tutorial text shown on the Teachy TV channel"),
    ("fame_checker",    "Fame Checker",
     "NPC fame/reputation dialogue entries"),
    ("quest_log",       "Quest Log",
     "Quest log summary strings"),
    ("trainer_class",   "Trainer Class Names",
     "Display names for trainer classes (Youngster, Lass, etc.)"),
    ("nature_names",    "Nature Names",
     "Pokémon nature display names"),
]


# ── TextEditorTab ────────────────────────────────────────────────────────────

class TextEditorTab(QWidget):
    """
    Full-featured text editor tab.  Drop-in replacement for UITabWidget.

    Public API (same as UITabWidget):
      - modified   signal
      - load(project_dir)
      - has_changes() -> bool
      - save()
    """

    modified = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._project_dir: str = ""
        self._index = TextIndex()
        self._current_entry: TextEntry | None = None
        self._search_results: list[TextEntry] = []
        # Saved searches: group_name → list of label strings (persisted)
        self._saved_search_labels: dict[str, list[str]] = {}
        self._search_timer = QTimer()
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(300)
        self._search_timer.timeout.connect(self._do_search)
        self._build_ui()

    # ── UI construction ──────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Search bar at the very top ───────────────────────────────────────
        search_bar = QWidget()
        search_bar.setStyleSheet("background-color: #222222;")
        sb_layout = QHBoxLayout(search_bar)
        sb_layout.setContentsMargins(8, 6, 8, 6)
        sb_layout.setSpacing(6)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search all game text...")
        self._search_input.setStyleSheet(_SEARCH_SS)
        self._search_input.setClearButtonEnabled(True)
        self._search_input.textChanged.connect(self._on_search_changed)
        sb_layout.addWidget(self._search_input, 1)

        # Search options
        self._case_cb = QCheckBox("Match case")
        self._case_cb.setStyleSheet("color: #aaa; font-size: 11px;")
        self._case_cb.toggled.connect(self._on_search_changed)
        sb_layout.addWidget(self._case_cb)

        self._whole_word_cb = QCheckBox("Whole word")
        self._whole_word_cb.setStyleSheet("color: #aaa; font-size: 11px;")
        self._whole_word_cb.toggled.connect(self._on_search_changed)
        sb_layout.addWidget(self._whole_word_cb)

        self._regex_cb = QCheckBox("Regex")
        self._regex_cb.setStyleSheet("color: #aaa; font-size: 11px;")
        self._regex_cb.toggled.connect(self._on_search_changed)
        sb_layout.addWidget(self._regex_cb)

        # Result count
        self._result_count = QLabel("")
        self._result_count.setStyleSheet("color: #888; font-size: 11px;")
        sb_layout.addWidget(self._result_count)

        # Save search button
        self._save_search_btn = QPushButton("Save Search")
        self._save_search_btn.setToolTip(
            "Save this search as a collapsible section in the tree"
        )
        self._save_search_btn.setEnabled(False)
        self._save_search_btn.setMaximumWidth(100)
        self._save_search_btn.clicked.connect(self._save_current_search)
        sb_layout.addWidget(self._save_search_btn)

        root.addWidget(search_bar)

        # ── Splitter: tree left, editor right ────────────────────────────────
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(self._splitter, 1)

        # Left panel: tree
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)

        # Description at top of tree
        tree_desc = QLabel(
            "Browse and edit all game text. Click a category to expand, "
            "select an entry to edit it. Yellow dot = modified."
        )
        tree_desc.setWordWrap(True)
        tree_desc.setStyleSheet(_NOTE_SS + " padding: 6px 8px;")
        left_layout.addWidget(tree_desc)

        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setStyleSheet(_TREE_SS)
        self._tree.setIndentation(16)
        self._tree.setAnimated(True)
        self._tree.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self._tree.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self._tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        self._tree.currentItemChanged.connect(self._on_tree_selection)
        left_layout.addWidget(self._tree, 1)

        self._splitter.addWidget(left)

        # Right panel: editor
        right = QWidget()
        right.setStyleSheet(_PANEL_SS)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 8, 8, 8)
        right_layout.setSpacing(6)

        # Context header
        self._ctx_header = QWidget()
        self._ctx_header.setStyleSheet(_HEADER_SS)
        ctx_layout = QVBoxLayout(self._ctx_header)
        ctx_layout.setContentsMargins(0, 0, 0, 0)
        ctx_layout.setSpacing(2)

        self._ctx_label = QLabel("Select an entry to edit")
        self._ctx_label.setStyleSheet(
            "font-size: 13px; font-weight: bold; color: #e0e0e0;"
        )
        ctx_layout.addWidget(self._ctx_label)

        self._ctx_file = QLabel("")
        self._ctx_file.setStyleSheet("font-size: 10px; color: #888;")
        ctx_layout.addWidget(self._ctx_file)

        self._ctx_refs = QLabel("")
        self._ctx_refs.setStyleSheet("font-size: 10px; color: #7aa3cc;")
        self._ctx_refs.setWordWrap(True)
        self._ctx_refs.setVisible(False)
        ctx_layout.addWidget(self._ctx_refs)

        right_layout.addWidget(self._ctx_header)

        # The text editor
        self._editor = GameTextEdit(36, 20)
        self._editor.editor.setEnabled(False)
        self._editor.editor.setPlaceholderText(
            "Select a text entry from the tree to begin editing."
        )
        self._editor.connectChanged(self._on_editor_changed)
        right_layout.addWidget(self._editor, 1)

        # Button row under editor
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        self._eventide_btn = QPushButton("Open in EVENTide")
        self._eventide_btn.setToolTip(
            "Open the script that references this text in EVENTide"
        )
        self._eventide_btn.setVisible(False)
        self._eventide_btn.clicked.connect(self._open_in_eventide)
        btn_row.addWidget(self._eventide_btn)

        self._owned_label = QLabel("")
        self._owned_label.setStyleSheet(
            "color: #ffb74d; font-size: 11px; font-style: italic;"
        )
        self._owned_label.setVisible(False)
        btn_row.addWidget(self._owned_label)

        btn_row.addStretch(1)

        self._revert_btn = QPushButton("Revert")
        self._revert_btn.setToolTip("Revert this entry to last saved value")
        self._revert_btn.setEnabled(False)
        self._revert_btn.clicked.connect(self._revert_entry)
        btn_row.addWidget(self._revert_btn)

        right_layout.addLayout(btn_row)

        # Replace bar (hidden by default)
        self._replace_bar = QWidget()
        self._replace_bar.setVisible(False)
        rep_layout = QHBoxLayout(self._replace_bar)
        rep_layout.setContentsMargins(0, 4, 0, 0)
        rep_layout.setSpacing(6)

        rep_layout.addWidget(QLabel("Replace with:"))
        self._replace_input = QLineEdit()
        self._replace_input.setStyleSheet(_SEARCH_SS)
        self._replace_input.setPlaceholderText("Replacement text...")
        rep_layout.addWidget(self._replace_input, 1)

        self._replace_sel_btn = QPushButton("Replace Selected")
        self._replace_sel_btn.clicked.connect(self._replace_selected)
        rep_layout.addWidget(self._replace_sel_btn)

        self._replace_all_btn = QPushButton("Replace All in Results")
        self._replace_all_btn.clicked.connect(self._replace_all)
        rep_layout.addWidget(self._replace_all_btn)

        right_layout.addWidget(self._replace_bar)

        # Show/hide replace bar toggle (in search bar area)
        self._replace_toggle = QPushButton("Replace...")
        self._replace_toggle.setMaximumWidth(80)
        self._replace_toggle.setCheckable(True)
        self._replace_toggle.toggled.connect(self._replace_bar.setVisible)
        sb_layout.addWidget(self._replace_toggle)

        self._splitter.addWidget(right)

        # Set initial splitter sizes (30% tree, 70% editor)
        self._splitter.setSizes([300, 700])

    # ── Tree building ────────────────────────────────────────────────────────

    def _populate_tree(self) -> None:
        """Rebuild the tree from the current index."""
        self._tree.clear()
        cats = self._index.categories()

        # Saved searches section (if any)
        if self._saved_search_labels:
            saved_root = QTreeWidgetItem(self._tree, ["Saved Searches"])
            saved_root.setFont(0, self._bold_font())
            saved_root.setForeground(0, QColor("#64b5f6"))
            saved_root.setToolTip(0, "Your bookmarked search results (persisted across sessions)")
            saved_root.setFlags(
                saved_root.flags() & ~Qt.ItemFlag.ItemIsSelectable
            )
            saved_root.setData(0, Qt.ItemDataRole.UserRole + 1, "__saved_root__")
            for group_name, labels in self._saved_search_labels.items():
                found = 0
                search_node = QTreeWidgetItem(saved_root, [group_name])
                search_node.setFont(0, self._italic_font())
                search_node.setForeground(0, QColor("#90caf9"))
                search_node.setFlags(
                    search_node.flags() & ~Qt.ItemFlag.ItemIsSelectable
                )
                search_node.setData(
                    0, Qt.ItemDataRole.UserRole + 1,
                    f"__saved_group__:{group_name}",
                )
                for lbl in labels:
                    entry = self._index.get(lbl)
                    if entry:
                        child = self._add_entry_item(search_node, entry)
                        child.setData(
                            0, Qt.ItemDataRole.UserRole + 1,
                            f"__saved_entry__:{group_name}",
                        )
                        found += 1
                    else:
                        # Stale bookmark — label no longer in project
                        stale = QTreeWidgetItem(search_node, [
                            f"{lbl}  (not found)"
                        ])
                        stale.setForeground(0, QColor("#666666"))
                        stale.setFont(0, self._italic_font())
                        stale.setData(0, Qt.ItemDataRole.UserRole, lbl)
                        stale.setData(
                            0, Qt.ItemDataRole.UserRole + 1,
                            f"__saved_entry__:{group_name}",
                        )
                search_node.setText(
                    0, f"{group_name} ({found}/{len(labels)})"
                )

        # Category sections
        for cat_key, display_name, description in _CATEGORY_ORDER:
            # Collect all subcategories for this top-level key
            matching = {}
            for full_key, subcats in cats.items():
                if full_key == cat_key or full_key.startswith(cat_key + "."):
                    for subcat_name, entries in subcats.items():
                        matching.setdefault(subcat_name, []).extend(entries)

            if not matching:
                continue

            total = sum(len(v) for v in matching.values())
            cat_item = QTreeWidgetItem(self._tree, [
                f"{display_name} ({total})"
            ])
            cat_item.setFont(0, self._bold_font())
            cat_item.setForeground(0, QColor("#cccccc"))
            cat_item.setFlags(
                cat_item.flags() & ~Qt.ItemFlag.ItemIsSelectable
            )

            # Description as italic child
            desc_item = QTreeWidgetItem(cat_item, [description])
            desc_item.setFont(0, self._italic_font())
            desc_item.setForeground(0, QColor("#777777"))
            desc_item.setFlags(
                desc_item.flags() & ~Qt.ItemFlag.ItemIsSelectable
            )

            # If multiple subcategories, add subcat groupings
            if len(matching) > 1:
                for subcat_name in sorted(matching.keys()):
                    entries = matching[subcat_name]
                    sub_item = QTreeWidgetItem(cat_item, [
                        f"{subcat_name} ({len(entries)})"
                    ])
                    sub_item.setFont(0, self._medium_font())
                    sub_item.setForeground(0, QColor("#aaaaaa"))
                    sub_item.setFlags(
                        sub_item.flags() & ~Qt.ItemFlag.ItemIsSelectable
                    )
                    for entry in entries:
                        self._add_entry_item(sub_item, entry)
            else:
                # Single subcategory — entries go directly under category
                for entries in matching.values():
                    for entry in entries:
                        self._add_entry_item(cat_item, entry)

    def _add_entry_item(
        self, parent: QTreeWidgetItem, entry: TextEntry
    ) -> QTreeWidgetItem:
        """Add a single text entry as a tree leaf."""
        # Truncate content preview
        preview = entry.content.replace("\\n", " ").replace("\\p", " ")
        preview = preview.replace("\\l", " ").rstrip("$").strip()
        if len(preview) > 50:
            preview = preview[:47] + "..."

        display = entry.display_label
        if entry.is_dirty:
            display = f"{_DIRTY_DOT} {display}"

        text = f"{display}  —  {preview}" if preview else display
        item = QTreeWidgetItem(parent, [text])
        item.setData(0, Qt.ItemDataRole.UserRole, entry.label)
        item.setToolTip(0, f"{entry.label}\n{entry.file_rel}:{entry.line_number}")

        if entry.is_dirty:
            item.setForeground(0, QColor("#ffeb3b"))  # yellow for dirty
        elif entry.owning_tab:
            item.setForeground(0, QColor("#777777"))  # dim for owned elsewhere

        return item

    def _update_tree_item_display(self, entry: TextEntry) -> None:
        """Update the display of a single tree item after edit."""
        # Find the item by label
        iterator = self._tree_iterator()
        for item in iterator:
            label = item.data(0, Qt.ItemDataRole.UserRole)
            if label == entry.label:
                preview = entry.content.replace("\\n", " ").replace("\\p", " ")
                preview = preview.replace("\\l", " ").rstrip("$").strip()
                if len(preview) > 50:
                    preview = preview[:47] + "..."

                display = entry.display_label
                if entry.is_dirty:
                    display = f"{_DIRTY_DOT} {display}"

                text = f"{display}  —  {preview}" if preview else display
                item.setText(0, text)
                item.setForeground(
                    0, QColor("#ffeb3b") if entry.is_dirty
                    else QColor("#d0d0d0")
                )
                break

    def _tree_iterator(self):
        """Yield all QTreeWidgetItems in the tree (depth-first)."""
        def _recurse(parent_item):
            for i in range(parent_item.childCount()):
                child = parent_item.child(i)
                yield child
                yield from _recurse(child)

        root = self._tree.invisibleRootItem()
        yield from _recurse(root)

    # ── Search ───────────────────────────────────────────────────────────────

    def _on_search_changed(self, *_args) -> None:
        """Debounce search input."""
        self._search_timer.start()

    def _do_search(self) -> None:
        """Execute search and show results in the tree."""
        query = self._search_input.text().strip()
        if not query:
            # Restore full tree
            self._search_results.clear()
            self._result_count.setText("")
            self._save_search_btn.setEnabled(False)
            self._populate_tree()
            return

        results = self._index.search(
            query,
            match_case=self._case_cb.isChecked(),
            whole_word=self._whole_word_cb.isChecked(),
            regex=self._regex_cb.isChecked(),
        )
        self._search_results = results

        count = len(results)
        self._result_count.setText(
            f"{count} result{'s' if count != 1 else ''}"
        )
        self._save_search_btn.setEnabled(count > 0)

        # Show search results in tree
        self._tree.clear()
        if not results:
            empty = QTreeWidgetItem(self._tree, ["No results found"])
            empty.setForeground(0, QColor("#888888"))
            empty.setFont(0, self._italic_font())
            empty.setFlags(empty.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            return

        # Group results by category
        grouped: dict[str, list[TextEntry]] = {}
        for entry in results:
            grouped.setdefault(entry.category, []).append(entry)

        for cat_key in sorted(grouped.keys()):
            entries = grouped[cat_key]
            # Find display name
            cat_display = cat_key
            for key, name, _desc in _CATEGORY_ORDER:
                if cat_key == key or cat_key.startswith(key + "."):
                    cat_display = name
                    break

            cat_item = QTreeWidgetItem(self._tree, [
                f"{cat_display} ({len(entries)})"
            ])
            cat_item.setFont(0, self._bold_font())
            cat_item.setFlags(
                cat_item.flags() & ~Qt.ItemFlag.ItemIsSelectable
            )

            for entry in entries:
                self._add_entry_item(cat_item, entry)

            cat_item.setExpanded(True)

    def _save_current_search(self) -> None:
        """Save current search results as a persistent bookmark group."""
        query = self._search_input.text().strip()
        if not query or not self._search_results:
            return

        default_name = f'"{query}"'
        name, ok = QInputDialog.getText(
            self, "Save Search", "Group name:", text=default_name,
        )
        if not ok or not name.strip():
            return
        name = name.strip()

        labels = [e.label for e in self._search_results]
        self._saved_search_labels[name] = labels
        self._persist_bookmarks()
        self._search_input.clear()

    # ── Bookmark persistence ─────────────────────────────────────────────────

    def _bookmarks_path(self) -> str:
        return os.path.join(self._project_dir, _BOOKMARKS_FILE)

    def _load_bookmarks(self) -> None:
        """Load saved searches from the project's bookmark file."""
        self._saved_search_labels.clear()
        path = self._bookmarks_path()
        if not os.path.isfile(path):
            return
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                for k, v in data.items():
                    if isinstance(v, list):
                        self._saved_search_labels[str(k)] = [
                            str(lbl) for lbl in v
                        ]
        except (json.JSONDecodeError, OSError):
            pass

    def _persist_bookmarks(self) -> None:
        """Write saved searches to the project's bookmark file."""
        if not self._project_dir:
            return
        path = self._bookmarks_path()
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(self._saved_search_labels, fh, indent=2,
                          ensure_ascii=False)
                fh.write("\n")
        except OSError:
            pass

    # ── Tree context menu ────────────────────────────────────────────────────

    def _on_tree_context_menu(self, pos) -> None:
        """Right-click context menu for saved search management."""
        item = self._tree.itemAt(pos)
        if item is None:
            return

        tag = item.data(0, Qt.ItemDataRole.UserRole + 1) or ""
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background: #2b2b2b; color: #ddd; }"
            "QMenu::item:selected { background: #1565c0; }"
        )

        if tag == "__saved_root__":
            # Right-click on "Saved Searches" header
            act = menu.addAction("Clear All Saved Searches")
            act.triggered.connect(self._clear_all_bookmarks)
        elif tag.startswith("__saved_group__:"):
            group_name = tag.split(":", 1)[1]
            act_rename = menu.addAction("Rename Group")
            act_rename.triggered.connect(
                lambda: self._rename_bookmark_group(group_name)
            )
            act_del = menu.addAction("Delete Group")
            act_del.triggered.connect(
                lambda: self._delete_bookmark_group(group_name)
            )
            act_clean = menu.addAction("Remove Stale Entries")
            act_clean.triggered.connect(
                lambda: self._clean_stale_bookmarks(group_name)
            )
        elif tag.startswith("__saved_entry__:"):
            group_name = tag.split(":", 1)[1]
            label = item.data(0, Qt.ItemDataRole.UserRole)
            if label:
                act = menu.addAction("Remove from Group")
                act.triggered.connect(
                    lambda: self._remove_bookmark_entry(group_name, label)
                )
        else:
            # Regular entry — offer "Add to Saved Searches" if search active
            label = item.data(0, Qt.ItemDataRole.UserRole)
            if label and self._saved_search_labels:
                sub = menu.addMenu("Add to Saved Searches")
                for gn in self._saved_search_labels:
                    act = sub.addAction(gn)
                    act.triggered.connect(
                        lambda checked, g=gn, l=label:
                            self._add_to_bookmark_group(g, l)
                    )
            if not menu.actions():
                return

        menu.exec(self._tree.mapToGlobal(pos))

    def _rename_bookmark_group(self, old_name: str) -> None:
        name, ok = QInputDialog.getText(
            self, "Rename Group", "New name:", text=old_name,
        )
        if not ok or not name.strip() or name.strip() == old_name:
            return
        new_name = name.strip()
        if old_name in self._saved_search_labels:
            self._saved_search_labels[new_name] = (
                self._saved_search_labels.pop(old_name)
            )
            self._persist_bookmarks()
            self._populate_tree()

    def _delete_bookmark_group(self, group_name: str) -> None:
        self._saved_search_labels.pop(group_name, None)
        self._persist_bookmarks()
        self._populate_tree()

    def _clear_all_bookmarks(self) -> None:
        if not self._saved_search_labels:
            return
        reply = QMessageBox.question(
            self, "Clear Saved Searches",
            "Remove all saved search groups?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._saved_search_labels.clear()
            self._persist_bookmarks()
            self._populate_tree()

    def _remove_bookmark_entry(self, group_name: str, label: str) -> None:
        if group_name in self._saved_search_labels:
            try:
                self._saved_search_labels[group_name].remove(label)
            except ValueError:
                pass
            if not self._saved_search_labels[group_name]:
                del self._saved_search_labels[group_name]
            self._persist_bookmarks()
            self._populate_tree()

    def _add_to_bookmark_group(self, group_name: str, label: str) -> None:
        if group_name in self._saved_search_labels:
            if label not in self._saved_search_labels[group_name]:
                self._saved_search_labels[group_name].append(label)
                self._persist_bookmarks()
                self._populate_tree()

    def _clean_stale_bookmarks(self, group_name: str) -> None:
        """Remove entries from a group whose labels no longer exist."""
        if group_name not in self._saved_search_labels:
            return
        self._saved_search_labels[group_name] = [
            lbl for lbl in self._saved_search_labels[group_name]
            if self._index.get(lbl) is not None
        ]
        if not self._saved_search_labels[group_name]:
            del self._saved_search_labels[group_name]
        self._persist_bookmarks()
        self._populate_tree()

    # ── Tree selection → editor ──────────────────────────────────────────────

    def _on_tree_selection(
        self, current: QTreeWidgetItem | None,
        previous: QTreeWidgetItem | None,
    ) -> None:
        """Handle tree item selection — load entry into editor."""
        # Flush previous entry
        self._flush_editor()

        if current is None:
            self._clear_editor()
            return

        label = current.data(0, Qt.ItemDataRole.UserRole)
        if not label:
            self._clear_editor()
            return

        entry = self._index.get(label)
        if not entry:
            self._clear_editor()
            return

        self._load_entry(entry)

    def _load_entry(self, entry: TextEntry) -> None:
        """Load a TextEntry into the editor panel."""
        self._current_entry = entry

        # Update context header
        self._ctx_label.setText(entry.display_label)
        self._ctx_file.setText(
            f"{entry.file_rel}:{entry.line_number}  ·  {entry.label}"
        )

        # Script cross-references
        refs = self._index.xrefs.get(entry.label, [])
        if refs:
            ref_parts = []
            for script_file, script_label, msg_type in refs:
                ref_parts.append(f"{script_label} ({msg_type})")
            self._ctx_refs.setText(
                "Referenced by: " + ", ".join(ref_parts)
            )
            self._ctx_refs.setVisible(True)
        else:
            self._ctx_refs.setVisible(False)

        # Show "Open in EVENTide" for any map dialogue, common script,
        # or cross-referenced entry — the script file lives alongside
        # the text file in the same map folder
        has_script = bool(refs) or entry.category in (
            "map_dialogue", "common_scripts"
        )
        self._eventide_btn.setVisible(has_script)

        # Owned by another tab?
        if entry.owning_tab:
            self._owned_label.setText(
                f"This string is managed by the {entry.owning_tab.title()} tab. "
                "Edits here may conflict."
            )
            self._owned_label.setVisible(True)
        else:
            self._owned_label.setVisible(False)

        # Configure editor limits
        self._editor.set_limits(entry.char_limit, entry.max_lines)
        self._editor.editor.setEnabled(True)

        # Load content — set_inc_text handles its own blockSignals,
        # plain text branch needs explicit blocking
        if entry.is_multiline:
            self._editor.set_inc_text(entry.content)
        else:
            self._editor.editor.blockSignals(True)
            self._editor.editor.setPlainText(entry.content)
            self._editor.editor.blockSignals(False)

        self._revert_btn.setEnabled(entry.is_dirty)

    def _clear_editor(self) -> None:
        """Clear the editor panel."""
        self._current_entry = None
        self._ctx_label.setText("Select an entry to edit")
        self._ctx_file.setText("")
        self._ctx_refs.setVisible(False)
        self._eventide_btn.setVisible(False)
        self._owned_label.setVisible(False)
        self._editor.editor.blockSignals(True)
        self._editor.editor.clear()
        self._editor.editor.blockSignals(False)
        self._editor.editor.setEnabled(False)
        self._revert_btn.setEnabled(False)

    def _flush_editor(self) -> None:
        """Write current editor content back to the TextEntry."""
        entry = self._current_entry
        if entry is None:
            return

        if entry.is_multiline:
            new_val = self._editor.get_inc_text()
        else:
            new_val = self._editor.editor.toPlainText()

        if new_val != entry.content:
            entry.content = new_val
            self._update_tree_item_display(entry)
            if entry.is_dirty:
                self.modified.emit()

    def _on_editor_changed(self) -> None:
        """Editor content changed — update dirty state."""
        entry = self._current_entry
        if entry is None:
            return

        if entry.is_multiline:
            new_val = self._editor.get_inc_text()
        else:
            new_val = self._editor.editor.toPlainText()

        entry.content = new_val
        self._revert_btn.setEnabled(entry.is_dirty)

        # Emit modified only on first dirty
        if entry.is_dirty:
            self.modified.emit()
            self._update_tree_item_display(entry)

    def _revert_entry(self) -> None:
        """Revert current entry to its last-saved value."""
        entry = self._current_entry
        if entry is None:
            return
        entry.content = entry.original
        self._load_entry(entry)
        self._update_tree_item_display(entry)

    # ── Replace ──────────────────────────────────────────────────────────────

    def _replace_selected(self) -> None:
        """Replace search term in the currently selected entry."""
        entry = self._current_entry
        if entry is None:
            return
        query = self._search_input.text()
        replacement = self._replace_input.text()
        if not query:
            return

        if self._case_cb.isChecked():
            entry.content = entry.content.replace(query, replacement)
        else:
            # Case-insensitive replace
            import re
            entry.content = re.sub(
                re.escape(query), replacement, entry.content,
                flags=re.IGNORECASE,
            )

        self._load_entry(entry)
        self._update_tree_item_display(entry)
        if entry.is_dirty:
            self.modified.emit()

    def _replace_all(self) -> None:
        """Replace search term in ALL search results."""
        query = self._search_input.text()
        replacement = self._replace_input.text()
        if not query or not self._search_results:
            return

        import re
        count = 0
        for entry in self._search_results:
            old = entry.content
            if self._case_cb.isChecked():
                entry.content = entry.content.replace(query, replacement)
            else:
                entry.content = re.sub(
                    re.escape(query), replacement, entry.content,
                    flags=re.IGNORECASE,
                )
            if entry.content != old:
                count += 1

        # Refresh current entry display if it changed
        if self._current_entry and self._current_entry.is_dirty:
            self._load_entry(self._current_entry)

        if count > 0:
            self.modified.emit()
            self._result_count.setText(
                f"Replaced in {count} entr{'ies' if count != 1 else 'y'}"
            )
            # Re-run search to update tree displays
            self._do_search()

    # ── EVENTide integration ─────────────────────────────────────────────────

    def _open_in_eventide(self) -> None:
        """Open the related script in EVENTide.

        If we have a cross-reference (msgbox), open the exact script and
        label.  Otherwise for map dialogue, derive the scripts.inc path
        from the text.inc path (they're in the same map folder).  For
        common scripts the .inc file itself IS the script.
        """
        entry = self._current_entry
        if entry is None:
            return

        refs = self._index.xrefs.get(entry.label, [])
        if refs:
            script_file, script_label, _ = refs[0]
        elif entry.category == "map_dialogue":
            # text.inc → scripts.inc in same map folder
            script_file = entry.file_rel.replace("text.inc", "scripts.inc")
            script_label = ""
        elif entry.category == "common_scripts":
            # The .inc file itself is the script
            script_file = entry.file_rel
            script_label = entry.label
        else:
            return

        full_path = os.path.join(self._project_dir, script_file)
        if not os.path.isfile(full_path):
            QMessageBox.information(
                self, "No Script File",
                f"Could not find {script_file}",
            )
            return

        main = self.window()
        if hasattr(main, "open_in_eventide_signal"):
            main.open_in_eventide_signal.emit({
                "file": full_path,
                "label": script_label,
            })

    # ── Font helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _bold_font() -> QFont:
        f = QFont()
        f.setBold(True)
        f.setPointSize(10)
        return f

    @staticmethod
    def _italic_font() -> QFont:
        f = QFont()
        f.setItalic(True)
        f.setPointSize(9)
        return f

    @staticmethod
    def _medium_font() -> QFont:
        f = QFont()
        f.setPointSize(10)
        return f

    # ── Public API (matches UITabWidget) ─────────────────────────────────────

    def load(self, project_dir: str) -> None:
        """Load all text from the project. Clears dirty state."""
        self._project_dir = project_dir
        self._current_entry = None
        self._search_results.clear()
        self._search_input.clear()

        self._index.load(project_dir)
        self._load_bookmarks()
        self._populate_tree()
        self._clear_editor()

    def has_changes(self) -> bool:
        return self._index.has_changes()

    def save(self) -> None:
        """Write all dirty entries back to their source files."""
        # Flush current editor first
        self._flush_editor()
        self._index.save()
        # Refresh tree to clear dirty indicators
        self._populate_tree()
