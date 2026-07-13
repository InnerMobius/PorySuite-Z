"""
Heal-location data access — read/write ``src/data/heal_locations.json``.

Heal locations (Pokémon-center respawn points) are the single source of truth
for where the player heals + respawns after a blackout. pokefirered generates
``include/constants/heal_locations.h`` and ``src/data/heal_locations.h`` from
this JSON at build time, so editing the JSON is all that's needed.

Each entry:
  {
    "id":          "HEAL_LOCATION_PALLET_TOWN",   # constant name
    "map":         "MAP_PALLET_TOWN",             # where healing triggers
    "x": 6, "y": 8,                               #   + its coords
    "respawn_map": "MAP_..._PLAYERS_HOUSE_1F",    # where the player reappears
    "respawn_npc": "LOCALID_MOM"                  # NPC faced on respawn (or "0")
  }

Pure logic (no Qt) so it can be unit-tested.
"""

from __future__ import annotations

import json
import os
from typing import List, Dict, Tuple

_FIELDS = ("id", "map", "x", "y", "respawn_map", "respawn_npc")


def json_path(root: str) -> str:
    return os.path.join(root, "src", "data", "heal_locations.json")


def load(root: str) -> List[Dict]:
    """Return the list of heal-location dicts (empty list if missing/bad)."""
    p = json_path(root)
    if not os.path.isfile(p):
        return []
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    return list(data.get("heal_locations", []) or [])


def save(root: str, locations: List[Dict]) -> Tuple[bool, str]:
    """Write the heal locations back. Returns (ok, message)."""
    p = json_path(root)
    # Normalize: keep only known fields, ints for x/y, in a stable field order.
    clean = []
    for loc in locations:
        entry = {
            "id": str(loc.get("id", "")).strip(),
            "map": str(loc.get("map", "")).strip(),
            "x": int(loc.get("x", 0) or 0),
            "y": int(loc.get("y", 0) or 0),
            "respawn_map": str(loc.get("respawn_map", "")).strip(),
            "respawn_npc": str(loc.get("respawn_npc", "") or "0").strip(),
        }
        if not entry["id"] or not entry["map"]:
            continue  # skip incomplete rows
        clean.append(entry)
    try:
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"heal_locations": clean}, f, indent=2)
            f.write("\n")
    except Exception as e:
        return False, f"Could not write heal_locations.json: {e}"
    return True, f"Saved {len(clean)} heal location(s)."


def validate(root: str, locations: List[Dict],
             valid_maps: set) -> Dict[str, List[str]]:
    """Return ``{loc_id: [problem, ...]}`` for entries that reference a map
    constant the project no longer defines (the exact thing that breaks the
    build after a map rename). *valid_maps* is the set of known MAP_* names.
    """
    problems: Dict[str, List[str]] = {}
    for loc in locations:
        issues = []
        m = str(loc.get("map", "")).strip()
        rm = str(loc.get("respawn_map", "")).strip()
        if m and valid_maps and m not in valid_maps:
            issues.append(f"trigger map {m} does not exist")
        if rm and valid_maps and rm not in valid_maps:
            issues.append(f"respawn map {rm} does not exist")
        if issues:
            problems[str(loc.get("id", "?"))] = issues
    return problems
