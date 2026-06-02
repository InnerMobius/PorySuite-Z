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

    def test_known_species_resolves(self):
        mons = dict(mod.list_mon_sprites(_PROJECT))
        assert "charizard" in mons
        d = mons["charizard"]
        assert os.path.isfile(mod.mon_sprite_path(d, "front"))
        pal = mod.mon_palette(_PROJECT, "charizard", d)
        assert len(pal) == 16
