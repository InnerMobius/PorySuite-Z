import os
import textwrap
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QHBoxLayout, QPushButton, QCheckBox
from PyQt6.QtCore import Qt, QSettings
from app_info import get_settings_path


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setModal(True)
        self.resize(520, 240)

        os.makedirs(os.path.dirname(get_settings_path()), exist_ok=True)
        self.settings = QSettings(get_settings_path(), QSettings.Format.IniFormat)

        layout = QVBoxLayout(self)

        # Advanced diagnostics
        adv_label = QLabel(
            textwrap.dedent(
                """
                Advanced Diagnostics
                Show verbose internal diagnostic messages (types/gender parsing).
                These messages are noisy and intended for debugging. Enable only
                when requested.
                """
            )
        )
        adv_label.setWordWrap(True)
        layout.addWidget(adv_label)

        self.adv_checkbox = QCheckBox("Enable Advanced Diagnostics")
        self.adv_checkbox.setChecked(
            bool(self.settings.value("advanced_diagnostics", False, type=bool))
        )
        layout.addWidget(self.adv_checkbox)

        # Autosave (not wired yet)
        auto_label = QLabel(
            textwrap.dedent(
                """
                Autosave (experimental)
                Automatically save project changes. THIS IS EXPERIMENTAL and may
                corrupt project setups. Keep backups locally or on GitHub before enabling.
                (Not active yet — TODO: implement with caution.)
                """
            )
        )
        auto_label.setWordWrap(True)
        layout.addWidget(auto_label)

        self.auto_checkbox = QCheckBox("Enable Autosave (experimental)")
        self.auto_checkbox.setChecked(
            bool(self.settings.value("autosave_enabled", False, type=bool))
        )
        layout.addWidget(self.auto_checkbox)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch(1)
        ok = QPushButton("OK")
        cancel = QPushButton("Cancel")
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        btn_layout.addWidget(ok)
        btn_layout.addWidget(cancel)
        layout.addLayout(btn_layout)

    def accept(self) -> None:
        self.settings.setValue("advanced_diagnostics", bool(self.adv_checkbox.isChecked()))
        self.settings.setValue("autosave_enabled", bool(self.auto_checkbox.isChecked()))
        super().accept()

