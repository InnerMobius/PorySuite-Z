"""Tests for ``core/battle_mon_ref.py`` — reference-mon sprite resolution."""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

_THIS = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_THIS, ".."))
_PROJECT = os.path.join(_ROOT, "pokefirered")


def _load():
    path = os.path.join(_ROOT, "core", "battle_mon_ref.py")
    spec = importlib.util.spec_from_file_location("battle_mon_ref", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("battle_mon_ref", mod)
    spec.loader.exec_module(mod)
    return mod


mod = _load()


def test_display_name():
    assert mod.mon_display_name("charizard") == "Charizard"
    assert mod.mon_display_name("nidoran_f") == "Nidoran F"
    assert mod.mon_display_name("mr-mime") == "Mr Mime"


def test_missing_tree_returns_empty(tmp_path):
    assert mod.list_mon_sprites(str(tmp_path)) == []


def test_synthetic_tree(tmp_path):
    base = tmp_path / "graphics" / "pokemon" / "testmon"
    base.mkdir(parents=True)
    (base / "front.png").write_bytes(b"\x89PNG\r\n")
    (base / "back.png").write_bytes(b"\x89PNG\r\n")
    mons = mod.list_mon_sprites(str(tmp_path))
    assert mons == [("testmon", str(base))]
    assert mod.mon_sprite_path(str(base), "front").endswith("front.png")
    assert mod.mon_sprite_path(str(base), "back").endswith("back.png")
    # A folder with no front.png is skipped.
    (tmp_path / "graphics" / "pokemon" / "empty").mkdir()
    assert [s for s, _ in mod.list_mon_sprites(str(tmp_path))] == ["testmon"]


@pytest.mark.skipif(not os.path.isdir(_PROJECT), reason="test project absent")
class TestRealProject:
    def test_lists_many_species(self):
        mons = mod.list_mon_sprites(_PROJECT)
        assert len(mons) >= 100, f"only {len(mons)} species folders"

    def test_every_listed_species_resolves(self):
        """Every species the PROJECT defines must resolve to a front sprite.

        This used to name one species (``charizard``) and assert it was in the
        list. That only holds for a stock FireRed — the project this runs
        against is free to replace the whole roster, and does. Naming a vanilla
        species here breaks the standing rule in CLAUDE.md: parse everything
        from the project's own source, never assume vanilla defaults.

        Checking all of them is also a stronger test than checking one.
        """
        mons = mod.list_mon_sprites(_PROJECT)
        assert mons, "no species sprite folders found"
        missing = [slug for slug, d in mons
                   if not os.path.isfile(mod.mon_sprite_path(d, "front"))]
        assert not missing, f"front sprite unresolvable for: {missing[:10]}"

    def test_palettes_resolve_where_a_palette_file_exists(self):
        """A species with a ``normal.gbapal`` must resolve to 16 colours.

        Species without that file are skipped on purpose: ``mon_palette``
        documents ``[]`` as the total-miss result, so demanding 16 everywhere
        would fail on a legitimately palette-less folder.
        """
        mons = mod.list_mon_sprites(_PROJECT)
        assert mons, "no species sprite folders found"
        checked = 0
        wrong = []
        for slug, d in mons:
            if not os.path.isfile(os.path.join(d, "normal.gbapal")):
                continue
            checked += 1
            pal = mod.mon_palette(_PROJECT, slug, d)
            if len(pal) != 16:
                wrong.append((slug, len(pal)))
        assert checked, "no species had a normal.gbapal to check"
        assert not wrong, f"palette not 16 colours for: {wrong[:10]}"
