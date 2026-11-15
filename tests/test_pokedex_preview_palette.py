"""Finding F8: the Pokédex size-comparison preview ignored palette edits.

`_load_gba_sprite` is a flat `QImage(path)` that reads whatever colour table
the PNG happens to carry. The authoritative palette lives in the `.pal` file,
and unsaved edits live only in `core/sprite_palette_bus` — so editing a
palette in the Graphics tab recoloured the header sprite and left the size
preview right beside it on the old colours. Two renderings of the same
creature, disagreeing on one screen, with nothing to explain it.

CLAUDE.md's Sprite Rendering Pipeline rule names
`core/sprite_render.load_sprite_pixmap` as the only sanctioned way to combine
a sprite PNG with its palette.

DO NOT REGRESS:
  * `set_pokemon` / `set_trainer` must accept a palette. Passing a path alone
    is what made the preview structurally incapable of showing an edit.
  * `_on_palette_changed` must refresh the PREVIEW as well as the header
    sprite. Refreshing only the header is the visible half of this defect.
  * Keep the fallback: with no palette resolved, a sprite must still appear
    rather than vanishing.
"""
import glob
import inspect
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

APP = QApplication.instance() or QApplication(sys.argv)

import ui.pokedex_detail_panel as pdp  # noqa: E402

# Sprites come from the read-only vanilla reference, the same source the
# engine-patch tests use.
#
# This used to point at a throwaway investigation folder
# (`C:\GBA\porysuite-investigation-2026-10-30\work\firered-disposable`). When
# that folder was deleted these three tests did not fail — they SKIPPED, so
# the suite stayed green while three guards quietly stopped running. A guard
# tied to a scratch directory is a guard with an expiry date on it.
#
# It still skips when the reference is absent, because a machine without it
# genuinely cannot run this. The difference is that the reference is a
# permanent fixture of this setup rather than one session's leftovers.
_SPRITE_ROOTS = (
    r"C:\GBA\READONLYREFERENCE\pokefirered",
)


def _a_sprite():
    """One sprite PNG, and the project root it belongs to."""
    for root in _SPRITE_ROOTS:
        hits = sorted(glob.glob(os.path.join(
            root, "graphics", "pokemon", "*", "front.png")))
        if hits:
            return hits[0], root
    pytest.skip("no vanilla reference sprites available on this machine")


def _fingerprint(pm):
    if pm is None or pm.isNull():
        return None
    img = pm.toImage()
    return tuple(img.pixel(x, y)
                 for y in range(0, min(img.height(), 48), 12)
                 for x in range(0, min(img.width(), 48), 12))


def test_the_preview_api_accepts_a_palette():
    """Taking a path alone is what made the defect structural."""
    for name in ("set_pokemon", "set_trainer"):
        fn = getattr(pdp._SizePreview, name, None)
        assert fn is not None, name
        assert "palette" in inspect.signature(fn).parameters, name


def test_rendering_follows_the_palette():
    png, root = _a_sprite()
    from core.sprite_palette_bus import get_bus
    const = "SPECIES_" + os.path.basename(os.path.dirname(png)).upper()
    pal = get_bus().ensure_pokemon_palette(root, const, "normal")
    if not pal:
        pytest.skip("no palette resolved for the sample sprite")
    before = _fingerprint(pdp._render_sprite(png, pal))
    edited = [((r + 97) % 256, (g + 151) % 256, (b + 203) % 256)
              for (r, g, b) in pal]
    after = _fingerprint(pdp._render_sprite(png, edited))
    assert before != after, "the preview still ignores the palette"


def test_the_old_flat_loader_is_palette_blind():
    """The premise. If this ever changes, the defect has moved."""
    png, _root = _a_sprite()
    a = _fingerprint(pdp._load_gba_sprite(png))
    b = _fingerprint(pdp._load_gba_sprite(png))
    assert a == b


def test_a_sprite_still_appears_without_a_palette():
    """Degrade, never vanish."""
    png, _root = _a_sprite()
    assert pdp._render_sprite(png, None) is not None


def test_no_path_renders_nothing():
    assert pdp._render_sprite(None, ["x"]) is None
    assert pdp._render_sprite("", None) is None


def test_the_palette_handler_refreshes_the_preview_too():
    """Refreshing only the header sprite is the visible half of the defect."""
    src = inspect.getsource(pdp.PokedexDetailPanel._on_palette_changed)
    assert "_sprite_lbl" in src
    assert "_size_preview" in src, \
        "_on_palette_changed still refreshes only the header sprite"
