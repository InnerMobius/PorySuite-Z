"""Voicegroup friendly labels — UI-only names stored per project.

Labels are saved to PorySuite's cache dir (not the pokefirered source).
Auto-generated from song table usage, but user-renamable.

File: {cache_dir}/voicegroup_labels.json
Format: {"voicegroup013": "Encounter Rocket / Route 1", ...}
"""

from __future__ import annotations

import json
import os
from typing import Optional


def _labels_path(project_dir: str) -> str:
    """Return the JSON file path for this project's VG labels."""
    from core.app_info import get_cache_dir
    return os.path.join(get_cache_dir(project_dir), "voicegroup_labels.json")


def load_labels(project_dir: str) -> dict[str, str]:
    """Load saved friendly labels. Returns {} if none exist."""
    path = _labels_path(project_dir)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_labels(project_dir: str, labels: dict[str, str]):
    """Write the label mapping to disk."""
    path = _labels_path(project_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(labels, f, indent=2, sort_keys=True)


def generate_labels_from_song_table(song_table) -> dict[str, str]:
    """Build friendly names from which songs use each voicegroup.

    Args:
        song_table: SongTableData with .entries list of SongEntry objects.

    Returns:
        {"voicegroup013": "encounter_rocket, rival_battle", ...}
    """
    # Map voicegroup number -> list of song labels
    vg_songs: dict[int, list[str]] = {}
    for entry in song_table.entries:
        if entry.voicegroup_index is not None:
            vg_songs.setdefault(entry.voicegroup_index, []).append(
                _shorten_song_name(entry.label))

    labels = {}
    for vg_num, songs in sorted(vg_songs.items()):
        vg_name = f"voicegroup{vg_num:03d}"
        if len(songs) == 1:
            labels[vg_name] = songs[0]
        elif len(songs) <= 3:
            labels[vg_name] = ", ".join(songs)
        else:
            labels[vg_name] = f"{songs[0]} + {len(songs) - 1} more"

    return labels


def _shorten_song_name(label: str) -> str:
    """Turn 'mus_encounter_rocket' into 'Encounter Rocket'."""
    name = label
    for prefix in ('mus_', 'se_', 'sfx_', 'me_'):
        if name.lower().startswith(prefix):
            name = name[len(prefix):]
            break
    return name.replace('_', ' ').title()


def get_display_name(vg_name: str, labels: dict[str, str]) -> str:
    """Return 'friendly label (voicegroupNNN)' or just the raw name."""
    friendly = labels.get(vg_name, '')
    if friendly:
        return f"{friendly}  ({vg_name})"
    return vg_name


def vg_name_from_display(display: str) -> str:
    """Extract 'voicegroupNNN' from a display string.

    Handles both 'Friendly Label  (voicegroup013)' and plain 'voicegroup013'.
    """
    if '(' in display and display.rstrip().endswith(')'):
        # Extract from parentheses
        start = display.rfind('(')
        return display[start + 1:-1].strip()
    return display.strip()
