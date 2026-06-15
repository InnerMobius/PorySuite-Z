"""Form-change editor (PorySuite-Z Layer B).

A form change in the (pokeemerald-expansion) model is simply "this species turns
into THAT species" under a trigger — so the **Becomes** target is any mon in the
dex. The editor is a reusable ``FormChangePanel`` (embedded inline in the Pokémon
**Forms** sub-tab) plus a thin ``FormChangeDialog`` wrapper (used by the tree's
right-click menu). Each rule: hold an item / use an item / time of day / overworld
weather → becomes <species>, with the **Parameter** a project-loaded dropdown that
swaps to match the trigger. They collect entries only; core.append_species.
set_form_change writes them.
"""

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QComboBox,
    QPushButton, QLabel, QDialogButtonBox, QHeaderView)

from ui.custom_widgets.scroll_guard import install_scroll_guard

# (display label, FORM_CHANGE_* constant)
_METHODS = [
    ("Holds an item", "FORM_CHANGE_ITEM_HOLD"),
    ("Has an item used on it", "FORM_CHANGE_ITEM_USE"),
    ("Time of day", "FORM_CHANGE_TIME_OF_DAY"),
    ("Overworld weather", "FORM_CHANGE_WEATHER"),
    ("After a story flag is set", "FORM_CHANGE_FLAG"),
    ("Has a status condition", "FORM_CHANGE_STATUS"),
]
# Triggers not yet wired to an in-game dispatch hook are hidden from NEW rules so
# the user isn't offered a dead option. ITEM_USE needs the trigger item to be a
# party-usable field item (its own field-use callback) plus a morph hook — a larger
# feature, scoped separately. An EXISTING rule using a hidden method is still shown
# (so it round-trips), but it can't be newly selected.
_UNWIRED_METHODS = {"FORM_CHANGE_ITEM_USE"}


def _methods_for(current=None):
    """Trigger choices for a combo: hide unwired methods unless this row already
    uses one (so existing rules still display + round-trip)."""
    return [m for m in _METHODS
            if m[1] not in _UNWIRED_METHODS or m[1] == current]


class FormChangePanel(QWidget):
    """Embeddable editor for one species' in-game form-change rules. Call
    ``load(who_label, species_choices, param_options, entries)`` to populate and
    ``entries()`` to read the edited rules back. Emits ``modified`` on any
    user edit (so the host can mark the section dirty); ``load()`` repopulates
    without emitting (guarded by ``_loading``)."""

    modified = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._choices = []
        self._param_options = {}
        self._loading = False

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        self._intro = QLabel()
        self._intro.setWordWrap(True)
        v.addWidget(self._intro)

        self.table = QTableWidget(0, 3, self)
        self.table.setHorizontalHeaderLabels(["When (trigger)", "Becomes", "Parameter"])
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        v.addWidget(self.table)

        bar = QHBoxLayout()
        self._add_btn = QPushButton("Add rule")
        self._rem_btn = QPushButton("Remove selected")
        self._add_btn.clicked.connect(lambda: self._add_row())
        self._rem_btn.clicked.connect(self._remove_row)
        bar.addWidget(self._add_btn)
        bar.addWidget(self._rem_btn)
        bar.addStretch(1)
        v.addLayout(bar)

    def load(self, who_label, species_choices, param_options, entries):
        self._choices = species_choices or []
        self._param_options = param_options or {}
        self._intro.setText(
            f"Rules for what makes <b>{who_label}</b> turn into another species "
            f"in-game — the target can be any mon in the dex. The change holds "
            f"while its condition is true and reverts to <b>{who_label}</b> when it "
            f"no longer applies. Time of day needs a clock in your project.")
        # Repopulating is not a user edit — guard so it never fires `modified`.
        self._loading = True
        try:
            self.table.setRowCount(0)
            for e in (entries or []):
                self._add_row(e)
        finally:
            self._loading = False

    def set_enabled(self, on):
        self.table.setEnabled(on)
        self._add_btn.setEnabled(on)
        self._rem_btn.setEnabled(on)

    def _combo(self, options, selected):
        c = QComboBox()
        for label, const in options:
            c.addItem(label, const)
        if selected is not None:
            i = c.findData(selected)
            if i >= 0:
                c.setCurrentIndex(i)
        install_scroll_guard(c)
        return c

    def _add_row(self, entry=None):
        e = entry if isinstance(entry, dict) else {}
        r = self.table.rowCount()
        self.table.insertRow(r)
        trig = self._combo(_methods_for(e.get("method")), e.get("method"))
        self.table.setCellWidget(r, 0, trig)
        target = self._combo(self._choices, e.get("target"))
        self.table.setCellWidget(r, 1, target)
        param = QComboBox()
        install_scroll_guard(param)
        self.table.setCellWidget(r, 2, param)
        # Resolve the row from the combo at signal time, NOT a captured index — a
        # later removeRow shifts rows up, so a captured `r` would rebuild/edit the
        # WRONG row's Parameter. _row_of() finds the combo's current row.
        trig.currentIndexChanged.connect(lambda _=0, c=trig: self._on_trigger_changed(c))
        target.currentIndexChanged.connect(self._emit_modified)
        param.currentIndexChanged.connect(self._emit_modified)
        self._rebuild_param(r, preselect=e.get("param"))
        if not self._loading:
            self.modified.emit()

    def _row_of(self, widget):
        """Current row of a cell widget — combos move when rows are removed."""
        for r in range(self.table.rowCount()):
            if self.table.cellWidget(r, 0) is widget:
                return r
        return -1

    def _on_trigger_changed(self, trig):
        row = self._row_of(trig)
        if row >= 0:
            self._rebuild_param(row)
        self._emit_modified()

    def _emit_modified(self, *args):
        if not self._loading:
            self.modified.emit()

    def _rebuild_param(self, row, preselect=None):
        trig = self.table.cellWidget(row, 0)
        param = self.table.cellWidget(row, 2)
        if trig is None or param is None:
            return
        opts = self._param_options.get(trig.currentData() or "", [])
        param.blockSignals(True)
        param.clear()
        if not opts:
            param.addItem("—", "0")
            param.setEnabled(False)
        else:
            param.setEnabled(True)
            for val, label in opts:
                param.addItem(label, val)
            if preselect:
                i = param.findData(preselect)
                if i >= 0:
                    param.setCurrentIndex(i)
        param.blockSignals(False)

    def _remove_row(self):
        r = self.table.currentRow()
        if r >= 0:
            self.table.removeRow(r)
            self._emit_modified()

    def entries(self):
        """Collect the edited rows → [{method, target, param}]."""
        out = []
        for r in range(self.table.rowCount()):
            tw, sw = self.table.cellWidget(r, 0), self.table.cellWidget(r, 1)
            param = self.table.cellWidget(r, 2)
            if tw is None or sw is None:
                continue
            method, target = tw.currentData(), sw.currentData()
            pval = param.currentData() if (param and param.isEnabled()) else "0"
            if method and target:
                out.append({"method": method, "target": target, "param": pval or "0"})
        return out


class FormChangeDialog(QDialog):
    """Thin dialog wrapper around FormChangePanel (used by the tree right-click)."""

    def __init__(self, parent, who_label, species_choices, param_options, entries):
        super().__init__(parent)
        self.setWindowTitle(f"Form Changes — {who_label}")
        self.resize(760, 360)
        v = QVBoxLayout(self)
        self.panel = FormChangePanel(self)
        self.panel.load(who_label, species_choices, param_options, entries)
        v.addWidget(self.panel)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def entries(self):
        return self.panel.entries()
