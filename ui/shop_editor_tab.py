"""
ui/shop_editor_tab.py
Shop Editor — browse every shop in the project and edit what each one sells.

A shop is opened in-game by a ``pokemart`` / ``pokemartdecoration`` script
command pointing at an item list.  This tab scans the project source every time
(``core.shop_data.load_shops``) so projects that added or removed shops always
load correctly — nothing is hardcoded.

Layout
------
    Left  — searchable list of every shop (label + map / context).
    Right — the selected shop's stock as an editable, reorderable list, with
            add / remove / move-up / move-down and an item-constant picker
            (DECOR_* for decoration shops).

Dirty handling follows the project's standard internal-tab contract (the
Tilemap Editor is the reference):
    * a ``modified`` signal the main window connects to,
    * per-row amber tinting on the shop list + an amber detail-frame,
    * ``has_unsaved_changes()`` / ``flush_to_disk()`` for the Save-All path,
    * a ``set_project()`` lazy loader and a ``load()`` that fully resets state
      (the F5 / refresh contract).
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QLabel, QLineEdit, QPushButton, QGroupBox, QComboBox,
    QListWidget, QListWidgetItem, QMessageBox, QDialog, QDialogButtonBox,
    QFormLayout,
)

from ui.custom_widgets.scroll_guard import install_scroll_guard
import core.shop_data as shop_data
from core.shop_data import Shop, KIND_MART, KIND_DECOR


# ── File logger (writes to porysuite/shop_editor.log) ─────────────────────────

_log = logging.getLogger("ShopEditor")
_log.setLevel(logging.DEBUG)
try:
    _log_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "shop_editor.log")
    _fh = logging.FileHandler(_log_path, mode="w", encoding="utf-8")
    _fh.setFormatter(
        logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S"))
    _log.addHandler(_fh)
except Exception:  # pragma: no cover — logging must never crash the tab
    pass


# ── Visual constants ──────────────────────────────────────────────────────────

_DIRTY_BG = QColor("#3d2e00")          # amber row tint (matches Sound Editor)
_CLEAR_BG = QColor(0, 0, 0, 0)         # transparent (clears the tint)
_DIRTY_SS = "QGroupBox { border: 1px solid #ffb74d; border-radius: 4px; }"

_ROLE_LABEL = Qt.ItemDataRole.UserRole + 1   # shop label on each list row
_ROLE_CONST = Qt.ItemDataRole.UserRole + 2   # ITEM_*/DECOR_* constant on a stock row


def _prettify_const(const: str) -> str:
    """Fallback display for a constant with no friendly name (ITEM_POKE_BALL ->
    'Poke Ball', DECOR_SMALL_DESK -> 'Small Desk'). Used for decorations (no
    name source) and for any item missing from the project's items.json."""
    s = const
    for pre in ("ITEM_", "DECOR_"):
        if s.startswith(pre):
            s = s[len(pre):]
            break
    return s.replace("_", " ").title() or const


class _ItemPickerDialog(QDialog):
    """Searchable picker over (constant, display) pairs — used to add a stock
    entry or change an existing one. Shows friendly names, returns the chosen
    constant. Filter box + list + double-click-to-accept."""

    def __init__(self, pairs: list[tuple[str, str]], current: str = "",
                 title: str = "Choose item", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(360, 460)
        self._pairs = pairs
        self._chosen: Optional[str] = None

        lay = QVBoxLayout(self)
        self._filter = QLineEdit()
        self._filter.setPlaceholderText("Type to filter…")
        self._filter.textChanged.connect(self._apply_filter)
        lay.addWidget(self._filter)

        self._list = QListWidget()
        self._list.itemDoubleClicked.connect(lambda _i: self._accept())
        lay.addWidget(self._list, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

        self._populate(current)
        self._filter.setFocus()

    def _populate(self, current: str) -> None:
        self._list.clear()
        sel_row = -1
        for const, display in self._pairs:
            it = QListWidgetItem(display)
            it.setData(_ROLE_CONST, const)
            it.setToolTip(const)
            self._list.addItem(it)
            if const == current:
                sel_row = self._list.count() - 1
        if sel_row >= 0:
            self._list.setCurrentRow(sel_row)
        elif self._list.count():
            self._list.setCurrentRow(0)

    def _apply_filter(self, text: str) -> None:
        needle = text.lower().strip()
        first_visible = -1
        for i in range(self._list.count()):
            it = self._list.item(i)
            show = (not needle
                    or needle in it.text().lower()
                    or needle in (it.data(_ROLE_CONST) or "").lower())
            it.setHidden(not show)
            if show and first_visible < 0:
                first_visible = i
        # Keep a sensible selection among visible rows.
        cur = self._list.currentItem()
        if cur is None or cur.isHidden():
            if first_visible >= 0:
                self._list.setCurrentRow(first_visible)

    def _accept(self) -> None:
        it = self._list.currentItem()
        if it is not None and not it.isHidden():
            self._chosen = it.data(_ROLE_CONST)
            self.accept()

    @staticmethod
    def pick(pairs: list[tuple[str, str]], current: str, title: str,
             parent: Optional[QWidget]) -> Optional[str]:
        """Run the dialog modally; return the chosen constant or None."""
        dlg = _ItemPickerDialog(pairs, current, title, parent)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            return dlg._chosen
        return None


class _NewShopDialog(QDialog):
    """Collect the label, kind, and home map for a brand-new shop list."""

    def __init__(self, map_names: list[str], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("New shop")
        self.resize(440, 210)

        lay = QVBoxLayout(self)
        form = QFormLayout()

        self._label_edit = QLineEdit()
        self._label_edit.setPlaceholderText("e.g. ViridianCity_Mart_Items")
        form.addRow("List label:", self._label_edit)

        self._kind_combo = QComboBox()
        install_scroll_guard(self._kind_combo)
        self._kind_combo.addItem("Item shop", KIND_MART)
        self._kind_combo.addItem("Decoration shop", KIND_DECOR)
        form.addRow("Kind:", self._kind_combo)

        self._map_combo = QComboBox()
        install_scroll_guard(self._map_combo)
        self._map_combo.setEditable(True)            # type-to-filter long list
        self._map_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for m in map_names:
            self._map_combo.addItem(m)
        form.addRow("Add list to map:", self._map_combo)

        lay.addLayout(form)

        hint = QLabel(
            "Creates an empty shop list in that map's scripts. To make it open "
            "in-game, wire a <i>pokemart</i> call to an NPC in EVENTide "
            "(use the “Open in EVENTide” button).")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #aaa;")
        lay.addWidget(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def values(self) -> tuple[str, str, str]:
        """(label, kind, map_name) from the fields."""
        return (
            self._label_edit.text().strip(),
            self._kind_combo.currentData() or KIND_MART,
            self._map_combo.currentText().strip(),
        )


class ShopEditorTab(QWidget):
    """Internal tab: list shops, edit each shop's item list, save in place."""

    modified = pyqtSignal()
    # (map_name, mart_label) — ask the main window to open EVENTide on the map
    # whose NPC opens this shop. mart_label is "" for a not-yet-wired shop (just
    # open the map so the user can add a pokemart NPC).
    jump_to_script_requested = pyqtSignal(str, str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._project_root: str = ""

        # In-memory model -----------------------------------------------------
        self._shops: list[Shop] = []           # all shops, parse order
        self._by_label: dict[str, Shop] = {}   # label -> Shop (same objects)
        self._current_label: str = ""          # selected shop label, or ""
        self._dirty_labels: set[str] = set()   # shops with unsaved edits
        self._item_catalog: list[str] = []     # ITEM_* constants (valid set)
        self._decor_catalog: list[str] = []    # DECOR_* constants (valid set)
        # Friendly display names from the app's item loader (project_data
        # .get_items_list()). const -> "Poké Ball"; pairs feed the picker.
        self._item_display: dict[str, str] = {}
        self._item_pairs: list[tuple[str, str]] = []    # (const, display) items
        self._decor_pairs: list[tuple[str, str]] = []   # (const, display) decor

        # Guard so programmatic widget changes during load() don't mark dirty.
        self._loading = False

        self._build_ui()
        # No project yet — create/delete/jump stay disabled until load().
        self._btn_new_shop.setEnabled(False)
        self._btn_del_shop.setEnabled(False)
        self._btn_eventide.setEnabled(False)

    # ═════════════════════════════════════════════════════════════════════════
    # UI construction
    # ═════════════════════════════════════════════════════════════════════════

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        header = QLabel("Shop Editor")
        hf = QFont()
        hf.setPointSize(13)
        hf.setBold(True)
        header.setFont(hf)
        root.addWidget(header)

        sub = QLabel(
            "Every shop in the project, scanned live from the map scripts. "
            "Pick a shop on the left, then add, remove, or reorder what it "
            "sells. Changes are written back into the script files on Save.")
        sub.setWordWrap(True)
        sub.setStyleSheet("color: #aaa;")
        root.addWidget(sub)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter, 1)

        # ── Left: shop list ──────────────────────────────────────────────────
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter shops…")
        self._search.textChanged.connect(self._filter_shop_list)
        ll.addWidget(self._search)

        self._shop_list = QListWidget()
        self._shop_list.currentItemChanged.connect(self._on_shop_selected)
        ll.addWidget(self._shop_list, 1)

        shop_btns = QHBoxLayout()
        self._btn_new_shop = QPushButton("New Shop…")
        self._btn_new_shop.setToolTip(
            "Create a new (empty) shop item list in a map you choose")
        self._btn_new_shop.clicked.connect(self._on_new_shop)
        shop_btns.addWidget(self._btn_new_shop)

        self._btn_del_shop = QPushButton("Delete Shop")
        self._btn_del_shop.setToolTip("Delete the selected shop's item list")
        self._btn_del_shop.clicked.connect(self._on_delete_shop)
        shop_btns.addWidget(self._btn_del_shop)
        ll.addLayout(shop_btns)

        self._count_lbl = QLabel("No project loaded")
        self._count_lbl.setStyleSheet("color: #888;")
        ll.addWidget(self._count_lbl)

        splitter.addWidget(left)

        # ── Right: shop detail (items) ────────────────────────────────────────
        self._detail_box = QGroupBox("Shop stock")
        dl = QVBoxLayout(self._detail_box)

        self._info_lbl = QLabel("Select a shop to edit its stock.")
        self._info_lbl.setWordWrap(True)
        self._info_lbl.setStyleSheet("color: #aaa;")
        dl.addWidget(self._info_lbl)

        self._btn_eventide = QPushButton("Open in EVENTide  ↗")
        self._btn_eventide.setToolTip(
            "Jump to the map + NPC whose script opens this shop. For a "
            "not-yet-wired shop it opens the map so you can add a pokemart NPC.")
        self._btn_eventide.clicked.connect(self._on_open_in_eventide)
        dl.addWidget(self._btn_eventide)

        self._items_list = QListWidget()
        self._items_list.currentRowChanged.connect(self._update_button_states)
        # Double-click a row to change which item it is (in-place edit).
        self._items_list.itemDoubleClicked.connect(
            lambda _i: self._on_edit_item())
        dl.addWidget(self._items_list, 1)

        # Manage row: add / edit / remove / up / down --------------------------
        btn_row = QHBoxLayout()
        self._btn_add = QPushButton("Add…")
        self._btn_add.setToolTip("Add an item to this shop's stock")
        self._btn_add.clicked.connect(self._on_add_item)
        btn_row.addWidget(self._btn_add)

        self._btn_edit = QPushButton("Edit…")
        self._btn_edit.setToolTip(
            "Change the selected item (or double-click it)")
        self._btn_edit.clicked.connect(self._on_edit_item)
        btn_row.addWidget(self._btn_edit)

        self._btn_remove = QPushButton("Remove")
        self._btn_remove.clicked.connect(self._on_remove_item)
        btn_row.addWidget(self._btn_remove)

        self._btn_up = QPushButton("Move Up")
        self._btn_up.clicked.connect(lambda: self._move_item(-1))
        btn_row.addWidget(self._btn_up)

        self._btn_down = QPushButton("Move Down")
        self._btn_down.clicked.connect(lambda: self._move_item(+1))
        btn_row.addWidget(self._btn_down)

        btn_row.addStretch(1)
        dl.addLayout(btn_row)

        splitter.addWidget(self._detail_box)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

        self._set_detail_enabled(False)
        self._update_button_states()

    # ═════════════════════════════════════════════════════════════════════════
    # Project loading  (set_project / load — the F5 / refresh contract)
    # ═════════════════════════════════════════════════════════════════════════

    def set_project(self, project_root: str,
                    item_names: Optional[list] = None) -> None:
        """Point the tab at a project and (re)scan it. Lazy-load entry point.

        ``item_names`` is the app's ``[(ITEM_const, display)]`` list
        (``project_data.get_items_list()``) — the same friendly-name source
        every other editor uses, so the stock list and picker show "Poké Ball",
        not "ITEM_POKE_BALL". Optional: without it, names fall back to a
        prettified constant.
        """
        self._project_root = project_root or ""
        if item_names is not None:
            self._ingest_item_names(item_names)
        self.load()

    def set_item_names(self, item_names: list) -> None:
        """Refresh the friendly-name source (e.g. on F5 after items changed) and
        repaint the open shop. Safe to call any time."""
        self._ingest_item_names(item_names)
        if self._current_label:
            self._populate_items(self._current_label)

    def _ingest_item_names(self, item_names: list) -> None:
        """Store the app's ``[(const, display)]`` list as the item name source."""
        self._item_display = {}
        self._item_pairs = []
        for pair in (item_names or []):
            try:
                const, display = pair[0], pair[1]
            except (TypeError, IndexError):
                continue
            if not const or const == "ITEM_NONE":
                continue
            name = display or _prettify_const(const)
            self._item_display[const] = name
            self._item_pairs.append((const, name))

    def load(self) -> None:
        """Fully reset state and re-scan the project from disk.

        F5 / refresh calls this. It must clear ALL in-memory dirty state AND
        the visual dirty state (row tints, amber frame, stale labels), then
        rebuild from a fresh scan.
        """
        # 1. Reset in-memory state.
        self._shops = []
        self._by_label = {}
        self._current_label = ""
        self._dirty_labels.clear()

        # 2. Reset visual dirty state on the detail panel.
        self._detail_box.setStyleSheet("")
        self._info_lbl.setText("Select a shop to edit its stock.")

        # 3. Rebuild under the loading guard so nothing re-marks dirty.
        self._loading = True
        try:
            root = self._project_root
            if root and os.path.isdir(root):
                try:
                    self._shops = shop_data.load_shops(root)
                    self._item_catalog = shop_data.load_item_catalog(root)
                    self._decor_catalog = shop_data.load_decor_catalog(root)
                except Exception as e:  # noqa: BLE001
                    _log.exception("load_shops failed")
                    self._shops = []
                    self._count_lbl.setText(f"Scan error: {e}")

            # Build the pickable (constant, display) pairs. Items use the app's
            # friendly names where supplied (set_project/set_item_names) and
            # prettified constants otherwise, merged with EVERY defined ITEM_* so
            # the whole catalog stays pickable. Decorations have no name source,
            # so their constants are prettified.
            have = {c for c, _ in self._item_pairs}
            for c in self._item_catalog:
                if c == "ITEM_NONE" or c in have:
                    continue
                name = self._item_display.get(c) or _prettify_const(c)
                self._item_pairs.append((c, name))
                self._item_display[c] = name
            self._decor_pairs = [(c, _prettify_const(c))
                                 for c in self._decor_catalog
                                 if c != "DECOR_NONE"]

            self._by_label = {s.label: s for s in self._shops}

            self._items_list.clear()
            self._rebuild_shop_list()
            self._set_detail_enabled(False)
            self._update_button_states()
            have_project = bool(
                self._project_root and os.path.isdir(self._project_root))
            self._btn_new_shop.setEnabled(have_project)
            self._btn_del_shop.setEnabled(False)
            self._btn_eventide.setEnabled(False)
            self._count_lbl.setText(self._count_text())
        finally:
            self._loading = False

        _log.info("Loaded %d shops from %s", len(self._shops), self._project_root)

    def _count_text(self) -> str:
        if not self._project_root:
            return "No project loaded"
        n = len(self._shops)
        return f"{n} shop{'' if n == 1 else 's'}"

    # ═════════════════════════════════════════════════════════════════════════
    # Shop list (left)
    # ═════════════════════════════════════════════════════════════════════════

    def _rebuild_shop_list(self) -> None:
        """Repopulate the shop list, re-applying amber tint to dirty shops."""
        self._shop_list.blockSignals(True)
        self._shop_list.clear()
        needle = self._search.text().lower().strip()
        for shop in self._shops:
            display = self._shop_display(shop)
            if needle and needle not in display.lower() \
                    and needle not in shop.label.lower():
                continue
            item = QListWidgetItem(display)
            item.setData(_ROLE_LABEL, shop.label)
            item.setToolTip(
                f"{shop.label}\n{shop.context}\n{os.path.basename(shop.file)}")
            if shop.label in self._dirty_labels:
                item.setBackground(_DIRTY_BG)
            self._shop_list.addItem(item)
        self._shop_list.blockSignals(False)

    @staticmethod
    def _shop_display(shop: Shop) -> str:
        """Two-part label: human context + (kind) for decoration shops."""
        ctx = shop.context or shop.label
        if shop.kind == KIND_DECOR:
            return f"{ctx}  [decor]"
        return ctx

    def _filter_shop_list(self, _text: str = "") -> None:
        # Preserve selection across the filter rebuild.
        keep = self._current_label
        self._rebuild_shop_list()
        if keep:
            self._select_label_in_list(keep)

    def _select_label_in_list(self, label: str) -> None:
        for i in range(self._shop_list.count()):
            it = self._shop_list.item(i)
            if it and it.data(_ROLE_LABEL) == label:
                self._shop_list.blockSignals(True)
                self._shop_list.setCurrentRow(i)
                self._shop_list.blockSignals(False)
                return

    def _on_shop_selected(self, current: Optional[QListWidgetItem],
                          _prev: Optional[QListWidgetItem] = None) -> None:
        if current is None:
            self._current_label = ""
            self._set_detail_enabled(False)
            self._items_list.clear()
            self._btn_del_shop.setEnabled(False)
            self._btn_eventide.setEnabled(False)
            self._update_button_states()
            return
        label = current.data(_ROLE_LABEL) or ""
        self._current_label = label
        self._populate_items(label)
        shop = self._by_label.get(label)
        self._btn_del_shop.setEnabled(shop is not None)
        self._btn_eventide.setEnabled(
            shop is not None and bool(shop.ref_map or shop.map_name))

    # ═════════════════════════════════════════════════════════════════════════
    # Item list (right)
    # ═════════════════════════════════════════════════════════════════════════

    def _populate_items(self, label: str) -> None:
        shop = self._by_label.get(label)
        if shop is None:
            self._set_detail_enabled(False)
            return

        self._loading = True
        try:
            self._set_detail_enabled(True)

            kind_word = "decorations" if shop.kind == KIND_DECOR else "items"
            term = "DECOR_NONE" if shop.kind == KIND_DECOR else "ITEM_NONE"
            price_note = ("" if shop.kind == KIND_DECOR else
                          "  Prices are per-item (set in the Items editor) and "
                          "are the same in every shop — there is no per-shop "
                          "price in this engine.")
            self._info_lbl.setText(
                f"{shop.context}\n"
                f"Label: {shop.label}   File: {os.path.basename(shop.file)}\n"
                f"{len(shop.items)} {kind_word} sold (the {term} terminator is "
                f"added automatically).{price_note}")

            # Fill the stock list — show friendly names, keep the constant on
            # each row (the source of truth, written on save) + as a tooltip.
            self._items_list.blockSignals(True)
            self._items_list.clear()
            for const in shop.items:
                self._items_list.addItem(self._make_item_row(const, shop.kind))
            self._items_list.blockSignals(False)

            # Reflect this shop's dirty state on the detail frame.
            self._detail_box.setStyleSheet(
                _DIRTY_SS if label in self._dirty_labels else "")
        finally:
            self._loading = False
        self._update_button_states()

    def _make_item_row(self, const: str, kind: str) -> QListWidgetItem:
        """A stock-list row: friendly name as text, constant in a role + tooltip."""
        it = QListWidgetItem(self._display_for(const, kind))
        it.setData(_ROLE_CONST, const)
        it.setToolTip(const)
        return it

    def _display_for(self, const: str, kind: str) -> str:
        """Friendly name for a constant (app's item names; prettified decor)."""
        if kind == KIND_DECOR:
            return _prettify_const(const)
        return self._item_display.get(const) or _prettify_const(const)

    def _pairs_for(self, kind: str) -> list[tuple[str, str]]:
        """(constant, display) choices for the picker dialog, by shop kind."""
        return self._decor_pairs if kind == KIND_DECOR else self._item_pairs

    # ── Edit operations ───────────────────────────────────────────────────────

    def _on_add_item(self) -> None:
        shop = self._current_shop()
        if shop is None:
            return
        pairs = self._pairs_for(shop.kind)
        if not pairs:
            QMessageBox.warning(
                self, "No items",
                "No item constants were found for this project.")
            return
        const = _ItemPickerDialog.pick(pairs, "", "Add item to shop", self)
        if not const:
            return
        # Insert after the current selection (or at the end).
        row = self._items_list.currentRow()
        insert_at = row + 1 if row >= 0 else self._items_list.count()
        self._items_list.insertItem(
            insert_at, self._make_item_row(const, shop.kind))
        self._items_list.setCurrentRow(insert_at)
        self._commit_items_to_model()
        self._mark_dirty()

    def _on_edit_item(self) -> None:
        """Change which item the selected stock row is, in place (keeps order)."""
        shop = self._current_shop()
        if shop is None:
            return
        row = self._items_list.currentRow()
        if row < 0:
            return
        cur = self._items_list.item(row)
        cur_const = (cur.data(_ROLE_CONST) or "") if cur else ""
        pairs = self._pairs_for(shop.kind)
        if not pairs:
            return
        const = _ItemPickerDialog.pick(pairs, cur_const, "Change item", self)
        if not const or const == cur_const:
            return
        cur.setText(self._display_for(const, shop.kind))
        cur.setData(_ROLE_CONST, const)
        cur.setToolTip(const)
        self._commit_items_to_model()
        self._mark_dirty()

    def _on_remove_item(self) -> None:
        row = self._items_list.currentRow()
        if row < 0:
            return
        self._items_list.takeItem(row)
        self._commit_items_to_model()
        self._mark_dirty()

    def _move_item(self, delta: int) -> None:
        row = self._items_list.currentRow()
        if row < 0:
            return
        new_row = row + delta
        if new_row < 0 or new_row >= self._items_list.count():
            return
        item = self._items_list.takeItem(row)
        self._items_list.insertItem(new_row, item)
        self._items_list.setCurrentRow(new_row)
        self._commit_items_to_model()
        self._mark_dirty()

    def _commit_items_to_model(self) -> None:
        """Sync the on-screen list into the current Shop object.

        The constant (not the friendly display text) is the source of truth and
        is read from each row's role; text() is only a last-resort fallback.
        """
        shop = self._current_shop()
        if shop is None:
            return
        items: list[str] = []
        for i in range(self._items_list.count()):
            it = self._items_list.item(i)
            items.append(it.data(_ROLE_CONST) or it.text())
        shop.items = items

    # ── Create / delete / EVENTide jump ───────────────────────────────────────

    def _on_new_shop(self) -> None:
        """Create a new (empty, not-yet-wired) shop list in a chosen map."""
        if not self._project_root or not os.path.isdir(self._project_root):
            return
        maps = shop_data.list_map_names(self._project_root)
        if not maps:
            QMessageBox.warning(
                self, "No maps",
                "No maps with scripts were found to add a shop list to.")
            return
        dlg = _NewShopDialog(maps, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        label, kind, map_name = dlg.values()
        if not label or not map_name:
            return
        try:
            shop = shop_data.create_shop(
                self._project_root, label, kind, map_name)
        except Exception as e:  # noqa: BLE001 — surface the reason to the user
            QMessageBox.warning(self, "Couldn't create shop", str(e))
            return
        # create_shop already wrote the (empty) list to disk — no dirty state.
        self._shops.append(shop)
        self._shops.sort(key=lambda s: (s.map_name.lower(), s.label.lower()))
        self._by_label[shop.label] = shop
        self._rebuild_shop_list()
        self._select_label_in_list(shop.label)
        self._count_lbl.setText(self._count_text())
        QMessageBox.information(
            self, "Shop created",
            f"Created an empty shop list “{shop.label}” in "
            f"{map_name}.\n\nIt isn't wired to an NPC yet — add some items, "
            f"then use “Open in EVENTide” to attach a pokemart call "
            f"to an NPC so it opens in-game.")

    def _on_delete_shop(self) -> None:
        """Delete the selected shop's item list (warns if a pokemart call uses it)."""
        shop = self._current_shop()
        if shop is None:
            return
        warn = ""
        if shop.referenced:
            where = shop.ref_map or os.path.basename(shop.ref_file) or "a script"
            warn = (f"<br><br>⚠ This shop is opened by a <b>pokemart</b> "
                    f"call in <b>{where}</b>. Deleting the list leaves that call "
                    f"dangling — the game will crash at that NPC until you "
                    f"remove the pokemart command in EVENTide.")
        ans = QMessageBox.question(
            self, "Delete shop",
            f"Delete the item list <b>{shop.label}</b> "
            f"({len(shop.items)} items)?{warn}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        if ans != QMessageBox.StandardButton.Yes:
            return
        try:
            shop_data.delete_shop(self._project_root, shop)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Couldn't delete shop", str(e))
            return
        self._dirty_labels.discard(shop.label)
        self._by_label.pop(shop.label, None)
        self._shops = [s for s in self._shops if s.label != shop.label]
        if self._current_label == shop.label:
            self._current_label = ""
        self._rebuild_shop_list()
        self._set_detail_enabled(False)
        self._items_list.clear()
        self._btn_del_shop.setEnabled(False)
        self._btn_eventide.setEnabled(False)
        self._count_lbl.setText(self._count_text())
        # Offer to go remove the now-dangling pokemart call.
        if shop.referenced and (shop.ref_map or shop.map_name):
            j = QMessageBox.question(
                self, "Remove the pokemart call?",
                "Open EVENTide on that map now so you can remove the pokemart "
                "command from the NPC?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if j == QMessageBox.StandardButton.Yes:
                self.jump_to_script_requested.emit(
                    shop.ref_map or shop.map_name, shop.label)

    def _on_open_in_eventide(self) -> None:
        """Jump to EVENTide: the map + NPC whose script opens this shop."""
        shop = self._current_shop()
        if shop is None:
            return
        map_name = shop.ref_map or shop.map_name
        if not map_name:
            QMessageBox.information(
                self, "Open in EVENTide",
                "This shop's list isn't in a map folder, so there's no map to "
                "open. Move the list into a map's scripts to wire it to an NPC.")
            return
        # Wired shop → pass the label so EVENTide selects the NPC; not-yet-wired
        # → pass "" so it just opens the map (the user adds a pokemart NPC).
        mart = shop.label if shop.referenced else ""
        self.jump_to_script_requested.emit(map_name, mart)

    # ── Dirty marking ──────────────────────────────────────────────────────────

    def _mark_dirty(self) -> None:
        if self._loading or not self._current_label:
            return
        label = self._current_label
        self._dirty_labels.add(label)
        # Amber the matching shop-list row.
        for i in range(self._shop_list.count()):
            it = self._shop_list.item(i)
            if it and it.data(_ROLE_LABEL) == label:
                it.setBackground(_DIRTY_BG)
                break
        # Amber the detail frame.
        self._detail_box.setStyleSheet(_DIRTY_SS)
        self.modified.emit()

    def _clear_dirty_visuals(self, label: str) -> None:
        for i in range(self._shop_list.count()):
            it = self._shop_list.item(i)
            if it and it.data(_ROLE_LABEL) == label:
                it.setBackground(_CLEAR_BG)
                break
        if label == self._current_label:
            self._detail_box.setStyleSheet("")

    # ═════════════════════════════════════════════════════════════════════════
    # Save path  (Save-All contract: has_unsaved_changes + flush_to_disk)
    # ═════════════════════════════════════════════════════════════════════════

    def has_unsaved_changes(self) -> bool:
        return bool(self._dirty_labels)

    def flush_to_disk(self) -> tuple[int, list[str]]:
        """Write every dirty shop back to its source file.

        Returns ``(saved_count, errors)`` — matching the Tilemap Editor's
        flush contract that the main window's Save-All path expects.
        """
        if not self._dirty_labels:
            return 0, []

        # Make sure the on-screen edits for the open shop are in the model.
        self._commit_items_to_model()

        saved = 0
        errors: list[str] = []
        for label in list(self._dirty_labels):
            shop = self._by_label.get(label)
            if shop is None:
                continue
            try:
                shop_data.save_shop(self._project_root, shop)
                saved += 1
                self._dirty_labels.discard(label)
                self._clear_dirty_visuals(label)
            except Exception as e:  # noqa: BLE001 — report per-shop, continue
                _log.exception("save_shop failed for %s", label)
                errors.append(f"{label}: {e}")

        _log.info("flush_to_disk: saved=%d errors=%d", saved, len(errors))
        return saved, errors

    # ═════════════════════════════════════════════════════════════════════════
    # Helpers
    # ═════════════════════════════════════════════════════════════════════════

    def _current_shop(self) -> Optional[Shop]:
        return self._by_label.get(self._current_label)

    def _set_detail_enabled(self, on: bool) -> None:
        for w in (self._items_list, self._btn_add, self._btn_edit,
                  self._btn_remove, self._btn_up, self._btn_down):
            w.setEnabled(on)
        if not on:
            self._info_lbl.setText("Select a shop to edit its stock.")

    def _update_button_states(self, *_a) -> None:
        has_shop = self._current_shop() is not None
        row = self._items_list.currentRow()
        count = self._items_list.count()
        self._btn_remove.setEnabled(has_shop and row >= 0)
        self._btn_edit.setEnabled(has_shop and row >= 0)
        self._btn_up.setEnabled(has_shop and row > 0)
        self._btn_down.setEnabled(has_shop and 0 <= row < count - 1)
        self._btn_add.setEnabled(has_shop)
