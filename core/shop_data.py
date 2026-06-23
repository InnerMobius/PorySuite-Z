"""
core/shop_data.py
Dynamic shop scanner / editor for pokefirered-based projects.

A "shop" in pokefirered is opened by a script command — ``pokemart`` (regular
mart), ``pokemartdecoration`` or ``pokemartdecoration2`` (decoration mart) — each
of which takes a pointer to an item list.  That item list is an assembly label
followed by a block of ``.2byte ITEM_*`` (or ``.2byte DECOR_*``) entries, ending
in an ``ITEM_NONE`` (resp. ``DECOR_NONE``) terminator:

    .align 2
    ViridianCity_Mart_Items::
        .2byte ITEM_POKE_BALL
        .2byte ITEM_POTION
        .2byte ITEM_NONE

This module scans the project source EVERY time (no hardcoded shop list), so
projects that add or remove shops load correctly.  It tolerates any shop count,
missing files, inline comments, and shops whose item-list label lives in a
different file than the ``pokemart`` call that points at it.

Public API
----------
    load_shops(project_root) -> list[Shop]
    save_shop(project_root, shop)            # write one shop back in place
    save_shops(project_root, shops)          # write several
    load_item_catalog(project_root) -> list[str]
    load_decor_catalog(project_root) -> list[str]

Design notes
------------
* Each Shop is keyed by its item-list LABEL (e.g. ``ViridianCity_Mart_Items``),
  which is stable and unique across the project — that is the identifier the
  ``pokemart`` command points at.
* A single map file may define several shops (Celadon Dept. Store, Two Island,
  etc.).  Each item-list label is a separate Shop.
* Writing rewrites ONLY the ``.2byte`` block for the target label, preserving
  the ``.align``, the label line, the terminator line, and everything else in
  the file.  Other shops in the same file are never touched or reordered.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Optional


# ── Constants ───────────────────────────────────────────────────────────────

KIND_MART = "mart"
KIND_DECOR = "decor"

# Script commands that open a shop, mapped to the kind of list they take.
_MART_COMMANDS = {
    "pokemart": KIND_MART,
    "pokemartdecoration": KIND_DECOR,
    "pokemartdecoration2": KIND_DECOR,
    # Some forks rename / add variants; tolerate the common aliases too.
    "pokemartdecor": KIND_DECOR,
    "pokemartbp": KIND_MART,
}

# The terminator constant per kind (the entry the engine stops reading at).
_TERMINATOR = {
    KIND_MART: "ITEM_NONE",
    KIND_DECOR: "DECOR_NONE",
}

# Match an assembly label definition:  ``SomeLabel::``  (also accepts a single
# colon, which the assembler also allows for local labels).
_LABEL_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*::?\s*(?:@.*)?$")

# Match a ``.2byte VALUE`` line (one value per line is the pokefirered norm).
# Captures the raw value token so we can keep ITEM_* / DECOR_* / numeric forms.
_TWOBYTE_RE = re.compile(r"^\s*\.2byte\s+([^@\n]+?)\s*(?:@.*)?$")

# Match a ``.align N`` directive.
_ALIGN_RE = re.compile(r"^\s*\.align\b")

# Match a ``#define NAME value`` constant (items.h / decorations.h).
_DEFINE_RE = re.compile(r"^\s*#define\s+([A-Za-z_][A-Za-z0-9_]*)\s+(.+?)\s*$")


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class Shop:
    """One shop = one item-list label and the constants it sells.

    Attributes
    ----------
    label
        The item-list assembly label (stable identifier), e.g.
        ``ViridianCity_Mart_Items``.
    items
        Ordered list of constant strings (``ITEM_*`` / ``DECOR_*`` /
        numeric), WITHOUT the trailing terminator.
    kind
        ``"mart"`` or ``"decor"``.
    file
        Absolute path to the source file that defines the label.
    context
        Human-readable description (map / script name + nearby comment).
    map_name
        The map folder name if the file is under ``data/maps/<Map>/``, else "".
    line
        1-based line number of the label definition (for stable sorting).
    referenced
        True if at least one ``pokemart*`` command points at this label.
        Lists that exist but are never referenced still load (resilient),
        flagged so the UI can show them.
    """

    label: str
    items: list[str] = field(default_factory=list)
    kind: str = KIND_MART
    file: str = ""
    context: str = ""
    map_name: str = ""
    line: int = 0
    referenced: bool = True
    # Where the `pokemart <label>` call that opens this shop lives (for the
    # "Open in EVENTide" jump). Empty when the shop is not wired to any script.
    ref_file: str = ""
    ref_line: int = 0
    ref_map: str = ""


# ── Internal: file discovery ─────────────────────────────────────────────────

def _candidate_files(project_root: str) -> list[str]:
    """Every file that may contain a shop item list or a ``pokemart`` call.

    Covers the standard pokefirered locations:
      * ``data/maps/<Map>/scripts.inc``  (most shops)
      * ``data/scripts/**/*.inc``        (shared / misc scripts)
      * ``src/data/**/*.inc`` and ``.s``  (some forks store lists here)
    Missing directories are skipped silently.
    """
    roots = [
        os.path.join(project_root, "data", "maps"),
        os.path.join(project_root, "data", "scripts"),
        os.path.join(project_root, "src", "data"),
    ]
    out: list[str] = []
    seen: set[str] = set()
    for base in roots:
        if not os.path.isdir(base):
            continue
        for dirpath, _dirs, names in os.walk(base):
            for name in names:
                if name.endswith((".inc", ".s")):
                    p = os.path.normpath(os.path.join(dirpath, name))
                    if p not in seen:
                        seen.add(p)
                        out.append(p)
    return out


def _read_lines(path: str) -> list[str]:
    """Read a file's lines, tolerating encoding quirks. Returns [] on error."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().splitlines()
    except OSError:
        return []


def _map_name_for(path: str) -> str:
    """If *path* is ``.../data/maps/<Map>/scripts.inc`` return ``<Map>``, else ''."""
    norm = path.replace("\\", "/")
    m = re.search(r"/data/maps/([^/]+)/", norm)
    return m.group(1) if m else ""


# ── Internal: parsing one file ───────────────────────────────────────────────

@dataclass
class _RawList:
    """An item-list label + parsed entries discovered in a file."""
    label: str
    file: str
    label_line: int          # 1-based index of the label line
    align_line: int          # 1-based index of the preceding .align, or -1
    first_2byte_line: int    # 1-based index of first .2byte line
    last_2byte_line: int     # 1-based index of last .2byte line (the block end)
    entries: list[str]       # all .2byte values, terminator INCLUDED
    comment: str             # nearby comment text, if any


def _looks_like_constant(token: str) -> bool:
    """Heuristic: is this ``.2byte`` value a shop item/decor constant?

    Accepts ``ITEM_*``, ``DECOR_*``, and bare integers (some lists use 0 as the
    terminator).  Rejects obvious non-list payloads.
    """
    t = token.strip()
    if not t:
        return False
    if t.startswith(("ITEM_", "DECOR_")):
        return True
    # Bare decimal / hex terminator (rare but valid).
    return bool(re.fullmatch(r"0[xX][0-9A-Fa-f]+|\d+", t))


def _parse_lists_in_file(path: str) -> list[_RawList]:
    """Find every label whose body is a contiguous run of ``.2byte`` constants.

    A list is recognised as: an optional ``.align`` line, a label line, then one
    or more ``.2byte ITEM_*/DECOR_*`` lines.  The block ends at the first line
    that is not a ``.2byte`` constant entry (e.g. ``release`` / ``end`` / a new
    label) — pokefirered routinely places junk like ``release``/``end`` AFTER the
    terminator, which is dead code the engine never reaches, so we stop at the
    last ``.2byte`` entry.
    """
    lines = _read_lines(path)
    out: list[_RawList] = []
    n = len(lines)
    i = 0
    while i < n:
        m = _LABEL_RE.match(lines[i])
        if not m:
            i += 1
            continue
        label = m.group(1)
        label_line = i + 1

        # Look back one line for a preceding .align (and grab a comment if the
        # line above the label or the line above the align is an @ comment).
        align_line = -1
        if i - 1 >= 0 and _ALIGN_RE.match(lines[i - 1]):
            align_line = i  # 1-based index of the align line == i (line i-1 -> i)
        comment = _nearby_comment(lines, i)

        # Walk forward collecting contiguous .2byte constant entries.
        j = i + 1
        entries: list[str] = []
        first_2b = -1
        last_2b = -1
        while j < n:
            raw = lines[j]
            tb = _TWOBYTE_RE.match(raw)
            if tb:
                val = tb.group(1).strip()
                if not _looks_like_constant(val):
                    # A .2byte that isn't an item/decor constant -> not a shop
                    # list (e.g. a pointer table). Abort this label.
                    entries = []
                    break
                if first_2b < 0:
                    first_2b = j + 1
                last_2b = j + 1
                entries.append(val)
                j += 1
                continue
            # Allow blank lines / comment-only lines WITHIN the block only
            # before the first entry (rare); once entries started, any
            # non-.2byte line ends the block.
            if not entries and (raw.strip() == "" or raw.lstrip().startswith("@")):
                j += 1
                continue
            break

        if entries:
            out.append(_RawList(
                label=label,
                file=path,
                label_line=label_line,
                align_line=align_line,
                first_2byte_line=first_2b,
                last_2byte_line=last_2b,
                entries=entries,
                comment=comment,
            ))
            i = j
        else:
            i += 1
    return out


def _nearby_comment(lines: list[str], label_idx: int) -> str:
    """Pull a human comment near the label (the line above, or above .align)."""
    # Line directly above the label.
    for probe in (label_idx - 1, label_idx - 2):
        if probe < 0:
            break
        s = lines[probe].strip()
        if s.startswith("@"):
            return s.lstrip("@ ").strip()
        if _ALIGN_RE.match(lines[probe]):
            continue  # skip the .align, look one higher
        break
    return ""


# ── Internal: finding pokemart references ────────────────────────────────────

def _scan_references(files: list[str]) -> dict[str, tuple[str, str, int]]:
    """Return ``{label: (kind, file, line)}`` for every ``pokemart*`` call site.

    If the same label is referenced by several commands the first wins for the
    kind (they should agree anyway).
    """
    refs: dict[str, tuple[str, str, int]] = {}
    # Build a combined regex for the command names.
    cmd_alt = "|".join(re.escape(c) for c in _MART_COMMANDS)
    call_re = re.compile(
        r"^\s*(" + cmd_alt + r")\s+([A-Za-z_][A-Za-z0-9_]*)\b"
    )
    for path in files:
        for idx, raw in enumerate(_read_lines(path)):
            m = call_re.match(raw)
            if not m:
                continue
            cmd, label = m.group(1), m.group(2)
            if label not in refs:
                refs[label] = (_MART_COMMANDS[cmd], path, idx + 1)
    return refs


# ── Public: catalogs ──────────────────────────────────────────────────────────

def _parse_define_header(path: str) -> list[str]:
    """Parse ``#define NAME value`` constants from a C header, in file order."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in _read_lines(path):
        m = _DEFINE_RE.match(raw)
        if not m:
            continue
        name = m.group(1)
        if name in seen:
            continue
        # Skip header-guard / length / count helper macros that aren't real
        # selectable constants.
        if name.endswith(("_COUNT", "_LENGTH")) or name.startswith("GUARD_"):
            continue
        seen.add(name)
        out.append(name)
    return out


def load_item_catalog(project_root: str) -> list[str]:
    """All ``ITEM_*`` constants from ``include/constants/items.h`` (file order).

    Project-specific — never hardcoded. Returns [] if the header is missing.
    """
    path = os.path.join(project_root, "include", "constants", "items.h")
    names = [n for n in _parse_define_header(path) if n.startswith("ITEM_")]
    return names


def load_decor_catalog(project_root: str) -> list[str]:
    """All ``DECOR_*`` constants from ``include/constants/decorations.h``."""
    path = os.path.join(project_root, "include", "constants", "decorations.h")
    names = [n for n in _parse_define_header(path) if n.startswith("DECOR_")]
    return names


# ── Public: load ─────────────────────────────────────────────────────────────

def load_shops(project_root: str) -> list[Shop]:
    """Scan *project_root* and return every shop, freshly parsed from source.

    Resilient: tolerates any shop count and missing files.  Shops are returned
    sorted by map name then label for a stable, readable ordering.
    """
    if not project_root or not os.path.isdir(project_root):
        return []

    files = _candidate_files(project_root)
    refs = _scan_references(files)

    # Parse every candidate list across all files once, keyed by label.
    raw_by_label: dict[str, _RawList] = {}
    for path in files:
        for rl in _parse_lists_in_file(path):
            # First definition of a label wins (labels are globally unique in a
            # well-formed project; this just guards against accidental dupes).
            raw_by_label.setdefault(rl.label, rl)

    shops: list[Shop] = []
    referenced_labels: set[str] = set()
    for label, (kind, ref_file, ref_line) in refs.items():
        rl = raw_by_label.get(label)
        if rl is None:
            # Referenced label whose list we couldn't parse (cross-file label
            # missing, or non-standard layout). Skip rather than crash.
            continue
        items, _term = _split_terminator(rl.entries, kind)
        shops.append(_make_shop(rl, kind, items, referenced=True,
                                ref_file=ref_file, ref_line=ref_line))
        referenced_labels.add(label)

    # Surface UNREFERENCED item lists too — newly-created (not-yet-wired) shops,
    # plus any shop whose `pokemart` call the scan didn't catch. Only lists that
    # end in the named terminator (ITEM_NONE / DECOR_NONE) qualify, so arbitrary
    # `.2byte` arrays aren't mistaken for shops. Flagged referenced=False so the
    # UI marks them "not wired".
    for label, rl in raw_by_label.items():
        if label in referenced_labels:
            continue
        kind = _infer_kind(rl.entries)
        if kind is None:
            continue
        items, _term = _split_terminator(rl.entries, kind)
        shops.append(_make_shop(rl, kind, items, referenced=False))

    return sorted(shops, key=lambda s: (s.map_name.lower(), s.label.lower()))


def _split_terminator(entries: list[str], kind: str) -> tuple[list[str], str]:
    """Split parsed ``.2byte`` entries into (sellable items, terminator token).

    Stops at the first terminator (``ITEM_NONE`` / ``DECOR_NONE`` / ``0``).
    Everything before it is the shop's stock; the terminator is returned so the
    writer can reuse the exact token (in case a fork uses ``0`` instead of the
    named constant).
    """
    term_const = _TERMINATOR.get(kind, "ITEM_NONE")
    items: list[str] = []
    for e in entries:
        if e == term_const or e == "0" or e == "ITEM_NONE" or e == "DECOR_NONE":
            return items, e
        items.append(e)
    # No explicit terminator found — return all entries, default terminator.
    return items, term_const


def _make_shop(rl: _RawList, kind: str, items: list[str], referenced: bool,
               ref_file: str = "", ref_line: int = 0) -> Shop:
    map_name = _map_name_for(rl.file)
    if map_name:
        ctx = map_name.replace("_", " ")
    else:
        ctx = os.path.basename(rl.file)
    if rl.comment:
        ctx = f"{ctx} — {rl.comment}"
    if not referenced:
        ctx = f"{ctx}  (not wired)"
    return Shop(
        label=rl.label,
        items=list(items),
        kind=kind,
        file=rl.file,
        context=ctx,
        map_name=map_name,
        line=rl.label_line,
        referenced=referenced,
        ref_file=ref_file,
        ref_line=ref_line,
        ref_map=_map_name_for(ref_file) if ref_file else "",
    )


def _infer_kind(entries: list[str]) -> Optional[str]:
    """Infer an item list's kind from its terminator, or None if it isn't a
    terminator-ended item/decor list (so arbitrary `.2byte` arrays aren't
    mistaken for shops). The first named terminator found decides the kind."""
    for e in entries:
        if e == "DECOR_NONE":
            return KIND_DECOR
        if e == "ITEM_NONE":
            return KIND_MART
    return None


# ── Public: save ─────────────────────────────────────────────────────────────

def save_shop(project_root: str, shop: Shop) -> None:
    """Write *shop*'s item list back into its source file, in place.

    Rewrites ONLY the ``.2byte`` block for ``shop.label`` (between the label
    line and the existing terminator), preserving the ``.align``, the label
    line, the terminator, and every other shop in the file.  The terminator
    (``ITEM_NONE`` / ``DECOR_NONE``) is always kept.

    Raises ``FileNotFoundError`` / ``ValueError`` if the label can't be located.
    """
    path = shop.file
    if not path or not os.path.isfile(path):
        raise FileNotFoundError(f"Shop source file not found: {path!r}")

    lines = _read_lines(path)

    # Re-locate the label and its current .2byte block fresh from disk (file may
    # have changed since load; we never trust stale line numbers for writing).
    rl = _find_list_for_label(lines, path, shop.label)
    if rl is None:
        raise ValueError(
            f"Item-list label {shop.label!r} not found in {path!r}"
        )

    # Preserve the indentation style used by the file's existing .2byte lines.
    indent = _detect_indent(lines, rl.first_2byte_line - 1)

    term_const = _TERMINATOR.get(shop.kind, "ITEM_NONE")
    # Reuse the on-disk terminator token if it differs (e.g. literal 0).
    existing_items, existing_term = _split_terminator(rl.entries, shop.kind)
    if existing_term:
        term_const = existing_term

    new_block: list[str] = []
    for const in shop.items:
        const = const.strip()
        if not const:
            continue
        # Never let the caller smuggle a second terminator into the middle.
        if const in ("ITEM_NONE", "DECOR_NONE", "0"):
            continue
        new_block.append(f"{indent}.2byte {const}")
    new_block.append(f"{indent}.2byte {term_const}")

    # Replace the original block [first_2byte_line .. last_2byte_line] (1-based,
    # inclusive) with new_block.
    start = rl.first_2byte_line - 1   # 0-based
    end = rl.last_2byte_line          # 0-based exclusive (== last index + 1)
    new_lines = lines[:start] + new_block + lines[end:]

    _write_lines(path, new_lines, original=lines)


def save_shops(project_root: str, shops: list[Shop]) -> list[str]:
    """Save several shops. Returns a list of error strings (empty = all OK)."""
    errors: list[str] = []
    for shop in shops:
        try:
            save_shop(project_root, shop)
        except Exception as e:  # noqa: BLE001 — surface per-shop, keep going
            errors.append(f"{shop.label}: {e}")
    return errors


def list_map_names(project_root: str) -> list[str]:
    """Map folders under data/maps that have a scripts.inc — the candidate
    homes for a new shop's item list (so it's compiled into the build)."""
    base = os.path.join(project_root, "data", "maps")
    out: list[str] = []
    if os.path.isdir(base):
        for name in sorted(os.listdir(base)):
            if os.path.isfile(os.path.join(base, name, "scripts.inc")):
                out.append(name)
    return out


def _label_exists(project_root: str, label: str) -> bool:
    """True if *label* is already defined as a list label anywhere we scan."""
    for path in _candidate_files(project_root):
        for rl in _parse_lists_in_file(path):
            if rl.label == label:
                return True
    return False


def create_shop(project_root: str, label: str, kind: str,
                map_name: str) -> Shop:
    """Create a new (unwired) shop item list in *map_name*'s scripts.inc.

    Writes an empty list — just the terminator — that the user then wires to an
    NPC via a ``pokemart <label>`` call in EVENTide. The list lives in the map's
    scripts.inc so it's compiled into the build. Raises ``ValueError`` on a bad
    or duplicate label, ``FileNotFoundError`` if the map has no scripts.inc.
    """
    label = (label or "").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", label):
        raise ValueError(
            f"'{label}' is not a valid label (letters, digits and underscores "
            f"only, and it can't start with a digit).")
    if _label_exists(project_root, label):
        raise ValueError(f"A list named '{label}' already exists.")
    if kind not in (KIND_MART, KIND_DECOR):
        kind = KIND_MART
    scripts = os.path.join(project_root, "data", "maps", map_name, "scripts.inc")
    if not os.path.isfile(scripts):
        raise FileNotFoundError(
            f"{map_name} has no scripts.inc to add the shop list to.")
    term = _TERMINATOR.get(kind, "ITEM_NONE")
    lines = _read_lines(scripts)
    # Append a blank separator + the new block at the end of the file.
    block = ["", "\t.align 2", f"{label}::", f"\t.2byte {term}"]
    new_lines = lines + block
    _write_lines(scripts, new_lines, original=lines)
    rl = _RawList(
        label=label, file=scripts,
        label_line=len(lines) + 3, align_line=len(lines) + 2,
        first_2byte_line=len(lines) + 4, last_2byte_line=len(lines) + 4,
        entries=[term], comment="")
    return _make_shop(rl, kind, items=[], referenced=False)


def delete_shop(project_root: str, shop: Shop) -> None:
    """Remove a shop's item-list block from its file (a directly-preceding
    ``.align``, the label, and the ``.2byte`` lines through the terminator).

    Does NOT touch any ``pokemart`` call that references it — the caller warns
    the user to unwire it in EVENTide. Raises ``FileNotFoundError`` /
    ``ValueError`` if the block can't be located.
    """
    path = shop.file
    if not path or not os.path.isfile(path):
        raise FileNotFoundError(f"Shop source file not found: {path!r}")
    lines = _read_lines(path)
    rl = _find_list_for_label(lines, path, shop.label)
    if rl is None:
        raise ValueError(f"Item-list label {shop.label!r} not found in {path!r}")
    start = rl.label_line - 1                       # 0-based label line
    if start - 1 >= 0 and _ALIGN_RE.match(lines[start - 1]):
        start -= 1                                  # also drop the list's .align
    end = rl.last_2byte_line                        # 0-based exclusive (past term)
    new_lines = lines[:start] + lines[end:]
    _write_lines(path, new_lines, original=lines)


def _find_list_for_label(lines: list[str], path: str,
                         label: str) -> Optional[_RawList]:
    """Locate the ``.2byte`` block for a specific label within *lines*."""
    for rl in _parse_lists_in_file_lines(lines, path):
        if rl.label == label:
            return rl
    return None


def _parse_lists_in_file_lines(lines: list[str], path: str) -> list[_RawList]:
    """Same as ``_parse_lists_in_file`` but on already-read *lines*."""
    out: list[_RawList] = []
    n = len(lines)
    i = 0
    while i < n:
        m = _LABEL_RE.match(lines[i])
        if not m:
            i += 1
            continue
        label = m.group(1)
        label_line = i + 1
        align_line = i if (i - 1 >= 0 and _ALIGN_RE.match(lines[i - 1])) else -1
        comment = _nearby_comment(lines, i)
        j = i + 1
        entries: list[str] = []
        first_2b = -1
        last_2b = -1
        while j < n:
            tb = _TWOBYTE_RE.match(lines[j])
            if tb:
                val = tb.group(1).strip()
                if not _looks_like_constant(val):
                    entries = []
                    break
                if first_2b < 0:
                    first_2b = j + 1
                last_2b = j + 1
                entries.append(val)
                j += 1
                continue
            if not entries and (lines[j].strip() == ""
                                or lines[j].lstrip().startswith("@")):
                j += 1
                continue
            break
        if entries:
            out.append(_RawList(
                label=label, file=path, label_line=label_line,
                align_line=align_line, first_2byte_line=first_2b,
                last_2byte_line=last_2b, entries=entries, comment=comment,
            ))
            i = j
        else:
            i += 1
    return out


def _detect_indent(lines: list[str], idx: int) -> str:
    """Return the leading whitespace of the ``.2byte`` line at *idx*."""
    if 0 <= idx < len(lines):
        raw = lines[idx]
        stripped = raw.lstrip()
        if stripped:
            return raw[: len(raw) - len(stripped)]
    return "\t"


def _write_lines(path: str, new_lines: list[str], original: list[str]) -> None:
    """Write lines back, preserving the file's newline + trailing-newline style."""
    # Detect newline style from the raw bytes (default to the OS-agnostic '\n').
    newline = "\n"
    try:
        with open(path, "rb") as f:
            head = f.read(65536)
        if b"\r\n" in head:
            newline = "\r\n"
    except OSError:
        pass

    # Preserve a trailing newline if the original had one.
    text = newline.join(new_lines)
    trailing = _had_trailing_newline(path)
    if trailing:
        text += newline

    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def _had_trailing_newline(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            if f.tell() == 0:
                return False
            f.seek(-1, os.SEEK_END)
            return f.read(1) in (b"\n", b"\r")
    except OSError:
        return True
