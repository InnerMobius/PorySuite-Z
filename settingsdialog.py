"""settingsdialog.py — PorySuite-Z Settings dialog."""

import os
import textwrap
from collections import defaultdict

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QCheckBox, QGroupBox, QScrollArea, QWidget, QSizePolicy,
)
from PyQt6.QtCore import Qt, QSettings

from app_info import get_settings_path
from suppress_dialog import SUPPRESSIBLE, suppress, is_suppressed, clear_all


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings — PorySuite-Z")
        self.setModal(True)
        self.resize(560, 500)
        self.setMinimumWidth(480)

        os.makedirs(os.path.dirname(get_settings_path()), exist_ok=True)
        self.settings = QSettings(get_settings_path(), QSettings.Format.IniFormat)

        root = QVBoxLayout(self)
        root.setSpacing(12)

        # ── Scroll area for all sections ─────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setSpacing(14)
        inner_layout.setContentsMargins(4, 4, 4, 4)
        scroll.setWidget(inner)
        root.addWidget(scroll)

        # ── Advanced Diagnostics ─────────────────────────────────────────────
        diag_box = QGroupBox("Advanced Diagnostics")
        diag_layout = QVBoxLayout(diag_box)
        diag_layout.addWidget(QLabel(
            "Show verbose internal diagnostic messages (types/gender parsing).\n"
            "These messages are noisy and intended for debugging.\n"
            "Enable only when requested."
        ))
        self.adv_checkbox = QCheckBox("Enable Advanced Diagnostics")
        self.adv_checkbox.setChecked(
            bool(self.settings.value("advanced_diagnostics", False, type=bool))
        )
        diag_layout.addWidget(self.adv_checkbox)
        inner_layout.addWidget(diag_box)

        # ── Autosave ─────────────────────────────────────────────────────────
        auto_box = QGroupBox("Autosave (Experimental)")
        auto_layout = QVBoxLayout(auto_box)
        auto_layout.addWidget(QLabel(
            "Automatically save project changes.\n"
            "THIS IS EXPERIMENTAL — keep backups before enabling."
        ))
        self.auto_checkbox = QCheckBox("Enable Autosave (experimental)")
        self.auto_checkbox.setChecked(
            bool(self.settings.value("autosave_enabled", False, type=bool))
        )
        auto_layout.addWidget(self.auto_checkbox)
        inner_layout.addWidget(auto_box)

        # ── Notification Preferences ──────────────────────────────────────────
        notif_box = QGroupBox("Notification Preferences")
        notif_layout = QVBoxLayout(notif_box)
        notif_layout.addWidget(QLabel(
            "Dialogs marked 'Don't show again' are listed below.\n"
            "Re-check a box to re-enable that confirmation."
        ))

        self._notif_checks: dict[str, QCheckBox] = {}

        # Group by category
        by_cat: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for key, (label, cat) in SUPPRESSIBLE.items():
            by_cat[cat].append((key, label))

        for cat, entries in sorted(by_cat.items()):
            cat_label = QLabel(f"<b>{cat}</b>")
            notif_layout.addWidget(cat_label)
            for key, label in entries:
                cb = QCheckBox(label)
                # Checked = notification is ACTIVE (not suppressed)
                cb.setChecked(not is_suppressed(key))
                self._notif_checks[key] = cb
                notif_layout.addWidget(cb)

        btn_reset = QPushButton("Re-enable All Notifications")
        btn_reset.clicked.connect(self._reset_all_notifications)
        notif_layout.addWidget(btn_reset)

        inner_layout.addWidget(notif_box)

        # ── Build Environment / Setup ─────────────────────────────────────────
        setup_box = QGroupBox("Build Environment")
        setup_layout = QVBoxLayout(setup_box)
        setup_layout.addWidget(QLabel(
            "Check and install the tools required to build GBA ROMs:\n"
            "MSYS2, devkitPro, agbcc, and more."
        ))
        btn_setup = QPushButton("Open Setup Wizard...")
        btn_setup.clicked.connect(self._open_setup)
        setup_layout.addWidget(btn_setup)
        inner_layout.addWidget(setup_box)

        inner_layout.addStretch(1)

        # ── Dialog buttons ───────────────────────────────────────────────────
        btn_layout = QHBoxLayout()
        btn_layout.addStretch(1)
        ok = QPushButton("OK")
        cancel = QPushButton("Cancel")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        btn_layout.addWidget(ok)
        btn_layout.addWidget(cancel)
        root.addLayout(btn_layout)

    def _open_setup(self):
        from programsetup import ProgramSetup
        dlg = ProgramSetup(self)
        dlg.exec()

    def _reset_all_notifications(self):
        clear_all()
        for cb in self._notif_checks.values():
            cb.setChecked(True)

    def accept(self) -> None:
        self.settings.setValue("advanced_diagnostics", bool(self.adv_checkbox.isChecked()))
        self.settings.setValue("autosave_enabled",     bool(self.auto_checkbox.isChecked()))
        self.settings.sync()

        # Apply notification preferences
        for key, cb in self._notif_checks.items():
            suppress(key, not cb.isChecked())   # unchecked = suppressed

        super().accept()
