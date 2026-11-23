"""The list of repairs the app offers, in one place.

Why this exists
---------------
PorySuite-Z has shipped bugs that wrote wrong bytes into the user's game
files. Fixing the code that wrote them does nothing for the files already on
disk, so each of those bugs needs a repair the user can run — and there will be
more of them over time.

Each one used to get its own entry in the Tools menu. Two of them were already
there ("Repair Map Script Damage…", "Repair Overflowing Dialogue…") and the
menu was on its way to being a list of repairs with the actual tools buried
among them. They now live behind a single Tools → Repair… window, and this
module is the list that window reads.

A repair is registered here with a scan that is READ-ONLY and a sentence the
user can understand. Nothing in this module writes anything, and nothing in it
imports Qt — the window is a separate file, and this can be tested on its own.

Adding a repair
---------------
Write the detector and the writer in their own module (``core/eventide_repair``
and ``eventide/backend/dialogue_repair`` are the two examples), then add one
``RepairSpec`` to ``_SPECS``. Describe the SYMPTOM the user saw, not the
mechanism: they are looking for the thing that went wrong in their game, not
for the name of the function that caused it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

_log = logging.getLogger("PorySuite.Repairs")

# Keys are stored nowhere and shown nowhere; they exist so the window can map a
# row back to the flow that runs it. Renaming one is harmless.
KEY_MAP_SCRIPTS = 'map_scripts'
KEY_DIALOGUE = 'dialogue_overflow'


@dataclass(frozen=True)
class RepairSpec:
    """One repair the user can run.

    ``scan(root)`` must not write anything — the window runs every scan as soon
    as it opens, before the user has agreed to change a thing.

    ``describe(findings)`` returns a plain-English count for the window's
    status column, or ``''`` when there is nothing to do.
    """

    key: str
    title: str
    description: str
    scan: Callable
    describe: Callable


def plural(n: int, one: str, many: str) -> str:
    """``3 maps`` / ``1 map``. The user reads these; "1 map(s)" looks broken."""
    return f"{n} {one if n == 1 else many}"


def _describe_map_scripts(findings) -> str:
    from core.eventide_repair import REPAIRABLE, damaged_maps
    repairable = [f for f in findings if f.kind in REPAIRABLE]
    if not repairable:
        return ''
    maps = damaged_maps(repairable)
    return (f"{plural(len(repairable), 'problem', 'problems')} in "
            f"{plural(len(maps), 'map', 'maps')}")


def _describe_dialogue(findings) -> str:
    if not findings:
        return ''
    return f"{plural(len(findings), 'text box', 'text boxes')} affected"


def _scan_map_scripts(root):
    from core.eventide_repair import scan
    return scan(root)


def _scan_dialogue(root):
    from eventide.backend.dialogue_repair import scan
    return scan(root)


_SPECS = (
    RepairSpec(
        key=KEY_MAP_SCRIPTS,
        title="Map script damage",
        description=(
            "Older EVENTide builds could mangle a map's script file when they "
            "saved it — turning a message command into something the build "
            "rejects, leaving a stray end after data, or stripping the "
            "indentation. Finds those and offers to put them right."),
        scan=_scan_map_scripts,
        describe=_describe_map_scripts,
    ),
    RepairSpec(
        key=KEY_DIALOGUE,
        title="Dialogue that does not fit the text box",
        description=(
            "The message box shows two lines at a time. Dialogue written "
            "before PorySuite knew that could put a line below the bottom "
            "edge, where the game never draws it — the box opens and a line "
            "of the conversation is simply missing. Fixes the line breaks "
            "without changing a word."),
        scan=_scan_dialogue,
        describe=_describe_dialogue,
    ),
)


def available_repairs() -> tuple:
    """Every registered repair, in the order the window should list them."""
    return _SPECS


def repair_by_key(key: str):
    """The spec with this key, or ``None``."""
    for spec in _SPECS:
        if spec.key == key:
            return spec
    return None


def scan_all(root) -> dict:
    """Run every repair's scan. Returns ``{key: findings}``.

    A detector that raises is reported as ``None`` for its key rather than
    taking the whole window down with it — one broken scan must not hide the
    repairs that do work.
    """
    out: dict = {}
    for spec in _SPECS:
        try:
            out[spec.key] = spec.scan(root)
        except Exception:
            _log.exception("repair scan failed: %s", spec.key)
            out[spec.key] = None
    return out
