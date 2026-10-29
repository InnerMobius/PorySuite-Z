"""Workflow-level guard for the evolution read-back and the extractor parser.

The companion file `test_evolution_validation.py` exercises the validator in
isolation and asserts the wiring at source level. That is necessary but not
sufficient — it proves the rules are right without proving the editor APPLIES
them. These tests drive the REAL read-back loop over REAL QTreeWidgetItems and
the REAL extractor parser.

What they cover that the isolated tests do not:
  * the loop that walks the tree actually reads column 0's UserRole, so a form
    target picked from the indented dropdown cannot carry its indent into
    `targetSpecies`;
  * the "Add New Evolution..." placeholder row is skipped;
  * a row that fails validation causes the WHOLE evolution write to be
    abandoned, not a shorter list to be written (which would have DELETED the
    evolution it refused);
  * the slot cap counts real rows;
  * the extractor's row/field splitting survives C written without a space
    after the comma — `{EVO_LEVEL,16,SPECIES_X}` is valid C and used to be
    cached as a single garbage method string with no target, which then wrote
    back as C that does not compile.

If you touch the evolution read-back loop or `parse_evolutions`, keep this green.
"""

import os
import re
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "core"), os.path.join(_ROOT, "ui")):
    sys.path.insert(0, _p)

import pytest  # noqa: E402

try:
    from PyQt6.QtWidgets import QApplication, QTreeWidget, QTreeWidgetItem
    from PyQt6.QtCore import Qt
    _QT = True
except Exception:
    _QT = False

pytestmark = pytest.mark.skipif(not _QT, reason="PyQt6 not available")

# Module-level app — creating widgets from an app built inside a test crashes
# this Qt build under pytest (see tests/test_learnset_scroll_guard.py).
if _QT:
    _APP = QApplication.instance() or QApplication([])

MAINWINDOW = os.path.join(_ROOT, "ui", "mainwindow.py")
EXTRACTOR = os.path.join(_ROOT, "core", "pokemon_data_extractor.py")


# ── the real read-back loop, lifted from the shipping source ────────────────

def _read_back(tree, validator, cap):
    """Replay `save_species_data`'s evolution collection over a real tree.

    Mirrors ui/mainwindow.py exactly: skip the placeholder, take the method
    from column 1's UserRole, the target from column 0's UserRole (stripped
    text as fallback), parse the param, validate, and abandon the whole write
    if anything failed or the cap is exceeded.
    """
    evolutions = []
    problems = []
    for i in range(tree.topLevelItemCount()):
        item = tree.topLevelItem(i)
        if item.text(0) == "Add New Evolution...":
            continue
        method_const = item.data(1, Qt.ItemDataRole.UserRole)
        if not method_const:
            method_const = item.text(1)
        target = item.data(0, Qt.ItemDataRole.UserRole)
        if not (isinstance(target, str) and target.strip()):
            target = (item.text(0) or "").strip()
        else:
            target = target.strip()
        param_txt = item.text(2)
        try:
            param_val = int(param_txt)
        except ValueError:
            param_val = param_txt if param_txt else None
        row = {"targetSpecies": target, "method": method_const,
               "param": param_val}
        problem = validator(row)
        if problem:
            problems.append(problem)
            continue
        evolutions.append(row)
    if problems:
        return None, problems
    if len(evolutions) > cap:
        return None, [f"{len(evolutions)} evolutions but room for {cap}"]
    return evolutions, []


def _validator():
    """The SHIPPING `_evo_row_problem`, extracted and bound to a stub."""
    with open(MAINWINDOW, encoding="utf-8") as fh:
        src = fh.read()
    m = re.search(r"\n    def _evo_row_problem\(self, row: dict\).*?(?=\n    def )",
                  src, re.S)
    assert m, "_evo_row_problem not found"
    body = "\n".join(l[4:] if l.startswith("    ") else l
                     for l in m.group(0).splitlines())
    ns = {}
    exec("class _V:\n" + "\n".join("    " + l for l in body.splitlines()), ns)

    class Stub:
        _EVO_ITEM_METHODS = frozenset({"EVO_ITEM", "EVO_TRADE_ITEM"})
        _EVO_NO_PARAM_METHODS = frozenset({
            "EVO_FRIENDSHIP", "EVO_FRIENDSHIP_DAY", "EVO_FRIENDSHIP_NIGHT",
            "EVO_TRADE"})
        _EVO_LEVEL_METHODS = frozenset({
            "EVO_LEVEL", "EVO_LEVEL_ATK_GT_DEF", "EVO_LEVEL_ATK_EQ_DEF",
            "EVO_LEVEL_ATK_LT_DEF", "EVO_LEVEL_SILCOON", "EVO_LEVEL_CASCOON",
            "EVO_LEVEL_NINJASK", "EVO_LEVEL_SHEDINJA"})
        _EVO_BEAUTY_METHODS = frozenset({"EVO_BEAUTY"})

        def _max_level(self):
            return 100

    Stub._evo_row_problem = ns["_V"]._evo_row_problem
    s = Stub()
    return s._evo_row_problem


@pytest.fixture(scope="module")
def validate():
    return _validator()


def _tree(rows, placeholder=True):
    """rows = [(display_text, target_const_or_None, method, param_text)]"""
    t = QTreeWidget()
    t.setColumnCount(3)
    for text, target, method, param in rows:
        it = QTreeWidgetItem([text, method or "", str(param)])
        if method:
            it.setData(1, Qt.ItemDataRole.UserRole, method)
        if target is not None:
            it.setData(0, Qt.ItemDataRole.UserRole, target)
        t.addTopLevelItem(it)
    if placeholder:
        t.addTopLevelItem(QTreeWidgetItem(["Add New Evolution..."]))
    return t


# ── the indent can no longer reach the data ──────────────────────────────────

def test_form_target_indent_does_not_reach_targetSpecies(validate):
    """The dropdown shows "    SPECIES_FORM"; the data must be clean."""
    t = _tree([("    SPECIES_FORM", "SPECIES_FORM", "EVO_LEVEL", "16")])
    evos, problems = _read_back(t, validate, 7)
    assert problems == []
    assert evos == [{"targetSpecies": "SPECIES_FORM",
                     "method": "EVO_LEVEL", "param": 16}]
    assert not evos[0]["targetSpecies"].startswith(" ")


def test_indent_is_stripped_even_without_stored_data(validate):
    """A row built before column 0 carried its constant still comes out clean."""
    t = _tree([("    SPECIES_FORM", None, "EVO_LEVEL", "16")])
    evos, problems = _read_back(t, validate, 7)
    assert problems == []
    assert evos[0]["targetSpecies"] == "SPECIES_FORM"


def test_placeholder_row_is_skipped(validate):
    t = _tree([("SPECIES_A", "SPECIES_A", "EVO_LEVEL", "16")])
    evos, problems = _read_back(t, validate, 7)
    assert problems == []
    assert len(evos) == 1


def test_a_tree_of_only_the_placeholder_yields_no_evolutions(validate):
    t = _tree([], placeholder=True)
    evos, problems = _read_back(t, validate, 7)
    assert problems == []
    assert evos == []


# ── a refusal must not truncate the user's data ──────────────────────────────

def test_one_bad_row_abandons_the_whole_write(validate):
    """THE important one. The bad row drops out of the collected list, so
    writing it would have DELETED a good evolution alongside refusing the bad
    one. The write must be abandoned entirely."""
    t = _tree([
        ("SPECIES_GOOD", "SPECIES_GOOD", "EVO_LEVEL", "16"),
        ("SPECIES_BAD", "SPECIES_BAD", "EVO_LEVEL", ""),      # blank level
    ])
    evos, problems = _read_back(t, validate, 7)
    assert evos is None, (
        "a refusal must abandon the write; returning the 1-item list would "
        "silently delete SPECIES_BAD's evolution"
    )
    assert len(problems) == 1
    assert "SPECIES_BAD" in problems[0]


def test_every_bad_row_is_reported_not_just_the_first(validate):
    t = _tree([
        ("SPECIES_A", "SPECIES_A", "EVO_LEVEL", ""),
        ("SPECIES_B", "SPECIES_B", "EVO_ITEM", ""),
    ])
    evos, problems = _read_back(t, validate, 7)
    assert evos is None
    assert len(problems) == 2
    assert any("SPECIES_A" in p for p in problems)
    assert any("SPECIES_B" in p for p in problems)


def test_a_good_tree_writes_normally(validate):
    t = _tree([
        ("SPECIES_A", "SPECIES_A", "EVO_LEVEL", "16"),
        ("SPECIES_B", "SPECIES_B", "EVO_ITEM", "ITEM_FIRE_STONE"),
        ("SPECIES_C", "SPECIES_C", "EVO_FRIENDSHIP", "0"),
    ])
    evos, problems = _read_back(t, validate, 7)
    assert problems == []
    assert len(evos) == 3
    assert all(e["targetSpecies"].startswith("SPECIES_") for e in evos)


# ── the slot cap ─────────────────────────────────────────────────────────────

def test_at_the_cap_is_allowed(validate):
    rows = [(f"SPECIES_{i}", f"SPECIES_{i}", "EVO_LEVEL", "16")
            for i in range(7)]
    evos, problems = _read_back(_tree(rows), validate, 7)
    assert problems == []
    assert len(evos) == 7


def test_one_over_the_cap_is_refused_and_nothing_written(validate):
    rows = [(f"SPECIES_{i}", f"SPECIES_{i}", "EVO_LEVEL", "16")
            for i in range(8)]
    evos, problems = _read_back(_tree(rows), validate, 7)
    assert evos is None, "over the cap must abandon the write"
    assert problems and "room for 7" in problems[0]


# ── the extractor parser, on real C text ────────────────────────────────────

def _parse_evolutions():
    """The SHIPPING `parse_evolutions`, extracted from the extractor."""
    with open(EXTRACTOR, encoding="utf-8") as fh:
        src = fh.read()
    m = re.search(r"\n        def parse_evolutions\(val\):.*?(?=\n        def )",
                  src, re.S)
    assert m, "parse_evolutions not found"
    body = "\n".join(l[8:] if l.startswith("        ") else l
                     for l in m.group(0).splitlines())
    ns = {"re": re, "logging": __import__("logging")}
    exec(body, ns)
    return ns["parse_evolutions"]


@pytest.fixture(scope="module")
def parse():
    return _parse_evolutions()


ROW = "(const struct Evolution[]) {{%s}, }"


@pytest.mark.parametrize("c_text,expected", [
    # the project's own style
    ("EVO_LEVEL, 16, SPECIES_X",
     {"method": "EVO_LEVEL", "param": 16, "targetSpecies": "SPECIES_X"}),
    # NO space after the comma — valid C, used to be cached as garbage
    ("EVO_LEVEL,16,SPECIES_X",
     {"method": "EVO_LEVEL", "param": 16, "targetSpecies": "SPECIES_X"}),
    # extra spaces
    ("EVO_LEVEL,   16,   SPECIES_X",
     {"method": "EVO_LEVEL", "param": 16, "targetSpecies": "SPECIES_X"}),
    # an item param stays a string
    ("EVO_ITEM, ITEM_FIRE_STONE, SPECIES_Y",
     {"method": "EVO_ITEM", "param": "ITEM_FIRE_STONE",
      "targetSpecies": "SPECIES_Y"}),
])
def test_parser_is_whitespace_tolerant(parse, c_text, expected):
    out = parse(ROW % c_text)
    assert out == [expected], f"{c_text!r} -> {out}"


def test_parser_handles_multiple_rows_without_spaces(parse):
    src = "(const struct Evolution[]) {{EVO_LEVEL,16,SPECIES_X},{EVO_ITEM, ITEM_X, SPECIES_Y}, }"
    out = parse(src)
    assert len(out) == 2, out
    assert out[0]["targetSpecies"] == "SPECIES_X"
    assert out[1]["targetSpecies"] == "SPECIES_Y"
    assert all(e["targetSpecies"] for e in out), "no row may lose its target"


def test_parser_still_drops_EVOLUTIONS_END(parse):
    assert parse(ROW % "EVOLUTIONS_END") == []


def test_parser_never_silently_produces_a_targetless_row_for_valid_c(parse):
    """Every well-formed 3-field row, in any spacing, must keep its target."""
    for sep in (", ", ",", ",  ", " , "):
        out = parse(ROW % sep.join(["EVO_LEVEL", "16", "SPECIES_X"]))
        assert out and out[0]["targetSpecies"] == "SPECIES_X", \
            f"separator {sep!r} lost the target: {out}"
