"""Upgrade overworld sprites to use their unused 10th frame as an emote
pose (also reachable by the VS-seeker animation dispatch).

Background
==========

Every standard NPC sprite that ships with pokefirered has a 10-frame PNG
on disk (e.g. 160×32 for a 16×32 sprite — frames 0..9).  The 9 walk-cycle
frames are referenced by `sAnimTable_Standard`; frame 9 is on disk and
exposed by the per-sprite `sPicTable_*` array, but NO standard anim
command references it — so it just sits there taking up ROM space and
doing nothing.

This module wires that 10th frame as a usable `ANIM_EMOTE` animation
state.  Same slot doubles as the fallback target for VS-seeker dispatch,
so a sprite that's been "upgraded" works for both purposes.

Detection is conservative and project-agnostic:

  - Survey every `gObjectEventGraphicsInfo_*` entry the project actually
    declares (no hardcoded names; works on stock vanilla, your fork, or
    any other pokefirered fork).
  - Compute `frames_on_disk` from the sprite's PNG dimensions and its
    declared frame size (`.width`, `.height`).  No assumption of 16×32 —
    a 32×32 NPC fork gets the same treatment.
  - Compute `frames_used_by_anims` by parsing every `ANIMCMD_FRAME(N, ...)`
    in the sprite's current `.anims` table.
  - Compute `frames_in_pic_table` by parsing the sprite's `sPicTable_*[]`.
  - Flag the sprite as a CANDIDATE when there are frames on disk that
    NO anim command currently references AND the project is using a
    `Standard`-style anim table that could be cleanly swapped.

The upgrade operation is THREE patcher edits, idempotent and reversible:

  1. Add the `ANIM_EMOTE` constant + the shared `sAnimTable_StandardWithEmote`
     + the supporting `sAnim_FrameNineEmote` if any of those aren't already
     present.  These are written ONCE per project; subsequent calls are
     no-ops.
  2. Extend the sprite's `sPicTable_*[]` to cover frame 9 if it doesn't
     already.  Most vanilla NPCs already cover 0..9 — the extension only
     fires on projects whose pic tables were trimmed to 9 entries.
  3. Rewrite the sprite's `.anims` field in `object_event_graphics_info.h`
     from its current Standard-family table to `sAnimTable_StandardWithEmote`.

Garbage-free contract
=====================

This module NEVER:
  - Writes temp/backup files (.bak, .tmp) outside the atomic-replace pattern.
  - Creates duplicate constants or anim tables.
  - Touches sprites that don't fit the candidate criteria.

Each public function returns `(success, applied_messages, error_messages)`
matching the convention used elsewhere in `core/overworld_*`.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


# ────────────────────────────── data model ────────────────────────────────


@dataclass
class EmoteCandidate:
    """One sprite that's a candidate for the emote upgrade."""
    info_name: str           # PascalCase, e.g. "Boy"
    pic_table_name: str      # e.g. "sPicTable_Boy"
    current_anim_table: str  # e.g. "sAnimTable_Standard"
    png_path: str            # absolute path on disk
    frame_w: int             # from GraphicsInfo .width
    frame_h: int             # from GraphicsInfo .height
    frames_on_disk: int      # total frames in PNG
    frames_used: int         # highest index referenced by current anim table + 1
    frames_in_pic_table: int # number of entries in sPicTable_*
    extra_frames: int        # frames_on_disk - frames_used

    @property
    def needs_pic_table_extension(self) -> bool:
        return self.frames_in_pic_table < self.frames_on_disk


@dataclass
class ScanReport:
    candidates: List[EmoteCandidate]
    already_upgraded: int     # already wired to sAnimTable_StandardWithEmote
    skipped_no_extra: int     # PNG doesn't have unused frames
    skipped_custom_anim: int  # uses a custom non-Standard anim table — leave alone
    skipped_missing_png: int  # GraphicsInfo says width=N but PNG isn't on disk

    def summary(self) -> str:
        return (
            f"{len(self.candidates)} candidate(s), "
            f"{self.already_upgraded} already upgraded, "
            f"{self.skipped_no_extra} have no unused frames, "
            f"{self.skipped_custom_anim} skipped (custom anim table), "
            f"{self.skipped_missing_png} skipped (missing PNG)"
        )


# Constants and identifiers we generate / look for.  Centralised so a
# future rename only happens in one place.
ANIM_EMOTE_CONSTANT = "ANIM_EMOTE"
ANIM_EMOTE_DEFINE_BODY = "(ANIM_STD_COUNT + 0)"
EMOTE_ANIM_NAME = "sAnim_FrameNineEmote"
EMOTE_TABLE_NAME = "sAnimTable_StandardWithEmote"

# Anim tables we recognise as "Standard family" — i.e. safe to swap to
# `sAnimTable_StandardWithEmote` without losing distinct anim states.
# Other tables (Inanimate, RedGreenFieldMove, RedGreenVSSeeker, etc.) get
# left alone because their existing slots are meaningful and a generic
# swap would break them.
STANDARD_FAMILY_ANIM_TABLES = {
    "sAnimTable_Standard",
}


# ────────────────────────────── parsing helpers ─────────────────────────────


def _read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def _atomic_write_text(path: str, text: str) -> bool:
    """Write text atomically; clean up the .tmp on any failure."""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
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
        return True
    except OSError:
        return False


def _parse_pic_table_lengths(root: str) -> Dict[str, int]:
    """Parse `object_event_pic_tables.h` and return
    `{pic_table_name: number_of_entries}` for every table found.
    """
    path = os.path.join(
        root, "src", "data", "object_events", "object_event_pic_tables.h"
    )
    if not os.path.isfile(path):
        return {}
    text = _read(path)
    out: Dict[str, int] = {}
    # Each table: `static const struct SpriteFrameImage sPicTable_X[] = { ... };`
    pat = re.compile(
        r"sPicTable_(\w+)\s*\[\]\s*=\s*\{([^}]*)\}",
        re.DOTALL,
    )
    for m in pat.finditer(text):
        name = "sPicTable_" + m.group(1)
        body = m.group(2)
        # Count entries by counting `overworld_frame(` occurrences.
        count = body.count("overworld_frame")
        out[name] = count
    return out


def _parse_pic_table_symbol(root: str) -> Dict[str, str]:
    """Parse `object_event_pic_tables.h` and return
    `{pic_table_name: pic_symbol_name}` (e.g.
    `sPicTable_Boy -> gObjectEventPic_Boy`).
    """
    path = os.path.join(
        root, "src", "data", "object_events", "object_event_pic_tables.h"
    )
    if not os.path.isfile(path):
        return {}
    text = _read(path)
    out: Dict[str, str] = {}
    pat = re.compile(
        r"sPicTable_(\w+)\s*\[\]\s*=\s*\{([^}]*)\}",
        re.DOTALL,
    )
    for m in pat.finditer(text):
        body = m.group(2)
        sym_match = re.search(r"overworld_frame\((gObjectEventPic_\w+)", body)
        if sym_match:
            out["sPicTable_" + m.group(1)] = sym_match.group(1)
    return out


def _parse_anim_max_frame(root: str) -> Dict[str, int]:
    """Parse `object_event_anims.h` and return
    `{anim_table_name: highest_frame_index_used}` for every anim TABLE
    found.  Walks every `sAnim_*` that the table references and looks
    inside its `ANIMCMD_FRAME(N, ...)` entries.  This is what tells us
    whether a sprite's current animations already cover frame 9.
    """
    path = os.path.join(
        root, "src", "data", "object_events", "object_event_anims.h"
    )
    if not os.path.isfile(path):
        return {}
    text = _read(path)

    # First pass: map each sAnim_* to its highest frame index.
    anim_max: Dict[str, int] = {}
    pat = re.compile(
        r"sAnim_(\w+)\s*\[\]\s*=\s*\{([^}]*)\}",
        re.DOTALL,
    )
    for m in pat.finditer(text):
        name = "sAnim_" + m.group(1)
        body = m.group(2)
        frames = [int(x) for x in re.findall(r"ANIMCMD_FRAME\(\s*(\d+)", body)]
        anim_max[name] = max(frames) if frames else 0

    # Second pass: for each anim table, find every sAnim_* it references
    # and take the max of those frame counts.
    table_max: Dict[str, int] = {}
    table_pat = re.compile(
        r"sAnimTable_(\w+)\s*\[\]\s*=\s*\{([^}]*)\}",
        re.DOTALL,
    )
    for m in table_pat.finditer(text):
        name = "sAnimTable_" + m.group(1)
        body = m.group(2)
        anim_refs = re.findall(r"sAnim_\w+", body)
        if not anim_refs:
            table_max[name] = 0
            continue
        table_max[name] = max(
            (anim_max.get(a, 0) for a in anim_refs), default=0,
        )
    return table_max


def _png_frame_count(png_path: str, frame_w: int, frame_h: int) -> int:
    """Return the number of frames the PNG holds at the declared frame
    size.  Returns 0 if PIL can't open the file or the dimensions don't
    cleanly divide.
    """
    if frame_w <= 0 or frame_h <= 0:
        return 0
    try:
        from PIL import Image
        img = Image.open(png_path)
        w, h = img.size
    except Exception:
        return 0
    if w % frame_w != 0 or h % frame_h != 0:
        return 0
    return (w // frame_w) * (h // frame_h)


# ────────────────────────────── public API ──────────────────────────────────


def scan_emote_candidates(
    root: str,
    *,
    info_parser=None,
    sprite_png_resolver=None,
) -> ScanReport:
    """Walk every overworld sprite and decide whether it's a candidate
    for the emote upgrade.

    `info_parser` and `sprite_png_resolver` are optional callables for
    testability.  In normal operation they're inferred from the
    overworld-graphics-tab parsing functions already in PorySuite.
    """
    # Lazy import to avoid pulling Qt into core code paths.
    from ui.overworld_graphics_tab import (
        _parse_graphics_info as _default_info_parser,
        _parse_pic_table_to_symbol as _default_pic_to_sym,
        _parse_pic_symbol_to_path as _default_sym_to_path,
        _resolve_sprite_png as _default_resolver,
        _parse_pic_tables as _default_gfx_to_info,
        _find_sprite_pngs as _default_find_slugs,
    )

    info_data = (info_parser or _default_info_parser)(root)
    pic_table_to_sym = _default_pic_to_sym(root)
    pic_sym_to_path = _default_sym_to_path(root)
    slug_to_png = _default_find_slugs(root)
    gfx_to_info = _default_gfx_to_info(root)

    pic_table_lengths = _parse_pic_table_lengths(root)
    anim_max_frames = _parse_anim_max_frame(root)

    candidates: List[EmoteCandidate] = []
    already = 0
    no_extra = 0
    custom = 0
    missing_png = 0

    for _gfx_const, info_name in gfx_to_info.items():
        info = info_data.get(info_name, {})
        if not info:
            continue
        anim_table = info.get("anims", "")
        if anim_table == EMOTE_TABLE_NAME:
            already += 1
            continue
        if anim_table not in STANDARD_FAMILY_ANIM_TABLES:
            # Custom anim tables (Inanimate, RedGreenFieldMove, etc.) —
            # leave alone, the swap would lose state.
            custom += 1
            continue

        # Resolve PNG path.
        png_info = (sprite_png_resolver or _default_resolver)(
            info_name, info_data, pic_table_to_sym, pic_sym_to_path,
            slug_to_png, root,
        )
        if not png_info:
            missing_png += 1
            continue
        png_path = png_info[0]
        if not os.path.isfile(png_path):
            missing_png += 1
            continue

        frame_w = int(info.get("width", 16))
        frame_h = int(info.get("height", 32))
        frames_on_disk = _png_frame_count(png_path, frame_w, frame_h)
        if frames_on_disk <= 0:
            missing_png += 1
            continue

        # How many frames does the current anim table actually touch?
        # +1 because anim_max_frames stores the highest index, not the
        # count.  If the table doesn't appear (e.g. project deleted it),
        # treat as 0 so the candidate logic flags this sprite for review.
        max_idx = anim_max_frames.get(anim_table, -1)
        frames_used = max_idx + 1 if max_idx >= 0 else 0

        if frames_on_disk <= frames_used:
            no_extra += 1
            continue

        pic_table_name = info.get("images", "")
        pic_table_entries = pic_table_lengths.get(pic_table_name, 0)

        candidates.append(EmoteCandidate(
            info_name=info_name,
            pic_table_name=pic_table_name,
            current_anim_table=anim_table,
            png_path=png_path,
            frame_w=frame_w,
            frame_h=frame_h,
            frames_on_disk=frames_on_disk,
            frames_used=frames_used,
            frames_in_pic_table=pic_table_entries,
            extra_frames=frames_on_disk - frames_used,
        ))

    return ScanReport(
        candidates=candidates,
        already_upgraded=already,
        skipped_no_extra=no_extra,
        skipped_custom_anim=custom,
        skipped_missing_png=missing_png,
    )


def ensure_emote_anim_table(root: str) -> Tuple[bool, List[str]]:
    """Idempotent: write the `ANIM_EMOTE` constant + `sAnim_FrameNineEmote`
    + `sAnimTable_StandardWithEmote` into the project source if they
    don't already exist.

    Returns `(success, applied_messages)`.  On a project that already
    has everything wired up, returns `(True, [])` — no edits made.
    """
    applied: List[str] = []

    # Step 1: ANIM_EMOTE constant
    const_path = os.path.join(
        root, "include", "constants", "event_object_movement.h"
    )
    if not os.path.isfile(const_path):
        return False, [f"missing {const_path}"]
    text = _read(const_path)
    if f"#define {ANIM_EMOTE_CONSTANT}" not in text:
        # Insert after ANIM_NURSE_BOW if we can find it, otherwise after
        # ANIM_STD_COUNT so the file still parses.
        anchor_pat = re.compile(
            r"#define\s+ANIM_NURSE_BOW\s+\([^)]+\)",
        )
        m = anchor_pat.search(text)
        if m:
            insert_at = m.end()
            new_define = (
                f"\n#define {ANIM_EMOTE_CONSTANT:<32s} "
                f"{ANIM_EMOTE_DEFINE_BODY}  "
                f"// PorySuite-Z: shared emote / VS-seeker slot"
            )
            text = text[:insert_at] + new_define + text[insert_at:]
            if not _atomic_write_text(const_path, text):
                return False, [f"failed to write {const_path}"]
            applied.append(
                f"Added #define {ANIM_EMOTE_CONSTANT} "
                f"{ANIM_EMOTE_DEFINE_BODY} to event_object_movement.h"
            )
        else:
            return False, [
                "Could not find ANIM_NURSE_BOW in "
                "event_object_movement.h — anim-constant section may "
                "be unexpectedly shaped"
            ]

    # Step 2: sAnim_FrameNineEmote
    anims_path = os.path.join(
        root, "src", "data", "object_events", "object_event_anims.h"
    )
    if not os.path.isfile(anims_path):
        return False, [f"missing {anims_path}"]
    anims_text = _read(anims_path)
    if EMOTE_ANIM_NAME not in anims_text:
        # Append the new anim definition AND the new table at the end of
        # the file.  Idempotent: re-running won't duplicate because of
        # the substring guards above and below.
        block = _generate_anim_and_table_block()
        anims_text = anims_text.rstrip() + "\n\n" + block + "\n"
        if not _atomic_write_text(anims_path, anims_text):
            return False, [f"failed to write {anims_path}"]
        applied.append(
            f"Added {EMOTE_ANIM_NAME} + {EMOTE_TABLE_NAME} to "
            f"object_event_anims.h"
        )
    elif EMOTE_TABLE_NAME not in anims_text:
        # Edge case: anim defined but table missing.  Append just the
        # table.
        table_block = _generate_table_block()
        anims_text = anims_text.rstrip() + "\n\n" + table_block + "\n"
        if not _atomic_write_text(anims_path, anims_text):
            return False, [f"failed to write {anims_path}"]
        applied.append(
            f"Added {EMOTE_TABLE_NAME} to object_event_anims.h"
        )

    return True, applied


def _generate_anim_and_table_block() -> str:
    """Return the C-source block that defines both the anim and the
    table.  Frame index 9 = 10th frame (the one every vanilla NPC
    already has on disk).  Duration 32 ticks ≈ half a second.
    """
    return (
        f"// PorySuite-Z: emote pose using the 10th frame every standard\n"
        f"// NPC PNG already carries on disk.  Same slot doubles as the\n"
        f"// fallback target for VS-seeker dispatch.\n"
        f"static const union AnimCmd {EMOTE_ANIM_NAME}[] = {{\n"
        f"    ANIMCMD_FRAME(9, 32),\n"
        f"    ANIMCMD_END,\n"
        f"}};\n"
        f"\n"
        f"{_generate_table_block()}"
    )


def _generate_table_block() -> str:
    """Return only the anim-table definition.  Copies all 20 standard
    slots verbatim (so behaviour matches `sAnimTable_Standard` for the
    walk cycle) and adds the emote slot at the end.
    """
    return (
        f"static const union AnimCmd *const {EMOTE_TABLE_NAME}[] = {{\n"
        f"    [ANIM_STD_FACE_SOUTH] = sAnim_FaceSouth,\n"
        f"    [ANIM_STD_FACE_NORTH] = sAnim_FaceNorth,\n"
        f"    [ANIM_STD_FACE_WEST]  = sAnim_FaceWest,\n"
        f"    [ANIM_STD_FACE_EAST]  = sAnim_FaceEast,\n"
        f"    [ANIM_STD_GO_SOUTH] = sAnim_GoSouth,\n"
        f"    [ANIM_STD_GO_NORTH] = sAnim_GoNorth,\n"
        f"    [ANIM_STD_GO_WEST]  = sAnim_GoWest,\n"
        f"    [ANIM_STD_GO_EAST]  = sAnim_GoEast,\n"
        f"    [ANIM_STD_GO_FAST_SOUTH] = sAnim_GoFastSouth,\n"
        f"    [ANIM_STD_GO_FAST_NORTH] = sAnim_GoFastNorth,\n"
        f"    [ANIM_STD_GO_FAST_WEST]  = sAnim_GoFastWest,\n"
        f"    [ANIM_STD_GO_FAST_EAST]  = sAnim_GoFastEast,\n"
        f"    [ANIM_STD_GO_FASTER_SOUTH] = sAnim_GoFasterSouth,\n"
        f"    [ANIM_STD_GO_FASTER_NORTH] = sAnim_GoFasterNorth,\n"
        f"    [ANIM_STD_GO_FASTER_WEST]  = sAnim_GoFasterWest,\n"
        f"    [ANIM_STD_GO_FASTER_EAST]  = sAnim_GoFasterEast,\n"
        f"    [ANIM_STD_GO_FASTEST_SOUTH] = sAnim_GoFastestSouth,\n"
        f"    [ANIM_STD_GO_FASTEST_NORTH] = sAnim_GoFastestNorth,\n"
        f"    [ANIM_STD_GO_FASTEST_WEST]  = sAnim_GoFastestWest,\n"
        f"    [ANIM_STD_GO_FASTEST_EAST]  = sAnim_GoFastestEast,\n"
        f"    [{ANIM_EMOTE_CONSTANT}] = {EMOTE_ANIM_NAME},\n"
        f"}};"
    )


def upgrade_sprite_to_emote(
    root: str, info_name: str,
) -> Tuple[bool, List[str], List[str]]:
    """Wire `info_name`'s 10th frame as its emote / VS-seeker pose.

    Three patcher edits, atomic per file, idempotent across runs:
      1. Make sure ANIM_EMOTE + the shared anim table exist.
      2. Extend `sPicTable_<X>` if it currently has fewer entries than
         the sprite's PNG provides on disk.
      3. Rewrite the sprite's `.anims` in `object_event_graphics_info.h`
         to point at `sAnimTable_StandardWithEmote`.

    Returns `(success, applied_messages, error_messages)`.
    """
    applied: List[str] = []
    errors: List[str] = []

    # Step 1: shared infrastructure.
    ok, msgs = ensure_emote_anim_table(root)
    if not ok:
        errors.extend(msgs)
        return False, applied, errors
    applied.extend(msgs)

    # Look up the sprite's current pic table + dimensions.  We re-parse
    # so the function is safe to call standalone (not just from a
    # post-scan loop).
    from ui.overworld_graphics_tab import (
        _parse_graphics_info, _parse_pic_table_to_symbol,
        _parse_pic_symbol_to_path, _resolve_sprite_png,
        _find_sprite_pngs, _parse_pic_tables,
    )
    info_data = _parse_graphics_info(root)
    info = info_data.get(info_name, {})
    if not info:
        errors.append(
            f"No GraphicsInfo entry found for '{info_name}' — "
            f"check the spelling and that "
            f"`gObjectEventGraphicsInfo_{info_name}` exists in "
            f"object_event_graphics_info.h"
        )
        return False, applied, errors

    pic_table_name = info.get("images", "")
    frame_w = int(info.get("width", 16))
    frame_h = int(info.get("height", 32))

    # Step 2: extend pic table if needed.
    pic_table_lengths = _parse_pic_table_lengths(root)
    pic_table_to_sym = _parse_pic_table_to_symbol(root)
    pic_sym_to_path = _parse_pic_symbol_to_path(root)
    slug_to_png = _find_sprite_pngs(root)
    gfx_to_info = _parse_pic_tables(root)
    png_info = _resolve_sprite_png(
        info_name, info_data, pic_table_to_sym, pic_sym_to_path,
        slug_to_png, root,
    )
    if png_info and os.path.isfile(png_info[0]):
        frames_on_disk = _png_frame_count(png_info[0], frame_w, frame_h)
        current_entries = pic_table_lengths.get(pic_table_name, 0)
        if current_entries > 0 and frames_on_disk > current_entries:
            symbol = pic_table_to_sym.get(pic_table_name, "")
            ok2, msg2 = _extend_pic_table(
                root, pic_table_name, symbol, frame_w, frame_h,
                current_entries, frames_on_disk,
            )
            if not ok2:
                errors.append(msg2)
                return False, applied, errors
            applied.append(msg2)

    # Step 3: rewrite .anims field.
    ok3, msg3 = _rewrite_sprite_anims(root, info_name, EMOTE_TABLE_NAME)
    if not ok3:
        errors.append(msg3)
        return False, applied, errors
    applied.append(msg3)

    return True, applied, errors


def _extend_pic_table(
    root: str,
    table_name: str,
    pic_symbol: str,
    frame_w_px: int,
    frame_h_px: int,
    current_entries: int,
    needed_entries: int,
) -> Tuple[bool, str]:
    """Add `overworld_frame(...)` entries to a pic table so it covers
    up to `needed_entries` frames.  Returns (success, message).
    """
    if not pic_symbol:
        return False, f"missing pic symbol for {table_name}"
    if needed_entries <= current_entries:
        return True, f"{table_name} already covers {current_entries} frames"

    path = os.path.join(
        root, "src", "data", "object_events", "object_event_pic_tables.h"
    )
    if not os.path.isfile(path):
        return False, f"missing {path}"
    text = _read(path)

    # Find the table and its closing `};`
    pat = re.compile(
        r"(" + re.escape(table_name) + r"\s*\[\]\s*=\s*\{)([^}]*)(\};)",
        re.DOTALL,
    )
    m = pat.search(text)
    if not m:
        return False, f"could not locate {table_name} in pic tables"

    tile_w = max(1, frame_w_px // 8)
    tile_h = max(1, frame_h_px // 8)
    new_lines = []
    for i in range(current_entries, needed_entries):
        new_lines.append(
            f"    overworld_frame({pic_symbol}, {tile_w}, {tile_h}, {i}),"
        )
    addition = "\n" + "\n".join(new_lines) + "\n"
    body = m.group(2).rstrip() + addition
    new_table = m.group(1) + body + m.group(3)
    text = text[:m.start()] + new_table + text[m.end():]
    if not _atomic_write_text(path, text):
        return False, f"failed to write {path}"
    return True, (
        f"Extended {table_name} from {current_entries} to "
        f"{needed_entries} frames"
    )


def _rewrite_sprite_anims(
    root: str, info_name: str, new_table_name: str,
) -> Tuple[bool, str]:
    """Rewrite `gObjectEventGraphicsInfo_<info_name>.anims` to
    `new_table_name`.  Returns (success, message)."""
    path = os.path.join(
        root, "src", "data", "object_events", "object_event_graphics_info.h"
    )
    if not os.path.isfile(path):
        return False, f"missing {path}"
    text = _read(path)
    pat = re.compile(
        r"(gObjectEventGraphicsInfo_" + re.escape(info_name)
        + r"\s*=\s*\{[^;]*?\.anims\s*=\s*)"
        + r"(\w+)"
        + r"(,)",
        re.DOTALL,
    )
    new_text, n = pat.subn(r"\g<1>" + new_table_name + r"\3", text)
    if n == 0:
        return False, (
            f"could not find .anims field for {info_name} — "
            f"may be using a different field style or already upgraded"
        )
    if not _atomic_write_text(path, new_text):
        return False, f"failed to write {path}"
    return True, (
        f"Rewrote gObjectEventGraphicsInfo_{info_name}.anims → "
        f"{new_table_name}"
    )
