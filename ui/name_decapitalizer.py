"""
Name Decapitalizer — batch rename ALL-CAPS display names to Smart Title Case.

Scope: display-name fields only (species, moves, items, trainers, trainer
classes, abilities, UI key strings). Dialogue scripts and source code are left
untouched — the user can edit those by hand.

Casing rules:
  - Only converts strings that are ≥70% uppercase letters (i.e., already
    ALL-CAPS). Mixed-case strings are skipped so user-customised names stay.
  - First letter of each space-separated word is capitalized, the rest
    lowered.
  - Short filler words (of / the / and / in / on / to / at / for / by / or
    / as / vs) are kept lowercase when they appear mid-string.
  - Words in the skip-list (HP, PP, EXP, HM, TM, PC, STR, DEF, ATK, etc.)
    stay fully upper-case.
  - Roman numerals (II, III, IV, V, VI, VII, VIII, IX, X …) stay upper-case.
  - Apostrophes and hyphens are preserved: FARFETCH'D → Farfetch'd,
    ROCK-HARD → Rock-Hard.

Skip-list is persisted per-project in settings.ini under
[NameDecapitalizer]/skip_list.
"""

from __future__ import annotations

import os
import re
from typing import Callable

from PyQt6.QtCore import Qt, QSettings
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QCheckBox, QPlainTextEdit, QTableWidget, QTableWidgetItem, QHeaderView,
    QMessageBox, QGroupBox, QAbstractItemView, QWidget,
)

try:
    from app_info import get_settings_path
except Exception:  # pragma: no cover — fallback for isolated testing
    def get_settings_path() -> str:
        return os.path.join(os.path.dirname(__file__), "..", "data", "settings.ini")


# ── Constants ────────────────────────────────────────────────────────────────

CATEGORY_ORDER = [
    ("species",         "Species Names"),
    ("moves",           "Move Names"),
    ("items",           "Item Names"),
    ("trainers",        "Trainer Names"),
    ("trainer_classes", "Trainer Class Names"),
    ("abilities",       "Ability Names"),
    ("ui_strings",      "UI Key Strings"),
]

# Pre-seeded skip list — common display abbreviations the user doesn't want
# touched. HP/STR/DEF/ATK etc. per user request.
DEFAULT_SKIP_LIST = [
    "HM", "TM", "PP", "HP", "EXP", "PC", "LV",
    "STR", "DEF", "ATK", "SPE", "SPA", "SPD", "SP",
    "OK", "OT", "ID", "VS", "AI", "IV", "EV",
    "KO", "CPU", "NPC", "TV", "PS",
]

FILLER_WORDS = {
    "of", "the", "and", "a", "an", "in", "on", "to", "at",
    "for", "by", "or", "as", "vs", "de", "la", "du", "nor", "but",
}

ROMAN_NUMERALS = {
    "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X",
    "XI", "XII", "XIII", "XIV", "XV", "XX",
}


# ── Core logic ───────────────────────────────────────────────────────────────

def _cap_word(word: str) -> str:
    """Capitalise first letter, lowercase the rest. Apostrophes/dots stay put."""
    if not word:
        return word
    return word[0].upper() + word[1:].lower()


def _is_mostly_upper(name: str) -> bool:
    """True if the alphabetic chars in *name* are ≥70% upper-case."""
    letters = [c for c in name if c.isalpha()]
    if not letters:
        return False
    upper = sum(1 for c in letters if c.isupper())
    return upper / len(letters) >= 0.70


_CONTROL_CODE_RX = re.compile(r"\{[^{}]*\}")


def decapitalize(name: str, skip_list: list[str] | None = None) -> str:
    """
    Convert an ALL-CAPS display name to Smart Title Case.

    Returns the original string unchanged if it's already mixed-case or if
    the conversion would produce no change. `{...}` control codes
    (charmap sequences like `{PKMN}`, `{PLAYER}`, `{STR_VAR_1}`) are
    preserved verbatim — they are parsed out, the remainder is
    decapitalized, and the codes are reinserted.
    """
    if not name:
        return name

    # Pull out all {...} control codes, decapitalize only the plain text,
    # then stitch them back in at their original positions.
    segments: list[tuple[str, str]] = []  # (kind, text) where kind in {'text','code'}
    pos = 0
    for m in _CONTROL_CODE_RX.finditer(name):
        if m.start() > pos:
            segments.append(("text", name[pos:m.start()]))
        segments.append(("code", m.group(0)))
        pos = m.end()
    if pos < len(name):
        segments.append(("text", name[pos:]))

    text_only = "".join(s for kind, s in segments if kind == "text")
    if not _is_mostly_upper(text_only):
        return name

    converted_text = _decapitalize_plain(text_only, skip_list)
    if converted_text == text_only:
        return name

    # Re-weave: convert each text segment individually (proportional
    # slicing would be error-prone), preserving code segments as-is.
    out_parts: list[str] = []
    for kind, seg in segments:
        if kind == "code":
            out_parts.append(seg)
        else:
            out_parts.append(_decapitalize_plain(seg, skip_list))
    return "".join(out_parts)


def _decapitalize_plain(name: str, skip_list: list[str] | None) -> str:
    """Decapitalize a string that contains no control codes."""
    if not name:
        return name
    skip_upper = {s.strip().upper() for s in (skip_list or []) if s.strip()}
    skip_upper |= ROMAN_NUMERALS

    words = name.split(" ")
    out_words: list[str] = []
    for i, w in enumerate(words):
        if not w:
            out_words.append(w)
            continue
        # Split on hyphens and handle each segment independently
        segs = w.split("-")
        new_segs: list[str] = []
        for j, seg in enumerate(segs):
            if not seg:
                new_segs.append(seg)
                continue
            # Strip punctuation for skip-list comparison but keep original
            core = re.sub(r"[^A-Za-z0-9]", "", seg)
            core_upper = core.upper()
            # Also check just the alphabetic prefix (so TM42 / HM03 match "TM"/"HM")
            alpha_prefix = re.match(r"[A-Za-z]+", core)
            prefix_upper = alpha_prefix.group(0).upper() if alpha_prefix else ""
            if core_upper in skip_upper:
                # Keep entire segment upper-cased, preserving punctuation positions
                new_segs.append(seg.upper())
            elif prefix_upper and prefix_upper in skip_upper and core != prefix_upper:
                # Segment is "SKIP + digits" — keep prefix upper, digits as-is
                new_segs.append(seg.upper())
            elif i > 0 and j == 0 and core.lower() in FILLER_WORDS:
                new_segs.append(seg.lower())
            else:
                new_segs.append(_cap_word(seg))
        out_words.append("-".join(new_segs))
    return " ".join(out_words)


# ── Skip-list persistence ────────────────────────────────────────────────────

def load_skip_list() -> list[str]:
    """Load the user's skip list from settings.ini, or seed defaults."""
    try:
        s = QSettings(get_settings_path(), QSettings.Format.IniFormat)
        raw = s.value("NameDecapitalizer/skip_list", "", type=str)
    except Exception:
        raw = ""
    if not raw:
        return list(DEFAULT_SKIP_LIST)
    words = [w.strip() for w in raw.replace(",", "\n").splitlines() if w.strip()]
    return words or list(DEFAULT_SKIP_LIST)


def save_skip_list(words: list[str]) -> None:
    try:
        s = QSettings(get_settings_path(), QSettings.Format.IniFormat)
        s.setValue("NameDecapitalizer/skip_list", "\n".join(words))
        s.sync()
    except Exception:
        pass


# ── Data scanning — find all decapitalizable names in the project ───────────

class Entry:
    __slots__ = ("category", "cat_label", "key", "original", "proposed", "apply_fn")

    def __init__(
        self,
        category: str,
        cat_label: str,
        key: str,
        original: str,
        proposed: str,
        apply_fn: Callable[[str], None],
    ) -> None:
        self.category = category
        self.cat_label = cat_label
        self.key = key
        self.original = original
        self.proposed = proposed
        self.apply_fn = apply_fn


def _scan_species(ps, skip: list[str]) -> list[Entry]:
    out: list[Entry] = []
    data = ps.source_data.get_pokemon_data() or {}
    for sp in data:
        name = ps.source_data.get_species_info(sp, "speciesName") or ""
        new = decapitalize(name, skip)
        if new and new != name:
            def make_fn(const=sp):
                def _fn(val):
                    # Write to the source-of-truth field (saved to disk) AND
                    # the display-cache field (shown in the Pokemon tree).
                    ps.source_data.set_species_info(const, "speciesName", val)
                    try:
                        ps.source_data.data["species_data"].data[const]["name"] = val
                    except Exception:
                        pass
                return _fn
            out.append(Entry("species", "Species", sp, name, new, make_fn()))
    return out


def _scan_moves(ps, skip: list[str]) -> list[Entry]:
    out: list[Entry] = []
    moves = ps.source_data.get_pokemon_moves() or {}
    for m in moves:
        name = ps.source_data.get_move_data(m, "name") or ""
        new = decapitalize(name, skip)
        if new and new != name:
            def make_fn(const=m):
                def _fn(val):
                    ps.source_data.set_move_data(const, "name", val)
                return _fn
            out.append(Entry("moves", "Move", m, name, new, make_fn()))
    return out


def _scan_items(ps, skip: list[str]) -> list[Entry]:
    """Read items from source_data (loaded at startup) — the items_editor
    loads lazily so we can't rely on its _items dict being populated.
    Mirror edits into both the source_data dict (saved to disk) and the
    editor's dict (if present) so the list widget refreshes."""
    out: list[Entry] = []
    raw = ps.source_data.get_pokemon_items() or {}
    editor = getattr(ps, "items_editor", None)
    editor_items = getattr(editor, "_items", None) if editor else None

    # Normalize to an iterable of (const, data_dict) pairs. The underlying
    # plugin may expose either list-of-dicts or dict-keyed-by-const form.
    pairs: list[tuple[str, dict]] = []
    if isinstance(raw, list):
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            const = entry.get("itemId") or entry.get("constant") or ""
            if const:
                pairs.append((const, entry))
    elif isinstance(raw, dict):
        for const, entry in raw.items():
            if isinstance(entry, dict):
                pairs.append((const, entry))

    for const, data in pairs:
        if const == "ITEM_NONE":
            continue
        field = "english" if "english" in data else "name"
        name = data.get(field) or ""
        new = decapitalize(name, skip)
        if new and new != name:
            def make_fn(c=const, f=field, entry=data):
                def _fn(val):
                    # Update the source_data dict (this drives save to disk)
                    entry[f] = val
                    # Mirror into the items editor's private dict so the
                    # list widget + detail panel pick up the new name.
                    if editor_items is not None and c in editor_items:
                        editor_items[c][f] = val
                return _fn
            out.append(Entry("items", "Item", const, name, new, make_fn()))
    return out


_TRAINER_NAME_RX = re.compile(r'_\(\s*"([^"]*)"\s*\)')


def _unwrap_trainer_name(raw: str) -> tuple[str, Callable[[str], str]]:
    """Trainer names are stored as C-macro-wrapped strings like _("MATT").
    Return (inner_name, rewrap_fn) so decapitalize() sees only the actual
    display text and we can put the wrapper back on apply. If the raw value
    isn't wrapped, return it as-is with an identity rewrap."""
    m = _TRAINER_NAME_RX.fullmatch(raw.strip()) if raw else None
    if m:
        inner = m.group(1)
        def _rewrap(v: str) -> str:
            return f'_("{v}")'
        return inner, _rewrap
    return raw, (lambda v: v)


def _scan_trainers(ps, skip: list[str]) -> list[Entry]:
    out: list[Entry] = []
    trainers = ps.source_data.get_pokemon_trainers() or {}
    trainers_editor = getattr(ps, "trainers_editor", None)
    editor_trainers = getattr(trainers_editor, "_trainers", None) if trainers_editor else None
    for t in trainers:
        raw = ps.source_data.get_trainer_data(t, "trainerName") or ""
        inner, rewrap = _unwrap_trainer_name(raw)
        new = decapitalize(inner, skip)
        if new and new != inner:
            def make_fn(const=t, rw=rewrap):
                def _fn(val):
                    wrapped = rw(val)
                    ps.source_data.set_trainer_data(const, "trainerName", wrapped)
                    # Mirror into the Trainers editor's private dict so the
                    # list/header repaint picks up the new name.
                    if editor_trainers is not None and const in editor_trainers:
                        editor_trainers[const]["trainerName"] = wrapped
                return _fn
            # Show the unwrapped display name to the user in the preview
            out.append(Entry("trainers", "Trainer", t, inner, new, make_fn()))
    return out


def _scan_trainer_classes(ps, skip: list[str]) -> list[Entry]:
    out: list[Entry] = []
    editor = getattr(ps, "trainer_class_editor", None)
    if not editor:
        return out
    # The Trainer Classes editor loads lazily (only when that sub-tab is
    # clicked). Force-load it if the user hasn't opened it yet — otherwise
    # _names is empty and we'd find nothing.
    if not getattr(editor, "_loaded", False):
        try:
            root = ps.project_info.get("dir", "")
            trainers = ps.source_data.get_pokemon_trainers() or {}
            editor.load(root, trainers)
        except Exception:
            return out
    names: dict[str, str] = getattr(editor, "_names", {}) or {}
    dirty: dict[str, str] = getattr(editor, "_dirty_names", {}) or {}
    for const in names:
        effective = dirty.get(const, names.get(const, ""))
        new = decapitalize(effective, skip)
        if new and new != effective:
            def make_fn(c=const):
                def _fn(val):
                    editor._dirty_names[c] = val
                return _fn
            out.append(Entry("trainer_classes", "Trainer Class", const, effective, new, make_fn()))
    return out


# Ability names live in pokefirered/src/data/text/abilities.h as:
#   [ABILITY_XXX] = _("NAME"),
# We do a direct file rewrite.
_ABILITY_LINE_RX = re.compile(
    r'(\[(ABILITY_[A-Z0-9_]+)\]\s*=\s*_\(")([^"]*)("\),)'
)


def _abilities_file_path(ps) -> str | None:
    try:
        root = ps.project_info.get("dir", "")
    except Exception:
        return None
    path = os.path.join(root, "src", "data", "text", "abilities.h")
    return path if os.path.isfile(path) else None


def _scan_abilities(ps, skip: list[str]) -> list[Entry]:
    out: list[Entry] = []
    path = _abilities_file_path(ps)
    if not path:
        return out
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception:
        return out
    # Collect proposed edits; apply_fn writes them out in a single file write
    # at the end via a shared closure.
    pending: dict[str, str] = {}

    def make_apply_fn(const: str):
        def _fn(val: str):
            pending[const] = val
            _flush_abilities(path, pending)
        return _fn

    for m in _ABILITY_LINE_RX.finditer(text):
        const = m.group(2)
        if const == "ABILITY_NONE":
            continue
        original = m.group(3)
        new = decapitalize(original, skip)
        if new and new != original:
            out.append(Entry("abilities", "Ability", const, original, new, make_apply_fn(const)))
    return out


def _flush_abilities(path: str, pending: dict[str, str]) -> None:
    """Rewrite abilities.h with all pending edits. Called repeatedly but
    idempotent — reads file fresh each time."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception:
        return

    def _sub(m: re.Match) -> str:
        const = m.group(2)
        if const in pending:
            return f'{m.group(1)}{pending[const]}{m.group(4)}'
        return m.group(0)

    new_text = _ABILITY_LINE_RX.sub(_sub, text)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_text)
    except Exception:
        pass


def _scan_ui_strings(ps, skip: list[str], unified_win) -> list[Entry]:
    """Scan UI Key Strings tab's QLineEdit fields. Multi-line strings are
    skipped — those are dialogue, not display labels."""
    out: list[Entry] = []
    # The UITabWidget lives inside the unified window's stacked pages.
    ui_tab = _find_ui_tab(unified_win, ps)
    if ui_tab is None:
        return out
    key_strings_sub = getattr(ui_tab, "_key_strings", None)
    if key_strings_sub is None:
        return out
    fields = getattr(key_strings_sub, "_fields", {}) or {}
    from PyQt6.QtWidgets import QLineEdit
    for var_name, widget in fields.items():
        # Only single-line fields (labels, button text, menu items)
        if not isinstance(widget, QLineEdit):
            continue
        original = widget.text()
        new = decapitalize(original, skip)
        if new and new != original:
            def make_fn(w=widget):
                def _fn(val):
                    w.setText(val)
                return _fn
            out.append(Entry("ui_strings", "UI String", var_name, original, new, make_fn()))
    return out


def _find_ui_tab(unified_win, ps):
    """Best-effort lookup of the UITabWidget instance."""
    # Try common paths
    for attr in ("ui_tab_widget", "_ui_tab_widget", "ui_widget"):
        obj = getattr(ps, attr, None)
        if obj is not None:
            return obj
    # Walk the unified window's stacked pages for a UITabWidget
    try:
        from ui.ui_tab_widget import UITabWidget
    except Exception:
        return None
    for w in unified_win.findChildren(UITabWidget):
        return w
    return None


# ── Dialog ───────────────────────────────────────────────────────────────────

class NameDecapitalizerDialog(QDialog):
    """Preview-and-apply dialog for batch-decapitalising display names."""

    def __init__(self, unified_win, porysuite_window, parent=None) -> None:
        super().__init__(parent or unified_win)
        self._unified = unified_win
        self._ps = porysuite_window
        self._entries: list[Entry] = []
        self._row_entries: list[Entry] = []  # parallel to table rows

        self.setWindowTitle("Name Decapitalizer")
        self.resize(900, 650)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        intro = QLabel(
            "Converts ALL-CAPS display names to Smart Title Case "
            "(BULBASAUR → Bulbasaur, MASTER BALL → Master Ball). "
            "Only display-name fields are touched — dialogue and scripts are "
            "left alone. Tick the categories to scan, untick any rows you "
            "don't want changed, then click Apply."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #888; font-size: 11px;")
        root.addWidget(intro)

        # ── Categories + skip-list row ──────────────────────────────────────
        top_row = QHBoxLayout()
        top_row.setSpacing(10)

        cats_grp = QGroupBox("Categories")
        cats_grid = QGridLayout(cats_grp)
        cats_grid.setContentsMargins(10, 14, 10, 10)
        self._cat_boxes: dict[str, QCheckBox] = {}
        for i, (key, label) in enumerate(CATEGORY_ORDER):
            cb = QCheckBox(label)
            cb.setChecked(True)
            self._cat_boxes[key] = cb
            cats_grid.addWidget(cb, i // 2, i % 2)
        top_row.addWidget(cats_grp, 1)

        skip_grp = QGroupBox("Skip-list (keep these upper-case, one per line)")
        skip_lay = QVBoxLayout(skip_grp)
        skip_lay.setContentsMargins(10, 14, 10, 10)
        self._skip_edit = QPlainTextEdit()
        self._skip_edit.setPlainText("\n".join(load_skip_list()))
        self._skip_edit.setPlaceholderText("HP\nPP\nTM\nHM …")
        skip_lay.addWidget(self._skip_edit)
        top_row.addWidget(skip_grp, 1)

        root.addLayout(top_row)

        # ── Scan button ─────────────────────────────────────────────────────
        scan_row = QHBoxLayout()
        self._scan_btn = QPushButton("Scan Project")
        self._scan_btn.clicked.connect(self._on_scan)
        scan_row.addWidget(self._scan_btn)
        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet("color: #888;")
        scan_row.addWidget(self._status_lbl, 1)

        self._check_all_btn = QPushButton("Check All")
        self._check_all_btn.clicked.connect(lambda: self._set_all_checked(True))
        self._uncheck_all_btn = QPushButton("Uncheck All")
        self._uncheck_all_btn.clicked.connect(lambda: self._set_all_checked(False))
        scan_row.addWidget(self._check_all_btn)
        scan_row.addWidget(self._uncheck_all_btn)
        root.addLayout(scan_row)

        # ── Preview table ───────────────────────────────────────────────────
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["Apply", "Category", "Original", "Proposed"])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        root.addWidget(self._table, 1)

        # ── Buttons ─────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self._apply_btn = QPushButton("Apply Checked")
        self._apply_btn.setDefault(True)
        self._apply_btn.clicked.connect(self._on_apply)
        self._apply_btn.setEnabled(False)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._apply_btn)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    def _skip_list(self) -> list[str]:
        return [w.strip() for w in self._skip_edit.toPlainText().splitlines() if w.strip()]

    def _on_scan(self) -> None:
        skip = self._skip_list()
        save_skip_list(skip)
        selected = {k for k, cb in self._cat_boxes.items() if cb.isChecked()}
        entries: list[Entry] = []

        try:
            if "species" in selected:
                entries += _scan_species(self._ps, skip)
            if "moves" in selected:
                entries += _scan_moves(self._ps, skip)
            if "items" in selected:
                entries += _scan_items(self._ps, skip)
            if "trainers" in selected:
                entries += _scan_trainers(self._ps, skip)
            if "trainer_classes" in selected:
                entries += _scan_trainer_classes(self._ps, skip)
            if "abilities" in selected:
                entries += _scan_abilities(self._ps, skip)
            if "ui_strings" in selected:
                entries += _scan_ui_strings(self._ps, skip, self._unified)
        except Exception as exc:
            QMessageBox.warning(self, "Scan Error", f"Error while scanning:\n{exc}")
            return

        self._entries = entries
        self._populate_table()
        self._status_lbl.setText(
            f"Found {len(entries)} name(s) to decapitalize. "
            "Review and untick anything you want to keep as-is."
        )
        self._apply_btn.setEnabled(bool(entries))

    def _populate_table(self) -> None:
        self._table.setRowCount(0)
        self._row_entries = []
        for e in self._entries:
            row = self._table.rowCount()
            self._table.insertRow(row)
            cb_item = QTableWidgetItem()
            cb_item.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
            )
            cb_item.setCheckState(Qt.CheckState.Checked)
            self._table.setItem(row, 0, cb_item)
            self._table.setItem(row, 1, QTableWidgetItem(f"{e.cat_label}  ({e.key})"))
            self._table.setItem(row, 2, QTableWidgetItem(e.original))
            self._table.setItem(row, 3, QTableWidgetItem(e.proposed))
            self._row_entries.append(e)

    def _set_all_checked(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 0)
            if item is not None:
                item.setCheckState(state)

    def _on_apply(self) -> None:
        to_apply: list[Entry] = []
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 0)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                to_apply.append(self._row_entries[row])

        if not to_apply:
            QMessageBox.information(
                self, "Nothing to Apply",
                "No rows are ticked. Tick the ones you want to change and try again.",
            )
            return

        # Warn if this will touch abilities.h directly on disk
        has_abilities = any(e.category == "abilities" for e in to_apply)
        if has_abilities:
            ret = QMessageBox.question(
                self, "Confirm",
                f"Apply {len(to_apply)} name change(s)?\n\n"
                "Note: Ability names are written directly to "
                "src/data/text/abilities.h (not through the normal save "
                "pipeline). Other changes will be marked dirty and saved "
                "when you click File → Save.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ret != QMessageBox.StandardButton.Yes:
                return
        else:
            ret = QMessageBox.question(
                self, "Confirm",
                f"Apply {len(to_apply)} name change(s)?\n\n"
                "Changes will be marked dirty and saved to disk when you "
                "click File → Save.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ret != QMessageBox.StandardButton.Yes:
                return

        applied = 0
        failures = 0
        for e in to_apply:
            try:
                e.apply_fn(e.proposed)
                applied += 1
            except Exception:
                failures += 1

        # Species names live in src/data/text/species_names.h — they are NOT
        # touched by the normal save pipeline (only by the individual Rename
        # tool). Write them to disk right now so the next build picks them up.
        species_changed = {
            e.key: e.proposed for e in to_apply if e.category == "species"
        }
        if species_changed:
            try:
                self._write_species_names_header(species_changed)
            except Exception:
                pass

        # Mark dirty + refresh lists in open editors so the user sees the change
        self._refresh_editors()
        try:
            self._ps.setWindowModified(True)
        except Exception:
            pass
        try:
            self._unified.setWindowModified(True)
        except Exception:
            pass

        msg = f"Applied {applied} name change(s)."
        if failures:
            msg += f"\n{failures} failed silently — check the log."
        msg += "\n\nDon't forget to click File → Save."
        QMessageBox.information(self, "Done", msg)
        self.accept()

    def _write_species_names_header(self, changed: dict[str, str]) -> int:
        """Patch src/data/text/species_names.h in place.

        `changed` is {SPECIES_CONST: "New Name"}. Returns number of lines
        updated. This mirrors the logic the individual Rename tool uses
        (core/refactor_service.py) — species_names.h is the ROM's actual
        in-game text table; the speciesName field in species_info is just
        the JSON cache mirror.
        """
        ps = self._ps
        try:
            root = ps.project_info.get("dir", "")
        except Exception:
            root = ""
        if not root:
            return 0
        path = os.path.join(root, "src", "data", "text", "species_names.h")
        if not os.path.isfile(path):
            return 0

        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            return 0

        pat = re.compile(r"\[(SPECIES_[A-Z0-9_]+)\]\s*=\s*_\(\"(.*?)\"\)")
        updated = 0
        for i, ln in enumerate(lines):
            m = pat.search(ln)
            if not m:
                continue
            const = m.group(1)
            if const not in changed:
                continue
            # Cap at POKEMON_NAME_LENGTH (10) to be safe — smart-title-casing
            # an all-caps string can't make it longer, but guard anyway.
            new_name = (changed[const] or "")[:10]
            # Escape any double quotes/backslashes just in case.
            escaped = new_name.replace("\\", "\\\\").replace('"', '\\"')
            new_line = re.sub(r'_\(".*?"\)', f'_("{escaped}")', ln, count=1)
            if new_line != ln:
                lines[i] = new_line
                updated += 1

        if updated:
            try:
                with open(path, "w", encoding="utf-8", newline="\n") as f:
                    f.writelines(lines)
            except OSError:
                return 0
        return updated

    def _refresh_editors(self) -> None:
        """After bulk rename, rebuild editor list widgets so new names show."""
        ps = self._ps
        # Items list
        editor = getattr(ps, "items_editor", None)
        if editor is not None and hasattr(editor, "_rebuild_list"):
            try:
                editor._rebuild_list()
            except Exception:
                pass
        # Trainer classes list
        tce = getattr(ps, "trainer_class_editor", None)
        if tce is not None:
            try:
                # _dirty_names is read in TrainerClassEditor's list-builder via
                # the name getter; force a re-select to refresh the name field.
                current = getattr(tce, "_current_class", None)
                # Update each list item's text
                lst = getattr(tce, "_list", None)
                if lst is not None:
                    from PyQt6.QtCore import Qt as _Qt
                    for i in range(lst.count()):
                        it = lst.item(i)
                        const = it.data(_Qt.ItemDataRole.UserRole)
                        new_name = tce._dirty_names.get(const, tce._names.get(const, const))
                        it.setText(new_name or const)
                if current is not None and hasattr(tce, "_name_edit"):
                    new_name = tce._dirty_names.get(current, tce._names.get(current, ""))
                    tce._name_edit.blockSignals(True)
                    tce._name_edit.setText(new_name)
                    tce._name_edit.blockSignals(False)
            except Exception:
                pass
        # Pokemon tree: walk each top-level item and re-apply its display
        # name from the (now-updated) "name" cache.
        try:
            tree = getattr(ps.ui, "tree_pokemon", None)
            if tree is not None:
                for i in range(tree.topLevelItemCount()):
                    it = tree.topLevelItem(i)
                    const = it.text(1)  # column 1 holds the species constant
                    if const:
                        new_name = ps.source_data.get_species_data(const, "name") or const
                        it.setText(0, new_name)
        except Exception:
            pass

        # Pokemon detail panel: refresh the speciesName QLineEdit for the
        # currently-selected species so the user sees the change AND so that
        # save_species_data doesn't read a stale ALL-CAPS value and clobber
        # our new value back on File → Save.
        try:
            current_sp = getattr(ps, "previous_selected_species", None)
            if current_sp and hasattr(ps.ui, "species_name"):
                new_name = ps.source_data.get_species_info(current_sp, "speciesName") or ""
                w = ps.ui.species_name
                w.blockSignals(True)
                try:
                    w.setText(new_name)
                finally:
                    w.blockSignals(False)
        except Exception:
            pass

        # Trainers editor: rebuild its list + refresh header
        trainers_editor = getattr(ps, "trainers_editor", None)
        if trainers_editor is not None:
            try:
                if hasattr(trainers_editor, "_rebuild_list"):
                    trainers_editor._rebuild_list()
                if hasattr(trainers_editor, "_refresh_header"):
                    trainers_editor._refresh_header()
                # Re-load the currently-selected trainer so the Name field
                # and header both pick up the new value.
                current = getattr(trainers_editor, "_current_const", None)
                if current:
                    editor_trainers = getattr(trainers_editor, "_trainers", {})
                    parties = getattr(trainers_editor, "_parties", {})
                    panel = getattr(trainers_editor, "_detail_panel", None)
                    if current in editor_trainers and panel is not None:
                        panel.load(
                            current, editor_trainers[current], parties.get(current)
                        )
            except Exception:
                pass

        # Other editors: call refresh methods if present
        for method_name in ("refresh_moves_list", "refresh_items_list"):
            m = getattr(ps, method_name, None)
            if callable(m):
                try:
                    m()
                except Exception:
                    pass


def open_decapitalizer(unified_win, porysuite_window) -> None:
    """Entry point called from the Edit menu."""
    if porysuite_window is None or getattr(porysuite_window, "source_data", None) is None:
        QMessageBox.information(
            unified_win, "Name Decapitalizer",
            "Open a project first — the decapitalizer needs loaded project data.",
        )
        return
    dlg = NameDecapitalizerDialog(unified_win, porysuite_window, parent=unified_win)
    dlg.exec()
