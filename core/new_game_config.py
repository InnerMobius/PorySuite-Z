"""Read + write the pokefirered "new game" starting values for the Config tab.

Everything here patches real engine source the way the tool's other patchers do
(regex parse + targeted rewrite, byte-equality guarded so a no-op re-run is a
clean diff). The values:

  • Starting money        src/new_game.c   SetMoney(&gSaveBlock1Ptr->money, N)
  • Starting location+xy  src/new_game.c   WarpToPlayersRoom() SetWarpDestination
  • National Dex at start src/new_game.c   EnableNationalPokedex_RSE();  (toggle)
  • Default text speed     src/new_game.c   SetDefaultOptions() optionsTextSpeed
  • Default battle style   src/new_game.c   SetDefaultOptions() optionsBattleStyle
  • Starting PC items     src/player_pc.c  gNewGamePCItems[] table
  • Starting bag items    src/new_game.c   a tool-managed AddBagItem() block after
                                           ClearBag() (vanilla starts empty)

Each getter returns a sensible default if its line can't be found, so a project
that has already been hand-edited or is on an odd revision degrades gracefully
(the UI just shows the default and the user can re-set it).
"""

from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Tuple

from core.file_io import write_text_if_changed

ItemQty = Tuple[str, int]   # (ITEM_* constant, quantity)

TEXT_SPEEDS = ["OPTIONS_TEXT_SPEED_SLOW", "OPTIONS_TEXT_SPEED_MID",
               "OPTIONS_TEXT_SPEED_FAST"]
BATTLE_STYLES = ["OPTIONS_BATTLE_STYLE_SHIFT", "OPTIONS_BATTLE_STYLE_SET"]

_BAG_BEGIN = "// PORYSUITE_STARTING_BAG_BEGIN"
_BAG_END = "// PORYSUITE_STARTING_BAG_END"


def _new_game_c(root: str) -> str:
    return os.path.join(root, "src", "new_game.c")


def _player_pc_c(root: str) -> str:
    return os.path.join(root, "src", "player_pc.c")


def _items_h(root: str) -> str:
    return os.path.join(root, "include", "constants", "items.h")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


# ── item constants (for the UI's picker + validation) ──────────────────────

def parse_item_constants(root: str) -> List[str]:
    """Every ITEM_* constant defined in constants/items.h, in id order."""
    path = _items_h(root)
    if not os.path.isfile(path):
        return ["ITEM_NONE"]
    pairs = []
    for m in re.finditer(r"#define\s+(ITEM_\w+)\s+(\d+)", _read(path)):
        pairs.append((int(m.group(2)), m.group(1)))
    pairs.sort()
    seen, out = set(), []
    for _id, name in pairs:
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out or ["ITEM_NONE"]


def parse_map_constants(root: str) -> List[str]:
    """Every real MAP_* constant from include/constants/map_groups.h (sorted).

    Excludes the MAP_GROUP()/MAP_NUM() helper macros and the *_COUNT sizes,
    leaving the actual map ids the start-location picker can offer.
    """
    path = os.path.join(root, "include", "constants", "map_groups.h")
    if not os.path.isfile(path):
        return []
    names = set()
    for m in re.finditer(r"#define\s+(MAP_\w+)[ \t]", _read(path)):
        n = m.group(1)
        if n in ("MAP_GROUP", "MAP_NUM") or n.endswith("_COUNT"):
            continue
        names.add(n)
    return sorted(names)


# ── starting money ──────────────────────────────────────────────────────────

_MONEY_RE = re.compile(r"(SetMoney\(&gSaveBlock1Ptr->money,\s*)(\d+)(\s*\))")


def get_starting_money(root: str) -> int:
    path = _new_game_c(root)
    if os.path.isfile(path):
        m = _MONEY_RE.search(_read(path))
        if m:
            return int(m.group(2))
    return 3000


def set_starting_money(root: str, value: int) -> bool:
    path = _new_game_c(root)
    if not os.path.isfile(path):
        return False
    value = max(0, min(999999, int(value)))
    text = _read(path)
    if not _MONEY_RE.search(text):
        return False
    new = _MONEY_RE.sub(rf"\g<1>{value}\g<3>", text, count=1)
    return write_text_if_changed(path, new)


# ── starting location + x/y ─────────────────────────────────────────────────

_WARP_RE = re.compile(
    r"SetWarpDestination\(\s*MAP_GROUP\((\w+)\)\s*,\s*MAP_NUM\(\w+\)\s*,\s*"
    r"(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*\)")


def get_start_location(root: str) -> Tuple[str, int, int]:
    """(MAP_* constant, x, y). Defaults to the vanilla bedroom warp."""
    path = _new_game_c(root)
    if os.path.isfile(path):
        m = _WARP_RE.search(_read(path))
        if m:
            return m.group(1), int(m.group(3)), int(m.group(4))
    return "MAP_PALLET_TOWN_PLAYERS_HOUSE_2F", 6, 6


def set_start_location(root: str, map_const: str, x: int, y: int) -> bool:
    path = _new_game_c(root)
    if not os.path.isfile(path):
        return False
    map_const = map_const.strip()
    if not re.fullmatch(r"MAP_\w+", map_const):
        return False
    text = _read(path)
    m = _WARP_RE.search(text)
    if not m:
        return False
    warp_id = m.group(2)   # keep the existing warp-id (-1 = use x/y)
    repl = (f"SetWarpDestination(MAP_GROUP({map_const}), MAP_NUM({map_const}), "
            f"{warp_id}, {int(x)}, {int(y)})")
    new = text[:m.start()] + repl + text[m.end():]
    return write_text_if_changed(path, new)


# ── national dex at start (toggle the call) ─────────────────────────────────

_DEX_ON_RE = re.compile(r"^([ \t]*)EnableNationalPokedex_RSE\(\);", re.MULTILINE)
_DEX_OFF_RE = re.compile(
    r"^([ \t]*)//\s*EnableNationalPokedex_RSE\(\);.*$", re.MULTILINE)


def get_national_dex(root: str) -> bool:
    path = _new_game_c(root)
    if not os.path.isfile(path):
        return True
    return bool(_DEX_ON_RE.search(_read(path)))


def set_national_dex(root: str, enabled: bool) -> bool:
    path = _new_game_c(root)
    if not os.path.isfile(path):
        return False
    text = _read(path)
    if enabled:
        m = _DEX_OFF_RE.search(text)
        if m:
            text = text[:m.start()] + f"{m.group(1)}EnableNationalPokedex_RSE();" + text[m.end():]
        # already on (or no line) → nothing to do
    else:
        m = _DEX_ON_RE.search(text)
        if m:
            indent = m.group(1)
            text = (text[:m.start()]
                    + f"{indent}// EnableNationalPokedex_RSE();  // PorySuite: National Dex off at start"
                    + text[m.end():])
    return write_text_if_changed(path, text)


# ── default options (text speed / battle style) ─────────────────────────────

def _opt_re(field: str) -> re.Pattern:
    return re.compile(
        rf"(gSaveBlock2Ptr->{field}\s*=\s*)(\w+)(\s*;)")


def get_default_text_speed(root: str) -> str:
    path = _new_game_c(root)
    if os.path.isfile(path):
        m = _opt_re("optionsTextSpeed").search(_read(path))
        if m and m.group(2) in TEXT_SPEEDS:
            return m.group(2)
    return "OPTIONS_TEXT_SPEED_MID"


def set_default_text_speed(root: str, value: str) -> bool:
    if value not in TEXT_SPEEDS:
        return False
    return _set_opt(root, "optionsTextSpeed", value)


def get_default_battle_style(root: str) -> str:
    path = _new_game_c(root)
    if os.path.isfile(path):
        m = _opt_re("optionsBattleStyle").search(_read(path))
        if m and m.group(2) in BATTLE_STYLES:
            return m.group(2)
    return "OPTIONS_BATTLE_STYLE_SHIFT"


def set_default_battle_style(root: str, value: str) -> bool:
    if value not in BATTLE_STYLES:
        return False
    return _set_opt(root, "optionsBattleStyle", value)


def _set_opt(root: str, field: str, value: str) -> bool:
    path = _new_game_c(root)
    if not os.path.isfile(path):
        return False
    text = _read(path)
    rx = _opt_re(field)
    if not rx.search(text):
        return False
    return write_text_if_changed(path, rx.sub(rf"\g<1>{value}\g<3>", text, count=1))


# ── starting PC items (gNewGamePCItems table) ───────────────────────────────

_PC_TABLE_RE = re.compile(
    r"(static const struct ItemSlot gNewGamePCItems\[\]\s*=\s*\{)(.*?)(\};)",
    re.DOTALL)
_ITEM_ROW_RE = re.compile(r"\{\s*(ITEM_\w+)\s*,\s*(\d+)\s*\}")


def get_pc_items(root: str) -> List[ItemQty]:
    """The gNewGamePCItems rows up to (not including) the ITEM_NONE sentinel."""
    path = _player_pc_c(root)
    if not os.path.isfile(path):
        return []
    m = _PC_TABLE_RE.search(_read(path))
    if not m:
        return []
    out: List[ItemQty] = []
    for row in _ITEM_ROW_RE.finditer(m.group(2)):
        item, qty = row.group(1), int(row.group(2))
        if item == "ITEM_NONE":
            break
        out.append((item, qty))
    return out


def set_pc_items(root: str, items: List[ItemQty]) -> bool:
    path = _player_pc_c(root)
    if not os.path.isfile(path):
        return False
    text = _read(path)
    m = _PC_TABLE_RE.search(text)
    if not m:
        return False
    rows = "".join(f"    {{ {it}, {max(1, int(q))} }},\n"
                   for it, q in items if it and it != "ITEM_NONE")
    body = "\n" + rows + "    { ITEM_NONE,   0 }\n"
    new = text[:m.start()] + m.group(1) + body + m.group(3) + text[m.end():]
    return write_text_if_changed(path, new)


# ── starting bag items (tool-managed block in new_game.c) ───────────────────

def get_bag_items(root: str) -> List[ItemQty]:
    """Items in the tool-managed AddBagItem block (empty if none added yet)."""
    path = _new_game_c(root)
    if not os.path.isfile(path):
        return []
    text = _read(path)
    i = text.find(_BAG_BEGIN)
    j = text.find(_BAG_END, i + 1) if i >= 0 else -1
    if i < 0 or j < 0:
        return []
    block = text[i:j]
    out: List[ItemQty] = []
    for m in re.finditer(r"AddBagItem\(\s*(ITEM_\w+)\s*,\s*(\d+)\s*\)", block):
        out.append((m.group(1), int(m.group(2))))
    return out


def set_bag_items(root: str, items: List[ItemQty]) -> bool:
    """Insert/replace/remove the tool-managed bag block right after ClearBag()."""
    path = _new_game_c(root)
    if not os.path.isfile(path):
        return False
    text = _read(path)
    rows = "".join(f"    AddBagItem({it}, {max(1, int(q))});\n"
                   for it, q in items if it and it != "ITEM_NONE")
    block = (f"    {_BAG_BEGIN}\n{rows}    {_BAG_END}\n") if rows else ""

    i = text.find(_BAG_BEGIN)
    if i >= 0:
        # replace existing block (from the start of its line to end of END line)
        line_start = text.rfind("\n", 0, i) + 1
        j = text.find(_BAG_END, i)
        end = text.find("\n", j)
        end = (end + 1) if end >= 0 else len(text)
        new = text[:line_start] + block + text[end:]
    else:
        if not block:
            return False  # nothing to add, no existing block — no-op
        anchor = re.search(r"^[ \t]*ClearBag\(\);[ \t]*\n", text, re.MULTILINE)
        if not anchor:
            return False
        new = text[:anchor.end()] + block + text[anchor.end():]
    return write_text_if_changed(path, new)


# ── run indoors (patches src/bike.c IsRunningDisallowed) ────────────────────
# Not a new-game value, but a "game config" tweak that belongs with the rest.
# Vanilla disallows running on maps whose header has allowRunning = 0 (indoors).
# Neutering that one check (`&& 0` so it's never taken) lets the dash work inside
# buildings; the per-tile forbid check (warps, ledges, etc.) still applies.

def _bike_c(root: str) -> str:
    return os.path.join(root, "src", "bike.c")


_RUN_GATE_RE = re.compile(
    r"(if\s*\(!gMapHeader\.allowRunning)(\s*&&\s*0)?(\s*\)[^\n]*)")


def get_run_indoors(root: str) -> bool:
    path = _bike_c(root)
    if os.path.isfile(path):
        m = _RUN_GATE_RE.search(_read(path))
        if m:
            return bool(m.group(2))   # " && 0" present → run-indoors ON
    return False


def set_run_indoors(root: str, enabled: bool) -> bool:
    path = _bike_c(root)
    if not os.path.isfile(path):
        return False
    text = _read(path)
    m = _RUN_GATE_RE.search(text)
    if not m:
        return False
    repl = ("if (!gMapHeader.allowRunning && 0) // PorySuite: run indoors"
            if enabled else "if (!gMapHeader.allowRunning)")
    new = text[:m.start()] + repl + text[m.end():]
    return write_text_if_changed(path, new)


# ── one-shot snapshot for the UI ────────────────────────────────────────────

def read_all(root: str) -> Dict[str, object]:
    return {
        "money": get_starting_money(root),
        "location": get_start_location(root),       # (map, x, y)
        "national_dex": get_national_dex(root),
        "text_speed": get_default_text_speed(root),
        "battle_style": get_default_battle_style(root),
        "pc_items": get_pc_items(root),
        "bag_items": get_bag_items(root),
        "run_indoors": get_run_indoors(root),
    }
