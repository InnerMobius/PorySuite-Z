"""Regression guard: evolution rows are validated against the ENGINE's rules.

THE BUG (found 2026-10-29, fixed 2026-10-29). Three defects, one root cause —
nothing checked an evolution row against what the chosen method actually needs.

1. A blank Param serialised to Python `None`, which `core/pokemon_data.py`'s
   `_fmt_param` turned into the literal text "None" in `gEvolutionTable`.
   `None` is not a C identifier, so the project stopped building. It was also
   self-perpetuating: the load path renders a stored `None` as the four-char
   text "None", the next save stores the *string* "None", and `_canon_evos`
   normalises "None" to "" so the species stopped being reported as edited.
2. Nothing enforced `EVOS_PER_MON`. Verified by compiling an 8-row case against
   the real struct: with -Werror (the default build, Makefile:83) it is an
   "excess elements in array initializer" ERROR; under MODERN=1 (no -Werror) the
   8th row is SILENTLY DISCARDED.
3. Form targets picked from the `evo_species` dropdown carried the dropdown's
   display INDENT ("    SPECIES_X") into `targetSpecies`, because the save read
   the displayed text instead of the stored constant. The emitted C tolerated
   the whitespace, but `core/refactor_service.py`'s three rename sites compare
   exactly, so renaming left a dangling reference that would not build.

THE RULE (do not simplify away):
  * NEVER default a blank param to 0. Read from pokefirered/src/pokemon.c:5450-5545:
    the nine level/beauty methods test `param <= level` (or `<= beauty`), so 0 is
    ALWAYS TRUE and the mon would evolve on its very next level-up;
    EVO_TRADE_ITEM tests `param == heldItem`, so 0 (ITEM_NONE) means "any trade
    holding nothing". 0 is correct for exactly the four methods whose param the
    engine never reads.
  * A refusal must leave the STORED evolutions untouched. Validation exists to
    protect data, not to rewrite it.
  * Limits come from the project's own headers (`EVOS_PER_MON`, `MAX_LEVEL`),
    never hardcoded.
  * Targets come from column 0's UserRole, never from the displayed text.
  * The three unsupported methods stay selectable and are only LABELLED;
    removing them would change what existing species may keep.

If you touch the evolution read-back, `_evo_row_problem`, or the evo_species
dropdown, keep this green.
"""

import os
import re
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "core"), os.path.join(_ROOT, "ui")):
    sys.path.insert(0, _p)

import pytest  # noqa: E402

MAINWINDOW = os.path.join(_ROOT, "ui", "mainwindow.py")


# ── the validator, exercised without building a MainWindow ───────────────────

class _Stub:
    """Just enough of MainWindow to call the real validator methods."""

    _EVO_ITEM_METHODS = frozenset({"EVO_ITEM", "EVO_TRADE_ITEM"})
    _EVO_NO_PARAM_METHODS = frozenset({
        "EVO_FRIENDSHIP", "EVO_FRIENDSHIP_DAY", "EVO_FRIENDSHIP_NIGHT",
        "EVO_TRADE",
    })
    _EVO_LEVEL_METHODS = frozenset({
        "EVO_LEVEL", "EVO_LEVEL_ATK_GT_DEF", "EVO_LEVEL_ATK_EQ_DEF",
        "EVO_LEVEL_ATK_LT_DEF", "EVO_LEVEL_SILCOON", "EVO_LEVEL_CASCOON",
        "EVO_LEVEL_NINJASK", "EVO_LEVEL_SHEDINJA",
    })
    _EVO_BEAUTY_METHODS = frozenset({"EVO_BEAUTY"})

    def _max_level(self):
        return 100


def _validator():
    """Bind the real `_evo_row_problem` onto the stub by source extraction.

    Importing ui.mainwindow pulls in the whole Qt app; the suite's other
    mainwindow tests stub it out wholesale. Extracting the method keeps this
    test honest (it runs the SHIPPING source) without that cost.
    """
    with open(MAINWINDOW, encoding="utf-8") as fh:
        src = fh.read()
    m = re.search(
        r"\n    def _evo_row_problem\(self, row: dict\).*?(?=\n    def )",
        src, re.S)
    assert m, "_evo_row_problem not found — did it get renamed?"
    body = "\n".join(l[4:] if l.startswith("    ") else l
                     for l in m.group(0).splitlines())
    ns = {}
    exec("class _V:\n" + "\n".join("    " + l for l in body.splitlines()), ns)
    fn = ns["_V"]._evo_row_problem
    _Stub._evo_row_problem = fn
    return _Stub()


@pytest.fixture(scope="module")
def v():
    return _validator()


def _row(method, param, target="SPECIES_TARGET"):
    return {"targetSpecies": target, "method": method, "param": param}


# ── the four methods whose param the engine ignores ──────────────────────────

@pytest.mark.parametrize("method", sorted(_Stub._EVO_NO_PARAM_METHODS))
@pytest.mark.parametrize("param", [None, "", 0, "0", "anything"])
def test_no_param_methods_accept_anything(v, method, param):
    """The engine never reads param for these, so nothing here is an error."""
    assert v._evo_row_problem(_row(method, param)) is None


# ── level / beauty methods: blank must be REFUSED, never defaulted ───────────

@pytest.mark.parametrize("method", sorted(
    _Stub._EVO_LEVEL_METHODS | _Stub._EVO_BEAUTY_METHODS))
@pytest.mark.parametrize("param", [None, "", "None"])
def test_blank_level_or_beauty_is_refused(v, method, param):
    problem = v._evo_row_problem(_row(method, param))
    assert problem, (
        f"{method} with param {param!r} must be refused — 0 would mean "
        f"'evolves on the next level-up'"
    )
    assert "SPECIES_TARGET" in problem, "the message must name the row"


@pytest.mark.parametrize("method", sorted(_Stub._EVO_LEVEL_METHODS))
def test_valid_level_is_accepted(v, method):
    assert v._evo_row_problem(_row(method, 16)) is None
    assert v._evo_row_problem(_row(method, "36")) is None
    assert v._evo_row_problem(_row(method, " 36 ")) is None


@pytest.mark.parametrize("bad", [0, 101, -1, 9999])
def test_out_of_range_level_is_refused(v, bad):
    assert v._evo_row_problem(_row("EVO_LEVEL", bad)), f"level {bad} must fail"


def test_beauty_allows_zero_but_level_does_not(v):
    """Beauty's usable range starts at 0; a level of 0 is meaningless."""
    assert v._evo_row_problem(_row("EVO_BEAUTY", 0)) is None
    assert v._evo_row_problem(_row("EVO_LEVEL", 0))


@pytest.mark.parametrize("junk", ["abc", "sixteen", "16a", "--"])
def test_non_numeric_level_is_refused(v, junk):
    problem = v._evo_row_problem(_row("EVO_LEVEL", junk))
    assert problem and junk in problem


# ── item methods ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("method", sorted(_Stub._EVO_ITEM_METHODS))
def test_item_method_needs_a_real_item(v, method):
    assert v._evo_row_problem(_row(method, "ITEM_FIRE_STONE")) is None
    for bad in (None, "", "0", "ITEM_NONE", "None"):
        assert v._evo_row_problem(_row(method, bad)), (
            f"{method} with {bad!r} must be refused — ITEM_NONE means "
            f"'any trade holding nothing' for EVO_TRADE_ITEM"
        )
    assert v._evo_row_problem(_row(method, "16")), \
        "a bare number is not an item constant"


# ── structural problems ──────────────────────────────────────────────────────

def test_missing_target_is_refused(v):
    assert v._evo_row_problem(_row("EVO_LEVEL", 16, target=""))
    assert v._evo_row_problem(_row("EVO_LEVEL", 16, target="   "))


def test_missing_method_is_refused(v):
    assert v._evo_row_problem(_row("", 16))


def test_whitespace_target_is_tolerated_by_the_validator(v):
    """The read-back strips it; the validator must not then reject it."""
    assert v._evo_row_problem(_row("EVO_LEVEL", 16,
                                   target="    SPECIES_TARGET")) is None


# ── the surrounding wiring, asserted on the shipping source ─────────────────

@pytest.fixture(scope="module")
def src():
    with open(MAINWINDOW, encoding="utf-8") as fh:
        return fh.read()


def test_save_reads_the_target_constant_not_the_display_text(src):
    m = re.search(r"\n    def save_species_data\(.*?(?=\n    def )", src, re.S)
    assert m
    body = m.group(0)
    assert 'item.data(0, Qt.ItemDataRole.UserRole)' in body, (
        "the save must take targetSpecies from column 0's user data"
    )
    assert '"targetSpecies": target' in body


def test_rows_store_the_target_constant_in_column_zero(src):
    for fn in ("add_evolution", "edit_evolution"):
        m = re.search(rf"\n    def {fn}\(.*?(?=\n    def )", src, re.S)
        assert m, fn
        assert "setData(\n                0, Qt.ItemDataRole.UserRole" in m.group(0) \
            or "setData(0, Qt.ItemDataRole.UserRole" in m.group(0), (
                f"{fn} must stash the target constant on column 0")


def test_dropdown_restore_uses_find_data(src):
    m = re.search(r"\n    def update_evolutions\(.*?(?=\n    def )", src, re.S)
    assert m
    assert "evo_species.findData(" in m.group(0), (
        "update_evolutions must restore the target by constant, not by text"
    )


def test_limits_are_read_from_the_project_headers(src):
    assert "#define EVOS_PER_MON" in src, "EVOS_PER_MON must be parsed, not hardcoded"
    assert "#define MAX_LEVEL" in src, "MAX_LEVEL must be parsed, not hardcoded"
    assert "def _evos_per_mon" in src
    assert "def _max_level" in src


def test_a_refusal_abandons_the_write_instead_of_truncating(src):
    """The failed rows are dropped from the list, so the write MUST be skipped
    — otherwise saving would delete the evolution it refused."""
    m = re.search(r"\n    def save_species_data\(.*?(?=\n    def )", src, re.S)
    body = m.group(0)
    assert "evolutions = None" in body
    assert "if evolutions is not None and _canon_evos" in body, (
        "the evolution write must be guarded so a refusal changes nothing"
    )


def test_unsupported_methods_are_labelled_not_removed(src):
    assert "_EVO_UNSUPPORTED_METHODS" in src
    assert "not supported by this engine" in src
    m = re.search(r"_EVO_UNSUPPORTED_METHODS = frozenset\(\{(.*?)\}\)", src, re.S)
    assert m
    block = m.group(1)
    for meth in ("EVO_FRIENDSHIP_DAY", "EVO_FRIENDSHIP_NIGHT",
                 "EVO_LEVEL_SHEDINJA"):
        assert meth in block
    # and they must NOT be filtered out of the dropdown
    assert "continue" not in re.search(
        r"if evolution_method in self\._EVO_UNSUPPORTED_METHODS:.*?addItem",
        src, re.S).group(0), "unsupported methods must stay selectable"


# ── the writer must no longer be able to emit non-numeric params ─────────────

def test_fmt_param_never_emits_the_word_None_for_a_valid_row():
    """End-to-end on the real writer helper: every param the validator now
    allows for a level method must format as a number."""
    import importlib.util as ilu
    s = ilu.spec_from_file_location(
        "pd", os.path.join(_ROOT, "core", "pokemon_data.py"))
    # pokemon_data pulls in the whole core package; extract just the helper.
    with open(os.path.join(_ROOT, "core", "pokemon_data.py"),
              encoding="utf-8") as fh:
        text = fh.read()
    m = re.search(r"            def _fmt_param\(p\):.*?(?=\n            for )",
                  text, re.S)
    assert m, "_fmt_param not found"
    body = "\n".join(l[12:] if l.startswith("            ") else l
                     for l in m.group(0).splitlines())
    ns = {}
    exec(body, ns)
    fmt = ns["_fmt_param"]
    for ok in (1, 16, 36, 100, "16", " 36 "):
        out = fmt(ok)
        assert out.strip().lstrip("-").isdigit(), f"{ok!r} -> {out!r}"
    # And the shapes the validator now blocks are exactly the ones that used
    # to produce broken C, which is why they must never reach the writer.
    assert fmt(None) == "None"
    assert fmt("abc") == "abc"


# ── the three unsupported methods must be VISIBLY flagged, not silent ───────
# Added 2026-10-29 after review: the dropdown labelled them, but a row ALREADY
# using one showed a plain name, so an existing evolution that can never fire
# looked normal. Both places are flagged now, and the data is never changed.

def test_unsupported_method_is_flagged_in_the_dropdown(src):
    m = re.search(r"if evolution_method in self\._EVO_UNSUPPORTED_METHODS:.*?"
                  r"addItem\(name, evolution_method\)", src, re.S)
    assert m, "the dropdown must label unsupported methods before adding them"
    assert "not supported by this engine" in m.group(0)


def test_unsupported_method_is_flagged_on_an_EXISTING_row(src):
    """A loaded evolution using one must say so on the row itself."""
    m = re.search(r"method_name = str\(method_const\)(.*?)try:", src, re.S)
    assert m, "the load path's method_name block moved"
    assert "_EVO_UNSUPPORTED_METHODS" in m.group(1), (
        "a row that already uses an unsupported method must be flagged too, "
        "not just one being picked"
    )
    assert "not supported by this engine" in m.group(1)


def test_unsupported_row_gets_an_explanatory_tooltip(src):
    assert "never acts on this" in src and "setToolTip" in src, (
        "the row should explain what 'not supported' means for the player"
    )
    assert "nothing has been changed for" in src, (
        "the tooltip must reassure the user their data was not altered"
    )


def test_the_name_lookup_tolerates_the_suffix(src):
    """The save's fallback reverse-looks-up the method by display NAME; the
    suffix must not break it."""
    m = re.search(r"\n    def save_species_data\(.*?(?=\n    def )", src, re.S)
    assert 'split(" — not supported")' in m.group(0), (
        "stripping the suffix keeps the name-based fallback working"
    )


def test_unsupported_methods_are_still_accepted_by_the_validator(v):
    """Preserve existing data: a row using one must SAVE, not be refused."""
    assert v._evo_row_problem(_row("EVO_FRIENDSHIP_DAY", None)) is None
    assert v._evo_row_problem(_row("EVO_FRIENDSHIP_NIGHT", "")) is None
    # SHEDINJA is level-based, so it still needs a valid level - but a valid
    # one must be accepted rather than rejected for being unsupported.
    assert v._evo_row_problem(_row("EVO_LEVEL_SHEDINJA", 20)) is None


def test_nothing_auto_replaces_an_unsupported_method(src):
    """No code may swap the method out from under the user."""
    for bad in ('method"] = "EVO_LEVEL"', "method'] = 'EVO_LEVEL'",
                'method"] = "EVO_FRIENDSHIP"'):
        assert bad not in src, f"found an automatic method replacement: {bad}"
