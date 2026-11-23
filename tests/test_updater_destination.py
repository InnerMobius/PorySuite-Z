"""The auto-updater must update the installation that launched it — never a
presumed default path, never a different copy, and never silently.

WHY (2026-11-23). The generated ``_apply_update.bat`` bakes absolute paths at
download time and used to be effectively immortal: its self-delete line was
mis-quoted, so the script survived every update (one from July was still
sitting in the maintainer's install in November, full of that machine's
paths).  A script like that travels along when an installation is copied, and
the old ``launch_update_and_exit`` would run ANY ``_apply_update.bat`` it
found — aiming the update at whatever installation the script was written
for, not the one running.  The apply step also used ``robocopy /MIR`` (which
DELETES files the release doesn't contain, such as private notes or
uncommitted additions) and reported success no matter how many copies failed.

These tests drive the real download → stage → script → apply pipeline
against disposable installations in temp folders (plain paths, paths with
spaces, two installs side by side) using ``file://`` zipballs.  The live
PorySuite installation is never touched: every test passes an explicit
``app_dir``.
"""

import os
import pathlib
import re
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
                                  reason="the update script is a Windows .bat")

# Stub launcher: proves the relaunch step ran, without starting anything.
_LAUNCHER_STUB = '@echo off\r\necho relaunched %1> "%~dp0relaunch_marker.txt"\r\n'


# ── helpers ────────────────────────────────────────────────────────────────

def _make_install(root: pathlib.Path) -> pathlib.Path:
    """A disposable fake PorySuite installation."""
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


def _make_zipball(path: pathlib.Path) -> str:
    """A fake GitHub release zipball; returns its file:// URL."""
    top = "InnerMobius-PorySuite-Z-abc1234"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"{top}/app.py", "print('new app')\n")
        zf.writestr(f"{top}/core/module.py", "NEW = 2\n")
        zf.writestr(f"{top}/core/added.py", "NEW = 2\n")
        zf.writestr(f"{top}/ui/widget.py", "NEW = 2\n")
        zf.writestr(f"{top}/docs/README.md", "new readme\n")
        zf.writestr(f"{top}/LaunchPorySuite.bat", _LAUNCHER_STUB)
        # Things a release must NOT be allowed to plant:
        zf.writestr(f"{top}/settings.ini", "[evil]\nclobbered=1\n")
        zf.writestr(f"{top}/_apply_update.bat", "@echo should never be copied\n")
    return path.as_uri()


def _dead_pid() -> int:
    """A PID that no longer exists, so the script's wait loop exits at once."""
    p = subprocess.Popen(["cmd", "/c", "exit"],
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    p.wait()
    return p.pid


def _stage(install: pathlib.Path, tmp: pathlib.Path) -> pathlib.Path:
    """Run the real download+stage step against *install*; return the script."""
    url = _make_zipball(tmp / "zipball.zip")
    updater.download_and_install(url, app_dir=str(install),
                                 wait_pid=_dead_pid())
    return install / "_apply_update.bat"


def _apply(script: pathlib.Path, run_from: pathlib.Path) -> None:
    """Execute the generated script the way Windows would, from a FOREIGN
    working directory to prove the destination never depends on cwd.

    Streams go to a FILE, not pipes: the script's `start` grandchildren
    inherit pipe handles and deadlock a captured run.  The file persists in
    pytest's tmp dir, so a failed run can be diagnosed afterwards."""
    log = script.parent / "apply_output.log"
    with open(log, "w", encoding="utf-8", errors="replace") as out:
        p = subprocess.run(["cmd", "/c", str(script)], cwd=str(run_from),
                           timeout=180, stdin=subprocess.DEVNULL,
                           stdout=out, stderr=subprocess.STDOUT,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    out_text = log.read_text(encoding="utf-8", errors="replace")
    assert "Applying update..." in out_text, (
        f"the update script exited (rc={p.returncode}) without reaching the "
        f"apply step; its output was:\n{out_text}")


def _wait_for(path: pathlib.Path, timeout: float = 15.0) -> bool:
    """Poll for a file the script creates asynchronously (via `start`)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists():
            return True
        time.sleep(0.25)
    return path.exists()


def _snapshot(root: pathlib.Path) -> dict:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = p.read_bytes()
    return out


# ── where does the update go? ──────────────────────────────────────────────

def test_default_destination_is_the_running_installation():
    """With no override, the destination is derived from the updater's own
    location — the install that launched it — not cwd or any fixed path."""
    assert os.path.normcase(updater._default_app_dir()) == \
        os.path.normcase(str(ROOT))


@WINDOWS_ONLY
def test_script_contains_no_paths_outside_its_installation(tmp_path):
    """Every absolute path baked into the script stays inside the install —
    the regression pin for 'updater writes to a presumed default'."""
    install = _make_install(tmp_path / "With Spaces" / "PorySuite Z")
    script = _stage(install, tmp_path)
    text = script.read_text(encoding="utf-8")
    prefix = os.path.normcase(str(install))
    for m in re.finditer(r'[A-Za-z]:[\\/][^"\r\n]*', text):
        assert os.path.normcase(m.group(0)).startswith(prefix), \
            f"script references a path outside the install: {m.group(0)!r}"


@WINDOWS_ONLY
def test_relaunch_never_uses_bare_start_on_a_bat(tmp_path):
    """`start "" something.bat` runs the bat with cmd /K — a dead console
    window that stays open forever (one per update).  Every relaunch of a
    .bat must go through an explicit `cmd /c`."""
    install = _make_install(tmp_path / "PorySuiteA")
    script = _stage(install, tmp_path)
    for line in script.read_text(encoding="utf-8").splitlines():
        low = line.strip().lower()
        if low.startswith("start") and ".bat" in low:
            assert re.search(r'cmd(\.exe"?)?\s+/c', low), \
                f"bare start on a .bat leaves a stuck cmd /K window: {line!r}"


@WINDOWS_ONLY
def test_update_applies_to_the_install_that_ran_it(tmp_path):
    """End to end, in a path with spaces, run from a foreign cwd."""
    install = _make_install(tmp_path / "With Spaces" / "PorySuite Z")
    script = _stage(install, tmp_path)

    target, staging = updater.read_script_markers(str(script))
    assert os.path.normcase(target) == os.path.normcase(str(install))
    assert os.path.normcase(staging).startswith(os.path.normcase(str(install)))

    foreign_cwd = tmp_path / "elsewhere"
    foreign_cwd.mkdir()
    _apply(script, run_from=foreign_cwd)

    read = lambda *parts: install.joinpath(*parts).read_text(encoding="utf-8")
    assert "new app" in read("app.py")
    assert "NEW = 2" in read("core", "module.py")
    assert "NEW = 2" in read("core", "added.py")
    assert "new readme" in read("docs", "README.md")
    # Preserved and user-owned content survived:
    assert "kept=1" in read("settings.ini")
    assert "private notes" in read("docs", "BUGS.md")
    assert "mine" in read("core", "my_uncommitted.py")
    assert read("data", "projects.json") == "{}"
    # Nothing was planted in the foreign cwd:
    assert list(foreign_cwd.iterdir()) == []
    # Relaunch happened, staging gone, no error, script deleted itself:
    assert _wait_for(install / "relaunch_marker.txt"), "relaunch never ran"
    assert not (install / "_update_staging").exists()
    assert not (install / "_update_error.log").exists()
    for _ in range(20):          # self-delete is the script's last act
        if not script.exists():
            break
        time.sleep(0.25)
    assert not script.exists(), "_apply_update.bat did not delete itself"


@WINDOWS_ONLY
def test_two_installations_side_by_side_do_not_cross(tmp_path):
    install_a = _make_install(tmp_path / "PorySuiteA")
    install_b = _make_install(tmp_path / "PorySuiteB")
    before_b = _snapshot(install_b)

    script = _stage(install_a, tmp_path)
    # Snapshot after staging: the zipball fixture file lives in tmp_path.
    top_level_before = set(os.listdir(tmp_path))
    _apply(script, run_from=tmp_path)

    assert "new app" in (install_a / "app.py").read_text(encoding="utf-8")
    assert _snapshot(install_b) == before_b, \
        "updating installation A changed installation B"
    assert set(os.listdir(tmp_path)) == top_level_before, \
        "the update created something outside the installation being updated"


# ── stale and foreign scripts are refused, not run ─────────────────────────

def test_script_targeting_a_different_installation_is_refused(tmp_path):
    install = tmp_path / "PorySuiteB"
    install.mkdir()
    stale = install / "_apply_update.bat"
    stale.write_text(
        "@echo off\r\n"
        f"{updater._TARGET_MARKER}C:\\GBA\\porysuite\r\n"
        f"{updater._STAGING_MARKER}C:\\GBA\\porysuite\\_update_staging\r\n",
        encoding="utf-8")
    with pytest.raises(RuntimeError) as e:
        updater._validate_update_script(str(stale), str(install))
    assert "different installation" in str(e.value)
    assert stale.exists(), "validation must refuse, not run or delete"


def test_markerless_legacy_script_is_refused(tmp_path):
    stale = tmp_path / "_apply_update.bat"
    stale.write_text("@echo off\r\necho Waiting...\r\n", encoding="utf-8")
    with pytest.raises(RuntimeError):
        updater._validate_update_script(str(stale), str(tmp_path))


def test_script_with_missing_staging_is_refused(tmp_path):
    stale = tmp_path / "_apply_update.bat"
    stale.write_text(
        "@echo off\r\n"
        f"{updater._TARGET_MARKER}{tmp_path}\r\n"
        f"{updater._STAGING_MARKER}{tmp_path / '_update_staging'}\r\n",
        encoding="utf-8")
    with pytest.raises(RuntimeError) as e:
        updater._validate_update_script(str(stale), str(tmp_path))
    assert "missing" in str(e.value).lower()


@WINDOWS_ONLY
def test_fresh_download_replaces_any_stale_script(tmp_path):
    install = _make_install(tmp_path / "PorySuiteA")
    (install / "_apply_update.bat").write_text(
        f"@echo off\r\n{updater._TARGET_MARKER}C:\\somewhere\\else\r\n",
        encoding="utf-8")
    (install / "_update_error.log").write_text("old failure", encoding="utf-8")

    script = _stage(install, tmp_path)
    target, _ = updater.read_script_markers(str(script))
    assert os.path.normcase(target) == os.path.normcase(str(install))
    assert not (install / "_update_error.log").exists()
    # And the staged, validated script is accepted for THIS install:
    updater._validate_update_script(str(script), str(install))


# ── failure honesty ────────────────────────────────────────────────────────

@WINDOWS_ONLY
def test_release_dir_over_user_file_refused_before_any_change(tmp_path):
    """A release folder colliding with a file of the same name used to fail
    at APPLY time (after some files were already replaced).  It must now be
    refused during preparation, before anything is modified at all.
    (Runtime copy failures and their rollback are covered in
    test_updater_transaction.py.)"""
    install = _make_install(tmp_path / "PorySuiteA")
    # Sabotage: a FILE where the update needs the ui directory.
    import shutil
    shutil.rmtree(install / "ui")
    (install / "ui").write_text("not a directory", encoding="utf-8")
    before = _snapshot(install)

    with pytest.raises(RuntimeError) as e:
        _stage(install, tmp_path)
    assert "Nothing was changed" in str(e.value)
    assert _snapshot(install) == before, \
        "a refused update must not modify the installation"
    assert not (install / "_update_staging").exists()
    assert not (install / "_apply_update.bat").exists()
    assert not (install / "_update_backup").exists()


def test_failed_download_stages_nothing(tmp_path):
    install = tmp_path / "PorySuiteA"
    (install / "core").mkdir(parents=True)
    missing = (tmp_path / "no_such.zip").as_uri()
    with pytest.raises(Exception):
        updater.download_and_install(missing, app_dir=str(install),
                                     wait_pid=12345)
    assert not (install / "_apply_update.bat").exists()
    assert not (install / "_update_staging").exists()


def test_non_windows_gets_a_clear_error_not_a_bat_file(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "_is_windows", lambda: False)
    with pytest.raises(RuntimeError) as e:
        updater.download_and_install("file:///nowhere.zip",
                                     app_dir=str(tmp_path))
    assert "releases" in str(e.value).lower()
    assert not (tmp_path / "_apply_update.bat").exists()
