"""A cached pokedex.json with no regional model counts as out of date.

Found by the Stage 4 end-to-end run, not by a unit test: every earlier test
handed the extractor a header and watched it parse. The real app does not
always get that far. ``instantiate_extractor`` loads ``src/data/pokedex.json``
and only calls ``extract_data`` when ``should_extract()`` says so, and the base
check is purely timestamps. A cache written before the regional dex existed as
a model therefore looked perfectly fresh while carrying no ``hoenn_order`` --
so the save path had no authoritative regional dex and declined to write one.
The tab looked editable and saved nothing, silently, on every existing project.

The fix is one override: a cache missing the model is stale by definition.

DO NOT regress: `PokedexDataExtractor.should_extract` must keep reporting a
cache with no (or an empty) ``hoenn_order`` as needing extraction. Deleting it
restores the silent-no-save behaviour, which no amount of unit coverage of
``hoenn_dex`` will reveal.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.test_hoenn_regional_dex import ALL, HEADER, POKEMON_C  # noqa: E402


def _project(tmp_path, cache: dict | str | None):
    """A minimal project tree. Returns its project_info."""
    (tmp_path / "include" / "constants").mkdir(parents=True)
    (tmp_path / "src" / "data").mkdir(parents=True)
    (tmp_path / "include" / "constants" / "pokedex.h").write_text(
        HEADER, encoding="utf-8"
    )
    (tmp_path / "src" / "pokemon.c").write_text(POKEMON_C, encoding="utf-8")
    if cache is not None:
        raw = cache if isinstance(cache, str) else json.dumps(cache)
        (tmp_path / "src" / "data" / "pokedex.json").write_text(
            raw, encoding="utf-8"
        )
    return {
        "dir": str(tmp_path),
        "project_name": "fixture",
        "source_prefix": "",
    }


def _extractor(project_info):
    from core.pokemon_data_extractor import PokedexDataExtractor

    # files={} on purpose: with no tracked files the base timestamp check is
    # satisfied by a cache merely existing, so what this measures is the
    # override's own decision and nothing else.
    return PokedexDataExtractor(project_info, "pokedex.json", {})


NATIONAL_ONLY = {
    "national_dex": [{"dex_constant": f"NATIONAL_DEX_{n}"} for n in ALL],
    "regional_dex": [{"dex_constant": f"NATIONAL_DEX_{ALL[0]}"}],
}

def _with_model(**over):
    from core.pokemon_data_extractor import PokedexDataExtractor
    base = dict(
        NATIONAL_ONLY,
        hoenn_order=[{"hoenn_constant": f"HOENN_DEX_{n}"} for n in ALL],
        regional_count=4,
        cache_format=PokedexDataExtractor.CACHE_FORMAT,
    )
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# The staleness decision
# ---------------------------------------------------------------------------

def test_a_cache_without_the_model_is_stale(tmp_path):
    ex = _extractor(_project(tmp_path, NATIONAL_ONLY))
    assert ex.should_extract() is True


def test_a_cache_with_the_model_is_left_alone(tmp_path):
    """The counterpart. Without this the override could just return True."""
    ex = _extractor(_project(tmp_path, _with_model()))
    assert ex.should_extract() is False


def test_the_model_alone_is_not_enough_without_the_format_stamp(tmp_path):
    """Added 2026-11-02 with ``CACHE_FORMAT``.

    Having ``hoenn_order`` says the cache knows about the regional dex. It says
    nothing about whether the parser that wrote the ENTRIES could be trusted --
    and the cache that triggered this carried a good regional model beside 77
    creatures with no data at all. See tests/test_pokedex_cache_refresh.py.
    """
    stale = _with_model()
    del stale["cache_format"]
    ex = _extractor(_project(tmp_path, stale))
    assert ex.should_extract() is True


def test_an_empty_model_counts_as_no_model(tmp_path):
    cache = _with_model(hoenn_order=[], regional_count=0)
    ex = _extractor(_project(tmp_path, cache))
    assert ex.should_extract() is True


def test_unreadable_cache_is_stale(tmp_path):
    ex = _extractor(_project(tmp_path, "{ this is not json"))
    assert ex.should_extract() is True


def test_a_cache_that_is_not_an_object_is_stale(tmp_path):
    ex = _extractor(_project(tmp_path, "[1, 2, 3]"))
    assert ex.should_extract() is True


def test_a_missing_cache_is_stale(tmp_path):
    ex = _extractor(_project(tmp_path, None))
    assert ex.should_extract() is True


# ---------------------------------------------------------------------------
# The consequence that actually bit: construction must hydrate the model
# ---------------------------------------------------------------------------

@pytest.fixture
def pokedex_class():
    pytest.importorskip("PyQt6.QtCore")
    from core.pokemon_data import Pokedex

    return Pokedex


def test_opening_a_project_with_an_old_cache_hydrates_the_model(
    tmp_path, pokedex_class
):
    """The bug in one assertion: the model has to be there after a load.

    Without it ``_apply_hoenn_dex`` finds no ``hoenn_order`` and every regional
    save is a no-op, on every project whose cache predates this work.
    """
    info = _project(tmp_path, NATIONAL_ONLY)
    dex = pokedex_class(info, None)
    order = [
        e["hoenn_constant"] if isinstance(e, dict) else e
        for e in dex.data.get("hoenn_order") or []
    ]
    assert order == [f"HOENN_DEX_{n}" for n in ALL]
    assert dex.data.get("regional_count") == 4


def test_the_stale_derived_list_is_replaced_not_kept_alongside(
    tmp_path, pokedex_class
):
    """The old 'regional_dex' slice must stop being a second mutable list."""
    info = _project(tmp_path, NATIONAL_ONLY)
    dex = pokedex_class(info, None)
    regional = dex.data.get("regional_dex") or []
    assert len(regional) == 4, "regional_dex is still the old national slice"
    assert [e.get("hoenn_constant") for e in regional] == [
        f"HOENN_DEX_{n}" for n in ALL[:4]
    ]


def test_a_hydrated_project_saves_the_regional_dex(tmp_path, pokedex_class):
    """End of the chain: an old-cache project can now write the header."""
    info = _project(tmp_path, NATIONAL_ONLY)
    dex = pokedex_class(info, None)
    header = tmp_path / "include" / "constants" / "pokedex.h"

    before = header.read_text(encoding="utf-8")
    dex.parse_to_c_code()
    assert header.read_text(encoding="utf-8") == before, "no-op save churned it"

    # move the boundary by one and confirm it reaches the file
    dex.data["regional_count"] = 5
    dex.parse_to_c_code()
    after = header.read_text(encoding="utf-8")
    assert f"#define HOENN_DEX_COUNT HOENN_DEX_{ALL[4]}" in after
    assert f"#define HOENN_DEX_COUNT HOENN_DEX_{ALL[3]}" not in after
