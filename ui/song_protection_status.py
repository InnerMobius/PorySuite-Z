"""The Songs page's song build protection status line.

Three states (``song_guard.assess_risk``), checked in the background when a
project's songs load and again after the setup window closes:

* **protected** — a quiet one-line note;
* **unprotected but safe** — every song can be rebuilt exactly from its .mid,
  so nothing is shown beyond a quiet note (no warning for songs that a terminal
  build could rebuild anyway);
* **unprotected and at risk** — an amber line naming the songs whose music
  is only in their .s, with a button to Tools → Song Build Protection.

It only reads the project. It never claims PorySuite can stop a terminal
command: PorySuite is not running when one is typed.
"""
from __future__ import annotations

import logging

from PyQt6.QtCore import QObject, QThread, pyqtSignal
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton

_log = logging.getLogger("SoundEditor.SongGuard")


class _Worker(QObject):
    done = pyqtSignal(object, str)

    def __init__(self, root):
        super().__init__()
        self.root = root

    def run(self):
        try:
            from core.sound.song_guard import assess_risk
            self.done.emit(assess_risk(self.root), self.root)
        except Exception as exc:                       # never break the Sound Editor
            _log.warning("Song protection status check failed: %s", exc)
            self.done.emit(None, self.root)


class SongProtectionStatus(QFrame):
    """One line above the song list. Hidden until the check has answered."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._root = ""
        self._thread = None
        self._worker = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        self._label = QLabel()
        self._label.setWordWrap(True)
        lay.addWidget(self._label, 1)
        self._button = QPushButton("Song Build Protection…")
        self._button.setToolTip("Tools → Song Build Protection: shows every change and asks first.")
        self._button.clicked.connect(self._open_setup)
        lay.addWidget(self._button)
        self.state = ""
        self.hide()

    def refresh(self, project_root: str) -> None:
        self._root = project_root or ""
        if not self._root or self._thread is not None:
            return
        self._thread = QThread(self)
        self._worker = _Worker(self._root)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.done.connect(self._show)
        self._worker.done.connect(self._thread.quit)
        self._thread.finished.connect(self._finished)
        self._thread.start()

    def _finished(self):
        self._thread = None
        self._worker = None

    def show_report(self, rep) -> None:
        """Display a RiskReport (also used directly by tests)."""
        from core.sound import song_guard as G
        if rep is None:
            self.state = ""
            self.hide()
            return
        self.state = rep.state
        muted = "QFrame { color: palette(mid); }"
        self._label.setToolTip("")
        if rep.state == G.RISK_PROTECTED:
            self.setStyleSheet(muted)
            self._label.setText("✓ Song build protection is on. Builds from a command prompt, including "
                                "make clean, check each song before replacing or deleting it.")
            self._button.setText("Details…")
            self._button.show()
        elif rep.state == G.RISK_SAFE:
            self.setStyleSheet(muted)
            self._label.setText("Song build protection is off. That is fine for now: every song can be "
                                "recreated exactly from its MIDI file, so a rebuild or make clean would "
                                "not lose any music.")
            self._label.setToolTip("Song Build Protection (Tools menu) is optional. It matters once a song "
                                   "has changes that exist only in its .s file.")
            self._button.hide()
        elif rep.state == G.RISK_UNCHECKED:
            self.setStyleSheet(muted)
            self._label.setText("Song build protection is off. The songs could not be checked yet ("
                                + (rep.note or "the song converter is not built") + "). Build the "
                                "project once and reopen the Sound Editor.")
            self._button.hide()
        else:
            names = sorted(rep.at_risk)
            shown = ", ".join(names[:6]) + (f" and {len(names) - 6} more" if len(names) > 6 else "")
            self.setStyleSheet("QFrame { background: #3d2e00; color: #ffcc80; border-radius: 4px; }")
            self._label.setText(
                f"⚠ {len(names)} song(s) contain changes that cannot be recreated from their MIDI files: "
                f"{shown}. PorySuite's Make button keeps them, but building from a command prompt — "
                f"including make clean — could permanently remove those changes. PorySuite cannot step "
                f"in when it is not the one running the build. Song Build Protection adds that check to "
                f"the project's own build.")
            self._label.setToolTip("Why each song is listed:\n"
                                   + "\n".join(f"{n}: {rep.at_risk[n]}" for n in names[:30]))
            self._button.setText("Song Build Protection…")
            self._button.show()
        self.show()

    def _show(self, rep, root):
        if root != self._root:                 # another project was opened meanwhile
            self.refresh(self._root)
            return
        self.show_report(rep)

    def _open_setup(self):
        from ui.dialogs.song_guard_dialog import open_song_guard_dialog
        open_song_guard_dialog(self.window(), self._root)
        self.refresh(self._root)
