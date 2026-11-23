"""Switching a species between the three shared mini/icon palettes and saving
must rewrite that species' ``icon.png`` with the colours of the palette it now
points at.

THE BUG (found 2026-11-22, fixed 2026-11-22). The icon loop inside
``GraphicsTabWidget.flush_to_disk`` calls ``_remapped_imported_icon`` and then
``_reindex_existing_icon``. Both methods were deleted by the 2026-10-22
dead-code sweep (commit 181629b) while this caller was still live, so the loop
raised ``AttributeError`` on every save that had an icon pending. ``MainWindow``
wraps the whole flush in ``except Exception: logging.exception(...)``, so the
save reported success, ``icon.png`` kept its old colours, and the footprint
writes that come AFTER the icon loop never ran either.

This test drives the real ``flush_to_disk`` (called unbound on a minimal stub,
the pattern the other save-path tests in here use) against a real indexed PNG
on disk, and reads the file back to check its colour table actually changed.
It fails with ``AttributeError`` against the broken code.
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


SPECIES = "SPECIES_TEST_MINI"

#: Three deliberately different shared icon palettes. Slot 0 is the
#: transparent background in every one, as the engine expects.
PAL_0 = [(0, 0, 0)] + [(8 * i, 0, 0) for i in range(1, 16)]
PAL_1 = [(0, 0, 0)] + [(0, 8 * i, 0) for i in range(1, 16)]
PAL_2 = [(0, 0, 0)] + [(0, 0, 8 * i) for i in range(1, 16)]


class _Combo:
    """Just the one method flush_to_disk's icon path reads off the combo."""

    def __init__(self, idx):
        self._idx = idx

    def currentIndex(self):
        return self._idx


#: The real, shipping helper methods the icon save path runs through. They are
#: attached to the stub below rather than re-implemented, so this test exercises
#: the code the app runs. ``getattr(..., None)`` keeps a MISSING method a clean
#: test failure inside flush_to_disk instead of a collection error.
_REAL_METHODS = ("_icon_target_slot", "_remapped_imported_icon",
                 "_reindex_existing_icon", "_refresh_imported_icon_preview")


class _Stub:
    """The smallest object GraphicsTabWidget.flush_to_disk can run against
    with ONLY an icon pending. Every other dirty set is empty, so the other
    loops are no-ops and nothing else on disk is touched."""

    def __init__(self, icon_path, project_root, slot):
        self._data = None
        self._project_root = project_root
        self._current_species = SPECIES
        self._icon_pal_combo = _Combo(slot)
        self._icon_palettes = {0: list(PAL_0), 1: list(PAL_1), 2: list(PAL_2)}
        self._sprite_paths = {SPECIES: {"icon": icon_path}}
        self._sprite_imgs = {}
        self._palettes = {}
        self._icon_import_src = {}
        # Only the icon is dirty.
        self._icon_png_dirty = {SPECIES}
        self._normal_pal_dirty = set()
        self._shiny_pal_dirty = set()
        self._sprite_png_dirty = set()
        self._footprint_png_dirty = set()
        self._icon_pal_dirty = set()


for _name in _REAL_METHODS:
    _fn = getattr(GraphicsTabWidget, _name, None)
    if _fn is not None:
        setattr(_Stub, _name, _fn)


def _write_icon(path, palette):
    """Write a 32x64 two-frame indexed icon sheet using `palette`."""
    img = QImage(32, 64, QImage.Format.Format_Indexed8)
    img.setColorCount(16)
    for i, (r, g, b) in enumerate(palette):
        img.setColor(i, 0xFF000000 | (r << 16) | (g << 8) | b)
    for y in range(64):
        for x in range(32):
            # A spread of indices so a palette swap is visible in the table,
            # and index 0 round the border so slot-0 transparency is exercised.
            edge = x in (0, 31) or y in (0, 63)
            img.setPixel(x, y, 0 if edge else 1 + ((x + y) % 15))
    assert export_indexed_png(img, palette, path, transparent_index=0), (
        "test setup could not write the starting icon.png")


def _color_table(path):
    img = QImage(path)
    assert not img.isNull(), f"could not read back {path}"
    return [(c >> 16 & 0xFF, c >> 8 & 0xFF, c & 0xFF) for c in img.colorTable()]


def test_icon_palette_switch_is_written_into_icon_png(tmp_path):
    """Pick palette 2 for a species whose icon.png is baked with palette 0,
    save, and the PNG on disk must come back carrying palette 2's colours."""
    icon = str(tmp_path / "icon.png")
    _write_icon(icon, PAL_0)

    before = _color_table(icon)
    assert before[1][0] > 0 and before[1][2] == 0, (
        "setup sanity: the starting icon should be baked with the RED palette")

    stub = _Stub(icon, str(tmp_path), slot=2)
    ok, errors = GraphicsTabWidget.flush_to_disk(stub)

    assert not errors, f"save reported errors: {errors}"
    assert ok >= 1, "save claimed it wrote nothing"

    after = _color_table(icon)
    assert after != before, (
        "icon.png was not rewritten — switching the shared icon palette and "
        "saving left the old colours on disk (the 2026-11-22 regression)")
    # Palette 2 is the blue ramp: every non-background entry is pure blue.
    for i in range(1, 16):
        r, g, b = after[i]
        assert (r, g) == (0, 0) and b > 0, (
            f"icon.png entry {i} is {after[i]}, which is not palette 2's "
            f"blue ramp — the wrong shared palette was baked in")

    # A successful write must clear the pending flag, so the next save does
    # not rewrite the same file again.
    assert SPECIES not in stub._icon_png_dirty


def test_icon_save_leaves_the_flag_set_when_the_write_is_refused(tmp_path):
    """A refused write must KEEP the species dirty — the 2026-10-29 rule that
    a failed write never silently discards a pending edit."""
    icon = str(tmp_path / "icon.png")
    _write_icon(icon, PAL_0)
    before = open(icon, "rb").read()

    stub = _Stub(icon, str(tmp_path), slot=2)
    # A palette with fewer entries than the icon's highest pixel index is
    # refused by export_indexed_png rather than written as garbage.
    stub._icon_palettes[2] = [(0, 0, 0), (255, 255, 255)]

    ok, errors = GraphicsTabWidget.flush_to_disk(stub)

    assert errors, "a refused icon write must be reported, not swallowed"
    assert SPECIES in stub._icon_png_dirty, (
        "a refused write dropped the pending icon edit")
    assert open(icon, "rb").read() == before, (
        "a refused write must leave the file on disk byte-identical")


def test_pending_import_is_fitted_to_the_chosen_shared_palette(tmp_path):
    """With a freshly imported icon still in memory, the save fits that raw
    artwork onto the shared palette the combo points at."""
    icon = str(tmp_path / "icon.png")
    _write_icon(icon, PAL_0)

    # A true-colour source, as QFileDialog would hand over from a PNG.
    src = QImage(32, 64, QImage.Format.Format_ARGB32)
    src.fill(0xFF0000FF)        # solid blue artwork

    stub = _Stub(icon, str(tmp_path), slot=1)
    stub._icon_import_src = {SPECIES: src}

    ok, errors = GraphicsTabWidget.flush_to_disk(stub)

    assert not errors, f"save reported errors: {errors}"
    after = _color_table(icon)
    # Palette 1 is the green ramp.
    for i in range(1, 16):
        r, g, b = after[i]
        assert (r, b) == (0, 0) and g > 0, (
            f"icon.png entry {i} is {after[i]}, not palette 1's green ramp")
    assert SPECIES not in stub._icon_import_src, (
        "a written import should be cleared from the pending-import cache")
