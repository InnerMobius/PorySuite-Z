"""
Shared rename dialog used for species, moves, items, and trainers.

Layout (species)
----------------
  Renaming:     SPECIES_BULBASAUR          (read-only label)
  Display name: [Bulbasaur__________] 5/10 (PRIMARY input -- drives constant)
  New constant: SPECIES_ [BULBASAUR______] (auto-derived, editable override)
  Preview:      src/data/... -> ...        (read-only text area)

The constant suffix auto-derives from the display name as you type.
If you manually edit the constant field it stops auto-syncing (override mode).

Derivation rules
----------------
  ♀  ->  _F       ♂  ->  _M
  spaces  ->  removed (Bulba Saur  -> BULBASAUR)
  dashes  ->  removed (Ho-Oh       -> HOOH)
  apostrophes / periods  ->  removed (Farfetch'd -> FARFETCHD)
  everything else non-alphanumeric  ->  removed
  result uppercased

Constants have no practical length limit in GCC/C99 (63+ chars guaranteed).
Display names are capped at POKEMON_NAME_LENGTH = 10.
"""
import re

from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLabel,
    QLineEdit,
    QDialogButtonBox,
    QPlainTextEdit,
)
from PyQt6.QtGui import QFont, QPalette
from PyQt6.QtCore import Qt


# ── Conversion helper ─────────────────────────────────────────────────────────

_GENDER_MAP = [
    ("\u2640", "_F"),   # ♀  ->  _F
    ("\u2642", "_M"),   # ♂  ->  _M
]


def _display_to_suffix(text: str) -> str:
    """Derive an ALL_CAPS_UNDERSCORE constant suffix from a display name.

    Rules (applied in order):
      ♀ → _F, ♂ → _M
      apostrophes and periods → removed (Farfetch'd → FARFETCHD)
      spaces → removed (Bulba Saur → BULBASAUR, not BULBA_SAUR)
      dashes → underscore (Ho-Oh → HO_OH)
      any remaining non-alphanumeric-non-underscore char → removed
      uppercase, collapse __ runs, strip leading/trailing underscores
    """
    for sym, repl in _GENDER_MAP:
        text = text.replace(sym, repl)
    text = text.replace("'", "").replace(".", "")
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"\-+", "_", text)
    text = re.sub(r"[^A-Za-z0-9_]", "", text)
    text = re.sub(r"_+", "_", text.upper()).strip("_")
    return text


# ── Dialog ────────────────────────────────────────────────────────────────────

class RenameDialog(QDialog):
    """
    Rename dialog for any constant that has a fixed prefix.

    Parameters
    ----------
    parent      : QWidget parent
    prefix      : The locked prefix string, e.g. "SPECIES_", "MOVE_", "ITEM_"
    entity_type : Human-readable type name shown in the title, e.g. "Species"
    show_display: Whether to show the display name field (species only).
                  When True the display name drives the constant automatically.
    """

    def __init__(
        self,
        parent=None,
        prefix="SPECIES_",
        entity_type="Species",
        show_display=True,
    ):
        super().__init__(parent)
        self._prefix = prefix
        self._show_display = show_display
        self._auto_sync = True   # False once user manually edits suffix_edit
        self.setWindowTitle("Rename " + entity_type)
        self.setMinimumWidth(540)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # ── Row 1: read-only old constant ─────────────────────────────────────
        self._old_label = QLabel()
        self._old_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        bold = QFont()
        bold.setBold(True)
        self._old_label.setFont(bold)
        old_row_label = QLabel("Renaming:")
        old_row_label.setToolTip(
            "The constant that will be renamed.\n"
            "Select a different entry from the list to change which one."
        )
        form.addRow(old_row_label, self._old_label)

        mono = QFont("Courier New", 10)
        mono.setBold(True)

        # ── Row 2: display name — PRIMARY input ──────────────────────────────
        if show_display:
            # Character limits per entity type
            _NAME_LIMITS = {"Species": 10, "Move": 12, "Item": 20, "Ability": 12}
            _DISP_MAX = _NAME_LIMITS.get(entity_type, 10)
            self.display_edit = QLineEdit()
            self.display_edit.setMaxLength(_DISP_MAX)
            self.display_edit.setPlaceholderText(
                "e.g. Tackle" if entity_type == "Move" else "e.g. Bulbasaur"
            )
            display_label = QLabel("Display name:")
            display_label.setToolTip(
                f"The human-readable name shown in-game and in the editor.\n"
                f"Maximum {_DISP_MAX} characters.\n"
                "As you type, the constant below is auto-derived:\n"
                "  spaces → removed  (Bulba Saur → BULBASAUR)\n"
                "  dashes → underscore  (Ho-Oh → HO_OH)\n"
                "  ♀ → _F,  ♂ → _M\n"
                "  apostrophes and periods removed\n"
                "You can still edit the constant manually if needed."
            )
            disp_counter = QLabel("0/{0}".format(_DISP_MAX))
            disp_counter.setStyleSheet(
                "color: #888888; font-size: 10px; font-family: 'Courier New';"
            )
            disp_counter.setToolTip(f"Characters used / max ({_DISP_MAX})")

            def _update_disp(text, _lbl=disp_counter, _max=_DISP_MAX):
                used = len(text)
                _lbl.setText("{0}/{1}".format(used, _max))
                _lbl.setStyleSheet(
                    "color: #cc3333; font-size: 10px; font-family: 'Courier New';"
                    if used >= _max
                    else "color: #888888; font-size: 10px; font-family: 'Courier New';"
                )

            self.display_edit.textChanged.connect(_update_disp)
            self.display_edit.textChanged.connect(self._on_display_changed)

            disp_row = QHBoxLayout()
            disp_row.setContentsMargins(0, 0, 0, 0)
            disp_row.setSpacing(6)
            disp_row.addWidget(self.display_edit)
            disp_row.addWidget(disp_counter)
            form.addRow(display_label, disp_row)
        else:
            self.display_edit = None

        # ── Row 3: new constant — locked prefix + editable suffix ─────────────
        const_row = QHBoxLayout()
        const_row.setSpacing(2)

        self._prefix_label = QLabel(prefix)
        self._prefix_label.setToolTip(
            "This prefix is fixed — all constants of this type must keep it."
        )
        pal = self._prefix_label.palette()
        pal.setColor(QPalette.ColorRole.WindowText, pal.color(QPalette.ColorRole.Mid))
        self._prefix_label.setPalette(pal)
        self._prefix_label.setFont(mono)
        const_row.addWidget(self._prefix_label)

        self.suffix_edit = QLineEdit()
        self.suffix_edit.setFont(mono)
        self.suffix_edit.setPlaceholderText("NEW_NAME")
        self.suffix_edit.setToolTip(
            "Auto-derived from the display name above.\n"
            "You can edit this directly to override — it will stop auto-syncing.\n\n"
            "Rules: spaces removed, dashes → underscores, ♀ → _F, ♂ → _M,\n"
            "apostrophes/periods removed, result uppercased.\n\n"
            "C macro names have no practical length limit (C99 guarantees 63+ chars).\n"
            "Full constant: " + prefix + "<suffix>"
        )
        const_row.addWidget(self.suffix_edit)

        new_row_label = QLabel("New constant:")
        new_row_label.setToolTip(
            "The new C macro constant. The '" + prefix + "' prefix is fixed."
        )
        form.addRow(new_row_label, const_row)

        layout.addLayout(form)

        # ── Live preview ──────────────────────────────────────────────────────
        preview_label = QLabel(
            "Source file changes preview "
            "(first 20 hits — all matching tokens updated on Save):"
        )
        preview_label.setWordWrap(True)
        layout.addWidget(preview_label)

        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMaximumHeight(160)
        self.preview.setPlaceholderText(
            "Type a display name above to see which source files will be updated..."
        )
        mono2 = QFont("Courier New", 9)
        self.preview.setFont(mono2)
        layout.addWidget(self.preview)

        # ── OK / Cancel ───────────────────────────────────────────────────────
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # Auto-uppercase on every change (programmatic or user)
        self.suffix_edit.textChanged.connect(self._enforce_upper)
        # Detect manual user edits to disable auto-sync
        self.suffix_edit.textEdited.connect(self._on_suffix_user_edited)

    # ── Signal handlers ───────────────────────────────────────────────────────

    def _on_display_changed(self, text: str) -> None:
        """Auto-derive constant suffix from display name (when not overridden)."""
        if not self._auto_sync:
            return
        derived = _display_to_suffix(text)
        self.suffix_edit.blockSignals(True)
        self.suffix_edit.setText(derived)
        self.suffix_edit.blockSignals(False)

    def _on_suffix_user_edited(self, _text: str) -> None:
        """User manually typed in the constant field — disable auto-sync."""
        self._auto_sync = False

    def _enforce_upper(self, text: str) -> None:
        """Keep the suffix field all-caps with underscores (no spaces)."""
        upper = text.upper().replace(" ", "")
        if upper != text:
            cur = self.suffix_edit.cursorPosition()
            self.suffix_edit.blockSignals(True)
            self.suffix_edit.setText(upper)
            self.suffix_edit.setCursorPosition(cur)
            self.suffix_edit.blockSignals(False)

    # ── Public API ────────────────────────────────────────────────────────────

    def set_old_constant(self, constant: str) -> None:
        """Display the old constant (read-only) and pre-fill the suffix."""
        self._old_label.setText(constant)
        if constant.startswith(self._prefix):
            suffix = constant[len(self._prefix):]
        else:
            suffix = constant
        self.suffix_edit.blockSignals(True)
        self.suffix_edit.setText(suffix)
        self.suffix_edit.blockSignals(False)
        self.suffix_edit.selectAll()

    def set_display_name(self, name: str) -> None:
        if self.display_edit is not None:
            self.display_edit.setText(name)
            # Pre-fill constant from display name if it still matches the
            # default (i.e. user hasn't touched anything yet)
            self._auto_sync = True

    def set_preview(self, preview) -> None:
        """Display preview entries as plain text (list of 4-tuples)."""
        lines = [
            "{0}:{1}  {2}  ->  {3}".format(p[0], p[1], p[2], p[3])
            for p in preview[:20]
        ]
        if not lines:
            self.preview.setPlainText("(No references found in source files yet)")
        else:
            self.preview.setPlainText("\n".join(lines))

    def get_values(self):
        """
        Return (old_constant, new_constant, display_name).
        new_constant is assembled as  prefix + suffix.upper().
        """
        old = self._old_label.text().strip()
        suffix = self.suffix_edit.text().strip().upper()
        new = self._prefix + suffix if suffix else old
        display = self.display_edit.text().strip() if self.display_edit else ""
        return old, new, display

    # ── Backwards-compat shims ────────────────────────────────────────────────

    @property
    def old_edit(self):
        """Legacy shim — exposes old_label as a pseudo-QLineEdit."""
        return _LabelShim(self._old_label)

    @property
    def new_edit(self):
        """Legacy shim — exposes suffix_edit as the 'new' field."""
        return self.suffix_edit


class _LabelShim:
    """Thin wrapper so legacy callers can do dialog.old_edit.setText(x)."""

    def __init__(self, label):
        self._label = label

    def setText(self, text):
        self._label.setText(text)

    def text(self):
        return self._label.text()

    def setReadOnly(self, _):
        pass  # always read-only
