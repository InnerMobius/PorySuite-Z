"""
ui/dialogs/startup_dialogs.py
Startup dialogs for PorySuite-Z:
  - UpdateDialog: shows available update with changelog
  - DisclaimerDialog: first-run beta warning with checkbox
  - BackupReminderDialog: first-open-per-project backup check
"""
from __future__ import annotations

import webbrowser

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
    QMessageBox, QPushButton, QTextBrowser, QVBoxLayout,
)


# ── Update Available Dialog ──────────────────────────────────────────────────

class UpdateDialog(QDialog):
    """Shows when a newer version is found on GitHub.

    Displays the release name, changelog/body, and three actions:
    Install Now, View Release Page, Later.
    """

    INSTALL = "install"
    VIEW_PAGE = "view"
    LATER = "later"

    def __init__(self, release_info: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Update Available")
        self.setMinimumWidth(520)
        self.setMinimumHeight(350)
        self._release = release_info
        self._result_action = self.LATER
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        from core.app_info import VERSION
        tag = self._release.get("tag", "?")
        name = self._release.get("name", tag)

        header = QLabel(
            f'<h2>PorySuite-Z {name} is available</h2>'
            f'<p>You are currently running <b>{VERSION}</b>.</p>'
        )
        header.setTextFormat(Qt.TextFormat.RichText)
        header.setWordWrap(True)
        layout.addWidget(header)

        # Changelog / release body
        body = self._release.get("body", "").strip()
        if body:
            changelog_label = QLabel("<b>What's new:</b>")
            layout.addWidget(changelog_label)

            changelog = QTextBrowser()
            changelog.setOpenExternalLinks(True)
            changelog.setMarkdown(body)
            changelog.setMinimumHeight(150)
            layout.addWidget(changelog, 1)
        else:
            no_notes = QLabel(
                '<i style="color: #999;">No release notes available.</i>'
            )
            no_notes.setTextFormat(Qt.TextFormat.RichText)
            layout.addWidget(no_notes)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        install_btn = QPushButton("Install Now")
        install_btn.setToolTip("Download and install the update, then restart")
        install_btn.setDefault(True)
        install_btn.clicked.connect(self._on_install)
        btn_row.addWidget(install_btn)

        page_btn = QPushButton("View Release Page")
        page_btn.setToolTip("Open the GitHub release page in your browser")
        page_btn.clicked.connect(self._on_view_page)
        btn_row.addWidget(page_btn)

        btn_row.addStretch(1)

        later_btn = QPushButton("Later")
        later_btn.clicked.connect(self._on_later)
        btn_row.addWidget(later_btn)

        layout.addLayout(btn_row)

    def _on_install(self):
        self._result_action = self.INSTALL
        self.accept()

    def _on_view_page(self):
        url = self._release.get("url", "")
        if url:
            webbrowser.open(url)
        self._result_action = self.VIEW_PAGE
        self.accept()

    def _on_later(self):
        self._result_action = self.LATER
        self.reject()

    @property
    def action(self) -> str:
        return self._result_action


# ── First-Run Disclaimer Dialog ──────────────────────────────────────────────

class DisclaimerDialog(QDialog):
    """Shown once on the very first launch.  User must check the
    acknowledgement checkbox before OK becomes clickable."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("PorySuite-Z — Important Notice")
        self.setMinimumWidth(560)
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        from core.app_info import VERSION

        warning = QLabel(
            f'<h2>Welcome to PorySuite-Z {VERSION}</h2>'
            '<p style="font-size: 12px;">'
            'This application is in <b>beta</b> and is <b>experimental</b>. '
            'It was developed with the assistance of <b>Anthropic Claude Code</b>.</p>'
            '<p style="font-size: 12px;">'
            'PorySuite-Z directly reads and writes files inside your '
            '<b>pokefirered</b> project folder. While every effort has been '
            'made to prevent data loss, <b>bugs may exist</b> that could '
            'corrupt or unintentionally modify your project files.</p>'
            '<p style="font-size: 12px; color: #ff6666;">'
            '<b>Before continuing, please create a backup of your project '
            'folder.</b> The easiest way is to commit your current state '
            'with <code>git commit</code> so you can always revert.</p>'
            '<p style="font-size: 12px;">'
            'Bug reports are welcome and encouraged — please report any '
            'issues you find on the GitHub repository.</p>'
            '<p style="font-size: 12px;">'
            'By using this application, you acknowledge that it is '
            'experimental software and that the developer is not '
            'responsible for any unwanted changes to your project files '
            'if a backup was not made.</p>'
        )
        warning.setTextFormat(Qt.TextFormat.RichText)
        warning.setWordWrap(True)
        layout.addWidget(warning)

        self._checkbox = QCheckBox(
            "I understand this app is experimental and I take responsibility "
            "for backing up my project"
        )
        self._checkbox.toggled.connect(self._on_checkbox)
        layout.addWidget(self._checkbox)

        self._buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        self._buttons.accepted.connect(self.accept)
        layout.addWidget(self._buttons)

    def _on_checkbox(self, checked: bool):
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(checked)


# ── Per-Project Backup Reminder ──────────────────────────────────────────────

class BackupReminderDialog(QDialog):
    """Shown the first time a specific project is opened.
    Two buttons: continue or cancel."""

    def __init__(self, project_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("First Time Opening Project")
        self.setMinimumWidth(480)
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )
        self._build_ui(project_name)

    def _build_ui(self, project_name: str):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        msg = QLabel(
            f'<p style="font-size: 12px;">'
            f'This is the first time PorySuite-Z is opening '
            f'<b>{project_name}</b>.</p>'
            f'<p style="font-size: 12px;">'
            f'Have you backed up your project folder? We strongly '
            f'recommend using <code>git commit</code> or copying the '
            f'folder before continuing.</p>'
        )
        msg.setTextFormat(Qt.TextFormat.RichText)
        msg.setWordWrap(True)
        layout.addWidget(msg)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        continue_btn = QPushButton("I have a backup, continue")
        continue_btn.setDefault(True)
        continue_btn.clicked.connect(self.accept)
        btn_row.addWidget(continue_btn)

        layout.addLayout(btn_row)
