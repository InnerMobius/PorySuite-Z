"""
ui/moves_tab_widget.py
Modernised Moves Editor
  Left  – searchable / filterable list of all moves
  Right – scrollable detail panel covering every field of the Move struct
"""

from __future__ import annotations

import re

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QFrame,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QScrollArea, QSizePolicy,
    QSpinBox, QSplitter, QVBoxLayout, QWidget,
)

from ui.dex_description_edit import DexDescriptionEdit


# ── known constants (imported from single source of truth) ────────────────────
from ui.constants import (
    TYPE_CHOICES, MOVE_TARGET_CHOICES as TARGET_CHOICES,
    MOVE_FLAGS as ALL_FLAGS, EFFECT_CHOICES,
    PHYSICAL_TYPES as _PHYSICAL_TYPES,
    SPECIAL_TYPES as _SPECIAL_TYPES,
    TYPE_COLORS as _TYPE_COLORS,
)

# _PHYSICAL_TYPES, _SPECIAL_TYPES, _TYPE_COLORS imported from ui.constants

MOVE_NAME_LENGTH = 12  # FireRed max in-game move name length


def _gen3_category(move_type: str, power: int) -> str:
    """Return Physical / Special / Status based on Gen 3 type-based split."""
    if not power:
        return "Status"
    if move_type in _PHYSICAL_TYPES:
        return "Physical"
    if move_type in _SPECIAL_TYPES:
        return "Special"
    return "—"


def _display_to_constant(display_name: str) -> str:
    """Derive MOVE_CONSTANT from a display name like 'Shadow Rush'."""
    text = display_name.strip().upper()
    text = re.sub(r"[^A-Z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return f"MOVE_{text}" if text else ""


# ── stylesheet constants ───────────────────────────────────────────────────────

_CARD_SS = """
QGroupBox {
    font-weight: bold;
    font-size: 10px;
    border: 1px solid #383838;
    border-radius: 6px;
    margin-top: 10px;
    padding-top: 6px;
    background-color: #252525;
    color: #cccccc;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 5px;
    color: #777777;
    font-size: 9px;
    text-transform: uppercase;
    letter-spacing: 1px;
}
"""

_FIELD_SS = """
QLineEdit, QSpinBox, QComboBox, QPlainTextEdit {
    background-color: #1e1e1e;
    border: 1px solid #3a3a3a;
    border-radius: 4px;
    padding: 4px 7px;
    color: #e0e0e0;
    font-size: 12px;
    selection-background-color: #1565c0;
}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {
    border: 1px solid #1976d2;
}
QLineEdit:read-only { color: #888888; }
QComboBox::drop-down { border: none; padding-right: 6px; }
QSpinBox::up-button, QSpinBox::down-button {
    background-color: #333333; border: none; width: 16px;
}
QCheckBox { color: #cccccc; font-size: 12px; spacing: 6px; }
QCheckBox::indicator {
    width: 14px; height: 14px;
    border: 1px solid #555555;
    border-radius: 3px;
    background-color: #1e1e1e;
}
QCheckBox::indicator:checked {
    background-color: #1976d2;
    border-color: #1976d2;
}
"""

_LIST_SS = """
QListWidget {
    background-color: #191919;
    border: none;
    outline: 0;
    font-size: 12px;
}
QListWidget::item {
    padding: 5px 8px;
    border-bottom: 1px solid #1f1f1f;
    color: #cccccc;
}
QListWidget::item:selected {
    background-color: #1565c0;
    color: #ffffff;
    border-bottom: 1px solid #1565c0;
}
QListWidget::item:hover:!selected {
    background-color: #232323;
}
"""

_SEARCH_SS = """
QLineEdit {
    background-color: #1e1e1e;
    border: none;
    border-bottom: 1px solid #383838;
    padding: 6px 10px;
    color: #cccccc;
    font-size: 12px;
    border-radius: 0px;
}
QLineEdit:focus { border-bottom: 1px solid #1976d2; }
"""

_FILTER_COMBO_SS = """
QComboBox {
    background-color: #1e1e1e;
    border: none;
    border-bottom: 1px solid #383838;
    padding: 4px 10px;
    color: #aaaaaa;
    font-size: 11px;
    border-radius: 0px;
}
QComboBox:focus { border-bottom: 1px solid #1976d2; }
QComboBox::drop-down { border: none; padding-right: 6px; }
QComboBox QAbstractItemView {
    background-color: #1e1e1e;
    color: #cccccc;
    border: 1px solid #3a3a3a;
    selection-background-color: #1565c0;
}
"""

_SCROLL_SS = """
QScrollArea { background-color: #1a1a1a; border: none; }
QScrollBar:vertical {
    background-color: #1a1a1a; width: 8px; border: none;
}
QScrollBar::handle:vertical {
    background-color: #444444; border-radius: 4px; min-height: 20px;
}
QScrollBar::handle:vertical:hover { background-color: #555555; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
"""

_CATEGORY_SS = {
    "Physical": "color: #ef5350; font-size: 11px; font-weight: bold;",
    "Special":  "color: #42a5f5; font-size: 11px; font-weight: bold;",
    "Status":   "color: #aaaaaa; font-size: 11px; font-weight: bold;",
    "—":        "color: #666666; font-size: 11px;",
}

_BTN_SS = """
    QPushButton {
        background-color: #2a2a2a; color: #aaaaaa;
        border: 1px solid #3a3a3a; border-radius: 4px;
        font-size: 11px; padding: 4px 10px;
    }
    QPushButton:hover  { background-color: #333333; color: #cccccc; }
    QPushButton:pressed { background-color: #222222; }
    QPushButton:disabled { color: #555555; border-color: #2a2a2a; }
"""


# ── helpers ───────────────────────────────────────────────────────────────────

def _lbl(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet("color: #888888; font-size: 11px;")
    return lbl


def _hint(text: str) -> QLabel:
    """Small muted description label placed below a field."""
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setStyleSheet("color: #606060; font-size: 9px; padding: 0 0 2px 2px; margin: 0;")
    return lbl


def _card(title: str) -> tuple[QGroupBox, QFormLayout]:
    box = QGroupBox(title)
    box.setStyleSheet(_CARD_SS)
    form = QFormLayout(box)
    form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    form.setHorizontalSpacing(10)
    form.setVerticalSpacing(6)
    form.setContentsMargins(10, 14, 10, 10)
    return box, form


# ── NewMoveDialog ─────────────────────────────────────────────────────────────

class NewMoveDialog(QDialog):
    """Dialog for entering a new move's display name and constant."""

    def __init__(self, parent=None, existing_constants: set | None = None):
        super().__init__(parent)
        self._existing = existing_constants or set()
        self._auto_sync = True
        self.setWindowTitle("New Move")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # Display name
        self.display_edit = QLineEdit()
        self.display_edit.setMaxLength(MOVE_NAME_LENGTH)
        self.display_edit.setPlaceholderText("e.g. Shadow Rush")
        self.display_edit.setToolTip(
            f"The in-game move name (max {MOVE_NAME_LENGTH} characters).\n"
            "The constant below auto-derives from this."
        )
        counter = QLabel(f"0/{MOVE_NAME_LENGTH}")
        counter.setStyleSheet("color: #888888; font-size: 10px; font-family: 'Courier New';")

        def _update_counter(text):
            n = len(text)
            counter.setText(f"{n}/{MOVE_NAME_LENGTH}")
            counter.setStyleSheet(
                "color: #cc3333; font-size: 10px; font-family: 'Courier New';"
                if n >= MOVE_NAME_LENGTH else
                "color: #888888; font-size: 10px; font-family: 'Courier New';"
            )

        self.display_edit.textChanged.connect(_update_counter)
        self.display_edit.textChanged.connect(self._on_display_changed)

        disp_row = QHBoxLayout()
        disp_row.setSpacing(6)
        disp_row.addWidget(self.display_edit)
        disp_row.addWidget(counter)
        form.addRow(QLabel("Display name:"), disp_row)

        # Constant
        mono = QFont("Courier New", 10)
        mono.setBold(True)

        const_row = QHBoxLayout()
        const_row.setSpacing(2)
        prefix_lbl = QLabel("MOVE_")
        prefix_lbl.setFont(mono)
        prefix_lbl.setStyleSheet("color: #888888;")
        const_row.addWidget(prefix_lbl)

        self.suffix_edit = QLineEdit()
        self.suffix_edit.setFont(mono)
        self.suffix_edit.setPlaceholderText("SHADOW_RUSH")
        self.suffix_edit.setToolTip("Auto-derived from display name. Edit to override.")
        self.suffix_edit.textChanged.connect(self._enforce_upper)
        self.suffix_edit.textEdited.connect(lambda _: setattr(self, '_auto_sync', False))
        const_row.addWidget(self.suffix_edit)
        form.addRow(QLabel("Constant:"), const_row)

        # Validation label
        self._validation = QLabel()
        self._validation.setStyleSheet("color: #cc3333; font-size: 11px;")
        self._validation.setWordWrap(True)
        form.addRow(QLabel(), self._validation)

        layout.addLayout(form)

        # Buttons
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.accepted.connect(self._validate_and_accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

    def _on_display_changed(self, text: str) -> None:
        if not self._auto_sync:
            return
        suffix = text.strip().upper()
        suffix = re.sub(r"[^A-Z0-9]+", "_", suffix)
        suffix = re.sub(r"_+", "_", suffix).strip("_")
        self.suffix_edit.blockSignals(True)
        self.suffix_edit.setText(suffix)
        self.suffix_edit.blockSignals(False)

    def _enforce_upper(self, text: str) -> None:
        upper = text.upper().replace(" ", "")
        if upper != text:
            cur = self.suffix_edit.cursorPosition()
            self.suffix_edit.blockSignals(True)
            self.suffix_edit.setText(upper)
            self.suffix_edit.setCursorPosition(cur)
            self.suffix_edit.blockSignals(False)

    def _validate_and_accept(self) -> None:
        display = self.display_edit.text().strip()
        suffix = self.suffix_edit.text().strip()
        const = f"MOVE_{suffix}" if suffix else ""

        if not display:
            self._validation.setText("Display name is required.")
            return
        if not suffix:
            self._validation.setText("Constant suffix is required.")
            return
        if const in self._existing:
            self._validation.setText(f"{const} already exists. Choose a different name.")
            return
        self._validation.clear()
        self.accept()

    def get_values(self) -> tuple[str, str]:
        """Return (constant, display_name)."""
        suffix = self.suffix_edit.text().strip().upper()
        return f"MOVE_{suffix}", self.display_edit.text().strip()


# ── MoveDetailPanel ───────────────────────────────────────────────────────────

class MoveDetailPanel(QWidget):
    """Right-hand scrollable detail panel for a single move."""

    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = False
        self._current_move: str | None = None
        self._build_ui()
        # Prevent scroll-wheel from changing combos/spins unless clicked
        try:
            from ui.custom_widgets.scroll_guard import install_scroll_guard_recursive
            install_scroll_guard_recursive(self)
        except Exception:
            pass

    # ── build ─────────────────────────────────────────────────────────────────

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(_SCROLL_SS)
        outer.addWidget(scroll)

        container = QWidget()
        container.setStyleSheet(_FIELD_SS)
        scroll.setWidget(container)

        root = QVBoxLayout(container)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # ── Identity card ──────────────────────────────────────────────────
        id_card = QGroupBox("Identity")
        id_card.setStyleSheet(_CARD_SS)
        id_layout = QVBoxLayout(id_card)
        id_layout.setContentsMargins(10, 18, 10, 10)
        id_layout.setSpacing(4)

        # Display name — big and prominent, like the species page
        self.f_display_name = QLabel("—")
        self.f_display_name.setStyleSheet(
            "color: #e0e0e0; font-size: 16px; font-weight: bold; padding: 2px 0;"
        )
        id_layout.addWidget(self.f_display_name)

        # Constant + Rename button row — smaller, underneath
        const_row = QHBoxLayout()
        const_row.setSpacing(6)
        self.f_constant = QLabel("MOVE_…")
        self.f_constant.setStyleSheet(
            "color: #888888; font-size: 10px; font-family: 'Courier New';"
        )
        self.f_constant.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        const_row.addWidget(self.f_constant)

        self.f_id = QLabel("—")
        self.f_id.setStyleSheet("color: #666666; font-size: 10px;")
        self.f_id.setToolTip("Numeric move ID")
        const_row.addWidget(self.f_id)

        const_row.addStretch(1)

        self.btn_rename = QPushButton("Rename…")
        self.btn_rename.setFixedWidth(72)
        self.btn_rename.setFixedHeight(24)
        self.btn_rename.setEnabled(False)
        self.btn_rename.setToolTip(
            "Change the display name and/or constant.\n"
            "You can set any capitalization (e.g. TACKLE → Tackle)."
        )
        self.btn_rename.setStyleSheet(_BTN_SS)
        const_row.addWidget(self.btn_rename)
        id_layout.addLayout(const_row)

        id_layout.addWidget(_hint(
            "Click Rename to change the name or constant. "
            "You can use any capitalization you want."
        ))

        root.addWidget(id_card)

        # ── Stats card ─────────────────────────────────────────────────────
        stats_card, stats_form = _card("Stats")

        self.f_power = QSpinBox()
        self.f_power.setRange(0, 255)
        self.f_power.setToolTip("Base power (0 = status move)")
        stats_form.addRow(_lbl("Power"), self.f_power)
        stats_form.addRow(QLabel(), _hint("How much damage the move deals. 0 means it's a status move."))

        self.f_accuracy = QSpinBox()
        self.f_accuracy.setRange(0, 100)
        self.f_accuracy.setToolTip("Accuracy % (0 = always hits / never misses)")
        stats_form.addRow(_lbl("Accuracy %"), self.f_accuracy)
        stats_form.addRow(QLabel(), _hint("Hit chance out of 100. 0 means it never misses."))

        self.f_pp = QSpinBox()
        self.f_pp.setRange(0, 40)
        self.f_pp.setToolTip("Base PP — max PP with PP Up × 3 is shown alongside")
        stats_form.addRow(_lbl("PP"), self.f_pp)

        # PP max hint label (auto-updates)
        self._pp_max_lbl = QLabel()
        self._pp_max_lbl.setStyleSheet("color: #666666; font-size: 10px; padding-left: 2px;")
        stats_form.addRow(QLabel(), self._pp_max_lbl)
        stats_form.addRow(QLabel(), _hint("How many times the move can be used before needing a rest stop."))

        self.f_priority = QSpinBox()
        self.f_priority.setRange(-7, 7)
        self.f_priority.setToolTip(
            "Priority bracket — higher value moves first.\n"
            " 0 = Normal\n"
            "+1 = Quick Attack / Mach Punch tier\n"
            "+3 = Protect / Detect tier\n"
            "+4 = Fake Out / Endure / Helping Hand tier\n"
            "-1 = Vital Throw tier\n"
            "-6 = Counter / Mirror Coat tier\n"
            "-7 = Trick Room (Gen 4+)"
        )
        stats_form.addRow(_lbl("Priority"), self.f_priority)
        stats_form.addRow(QLabel(), _hint("Who goes first. 0 is normal. Positive = faster (like Quick Attack). Negative = slower."))

        self.f_secondary = QSpinBox()
        self.f_secondary.setRange(0, 100)
        self.f_secondary.setToolTip(
            "Chance (%) for the move's secondary effect to trigger.\n"
            "0 = never, 100 = always.\n"
            "Only relevant for moves whose EFFECT_ has a secondary."
        )
        stats_form.addRow(_lbl("Effect chance %"), self.f_secondary)
        stats_form.addRow(QLabel(), _hint("Chance the bonus effect triggers (burn, flinch, etc). 0 = no bonus. 100 = always."))

        root.addWidget(stats_card)

        # ── Classification card ────────────────────────────────────────────
        cls_card, cls_form = _card("Classification")

        # Type — pure dropdown, no free text
        self.f_type = QComboBox()
        self.f_type.setEditable(False)
        for t in TYPE_CHOICES:
            self.f_type.addItem(t)
        cls_form.addRow(_lbl("Type"), self.f_type)
        cls_form.addRow(QLabel(), _hint("The elemental type (Fire, Water, etc). Also determines Physical vs Special in Gen 3."))

        # Gen 3 category (derived from type + power, read-only informational)
        self.f_category = QLabel("—")
        self.f_category.setStyleSheet(_CATEGORY_SS["—"])
        self.f_category.setToolTip(
            "Gen 3 damage category — determined entirely by move TYPE\n"
            "(Physical types: Normal Fighting Flying Poison Ground Rock Bug Ghost Steel)\n"
            "(Special types: Fire Water Grass Electric Psychic Ice Dragon Dark)\n"
            "Status = power is 0"
        )
        cls_form.addRow(_lbl("Category"), self.f_category)
        cls_form.addRow(QLabel(), _hint("Auto-calculated from Type. In Gen 3, all Fire/Water/etc moves are Special; Fighting/Normal/etc are Physical."))

        # Effect — proper dropdown (battle behavior, NOT animation)
        self.f_effect = QComboBox()
        self.f_effect.setEditable(False)
        self.f_effect.setMaxVisibleItems(20)
        self.f_effect.setToolTip(
            "Battle effect constant (EFFECT_…)\n"
            "Controls what the move DOES mechanically (damage, status, stat changes).\n"
            "This is NOT the animation — see the Animation field below."
        )
        for e in EFFECT_CHOICES:
            self.f_effect.addItem(e)
        cls_form.addRow(_lbl("Effect"), self.f_effect)
        cls_form.addRow(QLabel(), _hint(
            "What the move DOES in battle — damage, inflict status, change stats, etc. "
            "This is NOT the visual animation. Two moves can share the same effect but look totally different."
        ))

        # Target — pure dropdown
        self.f_target = QComboBox()
        self.f_target.setEditable(False)
        for display, val in TARGET_CHOICES:
            self.f_target.addItem(display, val)
        self.f_target.setToolTip("Which Pokémon the move targets in battle")
        cls_form.addRow(_lbl("Target"), self.f_target)
        cls_form.addRow(QLabel(), _hint("Who the move hits — the opponent, yourself, both sides, etc."))

        # Animation — dropdown of existing animation labels
        self.f_animation = QComboBox()
        self.f_animation.setEditable(False)
        self.f_animation.setMaxVisibleItems(20)
        self.f_animation.setToolTip(
            "Battle animation script (from data/battle_anim_scripts.s)\n"
            "Controls what plays VISUALLY when the move is used.\n"
            "Independent from Effect — you can mix and match freely.\n"
            "New moves reuse an existing animation by default."
        )
        cls_form.addRow(_lbl("Animation"), self.f_animation)
        cls_form.addRow(QLabel(), _hint(
            "What the move LOOKS LIKE on screen — the visual effect that plays. "
            "Completely separate from Effect. You can put any animation on any move."
        ))

        root.addWidget(cls_card)

        # ── Flags card ─────────────────────────────────────────────────────
        flags_card, flags_form = _card("Flags")
        flags_form.addRow(_hint("Check the boxes that apply to this move. Each flag changes how the move behaves."))

        # Plain-English summaries for each flag
        _FLAG_HINTS: dict[str, str] = {
            "FLAG_MAKES_CONTACT": "The user physically touches the target. Triggers abilities like Static and Rough Skin.",
            "FLAG_PROTECT_AFFECTED": "This move is blocked if the target uses Protect or Detect.",
            "FLAG_MAGIC_COAT_AFFECTED": "If the target used Magic Coat, this move bounces back at you.",
            "FLAG_SNATCH_AFFECTED": "If an opponent used Snatch, they steal this move's effect for themselves.",
            "FLAG_MIRROR_MOVE_AFFECTED": "This move can be copied by a Pokemon that uses Mirror Move.",
            "FLAG_KINGS_ROCK_AFFECTED": "If the user holds a King's Rock, this move has a chance to make the target flinch.",
        }

        self._flag_checks: dict[str, QCheckBox] = {}
        for flag, tip in ALL_FLAGS:
            label = flag.replace("FLAG_", "").replace("_", " ").title()
            cb = QCheckBox(label)
            cb.setToolTip(f"{flag}\n{tip}")
            self._flag_checks[flag] = cb
            flags_form.addRow(cb)
            hint_text = _FLAG_HINTS.get(flag, tip)
            flags_form.addRow(QLabel(), _hint(hint_text))

        root.addWidget(flags_card)

        # ── Description card ───────────────────────────────────────────────
        desc_card, desc_form = _card("Description")
        desc_form.addRow(_hint("The text players see when they check the move's summary in-game."))

        self.f_description = DexDescriptionEdit(max_chars_per_line=21, max_lines=4)
        self.f_description.setMinimumHeight(80)
        self.f_description.setMaximumHeight(120)
        self.f_description.setFont(QFont("Courier New", 10))
        self.f_description.setPlaceholderText("In-game move description…")
        self._desc_counter = QLabel()
        self._desc_counter.setTextFormat(Qt.TextFormat.RichText)
        self.f_description.set_counter_label(self._desc_counter)
        desc_form.addRow(self.f_description)
        desc_form.addRow(self._desc_counter)

        root.addWidget(desc_card)
        root.addStretch(1)

        # ── wire change signals ────────────────────────────────────────────
        for w in (self.f_power, self.f_accuracy, self.f_pp,
                  self.f_priority, self.f_secondary):
            w.valueChanged.connect(self._emit)
        self.f_pp.valueChanged.connect(self._update_pp_max)
        self.f_type.currentIndexChanged.connect(self._emit)
        self.f_type.currentIndexChanged.connect(self._update_category)
        self.f_power.valueChanged.connect(self._update_category)
        self.f_effect.currentIndexChanged.connect(self._emit)
        self.f_target.currentIndexChanged.connect(self._emit)
        self.f_animation.currentIndexChanged.connect(self._emit)
        self.f_description.textChanged.connect(self._emit)
        for cb in self._flag_checks.values():
            cb.stateChanged.connect(self._emit)

    def _emit(self, *_):
        if not self._loading:
            self.changed.emit()

    def _update_pp_max(self, val: int):
        max_pp = int(val * 8 / 5)  # PP × 1.6 rounded down
        self._pp_max_lbl.setText(f"Max with PP Up ×3: {max_pp}")

    def _update_category(self, *_):
        move_type = self.f_type.currentText()
        power = self.f_power.value()
        cat = _gen3_category(move_type, power)
        self.f_category.setText(cat)
        self.f_category.setStyleSheet(_CATEGORY_SS.get(cat, _CATEGORY_SS["—"]))

    # ── public API ─────────────────────────────────────────────────────────────

    def populate_effects(self, extra_effects: list[str]) -> None:
        """
        Ensure the Effect combo has all known effects.
        Starts from the hardcoded EFFECT_CHOICES and merges any extras from data.
        """
        current = self.f_effect.currentText()
        all_effects = sorted(set(EFFECT_CHOICES) | {e for e in extra_effects if e})
        self.f_effect.blockSignals(True)
        self.f_effect.clear()
        for e in all_effects:
            self.f_effect.addItem(e)
        self.f_effect.blockSignals(False)
        # Restore selection
        if current:
            idx = self.f_effect.findText(current)
            if idx >= 0:
                self.f_effect.setCurrentIndex(idx)

    def populate_animations(self, anim_labels: list[str]) -> None:
        """Populate the Animation dropdown with labels from battle_anim_scripts.s."""
        current = self.f_animation.currentText()
        self.f_animation.blockSignals(True)
        self.f_animation.clear()
        for label in anim_labels:
            self.f_animation.addItem(label)
        self.f_animation.blockSignals(False)
        if current:
            idx = self.f_animation.findText(current)
            if idx >= 0:
                self.f_animation.setCurrentIndex(idx)

    def load(self, move: str, data: dict, description: str = "") -> None:
        """Populate all fields from move constant + data dict."""
        self._loading = True
        self._current_move = move
        try:
            # Display name — big at the top
            display_name = (data.get("name") or "").strip()
            if not display_name:
                base = move[len("MOVE_"):] if move.startswith("MOVE_") else move
                display_name = base.replace("_", " ").title()
            self.f_display_name.setText(display_name)

            self.f_constant.setText(move)
            move_id = data.get("id", "")
            self.f_id.setText(f"(#{move_id})" if move_id else "—")

            self.f_power.setValue(int(data.get("power", 0) or 0))
            self.f_accuracy.setValue(int(data.get("accuracy", 0) or 0))
            pp_val = int(data.get("pp", 0) or 0)
            self.f_pp.setValue(pp_val)
            self._update_pp_max(pp_val)
            self.f_priority.setValue(int(data.get("priority", 0) or 0))
            self.f_secondary.setValue(int(data.get("secondaryEffectChance", 0) or 0))

            # Type
            t = str(data.get("type", "TYPE_NORMAL"))
            idx = self.f_type.findText(t)
            self.f_type.setCurrentIndex(idx if idx >= 0 else 0)

            # Effect
            effect = str(data.get("effect", ""))
            idx = self.f_effect.findText(effect)
            if idx >= 0:
                self.f_effect.setCurrentIndex(idx)
            elif effect:
                # Unknown effect from data — add it so it can be selected
                self.f_effect.addItem(effect)
                self.f_effect.setCurrentIndex(self.f_effect.count() - 1)

            # Target
            target = str(data.get("target", "MOVE_TARGET_SELECTED"))
            for i, (_, val) in enumerate(TARGET_CHOICES):
                if val == target:
                    self.f_target.setCurrentIndex(i)
                    break

            # Animation
            anim = str(data.get("animation", ""))
            if anim:
                idx = self.f_animation.findText(anim)
                if idx >= 0:
                    self.f_animation.setCurrentIndex(idx)
                else:
                    self.f_animation.addItem(anim)
                    self.f_animation.setCurrentIndex(self.f_animation.count() - 1)
            elif self.f_animation.count() > 0:
                self.f_animation.setCurrentIndex(0)

            # Flags
            flags_str = str(data.get("flags", ""))
            active = {f.strip() for f in flags_str.split("|") if f.strip()}
            for flag, cb in self._flag_checks.items():
                cb.setChecked(flag in active)

            # Description — stored with literal \n, show as real newlines
            self.f_description.setPlainText(description.replace("\\n", "\n") if description else "")

            # Update derived displays
            self._update_category()
            self.btn_rename.setEnabled(True)

        finally:
            self._loading = False

    def collect(self) -> tuple[str, dict, str]:
        """Return (move_constant, data_dict, description_string)."""
        active_flags = [f for f, cb in self._flag_checks.items() if cb.isChecked()]
        flags_str = " | ".join(active_flags) if active_flags else "0"

        data = {
            "power":                 self.f_power.value(),
            "accuracy":              self.f_accuracy.value(),
            "pp":                    self.f_pp.value(),
            "priority":              self.f_priority.value(),
            "secondaryEffectChance": self.f_secondary.value(),
            "type":                  self.f_type.currentText(),
            "effect":                self.f_effect.currentText(),
            "target":                self.f_target.currentData() or self.f_target.currentText(),
            "flags":                 flags_str,
            "animation":             self.f_animation.currentText(),
        }
        # Convert real newlines → literal \n for storage
        desc = self.f_description.toPlainText().replace("\n", "\\n")
        return self._current_move or "", data, desc

    def clear(self):
        self._loading = True
        self._current_move = None
        try:
            self.f_display_name.setText("—")
            self.f_constant.setText("MOVE_…")
            self.f_id.setText("—")
            self.f_power.setValue(0)
            self.f_accuracy.setValue(0)
            self.f_pp.setValue(0)
            self._update_pp_max(0)
            self.f_priority.setValue(0)
            self.f_secondary.setValue(0)
            self.f_type.setCurrentIndex(0)
            self.f_effect.setCurrentIndex(0)
            self.f_target.setCurrentIndex(0)
            if self.f_animation.count() > 0:
                self.f_animation.setCurrentIndex(0)
            for cb in self._flag_checks.values():
                cb.setChecked(False)
            self.f_description.clear()
            self.f_category.setText("—")
            self.f_category.setStyleSheet(_CATEGORY_SS["—"])
            self.btn_rename.setEnabled(False)
        finally:
            self._loading = False


# ── MovesTabWidget ─────────────────────────────────────────────────────────────

class MovesTabWidget(QWidget):
    """
    Full moves tab: searchable / filterable list on the left, detail panel on the right.
    Signals `data_changed` whenever the user edits a field and navigates away
    (or the caller explicitly calls `save_current()`).
    """

    data_changed     = pyqtSignal()
    rename_requested = pyqtSignal(str)   # emits the current move constant

    def __init__(self, parent=None):
        super().__init__(parent)
        self._moves_data: dict = {}          # const → {id, effect, power, …}
        self._move_descriptions: dict = {}   # const → description string
        self._new_moves: set = set()         # constants added this session
        self._current_move: str | None = None
        self._dirty = False
        self._build_ui()

    # ── build ─────────────────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter)

        # ── Left: list panel ───────────────────────────────────────────────
        left = QWidget()
        left_v = QVBoxLayout(left)
        left_v.setContentsMargins(0, 0, 0, 0)
        left_v.setSpacing(0)

        # Type filter combo
        self._type_filter = QComboBox()
        self._type_filter.setStyleSheet(_FILTER_COMBO_SS)
        self._type_filter.addItem("All Types", "")
        for t in TYPE_CHOICES:
            short = t.replace("TYPE_", "").title()
            self._type_filter.addItem(short, t)
        self._type_filter.currentIndexChanged.connect(lambda _: self._filter_list(self._search.text()))
        left_v.addWidget(self._type_filter)

        # Text search
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search moves…")
        self._search.setClearButtonEnabled(True)
        self._search.setStyleSheet(_SEARCH_SS)
        self._search.textChanged.connect(self._filter_list)
        left_v.addWidget(self._search)

        # ── Add / Duplicate buttons (above the list so they're always visible)
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(4, 3, 4, 3)
        btn_row.setSpacing(4)

        self.btn_add = QPushButton("+ Add")
        self.btn_add.setStyleSheet(_BTN_SS)
        self.btn_add.setToolTip("Create a new move from scratch")
        self.btn_add.clicked.connect(self._on_add_move)
        btn_row.addWidget(self.btn_add)

        self.btn_duplicate = QPushButton("Duplicate")
        self.btn_duplicate.setStyleSheet(_BTN_SS)
        self.btn_duplicate.setToolTip("Copy the selected move's stats, flags, description, and animation into a new move")
        self.btn_duplicate.clicked.connect(self._on_duplicate_move)
        btn_row.addWidget(self.btn_duplicate)

        # Count label (inline with buttons)
        self._count_lbl = QLabel()
        self._count_lbl.setStyleSheet(
            "color: #555555; font-size: 10px; padding: 0 4px;"
        )
        self._count_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        btn_row.addWidget(self._count_lbl)

        left_v.addLayout(btn_row)

        self._list = QListWidget()
        self._list.setStyleSheet(_LIST_SS)
        self._list.setMinimumWidth(240)
        self._list.currentRowChanged.connect(self._on_row_changed)
        left_v.addWidget(self._list)

        splitter.addWidget(left)

        # ── Right: detail panel ────────────────────────────────────────────
        self._detail = MoveDetailPanel()
        self._detail.changed.connect(self._on_detail_changed)
        self._detail.btn_rename.clicked.connect(self._on_rename_clicked)
        splitter.addWidget(self._detail)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

    # ── public API ─────────────────────────────────────────────────────────────

    def load_moves(self, moves: dict, descriptions: dict) -> None:
        """
        Populate the widget.
        `moves`        — {MOVE_CONSTANT: {id, effect, power, …}}
        `descriptions` — {MOVE_CONSTANT: description_string}
        """
        self._moves_data = moves
        self._move_descriptions = descriptions

        # Collect extra effect constants from data and merge with hardcoded list
        extra_effects = list({v.get("effect", "") for v in moves.values() if v.get("effect")})
        self._detail.populate_effects(extra_effects)

        self._rebuild_list()

        if self._list.count():
            self._list.setCurrentRow(0)

    def populate_animations(self, anim_labels: list[str]) -> None:
        """Pass animation labels through to the detail panel."""
        self._detail.populate_animations(anim_labels)

    def save_current(self) -> None:
        """Flush the current detail panel edits back to internal dicts."""
        if not self._dirty or self._current_move is None:
            return
        move, data, desc = self._detail.collect()
        if move and move in self._moves_data:
            existing = dict(self._moves_data[move])
            existing.update(data)
            self._moves_data[move] = existing
            self._move_descriptions[move] = desc
        self._dirty = False

    def get_moves_data(self) -> dict:
        return self._moves_data

    def get_descriptions(self) -> dict:
        return self._move_descriptions

    def get_new_moves(self) -> set:
        """Return the set of move constants that were added this session."""
        return set(self._new_moves)

    def clear_new_moves(self) -> None:
        """Clear the new-moves tracker (call after successful save)."""
        self._new_moves.clear()

    # ── add / duplicate ──────────────────────────────────────────────────────

    def _next_move_id(self) -> int:
        """Return the next available move ID."""
        if not self._moves_data:
            return 1
        max_id = max(
            (int(v.get("id", 0) or 0) for v in self._moves_data.values()),
            default=0,
        )
        return max_id + 1

    def _on_add_move(self) -> None:
        existing = set(self._moves_data.keys())
        dlg = NewMoveDialog(self, existing_constants=existing)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        const, display = dlg.get_values()
        new_id = self._next_move_id()

        # Default animation: reuse Move_POUND (basic hit) if available
        default_anim = "Move_POUND"
        if self.f_animation_count() == 0:
            default_anim = ""

        self._moves_data[const] = {
            "id":                    new_id,
            "name":                  display,
            "power":                 0,
            "accuracy":              0,
            "pp":                    5,
            "priority":              0,
            "secondaryEffectChance": 0,
            "type":                  "TYPE_NORMAL",
            "effect":                "EFFECT_HIT",
            "target":                "MOVE_TARGET_SELECTED",
            "flags":                 "0",
            "animation":             default_anim,
        }
        self._move_descriptions[const] = ""
        self._new_moves.add(const)

        self._rebuild_list()
        self._select_move(const)
        self._dirty = False
        self.data_changed.emit()

    def _on_duplicate_move(self) -> None:
        if not self._current_move or self._current_move not in self._moves_data:
            return

        # Save current edits first
        self.save_current()

        source_const = self._current_move
        source_data = dict(self._moves_data[source_const])
        source_desc = self._move_descriptions.get(source_const, "")

        existing = set(self._moves_data.keys())
        dlg = NewMoveDialog(self, existing_constants=existing)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        const, display = dlg.get_values()
        new_id = self._next_move_id()

        # Copy all data from source, override identity fields
        new_data = dict(source_data)
        new_data["id"] = new_id
        new_data["name"] = display

        self._moves_data[const] = new_data
        self._move_descriptions[const] = source_desc
        self._new_moves.add(const)

        self._rebuild_list()
        self._select_move(const)
        self._dirty = False
        self.data_changed.emit()

    def f_animation_count(self) -> int:
        """Return the number of animation labels loaded."""
        return self._detail.f_animation.count()

    # ── internal ──────────────────────────────────────────────────────────────

    def _select_move(self, const: str) -> None:
        """Select a move in the list by constant."""
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) == const:
                self._list.setCurrentRow(i)
                return

    def _rebuild_list(self) -> None:
        """Build/rebuild the list widget items, sorted by move ID."""
        ordered = sorted(
            self._moves_data.keys(),
            key=lambda k: self._moves_data[k].get("id", 0)
        )

        self._list.blockSignals(True)
        self._list.clear()
        for mv in ordered:
            if mv == "MOVE_NONE":
                continue
            data = self._moves_data.get(mv, {})
            move_type = str(data.get("type", ""))
            power = int(data.get("power", 0) or 0)
            pp = int(data.get("pp", 0) or 0)
            effect = str(data.get("effect", ""))

            display_name = (data.get("name") or "").strip()
            if not display_name:
                display_name = mv.replace("MOVE_", "").replace("_", " ").title()

            item = QListWidgetItem(display_name)
            item.setData(Qt.ItemDataRole.UserRole, mv)
            # Store type for type-filter
            item.setData(Qt.ItemDataRole.UserRole + 1, move_type)

            # Tooltip with quick stats
            short_type = move_type.replace("TYPE_", "")
            pwr_str = str(power) if power else "—"
            cat = _gen3_category(move_type, power)
            tip = (
                f"<b>{display_name}</b><br>"
                f"Type: {short_type}  |  Category: {cat}<br>"
                f"Power: {pwr_str}  |  PP: {pp}<br>"
                f"Effect: {effect}"
            )
            item.setToolTip(tip)
            self._list.addItem(item)
        self._list.blockSignals(False)

        self._filter_list(self._search.text())

    def _filter_list(self, text: str = "") -> None:
        text = (text or "").strip().lower()
        type_filter = self._type_filter.currentData() or ""

        visible = 0
        for i in range(self._list.count()):
            item = self._list.item(i)
            mv = item.data(Qt.ItemDataRole.UserRole) or item.text()
            item_type = item.data(Qt.ItemDataRole.UserRole + 1) or ""

            # Type filter
            if type_filter and item_type != type_filter:
                item.setHidden(True)
                continue

            # Text search — match constant, effect, or type
            if text:
                info = self._moves_data.get(mv, {})
                match = (
                    text in mv.lower()
                    or text in item.text().lower()
                    or text in str(info.get("effect", "")).lower()
                    or text in str(info.get("type", "")).lower()
                )
            else:
                match = True

            item.setHidden(not match)
            if match:
                visible += 1

        total = self._list.count()
        self._count_lbl.setText(f"{visible} / {total}")

    def _on_row_changed(self, row: int) -> None:
        # Save previous before switching
        self.save_current()

        item = self._list.item(row)
        if item is None:
            self._detail.clear()
            self._current_move = None
            return

        mv = item.data(Qt.ItemDataRole.UserRole) or item.text()
        self._current_move = mv
        self._dirty = False
        data = self._moves_data.get(mv, {})
        desc = self._move_descriptions.get(mv, "")
        self._detail.load(mv, data, desc)

    def _on_rename_clicked(self) -> None:
        if self._current_move:
            self.rename_requested.emit(self._current_move)

    def rename_move_key(self, old_const: str, new_const: str) -> None:
        """Update the in-memory data dicts when a move is renamed."""
        if old_const in self._moves_data:
            self._moves_data[new_const] = self._moves_data.pop(old_const)
        if old_const in self._move_descriptions:
            self._move_descriptions[new_const] = self._move_descriptions.pop(old_const)
        if old_const in self._new_moves:
            self._new_moves.discard(old_const)
            self._new_moves.add(new_const)
        if self._current_move == old_const:
            self._current_move = new_const
        self._rebuild_list()
        # Re-select the renamed move
        self._select_move(new_const)

    def _on_detail_changed(self) -> None:
        self._dirty = True
        self.data_changed.emit()
