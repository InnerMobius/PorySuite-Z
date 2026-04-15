"""
Bridge Watcher — monitors porysuite_bridge.json for messages from Porymap.

Porymap's JS companion script writes JSON messages to this file whenever
something relevant happens (map opened, event selected, map saved, or the
user invokes Ctrl+E / Ctrl+Shift+E). This module watches the file and
emits Qt signals that PorySuite-Z editors connect to.

Only the signals actually consumed by the app are exposed — anything the
JS bridge doesn't emit, or that no PorySuite-Z editor listens for, is not
defined here. Adding unused signals is dead weight that obscures what the
bridge actually does.

Usage:
    watcher = BridgeWatcher(project_dir)
    watcher.map_opened.connect(my_handler)
    watcher.start()
    # ... later ...
    watcher.stop()
"""

import json
import os
import time

from PyQt6.QtCore import QObject, QFileSystemWatcher, pyqtSignal, QTimer


class BridgeWatcher(QObject):
    """Watches porysuite_bridge.json for messages from Porymap."""

    # Porymap opened a map, OR the user invoked Ctrl+Shift+E to re-sync.
    # Both deliver just the map name — that's all PorySuite-Z needs.
    map_opened     = pyqtSignal(str)
    sync_requested = pyqtSignal(str)

    # User clicked an event in Porymap's event list.
    event_selected = pyqtSignal(str, str, int, str, int, int)
    # (mapName, eventType, eventIndex, scriptLabel, x, y)

    # User pressed Ctrl+E over a tile in Porymap.
    edit_requested = pyqtSignal(str, int, int)
    # (mapName, x, y)

    # User saved the map in Porymap — PorySuite-Z should reload if viewing it.
    map_saved = pyqtSignal(str)

    def __init__(self, project_dir: str, parent=None):
        super().__init__(parent)
        self._project_dir = project_dir
        self._bridge_path = os.path.join(project_dir, "porysuite_bridge.json")
        self._watcher = None
        self._active = False
        self._last_timestamp = 0
        # Debounce rapid file changes (Porymap may write multiple times quickly)
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(50)
        self._debounce_timer.timeout.connect(self._read_bridge)

    def start(self):
        """Start watching the bridge file."""
        self._active = True
        # Record current time so we ignore stale messages from previous sessions.
        # Small leeway so a write that landed 1-2s before start() still counts.
        self._last_timestamp = int(time.time() * 1000) - 2000

        self._watcher = QFileSystemWatcher(parent=self)
        if os.path.isfile(self._bridge_path):
            self._watcher.addPath(self._bridge_path)
        else:
            # Watch the directory so we detect when the file is first created
            self._watcher.addPath(self._project_dir)

        self._watcher.fileChanged.connect(self._on_file_changed)
        self._watcher.directoryChanged.connect(self._on_dir_changed)

    def stop(self):
        """Stop watching."""
        self._active = False
        if self._watcher:
            self._watcher.deleteLater()
            self._watcher = None

    @property
    def bridge_path(self) -> str:
        return self._bridge_path

    def _on_file_changed(self, path: str):
        """Bridge file was modified — debounce then read."""
        if not self._active:
            return
        self._debounce_timer.start()
        # Re-add the path — QFileSystemWatcher drops it on some platforms
        # when the file is replaced rather than appended to.
        if self._watcher and path not in self._watcher.files():
            self._watcher.addPath(path)

    def _on_dir_changed(self, _path: str):
        """Directory changed — check if bridge file was created."""
        if not self._active:
            return
        if os.path.isfile(self._bridge_path):
            if self._watcher and self._bridge_path not in self._watcher.files():
                self._watcher.addPath(self._bridge_path)
            self._debounce_timer.start()

    def _read_bridge(self):
        """Read the bridge file and dispatch the message."""
        if not self._active or not os.path.isfile(self._bridge_path):
            return

        try:
            with open(self._bridge_path, "r", encoding="utf-8") as f:
                raw = f.read().strip()
            if not raw:
                return
            data = json.loads(raw)
        except (json.JSONDecodeError, OSError):
            return

        # Ignore stale messages (from a previous session or older than start)
        ts = data.get("timestamp", 0)
        if ts <= self._last_timestamp:
            return
        self._last_timestamp = ts

        self._dispatch(data.get("type", ""), data)

    def _dispatch(self, msg_type: str, data: dict):
        """Route a bridge message to the correct signal.

        Unknown message types are silently ignored — if the JS bridge adds
        a new message before the Python side is updated, that's fine.
        """
        try:
            if msg_type == "map_opened":
                self.map_opened.emit(data.get("map", ""))

            elif msg_type == "sync_request":
                self.sync_requested.emit(data.get("map", ""))

            elif msg_type == "event_selected":
                self.event_selected.emit(
                    data.get("map", ""),
                    data.get("eventType", ""),
                    data.get("eventIndex", 0),
                    data.get("script", ""),
                    data.get("x", 0),
                    data.get("y", 0),
                )

            elif msg_type == "edit_request":
                self.edit_requested.emit(
                    data.get("map", ""),
                    data.get("x", 0),
                    data.get("y", 0),
                )

            elif msg_type == "map_saved":
                self.map_saved.emit(data.get("map", ""))

        except Exception:
            # Never crash PorySuite-Z because of a bad bridge message
            pass
