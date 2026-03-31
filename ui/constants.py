"""
ui/constants.py
Single source of truth for every name-pool / constant list used by the UI.

Other modules should import from here instead of defining their own copies.
This prevents the same list drifting out of sync across multiple files.
"""
from __future__ import annotations


# ── Pokémon types ─────────────────────────────────────────────────────────────

TYPE_CHOICES: list[str] = [
    "TYPE_NORMAL", "TYPE_FIGHTING", "TYPE_FLYING", "TYPE_POISON",
    "TYPE_GROUND", "TYPE_ROCK", "TYPE_BUG", "TYPE_GHOST",
    "TYPE_STEEL", "TYPE_MYSTERY", "TYPE_FIRE", "TYPE_WATER",
    "TYPE_GRASS", "TYPE_ELECTRIC", "TYPE_PSYCHIC", "TYPE_ICE",
    "TYPE_DRAGON", "TYPE_DARK",
]

# Gen 3 physical / special split (no split move, category follows type)
PHYSICAL_TYPES: set[str] = {
    "TYPE_NORMAL", "TYPE_FIGHTING", "TYPE_FLYING", "TYPE_POISON",
    "TYPE_GROUND", "TYPE_ROCK", "TYPE_BUG", "TYPE_GHOST", "TYPE_STEEL",
}
SPECIAL_TYPES: set[str] = {
    "TYPE_FIRE", "TYPE_WATER", "TYPE_GRASS", "TYPE_ELECTRIC",
    "TYPE_PSYCHIC", "TYPE_ICE", "TYPE_DRAGON", "TYPE_DARK",
}

# Colors for type badges in the UI
TYPE_COLORS: dict[str, str] = {
    "TYPE_NORMAL":   "#9E9E9E", "TYPE_FIGHTING": "#D32F2F",
    "TYPE_FLYING":   "#5C9BD6", "TYPE_POISON":   "#8E24AA",
    "TYPE_GROUND":   "#B8860B", "TYPE_ROCK":     "#7D6608",
    "TYPE_BUG":      "#558B2F", "TYPE_GHOST":    "#4A148C",
    "TYPE_STEEL":    "#607D8B", "TYPE_MYSTERY":  "#555555",
    "TYPE_FIRE":     "#E64A19", "TYPE_WATER":    "#1565C0",
    "TYPE_GRASS":    "#2E7D32", "TYPE_ELECTRIC": "#F9A825",
    "TYPE_PSYCHIC":  "#C2185B", "TYPE_ICE":      "#00838F",
    "TYPE_DRAGON":   "#1A237E", "TYPE_DARK":     "#37474F",
}


# ── Move targets ──────────────────────────────────────────────────────────────

MOVE_TARGET_CHOICES: list[tuple[str, str]] = [
    ("Selected target",          "MOVE_TARGET_SELECTED"),
    ("Random opponent",          "MOVE_TARGET_RANDOM"),
    ("Both opponents",           "MOVE_TARGET_BOTH"),
    ("Depends",                  "MOVE_TARGET_DEPENDS"),
    ("User",                     "MOVE_TARGET_USER"),
    ("Foes & ally",              "MOVE_TARGET_FOES_AND_ALLY"),
    ("Opponents' field",         "MOVE_TARGET_OPPONENTS_FIELD"),
]


# ── Move flags ────────────────────────────────────────────────────────────────

MOVE_FLAGS: list[tuple[str, str]] = [
    ("FLAG_MAKES_CONTACT",        "Makes contact — triggers contact-check abilities"),
    ("FLAG_PROTECT_AFFECTED",     "Blocked by Protect / Detect"),
    ("FLAG_MAGIC_COAT_AFFECTED",  "Reflected by Magic Coat"),
    ("FLAG_SNATCH_AFFECTED",      "Stolen by Snatch"),
    ("FLAG_MIRROR_MOVE_AFFECTED", "Copied by Mirror Move"),
    ("FLAG_KINGS_ROCK_AFFECTED",  "Can flinch with King's Rock"),
]


# ── Move effects (all 214 from include/constants/battle_move_effects.h) ──────

EFFECT_CHOICES: list[str] = sorted([
    "EFFECT_ABSORB", "EFFECT_ACCURACY_DOWN", "EFFECT_ACCURACY_DOWN_2",
    "EFFECT_ACCURACY_DOWN_HIT", "EFFECT_ACCURACY_UP", "EFFECT_ACCURACY_UP_2",
    "EFFECT_ALL_STATS_UP_HIT", "EFFECT_ALWAYS_HIT", "EFFECT_ASSIST",
    "EFFECT_ATTACK_DOWN", "EFFECT_ATTACK_DOWN_2", "EFFECT_ATTACK_DOWN_HIT",
    "EFFECT_ATTACK_UP", "EFFECT_ATTACK_UP_2", "EFFECT_ATTACK_UP_HIT",
    "EFFECT_ATTRACT", "EFFECT_BATON_PASS", "EFFECT_BEAT_UP",
    "EFFECT_BELLY_DRUM", "EFFECT_BIDE", "EFFECT_BLAZE_KICK",
    "EFFECT_BRICK_BREAK", "EFFECT_BULK_UP", "EFFECT_BURN_HIT",
    "EFFECT_CALM_MIND", "EFFECT_CAMOUFLAGE", "EFFECT_CHARGE",
    "EFFECT_CONFUSE", "EFFECT_CONFUSE_HIT", "EFFECT_CONVERSION",
    "EFFECT_CONVERSION_2", "EFFECT_COSMIC_POWER", "EFFECT_COUNTER",
    "EFFECT_CURSE", "EFFECT_DEFENSE_CURL", "EFFECT_DEFENSE_DOWN",
    "EFFECT_DEFENSE_DOWN_2", "EFFECT_DEFENSE_DOWN_HIT", "EFFECT_DEFENSE_UP",
    "EFFECT_DEFENSE_UP_2", "EFFECT_DEFENSE_UP_HIT", "EFFECT_DESTINY_BOND",
    "EFFECT_DISABLE", "EFFECT_DOUBLE_EDGE", "EFFECT_DOUBLE_HIT",
    "EFFECT_DRAGON_DANCE", "EFFECT_DRAGON_RAGE", "EFFECT_DREAM_EATER",
    "EFFECT_EARTHQUAKE", "EFFECT_ENCORE", "EFFECT_ENDEAVOR",
    "EFFECT_ENDURE", "EFFECT_ERUPTION", "EFFECT_EVASION_DOWN",
    "EFFECT_EVASION_DOWN_2", "EFFECT_EVASION_DOWN_HIT", "EFFECT_EVASION_UP",
    "EFFECT_EVASION_UP_2", "EFFECT_EXPLOSION", "EFFECT_FACADE",
    "EFFECT_FAKE_OUT", "EFFECT_FALSE_SWIPE", "EFFECT_FLAIL",
    "EFFECT_FLATTER", "EFFECT_FLINCH_HIT", "EFFECT_FLINCH_MINIMIZE_HIT",
    "EFFECT_FOCUS_ENERGY", "EFFECT_FOCUS_PUNCH", "EFFECT_FOLLOW_ME",
    "EFFECT_FORESIGHT", "EFFECT_FREEZE_HIT", "EFFECT_FRUSTRATION",
    "EFFECT_FURY_CUTTER", "EFFECT_FUTURE_SIGHT", "EFFECT_GRUDGE",
    "EFFECT_GUST", "EFFECT_HAIL", "EFFECT_HAZE",
    "EFFECT_HEAL_BELL", "EFFECT_HELPING_HAND", "EFFECT_HIDDEN_POWER",
    "EFFECT_HIGH_CRITICAL", "EFFECT_HIT", "EFFECT_IMPRISON",
    "EFFECT_INGRAIN", "EFFECT_KNOCK_OFF", "EFFECT_LEECH_SEED",
    "EFFECT_LEVEL_DAMAGE", "EFFECT_LIGHT_SCREEN", "EFFECT_LOCK_ON",
    "EFFECT_LOW_KICK", "EFFECT_MAGIC_COAT", "EFFECT_MAGNITUDE",
    "EFFECT_MEAN_LOOK", "EFFECT_MEMENTO", "EFFECT_METRONOME",
    "EFFECT_MIMIC", "EFFECT_MINIMIZE", "EFFECT_MIRROR_COAT",
    "EFFECT_MIRROR_MOVE", "EFFECT_MIST", "EFFECT_MOONLIGHT",
    "EFFECT_MORNING_SUN", "EFFECT_MUD_SPORT", "EFFECT_MULTI_HIT",
    "EFFECT_NATURE_POWER", "EFFECT_NIGHTMARE", "EFFECT_OHKO",
    "EFFECT_OVERHEAT", "EFFECT_PAIN_SPLIT", "EFFECT_PARALYZE",
    "EFFECT_PARALYZE_HIT", "EFFECT_PAY_DAY", "EFFECT_PERISH_SONG",
    "EFFECT_POISON", "EFFECT_POISON_FANG", "EFFECT_POISON_HIT",
    "EFFECT_POISON_TAIL", "EFFECT_PRESENT", "EFFECT_PROTECT",
    "EFFECT_PSYCH_UP", "EFFECT_PSYWAVE", "EFFECT_PURSUIT",
    "EFFECT_QUICK_ATTACK", "EFFECT_RAGE", "EFFECT_RAIN_DANCE",
    "EFFECT_RAMPAGE", "EFFECT_RAPID_SPIN", "EFFECT_RAZOR_WIND",
    "EFFECT_RECHARGE", "EFFECT_RECYCLE", "EFFECT_RECOIL",
    "EFFECT_RECOIL_IF_MISS", "EFFECT_REFLECT", "EFFECT_REFRESH",
    "EFFECT_REST", "EFFECT_RESTORE_HP", "EFFECT_RETURN",
    "EFFECT_REVENGE", "EFFECT_ROAR", "EFFECT_ROLE_PLAY",
    "EFFECT_ROLLOUT", "EFFECT_SAFEGUARD", "EFFECT_SANDSTORM",
    "EFFECT_SECRET_POWER", "EFFECT_SEMI_INVULNERABLE", "EFFECT_SKETCH",
    "EFFECT_SKILL_SWAP", "EFFECT_SKY_ATTACK", "EFFECT_SKY_UPPERCUT",
    "EFFECT_SLEEP", "EFFECT_SLEEP_TALK", "EFFECT_SMELLINGSALT",
    "EFFECT_SNATCH", "EFFECT_SNORE", "EFFECT_SOFTBOILED",
    "EFFECT_SOLAR_BEAM", "EFFECT_SONICBOOM", "EFFECT_SPECIAL_ATTACK_DOWN",
    "EFFECT_SPECIAL_ATTACK_DOWN_2", "EFFECT_SPECIAL_ATTACK_DOWN_HIT",
    "EFFECT_SPECIAL_ATTACK_UP", "EFFECT_SPECIAL_ATTACK_UP_2",
    "EFFECT_SPECIAL_DEFENSE_DOWN", "EFFECT_SPECIAL_DEFENSE_DOWN_2",
    "EFFECT_SPECIAL_DEFENSE_DOWN_HIT", "EFFECT_SPECIAL_DEFENSE_UP",
    "EFFECT_SPECIAL_DEFENSE_UP_2", "EFFECT_SPEED_DOWN", "EFFECT_SPEED_DOWN_2",
    "EFFECT_SPEED_DOWN_HIT", "EFFECT_SPEED_UP", "EFFECT_SPEED_UP_2",
    "EFFECT_SPITE", "EFFECT_SPIKES", "EFFECT_SPIT_UP",
    "EFFECT_SPLASH", "EFFECT_STOCKPILE", "EFFECT_SUBSTITUTE",
    "EFFECT_SUNNY_DAY", "EFFECT_SUPER_FANG", "EFFECT_SUPERPOWER",
    "EFFECT_SWAGGER", "EFFECT_SWALLOW", "EFFECT_SYNTHESIS",
    "EFFECT_TAUNT", "EFFECT_TEETER_DANCE", "EFFECT_TELEPORT",
    "EFFECT_THAW_HIT", "EFFECT_THIEF", "EFFECT_THUNDER",
    "EFFECT_TICKLE", "EFFECT_TORMENT", "EFFECT_TOXIC",
    "EFFECT_TRANSFORM", "EFFECT_TRAP", "EFFECT_TRI_ATTACK",
    "EFFECT_TRICK", "EFFECT_TRIPLE_KICK", "EFFECT_TWINEEDLE",
    "EFFECT_TWISTER", "EFFECT_UPROAR", "EFFECT_UNUSED_60",
    "EFFECT_UNUSED_6E", "EFFECT_UNUSED_83", "EFFECT_UNUSED_8D",
    "EFFECT_UNUSED_A3", "EFFECT_VITAL_THROW", "EFFECT_WATER_SPORT",
    "EFFECT_WEATHER_BALL", "EFFECT_WILL_O_WISP", "EFFECT_WISH",
    "EFFECT_YAWN",
])


# ── AI script flags ──────────────────────────────────────────────────────────
# Every flag here has real AI script code in pokefirered's battle_ai_scripts.s.
# Removed flags that don't belong in the trainer editor:
#   SMART_SWITCHING — constant doesn't exist in pokefirered
#   ROAMING / SAFARI — set by the engine for wild encounters, not trainer data
#   FIRST_BATTLE — set by event scripts (trainerbattle_earlyrival), not trainer data
#   UNKNOWN (bit 9) — empty/placeholder script, no useful behaviour

AI_FLAGS: list[tuple[str, str]] = [
    ("AI_SCRIPT_CHECK_BAD_MOVE",         "Check Bad Move — avoid clearly useless moves"),
    ("AI_SCRIPT_CHECK_VIABILITY",        "Check Viability — prefer super-effective moves"),
    ("AI_SCRIPT_TRY_TO_FAINT",          "Try To Faint — go for the knock-out"),
    ("AI_SCRIPT_SETUP_FIRST_TURN",      "Setup First Turn — use stat-boosting moves early"),
    ("AI_SCRIPT_RISKY",                  "Risky — favour high-risk high-reward moves"),
    ("AI_SCRIPT_PREFER_STRONGEST_MOVE", "Prefer Strongest Move — always pick the hardest hit"),
    ("AI_SCRIPT_PREFER_BATON_PASS",     "Prefer Baton Pass — pass stat boosts to teammates"),
    ("AI_SCRIPT_DOUBLE_BATTLE",         "Double Battle — double-battle-specific logic"),
    ("AI_SCRIPT_HP_AWARE",              "HP Aware — smarter decisions based on remaining HP"),
]


# ── Trainer encounter music ──────────────────────────────────────────────────

ENCOUNTER_MUSIC: list[tuple[str, str]] = [
    ("TRAINER_ENCOUNTER_MUSIC_MALE",        "Normal (Male)"),
    ("TRAINER_ENCOUNTER_MUSIC_FEMALE",      "Normal (Female)"),
    ("TRAINER_ENCOUNTER_MUSIC_GIRL",        "Girl / Tuber"),
    ("TRAINER_ENCOUNTER_MUSIC_SUSPICIOUS",  "Suspicious"),
    ("TRAINER_ENCOUNTER_MUSIC_INTENSE",     "Intense"),
    ("TRAINER_ENCOUNTER_MUSIC_COOL",        "Cool"),
    ("TRAINER_ENCOUNTER_MUSIC_AQUA",        "Team Aqua"),
    ("TRAINER_ENCOUNTER_MUSIC_MAGMA",       "Team Magma"),
    ("TRAINER_ENCOUNTER_MUSIC_SWIMMER",     "Swimmer"),
    ("TRAINER_ENCOUNTER_MUSIC_TWINS",       "Twins"),
    ("TRAINER_ENCOUNTER_MUSIC_ELITE_FOUR",  "Elite Four"),
    ("TRAINER_ENCOUNTER_MUSIC_HIKER",       "Hiker"),
    ("TRAINER_ENCOUNTER_MUSIC_INTERVIEWER", "Interviewer"),
    ("TRAINER_ENCOUNTER_MUSIC_RICH",        "Rich"),
]


# ── Trainer party types ──────────────────────────────────────────────────────

PARTY_TYPES: list[tuple[str, str]] = [
    ("NO_ITEM_DEFAULT_MOVES", "No Item, Default Moves"),
    ("ITEM_DEFAULT_MOVES",    "Held Item, Default Moves"),
    ("NO_ITEM_CUSTOM_MOVES",  "No Item, Custom Moves"),
    ("ITEM_CUSTOM_MOVES",     "Held Item + Custom Moves"),
]

STRUCT_FOR_PARTY_TYPE: dict[str, str] = {
    "NO_ITEM_DEFAULT_MOVES": "TrainerMonNoItemDefaultMoves",
    "ITEM_DEFAULT_MOVES":    "TrainerMonItemDefaultMoves",
    "NO_ITEM_CUSTOM_MOVES":  "TrainerMonNoItemCustomMoves",
    "ITEM_CUSTOM_MOVES":     "TrainerMonItemCustomMoves",
}

PARTY_TYPE_FOR_STRUCT: dict[str, str] = {v: k for k, v in STRUCT_FOR_PARTY_TYPE.items()}


# ── Item categories ──────────────────────────────────────────────────────────

POCKET_CHOICES: list[tuple[str, str]] = [
    ("Items",       "POCKET_ITEMS"),
    ("Poké Balls",  "POCKET_POKE_BALLS"),
    ("TM / HM",     "POCKET_TM_HM"),
    ("Berries",     "POCKET_BERRIES"),
    ("Key Items",   "POCKET_KEY_ITEMS"),
    ("(none)",      "POCKET_NONE"),
]

ITEM_TYPE_CHOICES: list[tuple[str, str]] = [
    ("0 – Normal item",     "0"),
    ("1 – HM",              "1"),
    ("2 – TM",              "2"),
    ("3 – Repel-type",      "3"),
    ("4 – Evolution stone", "4"),
]

HOLD_EFFECT_CHOICES: list[str] = [
    "HOLD_EFFECT_NONE",
    "HOLD_EFFECT_RESTORE_HP",
    "HOLD_EFFECT_CURE_PAR",
    "HOLD_EFFECT_CURE_SLP",
    "HOLD_EFFECT_CURE_PSN",
    "HOLD_EFFECT_CURE_BRN",
    "HOLD_EFFECT_CURE_FRZ",
    "HOLD_EFFECT_RESTORE_PP",
    "HOLD_EFFECT_CURE_CONFUSION",
    "HOLD_EFFECT_CURE_STATUS",
    "HOLD_EFFECT_CONFUSE_SPICY",
    "HOLD_EFFECT_CONFUSE_DRY",
    "HOLD_EFFECT_CONFUSE_SWEET",
    "HOLD_EFFECT_CONFUSE_BITTER",
    "HOLD_EFFECT_CONFUSE_SOUR",
    "HOLD_EFFECT_ATTACK_UP",
    "HOLD_EFFECT_DEFENSE_UP",
    "HOLD_EFFECT_SPEED_UP",
    "HOLD_EFFECT_SP_ATTACK_UP",
    "HOLD_EFFECT_SP_DEFENSE_UP",
    "HOLD_EFFECT_CRITICAL_UP",
    "HOLD_EFFECT_RANDOM_STAT_UP",
    "HOLD_EFFECT_EVASION_UP",
    "HOLD_EFFECT_RESTORE_STATS",
    "HOLD_EFFECT_MACHO_BRACE",
    "HOLD_EFFECT_EXP_SHARE",
    "HOLD_EFFECT_QUICK_CLAW",
    "HOLD_EFFECT_FRIENDSHIP_UP",
    "HOLD_EFFECT_CURE_ATTRACT",
    "HOLD_EFFECT_CHOICE_BAND",
    "HOLD_EFFECT_FLINCH",
    "HOLD_EFFECT_BUG_POWER",
    "HOLD_EFFECT_DOUBLE_PRIZE",
    "HOLD_EFFECT_REPEL",
    "HOLD_EFFECT_SOUL_DEW",
    "HOLD_EFFECT_DEEP_SEA_TOOTH",
    "HOLD_EFFECT_DEEP_SEA_SCALE",
    "HOLD_EFFECT_CAN_ALWAYS_RUN",
    "HOLD_EFFECT_PREVENT_EVOLVE",
    "HOLD_EFFECT_FOCUS_BAND",
    "HOLD_EFFECT_LUCKY_EGG",
    "HOLD_EFFECT_SCOPE_LENS",
    "HOLD_EFFECT_STEEL_POWER",
    "HOLD_EFFECT_LEFTOVERS",
    "HOLD_EFFECT_DRAGON_SCALE",
    "HOLD_EFFECT_LIGHT_BALL",
    "HOLD_EFFECT_GROUND_POWER",
    "HOLD_EFFECT_ROCK_POWER",
    "HOLD_EFFECT_GRASS_POWER",
    "HOLD_EFFECT_DARK_POWER",
    "HOLD_EFFECT_FIGHTING_POWER",
    "HOLD_EFFECT_ELECTRIC_POWER",
    "HOLD_EFFECT_WATER_POWER",
    "HOLD_EFFECT_FLYING_POWER",
    "HOLD_EFFECT_POISON_POWER",
    "HOLD_EFFECT_ICE_POWER",
    "HOLD_EFFECT_GHOST_POWER",
    "HOLD_EFFECT_PSYCHIC_POWER",
    "HOLD_EFFECT_FIRE_POWER",
    "HOLD_EFFECT_DRAGON_POWER",
    "HOLD_EFFECT_NORMAL_POWER",
    "HOLD_EFFECT_UP_GRADE",
    "HOLD_EFFECT_SHELL_BELL",
    "HOLD_EFFECT_LUCKY_PUNCH",
    "HOLD_EFFECT_METAL_POWDER",
    "HOLD_EFFECT_THICK_CLUB",
    "HOLD_EFFECT_STICK",
]

# Functions that can be set as an item's Field Use (what happens when
# the player uses the item from the Bag outside of battle).
FIELD_USE_FUNC_CHOICES: list[str] = [
    "NULL",
    "FieldUseFunc_Medicine",
    "FieldUseFunc_RareCandy",
    "FieldUseFunc_EvoItem",
    "FieldUseFunc_Ether",
    "FieldUseFunc_PpUp",
    "FieldUseFunc_SacredAsh",
    "FieldUseFunc_Repel",
    "FieldUseFunc_BlackWhiteFlute",
    "FieldUseFunc_Rod",
    "FieldUseFunc_Bike",
    "FieldUseFunc_Mail",
    "FieldUseFunc_TmCase",
    "FieldUseFunc_BerryPouch",
    "FieldUseFunc_TeachyTv",
    "FieldUseFunc_TownMap",
    "FieldUseFunc_FameChecker",
    "FieldUseFunc_VsSeeker",
    "FieldUseFunc_CoinCase",
    "FieldUseFunc_PowderJar",
    "FieldUseFunc_PokeFlute",
    "FieldUseFunc_OakStopsYou",
    "ItemUseOutOfBattle_EscapeRope",
    "ItemUseOutOfBattle_Itemfinder",
    "ItemUseOutOfBattle_EnigmaBerry",
]

# Functions that can be set as an item's Battle Use Func (what happens
# when the player uses the item during battle).
BATTLE_USE_FUNC_CHOICES: list[str] = [
    "NULL",
    "BattleUseFunc_Medicine",
    "BattleUseFunc_Ether",
    "BattleUseFunc_PokeBallEtc",
    "BattleUseFunc_StatBooster",
    "BattleUseFunc_PokeDoll",
    "BattleUseFunc_PokeFlute",
    "BattleUseFunc_BerryPouch",
    "ItemUseInBattle_EnigmaBerry",
]
