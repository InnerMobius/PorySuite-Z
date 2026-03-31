import os
import subprocess
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QWidget,
)


class GitRunner:
    def __init__(self, cwd: Path):
        self.cwd = cwd

    def run(self, args: list[str]) -> tuple[int, str, str]:
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=self.cwd,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
            )
            return proc.returncode, proc.stdout, proc.stderr
        except FileNotFoundError:
            return 1, "", "git executable not found in PATH"

    def current_branch(self) -> str:
        code, out, _ = self.run(["rev-parse", "--abbrev-ref", "HEAD"])
        return out.strip() if code == 0 else ""

    def has_staged(self) -> bool:
        code, out, _ = self.run(["diff", "--name-only", "--cached"])
        return bool(out.strip())

    def has_unstaged(self) -> bool:
        code, out, _ = self.run(["status", "--porcelain"])
        return any(line and not line.startswith("??") for line in out.splitlines())


class GitWindow(QMainWindow):
    def __init__(self, repo_root: Path):
        super().__init__()
        self.setWindowTitle("PorySuite • Git Helper")
        self.resize(900, 600)

        self.repo_root = repo_root
        self.git = GitRunner(repo_root)

        central = QWidget(self)
        self.setCentralWidget(central)
        grid = QGridLayout(central)

        # Controls row
        row = QHBoxLayout()
        self.btn_status = QPushButton("Status")
        self.btn_add = QPushButton("Add All")
        self.btn_commit = QPushButton("Commit…")
        self.btn_push = QPushButton("Push…")
        self.btn_stash = QPushButton("Stash Save…")
        self.btn_stash_pop = QPushButton("Stash Pop")

        for b in [
            self.btn_status,
            self.btn_add,
            self.btn_commit,
            self.btn_push,
            self.btn_stash,
            self.btn_stash_pop,
        ]:
            row.addWidget(b)

        grid.addLayout(row, 0, 0)

        # Options row
        opts = QHBoxLayout()
        self.opt_force = QCheckBox("Force push (with lease)")
        self.opt_untracked = QCheckBox("Stash includes untracked (-u)")
        opts.addWidget(self.opt_force)
        opts.addWidget(self.opt_untracked)
        opts.addStretch(1)
        grid.addLayout(opts, 1, 0)

        # Output
        self.out = QPlainTextEdit()
        self.out.setReadOnly(True)
        grid.addWidget(self.out, 2, 0)

        # Wire signals
        self.btn_status.clicked.connect(self.do_status)
        self.btn_add.clicked.connect(self.do_add)
        self.btn_commit.clicked.connect(self.do_commit)
        self.btn_push.clicked.connect(self.do_push)
        self.btn_stash.clicked.connect(self.do_stash)
        self.btn_stash_pop.clicked.connect(self.do_stash_pop)

        self.log(f"Repo: {self.repo_root}")

    # Utilities
    def log(self, msg: str):
        self.out.appendPlainText(msg.rstrip("\n"))

    def run_git(self, args: list[str], title: str | None = None) -> int:
        if title:
            self.log(f"$ git {' '.join(args)}")
        QApplication.setOverrideCursor(Qt.CursorShape.BusyCursor)
        try:
            code, out, err = self.git.run(args)
        finally:
            QApplication.restoreOverrideCursor()
        if out:
            self.log(out)
        if err:
            self.log(err)
        return code

    # Actions
    def do_status(self):
        self.run_git(["status", "-sb"], "status")

    def do_add(self):
        code = self.run_git(["add", "-A"], "add")
        if code == 0:
            self.log("Staged all changes.")

    def do_commit(self):
        text, ok = QInputDialog.getText(self, "Commit Message", "Message:")
        if not ok:
            return
        if not text.strip():
            QMessageBox.warning(self, "Commit", "Commit message is required.")
            return
        if not self.git.has_staged():
            if self.git.has_unstaged():
                ret = QMessageBox.question(
                    self,
                    "Stage Changes",
                    "No staged changes. Stage all and continue?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if ret == QMessageBox.StandardButton.Yes:
                    self.do_add()
                else:
                    return
        self.run_git(["commit", "-m", text], "commit")

    def do_push(self):
        branch = self.git.current_branch() or ""
        remote, ok = QInputDialog.getText(self, "Push", "Remote:", text="origin")
        if not ok:
            return
        if not branch:
            branch, ok = QInputDialog.getText(self, "Push", "Branch:", text="")
            if not ok:
                return
        args = ["push", remote, branch]
        if self.opt_force.isChecked():
            args.insert(1, "--force-with-lease")
        if self.git.has_unstaged():
            ret = QMessageBox.question(
                self,
                "Unstaged Changes",
                "You have unstaged changes. Continue push?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ret != QMessageBox.StandardButton.Yes:
                return
        self.run_git(args, "push")

    def do_stash(self):
        text, ok = QInputDialog.getText(self, "Stash Save", "Message:")
        if not ok:
            return
        args = ["stash", "push", "-m", text]
        if self.opt_untracked.isChecked():
            args.append("-u")
        self.run_git(args, "stash push")

    def do_stash_pop(self):
        ret = QMessageBox.question(
            self,
            "Stash Pop",
            "Apply the latest stash and drop it?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        self.run_git(["stash", "pop"], "stash pop")


def main():
    app = QApplication([])
    repo_root = Path(__file__).resolve().parents[1]
    win = GitWindow(repo_root)
    win.show()
    app.exec()


if __name__ == "__main__":
    main()

