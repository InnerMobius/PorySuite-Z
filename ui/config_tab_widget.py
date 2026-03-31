"""
ui/config_tab_widget.py
Build & Debug Configuration Editor

Edits two files in the loaded project:
  - config.mk             (VARIABLE := VALUE format)
  - include/config.h      (#define NAME VALUE / //#define NAME)
"""
from __future__ import annotations

import os
import re

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QScrollArea, QSizePolicy,
    QVBoxLayout, QWidget,
)


# ── stylesheet helpers (match project dark theme) ─────────────────────────────

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
QComboBox {
    background-color: #1e1e1e;
    border: 1px solid #3a3a3a;
    border-radius: 4px;
    padding: 4px 7px;
    color: #e0e0e0;
    font-size: 12px;
}
QComboBox:focus { border: 1px solid #1976d2; }
QComboBox::drop-down { border: none; padding-right: 6px; }
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

_NOTE_SS = "color: #888888; font-size: 10px; font-style: italic;"


# ── helpers ───────────────────────────────────────────────────────────────────

def _read_file(path: str) -> str:
    """Return file contents or empty string on error."""
    try:
        with open(path, encoding="utf-8", errors="surrogateescape") as fh:
            return fh.read()
    except OSError:
        return ""


def _write_file(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8", errors="surrogateescape", newline="\n") as fh:
        fh.write(content)


def _parse_mk_value(text: str, var: str) -> str:
    """Return current VALUE of  VAR := VALUE  in a Makefile snippet."""
    m = re.search(r"^\s*" + re.escape(var) + r"\s*:=\s*(.+)$", text, re.MULTILINE)
    return m.group(1).strip() if m else ""


def _set_mk_value(text: str, var: str, value: str) -> str:
    """Replace VALUE in  VAR := OLD  → VAR := VALUE.  Adds line if missing."""
    pattern = r"(^\s*" + re.escape(var) + r"\s*:=\s*).+$"
    replacement = r"\g<1>" + value
    new_text, n = re.subn(pattern, replacement, text, flags=re.MULTILINE)
    if n == 0:
        new_text = text.rstrip("\n") + "\n" + var + " := " + value + "\n"
    return new_text


def _parse_define_value(text: str, name: str) -> str | None:
    """
    Return the value after #define NAME in config.h.
    Returns None if the define is commented out (//#define).
    """
    # Active define
    m = re.search(r"^#define\s+" + re.escape(name) + r"(?:\s+(\S+))?\s*$",
                  text, re.MULTILINE)
    if m:
        return m.group(1) or ""
    return None


def _is_define_active(text: str, name: str) -> bool:
    return _parse_define_value(text, name) is not None


def _set_define_active(text: str, name: str, active: bool) -> str:
    """Toggle the #define / //#define comment state."""
    active_pat = r"^(#define\s+" + re.escape(name) + r"(?:\s+\S+)?)\s*$"
    commented_pat = r"^(//\s*#define\s+" + re.escape(name) + r"(?:\s+\S+)?)\s*$"
    if active:
        new_text, n = re.subn(commented_pat, r"\1", text, flags=re.MULTILINE)
        if n == 0:
            # Was plain active or missing — ensure it's present and uncommented
            new_text2, n2 = re.subn(active_pat, r"\1", text, flags=re.MULTILINE)
            if n2 == 0:
                new_text = text.rstrip("\n") + "\n#define " + name + "\n"
            else:
                new_text = new_text2
    else:
        new_text, n = re.subn(active_pat, r"//\1", text, flags=re.MULTILINE)
        if n == 0:
            # Already commented or missing
            new_text = text
    return new_text


def _set_define_value(text: str, name: str, value: str) -> str:
    """Replace the value in an active #define NAME OLD → #define NAME VALUE."""
    pattern = r"^(#define\s+" + re.escape(name) + r")\s+\S+"
    replacement = r"\1 " + value
    new_text, n = re.subn(pattern, replacement, text, flags=re.MULTILINE)
    if n == 0:
        # Not present at all — add it
        new_text = text.rstrip("\n") + "\n#define " + name + " " + value + "\n"
    return new_text


# ── main widget ───────────────────────────────────────────────────────────────

class ConfigTabWidget(QWidget):
    """
    Embedded widget for the Config tab.
    Exposes a `modified` signal and load()/save() interface matching the
    pattern used by ItemsTabWidget / MovesTabWidget.
    """

    modified = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._project_dir: str = ""
        self._dirty: bool = False
        self._build_ui()

    # ── construction ─────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # Scroll area so the form still works if the window is small
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(4, 4, 4, 4)
        inner_layout.setSpacing(14)

        def _desc(text: str) -> QLabel:
            """Small grey description label shown directly below a form row."""
            lbl = QLabel(text)
            lbl.setStyleSheet("color: #666666; font-size: 10px;")
            lbl.setWordWrap(True)
            return lbl

        # ── Build Settings (config.mk) ────────────────────────────────────────
        mk_group = QGroupBox("Build Settings  (config.mk)")
        mk_group.setStyleSheet(_CARD_SS + _FIELD_SS)
        mk_form = QFormLayout(mk_group)
        mk_form.setContentsMargins(12, 16, 12, 12)
        mk_form.setSpacing(4)

        self._game_version = QComboBox()
        self._game_version.addItems(["FIRERED", "LEAFGREEN"])
        mk_form.addRow("Game Version:", self._game_version)
        mk_form.addRow("", _desc(
            "Build as FireRed or LeafGreen. Affects the ROM title, game code (BPRE / BPGE), "
            "and which version-specific maps and events are included."
        ))

        self._game_revision = QComboBox()
        self._game_revision.addItems(["0", "1"])
        mk_form.addRow("Game Revision:", self._game_revision)
        mk_form.addRow("", _desc(
            "ROM revision: 0 = original launch release, 1 = bug-fix re-release. "
            "Use 0 unless you specifically need the revision-1 binary."
        ))

        self._game_language = QComboBox()
        self._game_language.addItems(["ENGLISH"])
        mk_form.addRow("Game Language:", self._game_language)
        mk_form.addRow("", _desc("Target language. Only ENGLISH is currently supported by pokefirered."))

        self._modern_cb = QCheckBox("Enable")
        mk_form.addRow("Modern Mode:", self._modern_cb)
        self._modern_note = QLabel(
            "Uses arm-none-eabi-gcc instead of agbcc. Enables modern C features, "
            "BUGFIX, and UBFIX automatically. ROM will NOT match the original binary."
        )
        self._modern_note.setStyleSheet("color: #bb8800; font-size: 10px;")
        self._modern_note.setWordWrap(True)
        self._modern_note.setVisible(False)
        mk_form.addRow("", self._modern_note)
        mk_form.addRow("", _desc(
            "Compiles with modern GCC instead of the original agbcc. Produces a larger "
            "but more feature-rich ROM. Required for C++ and advanced optimisations."
        ))

        self._compare_cb = QCheckBox("Enable")
        mk_form.addRow("Compare Build:", self._compare_cb)
        mk_form.addRow("", _desc(
            "Also builds an unmodified reference ROM alongside yours and compares the two. "
            "Only useful for verifying that your changes differ exactly as expected."
        ))

        self._keep_temps_cb = QCheckBox("Enable")
        mk_form.addRow("Keep Temps:", self._keep_temps_cb)
        mk_form.addRow("", _desc(
            "Retains intermediate .i (preprocessed) and .s (assembly) files in the build "
            "directory after compilation. Useful for debugging compiler output."
        ))

        inner_layout.addWidget(mk_group)

        # ── Debug Settings (include/config.h) ─────────────────────────────────
        h_group = QGroupBox("Debug / Logging Settings  (include/config.h)")
        h_group.setStyleSheet(_CARD_SS + _FIELD_SS)
        h_form = QFormLayout(h_group)
        h_form.setContentsMargins(12, 16, 12, 12)
        h_form.setSpacing(4)

        self._ndebug_cb = QCheckBox("Enable  (release mode — disables all debug output)")
        h_form.addRow("NDEBUG:", self._ndebug_cb)
        h_form.addRow("", _desc(
            "When enabled, all debug logging is compiled out completely (no overhead). "
            "Enable for final release builds. Disable when testing on an emulator."
        ))

        self._log_handler = QComboBox()
        self._log_handler.addItems([
            "LOG_HANDLER_AGB_PRINT",
            "LOG_HANDLER_NOCASH_PRINT",
            "LOG_HANDLER_MGBA_PRINT",
        ])
        h_form.addRow("Log Handler:", self._log_handler)
        h_form.addRow("", _desc(
            "AGB_PRINT: hardware AGB Cartridge Printer (very rare).  "
            "NOCASH_PRINT: no$gba debugger output.  "
            "MGBA_PRINT: mGBA scripting console (recommended for most users)."
        ))

        self._pretty_print = QComboBox()
        self._pretty_print.addItems([
            "PRETTY_PRINT_OFF",
            "PRETTY_PRINT_MINI_PRINTF",
            "PRETTY_PRINT_LIBC",
        ])
        h_form.addRow("Pretty Print:", self._pretty_print)
        h_form.addRow("", _desc(
            "Formatting library used for debug strings.  "
            "OFF: raw strings only (smallest).  "
            "MINI_PRINTF: lightweight custom printf.  "
            "LIBC: full newlib printf (largest ROM, most features)."
        ))

        inner_layout.addWidget(h_group)

        inner_layout.addStretch(1)
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

        # ── Bottom button bar ─────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self._save_btn = QPushButton("Save Config")
        self._save_btn.setMinimumWidth(120)
        self._save_btn.setEnabled(False)
        btn_row.addWidget(self._save_btn)
        root.addLayout(btn_row)

        # ── Signal wiring ─────────────────────────────────────────────────────
        self._modern_cb.toggled.connect(self._on_modern_toggled)
        self._ndebug_cb.toggled.connect(self._on_ndebug_toggled)
        self._save_btn.clicked.connect(self.save)

        for widget in (
            self._game_version, self._game_revision, self._game_language,
            self._log_handler, self._pretty_print,
        ):
            widget.currentIndexChanged.connect(self._mark_dirty)

        for cb in (self._modern_cb, self._compare_cb, self._keep_temps_cb, self._ndebug_cb):
            cb.toggled.connect(self._mark_dirty)

    # ── internal slots ────────────────────────────────────────────────────────

    def _on_modern_toggled(self, checked: bool) -> None:
        self._modern_note.setVisible(checked)

    def _on_ndebug_toggled(self, checked: bool) -> None:
        """Grey out log/print combos when NDEBUG is checked."""
        self._log_handler.setEnabled(not checked)
        self._pretty_print.setEnabled(not checked)

    def _mark_dirty(self, *_) -> None:
        if not self._dirty:
            self._dirty = True
            self._save_btn.setEnabled(True)
            self.modified.emit()

    # ── public API ────────────────────────────────────────────────────────────

    def load(self, project_dir: str) -> None:
        """Populate all widgets from disk. Clears the dirty flag."""
        self._project_dir = project_dir
        self._dirty = False
        self._save_btn.setEnabled(False)

        mk_path = os.path.join(project_dir, "config.mk")
        mk_text = _read_file(mk_path)

        h_path = os.path.join(project_dir, "include", "config.h")
        h_text = _read_file(h_path)

        # Block signals while populating to avoid spurious dirty marks
        for widget in (
            self._game_version, self._game_revision, self._game_language,
            self._log_handler, self._pretty_print,
            self._modern_cb, self._compare_cb, self._keep_temps_cb, self._ndebug_cb,
        ):
            widget.blockSignals(True)

        try:
            # config.mk dropdowns
            def _set_combo(combo: QComboBox, value: str) -> None:
                idx = combo.findText(value)
                if idx >= 0:
                    combo.setCurrentIndex(idx)

            _set_combo(self._game_version,  _parse_mk_value(mk_text, "GAME_VERSION"))
            _set_combo(self._game_revision,  _parse_mk_value(mk_text, "GAME_REVISION"))
            _set_combo(self._game_language,  _parse_mk_value(mk_text, "GAME_LANGUAGE"))

            # config.mk checkboxes — value "1" means on, "0" off
            self._modern_cb.setChecked(_parse_mk_value(mk_text, "MODERN") == "1")
            self._compare_cb.setChecked(_parse_mk_value(mk_text, "COMPARE") == "1")
            self._keep_temps_cb.setChecked(_parse_mk_value(mk_text, "KEEP_TEMPS") == "1")

            # include/config.h
            ndebug_on = _is_define_active(h_text, "NDEBUG")
            self._ndebug_cb.setChecked(ndebug_on)
            self._log_handler.setEnabled(not ndebug_on)
            self._pretty_print.setEnabled(not ndebug_on)

            log_val = _parse_define_value(h_text, "LOG_HANDLER")
            if log_val is not None:
                _set_combo(self._log_handler, log_val)

            pp_val = _parse_define_value(h_text, "PRETTY_PRINT_HANDLER")
            if pp_val is not None:
                _set_combo(self._pretty_print, pp_val)

        finally:
            for widget in (
                self._game_version, self._game_revision, self._game_language,
                self._log_handler, self._pretty_print,
                self._modern_cb, self._compare_cb, self._keep_temps_cb, self._ndebug_cb,
            ):
                widget.blockSignals(False)

        self._modern_note.setVisible(self._modern_cb.isChecked())

    def has_changes(self) -> bool:
        return self._dirty

    def save(self) -> None:
        """Write config.mk and include/config.h back to disk."""
        if not self._project_dir:
            return

        # ── config.mk ────────────────────────────────────────────────────────
        mk_path = os.path.join(self._project_dir, "config.mk")
        mk_text = _read_file(mk_path)
        if not mk_text and not os.path.isfile(mk_path):
            QMessageBox.warning(
                self, "Config",
                f"config.mk not found at:\n{mk_path}\n\nLoad a project first."
            )
            return

        mk_text = _set_mk_value(mk_text, "GAME_VERSION",  self._game_version.currentText())
        mk_text = _set_mk_value(mk_text, "GAME_REVISION",  self._game_revision.currentText())
        mk_text = _set_mk_value(mk_text, "GAME_LANGUAGE",  self._game_language.currentText())
        mk_text = _set_mk_value(mk_text, "MODERN",   "1" if self._modern_cb.isChecked() else "0")
        mk_text = _set_mk_value(mk_text, "COMPARE",  "1" if self._compare_cb.isChecked() else "0")
        mk_text = _set_mk_value(mk_text, "KEEP_TEMPS", "1" if self._keep_temps_cb.isChecked() else "0")
        _write_file(mk_path, mk_text)

        # ── include/config.h ─────────────────────────────────────────────────
        h_path = os.path.join(self._project_dir, "include", "config.h")
        h_text = _read_file(h_path)
        if not h_text and not os.path.isfile(h_path):
            QMessageBox.warning(
                self, "Config",
                f"include/config.h not found at:\n{h_path}"
            )
            return

        h_text = _set_define_active(h_text, "NDEBUG", self._ndebug_cb.isChecked())
        h_text = _set_define_value(h_text, "LOG_HANDLER", self._log_handler.currentText())
        h_text = _set_define_value(h_text, "PRETTY_PRINT_HANDLER", self._pretty_print.currentText())
        _write_file(h_path, h_text)

        self._dirty = False
        self._save_btn.setEnabled(False)
