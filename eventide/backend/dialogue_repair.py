"""Find and repair map dialogue that cannot fit the field message box.

Why this exists
---------------
pokefirered's field message box shows exactly TWO lines of text
(``sStandardTextBox_WindowTemplates`` in ``src/new_menu_helpers.c``: four tiles
tall, printed from ``y = 1`` with ``lineSpacing = 1``). ``RenderText`` in
``src/text.c`` advances the line slot on ``\\n`` and NOT on ``\\l``, so the
third line of a page has to be reached by scrolling. Reached with ``\\n`` it is
drawn one line below the window floor and renders as a one-pixel sliver, which
to the player looks like the text simply never arrived.

Two faults wrote dialogue in that shape into this project's maps before the
writer learned the rule (see the 2026-11-22 entry in ``docs/BUGS.md``):

* every line break was encoded as ``\\n``, so no text EVENTide wrote ever
  contained a ``\\l``;
* a page break was once encoded as a literal ``\\n\\n``, which advances the
  slot twice and buries the line after it just as deep.

Fixing the writer does not fix bytes that are already on disk, and a user is
not going to open and re-save every map by hand to find out which ones are
affected. This module is the tool doing it: it reads what is there, works out
which texts the engine cannot draw, and repairs exactly those.

What it will and will not touch
-------------------------------
* **Map dialogue only** — ``data/maps/*/text.inc``. Text in ``data/text/*.inc``
  is drawn in windows of other sizes (the controls guide's is six tiles, the
  new-game intro textbox fifteen; see ``src/oak_speech.c``), where three lines
  on a page are perfectly legal. Applying the two-line rule there would break
  working text.
* **Only texts that actually overflow.** A text whose re-encoding merely
  differs is left alone; the verdict comes from walking the line slots the way
  the engine does. This is a repair, not a reformatter.
* **Only the offending label.** Writing goes through
  ``patch_text_in_file``, which replaces that one block's directive lines and
  preserves every other byte of the file — its other labels, its comments, its
  blank lines and its line endings.
* **Never ``.braille``.** Braille draws in its own taller window.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from core.gba_text_metrics import encode_gba_linebreaks

from eventide.backend.eventide_utils import (
    parse_text_inc, patch_text_in_file,
)

_log = logging.getLogger("EVENTide.DialogueRepair")

# Lines visible at once in the field message box.
LINES_PER_PAGE = 2

_PAGE = "\\p"
_SCROLL = "\\l"
_NEWLINE = "\\n"
_BREAK_RE = re.compile(r"(\\[nlp])")

_LABEL_RE = re.compile(r'^([A-Za-z0-9_]+)::')
_STRING_RE = re.compile(r'^\s*\.string\s+"(.*)"\s*$')
_BRAILLE_RE = re.compile(r'^\s*\.braille\s')


@dataclass
class Finding:
    """One text the engine cannot draw, and the bytes that would fix it."""

    path: Path
    label: str
    on_disk: str                  # the encoded string as the file has it
    repaired: str                 # the encoded string it should have
    content: str                  # decoded form, for patch_text_in_file

    @property
    def map_name(self) -> str:
        return self.path.parent.name


def overflows(encoded: str) -> bool:
    """True when *encoded* draws a line below the message box's floor.

    The slot model, straight from ``RenderText``: ``\\n`` moves down a slot,
    ``\\p`` returns to slot 0, ``\\l`` scrolls the window and leaves the slot
    where it is.
    """
    slot = 0
    for piece in _BREAK_RE.split(encoded or ""):
        if piece == _NEWLINE:
            slot += 1
        elif piece == _PAGE:
            slot = 0
        elif piece == _SCROLL:
            pass
        elif piece.strip(" $") and slot >= LINES_PER_PAGE:
            return True
    return False


def _raw_strings(path: Path) -> dict:
    """``{label: encoded string}`` exactly as the file spells it.

    ``parse_text_inc`` returns the DECODED form (real newlines), which cannot
    tell a ``\\n`` from a ``\\l``. The verdict needs the literal escapes, so
    the directive lines are read here as written. Labels defined with
    ``.braille`` are skipped.
    """
    out: dict = {}
    try:
        text = path.read_text(encoding='utf-8', errors='replace')
    except Exception:
        _log.warning("could not read %s", path)
        return out
    label = None
    parts: list = []
    braille = False

    def flush():
        if label and parts and not braille:
            out[label] = ''.join(parts)

    for line in text.splitlines():
        m = _LABEL_RE.match(line.strip())
        if m:
            flush()
            label, parts, braille = m.group(1), [], False
            continue
        if label is None:
            continue
        if _BRAILLE_RE.match(line):
            braille = True
            continue
        s = _STRING_RE.match(line)
        if s:
            parts.append(s.group(1))
    flush()
    return out


def scan(root) -> list:
    """Every map dialogue the engine cannot draw, with its repair.

    Reports only texts that overflow now and would NOT overflow once
    re-encoded — so a text this module cannot actually help is never offered as
    if it could be.
    """
    findings: list = []
    maps_dir = Path(root) / 'data' / 'maps'
    if not maps_dir.is_dir():
        _log.warning("no data/maps under %s", root)
        return findings

    for path in sorted(maps_dir.glob('*/text.inc')):
        raw = _raw_strings(path)
        if not raw:
            continue
        bad = {lab: enc for lab, enc in raw.items() if overflows(enc)}
        if not bad:
            continue
        try:
            decoded = parse_text_inc(path)
        except Exception:
            _log.exception("could not parse %s", path)
            continue
        for label, on_disk in bad.items():
            content = decoded.get(label)
            if content is None or content.startswith('__BRAILLE__'):
                continue
            repaired = encode_gba_linebreaks(content)
            if repaired == on_disk or overflows(repaired):
                # Either nothing to change, or re-encoding does not rescue it
                # (a single line too long for the box is a different problem,
                # and not one a line break can solve).
                continue
            findings.append(Finding(path=path, label=label, on_disk=on_disk,
                                    repaired=repaired, content=content))
    return findings


def repair(findings) -> tuple:
    """Write each finding's repair. Returns ``(done, failed)`` label lists.

    Each write is surgical and verified: the file is re-read afterwards and the
    label's bytes must come back no longer overflowing, or the label is
    reported as failed rather than silently assumed fixed.
    """
    done: list = []
    failed: list = []
    for f in findings:
        try:
            ok = patch_text_in_file(f.path, f.label, f.content)
        except Exception:
            _log.exception("patch failed for %s in %s", f.label, f.path)
            ok = False
        if ok:
            after = _raw_strings(f.path).get(f.label, '')
            ok = bool(after) and not overflows(after)
        (done if ok else failed).append(f.label)
        _log.info("%s %s in %s", "repaired" if ok else "FAILED",
                  f.label, f.path)
    return done, failed
