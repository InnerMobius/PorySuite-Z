"""Patcher: fix free-pixel-movement coord-event re-fire loop + silent
script skip-align.

In free-pixel-movement projects, ``FieldGetPlayerInput`` carries over
``gFieldInputRecord.tookStep`` / ``checkStandardWildEncounter`` from the
previous frame (Step 2.5).  This produces the "1-frame-delayed" tookStep
signal the rest of the engine expects.

BUT ``ProcessPlayerFieldInput`` (in ``src/field_control_avatar.c``) RE-SETS
``gFieldInputRecord.tookStep = TRUE`` immediately after a step-based script
fires — and the same for ``checkStandardWildEncounter`` on wild-encounter
and walk-into-sign paths.  In vanilla pokefirered those re-sets are dead
state (nothing reads ``gFieldInputRecord.tookStep`` back).  In a free-pixel
project that DOES read it back, the re-set causes the next frame to think
the player took another step — and if the player hasn't moved (they're
locked during the script that just fired) the SAME coord event fires
AGAIN.  For coord events whose own trigger var isn't changed by the
script (e.g. VermilionCity's ``ExitedTicketCheck`` which only resets the
NEIGHBOURING tile's TICKET trigger), this loops forever: HUD flashes
on/off (lock/unlock cycle), no dialog appears because the script has no
msgbox, player is stuck.  The existing ``Fix 20c`` warp-arrival clear at
``field_player_avatar.c``'s warp init already documents the same bug
pattern for warps ("warp loop"), and clears the flag — we apply the same
treatment to step-based scripts here.

Fix: replace the three ``gFieldInputRecord.<flag> = TRUE`` re-sets in
``ProcessPlayerFieldInput`` with ``= FALSE``.  Vanilla behaviour is
preserved (vanilla never read the flag back, so a TRUE-vs-FALSE difference
on the write is invisible there).  Free-pixel projects stop looping.

Gate: only patches projects with the free-pixel-movement carry-over
read at the top of ``FieldGetPlayerInput`` -- vanilla pokefirered does
not have that read, so the TRUE-vs-FALSE re-set is dead state there.
We detect the read pattern; absent it, the patcher is a silent no-op
on vanilla projects.

Sentinel: ``PORYSUITE-FREEINPUT carry-over-clear`` so re-runs are
idempotent and a future revision can self-heal an installed block.
"""

from __future__ import annotations

import os
from typing import List, Tuple

_FCA_REL = os.path.join("src", "field_control_avatar.c")

_SENTINEL = "PORYSUITE-FREEINPUT carry-over-clear"

# Free-pixel-movement carry-over read.  When this is present the project
# reads gFieldInputRecord.tookStep back in FieldGetPlayerInput, which is
# what makes the re-set bug observable.  Vanilla pokefirered lacks it.
_FREE_INPUT_CARRY_OVER_PROBE = "if (gFieldInputRecord.tookStep)"


def _project_has_free_pixel_carry_over(project_root: str) -> bool:
    """Vanilla pokefirered does not read gFieldInputRecord.tookStep back in
    FieldGetPlayerInput; free-pixel-movement projects do.  Returns True
    only when the read pattern is present."""
    path = os.path.join(project_root, _FCA_REL)
    if not os.path.isfile(path):
        return False
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return _FREE_INPUT_CARRY_OVER_PROBE in f.read()
    except OSError:
        return False


# The three re-sets we replace.  Each is the EXACT bytes of one line in
# ProcessPlayerFieldInput's coord-event / signpost / wild-encounter
# success branches.  Replacement preserves indentation.
_REPLACEMENTS = [
    # Coord event / warp / step-based script fired -- DON'T carry tookStep
    # forward, or the same coord event re-fires next frame while the player
    # is still on the trigger tile (they couldn't move during the script).
    (
        "            gFieldInputRecord.tookStep = TRUE;\n",
        "            // PORYSUITE-FREEINPUT carry-over-clear: don't re-set\n"
        "            // tookStep after a step-based script fires.  In vanilla\n"
        "            // this re-set is dead state (FieldGetPlayerInput never\n"
        "            // reads it back); in free-pixel-movement projects the\n"
        "            // Step 2.5 carry-over DOES read it back, so leaving it\n"
        "            // TRUE re-fires the same coord event on the frame after\n"
        "            // the script ends -- infinite loop (HUD flash, no\n"
        "            // dialog, player stuck) for coord events whose trigger\n"
        "            // var isn't changed by the script itself.\n"
        "            gFieldInputRecord.tookStep = FALSE;\n",
    ),
    # Walk-into-sign script success branch.
    (
        "                gFieldInputRecord.checkStandardWildEncounter = TRUE;\n",
        "                // PORYSUITE-FREEINPUT carry-over-clear: same reasoning\n"
        "                // as the tookStep clear above -- vanilla never reads\n"
        "                // this back; carry-over projects do, so a TRUE re-set\n"
        "                // re-fires the encounter check next frame.\n"
        "                gFieldInputRecord.checkStandardWildEncounter = FALSE;\n",
    ),
    # Standard wild-encounter success branch.
    (
        "        gFieldInputRecord.checkStandardWildEncounter = TRUE;\n"
        "        return TRUE;\n"
        "    }\n",
        "        // PORYSUITE-FREEINPUT carry-over-clear: don't double-fire\n"
        "        // wild encounters on the frame after a battle / encounter\n"
        "        // returns to the field (same carry-over loop pattern).\n"
        "        gFieldInputRecord.checkStandardWildEncounter = FALSE;\n"
        "        return TRUE;\n"
        "    }\n",
    ),
]


def ensure_step_based_script_carry_over_fix(
    project_root: str,
) -> Tuple[bool, List[str]]:
    """Install (or refresh) the step-based-script carry-over clear.

    Idempotent: when the sentinel is already present the patcher is a
    no-op.  Vanilla pokefirered (no carry-over read in FieldGetPlayerInput)
    is a silent no-op too.

    Returns ``(success, messages)``.
    """
    messages: List[str] = []
    path = os.path.join(project_root, _FCA_REL)
    if not os.path.isfile(path):
        return True, messages  # nothing to patch
    if not _project_has_free_pixel_carry_over(project_root):
        return True, messages  # vanilla project, silent no-op

    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError as exc:
        return False, [f"step-based carry-over fix: cannot read {_FCA_REL}: {exc}"]

    if _SENTINEL in text:
        # First pass already installed -- skip it but STILL run the
        # silent-skip second pass below (independent sentinel).
        ok2, msgs2 = _patch_silent_script_skip_align(project_root)
        messages.extend(msgs2)
        return ok2, messages

    new_text = text
    applied = 0
    for old, new in _REPLACEMENTS:
        if old in new_text:
            # Replace EVERY occurrence -- there is one coord-event branch,
            # one signpost branch, and one wild-encounter branch.
            new_count = new_text.count(old)
            new_text = new_text.replace(old, new)
            applied += new_count

    if applied == 0:
        # File doesn't match the expected shape (already hand-edited?).
        # Refuse silently -- never corrupt a project we don't recognise.
        return True, [
            f"{_FCA_REL}: step-based carry-over fix anchors not found; "
            f"skipped (file may already be hand-edited)"
        ]

    try:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(new_text)
    except OSError as exc:
        return False, [f"step-based carry-over fix: cannot write {_FCA_REL}: {exc}"]

    messages.append(
        f"{_FCA_REL}: cleared {applied} stale tookStep / wild-encounter "
        f"re-set(s) in ProcessPlayerFieldInput (PORYSUITE-FREEINPUT "
        f"carry-over-clear)"
    )

    # Second pass: silent-script skip-align in StartPreScriptAlign.
    ok2, msgs2 = _patch_silent_script_skip_align(project_root)
    messages.extend(msgs2)
    if not ok2:
        return False, messages
    return True, messages


# ───────────────── Silent-script skip-align in StartPreScriptAlign ──
#
# Engine-side companion to the carry-over-clear above.  When a coord-event
# script is "silent" -- it doesn't show a msgbox, move the player, delay,
# wait for state, or call a special -- the pre-script tile-centre align
# task is pointless.  The script doesn't reference the player's pixel
# position, so aligning before running just adds a visible HUD-flash and
# pixel-snap effect on every step across the trigger tile.  Vanilla
# pokefirered scripts that do this (e.g. VermilionCity's
# ExitedTicketCheck reset on (22,32)/(23,32)) are designed to be
# completely invisible.  Skipping the align makes them invisible again.
#
# Detection: scan the first 16 bytes of the script bytecode for any of
# these "needs align" opcodes:
#   0x09 callstd            (msgbox via callstd MSGBOX_DEFAULT etc.)
#   0x0f loadword           (msgbox text load, used as a pair with callstd)
#   0x25 special            (game engine call -- may move player or open
#                            a menu)
#   0x27 waitstate          (yields to game state machine, e.g. menus)
#   0x28 delay              (visible pause -- timing matters, hold alignment)
#   0x4f applymovement      (may move the player)
#   0x50 applymovement_at   (alt form, may move the player)
#   0x51 waitmovement       (yields to movement -- usually paired with
#                            applymovement)
#
# If we find `end` (0x02) within 16 bytes WITHOUT hitting any of the
# above, the script is silent -- skip the align task and just call
# ScriptContext_SetupScript(script) directly.  16 bytes covers the
# common silent patterns (lockall+setvar+releaseall+end is 8 bytes;
# lockall+setflag+setvar+releaseall+end is 13 bytes).  Longer scripts
# that DO need align won't end within 16 bytes anyway.
#
# Sentinel: ``PORYSUITE-FREEINPUT silent-script-skip-align``.

_SILENT_SKIP_SENTINEL = "PORYSUITE-FREEINPUT silent-script-skip-align"

# Inserted INSIDE StartPreScriptAlign, between the footprint skip-align
# branch and the "already aligned" check.  Uses local arrays only --
# no #include needed.
_SILENT_SKIP_BLOCK = (
    "    // PORYSUITE-FREEINPUT silent-script-skip-align begin\n"
    "    // Silent script -- no msgbox, no player movement, no delay, no\n"
    "    // special, no waitstate -- doesn't need tile-centre alignment.\n"
    "    // Skipping the align avoids the visible HUD-flash + player-snap\n"
    "    // effect on every step across a silent coord-event trigger\n"
    "    // (e.g. VermilionCity's ExitedTicketCheck reset).  Vanilla\n"
    "    // scripts that designed these to be invisible stay invisible.\n"
    "    //\n"
    "    // Opcode-aware whitelist scan: we step the cursor by each opcode's\n"
    "    // ACTUAL byte size (not 1 byte at a time) so setvar's 4-byte arg\n"
    "    // can't false-match `end` (0x02) when the value happens to be 2.\n"
    "    // Any non-whitelisted opcode means \"unknown or needs align\" --\n"
    "    // default to aligning.  Conservative: we only skip when we're\n"
    "    // certain the whole script is composed of cheap, alignment-\n"
    "    // independent commands.\n"
    "    {\n"
    "        const u8 *sp = script;\n"
    "        const u8 *sp_end = script + 32;  // hard cap, longer scripts align\n"
    "        bool8 silent = FALSE;\n"
    "        while (sp < sp_end)\n"
    "        {\n"
    "            u8 op = *sp;\n"
    "            if (op == 0x02) { silent = TRUE; break; }     // end\n"
    "            switch (op)\n"
    "            {\n"
    "            case 0x69: case 0x6a: case 0x6b: case 0x6c:   // lockall/lock/releaseall/release\n"
    "                sp += 1; break;\n"
    "            case 0x37: case 0x38:                          // fadeoutbgm/fadeinbgm\n"
    "            case 0xc7:                                     // textcolor\n"
    "                sp += 2; break;\n"
    "            case 0x29: case 0x2a:                          // setflag/clearflag\n"
    "                sp += 3; break;\n"
    "            case 0x16: case 0x17: case 0x18:               // setvar/addvar/subvar\n"
    "            case 0x19:                                     // copyvar\n"
    "                sp += 5; break;\n"
    "            default:\n"
    "                // Unknown / needs-align opcode -- bail out, align.\n"
    "                sp = sp_end;  // forces loop exit, silent stays FALSE\n"
    "                break;\n"
    "            }\n"
    "        }\n"
    "        if (silent)\n"
    "        {\n"
    "            ScriptContext_SetupScript(script);\n"
    "            return;\n"
    "        }\n"
    "    }\n"
    "    // PORYSUITE-FREEINPUT silent-script-skip-align end\n"
    "\n"
)

# Anchor we insert BEFORE: the existing "already aligned" check inside
# StartPreScriptAlign.  This is the byte-exact line as it appears in
# field_control_avatar.c right after the PORYSUITE-FOOTPRINT skip-align
# (read) block.
_SILENT_SKIP_ANCHOR = (
    "    // If already aligned, start the script immediately"
    " — no task overhead.\n"
    "    if (gPlayerAvatar.pixelX == targetX"
    " && gPlayerAvatar.pixelY == targetY)\n"
)


def _patch_silent_script_skip_align(project_root: str) -> Tuple[bool, List[str]]:
    """Insert the silent-script skip-align fast-path inside
    ``StartPreScriptAlign``.

    Gate: only patches projects that have ``StartPreScriptAlign`` in
    ``field_control_avatar.c`` (i.e. free-pixel-movement projects).
    Vanilla pokefirered has no such function -- silent no-op there.

    Idempotent: detects the sentinel and skips re-installation.
    """
    messages: List[str] = []
    path = os.path.join(project_root, _FCA_REL)
    if not os.path.isfile(path):
        return True, messages

    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError as exc:
        return False, [f"silent-script skip-align: cannot read {_FCA_REL}: {exc}"]

    # Vanilla pokefirered has no StartPreScriptAlign -- silent no-op.
    if "static void StartPreScriptAlign(" not in text:
        return True, messages

    if _SILENT_SKIP_SENTINEL in text:
        return True, messages  # already installed

    if _SILENT_SKIP_ANCHOR not in text:
        # Anchor not found -- file may have been hand-edited or the
        # comment text differs.  Refuse silently rather than corrupt.
        return True, [
            f"{_FCA_REL}: silent-script skip-align anchor not found; "
            f"skipped (StartPreScriptAlign may already be hand-edited)"
        ]

    new_text = text.replace(
        _SILENT_SKIP_ANCHOR,
        _SILENT_SKIP_BLOCK + _SILENT_SKIP_ANCHOR,
        1,
    )

    try:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(new_text)
    except OSError as exc:
        return False, [
            f"silent-script skip-align: cannot write {_FCA_REL}: {exc}"
        ]

    messages.append(
        f"{_FCA_REL}: installed PORYSUITE-FREEINPUT silent-script-skip-align "
        f"fast-path in StartPreScriptAlign"
    )
    return True, messages
