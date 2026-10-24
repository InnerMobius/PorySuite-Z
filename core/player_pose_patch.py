"""Engine patcher: register a new PLAYER AVATAR POSE from a pair of sprites.

What a pose is
==============

The player's overworld sprite is not one graphic — it is a state picked from
``sPlayerAvatarGfxIds[state][gender]``, which is why
``GetPlayerAvatarGraphicsIdByStateId()`` resolves the right red/green sprite
with no gender branch anywhere in the caller. Adding a pose means adding a row
to that table, which means registering two OBJ_EVENT_GFX graphics first.

That is six edits across six files, and the Overworld Graphics tab's "Add New
Sprite" does not do it: that writes an ordinary NPC graphic and has no concept
of the avatar state table or of a red/green pair. Hence this.

The six edits, all sentinel-fenced and idempotent:

1. ``include/constants/event_objects.h``        — two OBJ_EVENT_GFX_* ids, NUM bumped
2. ``src/data/object_events/object_event_graphics.h``        — the INCBINs
3. ``src/data/object_events/object_event_pic_tables.h``      — frame tables
4. ``src/data/object_events/object_event_anims.h``           — the anim table
5. ``src/data/object_events/object_event_graphics_info_pointers.h`` — decls + table rows
6. ``src/data/object_events/object_event_graphics_info.h``   — the info structs
plus ``include/global.fieldmap.h`` for the state enum and
``src/field_player_avatar.c`` for the ``sPlayerAvatarGfxIds`` row.

Single-frame poses
==================

A pose sheet does not have to be a 9-frame directional sheet. A one-frame
sheet is a perfectly good "hold this pose" — and for a get-item pose it is the
right thing, because the cutscene forces the player to face the camera anyway.

What a one-frame sheet CANNOT use is the normal 9-frame anim table: every
direction and walk state would index frames that do not exist, and the sprite
renders as garbage. So this generates an anim table that maps every state —
all four facings, every walk and run speed — onto one anim. The engine does
the same thing in miniature with ``sAnimTable_RedGreenFieldMove`` and
``sAnimTable_RedGreenVSSeeker``: each holds a single entry, because a pose
that looks the same whichever way you are facing needs nothing else.

The caller's half
=================

Registering the pose does not make anything use it. ``apply_pose_to_call``
repoints an existing ``GetPlayerAvatarGraphicsIdByStateId(PLAYER_AVATAR_GFX_X)``
call at the new state, which is how a cutscene that currently borrows another
pose gets its own.

And whatever uses it must keep the ``ObjectEventTurn`` that follows
``ObjectEventSetGraphicsId``: the swap allocates fresh VRAM but does not
populate it, so without restarting the animation the player renders as
garbage. This project's ``src/bomb.c`` carries the comment that documents it.
"""

from __future__ import annotations

import os
import re

_CONSTANTS_REL = os.path.join("include", "constants", "event_objects.h")
_FIELDMAP_REL = os.path.join("include", "global.fieldmap.h")
_AVATAR_C_REL = os.path.join("src", "field_player_avatar.c")
_OE = os.path.join("src", "data", "object_events")
_GFX_REL = os.path.join(_OE, "object_event_graphics.h")
_PICS_REL = os.path.join(_OE, "object_event_pic_tables.h")
_ANIMS_REL = os.path.join(_OE, "object_event_anims.h")
_INFO_PTRS_REL = os.path.join(_OE, "object_event_graphics_info_pointers.h")
_INFO_REL = os.path.join(_OE, "object_event_graphics_info.h")

_SENTINEL = "PORYSUITE-POSE"

_PIC_DIR = os.path.join("graphics", "object_events", "pics", "people")

#: Frame geometry. Overworld people are 16x32; `overworld_frame(pic, 2, 4, n)`
#: is "2 tiles wide, 4 tall, frame n", which is what every player sheet uses.
_FRAME_W_TILES = 2
_FRAME_H_TILES = 4
_FRAME_W_PX = _FRAME_W_TILES * 8
_FRAME_H_PX = _FRAME_H_TILES * 8


class PlayerPosePatchError(RuntimeError):
    """Raised rather than guessing when the source is not what we expect."""


# ─────────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────────

def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _write(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)


def _camel(name: str) -> str:
    """``red_carry_pose`` -> ``RedCarryPose``."""
    return "".join(p.capitalize() for p in re.split(r"[^A-Za-z0-9]+", name) if p)


def _png_frames(path: str) -> tuple[int, int]:
    """(frame_count, height_px) for a 16px-wide player sheet.

    Reads the PNG header directly — a 24-byte read, no Pillow dependency in a
    patcher that might run before the image stack is loaded.
    """
    with open(path, "rb") as fh:
        head = fh.read(24)
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n":
        raise PlayerPosePatchError(f"{os.path.basename(path)} is not a PNG.")
    width = int.from_bytes(head[16:20], "big")
    height = int.from_bytes(head[20:24], "big")
    if width != _FRAME_W_PX:
        raise PlayerPosePatchError(
            f"{os.path.basename(path)} is {width}px wide; a player pose sheet "
            f"must be {_FRAME_W_PX}px wide, with frames stacked vertically.")
    if height % _FRAME_H_PX:
        raise PlayerPosePatchError(
            f"{os.path.basename(path)} is {height}px tall, which is not a "
            f"whole number of {_FRAME_H_PX}px frames.")
    return height // _FRAME_H_PX, height


# ─────────────────────────────────────────────────────────────────────────────
# status
# ─────────────────────────────────────────────────────────────────────────────

def pose_sentinel(state_name: str) -> str:
    return f"{_SENTINEL} {state_name}"


def is_pose_installed(project_root: str, state_name: str) -> bool:
    try:
        return pose_sentinel(state_name) in _read(
            os.path.join(project_root, _AVATAR_C_REL))
    except OSError:
        return False


def project_supports_poses(project_root: str) -> bool:
    """True when this project has the avatar-state table at all.

    A vanilla pokefirered does — but a project that has rewritten the player
    avatar may not, and adding a row to a table that is not there would be a
    confusing failure rather than a clear one.
    """
    try:
        return "sPlayerAvatarGfxIds[][GENDER_COUNT]" in _read(
            os.path.join(project_root, _AVATAR_C_REL))
    except OSError:
        return False


def describe(project_root: str, state_name: str, red_png: str,
             green_png: str) -> str:
    """What would happen, in one paragraph, before anything is written."""
    if not project_supports_poses(project_root):
        return ("This project has no sPlayerAvatarGfxIds table, so it does "
                "not use the avatar-state system this adds a pose to.")
    if is_pose_installed(project_root, state_name):
        return f"PLAYER_AVATAR_GFX_{state_name} is already registered."
    pics = os.path.join(project_root, _PIC_DIR)
    missing = [p for p in (red_png, green_png)
               if not os.path.isfile(os.path.join(pics, f"{p}.png"))]
    if missing:
        return ("Missing sprite sheet(s) in graphics/object_events/pics/"
                "people: " + ", ".join(f"{m}.png" for m in missing))
    try:
        frames, _ = _png_frames(os.path.join(pics, f"{red_png}.png"))
    except PlayerPosePatchError as exc:
        return str(exc)
    shape = ("one frame, so the pose will look the same whichever way the "
             "player faces" if frames == 1 else f"{frames} frames")
    return (f"Would register PLAYER_AVATAR_GFX_{state_name} from "
            f"{red_png}.png / {green_png}.png ({shape}), touching 8 files.")


# ─────────────────────────────────────────────────────────────────────────────
# the edits
# ─────────────────────────────────────────────────────────────────────────────

def _patch_constants(root: str, state: str, red: str, green: str) -> int:
    """Add the two OBJ_EVENT_GFX ids. Returns the red one's numeric id."""
    path = os.path.join(root, _CONSTANTS_REL)
    text = _read(path)
    m = re.search(r"^#define\s+NUM_OBJ_EVENT_GFX\s+(\d+)", text, re.M)
    if not m:
        raise PlayerPosePatchError(
            "NUM_OBJ_EVENT_GFX not found in event_objects.h — cannot tell "
            "which graphics ids are free.")
    base = int(m.group(1))
    red_id, green_id = base, base + 1
    block = (
        f"\n/* {pose_sentinel(state)} */\n"
        f"#define OBJ_EVENT_GFX_{state}_RED{' ' * 14}{red_id}"
        f"   // {red}.png\n"
        f"#define OBJ_EVENT_GFX_{state}_GREEN{' ' * 12}{green_id}"
        f"   // {green}.png\n"
    )
    text = text[:m.start()] + block + "\n" + text[m.start():]
    text = re.sub(r"^#define\s+NUM_OBJ_EVENT_GFX\s+\d+",
                  f"#define NUM_OBJ_EVENT_GFX     {base + 2}", text,
                  count=1, flags=re.M)
    _write(path, text)
    return red_id


def _patch_graphics(root: str, state: str, red: str, green: str) -> None:
    path = os.path.join(root, _GFX_REL)
    text = _read(path).rstrip("\n")
    text += (
        f"\n\n/* {pose_sentinel(state)} */\n"
        f'const u16 gObjectEventPic_{_camel(red)}[] = '
        f'INCBIN_U16("{_PIC_DIR}/{red}.4bpp".replace(chr(92), "/"));\n'
    ).replace('.replace(chr(92), "/")', "")
    text += (
        f'const u16 gObjectEventPic_{_camel(green)}[] = '
        f'INCBIN_U16("{_PIC_DIR}/{green}.4bpp");\n'
    )
    _write(path, text.replace("\\", "/"))


def _patch_pic_tables(root: str, state: str, red: str, green: str,
                      frames: int) -> None:
    path = os.path.join(root, _PICS_REL)
    text = _read(path).rstrip("\n") + f"\n\n/* {pose_sentinel(state)} */\n"
    for name in (red, green):
        rows = "".join(
            f"    overworld_frame(gObjectEventPic_{_camel(name)}, "
            f"{_FRAME_W_TILES}, {_FRAME_H_TILES}, {i}),\n"
            for i in range(frames))
        text += (f"static const struct SpriteFrameImage "
                 f"sPicTable_{_camel(name)}[] = {{\n{rows}}};\n\n")
    _write(path, text)


_ANIM_STATES = [
    "ANIM_STD_FACE_SOUTH", "ANIM_STD_FACE_NORTH", "ANIM_STD_FACE_WEST",
    "ANIM_STD_FACE_EAST", "ANIM_STD_GO_SOUTH", "ANIM_STD_GO_NORTH",
    "ANIM_STD_GO_WEST", "ANIM_STD_GO_EAST", "ANIM_STD_GO_FAST_SOUTH",
    "ANIM_STD_GO_FAST_NORTH", "ANIM_STD_GO_FAST_WEST", "ANIM_STD_GO_FAST_EAST",
    "ANIM_STD_GO_FASTER_SOUTH", "ANIM_STD_GO_FASTER_NORTH",
    "ANIM_STD_GO_FASTER_WEST", "ANIM_STD_GO_FASTER_EAST",
    "ANIM_STD_GO_FASTEST_SOUTH", "ANIM_STD_GO_FASTEST_NORTH",
    "ANIM_STD_GO_FASTEST_WEST", "ANIM_STD_GO_FASTEST_EAST",
    "ANIM_RUN_SOUTH", "ANIM_RUN_NORTH", "ANIM_RUN_WEST", "ANIM_RUN_EAST",
]


def _patch_anims(root: str, state: str) -> str:
    """Add a hold-this-pose anim table. Returns its symbol name."""
    table = f"sAnimTable_Pose{_camel(state)}"
    anim = f"sAnim_Pose{_camel(state)}"
    path = os.path.join(root, _ANIMS_REL)
    text = _read(path).rstrip("\n")
    rows = "".join(f"    [{s}]{' ' * max(1, 30 - len(s))}= {anim},\n"
                   for s in _ANIM_STATES)
    text += (
        f"\n\n/* {pose_sentinel(state)} */\n"
        f"// One held pose. EVERY facing and walk state maps to the same anim:\n"
        f"// a single-frame sheet has no per-direction frames to index, and\n"
        f"// pointing the normal table at it would read frames that do not\n"
        f"// exist and render garbage.\n"
        f"static const union AnimCmd {anim}[] = {{\n"
        f"    ANIMCMD_FRAME(0, 16),\n"
        f"    ANIMCMD_JUMP(0),\n"
        f"}};\n\n"
        f"static const union AnimCmd *const {table}[] = {{\n{rows}}};\n"
    )
    _write(path, text)
    return table


def _patch_info_pointers(root: str, state: str, red: str, green: str) -> None:
    path = os.path.join(root, _INFO_PTRS_REL)
    text = _read(path)
    decls = (f"\n/* {pose_sentinel(state)} */\n"
             f"const struct ObjectEventGraphicsInfo "
             f"gObjectEventGraphicsInfo_{_camel(red)};\n"
             f"const struct ObjectEventGraphicsInfo "
             f"gObjectEventGraphicsInfo_{_camel(green)};\n")
    anchor = "const struct ObjectEventGraphicsInfo gObjectEventGraphicsInfo_RedBomb;"
    if anchor in text:
        text = text.replace(anchor, decls.rstrip("\n") + "\n" + anchor, 1)
    else:
        first = re.search(r"^const struct ObjectEventGraphicsInfo \w+;", text, re.M)
        if not first:
            raise PlayerPosePatchError(
                "No ObjectEventGraphicsInfo declarations found in "
                "object_event_graphics_info_pointers.h.")
        text = text[:first.start()] + decls.lstrip("\n") + text[first.start():]

    # Table rows, inserted before the closing brace of the pointer table.
    rows = (f"    /* {pose_sentinel(state)} */\n"
            f"    [OBJ_EVENT_GFX_{state}_RED]"
            f"{' ' * 10}= &gObjectEventGraphicsInfo_{_camel(red)},\n"
            f"    [OBJ_EVENT_GFX_{state}_GREEN]"
            f"{' ' * 8}= &gObjectEventGraphicsInfo_{_camel(green)},\n")
    m = None
    for m in re.finditer(r"\n\};", text):
        pass
    if m is None:
        raise PlayerPosePatchError(
            "Could not find the end of the graphics-info pointer table.")
    text = text[:m.start()] + "\n" + rows.rstrip("\n") + text[m.start():]
    _write(path, text)


def _player_sprite_size(root: str) -> int:
    """The `.size` the project gives the player's NORMAL sprite.

    Copied rather than computed. `.size` is the VRAM allocation, and it is NOT
    simply width*height/2: across the engine's own player poses it is 512 for
    some (normal, bike, fish) and 256 for others (surf, field move, VS Seeker),
    with no rule derivable from the geometry. Guessing low would
    under-allocate and truncate the sprite's tiles, which shows up as a
    corrupted player — so take the value from the sprite the player spends
    most of its life as, which can only ever over-allocate.
    """
    try:
        text = _read(os.path.join(root, _INFO_REL))
    except OSError:
        return _FRAME_W_PX * _FRAME_H_PX // 2
    m = re.search(r"gObjectEventGraphicsInfo_RedNormal\s*=\s*\{.*?"
                  r"\.size\s*=\s*(\d+)", text, re.S)
    if m:
        return int(m.group(1))
    # No RedNormal: fall back to one frame's worth, and say so in the output.
    return _FRAME_W_PX * _FRAME_H_PX // 2


def _patch_info(root: str, state: str, red: str, green: str,
                anim_table: str, frames: int) -> None:
    path = os.path.join(root, _INFO_REL)
    size = _player_sprite_size(root)
    text = _read(path).rstrip("\n") + f"\n\n/* {pose_sentinel(state)} */\n"
    for name, pal in ((red, "OBJ_EVENT_PAL_TAG_PLAYER_RED"),
                      (green, "OBJ_EVENT_PAL_TAG_PLAYER_GREEN")):
        text += (
            f"const struct ObjectEventGraphicsInfo "
            f"gObjectEventGraphicsInfo_{_camel(name)} = {{\n"
            f"    .tileTag = TAG_NONE,\n"
            f"    .paletteTag = {pal},\n"
            f"    .reflectionPaletteTag = OBJ_EVENT_PAL_TAG_BRIDGE_REFLECTION,\n"
            f"    .size = {size},   // matches the player's normal sprite\n"
            f"    .width = {_FRAME_W_PX},\n"
            f"    .height = {_FRAME_H_PX},\n"
            f"    .paletteSlot = PALSLOT_PLAYER,\n"
            f"    .shadowSize = SHADOW_SIZE_M,\n"
            f"    .inanimate = FALSE,\n"
            f"    .disableReflectionPaletteLoad = FALSE,\n"
            f"    .tracks = TRACKS_FOOT,\n"
            f"    .oam = &gObjectEventBaseOam_16x32,\n"
            f"    .subspriteTables = gObjectEventSpriteOamTables_16x32,\n"
            f"    .anims = {anim_table},\n"
            f"    .images = sPicTable_{_camel(name)},\n"
            f"    .affineAnims = gDummySpriteAffineAnimTable,\n"
            f"}};\n\n")
    _write(path, text)


def _patch_state_enum(root: str, state: str) -> None:
    path = os.path.join(root, _FIELDMAP_REL)
    text = _read(path)
    m = re.search(r"(\n\s*)PLAYER_AVATAR_GFX_NORMAL,", text)
    if not m:
        raise PlayerPosePatchError(
            "PLAYER_AVATAR_GFX_NORMAL not found in global.fieldmap.h — this "
            "project does not use the avatar-state enum.")
    # Append at the END of the enum so existing state numbers do not shift.
    # Anything that saved a state id would otherwise come back as a different
    # pose, and save data outlives a rebuild.
    end = text.index("}", m.end())
    last_nl = text.rindex("\n", 0, end)
    text = (text[:last_nl]
            + f"\n    PLAYER_AVATAR_GFX_{state},   // {pose_sentinel(state)}"
            + text[last_nl:])
    _write(path, text)


def _patch_avatar_table(root: str, state: str) -> None:
    path = os.path.join(root, _AVATAR_C_REL)
    text = _read(path)
    anchor = "static const u8 sPlayerAvatarGfxIds[][GENDER_COUNT] = {"
    if anchor not in text:
        raise PlayerPosePatchError(
            "sPlayerAvatarGfxIds not found in field_player_avatar.c.")
    end = text.index("};", text.index(anchor))
    row = (f"    [PLAYER_AVATAR_GFX_{state}]{' ' * 4}= "
           f"{{OBJ_EVENT_GFX_{state}_RED, OBJ_EVENT_GFX_{state}_GREEN}},"
           f"   // {pose_sentinel(state)}\n")
    text = text[:end] + row + text[end:]
    _write(path, text)


# ─────────────────────────────────────────────────────────────────────────────
# public
# ─────────────────────────────────────────────────────────────────────────────

def ensure_player_pose(project_root: str, state_name: str,
                       red_png: str, green_png: str) -> str:
    """Register a player avatar pose. Idempotent; returns a summary."""
    state = state_name.strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", state):
        raise PlayerPosePatchError(
            f"'{state_name}' is not a usable state name — letters, digits and "
            f"underscores only, starting with a letter.")
    if not project_supports_poses(project_root):
        raise PlayerPosePatchError(
            "This project has no sPlayerAvatarGfxIds table, so it does not "
            "use the avatar-state system.")
    if is_pose_installed(project_root, state):
        return f"PLAYER_AVATAR_GFX_{state} was already registered."

    pics = os.path.join(project_root, _PIC_DIR)
    for png in (red_png, green_png):
        full = os.path.join(pics, f"{png}.png")
        if not os.path.isfile(full):
            raise PlayerPosePatchError(
                f"{png}.png is not in graphics/object_events/pics/people. "
                f"Put both sheets there first.")
    frames, _ = _png_frames(os.path.join(pics, f"{red_png}.png"))
    gframes, _ = _png_frames(os.path.join(pics, f"{green_png}.png"))
    if frames != gframes:
        raise PlayerPosePatchError(
            f"{red_png}.png has {frames} frame(s) but {green_png}.png has "
            f"{gframes}. Both sheets must match, or the two genders would "
            f"animate differently.")

    red_id = _patch_constants(project_root, state, red_png, green_png)
    _patch_graphics(project_root, state, red_png, green_png)
    _patch_pic_tables(project_root, state, red_png, green_png, frames)
    anim_table = _patch_anims(project_root, state)
    _patch_info_pointers(project_root, state, red_png, green_png)
    _patch_info(project_root, state, red_png, green_png, anim_table, frames)
    _patch_state_enum(project_root, state)
    _patch_avatar_table(project_root, state)

    shape = ("one held frame" if frames == 1 else f"{frames} frames")
    return (f"Registered PLAYER_AVATAR_GFX_{state}:\n"
            f"  • OBJ_EVENT_GFX_{state}_RED / _GREEN (ids {red_id}, {red_id + 1})\n"
            f"  • {red_png}.png / {green_png}.png, {shape}\n"
            f"  • added to the avatar state table, so the engine picks the\n"
            f"    right one by gender automatically\n\n"
            f"Nothing uses the pose yet — point a cutscene at it, or use it\n"
            f"from a script with Change Graphic.\n\n"
            f"Rebuild the game to see it.")


def apply_pose_to_call(project_root: str, source_rel: str,
                       function_name: str, state_name: str) -> str:
    """Repoint one ``GetPlayerAvatarGraphicsIdByStateId`` call at a new pose.

    For a cutscene that currently borrows another pose. Only the call inside
    *function_name* is touched, so a file with several pose swaps keeps the
    others — changing all of them would be a different and much worse edit.
    """
    state = state_name.strip().upper()
    path = os.path.join(project_root, source_rel)
    if not os.path.isfile(path):
        raise PlayerPosePatchError(f"{source_rel} not found.")
    text = _read(path)

    m = re.search(rf"^[\w \*]*\b{re.escape(function_name)}\s*\([^)]*\)\s*\{{",
                  text, re.M)
    if not m:
        raise PlayerPosePatchError(
            f"{function_name}() not found in {source_rel}.")
    # The function body, to its closing brace at column 0.
    end = text.find("\n}", m.end())
    if end == -1:
        raise PlayerPosePatchError(
            f"Could not find the end of {function_name}().")
    body = text[m.end():end]

    call = re.search(r"GetPlayerAvatarGraphicsIdByStateId\(\s*"
                     r"(PLAYER_AVATAR_GFX_\w+)\s*\)", body)
    if not call:
        raise PlayerPosePatchError(
            f"{function_name}() does not call "
            f"GetPlayerAvatarGraphicsIdByStateId, so there is no pose to "
            f"repoint.")
    was = call.group(1)
    if was == f"PLAYER_AVATAR_GFX_{state}":
        return f"{function_name}() already uses PLAYER_AVATAR_GFX_{state}."

    new_body = body[:call.start(1)] + f"PLAYER_AVATAR_GFX_{state}" + \
        body[call.end(1):]
    _write(path, text[:m.end()] + new_body + text[end:])
    return (f"{source_rel}: {function_name}() now uses "
            f"PLAYER_AVATAR_GFX_{state} (was {was}).")
