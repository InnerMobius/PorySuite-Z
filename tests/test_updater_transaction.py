"""The auto-updater must never cost the user work — not a developer's
uncommitted changes, not user-created files, and not a working installation
when an update dies halfway.

WHY (2026-11-23).  The first updater hardening (test_updater_destination.py)
made updates target the right installation and stop deleting user files, but
three gaps remained:

* Running from a Git source checkout, the updater would happily replace
  tracked files that held uncommitted development work.
* A copy failure halfway through left a MIX of old and new files — the
  error log was honest, but nothing restored the previous version.
* Because ``robocopy /MIR`` was (rightly) banned, files removed from newer
  releases lingered in the installation forever.

The updater now refuses Git checkouts outright (git pull is the update path
there), runs every update as a prepared transaction (verified backup +
journal + standalone rollback script + crash detection at next launch), and
tracks release-file ownership in ``_installed_manifest.json`` so obsolete
release files are removed ONLY when they are provably the release's own,
unmodified files.

These tests drive the real pipeline against disposable installations in
temp folders and inject real failures (read-only targets, deleted backups,
simulated crashes).  The live PorySuite installation is never touched:
every test passes an explicit ``app_dir``.
"""

import json
import os
import pathlib
import re
import shutil
import stat
import subprocess
import sys
import time
import zipfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "core")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import updater  # noqa: E402

WINDOWS_ONLY = pytest.mark.skipif(os.name != "nt",
                                  reason="the update scripts are Windows .bat")
NEEDS_GIT = pytest.mark.skipif(shutil.which("git") is None,
                               reason="git is not installed")

# Stub launcher: proves the relaunch step ran, without starting anything.
_LAUNCHER_STUB = '@echo off\r\necho relaunched %1> "%~dp0relaunch_marker.txt"\r\n'

# Two fake releases.  v2 ships a new module and a whole new folder, and
# DROPS core/oldmod.py and core/droppedmod.py.
V1_FILES = {
    "app.py": "print('new app v1')\n",
    "core/module.py": "V = 1\n",
    "core/oldmod.py": "OLD MODULE\n",
    "core/droppedmod.py": "DROPPED\n",
    "ui/widget.py": "V = 1\n",
    "docs/README.md": "readme v1\n",
    "LaunchPorySuite.bat": _LAUNCHER_STUB,
}
V2_FILES = {
    "app.py": "print('new app v2')\n",
    "core/module.py": "V = 2\n",
    "core/newmod.py": "NEW MODULE\n",
    "newpkg/thing.py": "THING\n",
    "ui/widget.py": "V = 2\n",
    "docs/README.md": "readme v2\n",
    "LaunchPorySuite.bat": _LAUNCHER_STUB,
}

# Snapshot noise: the transaction's own working files plus test scaffolding.
_SNAPSHOT_IGNORE = ("_update_", "_apply_update.bat", "_recover_update.bat",
                    "apply_output.log", "recover_output.log",
                    "relaunch_marker.txt")


# ── helpers ────────────────────────────────────────────────────────────────

def _make_install(root: pathlib.Path) -> pathlib.Path:
    """A disposable fake PorySuite installation with user-owned content."""
    root.mkdir(parents=True)
    (root / "core").mkdir()
    (root / "ui").mkdir()
    (root / "docs").mkdir()
    (root / "data").mkdir()
    (root / "app.py").write_text("print('old app')\n", encoding="utf-8")
    (root / "core" / "module.py").write_text("OLD = 1\n", encoding="utf-8")
    (root / "ui" / "widget.py").write_text("OLD = 1\n", encoding="utf-8")
    (root / "docs" / "README.md").write_text("old readme\n", encoding="utf-8")
    # Content an update must never destroy:
    (root / "settings.ini").write_text("[user]\nkept=1\n", encoding="utf-8")
    (root / "data" / "projects.json").write_text("{}", encoding="utf-8")
    (root / "docs" / "BUGS.md").write_text("private notes\n", encoding="utf-8")
    (root / "core" / "my_uncommitted.py").write_text("mine\n", encoding="utf-8")
    (root / "LaunchPorySuite.bat").write_text(_LAUNCHER_STUB, encoding="utf-8")
    return root


def _make_zipball(path: pathlib.Path, files: dict) -> str:
    top = "InnerMobius-PorySuite-Z-abc1234"
    with zipfile.ZipFile(path, "w") as zf:
        for rel, content in files.items():
            zf.writestr(f"{top}/{rel}", content)
    return path.as_uri()


def _dead_pid() -> int:
    p = subprocess.Popen(["cmd", "/c", "exit"],
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    p.wait()
    return p.pid


def _stage(install: pathlib.Path, tmp: pathlib.Path, files: dict,
           name: str = "zipball.zip") -> pathlib.Path:
    url = _make_zipball(tmp / name, files)
    updater.download_and_install(url, app_dir=str(install),
                                 wait_pid=_dead_pid())
    return install / "_apply_update.bat"


def _run_bat(script: pathlib.Path, run_from: pathlib.Path,
             log_name: str, must_reach: str | None = None) -> str:
    """Execute a generated script from a FOREIGN working directory.  Streams
    go to a FILE, not pipes: `start` grandchildren inherit pipe handles and
    deadlock a captured run."""
    log = script.parent / log_name
    with open(log, "w", encoding="utf-8", errors="replace") as out:
        p = subprocess.run(["cmd", "/c", str(script)], cwd=str(run_from),
                           timeout=180, stdin=subprocess.DEVNULL,
                           stdout=out, stderr=subprocess.STDOUT,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    out_text = log.read_text(encoding="utf-8", errors="replace")
    if must_reach is not None:
        assert must_reach in out_text, (
            f"the script exited (rc={p.returncode}) without reaching "
            f"{must_reach!r}; its output was:\n{out_text}")
    return out_text


def _apply(script: pathlib.Path, run_from: pathlib.Path) -> str:
    return _run_bat(script, run_from, "apply_output.log",
                    must_reach="Applying update...")


def _wait_for(path: pathlib.Path, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists():
            return True
        time.sleep(0.25)
    return path.exists()


def _snapshot(root: pathlib.Path) -> dict:
    out = {}
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root))
        if any(rel.startswith(x) for x in _SNAPSHOT_IGNORE):
            continue
        if p.is_file():
            out[rel] = p.read_bytes()
    return out


def _install_v1(install: pathlib.Path, tmp: pathlib.Path) -> None:
    """Bring a disposable install to released v1 (writes the manifest)."""
    script = _stage(install, tmp, V1_FILES, name="zip_v1.zip")
    _apply(script, run_from=tmp)
    assert (install / "_installed_manifest.json").exists(), \
        "a successful update must write the installed-files manifest"
    assert not (install / "_update_journal.json").exists()
    assert not (install / "_update_backup").exists()


def _no_transaction_leftovers(install: pathlib.Path) -> None:
    for name in ("_update_staging", "_update_backup", "_update_journal.json",
                 "_update_phase.txt", "_apply_update.bat",
                 "_recover_update.bat", "_update_lock"):
        assert not (install / name).exists(), f"leftover: {name}"


def _git(install: pathlib.Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(install),
                    "-c", "user.email=test@example.invalid",
                    "-c", "user.name=Test", *args],
                   check=True, capture_output=True, timeout=30)


# ═══ A. Git development checkouts are never updated by file replacement ═════

@NEEDS_GIT
def test_dirty_git_checkout_is_refused_with_nothing_changed(tmp_path):
    install = _make_install(tmp_path / "PorySuiteDev")
    _git(install, "init")
    _git(install, "add", "-A")
    _git(install, "commit", "-m", "base")
    # Uncommitted development work in every flavor:
    (install / "core" / "module.py").write_text("WIP = True\n",
                                                encoding="utf-8")
    (install / "ui" / "widget.py").write_text("STAGED\n", encoding="utf-8")
    _git(install, "add", "ui/widget.py")
    (install / "core" / "brand_new.py").write_text("untracked\n",
                                                   encoding="utf-8")
    before = _snapshot(install)

    with pytest.raises(RuntimeError) as e:
        _stage(install, tmp_path, V1_FILES)
    msg = str(e.value)
    assert "git pull" in msg.lower()
    assert ".git" in msg
    assert _snapshot(install) == before, \
        "refusing a checkout update must not touch the checkout"
    _no_transaction_leftovers(install)


@NEEDS_GIT
def test_clean_git_checkout_is_refused_too(tmp_path):
    """A clean status moments ago proves nothing by the time the apply
    script runs — ALL source checkouts update via git."""
    install = _make_install(tmp_path / "PorySuiteDev")
    _git(install, "init")
    _git(install, "add", "-A")
    _git(install, "commit", "-m", "base")
    with pytest.raises(RuntimeError) as e:
        _stage(install, tmp_path, V1_FILES)
    assert "git pull" in str(e.value).lower()
    _no_transaction_leftovers(install)


def test_git_worktree_marker_file_also_counts(tmp_path):
    """git worktrees mark themselves with a .git FILE, not a folder."""
    install = _make_install(tmp_path / "PorySuiteDev")
    (install / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
    assert updater.installation_is_git_checkout(str(install))
    with pytest.raises(RuntimeError):
        _stage(install, tmp_path, V1_FILES)


@WINDOWS_ONLY
def test_nested_project_repo_does_not_block_updates(tmp_path):
    """Only the installation ROOT decides — a game project with its own
    repo nested inside must not disable the packaged install's updater."""
    install = _make_install(tmp_path / "PorySuiteA")
    nested = install / "myproject" / ".git"
    nested.mkdir(parents=True)
    (nested / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (install / "myproject" / "notes.txt").write_text("game stuff\n",
                                                     encoding="utf-8")
    assert not updater.installation_is_git_checkout(str(install))
    script = _stage(install, tmp_path, V1_FILES)
    assert script.exists()
    updater._validate_update_script(str(script), str(install))
    # The nested project was never part of the plan:
    journal = json.loads((install / "_update_journal.json")
                         .read_text(encoding="utf-8"))
    for group in ("replaced", "added_files", "removed"):
        assert not any(p.startswith("myproject") for p in journal[group])


def test_downloaded_update_never_bypasses_the_checkout_guard(tmp_path):
    """Having an update already staged is NOT a license to apply it over a
    checkout — the guard runs again at launch time."""
    install = _make_install(tmp_path / "PorySuiteA")
    (install / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
    with pytest.raises(RuntimeError):
        updater._refuse_if_git_checkout(str(install))


# ═══ B+E. Successful update: manifest ownership and user-file handling ══════

@WINDOWS_ONLY
def test_full_update_lifecycle_protects_every_kind_of_file(tmp_path):
    # Parentheses and a trailing digit in the install path: both have bitten
    # generated cmd scripts before (block-ending ')' and `2>>` redirects).
    install = _make_install(tmp_path / "PorySuite (x86) 2")
    _install_v1(install, tmp_path)
    manifest_v1 = json.loads((install / "_installed_manifest.json")
                             .read_text(encoding="utf-8"))["files"]
    assert "core/oldmod.py" in manifest_v1

    # The user now lives in this install for a while:
    (install / "docs" / "MYNOTES.md").write_text("my notes\n",
                                                 encoding="utf-8")
    (install / "docs" / "README.md").write_text("my edited readme\n",
                                                encoding="utf-8")
    (install / "core" / "droppedmod.py").write_text("my version\n",
                                                    encoding="utf-8")
    (install / "data" / "templates.json").write_text('{"mine": 1}',
                                                     encoding="utf-8")

    script = _stage(install, tmp_path, V2_FILES, name="zip_v2.zip")
    _apply(script, run_from=tmp_path)

    read = lambda *parts: install.joinpath(*parts).read_text(encoding="utf-8")
    # New release content applied:
    assert "v2" in read("app.py")
    assert "V = 2" in read("core", "module.py")
    assert "NEW MODULE" in read("core", "newmod.py")
    assert "THING" in read("newpkg", "thing.py")
    # Obsolete, UNMODIFIED release file removed:
    assert not (install / "core" / "oldmod.py").exists(), \
        "an unmodified obsolete release file must be removed"
    # Obsolete but LOCALLY MODIFIED release file kept:
    assert read("core", "droppedmod.py") == "my version\n"
    # Modified release file replaced, but the user's edit set aside first:
    assert read("docs", "README.md") == "readme v2\n"
    assert read("_update_preserved", "docs", "README.md") == \
        "my edited readme\n"
    # User-owned content untouched:
    assert read("docs", "MYNOTES.md") == "my notes\n"
    assert read("docs", "BUGS.md") == "private notes\n"
    assert read("core", "my_uncommitted.py") == "mine\n"
    assert "kept=1" in read("settings.ini")
    assert read("data", "templates.json") == '{"mine": 1}'
    # Manifest moved to the v2 file set:
    manifest_v2 = json.loads(read("_installed_manifest.json"))["files"]
    assert "core/newmod.py" in manifest_v2
    assert "core/oldmod.py" not in manifest_v2
    # Transaction cleaned up after itself:
    _no_transaction_leftovers(install)
    assert not (install / "_update_error.log").exists()
    # The user is TOLD about the set-aside edit and the kept file:
    state = updater.startup_update_check(str(install))
    assert state and state["kind"] == "notice"
    assert "docs/README.md" in state["message"]
    assert "core/droppedmod.py" in state["message"]
    assert updater.startup_update_check(str(install)) is None


@WINDOWS_ONLY
def test_release_file_over_user_created_file_is_a_conflict(tmp_path):
    """v2 ships core/newmod.py; the user already made their own file with
    that exact path.  Silently taking ownership is forbidden — the update
    stops before changing anything."""
    install = _make_install(tmp_path / "PorySuiteA")
    _install_v1(install, tmp_path)
    (install / "core" / "newmod.py").write_text("MY OWN FILE\n",
                                                encoding="utf-8")
    before = _snapshot(install)

    with pytest.raises(RuntimeError) as e:
        _stage(install, tmp_path, V2_FILES, name="zip_v2.zip")
    msg = str(e.value)
    assert "core/newmod.py" in msg
    assert "Nothing was changed" in msg
    assert _snapshot(install) == before
    _no_transaction_leftovers(install)


@WINDOWS_ONLY
def test_legacy_install_without_manifest_is_migrated_conservatively(tmp_path):
    """No manifest (updated by an older PorySuite-Z): nothing is ever
    removed, files the release ships are treated as release-owned, and the
    first manifest is written for future updates."""
    install = _make_install(tmp_path / "PorySuiteA")
    (install / "docs" / "MYNOTES.md").write_text("my notes\n",
                                                 encoding="utf-8")
    (install / "core" / "stale_old_thing.py").write_text("ancient\n",
                                                         encoding="utf-8")
    script = _stage(install, tmp_path, V1_FILES)
    _apply(script, run_from=tmp_path)

    assert "v1" in (install / "app.py").read_text(encoding="utf-8")
    # Unknown pre-existing files: ownership unknowable, so ALL kept.
    assert (install / "core" / "stale_old_thing.py").exists()
    assert (install / "docs" / "MYNOTES.md").exists()
    manifest = json.loads((install / "_installed_manifest.json")
                          .read_text(encoding="utf-8"))["files"]
    assert "core/stale_old_thing.py" not in manifest, \
        "the manifest records only files the release installed"
    assert "core/module.py" in manifest


# ═══ C. Failure injection: every failure rolls back or reports honestly ═════

def test_unwritable_install_is_refused_before_download(tmp_path):
    install = _make_install(tmp_path / "PorySuiteA")
    # The write probe cannot be created where a folder sits on its name.
    (install / "_update_write_probe.tmp").mkdir()
    with pytest.raises(RuntimeError) as e:
        _stage(install, tmp_path, V1_FILES)
    assert "cannot write" in str(e.value)
    assert not (install / "_update_staging").exists()


@WINDOWS_ONLY
def test_insufficient_disk_space_is_refused_before_backup(tmp_path, monkeypatch):
    install = _make_install(tmp_path / "PorySuiteA")
    _install_v1(install, tmp_path)
    before = _snapshot(install)
    real = shutil.disk_usage(str(install))
    monkeypatch.setattr(updater.shutil, "disk_usage",
                        lambda p: real._replace(free=1024))
    with pytest.raises(RuntimeError) as e:
        _stage(install, tmp_path, V2_FILES, name="zip_v2.zip")
    assert "disk space" in str(e.value).lower()
    assert _snapshot(install) == before
    _no_transaction_leftovers(install)


@WINDOWS_ONLY
def test_midway_copy_failure_rolls_back_to_the_previous_version(tmp_path):
    """Folders copy first, root files last.  A read-only app.py makes the
    LAST copy fail — by then core/, docs/ and the new folder were already
    replaced.  Rollback must return every one of them to v1."""
    install = _make_install(tmp_path / "PorySuiteA")
    _install_v1(install, tmp_path)
    before = _snapshot(install)
    app_py = install / "app.py"
    os.chmod(app_py, stat.S_IREAD)
    try:
        script = _stage(install, tmp_path, V2_FILES, name="zip_v2.zip")
        out = _apply(script, run_from=tmp_path)
        assert "Restoring the previous PorySuite-Z version" in out
    finally:
        os.chmod(app_py, stat.S_IREAD | stat.S_IWRITE)

    # Honest failure report:
    log = (install / "_update_error.log").read_text(encoding="utf-8")
    assert "did NOT fully apply" in log
    assert "app.py" in log                      # names the failing step
    assert "restored successfully" in log
    assert (install / "_update_staging").exists(), \
        "staged files are kept after a failure"
    assert updater.read_update_phase(str(install)) == "ROLLED_BACK"
    assert _wait_for(install / "relaunch_marker.txt"), \
        "the app must be relaunched after a rolled-back update"
    # The installation is EXACTLY v1 again — no mixed versions:
    assert _snapshot(install) == before, \
        "rollback must restore every already-replaced file"
    assert not (install / "newpkg").exists()
    assert not (install / "core" / "newmod.py").exists()
    # Next launch reports it once, then clears the transaction:
    state = updater.startup_update_check(str(install))
    assert state and state["kind"] == "error"
    assert "restored successfully" in state["message"]
    _no_transaction_leftovers(install)
    assert updater.startup_update_check(str(install)) is None


@WINDOWS_ONLY
def test_locked_file_fails_the_update_and_rolls_back(tmp_path):
    """A file held open with no sharing (as another program would) blocks
    its replacement; the update must fail and roll back, not half-apply."""
    import ctypes
    install = _make_install(tmp_path / "PorySuiteA")
    _install_v1(install, tmp_path)
    before = _snapshot(install)
    locked = install / "docs" / "README.md"

    # Stage FIRST (planning hashes the file, so it must still be readable),
    # then lock it for the apply step — as a viewer/editor holding the file
    # open at update time would.
    script = _stage(install, tmp_path, V2_FILES, name="zip_v2.zip")
    GENERIC_READ = 0x80000000
    OPEN_EXISTING = 3
    k32 = ctypes.windll.kernel32
    handle = k32.CreateFileW(str(locked), GENERIC_READ, 0, None,
                             OPEN_EXISTING, 0, None)
    assert handle != -1, "could not lock the test file"
    try:
        _apply(script, run_from=tmp_path)
    finally:
        k32.CloseHandle(handle)

    assert updater.read_update_phase(str(install)) == "ROLLED_BACK"
    assert (install / "_update_error.log").exists()
    assert _snapshot(install) == before, \
        "a locked file must abort and roll back the whole update"


@WINDOWS_ONLY
def test_failed_rollback_is_reported_never_claimed_recovered(tmp_path):
    """When rollback itself cannot run (the backup vanished), the updater
    must say exactly that, keep every recovery material, and NOT pretend
    the installation is consistent."""
    install = _make_install(tmp_path / "PorySuiteA")
    _install_v1(install, tmp_path)
    app_py = install / "app.py"
    try:
        script = _stage(install, tmp_path, V2_FILES, name="zip_v2.zip")
        os.chmod(app_py, stat.S_IREAD)              # makes the apply fail
        shutil.rmtree(install / "_update_backup")   # sabotage the backup
        _apply(script, run_from=tmp_path)
    finally:
        os.chmod(app_py, stat.S_IREAD | stat.S_IWRITE)

    assert updater.read_update_phase(str(install)) == "ROLLBACK_FAILED"
    log = (install / "_update_error.log").read_text(encoding="utf-8")
    assert "FAILED" in log
    assert "_update_backup" in log               # tells the user where/how
    # Recovery materials retained, nothing silently discarded:
    assert (install / "_update_staging").exists()
    assert (install / "_recover_update.bat").exists()
    assert (install / "_update_journal.json").exists()
    # Next launch reports the mixed state and does NOT clean it away:
    state = updater.startup_update_check(str(install))
    assert state and state["kind"] == "rollback_failed"
    assert (install / "_update_error.log").exists(), \
        "the report must persist until the user acts on it"
    assert (install / "_update_journal.json").exists()
    # A new update refuses to pave over the unrecovered state:
    with pytest.raises(RuntimeError) as e:
        _stage(install, tmp_path, V2_FILES, name="zip_v2b.zip")
    assert "recovered first" in str(e.value)


@WINDOWS_ONLY
def test_obsolete_file_blocked_from_removal_is_kept_and_reported(tmp_path):
    """Managed-file removal must never turn destructive: if the obsolete
    path now holds a folder, leave it alone and say so — the update itself
    still succeeds."""
    install = _make_install(tmp_path / "PorySuiteA")
    _install_v1(install, tmp_path)
    script = _stage(install, tmp_path, V2_FILES, name="zip_v2.zip")
    # AFTER planning, the obsolete file's path becomes a folder:
    old = install / "core" / "oldmod.py"
    old.unlink()
    old.mkdir()
    (old / "surprise.txt").write_text("user data\n", encoding="utf-8")

    _apply(script, run_from=tmp_path)

    assert (old / "surprise.txt").exists(), \
        "del on a folder path would have destroyed its contents"
    assert "v2" in (install / "app.py").read_text(encoding="utf-8"), \
        "the update itself still applies"
    state = updater.startup_update_check(str(install))
    assert state and state["kind"] == "notice"
    assert "core/oldmod.py" in state["message"]


# ═══ D. Interrupted updates: crash detection and standalone recovery ════════

@WINDOWS_ONLY
def test_interrupted_apply_is_detected_and_recoverable(tmp_path):
    """Update starts, some files are replaced, the process dies (power
    loss).  Next launch must detect the incomplete transaction, and the
    standalone recovery script must restore the previous version — it is
    a plain .bat precisely so it works when Python cannot start."""
    install = _make_install(tmp_path / "PorySuiteA")
    _install_v1(install, tmp_path)
    (install / "docs" / "MYNOTES.md").write_text("my notes\n",
                                                 encoding="utf-8")
    before = _snapshot(install)

    _stage(install, tmp_path, V2_FILES, name="zip_v2.zip")
    # Simulate the crash: the apply step replaced SOME files, then died.
    journal = json.loads((install / "_update_journal.json")
                         .read_text(encoding="utf-8"))
    src = pathlib.Path(journal["src"])
    shutil.copy2(src / "core" / "module.py", install / "core" / "module.py")
    (install / "newpkg").mkdir()
    shutil.copy2(src / "newpkg" / "thing.py", install / "newpkg" / "thing.py")
    updater._write_update_phase(str(install), updater.PHASE_APPLYING)

    # Next launch sees the incomplete transaction:
    state = updater.startup_update_check(str(install))
    assert state and state["kind"] == "recovery_needed"
    assert state["recovery_script"], "recovery script must be offered"
    assert "restore" in state["message"].lower()
    # The mixed state was NOT cleaned away behind the user's back:
    assert (install / "_update_journal.json").exists()

    # Run the standalone recovery script (as the app — or the user, by
    # double-click — would):
    out = _run_bat(pathlib.Path(state["recovery_script"]), tmp_path,
                   "recover_output.log",
                   must_reach="Restoring the previous PorySuite-Z version")
    assert updater.read_update_phase(str(install)) == "ROLLED_BACK"
    assert _snapshot(install) == before, \
        "recovery must restore the exact pre-update installation"
    assert not (install / "newpkg").exists()
    assert _wait_for(install / "relaunch_marker.txt")
    log = (install / "_update_error.log").read_text(encoding="utf-8")
    assert "restored successfully" in log

    # And the launch after THAT reports it once and clears the state:
    state = updater.startup_update_check(str(install))
    assert state and state["kind"] == "error"
    _no_transaction_leftovers(install)
    assert updater.startup_update_check(str(install)) is None


def test_crash_after_success_just_finishes_the_cleanup(tmp_path):
    """Dying between 'everything verified' and 'cleanup finished' must not
    look like a failure — next launch finishes the cleanup silently."""
    install = _make_install(tmp_path / "PorySuiteA")
    (install / "_update_journal.json").write_text(
        json.dumps({"format": 1, "target": str(install)}), encoding="utf-8")
    (install / "_update_staging").mkdir()
    (install / "_update_backup").mkdir()
    (install / "_update_phase.txt").write_text("DONE\n", encoding="utf-8")
    (install / "_update_notices.log").write_text("note line\n",
                                                 encoding="utf-8")
    state = updater.startup_update_check(str(install))
    assert state and state["kind"] == "notice"
    _no_transaction_leftovers(install)
    assert updater.startup_update_check(str(install)) is None


def test_prepared_but_never_applied_update_is_cleared_silently(tmp_path):
    install = _make_install(tmp_path / "PorySuiteA")
    (install / "_update_journal.json").write_text(
        json.dumps({"format": 1, "target": str(install)}), encoding="utf-8")
    (install / "_update_phase.txt").write_text("PREPARED\n", encoding="utf-8")
    (install / "_update_staging").mkdir()
    (install / "_update_backup").mkdir()
    assert updater.startup_update_check(str(install)) is None
    _no_transaction_leftovers(install)


# ═══ F. Multiple installations stay isolated ════════════════════════════════

@WINDOWS_ONLY
def test_copied_transaction_artifacts_cannot_redirect_an_update(tmp_path):
    """Journals, scripts and backups that travelled along when an install
    was copied must be recognized as FOREIGN — reported, refused, and never
    acted on."""
    install_a = _make_install(tmp_path / "PorySuiteA")
    install_b = _make_install(tmp_path / "PorySuiteB")
    _install_v1(install_a, tmp_path)
    _install_v1(install_b, tmp_path)
    _stage(install_a, tmp_path, V2_FILES, name="zip_v2.zip")

    # "Copy the installation" — A's pending transaction lands inside B:
    for name in ("_update_journal.json", "_update_phase.txt",
                 "_apply_update.bat", "_recover_update.bat"):
        shutil.copy2(install_a / name, install_b / name)
    before_b = _snapshot(install_b)

    state = updater.startup_update_check(str(install_b))
    assert state and state["kind"] == "foreign"
    assert str(install_a) in state["message"]
    with pytest.raises(RuntimeError) as e:
        updater._validate_update_script(
            str(install_b / "_apply_update.bat"), str(install_b))
    assert "different installation" in str(e.value)
    with pytest.raises(RuntimeError):
        updater._validate_recovery_script(
            str(install_b / "_recover_update.bat"), str(install_b))
    with pytest.raises(RuntimeError) as e:
        _stage(install_b, tmp_path, V2_FILES, name="zip_bar.zip")
    assert "different" in str(e.value)
    assert _snapshot(install_b) == before_b, "B must be untouched"

    # A's own update still applies cleanly, and B stays byte-identical:
    for name in ("_update_journal.json", "_update_phase.txt",
                 "_apply_update.bat", "_recover_update.bat"):
        (install_b / name).unlink()
    before_b = _snapshot(install_b)
    _apply(install_a / "_apply_update.bat", run_from=tmp_path)
    assert "v2" in (install_a / "app.py").read_text(encoding="utf-8")
    assert _snapshot(install_b) == before_b, \
        "updating installation A changed installation B"


@WINDOWS_ONLY
def test_concurrent_update_attempts_cannot_overlap(tmp_path):
    install = _make_install(tmp_path / "PorySuiteA")
    # Another LIVE process holds the update lock.  By absolute interpreter
    # path: a bare tool name (`ping`) resolves through the test process's
    # PATH, where Git/MSYS lookalikes exit instantly — making the "live"
    # holder dead and the test meaningless.
    other = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        (install / "_update_lock").write_text(
            json.dumps({"pid": other.pid}), encoding="utf-8")
        with pytest.raises(RuntimeError) as e:
            _stage(install, tmp_path, V1_FILES)
        assert "already preparing an update" in str(e.value)
        assert not (install / "_update_staging").exists()
    finally:
        other.kill()
        other.wait()
    # A DEAD holder's stale lock is stolen and the update proceeds:
    script = _stage(install, tmp_path, V1_FILES, name="zip2.zip")
    assert script.exists()


# ═══ Script and archive safety ══════════════════════════════════════════════

@WINDOWS_ONLY
def test_recovery_script_paths_stay_inside_the_installation(tmp_path):
    install = _make_install(tmp_path / "Program Files (x86)" / "PorySuite Z")
    _stage(install, tmp_path, V1_FILES)
    text = (install / "_recover_update.bat").read_text(encoding="utf-8")
    prefix = os.path.normcase(str(install))
    for m in re.finditer(r'[A-Za-z]:[\\/][^"\r\n]*', text):
        assert os.path.normcase(m.group(0)).startswith(prefix), \
            f"recovery script references a path outside the install: {m.group(0)!r}"
    for line in text.splitlines():
        low = line.strip().lower()
        if low.startswith("start") and ".bat" in low:
            assert re.search(r'cmd(\.exe"?)?\s+/c', low), \
                f"bare start on a .bat leaves a stuck cmd /K window: {line!r}"


def test_zip_with_traversal_or_absolute_paths_is_refused(tmp_path):
    install = _make_install(tmp_path / "PorySuiteA")
    top = "InnerMobius-PorySuite-Z-abc1234"
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr(f"{top}/app.py", "print('x')\n")
        zf.writestr(f"{top}/core/module.py", "V = 1\n")
        zf.writestr(f"{top}/../escape.txt", "escaped\n")
    with pytest.raises(RuntimeError) as e:
        updater.download_and_install(evil.as_uri(), app_dir=str(install),
                                     wait_pid=_dead_pid())
    assert "refused" in str(e.value)
    assert not (install / "_update_staging").exists()
    assert not (tmp_path / "escape.txt").exists()
    assert not (install.parent / "escape.txt").exists()

    evil2 = tmp_path / "evil2.zip"
    with zipfile.ZipFile(evil2, "w") as zf:
        zf.writestr(f"{top}/app.py", "print('x')\n")
        zf.writestr("C:\\escape2.txt", "escaped\n")
    with pytest.raises(RuntimeError):
        updater.download_and_install(evil2.as_uri(), app_dir=str(install),
                                     wait_pid=_dead_pid())
    assert not (install / "_update_staging").exists()


@WINDOWS_ONLY
def test_planted_updater_artifacts_in_the_archive_are_stripped(tmp_path):
    """A malicious or broken archive must not be able to smuggle in its own
    _apply_update.bat / _recover_update.bat / manifest / journal."""
    install = _make_install(tmp_path / "PorySuiteA")
    files = dict(V1_FILES)
    files["_apply_update.bat"] = "@echo should never be copied\n"
    files["_recover_update.bat"] = "@echo should never be copied\n"
    files["_update_journal.json"] = '{"target": "C:/elsewhere"}'
    files["_installed_manifest.json"] = '{"format": 1, "files": {}}'
    script = _stage(install, tmp_path, files)
    _apply(script, run_from=tmp_path)

    assert "v1" in (install / "app.py").read_text(encoding="utf-8")
    # The planted manifest was replaced by the REAL one built from staging:
    manifest = json.loads((install / "_installed_manifest.json")
                          .read_text(encoding="utf-8"))["files"]
    assert "core/module.py" in manifest
    # No planted scripts/journal survived into the installation:
    _no_transaction_leftovers(install)


def test_archive_that_is_not_a_porysuite_release_is_refused(tmp_path):
    install = _make_install(tmp_path / "PorySuiteA")
    top = "InnerMobius-PorySuite-Z-abc1234"
    junk = tmp_path / "junk.zip"
    with zipfile.ZipFile(junk, "w") as zf:
        zf.writestr(f"{top}/readme.txt", "not an app\n")
    with pytest.raises(RuntimeError) as e:
        updater.download_and_install(junk.as_uri(), app_dir=str(install),
                                     wait_pid=_dead_pid())
    assert "does not look like a PorySuite-Z release" in str(e.value)
    assert not (install / "_update_staging").exists()
