"""Fame Checker data model — reads the engine's Fame Checker tables into an
editable structure (and, later, regenerates them).

The Fame Checker is a per-person trivia database. PorySuite exposes it as an
editable tab so a project can repurpose it (commonly as a QUEST TRACKER:
person -> quest, flavor-text entry -> objective).

Everything is parsed from the PROJECT's own source — nothing about the person
list, entry count, names or graphics is hardcoded here, so a project that has
already renamed/expanded its Fame Checker round-trips correctly.

Sources read
------------
* ``include/constants/fame_checker.h``   person constants + NUM_FAMECHECKER_PERSONS
* ``src/fame_checker.c``                 the NINE per-person tables + FC_NONTRAINER_START
* ``src/strings.c``                      the four non-trainer name strings
* ``data/text/fame_checker.inc``         name / quote / flavor / origin strings

The NINE tables (see docs/FAME_CHECKER_PLAN.md §1.2):
    1 sTrainerIdxs[N]                       TRAINER_*, or FC_NONTRAINER_START + slot
    2 sFameCheckerTrainerPicIdxs[N]         TRAINER_PIC_* portrait
    3 sFameCheckerTrainerGenders_Unused[N]  dead, but still per-person
    4 sNonTrainerNamePointers[C]            custom names (indexed by the slot above)
    5 sFameCheckerNameAndQuotesPointers[2N] first N = names, next N = quotes
    6 sFameCheckerFlavorTextPointers[N*E]   the flavor-text entries
    7 sFameCheckerArrayNpcGraphicsIds[N*E]  OBJ_EVENT_GFX_* informant icon
    8 sFlavorTextOriginLocationTexts[N*E]   "where you heard it"   (LIVE)
    9 sFlavorTextOriginObjectNameTexts[N*E] "who/what told you"    (LIVE)

Tables 6-9 are parallel and MUST always be regenerated together — the engine
indexes all four with ``person * E + entry``; a short table means an
out-of-bounds pointer read (crash), not a cosmetic gap.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

try:
    from core.gba_text_metrics import load_text_metrics
except Exception:                                    # pragma: no cover
    load_text_metrics = None


# ── source locations (relative to the project root) ─────────────────────────
_CONSTANTS_H = ("include", "constants", "fame_checker.h")
_SOURCE_C = ("src", "fame_checker.c")
_STRINGS_C = ("src", "strings.c")
_TEXT_INC = ("data", "text", "fame_checker.inc")

# THE STABLE CONTRACT — the only things a project may NOT rename:
#   * the nine table identifiers (sTrainerIdxs, sFameCheckerFlavorTextPointers, …)
#   * the FAMECHECKER_* person constants and FC_NONTRAINER_START
# These are pokefirered engine symbols, not project data, and every lookup here
# keys on them. A project that renames a TABLE gets no tab (has_fame_checker
# returns False) — deliberate, but it means the failure is silent, so it is
# documented here and in FAME_CHECKER_PLAN.md Phase 0. Everything else — text
# symbols, person names, portraits, entry counts — may be renamed freely; that
# is the whole point of repurposing this as a quest tracker.
#
# NOTE: symbol ownership is NEVER decided by name prefix. A project is free to
# rename every symbol (renaming them is the whole point of repurposing this as a
# quest tracker), so ownership is derived from what the TABLES actually
# reference — see _owned_text_symbols(). Anything else in the .inc belongs to
# another system and must be preserved verbatim on regeneration.


@dataclass
class FameCheckerProblem:
    """A parse finding, with a severity the UI can act on WITHOUT string-matching.

    info     - harmless observation (e.g. a dead table)
    warn     - one field is untrustworthy; grey it out, keep editing the rest
    blocking - the MODEL may be wrong; the UI must disable Save entirely, because
               writing it back would persist misaligned/invented data.
    """
    severity: str
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass
class FameCheckerTextMetrics:
    """Real on-screen text budgets, measured from the PROJECT's own source.

    A project that widened the Fame Checker windows, or swapped the fonts it
    prints with, must get its own limits — so none of this is hardcoded.
    `derived` is False when we had to fall back to the values observed in
    vanilla, and the caller raises a `warn` for that.

    Widths are in PIXELS, not characters, because the GBA font is proportional.
    `gba` does the actual measuring; see `core/gba_text_metrics`.
    """
    msgbox_px: int = 208        # usable pixel width of the flavor-text window
    msgbox_lines: int = 2       # visible lines PER PAGE (pages split on \p)
    msgbox_font: str = "FONT_NORMAL"
    icondesc_px: int = 84       # centring width used for the two origin fields
    icondesc_font: str = "FONT_SMALL"
    # The person LIST on the left. A completely different budget from the
    # other two — the window is 8 tiles wide and the text starts 8px in, so a
    # name has ~56px, not the msgbox's 208. Reusing the wrong one would let a
    # name be typed at four times the width it can actually draw.
    list_px: int = 56
    list_font: str = "FONT_NORMAL"
    derived: bool = False
    # False when the printer call's y / lineSpacing could not be read, so
    # `msgbox_lines` is a guess rather than a measurement.
    geometry_read: bool = False

    # The pixel measurer for this project. None only if the module failed to
    # import; `exact` reports whether it found real font tables to work from.
    gba: object = None

    @property
    def exact(self) -> bool:
        return bool(getattr(self.gba, "exact", False))

    # NOTE: no `layout` / `width_px` wrappers here on purpose. They existed,
    # had no callers, and silently dropped `expand_placeholders` — so anyone who
    # later reached for the wrapper would have got placeholder expansion back on
    # the origin-location field, which the engine does not do. Callers use
    # `metrics.gba` directly and pass every flag explicitly.


_FONTINFO_RE = re.compile(
    r"struct\s+FontInfo\s+\w*[Ff]ontInfos\s*\[\s*\]\s*=\s*\{(.*?)\n\}\s*;",
    re.DOTALL)
_FONTENTRY_RE = re.compile(r"\[\s*(FONT_\w+)\s*\]\s*=\s*\{(.*?)\}", re.DOTALL)


def _font_line_height(font: str, project_dir: str) -> tuple:
    """(maxLetterHeight, lineSpacing) for *font*, read from `gFontInfos[]`."""
    if not project_dir:
        return 0, 0
    for rel in (("src", "new_menu_helpers.c"), ("src", "menu.c"),
                ("src", "text.c")):
        try:
            with open(os.path.join(project_dir, *rel), "r",
                      encoding="utf-8", errors="replace") as fh:
                body = fh.read()
        except OSError:
            continue
        tbl = _FONTINFO_RE.search(body)
        if not tbl:
            continue
        for e in _FONTENTRY_RE.finditer(tbl.group(1)):
            if e.group(1) != font:
                continue
            h = re.search(r"\.maxLetterHeight\s*=\s*(\d+)", e.group(2))
            s = re.search(r"\.lineSpacing\s*=\s*(\d+)", e.group(2))
            if h:
                return int(h.group(1)), int(s.group(1)) if s else 0
    return 0, 0


def _split_args(s: str) -> list:
    """Top-level comma split of a C argument list."""
    out, depth, cur = [], 0, []
    for ch in s:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if cur:
        out.append("".join(cur).strip())
    return out


def _call_args(src: str, after_name: int) -> list:
    """Arguments of the call whose name ends at *after_name*, in order.

    Paren-balanced, so a nested call in an argument doesn't truncate the list —
    and it starts at the FIRST argument, so positions line up with the callee's
    parameter list.
    """
    open_p = src.find("(", after_name)
    if open_p < 0:
        return []
    depth, i = 1, open_p + 1
    while i < len(src) and depth:
        if src[i] == "(":
            depth += 1
        elif src[i] == ")":
            depth -= 1
        i += 1
    return _split_args(src[open_p + 1:i - 1])


def _enclosing_body(src: str, at: int) -> str:
    """Brace-matched body of the function containing offset *at*.

    Must VERIFY the result actually contains *at*. Taking the first `{` after
    the previous function grabs an array initializer when one sits between the
    two functions — `static const u8 sPad[] = { 1, 2, 3 };` matched as the body,
    which then contains none of the constants we are looking for and reports a
    mismatch on a perfectly healthy project.
    """
    start = src.rfind("\n}", 0, at)
    start = 0 if start < 0 else start + 2
    while True:
        brace = src.find("{", start)
        if brace < 0 or brace > at:
            return ""
        depth, i = 1, brace + 1
        while i < len(src) and depth:
            if src[i] == "{":
                depth += 1
            elif src[i] == "}":
                depth -= 1
            i += 1
        if brace <= at < i:
            return src[brace:i]
        start = i                        # that block ended before `at` — next


def parse_custom_pic_persons(src: str, const_names) -> tuple:
    """Which persons the engine draws with CUSTOM art — and whether it agrees.

    Vanilla's portrait code makes the custom-vs-trainer decision **twice**, as a
    hardcoded `if / else if` chain over four person constants:

    * `CreatePersonPicSprite` picks the sprite and palette.
    * `DestroyPersonPicSprite` repeats the same chain to decide between
      `DestroySprite` and `FreeAndDestroyTrainerPicSprite` — but against an
      index it derives itself, with a `- 1` fudge for the last row.

    If the two chains list different people, the project frees a trainer pic as
    if it were a raw sprite (leaking its tiles and palette for the session) or
    frees a raw sprite as if it owned tiles the shared sheet still owns. That is
    a live bug in the project and the editor would be modelling a game that does
    not exist — hence `blocking`, on the same rule as the stride mismatch.

    Anchored on the CALLS, not on function names, so a fork that renamed the
    functions still parses.

    Returns `(create_set, destroy_set, missing)` — `missing` names any marker
    that could not be found at all. That is a DIFFERENT problem from a mismatch
    ("we could not locate the free path" vs "the two paths disagree") and must
    not be reported as one, or a project that frees everything the same way gets
    told four persons are "on one side only".
    """
    known = set(const_names or ())
    # Comments and string literals must go first: `src.find` would otherwise
    # anchor on the first TEXTUAL occurrence of the call, which a comment
    # describing the function satisfies.
    clean = _blank_c_comments_and_strings(src)
    out, missing = [], []
    for label, anchor, raw_call in (
            ("create", "CreateTrainerPicSprite(", "CreateSprite("),
            ("free", "FreeAndDestroyTrainerPicSprite(", "DestroySprite(")):
        at = clean.find(anchor)
        if at < 0:
            missing.append(label)
            out.append(set())
            continue
        out.append(_custom_branch_consts(
            _enclosing_body(clean, at), known, raw_call))
    return out[0], out[1], missing


def dispatch_branches(body: str, known: set):
    """Yield ``(person constants, the code that branch runs)`` for one function.

    **The single place this file understands a person dispatch.** Two callers
    ask different questions of the same structure — "which people take the raw
    sprite path" and "which sprite template does each person create" — and
    writing a second scanner for the second question is how they end up
    disagreeing. They did: a switch/case project reported four custom-art people
    and zero portraits, so the tab drew four blank cells and claimed 0 KB of
    artwork. One walk, one answer.

    Handles both shapes vanilla and a patcher produce: an `if / else if` chain
    with one constant per branch, and a `switch` whose consecutive `case`
    labels share a body. Conditions are paren-balanced and statements are
    brace-matched, because the naive versions of both are wrong on real code.
    """
    for m in re.finditer(r"\bif\s*\(", body):
        depth, i = 1, m.end()
        while i < len(body) and depth:
            if body[i] == "(":
                depth += 1
            elif body[i] == ")":
                depth -= 1
            i += 1
        cond = body[m.end():i - 1]
        consts = {c for c in re.findall(r"[A-Za-z_]\w*", cond) if c in known}
        if not consts:
            continue
        rest = body[i:]
        stripped = rest.lstrip()
        if stripped.startswith("{"):
            off = i + (len(rest) - len(stripped))
            depth, j = 1, off + 1
            while j < len(body) and depth:
                if body[j] == "{":
                    depth += 1
                elif body[j] == "}":
                    depth -= 1
                j += 1
            stmt = body[off:j]
        else:
            stmt = stripped.split(";", 1)[0]
        yield consts, stmt

    labels = list(re.finditer(r"\bcase\s+([A-Za-z_]\w*)\s*:", body))
    for n, lab in enumerate(labels):
        if lab.group(1) not in known:
            continue
        run = {lab.group(1)}
        k = n + 1
        while k < len(labels) and not body[labels[k - 1].end():
                                          labels[k].start()].strip():
            if labels[k].group(1) in known:
                run.add(labels[k].group(1))
            k += 1
        # The terminator scan must be DEPTH-AWARE. A `}` at any depth ends the
        # case body only if it is the switch's own; a nested block or a for-loop
        # inside the case would otherwise truncate the statement before the call
        # we are looking for, silently dropping that person.
        rest = body[labels[k - 1].end():]
        depth, pos = 0, 0
        end_rel = len(rest)
        while pos < len(rest):
            ch = rest[pos]
            if ch == "{":
                depth += 1
            elif ch == "}":
                if depth == 0:
                    end_rel = pos
                    break
                depth -= 1
            elif depth == 0:
                m2 = re.match(r"\b(?:case|default|break)\b", rest[pos:])
                if m2:
                    end_rel = pos
                    break
            pos += 1
        yield run, rest[:end_rel]


def _custom_branch_consts(body: str, known: set, raw_call: str) -> set:
    """Person constants whose branch creates or destroys a RAW sprite.

    "Constants in the function" and even "constants in a comparison" are both
    too loose — an unrelated `if (idx == FAMECHECKER_GIOVANNI) PlaySE(...)`
    would make Giovanni look like a custom-art person. The constant has to lead
    to code that handles a raw sprite.
    """
    found = set()
    for consts, stmt in dispatch_branches(body, known):
        if raw_call in stmt:
            found |= consts
    return found


@dataclass
class FameCheckerGraphics:
    """Everything the portrait panel needs, parsed from the project.

    Read-only intelligence. Nothing here writes, and every field is derived
    from the project's own source rather than assumed, so a project that
    renamed its symbols, moved its art or added a fifth custom portrait is
    described accurately instead of being measured against vanilla.
    """
    # person const -> {"gfx", "tag", "png", "gbapal", "tile_bytes", "template"}
    custom: dict = field(default_factory=dict)
    # every tag in the sheet table -> byte size, for the VRAM budget
    sheet_bytes: dict = field(default_factory=dict)
    # tags freed ANYWHERE in the file
    freed_tags: set = field(default_factory=set)
    silhouette_gbapal: str = ""
    # person const -> FCPICKSTATE_* the engine writes for a NEW GAME
    pickstate_defaults: dict = field(default_factory=dict)
    # OBJ tile VRAM available in this screen's video mode. Derived, because a
    # project using a bitmap mode has HALF of it.
    obj_vram_bytes: int = 32 * 1024
    # Tags whose #define carries a warning comment in the project's own source.
    shared_tag_notes: list = field(default_factory=list)

    @property
    def static_sheet_bytes(self) -> int:
        """Everything the sheet table commits the moment the screen opens.

        NOT a headroom figure. This is the STATIC table only; the trainer
        portraits, the informant icons and the scroll arrows all allocate at
        runtime on top of it, so presenting the remainder as "space left"
        would overstate what is free.
        """
        return sum(self.sheet_bytes.values())

    @property
    def custom_tile_bytes(self) -> int:
        """VRAM cost of the custom portraits, which load whether used or not.

        The ceiling on adding portraits is TILES, not palette slots: every
        portrait — custom and trainer alike — is blitted into one hardcoded OBJ
        palette slot, so an extra one costs no palette slots at all.
        """
        return sum(v.get("tile_bytes", 0) for v in self.custom.values())

    @property
    def leaked_tags(self) -> list:
        """Portrait tags nothing frees — a live leak in the user's project."""
        return sorted(v["tag"] for v in self.custom.values()
                      if v.get("tag") and v["tag"] not in self.freed_tags)


_INCBIN_RE = re.compile(
    r"\b(\w+)\s*\[\s*\]\s*=\s*INCBIN_U(?:8|16|32)\s*\(\s*\"([^\"]+)\"\s*\)")
_SHEET_RE = re.compile(
    r"\{\s*(\w+)\s*,\s*(0[xX][0-9A-Fa-f]+|\d+)\s*,\s*(\w+)\s*\}")
_TEMPLATE_RE = re.compile(
    r"struct\s+SpriteTemplate\s+(\w+)\s*=\s*\{\s*([A-Za-z_]\w*)")


def _sheet_table_body(clean: str) -> str:
    """Body of the sheet table(s) this screen actually LOADS.

    Two levels of anchoring, each closing a real hole:

    * not a bare `{symbol, number, symbol}` scan — other tables share that
      shape, and an unrelated three-field table inflated the figure 2.6x;
    * not merely "any `struct SpriteSheet[]`" either — a project that keeps a
      second, unloaded sheet table would have it summed into a VRAM figure it
      never costs. And that is exactly the project this figure exists for: the
      one adding portraits is the one likely to add a table.

    So the anchor is `LoadSpriteSheets(<name>)`. Plural is fine: if a project
    loads two tables, both genuinely commit.
    """
    names = re.findall(r"LoadSpriteSheets?\s*\(\s*&?\s*(\w+)", clean)
    bodies = [_array_body(clean, n) for n in dict.fromkeys(names)]
    bodies = [b for b in bodies if b]
    if bodies:
        return "\n".join(bodies)
    # Nothing named a load call — fall back to the declared table so a project
    # that loads through a wrapper still gets a figure rather than zero.
    m = re.search(r"struct\s+SpriteSheet\s+(\w+)\s*\[", clean)
    return _array_body(clean, m.group(1)) if m else ""


def _new_game_pickstate_body(clean: str, project_dir: str) -> str:
    """Body of the function that sets pickState FOR A NEW GAME, or ''.

    Taking the first textual `pickState =` is wrong and confidently so: with
    the reset function absent or reordered it lands on the debug
    unlock-everything function and reports that every person starts fully
    visible. Both candidates are non-static and both loop over the person
    count, so neither export nor shape tells them apart — only the CALLER does.

    Two or more candidates and no caller to choose between them means the
    honest answer is "unknown", not a guess.
    """
    cands = {}
    for m in re.finditer(r"\bpickState\s*=", clean):
        body = _enclosing_body(clean, m.start())
        if not body:
            continue
        name = re.search(r"(\w+)\s*\([^)]*\)\s*$",
                         clean[:clean.index(body)].rstrip())
        cands[name.group(1) if name else str(len(cands))] = body
    if len(cands) <= 1:
        return next(iter(cands.values()), "")

    # Ambiguous: let the new-game path decide which one is the default.
    for rel in (("src", "new_game.c"), ("src", "main_menu.c")):
        try:
            with open(os.path.join(project_dir, *rel), "r",
                      encoding="utf-8", errors="replace") as fh:
                caller = fh.read()
        except OSError:
            continue
        for name, body in cands.items():
            if re.search(r"\b" + re.escape(name) + r"\s*\(", caller):
                return body
    return ""


def parse_fame_checker_graphics(src: str, project_dir: str,
                                custom_persons=None,
                                all_persons=None) -> FameCheckerGraphics:
    """Resolve each custom-art person to its PNG, palette and tile budget.

    The chain, every link parsed rather than assumed:
    person const -> the `CreateSprite(&<template>` in its branch -> the
    template's tile tag -> the sheet entry with that tag -> the graphics symbol
    -> the `INCBIN` path -> the `.png` beside it.

    The `.4bpp` in the INCBIN is a BUILD ARTIFACT; the editable source is the
    `.png` next to it, which is why no 4bpp decoder is needed anywhere here.
    """
    g = FameCheckerGraphics()
    clean = _blank_c_comments_and_strings(src)
    incbin = {m.group(1): m.group(2) for m in _INCBIN_RE.finditer(src)}
    templates = {m.group(1): m.group(2) for m in _TEMPLATE_RE.finditer(clean)}

    tag_to_sheet = {}
    for m in _SHEET_RE.finditer(_sheet_table_body(clean)):
        sym, size, tag = m.group(1), m.group(2), m.group(3)
        if sym in incbin:
            try:
                tag_to_sheet[tag] = (sym, int(size, 0))
            except ValueError:
                continue
    g.sheet_bytes = {t: n for t, (_s, n) in tag_to_sheet.items()}

    # Which template each custom person creates — via the SAME branch walk
    # `parse_custom_pic_persons` uses. A second scanner here disagreed with it
    # on switch/case, on a nested call in the condition, and on a CreateSprite
    # that wasn't the first statement: the tab said four people use custom art
    # and then drew four blank cells claiming 0 KB of artwork.
    at = clean.find("CreateTrainerPicSprite(")
    body = _enclosing_body(clean, at) if at >= 0 else ""
    for consts, stmt in dispatch_branches(body, set(custom_persons or ())):
        tm = re.search(r"CreateSprite\s*\(\s*&\s*(\w+)", stmt)
        if not tm:
            continue
        tag = templates.get(tm.group(1), "")
        sym, size = tag_to_sheet.get(tag, ("", 0))
        rel = incbin.get(sym, "")
        png = gbapal = ""
        if rel:
            base = os.path.splitext(
                os.path.join(project_dir, *rel.split("/")))[0]
            png, gbapal = base + ".png", base + ".gbapal"
        for c in consts:
            g.custom[c] = {"gfx": sym, "tag": tag, "png": png,
                           "gbapal": gbapal, "tile_bytes": size,
                           "template": tm.group(1)}

    # Which tags get freed ANYWHERE. Scanning only the first function that
    # calls FreeSpriteTilesByTag finds whichever comes first — in vanilla the
    # cursor's — so all four portraits read as leaks on a healthy project.
    g.freed_tags = set(re.findall(
        r"FreeSpriteTilesByTag\s*\(\s*(\w+)\s*\)", clean))

    # The silhouette palette, found through the CODE rather than by looking for
    # the word "silhouette" in a filename — rename the asset and a filename
    # match silently drops the row. It is the palette loaded in the branch that
    # tests for the silhouette pick-state.
    # Every mention, not the first: vanilla names the state three times and
    # only the third is the one that loads the palette. Taking the first match
    # found nothing at all.
    for sil in re.finditer(r"FCPICKSTATE_SILHOUETTE\b", clean):
        lp = re.search(r"LoadPalette\s*\(\s*(\w+)",
                       clean[sil.end():sil.end() + 400])
        rel = incbin.get(lp.group(1), "") if lp else ""
        if rel:
            g.silhouette_gbapal = os.path.join(project_dir, *rel.split("/"))
            break

    # OBJ tile VRAM depends on the video mode this screen sets. Modes 3-5 are
    # bitmap modes and halve it. Take the LAST write that names a mode: the
    # first DISPCNT write in vanilla is a plain 0, so first-match works only by
    # luck of ordering.
    for m in re.finditer(r"REG_OFFSET_DISPCNT\s*,([^;]*)", clean):
        if re.search(r"DISPCNT_MODE_\d", m.group(1)):
            g.obj_vram_bytes = (16 if re.search(r"DISPCNT_MODE_[345]\b",
                                                m.group(1)) else 32) * 1024

    # A project's own warning comments about a tag being shared.
    for m in re.finditer(r"#define\s+(SPRITETAG_\w+)\s+\d+\s*//\s*(.+)", src):
        g.shared_tag_notes.append((m.group(1), m.group(2).strip()))

    # New-game pickState. SAVE data — the editor can only ever see the default
    # a NEW GAME is given, never what a player is looking at.
    persons = list(all_persons or custom_persons or ())
    reset = _new_game_pickstate_body(clean, project_dir)
    if reset:
        base = re.search(r"pickState\s*=\s*(FCPICKSTATE_\w+)", reset)
        if base:
            # The engine's loop applies this to EVERY person, so the model must
            # too — seeding only the custom-art four left twelve people with a
            # blank field the engine definitely gives a value.
            for c in persons:
                g.pickstate_defaults[c] = base.group(1)
        for m in re.finditer(
                r"fameChecker\s*\[\s*(\w+)\s*\]\s*\.pickState\s*=\s*"
                r"(FCPICKSTATE_\w+)", reset):
            # Reject anything that isn't a real person constant: the loop
            # itself matches as `fameChecker[i]`, which would otherwise invent
            # a person called "i".
            if m.group(1) in persons:
                g.pickstate_defaults[m.group(1)] = m.group(2)
    return g


def _param_name(decl: str) -> str:
    """Identifier of one C parameter declaration, for positional resolution.

    Returns the last identifier BEFORE any parenthesis. For a plain parameter
    that is its name (`const u8 *str` -> `str`, `u8 buf[16]` -> `buf`). For a
    function-pointer parameter (`void (*callback)(struct TextPrinterTemplate *,
    u16)`) it yields the RETURN TYPE, `void`, not `callback` — the point is only
    that it must not reach into the pointer's own argument list and return
    `u16`, which could alias a real parameter name elsewhere in the list. A type
    name in the table is harmless: nothing a `printer.y = …` assignment can name
    ever resolves to one.
    """
    head = decl.split("(", 1)[0] if "(" in decl else decl
    ids = re.findall(r"[A-Za-z_]\w*", head)
    return ids[-1].lstrip("*") if ids else ""


def _printer_geometry(project_dir: str, helper: str, call_args: list) -> tuple:
    """(y, lineSpacing) actually used by *helper* for this call.

    **These do NOT come from `gFontInfos[]`.** The renderer steps by
    `printerTemplate.lineSpacing` (`text.c`), which is whatever the printer
    helper puts there — `AddTextPrinterParameterized2` hardcodes `y = 1,
    lineSpacing = 1`, while the `...Parameterized4` calls for the icon
    description pass `y = 0/10, lineSpacing = 2`. Reading `gFontInfos` instead
    under-counts the line step and tells the user they have a line the engine
    will clip.

    So: read the helper's own definition. If it assigns literals, use them; if
    it assigns from its parameters, resolve those against this call site.
    """
    if not project_dir or not helper:
        return None, None
    body = ""
    params = []
    for rel in (("src", "new_menu_helpers.c"), ("src", "menu2.c"),
                ("src", "menu.c"), ("src", "text.c")):
        try:
            with open(os.path.join(project_dir, *rel), "r",
                      encoding="utf-8", errors="replace") as fh:
                txt = fh.read()
        except OSError:
            continue
        for hit in re.finditer(r"\b" + re.escape(helper) + r"\s*\(", txt):
            # A `[^)]*` param scan does NOT work here: these helpers take a
            # function-pointer argument, `void (*callback)(...)`, whose own
            # parens end the scan early and make the definition unfindable.
            args = _call_args(txt, hit.start() + len(helper))
            close = txt.find("(", hit.start()) + 1
            depth = 1
            while close < len(txt) and depth:
                if txt[close] == "(":
                    depth += 1
                elif txt[close] == ")":
                    depth -= 1
                close += 1
            tail = txt[close:close + 4].lstrip()
            if not tail.startswith("{"):
                continue                      # a prototype, not the definition
            # Brace-MATCH the body. A fixed-size window spills into whatever
            # function comes next — `AddTextPrinterParameterized2` is ~1600
            # chars, so a 3000-char window reads ~1400 chars of its neighbour
            # and would happily take that function's `printer.y` as this one's.
            brace = txt.index("{", close)
            depth, k = 1, brace + 1
            while k < len(txt) and depth:
                if txt[k] == "{":
                    depth += 1
                elif txt[k] == "}":
                    depth -= 1
                k += 1
            body = txt[brace:k]
            params = [_param_name(a) for a in args if a.strip()]
            break
        if body:
            break
    if not body:
        return None, None

    def resolve(field):
        # `\w+\.` rather than `printer\.` — the same struct is spelled
        # `printerTemplate` in other decomps. First assignment wins, which is
        # right for a conditional override and the common case besides.
        m = re.search(r"\w+\.%s\s*=\s*([A-Za-z_0-9]+)\s*;" % field, body)
        if not m:
            return None
        val = m.group(1)
        if val.isdigit():
            return int(val)
        if val in params:
            idx = params.index(val)
            if idx < len(call_args) and call_args[idx].strip().isdigit():
                return int(call_args[idx].strip())
        return None

    return resolve("y"), resolve("lineSpacing")


def _lines_that_fit(window_px_h: int, font: str, project_dir: str,
                    y: int = None, line_spacing: int = None) -> int:
    """How many text lines fit in a window `window_px_h` pixels tall.

    Mirrors the renderer: the first line is drawn at `y` and each `\\n` steps
    down by `maxLetterHeight + lineSpacing`. A line that would start below the
    floor is drawn clipped, so it does not count.

    `maxLetterHeight` comes from `gFontInfos[]`; `y` and `lineSpacing` come from
    the PRINTER CALL (see `_printer_geometry`) — mixing those up over-reports
    the budget, which is the dangerous direction.

    Falls back to a flat 16 px per line only when nothing can be read, so a
    project with a taller font still gets a budget it can actually render.
    """
    h, _ = _font_line_height(font, project_dir)
    if h <= 0:
        return max(1, window_px_h // 16)
    step = h + (line_spacing if line_spacing is not None else 0)
    top = y if y is not None else 0
    usable = window_px_h - top - h
    if usable < 0 or step <= 0:
        return 1
    return max(1, usable // step + 1)


_MUL_RE = re.compile(r"\*\s*(\d+)\b|\b(\d+)\s*\*")


_DECL_KEYWORDS = {
    "extern", "static", "const", "volatile", "register", "typedef",
    "struct", "union", "enum", "void", "char", "int", "short", "long",
    "signed", "unsigned", "float", "double",
    "u8", "s8", "u16", "s16", "u32", "s32", "u64", "s64", "bool8", "bool32",
}


def _is_declaration(src: str, name_at: int) -> bool:
    """Is the identifier at *name_at* being DECLARED rather than indexed?

    Scans back to the start of the statement (the previous `;`, `{` or `}`).
    A declaration has a storage class or type keyword in that span and no `=`
    before the identifier; an index read has neither, or has the `=` of an
    assignment. This is the only discriminator that works — see the caller.
    """
    start = max(src.rfind(";", 0, name_at), src.rfind("{", 0, name_at),
                src.rfind("}", 0, name_at))
    head = src[start + 1:name_at]
    if "=" in head:
        return False                     # `x = sFameCheckerFoo[...]` — a read
    words = re.findall(r"[A-Za-z_]\w*", head)
    if not (_DECL_KEYWORDS & set(words)):
        return False
    # `Foo((u8)sFameCheckerT[p * 7 + i])` has a type keyword in the span but is
    # a CAST inside a call, not a declaration. The tell is a CLOSING paren
    # between the last type keyword and the identifier — `(u8)name` — where a
    # declaration has only whitespace, `*` or `const` (`const u8 *const name`).
    last = max(head.rfind(w) for w in words if w in _DECL_KEYWORDS)
    return ")" not in head[last:]


def _blank_c_comments_and_strings(src: str) -> str:
    """Replace comment and string-literal bodies with spaces, keeping offsets.

    Offsets are preserved so anything else that scans the same text still lines
    up. Used before code-shape detection, so a comment describing the old code
    can't be mistaken for the code.
    """
    def blank(m):
        return " " * len(m.group(0))
    # ORDER MATTERS. Blanking `//` first makes a URL inside a string literal
    # ("http://x") swallow the rest of that line — including real code sharing
    # it, which silently hides a stride from the mismatch check. Strings and
    # character literals go first, then block comments, then line comments.
    src = re.sub(r'"(?:\\.|[^"\\\n])*"', blank, src)
    src = re.sub(r"'(?:\\.|[^'\\\n])*'", blank, src)
    src = re.sub(r"/\*.*?\*/", blank, src, flags=re.DOTALL)
    src = re.sub(r"//[^\n]*", blank, src)
    return src


def _hardcoded_strides(src: str) -> set:
    """Entries-per-person literals baked into the engine's indexing maths.

    The tables are sized data, but the code that reads them multiplies by the
    stride as a LITERAL — `sFameCheckerFlavorTextPointers[person * 6 + index]`,
    `6 * unlockedPersons[...] + whichText`, `u8 spriteIds[6]`. Resizing the
    tables without patching those leaves the engine reading the wrong slots.

    A plain regex can't do this: the index expression contains its own brackets
    (`unlockedPersons[cursorPos]`), so a `[^\\]]*` scan stops in the wrong place
    and silently finds nothing. This walks balanced brackets instead.
    """
    # A false positive here is worse than a false negative: `blocking` disables
    # Save, locks every field AND hides the entries. So each probe below is
    # deliberately narrow, and comments and string literals are removed first —
    # someone documenting the OLD shape while doing exactly the refactor this
    # check protects would otherwise be locked out by their own comment.
    src = _blank_c_comments_and_strings(src)

    out = set()
    for m in re.finditer(r"\bsFameChecker\w*\s*\[", src):
        depth, i = 1, m.end()
        while i < len(src) and depth:
            if src[i] == "[":
                depth += 1
            elif src[i] == "]":
                depth -= 1
            i += 1
        # A DECLARED SIZE is not a stride: `name[2 * NUM_FAMECHECKER_PERSONS]`
        # means two pointers per person, and counting it flags healthy vanilla
        # as broken.
        #
        # The test has to look LEFT, not right. Nothing after the `]`
        # distinguishes the cases: `= {` is a definition, `= 0` is an lvalue
        # index, and `;` is BOTH an extern declaration and an index read ending
        # a statement. Looking left is decisive — a declaration is preceded, in
        # the same statement, by a storage class or type keyword and no `=`.
        # That also handles the shapes a right-hand rule cannot:
        # `[NUM * 5 + 4];`, tentative definitions, 2-D arrays, function-pointer
        # arrays and multi-declarator lines.
        if _is_declaration(src, m.start()):
            continue
        inner = src[m.end():i - 1]
        # Real index arithmetic is `person * STRIDE + entry`.
        if "+" not in inner:
            continue
        for a, b in _MUL_RE.findall(inner):
            out.add(int(a or b))

    # `u32 idx = 6 * sFameCheckerData->unlockedPersons[...] + whichText;` scales
    # the person index OUTSIDE any table bracket, so the walk above misses it.
    # Requiring a `[` after the person term is what keeps the table's declared
    # size (`2 * NUM_FAMECHECKER_PERSONS]`, a different 2) from matching.
    for n in re.findall(
            r"\b(\d+)\s*\*\s*[A-Za-z_][\w.>-]*[Pp]ersons?\s*\[", src):
        out.add(int(n))

    # The per-person sprite array is sized by the same stride. Only the
    # DECLARATION counts — `spriteIds[0]` as an index is not a stride.
    for n in re.findall(
            r"\b(?:u8|s8|u16|s16|u32|s32|int|char)\s+spriteIds\s*\[\s*(\d+)\s*\]",
            src):
        out.add(int(n))
    return out


def parse_text_metrics(src: str, project_dir: str = "") -> FameCheckerTextMetrics:
    """Derive the text budgets from `src/fame_checker.c`.

    IMPORTANT: the origin-field width comes from the literal in
    `UpdateIconDescriptionBox` (`(0x54 - GetStringWidth(...)) / 2`), NOT from
    `FCWINDOWID_ICONDESC`'s template width. The template is 11 tiles = 88 px but
    the code centres within 84 — deriving from the template would silently allow
    4 px of overflow.

    The fonts are read from the project's own `AddTextPrinter*` calls rather
    than assumed, because a project is free to print the flavor text in any font
    and the fonts differ in width.
    """
    m = FameCheckerTextMetrics()
    got_box = got_icon = False

    geom = {}
    for attr, window in (("msgbox_font", "FCWINDOWID_MSGBOX"),
                         ("icondesc_font", "FCWINDOWID_ICONDESC")):
        fm = re.search(
            r"(AddTextPrinter\w*)\s*\(\s*" + window + r"\s*,\s*(FONT_\w+)", src)
        if fm:
            setattr(m, attr, fm.group(2))
            geom[window] = _printer_geometry(
                project_dir, fm.group(1), _call_args(src, fm.end(1)))

    # The person list's own budget: window width minus where the text starts.
    # Every number parsed — a project that widened its list, moved the text in,
    # or printed it in another font gets its own figure.
    lst = re.search(r"\[\s*FCWINDOWID_LIST\s*\]\s*=\s*\{(.*?)\}", src, re.DOTALL)
    if lst:
        w = re.search(r"\.width\s*=\s*(\d+)", lst.group(1))
        if w:
            item_x = re.search(r"\.item_X\s*=\s*(\d+)", src)
            m.list_px = max(8, int(w.group(1)) * 8
                            - (int(item_x.group(1)) if item_x else 0))
    lf = re.search(r"\.fontId\s*=\s*(FONT_\w+)", src)
    if lf:
        m.list_font = lf.group(1)

    blk = re.search(
        r"\[\s*FCWINDOWID_MSGBOX\s*\]\s*=\s*\{(.*?)\}", src, re.DOTALL)
    if blk:
        w = re.search(r"\.width\s*=\s*(\d+)", blk.group(1))
        h = re.search(r"\.height\s*=\s*(\d+)", blk.group(1))
        if w and h:
            y, sp = geom.get("FCWINDOWID_MSGBOX", (None, None))
            m.msgbox_px = int(w.group(1)) * 8
            m.msgbox_lines = _lines_that_fit(
                int(h.group(1)) * 8, m.msgbox_font, project_dir, y, sp)
            # A geometry we couldn't read must NOT quietly become y=0,
            # spacing=0 — both of those mean MORE lines fit, which is the
            # direction that tells the user a line is fine when the engine will
            # clip it. Same shape as the silent-blank-counter bug: an unreadable
            # value has to be loud.
            m.geometry_read = y is not None and sp is not None
            got_box = True

    # Anchor to the code that actually prints INTO the icon-description window:
    # the centring maths sits immediately above each such call. An unanchored
    # search takes the first `(N - GetStringWidth` anywhere in the file, and
    # `PrintUIHelp`'s `188 - width` sits earlier — it only misses today because
    # vanilla splits that across two statements. Anchoring on the function NAME
    # is no good either: the forward declaration at the top of the file matches
    # before the definition does.
    icon_re = re.compile(r"\(\s*(0[xX][0-9A-Fa-f]+|\d+)\s*-\s*GetStringWidth")
    for call in re.finditer(
            r"AddTextPrinter\w*\s*\(\s*FCWINDOWID_ICONDESC\b", src):
        hit = icon_re.search(src[max(0, call.start() - 600):call.start()])
        if hit:
            val = _parse_int_literal(hit.group(1))
            if val:
                m.icondesc_px = val
                got_icon = True
                break

    if project_dir and load_text_metrics is not None:
        m.gba = load_text_metrics(project_dir)

    m.derived = got_box and got_icon
    return m


@dataclass
class FameCheckerEntry:
    """One flavor-text entry (a quest OBJECTIVE when repurposed)."""
    index: int
    text_symbol: str = ""       # gFameCheckerFlavorText_ProfOak0
    text: str = ""              # decoded string (control codes intact)
    npc_gfx: str = ""           # OBJ_EVENT_GFX_* informant icon
    origin_location_symbol: str = ""
    origin_location: str = ""   # "where you heard it"  (table 8)
    origin_object_symbol: str = ""
    origin_object: str = ""     # "who/what told you"   (table 9)


@dataclass
class FameCheckerPerson:
    """One Fame Checker person (a QUEST when repurposed)."""
    index: int
    const: str = ""             # FAMECHECKER_OAK
    trainer_idx: str = ""       # TRAINER_* or FAME_CHECKER_* pseudo-const
    trainer_pic: str = ""       # TRAINER_PIC_*
    gender_unused: str = ""     # from the dead genders table (kept for regen)

    # True when the engine draws this person with its own custom art instead of
    # `trainer_pic`. For those persons `sFameCheckerTrainerPicIdxs` still holds
    # a TRAINER_PIC_* value that NOTHING READS — showing it as "the portrait"
    # tells the user something the game does not do. Same trap as the trainer
    # name in table 5, which is only the pick-mode quote header.
    uses_custom_pic: bool = False

    # How this person's name reaches the in-game LIST:
    #   "trainer" -> gTrainers[trainer_idx].trainerName  (read-only here)
    #   "custom"  -> sNonTrainerNamePointers[custom_name_slot]
    # Table 5's name is ONLY the pick-mode quote header, never the list label.
    name_source: str = "trainer"
    custom_name_slot: int = -1
    custom_name_symbol: str = ""
    custom_name: str = ""

    name_symbol: str = ""       # gFameCheckerPersonName_* (quote header only)
    name: str = ""
    quote_symbol: str = ""      # gFameCheckerPersonQuote_*
    quote: str = ""
    entries: list = field(default_factory=list)   # list[FameCheckerEntry]

    @property
    def list_name(self) -> str:
        """The label the in-game list actually shows (best effort)."""
        if self.name_source == "custom":
            return self.custom_name
        return ""   # comes from gTrainers[] — resolved by the UI/Trainers tab


@dataclass
class FameCheckerData:
    persons: list = field(default_factory=list)   # list[FameCheckerPerson]
    # Both of these are DERIVED from the project — never assume a value.
    entries_per_person: int = 0
    non_trainer_start: "int | None" = None   # read from project; never assumed
    # Custom (non-trainer) name symbols, in engine array order.
    non_trainer_name_symbols: list = field(default_factory=list)
    # symbol -> decoded string, for every string we can see
    text_by_symbol: dict = field(default_factory=dict)
    # Symbols a table references but whose string we could not find. The UI must
    # render these read-only — writing them would delete the real text.
    unresolved_text_symbols: list = field(default_factory=list)
    # Real on-screen text budgets, measured from this project's own source.
    metrics: "FameCheckerTextMetrics" = field(default_factory=lambda: FameCheckerTextMetrics())
    # Labels present in fame_checker.inc that are NOT ours — regeneration must
    # preserve these verbatim or another system loses its text.
    foreign_text_symbols: list = field(default_factory=list)
    # Labels the TABLES actually reference: the only strings this tab may write.
    # Derived from table contents, never from a name prefix, because renaming
    # every symbol is exactly what repurposing this as a quest tracker involves.
    owned_text_symbols: list = field(default_factory=list)
    # False when the project has no (usable) Fame Checker — the tab must then
    # stay hidden and the project must never be modified.
    available: bool = False
    unavailable_reason: str = ""
    # Non-empty when the PORTRAIT feature specifically is unsafe or unreadable.
    # Scoped, not global: it locks the portrait fields and leaves the rest of
    # the tab working, because none of it makes the parsed model wrong.
    portrait_reason: str = ""
    # Portrait / tile-budget intelligence. Only populated when the portrait
    # dispatch was readable, so the panel never renders off a failed parse.
    graphics: "FameCheckerGraphics" = field(
        default_factory=lambda: FameCheckerGraphics())
    problems: list = field(default_factory=list)  # list[FameCheckerProblem]

    def add_problem(self, severity: str, message: str) -> None:
        self.problems.append(FameCheckerProblem(severity, message))

    @property
    def blocking_problems(self) -> list:
        """Problems that mean the model is wrong. UI rule: Save is disabled
        while this is non-empty."""
        return [p for p in self.problems if p.severity == "blocking"]

    @property
    def person_count(self) -> int:
        return len(self.persons)


# ── low-level parsing helpers ───────────────────────────────────────────────

def _read(root: str, parts) -> str:
    """Read a project file.

    Uses ``surrogateescape`` — NEVER ``errors="ignore"``, which silently DELETES
    characters on a non-UTF-8 project (an accented name would vanish on read and
    be lost on the next write).
    """
    path = os.path.join(root, *parts)
    try:
        with open(path, "r", encoding="utf-8", errors="surrogateescape") as f:
            return f.read()
    except OSError:
        return ""


def _array_body(text: str, name: str) -> str:
    """Return the ``{ ... }`` body of the C array *name*, or ''."""
    m = re.search(
        rf"\b{re.escape(name)}\s*\[[^\]]*\]\s*=\s*\{{(.*?)\n\}}\s*;",
        text, re.DOTALL)
    return m.group(1) if m else ""


def _strip_comments(body: str) -> str:
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.DOTALL)
    body = re.sub(r"//[^\n]*", "", body)
    return body


def _positional_values(body: str) -> list:
    """Values of a positionally-initialised array, in order."""
    body = _strip_comments(body)
    return [v.strip() for v in body.split(",") if v.strip()]


def _designated_values(body: str) -> dict:
    """``{ [KEY] = VALUE, ... }`` → {KEY: VALUE}."""
    out = {}
    for m in re.finditer(r"\[\s*(\w+)\s*\]\s*=\s*([^,\n}]+)", _strip_comments(body)):
        out[m.group(1)] = m.group(2).strip()
    return out


def _parse_int_literal(tok: str):
    """'0xFE00' / '65024' → int, else None."""
    tok = (tok or "").strip()
    try:
        return int(tok, 0)
    except (TypeError, ValueError):
        return None


def _clean_macro_body(body: str) -> str:
    """Strip trailing comments and outer parens from a #define body."""
    # _strip_comments (not split) — splitting on "/*" TRUNCATES the rest of the
    # expression, so "0xFE00 /* base */ + 2" would silently resolve to slot 0.
    expr = _strip_comments(body or "").strip()
    while expr.startswith("("):
        # Peel only a pair that WRAPS the whole expression: the first "(" must
        # close at the very last character. "(A) + (B)" must not be peeled.
        depth = 0
        wraps = False
        for i, ch in enumerate(expr):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    wraps = (i == len(expr) - 1)
                    break
        if not wraps:
            break
        expr = expr[1:-1].strip()
    return expr


def _eval_int_expr(body: str):
    """Evaluate a simple additive integer #define body, or None."""
    expr = _clean_macro_body(body)
    if not expr:
        return None
    total = 0
    for tok in expr.split("+"):
        val = _parse_int_literal(tok.strip())
        if val is None:
            return None
        total += val
    return total


def _eval_slot_expr(body: str, nontrainer_start):
    """Resolve a ``FAME_CHECKER_*`` define to its non-trainer name slot.

    Handles every shape seen in the wild — ``FC_NONTRAINER_START + 2``,
    ``(FC_NONTRAINER_START + 2)``, ``0xFE02``, ``(0xFE00 + 2)``, with or without
    a trailing comment. When the body is written relative to
    ``FC_NONTRAINER_START`` the slot is the numeric remainder, so it resolves
    even if the base value itself is unknown. Returns None when it genuinely
    can't be resolved, so the caller reports it rather than silently
    mislabelling the person as trainer-named.
    """
    expr = _clean_macro_body(body)
    if not expr:
        return None
    total = 0
    saw_base = False
    for tok in expr.split("+"):
        tok = tok.strip()
        if not tok:
            return None
        if tok == "FC_NONTRAINER_START":
            saw_base = True
            continue
        val = _parse_int_literal(tok)
        if val is None:
            return None
        total += val
    if saw_base:
        return total                      # offset from the base
    if nontrainer_start is None:
        return None                       # bare literal, base unknown
    if total < nontrainer_start:
        return None
    return total - nontrainer_start


def _parse_c_string_defs_exact(text: str, symbols) -> dict:
    """Parse ``const u8 <sym>[] = _("…");`` for an EXACT set of symbols.

    Exact-match (not prefix-match) so a project that renamed its custom-name
    symbols still resolves, and so unrelated same-prefix UI strings never leak
    into the editable model.
    """
    out = {}
    for sym in symbols:
        if not sym:
            continue
        m = re.search(
            rf'\b{re.escape(sym)}\s*\[\s*\]\s*=\s*_\(\s*"(.*?)"\s*\)\s*;',
            text, re.DOTALL)
        if m:
            out[sym] = m.group(1)
    return out


# ── public API ──────────────────────────────────────────────────────────────

def has_fame_checker(project_dir: str) -> bool:
    """Cheap check: does this project ship a Fame Checker at all?

    Used to decide whether to show the tab, without paying for a full parse.
    A project that removed the feature gets no tab and is never modified.
    """
    if not project_dir:
        return False
    if not os.path.isfile(os.path.join(project_dir, *_CONSTANTS_H)):
        return False
    if not os.path.isfile(os.path.join(project_dir, *_SOURCE_C)):
        return False
    src = _read(project_dir, _SOURCE_C)
    # Require a real table DEFINITION (`name[...] = {`), not merely a mention —
    # the index maths reference these symbols, so matching a usage would report
    # support for a file whose tables have been gutted.
    return any(
        re.search(rf"\b{name}\s*\[[^\]]*\]\s*=\s*\{{", src)
        for name in ("sFameCheckerFlavorTextPointers",
                     "sFameCheckerNameAndQuotesPointers"))


def parse_person_constants(root: str) -> tuple:
    """((value, name) pairs sorted by value, declared NUM_FAMECHECKER_PERSONS).

    The VALUES matter: the engine indexes the positional tables by a person's
    constant value, not by its declaration order. Discarding them lets a
    duplicate or a gap misalign every entry while the designated tables
    (portrait, trainer link) still resolve by name — i.e. the right portrait
    with the wrong objectives, which looks plausible on screen.
    """
    text = _read(root, _CONSTANTS_H)
    if not text:
        return [], None
    persons = []
    for m in re.finditer(r"^#define\s+(FAMECHECKER_\w+)\s+(\d+)", text, re.M):
        persons.append((int(m.group(2)), m.group(1)))
    persons.sort()
    declared = None
    dm = re.search(r"^#define\s+NUM_FAMECHECKER_PERSONS\s+(\d+)", text, re.M)
    if dm:
        declared = int(dm.group(1))
    return persons, declared


def parse_text_strings(root: str) -> tuple:
    """Parse ``data/text/fame_checker.inc``.

    Returns (``{symbol: decoded string}``, ``[labels in file order]``).
    Consecutive ``.string`` lines are concatenated (as the assembler does) and
    the trailing ``$`` dropped. Ownership is decided later by the caller from
    the table contents — NOT from the label's name.
    """
    text = _read(root, _TEXT_INC)
    out: dict = {}
    labels: list = []
    if not text:
        return out, labels
    current = None
    parts: list = []

    def _flush():
        if current and parts:
            joined = "".join(parts)
            if joined.endswith("$"):
                joined = joined[:-1]
            out[current] = joined

    for line in text.splitlines():
        stripped = line.strip()
        lm = re.match(r"^(\w+)::", stripped)
        if lm:
            _flush()
            current = lm.group(1)
            parts = []
            labels.append(current)
            continue
        sm = re.match(r'^\.string\s+"(.*)"\s*$', stripped)
        if sm and current:
            parts.append(sm.group(1))
    _flush()
    return out, labels


def load_fame_checker(project_dir: str) -> FameCheckerData:
    """Read the whole Fame Checker model out of a project."""
    data = FameCheckerData()
    if not project_dir:
        return data

    # ── Detect whether this project even HAS a Fame Checker ────────────────
    consts_text = _read(project_dir, _CONSTANTS_H)
    src = _read(project_dir, _SOURCE_C)
    if not consts_text and not src:
        data.unavailable_reason = (
            "This project has no Fame Checker — neither "
            "include/constants/fame_checker.h nor src/fame_checker.c exists.")
        return data
    if not src:
        data.unavailable_reason = (
            "src/fame_checker.c is missing — the Fame Checker engine has been "
            "removed from this project.")
        return data
    if not consts_text:
        data.unavailable_reason = (
            "include/constants/fame_checker.h is missing — the Fame Checker "
            "person constants have been removed from this project.")
        return data

    const_pairs, declared = parse_person_constants(project_dir)
    if not const_pairs:
        data.unavailable_reason = (
            "No FAMECHECKER_* person constants found in "
            "include/constants/fame_checker.h.")
        return data
    # The positional tables are indexed by CONSTANT VALUE. If the values aren't
    # a clean 0..n-1 run (a duplicate, or a gap left by deleting a person), every
    # entry silently misaligns while portraits still look right — and a Save
    # would persist that. Refuse rather than guess: a gap also means the engine's
    # own NUM_FAMECHECKER_PERSONS loops are already wrong.
    values = [v for v, _ in const_pairs]
    if values != list(range(len(values))):
        dupes = sorted({v for v in values if values.count(v) > 1})
        data.unavailable_reason = (
            "The FAMECHECKER_* constants are not a contiguous 0-based run "
            f"(values: {values}{', duplicates: ' + str(dupes) if dupes else ''}). "
            "The engine indexes its tables by these values, so the data cannot "
            "be aligned safely. Renumber them consecutively from 0.")
        return data
    consts = [name for _, name in const_pairs]

    data.text_by_symbol, inc_labels = parse_text_strings(project_dir)

    # FC_NONTRAINER_START and the FAME_CHECKER_* pseudo-consts may live in
    # EITHER the .c or the constants header — search both. Looking only in the
    # .c silently reclassifies every custom-named person as trainer-named.
    define_src = consts_text + "\n" + src
    m = re.search(r"^\s*#define\s+FC_NONTRAINER_START\s+(.+)$", define_src, re.M)
    if m:
        # Report rather than default — silently substituting this project's
        # 0xFE00 would misclassify every custom person on a project that uses a
        # different base.
        parsed = _eval_int_expr(m.group(1))
        if parsed is None:
            data.add_problem("warn",
                f"Could not read FC_NONTRAINER_START from "
                f"'#define FC_NONTRAINER_START {m.group(1).strip()}'.")
        else:
            data.non_trainer_start = parsed

    # Resolve every FAME_CHECKER_* define we can. Do NOT report failures here:
    # a project may define unrelated FAME_CHECKER_* macros (limits, UI sizes)
    # that are not person pseudo-consts. Failures are only interesting for a
    # symbol some person actually points at — reported in the person loop.
    pseudo = {}
    pseudo_unresolved = {}
    # True once a person's slot could not be PROVEN either way — used to stop the
    # tail invariant declaring table 4 "dead data" when we simply couldn't tell.
    unprovable_custom = False
    for pm in re.finditer(r"^\s*#define\s+(FAME_CHECKER_\w+)\s+(.+)$", define_src, re.M):
        name, body = pm.group(1), pm.group(2)
        slot = _eval_slot_expr(body, data.non_trainer_start)
        if slot is None:
            pseudo_unresolved[name] = _clean_macro_body(body)
        else:
            pseudo[name] = slot

    # ── The nine tables ────────────────────────────────────────────────────
    trainer_idxs = _designated_values(_array_body(src, "sTrainerIdxs"))
    pic_idxs = _designated_values(_array_body(src, "sFameCheckerTrainerPicIdxs"))
    genders = _designated_values(
        _array_body(src, "sFameCheckerTrainerGenders_Unused"))
    non_trainer = _positional_values(_array_body(src, "sNonTrainerNamePointers"))
    name_quote = _positional_values(
        _array_body(src, "sFameCheckerNameAndQuotesPointers"))
    flavor = _positional_values(
        _array_body(src, "sFameCheckerFlavorTextPointers"))
    npc_gfx = _positional_values(
        _array_body(src, "sFameCheckerArrayNpcGraphicsIds"))
    origin_loc = _positional_values(
        _array_body(src, "sFlavorTextOriginLocationTexts"))
    origin_obj = _positional_values(
        _array_body(src, "sFlavorTextOriginObjectNameTexts"))

    if not flavor:
        data.unavailable_reason = (
            "src/fame_checker.c has no sFameCheckerFlavorTextPointers table — "
            "there are no entries to edit.")
        return data

    data.available = True
    data.metrics = parse_text_metrics(src, project_dir)
    if not data.metrics.derived:
        data.add_problem(
            "warn",
            "Could not read the Fame Checker window sizes from "
            "src/fame_checker.c — text length limits fall back to the values "
            "observed in vanilla and may be wrong for this project.")
    if data.metrics.derived and not data.metrics.geometry_read:
        data.add_problem(
            "warn",
            "Could not read the text printer's line spacing from this project, "
            f"so “{data.metrics.msgbox_lines} line(s) per screen” is an "
            "estimate. Text width is still measured exactly.")
    if data.metrics.gba is None:
        # The measurer module failed to import. Without this the counters go
        # silently BLANK — no numbers, no red, no "estimated" label, and the
        # diagnostics panel says everything is fine. A quiet nothing is the one
        # outcome that must never happen.
        data.add_problem(
            "warn",
            "Text length checking is unavailable — the GBA text measurement "
            "module could not be loaded. Text is still shown and saved "
            "normally, but nothing will warn you if a line is too long for the "
            "window.")
    elif not data.metrics.exact:
        data.add_problem(
            "info",
            "Could not read this project's font width tables (charmap.txt / "
            "src/text.c), so text length feedback is an estimate rather than an "
            "exact pixel measurement.")
    n = len(consts)
    if declared is not None and declared != n:
        data.add_problem("blocking",
            f"NUM_FAMECHECKER_PERSONS is {declared} but {n} FAMECHECKER_* "
            f"constants were found.")

    # Entries-per-person is DERIVED, never assumed.
    entries_per = 0
    if n and flavor and len(flavor) % n == 0:
        entries_per = len(flavor) // n
    else:
        data.add_problem("blocking",
            f"sFameCheckerFlavorTextPointers has {len(flavor)} entries, which is "
            f"not a whole multiple of {n} persons — entries-per-person could not "
            f"be derived, so no entries are shown (guessing a count would invent "
            f"data a Save could write back).")
    data.entries_per_person = entries_per

    # The engine also carries the stride as a LITERAL in its indexing maths
    # (`sFameCheckerFlavorTextPointers[person * 6 + index]` and friends). If a
    # project resized the tables without patching those literals, the tables
    # parse cleanly and every extra entry is one the engine will never read —
    # so the editor would show rows that silently do nothing, and a Save would
    # write text into dead slots. Blocking, because there is no safe guess.
    if entries_per:
        bad = sorted(s for s in _hardcoded_strides(src) if s != entries_per)
        if bad:
            data.add_problem("blocking",
                f"The tables hold {entries_per} entries per person, but "
                f"src/fame_checker.c still indexes them with a hardcoded stride "
                f"of {', '.join(str(b) for b in bad)}. The engine would read the "
                f"wrong entries, so nothing is shown until the code and the "
                f"tables agree.")

    data.non_trainer_name_symbols = non_trainer

    # Tables 6-9 are parallel; a short one is a crash risk in-engine.
    expected = n * entries_per
    for label, tbl in (("sFameCheckerArrayNpcGraphicsIds", npc_gfx),
                       ("sFlavorTextOriginLocationTexts", origin_loc),
                       ("sFlavorTextOriginObjectNameTexts", origin_obj)):
        if tbl and len(tbl) != expected:
            data.add_problem("blocking",
                f"{label} has {len(tbl)} entries but {expected} were expected "
                f"({n} persons x {entries_per}) — the engine indexes it in "
                f"parallel with the flavor-text table.")
        elif not tbl:
            data.add_problem("blocking", f"{label} is missing from src/fame_checker.c.")

    # Symbol ownership is derived from what the TABLES reference — never from a
    # name prefix, because renaming every symbol is exactly what repurposing
    # this as a quest tracker involves. Anything else in the .inc belongs to
    # another system and must survive regeneration untouched.
    owned = set(non_trainer) | set(name_quote) | set(flavor) \
        | set(origin_loc) | set(origin_obj)
    data.foreign_text_symbols = [s for s in inc_labels if s not in owned]
    data.owned_text_symbols = sorted(owned)

    # The four custom names live in src/strings.c. Look them up by EXACT symbol
    # (from table 4) rather than by prefix, so a renamed project still resolves
    # and unrelated gFameChecker* UI strings (notably gFameCheckerText_Cancel,
    # shared by ~15 other systems) never enter the editable model.
    if non_trainer:
        # Most projects keep these in src/strings.c, but fame_checker.c is the
        # other likely home — search both before calling a symbol unresolved.
        for blob in (_read(project_dir, _STRINGS_C), src):
            for sym, val in _parse_c_string_defs_exact(blob, non_trainer).items():
                data.text_by_symbol.setdefault(sym, val)

    # A table can reference a symbol whose string lives somewhere we didn't read
    # (a split .inc, a .s file, another C file). Those resolve to "" — which the
    # UI would show as an empty, editable field and a Save would then write back
    # as an empty string, DELETING the real text. Report them; the UI must render
    # them read-only rather than blank-and-editable.
    # A table slot may legitimately hold NULL / 0 to mean "no text". Those are
    # not symbols and must not be reported as unresolved, nor locked in the UI.
    _real = [s for s in owned
             if s and re.match(r"^[A-Za-z_]\w*$", s) and s != "NULL"]
    data.unresolved_text_symbols = sorted(
        s for s in _real if s not in data.text_by_symbol)
    if data.unresolved_text_symbols:
        sample = ", ".join(data.unresolved_text_symbols[:3])
        data.add_problem("warn",
            f"{len(data.unresolved_text_symbols)} referenced text symbol(s) "
            f"have no string in the files read (e.g. {sample}) — they likely "
            f"live elsewhere. Those fields must stay read-only; saving them "
            f"would overwrite the real text with an empty string.")

    # Table 5 is one array of 2N. A short/long one silently misaligns EVERY
    # quote by one person, which a Save would then write back as real data.
    names: list = []
    quotes: list = []
    if not name_quote:
        # An ABSENT table must be as loud as a wrong-length one — tables 7/8/9
        # already do this. Without it the names/quotes come back empty with no
        # problem raised, and the UI would offer them as editable fields with
        # nowhere to write to.
        data.add_problem(
            "blocking",
            "sFameCheckerNameAndQuotesPointers is missing from "
            "src/fame_checker.c — names and quotes cannot be read.")
    elif len(name_quote) != 2 * n:
        data.add_problem("blocking",
            f"sFameCheckerNameAndQuotesPointers has {len(name_quote)} entries "
            f"but {2 * n} were expected (2 x {n} persons). Names/quotes are "
            f"left blank rather than risk misaligning them.")
    else:
        names = name_quote[:n]
        quotes = name_quote[n:2 * n]

    # NOT blocking. `blocking` means "the model may be wrong, so Save would
    # persist invented data" — and a create/clean-up disagreement does not make
    # the model wrong: every table, person, entry, name and quote still parses
    # correctly. What's wrong is that the running game leaks tiles. So this is
    # a `warn` that locks the PORTRAIT fields only, leaving the text entries
    # editable. Compare the stride mismatch, which genuinely misaligns every
    # entry and does earn `blocking`. A false positive here is then an
    # annoyance rather than a bricked tab — which matters, because the shapes a
    # project can write this dispatch in are open-ended.
    custom_create, custom_destroy, pic_missing = \
        parse_custom_pic_persons(src, consts)
    if pic_missing:
        data.portrait_reason = (
            "Could not locate this project's portrait "
            f"{'creation' if 'create' in pic_missing else 'clean-up'} code in "
            "src/fame_checker.c.")
    elif not custom_create or not custom_destroy:
        # The anchor call exists but no branch was recognised — a `switch`,
        # ternary or helper-function dispatch this parser can't read. Saying
        # "these persons are on one side only" here would be an accusation
        # built on a failed parse; the honest statement is that we can't read
        # the shape.
        data.portrait_reason = (
            "This project dispatches portraits in a form the editor cannot "
            "read, so it cannot tell which people use custom artwork.")
    elif custom_create != custom_destroy:
        only_c = sorted(custom_create - custom_destroy)
        only_d = sorted(custom_destroy - custom_create)
        data.portrait_reason = (
            "src/fame_checker.c decides twice, differently, which people use "
            "custom portrait art: the create path lists "
            f"{', '.join(only_c) or 'nothing extra'} that the clean-up path "
            f"does not, and {', '.join(only_d) or 'nothing extra'} the other "
            "way. In game that leaks sprite tiles and a palette every time the "
            "screen closes.")
    if data.portrait_reason:
        data.add_problem("warn", data.portrait_reason +
                         " Portrait editing is unavailable; everything else on "
                         "this tab still works.")
    else:
        data.graphics = parse_fame_checker_graphics(
            src, project_dir, custom_create, consts)
        # A leaked tag is a defect in the user's project that exists right now,
        # independent of anything the editor does — worth saying out loud, but
        # it makes nothing the editor read wrong, so it never blocks.
        if data.graphics.leaked_tags:
            data.add_problem(
                "warn",
                "These portrait graphics are loaded but never freed: "
                + ", ".join(data.graphics.leaked_tags)
                + ". In game their sprite memory is lost every time the Fame "
                  "Checker closes, until the game is restarted.")

    for i, const in enumerate(consts):
        p = FameCheckerPerson(index=i, const=const)
        p.trainer_idx = trainer_idxs.get(const, "")
        p.trainer_pic = pic_idxs.get(const, "")
        p.uses_custom_pic = const in custom_create
        p.gender_unused = genders.get(const, "")
        # Check EVERY designated table, not just the first — a missing key
        # yields "" here, which a regenerator would happily write back as 0.
        for tbl_name, tbl in (("sTrainerIdxs", trainer_idxs),
                              ("sFameCheckerTrainerPicIdxs", pic_idxs),
                              ("sFameCheckerTrainerGenders_Unused", genders)):
            if tbl and const not in tbl:
                data.add_problem("warn", f"{const} has no entry in {tbl_name}.")

        # Resolve how this person's LIST name is produced.
        slot = pseudo.get(p.trainer_idx)
        if slot is None:
            raw = _parse_int_literal(p.trainer_idx)
            if (raw is not None and data.non_trainer_start is not None
                    and raw >= data.non_trainer_start):
                slot = raw - data.non_trainer_start
        if (slot is None and data.non_trainer_start is None
                and _parse_int_literal(p.trainer_idx) is not None):
            unprovable_custom = True
            # A bare number can be either a trainer index or a custom-name slot,
            # and without the base we cannot tell. Say so rather than silently
            # picking "trainer".
            data.add_problem("warn",
                f"{const} uses the bare numeric index '{p.trainer_idx}', but "
                f"FC_NONTRAINER_START could not be read — it cannot be told apart "
                f"from a trainer index, so it is treated as trainer-named.")
        if slot is None and p.trainer_idx in pseudo_unresolved:
            # A person points at a FAME_CHECKER_* symbol we couldn't evaluate —
            # THIS is worth reporting (an unreferenced macro is not).
            data.add_problem("warn",
                f"{const} points at '{p.trainer_idx}', but its #define "
                f"('{pseudo_unresolved[p.trainer_idx]}') could not be resolved "
                f"to a custom-name slot — treating it as trainer-named.")
        if slot is not None:
            p.name_source = "custom"
            p.custom_name_slot = slot
            if 0 <= slot < len(non_trainer):
                p.custom_name_symbol = non_trainer[slot]
                p.custom_name = data.text_by_symbol.get(p.custom_name_symbol, "")
            else:
                data.add_problem("warn",
                    f"{const} points at non-trainer name slot {slot}, but "
                    f"sNonTrainerNamePointers only has {len(non_trainer)}.")
        else:
            p.name_source = "trainer"

        if i < len(names):
            p.name_symbol = names[i]
            p.name = data.text_by_symbol.get(p.name_symbol, "")
        if i < len(quotes):
            p.quote_symbol = quotes[i]
            p.quote = data.text_by_symbol.get(p.quote_symbol, "")

        for e in range(entries_per):
            k = i * entries_per + e
            entry = FameCheckerEntry(index=e)
            if k < len(flavor):
                entry.text_symbol = flavor[k]
                entry.text = data.text_by_symbol.get(entry.text_symbol, "")
            if k < len(npc_gfx):
                entry.npc_gfx = npc_gfx[k]
            if k < len(origin_loc):
                entry.origin_location_symbol = origin_loc[k]
                entry.origin_location = data.text_by_symbol.get(
                    entry.origin_location_symbol, "")
            if k < len(origin_obj):
                entry.origin_object_symbol = origin_obj[k]
                entry.origin_object = data.text_by_symbol.get(
                    entry.origin_object_symbol, "")
            p.entries.append(entry)
        data.persons.append(p)

    # Invariant: if the project ships custom names, somebody must use them.
    # Zero matches means the pseudo-const parse failed (e.g. an unrecognised
    # define shape) — a parse failure, not a valid project.
    if (non_trainer
            and not any(p.name_source == "custom" for p in data.persons)
            and not unprovable_custom
            and not any(p.trainer_idx in pseudo_unresolved for p in data.persons)):
        # Nothing failed to resolve — the table simply isn't referenced.
        data.add_problem("info",
            f"sNonTrainerNamePointers lists {len(non_trainer)} custom name(s) but "
            f"no person uses one — the table appears to be unused (dead data).")

    return data
