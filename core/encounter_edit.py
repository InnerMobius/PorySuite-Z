"""Editable, round-trip-faithful model of `src/data/wild_encounters.json`.

Separate from `core/encounter_data.py` (the read-only loader feeding the Pokédex
habitat panel). This one exists to *write*, so its discipline is:

* **Editing nothing changes nothing.** Dirtiness is tracked by the DATA, not by
  bytes, so a file another tool formatted differently (4-space, tabs, CRLF, no
  trailing newline) never reports dirty on load and is never rewritten unless
  the user actually changes an encounter. The file's own indent / newline style
  is detected and reproduced, so when a save IS needed the diff is minimal.
  `is_foreign_format` is exposed for the UI to warn when the file couldn't be
  reproduced exactly and a save would normalise it.

* **Categories are found by their `_mons` suffix, not a fixed list.** A hack may
  add its own (`hidden_mons`, …); convert/revert must carry every one, in the
  entry's own order, so nothing is dropped or reordered.

* **Time-of-day is PER-CATEGORY.** Each category (grass/surf/rock/fishing) is
  independently either a constant `{encounter_rate, mons}` table or a split
  `{ "time_of_day": [ {"phase": "morning", encounter_rate, mons}, … ] }`. So a
  cave can keep one constant water table while its grass varies by time, or a
  water-only spot can be constant while nothing else is. A category with no
  split, and any wholly-untouched entry, is byte-identical to vanilla.

  The per-phase set MUST be an array, not an object: the codegen (jsonproc/Inja)
  reorders object keys ALPHABETICALLY, which would emit the per-phase tables in
  the wrong order and silently send night encounters to the day table. Array
  position IS the table slot, matching the generated phase→slot map. Every split
  category on every map is kept in one canonical phase order so a single global
  slot map is valid.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from typing import List, Optional

# Canonical display/emit order for the categories vanilla knows. Only a
# presentation hint now — correctness uses the `_mons` suffix so unknown
# categories are never lost.
CATEGORY_ORDER = ("land_mons", "water_mons", "rock_smash_mons", "fishing_mons")

TIME_KEY = "time_of_day"
# A convenience marker the codegen reads: present on an entry iff ANY category
# is split, holding the map's phase order. It lets the template gate on one key
# and iterate the phases to emit the right number of headers, without hunting
# for whichever category happens to be the split one. Maintained automatically.
PHASES_KEY = "time_of_day_phases"


def _is_category(key: str) -> bool:
    return key.endswith("_mons")


class EncounterProject:
    """The whole encounter file, editable in place and saved faithfully."""

    def __init__(self, root: str, path: str, data: dict, raw: str):
        self.root = root
        self.path = path
        self.data = data
        self._raw = raw
        self._indent, self._trailing_nl, self._crlf = self._detect_format(raw)
        # Baseline for dirty-tracking: the file re-emitted in ITS OWN detected
        # format. Comparing against this (not against raw) means a formatting
        # difference never counts as an edit — only a real data change does.
        self._baseline = self._serialize()
        # If detection couldn't reproduce the file exactly, a save would
        # normalise its formatting. The UI can warn; we never do it silently on
        # a no-op (save() skips writing when the DATA is unchanged).
        self.is_foreign_format = (self._baseline != raw)

    # -- format detection / serialization -----------------------------------

    @staticmethod
    def _detect_format(raw: str):
        indent = 2
        m = re.search(r"\n([ \t]+)\S", raw)
        if m:
            ws = m.group(1)
            indent = "\t" if ws[0] == "\t" else len(ws)
        return indent, raw.endswith(("\n", "\r\n")), "\r\n" in raw

    def _serialize(self) -> str:
        s = json.dumps(self.data, indent=self._indent, ensure_ascii=False)
        if self._crlf:
            s = s.replace("\n", "\r\n")
        if self._trailing_nl:
            s += "\r\n" if self._crlf else "\n"
        return s

    # -- load / save --------------------------------------------------------

    @classmethod
    def load(cls, project_dir: str) -> "EncounterProject":
        path = os.path.join(project_dir, "src", "data", "wild_encounters.json")
        with open(path, encoding="utf-8", newline="") as fh:
            raw = fh.read()
        return cls(project_dir, path, json.loads(raw), raw)

    def is_dirty(self) -> bool:
        """True only when the DATA differs from what was loaded — never merely
        because the file's formatting differs from ours."""
        return self._serialize() != self._baseline

    def save(self) -> bool:
        """Write the file if (and only if) the data changed. Returns whether it
        wrote. Atomic (unique temp + fsync + replace)."""
        if not self.is_dirty():
            return False
        out = self._serialize()
        directory = os.path.dirname(self.path)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".wild_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
                fh.write(out)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        self._raw = out
        self._baseline = out
        return True

    # -- navigation ---------------------------------------------------------

    def map_groups(self) -> List[dict]:
        return self.data.get("wild_encounter_groups", [])

    def entries(self) -> List[dict]:
        """Every encounter entry across all for-maps groups, in file order.

        Non-`for_maps` groups (Battle-Pike-style, indexed not keyed by map) are
        excluded deliberately — they aren't per-map, so a per-map time-of-day
        editor has nothing to do with them. Vanilla firered has none.
        """
        out = []
        for group in self.map_groups():
            if group.get("for_maps"):
                out.extend(group.get("encounters", []))
        return out

    def entry_for_map(self, map_const: str) -> Optional[dict]:
        for e in self.entries():
            if e.get("map") == map_const:
                return e
        return None

    def field_ratios(self, cat_key: str) -> List[int]:
        """The engine's fixed per-slot weights for a category, for display.

        These live in the group's `fields` (global, not per-map). They define
        how many slots a category has and each slot's relative chance — the
        `Slot Ratio` / `Encounter Chance` columns are computed from them and are
        read-only (the engine hard-codes them; a per-map override doesn't exist).
        """
        for group in self.map_groups():
            if not group.get("for_maps"):
                continue
            for field in group.get("fields", []):
                if field.get("type") == cat_key:
                    return list(field.get("encounter_rates", []))
        return []

    def category_slot_count(self, cat_key: str) -> int:
        return len(self.field_ratios(cat_key))

    # -- time-of-day shape --------------------------------------------------

    @classmethod
    def is_time_aware(cls, entry: dict) -> bool:
        """A map is time-aware — the engine emits per-phase headers for it — if
        ANY of its categories is split by time of day."""
        return any(cls.is_category_split(entry.get(k))
                   for k in cls._category_keys(entry))

    @staticmethod
    def _category_keys(entry: dict) -> List[str]:
        """Category keys in the entry, in the entry's own order."""
        return [k for k in entry if _is_category(k)]

    @staticmethod
    def is_category_split(cat_value) -> bool:
        """Is this category a per-phase set rather than one constant table?"""
        return isinstance(cat_value, dict) and TIME_KEY in cat_value

    @classmethod
    def is_split(cls, entry: dict, cat_key: str) -> bool:
        return cls.is_category_split(entry.get(cat_key))

    @classmethod
    def phase_keys_of(cls, entry: dict) -> List[str]:
        """The phases used by this map's split categories, in table-slot order.

        Every split category on a map shares the same phase set, so the first
        one found answers for the map. Empty when nothing is split.
        """
        for k in cls._category_keys(entry):
            cv = entry.get(k)
            if cls.is_category_split(cv):
                return [ph.get("phase") for ph in cv[TIME_KEY]]
        return []

    @classmethod
    def category_table(cls, entry: dict, cat_key: str,
                       phase_key: Optional[str] = None) -> Optional[dict]:
        """The `{encounter_rate, mons}` table for a category.

        For a split category, the table for `phase_key` (or the first phase if
        omitted). For a constant category, the category itself. None if the map
        has no such category.
        """
        cv = entry.get(cat_key)
        if cv is None:
            return None
        if not cls.is_category_split(cv):
            return cv
        phases = cv[TIME_KEY]
        if phase_key is None:
            return phases[0] if phases else None
        for ph in phases:
            if ph.get("phase") == phase_key:
                return ph
        return None

    def split_category(self, entry: dict, cat_key: str,
                       phase_keys: List[str]) -> None:
        """Make ONE category vary by time of day, seeding every phase with a
        copy of its current table. Other categories are untouched — a cave can
        keep constant water while its grass varies, and vice versa.

        No-op if the category is absent or already split. `phase_keys` order is
        the table-slot order and must be the project's canonical phase order.
        """
        cv = entry.get(cat_key)
        if cv is None or self.is_category_split(cv):
            return
        if not phase_keys:
            raise ValueError("split_category needs at least one phase")
        base = {k: cv[k] for k in cv}          # {encounter_rate, mons}, in order
        phases = []
        for pk in phase_keys:
            block = {"phase": pk}
            block.update(json.loads(json.dumps(base)))
            phases.append(block)
        entry[cat_key] = {TIME_KEY: phases}
        self._sync_phases_key(entry)

    def merge_category(self, entry: dict, cat_key: str,
                       keep_phase_key: str) -> None:
        """Collapse ONE split category back to a single table (the kept phase).

        Raises if the phase doesn't exist — silently defaulting to empty would
        wipe the category. Restores the table's own key order so an unedited
        split→merge is byte-identical.
        """
        cv = entry.get(cat_key)
        if not self.is_category_split(cv):
            return
        block = None
        for ph in cv[TIME_KEY]:
            if ph.get("phase") == keep_phase_key:
                block = ph
                break
        if block is None:
            raise KeyError(
                "phase %r is not one of %s's phases (%s); refusing to merge, "
                "which would wipe the table."
                % (keep_phase_key, cat_key,
                   ", ".join(p.get("phase") for p in cv[TIME_KEY])))
        entry[cat_key] = {k: block[k] for k in block if k != "phase"}
        self._sync_phases_key(entry)

    def _sync_phases_key(self, entry: dict) -> None:
        """Keep `PHASES_KEY` present iff the entry has any split category. Placed
        at the end so a split→merge round-trip removes it and restores the exact
        original byte layout."""
        phases = self.phase_keys_of(entry)
        if phases:
            entry[PHASES_KEY] = phases      # add-at-end or update-in-place
        else:
            entry.pop(PHASES_KEY, None)

    def add_category(self, entry: dict, cat_key: str, slot_count: int,
                     default_rate: int = 0) -> None:
        """Add an empty constant category with `slot_count` blank slots, placed
        in canonical order. No-op if it already exists."""
        if cat_key in entry:
            return
        table = {"encounter_rate": default_rate,
                 "mons": [{"min_level": 1, "max_level": 1,
                           "species": "SPECIES_NONE"} for _ in range(slot_count)]}
        # Rebuild the entry so the new category lands in canonical order.
        order = list(CATEGORY_ORDER)
        rebuilt = {k: v for k, v in entry.items() if not _is_category(k)}
        present = {k: entry[k] for k in self._category_keys(entry)}
        present[cat_key] = table
        for k in order:
            if k in present:
                rebuilt[k] = present[k]
        entry.clear()
        entry.update(rebuilt)

    def remove_category(self, entry: dict, cat_key: str) -> None:
        """Remove a category entirely (constant or split)."""
        entry.pop(cat_key, None)
