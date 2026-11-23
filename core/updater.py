"""
core/updater.py
Auto-updater for PorySuite-Z.

Checks the GitHub releases API for new versions, shows a notification
dialog with the changelog, and can download + extract the update
in-place (preserving settings, data, and cache).

The actual file replacement is done by a small batch script that runs
AFTER the app exits, since Windows locks files that are in use.

DESTINATION SAFETY (2026-11-23). Every path the updater touches is derived
from THIS file's location — the installation that is actually running —
never from a presumed default, the working directory, or a path remembered
in settings.  The generated batch scripts carry machine-readable marker
lines naming their target installation, and the launch functions refuse to
run any script whose target is not the running installation (for example a
stale script that travelled along when an installation was copied).

TRANSACTIONAL UPDATES (2026-11-23).  An update now runs as a prepared
transaction:

* Git source checkouts are refused outright — the built-in updater never
  replaces files in a development working tree (``git pull`` is the update
  path there).  Packaged installations (no ``.git``) keep the built-in
  updater.
* Before anything is modified, every file the update will replace or
  remove is copied into ``_update_backup`` and the copy is verified by
  hash.  A plain-text phase file (``_update_phase.txt``) and a JSON
  journal (``_update_journal.json``) record what the transaction will do.
* The apply script fails FAST on the first copy error and immediately
  calls ``_recover_update.bat``, which restores every backed-up file and
  deletes files the update added — returning the installation to exactly
  its previous state.  ``_recover_update.bat`` is a standalone script: if
  the apply step (or Windows itself) dies mid-update and the application
  can no longer start, the user can run it by double-clicking it.
* On the next launch, ``startup_update_check`` reads the phase file and
  either finishes an interrupted cleanup, reports a rolled-back failure,
  or offers recovery for an interrupted apply.

FILE OWNERSHIP (2026-11-23).  Each successful update writes
``_installed_manifest.json``: the relative path, size and SHA-256 of every
file the release installed.  The next update uses it to tell release files
from the user's own files:

* A release file that would overwrite a file NOT in the manifest (one the
  user created) stops the update before anything is modified.
* A manifest file missing from the new release is removed — but only when
  its content still matches the manifest hash.  Locally modified copies
  are kept and reported.
* A manifest file the user modified that the new release replaces is
  copied to ``_update_preserved`` first, so their edit is never lost.
* Installations without a manifest (updated by an older PorySuite-Z) are
  migrated conservatively: nothing is ever removed, files the new release
  ships are treated as release-owned, and the first manifest is written
  for future updates.  ``_update_preserved`` itself is user data and is
  never deleted by the updater.
"""
from __future__ import annotations

import os
import io
import sys
import json
import stat
import ctypes
import shutil
import hashlib
import zipfile
import subprocess
import urllib.request
import urllib.error
from datetime import datetime
from typing import Optional

from app_info import VERSION

# ── GitHub repo coordinates ──────────────────────────────────────────────────

GITHUB_OWNER = "InnerMobius"
GITHUB_REPO = "PorySuite-Z"
RELEASES_API = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases"

# Files/dirs that must NEVER be overwritten by an update
PRESERVE = {
    "settings.ini",
    "data",
    "cache",
    "pokefirered",
    ".venv",
    "venv",
    "cleanenv",
    "porymap",
    "porymap_src",
    "qt_sdk",
    "crashlogs",
    "launch.log",
}

# ── Updater artifact names (all live at the installation root) ───────────────

_STAGING_DIR = "_update_staging"
_BACKUP_DIR = "_update_backup"
_PRESERVED_DIR = "_update_preserved"     # user data — NEVER auto-deleted
_APPLY_SCRIPT = "_apply_update.bat"
_RECOVER_SCRIPT = "_recover_update.bat"
_ERROR_LOG = "_update_error.log"
_NOTICES_PENDING = "_update_notices.pending"
_NOTICES_LOG = "_update_notices.log"
_JOURNAL = "_update_journal.json"
_PHASE_FILE = "_update_phase.txt"
_LOCK_FILE = "_update_lock"
_MANIFEST = "_installed_manifest.json"
_PROBE = "_update_write_probe.tmp"

# The updater's own artifacts — never treated as release content.  A zip
# that tries to plant one of these is stripped of it before planning.
_UPDATER_ARTIFACTS = {
    _STAGING_DIR, _BACKUP_DIR, _PRESERVED_DIR, _APPLY_SCRIPT, _RECOVER_SCRIPT,
    _ERROR_LOG, _NOTICES_PENDING, _NOTICES_LOG, _JOURNAL, _PHASE_FILE,
    _LOCK_FILE, _MANIFEST, _PROBE, ".git",
}

# ── Transaction phases (written to _update_phase.txt) ────────────────────────

PHASE_PREPARED = "PREPARED"            # staged + backed up; nothing modified
PHASE_APPLYING = "APPLYING"            # the apply script is replacing files
PHASE_ROLLING_BACK = "ROLLING_BACK"    # a failure is being rolled back
PHASE_ROLLED_BACK = "ROLLED_BACK"      # rollback finished; old version intact
PHASE_ROLLBACK_FAILED = "ROLLBACK_FAILED"  # mixed state; backup retained
PHASE_DONE = "DONE"                    # applied + verified; cleaning up

#: Marker lines written at the top of the generated batch scripts.  They are
#: how the launch functions verify a script belongs to THIS installation
#: before running it.
_TARGET_MARKER = "REM PORYSUITE-UPDATE-TARGET: "
_STAGING_MARKER = "REM PORYSUITE-UPDATE-STAGING: "
_BACKUP_MARKER = "REM PORYSUITE-UPDATE-BACKUP: "


def _is_windows() -> bool:
    return os.name == "nt"


def _default_app_dir() -> str:
    """The installation that is running: the folder holding this file's
    parent ``core/`` — never the working directory or a remembered path."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == \
        os.path.normcase(os.path.normpath(b))


# ── Version comparison ───────────────────────────────────────────────────────

def parse_version(v: str) -> tuple:
    """Parse a version string like '0.0.6b' into a comparable tuple.

    The version scheme treats the third segment as a decimal fraction so
    that 0.0.6 > 0.0.59 (just as 0.6 > 0.59 in decimal arithmetic).
    This means '6' → 0.6, '59' → 0.59, '61' → 0.61, '611' → 0.611.
    The first two segments (major, minor) are plain integers.
    Strips leading 'v' and trailing alpha suffix before parsing.
    """
    v = v.strip().lstrip("v")
    # Split off trailing alpha suffix (e.g. 'b' for beta)
    suffix = ""
    while v and v[-1].isalpha():
        suffix = v[-1] + suffix
        v = v[:-1]
    parts = v.split(".")
    try:
        major = int(parts[0]) if len(parts) > 0 else 0
    except ValueError:
        major = 0
    try:
        minor = int(parts[1]) if len(parts) > 1 else 0
    except ValueError:
        minor = 0
    # Third segment is a decimal fraction: "6" → 0.6, "59" → 0.59
    patch_str = parts[2] if len(parts) > 2 else "0"
    try:
        patch = float("0." + patch_str)
    except ValueError:
        patch = 0.0
    return (major, minor, patch, suffix)


def is_newer(remote: str, local: str = VERSION) -> bool:
    """Return True if `remote` is a newer version than `local`."""
    return parse_version(remote) > parse_version(local)


# ── GitHub API ───────────────────────────────────────────────────────────────

def check_for_update() -> Optional[dict]:
    """Check GitHub for the latest release.

    Returns a dict with keys: tag, name, body, url, zipball_url
    if a newer version exists, or None if up to date (or on error).
    """
    try:
        req = urllib.request.Request(
            RELEASES_API,
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": f"PorySuite-Z/{VERSION}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

    tag = data.get("tag_name", "")
    if not tag:
        return None

    if not is_newer(tag):
        return None

    return {
        "tag": tag,
        "name": data.get("name", tag),
        "body": data.get("body", ""),
        "url": data.get("html_url", f"{RELEASES_PAGE}/tag/{tag}"),
        "zipball_url": data.get("zipball_url", ""),
    }


# ── Git development checkouts ────────────────────────────────────────────────

def installation_is_git_checkout(app_dir: str | None = None) -> bool:
    """True when the installation itself is a Git working tree.

    Only the installation ROOT is checked — a project repository nested
    inside (like the loaded game's own repo in ``pokefirered/``) does not
    make the installation a checkout.  A ``.git`` FILE counts too (that is
    how git worktrees mark themselves).
    """
    app_dir = app_dir or _default_app_dir()
    return os.path.exists(os.path.join(app_dir, ".git"))


def _git_work_summary(app_dir: str) -> Optional[tuple[int, int]]:
    """(changed, untracked) file counts from ``git status``, or None when
    git is unavailable.  Purely informational — the refusal below never
    depends on it."""
    try:
        out = subprocess.run(
            ["git", "-C", app_dir, "status", "--porcelain"],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if out.returncode != 0:
            return None
        lines = [ln for ln in out.stdout.splitlines() if ln.strip()]
        untracked = sum(1 for ln in lines if ln.startswith("??"))
        return (len(lines) - untracked, untracked)
    except Exception:
        return None


def _refuse_if_git_checkout(app_dir: str) -> None:
    """The release-file updater NEVER runs over a Git source checkout —
    even a clean one.  Replacing tracked files with release copies can
    silently destroy uncommitted development work, and a 'clean' status
    moments ago proves nothing about the working tree by the time the
    apply script runs.  Developers update with git."""
    if not installation_is_git_checkout(app_dir):
        return
    summary = _git_work_summary(app_dir)
    detail = ""
    if summary:
        changed, untracked = summary
        if changed or untracked:
            detail = (f" Right now it has {changed} changed and "
                      f"{untracked} untracked file(s) that an update "
                      "would put at risk.")
    raise RuntimeError(
        "This PorySuite-Z installation is a Git source checkout (it "
        "contains a .git folder), so the built-in updater is disabled: "
        "replacing files with release copies could overwrite development "
        f"work.{detail}\n\n"
        "To update a source checkout, open a terminal in\n"
        f"  {app_dir}\n"
        "and run:  git pull")


# ── Small helpers ────────────────────────────────────────────────────────────

def _hash_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _pid_alive(pid) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if not _is_windows():
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    k32 = ctypes.windll.kernel32
    handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        if not k32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        k32.CloseHandle(handle)


def _is_reparse_point(path: str) -> bool:
    """Symlinks and junctions must never ride in on an update."""
    FILE_ATTRIBUTE_REPARSE_POINT = 0x400
    try:
        st = os.stat(path, follow_symlinks=False)
    except OSError:
        return False
    return bool(getattr(st, "st_file_attributes", 0)
                & FILE_ATTRIBUTE_REPARSE_POINT)


# Characters that cannot be embedded safely in a generated batch script
# (or are illegal/ambiguous in Windows file names).
_BAD_NAME_CHARS = set('%"<>|*?') | {chr(c) for c in range(32)}


def _validate_zip_member(name: str) -> None:
    """Refuse traversal, absolute paths, drive/stream colons, and names the
    generated batch scripts could not quote safely."""
    if name.startswith(("/", "\\")) or ":" in name:
        raise RuntimeError(
            "The update archive contains an unsafe absolute path and was "
            f"refused: {name!r}")
    for part in name.replace("\\", "/").split("/"):
        if part == "..":
            raise RuntimeError(
                "The update archive contains a path that climbs out of its "
                f"folder and was refused: {name!r}")
        if any(ch in _BAD_NAME_CHARS for ch in part):
            raise RuntimeError(
                "The update archive contains a file name with characters "
                f"that cannot be handled safely and was refused: {name!r}")


def _require_script_safe_path(path: str, what: str) -> None:
    """Paths embedded in the generated batch scripts: % expands even inside
    quotes, and a stray quote breaks every quoted argument after it."""
    if any(ch in path for ch in ('%', '"', '\n', '\r')):
        raise RuntimeError(
            f"The {what} path contains characters the Windows update "
            f"script cannot handle safely ({path}). Please move the "
            "installation to a folder without %% or quote characters.")


# ── Transaction state files ──────────────────────────────────────────────────

def read_update_phase(app_dir: str) -> Optional[str]:
    try:
        with open(os.path.join(app_dir, _PHASE_FILE), "r",
                  encoding="utf-8", errors="replace") as f:
            return f.read().strip() or None
    except OSError:
        return None


def _write_update_phase(app_dir: str, phase: str) -> None:
    with open(os.path.join(app_dir, _PHASE_FILE), "w",
              encoding="utf-8") as f:
        f.write(phase + "\n")


def read_update_journal(app_dir: str) -> Optional[dict]:
    try:
        with open(os.path.join(app_dir, _JOURNAL), "r",
                  encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def read_installed_manifest(app_dir: str) -> Optional[dict]:
    """The manifest of the last installed release: {rel_path: {sha256, size}}
    with forward-slash relative paths.  None when absent or unreadable
    (a legacy installation)."""
    try:
        with open(os.path.join(app_dir, _MANIFEST), "r",
                  encoding="utf-8") as f:
            data = json.load(f)
        files = data.get("files")
        return files if isinstance(files, dict) else None
    except (OSError, ValueError):
        return None


def _rmtree_force(path: str) -> None:
    """rmtree that also removes read-only files (a backup of a read-only
    installed file is itself read-only)."""
    def _clear_ro(func, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass
    try:
        shutil.rmtree(path, onexc=_clear_ro)        # Python 3.12+
    except TypeError:
        shutil.rmtree(path, onerror=_clear_ro)      # older signature
    except FileNotFoundError:
        pass


def _clean_update_artifacts(app_dir: str, *, logs: bool = False,
                            keep_lock: bool = False) -> None:
    """Remove the transaction's working files.  ``_update_preserved`` holds
    copies of the USER'S edited files and is never touched."""
    for name in (_STAGING_DIR, _BACKUP_DIR):
        p = os.path.join(app_dir, name)
        if os.path.isdir(p):
            _rmtree_force(p)
    names = [_APPLY_SCRIPT, _RECOVER_SCRIPT, _JOURNAL, _PHASE_FILE,
             _NOTICES_PENDING]
    if not keep_lock:
        names.append(_LOCK_FILE)
    if logs:
        names += [_ERROR_LOG, _NOTICES_LOG]
    for name in names:
        try:
            os.remove(os.path.join(app_dir, name))
        except OSError:
            pass


# ── Concurrency lock ─────────────────────────────────────────────────────────

def _acquire_update_lock(app_dir: str) -> None:
    path = os.path.join(app_dir, _LOCK_FILE)
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"pid": os.getpid()}, f)
            return
        except FileExistsError:
            holder = None
            try:
                with open(path, "r", encoding="utf-8") as f:
                    holder = json.load(f).get("pid")
            except (OSError, ValueError):
                pass
            if holder == os.getpid() or not _pid_alive(holder):
                try:
                    os.remove(path)   # our own, or a dead holder's — steal
                except OSError:
                    pass
                continue
            raise RuntimeError(
                "Another PorySuite-Z window is already preparing an update "
                "for this installation. Let it finish (or close it), then "
                "try again.")
    raise RuntimeError("Could not create the update lock file. Try again.")


def _release_update_lock(app_dir: str) -> None:
    try:
        os.remove(os.path.join(app_dir, _LOCK_FILE))
    except OSError:
        pass


# ── Failed-update / notices detection ────────────────────────────────────────

def consume_update_error(app_dir: str | None = None) -> Optional[str]:
    """If the last update left an ``_update_error.log``, return its text and
    remove the file; otherwise return None.  Lets the app tell the user a
    previous update did NOT fully apply instead of failing silently."""
    return _consume_text(app_dir or _default_app_dir(), _ERROR_LOG,
                         "The previous update did not fully apply.")


def consume_update_notices(app_dir: str | None = None) -> Optional[str]:
    """Non-fatal things the last update wants the user to know (edited
    files that were set aside, obsolete files that could not be removed)."""
    return _consume_text(app_dir or _default_app_dir(), _NOTICES_LOG, None)


def _consume_text(app_dir: str, name: str,
                  fallback: Optional[str]) -> Optional[str]:
    log_path = os.path.join(app_dir, name)
    if not os.path.isfile(log_path):
        return None
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read().strip()
    except OSError:
        return None
    try:
        os.remove(log_path)
    except OSError:
        pass
    return text or fallback


# ── Startup state check ──────────────────────────────────────────────────────

def startup_update_check(app_dir: str | None = None) -> Optional[dict]:
    """Inspect the installation for leftovers of a previous update and
    classify them.  Returns None when there is nothing to report, else a
    dict with ``kind`` and a plain-English ``message``:

    * ``error``            — a previous update failed; install is consistent
                             (the old version).  Message consumed.
    * ``notice``           — informational (preserved edits, kept files).
    * ``recovery_needed``  — an apply was interrupted mid-copy; the install
                             may be mixed.  ``recovery_script`` is the path
                             of ``_recover_update.bat`` when it exists.
    * ``rollback_failed``  — a rollback failed; the install may be mixed
                             and the backup is retained.  ``recovery_script``
                             and ``backup`` included when present.
    * ``foreign``          — the update files here target a DIFFERENT
                             installation (they travelled with a copy).
                             Nothing is touched.

    Safe cleanup (a crash after the update finished, or a prepared update
    that never ran) happens silently.
    """
    app_dir = app_dir or _default_app_dir()
    journal = read_update_journal(app_dir)
    phase = read_update_phase(app_dir)
    recover = os.path.join(app_dir, _RECOVER_SCRIPT)
    backup = os.path.join(app_dir, _BACKUP_DIR)

    if journal is not None and not _same_path(str(journal.get("target", "")),
                                              app_dir):
        return {
            "kind": "foreign",
            "message": (
                "This folder contains update files that belong to a "
                "DIFFERENT PorySuite-Z installation:\n"
                f"  they target: {journal.get('target', '(unknown)')}\n"
                f"  this install: {app_dir}\n"
                "They probably travelled along when the installation was "
                "copied, and they were not used. Delete these to clear "
                "this message: _update_journal.json, _update_phase.txt, "
                "_apply_update.bat, _recover_update.bat, _update_staging, "
                "_update_backup."),
        }

    if journal is None:
        # No transaction. Clear orphan state files, then surface any logs.
        if phase is not None:
            _clean_update_artifacts(app_dir)
        err = consume_update_error(app_dir)
        notices = consume_update_notices(app_dir)
        if err:
            if notices:
                err += "\n\n" + notices
            return {"kind": "error", "message": err}
        if notices:
            return {"kind": "notice", "message": notices}
        return None

    if phase in (None, PHASE_PREPARED):
        # Prepared but never applied — nothing was modified.
        _clean_update_artifacts(app_dir)
        return None

    if phase == PHASE_DONE:
        # Applied and verified; the script died during cleanup.
        _clean_update_artifacts(app_dir)
        notices = consume_update_notices(app_dir)
        if notices:
            return {"kind": "notice", "message": notices}
        return None

    if phase in (PHASE_APPLYING, PHASE_ROLLING_BACK):
        msg = ("A PorySuite-Z update was interrupted before it finished, "
               "so the installation may contain a mix of old and new "
               "files. The previous version will now be restored from the "
               "update's backup, and PorySuite-Z will restart.")
        if not os.path.isfile(recover):
            msg = ("A PorySuite-Z update was interrupted before it "
                   "finished, and the recovery script is missing. To "
                   "restore the previous version by hand, copy everything "
                   f"inside\n  {backup}\ninto\n  {app_dir}\n"
                   "replacing the files there.")
        return {"kind": "recovery_needed", "message": msg,
                "recovery_script": recover if os.path.isfile(recover) else None,
                "backup": backup if os.path.isdir(backup) else None}

    if phase == PHASE_ROLLED_BACK:
        err = consume_update_error(app_dir) or (
            "A previous update failed, and the previous PorySuite-Z "
            "version was restored successfully.")
        _clean_update_artifacts(app_dir)
        return {"kind": "error", "message": err}

    if phase == PHASE_ROLLBACK_FAILED:
        err = None
        try:
            with open(os.path.join(app_dir, _ERROR_LOG), "r",
                      encoding="utf-8", errors="replace") as f:
                err = f.read().strip()
        except OSError:
            pass
        msg = err or (
            "A previous update failed AND restoring the previous version "
            "failed, so the installation may contain a mix of old and new "
            f"files. Your previous files are saved in:\n  {backup}")
        return {"kind": "rollback_failed", "message": msg,
                "recovery_script": recover if os.path.isfile(recover) else None,
                "backup": backup if os.path.isdir(backup) else None}

    # Unknown phase value — be conservative: report, touch nothing.
    return {"kind": "recovery_needed",
            "message": ("A previous update left this installation in an "
                        f"unrecognized state ({phase}). Run "
                        "_recover_update.bat to restore the previous "
                        "version."),
            "recovery_script": recover if os.path.isfile(recover) else None,
            "backup": backup if os.path.isdir(backup) else None}


# ── Update planning ──────────────────────────────────────────────────────────

def _walk_release_files(src_dir: str) -> tuple[list[str], list[str]]:
    """All (files, dirs) of the staged release as forward-slash relative
    paths, excluding preserved top-level items and updater artifacts."""
    rel_files: list[str] = []
    rel_dirs: list[str] = []
    skip_top = PRESERVE | _UPDATER_ARTIFACTS
    for root, dirs, files in os.walk(src_dir):
        rel_root = os.path.relpath(root, src_dir)
        if rel_root == ".":
            dirs[:] = [d for d in dirs if d not in skip_top]
            files = [f for f in files if f not in skip_top]
        for d in sorted(dirs):
            rel = os.path.join(rel_root, d) if rel_root != "." else d
            rel_dirs.append(rel.replace(os.sep, "/"))
        for f in sorted(files):
            rel = os.path.join(rel_root, f) if rel_root != "." else f
            rel_files.append(rel.replace(os.sep, "/"))
    return rel_files, rel_dirs


def _topmost_dirs(rel_dirs: list[str]) -> list[str]:
    """Reduce a list of relative dirs to the topmost ones (drop any dir
    that lies under another dir in the list)."""
    out: list[str] = []
    for d in sorted(rel_dirs):
        if not any(d == t or d.startswith(t + "/") for t in out):
            out.append(d)
    return out


def _plan_update(app_dir: str, src_dir: str) -> dict:
    """Compare the staged release against the installation and decide what
    will be replaced, added, removed, preserved and kept.  Raises before
    anything is modified when the plan would clobber user-owned files."""
    manifest_old = read_installed_manifest(app_dir)
    lookup_old = ({k.replace("\\", "/").lower(): v
                   for k, v in manifest_old.items()}
                  if manifest_old is not None else None)
    rel_files, rel_dirs = _walk_release_files(src_dir)

    # A release folder/file whose install-side counterpart is the opposite
    # kind can never be applied safely — refuse before modifying anything.
    mismatches = [d for d in rel_dirs
                  if os.path.isfile(os.path.join(app_dir, d))]
    mismatches += [f for f in rel_files
                   if os.path.isdir(os.path.join(app_dir, f))]
    if mismatches:
        listing = "\n  ".join(mismatches[:10])
        raise RuntimeError(
            "The update cannot be applied: the new release has a folder "
            "where this installation has a file of the same name (or the "
            "other way round):\n  " + listing +
            "\nNothing was changed. Move or rename the item(s) above, "
            "then check for updates again.")

    replaced: list[str] = []
    added_files: list[str] = []
    preserved: list[str] = []
    conflicts: list[str] = []
    for rel in rel_files:
        dst = os.path.join(app_dir, rel)
        if os.path.isfile(dst):
            replaced.append(rel)
            if lookup_old is not None:
                rec = lookup_old.get(rel.lower())
                if rec is None:
                    conflicts.append(rel)
                elif _hash_file(dst) != rec.get("sha256"):
                    preserved.append(rel)
        else:
            added_files.append(rel)

    if conflicts:
        listing = "\n  ".join(conflicts[:10])
        more = ("" if len(conflicts) <= 10
                else f"\n  ... and {len(conflicts) - 10} more")
        raise RuntimeError(
            "Updating would overwrite files that are not part of the "
            "installed PorySuite-Z release — they look like files you "
            "created yourself:\n  " + listing + more +
            "\nNothing was changed. Move or rename these files, then "
            "check for updates again.")

    added_dirs = _topmost_dirs(
        [d for d in rel_dirs
         if not os.path.isdir(os.path.join(app_dir, d))])
    # Added files inside an added dir are removed with the dir on rollback.
    added_files_outside = [
        f for f in added_files
        if not any(f.startswith(t + "/") for t in added_dirs)]

    removed: list[str] = []
    kept_modified: list[str] = []
    if lookup_old is not None:
        new_set = {f.lower() for f in rel_files}
        for key, rec in lookup_old.items():
            if key in new_set:
                continue
            top = key.split("/", 1)[0]
            if top in {p.lower() for p in PRESERVE} or \
                    top in {a.lower() for a in _UPDATER_ARTIFACTS}:
                continue
            dst = os.path.join(app_dir, *key.split("/"))
            if not os.path.isfile(dst):
                continue
            if _hash_file(dst) == rec.get("sha256"):
                removed.append(key)
            else:
                kept_modified.append(key)

    # The manifest itself is part of the transaction so a rollback restores
    # (or removes) it along with everything else — a stale manifest after a
    # rollback would poison the NEXT update's ownership decisions.
    if os.path.isfile(os.path.join(app_dir, _MANIFEST)):
        replaced.append(_MANIFEST)
    else:
        added_files_outside.append(_MANIFEST)

    return {
        "replaced": replaced,
        "added_files": added_files_outside,
        "added_dirs": added_dirs,
        "removed": removed,
        "kept_modified": kept_modified,
        "preserved": preserved,
        "legacy": manifest_old is None,
    }


def _build_manifest(src_dir: str) -> dict:
    files = {}
    rel_files, _ = _walk_release_files(src_dir)
    for rel in rel_files:
        p = os.path.join(src_dir, rel)
        files[rel] = {"sha256": _hash_file(p), "size": os.path.getsize(p)}
    return {"format": 1, "created": datetime.now().isoformat(timespec="seconds"),
            "files": files}


# ── Preparation steps ────────────────────────────────────────────────────────

def _preflight_writable(app_dir: str) -> None:
    probe = os.path.join(app_dir, _PROBE)
    try:
        with open(probe, "w", encoding="utf-8") as f:
            f.write("probe")
        os.remove(probe)
    except OSError as e:
        raise RuntimeError(
            "PorySuite-Z cannot write into its own installation folder:\n"
            f"  {app_dir}\n({e})\n"
            "The update was not started. Check the folder's permissions "
            "(or run PorySuite-Z from a writable location).") from e


def _preflight_disk_space(app_dir: str, plan: dict) -> None:
    needed = 0
    for rel in plan["replaced"] + plan["removed"]:
        p = os.path.join(app_dir, *rel.split("/"))
        try:
            needed += os.path.getsize(p)
        except OSError:
            pass
    margin = 64 * 1024 * 1024
    free = shutil.disk_usage(app_dir).free
    if free < needed + margin:
        raise RuntimeError(
            "Not enough free disk space to back up the current version "
            f"before updating (about {(needed + margin) // (1024 * 1024)} MB "
            f"needed, {free // (1024 * 1024)} MB free). "
            "Nothing was changed. Free some space and try again.")


def _make_backup(app_dir: str, plan: dict) -> str:
    """Copy every file the update will replace or remove into
    ``_update_backup``, verifying each copy by hash.  Nothing in the
    installation is modified."""
    backup = os.path.join(app_dir, _BACKUP_DIR)
    if os.path.isdir(backup):
        shutil.rmtree(backup)
    os.makedirs(backup)
    for rel in plan["replaced"] + plan["removed"]:
        src = os.path.join(app_dir, *rel.split("/"))
        if not os.path.isfile(src):
            continue
        dst = os.path.join(backup, *rel.split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        if _hash_file(src) != _hash_file(dst):
            raise RuntimeError(
                f"The backup copy of {rel} does not match the original. "
                "The update was stopped before changing anything.")
    return backup


def _preserve_modified(app_dir: str, plan: dict) -> list[str]:
    """Set aside the user's edited copies of release-owned files that the
    update will replace.  Returns notice lines."""
    notices = []
    pres_root = os.path.join(app_dir, _PRESERVED_DIR)
    for rel in plan["preserved"]:
        src = os.path.join(app_dir, *rel.split("/"))
        dst = os.path.join(pres_root, *rel.split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        notices.append(
            f"You had edited {rel}. Your edited copy was saved to "
            f"{_PRESERVED_DIR}\\{rel} before the update replaced it with "
            "the release version.")
    for rel in plan["kept_modified"]:
        notices.append(
            f"{rel} was removed from the new release, but your copy is "
            "locally modified, so it was kept in place.")
    return notices


def _handle_leftover_state(app_dir: str) -> None:
    """Before preparing a new update: refuse when a previous transaction
    still needs recovery; otherwise clear stale leftovers."""
    journal = read_update_journal(app_dir)
    phase = read_update_phase(app_dir)
    if journal is not None and not _same_path(str(journal.get("target", "")),
                                              app_dir):
        raise RuntimeError(
            "This folder contains update files that belong to a different "
            f"PorySuite-Z installation ({journal.get('target', '?')}). "
            "They probably travelled along when the installation was "
            "copied. Delete _update_journal.json, _update_phase.txt, "
            "_apply_update.bat, _recover_update.bat, _update_staging and "
            "_update_backup, then check for updates again.")
    if phase in (PHASE_APPLYING, PHASE_ROLLING_BACK, PHASE_ROLLBACK_FAILED):
        raise RuntimeError(
            "A previous update was interrupted and must be recovered "
            "first. Restart PorySuite-Z (it will offer to restore the "
            "previous version), or run _recover_update.bat in the "
            "installation folder.")
    # Everything else is stale and safe to clear (PREPARED never modified
    # anything; DONE/ROLLED_BACK leftovers mean cleanup was interrupted).
    # keep_lock: the caller holds the update lock right now.
    _clean_update_artifacts(app_dir, logs=True, keep_lock=True)


# ── Download and install ─────────────────────────────────────────────────────

def download_and_install(zipball_url: str, progress_cb=None, *,
                         app_dir: str | None = None,
                         wait_pid: int | None = None) -> str:
    """Download the release zipball and stage it as a prepared transaction.

    Downloads the zip, extracts to a staging folder next to the app,
    backs up every file the update will touch, writes the journal and
    recovery script, then writes the apply script that runs after the app
    exits.  Returns a status message.  Raises on failure — and until the
    apply script actually runs, NOTHING in the installation is modified.

    ``app_dir`` / ``wait_pid`` exist so regression tests can drive
    disposable installations; production callers pass neither, and the
    destination is then the installation this module was imported from.
    """
    if not _is_windows():
        raise RuntimeError(
            "Automatic updates are only supported on Windows. "
            f"Please download the new version from {RELEASES_PAGE}")

    app_dir = app_dir or _default_app_dir()
    _refuse_if_git_checkout(app_dir)
    _require_script_safe_path(app_dir, "installation")
    _acquire_update_lock(app_dir)

    staging_dir = os.path.join(app_dir, _STAGING_DIR)
    try:
        _handle_leftover_state(app_dir)
        _preflight_writable(app_dir)

        if progress_cb:
            progress_cb("Downloading update...")

        req = urllib.request.Request(
            zipball_url,
            headers={"User-Agent": f"PorySuite-Z/{VERSION}",
                     "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            zip_data = resp.read()

        if progress_cb:
            progress_cb("Extracting...")

        # Extract to a staging directory inside the app folder
        # (not %TEMP% — that might be on a different drive)
        if os.path.exists(staging_dir):
            shutil.rmtree(staging_dir)
        os.makedirs(staging_dir)

        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            for info in zf.infolist():
                _validate_zip_member(info.filename)
            zf.extractall(staging_dir)

        # No symlink/junction may ride in on an update.
        for root, dirs, files in os.walk(staging_dir):
            for name in dirs + files:
                p = os.path.join(root, name)
                if _is_reparse_point(p):
                    raise RuntimeError(
                        "The update archive contains a symbolic link or "
                        f"junction and was refused: {p}")

        # GitHub zipballs have a top-level folder like Owner-Repo-hash/
        contents = os.listdir(staging_dir)
        if len(contents) == 1 and os.path.isdir(os.path.join(staging_dir,
                                                             contents[0])):
            src_dir = os.path.join(staging_dir, contents[0])
        else:
            src_dir = staging_dir
        _require_script_safe_path(src_dir, "downloaded update")

        # Strip anything the archive tried to plant under an updater
        # artifact name (a planted _apply_update.bat must never be copied).
        for name in _UPDATER_ARTIFACTS:
            p = os.path.join(src_dir, name)
            if os.path.isdir(p):
                shutil.rmtree(p)
            elif os.path.isfile(p):
                os.remove(p)

        # Structure sanity: this must look like a PorySuite-Z release.
        if not (os.path.isfile(os.path.join(src_dir, "app.py"))
                and os.path.isdir(os.path.join(src_dir, "core"))):
            raise RuntimeError(
                "The downloaded archive does not look like a PorySuite-Z "
                "release (app.py / core missing). The update was not "
                "started.")

        if progress_cb:
            progress_cb("Preparing update (planning + backup)...")

        plan = _plan_update(app_dir, src_dir)
        _preflight_disk_space(app_dir, plan)
        backup_dir = _make_backup(app_dir, plan)
        notices = _preserve_modified(app_dir, plan)
        if notices:
            with open(os.path.join(app_dir, _NOTICES_PENDING), "w",
                      encoding="utf-8") as f:
                f.write("\n".join(notices) + "\n")

        # Our manifest of the NEW release, written into staging so the
        # apply step installs it along with everything else.
        manifest = _build_manifest(src_dir)
        with open(os.path.join(src_dir, _MANIFEST), "w",
                  encoding="utf-8") as f:
            json.dump(manifest, f, indent=1)

        journal = {
            "format": 1,
            "target": app_dir,
            "staging": staging_dir,
            "src": src_dir,
            "backup": backup_dir,
            "created": datetime.now().isoformat(timespec="seconds"),
            "replaced": plan["replaced"],
            "added_files": plan["added_files"],
            "added_dirs": plan["added_dirs"],
            "removed": plan["removed"],
            "kept_modified": plan["kept_modified"],
            "preserved": plan["preserved"],
        }
        with open(os.path.join(app_dir, _JOURNAL), "w",
                  encoding="utf-8") as f:
            json.dump(journal, f, indent=1)
        _write_update_phase(app_dir, PHASE_PREPARED)

        if progress_cb:
            progress_cb("Preparing update scripts...")

        _write_recovery_script(app_dir, src_dir, plan)
        _write_update_script(app_dir, src_dir, plan,
                             pid=wait_pid if wait_pid is not None
                             else os.getpid())

    except Exception:
        # Nothing has been modified by THIS attempt — clear the
        # half-prepared transaction (_update_preserved is the user's data
        # and is kept).  But never clear what the refusal was ABOUT:
        # another installation's artifacts, or a transaction that still
        # needs recovery — those are evidence and recovery material.
        journal = read_update_journal(app_dir)
        phase = read_update_phase(app_dir)
        foreign = (journal is not None
                   and not _same_path(str(journal.get("target", "")),
                                      app_dir))
        needs_recovery = phase in (PHASE_APPLYING, PHASE_ROLLING_BACK,
                                   PHASE_ROLLBACK_FAILED)
        if not foreign and not needs_recovery:
            _clean_update_artifacts(app_dir)
        _release_update_lock(app_dir)
        raise

    return ("Update downloaded. PorySuite-Z will close and apply the update, "
            "then relaunch automatically.")


# ── Generated batch scripts ──────────────────────────────────────────────────

def _bat_relaunch_block(app_dir: str) -> str:
    # No ( ) blocks anywhere near embedded paths: an installation under a
    # path containing parentheses (C:\\Program Files (x86)\\...) would end
    # the block early and corrupt the script.
    launcher = os.path.join(app_dir, "LaunchPorySuite.bat")
    vbs = os.path.join(app_dir, "PorySuite.vbs")
    return f"""echo Relaunching PorySuite-Z...
REM Through the VBS when present (invisible, the normal launch path).
REM NEVER `start "" launcher.bat` directly: start runs .bat files with
REM cmd /K, which leaves a dead console window open forever.
if exist "{vbs}" goto :relaunch_vbs
start "" /MIN "%SYS32%\\cmd.exe" /c ""{launcher}" _hidden_"
goto :relaunch_done
:relaunch_vbs
start "" "%SYS32%\\wscript.exe" //nologo "{vbs}"
:relaunch_done"""


def _bat_tool_block() -> str:
    return """REM System tools by absolute path — the inherited PATH may resolve `find`
REM etc. to GNU lookalikes (Git/MSYS) with different behavior.
set "SYS32=%SystemRoot%\\System32"
set "ROBOCOPY=%SYS32%\\robocopy.exe\""""


def _write_update_script(app_dir: str, src_dir: str, plan: dict,
                         pid: int | None = None) -> str:
    """Write the apply .bat: wait for the app to exit, mark APPLYING, copy
    (failing FAST to rollback on the first error), verify every copy,
    remove obsolete release files, mark DONE, clean up, relaunch."""
    script_path = os.path.join(app_dir, _APPLY_SCRIPT)
    if pid is None:
        pid = os.getpid()

    top_items = sorted(os.listdir(src_dir))
    top_dirs = [i for i in top_items
                if os.path.isdir(os.path.join(src_dir, i))
                and i not in PRESERVE and i not in _UPDATER_ARTIFACTS]
    top_files = [i for i in top_items
                 if os.path.isfile(os.path.join(src_dir, i))
                 and i not in PRESERVE
                 and (i == _MANIFEST or i not in _UPDATER_ARTIFACTS)]

    # robocopy WITHOUT /MIR (/E copies subdirs but never deletes files the
    # release doesn't contain — user notes and uncommitted additions
    # survive).  Exit codes 0-7 are success for robocopy; 8+ is failure.
    # The FIRST failure aborts to :failed, which rolls everything back.
    copy_cmds, verify_cmds = [], []
    for n, item in enumerate(top_dirs):
        src = os.path.join(src_dir, item)
        dst = os.path.join(app_dir, item)
        copy_cmds.append(
            f'set "CURRENT_OP=copying {item}"\n'
            f'%ROBOCOPY% "{src}" "{dst}" /E /NFL /NDL /NJH /NJS /R:3 /W:1\n'
            'if %ERRORLEVEL% GEQ 8 goto :failed')
        # robocopy /L re-lists what a copy WOULD still do.  Exit code is a
        # bitmask: 1 = would copy (verification FAILURE), 2 = extra files in
        # the destination (the user's own files — fine, /XX only excludes
        # them from copying, the bit is still set), 4 = file/dir mismatch,
        # 8+ = errors.  Only 0 and 2 mean "verified".
        verify_cmds.append(
            f'set "CURRENT_OP=verifying {item}"\n'
            f'%ROBOCOPY% "{src}" "{dst}" /E /L /XX /NFL /NDL /NJH /NJS\n'
            f'if %ERRORLEVEL% EQU 0 goto :verify{n}_ok\n'
            f'if %ERRORLEVEL% EQU 2 goto :verify{n}_ok\n'
            'goto :failed\n'
            f':verify{n}_ok')
    for item in top_files:
        src = os.path.join(src_dir, item)
        dst = os.path.join(app_dir, item)
        copy_cmds.append(
            f'set "CURRENT_OP=copying {item}"\n'
            f'copy /Y "{src}" "{dst}" >nul 2>&1\n'
            'if errorlevel 1 goto :failed')
        verify_cmds.append(
            f'set "CURRENT_OP=verifying {item}"\n'
            f'"%SYS32%\\fc.exe" /B "{src}" "{dst}" >nul 2>&1\n'
            'if errorlevel 1 goto :failed')

    # Obsolete release files (manifest-owned, unmodified, absent from the
    # new release).  A failure to remove one is NOT fatal — the file simply
    # stays, and the user is told via the notices log.
    # NOTE: the >> redirect comes FIRST on these echo lines — a message that
    # happens to end in a digit would otherwise turn `2>>` into a stream
    # redirect.  And no ( ) blocks: paths may contain parentheses.
    remove_cmds = []
    pending_path = os.path.join(app_dir, _NOTICES_PENDING)
    for n, rel in enumerate(plan["removed"]):
        dst = os.path.join(app_dir, *rel.split("/"))
        remove_cmds.append(
            f'if not exist "{dst}" goto :rm{n}_done\n'
            f'if not exist "{dst}\\" goto :rm{n}_file\n'
            f'>>"{pending_path}" echo An obsolete release file is now a '
            f'folder and was left alone: {rel}\n'
            f'goto :rm{n}_done\n'
            f':rm{n}_file\n'
            f'del /F /Q "{dst}" >nul 2>&1\n'
            f'if not exist "{dst}" goto :rm{n}_done\n'
            f'>>"{pending_path}" echo An obsolete release file could not '
            f'be removed and was left in place: {rel}\n'
            f':rm{n}_done')

    copy_block = "\n".join(copy_cmds)
    verify_block = "\n".join(verify_cmds)
    remove_block = "\n".join(remove_cmds) if remove_cmds else "REM (none)"

    error_log = os.path.join(app_dir, _ERROR_LOG)
    phase = os.path.join(app_dir, _PHASE_FILE)
    pending = os.path.join(app_dir, _NOTICES_PENDING)
    notices_log = os.path.join(app_dir, _NOTICES_LOG)
    recover_bat = os.path.join(app_dir, _RECOVER_SCRIPT)
    staging = os.path.join(app_dir, _STAGING_DIR)
    backup = os.path.join(app_dir, _BACKUP_DIR)
    journal = os.path.join(app_dir, _JOURNAL)
    lock = os.path.join(app_dir, _LOCK_FILE)

    # NOTE: delays use `ping -n <seconds+1> 127.0.0.1` because `timeout`
    # errors out when stdin is redirected (e.g. launched from the app).
    bat_content = f"""@echo off
{_TARGET_MARKER}{app_dir}
{_STAGING_MARKER}{src_dir}
{_BACKUP_MARKER}{backup}
echo Waiting for PorySuite-Z to close...

{_bat_tool_block()}

REM Wait for the app process to exit (check every second, up to 30s)
set TRIES=0
:wait_loop
"%SYS32%\\tasklist.exe" /FI "PID eq {pid}" 2>nul | "%SYS32%\\find.exe" /I "{pid}" >nul
if errorlevel 1 goto :do_update
set /A TRIES+=1
if %TRIES% GEQ 30 goto :do_update
"%SYS32%\\ping.exe" -n 2 127.0.0.1 >nul
goto :wait_loop

:do_update
echo Applying update...
REM Small extra delay to ensure file handles are released
"%SYS32%\\ping.exe" -n 3 127.0.0.1 >nul

echo {PHASE_APPLYING}> "{phase}"
set "CURRENT_OP=starting"
{copy_block}

echo Verifying update...
{verify_block}

echo Removing obsolete release files...
{remove_block}

if exist "{pending}" move /Y "{pending}" "{notices_log}" >nul 2>&1
echo {PHASE_DONE}> "{phase}"

echo Cleaning up...
rmdir /S /Q "{staging}" >nul 2>&1
rmdir /S /Q "{backup}" >nul 2>&1
del /F /Q "{journal}" >nul 2>&1
del /F /Q "{recover_bat}" >nul 2>&1
del /F /Q "{lock}" >nul 2>&1
del /F /Q "{phase}" >nul 2>&1

{_bat_relaunch_block(app_dir)}
goto :remove_self

:failed
echo The update could not be fully applied.
> "{error_log}" echo The last PorySuite-Z update did NOT fully apply on %DATE% %TIME%.
>>"{error_log}" echo It failed while: %CURRENT_OP%
>>"{error_log}" echo The previous version is being restored from the update's backup.
>>"{error_log}" echo The downloaded update was kept in: {staging}
>>"{error_log}" echo Close every PorySuite-Z window and run Help ^> Check for Updates again.
del /F /Q "{pending}" >nul 2>&1
call "{recover_bat}"

:remove_self
REM Delete this script; (goto) ends the batch context so del can succeed
(goto) 2>nul & del /F /Q "%~f0"
"""
    with open(script_path, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(bat_content)
    return script_path


def _write_recovery_script(app_dir: str, src_dir: str, plan: dict) -> str:
    """Write the standalone rollback .bat.  It restores every backed-up
    file and deletes files/folders the update added.  It is called by the
    apply script on failure, by the app after detecting an interrupted
    update, and can be double-clicked by hand when the app cannot start."""
    script_path = os.path.join(app_dir, _RECOVER_SCRIPT)

    del_cmds = []
    for rel in plan["added_dirs"]:
        p = os.path.join(app_dir, *rel.split("/"))
        del_cmds.append(f'rmdir /S /Q "{p}" >nul 2>&1')
    for rel in plan["added_files"]:
        p = os.path.join(app_dir, *rel.split("/"))
        # The trailing backslash test keeps `del` away from a folder that
        # somehow appeared at an added-file path (del on a folder would
        # delete the folder's CONTENTS).
        del_cmds.append(f'if not exist "{p}\\" del /F /Q "{p}" >nul 2>&1')
    del_block = "\n".join(del_cmds) if del_cmds else "REM (none)"

    error_log = os.path.join(app_dir, _ERROR_LOG)
    phase = os.path.join(app_dir, _PHASE_FILE)
    backup = os.path.join(app_dir, _BACKUP_DIR)
    lock = os.path.join(app_dir, _LOCK_FILE)

    bat_content = f"""@echo off
{_TARGET_MARKER}{app_dir}
{_STAGING_MARKER}{src_dir}
{_BACKUP_MARKER}{backup}
echo PorySuite-Z update recovery

{_bat_tool_block()}

REM Optional first argument: a PID to wait for before recovering.
if "%~1"=="" goto :recover
echo Waiting for PorySuite-Z to close...
set TRIES=0
:wait_loop
"%SYS32%\\tasklist.exe" /FI "PID eq %~1" 2>nul | "%SYS32%\\find.exe" /I "%~1" >nul
if errorlevel 1 goto :recover
set /A TRIES+=1
if %TRIES% GEQ 30 goto :recover
"%SYS32%\\ping.exe" -n 2 127.0.0.1 >nul
goto :wait_loop

:recover
echo Restoring the previous PorySuite-Z version...
echo {PHASE_ROLLING_BACK}> "{phase}"
if not exist "{backup}\\" goto :rb_failed
%ROBOCOPY% "{backup}" "{app_dir}" /E /NFL /NDL /NJH /NJS /R:3 /W:1
if %ERRORLEVEL% GEQ 8 goto :rb_failed
echo Removing files the update had added...
{del_block}
echo {PHASE_ROLLED_BACK}> "{phase}"
>>"{error_log}" echo The previous PorySuite-Z version was restored successfully on %DATE% %TIME%.
>>"{error_log}" echo You can try the update again from Help ^> Check for Updates.
del /F /Q "{lock}" >nul 2>&1
goto :relaunch

:rb_failed
echo {PHASE_ROLLBACK_FAILED}> "{phase}"
>>"{error_log}" echo RESTORING THE PREVIOUS VERSION FAILED on %DATE% %TIME%.
>>"{error_log}" echo The installation may contain a mix of old and new files.
>>"{error_log}" echo Your previous files are saved in: {backup}
>>"{error_log}" echo To recover by hand: copy everything inside that folder back into
>>"{error_log}" echo   {app_dir}
>>"{error_log}" echo replacing the files there — or run _recover_update.bat again.

:relaunch
{_bat_relaunch_block(app_dir)}
exit /b 0
"""
    with open(script_path, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(bat_content)
    return script_path


# ── Script validation and launch ─────────────────────────────────────────────

def read_script_markers(script_path: str) -> tuple[Optional[str], Optional[str]]:
    """Return the (target, staging) paths recorded in an update script's
    marker lines, or (None, None) if the markers are missing (a script this
    version of the updater did not write)."""
    target = staging = None
    try:
        with open(script_path, "r", encoding="utf-8", errors="replace") as f:
            for _ in range(6):
                line = f.readline()
                if not line:
                    break
                if line.startswith(_TARGET_MARKER):
                    target = line[len(_TARGET_MARKER):].strip()
                elif line.startswith(_STAGING_MARKER):
                    staging = line[len(_STAGING_MARKER):].strip()
    except OSError:
        return (None, None)
    return (target, staging)


def _validate_update_script(script_path: str, app_dir: str) -> None:
    """Refuse to run an update script unless it was written by THIS
    installation for THIS installation and its staged files, backup and
    journal still exist.  Raises RuntimeError with a plain-English reason
    otherwise."""
    if not os.path.isfile(script_path):
        raise RuntimeError(
            "Update script not found — download may have failed")

    target, staging = read_script_markers(script_path)
    if not target or not staging:
        raise RuntimeError(
            "The pending update script does not say which installation it "
            "belongs to (it may be left over from an older version). "
            "It was not run. Delete _apply_update.bat and check for "
            "updates again.")

    if not _same_path(target, app_dir):
        raise RuntimeError(
            "The pending update script targets a different installation:\n"
            f"  script targets: {target}\n"
            f"  this install:   {app_dir}\n"
            "It was not run (it may have been copied here along with the "
            "installation). Delete _apply_update.bat and check for updates "
            "again.")

    if not os.path.isdir(staging):
        raise RuntimeError(
            "The downloaded update files are missing — download may have "
            "failed. Please check for updates again.")

    journal = read_update_journal(app_dir)
    phase = read_update_phase(app_dir)
    if journal is None or not _same_path(str(journal.get("target", "")),
                                         app_dir) or phase != PHASE_PREPARED:
        raise RuntimeError(
            "The pending update's journal is missing or stale, so it "
            "cannot be applied safely. Please check for updates again.")

    if not os.path.isdir(os.path.join(app_dir, _BACKUP_DIR)):
        raise RuntimeError(
            "The pending update's backup of your current files is missing, "
            "so it cannot be applied safely. Please check for updates "
            "again.")


def _validate_recovery_script(script_path: str, app_dir: str) -> None:
    if not os.path.isfile(script_path):
        raise RuntimeError(
            "The recovery script (_recover_update.bat) is missing. To "
            "restore the previous version by hand, copy everything inside "
            "_update_backup back into the installation folder.")
    target, _staging = read_script_markers(script_path)
    if not target:
        raise RuntimeError(
            "The recovery script does not say which installation it "
            "belongs to, so it was not run.")
    if not _same_path(target, app_dir):
        raise RuntimeError(
            "The recovery script targets a different installation:\n"
            f"  script targets: {target}\n"
            f"  this install:   {app_dir}\n"
            "It was not run (it may have been copied here along with the "
            "installation).")
    if not os.path.isdir(os.path.join(app_dir, _BACKUP_DIR)):
        raise RuntimeError(
            "The update's backup folder (_update_backup) is missing, so "
            "the previous version cannot be restored automatically.")


def _launch_script_detached(script_path: str, args: list[str]) -> None:
    subprocess.Popen(
        ["cmd", "/c", script_path, *args],
        creationflags=(getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
                       | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)),
        close_fds=True,
    )


def launch_update_and_exit():
    """Validate and launch the update batch script, then exit the app.

    Only runs a script that names THIS installation as its target — a
    stale or copied script aimed anywhere else raises instead of running.
    A Git source checkout refuses here too: already having downloaded the
    update never bypasses the developer-work safeguard.
    """
    app_dir = _default_app_dir()
    _refuse_if_git_checkout(app_dir)
    script_path = os.path.join(app_dir, _APPLY_SCRIPT)
    _validate_update_script(script_path, app_dir)

    # Launch the script detached so it survives our exit
    _launch_script_detached(script_path, [])
    # Exit the app — the script will wait for us to fully close
    sys.exit(0)


def launch_recovery_and_exit(app_dir: str | None = None):
    """Validate and launch the standalone recovery script (restoring the
    previous version from the update's backup), then exit the app."""
    app_dir = app_dir or _default_app_dir()
    script_path = os.path.join(app_dir, _RECOVER_SCRIPT)
    _validate_recovery_script(script_path, app_dir)
    _launch_script_detached(script_path, [str(os.getpid())])
    sys.exit(0)
