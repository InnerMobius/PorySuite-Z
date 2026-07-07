"""ui/map_transfer_tab.py — Map Transfer tab.

Move whole maps between two pokefirered decomp projects. porymap/PorySuite only
offer "import from AdvanceMap", which can't move a map you already have in one
decomp into another. This tab does the whole job:

  Export — tick the maps you want, see every layout + tileset they drag along,
           and write a self-contained ".zip" bundle.
  Import — open a bundle in a DIFFERENT project, rename the maps/layouts (and
           their MAP_/LAYOUT_ constants) as they come in, resolve any name
           collisions (skip / overwrite / rename), pick the map group, and
           inject everything — patching layouts.json, map_groups.json and the
           three tileset headers for you.

The heavy lifting lives in core.map_transfer; this file is only the UI. Import
writes straight to disk on a confirmed button press (it is a deliberate commit,
not a live edit), so this tab follows the set_project()/load() contract but has
no unsaved-dirty state of its own.
"""

from __future__ import annotations

import importlib.util
import logging
import os
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QTabWidget,
    QLabel, QLineEdit, QPushButton, QGroupBox, QComboBox,
    QListWidget, QListWidgetItem, QTableWidget, QTableWidgetItem,
    QHeaderView, QMessageBox, QFileDialog, QProgressDialog, QAbstractItemView,
    QApplication, QPlainTextEdit, QCheckBox,
)

from ui.custom_widgets.scroll_guard import install_scroll_guard


# ── File logger (writes to porysuite/map_transfer.log) ────────────────────────
_log = logging.getLogger("MapTransfer")
_log.setLevel(logging.DEBUG)
try:
    _lp = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                       "map_transfer.log")
    _fh = logging.FileHandler(_lp, mode="w", encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s  %(message)s",
                                       datefmt="%H:%M:%S"))
    _log.addHandler(_fh)
except Exception:  # pragma: no cover
    pass


def _load_engine():
    """Import core.map_transfer without dragging in the heavy core package."""
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                        "core", "map_transfer.py")
    spec = importlib.util.spec_from_file_location("map_transfer", path)
    mod = importlib.util.module_from_spec(spec)
    import sys
    sys.modules.setdefault("map_transfer", mod)
    spec.loader.exec_module(mod)
    return mod


mt = _load_engine()


# ============================================================================
# Export panel
# ============================================================================

class _ExportPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._root = ""
        self._maps: list = []          # list[MapDep]
        self._build_ui()

    def _build_ui(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 10, 10, 10)
        blurb = QLabel(
            "Tick the maps to move, then export a portable bundle (.zip). "
            "Each map brings its layout, tileset(s) and palettes with it. "
            "Open the bundle from the Import tab in your other project.")
        blurb.setWordWrap(True)
        v.addWidget(blurb)

        split = QSplitter(Qt.Orientation.Horizontal)
        v.addWidget(split, 1)

        # left — searchable checkable map list
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search maps…")
        self._search.textChanged.connect(self._apply_filter)
        row.addWidget(self._search)
        self._all_btn = QPushButton("All")
        self._all_btn.clicked.connect(lambda: self._set_all(True))
        self._none_btn = QPushButton("None")
        self._none_btn.clicked.connect(lambda: self._set_all(False))
        row.addWidget(self._all_btn)
        row.addWidget(self._none_btn)
        lv.addLayout(row)
        self._list = QListWidget()
        self._list.itemChanged.connect(self._on_item_changed)
        lv.addWidget(self._list, 1)
        self._count_lbl = QLabel("No project loaded")
        lv.addWidget(self._count_lbl)
        split.addWidget(left)

        # right — dependency preview
        right = QGroupBox("What will be included")
        rv = QVBoxLayout(right)
        self._deps = QPlainTextEdit()
        self._deps.setReadOnly(True)
        self._deps.setPlaceholderText(
            "Tick one or more maps to preview their layouts and tilesets.")
        f = QFont("Consolas")
        f.setStyleHint(QFont.StyleHint.Monospace)
        self._deps.setFont(f)
        rv.addWidget(self._deps, 1)
        split.addWidget(right)
        split.setSizes([340, 460])

        btnrow = QHBoxLayout()
        btnrow.addStretch(1)
        self._export_btn = QPushButton("Export selected to bundle…")
        self._export_btn.clicked.connect(self._do_export)
        btnrow.addWidget(self._export_btn)
        v.addLayout(btnrow)

    # -- loading --
    def set_project(self, root: str):
        self._root = root or ""
        self.load()

    def load(self):
        self._list.blockSignals(True)
        self._list.clear()
        self._maps = []
        try:
            if self._root and mt.is_decomp_project(self._root):
                self._maps = mt.scan_maps(self._root)
                for md in self._maps:
                    it = QListWidgetItem(md.name)
                    it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    it.setCheckState(Qt.CheckState.Unchecked)
                    it.setData(Qt.ItemDataRole.UserRole, md.name)
                    grp = md.group.replace("gMapGroup_", "") if md.group else "?"
                    it.setToolTip(f"{md.const}   ·   group {grp}")
                    self._list.addItem(it)
                self._count_lbl.setText(f"{len(self._maps)} maps")
            else:
                self._count_lbl.setText("No decomp project loaded")
        except Exception as e:
            _log.exception("export load failed")
            self._count_lbl.setText(f"Load error: {e}")
        self._list.blockSignals(False)
        self._deps.clear()
        self._apply_filter()

    def _apply_filter(self):
        q = self._search.text().strip().lower()
        for i in range(self._list.count()):
            it = self._list.item(i)
            it.setHidden(bool(q) and q not in it.text().lower())

    def _set_all(self, on: bool):
        self._list.blockSignals(True)
        for i in range(self._list.count()):
            it = self._list.item(i)
            if not it.isHidden():
                it.setCheckState(Qt.CheckState.Checked if on
                                 else Qt.CheckState.Unchecked)
        self._list.blockSignals(False)
        self._refresh_deps()

    def _on_item_changed(self, _it):
        self._refresh_deps()

    def _checked_maps(self) -> list:
        out = []
        for i in range(self._list.count()):
            it = self._list.item(i)
            if it.checkState() == Qt.CheckState.Checked:
                out.append(it.data(Qt.ItemDataRole.UserRole))
        return out

    def _refresh_deps(self):
        picks = self._checked_maps()
        if not picks:
            self._deps.clear()
            return
        layouts: dict = {}
        tilesets: dict = {}   # label -> {kind, animated, maps:set}
        warns: list = []
        for name in picks:
            try:
                info = mt.resolve_map_dependencies(self._root, name)
            except Exception as e:
                warns.append(f"{name}: {e}")
                continue
            if info["layout"]:
                layouts[info["layout"].entry["id"]] = info["layout"].entry.get("name", "")
            for ts in info["tilesets"]:
                d = tilesets.setdefault(
                    ts.label, {"kind": ts.kind, "animated": bool(ts.anim_code),
                               "maps": set()})
                d["maps"].add(name)
            warns.extend(info["warnings"])
        lines = [f"Maps ({len(picks)}):"]
        lines += [f"    {p}" for p in picks]
        lines.append("")
        lines.append(f"Layouts ({len(layouts)}):")
        lines += [f"    {lid}" for lid in sorted(layouts)]
        lines.append("")
        lines.append(f"Tilesets ({len(tilesets)}):")
        for lbl in sorted(tilesets):
            d = tilesets[lbl]
            tags = [d["kind"]]
            if d["animated"]:
                tags.append("animated — code carried")
            if len(d["maps"]) > 1:
                tags.append(f"shared by {len(d['maps'])} maps")
            lines.append(f"    {lbl}  ({', '.join(tags)})")
        lines.append("")
        lines.append("Each tileset brings its tiles, metatiles, attributes, all "
                     "palettes and animation frames.")
        if warns:
            lines.append("")
            lines.append("Notes:")
            lines += [f"    ! {w}" for w in dict.fromkeys(warns)]
        self._deps.setPlainText("\n".join(lines))

    def _do_export(self):
        picks = self._checked_maps()
        if not picks:
            QMessageBox.information(self, "Nothing selected",
                                    "Tick at least one map to export.")
            return
        default = os.path.join(
            os.path.expanduser("~"),
            (picks[0] if len(picks) == 1 else "maps") + "_bundle.zip")
        path, _ = QFileDialog.getSaveFileName(
            self, "Export map bundle", default, "Map bundle (*.zip)")
        if not path:
            return
        if not path.lower().endswith(".zip"):
            path += ".zip"
        # bundle folder sits next to the zip (build_bundle writes both)
        bundle_dir = path[:-4]
        prog = QProgressDialog("Packing maps…", None, 0, len(picks) + 1, self)
        prog.setWindowTitle("Exporting")
        prog.setWindowModality(Qt.WindowModality.WindowModal)
        prog.setMinimumDuration(0)
        prog.setValue(0)

        def cb(i, total, msg):
            prog.setMaximum(total + 1)
            prog.setValue(i)
            prog.setLabelText(msg)
            QApplication.processEvents()

        try:
            proj = os.path.basename(self._root.rstrip("/\\"))
            res = mt.build_bundle(self._root, picks, bundle_dir,
                                  project_name=proj, make_zip=True, progress=cb)
        except Exception as e:
            prog.close()
            _log.exception("export failed")
            QMessageBox.critical(self, "Export failed", str(e))
            return
        prog.setValue(prog.maximum())
        prog.close()
        m = res["manifest"]
        msg = (f"Exported {len(m['maps'])} map(s), {len(m['layouts'])} layout(s) "
               f"and {len(m['tilesets'])} tileset(s).\n\nBundle:\n{res['zip_path']}")
        if res["warnings"]:
            msg += "\n\nNotes:\n" + "\n".join(
                f"• {w}" for w in dict.fromkeys(res["warnings"]))
        QMessageBox.information(self, "Export complete", msg)


# ============================================================================
# Import panel
# ============================================================================

_ACTIONS_NEW = ["Create"]
_ACTIONS_COLLIDE = ["Skip (reuse existing)", "Overwrite", "Rename"]


class _ImportPanel(QWidget):
    imported = pyqtSignal()   # emitted after a successful import

    def __init__(self, parent=None):
        super().__init__(parent)
        self._root = ""
        self._bundle_path = ""
        self._manifest: Optional[dict] = None
        self._collisions: dict = {}
        self._build_ui()

    def _build_ui(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 10, 10, 10)
        blurb = QLabel(
            "Open a bundle exported from another project, rename anything you "
            "like, decide what to do about name clashes, then import. Each map "
            "keeps its own group by default — a group the target doesn't have "
            "is created automatically.")
        blurb.setWordWrap(True)
        v.addWidget(blurb)

        row = QHBoxLayout()
        self._open_btn = QPushButton("Open bundle…")
        self._open_btn.clicked.connect(self._open_bundle)
        row.addWidget(self._open_btn)
        self._bundle_lbl = QLabel("No bundle loaded")
        self._bundle_lbl.setStyleSheet("color:#888;")
        row.addWidget(self._bundle_lbl, 1)
        v.addLayout(row)

        # maps table
        v.addWidget(self._section_label("Maps"))
        self._map_tbl = self._make_table(
            ["From bundle", "New map name", "MAP_ constant", "Group", "On clash"])
        self._map_tbl.cellChanged.connect(self._on_map_cell)
        v.addWidget(self._map_tbl)

        # layouts table
        v.addWidget(self._section_label("Layouts"))
        self._lay_tbl = self._make_table(
            ["From bundle", "New layout name", "LAYOUT_ constant", "On clash"])
        self._lay_tbl.cellChanged.connect(self._on_lay_cell)
        v.addWidget(self._lay_tbl)

        # tilesets table
        v.addWidget(self._section_label("Tilesets"))
        self._ts_tbl = self._make_table(
            ["From bundle", "Already in project?", "On clash", "New name (if rename)"])
        self._ts_tbl.cellChanged.connect(self._on_ts_cell)
        v.addWidget(self._ts_tbl)

        # options row
        orow = QHBoxLayout()
        self._strip_conn = QCheckBox(
            "Remove connections to maps not included in this import")
        self._strip_conn.setChecked(True)
        self._strip_conn.setToolTip(
            "A map's edge-connections point at neighbouring maps. Links to maps "
            "that aren't in this project (and aren't in this bundle) would break "
            "the build, so they're dropped. Links between maps you import "
            "together are kept.")
        orow.addWidget(self._strip_conn)
        orow.addStretch(1)
        v.addLayout(orow)

        # optional bulk override + import
        brow = QHBoxLayout()
        brow.addWidget(QLabel("Set all maps to group:"))
        self._bulk_group = QComboBox()
        install_scroll_guard(self._bulk_group)
        self._bulk_group.setToolTip(
            "Optional. Leave on 'keep each map's own group' to preserve the "
            "groups the maps came from. Pick a group here to force every map "
            "into it instead.")
        self._bulk_group.currentIndexChanged.connect(self._apply_bulk_group)
        brow.addWidget(self._bulk_group)
        brow.addStretch(1)
        self._import_btn = QPushButton("Import into this project")
        self._import_btn.clicked.connect(self._do_import)
        self._import_btn.setEnabled(False)
        brow.addWidget(self._import_btn)
        v.addLayout(brow)

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        f = lbl.font()
        f.setBold(True)
        lbl.setFont(f)
        return lbl

    def _make_table(self, headers: list) -> QTableWidget:
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QAbstractItemView.EditTrigger.AllEditTriggers)
        t.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        hh = t.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in range(1, len(headers)):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.Stretch)
        t.setMaximumHeight(180)
        return t

    # -- loading --
    def set_project(self, root: str):
        self._root = root or ""
        self.load()

    def load(self):
        # F5: forget any loaded bundle, reset all tables/state
        self._manifest = None
        self._bundle_path = ""
        self._collisions = {}
        self._bundle_lbl.setText("No bundle loaded")
        for t in (self._map_tbl, self._lay_tbl, self._ts_tbl):
            t.blockSignals(True)
            t.setRowCount(0)
            t.blockSignals(False)
        # (display, const) for every group already in the target project
        self._groups = []
        try:
            if self._root and mt.is_decomp_project(self._root):
                mg = mt._read_json(os.path.join(
                    mt.maps_dir(self._root), "map_groups.json"))
                for g in mg.get("group_order", []):
                    self._groups.append((g.replace("gMapGroup_", ""), g))
        except Exception:
            _log.exception("import group load failed")
        self._bulk_group.blockSignals(True)
        self._bulk_group.clear()
        self._bulk_group.addItem("— keep each map's own group —", None)
        for disp, const in self._groups:
            self._bulk_group.addItem(disp, const)
        self._bulk_group.blockSignals(False)
        self._import_btn.setEnabled(False)

    def _group_combo_for(self, original_group: str) -> QComboBox:
        """Per-map group picker, defaulting to the map's own original group.
        If the target doesn't have that group, it's offered as '(new)'."""
        cb = QComboBox()
        install_scroll_guard(cb)
        consts = [c for _d, c in self._groups]
        for disp, const in self._groups:
            cb.addItem(disp, const)
        if original_group and original_group not in consts:
            cb.addItem(original_group.replace("gMapGroup_", "") + "  (new)",
                       original_group)
        # select the map's original group
        idx = cb.findData(original_group)
        if idx >= 0:
            cb.setCurrentIndex(idx)
        return cb

    def _apply_bulk_group(self, _idx):
        const = self._bulk_group.currentData()
        if const is None:
            return  # "keep each map's own group" — leave rows untouched
        for r in range(self._map_tbl.rowCount()):
            cb = self._map_tbl.cellWidget(r, 3)
            if cb is not None:
                j = cb.findData(const)
                if j < 0:
                    cb.addItem(const.replace("gMapGroup_", ""), const)
                    j = cb.findData(const)
                cb.setCurrentIndex(j)

    def _open_bundle(self):
        if not (self._root and mt.is_decomp_project(self._root)):
            QMessageBox.warning(self, "No project",
                                "Open a decomp project first.")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Open map bundle", os.path.expanduser("~"),
            "Map bundle (*.zip);;All files (*)")
        if not path:
            # allow picking a bundle FOLDER too
            path = QFileDialog.getExistingDirectory(
                self, "Or pick a bundle folder", os.path.expanduser("~"))
            if not path:
                return
        try:
            loaded = mt.load_bundle(path)
            self._manifest = loaded["manifest"]
            self._bundle_path = path
            self._collisions = mt.detect_collisions(self._root, self._manifest)
        except Exception as e:
            _log.exception("open bundle failed")
            QMessageBox.critical(self, "Could not open bundle", str(e))
            return
        self._bundle_lbl.setText(
            f"{os.path.basename(path)}  ·  from "
            f"{self._manifest.get('source_project', '?')}")
        self._bundle_lbl.setStyleSheet("")
        self._populate_tables()
        self._import_btn.setEnabled(True)

    def _populate_tables(self):
        m = self._manifest
        coll = self._collisions
        # maps
        t = self._map_tbl
        t.blockSignals(True)
        t.setRowCount(0)
        for mp in m.get("maps", []):
            r = t.rowCount()
            t.insertRow(r)
            t.setItem(r, 0, self._ro(mp["name"]))
            t.setItem(r, 1, QTableWidgetItem(mp["name"]))
            t.setItem(r, 2, self._ro("MAP_" + mt.camel_to_screaming(mp["name"])))
            t.setCellWidget(r, 3, self._group_combo_for(mp.get("group", "")))
            t.setCellWidget(r, 4, self._clash_combo(coll["maps"].get(mp["name"], False)))
        t.blockSignals(False)
        # layouts
        t = self._lay_tbl
        t.blockSignals(True)
        t.setRowCount(0)
        for l in m.get("layouts", []):
            entry = l["entry"]
            folder = l["folder"]
            r = t.rowCount()
            t.insertRow(r)
            t.setItem(r, 0, self._ro(entry["id"]))
            t.setItem(r, 1, QTableWidgetItem(folder))   # layout folder/base name
            t.setItem(r, 2, self._ro("LAYOUT_" + mt.camel_to_screaming(folder)))
            t.setCellWidget(r, 3, self._clash_combo(
                coll["layouts"].get(entry["id"], False)))
            t.item(r, 0).setData(Qt.ItemDataRole.UserRole, entry["id"])
        t.blockSignals(False)
        # tilesets
        t = self._ts_tbl
        t.blockSignals(True)
        t.setRowCount(0)
        for ts in m.get("tilesets", []):
            exists = coll["tilesets"].get(ts["label"], False)
            is_primary = ts.get("kind") == "primary"
            r = t.rowCount()
            t.insertRow(r)
            label_cell = self._ro(ts["label"] + f"  ({ts['kind']})")
            label_cell.setToolTip(self._inventory_tip(ts))
            t.setItem(r, 0, label_cell)
            # "Already in project?" — for a colliding primary, show how many
            # target maps ride on it (overwriting hits all of them).
            if exists and is_primary:
                used = mt.tileset_usage_count(self._root, ts["label"])
                exists_cell = self._ro(f"Yes · {used} layout(s) use it")
            else:
                exists_cell = self._ro("Yes" if exists else "No")
            t.setItem(r, 1, exists_cell)
            combo = self._clash_combo(exists)
            # a shared primary defaults to Skip (safe) and is warned on overwrite
            t.setCellWidget(r, 2, combo)
            newname = QTableWidgetItem("")
            newname.setFlags(newname.flags() | Qt.ItemFlag.ItemIsEditable)
            t.setItem(r, 3, newname)
            it0 = t.item(r, 0)
            it0.setData(Qt.ItemDataRole.UserRole, ts["label"])
            it0.setData(Qt.ItemDataRole.UserRole + 1, is_primary)
        t.blockSignals(False)

    def _inventory_tip(self, ts: dict) -> str:
        inv = ts.get("inventory", {})
        bits = [
            f"tiles.png: {'yes' if inv.get('tiles') else 'MISSING'}",
            f"metatiles: {'yes' if inv.get('metatiles') else 'MISSING'}",
            f"attributes: {'yes' if inv.get('attributes') else 'MISSING'}",
            f"palettes: {inv.get('palettes', 0)}",
            f"anim frames: {inv.get('anim_frames', 0)}"
            + ("  (animation code carried)" if ts.get("animated") else ""),
        ]
        used = ts.get("used_by") or []
        if used:
            bits.append("used by: " + ", ".join(used))
        return "\n".join(bits)

    def _ro(self, text: str) -> QTableWidgetItem:
        it = QTableWidgetItem(text)
        it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
        return it

    def _clash_combo(self, collides: bool) -> QComboBox:
        cb = QComboBox()
        install_scroll_guard(cb)
        if collides:
            cb.addItems(_ACTIONS_COLLIDE)
            cb.setStyleSheet("QComboBox { color:#ffb74d; }")
        else:
            cb.addItems(_ACTIONS_NEW)
            cb.setEnabled(False)
        return cb

    # cell handlers keep the derived constant column live
    def _on_map_cell(self, r, c):
        if c == 1:
            name = self._map_tbl.item(r, 1).text().strip()
            self._map_tbl.blockSignals(True)
            self._map_tbl.item(r, 2).setText("MAP_" + mt.camel_to_screaming(name))
            self._map_tbl.blockSignals(False)

    def _on_lay_cell(self, r, c):
        if c == 1:
            name = self._lay_tbl.item(r, 1).text().strip()
            self._lay_tbl.blockSignals(True)
            self._lay_tbl.item(r, 2).setText(
                "LAYOUT_" + mt.camel_to_screaming(name))
            self._lay_tbl.blockSignals(False)

    def _on_ts_cell(self, r, c):
        pass

    # -- build plan + import --
    def _action_of(self, combo: QComboBox) -> str:
        txt = combo.currentText()
        if txt.startswith("Skip"):
            return "skip"
        if txt == "Overwrite":
            return "overwrite"
        if txt == "Rename":
            return "rename"
        return "create"

    def _do_import(self):
        if not self._manifest:
            return
        plan = {"maps": {}, "layouts": {}, "tilesets": {}}

        # maps (each keeps its own group unless the row's Group combo changed it)
        for r in range(self._map_tbl.rowCount()):
            orig = self._map_tbl.item(r, 0).text()
            new_name = self._map_tbl.item(r, 1).text().strip() or orig
            new_const = self._map_tbl.item(r, 2).text().strip()
            group = self._map_tbl.cellWidget(r, 3).currentData()
            action = self._action_of(self._map_tbl.cellWidget(r, 4))
            plan["maps"][orig] = {"action": action, "new_name": new_name,
                                  "new_const": new_const, "group": group}
        # layouts
        for r in range(self._lay_tbl.rowCount()):
            oid = self._lay_tbl.item(r, 0).data(Qt.ItemDataRole.UserRole)
            folder = self._lay_tbl.item(r, 1).text().strip()
            const = self._lay_tbl.item(r, 2).text().strip()
            action = self._action_of(self._lay_tbl.cellWidget(r, 3))
            plan["layouts"][oid] = {
                "action": action, "new_folder": folder,
                "new_id": const, "new_name": folder + "_Layout"}
        # tilesets
        primary_overwrites = []
        for r in range(self._ts_tbl.rowCount()):
            label = self._ts_tbl.item(r, 0).data(Qt.ItemDataRole.UserRole)
            is_primary = bool(self._ts_tbl.item(r, 0).data(
                Qt.ItemDataRole.UserRole + 1))
            action = self._action_of(self._ts_tbl.cellWidget(r, 2))
            entry = {"action": action}
            if action == "rename":
                newname = self._ts_tbl.item(r, 3).text().strip()
                if not newname:
                    QMessageBox.warning(
                        self, "Rename needs a name",
                        f"Tileset {label} is set to Rename but no new name "
                        f"was given.")
                    return
                entry["new_suffix"] = newname.replace("gTileset_", "")
                entry["new_folder"] = mt.camel_to_screaming(
                    entry["new_suffix"]).lower()
            if action == "overwrite" and is_primary:
                primary_overwrites.append(label)
            plan["tilesets"][label] = entry

        n_maps = sum(1 for v in plan["maps"].values() if v["action"] != "skip")

        # Loud warning: overwriting a PRIMARY tileset changes every map in the
        # target that is built on it — not just the maps you're importing.
        if primary_overwrites:
            details = "\n".join(
                f"  • {lbl} — {mt.tileset_usage_count(self._root, lbl)} "
                f"layout(s) in this project use it"
                for lbl in primary_overwrites)
            warn = QMessageBox.warning(
                self, "Overwriting shared (primary) tileset(s)",
                "You chose to OVERWRITE these primary tilesets. Primary "
                "tilesets are shared — overwriting one changes how EVERY map "
                "built on it looks, not only the maps you're importing:\n\n"
                f"{details}\n\nUnless you know both projects use an identical "
                "primary tileset, choose 'Skip (reuse existing)' instead.\n\n"
                "Overwrite anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if warn != QMessageBox.StandardButton.Yes:
                return

        ok = QMessageBox.question(
            self, "Import into project?",
            f"This will write {n_maps} map(s) plus their layouts and tilesets "
            f"into:\n{self._root}\n\nExisting files are only touched where you "
            f"chose Overwrite. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ok != QMessageBox.StandardButton.Yes:
            return

        steps = (len(self._manifest.get("tilesets", [])) +
                 len(self._manifest.get("layouts", [])) +
                 len(self._manifest.get("maps", [])) + 1)
        prog = QProgressDialog("Importing…", None, 0, steps, self)
        prog.setWindowTitle("Importing")
        prog.setWindowModality(Qt.WindowModality.WindowModal)
        prog.setMinimumDuration(0)
        prog.setValue(0)

        def cb(i, total, msg):
            prog.setMaximum(total + 1)
            prog.setValue(i)
            prog.setLabelText(msg)
            QApplication.processEvents()

        try:
            rep = mt.import_bundle(self._root, self._bundle_path, plan,
                                   progress=cb,
                                   strip_connections=self._strip_conn.isChecked())
        except Exception as e:
            prog.close()
            _log.exception("import failed")
            QMessageBox.critical(self, "Import failed", str(e))
            return
        prog.setValue(prog.maximum())
        prog.close()

        lines = [
            f"Maps added: {', '.join(rep.added_maps) or '(none)'}",
            f"Layouts added: {', '.join(rep.added_layouts) or '(none)'}",
            f"Tilesets added: {', '.join(rep.added_tilesets) or '(none)'}",
        ]
        if rep.skipped:
            lines.append("Skipped: " + ", ".join(rep.skipped))
        if rep.warnings:
            lines.append("\nNotes:")
            lines += [f"• {w}" for w in dict.fromkeys(rep.warnings)]
        if rep.errors:
            lines.append("\nErrors:")
            lines += [f"✗ {e}" for e in rep.errors]
        box = QMessageBox(self)
        box.setWindowTitle("Import complete" if not rep.errors
                           else "Import finished with errors")
        box.setIcon(QMessageBox.Icon.Warning if rep.errors
                    else QMessageBox.Icon.Information)
        box.setText("\n".join(lines))
        box.exec()
        self.imported.emit()
        # refresh collision state (things now exist)
        self._collisions = mt.detect_collisions(self._root, self._manifest)


# ============================================================================
# Top-level tab
# ============================================================================

class MapTransferTab(QWidget):
    """Export/import maps between decomp projects."""

    modified = pyqtSignal()          # kept for main-window wiring symmetry
    project_changed = pyqtSignal()   # emitted after an import writes to disk

    def __init__(self, parent=None):
        super().__init__(parent)
        self._root = ""
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        self._tabs = QTabWidget()
        self._export = _ExportPanel()
        self._import = _ImportPanel()
        self._import.imported.connect(self.project_changed.emit)
        self._tabs.addTab(self._export, "Export")
        self._tabs.addTab(self._import, "Import")
        v.addWidget(self._tabs)

    def set_project(self, root: str):
        self._root = root or ""
        self._export.set_project(self._root)
        self._import.set_project(self._root)

    def load(self):
        self._export.load()
        self._import.load()
