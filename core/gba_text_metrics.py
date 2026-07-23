"""Exact GBA string-width measurement, parsed from the project's own source.

Why this exists
---------------
Every text field in this app that says "you have N characters left" is lying a
little: the GBA renders a **proportional** font, so `WWWWW` is nearly twice as
wide as `iiiii`. Counting characters therefore flags perfectly legal shipped
text as overflow, and passes text that genuinely runs off the window.

This module measures the real thing. Everything it needs is already sitting in
the user's decomp:

* ``charmap.txt``               — character -> byte value
* ``src/text.c``                — ``sFont<Name>LatinGlyphWidths[]``, one pixel
                                  width per byte value

``width(str) = sum(glyph_width[byte])`` — that and nothing else.

Four engine behaviours, each of which makes vanilla's own text measure as
overflowing if you get it wrong
-------------------------------------------------------------------------
**1. letterSpacing does not apply to Latin text.** Both the renderer and the
measurer in ``src/text.c`` gate it on the Japanese flag::

    // RenderText()
    if (textPrinter->japanese)
        currentX += (gGlyphInfo.width + letterSpacing);
    else
        currentX += gGlyphInfo.width;

    // GetStringWidth()
    lineWidth += isJapanese ? glyphWidth + localLetterSpacing : glyphWidth;

So ``letterSpacing = 1`` is inert for English. Adding it inflates a 35-character
line by 35px and flags 111 of vanilla Fame Checker's 420 lines. Without it the
widest vanilla line is 199px and all 420 fit. Do not "fix" this back.

**2. ``{FONT_MALE}`` switches font mid-string and it sticks** — across ``\\n``,
``\\l`` and ``\\p``, to the end of the entry. Half of vanilla does this.

**3. ``\\p`` resets the cursor; ``\\l`` does not.** ``RENDER_STATE_CLEAR``
resets ``currentX`` **and** ``currentY``. ``RENDER_STATE_SCROLL_START`` resets
``currentX`` **only** and scrolls the window up by one line. So within a page
the line slot keeps climbing on every ``\\n`` no matter how many ``\\l``s
intervene: ``A\\nB\\lC\\nD`` puts ``D`` one line below the window floor, where
it renders as a clipped sliver. See `layout`'s slot model.

**4. Control codes cost zero pixels** — ``{COLOR DARK_GRAY}{SHADOW LIGHT_GRAY}``
is 36 characters and 0px wide.

Project-agnostic by construction
--------------------------------
Nothing here is hardcoded to vanilla. The font list, the glyph widths and the
charmap all come from whatever the user's project actually contains — a project
that redrew its font or added glyphs measures correctly. If any piece can't be
parsed, or parses into something that fails a sanity check, the measurer
degrades to an average width and reports ``exact=False`` so the UI can say
"approximate" instead of pretending.

It never blocks typing and never truncates. It colours and warns.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache

_log = logging.getLogger("PorySuite.GbaText")

# Fallback average pixel widths, used only when the real tables can't be read.
_FALLBACK_PX = {"FONTSMALL": 5.2, "FONTNORMAL": 6.0}
_FALLBACK_DEFAULT = 6.0

# `{FOO}` and `{FOO BAR}` are control codes: zero pixels on screen.
_CTRL_RE = re.compile(r"\{[^}]*\}")

# Line/page structure, as literal two-character escapes in the .inc source.
_PAGE = "\\p"       # new page: window cleared, cursor home
_SCROLL = "\\l"     # scroll up one line; cursor column resets, ROW DOES NOT
_NEWLINE = "\\n"    # next line down
_BREAKS = (_PAGE, _SCROLL, _NEWLINE)

# Any other backslash escape is control, not a glyph.
_ESCAPE_RE = re.compile(r"\\[a-zA-Z]")

# Placeholders whose real width is only known at runtime. We substitute a
# plausible stand-in so the count is a sane lower bound rather than zero, and
# flag the line so the UI can say "plus whatever this expands to".
_PLACEHOLDER_STANDIN = {
    "PLAYER": "PLAYERR",         # PLAYER_NAME_LENGTH is 7
    "RIVAL": "RIVALNM",
    "STR_VAR_1": "AAAAAAA",
    "STR_VAR_2": "AAAAAAA",
    "STR_VAR_3": "AAAAAAA",
}


@lru_cache(maxsize=256)
def _norm_font(name: str) -> str:
    """FONT_NORMAL_COPY_1 / sFontNormalCopy1... -> NORMALCOPY1.

    Both sides of the lookup are normalised the same way, because the C source
    spells the same font two different ways: `FONT_NORMAL_COPY_1` in the enum and
    `sFontNormalCopy1LatinGlyphWidths` in the table name. Comparing them raw
    silently misses and falls back to an average.
    """
    s = re.sub(r"[^A-Za-z0-9]", "", name or "").upper()
    return s[4:] if s.startswith("FONT") else s


# ─────────────────────────── charmap.txt ────────────────────────────────────

# `'A' = BB`, `'=' = 35`, `'\'' = B4`, `'\l' = FA`. The left side is a quoted
# literal that may itself contain `=` or an escaped quote, so the value is
# matched from the END of the line rather than by splitting on the first `=`.
_CHARMAP_RE = re.compile(r"^'(\\.|[^'])'\s*=\s*([0-9A-Fa-f]{2})\s*$")


def _read_charmap_text(path: str) -> str:
    """Read charmap.txt without silently corrupting non-ASCII glyph names.

    `errors="replace"` would turn every accented character into U+FFFD and hand
    back a charmap that looks fine and is wrong. Try UTF-8 strictly, then the
    legacy encoding a Windows editor would have written, and give up rather
    than guess.
    """
    # utf-8-sig FIRST: it strips a BOM and still decodes plain UTF-8. Without
    # it a Windows-edited charmap loses its first entry (the space character),
    # which is in the sanity check, so the whole project degrades to estimates.
    for enc in ("utf-8-sig", "cp1252"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read()
        except UnicodeDecodeError:
            continue
        except OSError:
            return ""
    return ""


def _parse_charmap(root: str) -> tuple:
    """``charmap.txt`` -> ({character: byte}, collision count, collided set).

    The collision COUNT and the SET are both needed and measure different
    things. A file mangled by the wrong codec collapses 169 distinct glyphs onto
    ONE replacement character: the count is 169, the set has one member. Testing
    only the set's size would let that through. A correctly-decoded charmap has
    almost no collisions; the mangled dict is otherwise perfectly plausible, so
    this is the only place the corruption shows up at all.
    """
    raw = _read_charmap_text(os.path.join(root, "charmap.txt"))
    if not raw:
        return {}, 0, set()

    out = {}
    collisions = 0
    collided = set()
    for line in raw.splitlines():
        line = line.split("@", 1)[0].strip()          # strip trailing comments
        m = _CHARMAP_RE.match(line)
        if not m:
            continue                                   # multi-byte / symbolic
        lit, val = m.group(1), m.group(2)
        if lit.startswith("\\"):
            esc = lit[1]
            if esc in ("'", "\\"):
                ch = esc                               # \' and \\ are literals
            else:
                # `'\l'`, `'\n'`, `'\p'` name the LINE-BREAK bytes, not the
                # letters l/n/p. Un-escaping them would map three common
                # letters onto 0xFA/0xFE/0xFB and mis-measure every word
                # containing them.
                continue
        else:
            ch = lit
        if ch in out:
            collisions += 1
            collided.add(ch)
            continue
        out[ch] = int(val, 16)
    return out, collisions, collided


_NEED_CHARS = "ABCXYZabcxyz0123456789 .,!?'"


def _charmap_is_sane(cm: dict, collisions: int, collided: set) -> bool:
    """Reject a charmap that parsed into nonsense rather than trust it.

    A mis-decoded or truncated file yields a dict that is non-empty and useless.
    Checking that the basics are present, that no replacement character got in,
    and that distinct glyphs didn't collapse onto each other is what stops this
    module reporting `exact=True` over guessed widths.
    """
    if len(cm) < 64 or "�" in cm:
        return False
    if collisions > max(4, len(cm) // 20):
        return False
    # A ratio alone lets a big, partly-mangled file squeak through. If a
    # character we depend on was named twice, the file is wrong however small
    # the ratio is.
    if collided & set(_NEED_CHARS):
        return False
    return all(c in cm for c in _NEED_CHARS)


# ─────────────────────────── src/text.c ─────────────────────────────────────

_WIDTHS_RE = re.compile(
    r"sFont(\w+?)LatinGlyphWidths\s*\[\s*\]\s*=\s*\{(.*?)\}\s*;",
    re.DOTALL,
)


def _strip_c_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", "", src)


def _parse_glyph_widths(root: str) -> dict:
    """``src/text.c`` -> {"NORMAL": [w0, w1, ...], "SMALL": [...]}."""
    path = os.path.join(root, "src", "text.c")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            src = _strip_c_comments(fh.read())
    except OSError:
        return {}

    out = {}
    for m in _WIDTHS_RE.finditer(src):
        name, body = m.group(1), m.group(2)
        widths = []
        ok = True
        for tok in body.replace("\n", " ").split(","):
            tok = tok.strip()
            if not tok:
                continue
            try:
                widths.append(int(tok, 0))
            except ValueError:
                # A designated initialiser or an expression - we can't trust
                # positional indexing any more, so drop this font entirely
                # rather than measure against a shifted table.
                ok = False
                break
        if ok and widths:
            out[_norm_font(name)] = widths
    return out


# ─────────────────────────── the measurer ───────────────────────────────────

@dataclass
class GbaTextMetrics:
    """Measures strings in real GBA pixels for one project.

    `exact` is False when a required table was missing, unparseable, or failed a
    sanity check — the numbers are then averages and the UI must present them as
    approximate.
    """

    charmap: dict = field(default_factory=dict)
    widths: dict = field(default_factory=dict)      # "NORMAL" -> [px, ...]
    exact: bool = False

    # Bounded cache, keyed on the run text actually measured. `layout` and
    # `width_px` share it because both bottom out in `_run_px`.
    _cache: dict = field(default_factory=dict, repr=False)

    # ---- public API ------------------------------------------------------

    def width_px(self, text: str, font: str = "FONT_NORMAL",
                 expand_placeholders: bool = True) -> int:
        """Rendered width of *text* in pixels, control codes excluded.

        `expand_placeholders=False` for a field the engine prints WITHOUT
        running `StringExpandPlaceholders`: `GetStringWidth` returns 0 for an
        unexpanded placeholder there, so charging a stand-in width would report
        a plausible number for text the game draws as nothing.
        """
        total = 0.0
        for f, body, ph in self._runs(text or "", font):
            if ph and not expand_placeholders:
                continue
            total += self._run_px(body, f)
        return int(round(total))

    def approx_chars(self, budget_px: int, font: str = "FONT_NORMAL") -> int:
        """Rough characters-per-line, for a human-readable hint only.

        Never used to decide overflow — `width_px` and `layout` do that.
        """
        return max(1, int(budget_px / self._avg_px(font)))

    def layout(self, text: str, font: str = "FONT_NORMAL",
               expand_placeholders: bool = True) -> list:
        """Break *text* into pages of lines, with pixel widths and line slots.

        The slot model, straight from `RenderText`:

        * ``\\p`` clears the window — slot returns to 0.
        * ``\\n`` moves down one line — slot += 1.
        * ``\\l`` scrolls the window up one line and resets the column only —
          **slot is unchanged**, which is what makes a scroll free.

        A line whose slot is >= the window's line capacity is drawn below the
        window floor and renders clipped. That is why the budget is checked
        against the slot rather than against a per-scroll-group line count: a
        group-based count says ``A\\nB\\lC\\nD`` is fine, and the engine says
        ``D`` is a sliver.

        Each line also carries its ``start``/``end`` offsets in the ORIGINAL
        string and the font in effect when it began, so `overflow_spans` can be
        derived from this rather than re-walking the text. That is deliberate:
        two independent walks disagreed four separate times (control-code
        overflow, escapes, empty lines, per-run rounding), and each one-sided
        fix produced a new divergence in the opposite direction. One walk, one
        width, no divergence possible.

        Returns ``[[{px, font, text, slot, start, end, has_placeholder,
        approx}, ...], ...]`` — one list of line dicts per page.
        """
        pages = []
        cur = _norm_font(font)
        line_font = cur
        slot = 0
        line = []
        page = []
        line_start = 0

        def flush_line(end):
            nonlocal line, line_start, line_font
            px = int(round(sum(p for p, _f, _ph, _a in line)))
            page.append({
                "px": px, "font": line_font, "slot": slot,
                "text": "".join(f for _p, f, _ph, _a in line),
                "start": line_start, "end": end,
                "has_placeholder": any(ph for _p, _f, ph, _a in line),
                "approx": any(a for _p, _f, _ph, a in line),
            })
            line = []
            line_font = cur

        for kind, payload, start, end in self._tokens(text or ""):
            if kind == "text":
                for f, body, ph in self._runs(payload, cur):
                    cur = f
                    # `approx` must also be true when the CHARMAP is unusable —
                    # every character is then an average even though the font
                    # itself resolved. Reporting False there is a lie any future
                    # consumer of the flag would act on.
                    known = _norm_font(f) in self.widths and bool(self.charmap)
                    px = 0.0 if (ph and not expand_placeholders) \
                        else self._run_px(body, f)
                    line.append((px, body, ph, not known))
                # a control code may switch the font with no text after it
                cur = self._terminal_font(payload, cur)
            else:
                flush_line(start)
                line_start = end
                if kind == _NEWLINE:
                    slot += 1
                elif kind == _PAGE:
                    pages.append(page)
                    page = []
                    slot = 0
                # _SCROLL: column resets, row does NOT
        flush_line(len(text or ""))
        pages.append(page)
        return pages

    def overflow_spans(self, text: str, font: str, budget_px: int,
                       lines_per_page: int,
                       expand_placeholders: bool = True) -> list:
        """Character ranges of *text* that will not render correctly.

        Returns ``[(start, end), ...]`` as offsets into the ORIGINAL string, so
        the editor can paint them red in place. Two causes:

        * the pixels past `budget_px` on a line — everything from the first
          character that crosses the window edge to the end of that line;
        * every character of a line whose slot is below the window floor.

        **Derived from `layout`, never re-walked.** Every verdict here reads the
        same `px` the counter shows, so the two cannot contradict each other —
        which four separate bugs did while this was an independent walk. The
        only thing this adds is *where* on an over-wide line the text crosses the
        window edge, and that is a scan of one line's own source.
        """
        spans = []
        text = text or ""
        for page in self.layout(text, font, expand_placeholders):
            for line in page:
                if line["slot"] >= lines_per_page and line["px"] > 0:
                    spans.append((line["start"], line["end"]))
                elif line["px"] > budget_px:
                    at = self._crossing(text, line, budget_px,
                                        expand_placeholders)
                    if at is not None:
                        spans.append((at, line["end"]))
        return spans

    def _crossing(self, text: str, line: dict, budget_px: int,
                  expand_placeholders: bool):
        """Offset of the first character on *line* that crosses the budget."""
        px = 0.0
        cur = line["font"]
        pos, end = line["start"], line["end"]
        while pos < end:
            m = _CTRL_RE.match(text, pos)
            if m and m.end() <= end:
                cur, ph = self._ctrl_effect(m.group(0), cur)
                if ph and expand_placeholders:
                    px += self._run_px(ph, cur)
                    if round(px) > budget_px:
                        return m.start()
                pos = m.end()
                continue
            esc = _ESCAPE_RE.match(text, pos)
            if esc and esc.end() <= end:
                pos = esc.end()                     # not a glyph; costs nothing
                continue
            if text[pos] == "$":
                pos += 1
                continue
            px += self._char_px(text[pos], cur)
            if round(px) > budget_px:
                return pos
            pos += 1
        return None

    # ---- internals -------------------------------------------------------

    def _avg_px(self, font: str) -> float:
        tbl = self.widths.get(_norm_font(font))
        if tbl:
            # Average over the printable-letter range rather than the whole
            # table, which is padded with wide placeholder entries.
            sample = [w for w in tbl if 0 < w <= 10]
            if sample:
                return sum(sample) / len(sample)
        return _FALLBACK_PX.get(_norm_font(font), _FALLBACK_DEFAULT)

    def _tokens(self, text: str):
        """Yield ``(kind, payload, start, end)``, splitting on break escapes.

        Offsets are carried so `layout` can record where each line begins and
        ends in the source, which is what lets the highlighter be derived from
        the layout instead of re-deriving it.
        """
        buf = []
        buf_at = 0
        i = 0
        n = len(text)
        while i < n:
            two = text[i:i + 2]
            if two in _BREAKS:
                if buf:
                    yield "text", "".join(buf), buf_at, i
                    buf = []
                yield two, two, i, i + 2
                i += 2
                buf_at = i
            else:
                buf.append(text[i])
                i += 1
        if buf:
            yield "text", "".join(buf), buf_at, n

    @staticmethod
    def _ctrl_head(code: str) -> str:
        body = code[1:-1].strip()
        return body.split()[0].upper() if body else ""

    def _ctrl_effect(self, code: str, cur: str) -> tuple:
        """(font after this code, stand-in text it contributes)."""
        head = self._ctrl_head(code)
        if head.startswith("FONT"):
            return _norm_font(head), ""
        return cur, _PLACEHOLDER_STANDIN.get(head, "")

    def _runs(self, text: str, font: str):
        """Yield ``(font, literal, is_placeholder)`` runs."""
        pos = 0
        cur = _norm_font(font)
        for m in _CTRL_RE.finditer(text):
            if m.start() > pos:
                yield cur, text[pos:m.start()], False
            cur, standin = self._ctrl_effect(m.group(0), cur)
            if standin:
                yield cur, standin, True
            pos = m.end()
        if pos < len(text):
            yield cur, text[pos:], False

    def _terminal_font(self, text: str, font: str) -> str:
        """The font in effect AFTER *text*.

        `_runs` only reports a font alongside a literal, so a `{FONT_SMALL}`
        that ends a line yields nothing and the switch would be lost at the line
        boundary. This recovers it.
        """
        cur = _norm_font(font)
        for m in _CTRL_RE.finditer(text):
            cur, _ = self._ctrl_effect(m.group(0), cur)
        return cur

    def _char_px(self, ch: str, font: str) -> float:
        tbl = self.widths.get(_norm_font(font))
        if not tbl or not self.charmap:
            return self._avg_px(font)
        byte = self.charmap.get(ch)
        if byte is None or byte >= len(tbl):
            # A glyph this project's charmap doesn't define. Charge the average
            # rather than zero, so unknown text can't sneak past the budget by
            # being unmeasurable.
            return self._avg_px(font)
        return tbl[byte]

    def _run_px(self, body: str, font: str) -> float:
        """Width of one run in pixels, UNROUNDED.

        Rounding here would round once per run, while `overflow_spans` rounds
        once per line — so a line whose true width lands exactly on the budget
        gets two different verdicts and the counter contradicts the
        highlighting. There is exactly ONE rounding point: the line total.
        This only bites when a glyph is missing from the project's charmap and
        the fractional average stands in for it — which is not exotic, since 13
        typeable ASCII characters are absent from this project's charmap.
        """
        key = (_norm_font(font), body)
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        clean = _ESCAPE_RE.sub("", body).replace("$", "")
        val = sum(self._char_px(c, font) for c in clean)
        if len(self._cache) > 4000:                  # keep it from growing free
            self._cache.clear()
        self._cache[key] = val
        return val


def load_text_metrics(root: str) -> GbaTextMetrics:
    """Build a measurer for the project at *root*. Never raises."""
    try:
        charmap, collisions, collided = _parse_charmap(root)
        widths = _parse_glyph_widths(root)
    except Exception:                                 # pragma: no cover
        _log.exception("Failed to read font tables from %s", root)
        return GbaTextMetrics()

    sane = _charmap_is_sane(charmap, collisions, collided)
    m = GbaTextMetrics(charmap=charmap if sane else {}, widths=widths)
    m.exact = sane and bool(widths)
    if not m.exact:
        _log.warning(
            "Font tables not fully usable in %s (charmap=%d entries, "
            "%d collisions, sane=%s, fonts=%s) - text width feedback will be "
            "approximate.", root, len(charmap), collisions, sane,
            sorted(widths))
    return m
