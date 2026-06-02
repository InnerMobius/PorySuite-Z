"""Engine patcher for the collision-footprint feature.

What it installs
================

This is the Phase 7b engine refactor.  It puts the runtime scaffolding
in place so the per-sprite footprints PorySuite-Z's editor saves to
``src/data/object_events/object_event_footprints.h`` actually affect
the game:

- ``include/object_footprint.h`` (NEW)
    The ``ObjectFootprint`` struct + the ``gObjectEventFootprints[]``
    extern declaration.

- ``src/data/object_events/object_event_footprints.h`` (NEW, empty)
    Created lazily so the engine reference ALWAYS compiles, even
    before the user has authored any footprint.  Once any footprint is
    saved the file is owned by ``core/overworld_footprint.py``'s
    serialiser — this patcher never touches it again.

- ``src/event_object_movement.c`` (MODIFIED)
    Four idempotent edits:
      1. ``#include "object_footprint.h"`` near the top of the file.
      2. ``#include "data/object_events/object_event_footprints.h"``
         next to the existing data includes.
      3. A static helper ``ObjectEventFootprintHitsTile`` placed
         directly above ``GetObjectEventIdByPosition``, plus a fenced
         insertion inside that function's loop body that returns the
         object event whenever the target tile falls inside any of its
         footprint's cells (rather than only matching the anchor tile)
         -- this is the A-button interaction extension.
      4. A second fenced insertion inside ``DoesObjectCollideWithObjectAt``
         that calls the same helper so the player physically cannot
         walk into a painted cell -- this is the tile-locked movement
         collision extension.  (Projects using the Zeldamon-style
         free-pixel movement need the optional player-avatar edits
         below as well; the vanilla path does NOT go through their
         FreeStepNpc*Blocked checks.)

All edits are detected via PORYSUITE-FOOTPRINT sentinels so a second
run on an already-installed project is a no-op.  Same discipline as
``core/sprite_depth_patch.py``: byte-exact match against the vanilla
function body, fail loudly if the upstream code drifts so we never
patch a hand-edited copy.

What it does NOT do
===================

It never writes per-sprite footprint DATA — that's
``core/overworld_footprint.py``'s job, called from the save path
(Phase 7d).  This patcher only puts the engine plumbing in place so
the data has somewhere to live and someone to read it.
"""

from __future__ import annotations

import os
from typing import List, Tuple


# Relative paths inside the pokefirered tree.
_STRUCT_HEADER_REL = os.path.join("include", "object_footprint.h")
_DATA_HEADER_REL = os.path.join(
    "src", "data", "object_events", "object_event_footprints.h",
)
_MOVEMENT_C_REL = os.path.join("src", "event_object_movement.c")
# Optional: only present on projects that have replaced the vanilla
# tile-locked movement with a free-pixel-movement system (Zeldamon-style).
# Vanilla pokefirered's player avatar goes through
# DoesObjectCollideWithObjectAt (already patched in _MOVEMENT_C_REL),
# so vanilla projects don't need this file touched.
_PLAYER_C_REL = os.path.join("src", "field_player_avatar.c")
# Optional: only present on projects that have added the
# StartPreScriptAlign / Task_PreScriptAlign script-start alignment task.
# That task is a free-pixel-movement extension absent from vanilla
# pokefirered; if present, we patch it to skip alignment when the
# interaction source has a footprint (alignment would drag the player
# into a painted cell -- see PORYSUITE-FOOTPRINT skip-align comments).
_CONTROL_C_REL = os.path.join("src", "field_control_avatar.c")


# A signature in the struct header.  Same idea as PORYSUITE-DEPTH:
# its presence means the engine support is installed.
_SIGNATURE = "PORYSUITE-FOOTPRINT v1"


# ────────────────────────────────────────────── struct header body ──

_STRUCT_HEADER_BODY = """\
#ifndef GUARD_OBJECT_FOOTPRINT_H
#define GUARD_OBJECT_FOOTPRINT_H

// PORYSUITE-FOOTPRINT v1
//
// Per-sprite collision footprint.  An opt-in multi-cell solid mask the
// engine consults whenever it asks "is there an object at this tile?"
// Authored in PorySuite-Z's Overworld Graphics tab and managed
// entirely by the tool -- do not edit by hand.
//
// The default for any object event whose graphics ID has NO entry in
// gObjectEventFootprints[] (= NULL) is the vanilla single-tile block:
// only the object's currentCoords match, exactly like un-patched
// pokefirered.

#include "global.h"

struct ObjectFootprint
{
    u8 width;          // cells (8px each)
    u8 height;         // cells (8px each)
    const u8 *cells;   // row-major, width*height bytes (0 = open, 1 = solid)
};

// Indexed by OBJ_EVENT_GFX_*.  NULL = no footprint = vanilla single-tile.
extern const struct ObjectFootprint *const gObjectEventFootprints[];

#endif // GUARD_OBJECT_FOOTPRINT_H
"""


# ──────────────────────────────────────────── seed data-file body ──

# What the patcher writes when src/data/object_events/object_event_footprints.h
# does not yet exist.  Once any footprint is authored, the save path takes
# over and replaces this file wholesale -- the patcher must never overwrite
# an existing file.
_SEED_DATA_HEADER_BODY = """\
// Generated by PorySuite-Z.  Do not edit by hand -- re-run the
// Edit Collision Footprint... dialog instead.  Per-footprint blocks
// are fenced by PORYSUITE-GEN sentinels; the lookup table is
// regenerated wholesale on every save.

#include "object_footprint.h"
#include "constants/event_objects.h"

// PORYSUITE-GEN BEGIN table
// MUST be sized [NUM_OBJ_EVENT_GFX] -- the engine indexes this array by
// graphicsId for every object event on every collision / interaction
// check.  An array sized smaller than NUM_OBJ_EVENT_GFX reads off the
// end into garbage memory for any graphicsId past the max designator,
// causing phantom collision walls and lag.  See core/overworld_footprint.py
// for the full failure mode.
const struct ObjectFootprint *const gObjectEventFootprints[NUM_OBJ_EVENT_GFX] = {
};
// PORYSUITE-GEN END table
"""


# ────────────────────────────────────────── event_object_movement.c ──

_INCLUDE_LINE = '#include "object_footprint.h"'
_INCLUDE_ANCHOR = '#include "event_object_movement.h"'

_DATA_INCLUDE_LINE = '#include "data/object_events/object_event_footprints.h"'
_DATA_INCLUDE_ANCHOR = '#include "data/object_events/object_event_graphics_info.h"'


# Static helper inserted directly above GetObjectEventIdByPosition.
# Pure ASCII (agbcc-friendly).  Math: convert each solid 8x8 cell to
# its world-pixel rectangle (the sprite is drawn bottom-center anchored
# to its currentCoords tile), then AABB-overlap against the target tile.
_HELPER_FUNC = """// PORYSUITE-FOOTPRINT helper begin
// Returns TRUE iff any solid cell of the object event's footprint
// overlaps the 16x16 target tile at (tx, ty).  Used by
// GetObjectEventIdByPosition (A-button interaction lookup) and by
// DoesObjectCollideWithObjectAt (player movement collision) to extend
// the vanilla single-tile checks across a multi-cell footprint when
// one is installed.  An object event whose graphicsId has no entry in
// gObjectEventFootprints[] (= NULL) trivially returns FALSE here and
// falls back to the vanilla anchor-only check.
static bool8 ObjectEventFootprintHitsTile(const struct ObjectEvent *objectEvent, s16 tx, s16 ty)
{
    const struct ObjectFootprint *fp;
    const struct ObjectEventGraphicsInfo *gi;
    s32 tlX, tlY, tileLeft, tileTop;
    u8 r, c;

    fp = gObjectEventFootprints[objectEvent->graphicsId];
    if (fp == NULL)
        return FALSE;

    gi = GetObjectEventGraphicsInfo(objectEvent->graphicsId);

    // Sprite art top-left in world pixels: the sprite is drawn with
    // its bottom-center on the bottom-center of the anchor tile.
    tlX = ((s32)objectEvent->currentCoords.x * 16) + 8 - ((s32)gi->width / 2);
    tlY = ((s32)objectEvent->currentCoords.y * 16) + 16 - (s32)gi->height;

    tileLeft = (s32)tx * 16;
    tileTop = (s32)ty * 16;

    for (r = 0; r < fp->height; r++)
    {
        for (c = 0; c < fp->width; c++)
        {
            s32 cellLeft, cellTop;
            if (!fp->cells[(u32)r * fp->width + c])
                continue;
            cellLeft = tlX + ((s32)c * 8);
            cellTop = tlY + ((s32)r * 8);
            // 8x8 cell vs 16x16 target tile -- AABB overlap.
            if (cellLeft < tileLeft + 16
                && cellLeft + 8 > tileLeft
                && cellTop < tileTop + 16
                && cellTop + 8 > tileTop)
                return TRUE;
        }
    }
    return FALSE;
}
// PORYSUITE-FOOTPRINT helper end

"""


# Byte-exact vanilla GetObjectEventIdByPosition -- the per-tile object
# lookup the A-button interaction goes through
# (field_control_avatar.c::GetInteractedObjectEventScript →
# GetObjectEventIdByPosition).  Extending this is what makes pressing A
# while facing any painted cell trigger the object's script, not only
# the anchor tile.  The older GetObjectEventIdByXY function is left
# untouched -- in vanilla and in projects we've inspected it is only
# called by TryPushBoulder (Strength HM), which targets a single-tile
# pushable boulder that never carries a footprint, so a footprint
# extension there does nothing in practice.
_VANILLA_GETPOS_BODY = """u8 GetObjectEventIdByPosition(u16 x, u16 y, u8 elevation)
{
    u8 i;

    for (i = 0; i < OBJECT_EVENTS_COUNT; i++)
    {
        if (gObjectEvents[i].active)
        {
            if (gObjectEvents[i].currentCoords.x == x
             && gObjectEvents[i].currentCoords.y == y
             && ObjectEventDoesElevationMatch(&gObjectEvents[i], elevation))
                return i;
        }
    }
    return OBJECT_EVENTS_COUNT;
}"""


# Patched: the vanilla anchor / elevation match still runs first (so
# NULL-footprint sprites behave exactly like un-patched pokefirered),
# then a fenced footprint-hit fallback also gated on elevation match.
_PATCHED_GETPOS_BODY = """u8 GetObjectEventIdByPosition(u16 x, u16 y, u8 elevation)
{
    u8 i;

    for (i = 0; i < OBJECT_EVENTS_COUNT; i++)
    {
        if (gObjectEvents[i].active)
        {
            if (gObjectEvents[i].currentCoords.x == x
             && gObjectEvents[i].currentCoords.y == y
             && ObjectEventDoesElevationMatch(&gObjectEvents[i], elevation))
                return i;
            // PORYSUITE-FOOTPRINT interact begin
            if (ObjectEventFootprintHitsTile(&gObjectEvents[i], (s16)x, (s16)y)
             && ObjectEventDoesElevationMatch(&gObjectEvents[i], elevation))
                return i;
            // PORYSUITE-FOOTPRINT interact end
        }
    }
    return OBJECT_EVENTS_COUNT;
}"""


# Byte-exact vanilla DoesObjectCollideWithObjectAt -- the player-movement
# collision check (called from GetCollisionAtCoords → CheckForObjectEventCollision).
# This is the one that actually blocks the player from walking into an
# object; without patching this the footprint affects nothing visible
# in-game, even with the GetObjectEventIdByPosition interaction patch
# above applied.  (The first cut of the footprint patcher only modified
# the interaction lookup -- interaction worked but movement didn't,
# because tile-locked movement goes through a different function.)
_VANILLA_COLLIDE_BODY = """static bool8 DoesObjectCollideWithObjectAt(struct ObjectEvent *objectEvent, s16 x, s16 y)
{
    u8 i;
    struct ObjectEvent *curObject;

    for (i = 0; i < OBJECT_EVENTS_COUNT; i++)
    {
        curObject = &gObjectEvents[i];
        if (curObject->active && curObject != objectEvent)
        {
            if ((curObject->currentCoords.x == x && curObject->currentCoords.y == y) || (curObject->previousCoords.x == x && curObject->previousCoords.y == y))
            {
                if (AreElevationsCompatible(objectEvent->currentElevation, curObject->currentElevation))
                    return TRUE;
            }
        }
    }
    return FALSE;
}"""


# Patched DoesObjectCollideWithObjectAt: vanilla anchor / previous-coord
# check still runs first (so a NULL-footprint sprite behaves exactly
# like un-patched pokefirered), THEN a fenced footprint check tests
# every cell of the other object's footprint.  The elevation guard
# stays applied to both paths -- a sprite on a different elevation
# tier still doesn't block you, footprint or not.
_PATCHED_COLLIDE_BODY = """static bool8 DoesObjectCollideWithObjectAt(struct ObjectEvent *objectEvent, s16 x, s16 y)
{
    u8 i;
    struct ObjectEvent *curObject;

    for (i = 0; i < OBJECT_EVENTS_COUNT; i++)
    {
        curObject = &gObjectEvents[i];
        if (curObject->active && curObject != objectEvent)
        {
            if ((curObject->currentCoords.x == x && curObject->currentCoords.y == y) || (curObject->previousCoords.x == x && curObject->previousCoords.y == y))
            {
                if (AreElevationsCompatible(objectEvent->currentElevation, curObject->currentElevation))
                    return TRUE;
            }
            // PORYSUITE-FOOTPRINT collide begin
            if (ObjectEventFootprintHitsTile(curObject, x, y)
                && AreElevationsCompatible(objectEvent->currentElevation, curObject->currentElevation))
                return TRUE;
            // PORYSUITE-FOOTPRINT collide end
        }
    }
    return FALSE;
}"""


# ───────────────────────────────────────────── public API ──

def is_engine_installed(project_root: str) -> bool:
    """True iff the patcher has been applied to ``project_root``.

    The probe is the signature in ``include/object_footprint.h`` --
    same approach as the depth patcher.  Restoring a clean pokefirered
    over the project clears the header, so the next install correctly
    re-applies.
    """
    path = os.path.join(project_root, _STRUCT_HEADER_REL)
    if not os.path.isfile(path):
        return False
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return _SIGNATURE in f.read()
    except OSError:
        return False


def ensure_footprint_engine_support(
    project_root: str,
) -> Tuple[bool, List[str]]:
    """Install (or refresh) every piece of engine scaffolding the
    footprint feature needs.  Idempotent: re-running on a project
    that already has it is a clean no-op.

    Returns ``(success, messages)`` -- ``messages`` is empty on a
    no-op run and lists the files touched otherwise.
    """
    messages: List[str] = []
    try:
        if _install_struct_header(project_root):
            messages.append(f"installed {_STRUCT_HEADER_REL}")
        if _seed_data_header(project_root):
            messages.append(f"seeded empty {_DATA_HEADER_REL}")
        movement_msg = _patch_movement_c(project_root)
        if movement_msg:
            messages.append(movement_msg)
        # Optional: projects with a Zeldamon-style free-pixel-movement
        # system have a SEPARATE NPC collision pipeline that bypasses
        # DoesObjectCollideWithObjectAt entirely.  If those functions
        # are present, patch them too; otherwise this is a silent no-op
        # (vanilla projects don't need it).
        player_msg = _patch_player_avatar_c(project_root)
        if player_msg:
            messages.append(player_msg)
        # Optional: same family of project has a script-start alignment
        # task (StartPreScriptAlign) that drags the player to their
        # currentCoords pixel position before running a script.  For a
        # multi-tile footprint NPC that alignment can push the hitbox
        # into a painted cell and softlock the player.  Patch so the
        # alignment is skipped when the interaction source has a
        # footprint.  Absent in vanilla pokefirered -- silent no-op.
        control_msg = _patch_field_control_avatar_c(project_root)
        if control_msg:
            messages.append(control_msg)
    except OSError as exc:
        return False, messages + [f"footprint engine install failed: {exc}"]
    return True, messages


# ────────────────────────────────────────────── sub-installers ──

def _install_struct_header(project_root: str) -> bool:
    """Write ``include/object_footprint.h`` if missing or stale.

    Returns True iff the file was (re)written.  The signature inside
    the body is the idempotency lock.
    """
    path = os.path.join(project_root, _STRUCT_HEADER_REL)
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                if _SIGNATURE in f.read():
                    return False
        except OSError:
            # If we can't read it, rewrite is the safe move.
            pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(_STRUCT_HEADER_BODY)
    return True


def _seed_data_header(project_root: str) -> bool:
    """Create an empty footprints data file if it doesn't exist yet.

    Once the file exists, this function never touches it again -- the
    save path (Phase 7d) owns it from that point on.  Returns True
    iff the seed was newly written.
    """
    path = os.path.join(project_root, _DATA_HEADER_REL)
    if os.path.isfile(path):
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(_SEED_DATA_HEADER_BODY)
    return True


def _patch_movement_c(project_root: str) -> str:
    """Apply the three idempotent edits to event_object_movement.c.

    Returns a human-readable summary of what changed, or "" on a
    no-op run.  Raises ``OSError`` when the file is missing or its
    anchors do not match the expected vanilla shape.
    """
    path = os.path.join(project_root, _MOVEMENT_C_REL)
    if not os.path.isfile(path):
        raise OSError(f"missing {_MOVEMENT_C_REL}")
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    original = text
    changes: List[str] = []

    # Edit 1: top-level #include for the struct header.
    if _INCLUDE_LINE not in text:
        if _INCLUDE_ANCHOR not in text:
            raise OSError(
                f"{_MOVEMENT_C_REL}: anchor for top-level #include "
                f"({_INCLUDE_ANCHOR!r}) not found"
            )
        text = text.replace(
            _INCLUDE_ANCHOR,
            _INCLUDE_ANCHOR + "\n" + _INCLUDE_LINE,
            1,
        )
        changes.append('added #include "object_footprint.h"')

    # Edit 2: data-table #include alongside the other object-event data.
    if _DATA_INCLUDE_LINE not in text:
        if _DATA_INCLUDE_ANCHOR not in text:
            raise OSError(
                f"{_MOVEMENT_C_REL}: anchor for data-table #include "
                f"({_DATA_INCLUDE_ANCHOR!r}) not found"
            )
        text = text.replace(
            _DATA_INCLUDE_ANCHOR,
            _DATA_INCLUDE_ANCHOR + "\n" + _DATA_INCLUDE_LINE,
            1,
        )
        changes.append('added #include of footprints data table')

    # Edit 3a: install the ObjectEventFootprintHitsTile helper.  Gated
    # by its own sentinel so a later refactor that retargets the body
    # patches (Edit 3b / Edit 4) can still proceed when the helper is
    # already in place from a previous install.  The helper is inserted
    # directly above GetObjectEventIdByPosition; placement is cosmetic
    # (the function must be defined before its callers, and both
    # GetObjectEventIdByPosition and DoesObjectCollideWithObjectAt sit
    # below this point).
    if "PORYSUITE-FOOTPRINT helper begin" not in text:
        if _VANILLA_GETPOS_BODY not in text:
            raise OSError(
                f"{_MOVEMENT_C_REL}: GetObjectEventIdByPosition does "
                f"not match the expected vanilla body; refusing to "
                f"insert the helper before a modified copy."
            )
        text = text.replace(
            _VANILLA_GETPOS_BODY,
            _HELPER_FUNC + _VANILLA_GETPOS_BODY,
            1,
        )
        changes.append("installed ObjectEventFootprintHitsTile")

    # Edit 3b: extend GetObjectEventIdByPosition (the A-button
    # interaction lookup) so the player can talk to any painted cell
    # of a multi-tile sprite, not only its anchor tile.  Gated by its
    # own PORYSUITE-FOOTPRINT interact sentinel.
    if "PORYSUITE-FOOTPRINT interact begin" not in text:
        if _VANILLA_GETPOS_BODY not in text:
            raise OSError(
                f"{_MOVEMENT_C_REL}: GetObjectEventIdByPosition does "
                f"not match the expected vanilla body; refusing to "
                f"patch a modified copy."
            )
        text = text.replace(
            _VANILLA_GETPOS_BODY,
            _PATCHED_GETPOS_BODY,
            1,
        )
        changes.append(
            "extended GetObjectEventIdByPosition for footprint interaction"
        )

    # Edit 4: extend DoesObjectCollideWithObjectAt (player movement
    # collision -- the player physically can't enter a footprint cell).
    # Without this the footprint affects nothing visible in-game; the
    # first cut of the patcher hit interaction only and the user could
    # still walk straight through the sprite.  Sentinel-fenced so a
    # second run is a no-op.
    if "PORYSUITE-FOOTPRINT collide begin" not in text:
        if _VANILLA_COLLIDE_BODY not in text:
            raise OSError(
                f"{_MOVEMENT_C_REL}: DoesObjectCollideWithObjectAt does "
                f"not match the expected vanilla body; refusing to "
                f"patch a modified copy."
            )
        text = text.replace(
            _VANILLA_COLLIDE_BODY,
            _PATCHED_COLLIDE_BODY,
            1,
        )
        changes.append(
            "extended DoesObjectCollideWithObjectAt for footprint collision"
        )

    if text == original:
        return ""
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return f"{_MOVEMENT_C_REL}: " + "; ".join(changes)


# ─────────────────────────────────── free-movement collision (optional) ──

# Anchor that flags a project as having the Zeldamon-style free-pixel
# movement collision pipeline.  Vanilla pokefirered does not have this
# function and the patcher's player-avatar edits are skipped.
_FREE_HELPER_ANCHOR = "static bool8 FreeStepNpcXBlocked(s8 dx)"

# Top-level include we add so the patched file can name the footprint
# struct + the gObjectEventFootprints lookup table.
_PLAYER_INCLUDE_LINE = '#include "object_footprint.h"'
_PLAYER_INCLUDE_ANCHOR = '#include "event_object_movement.h"'


# Helper inserted directly ABOVE FreeStepNpcXBlocked.  AABB-overlap the
# player's pixel hitbox against any solid cell of an NPC's footprint;
# returns FALSE for sprites with no footprint installed.  Pure ASCII
# (agbcc-friendly).
_FREE_HELPER_FUNC = """\
// PORYSUITE-FOOTPRINT free-helper begin
// AABB-overlap the player's pixel hitbox against any solid 8x8 cell of
// an NPC's collision footprint.  Called from the free-movement pixel
// collision path (FreeStepNpcXBlocked / FreeStepNpcYBlocked) AFTER the
// vanilla single-tile 14x14 NPC box check, so a NULL-footprint sprite
// behaves exactly like un-patched pokefirered.  The sprite is drawn
// bottom-center anchored to the NPC tile's bottom-center; each cell's
// world-pixel rect is derived from the GraphicsInfo width/height.
static bool8 PlayerFootprintHitsObject(const struct ObjectEvent *obj,
                                       s16 npcX, s16 npcY,
                                       s16 pLeft, s16 pRight,
                                       s16 pTop, s16 pBot)
{
    const struct ObjectFootprint *fp;
    const struct ObjectEventGraphicsInfo *gi;
    s32 artTLX, artTLY;
    u8 r, c;

    fp = gObjectEventFootprints[obj->graphicsId];
    if (fp == NULL)
        return FALSE;

    gi = GetObjectEventGraphicsInfo(obj->graphicsId);
    // Anchor tile's top-left pixel is (npcX, npcY); sprite art is drawn
    // bottom-center on the anchor tile's bottom-center.
    artTLX = (s32)npcX + 8 - ((s32)gi->width / 2);
    artTLY = (s32)npcY + 16 - (s32)gi->height;

    for (r = 0; r < fp->height; r++)
    {
        for (c = 0; c < fp->width; c++)
        {
            s32 cellL, cellT;
            if (!fp->cells[(u32)r * fp->width + c])
                continue;
            cellL = artTLX + ((s32)c * 8);
            cellT = artTLY + ((s32)r * 8);
            // Full 8x8 cell (no 1-px inset) so adjacent painted cells
            // form a continuous wall with no slip-through gap.
            if (pLeft <= cellL + 7 && pRight >= cellL
             && pTop  <= cellT + 7 && pBot  >= cellT)
                return TRUE;
        }
    }
    return FALSE;
}
// PORYSUITE-FOOTPRINT free-helper end

"""


# The vanilla 14x14 NPC overlap check.  Byte-identical in both
# FreeStepNpcXBlocked and FreeStepNpcYBlocked, so a single str.replace
# (no count) extends both call sites in one pass.
_VANILLA_FREE_OVERLAP = """        if (pLeft  <= npcX + 14 && pRight >= npcX + 1
         && pTop   <= npcY + 14 && pBot   >= npcY + 1)
            return TRUE;"""

_PATCHED_FREE_OVERLAP = """        if (pLeft  <= npcX + 14 && pRight >= npcX + 1
         && pTop   <= npcY + 14 && pBot   >= npcY + 1)
            return TRUE;
        // PORYSUITE-FOOTPRINT free-collide begin
        if (PlayerFootprintHitsObject(obj, npcX, npcY, pLeft, pRight, pTop, pBot))
            return TRUE;
        // PORYSUITE-FOOTPRINT free-collide end"""


def _patch_player_avatar_c(project_root: str) -> str:
    """Patch the free-pixel-movement NPC collision -- only if present.

    Vanilla pokefirered has no ``FreeStepNpcXBlocked`` / ``YBlocked`` --
    those belong to a Zeldamon-style pixel-movement system that
    bypasses ``DoesObjectCollideWithObjectAt`` entirely.  If they're
    absent the function returns ``""`` and the vanilla
    event_object_movement.c patches are sufficient.

    Edits applied (all idempotent, sentinel-fenced):
      1. ``#include "object_footprint.h"`` near the top of the file.
      2. ``PlayerFootprintHitsObject`` static helper inserted directly
         above ``FreeStepNpcXBlocked``.
      3. After the vanilla 14x14 NPC overlap check in BOTH
         ``FreeStepNpcXBlocked`` and ``FreeStepNpcYBlocked``, an
         additional call to ``PlayerFootprintHitsObject`` -- a single
         ``str.replace`` (no count) catches both byte-identical
         occurrences.

    Returns a human-readable summary, or ``""`` on a no-op run.
    """
    path = os.path.join(project_root, _PLAYER_C_REL)
    if not os.path.isfile(path):
        return ""
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    if _FREE_HELPER_ANCHOR not in text:
        # Free-movement system isn't installed in this project; the
        # vanilla DoesObjectCollideWithObjectAt patch covers it.
        return ""

    original = text
    changes: List[str] = []

    # Edit 1: #include the struct header.
    if _PLAYER_INCLUDE_LINE not in text:
        if _PLAYER_INCLUDE_ANCHOR not in text:
            raise OSError(
                f"{_PLAYER_C_REL}: anchor for top-level #include "
                f"({_PLAYER_INCLUDE_ANCHOR!r}) not found"
            )
        text = text.replace(
            _PLAYER_INCLUDE_ANCHOR,
            _PLAYER_INCLUDE_ANCHOR + "\n" + _PLAYER_INCLUDE_LINE,
            1,
        )
        changes.append('added #include "object_footprint.h"')

    # Edit 2: insert the helper above FreeStepNpcXBlocked.
    if "PORYSUITE-FOOTPRINT free-helper begin" not in text:
        text = text.replace(
            _FREE_HELPER_ANCHOR,
            _FREE_HELPER_FUNC + _FREE_HELPER_ANCHOR,
            1,
        )
        changes.append("installed PlayerFootprintHitsObject")

    # Edit 3: extend the NPC overlap check in both FreeStep functions.
    # The vanilla anchor appears identically in BOTH X and Y functions,
    # so a single replace (no count) catches both -- intentional.
    if "PORYSUITE-FOOTPRINT free-collide begin" not in text:
        if _VANILLA_FREE_OVERLAP not in text:
            raise OSError(
                f"{_PLAYER_C_REL}: vanilla NPC-overlap check pattern not "
                f"found in FreeStepNpcXBlocked/YBlocked; refusing to "
                f"patch a modified copy."
            )
        text = text.replace(_VANILLA_FREE_OVERLAP, _PATCHED_FREE_OVERLAP)
        changes.append(
            "extended FreeStepNpcXBlocked/YBlocked for footprint collision"
        )

    if text == original:
        return ""
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return f"{_PLAYER_C_REL}: " + "; ".join(changes)


# ────────────────────────────── PreScriptAlign skip (optional) ──

# Detection anchor: the script-start alignment task is a free-pixel-
# movement feature absent from vanilla pokefirered.  When present, the
# alignment drags the player toward currentCoords*16, which for a
# multi-cell footprint NPC can shove the hitbox into a painted cell
# (currentCoords is set by the HEAD tile adapter, which advances half
# a tile before the hitbox can fit there).
_PRESCRIPT_ANCHOR = "static void StartPreScriptAlign(const u8 *script)"

# Top-level include so gObjectEventFootprints is visible.
_CONTROL_INCLUDE_LINE = '#include "object_footprint.h"'
_CONTROL_INCLUDE_ANCHOR = '#include "field_player_avatar.h"'

# Static one-shot flag, inserted after the forward declarations.  Read
# + cleared by StartPreScriptAlign; set by GetInteractedObjectEventScript
# on a successful object-event lookup whose graphics ID has a footprint.
_CONTROL_STATIC_ANCHOR = "static void Task_PreScriptAlign(u8 taskId);"
_CONTROL_STATIC_PATCH = """static void Task_PreScriptAlign(u8 taskId);

// PORYSUITE-FOOTPRINT skip-align (flag) begin
// One-shot flag: set in GetInteractedObjectEventScript when the
// targeted object has a multi-cell collision footprint; read and
// cleared in StartPreScriptAlign to bypass the script-start alignment
// task (which otherwise drags the player hitbox into a painted /
// solid footprint cell).  See the skip-align (mark) and (read)
// blocks below for the full reasoning.
static bool8 sPorySuiteFootprintSkipAlign = FALSE;
// PORYSUITE-FOOTPRINT skip-align (flag) end"""


# Byte-exact anchor: the three SelectedObjectEvent/SpecialVar/Facing
# assignments that follow a successful object-event lookup in
# GetInteractedObjectEventScript.  The flag-set block is inserted
# immediately after.  An idempotency check on the assignment line
# avoids re-patching.
_VANILLA_GET_INTERACTED = """    gSelectedObjectEvent = objectEventId;
    gSpecialVar_LastTalked = gObjectEvents[objectEventId].localId;
    gSpecialVar_Facing = direction;"""

_PATCHED_GET_INTERACTED = """    gSelectedObjectEvent = objectEventId;
    gSpecialVar_LastTalked = gObjectEvents[objectEventId].localId;
    gSpecialVar_Facing = direction;
    // PORYSUITE-FOOTPRINT skip-align (mark) begin
    // Mark the script-start alignment to be skipped when the just-
    // looked-up object has a multi-cell collision footprint.  Aligning
    // toward currentCoords*16 in that case can drag the player hitbox
    // into a painted cell and softlock; one-shot, cleared on read in
    // StartPreScriptAlign.
    sPorySuiteFootprintSkipAlign =
        (gObjectEventFootprints[gObjectEvents[objectEventId].graphicsId] != NULL);
    // PORYSUITE-FOOTPRINT skip-align (mark) end"""


# Byte-exact anchor: the head of StartPreScriptAlign through the
# "If already aligned" comment.  Note the em-dash in the comment is
# the project's own punctuation -- preserved here so the match holds.
_VANILLA_START_PRESCRIPT = """static void StartPreScriptAlign(const u8 *script)
{
    struct ObjectEvent *objEvent = &gObjectEvents[gPlayerAvatar.objectEventId];
    s16 targetX = (s16)(objEvent->currentCoords.x << 4);
    s16 targetY = (s16)(objEvent->currentCoords.y << 4);

    // If already aligned, start the script immediately — no task overhead."""

_PATCHED_START_PRESCRIPT = """static void StartPreScriptAlign(const u8 *script)
{
    struct ObjectEvent *objEvent = &gObjectEvents[gPlayerAvatar.objectEventId];
    s16 targetX = (s16)(objEvent->currentCoords.x << 4);
    s16 targetY = (s16)(objEvent->currentCoords.y << 4);

    // PORYSUITE-FOOTPRINT skip-align (read) begin
    // Multi-cell footprint NPCs: skip the alignment entirely.
    // Aligning to currentCoords*16 would drag the player hitbox into a
    // painted cell because the HEAD tile adapter advances currentCoords
    // by half a tile before the hitbox can rest there.  The flag is
    // set by GetInteractedObjectEventScript on a successful object-event
    // lookup with a footprint; one-shot, cleared here.
    if (sPorySuiteFootprintSkipAlign)
    {
        sPorySuiteFootprintSkipAlign = FALSE;
        ScriptContext_SetupScript(script);
        return;
    }
    // PORYSUITE-FOOTPRINT skip-align (read) end

    // If already aligned, start the script immediately — no task overhead."""


def _patch_field_control_avatar_c(project_root: str) -> str:
    """Skip the script-start alignment when the interaction source has
    a footprint -- only on projects that have the alignment task.

    Vanilla pokefirered has no StartPreScriptAlign; without it there is
    no alignment to skip and this function returns ``""``.  When the
    anchor is present, four idempotent edits are applied (each gated by
    a unique code substring so partial reinstalls work cleanly):

      1. ``#include "object_footprint.h"`` next to the existing
         ``field_player_avatar.h`` include.
      2. A static one-shot ``sPorySuiteFootprintSkipAlign`` flag
         declared after the file's forward declarations.
      3. In ``GetInteractedObjectEventScript``, the flag is set after
         a successful object-event lookup based on whether the found
         object's graphics ID has a footprint.
      4. ``StartPreScriptAlign`` reads + clears the flag at its top
         and skips the alignment task when set.
    """
    path = os.path.join(project_root, _CONTROL_C_REL)
    if not os.path.isfile(path):
        return ""
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    if _PRESCRIPT_ANCHOR not in text:
        # Vanilla project -- no alignment task to skip.
        return ""

    original = text
    changes: List[str] = []

    # Edit 1: include the struct header so gObjectEventFootprints is visible.
    if _CONTROL_INCLUDE_LINE not in text:
        if _CONTROL_INCLUDE_ANCHOR not in text:
            raise OSError(
                f"{_CONTROL_C_REL}: anchor for top-level #include "
                f"({_CONTROL_INCLUDE_ANCHOR!r}) not found"
            )
        text = text.replace(
            _CONTROL_INCLUDE_ANCHOR,
            _CONTROL_INCLUDE_ANCHOR + "\n" + _CONTROL_INCLUDE_LINE,
            1,
        )
        changes.append('added #include "object_footprint.h"')

    # Edit 2: declare the static one-shot flag.  Gated on the variable
    # name so the unique sentinel string in the fence comment isn't
    # mistaken for any of the other "skip-align" sentinels below.
    if "static bool8 sPorySuiteFootprintSkipAlign" not in text:
        if _CONTROL_STATIC_ANCHOR not in text:
            raise OSError(
                f"{_CONTROL_C_REL}: anchor for the static flag declaration "
                f"({_CONTROL_STATIC_ANCHOR!r}) not found"
            )
        text = text.replace(
            _CONTROL_STATIC_ANCHOR, _CONTROL_STATIC_PATCH, 1,
        )
        changes.append("declared sPorySuiteFootprintSkipAlign flag")

    # Edit 3: set the flag in every function that does an object-event
    # interaction lookup.  The vanilla 3-line block (gSelectedObjectEvent
    # + gSpecialVar_LastTalked + gSpecialVar_Facing) appears
    # BYTE-IDENTICALLY in both ``GetInteractedObjectEventScript`` (the
    # single-player A-button path) and ``GetInteractedLinkPlayerScript``
    # (the link-cable multiplayer path).  An earlier draft used
    # ``replace(..., 1)`` and only patched the FIRST occurrence -- which
    # happens to be the link-cable function -- leaving single-player
    # interactions un-marked and the schule softlock unfixed.
    #
    # Idempotency is subtle here: ``_PATCHED_GET_INTERACTED`` STARTS
    # with the same 3-line vanilla block (the patched form keeps the
    # original assignments and appends the mark).  So ``vanilla in text``
    # is still True after a successful patch -- the substring is
    # contained inside the patched block.  Counting works:
    #   unpatched_blocks = vanilla_count - mark_count
    # because every patched block contributes +1 to BOTH counts, but
    # every unpatched block contributes +1 only to vanilla_count.
    # Replace exactly that many times so re-runs are no-ops AND a
    # partial state (one function patched, one not) finishes the job.
    vanilla_count = text.count(_VANILLA_GET_INTERACTED)
    mark_count = text.count("// PORYSUITE-FOOTPRINT skip-align (mark) begin")
    unpatched_blocks = vanilla_count - mark_count
    if unpatched_blocks > 0:
        text = text.replace(
            _VANILLA_GET_INTERACTED, _PATCHED_GET_INTERACTED, unpatched_blocks,
        )
        changes.append(
            "hooked footprint mark in GetInteracted{ObjectEvent,LinkPlayer}Script"
        )

    # Edit 4: read + clear the flag at the top of StartPreScriptAlign.
    # Gated on the if-check so we can recognise the patched state.
    if "if (sPorySuiteFootprintSkipAlign)" not in text:
        if _VANILLA_START_PRESCRIPT not in text:
            raise OSError(
                f"{_CONTROL_C_REL}: vanilla StartPreScriptAlign head does "
                f"not match expected body; refusing to patch a modified "
                f"copy."
            )
        text = text.replace(
            _VANILLA_START_PRESCRIPT, _PATCHED_START_PRESCRIPT, 1,
        )
        changes.append("extended StartPreScriptAlign to honour skip-align flag")

    if text == original:
        return ""
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return f"{_CONTROL_C_REL}: " + "; ".join(changes)
