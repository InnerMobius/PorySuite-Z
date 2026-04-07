"""
core/ability_effect_templates.py
Battle and field effect templates for the Abilities Editor.

Handles:
- Defining configurable effect patterns (what parameters users can tweak)
- Detecting which pattern an existing ability uses by parsing C source
- Generating C code from a template + user-chosen parameters
- Inserting / replacing / removing effect code in the correct C files
"""

from __future__ import annotations

import os
import re
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple


# ═══════════════════════════════════════════════════════════════════════════════
# Value maps — dropdown choices for each parameter type
# ═══════════════════════════════════════════════════════════════════════════════

STATUS_CHOICES: list[tuple[str, dict]] = [
    ("Poison", {
        "flags": "STATUS1_POISON | STATUS1_TOXIC_POISON | STATUS1_TOXIC_COUNTER",
        "string": "gStatusConditionString_PoisonJpn",
        "effect": 1, "field": "status1",
        "move_effect": "MOVE_EFFECT_POISON",
        "extra": "",
    }),
    ("Burn", {
        "flags": "STATUS1_BURN",
        "string": "gStatusConditionString_BurnJpn",
        "effect": 1, "field": "status1",
        "move_effect": "MOVE_EFFECT_BURN",
        "extra": "",
    }),
    ("Freeze", {
        "flags": "STATUS1_FREEZE",
        "string": "gStatusConditionString_IceJpn",
        "effect": 1, "field": "status1",
        "move_effect": "MOVE_EFFECT_FREEZE",
        "extra": "",
    }),
    ("Paralysis", {
        "flags": "STATUS1_PARALYSIS",
        "string": "gStatusConditionString_ParalysisJpn",
        "effect": 1, "field": "status1",
        "move_effect": "MOVE_EFFECT_PARALYSIS",
        "extra": "",
    }),
    ("Sleep", {
        "flags": "STATUS1_SLEEP",
        "string": "gStatusConditionString_SleepJpn",
        "effect": 1, "field": "status1",
        "move_effect": "MOVE_EFFECT_SLEEP",
        "extra": "            gBattleMons[battler].status2 &= ~STATUS2_NIGHTMARE;\n",
    }),
    ("Confusion", {
        "flags": "STATUS2_CONFUSION",
        "string": "gStatusConditionString_ConfusionJpn",
        "effect": 2, "field": "status2",
        "move_effect": "MOVE_EFFECT_CONFUSION",
        "extra": "",
    }),
    ("Infatuation", {
        "flags": "STATUS2_INFATUATION",
        "string": "gStatusConditionString_LoveJpn",
        "effect": 3, "field": "status2",
        "move_effect": None,
        "extra": "",
    }),
]

TYPE_CHOICES: list[tuple[str, str]] = [
    ("Normal", "TYPE_NORMAL"),
    ("Fighting", "TYPE_FIGHTING"),
    ("Flying", "TYPE_FLYING"),
    ("Poison", "TYPE_POISON"),
    ("Ground", "TYPE_GROUND"),
    ("Rock", "TYPE_ROCK"),
    ("Bug", "TYPE_BUG"),
    ("Ghost", "TYPE_GHOST"),
    ("Steel", "TYPE_STEEL"),
    ("Fire", "TYPE_FIRE"),
    ("Water", "TYPE_WATER"),
    ("Grass", "TYPE_GRASS"),
    ("Electric", "TYPE_ELECTRIC"),
    ("Psychic", "TYPE_PSYCHIC"),
    ("Ice", "TYPE_ICE"),
    ("Dragon", "TYPE_DRAGON"),
    ("Dark", "TYPE_DARK"),
]

STAT_CHOICES: list[tuple[str, str]] = [
    ("Attack", "STAT_ATK"),
    ("Defense", "STAT_DEF"),
    ("Speed", "STAT_SPEED"),
    ("Sp. Attack", "STAT_SPATK"),
    ("Sp. Defense", "STAT_SPDEF"),
]

WEATHER_CHOICES: list[tuple[str, dict]] = [
    ("Rain", {
        "check": "B_WEATHER_RAIN_PERMANENT",
        "set": "(B_WEATHER_RAIN_PERMANENT | B_WEATHER_RAIN_TEMPORARY)",
        "script": "BattleScript_DrizzleActivates",
    }),
    ("Sandstorm", {
        "check": "B_WEATHER_SANDSTORM_PERMANENT",
        "set": "B_WEATHER_SANDSTORM",
        "script": "BattleScript_SandstreamActivates",
    }),
    ("Sun", {
        "check": "B_WEATHER_SUN_PERMANENT",
        "set": "(B_WEATHER_SUN_PERMANENT | B_WEATHER_SUN_TEMPORARY)",
        "script": "BattleScript_DroughtActivates",
    }),
]

WEATHER_SPEED_CHOICES: list[tuple[str, str]] = [
    ("Rain", "B_WEATHER_RAIN"),
    ("Sandstorm", "B_WEATHER_SANDSTORM"),
    ("Sun", "B_WEATHER_SUN"),
]

CONTACT_STATUS_CHOICES: list[tuple[str, str]] = [
    ("Poison", "MOVE_EFFECT_POISON"),
    ("Burn", "MOVE_EFFECT_BURN"),
    ("Paralysis", "MOVE_EFFECT_PARALYSIS"),
    ("Sleep", "MOVE_EFFECT_SLEEP"),
    ("Freeze", "MOVE_EFFECT_FREEZE"),
]

FRACTION_CHOICES: list[tuple[str, int]] = [
    ("1/4 max HP", 4),
    ("1/8 max HP", 8),
    ("1/16 max HP", 16),
]

CHANCE_CHOICES: list[tuple[str, int]] = [
    ("10%", 10),
    ("20%", 5),
    ("30%", 3),  # (Random() % 3) == 0
    ("33%", 3),
    ("50%", 2),
]


# ═══════════════════════════════════════════════════════════════════════════════
# Template definitions
# ═══════════════════════════════════════════════════════════════════════════════

class EffectParam:
    """One configurable parameter in a template."""
    __slots__ = ("id", "label", "choices")

    def __init__(self, pid: str, label: str, choices: list[tuple[str, Any]]):
        self.id = pid
        self.label = label
        self.choices = choices  # [(display_name, value), ...]


class EffectTemplate:
    """Base for battle/field effect templates."""
    __slots__ = ("id", "name", "description", "params")

    def __init__(self, tid: str, name: str, description: str,
                 params: list[EffectParam]):
        self.id = tid
        self.name = name
        self.description = description
        self.params = params


# ── Battle effect templates ─────────────────────────────────────────────────

BATTLE_TEMPLATES: list[EffectTemplate] = [
    EffectTemplate(
        "status_immunity",
        "Status Immunity",
        "Cures and prevents a specific status condition (like Water Veil blocks burns)",
        [EffectParam("status", "Immune to", STATUS_CHOICES)],
    ),
    EffectTemplate(
        "contact_status",
        "Contact Status Infliction",
        "May inflict a status on the attacker when hit by a contact move (like Static)",
        [
            EffectParam("status", "Inflict", CONTACT_STATUS_CHOICES),
            EffectParam("chance", "Chance", CHANCE_CHOICES),
        ],
    ),
    EffectTemplate(
        "type_absorb_hp",
        "Type Absorb → Heal HP",
        "Absorbs moves of a type and heals HP instead of taking damage (like Water Absorb)",
        [EffectParam("type", "Absorb type", TYPE_CHOICES)],
    ),
    EffectTemplate(
        "type_absorb_boost",
        "Type Absorb → Power Boost",
        "Absorbs moves of a type and gains a power boost (like Flash Fire)",
        [EffectParam("type", "Absorb type", TYPE_CHOICES)],
    ),
    EffectTemplate(
        "weather_switchin",
        "Set Weather on Switch-In",
        "Changes weather when entering battle (like Drizzle, Drought, Sand Stream)",
        [EffectParam("weather", "Weather", WEATHER_CHOICES)],
    ),
    EffectTemplate(
        "stat_boost_eot",
        "End-of-Turn Stat Boost",
        "Raises a stat by one stage at the end of each turn (like Speed Boost)",
        [EffectParam("stat", "Stat to boost", STAT_CHOICES)],
    ),
    EffectTemplate(
        "intimidate",
        "Intimidate (Lower Foe's Stat on Switch-In)",
        "Lowers the opponent's stat by one stage when entering battle",
        [EffectParam("stat", "Stat to lower", STAT_CHOICES)],
    ),
    EffectTemplate(
        "contact_recoil",
        "Contact Recoil Damage",
        "Damages the attacker when hit by a contact move (like Rough Skin)",
        [EffectParam("fraction", "Damage", FRACTION_CHOICES)],
    ),
    EffectTemplate(
        "pinch_type_boost",
        "Low-HP Type Power Boost",
        "Boosts moves of a type by 50% when HP is below 1/3 (like Overgrow, Blaze, Torrent)",
        [EffectParam("type", "Move type boosted", TYPE_CHOICES)],
    ),
    EffectTemplate(
        "type_immunity",
        "Type Immunity",
        "Completely immune to moves of a type (like Levitate blocks Ground)",
        [EffectParam("type", "Immune to type", TYPE_CHOICES)],
    ),
    EffectTemplate(
        "weather_recovery",
        "Weather HP Recovery",
        "Recovers HP each turn during a specific weather (like Rain Dish)",
        [
            EffectParam("weather", "Required weather", WEATHER_SPEED_CHOICES),
            EffectParam("fraction", "HP recovered", FRACTION_CHOICES),
        ],
    ),
    EffectTemplate(
        "type_trap",
        "Trap Type (Prevent Fleeing)",
        "Prevents Pokemon of a specific type from fleeing or switching (like Magnet Pull)",
        [EffectParam("type", "Trapped type", TYPE_CHOICES)],
    ),
    EffectTemplate(
        "crit_prevention",
        "Critical Hit Prevention",
        "Prevents the opponent's moves from landing critical hits (like Battle Armor)",
        [],
    ),
]

# ── Field effect templates ──────────────────────────────────────────────────

FIELD_TEMPLATES: list[EffectTemplate] = [
    EffectTemplate(
        "encounter_halve",
        "Reduce Wild Encounter Rate",
        "Halves the wild encounter rate when this Pokemon leads the party (like Stench)",
        [],
    ),
    EffectTemplate(
        "encounter_double",
        "Increase Wild Encounter Rate",
        "Doubles the wild encounter rate when this Pokemon leads the party (like Illuminate)",
        [],
    ),
    EffectTemplate(
        "type_encounter",
        "Increase Type Encounter Rate",
        "50% chance to force wild encounters to match a specific type (like Magnet Pull for Steel, Static for Electric)",
        [EffectParam("type", "Attract type", TYPE_CHOICES)],
    ),
    EffectTemplate(
        "pickup",
        "Post-Battle Item Pickup",
        "Chance to find an item after battle if not already holding one (like Pickup)",
        [EffectParam("chance", "Chance", [
            ("10% (standard)", 10),
            ("20%", 5),
            ("30%", 3),
            ("50%", 2),
        ])],
    ),
    EffectTemplate(
        "guaranteed_escape",
        "Guaranteed Wild Escape",
        "Always successfully flee from wild battles (like Run Away)",
        [],
    ),
    EffectTemplate(
        "egg_hatch_speed",
        "Faster Egg Hatching",
        "Halves the number of steps needed to hatch eggs when in the party (like Flame Body / Magma Armor)",
        [],
    ),
    EffectTemplate(
        "nature_sync",
        "Nature Sync (Wild Encounters)",
        "50% chance wild encounters match this Pokemon's nature when leading the party (like Synchronize)",
        [],
    ),
    EffectTemplate(
        "gender_attract",
        "Gender Attract (Wild Encounters)",
        "66% chance wild encounters are the opposite gender of the lead Pokemon (like Cute Charm)",
        [],
    ),
]

# Lookup dicts
BATTLE_TEMPLATE_MAP: dict[str, EffectTemplate] = {t.id: t for t in BATTLE_TEMPLATES}
FIELD_TEMPLATE_MAP: dict[str, EffectTemplate] = {t.id: t for t in FIELD_TEMPLATES}


# ═══════════════════════════════════════════════════════════════════════════════
# Detection — parse C source to identify what template+params an ability uses
# ═══════════════════════════════════════════════════════════════════════════════

def _read_file(path: str) -> str:
    """Read a file, return empty string if missing."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _extract_ability_case_block(source: str, ability_const: str) -> str:
    """Extract a 'case ABILITY_XXX: ... break;' block from C source."""
    pat = re.compile(
        r'^\s*case\s+' + re.escape(ability_const) + r'\s*:',
        re.MULTILINE,
    )
    m = pat.search(source)
    if not m:
        return ""
    start = m.start()
    # Find the break; that ends this case
    depth = 0
    i = m.end()
    while i < len(source):
        ch = source[i]
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
        elif depth == 0 and source[i:i+6] == 'break;':
            return source[start:i+6]
        i += 1
    return source[start:i]


def detect_battle_effect(project_root: str, ability_const: str
                         ) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Detect which battle effect template an ability matches.

    Returns (template_id, {param_id: value_key}) or None if unrecognised.
    """
    battle_util = _read_file(os.path.join(project_root, "src", "battle_util.c"))

    block = _extract_ability_case_block(battle_util, ability_const)
    if not block:
        # Check pokemon.c for pinch boosts
        pokemon_c = _read_file(os.path.join(project_root, "src", "pokemon.c"))
        for display, type_const in TYPE_CHOICES:
            pat = (r'type\s*==\s*' + re.escape(type_const) + r'\s*&&\s*'
                   r'attacker->ability\s*==\s*' + re.escape(ability_const))
            if re.search(pat, pokemon_c):
                return ("pinch_type_boost", {"type": type_const})

        # Check battle_script_commands.c for type immunity (Levitate pattern)
        bsc = _read_file(os.path.join(
            project_root, "src", "battle_script_commands.c"))
        pat = (r'ability\s*==\s*' + re.escape(ability_const) +
               r'\s*&&\s*moveType\s*==\s*(TYPE_\w+)')
        m = re.search(pat, bsc)
        if m:
            return ("type_immunity", {"type": m.group(1)})

        # Check battle_main.c for type trap (Magnet Pull pattern)
        # Pattern: ABILITY_XXX appears, then IS_BATTLER_OF_TYPE(..., TYPE_YYY)
        battle_main = _read_file(os.path.join(
            project_root, "src", "battle_main.c"))
        pat = (re.escape(ability_const) +
               r'.*?IS_BATTLER_OF_TYPE\([^,]+,\s*(TYPE_\w+)\)')
        m = re.search(pat, battle_main, re.DOTALL)
        if m:
            return ("type_trap", {"type": m.group(1)})

        return None

    # ── Type Absorb Power Boost (Flash Fire pattern) — check BEFORE immunity
    # because Flash Fire's block contains STATUS1_FREEZE as a condition, not
    # as an immunity.
    if "RESOURCE_FLAG_FLASH_FIRE" in block or "FlashFireBoost" in block:
        abs_m = re.search(r'moveType\s*==\s*(TYPE_\w+)', block)
        if abs_m:
            return ("type_absorb_boost", {"type": abs_m.group(1)})

    # ── Type Absorb HP (caseID 3) ──
    abs_m = re.search(r'moveType\s*==\s*(TYPE_\w+).*?MoveHPDrain', block,
                      re.DOTALL)
    if abs_m:
        return ("type_absorb_hp", {"type": abs_m.group(1)})

    # ── Contact Status Infliction (caseID 4) ──
    for display, move_effect in CONTACT_STATUS_CHOICES:
        if move_effect in block and "FLAG_MAKES_CONTACT" in block:
            # Detect chance
            chance_m = re.search(r'Random\(\)\s*%\s*(\d+)', block)
            chance_val = int(chance_m.group(1)) if chance_m else 3
            return ("contact_status", {"status": move_effect,
                                       "chance": chance_val})

    # ── Status Immunity (caseID 5) ──
    # Check for the immunity pattern: status field check + StringCopy + effect = N
    # Must have StringCopy to distinguish from other uses of status flags.
    for display, info in STATUS_CHOICES:
        flag_pattern = info["flags"].split("|")[0].strip()
        if flag_pattern in block and info["string"] in block:
            return ("status_immunity", {"status": display})

    # ── Weather Switch-In (caseID 0) ──
    for display, info in WEATHER_CHOICES:
        if info["script"] in block:
            return ("weather_switchin", {"weather": display})

    # ── Stat Boost End-of-Turn (caseID 1) ──
    stat_m = re.search(r'statStages\[(STAT_\w+)\]', block)
    if stat_m and "MAX_STAT_STAGE" in block and "++" in block:
        return ("stat_boost_eot", {"stat": stat_m.group(1)})

    # ── Intimidate ──
    if "STATUS3_INTIMIDATE_POKES" in block:
        return ("intimidate", {"stat": "STAT_ATK"})

    # ── Contact Recoil ──
    if "FLAG_MAKES_CONTACT" in block and "gBattleMoveDamage" in block:
        frac_m = re.search(r'maxHP\s*/\s*(\d+)', block)
        frac = int(frac_m.group(1)) if frac_m else 16
        return ("contact_recoil", {"fraction": frac})

    # ── Weather HP Recovery ──
    weather_m = re.search(r'gBattleWeather\s*&\s*(B_WEATHER_\w+)', block)
    if weather_m and "gBattleMoveDamage" in block and "*= -1" in block:
        frac_m = re.search(r'maxHP\s*/\s*(\d+)', block)
        frac = int(frac_m.group(1)) if frac_m else 16
        return ("weather_recovery", {
            "weather": weather_m.group(1), "fraction": frac})

    # ── Critical Hit Prevention ──
    if "ABILITY_BATTLE_ARMOR" in block or "ABILITY_SHELL_ARMOR" in block:
        return ("crit_prevention", {})

    return None


def detect_field_effect(project_root: str, ability_const: str
                        ) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Detect which field effect template an ability matches."""

    # ── Encounter rate modification (wild_encounter.c) ──
    wild_enc = _read_file(os.path.join(
        project_root, "src", "wild_encounter.c"))

    func_m = re.search(
        r'GetAbilityEncounterRateModType.*?\n\}',
        wild_enc, re.DOTALL,
    )
    if func_m:
        func_body = func_m.group(0)
        if ability_const in func_body:
            # Find which abilityEffect value is assigned
            pat = re.compile(
                re.escape(ability_const) + r'[^;]*abilityEffect\s*=\s*(\d+)',
                re.DOTALL,
            )
            m = pat.search(func_body)
            if not m:
                lines = func_body.split('\n')
                for i, line in enumerate(lines):
                    if ability_const in line:
                        block = '\n'.join(lines[i:i+3])
                        ae_m = re.search(r'abilityEffect\s*=\s*(\d+)', block)
                        if ae_m:
                            m = ae_m
                            break
            if m:
                val = int(m.group(1))
                if val == 1:
                    return ("encounter_halve", {})
                elif val == 2:
                    return ("encounter_double", {})

    # ── Type encounter bias (wild_encounter.c) ──
    if ability_const in wild_enc:
        type_m = re.search(
            re.escape(ability_const) + r'.*?(?:type1|type2)\s*==\s*(TYPE_\w+)',
            wild_enc, re.DOTALL,
        )
        if type_m:
            return ("type_encounter", {"type": type_m.group(1)})

    # ── Nature sync (wild_encounter.c) ──
    if ability_const in wild_enc and "GetNatureFromPersonality" in wild_enc:
        # Check if this ability is referenced near a nature sync block
        pat = re.escape(ability_const) + r'.*?GetNatureFromPersonality'
        if re.search(pat, wild_enc, re.DOTALL):
            return ("nature_sync", {})

    # ── Gender attract (wild_encounter.c) ──
    if ability_const in wild_enc and "MON_MALE" in wild_enc:
        pat = re.escape(ability_const) + r'.*?MON_MALE'
        if re.search(pat, wild_enc, re.DOTALL):
            return ("gender_attract", {})

    # ── Pickup (battle_script_commands.c) ──
    bsc = _read_file(os.path.join(
        project_root, "src", "battle_script_commands.c"))
    if ability_const in bsc:
        # Must appear on same line as sPickupItems or within ~5 lines
        bsc_lines = bsc.split('\n')
        for i, line in enumerate(bsc_lines):
            if ability_const in line:
                nearby = '\n'.join(bsc_lines[i:i+10])
                if 'sPickupItems' in nearby:
                    chance_m = re.search(
                        r'Random\(\)\s*%\s*(\d+)', nearby)
                    chance_val = int(chance_m.group(1)) if chance_m else 10
                    return ("pickup", {"chance": chance_val})

    # ── Guaranteed escape (battle_main.c) ──
    battle_main = _read_file(os.path.join(
        project_root, "src", "battle_main.c"))
    if ability_const in battle_main:
        # Must appear on same line or adjacent line as BATTLE_RUN_SUCCESS
        bm_lines = battle_main.split('\n')
        for i, line in enumerate(bm_lines):
            if ability_const in line:
                nearby = '\n'.join(bm_lines[max(0, i-2):i+3])
                if 'BATTLE_RUN_SUCCESS' in nearby:
                    return ("guaranteed_escape", {})

    # ── Egg hatch speed (daycare.c) ──
    daycare = _read_file(os.path.join(project_root, "src", "daycare.c"))
    if ability_const in daycare:
        return ("egg_hatch_speed", {})

    return None


# ═══════════════════════════════════════════════════════════════════════════════
# Code generation — produce C code snippets from template + params
# ═══════════════════════════════════════════════════════════════════════════════

def _get_status_info(status_display: str) -> dict:
    """Look up status info dict by display name."""
    for display, info in STATUS_CHOICES:
        if display == status_display:
            return info
    return STATUS_CHOICES[0][1]


def _get_weather_info(weather_display: str) -> dict:
    """Look up weather info dict by display name."""
    for display, info in WEATHER_CHOICES:
        if display == weather_display:
            return info
    return WEATHER_CHOICES[0][1]


def generate_battle_code(template_id: str, ability_const: str,
                         params: Dict[str, Any]) -> list[Tuple[str, str]]:
    """Generate C code for a battle effect.

    Returns list of (relative_file_path, code_snippet) pairs.
    The code_snippet is the case block (or inline code) to insert.
    """
    result: list[Tuple[str, str]] = []

    if template_id == "status_immunity":
        info = _get_status_info(params.get("status", "Poison"))
        extra = info["extra"]
        block = (
            f"                case {ability_const}:\n"
            f"                    if (gBattleMons[battler].{info['field']} "
            f"& ({info['flags']}))\n"
            f"                    {{\n"
            f"{extra}"
            f"                        StringCopy(gBattleTextBuff1, "
            f"{info['string']});\n"
            f"                        effect = {info['effect']};\n"
            f"                    }}\n"
            f"                    break;"
        )
        result.append(("src/battle_util.c", block))

    elif template_id == "contact_status":
        move_effect = params.get("status", "MOVE_EFFECT_POISON")
        chance = params.get("chance", 3)
        block = (
            f"            case {ability_const}:\n"
            f"                if (!(gMoveResultFlags & MOVE_RESULT_NO_EFFECT)\n"
            f"                 && gBattleMons[gBattlerAttacker].hp != 0\n"
            f"                 && !gProtectStructs[gBattlerAttacker].confusionSelfDmg\n"
            f"                 && TARGET_TURN_DAMAGED\n"
            f"                 && (gBattleMoves[move].flags & FLAG_MAKES_CONTACT)\n"
            f"                 && (Random() % {chance}) == 0)\n"
            f"                {{\n"
            f"                    gBattleCommunication[MOVE_EFFECT_BYTE] = "
            f"MOVE_EFFECT_AFFECTS_USER | {move_effect};\n"
            f"                    BattleScriptPushCursor();\n"
            f"                    gBattlescriptCurrInstr = "
            f"BattleScript_ApplySecondaryEffect;\n"
            f"                    gHitMarker |= HITMARKER_STATUS_ABILITY_EFFECT;\n"
            f"                    effect++;\n"
            f"                }}\n"
            f"                break;"
        )
        result.append(("src/battle_util.c", block))

    elif template_id == "type_absorb_hp":
        type_const = params.get("type", "TYPE_WATER")
        block = (
            f"                case {ability_const}:\n"
            f"                    if (moveType == {type_const} "
            f"&& gBattleMoves[move].power != 0)\n"
            f"                    {{\n"
            f"                        if (gProtectStructs[gBattlerAttacker]"
            f".notFirstStrike)\n"
            f"                            gBattlescriptCurrInstr = "
            f"BattleScript_MoveHPDrain;\n"
            f"                        else\n"
            f"                            gBattlescriptCurrInstr = "
            f"BattleScript_MoveHPDrain_PPLoss;\n"
            f"\n"
            f"                        effect = 1;\n"
            f"                    }}\n"
            f"                    break;"
        )
        result.append(("src/battle_util.c", block))

    elif template_id == "type_absorb_boost":
        type_const = params.get("type", "TYPE_FIRE")
        block = (
            f"                case {ability_const}:\n"
            f"                    if (moveType == {type_const} "
            f"&& !(gBattleResources->flags->flags[battler] "
            f"& RESOURCE_FLAG_FLASH_FIRE))\n"
            f"                    {{\n"
            f"                        gBattleResources->flags->flags[battler] "
            f"|= RESOURCE_FLAG_FLASH_FIRE;\n"
            f"                        BattleScriptPushCursorAndCallback("
            f"BattleScript_FlashFireBoost);\n"
            f"                        gBattleScripting.battler = battler;\n"
            f"                        effect++;\n"
            f"                    }}\n"
            f"                    break;"
        )
        result.append(("src/battle_util.c", block))

    elif template_id == "weather_switchin":
        info = _get_weather_info(params.get("weather", "Rain"))
        block = (
            f"            case {ability_const}:\n"
            f"                if (!(gBattleWeather & {info['check']}))\n"
            f"                {{\n"
            f"                    gBattleWeather = {info['set']};\n"
            f"                    BattleScriptPushCursorAndCallback("
            f"{info['script']});\n"
            f"                    gBattleScripting.battler = battler;\n"
            f"                    effect++;\n"
            f"                }}\n"
            f"                break;"
        )
        result.append(("src/battle_util.c", block))

    elif template_id == "stat_boost_eot":
        stat = params.get("stat", "STAT_SPEED")
        block = (
            f"                case {ability_const}:\n"
            f"                    if (gBattleMons[battler].statStages"
            f"[{stat}] < MAX_STAT_STAGE"
            f" && gDisableStructs[battler].isFirstTurn != 2)\n"
            f"                    {{\n"
            f"                        gBattleMons[battler].statStages"
            f"[{stat}]++;\n"
            f"                        gBattleScripting.animArg1 = "
            f"14 + {stat};\n"
            f"                        gBattleScripting.animArg2 = 0;\n"
            f"                        BattleScriptPushCursorAndCallback("
            f"BattleScript_SpeedBoostActivates);\n"
            f"                        gBattleScripting.battler = battler;\n"
            f"                        effect++;\n"
            f"                    }}\n"
            f"                    break;"
        )
        result.append(("src/battle_util.c", block))

    elif template_id == "intimidate":
        # Intimidate uses a special flag + separate case handlers
        # We generate the switch-in case that sets the flag
        block = (
            f"            case {ability_const}:\n"
            f"                if (!gSpecialStatuses[battler].intimitatedMon)\n"
            f"                {{\n"
            f"                    gStatuses3[battler] |= "
            f"STATUS3_INTIMIDATE_POKES;\n"
            f"                    gSpecialStatuses[battler]"
            f".intimitatedMon = TRUE;\n"
            f"                }}\n"
            f"                break;"
        )
        result.append(("src/battle_util.c", block))

    elif template_id == "contact_recoil":
        frac = params.get("fraction", 16)
        block = (
            f"            case {ability_const}:\n"
            f"                if (!(gMoveResultFlags & "
            f"MOVE_RESULT_NO_EFFECT)\n"
            f"                 && gBattleMons[gBattlerAttacker].hp != 0\n"
            f"                 && !gProtectStructs[gBattlerAttacker]"
            f".confusionSelfDmg\n"
            f"                 && TARGET_TURN_DAMAGED\n"
            f"                 && (gBattleMoves[move].flags & "
            f"FLAG_MAKES_CONTACT))\n"
            f"                {{\n"
            f"                    gBattleMoveDamage = "
            f"gBattleMons[gBattlerAttacker].maxHP / {frac};\n"
            f"                    if (gBattleMoveDamage == 0)\n"
            f"                        gBattleMoveDamage = 1;\n"
            f"                    BattleScriptPushCursor();\n"
            f"                    gBattlescriptCurrInstr = "
            f"BattleScript_RoughSkinActivates;\n"
            f"                    effect++;\n"
            f"                }}\n"
            f"                break;"
        )
        result.append(("src/battle_util.c", block))

    elif template_id == "pinch_type_boost":
        type_const = params.get("type", "TYPE_GRASS")
        code = (
            f"    if (type == {type_const} && attacker->ability == "
            f"{ability_const} && attacker->hp <= (attacker->maxHP / 3))\n"
            f"        gBattleMovePower = (150 * gBattleMovePower) / 100;"
        )
        result.append(("src/pokemon.c", code))

    elif template_id == "type_immunity":
        type_const = params.get("type", "TYPE_GROUND")
        # Primary check in type effectiveness calculation
        code = (
            f"    if (gBattleMons[gBattlerTarget].ability == "
            f"{ability_const} && moveType == {type_const})\n"
            f"    {{\n"
            f"        gLastUsedAbility = gBattleMons[gBattlerTarget].ability;\n"
            f"        gMoveResultFlags |= (MOVE_RESULT_MISSED | "
            f"MOVE_RESULT_DOESNT_AFFECT_FOE);\n"
            f"        gLastLandedMoves[gBattlerTarget] = 0;\n"
            f"        gLastHitByType[gBattlerTarget] = 0;\n"
            f"        gBattleCommunication[MISS_TYPE] = B_MSG_GROUND_MISS;\n"
            f"        RecordAbilityBattle(gBattlerTarget, "
            f"{ability_const});\n"
            f"    }}"
        )
        result.append(("src/battle_script_commands.c", code))

    elif template_id == "weather_recovery":
        weather = params.get("weather", "B_WEATHER_RAIN")
        frac = params.get("fraction", 16)
        block = (
            f"                case {ability_const}:\n"
            f"                    if (WEATHER_HAS_EFFECT && "
            f"(gBattleWeather & {weather})\n"
            f"                     && gBattleMons[battler].maxHP > "
            f"gBattleMons[battler].hp)\n"
            f"                    {{\n"
            f"                        BattleScriptPushCursorAndCallback("
            f"BattleScript_RainDishActivates);\n"
            f"                        gBattleMoveDamage = "
            f"gBattleMons[battler].maxHP / {frac};\n"
            f"                        if (gBattleMoveDamage == 0)\n"
            f"                            gBattleMoveDamage = 1;\n"
            f"                        gBattleMoveDamage *= -1;\n"
            f"                        effect++;\n"
            f"                    }}\n"
            f"                    break;"
        )
        result.append(("src/battle_util.c", block))

    elif template_id == "type_trap":
        type_const = params.get("type", "TYPE_STEEL")
        code = (
            f"        if (IS_BATTLER_OF_TYPE(gActiveBattler, {type_const})"
            f"\n"
            f"         && gBattleMons[i].ability == {ability_const})\n"
            f"            cannotRun = TRUE;"
        )
        result.append(("src/battle_main.c", code))

    elif template_id == "crit_prevention":
        # This is checked inline; we'd add to the existing check
        # The pattern is: if (ability != BATTLE_ARMOR && ability != SHELL_ARMOR)
        # We note it but the actual insertion is more complex
        result.append(("src/battle_script_commands.c",
                       f"// {ability_const}: Critical hit prevention "
                       f"(add to critical hit check)"))

    return result


def generate_field_code(template_id: str, ability_const: str,
                        params: Dict[str, Any]) -> list[Tuple[str, str]]:
    """Generate C code for a field effect.

    Returns list of (relative_file_path, code_snippet) pairs.
    """
    if template_id == "encounter_halve":
        code = (
            f"        else if (ability == {ability_const})\n"
            f"            sWildEncounterData.abilityEffect = 1;"
        )
        return [("src/wild_encounter.c", code)]

    elif template_id == "encounter_double":
        code = (
            f"        else if (ability == {ability_const})\n"
            f"            sWildEncounterData.abilityEffect = 2;"
        )
        return [("src/wild_encounter.c", code)]

    elif template_id == "type_encounter":
        type_const = params.get("type", "TYPE_STEEL")
        # Adds a type-biased encounter check in ChooseWildMonIndex or
        # equivalent. Inserts into wild_encounter.c near the encounter
        # generation code.
        code = (
            f"    // {ability_const}: 50% chance to force {type_const} encounter\n"
            f"    if (GetMonAbility(&gPlayerParty[0]) == {ability_const})\n"
            f"    {{\n"
            f"        if (Random() % 2 == 0)\n"
            f"        {{\n"
            f"            // Scan table for a {type_const} Pokemon\n"
            f"            for (i = 0; i < count; i++)\n"
            f"            {{\n"
            f"                u16 species = wildMons[i].species;\n"
            f"                if (gBaseStats[species].type1 == {type_const}"
            f" || gBaseStats[species].type2 == {type_const})\n"
            f"                {{\n"
            f"                    return i;\n"
            f"                }}\n"
            f"            }}\n"
            f"        }}\n"
            f"    }}"
        )
        return [("src/wild_encounter.c", code)]

    elif template_id == "pickup":
        chance_div = params.get("chance", 10)
        # Adds a Pickup check in the post-battle code
        code = (
            f"        if (ability == {ability_const}"
            f" && species != SPECIES_NONE && species != SPECIES_EGG"
            f" && heldItem == ITEM_NONE && !(Random() % {chance_div}))\n"
            f"        {{\n"
            f"            s32 random = Random() % 100;\n"
            f"\n"
            f"            for (j = 0; j < 15; ++j)\n"
            f"                if (sPickupItems[j].chance > random)\n"
            f"                    break;\n"
            f"            SetMonData(&gPlayerParty[i], MON_DATA_HELD_ITEM,"
            f" &sPickupItems[j]);\n"
            f"        }}"
        )
        return [("src/battle_script_commands.c", code)]

    elif template_id == "guaranteed_escape":
        # Adds ability to the run-away check in battle_main.c
        code = (
            f"     || gBattleMons[gActiveBattler].ability == {ability_const})"
        )
        return [("src/battle_main.c", code)]

    elif template_id == "egg_hatch_speed":
        # Adds a check in daycare.c TryProduceOrHatchEgg or ShouldEggHatch
        # to halve egg steps when this ability is in the party
        code = (
            f"    // {ability_const}: halve egg hatch steps\n"
            f"    {{\n"
            f"        u32 p;\n"
            f"        for (p = 0; p < PARTY_SIZE; p++)\n"
            f"        {{\n"
            f"            if (GetMonAbility(&gPlayerParty[p]) == "
            f"{ability_const})\n"
            f"            {{\n"
            f"                for (i = 0; i < DAYCARE_MON_COUNT; i++)\n"
            f"                {{\n"
            f"                    if (GetBoxMonData(&daycare->mons[i].mon,"
            f" MON_DATA_SANITY_HAS_SPECIES))\n"
            f"                        daycare->mons[i].steps++;\n"
            f"                }}\n"
            f"                break;\n"
            f"            }}\n"
            f"        }}\n"
            f"    }}"
        )
        return [("src/daycare.c", code)]

    elif template_id == "nature_sync":
        # Adds a 50% nature sync check to wild encounter generation
        code = (
            f"    // {ability_const}: 50% chance wild nature matches lead\n"
            f"    if (GetMonAbility(&gPlayerParty[0]) == {ability_const}"
            f" && (Random() % 2) == 0)\n"
            f"    {{\n"
            f"        SetMonData(&gEnemyParty[0], MON_DATA_PERSONALITY,\n"
            f"                   &(u32){{(GetMonData(&gEnemyParty[0],"
            f" MON_DATA_PERSONALITY) / 25 * 25)\n"
            f"                   + GetNatureFromPersonality("
            f"GetMonData(&gPlayerParty[0],"
            f" MON_DATA_PERSONALITY))}});\n"
            f"    }}"
        )
        return [("src/wild_encounter.c", code)]

    elif template_id == "gender_attract":
        # Adds a 66% gender attraction check to wild encounter generation
        code = (
            f"    // {ability_const}: 66% chance wild is opposite gender\n"
            f"    if (GetMonAbility(&gPlayerParty[0]) == {ability_const}"
            f" && (Random() % 3) != 0)\n"
            f"    {{\n"
            f"        u8 leadGender = GetMonGender(&gPlayerParty[0]);\n"
            f"        if (leadGender == MON_MALE || leadGender == MON_FEMALE)"
            f"\n"
            f"        {{\n"
            f"            u8 targetGender = (leadGender == MON_MALE)"
            f" ? MON_FEMALE : MON_MALE;\n"
            f"            // Reroll personality until gender matches"
            f" (max 100 tries)\n"
            f"            u32 personality = GetMonData(&gEnemyParty[0],"
            f" MON_DATA_PERSONALITY);\n"
            f"            u16 species = GetMonData(&gEnemyParty[0],"
            f" MON_DATA_SPECIES);\n"
            f"            s32 tries;\n"
            f"            for (tries = 0; tries < 100; tries++)\n"
            f"            {{\n"
            f"                personality = (personality & ~0xFF)"
            f" | (Random() & 0xFF);\n"
            f"                if (GetGenderFromSpeciesAndPersonality"
            f"(species, personality) == targetGender)\n"
            f"                    break;\n"
            f"            }}\n"
            f"            SetMonData(&gEnemyParty[0],"
            f" MON_DATA_PERSONALITY, &personality);\n"
            f"        }}\n"
            f"    }}"
        )
        return [("src/wild_encounter.c", code)]

    return []


# ═══════════════════════════════════════════════════════════════════════════════
# File manipulation — insert / replace / remove code in C files
# ═══════════════════════════════════════════════════════════════════════════════

def _find_case_block_range(lines: list[str], ability_const: str
                           ) -> Optional[Tuple[int, int]]:
    """Find start/end line indices of a case block in a list of lines."""
    pat = re.compile(r'^\s*case\s+' + re.escape(ability_const) + r'\s*:')
    for i, line in enumerate(lines):
        if pat.match(line):
            # Find the break; that ends this case
            depth = 0
            for j in range(i + 1, len(lines)):
                stripped = lines[j].strip()
                depth += stripped.count('{') - stripped.count('}')
                if 'break;' in stripped and depth <= 0:
                    return (i, j)
                # Next case at same or lesser indent
                if stripped.startswith('case ') or stripped.startswith('default:'):
                    return (i, j - 1)
            return (i, min(i + 20, len(lines) - 1))
    return None


def _find_inline_ability_line(lines: list[str], ability_const: str
                              ) -> Optional[int]:
    """Find a line containing an inline ability check (not a case block)."""
    for i, line in enumerate(lines):
        if ability_const in line and 'case ' not in line:
            return i
    return None


def _find_insertion_point_battle_util(lines: list[str], template_id: str
                                     ) -> Optional[int]:
    """Find where to insert a new case block in AbilityBattleEffects.

    Returns the line number where the new case should be inserted
    (before the closing brace of the relevant switch section).
    """
    # Map templates to the caseID section they belong in.
    # We find the relevant section by looking for known abilities.
    section_markers = {
        "status_immunity": "ABILITY_OBLIVIOUS",   # last in immunity section
        "contact_status": "ABILITY_CUTE_CHARM",    # last in on-damage section
        "type_absorb_hp": "ABILITY_FLASH_FIRE",    # last in absorbing section
        "type_absorb_boost": "ABILITY_FLASH_FIRE",
        "weather_switchin": "ABILITY_DROUGHT",     # last in switch-in section
        "stat_boost_eot": "ABILITY_TRUANT",        # last in end-of-turn
        "intimidate": "ABILITY_FORECAST",          # in switch-in section
        "contact_recoil": "ABILITY_CUTE_CHARM",    # in on-damage section
        "weather_recovery": "ABILITY_TRUANT",      # in end-of-turn section
    }

    marker = section_markers.get(template_id)
    if not marker:
        return None

    # Find the marker ability's break; line
    rng = _find_case_block_range(lines, marker)
    if rng:
        return rng[1] + 1  # Insert after the last break;

    return None


def apply_battle_effect(project_root: str, template_id: str,
                        ability_const: str, params: Dict[str, Any],
                        old_template_id: str = None) -> int:
    """Write battle effect code to the appropriate C files.

    Returns the number of code blocks written.
    """
    code_blocks = generate_battle_code(template_id, ability_const, params)
    written = 0

    for rel_path, snippet in code_blocks:
        filepath = os.path.join(project_root, rel_path)
        if not os.path.isfile(filepath):
            continue

        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()

        # Remove existing code for this ability first
        _remove_ability_from_lines(lines, ability_const)

        # Find insertion point
        if rel_path == "src/battle_util.c" and "case " in snippet:
            insert_at = _find_insertion_point_battle_util(lines, template_id)
            if insert_at is not None:
                # Insert the new case block
                new_lines = [ln + "\n" for ln in snippet.split("\n")]
                for idx, ln in enumerate(new_lines):
                    lines.insert(insert_at + idx, ln)
                written += 1

        elif rel_path == "src/pokemon.c":
            # Insert pinch boost near the other pinch boosts
            # Find the last ABILITY_SWARM or ABILITY_TORRENT line
            insert_at = None
            for i, line in enumerate(lines):
                if "ABILITY_SWARM" in line or "ABILITY_TORRENT" in line:
                    # Find the end of this if-block (next line with gBattleMovePower)
                    for j in range(i, min(i + 3, len(lines))):
                        if "gBattleMovePower" in lines[j]:
                            insert_at = j + 1
                            break
            if insert_at:
                new_lines = [ln + "\n" for ln in snippet.split("\n")]
                for idx, ln in enumerate(new_lines):
                    lines.insert(insert_at + idx, ln)
                written += 1

        elif rel_path == "src/battle_script_commands.c":
            # For type immunity, insert near the ABILITY_LEVITATE check
            insert_at = None
            for i, line in enumerate(lines):
                if "ABILITY_LEVITATE" in line and "moveType" in line:
                    # Find end of this if-block
                    depth = 0
                    for j in range(i, len(lines)):
                        depth += lines[j].count('{') - lines[j].count('}')
                        if depth <= 0 and j > i:
                            insert_at = j + 1
                            break
                    break
            if insert_at:
                new_lines = [ln + "\n" for ln in snippet.split("\n")]
                for idx, ln in enumerate(new_lines):
                    lines.insert(insert_at + idx, ln)
                written += 1

        elif rel_path == "src/battle_main.c":
            # For type trap, insert near ABILITY_MAGNET_PULL
            insert_at = None
            for i, line in enumerate(lines):
                if "ABILITY_MAGNET_PULL" in line:
                    # Find end of this block
                    for j in range(i, min(i + 5, len(lines))):
                        if "cannotRun" in lines[j]:
                            insert_at = j + 1
                            break
                    break
            if insert_at:
                new_lines = [ln + "\n" for ln in snippet.split("\n")]
                for idx, ln in enumerate(new_lines):
                    lines.insert(insert_at + idx, ln)
                written += 1

        with open(filepath, "w", encoding="utf-8") as f:
            f.writelines(lines)

    return written


def apply_field_effect(project_root: str, template_id: str,
                       ability_const: str, params: Dict[str, Any]) -> int:
    """Write field effect code to the appropriate C files.

    Returns the number of code blocks written.
    """
    code_blocks = generate_field_code(template_id, ability_const, params)
    written = 0

    for rel_path, snippet in code_blocks:
        filepath = os.path.join(project_root, rel_path)
        if not os.path.isfile(filepath):
            continue

        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()

        # Remove existing code for this ability in this file
        _remove_ability_from_lines(lines, ability_const)

        # Find insertion point based on which file and template
        insert_at = None

        if rel_path == "src/wild_encounter.c":
            if template_id in ("encounter_halve", "encounter_double"):
                # Insert in GetAbilityEncounterRateModType
                for i, line in enumerate(lines):
                    if "ABILITY_ILLUMINATE" in line and "abilityEffect" in line:
                        insert_at = i + 1
                        break
                    elif "ABILITY_STENCH" in line and "abilityEffect" in line:
                        insert_at = i + 1
            else:
                # type_encounter, nature_sync, gender_attract:
                # Insert near the end of CreateWildMon or equivalent
                # Look for the function that creates gEnemyParty[0]
                for i, line in enumerate(lines):
                    if "CreateMonWithNature" in line or "CreateMon(" in line:
                        # Insert after the block that creates the wild mon
                        depth = 0
                        for j in range(i, len(lines)):
                            depth += lines[j].count('{') - lines[j].count('}')
                            if depth <= 0 and j > i:
                                insert_at = j + 1
                                break
                        break
                # Fallback: insert before the last closing brace of
                # the first function that references gEnemyParty
                if insert_at is None:
                    for i, line in enumerate(lines):
                        if "gEnemyParty" in line:
                            # Go forward to find end of this function
                            for j in range(i + 1, len(lines)):
                                if lines[j].strip() == '}' and \
                                        len(lines[j]) - len(lines[j].lstrip()) == 0:
                                    insert_at = j
                                    break
                            break

        elif rel_path == "src/battle_script_commands.c":
            # Pickup: insert near existing ABILITY_PICKUP code
            for i, line in enumerate(lines):
                if "ABILITY_PICKUP" in line and "sPickupItems" in \
                        ''.join(lines[i:i+10]):
                    # Find the end of this if-block
                    depth = 0
                    for j in range(i, len(lines)):
                        depth += lines[j].count('{') - lines[j].count('}')
                        if depth <= 0 and j > i:
                            insert_at = j + 1
                            break
                    break

        elif rel_path == "src/battle_main.c":
            # Guaranteed escape: insert near ABILITY_RUN_AWAY
            for i, line in enumerate(lines):
                if "ABILITY_RUN_AWAY" in line and "BATTLE_RUN_SUCCESS" in \
                        ''.join(lines[max(0, i-2):i+3]):
                    insert_at = i + 1
                    break

        elif rel_path == "src/daycare.c":
            # Egg hatch: insert at the start of TryProduceOrHatchEgg
            for i, line in enumerate(lines):
                if "TryProduceOrHatchEgg" in line and '{' in line:
                    insert_at = i + 1
                    break
                elif "TryProduceOrHatchEgg" in line:
                    # Opening brace may be on the next line
                    for j in range(i, min(i + 3, len(lines))):
                        if '{' in lines[j]:
                            insert_at = j + 1
                            break
                    break

        if insert_at is not None:
            new_lines = [ln + "\n" for ln in snippet.split("\n")]
            for idx, ln in enumerate(new_lines):
                lines.insert(insert_at + idx, ln)
            written += 1

            with open(filepath, "w", encoding="utf-8") as f:
                f.writelines(lines)

    return written


def remove_battle_effect(project_root: str, ability_const: str) -> int:
    """Remove all battle effect code for an ability from C files.

    Returns the number of blocks removed.
    """
    removed = 0
    for rel_path in ["src/battle_util.c", "src/pokemon.c",
                     "src/battle_script_commands.c", "src/battle_main.c"]:
        filepath = os.path.join(project_root, rel_path)
        if not os.path.isfile(filepath):
            continue

        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()

        count = _remove_ability_from_lines(lines, ability_const)
        if count > 0:
            with open(filepath, "w", encoding="utf-8") as f:
                f.writelines(lines)
            removed += count

    return removed


def remove_field_effect(project_root: str, ability_const: str) -> int:
    """Remove field effect code for an ability from all field-effect files."""
    removed = 0
    for rel_path in ["src/wild_encounter.c", "src/battle_script_commands.c",
                     "src/battle_main.c", "src/daycare.c"]:
        filepath = os.path.join(project_root, rel_path)
        if not os.path.isfile(filepath):
            continue

        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()

        count = _remove_ability_from_lines(lines, ability_const)
        if count > 0:
            with open(filepath, "w", encoding="utf-8") as f:
                f.writelines(lines)
            removed += count

    return removed


def _remove_ability_from_lines(lines: list[str], ability_const: str) -> int:
    """Remove case blocks and inline references for an ability (in-place).

    Returns number of blocks removed.
    """
    removed = 0

    # Remove case blocks
    while True:
        rng = _find_case_block_range(lines, ability_const)
        if not rng:
            break
        del lines[rng[0]:rng[1] + 1]
        removed += 1

    # Remove inline references (single-line or multi-line if blocks)
    i = 0
    while i < len(lines):
        if ability_const in lines[i] and 'case ' not in lines[i]:
            # Check if it's part of a small if-block
            stripped = lines[i].strip()
            if stripped.startswith('if ') or stripped.startswith('else if '):
                # Remove the if-block (look for matching brace or single-line)
                if '{' in lines[i]:
                    depth = lines[i].count('{') - lines[i].count('}')
                    end = i
                    while depth > 0 and end + 1 < len(lines):
                        end += 1
                        depth += lines[end].count('{') - lines[end].count('}')
                    del lines[i:end + 1]
                elif i + 1 < len(lines) and '{' in lines[i + 1]:
                    depth = 0
                    end = i
                    for j in range(i, len(lines)):
                        depth += lines[j].count('{') - lines[j].count('}')
                        if depth <= 0 and j > i:
                            end = j
                            break
                    del lines[i:end + 1]
                else:
                    # Single-line if + next statement line
                    if i + 1 < len(lines) and lines[i + 1].strip().startswith('g'):
                        del lines[i:i + 2]
                    else:
                        del lines[i]
                removed += 1
                continue
        i += 1

    return removed


def _remove_ability_field_line(lines: list[str], ability_const: str) -> int:
    """Remove field effect lines for an ability from wild_encounter.c."""
    removed = 0
    i = 0
    while i < len(lines):
        if ability_const in lines[i] and "abilityEffect" in lines[i]:
            del lines[i]
            removed += 1
            continue
        # Handle two-line patterns (ability on one line, abilityEffect on next)
        if ability_const in lines[i]:
            if i + 1 < len(lines) and "abilityEffect" in lines[i + 1]:
                del lines[i:i + 2]
                removed += 1
                continue
        i += 1
    return removed


# ═══════════════════════════════════════════════════════════════════════════════
# Convenience — full detect for an ability
# ═══════════════════════════════════════════════════════════════════════════════

def detect_all_effects(project_root: str, ability_const: str
                       ) -> Tuple[Optional[Tuple[str, dict]],
                                  Optional[Tuple[str, dict]]]:
    """Detect both battle and field effects for an ability.

    Returns (battle_result, field_result) where each is
    (template_id, params_dict) or None.
    """
    battle = detect_battle_effect(project_root, ability_const)
    field = detect_field_effect(project_root, ability_const)
    return battle, field
