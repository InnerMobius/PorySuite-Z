"""
Porymap Launcher — handles launching Porymap, writing its config, and process detection.

Responsible for:
- Detecting if Porymap is installed (compiled binary exists)
- Launching Porymap pointed at the current project and map
- Writing porymap.cfg (global — recent project) and porymap.user.cfg (per-project — recent map, custom scripts)
- Detecting if Porymap is already running and bringing it to front
- Auto-injecting the bridge script into a project's config on every open
"""

import os
import re
import subprocess
import ctypes
import logging

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
    """
    cfg_path = porymap_user_config_path(project_dir)
    data = _read_cfg(cfg_path)

    script_path = bridge_script_path().replace("\\", "/")
    existing_scripts = data.get("custom_scripts", "")

    # Porymap stores custom_scripts as: "path1:1,path2:1" where :1=enabled, :0=disabled
    if script_path in existing_scripts:
        # Clean up any old wrong-format entries (1:path instead of path:1)
        if f"1:{script_path}" in existing_scripts:
            existing_scripts = existing_scripts.replace(f"1:{script_path}", f"{script_path}")
            data["custom_scripts"] = existing_scripts
            _write_cfg(cfg_path, data)
        return  # Already registered

    # Append our script (enabled)
    entry = f"{script_path}:1"
    if existing_scripts:
        data["custom_scripts"] = f"{existing_scripts},{entry}"
    else:
        data["custom_scripts"] = entry

    _write_cfg(cfg_path, data)


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
