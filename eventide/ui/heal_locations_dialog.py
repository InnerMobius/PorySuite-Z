"""
Heal Locations editor — set where the player heals and respawns.

Lists every heal location (Pokémon-center respawn point), lets you edit its
trigger map + coordinates, respawn map, and respawn NPC, add/remove entries,
and flags any that point at a map constant the project no longer defines — the
exact break you get after renaming a map (e.g. `MAP_..._PLAYERS_HOUSE_1F`
disappears and the build fails). Writes `src/data/heal_locations.json`; the
build regenerates the headers from it.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QListWidget,
    QListWidgetItem, QLineEdit, QSpinBox, QPushButton, QLabel, QMessageBox,
    QSplitter, QWidget, QAbstractItemView,
)

from eventide.backend import heal_locations as hl
from eventide.backend.constants_manager import ConstantsManager
from eventide.ui.widgets import ConstantPicker


class HealLocationsDialog(QDialog):
    def __init__(self, root, parent=None):
        super().__init__(parent)
        self._root = root
        self.setWindowTitle("Heal Locations")
        self.resize(720, 520)
        self._locs = hl.load(root)
        self._valid_maps = set(ConstantsManager.MAP_CONSTANTS or [])
        self._loading = False

        v = QVBoxLayout(self)
        intro = QLabel(
            "Where the player heals and respawns after a blackout. Red entries "
            "point at a map that no longer exists (rename break) — fix their "
            "Respawn Map.")
        intro.setWordWrap(True)
        intro.setStyleSheet("color:#888;")
        v.addWidget(intro)

        split = QSplitter(Qt.Orientation.Horizontal)
        v.addWidget(split, 1)

        # ── Left: the list ──────────────────────────────────────────────
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self._list = QListWidget()
        self._list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self._list.currentRowChanged.connect(self._on_select)
        ll.addWidget(self._list, 1)
        row = QHBoxLayout()
        self._btn_add = QPushButton("Add")
        self._btn_add.clicked.connect(self._on_add)
        self._btn_del = QPushButton("Remove")
        self._btn_del.clicked.connect(self._on_del)
        row.addWidget(self._btn_add)
        row.addWidget(self._btn_del)
        ll.addLayout(row)
        split.addWidget(left)

        # ── Right: the detail form ──────────────────────────────────────
        right = QWidget()
        form = QFormLayout(right)
        self._id_edit = QLineEdit()
        self._id_edit.setPlaceholderText("HEAL_LOCATION_SOMEWHERE")
        self._id_edit.editingFinished.connect(self._commit)
        form.addRow("ID:", self._id_edit)

        maps = list(ConstantsManager.MAP_CONSTANTS or [])
        self._map = ConstantPicker(maps, prefix="MAP_")
        self._map.currentTextChanged.connect(lambda *_: self._commit())
        form.addRow("Heals at (map):", self._map)

        xy = QHBoxLayout()
        self._x = QSpinBox(); self._x.setRange(0, 999)
        self._x.valueChanged.connect(lambda *_: self._commit())
        self._y = QSpinBox(); self._y.setRange(0, 999)
        self._y.valueChanged.connect(lambda *_: self._commit())
        xy.addWidget(QLabel("X")); xy.addWidget(self._x)
        xy.addWidget(QLabel("Y")); xy.addWidget(self._y)
        xy.addStretch()
        form.addRow("Coordinates:", xy)

        self._respawn = ConstantPicker(maps, prefix="MAP_")
        self._respawn.currentTextChanged.connect(lambda *_: self._commit())
        form.addRow("Respawn map:", self._respawn)

        self._npc = QLineEdit()
        self._npc.setPlaceholderText("LOCALID_MOM  (or 0 for none)")
        self._npc.editingFinished.connect(self._commit)
        form.addRow("Respawn NPC:", self._npc)

        self._warn = QLabel("")
        self._warn.setStyleSheet("color:#d9a441;font-size:11px;")
        self._warn.setWordWrap(True)
        form.addRow("", self._warn)
        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 2)

        # ── Buttons ─────────────────────────────────────────────────────
        brow = QHBoxLayout()
        brow.addStretch()
        save = QPushButton("Save")
        save.clicked.connect(self._on_save)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        brow.addWidget(save)
        brow.addWidget(close)
        v.addLayout(brow)

        self._rebuild_list()
        if self._locs:
            self._list.setCurrentRow(0)

    # ── list / selection ────────────────────────────────────────────────
    def _problems(self) -> dict:
        return hl.validate(self._root, self._locs, self._valid_maps)

    def _rebuild_list(self):
        problems = self._problems()
        cur = self._list.currentRow()
        self._list.blockSignals(True)
        self._list.clear()
        for loc in self._locs:
            lid = str(loc.get("id", "?"))
            short = lid[len("HEAL_LOCATION_"):] if lid.startswith(
                "HEAL_LOCATION_") else lid
            item = QListWidgetItem(short.replace("_", " ").title() or lid)
            item.setToolTip(lid)
            if lid in problems:
                item.setForeground(QColor("#e06c6c"))
                item.setToolTip(lid + "\n" + "\n".join(problems[lid]))
            self._list.addItem(item)
        self._list.blockSignals(False)
        if 0 <= cur < self._list.count():
            self._list.setCurrentRow(cur)

    def _cur_index(self) -> int:
        return self._list.currentRow()

    def _on_select(self, idx):
        if not (0 <= idx < len(self._locs)):
            return
        loc = self._locs[idx]
        self._loading = True
        try:
            self._id_edit.setText(str(loc.get("id", "")))
            self._map.set_constant(str(loc.get("map", "")))
            self._x.setValue(int(loc.get("x", 0) or 0))
            self._y.setValue(int(loc.get("y", 0) or 0))
            self._respawn.set_constant(str(loc.get("respawn_map", "")))
            self._npc.setText(str(loc.get("respawn_npc", "") or ""))
        finally:
            self._loading = False
        self._update_warning(loc)

    def _update_warning(self, loc):
        probs = []
        for field, label in (("map", "Heals-at map"),
                              ("respawn_map", "Respawn map")):
            val = str(loc.get(field, "")).strip()
            if val and self._valid_maps and val not in self._valid_maps:
                probs.append(f"{label} '{val}' doesn't exist — pick a real map.")
        self._warn.setText("  ".join(f"⚠ {p}" for p in probs))

    def _commit(self):
        if self._loading:
            return
        idx = self._cur_index()
        if not (0 <= idx < len(self._locs)):
            return
        loc = self._locs[idx]
        loc["id"] = self._id_edit.text().strip()
        loc["map"] = self._map.selected_constant().strip()
        loc["x"] = self._x.value()
        loc["y"] = self._y.value()
        loc["respawn_map"] = self._respawn.selected_constant().strip()
        loc["respawn_npc"] = self._npc.text().strip() or "0"
        self._update_warning(loc)
        self._rebuild_list()

    def _on_add(self):
        self._locs.append({
            "id": "HEAL_LOCATION_NEW", "map": "", "x": 0, "y": 0,
            "respawn_map": "", "respawn_npc": "0"})
        self._rebuild_list()
        self._list.setCurrentRow(len(self._locs) - 1)

    def _on_del(self):
        idx = self._cur_index()
        if not (0 <= idx < len(self._locs)):
            return
        lid = self._locs[idx].get("id", "this heal location")
        if QMessageBox.question(
                self, "Remove", f"Remove {lid}?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self._locs.pop(idx)
        self._rebuild_list()

    def _on_save(self):
        self._commit()
        ok, msg = hl.save(self._root, self._locs)
        if ok:
            QMessageBox.information(self, "Heal Locations", msg)
            self.accept()
        else:
            QMessageBox.warning(self, "Heal Locations", msg)
