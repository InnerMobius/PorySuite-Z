"""NEW3a containment — SUPERSEDED 2026-11-01 by the actual repair.

WHAT THIS FILE USED TO TEST

A description is a pointer into a separate text file, and in the owner's
project 102 of 488 creatures pointed at one shared ``gDummyPokedexText``
block. The save path filed descriptions by C SYMBOL, so the last creature in
the list silently won the shared block and every earlier edit was discarded
before anything was written. The containment refused those writes and said
why. `_plan_pokedex_descriptions` and `_current_description_in_header` on
MainWindow implemented it.

WHY IT IS SUPERSEDED

Refusing was never the repair — it was the brake. The repair allocates a
symbol of its own to any creature actually given text, lazily, and writes the
block and the pointer together. `core/pokedex_descriptions.py` holds that, and
`tests/test_pokedex_descriptions.py` tests it. The two MainWindow helpers were
deleted rather than left behind, so this file's old tests had nothing to call.

WHAT SURVIVES, AND IS TESTED BELOW

Four of the five containment guarantees are still requirements, and the repair
must honour them. They are kept here, against the new implementation, because
they were learned from a real failure and would otherwise quietly lapse:

  * A save that changed nothing produces NO diagnostics. Most saves touch no
    description at all, and an editor that complains about the data's shape on
    every save trains the user to ignore it.
  * An unchanged placeholder sharing a symbol is NOT a conflict and NOT a
    write.
  * A creature with its own symbol still saves normally, in place.
  * The shared placeholder block itself is never rewritten.

The fifth — "a description with no symbol must be refused" — is the one the
repair deliberately changes: it is now allocated a symbol instead of refused.
That is the whole point, and the test for it says so rather than being deleted
quietly.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import pokedex_descriptions as pdesc  # noqa: E402

PLACEHOLDER = "A placeholder.\nNothing to see."
SOLO_TEXT = "Solo line one.\nSolo line two."

TEXT = (
    'const u8 gSoloPokedexText[] = _(\n'
    '    "Solo line one.\\n"\n'
    '    "Solo line two.");\n'
    '\n'
    'const u8 gDummyPokedexText[] = _(\n'
    '    "A placeholder.\\n"\n'
    '    "Nothing to see.");\n'
)


def _entry(const, symbol, text):
    body = (
        f"    [{const}] =\n"
        "    {\n"
        '        .categoryName = _("THING"),\n'
        "        .height = 10,\n"
        f"        .description = {symbol},\n"
        f"        .unusedDescription = {symbol},\n"
        "        .pokemonScale = 256,\n"
        "    },\n"
    )
    row = {"dex_constant": const, "descriptionText": text}
    if symbol:
        row["description"] = symbol
    return body, row


def _project(rows):
    """Build (entries_text, natdex) from (const, symbol, text) triples."""
    blocks, natdex = [], []
    for const, symbol, text in rows:
        body, row = _entry(const, symbol or "gDummyPokedexText", text)
        blocks.append(body)
        natdex.append(row)
    entries = ("const struct PokedexEntry gPokedexEntries[] =\n{\n"
               + "".join(blocks) + "};\n")
    return entries, natdex


DEFAULT = [
    ("NATIONAL_DEX_SOLO", "gSoloPokedexText", SOLO_TEXT),
    ("NATIONAL_DEX_A", "gDummyPokedexText", PLACEHOLDER),
    ("NATIONAL_DEX_B", "gDummyPokedexText", PLACEHOLDER),
]


# ---------------------------------------------------------------------------
# The guarantees that survive
# ---------------------------------------------------------------------------

def test_a_save_that_changed_nothing_says_nothing():
    """Note the fixture. DEFAULT puts A and B on the shared placeholder, and
    since 2026-11-08 that is itself work to do, so "changed nothing" means a
    project where every creature already owns its block."""
    entries, natdex = _project([DEFAULT[0]])
    plan = pdesc.plan_descriptions(natdex, entries, TEXT)
    assert plan.conflicts == []
    assert plan.is_empty


def test_an_unchanged_placeholder_is_not_a_conflict():
    """It never was a conflict, and now it is not a refusal either -- it is
    un-duplicated. Both halves asserted."""
    entries, natdex = _project(DEFAULT)
    plan = pdesc.plan_descriptions(natdex, entries, TEXT)
    assert plan.conflicts == []
    assert "NATIONAL_DEX_A" in plan.allocations


def test_un_duplicating_an_unedited_sharer_keeps_its_words():
    """Same words, storage of its own. The screen does not change."""
    entries, natdex = _project(DEFAULT)
    plan = pdesc.plan_descriptions(natdex, entries, TEXT)
    for const in ("NATIONAL_DEX_A", "NATIONAL_DEX_B"):
        sym = plan.allocations[const]
        assert plan.new_blocks[sym] == PLACEHOLDER
    assert plan.allocations["NATIONAL_DEX_A"] != plan.allocations["NATIONAL_DEX_B"]


def test_a_creature_with_its_own_symbol_saves_in_place():
    rows = list(DEFAULT)
    rows[0] = ("NATIONAL_DEX_SOLO", "gSoloPokedexText", "Brand new words.")
    entries, natdex = _project(rows)
    plan = pdesc.plan_descriptions(natdex, entries, TEXT)
    assert plan.rewrites == {"gSoloPokedexText": "Brand new words."}
    assert "NATIONAL_DEX_SOLO" not in plan.allocations, (
        "an owner must not be allocated a second symbol")
    assert not any(s.startswith("gSolo") for s in plan.new_blocks), (
        "an owner's block is rewritten in place, never duplicated")


def test_the_shared_placeholder_block_is_never_rewritten():
    rows = list(DEFAULT)
    rows[1] = ("NATIONAL_DEX_A", "gDummyPokedexText", "A's own words.")
    entries, natdex = _project(rows)
    plan = pdesc.plan_descriptions(natdex, entries, TEXT)
    assert "gDummyPokedexText" not in plan.rewrites
    _ent, texts = pdesc.apply(plan, entries, {"fr": TEXT}, "fr")
    assert pdesc.parse_blocks(texts["fr"])["gDummyPokedexText"] == PLACEHOLDER


def test_rewriting_the_placeholder_is_refused_outright():
    """Even if some future caller asks for it directly."""
    try:
        pdesc.replace_block(TEXT, pdesc.DUMMY, "anything")
    except pdesc.DescriptionError as exc:
        assert "no engine file was modified" in str(exc).lower()
    else:
        raise AssertionError("the shared placeholder was rewritten")


# ---------------------------------------------------------------------------
# The guarantee that deliberately changed
# ---------------------------------------------------------------------------

def test_a_description_with_no_symbol_is_now_allocated_one():
    """This REVERSES the old containment, on purpose.

    The old behaviour was to refuse and tell the user their creature could not
    have its own description. That was a true report of a broken editor, not a
    feature. Allocation is the repair.
    """
    rows = list(DEFAULT) + [
        ("NATIONAL_DEX_NEW", None, "Words for a creature with no symbol."),
    ]
    entries, natdex = _project(rows)
    natdex[-1].pop("description", None)      # the cache knew of no pointer

    plan = pdesc.plan_descriptions(natdex, entries, TEXT)
    assert plan.allocations["NATIONAL_DEX_NEW"] == "gNewPokedexText"
    assert plan.new_blocks["gNewPokedexText"] == \
        "Words for a creature with no symbol."
    assert plan.conflicts == []
