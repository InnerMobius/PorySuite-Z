"""Read/write the fishing minigame's per-rod reach.

A hack with the custom fishing minigame stores, in ``src/fishing_minigame.c``,
a ``sRodTiers[]`` table whose ``.maxSlot`` field is how far into the fishing
pool each rod reaches (cumulative — Old ⊆ Good ⊆ Super). This module reads and
writes ONLY those three integers; every other byte of the file is preserved, so
a project whose reach is unchanged rebuilds byte-identical.

Vanilla pokefirered has no fishing minigame and no ``sRodTiers``, so
:func:`read_rod_reach` returns ``None`` there and the editor stays hidden — the
feature can never touch a project that doesn't already ship the table.
"""

from __future__ import annotations

import os
import re

_SRC_REL = os.path.join("src", "fishing_minigame.c")
RODS = ("OLD_ROD", "GOOD_ROD", "SUPER_ROD")


def _src_path(project_dir: str) -> str:
    return os.path.join(project_dir, _SRC_REL)


def _rod_block_re(rod: str) -> "re.Pattern":
    # `[OLD_ROD] = { ... .maxSlot = N ... }` — capture N inside THIS rod's
    # designated-initializer brace group (non-greedy up to .maxSlot, so a
    # neighbouring rod's block can't be crossed).
    return re.compile(
        r"(\[\s*" + rod + r"\s*\]\s*=\s*\{[^}]*?\.maxSlot\s*=\s*)(\d+)",
        re.DOTALL)


def _read_src(project_dir: str):
    path = _src_path(project_dir)
    if not os.path.isfile(path):
        return None, None
    try:
        with open(path, "rb") as f:
            src = f.read().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None, None
    return path, src


def has_rod_reach(project_dir: str) -> bool:
    return read_rod_reach(project_dir) is not None


def read_rod_reach(project_dir: str):
    """``{'OLD_ROD': n, 'GOOD_ROD': n, 'SUPER_ROD': n}`` of maxSlot values, or
    ``None`` if this project has no custom rod-tier table."""
    _path, src = _read_src(project_dir)
    if src is None or "sRodTiers" not in src or ".maxSlot" not in src:
        return None
    out = {}
    for rod in RODS:
        m = _rod_block_re(rod).search(src)
        if not m:
            return None
        out[rod] = int(m.group(2))
    return out


def write_rod_reach(project_dir: str, values: dict) -> bool:
    """Write the three ``maxSlot`` values back into ``sRodTiers``.

    Only the changed digits are rewritten; if nothing changed the file is left
    untouched (byte-identical). Returns True on success (including a no-op),
    False if the table isn't present or a rod couldn't be located.
    """
    path, src = _read_src(project_dir)
    if src is None or "sRodTiers" not in src:
        return False
    new = src
    for rod in RODS:
        if rod not in values:
            continue
        val = int(values[rod])
        new2, n = _rod_block_re(rod).subn(
            lambda m, v=val: m.group(1) + str(v), new, count=1)
        if n == 0:
            return False
        new = new2
    if new != src:
        with open(path, "wb") as f:
            f.write(new.encode("utf-8"))
    return True
