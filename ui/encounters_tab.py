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
    QAbstractItemView, QCheckBox, QComboBox, QCompleter, QFrame, QGridLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMenu, QMessageBox, QPushButton, QScrollArea, QSpinBox,
    QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from core.encounter_edit import CATEGORY_ORDER, EncounterProject
from core.time_of_day import parse_time_of_day
from core.fishing_rod_reach import read_rod_reach, write_rod_reach, RODS
from core.map_transfer import scan_maps

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
_TIP_ROD = ("Which rod catches this slot. The rod is fixed by the slot's "
            "position in the engine, so to require a specific rod, put the "
            "Pokémon in one of that rod's rows. Read-only.")
_TIP_SPLIT = ("Give this category its own table for each time of day, so its "
              "Pokémon can change with the clock.")


def _friendly_map(const: str) -> str:
    s = const[4:] if const.startswith("MAP_") else const
    return s.replace("_", " ").title()


def _prettify_name(raw: str) -> str:
    """A map FOLDER name (the project's real name) → a spaced display name, e.g.
    "CeladonCity_Condominiums_1F" → "Celadon City Condominiums 1F". Splits both
    underscores and camelCase, but leaves floor tags like "1F"/"2F" intact."""
    import re
    parts = []
    for chunk in raw.split("_"):
        if not chunk:
            continue
        s = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", chunk)
        s = re.sub(r"(?<=[a-z])(?=[0-9])", " ", s)
        parts.append(s)
    return " ".join(parts) or raw


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

        # Fishing gets a read-only "Rod" column: its slots are split across
        # Old / Good / Super Rod BY POSITION in the engine, so the rod each slot
        # needs is fixed — this labels which run of rows belongs to which rod.
        # The column exists for every category (so the data columns keep the
        # same indices everywhere) but is only shown for fishing.
        is_fishing = (cat_key == "fishing_mons")
        rod_labels = tab._fishing_rod_labels() if is_fishing else []

        mons = table.get("mons", [])
        grid = QTableWidget(len(mons), 7)
        grid.setHorizontalHeaderLabels(
            ["", "Rod", "Species", "Min", "Max", "Slot Ratio", "Chance"])
        for col, tip in ((1, _TIP_ROD), (2, _TIP_SPECIES), (3, _TIP_MIN),
                         (4, _TIP_MAX), (5, _TIP_RATIO), (6, _TIP_CHANCE)):
            hi = grid.horizontalHeaderItem(col)
            if hi is not None:
                hi.setToolTip(tip)
        grid.setColumnHidden(1, not is_fishing)
        grid.verticalHeader().setVisible(False)
        grid.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        grid.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch)
        self._grid = grid
        self._combos = []
        self._icons = []
        for i, mon in enumerate(mons):
            icon = QLabel()
            icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            grid.setCellWidget(i, 0, icon)
            self._icons.append(icon)
            rod = QTableWidgetItem(rod_labels[i] if i < len(rod_labels) else "")
            rod.setFlags(Qt.ItemFlag.ItemIsEnabled)
            rod.setToolTip(_TIP_ROD)
            grid.setItem(i, 1, rod)
            combo = QComboBox()
            # Share ONE model across every picker — repopulating 490 species
            # into ~32 combos on every map click would lag noticeably.
            combo.setModel(tab._species_model())
            # Type-ahead: editable + a substring, case-insensitive completer, so
            # you can type "char" to find Charmander etc. NoInsert keeps typed
            # text from being added to the shared model.
            combo.setEditable(True)
            combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            comp = combo.completer()
            comp.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
            comp.setFilterMode(Qt.MatchFlag.MatchContains)
            comp.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            combo.setToolTip(_TIP_SPECIES)
            self._select_species(combo, mon.get("species"))
            combo.currentIndexChanged.connect(
                lambda _n, r=i: self._on_species(r))
            combo.lineEdit().editingFinished.connect(
                lambda c=combo, r=i: self._finish_species_edit(c, r))
            grid.setCellWidget(i, 2, combo)
            self._combos.append(combo)
            self._refresh_icon(i)
            mn = QSpinBox(); mn.setRange(1, 100)
            mn.setValue(int(mon.get("min_level", 1)))
            mn.setToolTip(_TIP_MIN)
            mn.valueChanged.connect(lambda _n, r=i: self._on_level(r))
            grid.setCellWidget(i, 3, mn)
            mx = QSpinBox(); mx.setRange(1, 100)
            mx.setValue(int(mon.get("max_level", 1)))
            mx.setToolTip(_TIP_MAX)
            mx.valueChanged.connect(lambda _n, r=i: self._on_level(r))
            grid.setCellWidget(i, 4, mx)
            ratio = ratios[i] if i < len(ratios) else 0
            ri = QTableWidgetItem(str(ratio))
            ri.setFlags(Qt.ItemFlag.ItemIsEnabled)
            ri.setToolTip(_TIP_RATIO)
            grid.setItem(i, 5, ri)
            ci = QTableWidgetItem("%.1f%%" % (100.0 * ratio / total))
            ci.setFlags(Qt.ItemFlag.ItemIsEnabled)
            ci.setToolTip(_TIP_CHANCE)
            grid.setItem(i, 6, ci)
            grid.setRowHeight(i, 28)
        grid.resizeColumnsToContents()
        grid.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch)
        v.addWidget(grid)

    def _mn(self, r):  # the min/max spinboxes
        return (self._grid.cellWidget(r, 3), self._grid.cellWidget(r, 4))

    def relabel_rods(self, labels):
        """Refresh the read-only Rod column (col 1) after a reach change."""
        for i in range(self._grid.rowCount()):
            it = self._grid.item(i, 1)
            if it is not None and i < len(labels):
                it.setText(labels[i])

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
        const = self._combos[r].currentData()
        if not const:                       # invalid / half-typed text — ignore
            return
        self._table["mons"][r]["species"] = const
        self._refresh_icon(r)
        self._tab._on_edited()

    def _finish_species_edit(self, combo, r):
        """When focus leaves an editable species picker: commit an exact-name
        match, otherwise snap the text back to the slot's committed species so a
        half-typed entry can never be left showing."""
        text = combo.currentText().strip()
        idx = combo.findText(text, Qt.MatchFlag.MatchFixedString)
        if idx >= 0:
            combo.setCurrentIndex(idx)      # fires _on_species if it changed
        else:
            const = self._table["mons"][r].get("species")
            j = combo.findData(const)
            combo.setEditText(combo.itemText(j) if j >= 0 else "")

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
        self._rod_reach = None
        self._rod_reach_dirty = False
        self._map_name = {}
        self._cat_tabs = None
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
        self._hide_empty = QCheckBox("Hide maps with no encounters")
        self._hide_empty.setToolTip(
            "Hide the maps that have no encounter tables yet, so the list shows "
            "only maps you've already set up.")
        self._hide_empty.toggled.connect(self._apply_filter)
        lv.addWidget(self._hide_empty)
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
            self._rod_reach = None              # {ROD: maxSlot} or None (no minigame)
            self._rod_reach_dirty = False
            self._map_name = {}                 # {MAP_const: folder name}
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
                # Present only for a hack whose custom fishing minigame ships a
                # rod-tier table; None for vanilla → the reach editor stays hidden.
                self._rod_reach = read_rod_reach(self._project_dir)
                # EVERY map in the project (real folder names), so maps with no
                # encounters yet still appear and can be given some.
                try:
                    self._map_name = {m.const: m.name
                                      for m in scan_maps(self._project_dir)
                                      if m.const}
                except Exception:
                    _log.warning("Could not scan project maps", exc_info=True)
                    self._map_name = {}
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

    def _display_name(self, const: str) -> str:
        """The map's real name — its folder name from the project — falling back
        to a title-cased constant when the project didn't scan (or the const
        isn't a known map)."""
        folder = self._map_name.get(const)
        return _prettify_name(folder) if folder else _friendly_map(const)

    def _populate_map_list(self):
        self._list.clear()
        vis = self._visible_entries()
        # A few maps have several entries that display the same (the Altering
        # Cave's tables); number them so no two rows look alike.
        counts = {}
        for e in vis:
            counts[self._base_text(e)] = counts.get(self._base_text(e), 0) + 1
        seen = {}
        covered = {e.get("map") for e in self._ep.entries()}
        for entry in vis:
            label = self._entry_label(entry)
            it = QListWidgetItem()
            it.setData(Qt.ItemDataRole.UserRole, label)
            self._set_item_text(it, entry, counts, seen)
            if label in self._dirty_labels:
                it.setBackground(QColor("#3d2e00"))
            self._list.addItem(it)
        # Every OTHER map in the project — no encounter table yet, so it can be
        # given one. Keyed by the map const (never collides with an 's…'
        # base_label). Dimmed so it reads as "empty, click to add".
        for const in sorted(self._map_name):
            if const in covered:
                continue
            it = QListWidgetItem()
            it.setData(Qt.ItemDataRole.UserRole, const)
            it.setData(Qt.ItemDataRole.UserRole + 1, True)   # "no encounters" row
            it.setText("%s  (%s)  — no encounters" % (
                self._display_name(const), const))
            it.setForeground(QColor("#888"))
            if const in self._dirty_labels:
                it.setBackground(QColor("#3d2e00"))
            self._list.addItem(it)
        self._apply_filter()

    def _base_text(self, entry) -> str:
        const = entry.get("map", "?")
        return "%s  (%s)" % (self._display_name(const), const)

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
        hide_empty = self._hide_empty.isChecked()
        for i in range(self._list.count()):
            it = self._list.item(i)
            is_empty = bool(it.data(Qt.ItemDataRole.UserRole + 1))
            it.setHidden((bool(q) and q not in it.text().lower())
                         or (hide_empty and is_empty))

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
            # A project map with no encounter table yet — its row key is the map
            # const. Offer to create one instead of showing a blank panel.
            if isinstance(label, str) and label.startswith("MAP_"):
                self._show_new_map(label)
            return
        self._current_map = entry.get("map")

        # ONE container holds the whole panel, so _clear_detail wipes it whole.
        container = QWidget()
        v = QVBoxLayout(container)
        v.setContentsMargins(0, 0, 0, 0)

        head = QHBoxLayout()
        head.addWidget(QLabel("<b>%s</b>" % self._display_name(entry.get("map", ""))))
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

        self._fishing_tables = []           # rebuilt per map; live-relabel refs
        cat_tabs = QTabWidget()
        self._cat_tabs = cat_tabs           # for restoring selection after a copy
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

    def _show_new_map(self, map_const):
        """Panel for a map that has no encounter table yet: a header and an
        'Add category' that CREATES the entry (only on that click — merely
        viewing a map never writes an empty entry into the file)."""
        self._current_map = map_const
        self._cat_tabs = None
        container = QWidget()
        v = QVBoxLayout(container)
        v.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        head.addWidget(QLabel("<b>%s</b>" % self._display_name(map_const)))
        head.addStretch()
        add = QPushButton("Add category ▾")
        menu = QMenu(add)
        for c in CATEGORY_ORDER:
            menu.addAction(_CATEGORY_LABEL[c],
                           lambda _=False, k=c: self._add_category_to_new_map(
                               map_const, k))
        add.setMenu(menu)
        head.addWidget(add)
        v.addLayout(head)
        v.addWidget(QLabel(
            "This map has no wild encounters yet. Use “Add category” to give it "
            "a Grass, Surfing, Rock Smash, or Fishing table."))
        v.addStretch(1)
        self._detail.addWidget(container)

    def _new_base_label(self, map_const) -> str:
        """A unique C label for a newly-created map entry, from its real folder
        name where known (e.g. MAP_VIRIDIAN_CITY → 'sViridianCity')."""
        folder = self._map_name.get(map_const)
        if folder:
            stem = folder.replace("_", "")
        else:
            stem = "".join(p.title() for p in map_const[4:].split("_"))
        base = "s" + (stem or "Map")
        existing = self._ep.base_labels()
        cand, i = base, 2
        while cand in existing:
            cand, i = "%s_%d" % (base, i), i + 1
        return cand

    def _add_category_to_new_map(self, map_const, cat_key):
        base_label = self._new_base_label(map_const)
        entry = self._ep.add_map_entry(map_const, base_label)
        if entry is None:
            QMessageBox.warning(
                self, "Couldn't add",
                "This wild_encounters.json has no map-keyed group to add to.")
            return
        self._ep.add_category(
            entry, cat_key, self._ep.category_slot_count(cat_key))
        self._populate_map_list()
        self._select_label(base_label)
        self._on_edited()

    def _select_label(self, label):
        for i in range(self._list.count()):
            if self._list.item(i).data(Qt.ItemDataRole.UserRole) == label:
                self._list.setCurrentRow(i)
                return

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

        # Fishing rod reach (only when the project ships the custom minigame's
        # rod-tier table). Global — one set of cutoffs for all maps and phases —
        # so it lives here, above the per-phase / per-map slot tables.
        if cat_key == "fishing_mons" and self._rod_reach is not None:
            v.addWidget(self._build_rod_reach_box())

        if cat_key == "fishing_mons":
            self._fishing_tables = []           # for live Rod-column relabelling
        if EncounterProject.is_split(entry, cat_key):
            phase_tabs = QTabWidget()
            for pk in EncounterProject.phase_keys_of(entry):
                table = EncounterProject.category_table(entry, cat_key, pk)
                st = _SlotTable(self, table, cat_key)
                if cat_key == "fishing_mons":
                    self._fishing_tables.append(st)
                phase_tabs.addTab(st, pk.title())
            v.addWidget(phase_tabs, 1)
            copy_bar = QHBoxLayout()
            copy_bar.addStretch()
            copy_btn = QPushButton("Copy this table to all times")
            copy_btn.setToolTip(
                "Copy the loadout of the time-of-day tab you're on now to every "
                "other time of day, so you don't have to re-enter them.")
            copy_btn.clicked.connect(
                lambda _=False, e=entry, k=cat_key, pt=phase_tabs:
                self._copy_phase_to_all(e, k, pt))
            copy_bar.addWidget(copy_btn)
            v.addLayout(copy_bar)
        else:
            table = EncounterProject.category_table(entry, cat_key)
            st = _SlotTable(self, table, cat_key)
            if cat_key == "fishing_mons":
                self._fishing_tables.append(st)
            v.addWidget(st, 1)
        return w

    def _copy_phase_to_all(self, entry, cat_key, phase_tabs):
        """Copy the currently-viewed time-of-day table over the others."""
        keys = EncounterProject.phase_keys_of(entry)
        idx = phase_tabs.currentIndex()
        if not (0 <= idx < len(keys)):
            return
        src_phase = keys[idx]
        if len(keys) > 1 and QMessageBox.question(
                self, "Copy to all times",
                "Copy the %s %s table to every time of day? This overwrites the "
                "other times’ tables for this category."
                % (src_phase.title(), _CATEGORY_LABEL.get(cat_key, ""))) \
                != QMessageBox.StandardButton.Yes:
            return
        if not self._ep.copy_phase_to_all(entry, cat_key, src_phase):
            return
        # Rebuild the panel with the new data, landing back on the same category
        # tab and time-of-day tab the user copied from.
        cat_idx = self._cat_tabs.currentIndex() if self._cat_tabs else 0
        self._show_entry(self._current_label)
        if self._cat_tabs is not None and 0 <= cat_idx < self._cat_tabs.count():
            self._cat_tabs.setCurrentIndex(cat_idx)
            cur = self._cat_tabs.currentWidget()
            pt = cur.findChild(QTabWidget) if cur is not None else None
            if pt is not None and 0 <= idx < pt.count():
                pt.setCurrentIndex(idx)
        self._on_edited()

    # ── fishing rod reach ─────────────────────────────────────────────────────

    def _fishing_rod_labels(self):
        """Per-slot rod labels for the Fishing table's Rod column.

        With a rod-tier table present the rods are CUMULATIVE, so a slot is
        labelled with every rod that reaches it (e.g. slot 0 → "Old · Good ·
        Super"). Without one (vanilla), fall back to the stock disjoint labels."""
        n = self._ep.category_slot_count("fishing_mons")
        reach = self._rod_reach
        if reach:
            names = (("OLD_ROD", "Old"), ("GOOD_ROD", "Good"),
                     ("SUPER_ROD", "Super"))
            out = []
            for i in range(n):
                rods = [nm for key, nm in names if i <= reach.get(key, -1)]
                out.append(" · ".join(rods) if rods else "—")
            return out
        return self._ep.fishing_rod_labels()

    def _build_rod_reach_box(self) -> QGroupBox:
        """The global 'how far each rod reaches' editor (fishing minigame only).

        Shows one spinbox per rod — "reaches the first N fish" (N = maxSlot + 1).
        Kept in cumulative order Old ≤ Good ≤ Super. Writes to the minigame's
        rod-tier table on Save."""
        n = self._ep.category_slot_count("fishing_mons") or 10
        box = QGroupBox("Rod reach")
        box.setToolTip(
            "How far into the fishing pool each rod reaches. Rods are "
            "cumulative — a stronger rod catches everything a weaker one can, "
            "plus more. Saved into your fishing minigame's rod tiers.")
        g = QGridLayout(box)
        self._reach_spins = {}
        labels = {"OLD_ROD": "Old Rod", "GOOD_ROD": "Good Rod",
                  "SUPER_ROD": "Super Rod"}
        for col, rod in enumerate(RODS):
            g.addWidget(QLabel(labels[rod] + " reaches the first"), 0, col * 2)
            sp = QSpinBox()
            sp.setRange(1, n)
            sp.setValue(int(self._rod_reach.get(rod, 0)) + 1)   # maxSlot → count
            sp.setSuffix(" fish")
            sp.valueChanged.connect(
                lambda _v, r=rod: self._on_rod_reach_changed(r))
            g.addWidget(sp, 0, col * 2 + 1)
            self._reach_spins[rod] = sp
        return box

    def _on_rod_reach_changed(self, rod):
        if self._loading or self._rod_reach is None:
            return
        spins = getattr(self, "_reach_spins", {})
        if not spins:
            return
        old = spins["OLD_ROD"].value()
        good = spins["GOOD_ROD"].value()
        sup = spins["SUPER_ROD"].value()
        # Keep the cutoffs in order, anchored on the one the user just moved.
        if rod == "OLD_ROD":
            good = max(good, old); sup = max(sup, good)
        elif rod == "GOOD_ROD":
            old = min(old, good); sup = max(sup, good)
        else:
            good = min(good, sup); old = min(old, good)
        new = {"OLD_ROD": old, "GOOD_ROD": good, "SUPER_ROD": sup}
        for r, sp in spins.items():
            if sp.value() != new[r]:
                sp.blockSignals(True); sp.setValue(new[r]); sp.blockSignals(False)
        changed = False
        for r in RODS:
            ms = new[r] - 1                      # count → maxSlot (0-based)
            if self._rod_reach.get(r) != ms:
                self._rod_reach[r] = ms; changed = True
        if changed:
            self._rod_reach_dirty = True
            self._on_edited()
            # Update the Rod column of the visible fishing table(s) in place — no
            # panel rebuild, so the user isn't bumped off the Fishing tab.
            labels = self._fishing_rod_labels()
            for st in getattr(self, "_fishing_tables", []):
                st.relabel_rods(labels)

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
            # Fishing rod reach → the minigame's rod-tier table (only when the
            # user actually changed it; byte-identical otherwise).
            if self._rod_reach_dirty and self._rod_reach is not None:
                if write_rod_reach(self._project_dir, self._rod_reach):
                    self._rod_reach_dirty = False
        except Exception:
            _log.warning("Encounter save failed", exc_info=True)
            return 0, ["wild_encounters.json"]
        for label in list(self._dirty_labels):
            self._paint_row(label, False)
        self._dirty_labels.clear()
        return (1 if wrote else 0), []
