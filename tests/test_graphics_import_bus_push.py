"""Regression guard: every palette-import path must push to the SpritePaletteBus.

THE BUG (found 2026-10-29, fixed 2026-10-29). Six of the nine palette-mutation
paths on the Graphics tab broadcast the new colours on the shared
``core.sprite_palette_bus``; three did not — all three being the IMPORT paths
(`_import_sprite_set`, `_do_import_palette_from_png`, `_import_palette_from_pal`).
So importing a palette recoloured the Graphics tab while the Pokedex panel
sprite, the species-tree icons, the starter previews and the Info-tab front
sprite kept the OLD colours until a save plus a reload. Two views of the same
Pokemon disagreed.

CLAUDE.md's Sprite Rendering Pipeline rule names this case: "If you wire a new
way for the user to edit a palette (palette reorder, swatch edit, import, BG
swap), call the matching bus.set_*_palette() on that code path."

This test asserts the CONTRACT (the module calls the bus) rather than driving
the Qt dialogs, because the import paths open file pickers. If you add another
palette-mutation path, add it here too.
"""

import os
import re
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "core"), os.path.join(_ROOT, "ui")):
    sys.path.insert(0, _p)

import pytest  # noqa: E402

SRC = os.path.join(_ROOT, "ui", "graphics_tab_widget.py")

#: Every method that writes into ``self._palettes`` and therefore owes the bus
#: a push. Name -> how many ``set_pokemon_palette`` calls it must make.
MUTATORS = {
    "_on_normal_changed": 1,
    "_on_shiny_changed": 1,
    "_on_palette_reordered": 1,
    "_on_set_swatch_as_bg": 2,          # normal AND shiny are swapped together
    "_try_import_shiny_from_sprite_png": 1,
    "_import_sprite_set": 2,            # normal AND shiny come from the set
    "_do_import_palette_from_png": 1,
    "_import_palette_from_pal": 1,
}


def _method_bodies(path):
    """Split a class file into {method_name: body_text} by def indentation."""
    with open(path, encoding="utf-8") as fh:
        lines = fh.readlines()
    starts = []
    for i, ln in enumerate(lines):
        m = re.match(r"^    def (\w+)\s*\(", ln)
        if m:
            starts.append((i, m.group(1)))
    bodies = {}
    for n, (i, name) in enumerate(starts):
        end = starts[n + 1][0] if n + 1 < len(starts) else len(lines)
        bodies[name] = "".join(lines[i:end])
    return bodies


@pytest.fixture(scope="module")
def bodies():
    return _method_bodies(SRC)


@pytest.mark.parametrize("name,expected", sorted(MUTATORS.items()))
def test_palette_mutator_pushes_to_the_bus(bodies, name, expected):
    assert name in bodies, f"{name} no longer exists in graphics_tab_widget.py"
    body = bodies[name]
    n = len(re.findall(r"set_pokemon_palette\s*\(", body))
    assert n >= expected, (
        f"{name} mutates the palette but makes {n} bus push(es); "
        f"at least {expected} required. Other tabs will show stale colours."
    )


def test_the_three_import_paths_specifically_push(bodies):
    """The three that were missing. Named separately so a failure is obvious."""
    for name in ("_import_sprite_set", "_do_import_palette_from_png",
                 "_import_palette_from_pal"):
        body = bodies[name]
        assert "set_pokemon_palette" in body, (
            f"{name} is an IMPORT path and must push to the bus — this is the "
            f"exact defect fixed on 2026-10-29."
        )


def test_bus_really_has_the_method_being_called():
    """Guard against the push being a typo that silently does nothing."""
    from core.sprite_palette_bus import get_bus
    bus = get_bus()
    assert hasattr(bus, "set_pokemon_palette")
    assert callable(bus.set_pokemon_palette)


def test_bus_round_trip_and_signal():
    """A push must be readable back and must notify subscribers."""
    from core.sprite_palette_bus import get_bus, CAT_POKEMON
    bus = get_bus()
    seen = []
    bus.palette_changed.connect(lambda cat, key: seen.append((cat, key)))
    pal = [(1, 2, 3)] * 16
    bus.set_pokemon_palette("SPECIES_TEST_BUS", "normal", pal)
    assert bus.get_pokemon_palette("SPECIES_TEST_BUS", "normal") == pal
    assert (CAT_POKEMON, "SPECIES_TEST_BUS:normal") in seen, (
        f"palette_changed did not fire as expected; saw {seen}"
    )
