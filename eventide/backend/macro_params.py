"""
eventide/backend/macro_params.py
What each script command's parameters are, read from the project's own macros.

A script command is an assembler macro, and the macro declares which of its
parameters are required:

    .macro savebgm song:req
    .macro warp map:req, x=0, y=0

Leaving a required one out does not fail in the editor and does not fail in
game — it fails the BUILD, later, as a message that names a macro and a line
number and nothing else:

    data/maps/Faron/scripts.inc:69: Error: Missing value for required
        parameter `song' of macro `savebgm'

Nothing in that connects back to the dialog it came from, and an audit of the
editor found 84 of the 127 commands it offers could be saved with a required
parameter blank. So the editor asks this module before it writes one.

The macro file is the project's OWN (`asm/macros/event.inc`), so a project that
adds commands, removes them, or changes which parameters are required gets
checked against its own rules rather than against anything assumed here. If the
file cannot be found or read, every command is reported as fine — this must
never be the reason somebody cannot save their work.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Dict, List, Optional, Sequence, Tuple

_log = logging.getLogger("EVENTide.MacroParams")

# `.macro warp map:req, x=0, y=0`
_MACRO_RE = re.compile(r"^\s*\.macro\s+(?P<name>\w+)(?P<params>.*)$")

_REL_MACRO_PATHS = (
    os.path.join("asm", "macros", "event.inc"),
    os.path.join("asm", "macros", "event_move.inc"),
)


class MacroParam:
    """One declared parameter of a script macro."""

    __slots__ = ("name", "required", "index", "vararg")

    def __init__(self, name: str, required: bool, index: int,
                 vararg: bool = False):
        self.name = name
        self.required = required
        self.index = index
        # Recorded at parse time: `rest:vararg` loses its `:vararg` once the
        # name is split out, so asking the NAME about it later always says no.
        self.vararg = vararg


def _parse_macros(root_dir: str) -> Dict[str, List[MacroParam]]:
    out: Dict[str, List[MacroParam]] = {}
    for rel in _REL_MACRO_PATHS:
        path = os.path.join(root_dir, rel)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                lines = fh.read().splitlines()
        except OSError:
            continue
        for line in lines:
            m = _MACRO_RE.match(line)
            if not m:
                continue
            name = m.group("name")
            raw = m.group("params").split("@")[0]          # strip a trailing comment
            params: List[MacroParam] = []
            for i, chunk in enumerate(p.strip() for p in raw.split(",")):
                if not chunk:
                    continue
                # `song:req`, `x=0`, `rest:vararg`, or a bare name.
                pname = re.split(r"[:=]", chunk, maxsplit=1)[0].strip()
                if not pname:
                    continue
                params.append(MacroParam(pname, ":req" in chunk, i,
                                         ":vararg" in chunk))
            out.setdefault(name, params)
    _log.debug("parsed %d macros under %s", len(out), root_dir)
    return out


_CACHE: Dict[str, Dict[str, List[MacroParam]]] = {}


def get_macros(root_dir) -> Dict[str, List[MacroParam]]:
    """Macro name -> its declared parameters, for this project."""
    if not root_dir:
        return {}
    key = os.path.abspath(str(root_dir))
    if key not in _CACHE:
        _CACHE[key] = _parse_macros(key)
    return _CACHE[key]


def clear_cache() -> None:
    """Drop the parsed macros. Call when the project changes or reloads."""
    _CACHE.clear()


def flatten_args(args: Sequence) -> List[str]:
    """One argument per macro parameter.

    Command widgets hand back arguments in two shapes — separate values, or
    several already joined with commas into one string — so both are reduced to
    the same positional list the macro sees.
    """
    flat: List[str] = []
    for a in args:
        if a is None:
            flat.append("")
            continue
        for piece in str(a).split(","):
            flat.append(piece.strip())
    return flat


def missing_required(root_dir, command: str,
                     args: Sequence) -> List[str]:
    """Names of the required parameters this command has left empty.

    Empty list means nothing is missing — including whenever the macro is
    unknown, has no required parameters, or the project's macro file could not
    be read. Being unsure is never a reason to block somebody's edit.
    """
    params = get_macros(root_dir).get(command)
    if not params:
        return []
    # A vararg tail swallows everything after it, so positions stop meaning
    # anything and this cannot judge the command.
    if any(p.vararg for p in params):
        return []
    flat = flatten_args(args)
    # More arguments than the macro declares means the editor's tuple is NOT
    # the macro's argument list, so comparing them position by position is
    # meaningless. The Text command is the case that proved it: the editor
    # holds it as (label, text, type) while `message` declares one parameter,
    # `text:req` — so position 0 compared the LABEL against it and announced
    # that "text" was empty on a command with text plainly typed into it. The
    # label is SUPPOSED to be blank; the editor generates it when saving.
    if len(flat) > len(params):
        return []
    missing: List[str] = []
    for p in params:
        if not p.required:
            continue
        if p.index >= len(flat) or not flat[p.index]:
            missing.append(p.name)
    return missing


def describe_missing(command: str, missing: Sequence[str]) -> str:
    """A plain-English sentence naming what still has to be filled in."""
    names = [m.replace("_", " ") for m in missing]
    if len(names) == 1:
        what = f'"{names[0]}"'
    else:
        what = ", ".join(f'"{n}"' for n in names[:-1]) + f' and "{names[-1]}"'
    verb = "needs" if len(names) == 1 else "need"
    return (f"{what} {verb} a value before this command can be saved.\n\n"
            f"The game's {command} command requires it, and leaving it empty "
            f"would stop the project building rather than show up here.")
