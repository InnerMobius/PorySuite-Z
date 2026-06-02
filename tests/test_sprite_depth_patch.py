"""Verification for ``core/sprite_depth_patch.py`` — the composite-sprite
depth-sort engine fix.

The patcher module is PURE (stdlib only), so it is loaded straight from
its file with ``importlib`` — the test never imports ``core/__init__.py``.

The transform is exercised on synthetic ``sprite.c`` bodies, and the
hardcoded vanilla anchor is checked against a *genuine* unpatched
pokefirered ``sprite.c`` (the read-only reference copy when present,
otherwise the project copy) so a whitespace drift in the anchor string
is caught immediately.

Run directly:   python tests/test_sprite_depth_patch.py
"""

import importlib.util
import os
import sys
import tempfile
import unittest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)


def _load(modname, filename):
    """Load a pure ``core/`` module without importing the core package."""
    path = os.path.join(ROOT_DIR, "core", filename)
    spec = importlib.util.spec_from_file_location(modname, path)
    module = importlib.util.module_from_spec(spec)
    # @dataclass resolves cls.__module__ via sys.modules during exec.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sdp = _load("sprite_depth_patch", "sprite_depth_patch.py")


# A synthetic SortSprites body that embeds the exact vanilla tie-break.
_FAKE_SPRITE_C = (
    "void SortSprites(void)\n"
    "{\n"
    "    u8 i;\n"
    "    for (i = 1; i < MAX_SPRITES; i++)\n"
    "    {\n"
    "        u8 j = i;\n"
    "        s16 sprite1Y = sprite1->oam.y;\n"
    "        s16 sprite2Y = sprite2->oam.y;\n"
    + sdp._VANILLA + "\n"
    "        {\n"
    "            u8 temp = gSpriteOrder[j];\n"
    "        }\n"
    "    }\n"
    "}\n"
)


def _find_genuine_sprite_c():
    """Path to a real pokefirered sprite.c — prefer the untouched
    read-only reference, fall back to the project copy.  Returns None if
    neither is present."""
    candidates = [
        os.path.join("C:\\", "GBA", "READONLYREFERENCE", "pokefirered",
                     "src", "sprite.c"),
        os.path.join(ROOT_DIR, "pokefirered", "src", "sprite.c"),
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return None


class TextTransformTest(unittest.TestCase):
    """The pure ``_apply_to_text`` / ``_is_applied_text`` transform."""

    def test_vanilla_is_not_detected_as_applied(self):
        self.assertFalse(sdp._is_applied_text(_FAKE_SPRITE_C))

    def test_apply_rewrites_vanilla_tie_break(self):
        new, changed, ok = sdp._apply_to_text(_FAKE_SPRITE_C)
        self.assertTrue(changed)
        self.assertTrue(ok)
        self.assertNotEqual(new, _FAKE_SPRITE_C)

    def test_patched_text_compares_feet_not_corner(self):
        new, _, _ = sdp._apply_to_text(_FAKE_SPRITE_C)
        # Both sprites' feet expressions must be present.
        self.assertIn("sprite1Y - 2 * sprite1->centerToCornerVecY", new)
        self.assertIn("sprite2Y - 2 * sprite2->centerToCornerVecY", new)
        # The bare top-corner tie-break must be gone.
        self.assertNotIn(
            "sprite1Priority == sprite2Priority && sprite1Y < sprite2Y", new,
        )

    def test_patched_text_is_detected_as_applied(self):
        new, _, _ = sdp._apply_to_text(_FAKE_SPRITE_C)
        self.assertTrue(sdp._is_applied_text(new))

    def test_apply_is_idempotent(self):
        once, changed1, ok1 = sdp._apply_to_text(_FAKE_SPRITE_C)
        twice, changed2, ok2 = sdp._apply_to_text(once)
        self.assertTrue(changed1)
        self.assertFalse(changed2)      # second pass is a no-op
        self.assertTrue(ok1 and ok2)
        self.assertEqual(once, twice)   # text is unchanged the second time

    def test_missing_anchor_reports_not_ok(self):
        text = "void SortSprites(void) { /* hand-rewritten */ }\n"
        new, changed, ok = sdp._apply_to_text(text)
        self.assertFalse(changed)
        self.assertFalse(ok)
        self.assertEqual(new, text)

    def test_patched_block_has_balanced_parens(self):
        # The rewritten condition must not drop or add a parenthesis.
        self.assertEqual(
            sdp._PATCHED.count("("), sdp._PATCHED.count(")"),
        )
        self.assertEqual(
            sdp._VANILLA.count("("), sdp._VANILLA.count(")"),
        )


class ProjectLevelTest(unittest.TestCase):
    """``ensure_sprite_depth_fix`` against a temp project tree."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="depthpatch_")
        self.src = os.path.join(self.tmp, "src")
        os.makedirs(self.src)
        self.sprite_c = os.path.join(self.src, "sprite.c")
        with open(self.sprite_c, "w", encoding="utf-8", newline="\n") as f:
            f.write(_FAKE_SPRITE_C)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_first_apply_changes_the_file(self):
        res = sdp.ensure_sprite_depth_fix(self.tmp)
        self.assertTrue(res.changed)
        self.assertTrue(res.ok)
        self.assertTrue(sdp.is_sprite_depth_fix_applied(self.tmp))

    def test_second_apply_is_a_no_op(self):
        sdp.ensure_sprite_depth_fix(self.tmp)
        res = sdp.ensure_sprite_depth_fix(self.tmp)
        self.assertFalse(res.changed)
        self.assertTrue(res.ok)

    def test_apply_preserves_unix_newlines(self):
        sdp.ensure_sprite_depth_fix(self.tmp)
        with open(self.sprite_c, "rb") as f:
            raw = f.read()
        self.assertNotIn(b"\r\n", raw)

    def test_missing_sprite_c_reports_not_ok(self):
        os.remove(self.sprite_c)
        res = sdp.ensure_sprite_depth_fix(self.tmp)
        self.assertFalse(res.changed)
        self.assertFalse(res.ok)

    def test_hand_modified_engine_reports_not_ok(self):
        with open(self.sprite_c, "w", encoding="utf-8", newline="\n") as f:
            f.write("void SortSprites(void) { /* rewritten */ }\n")
        res = sdp.ensure_sprite_depth_fix(self.tmp)
        self.assertFalse(res.changed)
        self.assertFalse(res.ok)
        self.assertFalse(sdp.is_sprite_depth_fix_applied(self.tmp))


class GenuineSourceTest(unittest.TestCase):
    """The hardcoded vanilla anchor must match a real pokefirered file."""

    def test_anchor_matches_genuine_sprite_c(self):
        path = _find_genuine_sprite_c()
        if path is None:
            self.skipTest("no pokefirered sprite.c available")
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        # The real file must be in a state the patcher understands:
        # either still vanilla (anchor present) or already patched.
        self.assertTrue(
            (sdp._VANILLA in text) or sdp._is_applied_text(text),
            "SortSprites tie-break in the real sprite.c matches neither "
            "the vanilla anchor nor the applied signature — the hardcoded "
            "_VANILLA string has drifted from the genuine source.",
        )

    def test_reference_copy_is_strictly_vanilla(self):
        ref = os.path.join("C:\\", "GBA", "READONLYREFERENCE",
                           "pokefirered", "src", "sprite.c")
        if not os.path.isfile(ref):
            self.skipTest("read-only reference pokefirered not present")
        with open(ref, encoding="utf-8", errors="replace") as f:
            text = f.read()
        # The untouched reference must contain the exact vanilla anchor.
        self.assertIn(sdp._VANILLA, text)
        self.assertFalse(sdp._is_applied_text(text))


if __name__ == "__main__":
    unittest.main(verbosity=2)
