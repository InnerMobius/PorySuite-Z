"""suppress_dialog.py — "Don't show again" helper for PorySuite-Z.

Usage
-----
    from suppress_dialog import maybe_exec, SUPPRESSIBLE
    from PyQt6.QtWidgets import QMessageBox

    # Informational (returns QMessageBox.StandardButton.Ok when suppressed)
    maybe_exec(
        key="rename_queued",
        parent=self,
        title="Rename Queued",
        text="Rename staged. Save to apply.",
    )

    # Question (returns default_button when suppressed)
    ans = maybe_exec(
        key="pull_confirm",
        parent=self,
        title="Pull from Remote",
        text="This will discard local changes. Continue?",
        icon=QMessageBox.Icon.Question,
        buttons=QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        default_button=QMessageBox.StandardButton.Yes,
    )
    if ans != QMessageBox.StandardButton.Yes:
        return
"""

from __future__ import annotations

from PyQt6.QtWidgets import QMessageBox, QCheckBox
from PyQt6.QtCore import QSettings

from app_info import get_settings_path

# ---------------------------------------------------------------------------
# Registry of all suppressible dialogs
# key → (human-readable label, category)
# ---------------------------------------------------------------------------
SUPPRESSIBLE: dict[str, tuple[str, str]] = {
    # Rename flow
    "rename_queued_trainer":    ("Trainer rename staged notification",          "Rename"),
    "rename_queued_move":       ("Move rename staged notification",              "Rename"),
    "rename_queued_item":       ("Item rename staged notification",              "Rename"),
    "rename_apply_confirm":     ("Species rename preview confirmation",          "Rename"),
    "rename_complete":          ("Species rename queued notification",           "Rename"),
    # Save flow
    "save_header_confirm":      ("C header write-back confirmation on Save",     "Save"),
}


def _settings() -> QSettings:
    return QSettings(get_settings_path(), QSettings.Format.IniFormat)


def is_suppressed(key: str) -> bool:
    s = _settings()
    return bool(s.value(f"suppress/{key}", False, type=bool))


def suppress(key: str, value: bool = True) -> None:
    s = _settings()
    s.setValue(f"suppress/{key}", value)
    s.sync()


def clear_all() -> None:
    s = _settings()
    s.beginGroup("suppress")
    s.remove("")   # removes all keys in the group
    s.endGroup()
    s.sync()


def maybe_exec(
    key: str,
    parent,
    title: str,
    text: str,
    icon: QMessageBox.Icon = QMessageBox.Icon.Information,
    buttons: QMessageBox.StandardButton = QMessageBox.StandardButton.Ok,
    default_button: QMessageBox.StandardButton | None = None,
) -> QMessageBox.StandardButton:
    """Show a message box with a 'Don't show again' checkbox.

    If the dialog has been suppressed, immediately returns *default_button*
    (or the first button in *buttons* if not specified) without showing UI.
    """
    if default_button is None:
        # Pick a sensible default: prefer Yes, then Ok, then first bit set
        for candidate in (
            QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Ok,
            QMessageBox.StandardButton.Save,
        ):
            if buttons & candidate:
                default_button = candidate
                break
        else:
            default_button = QMessageBox.StandardButton.Ok

    if is_suppressed(key):
        return default_button

    dlg = QMessageBox(icon, title, text, buttons, parent)
    if default_button:
        dlg.setDefaultButton(default_button)

    cb = QCheckBox("Don't show this again")
    dlg.setCheckBox(cb)

    result = QMessageBox.StandardButton(dlg.exec())

    if cb.isChecked():
        suppress(key, True)

    return result
