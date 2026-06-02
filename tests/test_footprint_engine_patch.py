"""Tests for ``core/footprint_engine_patch.py`` — the Phase 7b engine
refactor.

Loaded with importlib like the other ``core/*`` tests so ``core/__init__.py``
(and its full PyQt + data-layer import chain) stays out of the way.  The
genuine vanilla pokefirered tree at READONLYREFERENCE is the reference —
tests copy only the files the patcher touches into a temp dir, apply
the patch, and verify the result.  One test asserts the patcher's
byte-exact ``_VANILLA_GETPOS_BODY`` anchor still matches the live
pokefirered source, so upstream drift fails loudly.
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
    """Load a single ``core/*.py`` file without going through ``core/__init__.py``.

    The footprint patcher has stdlib-only imports, so this is a clean
    way to run the tests in environments that lack PorySuite's full
    PyQt + project-loader stack.
    """
    path = os.path.join(_ROOT, rel_path)
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


patch = _load_module("core/footprint_engine_patch.py", "footprint_engine_patch")


_VANILLA_ROOT = r"C:\GBA\READONLYREFERENCE\pokefirered"

# Files the patcher touches.  Copy only these into the temp dir;
# everything else can stay unset because the patcher only ever opens
# these exact paths.
_FILES_TO_COPY = (
    os.path.join("src", "event_object_movement.c"),
)


def _materialise_vanilla_into(dest_root: str) -> None:
    """Copy the files the patcher reads/writes into ``dest_root`` so a
    test can apply the patch against a virgin copy of pokefirered.
    """
    for rel in _FILES_TO_COPY:
        src_path = os.path.join(_VANILLA_ROOT, rel)
        dst_path = os.path.join(dest_root, rel)
        os.makedirs(os.path.dirname(dst_path), exist_ok=True)
        shutil.copy(src_path, dst_path)


class FootprintEnginePatchTests(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="poryfootprint_")
        _materialise_vanilla_into(self.tmp)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ── install ────────────────────────────────────────────────────

    def test_not_installed_on_vanilla(self):
        self.assertFalse(patch.is_engine_installed(self.tmp))

    def test_install_creates_struct_header_with_signature(self):
        ok, _msgs = patch.ensure_footprint_engine_support(self.tmp)
        self.assertTrue(ok)
        header = os.path.join(self.tmp, "include", "object_footprint.h")
        self.assertTrue(os.path.isfile(header))
        with open(header, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("PORYSUITE-FOOTPRINT v1", text)
        self.assertIn("struct ObjectFootprint", text)
        self.assertIn("extern const struct ObjectFootprint *const gObjectEventFootprints[]", text)
        self.assertTrue(patch.is_engine_installed(self.tmp))

    def test_install_seeds_empty_data_file(self):
        patch.ensure_footprint_engine_support(self.tmp)
        data = os.path.join(
            self.tmp, "src", "data", "object_events", "object_event_footprints.h",
        )
        self.assertTrue(os.path.isfile(data))
        with open(data, encoding="utf-8") as f:
            text = f.read()
        # The seed array MUST be sized [NUM_OBJ_EVENT_GFX] so any graphicsId
        # lookup before footprints are authored returns NULL instead of
        # reading off the end of an empty array.  Without the explicit
        # size, even an empty table can cause OOB reads if the C compiler
        # treats `[] = {}` as a zero-length array on some toolchains.
        self.assertIn(
            "gObjectEventFootprints[NUM_OBJ_EVENT_GFX]", text)
        self.assertIn("PORYSUITE-GEN BEGIN table", text)
        self.assertIn("PORYSUITE-GEN END table", text)

    # ── movement.c ────────────────────────────────────────────────

    def test_install_patches_movement_c(self):
        ok, _msgs = patch.ensure_footprint_engine_support(self.tmp)
        self.assertTrue(ok)
        with open(
            os.path.join(self.tmp, "src", "event_object_movement.c"),
            encoding="utf-8",
        ) as f:
            text = f.read()
        # Both includes landed.
        self.assertIn('#include "object_footprint.h"', text)
        self.assertIn(
            '#include "data/object_events/object_event_footprints.h"', text
        )
        # Helper installed with the BEGIN sentinel.
        self.assertIn("PORYSUITE-FOOTPRINT helper begin", text)
        self.assertIn(
            "static bool8 ObjectEventFootprintHitsTile", text,
        )
        # GetObjectEventIdByPosition gained the footprint branch (the
        # A-button interaction lookup; without it the player can talk
        # to the anchor tile only, not any painted cell).
        self.assertIn("PORYSUITE-FOOTPRINT interact begin", text)
        self.assertIn(
            "ObjectEventFootprintHitsTile(&gObjectEvents[i]", text,
        )
        # DoesObjectCollideWithObjectAt gained the footprint branch
        # (player movement collision — the part that actually blocks
        # the player from walking into a painted cell in-game).
        self.assertIn("PORYSUITE-FOOTPRINT collide begin", text)
        self.assertIn(
            "ObjectEventFootprintHitsTile(curObject, x, y)", text,
        )
        # Vanilla anchor / elevation match in GetObjectEventIdByPosition
        # is preserved (the patch is additive; NULL-footprint sprites
        # behave exactly like un-patched pokefirered).
        self.assertIn(
            "gObjectEvents[i].currentCoords.x == x\n"
            "             && gObjectEvents[i].currentCoords.y == y\n"
            "             && ObjectEventDoesElevationMatch(",
            text,
        )
        # And the vanilla previousCoords/currentCoords pair-match in
        # DoesObjectCollideWithObjectAt is preserved too -- footprint
        # adds a path, doesn't replace one.
        self.assertIn(
            "curObject->previousCoords.x == x && curObject->previousCoords.y == y",
            text,
        )

    # ── idempotency ───────────────────────────────────────────────

    def test_install_is_idempotent(self):
        ok1, msgs1 = patch.ensure_footprint_engine_support(self.tmp)
        self.assertTrue(ok1)
        self.assertTrue(msgs1, "first install should report changes")

        # Capture every file the patcher could touch.
        snapshot = {}
        for rel in (
            os.path.join("include", "object_footprint.h"),
            os.path.join("src", "data", "object_events", "object_event_footprints.h"),
            os.path.join("src", "event_object_movement.c"),
        ):
            p = os.path.join(self.tmp, rel)
            with open(p, encoding="utf-8") as f:
                snapshot[rel] = f.read()

        # Re-run.  Should not change a single byte.
        ok2, msgs2 = patch.ensure_footprint_engine_support(self.tmp)
        self.assertTrue(ok2)
        self.assertEqual(msgs2, [], "second install should be a clean no-op")

        for rel, before in snapshot.items():
            p = os.path.join(self.tmp, rel)
            with open(p, encoding="utf-8") as f:
                after = f.read()
            self.assertEqual(before, after, f"{rel} changed on re-run")

    # ── data-file preservation ────────────────────────────────────

    def test_seed_does_not_overwrite_existing_data_file(self):
        # A project that already has authored footprints must NEVER have
        # its data file clobbered by the install step -- the save path
        # owns that file from first use onward.
        data_path = os.path.join(
            self.tmp, "src", "data", "object_events", "object_event_footprints.h",
        )
        os.makedirs(os.path.dirname(data_path), exist_ok=True)
        sentinel = "// USER CONTENT -- DO NOT TOUCH\n"
        with open(data_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(sentinel)

        ok, _msgs = patch.ensure_footprint_engine_support(self.tmp)
        self.assertTrue(ok)

        with open(data_path, encoding="utf-8") as f:
            self.assertEqual(f.read(), sentinel)

    # ── upstream-drift guard ──────────────────────────────────────

    def test_vanilla_anchor_matches_genuine_source(self):
        # If pokefirered upstream ever changes GetObjectEventIdByPosition,
        # the byte-exact anchor we substitute against stops matching
        # and patching would either silently fail or apply to the wrong
        # function.  Fail loudly here instead.
        with open(
            os.path.join(_VANILLA_ROOT, "src", "event_object_movement.c"),
            encoding="utf-8",
        ) as f:
            vanilla = f.read()
        self.assertIn(
            patch._VANILLA_GETPOS_BODY,
            vanilla,
            "footprint_engine_patch._VANILLA_GETPOS_BODY no longer matches "
            "the genuine pokefirered GetObjectEventIdByPosition -- "
            "upstream changed and the patcher's anchor needs updating "
            "before it can apply safely.",
        )

    def test_vanilla_collide_anchor_matches_genuine_source(self):
        # Same upstream-drift guard for the movement-collision function
        # the second patch hits.  Without this the patcher could silently
        # mis-apply -- exactly the regression that produced the
        # "footprint paints but the player walks through anyway" bug
        # before the collide-patch was added.
        with open(
            os.path.join(_VANILLA_ROOT, "src", "event_object_movement.c"),
            encoding="utf-8",
        ) as f:
            vanilla = f.read()
        self.assertIn(
            patch._VANILLA_COLLIDE_BODY,
            vanilla,
            "footprint_engine_patch._VANILLA_COLLIDE_BODY no longer matches "
            "the genuine pokefirered DoesObjectCollideWithObjectAt -- "
            "upstream changed and the patcher's anchor needs updating.",
        )

    def test_install_refuses_when_function_already_modified(self):
        # If someone has hand-edited GetObjectEventIdByPosition out
        # from under us, the patcher must refuse rather than silently
        # mis-apply.
        mc = os.path.join(self.tmp, "src", "event_object_movement.c")
        with open(mc, encoding="utf-8") as f:
            text = f.read()
        modified = text.replace(
            "u8 GetObjectEventIdByPosition(u16 x, u16 y, u8 elevation)",
            "u8 GetObjectEventIdByPosition(u16 x, u16 y, u8 elevation) /* user-edited */",
        )
        with open(mc, "w", encoding="utf-8", newline="\n") as f:
            f.write(modified)
        ok, msgs = patch.ensure_footprint_engine_support(self.tmp)
        self.assertFalse(ok, msgs)
        # Includes + struct header may have landed before the failure;
        # the function-body edit must NOT have.
        with open(mc, encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("ObjectEventFootprintHitsTile", text)

    # ── free-movement collision (optional) ───────────────────────

    def test_vanilla_project_skips_field_player_avatar_silently(self):
        # Vanilla pokefirered has no FreeStepNpcXBlocked -- the
        # patcher's player-avatar pass must silently skip and the
        # vanilla DoesObjectCollideWithObjectAt patch (which IS
        # applied) covers movement collision.
        ok, msgs = patch.ensure_footprint_engine_support(self.tmp)
        self.assertTrue(ok, msgs)
        # No messages mentioning field_player_avatar.c at all.
        self.assertFalse(
            any("field_player_avatar.c" in m for m in msgs),
            f"vanilla project should not touch field_player_avatar.c (got: {msgs})",
        )

    def test_free_movement_project_patches_field_player_avatar(self):
        # Project WITH FreeStepNpcXBlocked / YBlocked: the patcher
        # installs the helper, includes the struct header, and extends
        # BOTH overlap checks in one pass (single str.replace catches
        # both byte-identical occurrences).
        player_path = os.path.join(
            self.tmp, "src", "field_player_avatar.c",
        )
        # Synthetic free-movement source: minimal but contains every
        # anchor the patcher reads.
        synthetic = (
            '#include "global.h"\n'
            '#include "event_object_movement.h"\n'
            '#include "fieldmap.h"\n'
            '\n'
            'static bool8 FreeStepNpcXBlocked(s8 dx)\n'
            '{\n'
            '    u8 i;\n'
            '    const struct ObjectEvent *obj;\n'
            '    s16 npcX, npcY;\n'
            '    s16 pLeft, pRight, pTop, pBot;\n'
            '    /* ... pixel calc elided ... */\n'
            '    for (i = 0; i < OBJECT_EVENTS_COUNT; i++)\n'
            '    {\n'
            '        obj = &gObjectEvents[i];\n'
            '        if (!obj->active) continue;\n'
            '        GetNpcInterpolatedPos(obj, &npcX, &npcY);\n'
            '\n'
            '        if (pLeft  <= npcX + 14 && pRight >= npcX + 1\n'
            '         && pTop   <= npcY + 14 && pBot   >= npcY + 1)\n'
            '            return TRUE;\n'
            '    }\n'
            '    return FALSE;\n'
            '}\n'
            '\n'
            'static bool8 FreeStepNpcYBlocked(s8 dy)\n'
            '{\n'
            '    u8 i;\n'
            '    const struct ObjectEvent *obj;\n'
            '    s16 npcX, npcY;\n'
            '    s16 pLeft, pRight, pTop, pBot;\n'
            '    /* ... pixel calc elided ... */\n'
            '    for (i = 0; i < OBJECT_EVENTS_COUNT; i++)\n'
            '    {\n'
            '        obj = &gObjectEvents[i];\n'
            '        if (!obj->active) continue;\n'
            '        GetNpcInterpolatedPos(obj, &npcX, &npcY);\n'
            '\n'
            '        if (pLeft  <= npcX + 14 && pRight >= npcX + 1\n'
            '         && pTop   <= npcY + 14 && pBot   >= npcY + 1)\n'
            '            return TRUE;\n'
            '    }\n'
            '    return FALSE;\n'
            '}\n'
        )
        with open(player_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(synthetic)

        ok, msgs = patch.ensure_footprint_engine_support(self.tmp)
        self.assertTrue(ok, msgs)

        with open(player_path, encoding="utf-8") as f:
            text = f.read()

        # All three edits landed.
        self.assertIn('#include "object_footprint.h"', text)
        self.assertIn("PORYSUITE-FOOTPRINT free-helper begin", text)
        self.assertIn(
            "static bool8 PlayerFootprintHitsObject", text,
        )
        # The overlap-check extension applied to BOTH functions --
        # the BEGIN sentinel appears once per function = twice total.
        self.assertEqual(
            text.count("PORYSUITE-FOOTPRINT free-collide begin"), 2,
            "free-collide BEGIN should appear in both X and Y functions",
        )
        # Vanilla overlap check preserved both times -- patch is
        # additive, not a replacement.
        self.assertEqual(
            text.count("pLeft  <= npcX + 14 && pRight >= npcX + 1"), 2,
        )

    # ── PreScriptAlign skip (optional) ───────────────────────────

    def test_vanilla_project_skips_field_control_avatar_silently(self):
        # Vanilla pokefirered has no StartPreScriptAlign -- the patcher
        # must silently skip field_control_avatar.c.  The Phase 7b
        # patches on event_object_movement.c still cover NPC-vs-NPC
        # vanilla collision; nothing about that depends on the script-
        # start alignment hook.
        ok, msgs = patch.ensure_footprint_engine_support(self.tmp)
        self.assertTrue(ok, msgs)
        self.assertFalse(
            any("field_control_avatar.c" in m for m in msgs),
            f"vanilla project should not touch field_control_avatar.c (got: {msgs})",
        )

    def test_prescript_align_project_patches_field_control_avatar(self):
        # Synthetic free-script source with the StartPreScriptAlign
        # task and the GetInteractedObjectEventScript anchors -- the
        # patcher should add all four edits.
        control_path = os.path.join(
            self.tmp, "src", "field_control_avatar.c",
        )
        synthetic = (
            '#include "global.h"\n'
            '#include "event_object_movement.h"\n'
            '#include "field_player_avatar.h"\n'
            '#include "script.h"\n'
            '\n'
            'static void StartPreScriptAlign(const u8 *script);\n'
            'static void Task_PreScriptAlign(u8 taskId);\n'
            '\n'
            'static const u8 *GetInteractedObjectEventScript(struct MapPosition *position, u8 metatileBehavior, u8 direction)\n'
            '{\n'
            '    u8 objectEventId = GetObjectEventIdByPosition(position->x, position->y, position->elevation);\n'
            '    if (objectEventId == OBJECT_EVENTS_COUNT) return NULL;\n'
            '\n'
            '    gSelectedObjectEvent = objectEventId;\n'
            '    gSpecialVar_LastTalked = gObjectEvents[objectEventId].localId;\n'
            '    gSpecialVar_Facing = direction;\n'
            '\n'
            '    return GetObjectEventScriptPointerByObjectEventId(objectEventId);\n'
            '}\n'
            '\n'
            'static void StartPreScriptAlign(const u8 *script)\n'
            '{\n'
            '    struct ObjectEvent *objEvent = &gObjectEvents[gPlayerAvatar.objectEventId];\n'
            '    s16 targetX = (s16)(objEvent->currentCoords.x << 4);\n'
            '    s16 targetY = (s16)(objEvent->currentCoords.y << 4);\n'
            '\n'
            '    // If already aligned, start the script immediately — no task overhead.\n'
            '    if (gPlayerAvatar.pixelX == targetX && gPlayerAvatar.pixelY == targetY)\n'
            '    {\n'
            '        ScriptContext_SetupScript(script);\n'
            '        return;\n'
            '    }\n'
            '    /* ... task spawn elided ... */\n'
            '}\n'
        )
        with open(control_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(synthetic)

        ok, msgs = patch.ensure_footprint_engine_support(self.tmp)
        self.assertTrue(ok, msgs)

        with open(control_path, encoding="utf-8") as f:
            text = f.read()

        # All four edits landed.
        self.assertIn('#include "object_footprint.h"', text)
        self.assertIn(
            "static bool8 sPorySuiteFootprintSkipAlign = FALSE;", text,
        )
        self.assertIn(
            "PORYSUITE-FOOTPRINT skip-align (flag)", text,
        )
        self.assertIn(
            "PORYSUITE-FOOTPRINT skip-align (mark)", text,
        )
        self.assertIn(
            "PORYSUITE-FOOTPRINT skip-align (read)", text,
        )
        # The flag-read path runs ScriptContext_SetupScript when the
        # mark fired, so a non-NULL footprint sprite never starts the
        # align task.
        self.assertIn(
            "if (sPorySuiteFootprintSkipAlign)", text,
        )
        # Vanilla "already aligned" fast path is preserved -- the new
        # block is inserted ABOVE it, not in place of it.
        self.assertIn(
            "if (gPlayerAvatar.pixelX == targetX && gPlayerAvatar.pixelY == targetY)",
            text,
        )
        # GetInteractedObjectEventScript still does its vanilla
        # assignments; the patch is additive.
        self.assertIn("gSelectedObjectEvent = objectEventId;", text)
        self.assertIn("gSpecialVar_LastTalked = gObjectEvents[objectEventId]", text)

    def test_prescript_align_patches_both_interact_functions(self):
        # Regression: the vanilla 3-line assignment block appears in
        # BOTH GetInteractedObjectEventScript (the single-player A-button
        # path) and GetInteractedLinkPlayerScript (the link-cable
        # multiplayer path).  An earlier draft used replace(..., 1) and
        # only patched the first occurrence -- which happens to be the
        # link-cable function -- leaving the single-player schule
        # softlock unfixed.  The corrected patcher must extend BOTH.
        control_path = os.path.join(
            self.tmp, "src", "field_control_avatar.c",
        )
        synthetic = (
            '#include "global.h"\n'
            '#include "field_player_avatar.h"\n'
            '\n'
            'static void StartPreScriptAlign(const u8 *script);\n'
            'static void Task_PreScriptAlign(u8 taskId);\n'
            '\n'
            'const u8 *GetInteractedLinkPlayerScript(struct MapPosition *position, u8 metatileBehavior, u8 direction)\n'
            '{\n'
            '    u8 objectEventId = GetObjectEventIdByPosition(position->x, position->y, position->elevation);\n'
            '    gSelectedObjectEvent = objectEventId;\n'
            '    gSpecialVar_LastTalked = gObjectEvents[objectEventId].localId;\n'
            '    gSpecialVar_Facing = direction;\n'
            '    return GetObjectEventScriptPointerByObjectEventId(objectEventId);\n'
            '}\n'
            '\n'
            'static const u8 *GetInteractedObjectEventScript(struct MapPosition *position, u8 metatileBehavior, u8 direction)\n'
            '{\n'
            '    u8 objectEventId = GetObjectEventIdByPosition(position->x, position->y, position->elevation);\n'
            '    gSelectedObjectEvent = objectEventId;\n'
            '    gSpecialVar_LastTalked = gObjectEvents[objectEventId].localId;\n'
            '    gSpecialVar_Facing = direction;\n'
            '    return GetObjectEventScriptPointerByObjectEventId(objectEventId);\n'
            '}\n'
            '\n'
            'static void StartPreScriptAlign(const u8 *script)\n'
            '{\n'
            '    struct ObjectEvent *objEvent = &gObjectEvents[gPlayerAvatar.objectEventId];\n'
            '    s16 targetX = (s16)(objEvent->currentCoords.x << 4);\n'
            '    s16 targetY = (s16)(objEvent->currentCoords.y << 4);\n'
            '\n'
            '    // If already aligned, start the script immediately — no task overhead.\n'
            '    if (gPlayerAvatar.pixelX == targetX && gPlayerAvatar.pixelY == targetY) return;\n'
            '}\n'
        )
        with open(control_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(synthetic)

        ok, _msgs = patch.ensure_footprint_engine_support(self.tmp)
        self.assertTrue(ok)

        with open(control_path, encoding="utf-8") as f:
            text = f.read()
        # EXACTLY two mark blocks -- one per function, no duplicates.
        self.assertEqual(
            text.count("PORYSUITE-FOOTPRINT skip-align (mark) begin"), 2,
            "patcher should mark both interact functions exactly once each",
        )
        # And a re-run must not change a thing.
        before = text
        ok2, msgs2 = patch.ensure_footprint_engine_support(self.tmp)
        with open(control_path, encoding="utf-8") as f:
            after = f.read()
        self.assertTrue(ok2)
        self.assertEqual(before, after, "second run added a duplicate mark block")
        self.assertEqual(msgs2, [])

    def test_prescript_align_install_is_idempotent(self):
        # Same synthetic fixture; re-running the patcher must not
        # duplicate any of the four edits.
        control_path = os.path.join(
            self.tmp, "src", "field_control_avatar.c",
        )
        synthetic = (
            '#include "global.h"\n'
            '#include "field_player_avatar.h"\n'
            '\n'
            'static void StartPreScriptAlign(const u8 *script);\n'
            'static void Task_PreScriptAlign(u8 taskId);\n'
            '\n'
            'static const u8 *GetInteractedObjectEventScript(struct MapPosition *position, u8 metatileBehavior, u8 direction)\n'
            '{\n'
            '    u8 objectEventId = GetObjectEventIdByPosition(position->x, position->y, position->elevation);\n'
            '    gSelectedObjectEvent = objectEventId;\n'
            '    gSpecialVar_LastTalked = gObjectEvents[objectEventId].localId;\n'
            '    gSpecialVar_Facing = direction;\n'
            '    return NULL;\n'
            '}\n'
            '\n'
            'static void StartPreScriptAlign(const u8 *script)\n'
            '{\n'
            '    struct ObjectEvent *objEvent = &gObjectEvents[gPlayerAvatar.objectEventId];\n'
            '    s16 targetX = (s16)(objEvent->currentCoords.x << 4);\n'
            '    s16 targetY = (s16)(objEvent->currentCoords.y << 4);\n'
            '\n'
            '    // If already aligned, start the script immediately — no task overhead.\n'
            '    if (gPlayerAvatar.pixelX == targetX && gPlayerAvatar.pixelY == targetY) return;\n'
            '}\n'
        )
        with open(control_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(synthetic)

        patch.ensure_footprint_engine_support(self.tmp)
        with open(control_path, encoding="utf-8") as f:
            first = f.read()
        ok2, msgs2 = patch.ensure_footprint_engine_support(self.tmp)
        with open(control_path, encoding="utf-8") as f:
            second = f.read()
        self.assertTrue(ok2)
        self.assertEqual(first, second, "second run mutated field_control_avatar.c")
        # No duplicate fence blocks.
        self.assertEqual(
            second.count("PORYSUITE-FOOTPRINT skip-align (flag) begin"), 1,
        )
        self.assertEqual(
            second.count("PORYSUITE-FOOTPRINT skip-align (mark) begin"), 1,
        )
        self.assertEqual(
            second.count("PORYSUITE-FOOTPRINT skip-align (read) begin"), 1,
        )

    def test_free_movement_install_is_idempotent(self):
        # Same Zeldamon-style fixture as above; re-running the patcher
        # must not duplicate the helper or the BEGIN sentinels.
        player_path = os.path.join(
            self.tmp, "src", "field_player_avatar.c",
        )
        with open(player_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(
                '#include "global.h"\n'
                '#include "event_object_movement.h"\n'
                'static bool8 FreeStepNpcXBlocked(s8 dx)\n'
                '{\n'
                '        if (pLeft  <= npcX + 14 && pRight >= npcX + 1\n'
                '         && pTop   <= npcY + 14 && pBot   >= npcY + 1)\n'
                '            return TRUE;\n'
                '}\n'
                'static bool8 FreeStepNpcYBlocked(s8 dy)\n'
                '{\n'
                '        if (pLeft  <= npcX + 14 && pRight >= npcX + 1\n'
                '         && pTop   <= npcY + 14 && pBot   >= npcY + 1)\n'
                '            return TRUE;\n'
                '}\n'
            )

        patch.ensure_footprint_engine_support(self.tmp)
        with open(player_path, encoding="utf-8") as f:
            first = f.read()
        ok2, msgs2 = patch.ensure_footprint_engine_support(self.tmp)
        with open(player_path, encoding="utf-8") as f:
            second = f.read()
        self.assertTrue(ok2)
        self.assertEqual(first, second, "second run mutated field_player_avatar.c")
        # Helper name should appear exactly 3 times after either run:
        # the definition plus one call site in each of the two FreeStep
        # functions.  A higher count means the patcher duplicated.
        self.assertEqual(
            second.count("PlayerFootprintHitsObject"), 3,
            "expected definition (1) + X call site (1) + Y call site (1)",
        )


if __name__ == "__main__":
    unittest.main()
