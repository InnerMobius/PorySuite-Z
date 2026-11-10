"""Flags and variables the game sets when a NEW GAME starts.

Some flags have to be ON before the player has done anything. The engine skips
spawning an object whose hide flag is set, so "this person is not here yet" is
a flag that starts on and gets cleared when they should appear — Oak in Pallet
Town, the rival in Cerulean, the Mystery Gift deliverymen. Flags are off in a
fresh save file, so something has to switch them on, and vanilla does it with
one script run once at the start of a new game: a long run of ``setflag`` lines
ending in ``end``.

Nothing in PorySuite could read or write that script. The effect was that a
hide flag set in the editor worked perfectly and then never came on, so the
NPC stood in the street from the first save until the script that was supposed
to hide them cleared a flag that was already clear.

This module is the reader and writer for that script. It is deliberately
small and conservative:

* **The script is FOUND, not named.** ``src/new_game.c`` hands a label to
  ``RunScriptImmediately``; that label is what gets read. A project that
  renamed the script keeps working, and a project that has no such call is
  reported as having none rather than guessed at.
* **Only the line asked for changes.** Everything else in the file — other
  scripts, comments, blank lines, the project's own indentation and line
  endings — comes back byte-identical. :func:`set_flag` returns what it did so
  a caller can say so rather than assume.
* **Conditional assembly means hands off.** A block containing ``.ifdef`` /
  ``.if`` has lines that may or may not be part of the build, and inserting
  into it could put a ``setflag`` inside the wrong branch. Those are refused
  with a reason instead of edited.

What this module does NOT do: decide which flags belong here. That is the
user's call, made one checkbox at a time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

#: Where the engine kicks off its new-game script.
NEW_GAME_C = 'src/new_game.c'

#: The call whose argument is the script run at the start of a new game.
_RUN_CALL = 'RunScriptImmediately'

#: Where script bodies live. Searched in this order.
_DATA_DIRS = ('data',)
_DATA_EXTS = ('.s', '.inc')

_RUN_RE = re.compile(re.escape(_RUN_CALL) + r'\s*\(\s*([A-Za-z_]\w*)\s*\)')
_SETFLAG_RE = re.compile(r'^\s*setflag\s+([A-Za-z_]\w*)\s*$')
_SETVAR_RE = re.compile(r'^\s*setvar\s+([A-Za-z_]\w*)\s*,\s*(\S+?)\s*$')
_TERMINATOR_RE = re.compile(r'^\s*(end|return)\s*$')
_LABEL_RE = re.compile(r'^([A-Za-z_]\w*)::')
#: Anything that makes a line's presence in the build conditional.
_CONDITIONAL_RE = re.compile(r'^\s*\.(if|ifdef|ifndef|else|elseif|endif)\b')


@dataclass
class NewGameState:
    """What the new-game script does, and where it lives."""

    #: The script's label, e.g. ``EventScript_ResetAllMapFlags``.
    label: str = ''
    #: The file holding it.
    path: Path | None = None
    #: Flags the script switches ON, in the order it does so.
    flags: list[str] = field(default_factory=list)
    #: Variables it sets, as ``{name: value}``.
    vars: dict[str, str] = field(default_factory=dict)
    #: Set when the block cannot be edited safely, with the reason.
    locked: str = ''

    @property
    def found(self) -> bool:
        return bool(self.label and self.path is not None)

    @property
    def editable(self) -> bool:
        return self.found and not self.locked

    def sets_flag(self, flag: str) -> bool:
        return flag in self.flags


def _read(path: Path) -> str:
    """The file's text with its line endings INTACT.

    `newline=''` is the whole point. `read_text` translates every ending to
    ``\\n``, so a CRLF project's file came back as LF, was split into LF
    lines, and was written back as LF — one checkbox tick rewriting every
    line in the file. The caller's promise is that only the line asked for
    changes, and that promise starts here.
    """
    try:
        with path.open(encoding='utf-8', errors='replace', newline='') as fh:
            return fh.read()
    except OSError:
        return ''


def _candidate_labels(root: Path) -> list[str]:
    """Labels ``new_game.c`` runs immediately, in the order it runs them."""
    text = _read(Path(root) / NEW_GAME_C)
    if not text:
        return []
    seen: list[str] = []
    for m in _RUN_RE.finditer(text):
        if m.group(1) not in seen:
            seen.append(m.group(1))
    return seen


def _find_block(root: Path, label: str) -> tuple[Path | None, int, int]:
    """Locate *label*'s block. Returns (path, first_line_idx, end_line_idx).

    ``first_line_idx`` is the index of the ``label::`` line itself;
    ``end_line_idx`` is one past the block's terminator (or the start of the
    next label). Both are indices into the file's ``splitlines(keepends=True)``.
    """
    for sub in _DATA_DIRS:
        base = Path(root) / sub
        if not base.is_dir():
            continue
        for path in sorted(base.rglob('*')):
            if path.suffix not in _DATA_EXTS or not path.is_file():
                continue
            text = _read(path)
            if f'{label}::' not in text:
                continue
            lines = text.splitlines(keepends=True)
            start = None
            for i, ln in enumerate(lines):
                m = _LABEL_RE.match(ln)
                if m and m.group(1) == label:
                    start = i
                    break
            if start is None:
                continue
            end = len(lines)
            for i in range(start + 1, len(lines)):
                if _TERMINATOR_RE.match(lines[i]):
                    end = i + 1
                    break
                m = _LABEL_RE.match(lines[i])
                if m and m.group(1) != label:
                    end = i
                    break
            return path, start, end
    return None, -1, -1


def read(root) -> NewGameState:
    """What the project's new-game script sets, or an empty state if none.

    When ``new_game.c`` runs more than one script, the one with the most
    ``setflag`` lines wins — a behaviour test, not a name test, so a project
    that split or renamed the script is followed rather than second-guessed.
    """
    root = Path(root)
    best: NewGameState | None = None
    for label in _candidate_labels(root):
        path, start, end = _find_block(root, label)
        if path is None:
            continue
        lines = _read(path).splitlines(keepends=True)[start:end]
        state = NewGameState(label=label, path=path)
        for ln in lines:
            if _CONDITIONAL_RE.match(ln):
                state.locked = (
                    'the script uses conditional assembly (.ifdef / .if), so '
                    'a line added here might land in a branch that is not '
                    'built')
            m = _SETFLAG_RE.match(ln)
            if m:
                if m.group(1) not in state.flags:
                    state.flags.append(m.group(1))
                continue
            m = _SETVAR_RE.match(ln)
            if m:
                state.vars[m.group(1)] = m.group(2)
        if best is None or len(state.flags) > len(best.flags):
            best = state
    return best or NewGameState()


# ─────────────────────────────────────────────────────────────────────────────
# Writing
# ─────────────────────────────────────────────────────────────────────────────

def _indent_and_ending(lines: list[str], start: int, end: int) -> tuple[str, str]:
    """The indentation and line ending the block already uses.

    Copied from a real line in the block rather than assumed, so a project
    using spaces, or CRLF, keeps them.
    """
    indent, ending = '\t', '\n'
    for ln in lines[start + 1:end]:
        body = ln.rstrip('\r\n')
        if not body.strip():
            continue
        lead = body[:len(body) - len(body.lstrip())]
        if lead:
            indent = lead
        if ln.endswith('\r\n'):
            ending = '\r\n'
        elif ln.endswith('\n'):
            ending = '\n'
        break
    return indent, ending


def _write(path: Path, lines: list[str]) -> None:
    # newline='' so the endings already in the lines are written through
    # unchanged rather than translated by the platform.
    with path.open('w', encoding='utf-8', newline='') as fh:
        fh.writelines(lines)


def _without(lines: list[str], start: int, end: int,
             matcher: re.Pattern, name: str) -> list[str]:
    """*lines* minus any line INSIDE the block that *matcher* ties to *name*.

    Bounded to the block on purpose: the same `setflag` in another script in
    the same file is somebody else's line, and a checkbox about the new-game
    script must not reach outside it.
    """
    out: list[str] = []
    for i, ln in enumerate(lines):
        if start < i < end:
            m = matcher.match(ln)
            if m and m.group(1) == name:
                continue
        out.append(ln)
    return out


def _insert_after_last(lines: list[str], start: int, end: int,
                       matcher: re.Pattern, new_line: str) -> list[str]:
    """Put *new_line* after the last line in the block matching *matcher*.

    Falls back to just before the block's terminator, and then to just after
    the label. Never past the terminator — a command after ``end`` never runs.
    """
    at = -1
    for i in range(start + 1, end):
        if matcher.match(lines[i]):
            at = i
    if at < 0:
        at = start
        for i in range(start + 1, end):
            if _TERMINATOR_RE.match(lines[i]):
                at = i - 1
                break
    out = list(lines)
    out.insert(at + 1, new_line)
    return out


def set_flag(root, flag: str, on: bool) -> tuple[bool, str]:
    """Add or remove ``setflag <flag>`` in the new-game script.

    Returns ``(changed, message)``. ``changed`` is False both when there was
    nothing to do and when the write was refused; the message says which, so
    a caller never has to guess from a bare bool.
    """
    state = read(root)
    if not state.found:
        return False, ('this project has no new-game script — nothing in '
                       f'{NEW_GAME_C} hands a script to {_RUN_CALL}()')
    if state.locked:
        return False, f'{state.label} cannot be edited safely: {state.locked}'

    path, start, end = _find_block(Path(root), state.label)
    if path is None:
        return False, f'{state.label} could not be re-read'
    lines = _read(path).splitlines(keepends=True)

    already = state.sets_flag(flag)
    if on and already:
        return False, f'{flag} is already switched on at the start of a new game'
    if not on and not already:
        return False, f'{flag} was not switched on at the start of a new game'

    if on:
        indent, ending = _indent_and_ending(lines, start, end)
        new_lines = _insert_after_last(
            lines, start, end, _SETFLAG_RE,
            f'{indent}setflag {flag}{ending}')
        _write(path, new_lines)
        return True, (f'{flag} is now switched on when a new game starts '
                      f'({state.label})')

    kept = _without(lines, start, end, _SETFLAG_RE, flag)
    if len(kept) == len(lines):
        return False, f'{flag} was not found in {state.label}'
    _write(path, kept)
    return True, (f'{flag} is no longer switched on when a new game starts '
                  f'({state.label})')


def set_var(root, var: str, value) -> tuple[bool, str]:
    """Set, change or remove ``setvar <var>, <value>`` in the new-game script.

    *value* of None removes the line. Returns ``(changed, message)``.
    """
    state = read(root)
    if not state.found:
        return False, ('this project has no new-game script — nothing in '
                       f'{NEW_GAME_C} hands a script to {_RUN_CALL}()')
    if state.locked:
        return False, f'{state.label} cannot be edited safely: {state.locked}'

    path, start, end = _find_block(Path(root), state.label)
    if path is None:
        return False, f'{state.label} could not be re-read'
    lines = _read(path).splitlines(keepends=True)
    current = state.vars.get(var)

    if value is None:
        if current is None:
            return False, f'{var} was not given a starting value'
        kept = _without(lines, start, end, _SETVAR_RE, var)
        _write(path, kept)
        return True, (f'{var} no longer gets a starting value '
                      f'({state.label})')

    value = str(value)
    if current == value:
        return False, f'{var} already starts at {value}'

    if current is not None:
        indent, ending = _indent_and_ending(lines, start, end)
        out = list(lines)
        for i in range(start + 1, end):
            m = _SETVAR_RE.match(out[i])
            if m and m.group(1) == var:
                out[i] = f'{indent}setvar {var}, {value}{ending}'
                break
        _write(path, out)
        return True, f'{var} now starts at {value} ({state.label})'

    indent, ending = _indent_and_ending(lines, start, end)
    # After the last setvar if there is one, otherwise after the last setflag,
    # which keeps vanilla's shape: the flags, then the values, then `end`.
    anchor = _SETVAR_RE if state.vars else _SETFLAG_RE
    out = _insert_after_last(lines, start, end, anchor,
                             f'{indent}setvar {var}, {value}{ending}')
    _write(path, out)
    return True, f'{var} now starts at {value} ({state.label})'
