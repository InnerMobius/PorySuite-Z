"""Regression guard: the female-art generator must write sprite-sheet RECORDS, not raw arrays.

THE BUG (v0.1.47b-era Flarewing female art): `set_female_art` wrote override rows like
    { SPECIES_FLAREWING, &gMonFrontPic_FlarewingF, &gMonBackPic_FlarewingF, ... }
but the engine's `struct GenderPicOverride` (include/pokemon.h) holds pointers to
`CompressedSpriteSheet` / `CompressedSpritePalette` records. A pointer to the raw INCBIN
array is the wrong type -- GCC 14+ rejects it (-Wincompatible-pointer-types), and if forced,
the game reads the graphics bytes as a record (`src->data`) and crashes on the first female
mon of that species.

THE RULE (core/female_art.py::update_override_text):
  * Each species gets four records, built exactly like the standard tables (src/data.c):
    sheets {data, 0x800, SPECIES_X}, palette {data, SPECIES_X},
    shiny palette {data, SPECIES_X + SPECIES_SHINY_TAG}.
  * The override row points at those records.
  * An existing row in the old raw-array form is REPLACED (re-running Set Female Art repairs it).

If you touch `update_override_text` or `set_female_art`, keep this green.
"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "core")):
    sys.path.insert(0, _p)

from core.female_art import update_override_text  # noqa: E402

EMPTY = """// header comment
const struct GenderPicOverride gFemalePicOverrides[] =
{
    { SPECIES_NONE }, // terminator
};
"""

# The exact broken form the old generator produced.
OLD_ROW_FILE = """// header comment
extern const u32 gMonFrontPic_FlarewingF[];
extern const u32 gMonBackPic_FlarewingF[];
extern const u32 gMonPalette_FlarewingF[];
extern const u32 gMonShinyPalette_FlarewingF[];

const struct GenderPicOverride gFemalePicOverrides[] =
{
    { SPECIES_FLAREWING, &gMonFrontPic_FlarewingF, &gMonBackPic_FlarewingF, &gMonPalette_FlarewingF, &gMonShinyPalette_FlarewingF },
    { SPECIES_NONE }, // terminator
};
"""

GOOD_ROW = ("    { SPECIES_FLAREWING, &sFemaleFrontPic_FlarewingF, &sFemaleBackPic_FlarewingF, "
            "&sFemalePalette_FlarewingF, &sFemaleShinyPalette_FlarewingF },\n")


def _check(t):
    assert "static const struct CompressedSpriteSheet sFemaleFrontPic_FlarewingF = " \
           "{ gMonFrontPic_FlarewingF, 0x800, SPECIES_FLAREWING };" in t
    assert "static const struct CompressedSpriteSheet sFemaleBackPic_FlarewingF = " \
           "{ gMonBackPic_FlarewingF, 0x800, SPECIES_FLAREWING };" in t
    assert "static const struct CompressedSpritePalette sFemalePalette_FlarewingF = " \
           "{ gMonPalette_FlarewingF, SPECIES_FLAREWING };" in t
    assert "static const struct CompressedSpritePalette sFemaleShinyPalette_FlarewingF = " \
           "{ gMonShinyPalette_FlarewingF, SPECIES_FLAREWING + SPECIES_SHINY_TAG };" in t
    assert GOOD_ROW in t
    assert "&gMonFrontPic_FlarewingF" not in t          # the row never points at a raw array
    assert t.count("{ SPECIES_FLAREWING,") == 1
    # records are defined before the table that takes their address
    assert t.index("sFemaleFrontPic_FlarewingF =") < t.index("gFemalePicOverrides[]")
    assert t.rstrip().endswith("};") and "{ SPECIES_NONE }" in t


def test_fresh_insert():
    _check(update_override_text(EMPTY, "SPECIES_FLAREWING", "FlarewingF"))


def test_repairs_old_raw_array_row():
    _check(update_override_text(OLD_ROW_FILE, "SPECIES_FLAREWING", "FlarewingF"))


def test_idempotent():
    once = update_override_text(OLD_ROW_FILE, "SPECIES_FLAREWING", "FlarewingF")
    assert update_override_text(once, "SPECIES_FLAREWING", "FlarewingF") == once


def test_other_species_untouched():
    t = update_override_text(EMPTY, "SPECIES_FLAREWING", "FlarewingF")
    t = update_override_text(t, "SPECIES_PIKACHU", "PikachuF")
    _check(t)
    assert "{ SPECIES_PIKACHU, &sFemaleFrontPic_PikachuF," in t
    assert t.index("{ SPECIES_PIKACHU,") < t.index("{ SPECIES_NONE }")
