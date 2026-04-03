"""
Bridge Watcher — monitors the porysuite_bridge.json file for messages from Porymap.

Porymap's JS companion script writes JSON messages to this file whenever something
happens (map opened, event selected, etc.). This module watches the file and emits
Qt signals that PorySuite-Z editors connect to.

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

    # ── Map & Navigation signals ────────────────────────────────────────────
    map_opened = pyqtSignal(str, dict, dict, list)
    # (mapName, header, tilesets, connections)

    tab_changed = pyqtSignal(int)
    # (tabIndex)  0=Map, 1=Events, 2=Header, 3=Connections, 4=WildPokemon

    sync_requested = pyqtSignal(str, dict, dict, list)
    # (mapName, header, tilesets, connections)

    project_opened = pyqtSignal(str)
    # (projectPath)

    project_closed = pyqtSignal()

    # ── Event signals ───────────────────────────────────────────────────────
    event_selected = pyqtSignal(str, str, int, str, int, int)
    # (mapName, eventType, eventIndex, scriptLabel, x, y)

    event_created = pyqtSignal(str, str, int)
    # (mapName, eventType, eventIndex)

    event_deleted = pyqtSignal(str, str, int)
    # (mapName, eventType, eventIndex)

    event_moved = pyqtSignal(str, str, int, int, int, int, int)
    # (mapName, eventType, eventIndex, oldX, oldY, newX, newY)

    edit_requested = pyqtSignal(str, int, int)
    # (mapName, x, y)

    # ── Map data change signals ─────────────────────────────────────────────
    map_saved = pyqtSignal(str)
    # (mapName)

    layout_saved = pyqtSignal(str)
    # (layoutId)

    connection_changed = pyqtSignal(str, str, str)
    # (mapName, direction, targetMap)

    wild_encounters_saved = pyqtSignal(str)
    # (mapName)

    heal_location_changed = pyqtSignal(str, int, int)
    # (mapName, x, y)

    header_changed = pyqtSignal(str, str, str)
    # (mapName, property, value)

    tileset_changed = pyqtSignal(str, str, str)
    # (mapName, primaryTileset, secondaryTileset)

    tileset_updated = pyqtSignal(str)
    # (tilesetName)

    map_resized = pyqtSignal(str, int, int, dict)
    # (mapName, oldWidth, oldHeight, delta)

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
        # Record current time so we ignore stale messages from previous sessions
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
        # Re-add the path (QFileSystemWatcher removes it after a signal on some platforms)
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

        # Ignore stale messages (from a previous session or >2s old)
        ts = data.get("timestamp", 0)
        if ts <= self._last_timestamp:
            return
        self._last_timestamp = ts

        msg_type = data.get("type", "")
        self._dispatch(msg_type, data)

    def _dispatch(self, msg_type: str, data: dict):
        """Route a bridge message to the correct signal."""
        try:
            if msg_type == "project_opened":
                self.project_opened.emit(data.get("project", ""))

            elif msg_type == "project_closed":
                self.project_closed.emit()

            elif msg_type == "map_opened":
                self.map_opened.emit(
                    data.get("map", ""),
                    data.get("header", {}),
                    data.get("tilesets", {}),
                    data.get("connections", []),
                )

            elif msg_type == "tab_changed":
                self.tab_changed.emit(data.get("tab", 0))

            elif msg_type == "sync_request":
                self.sync_requested.emit(
                    data.get("map", ""),
                    data.get("header", {}),
                    data.get("tilesets", {}),
                    data.get("connections", []),
                )

            elif msg_type == "event_selected":
                self.event_selected.emit(
                    data.get("map", ""),
                    data.get("eventType", ""),
                    data.get("eventIndex", 0),
                    data.get("script", ""),
                    data.get("x", 0),
                    data.get("y", 0),
                )

            elif msg_type == "event_created":
                self.event_created.emit(
                    data.get("map", ""),
                    data.get("eventType", ""),
                    data.get("eventIndex", 0),
                )

            elif msg_type == "event_deleted":
                self.event_deleted.emit(
                    data.get("map", ""),
                    data.get("eventType", ""),
                    data.get("eventIndex", 0),
                )

            elif msg_type == "event_moved":
                self.event_moved.emit(
                    data.get("map", ""),
                    data.get("eventType", ""),
                    data.get("eventIndex", 0),
                    data.get("oldX", 0),
                    data.get("oldY", 0),
                    data.get("newX", 0),
                    data.get("newY", 0),
                )

            elif msg_type == "edit_request":
                self.edit_requested.emit(
                    data.get("map", ""),
                    data.get("x", 0),
                    data.get("y", 0),
                )

            elif msg_type == "map_saved":
                self.map_saved.emit(data.get("map", ""))

            elif msg_type == "layout_saved":
                self.layout_saved.emit(data.get("layout", ""))

            elif msg_type == "connection_changed":
                self.connection_changed.emit(
                    data.get("map", ""),
                    data.get("direction", ""),
                    data.get("target", ""),
                )

            elif msg_type == "wild_encounters_saved":
                self.wild_encounters_saved.emit(data.get("map", ""))

            elif msg_type == "heal_location_changed":
                self.heal_location_changed.emit(
                    data.get("map", ""),
                    data.get("x", 0),
                    data.get("y", 0),
                )

            elif msg_type == "header_changed":
                self.header_changed.emit(
                    data.get("map", ""),
                    data.get("property", ""),
                    str(data.get("value", "")),
                )

            elif msg_type == "tileset_changed":
                self.tileset_changed.emit(
                    data.get("map", ""),
                    data.get("primary", ""),
                    data.get("secondary", ""),
                )

            elif msg_type == "tileset_updated":
                self.tileset_updated.emit(data.get("tileset", ""))

            elif msg_type == "map_resized":
                self.map_resized.emit(
                    data.get("map", ""),
                    data.get("oldWidth", 0),
                    data.get("oldHeight", 0),
                    data.get("delta", {}),
                )

        except Exception:
            # Never crash PorySuite-Z because of a bad bridge message
            pass
