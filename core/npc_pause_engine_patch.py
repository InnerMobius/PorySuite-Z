"""Engine patcher for the NPC-pauses-when-blocked-by-player feature.

What it installs
================

Three idempotent edits across two pokefirered source files so an NPC
that's mid-walk gets cleanly paused when the player blocks its path
(LADX-style "freeze and wait" model), and so the player can escape
a paused NPC's footprint without locking themselves in.

- ``src/event_object_movement.c::NpcTakeStep`` (MODIFIED)
  Two edits inside the per-frame mid-walk pixel-advance routine:

    1. Pause check is footprint-aware -- not just the anchor tile.  When
       the NPC has a footprint installed (``gObjectEventFootprints[gfx]
       != NULL``), the pause fires for the player's HEAD tile landing
       on ANY cell of the footprint, not only the anchor.  Anchor-only
       NPCs fall back to the existing anchor check.

    2. Animation freezes during the pause.  When the pause fires,
       ``sprite->animPaused = TRUE`` so the visible frame holds in
       place; when the pause clears, ``sprite->animPaused = FALSE`` so
       the walk anim resumes from exactly where it stopped.  Vanilla
       ticks the frame counter even while position is held, which made
       the original "Step 2.13" hand-edit pause look like an NPC stuck
       cycling its walk frames in mid-stride.

  If a legacy hand-edited "Step 2.13" pause block is present, the
  patcher detects it and replaces it with the sentinel-fenced version.
  If only vanilla NpcTakeStep is present (no pause check at all), the
  patcher installs the fenced version before the
  ``sNpcStepFuncTables`` lookup that follows.

- ``src/field_player_avatar.c::FreeStepNpcXBlocked`` (MODIFIED)
- ``src/field_player_avatar.c::FreeStepNpcYBlocked`` (MODIFIED)
  One edit each: the existing footprint-overlap check (inside the
  ``PORYSUITE-FOOTPRINT free-collide`` fences) is wrapped so it only
  fires when the player's CURRENT hitbox does NOT already overlap the
  NPC's footprint.  An already-overlapping player is "escaping" --
  the footprint check is suppressed for that NPC only, so the player
  can move away in any direction.  The 14x14 anchor inset check above
  the footprint call stays in force, so the player still can't walk
  INTO a stationary NPC's body; the escape pass applies only to
  footprint cells of an NPC the player is already inside.

What this fixes
===============

A footprint NPC walking toward tile X, plus the player walking onto
X (or a footprint cell of X) at the same time, was producing:

  1. NPC's position froze (the pre-existing pause check returned
     FALSE for NpcTakeStep) but the visible anim kept cycling -- NPC
     looked stuck mid-walk with a broken frame.
  2. NPC's footprint kept blocking the player on every direction --
     the player was inside a footprint cell with no exit, since every
     adjacent move also landed on a footprint cell -> hard softlock.

This patcher addresses both with the same fix surface the footprint
engine already uses: sentinel-fenced edits, byte-exact vanilla anchor
match, idempotent on re-runs.

What it does NOT do
===================

- No new struct fields.  The pause is per-frame re-derived from
  player position; ``sprite->animPaused`` is a vanilla bool already
  consumed by ``UpdateObjectEventSpriteAnimPause``.
- No new movement state.  The pause is just "skip my step this
  frame, freeze my visible frame," NOT a new MovementType state.
- No effect on NPC-vs-NPC collision.  ``DoesObjectCollideWithObjectAt``
  is the footprint patcher's territory; this patcher only adds
  player-aware behaviour.

This patcher depends on the footprint engine being installed first
(it consumes ``gObjectEventFootprints[]`` and the
``ObjectEventFootprintHitsTile`` helper).  ``ensure_npc_pause_engine_support``
calls the footprint installer up front and bails on failure.
"""

from __future__ import annotations

import os
import re
from typing import List, Tuple


_MOVEMENT_C_REL = os.path.join("src", "event_object_movement.c")
_PLAYER_C_REL = os.path.join("src", "field_player_avatar.c")


# Sentinel used in source -- presence of this marker on a fresh re-run
# means the patch is already installed, no-op.
_INSTALL_SENTINEL = "PORYSUITE-NPCPAUSE pause-step"
# v2 escape-pass sentinel.  Renamed to "escape-or-collide" because the
# escape pass now covers BOTH the anchor inset and the footprint check.
_ESCAPE_SENTINEL = "PORYSUITE-NPCPAUSE escape-or-collide"


# ───────────────────────────────────────────── NpcTakeStep edit ──

# Byte-exact vanilla NpcTakeStep body BEFORE any pause check.  When
# this matches the user has NEVER touched NpcTakeStep -- patcher
# installs the fenced pause block before the step-function dispatch.
_VANILLA_NPCTAKESTEP_BODY = (
    "bool8 NpcTakeStep(struct Sprite *sprite)\n"
    "{\n"
    "    if (sprite->tStepNo >= sStepTimes[sprite->tSpeed])\n"
    "        return FALSE;\n"
    "\n"
    "    sNpcStepFuncTables[sprite->tSpeed][sprite->tStepNo]"
    "(sprite, sprite->tDirection);\n"
)


# Byte-exact legacy "Step 2.13" hand-edit some projects already have
# (the user's project shipped with this in place before the patcher
# existed).  When this matches, the patcher REPLACES the whole block
# with the fenced version so the legacy hand-edit doesn't leave a
# dead duplicate.
_LEGACY_STEP213_BLOCK = (
    "    // Step 2.13 — Pause this NPC mid-step if the player's HEAD tile occupies\n"
    "    // the destination tile (currentCoords).  Mirrors LADX behavior: an NPC\n"
    "    // walking A→B stops when the player steps in front of them, then resumes\n"
    "    // once the player moves away — with no tile-grid drift since currentCoords\n"
    "    // and previousCoords are not touched during the pause.\n"
    "    // The player-side FreeStepNpcX/YBlocked (field_player_avatar.c) handles the\n"
    "    // reverse: preventing the player from walking INTO a moving NPC.\n"
    "    {\n"
    "        s16 playerTileX = (gPlayerAvatar.pixelX + 8) >> 4;\n"
    "        s16 playerTileY = (gPlayerAvatar.pixelY + 8) >> 4;\n"
    "        u8  selfSprIdx  = (u8)(sprite - gSprites);\n"
    "        u8  i;\n"
    "        for (i = 0; i < OBJECT_EVENTS_COUNT; i++)\n"
    "        {\n"
    "            struct ObjectEvent *npcObj = &gObjectEvents[i];\n"
    "            if (npcObj->active && !npcObj->isPlayer && npcObj->spriteId == selfSprIdx)\n"
    "            {\n"
    "                if (playerTileX == npcObj->currentCoords.x\n"
    "                 && playerTileY == npcObj->currentCoords.y)\n"
    "                    return FALSE; // player on destination tile — pause step\n"
    "                break;\n"
    "            }\n"
    "        }\n"
    "    }\n"
    "\n"
)


# The fenced pause block the patcher writes.  Footprint-aware AND
# sets ``sprite->animPaused`` so the visible frame holds during the
# pause.  Falls back to anchor-only check when the NPC has no
# footprint installed (NULL entry in ``gObjectEventFootprints[]``).
_PAUSE_BLOCK = (
    "    // PORYSUITE-NPCPAUSE pause-step begin\n"
    "    // LADX-style mid-step pause.  When the player's HEAD tile occupies\n"
    "    // ANY cell of this NPC's collision footprint (or its anchor tile,\n"
    "    // for anchor-only NPCs), skip the pixel advance for this frame and\n"
    "    // freeze the visible frame via sprite->animPaused.  currentCoords /\n"
    "    // previousCoords are NOT touched -- the NPC resumes exactly where it\n"
    "    // stopped the frame the player moves out of the way.  The player-side\n"
    "    // FreeStepNpcX/YBlocked (field_player_avatar.c) handles the reverse\n"
    "    // direction (player walking INTO a moving NPC).\n"
    "    {\n"
    "        s16 playerTileX = (gPlayerAvatar.pixelX + 8) >> 4;\n"
    "        s16 playerTileY = (gPlayerAvatar.pixelY + 8) >> 4;\n"
    "        u8  selfSprIdx  = (u8)(sprite - gSprites);\n"
    "        bool8 paused = FALSE;\n"
    "        u8  i;\n"
    "        for (i = 0; i < OBJECT_EVENTS_COUNT; i++)\n"
    "        {\n"
    "            struct ObjectEvent *npcObj = &gObjectEvents[i];\n"
    "            if (npcObj->active && !npcObj->isPlayer && npcObj->spriteId == selfSprIdx)\n"
    "            {\n"
    "                if (playerTileX == npcObj->currentCoords.x\n"
    "                 && playerTileY == npcObj->currentCoords.y)\n"
    "                {\n"
    "                    paused = TRUE;\n"
    "                }\n"
    "                else if (gObjectEventFootprints[npcObj->graphicsId] != NULL\n"
    "                      && ObjectEventFootprintHitsTile(npcObj, playerTileX, playerTileY))\n"
    "                {\n"
    "                    paused = TRUE;\n"
    "                }\n"
    "                break;\n"
    "            }\n"
    "        }\n"
    "        if (paused)\n"
    "        {\n"
    "            sprite->animPaused = TRUE;\n"
    "            return FALSE;\n"
    "        }\n"
    "        sprite->animPaused = FALSE;\n"
    "    }\n"
    "    // PORYSUITE-NPCPAUSE pause-step end\n"
    "\n"
)


# What the patched NpcTakeStep looks like after the install (used for
# refusal when neither vanilla nor legacy anchor matches).
def _patched_npctakestep_head() -> str:
    return (
        "bool8 NpcTakeStep(struct Sprite *sprite)\n"
        "{\n"
        "    if (sprite->tStepNo >= sStepTimes[sprite->tSpeed])\n"
        "        return FALSE;\n"
        "\n"
        + _PAUSE_BLOCK
        + "    sNpcStepFuncTables[sprite->tSpeed][sprite->tStepNo]"
          "(sprite, sprite->tDirection);\n"
    )


# ────────────────────────────────────── FreeStepNpc*Blocked edits ──

# The patch replaces a TWO-CHUNK region in both FreeStepNpcXBlocked and
# FreeStepNpcYBlocked: the 14x14 anchor-inset check + the existing
# PORYSUITE-FOOTPRINT free-collide footprint check.  These two checks
# are unified inside ONE escape-or-collide block so the escape pass
# (skip when player is already overlapping) covers BOTH checks instead
# of just the footprint check.
#
# Why both?  An NPC walking into the player can leave the player on
# the NPC's anchor tile, not just its footprint cells.  The standalone
# anchor-inset check then fires for every direction the player tries
# to leave -- softlock.  Unifying the two checks under one escape gate
# lets the player walk away regardless of which sub-check is "owned"
# by the overlap.
#
# Three before-states the patcher recognises:
#
#  v0 — vanilla footprint-engine output: anchor inset followed by the
#       3-line PORYSUITE-FOOTPRINT free-collide block.  Project has the
#       footprint engine installed but the NPC pause patcher has never
#       run.
#
#  v1 — earlier NPC pause patcher output: anchor inset followed by the
#       escape-pass-footprint-only block (PORYSUITE-NPCPAUSE escape-pass).
#       Project has the v1 patcher's output; we upgrade in place to v2.
#
#  v2 — current output: anchor inset folded INTO the same fenced block
#       as the footprint check, both gated by the escape pass.  No-op
#       on re-run.
#
# All three preserve the outer ``PORYSUITE-FOOTPRINT free-collide``
# sentinels so the footprint patcher's own idempotency (which probes
# for the begin sentinel) keeps working.


# v0 — vanilla anchor inset + footprint patcher's plain footprint block.
_V0_BEFORE_BLOCK = (
    "        if (pLeft  <= npcX + 14 && pRight >= npcX + 1\n"
    "         && pTop   <= npcY + 14 && pBot   >= npcY + 1)\n"
    "            return TRUE;\n"
    "        // PORYSUITE-FOOTPRINT free-collide begin\n"
    "        if (PlayerFootprintHitsObject(obj, npcX, npcY, pLeft, pRight, pTop, pBot))\n"
    "            return TRUE;\n"
    "        // PORYSUITE-FOOTPRINT free-collide end\n"
)


# v1 — anchor inset + v1 escape-pass-footprint-only block.
_V1_BEFORE_BLOCK = (
    "        if (pLeft  <= npcX + 14 && pRight >= npcX + 1\n"
    "         && pTop   <= npcY + 14 && pBot   >= npcY + 1)\n"
    "            return TRUE;\n"
    "        // PORYSUITE-FOOTPRINT free-collide begin\n"
    "        // PORYSUITE-NPCPAUSE escape-pass: skip this NPC's footprint check\n"
    "        // when the player's CURRENT hitbox already overlaps the footprint.\n"
    "        // An already-overlapping player is ESCAPING a paused NPC's footprint;\n"
    "        // every adjacent move would also overlap the footprint, so blocking\n"
    "        // here would trap them.  The 14x14 anchor inset check above this\n"
    "        // block stays in force, so the player still can't walk INTO a\n"
    "        // stationary NPC's body -- escape applies ONLY to footprint cells.\n"
    "        {\n"
    "            s16 curL = gPlayerAvatar.pixelX + FREEMOVE_HB_LEFT;\n"
    "            s16 curR = gPlayerAvatar.pixelX + FREEMOVE_HB_RIGHT;\n"
    "            s16 curT = gPlayerAvatar.pixelY + FREEMOVE_HB_TOP;\n"
    "            s16 curB = gPlayerAvatar.pixelY + FREEMOVE_HB_BOT;\n"
    "            if (!PlayerFootprintHitsObject(obj, npcX, npcY, curL, curR, curT, curB)\n"
    "             &&  PlayerFootprintHitsObject(obj, npcX, npcY, pLeft, pRight, pTop, pBot))\n"
    "                return TRUE;\n"
    "        }\n"
    "        // PORYSUITE-FOOTPRINT free-collide end\n"
)


# v2 — anchor inset folded INTO the fenced block; both checks gated
# by the escape pass.  This is what the patcher writes.
_V2_AFTER_BLOCK = (
    "        // PORYSUITE-FOOTPRINT free-collide begin\n"
    "        // PORYSUITE-NPCPAUSE escape-or-collide begin\n"
    "        // Unified collision check.  Both the 14x14 anchor inset and the\n"
    "        // footprint AABB are skipped for any NPC the player's CURRENT\n"
    "        // hitbox is already overlapping -- the player is ESCAPING that\n"
    "        // NPC (its anchor or footprint), so every direction would still\n"
    "        // overlap and blocking here would trap them.  When the player is\n"
    "        // NOT already overlapping, both checks run normally against the\n"
    "        // proposed (post-move) hitbox.  The escape pass is per-NPC, so\n"
    "        // adjacent NPCs (not the one being escaped) still block normally.\n"
    "        {\n"
    "            s16 curL = gPlayerAvatar.pixelX + FREEMOVE_HB_LEFT;\n"
    "            s16 curR = gPlayerAvatar.pixelX + FREEMOVE_HB_RIGHT;\n"
    "            s16 curT = gPlayerAvatar.pixelY + FREEMOVE_HB_TOP;\n"
    "            s16 curB = gPlayerAvatar.pixelY + FREEMOVE_HB_BOT;\n"
    "            bool8 already =\n"
    "                (curL <= npcX + 14 && curR >= npcX + 1\n"
    "                 && curT <= npcY + 14 && curB >= npcY + 1)\n"
    "                || PlayerFootprintHitsObject(obj, npcX, npcY, curL, curR, curT, curB);\n"
    "            if (!already)\n"
    "            {\n"
    "                if (pLeft  <= npcX + 14 && pRight >= npcX + 1\n"
    "                 && pTop   <= npcY + 14 && pBot   >= npcY + 1)\n"
    "                    return TRUE;\n"
    "                if (PlayerFootprintHitsObject(obj, npcX, npcY, pLeft, pRight, pTop, pBot))\n"
    "                    return TRUE;\n"
    "            }\n"
    "        }\n"
    "        // PORYSUITE-NPCPAUSE escape-or-collide end\n"
    "        // PORYSUITE-FOOTPRINT free-collide end\n"
)


# Sentinel used to detect v2-installed state (idempotency probe).
_V2_SENTINEL = "PORYSUITE-NPCPAUSE escape-or-collide"


# ─────────────────────────────────────────────────── public API ──

def is_engine_installed(project_root: str) -> bool:
    """True iff the NPC pause patches are present in the movement file.

    Probes the install sentinel in ``event_object_movement.c``.  The
    escape-pass sentinel in ``field_player_avatar.c`` is a separate
    file -- ``ensure_npc_pause_engine_support`` checks both files
    individually so a partially-installed state can finish in one run.
    """
    path = os.path.join(project_root, _MOVEMENT_C_REL)
    if not os.path.isfile(path):
        return False
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return _INSTALL_SENTINEL in f.read()
    except OSError:
        return False


def _project_has_free_pixel_movement(project_root: str) -> bool:
    """Probe whether the project uses a free-pixel-movement system.

    Vanilla pokefirered's player avatar is tile-locked: ``gPlayerAvatar``
    has no pixel-level position fields, and there's no ``FreeStepNpcXBlocked``
    function.  Projects that mod in a free-pixel movement system (the
    pattern the NPC pause feature is built for) add EITHER:

      * ``gPlayerAvatar.pixelX`` / ``pixelY`` fields on the PlayerAvatar
        struct, AND
      * ``FreeStepNpcXBlocked(s8 dx)`` / ``FreeStepNpcYBlocked(s8 dy)``
        helper functions called from the player's pixel-step routine.

    The NpcTakeStep patch this module installs uses
    ``gPlayerAvatar.pixelX`` to compute the player's HEAD tile, which
    breaks the build on any vanilla project (struct field doesn't
    exist).  This gate keeps the patcher a silent no-op for vanilla
    pokefirered users — they don't NEED the pause feature anyway,
    because vanilla tile-locked movement already blocks two object
    events from occupying the same tile.

    Probe: look for ``FreeStepNpcXBlocked`` definition in
    ``src/field_player_avatar.c``.  Cheap, reliable, doesn't require
    parsing the PlayerAvatar struct.
    """
    path = os.path.join(project_root, _PLAYER_C_REL)
    if not os.path.isfile(path):
        return False
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return False
    # Either signature is enough.  Both is the standard pattern.
    return (
        "FreeStepNpcXBlocked(s8 dx)" in text
        or "gPlayerAvatar.pixelX" in text
    )


def ensure_npc_pause_engine_support(
    project_root: str,
) -> Tuple[bool, List[str]]:
    """Install (or refresh) the NPC pause engine support.

    Idempotent: re-running on a project that already has it is a clean
    no-op.  Requires the footprint engine -- the patches consume
    ``gObjectEventFootprints[]`` and ``ObjectEventFootprintHitsTile``,
    both of which the footprint patcher installs.  This function
    delegates to ``core.footprint_engine_patch.ensure_footprint_engine_support``
    first; if that fails the NPC pause patches are skipped so a partial
    install never lands.

    **Vanilla pokefirered users get a silent no-op for the NpcTakeStep
    edit.**  The pause block uses ``gPlayerAvatar.pixelX``/``pixelY``
    which only exist on free-pixel-movement mod projects (Zeldamon-style
    overhauls); installing it on a vanilla project would break the
    build with "no field 'pixelX' in struct PlayerAvatar".  The probe
    in ``_project_has_free_pixel_movement`` gates the install.  Vanilla
    projects don't need the pause feature anyway: their tile-locked
    movement already prevents two object events from occupying the
    same tile, so the softlock this patch fixes can't happen there.

    Returns ``(success, messages)`` -- ``messages`` lists touched files
    on a fresh install and is empty on a no-op re-run.
    """
    messages: List[str] = []
    try:
        # Footprint engine is a hard dependency -- the pause block
        # references gObjectEventFootprints[] and ObjectEventFootprintHitsTile.
        from core.footprint_engine_patch import ensure_footprint_engine_support
        ok_fp, fp_msgs = ensure_footprint_engine_support(project_root)
        if not ok_fp:
            return False, messages + [
                "NPC pause install aborted: footprint engine "
                "prerequisite failed (" + "; ".join(fp_msgs) + ")"
            ]
        # Surface footprint install messages so the user sees the full
        # install chain on a fresh project.
        for m in fp_msgs:
            messages.append(f"(prereq) {m}")

        # Gate the NpcTakeStep patch on free-pixel-movement detection.
        # Vanilla pokefirered has no pixelX/pixelY fields; installing
        # the patch breaks the build.  Vanilla users don't need the
        # feature either — tile-locked movement already blocks the
        # softlock this fixes.
        if _project_has_free_pixel_movement(project_root):
            npctake_msg = _patch_npctakestep(project_root)
            if npctake_msg:
                messages.append(npctake_msg)
        # else: silent no-op.  Vanilla project, nothing to do.

        # The escape-pass patch in field_player_avatar.c is already
        # gated by _patch_free_step_blocked itself (which silently
        # no-ops when the file is missing or has no FreeStepNpc*Blocked
        # functions).
        escape_msg = _patch_free_step_blocked(project_root)
        if escape_msg:
            messages.append(escape_msg)
    except OSError as exc:
        return False, messages + [f"NPC pause install failed: {exc}"]
    return True, messages


# ───────────────────────────────────────────── sub-installers ──

def _patch_npctakestep(project_root: str) -> str:
    """Install the footprint-aware pause + animPaused freeze inside
    ``NpcTakeStep``.

    Three handled states (in priority order):
      1. Already installed -- detect via ``_INSTALL_SENTINEL`` -> no-op.
      2. Legacy ``Step 2.13`` hand-edit present -> replace it.
      3. Vanilla NpcTakeStep -> insert the fenced pause block before
         the step-function table dispatch.

    Raises ``OSError`` when the source file is missing or no anchor
    matches (refuses to patch a hand-edited copy that doesn't match
    any known shape).
    """
    path = os.path.join(project_root, _MOVEMENT_C_REL)
    if not os.path.isfile(path):
        raise OSError(f"missing {_MOVEMENT_C_REL}")
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()

    if _INSTALL_SENTINEL in text:
        return ""  # already installed

    if _LEGACY_STEP213_BLOCK in text:
        # Replace the user's legacy hand-edit with the sentinel-fenced
        # version (which is a superset: footprint-aware + animPaused).
        new_text = text.replace(
            _LEGACY_STEP213_BLOCK, _PAUSE_BLOCK, 1,
        )
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(new_text)
        return (
            f"{_MOVEMENT_C_REL}: replaced legacy 'Step 2.13' hand-edit "
            f"with PORYSUITE-NPCPAUSE pause-step block "
            f"(footprint-aware + animPaused freeze)"
        )

    if _VANILLA_NPCTAKESTEP_BODY in text:
        # Vanilla NpcTakeStep -- insert the fenced block before the
        # dispatch line.
        new_text = text.replace(
            _VANILLA_NPCTAKESTEP_BODY,
            _patched_npctakestep_head(),
            1,
        )
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(new_text)
        return (
            f"{_MOVEMENT_C_REL}: installed PORYSUITE-NPCPAUSE pause-step "
            f"block in NpcTakeStep"
        )

    raise OSError(
        f"{_MOVEMENT_C_REL}: NpcTakeStep does not match any expected "
        f"shape (vanilla / legacy 'Step 2.13' hand-edit) -- refusing "
        f"to patch a modified copy"
    )


def _patch_free_step_blocked(project_root: str) -> str:
    """Wrap the footprint-overlap check in both ``FreeStepNpcXBlocked``
    and ``FreeStepNpcYBlocked`` with the escape-pass guard.

    Three before-states the patcher recognises -- detection runs in
    order, each upgrade transforms the file into v2 form:

      v2 (already installed)
          File already contains ``PORYSUITE-NPCPAUSE escape-or-collide``.
          No-op.  Idempotency probe.

      v1 (legacy escape-pass-footprint-only output)
          File contains the v1 anchor+wrapped-footprint blocks.  Replace
          both with v2.  Same outer ``PORYSUITE-FOOTPRINT free-collide``
          sentinels survive so the footprint patcher's own idempotency
          keeps working.

      v0 (vanilla footprint output, no NPC pause)
          File contains the unwrapped anchor + plain footprint blocks.
          Replace both with v2.

    Returns a human-readable summary of what changed, or "" on no-op /
    missing file.  Raises ``OSError`` when the file exists but matches
    none of the three states -- refusal so we don't silently corrupt
    a hand-edited copy.
    """
    path = os.path.join(project_root, _PLAYER_C_REL)
    if not os.path.isfile(path):
        # field_player_avatar.c only exists on projects with free-pixel
        # movement.  Vanilla projects don't have FreeStepNpc*Blocked at
        # all -- the escape-pass patch is a silent no-op there.
        return ""
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()

    # ── v2 probe: already installed?  Two functions -> two markers ──
    v2_count = text.count(_V2_SENTINEL + " begin")
    if v2_count >= 2:
        return ""  # both X and Y already at v2, no-op
    # Partial v2 (one of two functions patched) shouldn't normally
    # happen but if it does, fall through and finish the install -- the
    # below replacements no-op on the already-patched block (its source
    # form isn't v0 or v1 anymore).

    # ── v1 -> v2 upgrade ──
    # Count v1 anchor+footprint pairs and replace each with v2.  The v1
    # form contains the v1 PORYSUITE-NPCPAUSE escape-pass marker; if any
    # are present we're upgrading from v1.
    v1_count = text.count(_V1_BEFORE_BLOCK)
    if v1_count > 0:
        new_text = text.replace(_V1_BEFORE_BLOCK, _V2_AFTER_BLOCK, v1_count)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(new_text)
        return (
            f"{_PLAYER_C_REL}: upgraded {v1_count} v1 escape-pass block(s) "
            f"to v2 PORYSUITE-NPCPAUSE escape-or-collide (anchor + footprint)"
        )

    # ── v0 -> v2 fresh install ──
    v0_count = text.count(_V0_BEFORE_BLOCK)
    if v0_count > 0:
        new_text = text.replace(_V0_BEFORE_BLOCK, _V2_AFTER_BLOCK, v0_count)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(new_text)
        return (
            f"{_PLAYER_C_REL}: installed {v0_count} PORYSUITE-NPCPAUSE "
            f"escape-or-collide block(s) (anchor + footprint)"
        )

    # ── Nothing matched ──
    # If the file has PORYSUITE-FOOTPRINT free-collide markers but
    # neither v0 nor v1 nor v2 shape, the user has hand-edited the
    # collision loop.  Refuse rather than corrupt their changes.
    if "PORYSUITE-FOOTPRINT free-collide" in text:
        raise OSError(
            f"{_PLAYER_C_REL}: FreeStepNpc*Blocked collision body does "
            f"not match v0 (vanilla footprint), v1 (legacy escape-pass), "
            f"or v2 (current) shapes -- refusing to patch a modified copy"
        )
    # Footprint engine not yet installed in this file (no fence at all).
    # Could happen if footprint_engine_patch's optional _patch_player_avatar_c
    # didn't fire (vanilla pokefirered without free-pixel movement, etc.).
    # Silent no-op.
    return ""
