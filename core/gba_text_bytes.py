"""Measure a string the way the engine stores it: in ENCODED BYTES.

Why this exists
---------------
Fields like an item's name are limited by a fixed byte buffer in the engine
(``ITEM_NAME_LENGTH``), not by how many characters the user typed. Those two
numbers are not the same, because ``charmap.txt`` maps some tokens to several
bytes and some multi-character tokens to one byte::

    'A'         = BB            1 character  -> 1 byte
    PK          = 53            {PK}         -> 1 byte   (4 characters)
    PKMN        = 53 54         {PKMN}       -> 2 bytes  (6 characters)
    POKEBLOCK   = 55 56 57 58 59  {POKEBLOCK} -> 5 bytes (11 characters)

Counting characters instead of bytes is what corrupted
``ITEM_POKEBLOCK_CASE`` on 2026-10-29: its name ``{POKEBLOCK} CASE`` is 16
characters but only 10 bytes, comfortably inside the 14-byte buffer. The editor
measured 16, decided it was too long, and silently cut it to ``{POKEBLOCK} C``.

Relationship to ``core/gba_text_metrics``
-----------------------------------------
That module answers a DIFFERENT question — how many PIXELS WIDE a string
renders, for text that must fit a window. It deliberately reads only the
single-character, single-byte charmap entries, because those are the ones with
glyphs. This module answers "how many bytes does the assembler emit", which
needs the multi-byte named tokens that one skips. They are complementary; this
one does not replace it.

It shares that module's governing principle: **never truncate.** A measurement
is something the UI reports; it is not licence to rewrite the user's text.

Unknown tokens are NEVER guessed
--------------------------------
If a string contains a ``{CODE}`` this project's charmap does not define, or a
parameterised code like ``{COLOR DARK_GRAY}`` whose argument byte cannot be
resolved from the charmap alone, ``encoded_len`` returns ``None`` — "I cannot
measure this" — and ``fits`` returns ``True``. Refusing to measure is correct;
inventing a number would let the UI reject or alter a name the engine would
have accepted. A custom code added by the user is exactly the case that must
survive untouched.
"""
from __future__ import annotations

import os
import re
from typing import Dict, Optional

__all__ = ["GbaTextBytes", "load_text_bytes"]

# 'X' = NN [NN ...]        a literal character
_CHAR_RE = re.compile(r"^'(\\.|[^'])'\s*=\s*((?:[0-9A-Fa-f]{2}\s*)+)$")
# NAME = NN [NN ...]       a named token, written {NAME} in source text
_NAME_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*((?:[0-9A-Fa-f]{2}\s*)+)$")

# The line-break escapes are emitted by preproc rather than declared in
# charmap.txt. Each is a single byte. Documented here rather than silently
# assumed; anything else backslash-escaped is treated as unmeasurable.
_ESCAPE_BYTES = {"n": 1, "l": 1, "p": 1}

_TERMINATOR = 1          # EOS


class GbaTextBytes:
    """Byte measurement for one project's charmap."""

    def __init__(self, chars: Dict[str, int], names: Dict[str, int],
                 name_limit: int) -> None:
        self._chars = chars
        self._names = names
        self._name_limit = int(name_limit)

    # ── queries ──────────────────────────────────────────────────────────
    def name_limit(self) -> int:
        """The item-name BUFFER size in bytes, including the terminator."""
        return self._name_limit

    def encoded_len(self, text: str) -> Optional[int]:
        """Bytes this string occupies, excluding the terminator.

        Returns ``None`` when any part cannot be resolved from this project's
        charmap — never a guess.
        """
        if text is None:
            return None
        total = 0
        i, n = 0, len(text)
        while i < n:
            ch = text[i]
            if ch == "{":
                close = text.find("}", i + 1)
                if close == -1:
                    return None                  # unterminated — unmeasurable
                token = text[i + 1:close].strip()
                size = self._names.get(token)
                if size is None:
                    # Parameterised ({COLOR DARK_GRAY}) or custom: the argument
                    # byte is not resolvable from charmap.txt alone.
                    return None
                total += size
                i = close + 1
                continue
            if ch == "\\" and i + 1 < n:
                size = _ESCAPE_BYTES.get(text[i + 1])
                if size is None:
                    return None
                total += size
                i += 2
                continue
            size = self._chars.get(ch)
            if size is None:
                return None                      # not in this project's charmap
            total += size
            i += 1
        return total

    def fits(self, text: str, limit: Optional[int] = None) -> bool:
        """Does this fit the buffer, leaving room for the terminator?

        Unmeasurable text returns ``True``. The consequence of ``False`` is
        refusing the user's input, so uncertainty must never produce it.
        """
        cap = self._name_limit if limit is None else int(limit)
        used = self.encoded_len(text)
        if used is None:
            return True
        return used + _TERMINATOR <= cap

    def usable_bytes(self, limit: Optional[int] = None) -> int:
        """Bytes available for text once the terminator is reserved."""
        cap = self._name_limit if limit is None else int(limit)
        return max(0, cap - _TERMINATOR)

    def describe(self, text: str, limit: Optional[int] = None) -> str:
        """Short counter text: ``10/13`` bytes, or ``?/13`` when unmeasurable."""
        used = self.encoded_len(text)
        cap = self.usable_bytes(limit)
        return f"{'?' if used is None else used}/{cap}"


def _parse_charmap(root: str):
    chars: Dict[str, int] = {}
    names: Dict[str, int] = {}
    path = os.path.join(root, "charmap.txt")
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return chars, names
    for line in text.splitlines():
        line = line.split("@")[0].strip()
        if not line:
            continue
        m = _CHAR_RE.match(line)
        if m:
            raw = m.group(1)
            # charmap writes an escaped quote/backslash as \' and \\
            ch = raw[1] if len(raw) == 2 and raw[0] == "\\" else raw
            chars.setdefault(ch, len(m.group(2).split()))
            continue
        m = _NAME_RE.match(line)
        if m:
            names.setdefault(m.group(1), len(m.group(2).split()))
    return chars, names


_CACHE: Dict[str, GbaTextBytes] = {}


def load_text_bytes(root: str) -> GbaTextBytes:
    """Build (and cache) the measurer for a project root."""
    key = os.path.normcase(os.path.abspath(root or ""))
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    chars, names = _parse_charmap(root)
    try:
        from core.item_enums import parse_item_name_length
    except Exception:          # pragma: no cover - import shape varies
        from item_enums import parse_item_name_length      # type: ignore
    limit = parse_item_name_length(root)
    out = GbaTextBytes(chars, names, limit)
    _CACHE[key] = out
    return out
