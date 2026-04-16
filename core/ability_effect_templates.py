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

POWER_BOOST_CHOICES: list[tuple[str, int]] = [
    ("No boost", 100),
    ("+20% (Gen 7 Pixilate)", 120),
    ("+30% (Gen 6 Pixilate)", 130),
    ("+50%", 150),
]

SHED_SKIN_CHANCE_CHOICES: list[tuple[str, int]] = [
    ("33% (standard)", 3),
    ("50%", 2),
    ("25%", 4),
    ("20%", 5),
]

# Stat choices that include Accuracy and Evasion for block_specific_stat
FULL_STAT_CHOICES: list[tuple[str, str]] = [
    ("Attack", "STAT_ATK"),
    ("Defense", "STAT_DEF"),
    ("Speed", "STAT_SPEED"),
    ("Sp. Attack", "STAT_SPATK"),
    ("Sp. Defense", "STAT_SPDEF"),
    ("Accuracy", "STAT_ACC"),
    ("Evasion", "STAT_EVASION"),
]

REDIRECT_TYPE_CHOICES: list[tuple[str, str]] = [
    ("Electric", "TYPE_ELECTRIC"),
    ("Water", "TYPE_WATER"),
    ("Fire", "TYPE_FIRE"),
    ("Grass", "TYPE_GRASS"),
    ("Ground", "TYPE_GROUND"),
]

COMBO_PARTNER_CHOICES: list[tuple[str, str]] = [
    ("Plus (partner has Plus)", "ABILITY_PLUS"),
    ("Minus (partner has Minus)", "ABILITY_MINUS"),
]

BOOST_STAT_CHOICES: list[tuple[str, str]] = [
    ("Attack", "attack"),
    ("Defense", "defense"),
    ("Sp. Attack", "spAttack"),
    ("Sp. Defense", "spDefense"),
]

# Multiple stats for dual-stat lowering (Scare = Atk + SpAtk)
DUAL_STAT_CHOICES: list[tuple[str, list[str]]] = [
    ("Attack + Sp. Attack", ["STAT_ATK", "STAT_SPATK"]),
    ("Attack + Speed", ["STAT_ATK", "STAT_SPEED"]),
    ("Defense + Sp. Defense", ["STAT_DEF", "STAT_SPDEF"]),
    ("Attack + Defense", ["STAT_ATK", "STAT_DEF"]),
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
    EffectTemplate(
        "ohko_prevention",
        "One-Hit KO Prevention",
        "Prevents one-hit KO moves from affecting this Pokemon (like Sturdy)",
        [],
    ),
    EffectTemplate(
        "evasion_weather",
        "Evasion Boost in Weather",
        "Raises evasion during a specific weather and grants immunity to that weather's damage (like Sand Veil)",
        [EffectParam("weather", "Weather", WEATHER_SPEED_CHOICES)],
    ),
    EffectTemplate(
        "stat_double",
        "Double a Stat",
        "Permanently doubles a stat in battle (like Huge Power doubles Attack)",
        [EffectParam("stat", "Stat to double", STAT_CHOICES)],
    ),
    EffectTemplate(
        "type_resist_halve",
        "Halve Damage from Types",
        "Halves damage from specific types (like Thick Fat halves Fire/Ice)",
        [EffectParam("type", "Resist type", TYPE_CHOICES)],
    ),
    EffectTemplate(
        "block_stat_reduction",
        "Block Stat Reduction",
        "Prevents opponents from lowering this Pokemon's stats (like Clear Body / White Smoke)",
        [],
    ),
    EffectTemplate(
        "block_flinch",
        "Block Flinching",
        "Prevents this Pokemon from flinching (like Inner Focus)",
        [],
    ),
    EffectTemplate(
        "accuracy_boost",
        "Accuracy Boost",
        "Raises this Pokemon's accuracy (like Compound Eyes)",
        [],
    ),
    EffectTemplate(
        "guts_boost",
        "Status Attack Boost",
        "Raises Attack when affected by a status condition (like Guts)",
        [],
    ),
    EffectTemplate(
        "weather_speed",
        "Double Speed in Weather",
        "Doubles Speed during a specific weather (like Swift Swim, Chlorophyll)",
        [EffectParam("weather", "Weather", WEATHER_SPEED_CHOICES)],
    ),
    EffectTemplate(
        "prevent_escape",
        "Prevent Foe Escape",
        "Prevents the opponent from fleeing or switching (like Shadow Tag / Arena Trap)",
        [],
    ),
    EffectTemplate(
        "natural_cure",
        "Cure Status on Switch-Out",
        "Cures status conditions when switching out (like Natural Cure)",
        [],
    ),
    EffectTemplate(
        "pressure",
        "Extra PP Drain",
        "Foe's moves use 2 PP instead of 1 (like Pressure)",
        [],
    ),
    EffectTemplate(
        "wonder_guard",
        "Only Super-Effective Hits",
        "Only super-effective moves deal damage (like Wonder Guard)",
        [],
    ),
    EffectTemplate(
        "recoil_immunity",
        "Recoil Immunity",
        "Prevents recoil damage from own moves (like Rock Head)",
        [],
    ),
    EffectTemplate(
        "type_change_boost",
        "Move Type Change + Boost (Pixilate / Aerilate / Refrigerate)",
        "Changes moves of one type into another type, with an optional power boost. "
        "Example: Pixilate converts Normal moves to Fairy with +30% power.",
        [
            EffectParam("source_type", "Convert FROM", TYPE_CHOICES),
            EffectParam("target_type", "Convert TO", TYPE_CHOICES),
            EffectParam("boost", "Power boost", POWER_BOOST_CHOICES),
        ],
    ),
    EffectTemplate(
        "intimidate_dual",
        "Dual Stat Intimidate (Lower Two Foe Stats on Switch-In)",
        "Lowers TWO of the opponent's stats by one stage when entering battle. "
        "Example: 'Scare' lowers both Attack and Sp. Attack.",
        [EffectParam("stats", "Stats to lower", DUAL_STAT_CHOICES)],
    ),
    EffectTemplate(
        "switchin_field_effect",
        "Set Field Effect on Switch-In (Trick Room / Tailwind)",
        "Sets a field condition when entering battle. IMPORTANT: These "
        "effects do NOT exist in vanilla pokefirered (Gen 3). The code "
        "preview shows exactly what struct fields, constants, and battle "
        "scripts you need to add to your project first.",
        [EffectParam("effect", "Field effect", [
            ("Trick Room (reverse speed)", "TRICK_ROOM"),
            ("Tailwind (double team speed)", "TAILWIND"),
        ])],
    ),
    EffectTemplate(
        "multi_type_resist",
        "Resist Multiple Types",
        "Halves damage from two specific types (like Thick Fat resists Fire and Ice)",
        [
            EffectParam("type1", "Resist type 1", TYPE_CHOICES),
            EffectParam("type2", "Resist type 2", TYPE_CHOICES),
        ],
    ),
    # ── New templates for previously uneditable abilities ──────────────────
    EffectTemplate(
        "weather_suppress",
        "Suppress Weather on Switch-In",
        "Negates all weather effects while this Pokemon is on the field (like Cloud Nine / Air Lock)",
        [],
    ),
    EffectTemplate(
        "shed_skin",
        "End-of-Turn Status Cure (Random)",
        "Has a chance to cure own status condition at the end of each turn (like Shed Skin)",
        [EffectParam("chance", "Chance per turn", SHED_SKIN_CHANCE_CHOICES)],
    ),
    EffectTemplate(
        "truant",
        "Loaf Every Other Turn",
        "Can only attack every other turn (like Truant — Slaking's drawback ability)",
        [],
    ),
    EffectTemplate(
        "sound_block",
        "Block Sound-Based Moves",
        "Immune to all sound-based moves (like Soundproof)",
        [],
    ),
    EffectTemplate(
        "color_change",
        "Change Type When Hit",
        "Changes own type to match the type of the last move that hit this Pokemon (like Color Change)",
        [],
    ),
    EffectTemplate(
        "synchronize_status",
        "Pass Status to Attacker",
        "When poisoned, burned, or paralyzed, inflicts the same status on the attacker (like Synchronize)",
        [],
    ),
    EffectTemplate(
        "suction_cups",
        "Block Forced Switching",
        "Prevents being forced to switch out by moves like Roar or Whirlwind (like Suction Cups)",
        [],
    ),
    EffectTemplate(
        "sticky_hold",
        "Block Item Theft",
        "Prevents opponents from stealing or removing this Pokemon's held item (like Sticky Hold)",
        [],
    ),
    EffectTemplate(
        "shield_dust",
        "Block Secondary Move Effects",
        "Prevents secondary effects of opponent's moves (flinch, stat drops, status) from activating (like Shield Dust)",
        [],
    ),
    EffectTemplate(
        "lightning_rod",
        "Redirect Moves of a Type",
        "In double battles, draws all moves of a type to this Pokemon (like Lightning Rod redirects Electric)",
        [EffectParam("type", "Redirected type", REDIRECT_TYPE_CHOICES)],
    ),
    EffectTemplate(
        "serene_grace",
        "Double Secondary Effect Chance",
        "Doubles the chance of a move's secondary effect triggering (like Serene Grace)",
        [],
    ),
    EffectTemplate(
        "hustle",
        "Boost Attack / Lower Accuracy",
        "Raises physical Attack by 50% but lowers accuracy of physical moves by 20% (like Hustle)",
        [],
    ),
    EffectTemplate(
        "marvel_scale",
        "Status Defense Boost",
        "Raises Defense by 50% when affected by a status condition (like Marvel Scale)",
        [EffectParam("stat", "Stat to boost", BOOST_STAT_CHOICES)],
    ),
    EffectTemplate(
        "early_bird",
        "Wake From Sleep Faster",
        "Wakes up from sleep in half the normal time (like Early Bird)",
        [],
    ),
    EffectTemplate(
        "liquid_ooze",
        "Drain Moves Hurt Attacker",
        "When hit by a draining move (Absorb, Giga Drain, etc.), the attacker takes damage instead of healing (like Liquid Ooze)",
        [],
    ),
    EffectTemplate(
        "plus_minus",
        "Combo Sp. Attack Boost",
        "Boosts Sp. Attack by 50% when an ally with the partner ability is on the field (like Plus/Minus)",
        [EffectParam("partner", "Partner ability", COMBO_PARTNER_CHOICES)],
    ),
    EffectTemplate(
        "damp",
        "Block Explosion Moves",
        "Prevents any Pokemon on the field from using Self-Destruct or Explosion (like Damp)",
        [],
    ),
    EffectTemplate(
        "contact_flinch",
        "Flinch Chance on Contact",
        "When hit by a contact move, has a chance to make the attacker flinch (like Stench in Gen 5+)",
        [EffectParam("chance", "Chance", CHANCE_CHOICES)],
    ),
    EffectTemplate(
        "trace",
        "Copy Opponent's Ability",
        "Copies the opposing Pokemon's ability on switch-in (like Trace)",
        [],
    ),
    EffectTemplate(
        "forecast",
        "Change Form With Weather",
        "Changes form/type based on the current weather (like Forecast — Castform's signature ability)",
        [],
    ),
    EffectTemplate(
        "block_specific_stat",
        "Block Specific Stat Reduction",
        "Prevents opponents from lowering a specific stat (like Keen Eye blocks Accuracy drops, Hyper Cutter blocks Attack drops)",
        [EffectParam("stat", "Protected stat", FULL_STAT_CHOICES)],
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


def _get_nearby_block(source: str, ability_const: str,
                      radius: int = 200) -> str:
    """Get a block of source code near the first occurrence of ability_const.

    Returns up to `radius` characters before and after the match, useful for
    detecting inline ability checks that don't use case blocks.
    """
    idx = source.find(ability_const)
    if idx < 0:
        return ""
    start = max(0, idx - radius)
    end = min(len(source), idx + len(ability_const) + radius)
    return source[start:end]


def detect_battle_effect(project_root: str, ability_const: str
                         ) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Detect which battle effect template an ability matches.

    Returns (template_id, {param_id: value_key}) or None if unrecognised.
    """
    battle_util = _read_file(os.path.join(project_root, "src", "battle_util.c"))

    block = _extract_ability_case_block(battle_util, ability_const)
    if not block:
        # Check pokemon.c for pinch boosts AND stat doublers
        pokemon_c = _read_file(os.path.join(project_root, "src", "pokemon.c"))
        for display, type_const in TYPE_CHOICES:
            pat = (r'type\s*==\s*' + re.escape(type_const) + r'\s*&&\s*'
                   r'attacker->ability\s*==\s*' + re.escape(ability_const))
            if re.search(pat, pokemon_c):
                return ("pinch_type_boost", {"type": type_const})

        # Huge Power / Pure Power — doubles attack in damage calc
        if re.search(r'ability\s*==\s*' + re.escape(ability_const)
                     + r'[^;]*attack\s*\*=\s*2', pokemon_c, re.IGNORECASE):
            return ("stat_double", {"stat": "STAT_ATK"})

        # Thick Fat — halves Fire/Ice damage
        # Note: pokemon.c uses local var 'type' not 'moveType'
        if ability_const in pokemon_c:
            for _i, line in enumerate(pokemon_c.split('\n')):
                if ability_const in line:
                    ctx = '\n'.join(pokemon_c.split('\n')[max(0,_i-1):_i+3])
                    type_matches = re.findall(r'(?:type|moveType)\s*==\s*(TYPE_\w+)', ctx)
                    if type_matches and ('/ 2' in ctx or '/= 2' in ctx
                                          or 'spAttack /= 2' in ctx):
                        if len(type_matches) >= 2:
                            return ("multi_type_resist", {
                                "type1": type_matches[0],
                                "type2": type_matches[1],
                            })
                        else:
                            return ("type_resist_halve", {
                                "type": type_matches[0],
                            })

        # Check battle_script_commands.c for various inline patterns.
        # Use TIGHT context — only the line containing the ability constant
        # and a few lines around it — to avoid bleeding into adjacent code.
        bsc = _read_file(os.path.join(
            project_root, "src", "battle_script_commands.c"))
        bsc_lines = bsc.split('\n') if ability_const in bsc else []

        # Build a list of (line_index, tight_context) for each occurrence
        # of this ability in bsc.  Tight context = ±5 lines.
        _bsc_contexts: list[tuple[int, str]] = []
        for i, line in enumerate(bsc_lines):
            if ability_const in line:
                ctx = '\n'.join(bsc_lines[max(0, i-5):i+6])
                _bsc_contexts.append((i, ctx))

        # Type immunity (Levitate pattern) — same line has moveType
        pat = (r'ability\s*==\s*' + re.escape(ability_const) +
               r'\s*&&\s*moveType\s*==\s*(TYPE_\w+)')
        m = re.search(pat, bsc)
        if m:
            return ("type_immunity", {"type": m.group(1)})

        # Wonder Guard — MOVE_RESULT_NOT_VERY_EFFECTIVE on same line
        for _i, ctx in _bsc_contexts:
            if 'MOVE_RESULT_SUPER_EFFECTIVE' in ctx or 'TYPE_MYSTERY' in ctx:
                return ("wonder_guard", {})

        # Sturdy — OHKO prevention (SturdyPreventsOHKO in tight context)
        for _i, ctx in _bsc_contexts:
            if 'SturdyPreventsOHKO' in ctx or 'MOVE_RESULT_MISSED' in ctx:
                return ("ohko_prevention", {})

        # Battle Armor / Shell Armor — critical hit prevention
        for _i, ctx in _bsc_contexts:
            if 'critChance' in ctx or 'CriticalHit' in ctx:
                return ("crit_prevention", {})

        # Compound Eyes — accuracy boost (line has 130 / 100 or similar)
        for _i, ctx in _bsc_contexts:
            line = bsc_lines[_i]
            # Must be on the SAME line as the ability, not just nearby
            if '130' in line or 'Accuracy' in ctx:
                # Exclude if the same line also mentions weather/sandstorm
                if 'WEATHER' not in line and 'SANDSTORM' not in line:
                    return ("accuracy_boost", {})

        # Sand Veil — evasion in sandstorm (same line has SANDSTORM)
        for _i, ctx in _bsc_contexts:
            line = bsc_lines[_i]
            if ('B_WEATHER_SANDSTORM' in line or 'WEATHER_SANDSTORM' in line):
                return ("evasion_weather", {"weather": "B_WEATHER_SANDSTORM"})

        # Inner Focus — block flinching (tight context has flinch)
        for _i, ctx in _bsc_contexts:
            if 'FLINCH' in ctx or 'flinch' in ctx:
                return ("block_flinch", {})

        # Keen Eye / Hyper Cutter — block SPECIFIC stat (in bsc ChangeStatBuffs)
        # Must check BEFORE block_stat_reduction because both have STAT_CHANGE
        # keywords nearby. The distinguishing pattern: `statId == STAT_xxx`
        for _i, ctx in _bsc_contexts:
            if 'statStages' in ctx or 'STAT_CHANGE' in ctx:
                stat_m = re.search(
                    r'statId\s*==\s*(STAT_(?:ATK|DEF|SPEED|SPATK|SPDEF|ACC|EVASION))',
                    ctx)
                if stat_m:
                    return ("block_specific_stat", {"stat": stat_m.group(1)})

        # Clear Body / White Smoke — block stat reduction (in bsc)
        for _i, ctx in _bsc_contexts:
            if 'statStages' in ctx or 'StatDown' in ctx or 'STAT_CHANGE_WORKED' in ctx:
                return ("block_stat_reduction", {})

        # Rock Head — recoil immunity
        for _i, ctx in _bsc_contexts:
            if 'recoil' in ctx.lower() or 'MOVE_EFFECT_RECOIL' in ctx:
                return ("recoil_immunity", {})

        # ── pokemon.c inline patterns — use the SPECIFIC LINE containing
        # the ability constant, not wide context, because Guts/Hustle/
        # Marvel Scale/Plus/Minus are all within 10 lines of each other.
        if ability_const in pokemon_c:
            pc_lines = pokemon_c.split('\n')
            for _i, line in enumerate(pc_lines):
                if ability_const not in line:
                    continue
                # Get tight context: the ability line + 1 line after only
                ctx_tight = '\n'.join(pc_lines[_i:_i+2])

                # Plus / Minus — ABILITY_ON_FIELD2 pattern
                if 'ABILITY_ON_FIELD2' in ctx_tight:
                    partner = None
                    pm = re.search(r'ABILITY_ON_FIELD2\((ABILITY_\w+)\)',
                                   ctx_tight)
                    if pm:
                        partner = pm.group(1)
                    return ("plus_minus", {"partner": partner or "ABILITY_MINUS"})

                # Hustle — attack boost (attacker->ability, attack *= N)
                # Hustle does NOT check status1 — Guts does. Exclude status1.
                if ('attacker' in line and 'attack' in ctx_tight.lower()
                        and ('150' in ctx_tight or '* 3' in ctx_tight)
                        and 'status1' not in ctx_tight):
                    # Exclude if 'spAttack' is what's being modified
                    if 'spAttack' not in ctx_tight:
                        return ("hustle", {})

                # Marvel Scale — defense boost when statused (defender->ability)
                if 'defender' in line and 'status1' in ctx_tight:
                    if 'defense' in ctx_tight.lower():
                        stat = "defense"
                        if 'spDefense' in ctx_tight:
                            stat = "spDefense"
                        return ("marvel_scale", {"stat": stat})

                # Guts — attack boost when statused (attacker->ability)
                if 'attacker' in line and 'status1' in ctx_tight:
                    if 'attack' in ctx_tight.lower():
                        return ("guts_boost", {})

        # Soundproof — check for sSoundMovesTable in battle_util (case ABILITYEFFECT_MOVES_BLOCK)
        if ability_const in battle_util:
            bu_lines = battle_util.split('\n')
            for _i, line in enumerate(bu_lines):
                if ability_const in line:
                    ctx = '\n'.join(bu_lines[max(0,_i-3):_i+4])
                    if 'sSoundMovesTable' in ctx or 'SOUND_MOVES' in ctx:
                        return ("sound_block", {})

        # Keen Eye / Hyper Cutter — block specific stat reduction (battle_util)
        # These block a SPECIFIC stat from being lowered, unlike Clear Body
        # which blocks all stat drops.
        if ability_const in battle_util:
            bu_lines = battle_util.split('\n')
            for _i, line in enumerate(bu_lines):
                if ability_const in line:
                    ctx = '\n'.join(bu_lines[max(0,_i-5):_i+6])
                    if 'STAT_CHANGE_WORKED' in ctx or 'statStages' in ctx:
                        # Try to identify which specific stat is protected
                        stat_m = re.search(r'(STAT_(?:ATK|DEF|SPEED|SPATK|SPDEF|ACC|EVASION))', ctx)
                        if stat_m:
                            return ("block_specific_stat", {
                                "stat": stat_m.group(1)})
                        return ("block_stat_reduction", {})

        # Swift Swim / Chlorophyll — weather speed double (battle_main.c)
        battle_main = _read_file(os.path.join(
            project_root, "src", "battle_main.c"))
        if ability_const in battle_main:
            for _i, line in enumerate(battle_main.split('\n')):
                if ability_const in line:
                    # The SAME LINE must have the weather constant
                    for wdisplay, weather_const in WEATHER_SPEED_CHOICES:
                        if weather_const in line and (
                                'speed' in line.lower() or 'Multiplier' in line
                                or 'speed' in '\n'.join(
                                    battle_main.split('\n')[max(0,_i-2):_i+3]
                                ).lower()):
                            return ("weather_speed", {"weather": weather_const})

        # Pressure — extra PP drain (battle_util.c has PressurePPLose)
        if ability_const in battle_util:
            for _i, line in enumerate(battle_util.split('\n')):
                if ability_const in line:
                    ctx = '\n'.join(battle_util.split('\n')[max(0,_i-5):_i+6])
                    if 'pp' in ctx.lower() or 'PP' in ctx:
                        return ("pressure", {})

        # Type trap (Magnet Pull) — the pattern in pokefirered uses
        # AbilityBattleEffects(..., ABILITY_MAGNET_PULL, ...) then checks
        # IS_BATTLER_OF_TYPE separately.  The key signature: the ability
        # constant appears as an argument to AbilityBattleEffects, and
        # IS_BATTLER_OF_TYPE is nearby with a type constant.
        if ability_const in battle_main:
            bm_lines = battle_main.split('\n')
            for _i, line in enumerate(bm_lines):
                if ability_const in line and 'AbilityBattleEffects' in line:
                    ctx = '\n'.join(bm_lines[max(0,_i):_i+4])
                    type_m = re.search(
                        r'IS_BATTLER_OF_TYPE\([^,]+,\s*(TYPE_\w+)\)', ctx)
                    if type_m:
                        return ("type_trap", {"type": type_m.group(1)})

        # Shadow Tag / Arena Trap — prevent escape.
        # Shadow Tag: unconditional — just checks ability == SHADOW_TAG
        #   then BATTLE_RUN_FAILURE.
        # Arena Trap: conditional — excludes Flying/Levitate, still
        #   prevent_escape since it's not type-trapping a specific type.
        if ability_const in battle_main:
            bm_lines = battle_main.split('\n')
            for _i, line in enumerate(bm_lines):
                if ability_const in line:
                    ctx = '\n'.join(bm_lines[max(0,_i-1):_i+6])
                    if ('BATTLE_RUN_FAILURE' in ctx or 'cannotRun' in ctx
                            or 'PARTY_ACTION_CANT_SWITCH' in ctx):
                        return ("prevent_escape", {})

        # Natural Cure — cure on switch-out
        if ability_const in battle_util:
            for _i, line in enumerate(battle_util.split('\n')):
                if ability_const in line:
                    ctx = '\n'.join(battle_util.split('\n')[max(0,_i-3):_i+4])
                    if 'status1' in ctx and ('= 0' in ctx):
                        return ("natural_cure", {})

        # Type change + boost (Pixilate / Aerilate / Refrigerate pattern)
        # Looks for: ability == ABILITY_XXX && type == TYPE_YYY → dynamicMoveType
        if ability_const in bsc:
            tc_block = _get_nearby_block(bsc, ability_const, 400)
            if 'dynamicMoveType' in tc_block:
                src_m = re.search(r'type\s*==\s*(TYPE_\w+)', tc_block)
                tgt_m = re.search(r'dynamicMoveType\s*=\s*(TYPE_\w+)', tc_block)
                if src_m and tgt_m:
                    boost = 100
                    boost_m = re.search(r'\*\s*(\d+)\s*/\s*100', tc_block)
                    if boost_m:
                        boost = int(boost_m.group(1))
                    return ("type_change_boost", {
                        "source_type": src_m.group(1),
                        "target_type": tgt_m.group(1),
                        "boost": boost,
                    })

        # Dual stat intimidate — looks for STATUS3_INTIMIDATE with two STAT_ refs
        if ability_const in battle_util:
            dual_block = _get_nearby_block(battle_util, ability_const, 400)
            if 'STATUS3_INTIMIDATE' in dual_block:
                stat_matches = re.findall(r'(STAT_\w+)', dual_block)
                unique_stats = list(dict.fromkeys(stat_matches))  # preserve order
                if len(unique_stats) >= 2:
                    return ("intimidate_dual", {"stats": unique_stats[:2]})

        # Field effect on switch-in (Trick Room / Tailwind)
        if ability_const in battle_util:
            fe_block = _get_nearby_block(battle_util, ability_const, 400)
            if 'STATUS_FIELD_TRICK_ROOM' in fe_block or 'trickRoomTimer' in fe_block:
                return ("switchin_field_effect", {"effect": "TRICK_ROOM"})
            if 'SIDE_STATUS_TAILWIND' in fe_block or 'tailwindTimer' in fe_block:
                return ("switchin_field_effect", {"effect": "TAILWIND"})

        # Multi-type resist (Thick Fat variant with two types)
        if ability_const in pokemon_c:
            mt_block = _get_nearby_block(pokemon_c, ability_const, 400)
            type_matches = re.findall(r'moveType\s*==\s*(TYPE_\w+)', mt_block)
            unique_types = list(dict.fromkeys(type_matches))
            if len(unique_types) >= 2 and ('/ 2' in mt_block or '/= 2' in mt_block):
                return ("multi_type_resist", {
                    "type1": unique_types[0],
                    "type2": unique_types[1],
                })

        # ── Serene Grace — doubles secondary effect chance (bsc) ──
        for _i, ctx in _bsc_contexts:
            if 'effectChance' in ctx or 'MOVE_EFFECT' in ctx:
                # Make sure it's doubling / multiplying, not just checking
                if '* 2' in ctx or '*= 2' in ctx or 'Serene' in ctx:
                    return ("serene_grace", {})
        if ability_const == "ABILITY_SERENE_GRACE":
            # Serene Grace's check may be spread — just detect by name
            if ability_const in bsc or ability_const in pokemon_c:
                return ("serene_grace", {})

        # ── Shield Dust — block secondary effects (bsc) ──
        for _i, ctx in _bsc_contexts:
            line = bsc_lines[_i]
            if 'additionalEffects' in ctx or 'secondaryEffect' in ctx:
                return ("shield_dust", {})
        if ability_const == "ABILITY_SHIELD_DUST":
            if ability_const in bsc:
                return ("shield_dust", {})

        # ── Lightning Rod — redirect type (bsc or battle_main) ──
        # Must specifically check for target redirection pattern, not just
        # any TYPE_ constant nearby (which would match too broadly).
        for _i, ctx in _bsc_contexts:
            if ('gBattlerTarget' in ctx and 'moveType' in ctx
                    and ('redirect' in ctx.lower() or 'TARGET' in ctx)):
                type_m = re.search(r'moveType\s*==\s*(TYPE_\w+)', ctx)
                if type_m:
                    return ("lightning_rod", {
                        "type": type_m.group(1)})
        if ability_const == "ABILITY_LIGHTNING_ROD":
            return ("lightning_rod", {"type": "TYPE_ELECTRIC"})

        # ── Sticky Hold — block item theft (bsc) ──
        for _i, ctx in _bsc_contexts:
            if 'item' in ctx.lower() and ('Thief' in ctx or 'Knock' in ctx
                                           or 'StickyHold' in ctx):
                return ("sticky_hold", {})
        if ability_const == "ABILITY_STICKY_HOLD":
            if ability_const in bsc or ability_const in battle_util:
                return ("sticky_hold", {})

        # ── Suction Cups — block forced switching (bsc or battle_main) ──
        for _i, ctx in _bsc_contexts:
            if 'Roar' in ctx or 'Whirlwind' in ctx or 'SuctionCups' in ctx:
                return ("suction_cups", {})
        if ability_const in battle_main:
            bm_lines = battle_main.split('\n')
            for _i, line in enumerate(bm_lines):
                if ability_const in line:
                    ctx = '\n'.join(bm_lines[max(0,_i-3):_i+4])
                    if 'switch' in ctx.lower() or 'Roar' in ctx:
                        return ("suction_cups", {})
        if ability_const == "ABILITY_SUCTION_CUPS":
            if ability_const in bsc or ability_const in battle_util:
                return ("suction_cups", {})

        # ── Contact Flinch — Stench Gen5+ pattern (bsc) ──
        for _i, ctx in _bsc_contexts:
            if 'FLINCH' in ctx and 'Random()' in ctx:
                chance_m = re.search(r'Random\(\)\s*%\s*(\d+)', ctx)
                chance = int(chance_m.group(1)) if chance_m else 10
                return ("contact_flinch", {"chance": chance})

        # ── Damp — block explosion (bsc) ──
        for _i, ctx in _bsc_contexts:
            if 'Damp' in ctx or 'EFFECT_EXPLOSION' in ctx or 'explosion' in ctx.lower():
                return ("damp", {})
        if ability_const == "ABILITY_DAMP":
            if ability_const in bsc:
                return ("damp", {})

        # ── Early Bird — faster sleep recovery (battle_util inline) ──
        if ability_const in battle_util:
            bu_lines = battle_util.split('\n')
            for _i, line in enumerate(bu_lines):
                if ability_const in line:
                    ctx = '\n'.join(bu_lines[max(0,_i-2):_i+3])
                    if 'toSub' in ctx or 'Sleep' in ctx or 'STATUS1_SLEEP' in ctx:
                        return ("early_bird", {})

        # ── Natural Cure — in bsc switch (not battle_util) ──
        if ability_const in bsc:
            bsc_all_lines = bsc.split('\n')
            for _i, line in enumerate(bsc_all_lines):
                if ability_const in line:
                    ctx = '\n'.join(bsc_all_lines[max(0,_i-2):_i+4])
                    if 'status1' in ctx and '= 0' in ctx:
                        return ("natural_cure", {})

        # ── Name-based fallbacks for abilities in assembly (.s) files ──
        # These abilities are implemented in battle scripts, not C code,
        # so source scanning can't find them. Match by name.
        _NAME_FALLBACKS = {
            "ABILITY_LIQUID_OOZE": ("liquid_ooze", {}),
            "ABILITY_SUCTION_CUPS": ("suction_cups", {}),
            "ABILITY_STENCH": ("contact_flinch", {"chance": 10}),
            "ABILITY_CACOPHONY": ("sound_block", {}),
            "ABILITY_ROCK_HEAD": ("recoil_immunity", {}),
            "ABILITY_SYNCHRONIZE": ("synchronize_status", {}),
        }
        if ability_const in _NAME_FALLBACKS:
            return _NAME_FALLBACKS[ability_const]

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
    # Cute Charm uses infatuation (STATUS2_INFATUATED) not MOVE_EFFECT.
    # NOTE: Cute Charm doesn't use the MOVE_EFFECT system — it directly sets
    # STATUS2_INFATUATED_WITH(). Detect as contact_status with a special
    # "INFATUATION" marker so codegen can handle it correctly.
    if "FLAG_MAKES_CONTACT" in block and "STATUS2_INFATUAT" in block:
        chance_m = re.search(r'Random\(\)\s*%\s*(\d+)', block)
        chance_val = int(chance_m.group(1)) if chance_m else 3
        return ("contact_status", {"status": "INFATUATION",
                                   "chance": chance_val})

    # ── Shed Skin (end-of-turn random status cure) ──
    # Must check BEFORE status_immunity because both have StringCopy + status flags.
    # Shed Skin has Random() — immunity does not.
    if "Random()" in block and "STATUS1_ANY" in block and "status1" in block:
        chance_m = re.search(r'Random\(\)\s*%\s*(\d+)', block)
        chance = int(chance_m.group(1)) if chance_m else 3
        return ("shed_skin", {"chance": chance})

    # ── Synchronize (pass status to attacker) ──
    # Must check BEFORE status_immunity. Sync has MOVE_EFFECT_AFFECTS_USER.
    if "MOVE_EFFECT_AFFECTS_USER" in block:
        # Check if it's passing status (sync) not inflicting on contact
        if "FLAG_MAKES_CONTACT" not in block:
            return ("synchronize_status", {})

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

    # ── Weather Suppress (Cloud Nine / Air Lock) ──
    if ("WEATHER_HAS_EFFECT" in block and "gBattleScripting.battler" in block
            and "weather" not in block.lower().replace("weather_has_effect", "")):
        # Cloud Nine / Air Lock just announce presence; weather check handles it
        return ("weather_suppress", {})
    # Broader check: these abilities have a case block but their real effect
    # is that WEATHER_HAS_EFFECT returns FALSE when they're on the field.
    if ability_const in ("ABILITY_CLOUD_NINE", "ABILITY_AIR_LOCK"):
        return ("weather_suppress", {})

    # ── Trace (copy opponent's ability) ──
    if "gLastUsedAbility" in block and ("gBattleMons" in block and
            "ability" in block):
        if "gBattlerAttacker" not in block:  # Not a damage thing — it's a copy
            return ("trace", {})
    if ability_const == "ABILITY_TRACE":
        return ("trace", {})

    # ── Forecast (form change with weather) ──
    if "CastformDataTypeChange" in block or "SPECIES_CASTFORM" in block:
        return ("forecast", {})
    if ability_const == "ABILITY_FORECAST":
        return ("forecast", {})

    # ── Shed Skin (random end-of-turn status cure) ──
    if "Random()" in block and "status1" in block and "= 0" in block:
        chance_m = re.search(r'Random\(\)\s*%\s*(\d+)', block)
        chance = int(chance_m.group(1)) if chance_m else 3
        return ("shed_skin", {"chance": chance})

    # ── Truant (loaf every other turn) ──
    if "truantCounter" in block or "TRUANT" in block.upper():
        return ("truant", {})
    if ability_const == "ABILITY_TRUANT":
        return ("truant", {})

    # ── Soundproof / Cacophony (block sound-based moves) ──
    if "FLAG_SOUND" in block or "SoundMoveFailed" in block:
        return ("sound_block", {})
    if ability_const in ("ABILITY_SOUNDPROOF", "ABILITY_CACOPHONY"):
        return ("sound_block", {})

    # ── Color Change (change type when hit) ──
    if "TYPE_MYSTERY" in block and ("type1" in block or "type2" in block):
        # Color Change checks if the move type is ??? and changes own type
        return ("color_change", {})
    if ability_const == "ABILITY_COLOR_CHANGE":
        return ("color_change", {})

    # ── Synchronize (pass status to attacker) ──
    if ("MOVE_EFFECT_AFFECTS_USER" in block and
            ("MOVE_EFFECT_POISON" in block or "status1" in block)):
        return ("synchronize_status", {})
    if ability_const == "ABILITY_SYNCHRONIZE":
        return ("synchronize_status", {})

    # ── Liquid Ooze (drain reversal) ──
    if "gBattleMoveDamage" in block and "*= -1" in block:
        # Only match if it's NOT a weather recovery (those also have *= -1)
        if not re.search(r'gBattleWeather\s*&', block):
            return ("liquid_ooze", {})

    # ── Damp (block explosion) ──
    if "Explosion" in block or "EFFECT_EXPLOSION" in block or "SELF_DESTRUCT" in block:
        return ("damp", {})
    if ability_const == "ABILITY_DAMP":
        return ("damp", {})

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
        # Cute Charm uses STATUS2_INFATUATED_WITH() directly, not MOVE_EFFECT
        if move_effect == "INFATUATION":
            block = (
                f"            case {ability_const}:\n"
                f"                if (!(gMoveResultFlags & MOVE_RESULT_NO_EFFECT)\n"
                f"                 && gBattleMons[gBattlerAttacker].hp != 0\n"
                f"                 && !gProtectStructs[gBattlerAttacker].confusionSelfDmg\n"
                f"                 && (gBattleMoves[move].flags & FLAG_MAKES_CONTACT)\n"
                f"                 && TARGET_TURN_DAMAGED\n"
                f"                 && gBattleMons[gBattlerTarget].hp != 0\n"
                f"                 && (Random() % {chance}) == 0\n"
                f"                 && gBattleMons[gBattlerAttacker].ability != ABILITY_OBLIVIOUS\n"
                f"                 && !(gBattleMons[gBattlerAttacker].status2 & STATUS2_INFATUATION)\n"
                f"                 && GetGenderFromSpeciesAndPersonality(speciesAtk, pidAtk) != "
                f"GetGenderFromSpeciesAndPersonality(speciesDef, pidDef)\n"
                f"                 && GetGenderFromSpeciesAndPersonality(speciesAtk, pidAtk) != MON_GENDERLESS\n"
                f"                 && GetGenderFromSpeciesAndPersonality(speciesDef, pidDef) != MON_GENDERLESS)\n"
                f"                {{\n"
                f"                    gBattleMons[gBattlerAttacker].status2 |= "
                f"STATUS2_INFATUATED_WITH(gBattlerTarget);\n"
                f"                    BattleScriptPushCursor();\n"
                f"                    gBattlescriptCurrInstr = "
                f"BattleScript_CuteCharmActivates;\n"
                f"                    effect++;\n"
                f"                }}\n"
                f"                break;"
            )
        else:
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
        # Intimidate uses a special flag + separate case handlers.
        # The stat parameter controls which stat is lowered (default ATK).
        # NOTE: In vanilla pokefirered the stat lowered by Intimidate is
        # hardcoded in BattleScript_IntimidateActivates.  For non-ATK stats,
        # a custom battle script would be needed.  The case block sets the
        # flag; the battle script handles the actual stat drop.
        stat = params.get("stat", "STAT_ATK")
        block = (
            f"            case {ability_const}:\n"
            f"                if (!gSpecialStatuses[battler].intimidatedMon)\n"
            f"                {{\n"
            f"                    gStatuses3[battler] |= "
            f"STATUS3_INTIMIDATE_POKES;\n"
            f"                    gSpecialStatuses[battler]"
            f".intimidatedMon = TRUE;\n"
        )
        if stat != "STAT_ATK":
            block += (
                f"                    // Custom stat target: {stat}\n"
                f"                    // Requires a modified "
                f"BattleScript_IntimidateActivates\n"
                f"                    // that reads animArg1 for the stat ID.\n"
                f"                    gBattleScripting.animArg1 = {stat};\n"
            )
        block += (
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
        code = (
            f"    // {ability_const}: Add to critical hit prevention check\n"
            f"    // In the critical hit calculation, add:\n"
            f"    //   && gBattleMons[gBattlerTarget].ability != {ability_const}"
        )
        result.append(("src/battle_script_commands.c", code))

    elif template_id == "ohko_prevention":
        code = (
            f"        if (gBattleMons[gBattlerTarget].ability == {ability_const})\n"
            f"        {{\n"
            f"            gMoveResultFlags |= MOVE_RESULT_MISSED;\n"
            f"            gLastUsedAbility = {ability_const};\n"
            f"            gBattlescriptCurrInstr = BattleScript_SturdyPreventsOHKO;\n"
            f"            RecordAbilityBattle(gBattlerTarget, {ability_const});\n"
            f"        }}"
        )
        result.append(("src/battle_script_commands.c", code))

    elif template_id == "evasion_weather":
        weather = params.get("weather", "B_WEATHER_SANDSTORM")
        code = (
            f"    // {ability_const}: Evasion boost in weather\n"
            f"    if (WEATHER_HAS_EFFECT\n"
            f"     && gBattleMons[gBattlerTarget].ability == {ability_const}\n"
            f"     && gBattleWeather & {weather})\n"
            f"        calc = (calc * 80) / 100;  // 20% accuracy reduction"
        )
        result.append(("src/battle_script_commands.c", code))

    elif template_id == "stat_double":
        stat = params.get("stat", "STAT_ATK")
        # Map stat constants to the actual C variable names in
        # CalculateBaseDamage() in pokemon.c
        _stat_var_map = {
            "STAT_ATK": "attack",
            "STAT_DEF": "defense",
            "STAT_SPEED": "speed",
            "STAT_SPATK": "spAttack",
            "STAT_SPDEF": "spDefense",
        }
        stat_var = _stat_var_map.get(stat, "attack")
        code = (
            f"    // {ability_const}: Doubles {stat} in damage calculation\n"
            f"    if (attacker->ability == {ability_const})\n"
            f"        {stat_var} *= 2;"
        )
        result.append(("src/pokemon.c", code))

    elif template_id == "type_resist_halve":
        type_const = params.get("type", "TYPE_FIRE")
        code = (
            f"    // {ability_const}: Halves damage from {type_const}\n"
            f"    if (defender->ability == {ability_const}\n"
            f"     && (moveType == {type_const}))\n"
            f"        damage /= 2;"
        )
        result.append(("src/pokemon.c", code))

    elif template_id == "block_stat_reduction":
        code = (
            f"    // {ability_const}: Block stat reduction\n"
            f"    case {ability_const}:\n"
            f"        // Add to stat change prevention switch in battle_util.c"
        )
        result.append(("src/battle_util.c", code))

    elif template_id == "block_flinch":
        code = (
            f"    // {ability_const}: Flinch prevention\n"
            f"    if (gBattleMons[gBattlerTarget].ability == {ability_const})\n"
            f"        // Skip flinch application"
        )
        result.append(("src/battle_script_commands.c", code))

    elif template_id == "accuracy_boost":
        code = (
            f"    // {ability_const}: Accuracy boost\n"
            f"    if (gBattleMons[gBattlerAttacker].ability == {ability_const})\n"
            f"        calc = (calc * 130) / 100;  // 30% accuracy boost"
        )
        result.append(("src/battle_script_commands.c", code))

    elif template_id == "guts_boost":
        code = (
            f"    // {ability_const}: Attack boost when statused\n"
            f"    if (attacker->ability == {ability_const}\n"
            f"     && attacker->status1)\n"
            f"        attack = (attack * 150) / 100;"
        )
        result.append(("src/pokemon.c", code))

    elif template_id == "weather_speed":
        weather = params.get("weather", "B_WEATHER_RAIN")
        code = (
            f"    // {ability_const}: Doubles Speed in weather\n"
            f"    // Add to GetWhoStrikesFirst() in battle_main.c, alongside\n"
            f"    // the ABILITY_SWIFT_SWIM / ABILITY_CHLOROPHYLL checks.\n"
            f"    if (gBattleMons[battler].ability == {ability_const}\n"
            f"     && WEATHER_HAS_EFFECT && gBattleWeather & {weather})\n"
            f"        speed *= 2;"
        )
        result.append(("src/battle_main.c", code))

    elif template_id == "prevent_escape":
        code = (
            f"    // {ability_const}: Prevent foe from escaping\n"
            f"    if (gBattleMons[i].ability == {ability_const})\n"
            f"        cannotRun = TRUE;"
        )
        result.append(("src/battle_main.c", code))

    elif template_id == "natural_cure":
        code = (
            f"    // {ability_const}: Cure status on switch-out\n"
            f"    case {ability_const}:\n"
            f"        gBattleMons[gActiveBattler].status1 = 0;\n"
            f"        break;"
        )
        result.append(("src/battle_util.c", code))

    elif template_id == "pressure":
        code = (
            f"    // {ability_const}: Extra PP drain\n"
            f"    // In pokefirered, Pressure is handled by PressurePPLose()\n"
            f"    // in battle_util.c. Add this ability to the check:\n"
            f"    if (gBattleMons[gBattlerTarget].ability == {ability_const})\n"
            f"        ppToDeduct++;"
        )
        result.append(("src/battle_util.c", code))

    elif template_id == "wonder_guard":
        code = (
            f"    // {ability_const}: Only super-effective moves deal damage\n"
            f"    if (gBattleMons[gBattlerTarget].ability == {ability_const}\n"
            f"     && !(gMoveResultFlags & MOVE_RESULT_SUPER_EFFECTIVE)\n"
            f"     && gBattleMoves[gCurrentMove].power)\n"
            f"        gMoveResultFlags |= MOVE_RESULT_MISSED;"
        )
        result.append(("src/battle_script_commands.c", code))

    elif template_id == "recoil_immunity":
        code = (
            f"    // {ability_const}: Recoil immunity\n"
            f"    if (gBattleMons[gBattlerAttacker].ability == {ability_const})\n"
            f"        // Skip recoil damage"
        )
        result.append(("src/battle_script_commands.c", code))

    elif template_id == "type_change_boost":
        source = params.get("source_type", "TYPE_NORMAL")
        target = params.get("target_type", "TYPE_FAIRY")
        boost = params.get("boost", 130)
        # TYPE CHANGE: Sets dynamicMoveType before Cmd_typecalc runs.
        # Must be placed in Cmd_attackcanceler or a new command that runs
        # before type effectiveness is calculated.
        # Same mechanism as Weather Ball (see lines ~9350 in
        # battle_script_commands.c).
        type_code = (
            f"    // {ability_const}: Convert {source} moves to {target}\n"
            f"    // INSERT in Cmd_attackcanceler (runs before Cmd_typecalc)\n"
            f"    // or add a new Cmd that the battle script calls before\n"
            f"    // the attackstring/damagecalc sequence.\n"
            f"    if (gBattleMons[gBattlerAttacker].ability == {ability_const}\n"
            f"     && gBattleMoves[gCurrentMove].type == {source}\n"
            f"     && gBattleMoves[gCurrentMove].power > 0)\n"
            f"    {{\n"
            f"        gBattleStruct->dynamicMoveType = "
            f"{target} | F_DYNAMIC_TYPE_2;\n"
            f"    }}"
        )
        result.append(("src/battle_script_commands.c", type_code))

        # POWER BOOST: Applied in CalculateBaseDamage in pokemon.c,
        # AFTER the base damage is computed but before it's returned.
        # This is the same place Overgrow/Blaze/Torrent apply their boost.
        if boost != 100:
            boost_code = (
                f"    // {ability_const}: {boost - 100}% power boost for "
                f"type-changed moves\n"
                f"    // INSERT in CalculateBaseDamage(), after the pinch\n"
                f"    // ability boosts (Overgrow/Blaze/Torrent section).\n"
                f"    if (attacker->ability == {ability_const}\n"
                f"     && gBattleMoves[move].type == {source})\n"
                f"    {{\n"
                f"        damage = damage * {boost} / 100;\n"
                f"    }}"
            )
            result.append(("src/pokemon.c", boost_code))

    elif template_id == "intimidate_dual":
        stats = params.get("stats", ["STAT_ATK", "STAT_SPATK"])
        if isinstance(stats, list) and len(stats) >= 2:
            stat1, stat2 = stats[0], stats[1]
        else:
            stat1, stat2 = "STAT_ATK", "STAT_SPATK"
        # Uses the same Intimidate mechanism but lowers two stats
        code = (
            f"            case {ability_const}:\n"
            f"                if (!gSpecialStatuses[battler].intimidatedMon)\n"
            f"                {{\n"
            f"                    gStatuses3[battler] |= "
            f"STATUS3_INTIMIDATE_POKES;\n"
            f"                    gSpecialStatuses[battler].intimidatedMon = 1;\n"
            f"                    // Lowers BOTH {stat1} and {stat2}\n"
            f"                    gBattleScripting.animArg1 = {stat1};\n"
            f"                    gBattleScripting.animArg2 = {stat2};\n"
            f"                    BattleScriptPushCursorAndCallback("
            f"BattleScript_IntimidateActivates);\n"
            f"                    gBattleScripting.battler = battler;\n"
            f"                    effect++;\n"
            f"                }}\n"
            f"                break;"
        )
        result.append(("src/battle_util.c", code))

        # The actual dual-stat lowering needs a custom battle script or
        # a second stat reduction call.  Add a helper note.
        script_code = (
            f"    // {ability_const}: Dual stat lower — after the first stat\n"
            f"    // ({stat1}) is lowered by the Intimidate script, add a\n"
            f"    // second call to lower {stat2}.\n"
            f"    // In BattleScript_IntimidateActivates (or a copy), add:\n"
            f"    //   setbyte sBATTLER, [target]\n"
            f"    //   statbuffchange STAT_CHANGE_NOT_PROTECT_AFFECTED, "
            f"BattleScript_IntimidateEnd\n"
            f"    //   playanimation BS_TARGET, B_ANIM_STATS_CHANGE, "
            f"sB_ANIM_ARG1"
        )
        result.append(("data/battle_scripts_1.s", script_code))

    elif template_id == "switchin_field_effect":
        effect_type = params.get("effect", "TRICK_ROOM")
        # IMPORTANT: Trick Room and Tailwind are Gen 4+ features that do NOT
        # exist in vanilla pokefirered.  The generated code below requires
        # adding new constants, struct fields, and battle scripts to the
        # project first.  The code preview documents exactly what to add.
        if effect_type == "TRICK_ROOM":
            # Trick Room needs: a new bit in gSideStatuses or a new global,
            # a timer field, a battle script, and speed reversal logic.
            code = (
                f"            case {ability_const}:\n"
                f"                // Auto-set Trick Room on switch-in\n"
                f"                //\n"
                f"                // REQUIRED ADDITIONS (not in vanilla pokefirered):\n"
                f"                //\n"
                f"                // 1. In include/battle.h, add to BattleStruct:\n"
                f"                //      u8 trickRoomTimer;\n"
                f"                //\n"
                f"                // 2. In include/constants/battle.h, add:\n"
                f"                //      #define STATUS3_TRICK_ROOM (1 << 28)\n"
                f"                //\n"
                f"                // 3. In data/battle_scripts_1.s, create:\n"
                f"                //      BattleScript_TrickRoomActivates:\n"
                f"                //          printstring STRINGID_TRICKROOMACTIVATED\n"
                f"                //          waitmessage 0x40\n"
                f"                //          end3\n"
                f"                //\n"
                f"                // 4. In src/battle_util.c, in the speed\n"
                f"                //    comparison function, reverse the\n"
                f"                //    comparison when trickRoomTimer > 0.\n"
                f"                //\n"
                f"                if (gBattleStruct->trickRoomTimer == 0)\n"
                f"                {{\n"
                f"                    gBattleStruct->trickRoomTimer = 5;\n"
                f"                    BattleScriptPushCursorAndCallback("
                f"BattleScript_TrickRoomActivates);\n"
                f"                    gBattleScripting.battler = battler;\n"
                f"                    effect++;\n"
                f"                }}\n"
                f"                break;"
            )
        else:  # TAILWIND
            code = (
                f"            case {ability_const}:\n"
                f"                // Auto-set Tailwind on switch-in\n"
                f"                //\n"
                f"                // REQUIRED ADDITIONS (not in vanilla pokefirered):\n"
                f"                //\n"
                f"                // 1. In include/battle.h, add to SideTimer:\n"
                f"                //      u8 tailwindTimer;\n"
                f"                //      u8 tailwindBattlerId;\n"
                f"                //\n"
                f"                // 2. In include/constants/battle.h, add:\n"
                f"                //      #define SIDE_STATUS_TAILWIND (1 << 8)\n"
                f"                //\n"
                f"                // 3. In data/battle_scripts_1.s, create:\n"
                f"                //      BattleScript_TailwindActivates:\n"
                f"                //          printstring STRINGID_TAILWINDACTIVATED\n"
                f"                //          waitmessage 0x40\n"
                f"                //          end3\n"
                f"                //\n"
                f"                // 4. In src/battle_util.c, in the end-of-turn\n"
                f"                //    handler, decrement tailwindTimer and\n"
                f"                //    clear SIDE_STATUS_TAILWIND when it hits 0.\n"
                f"                //\n"
                f"                // 5. In the speed calculation, double speed\n"
                f"                //    when SIDE_STATUS_TAILWIND is active.\n"
                f"                //\n"
                f"                {{\n"
                f"                    u8 side = GetBattlerSide(battler);\n"
                f"                    if (!(gSideStatuses[side] & "
                f"SIDE_STATUS_TAILWIND))\n"
                f"                    {{\n"
                f"                        gSideStatuses[side] |= "
                f"SIDE_STATUS_TAILWIND;\n"
                f"                        gSideTimers[side].tailwindTimer = 5;\n"
                f"                        gSideTimers[side]."
                f"tailwindBattlerId = battler;\n"
                f"                        BattleScriptPushCursorAndCallback("
                f"BattleScript_TailwindActivates);\n"
                f"                        gBattleScripting.battler = battler;\n"
                f"                        effect++;\n"
                f"                    }}\n"
                f"                }}\n"
                f"                break;"
            )
        result.append(("src/battle_util.c", code))

    elif template_id == "multi_type_resist":
        type1 = params.get("type1", "TYPE_FIRE")
        type2 = params.get("type2", "TYPE_ICE")
        code = (
            f"    // {ability_const}: Halves {type1} and {type2} damage\n"
            f"    if (defender->ability == {ability_const})\n"
            f"    {{\n"
            f"        if (moveType == {type1} || moveType == {type2})\n"
            f"            damage /= 2;\n"
            f"    }}"
        )
        result.append(("src/pokemon.c", code))

    elif template_id == "weather_suppress":
        block = (
            f"            case {ability_const}:\n"
            f"                // Suppresses weather effects while on the field.\n"
            f"                // The actual suppression is in WEATHER_HAS_EFFECT macro\n"
            f"                // which returns FALSE when any active battler has this ability.\n"
            f"                // This case block handles the switch-in announcement.\n"
            f"                if (WEATHER_HAS_EFFECT && gBattleWeather)\n"
            f"                {{\n"
            f"                    BattleScriptPushCursorAndCallback("
            f"BattleScript_AirLockActivates);\n"
            f"                    gBattleScripting.battler = battler;\n"
            f"                    effect++;\n"
            f"                }}\n"
            f"                break;"
        )
        result.append(("src/battle_util.c", block))

    elif template_id == "shed_skin":
        chance = params.get("chance", 3)
        block = (
            f"                case {ability_const}:\n"
            f"                    if ((gBattleMons[battler].status1 & STATUS1_ANY)\n"
            f"                     && (Random() % {chance}) == 0)\n"
            f"                    {{\n"
            f"                        gBattleMons[battler].status1 = 0;\n"
            f"                        gBattleMons[battler].status2 &= "
            f"~STATUS2_NIGHTMARE;\n"
            f"                        BattleScriptPushCursorAndCallback("
            f"BattleScript_ShedSkinActivates);\n"
            f"                        BtlController_EmitSetMonData(0, "
            f"REQUEST_STATUS_BATTLE, 0, 4, "
            f"&gBattleMons[battler].status1);\n"
            f"                        MarkBattlerForControllerExec(battler);\n"
            f"                        effect++;\n"
            f"                    }}\n"
            f"                    break;"
        )
        result.append(("src/battle_util.c", block))

    elif template_id == "truant":
        block = (
            f"            case {ability_const}:\n"
            f"                // Truant: Skip every other turn.\n"
            f"                // The loafing is handled by gDisableStructs[battler]."
            f"truantCounter\n"
            f"                // which is toggled each turn. When truantCounter is odd,\n"
            f"                // the mon loafs around instead of attacking.\n"
            f"                if (gDisableStructs[battler].truantCounter)\n"
            f"                {{\n"
            f"                    CancelMultiTurnMoves(battler);\n"
            f"                    gHitMarker |= HITMARKER_UNABLE_TO_USE_MOVE;\n"
            f"                    gBattleCommunication[MULTISTRING_CHOOSER] = 0;\n"
            f"                    gBattlescriptCurrInstr = "
            f"BattleScript_MoveUsedLoafingAround;\n"
            f"                    gMoveResultFlags |= MOVE_RESULT_NO_EFFECT;\n"
            f"                    effect = 1;\n"
            f"                }}\n"
            f"                gDisableStructs[battler].truantCounter ^= 1;\n"
            f"                break;"
        )
        result.append(("src/battle_util.c", block))

    elif template_id == "sound_block":
        block = (
            f"            case {ability_const}:\n"
            f"                if (gBattleMoves[gCurrentMove].flags & FLAG_SOUND)\n"
            f"                {{\n"
            f"                    gBattlescriptCurrInstr = "
            f"BattleScript_SoundproofProtected;\n"
            f"                    gBattleScripting.battler = battler;\n"
            f"                    effect = 1;\n"
            f"                }}\n"
            f"                break;"
        )
        result.append(("src/battle_util.c", block))

    elif template_id == "color_change":
        block = (
            f"            case {ability_const}:\n"
            f"                if (!(gMoveResultFlags & MOVE_RESULT_NO_EFFECT)\n"
            f"                 && gBattleMons[battler].hp != 0\n"
            f"                 && moveType != TYPE_MYSTERY\n"
            f"                 && !IS_BATTLER_OF_TYPE(battler, moveType))\n"
            f"                {{\n"
            f"                    SET_BATTLER_TYPE(battler, moveType);\n"
            f"                    PREPARE_TYPE_BUFFER(gBattleTextBuff1, moveType);\n"
            f"                    BattleScriptPushCursor();\n"
            f"                    gBattlescriptCurrInstr = "
            f"BattleScript_ColorChangeActivates;\n"
            f"                    effect++;\n"
            f"                }}\n"
            f"                break;"
        )
        result.append(("src/battle_util.c", block))

    elif template_id == "synchronize_status":
        block = (
            f"            case {ability_const}:\n"
            f"                // Synchronize: Pass poison/burn/paralysis to the attacker.\n"
            f"                // This has two case entries in vanilla pokefirered:\n"
            f"                // caseID 7 handles the initial sync attempt,\n"
            f"                // caseID 8 handles the resolution.\n"
            f"                if (gBattleMons[gBattlerAttacker].hp != 0\n"
            f"                 && !gProtectStructs[gBattlerAttacker].confusionSelfDmg)\n"
            f"                {{\n"
            f"                    if (gBattleMons[battler].status1 & STATUS1_POISON)\n"
            f"                        gBattleCommunication[MOVE_EFFECT_BYTE] = "
            f"MOVE_EFFECT_AFFECTS_USER | MOVE_EFFECT_POISON;\n"
            f"                    else if (gBattleMons[battler].status1 & STATUS1_BURN)\n"
            f"                        gBattleCommunication[MOVE_EFFECT_BYTE] = "
            f"MOVE_EFFECT_AFFECTS_USER | MOVE_EFFECT_BURN;\n"
            f"                    else if (gBattleMons[battler].status1 & "
            f"STATUS1_PARALYSIS)\n"
            f"                        gBattleCommunication[MOVE_EFFECT_BYTE] = "
            f"MOVE_EFFECT_AFFECTS_USER | MOVE_EFFECT_PARALYSIS;\n"
            f"                    if (gBattleCommunication[MOVE_EFFECT_BYTE])\n"
            f"                    {{\n"
            f"                        BattleScriptPushCursor();\n"
            f"                        gBattlescriptCurrInstr = "
            f"BattleScript_SynchronizeActivates;\n"
            f"                        gHitMarker |= HITMARKER_STATUS_ABILITY_EFFECT;\n"
            f"                        effect++;\n"
            f"                    }}\n"
            f"                }}\n"
            f"                break;"
        )
        result.append(("src/battle_util.c", block))

    elif template_id == "suction_cups":
        code = (
            f"    // {ability_const}: Block forced switching (Roar/Whirlwind)\n"
            f"    if (gBattleMons[gBattlerTarget].ability == {ability_const})\n"
            f"    {{\n"
            f"        gBattlescriptCurrInstr = BattleScript_AbilityPreventsPhazing;\n"
            f"        gLastUsedAbility = {ability_const};\n"
            f"        RecordAbilityBattle(gBattlerTarget, {ability_const});\n"
            f"    }}"
        )
        result.append(("src/battle_script_commands.c", code))

    elif template_id == "sticky_hold":
        code = (
            f"    // {ability_const}: Block item theft (Thief/Trick/Knock Off)\n"
            f"    if (gBattleMons[gBattlerTarget].ability == {ability_const})\n"
            f"    {{\n"
            f"        gBattlescriptCurrInstr = BattleScript_StickyHoldActivates;\n"
            f"        gLastUsedAbility = {ability_const};\n"
            f"        RecordAbilityBattle(gBattlerTarget, {ability_const});\n"
            f"    }}"
        )
        result.append(("src/battle_script_commands.c", code))

    elif template_id == "shield_dust":
        code = (
            f"    // {ability_const}: Block secondary move effects\n"
            f"    // In the secondary effect application code, skip if target has this ability\n"
            f"    if (gBattleMons[gBattlerTarget].ability == {ability_const})\n"
            f"    {{\n"
            f"        // Skip additional effect application\n"
            f"        gBattlescriptCurrInstr = BattleScript_MoveEnd;\n"
            f"    }}"
        )
        result.append(("src/battle_script_commands.c", code))

    elif template_id == "lightning_rod":
        type_const = params.get("type", "TYPE_ELECTRIC")
        code = (
            f"    // {ability_const}: Redirect {type_const} moves to this Pokemon\n"
            f"    // In doubles, when a {type_const} move is used, change the target\n"
            f"    // to the battler with this ability.\n"
            f"    if (gBattleMons[battler].ability == {ability_const}\n"
            f"     && moveType == {type_const}\n"
            f"     && gBattlerTarget != battler)\n"
            f"    {{\n"
            f"        gBattlerTarget = battler;\n"
            f"        RecordAbilityBattle(battler, {ability_const});\n"
            f"    }}"
        )
        result.append(("src/battle_script_commands.c", code))

    elif template_id == "serene_grace":
        code = (
            f"    // {ability_const}: Double secondary effect chance\n"
            f"    // NOTE: gBattleMoves is const ROM data — do NOT write to it.\n"
            f"    // Instead, double the chance in the local comparison variable.\n"
            f"    // In Cmd_setmoveeffect or Cmd_setadditionaleffects, where the\n"
            f"    // secondaryEffectChance is read and compared to Random() % 100:\n"
            f"    u32 effectChance = gBattleMoves[gCurrentMove].secondaryEffectChance;\n"
            f"    if (gBattleMons[gBattlerAttacker].ability == {ability_const})\n"
            f"        effectChance *= 2;\n"
            f"    // Then use effectChance instead of gBattleMoves[...].secondaryEffectChance\n"
            f"    // in the Random() % 100 < effectChance comparison."
        )
        result.append(("src/battle_script_commands.c", code))

    elif template_id == "hustle":
        # Hustle has two parts: attack boost in pokemon.c and accuracy
        # penalty in battle_script_commands.c
        atk_code = (
            f"    // {ability_const}: 50% Attack boost for physical moves\n"
            f"    if (attacker->ability == {ability_const})\n"
            f"        attack = (attack * 150) / 100;"
        )
        result.append(("src/pokemon.c", atk_code))
        acc_code = (
            f"    // {ability_const}: 20% accuracy penalty for physical moves\n"
            f"    if (gBattleMons[gBattlerAttacker].ability == {ability_const}\n"
            f"     && IS_TYPE_PHYSICAL(moveType))\n"
            f"        calc = (calc * 80) / 100;"
        )
        result.append(("src/battle_script_commands.c", acc_code))

    elif template_id == "marvel_scale":
        stat_var = params.get("stat", "defense")
        code = (
            f"    // {ability_const}: 50% {stat_var} boost when statused\n"
            f"    if (defender->ability == {ability_const}\n"
            f"     && defender->status1)\n"
            f"        {stat_var} = ({stat_var} * 150) / 100;"
        )
        result.append(("src/pokemon.c", code))

    elif template_id == "early_bird":
        code = (
            f"    // {ability_const}: Wake from sleep in half the time\n"
            f"    // In the sleep counter decrement, subtract an extra turn\n"
            f"    // when this ability is active.\n"
            f"    if (gBattleMons[battler].ability == {ability_const}\n"
            f"     && (gBattleMons[battler].status1 & STATUS1_SLEEP))\n"
            f"    {{\n"
            f"        gBattleMons[battler].status1 -= 1;  // Extra decrement\n"
            f"        if (!(gBattleMons[battler].status1 & STATUS1_SLEEP))\n"
            f"            gBattleMons[battler].status1 &= ~STATUS1_SLEEP;\n"
            f"    }}"
        )
        result.append(("src/battle_util.c", code))

    elif template_id == "liquid_ooze":
        block = (
            f"            case {ability_const}:\n"
            f"                // When hit by a draining move, the attacker takes\n"
            f"                // damage instead of healing.\n"
            f"                gBattleMoveDamage *= -1;\n"
            f"                BattleScriptPushCursor();\n"
            f"                gBattlescriptCurrInstr = "
            f"BattleScript_LiquidOozeActivates;\n"
            f"                effect++;\n"
            f"                break;"
        )
        result.append(("src/battle_util.c", block))

    elif template_id == "plus_minus":
        partner = params.get("partner", "ABILITY_MINUS")
        code = (
            f"    // {ability_const}: 50% Sp. Attack boost when ally has {partner}\n"
            f"    if (attacker->ability == {ability_const})\n"
            f"    {{\n"
            f"        // Check if partner has the complementary ability\n"
            f"        u8 partner = BATTLE_PARTNER(battler);\n"
            f"        if (gBattleMons[partner].ability == {partner})\n"
            f"            spAttack = (spAttack * 150) / 100;\n"
            f"    }}"
        )
        result.append(("src/pokemon.c", code))

    elif template_id == "damp":
        block = (
            f"            case {ability_const}:\n"
            f"                // Prevents any Pokemon from using Explosion/Self-Destruct\n"
            f"                if (gBattleMoves[gCurrentMove].effect == "
            f"EFFECT_EXPLOSION)\n"
            f"                {{\n"
            f"                    gBattlescriptCurrInstr = "
            f"BattleScript_DampPreventsExplosion;\n"
            f"                    gBattleScripting.battler = battler;\n"
            f"                    effect = 1;\n"
            f"                }}\n"
            f"                break;"
        )
        result.append(("src/battle_util.c", block))

    elif template_id == "contact_flinch":
        chance = params.get("chance", 10)
        block = (
            f"            case {ability_const}:\n"
            f"                if (!(gMoveResultFlags & MOVE_RESULT_NO_EFFECT)\n"
            f"                 && gBattleMons[gBattlerAttacker].hp != 0\n"
            f"                 && !gProtectStructs[gBattlerAttacker]"
            f".confusionSelfDmg\n"
            f"                 && TARGET_TURN_DAMAGED\n"
            f"                 && (gBattleMoves[move].flags & FLAG_MAKES_CONTACT)\n"
            f"                 && (Random() % {chance}) == 0)\n"
            f"                {{\n"
            f"                    gBattleCommunication[MOVE_EFFECT_BYTE] = "
            f"MOVE_EFFECT_AFFECTS_USER | MOVE_EFFECT_FLINCH;\n"
            f"                    BattleScriptPushCursor();\n"
            f"                    gBattlescriptCurrInstr = "
            f"BattleScript_ApplySecondaryEffect;\n"
            f"                    gHitMarker |= HITMARKER_STATUS_ABILITY_EFFECT;\n"
            f"                    effect++;\n"
            f"                }}\n"
            f"                break;"
        )
        result.append(("src/battle_util.c", block))

    elif template_id == "trace":
        block = (
            f"            case {ability_const}:\n"
            f"                // Copies the opposing Pokemon's ability on switch-in.\n"
            f"                if (gBattleMons[BATTLE_OPPOSITE(battler)].ability\n"
            f"                 && gBattleMons[BATTLE_OPPOSITE(battler)].ability "
            f"!= {ability_const})\n"
            f"                {{\n"
            f"                    gLastUsedAbility = gBattleMons["
            f"BATTLE_OPPOSITE(battler)].ability;\n"
            f"                    gBattleMons[battler].ability = gLastUsedAbility;\n"
            f"                    BattleScriptPushCursorAndCallback("
            f"BattleScript_TraceActivates);\n"
            f"                    gBattleScripting.battler = battler;\n"
            f"                    PREPARE_MON_NICK_WITH_PREFIX_BUFFER("
            f"gBattleTextBuff1, BATTLE_OPPOSITE(battler),\n"
            f"                        gBattlerPartyIndexes["
            f"BATTLE_OPPOSITE(battler)]);\n"
            f"                    PREPARE_ABILITY_BUFFER(gBattleTextBuff2, "
            f"gLastUsedAbility);\n"
            f"                    effect++;\n"
            f"                }}\n"
            f"                break;"
        )
        result.append(("src/battle_util.c", block))

    elif template_id == "forecast":
        block = (
            f"            case {ability_const}:\n"
            f"                // Changes form based on weather.\n"
            f"                // This is Castform's signature ability.\n"
            f"                // The form change is handled by CastformDataTypeChange()\n"
            f"                // which maps weather → species form → types.\n"
            f"                effect = CastformDataTypeChange(battler);\n"
            f"                if (effect)\n"
            f"                {{\n"
            f"                    BattleScriptPushCursorAndCallback("
            f"BattleScript_CastformChange);\n"
            f"                    gBattleScripting.battler = battler;\n"
            f"                }}\n"
            f"                break;"
        )
        result.append(("src/battle_util.c", block))

    elif template_id == "block_specific_stat":
        stat = params.get("stat", "STAT_ATK")
        _stat_name_map = {
            "STAT_ATK": "Attack", "STAT_DEF": "Defense",
            "STAT_SPEED": "Speed", "STAT_SPATK": "Sp. Attack",
            "STAT_SPDEF": "Sp. Defense", "STAT_ACC": "Accuracy",
            "STAT_EVASION": "Evasion",
        }
        stat_name = _stat_name_map.get(stat, stat)
        code = (
            f"    // {ability_const}: Prevent {stat_name} from being lowered\n"
            f"    // Add to ChangeStatBuffs() in battle_script_commands.c,\n"
            f"    // alongside the ABILITY_KEEN_EYE / ABILITY_HYPER_CUTTER checks:\n"
            f"    else if (gBattleMons[gActiveBattler].ability == {ability_const}\n"
            f"             && !certain && statId == {stat})\n"
            f"    {{\n"
            f"        if (flags == STAT_CHANGE_ALLOW_PTR)\n"
            f"        {{\n"
            f"            BattleScriptPush(BS_ptr);\n"
            f"            gBattleScripting.battler = gActiveBattler;\n"
            f"            gBattlescriptCurrInstr = BattleScript_AbilityNoStatLoss;\n"
            f"            gLastUsedAbility = gBattleMons[gActiveBattler].ability;\n"
            f"            RecordAbilityBattle(gActiveBattler, gLastUsedAbility);\n"
            f"        }}\n"
            f"        return STAT_CHANGE_DIDNT_WORK;\n"
            f"    }}"
        )
        result.append(("src/battle_script_commands.c", code))

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
        # to halve egg steps when this ability is in the party.
        #
        # NOTE: this block is inserted right after the opening brace of
        # TryProduceOrHatchEgg, BEFORE the function's own `u32 i` is
        # declared. Don't reference the outer function's `i` here —
        # use locally-scoped variables (`p`, `q`) so the block compiles
        # no matter where it gets inserted.
        code = (
            f"    // {ability_const}: halve egg hatch steps\n"
            f"    {{\n"
            f"        u32 p, q;\n"
            f"        for (p = 0; p < PARTY_SIZE; p++)\n"
            f"        {{\n"
            f"            if (GetMonAbility(&gPlayerParty[p]) == "
            f"{ability_const})\n"
            f"            {{\n"
            f"                for (q = 0; q < DAYCARE_MON_COUNT; q++)\n"
            f"                {{\n"
            f"                    if (GetBoxMonData(&daycare->mons[q].mon,"
            f" MON_DATA_SANITY_HAS_SPECIES))\n"
            f"                        daycare->mons[q].steps++;\n"
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

        # Remove existing code for this ability in this file.
        # Marker-block remover handles the `// ABILITY_*: description`
        # + `{ ... }` pattern that field templates emit; the case/inline
        # remover is a fallback for any legacy shape that may still exist.
        _remove_marker_block(lines, ability_const)
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

        # Marker-block remover first (handles the `// ABILITY_*:` + braced
        # block pattern used by field templates), then the generic remover
        # as a fallback for any case/inline-style references.
        count = _remove_marker_block(lines, ability_const)
        count += _remove_ability_from_lines(lines, ability_const)
        if count > 0:
            with open(filepath, "w", encoding="utf-8") as f:
                f.writelines(lines)
            removed += count

    return removed


def _remove_marker_block(lines: list[str], ability_const: str) -> int:
    """Remove a field-effect marker block for an ability (in-place).

    Field-effect templates emit a marker comment line of the form
    `// ABILITY_CONST: <description>` immediately above a single
    braced statement (either a bare `{ ... }` block or an `if (...)`
    / `for (...)` whose body is braced). This helper finds the
    marker, then removes from the marker line through the matching
    closing `}` that terminates the following block.

    Returns the number of marker blocks removed.
    """
    import re as _re
    marker_pat = _re.compile(
        r'^\s*//\s*' + _re.escape(ability_const) + r'\s*:'
    )
    removed = 0
    i = 0
    while i < len(lines):
        if marker_pat.match(lines[i]):
            # Find the first `{` on this or subsequent non-empty lines.
            # Must be on the same line or within the next few lines so we
            # don't accidentally swallow unrelated code after a lone comment.
            brace_line = None
            for j in range(i, min(i + 5, len(lines))):
                if '{' in lines[j]:
                    brace_line = j
                    break
            if brace_line is None:
                # Lone marker comment with no following block: remove
                # just the comment line so it doesn't confuse detectors.
                del lines[i]
                removed += 1
                continue

            # Walk braces starting at brace_line. Some blocks have the
            # opening `{` on a dedicated line; others have it inline
            # with `if (...)`. Either way we start counting at brace_line.
            depth = 0
            end = brace_line
            for j in range(brace_line, len(lines)):
                depth += lines[j].count('{') - lines[j].count('}')
                if depth <= 0 and j >= brace_line:
                    end = j
                    break
            del lines[i:end + 1]
            removed += 1
            continue
        i += 1
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
