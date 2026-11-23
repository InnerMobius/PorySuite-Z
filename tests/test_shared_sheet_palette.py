"""Regression guard: species on a SHARED sprite sheet must still get their palette.

THE BUG (reported 2026-11-22, the exported Full Dex Sheet): a species whose
graphics mapping points at a shared sheet — pokefirered keeps several species
on ``graphics/pokemon/question_mark/circled`` and ``.../question_mark/double``
— has NO ``graphics/pokemon/<slug>/`` folder of its own. The sprite PNG path
resolves correctly through the graphics mapping, but
``SpritePaletteBus.ensure_pokemon_palette`` built the ``.pal`` path from the
species SLUG alone, found nothing at ``graphics/pokemon/flyingfish/normal.pal``
and returned ``[]`` — so the dex export (and every other viewer using the
slug-only lookup) rendered the sprite with whatever colour table the PNG
happened to carry: black-and-white.

WORSE: FlyingFish's slug folder EXISTS (a placeholder copy of the circled
question mark, black-and-white palette included), so a mere "fall back when
the slug path misses" would never fire. The palette paired with a sprite is
the one in the sprite's OWN folder — the exact rule
``get_species_shiny_image_path`` and the game's palette table already use.

THE RULE: ``ensure_pokemon_palette`` takes ``sprite_png_path``; the ``.pal``
next to that PNG WINS whenever it exists, the slug path is the fallback, and
the RAM cache (unsaved Graphics-tab edits) wins over both. A palette read
from a folder other than the species' own is NOT cached — the per-species
cache would let a shared sheet's (or a form's) palette shadow the species'
own palette for every later caller.

DO NOT REGRESS:
  * Never seed the per-species cache from a foreign folder's .pal.
  * Every renderer that has the sprite path in hand must pass it
    (dex export, starter card, Info tab header, Pokédex detail panel,
    battle reference strip).
  * No species-name hardcoding: the fixture species here are invented.
"""

import os
import shutil
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "core"), os.path.join(_ROOT, "ui")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QImage, qRgb  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

APP = QApplication.instance() or QApplication(sys.argv)

from core.sprite_palette_bus import get_bus  # noqa: E402


# ── fixture plumbing ─────────────────────────────────────────────────────────

# The right colours, written to the .pal files. Slot 0 is background.
PAL_COLORS = [(255, 0, 255)] + [(10 * i, 200, 30) for i in range(1, 16)]
# A deliberately WRONG colour table baked into the PNG itself: all grey.
BAKED_GREY = [(i * 16, i * 16, i * 16) for i in range(16)]
# A species' OWN palette, distinct from the shared one.
OWN_COLORS = [(0, 0, 0)] + [(5, 5, 10 * i) for i in range(1, 16)]


def _write_jasc(path, colors):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("JASC-PAL\n0100\n%d\n" % len(colors))
        for r, g, b in colors:
            fh.write("%d %d %d\n" % (r, g, b))


def _write_indexed_png(path, size=64):
    """A 4bpp-style indexed PNG filled with colour index 1, grey table baked."""
    img = QImage(size, size, QImage.Format.Format_Indexed8)
    img.setColorTable([qRgb(*c) for c in BAKED_GREY])
    img.fill(1)
    assert img.save(path, "PNG")


def _make_project(root):
    """graphics/pokemon with one shared sheet and one own-folder species."""
    shared = os.path.join(root, "graphics", "pokemon", "shared_sheet", "double")
    os.makedirs(shared)
    _write_indexed_png(os.path.join(shared, "front.png"))
    _write_indexed_png(os.path.join(shared, "back.png"))
    _write_jasc(os.path.join(shared, "normal.pal"), PAL_COLORS)
    _write_jasc(os.path.join(shared, "shiny.pal"), list(reversed(PAL_COLORS)))

    own = os.path.join(root, "graphics", "pokemon", "ownmon")
    os.makedirs(own)
    _write_indexed_png(os.path.join(own, "front.png"))
    _write_jasc(os.path.join(own, "normal.pal"), OWN_COLORS)
    return shared, own


class _Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="shared_sheet_pal_")
        self.shared, self.own = _make_project(self.root)
        get_bus().clear()

    def tearDown(self):
        get_bus().clear()
        shutil.rmtree(self.root, ignore_errors=True)


# ── the bus itself ───────────────────────────────────────────────────────────

class EnsurePokemonPaletteSharedSheet(_Base):

    def test_shared_sheet_species_resolves_from_the_sprite_folder(self):
        """The FlyingFish shape: no slug folder, pal next to the sprite."""
        pal = get_bus().ensure_pokemon_palette(
            self.root, "SPECIES_SHAREDFISH", "normal",
            sprite_png_path=os.path.join(self.shared, "front.png"))
        self.assertEqual(pal, PAL_COLORS)

    def test_shiny_follows_the_same_sprite_folder_rule(self):
        pal = get_bus().ensure_pokemon_palette(
            self.root, "SPECIES_SHAREDFISH", "shiny",
            sprite_png_path=os.path.join(self.shared, "front.png"))
        self.assertEqual(pal, list(reversed(PAL_COLORS)))

    def test_the_sprite_folder_beats_a_stale_slug_folder(self):
        """The real FlyingFish shape: the slug folder EXISTS but holds a
        black-and-white placeholder palette, while the rendered sprite
        lives on a shared sheet with the right .pal beside it."""
        stale = os.path.join(self.root, "graphics", "pokemon", "stalefish")
        os.makedirs(stale)
        _write_indexed_png(os.path.join(stale, "front.png"))
        _write_jasc(os.path.join(stale, "normal.pal"),
                    [(i * 16, i * 16, i * 16) for i in range(16)])
        pal = get_bus().ensure_pokemon_palette(
            self.root, "SPECIES_STALEFISH", "normal",
            sprite_png_path=os.path.join(self.shared, "front.png"))
        self.assertEqual(pal, PAL_COLORS)

    def test_a_sprite_in_the_species_own_folder_behaves_as_before(self):
        pal = get_bus().ensure_pokemon_palette(
            self.root, "SPECIES_OWNMON", "normal",
            sprite_png_path=os.path.join(self.own, "front.png"))
        self.assertEqual(pal, OWN_COLORS)

    def test_without_a_sprite_path_a_slugless_species_still_misses_quietly(self):
        pal = get_bus().ensure_pokemon_palette(
            self.root, "SPECIES_SHAREDFISH", "normal")
        self.assertEqual(pal, [])

    def test_a_missing_pal_next_to_the_sprite_is_an_empty_list_not_a_crash(self):
        bare = os.path.join(self.root, "graphics", "pokemon", "bare")
        os.makedirs(bare)
        _write_indexed_png(os.path.join(bare, "front.png"))
        os.rename(os.path.join(bare, "front.png"),
                  os.path.join(bare, "front_only.png"))
        pal = get_bus().ensure_pokemon_palette(
            self.root, "SPECIES_NOPAL", "normal",
            sprite_png_path=os.path.join(bare, "front_only.png"))
        self.assertEqual(pal, [])

    def test_a_foreign_folder_palette_never_seeds_the_species_cache(self):
        """A shared sheet's palette must not shadow the species' own: after
        a shared-sheet render, a slug-only lookup still sees the slug truth
        (here: nothing), and an own-folder species keeps its own colours."""
        get_bus().ensure_pokemon_palette(
            self.root, "SPECIES_SHAREDFISH", "normal",
            sprite_png_path=os.path.join(self.shared, "front.png"))
        self.assertEqual(
            get_bus().get_pokemon_palette("SPECIES_SHAREDFISH", "normal"),
            None)
        get_bus().ensure_pokemon_palette(
            self.root, "SPECIES_OWNMON", "normal",
            sprite_png_path=os.path.join(self.shared, "front.png"))
        self.assertEqual(
            get_bus().ensure_pokemon_palette(self.root, "SPECIES_OWNMON",
                                             "normal"),
            OWN_COLORS)

    def test_an_own_folder_read_still_hydrates_the_cache(self):
        get_bus().ensure_pokemon_palette(
            self.root, "SPECIES_OWNMON", "normal",
            sprite_png_path=os.path.join(self.own, "front.png"))
        self.assertEqual(
            get_bus().get_pokemon_palette("SPECIES_OWNMON", "normal"),
            OWN_COLORS)

    def test_ram_edits_beat_every_disk_path(self):
        edited = [(1, 2, 3)] * 16
        get_bus().set_pokemon_palette("SPECIES_SHAREDFISH", "normal", edited)
        pal = get_bus().ensure_pokemon_palette(
            self.root, "SPECIES_SHAREDFISH", "normal",
            sprite_png_path=os.path.join(self.shared, "front.png"))
        self.assertEqual(pal, edited)


# ── the reported symptom: the exported dex sheet ─────────────────────────────

class _StubSource:
    """get_species_image_path pointing a species at the shared sheet."""

    def __init__(self, paths):
        self._paths = paths

    def get_species_image_path(self, species, key, form=None):
        return self._paths.get((species, key))


class FullDexSheetSharedSheet(_Base):

    def setUp(self):
        super().setUp()
        # The exact real shape: the species ALSO has a slug folder holding a
        # stale black-and-white placeholder palette. It must lose.
        stale = os.path.join(self.root, "graphics", "pokemon", "sharedfish")
        os.makedirs(stale)
        _write_jasc(os.path.join(stale, "normal.pal"), BAKED_GREY)

    def _pixmaps(self, entry):
        from core.fulldex_sheet import _species_pixmaps
        src = _StubSource({
            ("SPECIES_SHAREDFISH", "frontPic"):
                os.path.join(self.shared, "front.png"),
            ("SPECIES_SHAREDFISH", "backPic"):
                os.path.join(self.shared, "back.png"),
        })
        return _species_pixmaps(src, self.root, entry)

    def test_the_export_renders_shared_sheet_species_in_palette_colours(self):
        front, back, _icon = self._pixmaps({"species": "SPECIES_SHAREDFISH"})
        for name, pm in (("front", front), ("back", back)):
            self.assertIsNotNone(pm, name)
            img = pm.toImage()
            c = img.pixelColor(5, 5)
            got = (c.red(), c.green(), c.blue())
            # The sprite is filled with colour index 1.
            self.assertEqual(got, PAL_COLORS[1],
                             "%s pic must use the .pal colours, not the "
                             "grey table baked into the PNG" % name)
            self.assertNotEqual(got, BAKED_GREY[1])


if __name__ == "__main__":
    unittest.main()
