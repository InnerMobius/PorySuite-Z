"""A palette change must NEVER re-stamp itself onto the back sprite's existing
pixels.

THE BUG (reported 2026-11-22, fixed 2026-11-22). ``flush_to_disk``'s
``_normal_pal_dirty`` loop baked the species' normal palette into BOTH
``front.png`` and ``back.png``, keeping each file's existing pixel indices and
swapping the colour table underneath. Those back-sprite indices were drawn
against the OLD palette ORDER. Importing a new front sprite gives the species a
NEW order, so every back pixel then pointed at the wrong colour and the file was
overwritten with the garbled result — destroying the original art on disk.

The user's words: "it doesn't fucking take into account the back sprites data
and fucks it up like this when it was that by importing a new front." Their
repair route is "Import Normal + Shiny Set", which re-matches the back onto the
new palette — but only while an intact ``back.png`` still exists to work from.

THE RULE NOW:
* The back is written when its PIXELS were imported (staged in ``_sprite_imgs``).
  That is how "Import Normal + Shiny Set" writes it, so the repair route still
  works.
* The back is NOT written when only the palette changed. The file on disk keeps
  its real colours.
* The FRONT is unchanged — it is still re-baked from disk on a palette edit,
  because that is where the newly imported palette belongs.

Note for whoever reads this next: the game reads the ``.pal``, not the PNG's
embedded table. So after a front-only palette import the back will still render
wrong IN GAME until a matching back is imported. That is inherent — front and
back share one palette — and it is exactly why the PNG must survive intact.
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "core"), os.path.join(_ROOT, "ui")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

from PyQt6.QtGui import QImage  # noqa: E402

from core.gba_image_utils import export_indexed_png  # noqa: E402
from ui.graphics_tab_widget import GraphicsTabWidget  # noqa: E402


SPECIES = "SPECIES_TEST_BACKBAKE"

#: The order the back sprite's pixels were drawn against.
OLD_PAL = [(0, 0, 0), (200, 20, 20), (20, 200, 20), (20, 20, 200)] + \
          [(8 * i, 8 * i, 8 * i) for i in range(4, 16)]
#: A differently-ORDERED palette, as a front-sprite import produces.
NEW_PAL = [(0, 0, 0), (20, 20, 200), (200, 20, 20), (20, 200, 20)] + \
          [(8 * i, 8 * i, 8 * i) for i in range(4, 16)]


class _Stub:
    """Enough of GraphicsTabWidget for flush_to_disk's palette loop, with only
    the normal palette dirty — the exact state a front-sprite import leaves."""

    def __init__(self, tmp, front, back, staged_back=None):
        self._data = None
        self._project_root = tmp
        self._current_species = SPECIES
        self._palettes = {SPECIES: {"normal": list(NEW_PAL), "shiny": []}}
        self._sprite_paths = {SPECIES: {"front": front, "back": back}}
        self._sprite_imgs = {SPECIES: {}}
        if staged_back is not None:
            self._sprite_imgs[SPECIES]["back"] = staged_back
        self._icon_palettes = {}
        self._icon_import_src = {}
        self._pal_folder = {}
        self._normal_pal_dirty = {SPECIES}
        self._shiny_pal_dirty = set()
        self._sprite_png_dirty = set()
        self._icon_png_dirty = set()
        self._footprint_png_dirty = set()
        self._icon_pal_dirty = set()


for _n in ("_pal_paths_for",):
    _f = getattr(GraphicsTabWidget, _n, None)
    if _f is not None:
        setattr(_Stub, _n, _f)


def _sprite(palette, seed=0):
    """A 64x64 indexed sprite whose pixels use the first four palette slots."""
    img = QImage(64, 64, QImage.Format.Format_Indexed8)
    img.setColorCount(16)
    for i, (r, g, b) in enumerate(palette):
        img.setColor(i, 0xFF000000 | (r << 16) | (g << 8) | b)
    for y in range(64):
        for x in range(64):
            img.setPixel(x, y, (x + y + seed) % 4)
    return img


@pytest.fixture()
def sprites(tmp_path):
    front = str(tmp_path / "front.png")
    back = str(tmp_path / "back.png")
    assert export_indexed_png(_sprite(OLD_PAL, 0), OLD_PAL, front,
                              transparent_index=0)
    assert export_indexed_png(_sprite(OLD_PAL, 1), OLD_PAL, back,
                              transparent_index=0)
    return str(tmp_path), front, back


def test_palette_change_leaves_back_png_untouched(sprites):
    """The whole point: a palette-only save must not rewrite back.png."""
    tmp, front, back = sprites
    before = open(back, "rb").read()

    ok, errors = GraphicsTabWidget.flush_to_disk(_Stub(tmp, front, back))

    assert not errors, f"save reported errors: {errors}"
    assert open(back, "rb").read() == before, (
        "back.png was rewritten by a palette-only save — the 2026-11-22 "
        "regression that destroyed the user's back sprites")


def test_the_back_keeps_its_real_colours(sprites):
    """Stated as colours rather than bytes, so the failure reads plainly."""
    tmp, front, back = sprites

    GraphicsTabWidget.flush_to_disk(_Stub(tmp, front, back))

    table = QImage(back).colorTable()[:4]
    got = [(c >> 16 & 0xFF, c >> 8 & 0xFF, c & 0xFF) for c in table]
    assert got == OLD_PAL[:4], (
        f"back.png now carries {got}, the NEW palette order. Its pixels were "
        f"drawn against {OLD_PAL[:4]}, so every one of them is now the wrong "
        f"colour")


def test_the_front_is_still_rebaked(sprites):
    """The front SHOULD take the new palette — that is where it belongs. If
    this ever fails, the fix was applied too widely."""
    tmp, front, back = sprites

    GraphicsTabWidget.flush_to_disk(_Stub(tmp, front, back))

    table = QImage(front).colorTable()[:4]
    got = [(c >> 16 & 0xFF, c >> 8 & 0xFF, c & 0xFF) for c in table]
    assert got == NEW_PAL[:4], (
        f"front.png carries {got}, not the new palette {NEW_PAL[:4]} — the "
        f"front must still be baked on a palette change")


def test_an_imported_back_is_still_written(sprites):
    """"Import Normal + Shiny Set" stages the back's PIXELS. That must still
    reach disk, or the user loses the route they repair a back sprite with."""
    tmp, front, back = sprites
    before = open(back, "rb").read()

    # Pixels re-matched onto the NEW order, as build_sprite_set produces.
    staged = _sprite(NEW_PAL, 7)
    ok, errors = GraphicsTabWidget.flush_to_disk(
        _Stub(tmp, front, back, staged_back=staged))

    assert not errors, f"save reported errors: {errors}"
    assert open(back, "rb").read() != before, (
        "an IMPORTED back sprite was not written — Import Normal + Shiny Set "
        "can no longer repair a back sprite")
    table = QImage(back).colorTable()[:4]
    got = [(c >> 16 & 0xFF, c >> 8 & 0xFF, c & 0xFF) for c in table]
    assert got == NEW_PAL[:4], (
        "an imported back should be written with the new palette it was "
        "matched against")
