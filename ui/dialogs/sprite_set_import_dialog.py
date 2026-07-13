"""
Dialog for importing a species' full sprite set (normal + shiny) at once.

The artist hands over separate PNGs for normal-front, shiny-front, normal-back,
shiny-back (and a menu icon). This dialog collects those files; the caller runs
``core.sprite_set_import.build_sprite_set`` on them to produce ONE shared sprite
per view plus matched normal/shiny palettes, so both versions display exactly as
drawn (see that module).

Convenience: pick the Normal Front file and the other rows auto-fill from
same-folder siblings whose names differ only by a trailing number (the common
``001Octorok1..5`` layout), each still editable.
"""

from __future__ import annotations

import os
import re
from typing import Dict, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QGridLayout, QLabel, QLineEdit, QPushButton,
    QFileDialog, QDialogButtonBox, QHBoxLayout,
)

# Row key → (display label, help)
_ROWS = [
    ("front_normal", "Normal Front", "The everyday front sprite (required)."),
    ("front_shiny",  "Shiny Front",  "Shiny recolour of the front (same drawing)."),
    ("back_normal",  "Normal Back",  "The everyday back sprite."),
    ("back_shiny",   "Shiny Back",   "Shiny recolour of the back."),
    ("icon",         "Menu Icon",    "32×64 mini icon (optional)."),
]


def _sibling(path: str, my_num: str, target_num: str) -> str:
    """Given a picked file whose name ends in `my_num` before .png, return the
    same-folder file ending in `target_num`, if it exists."""
    folder = os.path.dirname(path)
    base = os.path.basename(path)
    # Replace the LAST run of digits before the extension.
    m = re.match(r"^(.*?)(\d+)(\.[^.]+)$", base)
    if not m:
        return ""
    stem, num, ext = m.group(1), m.group(2), m.group(3)
    if num != my_num:
        return ""
    cand = os.path.join(folder, f"{stem}{target_num}{ext}")
    return cand if os.path.isfile(cand) else ""


class SpriteSetImportDialog(QDialog):
    def __init__(self, start_dir: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import Normal + Shiny Sprite Set")
        self.setMinimumWidth(560)
        self._start_dir = start_dir or ""
        self._edits: Dict[str, QLineEdit] = {}

        v = QVBoxLayout(self)
        intro = QLabel(
            "Pick your artist's PNGs. The normal sprite's pixels become the "
            "shared sprite; the shiny palette is auto-mapped from the shiny PNG "
            "pixel-for-pixel, so both display exactly as drawn. Only Normal "
            "Front is required — leave the rest blank if you don't have them.")
        intro.setWordWrap(True)
        intro.setStyleSheet("color:#aaa;")
        v.addWidget(intro)

        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        for r, (key, label, help_txt) in enumerate(_ROWS):
            lbl = QLabel(label + ":")
            lbl.setToolTip(help_txt)
            edit = QLineEdit()
            edit.setPlaceholderText(help_txt)
            edit.setClearButtonEnabled(True)
            btn = QPushButton("Browse…")
            btn.clicked.connect(lambda _=False, k=key: self._browse(k))
            grid.addWidget(lbl, r, 0)
            grid.addWidget(edit, r, 1)
            grid.addWidget(btn, r, 2)
            self._edits[key] = edit
        v.addLayout(grid)

        self._auto_lbl = QLabel("")
        self._auto_lbl.setStyleSheet("color:#7bc47b;font-size:11px;")
        self._auto_lbl.setWordWrap(True)
        v.addWidget(self._auto_lbl)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._on_ok)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _browse(self, key: str) -> None:
        cur = self._edits[key].text().strip()
        start = (os.path.dirname(cur) if cur and os.path.isdir(os.path.dirname(cur))
                 else self._start_dir)
        path, _ = QFileDialog.getOpenFileName(
            self, f"Select {dict((k, l) for k, l, _h in _ROWS)[key]} PNG",
            start, "PNG Images (*.png)")
        if not path:
            return
        self._edits[key].setText(path)
        self._start_dir = os.path.dirname(path)
        if key == "front_normal":
            self._autofill_siblings(path)

    def _autofill_siblings(self, front_normal_path: str) -> None:
        """Fill blank rows from same-folder numbered siblings (…1 → …2/3/4/5)."""
        base = os.path.basename(front_normal_path)
        m = re.match(r"^(.*?)(\d+)(\.[^.]+)$", base)
        if not m:
            return
        my_num = m.group(2)
        targets = {
            "front_shiny": str(int(my_num) + 1),
            "back_normal": str(int(my_num) + 2),
            "back_shiny":  str(int(my_num) + 3),
            "icon":        str(int(my_num) + 4),
        }
        filled = []
        for key, tnum in targets.items():
            if self._edits[key].text().strip():
                continue     # don't clobber a user's manual pick
            sib = _sibling(front_normal_path, my_num, tnum)
            if sib:
                self._edits[key].setText(sib)
                filled.append(os.path.basename(sib))
        self._auto_lbl.setText(
            "Auto-filled: " + ", ".join(filled) if filled else "")

    def _on_ok(self) -> None:
        if not self._edits["front_normal"].text().strip():
            self._auto_lbl.setStyleSheet("color:#e06c6c;font-size:11px;")
            self._auto_lbl.setText("Normal Front is required.")
            return
        self.accept()

    def paths(self) -> Dict[str, str]:
        return {k: e.text().strip() for k, e in self._edits.items()}


def pick_sprite_set(start_dir: str = "", parent=None) -> Optional[Dict[str, str]]:
    dlg = SpriteSetImportDialog(start_dir, parent)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    return dlg.paths()
