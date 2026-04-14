"""
core/updater.py
Auto-updater for PorySuite-Z.

Checks the GitHub releases API for new versions, shows a notification
dialog with the changelog, and can download + extract the update
in-place (preserving settings, data, and cache).
"""
from __future__ import annotations

import os
import io
import shutil
import zipfile
import tempfile
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
    "porymap",
    "porymap_src",
    "qt_sdk",
    "crashlogs",
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
    """Download the release zipball and extract it over the app directory.

    Returns a status message string.  Raises on failure.
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

    # Extract to a temp directory first
    tmp_dir = tempfile.mkdtemp(prefix="porysuite_update_")
    try:
        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            zf.extractall(tmp_dir)

        # GitHub zipballs have a top-level folder like Owner-Repo-hash/
        contents = os.listdir(tmp_dir)
        if len(contents) == 1 and os.path.isdir(os.path.join(tmp_dir, contents[0])):
            src_dir = os.path.join(tmp_dir, contents[0])
        else:
            src_dir = tmp_dir

        if progress_cb:
            progress_cb("Installing...")

        # Copy files over, skipping preserved items
        for item in os.listdir(src_dir):
            if item in PRESERVE:
                continue
            src = os.path.join(src_dir, item)
            dst = os.path.join(app_dir, item)
            if os.path.isdir(src):
                if os.path.exists(dst):
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)

    finally:
        # Clean up temp dir
        try:
            shutil.rmtree(tmp_dir)
        except Exception:
            pass

    return "Update installed successfully. Please restart PorySuite-Z."
