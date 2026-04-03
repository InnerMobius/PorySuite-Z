"""
Region Map tab — Visual region map editor with clickable grid.

Renders the actual tileset graphics as a background image, overlays a
transparent clickable grid for cell selection and section assignment.
Matches the original TriforceGUI visual behavior.
"""

from PyQt6.QtCore import Qt, QRectF, pyqtSignal
from PyQt6.QtGui import QColor, QBrush, QPen, QPixmap, QImage
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QComboBox, QPushButton, QLabel,
    QGraphicsView, QGraphicsScene, QGraphicsRectItem,
    QGraphicsPixmapItem,
    QInputDialog, QMessageBox,
)


# ─────────────────────────────────────────────────────────────────────────────
# RegionMapView — matches TriforceGUI's RegionMapView exactly
# ─────────────────────────────────────────────────────────────────────────────

class RegionMapView(QGraphicsView):
    """QGraphicsView that displays the tileset image with a clickable
    transparent grid overlay. Cell sizes are derived from the pixmap,
    not hardcoded."""

    cellClicked = pyqtSignal(int, int)   # (grid_x, grid_y)

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

    def update_cell(self, x, y, sid):
        item = self.cell_items.get((x, y))
        if item:
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
        self._build_ui()

    def _build_ui(self):
        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(8, 8, 8, 8)

        # ── Region selector + layer toggle ──────────────────────────────────
        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("Region:"))
        self.region_combo = QComboBox()
        self.region_combo.setPlaceholderText("Select a region...")
        self.region_combo.currentTextChanged.connect(self._on_region_changed)
        top_row.addWidget(self.region_combo, 1)

        top_row.addWidget(QLabel("  Layer:"))
        self.btn_layer_map = QPushButton("Map")
        self.btn_layer_dungeon = QPushButton("Dungeon")
        self.btn_layer_map.setCheckable(True)
        self.btn_layer_dungeon.setCheckable(True)
        self.btn_layer_map.setChecked(True)
        self.btn_layer_map.setFixedWidth(80)
        self.btn_layer_dungeon.setFixedWidth(80)
        self.btn_layer_map.clicked.connect(lambda: self._set_layer('map'))
        self.btn_layer_dungeon.clicked.connect(lambda: self._set_layer('dungeon'))
        top_row.addWidget(self.btn_layer_map)
        top_row.addWidget(self.btn_layer_dungeon)

        self._root_layout.addLayout(top_row)
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
        self.section_combo.setPlaceholderText("Select a section...")
        self.section_combo.setEditable(True)
        section_row.addWidget(self.section_combo, 1)
        self.btn_assign = QPushButton("Assign to Selected Cell")
        self.btn_assign.setEnabled(False)
        self.btn_assign.clicked.connect(self._on_assign_section)
        section_row.addWidget(self.btn_assign)
        self._root_layout.addLayout(section_row)

        # ── Action buttons ───────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self.btn_save = QPushButton("Save")
        self.btn_reload = QPushButton("Reload")
        self.btn_clone = QPushButton("Clone Region")
        self.btn_rename_region = QPushButton("Rename Region")
        self.btn_rename_section = QPushButton("Rename Section")
        self.btn_delete_region = QPushButton("Delete Region")

        for btn in (self.btn_save, self.btn_reload, self.btn_clone,
                    self.btn_rename_region, self.btn_rename_section,
                    self.btn_delete_region):
            btn.setEnabled(False)
            btn_row.addWidget(btn)

        self._root_layout.addLayout(btn_row)

        # ── Button connections ───────────────────────────────────────────────
        self.btn_save.clicked.connect(self._on_save)
        self.btn_reload.clicked.connect(self._on_reload)
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

        from eventide.backend.region_map_manager import RegionMapManager
        try:
            self._manager = RegionMapManager(project_dir)
        except Exception as e:
            self._mw.log_message(f"Region Map tab: failed to load backend: {e}")
            return

        for btn in (self.btn_save, self.btn_reload, self.btn_clone,
                    self.btn_rename_region, self.btn_rename_section,
                    self.btn_delete_region, self.btn_assign):
            btn.setEnabled(True)

        self._populate_regions()
        self._mw.log_message(f"Region Map tab: loaded {project_dir}")

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

    def _set_layer(self, layer: str):
        """Switch between Map and Dungeon layers."""
        self._current_layer = layer
        self.btn_layer_map.setChecked(layer == 'map')
        self.btn_layer_dungeon.setChecked(layer == 'dungeon')
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
            if self._current_layer == 'dungeon':
                self._grid, self._grid_w, self._grid_h = self._manager.load_dungeon_grid()
            else:
                self._grid, self._grid_w, self._grid_h = self._manager.load_grid()
        except Exception as e:
            self._mw.log_message(f"Region Map tab: failed to load grid: {e}")
            return

        # Populate section combo — show all known sections (from JSON),
        # not just what's on the current grid, so dungeon entries can be assigned
        grid_sections = {cell for row in self._grid for cell in row if cell}
        all_known = set(self._manager.get_section_ids())
        all_sections = sorted(grid_sections | all_known)
        self.section_combo.clear()
        self.section_combo.addItem("(none)")
        for sid in all_sections:
            self.section_combo.addItem(sid)

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
        # Insert at position 1 (after the region selector row)
        self._root_layout.insertWidget(1, self._map_view, 1)

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
        self._mw.log_message(
            f"Region Map: selected cell ({grid_x}, {grid_y}) = {cell_value or '(empty)'}")
        if cell_value:
            idx = self.section_combo.findText(cell_value)
            if idx >= 0:
                self.section_combo.setCurrentIndex(idx)

    # ─────────────────────────────────────────────────────────────────────────
    # Actions
    # ─────────────────────────────────────────────────────────────────────────

    def _on_assign_section(self):
        if not self._selected_cell or not self._grid:
            QMessageBox.information(self, "Assign Section", "Click a cell on the grid first.")
            return
        grid_x, grid_y = self._selected_cell
        section = self.section_combo.currentText()
        if section == "(none)":
            self._grid[grid_y][grid_x] = None
        else:
            self._grid[grid_y][grid_x] = section
        if self._map_view:
            self._map_view.update_cell(grid_x, grid_y, section)
        self._mw.log_message(f"Region Map: set ({grid_x}, {grid_y}) = {section}")

    def _on_save(self):
        if not self._manager or not self._grid:
            return
        try:
            self._manager.save_grid(self._grid, layer=self._current_layer)
            layer_name = 'dungeon' if self._current_layer == 'dungeon' else 'map'
            self._mw.log_message(
                f"Region Map: saved {layer_name} layer and regenerated headers")
            self.data_changed.emit()
            QMessageBox.information(self, "Save", "Region map saved successfully.")
        except Exception as e:
            QMessageBox.critical(self, "Save", str(e))

    def _on_reload(self):
        if self._manager:
            try:
                self._manager.sections = self._manager.load_sections()
                self._manager._load_all_layouts()
            except Exception as e:
                QMessageBox.critical(self, "Reload", str(e))
                return
        self._load_grid()
        self._mw.log_message("Region Map: reloaded from disk")

    def _on_clone_region(self):
        if not self._manager:
            return
        current = self.region_combo.currentText()
        name, ok = QInputDialog.getText(
            self, "Clone Region", f"Name for clone of '{current}':")
        if not ok or not name.strip():
            return
        try:
            clean = self._manager.clone_region(current, name.strip())
            self._mw.log_message(f"Region Map: cloned {current} -> {clean}")
            self.data_changed.emit()
            self._populate_regions()
            idx = self.region_combo.findText(clean)
            if idx >= 0:
                self.region_combo.setCurrentIndex(idx)
        except Exception as e:
            QMessageBox.critical(self, "Clone Region", str(e))

    def _on_rename_region(self):
        if not self._manager:
            return
        current = self.region_combo.currentText()
        new_name, ok = QInputDialog.getText(
            self, "Rename Region", f"New name for '{current}':", text=current)
        if not ok or not new_name.strip() or new_name == current:
            return
        try:
            clean = self._manager.rename_region(current, new_name.strip())
            self._mw.log_message(f"Region Map: renamed {current} -> {clean}")
            self.data_changed.emit()
            self._populate_regions()
            idx = self.region_combo.findText(clean)
            if idx >= 0:
                self.region_combo.setCurrentIndex(idx)
        except Exception as e:
            QMessageBox.critical(self, "Rename Region", str(e))

    def _on_rename_section(self):
        if not self._manager:
            return
        sections = self._manager.get_section_ids()
        if not sections:
            QMessageBox.information(self, "Rename Section", "No sections found.")
            return
        old_id, ok = QInputDialog.getItem(
            self, "Rename Section", "Section to rename:", sections, editable=False)
        if not ok:
            return
        new_id, ok2 = QInputDialog.getText(
            self, "Rename Section", f"New ID for '{old_id}':", text=old_id)
        if not ok2 or not new_id.strip() or new_id == old_id:
            return
        try:
            self._manager.rename_section(old_id, new_id.strip())
            self._mw.log_message(f"Region Map: renamed section {old_id} -> {new_id.strip()}")
            self.data_changed.emit()
            self._load_grid()
        except Exception as e:
            QMessageBox.critical(self, "Rename Section", str(e))

    def _on_delete_region(self):
        if not self._manager:
            return
        current = self.region_combo.currentText()
        regions = self._manager.list_regions()
        if len(regions) <= 1:
            QMessageBox.warning(self, "Delete Region", "Cannot delete the only region.")
            return
        reply = QMessageBox.question(
            self, "Delete Region",
            f"Delete region '{current}'? This removes layout and tilemap files.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            self._manager.delete_region(current)
            self._mw.log_message(f"Region Map: deleted region {current}")
            self.data_changed.emit()
            self._populate_regions()
        except Exception as e:
            QMessageBox.critical(self, "Delete Region", str(e))
