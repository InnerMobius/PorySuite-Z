"""
Region Map tab — Visual region map editor with clickable grid.

Renders the actual tileset graphics as a background image, overlays a
transparent clickable grid for cell selection and section assignment.

Save model:
- Cell paints are STAGED in memory (`_pending_cells`) and only flush to
  disk on Ctrl+S or the in-tab Save button. Each staged cell shows an
  amber overlay so the user can see exactly what hasn't been written.
  F5 / clear_dirty_markers() discards every pending paint.
- Region rename / clone / delete and Section rename are IMMEDIATE WRITES
  — they touch layout/tilemap files and rewrite cross-project references
  that aren't safely deferrable. Each shows a confirmation dialog with a
  do-not-undo warning.
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QBrush, QPen, QPixmap, QImage
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QComboBox, QPushButton, QLabel, QLineEdit,
    QGraphicsView, QGraphicsScene, QGraphicsRectItem,
    QGraphicsPixmapItem,
    QDialog, QDialogButtonBox,
    QInputDialog, QMessageBox,
)


# ─────────────────────────────────────────────────────────────────────────────
# Constants & shared helpers — mirror layouts_tab / maps_tab grammar
# ─────────────────────────────────────────────────────────────────────────────

_WARN_IMMEDIATE_REGION = (
    "\n\n⚠  WRITES TO DISK IMMEDIATELY when you confirm. Section rename "
    "touches every map header that references the MAPSEC. Cannot be "
    "undone from within the app.\n"
    "Make a backup or commit to Git before proceeding."
)

_NOTE_STAGED_REGION_OP = (
    "\n\nThis change is staged in memory. Press Ctrl+S (or the Save "
    "button) to write it to disk and regenerate engine code. F5 "
    "discards all staged changes."
)

# MAPSEC_* constants are C symbols — same 48-char soft cap as maps_tab.
SECTION_ID_MAX = 48
# Region names become folder/file names — keep short.
REGION_NAME_MAX = 32

# Amber overlay applied to staged (unsaved) cells.
_DIRTY_CELL_BRUSH = QBrush(QColor(255, 183, 77, 110))  # #ffb74d w/ alpha


# ─────────────────────────────────────────────────────────────────────────────
# Cross-tab navigation callback — registered by unified_mainwindow at startup.
# Same pattern event_editor_tab uses for the Sound Editor + Overworld Graphics
# nav buttons. None until the unified window wires it up.
# ─────────────────────────────────────────────────────────────────────────────

_open_tilemap_cb = None  # Callable[[str], None] — bin_path -> open in Tilemap Editor


def _attach_char_counter(line_edit: QLineEdit, counter_lbl: QLabel,
                         max_chars: int) -> None:
    """Wire a max-length cap + grey/amber/red counter — same pattern as
    maps_tab / layouts_tab / items_tab."""
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


def _sanitize_region_name_live(text: str) -> str:
    """Auto-format a region name as the user types it.

    - Lowercase.
    - Spaces → underscores.
    - Any other non-`[a-z0-9_]` character → underscore.
    - Leading underscores are kept while typing (so the user can still
      backspace through them); the manager's validate_region_name handles
      the final normalization on submit.
    """
    out = []
    for ch in text.lower():
        if ch.isalnum() or ch == "_":
            out.append(ch)
        elif ch == " " or ch == "-":
            out.append("_")
        else:
            out.append("_")
    return "".join(out)


def _prompt_text(parent, title: str, label: str, current: str,
                 max_chars: int, helper: str = "",
                 sanitizer=None) -> tuple[str, bool]:
    """Modal text-entry dialog with a live char counter. Returns (text, ok).

    `sanitizer` (optional callable str→str) is applied on every keystroke
    so the user sees exactly what will be staged. Cursor position is
    preserved by Qt because we re-set the same string when no change
    occurs and only update if the sanitizer actually changed the text.
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
    edit = QLineEdit(sanitizer(current) if sanitizer else current)
    layout.addWidget(edit)
    counter = QLabel("")
    counter.setAlignment(Qt.AlignmentFlag.AlignRight)
    layout.addWidget(counter)
    _attach_char_counter(edit, counter, max_chars)

    if sanitizer is not None:
        def _on_text_changed(text: str):
            sanitized = sanitizer(text)
            if sanitized != text:
                # Track cursor offset so user keeps typing where they were.
                pos = edit.cursorPosition()
                # Length-preserving replacement (we never delete chars,
                # just substitute), so cursor pos stays valid.
                edit.blockSignals(True)
                edit.setText(sanitized)
                edit.setCursorPosition(min(pos, len(sanitized)))
                edit.blockSignals(False)
        edit.textChanged.connect(_on_text_changed)

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


# ─────────────────────────────────────────────────────────────────────────────
# RegionMapView — matches TriforceGUI's RegionMapView exactly
# ─────────────────────────────────────────────────────────────────────────────

class RegionMapView(QGraphicsView):
    """QGraphicsView that displays the tileset image with a clickable
    transparent grid overlay. Cell sizes are derived from the pixmap,
    not hardcoded."""

    cellClicked = pyqtSignal(int, int)   # (grid_x, grid_y)
    cellHovered = pyqtSignal(int, int)   # (grid_x, grid_y) — -1, -1 when off-grid

    def __init__(self, pixmap: QPixmap, grid, offset=(0, 0), parent=None):
        super().__init__(parent)
        self.pixmap = pixmap
        self.grid = grid
        self.width = len(grid[0]) if grid else 0
        self.height = len(grid) if grid else 0
        self.offset_x, self.offset_y = offset
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.cell_items = {}
        self.selected_rect = None
        self._last_hover = (-2, -2)  # sentinel — emit on first mouseMove
        self.setMouseTracking(True)
        self.build_scene()
        self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def build_scene(self):
        self._scene.clear()
        self.cell_items.clear()

        # Background — the actual rendered tileset image
        self.base_item = QGraphicsPixmapItem(self.pixmap)
        self._scene.addItem(self.base_item)

        # Cell dimensions derived from the pixmap (30 tiles wide, 20 tiles tall)
        tile_w = self.pixmap.width() / 30
        tile_h = self.pixmap.height() / 20
        cell_w = tile_w
        cell_h = tile_h

        # Transparent grid overlay
        for y in range(self.height):
            for x in range(self.width):
                rect = QGraphicsRectItem(
                    (x + self.offset_x) * cell_w,
                    (y + self.offset_y) * cell_h,
                    cell_w, cell_h)
                pen = QPen(QColor(0, 0, 0, 50))
                pen.setCosmetic(True)
                rect.setPen(pen)
                rect.setBrush(QBrush(Qt.BrushStyle.NoBrush))
                self._scene.addItem(rect)
                self.cell_items[(x, y)] = rect

        # Selection rectangle — red border, no fill
        self.selected_rect = QGraphicsRectItem()
        self.selected_rect.setPen(QPen(Qt.GlobalColor.red, 2))
        self.selected_rect.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        self._scene.addItem(self.selected_rect)

    def select_cell(self, x, y):
        tile_w = self.pixmap.width() / 30
        tile_h = self.pixmap.height() / 20
        self.selected_rect.setRect(
            (x + self.offset_x) * tile_w,
            (y + self.offset_y) * tile_h,
            tile_w, tile_h)

    def set_cell_dirty(self, x, y, dirty: bool):
        """Tint a cell amber if it has staged (unsaved) edits, clear if not."""
        item = self.cell_items.get((x, y))
        if not item:
            return
        if dirty:
            item.setBrush(_DIRTY_CELL_BRUSH)
        else:
            item.setBrush(QBrush(Qt.BrushStyle.NoBrush))

    def clear_all_dirty(self):
        """Wipe every amber overlay — used after Save or F5."""
        for item in self.cell_items.values():
            item.setBrush(QBrush(Qt.BrushStyle.NoBrush))

    def mousePressEvent(self, event):
        pos = self.mapToScene(event.position().toPoint())
        tile_w = self.pixmap.width() / 30
        tile_h = self.pixmap.height() / 20
        x = int(pos.x() / tile_w - self.offset_x)
        y = int(pos.y() / tile_h - self.offset_y)
        if 0 <= x < self.width and 0 <= y < self.height:
            self.select_cell(x, y)
            self.cellClicked.emit(x, y)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        pos = self.mapToScene(event.position().toPoint())
        tile_w = self.pixmap.width() / 30
        tile_h = self.pixmap.height() / 20
        x = int(pos.x() / tile_w - self.offset_x)
        y = int(pos.y() / tile_h - self.offset_y)
        in_bounds = 0 <= x < self.width and 0 <= y < self.height
        new_hover = (x, y) if in_bounds else (-1, -1)
        if new_hover != self._last_hover:
            self._last_hover = new_hover
            self.cellHovered.emit(*new_hover)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if self._last_hover != (-1, -1):
            self._last_hover = (-1, -1)
            self.cellHovered.emit(-1, -1)
        super().leaveEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)


# ─────────────────────────────────────────────────────────────────────────────
# RegionMapTab
# ─────────────────────────────────────────────────────────────────────────────

class RegionMapTab(QWidget):
    data_changed = pyqtSignal()

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self._mw = main_window
        self.project_info = None
        self._manager = None
        self._grid = None
        self._grid_w = 0
        self._grid_h = 0
        self._map_view = None     # RegionMapView instance (replaces QGraphicsView)

        # ── Staging ────────────────────────────────────────────────────────
        # _pending_cells[(region, layer, x, y)] = section_id or None
        # Region is in the key so switching regions doesn't smear paints
        # from one region onto another. Cleared on save() success or
        # clear_dirty_markers().
        self._pending_cells: dict = {}
        # Layers that have actually been mutated and need a flush on save().
        # Tracked as (region, layer) tuples for the same reason.
        self._dirty_layers: set = set()
        # First-time engine-edit warning is fired ONCE per session, before
        # the user stages their first region op (Create / Clone / Rename /
        # Delete) — NOT at save time, so they can still back out and back
        # up before doing the work.
        self._first_engine_warning_acked = False

        self._build_ui()

    def _build_ui(self):
        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(8, 8, 8, 8)

        # ── Header banner — explains save model ─────────────────────────────
        banner = QLabel(
            "<small>"
            "Click a cell on the map and pick a section to assign it. "
            "<b>All edits are staged</b> — cell paints, region creates, "
            "renames, clones and deletes all queue in memory until you "
            "save (Ctrl+S or the Save button). F5 discards staged "
            "edits.<br><br>"
            "<b>Section rename "
            "<span style='color:#f99'>writes to disk immediately</span></b> "
            "(rewrites every map header that references the MAPSEC). "
            "Back up or commit before using it."
            "</small>")
        banner.setWordWrap(True)
        banner.setStyleSheet("padding: 4px; color: #ccc;")
        self._root_layout.addWidget(banner)

        # ── Region selector + Open in Tilemap Editor ────────────────────────
        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("Region:"))
        self.region_combo = QComboBox()
        self.region_combo.wheelEvent = lambda e: e.ignore()
        self.region_combo.setPlaceholderText("Select a region...")
        self.region_combo.setToolTip(
            "Pick which region's map to display. Switching regions while "
            "you have unsaved cell paints will discard them — save first.")
        self.region_combo.currentTextChanged.connect(self._on_region_changed)
        top_row.addWidget(self.region_combo, 1)

        self.btn_open_tilemap = QPushButton("Open in Tilemap Editor")
        self.btn_open_tilemap.setToolTip(
            "Open this region's tilemap (.bin) in the Tilemap Editor "
            "tab so you can paint the actual map artwork.")
        self.btn_open_tilemap.setEnabled(False)
        self.btn_open_tilemap.clicked.connect(self._on_open_in_tilemap_editor)
        top_row.addWidget(self.btn_open_tilemap)

        self._root_layout.addLayout(top_row)
        # The dungeon layer still exists on disk and the engine still uses
        # it (the player-region lookup walks both layers via the manager),
        # but per the roadmap we don't expose a UI toggle — most hacks
        # treat each dungeon as its own region instead of layering them
        # under an overworld region.
        self._current_layer = 'map'

        # ── Map view placeholder — replaced with RegionMapView on load ───────
        self._view_placeholder = QLabel("Load a project to see the region map.")
        self._view_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._view_placeholder.setMinimumHeight(300)
        self._view_placeholder.setObjectName('regionPlaceholder')
        self._view_placeholder.setStyleSheet(
            "#regionPlaceholder { color: palette(dark); background: palette(base); }")
        self._root_layout.addWidget(self._view_placeholder, 1)

        # ── Section info / assignment ────────────────────────────────────────
        section_row = QHBoxLayout()
        section_row.addWidget(QLabel("Section:"))
        self.section_combo = QComboBox()
        self.section_combo.wheelEvent = lambda e: e.ignore()
        self.section_combo.setPlaceholderText("Select a section...")
        # Strict dropdown — only MAPSECs from region_map_sections.json are
        # selectable. Free-typing risks broken MAPSEC constants in the grid.
        self.section_combo.setEditable(False)
        # Cap visible width so the row doesn't sprawl across the page.
        # ~280 px ≈ 40 chars at a normal font; the dropdown popup itself
        # opens wider and shows the full names.
        self.section_combo.setMaximumWidth(280)
        self.section_combo.setMinimumWidth(220)
        self.section_combo.setToolTip(
            "MAPSEC_* constant to assign to the currently-selected cell. "
            "Pick a value to immediately stage the assignment; the cell "
            "turns amber. (none) clears the cell.")
        # Auto-stage on selection change — no separate Assign button needed.
        self.section_combo.currentIndexChanged.connect(
            self._on_section_combo_changed)
        section_row.addWidget(self.section_combo)
        section_row.addStretch(1)
        self._root_layout.addLayout(section_row)

        # ── Hover tooltip line — shows which cell the cursor is over ─────────
        self._hover_lbl = QLabel("")
        self._hover_lbl.setStyleSheet(
            "color: #aaa; font-size: 11px; font-family: 'Courier New'; "
            "padding-left: 4px;")
        self._hover_lbl.setMinimumHeight(16)
        self._root_layout.addWidget(self._hover_lbl)

        # ── Status line — staged-edit count ──────────────────────────────────
        # Style is updated in _update_status() — grey when empty, bold amber
        # when there's pending content so the user can't miss it.
        self._status_lbl = QLabel("")
        self._status_lbl.setMinimumHeight(22)
        self._status_lbl.setWordWrap(True)
        self._root_layout.addWidget(self._status_lbl)

        # ── Action buttons ───────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self.btn_save = QPushButton("Save")
        self.btn_save.setToolTip(
            "Flush all staged edits to disk: region creates/clones/renames/"
            "deletes apply, engine code is regenerated, then cell paints "
            "are written. Same effect as the toolbar Save All / Ctrl+S.")
        self.btn_reload = QPushButton("Reload")
        self.btn_reload.setToolTip(
            "Re-read everything from disk and discard ALL staged edits "
            "(both cell paints and region ops). Quicker than F5.")
        self.btn_create = QPushButton("Create New Region")
        self.btn_create.setToolTip(
            "STAGED.\n\n"
            "Creates a new empty region (blank map, blank tilemap). "
            "Engine code is regenerated on save.")
        self.btn_clone = QPushButton("Clone Region")
        self.btn_clone.setToolTip(
            "STAGED.\n\n"
            "Queues a clone of the current region's TILEMAP ARTWORK "
            "(.bin) under a new name. The new region's MAPSEC grid "
            "starts BLANK — you paint the cells you want it to own.\n\n"
            "(Two regions can't both claim the same MAPSEC; the engine "
            "would silently route in-game maps to the slot that comes "
            "later in the enum.)")
        self.btn_rename_region = QPushButton("Rename Region")
        self.btn_rename_region.setToolTip(
            "STAGED.\n\n"
            "Queues a rename of the current region's folder + files. "
            "On save, files rename and engine code regenerates.")
        self.btn_rename_section = QPushButton("Rename Section")
        self.btn_rename_section.setToolTip(
            "IMMEDIATE WRITE.\n\n"
            "Renames a MAPSEC_* constant across region_map_sections.json "
            "and every map header that references it. Cannot be undone "
            "from within the app.")
        self.btn_delete_region = QPushButton("Delete Region")
        self.btn_delete_region.setToolTip(
            "STAGED.\n\n"
            "Queues deletion of the current region's layout and tilemap "
            "files. Refuses to run if it would leave zero regions. "
            "Applied on save.")

        for btn in (self.btn_save, self.btn_reload, self.btn_create,
                    self.btn_clone, self.btn_rename_region,
                    self.btn_rename_section, self.btn_delete_region):
            btn.setEnabled(False)
            btn_row.addWidget(btn)

        self._root_layout.addLayout(btn_row)

        # ── Button connections ───────────────────────────────────────────────
        self.btn_save.clicked.connect(self._on_save)
        self.btn_reload.clicked.connect(self._on_reload)
        self.btn_create.clicked.connect(self._on_create_region)
        self.btn_clone.clicked.connect(self._on_clone_region)
        self.btn_rename_region.clicked.connect(self._on_rename_region)
        self.btn_rename_section.clicked.connect(self._on_rename_section)
        self.btn_delete_region.clicked.connect(self._on_delete_region)

    # Track which cell is selected — (grid_x, grid_y)
    _selected_cell = None

    # ─────────────────────────────────────────────────────────────────────────
    # Project loading
    # ─────────────────────────────────────────────────────────────────────────

    def load_project(self, project_info: dict):
        self.project_info = project_info
        project_dir = project_info.get("dir", "")

        # Discard any in-flight staged edits — F5 / project switch must
        # leave the tab matching what's on disk, not what the user was
        # halfway through editing. (Pending region ops live on the manager
        # and get cleared automatically when we re-instantiate it below.)
        self._pending_cells.clear()
        self._dirty_layers.clear()
        # New project / fresh F5 — re-arm the first-time-edit warning so the
        # user sees it again if they're about to touch a fresh vanilla file.
        self._first_engine_warning_acked = False

        from eventide.backend.region_map_manager import RegionMapManager
        try:
            self._manager = RegionMapManager(project_dir)
        except Exception as e:
            self._mw.log_message(f"Region Map tab: failed to load backend: {e}")
            return

        for btn in (self.btn_save, self.btn_reload, self.btn_create,
                    self.btn_clone, self.btn_rename_region,
                    self.btn_rename_section, self.btn_delete_region,
                    self.btn_open_tilemap):
            btn.setEnabled(True)

        self._populate_regions()
        self._update_status()
        self._update_hover_label(-1, -1)
        self._mw.log_message(f"Region Map tab: loaded {project_dir}")

    # ─────────────────────────────────────────────────────────────────────────
    # Dirty / save contract — called by unified_mainwindow
    # ─────────────────────────────────────────────────────────────────────────

    def has_unsaved_changes(self) -> bool:
        if self._pending_cells:
            return True
        if self._manager and self._manager.has_pending_region_ops():
            return True
        return False

    def save(self) -> bool:
        """Two-phase flush:
          1. Apply staged region ops (creates/clones/renames/deletes) — this
             rewrites engine code in src/region_map.c via codegen.
          2. Flush staged cell paints for every dirty (region, layer).
        Returns True on success (or no-op), False if anything failed —
        pending state is preserved so the user can retry.

        Surfaces a confirmation dialog before any engine codegen runs.
        Surfaces an additional one-time warning the first time markers are
        inserted into a vanilla project's src/region_map.c.
        """
        if not self._manager:
            return True

        ok = True
        cur_region = self._manager.region

        # Phase 1 — region ops.
        if self._manager.has_pending_region_ops():
            # The first-time-marker warning is fired in
            # _confirm_first_engine_edit() at staging time (before the user
            # sets up region ops), NOT here. By save time the user's
            # already done the work — telling them to back up at that
            # point is useless.

            # Engine-codegen confirmation — describes what will change.
            summary = self._manager.pending_region_ops_summary()
            warn_msg = (
                "<b>Engine code regeneration</b><br><br>"
                f"Saving will regenerate <code>src/region_map.c</code> "
                f"to reflect: <b>{summary}</b>.<br><br>"
                "<b>Make sure your project is committed to git or "
                "backed up</b> — there is no automatic backup. Re-run "
                "<code>make</code> after to rebuild the ROM.<br><br>"
                "Continue?"
            )
            confirm = QMessageBox.warning(
                self, "Engine code regeneration", warn_msg,
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel)
            if confirm != QMessageBox.StandardButton.Save:
                return False

            try:
                self._manager.flush_pending_region_ops()
                self._mw.log_message(
                    "Region Map: applied staged region ops + regenerated engine code")
            except Exception as e:
                ok = False
                self._mw.log_message(
                    f"Region Map: region ops flush failed: {e}")
                # Don't proceed to cell-paint flush — the engine state may be
                # inconsistent and grid saves could compound the problem.
                return False

        # Phase 2 — cell paints.
        for (region, layer) in list(self._dirty_layers):
            try:
                if region == cur_region and layer == self._current_layer and self._grid is not None:
                    grid_to_save = self._grid
                else:
                    self._manager.set_region(region)
                    grid_to_save, _, _ = self._manager.load_grid()
                    for (rg, lyr, x, y), sid in self._pending_cells.items():
                        if rg != region or lyr != layer:
                            continue
                        if 0 <= y < len(grid_to_save) and 0 <= x < len(grid_to_save[y]):
                            grid_to_save[y][x] = sid
                self._manager.save_grid(grid_to_save, layer=layer)
                self._mw.log_message(
                    f"Region Map: saved {region} cell paints")
            except Exception as e:
                ok = False
                self._mw.log_message(
                    f"Region Map: cell-paint save failed for {region}: {e}")

        # Restore the manager's current region so the visible view still
        # matches what the user was looking at.
        try:
            if self._manager.region != cur_region:
                # Region might have been deleted/renamed; pick a sensible fallback.
                regions = self._manager.list_regions()
                if cur_region in regions:
                    self._manager.set_region(cur_region)
                elif regions:
                    self._manager.set_region(regions[0])
        except Exception:
            pass

        if ok:
            self._pending_cells.clear()
            self._dirty_layers.clear()
            if self._map_view:
                self._map_view.clear_all_dirty()
            # Refresh region picker — created/cloned regions now exist on disk;
            # deleted ones are gone; renamed ones have new names.
            self._populate_regions()
            self._update_status()
        return ok

    def clear_dirty_markers(self) -> None:
        """Discard staged paints AND staged region ops, then refresh from
        disk. Called from F5 / Save-All success."""
        self._pending_cells.clear()
        self._dirty_layers.clear()
        if self._manager:
            self._manager.discard_pending_region_ops()
        if self._map_view:
            self._map_view.clear_all_dirty()
        try:
            self._load_grid()
        except Exception:
            pass
        self._update_status()

    _STATUS_SS_IDLE = (
        "color: #888; font-size: 11px; padding: 2px 6px;"
    )
    _STATUS_SS_PENDING = (
        "color: #1a1a1a; background: #ffb74d; font-weight: bold; "
        "font-size: 12px; padding: 4px 8px; border-radius: 3px;"
    )

    def _update_status(self):
        n = len(self._pending_cells)
        region_ops_summary = ""
        if self._manager and self._manager.has_pending_region_ops():
            region_ops_summary = self._manager.pending_region_ops_summary()

        if n == 0 and not region_ops_summary:
            self._status_lbl.setStyleSheet(self._STATUS_SS_IDLE)
            self._status_lbl.setText("No staged edits.")
            return

        bits = []
        if region_ops_summary:
            bits.append(f"region ops: {region_ops_summary}")
        if n:
            regions = sorted({r for (r, _l) in self._dirty_layers})
            bits.append(f"{n} cell paint(s) on {', '.join(regions)}")
        self._status_lbl.setStyleSheet(self._STATUS_SS_PENDING)
        self._status_lbl.setText(
            f"⚠ STAGED — {'; '.join(bits)}. "
            f"Save (Ctrl+S) to write, F5 to discard.")

    def _populate_regions(self):
        self.region_combo.blockSignals(True)
        self.region_combo.clear()
        if self._manager:
            for region in self._manager.list_regions():
                self.region_combo.addItem(region)
            current = self._manager.region
            idx = self.region_combo.findText(current)
            if idx >= 0:
                self.region_combo.setCurrentIndex(idx)
        self.region_combo.blockSignals(False)
        self._load_grid()

    def _on_region_changed(self, text):
        if self._manager and text:
            self._manager.set_region(text)
            self._load_grid()

    # ─────────────────────────────────────────────────────────────────────────
    # Grid rendering — tileset image + transparent overlay, matching TriforceGUI
    # ─────────────────────────────────────────────────────────────────────────

    def _load_grid(self):
        self._selected_cell = None
        if not self._manager:
            return
        try:
            self._grid, self._grid_w, self._grid_h = self._manager.load_grid()
        except Exception as e:
            self._mw.log_message(f"Region Map tab: failed to load grid: {e}")
            return

        # Populate section combo from the SECTIONS JSON, not from the grid.
        # Sections defined in JSON but not currently placed must still appear
        # so the user can assign them; sections placed on the current grid
        # but missing from JSON also show (dropped silently elsewhere would
        # surprise the user).
        json_sections = set()
        if isinstance(self._manager.sections, dict):
            for entry in self._manager.sections.get('map_sections', []):
                sid = entry.get('id')
                if sid:
                    json_sections.add(sid)
        grid_sections = {cell for row in self._grid for cell in row if cell}
        all_sections = sorted(json_sections | grid_sections)
        self._suppress_combo_auto = True
        try:
            self.section_combo.clear()
            self.section_combo.addItem("(none)")
            for sid in all_sections:
                self.section_combo.addItem(sid)
        finally:
            self._suppress_combo_auto = False

        # Build the tileset pixmap
        pixmap = self._load_pixmap()
        if pixmap is None or pixmap.isNull():
            self._mw.log_message("Region Map tab: could not render tileset image")
            return

        # Get offset
        try:
            offset = self._manager.get_tile_offset()
        except Exception:
            offset = (0, 0)

        # Remove old view / placeholder
        if self._map_view:
            self._root_layout.removeWidget(self._map_view)
            self._map_view.deleteLater()
            self._map_view = None
        if self._view_placeholder:
            self._root_layout.removeWidget(self._view_placeholder)
            self._view_placeholder.deleteLater()
            self._view_placeholder = None

        # Create the real map view
        self._map_view = RegionMapView(pixmap, self._grid, offset)
        self._map_view.setMinimumHeight(300)
        self._map_view.cellClicked.connect(self._on_cell_clicked)
        self._map_view.cellHovered.connect(self._update_hover_label)

        # Re-apply staged amber overlays for this (region, layer) — the
        # rebuild wipes overlays and the disk read wipes painted values.
        cur_region = self._manager.region if self._manager else ""
        for (rg, lyr, x, y), sid in self._pending_cells.items():
            if rg != cur_region or lyr != self._current_layer:
                continue
            if 0 <= y < len(self._grid) and 0 <= x < len(self._grid[y]):
                self._grid[y][x] = sid
            self._map_view.set_cell_dirty(x, y, True)

        # Insert at position 2 (after banner [0] + region selector row [1]).
        self._root_layout.insertWidget(2, self._map_view, 1)

    def _load_pixmap(self) -> QPixmap:
        """Build the region map image via the backend and return a QPixmap."""
        try:
            img = self._manager.build_region_map_image()
            if isinstance(img, QImage):
                return QPixmap.fromImage(img)
            # PIL Image
            try:
                from PIL import Image as _PIL_Image
                if isinstance(img, _PIL_Image.Image):
                    img = img.convert("RGBA")
                    data = img.tobytes("raw", "RGBA")
                    qimg = QImage(data, img.width, img.height,
                                  4 * img.width,
                                  QImage.Format.Format_RGBA8888)
                    return QPixmap.fromImage(qimg.copy())
            except ImportError:
                pass
        except Exception as e:
            self._mw.log_message(f"Region Map: image render error: {e}")
        return QPixmap()

    def _on_cell_clicked(self, grid_x, grid_y):
        self._selected_cell = (grid_x, grid_y)
        cell_value = None
        if self._grid and 0 <= grid_y < len(self._grid) and 0 <= grid_x < len(self._grid[grid_y]):
            cell_value = self._grid[grid_y][grid_x]
        # Sync combo to the cell's current value WITHOUT firing auto-stage
        # — clicking a cell is just inspect, not edit.
        self._suppress_combo_auto = True
        try:
            target = "(none)" if not cell_value else cell_value
            idx = self.section_combo.findText(target)
            if idx >= 0:
                self.section_combo.setCurrentIndex(idx)
        finally:
            self._suppress_combo_auto = False

    def on_file_saved(self, saved_path: str) -> None:
        """App-wide hook — fires whenever any tab writes a file via the
        unified mainwindow's `file_saved` broadcast. Cheap-by-default:
        returns immediately unless the saved path is the current region's
        tilemap. Adding more "files I care about" checks here is fine —
        keep them O(few)."""
        if not self._manager or not saved_path:
            return
        import os
        try:
            current_bin = self._manager._tilemap_path()
            same = os.path.normcase(os.path.abspath(saved_path)) == \
                   os.path.normcase(os.path.abspath(current_bin))
        except Exception:
            return
        if not same:
            return
        # Rebuild the canvas + pixmap; re-applies amber overlays.
        self._load_grid()
        self._mw.log_message(
            f"Region Map: refreshed background after external save "
            f"({os.path.basename(saved_path)})")

    def _update_hover_label(self, x: int, y: int):
        """Update the hover status line below the map."""
        if x < 0 or y < 0 or not self._grid:
            self._hover_lbl.setText("")
            return
        cell = None
        if 0 <= y < len(self._grid) and 0 <= x < len(self._grid[y]):
            cell = self._grid[y][x]
        label = cell or "(empty)"
        self._hover_lbl.setText(f"({x:>2}, {y:>2})  {label}")

    def _on_open_in_tilemap_editor(self):
        """Cross-tab nav — switch to Tilemap Editor with this region's .bin loaded."""
        if not self._manager:
            return
        import os
        bin_path = self._manager._tilemap_path()
        if not os.path.exists(bin_path):
            QMessageBox.warning(
                self, "Open in Tilemap Editor",
                f"Tilemap file not found:\n{bin_path}\n\n"
                "Save this region first if it was just created.")
            return
        if _open_tilemap_cb is None:
            QMessageBox.warning(
                self, "Open in Tilemap Editor",
                "Tilemap Editor cross-tab navigation isn't wired up in "
                "this build of PorySuite-Z.")
            return
        _open_tilemap_cb(bin_path)

    # ─────────────────────────────────────────────────────────────────────────
    # Actions
    # ─────────────────────────────────────────────────────────────────────────

    # Suppress combo-driven auto-stage while we set the combo programmatically
    # (cell click, _load_grid populate, etc.).
    _suppress_combo_auto = False

    def _on_section_combo_changed(self, _idx: int):
        """Auto-stage assignment when the user picks a section from the
        dropdown. No separate Assign button — selecting IS the action.
        """
        if self._suppress_combo_auto:
            return
        self._stage_section_assignment()

    def _stage_section_assignment(self):
        """Apply the current combo value to the currently-selected cell,
        stage the paint, light the amber overlay."""
        if not self._selected_cell or not self._grid:
            QMessageBox.information(
                self, "Assign Section", "Click a cell on the grid first.")
            return
        grid_x, grid_y = self._selected_cell
        section_text = self.section_combo.currentText().strip()
        section = None if section_text == "(none)" or not section_text else section_text

        # No-op if the cell is already that value (avoids redundant amber +
        # status updates when the combo's set programmatically by a click).
        existing = self._grid[grid_y][grid_x]
        if existing == section:
            return

        # Apply to in-memory grid (currently-displayed layer only).
        self._grid[grid_y][grid_x] = section

        # Stage the paint and flag the (region, layer) dirty.
        region = self._manager.region if self._manager else ""
        key = (region, self._current_layer, grid_x, grid_y)
        self._pending_cells[key] = section
        self._dirty_layers.add((region, self._current_layer))

        # Visual amber overlay.
        if self._map_view:
            self._map_view.set_cell_dirty(grid_x, grid_y, True)

        self._update_status()
        self.data_changed.emit()
        self._mw.log_message(
            f"Region Map: staged ({grid_x}, {grid_y}) "
            f"= {section or '(none)'}")

    def _on_save(self):
        if not self._manager:
            return
        if not self._dirty_layers:
            QMessageBox.information(self, "Save", "Nothing to save — no staged edits.")
            return
        if self.save():
            QMessageBox.information(self, "Save", "Region map saved successfully.")
        else:
            QMessageBox.critical(
                self, "Save",
                "One or more layers failed to save. Staged edits kept — "
                "see the log for details.")

    def _on_reload(self):
        if self.has_unsaved_changes():
            n_paints = len(self._pending_cells)
            n_ops = (len(self._manager._pending_creates)
                     + len(self._manager._pending_clones)
                     + len(self._manager._pending_renames)
                     + len(self._manager._pending_deletes)) if self._manager else 0
            details = []
            if n_paints:
                details.append(f"{n_paints} cell paint(s)")
            if n_ops:
                details.append(f"{n_ops} region op(s)")
            reply = QMessageBox.warning(
                self, "Reload",
                f"You have {' and '.join(details)} staged that will be "
                f"discarded. Continue?",
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
            if reply != QMessageBox.StandardButton.Ok:
                return
        self._pending_cells.clear()
        self._dirty_layers.clear()
        if self._manager:
            self._manager.discard_pending_region_ops()
            if self._map_view:
                self._map_view.clear_all_dirty()
            try:
                self._manager.sections = self._manager.load_sections()
                self._manager._load_all_layouts()
                self._manager._engine_state = self._manager._parse_engine_state()
            except Exception as e:
                QMessageBox.critical(self, "Reload", str(e))
                return
        self._populate_regions()
        self._update_status()
        self._mw.log_message("Region Map: reloaded from disk")

    # ── Staged region ops ────────────────────────────────────────────────────

    def _confirm_first_engine_edit(self) -> bool:
        """Fire ONCE per session, before the user's first staging op that
        will eventually trigger engine codegen. Lets them back out and
        back up their project BEFORE doing the work — catching this at
        save time was useless because the user had already invested in
        the change.
        Returns True to proceed, False to cancel. No-op (returns True) if
        the warning was already acked this session, or if markers already
        exist in src/region_map.c (project's already been edited)."""
        if self._first_engine_warning_acked:
            return True
        if not self._manager or not self._manager.is_first_engine_codegen():
            self._first_engine_warning_acked = True
            return True
        msg = (
            "<b>First-time engine edit</b><br><br>"
            "This is the first time PorySuite will edit "
            "<code>src/region_map.c</code> for this project. When you "
            "save, comment markers (<code>// PORYSUITE-REGIONS-START / "
            "END</code>) will be inserted around the engine blocks "
            "PorySuite manages from now on. Code outside the markers "
            "will never be touched.<br><br>"
            "<b>Back up your project (git commit) NOW before staging "
            "any region changes if you haven't already.</b><br><br>"
            "Click OK to continue staging the change. You can still "
            "cancel before saving with F5."
        )
        reply = QMessageBox.warning(
            self, "First-time engine edit", msg,
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        if reply != QMessageBox.StandardButton.Ok:
            return False
        self._first_engine_warning_acked = True
        return True

    def _rewrite_pending_paints_for_rename(self, old: str, new: str) -> None:
        """A region rename invalidates any pending cell-paint keys that
        reference the old name. Rewrite them in place so the paints flush
        to the renamed region on save."""
        if old == new:
            return
        new_pending = {}
        for (rg, lyr, x, y), sid in self._pending_cells.items():
            if rg == old:
                new_pending[(new, lyr, x, y)] = sid
            else:
                new_pending[(rg, lyr, x, y)] = sid
        self._pending_cells = new_pending
        new_dirty = set()
        for (rg, lyr) in self._dirty_layers:
            new_dirty.add((new if rg == old else rg, lyr))
        self._dirty_layers = new_dirty

    def _drop_pending_paints_for_delete(self, name: str) -> None:
        """A region delete drops any pending paints on that region."""
        self._pending_cells = {
            k: v for k, v in self._pending_cells.items() if k[0] != name
        }
        self._dirty_layers = {
            (rg, lyr) for (rg, lyr) in self._dirty_layers if rg != name
        }

    def _on_create_region(self):
        if not self._manager:
            return
        if not self._confirm_first_engine_edit():
            return
        name, ok = _prompt_text(
            self, "Create New Region", "Name for new region:", "",
            REGION_NAME_MAX,
            helper="Creates a new empty region (blank map, blank tilemap). "
                   "Engine code is regenerated on save. "
                   "Spaces become underscores; uppercase becomes lowercase.",
            sanitizer=_sanitize_region_name_live)
        if not ok or not name:
            return
        try:
            clean = self._manager.stage_create_region(name)
        except Exception as e:
            QMessageBox.critical(self, "Create Region", str(e))
            return
        self._mw.log_message(f"Region Map: staged create region '{clean}'")
        self._update_status()
        self.data_changed.emit()
        QMessageBox.information(
            self, "Create Region",
            f"Region '{clean}' staged for creation.{_NOTE_STAGED_REGION_OP}")

    def _on_clone_region(self):
        if not self._manager:
            return
        if not self._confirm_first_engine_edit():
            return
        current = self.region_combo.currentText()
        if not current:
            return
        name, ok = _prompt_text(
            self, "Clone Region", f"Name for clone of '{current}':", "",
            REGION_NAME_MAX,
            helper=(
                "Copies the region's TILEMAP ARTWORK (.bin) under a new "
                "name. The MAPSEC grid starts BLANK — you paint the "
                "MAPSECs you want this region to own. Engine code is "
                "regenerated on save.\n\n"
                "Tip: if you want a fully empty region (blank artwork "
                "AND blank grid), use Create New Region instead. If you "
                "want the source region's exact MAPSEC layout, you'd "
                "have to paint it manually here — but two regions "
                "claiming the same MAPSECs conflict (the later slot "
                "wins in-game), so usually you don't.\n\n"
                "Spaces become underscores; uppercase becomes lowercase."),
            sanitizer=_sanitize_region_name_live)
        if not ok or not name:
            return
        try:
            clean = self._manager.stage_clone_region(current, name)
        except Exception as e:
            QMessageBox.critical(self, "Clone Region", str(e))
            return
        self._mw.log_message(f"Region Map: staged clone {current} -> {clean}")
        self._update_status()
        self.data_changed.emit()
        QMessageBox.information(
            self, "Clone Region",
            f"Clone of '{current}' as '{clean}' staged.{_NOTE_STAGED_REGION_OP}")

    def _on_rename_region(self):
        if not self._manager:
            return
        if not self._confirm_first_engine_edit():
            return
        current = self.region_combo.currentText()
        if not current:
            return
        new_name, ok = _prompt_text(
            self, "Rename Region", f"New name for '{current}':", current,
            REGION_NAME_MAX,
            helper="Queues a rename of the region's folder/files. "
                   "Engine code is regenerated on save. Spaces become "
                   "underscores; uppercase becomes lowercase.",
            sanitizer=_sanitize_region_name_live)
        if not ok or not new_name or new_name == current:
            return

        # Same scan as delete — external references won't auto-rewrite.
        try:
            external_refs = self._manager.find_external_region_references(current)
        except Exception:
            external_refs = []
        if external_refs:
            old_const = f"REGIONMAP_{current.replace('_', '').upper()}"
            new_const = f"REGIONMAP_{new_name.replace('_', '').upper()}"
            lines = [
                f"<b>⚠ {len(external_refs)} external reference(s) to "
                f"<code>{old_const}</code> found outside "
                f"<code>src/region_map.c</code>:</b>",
                "",
            ]
            for path, lineno, line in external_refs[:10]:
                snippet = line if len(line) <= 80 else line[:80] + "…"
                lines.append(
                    f"&nbsp;&nbsp;• <code>{path}:{lineno}</code> &nbsp; "
                    f"<small style='color:#aaa;'>{snippet}</small>"
                )
            if len(external_refs) > 10:
                lines.append(
                    f"&nbsp;&nbsp;• <i>… and {len(external_refs) - 10} more</i>"
                )
            lines.append("")
            lines.append(
                f"<b>The rename will NOT update these references.</b> The "
                f"build will fail until you change <code>{old_const}</code> "
                f"to <code>{new_const}</code> in each file manually."
            )
            lines.append("")
            lines.append("Stage the rename anyway?")
            confirm = QMessageBox.warning(
                self, "Rename Region — External References",
                "<br>".join(lines),
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
            if confirm != QMessageBox.StandardButton.Ok:
                return

        try:
            clean = self._manager.stage_rename_region(current, new_name)
        except Exception as e:
            QMessageBox.critical(self, "Rename Region", str(e))
            return
        # Pending paints on the old name need to follow the rename.
        self._rewrite_pending_paints_for_rename(current, clean)
        self._mw.log_message(f"Region Map: staged rename {current} -> {clean}")
        self._update_status()
        self.data_changed.emit()
        QMessageBox.information(
            self, "Rename Region",
            f"Rename '{current}' → '{clean}' staged.{_NOTE_STAGED_REGION_OP}")

    def _on_rename_section(self):
        # Section rename is the ONE remaining immediate-write op — it
        # rewrites every map header file that references the MAPSEC, which
        # touches dozens of unrelated files. Staging that across an entire
        # project is a much larger feature than this phase covers.
        if not self._manager:
            return
        if self._pending_cells:
            reply = QMessageBox.warning(
                self, "Rename Section",
                f"You have {len(self._pending_cells)} staged cell paint(s). "
                f"Section rename writes immediately and may invalidate "
                f"those paints if their MAPSECs change. Continue?",
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
            if reply != QMessageBox.StandardButton.Ok:
                return
        sections = self._manager.get_section_ids()
        if not sections:
            QMessageBox.information(self, "Rename Section", "No sections found.")
            return
        old_id, ok = QInputDialog.getItem(
            self, "Rename Section", "Section to rename:", sections, editable=False)
        if not ok:
            return
        new_id, ok2 = _prompt_text(
            self, "Rename Section",
            f"New ID for '{old_id}':", old_id, SECTION_ID_MAX,
            helper="Renames a MAPSEC_* constant across "
                   "region_map_sections.json and every map header that "
                   "references it. Confirming WRITES TO DISK IMMEDIATELY.")
        if not ok2 or not new_id or new_id == old_id:
            return
        confirm = QMessageBox.warning(
            self, "Rename Section — Confirm",
            f"Rename section '{old_id}' → '{new_id}' across the entire "
            f"project?{_WARN_IMMEDIATE_REGION}",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        if confirm != QMessageBox.StandardButton.Ok:
            return
        try:
            self._manager.rename_section(old_id, new_id)
            self._mw.log_message(f"Region Map: renamed section {old_id} -> {new_id}")
            self._load_grid()
            self.data_changed.emit()
        except Exception as e:
            QMessageBox.critical(self, "Rename Section", str(e))

    def _on_delete_region(self):
        if not self._manager:
            return
        if not self._confirm_first_engine_edit():
            return
        current = self.region_combo.currentText()
        if not current:
            return
        # Refuse if it would leave zero regions (committed minus pending
        # deletes).
        committed = self._manager.list_regions()
        pending_deletes = set(self._manager._pending_deletes)
        survivors = [r for r in committed if r not in pending_deletes and r != current]
        # Pending creates also count as survivors — the user might be
        # trading old regions for new ones.
        survivors += [r.name for r in self._manager._pending_creates]
        if not survivors:
            QMessageBox.warning(
                self, "Delete Region",
                "Cannot delete — this would leave zero regions. Stage a "
                "create or undo other pending deletes first.")
            return

        # Scan for external references — anywhere outside region_map.c
        # that hardcodes the REGIONMAP_<name> constant. The codegen owns
        # every reference INSIDE region_map.c (those just disappear), but
        # any custom code in scripts/.c/.h/.inc files that mentions the
        # constant by name will fail to compile after delete.
        const = f"REGIONMAP_{current.replace('_', '').upper()}"
        try:
            external_refs = self._manager.find_external_region_references(current)
        except Exception:
            external_refs = []

        # Visibility gates that will be silently dropped.
        gate_count = sum(1 for g in self._manager._engine_state.gates
                         if g.region_name == current)

        # Mapsec count for context.
        mapsec_count = len(self._manager._compute_mapsecs_for_region(current))

        # Build the confirmation body.
        msg_lines = [
            f"<b>Delete region '{current}'?</b>",
            "",
            "<b>On save, this will:</b>",
            f"&nbsp;&nbsp;• Remove <code>src/data/region_map/region_map_layout_{current}.h</code>",
            f"&nbsp;&nbsp;• Remove <code>graphics/region_map/{current}.bin</code> "
            f"(and <code>.bin.lz</code>)",
            f"&nbsp;&nbsp;• Drop <code>{const}</code> from the engine enum",
            f"&nbsp;&nbsp;• Regenerate every block in <code>src/region_map.c</code> "
            f"that referenced it",
        ]
        if gate_count:
            msg_lines.append(
                f"&nbsp;&nbsp;• Drop {gate_count} story-flag visibility gate(s) "
                f"tied to this region"
            )
        if mapsec_count:
            msg_lines.append(
                f"&nbsp;&nbsp;• Remove {mapsec_count} MAPSEC(s) from the player-region "
                f"lookup (in-game maps using those MAPSECs will fall back "
                f"to your base region)"
            )
        msg_lines.append("")

        if external_refs:
            msg_lines.append(
                f"<b style='color:#ff6666;'>⚠ {len(external_refs)} external "
                f"reference(s) to <code>{const}</code> found outside "
                f"<code>src/region_map.c</code>:</b>"
            )
            for path, lineno, line in external_refs[:10]:
                snippet = line if len(line) <= 80 else line[:80] + "…"
                msg_lines.append(
                    f"&nbsp;&nbsp;• <code>{path}:{lineno}</code> &nbsp; "
                    f"<small style='color:#aaa;'>{snippet}</small>"
                )
            if len(external_refs) > 10:
                msg_lines.append(
                    f"&nbsp;&nbsp;• <i>… and {len(external_refs) - 10} more</i>"
                )
            msg_lines.append("")
            msg_lines.append(
                "<b>The build WILL FAIL until you remove or update those "
                "references manually.</b> Cancel and update them first, "
                "or proceed if you know what you're doing."
            )
        else:
            msg_lines.append(
                "No external code references this region — the build will "
                "still work after delete."
            )

        msg_lines.append("")
        msg_lines.append(
            "<small><i>Cannot be undone from within the app once saved. "
            "Make sure your project is committed to git before saving.</i></small>"
        )

        reply = QMessageBox.warning(
            self, "Delete Region — Confirm", "<br>".join(msg_lines),
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        if reply != QMessageBox.StandardButton.Ok:
            return
        try:
            self._manager.stage_delete_region(current)
        except Exception as e:
            QMessageBox.critical(self, "Delete Region", str(e))
            return
        # Drop pending paints on the deleted region.
        self._drop_pending_paints_for_delete(current)
        self._mw.log_message(f"Region Map: staged delete region '{current}'")
        self._update_status()
        self.data_changed.emit()
