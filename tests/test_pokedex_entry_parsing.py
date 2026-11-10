"""Pokedex entries must be read for EVERY species, however it is defined.

`parse_pokedex_entries` collects the set of real species constants so it can
ignore Pokedex entries that have no species behind them. It matched them with
``#define\\s+(SPECIES_[A-Z0-9_]+)\\s+(\\d+)`` -- decimal digits.

Vanilla writes ``#define SPECIES_BULBASAUR 1``, so vanilla matched. Every
species PorySuite itself appends is written as
``#define SPECIES_MASSIVEEYE (NUM_SPECIES + 30)``, an expression. Those names
never entered the set, so their whole Pokedex entry blocks were dropped: no
description pointer, no category, no height, weight, scale or offset. In the
owner's project that was 77 of 490 creatures, blank in the editor while the
engine had real values for them all along.

The captured value was never used. The regex asked about the VALUE to answer a
question about the NAME.

DO NOT regress: the species-constant scan must not constrain what a define
expands to. Under-reporting here is invisible -- a dropped entry looks like
absent data rather than a broken parser.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SPECIES_H = """\
#ifndef GUARD_CONSTANTS_SPECIES_H
#define GUARD_CONSTANTS_SPECIES_H

#define SPECIES_NONE 0
#define SPECIES_PLAIN 1
#define SPECIES_SPACED    2
#define NUM_SPECIES 3
#define SPECIES_APPENDED (NUM_SPECIES + 1)
#define SPECIES_APPENDED_TWO    (NUM_SPECIES + 2)
#define SPECIES_HEXY 0x04

#endif
"""

ENTRIES_H = """\
const struct PokedexEntry gPokedexEntries[] =
{
%s};
"""

BLOCK = """\
    [NATIONAL_DEX_%(n)s] =
    {
        .categoryName = _("CAT%(n)s"),
        .height = 7,
        .weight = 159,
        .description = g%(c)sPokedexText,
        .unusedDescription = gDummyPokedexText,
        .pokemonScale = 238,
        .pokemonOffset = 16,
        .trainerScale = 256,
        .trainerOffset = -2,
    },
"""

NAMES = ["PLAIN", "SPACED", "APPENDED", "APPENDED_TWO", "HEXY", "GHOST"]


def _camel(n):
    return "".join(p.capitalize() for p in n.split("_"))


TEXT_H = "".join(
    f'const u8 g{_camel(n)}PokedexText[] = _(\n    "Text for {n}.");\n\n'
    for n in NAMES
) + 'const u8 gDummyPokedexText[] = _(\n    "Placeholder.");\n'


@pytest.fixture
def project(tmp_path):
    (tmp_path / "include" / "constants").mkdir(parents=True)
    (tmp_path / "src" / "data" / "pokemon").mkdir(parents=True)
    (tmp_path / "include" / "constants" / "species.h").write_text(
        SPECIES_H, encoding="utf-8")
    blocks = "".join(BLOCK % {"n": n, "c": _camel(n)} for n in NAMES)
    (tmp_path / "src" / "data" / "pokemon" / "pokedex_entries.h").write_text(
        ENTRIES_H % blocks, encoding="utf-8")
    (tmp_path / "src" / "data" / "pokemon" / "pokedex_text_fr.h").write_text(
        TEXT_H, encoding="utf-8")
    return {"dir": str(tmp_path), "project_name": "fixture",
            "source_prefix": ""}


@pytest.fixture
def parsed(project):
    from core.local_env import LocalUtil
    from core.pokemon_data_extractor import parse_pokedex_entries
    return parse_pokedex_entries(LocalUtil(project))


def test_a_species_defined_as_an_expression_is_read(parsed):
    """The bug, in one assertion."""
    assert "SPECIES_APPENDED" in parsed


def test_every_field_of_such_an_entry_survives(parsed):
    entry = parsed["SPECIES_APPENDED"]
    assert entry["categoryName"] == "CATAPPENDED"
    assert entry["height"] == 7
    assert entry["weight"] == 159
    assert entry["description"] == "gAppendedPokedexText"
    assert entry["descriptionText"] == "Text for APPENDED."
    assert entry["pokemonScale"] == 238
    assert entry["trainerOffset"] == -2


def test_extra_whitespace_before_an_expression_is_fine(parsed):
    assert "SPECIES_APPENDED_TWO" in parsed


def test_a_hexadecimal_define_is_read(parsed):
    """It never matched `\\d+` either, and nothing says a project may not."""
    assert "SPECIES_HEXY" in parsed


def test_plain_decimal_species_still_work(parsed):
    assert "SPECIES_PLAIN" in parsed
    assert "SPECIES_SPACED" in parsed


def test_an_entry_with_no_species_is_still_ignored(parsed):
    """The filter must still filter. Widening it is not removing it."""
    assert "SPECIES_GHOST" not in parsed


def test_every_block_with_a_species_is_returned(parsed):
    assert set(parsed) == {
        "SPECIES_PLAIN", "SPECIES_SPACED", "SPECIES_APPENDED",
        "SPECIES_APPENDED_TWO", "SPECIES_HEXY",
    }


def test_the_scan_does_not_require_a_value_shape():
    """Guards the rule rather than one example of it."""
    import inspect
    import re
    from core import pokemon_data_extractor as ex
    src = inspect.getsource(ex.parse_pokedex_entries)
    scans = re.findall(r'r"#define[^"]*"', src)
    assert scans, "the species-constant scan moved; re-point this test"
    for pattern in scans:
        assert r"(\d+)" not in pattern, (
            f"the species scan constrains the define's value again: {pattern}"
        )
