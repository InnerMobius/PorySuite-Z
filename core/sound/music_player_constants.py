"""Make a song table row that names its music player actually assemble.

PoryDAW is the music editor most pokefirered projects use, and when it
registers a song it writes pokeemerald's symbolic constants::

    song mus_castle_town, MUSIC_PLAYER_BGM, 0

Both ``MUSIC_PLAYER_BGM`` and ``MUSIC_PLAYER_SE1`` are strings inside
``porydaw.exe``; its UI shows no such field, so a user has no way to know it
is happening. pokeemerald defines those names. **pokefirered does not** — in a
clean vanilla tree the name appears nowhere at all.

The song macro emits ``.2byte \\music_player``, so an undefined name has no
value and the project stops assembling. That is not one person's problem: it
is every pokefirered project whose owner adds a song in PoryDAW.

This module finds that situation and can repair it. The repair is **derived,
opt-in and idempotent**:

* The value of each name comes from the project's own
  ``sound/music_player_table.inc``, where row order IS the index. Nothing is
  assumed from the name — a project may add or reorder players.
* A name that is referenced but has no matching row is REPORTED, never
  invented. Guessing there points a song at the wrong channel, which plays,
  just wrong.
* A project that references no such names is left completely untouched. On a
  vanilla tree every function here is a no-op.
* Nothing runs on its own. :func:`apply` is called because somebody asked.

The constants go into ``sound/music_player_table.inc``, which already opens
with ``.equiv`` definitions in exactly this style and is ``.include``d
immediately before ``song_table.inc``, so no build file has to change.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field

_log = logging.getLogger("SoundEditor.MusicPlayerConstants")

PLAYER_TABLE = os.path.join("sound", "music_player_table.inc")
SONG_TABLE = os.path.join("sound", "song_table.inc")

#: Where the song macro's fields are read from.
_RE_SONG_ROW = re.compile(
    r'song\s+[A-Za-z_]\w*\s*,\s*([A-Za-z0-9_]+)\s*,\s*([A-Za-z0-9_]+)')
_RE_PLAYER_ROW = re.compile(r'music_player\s+gMPlayInfo_(\w+)\s*,')
#: Both spellings a project might already use to define one.
_RE_EXISTING = re.compile(
    r'(?:^|\s)(?:\.equiv|\.equ|\.set)\s+(MUSIC_PLAYER_\w+)\s*,'
    r'|#\s*define\s+(MUSIC_PLAYER_\w+)\b')

_MARKER = "@ Added by PorySuite: names PoryDAW writes into song_table.inc"


@dataclass
class Finding:
    """What the project references, what it defines, and what is missing."""

    referenced: set[str] = field(default_factory=set)
    defined: set[str] = field(default_factory=set)
    #: ``{name: index}`` for names the player table can justify.
    resolvable: dict[str, int] = field(default_factory=dict)
    #: Referenced, undefined, and NOT justified by any player row.
    unknown: set[str] = field(default_factory=set)
    #: True only when the whole-tree search for existing definitions ran. It
    #: is skipped entirely for a project whose song table holds plain numbers,
    #: which is every project that has never used PoryDAW — they should not
    #: pay for a walk of sound/, include/, asm/ and data/ to be told nothing
    #: is wrong. Recorded rather than left implicit so that staying out of
    #: those projects is a property a test can check.
    deep_scanned: bool = False

    @property
    def missing(self) -> dict[str, int]:
        """Names that need defining, and the value each should get."""
        return {n: i for n, i in self.resolvable.items()
                if n in self.referenced and n not in self.defined}

    @property
    def breaks_build(self) -> bool:
        return bool(self.missing or self.unknown)

    def summary(self) -> str:
        if not self.breaks_build:
            return "nothing to do"
        bits = []
        if self.missing:
            bits.append(f"{len(self.missing)} name(s) to define")
        if self.unknown:
            bits.append(f"{len(self.unknown)} name(s) this project cannot "
                        f"explain: {', '.join(sorted(self.unknown))}")
        return "; ".join(bits)


def _read(path: str) -> str:
    """The file's text with its line endings INTACT.

    ``newline=""`` matters: without it Python translates every ending to
    ``\\n`` on the way in, and writing the result back converts a CRLF
    project's whole file. The promise is that this adds a few lines and
    changes nothing else, and that promise starts at the read.
    """
    try:
        with open(path, encoding="utf-8", errors="replace", newline="") as fh:
            return fh.read()
    except OSError:
        return ""


def player_indices(project_root: str) -> dict[str, int]:
    """``{'MUSIC_PLAYER_BGM': 0, ...}`` from the project's own table."""
    out: dict[str, int] = {}
    for line in _read(os.path.join(project_root, PLAYER_TABLE)).splitlines():
        m = _RE_PLAYER_ROW.search(line)
        if m:
            out["MUSIC_PLAYER_" + m.group(1).upper()] = len(out)
    return out


def inspect(project_root: str) -> Finding:
    """What is referenced, what is defined, what is missing. Read-only."""
    found = Finding()

    for line in _read(os.path.join(project_root, SONG_TABLE)).splitlines():
        m = _RE_SONG_ROW.search(line)
        if not m:
            continue
        for token in (m.group(1), m.group(2)):
            if not token.isdigit():
                found.referenced.add(token.upper())

    if not found.referenced:
        return found        # vanilla project: nothing to look at, nothing to do

    # A definition anywhere in the tree counts — the project may already carry
    # these, and defining one twice is an assembler error.
    found.deep_scanned = True
    for sub in ("sound", "include", "asm", "data"):
        base = os.path.join(project_root, sub)
        if not os.path.isdir(base):
            continue
        for dirpath, _dirs, files in os.walk(base):
            for fn in files:
                if not fn.endswith((".inc", ".h", ".s")):
                    continue
                for m in _RE_EXISTING.finditer(
                        _read(os.path.join(dirpath, fn))):
                    found.defined.add((m.group(1) or m.group(2)).upper())

    found.resolvable = player_indices(project_root)
    found.unknown = {n for n in found.referenced
                     if n not in found.defined and n not in found.resolvable}
    return found


def apply(project_root: str, dry_run: bool = False) -> tuple[bool, str]:
    """Define the missing names in ``music_player_table.inc``.

    Returns ``(changed, message)``. Writes nothing when there is nothing to
    define, so a vanilla project is untouched and running it twice is a no-op.
    A name the project cannot explain is never invented — it is reported and
    left for a person to decide.
    """
    found = inspect(project_root)
    if not found.referenced:
        return False, ("this project's song table uses plain numbers, so "
                       "there is nothing to define")
    missing = found.missing
    if not missing:
        if found.unknown:
            return False, (
                "these names are referenced but this project has no music "
                "player to match them, so PorySuite will not invent a value: "
                + ", ".join(sorted(found.unknown)))
        return False, "every name the song table uses is already defined"

    path = os.path.join(project_root, PLAYER_TABLE)
    text = _read(path)
    if not text:
        return False, f"{PLAYER_TABLE} could not be read"

    ending = "\r\n" if "\r\n" in text else "\n"
    block = [_MARKER]
    for name, value in sorted(missing.items(), key=lambda kv: kv[1]):
        block.append(f"\t.equiv {name}, {value}")
    addition = ending.join(block) + ending + ending

    if dry_run:
        return True, addition

    # Prepend, so the names are defined before anything could use them and
    # before the table's own data rows.
    try:
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(addition + text)
    except OSError as exc:
        return False, f"could not write {PLAYER_TABLE}: {exc}"

    named = ", ".join(f"{n}={v}" for n, v in sorted(missing.items(),
                                                    key=lambda kv: kv[1]))
    _log.info("Defined %s in %s", named, PLAYER_TABLE)
    msg = f"defined {named} in {PLAYER_TABLE}"
    if found.unknown:
        msg += ("; still unexplained, and left alone: "
                + ", ".join(sorted(found.unknown)))
    return True, msg
