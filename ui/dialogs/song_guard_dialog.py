"""Tools → Song Build Protection: install or remove the opt-in build guard
(core/sound/song_guard.py) for the open project.

The window explains in plain English what protection is, why someone would
want it and what installing or removing it does; the exact file changes are
one click away under "Technical details" for advanced users. Nothing is written
until the owner presses Install / Remove and confirms. Both back the build
files up into PorySuite's cache first.
"""
from __future__ import annotations

import logging
import os

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (QApplication, QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel,
                             QMessageBox, QPlainTextEdit, QProgressDialog, QPushButton, QScrollArea,
                             QToolButton, QVBoxLayout, QWidget)

_log = logging.getLogger("SoundEditor.SongGuard")

# ── Plain-English text (kept here so every message says the same thing) ─────

WHAT_IS_IT = (
    "<p>When you build your game, the game's build tools may <b>recreate song files from MIDI "
    "files</b>. Each song has a <i>.mid</i> (the MIDI) and a <i>.s</i> (the assembled music file the "
    "game actually uses). If a song has changes that exist only in its .s, a rebuild can "
    "<b>overwrite or delete them</b>.</p>"
    "<p><b>Song Build Protection</b> checks whether a song can safely be rebuilt before the build "
    "replaces it. It also works when you build from a command prompt (<tt>make</tt>, "
    "<tt>make modern</tt>, <tt>make clean</tt>), on any computer, even when PorySuite is closed.</p>")

WHY = (
    "<p><b>Why you might want it</b></p><ul>"
    "<li>You edit songs in PorySuite's piano roll or by hand, and sometimes build from a command "
    "prompt.</li>"
    "<li>You use Git (pulls, branch switches): Git changes file dates, and the build tools judge by "
    "dates.</li>"
    "<li>You run <tt>make clean</tt>: without protection it deletes every .s file, including songs "
    "whose only full copy is their .s.</li></ul>"
    "<p>It is <b>optional</b>. Projects without it keep working as before, and PorySuite's own Make "
    "button already checks songs before it builds.</p>")

WHAT_INSTALL_DOES = (
    "<p><b>What installing does</b></p><ul>"
    "<li>Adds two small files to the project and changes three lines of its build files "
    "(<tt>Makefile</tt>, <tt>audio_rules.mk</tt>). A backup is taken first.</li>"
    "<li>Adds one comment line — a <b>protection record</b> — to the top of every song .s. It records "
    "which MIDI the song was built from; it does not change the music.</li>"
    "<li><b>Git will list those song files as changed.</b> That is intended: commit them (with the build "
    "file changes) so protection also works on other computers.</li>"
    "<li>From then on a build rebuilds a song whose MIDI changed, keeps a .s holding edits of its own, "
    "and <b>stops with a message</b> when both changed, instead of guessing.</li>"
    "<li>It cannot bring back music that was never saved or committed.</li></ul>")

ABOUT_REMOVING = (
    "<p><b>Removing it later</b> restores your original build rules exactly. The protection records "
    "already added to song files <b>stay</b> (they are harmless comments), but they are no longer "
    "enforced during terminal builds.</p>")

TRACK_LABEL = "Include generated song files in Git"
TRACK_HELP = (
    "<span style='color:palette(mid)'>Git is the system that saves and shares your project's changes. "
    "With this on, the assembled song files (.s) are included in your Git history, so you can review "
    "them or get them back. They may show up as changes when songs are rebuilt. This changes one line "
    "of <tt>.gitignore</tt>. It does not replace Song Build Protection, and switching it off later does "
    "not remove files Git already tracks.</span>")

REMOVE_CONFIRM = (
    "Removing protection restores your original build rules and deletes song_guard.sh and "
    "song_guard.mk.\n\n"
    "The protection records already added to {n} song file(s) will remain, but they will no longer be "
    "enforced during terminal builds. Removal does not restore every song file to how it was before "
    "installing.\n\n"
    "Afterwards, building from a command prompt judges songs by file dates again.\n\n"
    "Remove protection now?")


def _section(html: str) -> QLabel:
    lab = QLabel(html)
    lab.setWordWrap(True)
    lab.setTextFormat(Qt.TextFormat.RichText)
    lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lab


def _song_count(project_dir: str) -> int:
    md = os.path.join(project_dir, "sound", "songs", "midi")
    try:
        return sum(1 for n in os.listdir(md) if n.endswith(".s"))
    except OSError:
        return 0


class SongGuardDialog(QDialog):
    def __init__(self, project_dir: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Song Build Protection")
        self.project_dir = project_dir
        self.setMinimumSize(520, 360)
        self.resize(780, 680)
        outer = QVBoxLayout(self)

        name = os.path.basename(os.path.normpath(project_dir)) or project_dir
        head = _section(f"<h3 style='margin:0'>Song Build Protection</h3>"
                        f"<span style='color:palette(mid)'>Project: <b>{name}</b> — {project_dir}</span>")
        outer.addWidget(head)
        self._status = _section("")
        self._status.setFrameShape(QFrame.Shape.StyledPanel)
        self._status.setContentsMargins(8, 6, 8, 6)
        outer.addWidget(self._status)

        # Everything explanatory scrolls, so the window works on small screens.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        # Text wraps to the window's width; only the technical diff scrolls sideways.
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 8, 0)
        lay.addWidget(_section(WHAT_IS_IT))
        lay.addWidget(_section(WHY))
        self._install_text = _section(WHAT_INSTALL_DOES)
        lay.addWidget(self._install_text)
        lay.addWidget(_section(ABOUT_REMOVING))

        self._track = QCheckBox(TRACK_LABEL)
        self._track.toggled.connect(self._refresh_diff)
        self._track_help = _section(TRACK_HELP)
        self._track_help.setContentsMargins(22, 0, 0, 0)
        lay.addWidget(self._track)
        lay.addWidget(self._track_help)

        self._details_btn = QToolButton()
        self._details_btn.setCheckable(True)
        self._details_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._details_btn.setArrowType(Qt.ArrowType.RightArrow)
        self._details_btn.setText("Technical details: the exact file changes")
        self._details_btn.setToolTip("Shows the exact lines that will be added, changed or removed.")
        self._details_btn.toggled.connect(self._toggle_details)
        lay.addWidget(self._details_btn)
        self._diff = QPlainTextEdit()
        self._diff.setReadOnly(True)
        f = QFont("Consolas")
        f.setStyleHint(QFont.StyleHint.Monospace)
        self._diff.setFont(f)
        self._diff.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._diff.setMinimumHeight(220)
        self._diff.setVisible(False)
        lay.addWidget(self._diff)
        lay.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        row = QHBoxLayout()
        self._install = QPushButton("Install protection…")
        self._install.setToolTip("Shows a short summary and asks before changing anything.")
        self._install.clicked.connect(self._on_install)
        self._remove = QPushButton("Remove protection…")
        self._remove.setToolTip("Restores the original build rules. Asks first and says what stays.")
        self._remove.clicked.connect(self._on_remove)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        row.addWidget(self._install)
        row.addWidget(self._remove)
        row.addStretch(1)
        row.addWidget(close)
        outer.addLayout(row)
        self._plan = None
        self._refresh()

    # ── state ───────────────────────────────────────────────────────────
    def _toggle_details(self, on: bool):
        self._details_btn.setArrowType(Qt.ArrowType.DownArrow if on else Qt.ArrowType.RightArrow)
        self._diff.setVisible(on)

    def _set_status(self, html: str, color: str):
        self._status.setText(f"<span style='color:{color}'>{html}</span>")

    def _refresh(self):
        from core.sound import song_guard as G
        st = G.detect(self.project_dir)
        self._plan = None
        self._install.setEnabled(False)
        self._remove.setEnabled(False)
        self._track.setVisible(False)
        self._track_help.setVisible(False)
        if st.state == G.INSTALLED:
            note = "" if st.script_current else (
                " The installed version differs from the one this PorySuite ships; remove and install "
                "again to update it.")
            self._set_status("<b>Protection is on in this project.</b> Builds from a command prompt "
                             "check each song before replacing or deleting it." + note, "#4ec9b0")
            self._remove.setEnabled(True)
            self._install_text.setVisible(False)
            self._details_btn.setText("Technical details: what removing would change")
            self._show_plan(G.plan_uninstall)
            return
        self._install_text.setVisible(True)
        if st.state == G.PARTIAL:
            self._set_status("<b>Protection is only partly installed</b> ("
                             + "; ".join(st.problems) + "). Remove it to put the build back as it "
                             "was, then install again if you want it.", "#f44747")
            self._remove.setEnabled(True)
            self._details_btn.setText("Technical details: what removing would change")
            self._show_plan(G.plan_uninstall)
            return
        if st.state == G.UNSUPPORTED:
            self._set_status("<b>This project's build files are set up in a way the setup does not "
                             "recognise</b>, so it will not guess and changes nothing:<ul>"
                             + "".join(f"<li>{r}</li>" for r in st.problems) + "</ul>", "#ffb74d")
            self._diff.setPlainText("")
            return
        self._set_status("<b>Protection is not installed.</b> Nothing is changed until you press "
                         "Install and confirm.", "palette(text)")
        has_track = G.plan_track_s(self.project_dir) is not None
        self._track.setVisible(has_track)
        self._track_help.setVisible(has_track)
        self._install.setEnabled(True)
        self._details_btn.setText("Technical details: the exact file changes")
        self._show_plan(G.plan_install)

    def _show_plan(self, planner):
        from core.sound import song_guard as G
        try:
            self._plan = planner(self.project_dir)
        except G.GuardRefused as exc:
            self._diff.setPlainText("\n".join(exc.reasons))
            self._install.setEnabled(False)
            return
        self._refresh_diff()

    def _refresh_diff(self, *_):
        from core.sound import song_guard as G
        if self._plan is None:
            return
        text = self._plan.diff()
        if self._plan.action == "install" and self._track.isChecked():
            gi = G.plan_track_s(self.project_dir)
            if gi is not None:
                text += "\n" + gi.diff()
        if self._plan.action == "install":
            text += ("\n# And every song .s gets one comment line at the top, for example:\n# "
                     + G.stamp_line("<sha1 of the .mid>", "<fingerprint>", "<its midi.cfg flags>") + "\n")
        self._diff.setPlainText(text)

    # ── actions ─────────────────────────────────────────────────────────
    def _on_install(self):
        from core.sound import song_guard as G
        if self._plan is None or self._plan.action != "install":
            return
        plan = self._plan
        if self._track.isChecked():
            gi = G.plan_track_s(self.project_dir)
            if gi is not None:
                plan.changes.append(gi)
        n = _song_count(self.project_dir)
        name = os.path.basename(os.path.normpath(self.project_dir))
        ok = QMessageBox.question(
            self, "Install song build protection",
            f"Install song build protection in “{name}”?\n\n"
            f"• Changes {len(plan.changes)} project file(s): {', '.join(plan.names())}"
            f" (backed up first).\n"
            f"• Adds a protection record to {n} song file(s). Git will list them as changed — commit "
            f"them so protection works on other computers too.\n"
            f"• You can remove it later; the records stay in the song files.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if ok != QMessageBox.StandardButton.Yes:
            if self._track.isChecked():
                self._refresh()
            return
        prog = QProgressDialog("Checking songs…", "", 0, 0, self)
        prog.setWindowTitle("Song Build Protection")
        prog.setCancelButton(None)
        prog.setMinimumDuration(300)
        prog.setWindowModality(Qt.WindowModality.WindowModal)

        def tick(done, total):
            prog.setMaximum(total)
            prog.setValue(done)
            QApplication.processEvents()

        try:
            rep = G.install_with_records(self.project_dir, plan, progress=tick)
        finally:
            prog.close()
        self._report(rep)
        self._refresh()

    def _report(self, rep):
        res = rep.result
        if not res.ok:
            QMessageBox.critical(self, "Song build protection was not installed",
                                 "\n".join(res.messages) or "Unknown problem.")
            return
        lines = ["Protection is on. Builds from a command prompt now check each song first.",
                 f"{rep.stamped} song(s) got their protection record. Commit the changed files "
                 f"(songs and build files) when you are ready.",
                 f"Backup of the original build files:\n{res.backup_dir}"]
        if rep.unsettled:
            lines.append(f"\n{len(rep.unsettled)} song(s) have changes in both their MIDI file and "
                         f"their .s, or PorySuite cannot tell which is newer. The next build stops on "
                         f"each one and asks which version to keep (nothing is changed until you "
                         f"choose):")
            for label, why in sorted(rep.unsettled.items())[:12]:
                lines.append(f"  • {label}: {why}")
            if len(rep.unsettled) > 12:
                lines.append(f"  … and {len(rep.unsettled) - 12} more (see the sound editor log)")
        for e in rep.errors:
            lines.append(f"Warning: {e}")
        for label, why in sorted(rep.unsettled.items()):
            _log.info("Song guard setup: %s not recorded — %s", label, why)
        QMessageBox.information(self, "Song build protection", "\n".join(lines))

    def _on_remove(self):
        from core.sound import song_guard as G
        try:
            plan = G.plan_uninstall(self.project_dir)
        except G.GuardRefused as exc:
            QMessageBox.information(self, "Song build protection", "\n".join(exc.reasons))
            return
        md = os.path.join(self.project_dir, "sound", "songs", "midi")
        n = 0
        try:
            for fn in os.listdir(md):
                if fn.endswith(".s"):
                    with open(os.path.join(md, fn), "rb") as fh:
                        n += G.parse_stamp(fh.read(256)) is not None
        except OSError:
            pass
        ok = QMessageBox.question(
            self, "Remove song build protection", REMOVE_CONFIRM.format(n=n),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if ok != QMessageBox.StandardButton.Yes:
            return
        res = G.apply(self.project_dir, plan)
        if res.ok:
            QMessageBox.information(self, "Song build protection",
                                    "Protection was removed and the original build rules are back. "
                                    "The protection records in song files remain and are no longer "
                                    f"enforced.\n\nBackup of the build files as they were:\n{res.backup_dir}")
        else:
            QMessageBox.critical(self, "Song build protection was not removed",
                                 "\n".join(res.messages) or "Unknown problem.")
        self._refresh()


def open_song_guard_dialog(parent, project_dir: str) -> None:
    if not project_dir or not os.path.isdir(project_dir):
        QMessageBox.warning(parent, "No Project", "Open a project first.")
        return
    SongGuardDialog(project_dir, parent).exec()
