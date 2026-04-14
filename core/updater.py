"""
core/updater.py
Auto-updater for PorySuite-Z.

Checks the GitHub releases API for new versions, shows a notification
dialog with the changelog, and can download + extract the update
in-place (preserving settings, data, and cache).

The actual file replacement is done by a small batch script that runs
AFTER the app exits, since Windows locks files that are in use.
"""
from __future__ import annotations

import os
import io
import sys
import shutil
import zipfile
import tempfile
import subprocess
import urllib.request
import urllib.error
import json
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


# ── Version comparison ───────────────────────────────────────────────────────

def parse_version(v: str) -> tuple:
    """Parse a version string like '0.0.2b' into a comparable tuple.

    Strips leading 'v' and trailing letter suffix.  Returns something
    like (0, 0, 2, 'b') so that 0.0.2b > 0.0.1b.
    """
    v = v.strip().lstrip("v")
    # Split off trailing alpha suffix (e.g. 'b' for beta)
    suffix = ""
    while v and v[-1].isalpha():
        suffix = v[-1] + suffix
        v = v[:-1]
    parts = []
    for p in v.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    # Pad to at least 3 parts
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts) + (suffix,)


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


# ── Download and install ─────────────────────────────────────────────────────

def download_and_install(zipball_url: str, progress_cb=None) -> str:
    """Download the release zipball and stage it for install-on-exit.

    Downloads the zip, extracts to a staging folder next to the app,
    then writes a batch script that will copy files after the app exits
    and relaunch.  Returns a status message.  Raises on failure.
    """
    app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    if progress_cb:
        progress_cb("Downloading update...")

    # Download the zipball
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
    # (not %TEMP% — that might be on a different drive, making moves slow)
    staging_dir = os.path.join(app_dir, "_update_staging")
    if os.path.exists(staging_dir):
        shutil.rmtree(staging_dir)
    os.makedirs(staging_dir)

    try:
        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            zf.extractall(staging_dir)

        # GitHub zipballs have a top-level folder like Owner-Repo-hash/
        contents = os.listdir(staging_dir)
        if len(contents) == 1 and os.path.isdir(os.path.join(staging_dir, contents[0])):
            src_dir = os.path.join(staging_dir, contents[0])
        else:
            src_dir = staging_dir

        if progress_cb:
            progress_cb("Preparing update script...")

        # Build the list of items to copy (excluding preserved)
        items_to_copy = [item for item in os.listdir(src_dir)
                         if item not in PRESERVE and item != "_update_staging"]

        if not items_to_copy:
            raise RuntimeError("Update archive appears empty")

        # Write a batch script that waits for us to exit, then copies
        _write_update_script(app_dir, src_dir, items_to_copy)

    except Exception:
        # Clean up staging on failure
        try:
            shutil.rmtree(staging_dir)
        except Exception:
            pass
        raise

    return ("Update downloaded. PorySuite-Z will close and apply the update, "
            "then relaunch automatically.")


def _write_update_script(app_dir: str, src_dir: str, items: list[str]):
    """Write a .bat that waits for the app to exit, copies files, cleans up,
    and relaunches."""
    script_path = os.path.join(app_dir, "_apply_update.bat")
    pid = os.getpid()

    # Build xcopy/robocopy commands for each item
    copy_cmds = []
    for item in items:
        src = os.path.join(src_dir, item)
        dst = os.path.join(app_dir, item)
        if os.path.isdir(src):
            # robocopy mirrors the directory (/MIR), /NFL /NDL /NJH /NJS = quiet
            copy_cmds.append(
                f'robocopy "{src}" "{dst}" /MIR /NFL /NDL /NJH /NJS /R:3 /W:1'
            )
        else:
            copy_cmds.append(f'copy /Y "{src}" "{dst}" >nul 2>&1')

    copy_block = "\n".join(copy_cmds)

    bat_content = f"""@echo off
echo Waiting for PorySuite-Z to close...

REM Wait for the app process to exit (check every second, up to 30s)
set TRIES=0
:wait_loop
tasklist /FI "PID eq {pid}" 2>nul | find /I "{pid}" >nul
if errorlevel 1 goto :do_update
set /A TRIES+=1
if %TRIES% GEQ 30 goto :do_update
timeout /T 1 /NOBREAK >nul
goto :wait_loop

:do_update
REM Small extra delay to ensure file handles are released
timeout /T 2 /NOBREAK >nul

echo Applying update...
{copy_block}

echo Cleaning up...
rmdir /S /Q "{src_dir}" >nul 2>&1
REM Try to clean the staging dir (may fail if src_dir IS staging)
rmdir /S /Q "{os.path.join(app_dir, '_update_staging')}" >nul 2>&1

echo Relaunching PorySuite-Z...
start "" "{os.path.join(app_dir, 'LaunchPorySuite.bat')}" _hidden_

REM Delete this script (cmd trick: start a new cmd that deletes us)
start /MIN cmd /c "timeout /T 2 /NOBREAK >nul & del /F /Q \\"{script_path}\\" >nul 2>&1"
exit /b 0
"""
    with open(script_path, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(bat_content)


def launch_update_and_exit():
    """Launch the update batch script and exit the app."""
    app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script_path = os.path.join(app_dir, "_apply_update.bat")
    if not os.path.isfile(script_path):
        raise FileNotFoundError("Update script not found — download may have failed")

    # Launch the script detached so it survives our exit
    subprocess.Popen(
        ["cmd", "/c", script_path],
        creationflags=(subprocess.CREATE_NEW_CONSOLE
                       | subprocess.CREATE_NEW_PROCESS_GROUP),
        close_fds=True,
    )
    # Exit the app — the script will wait for us to fully close
    sys.exit(0)
