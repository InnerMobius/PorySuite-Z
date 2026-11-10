"""The regional (Hoenn) Pokedex: real engine model, and a no-op that writes nothing.

BACKGROUND
----------
PorySuite's "Regional Dex" list was ``national_dex[:KANTO_DEX_COUNT]`` -- a
slice of the NATIONAL order under a regional label. Measured against a real
project it matched the engine's regional dex in neither length (151 vs 202),
order, nor membership: only 47 of the 151 names appeared in the engine's dex at
all. Wiring a serializer to that list would have overwritten a hand-curated
202-entry dex with the wrong creatures in the wrong order on the first Save.

The engine's regional dex is the ``HOENN_DEX_*`` enum, with membership being
the PREFIX of it up to ``HOENN_DEX_COUNT``. So the model is one ordering plus
one boundary.

DO NOT REGRESS
--------------
* A no-op save must leave ``pokedex.h`` BYTE-IDENTICAL. A writer that
  rearranges hundreds of entries merely because Save was pressed is worse than
  no writer.
* ``src/pokemon.c`` must NOT be rewritten for an ordering or membership edit.
  Both macro tables derive index AND value from the name, so the preprocessor
  re-indexes them from the enum. Regenerating them risks an off-by-one that
  compiles cleanly while misidentifying Pokemon -- which no build can catch.
* A failed parse must RAISE, never fall back to the national slice. That
  fallback could be written into engine source.
* Removing a Pokemon from the dex must not delete it from the ordering.
* ``HOENN_DEX_NONE`` is a structural anchor, never an entry.
* The boundary comments are pinned to the boundary, not carried along with
  whichever constant happens to follow them.
* The writer must never read ``regional_dex``: that key is a derived view, and
  letting it become the serialization source is the original defect.
"""
import ast
import inspect
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import hoenn_dex as H  # noqa: E402


# ---------------------------------------------------------------------------
# A fixture faithful to the engine's real shape
# ---------------------------------------------------------------------------
# Mirrors include/constants/pokedex.h: the National enum FIRST (so "the first
# enum in the file" is the wrong block), then the Hoenn section behind its own
# comment, then the count define further down. Four of the six Hoenn entries
# are in the dex, two are outside it.
INCLUDED = ["ALPHA", "BETA", "GAMMA", "DELTA"]
EXCLUDED = ["EPSILON", "ZETA"]
ALL = INCLUDED + EXCLUDED

HEADER = (
    "#ifndef GUARD_CONSTANTS_POKEDEX_H\n"
    "#define GUARD_CONSTANTS_POKEDEX_H\n"
    "\n"
    "// National Pokedex order\n"
    "enum {\n"
    "    NATIONAL_DEX_NONE,\n"
    + "".join(f"    NATIONAL_DEX_{n},\n" for n in ALL)
    + "};\n"
    "\n"
    "// Hoenn Pokedex order\n"
    "enum {\n"
    "    HOENN_DEX_NONE,\n"
    + "".join(f"    HOENN_DEX_{n},\n" for n in INCLUDED)
    + "    // End of Hoenn Dex (see HOENN_DEX_COUNT)\n"
    "    // Here below have values but are excluded from the Pokedex\n"
    + "".join(f"    HOENN_DEX_{n},\n" for n in EXCLUDED)
    + "};\n"
    "\n"
    "#define KANTO_DEX_COUNT NATIONAL_DEX_BETA\n"
    f"#define HOENN_DEX_COUNT HOENN_DEX_{INCLUDED[-1]}\n"
    "\n"
    "#endif\n"
)

POKEMON_C = (
    "#define SPECIES_TO_HOENN(name)      [SPECIES_##name - 1] = HOENN_DEX_##name\n"
    "#define HOENN_TO_NATIONAL(name)     [HOENN_DEX_##name - 1] = NATIONAL_DEX_##name\n"
    "\n"
    "static const u16 sSpeciesToHoennPokedexNum[NUM_TOTAL_SPECIES - 1] =\n{\n"
    + "".join(f"    SPECIES_TO_HOENN({n}),\n" for n in ALL)
    + "};\n"
    "\n"
    "static const u16 sHoennToNationalOrder[NUM_SPECIES - 1] =\n{\n"
    + "".join(f"    HOENN_TO_NATIONAL({n}),\n" for n in ALL)
    + "};\n"
)


@pytest.fixture
def dex():
    return H.parse(HEADER, POKEMON_C)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def test_the_hoenn_block_is_found_not_the_national_one(dex):
    """The National enum comes first in the real file."""
    assert dex.order == [f"HOENN_DEX_{n}" for n in ALL]


def test_membership_is_the_prefix_up_to_the_boundary(dex):
    assert dex.regional_count == len(INCLUDED)
    assert dex.included == [f"HOENN_DEX_{n}" for n in INCLUDED]


def test_entries_outside_the_boundary_are_kept(dex):
    """209 real entries live out here. They must not vanish."""
    assert dex.excluded == [f"HOENN_DEX_{n}" for n in EXCLUDED]


def test_none_is_not_an_entry(dex):
    assert "HOENN_DEX_NONE" not in dex.order


def test_the_count_names_the_last_included_entry(dex):
    """Not the entry after it, and not a row count."""
    assert dex.count_symbol == f"HOENN_DEX_{INCLUDED[-1]}"
    assert dex.included[-1] == dex.count_symbol


def test_count_value_is_the_number_included(dex):
    """HOENN_DEX_NONE occupies 0, so the enum value IS the count."""
    assert dex.regional_count == dex.order.index(dex.count_symbol) + 1


def test_national_identity_comes_from_the_engine_table(dex):
    for n in ALL:
        assert dex.national_of(f"HOENN_DEX_{n}") == f"NATIONAL_DEX_{n}"


def test_boundary_comments_are_captured_not_dropped(dex):
    assert len(dex.boundary_comments) == 2


# ---------------------------------------------------------------------------
# The gate: a no-op save writes nothing
# ---------------------------------------------------------------------------

def test_noop_roundtrip_is_byte_identical(dex):
    assert H.serialize(HEADER, dex) == HEADER


def test_noop_roundtrip_survives_a_numeric_count():
    """Some projects write the boundary as a literal."""
    text = HEADER.replace(
        f"#define HOENN_DEX_COUNT HOENN_DEX_{INCLUDED[-1]}",
        f"#define HOENN_DEX_COUNT {len(INCLUDED)}",
    )
    d = H.parse(text, POKEMON_C)
    assert d.regional_count == len(INCLUDED)
    # The define is normalised to name the entry, which is what the engine's
    # own convention is -- so this one is NOT byte-identical, and that is the
    # intended behaviour rather than an accident.
    out = H.serialize(text, d)
    assert f"#define HOENN_DEX_COUNT HOENN_DEX_{INCLUDED[-1]}" in out
    assert H.parse(out, POKEMON_C).regional_count == len(INCLUDED)


def test_the_serializer_is_not_an_identity_function(dex):
    """A writer that always returned its input would pass the gate above."""
    H.include(dex, f"HOENN_DEX_{EXCLUDED[0]}")
    assert H.serialize(HEADER, dex) != HEADER


def test_a_regional_edit_leaves_the_national_enum_alone(dex):
    H.include(dex, f"HOENN_DEX_{EXCLUDED[0]}")
    out = H.serialize(HEADER, dex)
    before = out.split(H.HOENN_ANCHOR)[0]
    assert before == HEADER.split(H.HOENN_ANCHOR)[0]


def test_boundary_comments_stay_at_the_boundary(dex):
    """They describe the boundary, so they must not travel with a creature."""
    H.include(dex, f"HOENN_DEX_{EXCLUDED[0]}")
    out = H.serialize(HEADER, dex)
    lines = [ln.strip() for ln in out.splitlines()]
    marker = lines.index("// End of Hoenn Dex (see HOENN_DEX_COUNT)")
    # the line above the marker is the newly-last included entry
    assert lines[marker - 1] == f"HOENN_DEX_{EXCLUDED[0]},"


# ---------------------------------------------------------------------------
# Editing, against the one model
# ---------------------------------------------------------------------------

def test_include_moves_rather_than_copies(dex):
    H.include(dex, f"HOENN_DEX_{EXCLUDED[0]}")
    assert len(dex.order) == len(ALL)
    assert dex.order.count(f"HOENN_DEX_{EXCLUDED[0]}") == 1


def test_include_lands_at_the_end_of_the_dex(dex):
    H.include(dex, f"HOENN_DEX_{EXCLUDED[1]}")
    assert dex.included[-1] == f"HOENN_DEX_{EXCLUDED[1]}"
    assert dex.regional_count == len(INCLUDED) + 1


def test_exclude_keeps_it_in_the_ordering(dex):
    """Removing from the dex is not deleting the Pokemon."""
    H.exclude(dex, f"HOENN_DEX_{INCLUDED[0]}")
    assert f"HOENN_DEX_{INCLUDED[0]}" in dex.order
    assert len(dex.order) == len(ALL)
    assert dex.regional_count == len(INCLUDED) - 1


def test_exclude_lands_at_the_start_of_the_excluded_section(dex):
    H.exclude(dex, f"HOENN_DEX_{INCLUDED[0]}")
    assert dex.excluded[0] == f"HOENN_DEX_{INCLUDED[0]}"


def test_exclude_then_include_restores_the_file(dex):
    H.exclude(dex, f"HOENN_DEX_{INCLUDED[-1]}")
    H.include(dex, f"HOENN_DEX_{INCLUDED[-1]}")
    assert H.serialize(HEADER, dex) == HEADER


def test_untouched_entries_keep_their_relative_order(dex):
    before = [c for c in dex.order if c != f"HOENN_DEX_{INCLUDED[1]}"]
    H.exclude(dex, f"HOENN_DEX_{INCLUDED[1]}")
    after = [c for c in dex.order if c != f"HOENN_DEX_{INCLUDED[1]}"]
    assert before == after


def test_reorder_does_not_change_membership(dex):
    H.move(dex, f"HOENN_DEX_{INCLUDED[0]}", 2)
    assert dex.regional_count == len(INCLUDED)
    assert dex.order.index(f"HOENN_DEX_{INCLUDED[0]}") == 2


def test_reorder_cannot_push_an_entry_across_the_boundary(dex):
    """A reorder is not a membership change, so it is clamped to its section."""
    H.move(dex, f"HOENN_DEX_{INCLUDED[0]}", 999)
    assert dex.order.index(f"HOENN_DEX_{INCLUDED[0]}") == dex.regional_count - 1
    assert set(dex.included) == {f"HOENN_DEX_{n}" for n in INCLUDED}


def test_reorder_within_the_excluded_section_stays_there(dex):
    H.move(dex, f"HOENN_DEX_{EXCLUDED[1]}", 0)
    assert dex.regional_count == len(INCLUDED)
    assert f"HOENN_DEX_{EXCLUDED[1]}" in dex.excluded


def test_including_something_already_in_the_dex_is_harmless(dex):
    H.include(dex, f"HOENN_DEX_{INCLUDED[0]}")
    assert dex.regional_count == len(INCLUDED)
    assert H.serialize(HEADER, dex) == HEADER


def test_excluding_something_already_out_is_harmless(dex):
    H.exclude(dex, f"HOENN_DEX_{EXCLUDED[0]}")
    assert dex.regional_count == len(INCLUDED)
    assert H.serialize(HEADER, dex) == HEADER


def test_editing_an_unknown_entry_is_refused(dex):
    with pytest.raises(H.HoennDexError):
        H.include(dex, "HOENN_DEX_NOT_A_THING")
    with pytest.raises(H.HoennDexError):
        H.exclude(dex, "HOENN_DEX_NOT_A_THING")


# ---------------------------------------------------------------------------
# Refusals: a bad parse must raise, never fall back
# ---------------------------------------------------------------------------

def _assert_reassures(exc):
    """Every refusal must tell the user their engine files are intact.

    Checked as a clause rather than a fixed sentence: it reads "No engine file
    was modified." standing alone, and "...so no engine file was modified."
    when it closes a longer sentence. The contract is that the user is told,
    not which capital letter it starts with.
    """
    assert "engine file was modified" in str(exc).lower(), str(exc)


def test_a_missing_hoenn_section_raises():
    text = HEADER.replace("// Hoenn Pokedex order\n", "")
    with pytest.raises(H.HoennDexError) as e:
        H.parse(text, POKEMON_C)
    _assert_reassures(e.value)


def test_a_missing_count_define_raises():
    text = HEADER.replace(
        f"#define HOENN_DEX_COUNT HOENN_DEX_{INCLUDED[-1]}\n", ""
    )
    with pytest.raises(H.HoennDexError):
        H.parse(text, POKEMON_C)


def test_a_count_naming_an_unknown_entry_raises():
    text = HEADER.replace(
        f"#define HOENN_DEX_COUNT HOENN_DEX_{INCLUDED[-1]}",
        "#define HOENN_DEX_COUNT HOENN_DEX_WHO",
    )
    with pytest.raises(H.HoennDexError):
        H.parse(text, POKEMON_C)


def test_a_duplicate_enum_entry_raises():
    text = HEADER.replace(
        f"    HOENN_DEX_{INCLUDED[0]},\n",
        f"    HOENN_DEX_{INCLUDED[0]},\n    HOENN_DEX_{INCLUDED[0]},\n",
    )
    with pytest.raises(H.HoennDexError) as e:
        H.parse(text, POKEMON_C)
    assert INCLUDED[0] in str(e.value)


def test_an_entry_with_no_national_identity_raises():
    """Refuse the write; do not drop the entry or invent an identifier."""
    c = POKEMON_C.replace(f"    HOENN_TO_NATIONAL({ALL[0]}),\n", "")
    with pytest.raises(H.HoennDexError) as e:
        H.parse(HEADER, c)
    assert ALL[0] in str(e.value)


def test_validate_refuses_a_boundary_past_the_end(dex):
    dex.regional_count = len(dex.order) + 1
    with pytest.raises(H.HoennDexError):
        H.serialize(HEADER, dex)


def test_validate_refuses_a_zero_boundary(dex):
    dex.regional_count = 0
    with pytest.raises(H.HoennDexError):
        H.serialize(HEADER, dex)


def test_validate_refuses_none_in_the_ordering(dex):
    dex.order.insert(0, "HOENN_DEX_NONE")
    with pytest.raises(H.HoennDexError):
        H.serialize(HEADER, dex)


def test_validate_refuses_duplicates(dex):
    dex.order.append(dex.order[0])
    with pytest.raises(H.HoennDexError):
        H.serialize(HEADER, dex)


def test_validate_refuses_a_missing_national_identity(dex):
    dex.national[dex.order[0]] = None
    with pytest.raises(H.HoennDexError) as e:
        H.serialize(HEADER, dex)
    assert "National Dex" in str(e.value)


def _break_duplicate(d):
    d.order.append(d.order[0])


def _break_none(d):
    d.order.insert(0, "HOENN_DEX_NONE")


def _break_boundary_high(d):
    d.regional_count = len(d.order) + 1


def _break_boundary_zero(d):
    d.regional_count = 0


def _break_identity(d):
    d.national[d.order[0]] = None


def _break_empty(d):
    d.order.clear()


@pytest.mark.parametrize("break_it", [
    _break_duplicate, _break_none, _break_boundary_high,
    _break_boundary_zero, _break_identity, _break_empty,
])
def test_every_save_refusal_says_nothing_was_written(dex, break_it):
    """Each message has to tell the user their files are intact.

    Parametrised over every way a save can be refused, not just one: a
    reassurance that only some paths carry is a reassurance the user cannot
    rely on. The in-memory edit guards in include/exclude/move are excluded
    deliberately -- they fire before any save is attempted, so mentioning
    engine files there would be noise.
    """
    break_it(dex)
    with pytest.raises(H.HoennDexError) as e:
        H.serialize(HEADER, dex)
    _assert_reassures(e.value)


@pytest.mark.parametrize("mangle", [
    lambda t: t.replace("// Hoenn Pokedex order\n", ""),
    lambda t: t.replace(f"#define HOENN_DEX_COUNT HOENN_DEX_{INCLUDED[-1]}\n", ""),
    lambda t: t.replace(f"#define HOENN_DEX_COUNT HOENN_DEX_{INCLUDED[-1]}",
                        "#define HOENN_DEX_COUNT HOENN_DEX_WHO"),
    lambda t: t.replace(f"    HOENN_DEX_{INCLUDED[0]},\n",
                        f"    HOENN_DEX_{INCLUDED[0]},\n"
                        f"    HOENN_DEX_{INCLUDED[0]},\n"),
    lambda t: t.replace("// Hoenn Pokedex order\nenum {\n",
                        "// Hoenn Pokedex order\nenum {\n    1 + 2,\n"),
])
def test_every_parse_refusal_says_nothing_was_written(mangle):
    with pytest.raises(H.HoennDexError) as e:
        H.parse(mangle(HEADER), POKEMON_C)
    _assert_reassures(e.value)


# ---------------------------------------------------------------------------
# The derived view must never become the serialization source
# ---------------------------------------------------------------------------

def _pokedex_writer_source():
    import core.pokemon_data as pd
    return inspect.getsource(pd.Pokedex)


def test_the_writer_never_reads_regional_dex():
    """`regional_dex` is a derived view. Serializing from it is the defect.

    Asked of the parser rather than a substring scan: the name appears in
    prose in this file and in comments in that one, and a comment is not a
    read.
    """
    tree = ast.parse(_pokedex_writer_source())
    strings = {
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }
    assert "regional_dex" not in strings


def test_the_writer_reads_the_authoritative_pair():
    tree = ast.parse(_pokedex_writer_source())
    strings = {
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }
    assert {"hoenn_order", "regional_count"} <= strings


def test_no_model_means_no_write():
    """An old cache or a failed extraction must leave the region untouched."""
    import core.pokemon_data as pd

    class _Stub:
        data = {"hoenn_order": [], "hoenn_dex_error": "could not read"}
        _apply_hoenn_dex = pd.Pokedex._apply_hoenn_dex

    assert _Stub()._apply_hoenn_dex(HEADER) == HEADER


def test_a_missing_boundary_means_no_write():
    import core.pokemon_data as pd

    class _Stub:
        data = {
            "hoenn_order": [
                {"hoenn_constant": f"HOENN_DEX_{n}",
                 "dex_constant": f"NATIONAL_DEX_{n}"} for n in ALL
            ],
            "regional_count": None,
        }
        _apply_hoenn_dex = pd.Pokedex._apply_hoenn_dex

    assert _Stub()._apply_hoenn_dex(HEADER) == HEADER


def test_the_writer_round_trips_a_cached_model_byte_identically(dex):
    """Through the cache shape the extractor actually stores.

    The layout -- indentation and boundary comments -- is read back from the
    file being patched, so it does not matter that the cache omits them.
    """
    import core.pokemon_data as pd

    cached = dex.to_cache()

    class _Stub:
        data = cached
        _apply_hoenn_dex = pd.Pokedex._apply_hoenn_dex

    assert _Stub()._apply_hoenn_dex(HEADER) == HEADER


# ---------------------------------------------------------------------------
# The old derived list is recognised as derived
# ---------------------------------------------------------------------------

def test_the_old_national_slice_is_recognised():
    from core.pokemon_data_extractor import PokedexDataExtractor as P
    nat = [{"dex_constant": f"NATIONAL_DEX_{n}"} for n in ALL]
    assert P._is_national_slice(nat[:2], nat) is True


def test_an_edited_regional_list_is_not_mistaken_for_derived():
    """It has to be reported, not silently discarded."""
    from core.pokemon_data_extractor import PokedexDataExtractor as P
    nat = [{"dex_constant": f"NATIONAL_DEX_{n}"} for n in ALL]
    edited = [{"dex_constant": f"NATIONAL_DEX_{ALL[3]}"}] + nat[:2]
    assert P._is_national_slice(edited, nat) is False


# ---------------------------------------------------------------------------
# Against the real engine, when one is present
# ---------------------------------------------------------------------------

REAL = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "pokefirered",
)


@pytest.mark.skipif(
    not os.path.isfile(os.path.join(REAL, "include", "constants", "pokedex.h")),
    reason="no game project checked out next to PorySuite",
)
def test_real_engine_noop_is_byte_identical():
    """The property that matters, measured on real engine source.

    Read-only: it parses and serializes in memory and asserts the text is
    unchanged. Nothing is written to the game project.
    """
    header_path = os.path.join(REAL, "include", "constants", "pokedex.h")
    c_path = os.path.join(REAL, "src", "pokemon.c")
    with open(header_path, encoding="utf-8", newline="") as f:
        header = f.read()
    c_text = ""
    if os.path.isfile(c_path):
        with open(c_path, encoding="utf-8", newline="") as f:
            c_text = f.read()

    real = H.parse(header, c_text)
    assert real.order, "parsed no Hoenn entries from the real project"
    assert 0 < real.regional_count <= len(real.order)
    assert H.serialize(header, real) == header


@pytest.mark.skipif(
    not os.path.isfile(os.path.join(REAL, "src", "pokemon.c")),
    reason="no game project checked out next to PorySuite",
)
def test_real_engine_tables_carry_no_positional_information():
    """Why pokemon.c is not rewritten: every row expands from its name.

    If a project ever hand-wrote a literal index into either table, this fails
    and the no-rewrite decision has to be revisited.
    """
    import re
    with open(os.path.join(REAL, "src", "pokemon.c"), encoding="utf-8") as f:
        c_text = f.read()

    for arr, macro in (
        ("sSpeciesToHoennPokedexNum", "SPECIES_TO_HOENN"),
        ("sHoennToNationalOrder", "HOENN_TO_NATIONAL"),
    ):
        i = c_text.find(arr)
        if i < 0:
            continue
        o = c_text.index("{", i)
        depth = 0
        for j in range(o, len(c_text)):
            if c_text[j] == "{":
                depth += 1
            elif c_text[j] == "}":
                depth -= 1
                if depth == 0:
                    body = c_text[o + 1:j]
                    break
        rows = [ln.split("//")[0].strip() for ln in body.splitlines()]
        rows = [r for r in rows if r]
        assert rows, f"{arr} parsed as empty"
        for r in rows:
            assert re.fullmatch(macro + r"\(\w+\),?", r), (
                f"{arr} has a row that is not a plain macro call: {r!r} -- "
                "the no-rewrite decision for pokemon.c needs revisiting"
            )
