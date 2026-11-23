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

    data/maps/Route1/scripts.inc:69: Error: Missing value for required
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


# ── A project's own commands, described for the editor ──────────────────
#
# A project can add script commands of its own (a macro in its event.inc). The
# editor can always ASSEMBLE them, but without help it can only show one as a
# box of comma-separated text, which is useless to somebody who does not write
# code. So a macro may carry a few comment lines, directly above its `.macro`
# line, that tell the editor how to present it:
#
#     @ eventide name: Give Treasure
#     @ eventide section: Items & Rewards
#     @ eventide help: The player holds a treasure up, and it is theirs.
#     @ eventide param what: Treasure | ITEM_ TREASURE_
#     .macro givetreasure what:req, count=1
#
#   name     what the command is called in the list and in Add Command.
#            A macro with a name is offered in Add Command; one without stays
#            as it was.
#   section  which Add Command heading it goes under. An existing heading is
#            joined; any other name becomes a heading of its own.
#   help     the one-line description shown in the picker and the dialog.
#   param    which kinds of constant a parameter takes, by name prefix. Each
#            prefix's names are offered in one searchable dropdown. A
#            parameter with no line gets a plain value box. An optional label
#            can come first, before a bar: `param item: Reward | ITEM_`.
#
# These are comments, so the build never sees them, and a project that writes
# none loses nothing.

_ANNOT_RE = re.compile(
    r"^\s*@\s*eventide\s+(?P<key>name|section|help|param\s+\w+)\s*:\s*"
    r"(?P<val>.*?)\s*$", re.I)


class ProjectCommand:
    """A project macro that described itself for the editor."""

    __slots__ = ("name", "label", "section", "help", "params")

    def __init__(self, name: str, label: str, section: str, help_: str,
                 params: List[Tuple[MacroParam, str, Tuple[str, ...]]]):
        self.name = name
        self.label = label
        self.section = section
        self.help = help_
        # (parameter, label shown beside its box, constant prefixes)
        self.params = params


_CMD_CACHE: Dict[str, Dict[str, ProjectCommand]] = {}


def _read_macro_lines(root_dir: str):
    for rel in _REL_MACRO_PATHS:
        path = os.path.join(root_dir, rel)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                yield fh.read().splitlines()
        except OSError:
            continue


def _parse_project_commands(root_dir: str) -> Dict[str, ProjectCommand]:
    macros = get_macros(root_dir)
    out: Dict[str, ProjectCommand] = {}
    for lines in _read_macro_lines(root_dir):
        notes: Dict[str, str] = {}
        for line in lines:
            a = _ANNOT_RE.match(line)
            if a:
                key = " ".join(a.group("key").lower().split())
                notes[key] = a.group("val")
                continue
            if line.strip().startswith("@"):
                continue            # an ordinary comment in the same block
            m = _MACRO_RE.match(line)
            if m and notes.get("name"):
                name = m.group("name")
                params = []
                for p in macros.get(name, []):
                    spec = notes.get(f"param {p.name.lower()}", "")
                    label, _, prefixes = spec.rpartition("|")
                    label = label.strip() or p.name.replace("_", " ").title()
                    pfx = tuple(x for x in re.split(r"[\s,]+", prefixes) if x)
                    params.append((p, label, pfx))
                out.setdefault(name, ProjectCommand(
                    name, notes["name"], notes.get("section", ""),
                    notes.get("help", ""), params))
            notes = {}
    _log.debug("found %d described project commands under %s",
               len(out), root_dir)
    return out


def get_project_commands(root_dir) -> Dict[str, ProjectCommand]:
    """Macro name -> how to present it, for every macro that described itself."""
    if not root_dir:
        return {}
    key = os.path.abspath(str(root_dir))
    if key not in _CMD_CACHE:
        _CMD_CACHE[key] = _parse_project_commands(key)
    return _CMD_CACHE[key]


def project_command(root_dir, name: str) -> Optional[ProjectCommand]:
    """How to present macro *name*, or None if it did not describe itself."""
    return get_project_commands(root_dir).get(name)


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
    _CMD_CACHE.clear()


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
