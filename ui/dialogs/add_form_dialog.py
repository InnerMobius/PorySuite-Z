"""Add-Form wizard (PorySuite-Z).

One cohesive step to set up a new form: choose whether it's a brand-new species
(name + graphics) or an existing mon to link, AND its in-game trigger (what makes
the base turn into it) — all in one dialog. On accept the caller creates/links the
form AND writes the trigger as a form-change rule, so the form is wired end to end
without a separate trip to Form Changes.
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QComboBox, QLineEdit, QLabel,
    QDialogButtonBox)

from ui.custom_widgets.scroll_guard import install_scroll_guard

# (label, (own_image, own_palette))
# Order matters: the FIRST entry is the default, and it must be the fully
# independent "own image + own palette" mode — that is what "give the form its
# own graphics" means. The other two REUSE the base's image (they render a frame
# of the base's sheet), which on a normal single-frame mon makes the form look
# identical to the base ("duping"); they're only useful for a base whose sheet
# actually stacks multiple frames (e.g. vanilla Deoxys).
_GFX_MODES = [
    ("Its own image + its own palette  (separate, editable — recommended)",
     (True, True)),
    ("Its own palette, but REUSES the base's image", (False, True)),
    ("REUSES the base's image and palette", (False, False)),
]
# (label, FORM_CHANGE_* constant); "" = no automatic trigger.
# FORM_CHANGE_ITEM_USE is intentionally omitted: it has no in-game dispatch hook yet
# (it needs the trigger item to be a party-usable field item), so offering it on a new
# form would be a dead option. Add it back here once that feature lands.
_TRIGGERS = [
    ("No automatic trigger (set it later)", ""),
    ("Holds an item", "FORM_CHANGE_ITEM_HOLD"),
    ("Time of day", "FORM_CHANGE_TIME_OF_DAY"),
    ("Overworld weather", "FORM_CHANGE_WEATHER"),
    ("After a story flag is set", "FORM_CHANGE_FLAG"),
    ("Has a status condition", "FORM_CHANGE_STATUS"),
]


class AddFormDialog(QDialog):
    """Collects everything to set up a form in one pass.

    base_label     : display name of the base species.
    species_choices: [(label, SPECIES_* const)] for the "existing mon" picker.
    param_options  : {method_const: [(value_const, label), …]} for the dynamic
                     trigger Parameter dropdown.
    """

    def __init__(self, parent, base_label, species_choices, param_options):
        super().__init__(parent)
        self.setWindowTitle(f"Add Form — {base_label}")
        self.setMinimumWidth(480)
        self._param_options = param_options or {}

        v = QVBoxLayout(self)
        v.addWidget(QLabel(
            f"Add a form to <b>{base_label}</b> and set what makes it appear "
            f"in-game — it reverts to <b>{base_label}</b> when that no longer "
            f"applies."))

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        v.addLayout(form)

        self._type = QComboBox()
        self._type.addItem("Create a new form (its own species)", "new")
        self._type.addItem("Use an existing Pokémon (link it)", "existing")
        install_scroll_guard(self._type)
        form.addRow("Form is:", self._type)

        # New-form fields
        self._name = QLineEdit()
        self._name.setPlaceholderText("e.g. ATTACK, RAINY, MEGA")
        self._gfx = QComboBox()
        for label, _ in _GFX_MODES:
            self._gfx.addItem(label)
        self._gfx.setCurrentIndex(0)   # default: its own image + own palette
        install_scroll_guard(self._gfx)
        self._name_lbl = QLabel("Name:")
        self._gfx_lbl = QLabel("Graphics:")
        form.addRow(self._name_lbl, self._name)
        form.addRow(self._gfx_lbl, self._gfx)

        # Existing-mon field
        self._existing = QComboBox()
        for label, const in species_choices:
            self._existing.addItem(label.strip(), const)
        install_scroll_guard(self._existing)
        self._existing_lbl = QLabel("Pokémon:")
        form.addRow(self._existing_lbl, self._existing)

        # Trigger
        self._trig = QComboBox()
        for label, const in _TRIGGERS:
            self._trig.addItem(label, const)
        install_scroll_guard(self._trig)
        form.addRow("Appears when:", self._trig)
        self._param = QComboBox()
        install_scroll_guard(self._param)
        self._param_lbl = QLabel("Parameter:")
        form.addRow(self._param_lbl, self._param)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._on_ok)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

        self._type.currentIndexChanged.connect(self._sync_type)
        self._trig.currentIndexChanged.connect(self._rebuild_param)
        if not species_choices:                  # nothing to link → force "new"
            self._type.model().item(1).setEnabled(False)
        self._sync_type()
        self._rebuild_param()

    def _sync_type(self):
        new = self._type.currentData() == "new"
        for w in (self._name, self._gfx, self._name_lbl, self._gfx_lbl):
            w.setVisible(new)
        for w in (self._existing, self._existing_lbl):
            w.setVisible(not new)

    def _rebuild_param(self):
        opts = self._param_options.get(self._trig.currentData() or "", [])
        self._param.blockSignals(True)
        self._param.clear()
        if not opts:
            self._param.addItem("—", "0")
            self._param.setEnabled(False)
        else:
            self._param.setEnabled(True)
            for val, label in opts:
                self._param.addItem(label, val)
        self._param.blockSignals(False)

    def _on_ok(self):
        from PyQt6.QtWidgets import QMessageBox
        if self._type.currentData() == "new" and not self.form_suffix():
            QMessageBox.warning(self, "Add Form", "Enter a form name.")
            return
        if self._type.currentData() == "existing" and not self._existing.currentData():
            QMessageBox.warning(self, "Add Form", "Pick a Pokémon to link.")
            return
        self.accept()

    # ── results ──
    def is_existing(self):
        return self._type.currentData() == "existing"

    def existing_const(self):
        return self._existing.currentData()

    def form_suffix(self):
        import re
        return re.sub(r"[^A-Za-z0-9_]", "",
                      (self._name.text() or "").strip().upper().replace(" ", "_"))

    def graphics_mode(self):
        """(own_image, own_palette) for the chosen graphics mode."""
        return _GFX_MODES[self._gfx.currentIndex()][1]

    def trigger(self):
        """(method_const, param) for the chosen trigger, or (None, None)."""
        method = self._trig.currentData()
        if not method:
            return (None, None)
        param = self._param.currentData() if self._param.isEnabled() else "0"
        return (method, param or "0")
