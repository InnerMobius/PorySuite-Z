"""Independent Pokedex descriptions: planning, allocation and writing.

A description is a POINTER into a separate text file. Most creatures own their
block; many share the engine's one ``gDummyPokedexText`` placeholder. Giving a
sharer its own words therefore means allocating it a symbol, writing a new
block, and repointing its entry -- all of which must happen together or not at
all, because a pointer with no block does not compile and a block nothing
points at is dead weight.

The behaviours here are the ones that cost real data when they were wrong:

* The shared placeholder is never rewritten. Writing one creature's words into
  it changed what 102 of them displayed.
* The pointer is read from the ENGINE, never the cache. The cache dropped the
  pointer for 77 creatures (their species constant is an expression, and the
  entry parser matched only decimal literals), and allocating a second symbol
  for a creature that already had one orphans its first block.
* An edit goes only to the description file the editor read from. The other
  file holds the same creatures' text for the other build of the game, and
  256 of them differ; copying one over the other destroys data nobody touched.
* A NEW block goes to every description file, because the pointer is shared by
  both builds and a symbol defined in only one makes the other fail to link.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import pokedex_descriptions as D  # noqa: E402

DUMMY_TEXT = "A placeholder.\nNothing to see."

FR = (
    'const u8 gAlphaPokedexText[] = _(\n'
    '    "Alpha one.\\n"\n'
    '    "Alpha two.");\n'
    '\n'
    'const u8 gBetaPokedexText[] = _(\n'
    '    "Beta FireRed.");\n'
    '\n'
    'const u8 gDummyPokedexText[] = _(\n'
    '    "A placeholder.\\n"\n'
    '    "Nothing to see.");\n'
)

LG = (
    'const u8 gAlphaPokedexText[] = _(\n'
    '    "Alpha LeafGreen.");\n'
    '\n'
    'const u8 gBetaPokedexText[] = _(\n'
    '    "Beta LeafGreen.");\n'
    '\n'
    'const u8 gDummyPokedexText[] = _(\n'
    '    "A placeholder.\\n"\n'
    '    "Nothing to see.");\n'
)

ROWS = [
    ("NATIONAL_DEX_ALPHA", "gAlphaPokedexText"),
    ("NATIONAL_DEX_BETA", "gBetaPokedexText"),
    ("NATIONAL_DEX_GAMMA", "gDummyPokedexText"),
    ("NATIONAL_DEX_DELTA", "gDummyPokedexText"),
]

ENTRIES = "const struct PokedexEntry gPokedexEntries[] =\n{\n" + "".join(
    f"    [{const}] =\n"
    "    {\n"
    '        .categoryName = _("THING"),\n'
    "        .height = 10,\n"
    f"        .description = {sym},\n"
    f"        .unusedDescription = {sym},\n"
    "        .pokemonScale = 256,\n"
    "    },\n"
    for const, sym in ROWS
) + "};\n"

ON_DISK = {
    "NATIONAL_DEX_ALPHA": "Alpha one.\nAlpha two.",
    "NATIONAL_DEX_BETA": "Beta FireRed.",
    "NATIONAL_DEX_GAMMA": DUMMY_TEXT,
    "NATIONAL_DEX_DELTA": DUMMY_TEXT,
}


def natdex(**edits):
    """The cached rows, with `edits` overriding descriptionText by constant."""
    out = []
    for const, sym in ROWS:
        row = {"dex_constant": const, "description": sym,
               "descriptionText": ON_DISK[const]}
        if const in edits:
            row["descriptionText"] = edits[const]
        out.append(row)
    return out


def files():
    return {"fr": FR, "lg": LG}


def run(plan, entries=ENTRIES):
    ent, texts = D.apply(plan, entries, files(), "fr")
    D.verify(plan, ent, texts, "fr")
    return ent, texts


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def test_blocks_are_read_as_the_player_would_see_them():
    assert D.parse_blocks(FR)["gAlphaPokedexText"] == "Alpha one.\nAlpha two."


def test_the_pointer_comes_from_the_engine_file():
    assert D.description_pointers(ENTRIES) == dict(ROWS)


def test_every_defined_name_counts_as_taken():
    taken = D.taken_symbols(ENTRIES, FR, LG)
    assert {"gAlphaPokedexText", "gBetaPokedexText", "gDummyPokedexText"} <= taken


# ---------------------------------------------------------------------------
# GPT's behaviour table
# ---------------------------------------------------------------------------

def _owned_project():
    """The same project with NOBODY on the shared placeholder.

    Needed since 2026-11-08: in the ROWS fixture GAMMA and DELTA share the
    placeholder, and sharing is now itself work to do. "Nothing to do" means a
    project where every creature already owns its block.
    """
    entries = ENTRIES
    text = FR
    rows = []
    for const, sym in ROWS:
        if sym == D.DUMMY:
            own = f"g{const.split('_')[-1].title()}OwnText"
            entries = entries.replace(
                f"    [{const}] =\n    {{\n"
                '        .categoryName = _("THING"),\n'
                "        .height = 10,\n"
                f"        .description = {sym},",
                f"    [{const}] =\n    {{\n"
                '        .categoryName = _("THING"),\n'
                "        .height = 10,\n"
                f"        .description = {own},")
            text += f'\nconst u8 {own}[] = _(\n    "Owned words.");\n'
            rows.append({"dex_constant": const, "description": own,
                         "descriptionText": "Owned words."})
        else:
            rows.append({"dex_constant": const, "description": sym,
                         "descriptionText": ON_DISK[const]})
    return rows, entries, text


def test_no_op_plans_nothing():
    """Nothing shared and nothing edited means nothing written."""
    rows, entries, text = _owned_project()
    plan = D.plan_descriptions(rows, entries, text, LG)
    assert plan.is_empty and plan.conflicts == [], (
        f"allocations={plan.allocations} rewrites={plan.rewrites}")


def test_a_project_with_sharers_is_not_a_no_op():
    """THE NEW RULE. Two creatures on one text block is work to do, not a
    state to preserve -- distinct creatures do not share a Pokedex entry."""
    plan = D.plan_descriptions(natdex(), ENTRIES, FR, LG)
    assert not plan.is_empty
    assert set(plan.allocations) == {"NATIONAL_DEX_GAMMA", "NATIONAL_DEX_DELTA"}
    assert plan.conflicts == []


def test_un_duplicating_an_unedited_sharer_keeps_its_words():
    """The screen must not change -- only where the words are stored."""
    plan = D.plan_descriptions(natdex(), ENTRIES, FR, LG)
    for const in ("NATIONAL_DEX_GAMMA", "NATIONAL_DEX_DELTA"):
        sym = plan.allocations[const]
        assert plan.new_blocks[sym] == DUMMY_TEXT, (
            "un-duplication must copy the placeholder's words, not blank it")
    ent, texts = run(plan)
    blocks = D.parse_blocks(texts["fr"])
    ptr = D.description_pointers(ent)
    assert blocks[ptr["NATIONAL_DEX_GAMMA"]] == DUMMY_TEXT
    assert blocks[ptr["NATIONAL_DEX_DELTA"]] == DUMMY_TEXT


def test_un_duplicating_gives_every_sharer_a_different_symbol():
    """The whole point. Two creatures must not come out of this still sharing."""
    plan = D.plan_descriptions(natdex(), ENTRIES, FR, LG)
    ent, _texts = run(plan)
    ptr = D.description_pointers(ent)
    assert ptr["NATIONAL_DEX_GAMMA"] != ptr["NATIONAL_DEX_DELTA"]
    assert D.DUMMY not in (ptr["NATIONAL_DEX_GAMMA"], ptr["NATIONAL_DEX_DELTA"])


def test_un_duplication_never_rewrites_the_placeholder_block():
    """It is shared engine data and the reason any of this exists."""
    plan = D.plan_descriptions(natdex(), ENTRIES, FR, LG)
    assert D.DUMMY not in plan.rewrites
    assert D.DUMMY not in plan.new_blocks
    _ent, texts = run(plan)
    assert D.parse_blocks(texts["fr"])[D.DUMMY] == DUMMY_TEXT
    assert D.parse_blocks(texts["lg"])[D.DUMMY] == DUMMY_TEXT


def test_un_duplication_happens_once():
    """Saving twice must not churn the files or allocate second symbols."""
    plan = D.plan_descriptions(natdex(), ENTRIES, FR, LG)
    ent, texts = run(plan)
    ptr = D.description_pointers(ent)
    blocks = D.parse_blocks(texts["fr"])
    reopened = [{"dex_constant": c, "description": ptr[c],
                 "descriptionText": blocks[ptr[c]]} for c, _s in ROWS]
    again = D.plan_descriptions(reopened, ent, texts["fr"], texts["lg"])
    assert again.is_empty, (
        f"a second save would churn: allocations={again.allocations} "
        f"rewrites={again.rewrites}")


def _with_null_entry():
    """The project plus vanilla's own ``[NATIONAL_DEX_NONE]`` placeholder row.

    Vanilla pokefirered points exactly ONE entry at gDummyPokedexText and it is
    this one -- ``.categoryName = _("UNKNOWN")``, dex number 0, not a creature.
    Measured on a vanilla tree: 387 entries, 1 placeholder pointer, that one.
    """
    entries = ENTRIES.replace("};\n", (
        "    [NATIONAL_DEX_NONE] =\n"
        "    {\n"
        '        .categoryName = _("UNKNOWN"),\n'
        "        .height = 0,\n"
        "        .description = gDummyPokedexText,\n"
        "        .unusedDescription = gDummyPokedexText,\n"
        "        .pokemonScale = 256,\n"
        "    },\n"
        "};\n"))
    rows = natdex() + [{"dex_constant": "NATIONAL_DEX_NONE", "dex_num": 0,
                        "description": D.DUMMY, "descriptionText": DUMMY_TEXT}]
    return rows, entries


def test_the_null_entry_is_never_un_duplicated():
    """THE SCOPE GUARD. Un-duplication must be a no-op on a project that has
    added nothing, and vanilla's only sharer is the null entry. Writing a
    block for it would change a stranger's untouched project."""
    rows, entries = _with_null_entry()
    plan = D.plan_descriptions(rows, entries, FR, LG)
    assert "NATIONAL_DEX_NONE" not in plan.allocations
    ent, _texts = run(plan, entries)
    assert D.description_pointers(ent)["NATIONAL_DEX_NONE"] == D.DUMMY


def test_a_vanilla_shaped_project_is_left_completely_alone():
    """The null entry on the placeholder and nothing else shared: no work."""
    rows, entries = _with_null_entry()
    owned = [r for r in rows if r["description"] != D.DUMMY]
    owned.append(next(r for r in rows
                      if r["dex_constant"] == "NATIONAL_DEX_NONE"))
    plan = D.plan_descriptions(owned, entries, FR, LG)
    assert plan.is_empty, (
        f"touched a project that added nothing: {plan.allocations}")
    assert plan.conflicts == []


def test_the_null_entry_is_recognised_by_its_dex_number_too():
    """Not only by name -- a project that renamed the constant is still safe."""
    rows, entries = _with_null_entry()
    entries = entries.replace("NATIONAL_DEX_NONE", "NATIONAL_DEX_ZERO")
    for r in rows:
        if r["dex_constant"] == "NATIONAL_DEX_NONE":
            r["dex_constant"] = "NATIONAL_DEX_ZERO"
    plan = D.plan_descriptions(rows, entries, FR, LG)
    assert "NATIONAL_DEX_ZERO" not in plan.allocations, (
        "dex number 0 is the null entry whatever the constant is called")


def test_the_null_entry_is_recognised_by_name_without_a_dex_number():
    """The other half of the guard, and the half a mutation slipped through on
    2026-11-08: every fixture here happened to carry ``dex_num: 0``, so the
    name check was never the thing being tested. A cache row that omits the
    dex number must still be recognised."""
    rows, entries = _with_null_entry()
    for r in rows:
        if r["dex_constant"] == "NATIONAL_DEX_NONE":
            r.pop("dex_num", None)
    plan = D.plan_descriptions(rows, entries, FR, LG)
    assert "NATIONAL_DEX_NONE" not in plan.allocations, (
        "the null entry must be recognised by name when it has no dex number")


def test_a_sharer_with_no_cached_text_is_seeded_from_the_placeholder():
    """The empty-text branch, which a mutation also slipped through.

    A cache row can carry no description text at all. Un-duplicating it still
    has to put the placeholder's WORDS in the new block -- anything else
    invents content the user never wrote, or blanks a creature's entry.
    """
    rows = natdex()
    for r in rows:
        if r["dex_constant"] == "NATIONAL_DEX_GAMMA":
            r["descriptionText"] = ""
    plan = D.plan_descriptions(rows, ENTRIES, FR, LG)
    sym = plan.allocations["NATIONAL_DEX_GAMMA"]
    assert plan.new_blocks[sym] == DUMMY_TEXT, (
        f"seeded with {plan.new_blocks[sym]!r} instead of the placeholder")
    _ent, texts = run(plan)
    assert D.parse_blocks(texts["fr"])[sym] == DUMMY_TEXT


def test_a_sharer_with_no_text_and_no_placeholder_is_left_alone():
    """Nothing to copy means nothing is invented. The creature keeps sharing
    and no empty block is written."""
    fr_no_dummy = (
        'const u8 gAlphaPokedexText[] = _(\n'
        '    "Alpha one.\\n"\n'
        '    "Alpha two.");\n'
        '\n'
        'const u8 gBetaPokedexText[] = _(\n'
        '    "Beta FireRed.");\n'
    )
    rows = natdex()
    for r in rows:
        if r["dex_constant"] in ("NATIONAL_DEX_GAMMA", "NATIONAL_DEX_DELTA"):
            r["descriptionText"] = ""
    plan = D.plan_descriptions(rows, ENTRIES, fr_no_dummy, fr_no_dummy)
    assert plan.allocations == {}, plan.allocations
    assert plan.new_blocks == {}


def test_a_creature_that_owns_its_block_is_not_un_duplicated():
    """Only sharing is being removed. An owner keeps the symbol it has."""
    plan = D.plan_descriptions(natdex(), ENTRIES, FR, LG)
    assert "NATIONAL_DEX_ALPHA" not in plan.allocations
    assert "NATIONAL_DEX_BETA" not in plan.allocations
    assert plan.rewrites == {}, "nobody's existing text was edited"


def test_edit_a_creature_that_owns_its_symbol():
    plan = D.plan_descriptions(
        natdex(NATIONAL_DEX_ALPHA="New alpha."), ENTRIES, FR, LG)
    assert plan.rewrites == {"gAlphaPokedexText": "New alpha."}
    assert "NATIONAL_DEX_ALPHA" not in plan.allocations, (
        "an owner must not be allocated a second symbol")
    _ent, texts = run(plan)
    assert D.parse_blocks(texts["fr"])["gAlphaPokedexText"] == "New alpha."
    # and its pointer is untouched
    assert D.description_pointers(_ent)["NATIONAL_DEX_ALPHA"] == "gAlphaPokedexText"


def test_edit_a_creature_sharing_the_placeholder():
    plan = D.plan_descriptions(
        natdex(NATIONAL_DEX_GAMMA="Gamma's own."), ENTRIES, FR, LG)
    assert plan.allocations["NATIONAL_DEX_GAMMA"] == "gGammaPokedexText"
    ent, texts = run(plan)
    assert D.description_pointers(ent)["NATIONAL_DEX_GAMMA"] == "gGammaPokedexText"
    assert D.parse_blocks(texts["fr"])["gGammaPokedexText"] == "Gamma's own."


def test_the_other_sharer_keeps_its_words_and_its_own_block():
    """Editing GAMMA must not change what DELTA displays.

    Until 2026-11-08 this asserted DELTA was still pointing at the shared
    placeholder. It is not any more -- it gets a block of its own carrying the
    same words. The guarantee that matters is unchanged and is what is
    asserted: DELTA's TEXT is untouched, and the placeholder itself is not
    rewritten.
    """
    plan = D.plan_descriptions(
        natdex(NATIONAL_DEX_GAMMA="Gamma's own."), ENTRIES, FR, LG)
    ent, texts = run(plan)
    ptr = D.description_pointers(ent)
    blocks = D.parse_blocks(texts["fr"])
    assert ptr["NATIONAL_DEX_DELTA"] != ptr["NATIONAL_DEX_GAMMA"]
    assert blocks[ptr["NATIONAL_DEX_DELTA"]] == DUMMY_TEXT
    assert blocks["gDummyPokedexText"] == DUMMY_TEXT


def test_two_formerly_shared_creatures_get_distinct_symbols():
    plan = D.plan_descriptions(
        natdex(NATIONAL_DEX_GAMMA="Gamma's own.",
               NATIONAL_DEX_DELTA="Delta's own."), ENTRIES, FR, LG)
    syms = set(plan.allocations.values())
    assert len(syms) == 2, syms
    ent, texts = run(plan)
    blocks = D.parse_blocks(texts["fr"])
    ptr = D.description_pointers(ent)
    assert blocks[ptr["NATIONAL_DEX_GAMMA"]] == "Gamma's own."
    assert blocks[ptr["NATIONAL_DEX_DELTA"]] == "Delta's own."


def test_un_duplication_adds_exactly_one_block_per_sharer():
    """Replaces `test_unchanged_placeholders_produce_no_blocks_and_no_churn`,
    which asserted the rule the owner rejected on 2026-11-08.

    Two sharers, two new blocks -- not one per EDIT, and not one shared block
    between them. The churn half of the old intent is kept by
    `test_un_duplication_happens_once`.
    """
    plan = D.plan_descriptions(
        natdex(NATIONAL_DEX_GAMMA="Gamma's own."), ENTRIES, FR, LG)
    _ent, texts = run(plan)
    assert len(D.parse_blocks(texts["fr"])) == len(D.parse_blocks(FR)) + 2
    assert len(D.parse_blocks(texts["lg"])) == len(D.parse_blocks(LG)) + 2, (
        "a new symbol must exist in BOTH builds' files or one fails to link")


def test_save_and_reopen_reproduces_each_description():
    plan = D.plan_descriptions(
        natdex(NATIONAL_DEX_GAMMA="Gamma's own.",
               NATIONAL_DEX_ALPHA="New alpha."), ENTRIES, FR, LG)
    ent, texts = run(plan)

    # "reopen": read the pointers and blocks back from what was written
    ptr = D.description_pointers(ent)
    blocks = D.parse_blocks(texts["fr"])
    reopened = [{"dex_constant": c, "description": ptr[c],
                 "descriptionText": blocks[ptr[c]]} for c, _s in ROWS]
    again = D.plan_descriptions(reopened, ent, texts["fr"], texts["lg"])
    assert again.is_empty, "a second save would churn the files"
    assert blocks[ptr["NATIONAL_DEX_GAMMA"]] == "Gamma's own."
    assert blocks[ptr["NATIONAL_DEX_ALPHA"]] == "New alpha."


def test_a_cache_that_lost_the_pointer_recovers_it_from_the_engine():
    rows = natdex(NATIONAL_DEX_ALPHA="New alpha.")
    for r in rows:
        r.pop("description", None)          # the 77-creature extractor gap
    plan = D.plan_descriptions(rows, ENTRIES, FR, LG)
    assert plan.rewrites == {"gAlphaPokedexText": "New alpha."}
    assert "NATIONAL_DEX_ALPHA" not in plan.allocations, (
        "allocated a second symbol for an owner")


def test_a_cache_claiming_the_placeholder_does_not_override_the_engine():
    """The dangerous direction: the cache under-reports what a creature owns.

    ALPHA owns gAlphaPokedexText in the engine. A cache that says it is on the
    placeholder would have it allocated a SECOND symbol, leaving its original
    block orphaned and its old text still sitting in the file.
    """
    rows = natdex(NATIONAL_DEX_ALPHA="New alpha.")
    rows[0]["description"] = "gDummyPokedexText"
    plan = D.plan_descriptions(rows, ENTRIES, FR, LG)
    assert "NATIONAL_DEX_ALPHA" not in plan.allocations, plan.allocations
    assert plan.rewrites == {"gAlphaPokedexText": "New alpha."}


def test_a_cache_claiming_a_symbol_the_engine_does_not_use_is_ignored():
    """The other direction: the cache over-reports.

    GAMMA is on the placeholder in the engine. A cache naming someone else's
    symbol would rewrite THAT creature's block, while GAMMA's own entry still
    pointed at the placeholder -- the edit invisible, and a bystander's text
    destroyed.
    """
    rows = natdex(NATIONAL_DEX_GAMMA="Gamma's own.")
    rows[2]["description"] = "gBetaPokedexText"
    plan = D.plan_descriptions(rows, ENTRIES, FR, LG)
    assert "gBetaPokedexText" not in plan.rewrites, "clobbered a bystander"
    assert plan.allocations["NATIONAL_DEX_GAMMA"] == "gGammaPokedexText"
    _ent, texts = run(plan)
    assert D.parse_blocks(texts["fr"])["gBetaPokedexText"] == "Beta FireRed."


def test_a_colliding_symbol_gets_a_distinct_name():
    entries = ENTRIES.replace(
        "[NATIONAL_DEX_GAMMA]", "[NATIONAL_DEX_ALPHA_X]")
    rows = [{"dex_constant": "NATIONAL_DEX_ALPHA_X",
             "descriptionText": "Needs a name."}]
    # gAlphaXPokedexText is free, so force the clash directly instead
    taken = {"gAlphaXPokedexText"}
    first = D.allocate_symbol("NATIONAL_DEX_ALPHA_X", taken)
    assert first == "gAlphaXPokedexText2", first
    second = D.allocate_symbol("NATIONAL_DEX_ALPHA_X", taken)
    assert second == "gAlphaXPokedexText3", second
    assert len({first, second}) == 2
    plan = D.plan_descriptions(rows, entries, FR, LG)
    assert plan.allocations["NATIONAL_DEX_ALPHA_X"] not in D.taken_symbols(
        entries, FR, LG)


def test_an_entry_absent_from_the_engine_file_is_refused():
    rows = [{"dex_constant": "NATIONAL_DEX_NOWHERE",
             "descriptionText": "Words."}]
    plan = D.plan_descriptions(rows, ENTRIES, FR, LG)
    assert plan.is_empty
    assert any("NOWHERE" in c for c in plan.conflicts)


# ---------------------------------------------------------------------------
# The two files are not interchangeable
# ---------------------------------------------------------------------------

def test_an_edit_does_not_overwrite_the_other_builds_text():
    plan = D.plan_descriptions(
        natdex(NATIONAL_DEX_ALPHA="New alpha."), ENTRIES, FR, LG)
    _ent, texts = run(plan)
    assert D.parse_blocks(texts["lg"])["gAlphaPokedexText"] == "Alpha LeafGreen."


def test_a_new_block_is_defined_in_every_file():
    plan = D.plan_descriptions(
        natdex(NATIONAL_DEX_GAMMA="Gamma's own."), ENTRIES, FR, LG)
    _ent, texts = run(plan)
    sym = plan.allocations["NATIONAL_DEX_GAMMA"]
    for key, content in texts.items():
        assert sym in D.parse_blocks(content), f"{sym} missing from {key}"


def test_the_other_file_is_not_compared_against():
    """LeafGreen's differing text must not read as 256 pending edits."""
    plan = D.plan_descriptions(natdex(), ENTRIES, FR, LG)
    assert plan.rewrites == {}


# ---------------------------------------------------------------------------
# Refusals, and what they promise
# ---------------------------------------------------------------------------

def _assert_reassures(exc):
    assert "no engine file was modified" in str(exc).lower(), str(exc)


def test_rewriting_the_placeholder_is_refused():
    with pytest.raises(D.DescriptionError) as e:
        D.replace_block(FR, D.DUMMY, "anything")
    _assert_reassures(e.value)


def test_rewriting_a_missing_block_is_refused():
    with pytest.raises(D.DescriptionError) as e:
        D.replace_block(FR, "gNotThere", "anything")
    _assert_reassures(e.value)


def test_repointing_a_missing_entry_is_refused():
    with pytest.raises(D.DescriptionError) as e:
        D.repoint_entry(ENTRIES, "NATIONAL_DEX_NOWHERE", "gX")
    _assert_reassures(e.value)


def test_repointing_an_entry_with_no_description_line_is_refused():
    broken = ENTRIES.replace("        .description = gDummyPokedexText,\n", "", 1)
    with pytest.raises(D.DescriptionError) as e:
        D.repoint_entry(broken, "NATIONAL_DEX_GAMMA", "gX")
    _assert_reassures(e.value)


def test_apply_without_the_primary_file_is_refused():
    plan = D.plan_descriptions(
        natdex(NATIONAL_DEX_ALPHA="New alpha."), ENTRIES, FR, LG)
    with pytest.raises(D.DescriptionError) as e:
        D.apply(plan, ENTRIES, {"lg": LG}, "fr")
    _assert_reassures(e.value)


# ---------------------------------------------------------------------------
# verify() must be able to catch a half-written save
# ---------------------------------------------------------------------------

def test_verify_catches_a_pointer_written_without_its_block():
    plan = D.plan_descriptions(
        natdex(NATIONAL_DEX_GAMMA="Gamma's own."), ENTRIES, FR, LG)
    ent, texts = D.apply(plan, ENTRIES, files(), "fr")
    with pytest.raises(D.DescriptionError) as e:
        D.verify(plan, ent, {"fr": FR, "lg": texts["lg"]}, "fr")
    _assert_reassures(e.value)


def test_verify_catches_a_block_written_without_its_pointer():
    plan = D.plan_descriptions(
        natdex(NATIONAL_DEX_GAMMA="Gamma's own."), ENTRIES, FR, LG)
    _ent, texts = D.apply(plan, ENTRIES, files(), "fr")
    with pytest.raises(D.DescriptionError) as e:
        D.verify(plan, ENTRIES, texts, "fr")
    _assert_reassures(e.value)


def test_verify_catches_text_that_is_not_what_was_asked_for():
    plan = D.plan_descriptions(
        natdex(NATIONAL_DEX_ALPHA="New alpha."), ENTRIES, FR, LG)
    ent, texts = D.apply(plan, ENTRIES, files(), "fr")
    mangled = texts["fr"].replace("New alpha.", "Something else.")
    with pytest.raises(D.DescriptionError) as e:
        D.verify(plan, ent, {"fr": mangled, "lg": texts["lg"]}, "fr")
    _assert_reassures(e.value)


def test_verify_passes_on_a_correct_write():
    plan = D.plan_descriptions(
        natdex(NATIONAL_DEX_GAMMA="Gamma's own.",
               NATIONAL_DEX_ALPHA="New alpha."), ENTRIES, FR, LG)
    ent, texts = D.apply(plan, ENTRIES, files(), "fr")
    D.verify(plan, ent, texts, "fr")      # must not raise


# ---------------------------------------------------------------------------
# Round-tripping text through the C literal form
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "One line.",
    "Two\nlines.",
    'With "quotes" in it.',
    "Trailing blank line.\n",
    "POKéMON with an accent.",
    "A backslash \\ in the text.",
])
def test_text_survives_the_round_trip(text):
    block = D.render_block("gRoundTrip", text)
    assert D.parse_blocks(block) == {"gRoundTrip": text}


def test_a_rendered_block_looks_like_the_engines_own():
    block = D.render_block("gThing", "Line one.\nLine two.")
    assert block == (
        'const u8 gThing[] = _(\n'
        '    "Line one.\\n"\n'
        '    "Line two.");'
    )
