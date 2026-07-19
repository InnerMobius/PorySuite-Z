"""core/form_change_patch.py — Layer B engine patcher for in-game form changes.

Adds the pokeemerald-expansion-style form-change infrastructure to a pokefirered
project (which has none of it natively):

  * a ``struct FormChange`` ({method, targetSpecies, param})
  * a ``const struct FormChange *formChangeTable`` pointer on ``struct SpeciesInfo``
  * non-battle trigger constants in ``include/constants/form_change_types.h``
  * ``GetFormChangeTargetSpecies()`` in a new ``src/form_change.c``
    (auto-compiled — the Makefile globs ``src/*.c``)

This is **Layer B infrastructure only**. It is additive + idempotent: with no
``formChangeTable`` defined, every species' pointer is implicitly NULL and the
resolver returns ``SPECIES_NONE``, so the engine behaves identically and a
project still builds. The per-species form-change tables, the trigger hooks
(item-hold / item-use / time-of-day / weather), and the trigger-editor UI are
layered on top and reference this infrastructure.

Battle gimmicks (mega / dynamax / tera) are intentionally out of scope.

Game source is never hand-edited — all edits go through this patcher. Re-running
produces no changes (byte-identical).
"""

import logging
import os
import re

from core.form_system_patch import apply_form_system  # Layer B sits after Layer A

_STRUCT_MARKER = "struct FormChange"
_FIELD_MARKER = "formChangeTable"
_PROTO_MARKER = "TryUpdateOverworldFormChanges"  # present in both pokemon.h + form_change.c
_TYPES_GUARD = "GUARD_CONSTANTS_FORM_CHANGE_TYPES_H"

_SPECIESINFO_OPEN = "struct SpeciesInfo\n{"
_FORMCHANGE_STRUCT = (
    "struct FormChange\n"
    "{\n"
    "    u16 method;\n"
    "    u16 targetSpecies;\n"
    "    u16 param;\n"
    "};\n\n"
)
# Layer A ends the struct with the formSpeciesIdTable pointer then `};`.
_LAYER_A_CLOSE = "    const u16 *formSpeciesIdTable;\n};"
_LAYER_B_CLOSE = (
    "    const u16 *formSpeciesIdTable;\n"
    "    const struct FormChange *formChangeTable;\n};"
)
_PROTO_ANCHOR = "u8 GetSpeciesFormId(u16 species);\n"
_PROTOS = (
    "u8 PorySuite_GetTimeOfDay(void);",
    "void TryUpdateOverworldFormChanges(void);",
    "bool8 TryInBattleFormChange(u8 battler);",   # returns TRUE if it morphed
)
# Prototypes for functions that USED to be generated (the one-way resolver path).
# The live engine routes everything through TryUpdateOverworldFormChanges →
# ReevaluateOverworldForm, so these are gone from form_change.c — prune their stale
# declarations from a previously-patched pokemon.h (No-Dead-Code; idempotent).
_OBSOLETE_PROTOS = (
    "u16 GetFormChangeTargetSpecies(u16 species, u16 method, u16 param);\n",
    "bool8 TryFormChange(struct Pokemon *mon, u16 method, u16 param);\n",
    "void ApplyHeldItemFormChange(struct Pokemon *mon);\n",
    # TryInBattleFormChange gained a bool8 return (announce-on-morph); prune the
    # old void prototype so it doesn't conflict with the new one on upgrade.
    "void TryInBattleFormChange(u8 battler);\n",
)

_FORM_CHANGE_TYPES_H = '''#ifndef GUARD_CONSTANTS_FORM_CHANGE_TYPES_H
#define GUARD_CONSTANTS_FORM_CHANGE_TYPES_H

// In-game alternate-form change triggers (PorySuite-Z Layer B). Battle gimmicks
// (mega / dynamax / tera) are intentionally out of scope.
#define FORM_CHANGE_END         0
#define FORM_CHANGE_ITEM_HOLD   1   // param = held item id; reverts when removed
#define FORM_CHANGE_ITEM_USE    2   // param = item id used on the Pokemon (permanent)
#define FORM_CHANGE_TIME_OF_DAY 3   // param = time-of-day period (needs a time source)
#define FORM_CHANGE_WEATHER     4   // param = overworld weather id
#define FORM_CHANGE_FLAG        5   // param = a story FLAG_* id; form holds while set
#define FORM_CHANGE_STATUS      6   // param = a STATUS1_* mask; form holds while afflicted
#define FORM_CHANGE_HP_BELOW    7   // param = HP percent; in-battle, holds while hp% < param
// Battle-ability form triggers (PorySuite-Z Layer C). Constants are always defined; the
// engine hooks that read them are emitted by the patcher ONLY for abilities the project
// has, so a project with no such data/ability never runs any of this.
#define FORM_CHANGE_BATTLE_STANCE_BLADE  8  // Stance Change: become this form on a DAMAGING move
#define FORM_CHANGE_BATTLE_STANCE_SHIELD 9  // Stance Change: become this form on a STATUS move
#define FORM_CHANGE_BATTLE_WEATHER      10  // Forecast/Flower Gift: hold while in-battle weather = param (B_WEATHER_* mask)
#define FORM_CHANGE_BATTLE_TURN         11  // Hunger Switch: toggle each end of turn
#define FORM_CHANGE_BATTLE_KO           12  // Battle Bond: become this form once, after the holder lands a KO
#define FORM_CHANGE_BATTLE_MOVE         13  // Gulp Missile: become this form when the holder uses the move id in param
#define FORM_CHANGE_BATTLE_HP_ABOVE     14  // Schooling: hold while hp% >= param (and level >= 20)

#endif // GUARD_CONSTANTS_FORM_CHANGE_TYPES_H
'''

_FORM_CHANGE_C = '''#include "global.h"
#include "pokemon.h"
#include "field_weather.h"
#include "event_data.h"
#include "data.h"
#include "string_util.h"
#include "constants/species.h"
#include "constants/weather.h"
#include "constants/form_change_types.h"

// ── In-game form-change engine (added by PorySuite-Z Layer B patcher) ──
//
// A form-capable species points formChangeTable at an array of
// {method, targetSpecies, param} rows terminated by FORM_CHANGE_END. A trigger
// (method) plus its argument (param: held item id, weather id, time period)
// selects the species to morph into. With no tables defined every pointer is
// NULL, so all of this is inert and the engine behaves identically.

__WEATHER_GROUP_FUNCS__

// Time-of-day source. PorySuite does NOT add a clock to the engine — this weak
// default reports "no time source" (0xFF), so FORM_CHANGE_TIME_OF_DAY never
// fires. A project that HAS a clock (RTC or otherwise) overrides this with a
// strong PorySuite_GetTimeOfDay() returning its current period; nothing here
// forces RTC support on projects that lack it.
u8 __attribute__((weak)) PorySuite_GetTimeOfDay(void)
{
    return 0xFF;
}

// Morph a Pokemon to *target*, recalc its stats, and — if it was NOT nicknamed
// (its name still matched its old species) — rename it to the new species so a
// form change shows the right name; a custom nickname is kept. Mirrors the
// evolution rename in pokemon.c. No-op if already that species.
static void MorphMonSpecies(struct Pokemon *mon, u16 target)
{
    u16 old = GetMonData(mon, MON_DATA_SPECIES, NULL);
    u8 nick[POKEMON_NAME_LENGTH + 1];

    if (target == SPECIES_NONE || target == old)
        return;
    GetMonData(mon, MON_DATA_NICKNAME, nick);
    SetMonData(mon, MON_DATA_SPECIES, &target);
    CalculateMonStats(mon);
    if (StringCompare(nick, gSpeciesNames[old]) == 0)
        SetMonData(mon, MON_DATA_NICKNAME, gSpeciesNames[target]);
}

// Recompute one Pokemon's correct overworld form from the CURRENT conditions and
// apply it. Unlike a one-way morph-in, this also REVERTS a weather/time form
// back to the base when its condition ends — it always picks the right form
// rather than only morphing in. Precedence: held item > weather > time; if
// nothing matches, the base (the revert). A permanent (item-use) form is left
// untouched. Relies on .formSpeciesIdTable[0] being the base on every member.
static void ReevaluateOverworldForm(struct Pokemon *mon, u8 weather, u8 tod)
{
    u16 species = GetMonData(mon, MON_DATA_SPECIES, NULL);
    u16 heldItem = GetMonData(mon, MON_DATA_HELD_ITEM, NULL);
    const u16 *forms = gSpeciesInfo[species].formSpeciesIdTable;
    const struct FormChange *t;
    u16 base, target;
    u32 i;

    if (forms == NULL)
        return;                         // not a form-capable species
    base = forms[0];
    t = gSpeciesInfo[base].formChangeTable;
    if (t == NULL)
        return;

    // A form reached by USING an item is permanent; conditions never revert it.
    for (i = 0; t[i].method != FORM_CHANGE_END; i++)
    {
        if (t[i].method == FORM_CHANGE_ITEM_USE && t[i].targetSpecies == species)
            return;
    }

    // Pick the form the current conditions call for, in precedence order;
    // default to the base — that default IS the revert.
    target = base;
    for (i = 0; t[i].method != FORM_CHANGE_END; i++)
    {
        if (t[i].method == FORM_CHANGE_ITEM_HOLD && t[i].param == heldItem)
        {
            target = t[i].targetSpecies;
            break;
        }
    }
    if (target == base)
    {
        // A set story flag is a persistent world state (e.g. "true form after an
        // event"); it holds regardless of weather / time.
        for (i = 0; t[i].method != FORM_CHANGE_END; i++)
        {
            if (t[i].method == FORM_CHANGE_FLAG && FlagGet(t[i].param))
            {
                target = t[i].targetSpecies;
                break;
            }
        }
    }
    if (target == base)
    {
        // A status condition (poison / burn / sleep / …) the mon currently carries.
        // param is a STATUS1_* mask; reverts when the status is cured.
        u32 status = GetMonData(mon, MON_DATA_STATUS, NULL);
        for (i = 0; t[i].method != FORM_CHANGE_END; i++)
        {
            if (t[i].method == FORM_CHANGE_STATUS && (status & t[i].param))
            {
                target = t[i].targetSpecies;
                break;
            }
        }
    }
    if (target == base)
    {
        for (i = 0; t[i].method != FORM_CHANGE_END; i++)
        {
            if (t[i].method == FORM_CHANGE_WEATHER
                && WeatherMatches(t[i].param, weather))
            {
                target = t[i].targetSpecies;
                break;
            }
        }
    }
    if (target == base && tod != 0xFF)
    {
        for (i = 0; t[i].method != FORM_CHANGE_END; i++)
        {
            if (t[i].method == FORM_CHANGE_TIME_OF_DAY && t[i].param == tod)
            {
                target = t[i].targetSpecies;
                break;
            }
        }
    }

    MorphMonSpecies(mon, target);
}

// Re-evaluate the overworld triggers for the whole party — held item, overworld
// weather, time-of-day — morphing OR reverting each Pokemon to the form its
// current conditions call for. Call when the party is in a stable state (e.g.
// opening the party menu, or on map entry).
void TryUpdateOverworldFormChanges(void)
{
    // Read the SAVED weather, not the transient currWeather: the saved value is
    // set the instant weather is applied (SetSavedWeather), so a form morphs to
    // match a just-played Sun's/Storm's Song even before the on-screen weather
    // transition has finished animating. At a stable time (party menu / battle)
    // saved == current, so this is also correct there.
    u8 weather = GetSav1Weather();
    u8 tod = PorySuite_GetTimeOfDay();
    u32 i;
    for (i = 0; i < PARTY_SIZE; i++)
    {
        struct Pokemon *mon = &gPlayerParty[i];
        if (GetMonData(mon, MON_DATA_SPECIES_OR_EGG, NULL) == SPECIES_NONE
            || GetMonData(mon, MON_DATA_IS_EGG, NULL))
            continue;
        ReevaluateOverworldForm(mon, weather, tod);
    }
}
'''


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _write_if_changed(path, text):
    """Write only when content differs — byte-equality guard avoids phantom
    diffs on a no-op re-patch. Returns True if the file was written."""
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                if f.read() == text:
                    return False
    except Exception:
        pass
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return True


def _patch_pokemon_h(pokemon_h):
    """Add struct FormChange, the formChangeTable field, and the resolver
    prototype. Idempotent."""
    text = _read(pokemon_h)
    original = text

    if _STRUCT_MARKER not in text:
        if _SPECIESINFO_OPEN not in text:
            raise RuntimeError("form-change patch: struct SpeciesInfo not found")
        text = text.replace(_SPECIESINFO_OPEN,
                            _FORMCHANGE_STRUCT + _SPECIESINFO_OPEN, 1)

    if _FIELD_MARKER not in text:
        if _LAYER_A_CLOSE not in text:
            raise RuntimeError("form-change patch: Layer A formSpeciesIdTable field "
                               "not found (apply form_system_patch first)")
        text = text.replace(_LAYER_A_CLOSE, _LAYER_B_CLOSE, 1)

    if _PROTO_ANCHOR not in text:
        raise RuntimeError("form-change patch: GetSpeciesFormId prototype not found")
    # add any missing prototype right after the anchor (reversed so they end up
    # in declared order); idempotent — present prototypes are skipped.
    for proto in reversed(_PROTOS):
        if proto not in text:
            text = text.replace(_PROTO_ANCHOR, _PROTO_ANCHOR + proto + "\n", 1)
    # prune prototypes left by an earlier patcher version (No-Dead-Code).
    for op in _OBSOLETE_PROTOS:
        if op in text:
            text = text.replace(op, "", 1)

    if text != original:
        _write_if_changed(pokemon_h, text)
        return True
    return False


def _create_form_change_types_h(path):
    return _write_if_changed(path, _FORM_CHANGE_TYPES_H)


def _weather_group_funcs(project_root):
    """Build WeatherGroup()/WeatherMatches() from the project's weather.h.

    ONLY the rain family is grouped — a form-change keyed on WEATHER_RAIN also fires
    for thunderstorms / downpours, since those are all genuinely "raining" (the one
    grouping that was asked for). EVERY other weather matches EXACTLY. In particular
    WEATHER_SUNNY (the harsh-sun weather effect that drives sunny day in battle) is
    deliberately NOT lumped with WEATHER_SUNNY_CLOUDS, which is the NORMAL default
    outdoor weather — they are separate effects, so a "sunny" form rule must trigger
    only on the actual sunny effect, never on plain daylight. Project-agnostic;
    references only constants that exist."""
    wh = os.path.join(project_root, "include", "constants", "weather.h")
    rain = []
    try:
        with open(wh, encoding="utf-8") as f:
            for m in re.finditer(r"#define\s+(WEATHER_\w+)\s+\d+", f.read()):
                c = m.group(1)
                u = c[len("WEATHER_"):]
                # SAND excluded — SANDSTORM contains "STORM" but isn't rain.
                if "SAND" not in u and any(
                        k in u for k in ("RAIN", "STORM", "DOWNPOUR", "DRIZZLE")):
                    rain.append(c)          # RAIN / RAIN_THUNDERSTORM / DOWNPOUR …
    except Exception:
        pass
    cases = ""
    for c in rain:
        cases += f"    case {c}:\n"
    if rain:
        cases += "        return 1;  // rain family\n"
    return (
        "// Only the RAIN family is grouped: a form-change keyed on WEATHER_RAIN also\n"
        "// fires for thunderstorms / downpours (all genuinely raining). Built from\n"
        "// weather.h by name. Every other weather matches EXACTLY — notably SUNNY is\n"
        "// NOT grouped with SUNNY_CLOUDS (the normal default outdoor weather), so a\n"
        "// sunny form rule triggers only on the real sunny effect, never plain day.\n"
        "static u8 WeatherGroup(u16 w)\n"
        "{\n"
        "    switch (w)\n"
        "    {\n"
        f"{cases}"
        "    default:\n"
        "        return 0;  // ungrouped — exact match only\n"
        "    }\n"
        "}\n"
        "\n"
        "static bool8 WeatherMatches(u16 rule, u16 current)\n"
        "{\n"
        "    u8 g;\n"
        "    if (rule == current)\n"
        "        return TRUE;\n"
        "    g = WeatherGroup(rule);\n"
        "    return g != 0 && g == WeatherGroup(current);\n"
        "}\n"
    )


def _create_form_change_c(path, project_root):
    c = _FORM_CHANGE_C.replace("__WEATHER_GROUP_FUNCS__",
                               _weather_group_funcs(project_root))
    return _write_if_changed(path, c)


# ── Layer C: live in-battle reactive form changes (status / HP threshold) ──
# A new src/in_battle_forms.c (auto-compiled via the Makefile's src/*.c glob) morphs the
# ACTIVE battler mid-battle from its live gBattleMons state and repaints it. Hooked into
# the per-battler end-of-turn sweep (DoBattlerEndTurnEffects) as a new ENDTURN_FORM_CHANGE
# state. It also writes the party species so stats recompute via CalculateMonStats and
# the form reconciles after battle via the overworld resolver. (Slice 1: status + HP.)
_IN_BATTLE_FORMS_C = '''#include "global.h"
#include "pokemon.h"
#include "battle.h"
#include "battle_main.h"
#include "battle_anim.h"
#include "battle_gfx_sfx_util.h"
#include "battle_interface.h"
#include "sprite.h"
#include "task.h"
#include "gpu_regs.h"
#include "string_util.h"
#include "data.h"
#include "constants/species.h"
#include "constants/battle.h"
#include "constants/battle_string_ids.h"
#include "constants/form_change_types.h"

// Castform-style battle script run after a reactive morph: a short pause lets the
// mosaic transform shimmer (Task_FormChangeMosaic, kicked off when the data changed)
// play out and swap the sprite, THEN the morph is announced in the battle log
// ("<mon> transformed!"), then we return to the move-end / end-of-turn state machine
// that pushed us. Hand-assembled from the standard battle-script opcodes so no .s
// file or header has to be touched:
//   0x39 pause <frames:2>  0x10 printstring <id:2>  0x12 waitmessage <time:2>  0x3c return
const u8 BattleScript_FormChange[] = {
    0x39, 32, 0,
    0x10, STRINGID_PKMNTRANSFORMED & 0xFF, STRINGID_PKMNTRANSFORMED >> 8,
    0x12, B_WAIT_TIME_LONG & 0xFF, B_WAIT_TIME_LONG >> 8,
    0x3c,
};

static void RepaintBattlerGfx(u8 battler);

// Resolve the party Pokemon backing a battler (player or opponent side).
static struct Pokemon *InBattlePartyMon(u8 battler)
{
    struct Pokemon *party = (GetBattlerSide(battler) == B_SIDE_PLAYER) ? gPlayerParty : gEnemyParty;
    return &party[gBattlerPartyIndexes[battler]];
}

// Change the battler's DATA to *target* immediately (so battle logic uses the new
// species right away): recompute stats via the engine's own CalculateMonStats, copy
// them + the types into gBattleMons, clamp battle HP, write the party species (so the
// form persists and the overworld resolver reconciles it after battle), and rename an
// un-nicknamed mon to the new species. The SPRITE is repainted separately, at the
// mosaic peak, by Task_FormChangeMosaic — so the visual swap is hidden under the shimmer.
static void MorphBattlerData(u8 battler, u16 target)
{
    struct Pokemon *mon = InBattlePartyMon(battler);
    u16 old = gBattleMons[battler].species;
    u16 oldMaxHP = gBattleMons[battler].maxHP;
    s32 newHp;
    u8 nick[POKEMON_NAME_LENGTH + 1];

    GetMonData(mon, MON_DATA_NICKNAME, nick);   // for the not-nicknamed rename check below

    SetMonData(mon, MON_DATA_SPECIES, &target);
    CalculateMonStats(mon);                     // recompute stats for the new form (also mutates party HP; overridden below)
    gBattleMons[battler].species   = target;
    gBattleMons[battler].maxHP     = GetMonData(mon, MON_DATA_MAX_HP, NULL);
    gBattleMons[battler].attack    = GetMonData(mon, MON_DATA_ATK, NULL);
    gBattleMons[battler].defense   = GetMonData(mon, MON_DATA_DEF, NULL);
    gBattleMons[battler].speed     = GetMonData(mon, MON_DATA_SPEED, NULL);
    gBattleMons[battler].spAttack  = GetMonData(mon, MON_DATA_SPATK, NULL);
    gBattleMons[battler].spDefense = GetMonData(mon, MON_DATA_SPDEF, NULL);
    gBattleMons[battler].type1     = gSpeciesInfo[target].types[0];
    gBattleMons[battler].type2     = gSpeciesInfo[target].types[1];

    // HP sync for forms whose maxHP DIFFERS (e.g. Power Construct): carry the same maxHP delta into the LIVE
    // battle HP (a fainted mon stays at 0), clamp to the new max, write it back to the party mon so battle and
    // party HP never diverge, and refresh the health bar. Same-maxHP forms (Stance/Forecast/Zen/…) are a no-op.
    newHp = gBattleMons[battler].hp;
    if (newHp != 0 && gBattleMons[battler].maxHP != oldMaxHP)
        newHp += (s32)gBattleMons[battler].maxHP - (s32)oldMaxHP;
    if (newHp < 1)
        newHp = (gBattleMons[battler].hp != 0) ? 1 : 0; // a maxHP drop never faints on its own
    if (newHp > gBattleMons[battler].maxHP)
        newHp = gBattleMons[battler].maxHP;
    gBattleMons[battler].hp = newHp;
    SetMonData(mon, MON_DATA_HP, &gBattleMons[battler].hp);
    if (gBattleMons[battler].maxHP != oldMaxHP)
        UpdateHealthboxAttribute(gHealthboxSpriteIds[battler], mon, HEALTHBOX_HEALTH_BAR);

    if (StringCompare(nick, gSpeciesNames[old]) == 0)
    {
        SetMonData(mon, MON_DATA_NICKNAME, gSpeciesNames[target]);
        UpdateNickInHealthbox(gHealthboxSpriteIds[battler], mon);
    }
}

// Repaint the battler sprite for its CURRENT (already-morphed) species: BattleLoad*
// fills the shared gfx buffer + loads the palette; then DMA that buffer into the
// sprite's OBJ-VRAM tiles (without the DMA only the palette would change). Mirrors
// HandleSpeciesGfxDataChange. Called at the mosaic peak by Task_FormChangeMosaic.
static void RepaintBattlerGfx(u8 battler)
{
    struct Pokemon *mon = InBattlePartyMon(battler);
    u8 position = GetBattlerPosition(battler);

    if (GetBattlerSide(battler) == B_SIDE_PLAYER)
        BattleLoadPlayerMonSpriteGfx(mon, battler);
    else
        BattleLoadOpponentMonSpriteGfx(mon, battler);
    DmaCopy32(3, gMonSpritesGfxPtr->sprites[position],
              (void *)(VRAM + 0x10000 + gSprites[gBattlerSpriteIds[battler]].oam.tileNum * 32),
              0x800);
    StartSpriteAnim(&gSprites[gBattlerSpriteIds[battler]], 0);
}

// Mosaic transform shimmer (OBJ mosaic on the battler sprite), modelled on the engine's
// own AnimTask_TransformMon: ramp the sprite's mosaic up to full, swap the sprite gfx at
// the peak (so the change is hidden), then ramp back down and clear. Runs as a normal
// task while the battle script (BattleScript_FormChange) pauses, so the morph is animated
// rather than an instant snap. OBJ mosaic touches only sprites with oam.mosaic set, so the
// health boxes and other battlers are unaffected.
#define tState    data[0]
#define tStep     data[1]
#define tTick     data[2]
#define tBattler  data[3]
static void Task_FormChangeMosaic(u8 taskId)
{
    s16 *data = gTasks[taskId].data;
    u8 spriteId = gBattlerSpriteIds[tBattler];
    u16 m;

    switch (tState)
    {
    case 0:
        gSprites[spriteId].oam.mosaic = TRUE;
        SetGpuReg(REG_OFFSET_MOSAIC, 0);
        tState++;
        break;
    case 1:                                 // ramp OBJ mosaic up to 15 (one step / 2 frames)
        if (tTick++ > 0)
        {
            tTick = 0;
            m = ++tStep;
            SetGpuReg(REG_OFFSET_MOSAIC, (m << 12) | (m << 8));
            if (m >= 15)
                tState++;
        }
        break;
    case 2:                                 // swap the sprite at the peak, hidden by the mosaic
        RepaintBattlerGfx(tBattler);
        tState++;
        break;
    case 3:                                 // ramp back down to 0, revealing the new form
        if (tTick++ > 0)
        {
            tTick = 0;
            m = --tStep;
            SetGpuReg(REG_OFFSET_MOSAIC, (m << 12) | (m << 8));
            if (m == 0)
                tState++;
        }
        break;
    default:
        gSprites[spriteId].oam.mosaic = FALSE;
        SetGpuReg(REG_OFFSET_MOSAIC, 0);
        DestroyTask(taskId);
        break;
    }
}
#undef tState
#undef tStep
#undef tTick
#undef tBattler

// Morph the battler to *target*: change the data NOW (battle logic stays correct), then
// kick off the mosaic shimmer task that repaints the sprite at its peak. The caller
// (move-end hook) pushes BattleScript_FormChange, whose pause lets the shimmer play.
static void MorphBattlerSpecies(u8 battler, u16 target)
{
    u8 taskId;

    if (target == SPECIES_NONE || target == FORM_SPECIES_END || target == gBattleMons[battler].species)
        return;                                 // FORM_SPECIES_END guard: never morph to the table terminator
    MorphBattlerData(battler, target);
    taskId = CreateTask(Task_FormChangeMosaic, 10);
    gTasks[taskId].data[3] = battler;
}

// Re-evaluate a battler's reactive form from its LIVE battle state. Called per battler
// at move-end (so a move-inflicted status morphs the instant it lands) and at end of
// turn (HP threshold / end-of-turn status). In-battle methods: STATUS (shared with the
// overworld layer) and HP_BELOW. Defaults to the base species (the revert) when nothing
// matches. Returns TRUE if it actually morphed — the caller then announces it.
bool8 TryInBattleFormChange(u8 battler)
{
    u16 species = gBattleMons[battler].species;
    const u16 *forms = gSpeciesInfo[species].formSpeciesIdTable;
    const struct FormChange *t;
    u16 base, target;
    u32 i;

    if (gBattleMons[battler].hp == 0)
        return FALSE;                   // a fainted battler never morphs
    if (forms == NULL)
        return FALSE;                   // not a form-capable species
    base = forms[0];
    t = gSpeciesInfo[base].formChangeTable;
    if (t == NULL)
        return FALSE;

    // A form reached by USING an item, or by an EVENT-driven battle ability (Stance Change / Battle Bond /
    // Gulp Missile / Hunger Switch), is "sticky" — the reactive checker must never auto-revert it. Only the
    // genuinely reactive triggers below (status / weather / HP threshold) revert when their condition ends.
    for (i = 0; t[i].method != FORM_CHANGE_END; i++)
        if (t[i].targetSpecies == species
            && (t[i].method == FORM_CHANGE_ITEM_USE
             || t[i].method == FORM_CHANGE_BATTLE_KO
             || t[i].method == FORM_CHANGE_BATTLE_MOVE
             || t[i].method == FORM_CHANGE_BATTLE_TURN
             || t[i].method == FORM_CHANGE_BATTLE_STANCE_BLADE
             || t[i].method == FORM_CHANGE_BATTLE_STANCE_SHIELD))
            return FALSE;

    target = base;
    for (i = 0; t[i].method != FORM_CHANGE_END; i++)
    {
        if (t[i].method == FORM_CHANGE_STATUS && (gBattleMons[battler].status1 & t[i].param))
        {
            target = t[i].targetSpecies;
            break;
        }
    }
/*__REACTIVE_ABILITY_BRANCHES__*/
    if (target == base && gBattleMons[battler].maxHP != 0)
    {
        for (i = 0; t[i].method != FORM_CHANGE_END; i++)
        {
            if (t[i].method == FORM_CHANGE_HP_BELOW
                && (u32)gBattleMons[battler].hp * 100 / gBattleMons[battler].maxHP < t[i].param)
            {
                target = t[i].targetSpecies;
                break;
            }
        }
    }

    if (target != species)
    {
        MorphBattlerSpecies(battler, target);
        return TRUE;
    }
    return FALSE;
}
/*__FORM_ABILITY_HOOKS__*/
'''


# The battle-ability form triggers, each keyed to the ABILITY_* constant that drives it.
# The patcher emits a trigger's engine code ONLY when the target project defines its
# ability (read from the project's own abilities.h) — so a vanilla decomp gets none of
# it and its output is byte-identical. Mirrors _weather_group_funcs' "reference only what
# exists" rule.
_FORM_CHANGE_ABILITIES = (
    "ABILITY_STANCE_CHANGE", "ABILITY_SCHOOLING", "ABILITY_FORECAST",
    "ABILITY_FLOWER_GIFT", "ABILITY_HUNGER_SWITCH", "ABILITY_BATTLE_BOND",
    "ABILITY_GULP_MISSILE",
)


def _split_present(project_root):
    """True if the project has the physical/special split (a DAMAGE_CATEGORY_STATUS
    constant). Stance Change keys off move category, so its generated hook references
    that constant and the `.category` move field; gating on this keeps a split-less
    project that (oddly) defines ABILITY_STANCE_CHANGE from failing to compile."""
    for rel in (("include", "constants", "pokemon.h"),
                ("include", "pokemon.h"),
                ("include", "battle.h")):
        p = os.path.join(project_root, *rel)
        if os.path.isfile(p) and "DAMAGE_CATEGORY_STATUS" in _read(p):
            return True
    return False


def _form_change_abilities(project_root):
    """Return the subset of _FORM_CHANGE_ABILITIES that the target project actually
    defines. Whole-token, comment-safe (a `// #define ABILITY_X` never matches, since
    the regex requires #define at the start of the line). Empty on a vanilla decomp.

    Stance Change is additionally dropped when the physical/special split is absent
    (its generated hook references DAMAGE_CATEGORY_STATUS / the .category move field);
    this keeps every downstream consumer — function emission, prototype, call site,
    and the shared FindBattleFormTarget gate — consistently Stance-free so a
    split-less project still builds."""
    ah = os.path.join(project_root, "include", "constants", "abilities.h")
    try:
        with open(ah, encoding="utf-8") as f:
            defined = set(re.findall(r"^\s*#define\s+(ABILITY_\w+)\s+\d+",
                                     f.read(), re.MULTILINE))
    except Exception:
        defined = set()
    have = {a for a in _FORM_CHANGE_ABILITIES if a in defined}
    if "ABILITY_STANCE_CHANGE" in have and not _split_present(project_root):
        have.discard("ABILITY_STANCE_CHANGE")
        logging.getLogger(__name__).warning(
            "form-change patch: ABILITY_STANCE_CHANGE is defined but no physical/"
            "special split (DAMAGE_CATEGORY_STATUS) was detected — skipping the Stance "
            "Change form hook so the project still compiles.")
    return have


def _reactive_ability_branches(have):
    """C for the two REACTIVE ability form checks (weather / Schooling) inside
    TryInBattleFormChange — emitted only for abilities the project has. HP-below
    (Zen Mode / Shields Down / Power Construct) rides the always-present generic
    block below the placeholder, so it needs nothing here."""
    out = ""
    if "ABILITY_HUNGER_SWITCH" in have:
        # Hunger Switch manages its own forms via TryHungerSwitchForm's direct forms[0]<->forms[1] toggle
        # (which ignores the table), so the reactive checker must never revert it — regardless of whether the
        # project authored FORM_CHANGE_BATTLE_TURN rows. This ability-gated skip makes that robust.
        out += ("    if (gBattleMons[battler].ability == ABILITY_HUNGER_SWITCH)\n"
                "        return FALSE;\n")
    weather = [a for a in ("ABILITY_FORECAST", "ABILITY_FLOWER_GIFT") if a in have]
    if weather:
        cond = " || ".join("gBattleMons[battler].ability == %s" % a for a in weather)
        out += (
            "    // Forecast / Flower Gift — hold the form matching the current in-battle weather.\n"
            "    if (target == base && (%s) && WEATHER_HAS_EFFECT)\n"
            "    {\n"
            "        for (i = 0; t[i].method != FORM_CHANGE_END; i++)\n"
            "            if (t[i].method == FORM_CHANGE_BATTLE_WEATHER && (gBattleWeather & t[i].param))\n"
            "            {\n"
            "                target = t[i].targetSpecies;\n"
            "                break;\n"
            "            }\n"
            "    }\n" % cond)
    if "ABILITY_SCHOOLING" in have:
        out += (
            "    // Schooling — School form while HP% >= param AND level >= 20.\n"
            "    if (target == base && gBattleMons[battler].maxHP != 0\n"
            "        && gBattleMons[battler].ability == ABILITY_SCHOOLING && gBattleMons[battler].level >= 20)\n"
            "    {\n"
            "        for (i = 0; t[i].method != FORM_CHANGE_END; i++)\n"
            "            if (t[i].method == FORM_CHANGE_BATTLE_HP_ABOVE\n"
            "                && (u32)gBattleMons[battler].hp * 100 / gBattleMons[battler].maxHP >= t[i].param)\n"
            "            {\n"
            "                target = t[i].targetSpecies;\n"
            "                break;\n"
            "            }\n"
            "    }\n")
    return out


_HOOK_STANCE = (
    "// ability port: Stance Change — Blade form on a DAMAGING move / Shield form on a STATUS move, BEFORE the\n"
    "// move resolves so the new form's stats drive it. Called from Cmd_attackcanceler (past the can't-move gates).\n"
    "bool8 TryStanceChangeForm(u8 battler, u16 move)\n"
    "{\n"
    "    u8 wantMethod;\n"
    "    u16 target;\n"
    "    if (gBattleMons[battler].hp == 0 || gBattleMons[battler].ability != ABILITY_STANCE_CHANGE)\n"
    "        return FALSE;\n"
    "    wantMethod = (gBattleMoves[move].category == DAMAGE_CATEGORY_STATUS)\n"
    "        ? FORM_CHANGE_BATTLE_STANCE_SHIELD : FORM_CHANGE_BATTLE_STANCE_BLADE;\n"
    "    target = FindBattleFormTarget(battler, wantMethod, FALSE, 0);\n"
    "    if (target != SPECIES_NONE && target != gBattleMons[battler].species)\n"
    "    {\n"
    "        MorphBattlerSpecies(battler, target);\n"
    "        return TRUE;\n"
    "    }\n"
    "    return FALSE;\n"
    "}\n")

_HOOK_HUNGER = (
    "// ability port: Hunger Switch — toggle between the two forms every end of turn. Guards a 1-form (mis-\n"
    "// authored) table so forms[1] is never read out of bounds.\n"
    "bool8 TryHungerSwitchForm(u8 battler)\n"
    "{\n"
    "    u16 species = gBattleMons[battler].species;\n"
    "    const u16 *forms = gSpeciesInfo[species].formSpeciesIdTable;\n"
    "    u16 target;\n"
    "    if (gBattleMons[battler].hp == 0 || gBattleMons[battler].ability != ABILITY_HUNGER_SWITCH || forms == NULL)\n"
    "        return FALSE;\n"
    "    if (forms[1] == FORM_SPECIES_END)   // needs two forms to toggle\n"
    "        return FALSE;\n"
    "    target = (species == forms[0]) ? forms[1] : forms[0];\n"
    "    if (target != SPECIES_NONE && target != FORM_SPECIES_END && target != species)\n"
    "    {\n"
    "        MorphBattlerSpecies(battler, target);\n"
    "        return TRUE;\n"
    "    }\n"
    "    return FALSE;\n"
    "}\n")

_HOOK_BATTLE_BOND = (
    "// ability port: Battle Bond — morph once to the bonded form after the holder lands a KO (self-gated;\n"
    "// naturally once-per-battle, the bonded form has no KO row back).\n"
    "bool8 TryBattleBondForm(u8 battler)\n"
    "{\n"
    "    u16 target;\n"
    "    if (gBattleMons[battler].hp == 0 || gBattleMons[battler].ability != ABILITY_BATTLE_BOND)\n"
    "        return FALSE;\n"
    "    target = FindBattleFormTarget(battler, FORM_CHANGE_BATTLE_KO, FALSE, 0);\n"
    "    if (target != SPECIES_NONE && target != gBattleMons[battler].species)\n"
    "    {\n"
    "        MorphBattlerSpecies(battler, target);\n"
    "        return TRUE;\n"
    "    }\n"
    "    return FALSE;\n"
    "}\n")

_HOOK_GULP = (
    "// ability port: Gulp Missile — morph to the gulping form when the holder uses the move in a\n"
    "// FORM_CHANGE_BATTLE_MOVE row's param (Surf/Dive). (Spit-on-being-hit is a separate TODO.)\n"
    "bool8 TryGulpMissileForm(u8 battler, u16 move)\n"
    "{\n"
    "    u16 target;\n"
    "    if (gBattleMons[battler].hp == 0 || gBattleMons[battler].ability != ABILITY_GULP_MISSILE)\n"
    "        return FALSE;\n"
    "    target = FindBattleFormTarget(battler, FORM_CHANGE_BATTLE_MOVE, TRUE, move);\n"
    "    if (target != SPECIES_NONE && target != gBattleMons[battler].species)\n"
    "    {\n"
    "        MorphBattlerSpecies(battler, target);\n"
    "        return TRUE;\n"
    "    }\n"
    "    return FALSE;\n"
    "}\n")

_FIND_FORM_TARGET = (
    "// Shared lookup for the event-driven ability hooks: find the target species of the first row with\n"
    "// `wantMethod` in this battler's (base form's) table. SPECIES_NONE if none / not form-capable.\n"
    "static u16 FindBattleFormTarget(u8 battler, u8 wantMethod, bool8 checkParam, u16 matchParam)\n"
    "{\n"
    "    u16 species = gBattleMons[battler].species;\n"
    "    const u16 *forms = gSpeciesInfo[species].formSpeciesIdTable;\n"
    "    const struct FormChange *t;\n"
    "    u32 i;\n"
    "    if (forms == NULL)\n"
    "        return SPECIES_NONE;\n"
    "    t = gSpeciesInfo[forms[0]].formChangeTable;\n"
    "    if (t == NULL)\n"
    "        return SPECIES_NONE;\n"
    "    for (i = 0; t[i].method != FORM_CHANGE_END; i++)\n"
    "        if (t[i].method == wantMethod && (!checkParam || t[i].param == matchParam))\n"
    "            return t[i].targetSpecies;\n"
    "    return SPECIES_NONE;\n"
    "}\n")


def _form_ability_hooks(have):
    """C for the event-driven ability hook functions — only those whose ability the
    project has. FindBattleFormTarget is emitted only if a hook that uses it is
    present (avoids an unused-static warning on projects that have only Hunger Switch)."""
    out = ""
    uses_lookup = any(a in have for a in
                      ("ABILITY_STANCE_CHANGE", "ABILITY_BATTLE_BOND", "ABILITY_GULP_MISSILE"))
    if uses_lookup:
        out += "\n" + _FIND_FORM_TARGET
    if "ABILITY_STANCE_CHANGE" in have:
        out += "\n" + _HOOK_STANCE
    if "ABILITY_HUNGER_SWITCH" in have:
        out += "\n" + _HOOK_HUNGER
    if "ABILITY_BATTLE_BOND" in have:
        out += "\n" + _HOOK_BATTLE_BOND
    if "ABILITY_GULP_MISSILE" in have:
        out += "\n" + _HOOK_GULP
    return out


def _create_in_battle_forms_c(path, project_root):
    have = _form_change_abilities(project_root)
    text = (_IN_BATTLE_FORMS_C
            .replace("/*__REACTIVE_ABILITY_BRANCHES__*/\n", _reactive_ability_branches(have))
            .replace("/*__FORM_ABILITY_HOOKS__*/\n", _form_ability_hooks(have)))
    return _write_if_changed(path, text)


# DoBattlerEndTurnEffects per-battler state machine: add ENDTURN_FORM_CHANGE just before
# the terminal ENDTURN_BATTLER_COUNT (enum + switch case). TryInBattleFormChange is
# declared in pokemon.h (pulled in via global.h), so no local forward decl is added —
# a duplicate decl only risks drifting out of sync with the pokemon.h prototype.
_ENDTURN_ENUM_ANCHOR = "    ENDTURN_ITEMS2,\n    ENDTURN_BATTLER_COUNT\n};"
_ENDTURN_ENUM_HOOKED = "    ENDTURN_ITEMS2,\n    ENDTURN_FORM_CHANGE,\n    ENDTURN_BATTLER_COUNT\n};"
_ENDTURN_CASE_ANCHOR = "            case ENDTURN_BATTLER_COUNT:  // done\n"
_ENDTURN_CASE_HOOKED = (
    "            case ENDTURN_FORM_CHANGE:  // PorySuite-Z Layer C: reactive in-battle forms\n"
    "                TryInBattleFormChange(gActiveBattler);\n"
    "                gBattleStruct->turnEffectsTracker++;\n"
    "                break;\n"
    "            case ENDTURN_BATTLER_COUNT:  // done\n")


def _patch_battle_endturn(path):
    """Hook the per-battler end-of-turn sweep (DoBattlerEndTurnEffects in battle_util.c)
    to re-evaluate each battler's reactive form via TryInBattleFormChange — status1/HP
    are settled and weather is current there. Idempotent; skips gracefully if the file
    doesn't match the expected shape."""
    if not os.path.isfile(path):
        return False
    text = _read(path)
    if "TryInBattleFormChange" in text:
        return False
    if _ENDTURN_ENUM_ANCHOR not in text or _ENDTURN_CASE_ANCHOR not in text:
        return False
    text = text.replace(_ENDTURN_ENUM_ANCHOR, _ENDTURN_ENUM_HOOKED, 1)
    text = text.replace(_ENDTURN_CASE_ANCHOR, _ENDTURN_CASE_HOOKED, 1)
    _write_if_changed(path, text)
    return True


# ── Layer C timing: also fire the reactive check at MOVE-END ──
# The end-of-turn sweep above re-evaluates every battler's form, but a status
# inflicted by a move (paralysis, poison, burn, freeze, sleep) must morph the mon
# THE INSTANT it lands — right after the move resolves and its "is paralyzed!"
# message shows, before the next action — not jarringly at end of turn after a
# whole extra move has played. Cmd_moveend's per-move state machine in
# battle_script_commands.c is exactly that point. We append a MOVEEND_FORM_CHANGE
# state (renumbering the terminal MOVEEND_COUNT in the constants header) that
# re-checks every battler via the same idempotent resolver (a no-op for a battler
# that is already in its correct form, so end-of-turn never double-fires).
_MOVEEND_COUNT_RE = re.compile(r"#define\s+MOVEEND_COUNT\s+(\d+)")
_MOVEEND_DECL_ANCHOR = "static void Cmd_moveend(void)"
# TryInBattleFormChange is declared in pokemon.h (via global.h); only the battle
# script array (defined in in_battle_forms.c) needs a local extern here.
_MOVEEND_DECL_HOOKED = (
    "extern const u8 BattleScript_FormChange[];   // PorySuite-Z Layer C (in_battle_forms.c)\n"
    "static void Cmd_moveend(void)")
_MOVEEND_CASE_ANCHOR = "        case MOVEEND_COUNT:\n            break;\n"
# Loop every battler; the first that morphs gets announced ("<mon> transformed!")
# via the pushed BattleScript_FormChange, then we re-enter this same state (do NOT
# advance) so a second morph in the same move-end is announced too. Only advance
# once no battler morphs — the engine's own MOVEEND_IMMUNITY_ABILITIES pattern.
_MOVEEND_CASE_HOOKED = (
    "        case MOVEEND_FORM_CHANGE:  // PorySuite-Z Layer C: reactive in-battle forms\n"
    "            {\n"
    "                u32 i;\n"
    "                for (i = 0; i < gBattlersCount; i++)\n"
    "                {\n"
    "                    if (TryInBattleFormChange(i))\n"
    "                    {\n"
    "                        gBattleScripting.battler = i;\n"
    "                        BattleScriptPushCursor();\n"
    "                        gBattlescriptCurrInstr = BattleScript_FormChange;\n"
    "                        effect = TRUE;\n"
    "                        break;\n"
    "                    }\n"
    "                }\n"
    "            }\n"
    "            if (!effect)\n"
    "                gBattleScripting.moveendState++;\n"
    "            break;\n"
    "        case MOVEEND_COUNT:\n            break;\n")


def _patch_battle_moveend(commands_c, header):
    """Hook Cmd_moveend (battle_script_commands.c) with a MOVEEND_FORM_CHANGE state
    so a move-inflicted status morphs the mon the instant the move resolves — the
    timing the end-of-turn sweep can't give (it fires a whole action later).
    Renumbers the terminal MOVEEND_COUNT in constants/battle_script_commands.h and
    adds an extern for the BattleScript_FormChange message script. Idempotent; skipped
    gracefully if either file's shape differs (the end-of-turn hook still covers
    HP-threshold and end-of-turn-inflicted changes)."""
    if not os.path.isfile(commands_c) or not os.path.isfile(header):
        return False
    ctext = _read(commands_c)
    if "MOVEEND_FORM_CHANGE" in ctext:
        return False
    htext = _read(header)
    m = _MOVEEND_COUNT_RE.search(htext)
    if (m is None or _MOVEEND_CASE_ANCHOR not in ctext
            or _MOVEEND_DECL_ANCHOR not in ctext):
        return False
    n = int(m.group(1))
    htext = htext.replace(
        m.group(0),
        "#define MOVEEND_FORM_CHANGE                     {}\n"
        "#define MOVEEND_COUNT                           {}".format(n, n + 1), 1)
    _write_if_changed(header, htext)
    if "BattleScript_FormChange" not in ctext:
        ctext = ctext.replace(_MOVEEND_DECL_ANCHOR, _MOVEEND_DECL_HOOKED, 1)
    ctext = ctext.replace(_MOVEEND_CASE_ANCHOR, _MOVEEND_CASE_HOOKED, 1)
    _write_if_changed(commands_c, ctext)
    return True


_PARTY_MENU_ANCHOR = "    ResetPartyMenu();\n    sPartyMenuInternal = Alloc("
_PARTY_MENU_HOOKED = (
    "    ResetPartyMenu();\n"
    "    if (menuType == PARTY_MENU_TYPE_FIELD)\n"
    "        TryUpdateOverworldFormChanges();\n"
    "    sPartyMenuInternal = Alloc(")


def _patch_party_menu(path):
    """Re-evaluate held-item / weather / time-of-day form changes when the FIELD
    party menu opens: the party is valid there and is redrawn fresh, so a morphed
    form shows immediately and there is no battle desync (battle party menus are
    excluded by the PARTY_MENU_TYPE_FIELD gate). Skipped gracefully (returns
    False) if party_menu.c doesn't match the expected shape, so an unusual
    project still gets the rest of Layer B."""
    if not os.path.isfile(path):
        return False
    text = _read(path)
    if "TryUpdateOverworldFormChanges" in text:
        return False
    if _PARTY_MENU_ANCHOR not in text:
        return False
    _write_if_changed(path, text.replace(_PARTY_MENU_ANCHOR, _PARTY_MENU_HOOKED, 1))
    return True


_BATTLE_ANCHOR = "    SetUpBattleVars();\n"
_BATTLE_HOOKED = (
    "    SetUpBattleVars();\n"
    "    TryUpdateOverworldFormChanges();   // PorySuite-Z: morph weather/time/item\n"
    "                                       // forms before the battlers load\n")


def _patch_battle_start(path):
    """Re-evaluate form changes at the START of every battle (in
    CB2_InitBattleInternal, right after SetUpBattleVars(), before the battlers are
    created) — so a Pokémon in (e.g.) rainy weather enters battle already in its
    weather form, with no mid-battle desync. Without this the overworld weather
    only triggers a morph when the field party menu is opened. Skipped gracefully
    if battle_main.c doesn't match the expected shape."""
    if not os.path.isfile(path):
        return False
    text = _read(path)
    if "TryUpdateOverworldFormChanges" in text:
        return False
    if _BATTLE_ANCHOR not in text:
        return False
    _write_if_changed(path, text.replace(_BATTLE_ANCHOR, _BATTLE_HOOKED, 1))
    return True


# Overworld weather-change hook. Every in-field weather change (field scripts,
# walking into a weather zone, map weather) funnels through Task_WeatherMain's
# transition commit — the moment currWeather actually becomes the new weather. We
# re-evaluate the party's forms right there so a weather form morphs AND reverts
# in real time in the overworld, not only when the party menu opens or a battle
# starts. The HUD (a per-frame poller) then reflects it on its next tick.
_WEATHER_DEF_ANCHOR = "static void Task_WeatherMain(u8 taskId)\n{"
_WEATHER_DEF_HOOKED = (
    "void TryUpdateOverworldFormChanges(void);   // PorySuite-Z Layer B (fwd decl)\n\n"
    "static void Task_WeatherMain(u8 taskId)\n{")
_WEATHER_COMMIT_ANCHOR = (
    "            gWeatherPtr->currWeather = gWeatherPtr->nextWeather;\n"
    "            gWeatherPtr->weatherChangeComplete = TRUE;\n")
_WEATHER_COMMIT_HOOKED = (
    "            gWeatherPtr->currWeather = gWeatherPtr->nextWeather;\n"
    "            gWeatherPtr->weatherChangeComplete = TRUE;\n"
    "            TryUpdateOverworldFormChanges();   // PorySuite-Z: weather-driven\n"
    "                                               // forms morph/revert on change\n")


def _patch_weather_main(path):
    """Re-evaluate overworld form changes whenever the field weather actually
    changes (Task_WeatherMain's transition commit). This is what makes a
    weather form morph IN and revert OUT in real time as the player changes the
    weather, with no need to open the party menu. A local forward declaration is
    added so field_weather.c needn't include pokemon.h. Skipped gracefully if the
    file doesn't match the expected shape, so an unusual project still gets the
    rest of Layer B."""
    if not os.path.isfile(path):
        return False
    text = _read(path)
    if "TryUpdateOverworldFormChanges" in text:
        return False
    if _WEATHER_DEF_ANCHOR not in text or _WEATHER_COMMIT_ANCHOR not in text:
        return False
    text = text.replace(_WEATHER_DEF_ANCHOR, _WEATHER_DEF_HOOKED, 1)
    text = text.replace(_WEATHER_COMMIT_ANCHOR, _WEATHER_COMMIT_HOOKED, 1)
    _write_if_changed(path, text)
    return True


# Weather-APPLY hook. DoCurrentWeather() (weather resume) and SetWeather()
# (field scripts) are the high-level "apply this weather now" entry points. Unlike
# the Task_WeatherMain transition commit, they run even when the weather is set to
# the value it already has — e.g. a field script that re-applies a map's current weather.
# Without this, a forward form change silently fails in that case (no transition →
# no morph) until the player opens the party menu. TryUpdateOverworldFormChanges
# reads the SAVED weather, which both functions set before we call it, so the morph
# is correct immediately. field_weather_util.c includes field_weather.h (declares
# GetSav1Weather) but not pokemon.h, so a forward declaration is injected.
_UTIL_PROTO_ANCHOR = "static void UpdateRainCounter(u8 newWeather, u8 oldWeather);\n"
_UTIL_PROTO_HOOKED = (
    "static void UpdateRainCounter(u8 newWeather, u8 oldWeather);\n"
    "void TryUpdateOverworldFormChanges(void);   // PorySuite-Z Layer B (fwd decl)\n")
_UTIL_CALL = "    TryUpdateOverworldFormChanges();   // PorySuite-Z: weather-driven forms\n"
# DoCurrentWeather() ends `SetNextWeather(weather);` then `}`.
_UTIL_DO_ANCHOR = "    SetNextWeather(weather);\n}"
_UTIL_DO_HOOKED = "    SetNextWeather(weather);\n" + _UTIL_CALL + "}"
# SetWeather() ends `SetNextWeather(GetSav1Weather());` then `}` (SetWeather_Unused
# uses SetCurrentAndNextWeather, so this anchor is unique to SetWeather).
_UTIL_SET_ANCHOR = "    SetNextWeather(GetSav1Weather());\n}"
_UTIL_SET_HOOKED = "    SetNextWeather(GetSav1Weather());\n" + _UTIL_CALL + "}"


def _patch_weather_util(path):
    """Re-evaluate overworld forms whenever weather is APPLIED via DoCurrentWeather()
    / SetWeather(). These fire even when the weather doesn't actually change value
    (the Sun's-Song-on-an-already-sunny-map case), so a forward form change no longer
    needs a party-menu trip to take effect. Idempotent; skipped gracefully if the
    file's shape differs (the Task_WeatherMain hook still covers real transitions)."""
    if not os.path.isfile(path):
        return False
    text = _read(path)
    if "TryUpdateOverworldFormChanges" in text:
        return False
    if (_UTIL_PROTO_ANCHOR not in text
            or _UTIL_DO_ANCHOR not in text
            or _UTIL_SET_ANCHOR not in text):
        return False
    text = text.replace(_UTIL_PROTO_ANCHOR, _UTIL_PROTO_HOOKED, 1)
    text = text.replace(_UTIL_DO_ANCHOR, _UTIL_DO_HOOKED, 1)
    text = text.replace(_UTIL_SET_ANCHOR, _UTIL_SET_HOOKED, 1)
    _write_if_changed(path, text)
    return True


def _verify_hooks(project_root):
    """Log a warning for any source file that EXISTS but did not receive its
    TryUpdateOverworldFormChanges hook. Those hooks are 'feature works' not
    'compiles', so a missed anchor doesn't break the build — it silently leaves
    that trigger dead in-game. Logging (not raising) keeps an unusual project
    building while making the half-wired state visible. Returns the missing list."""
    checks = {
        "party_menu.c": "field party-menu morph",
        "battle_main.c": "battle-start morph",
        "field_weather.c": "weather-transition morph",
        "field_weather_util.c": "weather-apply morph",
    }
    missing = []
    for fn, what in checks.items():
        p = os.path.join(project_root, "src", fn)
        if os.path.isfile(p) and "TryUpdateOverworldFormChanges" not in _read(p):
            missing.append(f"{fn} ({what})")
    # Layer C end-of-turn hook uses a different marker (TryInBattleFormChange).
    bu = os.path.join(project_root, "src", "battle_util.c")
    if os.path.isfile(bu) and "TryInBattleFormChange" not in _read(bu):
        missing.append("battle_util.c (in-battle end-of-turn morph)")
    bsc = os.path.join(project_root, "src", "battle_script_commands.c")
    if os.path.isfile(bsc) and "MOVEEND_FORM_CHANGE" not in _read(bsc):
        missing.append("battle_script_commands.c (in-battle move-end morph)")
    if missing:
        logging.getLogger(__name__).warning(
            "form-change patch: hook(s) did NOT apply — the trigger won't fire "
            "in-game (anchors not matched): %s", ", ".join(missing))
    return missing


def _self_check(pokemon_h, types_h, form_c):
    """Fail loudly rather than leave the engine half-patched."""
    ph = _read(pokemon_h)
    if _STRUCT_MARKER not in ph or _FIELD_MARKER not in ph or _PROTO_MARKER not in ph:
        raise RuntimeError("form-change patch self-check: pokemon.h incomplete")
    if _TYPES_GUARD not in _read(types_h):
        raise RuntimeError("form-change patch self-check: form_change_types.h missing")
    if not os.path.isfile(form_c) or _PROTO_MARKER not in _read(form_c):
        raise RuntimeError("form-change patch self-check: form_change.c missing")


# ── Layer C event-ability CALL sites (each gated on its ABILITY_* existing) ──
# The hook FUNCTIONS are generated into in_battle_forms.c; these insert the CALLS at the
# right engine points + the prototypes into pokemon.h. All idempotent and ability-gated,
# so a vanilla decomp (no such ability) gets NO change here.
_ABILITY_HOOK_PROTOS = {
    "ABILITY_STANCE_CHANGE": "bool8 TryStanceChangeForm(u8 battler, u16 move);",
    "ABILITY_HUNGER_SWITCH": "bool8 TryHungerSwitchForm(u8 battler);",
    "ABILITY_BATTLE_BOND":   "bool8 TryBattleBondForm(u8 battler);",
    "ABILITY_GULP_MISSILE":  "bool8 TryGulpMissileForm(u8 battler, u16 move);",
}

# Stance: insert AFTER the can't-move / PP / obedience gates so a paralyzed / asleep /
# out-of-PP / disobedient mon does NOT flip. The `gHitMarker |= HITMARKER_OBEYS;` that
# immediately precedes the Magic-Coat bounce check is that point, and (unlike the copy
# inside the obedience switch) it is uniquely followed by the bounceMove check.
_STANCE_ANCHOR = ("    gHitMarker |= HITMARKER_OBEYS;\n\n"
                  "    if (gProtectStructs[gBattlerTarget].bounceMove")
_STANCE_HOOKED = ("    gHitMarker |= HITMARKER_OBEYS;\n\n"
                  "    TryStanceChangeForm(gBattlerAttacker, gCurrentMove);   // ability port: Stance Change (pre-move)\n\n"
                  "    if (gProtectStructs[gBattlerTarget].bounceMove")

# Hunger Switch: end of turn, right after the reactive form check.
_HUNGER_ANCHOR = ("                TryInBattleFormChange(gActiveBattler);\n"
                  "                gBattleStruct->turnEffectsTracker++;")
_HUNGER_HOOKED = ("                TryInBattleFormChange(gActiveBattler);\n"
                  "                TryHungerSwitchForm(gActiveBattler);   // ability port: Hunger Switch (turn toggle)\n"
                  "                gBattleStruct->turnEffectsTracker++;")

# Gulp Missile + Battle Bond: at the top of the MOVEEND_FORM_CHANGE case body, before the
# reactive loop. Gulp self-gates on the move param; Battle Bond gets an inline KO condition.
_MOVEEND_ABILITY_ANCHOR = (
    "        case MOVEEND_FORM_CHANGE:  // PorySuite-Z Layer C: reactive in-battle forms\n"
    "            {\n"
    "                u32 i;\n")
_GULP_LINE = ("                TryGulpMissileForm(gBattlerAttacker, gCurrentMove);   // ability port: Gulp Missile (Surf/Dive)\n")
# Loops ALL battlers for an opposing mon this move just KO'd — not only gBattlerTarget —
# because MOVEEND_FORM_CHANGE runs ONCE with gBattlerTarget pinned to the last target, so a
# doubles spread move that KOs the first-processed foe but not the second would otherwise miss.
# TryBattleBondForm self-gates on the attacker's ability + hp, so the loop only needs the KO.
_BATTLEBOND_LINES = (
    "                for (i = 0; i < gBattlersCount; i++)\n"
    "                    if (gBattleMons[i].hp == 0\n"
    "                     && GetBattlerSide(i) != GetBattlerSide(gBattlerAttacker)\n"
    "                     && (gSpecialStatuses[i].physicalDmg != 0 || gSpecialStatuses[i].specialDmg != 0))\n"
    "                    { TryBattleBondForm(gBattlerAttacker); break; }   // ability port: Battle Bond (KO by this move; doubles-safe)\n")


def _patch_battle_ability_hooks(project_root):
    """Insert the CALL sites for the event-driven form abilities the project has, plus
    their prototypes. Each is gated on its ABILITY_* existing and is idempotent (skip if
    the call is already present). A vanilla decomp (none present) makes NO change."""
    have = _form_change_abilities(project_root)
    pokemon_h = os.path.join(project_root, "include", "pokemon.h")
    commands_c = os.path.join(project_root, "src", "battle_script_commands.c")
    endturn_c = os.path.join(project_root, "src", "battle_util.c")
    changed = False

    # prototypes (only for the event hooks the project has)
    if os.path.isfile(pokemon_h):
        text = _read(pokemon_h)
        if _PROTO_ANCHOR in text:
            for ab, proto in _ABILITY_HOOK_PROTOS.items():
                if ab in have and proto not in text:
                    text = text.replace(_PROTO_ANCHOR, _PROTO_ANCHOR + proto + "\n", 1)
            changed = _write_if_changed(pokemon_h, text) or changed

    # Stance Change — attackcanceler, past the can't-move gates (F2)
    if "ABILITY_STANCE_CHANGE" in have and os.path.isfile(commands_c):
        text = _read(commands_c)
        if "TryStanceChangeForm(" not in text and _STANCE_ANCHOR in text:
            changed = _write_if_changed(
                commands_c, text.replace(_STANCE_ANCHOR, _STANCE_HOOKED, 1)) or changed

    # Gulp Missile + Battle Bond — move-end form-change case
    if (("ABILITY_GULP_MISSILE" in have or "ABILITY_BATTLE_BOND" in have)
            and os.path.isfile(commands_c)):
        text = _read(commands_c)
        add = ""
        if "ABILITY_GULP_MISSILE" in have and "TryGulpMissileForm(" not in text:
            add += _GULP_LINE
        if "ABILITY_BATTLE_BOND" in have and "TryBattleBondForm(" not in text:
            add += _BATTLEBOND_LINES
        if add and _MOVEEND_ABILITY_ANCHOR in text:
            changed = _write_if_changed(commands_c, text.replace(
                _MOVEEND_ABILITY_ANCHOR, _MOVEEND_ABILITY_ANCHOR + add, 1)) or changed

    # Hunger Switch — end of turn
    if "ABILITY_HUNGER_SWITCH" in have and os.path.isfile(endturn_c):
        text = _read(endturn_c)
        if "TryHungerSwitchForm(" not in text and _HUNGER_ANCHOR in text:
            changed = _write_if_changed(
                endturn_c, text.replace(_HUNGER_ANCHOR, _HUNGER_HOOKED, 1)) or changed

    return changed


def apply_form_change_system(project_root):
    """Apply Layer B form-change infrastructure to *project_root*.

    Ensures Layer A is present first (Layer B's struct field sits after Layer A's
    formSpeciesIdTable). Idempotent: re-running on an already-patched project
    changes nothing and returns all-False. Raises on a structurally unexpected
    engine rather than emitting half-patched C.
    """
    apply_form_system(project_root)

    inc = os.path.join(project_root, "include")
    pokemon_h = os.path.join(inc, "pokemon.h")
    types_h = os.path.join(inc, "constants", "form_change_types.h")
    form_c = os.path.join(project_root, "src", "form_change.c")

    if not os.path.isfile(pokemon_h):
        raise FileNotFoundError(f"form-change patch: missing {pokemon_h}")

    result = {
        "pokemon_h": _patch_pokemon_h(pokemon_h),
        "form_change_types_h": _create_form_change_types_h(types_h),
        "form_change_c": _create_form_change_c(form_c, project_root),
        "party_menu_c": _patch_party_menu(
            os.path.join(project_root, "src", "party_menu.c")),
        "battle_main_c": _patch_battle_start(
            os.path.join(project_root, "src", "battle_main.c")),
        "field_weather_c": _patch_weather_main(
            os.path.join(project_root, "src", "field_weather.c")),
        "field_weather_util_c": _patch_weather_util(
            os.path.join(project_root, "src", "field_weather_util.c")),
        # Layer C — live in-battle reactive forms (status / HP threshold).
        "in_battle_forms_c": _create_in_battle_forms_c(
            os.path.join(project_root, "src", "in_battle_forms.c"), project_root),
        "battle_util_c": _patch_battle_endturn(
            os.path.join(project_root, "src", "battle_util.c")),
        "battle_script_commands_c": _patch_battle_moveend(
            os.path.join(project_root, "src", "battle_script_commands.c"),
            os.path.join(project_root, "include", "constants",
                         "battle_script_commands.h")),
        # Layer C — event-driven form abilities (Stance/Hunger/Battle Bond/Gulp), gated.
        "battle_ability_hooks": _patch_battle_ability_hooks(project_root),
    }
    _self_check(pokemon_h, types_h, form_c)
    result["_unhooked"] = _verify_hooks(project_root)   # visible half-wired warning
    return result
