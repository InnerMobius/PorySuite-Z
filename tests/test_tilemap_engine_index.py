"""The Tilemap Editor must work on ANY project, not the one it was written in.

The engine index reads a project's own C source to learn each tilemap's width,
cell size, tile sheet and palette slots. Every one of those facts has to come
from the project in front of it — a tilemap that never existed in vanilla, a
checkout folder named anything at all, a palette destination written in a style
this project does not happen to use — and when the source says nothing, the
editor has to fall back quietly rather than break.

That is what these tests pin down. They build throwaway game trees in temp; no
real project is read or written.

An earlier version of the index rebuilt the game directory as
`<parent>/pokefirered`, which silently returned NOTHING for any checkout named
anything else. `test_renamed_checkout_is_found` exists so that cannot come back.
"""

import os
import shutil
import struct
import sys
import tempfile
import unittest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
# core/local_env.py is imported bare by the extractors that core/__init__ pulls.
_CORE_DIR = os.path.join(ROOT_DIR, "core")
if _CORE_DIR not in sys.path:
    sys.path.insert(1, _CORE_DIR)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

def _real_qt():
    """The genuine PyQt6, or None.

    Several modules in this suite install hand-built PyQt6 stubs into
    `sys.modules` so they can import GUI code without a Qt runtime. Under a
    shared-process runner those stubs leak into whatever imports PyQt6 next, and
    these tests need real `QImage` to write and read indexed PNGs. So rather
    than trusting the import, actually exercise the object: a stub will not
    survive being constructed and having a colour table set.
    """
    try:
        from PyQt6.QtGui import QImage, QGuiApplication, qRgb
        app = QGuiApplication.instance() or QGuiApplication([])
        probe = QImage(2, 2, QImage.Format.Format_Indexed8)
        probe.setColorTable([qRgb(1, 2, 3), qRgb(4, 5, 6)])
        probe.setPixel(0, 0, 1)
        if probe.pixelIndex(0, 0) != 1:
            return None
        return app
    except Exception:
        return None


_QT = _real_qt()
if _QT is not None:
    from PyQt6.QtGui import QImage, QGuiApplication, qRgb  # noqa: F401,E402


JASC_16 = ("JASC-PAL\n0100\n16\n"
           + "\n".join(f"{i * 16} {i * 8} {248 - i * 16}" for i in range(16))
           + "\n")

# A project's own menu tilemap: 24x6 u16 cells, every cell naming palette slot 3.
_PANEL_CELLS = [(i % 40) | (3 << 12) for i in range(24 * 6)]
PANEL_BIN = struct.pack(f"<{len(_PANEL_CELLS)}H", *_PANEL_CELLS)
# A byte-per-cell map of the project's own invention: 20x4.
ROWS_BIN = bytes((i * 3) % 40 for i in range(20 * 4))
# A tilemap on disk that no C file mentions.
ORPHAN_BIN = struct.pack("<64H", *[i % 30 for i in range(64)])

# Note the two palette destinations: the macro form AND a raw colour offset,
# because both appear across forks and vintages of this engine.
PROJECT_SOURCE = """
#define ZMENU_W  24
#define ZMENU_H  6

static const u16 sZeldaMenuTilemap[ZMENU_W * ZMENU_H] = INCBIN_U16("graphics/zmenu/panel.bin");
static const u32 sZeldaMenuGfx[] = INCBIN_U32("graphics/zmenu/panel.4bpp.lz");
static const u16 sZeldaMenuPal[] = INCBIN_U16("graphics/zmenu/panel.gbapal");

static const u8 sZeldaPackedRows[] = INCBIN_U8("graphics/zmenu/rows.bin");

static const u16 sZeldaAccentPal[] = INCBIN_U16("graphics/zmenu/accent.gbapal");

void ZeldaMenu_Init(void)
{
    LoadPalette(sZeldaMenuPal, BG_PLTT_ID(0), PLTT_SIZE_4BPP);
    LoadPalette(sZeldaAccentPal, 0x30, PLTT_SIZE_4BPP);
    DecompressAndLoadBgGfxUsingHeap(1, sZeldaMenuGfx, 0, 0, 0);
    CopyToBgTilemapBuffer(1, sZeldaMenuTilemap, 0, 0);
}
"""


def _write_indexed_png(path, w, h, ncolors):
    img = QImage(w, h, QImage.Format.Format_Indexed8)
    img.setColorTable([qRgb(i * 3 % 256, i * 5 % 256, i * 7 % 256)
                       for i in range(ncolors)])
    for y in range(h):
        for x in range(w):
            img.setPixel(x, y, (x + y) % ncolors)
    img.save(path)


@unittest.skipUnless(_QT is not None, "PyQt6 unavailable")
class ForeignProjectTests(unittest.TestCase):
    """A game tree that is not this project, in a folder not named after one."""

    CHECKOUT = "hyrule_engine"

    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix="tmidx_")
        cls.game = os.path.join(cls.root, cls.CHECKOUT)
        os.makedirs(os.path.join(cls.game, "src"))
        with open(os.path.join(cls.game, "src", "zmenu.c"), "w",
                  encoding="utf-8") as fh:
            fh.write(PROJECT_SOURCE)
        gfx = os.path.join(cls.game, "graphics", "zmenu")
        os.makedirs(gfx)
        for name, data in (("panel.bin", PANEL_BIN), ("rows.bin", ROWS_BIN),
                           ("orphan.bin", ORPHAN_BIN)):
            with open(os.path.join(gfx, name), "wb") as fh:
                fh.write(data)
        _write_indexed_png(os.path.join(gfx, "panel.png"), 64, 48, 16)
        # Deliberately the biggest and widest image in the folder: the old
        # filename/size heuristics would reach for this one.
        _write_indexed_png(os.path.join(gfx, "unrelated_big.png"), 256, 256, 16)
        for name in ("panel.pal", "accent.pal"):
            with open(os.path.join(gfx, name), "w", encoding="utf-8") as fh:
                fh.write(JASC_16)
        cls.panel = os.path.join(gfx, "panel.bin")
        cls.rows = os.path.join(gfx, "rows.bin")
        cls.orphan = os.path.join(gfx, "orphan.bin")

    @classmethod
    def tearDownClass(cls):
        from core.tilemap_engine_index import clear_engine_index
        clear_engine_index()
        shutil.rmtree(cls.root, ignore_errors=True)

    def setUp(self):
        from core.tilemap_engine_index import clear_engine_index
        clear_engine_index()

    # -- the regression this class exists for -------------------------------

    def test_renamed_checkout_is_found(self):
        """The game tree is located by having a src/ with C in it, never by name."""
        from core.tilemap_engine_index import infer_game_dir
        self.assertEqual(os.path.normcase(infer_game_dir(self.panel)),
                         os.path.normcase(self.game))

    def test_tilemap_absent_from_vanilla_is_indexed(self):
        from core.tilemap_engine_index import facts_for_path
        self.assertIsNotNone(facts_for_path(self.panel))

    # -- facts come from the project's own source ---------------------------

    def test_dimensions_from_the_projects_own_defines(self):
        from core.tilemap_engine_index import facts_for_path
        self.assertEqual(facts_for_path(self.panel).declared_dims, (24, 6))

    def test_width_source_is_reported_as_engine(self):
        from core.tilemap_data import Tilemap
        tm = Tilemap.from_file(self.panel)
        self.assertEqual((tm.width, tm.height, tm.width_source),
                         (24, 6, "engine"))

    def test_sheet_comes_from_the_declaration_not_the_filesystem(self):
        from core.tilemap_data import discover_assets
        assets = discover_assets(self.panel)
        self.assertEqual(os.path.basename(assets.best_sheet), "panel.png")

    def test_palette_slot_from_macro_form(self):
        from core.tilemap_engine_index import facts_for_path
        self.assertEqual(facts_for_path(self.panel).slot_palettes.get(0),
                         "graphics/zmenu/panel.gbapal")

    def test_palette_slot_from_raw_colour_offset(self):
        """`LoadPalette(x, 0x30, ...)` means slot 3 just as `BG_PLTT_ID(3)` does."""
        from core.tilemap_engine_index import facts_for_path
        self.assertEqual(facts_for_path(self.panel).slot_palettes.get(3),
                         "graphics/zmenu/accent.gbapal")

    def test_engine_named_slot_is_loaded_so_nothing_is_unresolved(self):
        from core.tilemap_data import (Tilemap, TileSheet, build_palette_set,
                                       discover_assets, unresolved_slots)
        assets = discover_assets(self.panel)
        sheet = TileSheet.from_file(assets.best_sheet)
        self.assertEqual(
            unresolved_slots(Tilemap.from_file(self.panel),
                             build_palette_set(assets, sheet)),
            [])

    # -- a cell size the project invented -----------------------------------

    def test_projects_own_byte_per_cell_map(self):
        from core.tilemap_data import Tilemap
        tm = Tilemap.from_file(self.rows)
        self.assertEqual((tm.cell_bytes, len(tm.entries)), (1, 80))

    def test_byte_per_cell_map_round_trips_byte_identical(self):
        """Saving must honour the cell size, or every such file doubles."""
        from core.tilemap_data import Tilemap
        out_dir = tempfile.mkdtemp(prefix="tmrt_")
        try:
            out = os.path.join(out_dir, "rows.bin")
            Tilemap.from_file(self.rows).save(out)
            with open(out, "rb") as fh:
                self.assertEqual(fh.read(), ROWS_BIN)
        finally:
            shutil.rmtree(out_dir, ignore_errors=True)

    # -- degrade quietly when the source says nothing -----------------------

    def test_unreferenced_tilemap_has_no_facts_and_still_loads(self):
        from core.tilemap_data import Tilemap, discover_assets
        from core.tilemap_engine_index import facts_for_path
        self.assertIsNone(facts_for_path(self.orphan))
        tm = Tilemap.from_file(self.orphan)
        self.assertEqual((len(tm.entries), tm.width_source), (64, "guess"))
        self.assertTrue(discover_assets(self.orphan).best_sheet)


@unittest.skipUnless(_QT is not None, "PyQt6 unavailable")
class NoEngineSourceTests(unittest.TestCase):
    """A folder of graphics with no C source at all still has to open."""

    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix="tmbare_")
        gfx = os.path.join(cls.root, "graphics", "x")
        os.makedirs(gfx)
        with open(os.path.join(gfx, "y.bin"), "wb") as fh:
            fh.write(ORPHAN_BIN)
        _write_indexed_png(os.path.join(gfx, "y.png"), 64, 64, 16)
        cls.bin_path = os.path.join(gfx, "y.bin")

    @classmethod
    def tearDownClass(cls):
        from core.tilemap_engine_index import clear_engine_index
        clear_engine_index()
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_no_game_dir_inferred(self):
        from core.tilemap_engine_index import infer_game_dir
        self.assertEqual(infer_game_dir(self.bin_path), "")

    def test_no_facts_but_no_exception(self):
        from core.tilemap_engine_index import facts_for_path
        self.assertIsNone(facts_for_path(self.bin_path))

    def test_tilemap_and_sheet_still_resolve_by_name(self):
        from core.tilemap_data import Tilemap, discover_assets
        self.assertEqual(len(Tilemap.from_file(self.bin_path).entries), 64)
        self.assertEqual(
            os.path.basename(discover_assets(self.bin_path).best_sheet), "y.png")


@unittest.skipUnless(_QT is not None, "PyQt6 unavailable")
class SiblingsAndAncestorsTests(unittest.TestCase):
    """The trainer-card shape: one unbroken block, and the sheet in another file.

    A real screen can declare its tilemaps, its sheets and a dozen palette
    VARIANTS as one run with no blank line between them, while the sheet those
    tilemaps actually use lives in a different source file entirely. Grouping by
    blank-line block gets this comprehensively wrong — it hands every tilemap the
    block's first sheet and all of its palettes — and cannot reach another file
    at all.

    What resolves it is the relationship between the NAMES. A sheet called
    `CardMenu` is an ANCESTOR of a tilemap called `CardMenuBack`, so it is its
    sheet. A sheet called `CardMenuBadges` is a SIBLING, so it is not. And the
    palettes here are all siblings of each other with no ancestor among them,
    which is the honest answer when the engine picks one of them at runtime.
    """

    # Deliberately: Back/Badges share the leading characters "Ba", so anything
    # comparing characters rather than name components pairs them up.
    OTHER_FILE = """
static const u32 gCardMenu_Gfx[] = INCBIN_U32("graphics/cardmenu/tiles.4bpp.lz");
"""
    ONE_BLOCK = """
static const u32 sCardMenuBadges_Gfx[] = INCBIN_U32("graphics/cardmenu/badges.4bpp.lz");
static const u32 sCardMenuBack_Tilemap[] = INCBIN_U32("graphics/cardmenu/back.bin.lz");
static const u32 sCardMenuFront_Tilemap[] = INCBIN_U32("graphics/cardmenu/front.bin.lz");
static const u16 sCardMenuBronze_Pal[] = INCBIN_U16("graphics/cardmenu/bronze.gbapal");
static const u16 sCardMenuSilver_Pal[] = INCBIN_U16("graphics/cardmenu/silver.gbapal");
static const u16 sCardMenuGold_Pal[] = INCBIN_U16("graphics/cardmenu/gold.gbapal");
"""

    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix="tmsib_")
        cls.game = os.path.join(cls.root, "some_engine")
        os.makedirs(os.path.join(cls.game, "src"))
        for name, text in (("graphics.c", cls.OTHER_FILE),
                           ("cardmenu.c", cls.ONE_BLOCK)):
            with open(os.path.join(cls.game, "src", name), "w",
                      encoding="utf-8") as fh:
                fh.write(text)
        gfx = os.path.join(cls.game, "graphics", "cardmenu")
        os.makedirs(gfx)
        cells = struct.pack("<600H", *[(i % 150) for i in range(600)])
        for name in ("back.bin", "front.bin"):
            with open(os.path.join(gfx, name), "wb") as fh:
                fh.write(cells)
        _write_indexed_png(os.path.join(gfx, "tiles.png"), 128, 96, 16)
        _write_indexed_png(os.path.join(gfx, "badges.png"), 32, 32, 16)
        for name in ("bronze.pal", "silver.pal", "gold.pal"):
            with open(os.path.join(gfx, name), "w", encoding="utf-8") as fh:
                fh.write(JASC_16)
        cls.back = os.path.join(gfx, "back.bin")

    @classmethod
    def tearDownClass(cls):
        from core.tilemap_engine_index import clear_engine_index
        clear_engine_index()
        shutil.rmtree(cls.root, ignore_errors=True)

    def setUp(self):
        from core.tilemap_engine_index import clear_engine_index
        clear_engine_index()

    def test_sheet_comes_from_the_other_file_not_the_block(self):
        from core.tilemap_engine_index import facts_for_path
        self.assertEqual(facts_for_path(self.back).sheet_rel,
                         "graphics/cardmenu/tiles.png")

    def test_sibling_sheet_sharing_leading_letters_is_not_chosen(self):
        """`Back` and `Badges` both begin "Ba"; only name components separate them."""
        from core.tilemap_engine_index import facts_for_path
        self.assertNotIn("badges", facts_for_path(self.back).sheet_rel)

    def test_sibling_palette_variants_are_not_all_adopted(self):
        """Three colour variants, none an ancestor: the editor must not pick one,
        and must certainly not load all three into slots 0, 1 and 2."""
        from core.tilemap_engine_index import facts_for_path
        self.assertEqual(facts_for_path(self.back).palette_rels, [])

    def test_variants_stay_offered_to_the_user(self):
        from core.tilemap_data import discover_assets
        names = {os.path.basename(p) for p in discover_assets(self.back).pal_files}
        self.assertTrue({"bronze.pal", "silver.pal", "gold.pal"} <= names)


@unittest.skipUnless(_QT is not None, "PyQt6 unavailable")
class ColourDepthTests(unittest.TestCase):
    """`is_8bpp` must test the artwork's pixel values.

    Not the colour table's length (storage padding — a 4bpp sheet can carry a
    256-entry table) and not the compiled asset's name (a sheet can compile to
    .4bpp while storing baked sub-palettes above index 15). Both were tried in
    the real code and both were wrong, in opposite directions.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tmbpp_")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _sheet(self, ncolors, max_index):
        """An indexed PNG whose highest pixel index is exactly `max_index`.

        The colour table must NOT be grey: Qt writes a grey indexed image out as
        a grayscale PNG, which reloads as Format_Grayscale8 rather than
        Format_Indexed8 and so is not an indexed sheet at all. Real tile sheets
        are colourful, so this keeps the fixture honest rather than testing a
        quirk of the fixture.
        """
        path = os.path.join(self.root, f"s{ncolors}_{max_index}.png")
        img = QImage(16, 16, QImage.Format.Format_Indexed8)
        img.setColorTable([qRgb(i * 7 % 256, i * 13 % 256, i * 29 % 256)
                           for i in range(ncolors)])
        img.fill(0)
        # Place max_index explicitly so the fixture cannot quietly fall short.
        img.setPixel(0, 0, max_index)
        for y in range(16):
            for x in range(1, 16):
                img.setPixel(x, y, (x + y) % (max_index + 1))
        img.save(path)
        from core.tilemap_data import TileSheet
        sheet = TileSheet.from_file(path)
        self.assertEqual(sheet.image.format(), QImage.Format.Format_Indexed8,
                         "fixture stopped being an indexed PNG")
        return sheet

    def test_padded_table_with_low_indices_is_4bpp(self):
        self.assertFalse(self._sheet(ncolors=256, max_index=15).is_8bpp)

    def test_indices_above_15_are_8bpp(self):
        self.assertTrue(self._sheet(ncolors=256, max_index=63).is_8bpp)

    def test_small_table_small_indices_is_4bpp(self):
        self.assertFalse(self._sheet(ncolors=16, max_index=15).is_8bpp)


if __name__ == "__main__":
    unittest.main()
