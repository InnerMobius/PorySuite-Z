"""
Porymap Launcher — handles launching Porymap, writing its config, and process detection.

Responsible for:
- Detecting if Porymap is installed (compiled binary exists)
- Launching Porymap pointed at the current project and map
- Writing porymap.cfg (global — recent project) and porymap.user.cfg (per-project — recent map, custom scripts)
- Detecting if Porymap is already running and bringing it to front
- Auto-injecting the bridge script into a project's config on every open
"""

import hashlib
import json as _json_mod
import os
import re
import subprocess
import ctypes
import logging
import urllib.request

from PyQt6.QtCore import QStandardPaths

log = logging.getLogger("porymap_launcher")


# ─── Path constants ──────────────────────────────────────────────────────────

def _porysuite_root() -> str:
    """Return the porysuite/ directory (parent of porymap_bridge/)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def porymap_exe_path() -> str:
    """Path to the compiled Porymap binary."""
    return os.path.join(_porysuite_root(), "porymap", "porymap.exe")


def porymap_source_path() -> str:
    """Path to the Porymap source repo (for patching/building)."""
    return os.path.join(_porysuite_root(), "porymap_src")


def bridge_script_path() -> str:
    """Path to our JS companion script."""
    return os.path.join(_porysuite_root(), "porymap_bridge", "porysuite_bridge.mjs")


def porymap_global_config_dir() -> str:
    """Porymap stores porymap.cfg in AppData/Local/pret/porymap/.

    Porymap sets org='pret', app='porymap', and uses QStandardPaths::AppDataLocation
    which resolves to AppData/Local/pret/porymap/ on Windows.
    """
    local_appdata = os.environ.get("LOCALAPPDATA",
                                   os.path.expanduser("~/AppData/Local"))
    return os.path.join(local_appdata, "pret", "porymap")


def porymap_global_config_path() -> str:
    return os.path.join(porymap_global_config_dir(), "porymap.cfg")


def porymap_user_config_path(project_dir: str) -> str:
    """Per-project config: <project_root>/porymap.user.cfg"""
    return os.path.join(project_dir, "porymap.user.cfg")


# ─── Install detection ───────────────────────────────────────────────────────

def is_porymap_installed() -> bool:
    """Check if the patched Porymap binary exists."""
    return os.path.isfile(porymap_exe_path())


def is_porymap_patched() -> bool:
    """Return True if the installed porymap.exe was built from our patched
    source. The installer drops a ``.psinstalled`` marker file next to the
    exe on success. If missing, the launcher treats the binary as stock
    Porymap and avoids passing patched-only CLI args or sending commands
    that the JS bridge can't handle.
    """
    exe = porymap_exe_path()
    if not os.path.isfile(exe):
        return False
    marker = os.path.join(os.path.dirname(exe), ".psinstalled")
    return os.path.isfile(marker)


def _exe_sha256(path: str) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def get_installed_porymap_info() -> dict:
    """Read the .psinstalled marker and return build info.

    Returns dict with keys: 'installed' (bool), 'patched' (bool),
    'built' (str, date), 'commit' (str, short hash),
    'exe_hash' (str, SHA-256 from marker), 'patches_intact' (bool).
    """
    info = {"installed": False, "patched": False, "built": "", "commit": "",
            "exe_hash": "", "patches_intact": False}
    exe = porymap_exe_path()
    if not os.path.isfile(exe):
        return info
    info["installed"] = True
    marker = os.path.join(os.path.dirname(exe), ".psinstalled")
    if not os.path.isfile(marker):
        return info
    info["patched"] = True
    try:
        with open(marker, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("built:"):
                    info["built"] = line.split(":", 1)[1].strip()
                elif line.startswith("commit:"):
                    info["commit"] = line.split(":", 1)[1].strip()[:10]
                elif line.startswith("exe_hash:"):
                    info["exe_hash"] = line.split(":", 1)[1].strip()
    except OSError:
        pass

    # Check if the binary has been replaced since we built it
    if info["exe_hash"]:
        current_hash = _exe_sha256(exe)
        info["patches_intact"] = (current_hash == info["exe_hash"])
    else:
        # Old marker without hash — fall back to modification time comparison
        try:
            exe_mtime = os.path.getmtime(exe)
            marker_mtime = os.path.getmtime(marker)
            # If exe is newer than our marker by more than 60s, it was replaced
            info["patches_intact"] = (exe_mtime <= marker_mtime + 60)
        except OSError:
            info["patches_intact"] = True  # Can't tell, assume OK

    return info


def _get_local_porymap_version() -> str:
    """Try to determine the installed Porymap version string.

    Checks CHANGELOG.md or RELEASE-README.txt in the porymap/ directory.
    Returns a version string like '6.3.1' or '' if unknown.
    """
    runtime = os.path.dirname(porymap_exe_path())

    # Try CHANGELOG.md — look for "## [X.Y.Z]" section headers (skip [Unreleased])
    changelog = os.path.join(runtime, "CHANGELOG.md")
    if os.path.isfile(changelog):
        try:
            with open(changelog, "r", encoding="utf-8") as f:
                for line in f:
                    # Match "## [6.3.0]" style headers (standard keepachangelog format)
                    m = re.match(r"^##\s+\[(\d+\.\d+\.\d+)\]", line)
                    if m:
                        return m.group(1)
        except OSError:
            pass

    return ""


def check_porymap_update_available() -> tuple:
    """Check if a newer Porymap release exists on GitHub.

    Uses the GitHub Releases API (HTTPS, no git required) so this
    works whether or not we have a .psinstalled marker with commit info.

    Returns (has_update: bool, local_version: str, remote_version: str).
    local/remote are version strings like '6.3.1' or '' if unknown.
    Returns (False, local, '') on network errors.
    """
    local = _get_local_porymap_version()
    info = get_installed_porymap_info()

    try:
        url = "https://api.github.com/repos/huderlem/porymap/releases/latest"
        req = urllib.request.Request(url, headers={
            "User-Agent": "PorySuite-Z",
            "Accept": "application/vnd.github.v3+json",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = _json_mod.loads(resp.read().decode("utf-8"))
            remote_tag = data.get("tag_name", "")  # e.g. "6.3.1"
            # Strip leading 'v' if present
            remote = remote_tag.lstrip("v") if remote_tag else ""

            if not remote:
                return (False, local, "")

            if not local:
                # Can't compare — just report what's available
                return (True, "unknown", remote)

            has_update = (remote != local)
            return (has_update, local, remote)
    except Exception as e:
        log.debug(f"GitHub releases API check failed: {e}")
        return (False, local, "")


def verify_patches_intact() -> dict:
    """Check whether our patched Porymap binary is still intact.

    Returns a dict:
      'status': 'not_installed' | 'stock' | 'patched_ok' | 'patches_replaced'
      'detail': human-readable explanation
    """
    exe = porymap_exe_path()
    if not os.path.isfile(exe):
        return {"status": "not_installed",
                "detail": "Porymap is not installed."}

    info = get_installed_porymap_info()
    if not info["patched"]:
        return {"status": "stock",
                "detail": "Porymap is installed but has no PorySuite patches. "
                          "Use Tools → Install Porymap to build a patched version."}

    if not info["patches_intact"]:
        return {"status": "patches_replaced",
                "detail": "Porymap appears to have been updated outside PorySuite "
                          "(the binary has changed since our build). "
                          "Bridge patches are likely missing. "
                          "Use Tools → Update Porymap to re-patch."}

    return {"status": "patched_ok",
            "detail": "Patched Porymap is installed and intact."}


# ─── Config writing ──────────────────────────────────────────────────────────

def _read_cfg(path: str) -> dict:
    """Read a Porymap key=value config file into a dict."""
    result = {}
    if not os.path.isfile(path):
        return result
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, _, value = line.partition("=")
                    result[key.strip()] = value.strip()
    except OSError:
        pass
    return result


def _write_cfg(path: str, data: dict):
    """Write a dict as a Porymap key=value config file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for key, value in data.items():
            f.write(f"{key}={value}\n")


def set_recent_project(project_dir: str):
    """Write the project path as Porymap's most recent project in porymap.cfg."""
    cfg_path = porymap_global_config_path()
    data = _read_cfg(cfg_path)

    # recent_project is a comma-separated list; put ours first
    existing = data.get("recent_project", "")
    projects = [p.strip() for p in existing.split(",") if p.strip()]
    # Normalize path for comparison
    norm = os.path.normpath(project_dir).replace("\\", "/")
    projects = [p for p in projects if os.path.normpath(p).replace("\\", "/") != norm]
    projects.insert(0, project_dir)
    data["recent_project"] = ",".join(projects[:10])

    # Ensure Porymap will auto-open the project on launch
    data["reopen_on_launch"] = "1"
    data["project_manually_closed"] = "0"

    _write_cfg(cfg_path, data)


def set_recent_map(project_dir: str, map_name: str):
    """Write the map name as Porymap's most recent map in porymap.user.cfg."""
    cfg_path = porymap_user_config_path(project_dir)
    data = _read_cfg(cfg_path)
    data["recent_map_or_layout"] = map_name
    _write_cfg(cfg_path, data)


def inject_bridge_script(project_dir: str):
    """Ensure our JS bridge script is registered in the project's porymap.user.cfg.

    This is the 'parasite' — every project the user opens automatically gets
    our bridge script injected into Porymap's custom scripts list.

    CRITICAL: This function MUST prune stale `porysuite_bridge.mjs` entries
    that point at a previous install location (e.g. an older versioned folder
    like `PorySuite-Z-0.0.4b/`). Porymap loads every script in `custom_scripts`
    and shows a "Failed to find script file" popup for any missing path. If a
    user upgrades by installing into a new folder, the stale entry must be
    replaced — not appended next to.
    """
    cfg_path = porymap_user_config_path(project_dir)
    data = _read_cfg(cfg_path)

    script_path = bridge_script_path().replace("\\", "/")
    existing_scripts = data.get("custom_scripts", "")

    # Porymap stores custom_scripts as: "path1:1,path2:1" where :1=enabled, :0=disabled.
    # Split, drop any entry whose path basename is porysuite_bridge.mjs (regardless
    # of where the parent folder is — that's the whole point), then re-append the
    # current install's path. Keep all third-party custom scripts untouched.
    BRIDGE_BASENAME = "porysuite_bridge.mjs"
    cleaned: list[str] = []
    pruned_any = False
    for raw in existing_scripts.split(","):
        entry = raw.strip()
        if not entry:
            continue
        # Each entry is "<path>:<0|1>" — but tolerate the legacy "1:<path>" form
        # this code used to emit.
        if entry.startswith("1:") or entry.startswith("0:"):
            path_part = entry[2:]
        elif entry.endswith(":1") or entry.endswith(":0"):
            path_part = entry[:-2]
        else:
            path_part = entry
        path_norm = path_part.replace("\\", "/")
        if path_norm.rsplit("/", 1)[-1].lower() == BRIDGE_BASENAME:
            # Drop every porysuite_bridge.mjs registration regardless of folder.
            # We re-add the canonical one below.
            pruned_any = True
            continue
        cleaned.append(entry)

    cleaned.append(f"{script_path}:1")
    new_value = ",".join(cleaned)

    if new_value != existing_scripts or pruned_any:
        data["custom_scripts"] = new_value
        _write_cfg(cfg_path, data)


def ensure_bridge_gitignored(project_dir: str):
    """Ensure PorySuite bridge files are ignored — LOCALLY ONLY.

    These files are ephemeral IPC artifacts that should never be committed:
    - porysuite_bridge.json  (Porymap → PorySuite messages)
    - porysuite_command.json (PorySuite → Porymap commands)
    - porymap.user.cfg       (per-project Porymap settings, user-specific)

    ── Why .git/info/exclude and not the project's .gitignore ──────────
    Writing to the tracked ``.gitignore`` shows up as "modified" on every
    fresh upstream pull: upstream doesn't have these entries, we append
    them on project open, and now every ``git status`` reports a dirty
    working tree even though the user never touched anything.  Users
    rightly complain that "Switch to Branch" refuses to run because their
    unedited project looks dirty.

    ``.git/info/exclude`` is git's dedicated file for *local-only* ignore
    rules.  It's not tracked, not committed, never appears in diffs, and
    is never overwritten by pull or checkout.  Any user who clones the
    project gets the bridge entries fresh on their first project open
    without ever seeing a phantom ``.gitignore`` modification.

    If a tracked ``.gitignore`` entry was added by an earlier version of
    PorySuite, we also scrub the auto-added block out now so upstream
    pulls stop showing ``.gitignore`` as modified.  Only the exact
    sentinel-wrapped block PorySuite wrote is removed — user-added lines
    nearby are untouched.
    """
    entries_needed = [
        "porysuite_bridge.json",
        "porysuite_command.json",
        "porymap.user.cfg",
    ]
    _SENTINEL_OPEN = "# PorySuite bridge files (auto-added, do not commit)"

    # ── Step 1: scrub any legacy block from the tracked .gitignore ──
    # Older builds appended to project/.gitignore with the sentinel above
    # followed by one line per entry. Remove only that exact shape so we
    # stop showing .gitignore as modified after every upstream pull.
    tracked_gi = os.path.join(project_dir, ".gitignore")
    if os.path.isfile(tracked_gi):
        try:
            with open(tracked_gi, "r", encoding="utf-8") as f:
                raw = f.read()
            if _SENTINEL_OPEN in raw:
                lines = raw.splitlines(keepends=True)
                cleaned: list[str] = []
                skipping = False
                for line in lines:
                    stripped = line.strip()
                    if not skipping and stripped == _SENTINEL_OPEN:
                        skipping = True
                        # Also drop one preceding blank line we added
                        if cleaned and cleaned[-1].strip() == "":
                            cleaned.pop()
                        continue
                    if skipping:
                        # Remove each bridge entry line immediately after
                        # the sentinel; stop once we hit a blank line or
                        # anything that isn't one of our entries.
                        if stripped in entries_needed:
                            continue
                        if stripped == "":
                            skipping = False
                            continue
                        # First non-entry line ends our block
                        skipping = False
                    cleaned.append(line)
                new_raw = "".join(cleaned)
                if new_raw != raw:
                    with open(tracked_gi, "w", encoding="utf-8", newline="") as f:
                        f.write(new_raw)
                    log.info(
                        f"Removed legacy PorySuite bridge block from {tracked_gi}"
                    )
        except OSError:
            pass

    # ── Step 2: make sure .git/info/exclude has the entries ──
    git_dir = os.path.join(project_dir, ".git")
    if not os.path.isdir(git_dir):
        # Not a git repo (or submodule weirdness) — nothing we can do,
        # bridge files will just appear as untracked, which is fine.
        return
    info_dir = os.path.join(git_dir, "info")
    try:
        os.makedirs(info_dir, exist_ok=True)
    except OSError:
        return
    exclude_path = os.path.join(info_dir, "exclude")

    existing = ""
    if os.path.isfile(exclude_path):
        try:
            with open(exclude_path, "r", encoding="utf-8") as f:
                existing = f.read()
        except OSError:
            return

    existing_lines = existing.splitlines()
    missing = [e for e in entries_needed if e not in existing_lines]
    if not missing:
        return

    try:
        with open(exclude_path, "a", encoding="utf-8") as f:
            if existing and not existing.endswith("\n"):
                f.write("\n")
            f.write(f"\n{_SENTINEL_OPEN}\n")
            for entry in missing:
                f.write(f"{entry}\n")
        log.info(
            f"Added {len(missing)} bridge entries to local-only "
            f"{exclude_path} (not tracked by git)"
        )
    except OSError:
        pass


# ─── Process detection ────────────────────────────────────────────────────────

def is_porymap_running() -> bool:
    """Check if porymap.exe is currently running."""
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq porymap.exe", "/NH"],
            capture_output=True, text=True, timeout=5,
        )
        return "porymap.exe" in result.stdout.lower()
    except (subprocess.SubprocessError, OSError):
        return False


def bring_porymap_to_front() -> bool:
    """Try to bring Porymap's window to the foreground on Windows."""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        EnumWindows = user32.EnumWindows
        GetWindowTextW = user32.GetWindowTextW
        SetForegroundWindow = user32.SetForegroundWindow
        IsWindowVisible = user32.IsWindowVisible
        ShowWindow = user32.ShowWindow
        SW_RESTORE = 9

        found = [False]

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def enum_callback(hwnd, _lparam):
            if IsWindowVisible(hwnd):
                buf = ctypes.create_unicode_buffer(256)
                GetWindowTextW(hwnd, buf, 256)
                title = buf.value
                if "porymap" in title.lower():
                    ShowWindow(hwnd, SW_RESTORE)
                    SetForegroundWindow(hwnd)
                    found[0] = True
                    return False  # Stop enumeration
            return True

        EnumWindows(enum_callback, 0)
        return found[0]
    except Exception:
        return False


# ─── Launch ───────────────────────────────────────────────────────────────────

def _send_command(project_dir: str, command: dict):
    """Write a command file that the bridge script polls and executes."""
    import json as _json
    cmd_path = os.path.join(project_dir, "porysuite_command.json")
    try:
        with open(cmd_path, "w", encoding="utf-8") as f:
            _json.dump(command, f)
    except OSError:
        pass


def _first_map_from_project(project_dir: str) -> str:
    """Read map_groups.json and return the first town/route map name."""
    import json as _json
    groups_path = os.path.join(project_dir, "data", "maps", "map_groups.json")
    try:
        with open(groups_path, "r", encoding="utf-8") as f:
            data = _json.load(f)
    except (OSError, ValueError):
        return ""
    # Prefer TownsAndRoutes group for a sensible default
    for group_name in data.get("group_order", []):
        if "towns" in group_name.lower() or "route" in group_name.lower():
            maps = data.get(group_name, [])
            if maps:
                return maps[0]
    # Otherwise grab the first map from the first non-empty group
    for group_name in data.get("group_order", []):
        maps = data.get(group_name, [])
        if maps:
            return maps[0]
    return ""


def launch_porymap(project_dir: str, map_name: str = "") -> bool:
    """Launch Porymap pointed at the given project and map.

    If Porymap is already running, brings it to front instead.
    Returns True if launch/focus succeeded.
    """
    log.info(f"launch_porymap called: project_dir={project_dir!r}, map_name={map_name!r}")

    if not is_porymap_installed():
        log.warning("Porymap not installed")
        return False

    patched = is_porymap_patched()
    if not patched:
        log.warning(
            "Porymap binary has no .psinstalled marker — treating as stock "
            "Porymap. Map-arg CLI and command-file navigation disabled.")

    # If already running, send a command to navigate to the right map
    if bring_porymap_to_front():
        log.info(f"Porymap already running, sending command: map={map_name!r}")
        if map_name and project_dir and patched:
            _send_command(project_dir, {"action": "openMap", "map": map_name})
        return True

    # If no map specified, pick a sensible default from the project
    if not map_name:
        map_name = _first_map_from_project(project_dir)
        log.info(f"No map specified, fallback picked: {map_name!r}")

    # Write config so Porymap opens to the right project/map
    set_recent_project(project_dir)
    if map_name:
        set_recent_map(project_dir, map_name)
        log.info(f"Wrote recent_map_or_layout={map_name!r} to porymap.user.cfg")

    # Ensure bridge script is registered
    inject_bridge_script(project_dir)

    # Launch with project dir as CLI argument + clean environment
    try:
        exe = porymap_exe_path()
        exe_dir = os.path.dirname(exe)
        env = os.environ.copy()
        # Filter PATH: remove mingw/msys directories to prevent DLL conflicts
        path_dirs = env.get("PATH", "").split(os.pathsep)
        clean_dirs = [d for d in path_dirs
                      if not any(x in d.lower() for x in
                                 ["mingw", "msys"])]
        # Ensure the exe's own directory is first so its DLLs are found
        env["PATH"] = os.pathsep.join([exe_dir] + clean_dirs)
        # Pass project dir + map name as CLI args (our patch to main.cpp
        # handles the second arg). Stock Porymap only understands the
        # project dir arg, so strip map_name when the marker is missing.
        cmd = [exe, project_dir]
        if map_name and patched:
            cmd.append(map_name)
        log.info(f"Launching: {cmd}")
        subprocess.Popen(
            cmd,
            cwd=exe_dir,
            env=env,
            creationflags=subprocess.DETACHED_PROCESS,
        )
        return True
    except OSError as e:
        log.error(f"Failed to launch Porymap: {e}")
        return False
