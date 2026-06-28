"""Parse a project's item-category enums (bag pockets + item types) from its
OWN source headers.

The Items editor's Pocket / Item-Type dropdowns must reflect the constants the
loaded project actually defines — never a hardcoded vanilla list. A project can
rename pockets (POCKET_TM_CASE vs POCKET_TM_HM), add its own (POCKET_EQUIPMENT),
or reorder the item-type enum, and the editor has to follow the source, because
the user's hack is not Kanto.

Locations are DISCOVERED, not assumed: every ``include/**/*.h`` is scanned, so a
project that keeps these constants in a non-standard file still resolves.

Returns are plain ``[(const_name, value)]`` lists in engine order; the caller
decides how to display and which form (name vs numeric) to store back.
"""
import os
import re

__all__ = [
    "parse_pockets", "parse_item_types", "parse_item_name_length",
    "find_include_headers",
]

_POCKET_DEFINE = re.compile(r"^[ \t]*#define[ \t]+(POCKET_[A-Z0-9_]+)[ \t]+(\d+)", re.M)
_NAME_LEN_DEFINE = re.compile(r"#define\s+ITEM_NAME_LENGTH\s+(\d+)")
# An (optionally named) C enum block. DOTALL so the body can span lines.
_ENUM_BLOCK = re.compile(r"enum\b[^{};]*\{(.*?)\}", re.S)
_LINE_COMMENT = re.compile(r"//[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
# Excluded sentinels that aren't real, selectable categories.
_POCKET_SKIP = ("POCKET_COUNT", "POCKETS_COUNT", "POCKET_NONE_2")


def find_include_headers(root: str) -> list[str]:
    """Every ``.h`` under ``<root>/include`` (recursively). Empty if missing."""
    inc = os.path.join(root, "include")
    if not os.path.isdir(inc):
        return []
    out = []
    for dirpath, _dirs, files in os.walk(inc):
        for fn in files:
            if fn.endswith(".h"):
                out.append(os.path.join(dirpath, fn))
    return out


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return f.read()
    except OSError:
        return ""


def parse_pockets(root: str) -> list[tuple[str, int]]:
    """Return ``[(POCKET_*, value)]`` for every pocket ``#define`` in the
    project's headers, sorted by value, de-duplicated, count-sentinels removed.
    Empty list if the project defines none (caller falls back)."""
    found: dict[str, int] = {}
    for path in find_include_headers(root):
        for name, val in _POCKET_DEFINE.findall(_read(path)):
            if name in _POCKET_SKIP or name.endswith("_COUNT"):
                continue
            found.setdefault(name, int(val))
    return sorted(found.items(), key=lambda kv: kv[1])


def parse_item_name_length(root: str, default: int = 14) -> int:
    """Return the project's ``ITEM_NAME_LENGTH`` — the size of the
    ``gItems[].name`` buffer — or *default* (vanilla 14) if not found.

    The maximum DISPLAYABLE name is one less than this: the buffer's last byte
    holds the string terminator, so a name of the full length has no room for
    it and overflows the menu at render time. Callers cap input at
    ``parse_item_name_length(root) - 1``.
    """
    for path in find_include_headers(root):
        m = _NAME_LEN_DEFINE.search(_read(path))
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                pass
    return default


def _strip_comments(text: str) -> str:
    return _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", text))


def parse_item_types(root: str) -> list[tuple[str, int]]:
    """Return ``[(ITEM_TYPE_*, index)]`` from the enum that declares the
    ``ITEM_TYPE_*`` members, in declaration order (honouring explicit ``= N``).
    Empty list if not found (caller falls back)."""
    for path in find_include_headers(root):
        text = _read(path)
        if "ITEM_TYPE_" not in text:
            continue
        for body in _ENUM_BLOCK.findall(text):
            if "ITEM_TYPE_" not in body:
                continue
            members: list[tuple[str, int]] = []
            idx = 0
            for raw in _strip_comments(body).split(","):
                tok = raw.strip()
                if not tok:
                    continue
                m = re.match(r"(ITEM_TYPE_[A-Z0-9_]+)\s*(?:=\s*(\d+))?$", tok)
                if not m:
                    # An unrelated member in the same enum — still advances index.
                    eq = re.search(r"=\s*(\d+)", tok)
                    if eq:
                        idx = int(eq.group(1)) + 1
                    else:
                        idx += 1
                    continue
                if m.group(2) is not None:
                    idx = int(m.group(2))
                members.append((m.group(1), idx))
                idx += 1
            if members:
                return members
    return []
