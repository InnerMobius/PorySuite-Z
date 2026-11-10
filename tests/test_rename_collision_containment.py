"""A rename must not corrupt other identifiers.

**History, because the guarantee moved.** `_rename_in_file` used to apply a
PLAIN SUBSTRING replace (`ln.replace(old, new)`), so renaming an identifier
that occurs inside another live identifier rewrote that one too. Measured in
this project: renaming MOVE_THUNDER rewrote MOVE_THUNDERBOLT,
MOVE_THUNDER_PUNCH, MOVE_THUNDER_WAVE and MOVE_THUNDER_SHOCK — 0 of 4
neighbours survived. `collision_preflight()` was added as CONTAINMENT: it
refused such a rename outright rather than performing it correctly.

On 2026-11-08 the replace itself was repaired. `core.refactor_service._bounded`
gives the single-token sweep the same asymmetric identifier boundaries the
multi-token sweep had used for species renames since 2026-10-29, so
MOVE_THUNDER now renames while all four neighbours survive. Refusing that
rename would be containment standing in for a repair that has landed, so the
preflight judges by the SAME matcher and only fires where the bounded replace
really would reach a sibling.

DO NOT REGRESS:
  * The preflight's criterion must stay the matcher the rename actually uses.
    `old in s` refuses safe renames; a bare prefix test refuses even more.
  * The guard is NOT dead. A sibling of the form `TM_MOVE_THUNDER` is still
    hit by a bounded rename of `MOVE_THUNDER`, because the leading boundary
    deliberately permits a preceding underscore. There is a test for it.
  * Do NOT "simplify" `_bounded` to a symmetric `\b` or `whole_word=True`.
    That has already been shown to break legitimate derived script-label
    renames and to half-rename maps and graphics rules, and it was proposed
    and rejected twice. See BUGS.md, "Rename proposal RESOLVED".
  * Derived symbols (an ability's sXxxDescription, a trainer's sParty_*) are
    renamed through their own explicit calls and are NOT in a family sibling
    set, so this guard must never block them.
  * `_family_siblings` must scan dict KEYS *and* prefixed string VALUES.
    A keys-only scan found ZERO item siblings — items.json is a LIST of
    records whose constant sits in an `itemId` field — leaving that guard
    inert while its test still passed.
  * A safe rename must still go through. A guard that refuses everything is
    not a repair.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.refactor_service import (  # noqa: E402
    RefactorService, UnsafeRenameError,
)


def _service(tmp_path):
    svc = RefactorService.__new__(RefactorService)

    class _Util:
        def repo_root(self):
            return str(tmp_path)

    svc.util = _Util()
    svc._changed_files = set()
    svc._logger = None
    svc.pending = []
    return svc


MOVES = ["MOVE_THUNDER", "MOVE_THUNDERBOLT", "MOVE_THUNDER_PUNCH",
         "MOVE_THUNDER_WAVE", "MOVE_THUNDER_SHOCK", "MOVE_ABSORB"]


def test_a_prefix_collision_is_now_renamed_correctly_not_refused(tmp_path):
    """THE REPAIR, replacing `test_unsafe_rename_is_refused` (2026-11-08).

    This used to assert the rename was REFUSED, which was the right answer
    while the replace was plain substring. Now it must go through — and the
    assertion that matters is not that it was allowed, it is that the file
    came out correct. All four neighbours are checked.
    """
    svc = _service(tmp_path)
    # no refusal
    svc.collision_preflight("MOVE_THUNDER", "MOVE_STORMCALL", MOVES,
                            kind="move")
    # and the replace does the right thing
    probe = tmp_path / "probe.c"
    probe.write_text(
        "[MOVE_THUNDER] = 1,\n"
        "[MOVE_THUNDERBOLT] = 2,\n"
        "[MOVE_THUNDER_PUNCH] = 3,\n"
        "[MOVE_THUNDER_WAVE] = 4,\n"
        "[MOVE_THUNDER_SHOCK] = 5,\n", encoding="utf-8")
    svc._rename_in_file(str(probe), "MOVE_THUNDER", "MOVE_STORMCALL", [])
    out = probe.read_text(encoding="utf-8")
    assert "[MOVE_STORMCALL] = 1," in out, "the rename did not happen at all"
    for survivor in ("MOVE_THUNDERBOLT", "MOVE_THUNDER_PUNCH",
                     "MOVE_THUNDER_WAVE", "MOVE_THUNDER_SHOCK"):
        assert f"[{survivor}]" in out, f"{survivor} was corrupted"
    assert "STORMCALLBOLT" not in out
    assert "MOVE_STORMCALL_PUNCH" not in out


def test_the_guard_still_refuses_what_the_boundaries_cannot_prevent(tmp_path):
    """The preflight is not dead weight.

    The leading boundary deliberately allows a preceding underscore, so that
    `gMonFrontPic_Paras` and a trainer's `_BULBASAUR` keep renaming. The cost
    is that a sibling of the form `TM_MOVE_THUNDER` really IS rewritten by a
    bounded rename of `MOVE_THUNDER` — so the guard still has to catch it.
    """
    svc = _service(tmp_path)
    siblings = MOVES + ["TM_MOVE_THUNDER"]
    with pytest.raises(UnsafeRenameError) as err:
        svc.collision_preflight("MOVE_THUNDER", "MOVE_STORMCALL", siblings,
                                kind="move")
    assert set(err.value.collisions) == {"TM_MOVE_THUNDER"}, (
        "the guard must name only the siblings a bounded rename really hits")


def test_refusal_message_is_actionable(tmp_path):
    svc = _service(tmp_path)
    with pytest.raises(UnsafeRenameError) as err:
        svc.collision_preflight("MOVE_THUNDER", "MOVE_X",
                                MOVES + ["TM_MOVE_THUNDER"], kind="move")
    msg = str(err.value)
    assert "Nothing has been changed" in msg
    assert "TM_MOVE_THUNDER" in msg


def test_a_prefix_sibling_no_longer_appears_in_any_refusal(tmp_path):
    """The control for the test above: the guard must have stopped naming the
    neighbours it no longer needs to protect, or it is still refusing safe
    renames and merely raising later."""
    svc = _service(tmp_path)
    svc.collision_preflight("MOVE_THUNDER", "MOVE_X", MOVES, kind="move")


def test_safe_rename_is_not_refused(tmp_path):
    """The whole point: a guard that refuses everything is not a repair."""
    svc = _service(tmp_path)
    svc.collision_preflight("MOVE_ABSORB", "MOVE_DRAIN", MOVES, kind="move")


def test_renaming_to_itself_is_not_refused(tmp_path):
    svc = _service(tmp_path)
    svc.collision_preflight("MOVE_THUNDER", "MOVE_THUNDER", MOVES, kind="move")


def test_replacement_still_renames_the_intended_identifier(tmp_path):
    """Containment must not disable the operation it protects."""
    svc = _service(tmp_path)
    probe = tmp_path / "probe.c"
    probe.write_text("[MOVE_SOLO] = 1,\n[MOVE_OTHER] = 2,\n[MOVE_SOLO_X] = 3,\n",
                     encoding="utf-8")
    svc._rename_in_file(str(probe), "MOVE_OTHER", "MOVE_RENAMED", [])
    out = probe.read_text(encoding="utf-8")
    assert "[MOVE_RENAMED] = 2," in out
    assert "[MOVE_SOLO] = 1," in out
    assert "[MOVE_SOLO_X] = 3," in out


def test_family_siblings_reads_keys_and_values(tmp_path):
    """items.json keys its constants in a FIELD, not as dict keys. A
    keys-only scan returned zero and left the item guard inert."""
    data_dir = tmp_path / "src" / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "keyed.json").write_text(
        '{"MOVE_A": {"id": 1}, "MOVE_B": {"id": 2}}', encoding="utf-8")
    (data_dir / "listed.json").write_text(
        '[{"itemId": "ITEM_POTION"}, {"itemId": "ITEM_SUPER_POTION"}]',
        encoding="utf-8")
    svc = _service(tmp_path)
    assert set(svc._family_siblings("src/data/keyed.json", "MOVE_")) == {
        "MOVE_A", "MOVE_B"}
    assert set(svc._family_siblings("src/data/listed.json", "ITEM_")) == {
        "ITEM_POTION", "ITEM_SUPER_POTION"}


def test_missing_data_file_does_not_crash_the_rename(tmp_path):
    svc = _service(tmp_path)
    assert svc._family_siblings("src/data/absent.json", "MOVE_") == []


def test_preflight_tolerates_empty_sibling_set(tmp_path):
    svc = _service(tmp_path)
    svc.collision_preflight("MOVE_A", "MOVE_B", [], kind="move")
    svc.collision_preflight("MOVE_A", "MOVE_B", None, kind="move")


def test_preview_does_not_queue_a_rename(tmp_path):
    """The rename dialog calls this on every keystroke. rename_move was
    missing the `if not preview` guard its three siblings have, so typing a
    ten-character name queued ten pending renames."""
    data_dir = tmp_path / "src" / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "moves.json").write_text('{"MOVE_ABSORB": {"id": 1}}',
                                         encoding="utf-8")
    svc = _service(tmp_path)
    for partial in ("M", "MO", "MOV", "MOVE_N", "MOVE_NE"):
        svc.rename_move("MOVE_ABSORB", "MOVE_" + partial, display_name="x",
                        preview=True)
    assert svc.pending == []


def test_apply_path_does_queue_a_rename(tmp_path):
    data_dir = tmp_path / "src" / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "moves.json").write_text('{"MOVE_ABSORB": {"id": 1}}',
                                         encoding="utf-8")
    svc = _service(tmp_path)
    svc.rename_move("MOVE_ABSORB", "MOVE_DRAIN", display_name="Drain")
    assert len(svc.pending) == 1
    assert svc.pending[0]["op"] == "rename_move"


# ── the matcher itself ──────────────────────────────────────────────────────
#
# Added 2026-11-08 after a mutation run. Dropping the LEADING boundary from
# `_bounded` escaped a suite of 35 tests: every end-to-end case happened to
# involve a token that could not be the SUFFIX of another identifier, so the
# half of the boundary pair that guards KADABRA was never exercised. The
# shared primitive is tested directly here, case by case, because end-to-end
# coverage of a matcher is coverage by coincidence.
#
# Every row below is a case from `_bounded`'s own docstring. If a change makes
# one of these fail, read that docstring before changing the test — each entry
# records a corruption that actually happened in this project.

from core.refactor_service import _bounded  # noqa: E402


@pytest.mark.parametrize("token,text", [
    # exact
    ("MOVE_THUNDER", "[MOVE_THUNDER] = 1,"),
    ("ITEM_TEA", '{"id": ITEM_TEA},'),
    # after an underscore: the leading boundary ALLOWS this on purpose, and a
    # symmetric boundary would stop it and half-rename maps and graphics rules
    ("Paras", "gMonFrontPic_Paras"),
    ("Octo", "const u32 gMonIcon_Octo[] ="),
    ("BULBASAUR", "TRAINER_RIVAL_BULBASAUR"),
    # after a path separator or a dot
    ("octo", "graphics/pokemon/octo/front.png"),
    ("octo", "old_octo.4bpp: %.4bpp"),
])
def test_bounded_matches_a_whole_identifier_component(token, text):
    assert _bounded(token).search(text), (
        f"{token!r} must still rename inside {text!r}; see _bounded's "
        f"docstring for why the leading boundary permits an underscore")


@pytest.mark.parametrize("token,text,why", [
    # TRAILING boundary: a token must never match the PREFIX of a longer name
    ("MOVE_THUNDER", "[MOVE_THUNDERBOLT] = 2,", "PREFIX of a longer constant"),
    ("MOVE_THUNDER", "[MOVE_THUNDER_PUNCH] = 3,", "underscore continues it"),
    ("ITEM_TEA", "[ITEM_TEACHY_TV] = 4,", "the owner's real collision"),
    ("PARAS", "SPECIES_PARASECT", "PARAS->SPARKLING made SPARKLINGECT"),
    ("Octo", "gMonFrontPic_Octorok", "the owner's stated case"),
    ("octo", "graphics/pokemon/octorok/front.png", "slug prefix"),
    ("SPECIES_ARMOS", "SPECIES_ARMOSTITAN", "real, 1 of this project's 24"),
    # LEADING boundary: a token must never match the END of a longer word.
    # This is the half a 35-test suite could not see until these rows existed.
    ("ABRA", "SPECIES_KADABRA", "ABRA->VOLTARIX made KADVOLTARIX"),
    ("ABRA", "KADABRA", "same, unqualified"),
    ("TEA", "ITEM_GREENTEA", "suffix of a longer word"),
    ("Paras", "gParasPokedexText", "renamed by its own explicit token"),
    ("500", "ITEM_X1500", "a numeric token mid-identifier"),
])
def test_bounded_refuses_a_partial_identifier(token, text, why):
    assert not _bounded(token).search(text), (
        f"{token!r} must NOT match inside {text!r} ({why})")


def test_bounded_replacement_does_not_interpret_backslashes():
    r"""A new name is user input. `re.sub` reads `\g<1>` and `\1` in a
    replacement STRING, so the replacement goes through a lambda."""
    from core.refactor_service import _bounded_replace
    out = _bounded_replace("ITEM_OLD", r"ITEM_\g<0>_\1", "[ITEM_OLD] = 1,")
    assert out == r"[ITEM_\g<0>_\1] = 1,"


def test_bounded_is_the_only_matcher_the_sweep_uses():
    """Structural guard: the preview and the write must not drift apart again.

    Both were plain substring; the defect that reached the user was the
    PREVIEW describing changes the save did not make.

    Asked of the PARSE TREE, not of the source text. The first version of this
    test searched the text for "if old in ln" and failed on the comment in
    `_rename_in_file` that QUOTES the old code to explain it -- a text pattern
    over source matching its own documentation.
    """
    import ast
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "core", "refactor_service.py"),
               encoding="utf-8").read()
    tree = ast.parse(src)
    for name in ("_search_and_replace", "_rename_in_file"):
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == name)
        calls = {n.func.id for n in ast.walk(fn)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert "_bounded" in calls or "_bounded_replace" in calls, (
            f"{name} stopped calling the shared matcher")
        # `old in ln` as a real comparison, not as text inside a comment
        substring_tests = [
            n for n in ast.walk(fn)
            if isinstance(n, ast.Compare)
            and any(isinstance(o, ast.In) for o in n.ops)
            and isinstance(n.left, ast.Name) and n.left.id == "old"
        ]
        assert not substring_tests, (
            f"{name} is back to a plain `old in ...` substring test at line "
            f"{[n.lineno for n in substring_tests]}")
