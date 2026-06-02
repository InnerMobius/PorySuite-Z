"""Verification for the Phase 3 geometry integration in
``core/overworld_sprite_creator.py`` — the New Sprite flow now routes
every frame size through ``overworld_sprite_geometry`` +
``overworld_subsprite_gen`` instead of the deleted hardcoded
``_OAM_TABLE``.

The creator is loaded via importlib with a stub ``core`` package in
``sys.modules`` so ``core/__init__.py`` (which pulls in the whole app)
never runs; the two pure helper modules are pre-registered as ``core.*``
so the creator's ``from core ...`` imports resolve to them.

PyQt6 is required — the creator reads and writes PNGs through QImage.

Run directly:  python tests/test_overworld_sprite_creator_geometry.py
"""

import importlib.util
import os
import re
import sys
import tempfile
import types
import unittest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

POKEFIRERED = os.path.join(ROOT_DIR, "pokefirered")


def _load(modname, filename):
    path = os.path.join(ROOT_DIR, "core", filename)
    spec = importlib.util.spec_from_file_location(modname, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# A stub `core` package: `from core import ...` inside the creator must
# resolve to the pure helper modules below, never to the real
# core/__init__.py (which imports the entire data layer + PyQt UI).
_core_pkg = types.ModuleType("core")
_core_pkg.__path__ = [os.path.join(ROOT_DIR, "core")]
sys.modules["core"] = _core_pkg

geo = _load("core.overworld_sprite_geometry", "overworld_sprite_geometry.py")
gen = _load("core.overworld_subsprite_gen", "overworld_subsprite_gen.py")
_core_pkg.overworld_sprite_geometry = geo
_core_pkg.overworld_subsprite_gen = gen
creator = _load(
    "core.overworld_sprite_creator", "overworld_sprite_creator.py")


# ── minimal synthetic project headers ───────────────────────────────────
# Just enough structure for the creator's regexes to anchor; base_oam.h
# and object_event_subsprites.h are copied from real pokefirered so
# vanilla-vs-generated detection is exercised against genuine content.

_EVENT_OBJECTS_H = "#define NUM_OBJ_EVENT_GFX 3\n"
_GRAPHICS_H = (
    "const u16 gObjectEventPic_Dummy[] = "
    'INCBIN_U16("graphics/object_events/pics/people/dummy.4bpp");\n'
)
_PIC_TABLES_H = (
    "static const struct SpriteFrameImage sPicTable_Dummy[] = {\n"
    "    overworld_frame(gObjectEventPic_Dummy, 2, 4, 0),\n"
    "};\n"
)
_GRAPHICS_INFO_H = "// object event graphics info\n"
_POINTERS_H = (
    "const struct ObjectEventGraphicsInfo gObjectEventGraphicsInfo_Dummy;\n"
    "\n"
    "const struct ObjectEventGraphicsInfo *const "
    "gObjectEventGraphicsInfoPointers[] = {\n"
    "    [OBJ_EVENT_GFX_DUMMY] = &gObjectEventGraphicsInfo_Dummy,\n"
    "};\n"
)
_SPRITESHEET_RULES = "# spritesheet rules\n"
# Minimal src/sprite.c carrying the exact vanilla SortSprites tie-break,
# so the creator's depth-fix step (sprite_depth_patch) has a real anchor.
_SPRITE_C = (
    "void SortSprites(void)\n"
    "{\n"
    "    u8 i;\n"
    "    for (i = 1; i < MAX_SPRITES; i++)\n"
    "    {\n"
    "        u8 j = i;\n"
    "        s16 sprite1Y = sprite1->oam.y;\n"
    "        s16 sprite2Y = sprite2->oam.y;\n"
    "        while (j > 0\n"
    "            && ((sprite1Priority > sprite2Priority)\n"
    "             || (sprite1Priority == sprite2Priority"
    " && sprite1Y < sprite2Y)))\n"
    "        {\n"
    "            u8 temp = gSpriteOrder[j];\n"
    "        }\n"
    "    }\n"
    "}\n"
)


def _write(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


class _CreatorTestBase(unittest.TestCase):
    GI = os.path.join(
        "src", "data", "object_events", "object_event_graphics_info.h")
    SUBS = os.path.join(
        "src", "data", "object_events", "object_event_subsprites.h")
    OAM = os.path.join("src", "data", "object_events", "base_oam.h")
    SPRITE_C = os.path.join("src", "sprite.c")
    RULES = "spritesheet_rules.mk"

    def setUp(self):
        if not os.path.isdir(POKEFIRERED):
            self.skipTest("pokefirered/ project copy not present")
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        oe = os.path.join(self.root, "src", "data", "object_events")
        os.makedirs(oe)
        os.makedirs(os.path.join(self.root, "include", "constants"))
        _write(os.path.join(self.root, "include", "constants",
                            "event_objects.h"), _EVENT_OBJECTS_H)
        _write(os.path.join(oe, "object_event_graphics.h"), _GRAPHICS_H)
        _write(os.path.join(oe, "object_event_pic_tables.h"), _PIC_TABLES_H)
        _write(os.path.join(oe, "object_event_graphics_info.h"),
               _GRAPHICS_INFO_H)
        _write(os.path.join(oe, "object_event_graphics_info_pointers.h"),
               _POINTERS_H)
        _write(os.path.join(self.root, self.RULES), _SPRITESHEET_RULES)
        _write(os.path.join(self.root, "src", "sprite.c"), _SPRITE_C)
        # base_oam.h / object_event_subsprites.h come from the real
        # project so vanilla-vs-generated detection runs against genuine
        # content — but any PORYSUITE-GEN blocks a previous app session
        # already wrote into the live copy are stripped, so the fixture
        # always starts with a clean (zero generated tables) baseline.
        for name in ("base_oam.h", "object_event_subsprites.h"):
            with open(os.path.join(POKEFIRERED, "src", "data",
                                   "object_events", name),
                      encoding="utf-8", errors="replace") as f:
                src_text = f.read()
            clean = re.sub(
                r"\n*// >>> PORYSUITE-GEN .*?<<<\n*", "\n",
                src_text, flags=re.DOTALL,
            )
            _write(os.path.join(oe, name), clean)

    def tearDown(self):
        self._tmp.cleanup()

    def _png(self, frame_w, frame_h, num_frames):
        """A synthetic 16-colour indexed horizontal frame strip."""
        from PyQt6.QtGui import QImage, qRgb
        path = os.path.join(self.root, "imported_source.png")
        img = QImage(frame_w * num_frames, frame_h,
                     QImage.Format.Format_Indexed8)
        img.setColorTable([qRgb(i * 16, i * 16, i * 16) for i in range(16)])
        for y in range(frame_h):
            for x in range(frame_w * num_frames):
                img.setPixel(x, y, (x + y) % 16)
        img.save(path, "PNG")
        return path

    def _create(self, slug, frame_w, frame_h, num_frames):
        return creator.create_overworld_sprite(
            self.root, self._png(frame_w, frame_h, num_frames), slug,
            frame_w, frame_h, "sAnimTable_Standard", "people",
            palette_tag="OBJ_EVENT_PAL_TAG_NPC_BLUE",
            palette_slot="PALSLOT_NPC_1",
        )

    def _read(self, rel):
        with open(os.path.join(self.root, rel), encoding="utf-8") as f:
            return f.read()


class VerticalStripTest(unittest.TestCase):
    """Direct checks on the `_write_vertical_frame_strip` helper."""

    def _horizontal_strip(self, frame_w, frame_h, num_frames):
        from PyQt6.QtGui import QImage, qRgb
        img = QImage(frame_w * num_frames, frame_h,
                     QImage.Format.Format_Indexed8)
        img.setColorTable([qRgb(i * 17, 255 - i * 16, i * 8)
                           for i in range(16)])
        for y in range(frame_h):
            for x in range(frame_w * num_frames):
                img.setPixel(x, y, (x * 3 + y * 5) % 16)
        return img

    def test_transpose_moves_every_pixel_to_its_frame_slot(self):
        from PyQt6.QtGui import QImage
        fw, fh, nf = 24, 16, 3
        with tempfile.TemporaryDirectory() as d:
            src_path = os.path.join(d, "h.png")
            dst_path = os.path.join(d, "v.png")
            self._horizontal_strip(fw, fh, nf).save(src_path, "PNG")
            creator._write_vertical_frame_strip(
                src_path, dst_path, fw, fh, nf)
            src = QImage(src_path)
            out = QImage(dst_path)
            self.assertEqual((out.width(), out.height()), (fw, fh * nf))
            for f in range(nf):
                for y in range(fh):
                    for x in range(fw):
                        self.assertEqual(
                            out.pixelIndex(x, f * fh + y),
                            src.pixelIndex(f * fw + x, y),
                            f"frame {f} pixel ({x},{y})",
                        )

    def test_transpose_preserves_the_colour_table(self):
        from PyQt6.QtGui import QImage
        with tempfile.TemporaryDirectory() as d:
            src_path = os.path.join(d, "h.png")
            dst_path = os.path.join(d, "v.png")
            self._horizontal_strip(16, 16, 2).save(src_path, "PNG")
            creator._write_vertical_frame_strip(
                src_path, dst_path, 16, 16, 2)
            self.assertEqual(
                QImage(dst_path).colorTable(),
                QImage(src_path).colorTable())


class PadSheetTest(unittest.TestCase):
    """Direct checks on the `pad_sprite_sheet` auto-pad helper."""

    def _indexed_strip(self, frame_w, frame_h, num_frames):
        from PyQt6.QtGui import QImage, qRgb
        img = QImage(frame_w * num_frames, frame_h,
                     QImage.Format.Format_Indexed8)
        img.setColorTable([qRgb(i * 16, i * 16, i * 16) for i in range(16)])
        for y in range(frame_h):
            for x in range(frame_w * num_frames):
                img.setPixel(x, y, (x + y) % 15 + 1)   # 1..15, never 0
        return img

    def test_pads_each_frame_to_the_next_multiple_of_16(self):
        from PyQt6.QtGui import QImage
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "odd.png")
            dst = os.path.join(d, "padded.png")
            self._indexed_strip(36, 40, 2).save(src, "PNG")
            pw, ph = creator.pad_sprite_sheet(src, 2, dst)
            self.assertEqual((pw, ph), (48, 48))      # 36->48, 40->48
            out = QImage(dst)
            self.assertEqual((out.width(), out.height()), (96, 48))

    def test_original_art_lands_bottom_centred(self):
        from PyQt6.QtGui import QImage
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "odd.png")
            dst = os.path.join(d, "padded.png")
            src_img = self._indexed_strip(36, 40, 2)
            src_img.save(src, "PNG")
            creator.pad_sprite_sheet(src, 2, dst)
            out = QImage(dst)
            x_off, y_off = (48 - 36) // 2, 48 - 40    # 6, 8
            for f in range(2):
                for y in range(40):
                    for x in range(36):
                        self.assertEqual(
                            out.pixelIndex(f * 48 + x_off + x, y_off + y),
                            src_img.pixelIndex(f * 36 + x, y),
                            f"frame {f} pixel ({x},{y})")
            # the new margin is transparent index 0
            self.assertEqual(out.pixelIndex(0, 0), 0)

    def test_uneven_frame_count_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "odd.png")
            self._indexed_strip(36, 40, 2).save(src, "PNG")
            with self.assertRaises(ValueError):
                creator.pad_sprite_sheet(src, 5, os.path.join(d, "x.png"))


class CreateSingleOamTest(_CreatorTestBase):
    def test_32x32_uses_vanilla_oam_and_table_untouched(self):
        ok, applied, errors = self._create("big_npc", 32, 32, 3)
        self.assertTrue(ok, errors)
        self.assertEqual(errors, [])
        gi = self._read(self.GI)
        self.assertIn("gObjectEventGraphicsInfo_BigNpc", gi)
        self.assertIn(".oam = &gObjectEventBaseOam_32x32", gi)
        self.assertIn(
            ".subspriteTables = gObjectEventSpriteOamTables_32x32", gi)
        # 32x32 is fully vanilla — nothing generated
        self.assertEqual(
            gen.scan_generated_subsprite_tables(self.root), set())
        self.assertEqual(gen.scan_generated_oam_bases(self.root), set())
        # single-OAM rule = frame size in tiles
        self.assertIn("-mwidth 4 -mheight 4", self._read(self.RULES))

    def test_single_oam_png_is_left_as_a_horizontal_strip(self):
        from PyQt6.QtGui import QImage
        self._create("walker", 16, 32, 4)
        dest = QImage(os.path.join(
            self.root, "graphics", "object_events", "pics", "people",
            "walker.png"))
        # 4 frames of 16x32 stay side by side
        self.assertEqual((dest.width(), dest.height()), (64, 32))


class CreateBuildBreakFixTest(_CreatorTestBase):
    def test_32x64_now_generates_its_missing_oam_and_table(self):
        # The headline bug: vanilla ships no gObjectEventBaseOam_32x64 and
        # no 32x64 subsprite table, so the old _OAM_TABLE pointed at a
        # symbol that did not exist -> build break.  Both are generated.
        ok, applied, errors = self._create("tall_guy", 32, 64, 2)
        self.assertTrue(ok, errors)
        self.assertEqual(errors, [])
        self.assertIn(
            ".oam = &gObjectEventBaseOam_32x64", self._read(self.GI))
        self.assertIn(
            (32, 64), gen.scan_generated_oam_bases(self.root))
        self.assertIn(
            (32, 64), gen.scan_generated_subsprite_tables(self.root))
        self.assertIn("-mwidth 4 -mheight 8", self._read(self.RULES))


class CreateCompositeTest(_CreatorTestBase):
    def test_64x96_single_frame_generates_a_subsprite_table(self):
        ok, applied, errors = self._create("ship", 64, 96, 1)
        self.assertTrue(ok, errors)
        self.assertEqual(errors, [])
        gi = self._read(self.GI)
        # composite -> dummy 8x8 base + a generated Ps-named subsprite
        # table (never the vanilla WxH name)
        self.assertIn(".oam = &gObjectEventBaseOam_8x8", gi)
        self.assertIn(
            ".subspriteTables = gObjectEventSpriteOamTables_Ps64x96", gi)
        self.assertIn(
            (64, 96), gen.scan_generated_subsprite_tables(self.root))
        # piece-size metatile rule (64x96 -> three 64x32 pieces)
        self.assertIn("-mwidth 8 -mheight 4", self._read(self.RULES))

    def test_multi_frame_composite_is_stored_as_a_vertical_strip(self):
        from PyQt6.QtGui import QImage
        ok, applied, errors = self._create("big_boss", 64, 96, 2)
        self.assertTrue(ok, errors)
        dest = QImage(os.path.join(
            self.root, "graphics", "object_events", "pics", "people",
            "big_boss.png"))
        # two 64x96 frames stacked vertically -> 64 wide, 192 tall
        self.assertEqual((dest.width(), dest.height()), (64, 192))


class CreateDepthFixTest(_CreatorTestBase):
    """Creating a sprite applies the composite depth-sort engine fix."""

    def test_create_applies_the_sortsprites_depth_fix(self):
        ok, applied, errors = self._create("ship", 64, 96, 1)
        self.assertTrue(ok, errors)
        self.assertEqual(errors, [])
        sprite_c = self._read(self.SPRITE_C)
        # The SortSprites tie-break now carries the fix signature...
        self.assertIn("PORYSUITE-DEPTH", sprite_c)
        # ...and the bare top-corner tie-break is gone.
        self.assertNotIn(
            "sprite1Priority == sprite2Priority && sprite1Y < sprite2Y",
            sprite_c,
        )

    def test_depth_fix_is_not_reapplied_on_a_second_sprite(self):
        self._create("ship_one", 64, 96, 1)
        ok, applied, errors = self._create("ship_two", 64, 96, 1)
        self.assertTrue(ok, errors)
        self.assertEqual(errors, [])
        # The fix landed once; the second creation must not re-patch it
        # (the signature appears exactly once in the patched block).
        self.assertEqual(self._read(self.SPRITE_C).count("PORYSUITE-DEPTH"), 1)


class CreateValidationTest(_CreatorTestBase):
    def test_non_multiple_of_8_size_is_rejected_cleanly(self):
        ok, applied, errors = self._create("bad_size", 30, 32, 1)
        self.assertFalse(ok)
        self.assertTrue(any("Invalid frame size" in e for e in errors))
        # rejected before any file was touched
        self.assertEqual(applied, [])
        self.assertNotIn(
            "gObjectEventGraphicsInfo_BadSize", self._read(self.GI))


class DeleteCleanupTest(_CreatorTestBase):
    def test_deleting_last_sprite_of_a_size_removes_generated_tables(self):
        ok, _, errors = self._create("lone_ship", 64, 96, 1)
        self.assertTrue(ok, errors)
        self.assertIn(
            (64, 96), gen.scan_generated_subsprite_tables(self.root))

        ok, applied, errors = creator.delete_overworld_sprite(
            self.root, "LoneShip", delete_files=False)
        self.assertTrue(ok, errors)
        # the generated table this sprite uniquely owned is gone
        self.assertEqual(
            gen.scan_generated_subsprite_tables(self.root), set())
        self.assertNotIn(
            "gObjectEventSpriteOamTables_64x96", self._read(self.SUBS))


if __name__ == "__main__":
    unittest.main(verbosity=2)
