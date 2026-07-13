"""
label_manager.py — Variables & Flags manager for PorySuite-Z

The single place to name and reuse the game's variable / flag slots, RPG-Maker
style. Two layers, one screen:

* **Friendly labels + notes** (non-destructive): give a constant a readable
  display name without touching game code. Saved per project in
  ``porysuite_labels.json``. The Event Editor shows these labels in script lines.
* **Create / repurpose** (structural, via ``var_flag_manager``): claim a free
  slot as a brand-new named variable/flag (safe — free slots have no references),
  or repurpose a vanilla one you don't need with a whole-project rename and a
  clear warning first.

Every slot is colour-coded by real usage (free / your scripts / vanilla engine /
reserved), computed from an actual project scan — so you can see at a glance what
is safe to reclaim.
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
)

from eventide.backend import var_flag_manager as vf


# ─── Helpers ────────────────────────────────────────────────────────────────

_LABELS_FILENAME = "porysuite_labels.json"

_TYPE_FLAG = "flag"
_TYPE_VAR = "var"

# Friendly label + colour for each backend usage status.
_STATUS_META = {
    'free':     ('Free',                  '#3fae5a'),
    'unused':   ('Free (vanilla name)',   '#3fae5a'),
    'yours':    ('Your scripts',          '#5a9bd4'),
    'vanilla':  ('Vanilla engine',        '#d9a441'),
    'reserved': ('Reserved (locked)',     '#8a8a8a'),
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
            "repurpose a vanilla one you don't need.")
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: #888; margin-bottom: 6px;")
        root.addWidget(subtitle)

        # ── Action row: New buttons + refresh ───────────────────────────────
        action_row = QHBoxLayout()
        self._btn_new_var = QPushButton("New Variable…")
        self._btn_new_var.setToolTip(
            "Create a new variable: type a plain name (e.g. \"Cucco Quest\") "
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
            ["All", "Flags", "Vars", "Free only", "Vanilla (reclaimable)"])
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
            '<span style="color:#5a9bd4">■ Your scripts</span>  '
            '<span style="color:#d9a441">■ Vanilla engine</span>  '
            '<span style="color:#8a8a8a">■ Reserved</span>')
        legend.setTextFormat(Qt.TextFormat.RichText)
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
        right_layout.addLayout(form)

        # Advanced / nav buttons
        adv_row = QHBoxLayout()
        self._btn_rename_code = QPushButton("Rename in code…")
        self._btn_rename_code.setToolTip(
            "Advanced: change the real symbol name across the whole project "
            "(not just the display label). Use to reclaim a vanilla slot.")
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
        for name, _val, _ln in vf._parse_defs(pdir, 'VAR_', vf.VARS_HEADER):
            self._entries_var.append({'name': name, 'status': None,
                                      'refs_total': 0, 'samples': []})
        for name, _val, _ln in vf._parse_defs(pdir, 'FLAG_', vf.FLAGS_HEADER):
            self._entries_flag.append({'name': name, 'status': None,
                                       'refs_total': 0, 'samples': []})

    def _run_scan(self, force=False):
        if not self._project_dir:
            return
        if self._scanned and not force:
            return
        QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        self._status_label.setText("Scanning usage…")
        QApplication.processEvents()
        try:
            self._entries_var = vf.scan(self._project_dir, 'var')
            self._entries_flag = vf.scan(self._project_dir, 'flag')
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
            status = e.get('status')
            if fidx == 1 and ctype != _TYPE_FLAG:
                continue
            if fidx == 2 and ctype != _TYPE_VAR:
                continue
            if fidx == 3 and status not in ('free', 'unused'):
                continue
            if fidx == 4 and status not in ('unused', 'vanilla'):
                continue

            label_text = self._labels.get(name, {}).get("label", "")
            if search and search not in name.lower() and search not in label_text.lower():
                continue

            if label_text:
                display = f"{name}  —  {label_text}"
            else:
                display = f"{name}  —  ({self._auto_name(name)})"
            if status and status in _STATUS_META:
                display += f"   ·  {_STATUS_META[status][0]}"

            item = QListWidgetItem(display)
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setData(Qt.ItemDataRole.UserRole + 1, ctype)
            if status and status in _STATUS_META:
                item.setForeground(QColor(_STATUS_META[status][1]))
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
                   if e.get('status') in ('free', 'unused'))
        extra = f" · {free} free" if self._scanned else ""
        self._counts_label.setText(
            f"{visible} shown / {total} total · {labeled} labeled{extra}")

    # ─── Detail panel ───────────────────────────────────────────────────────

    def _on_selection_changed(self, current, previous):
        if current is None:
            self._detail_name.setText("Select a constant from the list")
            self._detail_status.setText("")
            self._detail_usage.setText("")
            for w in (self._label_edit, self._notes_edit,
                      self._btn_rename_code, self._btn_use_in_editor):
                w.setEnabled(False)
            self._label_edit.clear()
            self._notes_edit.clear()
            return

        name = current.data(Qt.ItemDataRole.UserRole)
        self._detail_name.setText(name)

        e = self._status_by_name.get(name, {})
        status = e.get('status')
        if status and status in _STATUS_META:
            lbl, color = _STATUS_META[status]
            self._detail_status.setText(lbl)
            self._detail_status.setStyleSheet(f"font-weight: bold; color: {color};")
        else:
            self._detail_status.setText("")
        if e.get('refs_total'):
            samples = '  ·  '.join(e.get('samples', [])[:3])
            self._detail_usage.setText(
                f"Used in {e['refs_total']} place(s): {samples}")
        else:
            self._detail_usage.setText(
                "Not referenced anywhere." if self._scanned else "")

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

        self._btn_rename_code.setEnabled(status != 'reserved')
        self._btn_use_in_editor.setEnabled(True)

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
            status = self._status_by_name.get(name, {}).get('status')
            base = (f"{name}  —  {text}" if text.strip()
                    else f"{name}  —  ({self._auto_name(name)})")
            if status and status in _STATUS_META:
                base += f"   ·  {_STATUS_META[status][0]}"
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
                "No free slots left. Repurpose a vanilla one instead: select "
                "it and click \"Rename in code\".")
            return
        noun = 'variable' if kind == 'var' else 'flag'
        text, ok = QInputDialog.getText(
            self, f"New {noun.title()}",
            f"Name for this {noun} (plain words are fine, e.g. "
            f"\"Cucco Quest\"):")
        if not ok:
            return
        new = vf.normalize_name(kind, text)
        err = vf.validate_name(kind, new, self._all_names())
        if err:
            QMessageBox.warning(self, "New", err)
            return
        self._do_code_rename(free['name'], new, kind,
                             friendly=text.strip(), is_new=True)

    def _on_rename_in_code(self):
        name = self._current_const_name()
        if not name:
            return
        e = self._status_by_name.get(name, {})
        status = e.get('status')
        kind = 'flag' if name.startswith('FLAG_') else 'var'
        if status == 'reserved':
            QMessageBox.information(
                self, "Reserved",
                f"{name} is reserved by the engine and can't be renamed.")
            return
        if status in ('vanilla', 'yours'):
            where = '\n'.join('  • ' + s for s in e.get('samples', [])[:5])
            note = ("\n\nRepurposing is safe ONLY if your hack doesn't use "
                    "that vanilla feature." if status == 'vanilla' else
                    "\n\nEvery place below updates to the new name.")
            if QMessageBox.question(
                    self, "Repurpose?",
                    f"{name} is used in {e.get('refs_total', 0)} place(s):\n"
                    f"{where}{note}\n\nRepurpose it anyway?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
        text, ok = QInputDialog.getText(
            self, "Rename in code", "New name:",
            text=self._auto_name(name))
        if not ok:
            return
        new = vf.normalize_name(kind, text)
        err = vf.validate_name(kind, new, self._all_names() - {name})
        if err:
            QMessageBox.warning(self, "Rename", err)
            return
        self._do_code_rename(name, new, kind, friendly='', is_new=False)

    def _do_code_rename(self, old: str, new: str, kind: str,
                        friendly: str, is_new: bool):
        QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        try:
            n, _files = vf.rename_symbol(self._project_dir, old, new)
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
        verb = "Created" if is_new else "Renamed"
        QMessageBox.information(
            self, verb, f"{verb} {new}.\nUpdated {n} file(s).")

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
