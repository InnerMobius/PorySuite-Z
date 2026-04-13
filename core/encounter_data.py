"""
core/encounter_data.py
Wild encounter data parser for the Pokédex Habitat / Area display.

Reads wild_encounters.json and builds a reverse lookup:
    species_const → list of EncounterRecord

All map/section names are parsed from the project — nothing is hardcoded.
Compatible with custom maps, swapped regions, and non-Kanto hacks.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List


# ─── Encounter method display names ─────────────────────────────────────────

_METHOD_NAMES = {
    "land_mons":       "Grass",
    "water_mons":      "Surfing",
    "rock_smash_mons": "Rock Smash",
    "fishing_mons":    "Fishing",
}

# Fishing sub-group display names (from the "groups" field in the JSON)
_FISHING_GROUP_NAMES = {
    "old_rod":   "Old Rod",
    "good_rod":  "Good Rod",
    "super_rod": "Super Rod",
}


@dataclass
class EncounterRecord:
    """One encounter entry: a species appears at a location via a method."""
    location: str           # friendly display name (e.g. "Route 1")
    map_const: str          # original MAP_ constant (e.g. "MAP_ROUTE_1")
    method: str             # display method (e.g. "Grass", "Old Rod")
    min_level: int
    max_level: int
    slot_count: int = 1     # how many slots this species occupies
    _table_id: str = ""     # internal: base_label for merge disambiguation


@dataclass
class EncounterDatabase:
    """Reverse lookup: species → encounter records."""
    # species_const → sorted list of EncounterRecord
    _data: Dict[str, List[EncounterRecord]] = field(default_factory=dict)

    def get(self, species_const: str) -> List[EncounterRecord]:
        return self._data.get(species_const, [])

    def all_species(self) -> List[str]:
        return sorted(self._data.keys())

    def is_empty(self) -> bool:
        return len(self._data) == 0


def _build_map_name_lookup(project_root: str) -> Dict[str, str]:
    """Build MAP_CONSTANT → friendly display name lookup.

    Strategy:
    1. Scan data/maps/*/map.json to get MAP_ID → region_map_section.
    2. Load region_map_sections.json to get MAPSEC_* → display name.
    3. Map MAP_ID → display name via the section.

    Falls back to a cleaned-up MAP_ constant if anything is missing —
    so custom maps without region map entries still show something useful.
    """
    maps_dir = os.path.join(project_root, "data", "maps")
    if not os.path.isdir(maps_dir):
        return {}

    # Step 1: Load region_map_sections.json for MAPSEC → name mapping
    section_names: Dict[str, str] = {}
    rms_path = os.path.join(
        project_root, "src", "data", "region_map",
        "region_map_sections.json")
    if os.path.isfile(rms_path):
        try:
            with open(rms_path, "r", encoding="utf-8") as f:
                rms_data = json.load(f)
            for entry in rms_data.get("map_sections", []):
                sec_id = entry.get("id", "")
                name = entry.get("name", "")
                if sec_id and name:
                    section_names[sec_id] = _title_case(name)
        except (json.JSONDecodeError, OSError):
            pass

    # Step 2: Scan map.json files to get MAP_ID → section mapping
    result: Dict[str, str] = {}
    try:
        for folder_name in os.listdir(maps_dir):
            map_json = os.path.join(maps_dir, folder_name, "map.json")
            if not os.path.isfile(map_json):
                continue
            try:
                with open(map_json, "r", encoding="utf-8") as f:
                    mdata = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue

            map_id = mdata.get("id", "")
            if not map_id:
                continue

            # Try region_map_section → name from sections.json
            section = mdata.get("region_map_section", "")
            if section and section in section_names:
                result[map_id] = section_names[section]
            else:
                # Fallback: convert folder name to friendly display
                # e.g. "ViridianForest" → "Viridian Forest"
                result[map_id] = _camel_to_spaced(folder_name)
    except OSError:
        pass

    return result


def _title_case(s: str) -> str:
    """Convert ALL CAPS or mixed case to title case.
    'PALLET TOWN' → 'Pallet Town', 'MT. EMBER' → 'Mt. Ember'
    """
    words = s.split()
    out = []
    for w in words:
        if len(w) <= 1:
            out.append(w.upper())
        elif w == w.upper():
            # ALL CAPS → Title Case, preserving periods
            out.append(w[0] + w[1:].lower())
        else:
            out.append(w)
    return " ".join(out)


def _camel_to_spaced(name: str) -> str:
    """Convert CamelCase folder name to spaced display name.
    'ViridianForest' → 'Viridian Forest'
    'MtMoon_1F' → 'Mt Moon 1F'
    'SSAnne_B1F' → 'SS Anne B1F'
    """
    # Replace underscores with spaces first
    s = name.replace('_', ' ')
    # Insert space before uppercase that follows a lowercase letter
    # (handles CamelCase without breaking consecutive uppercase like SS, B1F)
    s = re.sub(r'([a-z])([A-Z])', r'\1 \2', s)
    # Insert space before uppercase-lowercase pair preceded by uppercase
    # (handles 'SSAnne' → 'SS Anne' but not 'B1F')
    s = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1 \2', s)
    return s


def _map_const_to_friendly(const: str) -> str:
    """Last-resort fallback: MAP_ROUTE_1 → 'Route 1'."""
    name = const
    if name.startswith("MAP_"):
        name = name[4:]
    return _title_case(name.replace("_", " "))


def load_encounter_database(project_root: str) -> EncounterDatabase:
    """Parse wild_encounters.json and build the reverse species lookup.

    Handles:
    - Multiple encounter groups (FireRed/LeafGreen variants, etc.)
    - All encounter methods (land, water, rock smash, fishing)
    - Fishing sub-groups (Old Rod, Good Rod, Super Rod)
    - Duplicate maps (same species in multiple slots → merged)
    - Custom/modded maps with no region map entry (fallback names)
    """
    enc_path = os.path.join(
        project_root, "src", "data", "wild_encounters.json")
    if not os.path.isfile(enc_path):
        return EncounterDatabase()

    try:
        with open(enc_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return EncounterDatabase()

    # Build friendly map name lookup
    map_names = _build_map_name_lookup(project_root)

    # Parse encounter groups
    result: Dict[str, List[EncounterRecord]] = {}

    for group in data.get("wild_encounter_groups", []):
        if not group.get("for_maps"):
            continue

        # Get fishing sub-group slot ranges from the fields definition
        fishing_groups: Dict[str, List[int]] = {}
        for field_def in group.get("fields", []):
            if field_def.get("type") == "fishing_mons":
                fishing_groups = field_def.get("groups", {})

        for encounter in group.get("encounters", []):
            map_const = encounter.get("map", "")
            base_label = encounter.get("base_label", "")
            location = map_names.get(
                map_const, _map_const_to_friendly(map_const))

            for method_key, method_display in _METHOD_NAMES.items():
                method_data = encounter.get(method_key)
                if not method_data or not isinstance(method_data, dict):
                    continue

                mons = method_data.get("mons", [])
                if not mons:
                    continue

                if method_key == "fishing_mons" and fishing_groups:
                    _process_fishing(
                        result, mons, fishing_groups,
                        location, map_const, base_label)
                else:
                    _process_method(
                        result, mons, method_display,
                        location, map_const, base_label)

    # Sort each species' encounters: by location name, then method
    db = EncounterDatabase()
    for species, records in result.items():
        # Merge duplicates: same location + method → combine levels
        merged = _merge_records(records)
        merged.sort(key=lambda r: (r.location, r.method))
        db._data[species] = merged

    return db


def _process_method(
    result: Dict[str, List[EncounterRecord]],
    mons: list,
    method_display: str,
    location: str,
    map_const: str,
    base_label: str = "",
) -> None:
    """Process a list of encounter slots for a single method."""
    for mon in mons:
        species = mon.get("species", "")
        if not species or species == "SPECIES_NONE":
            continue
        min_lv = mon.get("min_level", 0)
        max_lv = mon.get("max_level", 0)
        result.setdefault(species, []).append(EncounterRecord(
            location=location,
            map_const=map_const,
            method=method_display,
            min_level=min_lv,
            max_level=max_lv,
            slot_count=1,
            _table_id=base_label,
        ))


def _process_fishing(
    result: Dict[str, List[EncounterRecord]],
    mons: list,
    fishing_groups: Dict[str, List[int]],
    location: str,
    map_const: str,
    base_label: str = "",
) -> None:
    """Process fishing encounter slots, splitting by rod type."""
    for group_key, slot_indices in fishing_groups.items():
        rod_name = _FISHING_GROUP_NAMES.get(group_key, group_key)
        for idx in slot_indices:
            if idx >= len(mons):
                continue
            mon = mons[idx]
            species = mon.get("species", "")
            if not species or species == "SPECIES_NONE":
                continue
            min_lv = mon.get("min_level", 0)
            max_lv = mon.get("max_level", 0)
            result.setdefault(species, []).append(EncounterRecord(
                location=location,
                map_const=map_const,
                method=rod_name,
                min_level=min_lv,
                max_level=max_lv,
                slot_count=1,
                _table_id=base_label,
            ))


def _merge_records(records: List[EncounterRecord]) -> List[EncounterRecord]:
    """Merge duplicate entries for the same species in two phases.

    Phase 1: Group by (map_const, method) — SUM slot_counts.
      This counts how many encounter slots the species occupies in a
      single encounter table (e.g. Pidgey in 5 of 12 grass slots).

    Phase 2: Group by (location, method) — MAX slot_count, widen levels.
      This collapses different floors (Icefall Cave 1F/B1F) and
      FireRed/LeafGreen variants into one display entry, keeping the
      highest per-table slot count rather than doubling it.
    """
    # Phase 1: sum slots within each encounter table
    per_table: Dict[tuple, EncounterRecord] = {}
    for r in records:
        key = (r._table_id or r.map_const, r.method)
        if key in per_table:
            existing = per_table[key]
            existing.min_level = min(existing.min_level, r.min_level)
            existing.max_level = max(existing.max_level, r.max_level)
            existing.slot_count += r.slot_count
        else:
            per_table[key] = EncounterRecord(
                location=r.location,
                map_const=r.map_const,
                method=r.method,
                min_level=r.min_level,
                max_level=r.max_level,
                slot_count=r.slot_count,
            )

    # Phase 2: merge across floors/versions by display location
    merged: Dict[tuple, EncounterRecord] = {}
    for r in per_table.values():
        key = (r.location, r.method)
        if key in merged:
            existing = merged[key]
            existing.min_level = min(existing.min_level, r.min_level)
            existing.max_level = max(existing.max_level, r.max_level)
            existing.slot_count = max(existing.slot_count, r.slot_count)
        else:
            merged[key] = EncounterRecord(
                location=r.location,
                map_const=r.map_const,
                method=r.method,
                min_level=r.min_level,
                max_level=r.max_level,
                slot_count=r.slot_count,
            )
    return list(merged.values())
