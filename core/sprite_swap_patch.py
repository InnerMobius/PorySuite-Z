"""Engine patcher: a script command that changes an event's overworld sprite.

What it installs
================

A ``setobjectgraphics`` script command, so a script can swap any event's
sprite mid-scene — the player included. RPG Maker has had this as one command
since forever ("Change Event Graphic"); pokefirered has no equivalent at all,
which means an emotion pose, a character picking something up, an NPC changing
outfit, or a chest turning into an open chest are each a C function somebody
has to write by hand.

Three idempotent edits, each fenced with a sentinel so re-running is safe:

- ``asm/macros/event.inc`` — the ``setobjectgraphics localId, graphicsId``
  macro on the next free opcode.
- ``src/scrcmd.c`` — ``ScrCmd_setobjectgraphics``, the handler.
- ``data/script_cmd_table.inc`` — the opcode's table entry, inserted directly
  before ``gScriptCmdTableEnd``.

Why the handler is not a one-liner
==================================

``ObjectEventSetGraphicsId`` allocates fresh VRAM for the new graphics but does
NOT populate it — the frame tiles are DMA'd when the sprite's animation is
(re)started. Swapping without restarting the animation leaves the sprite
rendering as invisible or garbage tiles. The fix is to follow every swap with
``ObjectEventTurn`` on the object's CURRENT facing, which re-inits the facing
animation without forcing it to turn.

That is not a guess: every vanilla site that swaps a sprite's graphics follows
it with an animation restart, never the set on its own —
``StartPlayerAvatarSummonMonForFieldMoveAnim`` and
``StartPlayerAvatarVsSeekerAnim`` in ``src/field_player_avatar.c``, and the
rock-smash field effect. Anyone who writes this handler without the turn will
spend an afternoon on it, so the generated code says why.

The player is addressed with ``OBJ_EVENT_ID_PLAYER`` like every other object
command, and routed to ``gPlayerAvatar.objectEventId`` rather than looked up by
local id, because the player is not in the map's object event list.
"""

from __future__ import annotations

import os
import re

# ── Where things live ───────────────────────────────────────────────────────
_MACROS_REL = os.path.join("asm", "macros", "event.inc")
_SCRCMD_REL = os.path.join("src", "scrcmd.c")
_TABLE_REL = os.path.join("data", "script_cmd_table.inc")

# ── Sentinels. Every inserted block is fenced so the patch is idempotent and
#    a human reading the source can see where it came from. ─────────────────
_MACRO_SENTINEL = "PORYSUITE-SPRITESWAP macro"
_HANDLER_SENTINEL = "PORYSUITE-SPRITESWAP handler"
_TABLE_SENTINEL = "PORYSUITE-SPRITESWAP table"

_TABLE_END_ANCHOR = "gScriptCmdTableEnd::"

#: Name of the command as scripts write it.
COMMAND_NAME = "setobjectgraphics"


class SpriteSwapPatchError(RuntimeError):
    """Raised when an anchor is missing or ambiguous, rather than guessing."""


# ─────────────────────────────────────────────────────────────────────────────
# Small file helpers
# ─────────────────────────────────────────────────────────────────────────────

def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _write(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)


def _require_anchor(content: str, anchor: str, label: str) -> None:
    n = content.count(anchor)
    if n != 1:
        raise SpriteSwapPatchError(
            f"{label}: expected exactly one '{anchor}', found {n}. The file "
            f"has moved on from what this patch understands — fix the patch "
            f"rather than forcing it.")


# ─────────────────────────────────────────────────────────────────────────────
# Opcode discovery
# ─────────────────────────────────────────────────────────────────────────────

def next_free_opcode(project_root: str) -> int:
    """The first unused script opcode, read from the project's own table.

    Counted from the table rather than hard-coded: a project that has already
    added commands of its own would otherwise get a collision, and a colliding
    opcode is a bug that shows up as a completely unrelated command running.
    """
    table = _read(os.path.join(project_root, _TABLE_REL))

    # Count entries BEFORE the end marker only. `gScriptCmdTableEnd::` is
    # followed by its own `.4byte ScrCmd_nop`, which is a terminator and not
    # an opcode — counting it gives a number one too high, and because the
    # table is INDEX-addressed that is not a cosmetic error: the macro would
    # emit an opcode pointing one slot past its own entry, so the command
    # would silently do nothing at runtime.
    body = table.split(_TABLE_END_ANCHOR, 1)[0]
    entries = len(re.findall(r"^\s*\.4byte\s+ScrCmd_", body, re.M))

    # Cross-check against the `@ 0xNN` comments. They are documentation, not
    # the source of truth, but a disagreement means the table is not the dense
    # 0..N-1 run this assumes and guessing would be wrong.
    used = [int(m, 16) for m in re.findall(r"@\s*0x([0-9A-Fa-f]{2})", body)]
    if used and max(used) + 1 != entries:
        raise SpriteSwapPatchError(
            f"The script command table is not a dense run: it has {entries} "
            f"entries but its highest documented opcode is 0x{max(used):02X}. "
            f"Adding a command safely needs that resolved first.")

    nxt = entries
    if nxt > 0xFF:
        raise SpriteSwapPatchError(
            "The script command table is full (256 opcodes). A new command "
            "would need the table widened first.")
    return nxt


# ─────────────────────────────────────────────────────────────────────────────
# Status
# ─────────────────────────────────────────────────────────────────────────────

def is_installed(project_root: str) -> bool:
    """True when all three edits are present."""
    try:
        return all((
            _MACRO_SENTINEL in _read(os.path.join(project_root, _MACROS_REL)),
            _HANDLER_SENTINEL in _read(os.path.join(project_root, _SCRCMD_REL)),
            _TABLE_SENTINEL in _read(os.path.join(project_root, _TABLE_REL)),
        ))
    except OSError:
        return False


def describe(project_root: str) -> str:
    """One line on where this project stands, for a dialog or the log."""
    if is_installed(project_root):
        return (f"`{COMMAND_NAME}` is installed — scripts can change any "
                f"event's sprite, including the player's.")
    try:
        op = next_free_opcode(project_root)
    except Exception as exc:
        return f"Cannot read the script command table: {exc}"
    return (f"`{COMMAND_NAME}` is not installed. It would take opcode "
            f"0x{op:02X}, and touch 3 files.")


# ─────────────────────────────────────────────────────────────────────────────
# The generated code
# ─────────────────────────────────────────────────────────────────────────────

def _macro_block(opcode: int) -> str:
    return (
        f"\n"
        f"\t@ {_MACRO_SENTINEL}\n"
        f"\t@ Change an object event's overworld sprite mid-script.\n"
        f"\t@ localId: the event's LOCALID_* (OBJ_EVENT_ID_PLAYER for the player).\n"
        f"\t@ graphicsId: an OBJ_EVENT_GFX_* constant.\n"
        f"\t@ Both accept a variable, so a script can pick the sprite at runtime.\n"
        f"\t.macro {COMMAND_NAME} localId:req, graphicsId:req\n"
        f"\t.byte 0x{opcode:02x}\n"
        f"\t.2byte \\localId\n"
        f"\t.2byte \\graphicsId\n"
        f"\t.endm\n"
    )


_HANDLER_BLOCK = f"""
// {_HANDLER_SENTINEL}
// Change an object event's overworld sprite. The player is addressed as
// OBJ_EVENT_ID_PLAYER and routed through gPlayerAvatar, because the player is
// not in the map's object event list.
//
// DO NOT drop the ObjectEventTurn call. ObjectEventSetGraphicsId allocates
// fresh VRAM for the new graphics but does NOT populate it -- the frame tiles
// are DMA'd when the sprite's animation is (re)started. Without the turn the
// sprite renders as invisible or garbage tiles. Passing the object's CURRENT
// facing re-inits the animation without making it turn. See the comment above
// BombApplyCarryPose in src/bomb.c, which is where this was learned.
bool8 ScrCmd_{COMMAND_NAME}(struct ScriptContext * ctx)
{{
    u16 localId = VarGet(ScriptReadHalfword(ctx));
    u16 graphicsId = VarGet(ScriptReadHalfword(ctx));
    struct ObjectEvent *objectEvent;
    u8 objectEventId;

    if (localId == OBJ_EVENT_ID_PLAYER)
    {{
        objectEvent = &gObjectEvents[gPlayerAvatar.objectEventId];
    }}
    else
    {{
        if (TryGetObjectEventIdByLocalIdAndMap(localId,
                                               gSaveBlock1Ptr->location.mapNum,
                                               gSaveBlock1Ptr->location.mapGroup,
                                               &objectEventId))
            return FALSE;   // no such event on this map -- do nothing, don't crash
        objectEvent = &gObjectEvents[objectEventId];
    }}

    ObjectEventSetGraphicsId(objectEvent, graphicsId);
    ObjectEventTurn(objectEvent, objectEvent->facingDirection);
    return FALSE;
}}
"""


def _table_block(opcode: int) -> str:
    return (f"\t.4byte ScrCmd_{COMMAND_NAME}"
            f"{' ' * max(1, 30 - len(COMMAND_NAME))}@ 0x{opcode:02x}"
            f"  {_TABLE_SENTINEL}\n")


# ─────────────────────────────────────────────────────────────────────────────
# Install
# ─────────────────────────────────────────────────────────────────────────────

def ensure_sprite_swap_support(project_root: str) -> str:
    """Install the command if it is not already there. Returns a summary.

    Idempotent: a second run reports that nothing was needed. Each file is
    read, checked and written on its own, so a failure part-way leaves the
    earlier files already consistent rather than half-written.
    """
    if is_installed(project_root):
        return f"`{COMMAND_NAME}` was already installed — nothing to do."

    macros_path = os.path.join(project_root, _MACROS_REL)
    scrcmd_path = os.path.join(project_root, _SCRCMD_REL)
    table_path = os.path.join(project_root, _TABLE_REL)
    for path, label in ((macros_path, "event.inc"),
                        (scrcmd_path, "scrcmd.c"),
                        (table_path, "script_cmd_table.inc")):
        if not os.path.isfile(path):
            raise SpriteSwapPatchError(
                f"{label} not found at {path}. Is this a pokefirered project?")

    opcode = next_free_opcode(project_root)
    done: list[str] = []

    # ── 1. the macro ────────────────────────────────────────────────────
    macros = _read(macros_path)
    if _MACRO_SENTINEL not in macros:
        if re.search(rf"^\s*\.macro\s+{COMMAND_NAME}\b", macros, re.M):
            raise SpriteSwapPatchError(
                f"This project already defines a `{COMMAND_NAME}` macro that "
                f"PorySuite did not write. Rename or remove it first — two "
                f"macros with one name will not assemble.")
        macros = macros.rstrip("\n") + "\n" + _macro_block(opcode)
        _write(macros_path, macros)
        done.append(f"added the `{COMMAND_NAME}` macro (opcode 0x{opcode:02X})")

    # ── 2. the handler ──────────────────────────────────────────────────
    scrcmd = _read(scrcmd_path)
    if _HANDLER_SENTINEL not in scrcmd:
        scrcmd = scrcmd.rstrip("\n") + "\n" + _HANDLER_BLOCK
        _write(scrcmd_path, scrcmd)
        done.append("added the handler to scrcmd.c")

    # ── 3. the table entry ──────────────────────────────────────────────
    table = _read(table_path)
    if _TABLE_SENTINEL not in table:
        _require_anchor(table, _TABLE_END_ANCHOR, "script_cmd_table.inc")
        table = table.replace(
            _TABLE_END_ANCHOR,
            _table_block(opcode) + "\n" + _TABLE_END_ANCHOR, 1)
        _write(table_path, table)
        done.append(f"registered opcode 0x{opcode:02X} in the command table")

    # The opcode the macro emits MUST equal the index of the entry just added,
    # because the table is index-addressed. If those ever disagree the command
    # dispatches to whatever sits at that index instead — which at the end of
    # the table is the terminator, so it would appear to do nothing at all.
    verify = _read(table_path).split(_TABLE_END_ANCHOR, 1)[0]
    final_index = len(re.findall(r"^\s*\.4byte\s+ScrCmd_", verify, re.M)) - 1
    if final_index != opcode:
        raise SpriteSwapPatchError(
            f"Internal check failed: the macro emits 0x{opcode:02X} but the "
            f"table entry landed at index 0x{final_index:02X}. The command "
            f"would dispatch to the wrong slot — not installing.")

    if not done:
        return f"`{COMMAND_NAME}` was already installed — nothing to do."
    return (f"Installed `{COMMAND_NAME}`:\n  • "
            + "\n  • ".join(done)
            + "\n\nRebuild the game for the new command to take effect.")
