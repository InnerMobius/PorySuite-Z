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

    success = len(errors) == 0
    return success, applied, errors


# ── private helpers ────────────────────────────────────────────────────

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
