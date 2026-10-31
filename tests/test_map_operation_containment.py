"""Finding item 0: map rename and delete corrupted OTHER maps.

Both operations end in `replace_repo_wide` with PLAIN SUBSTRING pairs, so a
map whose name occurs inside another map's name was rewritten too.

Delete was the worse of the two. It passed ``(map_folder, '')`` — an EMPTY
replacement — which `replace_repo_wide`'s own docstring forbids outright
("never pass an empty `new` here at all"). Measured against the checkpoint
code, deleting a map called ``Route1``:

    data/maps/Route10/map.bin   ->  data/maps/0/map.bin
    Route11_Text                ->  1_Text
    EventScript_Route1_Sign::   ->  EventScript__Sign::

DO NOT REGRESS:
  * Never reinstate a blanket ``(name, '')`` replacement. It does not delete a
    reference, it deletes that text from every file in the project.
  * The MAP_ constant must be repointed as a WHOLE IDENTIFIER, or deleting
    MAP_ROUTE1 rewrites the middle of MAP_ROUTE10.
  * Both operations must check BEFORE the first write. Delete used to remove
    the map folder first, so a late refusal could not undo it.
  * A conflict is another live map whose name CONTAINS ours. Sharing a prefix
    is not in itself a conflict.
  * Refusals are verified here by FILE HASH, not merely by the exception.
  * A guard that refuses every rename or delete is not a repair — the safe
    cases below must keep working.
"""
import hashlib
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eventide.backend.map_renamer import (  # noqa: E402
    MapRenamer, UnsafeMapOperationError,
)


def _project(tmp_path, maps):
    """A minimal project with the given map folders in one group."""
    maps_dir = tmp_path / "data" / "maps"
    maps_dir.mkdir(parents=True)
    (tmp_path / "data" / "layouts").mkdir(parents=True)
    for folder in maps:
        d = maps_dir / folder
        d.mkdir()
        snake = folder.upper()
        (d / "map.json").write_text(
            json.dumps({"id": "MAP_" + snake, "name": folder}), encoding="utf-8")
        (d / "scripts.inc").write_text(
            "EventScript_%s_Sign::\n\tend\n" % folder, encoding="utf-8")
    (maps_dir / "map_groups.json").write_text(
        json.dumps({"group_order": ["gMapGroup_Main"],
                    "gMapGroup_Main": list(maps)}), encoding="utf-8")
    r = MapRenamer(str(tmp_path))
    # `_generate_headers()` shells out to `make -C tools/mapjson`, an external
    # build step that has nothing to do with the containment being tested and
    # does not exist in this fixture. Stubbing ONLY that call keeps every
    # assertion about file content intact — nothing about the operation under
    # test is weakened.
    r._generate_headers = lambda: None
    return r


def _digest(root):
    h = hashlib.sha256()
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d != ".git")
        for fn in sorted(files):
            p = os.path.join(dirpath, fn)
            h.update(os.path.relpath(p, root).encode())
            try:
                with open(p, "rb") as f:
                    h.update(f.read())
            except OSError:
                pass
    return h.hexdigest()


# ── the reported defect: overlapping names ───────────────────────────────

def test_rename_is_refused_when_another_map_contains_the_name(tmp_path):
    r = _project(tmp_path, ["Route1", "Route10", "Route11"])
    before = _digest(tmp_path)
    with pytest.raises(UnsafeMapOperationError) as err:
        r.rename_map("gMapGroup_Main", "Route1", new_folder="Coast")
    assert "Route10" in str(err.value) or "Route11" in str(err.value)
    assert "Nothing has been changed" in str(err.value)
    assert _digest(tmp_path) == before, "files changed despite the refusal"


def test_delete_is_refused_when_another_map_contains_the_name(tmp_path):
    r = _project(tmp_path, ["Route1", "Route10"])
    before = _digest(tmp_path)
    with pytest.raises(UnsafeMapOperationError):
        r.delete_map("gMapGroup_Main", "Route1")
    assert _digest(tmp_path) == before
    # the map folder must still be there — delete used to remove it first
    assert (tmp_path / "data" / "maps" / "Route1").is_dir()


def test_refusal_happens_before_the_folder_is_removed(tmp_path):
    r = _project(tmp_path, ["Route1", "Route10"])
    scripts = tmp_path / "data" / "maps" / "Route10" / "scripts.inc"
    original = scripts.read_text(encoding="utf-8")
    with pytest.raises(UnsafeMapOperationError):
        r.delete_map("gMapGroup_Main", "Route1")
    assert scripts.read_text(encoding="utf-8") == original


# ── the guard must not refuse everything ─────────────────────────────────

def test_a_uniquely_named_map_can_still_be_renamed(tmp_path):
    r = _project(tmp_path, ["Meadow", "Cavern"])
    r.rename_map("gMapGroup_Main", "Meadow", new_folder="Glade")
    assert (tmp_path / "data" / "maps" / "Glade").is_dir()
    assert not (tmp_path / "data" / "maps" / "Meadow").exists()
    assert (tmp_path / "data" / "maps" / "Cavern").is_dir()


def test_a_uniquely_named_map_can_still_be_deleted(tmp_path):
    r = _project(tmp_path, ["Meadow", "Cavern"])
    r.delete_map("gMapGroup_Main", "Meadow")
    assert not (tmp_path / "data" / "maps" / "Meadow").exists()
    assert (tmp_path / "data" / "maps" / "Cavern").is_dir()


def test_deleting_a_map_does_not_blank_the_word_everywhere(tmp_path):
    """The empty-replacement defect, asserted directly.

    Another map's file mentions the target's name inside LONGER identifiers.
    The old code deleted that text everywhere; the repair refuses, and the
    neighbouring file must come through byte-identical."""
    r = _project(tmp_path, ["Meadow", "Cavern"])
    other = tmp_path / "data" / "maps" / "Cavern" / "scripts.inc"
    original = (
        "EventScript_Cavern_Sign::\n"
        '\t.incbin "data/maps/Meadowlands/map.bin"\n'
        "\tmsgbox MeadowKeeper_Text\n")
    other.write_text(original, encoding="utf-8")
    with pytest.raises(UnsafeMapOperationError):
        r.delete_map("gMapGroup_Main", "Meadow")
    survived = other.read_text(encoding="utf-8")
    assert survived == original
    assert "Meadowlands" in survived
    assert "MeadowKeeper_Text" in survived


def test_delete_refuses_while_any_reference_remains(tmp_path):
    """Fail closed. Deleting first and reporting dangling references after is
    the finding-0j shape: the reference stops the project building, and by
    then the map is already gone."""
    r = _project(tmp_path, ["Meadow", "Cavern"])
    other = tmp_path / "data" / "maps" / "Cavern" / "scripts.inc"
    other.write_text("goto EventScript_Meadow_Sign\n", encoding="utf-8")
    before = _digest(tmp_path)
    with pytest.raises(UnsafeMapOperationError) as err:
        r.delete_map("gMapGroup_Main", "Meadow")
    assert "Cavern" in str(err.value)
    assert (tmp_path / "data" / "maps" / "Meadow").is_dir()
    assert _digest(tmp_path) == before


def test_delete_succeeds_when_nothing_else_refers_to_the_map(tmp_path):
    """The guard must not make deletion impossible."""
    r = _project(tmp_path, ["Meadow", "Cavern"])
    r.delete_map("gMapGroup_Main", "Meadow")
    assert not (tmp_path / "data" / "maps" / "Meadow").exists()
    assert (tmp_path / "data" / "maps" / "Cavern").is_dir()
    assert r.last_delete_leftovers == []


# ── whole-identifier constant handling ───────────────────────────────────

def test_map_constant_is_repointed_as_a_whole_identifier(tmp_path):
    r = _project(tmp_path, ["Meadow", "Cavern"])
    probe = tmp_path / "data" / "heal_locations.json"
    probe.write_text("MAP_MEADOW MAP_MEADOWSIDE\n", encoding="utf-8")
    r.delete_map("gMapGroup_Main", "Meadow")
    out = probe.read_text(encoding="utf-8")
    assert "MAP_UNDEFINED" in out
    assert "MAP_MEADOWSIDE" in out, "a longer constant was rewritten"


# ── conflict detection itself ────────────────────────────────────────────

def test_shared_prefix_alone_is_not_a_conflict(tmp_path):
    """Route1 and Route2 share a prefix but neither contains the other."""
    r = _project(tmp_path, ["Route1", "Route2"])
    assert r.map_operation_conflicts("Route1", "MAP_ROUTE1") == []


def test_containment_names_the_conflicting_map(tmp_path):
    r = _project(tmp_path, ["Route1", "Route10"])
    reasons = r.map_operation_conflicts("Route1", "MAP_ROUTE1")
    assert reasons
    assert any("Route10" in x for x in reasons)
