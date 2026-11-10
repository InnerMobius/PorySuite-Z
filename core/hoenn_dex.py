"""The engine's Hoenn Pokedex ordering: one ordering plus one boundary.

WHAT THIS MODULE IS -- AND IS NOT
---------------------------------
This reads and writes the ``HOENN_DEX_*`` enum in
``include/constants/pokedex.h``: ONE complete ordering, where membership is the
PREFIX of it up to ``HOENN_DEX_COUNT``. ``src/pokedex.c`` iterates
``i < HOENN_DEX_COUNT`` calling ``HoennToNationalOrder(i + 1)``, so entries
``1..HOENN_DEX_COUNT`` are included and everything after is indexed but
excluded.

It is NOT the regional Pokedex the player browses. In pokefirered that is
``KANTO_DEX_COUNT`` applied to the NATIONAL order, which is what
``src/pokedex_screen.c`` reads throughout::

    s32 max_n = IsNationalPokedexEnabled() ? NATIONAL_DEX_COUNT : KANTO_DEX_COUNT;

``HOENN_DEX_COUNT`` has exactly one live use in the whole engine --
``HasAllHoennMons()``, setting ``caughtAllHoenn`` on the trainer card -- and
``GetHoennPokedexCount`` beside it is commented out. The ordering is inherited
Ruby/Sapphire plumbing, in vanilla as much as in any fork.

An earlier version of this docstring asserted the opposite: that
``national_dex[:KANTO_DEX_COUNT]`` matched the engine's regional dex "in
neither length, order, nor membership". That was written after reading the
header and before reading the code that consumes it. The Kanto slice is what
the dex screen reads. A tab labelled "Regional Dex" belongs on that boundary;
this module describes a different, largely vestigial list and must not be
presented as the one the player sees.

So the authoritative model is one list and one boundary::

    order           every HOENN_DEX_* entry, in engine order, NONE excluded
    regional_count  how many of them are in the dex

and never two independently mutable lists that can silently disagree. The
included entries are exactly ``order[:regional_count]``.

WHAT THIS MODULE DELIBERATELY DOES NOT WRITE
--------------------------------------------
The two macro tables in ``src/pokemon.c`` are NOT part of serialization. Both
expand from the name alone::

    SPECIES_TO_HOENN(name)   [SPECIES_##name  - 1] = HOENN_DEX_##name
    HOENN_TO_NATIONAL(name)  [HOENN_DEX_##name - 1] = NATIONAL_DEX_##name

Index and value both derive from the name, so the rows carry no positional
information and the preprocessor re-indexes every one of them from the enum.
The engine states this itself directly above the arrays: "The order of the
elements in the 3 arrays below is irrelevant. To reorder the pokedex, see the
values in include/constants/pokedex.h."

Verified by scanning every row of both tables in a real project: each is a
bare macro invocation and none carries a literal index. Reordering therefore must NOT
rewrite ``pokemon.c``. Regenerating those tables would buy nothing and risk an
off-by-one that still compiles while misidentifying Pokemon throughout the
Pokedex -- the one failure mode a build cannot catch.

``pokemon.c`` needs an edit only if the SET of names changes (a species added
to or removed from the Hoenn enum entirely), which membership and ordering
edits do not do.

DO NOT REGRESS
--------------
* ``order`` and ``regional_count`` are the single source of truth. Anything
  that stores a second regional list must derive it, not duplicate it.
* Parsing is anchored on the engine's own section comment. ``pokedex.h`` holds
  several enums and the National one comes FIRST, so "the first enum in the
  file" is the wrong block.
* Read the RAW header text, never preprocessed lines: the anchor is a comment,
  and the writer must see byte-for-byte what the parser saw or a no-op save
  will not round-trip.
* ``HOENN_DEX_COUNT`` names the LAST INCLUDED entry, not the entry after it and
  not a row count. Its enum VALUE is the number of included entries because
  ``HOENN_DEX_NONE`` occupies 0.
* Boundary comments are pinned to the boundary, not carried along with the
  constant that happens to follow them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# The engine's own section comment above the Hoenn enum. pokedex.h contains the
# National enum first, so this anchor is what distinguishes the two blocks.
HOENN_ANCHOR = "Hoenn Pokedex order"

_HOENN_PREFIX = "HOENN_DEX_"
_NATIONAL_PREFIX = "NATIONAL_DEX_"
_NONE = "HOENN_DEX_NONE"
_COUNT_DEFINE = "HOENN_DEX_COUNT"

_CONST_RE = re.compile(r"^(HOENN_DEX_[A-Z0-9_]+)\s*,?$")
_COUNT_RE = re.compile(
    r"^(\s*#define\s+HOENN_DEX_COUNT\s+)(\S+)(.*)$", re.MULTILINE
)
_HOENN_TO_NATIONAL_RE = re.compile(r"HOENN_TO_NATIONAL\(\s*(\w+)\s*\)")
_SPECIES_TO_HOENN_RE = re.compile(r"SPECIES_TO_HOENN\(\s*(\w+)\s*\)")


class HoennDexError(RuntimeError):
    """The Hoenn dex could not be read or written confidently.

    Raised instead of guessing. A failed parse must never fall back to the old
    national-order slice, because that fallback could then be written into
    engine source and destroy the real dex.
    """


@dataclass
class HoennDex:
    """The engine's Hoenn ordering and its active membership boundary."""

    order: list[str] = field(default_factory=list)
    regional_count: int = 0
    count_symbol: str = ""
    national: dict[str, str] = field(default_factory=dict)
    boundary_comments: list[str] = field(default_factory=list)
    indent: str = "    "

    @property
    def included(self) -> list[str]:
        """The entries actually in the regional dex."""
        return self.order[: self.regional_count]

    @property
    def excluded(self) -> list[str]:
        """Entries present in the Hoenn ordering but outside the dex.

        These must survive a membership edit. Removing a Pokemon from the dex
        moves it here; it is not deleted from the ordering.
        """
        return self.order[self.regional_count:]

    def national_of(self, hoenn_const: str) -> str | None:
        """The NATIONAL_DEX_* identity of a Hoenn entry, per the engine table."""
        return self.national.get(hoenn_const)

    def to_cache(self) -> dict:
        """The shape stored in ``src/data/pokedex.json``."""
        return {
            "hoenn_order": [
                {
                    "hoenn_constant": h,
                    "dex_constant": self.national.get(h),
                }
                for h in self.order
            ],
            "regional_count": self.regional_count,
            "hoenn_count_symbol": self.count_symbol,
        }


# ---------------------------------------------------------------------------
# Locating the block
# ---------------------------------------------------------------------------

def find_hoenn_enum(header_text: str) -> tuple[int, int, str]:
    """Return ``(body_start, body_end, indent)`` character offsets.

    ``body_start`` is just past the opening brace and ``body_end`` is at the
    matching closing brace, so ``header_text[body_start:body_end]`` is exactly
    the enum body.

    Anchored on :data:`HOENN_ANCHOR` rather than on "the first enum", because
    the National enum appears earlier in the same file.
    """
    anchor = header_text.find(HOENN_ANCHOR)
    if anchor < 0:
        raise HoennDexError(
            "Could not find the Hoenn Pokedex section in "
            "include/constants/pokedex.h (looked for the comment "
            f"{HOENN_ANCHOR!r}). The regional Pokedex cannot be read or "
            "saved until that section is present. No engine file was modified."
        )

    open_brace = header_text.find("{", anchor)
    if open_brace < 0:
        raise HoennDexError(
            "Found the Hoenn Pokedex comment in include/constants/pokedex.h "
            "but no enum body after it. No engine file was modified."
        )

    depth = 0
    for i in range(open_brace, len(header_text)):
        ch = header_text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                body = header_text[open_brace + 1:i]
                return open_brace + 1, i, _infer_indent(body)

    raise HoennDexError(
        "The Hoenn enum in include/constants/pokedex.h is not closed. "
        "No engine file was modified."
    )


def _infer_indent(body: str) -> str:
    for raw in body.splitlines():
        if raw.strip().startswith(_HOENN_PREFIX):
            return raw[: len(raw) - len(raw.lstrip())]
    return "    "


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_hoenn_order(header_text: str) -> tuple[list[str], list[str], str]:
    """Return ``(order, boundary_comments, indent)``.

    ``order`` excludes :data:`_NONE`, which is a structural anchor at value 0
    and never a draggable entry.

    Comments that refer to the membership boundary are collected separately so
    the writer can pin them to the boundary instead of letting them travel with
    whichever constant follows them.
    """
    start, end, indent = find_hoenn_enum(header_text)
    body = header_text[start:end]

    order: list[str] = []
    boundary_comments: list[str] = []
    seen: set[str] = set()
    duplicates: list[str] = []
    unparsed: list[str] = []

    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("//"):
            if _is_boundary_comment(line):
                boundary_comments.append(line)
            continue
        m = _CONST_RE.match(line)
        if not m:
            unparsed.append(line)
            continue
        const = m.group(1)
        if const == _NONE:
            continue
        if const in seen:
            duplicates.append(const)
            continue
        seen.add(const)
        order.append(const)

    if unparsed:
        raise HoennDexError(
            "The Hoenn enum in include/constants/pokedex.h contains "
            f"{len(unparsed)} line(s) this editor cannot read, starting with "
            f"{unparsed[0]!r}. The regional Pokedex is read-only until that is "
            "resolved, so no engine file was modified."
        )
    if duplicates:
        raise HoennDexError(
            "The Hoenn enum in include/constants/pokedex.h lists these "
            "entries more than once: " + ", ".join(sorted(set(duplicates)))
            + ". Remove the duplicates before editing the regional Pokedex. "
            "No engine file was modified."
        )
    if not order:
        raise HoennDexError(
            "The Hoenn enum in include/constants/pokedex.h has no entries. "
            "No engine file was modified."
        )

    return order, boundary_comments, indent


def _is_boundary_comment(line: str) -> bool:
    """Is this comment about the membership boundary rather than a creature?

    Deliberately narrow. A comment that is merely NEAR the boundary stays
    attached to its constant; only one that talks about the boundary is pinned
    to it.
    """
    low = line.lower()
    return (
        _COUNT_DEFINE.lower() in low
        or "end of hoenn dex" in low
        or "excluded from the pokedex" in low
    )


def parse_hoenn_count(header_text: str, order: list[str]) -> tuple[int, str]:
    """Resolve ``HOENN_DEX_COUNT`` to a NUMBER of included entries.

    The define names an enum member, and that member's VALUE is the number of
    included entries because ``HOENN_DEX_NONE`` occupies 0. It is NOT the entry
    after the last included one, and NOT a row count: measured in this project
    it names an enum member rather than giving a count, and that member can
    sit anywhere in the ordering -- resolve it, never assume a position.
    """
    m = _COUNT_RE.search(header_text)
    if not m:
        raise HoennDexError(
            "include/constants/pokedex.h does not define HOENN_DEX_COUNT, so "
            "this editor cannot tell which Pokemon are in the regional "
            "Pokedex. No engine file was modified."
        )
    symbol = m.group(2).strip()

    if symbol.isdigit():
        count = int(symbol)
    elif symbol in order:
        # order excludes HOENN_DEX_NONE (value 0), so a 0-based index of i is
        # enum value i + 1, which is also the number of included entries.
        count = order.index(symbol) + 1
    else:
        raise HoennDexError(
            f"HOENN_DEX_COUNT names {symbol!r}, which is not an entry in the "
            "Hoenn enum. The regional Pokedex boundary cannot be resolved, so "
            "no engine file was modified."
        )

    if not 0 < count <= len(order):
        raise HoennDexError(
            f"HOENN_DEX_COUNT resolves to {count}, which is outside the "
            f"{len(order)}-entry Hoenn ordering. No engine file was modified."
        )
    return count, symbol


def parse_hoenn_to_national(pokemon_c_text: str) -> dict[str, str]:
    """``{HOENN_DEX_X: NATIONAL_DEX_X}`` from the engine's own macro rows.

    Read from the engine rather than assuming ``HOENN_DEX_X`` implies
    ``NATIONAL_DEX_X``. The suffix rule does hold in this project across all
    the enum, but it is the engine's table that decides, so the table is what
    is read.
    """
    out: dict[str, str] = {}
    for name in _HOENN_TO_NATIONAL_RE.findall(pokemon_c_text or ""):
        if name == "name":  # the #define line itself
            continue
        out[_HOENN_PREFIX + name] = _NATIONAL_PREFIX + name
    return out


def parse_species_to_hoenn(pokemon_c_text: str) -> dict[str, str]:
    """``{SPECIES_X: HOENN_DEX_X}`` from the engine's own macro rows."""
    out: dict[str, str] = {}
    for name in _SPECIES_TO_HOENN_RE.findall(pokemon_c_text or ""):
        if name == "name":
            continue
        out["SPECIES_" + name] = _HOENN_PREFIX + name
    return out


def parse(header_text: str, pokemon_c_text: str | None = None) -> HoennDex:
    """Read the authoritative Hoenn ordering and boundary from engine source.

    Raises :class:`HoennDexError` with an actionable message rather than
    returning a partial or guessed model.
    """
    order, boundary_comments, indent = parse_hoenn_order(header_text)
    count, symbol = parse_hoenn_count(header_text, order)

    national = parse_hoenn_to_national(pokemon_c_text or "")
    if national:
        missing = [h for h in order if h not in national]
        if missing:
            raise HoennDexError(
                f"{len(missing)} Hoenn Pokedex entr(ies) have no National Dex "
                "identity in the engine's HOENN_TO_NATIONAL table, starting "
                f"with {missing[0]}. The regional Pokedex cannot be saved "
                "until every entry maps to a National Dex constant. No engine "
                "file was modified."
            )
    else:
        # pokemon.c was not supplied or not parseable. Fall back to the suffix
        # correspondence, which is the engine's own macro definition, and record
        # nothing as verified.
        national = {
            h: _NATIONAL_PREFIX + h[len(_HOENN_PREFIX):] for h in order
        }

    return HoennDex(
        order=order,
        regional_count=count,
        count_symbol=symbol,
        national=national,
        boundary_comments=boundary_comments,
        indent=indent,
    )


# ---------------------------------------------------------------------------
# Serializing
# ---------------------------------------------------------------------------

def render_enum_body(dex: HoennDex) -> str:
    """The enum body text for ``dex``, boundary comments pinned to the boundary.

    Reproduces the engine's layout exactly for an unedited model, which is what
    makes a no-op save byte-identical.
    """
    indent = dex.indent
    out = ["\n"]
    out.append(f"{indent}{_NONE},\n")
    for i, const in enumerate(dex.order):
        out.append(f"{indent}{const},\n")
        if i + 1 == dex.regional_count:
            for comment in dex.boundary_comments:
                out.append(f"{indent}{comment}\n")
    return "".join(out)


def serialize(header_text: str, dex: HoennDex) -> str:
    """Return ``header_text`` with the Hoenn enum and count updated.

    Pure: it returns new text and writes nothing, so a caller can validate
    every output before replacing any file.
    """
    validate(dex)

    start, end, _indent = find_hoenn_enum(header_text)
    patched = header_text[:start] + render_enum_body(dex) + header_text[end:]

    last_included = dex.order[dex.regional_count - 1]

    def _sub(m: re.Match) -> str:
        return f"{m.group(1)}{last_included}{m.group(3)}"

    patched, n = _COUNT_RE.subn(_sub, patched, count=1)
    if n != 1:
        raise HoennDexError(
            "Could not update the HOENN_DEX_COUNT define in "
            "include/constants/pokedex.h. No engine file was modified."
        )
    return patched


def validate(dex: HoennDex) -> None:
    """Refuse an unsound model before anything is written."""
    if not dex.order:
        raise HoennDexError(
            "The regional Pokedex has no entries to save. No engine file was "
            "modified."
        )

    dupes = {c for c in dex.order if dex.order.count(c) > 1}
    if dupes:
        raise HoennDexError(
            "These Pokemon appear more than once in the Hoenn ordering: "
            + ", ".join(sorted(dupes))
            + ". Remove the duplicates before saving. No engine file was "
            "modified."
        )

    if _NONE in dex.order:
        raise HoennDexError(
            f"{_NONE} is a structural anchor and cannot be placed in the "
            "ordering. No engine file was modified."
        )

    if not 0 < dex.regional_count <= len(dex.order):
        raise HoennDexError(
            f"The regional Pokedex boundary is {dex.regional_count}, which is "
            f"outside the {len(dex.order)}-entry Hoenn ordering. No engine "
            "file was modified."
        )

    bad = [c for c in dex.order if not c.startswith(_HOENN_PREFIX)]
    if bad:
        raise HoennDexError(
            "These Hoenn ordering entries are not HOENN_DEX_* constants: "
            + ", ".join(bad[:5])
            + ". No engine file was modified."
        )

    # Every entry must have a National Dex identity. Refuse the whole write
    # rather than dropping the entry or inventing an identifier.
    unidentified = [c for c in dex.order if not dex.national.get(c)]
    if unidentified:
        raise HoennDexError(
            f"{len(unidentified)} Pokemon in the Hoenn ordering have no "
            "National Dex identity, starting with "
            f"{unidentified[0][len(_HOENN_PREFIX):]}. Give them a National Dex "
            "entry before saving the regional Pokedex. No engine file was "
            "modified."
        )


# ---------------------------------------------------------------------------
# Membership editing, against the single model
# ---------------------------------------------------------------------------

def include(dex: HoennDex, hoenn_const: str, position: int | None = None) -> None:
    """Move an entry into the regional dex, in place.

    Deterministic per the agreed rules: an added Pokemon lands at the END of
    the included section unless an explicit position is given. The entry is
    never removed from the overall ordering, and entries not involved keep
    their relative order.
    """
    if hoenn_const not in dex.order:
        raise HoennDexError(
            f"{hoenn_const} is not in the Hoenn ordering, so it cannot be "
            "added to the regional Pokedex."
        )
    current = dex.order.index(hoenn_const)
    if current < dex.regional_count:
        if position is not None and position != current:
            move(dex, hoenn_const, position)
        return

    dex.order.pop(current)
    target = dex.regional_count if position is None else max(
        0, min(position, dex.regional_count)
    )
    dex.order.insert(target, hoenn_const)
    dex.regional_count += 1


def exclude(dex: HoennDex, hoenn_const: str) -> None:
    """Move an entry out of the regional dex, in place.

    It lands at the BEGINNING of the excluded section and stays in the
    ordering. Nothing is deleted.
    """
    if hoenn_const not in dex.order:
        raise HoennDexError(
            f"{hoenn_const} is not in the Hoenn ordering, so it cannot be "
            "removed from the regional Pokedex."
        )
    current = dex.order.index(hoenn_const)
    if current >= dex.regional_count:
        return

    dex.order.pop(current)
    dex.regional_count -= 1
    dex.order.insert(dex.regional_count, hoenn_const)


def move(dex: HoennDex, hoenn_const: str, position: int) -> None:
    """Reorder within whichever section the entry is already in.

    A reorder never changes membership, so the boundary does not move.
    """
    if hoenn_const not in dex.order:
        raise HoennDexError(
            f"{hoenn_const} is not in the Hoenn ordering."
        )
    current = dex.order.index(hoenn_const)
    included = current < dex.regional_count

    if included:
        low, high = 0, dex.regional_count - 1
    else:
        low, high = dex.regional_count, len(dex.order) - 1

    target = max(low, min(position, high))
    if target == current:
        return
    dex.order.pop(current)
    dex.order.insert(target, hoenn_const)
