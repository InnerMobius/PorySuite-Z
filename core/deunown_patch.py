"""De-Unown engine patcher (PorySuite-Z).

Removes the Unown letter-form mechanism from a pokefirered project's game
source, turning the mon that used it into a normal single-sprite mon. This is an
engine refactor performed entirely through the patcher (parse + edit), sibling to
the ``Normalize`` feature.

WHAT THE "UNOWN MECHANISM" IS
    28 letter/symbol forms live in appended species slots
    ``SPECIES_UNOWN_B..Z/_EMARK/_QMARK`` (``NUM_SPECIES + 1..27``). At runtime the
    letter is derived from personality via ``GET_UNOWN_LETTER`` and the engine
    special-cases the mon in sprite/coord/decompress/icon/mail/menu/wild-spawn
    paths. A hack can repurpose all of this for a custom mon.

WHAT THIS REMOVES (per DEUNOWN_COMPAT_REPORT.md Section 2)
    * the 27 appended ``SPECIES_UNOWN_*`` form defines (species.h) -- the freed
      ids become permanent, harmless holes; ``NUM_TOTAL_SPECIES`` is left alone
      and NO other appended species is renumbered.
    * the appended-slot rows in the pic-coordinate and icon tables.
    * the letter-branch special-cases (battle_anim_mons, battle_main, decompress,
      pokemon_icon, menu2) so the base mon falls through to the standard
      ``gMon*[species]`` path, with now-dead locals cleaned up.
    * the helper machinery: ``GET_UNOWN_LETTER`` / ``NUM_UNOWN_FORMS``,
      ``GetUnownLetterByPersonality``, ``GetUnownLetterByPersonalityLoByte``,
      ``GenerateUnownPersonalityByLetter``, ``sUnownLetterSlots``, the
      ``CreateMonWithGenderNatureLetter`` letter branch and the mail Unown
      encode/decode (repointed to pass-through).
    * the orphaned ``OLD_UNOWN_SPECIES_INFO`` macro -- ONLY when it has no users
      left (a vanilla tree keeps it, because its 25 rows are still live).

SAFETY
    * Gated on :func:`has_unown_mechanism`. A project without the mechanism is
      never written (returns a no-op summary).
    * Idempotent: after a successful run the gate reads False, so a second run is
      a clean no-op. Every individual removal also checks presence first.
    * Atomic: all edits are computed in memory and the resulting tree is scanned
      for any dangling mechanism reference BEFORE a single byte is written. On an
      anchor miss (a definition removed while a caller survives) it raises
      ``DeUnownError`` and writes nothing, rather than leaving a half-patched
      tree that would fail the build.

NOTE ON PROJECT VARIANTS
    Vanilla pokefirered branches its special-cases on ``SPECIES_UNOWN`` (the base
    #201); a repurposed hack may branch on its own mon (e.g. ``SPECIES_ZIRRO``).
    The structural code edits capture the branch species generically
    (``SPECIES_\\w+``) so they work either way; the appended slots keep the
    ``SPECIES_UNOWN_*`` names in both. Only the vanilla tree is build-verifiable
    here, so the literal-anchored menu/mail/wild edits are matched against the
    canonical shapes; a divergent shape is caught by the pre-write scan and
    reported rather than silently skipped.
"""

import os
import re
import logging

_log = logging.getLogger(__name__)


class DeUnownError(RuntimeError):
    """Raised on an anchor miss so the caller writes nothing (refuse-and-report)."""


# ── low-level io ─────────────────────────────────────────────────────────────────
def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _write_if_changed(path, text):
    """Write only when content differs. Returns True if the file was written."""
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            if f.read() == text:
                return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return True


# Relative paths (POSIX form; joined with os.path for the platform).
_SPECIES_H = ("include", "constants", "species.h")
_POKEMON_H = ("include", "pokemon.h")
_POKEMON_ICON_H = ("include", "pokemon_icon.h")
_WILD_ENC_H = ("include", "wild_encounter.h")
_BATTLE_ANIM = ("src", "battle_anim_mons.c")
_BATTLE_MAIN = ("src", "battle_main.c")
_DECOMPRESS = ("src", "decompress.c")
_POKEMON_ICON = ("src", "pokemon_icon.c")
_MENU2 = ("src", "menu2.c")
_WILD_ENC = ("src", "wild_encounter.c")
_POKEMON_C = ("src", "pokemon.c")
_MAIL_DATA = ("src", "mail_data.c")
_FRONT_COORDS = ("src", "data", "pokemon_graphics", "front_pic_coordinates.h")
_BACK_COORDS = ("src", "data", "pokemon_graphics", "back_pic_coordinates.h")
_FRONT_TABLE = ("src", "data", "pokemon_graphics", "front_pic_table.h")
_BACK_TABLE = ("src", "data", "pokemon_graphics", "back_pic_table.h")
_PAL_TABLE = ("src", "data", "pokemon_graphics", "palette_table.h")
_SHINY_TABLE = ("src", "data", "pokemon_graphics", "shiny_palette_table.h")
_SPECIES_INFO = ("src", "data", "pokemon", "species_info.h")


def _p(root, parts):
    return os.path.join(root, *parts)


# ── detection gate ───────────────────────────────────────────────────────────────
_RE_APPENDED_UNOWN_DEF = re.compile(
    r"#define\s+SPECIES_UNOWN_\w+\s+\(\s*NUM_SPECIES\s*\+\s*\d+\s*\)")


def has_unown_mechanism(project_root):
    """True iff the project still has the Unown letter-form mechanism: the
    ``GET_UNOWN_LETTER`` macro in ``include/pokemon.h`` AND at least one appended
    ``SPECIES_UNOWN_* (NUM_SPECIES + N)`` form define in
    ``include/constants/species.h``. A vanilla-with-Unown tree returns True; a
    project that never had it (or one already de-unowned) returns False, and is
    left completely untouched."""
    ph = _p(project_root, _POKEMON_H)
    sp = _p(project_root, _SPECIES_H)
    if not (os.path.isfile(ph) and os.path.isfile(sp)):
        return False
    return ("GET_UNOWN_LETTER" in _read(ph)
            and _RE_APPENDED_UNOWN_DEF.search(_read(sp)) is not None)


# ── shared regexes ───────────────────────────────────────────────────────────────
# A rendering special-case of the form
#     if (species == SPECIES_<X>)
#     {   ... <references SPECIES_UNOWN> ... }
#     else if (<cond>)
# The block body carries no nested braces in any of the target sites, so a
# negated-brace class matches it exactly up to its own closing brace. Requiring
# SPECIES_UNOWN inside pins it to the Unown branch (never the CASTFORM/NUM_SPECIES
# sibling branches). The branch species is captured generically so a repurposed
# mon works the same as vanilla's SPECIES_UNOWN.
_RE_UNOWN_BRANCH = re.compile(
    r"([ \t]*)if \(species == SPECIES_\w+\)\n"
    r"[ \t]*\{[^{}]*?SPECIES_UNOWN[^{}]*?\}\n"
    r"[ \t]*else (if \([^\n]*\))")

# A designated-initializer row keyed on an appended Unown slot, e.g.
#   [SPECIES_UNOWN_B]     = gMonIcon_UnownB,
# Matches the whole line. Anchored on "SPECIES_UNOWN_" so it never touches the
# base SPECIES_UNOWN or the numeric SPECIES_OLD_UNOWN_* species.
_RE_UNOWN_ROW = re.compile(r"[ \t]*\[SPECIES_UNOWN_[A-Z]+\][^\n]*\n")

# A multi-line pic-coordinate block keyed on an appended Unown slot:
#   [SPECIES_UNOWN_B] =
#   {
#       .size = ...,
#       .y_offset = ...,
#   },
_RE_UNOWN_COORD_BLOCK = re.compile(
    r"[ \t]*\[SPECIES_UNOWN_[A-Z]+\] =\n[ \t]*\{[^{}]*?\},\n")

# A pic/palette/shiny-palette table row written in MACRO form, e.g.
#   SPECIES_SPRITE(UNOWN_B, gMonFrontPic_UnownB),
#   SPECIES_PAL(UNOWN_QMARK, gMonPalette_Unown),
# The macro concatenates SPECIES_##arg, so the arg UNOWN_B expands to
# SPECIES_UNOWN_B. Anchored on "(UNOWN_" + a LETTER so it never matches the base
# row SPECIES_SPRITE(UNOWN, ...) or the numeric SPECIES_SPRITE(OLD_UNOWN_*, ...).
_RE_UNOWN_MACRO_ROW = re.compile(r"[ \t]*SPECIES_\w+\(UNOWN_[A-Z]+,[^\n]*\n")

# Tokens that MUST NOT survive anywhere in the tree once the mechanism is removed.
# (Base SPECIES_UNOWN and numeric SPECIES_OLD_UNOWN_* are deliberately absent --
# they remain valid ordinary species.)
_FORBIDDEN = [
    re.compile(r"\bGET_UNOWN_LETTER\b"),
    re.compile(r"\bNUM_UNOWN_FORMS\b"),
    re.compile(r"\bGetUnownLetterByPersonalityLoByte\b"),
    re.compile(r"\bGetUnownLetterByPersonality\b"),
    re.compile(r"\bGenerateUnownPersonalityByLetter\b"),
    re.compile(r"\bsUnownLetterSlots\b"),
    re.compile(r"\bUNOWN_OFFSET\b"),
    re.compile(r"SPECIES_UNOWN_[A-Z]"),
    re.compile(r"\(UNOWN_[A-Z]"),   # appended-slot rows in macro form
]


def _require(condition, message):
    if not condition:
        raise DeUnownError(message)


def _brace_balance(text):
    """`{` count minus `}` count. Compared before-vs-after on the SAME file to
    detect an edit that structurally unbalanced the braces (string/comment braces
    are identical in both and cancel out)."""
    return text.count("{") - text.count("}")


def _replace_once(text, old, new, where):
    """Exact single replacement. Idempotent:

    * removal (``new == ""``) whose target is already gone → no-op (unchanged);
    * substitution whose *new* form is already present → no-op (unchanged).

    Raises (anchor miss) only when *old* is absent and there is a non-empty
    *new* that also isn't present — i.e. the file is in neither the pre nor the
    post state."""
    if old in text:
        return text.replace(old, new, 1)
    if new == "":
        # Nothing to remove — already removed (or never existed). The tree-wide
        # forbidden-token scan is the real backstop for a genuine miss, so a
        # no-op here is safe and keeps a half-patched tree re-runnable.
        return text
    _require(new in text,
             "de-unown: anchor not found in {} (expected block missing and "
             "post-state absent)".format(where))
    return text


# ── per-file transforms (pure: text -> text) ─────────────────────────────────────
def _patch_species_h(text):
    """Remove the 27 appended SPECIES_UNOWN_* form defines. Leaves NUM_SPECIES,
    NUM_TOTAL_SPECIES and every numeric SPECIES_OLD_UNOWN_* untouched."""
    text = re.sub(r"^#define SPECIES_UNOWN_\w+ \(NUM_SPECIES \+ \d+\)\n",
                  "", text, flags=re.M)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def _patch_pokemon_h(text):
    """Remove NUM_UNOWN_FORMS and the GET_UNOWN_LETTER macro."""
    old = (
        "#define NUM_UNOWN_FORMS 28\n\n"
        "#define GET_UNOWN_LETTER(personality) ((   \\\n"
        "      (((personality) & 0x03000000) >> 18) \\\n"
        "    | (((personality) & 0x00030000) >> 12) \\\n"
        "    | (((personality) & 0x00000300) >> 6)  \\\n"
        "    | (((personality) & 0x00000003) >> 0)  \\\n"
        ") % NUM_UNOWN_FORMS)\n\n")
    return _replace_once(text, old, "", "include/pokemon.h GET_UNOWN_LETTER")


def _patch_battle_anim(text):
    """Delete the 4 Unown letter branches (GetBattlerYDelta x2,
    GetBattlerSpriteCoordAttr x2) and the locals they leave dead."""
    text = _RE_UNOWN_BRANCH.sub(r"\1\2", text)

    # GetBattlerYDelta: letter/personality/spriteInfo/coordSpecies were used only
    # in the removed branches -> collapse the declaration block to just `ret`.
    text = _replace_once(
        text,
        "    u16 letter;\n"
        "    u32 personality;\n"
        "    struct BattleSpriteInfo *spriteInfo;\n"
        "    u8 ret;\n"
        "    u16 coordSpecies;\n",
        "    u8 ret;\n",
        "battle_anim_mons.c GetBattlerYDelta locals")

    # GetBattlerSpriteCoordAttr: drop the now-dead letter/unownSpecies/personality
    # declarations (species/ret/coords/spriteInfo stay live).
    text = _replace_once(
        text,
        "    u16 species;\n"
        "    u32 personality;\n"
        "    u16 letter;\n"
        "    u16 unownSpecies;\n"
        "    s32 ret;\n"
        "    const struct MonCoords *coords;\n"
        "    struct BattleSpriteInfo *spriteInfo;\n",
        "    u16 species;\n"
        "    s32 ret;\n"
        "    const struct MonCoords *coords;\n"
        "    struct BattleSpriteInfo *spriteInfo;\n",
        "battle_anim_mons.c GetBattlerSpriteCoordAttr locals")

    # ...and the personality assignments that are now set-but-unused. These exact
    # lines survive only inside GetBattlerSpriteCoordAttr after the branch removal.
    for dead in (
        "            personality = GetMonData(&gPlayerParty"
        "[gBattlerPartyIndexes[battlerId]], MON_DATA_PERSONALITY);\n",
        "            personality = GetMonData(&gEnemyParty"
        "[gBattlerPartyIndexes[battlerId]], MON_DATA_PERSONALITY);\n",
    ):
        text = text.replace(dead, "")
    text = text.replace(
        "            personality = gTransformedPersonalities[battlerId];\n", "")
    return text


def _patch_battle_main(text):
    """Delete the Unown faint-offset branch in SpriteCB_FaintOpponentMon."""
    return _RE_UNOWN_BRANCH.sub(r"\1\2", text)


def _patch_decompress(text):
    """Delete the 2 Unown pic-decompress branches (LoadSpecialPokePic and
    LoadSpecialPokePic_DontHandleDeoxys)."""
    return _RE_UNOWN_BRANCH.sub(r"\1\2", text)


def _patch_coords(text):
    """Delete every appended-Unown pic-coordinate block (front or back)."""
    return _RE_UNOWN_COORD_BLOCK.sub("", text)


def _patch_graphics_macro_table(text):
    """Delete every appended-Unown row written in macro form from a
    front/back-pic, palette, or shiny-palette table. Leaves the base
    SPECIES_SPRITE(UNOWN, ...) row and the numeric SPECIES_*(OLD_UNOWN_*, ...)
    rows untouched (both remain valid ordinary species)."""
    return _RE_UNOWN_MACRO_ROW.sub("", text)


_RE_ICON_GETSPECIES = re.compile(
    r"    if \(species == SPECIES_\w+\)\n"
    r"    \{\n"
    r"        u16 letter = GetUnownLetterByPersonality\(personality\);\n"
    r"        if \(letter == 0\)\n"
    r"            letter = SPECIES_\w+;\n"
    r"        else\n"
    r"            letter \+= \(SPECIES_UNOWN_B - 1\);\n"
    r"        result = letter;\n"
    r"    \}\n"
    r"    else\n"
    r"    \{\n"
    r"        if \(species > NUM_SPECIES\)\n"
    r"            result = SPECIES_NONE;\n"
    r"        else\n"
    r"            result = species;\n"
    r"    \}")

_RE_ICON_GETUNOWNLETTER = re.compile(
    r"u16 GetUnownLetterByPersonality\(u32 personality\)\n\{[^{}]*\}\n\n")

_RE_MAIL_ICON = re.compile(
    r"u16 MailSpeciesToIconSpecies\(u16 species\)\n\{\n"
    r"    u16 value;\n\n"
    r"    if \(MailSpeciesToSpecies\(species, &value\) == SPECIES_\w+\)\n"
    r"    \{\n"
    r"        if \(value == 0\)\n"
    r"            value \+= SPECIES_\w+;\n"
    r"        else\n"
    r"            value \+= \(SPECIES_UNOWN_B - 1\);\n"
    r"        return value;\n"
    r"    \}\n"
    r"    else\n"
    r"    \{\n"
    r"        if \(species > \(SPECIES_UNOWN_B - 1\)\)\n"
    r"            species = SPECIES_NONE;\n"
    r"        return GetIconSpecies\(species, 0\);\n"
    r"    \}\n"
    r"\}")


def _patch_pokemon_icon(text, icon_bound):
    """Strip the appended-Unown icon + palette-index rows, delete the
    GetIconSpecies letter branch, delete GetUnownLetterByPersonality, and rewrite
    MailSpeciesToIconSpecies to pass-through with a correct out-of-range bound
    (*icon_bound* is NUM_TOTAL_SPECIES when the project defines it, else
    NUM_SPECIES -- the old ``SPECIES_UNOWN_B - 1`` bound equals NUM_SPECIES but
    would wrongly sanitise every appended custom species to SPECIES_NONE)."""
    text = _RE_UNOWN_ROW.sub("", text)

    _require(_RE_ICON_GETSPECIES.search(text) is not None
             or "u16 GetIconSpecies(u16 species, u32 personality)" in text,
             "pokemon_icon.c GetIconSpecies anchor not found")
    text = _RE_ICON_GETSPECIES.sub(
        "    if (species > NUM_SPECIES)\n"
        "        result = SPECIES_NONE;\n"
        "    else\n"
        "        result = species;", text)

    text = _RE_ICON_GETUNOWNLETTER.sub("", text)

    new_mail = (
        "u16 MailSpeciesToIconSpecies(u16 species)\n{\n"
        "    if (species > " + icon_bound + ")\n"
        "        species = SPECIES_NONE;\n"
        "    return GetIconSpecies(species, 0);\n}")
    if _RE_MAIL_ICON.search(text):
        text = _RE_MAIL_ICON.sub(lambda _m: new_mail, text)
    else:
        _require(new_mail in text,
                 "pokemon_icon.c MailSpeciesToIconSpecies anchor not found")
    return text


def _patch_menu2(text):
    """Remove the two local OLD_UNOWN pos defines + comment, their two
    sMonPosAttributes rows, and the Unown branch in Menu2_GetMonPosAttribute."""
    text = _replace_once(
        text,
        '// Not actually "old" given these were introduced in Gen 3,\n'
        "// but they're used as equivalents here alongside the other OLD_UNOWN\n"
        "#define SPECIES_OLD_UNOWN_EMARK (NUM_SPECIES + 0)\n"
        "#define SPECIES_OLD_UNOWN_QMARK (NUM_SPECIES + 1)\n\n",
        "",
        "menu2.c local OLD_UNOWN defines")

    text = _replace_once(
        text,
        "    [SPECIES_OLD_UNOWN_EMARK - 1] = {32, 33,  8, 32, 43},\n"
        "    [SPECIES_OLD_UNOWN_QMARK - 1] = {32, 35,  8, 32, 45}\n",
        "",
        "menu2.c sMonPosAttributes OLD_UNOWN rows")

    text = re.sub(
        r"    if \(species == SPECIES_\w+\)\n"
        r"    \{\n"
        r"        u8 unownLetter = GetUnownLetterByPersonalityLoByte\(personality\);\n"
        r"        switch \(unownLetter\)\n"
        r"        \{\n"
        r"        case 0:\n"
        r"            break;\n"
        r"        case 26:\n"
        r"            species = SPECIES_OLD_UNOWN_EMARK;\n"
        r"            break;\n"
        r"        case 27:\n"
        r"            species = SPECIES_OLD_UNOWN_QMARK;\n"
        r"            break;\n"
        r"        default:\n"
        r"            species = SPECIES_OLD_UNOWN_B \+ unownLetter - 1;\n"
        r"            break;\n"
        r"        \}\n"
        r"    \}\n",
        "", text)
    return text


def _patch_wild_encounter(text):
    """Remove the sUnownLetterSlots table, the GenerateWildMon Unown branch, and
    the GenerateUnownPersonalityByLetter / GetUnownLetterByPersonalityLoByte
    helpers (and the forward prototype)."""
    text = text.replace(
        "static u32 GenerateUnownPersonalityByLetter(u8 letter);\n", "")

    text = re.sub(
        r"static const u8 sUnownLetterSlots\[\]\[LAND_WILD_COUNT\] = \{[^;]*\};\n\n",
        "", text)

    text = _replace_once(
        text,
        "    u32 personality;\n"
        "    s8 chamber;\n"
        "    ZeroEnemyPartyMons();\n"
        "    if (species != SPECIES_UNOWN)\n"
        "    {\n"
        "        CreateMonWithNature(&gEnemyParty[0], species, level, "
        "USE_RANDOM_IVS, Random() % NUM_NATURES);\n"
        "    }\n"
        "    else\n"
        "    {\n"
        "        chamber = gSaveBlock1Ptr->location.mapNum - "
        "MAP_NUM(MAP_SEVEN_ISLAND_TANOBY_RUINS_MONEAN_CHAMBER);\n"
        "        personality = GenerateUnownPersonalityByLetter("
        "sUnownLetterSlots[chamber][slot]);\n"
        "        CreateMon(&gEnemyParty[0], species, level, USE_RANDOM_IVS, "
        "TRUE, personality, FALSE, 0);\n"
        "    }\n",
        # `slot` was only read by the removed Unown branch; keep the parameter
        # (callers pass it) but mark it unused, matching the (void)unownLetter
        # collapse in pokemon.c so no dead/unused-parameter smell is left behind.
        "    (void)slot;\n"
        "    ZeroEnemyPartyMons();\n"
        "    CreateMonWithNature(&gEnemyParty[0], species, level, USE_RANDOM_IVS, "
        "Random() % NUM_NATURES);\n",
        "wild_encounter.c GenerateWildMon")

    text = _replace_once(
        text,
        "static u32 GenerateUnownPersonalityByLetter(u8 letter)\n"
        "{\n"
        "    u32 personality;\n"
        "    do\n"
        "    {\n"
        "        personality = (Random() << 16) | Random();\n"
        "    } while (GetUnownLetterByPersonalityLoByte(personality) != letter);\n"
        "    return personality;\n"
        "}\n\n"
        "u8 GetUnownLetterByPersonalityLoByte(u32 personality)\n"
        "{\n"
        "    return GET_UNOWN_LETTER(personality);\n"
        "}\n\n",
        "",
        "wild_encounter.c Unown helpers")
    return text


def _patch_pokemon_c(text):
    """Collapse CreateMonWithGenderNatureLetter to its non-letter path; keep the
    unownLetter parameter (sole caller passes 0) with a (void) cast."""
    return _replace_once(
        text,
        "    if ((u8)(unownLetter - 1) < NUM_UNOWN_FORMS)\n"
        "    {\n"
        "        u16 actualLetter;\n\n"
        "        do\n"
        "        {\n"
        "            personality = Random32();\n"
        "            actualLetter = GET_UNOWN_LETTER(personality);\n"
        "        }\n"
        "        while (nature != GetNatureFromPersonality(personality)\n"
        "            || gender != GetGenderFromSpeciesAndPersonality(species, personality)\n"
        "            || actualLetter != unownLetter - 1);\n"
        "    }\n"
        "    else\n"
        "    {\n"
        "        do\n"
        "        {\n"
        "            personality = Random32();\n"
        "        }\n"
        "        while (nature != GetNatureFromPersonality(personality)\n"
        "            || gender != GetGenderFromSpeciesAndPersonality(species, personality));\n"
        "    }\n",
        "    (void)unownLetter;\n\n"
        "    do\n"
        "    {\n"
        "        personality = Random32();\n"
        "    }\n"
        "    while (nature != GetNatureFromPersonality(personality)\n"
        "        || gender != GetGenderFromSpeciesAndPersonality(species, personality));\n",
        "pokemon.c CreateMonWithGenderNatureLetter")


def _patch_mail_data(text):
    """Drop UNOWN_OFFSET and repoint the mail Unown encode/decode to pass-through
    (MailSpeciesToSpecies also zeroes the out-param the way the old code did on
    the non-Unown path)."""
    text = _replace_once(text, "#define UNOWN_OFFSET 30000\n\n", "",
                         "mail_data.c UNOWN_OFFSET")

    text = _replace_once(
        text,
        "u16 SpeciesToMailSpecies(u16 species, u32 personality)\n"
        "{\n"
        "    if (species == SPECIES_UNOWN) {\n"
        "        u32 mailSpecies = GetUnownLetterByPersonality(personality) + UNOWN_OFFSET;\n"
        "        return mailSpecies;\n"
        "    }\n"
        "    return species;\n"
        "}\n",
        "u16 SpeciesToMailSpecies(u16 species, u32 personality)\n"
        "{\n"
        "    (void)personality;\n"
        "    return species;\n"
        "}\n",
        "mail_data.c SpeciesToMailSpecies")

    text = _replace_once(
        text,
        "u16 MailSpeciesToSpecies(u16 mailSpecies, u16 *unownLetter)\n"
        "{\n"
        "    u16 result;\n\n"
        "    if (mailSpecies >= UNOWN_OFFSET && mailSpecies < (UNOWN_OFFSET + NUM_UNOWN_FORMS))\n"
        "    {\n"
        "        result = SPECIES_UNOWN;\n"
        "        *unownLetter = mailSpecies - UNOWN_OFFSET;\n"
        "    }\n"
        "    else\n"
        "    {\n"
        "        result = mailSpecies;\n"
        "    }\n"
        "    return result;\n"
        "}\n",
        "u16 MailSpeciesToSpecies(u16 mailSpecies, u16 *unownLetter)\n"
        "{\n"
        "    *unownLetter = 0;\n"
        "    return mailSpecies;\n"
        "}\n",
        "mail_data.c MailSpeciesToSpecies")
    return text


_RE_OLD_UNOWN_MACRO = re.compile(
    r"#define OLD_UNOWN_SPECIES_INFO(?:[^\n]*\\\n)*[^\n]*\n\n?")


def _patch_species_info(text):
    """Remove the OLD_UNOWN_SPECIES_INFO macro ONLY when it is orphaned (no
    ``= OLD_UNOWN_SPECIES_INFO`` users remain). A vanilla tree still has 25 live
    users, so this is a no-op there; a project whose OLD_UNOWN rows were long ago
    de-shared into real stat blocks gets the dead macro cleaned up."""
    if "OLD_UNOWN_SPECIES_INFO" not in text:
        return text
    users = len(re.findall(r"=\s*OLD_UNOWN_SPECIES_INFO", text))
    if users > 0:
        return text  # still live -- leave it
    return _RE_OLD_UNOWN_MACRO.sub("", text, count=1)


def _patch_header_line(text, line):
    """Remove a single exported prototype line (idempotent)."""
    return text.replace(line, "")


# ── orchestration ────────────────────────────────────────────────────────────────
def _icon_bound(project_root):
    """NUM_TOTAL_SPECIES if the project defines it, else NUM_SPECIES."""
    sp = _p(project_root, _SPECIES_H)
    try:
        if "NUM_TOTAL_SPECIES" in _read(sp):
            return "NUM_TOTAL_SPECIES"
    except OSError:
        pass
    return "NUM_SPECIES"


def _scan_tree_for_forbidden(project_root, pending):
    """Scan include/ and src/ for any surviving mechanism token, reading the
    pending (in-memory) version of edited files and the on-disk version of the
    rest. Returns a list of ``(relpath, token)`` offenders."""
    offenders = []
    roots = [os.path.join(project_root, "include"),
             os.path.join(project_root, "src")]
    text_ext = (".c", ".h", ".inc")
    for base in roots:
        for dirpath, _dirs, files in os.walk(base):
            for name in files:
                if not name.endswith(text_ext):
                    continue
                full = os.path.join(dirpath, name)
                if full in pending:
                    content = pending[full]
                else:
                    try:
                        content = _read(full)
                    except (OSError, UnicodeDecodeError):
                        continue
                for rx in _FORBIDDEN:
                    if rx.search(content):
                        offenders.append((os.path.relpath(full, project_root),
                                          rx.pattern))
    return offenders


def apply_deunown(project_root):
    """Remove the Unown letter-form mechanism from *project_root*.

    Returns a summary dict. No-op (``{"supported": False, ...}``) when the
    project has no mechanism (the gate) -- so a vanilla-without-Unown tree and an
    already de-unowned tree are both left untouched. All edits are computed in
    memory and the tree is scanned for dangling references before anything is
    written; an anchor miss raises :class:`DeUnownError` without writing."""
    if not has_unown_mechanism(project_root):
        _log.info("de-unown: project has no Unown mechanism -- skipping.")
        return {"supported": False, "changed": [], "actions": []}

    icon_bound = _icon_bound(project_root)

    # (relative-parts, transform) -- transform is text->text.
    plan = [
        (_SPECIES_H, _patch_species_h),
        (_POKEMON_H, _patch_pokemon_h),
        (_BATTLE_ANIM, _patch_battle_anim),
        (_BATTLE_MAIN, _patch_battle_main),
        (_DECOMPRESS, _patch_decompress),
        (_FRONT_COORDS, _patch_coords),
        (_BACK_COORDS, _patch_coords),
        (_FRONT_TABLE, _patch_graphics_macro_table),
        (_BACK_TABLE, _patch_graphics_macro_table),
        (_PAL_TABLE, _patch_graphics_macro_table),
        (_SHINY_TABLE, _patch_graphics_macro_table),
        (_POKEMON_ICON, lambda t: _patch_pokemon_icon(t, icon_bound)),
        (_MENU2, _patch_menu2),
        (_WILD_ENC, _patch_wild_encounter),
        (_POKEMON_C, _patch_pokemon_c),
        (_MAIL_DATA, _patch_mail_data),
        (_SPECIES_INFO, _patch_species_info),
        (_POKEMON_ICON_H, lambda t: _patch_header_line(
            t, "u16 GetUnownLetterByPersonality(u32 personality);\n")),
        (_WILD_ENC_H, lambda t: _patch_header_line(
            t, "u8 GetUnownLetterByPersonalityLoByte(u32 personality);\n")),
    ]

    pending = {}      # abs path -> new text (only files whose content changed)
    originals = {}    # abs path -> pre-edit text (for the brace-balance guard)
    actions = []      # human-readable log
    for parts, transform in plan:
        full = _p(project_root, parts)
        if not os.path.isfile(full):
            # species_info.h etc. are always present in a real tree; a genuinely
            # missing engine file is an anchor miss.
            raise DeUnownError("de-unown: expected file missing: {}".format(
                os.path.join(*parts)))
        original = _read(full)
        updated = transform(original)
        if updated != original:
            pending[full] = updated
            originals[full] = original
            actions.append(os.path.join(*parts))

    # Refuse-and-report: never write a tree that still references the mechanism.
    offenders = _scan_tree_for_forbidden(project_root, pending)
    if offenders:
        raise DeUnownError(
            "de-unown: dangling mechanism references remain after edits "
            "(refusing to write): "
            + "; ".join("{} ~ {}".format(f, tok) for f, tok in offenders[:20]))

    # Structural guard (defence-in-depth): every removal takes out BALANCED
    # code, so an edit must never change a file's brace balance. If one did, a
    # branch-promotion left the C structurally broken (e.g. a stray `else` /
    # unbalanced `{}` in an unusually-nested special-case the token scan can't
    # see, since the Unown words are already gone) -- refuse before writing
    # rather than emit source that only fails at build time. String/comment
    # braces cancel out because we compare the SAME file before vs after.
    for full, new_text in pending.items():
        if _brace_balance(new_text) != _brace_balance(originals[full]):
            raise DeUnownError(
                "de-unown: editing {} unbalanced its braces -- a special-case "
                "removal left the C structurally broken (refusing to write; the "
                "project's file layout may be non-standard).".format(
                    os.path.relpath(full, project_root)))

    changed = []
    for full, text in pending.items():
        if _write_if_changed(full, text):
            changed.append(os.path.relpath(full, project_root))

    _log.info("de-unown: patched %d file(s).", len(changed))
    return {"supported": True, "changed": sorted(changed), "actions": actions,
            "icon_bound": icon_bound}
