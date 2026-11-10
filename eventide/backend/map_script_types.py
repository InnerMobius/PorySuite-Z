"""What map script moments a project actually has, read from its own source.

A map script table entry is ``map_script <TYPE>, <LABEL>``, and the engine
treats the types in two structurally different ways:

* Most are a plain script the engine runs straight off — ``ON_LOAD``,
  ``ON_TRANSITION``, ``ON_RESUME``, ``ON_RETURN_TO_FIELD``. Their target is an
  ordinary label ending in ``end``.
* ``ON_FRAME_TABLE`` and ``ON_WARP_INTO_MAP_TABLE`` point at ANOTHER table, of
  ``map_script_2 <VAR>, <VALUE>, <LABEL>`` rows terminated by ``.2byte 0``.
  The engine walks that table looking for a variable that currently holds the
  matching value, and runs that row's label.

Emitting the wrong shape for a type assembles cleanly, links cleanly, and then
silently does nothing in game — the worst kind of wrong, and invisible until
someone plays that map. So neither the list of types nor the shape each one
takes is written down here. Both are read from the project being edited:

* the types from ``include/constants/map_scripts.h``
* the shape from whichever engine source passes the type to
  ``MapHeaderCheckScriptTable``

A project that adds its own map script type therefore gets it offered without
needing a change to PorySuite, and a project that moves a type from direct to
table (or the reverse) is followed rather than second-guessed.

Nothing here writes. :func:`discover` reads and reports; building the table
entries is the caller's job.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

#: Where the type constants live, relative to the project root.
HEADER = 'include/constants/map_scripts.h'

#: Engine sources are searched for the table-dispatch call. Kept narrow on
#: purpose: a mention in a comment under ``docs/`` must not reclassify a type.
_ENGINE_DIRS = ('src',)

#: The call that means "this type's target is a table of ``map_script_2`` rows".
_TABLE_CALL = 'MapHeaderCheckScriptTable'

_DEF_RE = re.compile(
    r'^\s*#define\s+(MAP_SCRIPT_\w+)\s+(\d+)\s*(?://\s*(.*?))?\s*$')

#: Friendly names for the moments vanilla ships. A type that is NOT in here is
#: still offered — it just gets a name derived from its constant — because the
#: whole point of reading the header is to not depend on this list.
_FRIENDLY = {
    'MAP_SCRIPT_ON_LOAD': 'On Load',
    'MAP_SCRIPT_ON_FRAME_TABLE': 'On Frame',
    'MAP_SCRIPT_ON_TRANSITION': 'On Transition',
    'MAP_SCRIPT_ON_WARP_INTO_MAP_TABLE': 'On Warp In',
    'MAP_SCRIPT_ON_RESUME': 'On Resume',
    'MAP_SCRIPT_ON_DIVE_WARP': 'On Dive Warp',
    'MAP_SCRIPT_ON_RETURN_TO_FIELD': 'On Return to Field',
}

#: One plain-English line per moment, for the picker. Same rule as the names:
#: a type missing from here is still offered, just without a description.
_WHEN = {
    'MAP_SCRIPT_ON_LOAD':
        'Runs as the map loads, before anything is drawn. Use it to change '
        'tiles or set things up so the player never sees the "before" state.',
    'MAP_SCRIPT_ON_TRANSITION':
        'Runs when you arrive on the map, before the objects appear. Use it '
        'to move people to where this part of the story needs them, or to '
        'hide someone who should not be there yet.',
    'MAP_SCRIPT_ON_FRAME_TABLE':
        'Runs once the map is up and the player can see it, but only when a '
        'variable holds the value you choose. Use it for a scene that plays '
        'the moment you walk in.',
    'MAP_SCRIPT_ON_WARP_INTO_MAP_TABLE':
        'Runs when the player warps in (a door or stairs, not walking in), '
        'and only when a variable holds the value you choose.',
    'MAP_SCRIPT_ON_RESUME':
        'Runs every time the player comes back to the field — after a '
        'battle, after closing a menu, after the game reloads.',
    'MAP_SCRIPT_ON_RETURN_TO_FIELD':
        'Runs when the player returns to the field from somewhere that kept '
        'the map loaded underneath.',
    'MAP_SCRIPT_ON_DIVE_WARP':
        'Runs when the player surfaces or dives. FireRed does not use this.',
}


@dataclass(frozen=True)
class MapScriptType:
    """One moment the engine can run a map script at."""

    const: str
    value: int
    #: Friendly name, from :data:`_FRIENDLY` or derived from the constant.
    label: str
    #: True when the target is a table of ``map_script_2`` rows rather than a
    #: plain script. Read from the engine, never assumed.
    is_table: bool
    #: Whatever the header said after ``//`` on the define's own line, e.g.
    #: ``"Unused"``. Shown to the user as-is; the project's own note about its
    #: own engine is better than a guess from here.
    note: str = ''
    #: One plain-English line about when it fires, or '' if we have none.
    when: str = ''

    @property
    def unused(self) -> bool:
        """The project's own header calls this one unused."""
        return 'unused' in self.note.lower()

    @property
    def suffix(self) -> str:
        """The bit used to build a target label, e.g. ``OnTransition``.

        ``MAP_SCRIPT_ON_WARP_INTO_MAP_TABLE`` gives ``OnWarpIntoMap``: the
        trailing ``_TABLE`` is dropped because it describes the shape of the
        thing, not the moment, and vanilla's own labels leave it off
        (``<Map>_OnFrame``, not ``<Map>_OnFrameTable``).
        """
        core = self.const
        if core.startswith('MAP_SCRIPT_'):
            core = core[len('MAP_SCRIPT_'):]
        if core.endswith('_TABLE'):
            core = core[:-len('_TABLE')]
        return ''.join(p.capitalize() for p in core.split('_') if p)


def _header_text(root: Path) -> str:
    try:
        return (Path(root) / HEADER).read_text(encoding='utf-8',
                                               errors='replace')
    except OSError:
        return ''


def _table_consts(root: Path) -> set[str]:
    """Types the engine dispatches through :data:`_TABLE_CALL`.

    A type is called a table only on the strength of a real call in the
    engine. The alternative — assuming from the ``_TABLE`` in the name — would
    be guessing from a name, and a name is not a behaviour.
    """
    found: set[str] = set()
    call = re.compile(re.escape(_TABLE_CALL) + r'\s*\(\s*(MAP_SCRIPT_\w+)')
    for sub in _ENGINE_DIRS:
        base = Path(root) / sub
        if not base.is_dir():
            continue
        for path in base.rglob('*.c'):
            try:
                text = path.read_text(encoding='utf-8', errors='replace')
            except OSError:
                continue
            if _TABLE_CALL not in text:
                continue
            found.update(m.group(1) for m in call.finditer(text))
    return found


def discover(root) -> list[MapScriptType]:
    """Every map script moment this project has, in the header's own order.

    Returns an empty list when the header cannot be read — which is the
    honest answer for a project that has no map script constants, and lets the
    caller disable the feature with a reason instead of offering seven moments
    that may not exist.
    """
    text = _header_text(root)
    if not text:
        return []

    tables = _table_consts(root)
    out: list[MapScriptType] = []
    seen: set[str] = set()
    for line in text.splitlines():
        m = _DEF_RE.match(line)
        if not m:
            continue
        const, raw_value, note = m.group(1), m.group(2), (m.group(3) or '')
        if const in seen:
            continue
        seen.add(const)
        out.append(MapScriptType(
            const=const,
            value=int(raw_value),
            label=_FRIENDLY.get(const) or _derive_label(const),
            is_table=const in tables,
            note=note.strip(),
            when=_WHEN.get(const, ''),
        ))
    return out


def _derive_label(const: str) -> str:
    """A readable name for a type this tool has never heard of.

    ``MAP_SCRIPT_ON_MOON_RISE`` becomes ``On Moon Rise``. Deliberately plain:
    a project that invents a moment gets something usable in the picker
    without anyone having to add it here first.
    """
    core = const[len('MAP_SCRIPT_'):] if const.startswith('MAP_SCRIPT_') else const
    if core.endswith('_TABLE'):
        core = core[:-len('_TABLE')]
    return ' '.join(p.capitalize() for p in core.split('_') if p)


def by_const(root) -> dict[str, MapScriptType]:
    """:func:`discover` keyed by constant name, for lookups."""
    return {t.const: t for t in discover(root)}


# ─────────────────────────────────────────────────────────────────────────────
# Naming
# ─────────────────────────────────────────────────────────────────────────────

_ENTRY_RE = re.compile(
    r'^\s*map_script\s+(MAP_SCRIPT_\w+)\s*,\s*(\w+)\s*$', re.MULTILINE)


def observed_suffixes(root) -> dict[str, str]:
    """The label suffix this project already uses for each map script type.

    Derived by reading the entries the project has, not declared here. Vanilla
    names the target of ``MAP_SCRIPT_ON_WARP_INTO_MAP_TABLE`` ``<Map>_OnWarp``,
    which no rule about the constant's spelling would predict — and a project
    with its own convention deserves to have it followed rather than
    overridden by a new label in a different style sitting next to the old
    ones.

    Only suffixes that actually follow the map's own name are counted, so a
    shared or oddly-named target contributes nothing. Ties go to whichever is
    most common; :meth:`MapScriptType.suffix` covers anything unseen.
    """
    base = Path(root) / 'data' / 'maps'
    if not base.is_dir():
        return {}
    counts: dict[str, dict[str, int]] = {}
    for inc in base.glob('*/scripts.inc'):
        map_name = inc.parent.name
        try:
            text = inc.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        for const, target in _ENTRY_RE.findall(text):
            prefix = map_name + '_'
            if not target.startswith(prefix):
                continue  # shared or hand-named target — tells us nothing
            suffix = target[len(prefix):]
            if not suffix:
                continue
            counts.setdefault(const, {})
            counts[const][suffix] = counts[const].get(suffix, 0) + 1
    return {const: max(by_suffix.items(), key=lambda kv: kv[1])[0]
            for const, by_suffix in counts.items() if by_suffix}


def label_for(root, map_name: str, mst: MapScriptType,
              taken: set[str] | None = None) -> str:
    """A target label for a new *mst* entry on *map_name*.

    Follows the project's own naming where it has one, falls back to the
    constant's shape, and appends ``_2``, ``_3``… only if something already
    owns the name.
    """
    suffix = (observed_suffixes(root).get(mst.const) or mst.suffix
              or 'MapScript')
    stem = f'{map_name}_{suffix}'
    taken = taken or set()
    if stem not in taken:
        return stem
    n = 2
    while f'{stem}_{n}' in taken:
        n += 1
    return f'{stem}_{n}'
