"""
ui/game_text_edit.py
Standardised rich-text editor for GBA game text.

Architecture (V2 — token-free document model):

  * The QTextDocument behind the widget holds **only visible content**:
    the user's letters, spaces, punctuation, ``{PLAYER}``-style
    structural placeholders, and Unicode emoji glyphs. It does NOT
    store ``{COLOR X}`` / ``{SHADOW X}`` / ``{HIGHLIGHT X}`` tokens —
    color is encoded as character formats on the QTextDocument
    (foreground colour applied to ranges).

  * On ``get_inc_text`` / ``get_eventide_text``, the widget walks the
    document's character formats and EMITS color tokens around any
    coloured runs, plus converts visible emoji glyphs back to
    ``{EMOJI_*}`` tokens. The output is a normal pokefirered text
    string ready to write to ``.inc``.

  * On ``set_inc_text`` / ``set_eventide_text``, color tokens in the
    incoming text are PARSED OUT and re-applied as character formats;
    ``{EMOJI_*}`` tokens become Unicode glyphs. The user sees a clean
    document with no formatting markup visible.

  * The remaining ``{COMMAND}`` tokens (``{PLAYER}``, ``{PK}``,
    ``{LV}``, ``{STR_VAR_*}``, etc.) stay as visible markup because
    they represent runtime-substituted placeholders, not formatting.
    They render in blue-bold via the syntax highlighter.

  * ``set_implicit_color(name)`` sets the default text colour for
    un-coloured text — used to preview vanilla pokefirered's NPC
    gender-tinted dialogue (``AddTextPrinterDiffStyle``) without the
    user inserting any tokens.

This widget is the standard for ALL text editing in PorySuite-Z:
trainer dialogue, NPC text, item / move / ability / dex descriptions,
the Text Editor tab, and credits.

Character limits are auto-detected from vanilla pokefirered files.
The GBA text box can display 36 characters per line.
"""
from __future__ import annotations

import re
from typing import Optional

from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtGui import (
    QAction, QColor, QFont, QKeyEvent, QSyntaxHighlighter,
    QTextCharFormat, QTextCursor,
)
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QMenu, QTextEdit,
    QVBoxLayout, QWidget,
)


# ── Constants ────────────────────────────────────────────────────────────────

# Default character limit per display line (auto-detected from vanilla files)
DEFAULT_CHARS_PER_LINE = 36

# Maximum display lines per text box type
DEFAULT_MAX_LINES = 20

# Recognised structural-placeholder tokens — render as visible markup.
# (Color/shadow/highlight tokens are NOT in this list — those are
# stripped on load, applied as character formats, and re-emitted on save.)
TEXT_COMMANDS = [
    "{PLAYER}", "{RIVAL}", "{KUN}",
    "{STR_VAR_1}", "{STR_VAR_2}", "{STR_VAR_3}",
    "{PLAY_BGM}", "{PAUSE_MUSIC}", "{RESUME_MUSIC}",
    "{FONT_NORMAL}", "{FONT_MALE}", "{FONT_FEMALE}",
    "{PK}", "{PKMN}", "{LV}",
]

# Categorised for the right-click menu
_COMMAND_CATEGORIES = {
    "Variables": ["{PLAYER}", "{RIVAL}", "{KUN}",
                  "{STR_VAR_1}", "{STR_VAR_2}", "{STR_VAR_3}"],
    "Music": [
        "{PLAY_BGM}", "{PAUSE_MUSIC}", "{RESUME_MUSIC}",
    ],
    "Font": ["{FONT_NORMAL}", "{FONT_MALE}", "{FONT_FEMALE}"],
    "Symbols": ["{PK}", "{PKMN}", "{LV}"],
}

# Regex: any uppercase token inside braces, optionally with a
# space-separated argument. Catches {PLAYER}, {STR_VAR_1}, {COLOR RED},
# {SHADOW LIGHT_BLUE}, {PAUSE 30}, {EMOJI_HEART}, {FONT_NORMAL}, etc.
_COMMAND_RE = re.compile(r"\{[A-Z][A-Z0-9_]*(?:\s+[A-Z0-9][A-Z0-9_]*)?\}")

# Regex specifically for color/shadow/highlight wrappers (consumed at
# load time, regenerated at save time).
_COLOR_TOKEN_RE = re.compile(
    r"\{(COLOR|SHADOW|HIGHLIGHT)\s+([A-Z][A-Z0-9_]*)\}")


# ── Color preview RGB (matches in-game textbox colors) ─────────────────────

_COLOR_PREVIEW_HEX = {
    "DARK_GRAY":   "#404040",
    "WHITE":       "#f0f0f0",
    "LIGHT_GRAY":  "#a0a0a0",
    "RED":         "#cc4444",
    "LIGHT_RED":   "#e89494",
    "GREEN":       "#44aa44",
    "LIGHT_GREEN": "#94d894",
    "BLUE":        "#4470cc",
    "LIGHT_BLUE":  "#94b8e8",
    "TRANSPARENT": "#888888",
}

# Default foreground when no explicit color has been set on a span.
DEFAULT_COLOR_NAME = "DARK_GRAY"


# ── Emoji bidirectional map ────────────────────────────────────────────────
#
# The pokefirered font has a set of small glyphs that the engine
# accesses via ``{EMOJI_*}`` tokens. We map each one to a Unicode
# equivalent so the editor can show a real glyph — and convert back
# on save. The mapping is a best-visual-match, not a 1:1 byte
# correspondence (pokefirered's fire emoji is a specific tile, not
# the iOS-style 🔥 the user sees in the editor).
#
# The bijection means as long as the user types one of the values
# below in the editor, on save it round-trips to the corresponding
# token. Users can also type any other Unicode characters; those
# stay as-is in the saved bytes (and won't render in-game without a
# matching font glyph, but that's the user's call).
EMOJI_TOKEN_TO_UNICODE: dict[str, str] = {
    "{EMOJI_HEART}":       "❤",
    "{EMOJI_NOTE}":        "♪",
    "{EMOJI_BALL}":        "◯",
    "{EMOJI_FIRE}":        "🔥",
    "{EMOJI_WATER}":       "💧",
    "{EMOJI_LEAF}":        "🍃",
    "{EMOJI_BOLT}":        "⚡",
    "{EMOJI_MOON}":        "🌙",
    "{EMOJI_SPIRAL}":      "🌀",
    "{EMOJI_TONGUE}":      "😛",
    "{EMOJI_HAPPY}":       "🙂",
    "{EMOJI_BIGSMILE}":    "😄",
    "{EMOJI_MISCHIEVOUS}": "😏",
    "{EMOJI_SURPRISED}":   "😲",
    "{EMOJI_SHOCKED}":     "😱",
    "{EMOJI_ANGRY}":       "😠",
    "{EMOJI_BIGANGER}":    "😡",
    "{EMOJI_IRRITATED}":   "😒",
    "{EMOJI_EVIL}":        "😈",
    "{EMOJI_TIRED}":       "😴",
    "{EMOJI_NEUTRAL}":     "😐",
    "{EMOJI_TRIANGLE}":    "▲",
    "{EMOJI_SQUARE}":      "■",
    "{EMOJI_CIRCLE}":      "●",
    "{EMOJI_SPHERE}":      "⬤",
    "{EMOJI_BIGWHEEL}":    "⊕",
    "{EMOJI_SMALLWHEEL}":  "⊙",
    "{EMOJI_LEFT_FIST}":   "✊",
    "{EMOJI_RIGHT_FIST}":  "🤜",
    "{EMOJI_ACUTE}":       "´",
    "{EMOJI_GRAVE}":       "`",
}
# Reverse map — used at save time to convert glyphs back to tokens.
EMOJI_UNICODE_TO_TOKEN: dict[str, str] = {
    v: k for k, v in EMOJI_TOKEN_TO_UNICODE.items()
}


# ── Counter / overflow constants ───────────────────────────────────────────

_CLR_NORMAL = "#555555"
_CLR_AMBER  = "#ffb74d"
_CLR_RED    = "#e57373"

_BG_OVERFLOW     = QColor("#5c1a1a")
_FG_OVERFLOW     = QColor("#ff6b6b")
_BG_EXTRA_LINES  = QColor("#3d1a00")
_FG_EXTRA_LINES  = QColor("#ff9944")

_CLR_COMMAND = QColor("#1565c0")  # blue-bold for visible {COMMAND} tokens


# ── Highlighter — only handles visible {COMMAND} tokens ────────────────────

class _CommandHighlighter(QSyntaxHighlighter):
    """Bold-blue highlighter for the structural ``{COMMAND}`` tokens.

    Color/shadow/highlight tokens are NOT seen by this highlighter —
    they're stripped on load and stored as character formats instead.
    So this is a much simpler stateless highlighter than the V1 form:
    just walk the line, blue-bold any ``{COMMAND}`` match.

    The previously-stateful ``set_implicit_color`` / per-block color
    tracking is now unnecessary because color is a document-level
    property, not derived from token state. A no-op stub is kept so
    the existing ``GameTextEdit.set_implicit_color`` API keeps working.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cmd_fmt = QTextCharFormat()
        self._cmd_fmt.setForeground(_CLR_COMMAND)
        self._cmd_fmt.setFontWeight(QFont.Weight.Bold)

    def highlightBlock(self, text: str) -> None:
        for m in _COMMAND_RE.finditer(text):
            inside = m.group(0)
            # Color/shadow/highlight tokens shouldn't appear in a clean
            # document, but if the user manually typed one we still
            # blue-bold it so they can see it before save converts it.
            self.setFormat(m.start(), m.end() - m.start(), self._cmd_fmt)


# ── Token <-> rich-document conversions ────────────────────────────────────


def _replace_emoji_tokens(text: str) -> str:
    """Turn ``{EMOJI_HEART}`` etc. into the Unicode glyph version."""
    for token, glyph in EMOJI_TOKEN_TO_UNICODE.items():
        if token in text:
            text = text.replace(token, glyph)
    return text


def _replace_emoji_glyphs(text: str) -> str:
    """Turn Unicode glyphs back into ``{EMOJI_*}`` tokens for save."""
    for glyph, token in EMOJI_UNICODE_TO_TOKEN.items():
        if glyph in text:
            text = text.replace(glyph, token)
    return text


def parse_color_runs(text: str) -> tuple[str, list[tuple[int, int, str]]]:
    """Strip ``{COLOR X}`` / ``{SHADOW X}`` / ``{HIGHLIGHT X}`` tokens
    from ``text`` and return ``(plain_text, color_runs)``.

    ``color_runs`` is a list of ``(start_pos, end_pos, color_name)``
    tuples on the **plain text**'s coordinate system, describing
    contiguous coloured spans. Only the FOREGROUND color is tracked —
    SHADOW and HIGHLIGHT tokens are still stripped so they don't
    leak into the editor, but their colors are not recorded for the
    editor preview (the in-game shadow/highlight rendering doesn't
    affect what colour the editor should show; it affects the look
    of in-game text).

    Save-time emission re-pairs the foreground color with its matching
    shadow color (per ``text_format_toolbar.VANILLA_COLORS``) so the
    round-trip preserves the user's intent.
    """
    plain_chars: list[str] = []
    runs: list[tuple[int, int, str]] = []
    current_color = DEFAULT_COLOR_NAME
    run_start: Optional[int] = None
    plain_pos = 0

    pos = 0
    while pos < len(text):
        m = _COLOR_TOKEN_RE.match(text, pos)
        if m:
            kind = m.group(1)  # COLOR / SHADOW / HIGHLIGHT
            color = m.group(2)
            if kind == "COLOR":
                # Close any open run before transitioning.
                if (run_start is not None
                        and current_color != DEFAULT_COLOR_NAME
                        and plain_pos > run_start):
                    runs.append((run_start, plain_pos, current_color))
                current_color = color
                run_start = (
                    plain_pos if color != DEFAULT_COLOR_NAME else None)
            # SHADOW / HIGHLIGHT tokens are dropped from the preview —
            # we don't render them in the editor, but we'll re-emit
            # matching shadows on save based on the foreground color.
            pos = m.end()
            continue
        plain_chars.append(text[pos])
        plain_pos += 1
        pos += 1

    # Close any still-open run at end of text.
    if (run_start is not None
            and current_color != DEFAULT_COLOR_NAME
            and plain_pos > run_start):
        runs.append((run_start, plain_pos, current_color))

    return "".join(plain_chars), runs


_COLOR_SHADOW_PAIR_RE = re.compile(r"\{COLOR (\w+)\}\s*\{SHADOW (\w+)\}")
_TRAILING_RESET_RE = re.compile(
    r"\{COLOR DARK_GRAY\}(?:\{SHADOW LIGHT_GRAY\})?\$?\s*$")


def color_shadow_context(raw: str) -> dict:
    """What the ROUND TRIP would otherwise throw away.

    Two pieces of the author's original that the parse discards and the emit
    then guesses at — badly:

    * **the actual SHADOW paired with each colour.** `parse_color_runs` records
      only the foreground, and emit re-derives the shadow from a three-entry
      table (RED/GREEN/BLUE). Any other pairing is LOST: 104 of this project's
      labels use `{COLOR DYNAMIC_COLOR6}{SHADOW DYNAMIC_COLOR5}`, and a save
      silently dropped the shadow.
    * **whether the text ended with an explicit colour reset.** Emitting one
      unconditionally added 36 characters to every string that didn't have one
      — 23 in this project, against exactly 1 that did.

    Carried through `escape_map`, which `inc_to_display` has returned empty and
    `display_to_inc` has ignored since the line-break rework; it exists purely
    for call-site compatibility, so it can carry this without changing any
    signature.
    """
    pairs: dict = {}
    for m in _COLOR_SHADOW_PAIR_RE.finditer(raw or ""):
        pairs.setdefault(m.group(1), m.group(2))
    return {"shadows": pairs,
            "trailing_reset": bool(_TRAILING_RESET_RE.search(raw or ""))}


def emit_color_runs(plain_text: str,
                    color_runs: list[tuple[int, int, str]],
                    context: dict | None = None) -> str:
    """Turn a plain string + list of color runs back into a token-laden
    string suitable for ``.inc`` output.

    Each run becomes ``{COLOR <name>}{SHADOW <light_name>}<text>{COLOR
    DARK_GRAY}{SHADOW LIGHT_GRAY}`` — matching vanilla pokefirered's
    paired-color convention. Shadows are derived from the foreground
    color via the standard ``LIGHT_*`` partner; foregrounds without a
    standard partner emit the foreground only.

    Runs MUST be sorted by start position and non-overlapping.
    """
    # Map foreground color -> matching shadow color for vanilla pairs.
    paired_shadow = {
        "RED":   "LIGHT_RED",
        "GREEN": "LIGHT_GREEN",
        "BLUE":  "LIGHT_BLUE",
    }
    if not color_runs:
        return plain_text

    ctx = context or {}
    observed = ctx.get("shadows") or {}
    keep_trailing_reset = ctx.get("trailing_reset", True)

    out: list[str] = []
    cursor = 0
    n = len(plain_text)
    for start, end, color in sorted(color_runs):
        if start < cursor:
            # Overlapping run — skip to maintain valid output. This
            # shouldn't happen in normal use but defends against bugs.
            continue
        out.append(plain_text[cursor:start])
        # The shadow the AUTHOR wrote wins over the three-entry guess table.
        shadow = observed.get(color) or paired_shadow.get(color)
        if shadow:
            out.append(f"{{COLOR {color}}}{{SHADOW {shadow}}}")
        else:
            out.append(f"{{COLOR {color}}}")
        out.append(plain_text[start:end])
        # Put the reset AFTER any line breaks that immediately follow, which is
        # where the source files put it: `…question?\p{COLOR DARK_GRAY}…`, not
        # `…question?{COLOR DARK_GRAY}\p…`. Both render identically, but
        # emitting it before the break rewrote 26 of the Fame Checker's strings
        # on a save that changed nothing.
        after = end
        while after < n and plain_text[after] == "\n":
            after += 1
        out.append(plain_text[end:after])
        # A reset is only needed when text FOLLOWS the run. At end-of-text it
        # is optional, and adding one the source didn't have is a 36-character
        # change to data nobody edited.
        if after < n or keep_trailing_reset:
            if shadow:
                out.append("{COLOR DARK_GRAY}{SHADOW LIGHT_GRAY}")
            else:
                out.append("{COLOR DARK_GRAY}")
        cursor = after
    out.append(plain_text[cursor:])
    return "".join(out)


# ── Conversion helpers (.inc / EVENTide internal <-> display) ──────────────


def inc_to_display(raw: str) -> tuple[str, dict, list[tuple[int, int, str]]]:
    """Convert ``.inc`` text into display form for the rich editor.

    Returns ``(plain_text, escape_map, color_runs)``:

      * ``plain_text`` — what to show in the editor: user's words,
        ``{PLAYER}``-style placeholders, and Unicode emoji glyphs (no
        ``{COLOR}`` / ``{EMOJI_*}`` tokens).
      * ``escape_map`` — line-break escapes (``\\n`` / ``\\p`` / ``\\l``)
        in order, so ``display_to_inc`` can restore them on round-trip.
      * ``color_runs`` — ``[(start, end, color_name)]`` ranges on the
        plain text, ready to apply as character formats.
    """
    text = raw.rstrip("$").rstrip()

    # Line breaks use the EDIT-PROOF blank-line convention, NOT a positional
    # escape_map.  The old escape_map recorded each break's kind (\n/\p/\l) by
    # newline INDEX and restored it the same way on save — which desynced the
    # instant the user added or removed a line, silently turning every \p past
    # the edit point into \n.  Worse, the trainer-dialogue tab feeds this
    # function parse_text_inc output (already real newlines), so the escape_map
    # came up empty and EVERY save flattened \p -> \n.  That was the gym-leader
    # "one giant unbroken intro speech / 10-second text crawl" corruption.
    #
    # The fix carries the break KIND in the text structure itself:
    #   \p  <-> blank line (\n\n)   — new textbox screen / paragraph
    #   \n  <-> single newline      — line break within a screen
    #   \l  -> kept as a literal visible token (rare scroll code)
    # The GBA message box is 2 lines, so two consecutive breaks always mean a
    # new screen — making blank-line == \p unambiguous and safe.  This works
    # whether the input arrives as literal escapes (EVENTide / text editor) or
    # as already-real newlines (trainer tab): the literal-escape replaces below
    # are simply no-ops in the latter case, and the real \n\n is handled on the
    # way back out by display_to_inc.
    text_with_nl = text.replace("\\p", "\n\n").replace("\\n", "\n")

    # Replace emoji tokens with Unicode glyphs FIRST. Doing this before
    # the color parse means the resulting ``color_runs`` positions are
    # already in the post-emoji coordinate system, which is what the
    # editor's QTextDocument will use.
    text_with_nl_emoji = _replace_emoji_tokens(text_with_nl)

    # Strip color tokens, capture runs.
    plain_text, color_runs = parse_color_runs(text_with_nl_emoji)

    # The second slot was the old line-break `escape_map`, returned empty and
    # ignored ever since the break kind moved into the text structure. It now
    # carries what the colour parse would otherwise discard — the author's own
    # SHADOW pairings and whether the text ended with a reset — so a save
    # reproduces the source instead of re-deriving it from a guess table.
    # Callers only ever pass it straight back to `display_to_inc`.
    return plain_text, color_shadow_context(raw), color_runs


def display_to_inc(plain_text: str,
                   color_runs: list[tuple[int, int, str]],
                   escape_map: dict | None = None) -> str:
    """Convert display state back to ``.inc`` form.

    Uses ``escape_map`` (from ``inc_to_display``) to restore the
    original escape sequences (``\\n`` / ``\\p`` / ``\\l``). Defaults
    to ``\\n`` for any newline beyond what's recorded.

    Color runs are emitted as ``{COLOR X}{SHADOW LIGHT_X}...{COLOR
    DARK_GRAY}{SHADOW LIGHT_GRAY}`` token pairs, then emoji glyphs
    are converted back to ``{EMOJI_*}`` tokens, then newlines are
    converted to escape sequences. Order matters: re-emit colors
    before newline conversion so the color run positions still apply
    in the post-emoji string.
    """
    ctx = escape_map if isinstance(escape_map, dict) else None
    text = emit_color_runs(plain_text, color_runs, ctx)
    text = _replace_emoji_glyphs(text)

    # Line breaks back to escapes via the blank-line convention (the inverse of
    # inc_to_display).  ``escape_map`` is accepted for call-site compatibility
    # but DELIBERATELY ignored — the break kind is now carried by the text
    # structure (blank line == \p), which survives arbitrary editing instead of
    # desyncing like the old positional map did.
    #   \n\n (blank line) -> \p   ;   remaining \n -> \n
    # Any literal \l already in the text passes through untouched.
    result = text.replace("\n\n", "\\p").replace("\n", "\\n")
    if not result.endswith("$"):
        result += "$"
    return result


def eventide_to_display(internal: str) -> tuple[str, dict, list[tuple[int, int, str]]]:
    """Convert EVENTide's internal form (real ``\\n`` and ``\\n\\n``)
    into display state for the rich editor."""
    inc = internal.replace("\n\n", "\\p").replace("\n", "\\n")
    return inc_to_display(inc)


def display_to_eventide(plain_text: str,
                        color_runs: list[tuple[int, int, str]],
                        escape_map: dict | None = None) -> str:
    """Convert display state back to EVENTide's internal form."""
    inc = display_to_inc(plain_text, color_runs, escape_map)
    result = inc.rstrip("$")
    result = result.replace("\\p", "\n\n").replace("\\n", "\n").replace("\\l", "\n")
    result += "$"
    return result


# ── Display-character counting (excludes structural tokens) ─────────────────

def display_char_count(line: str) -> int:
    """Count visible characters in a line, excluding ``{COMMAND}`` tokens.

    ``{COMMAND}`` tokens are 0-character placeholders for in-game
    substitution; the GBA text engine doesn't reserve display columns
    for them. Our editor follows suit so the per-line counter doesn't
    over-count when the user types ``Hi {PLAYER}!`` in a 12-char limit.

    Emoji Unicode glyphs and other normal characters count as 1 each.
    """
    clean = _COMMAND_RE.sub("", line)
    return len(clean)


# ── Counter HTML rendering ─────────────────────────────────────────────────


def _build_counter_html(lines: list[str], max_cpl: int, max_lines: int) -> str:
    parts: list[str] = []
    for i, line in enumerate(lines[:max_lines + 5]):
        n = display_char_count(line)
        if n > max_cpl:
            color = _CLR_RED
            weight = "bold"
        elif n > int(max_cpl * 0.85):
            color = _CLR_AMBER
            weight = "bold"
        else:
            color = _CLR_NORMAL
            weight = "normal"
        parts.append(
            f'<span style="color:{color}; font-weight:{weight}; '
            f'font-size:10px;">L{i + 1}: {n}/{max_cpl}</span>'
        )
        if i < len(lines) - 1:
            parts.append('  ')
    return "".join(parts)


# ── Rich-document helpers ──────────────────────────────────────────────────


def _apply_color_runs_to_document(
    edit: QTextEdit,
    color_runs: list[tuple[int, int, str]],
) -> None:
    """Apply ``[(start, end, color_name)]`` runs to the editor's document
    as character formats. Positions are document character offsets.
    """
    cursor = QTextCursor(edit.document())
    for start, end, color_name in color_runs:
        hexcode = _COLOR_PREVIEW_HEX.get(color_name)
        if not hexcode:
            continue
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(hexcode))
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        cursor.mergeCharFormat(fmt)


def _document_to_color_runs(
    edit: QTextEdit,
    implicit_hex: str,
) -> list[tuple[int, int, str]]:
    """Walk the document's character formats and return color runs.

    A "run" is a contiguous range whose foreground colour matches one
    of the named engine colours (and is not the implicit / default).
    Runs whose colour is the implicit one are NOT emitted — they
    represent the un-coloured default text and don't need any tokens.

    Position coordinates are post-emoji (the document holds Unicode
    glyphs, not ``{EMOJI_*}`` tokens). ``display_to_inc`` re-converts
    glyphs to tokens after the runs are wrapped.
    """
    runs: list[tuple[int, int, str]] = []
    doc = edit.document()
    block = doc.firstBlock()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            if frag.isValid() and frag.length() > 0:
                fg = frag.charFormat().foreground().color()
                hexcode = "#%02x%02x%02x" % (fg.red(), fg.green(), fg.blue())
                # Find a matching named color (case-insensitive hex match).
                matched_name: Optional[str] = None
                matched_hex: Optional[str] = None
                for name, ref_hex in _COLOR_PREVIEW_HEX.items():
                    if (ref_hex.lower() == hexcode.lower()
                            and name != DEFAULT_COLOR_NAME):
                        matched_name = name
                        matched_hex = ref_hex
                        break
                # Skip when the run is in the implicit color (default
                # text) — it doesn't need a token. ``frag.position()``
                # is already a document-absolute offset; we use it
                # directly without re-adding ``block.position()``.
                if (matched_name
                        and matched_hex is not None
                        and matched_hex.lower() != implicit_hex.lower()):
                    start = frag.position()
                    runs.append((
                        start, start + frag.length(), matched_name))
            it += 1
        block = block.next()

    # Merge same-colour runs that are adjacent, OR separated only by line
    # breaks.
    #
    # The document stores each line as its own block, so a colour spanning a
    # line break arrives as two fragments with the break's newline character
    # sitting between them — `r[0] == last[1]` is false by exactly that one
    # position. Without this, a four-page coloured message came back with the
    # colour closed and re-opened around every page: 86 of the Fame Checker's
    # 324 strings grew by a pair of colour tokens per line on a save that
    # changed nothing.
    if not runs:
        return runs
    plain = edit.toPlainText()
    merged: list[tuple[int, int, str]] = [runs[0]]
    for r in runs[1:]:
        last = merged[-1]
        gap = plain[last[1]:r[0]]
        if r[2] == last[2] and (not gap or not gap.strip("\n")):
            merged[-1] = (last[0], r[1], last[2])
        else:
            merged.append(r)
    return merged


# ── GameTextEdit widget ────────────────────────────────────────────────────


class GameTextEdit(QWidget):
    """Rich-text editor for GBA game text.

    Holds a ``QTextEdit`` whose document carries the user's content
    (including Unicode emoji glyphs and ``{COMMAND}`` placeholders)
    plus per-character color formats. ``{COLOR X}`` and ``{EMOJI_*}``
    tokens are stripped on load and regenerated on save — they never
    appear inside the document the user is typing in.
    """

    def __init__(
        self,
        max_chars_per_line: int = DEFAULT_CHARS_PER_LINE,
        max_lines: int = DEFAULT_MAX_LINES,
        parent: QWidget | None = None,
        show_toolbar: bool = True,
    ):
        super().__init__(parent)
        self._max_cpl = max_chars_per_line
        self._max_lines = max_lines
        # Implicit color: the foreground used for un-coloured text.
        # Set by callers via set_implicit_color() to preview NPC
        # gender-tinted dialogue. Kept as a name (not hex) so save
        # can pass it to _document_to_color_runs for filtering.
        self._implicit_color_name = DEFAULT_COLOR_NAME

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        # Rich-text editor. Background mimics pokefirered's cream
        # textbox so DARK_GRAY default text reads correctly without
        # collision with Qt's dark theme.
        self._edit = QTextEdit()
        self._edit.setAcceptRichText(False)  # prevent paste of HTML
        self._edit.setFont(QFont("Courier New", 9))
        self._edit.setStyleSheet(
            "QTextEdit { background: #f8f0d8; "
            f"color: {_COLOR_PREVIEW_HEX[DEFAULT_COLOR_NAME]}; "
            "border: 1px solid #888; selection-background-color: #b8d8f0; "
            "selection-color: #000; }"
        )
        self._edit.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self._edit.customContextMenuRequested.connect(self._show_context_menu)

        # Formatting toolbar above the editor.
        self._toolbar: QWidget | None = None
        if show_toolbar:
            from ui.text_format_toolbar import TextFormatToolbar
            self._toolbar = TextFormatToolbar(self._edit, parent=self)
            layout.addWidget(self._toolbar)

        layout.addWidget(self._edit)

        # Per-line character counter
        self._counter = QLabel()
        self._counter.setStyleSheet("font-size: 10px; padding: 0 2px;")
        layout.addWidget(self._counter)

        # Escape map for round-tripping \n / \p / \l
        self._escape_map: dict = {}
        # Snapshot of what was loaded, so an untouched field round-trips
        # byte-exactly. See `_remember_source`.
        self._source_inc: Optional[str] = None
        self._source_plain: str = ""
        self._source_runs = None

        # Highlighter — only blue-bolds {COMMAND} tokens now.
        self._highlighter = _CommandHighlighter(self._edit.document())

        # Block Enter past line limit
        self._edit.installEventFilter(self)
        self._edit.textChanged.connect(self._refresh_counter)

    # ── public API ───────────────────────────────────────────────────────────

    @property
    def editor(self) -> QTextEdit:
        return self._edit

    def set_inc_text(self, raw: str) -> None:
        """Load ``.inc``-format text. Color tokens become character
        formats; emoji tokens become Unicode glyphs."""
        plain, self._escape_map, color_runs = inc_to_display(raw)
        self._set_document_text(plain, color_runs)
        self._remember_source(raw)

    def get_inc_text(self) -> str:
        """Serialize the document back to ``.inc`` form (with color
        tokens, emoji tokens, and the trailing ``$``).

        NOT a pure function of the visible document: when `set_inc_text`
        seeded a snapshot and nothing has changed since, this returns that
        original text verbatim (see `_remember_source`). A caller that builds
        a widget and reads it WITHOUT seeding gets regeneration instead of
        passthrough — correct, but worth knowing before relying on either.
        """
        unchanged = self._source_if_unchanged()
        if unchanged is not None:
            return unchanged
        plain = self._edit.toPlainText()
        runs = _document_to_color_runs(
            self._edit, _COLOR_PREVIEW_HEX[self._implicit_color_name])
        return display_to_inc(plain, runs, self._escape_map)

    # ── "unchanged content produces unchanged bytes" ───────────────────────

    def unrepresentable_reason(self) -> str:
        """Why editing THIS text would lose something, or "".

        The document model holds visible text plus the colours it has names
        for. Anything else — a colour outside the preview table, a
        `{HIGHLIGHT}`, load-bearing trailing spaces — survives an untouched
        save only because the snapshot returns the original verbatim. The
        moment the user edits, it is regenerated from the model and that
        detail is gone.

        The information is already in hand at snapshot time, so say so at the
        moment it matters rather than leaving it as a documented footnote.
        """
        raw = self._source_inc
        if not raw:
            return ""
        unknown = sorted({
            m.group(2) for m in _COLOR_TOKEN_RE.finditer(raw)
            if m.group(1) in ("COLOR", "SHADOW")
            and m.group(2) not in _COLOR_PREVIEW_HEX})
        if unknown:
            return ("This text uses colours this editor can't show ("
                    + ", ".join(unknown[:3])
                    + ") — editing it will lose them.")
        if "{HIGHLIGHT" in raw:
            return ("This text uses a highlight this editor can't show — "
                    "editing it will lose it.")
        if raw != raw.rstrip() and raw.rstrip():
            return ("This text ends in spaces that do something in game — "
                    "editing it will remove them.")
        return ""

    def _remember_source(self, raw: str) -> None:
        """Snapshot what was loaded, so an untouched field can be re-emitted
        verbatim instead of regenerated.

        The document model is deliberately LOSSY — it holds visible text plus
        the colours it has names for, which is the right shape for editing but
        cannot represent everything a `.string` can carry. Regenerating from it
        therefore rewrites things nobody edited: a `{HIGHLIGHT}`, a colour the
        preview table doesn't know (`DYNAMIC_COLOR6` came back with the colour
        gone entirely), trailing spaces that are load-bearing, or a reset token
        on the other side of a page break.

        Chasing those one at a time is endless — each is a different way the
        model is narrower than the format. The general answer is the same one
        the file writer uses: if nothing changed, emit exactly what came in.
        Any real edit — a keystroke or a colour change — falls through to
        normal regeneration.
        """
        self._source_inc = raw
        self._source_plain = self._edit.toPlainText()
        try:
            self._source_runs = _document_to_color_runs(
                self._edit, _COLOR_PREVIEW_HEX[self._implicit_color_name])
        except Exception:                            # pragma: no cover
            self._source_runs = None

    def _source_if_unchanged(self):
        """The originally-loaded text, or None if the user has edited it."""
        if self._source_inc is None or self._source_runs is None:
            return None
        if self._edit.toPlainText() != self._source_plain:
            return None
        try:
            now = _document_to_color_runs(
                self._edit, _COLOR_PREVIEW_HEX[self._implicit_color_name])
        except Exception:                            # pragma: no cover
            return None
        return self._source_inc if now == self._source_runs else None

    def set_eventide_text(self, internal: str) -> None:
        plain, self._escape_map, color_runs = eventide_to_display(internal)
        self._set_document_text(plain, color_runs)
        self._remember_source(None)   # EVENTide's form differs from .inc

    def get_eventide_text(self) -> str:
        plain = self._edit.toPlainText()
        runs = _document_to_color_runs(
            self._edit, _COLOR_PREVIEW_HEX[self._implicit_color_name])
        return display_to_eventide(plain, runs, self._escape_map)

    def get_display_text(self) -> str:
        """Plain visible text (Unicode glyphs intact, no tokens)."""
        return self._edit.toPlainText()

    def _set_document_text(
        self,
        plain: str,
        color_runs: list[tuple[int, int, str]],
    ) -> None:
        """Atomically replace the document content + apply color runs."""
        self._edit.blockSignals(True)
        try:
            self._edit.setPlainText(plain)
            _apply_color_runs_to_document(self._edit, color_runs)
        finally:
            self._edit.blockSignals(False)
        self._refresh_counter()

    def set_limits(self, max_chars_per_line: int, max_lines: int) -> None:
        self._max_cpl = max_chars_per_line
        self._max_lines = max_lines
        self._refresh_counter()

    def setMaximumHeight(self, h: int) -> None:
        self._edit.setMaximumHeight(h)

    def setPlaceholderText(self, text: str) -> None:
        self._edit.setPlaceholderText(text)

    def connectChanged(self, slot) -> None:
        self._edit.textChanged.connect(slot)

    def set_implicit_color(self, color_name: str) -> None:
        """Set the default text color for un-coloured text.

        Applied as the editor's stylesheet color so newly-typed text
        and any text without an explicit format renders in this
        color. Save-time emission filters out runs in this color so
        no tokens are emitted for "default" text.
        """
        if color_name not in _COLOR_PREVIEW_HEX:
            color_name = DEFAULT_COLOR_NAME
        if color_name == self._implicit_color_name:
            return
        self._implicit_color_name = color_name
        hexcode = _COLOR_PREVIEW_HEX[color_name]
        self._edit.setStyleSheet(
            f"QTextEdit {{ background: #f8f0d8; color: {hexcode}; "
            f"border: 1px solid #888; selection-background-color: #b8d8f0; "
            f"selection-color: #000; }}"
        )

    def set_braille_mode(self, braille: bool) -> None:
        """Switch the editor between Normal and Braille rendering."""
        if self._toolbar is not None:
            self._toolbar.set_braille_mode(braille)
        if braille:
            self._edit.setStyleSheet(
                "QTextEdit { background: #2a2419; color: #d4c896; "
                "border: 1px solid #5a4a2a; }")
        else:
            # Restore to whatever the implicit color is.
            hexcode = _COLOR_PREVIEW_HEX[self._implicit_color_name]
            self._edit.setStyleSheet(
                f"QTextEdit {{ background: #f8f0d8; color: {hexcode}; "
                f"border: 1px solid #888; "
                f"selection-background-color: #b8d8f0; selection-color: #000; }}"
            )

    # ── event filter — block Enter at line limit ─────────────────────────────

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self._edit and isinstance(event, QKeyEvent):
            if event.type() == QEvent.Type.KeyPress:
                if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    if self._edit.toPlainText().count("\n") >= self._max_lines - 1:
                        return True
        return False

    # ── context menu — same insert categories as the toolbar ─────────────────

    def _show_context_menu(self, pos):
        menu = self._edit.createStandardContextMenu()
        menu.addSeparator()
        insert_menu = QMenu("Insert", menu)
        insert_menu.setStyleSheet(
            "QMenu { background: #2b2b2b; color: #ddd; }"
            "QMenu::item:selected { background: #1565c0; }"
        )
        for category, commands in _COMMAND_CATEGORIES.items():
            cat_menu = QMenu(category, insert_menu)
            cat_menu.setStyleSheet(insert_menu.styleSheet())
            for cmd in commands:
                action = QAction(cmd, cat_menu)
                action.triggered.connect(
                    lambda checked=False, c=cmd: self._insert_command(c)
                )
                cat_menu.addAction(action)
            insert_menu.addMenu(cat_menu)
        menu.addMenu(insert_menu)
        menu.exec(self._edit.mapToGlobal(pos))

    def _insert_command(self, cmd: str) -> None:
        cursor = self._edit.textCursor()
        cursor.insertText(cmd)
        self._edit.setTextCursor(cursor)

    # ── refresh counter ──────────────────────────────────────────────────────

    def _refresh_counter(self) -> None:
        txt = self._edit.toPlainText()
        lines = txt.split("\n")
        self._counter.setText(
            _build_counter_html(lines, self._max_cpl, self._max_lines)
        )


# ── attach_game_text_ui (legacy — pre-existing API) ────────────────────────


def attach_game_text_ui(
    edit: QTextEdit,
    max_chars_per_line: int = DEFAULT_CHARS_PER_LINE,
    max_lines: int = DEFAULT_MAX_LINES,
) -> "_GameTextAttachment":
    """Attach character limits + {COMMAND} highlighting to an existing
    QTextEdit (used by widgets that need their own edit instance but
    still want PorySuite's text styling). Color preview / emoji
    conversion are NOT included by this attachment — those require
    GameTextEdit's full set/get pipeline. For full features, use
    GameTextEdit directly instead of attaching."""
    return _GameTextAttachment(edit, max_chars_per_line, max_lines)


class _GameTextAttachment(QObject):
    """Lightweight attachment — counter label + highlighter only."""

    def __init__(self, edit: QTextEdit, max_cpl: int, max_lines: int):
        super().__init__(edit)
        self._edit = edit
        self._max_cpl = max_cpl
        self._max_lines = max_lines
        self._highlighter = _CommandHighlighter(edit.document())
        edit.installEventFilter(self)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self._edit and isinstance(event, QKeyEvent):
            if event.type() == QEvent.Type.KeyPress:
                if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    plain = (
                        self._edit.toPlainText()
                        if hasattr(self._edit, "toPlainText")
                        else "")
                    if plain.count("\n") >= self._max_lines - 1:
                        return True
        return False
