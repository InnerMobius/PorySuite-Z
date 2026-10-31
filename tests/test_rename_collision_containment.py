"""C1 containment: a rename must not corrupt other identifiers.

`core/refactor_service.py::_rename_in_file` applies a PLAIN SUBSTRING replace
(`ln.replace(old, new)`), so renaming an identifier that occurs inside another
live identifier rewrites that one too. Measured in this project: renaming
MOVE_THUNDER rewrote MOVE_THUNDERBOLT, MOVE_THUNDER_PUNCH, MOVE_THUNDER_WAVE
and MOVE_THUNDER_SHOCK — 0 of 4 neighbours survived.

`collision_preflight()` refuses such a rename BEFORE anything is read, queued
or written.

DO NOT REGRESS:
  * The criterion is the operation's ACTUAL replacement rule — another live
    identifier CONTAINS the old name — not merely "two names share a prefix".
    A prefix test would refuse plenty of safe renames.
  * Do NOT replace this with a global `whole_word=True` in `_rename_in_file`.
    That has already been shown to break legitimate derived script-label
    renames. The guard is deliberately operation-level.
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


def test_unsafe_rename_is_refused(tmp_path):
    svc = _service(tmp_path)
    with pytest.raises(UnsafeRenameError) as err:
        svc.collision_preflight("MOVE_THUNDER", "MOVE_STORMCALL", MOVES,
                                kind="move")
    assert set(err.value.collisions) == {
        "MOVE_THUNDERBOLT", "MOVE_THUNDER_PUNCH",
        "MOVE_THUNDER_WAVE", "MOVE_THUNDER_SHOCK",
    }


def test_refusal_message_is_actionable(tmp_path):
    svc = _service(tmp_path)
    with pytest.raises(UnsafeRenameError) as err:
        svc.collision_preflight("MOVE_THUNDER", "MOVE_X", MOVES, kind="move")
    msg = str(err.value)
    assert "Nothing has been changed" in msg
    assert "MOVE_THUNDERBOLT" in msg


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
