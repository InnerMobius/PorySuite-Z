"""Project-wide script label index for fast lookup.

Scans all scripts.inc files across every map and data/scripts/*.inc
to build a searchable index of label names → source locations.
Lightweight: only extracts label names (lines ending with ::),
does NOT parse command bodies.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import NamedTuple


class ScriptLocation(NamedTuple):
    """Where a script label lives in the project."""
    label: str              # e.g. "PalletTown_EventScript_SignLady"
    map_name: str | None    # e.g. "PalletTown" (None for shared scripts)
    source_file: Path       # full path to the .inc/.s file
    source_type: str        # "map", "shared", or "event_scripts"


_LABEL_RE = re.compile(r'^([A-Za-z0-9_]+)::')


class ScriptIndex:
    """Fast project-wide script label index."""

    def __init__(self):
        self._entries: dict[str, ScriptLocation] = {}
        self._root: Path | None = None

    @property
    def count(self) -> int:
        return len(self._entries)

    # ── Build ─────────────────────────────────────────────────────────

    def build_index(self, root_dir: Path) -> int:
        """Scan the project and build the label index.

        Returns the number of labels found.
        """
        self._root = root_dir
        self._entries.clear()

        # 1. Map scripts: data/maps/*/scripts.inc
        maps_dir = root_dir / 'data' / 'maps'
        if maps_dir.is_dir():
            for map_folder in sorted(maps_dir.iterdir()):
                scripts_file = map_folder / 'scripts.inc'
                if scripts_file.is_file():
                    self._scan_file(
                        scripts_file,
                        map_name=map_folder.name,
                        source_type='map',
                    )

        # 2. Shared scripts: data/scripts/*.inc
        scripts_dir = root_dir / 'data' / 'scripts'
        if scripts_dir.is_dir():
            for inc_file in sorted(scripts_dir.glob('*.inc')):
                self._scan_file(
                    inc_file,
                    map_name=None,
                    source_type='shared',
                )

        # 3. Event scripts assembly: data/event_scripts.s
        event_scripts = root_dir / 'data' / 'event_scripts.s'
        if event_scripts.is_file():
            self._scan_file(
                event_scripts,
                map_name=None,
                source_type='event_scripts',
            )

        return len(self._entries)

    def _scan_file(self, path: Path, map_name: str | None,
                   source_type: str):
        """Extract all labels from a single file."""
        try:
            text = path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            return

        for line in text.splitlines():
            m = _LABEL_RE.match(line.strip())
            if m:
                label = m.group(1)
                # First occurrence wins (a label can't be in two places)
                if label not in self._entries:
                    self._entries[label] = ScriptLocation(
                        label=label,
                        map_name=map_name,
                        source_file=path,
                        source_type=source_type,
                    )

    # ── Query ─────────────────────────────────────────────────────────

    def search(self, query: str, limit: int = 200) -> list[ScriptLocation]:
        """Case-insensitive substring search on label names.

        Results are sorted: exact prefix matches first, then contains,
        alphabetical within each group.
        """
        if not query:
            return []

        q = query.lower()
        prefix_hits: list[ScriptLocation] = []
        contains_hits: list[ScriptLocation] = []

        for label, loc in self._entries.items():
            lower = label.lower()
            if lower.startswith(q):
                prefix_hits.append(loc)
            elif q in lower:
                contains_hits.append(loc)

        prefix_hits.sort(key=lambda loc: loc.label.lower())
        contains_hits.sort(key=lambda loc: loc.label.lower())

        results = prefix_hits + contains_hits
        return results[:limit]

    def get_location(self, label: str) -> ScriptLocation | None:
        """Direct lookup by exact label name."""
        return self._entries.get(label)

    def all_labels(self) -> list[str]:
        """Return all indexed label names, sorted."""
        return sorted(self._entries.keys())

    def map_name_for_label(self, label: str) -> str | None:
        """Return the map folder name for a label, or None if shared."""
        loc = self._entries.get(label)
        return loc.map_name if loc else None
