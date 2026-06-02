"""Tests for the generic-loop-table presets in ``core/anim_table_upgrade.py``.

The generic loop tables are project-wide reusable presets that any sprite
with 2-8 frames can opt in to — distinct from the per-sprite cycle tables
that ``ensure_cycle_anim_table`` produces.  These tests cover:

  - The generator produces a sentinel-fenced block with both orderings
    (Sequential / Random) for every supported frame count.
  - Each sequential table loops frames 0..N-1 in order with the same
    per-frame hold.
  - Each random table is a long pre-shuffled sequence with no two
    consecutive frames the same.
  - The install is idempotent: a second call against the same file is a
    clean no-op (no duplicated block, no spurious write).
  - A re-install replaces the existing block cleanly (so changing the
    generator and re-running doesn't leave stale entries behind).
  - ``scan_anim_tables`` groups + labels the presets in a way the
    dropdown can present to the user.

Loaded with importlib like the other ``core/*`` tests so
``core/__init__.py`` (and its full PyQt + data-layer import chain) stays
out of the way.
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys
import tempfile
import unittest


_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_THIS_DIR, ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _load_module(rel_path: str, module_name: str):
    """Load a single ``core/*.py`` file without going through ``core/__init__.py``."""
    path = os.path.join(_ROOT, rel_path)
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


atu = _load_module("core/anim_table_upgrade.py", "anim_table_upgrade")


# Minimal seed file that is shaped enough to satisfy the patcher and the
# scanner: the scanner reads every ``sAnimTable_*`` block, and the patcher
# only looks for its own sentinel.  Including a single Standard-shape
# table here exercises the scan_anim_tables sort + label path too.
_SEED_ANIMS = """\
static const union AnimCmd sAnim_FaceSouth[] = {
    ANIMCMD_FRAME(0, 16),
    ANIMCMD_JUMP(0),
};

static const union AnimCmd *const sAnimTable_Standard[] = {
    [ANIM_STD_FACE_SOUTH] = sAnim_FaceSouth,
};

static const union AnimCmd *const sAnimTable_Inanimate[] = {
    [ANIM_STD_FACE_SOUTH] = sAnim_FaceSouth,
};
"""


def _make_project(tmp_root: str, seed: str = _SEED_ANIMS) -> str:
    """Create a minimal project tree with just the anims file the generator needs."""
    anims_dir = os.path.join(
        tmp_root, "src", "data", "object_events",
    )
    os.makedirs(anims_dir, exist_ok=True)
    anims_path = os.path.join(anims_dir, "object_event_anims.h")
    with open(anims_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(seed)
    return tmp_root


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


class GenericLoopTablesTests(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="psz_loops_")
        _make_project(self.tmp)
        self.anims_path = os.path.join(
            self.tmp, "src", "data", "object_events", "object_event_anims.h",
        )

    def tearDown(self) -> None:
        # Best-effort cleanup; on Windows files briefly hold locks.
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ─────────────────────────────── generator shape ────────────────────────

    def test_install_writes_both_orderings_for_every_supported_frame_count(self) -> None:
        ok, msgs = atu.ensure_generic_loop_tables(self.tmp)
        self.assertTrue(ok)
        self.assertEqual(len(msgs), 1)  # one summary message

        text = _read(self.anims_path)
        for n in atu.GENERIC_LOOP_FRAME_COUNTS:
            seq_sym = atu.generic_loop_table_symbol(n, randomized=False)
            rnd_sym = atu.generic_loop_table_symbol(n, randomized=True)
            self.assertIn(seq_sym + "[]", text,
                          f"missing sequential table for {n} frames")
            self.assertIn(rnd_sym + "[]", text,
                          f"missing random table for {n} frames")

    def test_sequential_table_loops_in_order(self) -> None:
        atu.ensure_generic_loop_tables(self.tmp)
        text = _read(self.anims_path)

        for n in atu.GENERIC_LOOP_FRAME_COUNTS:
            anim_name = f"sAnim_Loop{n}Sequential"
            # Match the anim definition body so we can read its frame list.
            m = re.search(
                re.escape(anim_name) + r"\[\]\s*=\s*\{([^}]*)\}",
                text, re.DOTALL,
            )
            self.assertIsNotNone(m, f"missing definition for {anim_name}")
            body = m.group(1)
            frame_indices = [int(x) for x in re.findall(
                r"ANIMCMD_FRAME\(\s*(\d+)", body)]
            self.assertEqual(
                frame_indices, list(range(n)),
                f"{anim_name}: expected frames 0..{n-1}, got {frame_indices}",
            )
            self.assertIn("ANIMCMD_JUMP(0)", body)

    def test_random_table_has_no_consecutive_duplicate_frames(self) -> None:
        atu.ensure_generic_loop_tables(self.tmp)
        text = _read(self.anims_path)

        for n in atu.GENERIC_LOOP_FRAME_COUNTS:
            anim_name = f"sAnim_Loop{n}Random"
            m = re.search(
                re.escape(anim_name) + r"\[\]\s*=\s*\{([^}]*)\}",
                text, re.DOTALL,
            )
            self.assertIsNotNone(m, f"missing definition for {anim_name}")
            body = m.group(1)
            frame_indices = [int(x) for x in re.findall(
                r"ANIMCMD_FRAME\(\s*(\d+)", body)]
            self.assertGreaterEqual(
                len(frame_indices), 2,
                f"{anim_name}: random sequence too short")
            # n == 2 reduces to ABABAB (only valid no-repeat sequence over 2
            # elements), so the consecutive-duplicate check trivially holds.
            for prev, cur in zip(frame_indices, frame_indices[1:]):
                self.assertNotEqual(
                    prev, cur,
                    f"{anim_name}: consecutive duplicate frame {cur}")
            # Wrap-around: ANIMCMD_JUMP(0) loops the last frame into the
            # first.  Sequences over 2+ elements should avoid first==last.
            if n > 2:
                self.assertNotEqual(
                    frame_indices[0], frame_indices[-1],
                    f"{anim_name}: first==last would flicker at the JUMP wrap")

    def test_every_direction_slot_points_at_the_same_loop(self) -> None:
        """Generic loops never hFlip and never branch on direction — every
        slot in the table points at the SAME ``sAnim_*`` symbol."""
        atu.ensure_generic_loop_tables(self.tmp)
        text = _read(self.anims_path)

        for n in atu.GENERIC_LOOP_FRAME_COUNTS:
            for randomized in (False, True):
                table_sym = atu.generic_loop_table_symbol(n, randomized)
                anim_sym = f"sAnim_Loop{n}{'Random' if randomized else 'Sequential'}"
                m = re.search(
                    re.escape(table_sym) + r"\[\]\s*=\s*\{([^}]*)\}",
                    text, re.DOTALL,
                )
                self.assertIsNotNone(m, f"missing table {table_sym}")
                body = m.group(1)
                # Every "[X] = Y," entry must have Y == anim_sym.
                entries = re.findall(r"\[\w+\]\s*=\s*(\w+)\s*,", body)
                self.assertGreater(len(entries), 0)
                for got in entries:
                    self.assertEqual(
                        got, anim_sym,
                        f"{table_sym}: slot points at {got}, expected {anim_sym}")

    def test_frame_durations_clamp_into_six_bit_field(self) -> None:
        """ANIMCMD_FRAME's duration is a 6-bit bitfield in the engine; values
        > 63 wrap silently in the ROM.  No generated frame should exceed 63."""
        atu.ensure_generic_loop_tables(self.tmp)
        text = _read(self.anims_path)
        durations = [int(x) for x in re.findall(
            r"ANIMCMD_FRAME\(\s*\d+\s*,\s*(\d+)", text)]
        self.assertTrue(durations, "expected at least one ANIMCMD_FRAME")
        self.assertTrue(all(1 <= d <= 63 for d in durations),
                        f"out-of-range duration: {[d for d in durations if d > 63]}")

    # ─────────────────────────────── idempotency ────────────────────────────

    def test_second_install_is_a_clean_noop(self) -> None:
        ok1, msgs1 = atu.ensure_generic_loop_tables(self.tmp)
        self.assertTrue(ok1)
        self.assertEqual(len(msgs1), 1)
        # Snapshot mtime + content; a no-op should change neither.
        mtime_before = os.path.getmtime(self.anims_path)
        content_before = _read(self.anims_path)

        ok2, msgs2 = atu.ensure_generic_loop_tables(self.tmp)
        self.assertTrue(ok2)
        self.assertEqual(msgs2, [],
                         "second install should report no changes")

        # File must not have been rewritten on the no-op.
        self.assertEqual(content_before, _read(self.anims_path))
        # mtime can be flaky on Windows for very-fast operations; comparing
        # content is the load-bearing assertion.

    def test_reinstall_replaces_block_without_duplicating(self) -> None:
        """If the sentinel block already exists, a fresh generator output
        replaces it wholesale — never appends a second copy."""
        atu.ensure_generic_loop_tables(self.tmp)
        text_after_first = _read(self.anims_path)

        # Tamper: insert a stray comment INSIDE the sentinel block so the
        # second install must rewrite to restore the canonical content.
        tampered = text_after_first.replace(
            "// >>> PorySuite-Z generic loop tables >>>",
            "// >>> PorySuite-Z generic loop tables >>>\n// TAMPERED",
            1,
        )
        with open(self.anims_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(tampered)

        ok, msgs = atu.ensure_generic_loop_tables(self.tmp)
        self.assertTrue(ok)
        self.assertEqual(len(msgs), 1, "tampered block should be refreshed")
        text_after_refresh = _read(self.anims_path)
        self.assertNotIn("// TAMPERED", text_after_refresh)

        # Sentinel must appear exactly once after refresh.
        self.assertEqual(
            text_after_refresh.count(
                "// >>> PorySuite-Z generic loop tables >>>"), 1)
        self.assertEqual(
            text_after_refresh.count(
                "// <<< PorySuite-Z generic loop tables <<<"), 1)

    # ─────────────────────────── scan + label output ────────────────────────

    def test_scan_groups_generic_loops_after_standard_before_inanimate(self) -> None:
        atu.ensure_generic_loop_tables(self.tmp)
        rows = atu.scan_anim_tables(self.tmp)
        order = [sym for sym, _label in rows]

        # Walk Cycle (standard NPC) sorts first.
        self.assertEqual(order[0], "sAnimTable_Standard")
        # Generic loops come next, by frame count then sequential-before-random.
        gens = [s for s in order if s.startswith("sAnimTable_Loop")]
        expected_gens: list = []
        for n in atu.GENERIC_LOOP_FRAME_COUNTS:
            expected_gens.append(atu.generic_loop_table_symbol(n, False))
            expected_gens.append(atu.generic_loop_table_symbol(n, True))
        self.assertEqual(gens, expected_gens,
                         "generic loops mis-ordered in dropdown")
        # Inanimate sorts after every generic loop.
        gen_last_idx = order.index(gens[-1])
        self.assertGreater(
            order.index("sAnimTable_Inanimate"), gen_last_idx,
            "Inanimate should sort below the generic loop group")

    def test_generic_loop_labels_are_human_readable(self) -> None:
        atu.ensure_generic_loop_tables(self.tmp)
        rows = atu.scan_anim_tables(self.tmp)
        labels = {sym: label for sym, label in rows}
        # Sequential variant.
        self.assertEqual(
            labels[atu.generic_loop_table_symbol(2, False)],
            "Generic Loop · 2 frames · sequential")
        # Random variant.
        self.assertEqual(
            labels[atu.generic_loop_table_symbol(4, True)],
            "Generic Loop · 4 frames · random pace")

    def test_per_sprite_cycle_labels_surface_source_sprite_name(self) -> None:
        """A pre-existing per-sprite cycle table should be labeled with the
        source sprite's name so the user knows it's NOT a reusable preset."""
        # Tamper-add a synthetic per-sprite cycle so the scanner sees it.
        text = _read(self.anims_path)
        text += (
            "\n\nstatic const union AnimCmd sAnim_FooCycle[] = {\n"
            "    ANIMCMD_FRAME(0, 16),\n"
            "    ANIMCMD_FRAME(1, 16),\n"
            "    ANIMCMD_JUMP(0),\n"
            "};\n"
            "static const union AnimCmd *const sAnimTable_FooCycle[] = {\n"
            "    [ANIM_STD_FACE_SOUTH] = sAnim_FooCycle,\n"
            "};\n"
        )
        with open(self.anims_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)

        rows = atu.scan_anim_tables(self.tmp)
        labels = {sym: label for sym, label in rows}
        self.assertIn("sAnimTable_FooCycle", labels)
        label = labels["sAnimTable_FooCycle"]
        # Label should mention the source sprite name AND its frame count.
        self.assertIn("Foo", label)
        self.assertIn("Sequential Cycle", label)
        self.assertIn("2 frames", label)


if __name__ == "__main__":
    unittest.main()
