"""Project-level battle economy tunables.

Vanilla pokefirered hardcodes the trainer prize multiplier as the literal
``4`` in ``src/battle_script_commands.c``:

    moneyReward = 4 * lastMonLevel * gBattleStruct->moneyMultiplier
                * (gBattleTypeFlags & BATTLE_TYPE_DOUBLE ? 2 : 1)
                * gTrainerMoneyTable[i].value;

Hacks with non-standard economies (e.g. small-number "rupee" currencies
where 200 is a meaningful payout) need a way to scale that base
multiplier without hand-editing the engine source on every commit.

This module exposes the literal as a ``#define`` macro in
``include/config.h``, then idempotently rewrites the formula line to
reference the macro instead of the literal. Reading and writing both
paths is byte-equality-guarded — running the patch twice with the same
value produces zero git diffs.

Public API:
    * ``read_prize_multiplier(project_root)`` — current macro value, or
      ``4`` (the vanilla default) if the macro isn't defined yet.
    * ``write_prize_multiplier(project_root, value)`` — sets the macro
      and (one-time) patches the engine line to use it.
"""

from __future__ import annotations

import os
import re
from typing import Tuple

from core.file_io import write_text_if_changed

# Default base multiplier. Matches vanilla pokefirered's hardcoded `4`.
DEFAULT_PRIZE_MULTIPLIER = 4

# Macro name. Lives in include/config.h.
MACRO_NAME = "TRAINER_PRIZE_BASE_MULTIPLIER"

# Files we touch.
_CONFIG_H_REL = os.path.join("include", "config.h")
_BATTLE_SCRIPT_REL = os.path.join("src", "battle_script_commands.c")

# Regex for the vanilla formula line. Matches the literal-4 form.
# Capturing group 1 is everything from `moneyReward` up to (and not
# including) the leading `4 *`. Captured suffix preserves the exact
# whitespace/operator formatting of the rest of the line.
_VANILLA_FORMULA_RE = re.compile(
    r"^(\s*moneyReward\s*=\s*)4(\s*\*\s*lastMonLevel\s*\*)",
    re.MULTILINE,
)

# Regex for an already-patched line (the macro is in place).
_PATCHED_FORMULA_RE = re.compile(
    r"^\s*moneyReward\s*=\s*"
    + re.escape(MACRO_NAME)
    + r"\s*\*\s*lastMonLevel\s*\*",
    re.MULTILINE,
)

# Regex for parsing the macro from config.h.
_MACRO_DEFINE_RE = re.compile(
    r"^#define\s+" + re.escape(MACRO_NAME) + r"\s+(\d+)\s*$",
    re.MULTILINE,
)


def _read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def read_prize_multiplier(project_root: str) -> int:
    """Return the current TRAINER_PRIZE_BASE_MULTIPLIER value.

    Falls back to ``DEFAULT_PRIZE_MULTIPLIER`` (4) when the macro hasn't
    been added yet — that's the vanilla state and matches the literal
    that's still hardcoded in the engine source.
    """
    config_h_path = os.path.join(project_root, _CONFIG_H_REL)
    text = _read_text(config_h_path)
    if not text:
        return DEFAULT_PRIZE_MULTIPLIER
    m = _MACRO_DEFINE_RE.search(text)
    if not m:
        return DEFAULT_PRIZE_MULTIPLIER
    try:
        return int(m.group(1))
    except ValueError:
        return DEFAULT_PRIZE_MULTIPLIER


def write_prize_multiplier(
    project_root: str,
    value: int,
) -> Tuple[bool, str]:
    """Set the macro and patch the engine line to use it.

    Behaviour:
      * The macro is set / updated in ``include/config.h`` via a ``#define
        TRAINER_PRIZE_BASE_MULTIPLIER N`` line. If the macro is already
        present with a different value, the value is replaced. If absent,
        a new line is appended.
      * The engine line in ``src/battle_script_commands.c`` is patched
        once: literal ``4 *`` becomes ``TRAINER_PRIZE_BASE_MULTIPLIER *``.
        On subsequent calls the macro reference is already there, so the
        patch is a no-op.

    All writes go through ``write_text_if_changed``, so re-running with
    the same value (or no change) writes nothing and produces no git
    diff. Returns ``(success, message)``. On error, the message names
    which step failed.
    """
    if value < 0 or value > 65535:
        return False, f"Multiplier must be 0–65535. Got {value}."

    config_h_path = os.path.join(project_root, _CONFIG_H_REL)
    bs_path = os.path.join(project_root, _BATTLE_SCRIPT_REL)

    if not os.path.isfile(config_h_path):
        return False, f"include/config.h not found at: {config_h_path}"
    if not os.path.isfile(bs_path):
        return False, (
            "src/battle_script_commands.c not found — engine line cannot "
            "be patched. The macro will still be set in config.h but the "
            "engine won't read it.")

    # ── 1. Update / insert the macro in include/config.h ──────────────
    config_text = _read_text(config_h_path)
    new_define_line = f"#define {MACRO_NAME} {value}"
    if _MACRO_DEFINE_RE.search(config_text):
        new_config = _MACRO_DEFINE_RE.sub(new_define_line, config_text)
    else:
        # Append at end with a single leading newline so we don't crowd
        # against whatever was the last line of config.h.
        new_config = config_text.rstrip("\n") + "\n\n" + new_define_line + "\n"

    try:
        write_text_if_changed(config_h_path, new_config)
    except Exception as exc:
        return False, f"Failed to write config.h: {exc}"

    # ── 2. Patch the engine formula line (idempotent) ─────────────────
    bs_text = _read_text(bs_path)
    if _PATCHED_FORMULA_RE.search(bs_text):
        # Already using the macro — nothing to patch.
        return True, (
            f"Macro updated to {value}. Engine line was already patched "
            f"to use {MACRO_NAME}.")

    if not _VANILLA_FORMULA_RE.search(bs_text):
        # Neither vanilla nor patched form found. The engine source has
        # been customised in a way we don't recognise. Set the macro
        # but tell the caller the engine line wasn't touched.
        return True, (
            f"Macro updated to {value}, but the trainer prize formula "
            f"in src/battle_script_commands.c looks customised. The "
            f"macro is defined but the engine won't read it until you "
            f"manually replace the literal multiplier with "
            f"{MACRO_NAME} on the moneyReward = ... line.")

    new_bs = _VANILLA_FORMULA_RE.sub(
        rf"\g<1>{MACRO_NAME}\g<2>", bs_text)

    # Add a #include for the macro if the file doesn't already pull
    # in config.h. Most pokefirered .c files include "global.h" which
    # transitively includes config.h, but be defensive.
    if "config.h" not in new_bs and '"global.h"' not in new_bs:
        # Insert after the last #include near the top of the file.
        include_lines = list(re.finditer(r'^#include\s+"[^"]+"\s*\n',
                                         new_bs, re.MULTILINE))
        if include_lines:
            insert_at = include_lines[-1].end()
            new_bs = (
                new_bs[:insert_at]
                + '#include "config.h"\n'
                + new_bs[insert_at:])

    try:
        write_text_if_changed(bs_path, new_bs)
    except Exception as exc:
        return False, f"Failed to patch engine source: {exc}"

    return True, (
        f"Set {MACRO_NAME} = {value} and patched the engine formula. "
        f"Build the ROM to apply.")
