"""
EVENTide Main Window

Map/world management and event editing for pokefirered decomp projects.
Tabs: Maps (+warps), Layouts (+tilesets), Region Map, Event Editor.
"""

import os
import json
import subprocess

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QFont, QKeySequence
from PyQt6.QtWidgets import (
    QMainWindow,
    QTabWidget,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QTextEdit,
    QSplitter,
    QLabel,
    QMessageBox,
    QMenu,
)

from suppress_dialog import maybe_exec


class EventideMainWindow(QMainWindow):
    """Main window for the EVENTide application."""

    # Emitted when the user wants to open the same project in PorySuite
    open_in_porysuite_signal = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("EVENTide[*]")
        self.resize(1200, 800)
        self.project_info = None

        # ── Central widget: tabs + log splitter ──────────────────────────────
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Orientation.Vertical)
        root_layout.addWidget(splitter)

        # ── Tab widget ───────────────────────────────────────────────────────
        self.tabs = QTabWidget()
        splitter.addWidget(self.tabs)

        # Placeholder tabs — replaced with real widgets once backends are ported
        from eventide.ui.maps_tab import MapsTab
        from eventide.ui.layouts_tab import LayoutsTab
        from eventide.ui.region_map_tab import RegionMapTab
        from eventide.ui.event_editor_tab import EventEditorTab

        self.maps_tab = MapsTab(self)
        self.layouts_tab = LayoutsTab(self)
        self.region_map_tab = RegionMapTab(self)
        self.event_editor_tab = EventEditorTab(self)

        self.tabs.addTab(self.event_editor_tab, "Event Editor")
        self.tabs.addTab(self.maps_tab, "Maps")
        self.tabs.addTab(self.layouts_tab, "Layouts && Tilesets")
        self.tabs.addTab(self.region_map_tab, "Region Map")

        # ── Inter-tab refresh signals ────────────────────────────────────────
        # Maps changes (rename, section edit, delete) affect region map + layouts
        self.maps_tab.data_changed.connect(self._on_maps_changed)
        # Layout changes (rename, delete) affect maps tab (layout column)
        self.layouts_tab.data_changed.connect(self._on_layouts_changed)
        # Region map section changes affect maps tab (section column)
        self.region_map_tab.data_changed.connect(self._on_region_map_changed)

        # ── Dirty tracking (unsaved changes) ────────────────────────────────
        # All tabs that modify data mark the window as modified.
        # Maps/Layouts/Region Map tabs save immediately on action, so they
        # don't need dirty tracking.  Event Editor saves are manual.
        self.event_editor_tab.data_changed.connect(
            lambda: self.setWindowModified(True))

        # ── Log panel ────────────────────────────────────────────────────────
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(160)
        self.log.setFont(QFont("Courier New", 9))
        self.log.setPlaceholderText("Log output will appear here...")
        splitter.addWidget(self.log)

        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)

        # ── Menu bar ────────────────────────────────────────────────────────
        self._build_menus()

        # ── Status bar ───────────────────────────────────────────────────────
        self._git_bar_label = QLabel("")
        self._git_bar_label.setObjectName("git_status_bar")
        self._git_bar_label.setStyleSheet(
            "#git_status_bar { color: palette(dark); margin-right: 8px; }")
        self._git_bar_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self._git_bar_label.mousePressEvent = lambda _e: self._open_git_panel()
        self.statusBar().addPermanentWidget(self._git_bar_label)

    # ═════════════════════════════════════════════════════════════════════════
    # Menu bar
    # ═════════════════════════════════════════════════════════════════════════

    def _build_menus(self):
        menubar = self.menuBar()

        # ── File menu ────────────────────────────────────────────────────────
        file_menu = menubar.addMenu("&File")

        self._open_porysuite_action = QAction("Open in PorySuite", self)
        self._open_porysuite_action.setToolTip(
            "Open this project in PorySuite for data editing\n"
            "(Pokemon, items, moves, trainers, etc.)"
        )
        self._open_porysuite_action.setEnabled(False)
        self._open_porysuite_action.triggered.connect(self._open_in_porysuite)
        file_menu.addAction(self._open_porysuite_action)

        file_menu.addSeparator()

        self._refresh_action = QAction("Refresh from Disk", self)
        self._refresh_action.setShortcut("Ctrl+R")
        self._refresh_action.setEnabled(False)
        self._refresh_action.triggered.connect(self._refresh_project)
        file_menu.addAction(self._refresh_action)

        file_menu.addSeparator()

        quit_action = QAction("Quit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        # ── Git menu ────────────────────────────────────────────────────────
        self._git_menu = menubar.addMenu("&Git")

        self._git_panel_action = QAction("Git Panel...", self)
        self._git_panel_action.setShortcut("Ctrl+Shift+G")
        self._git_panel_action.setToolTip(
            "Open the Git panel — pull, push, commit, branches, stash, history,\n"
            "and remote configuration, all in one window with full descriptions."
        )
        self._git_panel_action.setEnabled(False)
        self._git_panel_action.triggered.connect(self._open_git_panel)
        self._git_menu.addAction(self._git_panel_action)

        self._git_menu.addSeparator()

        self._pull_upstream_action = QAction("Pull from Upstream", self)
        self._pull_upstream_action.setShortcut("Ctrl+Shift+L")
        self._pull_upstream_action.setEnabled(False)
        self._pull_upstream_action.triggered.connect(
            lambda: self._git_pull(use_upstream=True)
        )
        self._git_menu.addAction(self._pull_upstream_action)

        self._pull_origin_action = QAction("Pull from origin", self)
        self._pull_origin_action.setEnabled(False)
        self._pull_origin_action.triggered.connect(self._git_pull)
        self._git_menu.addAction(self._pull_origin_action)

        self._push_action = QAction("Push to origin", self)
        self._push_action.setShortcut("Ctrl+Shift+U")
        self._push_action.setEnabled(False)
        self._push_action.triggered.connect(self._git_push)
        self._git_menu.addAction(self._push_action)

        self._git_commit_action = QAction("Commit...", self)
        self._git_commit_action.setShortcut("Ctrl+Shift+K")
        self._git_commit_action.setEnabled(False)
        self._git_commit_action.triggered.connect(self._git_commit)
        self._git_menu.addAction(self._git_commit_action)

        # Stubs for _git_set_all_enabled
        self._git_configure_action = QAction("", self)
        self._git_status_action = QAction("", self)
        self._git_new_branch_action = QAction("", self)
        self._git_stash_action = QAction("", self)
        self._git_pop_stash_action = QAction("", self)
        self._git_log_action = QAction("", self)
        self._pull_menu = QMenu("", self)

        # ── Tools menu ───────────────────────────────────────────────────────
        tools_menu = menubar.addMenu("&Tools")

        settings_action = QAction("Settings...", self)
        settings_action.triggered.connect(self._open_settings)
        tools_menu.addAction(settings_action)

    # ═════════════════════════════════════════════════════════════════════════
    # Project loading
    # ═════════════════════════════════════════════════════════════════════════

    def load_data(self, project_info: dict):
        """Load project data and populate all tabs."""
        self.project_info = project_info
        project_dir = project_info.get("dir", "")
        project_name = project_info.get("name", os.path.basename(project_dir))
        self.setWindowTitle(f"EVENTide — {project_name}[*]")

        # Enable actions
        self._open_porysuite_action.setEnabled(True)
        self._refresh_action.setEnabled(True)
        self._git_set_all_enabled(True)

        # Notify tabs
        self.maps_tab.load_project(project_info)
        self.layouts_tab.load_project(project_info)
        self.region_map_tab.load_project(project_info)
        self.event_editor_tab.load_project(project_info)

        self.setWindowModified(False)
        self._git_refresh_status_bar()
        self.log_message(f"Loaded project: {project_name} ({project_dir})")

    def _refresh_project(self):
        """Reload all data from disk."""
        if not self.project_info:
            return
        # Check for unsaved Event Editor changes before discarding
        if self.isWindowModified():
            from app_util import create_unsaved_changes_dialog
            ret = create_unsaved_changes_dialog(
                self,
                "You have unsaved changes. Refreshing will discard them.\n"
                "Would you like to save first?")
            if ret == QMessageBox.StandardButton.Cancel:
                return
            if ret == QMessageBox.StandardButton.Save:
                self.event_editor_tab._on_save()
        self.load_data(self.project_info)
        self.statusBar().showMessage("Project refreshed from disk.", 4000)

    # ── Inter-tab refresh handlers ───────────────────────────────────────────

    def _on_maps_changed(self):
        """Maps tab mutated data — refresh region map and layouts tabs."""
        if self.project_info:
            self.region_map_tab.load_project(self.project_info)
            self.layouts_tab.load_project(self.project_info)

    def _on_layouts_changed(self):
        """Layouts tab mutated data — refresh maps tab (layout column)."""
        if self.project_info:
            self.maps_tab.load_project(self.project_info)

    def _on_region_map_changed(self):
        """Region map tab mutated data — refresh maps tab (section column)."""
        if self.project_info:
            self.maps_tab.load_project(self.project_info)

    # ═════════════════════════════════════════════════════════════════════════
    # Unsaved changes — Save / Discard / Cancel dialog
    # ═════════════════════════════════════════════════════════════════════════

    def _try_save_before_closing(self) -> bool:
        """Prompt to save unsaved changes.  Returns True if OK to proceed."""
        if not self.isWindowModified():
            return True
        from app_util import create_unsaved_changes_dialog
        ret = create_unsaved_changes_dialog(
            self,
            "You have unsaved changes. Would you like to save before closing?")
        if ret == QMessageBox.StandardButton.Save:
            self.event_editor_tab._on_save()
            return True
        return ret != QMessageBox.StandardButton.Cancel

    def closeEvent(self, event):
        """Prompt to save unsaved changes before closing."""
        if self._try_save_before_closing():
            event.accept()
        else:
            event.ignore()

    # ═════════════════════════════════════════════════════════════════════════
    # Logging
    # ═════════════════════════════════════════════════════════════════════════

    def log_message(self, msg: str):
        """Append a message to the log panel."""
        self.log.append(msg)

    # ═════════════════════════════════════════════════════════════════════════
    # Cross-launch
    # ═════════════════════════════════════════════════════════════════════════

    def _open_in_porysuite(self):
        if self.project_info:
            self.open_in_porysuite_signal.emit(self.project_info)

    def _open_settings(self):
        from settingsdialog import SettingsDialog
        SettingsDialog(self).exec()

    # ═════════════════════════════════════════════════════════════════════════
    # Git — same interface as PorySuite's MainWindow so GitPanel works as-is
    # ═════════════════════════════════════════════════════════════════════════

    def _git_exe(self) -> str:
        for c in (
            r"C:\Program Files\Git\bin\git.exe",
            r"C:\Program Files (x86)\Git\bin\git.exe",
        ):
            if os.path.isfile(c):
                return c
        return "git"

    def _git_run(self, *args, timeout: int = 120) -> tuple[bool, str]:
        cwd = (self.project_info or {}).get("dir", "")
        _no_win = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            r = subprocess.run(
                [self._git_exe(), "-C", cwd, *args],
                capture_output=True, text=True, timeout=timeout,
                creationflags=_no_win,
            )
            out = (r.stdout + r.stderr).strip()
            return r.returncode == 0, out
        except FileNotFoundError:
            return False, "git not found — install Git for Windows (https://git-scm.com)."
        except subprocess.TimeoutExpired:
            return False, f"git timed out after {timeout}s."
        except Exception as exc:
            return False, str(exc)

    def _open_git_panel(self) -> None:
        from git_panel import GitPanel
        panel = getattr(self, "_git_panel_instance", None)
        if panel is None or not panel.isVisible():
            panel = GitPanel(self)
            self._git_panel_instance = panel
        panel.show()
        panel.raise_()
        panel.activateWindow()

    def _git_set_all_enabled(self, enabled: bool) -> None:
        for name in (
            "_git_panel_action",
            "_git_configure_action", "_git_status_action",
            "_pull_upstream_action", "_pull_origin_action",
            "_push_action", "_git_commit_action", "_git_new_branch_action",
            "_git_stash_action", "_git_pop_stash_action", "_git_log_action",
        ):
            act = getattr(self, name, None)
            if act:
                act.setEnabled(enabled)
        pm = getattr(self, "_pull_menu", None)
        if pm:
            pm.setEnabled(enabled)

    # ── Saved-remotes persistence ────────────────────────────────────────────

    def _remotes_file(self) -> str:
        from app_info import get_data_dir
        return os.path.join(get_data_dir(), "git_remotes.json")

    def _load_saved_remotes(self) -> list[dict]:
        cwd = (self.project_info or {}).get("dir", "")
        try:
            with open(self._remotes_file(), "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get(cwd, [])
        except Exception:
            return []

    def _save_saved_remotes(self, remotes: list[dict]) -> None:
        cwd = (self.project_info or {}).get("dir", "")
        path = self._remotes_file()
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
        data[cwd] = remotes
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def _git_upstream_url(self) -> str:
        cwd = (self.project_info or {}).get("dir", "")
        try:
            with open(self._remotes_file(), "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("__upstream__", {}).get(cwd,
                "https://github.com/pret/pokefirered.git")
        except Exception:
            return "https://github.com/pret/pokefirered.git"

    def _git_save_upstream_url(self, url: str) -> None:
        cwd = (self.project_info or {}).get("dir", "")
        path = self._remotes_file()
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
        if "__upstream__" not in data:
            data["__upstream__"] = {}
        data["__upstream__"][cwd] = url
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def _git_refresh_status_bar(self) -> None:
        lbl = getattr(self, "_git_bar_label", None)
        if lbl is None or not self.project_info:
            return
        _, branch = self._git_run("rev-parse", "--abbrev-ref", "HEAD", timeout=5)
        branch = (branch or "").strip()
        if not branch or branch == "HEAD":
            lbl.setText("")
            return

        _, dirty_out = self._git_run("status", "--porcelain", timeout=5)
        dirty_lines = [l for l in (dirty_out or "").splitlines() if l.strip()]
        dirty_part = f"  \u270e {len(dirty_lines)}" if dirty_lines else ""

        _, ab = self._git_run(
            "rev-list", "--left-right", "--count", f"origin/{branch}...HEAD",
            timeout=5,
        )
        ab_part = ""
        ahead = behind = 0
        if ab:
            parts = ab.strip().split()
            if len(parts) == 2:
                try:
                    behind, ahead = int(parts[0]), int(parts[1])
                    if ahead:
                        ab_part += f"  \u2191{ahead}"
                    if behind:
                        ab_part += f"  \u2193{behind}"
                except ValueError:
                    pass

        lbl.setText(f"\u238b {branch}{dirty_part}{ab_part}")

        # Color-code: red for main/master, default otherwise
        if branch in ("main", "master"):
            lbl.setStyleSheet(
                "QLabel { color: #e06c75; font-weight: bold; padding: 0 6px; }"
            )
        else:
            lbl.setStyleSheet(
                "QLabel { padding: 0 6px; }"
            )

        lbl.setToolTip(
            f"Branch: {branch}"
            + (" ⚠ Protected branch!" if branch in ("main", "master") else "")
            + (f"\n{len(dirty_lines)} uncommitted file(s)" if dirty_lines else "")
            + (f"\n{ahead} commit(s) ahead of origin" if ahead else "")
            + (f"\n{behind} commit(s) behind origin" if behind else "")
        )

    # ── Git operations ───────────────────────────────────────────────────────

    def _git_pull(self, override_url: str = "", use_upstream: bool = False) -> None:
        if not self.project_info:
            return
        project_dir = self.project_info.get("dir", "")
        if not project_dir or not os.path.isdir(project_dir):
            QMessageBox.warning(self, "Pull", "Project directory not found.")
            return

        if use_upstream and not override_url:
            override_url = self._git_upstream_url()
        is_upstream_pull = bool(override_url)
        if override_url:
            remote_label = override_url
            fetch_args = ["fetch", override_url]
            reset_args = ["reset", "--hard", "FETCH_HEAD"]
            fetch_label = f"git fetch {override_url}"
            reset_label = "git reset --hard FETCH_HEAD"
        else:
            _, remote_url = self._git_run("remote", "get-url", "origin", timeout=10)
            remote_label = (remote_url or "origin").strip()
            fetch_args = ["fetch", "origin"]
            reset_args = ["reset", "--hard", "origin/HEAD"]
            fetch_label = "git fetch origin"
            reset_label = "git reset --hard origin/HEAD"

        _, branch = self._git_run("rev-parse", "--abbrev-ref", "HEAD", timeout=10)
        branch_label = (branch or "").strip()

        clean_preview = ""
        if is_upstream_pull:
            _, dry = self._git_run(
                "clean", "-fd", "--dry-run",
                "--exclude=project.json",
                "--exclude=config.json",
                timeout=10,
            )
            if dry and dry.strip():
                lines = dry.strip().splitlines()
                preview_lines = lines[:12]
                if len(lines) > 12:
                    preview_lines.append(f"  ... and {len(lines) - 12} more")
                clean_preview = "\n\nUntracked files that will be deleted:\n" + "\n".join(
                    f"  {l}" for l in preview_lines
                )

        confirm_text = (
            f"This will run:\n\n"
            f"  {fetch_label}\n"
            f"  {reset_label}\n"
            + ("  git clean -fd\n" if is_upstream_pull else "") +
            f"\n  Remote: {remote_label}\n"
            + (f"  Branch: {branch_label}\n" if branch_label else "") +
            f"\nThis will discard:\n"
            f"  - All uncommitted changes to tracked files\n"
            + ("  - ALL untracked files (custom graphics, scripts, etc.)\n" if is_upstream_pull else "") +
            clean_preview +
            f"\nContinue?"
        )

        ans = maybe_exec(
            key="git_pull_confirm",
            parent=self,
            title="Pull from Remote",
            text=confirm_text,
            icon=QMessageBox.Icon.Question,
            buttons=QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            default_button=QMessageBox.StandardButton.Yes,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return

        from PyQt6.QtWidgets import QDialog, QPlainTextEdit, QPushButton
        from PyQt6.QtCore import QThread, pyqtSignal as _sig

        prog_dlg = QDialog(self)
        prog_dlg.setWindowTitle("Git Pull")
        prog_dlg.setMinimumWidth(620)
        prog_dlg.setMinimumHeight(340)
        prog_dlg.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
        _vlayout = QVBoxLayout(prog_dlg)
        _vlayout.setSpacing(8)

        _from_lbl = QLabel(f"<b>Pulling from:</b> {remote_label}"
                           + (f"&nbsp;&nbsp;<b>Branch:</b> {branch_label}" if branch_label else ""))
        _from_lbl.setWordWrap(True)
        _vlayout.addWidget(_from_lbl)

        _out = QPlainTextEdit()
        _out.setReadOnly(True)
        _out.setFont(QFont("Courier New", 9))
        _out.setPlaceholderText("Waiting for git output...")
        _vlayout.addWidget(_out)

        _status_lbl = QLabel("Running...")
        _vlayout.addWidget(_status_lbl)

        _btn_row = QHBoxLayout()
        _btn_row.addStretch()
        _close_btn = QPushButton("Close")
        _close_btn.setEnabled(False)
        _close_btn.clicked.connect(prog_dlg.accept)
        _btn_row.addWidget(_close_btn)
        _vlayout.addLayout(_btn_row)

        prog_dlg.show()

        self._refresh_action.setEnabled(False)
        self._git_set_all_enabled(False)

        _steps = [
            (fetch_args, 180, fetch_label),
            (reset_args, 60, reset_label),
        ]
        if is_upstream_pull:
            _clean_args = [
                "clean", "-fd",
                "--exclude=project.json",
                "--exclude=config.json",
            ]
            _clean_label = "git clean -fd  (excluding config files)"
            _steps.append((_clean_args, 60, _clean_label))

        class _PullWorker(QThread):
            line_out = _sig(str)
            done = _sig(bool, str)

            def __init__(self, git, cwd, steps):
                super().__init__()
                self._git = git
                self._cwd = cwd
                self._steps = steps

            def _run_streaming(self, args, timeout_s):
                proc = subprocess.Popen(
                    [self._git, "-C", self._cwd, *args],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                import threading, queue as _queue
                q = _queue.Queue()

                def _reader():
                    for ln in proc.stdout:
                        q.put(ln)
                    q.put(None)

                threading.Thread(target=_reader, daemon=True).start()
                import time
                deadline = time.monotonic() + timeout_s
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        proc.kill()
                        return None, "timed out"
                    try:
                        item = q.get(timeout=min(0.1, remaining))
                    except _queue.Empty:
                        continue
                    if item is None:
                        break
                    self.line_out.emit(item.rstrip("\n"))
                proc.wait()
                return proc.returncode, None

            def run(self):
                for args, tmo, label in self._steps:
                    self.line_out.emit(f"\n$ {label}")
                    try:
                        rc, err = self._run_streaming(args, tmo)
                    except FileNotFoundError:
                        self.done.emit(False, "git not found — install Git for Windows.")
                        return
                    except Exception as exc:
                        self.done.emit(False, str(exc))
                        return
                    if err:
                        self.done.emit(False, f"{label}: {err}")
                        return
                    if rc != 0:
                        self.done.emit(False, f"{label} exited with code {rc}")
                        return
                self.done.emit(True, "Done.")

        worker = _PullWorker(self._git_exe(), project_dir, _steps)
        self._git_worker = worker

        def _append_line(ln: str):
            _out.appendPlainText(ln)

        def _on_done(ok: bool, msg: str):
            self._refresh_action.setEnabled(True)
            self._git_set_all_enabled(True)
            _status_lbl.setText(("\u2713 " if ok else "\u2717 ") + msg)
            _close_btn.setEnabled(True)
            prog_dlg.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, True)
            prog_dlg.show()

            if ok:
                self.statusBar().showMessage("Pull complete — refreshing project...", 4000)
                _append_line("\nReloading project data...")

                def _do_refresh():
                    self._refresh_project()
                    self._git_refresh_status_bar()
                    _append_line("Done.")
                    self.statusBar().showMessage("Pull complete.", 4000)
                QTimer.singleShot(50, _do_refresh)
            else:
                self.statusBar().showMessage("Pull failed.", 4000)

        worker.line_out.connect(_append_line)
        worker.done.connect(_on_done)
        worker.start()

    def _git_checkout_branch(self, branch: str) -> None:
        if not self.project_info:
            return
        project_dir = self.project_info.get("dir", "")
        if not project_dir or not os.path.isdir(project_dir):
            return

        ans = QMessageBox.question(
            self, "Switch Branch",
            f"Switch to branch  '{branch}'?\n\n"
            f"Unsaved changes will be lost.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return

        ok, msg = self._git_run("checkout", branch, timeout=20)
        if not ok:
            QMessageBox.warning(self, "Switch Branch", f"git checkout failed:\n{msg}")
            return

        self.statusBar().showMessage(f"Switched to branch '{branch}' — refreshing...", 4000)
        QTimer.singleShot(50, self._refresh_project)

    def _git_show_status(self) -> None:
        if not self.project_info:
            return
        from PyQt6.QtWidgets import QDialog, QPlainTextEdit, QPushButton

        _, branch = self._git_run("rev-parse", "--abbrev-ref", "HEAD", timeout=5)
        branch = (branch or "HEAD").strip()

        _, status_out = self._git_run("status", "--short", timeout=10)
        _, log_ahead = self._git_run("log", "--oneline", f"origin/{branch}..HEAD", timeout=10)
        _, log_behind = self._git_run("log", "--oneline", f"HEAD..origin/{branch}", timeout=10)
        _, stash_out = self._git_run("stash", "list", timeout=5)

        dlg = QDialog(self)
        dlg.setWindowTitle("Git Status")
        dlg.setMinimumWidth(560)
        vlay = QVBoxLayout(dlg)

        vlay.addWidget(QLabel(f"<b>Branch:</b> {branch}"))

        ahead_lines = [l for l in (log_ahead or "").splitlines() if l.strip()]
        behind_lines = [l for l in (log_behind or "").splitlines() if l.strip()]
        stash_lines = [l for l in (stash_out or "").splitlines() if l.strip()]

        if ahead_lines:
            vlay.addWidget(QLabel(f"<b>\u2191 {len(ahead_lines)} commit(s) ahead of origin</b>"))
        if behind_lines:
            vlay.addWidget(QLabel(f"<b>\u2193 {len(behind_lines)} commit(s) behind origin</b>"))
        if stash_lines:
            vlay.addWidget(QLabel(f"<b>\U0001f4e6 {len(stash_lines)} stash entry(s)</b>"))

        changed = [l for l in (status_out or "").splitlines() if l.strip()]
        if changed:
            vlay.addWidget(QLabel(f"<b>Changed files ({len(changed)}):</b>"))
            txt = QPlainTextEdit()
            txt.setReadOnly(True)
            txt.setFont(QFont("Courier New", 9))
            txt.setMaximumHeight(200)
            txt.setPlainText("\n".join(changed))
            vlay.addWidget(txt)
        else:
            vlay.addWidget(QLabel("<i>Working tree is clean.</i>"))

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dlg.accept)
        btn_row.addWidget(close_btn)
        vlay.addLayout(btn_row)

        dlg.exec()

    def _git_commit(self) -> None:
        if not self.project_info:
            return

        _, status_out = self._git_run("status", "--short", timeout=10)
        lines = [l for l in (status_out or "").splitlines() if l.strip()]

        if not lines:
            QMessageBox.information(self, "Commit", "Nothing to commit — working tree is clean.")
            return

        from PyQt6.QtWidgets import (
            QDialog, QListWidget, QListWidgetItem,
            QPlainTextEdit, QPushButton, QDialogButtonBox,
        )

        dlg = QDialog(self)
        dlg.setWindowTitle("Commit Changes")
        dlg.setMinimumWidth(560)
        dlg.setMinimumHeight(420)
        vlay = QVBoxLayout(dlg)

        vlay.addWidget(QLabel("<b>Files to commit</b>  (check to stage):"))
        file_list = QListWidget()
        file_list.setAlternatingRowColors(True)
        file_list.setMaximumHeight(180)

        for raw in lines:
            xy = raw[:2]
            path = raw[3:].strip()
            item = QListWidgetItem(f"{xy}  {path}")
            item.setData(256, path)
            item.setCheckState(Qt.CheckState.Checked)
            file_list.addItem(item)

        vlay.addWidget(file_list)
        vlay.addWidget(QLabel("<b>Commit message:</b>"))

        msg_edit = QPlainTextEdit()
        msg_edit.setPlaceholderText("Describe your changes...")
        msg_edit.setMaximumHeight(100)
        vlay.addWidget(msg_edit)

        status_lbl = QLabel("")
        vlay.addWidget(status_lbl)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("Commit")
        btns.rejected.connect(dlg.reject)
        vlay.addWidget(btns)

        def _do_commit():
            msg = msg_edit.toPlainText().strip()
            if not msg:
                status_lbl.setText("\u26a0  Please write a commit message.")
                return
            staged_any = False
            for i in range(file_list.count()):
                item = file_list.item(i)
                if item.checkState() == Qt.CheckState.Checked:
                    path = item.data(256)
                    self._git_run("add", path, timeout=10)
                    staged_any = True
            if not staged_any:
                status_lbl.setText("\u26a0  No files selected.")
                return
            ok, out = self._git_run("commit", "-m", msg, timeout=30)
            if ok:
                status_lbl.setText("\u2713  Committed successfully.")
                self._git_refresh_status_bar()
                dlg.accept()
            else:
                status_lbl.setText(f"\u2717  {out}")

        btns.accepted.connect(_do_commit)
        dlg.exec()

    def _git_new_branch(self) -> None:
        if not self.project_info:
            return
        from PyQt6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "New Branch", "Branch name:")
        if not ok or not name.strip():
            return
        name = name.strip().replace(" ", "-")
        ok2, msg = self._git_run("checkout", "-b", name, timeout=15)
        if ok2:
            self.statusBar().showMessage(f"Created and switched to branch '{name}'", 4000)
            self._git_refresh_status_bar()
        else:
            QMessageBox.warning(self, "New Branch", f"git checkout -b failed:\n\n{msg}")

    def _git_stash(self) -> None:
        if not self.project_info:
            return
        _, status_out = self._git_run("status", "--short", timeout=5)
        if not (status_out or "").strip():
            QMessageBox.information(self, "Stash", "Nothing to stash — working tree is clean.")
            return
        ok, msg = self._git_run("stash", "push", "--include-untracked", "-m",
                                "EVENTide stash", timeout=30)
        if ok:
            self.statusBar().showMessage("Changes stashed.", 3000)
            self._git_refresh_status_bar()
        else:
            QMessageBox.warning(self, "Stash Failed", f"git stash failed:\n\n{msg}")

    def _git_pop_stash(self) -> None:
        if not self.project_info:
            return
        _, stash_list = self._git_run("stash", "list", timeout=5)
        if not (stash_list or "").strip():
            QMessageBox.information(self, "Pop Stash", "No stash entries to restore.")
            return
        ok, msg = self._git_run("stash", "pop", timeout=30)
        if ok:
            self.statusBar().showMessage("Stash restored.", 3000)
            self._git_refresh_status_bar()
        else:
            QMessageBox.warning(self, "Pop Stash Failed", f"git stash pop failed:\n\n{msg}")

    def _git_view_log(self) -> None:
        if not self.project_info:
            return
        from PyQt6.QtWidgets import QDialog, QPlainTextEdit, QPushButton

        _, log_out = self._git_run(
            "log", "--oneline", "--format=%C(auto)%h  %ad  %s  [%an]",
            "--date=short", "-30",
            timeout=15,
        )

        dlg = QDialog(self)
        dlg.setWindowTitle("Git Log  (last 30 commits)")
        dlg.setMinimumWidth(700)
        dlg.setMinimumHeight(400)
        vlay = QVBoxLayout(dlg)

        txt = QPlainTextEdit()
        txt.setReadOnly(True)
        txt.setFont(QFont("Courier New", 9))
        txt.setPlainText(log_out or "(no commits found)")
        vlay.addWidget(txt)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dlg.accept)
        btn_row.addWidget(close_btn)
        vlay.addLayout(btn_row)

        dlg.exec()

    def _git_push(self) -> None:
        """Push to Remote — with branch selector, main/master protection,
        and first-push detection."""
        if not self.project_info:
            return
        project_dir = self.project_info.get("dir", "")
        if not project_dir or not os.path.isdir(project_dir):
            QMessageBox.warning(self, "Push", "Project directory not found.")
            return

        _, remote_url = self._git_run("remote", "get-url", "origin", timeout=10)
        if not remote_url:
            QMessageBox.warning(
                self, "Push to Remote",
                "No remote is configured.\n\nUse Git \u2192 Configure Remote\u2026 to set one first."
            )
            return

        _, current_branch = self._git_run("rev-parse", "--abbrev-ref", "HEAD", timeout=10)
        current_branch = (current_branch or "HEAD").strip()

        # Get all local branches
        _, branches_out = self._git_run(
            "branch", "--format=%(refname:short)", timeout=10
        )
        all_branches = [b.strip() for b in (branches_out or "").splitlines() if b.strip()]
        if not all_branches:
            all_branches = [current_branch]

        # ── Build push dialog ─────────────────────────────────────────────────
        from PyQt6.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
            QPlainTextEdit, QPushButton,
        )
        from PyQt6.QtGui import QFont
        from PyQt6.QtCore import Qt as _Qt

        dlg = QDialog(self)
        dlg.setWindowTitle("Push to Remote")
        dlg.setMinimumWidth(520)
        vlay = QVBoxLayout(dlg)

        # Branch selector
        branch_row = QHBoxLayout()
        branch_row.addWidget(QLabel("<b>Branch to push:</b>"))
        branch_combo = QComboBox()
        branch_combo.setFocusPolicy(_Qt.FocusPolicy.StrongFocus)
        branch_combo.installEventFilter(self._combo_wheel_filter())
        for b in all_branches:
            branch_combo.addItem(b)
        idx = branch_combo.findText(current_branch)
        if idx >= 0:
            branch_combo.setCurrentIndex(idx)
        branch_row.addWidget(branch_combo, 1)
        vlay.addLayout(branch_row)

        # Remote info
        remote_lbl = QLabel(f"<b>Remote:</b>  {remote_url.strip()}")
        remote_lbl.setStyleSheet("color: #aaa; font-size: 11px;")
        vlay.addWidget(remote_lbl)

        # Warning label (updates dynamically)
        warn_lbl = QLabel("")
        warn_lbl.setWordWrap(True)
        warn_lbl.setStyleSheet("font-size: 12px;")
        vlay.addWidget(warn_lbl)

        # Ahead log
        ahead_text = QPlainTextEdit()
        ahead_text.setReadOnly(True)
        ahead_text.setFont(QFont("Courier New", 9))
        ahead_text.setMaximumHeight(140)
        vlay.addWidget(ahead_text)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        new_branch_btn = QPushButton("Create New Branch\u2026")
        push_btn = QPushButton("Push")
        push_btn.setDefault(True)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(dlg.reject)
        btn_row.addWidget(new_branch_btn)
        btn_row.addWidget(push_btn)
        btn_row.addWidget(cancel_btn)
        vlay.addLayout(btn_row)

        PROTECTED = {"main", "master"}

        def _refresh_push_info():
            sel = branch_combo.currentText()
            _, ahead_log = self._git_run(
                "log", "--oneline", f"origin/{sel}..HEAD", timeout=10
            )
            ahead_lines = [l for l in (ahead_log or "").splitlines() if l.strip()]

            if ahead_lines:
                ahead_text.setPlainText("\n".join(ahead_lines))
                ahead_text.show()
            else:
                ahead_text.setPlainText("")
                ahead_text.hide()

            # Check if branch exists on remote
            _, remote_check = self._git_run(
                "ls-remote", "--heads", "origin", sel, timeout=15
            )
            is_first_push = not bool((remote_check or "").strip())

            parts = []
            if sel in PROTECTED:
                parts.append(
                    f"\u26a0 <b>You are pushing directly to '{sel}'.</b><br>"
                    f"This is the main branch \u2014 everyone pulling from this remote "
                    f"will get these changes immediately. If your work is not ready, "
                    f"consider creating a feature branch instead."
                )
            if is_first_push:
                parts.append(
                    f"\u2139  Branch '{sel}' does not exist on the remote yet. "
                    f"This push will create it."
                )
            if not ahead_lines:
                parts.append("No commits ahead of origin \u2014 nothing new to push.")

            if parts:
                warn_lbl.setText("<br><br>".join(parts))
                if sel in PROTECTED:
                    warn_lbl.setStyleSheet(
                        "font-size: 12px; color: #e8a44a; "
                        "background: #3a2a10; padding: 8px; border-radius: 4px;"
                    )
                else:
                    warn_lbl.setStyleSheet("font-size: 12px; color: #aaa;")
                warn_lbl.show()
            else:
                count = len(ahead_lines)
                warn_lbl.setText(f"\u2713  {count} commit(s) ready to push to '{sel}'.")
                warn_lbl.setStyleSheet("font-size: 12px; color: #7cbb5e;")
                warn_lbl.show()

            if sel in PROTECTED:
                push_btn.setText("\u26a0 Push to " + sel)
                push_btn.setStyleSheet(
                    "QPushButton { background: #5a3a10; color: #e8a44a; font-weight: bold; }"
                )
            else:
                push_btn.setText("Push")
                push_btn.setStyleSheet("")

        branch_combo.currentTextChanged.connect(lambda _: _refresh_push_info())
        _refresh_push_info()

        def _create_and_switch():
            from PyQt6.QtWidgets import QInputDialog
            name, ok = QInputDialog.getText(dlg, "New Branch", "Branch name:")
            if not ok or not name.strip():
                return
            name = name.strip().replace(" ", "-")
            ok2, msg = self._git_run("checkout", "-b", name, timeout=15)
            if ok2:
                branch_combo.addItem(name)
                branch_combo.setCurrentText(name)
                self._git_refresh_status_bar()
            else:
                QMessageBox.warning(dlg, "New Branch", f"Failed:\n{msg}")

        new_branch_btn.clicked.connect(_create_and_switch)

        chosen_branch = [None]

        def _do_push():
            sel = branch_combo.currentText()
            if sel in PROTECTED:
                ans = QMessageBox.warning(
                    dlg,
                    f"Push to {sel}?",
                    f"You are about to push directly to '{sel}'.\n\n"
                    f"This will update the remote immediately. Anyone pulling "
                    f"from this remote will receive these changes.\n\n"
                    f"Are you sure? Consider using a feature branch if your "
                    f"work is incomplete.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel,
                )
                if ans != QMessageBox.StandardButton.Yes:
                    return
            chosen_branch[0] = sel
            dlg.accept()

        push_btn.clicked.connect(_do_push)

        if dlg.exec() != QDialog.DialogCode.Accepted or not chosen_branch[0]:
            return

        branch = chosen_branch[0]

        # ── Execute push in background thread ─────────────────────────────────
        self.statusBar().showMessage(f"Pushing {branch} to origin\u2026", 0)
        self._refresh_action.setEnabled(False)
        self._git_set_all_enabled(False)

        from PyQt6.QtCore import QThread, pyqtSignal as _sig

        class _PushWorker(QThread):
            done = _sig(bool, str)
            def __init__(self, git, cwd, br):
                super().__init__()
                self._git = git
                self._cwd = cwd
                self._branch = br
            def run(self):
                try:
                    r = subprocess.run(
                        [self._git, "-C", self._cwd, "push", "origin", self._branch],
                        capture_output=True, text=True, timeout=180,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    out = (r.stdout + r.stderr).strip()
                    self.done.emit(r.returncode == 0, out)
                except FileNotFoundError:
                    self.done.emit(False, "git not found \u2014 install Git for Windows.")
                except subprocess.TimeoutExpired:
                    self.done.emit(False, "git push timed out after 3 minutes.")
                except Exception as exc:
                    self.done.emit(False, str(exc))

        worker = _PushWorker(self._git_exe(), project_dir, branch)
        self._git_worker = worker

        def _on_done(ok: bool, msg: str):
            self._refresh_action.setEnabled(True)
            self._git_set_all_enabled(True)
            if ok:
                self.statusBar().showMessage(f"Push complete: {branch} \u2192 origin", 5000)
                self._git_refresh_status_bar()
            else:
                self.statusBar().showMessage("Push failed.", 4000)
                QMessageBox.critical(self, "Push Failed", f"git reported:\n\n{msg}")

        worker.done.connect(_on_done)
        worker.start()

    def _combo_wheel_filter(self):
        """Return a shared event filter that blocks wheel events on unfocused combo boxes."""
        filt = getattr(self, "_wheel_filter_instance", None)
        if filt is not None:
            return filt
        from PyQt6.QtCore import QObject, QEvent
        class _WheelBlocker(QObject):
            def eventFilter(self, obj, event):
                if event.type() == QEvent.Type.Wheel and not obj.hasFocus():
                    return True
                return False
        self._wheel_filter_instance = _WheelBlocker(self)
        return self._wheel_filter_instance

    def _git_configure_remote(self) -> None:
        """Placeholder — handled by the git panel's remotes section."""
        self._open_git_panel()
