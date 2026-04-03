"""
Shared File Watcher — monitors project files that both PorySuite-Z and Porymap edit.

When Porymap saves a map, tileset, or layout change, this watcher detects the
file modification and emits signals so PorySuite-Z can prompt the user to reload.

Watched paths (relative to project root):
    data/maps/*/map.json         — map events, connections, header
    data/maps/*/scripts.inc      — map scripts (EVENTide)
    data/layouts/layouts.json    — layout definitions
    data/maps/map_groups.json    — map group structure
    src/data/wild_encounters.json — wild encounter tables
    src/data/heal_locations.h    — heal location table

The bridge watcher handles real-time signals from a patched Porymap, but this
file watcher serves as a safety net for stock Porymap and for cases where bridge
messages are missed (e.g., Porymap saves from File > Save rather than Ctrl+S).
"""

import os
import time

from PyQt6.QtCore import QObject, QFileSystemWatcher, pyqtSignal, QTimer


class SharedFileWatcher(QObject):
    """Watches shared project files for external modifications."""

    # Emitted when a map's map.json changes externally.
    # Args: map_folder (str) — e.g. "PalletTown"
    map_json_changed = pyqtSignal(str)

    # Emitted when a map's scripts.inc changes externally.
    # Args: map_folder (str)
    scripts_changed = pyqtSignal(str)

    # Emitted when data/layouts/layouts.json changes.
    layouts_changed = pyqtSignal()

    # Emitted when data/maps/map_groups.json changes.
    map_groups_changed = pyqtSignal()

    # Emitted when src/data/wild_encounters.json changes.
    wild_encounters_changed = pyqtSignal()

    # Emitted when src/data/heal_locations.h changes.
    heal_locations_changed = pyqtSignal()

    # Generic signal for any shared file change — carries the relative path.
    # This is handy for logging or showing a single "files changed" banner.
    file_changed = pyqtSignal(str)

    def __init__(self, project_dir: str, parent=None):
        super().__init__(parent)
        self._project_dir = project_dir
        self._watcher = None
        self._active = False

        # Track file modification times to avoid spurious signals
        # (QFileSystemWatcher can fire multiple times for a single save)
        self._mtimes: dict[str, float] = {}

        # Debounce: collect all changes in a short window, then emit once
        self._pending_changes: set[str] = set()
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(500)
        self._debounce_timer.timeout.connect(self._flush_changes)

        # Tracks files we ourselves saved (to suppress reload prompts)
        self._our_saves: set[str] = set()

    def start(self):
        """Start watching shared project files."""
        self._active = True
        self._watcher = QFileSystemWatcher(parent=self)
        self._watcher.fileChanged.connect(self._on_file_changed)
        self._watcher.directoryChanged.connect(self._on_dir_changed)

        # Register individual files and their containing directories
        self._register_paths()

    def stop(self):
        """Stop watching."""
        self._active = False
        if self._watcher:
            self._watcher.deleteLater()
            self._watcher = None

    def mark_our_save(self, rel_path: str):
        """Mark a file as being saved by PorySuite-Z (suppress reload prompt).

        Call this BEFORE writing a file so the watcher knows to ignore
        the resulting file-change notification.
        """
        norm = rel_path.replace("\\", "/")
        self._our_saves.add(norm)
        # Auto-clear after 2s in case the write never happens
        QTimer.singleShot(2000, lambda: self._our_saves.discard(norm))

    def _register_paths(self):
        """Register all shared paths with the QFileSystemWatcher."""
        root = self._project_dir

        # ── Standalone files ────────────────────────────────────────────────
        standalone = [
            os.path.join("data", "layouts", "layouts.json"),
            os.path.join("data", "maps", "map_groups.json"),
            os.path.join("src", "data", "wild_encounters.json"),
            os.path.join("src", "data", "heal_locations.h"),
        ]
        for rel in standalone:
            full = os.path.join(root, rel)
            if os.path.isfile(full):
                self._watcher.addPath(full)
                self._record_mtime(full)

        # ── Watch the maps directory for new/changed map.json and scripts.inc
        maps_dir = os.path.join(root, "data", "maps")
        if os.path.isdir(maps_dir):
            self._watcher.addPath(maps_dir)
            for entry in os.scandir(maps_dir):
                if entry.is_dir():
                    map_dir = entry.path
                    self._watcher.addPath(map_dir)
                    for filename in ("map.json", "scripts.inc"):
                        fpath = os.path.join(map_dir, filename)
                        if os.path.isfile(fpath):
                            self._watcher.addPath(fpath)
                            self._record_mtime(fpath)

        # ── Watch layouts directory for new layout files ────────────────────
        layouts_dir = os.path.join(root, "data", "layouts")
        if os.path.isdir(layouts_dir):
            self._watcher.addPath(layouts_dir)

    def _record_mtime(self, path: str):
        """Record the current mtime for a file."""
        try:
            self._mtimes[path] = os.path.getmtime(path)
        except OSError:
            self._mtimes[path] = 0

    def _on_file_changed(self, path: str):
        """A watched file was modified."""
        if not self._active:
            return

        # Check if mtime actually changed (filters duplicate signals)
        try:
            new_mtime = os.path.getmtime(path)
        except OSError:
            return  # File was deleted; ignore
        old_mtime = self._mtimes.get(path, 0)
        if new_mtime == old_mtime:
            return
        self._mtimes[path] = new_mtime

        # Re-add the path (some platforms remove after signal)
        if self._watcher and path not in self._watcher.files():
            self._watcher.addPath(path)

        self._pending_changes.add(path)
        self._debounce_timer.start()

    def _on_dir_changed(self, dir_path: str):
        """A watched directory changed — look for new files to register."""
        if not self._active or not self._watcher:
            return

        # Check if any new map.json/scripts.inc appeared
        maps_dir = os.path.join(self._project_dir, "data", "maps")
        if dir_path == maps_dir or os.path.dirname(dir_path) == maps_dir:
            # Re-scan for new map folders
            if os.path.isdir(maps_dir):
                for entry in os.scandir(maps_dir):
                    if entry.is_dir() and entry.path not in self._watcher.directories():
                        self._watcher.addPath(entry.path)
                        for filename in ("map.json", "scripts.inc"):
                            fpath = os.path.join(entry.path, filename)
                            if os.path.isfile(fpath) and fpath not in self._watcher.files():
                                self._watcher.addPath(fpath)
                                self._record_mtime(fpath)

    def _flush_changes(self):
        """Process all pending file changes after debounce window."""
        if not self._pending_changes:
            return

        changes = self._pending_changes.copy()
        self._pending_changes.clear()

        for path in changes:
            rel = os.path.relpath(path, self._project_dir).replace("\\", "/")

            # Check if this was our own save
            if rel in self._our_saves:
                self._our_saves.discard(rel)
                continue

            # Emit generic signal
            self.file_changed.emit(rel)

            # Emit specific signals based on path
            if rel.endswith("/map.json") and rel.startswith("data/maps/"):
                parts = rel.split("/")
                if len(parts) >= 4:
                    map_folder = parts[2]
                    self.map_json_changed.emit(map_folder)

            elif rel.endswith("/scripts.inc") and rel.startswith("data/maps/"):
                parts = rel.split("/")
                if len(parts) >= 4:
                    map_folder = parts[2]
                    self.scripts_changed.emit(map_folder)

            elif rel == "data/layouts/layouts.json":
                self.layouts_changed.emit()

            elif rel == "data/maps/map_groups.json":
                self.map_groups_changed.emit()

            elif rel == "src/data/wild_encounters.json":
                self.wild_encounters_changed.emit()

            elif rel == "src/data/heal_locations.h":
                self.heal_locations_changed.emit()
