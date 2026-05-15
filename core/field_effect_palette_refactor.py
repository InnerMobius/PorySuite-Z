"""Field effect palette refactor — gives field effect sprites their own
deterministic palette tag + palette data, instead of riding on whatever
happens to be in the OBJ palette slot they get assigned at runtime.

This is a real engine refactor, not a bandaid. Vanilla pokefirered's
design — sprite templates with ``paletteTag = TAG_NONE`` that pick up
"whatever palette is in the slot" — is fundamentally broken under any
dynamic palette system. Once palette slots are not deterministically
assigned, the visual rendering of those sprites becomes a function of
load order, not of the project's intent.

The refactor:

1. Generates a real palette tag (``FLDEFF_PAL_TAG_FLDEFF_<NAME>``) for
   each refactored template.
2. Extracts the palette data from the sprite's source PNG color table.
3. Writes a ``.gbapal`` binary file alongside the existing field effect
   palettes.
4. Adds the ``INCBIN`` declaration to ``object_event_graphics.h``.
5. Adds a ``SpritePalette`` declaration to ``field_effect_objects.h``.
6. Modifies the sprite template(s) to use the real tag.
7. Modifies the field effect script (if applicable) to ``loadfadedpal``
   the palette before spawning the sprite.

Result: the sprite always renders with its dedicated palette, loaded
deterministically by the field effect script, weather-synced at load
time via the standard ``FieldEffectScript_LoadFadedPal`` path.

The PorySuite-Z Field Effect Sprites tab edits the SAME palette data
(both the ``.gbapal`` file and the PNG color table). Save in the UI
means save in-game. No coincidence, no slot roulette.

This module is invoked from ``core.dynamic_ow_pal_patch`` when DOWP
is enabled / disabled. The refactor is part of DOWP because both
share the same premise: palettes load dynamically, sprites must declare
their palette source explicitly.

The REGISTRY at module top declares which templates get refactored.
Adding a new field effect to the refactor is one entry in the registry.
"""

from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Tuple

from core.palette_bake_audit import read_png_color_table
from core.tilemap_data import _write_gbapal_file


# ── Registry ──────────────────────────────────────────────────────────────
#
# Each entry describes one refactor: a single FLDEFF_PAL_TAG constant,
# a single .gbapal file, a single SpritePalette declaration, applied to
# one or more sprite templates that share this palette, and zero or
# more field effect script edits that add ``loadfadedpal`` before the
# sprite is spawned.
#
# To refactor a new field effect, add a registry entry. To extend an
# existing refactor with another shared template (e.g. a new shadow
# size), add the template to its ``templates`` list. Tag values must
# stay unique across the whole table.

REFACTORS: List[Dict] = [
    {
        # All four shadow sizes share one palette. The image content is
        # uniform across sizes (the shadow shape only changes dimensions),
        # so the palette source is the same and one tag is sufficient.
        #
        # Tag values 0x1300+ are the reserved range for refactor entries.
        # 0x1100-0x11FF is OBJ_EVENT_PAL_TAG_* (player + NPC palettes);
        # 0x1004-0x100F is the existing FLDEFF_PAL_TAG_* range; 0x1200+
        # is weather and other engine palettes; battle anim tags live
        # in various spots. 0x1300-0x13FF is unclaimed.
        "name": "Shadow",
        "tag_const": "FLDEFF_PAL_TAG_FLDEFF_SHADOW",
        "tag_value": 0x1300,
        "palette_symbol": "gSpritePalette_FldEff_Shadow",
        "data_symbol": "gFieldEffectPal_FldEff_Shadow",
        "gbapal_path": "graphics/field_effects/palettes/fldeff_shadow.gbapal",
        "source_png": "graphics/field_effects/pics/shadow_medium.png",
        "templates": [
            "gFieldEffectObjectTemplate_ShadowSmall",
            "gFieldEffectObjectTemplate_ShadowMedium",
            "gFieldEffectObjectTemplate_ShadowLarge",
            "gFieldEffectObjectTemplate_ShadowExtraLarge",
        ],
        "script_edits": [
            {
                "label": "gFldEffScript_Shadow",
                "vanilla": "\tcallnative FldEff_Shadow",
                "patched": "\tloadfadedpal_callnative gSpritePalette_FldEff_Shadow, FldEff_Shadow",
            },
        ],
    },
    {
        # Surf blob — the riding-on-water sprite under the player during Surf.
        # Vanilla template has paletteTag = TAG_NONE so it grabs whatever
        # palette ends up at its slot. Under DOWP that's an arbitrary
        # palette, producing wildly wrong colors. Source PNG lives under
        # object_events/pics/misc/ rather than field_effects/pics/ because
        # the surf blob shares the object_event sprite layout, not the
        # general field-effect layout.
        #
        # Additional vanilla quirk: FldEff_SurfBlob hardcodes
        # ``sprite->oam.paletteNum = 0;`` after CreateSpriteAtEnd, which
        # silently overrides the template's tag-based paletteNum. The
        # c_edit below strips that line so the template tag wins.
        "name": "SurfBlob",
        "tag_const": "FLDEFF_PAL_TAG_FLDEFF_SURF_BLOB",
        "tag_value": 0x1301,
        "palette_symbol": "gSpritePalette_FldEff_SurfBlob",
        "data_symbol": "gFieldEffectPal_FldEff_SurfBlob",
        "gbapal_path": "graphics/field_effects/palettes/fldeff_surf_blob.gbapal",
        "source_png": "graphics/object_events/pics/misc/surf_blob.png",
        "templates": [
            "gFieldEffectObjectTemplate_SurfBlob",
        ],
        "script_edits": [
            {
                "label": "gFldEffScript_SurfBlob",
                "vanilla": "\tcallnative FldEff_SurfBlob",
                "patched": "\tloadfadedpal_callnative gSpritePalette_FldEff_SurfBlob, FldEff_SurfBlob",
            },
        ],
        "c_edits": [
            {
                "file": "src/field_effect_helpers.c",
                "vanilla": "        sprite->oam.paletteNum = 0;\n",
                "patched": "        // DOWP: paletteNum is set by CreateSpriteAtEnd from the template's\n        // tag (FLDEFF_PAL_TAG_FLDEFF_SURF_BLOB); don't overwrite it here.\n",
            },
        ],
    },
]


# ── Helpers ───────────────────────────────────────────────────────────────


def _read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def _write(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)


def _self_repair_concatenated_defines(text: str) -> str:
    """Split any concatenated ``#define`` statements that landed on the
    same line via a previous buggy patcher run. Pattern: any non-whitespace
    character immediately followed by ``#define`` — a newline is missing
    between them. Insert one.

    This is a defensive self-heal step. Earlier patcher versions used
    regex-position math with ``\\s*$`` that could under some apply/disable
    sequences eat newlines around the inserted line. The result was
    ``0x100F#define FOO`` instead of ``0x100F\\n#define FOO``, which
    broke the C preprocessor catastrophically (the macro value would
    expand to include the whole next line). Line-based logic in the
    current patcher prevents new corruption, but old projects that
    survived the bad versions still need this repair on next apply.
    """
    return re.sub(r"(\S)(#define\b)", r"\1\n\2", text)


def _ensure_palette_data(project_root: str, refactor: Dict) -> bool:
    """Extract the source PNG's color table and write the .gbapal —
    always, on every apply.

    Each time DOWP is enabled, the .gbapal is rebaked from the PNG. This
    means the PNG color table is the source of truth: edit the PNG (or
    edit the palette in PorySuite's Field Effect Sprites tab, which
    bakes into the PNG and the .gbapal simultaneously), disable + enable
    DOWP, and the build picks up the new colors. No skip-if-exists
    shortcut; the slight cost of re-extracting is the price of the
    PNG-is-truth invariant.

    Returns True if the .gbapal was written successfully.
    """
    png_path = os.path.join(project_root, refactor["source_png"])
    if not os.path.isfile(png_path):
        return False

    colors = read_png_color_table(png_path)
    if not colors:
        return False

    # Pad to 16 colors with black if the PNG had fewer (4bpp expects 16).
    while len(colors) < 16:
        colors.append((0, 0, 0))
    colors = colors[:16]

    gbapal_path = os.path.join(project_root, refactor["gbapal_path"])
    return _write_gbapal_file(gbapal_path, colors)


def _add_tag_constant(project_root: str, tag_const: str, tag_value: int) -> Tuple[bool, str]:
    """Add the FLDEFF_PAL_TAG_FLDEFF_<NAME> constant to
    include/constants/field_effects.h next to the existing FLDEFF_PAL_TAG
    entries. Idempotent: if the constant is already present, no-op.

    Line-based. No regex-position math, no `\\s*$` greediness traps.
    """
    fe_path = os.path.join(project_root, "include", "constants", "field_effects.h")
    if not os.path.isfile(fe_path):
        return False, f"{fe_path} not found"

    text = _read(fe_path)
    repaired = _self_repair_concatenated_defines(text)
    if repaired != text:
        text = repaired
        _write(fe_path, text)

    if f"#define {tag_const}" in text:
        return False, "already present"

    lines = text.splitlines(keepends=True)
    define_re = re.compile(r"^#define\s+FLDEFF_PAL_TAG_\w+\s+0x[0-9A-Fa-f]+")

    last_idx = -1
    for i, line in enumerate(lines):
        if define_re.match(line):
            last_idx = i

    new_line = f"#define {tag_const:32s} 0x{tag_value:04X}\n"

    if last_idx >= 0:
        # Ensure the line we're inserting after ends with a newline. If a
        # previous apply/disable cycle stripped its newline (which we've
        # seen happen via greedy regex matches in earlier patcher revisions),
        # repair it before inserting so the new line lands cleanly.
        if not lines[last_idx].endswith("\n"):
            lines[last_idx] = lines[last_idx] + "\n"
        lines.insert(last_idx + 1, new_line)
    else:
        # No existing FLDEFF_PAL_TAG — append before the closing #endif if
        # the file has one, otherwise at end of file.
        endif_idx = -1
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].strip().startswith("#endif"):
                endif_idx = i
                break
        if endif_idx >= 0:
            lines.insert(endif_idx, new_line)
        else:
            if lines and not lines[-1].endswith("\n"):
                lines[-1] = lines[-1] + "\n"
            lines.append(new_line)

    _write(fe_path, "".join(lines))
    return True, "added"


def _remove_tag_constant(project_root: str, tag_const: str) -> Tuple[bool, str]:
    """Reverse of _add_tag_constant. Line-based. Idempotent."""
    fe_path = os.path.join(project_root, "include", "constants", "field_effects.h")
    if not os.path.isfile(fe_path):
        return False, f"{fe_path} not found"

    text = _read(fe_path)
    if f"#define {tag_const}" not in text:
        return False, "already removed"

    lines = text.splitlines(keepends=True)
    define_re = re.compile(rf"^#define\s+{re.escape(tag_const)}\s+0x[0-9A-Fa-f]+\b")

    new_lines = []
    removed = False
    for line in lines:
        if not removed and define_re.match(line):
            removed = True
            continue
        new_lines.append(line)

    if not removed:
        return False, "could not remove"

    _write(fe_path, "".join(new_lines))
    return True, "removed"


def _add_incbin_declaration(project_root: str, data_symbol: str, gbapal_path: str) -> Tuple[bool, str]:
    """Add the INCBIN_U16 declaration to object_event_graphics.h next to
    the existing gFieldEffectPal_* INCBIN lines. Idempotent. Line-based.
    """
    oeg_path = os.path.join(project_root, "src", "data", "object_events", "object_event_graphics.h")
    if not os.path.isfile(oeg_path):
        return False, f"{oeg_path} not found"

    text = _read(oeg_path)
    if f"const u16 {data_symbol}[]" in text:
        return False, "already present"

    lines = text.splitlines(keepends=True)
    incbin_re = re.compile(r"^const u16 gFieldEffectPal_\w+\[\] = INCBIN_U16\(")

    last_idx = -1
    for i, line in enumerate(lines):
        if incbin_re.match(line):
            last_idx = i

    new_line = f'const u16 {data_symbol}[] = INCBIN_U16("{gbapal_path}");\n'

    if last_idx >= 0:
        if not lines[last_idx].endswith("\n"):
            lines[last_idx] = lines[last_idx] + "\n"
        lines.insert(last_idx + 1, new_line)
    else:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] = lines[-1] + "\n"
        lines.append("\n")
        lines.append(new_line)

    _write(oeg_path, "".join(lines))
    return True, "added"


def _remove_incbin_declaration(project_root: str, data_symbol: str) -> Tuple[bool, str]:
    oeg_path = os.path.join(project_root, "src", "data", "object_events", "object_event_graphics.h")
    if not os.path.isfile(oeg_path):
        return False, f"{oeg_path} not found"

    text = _read(oeg_path)
    if f"{data_symbol}[]" not in text:
        return False, "already removed"

    lines = text.splitlines(keepends=True)
    target_re = re.compile(rf"^const u16 {re.escape(data_symbol)}\[\] = INCBIN_U16\(")

    new_lines = []
    removed = False
    for line in lines:
        if not removed and target_re.match(line):
            removed = True
            continue
        new_lines.append(line)

    if not removed:
        return False, "could not remove"

    _write(oeg_path, "".join(new_lines))
    return True, "removed"


def _add_sprite_palette_declaration(
    project_root: str,
    palette_symbol: str,
    data_symbol: str,
    tag_const: str,
) -> Tuple[bool, str]:
    """Add the ``const struct SpritePalette gSpritePalette_FldEff_<Name>``
    declaration to field_effect_objects.h, near the existing
    gSpritePalette_GeneralFieldEffect0/1 lines. Idempotent. Line-based.
    """
    feo_path = os.path.join(project_root, "src", "data", "field_effects", "field_effect_objects.h")
    if not os.path.isfile(feo_path):
        return False, f"{feo_path} not found"

    text = _read(feo_path)
    if f"const struct SpritePalette {palette_symbol}" in text:
        return False, "already present"

    lines = text.splitlines(keepends=True)
    decl_re = re.compile(r"^const struct SpritePalette gSpritePalette_\w+ = \{")

    last_idx = -1
    for i, line in enumerate(lines):
        if decl_re.match(line):
            last_idx = i

    new_line = (
        f"const struct SpritePalette {palette_symbol} = "
        f"{{ .data = {data_symbol}, .tag = {tag_const} }};\n"
    )

    if last_idx >= 0:
        if not lines[last_idx].endswith("\n"):
            lines[last_idx] = lines[last_idx] + "\n"
        lines.insert(last_idx + 1, new_line)
    else:
        # No existing palette declarations — prepend at top of file.
        lines.insert(0, new_line)

    _write(feo_path, "".join(lines))
    return True, "added"


def _remove_sprite_palette_declaration(project_root: str, palette_symbol: str) -> Tuple[bool, str]:
    feo_path = os.path.join(project_root, "src", "data", "field_effects", "field_effect_objects.h")
    if not os.path.isfile(feo_path):
        return False, f"{feo_path} not found"

    text = _read(feo_path)
    if palette_symbol not in text:
        return False, "already removed"

    lines = text.splitlines(keepends=True)
    target_re = re.compile(rf"^const struct SpritePalette {re.escape(palette_symbol)} = \{{")

    new_lines = []
    removed = False
    for line in lines:
        if not removed and target_re.match(line):
            removed = True
            continue
        new_lines.append(line)

    if not removed:
        return False, "could not remove"

    _write(feo_path, "".join(new_lines))
    return True, "removed"


def _patch_template_palette_tag(
    project_root: str,
    template_symbol: str,
    new_tag: str,
) -> Tuple[bool, str]:
    """Find the SpriteTemplate struct named *template_symbol* in
    field_effect_objects.h and change its ``.paletteTag = TAG_NONE`` line
    to ``.paletteTag = <new_tag>``. Operates on the first match inside
    the named struct's body. Idempotent.
    """
    feo_path = os.path.join(project_root, "src", "data", "field_effects", "field_effect_objects.h")
    if not os.path.isfile(feo_path):
        return False, f"{feo_path} not found"

    text = _read(feo_path)
    # Match the struct body from its declaration line through the
    # closing brace. Allow whitespace variation.
    pat = re.compile(
        rf"(const struct SpriteTemplate {re.escape(template_symbol)}\s*=\s*\{{[^}}]*?\.paletteTag\s*=\s*)(TAG_NONE|{re.escape(new_tag)})",
        re.DOTALL,
    )
    m = pat.search(text)
    if not m:
        return False, f"{template_symbol}: paletteTag line not found"
    if m.group(2) == new_tag:
        return False, "already patched"

    new_text = text[:m.start(2)] + new_tag + text[m.end(2):]
    _write(feo_path, new_text)
    return True, "patched"


def _restore_template_palette_tag(
    project_root: str,
    template_symbol: str,
    patched_tag: str,
) -> Tuple[bool, str]:
    """Reverse of _patch_template_palette_tag — restore TAG_NONE."""
    feo_path = os.path.join(project_root, "src", "data", "field_effects", "field_effect_objects.h")
    if not os.path.isfile(feo_path):
        return False, f"{feo_path} not found"

    text = _read(feo_path)
    pat = re.compile(
        rf"(const struct SpriteTemplate {re.escape(template_symbol)}\s*=\s*\{{[^}}]*?\.paletteTag\s*=\s*)({re.escape(patched_tag)}|TAG_NONE)",
        re.DOTALL,
    )
    m = pat.search(text)
    if not m:
        return False, f"{template_symbol}: paletteTag line not found"
    if m.group(2) == "TAG_NONE":
        return False, "already restored"

    new_text = text[:m.start(2)] + "TAG_NONE" + text[m.end(2):]
    _write(feo_path, new_text)
    return True, "restored"


def _patch_script_line(
    project_root: str,
    label: str,
    vanilla_line: str,
    patched_line: str,
) -> Tuple[bool, str]:
    """In data/field_effect_scripts.s, find the line matching
    *vanilla_line* immediately following the *label* and replace it
    with *patched_line*. Idempotent.
    """
    s_path = os.path.join(project_root, "data", "field_effect_scripts.s")
    if not os.path.isfile(s_path):
        return False, f"{s_path} not found"

    text = _read(s_path)
    # Find the label, then look for the vanilla line within a small
    # window after it (before the next label or the `end` directive).
    label_pat = re.compile(rf"^{re.escape(label)}::\s*$", re.MULTILINE)
    m = label_pat.search(text)
    if not m:
        return False, f"label {label} not found"

    # Search window: from label end to next blank-line block or 8 lines.
    window_start = m.end()
    window_end = min(len(text), window_start + 400)
    window = text[window_start:window_end]

    if patched_line in window:
        return False, "already patched"
    if vanilla_line not in window:
        return False, f"{label}: vanilla line not found in body"

    # Replace within the window only (not across the whole file).
    new_window = window.replace(vanilla_line, patched_line, 1)
    new_text = text[:window_start] + new_window + text[window_end:]
    _write(s_path, new_text)
    return True, "patched"


def _patch_c_edit(
    project_root: str,
    rel_file: str,
    vanilla: str,
    patched: str,
) -> Tuple[bool, str]:
    """Apply a free-form C-source edit. Anchors on the exact ``vanilla``
    substring (typically a single line of C), replaces with ``patched``.
    Idempotent — if ``patched`` is already present, no-op."""
    path = os.path.join(project_root, rel_file)
    if not os.path.isfile(path):
        return False, f"{rel_file} not found"
    text = _read(path)
    if patched in text:
        return False, "already patched"
    if vanilla not in text:
        return False, f"{rel_file}: vanilla text not found"
    text = text.replace(vanilla, patched, 1)
    _write(path, text)
    return True, "patched"


def _restore_c_edit(
    project_root: str,
    rel_file: str,
    vanilla: str,
    patched: str,
) -> Tuple[bool, str]:
    """Reverse of _patch_c_edit."""
    path = os.path.join(project_root, rel_file)
    if not os.path.isfile(path):
        return False, f"{rel_file} not found"
    text = _read(path)
    if vanilla in text:
        return False, "already restored"
    if patched not in text:
        return False, f"{rel_file}: patched text not found"
    text = text.replace(patched, vanilla, 1)
    _write(path, text)
    return True, "restored"


def _restore_script_line(
    project_root: str,
    label: str,
    vanilla_line: str,
    patched_line: str,
) -> Tuple[bool, str]:
    """Reverse of _patch_script_line."""
    s_path = os.path.join(project_root, "data", "field_effect_scripts.s")
    if not os.path.isfile(s_path):
        return False, f"{s_path} not found"

    text = _read(s_path)
    label_pat = re.compile(rf"^{re.escape(label)}::\s*$", re.MULTILINE)
    m = label_pat.search(text)
    if not m:
        return False, f"label {label} not found"
    window_start = m.end()
    window_end = min(len(text), window_start + 400)
    window = text[window_start:window_end]

    if vanilla_line in window:
        return False, "already restored"
    if patched_line not in window:
        return False, f"{label}: patched line not found in body"

    new_window = window.replace(patched_line, vanilla_line, 1)
    new_text = text[:window_start] + new_window + text[window_end:]
    _write(s_path, new_text)
    return True, "restored"


# ── Public API ────────────────────────────────────────────────────────────


# ════════════════════════════════════════════════════════════════════════════
# Auto-discovery: scan field_effect_objects.h for any sprite template still
# carrying `paletteTag = TAG_NONE` and synthesise a refactor entry for each.
# This is the structural answer to the "whack-a-mole" pattern — every time
# the user added DOWP and hit another broken field-effect sprite, we had to
# manually add an explicit REFACTORS entry.  Now the catch is automatic: any
# new vanilla-shaped template (or a template the user adds themselves) that
# leaves paletteTag unset gets discovered, allocated a unique tag from the
# 0x1302+ range, hooked into a freshly-baked .gbapal, and patched through.
# ════════════════════════════════════════════════════════════════════════════

# State file recording auto-discovered refactors that have been applied.
# Lives in the project root.  Lets `remove()` reverse what `apply()` did
# without having to re-scan (which wouldn't work — by the time we're
# reversing, the paletteTag values are no longer TAG_NONE).
_AUTO_STATE_REL = os.path.join("porysuite", "field_effect_auto_refactors.json")


# Reserved tag values we must never allocate to a new fldeff palette.
# Mirrors the namespace map documented in BUGS.md.  If a future engine
# refactor claims more values in 0x1300-0x13FF, add them here.
_RESERVED_TAG_VALUES = {
    0x1200,  # TAG_WEATHER_START (and adjacent weather sub-tags)
}


_TEMPLATE_NAME_RE = re.compile(
    r"const\s+struct\s+SpriteTemplate\s+(gFieldEffectObjectTemplate_(\w+))\s*=\s*\{",
)


def _parse_template_metadata(project_root: str) -> List[Dict[str, str]]:
    """Walk `field_effect_objects.h` and return one dict per
    `gFieldEffectObjectTemplate_*` struct.

    Each dict contains:
      - ``template_symbol``: full symbol name
      - ``name``: PascalCase suffix
      - ``palette_tag``: literal text of the ``.paletteTag = ...`` line
        (may be ``TAG_NONE`` or a real ``FLDEFF_PAL_TAG_*`` constant)
      - ``images``: literal text of the ``.images = ...`` line, or
        ``"NULL"`` / missing entries flagged as ``None``
      - ``pic_symbol``: ``gFieldEffectObjectPic_X`` extracted from the
        first ``overworld_frame`` entry of the pic table, when present.
        ``None`` when the template uses no pic table.

    The parser is line-oriented inside each struct body — robust to
    formatting variations (extra blank lines, comments).
    """
    feo_path = os.path.join(
        project_root, "src", "data", "field_effects", "field_effect_objects.h"
    )
    if not os.path.isfile(feo_path):
        return []
    text = _read(feo_path)

    out: List[Dict[str, str]] = []
    for m in _TEMPLATE_NAME_RE.finditer(text):
        template_symbol = m.group(1)
        name = m.group(2)
        # Body is everything between the opening { and its matching }.
        body_start = m.end() - 1  # position of `{`
        depth = 0
        body_end = body_start
        for i in range(body_start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    body_end = i
                    break
        body = text[body_start:body_end]

        # Extract palette tag (literal text after the `=`).
        pal_m = re.search(r"\.paletteTag\s*=\s*([^,\n]+?)\s*,", body)
        palette_tag = pal_m.group(1).strip() if pal_m else ""

        # Extract images field (look for sPicTable_X or NULL).
        img_m = re.search(r"\.images\s*=\s*([^,\n]+?)\s*,", body)
        images = img_m.group(1).strip() if img_m else None

        pic_symbol = None
        if images and images != "NULL":
            # images = sPicTable_X — find that pic table block and
            # extract the gFieldEffectObjectPic_Y from its first frame.
            pic_table_re = re.compile(
                rf"static\s+const\s+struct\s+SpriteFrameImage\s+{re.escape(images)}\s*\[\]\s*=\s*\{{([^}}]*)\}}",
                re.DOTALL,
            )
            pt = pic_table_re.search(text)
            if pt:
                frame_m = re.search(
                    r"overworld_frame\s*\(\s*(gFieldEffectObjectPic_\w+)",
                    pt.group(1),
                )
                if frame_m:
                    pic_symbol = frame_m.group(1)

        out.append({
            "template_symbol": template_symbol,
            "name": name,
            "palette_tag": palette_tag,
            "images": images,
            "pic_symbol": pic_symbol,
        })
    return out


def _parse_pic_to_png_map(project_root: str) -> Dict[str, str]:
    """Map ``gFieldEffectObjectPic_X`` → relative path of source PNG.

    Walks `src/data/object_events/object_event_graphics.h` (which holds
    all field-effect INCBINs alongside object event ones) and extracts the
    `.4bpp` path from each line, swapping the extension to `.png` for the
    source.
    """
    graphics_h = os.path.join(
        project_root, "src", "data", "object_events", "object_event_graphics.h"
    )
    if not os.path.isfile(graphics_h):
        return {}
    text = _read(graphics_h)
    result: Dict[str, str] = {}
    for m in re.finditer(
        r"(gFieldEffectObjectPic_\w+)\s*\[\]\s*=\s*INCBIN_U\d+\(\"([^\"]+)\.4bpp\"\)",
        text,
    ):
        symbol = m.group(1)
        rel_4bpp = m.group(2)
        result[symbol] = rel_4bpp + ".png"
    return result


def _parse_fldeffobj_to_template(project_root: str) -> Dict[str, str]:
    """Map ``FLDEFFOBJ_X`` → ``gFieldEffectObjectTemplate_X`` by parsing
    the template-pointers array.
    """
    fp_path = os.path.join(
        project_root, "src", "data", "field_effects",
        "field_effect_object_template_pointers.h",
    )
    if not os.path.isfile(fp_path):
        return {}
    text = _read(fp_path)
    return dict(re.findall(
        r"\[(FLDEFFOBJ_\w+)\]\s*=\s*&(gFieldEffectObjectTemplate_\w+)",
        text,
    ))


def _script_label_for(project_root: str, name: str) -> str:
    """Return the literal `gFldEffScript_<X>::` label that corresponds
    to template `<name>`, or empty string if no matching script exists.

    The mapping is usually direct (`Bird` → `gFldEffScript_Bird`), but
    a few vanilla templates have a "Placeholder" suffix that the script
    name doesn't carry (`SandDisguisePlaceholder` →
    `gFldEffScript_SandDisguise`).  Try the direct match first, then
    fall back to stripping common suffixes.
    """
    s_path = os.path.join(project_root, "data", "field_effect_scripts.s")
    if not os.path.isfile(s_path):
        return ""
    text = _read(s_path)
    candidates = [name]
    if name.endswith("Placeholder"):
        candidates.append(name[: -len("Placeholder")])
    for cand in candidates:
        label = f"gFldEffScript_{cand}"
        if f"{label}::" in text:
            return label
    return ""


def _has_script_label(project_root: str, name: str) -> bool:
    """True when a corresponding script label exists for `<name>`."""
    return bool(_script_label_for(project_root, name))


def _script_callnative_func(project_root: str, label: str) -> str:
    """Return the literal function name that *label* currently passes to
    ``callnative``, or empty string if the script body can't be parsed.

    Vanilla pokefirered's scripts don't follow a strict
    ``FldEff_<Name>`` naming convention — TreeDisguise calls
    ``ShowTreeDisguiseFieldEffect``, MountainDisguise calls
    ``ShowMountainDisguiseFieldEffect``, and so on.  The patcher must
    use whatever name is actually in the script body so the patched
    ``loadfadedpal_callnative`` keeps the same target.

    The scan window is bounded to the current script's body (between
    `label::` and the next `^gFldEffScript_` label or `^end` directive),
    not a fixed character count — otherwise short scripts leak into the
    next entry and we pick up the wrong callnative function.
    """
    s_path = os.path.join(project_root, "data", "field_effect_scripts.s")
    if not os.path.isfile(s_path):
        return ""
    text = _read(s_path)
    label_pat = re.compile(rf"^{re.escape(label)}::\s*$", re.MULTILINE)
    m = label_pat.search(text)
    if not m:
        return ""
    # Bound the window at the next script label or `end` directive.
    rest = text[m.end():]
    next_label = re.search(r"^gFldEffScript_\w+::\s*$", rest, re.MULTILINE)
    next_end = re.search(r"^\s*end\s*$", rest, re.MULTILINE)
    bound = min(
        (x.start() for x in (next_label, next_end) if x is not None),
        default=len(rest),
    )
    window = rest[:bound]
    # Prefer the already-patched form (so we extract the function name
    # even if the patcher has run before on this script).
    patched_m = re.search(
        r"^\s*loadfadedpal_callnative\s+\S+\s*,\s*(\S+)",
        window, re.MULTILINE,
    )
    if patched_m:
        return patched_m.group(1).strip().rstrip(",")
    call_m = re.search(r"^\s*callnative\s+(\S+)", window, re.MULTILINE)
    if call_m:
        return call_m.group(1).strip().rstrip(",")
    return ""


def _find_c_create_sites(
    project_root: str, fldeffobj_const: str,
) -> List[Tuple[str, str]]:
    """Find every line in C source under `src/` that creates a sprite
    via the named FLDEFFOBJ constant.

    Returns a list of ``(relative_path, exact_line_text)`` tuples.  Each
    line is captured verbatim — including leading whitespace — so a
    subsequent string-replace patch hits the exact bytes on disk.
    """
    pat = re.compile(
        r"^[^\n]*CreateSprite[A-Za-z_]*\s*\(\s*gFieldEffectObjectTemplatePointers\s*\[\s*"
        + re.escape(fldeffobj_const)
        + r"\s*\][^\n]*$",
        re.MULTILINE,
    )
    sites: List[Tuple[str, str]] = []
    src_root = os.path.join(project_root, "src")
    for dirpath, _, filenames in os.walk(src_root):
        for fn in filenames:
            if not fn.endswith(".c"):
                continue
            abs_path = os.path.join(dirpath, fn)
            try:
                text = _read(abs_path)
            except OSError:
                continue
            for m in pat.finditer(text):
                rel = os.path.relpath(abs_path, project_root).replace("\\", "/")
                sites.append((rel, m.group(0)))
    return sites


def _find_palettenum_override_block(
    project_root: str, rel_file: str, create_site_line: str,
) -> Tuple[str, str]:
    """Locate a `sprite->oam.paletteNum = 0;` override following the
    given create-site call.  Returns a tuple of
    ``(intervening_text, override_line)`` where ``intervening_text``
    is the exact text between the end of the create line and the start
    of the override line (typically a short declaration block) — this
    is what binds the override to its specific call site, so the
    surrounding c_edit can target one occurrence at a time even when
    multiple call sites share identical override text.

    Returns ``("", "")`` if no override is found within ~20 lines or
    if `create_site_line` itself isn't in the file.

    Same pattern that the SurfBlob refactor handles via its explicit
    c_edit: some FldEff_* C functions reset `paletteNum` to 0 right
    after `CreateSprite`, silently undoing the template-tag-resolved
    palette slot.  We detect those and emit a stripping c_edit so the
    auto-discovery covers the same case without a hand-written entry.
    """
    abs_path = os.path.join(project_root, rel_file)
    if not os.path.isfile(abs_path):
        return "", ""
    text = _read(abs_path)
    idx = text.find(create_site_line)
    if idx < 0:
        return "", ""
    window_start = idx + len(create_site_line)
    window_end = min(len(text), window_start + 800)
    close_brace = text.find("\n}", window_start, window_end)
    if close_brace >= 0:
        window_end = close_brace
    window = text[window_start:window_end]
    m = re.search(
        r"^[ \t]*(?:sprite|gSprites\s*\[\s*\w+\s*\])->oam\.paletteNum\s*=\s*0\s*;[^\n]*$",
        window, re.MULTILINE,
    )
    if not m:
        return "", ""
    return window[: m.start()], m.group(0)


class _FldeffTagAllocator:
    """Assigns unique FLDEFF_PAL_TAG_FLDEFF_* values from the 0x1302+ range.

    Pre-claims every value used by the explicit REFACTORS list plus any
    reserved-namespace values (currently `0x1200` for TAG_WEATHER_START).
    Subsequent `allocate()` calls return the next free slot.
    """

    def __init__(self) -> None:
        self._used = set(_RESERVED_TAG_VALUES)
        for r in REFACTORS:
            self._used.add(r["tag_value"])
        self._next = 0x1302  # first slot after Shadow (0x1300) + SurfBlob (0x1301)

    def allocate(self) -> int:
        while self._next in self._used or self._next > 0x13FF:
            if self._next > 0x13FF:
                raise RuntimeError(
                    "FLDEFF palette tag range 0x1300-0x13FF exhausted"
                )
            self._next += 1
        v = self._next
        self._used.add(v)
        self._next += 1
        return v


def _camel_to_snake(name: str) -> str:
    """``BirdBig`` → ``bird_big``."""
    s = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
    return s


def _discover_auto_refactors(project_root: str) -> List[Dict]:
    """Build a list of synthesised REFACTOR dicts covering every
    field-effect template that still has ``paletteTag = TAG_NONE`` and
    references a real image.  Skips templates whose ``.images`` is
    ``NULL`` (e.g. invisible affine-anim anchors like
    `ReflectionDistortion`) because they have no pixels to colour.

    Templates that already match an explicit REFACTORS entry are
    skipped too — those are handled by the hand-tuned path.
    """
    explicit_templates = set()
    for r in REFACTORS:
        explicit_templates.update(r.get("templates", []))

    templates = _parse_template_metadata(project_root)
    pic_to_png = _parse_pic_to_png_map(project_root)
    fldeffobj_to_template = _parse_fldeffobj_to_template(project_root)
    template_to_fldeffobj = {v: k for k, v in fldeffobj_to_template.items()}

    allocator = _FldeffTagAllocator()
    refactors: List[Dict] = []

    for meta in templates:
        if meta["palette_tag"] != "TAG_NONE":
            continue
        if not meta["images"] or meta["images"] == "NULL":
            continue
        if meta["template_symbol"] in explicit_templates:
            continue
        pic_symbol = meta["pic_symbol"]
        if not pic_symbol:
            continue
        source_png = pic_to_png.get(pic_symbol)
        if not source_png:
            continue
        png_abs = os.path.join(project_root, source_png)
        if not os.path.isfile(png_abs):
            continue

        name = meta["name"]
        snake = _camel_to_snake(name)
        try:
            tag_value = allocator.allocate()
        except RuntimeError:
            break  # exhausted namespace — stop discovering rather than fail apply

        refactor: Dict = {
            "name": name,
            "tag_const": f"FLDEFF_PAL_TAG_FLDEFF_{snake.upper()}",
            "tag_value": tag_value,
            "palette_symbol": f"gSpritePalette_FldEff_{name}",
            "data_symbol": f"gFieldEffectPal_FldEff_{name}",
            "gbapal_path": f"graphics/field_effects/palettes/fldeff_{snake}.gbapal",
            "source_png": source_png,
            "templates": [meta["template_symbol"]],
            "script_edits": [],
            "c_edits": [],
            "_auto": True,  # marker so reverse path knows this came from auto-discovery
        }

        # Decide where the palette load must be injected.  Two paths:
        #
        # 1. If a field-effect script `gFldEffScript_<Name>` exists,
        #    rewrite its `callnative FldEff_<Name>` to
        #    `loadfadedpal_callnative ...`.  This is the same pattern
        #    that Shadow / SurfBlob use.
        # 2. If no script exists, find every C call site that does
        #    `CreateSprite*(gFieldEffectObjectTemplatePointers[FLDEFFOBJ_<NAME>], ...)`
        #    and inject a `LoadSpritePalette(...);` line directly above it.
        #
        # Both paths can fire if a template is invoked both ways
        # (rare but possible).  The `_patch_c_edit` / `_patch_script_line`
        # helpers are idempotent so double-application is safe.
        # Look up the actual script label (handles `Placeholder` suffix
        # cases where template name != script name).
        script_label = _script_label_for(project_root, name)
        if script_label:
            # Don't assume `FldEff_<Name>` — vanilla pokefirered uses
            # arbitrary function names in script callnative directives
            # (`ShowTreeDisguiseFieldEffect` etc.).  Read the actual
            # function out of the existing script body and reuse it.
            func = _script_callnative_func(project_root, script_label)
            if func:
                refactor["script_edits"].append({
                    "label": script_label,
                    "vanilla": f"\tcallnative {func}",
                    "patched": (
                        f"\tloadfadedpal_callnative {refactor['palette_symbol']}, "
                        f"{func}"
                    ),
                })

        fldeffobj = template_to_fldeffobj.get(meta["template_symbol"])
        if fldeffobj:
            for rel_file, line in _find_c_create_sites(project_root, fldeffobj):
                # Preserve the original indentation in the injected
                # LoadSpritePalette line so the patched code stays
                # idiomatic when read by humans.
                indent_match = re.match(r"^([ \t]*)", line)
                indent = indent_match.group(1) if indent_match else ""

                load_block = (
                    f"{indent}extern const struct SpritePalette "
                    f"{refactor['palette_symbol']};\n"
                    f"{indent}LoadSpritePalette(&{refactor['palette_symbol']});\n"
                )

                # Look for a hardcoded `sprite->oam.paletteNum = 0;`
                # override in the next ~20 lines.  Vanilla
                # `FldEff_NpcFlyOut` and `CreateFlyBirdSprite` both
                # silently reset paletteNum to 0 right after their
                # CreateSprite call, which defeats the whole refactor —
                # the template-tag-resolved slot gets overwritten with
                # slot 0.  When found, bundle the strip into the SAME
                # c_edit as the load injection so both call sites
                # patch correctly even when their override lines are
                # byte-identical (a separate per-site c_edit would
                # short-circuit on `patched in text` after the first
                # site's strip lands).
                intervening, override_line = _find_palettenum_override_block(
                    project_root, rel_file, line,
                )
                if override_line:
                    override_indent = re.match(
                        r"^([ \t]*)", override_line
                    ).group(1)
                    override_body = override_line.lstrip()
                    commented = (
                        f"{override_indent}// {override_body}"
                        f"  // DOWP: stripped — template tag resolves slot"
                    )
                    # Single c_edit covering both the load injection and
                    # the override strip.  Vanilla = create line +
                    # intervening text + override; patched = load
                    # injection + create line + intervening text +
                    # commented override.  The intervening block is
                    # what makes this string unique per call site, so
                    # multiple sites in the same file all patch
                    # correctly.
                    refactor["c_edits"].append({
                        "file": rel_file,
                        "vanilla": line + intervening + override_line,
                        "patched": (
                            load_block + line + intervening + commented
                        ),
                    })
                else:
                    # No override to strip — just inject the load call
                    # ahead of the create line.
                    refactor["c_edits"].append({
                        "file": rel_file,
                        "vanilla": line,
                        "patched": load_block + line,
                    })

        refactors.append(refactor)

    return refactors


def _save_auto_state(project_root: str, auto_refactors: List[Dict]) -> None:
    """Persist the list of auto-discovered refactors so `remove()` can
    reverse them later.  Written under `porysuite/` so the user's
    `.gitignore` (which we configure to ignore that directory) keeps it
    out of commits.
    """
    state_path = os.path.join(project_root, _AUTO_STATE_REL)
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    import json
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(auto_refactors, f, indent=2)


def _load_auto_state(project_root: str) -> List[Dict]:
    """Read the previously-saved auto-discovered refactors.  Returns
    an empty list if no state file exists.
    """
    state_path = os.path.join(project_root, _AUTO_STATE_REL)
    if not os.path.isfile(state_path):
        return []
    import json
    try:
        with open(state_path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def _clear_auto_state(project_root: str) -> None:
    """Delete the saved auto-refactor state file (used during full DOWP
    disable so a future re-enable rediscovers from scratch).
    """
    state_path = os.path.join(project_root, _AUTO_STATE_REL)
    try:
        os.remove(state_path)
    except OSError:
        pass


def apply(project_root: str) -> Tuple[bool, List[str], List[str]]:
    """Apply every refactor in the REGISTRY to the project, PLUS any
    auto-discovered field-effect templates that still have
    ``paletteTag = TAG_NONE`` (see ``_discover_auto_refactors`` for the
    structural rationale).

    Returns ``(success, applied_messages, failed_messages)``.
    """
    applied: List[str] = []
    failed: List[str] = []

    # Auto-discovery pass.  Synthesise a refactor entry for every
    # un-tagged field-effect template the user's project contains, then
    # merge with the explicit list so the apply loop processes both the
    # same way.  Persist the discovered set so `remove()` can reverse
    # them without re-scanning (which wouldn't work — TAG_NONE will be
    # gone after apply).
    try:
        auto_refactors = _discover_auto_refactors(project_root)
        if auto_refactors:
            _save_auto_state(project_root, auto_refactors)
            applied.append(
                f"Auto-discovered {len(auto_refactors)} field-effect "
                f"template(s) needing palette refactor: "
                + ", ".join(r["name"] for r in auto_refactors)
            )
    except Exception as exc:
        failed.append(f"auto-discovery failed: {type(exc).__name__}: {exc}")
        auto_refactors = []

    for r in REFACTORS + auto_refactors:
        name = r["name"]

        # 1. Palette data file.
        if not _ensure_palette_data(project_root, r):
            failed.append(f"{name}: could not generate .gbapal from source PNG")
            continue
        applied.append(f"{name}: .gbapal palette data ready")

        # 2. Tag constant.
        _, status = _add_tag_constant(project_root, r["tag_const"], r["tag_value"])
        applied.append(f"{name}: {r['tag_const']} {status}")

        # 3. INCBIN declaration.
        _, status = _add_incbin_declaration(project_root, r["data_symbol"], r["gbapal_path"])
        if status.startswith("not found") or status == "could not remove":
            failed.append(f"{name}: INCBIN declaration {status}")
        else:
            applied.append(f"{name}: INCBIN declaration {status}")

        # 4. SpritePalette declaration.
        _, status = _add_sprite_palette_declaration(
            project_root, r["palette_symbol"], r["data_symbol"], r["tag_const"]
        )
        if status.startswith("not found"):
            failed.append(f"{name}: SpritePalette declaration {status}")
        else:
            applied.append(f"{name}: SpritePalette declaration {status}")

        # 5. Sprite template paletteTag rewrites.
        for tmpl in r["templates"]:
            ok, status = _patch_template_palette_tag(project_root, tmpl, r["tag_const"])
            if "not found" in status or "could not" in status:
                failed.append(f"{name}: template {tmpl} {status}")
            else:
                applied.append(f"{name}: template {tmpl} {status}")

        # 6. Field effect script edits (if any).
        for edit in r.get("script_edits", []):
            ok, status = _patch_script_line(
                project_root, edit["label"], edit["vanilla"], edit["patched"]
            )
            if "not found" in status or "could not" in status:
                failed.append(f"{name}: script {edit['label']} {status}")
            else:
                applied.append(f"{name}: script {edit['label']} {status}")

        # 7. Free-form C source edits (if any) — for cases where the
        # template's tag isn't enough because the C function hardcodes
        # paletteNum or otherwise sidesteps the template-driven path.
        for edit in r.get("c_edits", []):
            ok, status = _patch_c_edit(
                project_root, edit["file"], edit["vanilla"], edit["patched"]
            )
            if "not found" in status or "could not" in status:
                failed.append(f"{name}: c_edit {edit['file']} {status}")
            else:
                applied.append(f"{name}: c_edit {edit['file']} {status}")

    return len(failed) == 0, applied, failed


def remove(project_root: str) -> Tuple[bool, List[str], List[str]]:
    """Reverse every refactor in the REGISTRY plus every auto-discovered
    refactor recorded during the last `apply()`.

    Note: the .gbapal binary files are NOT deleted on remove. They're
    kept on disk so the user can re-enable DOWP without losing their
    palette edits. If the user really wants them gone they can delete
    via their OS file manager.
    """
    reverted: List[str] = []
    failed: List[str] = []

    # Load the saved auto-discovered list so we reverse exactly what
    # apply() wrote.  Re-discovery wouldn't work at this point — the
    # paletteTags we set during apply have replaced the TAG_NONE markers
    # the discovery function looks for.
    auto_refactors = _load_auto_state(project_root)
    if auto_refactors:
        reverted.append(
            f"Reversing {len(auto_refactors)} auto-discovered field-effect "
            f"refactor(s): " + ", ".join(r["name"] for r in auto_refactors)
        )

    for r in REFACTORS + auto_refactors:
        name = r["name"]

        # Reverse order: c_edits → script edits → templates → palette → INCBIN → tag.
        for edit in r.get("c_edits", []):
            ok, status = _restore_c_edit(
                project_root, edit["file"], edit["vanilla"], edit["patched"]
            )
            if "not found" in status or "could not" in status:
                failed.append(f"{name}: c_edit {edit['file']} {status}")
            else:
                reverted.append(f"{name}: c_edit {edit['file']} {status}")

        for edit in r.get("script_edits", []):
            ok, status = _restore_script_line(
                project_root, edit["label"], edit["vanilla"], edit["patched"]
            )
            if "not found" in status or "could not" in status:
                failed.append(f"{name}: script {edit['label']} {status}")
            else:
                reverted.append(f"{name}: script {edit['label']} {status}")

        for tmpl in r["templates"]:
            ok, status = _restore_template_palette_tag(
                project_root, tmpl, r["tag_const"]
            )
            if "not found" in status or "could not" in status:
                failed.append(f"{name}: template {tmpl} {status}")
            else:
                reverted.append(f"{name}: template {tmpl} {status}")

        _, status = _remove_sprite_palette_declaration(project_root, r["palette_symbol"])
        if status.startswith("not found"):
            failed.append(f"{name}: SpritePalette declaration {status}")
        else:
            reverted.append(f"{name}: SpritePalette declaration {status}")

        _, status = _remove_incbin_declaration(project_root, r["data_symbol"])
        if status.startswith("not found"):
            failed.append(f"{name}: INCBIN declaration {status}")
        else:
            reverted.append(f"{name}: INCBIN declaration {status}")

        _, status = _remove_tag_constant(project_root, r["tag_const"])
        if status.startswith("not found"):
            failed.append(f"{name}: {r['tag_const']} {status}")
        else:
            reverted.append(f"{name}: {r['tag_const']} {status}")

    # Clear the saved auto-state — a future DOWP enable will rediscover
    # whatever templates are still in TAG_NONE state at that point.
    if auto_refactors:
        _clear_auto_state(project_root)

    return len(failed) == 0, reverted, failed


# ── Lookup helpers for the PorySuite Field Effect Sprites tab ────────────


def find_refactor_for_png(png_path: str) -> Optional[Dict]:
    """Given a relative-to-project PNG path (e.g.
    ``graphics/field_effects/pics/shadow_medium.png``), return the
    REGISTRY entry whose source PNG matches, or whose templates
    reference a PNG with the same basename. Used by the PorySuite UI
    to find out where to write palette edits.

    Returns ``None`` if no refactor owns this PNG.
    """
    norm = png_path.replace("\\", "/").lower()
    basename = os.path.basename(norm)
    for r in REFACTORS:
        if r["source_png"].replace("\\", "/").lower() == norm:
            return r
        # For shadow specifically, ANY shadow size PNG falls under the
        # shared "Shadow" refactor since all 4 templates use the same
        # palette.
        if r["name"] == "Shadow" and basename.startswith("shadow_") and basename.endswith(".png"):
            return r
    return None


def gbapal_path_for_refactor(project_root: str, refactor: Dict) -> str:
    """Absolute path to the .gbapal binary for a registry entry."""
    return os.path.join(project_root, refactor["gbapal_path"])
