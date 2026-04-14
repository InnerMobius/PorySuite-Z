"""
Maps tab — Map Manager + Warp Validator + Layouts & Tilesets

Provides a tree view of map sections/groups/maps with rename, move, delete,
and orphan cleanup. Warp validation lives at the bottom of the Maps sub-tab.
Layouts & Tilesets is a sibling sub-tab for layout/tileset management.
"""

import os
import json

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QTabWidget,
    QTreeWidget, QTreeWidgetItem, QLineEdit, QCheckBox,
    QPushButton, QGroupBox, QTextEdit, QLabel, QMenu,
    QInputDialog, QMessageBox,
)


class MapsTab(QWidget):
    # Emitted after any map/group/section mutation so sibling tabs can refresh
    data_changed = pyqtSignal()
    # Emitted when user double-clicks a map — carries the map folder name
    map_selected = pyqtSignal(str)

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self._mw = main_window
        self.project_info = None
        self._renamer = None
        self._warp_validator = None
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        # ── Sub-tab widget: Map Manager | Layouts & Tilesets ────────────────
        self._sub_tabs = QTabWidget()
        root.addWidget(self._sub_tabs)

        # ── Sub-tab 1: Map Manager ──────────────────────────────────────────
        maps_page = QWidget()
        layout = QVBoxLayout(maps_page)
        layout.setContentsMargins(8, 8, 8, 8)

        splitter = QSplitter(Qt.Orientation.Vertical)
        layout.addWidget(splitter)

        # ── Top: Map Manager ─────────────────────────────────────────────────
        map_group = QGroupBox("Map Manager")
        map_layout = QVBoxLayout(map_group)

        # Search / filter row
        filter_row = QHBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search maps...")
        self.search_box.textChanged.connect(self._filter_tree)
        filter_row.addWidget(self.search_box)
        self.show_unused_cb = QCheckBox("Show Unused")
        filter_row.addWidget(self.show_unused_cb)
        map_layout.addLayout(filter_row)

        # Tree view
        self.map_tree = QTreeWidget()
        self.map_tree.setHeaderLabels(["Name", "Section", "Layout"])
        self.map_tree.setAlternatingRowColors(True)
        self.map_tree.itemDoubleClicked.connect(self._on_map_double_clicked)
        self.map_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.map_tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        map_layout.addWidget(self.map_tree)

        # Action buttons
        btn_row1 = QHBoxLayout()
        self.btn_rename_map = QPushButton("Rename Map")
        self.btn_rename_group = QPushButton("Rename Group")
        self.btn_create_group = QPushButton("Create Group")
        self.btn_delete_group = QPushButton("Delete Group")
        for btn in (self.btn_rename_map, self.btn_rename_group,
                    self.btn_create_group, self.btn_delete_group):
            btn.setEnabled(False)
            btn_row1.addWidget(btn)
        map_layout.addLayout(btn_row1)

        btn_row2 = QHBoxLayout()
        self.btn_rename_section = QPushButton("Rename Section")
        self.btn_move_map = QPushButton("Move Map")
        self.btn_delete_map = QPushButton("Delete Map")
        self.btn_clean_maps = QPushButton("Clean Orphaned Data")
        for btn in (self.btn_rename_section, self.btn_move_map,
                    self.btn_delete_map, self.btn_clean_maps):
            btn.setEnabled(False)
            btn_row2.addWidget(btn)
        map_layout.addLayout(btn_row2)

        splitter.addWidget(map_group)

        # ── Bottom: Warp Validator ───────────────────────────────────────────
        warp_group = QGroupBox("Warp Validator")
        warp_layout = QVBoxLayout(warp_group)

        warp_btn_row = QHBoxLayout()
        self.btn_check_warps = QPushButton("Check Warps")
        self.btn_check_warps.setEnabled(False)
        self.btn_clean_warps = QPushButton("Clean Invalid Warps")
        self.btn_clean_warps.setEnabled(False)
        warp_btn_row.addWidget(self.btn_check_warps)
        warp_btn_row.addWidget(self.btn_clean_warps)
        warp_btn_row.addStretch()
        warp_layout.addLayout(warp_btn_row)

        self.warp_results = QTextEdit()
        self.warp_results.setReadOnly(True)
        self.warp_results.setMaximumHeight(120)
        self.warp_results.setPlaceholderText("Warp validation results will appear here...")
        warp_layout.addWidget(self.warp_results)

        splitter.addWidget(warp_group)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

        self._sub_tabs.addTab(maps_page, "Map Manager")

        # ── Sub-tab 2: Layouts & Tilesets ───────────────────────────────────
        from eventide.ui.layouts_tab import LayoutsTab
        self.layouts_tab = LayoutsTab(self._mw)
        self._sub_tabs.addTab(self.layouts_tab, "Layouts && Tilesets")

        # ── Button connections ───────────────────────────────────────────────
        self.btn_rename_map.clicked.connect(self._on_rename_map)
        self.btn_rename_group.clicked.connect(self._on_rename_group)
        self.btn_create_group.clicked.connect(self._on_create_group)
        self.btn_delete_group.clicked.connect(self._on_delete_group)
        self.btn_rename_section.clicked.connect(self._on_rename_section)
        self.btn_move_map.clicked.connect(self._on_move_map)
        self.btn_delete_map.clicked.connect(self._on_delete_map)
        self.btn_clean_maps.clicked.connect(self._on_clean_orphaned)
        self.btn_check_warps.clicked.connect(self._on_check_warps)
        self.btn_clean_warps.clicked.connect(self._on_clean_warps)

    # ─────────────────────────────────────────────────────────────────────────
    # Project loading
    # ─────────────────────────────────────────────────────────────────────────

    def load_project(self, project_info: dict):
        self.project_info = project_info
        project_dir = project_info.get("dir", "")

        from eventide.backend.map_renamer import MapRenamer
        from eventide.backend.warp_validator import WarpValidator
        try:
            self._renamer = MapRenamer(project_dir)
            self._warp_validator = WarpValidator(project_dir)
        except Exception as e:
            self._mw.log_message(f"Maps tab: failed to load backends: {e}")
            return

        for btn in (self.btn_rename_map, self.btn_rename_group,
                    self.btn_create_group, self.btn_delete_group,
                    self.btn_rename_section, self.btn_move_map,
                    self.btn_delete_map, self.btn_clean_maps,
                    self.btn_check_warps, self.btn_clean_warps):
            btn.setEnabled(True)

        self._populate_tree()
        self._mw.log_message(f"Maps tab: loaded {project_dir}")

        # Also load the layouts sub-tab
        self.layouts_tab.load_project(project_info)

    def _populate_tree(self):
        self.map_tree.clear()
        if not self._renamer:
            return
        groups = self._renamer.groups
        maps_dir = self._renamer.maps_dir
        for group_name in groups.get('group_order', []):
            group_item = QTreeWidgetItem(self.map_tree, [group_name, "", ""])
            group_item.setData(0, Qt.ItemDataRole.UserRole, {"type": "group", "name": group_name})
            for map_folder in groups.get(group_name, []):
                map_json_path = os.path.join(maps_dir, map_folder, 'map.json')
                section = ""
                layout_id = ""
                try:
                    with open(map_json_path) as f:
                        data = json.load(f)
                    section = data.get('region_map_section', '')
                    layout_id = data.get('layout', '')
                except Exception:
                    pass
                map_item = QTreeWidgetItem(group_item, [map_folder, section, layout_id])
                map_item.setData(0, Qt.ItemDataRole.UserRole, {
                    "type": "map", "folder": map_folder, "group": group_name
                })
        self.map_tree.expandAll()
        for col in range(3):
            self.map_tree.resizeColumnToContents(col)

    def _filter_tree(self, text: str):
        text = text.lower()
        for i in range(self.map_tree.topLevelItemCount()):
            group_item = self.map_tree.topLevelItem(i)
            any_visible = False
            for j in range(group_item.childCount()):
                child = group_item.child(j)
                visible = not text or text in child.text(0).lower() or text in child.text(1).lower()
                child.setHidden(not visible)
                if visible:
                    any_visible = True
            group_item.setHidden(not any_visible and bool(text))

    def _selected_item_data(self):
        items = self.map_tree.selectedItems()
        if not items:
            return None
        return items[0].data(0, Qt.ItemDataRole.UserRole)

    def _on_map_double_clicked(self, item, column):
        """Emit map_selected when a map item is double-clicked."""
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data and data.get("type") == "map":
            self.map_selected.emit(data["folder"])

    # ─────────────────────────────────────────────────────────────────────────
    # Map Manager actions
    # ─────────────────────────────────────────────────────────────────────────

    def _on_rename_map(self):
        data = self._selected_item_data()
        if not data or data.get("type") != "map":
            QMessageBox.information(self, "Rename Map", "Select a map in the tree first.")
            return
        old_folder = data["folder"]
        new_folder, ok = QInputDialog.getText(
            self, "Rename Map", f"New folder name for '{old_folder}':", text=old_folder)
        if not ok or not new_folder.strip() or new_folder == old_folder:
            return
        new_folder = new_folder.strip()
        new_id = f"MAP_{new_folder.upper()}"
        try:
            self._renamer.rename_map(
                data["group"], old_folder, new_folder=new_folder, new_id=new_id,
                callback=lambda msg: self._mw.log_message(msg))
            self._mw.log_message(f"Renamed map {old_folder} -> {new_folder}")
            self._populate_tree()
            self.data_changed.emit()
        except Exception as e:
            QMessageBox.critical(self, "Rename Map", str(e))

    def _on_rename_group(self):
        data = self._selected_item_data()
        if not data or data.get("type") != "group":
            QMessageBox.information(self, "Rename Group", "Select a group in the tree first.")
            return
        old_name = data["name"]
        new_name, ok = QInputDialog.getText(
            self, "Rename Group", f"New name for '{old_name}':", text=old_name)
        if not ok or not new_name.strip() or new_name == old_name:
            return
        try:
            self._renamer.rename_group(old_name, new_name.strip())
            self._mw.log_message(f"Renamed group {old_name} -> {new_name.strip()}")
            self._populate_tree()
            self.data_changed.emit()
        except Exception as e:
            QMessageBox.critical(self, "Rename Group", str(e))

    def _on_create_group(self):
        name, ok = QInputDialog.getText(self, "Create Group", "New group name:")
        if not ok or not name.strip():
            return
        try:
            self._renamer.create_group(name.strip())
            self._mw.log_message(f"Created group {name.strip()}")
            self._populate_tree()
            self.data_changed.emit()
        except Exception as e:
            QMessageBox.critical(self, "Create Group", str(e))

    def _on_delete_group(self):
        data = self._selected_item_data()
        if not data or data.get("type") != "group":
            QMessageBox.information(self, "Delete Group", "Select an empty group in the tree first.")
            return
        name = data["name"]
        reply = QMessageBox.question(
            self, "Delete Group", f"Delete empty group '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            self._renamer.delete_group(name)
            self._mw.log_message(f"Deleted group {name}")
            self._populate_tree()
            self.data_changed.emit()
        except Exception as e:
            QMessageBox.critical(self, "Delete Group", str(e))

    def _on_rename_section(self):
        data = self._selected_item_data()
        if not data or data.get("type") != "map":
            QMessageBox.information(self, "Rename Section", "Select a map in the tree first.")
            return
        folder = data["folder"]
        map_json = os.path.join(self._renamer.maps_dir, folder, 'map.json')
        try:
            with open(map_json) as f:
                mdata = json.load(f)
        except Exception as e:
            QMessageBox.critical(self, "Rename Section", f"Can't read map.json: {e}")
            return
        old_section = mdata.get('region_map_section', '')
        new_section, ok = QInputDialog.getText(
            self, "Rename Section",
            f"New region_map_section for '{folder}':", text=old_section)
        if not ok or not new_section.strip() or new_section == old_section:
            return
        mdata['region_map_section'] = new_section.strip()
        try:
            with open(map_json, 'w', newline='\n') as f:
                json.dump(mdata, f, indent=2)
                f.write('\n')
            self._mw.log_message(f"Changed section for {folder}: {old_section} -> {new_section.strip()}")
            self._populate_tree()
            self.data_changed.emit()
        except Exception as e:
            QMessageBox.critical(self, "Rename Section", str(e))

    def _on_move_map(self):
        data = self._selected_item_data()
        if not data or data.get("type") != "map":
            QMessageBox.information(self, "Move Map", "Select a map in the tree first.")
            return
        groups = self._renamer.groups.get('group_order', [])
        new_group, ok = QInputDialog.getItem(
            self, "Move Map",
            f"Move '{data['folder']}' to group:", groups, editable=False)
        if not ok or new_group == data["group"]:
            return
        try:
            self._renamer.move_map(data["group"], data["folder"], new_group)
            self._mw.log_message(f"Moved {data['folder']} from {data['group']} to {new_group}")
            self._populate_tree()
            self.data_changed.emit()
        except Exception as e:
            QMessageBox.critical(self, "Move Map", str(e))

    def _on_delete_map(self):
        data = self._selected_item_data()
        if not data or data.get("type") != "map":
            QMessageBox.information(self, "Delete Map", "Select a map in the tree first.")
            return
        folder = data["folder"]
        reply = QMessageBox.question(
            self, "Delete Map",
            f"Delete map '{folder}'? This removes map files and updates all references.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            self._renamer.delete_map(
                data["group"], folder,
                callback=lambda msg: self._mw.log_message(msg))
            self._mw.log_message(f"Deleted map {folder}")
            self._populate_tree()
            self.data_changed.emit()
        except Exception as e:
            QMessageBox.critical(self, "Delete Map", str(e))

    def _on_clean_orphaned(self):
        reply = QMessageBox.question(
            self, "Clean Orphaned Data",
            "Remove maps from map_groups.json whose folders are missing or corrupt?\n"
            "This will also regenerate headers.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            count = self._renamer.clean_orphaned_map_data(
                ensure_headers=True,
                callback=lambda msg: self._mw.log_message(msg))
            self._mw.log_message(f"Cleaned {count} orphaned map(s)")
            self._populate_tree()
            self.data_changed.emit()
            QMessageBox.information(self, "Clean Orphaned Data",
                                   f"Removed {count} orphaned map(s).")
        except Exception as e:
            QMessageBox.critical(self, "Clean Orphaned Data", str(e))

    # ─────────────────────────────────────────────────────────────────────────
    # Warp Validator actions
    # ─────────────────────────────────────────────────────────────────────────

    def _on_check_warps(self):
        if not self._warp_validator:
            return
        try:
            issues = self._warp_validator.find_invalid_warps()
        except Exception as e:
            self.warp_results.setPlainText(f"Error: {e}")
            return
        if not issues:
            self.warp_results.setPlainText("No invalid warps found.")
            self._mw.log_message("Warp check: no issues found")
            return
        lines = [f"Found {len(issues)} invalid warp(s):", ""]
        for issue in issues:
            rel = os.path.relpath(issue.map_path, self._warp_validator.root_dir)
            lines.append(f"  {rel}  warp #{issue.index}  -> {issue.dest_map}")
        self.warp_results.setPlainText("\n".join(lines))
        self._mw.log_message(f"Warp check: {len(issues)} invalid warp(s) found")

    def _on_tree_context_menu(self, pos):
        """Right-click context menu on the map tree."""
        item = self.map_tree.itemAt(pos)
        if not item:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data or data.get("type") != "map":
            return

        menu = QMenu(self)
        act_porymap = menu.addAction("Open in Porymap")

        # Only enable if Porymap is installed
        try:
            from porymap_bridge.porymap_launcher import is_porymap_installed
            act_porymap.setEnabled(is_porymap_installed())
        except ImportError:
            act_porymap.setEnabled(False)

        action = menu.exec(self.map_tree.mapToGlobal(pos))
        if action == act_porymap:
            self._open_map_in_porymap(data["folder"])

    def _open_map_in_porymap(self, map_folder: str):
        """Launch Porymap focused on the given map."""
        if not self.project_info:
            return
        try:
            from porymap_bridge.porymap_launcher import launch_porymap
            project_dir = self.project_info.get("dir", "")
            if project_dir:
                launch_porymap(project_dir, map_folder)
        except Exception as e:
            QMessageBox.warning(self, "Open in Porymap", f"Could not launch Porymap: {e}")

    def _on_clean_warps(self):
        if not self._warp_validator:
            return
        reply = QMessageBox.question(
            self, "Clean Invalid Warps",
            "Remove all warp events that reference non-existent maps?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            count = self._warp_validator.clean_invalid_warps()
            self.warp_results.setPlainText(f"Removed {count} invalid warp(s).")
            self._mw.log_message(f"Cleaned {count} invalid warp(s)")
        except Exception as e:
            QMessageBox.critical(self, "Clean Invalid Warps", str(e))
