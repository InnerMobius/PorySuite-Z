"""Read a project's time-of-day system — capability, phases, phase->slot map.

Everything the time-of-day encounter feature does gates on this. It answers
three questions from the project's own source, assuming nothing about naming or
phase count:

* **Does this project even have a time-of-day system?** Vanilla pokefirered
  does not (FireRed carts have no RTC). A project may also carry only the weak
  `PorySuite_GetTimeOfDay` stub that returns a "no clock" sentinel. Both must
  read as *absent*, or the encounter editor would offer per-phase tables a
  vanilla build cannot use.

* **What are the phases, in what order?** The phases ARE whatever
  `PorySuite_GetTimeOfDay()` can actually return — not a hardcoded
  Morning/Day/Night, and not merely whatever the enum defines (this project's
  enum defines `TIME_EVENING`, which the function never returns). The enum only
  supplies ordering and values.

* **How does a phase map to a table slot?** The enum is NOT dense: here
  `TIME_NIGHT == 3` while three active phases occupy slots 0/1/2. The engine
  must offset a header lookup by the *slot*, never the raw enum value, or night
  overshoots into the next map's tables. The slot map is derived from the
  ACTIVE phases ordered by enum value.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Phase:
    name: str            # the enum identifier, e.g. "TIME_MORNING"
    key: str             # lower-cased, prefix stripped: "morning" (JSON key / UI)
    enum_value: int      # its value in the enum (0, 1, 3, …)
    active: bool         # is it actually returned by PorySuite_GetTimeOfDay()?
    slot: int = -1       # dense table index among ACTIVE phases, or -1


@dataclass
class TimeOfDayCapability:
    present: bool                                   # usable time system?
    reason: str = ""                                # if absent, why (UI note)
    phases: List[Phase] = field(default_factory=list)        # every enum phase
    source_header: Optional[str] = None             # where the enum was found
    count_symbol: Optional[str] = None              # e.g. "TIME_PHASE_COUNT"

    @property
    def active_phases(self) -> List[Phase]:
        """Only the phases the engine can actually be in, in enum order."""
        return [p for p in self.phases if p.active]

    @property
    def slot_count(self) -> int:
        return len(self.active_phases)

    def phase_by_key(self, key: str) -> Optional[Phase]:
        for p in self.phases:
            if p.key == key:
                return p
        return None


# `PorySuite_GetTimeOfDay(void) { … }` — brace-balanced body captured by hand,
# because the return statements inside it are the authoritative phase list.
_FN_HEAD_RE = re.compile(
    r"\bPorySuite_GetTimeOfDay\s*\(\s*void\s*\)\s*\{")
# `return TIME_DAY;` and `return (TIME_DAY);` both count — parenthesised returns
# are ordinary C. A numeric stub (`return 0xFF;`) still can't match: the capture
# requires an identifier start, and `0` isn't one.
_RETURN_IDENT_RE = re.compile(r"\breturn\s*\(*\s*([A-Za-z_]\w*)\s*\)*\s*;")
# A weak stub is not the project's real clock even if it returns an identifier;
# the strong override is. Recognise the common spellings, not just the canonical
# one the tool emits — a hand-rolled weak default is common in decomps.
_WEAK_RE = re.compile(
    r"__attribute__\s*\(\s*\(\s*weak\s*\)\s*\)|\b__weak\b|\bWEAK\b")
# A count/bound sentinel in the enum (`TIME_PHASE_COUNT`, `_MAX`, `_NUM`) is not
# a phase, wherever it sits in the enum — not only if it happens to be last.
_SENTINEL_RE = re.compile(r"_(?:COUNT|MAX|NUM|TOTAL)$", re.IGNORECASE)


def _iter_source(project_dir: str, exts, needle: str = ""):
    skip = {".git", ".github", "build", "__pycache__", ".vscode", ".idea"}
    for dirpath, dirs, files in os.walk(project_dir):
        dirs[:] = [d for d in dirs if d not in skip]
        for name in files:
            if not name.endswith(exts):
                continue
            path = os.path.join(dirpath, name)
            try:
                with open(path, encoding="utf-8", errors="surrogateescape") as fh:
                    text = fh.read()
            except OSError:
                continue
            if needle and needle not in text:
                continue
            yield path, name, text


def _strip_block_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"//[^\n]*", "", text)
    return text


def _strip_dead_preproc(text: str) -> str:
    """Blank lines inside `#if 0` / `#if FALSE` regions (nesting-aware).

    A strong `PorySuite_GetTimeOfDay` sitting in dead preprocessor code is NOT
    compiled, so reading it as a live clock is a silent false positive — the
    feature would appear for a build that has no clock. Only *unambiguously*
    dead code is removed: `#ifdef`/`#ifndef`/`#elif`/`#else` of macros whose
    truth can't be known from source are left fully live in BOTH branches, so
    this never drops a real definition (no new false negatives). Line count is
    preserved so reported line numbers stay meaningful.
    """
    out = []
    stack = []  # one entry per open #if level: 'dead' or 'live'
    dead = lambda: any(e == "dead" for e in stack)
    for line in text.split("\n"):
        s = line.lstrip()
        if re.match(r"#\s*if\s+(?:0|FALSE)\b", s):
            stack.append("dead"); out.append(""); continue
        if re.match(r"#\s*if\b", s) or re.match(r"#\s*if(?:n?def)\b", s):
            stack.append("live"); out.append(""); continue
        if re.match(r"#\s*elif\b", s):
            if stack:
                stack[-1] = "live"          # unknown branch — keep it live
            out.append(""); continue
        if re.match(r"#\s*else\b", s):
            if stack:
                stack[-1] = "live"          # the else of a dead #if 0 is live
            out.append(""); continue
        if re.match(r"#\s*endif\b", s):
            if stack:
                stack.pop()
            out.append(""); continue
        out.append("" if dead() else line)
    return "\n".join(out)


def _clean(text: str) -> str:
    """Comments gone, then dead `#if 0` code gone. Order matters: a `#if 0`
    written inside a comment must not steer the preprocessor pass."""
    return _strip_dead_preproc(_strip_block_comments(text))


def _balanced_body(text: str, open_brace_pos: int) -> str:
    depth, i = 0, open_brace_pos
    n = len(text)
    while i < n:
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace_pos + 1:i]
        i += 1
    return text[open_brace_pos + 1:]


def _returned_phase_names(project_dir: str) -> set:
    """Identifiers returned by any STRONG PorySuite_GetTimeOfDay definition.

    A numeric stub (`return 0xFF;`) contributes nothing — it doesn't match the
    identifier pattern. A weak definition is skipped even if it returns a name,
    because the strong override is the real clock.
    """
    names = set()
    for _path, _name, raw in _iter_source(project_dir, (".c",),
                                          needle="PorySuite_GetTimeOfDay"):
        text = _clean(raw)
        for m in _FN_HEAD_RE.finditer(text):
            # The weak attribute belongs to THIS function's declarator only —
            # the span from the previous statement's end to the name. A fixed
            # look-back window would swallow a weak attribute on an unrelated
            # neighbouring function and wrongly discard the real strong clock.
            prev_end = max(text.rfind(";", 0, m.start()),
                           text.rfind("}", 0, m.start()))
            declarator = text[prev_end + 1:m.start()]
            if _WEAK_RE.search(declarator):
                continue
            body = _balanced_body(text, m.end() - 1)
            for r in _RETURN_IDENT_RE.finditer(body):
                names.add(r.group(1))
    return names


def _find_phase_enum(project_dir: str, wanted: set):
    """The enum defining `wanted`: ordered [(name, value), …] + count symbol.

    Returns (members, count_symbol, header_path) or (None, None, None). Members
    are every constant in that enum, in order, with C enum value semantics
    (implicit +1, explicit `= N` respected). The count symbol is a trailing
    member that none of `wanted` includes — conventionally `*_COUNT`.
    """
    for path, _name, raw in _iter_source(project_dir, (".h", ".c")):
        text = _clean(raw)
        for em in re.finditer(r"\benum\b[^{;]*\{([^}]*)\}", text):
            body = em.group(1)
            members: list = []
            value = -1
            for token in body.split(","):
                token = token.strip()
                if not token:
                    continue
                mm = re.match(r"^([A-Za-z_]\w*)\s*(?:=\s*(.+))?$", token)
                if not mm:
                    continue
                ident = mm.group(1)
                if mm.group(2) is not None:
                    try:
                        value = int(mm.group(2).strip(), 0)
                    except ValueError:
                        value += 1        # non-literal init; best-effort order
                else:
                    value += 1
                members.append((ident, value))
            names = {m[0] for m in members}
            if wanted and wanted <= names:
                # The count sentinel is recognised by name (`…_COUNT/_MAX/…`)
                # wherever it appears, so it never counts as a phase and never
                # shifts a real phase's reported value — and an inactive real
                # phase that happens to sit last (e.g. a dusk with no return
                # yet) is NOT mistaken for a sentinel.
                count_symbol = next(
                    (n for (n, _v) in members
                     if n not in wanted and _SENTINEL_RE.search(n)), None)
                return members, count_symbol, path
    return None, None, None


def _common_prefix_key(name: str, all_names: set) -> str:
    """Strip the phase identifiers' shared prefix and lower-case the rest.

    `TIME_MORNING` with siblings `TIME_DAY`/`TIME_NIGHT` -> `morning`. Computed
    from the group so a project using `PHASE_DAWN` etc. gets `dawn`, not a
    hardcoded assumption about `TIME_`.
    """
    if len(all_names) >= 2:
        prefix = os.path.commonprefix(sorted(all_names))
        # Only cut on an underscore boundary so we don't chop mid-word.
        cut = prefix.rfind("_")
        if cut >= 0:
            stripped = name[cut + 1:]
            if stripped:
                return stripped.lower()
    # Single phase or no shared prefix: strip a leading TIME_/PHASE_ if present.
    return re.sub(r"^(?:TIME|PHASE)_", "", name, flags=re.IGNORECASE).lower()


def parse_time_of_day(project_dir: str) -> TimeOfDayCapability:
    """Read the project's time-of-day capability, or report why it's absent."""
    returned = _returned_phase_names(project_dir)
    if not returned:
        return TimeOfDayCapability(
            present=False,
            reason="This project has no time-of-day system "
                   "(PorySuite_GetTimeOfDay does not return any phase), so "
                   "wild encounters cannot vary by time of day.")

    members, count_symbol, header = _find_phase_enum(project_dir, returned)
    if members is None:
        return TimeOfDayCapability(
            present=False,
            reason="PorySuite_GetTimeOfDay returns %s, but no enum defining "
                   "those phases was found, so their order can't be resolved."
                   % ", ".join(sorted(returned)))

    # Every real phase member (drop the trailing count sentinel).
    phase_members = [(n, v) for (n, v) in members if n != count_symbol]
    all_names = {n for (n, _v) in phase_members}

    phases = [
        Phase(name=n, key=_common_prefix_key(n, all_names),
              enum_value=v, active=(n in returned))
        for (n, v) in phase_members
    ]
    # Dense slots for ACTIVE phases, ordered by enum value.
    for slot, p in enumerate(sorted((p for p in phases if p.active),
                                    key=lambda p: p.enum_value)):
        p.slot = slot

    return TimeOfDayCapability(
        present=True, phases=phases, count_symbol=count_symbol,
        source_header=os.path.relpath(header, project_dir).replace("\\", "/"),
    )
