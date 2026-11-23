"""Tools → Repair…: one window for every repair the app offers.

Each repair here fixes wrong bytes that an EARLIER VERSION of PorySuite-Z wrote
into the user's game files. Fixing the code that wrote them does not fix files
already on disk, so each one needs a repair the user can run — and each used to
get its own Tools menu entry, which was turning the menu into a list of repairs
with the real tools buried among them.

This window lists them instead. It scans as soon as it opens, which is safe
because every registered scan is read-only (see ``core/repairs.py``), and shows
each repair's own confirm-and-report flow when the user presses its button —
those flows are unchanged and still show exactly what would be written before
writing anything.

The window owns no repair logic of its own. It reads the list from
``core.repairs`` and calls back into the handlers it was given, so adding a
repair means adding a ``RepairSpec`` and a handler, not touching this file.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QPushButton,
    QDialogButtonBox, QWidget, QScrollArea,
)

from core.repairs import available_repairs, plural, scan_all

_log = logging.getLogger("PorySuite.Repairs")

_OK_COLOR = "#7bb47b"
_FOUND_COLOR = "#ffb74d"
_ERROR_COLOR = "#e57373"


class RepairDialog(QDialog):
    """Lists every repair, what it found, and a button to run it.

    *handlers* maps a repair's key to a zero-argument callable that runs that
    repair's own flow (confirm, write, report). It returns when that flow is
    done; the row is then re-scanned so the count the user is looking at is the
    truth and not what it was before they pressed the button.
    """

    def __init__(self, root: str, handlers: dict, parent=None):
        super().__init__(parent)
        self._root = root
        self._handlers = handlers or {}
        self._rows: dict = {}

        self.setWindowTitle("Repair")
        self.resize(720, 520)

        outer = QVBoxLayout(self)

        head = QLabel(
            "<b>These repair problems in your game files that earlier "
            "versions of PorySuite-Z may have created.</b><br><br>"
            "PorySuite has had bugs that wrote the wrong thing into your "
            "project. Fixing the bug stops it happening again, but it cannot "
            "fix files that were already written — so each of those bugs gets "
            "a repair here.<br><br>"
            "Nothing below has changed anything yet. Each repair shows you "
            "exactly what it found and asks before it writes. "
            "<b>Back up or commit your game project first.</b>")
        head.setWordWrap(True)
        head.setTextFormat(Qt.TextFormat.RichText)
        outer.addWidget(head)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        outer.addWidget(line)

        # Scrolls, because this list only grows as more old bugs get repairs.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        self._list = QVBoxLayout(inner)
        self._list.setContentsMargins(0, 4, 0, 4)
        self._list.setSpacing(10)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        for spec in available_repairs():
            self._list.addWidget(self._build_row(spec))
        self._list.addStretch(1)

        self._summary = QLabel()
        self._summary.setWordWrap(True)
        outer.addWidget(self._summary)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        rescan = buttons.addButton("Check Again",
                                   QDialogButtonBox.ButtonRole.ResetRole)
        rescan.clicked.connect(self.refresh)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        self.refresh()

    # ── rows ────────────────────────────────────────────────────────────────

    def _build_row(self, spec) -> QWidget:
        box = QFrame()
        box.setFrameShape(QFrame.Shape.StyledPanel)
        v = QVBoxLayout(box)
        v.setSpacing(4)

        top = QHBoxLayout()
        title = QLabel(f"<b>{spec.title}</b>")
        title.setTextFormat(Qt.TextFormat.RichText)
        top.addWidget(title, 1)
        btn = QPushButton("Repair…")
        btn.setEnabled(False)
        btn.clicked.connect(lambda _=False, s=spec: self._run(s))
        top.addWidget(btn)
        v.addLayout(top)

        desc = QLabel(spec.description)
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #aaaaaa;")
        v.addWidget(desc)

        status = QLabel()
        status.setWordWrap(True)
        v.addWidget(status)

        self._rows[spec.key] = {'button': btn, 'status': status, 'spec': spec}
        return box

    # ── scanning ────────────────────────────────────────────────────────────

    def refresh(self) -> None:
        """Re-run every scan and update each row. Writes nothing."""
        results = scan_all(self._root)
        found_any = 0
        broken = 0
        for key, row in self._rows.items():
            findings = results.get(key)
            if findings is None:
                row['status'].setText(
                    "Could not check this one — see the log for why.")
                row['status'].setStyleSheet(f"color: {_ERROR_COLOR};")
                row['button'].setEnabled(False)
                broken += 1
                continue
            text = row['spec'].describe(findings)
            if text:
                row['status'].setText(f"Found: {text}")
                row['status'].setStyleSheet(f"color: {_FOUND_COLOR};")
                row['button'].setEnabled(key in self._handlers)
                found_any += 1
            else:
                row['status'].setText("Nothing to repair.")
                row['status'].setStyleSheet(f"color: {_OK_COLOR};")
                row['button'].setEnabled(False)

        if broken and not found_any:
            self._summary.setText(
                f"{plural(broken, 'check', 'checks')} could not run. "
                "Nothing has been changed.")
        elif found_any:
            verb = "has" if found_any == 1 else "have"
            self._summary.setText(
                f"{plural(found_any, 'repair', 'repairs')} {verb} something "
                "to fix. Nothing has been changed yet.")
        else:
            self._summary.setText(
                "Nothing needs repairing — none of these problems are present "
                "in this project.")

    # ── running one ─────────────────────────────────────────────────────────

    def _run(self, spec) -> None:
        handler = self._handlers.get(spec.key)
        if handler is None:
            return
        try:
            handler()
        except Exception:
            _log.exception("repair flow failed: %s", spec.key)
        # The handler may have written, cancelled, or refused. Re-scan rather
        # than assume which, so the row cannot claim a fix that did not happen.
        self.refresh()
