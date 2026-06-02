"""Verification for ``core/overworld_subsprite_gen.py`` — Phase 2 of the
Overworld Editor arbitrary-dimensions upgrade.

Both the geometry module and the generator are PURE (stdlib only); they
are loaded straight from their files with ``importlib`` so the test never
touches ``core/__init__.py``.

Integration tests run against real copies of vanilla pokefirered's
``base_oam.h`` and ``object_event_subsprites.h`` in a throwaway temp tree,
so "is this size already vanilla?" is exercised against the genuine
headers — not a stub.

Run directly:   python tests/test_overworld_subsprite_gen.py
"""

import importlib.util
import os
import re
import sys
import tempfile
import unittest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

POKEFIRERED = os.path.join(ROOT_DIR, "pokefirered")


def _load(modname, filename):
    """Load a pure ``core/`` module without importing the core package."""
    path = os.path.join(ROOT_DIR, "core", filename)
    spec = importlib.util.spec_from_file_location(modname, path)
    module = importlib.util.module_from_spec(spec)
    # @dataclass resolves cls.__module__ via sys.modules during exec.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


geo = _load("overworld_sprite_geometry", "overworld_sprite_geometry.py")
gen = _load("overworld_subsprite_gen", "overworld_subsprite_gen.py")


class EmitTest(unittest.TestCase):
    """Structural checks on the raw C-text emitters — no files."""

    def test_subsprite_block_is_well_formed(self):
        block = gen._subsprite_block(geo.decompose(64, 96))
        self.assertIn(
            ">>> PORYSUITE-GEN overworld-subsprite 64x96 >>>", block)
        self.assertIn(
            "<<< PORYSUITE-GEN overworld-subsprite 64x96 <<<", block)
        self.assertEqual(block.count("{"), block.count("}"))
        # a composite gets a Ps-infixed name — never the vanilla WxH form
        self.assertIn(
            "const struct Subsprite gObjectEventSpriteOamTable_Ps64x96[]",
            block)
        self.assertIn(
            "const struct SubspriteTable "
            "gObjectEventSpriteOamTables_Ps64x96[]", block)
        # the piece array must be declared before the table that uses it
        self.assertLess(
            block.index("const struct Subsprite "
                        "gObjectEventSpriteOamTable_Ps64x96"),
            block.index("const struct SubspriteTable "
                        "gObjectEventSpriteOamTables_Ps64x96"),
        )
        # six SubspriteTable slots, all pointing at the one piece array
        self.assertEqual(
            block.count("{3, gObjectEventSpriteOamTable_Ps64x96}"), 6)

    def test_subsprite_block_piece_values_match_decompose(self):
        # 128x128 -> a 2x2 grid of 64x64, so x/y/tileOffset all vary.
        d = geo.decompose(128, 128)
        block = gen._subsprite_block(d)
        for p in d.pieces:
            self.assertIn(f".x = {p.x},", block)
            self.assertIn(f".y = {p.y},", block)
            self.assertIn(f".tileOffset = {p.tile_offset},", block)
            self.assertIn(
                f"SPRITE_SHAPE({p.shape_w}x{p.shape_h})", block)

    def test_oam_block_is_well_formed(self):
        block = gen._oam_block(32, 64)
        self.assertIn(">>> PORYSUITE-GEN overworld-oam 32x64 >>>", block)
        self.assertEqual(block.count("{"), block.count("}"))
        self.assertIn(
            "const struct OamData gObjectEventBaseOam_32x64 =", block)
        self.assertIn("SPRITE_SHAPE(32x64)", block)
        self.assertIn("SPRITE_SIZE(32x64)", block)


class _GenTestBase(unittest.TestCase):
    """Sets up a temp project holding real copies of the two headers
    the generator writes."""

    SUBS_REL = os.path.join(
        "src", "data", "object_events", "object_event_subsprites.h")
    OAM_REL = os.path.join(
        "src", "data", "object_events", "base_oam.h")

    def setUp(self):
        if not os.path.isdir(POKEFIRERED):
            self.skipTest("pokefirered/ project copy not present")
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        os.makedirs(os.path.join(
            self.root, "src", "data", "object_events"))
        # Copy the real headers, but strip any PORYSUITE-GEN blocks a
        # previous app session wrote into the live project copy — the
        # fixture must always start with zero generated tables.
        for rel in (self.SUBS_REL, self.OAM_REL):
            with open(os.path.join(POKEFIRERED, rel),
                      encoding="utf-8", errors="replace") as f:
                text = f.read()
            text = re.sub(
                r"\n*// >>> PORYSUITE-GEN .*?<<<\n*", "\n",
                text, flags=re.DOTALL,
            )
            with open(os.path.join(self.root, rel), "w",
                      encoding="utf-8", newline="\n") as f:
                f.write(text)

    def tearDown(self):
        self._tmp.cleanup()

    def subs_text(self):
        with open(os.path.join(self.root, self.SUBS_REL),
                  encoding="utf-8") as f:
            return f.read()

    def oam_text(self):
        with open(os.path.join(self.root, self.OAM_REL),
                  encoding="utf-8") as f:
            return f.read()


class EnsureSubspriteTest(_GenTestBase):
    def test_reusable_vanilla_table_is_a_noop(self):
        # A single-OAM sprite of a shape vanilla ships a grid-compatible
        # table for reuses that table — no generation.
        for w, h in [(16, 16), (16, 32), (32, 32), (64, 32), (64, 64)]:
            before = self.subs_text()
            res = gen.ensure_subsprite_table(
                self.root, geo.decompose(w, h))
            self.assertFalse(res.changed, f"{w}x{h} should be a no-op")
            self.assertEqual(self.subs_text(), before)

    def test_composite_generates_even_at_a_vanilla_size(self):
        # The King Zora regression: vanilla ships 48x48 / 128x64 tables,
        # but they are hand-tuned non-grid layouts.  A composite of those
        # sizes must still generate its OWN Ps-named grid table — never
        # bind to the incompatible vanilla one.
        for w, h in [(48, 48), (128, 64)]:
            res = gen.ensure_subsprite_table(
                self.root, geo.decompose(w, h))
            self.assertTrue(res.changed, f"{w}x{h} must generate")
            self.assertEqual(
                res.symbol, f"gObjectEventSpriteOamTables_Ps{w}x{h}")
            self.assertIn(
                f"gObjectEventSpriteOamTables_Ps{w}x{h}[]",
                self.subs_text())

    def test_generates_missing_composite(self):
        res = gen.ensure_subsprite_table(
            self.root, geo.decompose(64, 96))
        self.assertTrue(res.changed)
        self.assertEqual(
            res.symbol, "gObjectEventSpriteOamTables_Ps64x96")
        text = self.subs_text()
        self.assertIn(
            "const struct SubspriteTable "
            "gObjectEventSpriteOamTables_Ps64x96[]", text)
        self.assertIn(
            ">>> PORYSUITE-GEN overworld-subsprite 64x96 >>>", text)
        self.assertEqual(
            text.count("{3, gObjectEventSpriteOamTable_Ps64x96}"), 6)

    def test_generates_odd_single_oam_size(self):
        # 32x64 is a legal hardware shape but has no vanilla table, so it
        # gets a generated Ps table.
        res = gen.ensure_subsprite_table(
            self.root, geo.decompose(32, 64))
        self.assertTrue(res.changed)
        self.assertEqual(
            self.subs_text().count(
                "{1, gObjectEventSpriteOamTable_Ps32x64}"), 6)

    def test_is_idempotent(self):
        gen.ensure_subsprite_table(self.root, geo.decompose(48, 48))
        after_first = self.subs_text()
        res = gen.ensure_subsprite_table(
            self.root, geo.decompose(48, 48))
        self.assertFalse(res.changed)
        self.assertEqual(self.subs_text(), after_first)

    def test_generated_table_is_scannable(self):
        # The generated C must be recognised by the Phase 1 scanner —
        # proof its shape matches what the compiler expects too.
        gen.ensure_subsprite_table(self.root, geo.decompose(64, 96))
        self.assertIn(
            (64, 96), geo.scan_subsprite_tables(self.root))
        self.assertEqual(
            gen.scan_generated_subsprite_tables(self.root), {(64, 96)})


class EnsureOamTest(_GenTestBase):
    def test_vanilla_base_is_a_noop(self):
        for w, h in [(16, 32), (32, 32), (64, 64)]:
            before = self.oam_text()
            res = gen.ensure_oam_base(self.root, geo.decompose(w, h))
            self.assertFalse(res.changed)
            self.assertEqual(self.oam_text(), before)

    def test_composite_uses_dummy_8x8(self):
        before = self.oam_text()
        res = gen.ensure_oam_base(self.root, geo.decompose(48, 48))
        self.assertFalse(res.changed)
        self.assertEqual(res.symbol, "gObjectEventBaseOam_8x8")
        self.assertEqual(self.oam_text(), before)

    def test_generates_missing_single_oam_base(self):
        # 32x64 is the one 16-multiple hardware shape vanilla has no base
        # OAM template for — every other 16-multiple single-OAM shape
        # (16x16, 16x32, 32x16, 32x32, 64x32, 64x64) is already in
        # base_oam.h.
        res = gen.ensure_oam_base(self.root, geo.decompose(32, 64))
        self.assertTrue(res.changed, "32x64 base should generate")
        text = self.oam_text()
        self.assertIn(
            "const struct OamData gObjectEventBaseOam_32x64 =", text)
        self.assertIn("SPRITE_SHAPE(32x64)", text)

    def test_is_idempotent(self):
        gen.ensure_oam_base(self.root, geo.decompose(32, 64))
        after_first = self.oam_text()
        res = gen.ensure_oam_base(self.root, geo.decompose(32, 64))
        self.assertFalse(res.changed)
        self.assertEqual(self.oam_text(), after_first)

    def test_generated_base_is_scannable(self):
        gen.ensure_oam_base(self.root, geo.decompose(32, 64))
        self.assertIn((32, 64), geo.scan_oam_templates(self.root))
        self.assertEqual(
            gen.scan_generated_oam_bases(self.root), {(32, 64)})


class EnsureBothTest(_GenTestBase):
    def test_ensure_overworld_geometry_touches_both_files(self):
        # 32x64: needs a generated OAM base AND a generated table.
        results = gen.ensure_overworld_geometry(
            self.root, geo.decompose(32, 64))
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r.changed for r in results))
        self.assertIn((32, 64), geo.scan_oam_templates(self.root))
        self.assertIn((32, 64), geo.scan_subsprite_tables(self.root))


class RemoveTest(_GenTestBase):
    def test_remove_generated_subsprite_table(self):
        gen.ensure_subsprite_table(self.root, geo.decompose(64, 96))
        res = gen.remove_subsprite_table(self.root, 64, 96)
        self.assertTrue(res.changed)
        text = self.subs_text()
        self.assertNotIn("gObjectEventSpriteOamTables_Ps64x96", text)
        self.assertNotIn("PORYSUITE-GEN overworld-subsprite 64x96", text)

    def test_remove_refuses_vanilla_subsprite_table(self):
        res = gen.remove_subsprite_table(self.root, 16, 16)
        self.assertFalse(res.changed)
        # the vanilla table must be completely untouched
        self.assertIn(
            "const struct SubspriteTable "
            "gObjectEventSpriteOamTables_16x16[]", self.subs_text())

    def test_remove_generated_oam_base(self):
        gen.ensure_oam_base(self.root, geo.decompose(32, 64))
        res = gen.remove_oam_base(self.root, 32, 64)
        self.assertTrue(res.changed)
        self.assertNotIn(
            "gObjectEventBaseOam_32x64", self.oam_text())

    def test_remove_refuses_vanilla_oam_base(self):
        res = gen.remove_oam_base(self.root, 16, 32)
        self.assertFalse(res.changed)
        self.assertIn(
            "const struct OamData gObjectEventBaseOam_16x32",
            self.oam_text())

    def test_remove_one_size_keeps_another(self):
        gen.ensure_subsprite_table(self.root, geo.decompose(64, 96))
        gen.ensure_subsprite_table(self.root, geo.decompose(48, 48))
        gen.remove_subsprite_table(self.root, 64, 96)
        text = self.subs_text()
        self.assertNotIn("gObjectEventSpriteOamTables_Ps64x96", text)
        self.assertIn(
            "gObjectEventSpriteOamTables_Ps48x48", text)

    def test_generate_then_remove_restores_the_file(self):
        before = self.subs_text()
        gen.ensure_subsprite_table(self.root, geo.decompose(48, 48))
        gen.remove_subsprite_table(self.root, 48, 48)
        self.assertEqual(self.subs_text().rstrip(), before.rstrip())


class ScanTest(_GenTestBase):
    def test_scans_are_empty_on_a_fresh_project(self):
        self.assertEqual(
            gen.scan_generated_subsprite_tables(self.root), set())
        self.assertEqual(
            gen.scan_generated_oam_bases(self.root), set())

    def test_scans_report_every_generated_size(self):
        gen.ensure_subsprite_table(self.root, geo.decompose(64, 96))
        gen.ensure_subsprite_table(self.root, geo.decompose(48, 48))
        gen.ensure_oam_base(self.root, geo.decompose(32, 64))
        self.assertEqual(
            gen.scan_generated_subsprite_tables(self.root),
            {(64, 96), (48, 48)})
        self.assertEqual(
            gen.scan_generated_oam_bases(self.root), {(32, 64)})


if __name__ == "__main__":
    unittest.main(verbosity=2)
