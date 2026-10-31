"""NEW3a: Pokédex descriptions keyed by a shared or missing C symbol.

`_write_pokedex_text_header` filed descriptions by their C SYMBOL with a plain
`desc_map[sym] = desc` walked in list order. In this project 25 dex entries
share `gDummyPokedexText` and 77 more carry no symbol at all — 102 of 488. So
the LAST entry silently won the shared block, every earlier edit was discarded
before anything was written, and whichever text survived was shown by all 103
engine rows pointing at that symbol. The editor meanwhile kept showing each
creature its own text, because the UI reads pokedex.json.

DO NOT REGRESS:
  * A save that changed nothing must produce NO diagnostics. Most saves touch
    no description at all, and an editor that complains about the data's
    shape on every save trains the user to ignore it.
  * An unchanged placeholder sharing a symbol is NOT a conflict.
  * One diagnostic per underlying CAUSE. Editing one creature whose symbol is
    shared by 25 others must not emit 24 redundant errors.
  * A creature with its own symbol must still save normally.
  * A description with no symbol must be refused, not silently dropped.
  * This is containment only. Allocating per-entry symbols (NEW3b) rewrites
    engine source for 102 creatures and is the owner's decision.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import mainwindow as mw  # noqa: E402
MW = mw.MainWindow

HEADER = (
    'const u8 gSoloPokedexText[] = _(\n'
    '    "Solo line one.\\n"\n'
    '    "Solo line two.");\n'
    '\n'
    'const u8 gDummyPokedexText[] = _(\n'
    '    "A placeholder.\\n"\n'
    '    "Nothing to see.");\n'
)

PLACEHOLDER = "A placeholder.\nNothing to see."
SOLO_TEXT = "Solo line one.\nSolo line two."


class _Stub:
    _plan_pokedex_descriptions = MW._plan_pokedex_descriptions
    _current_description_in_header = staticmethod(
        MW._current_description_in_header)


def _dex(**overrides):
    entries = [
        {"dex_constant": "NATIONAL_DEX_SOLO", "description": "gSoloPokedexText",
         "descriptionText": SOLO_TEXT},
        {"dex_constant": "NATIONAL_DEX_A", "description": "gDummyPokedexText",
         "descriptionText": PLACEHOLDER},
        {"dex_constant": "NATIONAL_DEX_B", "description": "gDummyPokedexText",
         "descriptionText": PLACEHOLDER},
        {"dex_constant": "NATIONAL_DEX_C", "description": "",
         "descriptionText": ""},
    ]
    for e in entries:
        if e["dex_constant"] in overrides:
            e["descriptionText"] = overrides[e["dex_constant"]]
    return entries


def _plan(entries):
    return _Stub()._plan_pokedex_descriptions(entries, HEADER)


def test_the_header_reader_round_trips():
    """The comparison depends on reading back what is on disk."""
    read = _Stub._current_description_in_header(HEADER, "gSoloPokedexText")
    assert read == SOLO_TEXT


def test_an_unknown_symbol_reads_as_absent():
    assert _Stub._current_description_in_header(HEADER, "gMissing") is None


def test_an_untouched_save_says_nothing():
    """The common case. Most saves change no description at all."""
    desc_map, conflicts = _plan(_dex())
    assert desc_map == {}
    assert conflicts == []


def test_a_creature_with_its_own_symbol_still_saves():
    desc_map, conflicts = _plan(_dex(NATIONAL_DEX_SOLO="Brand new text."))
    assert desc_map == {"gSoloPokedexText": "Brand new text."}
    assert conflicts == []


def test_editing_a_shared_symbol_is_refused():
    desc_map, conflicts = _plan(_dex(NATIONAL_DEX_A="Only mine."))
    assert desc_map == {}
    assert len(conflicts) == 1
    assert "gDummyPokedexText" in conflicts[0]
    assert "NATIONAL_DEX_A" in conflicts[0]


def test_editing_two_shared_entries_gives_one_diagnostic_not_two():
    """One per underlying cause, not one per creature."""
    desc_map, conflicts = _plan(
        _dex(NATIONAL_DEX_A="Mine.", NATIONAL_DEX_B="Also mine."))
    assert desc_map == {}
    assert len(conflicts) == 1


def test_an_unchanged_shared_placeholder_is_not_a_conflict():
    """Sharing a symbol is only a problem when something CHANGED."""
    entries = _dex(NATIONAL_DEX_SOLO="New solo text.")
    desc_map, conflicts = _plan(entries)
    assert "gSoloPokedexText" in desc_map
    assert conflicts == [], "the untouched placeholders were reported"


def test_a_description_with_no_symbol_is_refused_not_dropped():
    entries = _dex()
    entries[3]["descriptionText"] = "Text with nowhere to go."
    desc_map, conflicts = _plan(entries)
    assert desc_map == {}
    assert len(conflicts) == 1
    assert "NATIONAL_DEX_C" in conflicts[0]
    assert "no text symbol" in conflicts[0]


def test_entries_without_any_text_are_ignored_entirely():
    """The 77 symbol-less entries carry no text today; that is not an error."""
    desc_map, conflicts = _plan(_dex())
    assert conflicts == []


def test_the_writer_goes_through_the_planner():
    import ast
    import inspect
    src = inspect.getsource(MW._write_pokedex_text_header)
    assert "_plan_pokedex_descriptions" in src
    tree = ast.parse(inspect.getsource(sys.modules[MW.__module__]))
    # the old unconditional mapping must be gone from live code
    offenders = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Subscript)
                and isinstance(node.targets[0].value, ast.Name)
                and node.targets[0].value.id == "desc_map"
                and isinstance(node.value, ast.Name)
                and node.value.id == "desc"):
            offenders.append(node.lineno)
    assert not offenders, (
        "descriptions are still filed by symbol unconditionally at line(s) %s"
        % offenders)
