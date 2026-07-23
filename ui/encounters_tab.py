"""Wild-encounters editor — grass / surf / rock-smash / fishing per map, each
category independently constant or split by time of day.

Layout mirrors Porymap: a map list on the left; on the right the selected map's
categories as TABS, each a slot table (species with its mini-sprite, level range,
and the engine's fixed slot ratio / encounter chance shown read-only). A category
can be added or removed. On a project with a day/night clock each category also
gets a "Split by time of day" toggle that fans it into Morning / Day / Night
sub-tabs — independently, so a cave's water can stay constant while its grass
varies.

Data + engine work is delegated to core.encounter_edit / core.time_of_day /
core.wild_encounter_tod_patch. Edits write straight through into the loaded data
so the byte-identical save guarantee holds.
"""

from __future__ import annotations

import logging
import os

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFrame, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox,
    QPushButton, QScrollArea, QSpinBox, QSplitter, QTableWidget,
    QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from core.encounter_edit import CATEGORY_ORDER, EncounterProject
from core.time_of_day import parse_time_of_day

try:
    from ui.custom_widgets.scroll_guard import install_scroll_guard_recursive
except Exception:                                   # pragma: no cover
    install_scroll_guard_recursive = None

_log = logging.getLogger("PorySuite.Encounters")

_CATEGORY_LABEL = {
    "land_mons": "Grass", "water_mons": "Surfing",
    "rock_smash_mons": "Rock Smash", "fishing_mons": "Fishing",
}

# Hover help for the slot grid.
_TIP_RATE = ("How often walking here triggers a wild encounter (0–255). "
             "Higher means more frequent; 0 means no encounters at all.")
_TIP_SPECIES = "The Pokémon that can appear in this slot."
_TIP_MIN = "The lowest level this Pokémon appears at."
_TIP_MAX = "The highest level this Pokémon appears at."
_TIP_RATIO = ("This slot's fixed weight in the encounter roll. The game engine "
              "hard-codes these and they're the same on every map, so it's "
              "read-only.")
_TIP_CHANCE = ("How likely this slot is, as a share of the whole table — worked "
               "out from the slot ratios. Read-only.")
_TIP_SPLIT = ("Give this category its own table for each time of day, so its "
              "Pokémon can change with the clock.")


def _friendly_map(const: str) -> str:
    s = const[4:] if const.startswith("MAP_") else const
    return s.replace("_", " ").title()


class _SlotTable(QWidget):
    """A category's encounter rate + slot grid, bound to one `{encounter_rate,
    mons}` table. Edits write straight through into that dict."""

    def __init__(self, tab, table: dict, cat_key: str):
        super().__init__()
        self._tab = tab
        self._table = table
        self._cat = cat_key
        ratios = tab._ep.field_ratios(cat_key)
        total = sum(ratios) or 1

        v = QVBoxLayout(self)
        v.setContentsMargins(2, 2, 2, 2)
        top = QHBoxLayout()
        rate_lbl = QLabel("Encounter Rate")
        rate_lbl.setToolTip(_TIP_RATE)
        top.addWidget(rate_lbl)
        self._rate = QSpinBox()
        self._rate.setRange(0, 255)
        self._rate.setValue(int(table.get("encounter_rate", 0)))
        self._rate.setToolTip(_TIP_RATE)
        self._rate.valueChanged.connect(self._on_rate)
        top.addWidget(self._rate)
        top.addStretch()
        v.addLayout(top)

        mons = table.get("mons", [])
        grid = QTableWidget(len(mons), 6)
        grid.setHorizontalHeaderLabels(
            ["", "Species", "Min", "Max", "Slot Ratio", "Chance"])
        for col, tip in ((1, _TIP_SPECIES), (2, _TIP_MIN), (3, _TIP_MAX),
                         (4, _TIP_RATIO), (5, _TIP_CHANCE)):
            hi = grid.horizontalHeaderItem(col)
            if hi is not None:
                hi.setToolTip(tip)
        grid.verticalHeader().setVisible(False)
        grid.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        grid.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        self._grid = grid
        self._combos = []
        self._icons = []
        for i, mon in enumerate(mons):
            icon = QLabel()
            icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            grid.setCellWidget(i, 0, icon)
            self._icons.append(icon)
            combo = QComboBox()
            # Share ONE model across every picker — repopulating 490 species
            # into ~32 combos on every map click would lag noticeably.
            combo.setModel(tab._species_model())
            combo.setToolTip(_TIP_SPECIES)
            self._select_species(combo, mon.get("species"))
            combo.currentIndexChanged.connect(
                lambda _n, r=i: self._on_species(r))
            grid.setCellWidget(i, 1, combo)
            self._combos.append(combo)
            self._refresh_icon(i)
            mn = QSpinBox(); mn.setRange(1, 100)
            mn.setValue(int(mon.get("min_level", 1)))
            mn.setToolTip(_TIP_MIN)
            mn.valueChanged.connect(lambda _n, r=i: self._on_level(r))
            grid.setCellWidget(i, 2, mn)
            mx = QSpinBox(); mx.setRange(1, 100)
            mx.setValue(int(mon.get("max_level", 1)))
            mx.setToolTip(_TIP_MAX)
            mx.valueChanged.connect(lambda _n, r=i: self._on_level(r))
            grid.setCellWidget(i, 3, mx)
            ratio = ratios[i] if i < len(ratios) else 0
            ri = QTableWidgetItem(str(ratio))
            ri.setFlags(Qt.ItemFlag.ItemIsEnabled)
            ri.setToolTip(_TIP_RATIO)
            grid.setItem(i, 4, ri)
            ci = QTableWidgetItem("%.1f%%" % (100.0 * ratio / total))
            ci.setFlags(Qt.ItemFlag.ItemIsEnabled)
            ci.setToolTip(_TIP_CHANCE)
            grid.setItem(i, 5, ci)
            grid.setRowHeight(i, 28)
        grid.resizeColumnsToContents()
        grid.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        v.addWidget(grid)

    def _mn(self, r):  # the min/max spinboxes
        return (self._grid.cellWidget(r, 2), self._grid.cellWidget(r, 3))

    @staticmethod
    def _select_species(combo, const):
        idx = combo.findData(const)
        if idx < 0:
            combo.addItem(const or "?", const)
            idx = combo.count() - 1
        combo.setCurrentIndex(idx)

    def _refresh_icon(self, r):
        const = self._combos[r].currentData()
        icon = None
        mw = self._tab._mw
        if mw is not None and hasattr(mw, "_species_list_icon"):
            try:
                icon = mw._species_list_icon(const)
            except Exception:
                icon = None
        if icon is not None:
            self._icons[r].setPixmap(icon.pixmap(24, 24))
        else:
            self._icons[r].setText("")

    def _on_rate(self, _v):
        self._table["encounter_rate"] = self._rate.value()
        self._tab._on_edited()

    def _on_species(self, r):
        self._table["mons"][r]["species"] = self._combos[r].currentData()
        self._refresh_icon(r)
        self._tab._on_edited()

    def _on_level(self, r):
        mn, mx = self._mn(r)
        self._table["mons"][r]["min_level"] = mn.value()
        self._table["mons"][r]["max_level"] = mx.value()
        self._tab._on_edited()


class EncountersTab(QWidget):
    """Left: map list. Right: the selected map's category tabs."""

    def __init__(self, mainwindow=None, parent=None):
        super().__init__(parent)
        self._mw = mainwindow
        self._project_dir = ""
        self._ep = None
        self._cap = None
        self._loading = False
        self._current_map = None
        self._current_label = None
        self._species_cache = None
        self._species_model_cache = None
        self._dirty_labels = set()      # map labels whose data was edited

        root = QVBoxLayout(self)
        self._note = QLabel("")
        self._note.setWordWrap(True)
        self._note.setStyleSheet("color:#8fc; padding:4px;")
        root.addWidget(self._note)

        split = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(split, 1)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search maps…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_filter)
        lv.addWidget(self._search)
        self._list = QListWidget()
        self._list.setMinimumWidth(240)
        self._list.currentItemChanged.connect(self._on_map_changed)
        lv.addWidget(self._list, 1)
        split.addWidget(left)

        self._detail_scroll = QScrollArea()
        self._detail_scroll.setWidgetResizable(True)
        self._detail_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._detail_host = QWidget()
        self._detail = QVBoxLayout(self._detail_host)
        self._detail_scroll.setWidget(self._detail_host)
        split.addWidget(self._detail_scroll)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        # No per-tab Save button: this tab follows the app's model — edits live
        # in RAM, the section + the edited map rows show amber, and the toolbar/
        # File → Save writes everything via flush_to_disk().

    # ── lifecycle ───────────────────────────────────────────────────────────

    @staticmethod
    def is_supported(project_dir: str) -> bool:
        return bool(project_dir) and os.path.isfile(os.path.join(
            project_dir, "src", "data", "wild_encounters.json"))

    def load_project(self, project_info: dict) -> None:
        self.load((project_info or {}).get("dir", ""))

    def clear_project(self) -> None:
        self._project_dir = ""
        self.load("")

    def _species_list(self):
        """[(SPECIES_const, display name), …] for the pickers — EVERY species,
        not just the ones already in a slot.

        The MainWindow holds the data as `source_data` (a PokemonDataManager);
        `get_pokemon_data()` returns the full `{const: {...}}` map. (An earlier
        version looked for a `project_data` attribute that MainWindow doesn't
        have, so it fell through to a one-item fallback and every dropdown held
        only its own current species.)
        """
        if self._species_cache is not None:
            return self._species_cache
        out = None
        sd = getattr(self._mw, "source_data", None)
        if sd is not None:
            try:
                data = sd.get_pokemon_data() or {}

                def _name(const, sp=None):
                    if sp is None and isinstance(data.get(const), dict):
                        sp = data[const]
                    return (sp.get("name") if isinstance(sp, dict) else None) \
                        or const.replace("SPECIES_", "").replace("_", " ").title()

                out = [("SPECIES_NONE", "None")]
                seen = {"SPECIES_NONE"}
                # National-dex order — the same order the app's other species
                # pickers use — not the data manager's raw parse order.
                for entry in (sd.get_national_dex() or []):
                    const = entry.get("species") if isinstance(entry, dict) \
                        else None
                    if not const or const in seen:
                        continue
                    seen.add(const)
                    out.append((const, _name(const)))
                # Anything not in the dex (extra forms, unlisted species) after.
                for const, sp in data.items():
                    if const not in seen:
                        out.append((const, _name(const, sp)))
            except Exception:
                _log.warning("Could not read species list", exc_info=True)
                out = None
        if not out or len(out) <= 1:
            out = [("SPECIES_NONE", "None")]
        self._species_cache = out
        return out

    def _species_model(self):
        """One shared model of every species, built once and reused by all the
        slot pickers (setModel), so selecting a map doesn't repopulate hundreds
        of items into dozens of combos."""
        if self._species_model_cache is None:
            m = QStandardItemModel(self)
            for const, name in self._species_list():
                it = QStandardItem(name)
                it.setData(const, Qt.ItemDataRole.UserRole)
                m.appendRow(it)
            self._species_model_cache = m
        return self._species_model_cache

    def load(self, project_dir: str = "") -> None:
        self._loading = True
        try:
            if project_dir:
                self._project_dir = project_dir
            self._ep = None
            self._cap = None
            self._current_map = None
            self._species_cache = None
            self._species_model_cache = None
            self._dirty_labels.clear()          # F5 / reload drops dirty state
            self._list.clear()
            self._clear_detail()
            if self._mw is not None:
                try:
                    self._mw.sectionDirtyChanged.emit("encounters", False)
                except Exception:
                    pass
            if not self._project_dir or not self.is_supported(self._project_dir):
                self._note.setText("This project has no wild-encounter data.")
                return
            try:
                self._ep = EncounterProject.load(self._project_dir)
                self._cap = parse_time_of_day(self._project_dir)
            except Exception:
                _log.warning("Could not load encounters", exc_info=True)
                self._note.setText("Could not read the encounter data.")
                self._ep = None
                return
            if self._cap and self._cap.present:
                # Day/night is mentioned ONLY when the project has that clock;
                # a vanilla project must never see time-of-day wording.
                self._note.setText(
                    "Pick a map. Each category tab can be split into %s with "
                    "its own tables — independently of the others."
                    % " / ".join(p.key.title()
                                 for p in self._cap.active_phases))
            else:
                self._note.setText(
                    "Pick a map to edit its wild Pokémon.")
            self._populate_map_list()
            if self._ep.is_foreign_format:
                self._note.setText(self._note.text() + "  (Note: this file is "
                                   "formatted unusually; saving will normalise "
                                   "it.)")
        finally:
            self._loading = False

    # ── map list ─────────────────────────────────────────────────────────────

    @staticmethod
    def _entry_label(entry) -> str:
        """The unique key for an entry — its base_label. Two entries can share a
        map const (a FireRed and a LeafGreen table for the same map), so the map
        const is NOT unique and must never be used to look an entry up."""
        return entry.get("base_label") or entry.get("map", "?")

    @staticmethod
    def _is_leafgreen(entry) -> bool:
        return "LeafGreen" in (entry.get("base_label") or "")

    def _visible_entries(self):
        """Entries shown in the list. This is a FireRed editor, so LeafGreen
        tables are hidden (they're #ifdef'd out of a FireRed build). They stay
        in the file untouched — hidden, not deleted."""
        return [e for e in self._ep.entries() if not self._is_leafgreen(e)]

    def entry_by_label(self, label):
        for e in self._ep.entries():
            if self._entry_label(e) == label:
                return e
        return None

    def _populate_map_list(self):
        self._list.clear()
        vis = self._visible_entries()
        # A few maps have several entries that display the same (the Altering
        # Cave's tables); number them so no two rows look alike.
        counts = {}
        for e in vis:
            counts[self._base_text(e)] = counts.get(self._base_text(e), 0) + 1
        seen = {}
        for entry in vis:
            label = self._entry_label(entry)
            it = QListWidgetItem()
            it.setData(Qt.ItemDataRole.UserRole, label)
            self._set_item_text(it, entry, counts, seen)
            if label in self._dirty_labels:
                it.setBackground(QColor("#3d2e00"))
            self._list.addItem(it)
        self._apply_filter()

    @staticmethod
    def _base_text(entry) -> str:
        const = entry.get("map", "?")
        return "%s  (%s)" % (_friendly_map(const), const)

    def _set_item_text(self, it, entry, counts=None, seen=None):
        base = self._base_text(entry)
        if counts and counts.get(base, 0) > 1:
            if seen is None:
                seen = {}
            seen[base] = seen.get(base, 0) + 1
            base = "%s  #%d" % (base, seen[base])
        it.setText(("⏱ " + base) if EncounterProject.is_time_aware(entry)
                   else base)

    def _apply_filter(self):
        q = (self._search.text() or "").strip().lower()
        for i in range(self._list.count()):
            it = self._list.item(i)
            it.setHidden(bool(q) and q not in it.text().lower())

    # ── detail ────────────────────────────────────────────────────────────────

    def _clear_detail(self):
        """Remove EVERYTHING from the detail area. The whole panel is a single
        container widget, so deleting that one child takes its entire subtree —
        no orphaned labels left parented to the host (the overlap bug)."""
        while self._detail.count():
            item = self._detail.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def _on_map_changed(self, cur, _prev):
        if self._loading or cur is None:
            return
        self._show_entry(cur.data(Qt.ItemDataRole.UserRole))

    def _show_entry(self, label):
        self._current_label = label
        self._clear_detail()
        entry = self.entry_by_label(label)
        if entry is None:
            return
        self._current_map = entry.get("map")

        # ONE container holds the whole panel, so _clear_detail wipes it whole.
        container = QWidget()
        v = QVBoxLayout(container)
        v.setContentsMargins(0, 0, 0, 0)

        head = QHBoxLayout()
        head.addWidget(QLabel("<b>%s</b>" % _friendly_map(entry.get("map", ""))))
        head.addStretch()
        add = QPushButton("Add category ▾")
        missing = [c for c in CATEGORY_ORDER if c not in entry]
        add.setEnabled(bool(missing))
        menu = QMenu(add)
        for c in missing:
            menu.addAction(_CATEGORY_LABEL[c],
                           lambda _=False, k=c: self._add_category(entry, k))
        add.setMenu(menu)
        head.addWidget(add)
        v.addLayout(head)

        cat_tabs = QTabWidget()
        for cat_key in CATEGORY_ORDER:
            if cat_key in entry:
                cat_tabs.addTab(self._build_category(entry, cat_key),
                                _CATEGORY_LABEL[cat_key])
        if cat_tabs.count() == 0:
            v.addWidget(QLabel("This map has no encounter tables. Use "
                               "“Add category”."))
        v.addWidget(cat_tabs, 1)
        self._detail.addWidget(container)

        if install_scroll_guard_recursive:
            try:
                install_scroll_guard_recursive(self._detail_host)
            except Exception:
                pass

    def _build_category(self, entry, cat_key) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        bar = QHBoxLayout()
        if self._cap and self._cap.present:
            chk = QCheckBox("Split by time of day")
            chk.setToolTip(_TIP_SPLIT)
            chk.setChecked(EncounterProject.is_split(entry, cat_key))
            chk.toggled.connect(
                lambda on, k=cat_key: self._toggle_split(entry, k, on))
            bar.addWidget(chk)
        bar.addStretch()
        rm = QPushButton("Remove this category")
        rm.clicked.connect(lambda: self._remove_category(entry, cat_key))
        bar.addWidget(rm)
        v.addLayout(bar)

        if EncounterProject.is_split(entry, cat_key):
            phase_tabs = QTabWidget()
            for pk in EncounterProject.phase_keys_of(entry):
                table = EncounterProject.category_table(entry, cat_key, pk)
                phase_tabs.addTab(_SlotTable(self, table, cat_key), pk.title())
            v.addWidget(phase_tabs, 1)
        else:
            table = EncounterProject.category_table(entry, cat_key)
            v.addWidget(_SlotTable(self, table, cat_key), 1)
        return w

    # ── structural edits ──────────────────────────────────────────────────────

    def _toggle_split(self, entry, cat_key, on):
        if self._loading:
            return
        if on:
            self._ep.split_category(
                entry, cat_key, [p.key for p in self._cap.active_phases])
        else:
            keep = EncounterProject.phase_keys_of(entry)
            if keep and QMessageBox.question(
                    self, "Merge category",
                    "Keep the “%s” table for %s and discard the other phases?"
                    % (keep[0].title(), _CATEGORY_LABEL[cat_key])) \
                    != QMessageBox.StandardButton.Yes:
                self._show_entry(self._entry_label(entry))   # revert checkbox
                return
            if keep:
                self._ep.merge_category(entry, cat_key, keep[0])
        self._after_structural(entry)

    def _add_category(self, entry, cat_key):
        self._ep.add_category(
            entry, cat_key, self._ep.category_slot_count(cat_key))
        self._after_structural(entry)

    def _remove_category(self, entry, cat_key):
        if QMessageBox.question(
                self, "Remove category",
                "Remove the %s table from this map?"
                % _CATEGORY_LABEL[cat_key]) != QMessageBox.StandardButton.Yes:
            return
        self._ep.remove_category(entry, cat_key)
        self._after_structural(entry)

    def _after_structural(self, entry):
        label = self._entry_label(entry)
        self._show_entry(label)
        for i in range(self._list.count()):
            it = self._list.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == label:
                self._set_item_text(it, entry)
                break
        self._on_edited()

    # ── dirty / save ──────────────────────────────────────────────────────────

    def _on_edited(self):
        if self._loading:
            return
        # Mark the edited map amber and flag the section dirty; the edit stays
        # in RAM until the toolbar/File Save calls flush_to_disk().
        if self._current_label is not None:
            self._dirty_labels.add(self._current_label)
            self._paint_row(self._current_label, True)
        if self._mw is not None:
            try:
                self._mw.setWindowModified(True)
                self._mw.sectionDirtyChanged.emit("encounters", True)
            except Exception:
                pass

    def _paint_row(self, label, dirty):
        for i in range(self._list.count()):
            it = self._list.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == label:
                it.setBackground(QColor("#3d2e00") if dirty
                                 else QColor(0, 0, 0, 0))
                break

    def flush_to_disk(self):
        """Write pending encounter edits. Called by the app's save pipeline.

        Returns (files_written, [errors]) like the other tabs. Clears the amber
        markers on success. Installs the time-of-day engine scaffolding when any
        map is time-aware."""
        if self._ep is None:
            return 0, []
        try:
            wrote = self._ep.save()
            if self._cap and self._cap.present and any(
                    EncounterProject.is_time_aware(e)
                    for e in self._ep.entries()):
                from core.wild_encounter_tod_patch import apply as apply_patch
                apply_patch(self._project_dir, self._cap)
        except Exception:
            _log.warning("Encounter save failed", exc_info=True)
            return 0, ["wild_encounters.json"]
        for label in list(self._dirty_labels):
            self._paint_row(label, False)
        self._dirty_labels.clear()
        return (1 if wrote else 0), []
