"""
Layouts & Tilesets tab — Layout Manager + Tileset Manager

Layout operations: rename, delete, clean orphans, apply tilesets.
Tileset operations: rename secondary tilesets.
"""

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QComboBox, QPushButton, QGroupBox, QLabel, QFormLayout,
    QInputDialog, QMessageBox, QMenu,
)


class LayoutsTab(QWidget):
    data_changed = pyqtSignal()

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self._mw = main_window
        self.project_info = None
        self._layout_renamer = None
        self._tileset_renamer = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # ── Layout Manager ───────────────────────────────────────────────────
        layout_group = QGroupBox("Layout Manager")
        layout_inner = QVBoxLayout(layout_group)

        form = QFormLayout()
        self.layout_combo = QComboBox()
        self.layout_combo.wheelEvent = lambda e: e.ignore()
        self.layout_combo.setPlaceholderText("Select a layout...")
        form.addRow("Layout:", self.layout_combo)
        layout_inner.addLayout(form)

        btn_row = QHBoxLayout()
        self.btn_rename_layout = QPushButton("Rename Layout")
        self.btn_delete_layout = QPushButton("Delete Layout")
        self.btn_clean_layouts = QPushButton("Clean Orphaned Layouts")
        self.btn_open_porymap = QPushButton("Open in Porymap")
        self.btn_open_porymap.setToolTip("Open the selected layout's map in Porymap")
        for btn in (self.btn_rename_layout, self.btn_delete_layout,
                    self.btn_clean_layouts, self.btn_open_porymap):
            btn.setEnabled(False)
            btn_row.addWidget(btn)
        layout_inner.addLayout(btn_row)

        # Tileset application
        tileset_form = QFormLayout()
        self.primary_combo = QComboBox()
        self.primary_combo.wheelEvent = lambda e: e.ignore()
        self.primary_combo.setPlaceholderText("Select primary tileset...")
        tileset_form.addRow("Primary Tileset:", self.primary_combo)
        self.secondary_combo = QComboBox()
        self.secondary_combo.wheelEvent = lambda e: e.ignore()
        self.secondary_combo.setPlaceholderText("Select secondary tileset...")
        tileset_form.addRow("Secondary Tileset:", self.secondary_combo)
        layout_inner.addLayout(tileset_form)

        self.btn_apply_tilesets = QPushButton("Apply Tilesets to Layout")
        self.btn_apply_tilesets.setEnabled(False)
        layout_inner.addWidget(self.btn_apply_tilesets)

        layout.addWidget(layout_group)

        # ── Tileset Manager ──────────────────────────────────────────────────
        tileset_group = QGroupBox("Tileset Manager")
        tileset_inner = QVBoxLayout(tileset_group)

        ts_form = QFormLayout()
        self.tileset_combo = QComboBox()
        self.tileset_combo.wheelEvent = lambda e: e.ignore()
        self.tileset_combo.setPlaceholderText("Select a secondary tileset...")
        ts_form.addRow("Secondary Tileset:", self.tileset_combo)
        tileset_inner.addLayout(ts_form)

        self.btn_rename_tileset = QPushButton("Rename Tileset")
        self.btn_rename_tileset.setEnabled(False)
        tileset_inner.addWidget(self.btn_rename_tileset)

        layout.addWidget(tileset_group)
        layout.addStretch()

        # ── Button connections ───────────────────────────────────────────────
        self.btn_rename_layout.clicked.connect(self._on_rename_layout)
        self.btn_delete_layout.clicked.connect(self._on_delete_layout)
        self.btn_clean_layouts.clicked.connect(self._on_clean_layouts)
        self.btn_apply_tilesets.clicked.connect(self._on_apply_tilesets)
        self.btn_rename_tileset.clicked.connect(self._on_rename_tileset)
        self.btn_open_porymap.clicked.connect(self._on_open_in_porymap)

    # ─────────────────────────────────────────────────────────────────────────
    # Project loading
    # ─────────────────────────────────────────────────────────────────────────

    def load_project(self, project_info: dict):
        self.project_info = project_info
        project_dir = project_info.get("dir", "")

        from eventide.backend.layout_renamer import LayoutRenamer
        from eventide.backend.tileset_renamer import TilesetRenamer
        try:
            self._layout_renamer = LayoutRenamer(project_dir)
            self._tileset_renamer = TilesetRenamer(project_dir)
        except Exception as e:
            self._mw.log_message(f"Layouts tab: failed to load backends: {e}")
            return

        for btn in (self.btn_rename_layout, self.btn_delete_layout,
                    self.btn_clean_layouts, self.btn_apply_tilesets,
                    self.btn_rename_tileset, self.btn_open_porymap):
            btn.setEnabled(True)

        self._populate_combos()
        self._mw.log_message(f"Layouts tab: loaded {project_dir}")

    def _populate_combos(self):
        # Layout combo
        self.layout_combo.clear()
        if self._layout_renamer:
            for layout in self._layout_renamer.get_layouts():
                lid = layout.get('id', '')
                self.layout_combo.addItem(lid, layout)

        # Tileset combos — parse from tileset headers
        self.tileset_combo.clear()
        self.primary_combo.clear()
        self.secondary_combo.clear()
        if self._tileset_renamer:
            try:
                tilesets = self._tileset_renamer.parse_tilesets()
                for label, folder in tilesets:
                    display = f"{label}  ({folder})"
                    self.tileset_combo.addItem(display, (label, folder))
                    self.secondary_combo.addItem(display, (label, folder))
            except Exception as e:
                self._mw.log_message(f"Layouts tab: could not parse tilesets: {e}")

        # Primary tilesets — look for primary tileset entries in layouts.json
        if self._layout_renamer:
            primaries = set()
            for layout in self._layout_renamer.get_layouts():
                pt = layout.get('primary_tileset', '')
                if pt:
                    primaries.add(pt)
            for pt in sorted(primaries):
                self.primary_combo.addItem(pt, pt)

    def _selected_layout(self):
        idx = self.layout_combo.currentIndex()
        if idx < 0:
            return None
        return self.layout_combo.itemData(idx)

    def _selected_tileset(self):
        idx = self.tileset_combo.currentIndex()
        if idx < 0:
            return None
        return self.tileset_combo.itemData(idx)

    # ─────────────────────────────────────────────────────────────────────────
    # Layout Manager actions
    # ─────────────────────────────────────────────────────────────────────────

    def _on_rename_layout(self):
        layout = self._selected_layout()
        if not layout:
            QMessageBox.information(self, "Rename Layout", "Select a layout first.")
            return
        old_id = layout['id']
        new_id, ok = QInputDialog.getText(
            self, "Rename Layout", f"New ID for '{old_id}':", text=old_id)
        if not ok or not new_id.strip() or new_id == old_id:
            return
        try:
            self._layout_renamer.rename_layout(old_id, new_id.strip())
            self._mw.log_message(f"Renamed layout {old_id} -> {new_id.strip()}")
            self._populate_combos()
            self.data_changed.emit()
        except Exception as e:
            QMessageBox.critical(self, "Rename Layout", str(e))

    def _on_delete_layout(self):
        layout = self._selected_layout()
        if not layout:
            QMessageBox.information(self, "Delete Layout", "Select a layout first.")
            return
        lid = layout['id']
        refs = self._layout_renamer.maps_using_layout(lid)
        if refs:
            QMessageBox.warning(
                self, "Delete Layout",
                f"Layout '{lid}' is used by {len(refs)} map(s). Remove those references first.")
            return
        reply = QMessageBox.question(
            self, "Delete Layout",
            f"Delete layout '{lid}'? This removes files and updates all references.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            self._layout_renamer.delete_layout(lid)
            self._mw.log_message(f"Deleted layout {lid}")
            self._populate_combos()
            self.data_changed.emit()
        except Exception as e:
            QMessageBox.critical(self, "Delete Layout", str(e))

    def _on_clean_layouts(self):
        reply = QMessageBox.question(
            self, "Clean Orphaned Layouts",
            "Remove layouts whose folders are missing and have no map references?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            count = self._layout_renamer.clean_orphaned_layouts()
            self._mw.log_message(f"Cleaned {count} orphaned layout(s)")
            self._populate_combos()
            self.data_changed.emit()
            QMessageBox.information(self, "Clean Orphaned Layouts",
                                   f"Removed {count} orphaned layout(s).")
        except Exception as e:
            QMessageBox.critical(self, "Clean Orphaned Layouts", str(e))

    def _on_apply_tilesets(self):
        layout = self._selected_layout()
        if not layout:
            QMessageBox.information(self, "Apply Tilesets", "Select a layout first.")
            return
        lid = layout['id']
        primary = self.primary_combo.currentData()
        secondary_data = self.secondary_combo.currentData()
        secondary = f"gTileset_{secondary_data[0]}" if secondary_data else None
        if not primary and not secondary:
            QMessageBox.information(self, "Apply Tilesets", "Select at least one tileset to apply.")
            return
        try:
            self._layout_renamer.rename_layout(
                lid, lid,
                primary_tileset=primary,
                secondary_tileset=secondary)
            self._mw.log_message(f"Applied tilesets to {lid}")
            self._populate_combos()
            self.data_changed.emit()
        except Exception as e:
            QMessageBox.critical(self, "Apply Tilesets", str(e))

    # ─────────────────────────────────────────────────────────────────────────
    # Tileset Manager actions
    # ─────────────────────────────────────────────────────────────────────────

    def _on_rename_tileset(self):
        data = self._selected_tileset()
        if not data:
            QMessageBox.information(self, "Rename Tileset", "Select a tileset first.")
            return
        old_label, old_folder = data
        new_label, ok = QInputDialog.getText(
            self, "Rename Tileset",
            f"New label for '{old_label}' (folder: {old_folder}):", text=old_label)
        if not ok or not new_label.strip() or new_label == old_label:
            return
        new_label = new_label.strip()
        new_folder, ok2 = QInputDialog.getText(
            self, "Rename Tileset",
            f"New folder name (currently '{old_folder}'):", text=old_folder)
        if not ok2 or not new_folder.strip():
            new_folder = old_folder
        else:
            new_folder = new_folder.strip()
        try:
            self._tileset_renamer.rename_tileset(old_label, old_folder, new_label, new_folder)
            self._mw.log_message(f"Renamed tileset {old_label} -> {new_label}")
            self._populate_combos()
            self.data_changed.emit()
        except Exception as e:
            QMessageBox.critical(self, "Rename Tileset", str(e))

    # ─────────────────────────────────────────────────────────────────────────
    # Porymap integration
    # ─────────────────────────────────────────────────────────────────────────

    def _on_open_in_porymap(self):
        """Launch Porymap. Since layouts don't map 1:1 to maps, just opens the project."""
        if not self.project_info:
            return
        try:
            from porymap_bridge.porymap_launcher import launch_porymap, is_porymap_installed
            if not is_porymap_installed():
                QMessageBox.information(
                    self, "Open in Porymap",
                    "Porymap is not installed. Use Tools > Install Porymap first.")
                return
            project_dir = self.project_info.get("dir", "")
            if project_dir:
                # Find the first map using this layout to give Porymap a starting point
                map_name = ""
                layout = self._selected_layout()
                if layout and self._layout_renamer:
                    refs = self._layout_renamer.maps_using_layout(layout['id'])
                    if refs:
                        map_name = refs[0]
                launch_porymap(project_dir, map_name)
        except Exception as e:
            QMessageBox.warning(self, "Open in Porymap", f"Could not launch Porymap: {e}")
