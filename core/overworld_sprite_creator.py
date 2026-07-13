"""Backend for adding new overworld sprites to a pokefirered project.

Handles all C header/source file modifications needed to register a new
overworld sprite: graphics data, pic table, graphics info, pointer array,
GFX constant, and palette registration.
"""

from __future__ import annotations

import os
import re
import shutil
from typing import Dict, List, Optional, Tuple

from core import overworld_subsprite_gen as subsprite_gen
from core.overworld_sprite_geometry import decompose, validate

Color = Tuple[int, int, int]

# Animation-table choices for the New Sprite dialog are no longer a
# hardcoded list — `core.anim_table_upgrade.scan_anim_tables` discovers
# every sAnimTable_* the project actually defines, with a frame-count
# hint per option.

# ── Standard NPC palette slots (non-DOWP) ──────────────────────────────────

NPC_PALETTE_SLOTS = [
    ("OBJ_EVENT_PAL_TAG_NPC_BLUE",   "PALSLOT_NPC_1", "NPC Blue"),
    ("OBJ_EVENT_PAL_TAG_NPC_PINK",   "PALSLOT_NPC_2", "NPC Pink"),
    ("OBJ_EVENT_PAL_TAG_NPC_GREEN",  "PALSLOT_NPC_3", "NPC Green"),
    ("OBJ_EVENT_PAL_TAG_NPC_WHITE",  "PALSLOT_NPC_4", "NPC White"),
]


def _read_file(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def _write_file(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _to_pascal(slug: str) -> str:
    """Convert 'my_sprite_name' → 'MySpriteName'."""
    return "".join(w.capitalize() for w in slug.split("_"))


def _to_upper(slug: str) -> str:
    """Convert 'my_sprite_name' → 'MY_SPRITE_NAME'."""
    return slug.upper()


def _ensure_spritesheet_rule(
    root: str,
    category: str,
    sprite_slug: str,
    tile_w: int,
    tile_h: int,
) -> bool:
    """Add a metatile rule to ``spritesheet_rules.mk`` for a new
    overworld sprite.

    ``tile_w``/``tile_h`` are the gbagfx ``-mwidth``/``-mheight`` values:
    the sprite's uniform hardware-piece size in tiles — equal to the
    frame size for a single-OAM sprite, the subsprite piece size for a
    composite.

    The default ``%.4bpp: %.png`` Makefile rule runs ``gbagfx`` with no
    metatile flags, so it lays out the resulting .4bpp tiles in
    *row-major* order across the entire image.  The engine's
    ``overworld_frame(ptr, w, h, frame)`` macro expects each frame's
    tiles to be *contiguous* in the .4bpp.  Without the explicit
    ``-mwidth N -mheight N`` flags, frame 0 actually loads the top
    halves of frames 0 AND 1 stitched horizontally — manifesting
    in-game as "two heads stacked" for small sprites.

    Idempotent: returns ``False`` if a rule already exists for this
    PNG.  Returns ``True`` if a new rule was appended.
    """
    rules_path = os.path.join(root, "spritesheet_rules.mk")
    rel_4bpp = (
        f"$(OBJEVENTGFXDIR)/{category}/{sprite_slug}.4bpp"
    )
    text = _read_file(rules_path)
    # Match any line that starts with this exact .4bpp target — both the
    # `target: pattern: pattern` form used by every vanilla rule, and
    # bare ``target:`` lines if someone hand-added one differently.
    pat = re.compile(
        r"^" + re.escape(rel_4bpp) + r"\s*:",
        re.MULTILINE,
    )
    if pat.search(text):
        return False  # rule already present, no-op

    block = (
        f"\n{rel_4bpp}: %.4bpp: %.png\n"
        f"\t$(GFX) $< $@ -mwidth {tile_w} -mheight {tile_h}\n"
    )
    # Append at end of file.  Order doesn't matter to Make; the
    # vanilla file is alphabetised within each category but Make
    # picks the most-specific rule regardless of position.
    if not text.endswith("\n"):
        text += "\n"
    text += block
    _write_file(rules_path, text)
    return True


def _update_spritesheet_rule(
    root: str,
    category: str,
    sprite_slug: str,
    tile_w: int,
    tile_h: int,
) -> Tuple[bool, str]:
    """Update an existing per-sprite ``spritesheet_rules.mk`` rule's
    ``-mwidth`` / ``-mheight`` flags in place.

    Why this exists separately from ``_ensure_spritesheet_rule``:
        ``_ensure_spritesheet_rule`` is a NO-OP when a rule already
        exists — it never touches a rule's flags.  That's correct for
        new-sprite creation, but ``replace_sprite_sheet`` needs to
        rewrite the existing rule's metatile flags when the frame size
        changes, because the flags determine how ``gbagfx`` packs tiles
        into the .4bpp.  A stale flag set produces a malformed .4bpp
        that the engine renders as garbage no matter how clean the
        GraphicsInfo / pic table are.

    Behaviour:
      - Rule exists → rewrite both ``-mwidth`` and ``-mheight`` to the
        passed values.  Idempotent: a re-run with the same values is a
        clean no-op (returns ``(False, "")``).
      - Rule missing → calls ``_ensure_spritesheet_rule`` so a sprite
        without an explicit rule gets one created with the correct
        flags (rather than silently falling back to the default rule
        that emits row-major tiles).

    Returns ``(changed, detail)``.  ``detail`` is a human-readable
    summary of what changed, suitable for the applied-messages list.
    """
    rules_path = os.path.join(root, "spritesheet_rules.mk")
    rel_4bpp = f"$(OBJEVENTGFXDIR)/{category}/{sprite_slug}.4bpp"
    if not os.path.isfile(rules_path):
        return False, f"missing {rules_path}"

    text = _read_file(rules_path)

    # Two-line rule: header (`$(...)/X.4bpp: %.4bpp: %.png`) followed
    # by an indented `$(GFX) $< $@ -mwidth N -mheight M` recipe.  Capture
    # the full block so we can rewrite the flags without touching the
    # rule's targets.
    pat = re.compile(
        r"(^" + re.escape(rel_4bpp) + r"\s*:[^\n]*\n)"
        r"(\t\$\(GFX\)\s+\$<\s+\$@\s+-mwidth\s+)(\d+)"
        r"(\s+-mheight\s+)(\d+)(\s*\n)",
        re.MULTILINE,
    )
    m = pat.search(text)
    if not m:
        # No existing rule → add a fresh one with the right flags.
        added = _ensure_spritesheet_rule(
            root, category, sprite_slug, tile_w, tile_h)
        if added:
            return True, (
                f"spritesheet_rules.mk: added rule for "
                f"{category}/{sprite_slug}.4bpp "
                f"(-mwidth {tile_w} -mheight {tile_h})"
            )
        return False, ""

    old_w, old_h = int(m.group(3)), int(m.group(5))
    if old_w == tile_w and old_h == tile_h:
        return False, ""  # already correct, no-op

    new_recipe = (
        m.group(1) + m.group(2) + str(tile_w)
        + m.group(4) + str(tile_h) + m.group(6)
    )
    text = text[:m.start()] + new_recipe + text[m.end():]
    _write_file(rules_path, text)
    return True, (
        f"spritesheet_rules.mk: updated {category}/{sprite_slug}.4bpp "
        f"(-mwidth {old_w} -mheight {old_h}) → "
        f"(-mwidth {tile_w} -mheight {tile_h})"
    )


def _remove_spritesheet_rule(
    root: str,
    rel_4bpp_path: str,
) -> bool:
    """Remove the per-sprite rule block from ``spritesheet_rules.mk``.

    ``rel_4bpp_path`` is the path as it appears in the rules file
    (e.g. ``$(OBJEVENTGFXDIR)/people/gravekid.4bpp``).  Returns
    ``True`` if a rule was found and removed.
    """
    rules_path = os.path.join(root, "spritesheet_rules.mk")
    if not os.path.isfile(rules_path):
        return False
    text = _read_file(rules_path)
    # Match the 2-line rule block plus surrounding blank lines.
    pat = re.compile(
        r"\n?"
        + re.escape(rel_4bpp_path)
        + r"\s*:[^\n]*\n\t[^\n]*\n",
        re.MULTILINE,
    )
    m = pat.search(text)
    if not m:
        return False
    text = text[:m.start()] + text[m.end():]
    _write_file(rules_path, text)
    return True


def get_next_gfx_id(root: str) -> Tuple[int, str]:
    """Read the current NUM_OBJ_EVENT_GFX value and return (next_id, file_path)."""
    path = os.path.join(root, "include", "constants", "event_objects.h")
    text = _read_file(path)
    m = re.search(r"#define\s+NUM_OBJ_EVENT_GFX\s+(\d+)", text)
    if not m:
        raise ValueError("Could not find NUM_OBJ_EVENT_GFX in event_objects.h")
    return int(m.group(1)), path


def get_next_palette_tag(root: str) -> int:
    """Find the highest OBJ_EVENT_PAL_TAG value and return next available."""
    path = os.path.join(root, "src", "event_object_movement.c")
    text = _read_file(path)
    tags = re.findall(r"#define\s+OBJ_EVENT_PAL_TAG_\w+\s+(0x[0-9a-fA-F]+|\d+)", text)
    if not tags:
        return 0x1120  # Safe default above standard tags
    max_val = max(int(t, 0) for t in tags)
    return max_val + 1


def create_overworld_sprite(
    root: str,
    png_source: str,
    sprite_slug: str,
    frame_w: int,
    frame_h: int,
    anim_table: str,
    category: str,
    palette_tag: Optional[str] = None,
    palette_slot: Optional[str] = None,
    create_new_palette: bool = False,
    palette_colors: Optional[List[Color]] = None,
) -> Tuple[bool, List[str], List[str]]:
    """Add a new overworld sprite to the project.

    Parameters:
        root:              project root path
        png_source:        path to the source PNG file
        sprite_slug:       lowercase underscore name (e.g. 'my_npc')
        frame_w, frame_h:  frame dimensions in pixels
        anim_table:        animation table name (e.g. 'sAnimTable_Standard')
        category:          'people', 'pokemon', or 'misc'
        palette_tag:       existing palette tag to use (e.g. 'OBJ_EVENT_PAL_TAG_NPC_BLUE')
                           if None and create_new_palette=True, creates a new one
        palette_slot:      palette slot (e.g. 'PALSLOT_NPC_1'), used when not creating new
        create_new_palette: if True, create a new palette tag + .gbapal file
        palette_colors:    16 RGB tuples for the new palette (required if create_new_palette)

    Returns:
        (success, applied_list, error_list)
    """
    applied: List[str] = []
    errors: List[str] = []

    pascal = _to_pascal(sprite_slug)
    upper = _to_upper(sprite_slug)
    gfx_const = f"OBJ_EVENT_GFX_{upper}"

    tile_w = frame_w // 8
    tile_h = frame_h // 8
    sprite_size = (frame_w * frame_h) // 2  # 4bpp

    # ── Geometry: validate the size, then decompose it ──────────────────
    # Replaces the old 5-entry hardcoded _OAM_TABLE.  Any frame whose
    # width and height are both multiples of 8 is handled — a single GBA
    # hardware sprite or a composite — and the exact OAM template and
    # subsprite table it needs are computed here, then generated below
    # if the project doesn't already define them.
    ok, reasons = validate(frame_w, frame_h)
    if not ok:
        errors.append("Invalid frame size: " + "; ".join(reasons))
        return False, applied, errors
    geo = decompose(frame_w, frame_h)
    oam_name = geo.oam_symbol
    # The decomposition picks the subsprite table symbol: a reusable
    # vanilla single-OAM table keeps its name, everything else (every
    # composite) gets a generated ``Ps``-named table — so a composite
    # never binds to a vanilla WxH table with an incompatible layout.
    sub_name = geo.subsprite_symbol

    # Determine frame count from the imported (horizontal) PNG strip.
    try:
        from PyQt6.QtGui import QImage
        img = QImage(png_source)
        if img.isNull():
            errors.append(f"Could not load PNG: {png_source}")
            return False, applied, errors
        num_frames = img.width() // frame_w if frame_w > 0 else 1
    except Exception as e:
        errors.append(f"Failed to read PNG: {e}")
        return False, applied, errors

    # ── Step 1: Bring the PNG into the project ──────────────────────────
    # A single-OAM sprite keeps the imported horizontal frame strip.  A
    # multi-frame composite is re-laid-out as a VERTICAL strip: gbagfx
    # emits each metatile (one hardware piece) row-major DOWN the image,
    # so frames must stack vertically for every frame's tiles to land in
    # the one contiguous run the engine's ``overworld_frame`` macro reads.
    dest_dir = os.path.join(root, "graphics", "object_events", "pics", category)
    os.makedirs(dest_dir, exist_ok=True)
    dest_png = os.path.join(dest_dir, f"{sprite_slug}.png")
    try:
        if num_frames > 1 and not geo.is_single_oam:
            _write_vertical_frame_strip(
                png_source, dest_png, frame_w, frame_h, num_frames,
            )
            applied.append(
                f"Imported PNG as a vertical {num_frames}-frame strip to "
                f"graphics/object_events/pics/{category}/{sprite_slug}.png "
                f"(composite sprites need a vertical tile layout)"
            )
        else:
            shutil.copy2(png_source, dest_png)
            applied.append(
                f"Copied PNG to graphics/object_events/pics/"
                f"{category}/{sprite_slug}.png"
            )
    except Exception as e:
        errors.append(f"Copy PNG: {e}")
        return False, applied, errors

    # ── Step 2: Handle palette ──────────────────────────────────────────
    new_pal_tag = None
    new_pal_slot = None

    if create_new_palette and palette_colors:
        # Create new palette tag and .gbapal file
        next_tag_val = get_next_palette_tag(root)
        new_pal_tag = f"OBJ_EVENT_PAL_TAG_{upper}"
        new_pal_slot = "PALSLOT_NPC_1"  # DOWP ignores slot at runtime

        # Write .gbapal file (raw 16-color GBA palette, 32 bytes)
        pal_dir = os.path.join(root, "graphics", "object_events", "palettes")
        os.makedirs(pal_dir, exist_ok=True)
        gbapal_path = os.path.join(pal_dir, f"{sprite_slug}.gbapal")
        try:
            with open(gbapal_path, "wb") as f:
                for r, g, b in palette_colors[:16]:
                    # GBA 15-bit: 0bBBBBBGGGGGRRRRR
                    r5 = min(r >> 3, 31)
                    g5 = min(g >> 3, 31)
                    b5 = min(b >> 3, 31)
                    val = r5 | (g5 << 5) | (b5 << 10)
                    f.write(val.to_bytes(2, "little"))
                # Pad to 16 colors if needed
                for _ in range(16 - min(len(palette_colors), 16)):
                    f.write(b"\x00\x00")
            applied.append(f"Created palette: graphics/object_events/palettes/{sprite_slug}.gbapal")
        except Exception as e:
            errors.append(f"Write .gbapal: {e}")
            return False, applied, errors

        # Add palette tag define to event_object_movement.c
        eom_path = os.path.join(root, "src", "event_object_movement.c")
        try:
            text = _read_file(eom_path)
            # Add define after the last OBJ_EVENT_PAL_TAG_ define
            last_tag = list(re.finditer(
                r"#define\s+OBJ_EVENT_PAL_TAG_\w+\s+0x[0-9a-fA-F]+", text
            ))
            if last_tag:
                insert_pos = last_tag[-1].end()
                define_line = f"\n#define {new_pal_tag:<44s} 0x{next_tag_val:04X}"
                text = text[:insert_pos] + define_line + text[insert_pos:]
            else:
                errors.append("Could not find palette tag defines")
                return False, applied, errors

            # Add INCBIN for palette in object_event_graphics.h
            gfx_h_path = os.path.join(
                root, "src", "data", "object_events", "object_event_graphics.h"
            )
            gfx_text = _read_file(gfx_h_path)
            # Find last gObjectEventPal_ line
            last_pal = list(re.finditer(
                r"const u16 gObjectEventPal_\w+\[\].*?;", gfx_text
            ))
            if last_pal:
                insert_pos_gfx = last_pal[-1].end()
                pal_incbin = (
                    f"\nconst u16 gObjectEventPal_{pascal}[] = "
                    f'INCBIN_U16("graphics/object_events/palettes/{sprite_slug}.gbapal");'
                )
                gfx_text = gfx_text[:insert_pos_gfx] + pal_incbin + gfx_text[insert_pos_gfx:]
                _write_file(gfx_h_path, gfx_text)
                applied.append(f"Added palette INCBIN to object_event_graphics.h")

            # Add entry to sObjectEventSpritePalettes array
            # Find the empty terminator {}
            empty_term = text.rfind("    {},")
            if empty_term < 0:
                empty_term = text.rfind("{},")
            if empty_term >= 0:
                pal_entry = (
                    f"    {{gObjectEventPal_{pascal},"
                    f"{' ' * max(1, 32 - len(f'gObjectEventPal_{pascal}'))}"
                    f"{new_pal_tag}}},\n"
                )
                text = text[:empty_term] + pal_entry + text[empty_term:]
                _write_file(eom_path, text)
                applied.append(f"Added palette entry to sObjectEventSpritePalettes")
            else:
                errors.append("Could not find palette array terminator")

            palette_tag = new_pal_tag
            palette_slot = new_pal_slot
        except Exception as e:
            errors.append(f"Palette registration: {e}")

    elif not palette_tag:
        errors.append("No palette tag specified")
        return False, applied, errors

    if not palette_slot:
        # Look up palette slot from tag
        for tag, slot, _name in NPC_PALETTE_SLOTS:
            if tag == palette_tag:
                palette_slot = slot
                break
        if not palette_slot:
            palette_slot = "PALSLOT_NPC_1"

    # ── Step 3: Add pic INCBIN to object_event_graphics.h ───────────────
    gfx_h_path = os.path.join(
        root, "src", "data", "object_events", "object_event_graphics.h"
    )
    try:
        gfx_text = _read_file(gfx_h_path)
        last_pic = list(re.finditer(
            r"const u16 gObjectEventPic_\w+\[\].*?;", gfx_text
        ))
        if last_pic:
            insert_pos = last_pic[-1].end()
            pic_incbin = (
                f"\nconst u16 gObjectEventPic_{pascal}[] = "
                f'INCBIN_U16("graphics/object_events/pics/{category}/{sprite_slug}.4bpp");'
            )
            gfx_text = gfx_text[:insert_pos] + pic_incbin + gfx_text[insert_pos:]
            _write_file(gfx_h_path, gfx_text)
            applied.append("Added pic INCBIN to object_event_graphics.h")
        else:
            errors.append("Could not find pic INCBINs in object_event_graphics.h")
    except Exception as e:
        errors.append(f"Pic INCBIN: {e}")

    # ── Step 4: Add pic table to object_event_pic_tables.h ──────────────
    pic_tables_path = os.path.join(
        root, "src", "data", "object_events", "object_event_pic_tables.h"
    )
    try:
        pt_text = _read_file(pic_tables_path)
        # Build pic table entries
        lines = [f"static const struct SpriteFrameImage sPicTable_{pascal}[] = {{"]
        for i in range(num_frames):
            lines.append(
                f"    overworld_frame(gObjectEventPic_{pascal}, {tile_w}, {tile_h}, {i}),"
            )
        lines.append("};")
        pic_table_block = "\n".join(lines) + "\n"

        # Append before the end of file
        # Find the last pic table closing };
        last_close = pt_text.rfind("};")
        if last_close >= 0:
            insert_pos = last_close + 2
            pt_text = pt_text[:insert_pos] + "\n\n" + pic_table_block + pt_text[insert_pos:]
            _write_file(pic_tables_path, pt_text)
            applied.append(f"Added sPicTable_{pascal} to object_event_pic_tables.h")
        else:
            errors.append("Could not find insertion point in pic_tables.h")
    except Exception as e:
        errors.append(f"Pic table: {e}")

    # ── Step 4b: Generate the OAM template + subsprite table ────────────
    # The geometry decomposition above named the OAM template and
    # subsprite table this size needs.  The generator writes any the
    # project doesn't already define (vanilla or previously generated).
    # Idempotent — a clean no-op for the standard NPC sizes vanilla
    # already ships tables for.
    try:
        for res in subsprite_gen.ensure_overworld_geometry(root, geo):
            if res.changed:
                applied.append(res.detail)
    except Exception as e:
        errors.append(f"OAM/subsprite generation: {e}")

    # ── Step 4c: Ensure the composite-sprite depth-sort engine fix ──────
    # pokefirered's SortSprites breaks subpriority ties using the sprite's
    # top corner, so a tall composite sprite loses every tie and draws
    # behind the player.  This one-time, idempotent engine patch rewrites
    # the tie-break to compare feet.  Applied on the first New Sprite and
    # a no-op thereafter.
    try:
        from core import sprite_depth_patch
        dres = sprite_depth_patch.ensure_sprite_depth_fix(root)
        if dres.changed:
            applied.append(dres.detail)
        elif not dres.ok:
            # Best-effort: a missing or hand-modified sprite.c must NOT
            # fail sprite creation — the sprite is still valid, it just
            # won't depth-sort until the engine is patchable.  Surface
            # it as a note, never as a creation error.
            applied.append(f"Note — composite depth fix skipped: {dres.detail}")
    except Exception as e:
        applied.append(f"Note — composite depth fix skipped: {e}")

    # ── Step 5: Add GraphicsInfo to object_event_graphics_info.h ────────
    gi_path = os.path.join(
        root, "src", "data", "object_events", "object_event_graphics_info.h"
    )
    try:
        gi_text = _read_file(gi_path)
        is_inanimate = "TRUE" if anim_table == "sAnimTable_Inanimate" else "FALSE"

        gi_block = f"""
const struct ObjectEventGraphicsInfo gObjectEventGraphicsInfo_{pascal} = {{
    .tileTag = TAG_NONE,
    .paletteTag = {palette_tag},
    .reflectionPaletteTag = OBJ_EVENT_PAL_TAG_BRIDGE_REFLECTION,
    .size = {sprite_size},
    .width = {frame_w},
    .height = {frame_h},
    .paletteSlot = {palette_slot},
    .shadowSize = SHADOW_SIZE_M,
    .inanimate = {is_inanimate},
    .disableReflectionPaletteLoad = FALSE,
    .tracks = TRACKS_FOOT,
    .oam = &{oam_name},
    .subspriteTables = {sub_name},
    .anims = {anim_table},
    .images = sPicTable_{pascal},
    .affineAnims = gDummySpriteAffineAnimTable,
}};
"""
        # Append at end of file
        gi_text = gi_text.rstrip() + "\n" + gi_block
        _write_file(gi_path, gi_text)
        applied.append(f"Added gObjectEventGraphicsInfo_{pascal}")
    except Exception as e:
        errors.append(f"GraphicsInfo: {e}")

    # ── Step 6: Add forward declaration + pointer ───────────────────────
    ptrs_path = os.path.join(
        root, "src", "data", "object_events",
        "object_event_graphics_info_pointers.h"
    )
    try:
        ptrs_text = _read_file(ptrs_path)

        # Add forward declaration at the top (after last existing one)
        last_fwd = list(re.finditer(
            r"const struct ObjectEventGraphicsInfo gObjectEventGraphicsInfo_\w+;",
            ptrs_text
        ))
        if last_fwd:
            fwd_pos = last_fwd[-1].end()
            fwd_decl = (
                f"\nconst struct ObjectEventGraphicsInfo "
                f"gObjectEventGraphicsInfo_{pascal};"
            )
            ptrs_text = ptrs_text[:fwd_pos] + fwd_decl + ptrs_text[fwd_pos:]

        # Add pointer entry before the closing };
        closing = ptrs_text.rfind("};")
        if closing >= 0:
            ptr_entry = (
                f"    [{gfx_const}]"
                f"{' ' * max(1, 37 - len(gfx_const))}"
                f"= &gObjectEventGraphicsInfo_{pascal},\n"
            )
            ptrs_text = ptrs_text[:closing] + ptr_entry + ptrs_text[closing:]
            _write_file(ptrs_path, ptrs_text)
            applied.append(f"Added pointer entry for {gfx_const}")
        else:
            errors.append("Could not find closing }; in pointers.h")
    except Exception as e:
        errors.append(f"Pointer entry: {e}")

    # ── Step 7a: Ensure spritesheet_rules.mk has a per-frame metatile
    #            rule for this PNG.  Without `-mwidth N -mheight N` the
    #            default `%.4bpp: %.png` rule emits row-major tile order,
    #            but the engine's `overworld_frame` macro expects per-frame
    #            contiguous tiles.  Skipping this rule produces a sprite
    #            where each in-game "frame" actually shows the top halves
    #            of two adjacent source frames stitched together — i.e.
    #            two heads stacked vertically.
    try:
        added = _ensure_spritesheet_rule(
            root, category, sprite_slug,
            geo.metatile_w, geo.metatile_h,
        )
        if added:
            applied.append(
                f"Added spritesheet_rules.mk rule "
                f"(-mwidth {geo.metatile_w} -mheight {geo.metatile_h})"
            )
    except Exception as e:
        errors.append(f"spritesheet rule: {e}")

    # ── Step 7: Add OBJ_EVENT_GFX_ define and bump NUM ──────────────────
    try:
        next_id, eo_path = get_next_gfx_id(root)
        eo_text = _read_file(eo_path)

        # Replace NUM_OBJ_EVENT_GFX line
        old_num = f"#define NUM_OBJ_EVENT_GFX     {next_id}"
        # Be flexible with whitespace
        num_pat = re.compile(r"#define\s+NUM_OBJ_EVENT_GFX\s+\d+")
        m = num_pat.search(eo_text)
        if m:
            # Insert new define before NUM line
            new_define = (
                f"#define {gfx_const:<38s} {next_id}\n\n"
                f"#define NUM_OBJ_EVENT_GFX     {next_id + 1}"
            )
            eo_text = eo_text[:m.start()] + new_define + eo_text[m.end():]
            _write_file(eo_path, eo_text)
            applied.append(f"Added {gfx_const} = {next_id}, NUM = {next_id + 1}")
        else:
            errors.append("Could not find NUM_OBJ_EVENT_GFX")
    except Exception as e:
        errors.append(f"GFX constant: {e}")

    success = len(errors) == 0
    return success, applied, errors


# ════════════════════════════════════════════════════════════════════════════
# Replace an overworld sprite's sheet (the dynamic Import Manually path)
# ════════════════════════════════════════════════════════════════════════════

def replace_sprite_sheet(
    root: str,
    info_name: str,
    new_png_path: str,
    new_frame_w: int,
    new_frame_h: int,
    *,
    palette_colors: Optional[List[Color]] = None,
    remapped_img=None,
) -> Tuple[bool, List[str], List[str]]:
    """Replace an existing sprite's sheet AND keep all metadata in sync.

    Why this exists
    ===============

    The Overworld Graphics tab's "Import Manually…" button replaces a
    sprite's PNG on disk with a user-picked source, optionally remapped to
    a new palette.  Before this function existed, NOTHING ELSE was kept
    in sync — GraphicsInfo `.width`/`.height`/`.size`/`.oam`/
    `.subspriteTables` and the `sPicTable_<X>[]` entry count all stayed
    pinned to the OLD sprite's geometry.

    So importing a new ``128×32`` sheet at the user's intended 16×32
    frame size over a 16×16 vanilla Jigglypuff entry made the engine
    slice the new PNG as 16 frames of 16×16 (top row of heads + bottom
    row of feet/bodies), wired a stale 16-entry pic table to those bogus
    indices, and triggered the emote-upgrade scan to "fix" a 10th-frame
    that didn't exist in the user's layout.  Symptoms: preview shows
    only feet, Frame Cycle reports 16 frames, the emote upgrade
    overrides the user's chosen anim table.

    This function repairs that by updating EVERY in-source piece of
    metadata together with the PNG swap:

      - Saves the new PNG (palette-remapped if ``remapped_img`` is
        passed; raw-copied otherwise) to the existing sprite's PNG path.
      - Updates ``gObjectEventGraphicsInfo_<info_name>``:
        ``.width``, ``.height``, ``.size``, ``.oam``, ``.subspriteTables``
        to match the new frame size.  ``.paletteTag`` is untouched
        (palette changes go through the separate Import-Palette flow).
        ``.anims`` is untouched (let the user pick separately so the
        existing Generic Loop / Cycle / custom choice isn't clobbered).
      - Rebuilds ``sPicTable_<info_name>[]`` to exactly
        ``new_frame_count`` entries (= ``new_png_width // new_frame_w``)
        with the correct ``tile_w`` / ``tile_h``.
      - Ensures any OAM template or subsprite table the new geometry
        needs is present (via ``ensure_overworld_geometry``).

    What it deliberately does NOT do:
      - Touch `.anims`.  Users pick their anim table explicitly; an
        anim that references frames the new sheet doesn't have will look
        wrong in-game, but that's a user-visible mistake to fix in the
        Animation dropdown — not something we silently overwrite.
      - Touch the palette.  Palette imports route through the existing
        Import Palette flow; this function only consumes a passed-in
        ``palette_colors`` + ``remapped_img`` to bake the right colours
        into the saved PNG.
      - Decrement / renumber any constants.  This is an in-place edit,
        not a delete-and-recreate.

    Returns ``(success, applied, errors)``.
    """
    applied: List[str] = []
    errors: List[str] = []

    # ── Geometry validation ──────────────────────────────────────────
    ok, reasons = validate(new_frame_w, new_frame_h)
    if not ok:
        errors.append(
            f"Invalid frame size {new_frame_w}×{new_frame_h}: "
            + "; ".join(reasons))
        return False, applied, errors

    # Read new PNG dimensions to derive frame count.
    try:
        from PyQt6.QtGui import QImage
        probe = QImage(new_png_path)
        if probe.isNull():
            errors.append(f"Could not load source PNG: {new_png_path}")
            return False, applied, errors
        png_w, png_h = probe.width(), probe.height()
    except Exception as e:
        errors.append(f"PNG probe failed: {e}")
        return False, applied, errors

    if png_w % new_frame_w != 0 or png_h % new_frame_h != 0:
        errors.append(
            f"PNG dimensions ({png_w}×{png_h}) don't cleanly divide "
            f"by frame size ({new_frame_w}×{new_frame_h}). "
            f"Pad or resize the source first."
        )
        return False, applied, errors
    cols = png_w // new_frame_w
    rows = png_h // new_frame_h
    num_frames = cols * rows
    if num_frames < 1:
        errors.append("New sheet resolves to 0 frames.")
        return False, applied, errors

    geo = decompose(new_frame_w, new_frame_h)
    sprite_size = (new_frame_w * new_frame_h) // 2  # 4bpp bytes/frame
    tile_w = new_frame_w // 8
    tile_h = new_frame_h // 8

    # ── Locate the GraphicsInfo block ────────────────────────────────
    gi_path = os.path.join(
        root, "src", "data", "object_events", "object_event_graphics_info.h",
    )
    if not os.path.isfile(gi_path):
        errors.append(f"missing {gi_path}")
        return False, applied, errors
    gi_text = _read_file(gi_path)
    info_symbol = f"gObjectEventGraphicsInfo_{info_name}"
    block_match = re.search(
        r"(const\s+struct\s+ObjectEventGraphicsInfo\s+"
        + re.escape(info_symbol)
        + r"\s*=\s*\{)(?P<body>[^;]*?)(\};)",
        gi_text, flags=re.DOTALL,
    )
    if not block_match:
        errors.append(
            f"Could not find {info_symbol} in object_event_graphics_info.h"
        )
        return False, applied, errors
    body = block_match.group("body")

    # Pull the existing pic-table reference + PNG path before we change
    # any geometry — both are needed for the pic-table rebuild and the
    # PNG save below.
    pic_match = re.search(r"\.images\s*=\s*(sPicTable_\w+)", body)
    if not pic_match:
        errors.append(f"{info_symbol} has no .images field")
        return False, applied, errors
    pic_table_name = pic_match.group(1)

    # ── Ensure any new OAM template + subsprite table exist ──────────
    try:
        for res in subsprite_gen.ensure_overworld_geometry(root, geo):
            if res.changed:
                applied.append(res.detail)
    except Exception as e:
        errors.append(f"OAM/subsprite ensure: {e}")
        return False, applied, errors

    # ── Rebuild GraphicsInfo body in place ───────────────────────────
    # Each .X = Y line is rewritten exactly once.  Anything not listed
    # below (.paletteTag, .anims, .images, .tracks, .inanimate, …) is
    # left intact so user-set fields survive the resize.
    def _replace_field(text: str, field: str, value: str) -> str:
        pat = re.compile(
            r"(\." + re.escape(field) + r"\s*=\s*)([^,\n]+)(,)",
        )
        new_text, n = pat.subn(r"\g<1>" + value + r"\3", text, count=1)
        if n == 0:
            # Field didn't exist; append it before the closing brace.
            return text.rstrip() + f"\n    .{field} = {value},\n"
        return new_text

    new_body = body
    new_body = _replace_field(new_body, "size", str(sprite_size))
    new_body = _replace_field(new_body, "width", str(new_frame_w))
    new_body = _replace_field(new_body, "height", str(new_frame_h))
    new_body = _replace_field(new_body, "oam", f"&{geo.oam_symbol}")
    new_body = _replace_field(
        new_body, "subspriteTables", geo.subsprite_symbol)

    gi_text = (gi_text[:block_match.start()]
               + block_match.group(1) + new_body + block_match.group(3)
               + gi_text[block_match.end():])
    try:
        _write_file(gi_path, gi_text)
        applied.append(
            f"Updated {info_symbol}: "
            f"size={sprite_size}, "
            f"width={new_frame_w}, height={new_frame_h}, "
            f"oam=&{geo.oam_symbol}, "
            f"subspriteTables={geo.subsprite_symbol}"
        )
    except Exception as e:
        errors.append(f"GraphicsInfo write: {e}")
        return False, applied, errors

    # ── Rebuild the pic table to exactly num_frames entries ──────────
    pt_path = os.path.join(
        root, "src", "data", "object_events", "object_event_pic_tables.h",
    )
    if not os.path.isfile(pt_path):
        errors.append(f"missing {pt_path}")
        return False, applied, errors
    pt_text = _read_file(pt_path)
    pt_block_pat = re.compile(
        r"(static\s+const\s+struct\s+SpriteFrameImage\s+"
        + re.escape(pic_table_name)
        + r"\s*\[\]\s*=\s*\{)"
        + r"(?P<body>[^}]*?)"
        + r"(\};)",
        re.DOTALL,
    )
    pt_match = pt_block_pat.search(pt_text)
    if not pt_match:
        errors.append(
            f"Could not find {pic_table_name} in object_event_pic_tables.h"
        )
        return False, applied, errors
    pic_symbol_match = re.search(
        r"overworld_frame\(\s*(gObjectEventPic_\w+)",
        pt_match.group("body"),
    )
    if not pic_symbol_match:
        errors.append(
            f"{pic_table_name} body has no overworld_frame entries "
            f"to learn the pic symbol from"
        )
        return False, applied, errors
    pic_symbol = pic_symbol_match.group(1)
    new_body_lines = [""]
    for i in range(num_frames):
        new_body_lines.append(
            f"    overworld_frame({pic_symbol}, {tile_w}, {tile_h}, {i}),"
        )
    new_body_lines.append("")
    new_pt = pt_match.group(1) + "\n".join(new_body_lines) + pt_match.group(3)
    pt_text = pt_text[:pt_match.start()] + new_pt + pt_text[pt_match.end():]
    try:
        _write_file(pt_path, pt_text)
        applied.append(
            f"Rebuilt {pic_table_name}: {num_frames} entries "
            f"({tile_w}×{tile_h} tiles per frame)"
        )
    except Exception as e:
        errors.append(f"pic table write: {e}")
        return False, applied, errors

    # ── Save the new PNG to the sprite's existing PNG path ──────────
    # The PNG path lives in the INCBIN line — we read it from
    # object_event_graphics.h via _scan_pic_png_path.  We delegate to
    # manual_palette_pick_dialog's save_remapped_image when a remapped
    # QImage + palette were passed (the manual-import case); fall back
    # to a raw shutil.copy2 for the auto case.
    #
    # Composite (multi-OAM) sprites need the PNG written as a VERTICAL
    # frame strip — gbagfx emits each metatile row-major DOWN the image,
    # so frames must stack vertically for every frame's tiles to land in
    # the one contiguous run the engine's overworld_frame macro reads.
    # Single-OAM sprites use the horizontal strip layout unchanged.  This
    # matches what create_overworld_sprite does for new sprites.
    dest_png = _scan_pic_png_path(root, pic_symbol)
    if not dest_png:
        errors.append(
            f"Could not resolve on-disk PNG path for {pic_symbol}"
        )
        return False, applied, errors

    needs_vertical_strip = (not geo.is_single_oam) and num_frames > 1

    try:
        if remapped_img is not None and palette_colors is not None:
            # Manual palette path — bake the remapped QImage to the
            # destination, then re-layout vertically if the geometry is
            # composite.
            from ui.dialogs.manual_palette_pick_dialog import (
                save_remapped_image,
            )
            if not save_remapped_image(
                    remapped_img, palette_colors, dest_png):
                errors.append(
                    f"Could not write remapped PNG to {dest_png}"
                )
                return False, applied, errors
            if needs_vertical_strip:
                # Re-read the just-written horizontal strip and rewrite
                # it as a vertical strip in place.  Two-step is safer
                # than trying to skip _write_vertical_frame_strip's
                # I/O — it's already battle-tested by the new-sprite path.
                tmp_path = dest_png + ".horiz.tmp.png"
                shutil.move(dest_png, tmp_path)
                try:
                    _write_vertical_frame_strip(
                        tmp_path, dest_png,
                        new_frame_w, new_frame_h, num_frames,
                    )
                finally:
                    if os.path.exists(tmp_path):
                        try:
                            os.remove(tmp_path)
                        except OSError:
                            pass
                applied.append(
                    f"Saved remapped sheet as a vertical {num_frames}-frame "
                    f"strip to {os.path.relpath(dest_png, root)}"
                )
            else:
                applied.append(
                    f"Saved remapped sheet to "
                    f"{os.path.relpath(dest_png, root)}"
                )
        else:
            if needs_vertical_strip:
                _write_vertical_frame_strip(
                    new_png_path, dest_png,
                    new_frame_w, new_frame_h, num_frames,
                )
                applied.append(
                    f"Saved sheet as a vertical {num_frames}-frame strip "
                    f"to {os.path.relpath(dest_png, root)}"
                )
            elif os.path.abspath(new_png_path) == os.path.abspath(dest_png):
                # Same file on both sides — happens when the silent sync
                # in Import Manually re-runs over the sprite's own PNG to
                # keep metadata in lockstep.  shutil.copy2 would raise
                # SameFileError on Windows here; instead bump the mtime
                # so the makefile build sees the PNG as "newer than the
                # .4bpp" and regenerates with whatever current
                # spritesheet_rules.mk metatile flags are in force.
                os.utime(dest_png, None)
                applied.append(
                    f"Touched {os.path.relpath(dest_png, root)} "
                    f"(same source — bumped mtime to trigger rebuild)"
                )
            else:
                shutil.copy2(new_png_path, dest_png)
                applied.append(
                    f"Copied sheet to {os.path.relpath(dest_png, root)}"
                )
    except Exception as e:
        errors.append(f"PNG save: {e}")
        return False, applied, errors

    # ── Update spritesheet_rules.mk to match the new tile geometry ──
    # gbagfx's -mwidth / -mheight flags are how each frame's tiles get
    # packed into the .4bpp.  Without this update the .4bpp stays
    # generated with the OLD metatile size — engine reads the wrong
    # tile run per frame and renders garbage no matter how clean the
    # GraphicsInfo and pic table are.
    #
    # geo.metatile_w / metatile_h is the uniform piece size in tiles —
    # equal to the frame size for single-OAM sprites, equal to the
    # sub-sprite piece size for composites.  This matches what the
    # matching new-sprite path uses (`_ensure_spritesheet_rule(...,
    # geo.metatile_w, geo.metatile_h)`).
    rule_tw, rule_th = geo.metatile_w, geo.metatile_h

    # Derive (category, slug) from the resolved PNG path.  The PNG sits
    # at `<root>/graphics/object_events/pics/<category>/<slug>.png`, so
    # the parent folder name is the category and the basename (sans
    # extension) is the slug.
    rel_png = os.path.relpath(dest_png, root).replace(os.sep, "/")
    cat_slug_m = re.search(
        r"graphics/object_events/pics/([^/]+)/([^/]+)\.png$",
        rel_png,
    )
    if cat_slug_m:
        category, slug = cat_slug_m.group(1), cat_slug_m.group(2)
        try:
            changed, detail = _update_spritesheet_rule(
                root, category, slug, rule_tw, rule_th,
            )
            if changed:
                applied.append(detail)
                # CRITICAL: make tracks file dependencies, not recipe
                # changes.  When the -mwidth/-mheight flags in
                # spritesheet_rules.mk change but the PNG mtime is older
                # than the existing .4bpp, make says "the .4bpp is up to
                # date" and skips the rebuild -- so the .4bpp stays
                # packed in the OLD metatile layout and the engine
                # renders garbage no matter how clean the GraphicsInfo
                # and pic table are.  Force a rebuild by bumping the
                # PNG's mtime now that the recipe has changed.  Safe
                # even on a no-op rerun: re-touching a PNG that already
                # bumped is idempotent for build correctness.
                try:
                    os.utime(dest_png, None)
                except OSError:
                    # If we can't touch (read-only?), leave alone — the
                    # user will see "make says nothing to do" and we
                    # have a CHANGELOG note documenting this risk.
                    pass
        except Exception as e:
            errors.append(f"spritesheet rule update: {e}")
            return False, applied, errors
    else:
        # Non-standard layout (sprite PNG outside the pics/<cat>/ tree).
        # Surface this as a non-fatal warning — the .4bpp may still build
        # if the project has a non-standard rule, but the user should
        # double-check.
        applied.append(
            f"Note — PNG at {rel_png} not under "
            f"graphics/object_events/pics/<category>/<slug>.png; "
            f"skipped spritesheet_rules.mk update.  Verify the "
            f"-mwidth / -mheight flags manually if the build looks wrong."
        )

    return True, applied, errors


def _scan_pic_png_path(root: str, pic_symbol: str) -> str:
    """Best-effort: find the on-disk PNG path for ``gObjectEventPic_<X>``
    by scanning the project's spritesheet rules + INCBIN declarations.

    Returns an empty string when the path can't be confidently resolved.
    """
    # The INCBIN tells us the .4bpp path; the matching .png lives at the
    # same relative location with the .4bpp extension swapped.
    pic_inc_path = os.path.join(
        root, "src", "data", "object_events", "object_event_graphics.h",
    )
    if not os.path.isfile(pic_inc_path):
        return ""
    try:
        with open(pic_inc_path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return ""
    # Pic INCBIN format in vanilla pokefirered:
    #   const u<8|16|32> gObjectEventPic_<X>[] = INCBIN_U<N>("graphics/...");
    # All three storage widths appear in the source; allow any.
    pat = re.compile(
        r"const\s+u(?:8|16|32)\s+" + re.escape(pic_symbol)
        + r"\s*\[\]\s*=\s*INCBIN_U\d+\s*\(\s*\"([^\"]+\.4bpp)\"",
    )
    m = pat.search(text)
    if not m:
        return ""
    rel_4bpp = m.group(1)
    rel_png = rel_4bpp[:-len(".4bpp")] + ".png"
    return os.path.join(root, rel_png.replace("/", os.sep))


# ════════════════════════════════════════════════════════════════════════════
# Normalize a sprite's frame table to its real on-disk frame count
# ════════════════════════════════════════════════════════════════════════════

def normalize_pic_table(root: str, info_name: str) -> Tuple[bool, List[str], List[str]]:
    """Rewrite ``sPicTable_<info_name>[]`` to sequential frames 0..N-1 of the
    sprite's OWN pic symbol, where N is the real frame count of its ``.4bpp`` on
    disk (file size / bytes-per-frame from the graphics info's frame size).

    WHY THIS EXISTS (do not remove): a frame table can be left in a stale layout
    after an image is REPLACED without the table being rebuilt — the classic
    case is a vanilla "standing" NPC whose vanilla table only references a few
    face frames and REPEATS them (e.g. OLD_MAN_2 was ``0,1,2,0,0,1,1,2,2,<a
    foreign OldWoman frame>``). Reused as a walking NPC, its 10-frame sheet is on
    disk but every "walk" slot still points at a standing frame, so it slides
    around with NO animation. Rebuilding to ``0..9`` of its own symbol fixes it.
    The generic image-import path (``import_image_manually_from_path``) swaps the
    image but never touches the table, so overworld import/replace callers MUST
    call this afterward — see ui/overworld_graphics_tab.py.

    Idempotent: a table already ``0..N-1`` of its own symbol is left unchanged.
    Returns ``(success, applied, errors)``.
    """
    applied: List[str] = []
    errors: List[str] = []

    gi_path = os.path.join(
        root, "src", "data", "object_events", "object_event_graphics_info.h")
    if not os.path.isfile(gi_path):
        return False, applied, [f"missing {gi_path}"]
    gi_text = _read_file(gi_path)
    info_symbol = f"gObjectEventGraphicsInfo_{info_name}"
    m = re.search(
        r"const\s+struct\s+ObjectEventGraphicsInfo\s+"
        + re.escape(info_symbol) + r"\s*=\s*\{(?P<body>[^;]*?)\};",
        gi_text, re.DOTALL)
    if not m:
        return False, applied, [f"{info_symbol} not found"]
    body = m.group("body")

    def _grab(field: str):
        mm = re.search(r"\." + field + r"\s*=\s*([^,\n]+),", body)
        return mm.group(1).strip() if mm else None

    try:
        w = int(_grab("width"))
        h = int(_grab("height"))
    except (TypeError, ValueError):
        return False, applied, [f"{info_symbol}: can't read width/height"]
    tile_w, tile_h = w // 8, h // 8
    bytes_per_frame = (w * h) // 2
    if bytes_per_frame <= 0:
        return False, applied, [f"{info_symbol}: bad frame size {w}x{h}"]

    pic_table_name = _grab("images")
    if not pic_table_name:
        return False, applied, [f"{info_symbol} has no .images"]
    # Always use the sprite's OWN pic symbol (never learn it from the possibly
    # broken existing entries — that's how the foreign OldWoman frame crept in).
    pic_symbol = f"gObjectEventPic_{info_name}"

    # Frame count from the sprite image on disk. Prefer the PNG (what the tool
    # writes at import time, BEFORE the build regenerates the .4bpp) so wiring
    # this into the import path is reliable; fall back to the built .4bpp.
    g_path = os.path.join(
        root, "src", "data", "object_events", "object_event_graphics.h")
    if not os.path.isfile(g_path):
        return False, applied, [f"missing {g_path}"]
    g_text = _read_file(g_path)
    incm = re.search(
        re.escape(pic_symbol) + r"\[\]\s*=\s*INCBIN_U16\(\"([^\"]+)\"\)", g_text)
    if not incm:
        return False, applied, [f"{pic_symbol} INCBIN not found"]
    rel_4bpp = incm.group(1)
    fourbpp = os.path.join(root, rel_4bpp.replace("/", os.sep))
    png = os.path.join(root, (rel_4bpp[:-5] + ".png").replace("/", os.sep))

    num_frames = None
    if os.path.isfile(png):
        try:
            with open(png, "rb") as fh:
                head = fh.read(24)
            # PNG IHDR: 8-byte sig + 4 len + "IHDR" + 4 width + 4 height (BE).
            if head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR":
                pw = int.from_bytes(head[16:20], "big")
                ph = int.from_bytes(head[20:24], "big")
                if pw % w == 0 and ph % h == 0:
                    num_frames = (pw // w) * (ph // h)
        except OSError:
            pass
    if num_frames is None and os.path.isfile(fourbpp):
        size = os.path.getsize(fourbpp)
        if size % bytes_per_frame == 0:
            num_frames = size // bytes_per_frame
    if not num_frames or num_frames < 1:
        return False, applied, [
            f"{pic_symbol}: could not determine frame count from PNG/.4bpp"]

    pt_path = os.path.join(
        root, "src", "data", "object_events", "object_event_pic_tables.h")
    if not os.path.isfile(pt_path):
        return False, applied, [f"missing {pt_path}"]
    pt_text = _read_file(pt_path)
    pat = re.compile(
        r"(static\s+const\s+struct\s+SpriteFrameImage\s+"
        + re.escape(pic_table_name) + r"\s*\[\]\s*=\s*\{)(?P<body>[^}]*?)(\};)",
        re.DOTALL)
    ptm = pat.search(pt_text)
    if not ptm:
        return False, applied, [f"{pic_table_name} not found"]

    desired = [
        f"    overworld_frame({pic_symbol}, {tile_w}, {tile_h}, {i}),"
        for i in range(num_frames)]
    new_block = ptm.group(1) + "\n" + "\n".join(desired) + "\n" + ptm.group(3)
    if re.sub(r"\s+", "", ptm.group(0)) == re.sub(r"\s+", "", new_block):
        return True, [f"{pic_table_name} already normalized "
                      f"({num_frames} frames)"], errors
    pt_text = pt_text[:ptm.start()] + new_block + pt_text[ptm.end():]
    try:
        _write_file(pt_path, pt_text)
    except Exception as e:
        return False, applied, [f"pic table write: {e}"]
    applied.append(
        f"Normalized {pic_table_name} -> frames 0..{num_frames - 1} "
        f"of {pic_symbol} ({num_frames} frames)")
    return True, applied, errors


# ════════════════════════════════════════════════════════════════════════════
# Delete an overworld sprite from a project
# ════════════════════════════════════════════════════════════════════════════

def delete_overworld_sprite(
    root: str,
    info_name: str,
    *,
    delete_files: bool = True,
) -> Tuple[bool, List[str], List[str]]:
    """Remove every trace of an overworld sprite from a pokefirered fork.

    Reverses what `create_overworld_sprite` adds.  Project-agnostic — no
    hardcoded names.  Targets `info_name` (PascalCase identifier of the
    sprite as it appears in `gObjectEventGraphicsInfo_<info_name>`).

    What gets removed:
      1. `#define OBJ_EVENT_GFX_<UPPER_NAME>` line in
         `include/constants/event_objects.h`, and `NUM_OBJ_EVENT_GFX` is
         decremented to match.  All other `#define`s with higher values
         are renumbered down by 1 so the pointer table stays packed.
      2. The sprite's row in `object_event_graphics_info_pointers.h`
         (both the `[OBJ_EVENT_GFX_<NAME>] = &gObjectEventGraphicsInfo_*`
         array entry AND the forward declaration up top).
      3. The full `const struct ObjectEventGraphicsInfo
         gObjectEventGraphicsInfo_<name> = { ... };` block in
         `object_event_graphics_info.h`.
      4. The `sPicTable_<name>[]` block in
         `object_event_pic_tables.h` — IF no other sprite still
         references it.
      5. The `gObjectEventPic_<name>[]` INCBIN line in
         `object_event_graphics.h` — IF no other sprite still
         references it (`sPicTable_*` arrays elsewhere).
      6. The sprite's PNG (`graphics/object_events/pics/<cat>/<slug>.png`)
         and its build artefact (`<slug>.4bpp`) — IF `delete_files=True`
         AND no other entry references the same .4bpp.
      7. If the sprite is a forked palette holder (its palette tag's
         `gObjectEventPal_<name>` symbol is referenced ONLY by this
         sprite's GraphicsInfo), also remove:
           - The `OBJ_EVENT_PAL_TAG_<UPPER_NAME>` `#define`
           - The `{gObjectEventPal_<name>, OBJ_EVENT_PAL_TAG_<NAME>}`
             entry in `sObjectEventSpritePalettes[]`
           - The `gObjectEventPal_<name>[]` INCBIN line
           - The `<slug>.gbapal` AND `<slug>.pal` files on disk

    Garbage-free contract:
      - No `.bak`/`.tmp` files left behind.
      - PNG and .gbapal files are only deleted when nothing else in the
        project references them, so a shared sprite asset can't be
        accidentally orphaned.
      - All file writes are atomic-replace via temp+rename.

    Returns `(success, applied_messages, error_messages)`.
    """
    applied: List[str] = []
    errors: List[str] = []

    # ── Derive related names from info_name ──────────────────────────
    # `info_name` is PascalCase ("BugCatcher").  The matching slug is
    # snake_case ("bug_catcher"); UPPER_SNAKE is the constant suffix
    # ("BUG_CATCHER").  We deliberately don't trust filename-based slug
    # mapping — derive everything from info_name and discover paths
    # by source-tree scan.
    upper = _camel_to_upper_snake(info_name)
    gfx_const = f"OBJ_EVENT_GFX_{upper}"
    info_symbol = f"gObjectEventGraphicsInfo_{info_name}"
    pic_table = f"sPicTable_{info_name}"
    pic_symbol = f"gObjectEventPic_{info_name}"
    pal_symbol = f"gObjectEventPal_{info_name}"

    # ── 1. Parse the GraphicsInfo block to learn .images, .paletteTag,
    #       and the sprite's PNG/category for cleanup decisions ───────
    gi_path = os.path.join(
        root, "src", "data", "object_events", "object_event_graphics_info.h",
    )
    if not os.path.isfile(gi_path):
        errors.append(f"missing {gi_path}")
        return False, applied, errors
    gi_text = _read_file(gi_path)
    info_match = re.search(
        r"const\s+struct\s+ObjectEventGraphicsInfo\s+"
        + re.escape(info_symbol)
        + r"\s*=\s*\{(?P<body>[^;]*?)\};",
        gi_text, flags=re.DOTALL,
    )
    if not info_match:
        errors.append(
            f"GraphicsInfo block for {info_symbol} not found — "
            f"sprite may already be removed or named differently"
        )
        return False, applied, errors
    info_body = info_match.group("body")
    cur_pic_table = _grab_field(info_body, "images") or pic_table
    cur_pal_tag = _grab_field(info_body, "paletteTag") or ""
    cur_w = _grab_field(info_body, "width") or ""
    cur_h = _grab_field(info_body, "height") or ""

    # ── 2. Decide which shared assets we can safely delete ───────────
    # Pic table / pic symbol — safe to remove only if no OTHER
    # GraphicsInfo block references this pic table.
    pic_table_others = [
        m.start() for m in re.finditer(
            r"\.images\s*=\s*" + re.escape(cur_pic_table) + r"\b",
            gi_text,
        )
    ]
    # Subtract our own occurrence (we'll be deleting it anyway)
    own_images_match = re.search(
        r"\.images\s*=\s*" + re.escape(cur_pic_table) + r"\b",
        info_body,
    )
    pic_table_is_unique = (
        len(pic_table_others) == 1 if own_images_match else False
    )

    # Palette: safe to remove only if no OTHER GraphicsInfo references
    # the SAME paletteTag.
    pal_tag_others = []
    if cur_pal_tag and cur_pal_tag != "OBJ_EVENT_PAL_TAG_NONE":
        pal_tag_others = [
            m.start() for m in re.finditer(
                r"\.paletteTag\s*=\s*" + re.escape(cur_pal_tag) + r"\b",
                gi_text,
            )
        ]
    pal_is_unique = (
        len(pal_tag_others) == 1 if cur_pal_tag else False
    )

    # ── 3. Remove the GraphicsInfo block ─────────────────────────────
    new_gi_text = (
        gi_text[:info_match.start()] + gi_text[info_match.end():]
    )
    # Tidy: collapse the leading newline before the block we removed so
    # we don't leave a triple-blank.
    new_gi_text = re.sub(r"\n{3,}", "\n\n", new_gi_text)
    _atomic_write(gi_path, new_gi_text)
    applied.append(f"Removed {info_symbol} block from object_event_graphics_info.h")

    # ── 4. Remove the pointer entry + forward declaration ────────────
    ptrs_path = os.path.join(
        root, "src", "data", "object_events",
        "object_event_graphics_info_pointers.h",
    )
    if os.path.isfile(ptrs_path):
        ptrs_text = _read_file(ptrs_path)
        # Forward declaration at the top (`const struct ... gObjectEventGraphicsInfo_X;`)
        new_ptrs = re.sub(
            r"const\s+struct\s+ObjectEventGraphicsInfo\s+"
            + re.escape(info_symbol) + r"\s*;\s*\n",
            "", ptrs_text, count=1,
        )
        # Pointer-table row (`[OBJ_EVENT_GFX_X] = &gObjectEventGraphicsInfo_X,`)
        new_ptrs = re.sub(
            r"\s*\[\s*" + re.escape(gfx_const)
            + r"\s*\][^,]*=\s*&" + re.escape(info_symbol) + r"\s*,\s*\n",
            "\n", new_ptrs, count=1,
        )
        if new_ptrs != ptrs_text:
            _atomic_write(ptrs_path, new_ptrs)
            applied.append(f"Removed pointer-table entry + fwd decl for {gfx_const}")

    # ── 5. Remove the gfx_const #define + renumber NUM_OBJ_EVENT_GFX ─
    eo_path = os.path.join(root, "include", "constants", "event_objects.h")
    if os.path.isfile(eo_path):
        eo_text = _read_file(eo_path)
        # Capture the deleted sprite's value so we can renumber every
        # higher #define down by 1.
        deleted_value: Optional[int] = None
        m = re.search(
            r"#define\s+" + re.escape(gfx_const) + r"\s+(\d+)",
            eo_text,
        )
        if m:
            deleted_value = int(m.group(1))
            new_eo = re.sub(
                r"#define\s+" + re.escape(gfx_const) + r"\s+\d+\s*\n",
                "", eo_text, count=1,
            )
            # Renumber every OBJ_EVENT_GFX_* with a value > deleted_value
            def _decrement(m2):
                name = m2.group(1)
                val = int(m2.group(2))
                if val > deleted_value:
                    val -= 1
                return f"#define {name} {val}"
            new_eo = re.sub(
                r"#define\s+(OBJ_EVENT_GFX_\w+)\s+(\d+)",
                _decrement, new_eo,
            )
            # Decrement NUM_OBJ_EVENT_GFX
            num_match = re.search(
                r"#define\s+(NUM_OBJ_EVENT_GFX)\s+(\d+)", new_eo,
            )
            if num_match:
                old_num = int(num_match.group(2))
                new_eo = re.sub(
                    r"(#define\s+NUM_OBJ_EVENT_GFX\s+)\d+",
                    rf"\g<1>{old_num - 1}", new_eo, count=1,
                )
            _atomic_write(eo_path, new_eo)
            applied.append(
                f"Removed #define {gfx_const} = {deleted_value} and "
                f"decremented NUM_OBJ_EVENT_GFX"
            )
        else:
            errors.append(
                f"#define {gfx_const} not found — already removed?"
            )

    # ── 6. Remove pic table + pic INCBIN if unique to this sprite ─────
    pt_path = os.path.join(
        root, "src", "data", "object_events", "object_event_pic_tables.h",
    )
    if pic_table_is_unique and os.path.isfile(pt_path):
        pt_text = _read_file(pt_path)
        new_pt = re.sub(
            r"static\s+const\s+struct\s+SpriteFrameImage\s+"
            + re.escape(cur_pic_table) + r"\s*\[\]\s*=\s*\{[^}]*\}\s*;\s*",
            "", pt_text, count=1, flags=re.DOTALL,
        )
        if new_pt != pt_text:
            new_pt = re.sub(r"\n{3,}", "\n\n", new_pt)
            _atomic_write(pt_path, new_pt)
            applied.append(f"Removed {cur_pic_table}[] from pic_tables.h")

    # Remove gObjectEventPic_<name>[] INCBIN if nothing else references
    # the pic symbol (e.g. another pic table).
    gfx_h_path = os.path.join(
        root, "src", "data", "object_events", "object_event_graphics.h",
    )
    if os.path.isfile(gfx_h_path):
        gfx_text = _read_file(gfx_h_path)
        other_refs = list(re.finditer(
            r"\b" + re.escape(pic_symbol) + r"\b",
            gfx_text,
        ))
        # One ref is the INCBIN line itself; check pic_tables.h + eom.c
        # for any LIVE consumers (after our edits above).
        pic_consumers = 0
        for path in (pt_path, os.path.join(root, "src", "event_object_movement.c")):
            if os.path.isfile(path):
                with open(path, encoding="utf-8", errors="replace") as f:
                    pic_consumers += f.read().count(pic_symbol)
        if pic_consumers == 0:
            new_gfx = re.sub(
                r"const\s+u16\s+" + re.escape(pic_symbol)
                + r"\s*\[\]\s*=\s*INCBIN_U\d+\([^)]*\);\s*\n",
                "", gfx_text, count=1,
            )
            if new_gfx != gfx_text:
                _atomic_write(gfx_h_path, new_gfx)
                applied.append(
                    f"Removed {pic_symbol} INCBIN from object_event_graphics.h"
                )

    # ── 7. Remove palette wiring if uniquely owned by this sprite ────
    if pal_is_unique and cur_pal_tag and cur_pal_tag != "OBJ_EVENT_PAL_TAG_NONE":
        # Remove from sObjectEventSpritePalettes[] in event_object_movement.c
        eom_path = os.path.join(root, "src", "event_object_movement.c")
        if os.path.isfile(eom_path):
            eom_text = _read_file(eom_path)
            # Array row: {gObjectEventPal_X, OBJ_EVENT_PAL_TAG_X},
            new_eom = re.sub(
                r"\s*\{\s*" + re.escape(pal_symbol)
                + r"\s*,\s*" + re.escape(cur_pal_tag) + r"\s*\}\s*,\s*\n",
                "\n", eom_text, count=1,
            )
            # #define OBJ_EVENT_PAL_TAG_X 0xNNNN
            new_eom = re.sub(
                r"#define\s+" + re.escape(cur_pal_tag) + r"\s+0x[0-9a-fA-F]+\s*\n",
                "", new_eom, count=1,
            )
            if new_eom != eom_text:
                _atomic_write(eom_path, new_eom)
                applied.append(
                    f"Removed {cur_pal_tag} #define + sObjectEventSpritePalettes entry"
                )

        # Remove gObjectEventPal_X INCBIN line
        if os.path.isfile(gfx_h_path):
            gfx_text = _read_file(gfx_h_path)
            new_gfx = re.sub(
                r"const\s+u16\s+" + re.escape(pal_symbol)
                + r"\s*\[\]\s*=\s*INCBIN_U\d+\(\"([^\"]+)\"\)\s*;\s*\n",
                "", gfx_text, count=1,
            )
            if new_gfx != gfx_text:
                # Capture the .gbapal path before we drop the INCBIN so
                # we can delete the on-disk file (its sibling .pal too).
                m_gba = re.search(
                    r"const\s+u16\s+" + re.escape(pal_symbol)
                    + r"\s*\[\]\s*=\s*INCBIN_U\d+\(\"([^\"]+)\"\)",
                    gfx_text,
                )
                _atomic_write(gfx_h_path, new_gfx)
                applied.append(
                    f"Removed {pal_symbol} INCBIN from object_event_graphics.h"
                )
                if delete_files and m_gba:
                    rel = m_gba.group(1)
                    abs_gba = os.path.join(root, rel)
                    abs_pal = os.path.splitext(abs_gba)[0] + ".pal"
                    for path in (abs_gba, abs_pal):
                        if os.path.isfile(path):
                            try:
                                os.remove(path)
                                applied.append(
                                    f"Deleted {os.path.relpath(path, root)}"
                                )
                            except OSError as exc:
                                errors.append(
                                    f"Could not delete {path}: {exc}"
                                )

    # ── 8. Delete the sprite's PNG + .4bpp build artefact ─────────────
    # Only if nothing else in the source still references the pic
    # symbol's .4bpp file (which is what gbagfx generates from the PNG).
    if delete_files and os.path.isfile(gfx_h_path):
        # The PNG path can no longer be inferred from the INCBIN (we
        # removed it).  Re-derive from cur_pic_table: pic_tables.h had
        # `overworld_frame(gObjectEventPic_<X>, ...)`.  The .4bpp
        # filename matches the pic symbol's INCBIN target.  We already
        # removed the INCBIN, but we captured the path during the
        # palette removal above.  For sprites whose palette wasn't
        # uniquely theirs, we never grabbed the path — try a slug-based
        # filename match as a fallback.
        slug = upper.lower()
        for cat in ("people", "pokemon", "misc"):
            for ext in (".png", ".4bpp"):
                p = os.path.join(
                    root, "graphics", "object_events", "pics", cat,
                    f"{slug}{ext}",
                )
                if os.path.isfile(p):
                    # Sanity-check: is anything in the source still
                    # referencing this file path?  If so, leave it.
                    rel = os.path.relpath(p, root).replace("\\", "/")
                    referenced = False
                    for ref_path in (
                        gfx_h_path,
                        os.path.join(root, "src", "data", "object_events",
                                     "object_event_pic_tables.h"),
                    ):
                        if os.path.isfile(ref_path):
                            with open(ref_path, encoding="utf-8",
                                      errors="replace") as f:
                                if rel in f.read():
                                    referenced = True
                                    break
                    if not referenced:
                        try:
                            os.remove(p)
                            applied.append(f"Deleted {rel}")
                        except OSError as exc:
                            errors.append(f"Could not delete {p}: {exc}")

    # ── 9. Strip the sprite's per-frame metatile rule from
    #       spritesheet_rules.mk (no-op if the sprite was deleted
    #       before the rule was ever added).  Iterate every category
    #       because we can't infer it from a sprite that's already
    #       been gutted from source.
    slug = upper.lower()
    for cat in ("people", "pokemon", "misc"):
        rel_4bpp = f"$(OBJEVENTGFXDIR)/{cat}/{slug}.4bpp"
        try:
            if _remove_spritesheet_rule(root, rel_4bpp):
                applied.append(
                    f"Removed spritesheet_rules.mk rule for {cat}/{slug}"
                )
        except Exception as exc:
            errors.append(
                f"Could not remove spritesheet rule for {cat}/{slug}: {exc}"
            )

    # ── 10. Remove generated OAM/subsprite scaffolding if this was the
    #        last sprite of its size.  Only PorySuite-generated (fenced)
    #        blocks are touched — `remove_*` leaves vanilla tables alone.
    if cur_w.isdigit() and cur_h.isdigit():
        w_i, h_i = int(cur_w), int(cur_h)
        still_used = False
        for om in re.finditer(
            r"\.width\s*=\s*(\d+)\s*,[^;]*?\.height\s*=\s*(\d+)\s*,",
            new_gi_text, flags=re.DOTALL,
        ):
            if int(om.group(1)) == w_i and int(om.group(2)) == h_i:
                still_used = True
                break
        if not still_used:
            try:
                for res in (
                    subsprite_gen.remove_subsprite_table(root, w_i, h_i),
                    subsprite_gen.remove_oam_base(root, w_i, h_i),
                ):
                    if res.changed:
                        applied.append(res.detail)
            except Exception as exc:
                errors.append(
                    f"Could not remove generated {w_i}x{h_i} tables: {exc}"
                )

    success = len(errors) == 0
    return success, applied, errors


# ════════════════════════════════════════════════════════════════════════════
# Project-wide self-heal: ensure every overworld sprite has a per-frame
# metatile rule in spritesheet_rules.mk
# ════════════════════════════════════════════════════════════════════════════

def ensure_all_overworld_spritesheet_rules(
    root: str,
) -> Tuple[List[str], List[str], List[str]]:
    """Scan every overworld sprite's GraphicsInfo + INCBIN path and make
    sure ``spritesheet_rules.mk`` has a matching per-frame metatile rule.

    This protects against the historical bug where ``create_overworld_sprite``
    didn't add the rule, leaving the .4bpp to be built by the generic
    ``%.4bpp: %.png`` rule (no ``-mwidth``/``-mheight``).  The resulting
    file uses row-major tile order across the whole strip — but the engine's
    ``overworld_frame`` macro reads bytes as if frames were contiguous.
    The render glitch is "frame N shows the top halves of N and N+1 stacked".

    What we do:

    1. Parse every ``gObjectEventPic_<X>[] = INCBIN_U16(...)`` from
       ``object_event_graphics.h`` to get the PNG path per pic symbol.
    2. Cross-reference each pic symbol with its
       ``gObjectEventGraphicsInfo_<X>`` (via the ``.images = sPicTable_<X>``
       linkage and the matching ``.width / .height`` fields) to determine
       per-frame pixel dimensions.
    3. For every PNG whose .4bpp target lacks a rule in
       ``spritesheet_rules.mk``, append a new rule with the correct
       ``-mwidth tile_w -mheight tile_h``.
    4. Delete any stale ``.4bpp`` that was previously built without
       the rule, so the next build regenerates it with correct tile order.

    Idempotent: re-running adds nothing if all rules already exist.
    Project-agnostic: derives everything from source-tree state — no
    hardcoded sprite names or dimensions.

    Returns ``(rules_added, files_invalidated, errors)`` — each a list
    of plain-English descriptions for the user-facing log.
    """
    rules_added: List[str] = []
    invalidated: List[str] = []
    errors: List[str] = []

    gfx_h_path = os.path.join(
        root, "src", "data", "object_events", "object_event_graphics.h"
    )
    gi_path = os.path.join(
        root, "src", "data", "object_events", "object_event_graphics_info.h"
    )
    pt_path = os.path.join(
        root, "src", "data", "object_events", "object_event_pic_tables.h"
    )
    rules_path = os.path.join(root, "spritesheet_rules.mk")
    for p in (gfx_h_path, gi_path, pt_path, rules_path):
        if not os.path.isfile(p):
            errors.append(f"missing {os.path.relpath(p, root)}")
            return rules_added, invalidated, errors

    try:
        gfx_text = _read_file(gfx_h_path)
        gi_text = _read_file(gi_path)
        pt_text = _read_file(pt_path)
        rules_text = _read_file(rules_path)
    except Exception as exc:
        errors.append(f"read source: {exc}")
        return rules_added, invalidated, errors

    # Map pic_symbol -> (category, slug)  from INCBIN lines like
    #   const u16 gObjectEventPic_Gravekid[] =
    #       INCBIN_U16("graphics/object_events/pics/people/gravekid.4bpp");
    pic_to_path: Dict[str, Tuple[str, str]] = {}
    for m in re.finditer(
        r"const\s+u16\s+(gObjectEventPic_\w+)\s*\[\]\s*=\s*"
        r"INCBIN_U\d+\(\"graphics/object_events/pics/([^/]+)/([^./]+)\.4bpp\"\)",
        gfx_text,
    ):
        pic_to_path[m.group(1)] = (m.group(2), m.group(3))

    # Walk every GraphicsInfo block; correlate to its pic symbol via the
    # `.images = sPicTable_<X>` line, then look up that pic table in
    # pic_tables.h to find its pic symbol.  Doing it this way matches
    # vanilla layout AND user-edited projects without assuming naming
    # parity between info_name and pic_symbol.
    pic_dims: Dict[str, Tuple[int, int]] = {}
    for m in re.finditer(
        r"const\s+struct\s+ObjectEventGraphicsInfo\s+gObjectEventGraphicsInfo_\w+\s*=\s*\{(?P<body>[^;]*?)\};",
        gi_text, flags=re.DOTALL,
    ):
        body = m.group("body")
        pt_match = re.search(r"\.images\s*=\s*(sPicTable_\w+)", body)
        w_match = re.search(r"\.width\s*=\s*(\d+)", body)
        h_match = re.search(r"\.height\s*=\s*(\d+)", body)
        if not (pt_match and w_match and h_match):
            continue
        pic_table = pt_match.group(1)
        frame_w = int(w_match.group(1))
        frame_h = int(h_match.group(1))
        # Find the pic table's first entry to get its pic symbol.
        pt_block_match = re.search(
            r"static\s+const\s+struct\s+SpriteFrameImage\s+"
            + re.escape(pic_table)
            + r"\s*\[\]\s*=\s*\{(?P<body>[^}]*)\}",
            pt_text, flags=re.DOTALL,
        )
        if not pt_block_match:
            continue
        first_pic = re.search(
            r"overworld_frame\s*\(\s*(gObjectEventPic_\w+)",
            pt_block_match.group("body"),
        )
        if not first_pic:
            continue
        pic_dims[first_pic.group(1)] = (frame_w, frame_h)

    # For each pic symbol that has both a path and dimensions, check
    # whether spritesheet_rules.mk already has a rule.  If not, append.
    # Track which .4bpp files need to be deleted so they get rebuilt.
    #
    # CRITICAL heuristic: only add a rule for sprites that are
    # HORIZONTAL STRIPS of multiple frames (PNG width > frame width).
    # Single-frame sprites (PNG width == frame width) work fine with
    # the default ``%.4bpp: %.png`` rule because row-major tile order
    # IS per-frame order when there's only one frame.  Adding rules to
    # single-frame sprites is harmless (gbagfx produces identical
    # output either way) but invalidates the existing .4bpp and forces
    # an unnecessary rebuild.  So we skip them.
    try:
        from PyQt6.QtGui import QImage
    except Exception:
        QImage = None  # type: ignore

    appended_any = False
    new_rules_text = rules_text
    for pic_symbol, (category, slug) in pic_to_path.items():
        if pic_symbol not in pic_dims:
            continue
        frame_w, frame_h = pic_dims[pic_symbol]
        tile_w = frame_w // 8
        tile_h = frame_h // 8
        if tile_w <= 0 or tile_h <= 0:
            continue

        # Skip single-frame sprites — they don't need the rule.  Determine
        # frame count by reading the PNG width.  If we can't read it (no
        # PyQt6, missing file), be conservative and skip — the user has
        # already shipped without the rule, so adding one now would only
        # invalidate the existing .4bpp.
        png_path = os.path.join(
            root, "graphics", "object_events", "pics", category, f"{slug}.png"
        )
        if QImage is None or not os.path.isfile(png_path):
            continue
        png = QImage(png_path)
        if png.isNull():
            continue
        if png.width() <= frame_w:
            continue  # single-frame; rule not needed

        rel_4bpp = f"$(OBJEVENTGFXDIR)/{category}/{slug}.4bpp"
        pat = re.compile(
            r"^" + re.escape(rel_4bpp) + r"\s*:",
            re.MULTILINE,
        )
        if pat.search(new_rules_text):
            continue  # already has a rule

        block = (
            f"\n{rel_4bpp}: %.4bpp: %.png\n"
            f"\t$(GFX) $< $@ -mwidth {tile_w} -mheight {tile_h}\n"
        )
        if not new_rules_text.endswith("\n"):
            new_rules_text += "\n"
        new_rules_text += block
        appended_any = True
        rules_added.append(
            f"{category}/{slug}.4bpp (-mwidth {tile_w} -mheight {tile_h})"
        )

        # The existing .4bpp was built without the rule and has wrong
        # tile order.  Delete it so the next build regenerates with
        # gbagfx's per-frame metatile flag.
        stale_4bpp = os.path.join(
            root, "graphics", "object_events", "pics", category, f"{slug}.4bpp"
        )
        if os.path.isfile(stale_4bpp):
            try:
                os.remove(stale_4bpp)
                invalidated.append(f"{category}/{slug}.4bpp")
            except OSError as exc:
                errors.append(
                    f"Could not delete stale {stale_4bpp}: {exc}"
                )

    if appended_any:
        try:
            _atomic_write(rules_path, new_rules_text)
        except Exception as exc:
            errors.append(f"write spritesheet_rules.mk: {exc}")

    return rules_added, invalidated, errors


# ── private helpers ────────────────────────────────────────────────────

def _write_vertical_frame_strip(
    src_png: str,
    dest_png: str,
    frame_w: int,
    frame_h: int,
    num_frames: int,
) -> None:
    """Re-lay-out a horizontal frame strip as a vertical one.

    Composite overworld sprites are built with gbagfx metatile flags
    sized to one hardware *piece*.  gbagfx emits metatiles row-major
    DOWN the image, so for every frame's tiles to land in the one
    contiguous run that ``overworld_frame`` indexes, the frames must be
    stacked vertically rather than side by side.

    The copy is index-for-index, so the sprite's 4bpp colour table —
    including the transparent slot 0 — is preserved exactly.
    """
    from PyQt6.QtGui import QImage

    src = QImage(src_png)
    if src.isNull():
        raise ValueError(f"could not read PNG: {src_png}")
    out = QImage(frame_w, frame_h * num_frames, src.format())
    color_table = src.colorTable()
    indexed = bool(color_table)
    if indexed:
        out.setColorTable(color_table)
    out.fill(0)
    for f in range(num_frames):
        for y in range(frame_h):
            dst_y = f * frame_h + y
            for x in range(frame_w):
                src_x = f * frame_w + x
                if indexed:
                    out.setPixel(x, dst_y, src.pixelIndex(src_x, y))
                else:
                    out.setPixel(x, dst_y, src.pixel(src_x, y))
    if not out.save(dest_png, "PNG"):
        raise ValueError(f"could not write PNG: {dest_png}")


def pad_sprite_sheet(
    src_png: str,
    num_frames: int,
    dest_png: str,
) -> Tuple[int, int]:
    """Pad every frame of a horizontal sprite sheet up to the next
    multiple of 16 in both dimensions, writing a new horizontal strip.

    For an odd sheet — e.g. 72x40 imported as two 36x40 frames — this
    turns each frame into a build-legal canvas (48x48) instead of the
    editor rejecting the import.  The original frame is placed
    bottom-centred in its padded cell, so a character keeps standing on
    the floor; the new margin is transparent (palette index 0).

    Returns the padded ``(frame_w, frame_h)``.  Raises ``ValueError`` if
    ``num_frames`` does not evenly divide the sheet width.
    """
    from PyQt6.QtGui import QImage

    src = QImage(src_png)
    if src.isNull():
        raise ValueError(f"could not read PNG: {src_png}")
    iw, ih = src.width(), src.height()
    if num_frames < 1 or iw % num_frames != 0:
        raise ValueError(
            f"{num_frames} frame(s) do not divide a {iw}px-wide sheet "
            f"evenly — pick a frame count that divides {iw}."
        )
    src_fw = iw // num_frames
    src_fh = ih
    pad_fw = ((src_fw + 15) // 16) * 16
    pad_fh = ((src_fh + 15) // 16) * 16

    out = QImage(pad_fw * num_frames, pad_fh, src.format())
    color_table = src.colorTable()
    indexed = bool(color_table)
    if indexed:
        out.setColorTable(color_table)
    out.fill(0)                        # index 0 = transparent margin
    x_off = (pad_fw - src_fw) // 2      # centre the frame horizontally
    y_off = pad_fh - src_fh            # bottom-align it (feet on the floor)
    for f in range(num_frames):
        for y in range(src_fh):
            for x in range(src_fw):
                sx = f * src_fw + x
                dx = f * pad_fw + x_off + x
                dy = y_off + y
                if indexed:
                    out.setPixel(dx, dy, src.pixelIndex(sx, y))
                else:
                    out.setPixel(dx, dy, src.pixel(sx, y))
    if not out.save(dest_png, "PNG"):
        raise ValueError(f"could not write PNG: {dest_png}")
    return pad_fw, pad_fh


def _atomic_write(path: str, text: str) -> None:
    """Write text atomically via temp+rename; clean up .tmp on failure.

    Used by delete_overworld_sprite to ensure partial-write corruption
    can't leave a project source file in a half-edited state.
    """
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _camel_to_upper_snake(name: str) -> str:
    """`BugCatcherR` -> `BUG_CATCHER_R`.  Matches the convention used
    by overworld_palette_fork — keep in sync if either is changed.
    """
    if "_" in name:
        return name.upper()
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).upper()


def _grab_field(struct_body: str, field: str) -> Optional[str]:
    """Pull `.field = VALUE,` from a C struct body.  Returns the
    VALUE token or None if not found."""
    m = re.search(
        r"\." + re.escape(field) + r"\s*=\s*(\S+)\s*,",
        struct_body,
    )
    return m.group(1) if m else None
