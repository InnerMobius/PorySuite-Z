"""
label_manager.py — Variables & Flags manager for PorySuite-Z

The single place to name and reuse the game's variable / flag slots, RPG-Maker
style. Two layers, one screen:

* **Friendly labels + notes** (non-destructive): give a constant a readable
  display name without touching game code. Saved per project in
  ``porysuite_labels.json``. The Event Editor shows these labels in script lines.
* **Create / reclaim** (structural, via ``var_flag_manager``): claim a free
  slot as a brand-new named variable/flag (safe — free slots have no
  references), or take over one the project no longer needs with a
  whole-project rename, the full reference list in front of you, and a
  snapshot of every file before it is touched.

Every slot carries a *category* — what it is for — and a *protection* level —
how safe it is to take over. Both are worked out from the project itself: the
slot's real number (resolved through the header's own arithmetic, so it lands
in the right engine region) and every place it is actually referenced. Nothing
is judged on its name alone.

Reclaiming is always the user's explicit act. This screen never frees, renames
or re-points a slot on its own.
"""

import json
import os
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QColor, QCursor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QPushButton, QLineEdit, QPlainTextEdit,
    QListWidget, QListWidgetItem, QSplitter, QMessageBox,
    QComboBox, QAbstractItemView, QInputDialog, QApplication,
    QCheckBox,
)

from eventide.backend import var_flag_manager as vf
from eventide.backend import new_game_state as ngs


# ─── Helpers ────────────────────────────────────────────────────────────────

_LABELS_FILENAME = "porysuite_labels.json"

_TYPE_FLAG = "flag"
_TYPE_VAR = "var"

# Colour per category — what the slot is FOR.
_CATEGORY_COLOR = {
    'free':      '#3fae5a',
    'item':      '#5a9bd4',
    'event':     '#5a9bd4',
    'trainer':   '#c2704a',
    'shared':    '#d9a441',
    'engine':    '#8a8a8a',
    'system':    '#8a8a8a',
    'temporary': '#8a8a8a',
    'unknown':   '#d9a441',
}

# What each protection level means, in the user's words, and whether the
# Reclaim button is allowed to go ahead at all.
_PROTECTION_META = {
    'free':        ("Free to use", True,
                    "Nothing in the project refers to this slot. Claiming it "
                    "changes nothing else."),
    'review':      ("Reclaim candidate", True,
                    "Used in one place only, so it is quick to check before "
                    "you take it over."),
    'preserve':    ("Preserved by default", False,
                    "Trainer slots are kept as they are, because trainer "
                    "numbering shifts as battles move onto new maps."),
    'investigate': ("Protected until investigated", False,
                    "Go through the references below before deciding."),
    'protected':   ("Protected", False,
                    "PorySuite will not rename this one."),
}

# Set by the unified window so "Use in Event Editor" can jump there.
_open_event_editor_cb = None   # Callable[[str], None]  const -> select & switch


def set_open_event_editor_cb(cb):
    """Register the callback that opens EVENTide focused on a constant."""
    global _open_event_editor_cb
    _open_event_editor_cb = cb


# ─── Widget ─────────────────────────────────────────────────────────────────

class LabelManagerWidget(QWidget):
    """The unified Variables & Flags manager page."""

    labels_changed = pyqtSignal()      # a display label / notes changed
    constants_changed = pyqtSignal()   # a slot was created or renamed in code
    modified = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._project_dir = ""
        self._labels: dict[str, dict] = {}   # const -> {label, notes}
        self._entries_var: list = []         # scan() results
        self._entries_flag: list = []
        self._status_by_name: dict[str, dict] = {}
        self._scanned = False
        self._dirty = False
        self._build_ui()

    # ─── UI construction ────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        title = QLabel("Variables & Flags")
        title.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        root.addWidget(title)

        subtitle = QLabel(
            "Name and reuse the game's variable and flag slots — like RPG "
            "Maker's Variables and Switches. Make a new one, give a friendly "
            "label to an existing one (safe — never changes game code), or "
            "reclaim one the project no longer needs. Every slot shows what "
            "it is for and exactly where it is used, so you can check before "
            "you take it over.")
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: #888; margin-bottom: 6px;")
        root.addWidget(subtitle)

        # ── Action row: New buttons + refresh ───────────────────────────────
        action_row = QHBoxLayout()
        self._btn_new_var = QPushButton("New Variable…")
        self._btn_new_var.setToolTip(
            "Create a new variable: type a plain name (e.g. \"Side Quest\") "
            "and it claims a free slot. Safe — nothing else uses it.")
        self._btn_new_var.clicked.connect(lambda: self._on_new(_TYPE_VAR))
        action_row.addWidget(self._btn_new_var)
        self._btn_new_flag = QPushButton("New Flag…")
        self._btn_new_flag.setToolTip(
            "Create a new flag (on/off switch): type a plain name and it "
            "claims a free slot.")
        self._btn_new_flag.clicked.connect(lambda: self._on_new(_TYPE_FLAG))
        action_row.addWidget(self._btn_new_flag)
        action_row.addStretch()
        self._btn_refresh = QPushButton("Rescan usage")
        self._btn_refresh.setToolTip(
            "Re-check which slots are free, used by your scripts, or used by "
            "the vanilla engine.")
        self._btn_refresh.clicked.connect(lambda: self._run_scan(force=True))
        action_row.addWidget(self._btn_refresh)
        root.addLayout(action_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter, stretch=1)

        # ── Left: filter + list ─────────────────────────────────────────────
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        filter_row = QHBoxLayout()
        self._filter_combo = QComboBox()
        self._filter_combo.addItems(
            ["All", "Flags", "Vars", "Free only", "Reclaim candidates",
             "Needs investigating", "Protected"])
        self._filter_combo.setToolTip(
            "Reclaim candidates are used in one place only. Needs "
            "investigating means shared between places, or in a block the "
            "engine addresses by number.")
        self._filter_combo.currentIndexChanged.connect(self._apply_filter)
        self._filter_combo.wheelEvent = lambda e: e.ignore()
        filter_row.addWidget(QLabel("Show:"))
        filter_row.addWidget(self._filter_combo)
        filter_row.addStretch()
        self._counts_label = QLabel("")
        self._counts_label.setStyleSheet("color: #888;")
        filter_row.addWidget(self._counts_label)
        left_layout.addLayout(filter_row)

        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText("Search constants or labels…")
        self._search_box.setClearButtonEnabled(True)
        self._search_box.textChanged.connect(self._apply_filter)
        left_layout.addWidget(self._search_box)

        self._const_list = QListWidget()
        self._const_list.setAlternatingRowColors(True)
        self._const_list.currentItemChanged.connect(self._on_selection_changed)
        left_layout.addWidget(self._const_list)

        legend = QLabel(
            '<span style="color:#3fae5a">■ Free</span>  '
            '<span style="color:#5a9bd4">■ Item / event</span>  '
            '<span style="color:#c2704a">■ Trainer</span>  '
            '<span style="color:#d9a441">■ Shared / unclear</span>  '
            '<span style="color:#8a8a8a">■ Engine, badges, temporary</span>')
        legend.setTextFormat(Qt.TextFormat.RichText)
        legend.setWordWrap(True)
        left_layout.addWidget(legend)

        splitter.addWidget(left)

        # ── Right: detail editor ────────────────────────────────────────────
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 0, 0, 0)

        self._detail_name = QLabel("Select a constant from the list")
        self._detail_name.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        self._detail_name.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self._detail_name.setWordWrap(True)
        right_layout.addWidget(self._detail_name)

        self._detail_status = QLabel("")
        self._detail_status.setStyleSheet("font-weight: bold;")
        right_layout.addWidget(self._detail_status)

        self._detail_usage = QLabel("")
        self._detail_usage.setStyleSheet("color: #888; font-size: 11px;")
        self._detail_usage.setWordWrap(True)
        right_layout.addWidget(self._detail_usage)

        right_layout.addWidget(QLabel("Where it is used:"))
        self._detail_refs = QPlainTextEdit()
        self._detail_refs.setReadOnly(True)
        self._detail_refs.setFont(QFont("Consolas", 9))
        self._detail_refs.setMaximumHeight(150)
        self._detail_refs.setPlaceholderText(
            "Select a constant to see every map, script and engine file that "
            "refers to it.")
        right_layout.addWidget(self._detail_refs)

        right_layout.addSpacing(10)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._label_edit = QLineEdit()
        self._label_edit.setPlaceholderText("Friendly display name (optional)…")
        self._label_edit.setMaxLength(80)
        self._label_edit.textChanged.connect(self._on_label_edited)
        self._label_edit.setEnabled(False)
        form.addRow("Label:", self._label_edit)

        self._notes_edit = QPlainTextEdit()
        self._notes_edit.setPlaceholderText(
            "Optional notes — what this flag/var is for, where it's used…")
        self._notes_edit.setMaximumHeight(90)
        self._notes_edit.textChanged.connect(self._on_notes_edited)
        self._notes_edit.setEnabled(False)
        form.addRow("Notes:", self._notes_edit)

        # ── New game ────────────────────────────────────────────────────────
        # Flags are off in a fresh save file. That is wrong for a hide flag:
        # the engine skips an object whose hide flag is ON, so "this person is
        # not here yet" has to START on and be cleared when they should
        # appear. Vanilla switches those on in one script run once at the
        # beginning of a new game, and nothing in PorySuite could read or
        # write it — so a hide flag set in the editor worked perfectly and
        # then never came on, and the NPC stood there from the first save.
        #
        # This is one property of one flag, so it lives here beside the
        # flag's other properties rather than in a screen of its own: a
        # second place to look up what a flag does is a second place for the
        # answer to drift.
        self._newgame_check = QCheckBox("Switched ON when a new game starts")
        self._newgame_check.setEnabled(False)
        self._newgame_check.toggled.connect(self._on_newgame_toggled)
        self._newgame_row = form.rowCount()
        form.addRow("New game:", self._newgame_check)

        self._newgame_value = QLineEdit()
        self._newgame_value.setPlaceholderText("not set — starts at 0")
        self._newgame_value.setMaximumWidth(120)
        self._newgame_value.setEnabled(False)
        self._newgame_value.editingFinished.connect(self._on_newgame_value_set)
        self._newgame_value_row = form.rowCount()
        form.addRow("Starts at:", self._newgame_value)

        self._newgame_note = QLabel("")
        self._newgame_note.setWordWrap(True)
        self._newgame_note.setStyleSheet("color: #888; font-size: 11px;")
        self._newgame_note_row = form.rowCount()
        form.addRow("", self._newgame_note)

        self._form = form
        right_layout.addLayout(form)

        # Advanced / nav buttons
        adv_row = QHBoxLayout()
        self._btn_rename_code = QPushButton("Reclaim / rename in code…")
        self._btn_rename_code.setToolTip(
            "Change the real symbol name across the whole project (not just "
            "the display label). Use this to take over a slot the project no "
            "longer needs. Every file is backed up first.")
        self._btn_rename_code.clicked.connect(self._on_rename_in_code)
        self._btn_rename_code.setEnabled(False)
        adv_row.addWidget(self._btn_rename_code)
        self._btn_use_in_editor = QPushButton("Find in Event Editor")
        self._btn_use_in_editor.setToolTip(
            "Jump to the Event Editor to use this in a script.")
        self._btn_use_in_editor.clicked.connect(self._on_use_in_editor)
        self._btn_use_in_editor.setEnabled(False)
        adv_row.addWidget(self._btn_use_in_editor)
        adv_row.addStretch()
        right_layout.addLayout(adv_row)

        right_layout.addStretch()

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._save_btn = QPushButton("Save Labels")
        self._save_btn.setEnabled(False)
        self._save_btn.clicked.connect(self._save_labels)
        btn_row.addWidget(self._save_btn)
        right_layout.addLayout(btn_row)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #888; margin-top: 4px;")
        root.addWidget(self._status_label)

    # ─── Project loading ────────────────────────────────────────────────────

    def load_project(self, project_dir: str):
        """Load labels and the (fast) constant list. The usage scan runs lazily
        the first time the tab is shown so project open stays snappy."""
        self._project_dir = project_dir
        self._scanned = False
        self._load_labels()
        self._load_constants_fast()
        self._apply_filter()

    def showEvent(self, event):
        super().showEvent(event)
        if self._project_dir and not self._scanned:
            self._run_scan()

    def _load_constants_fast(self):
        """A quick name-only list from the headers so the tab shows instantly.
        Statuses fill in after the lazy scan."""
        self._entries_var = []
        self._entries_flag = []
        self._status_by_name = {}
        if not self._project_dir:
            return
        pdir = Path(self._project_dir)
        blank = {'category': None, 'protection': None, 'reason': '',
                 'refs_total': 0, 'maps': [], 'engine': [], 'scripts': [],
                 'samples': [], 'slot': None, 'region': '',
                 'placeholder': False}
        for name, _val, _ln in vf._parse_defs(pdir, 'VAR_', vf.VARS_HEADER):
            self._entries_var.append(dict(blank, name=name))
        for name, _val, _ln in vf._parse_defs(pdir, 'FLAG_', vf.FLAGS_HEADER):
            self._entries_flag.append(dict(blank, name=name))

    def _run_scan(self, force=False):
        if not self._project_dir:
            return
        if self._scanned and not force:
            return
        QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        self._status_label.setText("Scanning usage…")
        QApplication.processEvents()
        try:
            vf.invalidate_cache(self._project_dir)
            self._entries_var = vf.cached_scan(self._project_dir, 'var')
            self._entries_flag = vf.cached_scan(self._project_dir, 'flag')
        finally:
            QApplication.restoreOverrideCursor()
        self._status_by_name = {}
        for e in self._entries_var:
            self._status_by_name[e['name']] = e
        for e in self._entries_flag:
            self._status_by_name[e['name']] = e
        self._scanned = True
        self._status_label.setText("")
        self._apply_filter()

    def _load_labels(self):
        self._labels = {}
        path = self._labels_path()
        if not path or not os.path.isfile(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "labels" in data:
                self._labels = data["labels"]
        except Exception:
            pass

    def _labels_path(self) -> str:
        if not self._project_dir:
            return ""
        return os.path.join(self._project_dir, _LABELS_FILENAME)

    # ─── Saving labels ──────────────────────────────────────────────────────

    def _save_labels(self):
        path = self._labels_path()
        if not path:
            return
        clean = {}
        for const_name, entry in self._labels.items():
            label = entry.get("label", "").strip()
            notes = entry.get("notes", "").strip()
            if label or notes:
                clean[const_name] = {"label": label, "notes": notes}
        data = {"version": 1, "labels": clean}
        try:
            with open(path, "w", encoding="utf-8", newline='\n') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            self._dirty = False
            self._save_btn.setEnabled(False)
            self._status_label.setText(
                f"Saved {len(clean)} label(s) to {_LABELS_FILENAME}")
            self.labels_changed.emit()
        except Exception as e:
            QMessageBox.warning(self, "Save Error",
                                f"Could not save labels:\n{e}")

    def has_unsaved_changes(self) -> bool:
        return self._dirty

    # ─── List building / filtering ──────────────────────────────────────────

    def _iter_entries(self):
        for e in self._entries_flag:
            yield e, _TYPE_FLAG
        for e in self._entries_var:
            yield e, _TYPE_VAR

    def _apply_filter(self):
        self._const_list.blockSignals(True)
        self._const_list.clear()

        fidx = self._filter_combo.currentIndex()
        search = self._search_box.text().strip().lower()

        rows = sorted(self._iter_entries(), key=lambda t: t[0]['name'])
        for e, ctype in rows:
            name = e['name']
            category = e.get('category')
            protection = e.get('protection')
            if fidx == 1 and ctype != _TYPE_FLAG:
                continue
            if fidx == 2 and ctype != _TYPE_VAR:
                continue
            if fidx == 3 and protection != 'free':
                continue
            if fidx == 4 and protection != 'review':
                continue
            if fidx == 5 and protection not in ('investigate', 'preserve'):
                continue
            if fidx == 6 and protection != 'protected':
                continue

            label_text = self._labels.get(name, {}).get("label", "")
            if search and search not in name.lower() and search not in label_text.lower():
                continue

            if label_text:
                display = f"{name}  —  {label_text}"
            else:
                display = f"{name}  —  ({self._auto_name(name)})"
            if category in vf.CATEGORIES:
                display += f"   ·  {vf.CATEGORIES[category]}"

            item = QListWidgetItem(display)
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setData(Qt.ItemDataRole.UserRole + 1, ctype)
            if category in _CATEGORY_COLOR:
                item.setForeground(QColor(_CATEGORY_COLOR[category]))
            if e.get('reason'):
                item.setToolTip(f"{vf.PROTECTION.get(protection, '')} — "
                                f"{e['reason']}")
            self._const_list.addItem(item)

        self._const_list.blockSignals(False)
        self._update_counts()

    @staticmethod
    def _auto_name(const_name: str) -> str:
        for prefix in ('FLAG_', 'VAR_'):
            if const_name.startswith(prefix):
                return const_name[len(prefix):].replace('_', ' ').title()
        return const_name

    def _update_counts(self):
        total = len(self._entries_var) + len(self._entries_flag)
        visible = self._const_list.count()
        labeled = sum(1 for v in self._labels.values()
                      if v.get("label", "").strip())
        free = sum(1 for e, _ in self._iter_entries()
                   if e.get('protection') == 'free')
        review = sum(1 for e, _ in self._iter_entries()
                     if e.get('protection') == 'review')
        extra = (f" · {free} free · {review} reclaimable"
                 if self._scanned else "")
        self._counts_label.setText(
            f"{visible} shown / {total} total · {labeled} labeled{extra}")
        if self._scanned:
            self._counts_label.setToolTip(self._capacity_tooltip())

    # ─── Detail panel ───────────────────────────────────────────────────────

    def _on_selection_changed(self, current, previous):
        if current is None:
            self._detail_name.setText("Select a constant from the list")
            self._detail_status.setText("")
            self._detail_usage.setText("")
            self._detail_refs.clear()
            for w in (self._label_edit, self._notes_edit,
                      self._btn_rename_code, self._btn_use_in_editor,
                      self._newgame_check, self._newgame_value):
                w.setEnabled(False)
            self._label_edit.clear()
            self._notes_edit.clear()
            # A tick left showing with nothing selected reads as a statement
            # about the project, so clear it — without firing the handler,
            # which would try to edit the game source.
            self._newgame_check.blockSignals(True)
            self._newgame_check.setChecked(False)
            self._newgame_check.blockSignals(False)
            self._newgame_value.blockSignals(True)
            self._newgame_value.clear()
            self._newgame_value.blockSignals(False)
            self._show_newgame_rows(flag=False, var=False, note="")
            return

        name = current.data(Qt.ItemDataRole.UserRole)
        self._detail_name.setText(name)

        e = self._status_by_name.get(name, {})
        category = e.get('category')
        protection = e.get('protection')
        if category in vf.CATEGORIES:
            prot_label = vf.PROTECTION.get(protection, '')
            color = _CATEGORY_COLOR.get(category, '#888')
            slot = e.get('slot')
            where = f"slot {slot} · {e.get('region', '')}" if slot is not None else ""
            self._detail_status.setText(
                f"{vf.CATEGORIES[category]} — {prot_label}"
                + (f"    ({where})" if where else ""))
            self._detail_status.setStyleSheet(
                f"font-weight: bold; color: {color};")
        else:
            self._detail_status.setText("")
            self._detail_status.setStyleSheet("font-weight: bold;")

        if e.get('reason'):
            meaning = _PROTECTION_META.get(protection, ('', False, ''))[2]
            self._detail_usage.setText(
                f"This is {e['reason']}. {meaning}")
        else:
            self._detail_usage.setText("")
        self._detail_refs.setPlainText(self._format_references(e))

        entry = self._labels.get(name, {})
        self._label_edit.blockSignals(True)
        self._label_edit.setText(entry.get("label", ""))
        self._label_edit.setPlaceholderText(f"Auto: {self._auto_name(name)}")
        self._label_edit.setEnabled(True)
        self._label_edit.blockSignals(False)

        self._notes_edit.blockSignals(True)
        self._notes_edit.setPlainText(entry.get("notes", ""))
        self._notes_edit.setEnabled(True)
        self._notes_edit.blockSignals(False)

        self._btn_rename_code.setEnabled(True)
        self._btn_use_in_editor.setEnabled(True)
        self._sync_newgame(name)

    # ─── New game ───────────────────────────────────────────────────────────

    def _newgame(self):
        """The project's new-game script, re-read each time it is needed.

        Not cached. It is a file the user can also edit by hand, and a stale
        cache here would show a tick for a line that is no longer there — a
        checkbox that lies about engine source is worse than one that is a
        few milliseconds slow.
        """
        if not self._project_dir:
            return ngs.NewGameState()
        try:
            return ngs.read(self._project_dir)
        except Exception:
            return ngs.NewGameState()

    def _show_newgame_rows(self, *, flag: bool, var: bool, note: str):
        for row, shown in ((self._newgame_row, flag),
                           (self._newgame_value_row, var),
                           (self._newgame_note_row, bool(note))):
            for role in (QFormLayout.ItemRole.LabelRole,
                         QFormLayout.ItemRole.FieldRole):
                item = self._form.itemAt(row, role)
                if item is not None and item.widget() is not None:
                    item.widget().setVisible(shown)
        self._newgame_note.setText(note)

    def _sync_newgame(self, name: str):
        """Show what the new-game script currently does with *name*."""
        is_flag = name.startswith("FLAG_")
        is_var = name.startswith("VAR_")
        state = self._newgame()

        if not state.found:
            self._newgame_check.setEnabled(False)
            self._newgame_value.setEnabled(False)
            self._show_newgame_rows(
                flag=is_flag, var=is_var,
                note="This project has no new-game script — nothing in "
                     "src/new_game.c hands one to RunScriptImmediately(), so "
                     "there is nowhere to record a starting value.")
            self._newgame_check.blockSignals(True)
            self._newgame_check.setChecked(False)
            self._newgame_check.blockSignals(False)
            self._newgame_value.blockSignals(True)
            self._newgame_value.clear()
            self._newgame_value.blockSignals(False)
            return

        note = ""
        if state.locked:
            note = f"{state.label} cannot be edited safely: {state.locked}"
        elif is_flag:
            note = ("Use this for someone who should not be in the world "
                    "yet. The engine skips an object while its hide flag is "
                    "on, so switch it on here and have the scene clear it.")

        self._newgame_check.blockSignals(True)
        self._newgame_check.setChecked(is_flag and state.sets_flag(name))
        self._newgame_check.setEnabled(is_flag and state.editable)
        self._newgame_check.blockSignals(False)

        self._newgame_value.blockSignals(True)
        self._newgame_value.setText(state.vars.get(name, "") if is_var else "")
        self._newgame_value.setEnabled(is_var and state.editable)
        self._newgame_value.blockSignals(False)

        self._show_newgame_rows(flag=is_flag, var=is_var, note=note)

    def _on_newgame_toggled(self, on: bool):
        """Add or remove this flag's line in the new-game script.

        This writes to the game's own source there and then — it is not part
        of the label file, and there is no Save button for it. So the status
        line says exactly what changed and in which script, and a refused or
        unnecessary write puts the tick back where it was rather than leaving
        the box disagreeing with the file.
        """
        name = self._current_const_name()
        if not name or not name.startswith("FLAG_"):
            return
        backup = ""
        state = self._newgame()
        if state.path is not None:
            try:
                backup = vf.backup_files(
                    self._project_dir, [str(state.path)], self._backup_root())
            except Exception:
                backup = ""
        try:
            changed, msg = ngs.set_flag(self._project_dir, name, on)
        except Exception as exc:
            changed, msg = False, f"could not write the new-game script: {exc}"
        if not changed:
            self._newgame_check.blockSignals(True)
            self._newgame_check.setChecked(self._newgame().sets_flag(name))
            self._newgame_check.blockSignals(False)
            self._status_label.setText(msg)
            return
        if backup:
            msg += f"  (previous version saved in {backup})"
        self._status_label.setText(msg)
        self._sync_newgame(name)

    def _on_newgame_value_set(self):
        """Give this variable a starting value, change it, or clear it."""
        name = self._current_const_name()
        if not name or not name.startswith("VAR_"):
            return
        raw = self._newgame_value.text().strip()
        state = self._newgame()
        current = state.vars.get(name)
        if raw == (current or ""):
            return
        backup = ""
        if state.path is not None:
            try:
                backup = vf.backup_files(
                    self._project_dir, [str(state.path)], self._backup_root())
            except Exception:
                backup = ""
        try:
            changed, msg = ngs.set_var(
                self._project_dir, name, raw if raw else None)
        except Exception as exc:
            changed, msg = False, f"could not write the new-game script: {exc}"
        if changed and backup:
            msg += f"  (previous version saved in {backup})"
        self._status_label.setText(msg)
        self._sync_newgame(name)

    # ─── Reference reporting ────────────────────────────────────────────────

    def _format_references(self, e: dict) -> str:
        """The plain-English "where is this used" block for the detail panel."""
        if not self._scanned:
            return "Checking the project…"
        if not e.get('refs_total'):
            return "Nothing in this project refers to this slot."
        out = [f"Referenced {e['refs_total']} time(s)."]
        for title, key in (("Maps", 'maps'),
                           ("Shared scripts and data", 'scripts'),
                           ("Engine source", 'engine')):
            items = e.get(key) or []
            if not items:
                continue
            out.append("")
            out.append(f"{title} ({len(items)}):")
            for s in items[:40]:
                out.append(f"  - {s}")
            if len(items) > 40:
                out.append(f"  ... and {len(items) - 40} more")
        samples = e.get('samples') or []
        if samples:
            out.append("")
            out.append("First lines:")
            for s in samples[:8]:
                out.append(f"  {s}")
        return "\n".join(out)

    def _capacity_tooltip(self) -> str:
        lines = ["Flags by region (named slots / still free):"]
        for region, d in sorted(vf.capacity(self._entries_flag).items()):
            lines.append(f"  {region}: {d['named']} named, {d['free']} free")
        lines.append("Variables by region:")
        for region, d in sorted(vf.capacity(self._entries_var).items()):
            lines.append(f"  {region}: {d['named']} named, {d['free']} free")
        for noun, entries in (("Flags", self._entries_flag),
                              ("Variables", self._entries_var)):
            free = len(vf.free_names(entries))
            auto = len(vf.auto_claim_names(entries))
            lines.append(
                f"{noun}: {free} free, {auto} of them handed out "
                f"automatically.")
            if free != auto:
                lines.append(
                    f"  The other {free - auto} sit in blocks the engine "
                    f"addresses by slot number, so a template never takes "
                    f"one — reclaim those by hand if you need them.")
        return "\n".join(lines)

    def _current_const_name(self) -> str:
        item = self._const_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else ""

    def _on_label_edited(self, text: str):
        name = self._current_const_name()
        if not name:
            return
        self._labels.setdefault(name, {})["label"] = text
        self._mark_dirty()
        item = self._const_list.currentItem()
        if item:
            category = self._status_by_name.get(name, {}).get('category')
            base = (f"{name}  —  {text}" if text.strip()
                    else f"{name}  —  ({self._auto_name(name)})")
            if category in vf.CATEGORIES:
                base += f"   ·  {vf.CATEGORIES[category]}"
            item.setText(base)

    def _on_notes_edited(self):
        name = self._current_const_name()
        if not name:
            return
        self._labels.setdefault(name, {})["notes"] = self._notes_edit.toPlainText()
        self._mark_dirty()

    def _mark_dirty(self):
        if not self._dirty:
            self.modified.emit()
        self._dirty = True
        self._save_btn.setEnabled(True)

    # ─── Create / repurpose ─────────────────────────────────────────────────

    def _all_names(self) -> set:
        return {e['name'] for e, _ in self._iter_entries()}

    def _on_new(self, kind: str):
        if not self._project_dir:
            QMessageBox.information(self, "New", "Open a project first.")
            return
        self._run_scan()  # need reliable free-slot info
        entries = self._entries_var if kind == 'var' else self._entries_flag
        free = vf.first_free(entries)
        if not free:
            QMessageBox.warning(
                self, "New",
                "No free slots left. Reclaim one instead: set Show to "
                "\"Reclaim candidates\", pick a slot, check what uses it on "
                "the right, then click \"Reclaim / rename in code\".")
            return
        noun = 'variable' if kind == 'var' else 'flag'
        text, ok = QInputDialog.getText(
            self, f"New {noun.title()}",
            f"Name for this {noun} (plain words are fine, e.g. "
            f"\"Side Quest\"):")
        if not ok:
            return
        new = vf.normalize_name(kind, text)
        err = vf.validate_name(kind, new, self._all_names())
        if err:
            QMessageBox.warning(self, "New", err)
            return
        self._do_code_rename(free['name'], new, kind,
                             friendly=text.strip(), is_new=True)

    def _reference_summary(self, e: dict) -> str:
        bits = []
        for title, key in (("map", 'maps'), ("shared script", 'scripts'),
                           ("engine file", 'engine')):
            items = e.get(key) or []
            if items:
                plural = "" if len(items) == 1 else "s"
                bits.append(f"{len(items)} {title}{plural}")
        return ", ".join(bits) if bits else "nowhere"

    def _confirm_reclaim(self, name: str, e: dict) -> bool:
        """Ask before taking over a slot. Returns True to go ahead.

        Three gates, by how much is known about the slot: none for a free one,
        a plain yes/no for a single-use one, and typing the slot's own name for
        anything shared, trainer-numbered or otherwise unclear. A protected
        slot is simply refused.
        """
        protection = e.get('protection')
        if not self._scanned:
            self._run_scan()
            e = self._status_by_name.get(name, e)
            protection = e.get('protection')
        label, allowed, meaning = _PROTECTION_META.get(
            protection, ("Unclassified", False,
                         "This slot has not been checked yet."))

        if protection == 'free':
            return True

        detail = (f"{name} is {e.get('reason', 'in use')}.\n\n"
                  f"{meaning}\n\n"
                  f"Used in: {self._reference_summary(e)}.\n\n"
                  "Everything listed in the panel on the right will be "
                  "rewritten to the new name. The slot number does not "
                  "change, so an existing save file keeps whatever this bit "
                  "was already set to. Every file is copied to a backup "
                  "folder first.")

        if allowed:
            return QMessageBox.question(
                self, f"Reclaim {name}?",
                f"{label}\n\n{detail}\n\nGo ahead?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            ) == QMessageBox.StandardButton.Yes

        if protection == 'protected':
            QMessageBox.information(
                self, "Protected",
                f"{name} is {e.get('reason', 'protected')}.\n\n{meaning}\n\n"
                "PorySuite will not rename this one. If you are certain it "
                "is dead in your project, change it in the header by hand.")
            return False

        # preserve / investigate — make the user type the name out.
        typed, ok = QInputDialog.getText(
            self, f"Reclaim {name}?",
            f"{label}\n\n{detail}\n\n"
            f"Type {name} exactly to confirm you have checked the "
            f"references:")
        if not ok:
            return False
        if typed.strip() != name:
            QMessageBox.information(
                self, "Not reclaimed",
                "That did not match, so nothing was changed.")
            return False
        return True

    def _on_rename_in_code(self):
        name = self._current_const_name()
        if not name:
            return
        e = self._status_by_name.get(name, {})
        kind = 'flag' if name.startswith('FLAG_') else 'var'
        if not self._confirm_reclaim(name, e):
            return
        text, ok = QInputDialog.getText(
            self, "Reclaim / rename in code", "New name:",
            text=self._auto_name(name))
        if not ok:
            return
        new = vf.normalize_name(kind, text)
        err = vf.validate_name(kind, new, self._all_names() - {name})
        if err:
            QMessageBox.warning(self, "Rename", err)
            return
        self._do_code_rename(name, new, kind, friendly='', is_new=False)

    def _backup_root(self) -> str:
        """Where snapshots of renamed files go — inside PorySuite's own cache,
        never inside the game tree."""
        # Imported here, not at module scope: app_info lives in core/, which
        # only joins sys.path once app.py has started.
        try:
            try:
                from app_info import get_cache_dir
            except ImportError:
                from core.app_info import get_cache_dir
            return os.path.join(get_cache_dir(self._project_dir),
                                "flag_backups")
        except Exception:
            return ""

    def _do_code_rename(self, old: str, new: str, kind: str,
                        friendly: str, is_new: bool):
        QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        try:
            n, _files, backup = vf.rename_symbol(
                self._project_dir, old, new, backup_root=self._backup_root())
        finally:
            QApplication.restoreOverrideCursor()

        # Carry any existing label/notes to the new name.
        if old in self._labels:
            self._labels[new] = self._labels.pop(old)
        # A friendly display name from the New dialog only if it differs from
        # the auto-generated one (otherwise the auto name already reads fine).
        if friendly and friendly.lower() != self._auto_name(new).lower():
            self._labels.setdefault(new, {})["label"] = friendly
            self._save_labels()

        try:
            from eventide.backend.constants_manager import ConstantsManager
            ConstantsManager.refresh()
        except Exception:
            pass

        self._run_scan(force=True)
        self.constants_changed.emit()
        self.select_constant(new)
        verb = "Created" if is_new else "Reclaimed"
        msg = f"{verb} {new}.\nUpdated {n} file(s)."
        if backup:
            msg += (f"\n\nThe previous version of every file that changed is "
                    f"in:\n{backup}")
        QMessageBox.information(self, verb, msg)

    def _on_use_in_editor(self):
        name = self._current_const_name()
        if name and _open_event_editor_cb:
            _open_event_editor_cb(name)

    # ─── Public API ─────────────────────────────────────────────────────────

    def select_constant(self, name: str):
        for i in range(self._const_list.count()):
            item = self._const_list.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) == name:
                self._const_list.setCurrentItem(item)
                self._const_list.scrollToItem(
                    item, QAbstractItemView.ScrollHint.PositionAtCenter)
                return
        self._filter_combo.setCurrentIndex(0)
        self._search_box.clear()
        self._apply_filter()
        for i in range(self._const_list.count()):
            item = self._const_list.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) == name:
                self._const_list.setCurrentItem(item)
                self._const_list.scrollToItem(
                    item, QAbstractItemView.ScrollHint.PositionAtCenter)
                return

    def focus_new(self, kind: str = 'var'):
        """Open the New dialog for *kind* — used by EVENTide's "＋" buttons."""
        self._on_new(kind)

    def get_labels(self) -> dict[str, dict]:
        return dict(self._labels)
