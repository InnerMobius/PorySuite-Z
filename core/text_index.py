"""
core/text_index.py
Unified text index — parses all game-visible text sources from a pokefirered
project into a flat searchable list.  Provides save-back writers for every
format.

Parsed sources:
  - src/strings.c            (gText_*, gPCText_*, gStartMenuDesc_*, etc.)
  - src/battle_message.c     (sText_* battle strings)
  - data/text/new_game_intro.inc  (Oak speech, intro pages, name pools)
  - data/maps/*/text.inc     (NPC dialogue, signs — all maps in project)
  - data/scripts/*.inc       (common shared scripts — 38 files)
  - src/data/text/*.h        (trainer class names, nature names, etc.)
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Optional


# ── TextEntry — one indexed string ────────────────────────────────────────────

@dataclass
class TextEntry:
    label: str                    # C/ASM label (e.g. "gText_NewGame")
    content: str                  # current in-RAM text value
    original: str                 # value at load time (for dirty detection)
    file_path: str                # absolute path to source file
    file_rel: str                 # path relative to project root
    line_number: int              # approx line in source file
    category: str                 # tree category key (e.g. "game_ui.start_menu")
    subcategory: str              # tree subcategory (e.g. "Start Menu")
    char_limit: int               # max chars per line
    max_lines: int                # max display lines
    is_multiline: bool            # True for dialogue, False for single-line labels
    format: str                   # "c_string" | "asm_string" | "name_label"
    owning_tab: str               # "" = this tab owns it, else "items", "moves", etc.
    display_label: str            # human-friendly label for the tree

    @property
    def is_dirty(self) -> bool:
        return self.content != self.original

    def mark_clean(self) -> None:
        self.original = self.content


# ── Character limit table ─────────────────────────────────────────────────────

# category prefix → (chars_per_line, max_lines)
_CHAR_LIMITS: dict[str, tuple[int, int]] = {
    "game_ui.start_menu":       (12, 1),
    "game_ui.start_menu_desc":  (36, 2),
    "game_ui.pc":               (36, 6),
    "game_ui.bag":              (18, 1),
    "game_ui.battle_ui":        (12, 1),
    "game_ui.gender":           (7, 1),
    "game_ui.pokedex_ui":       (36, 1),
    "game_ui.misc":             (36, 2),
    "new_game.intro":           (36, 20),
    "new_game.oak":             (36, 20),
    "new_game.names":           (7, 1),
    "location_names":           (16, 1),
    "map_dialogue":             (36, 200),
    "common_scripts":           (36, 200),
    "battle_messages":          (36, 4),
    "teachy_tv":                (36, 20),
    "fame_checker":             (36, 20),
    "quest_log":                (36, 4),
    "trainer_class":            (12, 1),
    "nature_names":             (10, 1),
}

DEFAULT_LIMIT = (36, 20)


def _get_limits(category: str) -> tuple[int, int]:
    """Return (chars_per_line, max_lines) for a category key."""
    # Try exact match first, then prefix match
    if category in _CHAR_LIMITS:
        return _CHAR_LIMITS[category]
    prefix = category.split(".")[0] if "." in category else category
    if prefix in _CHAR_LIMITS:
        return _CHAR_LIMITS[prefix]
    return DEFAULT_LIMIT


# ── File I/O helpers ──────────────────────────────────────────────────────────

def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="surrogateescape") as fh:
            return fh.read()
    except OSError:
        return ""


def _write(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8", errors="surrogateescape",
              newline="\n") as fh:
        fh.write(content)


# ── strings.c parser ──────────────────────────────────────────────────────────

_C_STRING_RE = re.compile(
    r'^(?:ALIGNED\(\d+\)\s+)?'           # optional ALIGNED(4) prefix
    r'(?:static\s+)?const\s+u8\s+'       # const u8
    r'(\w+)\s*\[\s*\]\s*=\s*'            # variable_name[]  =
    r'_\(\s*"((?:[^"\\]|\\.)*)"\s*\)\s*;',  # _("value");
    re.MULTILINE
)

# Categorize strings.c entries by prefix
_STRINGS_C_CATEGORIES: list[tuple[str, str, str]] = [
    # (prefix, category, subcategory)
    ("gText_NewGame",       "game_ui.start_menu", "Start Menu"),
    ("gText_Continue",      "game_ui.start_menu", "Start Menu"),
    ("gText_MenuPokemon",   "game_ui.start_menu", "Start Menu"),
    ("gText_Boy",           "game_ui.gender",     "Gender & Naming"),
    ("gText_Girl",          "game_ui.gender",     "Gender & Naming"),
    ("gText_EggNickname",   "game_ui.gender",     "Gender & Naming"),
    ("gText_Kanto",         "game_ui.pokedex_ui", "Pokédex UI"),
    ("gText_National",      "game_ui.pokedex_ui", "Pokédex UI"),
    ("gText_Controls",      "game_ui.misc",       "Miscellaneous"),
    ("gPCText_",            "game_ui.pc",         "PC Interface"),
    ("gStartMenuDesc_",     "game_ui.start_menu_desc", "Start Menu"),
]

# Prefixes owned by other tabs — search-only, no editing here
_OWNED_BY_OTHER: dict[str, str] = {
    "gCreditsString_": "credits",
    # Items, moves, abilities, species are in .json / data headers — not in strings.c
}


def _categorize_strings_c(var_name: str) -> tuple[str, str, str]:
    """Return (category, subcategory, owning_tab) for a strings.c variable."""
    # Check exact match first
    for prefix, cat, subcat in _STRINGS_C_CATEGORIES:
        if var_name == prefix or var_name.startswith(prefix):
            return cat, subcat, ""
    # Check owned-by-other prefixes
    for prefix, tab in _OWNED_BY_OTHER.items():
        if var_name.startswith(prefix):
            return "game_ui.misc", "Miscellaneous", tab
    # Default: misc
    return "game_ui.misc", "Miscellaneous", ""


def _parse_strings_c(project_dir: str) -> list[TextEntry]:
    """Parse all gText_* etc. from src/strings.c."""
    rel = "src/strings.c"
    path = os.path.join(project_dir, rel)
    text = _read(path)
    if not text:
        return []

    entries: list[TextEntry] = []
    for m in _C_STRING_RE.finditer(text):
        var_name = m.group(1)
        value = m.group(2)
        line = text[:m.start()].count("\n") + 1
        cat, subcat, owner = _categorize_strings_c(var_name)
        char_lim, max_lines = _get_limits(cat)

        # Make a friendly display label
        display = var_name
        for prefix in ("gText_", "gPCText_", "gStartMenuDesc_",
                        "gOtherText_", "gExpandedPlaceholder_"):
            if var_name.startswith(prefix):
                display = var_name[len(prefix):]
                # CamelCase to spaces
                display = re.sub(r"([a-z])([A-Z])", r"\1 \2", display)
                display = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", display)
                break

        entries.append(TextEntry(
            label=var_name,
            content=value,
            original=value,
            file_path=path,
            file_rel=rel,
            line_number=line,
            category=cat,
            subcategory=subcat,
            char_limit=char_lim,
            max_lines=max_lines,
            is_multiline="\\" in value and any(
                c in value for c in ("\\n", "\\p", "\\l")
            ),
            format="c_string",
            owning_tab=owner,
            display_label=display,
        ))
    return entries


# ── text.inc parser (map dialogue) ────────────────────────────────────────────

_ASM_LABEL_RE = re.compile(
    r"^(\w+)::\s*\n((?:[ \t]+\.string\s+\"[^\"]*\"[ \t]*\n?)+)",
    re.MULTILINE,
)


def _parse_text_inc(project_dir: str, rel_path: str,
                    category: str, subcategory: str) -> list[TextEntry]:
    """Parse all labelled .string blocks from an .inc file."""
    path = os.path.join(project_dir, rel_path)
    text = _read(path)
    if not text:
        return []

    entries: list[TextEntry] = []
    char_lim, max_lines = _get_limits(category)

    for m in _ASM_LABEL_RE.finditer(text):
        label = m.group(1)
        block = m.group(2)
        parts = re.findall(r'\.string\s+"([^"]*)"', block)
        value = "".join(parts).rstrip("$")
        line = text[:m.start()].count("\n") + 1

        # Display label: strip map prefix if present
        display = label
        if "_Text_" in label:
            display = label.split("_Text_", 1)[1]
            display = re.sub(r"([a-z])([A-Z])", r"\1 \2", display)

        entries.append(TextEntry(
            label=label,
            content=value,
            original=value,
            file_path=path,
            file_rel=rel_path,
            line_number=line,
            category=category,
            subcategory=subcategory,
            char_limit=char_lim,
            max_lines=max_lines,
            is_multiline=True,
            format="asm_string",
            owning_tab="",
            display_label=display,
        ))
    return entries


# ── new_game_intro.inc parser ─────────────────────────────────────────────────

def _parse_new_game_intro(project_dir: str) -> list[TextEntry]:
    """Parse Oak speech + intro pages from data/text/new_game_intro.inc."""
    rel = "data/text/new_game_intro.inc"
    path = os.path.join(project_dir, rel)
    text = _read(path)
    if not text:
        return []

    entries: list[TextEntry] = []

    for m in _ASM_LABEL_RE.finditer(text):
        label = m.group(1)
        block = m.group(2)
        parts = re.findall(r'\.string\s+"([^"]*)"', block)
        value = "".join(parts).rstrip("$")
        line = text[:m.start()].count("\n") + 1

        # Categorize
        if "PikachuIntro" in label or "Intro_Text" in label:
            cat = "new_game.intro"
            subcat = "Intro Pages"
        elif "OakSpeech" in label:
            cat = "new_game.oak"
            subcat = "Professor Speech"
        elif "NameChoice" in label:
            cat = "new_game.names"
            subcat = "Name Pools"
        else:
            cat = "new_game.oak"
            subcat = "Professor Speech"

        char_lim, max_lines = _get_limits(cat)

        display = label
        for prefix in ("gPikachuIntro_Text_", "gOakSpeech_Text_", "gNameChoice_"):
            if label.startswith(prefix):
                display = label[len(prefix):]
                display = re.sub(r"([a-z])([A-Z])", r"\1 \2", display)
                break

        entries.append(TextEntry(
            label=label,
            content=value,
            original=value,
            file_path=path,
            file_rel=rel,
            line_number=line,
            category=cat,
            subcategory=subcat,
            char_limit=char_lim,
            max_lines=max_lines,
            is_multiline=cat != "new_game.names",
            format="asm_string" if cat != "new_game.names" else "name_label",
            owning_tab="",
            display_label=display,
        ))
    return entries


# ── Location names parser ─────────────────────────────────────────────────────

def _parse_location_names(project_dir: str) -> list[TextEntry]:
    """Parse region map section names."""
    json_rel = "src/data/region_map/region_map_sections.json"
    json_path = os.path.join(project_dir, json_rel)
    char_lim, max_lines = _get_limits("location_names")

    if os.path.isfile(json_path):
        text = _read(json_path)
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return []

        # Unwrap
        if isinstance(data, dict):
            for k in ("map_sections", "sections", "entries"):
                if k in data and isinstance(data[k], list):
                    data = data[k]
                    break

        entries: list[TextEntry] = []
        if isinstance(data, list):
            for i, entry in enumerate(data):
                if not isinstance(entry, dict):
                    continue
                cid = entry.get("id") or entry.get("constant") or ""
                name = entry.get("name") or entry.get("display_name") or ""
                if not cid or not name:
                    continue
                entries.append(TextEntry(
                    label=cid,
                    content=name,
                    original=name,
                    file_path=json_path,
                    file_rel=json_rel,
                    line_number=i + 1,
                    category="location_names",
                    subcategory="Location Names",
                    char_limit=char_lim,
                    max_lines=max_lines,
                    is_multiline=False,
                    format="json_region_map",
                    owning_tab="",
                    display_label=name,
                ))
        return entries

    return []


# ── battle_message.c parser ───────────────────────────────────────────────────

_BATTLE_STRING_RE = re.compile(
    r'^(?:static\s+)?const\s+u8\s+'
    r'(sText_\w+)\s*\[\s*\]\s*=\s*'
    r'_\(\s*"((?:[^"\\]|\\.)*)"\s*\)\s*;',
    re.MULTILINE,
)


def _parse_battle_messages(project_dir: str) -> list[TextEntry]:
    """Parse sText_* from src/battle_message.c."""
    rel = "src/battle_message.c"
    path = os.path.join(project_dir, rel)
    text = _read(path)
    if not text:
        return []

    entries: list[TextEntry] = []
    char_lim, max_lines = _get_limits("battle_messages")

    for m in _BATTLE_STRING_RE.finditer(text):
        var_name = m.group(1)
        value = m.group(2)
        line = text[:m.start()].count("\n") + 1

        display = var_name
        if var_name.startswith("sText_"):
            display = var_name[6:]
            display = re.sub(r"([a-z])([A-Z])", r"\1 \2", display)

        entries.append(TextEntry(
            label=var_name,
            content=value,
            original=value,
            file_path=path,
            file_rel=rel,
            line_number=line,
            category="battle_messages",
            subcategory="Battle Messages",
            char_limit=char_lim,
            max_lines=max_lines,
            is_multiline=any(c in value for c in ("\\n", "\\p", "\\l")),
            format="c_string",
            owning_tab="",
            display_label=display,
        ))
    return entries


# ── Map dialogue scanner ──────────────────────────────────────────────────────

def _parse_all_map_dialogue(project_dir: str) -> list[TextEntry]:
    """Scan data/maps/*/text.inc for all map dialogue."""
    maps_dir = os.path.join(project_dir, "data", "maps")
    if not os.path.isdir(maps_dir):
        return []

    entries: list[TextEntry] = []
    for map_name in sorted(os.listdir(maps_dir)):
        text_inc = os.path.join(maps_dir, map_name, "text.inc")
        if not os.path.isfile(text_inc):
            continue
        rel = f"data/maps/{map_name}/text.inc"
        cat = "map_dialogue"
        subcat = map_name
        entries.extend(_parse_text_inc(project_dir, rel, cat, subcat))

    return entries


# ── Common scripts scanner ────────────────────────────────────────────────────

def _parse_common_scripts(project_dir: str) -> list[TextEntry]:
    """Scan data/scripts/*.inc for text labels."""
    scripts_dir = os.path.join(project_dir, "data", "scripts")
    if not os.path.isdir(scripts_dir):
        return []

    entries: list[TextEntry] = []
    for fname in sorted(os.listdir(scripts_dir)):
        if not fname.endswith(".inc"):
            continue
        rel = f"data/scripts/{fname}"
        cat = "common_scripts"
        subcat = fname.replace(".inc", "").replace("_", " ").title()
        entries.extend(_parse_text_inc(project_dir, rel, cat, subcat))

    return entries


# ── data/text/*.h parser ──────────────────────────────────────────────────────

_DATA_TEXT_H_RE = re.compile(
    r'^(?:static\s+)?const\s+u8\s+'
    r'(\w+)\s*\[\s*\]\s*=\s*'
    r'_\(\s*"((?:[^"\\]|\\.)*)"\s*\)\s*;',
    re.MULTILINE,
)

_DATA_TEXT_FILES: list[tuple[str, str, str]] = [
    ("src/data/text/trainer_class_names.h", "trainer_class", "Trainer Class Names"),
    ("src/data/text/nature_names.h", "nature_names", "Nature Names"),
    ("src/data/text/quest_log.h", "quest_log", "Quest Log"),
    ("src/data/text/teachy_tv.h", "teachy_tv", "Teachy TV"),
]


def _parse_data_text_h(project_dir: str) -> list[TextEntry]:
    """Parse const u8 strings from data/text/*.h files."""
    entries: list[TextEntry] = []
    for rel, cat, subcat in _DATA_TEXT_FILES:
        path = os.path.join(project_dir, rel)
        text = _read(path)
        if not text:
            continue
        char_lim, max_lines = _get_limits(cat)
        for m in _DATA_TEXT_H_RE.finditer(text):
            var_name = m.group(1)
            value = m.group(2)
            line = text[:m.start()].count("\n") + 1
            display = var_name
            # Strip common prefixes
            for pfx in ("sTrainerClassName_", "sNatureName_",
                        "sQuestLogText_", "sTeachyTvText_",
                        "sText_", "sFameCheckerText_"):
                if var_name.startswith(pfx) and len(var_name) > len(pfx):
                    display = var_name[len(pfx):]
                    display = re.sub(r"([a-z])([A-Z])", r"\1 \2", display)
                    break
            entries.append(TextEntry(
                label=var_name,
                content=value,
                original=value,
                file_path=path,
                file_rel=rel,
                line_number=line,
                category=cat,
                subcategory=subcat,
                char_limit=char_lim,
                max_lines=max_lines,
                is_multiline=any(c in value for c in ("\\n", "\\p")),
                format="c_string",
                owning_tab="",
                display_label=display,
            ))
    return entries


# ── Script cross-reference map ────────────────────────────────────────────────

_MSGBOX_RE = re.compile(
    r'\bmsgbox\s+(\w+)\s*,\s*(\w+)',
    re.MULTILINE,
)


def build_script_xrefs(project_dir: str) -> dict[str, list[tuple[str, str, str]]]:
    """
    Build a map: text_label → [(script_file_rel, script_label, msgbox_type), ...]
    by scanning all scripts.inc files for `msgbox LabelName, MSGBOX_TYPE`.
    """
    xrefs: dict[str, list[tuple[str, str, str]]] = {}

    maps_dir = os.path.join(project_dir, "data", "maps")
    if os.path.isdir(maps_dir):
        for map_name in os.listdir(maps_dir):
            scripts_path = os.path.join(maps_dir, map_name, "scripts.inc")
            if not os.path.isfile(scripts_path):
                continue
            rel = f"data/maps/{map_name}/scripts.inc"
            text = _read(scripts_path)
            current_label = ""
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("@") or stripped.startswith("#"):
                    continue
                if (stripped.endswith("::") or stripped.endswith(":")) \
                        and not stripped.startswith("."):
                    current_label = stripped.rstrip(":")
                mb = _MSGBOX_RE.search(line)
                if mb:
                    text_label = mb.group(1)
                    msg_type = mb.group(2)
                    xrefs.setdefault(text_label, []).append(
                        (rel, current_label, msg_type)
                    )
    return xrefs


# ── Save-back writers ─────────────────────────────────────────────────────────

def _save_c_string(entry: TextEntry) -> None:
    """Write back a modified C string (_("...")) to its source file."""
    text = _read(entry.file_path)
    if not text:
        return

    pattern = re.compile(
        r'(\b' + re.escape(entry.label) +
        r'\s*\[\s*\]\s*=\s*_\(\s*")((?:[^"\\]|\\.)*)("\s*\)\s*;)',
    )
    new_text, n = pattern.subn(
        lambda m: m.group(1) + entry.content + m.group(3),
        text, count=1,
    )
    if n > 0:
        _write(entry.file_path, new_text)


def _save_asm_string(entry: TextEntry) -> None:
    """Write back a modified .string block to its .inc file."""
    text = _read(entry.file_path)
    if not text:
        return

    # Split value at GBA line-break boundaries, keeping delimiter attached
    value = entry.content
    segments = re.split(r"(?<=\\[npl])", value)
    segments = [s for s in segments if s]
    if not segments:
        segments = [value or ""]

    # Add GBA null-terminator
    segments[-1] = segments[-1].rstrip("$") + "$"
    lines = "".join(f"\t.string \"{seg}\"\n" for seg in segments)
    new_block = f"{entry.label}::\n{lines}"

    pattern = re.compile(
        r"^" + re.escape(entry.label) + r"::\s*\n"
        r"(?:[ \t]+\.string\s+\"[^\"]*\"[ \t]*\n?)+",
        re.MULTILINE,
    )
    new_text, n = pattern.subn(new_block, text, count=1)
    if n > 0:
        _write(entry.file_path, new_text)


def _save_name_label(entry: TextEntry) -> None:
    """Write back a name pool label (.string "NAME$")."""
    text = _read(entry.file_path)
    if not text:
        return
    val = entry.content.rstrip("$") + "$"
    pattern = re.compile(
        r"(" + re.escape(entry.label) + r"::\s*\n\s*\.string\s+\")[^\"]*\"",
        re.MULTILINE,
    )
    new_text, n = pattern.subn(lambda m: m.group(1) + val + '"', text, count=1)
    if n > 0:
        _write(entry.file_path, new_text)


def _save_json_region_map(entries: list[TextEntry]) -> None:
    """Write back all modified location name entries to the JSON file."""
    if not entries:
        return
    path = entries[0].file_path
    text = _read(path)
    if not text:
        return

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return

    # Find the list
    arr = data
    if isinstance(data, dict):
        for k in ("map_sections", "sections", "entries"):
            if k in data and isinstance(data[k], list):
                arr = data[k]
                break

    updates = {e.label: e.content for e in entries if e.is_dirty}
    if not updates:
        return

    if isinstance(arr, list):
        for item in arr:
            if not isinstance(item, dict):
                continue
            cid = item.get("id") or item.get("constant") or ""
            if cid in updates:
                for field in ("name", "display_name", "label"):
                    if field in item:
                        item[field] = updates[cid]
                        break
                else:
                    item["name"] = updates[cid]

    _write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


# ── Save dispatcher ───────────────────────────────────────────────────────────

def save_dirty_entries(entries: list[TextEntry]) -> None:
    """Save all dirty entries back to their source files."""
    dirty = [e for e in entries if e.is_dirty]
    if not dirty:
        return

    # Group JSON entries for batch save
    json_entries = [e for e in dirty if e.format == "json_region_map"]
    if json_entries:
        _save_json_region_map(json_entries)
        for e in json_entries:
            e.mark_clean()

    # Save remaining entries individually
    for entry in dirty:
        if entry.format == "json_region_map":
            continue  # already saved
        if entry.format == "c_string":
            _save_c_string(entry)
        elif entry.format == "asm_string":
            _save_asm_string(entry)
        elif entry.format == "name_label":
            _save_name_label(entry)
        entry.mark_clean()


# ── Master index builder ──────────────────────────────────────────────────────

class TextIndex:
    """Holds the full project text index + cross-references."""

    def __init__(self) -> None:
        self.entries: list[TextEntry] = []
        self.xrefs: dict[str, list[tuple[str, str, str]]] = {}
        self._by_label: dict[str, TextEntry] = {}

    def load(self, project_dir: str) -> None:
        """Parse all text sources and build the index."""
        self.entries.clear()
        self._by_label.clear()

        # Parse all sources
        self.entries.extend(_parse_strings_c(project_dir))
        self.entries.extend(_parse_new_game_intro(project_dir))
        self.entries.extend(_parse_location_names(project_dir))
        self.entries.extend(_parse_battle_messages(project_dir))
        self.entries.extend(_parse_all_map_dialogue(project_dir))
        self.entries.extend(_parse_common_scripts(project_dir))
        self.entries.extend(_parse_data_text_h(project_dir))

        # Build label lookup
        for e in self.entries:
            self._by_label[e.label] = e

        # Build script cross-references
        self.xrefs = build_script_xrefs(project_dir)

    def get(self, label: str) -> TextEntry | None:
        return self._by_label.get(label)

    def dirty_count(self) -> int:
        return sum(1 for e in self.entries if e.is_dirty)

    def has_changes(self) -> bool:
        return any(e.is_dirty for e in self.entries)

    def save(self) -> None:
        save_dirty_entries(self.entries)

    def search(self, query: str, *,
               match_case: bool = False,
               whole_word: bool = False,
               regex: bool = False,
               scope: str = "") -> list[TextEntry]:
        """Search entries by content. Returns matching entries."""
        if not query:
            return []

        if regex:
            flags = 0 if match_case else re.IGNORECASE
            try:
                pat = re.compile(query, flags)
            except re.error:
                return []
            results = [
                e for e in self.entries
                if pat.search(e.content) or pat.search(e.label)
            ]
        else:
            if whole_word:
                flags = 0 if match_case else re.IGNORECASE
                pat = re.compile(r"\b" + re.escape(query) + r"\b", flags)
                results = [
                    e for e in self.entries
                    if pat.search(e.content) or pat.search(e.label)
                ]
            else:
                if match_case:
                    results = [
                        e for e in self.entries
                        if query in e.content or query in e.label
                    ]
                else:
                    q = query.lower()
                    results = [
                        e for e in self.entries
                        if q in e.content.lower() or q in e.label.lower()
                    ]

        # Filter by scope
        if scope and scope != "all":
            results = [e for e in results if e.category.startswith(scope)]

        return results

    # ── Categories for tree building ──────────────────────────────────────

    def categories(self) -> dict[str, dict[str, list[TextEntry]]]:
        """
        Return entries organized as:
          { category_key: { subcategory: [entries...] } }
        Only includes entries this tab owns (owning_tab == "").
        """
        result: dict[str, dict[str, list[TextEntry]]] = {}
        for e in self.entries:
            if e.owning_tab:
                continue
            cat = result.setdefault(e.category, {})
            cat.setdefault(e.subcategory, []).append(e)
        return result
