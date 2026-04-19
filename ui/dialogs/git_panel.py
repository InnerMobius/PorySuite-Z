"""
git_panel.py — PorySuite Git Panel

A single self-contained window that exposes every git operation the app
supports, with plain-English descriptions of what each section does and
why you'd use it.  No git knowledge required.

Usage:
    panel = GitPanel(mainwindow)
    panel.show()   # non-modal — stays open while you work
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from PyQt6.QtCore    import Qt, QTimer
from PyQt6.QtGui     import QFont
from PyQt6.QtWidgets import (
    QDialog, QWidget, QScrollArea,
    QVBoxLayout, QHBoxLayout, QFormLayout,
    QGroupBox, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QListWidget, QListWidgetItem,
    QRadioButton, QButtonGroup, QSizePolicy,
    QFrame, QSplitter,
)

if TYPE_CHECKING:
    from mainwindow import MainWindow


# ── Helpers ────────────────────────────────────────────────────────────────────

def _desc(text: str) -> QLabel:
    """Grey italic description label."""
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setStyleSheet("color: #999; font-style: italic; font-size: 11px;")
    return lbl


def _section_title(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet("font-weight: bold; font-size: 13px;")
    return lbl


def _divider() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet("color: #444;")
    return line


def _status_pill(text: str, colour: str = "#555") -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(
        f"background:{colour}; color:#fff; border-radius:4px;"
        f"padding: 2px 7px; font-size: 11px;"
    )
    return lbl


# ── Main panel ─────────────────────────────────────────────────────────────────

class GitPanel(QDialog):
    """
    The PorySuite Git Panel.

    Opens as a non-modal window so you can keep it visible while working.
    Every section has a plain-English description of what it does.
    """

    def __init__(self, mw: "MainWindow") -> None:
        super().__init__(mw)
        self._mw = mw
        self.setWindowTitle("Git")
        # Wide enough that section descriptions (Push / Commit / Branches)
        # don't clip their left margin off-screen.  Prior 620 min left the
        # first few characters of each paragraph hidden behind the
        # scrollbar; 880 clears every long line in the panel.
        self.setMinimumWidth(880)
        self.setMinimumHeight(600)
        self.resize(960, 820)
        self.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.WindowCloseButtonHint |
            Qt.WindowType.WindowMinimizeButtonHint
        )

        # ── Root layout: scroll area ──────────────────────────────────────────
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        root.addWidget(scroll)

        container = QWidget()
        scroll.setWidget(container)
        self._main = QVBoxLayout(container)
        self._main.setSpacing(12)
        self._main.setContentsMargins(14, 14, 14, 14)

        # Build all sections
        self._build_status_section()
        self._main.addWidget(_divider())
        self._build_pull_section()
        self._main.addWidget(_divider())
        self._build_push_section()
        self._main.addWidget(_divider())
        self._build_commit_section()
        self._main.addWidget(_divider())
        self._build_branches_section()
        self._main.addWidget(_divider())
        self._build_stash_section()
        self._main.addWidget(_divider())
        self._build_history_section()
        self._main.addWidget(_divider())
        self._build_remotes_section()

        self._main.addStretch()

        # Close button at the very bottom
        close_row = QHBoxLayout()
        close_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setFixedWidth(100)
        close_btn.clicked.connect(self.close)
        close_row.addWidget(close_btn)
        root.addLayout(close_row)
        root.setContentsMargins(8, 0, 8, 8)

        # Auto-refresh every 60 s while the panel is visible
        self._timer = QTimer(self)
        self._timer.setInterval(60_000)
        self._timer.timeout.connect(self.refresh)

    # ── show / hide ────────────────────────────────────────────────────────────

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()
        self._timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._timer.stop()

    # ── Full refresh ───────────────────────────────────────────────────────────

    def refresh(self) -> None:
        """Re-read git state and update every widget in the panel."""
        self._refresh_status()
        self._refresh_push()
        self._refresh_commit_files()
        self._refresh_branches()
        self._refresh_stash()
        self._refresh_history()
        self._refresh_remotes()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 1 — Status
    # ══════════════════════════════════════════════════════════════════════════

    def _build_status_section(self):
        row = QHBoxLayout()
        row.setSpacing(10)

        self._status_branch_lbl  = QLabel("Branch: —")
        self._status_branch_lbl.setStyleSheet("font-weight: bold; font-size: 13px;")
        self._status_dirty_lbl   = QLabel("")
        self._status_ahead_lbl   = QLabel("")
        self._status_behind_lbl  = QLabel("")

        refresh_btn = QPushButton("↻  Refresh")
        refresh_btn.setFixedWidth(100)
        refresh_btn.setToolTip("Re-read the current git state.")
        refresh_btn.clicked.connect(self.refresh)

        row.addWidget(self._status_branch_lbl)
        row.addWidget(self._status_dirty_lbl)
        row.addWidget(self._status_ahead_lbl)
        row.addWidget(self._status_behind_lbl)
        row.addStretch()
        row.addWidget(refresh_btn)

        self._main.addLayout(row)

    def _refresh_status(self):
        _, branch = self._mw._git_run("rev-parse", "--abbrev-ref", "HEAD", timeout=5)
        branch = (branch or "").strip()
        self._status_branch_lbl.setText(f"⎇  {branch or '—'}")

        _, dirty_out = self._mw._git_run("status", "--porcelain", timeout=5)
        all_lines   = [l for l in (dirty_out or "").splitlines() if l.strip()]
        tracked     = [l for l in all_lines if not l.startswith("??")]
        untracked   = [l for l in all_lines if l.startswith("??")]
        if tracked:
            self._status_dirty_lbl.setText(f"  ✎ {len(tracked)} modified")
            self._status_dirty_lbl.setStyleSheet("color: #e8a44a; font-size: 11px;")
        elif untracked:
            self._status_dirty_lbl.setText(f"  + {len(untracked)} untracked")
            self._status_dirty_lbl.setStyleSheet("color: #888; font-size: 11px;")
        else:
            self._status_dirty_lbl.setText("  ✓ clean")
            self._status_dirty_lbl.setStyleSheet("color: #7cbb5e; font-size: 11px;")

        _, ab = self._mw._git_run(
            "rev-list", "--left-right", "--count", f"origin/{branch}...HEAD",
            timeout=5,
        )
        self._status_ahead_lbl.setText("")
        self._status_behind_lbl.setText("")
        if ab:
            parts = ab.strip().split()
            if len(parts) == 2:
                try:
                    behind, ahead = int(parts[0]), int(parts[1])
                    if ahead:
                        self._status_ahead_lbl.setText(f"  ↑{ahead} ahead")
                        self._status_ahead_lbl.setStyleSheet("color: #7cbb5e; font-size: 11px;")
                    if behind:
                        self._status_behind_lbl.setText(f"  ↓{behind} behind")
                        self._status_behind_lbl.setStyleSheet("color: #e06c75; font-size: 11px;")
                except ValueError:
                    pass

    # ══════════════════════════════════════════════════════════════════════════
    # Section 2 — Pull
    # ══════════════════════════════════════════════════════════════════════════

    def _build_pull_section(self):
        box = QGroupBox("⬇  Pull")
        box.setStyleSheet("QGroupBox { font-weight: bold; font-size: 13px; }")
        lay = QVBoxLayout(box)
        lay.setSpacing(8)

        lay.addWidget(_desc(
            "Pull downloads code from the internet and replaces your local files "
            "with it — like syncing from the cloud.  "
            "Anything you haven't committed will be lost, so commit or stash first "
            "if you want to keep your work.  Choose where to pull from:"
        ))

        # Radio: upstream vs origin
        self._pull_btn_group = QButtonGroup(self)
        self._pull_radio_upstream = QRadioButton()
        self._pull_radio_origin   = QRadioButton()
        self._pull_radio_upstream.setChecked(True)
        self._pull_btn_group.addButton(self._pull_radio_upstream, 0)
        self._pull_btn_group.addButton(self._pull_radio_origin,   1)

        self._pull_upstream_lbl = QLabel()
        self._pull_origin_lbl   = QLabel()
        for lbl in (self._pull_upstream_lbl, self._pull_origin_lbl):
            lbl.setStyleSheet("font-family: Courier New; font-size: 11px; color: #aaa;")

        row_up = QHBoxLayout()
        row_up.addWidget(self._pull_radio_upstream)
        row_up.addWidget(QLabel(
            "<b>Upstream</b>  — the base project this hack is built on "
            "<span style='color:#777;font-size:10px;'>"
            "(e.g. pret/pokefirered — the clean, unmodified original)</span>"
        ))
        row_up.addStretch()
        row_up.addWidget(self._pull_upstream_lbl)
        lay.addLayout(row_up)

        row_or = QHBoxLayout()
        row_or.addWidget(self._pull_radio_origin)
        row_or.addWidget(QLabel(
            "<b>Origin</b>  — your own online copy "
            "<span style='color:#777;font-size:10px;'>"
            "(your GitHub fork — syncs your work between computers or teammates)</span>"
        ))
        row_or.addStretch()
        row_or.addWidget(self._pull_origin_lbl)
        lay.addLayout(row_or)

        pull_btn = QPushButton("⬇  Pull Now")
        pull_btn.setFixedWidth(130)
        pull_btn.setToolTip(
            "Runs  git fetch <remote>  then  git reset --hard  to the fetched HEAD.\n"
            "Stale auto-generated files (.h headers from JSON) are deleted\n"
            "afterward so make rebuilds them cleanly."
        )
        pull_btn.clicked.connect(self._do_pull)

        btn_row = QHBoxLayout()
        btn_row.addWidget(pull_btn)
        btn_row.addStretch()
        lay.addLayout(btn_row)

        self._main.addWidget(box)

    def _refresh_pull_urls(self):
        upstream = self._mw._git_upstream_url()
        host_up  = upstream.replace("https://github.com/", "").replace(".git", "")
        self._pull_upstream_lbl.setText(host_up)

        _, origin_url = self._mw._git_run("remote", "get-url", "origin", timeout=5)
        host_or = (origin_url or "").strip().replace("https://github.com/", "").replace(".git", "")
        self._pull_origin_lbl.setText(host_or or "(not set)")

    def _do_pull(self):
        use_upstream = self._pull_btn_group.checkedId() == 0
        self._mw._git_pull(use_upstream=use_upstream)

    # ══════════════════════════════════════════════════════════════════════════
    # Section 3 — Push
    # ══════════════════════════════════════════════════════════════════════════

    def _build_push_section(self):
        box = QGroupBox("⬆  Push")
        box.setStyleSheet("QGroupBox { font-weight: bold; font-size: 13px; }")
        lay = QVBoxLayout(box)
        lay.setSpacing(8)

        lay.addWidget(_desc(
            "Push uploads your committed snapshots to your online copy (origin) "
            "so they're backed up on GitHub and visible to anyone you share with.  "
            "Only committed changes are sent — anything you haven't committed yet "
            "stays on your computer only and is not included."
        ))

        self._push_origin_lbl  = QLabel("")
        self._push_ahead_lbl   = QLabel("")
        self._push_origin_lbl.setStyleSheet("font-size: 11px; color: #aaa;")
        self._push_ahead_lbl.setStyleSheet("font-size: 11px; color: #7cbb5e;")

        info_row = QHBoxLayout()
        info_row.addWidget(QLabel("→ origin:"))
        info_row.addWidget(self._push_origin_lbl)
        info_row.addStretch()
        info_row.addWidget(self._push_ahead_lbl)
        lay.addLayout(info_row)

        push_btn = QPushButton("⬆  Push to origin…")
        push_btn.setFixedWidth(160)
        push_btn.setToolTip("Opens the push dialog where you can choose a branch.")
        push_btn.clicked.connect(self._mw._git_push)

        btn_row = QHBoxLayout()
        btn_row.addWidget(push_btn)
        btn_row.addStretch()
        lay.addLayout(btn_row)

        self._main.addWidget(box)

    def _refresh_push(self):
        _, origin_url = self._mw._git_run("remote", "get-url", "origin", timeout=5)
        origin_url = (origin_url or "").strip()
        host = origin_url.replace("https://github.com/", "").replace(".git", "") or "(not set)"
        self._push_origin_lbl.setText(host)

        _, branch = self._mw._git_run("rev-parse", "--abbrev-ref", "HEAD", timeout=5)
        branch = (branch or "").strip()
        _, ahead_out = self._mw._git_run(
            "log", "--oneline", f"origin/{branch}..HEAD", timeout=5
        )
        ahead = [l for l in (ahead_out or "").splitlines() if l.strip()]
        if ahead:
            if branch in ("main", "master"):
                self._push_ahead_lbl.setText(
                    f"⚠ ↑ {len(ahead)} commit(s) on '{branch}' — consider a feature branch"
                )
                self._push_ahead_lbl.setStyleSheet("font-size: 11px; color: #e8a44a;")
            else:
                self._push_ahead_lbl.setText(f"↑ {len(ahead)} commit(s) ready to push")
                self._push_ahead_lbl.setStyleSheet("font-size: 11px; color: #7cbb5e;")
        else:
            self._push_ahead_lbl.setText("Up to date with origin")
            self._push_ahead_lbl.setStyleSheet("font-size: 11px; color: #aaa;")

        # Also refresh the pull URLs here (same remote data)
        self._refresh_pull_urls()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 4 — Commit
    # ══════════════════════════════════════════════════════════════════════════

    def _build_commit_section(self):
        box = QGroupBox("✓  Commit")
        box.setStyleSheet("QGroupBox { font-weight: bold; font-size: 13px; }")
        lay = QVBoxLayout(box)
        lay.setSpacing(8)

        lay.addWidget(_desc(
            "Save a permanent snapshot of your current changes to the local "
            "history.  Think of it like pressing Save in a game — you can always "
            "roll back to any commit.  Commits are local until you Push."
        ))

        # ── Modified / deleted / staged files (tracked by git) ───────────────
        self._commit_tracked_header = QLabel(
            "<b>Modified files</b>  — these are changes to files git already tracks:"
        )
        lay.addWidget(self._commit_tracked_header)

        self._commit_file_list = QListWidget()
        self._commit_file_list.setAlternatingRowColors(True)
        self._commit_file_list.setMaximumHeight(140)
        self._commit_file_list.setToolTip(
            "M = modified (you edited this file)\n"
            "A = added (staged for the first time)\n"
            "D = deleted\n"
            "R = renamed"
        )
        lay.addWidget(self._commit_file_list)

        self._commit_none_lbl = QLabel("<i>No tracked changes — working tree is clean.</i>")
        self._commit_none_lbl.setStyleSheet("color: #888;")
        self._commit_none_lbl.hide()
        lay.addWidget(self._commit_none_lbl)

        # ── Untracked files (new files git has never seen) ────────────────────
        self._commit_untracked_header = QLabel()   # filled in _refresh
        self._commit_untracked_header.setStyleSheet("margin-top: 6px;")
        self._commit_untracked_header.hide()
        lay.addWidget(self._commit_untracked_header)

        self._commit_untracked_note = QLabel(
            "These files are <b>not affected by Pull</b> — git ignores them until "
            "you explicitly add them.  Check the ones you want to save into git."
        )
        self._commit_untracked_note.setWordWrap(True)
        self._commit_untracked_note.setStyleSheet(
            "color: #888; font-size: 11px; font-style: italic;"
        )
        self._commit_untracked_note.hide()
        lay.addWidget(self._commit_untracked_note)

        self._commit_untracked_list = QListWidget()
        self._commit_untracked_list.setAlternatingRowColors(True)
        self._commit_untracked_list.setMaximumHeight(120)
        self._commit_untracked_list.setToolTip(
            "New files that git has never tracked.\n"
            "They survive Pull/Reset because git doesn't manage them yet.\n"
            "Check the ones you want included in your next commit."
        )
        self._commit_untracked_list.hide()
        lay.addWidget(self._commit_untracked_list)

        # ── Commit message ────────────────────────────────────────────────────
        lay.addWidget(QLabel("<b>Commit message</b>  — briefly describe what changed:"))
        self._commit_msg = QPlainTextEdit()
        self._commit_msg.setPlaceholderText(
            "e.g.  Add rival trainer sprites\n"
            "      Rename Bulbasaur → BulbaFrog\n"
            "      Fix wild encounter table for Route 1"
        )
        self._commit_msg.setMaximumHeight(80)
        lay.addWidget(self._commit_msg)

        self._commit_status_lbl = QLabel("")
        self._commit_status_lbl.setStyleSheet("font-size: 11px;")
        lay.addWidget(self._commit_status_lbl)

        commit_btn = QPushButton("✓  Commit")
        commit_btn.setFixedWidth(110)
        commit_btn.setToolTip(
            "Stages every checked file (tracked + untracked) then commits."
        )
        commit_btn.clicked.connect(self._do_commit)

        btn_row = QHBoxLayout()
        btn_row.addWidget(commit_btn)
        btn_row.addStretch()
        lay.addLayout(btn_row)

        self._main.addWidget(box)

    def _refresh_commit_files(self):
        _, status_out = self._mw._git_run("status", "--short", timeout=10)
        all_lines = [l for l in (status_out or "").splitlines() if l.strip()]

        # Split into tracked changes (M/A/D/R/etc.) vs untracked (??)
        tracked   = [l for l in all_lines if not l.startswith("??")]
        untracked = [l for l in all_lines if l.startswith("??")]

        # ── Tracked list ──────────────────────────────────────────────────────
        self._commit_file_list.clear()
        if tracked:
            self._commit_tracked_header.show()
            self._commit_file_list.show()
            self._commit_none_lbl.hide()
            for raw in tracked:
                xy   = raw[:2].strip()
                path = raw[3:].strip()
                item = QListWidgetItem(f"  {xy}   {path}")
                item.setData(256, path)
                item.setCheckState(Qt.CheckState.Checked)
                self._commit_file_list.addItem(item)
        else:
            self._commit_tracked_header.show()
            self._commit_file_list.hide()
            self._commit_none_lbl.show()

        # ── Untracked list ────────────────────────────────────────────────────
        self._commit_untracked_list.clear()
        if untracked:
            n = len(untracked)
            self._commit_untracked_header.setText(
                f"<b>New untracked files ({n})</b>"
                f"  — new files git has never seen before:"
            )
            self._commit_untracked_header.show()
            self._commit_untracked_note.show()
            self._commit_untracked_list.show()
            for raw in untracked:
                path = raw[3:].strip()
                item = QListWidgetItem(f"  ??   {path}")
                item.setData(256, path)
                item.setCheckState(Qt.CheckState.Unchecked)  # opt-in, not opt-out
                self._commit_untracked_list.addItem(item)
        else:
            self._commit_untracked_header.hide()
            self._commit_untracked_note.hide()
            self._commit_untracked_list.hide()

    def _do_commit(self):
        msg = self._commit_msg.toPlainText().strip()
        if not msg:
            self._commit_status_lbl.setText("⚠  Write a commit message first.")
            self._commit_status_lbl.setStyleSheet("color: #e8a44a; font-size: 11px;")
            return

        staged = 0
        # Stage checked tracked files
        for i in range(self._commit_file_list.count()):
            item = self._commit_file_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                self._mw._git_run("add", item.data(256), timeout=10)
                staged += 1
        # Stage checked untracked files (user opted in)
        for i in range(self._commit_untracked_list.count()):
            item = self._commit_untracked_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                self._mw._git_run("add", item.data(256), timeout=10)
                staged += 1

        if staged == 0:
            self._commit_status_lbl.setText("⚠  No files checked.")
            self._commit_status_lbl.setStyleSheet("color: #e8a44a; font-size: 11px;")
            return

        ok, out = self._mw._git_run("commit", "-m", msg, timeout=30)
        if ok:
            self._commit_msg.clear()
            self._commit_status_lbl.setText("✓  Committed successfully.")
            self._commit_status_lbl.setStyleSheet("color: #7cbb5e; font-size: 11px;")
            self._mw._git_refresh_status_bar()
            self.refresh()
        else:
            self._commit_status_lbl.setText(f"✗  {out}")
            self._commit_status_lbl.setStyleSheet("color: #e06c75; font-size: 11px;")

    # ══════════════════════════════════════════════════════════════════════════
    # Section 5 — Branches
    # ══════════════════════════════════════════════════════════════════════════

    def _build_branches_section(self):
        box = QGroupBox("🌿  Branches")
        box.setStyleSheet("QGroupBox { font-weight: bold; font-size: 13px; }")
        lay = QVBoxLayout(box)
        lay.setSpacing(8)

        lay.addWidget(_desc(
            "A branch is a named version of your project — like a save slot.  "
            "\"main\" (or \"master\") is the default one.  "
            "You can create extra branches to try new ideas or work on a feature "
            "without touching main.  Switching branches swaps ALL your files to "
            "that version instantly — nothing is deleted, just swapped."
        ))

        self._branch_list = QListWidget()
        self._branch_list.setAlternatingRowColors(True)
        self._branch_list.setMaximumHeight(150)
        lay.addWidget(self._branch_list)

        btn_row = QHBoxLayout()

        self._branch_switch_btn = QPushButton("⇄  Switch to Branch")
        self._branch_switch_btn.setEnabled(False)
        self._branch_switch_btn.setToolTip(
            "Check out the selected branch.\n"
            "If local files conflict, a dialog will offer to\n"
            "Stash or Discard them — no terminal needed."
        )
        self._branch_switch_btn.clicked.connect(self._do_switch_branch)

        new_branch_btn = QPushButton("＋  New Branch")
        new_branch_btn.setToolTip(
            "Create a new branch starting from the current HEAD\n"
            "and switch to it immediately."
        )
        new_branch_btn.clicked.connect(self._mw._git_new_branch)
        new_branch_btn.clicked.connect(lambda: QTimer.singleShot(500, self.refresh))

        btn_row.addWidget(self._branch_switch_btn)
        btn_row.addWidget(new_branch_btn)
        btn_row.addStretch()
        lay.addLayout(btn_row)

        self._branch_list.currentRowChanged.connect(self._on_branch_select)
        self._main.addWidget(box)

    def _refresh_branches(self):
        _, current_raw = self._mw._git_run(
            "rev-parse", "--abbrev-ref", "HEAD", timeout=5
        )
        current = (current_raw or "").strip()

        _, branches_raw = self._mw._git_run(
            "branch", "--format=%(refname:short)", timeout=5
        )
        branches = [b.strip() for b in (branches_raw or "").splitlines() if b.strip()]

        self._branch_list.clear()
        for b in branches:
            is_cur = b == current
            item = QListWidgetItem(
                ("  ✓  " if is_cur else "       ") + b +
                ("  ← current" if is_cur else "")
            )
            item.setData(256, b)
            item.setData(257, is_cur)
            if is_cur:
                item.setForeground(
                    __import__("PyQt6.QtGui", fromlist=["QColor"]).QColor("#7cbb5e")
                )
            self._branch_list.addItem(item)

    def _on_branch_select(self, row: int):
        item = self._branch_list.item(row)
        is_current = item.data(257) if item else True
        self._branch_switch_btn.setEnabled(item is not None and not is_current)

    def _do_switch_branch(self):
        item = self._branch_list.currentItem()
        if not item:
            return
        branch = item.data(256)
        self._mw._git_checkout_branch(branch)
        QTimer.singleShot(500, self.refresh)

    # ══════════════════════════════════════════════════════════════════════════
    # Section 6 — Stash
    # ══════════════════════════════════════════════════════════════════════════

    def _build_stash_section(self):
        box = QGroupBox("📦  Stash")
        box.setStyleSheet("QGroupBox { font-weight: bold; font-size: 13px; }")
        lay = QVBoxLayout(box)
        lay.setSpacing(8)

        lay.addWidget(_desc(
            "The stash is a temporary holding area for uncommitted changes.  "
            "Use it when you need to quickly set your work aside — for example, "
            "before pulling an update — and want to restore it afterward without "
            "making a permanent commit."
        ))

        self._stash_count_lbl = QLabel("Stashed entries: 0")
        self._stash_count_lbl.setStyleSheet("font-size: 11px;")
        lay.addWidget(self._stash_count_lbl)

        self._stash_list = QListWidget()
        self._stash_list.setMaximumHeight(80)
        self._stash_list.setStyleSheet("font-family: Courier New; font-size: 10px;")
        self._stash_list.hide()
        lay.addWidget(self._stash_list)

        btn_row = QHBoxLayout()

        stash_btn = QPushButton("📦  Stash Changes")
        stash_btn.setToolTip(
            "Saves ALL uncommitted changes (including untracked files) to the stash.\n"
            "Your working tree is left clean so you can pull/build/test."
        )
        stash_btn.clicked.connect(self._do_stash)

        pop_btn = QPushButton("⤴  Restore Latest Stash")
        pop_btn.setToolTip(
            "Restores the most recently stashed changes back into your working tree.\n"
            "The stash entry is removed once it's been applied."
        )
        pop_btn.clicked.connect(self._do_pop_stash)

        btn_row.addWidget(stash_btn)
        btn_row.addWidget(pop_btn)
        btn_row.addStretch()
        lay.addLayout(btn_row)

        self._main.addWidget(box)

    def _refresh_stash(self):
        _, stash_out = self._mw._git_run("stash", "list", timeout=5)
        entries = [l for l in (stash_out or "").splitlines() if l.strip()]
        n = len(entries)
        self._stash_count_lbl.setText(
            f"Stashed entries: {n}" if n else "Stashed entries: 0  — nothing saved"
        )
        self._stash_list.clear()
        if entries:
            self._stash_list.show()
            for e in entries:
                self._stash_list.addItem(e)
        else:
            self._stash_list.hide()

    def _do_stash(self):
        self._mw._git_stash()
        QTimer.singleShot(500, self.refresh)

    def _do_pop_stash(self):
        self._mw._git_pop_stash()
        QTimer.singleShot(500, self.refresh)

    # ══════════════════════════════════════════════════════════════════════════
    # Section 7 — History
    # ══════════════════════════════════════════════════════════════════════════

    def _build_history_section(self):
        box = QGroupBox("📋  History")
        box.setStyleSheet("QGroupBox { font-weight: bold; font-size: 13px; }")
        lay = QVBoxLayout(box)
        lay.setSpacing(8)

        lay.addWidget(_desc(
            "A record of every saved snapshot (commit) in your project, newest "
            "first.  Each entry shows the short ID, date, and summary message.  "
            "Use this to see what changed and when."
        ))

        self._history_list = QListWidget()
        self._history_list.setMaximumHeight(160)
        self._history_list.setFont(QFont("Courier New", 9))
        self._history_list.setAlternatingRowColors(True)
        self._history_list.setToolTip(
            "Double-click a commit to copy its hash to the clipboard."
        )
        self._history_list.itemDoubleClicked.connect(self._copy_hash)
        lay.addWidget(self._history_list)

        view_btn = QPushButton("View Full Log…")
        view_btn.setFixedWidth(130)
        view_btn.setToolTip("Opens a scrollable list of the last 30 commits.")
        view_btn.clicked.connect(self._mw._git_view_log)

        btn_row = QHBoxLayout()
        btn_row.addWidget(view_btn)
        btn_row.addStretch()
        lay.addLayout(btn_row)

        self._main.addWidget(box)

    def _refresh_history(self):
        _, log_out = self._mw._git_run(
            "log", "--format=%h  %ad  %s", "--date=short", "-10",
            timeout=10,
        )
        self._history_list.clear()
        for line in (log_out or "").splitlines():
            if line.strip():
                self._history_list.addItem(line)

    def _copy_hash(self, item: QListWidgetItem):
        import re
        m = re.match(r"^([0-9a-f]+)", item.text().strip())
        if m:
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText(m.group(1))

    # ══════════════════════════════════════════════════════════════════════════
    # Section 8 — Remotes
    # ══════════════════════════════════════════════════════════════════════════

    def switch_to_page(self, page: str):
        """Scroll to a named section (e.g. 'remotes')."""
        widget = {"remotes": getattr(self, "_remotes_box", None)}.get(page)
        if widget:
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(50, lambda: self.findChild(
                QScrollArea).ensureWidgetVisible(widget, 0, 50))

    def _build_remotes_section(self):
        box = QGroupBox("⚙  Remotes")
        self._remotes_box = box
        box.setStyleSheet("QGroupBox { font-weight: bold; font-size: 13px; }")
        lay = QVBoxLayout(box)
        lay.setSpacing(10)

        lay.addWidget(_desc(
            "Remotes are the online addresses your project syncs with.  "
            "Origin is YOUR copy on GitHub — the one you push to and back up.  "
            "Upstream is the ORIGINAL project yours is based on — the one you "
            "pull clean updates from when the base game gets fixes.  "
            "You need both if you're making a hack: origin for your own work, "
            "upstream to grab new changes from the source."
        ))

        # ── Origin ────────────────────────────────────────────────────────────
        grp_or = QGroupBox("Origin  — your personal GitHub copy  (where Push sends your commits)")
        grp_or.setStyleSheet(
            "QGroupBox { font-size: 11px; font-style: italic; color: #aaa; }"
        )
        form_or = QFormLayout(grp_or)
        self._remote_origin_edit = QLineEdit()
        self._remote_origin_edit.setPlaceholderText(
            "https://github.com/yourname/pokefirered.git"
        )
        form_or.addRow("URL:", self._remote_origin_edit)
        self._origin_status_lbl = QLabel("")
        self._origin_status_lbl.setStyleSheet("font-size: 11px;")

        apply_or_btn = QPushButton("Apply Origin")
        apply_or_btn.setFixedWidth(120)
        apply_or_btn.setToolTip(
            "Runs  git remote set-url origin <URL>\n"
            "Changes where Push sends your commits."
        )
        apply_or_btn.clicked.connect(self._do_apply_origin)

        or_btns = QHBoxLayout()
        or_btns.addWidget(apply_or_btn)
        or_btns.addWidget(self._origin_status_lbl)
        or_btns.addStretch()
        form_or.addRow("", or_btns)
        lay.addWidget(grp_or)

        # ── Upstream ──────────────────────────────────────────────────────────
        grp_up = QGroupBox(
            "Upstream  — the base project yours is built on  (where Pull → Upstream pulls from)"
        )
        grp_up.setStyleSheet(
            "QGroupBox { font-size: 11px; font-style: italic; color: #aaa; }"
        )
        form_up = QFormLayout(grp_up)
        self._remote_upstream_edit = QLineEdit()
        self._remote_upstream_edit.setPlaceholderText(
            "https://github.com/pret/pokefirered.git"
        )
        form_up.addRow("URL:", self._remote_upstream_edit)

        up_note = QLabel(
            "This URL is only used by Pull → Upstream above.  "
            "It is not permanently linked to your project, so it will never "
            "interfere with your normal Push or Pull from origin workflow."
        )
        up_note.setWordWrap(True)
        up_note.setStyleSheet("color: #777; font-size: 10px; font-style: italic;")
        form_up.addRow("", up_note)

        self._upstream_status_lbl = QLabel("")
        self._upstream_status_lbl.setStyleSheet("font-size: 11px;")

        save_up_btn = QPushButton("Save Upstream URL")
        save_up_btn.setFixedWidth(150)
        save_up_btn.setToolTip(
            "Saves this URL to app settings.  It will be used next time\n"
            "you click  Pull → Pull from Upstream."
        )
        save_up_btn.clicked.connect(self._do_save_upstream)

        up_btns = QHBoxLayout()
        up_btns.addWidget(save_up_btn)
        up_btns.addWidget(self._upstream_status_lbl)
        up_btns.addStretch()
        form_up.addRow("", up_btns)
        lay.addWidget(grp_up)

        # ── Saved remotes ─────────────────────────────────────────────────────
        grp_saved = QGroupBox("Saved Remotes  — quick-switch your origin")
        grp_saved.setStyleSheet(
            "QGroupBox { font-size: 11px; font-style: italic; color: #aaa; }"
        )
        saved_lay = QVBoxLayout(grp_saved)

        saved_lay.addWidget(_desc(
            "A handy list of URLs you've used before.  Select one and click "
            "\"Set as Active Origin\" to instantly switch where your pushes go — "
            "useful if you work with multiple forks or team repos."
        ))

        self._saved_list = QListWidget()
        self._saved_list.setAlternatingRowColors(True)
        self._saved_list.setMaximumHeight(120)
        saved_lay.addWidget(self._saved_list)

        sl_btns = QHBoxLayout()
        self._set_origin_btn = QPushButton("Set as Active Origin")
        self._set_origin_btn.setEnabled(False)
        self._set_origin_btn.setToolTip(
            "Makes the selected URL the active origin, "
            "running  git remote set-url origin <selected>."
        )
        self._set_origin_btn.clicked.connect(self._do_set_saved_as_origin)
        remove_saved_btn = QPushButton("Remove from List")
        remove_saved_btn.clicked.connect(self._do_remove_saved)
        sl_btns.addWidget(self._set_origin_btn)
        sl_btns.addWidget(remove_saved_btn)
        sl_btns.addStretch()
        saved_lay.addLayout(sl_btns)

        # Add-to-list form
        add_form = QFormLayout()
        self._saved_name_edit = QLineEdit()
        self._saved_name_edit.setPlaceholderText("e.g.  my fork,  team repo,  backup")
        self._saved_url_edit  = QLineEdit()
        self._saved_url_edit.setPlaceholderText(
            "https://github.com/yourname/pokefirered.git"
        )
        add_form.addRow("Name:", self._saved_name_edit)
        add_form.addRow("URL:", self._saved_url_edit)

        self._add_saved_status = QLabel("")
        self._add_saved_status.setStyleSheet("font-size: 11px;")
        add_btn = QPushButton("Add to List")
        add_btn.setFixedWidth(110)
        add_btn.clicked.connect(self._do_add_saved)
        add_row = QHBoxLayout()
        add_row.addWidget(add_btn)
        add_row.addWidget(self._add_saved_status)
        add_row.addStretch()

        saved_lay.addSpacing(6)
        saved_lay.addLayout(add_form)
        saved_lay.addLayout(add_row)

        lay.addWidget(grp_saved)
        self._saved_list.currentRowChanged.connect(
            lambda _: self._set_origin_btn.setEnabled(
                self._saved_list.currentItem() is not None
            )
        )
        self._saved_list.currentItemChanged.connect(self._on_saved_select)

        self._main.addWidget(box)

    def _refresh_remotes(self):
        _, origin_url = self._mw._git_run("remote", "get-url", "origin", timeout=5)
        self._remote_origin_edit.setText((origin_url or "").strip())

        upstream = self._mw._git_upstream_url()
        self._remote_upstream_edit.setText(upstream)

        # Refresh saved list
        saved = self._mw._load_saved_remotes()
        _, current_origin = self._mw._git_run("remote", "get-url", "origin", timeout=5)
        current_origin = (current_origin or "").strip()

        self._saved_list.clear()
        for r in saved:
            active = r["url"] == current_origin
            item = QListWidgetItem(
                ("  ✓  " if active else "       ") +
                f"{r['name']}   —   {r['url']}" +
                ("  [active]" if active else "")
            )
            item.setData(256, r)
            if active:
                item.setForeground(
                    __import__("PyQt6.QtGui", fromlist=["QColor"]).QColor("#7cbb5e")
                )
            self._saved_list.addItem(item)

    def _do_apply_origin(self):
        new_url = self._remote_origin_edit.text().strip()
        if not new_url:
            self._origin_status_lbl.setText("⚠  URL is empty.")
            return
        _, existing = self._mw._git_run("remote", "get-url", "origin", timeout=5)
        if existing:
            ok, msg = self._mw._git_run("remote", "set-url", "origin", new_url, timeout=10)
        else:
            ok, msg = self._mw._git_run("remote", "add", "origin", new_url, timeout=10)
        if ok:
            self._origin_status_lbl.setText("✓  Applied")
            self._origin_status_lbl.setStyleSheet("color: #7cbb5e; font-size: 11px;")
            saved = self._mw._load_saved_remotes()
            if not any(r["url"] == new_url for r in saved):
                saved.insert(0, {"name": "origin", "url": new_url})
                self._mw._save_saved_remotes(saved)
            self.refresh()
        else:
            self._origin_status_lbl.setText(f"✗  {msg[:60]}")
            self._origin_status_lbl.setStyleSheet("color: #e06c75; font-size: 11px;")

    def _do_save_upstream(self):
        new_url = self._remote_upstream_edit.text().strip()
        if not new_url:
            self._upstream_status_lbl.setText("⚠  URL is empty.")
            return
        self._mw._git_save_upstream_url(new_url)
        host = new_url.replace("https://github.com/", "").replace(".git", "")
        act = getattr(self._mw, "_pull_upstream_action", None)
        if act:
            act.setText(f"⬇  Pull from Upstream  ({host})")
        self._upstream_status_lbl.setText("✓  Saved")
        self._upstream_status_lbl.setStyleSheet("color: #7cbb5e; font-size: 11px;")
        self._refresh_pull_urls()

    def _on_saved_select(self, cur, _prev):
        if cur:
            r = cur.data(256)
            self._saved_url_edit.setText(r["url"])
            self._saved_name_edit.setText(r["name"])

    def _do_set_saved_as_origin(self):
        item = self._saved_list.currentItem()
        if not item:
            return
        r = item.data(256)
        _, existing = self._mw._git_run("remote", "get-url", "origin", timeout=5)
        if existing:
            ok, msg = self._mw._git_run("remote", "set-url", "origin", r["url"], timeout=10)
        else:
            ok, msg = self._mw._git_run("remote", "add", "origin", r["url"], timeout=10)
        if ok:
            self._remote_origin_edit.setText(r["url"])
            self._mw._git_refresh_status_bar()
            self.refresh()
        else:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Set Origin", f"git error:\n{msg}")

    def _do_remove_saved(self):
        item = self._saved_list.currentItem()
        if not item:
            return
        r = item.data(256)
        saved = self._mw._load_saved_remotes()
        saved[:] = [x for x in saved if x["url"] != r["url"]]
        self._mw._save_saved_remotes(saved)
        self._refresh_remotes()

    def _do_add_saved(self):
        name = self._saved_name_edit.text().strip()
        url  = self._saved_url_edit.text().strip()
        if not name or not url:
            self._add_saved_status.setText("⚠  Need both name and URL.")
            self._add_saved_status.setStyleSheet("color: #e8a44a; font-size: 11px;")
            return
        saved = self._mw._load_saved_remotes()
        for r in saved:
            if r["url"] == url:
                r["name"] = name
                break
        else:
            saved.append({"name": name, "url": url})
        self._mw._save_saved_remotes(saved)
        self._saved_name_edit.clear()
        self._saved_url_edit.clear()
        self._add_saved_status.setText("✓  Added")
        self._add_saved_status.setStyleSheet("color: #7cbb5e; font-size: 11px;")
        self._refresh_remotes()
