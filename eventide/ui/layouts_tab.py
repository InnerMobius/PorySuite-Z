"""
Layouts & Tilesets tab — Layout Manager + Tileset Manager

Every action in this sub-tab writes to disk immediately via the layout/
tileset renamer backends. There is no staging layer here — these ops do
filesystem renames and repo-wide source rewrites that are not safely
deferrable. Each dialog warns the user explicitly and the tooltips are
labelled IMMEDIATE WRITE.

The sub-tab participates in the Maps page dirty-flag system through its
data_changed signal: the parent MapsTab forwards it up to the toolbar
dot + title-bar asterisk wiring.
"""

from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QComboBox, QPushButton, QGroupBox, QLabel, QFormLayout, QLineEdit,
    QDialog, QDialogButtonBox, QInputDialog, QMessageBox, QMenu,
)


_WARN_IMMEDIATE_LAYOUT = (
    "\n\n⚠  WRITES TO DISK IMMEDIATELY when you confirm. Renames or "
    "deletes here update layouts.json and rewrite every reference to the "
    "old name across the project source. Cannot be undone from within "
    "the app.\nMake a backup or commit to Git before proceeding."
)


# Engine / convention limits for identifiers.
# Layout ids and tileset labels are C symbols — keep them under 60 to stay
# readable in headers and avoid generated-symbol overflow.
LAYOUT_ID_MAX = 60
TILESET_LABEL_MAX = 60
TILESET_FOLDER_MAX = 48


def _attach_char_counter(line_edit: QLineEdit, counter_lbl: QLabel,
                         max_chars: int) -> None:
    """Wire a max-length cap + live grey/amber/red counter to a QLineEdit.

    Mirrors the items / abilities pattern so every text input across the
    app uses the same grammar.
    """
    line_edit.setMaxLength(max_chars)
    base_ss = "font-size: 10px; font-family: 'Courier New';"

    def _refresh(_text=None):
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


def _prompt_text(parent, title: str, label: str, current: str,
                 max_chars: int, helper: str = "") -> tuple[str, bool]:
    """Modal text-entry dialog with a live character counter.

    Returns (text, ok). Replaces QInputDialog.getText where we need a
    character cap + visual counter for consistency with the rest of the
    app.
    """
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    layout = QVBoxLayout(dlg)
    if helper:
        h = QLabel(helper)
        h.setWordWrap(True)
        h.setStyleSheet("color: #aaa; font-size: 11px;")
        layout.addWidget(h)
    layout.addWidget(QLabel(label))
    edit = QLineEdit(current)
    layout.addWidget(edit)
    counter = QLabel("")
    counter.setAlignment(Qt.AlignmentFlag.AlignRight)
    layout.addWidget(counter)
    _attach_char_counter(edit, counter, max_chars)
    bb = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    bb.accepted.connect(dlg.accept)
    bb.rejected.connect(dlg.reject)
    layout.addWidget(bb)
    edit.setFocus()
    edit.selectAll()
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return "", False
    return edit.text().strip(), True


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

        # ── Header banner — explains save model + intent of this sub-tab ─────
        banner = QLabel(
            "<small>"
            "<b>Layouts</b> are the tilemaps + dimensions a map references "
            "from <code>data/layouts/layouts.json</code>. <b>Tilesets</b> "
            "are the graphics + metatiles a layout uses. Use this sub-tab "
            "to rename either, swap which tilesets a layout uses, or "
            "remove orphaned data."
            "<br><br>"
            "<b>Save model:</b> "
            "<span style='color:#f99'>Every action in this sub-tab "
            "writes to disk immediately</span> when you confirm — there "
            "is no Save / F5 staging here. Renames rewrite every "
            "reference across the project source. Back up or commit to "
            "Git before using these."
            "</small>"
        )
        banner.setWordWrap(True)
        banner.setStyleSheet("padding: 4px; color: #ccc;")
        layout.addWidget(banner)

        # ── Layout Manager ───────────────────────────────────────────────────
        layout_group = QGroupBox("Layout Manager")
        layout_inner = QVBoxLayout(layout_group)

        form = QFormLayout()
        self.layout_combo = QComboBox()
        self.layout_combo.wheelEvent = lambda e: e.ignore()
        self.layout_combo.setPlaceholderText("Select a layout...")
        self.layout_combo.setToolTip(
            "Pick the layout to rename, delete, or open in Porymap. "
            "Selection is required for the buttons below.")
        form.addRow("Layout:", self.layout_combo)
        layout_inner.addLayout(form)

        btn_row = QHBoxLayout()
        self.btn_rename_layout = QPushButton("Rename Layout")
        self.btn_rename_layout.setToolTip(
            "IMMEDIATE WRITE.\n\n"
            "Renames the layout id (e.g. LAYOUT_PALLET_TOWN), renames "
            "the matching folder under data/layouts/, updates "
            "layouts.json, and rewrites every reference to the old id "
            "across the project source.\n\n"
            "Not staged — confirms write directly to disk. Cannot be "
            "undone from within the app.")
        self.btn_delete_layout = QPushButton("Delete Layout")
        self.btn_delete_layout.setToolTip(
            "IMMEDIATE WRITE — DESTRUCTIVE.\n\n"
            "Removes the layout from layouts.json, deletes its folder "
            "under data/layouts/, and rewrites every reference across "
            "the project source. Refuses to run if any map still uses "
            "this layout — reassign those maps first.\n\n"
            "Not staged. Cannot be undone from within the app.")
        self.btn_clean_layouts = QPushButton("Clean Orphaned Layouts")
        self.btn_clean_layouts.setToolTip(
            "IMMEDIATE WRITE.\n\n"
            "Scans layouts.json for entries whose folders are missing "
            "and that no map references, and removes them.\n\n"
            "Not staged. Cannot be undone from within the app.")
        self.btn_open_porymap = QPushButton("Open in Porymap")
        self.btn_open_porymap.setToolTip(
            "READ-ONLY.\n\n"
            "Launches Porymap with the project loaded, opening the "
            "first map that uses the selected layout (if any). "
            "Porymap is a separate program — you'll need to install it "
            "via Tools → Install Porymap if you haven't.")
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
        self.primary_combo.setToolTip(
            "The primary tileset (usually shared between many maps) the "
            "selected layout will use. Picked from the project's "
            "layouts.json.")
        tileset_form.addRow("Primary Tileset:", self.primary_combo)
        self.secondary_combo = QComboBox()
        self.secondary_combo.wheelEvent = lambda e: e.ignore()
        self.secondary_combo.setPlaceholderText("Select secondary tileset...")
        self.secondary_combo.setToolTip(
            "The secondary tileset (typically the per-area look) the "
            "selected layout will use. Picked from the parsed tileset "
            "headers under data/tilesets/secondary/.")
        tileset_form.addRow("Secondary Tileset:", self.secondary_combo)
        layout_inner.addLayout(tileset_form)

        self.btn_apply_tilesets = QPushButton("Apply Tilesets to Layout")
        self.btn_apply_tilesets.setToolTip(
            "IMMEDIATE WRITE.\n\n"
            "Sets the primary and secondary tileset fields in "
            "layouts.json for the selected layout. The map will use "
            "those tilesets the next time it's opened or built.\n\n"
            "Not staged.")
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
        self.tileset_combo.setToolTip(
            "Pick the secondary tileset to rename. Format is "
            "label (folder).")
        ts_form.addRow("Secondary Tileset:", self.tileset_combo)
        tileset_inner.addLayout(ts_form)

        self.btn_rename_tileset = QPushButton("Rename Tileset")
        self.btn_rename_tileset.setToolTip(
            "IMMEDIATE WRITE.\n\n"
            "Renames the tileset's gTileset_* label (the C symbol used "
            "by layouts) and optionally the folder name under "
            "data/tilesets/secondary/. Rewrites every reference across "
            "the project source.\n\n"
            "Not staged. Cannot be undone from within the app.")
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
        new_id, ok = _prompt_text(
            self, "Rename Layout",
            f"New ID for '{old_id}':", old_id, LAYOUT_ID_MAX,
            helper="Layout id is a C symbol used across the project source "
                   "(e.g. LAYOUT_PALLET_TOWN). Confirming this dialog "
                   "WRITES TO DISK IMMEDIATELY.")
        if not ok or not new_id or new_id == old_id:
            return
        confirm = QMessageBox.warning(
            self, "Rename Layout — Confirm",
            f"Rename layout '{old_id}' → '{new_id}'?\n\n"
            f"This renames the layout folder, updates layouts.json, and "
            f"rewrites every reference to the old id across the project."
            f"{_WARN_IMMEDIATE_LAYOUT}",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        if confirm != QMessageBox.StandardButton.Ok:
            return
        try:
            self._layout_renamer.rename_layout(old_id, new_id)
            self._mw.log_message(f"Renamed layout {old_id} -> {new_id}")
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
        reply = QMessageBox.warning(
            self, "Delete Layout — Confirm",
            f"Delete layout '{lid}'?\n\n"
            f"This deletes the layout's folder under data/layouts/, removes "
            f"its entry from layouts.json, and rewrites every reference "
            f"across the project source."
            f"{_WARN_IMMEDIATE_LAYOUT}",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        if reply != QMessageBox.StandardButton.Ok:
            return
        try:
            self._layout_renamer.delete_layout(lid)
            self._mw.log_message(f"Deleted layout {lid}")
            self._populate_combos()
            self.data_changed.emit()
        except Exception as e:
            QMessageBox.critical(self, "Delete Layout", str(e))

    def _on_clean_layouts(self):
        reply = QMessageBox.warning(
            self, "Clean Orphaned Layouts — Confirm",
            "Scan layouts.json for entries whose folders are missing AND "
            "that no map references, and remove them?"
            f"{_WARN_IMMEDIATE_LAYOUT}",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        if reply != QMessageBox.StandardButton.Ok:
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
        confirm = QMessageBox.warning(
            self, "Apply Tilesets — Confirm",
            f"Set primary='{primary or '(unchanged)'}' and "
            f"secondary='{secondary or '(unchanged)'}' on layout '{lid}'?"
            f"{_WARN_IMMEDIATE_LAYOUT}",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        if confirm != QMessageBox.StandardButton.Ok:
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
        new_label, ok = _prompt_text(
            self, "Rename Tileset",
            f"New label for '{old_label}' (folder: {old_folder}):",
            old_label, TILESET_LABEL_MAX,
            helper="Tileset label is the gTileset_* C symbol layouts "
                   "reference. The 'gTileset_' prefix is added "
                   "automatically by the build — type just the suffix "
                   "(e.g. 'PalletTown'). Confirming WRITES TO DISK "
                   "IMMEDIATELY.")
        if not ok or not new_label or new_label == old_label:
            return
        new_folder, ok2 = _prompt_text(
            self, "Rename Tileset",
            f"New folder name (currently '{old_folder}'):",
            old_folder, TILESET_FOLDER_MAX,
            helper="Folder name under data/tilesets/secondary/. Leave as-is "
                   "to keep the existing folder.")
        if not ok2 or not new_folder:
            new_folder = old_folder
        confirm = QMessageBox.warning(
            self, "Rename Tileset — Confirm",
            f"Rename tileset label '{old_label}' → '{new_label}' "
            f"(folder '{old_folder}' → '{new_folder}')?"
            f"{_WARN_IMMEDIATE_LAYOUT}",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        if confirm != QMessageBox.StandardButton.Ok:
            return
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
