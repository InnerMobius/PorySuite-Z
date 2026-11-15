"""Read ``git status`` as exact (status, path) pairs.

Shared by both main windows (``ui/mainwindow.py`` and the unified PorySuite-Z
window ``ui/unified_mainwindow.py``) so the Git panel gets the same answer from
either. It used to live only on the legacy window; the Git panel calls it on
whichever window opened it, so in PorySuite-Z the call raised AttributeError
and aborted the panel's refresh half way — Branches, Stash, History and Remotes
never filled in (docs/BUGS.md, 2026-11-14).

Why ``-z``: git's normal status output WRAPS any path containing a space or
special character in quotes (``"concept/UI/trainer card concept.png"``), and
handing that quoted string to ``git add`` stages nothing, because no file has
that literal name. ``--porcelain -z`` gives every path verbatim.
"""

from __future__ import annotations

import subprocess


def parse_porcelain_z(stdout: str) -> "list[tuple[str, str]]":
    """Parse ``git status --porcelain -z`` output into ``[(xy, path), ...]``.

    Each record is ``XY<space><path>``, NUL-terminated. A rename or copy
    (``X`` is ``R`` or ``C``) is followed by one more NUL field holding the
    ORIGIN path, which is skipped — the entry's path is the new name.
    Records are never stripped: the first byte of ``XY`` can be a meaningful
    space (``" M"`` = modified in the worktree, not staged).
    """
    records = (stdout or "").split("\x00")
    entries: "list[tuple[str, str]]" = []
    i = 0
    while i < len(records):
        rec = records[i]
        if not rec:
            i += 1
            continue
        xy = rec[:2]
        path = rec[3:]
        if xy and xy[0] in ("R", "C"):
            i += 1
        entries.append((xy, path))
        i += 1
    return entries


def status_entries(git_exe: str, cwd: str) -> "list[tuple[str, str]]":
    """``[(xy, path), ...]`` for the repository at *cwd*; ``[]`` if none."""
    if not cwd:
        return []
    try:
        r = subprocess.run(
            [git_exe, "-C", cwd, "status", "--porcelain", "-z"],
            capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return []
    return parse_porcelain_z(r.stdout)
