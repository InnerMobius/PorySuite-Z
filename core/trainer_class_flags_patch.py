"""Data-driven trainer-class behaviour flags — engine refactor patcher.

PROBLEM
-------
Vanilla pokefirered hardcodes gym-leader / Elite-Four / champion / rival /
team-boss battle behaviour with bare ``trainerClass == TRAINER_CLASS_LEADER``
comparisons scattered across the engine (battle BGM, victory music, battle
transition, arena background, friendship-on-win, Quest Log categorisation,
rival-name text substitution).  The instant a project RENAMES a class
(``LEADER`` -> ``CHIEF``) or REASSIGNS a gym leader to a new class, every one
of those comparisons silently stops matching and the special behaviour is
lost — wrong battle music, generic victory jingle, no gym arena, etc.

FIX
---
Decouple behaviour from the class CONSTANT.  A per-class bitfield
``gTrainerClassFlags[]`` carries the behaviour; the engine gates read the flag
instead of comparing a constant.  Renaming or adding a class can never break
behaviour again — only the data table changes, never the engine code.  This is
the canonical PorySuite-Z "refactor the engine through the patcher" move
(see CLAUDE.md).

The patcher is idempotent and self-fencing:
  * Flag ``#define``s live in ``include/constants/trainers.h`` (asm-safe).
  * ``extern const u8 gTrainerClassFlags[];`` sits beside the ``gTrainers``
    extern in ``include/battle.h`` (C-only).
  * The table is generated into ``src/data/trainer_class_flags.h`` and
    ``#include``d once in ``src/pokemon.c``.
  * Each engine gate is rewritten by exact-text replacement (a no-op once the
    vanilla text is gone), so re-running never double-applies.

DEFAULT FLAG DETECTION (so the user's CURRENT broken game self-heals)
  1. Vanilla baseline: LEADER->GYM, ELITE_FOUR->E4, CHAMPION->CHAMPION,
     RIVAL_EARLY/RIVAL_LATE->RIVAL, BOSS->BOSS.
  2. Class-name substring: any class named *LEADER* -> GYM, *ELITE* -> E4,
     *CHAMPION* -> CHAMPION, *RIVAL* -> RIVAL, *BOSS* -> BOSS.
  3. Back-propagation from trainer CONSTANT names: a trainer whose constant is
     TRAINER_LEADER_* gives its CURRENT class the GYM flag (likewise
     ELITE_FOUR_/CHAMPION_/RIVAL_).  This is what auto-detects that a renamed
     ``CHIEF`` class is really a gym-leader class because
     ``TRAINER_LEADER_LT_SURGE`` still uses it.
On re-run, flags already present in the table are PRESERVED (user edits via the
Trainer Classes editor win); only classes missing from the table get defaults.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from typing import Dict, List, Tuple

# ── flag model ──────────────────────────────────────────────────────────────

# name -> (bit value, C macro). Order is the display/emit order.
FLAG_BITS: List[Tuple[str, int, str]] = [
    ("gym_leader", 1 << 0, "TRAINER_CLASS_FLAG_GYM_LEADER"),
    ("elite_four", 1 << 1, "TRAINER_CLASS_FLAG_ELITE_FOUR"),
    ("champion",   1 << 2, "TRAINER_CLASS_FLAG_CHAMPION"),
    ("rival",      1 << 3, "TRAINER_CLASS_FLAG_RIVAL"),
    ("boss",       1 << 4, "TRAINER_CLASS_FLAG_BOSS"),
]
_MACRO = {k: m for k, _, m in FLAG_BITS}
_NAME_TO_BIT = {k: b for k, b, _ in FLAG_BITS}

# Fences
_C_OPEN = "// >>> PORYSUITE-GEN trainer-class-flags >>>"
_C_CLOSE = "// <<< PORYSUITE-GEN trainer-class-flags <<<"


# ── IO helpers ──────────────────────────────────────────────────────────────

def _read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def _write(path: str, text: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    os.replace(tmp, path)


# ── parsing ─────────────────────────────────────────────────────────────────

def parse_classes(root: str) -> Dict[str, int]:
    """{TRAINER_CLASS_CONST: numeric_value} from include/constants/trainers.h."""
    path = os.path.join(root, "include", "constants", "trainers.h")
    out: Dict[str, int] = {}
    if not os.path.isfile(path):
        return out
    for m in re.finditer(r"#define\s+(TRAINER_CLASS_\w+)\s+(\d+)", _read(path)):
        out[m.group(1)] = int(m.group(2))
    return out


def parse_trainer_classmap(root: str) -> Dict[str, str]:
    """{TRAINER_CONST: TRAINER_CLASS_CONST}.

    Prefers src/data/trainers.json (PorySuite's source of truth); falls back to
    parsing src/data/trainers.h.
    """
    out: Dict[str, str] = {}
    jpath = os.path.join(root, "src", "data", "trainers.json")
    if os.path.isfile(jpath):
        try:
            data = json.loads(_read(jpath))
        except Exception:
            data = None
        if isinstance(data, dict):
            for const, info in data.items():
                if isinstance(info, dict) and info.get("trainerClass"):
                    out[const] = info["trainerClass"]
            if out:
                return out
    # Fallback: trainers.h struct literals
    hpath = os.path.join(root, "src", "data", "trainers.h")
    if os.path.isfile(hpath):
        for m in re.finditer(
            r"\[(TRAINER_\w+)\]\s*=\s*\{[^}]*?\.trainerClass\s*=\s*(TRAINER_CLASS_\w+)",
            _read(hpath), re.DOTALL,
        ):
            out[m.group(1)] = m.group(2)
    return out


def parse_existing_flags(root: str) -> Dict[str, int]:
    """{TRAINER_CLASS_CONST: flagset} parsed from a previously-generated table.

    Empty dict when the table does not exist yet (first run).
    """
    path = os.path.join(root, "src", "data", "trainer_class_flags.h")
    out: Dict[str, int] = {}
    if not os.path.isfile(path):
        return out
    text = _read(path)
    for m in re.finditer(
        r"\[(TRAINER_CLASS_\w+)\]\s*=\s*([^,\n]+),", text,
    ):
        cls, expr = m.group(1), m.group(2)
        bits = 0
        for macro in re.findall(r"TRAINER_CLASS_FLAG_\w+", expr):
            for k, b, mc in FLAG_BITS:
                if mc == macro:
                    bits |= b
        out[cls] = bits
    return out


# ── default detection ───────────────────────────────────────────────────────

# Vanilla classes the engine originally hardcoded, and the flag each got.
_VANILLA_BASELINE = {
    "TRAINER_CLASS_LEADER":      _NAME_TO_BIT["gym_leader"],
    "TRAINER_CLASS_ELITE_FOUR":  _NAME_TO_BIT["elite_four"],
    "TRAINER_CLASS_CHAMPION":    _NAME_TO_BIT["champion"],
    "TRAINER_CLASS_RIVAL_EARLY": _NAME_TO_BIT["rival"],
    "TRAINER_CLASS_RIVAL_LATE":  _NAME_TO_BIT["rival"],
    "TRAINER_CLASS_BOSS":        _NAME_TO_BIT["boss"],
}

# Trainer-constant prefix -> flag, for back-propagation onto the class a renamed
# leader/E4/champion/rival now uses.
_TRAINER_PREFIX_FLAG = [
    ("TRAINER_ELITE_FOUR_", _NAME_TO_BIT["elite_four"]),
    ("TRAINER_LEADER_",     _NAME_TO_BIT["gym_leader"]),
    ("TRAINER_CHAMPION_",   _NAME_TO_BIT["champion"]),
    ("TRAINER_RIVAL_",      _NAME_TO_BIT["rival"]),
]


def _name_flags(class_const: str) -> int:
    """Flags inferred from the class constant's own name."""
    n = class_const.upper()
    bits = 0
    if "LEADER" in n:
        bits |= _NAME_TO_BIT["gym_leader"]
    if "ELITE" in n:
        bits |= _NAME_TO_BIT["elite_four"]
    if "CHAMPION" in n:
        bits |= _NAME_TO_BIT["champion"]
    if "RIVAL" in n:
        bits |= _NAME_TO_BIT["rival"]
    if "BOSS" in n:
        bits |= _NAME_TO_BIT["boss"]
    return bits


def detect_flags(root: str) -> Tuple[Dict[str, int], List[str]]:
    """Compute the per-class flagset, merging existing table + auto-detection.

    Returns (flags_by_class, notes).  Existing (user-set) flags win; classes not
    yet in the table get vanilla-baseline | name | back-propagated defaults.
    """
    classes = parse_classes(root)
    existing = parse_existing_flags(root)
    classmap = parse_trainer_classmap(root)
    notes: List[str] = []

    # Back-propagation: trainer constant prefix -> the class it uses.
    backprop: Dict[str, int] = {}
    for tconst, cls in classmap.items():
        for prefix, bit in _TRAINER_PREFIX_FLAG:
            if tconst.startswith(prefix):
                backprop[cls] = backprop.get(cls, 0) | bit

    flags: Dict[str, int] = {}
    for cls in classes:
        if cls in existing:
            flags[cls] = existing[cls]          # preserve user edits
            continue
        bits = _VANILLA_BASELINE.get(cls, 0)
        bits |= _name_flags(cls)
        bits |= backprop.get(cls, 0)
        if bits:
            flags[cls] = bits
            human = " | ".join(
                k for k, b, _ in FLAG_BITS if bits & b
            )
            src = []
            if cls in _VANILLA_BASELINE:
                src.append("vanilla")
            if _name_flags(cls):
                src.append("name")
            if backprop.get(cls):
                src.append("trainer-constant")
            notes.append(f"{cls} -> {human}  ({', '.join(src)})")
    return flags, notes


# ── emit: constants + extern + include ──────────────────────────────────────

def ensure_constants(root: str) -> List[str]:
    """Add flag #defines (trainers.h), the extern (battle.h), and the table
    include (pokemon.c).  Idempotent.  Returns applied-messages."""
    applied: List[str] = []

    # 1. Flag #defines in include/constants/trainers.h
    tpath = os.path.join(root, "include", "constants", "trainers.h")
    text = _read(tpath)
    if _C_OPEN not in text:
        block = [
            "",
            _C_OPEN,
            "// Data-driven trainer-class behaviour flags. Renaming/adding a class",
            "// no longer breaks engine behaviour — set the matching flag in",
            "// src/data/trainer_class_flags.h (generated) instead.",
        ]
        for _k, bit, macro in FLAG_BITS:
            block.append(f"#define {macro:<32s} (1 << {bit.bit_length() - 1})")
        block.append(_C_CLOSE)
        # Append after the last TRAINER_CLASS_ define for locality.
        last = list(re.finditer(r"#define\s+TRAINER_CLASS_\w+\s+\d+[^\n]*\n", text))
        if last:
            pos = last[-1].end()
            text = text[:pos] + "\n".join(block) + "\n" + text[pos:]
        else:
            text = text.rstrip("\n") + "\n" + "\n".join(block) + "\n"
        _write(tpath, text)
        applied.append("Added TRAINER_CLASS_FLAG_* defines to constants/trainers.h")

    # 2. extern beside gTrainers in include/battle.h
    bpath = os.path.join(root, "include", "battle.h")
    btext = _read(bpath)
    if "gTrainerClassFlags" not in btext:
        anchor = "extern const struct Trainer gTrainers[];"
        if anchor in btext:
            btext = btext.replace(
                anchor,
                anchor + "\nextern const u8 gTrainerClassFlags[]; // PORYSUITE-GEN trainer-class-flags",
                1,
            )
            _write(bpath, btext)
            applied.append("Added gTrainerClassFlags extern to battle.h")
        else:
            raise RuntimeError("could not find gTrainers extern in battle.h")

    # 3. table include in src/pokemon.c
    ppath = os.path.join(root, "src", "pokemon.c")
    ptext = _read(ppath)
    if 'data/trainer_class_flags.h' not in ptext:
        anchor = '#include "data.h"'
        inc = '#include "data/trainer_class_flags.h" // PORYSUITE-GEN trainer-class-flags'
        if anchor in ptext:
            ptext = ptext.replace(anchor, anchor + "\n" + inc, 1)
            _write(ppath, ptext)
            applied.append("Added trainer_class_flags.h include to pokemon.c")
        else:
            raise RuntimeError('could not find #include "data.h" in pokemon.c')
    return applied


def generate_table(root: str, flags: Dict[str, int]) -> List[str]:
    """Write src/data/trainer_class_flags.h with the gTrainerClassFlags[] table."""
    classes = parse_classes(root)
    size = (max(classes.values()) + 1) if classes else 1
    lines = [
        "// Generated by PorySuite-Z — trainer-class behaviour flags.",
        "// Do NOT hand-edit; use the Trainer Classes editor (it regenerates",
        "// this file and preserves your choices).",
        f"const u8 gTrainerClassFlags[{size}] = {{",
    ]
    for cls in sorted(flags, key=lambda c: classes.get(c, 0)):
        bits = flags[cls]
        if not bits:
            continue
        expr = " | ".join(m for _k, b, m in FLAG_BITS if bits & b)
        lines.append(f"    [{cls}] = {expr},")
    lines.append("};")
    path = os.path.join(root, "src", "data", "trainer_class_flags.h")
    _write(path, "\n".join(lines) + "\n")
    return [f"Generated src/data/trainer_class_flags.h ({len(flags)} classes flagged, size {size})"]


# ── engine gate rewrites ────────────────────────────────────────────────────

# Each entry: (relative path, old_exact, new_exact).  Exact-text replacement is
# naturally idempotent — once old is gone, the replace is a no-op.
def _gate_edits() -> List[Tuple[str, str, str]]:
    F = _NAME_TO_BIT
    edits: List[Tuple[str, str, str]] = []

    # 1. pokemon.c GetBattleBGM
    edits.append((
        os.path.join("src", "pokemon.c"),
        """        switch (gTrainers[gTrainerBattleOpponent_A].trainerClass)
        {
        case TRAINER_CLASS_CHAMPION:
            return MUS_VS_CHAMPION;
        case TRAINER_CLASS_LEADER:
        case TRAINER_CLASS_ELITE_FOUR:
            return MUS_VS_GYM_LEADER;
        case TRAINER_CLASS_BOSS:
        case TRAINER_CLASS_TEAM_ROCKET:
        case TRAINER_CLASS_COOLTRAINER:
        case TRAINER_CLASS_GENTLEMAN:
        case TRAINER_CLASS_RIVAL_LATE:
        default:
            return MUS_VS_TRAINER;
        }""",
        """        {
            u8 classFlags = gTrainerClassFlags[gTrainers[gTrainerBattleOpponent_A].trainerClass];
            if (classFlags & TRAINER_CLASS_FLAG_CHAMPION)
                return MUS_VS_CHAMPION;
            if (classFlags & (TRAINER_CLASS_FLAG_GYM_LEADER | TRAINER_CLASS_FLAG_ELITE_FOUR))
                return MUS_VS_GYM_LEADER;
            return MUS_VS_TRAINER;
        }""",
    ))

    # 2. pokemon.c friendship league-battle gate
    edits.append((
        os.path.join("src", "pokemon.c"),
        """            if (!(gTrainers[gTrainerBattleOpponent_A].trainerClass == TRAINER_CLASS_LEADER
                || gTrainers[gTrainerBattleOpponent_A].trainerClass == TRAINER_CLASS_ELITE_FOUR
                || gTrainers[gTrainerBattleOpponent_A].trainerClass == TRAINER_CLASS_CHAMPION))
                return;""",
        """            if (!(gTrainerClassFlags[gTrainers[gTrainerBattleOpponent_A].trainerClass]
                & (TRAINER_CLASS_FLAG_GYM_LEADER | TRAINER_CLASS_FLAG_ELITE_FOUR | TRAINER_CLASS_FLAG_CHAMPION)))
                return;""",
    ))

    # 3. battle_main.c victory music
    edits.append((
        os.path.join("src", "battle_main.c"),
        """        switch (gTrainers[gTrainerBattleOpponent_A].trainerClass)
        {
        case TRAINER_CLASS_LEADER:
        case TRAINER_CLASS_CHAMPION:
            PlayBGM(MUS_VICTORY_GYM_LEADER);
            break;
        case TRAINER_CLASS_BOSS:
        case TRAINER_CLASS_TEAM_ROCKET:
        case TRAINER_CLASS_COOLTRAINER:
        case TRAINER_CLASS_ELITE_FOUR:
        case TRAINER_CLASS_GENTLEMAN:
        default:
            PlayBGM(MUS_VICTORY_TRAINER);
            break;
        }""",
        """        if (gTrainerClassFlags[gTrainers[gTrainerBattleOpponent_A].trainerClass]
            & (TRAINER_CLASS_FLAG_GYM_LEADER | TRAINER_CLASS_FLAG_CHAMPION))
            PlayBGM(MUS_VICTORY_GYM_LEADER);
        else
            PlayBGM(MUS_VICTORY_TRAINER);""",
    ))

    # 4. battle_setup.c transition — ELITE_FOUR + CHAMPION conditions
    edits.append((
        os.path.join("src", "battle_setup.c"),
        "    if (gTrainers[gTrainerBattleOpponent_A].trainerClass == TRAINER_CLASS_ELITE_FOUR)\n    {",
        "    if (gTrainerClassFlags[gTrainers[gTrainerBattleOpponent_A].trainerClass] & TRAINER_CLASS_FLAG_ELITE_FOUR)\n    {",
    ))
    edits.append((
        os.path.join("src", "battle_setup.c"),
        "    if (gTrainers[gTrainerBattleOpponent_A].trainerClass == TRAINER_CLASS_CHAMPION)\n        return B_TRANSITION_BLUE;",
        "    if (gTrainerClassFlags[gTrainers[gTrainerBattleOpponent_A].trainerClass] & TRAINER_CLASS_FLAG_CHAMPION)\n        return B_TRANSITION_BLUE;",
    ))

    # 5a. battle_bg.c entry gfx (LEADER/CHAMPION -> BUILDING)
    edits.append((
        os.path.join("src", "battle_bg.c"),
        """            if (trainerClass == TRAINER_CLASS_LEADER)
            {
                LoadBattleTerrainEntryGfx(BATTLE_TERRAIN_BUILDING);
                return;
            }
            else if (trainerClass == TRAINER_CLASS_CHAMPION)
            {
                LoadBattleTerrainEntryGfx(BATTLE_TERRAIN_BUILDING);
                return;
            }""",
        """            if (gTrainerClassFlags[trainerClass] & (TRAINER_CLASS_FLAG_GYM_LEADER | TRAINER_CLASS_FLAG_CHAMPION))
            {
                LoadBattleTerrainEntryGfx(BATTLE_TERRAIN_BUILDING);
                return;
            }""",
    ))
    # 5b. battle_bg.c GetBattleTerrainOverride (LEADER/CHAMPION -> arena)
    edits.append((
        os.path.join("src", "battle_bg.c"),
        """        if (gTrainers[gTrainerBattleOpponent_A].trainerClass == TRAINER_CLASS_LEADER)
            return BATTLE_TERRAIN_LEADER;
        else if (gTrainers[gTrainerBattleOpponent_A].trainerClass == TRAINER_CLASS_CHAMPION)
            return BATTLE_TERRAIN_CHAMPION;""",
        """        if (gTrainerClassFlags[gTrainers[gTrainerBattleOpponent_A].trainerClass] & TRAINER_CLASS_FLAG_GYM_LEADER)
            return BATTLE_TERRAIN_LEADER;
        else if (gTrainerClassFlags[gTrainers[gTrainerBattleOpponent_A].trainerClass] & TRAINER_CLASS_FLAG_CHAMPION)
            return BATTLE_TERRAIN_CHAMPION;""",
    ))

    # 6. battle_message.c rival-name substitution
    edits.append((
        os.path.join("src", "battle_message.c"),
        """                    if (gTrainers[gTrainerBattleOpponent_A].trainerClass == TRAINER_CLASS_RIVAL_EARLY
                     || gTrainers[gTrainerBattleOpponent_A].trainerClass == TRAINER_CLASS_RIVAL_LATE
                     || gTrainers[gTrainerBattleOpponent_A].trainerClass == TRAINER_CLASS_CHAMPION)""",
        """                    if (gTrainerClassFlags[gTrainers[gTrainerBattleOpponent_A].trainerClass]
                        & (TRAINER_CLASS_FLAG_RIVAL | TRAINER_CLASS_FLAG_CHAMPION))""",
    ))

    # 7. quest_log_battle.c QL event categorisation
    edits.append((
        os.path.join("src", "quest_log_battle.c"),
        """            switch (gTrainers[gTrainerBattleOpponent_A].trainerClass)
            {
            case TRAINER_CLASS_LEADER:
                eventId = QL_EVENT_DEFEATED_GYM_LEADER;
                break;
            case TRAINER_CLASS_CHAMPION:
                eventId = QL_EVENT_DEFEATED_CHAMPION;
                break;
            case TRAINER_CLASS_ELITE_FOUR:
                eventId = QL_EVENT_DEFEATED_E4_MEMBER;
                break;
            default:
                eventId = QL_EVENT_DEFEATED_TRAINER;
                break;
            }""",
        """            {
                u8 classFlags = gTrainerClassFlags[gTrainers[gTrainerBattleOpponent_A].trainerClass];
                if (classFlags & TRAINER_CLASS_FLAG_GYM_LEADER)
                    eventId = QL_EVENT_DEFEATED_GYM_LEADER;
                else if (classFlags & TRAINER_CLASS_FLAG_CHAMPION)
                    eventId = QL_EVENT_DEFEATED_CHAMPION;
                else if (classFlags & TRAINER_CLASS_FLAG_ELITE_FOUR)
                    eventId = QL_EVENT_DEFEATED_E4_MEMBER;
                else
                    eventId = QL_EVENT_DEFEATED_TRAINER;
            }""",
    ))

    # 8a. quest_log_events.c filter gate
    edits.append((
        os.path.join("src", "quest_log_events.c"),
        """        if (trainerClass == TRAINER_CLASS_RIVAL_EARLY
         || trainerClass == TRAINER_CLASS_RIVAL_LATE
         || trainerClass == TRAINER_CLASS_CHAMPION
         || trainerClass == TRAINER_CLASS_BOSS)
            return FALSE;""",
        """        if (gTrainerClassFlags[trainerClass]
         & (TRAINER_CLASS_FLAG_RIVAL | TRAINER_CLASS_FLAG_CHAMPION | TRAINER_CLASS_FLAG_BOSS))
            return FALSE;""",
    ))
    # 8b. quest_log_events.c placeholder gate
    edits.append((
        os.path.join("src", "quest_log_events.c"),
        """    if (gTrainers[r5[2]].trainerClass == TRAINER_CLASS_RIVAL_EARLY
     || gTrainers[r5[2]].trainerClass == TRAINER_CLASS_RIVAL_LATE
     || gTrainers[r5[2]].trainerClass == TRAINER_CLASS_CHAMPION)""",
        """    if (gTrainerClassFlags[gTrainers[r5[2]].trainerClass]
     & (TRAINER_CLASS_FLAG_RIVAL | TRAINER_CLASS_FLAG_CHAMPION))""",
    ))
    return edits


def rewrite_gates(root: str) -> Tuple[List[str], List[str]]:
    """Apply all engine gate rewrites. Returns (applied, skipped)."""
    applied: List[str] = []
    skipped: List[str] = []
    # group edits per file so each file is read/written once
    by_file: Dict[str, List[Tuple[str, str]]] = {}
    for rel, old, new in _gate_edits():
        by_file.setdefault(rel, []).append((old, new))
    for rel, pairs in by_file.items():
        path = os.path.join(root, rel)
        if not os.path.isfile(path):
            skipped.append(f"{rel} (missing)")
            continue
        text = _read(path)
        changed = False
        for old, new in pairs:
            if old in text:
                text = text.replace(old, new, 1)
                changed = True
                applied.append(f"{rel}: rewired a class gate to flags")
            elif new in text:
                skipped.append(f"{rel}: gate already flag-driven")
            else:
                skipped.append(f"{rel}: gate text not found (project differs from vanilla?)")
        if changed:
            _write(path, text)
    return applied, skipped


# ── orchestration ────────────────────────────────────────────────────────────

def apply(root: str, backup: bool = True) -> Tuple[bool, List[str], List[str]]:
    """Install/refresh the data-driven trainer-class flags system.

    Returns (ok, applied_messages, errors).
    """
    applied: List[str] = []
    errors: List[str] = []
    try:
        flags, notes = detect_flags(root)
        applied.extend(ensure_constants(root))
        applied.extend(generate_table(root, flags))
        gate_applied, gate_skipped = rewrite_gates(root)
        applied.extend(gate_applied)
        # Surface auto-detection so the user sees what got which flag.
        applied.extend("auto-flag " + n for n in notes)
        # Skipped gates are only an error if the gate text was NOT found AND not
        # already flag-driven — but we record them as info either way.
        for s in gate_skipped:
            if "not found" in s:
                errors.append(s)
            else:
                applied.append(s)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"trainer-class-flags: {exc}")
        return False, applied, errors
    return (not errors), applied, errors


def is_installed(root: str) -> bool:
    """True when the flags system has already been installed into the engine
    (the fence is present in constants/trainers.h)."""
    tpath = os.path.join(root, "include", "constants", "trainers.h")
    try:
        return _C_OPEN in _read(tpath)
    except OSError:
        return False


def get_flags(root: str) -> Dict[str, int]:
    """Public read accessor for the UI: {class_const: flagset} (merged)."""
    flags, _ = detect_flags(root)
    return flags


def set_flags(root: str, flags_by_class: Dict[str, int]) -> Tuple[bool, List[str], List[str]]:
    """Write the user's chosen flags then re-apply the engine refactor.

    The UI passes the full {class_const: flagset} map; it is written verbatim
    (overriding auto-detection) and the engine is (re)patched.
    """
    applied: List[str] = []
    errors: List[str] = []
    try:
        applied.extend(ensure_constants(root))
        applied.extend(generate_table(root, flags_by_class))
        gate_applied, gate_skipped = rewrite_gates(root)
        applied.extend(gate_applied)
        for s in gate_skipped:
            if "not found" in s:
                errors.append(s)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"trainer-class-flags set: {exc}")
        return False, applied, errors
    return (not errors), applied, errors
