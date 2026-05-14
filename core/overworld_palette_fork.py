"""Fork an existing overworld sprite's palette into a new unique palette tag.

When DOWP (Dynamic Overworld Palettes) is enabled, the runtime can hold up
to 16 unique palettes at once — but the vanilla pokefirered source still
declares many sprites sharing a small set of palette tags (NPC_BLUE,
NPC_PINK, NPC_GREEN, NPC_WHITE).  That means editing the palette for any
one of those tags still affects every sprite declared to use it, which
defeats the point of DOWP for users who want per-sprite custom palettes.

This module implements the "fork" operation: given an existing sprite and
a new 16-colour palette, it creates a fresh palette tag dedicated to that
one sprite, writes the palette data, registers it in the engine's palette
array, and rewrites the sprite's `.paletteTag` field to the new tag.  The
other sprites that previously shared the palette are left alone.

Files touched (all inside the project's pokefirered tree):
  - `include/constants/event_objects.h`             read-only (for tag scan)
  - `src/event_object_movement.c`                   new `#define` + array entry
  - `src/data/object_events/object_event_graphics.h` new `gObjectEventPal_*` INCBIN
  - `src/data/object_events/object_event_graphics_info.h` rewrite the sprite's
                                                          `.paletteTag` field
  - `graphics/object_events/palettes/<slug>.gbapal` new 32-byte palette file

Returns `(success, applied_list, errors_list)` matching the convention
used by `core.overworld_sprite_creator.create_overworld_sprite`.
"""

from __future__ import annotations

import os
import re
from typing import List, Optional, Tuple

Color = Tuple[int, int, int]


# ───────────────────────────── helpers (private) ─────────────────────────────

def _read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def _write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _to_pascal(slug: str) -> str:
    """`my_npc_thing` -> `MyNpcThing`."""
    return "".join(w.capitalize() for w in slug.split("_") if w)


def _to_upper(slug: str) -> str:
    """`my_npc_thing` -> `MY_NPC_THING`."""
    return slug.upper()


def _existing_palette_tags(root: str) -> dict[str, int]:
    """Scan `event_object_movement.c` for every `OBJ_EVENT_PAL_TAG_*` and
    return `{tag_name: tag_value}`.  Used both to find the next free tag
    value AND to detect name collisions when auto-naming the new tag.
    """
    path = os.path.join(root, "src", "event_object_movement.c")
    if not os.path.isfile(path):
        return {}
    text = _read(path)
    out: dict[str, int] = {}
    for m in re.finditer(
        r"#define\s+(OBJ_EVENT_PAL_TAG_\w+)\s+(0x[0-9a-fA-F]+|\d+)", text
    ):
        try:
            out[m.group(1)] = int(m.group(2), 0)
        except ValueError:
            pass
    return out


def _pick_unique_tag_name(
    existing: dict[str, int], info_name: str
) -> Tuple[str, str]:
    """Return (new_tag, suffix_slug) for the fork.

    `info_name` is the sprite's identifier (e.g. `Boy` or `BugCatcher` —
    in PascalCase as stored in `gObjectEventGraphicsInfo_*`).  The new tag
    is `OBJ_EVENT_PAL_TAG_<UPPER_SNAKE>`, where `<UPPER_SNAKE>` is derived
    from `info_name`.  If that tag already exists, append `_1`, `_2`, ...
    until a free name is found.  The matching slug for the .gbapal
    filename is the lowercase snake-case version of the chosen name.
    """
    base = _camel_to_upper_snake(info_name)
    candidate = f"OBJ_EVENT_PAL_TAG_{base}"
    slug = base.lower()
    if candidate not in existing:
        return candidate, slug
    i = 1
    while f"{candidate}_{i}" in existing:
        i += 1
    return f"{candidate}_{i}", f"{slug}_{i}"


def _camel_to_upper_snake(name: str) -> str:
    """`BugCatcherR` -> `BUG_CATCHER_R`.  `BoldOldMan2` -> `BOLD_OLD_MAN_2`.
    Handles both upper-camel and snake_case input gracefully.
    """
    if "_" in name:
        return name.upper()
    # Insert _ before each capital letter (but not at the very start).
    s = re.sub(r"(?<!^)(?=[A-Z])", "_", name)
    return s.upper()


# Tag values reserved by other pokefirered subsystems that share the same
# OBJ-palette tag namespace as object event palettes.  When forking, we
# MUST avoid these — collision means the sprite renders with the other
# system's palette at runtime.
#
# Sources (vanilla pokefirered):
#   - 0x1004..0x100F  FLDEFF_PAL_TAG_*  (include/constants/field_effects.h)
#   - 0x1100..0x111F  OBJ_EVENT_PAL_TAG_* (vanilla NPCs, player, etc.)
#   - 0x11FF          OBJ_EVENT_PAL_TAG_NONE — the array terminator value
#   - 0x1200          TAG_WEATHER_START — the weather palette base tag.
#                     `StartWeather()` calls `AllocSpritePalette(0x1200)`
#                     every map load; ANY sprite tag of 0x1200 collides
#                     and gets the weather palette at runtime (the bug
#                     that produced Boy's "blue blob" rendering).
#   - 0x1300, 0x1301  FLDEFF_PAL_TAG_FLDEFF_SHADOW / _SURF_BLOB (PorySuite-
#                     added in the field effect palette refactor).
#
# We start forking at 0x1400 — comfortably clear of every reserved range
# above.  64K tags above that before another wrap-around concern.
_RESERVED_TAG_VALUES = frozenset({
    0x1200,  # TAG_WEATHER_START
    0x1300,  # FLDEFF_PAL_TAG_FLDEFF_SHADOW
    0x1301,  # FLDEFF_PAL_TAG_FLDEFF_SURF_BLOB
})
_FORK_BASE_TAG = 0x1400
_OBJ_EVENT_PAL_TAG_NONE = 0x11FF  # not a "real" tag; never count toward "next"


def _next_palette_tag_value(existing: dict[str, int]) -> int:
    """Pick the next free OBJ-palette tag value for a new fork.

    Skips reserved system tag values (TAG_WEATHER_START at 0x1200, the
    field-effect refactor tags at 0x1300/0x1301, etc.) so forked NPC
    palettes can't collide with another subsystem at runtime.  Always
    returns >= `_FORK_BASE_TAG` (0x1400) so we stay above every reserved
    range vanilla pokefirered defines.
    """
    # Filter out the NONE sentinel from "existing" — its value (0x11FF)
    # is a terminator, not a real tag.  `max(existing.values()) + 1` of
    # that would land on 0x1200 (TAG_WEATHER_START) which is exactly the
    # collision that produced the original bug.
    real_values = [
        v for tag, v in existing.items()
        if tag != "OBJ_EVENT_PAL_TAG_NONE"
    ]
    if real_values:
        candidate = max(max(real_values) + 1, _FORK_BASE_TAG)
    else:
        candidate = _FORK_BASE_TAG
    # Skip over reserved values + any value already in use.
    used = set(real_values)
    while candidate in _RESERVED_TAG_VALUES or candidate in used:
        candidate += 1
    return candidate


def _write_gbapal(path: str, colors: List[Color]) -> None:
    """Write a 32-byte raw GBA palette file (16 colours × 2 bytes each,
    GBA 15-bit BGR packed).  Missing slots are padded with black.
    """
    with open(path, "wb") as f:
        n = min(len(colors), 16)
        for i in range(n):
            r, g, b = colors[i]
            r5 = min(max(int(r), 0) >> 3, 31)
            g5 = min(max(int(g), 0) >> 3, 31)
            b5 = min(max(int(b), 0) >> 3, 31)
            val = r5 | (g5 << 5) | (b5 << 10)
            f.write(val.to_bytes(2, "little"))
        for _ in range(16 - n):
            f.write(b"\x00\x00")


def _write_jasc_pal(path: str, colors: List[Color]) -> None:
    """Write a JASC-PAL 0100 text-format palette next to the binary
    .gbapal.  PorySuite's overworld save loop targets the .pal sibling
    when writing edits — without one, the loop falls back to the
    .gbapal path and writes JASC text into the binary file, which
    corrupts the file the build reads (gbagfx then rejects it with
    "Size 2 doesn't evenly divide file size 149" or similar).  Creating
    both files at fork time keeps subsequent saves on the JASC track.
    """
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("JASC-PAL\n0100\n16\n")
        n = min(len(colors), 16)
        for i in range(n):
            r, g, b = colors[i]
            f.write(f"{int(r)} {int(g)} {int(b)}\n")
        for _ in range(16 - n):
            f.write("0 0 0\n")


# ───────────────────────────── public API ────────────────────────────────────

def fork_palette_for_sprite(
    root: str,
    info_name: str,
    new_colors: List[Color],
    *,
    explicit_tag_name: Optional[str] = None,
) -> Tuple[bool, List[str], List[str], Optional[str]]:
    """Forge a new unique palette tag and assign this sprite to it.

    Parameters:
        root:         pokefirered project root.
        info_name:    PascalCase identifier of the sprite as it appears in
                      `gObjectEventGraphicsInfo_<info_name>` (e.g. "Boy",
                      "BugCatcherR").  Used both to find the sprite's
                      block in the C source AND to auto-name the new tag.
        new_colors:   16 RGB tuples.  Slot 0 is the BG / transparent.
        explicit_tag_name: optional override.  If provided, this tag name
                      is used verbatim (still collision-checked).  When
                      None, the name is derived from `info_name` per
                      `_pick_unique_tag_name`.

    Returns:
        (success, applied_messages, error_messages, new_tag_name).
        On success the caller can update its in-memory palette cache so
        the UI sees the new tag immediately without a project reload.
    """
    applied: List[str] = []
    errors: List[str] = []

    # ── Locate / validate the four source files we'll mutate ──────────────
    eom_path = os.path.join(root, "src", "event_object_movement.c")
    gfx_h_path = os.path.join(
        root, "src", "data", "object_events", "object_event_graphics.h"
    )
    gi_path = os.path.join(
        root, "src", "data", "object_events", "object_event_graphics_info.h"
    )
    pal_dir = os.path.join(root, "graphics", "object_events", "palettes")

    for required in (eom_path, gfx_h_path, gi_path):
        if not os.path.isfile(required):
            errors.append(f"Required source file missing: {required}")
            return False, applied, errors, None

    # ── Decide the new tag name ──────────────────────────────────────────
    existing = _existing_palette_tags(root)
    if explicit_tag_name:
        if explicit_tag_name in existing:
            errors.append(
                f"Tag '{explicit_tag_name}' already exists. Pick a "
                f"different name or omit explicit_tag_name to auto-name."
            )
            return False, applied, errors, None
        new_tag = explicit_tag_name
        slug = new_tag[len("OBJ_EVENT_PAL_TAG_"):].lower() \
            if new_tag.startswith("OBJ_EVENT_PAL_TAG_") else new_tag.lower()
    else:
        new_tag, slug = _pick_unique_tag_name(existing, info_name)

    pascal = _to_pascal(slug)
    tag_value = _next_palette_tag_value(existing)

    # ── Step 1: write BOTH the .gbapal (binary, what the build reads via
    # INCBIN) AND the .pal sibling (JASC text, what PorySuite's overworld
    # save loop edits when palette colours change in the editor).
    # Creating only the .gbapal leaves the .pal missing, so PorySuite's
    # save loop falls back to writing JASC text into the .gbapal path
    # itself — which corrupts the binary file and breaks the next build
    # ("Size 2 doesn't evenly divide file size 149").  Both files are
    # written at fork time so subsequent edits stay on the JASC track.
    try:
        os.makedirs(pal_dir, exist_ok=True)
        gbapal_path = os.path.join(pal_dir, f"{slug}.gbapal")
        jasc_path = os.path.join(pal_dir, f"{slug}.pal")
        _write_gbapal(gbapal_path, new_colors)
        _write_jasc_pal(jasc_path, new_colors)
        applied.append(
            f"Wrote palette: graphics/object_events/palettes/"
            f"{slug}.gbapal (+ {slug}.pal sibling)"
        )
    except Exception as e:
        errors.append(f"Write palette files: {e}")
        return False, applied, errors, None

    # ── Step 2: add `#define` for new tag in event_object_movement.c ─────
    try:
        eom_text = _read(eom_path)
        last_def = list(re.finditer(
            r"#define\s+OBJ_EVENT_PAL_TAG_\w+\s+0x[0-9a-fA-F]+",
            eom_text,
        ))
        if not last_def:
            errors.append(
                "Could not find any OBJ_EVENT_PAL_TAG_* defines in "
                "event_object_movement.c — file may be unexpected shape."
            )
            return False, applied, errors, None
        insert_at = last_def[-1].end()
        define_line = f"\n#define {new_tag:<44s} 0x{tag_value:04X}"
        eom_text = eom_text[:insert_at] + define_line + eom_text[insert_at:]
    except Exception as e:
        errors.append(f"Adding tag #define: {e}")
        return False, applied, errors, None

    # ── Step 3: add INCBIN line in object_event_graphics.h ───────────────
    try:
        gfx_text = _read(gfx_h_path)
        last_pal = list(re.finditer(
            r"const u16 gObjectEventPal_\w+\[\][^;]*?;",
            gfx_text,
            flags=re.DOTALL,
        ))
        if not last_pal:
            errors.append(
                "Could not find any gObjectEventPal_* INCBIN entries in "
                "object_event_graphics.h."
            )
            return False, applied, errors, None
        insert_at = last_pal[-1].end()
        incbin_line = (
            f"\nconst u16 gObjectEventPal_{pascal}[] = "
            f'INCBIN_U16("graphics/object_events/palettes/{slug}.gbapal");'
        )
        gfx_text = gfx_text[:insert_at] + incbin_line + gfx_text[insert_at:]
        _write(gfx_h_path, gfx_text)
        applied.append(
            f"Added gObjectEventPal_{pascal} INCBIN to object_event_graphics.h"
        )
    except Exception as e:
        errors.append(f"Adding INCBIN: {e}")
        return False, applied, errors, None

    # ── Step 4: add entry to sObjectEventSpritePalettes[] in eom.c ───────
    # The array ends with `{NULL, OBJ_EVENT_PAL_TAG_NONE}` or `{}` — the
    # vanilla source uses `{}` but some projects have rewritten it.  We
    # search for either form and insert before it.
    try:
        # Find the array — match "sObjectEventSpritePalettes[]" and then the
        # closing terminator.
        arr_match = re.search(
            r"sObjectEventSpritePalettes\[\]\s*=\s*\{",
            eom_text,
        )
        if not arr_match:
            errors.append(
                "Could not find sObjectEventSpritePalettes[] array."
            )
            return False, applied, errors, None

        # From arr_match.end(), scan to find the array close.  The
        # terminator entry is the last `{...}` before the `};` that ends
        # the whole array.  We'll insert just before that terminator.
        scan_from = arr_match.end()
        # Find the matching `};` for this array (single-level braces).
        depth = 1
        i = scan_from
        end_idx = -1
        while i < len(eom_text):
            ch = eom_text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end_idx = i
                    break
            i += 1
        if end_idx < 0:
            errors.append("Unbalanced braces in sObjectEventSpritePalettes.")
            return False, applied, errors, None

        # Walk backwards from end_idx to find the start of the terminator
        # entry — that's the last `{` inside this scope.
        body = eom_text[scan_from:end_idx]
        last_brace_in_body = body.rfind("{")
        if last_brace_in_body < 0:
            # Array is empty (rare); insert before end_idx.
            insert_at = end_idx
            prefix = ""
        else:
            # We want to insert BEFORE the line containing this last `{`,
            # so the terminator stays at the bottom.  Find newline before.
            absolute = scan_from + last_brace_in_body
            line_start = eom_text.rfind("\n", 0, absolute) + 1
            insert_at = line_start
            prefix = ""

        new_entry = (
            f"{prefix}    {{gObjectEventPal_{pascal},"
            f"{' ' * max(1, 32 - len(f'gObjectEventPal_{pascal}'))}"
            f"{new_tag}}},\n"
        )
        eom_text = eom_text[:insert_at] + new_entry + eom_text[insert_at:]
        _write(eom_path, eom_text)
        applied.append(
            f"Added {{gObjectEventPal_{pascal}, {new_tag}}} entry to "
            f"sObjectEventSpritePalettes"
        )
    except Exception as e:
        errors.append(f"Adding palette array entry: {e}")
        return False, applied, errors, None

    # ── Step 5: rewrite this sprite's .paletteTag in graphics_info.h ─────
    try:
        gi_text = _read(gi_path)
        # Match `gObjectEventGraphicsInfo_<info_name> = { ... .paletteTag = X,`
        pat = re.compile(
            r"(gObjectEventGraphicsInfo_" + re.escape(info_name)
            + r"\s*=\s*\{[^;]*?\.paletteTag\s*=\s*)"
            + r"(\w+)"
            + r"(,)",
            re.DOTALL,
        )
        new_gi, n = pat.subn(r"\g<1>" + new_tag + r"\3", gi_text)
        if n == 0:
            errors.append(
                f"Could not find gObjectEventGraphicsInfo_{info_name} in "
                f"object_event_graphics_info.h — sprite may use a different "
                f"info_name, or the block isn't structured as expected."
            )
            # Roll back the .gbapal write so we don't leave a stray file
            # if we can't complete the rewire.
            try:
                os.remove(gbapal_path)
            except OSError:
                pass
            return False, applied, errors, None
        _write(gi_path, new_gi)
        applied.append(
            f"Rewrote gObjectEventGraphicsInfo_{info_name}.paletteTag → "
            f"{new_tag}"
        )
    except Exception as e:
        errors.append(f"Rewriting paletteTag: {e}")
        return False, applied, errors, None

    return True, applied, errors, new_tag
