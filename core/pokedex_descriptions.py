"""Independent Pokedex descriptions: planning, symbol allocation, writing.

THE PROBLEM THIS SOLVES

A Pokedex entry's description is a POINTER, not a string::

    [NATIONAL_DEX_OCTO] = { .description = gOctoPokedexText, ... }

and the text lives in a separate file::

    const u8 gOctoPokedexText[] = _(
        "There is a plant seed on its back right\n"
        "from the day this POKeMON is born.\n"
        "The seed slowly grows larger.");

Most creatures have their own symbol. Creatures a project ADDS do not:
pokefirered ships a shared ``gDummyPokedexText`` placeholder and every appended
creature points at it. That is an artefact of being appended, not a design --
distinct creatures do not share a Pokedex entry, and a project of any size ends
up with a hundred of them pointing at one block.

Sharing cannot be edited out of. Writing the user's words into
``gDummyPokedexText`` changes what every sharer displays, and if two of them are
edited in one save the second silently wins.

WHAT THIS MODULE DOES

**Un-duplicates.** Any entry still pointing at the placeholder is given a symbol
and a block of its own, seeded with the placeholder's current words so nothing
changes on screen -- the same text, in storage the creature owns, so it can then
be edited like any other. A creature already carrying its own symbol is left on
it. Emits both halves of the change together:

* a new ``const u8 gXxxPokedexText[]`` block in the description text file(s)
* the matching ``.description =`` pointer in ``pokedex_entries.h``

Half of that is worse than none: a pointer to a symbol that does not exist does
not compile, and a block nothing points at is dead weight. So the caller
prepares every file's new content first and writes only if all of them could be
produced -- see ``apply``, which returns new text for each file and never
touches the filesystem itself.

RULES, in the order they matter

1. ``gDummyPokedexText`` is never rewritten. It is shared engine data, and the
   whole point of allocation is to stop editing it.
2. A creature that already has its own symbol keeps it. Its block is rewritten
   in place, which is what "edit my description" has always meant.
3. A creature with its OWN symbol whose text still equals what is on disk is
   not touched at all. A creature on the SHARED placeholder is always given its
   own symbol, edited or not -- that is the un-duplication, and it is why one
   save after this arrived reports a hundred descriptions written on a project
   with a hundred added creatures. It happens once.
6. The dex's null entry is never un-duplicated. Vanilla pokefirered points
   exactly one entry at the placeholder and it is ``[NATIONAL_DEX_NONE]``
   ("UNKNOWN"), which is not a creature. Excluding it is what makes this a
   no-op on a project that has added nothing -- measured on a vanilla tree:
   387 entries, 1 placeholder pointer, that one.
4. A new symbol is derived from the engine's own constant, never the display
   name. Names are renamed, re-cased and translated; ``NATIONAL_DEX_OCTO`` is
   stable, and it is also what vanilla's own symbols are named after.
5. Collisions are checked against every symbol defined in any text file, every
   symbol referenced by the entries file, and the ones allocated earlier in the
   same plan. A collision gets a numeric suffix rather than silently sharing.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

DUMMY = "gDummyPokedexText"

_BLOCK_RE = re.compile(
    r"const\s+u8\s+(g\w+)\s*\[\]\s*=\s*_\(\s*((?:\"(?:[^\"\\]|\\.)*\"\s*)+)\)\s*;",
    re.S,
)
_STRING_RE = re.compile(r'"((?:[^"\\]|\\.)*)"', re.S)
_POINTER_RE = re.compile(r"\.(?:unused)?[Dd]escription\s*=\s*(g\w+)\s*,")


class DescriptionError(RuntimeError):
    """A description change that cannot be made safely.

    Every message ends by saying nothing was written, because the user's next
    question is always "did it break my game?".
    """


@dataclass
class DescriptionPlan:
    """What a save would do to descriptions, decided before anything is written."""

    #: NATIONAL_DEX_* -> the symbol newly allocated for it
    allocations: dict[str, str] = field(default_factory=dict)
    #: symbol -> replacement text, for creatures that already had their own
    rewrites: dict[str, str] = field(default_factory=dict)
    #: symbol -> text, for blocks that do not exist yet
    new_blocks: dict[str, str] = field(default_factory=dict)
    #: human-readable reasons something was NOT written
    conflicts: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.allocations or self.rewrites or self.new_blocks)

    @property
    def touched(self) -> int:
        """How many creatures this plan changes."""
        return len(self.allocations) + len(self.rewrites)


# ---------------------------------------------------------------------------
# Reading what is there
# ---------------------------------------------------------------------------

def parse_blocks(text: str) -> dict[str, str]:
    """Every ``const u8 gXxx[] = _( ... );`` in *text*, as symbol -> plain text.

    The stored form escapes line breaks as a literal ``\\n`` inside the string
    pieces; this returns what the player would read, so it can be compared
    against what the editor holds.
    """
    out: dict[str, str] = {}
    for sym, body in _BLOCK_RE.findall(text):
        joined = "".join(_STRING_RE.findall(body))
        joined = joined.replace("\\n", "\n").replace('\\"', '"')
        joined = joined.replace("\\\\", "\\")
        out[sym] = joined
    return out


def referenced_symbols(entries_text: str) -> set[str]:
    """Symbols the entries file points at, description and unusedDescription."""
    return set(_POINTER_RE.findall(entries_text))


_ENTRY_RE = re.compile(r"\[(NATIONAL_DEX_\w+)\]\s*=\s*\{(.*?)\n\s*\},", re.S)
_DESC_RE = re.compile(r"\.description\s*=\s*(g\w+)\s*,")


def description_pointers(entries_text: str) -> dict[str, str]:
    """What the ENGINE says each entry's description is, by dex constant.

    The cache is not asked. A cached pointer can be missing (the extractor
    dropped entries whose species constant was an expression rather than a
    literal -- every species an editor appends) or stale, and deciding whether a
    description is shared from a cache that lost the pointer is how a
    creature ends up with a second symbol while its first block is orphaned.
    """
    out: dict[str, str] = {}
    for m in _ENTRY_RE.finditer(entries_text):
        d = _DESC_RE.search(m.group(2))
        if d:
            out[m.group(1)] = d.group(1)
    return out


def taken_symbols(entries_text: str, *text_files: str) -> set[str]:
    """Every name already spoken for, from any of the files involved."""
    taken: set[str] = referenced_symbols(entries_text)
    for t in text_files:
        if t:
            taken |= set(parse_blocks(t))
            # A block the block-regex cannot parse still owns its name.
            taken |= set(re.findall(r"const\s+u8\s+(g\w+)\s*\[\]", t))
    return taken


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------

def _camel(const: str) -> str:
    """``NATIONAL_DEX_BIG_OCTO`` -> ``BigOcto``, matching vanilla's style."""
    body = const
    for prefix in ("NATIONAL_DEX_", "HOENN_DEX_", "SPECIES_"):
        if body.startswith(prefix):
            body = body[len(prefix):]
            break
    parts = [p for p in body.split("_") if p]
    return "".join(p[:1].upper() + p[1:].lower() for p in parts) or "Entry"


def allocate_symbol(const: str, taken: set[str]) -> str:
    """A free ``g<Name>PokedexText`` for *const*.

    *taken* is both the question and the record: the chosen name is added to it
    so the next allocation in the same plan cannot pick it again.
    """
    base = f"g{_camel(const)}PokedexText"
    name = base
    n = 2
    while name in taken:
        name = f"{base}{n}"
        n += 1
        if n > 9999:                      # pathological; refuse rather than spin
            raise DescriptionError(
                f"Could not find an unused text symbol for {const}. "
                "No engine file was modified."
            )
    taken.add(name)
    return name


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------

def _is_null_entry(const: str, entry) -> bool:
    """True for the dex's index-0 placeholder, which is not a creature.

    Vanilla pokefirered points exactly ONE entry at ``gDummyPokedexText``::

        [NATIONAL_DEX_NONE] = {
            .categoryName = _("UNKNOWN"),
            .description = gDummyPokedexText,
        }

    Measured on a vanilla tree: 387 entries, 1 placeholder pointer, that one.
    Un-duplicating it would write a block into a project that had added
    nothing, which is not this tool's business.

    In practice the extractor's rows start at dex number 1 and do not include
    it, so this is a guard rather than a filter -- it is here because an
    extractor that DID include row 0 must not quietly rewrite it.
    """
    if const == "NATIONAL_DEX_NONE":
        return True
    try:
        return int(entry.get("dex_num")) == 0
    except (TypeError, ValueError):
        return False


def plan_descriptions(
    natdex, entries_text: str, primary_text: str, *other_texts: str
) -> DescriptionPlan:
    """Decide what to write for every entry in *natdex*.

    *natdex* entries are the cached Pokedex rows: ``dex_constant``, the current
    pointer in ``description``, and the editor's text in ``descriptionText``.

    *primary_text* is the description file the editor's text actually came
    from, and the ONLY one compared against. The others exist for the same
    creatures but hold different words -- pokefirered ships FireRed and
    LeafGreen descriptions side by side and the build picks one. Merging them
    into a single view made 253 untouched creatures look edited, because the
    editor shows FireRed's text and LeafGreen's differs for 256 of them. They
    are still scanned, for name collisions and for whether a symbol exists.
    """
    on_disk: dict[str, str] = parse_blocks(primary_text) if primary_text else {}
    defined_anywhere = dict(on_disk)
    for t in other_texts:
        if t:
            for sym, body in parse_blocks(t).items():
                defined_anywhere.setdefault(sym, body)

    taken = taken_symbols(entries_text, primary_text, *other_texts)
    pointed_at = referenced_symbols(entries_text)
    engine_pointer = description_pointers(entries_text)

    plan = DescriptionPlan()
    # symbol -> [(name, text)] for symbols shared by more than one creature
    shared_edits: dict[str, list[tuple[str, str]]] = {}

    for entry in natdex:
        const = entry.get("dex_constant")
        desired = entry.get("descriptionText")
        if not const:
            continue
        if not isinstance(desired, str):
            desired = ""
        if const not in engine_pointer:
            plan.conflicts.append(
                f"{const} is not in pokedex_entries.h, so its description "
                "could not be written."
            )
            continue
        # The engine's pointer, not the cache's. See description_pointers.
        sym = engine_pointer[const]

        if sym and sym != DUMMY:
            # Its own block already. Rewrite in place when the text moved.
            current = on_disk.get(sym)
            if current is None and sym not in defined_anywhere:
                plan.conflicts.append(
                    f"{const} points at {sym}, which is not defined in the "
                    "description text files, so its text was not written."
                )
                continue
            if current != desired:
                plan.rewrites[sym] = desired
            continue

        # On the shared placeholder, or carrying no pointer at all. Give it a
        # symbol of its own whether or not the text has been edited: sharing is
        # the thing being removed, not a state worth preserving. Before
        # 2026-11-08 an unedited sharer was skipped, which left every appended
        # creature pointing at one block and meant "write a description for
        # this one" could not be done without changing all of them.
        if _is_null_entry(const, entry):
            continue
        if desired == "":
            # Nothing of its own yet: seed the new block with the placeholder's
            # words, so the screen does not change and only the storage does.
            desired = on_disk.get(DUMMY) or ""
            if desired == "":
                # No placeholder text to copy either. Allocating an empty block
                # would be inventing content; leave it alone and say nothing.
                continue
        shared_edits.setdefault(sym or DUMMY, []).append((const, desired))

    for sym, rows in shared_edits.items():
        for const, desired in rows:
            if sym != DUMMY and sym not in pointed_at and sym not in defined_anywhere:
                # A pointer to something that does not exist anywhere. Allocating
                # would paper over a broken engine file rather than report it.
                plan.conflicts.append(
                    f"{const} points at {sym}, which no description block "
                    "defines, so its text was not written."
                )
                continue
            new_sym = allocate_symbol(const, taken)
            plan.allocations[const] = new_sym
            plan.new_blocks[new_sym] = desired

    return plan


# ---------------------------------------------------------------------------
# Rendering and application
# ---------------------------------------------------------------------------

def _escape(line: str) -> str:
    return line.replace("\\", "\\\\").replace('"', '\\"')


def render_block(symbol: str, text: str) -> str:
    """A block in the engine's own formatting, with no trailing newline."""
    lines = text.split("\n")
    parts = []
    for i, line in enumerate(lines):
        suffix = "\\n" if i < len(lines) - 1 else ""
        parts.append(f'    "{_escape(line)}{suffix}"')
    return f"const u8 {symbol}[] = _(\n" + "\n".join(parts) + ");"


def replace_block(text: str, symbol: str, new_text: str) -> str:
    """Replace *symbol*'s block in place. Raises if it is not there."""
    if symbol == DUMMY:
        raise DescriptionError(
            "Refusing to rewrite the shared placeholder description. "
            "No engine file was modified."
        )
    for m in _BLOCK_RE.finditer(text):
        if m.group(1) == symbol:
            return text[:m.start()] + render_block(symbol, new_text) + text[m.end():]
    raise DescriptionError(
        f"The description block {symbol} could not be found, so nothing was "
        "changed. No engine file was modified."
    )


def append_blocks(text: str, blocks: dict[str, str]) -> str:
    """Add new blocks at the end of a description file."""
    if not blocks:
        return text
    out = text
    if out and not out.endswith("\n"):
        out += "\n"
    for symbol, body in blocks.items():
        out += "\n" + render_block(symbol, body) + "\n"
    return out


def repoint_entry(entries_text: str, const: str, symbol: str) -> str:
    """Point *const*'s ``.description`` at *symbol*. Raises if it cannot."""
    block = re.compile(
        r"(\[" + re.escape(const) + r"\]\s*=\s*\{.*?\n\s*\},)", re.S
    )
    m = block.search(entries_text)
    if not m:
        raise DescriptionError(
            f"Could not find {const} in pokedex_entries.h, so its description "
            "pointer was not changed. No engine file was modified."
        )
    body = m.group(1)
    new_body, n = re.subn(
        r"(\.description\s*=\s*)g\w+(\s*,)",
        lambda mm: mm.group(1) + symbol + mm.group(2),
        body,
        count=1,
    )
    if n != 1:
        raise DescriptionError(
            f"{const} has no .description line to update, so nothing was "
            "changed. No engine file was modified."
        )
    return entries_text[:m.start()] + new_body + entries_text[m.end():]


def apply(
    plan: DescriptionPlan,
    entries_text: str,
    text_files: dict[str, str],
    primary_key: str,
):
    """Return the new content of every file, or raise having changed nothing.

    *text_files* maps a caller's own key (a path, usually) to that file's
    current content; the same keys come back. Nothing is written here -- the
    caller does that, and can therefore write all of them or none.

    *primary_key* names the file the editor's text belongs to. The two halves
    of this are deliberately different:

    * An EDIT is written only to the primary file. The other files hold the
      same creatures' descriptions for a different build of the game -- in
      this project 256 of them differ -- and copying FireRed's words over
      LeafGreen's would destroy data the user never touched.
    * A NEW BLOCK goes into every file. The pointer in pokedex_entries.h is
      shared by both builds, so a symbol defined in only one of them makes
      the other fail to compile.
    """
    if plan.is_empty:
        return entries_text, dict(text_files)
    if primary_key not in text_files:
        raise DescriptionError(
            f"The description file {primary_key} was not supplied, so nothing "
            "could be written. No engine file was modified."
        )

    new_entries = entries_text
    for const, symbol in plan.allocations.items():
        new_entries = repoint_entry(new_entries, const, symbol)

    new_texts: dict[str, str] = {}
    for key, content in text_files.items():
        updated = content
        present = parse_blocks(content)
        if key == primary_key:
            for symbol, body in plan.rewrites.items():
                if symbol in present:
                    updated = replace_block(updated, symbol, body)
        missing = {s: b for s, b in plan.new_blocks.items() if s not in present}
        updated = append_blocks(updated, missing)
        new_texts[key] = updated

    return new_entries, new_texts


@dataclass
class WriteResult:
    """What a save actually did, for the caller to report."""

    touched: int = 0
    conflicts: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    #: set when the write failed; the message already says what survived
    failure: str | None = None
    #: files that could NOT be put back after a failure -- the bad case
    unrestored: list[str] = field(default_factory=list)


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8", newline="") as fh:
        return fh.read()


def _write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def write_descriptions(
    natdex, entries_path: str, primary_path: str, other_paths=()
) -> WriteResult:
    """Plan, verify and persist descriptions. The whole operation, one call.

    The app and the end-to-end harness both come through here, so what is
    tested is what ships. Returns a :class:`WriteResult` rather than raising,
    because "nothing was written and here is why" is an outcome the caller has
    to report, not an exception it should swallow.
    """
    paths = [primary_path] + [p for p in other_paths if os.path.isfile(p)]
    try:
        originals = {p: _read(p) for p in [entries_path] + paths}
    except OSError as exc:
        return WriteResult(failure=(
            f"The Pokedex description files could not be read ({exc}), so "
            "nothing was written. No engine file was modified."))

    text_files = {p: originals[p] for p in paths}
    try:
        plan = plan_descriptions(
            natdex, originals[entries_path], originals[primary_path],
            *[originals[p] for p in paths[1:]],
        )
    except DescriptionError as exc:
        return WriteResult(failure=str(exc))

    if plan.is_empty:
        return WriteResult(conflicts=plan.conflicts)

    try:
        new_entries, new_texts = apply(
            plan, originals[entries_path], text_files, primary_path)
        verify(plan, new_entries, new_texts, primary_path)
    except DescriptionError as exc:
        return WriteResult(conflicts=plan.conflicts, failure=str(exc))

    pending = {entries_path: new_entries}
    pending.update(new_texts)
    written: list[str] = []
    try:
        for path, content in pending.items():
            if content != originals[path]:
                _write(path, content)
                written.append(path)
    except OSError as exc:
        # All-or-nothing at the FILE level. Not crash-atomic: a power cut
        # between two of these writes leaves the engine half-updated, and the
        # surrounding backup machinery is what covers that.
        unrestored = []
        for path in written:
            try:
                _write(path, originals[path])
            except OSError:
                unrestored.append(path)
        msg = f"Pokedex descriptions could not be written ({exc})."
        if unrestored:
            msg += (" These files could NOT be put back and may be "
                    "half-written: " + ", ".join(unrestored) + ".")
        else:
            msg += (" Every file was put back as it was, and your text is "
                    "still in the editor, so saving again will retry it.")
        return WriteResult(conflicts=plan.conflicts, failure=msg,
                           unrestored=unrestored)

    return WriteResult(touched=plan.touched, conflicts=plan.conflicts,
                       changed_files=written)


def verify(
    plan: DescriptionPlan,
    entries_text: str,
    text_files: dict[str, str],
    primary_key: str,
) -> None:
    """Check the produced text really says what the plan intended.

    Cheap, and it catches the failure that matters: a pointer written without
    its block, or a block without its pointer.
    """
    pointers = {}
    for m in re.finditer(r"\[(NATIONAL_DEX_\w+)\]\s*=\s*\{(.*?)\n\s*\},",
                         entries_text, re.S):
        p = re.search(r"\.description\s*=\s*(g\w+)\s*,", m.group(2))
        if p:
            pointers[m.group(1)] = p.group(1)

    for const, symbol in plan.allocations.items():
        if pointers.get(const) != symbol:
            raise DescriptionError(
                f"{const} should now point at {symbol} but does not. "
                "No engine file was modified."
            )

    for key, content in text_files.items():
        blocks = parse_blocks(content)
        for symbol, body in plan.new_blocks.items():
            if symbol not in blocks:
                raise DescriptionError(
                    f"The description block {symbol} is missing from {key}. "
                    "No engine file was modified."
                )
            if blocks[symbol] != body:
                raise DescriptionError(
                    f"The description written for {symbol} in {key} does not "
                    "match what was asked for. No engine file was modified."
                )
        if key == primary_key:
            for symbol, body in plan.rewrites.items():
                if symbol in blocks and blocks[symbol] != body:
                    raise DescriptionError(
                        f"The description rewritten for {symbol} in {key} does "
                        "not match what was asked for. No engine file was "
                        "modified."
                    )
        if DUMMY in blocks and DUMMY in plan.rewrites:
            raise DescriptionError(
                "The shared placeholder would have been rewritten. "
                "No engine file was modified."
            )
