"""Tests for ``core/npc_pause_engine_patch.py`` — the NPC pause-on-blocked
engine refactor.

Loaded with importlib (same approach as the other ``core/*`` tests) so
``core/__init__.py`` and its PyQt + data-layer chain stay out of the way.

The patcher chains into ``core.footprint_engine_patch.ensure_footprint_engine_support``
because the pause block references ``gObjectEventFootprints[]`` and
``ObjectEventFootprintHitsTile``.  Tests run that whole chain end-to-end:
real vanilla pokefirered → footprint engine installed → NPC pause patch
installed → assertions on the resulting source.

Anti-drift: ``_VANILLA_NPCTAKESTEP_BODY`` is asserted against the live
READONLYREFERENCE vanilla source so future upstream changes fail loudly.

A synthetic ``field_player_avatar.c`` is generated inline for the
escape-pass tests because the FreeStepNpc*Blocked functions are an
extension users add on top of vanilla pokefirered — they aren't in the
clean vanilla tree, so we can't copy them from READONLYREFERENCE.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
import unittest


_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_THIS_DIR, ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _load_module(rel_path: str, module_name: str):
    """Load a ``core/*.py`` without going through ``core/__init__.py``."""
    path = os.path.join(_ROOT, rel_path)
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# Load both the prereq and the unit under test.  The pause patcher
# imports ``core.footprint_engine_patch`` via its full name; pre-load
# it under that name so the import inside ``ensure_npc_pause_engine_support``
# resolves to the same module instance.
_load_module("core/footprint_engine_patch.py", "core.footprint_engine_patch")
patch = _load_module(
    "core/npc_pause_engine_patch.py", "npc_pause_engine_patch",
)


_VANILLA_ROOT = r"C:\GBA\READONLYREFERENCE\pokefirered"


# Files copied from READONLYREFERENCE into the temp project so the
# footprint installer (which the pause patcher chains into) has the
# vanilla anchors to match against.  field_player_avatar.c is NOT
# copied -- vanilla pokefirered doesn't have FreeStepNpc*Blocked, so
# the escape-pass patch is a silent no-op on a pure vanilla project.
_FILES_FROM_VANILLA = (
    os.path.join("src", "event_object_movement.c"),
)


def _materialise_vanilla_into(dest_root: str) -> None:
    for rel in _FILES_FROM_VANILLA:
        src_path = os.path.join(_VANILLA_ROOT, rel)
        dst_path = os.path.join(dest_root, rel)
        os.makedirs(os.path.dirname(dst_path), exist_ok=True)
        shutil.copy(src_path, dst_path)


# Synthetic minimal field_player_avatar.c containing JUST the two
# FreeStepNpc*Blocked functions in the exact shape the user's
# free-pixel-movement engine has them.  Mirrors the live patched
# state AFTER the footprint patcher has wrapped them with the
# PORYSUITE-FOOTPRINT free-collide fence.  Used to test that the
# escape-pass patcher wraps both blocks in one run.
_SYNTHETIC_FIELD_PLAYER = """\
// Synthetic minimal free-pixel-movement field_player_avatar.c stub
// for the NPC pause patcher tests.  Real projects have the full file.

#include "global.h"
#include "event_object_movement.h"

#define FREEMOVE_HB_LEFT  3
#define FREEMOVE_HB_RIGHT 12
#define FREEMOVE_HB_TOP   4
#define FREEMOVE_HB_BOT   15

static bool8 FreeStepNpcXBlocked(s8 dx)
{
    u8 i;
    const struct ObjectEvent *obj;
    s16 npcX, npcY;
    s16 pLeft, pRight, pTop, pBot;
    u8  playerElev;

    if (gSaveBlock2Ptr->debugNoclip)
        return FALSE;
    if (dx == 0)
        return FALSE;

    playerElev = gObjectEvents[gPlayerAvatar.objectEventId].currentElevation;
    pLeft  = gPlayerAvatar.pixelX + dx + FREEMOVE_HB_LEFT;
    pRight = gPlayerAvatar.pixelX + dx + FREEMOVE_HB_RIGHT;
    pTop   = gPlayerAvatar.pixelY      + FREEMOVE_HB_TOP;
    pBot   = gPlayerAvatar.pixelY      + FREEMOVE_HB_BOT;

    for (i = 0; i < OBJECT_EVENTS_COUNT; i++)
    {
        obj = &gObjectEvents[i];
        if (!obj->active || obj->isPlayer)
            continue;
        if (playerElev != 0 && obj->currentElevation != 0
         && playerElev != obj->currentElevation)
            continue;

        GetNpcInterpolatedPos(obj, &npcX, &npcY);

        if (pLeft  <= npcX + 14 && pRight >= npcX + 1
         && pTop   <= npcY + 14 && pBot   >= npcY + 1)
            return TRUE;
        // PORYSUITE-FOOTPRINT free-collide begin
        if (PlayerFootprintHitsObject(obj, npcX, npcY, pLeft, pRight, pTop, pBot))
            return TRUE;
        // PORYSUITE-FOOTPRINT free-collide end
    }
    return FALSE;
}

static bool8 FreeStepNpcYBlocked(s8 dy)
{
    u8 i;
    const struct ObjectEvent *obj;
    s16 npcX, npcY;
    s16 pLeft, pRight, pTop, pBot;
    u8  playerElev;

    if (gSaveBlock2Ptr->debugNoclip)
        return FALSE;
    if (dy == 0)
        return FALSE;

    playerElev = gObjectEvents[gPlayerAvatar.objectEventId].currentElevation;
    pLeft  = gPlayerAvatar.pixelX      + FREEMOVE_HB_LEFT;
    pRight = gPlayerAvatar.pixelX      + FREEMOVE_HB_RIGHT;
    pTop   = gPlayerAvatar.pixelY + dy + FREEMOVE_HB_TOP;
    pBot   = gPlayerAvatar.pixelY + dy + FREEMOVE_HB_BOT;

    for (i = 0; i < OBJECT_EVENTS_COUNT; i++)
    {
        obj = &gObjectEvents[i];
        if (!obj->active || obj->isPlayer)
            continue;
        if (playerElev != 0 && obj->currentElevation != 0
         && playerElev != obj->currentElevation)
            continue;

        GetNpcInterpolatedPos(obj, &npcX, &npcY);

        if (pLeft  <= npcX + 14 && pRight >= npcX + 1
         && pTop   <= npcY + 14 && pBot   >= npcY + 1)
            return TRUE;
        // PORYSUITE-FOOTPRINT free-collide begin
        if (PlayerFootprintHitsObject(obj, npcX, npcY, pLeft, pRight, pTop, pBot))
            return TRUE;
        // PORYSUITE-FOOTPRINT free-collide end
    }
    return FALSE;
}
"""


def _write_synthetic_field_player(dest_root: str) -> None:
    path = os.path.join(dest_root, "src", "field_player_avatar.c")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(_SYNTHETIC_FIELD_PLAYER)


class NpcPauseEnginePatchTests(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="porynpcpause_")
        _materialise_vanilla_into(self.tmp)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ── install detection ────────────────────────────────────────

    def test_not_installed_on_vanilla(self):
        self.assertFalse(patch.is_engine_installed(self.tmp))

    def test_install_succeeds_on_vanilla_but_skips_npctakestep_patch(self):
        # Vanilla pokefirered has no free-pixel-movement infrastructure
        # (no gPlayerAvatar.pixelX field, no FreeStepNpcXBlocked function).
        # The NpcTakeStep patch uses gPlayerAvatar.pixelX, which would
        # break the vanilla build with "no field 'pixelX' in struct
        # PlayerAvatar".  Installer MUST silently skip the NpcTakeStep
        # patch in this case — vanilla doesn't need the pause feature
        # anyway because tile-locked movement already prevents the
        # softlock this fixes.
        ok, _msgs = patch.ensure_npc_pause_engine_support(self.tmp)
        # Installer succeeded (silent no-op for the NpcTakeStep patch).
        self.assertTrue(ok)
        # But is_engine_installed reports False — the sentinel is NOT
        # present in event_object_movement.c because we skipped the
        # patch.  This is correct behaviour.
        self.assertFalse(patch.is_engine_installed(self.tmp))
        # And the file is BYTE-IDENTICAL to vanilla — the NpcTakeStep
        # body is unchanged.
        with open(
            os.path.join(self.tmp, "src", "event_object_movement.c"),
            encoding="utf-8",
        ) as f:
            patched = f.read()
        with open(
            os.path.join(
                _VANILLA_ROOT, "src", "event_object_movement.c"),
            encoding="utf-8",
        ) as f:
            vanilla = f.read()
        # The footprint engine's own patches WILL have landed (footprint
        # engine is vanilla-compatible by design), so the files won't be
        # byte-identical.  But the NpcTakeStep body MUST be the vanilla
        # one with no PORYSUITE-NPCPAUSE markers.
        self.assertNotIn("PORYSUITE-NPCPAUSE pause-step", patched)
        self.assertIn(patch._VANILLA_NPCTAKESTEP_BODY, patched)

    # ── NpcTakeStep — vanilla install ────────────────────────────

    def test_vanilla_anchor_matches_genuine_source(self):
        with open(
            os.path.join(_VANILLA_ROOT, "src", "event_object_movement.c"),
            encoding="utf-8",
        ) as f:
            text = f.read()
        self.assertIn(
            patch._VANILLA_NPCTAKESTEP_BODY, text,
            "Vanilla NpcTakeStep anchor has drifted — update "
            "_VANILLA_NPCTAKESTEP_BODY to match upstream",
        )

    def test_install_patches_vanilla_npctakestep(self):
        # Free-pixel-movement file present → NpcTakeStep patch lands.
        _write_synthetic_field_player(self.tmp)
        ok, _msgs = patch.ensure_npc_pause_engine_support(self.tmp)
        self.assertTrue(ok)
        with open(
            os.path.join(self.tmp, "src", "event_object_movement.c"),
            encoding="utf-8",
        ) as f:
            text = f.read()
        # Fenced block landed.
        self.assertIn("PORYSUITE-NPCPAUSE pause-step begin", text)
        self.assertIn("PORYSUITE-NPCPAUSE pause-step end", text)
        # Both branches of the pause check are present: anchor AND footprint.
        self.assertIn(
            "playerTileX == npcObj->currentCoords.x", text)
        self.assertIn(
            "ObjectEventFootprintHitsTile(npcObj, playerTileX, playerTileY)",
            text,
        )
        # The animPaused gate is wired in both directions.
        self.assertIn("sprite->animPaused = TRUE", text)
        self.assertIn("sprite->animPaused = FALSE", text)
        # The dispatch line that follows the fenced block is still there
        # (the patch inserts BEFORE it, doesn't replace it).
        self.assertIn(
            "sNpcStepFuncTables[sprite->tSpeed][sprite->tStepNo]"
            "(sprite, sprite->tDirection);",
            text,
        )

    def test_install_is_idempotent(self):
        _write_synthetic_field_player(self.tmp)
        patch.ensure_npc_pause_engine_support(self.tmp)
        path = os.path.join(self.tmp, "src", "event_object_movement.c")
        with open(path, encoding="utf-8") as f:
            first = f.read()
        ok, msgs = patch.ensure_npc_pause_engine_support(self.tmp)
        self.assertTrue(ok)
        with open(path, encoding="utf-8") as f:
            second = f.read()
        # File content must be byte-identical on a re-run.
        self.assertEqual(first, second)
        # Re-run reports no movement.c work (it can still report the
        # prereq footprint installer's no-ops as "(prereq)" lines, but
        # the movement.c message should be empty).
        movement_msgs = [m for m in msgs if "event_object_movement.c" in m]
        self.assertEqual(movement_msgs, [])

    def test_install_only_inserts_one_pause_block_on_rerun(self):
        # Triple-check the patch doesn't accidentally stack duplicates.
        _write_synthetic_field_player(self.tmp)
        patch.ensure_npc_pause_engine_support(self.tmp)
        patch.ensure_npc_pause_engine_support(self.tmp)
        patch.ensure_npc_pause_engine_support(self.tmp)
        with open(
            os.path.join(self.tmp, "src", "event_object_movement.c"),
            encoding="utf-8",
        ) as f:
            text = f.read()
        self.assertEqual(
            text.count("PORYSUITE-NPCPAUSE pause-step begin"), 1,
            "pause-step block was stacked across re-runs",
        )

    def test_vanilla_project_with_no_field_player_file_skips_patch(self):
        # Absolute vanilla case: no field_player_avatar.c at all
        # (extremely cut-down test project).  Installer must succeed
        # and the NpcTakeStep body must be unchanged.
        path = os.path.join(self.tmp, "src", "field_player_avatar.c")
        if os.path.exists(path):
            os.remove(path)
        ok, _msgs = patch.ensure_npc_pause_engine_support(self.tmp)
        self.assertTrue(ok)
        with open(
            os.path.join(self.tmp, "src", "event_object_movement.c"),
            encoding="utf-8",
        ) as f:
            text = f.read()
        self.assertNotIn("PORYSUITE-NPCPAUSE pause-step", text)
        self.assertIn(patch._VANILLA_NPCTAKESTEP_BODY, text)

    def test_vanilla_field_player_present_still_skips_patch(self):
        # field_player_avatar.c is present but VANILLA (no free-pixel-
        # movement symbols).  Still should skip the patch.
        vanilla_player_path = os.path.join(
            _VANILLA_ROOT, "src", "field_player_avatar.c")
        if os.path.exists(vanilla_player_path):
            dst = os.path.join(self.tmp, "src", "field_player_avatar.c")
            shutil.copy2(vanilla_player_path, dst)
            ok, _msgs = patch.ensure_npc_pause_engine_support(self.tmp)
            self.assertTrue(ok)
            with open(
                os.path.join(
                    self.tmp, "src", "event_object_movement.c"),
                encoding="utf-8",
            ) as f:
                text = f.read()
            self.assertNotIn("PORYSUITE-NPCPAUSE pause-step", text)
            self.assertIn(patch._VANILLA_NPCTAKESTEP_BODY, text)

    # ── NpcTakeStep — legacy 'Step 2.13' hand-edit upgrade ────────

    def test_legacy_step213_block_gets_replaced(self):
        # The legacy 'Step 2.13' hand-edit only exists on projects with
        # free-pixel-movement (it's a precursor to the pause feature
        # users like InnerMobius hand-rolled before the patcher).  Add
        # the synthetic field_player_avatar.c so the gate lets us in.
        _write_synthetic_field_player(self.tmp)
        # Stuff a legacy hand-edit into the temp NpcTakeStep so the
        # patcher's upgrade branch fires.
        path = os.path.join(self.tmp, "src", "event_object_movement.c")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        legacy_npc = (
            patch._VANILLA_NPCTAKESTEP_BODY.replace(
                "    if (sprite->tStepNo >= sStepTimes[sprite->tSpeed])\n"
                "        return FALSE;\n"
                "\n"
                "    sNpcStepFuncTables[sprite->tSpeed][sprite->tStepNo]"
                "(sprite, sprite->tDirection);\n",
                "    if (sprite->tStepNo >= sStepTimes[sprite->tSpeed])\n"
                "        return FALSE;\n"
                "\n"
                + patch._LEGACY_STEP213_BLOCK
                + "    sNpcStepFuncTables[sprite->tSpeed][sprite->tStepNo]"
                  "(sprite, sprite->tDirection);\n",
            )
        )
        text = text.replace(patch._VANILLA_NPCTAKESTEP_BODY, legacy_npc, 1)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        # Sanity: legacy block IS there before patching.
        with open(path, encoding="utf-8") as f:
            self.assertIn("Step 2.13", f.read())

        ok, _msgs = patch.ensure_npc_pause_engine_support(self.tmp)
        self.assertTrue(ok)
        with open(path, encoding="utf-8") as f:
            after = f.read()
        # Legacy comment is GONE -- the fenced version replaced it
        # (no duplicate "Step 2.13" wording survives).
        self.assertNotIn("Step 2.13", after)
        # Sentinel-fenced version IS there.
        self.assertIn("PORYSUITE-NPCPAUSE pause-step begin", after)
        # Footprint-aware branch is there (the upgrade is a strict
        # superset of the legacy anchor-only check).
        self.assertIn(
            "ObjectEventFootprintHitsTile(npcObj, playerTileX, playerTileY)",
            after,
        )

    # ── NpcTakeStep — refusal on unrecognised hand-edit ──────────

    def test_refuses_when_npctakestep_modified_beyond_recognition(self):
        # Refusal only fires when the patcher actually tries to install
        # — gated on free-pixel-movement being present.  Add the
        # synthetic field_player_avatar.c so the gate lets us in.
        _write_synthetic_field_player(self.tmp)
        path = os.path.join(self.tmp, "src", "event_object_movement.c")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        # Replace NpcTakeStep with a hand-edited version that matches
        # NEITHER the vanilla shape NOR the legacy 'Step 2.13' block.
        garbled = patch._VANILLA_NPCTAKESTEP_BODY.replace(
            "if (sprite->tStepNo >= sStepTimes[sprite->tSpeed])",
            "if (sprite->tStepNo > sStepTimes[sprite->tSpeed])  // some hand edit",
            1,
        )
        text = text.replace(patch._VANILLA_NPCTAKESTEP_BODY, garbled, 1)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        # The footprint installer should still succeed (it doesn't
        # touch NpcTakeStep), but the NPC pause installer should
        # surface the refusal.
        ok, msgs = patch.ensure_npc_pause_engine_support(self.tmp)
        self.assertFalse(ok)
        self.assertTrue(
            any("NpcTakeStep does not match" in m for m in msgs),
            f"expected refusal message, got: {msgs}",
        )

    # ── FreeStepNpc*Blocked — v0 → v2 fresh install ───────────────

    def test_v0_install_unifies_anchor_and_footprint_into_escape_or_collide(self):
        _write_synthetic_field_player(self.tmp)
        ok, _msgs = patch.ensure_npc_pause_engine_support(self.tmp)
        self.assertTrue(ok)
        with open(
            os.path.join(self.tmp, "src", "field_player_avatar.c"),
            encoding="utf-8",
        ) as f:
            text = f.read()
        # Both functions should have the v2 sentinel pair (begin + end)
        # exactly once each (= 4 occurrences total: 2 begin + 2 end).
        self.assertEqual(
            text.count("PORYSUITE-NPCPAUSE escape-or-collide begin"), 2)
        self.assertEqual(
            text.count("PORYSUITE-NPCPAUSE escape-or-collide end"), 2)
        # The old standalone anchor inset check is GONE — folded into
        # the fenced block.  Outside the v2 block, the original
        # "if (pLeft  <= npcX + 14 && pRight >= npcX + 1\n         && pTop"
        # 3-liner should no longer appear in the file.
        # (Detect: the v0 anchor-only chunk shouldn't be present anywhere
        # in the file — only its rewritten v2 form should be.)
        self.assertNotIn(
            "        if (pLeft  <= npcX + 14 && pRight >= npcX + 1\n"
            "         && pTop   <= npcY + 14 && pBot   >= npcY + 1)\n"
            "            return TRUE;\n"
            "        // PORYSUITE-FOOTPRINT free-collide begin\n"
            "        if (PlayerFootprintHitsObject(obj, npcX, npcY, pLeft, pRight, pTop, pBot))\n"
            "            return TRUE;\n",
            text,
            "v0 anchor+footprint pair survived the install — should have been replaced",
        )
        # The escape-pass guard captures the CURRENT (pre-move) hitbox
        # so overlap detection runs independently of the move attempt.
        self.assertIn("s16 curL = gPlayerAvatar.pixelX + FREEMOVE_HB_LEFT;",
                      text)
        self.assertIn("s16 curB = gPlayerAvatar.pixelY + FREEMOVE_HB_BOT;",
                      text)
        # The "already" overlap test unifies anchor AND footprint.
        self.assertIn(
            "bool8 already =\n"
            "                (curL <= npcX + 14 && curR >= npcX + 1\n"
            "                 && curT <= npcY + 14 && curB >= npcY + 1)\n"
            "                || PlayerFootprintHitsObject(obj, npcX, npcY, curL, curR, curT, curB);",
            text,
        )

    # ── FreeStepNpc*Blocked — v1 → v2 upgrade ─────────────────────

    def test_v1_legacy_block_gets_upgraded_to_v2(self):
        # Write a synthetic file that's already in v1 state (anchor +
        # v1 escape-pass-footprint-only block).
        _write_synthetic_field_player(self.tmp)
        path = os.path.join(self.tmp, "src", "field_player_avatar.c")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        # Replace the v0 chunks with v1 chunks before running the patcher.
        text = text.replace(patch._V0_BEFORE_BLOCK, patch._V1_BEFORE_BLOCK)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        # Sanity: file is now in v1 state (escape-pass marker present, no v2).
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("PORYSUITE-NPCPAUSE escape-pass: skip this NPC", text)
        self.assertNotIn("PORYSUITE-NPCPAUSE escape-or-collide", text)

        ok, _msgs = patch.ensure_npc_pause_engine_support(self.tmp)
        self.assertTrue(ok)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        # After upgrade: v1 marker gone, v2 marker present in both X and Y.
        self.assertNotIn("PORYSUITE-NPCPAUSE escape-pass: skip this NPC", text)
        self.assertEqual(
            text.count("PORYSUITE-NPCPAUSE escape-or-collide begin"), 2)

    # ── Idempotency ──────────────────────────────────────────────

    def test_escape_or_collide_is_idempotent(self):
        _write_synthetic_field_player(self.tmp)
        patch.ensure_npc_pause_engine_support(self.tmp)
        path = os.path.join(self.tmp, "src", "field_player_avatar.c")
        with open(path, encoding="utf-8") as f:
            first = f.read()
        # Re-run -- file should be byte-identical.
        patch.ensure_npc_pause_engine_support(self.tmp)
        with open(path, encoding="utf-8") as f:
            second = f.read()
        self.assertEqual(first, second)
        # Sentinel count stays at 2 across re-runs (not 4) -- count-based
        # idempotency catches re-entry.
        self.assertEqual(
            second.count("PORYSUITE-NPCPAUSE escape-or-collide begin"), 2)

    def test_freestep_patch_is_silent_noop_when_file_missing(self):
        # No field_player_avatar.c at all -- vanilla pokefirered case.
        ok, msgs = patch.ensure_npc_pause_engine_support(self.tmp)
        self.assertTrue(ok)
        # No error message about field_player_avatar.c (silent no-op).
        self.assertFalse(
            any("field_player_avatar" in m for m in msgs),
            f"expected silent no-op, got: {msgs}",
        )

    # ── Cross-file: full chain ────────────────────────────────────

    def test_full_install_chain_lands_both_files(self):
        _write_synthetic_field_player(self.tmp)
        ok, msgs = patch.ensure_npc_pause_engine_support(self.tmp)
        self.assertTrue(ok)
        # Both files get reported at LEAST once -- footprint installer
        # also touches event_object_movement.c (includes + helper +
        # collision patch), so movement.c usually shows up twice
        # (once from the footprint chain via "(prereq)" prefix, once
        # from this patcher's own NpcTakeStep edit).
        self.assertTrue(
            any("event_object_movement.c" in m
                and "NPCPAUSE" in m for m in msgs),
            f"expected an NPC-pause-attributed movement.c message, "
            f"got: {msgs}",
        )
        self.assertTrue(
            any("field_player_avatar.c" in m for m in msgs),
            f"expected a field_player_avatar.c message, got: {msgs}",
        )


if __name__ == "__main__":
    unittest.main()
