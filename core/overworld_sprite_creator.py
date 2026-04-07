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

Color = Tuple[int, int, int]

# ── OAM / subsprite table lookup by pixel dimensions ────────────────────────

_OAM_TABLE = {
    (16, 16): ("gObjectEventBaseOam_16x16", "gObjectEventSpriteOamTables_16x16"),
    (16, 32): ("gObjectEventBaseOam_16x32", "gObjectEventSpriteOamTables_16x32"),
    (32, 32): ("gObjectEventBaseOam_32x32", "gObjectEventSpriteOamTables_32x32"),
    (32, 64): ("gObjectEventBaseOam_32x32", "gObjectEventSpriteOamTables_32x64"),
    (64, 64): ("gObjectEventBaseOam_64x64", "gObjectEventSpriteOamTables_64x64"),
}

# ── Animation table names for user-facing choices ───────────────────────────

ANIM_TABLE_CHOICES = [
    ("sAnimTable_Standard", "Walk Cycle (standard 9-frame)"),
    ("sAnimTable_Inanimate", "Static / Inanimate"),
    ("sAnimTable_RedGreenNormal", "Walk Cycle (Player-style)"),
]

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

    # Determine frame count from PNG dimensions
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

    # OAM lookup
    oam_key = (frame_w, frame_h)
    if oam_key not in _OAM_TABLE:
        # Find closest match
        oam_name = f"gObjectEventBaseOam_{frame_w}x{frame_h}"
        sub_name = f"gObjectEventSpriteOamTables_{frame_w}x{frame_h}"
    else:
        oam_name, sub_name = _OAM_TABLE[oam_key]

    # ── Step 1: Copy PNG to project ─────────────────────────────────────
    dest_dir = os.path.join(root, "graphics", "object_events", "pics", category)
    os.makedirs(dest_dir, exist_ok=True)
    dest_png = os.path.join(dest_dir, f"{sprite_slug}.png")
    try:
        shutil.copy2(png_source, dest_png)
        applied.append(f"Copied PNG to graphics/object_events/pics/{category}/{sprite_slug}.png")
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
