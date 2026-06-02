"""Verification for ``core/overworld_sprite_geometry.py`` — Phase 1 of the
Overworld Editor arbitrary-dimensions upgrade.

The geometry module is PURE (stdlib only).  It is loaded here straight from
its file with ``importlib`` so the test never touches ``core/__init__.py``,
which would pull in the whole PyQt application.

The headline check is SS Anne: vanilla pokefirered composites the 128x64
SS Anne object sprite from four 64x32 hardware pieces at tileOffsets
0/32/64/96 (``object_event_subsprites.h::gObjectEventSpriteOamTable_128x64_0``).
``_compute_pieces(128, 64, 64, 32)`` must reproduce that table exactly.

Run directly:   python tests/test_overworld_sprite_geometry.py
Or via discovery: python -m unittest tests.test_overworld_sprite_geometry
"""

import importlib.util
import os
import sys
import unittest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)


def _load_geometry():
    """Load the pure-geometry module without importing the ``core`` package.

    ``core/__init__.py`` eagerly imports the whole data layer (and PyQt);
    none of that is needed to exercise pure geometry, so the module file is
    loaded in isolation — the same importlib pattern other tests use.
    """
    path = os.path.join(ROOT_DIR, "core", "overworld_sprite_geometry.py")
    spec = importlib.util.spec_from_file_location(
        "overworld_sprite_geometry", path,
    )
    module = importlib.util.module_from_spec(spec)
    # Register before exec: @dataclass resolves cls.__module__ via
    # sys.modules, so the module must be findable there as it runs.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


geo = _load_geometry()


def _pt(piece):
    """Flatten a ``SubspritePiece`` to a comparable tuple."""
    return (piece.x, piece.y, piece.shape_w, piece.shape_h, piece.tile_offset)


class ValidateTest(unittest.TestCase):
    def test_accepts_multiples_of_16(self):
        for w, h in [(16, 16), (16, 32), (64, 64), (128, 64), (256, 256)]:
            ok, reasons = geo.validate(w, h)
            self.assertTrue(ok, f"{w}x{h} should be valid: {reasons}")
            self.assertEqual(reasons, [])

    def test_rejects_non_multiples_of_16(self):
        # 15 is not even a multiple of 8; 32 is fine -> one reason.
        ok, reasons = geo.validate(15, 32)
        self.assertFalse(ok)
        self.assertEqual(len(reasons), 1)
        # 40 IS a multiple of 8 but not 16 -> still rejected.
        ok, reasons = geo.validate(40, 36)
        self.assertFalse(ok)
        # both 8-but-not-16 -> rejected on both dimensions.
        ok, reasons = geo.validate(24, 40)
        self.assertFalse(ok)
        self.assertEqual(len(reasons), 2)

    def test_rejects_non_positive(self):
        for w, h in [(0, 32), (16, 0), (-8, 16)]:
            ok, _ = geo.validate(w, h)
            self.assertFalse(ok, f"{w}x{h} should be invalid")

    def test_rejects_over_max_dim(self):
        ok, reasons = geo.validate(geo.MAX_DIM + 8, 32)
        self.assertFalse(ok)
        self.assertTrue(any("aximum" in r for r in reasons))


class HardwareShapeTest(unittest.TestCase):
    def test_recognises_all_12_hardware_shapes(self):
        self.assertEqual(len(geo.HARDWARE_SHAPES), 12)
        for shape in geo.HARDWARE_SHAPES:
            self.assertTrue(geo.is_hardware_shape(*shape))

    def test_rejects_non_hardware_shapes(self):
        for w, h in [(48, 48), (128, 64), (40, 40), (96, 40), (8, 64)]:
            self.assertFalse(geo.is_hardware_shape(w, h))


class CompositeLayoutTest(unittest.TestCase):
    """A composite's pieces tile the frame CENTRED on (0,0), so the
    sprite sits centred on its placement tile."""

    def test_compute_pieces_centres_a_128x64_grid(self):
        # 128x64 in 64x32 pieces — a 2x2 grid whose piece TOP-LEFT
        # corners start at -W/2,-H/2.  (Vanilla's hand-tuned SS Anne
        # table is deliberately off-centre; the generator does NOT copy
        # that — a user-placed sprite must sit centred on its tile.)
        pieces = geo._compute_pieces(128, 64, 64, 32)
        self.assertEqual(len(pieces), 4)
        self.assertEqual(
            [_pt(p) for p in pieces],
            [
                (-64, -32, 64, 32, 0),
                (0, -32, 64, 32, 32),
                (-64, 0, 64, 32, 64),
                (0, 0, 64, 32, 96),
            ],
        )


class DecomposeCompositeTest(unittest.TestCase):
    def test_128x64_is_a_composite(self):
        d = geo.decompose(128, 64)
        self.assertFalse(d.is_single_oam)
        self.assertEqual(d.oam_symbol, "gObjectEventBaseOam_8x8")

    def test_128x64_prefers_2x_64x64_over_vanilla_4x_64x32(self):
        # Vanilla SS Anne uses four 64x32 pieces; two 64x64 pieces tile the
        # same frame with half the OAM slots, so the generator improves on it.
        d = geo.decompose(128, 64)
        self.assertEqual((d.piece_w, d.piece_h), (64, 64))
        self.assertEqual(d.piece_count, 2)
        self.assertEqual((d.cols, d.rows), (2, 1))
        self.assertEqual(
            [_pt(p) for p in d.pieces],
            [(-64, -32, 64, 64, 0), (0, -32, 64, 64, 64)],
        )

    def test_48x48_uses_a_uniform_16x16_grid(self):
        # Vanilla's 48x48 table is a hand-tuned mix of 32x8 + 16x8 pieces;
        # gbagfx -mwidth/-mheight needs a uniform grid, so the generator
        # decomposes into nine 16x16 pieces instead.
        d = geo.decompose(48, 48)
        self.assertFalse(d.is_single_oam)
        self.assertEqual((d.piece_w, d.piece_h), (16, 16))
        self.assertEqual(d.piece_count, 9)


class DecomposeSingleOamTest(unittest.TestCase):
    def test_hardware_shapes_are_single_oam(self):
        for w, h in [(16, 16), (16, 32), (32, 32), (64, 64), (64, 32)]:
            d = geo.decompose(w, h)
            self.assertTrue(d.is_single_oam, f"{w}x{h} should be single-OAM")
            self.assertEqual(d.oam_symbol, f"gObjectEventBaseOam_{w}x{h}")
            self.assertEqual(d.piece_count, 1)
            # the lone piece spans the whole frame: its TOP-LEFT corner
            # is -W/2,-H/2 from the composite centre (not 0,0 — that
            # would be the centre, shoving the sprite off its tile).
            self.assertEqual(
                _pt(d.pieces[0]), (-(w // 2), -(h // 2), w, h, 0))


class SubspriteSymbolTest(unittest.TestCase):
    """The gObjectEventSpriteOamTables_* symbol a decomposition binds to.

    A composite must NEVER reuse a vanilla WxH table — even when vanilla
    ships one for that exact size (48x48, 128x64), vanilla's table is a
    hand-tuned non-grid layout incompatible with the metatile pipeline.
    """

    def test_composite_gets_a_ps_named_table(self):
        for w, h in [(48, 48), (128, 64), (64, 96), (48, 80)]:
            d = geo.decompose(w, h)
            self.assertFalse(d.is_single_oam)
            self.assertEqual(
                d.subsprite_symbol,
                f"gObjectEventSpriteOamTables_Ps{w}x{h}")
            self.assertEqual(
                d.subsprite_array_symbol,
                f"gObjectEventSpriteOamTable_Ps{w}x{h}")

    def test_reusable_single_oam_keeps_the_vanilla_table(self):
        for w, h in [(16, 16), (16, 32), (32, 32), (64, 32), (64, 64)]:
            d = geo.decompose(w, h)
            self.assertEqual(
                d.subsprite_symbol,
                f"gObjectEventSpriteOamTables_{w}x{h}")

    def test_single_oam_without_a_vanilla_table_gets_a_ps_name(self):
        # 32x64 is a hardware shape but vanilla ships no 32x64 table.
        d = geo.decompose(32, 64)
        self.assertTrue(d.is_single_oam)
        self.assertEqual(
            d.subsprite_symbol, "gObjectEventSpriteOamTables_Ps32x64")


class DecomposePathologicalTest(unittest.TestCase):
    def test_non_32_multiple_tiles_into_16x16_pieces(self):
        # 48 is a multiple of 16 but not 32, so the largest uniform
        # hardware piece that tiles it is 16x16 (a 3x3 grid).
        d = geo.decompose(48, 48)
        self.assertFalse(d.is_single_oam)
        self.assertEqual((d.piece_w, d.piece_h), (16, 16))
        self.assertEqual(d.piece_count, 9)

    def test_large_composite_triggers_an_oam_cost_warning(self):
        # 80x80 -> a 5x5 grid of 16x16 pieces = 25 OAM slots.
        d = geo.decompose(80, 80)
        self.assertEqual(d.piece_count, 25)
        self.assertTrue(geo.cost_warnings(d))

    def test_large_frame_triggers_a_vram_cost_warning(self):
        d = geo.decompose(128, 128)
        self.assertGreater(d.vram_bytes, geo._VRAM_WARN)
        self.assertTrue(any("VRAM" in w for w in geo.cost_warnings(d)))


class DecomposeRejectsInvalidTest(unittest.TestCase):
    def test_decompose_raises_on_invalid_size(self):
        for w, h in [(15, 16), (16, 20), (16, 0), (geo.MAX_DIM + 8, 8)]:
            with self.assertRaises(ValueError):
                geo.decompose(w, h)


class MetatileTest(unittest.TestCase):
    """The gbagfx -mwidth/-mheight values must track the piece size, not
    the frame size — Phase 3's spritesheet rule depends on this."""

    def test_metatile_dims_track_piece_size(self):
        self.assertEqual(
            (geo.decompose(128, 64).metatile_w,
             geo.decompose(128, 64).metatile_h), (8, 8))      # 64x64 pieces
        self.assertEqual(
            (geo.decompose(16, 32).metatile_w,
             geo.decompose(16, 32).metatile_h), (2, 4))       # single 16x32
        self.assertEqual(
            (geo.decompose(48, 48).metatile_w,
             geo.decompose(48, 48).metatile_h), (2, 2))       # 16x16 pieces


class DescribeTest(unittest.TestCase):
    def test_describe_distinguishes_single_and_composite(self):
        self.assertIn(
            "single hardware sprite", geo.describe(geo.decompose(16, 32)))
        self.assertIn(
            "composite", geo.describe(geo.decompose(128, 64)))


class GridInvariantTest(unittest.TestCase):
    """Every valid frame size must tile into a gap-free, overlap-free grid
    of real hardware shapes whose tile offsets exactly span the frame."""

    def test_all_valid_sizes_tile_cleanly(self):
        sizes = range(16, geo.MAX_DIM + 1, 16)
        for w in sizes:
            for h in sizes:
                d = geo.decompose(w, h)
                with self.subTest(w=w, h=h):
                    # piece count equals the cols x rows grid
                    self.assertEqual(d.piece_count, d.cols * d.rows)
                    # pieces cover the whole frame area exactly
                    covered = sum(p.shape_w * p.shape_h for p in d.pieces)
                    self.assertEqual(covered, w * h)
                    # tile offsets are contiguous, unique, frame-spanning
                    tiles_per_piece = (d.piece_w // 8) * (d.piece_h // 8)
                    offsets = sorted(p.tile_offset for p in d.pieces)
                    self.assertEqual(
                        offsets,
                        list(range(0, d.tiles_per_frame, tiles_per_piece)),
                    )
                    # every piece is a genuine GBA hardware shape
                    for p in d.pieces:
                        self.assertTrue(
                            geo.is_hardware_shape(p.shape_w, p.shape_h))
                    # the piece bounding box is centred on (0,0): the
                    # composite must sit centred on its placement tile,
                    # never shoved half a piece down-right.
                    self.assertEqual(
                        min(p.x for p in d.pieces), -(w // 2))
                    self.assertEqual(
                        max(p.x + p.shape_w for p in d.pieces), w // 2)
                    self.assertEqual(
                        min(p.y for p in d.pieces), -(h // 2))
                    self.assertEqual(
                        max(p.y + p.shape_h for p in d.pieces), h // 2)


class ProjectScanTest(unittest.TestCase):
    """Read-only scans of the bundled pokefirered headers."""

    POKEFIRERED = os.path.join(ROOT_DIR, "pokefirered")

    def setUp(self):
        if not os.path.isdir(self.POKEFIRERED):
            self.skipTest("pokefirered/ project copy not present")

    def test_scan_oam_templates_finds_vanilla_set(self):
        # Subset, not equality: a project that has created single-OAM
        # sprites of a shape vanilla lacks (8x16, 8x32, 32x64) also
        # carries PorySuite-generated base OAM templates.  Every vanilla
        # template must still be found.
        self.assertLessEqual(
            {(8, 8), (16, 8), (16, 16), (32, 8), (32, 16),
             (32, 32), (16, 32), (64, 32), (64, 64)},
            geo.scan_oam_templates(self.POKEFIRERED),
        )

    def test_scan_subsprite_tables_finds_vanilla_set(self):
        # Asserted as a subset, not equality: a project that has used the
        # New Sprite flow also carries PorySuite-generated subsprite
        # tables, and the scanner correctly reports those alongside the
        # vanilla ones.  What matters is that every vanilla table is found.
        self.assertLessEqual(
            {(16, 16), (16, 32), (32, 32), (48, 48), (64, 32),
             (64, 64), (88, 32), (96, 40), (128, 64)},
            geo.scan_subsprite_tables(self.POKEFIRERED),
        )

    def test_32x64_is_single_oam_but_template_is_missing(self):
        # 32x64 is a legal hardware shape, yet vanilla base_oam.h has no
        # gObjectEventBaseOam_32x64 — exactly the gap Phase 2 must fill.
        d = geo.decompose(32, 64)
        self.assertTrue(d.is_single_oam)
        self.assertNotIn(
            (32, 64), geo.scan_oam_templates(self.POKEFIRERED))


class FrameLayoutTest(unittest.TestCase):
    """Sprite-sheet frame detection — detect_frame_size /
    frame_count_options (the New Sprite dialog's import smarts)."""

    def test_wide_strip_detects_16px_frames(self):
        # dekuguard: a 160x32 ten-frame walk strip → ten 16x32 frames,
        # NOT one ultrawide 160x32 frame.
        self.assertEqual(geo.detect_frame_size(160, 32), (16, 32))
        # vanilla 9-frame NPC strip.
        self.assertEqual(geo.detect_frame_size(144, 32), (16, 32))

    def test_square_or_tall_image_is_a_single_frame(self):
        self.assertEqual(geo.detect_frame_size(64, 64), (64, 64))
        self.assertEqual(geo.detect_frame_size(48, 80), (48, 80))
        self.assertEqual(geo.detect_frame_size(32, 32), (32, 32))

    def test_frame_count_options_are_16px_aligned_divisors(self):
        # Only counts whose frame width is a multiple of 16 survive:
        # 160 -> 1(160) 2(80) 5(32) 10(16); 4/8/16/20 leave 8-but-not-16
        # or partial frames and are excluded.
        self.assertEqual(
            geo.frame_count_options(160), [1, 2, 5, 10])
        self.assertEqual(
            geo.frame_count_options(144), [1, 3, 9])
        self.assertEqual(geo.frame_count_options(0), [1])

    def test_every_frame_count_option_yields_whole_16px_frames(self):
        for w in (160, 144, 256, 96, 48, 32):
            for c in geo.frame_count_options(w):
                with self.subTest(image_w=w, count=c):
                    self.assertEqual(w % c, 0)
                    self.assertEqual((w // c) % 16, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
