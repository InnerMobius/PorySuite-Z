"""The Pokedex cache must be a cache of the engine, not a snapshot that can
never be corrected.

``instantiate_extractor`` calls ``extract_data`` only when the cache is missing
or STALE. ``PokedexDataExtractor.extract_data`` nevertheless opened
``pokedex.json`` and, if it existed, returned it unchanged -- handing straight
back the data that had just been judged untrustworthy. The engine read sat
below that early return and never ran.

The visible consequence: a cache written by a parser that could not see species
defined as expressions (``#define SPECIES_X (NUM_SPECIES + 30)``) kept 77
creatures blank in the editor -- no category, height, weight, scale or
description -- while ``pokedex_entries.h`` held real values for all of them.
Fixing the parser changed nothing on screen, because nothing called it. Nor
could reopening help: the cache was NEWER than the source it had misread, so
every timestamp check said "fresh".

Two rules come out of that, and both are guarded here:

* ``extract_data`` reads the engine. The cache is a fallback for when the
  engine read produces nothing, never the answer.
* Staleness cannot be decided by timestamps alone. ``CACHE_FORMAT`` is stamped
  on write so a cache from a parser we no longer trust is rebuilt once, and
  ``WATCHED_FILES`` covers the source files the inherited check never looked
  at.

DO NOT regress: do not reintroduce an early return of the cached file, and do
not drop the format stamp. Both failures are invisible -- the editor shows
missing data as though the project simply had none.
"""
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SPECIES_H = """\
#ifndef GUARD_CONSTANTS_SPECIES_H
#define GUARD_CONSTANTS_SPECIES_H

#define SPECIES_NONE 0
#define SPECIES_VANILLA 1
#define NUM_SPECIES 2
#define SPECIES_APPENDED (NUM_SPECIES + 1)

#endif
"""

POKEDEX_H = """\
#ifndef GUARD_CONSTANTS_POKEDEX_H
#define GUARD_CONSTANTS_POKEDEX_H

enum {
    NATIONAL_DEX_NONE,
    NATIONAL_DEX_VANILLA,
    NATIONAL_DEX_APPENDED,
};

#define NATIONAL_DEX_COUNT NATIONAL_DEX_APPENDED

// Hoenn Pokedex order
enum {
    HOENN_DEX_NONE,
    HOENN_DEX_VANILLA,
    // End of Hoenn Dex (see HOENN_DEX_COUNT)
    HOENN_DEX_APPENDED,
};

#define HOENN_DEX_COUNT HOENN_DEX_VANILLA

#endif
"""

ENTRY = """\
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

ENTRIES_H = ("const struct PokedexEntry gPokedexEntries[] =\n{\n"
             + ENTRY % {"n": "VANILLA", "c": "Vanilla"}
             + ENTRY % {"n": "APPENDED", "c": "Appended"}
             + "};\n")

TEXT_H = (
    'const u8 gVanillaPokedexText[] = _(\n    "Vanilla words.");\n\n'
    'const u8 gAppendedPokedexText[] = _(\n    "Appended words.");\n\n'
    'const u8 gDummyPokedexText[] = _(\n    "Placeholder.");\n'
)

# What the OLD parser wrote: the appended species was invisible to it, so its
# entry carries nothing but an identity. No cache_format, no hoenn_order.
STALE_CACHE = {
    "national_dex": [
        {
            "dex_num": 1,
            "species": "SPECIES_VANILLA",
            "dex_constant": "NATIONAL_DEX_VANILLA",
            "categoryName": "CATVANILLA",
            "height": 7,
            "weight": 159,
            "description": "gVanillaPokedexText",
            "descriptionText": "Vanilla words.",
            "unusedDescription": "gDummyPokedexText",
            "unusedDescriptionText": "Placeholder.",
            "pokemonScale": 238,
            "pokemonOffset": 16,
            "trainerScale": 256,
            "trainerOffset": -2,
        },
        {
            "dex_num": 2,
            "species": "SPECIES_APPENDED",
            "dex_constant": "NATIONAL_DEX_APPENDED",
        },
    ],
    "regional_dex": [],
}


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


@pytest.fixture
def project(tmp_path):
    root = str(tmp_path)
    _write(os.path.join(root, "include", "constants", "species.h"), SPECIES_H)
    _write(os.path.join(root, "include", "constants", "pokedex.h"), POKEDEX_H)
    _write(os.path.join(root, "src", "data", "pokemon", "pokedex_entries.h"),
           ENTRIES_H)
    _write(os.path.join(root, "src", "data", "pokemon", "pokedex_text_fr.h"),
           TEXT_H)
    _write(os.path.join(root, "src", "data", "pokemon", "pokedex_text_lg.h"),
           TEXT_H)
    return {"dir": root, "project_name": "fixture", "source_prefix": ""}


def _cache_path(project):
    return os.path.join(project["dir"], "src", "data", "pokedex.json")


def _put_cache(project, data):
    """Write a cache and make it the NEWEST file, as a real stale cache is."""
    path = _cache_path(project)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    newest = max(
        os.path.getmtime(os.path.join(dirpath, name))
        for dirpath, _, names in os.walk(project["dir"])
        for name in names
    )
    os.utime(path, (newest + 10, newest + 10))
    return path


@pytest.fixture
def extractor(project):
    from core.pokemon_data_extractor import PokedexDataExtractor
    return PokedexDataExtractor(dict(project), "pokedex.json")


def _by_species(data):
    return {e["species"]: e for e in data["national_dex"]}


# ── the bug itself ───────────────────────────────────────────────────────────

def test_a_cache_missing_detail_is_refreshed_from_the_engine(extractor,
                                                             project):
    """The whole defect, in one assertion.

    The cache on disk says the appended species has no category. The engine
    says it has one. Opening the project must believe the engine.
    """
    _put_cache(project, STALE_CACHE)
    data = extractor.extract_data()
    appended = _by_species(data)["SPECIES_APPENDED"]
    assert appended.get("categoryName") == "CATAPPENDED"


def test_every_engine_field_arrives_not_just_the_category(extractor, project):
    _put_cache(project, STALE_CACHE)
    appended = _by_species(extractor.extract_data())["SPECIES_APPENDED"]
    assert appended["height"] == 7
    assert appended["weight"] == 159
    assert appended["description"] == "gAppendedPokedexText"
    assert appended["descriptionText"] == "Appended words."
    assert appended["pokemonScale"] == 238
    assert appended["trainerOffset"] == -2


def test_the_engine_overrules_a_cache_that_disagrees(extractor, project):
    """Not merely "fills the gaps" -- the engine is authoritative.

    A cache that holds a value the engine contradicts is wrong, however
    complete it looks.
    """
    stale = json.loads(json.dumps(STALE_CACHE))
    stale["national_dex"][0]["categoryName"] = "WRONG"
    stale["national_dex"][0]["weight"] = 1
    _put_cache(project, stale)
    vanilla = _by_species(extractor.extract_data())["SPECIES_VANILLA"]
    assert vanilla["categoryName"] == "CATVANILLA"
    assert vanilla["weight"] == 159


def test_entries_the_engine_does_know_are_unharmed(extractor, project):
    _put_cache(project, STALE_CACHE)
    vanilla = _by_species(extractor.extract_data())["SPECIES_VANILLA"]
    for key, value in STALE_CACHE["national_dex"][0].items():
        assert vanilla[key] == value, key


# ── staleness must be detectable at all ──────────────────────────────────────

def test_a_cache_with_no_format_stamp_is_stale(extractor, project):
    """A cache can be NEWER than the source it misread, so timestamps alone
    can never catch this. The stamp is what makes the repair reachable."""
    _put_cache(project, STALE_CACHE)
    assert extractor.should_extract() is True


def test_a_current_cache_is_left_alone(extractor, project):
    """The repair must not turn into a rebuild on every single open."""
    fresh = extractor.extract_data()
    _put_cache(project, fresh)
    assert extractor.should_extract() is False


def test_the_stamp_is_written(extractor, project):
    _put_cache(project, STALE_CACHE)
    from core.pokemon_data_extractor import PokedexDataExtractor
    data = extractor.extract_data()
    assert data["cache_format"] == PokedexDataExtractor.CACHE_FORMAT


def test_an_older_stamp_is_stale(extractor, project):
    fresh = extractor.extract_data()
    fresh["cache_format"] = 1
    _put_cache(project, fresh)
    assert extractor.should_extract() is True


@pytest.mark.parametrize("rel", [
    os.path.join("src", "data", "pokemon", "pokedex_entries.h"),
    os.path.join("src", "data", "pokemon", "pokedex_text_fr.h"),
    os.path.join("src", "data", "pokemon", "pokedex_text_lg.h"),
    os.path.join("include", "constants", "species.h"),
])
def test_touching_a_source_file_makes_the_cache_stale(extractor, project, rel):
    """The inherited check compares only files registered for backup, which is
    pokedex.h alone. These four are where the data actually lives."""
    fresh = extractor.extract_data()
    path = _put_cache(project, fresh)
    assert extractor.should_extract() is False

    later = os.path.getmtime(path) + 10
    os.utime(os.path.join(project["dir"], rel), (later, later))
    assert extractor.should_extract() is True, (
        f"an edit to {rel} did not invalidate the cache"
    )


# ── the fallback the cache is still loaded for ───────────────────────────────

def test_an_unreadable_engine_keeps_the_existing_dex(extractor, project):
    """If the engine read yields nothing, hand back what was on disk rather
    than replacing a real Pokedex with an empty one."""
    _put_cache(project, STALE_CACHE)
    _write(os.path.join(project["dir"], "include", "constants", "pokedex.h"),
           "#ifndef GUARD\n#define GUARD\n#endif\n")
    data = extractor.extract_data()
    assert len(data["national_dex"]) == 2
    assert _by_species(data)["SPECIES_VANILLA"]["categoryName"] == "CATVANILLA"


def test_with_no_cache_and_no_engine_nothing_is_invented(extractor, project):
    _write(os.path.join(project["dir"], "include", "constants", "pokedex.h"),
           "#ifndef GUARD\n#define GUARD\n#endif\n")
    data = extractor.extract_data()
    assert data["national_dex"] == []


# ── the rule, not just one example of it ─────────────────────────────────────

def test_extract_data_does_not_return_the_cache_it_was_called_to_replace():
    """extract_data runs only when the cache is missing or stale. Returning
    that cache defeats every staleness check above it."""
    import inspect
    from core.pokemon_data_extractor import PokedexDataExtractor
    src = inspect.getsource(PokedexDataExtractor.extract_data)
    head = src.split("_read_header")[0]
    assert "return data" not in head, (
        "extract_data returns the cached file before reading the engine again"
    )
