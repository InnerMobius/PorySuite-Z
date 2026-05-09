"""Project-level text-coloring tunables.

Vanilla pokefirered auto-colors NPC dialogue based on each NPC sprite's
gender, mediated by ``sTextColorTable`` in
``src/dynamic_placeholder_text_util.c`` and applied by
``AddTextPrinterDiffStyle`` in ``src/new_menu_helpers.c``. The table
maps every ``OBJ_EVENT_GFX_*`` to one of:

  * ``NPC_TEXT_COLOR_MALE``    → renders dialogue blue
  * ``NPC_TEXT_COLOR_FEMALE``  → renders dialogue red
  * ``NPC_TEXT_COLOR_NEUTRAL`` → renders dialogue dark-gray (default)
  * ``NPC_TEXT_COLOR_MON``     → renders dialogue dark-gray

This module owns two responsibilities:

1. **The on/off toggle.** When disabled, ``AddTextPrinterDiffStyle`` is
   patched to always use ``DARK_GRAY`` — explicit ``{COLOR}`` tokens in
   the text still work, but the gender-tinted default goes away. The
   patch is idempotent (byte-equality-guarded) and reversible.

2. **Editor preview lookup.** Parses ``sTextColorTable`` so the
   PorySuite text editor can know which color a given NPC graphic
   would render in, and apply that as the implicit default in the
   editor preview.

Both helpers degrade gracefully if the engine source has been
customised in a way they don't recognise — they refuse to write rather
than corrupt the file.
"""

from __future__ import annotations

import os
import re
from typing import Dict, Tuple

from core.file_io import write_text_if_changed


# Files we touch / parse.
_NEW_MENU_HELPERS_REL = os.path.join("src", "new_menu_helpers.c")
_DYNAMIC_TEXT_REL = os.path.join("src", "dynamic_placeholder_text_util.c")

# The vanilla function body we patch. Matched loosely so common
# whitespace / formatting variations don't break the recognition; the
# replacement preserves the function signature exactly.
_VANILLA_DIFFSTYLE_RE = re.compile(
    r"void\s+AddTextPrinterDiffStyle\s*\(\s*bool8\s+allowSkippingDelayWithButtonPress\s*\)\s*"
    r"\{[^}]*?ContextNpcGetTextColor\(\)[^}]*?NPC_TEXT_COLOR_MALE[^}]*?\}",
    re.DOTALL,
)

# The patched (gender-disabled) function body. PorySuite emits this
# verbatim when the toggle is off; the regex below recognises it on
# subsequent passes so we can detect the patched state without
# guessing.
_PATCHED_DIFFSTYLE = (
    "void AddTextPrinterDiffStyle(bool8 allowSkippingDelayWithButtonPress)\n"
    "{\n"
    "    // PorySuite-Z: gender-tinted NPC dialogue disabled. Always\n"
    "    // render messages in DARK_GRAY; explicit {COLOR} tokens in\n"
    "    // text still apply via the normal text-printer path.\n"
    "    gTextFlags.canABSpeedUpPrint = allowSkippingDelayWithButtonPress;\n"
    "    AddTextPrinterParameterized2(0, FONT_NORMAL, gStringVar4, "
    "GetTextSpeedSetting(), NULL, TEXT_COLOR_DARK_GRAY, "
    "TEXT_COLOR_WHITE, TEXT_COLOR_LIGHT_GRAY);\n"
    "}"
)
# Recognise the patched form (so we can read the current state and
# detect already-patched files for idempotent re-runs).
_PATCHED_DIFFSTYLE_MARKER = (
    "PorySuite-Z: gender-tinted NPC dialogue disabled")


def _read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


# ── On/off toggle ─────────────────────────────────────────────────────────


def read_gender_dialogue_enabled(project_root: str) -> bool:
    """Return True if NPC dialogue is gender-tinted in this project.

    "Enabled" means the engine source still has the vanilla form of
    ``AddTextPrinterDiffStyle`` (which selects color based on NPC
    graphic). "Disabled" means PorySuite-Z has patched it to always
    use DARK_GRAY.

    Vanilla projects (never patched) read as enabled. Projects that
    PorySuite-Z patched read as disabled. Projects that someone else
    customized are detected as enabled by default — we only flip to
    disabled when the marker comment is present.
    """
    path = os.path.join(project_root, _NEW_MENU_HELPERS_REL)
    text = _read_text(path)
    if not text:
        return True  # File missing — treat as vanilla / enabled
    return _PATCHED_DIFFSTYLE_MARKER not in text


def write_gender_dialogue_enabled(
    project_root: str,
    enabled: bool,
) -> Tuple[bool, str]:
    """Patch / unpatch ``AddTextPrinterDiffStyle`` to match the toggle.

    When ``enabled`` is True we restore the vanilla function body if
    the file currently has the patched form. When False we replace
    the vanilla body with the always-DARK_GRAY form.

    Idempotent: re-running with the same value writes nothing thanks to
    ``write_text_if_changed``.

    Returns ``(success, message)``. On success the message describes
    what was done; on failure it names the reason.
    """
    path = os.path.join(project_root, _NEW_MENU_HELPERS_REL)
    text = _read_text(path)
    if not text:
        return False, (
            f"src/new_menu_helpers.c not found at {path}. The toggle "
            "cannot be applied without the engine source.")

    has_patched = _PATCHED_DIFFSTYLE_MARKER in text
    has_vanilla = bool(_VANILLA_DIFFSTYLE_RE.search(text))

    if enabled:
        if not has_patched:
            return True, "Gender-tinted dialogue already enabled (vanilla)."
        # Restore: the patched form is unique enough that we can
        # locate it and replace with the canonical vanilla body.
        new_text = _restore_vanilla_diffstyle(text)
        if new_text is None:
            return False, (
                "Couldn't restore vanilla AddTextPrinterDiffStyle — the "
                "patched form was found but the surrounding source has "
                "been customised in a way that prevents safe restoration. "
                "Restore from `git restore src/new_menu_helpers.c` or "
                "edit the function body manually.")
        try:
            write_text_if_changed(path, new_text)
        except Exception as exc:
            return False, f"Failed to write engine source: {exc}"
        return True, "Restored vanilla AddTextPrinterDiffStyle."

    # Disabling.
    if has_patched:
        return True, "Gender-tinted dialogue already disabled."
    if not has_vanilla:
        return False, (
            "Couldn't find the vanilla AddTextPrinterDiffStyle in "
            "src/new_menu_helpers.c. The function body has been "
            "customised. Restore the vanilla form (or apply the patch "
            "manually) before toggling from PorySuite-Z.")
    new_text = _VANILLA_DIFFSTYLE_RE.sub(_PATCHED_DIFFSTYLE, text)
    try:
        write_text_if_changed(path, new_text)
    except Exception as exc:
        return False, f"Failed to write engine source: {exc}"
    return True, (
        "Disabled gender-tinted NPC dialogue. Build the ROM to apply.")


def _restore_vanilla_diffstyle(text: str) -> str | None:
    """Replace the patched form of ``AddTextPrinterDiffStyle`` with the
    canonical vanilla form. Returns ``None`` if the patched function
    can't be cleanly located.

    The replacement is the EXACT vanilla body shipped by upstream
    pokefirered; the user's project may have had additional
    modifications inside that function which we'd lose by restoring
    the canonical form. We only proceed when the patched marker is
    found and the function body matches the PorySuite-emitted shape.
    """
    # Find the patched function block — from the signature to its
    # closing brace.
    pat = re.compile(
        r"void\s+AddTextPrinterDiffStyle\s*\(\s*bool8\s+"
        r"allowSkippingDelayWithButtonPress\s*\)\s*\{[^}]*?\}",
        re.DOTALL,
    )
    m = pat.search(text)
    if not m:
        return None
    if _PATCHED_DIFFSTYLE_MARKER not in m.group(0):
        return None
    vanilla_body = (
        "void AddTextPrinterDiffStyle(bool8 allowSkippingDelayWithButtonPress)\n"
        "{\n"
        "    u8 color;\n"
        "    void *nptr = NULL;\n\n"
        "    gTextFlags.canABSpeedUpPrint = allowSkippingDelayWithButtonPress;    \n"
        "    color = ContextNpcGetTextColor();\n"
        "    if (color == NPC_TEXT_COLOR_MALE)\n"
        "        AddTextPrinterParameterized2(0, FONT_MALE, gStringVar4, "
        "GetTextSpeedSetting(), nptr, TEXT_COLOR_BLUE, "
        "TEXT_COLOR_WHITE, TEXT_COLOR_LIGHT_GRAY);\n"
        "    else if (color == NPC_TEXT_COLOR_FEMALE)\n"
        "        AddTextPrinterParameterized2(0, FONT_FEMALE, gStringVar4, "
        "GetTextSpeedSetting(), nptr, TEXT_COLOR_RED, "
        "TEXT_COLOR_WHITE, TEXT_COLOR_LIGHT_GRAY);\n"
        "    else // NPC_TEXT_COLOR_MON / NPC_TEXT_COLOR_NEUTRAL\n"
        "        AddTextPrinterParameterized2(0, FONT_NORMAL, gStringVar4, "
        "GetTextSpeedSetting(), nptr, TEXT_COLOR_DARK_GRAY, "
        "TEXT_COLOR_WHITE, TEXT_COLOR_LIGHT_GRAY);\n"
        "}"
    )
    return text[:m.start()] + vanilla_body + text[m.end():]


# ── NPC text-color table parser ───────────────────────────────────────────


# Each line of sTextColorTable looks like:
#   [OBJ_EVENT_GFX_LASS / 2] = COLORS(NPC_TEXT_COLOR_FEMALE,
#       NPC_TEXT_COLOR_FEMALE), // OBJ_EVENT_GFX_WOMAN_1
#
# The bracketed gfx-id is the LOW gfx ID (the byte holds two entries).
# The first arg to COLORS() is its color. The // comment names the
# HIGH gfx ID and the second arg is its color.
_TABLE_LINE_RE = re.compile(
    r"\[\s*(OBJ_EVENT_GFX_\w+)\s*/\s*2\s*\]\s*"
    r"=\s*COLORS\(\s*NPC_TEXT_COLOR_(\w+)\s*,\s*NPC_TEXT_COLOR_(\w+)\s*\)\s*,"
    r"\s*//\s*(OBJ_EVENT_GFX_\w+)?",
    re.MULTILINE,
)


def parse_npc_text_color_table(project_root: str) -> Dict[str, str]:
    """Return ``{OBJ_EVENT_GFX_*: 'MALE' | 'FEMALE' | 'NEUTRAL' | 'MON'}``.

    Reads ``src/dynamic_placeholder_text_util.c`` and parses every
    table line. Both the bracketed (low-nibble) gfx id AND the comment
    (high-nibble) gfx id get their respective color recorded.

    Graphics that don't appear in the table fall through with no
    entry — callers should treat absent keys as ``NEUTRAL``.

    Returns an empty dict if the file can't be read or has been
    customised in a way the regex doesn't recognise.
    """
    path = os.path.join(project_root, _DYNAMIC_TEXT_REL)
    text = _read_text(path)
    if not text:
        return {}

    result: Dict[str, str] = {}
    for m in _TABLE_LINE_RE.finditer(text):
        low_gfx = m.group(1)
        low_color = m.group(2)
        high_color = m.group(3)
        high_gfx = m.group(4)
        result[low_gfx] = low_color
        if high_gfx:
            result[high_gfx] = high_color
    return result
