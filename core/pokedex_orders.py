"""Regenerate the Pokédex search sort arrays in ``pokedex_orders.h``.

The game's dex search screen sorts results using three pre-sorted index
arrays in ``src/data/pokemon/pokedex_orders.h``:

* ``gPokedexOrder_Alphabetical`` — national dex constants ordered by the
  species' in-game display name,
* ``gPokedexOrder_Weight`` — ordered by the dex entry's ``.weight``,
* ``gPokedexOrder_Height`` — ordered by the dex entry's ``.height``.

These arrays are DERIVED data over values the editor changes (display
names, dex heights/weights), but nothing in the engine rebuilds them —
they were generated once upstream. Every rename or size edit that is
saved without rebuilding them leaves the in-game search subtly
mis-sorted. This module re-derives all three from the project's current
data; the save paths call :func:`regenerate_pokedex_orders` after they
have written names / dex entries.

Sort semantics (matching the vanilla generator's intent):

* alphabetical: case-insensitive by display name,
* weight / height: ascending numeric,
* ties keep relative national-dex order (stable sort).

Membership rules, derived from the vanilla file:

* the alphabetical array holds EVERY constant of the national dex enum
  except ``NATIONAL_DEX_NONE`` (vanilla includes placeholder species that
  have a name but no dex entry),
* the weight/height arrays hold only constants that have a dex entry
  (vanilla: ``gPokedexEntries`` members minus ``NATIONAL_DEX_NONE``).

A fourth vanilla array, ``gPokedexOrder_Type``, is NOT derived from data
this editor changes and is left untouched, as is everything else in the
file. An array whose stored order already matches the computed order is
not rewritten at all, so comments inside a correct array survive and an
unchanged project never sees a phantom diff.
"""

from __future__ import annotations

import logging
import os
import re

from core.file_io import write_text_if_changed

_log = logging.getLogger("PorySuite.PokedexOrders")

#: pokedex_orders.h, relative to the project root.
ORDERS_REL_PATH = os.path.join("src", "data", "pokemon", "pokedex_orders.h")

#: The three derived arrays this module owns, with their sort key.
ARRAY_SORT_KEYS = {
    "gPokedexOrder_Alphabetical": "name",
    "gPokedexOrder_Weight": "weight",
    "gPokedexOrder_Height": "height",
}

_NAT_DEX_RE = re.compile(r"\bNATIONAL_DEX_[A-Za-z0-9_]+\b")
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
_LINE_COMMENT_RE = re.compile(r"//[^\n]*")


def _strip_comments(text: str) -> str:
    """Remove ``/* */`` and ``//`` comments so tokens inside them are ignored."""
    return _LINE_COMMENT_RE.sub("", _BLOCK_COMMENT_RE.sub("", text))


def _read_text(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def parse_national_dex_enum(project_root: str) -> list[str]:
    """Return the national dex constants in enum (dex-number) order.

    Reads ``include/constants/pokedex.h`` and returns every constant of
    the national dex enum except ``NATIONAL_DEX_NONE``. The enum is
    identified as the first ``enum { ... };`` block whose body mentions
    ``NATIONAL_DEX_NONE``. Returns ``[]`` when the file or enum cannot
    be found.
    """
    path = os.path.join(project_root, "include", "constants", "pokedex.h")
    text = _read_text(path)
    if text is None:
        return []
    text = _strip_comments(text)
    for m in re.finditer(r"\benum\b[^{;]*\{(.*?)\};", text, re.S):
        body = m.group(1)
        if "NATIONAL_DEX_NONE" not in body:
            continue
        consts: list[str] = []
        seen: set[str] = set()
        for token in _NAT_DEX_RE.findall(body):
            if token == "NATIONAL_DEX_NONE" or token in seen:
                continue
            seen.add(token)
            consts.append(token)
        return consts
    return []


def parse_dex_entry_sizes(project_root: str) -> dict[str, dict[str, int]]:
    """Return ``{NATIONAL_DEX_X: {"height": int, "weight": int}}``.

    Parsed from ``src/data/pokemon/pokedex_entries.h``. Only constants
    with an explicit ``[NATIONAL_DEX_X] =`` entry appear; a missing or
    non-numeric field is simply absent from the inner mapping.
    """
    path = os.path.join(
        project_root, "src", "data", "pokemon", "pokedex_entries.h")
    text = _read_text(path)
    if text is None:
        return {}
    text = _strip_comments(text)
    sizes: dict[str, dict[str, int]] = {}
    # re.split keeps the captured constants at odd indices, with each
    # entry's body following its constant.
    parts = re.split(r"\[\s*(NATIONAL_DEX_[A-Za-z0-9_]+)\s*\]\s*=", text)
    for i in range(1, len(parts) - 1, 2):
        const = parts[i]
        body = parts[i + 1]
        fields: dict[str, int] = {}
        for key in ("height", "weight"):
            fm = re.search(rf"\.{key}\s*=\s*(-?\d+)", body)
            if fm:
                fields[key] = int(fm.group(1))
        sizes[const] = fields
    return sizes


def parse_display_names(project_root: str) -> dict[str, str]:
    """Return ``{SPECIES_X: display name}`` from the project's name table.

    Prefers ``src/data/text/species_names.h`` (the classic name table).
    When that file does not exist, falls back to ``.speciesName`` fields
    in ``src/data/pokemon/species_info.h`` (the layout some forks use).
    """
    names: dict[str, str] = {}
    names_path = os.path.join(
        project_root, "src", "data", "text", "species_names.h")
    text = _read_text(names_path)
    if text is not None:
        pat = re.compile(
            r"\[\s*(SPECIES_[A-Za-z0-9_]+)\s*\]\s*=\s*_\(\s*\""
            r"((?:\\.|[^\"\\])*)\"")
        for m in pat.finditer(text):
            names[m.group(1)] = _unescape_c(m.group(2))
        return names

    info_path = os.path.join(
        project_root, "src", "data", "pokemon", "species_info.h")
    text = _read_text(info_path)
    if text is None:
        return names
    current: str | None = None
    name_pat = re.compile(r"\.speciesName\s*=\s*_\(\s*\"((?:\\.|[^\"\\])*)\"")
    for line in text.splitlines():
        m = re.search(r"\[\s*(SPECIES_[A-Za-z0-9_]+)\s*\]\s*=", line)
        if m:
            current = m.group(1)
            continue
        if current is None:
            continue
        nm = name_pat.search(line)
        if nm:
            names[current] = _unescape_c(nm.group(1))
            current = None
    return names


def _unescape_c(s: str) -> str:
    return s.replace('\\"', '"').replace("\\\\", "\\")


def _display_name(const: str, names: dict[str, str]) -> str:
    """Display name for a national dex constant, for sorting.

    Maps ``NATIONAL_DEX_X`` to ``SPECIES_X``'s name. A constant with no
    name entry falls back to its own suffix so it still sorts
    deterministically and is never dropped from the array.
    """
    suffix = const[len("NATIONAL_DEX_"):]
    return names.get("SPECIES_" + suffix) or suffix


def _replace_array(text: str, array_name: str,
                   order: list[str]) -> tuple[str, bool, bool]:
    """Rewrite one array's body inside *text* to hold *order*.

    Returns ``(new_text, found, changed)``. The array is located by
    name; everything between its opening ``{`` and closing ``};`` is
    replaced with one constant per line, reusing the existing entry
    indentation. When the stored order already equals *order* the text
    is returned untouched (``changed`` False) so interior comments and
    formatting survive on already-correct files.
    """
    decl = re.search(rf"\b{re.escape(array_name)}\s*\[\s*\]\s*=", text)
    if not decl:
        return text, False, False
    open_idx = text.find("{", decl.end())
    if open_idx == -1:
        return text, False, False
    close_idx = text.find("};", open_idx)
    if close_idx == -1:
        return text, False, False

    body = text[open_idx + 1:close_idx]
    existing = _NAT_DEX_RE.findall(_strip_comments(body))
    if existing == order:
        return text, True, False

    indent = "    "
    im = re.search(r"\n([ \t]+)NATIONAL_DEX_", body)
    if im:
        indent = im.group(1)
    new_body = "\n" + "\n".join(f"{indent}{c}," for c in order) + "\n"

    # Keep the closing line's own leading whitespace.
    close_line_start = text.rfind("\n", 0, close_idx) + 1
    if close_line_start <= open_idx:
        close_line_start = close_idx
    return (text[:open_idx + 1] + new_body + text[close_line_start:],
            True, True)


def regenerate_pokedex_orders(project_root: str,
                              natdex: list[dict] | None = None) -> int:
    """Re-derive the three sort arrays in ``pokedex_orders.h``.

    *natdex*, when given, is the editor's in-memory national dex model
    (a list of dicts with ``dex_constant`` and numeric ``height`` /
    ``weight``) and takes precedence over the sizes parsed from
    ``pokedex_entries.h`` — the save pipeline calls this before/without
    a guarantee that the numeric fields have reached the header yet.
    Display names are always read from the header tables on disk.

    Returns the number of arrays found (rewritten or confirmed correct),
    ``-2`` when the project has no ``pokedex_orders.h`` (a legitimate
    skip — not every engine fork keeps the file), or ``-1`` when the
    inputs could not be parsed. Never raises.
    """
    try:
        orders_path = os.path.join(project_root, ORDERS_REL_PATH)
        if not os.path.isfile(orders_path):
            return -2

        enum_order = parse_national_dex_enum(project_root)
        if not enum_order:
            _log.warning(
                "pokedex_orders: national dex enum not found; "
                "leaving %s untouched", orders_path)
            return -1
        enum_index = {c: i for i, c in enumerate(enum_order)}

        names = parse_display_names(project_root)
        file_sizes = parse_dex_entry_sizes(project_root)

        # Numeric sizes: the in-memory model wins field by field, the
        # header is the fallback, absent values sort as 0.
        model_sizes: dict[str, dict[str, int]] = {}
        if natdex:
            for entry in natdex:
                if not isinstance(entry, dict):
                    continue
                const = entry.get("dex_constant")
                if not const:
                    continue
                fields: dict[str, int] = {}
                for key in ("height", "weight"):
                    val = entry.get(key)
                    if not isinstance(val, int):
                        try:
                            val = int(val)
                        except (TypeError, ValueError):
                            continue
                    fields[key] = val
                model_sizes[const] = fields

        # Weight/height membership: constants that have a dex entry.
        members = model_sizes if model_sizes else file_sizes
        hw_members = [c for c in enum_order if c in members]

        def size_of(const: str, key: str) -> int:
            for source in (model_sizes.get(const), file_sizes.get(const)):
                if source and key in source:
                    return source[key]
            return 0

        orders = {
            "gPokedexOrder_Alphabetical": sorted(
                enum_order,
                key=lambda c: (_display_name(c, names).casefold(),
                               enum_index[c])),
            "gPokedexOrder_Weight": sorted(
                hw_members,
                key=lambda c: (size_of(c, "weight"), enum_index[c])),
            "gPokedexOrder_Height": sorted(
                hw_members,
                key=lambda c: (size_of(c, "height"), enum_index[c])),
        }

        text = _read_text(orders_path)
        if text is None:
            return -1

        found = 0
        changed = False
        for array_name, order in orders.items():
            text, was_found, was_changed = _replace_array(
                text, array_name, order)
            if was_found:
                found += 1
            else:
                _log.warning(
                    "pokedex_orders: %s not found in %s; skipped",
                    array_name, orders_path)
            changed = changed or was_changed

        if changed:
            newline = "\r\n" if "\r\n" in text else "\n"
            if newline == "\r\n":
                # Normalise before handing to the writer, which applies
                # the newline substitution itself.
                text = text.replace("\r\n", "\n")
            write_text_if_changed(orders_path, text, newline=newline)
            _log.info(
                "pokedex_orders: regenerated %d sort array(s) in %s",
                found, orders_path)
        return found
    except Exception:
        _log.exception("pokedex_orders: regeneration failed; "
                       "the file was left as it is")
        return -1
