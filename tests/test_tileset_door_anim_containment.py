"""Finding 0e: a tileset rename moved another tileset's door graphics.

`_rename_door_anim_files` matched filenames in `graphics/door_anims` by three
independent SUBSTRING rules and called `os.rename` on each hit as it found it.
Renaming a tileset called `lab` therefore also moved the door graphics
belonging to `cinnabar_lab` and `oaks_lab`.

The repair plans the whole move set, validates ownership and collisions before
the FIRST rename, and undoes completed moves if one fails.

DO NOT REGRESS:
  * Validation must happen before the tileset FOLDER is renamed. It used to
    run last, after the folder move and six file rewrites, so a refusal there
    left the project half-renamed.
  * Ownership is not a prefix test. A file belongs to another tileset when
    that tileset's name appears in the filename AND contains ours — the more
    specific match wins. `cinnabar_lab_door.png` belongs to `cinnabar_lab`.
  * A legitimate rename must still work. A guard that refuses everything is
    not a repair.
  * Rollback is BEST EFFORT, not atomicity. When the undo itself fails, the
    paths left in the wrong place must be reported, never swallowed.
  * An existing destination must never be overwritten.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eventide.backend.tileset_renamer import (  # noqa: E402
    TilesetRenamer, TilesetRenameRollbackError, UnsafeTilesetRenameError,
)


def _project(tmp_path, tilesets, door_files):
    """A minimal project: a graphics.h the parser can read, plus door anims."""
    gfx_dir = tmp_path / "src" / "data" / "tilesets"
    gfx_dir.mkdir(parents=True)
    lines = []
    for label, folder in tilesets:
        lines.append(
            'const u32 gTilesetTiles_%s[] = INCBIN_U32("data/tilesets/'
            'secondary/%s/tiles.4bpp.lz");' % (label, folder))
    (gfx_dir / "graphics.h").write_text("\n".join(lines), encoding="utf-8")

    door = tmp_path / "graphics" / "door_anims"
    door.mkdir(parents=True)
    for fn in door_files:
        (door / fn).write_text("x", encoding="utf-8")
    return TilesetRenamer(str(tmp_path))


def _door_names(tmp_path):
    return sorted(os.listdir(tmp_path / "graphics" / "door_anims"))


# ── the reported defect ──────────────────────────────────────────────────

def test_rename_does_not_claim_another_tilesets_door_files(tmp_path):
    r = _project(
        tmp_path,
        [("Lab", "lab"), ("CinnabarLab", "cinnabar_lab"),
         ("OaksLab", "oaks_lab")],
        ["lab_door.png", "cinnabar_lab_door.png", "oaks_lab_door.png"],
    )
    before = _door_names(tmp_path)
    with pytest.raises(UnsafeTilesetRenameError) as err:
        r.rename_tileset("Lab", "lab", "Workshop", "workshop")
    msg = str(err.value)
    assert "cinnabar_lab" in msg or "oaks_lab" in msg
    assert "Nothing has been renamed" in msg
    # nothing moved
    assert _door_names(tmp_path) == before


def test_validation_runs_before_the_folder_is_renamed(tmp_path):
    """The refusal must come before ANY write, including the folder move."""
    r = _project(
        tmp_path,
        [("Lab", "lab"), ("CinnabarLab", "cinnabar_lab")],
        ["lab_door.png", "cinnabar_lab_door.png"],
    )
    tiles = tmp_path / "data" / "tilesets" / "secondary" / "lab"
    tiles.mkdir(parents=True)
    with pytest.raises(UnsafeTilesetRenameError):
        r.rename_tileset("Lab", "lab", "Workshop", "workshop")
    assert tiles.is_dir(), "the tileset folder was renamed before validation"
    assert not (tmp_path / "data" / "tilesets" / "secondary" / "workshop").exists()


# ── the guard must not refuse everything ─────────────────────────────────

def test_an_unambiguous_rename_is_allowed(tmp_path):
    r = _project(
        tmp_path,
        [("Forest", "forest"), ("Cave", "cave")],
        ["forest_door.png", "cave_door.png"],
    )
    plan = r.plan_door_anim_renames("Forest", "Grove", "forest", "grove")
    assert r.validate_door_anim_plan(plan, "Forest", "forest") == []
    moved = r._apply_door_anim_plan(plan)
    assert moved == 1
    assert _door_names(tmp_path) == ["cave_door.png", "grove_door.png"]


def test_unrelated_files_are_left_alone(tmp_path):
    r = _project(
        tmp_path, [("Forest", "forest"), ("Cave", "cave")],
        ["forest_door.png", "cave_door.png", "readme.txt"],
    )
    plan = r.plan_door_anim_renames("Forest", "Grove", "forest", "grove")
    assert [s for s, _d in plan] == ["forest_door.png"]


# ── collisions ───────────────────────────────────────────────────────────

def test_existing_destination_is_refused(tmp_path):
    r = _project(
        tmp_path, [("Forest", "forest"), ("Grove", "grove")],
        ["forest_door.png", "grove_door.png"],
    )
    plan = r.plan_door_anim_renames("Forest", "Grove", "forest", "grove")
    reasons = r.validate_door_anim_plan(plan, "Forest", "forest")
    assert any("overwritten" in x for x in reasons), reasons


def test_destination_is_never_overwritten_during_apply(tmp_path):
    r = _project(tmp_path, [("Forest", "forest")],
                 ["forest_door.png", "grove_door.png"])
    with pytest.raises(FileExistsError):
        r._apply_door_anim_plan([("forest_door.png", "grove_door.png")])
    assert _door_names(tmp_path) == ["forest_door.png", "grove_door.png"]


# ── failure injection at each position, with rollback ────────────────────

@pytest.mark.parametrize("fail_at", [0, 1, 2])
def test_a_failed_move_is_rolled_back(tmp_path, monkeypatch, fail_at):
    r = _project(tmp_path, [("Forest", "forest")],
                 ["a_forest.png", "b_forest.png", "c_forest.png"])
    plan = [("a_forest.png", "a_grove.png"),
            ("b_forest.png", "b_grove.png"),
            ("c_forest.png", "c_grove.png")]
    before = _door_names(tmp_path)

    real = os.rename
    calls = {"n": 0}

    def flaky(src, dst):
        if calls["n"] == fail_at:
            calls["n"] += 1
            raise OSError("injected failure")
        calls["n"] += 1
        return real(src, dst)

    monkeypatch.setattr(os, "rename", flaky)
    with pytest.raises(OSError):
        r._apply_door_anim_plan(plan)
    monkeypatch.setattr(os, "rename", real)
    assert _door_names(tmp_path) == before, "rollback left files moved"


def test_a_failed_rollback_reports_the_stranded_paths(tmp_path, monkeypatch):
    """When the undo itself fails, say exactly what is in the wrong place."""
    r = _project(tmp_path, [("Forest", "forest")],
                 ["a_forest.png", "b_forest.png"])
    plan = [("a_forest.png", "a_grove.png"), ("b_forest.png", "b_grove.png")]

    real = os.rename
    state = {"moves": 0}

    def flaky(src, dst):
        # first move succeeds, second fails, and the undo also fails
        if state["moves"] == 0:
            state["moves"] += 1
            return real(src, dst)
        raise OSError("injected failure")

    monkeypatch.setattr(os, "rename", flaky)
    with pytest.raises(TilesetRenameRollbackError) as err:
        r._apply_door_anim_plan(plan)
    monkeypatch.setattr(os, "rename", real)
    assert err.value.unrecovered == [("a_grove.png", "a_forest.png")]
    assert "NOT where the project expects them" in str(err.value)


def test_missing_door_dir_is_not_an_error(tmp_path):
    gfx = tmp_path / "src" / "data" / "tilesets"
    gfx.mkdir(parents=True)
    (gfx / "graphics.h").write_text("", encoding="utf-8")
    r = TilesetRenamer(str(tmp_path))
    assert r.plan_door_anim_renames("A", "B", "a", "b") == []
