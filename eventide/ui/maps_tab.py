"""
Maps tab — Map Manager + Warp Validator + Layouts & Tilesets

Provides a tree view of map groups/maps with rename, move, delete, and orphan
cleanup. Warp validation lives below the tree. Layouts & Tilesets is a sibling
sub-tab.

Tree columns
------------
  Name      — map folder name (the code identifier; what every operation acts on)
  In-Game   — area name from region_map_sections.json, ALL CAPS as stored on disk
  Section   — raw MAPSEC constant from map.json (what "Rename Section" changes)
  Layout    — raw layout ID from map.json (read-only reference)

The In-Game column lets the user scan visually for the human-readable area
("S.S. ANNE", "VIRIDIAN FOREST") instead of having to recognise every folder
name. It is read-only — it only reflects what the Section maps to in the
region map JSON. To change it, edit the region map JSON or change the
Section assignment with "Rename Section".
"""

import os
import json

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
import re

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QTabWidget,
    QTreeWidget, QTreeWidgetItem, QLineEdit,
    QPushButton, QGroupBox, QTextEdit, QLabel, QMenu, QFormLayout,
    QDialog, QDialogButtonBox, QInputDialog, QMessageBox,
    QRadioButton, QButtonGroup, QComboBox,
)

_AMBER = QColor("#3d2e00")
_TRANSPARENT = QColor(0, 0, 0, 0)

_WARN_IMMEDIATE = (
    "\n\n⚠  WRITES TO DISK IMMEDIATELY when you confirm. This change "
    "cannot be undone from within the app, and the F5 / Save buttons do "
    "NOT control it.\nMake a backup or commit to Git before proceeding."
)

_WARN_STAGED = (
    "\n\nThis change is staged in memory only — nothing is written to "
    "disk until you press Save (or Ctrl+S). Press F5 (refresh) at any "
    "time to discard staged changes and reload from disk."
)

_TREE_COLS = 4   # Name | In-Game | Section | Layout
_COL_NAME, _COL_INGAME, _COL_SECTION, _COL_LAYOUT = 0, 1, 2, 3

# In-game area name max length — matches u8 mapName[19] in pokefirered's
# region_map.c (18 chars + null terminator). Existing data tops at 16.
INGAME_NAME_LENGTH = 18

# MAPSEC constant identifier — soft cap to prevent absurd input. No hard
# engine limit (it's a C preprocessor token), but anything past ~48 chars
# is unwieldy in generated headers.
MAPSEC_CONST_LENGTH = 48


def _attach_char_counter(line_edit, counter_lbl, max_chars: int):
    """Wire a QLineEdit + QLabel counter, matching items_tab_widget style.

    Grey under 85%, amber 85-99%, red at the cap. Uses Courier font so
    the counter doesn't shift width as it counts.
    """
    line_edit.setMaxLength(max_chars)
    base_ss = "font-size: 10px; font-family: 'Courier New';"

    def _refresh(text=None):
        used = len(line_edit.text())
        counter_lbl.setText(f"{used}/{max_chars}")
        if used >= max_chars:
            color = "#cc3333"
        elif used >= int(max_chars * 0.85):
            color = "#ffb74d"
        else:
            color = "#888888"
        counter_lbl.setStyleSheet(f"color: {color}; {base_ss}")

    counter_lbl.setStyleSheet(f"color: #888888; {base_ss}")
    line_edit.textChanged.connect(_refresh)
    _refresh()


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
        self._dirty_items: set[str] = set()
        # MAPSEC constant → in-game area name, stored exactly as found in
        # region_map_sections.json (ALL CAPS — no transformation applied).
        self._mapsec_names: dict = {}

        # ── In-memory staging (deferred-save) ───────────────────────────────
        # Lightweight JSON edits live here until the user hits Save. F5
        # discards them. Heavyweight filesystem ops (folder rename, delete,
        # clean) still bypass this and write immediately, with explicit
        # warnings on each dialog.
        #
        # _pending_sections[map_folder] = "MAPSEC_FOO"
        #     map.json region_map_section reassignment that hasn't been
        #     flushed yet. Tree overlays this on top of disk values so the
        #     user sees what Save will produce.
        #
        # _pending_rms[MAPSEC_ID] = {"name": "...", "new": bool}
        #     region_map_sections.json edits. "new" = True means append a
        #     fresh entry on save; False means update an existing entry's
        #     name field. Either way, _mapsec_names is updated immediately
        #     so the tree shows the future state.
        self._pending_sections: dict[str, str] = {}
        self._pending_rms: dict[str, dict] = {}
        self._build_ui()

    # ─────────────────────────────────────────────────────────────────────────
    # UI construction
    # ─────────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        self._sub_tabs = QTabWidget()
        root.addWidget(self._sub_tabs)

        # ── Sub-tab 1: Map Manager ──────────────────────────────────────────
        maps_page = QWidget()
        layout = QVBoxLayout(maps_page)
        layout.setContentsMargins(8, 8, 8, 8)

        splitter = QSplitter(Qt.Orientation.Vertical)
        layout.addWidget(splitter)

        # Top: map tree + controls
        map_group = QGroupBox("Map Manager")
        map_layout = QVBoxLayout(map_group)

        # Header summary — orients the user to what each column means and
        # what the save/dirty model is.
        banner = QLabel(
            "<small>"
            "<b>Columns:</b> "
            "<b>Name</b> = folder/code identifier (unique). "
            "<b>In-Game</b> = human-readable area shown to the player "
            "(driven by Section). "
            "<b>Section</b> = MAPSEC constant. "
            "<b>Layout</b> = tilemap this map uses."
            "<br><br>"
            "<b>Save model:</b> "
            "<span style='color:#9ad'>Set In-Game Name</span> and "
            "<span style='color:#9ad'>Rename Section</span> are "
            "<b>staged</b> in memory — press <b>Save</b> (or Ctrl+S) to "
            "write, <b>F5</b> to discard. "
            "<span style='color:#f99'>Rename Map, Move Map, Delete Map, "
            "Create/Rename/Delete Group, and Clean Orphaned Data</span> "
            "<b>write to disk immediately</b> when you confirm — they "
            "are not undoable from within the app. Back up or commit to "
            "Git before using them."
            "</small>"
        )
        banner.setWordWrap(True)
        banner.setStyleSheet("padding: 4px; color: #ccc;")
        map_layout.addWidget(banner)

        # Search / filter
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText(
            "Search by folder name, section, layout, or in-game area...")
        self.search_box.textChanged.connect(self._filter_tree)
        map_layout.addWidget(self.search_box)

        # Tree
        self.map_tree = QTreeWidget()
        self.map_tree.setHeaderLabels(["Name", "In-Game", "Section", "Layout"])
        self.map_tree.setAlternatingRowColors(True)
        self.map_tree.itemDoubleClicked.connect(self._on_map_double_clicked)
        self.map_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.map_tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        map_layout.addWidget(self.map_tree)

        # Status line: project totals
        self.status_lbl = QLabel("No project loaded")
        self.status_lbl.setStyleSheet("color: #888; padding: 2px;")
        map_layout.addWidget(self.status_lbl)

        # ── Button groups (Maps / Groups / Maintenance) ─────────────────────
        # Each button has a tooltip explaining what it touches in plain English.

        groups_row = QHBoxLayout()

        # Maps group
        maps_box = QGroupBox("Maps")
        maps_box_l = QHBoxLayout(maps_box)
        self.btn_rename_map = QPushButton("Rename Map")
        self.btn_rename_map.setToolTip(
            "IMMEDIATE WRITE.\n\n"
            "Renames the map folder on disk, renames the matching layout "
            "folder, updates map.json, and rewrites every reference to "
            "the old folder name across the entire project source. "
            "Regenerates header constants via mapjson.\n\n"
            "Not staged — confirms write directly to disk. Cannot be "
            "undone from within the app.")
        self.btn_rename_ingame = QPushButton("Set In-Game Name")
        self.btn_rename_ingame.setToolTip(
            "STAGED — write on Save.\n\n"
            "Set the human-readable area name shown in-game. Three "
            "options inside:\n"
            "  • Assign an existing area (swap this one map only).\n"
            "  • Create a new area just for this map (split it off).\n"
            "  • Rename the current area globally (affects all maps "
            "sharing the MAPSEC).\n\n"
            "Edits are held in memory until you press Save / Ctrl+S. "
            "Press F5 to discard.")
        self.btn_rename_section = QPushButton("Rename Section")
        self.btn_rename_section.setToolTip(
            "STAGED — write on Save.\n\n"
            "Type a MAPSEC constant directly to reassign this map's "
            "in-game area (lower-level than 'Set In-Game Name'). Edits "
            "the region_map_section field in map.json only.\n\n"
            "Held in memory until Save / Ctrl+S. F5 discards.")
        self.btn_move_map = QPushButton("Move Map")
        self.btn_move_map.setToolTip(
            "IMMEDIATE WRITE.\n\n"
            "Moves this map into a different group in map_groups.json. "
            "If moved into an Indoor group, attempts to inherit the "
            "MAPSEC from sibling maps in that group.\n\n"
            "Not staged — confirms write directly to disk.")
        self.btn_delete_map = QPushButton("Delete Map")
        self.btn_delete_map.setToolTip(
            "IMMEDIATE WRITE — DESTRUCTIVE.\n\n"
            "Permanently deletes the map folder and layout folder, "
            "removes the map from map_groups.json, replaces every "
            "reference to its MAP_* constant with MAP_UNDEFINED across "
            "the project source, and regenerates headers.\n\n"
            "Not staged. Cannot be undone from within the app.")
        for btn in (self.btn_rename_map, self.btn_rename_ingame,
                    self.btn_rename_section, self.btn_move_map,
                    self.btn_delete_map):
            btn.setEnabled(False)
            maps_box_l.addWidget(btn)
        groups_row.addWidget(maps_box, 4)

        # Groups group
        grp_box = QGroupBox("Groups")
        grp_box_l = QHBoxLayout(grp_box)
        self.btn_create_group = QPushButton("Create Group")
        self.btn_create_group.setToolTip(
            "IMMEDIATE WRITE.\n\n"
            "Adds a new map group to map_groups.json (appends to "
            "group_order with an empty map list).\n\n"
            "Not staged — saves directly.")
        self.btn_rename_group = QPushButton("Rename Group")
        self.btn_rename_group.setToolTip(
            "IMMEDIATE WRITE.\n\n"
            "Renames a group in map_groups.json and rewrites every "
            "reference to the old group name across the project source.\n\n"
            "Not staged — saves directly. Cannot be undone from within "
            "the app.")
        self.btn_delete_group = QPushButton("Delete Group")
        self.btn_delete_group.setToolTip(
            "IMMEDIATE WRITE.\n\n"
            "Removes an empty group from map_groups.json. The group must "
            "contain zero maps before it can be deleted.\n\n"
            "Not staged — saves directly.")
        for btn in (self.btn_create_group, self.btn_rename_group,
                    self.btn_delete_group):
            btn.setEnabled(False)
            grp_box_l.addWidget(btn)
        groups_row.addWidget(grp_box, 3)

        # Maintenance group
        maint_box = QGroupBox("Maintenance")
        maint_box_l = QHBoxLayout(maint_box)
        self.btn_save = QPushButton("Save")
        self.btn_save.setToolTip(
            "Writes all staged Set In-Game Name and Rename Section edits "
            "to disk in one pass. The same flush also runs when you press "
            "Ctrl+S anywhere in the app.\n\n"
            "Has no effect on folder renames, deletes, group ops, or "
            "Clean Orphaned Data — those bypass staging and were already "
            "saved at the time you confirmed them.\n\n"
            "Greyed out when nothing is staged.")
        self.btn_save.setEnabled(False)
        maint_box_l.addWidget(self.btn_save)
        self.btn_clean_maps = QPushButton("Clean Orphaned Data")
        self.btn_clean_maps.setToolTip(
            "IMMEDIATE WRITE — DESTRUCTIVE.\n\n"
            "Scans map_groups.json for entries whose folders are missing "
            "or whose map.json is corrupt, removes them from the groups "
            "data, deletes any leftover folder remnants, replaces broken "
            "MAP_* references with MAP_UNDEFINED across the project, and "
            "regenerates headers.\n\n"
            "Not staged. Cannot be undone from within the app.")
        self.btn_clean_maps.setEnabled(False)
        maint_box_l.addWidget(self.btn_clean_maps)
        groups_row.addWidget(maint_box, 2)

        map_layout.addLayout(groups_row)

        splitter.addWidget(map_group)

        # Bottom: Warp Validator
        warp_group = QGroupBox("Warp Validator")
        warp_layout = QVBoxLayout(warp_group)

        warp_btn_row = QHBoxLayout()
        self.btn_check_warps = QPushButton("Check Warps")
        self.btn_check_warps.setToolTip(
            "READ-ONLY. Scans every map for warp events that point at "
            "maps that no longer exist. Lists results below — does not "
            "modify anything.")
        self.btn_check_warps.setEnabled(False)
        self.btn_clean_warps = QPushButton("Clean Invalid Warps")
        self.btn_clean_warps.setToolTip(
            "IMMEDIATE WRITE.\n\n"
            "Removes every warp event found by 'Check Warps' from each "
            "map's map.json. Run 'Check Warps' first to see what will be "
            "affected.\n\n"
            "Not staged — saves directly. Cannot be undone from within "
            "the app.")
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
        # Forward layouts_tab.data_changed up to MapsTab.data_changed so
        # the toolbar dot, title-bar asterisk, and unified F5/save flow
        # treat the whole Maps page (both sub-tabs) as one section.
        self.layouts_tab.data_changed.connect(self.data_changed.emit)
        self._sub_tabs.addTab(self.layouts_tab, "Layouts && Tilesets")

        # Connections
        self.btn_rename_map.clicked.connect(self._on_rename_map)
        self.btn_rename_ingame.clicked.connect(self._on_rename_ingame)
        self.btn_rename_group.clicked.connect(self._on_rename_group)
        self.btn_create_group.clicked.connect(self._on_create_group)
        self.btn_delete_group.clicked.connect(self._on_delete_group)
        self.btn_rename_section.clicked.connect(self._on_rename_section)
        self.btn_move_map.clicked.connect(self._on_move_map)
        self.btn_delete_map.clicked.connect(self._on_delete_map)
        self.btn_save.clicked.connect(self.save)
        self.btn_clean_maps.clicked.connect(self._on_clean_orphaned)
        self.btn_check_warps.clicked.connect(self._on_check_warps)
        self.btn_clean_warps.clicked.connect(self._on_clean_warps)

    # ─────────────────────────────────────────────────────────────────────────
    # Project loading
    # ─────────────────────────────────────────────────────────────────────────

    def _load_mapsec_names(self, project_dir: str):
        """Load MAPSEC_* → area name from region_map_sections.json.

        Names are stored exactly as they appear in the JSON — ALL CAPS.
        No transformation is applied so what we show matches what is on disk.
        """
        self._mapsec_names = {}
        rms_path = os.path.join(
            project_dir, "src", "data", "region_map", "region_map_sections.json"
        )
        if not os.path.exists(rms_path):
            return
        try:
            with open(rms_path) as f:
                rms = json.load(f)
            for entry in rms.get("map_sections", []):
                mapsec_id = entry.get("id", "")
                name = entry.get("name", "")
                if mapsec_id and name:
                    self._mapsec_names[mapsec_id] = name
        except Exception as e:
            self._mw.log_message(f"Maps tab: couldn't load region map section names: {e}")

    def load_project(self, project_info: dict):
        self.project_info = project_info
        project_dir = project_info.get("dir", "")

        self._load_mapsec_names(project_dir)

        from eventide.backend.map_renamer import MapRenamer
        from eventide.backend.warp_validator import WarpValidator
        try:
            self._renamer = MapRenamer(project_dir)
            self._warp_validator = WarpValidator(project_dir)
        except Exception as e:
            self._mw.log_message(f"Maps tab: failed to load backends: {e}")
            return

        for btn in (self.btn_rename_map, self.btn_rename_ingame,
                    self.btn_rename_group, self.btn_create_group,
                    self.btn_delete_group, self.btn_rename_section,
                    self.btn_move_map, self.btn_delete_map,
                    self.btn_clean_maps, self.btn_check_warps,
                    self.btn_clean_warps):
            btn.setEnabled(True)
        # Save stays disabled until something is staged.
        self.btn_save.setEnabled(False)

        # Drop any pending unsaved JSON edits — F5 / project load means
        # "revert to whatever's on disk".
        self._dirty_items.clear()
        self._pending_sections.clear()
        self._pending_rms.clear()
        self.btn_save.setEnabled(False)
        self._populate_tree()
        self._mw.log_message(f"Maps tab: loaded {project_dir}")

        self.layouts_tab.load_project(project_info)

    # ─────────────────────────────────────────────────────────────────────────
    # Dirty / amber helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _mark_dirty(self, *keys: str):
        for k in keys:
            self._dirty_items.add(k)

    def _apply_dirty_styling(self):
        """Tint tree items amber for any key in _dirty_items."""
        for i in range(self.map_tree.topLevelItemCount()):
            group_item = self.map_tree.topLevelItem(i)
            gdata = group_item.data(0, Qt.ItemDataRole.UserRole)
            if gdata and gdata.get("name") in self._dirty_items:
                group_item.setBackground(0, _AMBER)
            for j in range(group_item.childCount()):
                child = group_item.child(j)
                mdata = child.data(0, Qt.ItemDataRole.UserRole)
                if mdata and mdata.get("folder") in self._dirty_items:
                    for col in range(_TREE_COLS):
                        child.setBackground(col, _AMBER)

    def clear_dirty_markers(self):
        """Clear all amber highlighting and dirty tracking. Called on save and F5.

        Also discards any staged-but-unsaved JSON edits (section
        reassignments + region_map_sections additions/renames). On F5 the
        user expects "revert to disk" — that means dropping pending edits
        AND repopulating the tree from disk. Caller is responsible for
        triggering the repopulate (load_project does, _refresh_project
        already calls load_project).
        """
        self._dirty_items.clear()
        self._pending_sections.clear()
        self._pending_rms.clear()
        if hasattr(self, "btn_save"):
            self.btn_save.setEnabled(False)
        for i in range(self.map_tree.topLevelItemCount()):
            group_item = self.map_tree.topLevelItem(i)
            group_item.setBackground(0, _TRANSPARENT)
            for j in range(group_item.childCount()):
                child = group_item.child(j)
                for col in range(_TREE_COLS):
                    child.setBackground(col, _TRANSPARENT)

    # ─────────────────────────────────────────────────────────────────────────
    # Tree population and filtering
    # ─────────────────────────────────────────────────────────────────────────

    def _populate_tree(self):
        self.map_tree.clear()
        if not self._renamer:
            return
        groups = self._renamer.groups
        maps_dir = self._renamer.maps_dir

        for group_name in groups.get('group_order', []):
            group_item = QTreeWidgetItem(self.map_tree, [group_name, "", "", ""])
            group_item.setData(0, Qt.ItemDataRole.UserRole,
                               {"type": "group", "name": group_name})

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

                # Overlay pending section reassignments so the tree shows
                # what Save will produce.
                section = self._effective_section(map_folder, section)

                # In-game area name from region_map_sections.json, verbatim
                # (ALL CAPS — never transformed). Empty string if no MAPSEC
                # match (e.g. MAPSEC_NONE or a custom section not yet added
                # to the region map data).
                area_name = self._mapsec_names.get(section, "")

                # Name      — folder name (unique code identifier)
                # In-Game   — area name driven by Section (read-only)
                # Section   — raw MAPSEC constant (edited by Rename Section)
                # Layout    — raw layout id (read-only reference)
                map_item = QTreeWidgetItem(group_item,
                                           [map_folder, area_name,
                                            section, layout_id])

                # In-game cell rendered in muted italics so the eye reads
                # it as supplementary context, not as the primary identifier.
                if area_name:
                    f = map_item.font(_COL_INGAME)
                    f.setItalic(True)
                    map_item.setFont(_COL_INGAME, f)
                    map_item.setForeground(_COL_INGAME, QColor("#9aa0a6"))

                map_item.setData(0, Qt.ItemDataRole.UserRole, {
                    "type": "map",
                    "folder": map_folder,
                    "group": group_name,
                    "section": section,
                    "area_name": area_name,   # raw ALL CAPS, may be ""
                })

        self.map_tree.expandAll()
        for col in range(_TREE_COLS):
            self.map_tree.resizeColumnToContents(col)

        # Status line: count groups (excluding group_order key) and maps.
        n_groups = len(groups.get('group_order', []))
        n_maps = sum(len(groups.get(g, [])) for g in groups.get('group_order', []))
        self.status_lbl.setText(f"{n_groups} groups · {n_maps} maps loaded")

    def _filter_tree(self, text: str):
        text = text.lower()
        for i in range(self.map_tree.topLevelItemCount()):
            group_item = self.map_tree.topLevelItem(i)
            any_visible = False
            for j in range(group_item.childCount()):
                child = group_item.child(j)
                mdata = child.data(0, Qt.ItemDataRole.UserRole) or {}
                # Search against: folder name, MAPSEC constant, layout ID,
                # and the in-game area name.
                folder = mdata.get("folder", "").lower()
                section = mdata.get("section", "").lower()
                area = mdata.get("area_name", "").lower()
                layout = child.text(_COL_LAYOUT).lower()
                visible = (
                    not text
                    or text in folder
                    or text in section
                    or text in layout
                    or text in area
                )
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
            QMessageBox.information(self, "Rename Map",
                                    "Select a map in the tree first.")
            return

        old_folder = data["folder"]
        area_name = data.get("area_name", "")   # ALL CAPS, may be ""

        # Single-field dialog. Shows in-game area as read-only context so the
        # user knows which map they are about to rename without ambiguity.
        dlg = QDialog(self)
        dlg.setWindowTitle("Rename Map")
        dlg.setMinimumWidth(400)
        form = QFormLayout(dlg)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)

        if area_name:
            area_lbl = QLabel(f"<b>{area_name}</b>")
            area_lbl.setToolTip("In-game area name (from region_map_sections.json)")
            form.addRow("In-game area:", area_lbl)

        folder_edit = QLineEdit(old_folder)
        folder_edit.setToolTip(
            "Renames the folder on disk, updates map.json, and rewrites all "
            "source references across the project."
        )
        form.addRow("New folder name:", folder_edit)

        note = QLabel(
            "<small>"
            "<b>This is an IMMEDIATE-WRITE action.</b> Confirming it "
            "renames the folder on disk, renames the matching layout "
            "folder, rewrites every reference to the old folder name "
            "across the entire project source, and regenerates "
            "headers.<br>"
            "It is <b>not</b> staged with the Save button and cannot "
            "be reverted with F5. Make sure you have a backup or have "
            "committed to Git before continuing."
            "</small>"
        )
        note.setStyleSheet("color:#fc9; padding: 4px;")
        note.setWordWrap(True)
        form.addRow(note)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        form.addRow(btns)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        new_folder = folder_edit.text().strip()
        if not new_folder or new_folder == old_folder:
            return

        confirm = QMessageBox.warning(
            self, "Rename Map — Confirm",
            f"Rename '{old_folder}' → '{new_folder}'?\n\n"
            f"This renames the map folder and rewrites all source references "
            f"across the project.{_WARN_IMMEDIATE}",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        if confirm != QMessageBox.StandardButton.Ok:
            return

        new_id = f"MAP_{new_folder.upper()}"
        try:
            self._renamer.rename_map(
                data["group"], old_folder,
                new_folder=new_folder, new_id=new_id,
                callback=lambda msg: self._mw.log_message(msg))
            self._mw.log_message(f"Renamed map {old_folder} → {new_folder}")
            self._mark_dirty(new_folder)
            self._populate_tree()
            self._apply_dirty_styling()
            self.data_changed.emit()
        except Exception as e:
            QMessageBox.critical(self, "Rename Map", str(e))

    def _maps_sharing_section(self, section: str) -> list[str]:
        """Return list of map folder names whose map.json uses this MAPSEC."""
        out: list[str] = []
        if not self._renamer or not section:
            return out
        for grp in self._renamer.groups.get('group_order', []):
            for m in self._renamer.groups.get(grp, []):
                mp = os.path.join(self._renamer.maps_dir, m, 'map.json')
                try:
                    with open(mp) as f:
                        md = json.load(f)
                    if md.get('region_map_section', '') == section:
                        out.append(m)
                except Exception:
                    pass
        return out

    @staticmethod
    def _suggest_mapsec_const(folder: str, taken: set[str]) -> str:
        """Suggest a MAPSEC_* constant from a folder name, ensuring uniqueness."""
        # CamelCase → UPPER_SNAKE: "SSAnne_B1F" → "SSANNE_B1F"
        s = re.sub(r'(?<=[a-z0-9])(?=[A-Z])', '_', folder)
        s = re.sub(r'[^A-Za-z0-9]+', '_', s).strip('_').upper()
        base = f"MAPSEC_{s}" if s else "MAPSEC_NEW"
        cand = base
        n = 2
        while cand in taken:
            cand = f"{base}_{n}"
            n += 1
        return cand

    def _write_map_section(self, folder: str, section: str):
        """Write region_map_section into a map's map.json."""
        path = os.path.join(self._renamer.maps_dir, folder, 'map.json')
        with open(path) as f:
            data = json.load(f)
        data['region_map_section'] = section
        with open(path, 'w', newline='\n') as f:
            json.dump(data, f, indent=2)
            f.write('\n')

    def _write_region_map_sections(self, rms: dict):
        """Persist the region_map_sections.json document."""
        project_dir = self.project_info.get("dir", "") if self.project_info else ""
        rms_path = os.path.join(
            project_dir, "src", "data", "region_map", "region_map_sections.json"
        )
        with open(rms_path, 'w', newline='\n') as f:
            json.dump(rms, f, indent=2)
            f.write('\n')

    def _load_region_map_sections(self) -> tuple[str, dict]:
        """Return (path, parsed-json) for region_map_sections.json."""
        project_dir = self.project_info.get("dir", "") if self.project_info else ""
        rms_path = os.path.join(
            project_dir, "src", "data", "region_map", "region_map_sections.json"
        )
        with open(rms_path) as f:
            return rms_path, json.load(f)

    def _on_rename_ingame(self):
        """Set the in-game area name for the selected map.

        Three modes:
          A) Assign an existing MAPSEC (swap this map into a different
             named area; doesn't affect any other map).
          B) Create a new MAPSEC just for this map (splits it off from
             whatever it currently shares; gives it a unique name).
          C) Rename the current MAPSEC globally (affects every map that
             shares it; explicit warning + scope listed).
        """
        data = self._selected_item_data()
        if not data or data.get("type") != "map":
            QMessageBox.information(self, "Set In-Game Name",
                                    "Select a map in the tree first.")
            return

        folder = data["folder"]
        cur_section = data.get("section", "") or "MAPSEC_NONE"
        cur_name = self._mapsec_names.get(cur_section, "")
        shared = self._maps_sharing_section(cur_section)
        # Existing sections sorted by display name for the dropdown.
        all_sections = sorted(self._mapsec_names.items(),
                              key=lambda kv: (kv[1], kv[0]))

        dlg = QDialog(self)
        dlg.setWindowTitle("Set In-Game Name")
        dlg.setMinimumWidth(580)
        outer = QVBoxLayout(dlg)

        # Header: which map, current state.
        header = QLabel(
            f"<b>Map:</b> <code>{folder}</code><br>"
            f"<b>Currently:</b> "
            f"{cur_name + ' ' if cur_name else ''}"
            f"<small><code>({cur_section})</code></small>"
        )
        header.setTextFormat(Qt.TextFormat.RichText)
        outer.addWidget(header)

        # Up-front explainer of what "in-game name" means and how the three
        # options differ — beginners shouldn't have to infer this from
        # radio labels alone.
        intro = QLabel(
            "<small>"
            "The <b>in-game name</b> is the area label the player sees "
            "(e.g. <i>VIRIDIAN FOREST</i>, <i>S.S. ANNE</i>). It is "
            "stored once per <b>MAPSEC constant</b> in "
            "<code>region_map_sections.json</code>, and many maps can "
            "share the same MAPSEC — which is why renaming one shared "
            "name renames it everywhere unless you split it off first."
            "</small>"
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color:#bbb; padding: 2px 0 6px 0;")
        outer.addWidget(intro)

        # ── Mode group ──────────────────────────────────────────────────────
        mode_group = QButtonGroup(dlg)

        _MODE_DESC_SS = "color:#9aa0a6; padding: 0 0 4px 22px;"

        # MODE A: assign existing
        a_box = QGroupBox()
        a_box_l = QVBoxLayout(a_box)
        a_radio = QRadioButton(
            "Pick an existing in-game area for this map only")
        a_radio.setChecked(True)
        mode_group.addButton(a_radio, 0)
        a_box_l.addWidget(a_radio)
        a_desc = QLabel(
            "<small>"
            "Reassigns this map's MAPSEC to point at one of the areas "
            "already defined in the region map. <b>Only this single map "
            "changes</b> — every other map keeps its current area. The "
            "in-game text already exists, so nothing new is added to the "
            "region map data."
            "<br><i>Use this when you want this map to display the same "
            "area name as some other existing area.</i>"
            "</small>"
        )
        a_desc.setWordWrap(True)
        a_desc.setStyleSheet(_MODE_DESC_SS)
        a_box_l.addWidget(a_desc)
        a_combo = QComboBox()
        # Prevent wheel scrolling per CLAUDE.md UI rules.
        a_combo.wheelEvent = lambda e: e.ignore()
        for sec_id, sec_name in all_sections:
            label = f"{sec_name}   —   {sec_id}" if sec_name else sec_id
            a_combo.addItem(label, sec_id)
        # Pre-select current
        for i in range(a_combo.count()):
            if a_combo.itemData(i) == cur_section:
                a_combo.setCurrentIndex(i)
                break
        a_box_l.addWidget(a_combo)
        outer.addWidget(a_box)

        # MODE B: create new
        b_box = QGroupBox()
        b_box_l = QVBoxLayout(b_box)
        b_radio = QRadioButton(
            "Make this map its own brand-new area")
        mode_group.addButton(b_radio, 1)
        b_box_l.addWidget(b_radio)
        b_desc = QLabel(
            "<small>"
            "Adds a fresh entry to <code>region_map_sections.json</code> "
            "with a new MAPSEC constant and the name you give it, then "
            "points this map at it. <b>Splits this map off</b> from any "
            "MAPSEC it currently shares with others — those other maps "
            "are not touched."
            "<br>The new area will not be placed on the world map "
            "automatically — open the Region Map editor afterwards if "
            "you want it visible there."
            "<br><i>Use this when you want this map to have a "
            "<b>unique</b> in-game name nothing else uses.</i>"
            "</small>"
        )
        b_desc.setWordWrap(True)
        b_desc.setStyleSheet(_MODE_DESC_SS)
        b_box_l.addWidget(b_desc)
        b_form = QFormLayout()

        # Name field with counter (max 18 — engine buffer is mapName[19]).
        b_name = QLineEdit()
        b_name.setPlaceholderText("e.g. NORTH WING")
        b_name.setToolTip(
            "Convention: ALL CAPS to match the project's in-game text. "
            f"Hard limit: {INGAME_NAME_LENGTH} characters "
            "(engine buffer is u8 mapName[19]).")
        b_name_counter = QLabel()
        b_name_row = QHBoxLayout()
        b_name_row.setContentsMargins(0, 0, 0, 0)
        b_name_row.setSpacing(6)
        b_name_row.addWidget(b_name)
        b_name_row.addWidget(b_name_counter)
        _attach_char_counter(b_name, b_name_counter, INGAME_NAME_LENGTH)
        b_form.addRow("New name:", b_name_row)

        # MAPSEC constant field. Auto-syncs to the name as the user types,
        # but stops as soon as they manually edit the constant — and
        # resumes if they clear it back to empty (sensible recovery).
        taken = set(self._mapsec_names.keys())
        initial_const = self._suggest_mapsec_const(folder, taken)
        b_const = QLineEdit(initial_const)
        b_const.setToolTip(
            "MAPSEC_* constant identifier. Auto-fills from the name as "
            "you type — edit it manually to override (auto-fill stops "
            "until you clear the field).\n\n"
            f"Soft limit: {MAPSEC_CONST_LENGTH} characters.")
        b_const_counter = QLabel()
        b_const_row = QHBoxLayout()
        b_const_row.setContentsMargins(0, 0, 0, 0)
        b_const_row.setSpacing(6)
        b_const_row.addWidget(b_const)
        b_const_row.addWidget(b_const_counter)
        _attach_char_counter(b_const, b_const_counter, MAPSEC_CONST_LENGTH)
        b_form.addRow("MAPSEC constant:", b_const)

        # Auto-sync state machine: tracks whether the user has overridden
        # the auto-fill. Suppresses the const→state guard during our own
        # programmatic writes so we don't see them as user edits.
        b_state = {"auto": True, "suppress": False}

        def _const_from_name() -> str:
            txt = b_name.text()
            base = re.sub(r'(?<=[a-z0-9])(?=[A-Z])', '_', txt)
            base = re.sub(r'[^A-Za-z0-9]+', '_', base).strip('_').upper()
            cand = f"MAPSEC_{base}" if base else ""
            if not cand:
                return ""
            # Ensure uniqueness against existing MAPSECs.
            if cand not in taken:
                return cand
            n = 2
            while f"{cand}_{n}" in taken:
                n += 1
            return f"{cand}_{n}"

        def _on_name_changed(_text=None):
            if not b_state["auto"]:
                return
            new_const = _const_from_name()
            if new_const and new_const != b_const.text():
                b_state["suppress"] = True
                b_const.setText(new_const[:MAPSEC_CONST_LENGTH])
                b_state["suppress"] = False

        def _on_const_changed(_text=None):
            if b_state["suppress"]:
                return
            # Manual edit detected → leave auto-fill mode.
            # If the user clears the field back to empty, resume auto-fill.
            if b_const.text() == "":
                b_state["auto"] = True
                _on_name_changed()
            else:
                b_state["auto"] = False

        b_name.textChanged.connect(_on_name_changed)
        b_const.textChanged.connect(_on_const_changed)

        b_box_l.addLayout(b_form)
        outer.addWidget(b_box)

        # MODE C: rename current globally
        c_box = QGroupBox()
        c_box_l = QVBoxLayout(c_box)
        c_radio = QRadioButton(
            f"Rename the current area for everyone "
            f"({len(shared)} map(s) share this MAPSEC)")
        mode_group.addButton(c_radio, 2)
        c_box_l.addWidget(c_radio)
        c_desc = QLabel(
            "<small>"
            "Edits the <i>name</i> field of the current MAPSEC entry in "
            f"<code>region_map_sections.json</code>. <b>Every map "
            f"sharing <code>{cur_section}</code> will start displaying "
            "the new name</b> — listed below so you can see the scope "
            "before applying."
            "<br><i>Use this when you want to rename the area itself "
            "(e.g. you decided <i>PALLET TOWN</i> should now be called "
            "<i>HYRULE FIELD</i> across the whole project).</i>"
            "</small>"
        )
        c_desc.setWordWrap(True)
        c_desc.setStyleSheet(_MODE_DESC_SS)
        c_box_l.addWidget(c_desc)
        c_form = QFormLayout()
        c_edit = QLineEdit(cur_name)
        c_edit.setToolTip(
            "The new global name — every map sharing the current MAPSEC "
            "will display this.\n\n"
            f"Hard limit: {INGAME_NAME_LENGTH} characters "
            "(engine buffer is u8 mapName[19]).")
        c_counter = QLabel()
        c_row = QHBoxLayout()
        c_row.setContentsMargins(0, 0, 0, 0)
        c_row.setSpacing(6)
        c_row.addWidget(c_edit)
        c_row.addWidget(c_counter)
        _attach_char_counter(c_edit, c_counter, INGAME_NAME_LENGTH)
        c_form.addRow("New name:", c_row)
        if len(shared) > 1:
            others = ', '.join(shared[:8]) + (
                ' …' if len(shared) > 8 else '')
            c_scope = QLabel(
                f"<small><b>Will rename for:</b> {others}</small>")
            c_scope.setWordWrap(True)
            c_form.addRow(c_scope)
        c_box_l.addLayout(c_form)
        if not cur_name:
            c_radio.setEnabled(False)
            c_radio.setToolTip(
                "Disabled — the current MAPSEC has no entry in "
                "region_map_sections.json yet.")
        outer.addWidget(c_box)

        # Enable/disable inputs based on selected mode.
        def _sync():
            a_combo.setEnabled(a_radio.isChecked())
            b_name.setEnabled(b_radio.isChecked())
            b_const.setEnabled(b_radio.isChecked())
            c_edit.setEnabled(c_radio.isChecked() and c_radio.isEnabled())
        for r in (a_radio, b_radio, c_radio):
            r.toggled.connect(_sync)
        _sync()

        note = QLabel(
            "<small>"
            "<b>Save model:</b> all three options are <b>staged in "
            "memory</b> when you click OK — nothing is written to disk "
            "yet. Press <b>Save</b> on the Map Manager (or Ctrl+S "
            "anywhere in the app) to flush all staged edits. Press "
            "<b>F5</b> to discard them and reload from disk."
            "</small>"
        )
        note.setStyleSheet("color:#8fc; padding: 4px;")
        note.setWordWrap(True)
        outer.addWidget(note)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        outer.addWidget(btns)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        try:
            mode = mode_group.checkedId()
            if mode == 0:
                self._apply_ingame_assign(folder, a_combo.currentData(),
                                          cur_section)
            elif mode == 1:
                self._apply_ingame_create(folder,
                                          b_name.text().strip(),
                                          b_const.text().strip(),
                                          cur_section)
            elif mode == 2:
                self._apply_ingame_rename_global(cur_section,
                                                 c_edit.text().strip(),
                                                 cur_name, shared)
            else:
                return
        except Exception as e:
            QMessageBox.critical(self, "Set In-Game Name", str(e))
            return

        self._populate_tree()
        self._apply_dirty_styling()
        self.data_changed.emit()

    # ─── Staging helpers (deferred-save) ────────────────────────────────────

    def _has_pending(self) -> bool:
        return bool(self._pending_sections) or bool(self._pending_rms)

    def _refresh_save_state(self):
        """Reflect pending state in the Save button + log a hint."""
        self.btn_save.setEnabled(self._has_pending())

    def _effective_section(self, folder: str, disk_section: str) -> str:
        """Return the section the tree should display for this map.

        Pending reassignments override what's on disk.
        """
        return self._pending_sections.get(folder, disk_section)

    def _apply_ingame_assign(self, folder: str, new_section: str,
                             old_section: str):
        if not new_section or new_section == old_section:
            return
        # Stage; remove the entry if it equals disk to avoid no-op writes.
        disk = self._read_disk_section(folder)
        if new_section == disk:
            self._pending_sections.pop(folder, None)
        else:
            self._pending_sections[folder] = new_section
        self._mark_dirty(folder)
        self._refresh_save_state()
        self._mw.log_message(
            f"Staged: {folder} {old_section} → {new_section} (unsaved)")

    def _apply_ingame_create(self, folder: str, new_name: str,
                             new_const: str, old_section: str):
        if not new_name:
            raise ValueError("New name is required.")
        if not new_const or not new_const.startswith("MAPSEC_"):
            raise ValueError("MAPSEC constant must start with 'MAPSEC_'.")
        if new_const in self._mapsec_names:
            raise ValueError(
                f"MAPSEC '{new_const}' already exists. Pick a different "
                "constant or use 'Assign existing' to use it as-is.")
        # Stage: new MAPSEC entry + section reassignment for this map.
        self._pending_rms[new_const] = {"name": new_name, "new": True}
        self._mapsec_names[new_const] = new_name   # tree shows future state
        self._pending_sections[folder] = new_const
        self._mark_dirty(folder)
        self._refresh_save_state()
        self._mw.log_message(
            f"Staged: new MAPSEC {new_const} ('{new_name}') for "
            f"{folder} (was {old_section}) — unsaved")

    def _apply_ingame_rename_global(self, section: str, new_name: str,
                                    old_name: str, shared: list[str]):
        if not new_name or new_name == old_name:
            return
        # Stage rename of an existing MAPSEC; tree updates immediately.
        self._pending_rms[section] = {"name": new_name, "new": False}
        self._mapsec_names[section] = new_name
        self._mark_dirty(*shared)
        self._refresh_save_state()
        self._mw.log_message(
            f"Staged: {section} '{old_name}' → '{new_name}' "
            f"(affects {len(shared)} map(s)) — unsaved")

    def _read_disk_section(self, folder: str) -> str:
        path = os.path.join(self._renamer.maps_dir, folder, 'map.json')
        try:
            with open(path) as f:
                return json.load(f).get('region_map_section', '')
        except Exception:
            return ''

    # ─── Save / revert ──────────────────────────────────────────────────────

    def save(self) -> bool:
        """Flush staged section + region-map edits to disk.

        Returns True on success (or when nothing was staged), False on any
        write error. Heavy filesystem ops (folder renames, deletes) are
        unaffected — those still write immediately at the time of the op.
        """
        if not self._has_pending():
            return True

        # 1) region_map_sections.json — apply renames + new entries.
        if self._pending_rms:
            try:
                rms_path, rms = self._load_region_map_sections()
                sections = rms.setdefault("map_sections", [])
                existing_ids = {e.get("id"): e for e in sections}
                for sec_id, info in self._pending_rms.items():
                    if info["new"]:
                        if sec_id in existing_ids:
                            # Race / re-stage — just update the name.
                            existing_ids[sec_id]["name"] = info["name"]
                        else:
                            sections.append({
                                "id": sec_id,
                                "name": info["name"],
                                "x": 0, "y": 0,
                                "width": 1, "height": 1,
                                "valid": False,
                            })
                    else:
                        if sec_id in existing_ids:
                            existing_ids[sec_id]["name"] = info["name"]
                        else:
                            raise RuntimeError(
                                f"MAPSEC '{sec_id}' missing from "
                                f"region_map_sections.json")
                with open(rms_path, 'w', newline='\n') as f:
                    json.dump(rms, f, indent=2)
                    f.write('\n')
            except Exception as e:
                QMessageBox.critical(self, "Save Maps",
                                     f"Failed writing region_map_sections.json: {e}")
                return False

        # 2) map.json per-map region_map_section reassignments.
        failed: list[str] = []
        for folder, section in list(self._pending_sections.items()):
            try:
                self._write_map_section(folder, section)
            except Exception as e:
                failed.append(f"{folder}: {e}")
        if failed:
            QMessageBox.critical(
                self, "Save Maps",
                "Some map.json writes failed:\n\n" + "\n".join(failed))
            return False

        n_sec = len(self._pending_sections)
        n_rms = len(self._pending_rms)
        self._pending_sections.clear()
        self._pending_rms.clear()
        self._mw.log_message(
            f"Maps tab: saved {n_sec} section reassignment(s), "
            f"{n_rms} region-map edit(s)")

        # Repopulate so disk values feed the tree, clear amber.
        self.clear_dirty_markers()
        self._populate_tree()
        self._refresh_save_state()
        # Notify the unified mainwindow that this section is clean now.
        # data_changed fires for any mutation; mark explicitly clean.
        try:
            self._mw.set_page_dirty("maps", False)
        except Exception:
            pass
        return True

    def _on_rename_group(self):
        data = self._selected_item_data()
        if not data or data.get("type") != "group":
            QMessageBox.information(self, "Rename Group",
                                    "Select a group in the tree first.")
            return
        old_name = data["name"]
        new_name, ok = QInputDialog.getText(
            self, "Rename Group", f"New name for '{old_name}':", text=old_name)
        if not ok or not new_name.strip() or new_name == old_name:
            return
        new_name = new_name.strip()
        confirm = QMessageBox.warning(
            self, "Rename Group — Confirm",
            f"Rename group '{old_name}' → '{new_name}'?{_WARN_IMMEDIATE}",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        if confirm != QMessageBox.StandardButton.Ok:
            return
        try:
            self._renamer.rename_group(old_name, new_name)
            self._mw.log_message(f"Renamed group {old_name} → {new_name}")
            self._mark_dirty(new_name)
            self._populate_tree()
            self._apply_dirty_styling()
            self.data_changed.emit()
        except Exception as e:
            QMessageBox.critical(self, "Rename Group", str(e))

    def _on_create_group(self):
        name, ok = QInputDialog.getText(self, "Create Group", "New group name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        try:
            self._renamer.create_group(name)
            self._mw.log_message(f"Created group {name}")
            self._mark_dirty(name)
            self._populate_tree()
            self._apply_dirty_styling()
            self.data_changed.emit()
        except Exception as e:
            QMessageBox.critical(self, "Create Group", str(e))

    def _on_delete_group(self):
        data = self._selected_item_data()
        if not data or data.get("type") != "group":
            QMessageBox.information(self, "Delete Group",
                                    "Select an empty group in the tree first.")
            return
        name = data["name"]
        reply = QMessageBox.warning(
            self, "Delete Group — Confirm",
            f"Delete empty group '{name}'?{_WARN_IMMEDIATE}",
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
        """Reassign a map's MAPSEC by typing the constant directly.

        Lower-level than 'Set In-Game Name' — useful when you already know
        the constant. Stages the change in memory like the other section
        ops; nothing is written until Save.
        """
        data = self._selected_item_data()
        if not data or data.get("type") != "map":
            QMessageBox.information(self, "Rename Section",
                                    "Select a map in the tree first.")
            return
        folder = data["folder"]
        old_section = self._effective_section(folder,
                                              self._read_disk_section(folder))
        new_section, ok = QInputDialog.getText(
            self, "Rename Section",
            f"New region_map_section for '{folder}':", text=old_section)
        if not ok or not new_section.strip() or new_section == old_section:
            return
        new_section = new_section.strip()
        try:
            self._apply_ingame_assign(folder, new_section, old_section)
        except Exception as e:
            QMessageBox.critical(self, "Rename Section", str(e))
            return
        self._populate_tree()
        self._apply_dirty_styling()
        self.data_changed.emit()

    def _on_move_map(self):
        data = self._selected_item_data()
        if not data or data.get("type") != "map":
            QMessageBox.information(self, "Move Map",
                                    "Select a map in the tree first.")
            return
        groups = self._renamer.groups.get('group_order', [])
        new_group, ok = QInputDialog.getItem(
            self, "Move Map",
            f"Move '{data['folder']}' to group:", groups, editable=False)
        if not ok or new_group == data["group"]:
            return
        confirm = QMessageBox.warning(
            self, "Move Map — Confirm",
            f"Move '{data['folder']}' from '{data['group']}' to "
            f"'{new_group}'?{_WARN_IMMEDIATE}",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        if confirm != QMessageBox.StandardButton.Ok:
            return
        try:
            self._renamer.move_map(data["group"], data["folder"], new_group)
            self._mw.log_message(
                f"Moved {data['folder']} from {data['group']} to {new_group}")
            self._mark_dirty(data["folder"])
            self._populate_tree()
            self._apply_dirty_styling()
            self.data_changed.emit()
        except Exception as e:
            QMessageBox.critical(self, "Move Map", str(e))

    def _on_delete_map(self):
        data = self._selected_item_data()
        if not data or data.get("type") != "map":
            QMessageBox.information(self, "Delete Map",
                                    "Select a map in the tree first.")
            return
        folder = data["folder"]
        reply = QMessageBox.warning(
            self, "Delete Map — Confirm",
            f"Delete map '{folder}'?\n\nThis permanently removes the map folder "
            f"and rewrites all references across the project.{_WARN_IMMEDIATE}",
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
        reply = QMessageBox.warning(
            self, "Clean Orphaned Data — Confirm",
            "Remove maps from map_groups.json whose folders are missing or "
            "corrupt?\nThis will also regenerate headers."
            f"{_WARN_IMMEDIATE}",
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
            lines.append(f"  {rel}  warp #{issue.index}  → {issue.dest_map}")
        self.warp_results.setPlainText("\n".join(lines))
        self._mw.log_message(f"Warp check: {len(issues)} invalid warp(s) found")

    def _on_clean_warps(self):
        if not self._warp_validator:
            return
        reply = QMessageBox.warning(
            self, "Clean Invalid Warps — Confirm",
            "Remove all warp events that reference non-existent maps?\n\n"
            "Run 'Check Warps' first to review which maps will be affected."
            f"{_WARN_IMMEDIATE}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            count, affected_folders = self._warp_validator.clean_invalid_warps()
            self.warp_results.setPlainText(f"Removed {count} invalid warp(s).")
            self._mw.log_message(f"Cleaned {count} invalid warp(s)")
            if count > 0:
                self._mark_dirty(*affected_folders)
                self._apply_dirty_styling()
                self.data_changed.emit()
        except Exception as e:
            QMessageBox.critical(self, "Clean Invalid Warps", str(e))

    # ─────────────────────────────────────────────────────────────────────────
    # Context menu
    # ─────────────────────────────────────────────────────────────────────────

    def _on_tree_context_menu(self, pos):
        item = self.map_tree.itemAt(pos)
        if not item:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data or data.get("type") != "map":
            return

        menu = QMenu(self)
        act_porymap = menu.addAction("Open in Porymap")

        try:
            from porymap_bridge.porymap_launcher import is_porymap_installed
            act_porymap.setEnabled(is_porymap_installed())
        except ImportError:
            act_porymap.setEnabled(False)

        action = menu.exec(self.map_tree.mapToGlobal(pos))
        if action == act_porymap:
            self._open_map_in_porymap(data["folder"])

    def _open_map_in_porymap(self, map_folder: str):
        if not self.project_info:
            return
        try:
            from porymap_bridge.porymap_launcher import launch_porymap
            project_dir = self.project_info.get("dir", "")
            if project_dir:
                launch_porymap(project_dir, map_folder)
        except Exception as e:
            QMessageBox.warning(self, "Open in Porymap",
                                f"Could not launch Porymap: {e}")
