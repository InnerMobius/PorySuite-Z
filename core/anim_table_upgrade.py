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


# Friendly display names for the well-known object-event anim tables;
# any table not listed gets a name derived from its symbol.  The generic
# Loop<N>Sequential / Loop<N>Random entries are added lazily below once
# GENERIC_LOOP_FRAME_COUNTS is defined.
_ANIM_TABLE_FRIENDLY = {
    "sAnimTable_Standard": "Walk Cycle (standard NPC)",
    "sAnimTable_Inanimate": "Static / Inanimate",
    "sAnimTable_RedGreenNormal": "Walk Cycle (Player-style)",
    "sAnimTable_StandardWithEmote": "Walk Cycle + Emote",
}


def _prettify_anim_symbol(sym: str) -> str:
    """A human-readable name from an ``sAnimTable_*`` symbol."""
    base = sym[len("sAnimTable_"):] if sym.startswith("sAnimTable_") else sym
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", base)
    return spaced or sym


# Pattern catching per-sprite cycle tables (sAnimTable_<InfoName>Cycle and
# sAnimTable_<InfoName>RandomCycle) so the scanner can route them through a
# distinct label/sort path — they are visually grouped UNDER their parent
# sprite, not next to the project-wide presets.
_PER_SPRITE_CYCLE_RE = re.compile(
    r"^sAnimTable_(?P<info>\w+?)(?P<kind>RandomCycle|Cycle)$"
)

# Pattern catching the project-wide generic loop tables that
# ensure_generic_loop_tables installs.  Splitting these out lets
# scan_anim_tables present them as a single block in the dropdown with a
# consistent label.
_GENERIC_LOOP_RE = re.compile(
    r"^sAnimTable_Loop(?P<n>\d+)(?P<order>Sequential|Random)$"
)


def _classify_anim_table(sym: str) -> Tuple[int, str, str]:
    """Bucket a table symbol for sorting + labeling.

    Returns ``(group_rank, sort_within_group, friendly_label_or_empty)``.

    Group ranks (lower sorts higher in the dropdown):
      0  Walk Cycle (standard NPC)
      1  Walk Cycle + Emote
      2  Generic loops (project-wide presets)
      3  Static / Inanimate
      4  Per-sprite cycle tables (sAnimTable_<X>Cycle / <X>RandomCycle)
      5  Everything else (alphabetic)
    """
    if sym == "sAnimTable_Standard":
        return (0, "", "")
    if sym == "sAnimTable_StandardWithEmote":
        return (1, "", "")
    m = _GENERIC_LOOP_RE.match(sym)
    if m:
        n = int(m.group("n"))
        order_rank = 0 if m.group("order") == "Sequential" else 1
        # "02-0" sorts before "02-1" sorts before "03-0" sorts before "03-1"…
        return (2, f"{n:02d}-{order_rank}", "")
    if sym == "sAnimTable_Inanimate":
        return (3, "", "")
    m = _PER_SPRITE_CYCLE_RE.match(sym)
    if m:
        info = m.group("info")
        kind_rank = 0 if m.group("kind") == "Cycle" else 1
        return (4, f"{info.lower()}-{kind_rank}", "")
    return (5, sym, "")


def _label_for_anim_table(sym: str, frame_count: int) -> str:
    """Build the dropdown label for an anim table.  Generic loops, per-sprite
    cycles, and the well-known named tables all get tailored wording; anything
    else falls back to the prettified symbol.  ``frame_count`` is the highest
    ``ANIMCMD_FRAME`` index referenced + 1 (i.e. how many sheet frames the
    table touches)."""
    # Generic loop presets.
    m = _GENERIC_LOOP_RE.match(sym)
    if m:
        n = int(m.group("n"))
        order = ("sequential" if m.group("order") == "Sequential"
                 else "random pace")
        return f"Generic Loop · {n} frames · {order}"

    # Per-sprite cycle tables — surface the source sprite name so the user
    # knows the table is shaped specifically for that sprite (and that
    # assigning it to a DIFFERENT-sized sprite will render wrong).
    m = _PER_SPRITE_CYCLE_RE.match(sym)
    if m:
        info = m.group("info")
        kind = ("Random Cycle" if m.group("kind") == "RandomCycle"
                else "Sequential Cycle")
        return f"{info}'s {kind}  ·  {frame_count} frames"

    name = _ANIM_TABLE_FRIENDLY.get(sym) or _prettify_anim_symbol(sym)
    return f"{name}  ·  {frame_count} frame{'' if frame_count == 1 else 's'}"


def scan_anim_tables(root: str) -> List[Tuple[str, str]]:
    """Return ``[(symbol, label), ...]`` for every object-event animation
    table the project defines in ``object_event_anims.h``.

    ``label`` carries a frame-count hint where useful — the number of
    sprite-sheet frames the table's animations reference (highest
    ``ANIMCMD_FRAME`` index + 1).  Items are grouped so the dropdown reads
    naturally:

      1. Walk Cycle (standard NPC)
      2. Walk Cycle + Emote
      3. Generic Loop presets (project-wide), sorted by frame count
      4. Static / Inanimate
      5. Per-sprite cycle tables (named ``sAnimTable_<X>Cycle`` /
         ``<X>RandomCycle``), alphabetic by sprite name
      6. Everything else (alphabetic)

    Falls back to a minimal built-in pair when the anim header cannot be
    read.
    """
    table_max = _parse_anim_max_frame(root)
    if not table_max:
        return [
            ("sAnimTable_Standard",
             "Walk Cycle (standard NPC)  ·  9 frames"),
            ("sAnimTable_Inanimate",
             "Static / Inanimate  ·  1 frame"),
        ]

    def _key(sym: str) -> Tuple[int, str]:
        group, within, _label = _classify_anim_table(sym)
        return (group, within)

    out: List[Tuple[str, str]] = []
    for sym in sorted(table_max, key=_key):
        n = table_max[sym] + 1   # highest frame index -> frame count
        out.append((sym, _label_for_anim_table(sym, n)))
    return out


def _png_frame_count(png_path: str, frame_w: int, frame_h: int) -> int:
    """Return the number of frames the PNG holds at the declared frame
    size.  Returns 0 if the image can't be read or the dimensions don't
    cleanly divide.

    Uses Qt's QImage, NOT PIL.  PyQt6 is a hard dependency of PorySuite-Z
    (every sprite viewer is built on it); Pillow is NOT in requirements.txt.
    A PIL-based reader threw ImportError in any environment without Pillow
    installed and the `except` swallowed it as "0 frames" — silently
    breaking both this and the emote scan (every sprite resolved to 0
    frames).  QImage reads raster dimensions and needs no running
    QApplication.
    """
    if frame_w <= 0 or frame_h <= 0:
        return 0
    try:
        from PyQt6.QtGui import QImage
        img = QImage(png_path)
        if img.isNull():
            return 0
        w, h = img.width(), img.height()
    except Exception:
        return 0
    if w <= 0 or h <= 0 or w % frame_w != 0 or h % frame_h != 0:
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


def _rewrite_sprite_inanimate(
    root: str, info_name: str, inanimate: bool,
) -> Tuple[bool, str]:
    """Rewrite `gObjectEventGraphicsInfo_<info_name>.inanimate` to TRUE/FALSE.

    A frame-cycle entity MUST be non-inanimate: the object-event spawn path
    only calls `StartSpriteAnim` when `!graphicsInfo->inanimate`, and without
    that initial StartSpriteAnim the sprite never starts its loop.

    Returns (success, message).  Treated as a no-op success when the field
    already holds the requested value.
    """
    path = os.path.join(
        root, "src", "data", "object_events", "object_event_graphics_info.h"
    )
    if not os.path.isfile(path):
        return False, f"missing {path}"
    text = _read(path)
    want = "TRUE" if inanimate else "FALSE"
    pat = re.compile(
        r"(gObjectEventGraphicsInfo_" + re.escape(info_name)
        + r"\s*=\s*\{[^;]*?\.inanimate\s*=\s*)"
        + r"(\w+)"
        + r"(,)",
        re.DOTALL,
    )
    m = pat.search(text)
    if m is None:
        return False, (
            f"could not find .inanimate field for {info_name}"
        )
    if m.group(2) == want:
        return True, f"{info_name}.inanimate already {want}"
    new_text = text[:m.start(2)] + want + text[m.end(2):]
    if not _atomic_write_text(path, new_text):
        return False, f"failed to write {path}"
    return True, f"Rewrote gObjectEventGraphicsInfo_{info_name}.inanimate → {want}"


# ───────────────── sequential frame-cycle animation ─────────────────────────
#
# A "frame-cycle" entity is a stationary object event whose sprite cycles
# every sheet frame in sequence — a painting, a flickering torch, an idle
# animated decoration.  It is NOT direction-based, so it sidesteps the
# East-mirror problem entirely (the standard table fakes East by hFlipping
# the West frame; a frame-cycle table never hFlips anything).
#
# Mechanism (verified against pokefirered's engine, 2026-05-18):
#   - The cycle table points EVERY one of the 21 standard animation slots
#     at one shared multi-frame loop.  No slot is the mirrored-East form,
#     so the sprite can never hFlip no matter what faces it.
#   - The object event is placed in Porymap with MOVEMENT_TYPE_NONE.  That
#     maps to MovementType_None — an empty-callback movement type that
#     never calls FaceDirection (which would set sprite->animPaused = TRUE
#     and freeze the sprite, the way MOVEMENT_TYPE_FACE_DOWN does).
#   - At spawn, a non-inanimate object event runs
#     StartSpriteAnim(ANIM_STD_FACE_SOUTH) and animPaused stays FALSE, so
#     AnimateSprites free-runs the loop forever.
#   - Frame index 9 is always excluded — that slot is the VS-seeker / emote
#     pose every standard NPC PNG carries.

# The 21 animation slots sAnimTable_Standard fills.  A frame-cycle table
# points every one at the same loop.
_STANDARD_ANIM_SLOTS = (
    "ANIM_STD_FACE_SOUTH", "ANIM_STD_FACE_NORTH",
    "ANIM_STD_FACE_WEST", "ANIM_STD_FACE_EAST",
    "ANIM_STD_GO_SOUTH", "ANIM_STD_GO_NORTH",
    "ANIM_STD_GO_WEST", "ANIM_STD_GO_EAST",
    "ANIM_STD_GO_FAST_SOUTH", "ANIM_STD_GO_FAST_NORTH",
    "ANIM_STD_GO_FAST_WEST", "ANIM_STD_GO_FAST_EAST",
    "ANIM_STD_GO_FASTER_SOUTH", "ANIM_STD_GO_FASTER_NORTH",
    "ANIM_STD_GO_FASTER_WEST", "ANIM_STD_GO_FASTER_EAST",
    "ANIM_STD_GO_FASTEST_SOUTH", "ANIM_STD_GO_FASTEST_NORTH",
    "ANIM_STD_GO_FASTEST_WEST", "ANIM_STD_GO_FASTEST_EAST",
    "ANIM_RAISE_HAND",
)

# Frame index every frame-cycle skips — the VS-seeker / emote pose slot.
CYCLE_SKIP_FRAME_INDEX = 9

# ANIMCMD_FRAME's `duration` is a 6-bit bitfield in the engine — see
# include/sprite.h, `struct AnimFrameCmd { ... u32 duration:6; ... }`.  A
# value above 63 silently wraps modulo 64 (100 -> 36, 64 -> 0).  Every
# generated frame duration is clamped to this so the in-ROM timing is
# exactly what the tool intends.
_ANIMCMD_DURATION_MAX = 63


def _cycle_sentinels(info_name: str) -> Tuple[str, str]:
    """Open/close sentinel comments wrapping a generated cycle block, so a
    re-generation (duration change, frame-count change) replaces it cleanly
    instead of leaving a stale duplicate behind."""
    return (
        f"// >>> PorySuite-Z frame-cycle: {info_name} >>>",
        f"// <<< PorySuite-Z frame-cycle: {info_name} <<<",
    )


def cycle_table_symbol(info_name: str) -> str:
    """The anim-table symbol a frame-cycle for `info_name` is generated under."""
    return f"sAnimTable_{info_name}Cycle"


def _generate_cycle_block(
    info_name: str, frame_count: int, frame_duration: int,
) -> str:
    """Return the C source for `sAnim_<Name>Cycle` + `sAnimTable_<Name>Cycle`,
    wrapped in regeneration sentinels."""
    anim_name = f"sAnim_{info_name}Cycle"
    table_name = cycle_table_symbol(info_name)
    open_s, close_s = _cycle_sentinels(info_name)

    dur = max(1, min(_ANIMCMD_DURATION_MAX, frame_duration))
    frames = [i for i in range(frame_count) if i != CYCLE_SKIP_FRAME_INDEX]
    frame_lines = "\n".join(
        f"    ANIMCMD_FRAME({i}, {dur})," for i in frames
    )
    slot_lines = "\n".join(
        f"    [{slot}] = {anim_name}," for slot in _STANDARD_ANIM_SLOTS
    )
    return (
        f"{open_s}\n"
        f"// Sequential frame-cycle for {info_name} (PorySuite-Z generated).\n"
        f"// Loops every sheet frame except index {CYCLE_SKIP_FRAME_INDEX} "
        f"(VS-seeker / emote pose).\n"
        f"// Every slot points at the same loop: the sprite animates the\n"
        f"// same in all directions and NEVER hFlips.  Drive it with\n"
        f"// MOVEMENT_TYPE_FRAME_CYCLE on a non-inanimate object event.\n"
        f"static const union AnimCmd {anim_name}[] = {{\n"
        f"{frame_lines}\n"
        f"    ANIMCMD_JUMP(0),\n"
        f"}};\n"
        f"\n"
        f"static const union AnimCmd *const {table_name}[] = {{\n"
        f"{slot_lines}\n"
        f"}};\n"
        f"{close_s}"
    )


def ensure_cycle_anim_table(
    root: str, info_name: str, frame_count: int, frame_duration: int = 16,
) -> Tuple[bool, str, List[str]]:
    """Generate (or regenerate) a sequential frame-cycle anim table for
    `info_name` in `object_event_anims.h`.

    Idempotent by sentinel: a pre-existing cycle block for this `info_name`
    is replaced wholesale, so re-running with a different duration or frame
    count simply updates it — no stale duplicates.

    Returns `(success, table_symbol, messages)`.
    """
    msgs: List[str] = []
    table_name = cycle_table_symbol(info_name)

    if frame_count < 2:
        return False, table_name, [
            f"{info_name}: a frame-cycle needs at least 2 frames "
            f"(sheet resolves to {frame_count})"
        ]

    anims_path = os.path.join(
        root, "src", "data", "object_events", "object_event_anims.h"
    )
    if not os.path.isfile(anims_path):
        return False, table_name, [f"missing {anims_path}"]

    text = _read(anims_path)
    block = _generate_cycle_block(info_name, frame_count, frame_duration)
    open_s, close_s = _cycle_sentinels(info_name)

    existing = re.compile(
        re.escape(open_s) + r".*?" + re.escape(close_s), re.DOTALL,
    )
    if existing.search(text):
        # lambda replacement — block may contain chars re would treat as
        # group backreferences if passed as a plain repl string.
        text = existing.sub(lambda _m: block, text, count=1)
        msgs.append(f"Updated {table_name} in object_event_anims.h")
    else:
        text = text.rstrip() + "\n\n" + block + "\n"
        msgs.append(f"Added {table_name} to object_event_anims.h")

    if not _atomic_write_text(anims_path, text):
        return False, table_name, [f"failed to write {anims_path}"]

    return True, table_name, msgs


# ──────────────────── random frame-cycle ────────────────────────────────────
#
# Same idea as the sequential frame-cycle, but the loop visits the sheet
# frames in a shuffled order instead of 0->N.  The GBA ANIMCMD interpreter has
# no "pick a random frame" opcode, so true per-frame RNG would need engine
# code AND would stutter whenever the RNG rolled the same frame twice in a
# row.  Instead the table bakes a long pre-shuffled sequence (no two
# consecutive frames alike); over its 64-frame period it reads as random, and
# it is pure data — the existing MOVEMENT_TYPE_FRAME_CYCLE drives it unchanged.
#
# Each frame in that sequence is also held a *random* number of ticks within
# a caller-supplied [min, max] range — so the cycle varies its pace (some
# frames flick by, some linger) the way the vanilla LOOK_AROUND / WANDER idle
# movement types do, instead of metronome-stepping at one fixed speed.
#
# Both the shuffled order and the per-frame durations are seeded
# deterministically from the sprite name, so regenerating the same sprite
# yields the same table (idempotent) while two different sprites differ.

RANDOM_CYCLE_SEQUENCE_LENGTH = 64


def random_cycle_table_symbol(info_name: str) -> str:
    """The anim-table symbol a RANDOM frame-cycle for `info_name` uses."""
    return f"sAnimTable_{info_name}RandomCycle"


def _generate_random_cycle_block(
    info_name: str, frame_count: int, hold_min: int, hold_max: int,
    seq_len: int = RANDOM_CYCLE_SEQUENCE_LENGTH,
) -> str:
    """Return the C source for `sAnim_<Name>RandomCycle` +
    `sAnimTable_<Name>RandomCycle`, wrapped in the shared frame-cycle
    sentinels (so it is mutually exclusive with the sequential block).

    Each frame in the shuffled sequence is held a random number of ticks in
    `[hold_min, hold_max]`, so the cycle varies its pace instead of flicking
    at one fixed speed.  Frame order AND durations are seeded deterministically
    from `info_name`.
    """
    import random as _random
    import zlib

    # Clamp into [1, 63] — ANIMCMD_FRAME's duration field is 6-bit, so a
    # larger value would silently wrap in the ROM.
    lo = max(1, min(_ANIMCMD_DURATION_MAX, hold_min, hold_max))
    hi = max(lo, min(_ANIMCMD_DURATION_MAX, max(hold_min, hold_max)))

    anim_name = f"sAnim_{info_name}RandomCycle"
    table_name = random_cycle_table_symbol(info_name)
    open_s, close_s = _cycle_sentinels(info_name)

    pool = [i for i in range(frame_count) if i != CYCLE_SKIP_FRAME_INDEX]
    rng = _random.Random(zlib.crc32(info_name.encode("utf-8")))
    seq: List[int] = []
    for _ in range(max(2, seq_len)):
        choices = [f for f in pool if not seq or f != seq[-1]]
        seq.append(rng.choice(choices))
    # Don't let the ANIMCMD_JUMP wrap show the same frame twice (last==first).
    if len(pool) > 1 and seq[-1] == seq[0]:
        alt = [f for f in pool
               if f != seq[0] and (len(seq) < 2 or f != seq[-2])]
        if alt:
            seq[-1] = rng.choice(alt)

    # Each frame gets its own random hold in [lo, hi] — varied pacing.
    frame_lines = "\n".join(
        f"    ANIMCMD_FRAME({i}, {rng.randint(lo, hi)})," for i in seq
    )
    slot_lines = "\n".join(
        f"    [{slot}] = {anim_name}," for slot in _STANDARD_ANIM_SLOTS
    )
    return (
        f"{open_s}\n"
        f"// Random frame-cycle for {info_name} (PorySuite-Z generated).\n"
        f"// A {len(seq)}-entry pre-shuffled sequence of every sheet frame\n"
        f"// except index {CYCLE_SKIP_FRAME_INDEX} (VS-seeker / emote pose),\n"
        f"// no two consecutive frames alike, each held a random {lo}-{hi}\n"
        f"// tick span, looped with ANIMCMD_JUMP(0).  Every slot points at\n"
        f"// the same loop: the sprite animates the same in all directions\n"
        f"// and NEVER hFlips.  Drive it with MOVEMENT_TYPE_FRAME_CYCLE on a\n"
        f"// non-inanimate object event.\n"
        f"static const union AnimCmd {anim_name}[] = {{\n"
        f"{frame_lines}\n"
        f"    ANIMCMD_JUMP(0),\n"
        f"}};\n"
        f"\n"
        f"static const union AnimCmd *const {table_name}[] = {{\n"
        f"{slot_lines}\n"
        f"}};\n"
        f"{close_s}"
    )


def ensure_random_cycle_anim_table(
    root: str, info_name: str, frame_count: int,
    hold_min: int = 24, hold_max: int = 120,
    seq_len: int = RANDOM_CYCLE_SEQUENCE_LENGTH,
) -> Tuple[bool, str, List[str]]:
    """Generate (or regenerate) a RANDOM-order frame-cycle anim table for
    `info_name` in `object_event_anims.h`.

    Each frame is held a random number of ticks in `[hold_min, hold_max]`, so
    the cycle varies its pace instead of stepping at one fixed speed.

    Shares the `_cycle_sentinels` block with the sequential
    `ensure_cycle_anim_table` — the two modes are mutually exclusive per
    sprite, so picking one replaces the other's block instead of leaving an
    orphaned table behind.  The caller must repoint the sprite's `.anims` to
    the returned symbol (the sequential and random tables have different
    names, so a stale `.anims` would dangle).

    Returns `(success, table_symbol, messages)`.
    """
    msgs: List[str] = []
    table_name = random_cycle_table_symbol(info_name)

    if frame_count < 2:
        return False, table_name, [
            f"{info_name}: a frame-cycle needs at least 2 frames "
            f"(sheet resolves to {frame_count})"
        ]

    anims_path = os.path.join(
        root, "src", "data", "object_events", "object_event_anims.h"
    )
    if not os.path.isfile(anims_path):
        return False, table_name, [f"missing {anims_path}"]

    text = _read(anims_path)
    block = _generate_random_cycle_block(
        info_name, frame_count, hold_min, hold_max, seq_len)
    open_s, close_s = _cycle_sentinels(info_name)

    existing = re.compile(
        re.escape(open_s) + r".*?" + re.escape(close_s), re.DOTALL,
    )
    if existing.search(text):
        text = existing.sub(lambda _m: block, text, count=1)
        msgs.append(f"Updated {table_name} in object_event_anims.h")
    else:
        text = text.rstrip() + "\n\n" + block + "\n"
        msgs.append(f"Added {table_name} to object_event_anims.h")

    if not _atomic_write_text(anims_path, text):
        return False, table_name, [f"failed to write {anims_path}"]

    return True, table_name, msgs


# ──────────────── generic loop tables (project-wide, reusable) ──────────────
#
# Unlike the per-sprite cycle tables generated by ensure_cycle_anim_table /
# ensure_random_cycle_anim_table, the GENERIC loop tables are a fixed set of
# project-wide presets any sprite can share.  They cover the common case of
# a sprite that has only a handful of frames and just needs to idle-cycle in
# place — flickering torches, a two-frame breathing NPC, background filler
# characters, decorative animated props, etc.
#
# Two ways to think about it:
#
#   - sAnimTable_<Name>Cycle / <Name>RandomCycle: per-sprite, sized to a
#     specific sprite's frame count.  Assigning sprite B's table to sprite A
#     produces wrong rendering if their frame counts differ.
#
#   - sAnimTable_Loop<N>Sequential / Loop<N>Random: project-wide presets, one
#     per frame count.  Any sprite whose first N frames make a complete loop
#     can opt in to the same table — no per-sprite generation needed, no
#     cross-sprite mismatch, dropdown reads "Generic Loop · 2 frames ·
#     sequential" so the user knows exactly what they're picking.
#
# Differences from the per-sprite cycle tables:
#   - NO frame index is skipped.  The per-sprite cycles skip frame 9 because
#     they're aimed at standard 10-frame NPC sheets where slot 9 is the
#     VS-seeker emote pose.  Generic loops target sprites that have N (< 10)
#     frames with no emote slot, so frames are dense from 0.
#   - One shared definition lives in the project source forever — installed
#     once via ensure_generic_loop_tables(root), idempotent on re-runs.
#   - Drive any sprite using a generic loop with MOVEMENT_TYPE_FRAME_CYCLE,
#     same as per-sprite cycles.  The engine support is identical.

# Frame counts the toolkit ships generic presets for.  2-8 covers the typical
# "small idle cycle" range; sprites with 9+ frames usually need the per-sprite
# generator (and its frame-9 skip) anyway.
GENERIC_LOOP_FRAME_COUNTS = (2, 3, 4, 5, 6, 7, 8)

# Default per-frame hold for the sequential preset (game ticks at ~60/sec).
# 16 ticks ≈ 0.27s per frame — reads as a comfortable idle pace.
_GENERIC_SEQ_DURATION = 16

# Random preset: each frame held a random hold in this range, so the cycle
# varies its pace instead of metronome-stepping.  Matches the per-sprite
# random-cycle defaults.
_GENERIC_RND_HOLD_MIN = 20   # ~0.33s
_GENERIC_RND_HOLD_MAX = 60   # ~1s
_GENERIC_RND_SEQ_LEN = 64

# Sentinel comments wrapping the whole generic-loop block so re-installs
# replace cleanly without leaving stale duplicates.
_GENERIC_LOOP_SENTINEL_OPEN = "// >>> PorySuite-Z generic loop tables >>>"
_GENERIC_LOOP_SENTINEL_CLOSE = "// <<< PorySuite-Z generic loop tables <<<"


def generic_loop_table_symbol(frame_count: int, randomized: bool) -> str:
    """Symbol name for the generic loop preset of ``frame_count`` frames in
    sequential (``randomized=False``) or random (``randomized=True``) order.

    Same naming scheme as the per-sprite cycle tables but with the literal
    ``Loop<N>`` instead of an ``<InfoName>`` — so the dropdown groups them
    together and a project-wide grep finds every site at once.
    """
    suffix = "Random" if randomized else "Sequential"
    return f"sAnimTable_Loop{frame_count}{suffix}"


def _generic_loop_anim_name(frame_count: int, randomized: bool) -> str:
    """The per-loop ``sAnim_*`` symbol referenced by the generic table."""
    suffix = "Random" if randomized else "Sequential"
    return f"sAnim_Loop{frame_count}{suffix}"


def _generate_generic_loop_block() -> str:
    """Return the full sentinel-fenced C source for every generic loop
    table (both orderings × every frame count in
    ``GENERIC_LOOP_FRAME_COUNTS``).

    Sequential variants: every frame held ``_GENERIC_SEQ_DURATION`` ticks.
    Random variants: a deterministic-but-pseudo-random pre-shuffled sequence
    (seeded from the frame count so a re-install is byte-stable), each frame
    held a random ``[_GENERIC_RND_HOLD_MIN, _GENERIC_RND_HOLD_MAX]`` ticks.
    """
    import random as _random
    import zlib

    parts: List[str] = [_GENERIC_LOOP_SENTINEL_OPEN]
    parts.append(
        "// Project-wide preset loops generated by PorySuite-Z.  Any sprite\n"
        "// whose first N frames make a complete idle cycle can assign its\n"
        "// .anims to one of these — no per-sprite table generation needed.\n"
        "// Every direction slot points at the same loop, the sprite never\n"
        "// hFlips, and NO frame index is skipped (these target sprites with\n"
        "// no VS-seeker emote pose).  Drive the sprite with\n"
        "// MOVEMENT_TYPE_FRAME_CYCLE on a non-inanimate object event."
    )

    # ── Sequential variants ─────────────────────────────────────────────
    for n in GENERIC_LOOP_FRAME_COUNTS:
        anim_name = _generic_loop_anim_name(n, randomized=False)
        table_name = generic_loop_table_symbol(n, randomized=False)
        frame_lines = "\n".join(
            f"    ANIMCMD_FRAME({i}, {_GENERIC_SEQ_DURATION}),"
            for i in range(n)
        )
        slot_lines = "\n".join(
            f"    [{slot}] = {anim_name}," for slot in _STANDARD_ANIM_SLOTS
        )
        parts.append(
            f"static const union AnimCmd {anim_name}[] = {{\n"
            f"{frame_lines}\n"
            f"    ANIMCMD_JUMP(0),\n"
            f"}};\n"
            f"static const union AnimCmd *const {table_name}[] = {{\n"
            f"{slot_lines}\n"
            f"}};"
        )

    # ── Random variants ─────────────────────────────────────────────────
    for n in GENERIC_LOOP_FRAME_COUNTS:
        anim_name = _generic_loop_anim_name(n, randomized=True)
        table_name = generic_loop_table_symbol(n, randomized=True)
        rng = _random.Random(
            zlib.crc32(f"PorySuiteZ_GenericLoop_{n}".encode("utf-8")))
        pool = list(range(n))
        seq: List[int] = []
        for _ in range(max(2, _GENERIC_RND_SEQ_LEN)):
            choices = [f for f in pool if not seq or f != seq[-1]]
            seq.append(rng.choice(choices) if choices else pool[0])
        # Avoid same-frame at the ANIMCMD_JUMP wrap (last == first).
        if len(pool) > 1 and seq[-1] == seq[0]:
            alt = [f for f in pool
                   if f != seq[0] and (len(seq) < 2 or f != seq[-2])]
            if alt:
                seq[-1] = rng.choice(alt)
        frame_lines = "\n".join(
            f"    ANIMCMD_FRAME({i}, "
            f"{rng.randint(_GENERIC_RND_HOLD_MIN, _GENERIC_RND_HOLD_MAX)}),"
            for i in seq
        )
        slot_lines = "\n".join(
            f"    [{slot}] = {anim_name}," for slot in _STANDARD_ANIM_SLOTS
        )
        parts.append(
            f"static const union AnimCmd {anim_name}[] = {{\n"
            f"{frame_lines}\n"
            f"    ANIMCMD_JUMP(0),\n"
            f"}};\n"
            f"static const union AnimCmd *const {table_name}[] = {{\n"
            f"{slot_lines}\n"
            f"}};"
        )

    parts.append(_GENERIC_LOOP_SENTINEL_CLOSE)
    return "\n\n".join(parts)


def ensure_generic_loop_tables(root: str) -> Tuple[bool, List[str]]:
    """Install (or refresh) the project-wide generic loop tables.

    Two orderings (Sequential / Random) × every frame count in
    ``GENERIC_LOOP_FRAME_COUNTS`` get written into
    ``object_event_anims.h`` inside a single sentinel-fenced block.  A
    re-run replaces the block wholesale, never duplicates.

    Returns ``(success, messages)``.  An installation that already matches
    the current generator output is a clean no-op (empty messages list).
    """
    msgs: List[str] = []
    anims_path = os.path.join(
        root, "src", "data", "object_events", "object_event_anims.h",
    )
    if not os.path.isfile(anims_path):
        return False, [f"missing {anims_path}"]

    text = _read(anims_path)
    block = _generate_generic_loop_block()

    existing = re.compile(
        re.escape(_GENERIC_LOOP_SENTINEL_OPEN) + r".*?"
        + re.escape(_GENERIC_LOOP_SENTINEL_CLOSE),
        re.DOTALL,
    )
    if existing.search(text):
        new_text = existing.sub(lambda _m: block, text, count=1)
        action = "Refreshed"
    else:
        new_text = text.rstrip() + "\n\n" + block + "\n"
        action = "Installed"

    if new_text == text:
        return True, []
    if not _atomic_write_text(anims_path, new_text):
        return False, [f"failed to write {anims_path}"]
    msgs.append(
        f"{action} generic loop tables in object_event_anims.h "
        f"({len(GENERIC_LOOP_FRAME_COUNTS)} frame counts × 2 orderings)"
    )
    return True, msgs


# ──────────────────── frame-cycle movement type ─────────────────────────────
#
# A frame-cycle entity must be placed in Porymap with MOVEMENT_TYPE_FRAME_CYCLE
# — NOT MOVEMENT_TYPE_NONE.
#
# Why a dedicated movement type exists:  MOVEMENT_TYPE_NONE has an *empty*
# movement callback — once the entity spawns the engine never touches it
# again.  That looks fine until the player talks to the entity.  A talk
# script runs `lock` (freezes the object, pausing its animation) and
# `faceplayer` (issues a face-direction held movement that ends in the engine
# function FaceDirection(), which sets sprite->animPaused = TRUE).  `release`
# restores the pre-freeze pause state, but with an empty callback NOTHING
# re-drives the animation afterwards, so the entity is left frozen on a single
# frame.  Ordinary idle NPCs escape this because their movement callbacks
# (LookAround, FaceDirection, …) run every frame and keep re-issuing movement.
#
# MovementType_FrameCycle is that missing callback: every idle frame it
# re-clears animPaused (and disableAnim), so the cycle animation free-runs
# forever no matter what a conversation did to the sprite.

FRAME_CYCLE_MOVEMENT_TYPE = "MOVEMENT_TYPE_FRAME_CYCLE"
FRAME_CYCLE_MOVEMENT_FUNC = "MovementType_FrameCycle"


def _fc_movement_function_block() -> str:
    """The C source for MovementType_FrameCycle + its per-frame callback."""
    return (
        "// >>> PorySuite-Z frame-cycle movement type >>>\n"
        "// A stationary entity whose animation table free-runs forever.\n"
        "// Unlike MovementType_None (an empty callback), the callback below\n"
        "// re-clears animPaused every idle frame, so a conversation cannot\n"
        "// leave the entity frozen: `lock` and `faceplayer` both pause the\n"
        "// sprite, and with an empty callback nothing un-pauses it again.\n"
        "// PorySuite-Z's Frame Cycle tool drives its sprites with this type.\n"
        "static u8 MovementType_FrameCycle_callback(struct ObjectEvent *, struct Sprite *);\n"
        "void MovementType_FrameCycle(struct Sprite *sprite)\n"
        "{\n"
        "    UpdateObjectEventCurrentMovement(&gObjectEvents[sprite->data[0]], sprite, MovementType_FrameCycle_callback);\n"
        "}\n"
        "static u8 MovementType_FrameCycle_callback(struct ObjectEvent *objectEvent, struct Sprite *sprite)\n"
        "{\n"
        "    sprite->animPaused = FALSE;\n"
        "    objectEvent->disableAnim = FALSE;\n"
        "    return 0;\n"
        "}\n"
        "// <<< PorySuite-Z frame-cycle movement type <<<"
    )


def _fc_insert_before_array_close(
    text: str, array_decl_re: str, entry_line: str,
) -> Tuple[bool, str]:
    """Insert `entry_line` just before the closing `};` of the C array whose
    declaration matches `array_decl_re`.

    The frame-cycle target arrays (`sMovementTypeCallbacks`,
    `gInitialMovementTypeFacingDirections`) are flat designated-initializer
    lists with no nested braces, so the first `\\n};` after the declaration is
    reliably the array's close.
    """
    m = re.search(array_decl_re, text)
    if not m:
        return False, text
    close = text.find("\n};", m.end())
    if close < 0:
        return False, text
    # text[:close + 1] keeps the newline that ends the last entry; entry_line
    # ends in its own newline; text[close + 1:] starts at "};".
    return True, text[:close + 1] + entry_line + text[close + 1:]


def ensure_frame_cycle_movement_type(root: str) -> Tuple[bool, List[str]]:
    """Install MOVEMENT_TYPE_FRAME_CYCLE engine support (idempotent).

    Five engine edits, each guarded by a unique marker so the call is safe to
    repeat and a partially-applied install self-heals on the next call:

      1. include/constants/event_object_movement.h — define the constant and
         bump MOVEMENT_TYPES_COUNT by one.
      2. src/event_object_movement.c — forward-declare MovementType_FrameCycle.
      3. src/event_object_movement.c — register it in sMovementTypeCallbacks[].
      4. src/event_object_movement.c — give it a sensible initial facing in
         gInitialMovementTypeFacingDirections[].
      5. src/event_object_movement.c — define MovementType_FrameCycle and its
         per-frame callback.

    Project-agnostic: the new constant takes whatever MOVEMENT_TYPES_COUNT
    currently is (vanilla 0x51, or higher on a fork that added its own types),
    and the count is bumped from that — no hardcoded numbering.

    Returns (success, messages).
    """
    msgs: List[str] = []

    hdr = os.path.join(
        root, "include", "constants", "event_object_movement.h")
    src = os.path.join(root, "src", "event_object_movement.c")
    for p in (hdr, src):
        if not os.path.isfile(p):
            return False, [f"missing {p}"]

    # ── Edit 1: header constant + MOVEMENT_TYPES_COUNT bump ──────────────
    htext = _read(hdr)
    if re.search(r"\bMOVEMENT_TYPE_FRAME_CYCLE\b", htext):
        msgs.append("MOVEMENT_TYPE_FRAME_CYCLE already defined")
    else:
        m = re.search(
            r"^([ \t]*#define[ \t]+MOVEMENT_TYPES_COUNT[ \t]+)"
            r"(0[xX][0-9A-Fa-f]+|\d+)[ \t]*$",
            htext, re.MULTILINE,
        )
        if not m:
            return False, [
                "could not find the MOVEMENT_TYPES_COUNT #define in "
                "event_object_movement.h"]
        count_txt = m.group(2)
        is_hex = count_txt[:2].lower() == "0x"
        count_val = int(count_txt, 16) if is_hex else int(count_txt, 10)
        new_count = count_val + 1
        if is_hex:
            new_val_s, new_count_s = f"0x{count_val:02X}", f"0x{new_count:02X}"
        else:
            new_val_s, new_count_s = str(count_val), str(new_count)
        value_col = len(m.group(1))            # column the value starts at
        new_define = "#define " + FRAME_CYCLE_MOVEMENT_TYPE
        pad = " " * max(1, value_col - len(new_define))
        block = (
            "// >>> PorySuite-Z frame-cycle movement type >>>\n"
            f"{new_define}{pad}{new_val_s}\n"
            "// <<< PorySuite-Z frame-cycle movement type <<<\n"
            f"{m.group(1)}{new_count_s}"
        )
        htext = htext.replace(m.group(0), block, 1)
        if not _atomic_write_text(hdr, htext):
            return False, [f"failed to write {hdr}"]
        msgs.append(
            f"event_object_movement.h: added {FRAME_CYCLE_MOVEMENT_TYPE} = "
            f"{new_val_s}, MOVEMENT_TYPES_COUNT -> {new_count_s}")

    # ── Edits 2-5: event_object_movement.c ───────────────────────────────
    stext = _read(src)
    before = stext

    # Edit 2 — forward declaration (used by sMovementTypeCallbacks below)
    fwd_decl = "static void MovementType_FrameCycle(struct Sprite *);"
    if fwd_decl not in stext:
        anchor = "static void MovementType_None(struct Sprite *);"
        if anchor not in stext:
            return False, [
                "could not find the MovementType_None forward declaration "
                "in event_object_movement.c"]
        stext = stext.replace(
            anchor,
            anchor + "\n" + fwd_decl + "  // PorySuite-Z frame-cycle",
            1,
        )
        msgs.append("event_object_movement.c: forward-declared "
                    "MovementType_FrameCycle")

    # Edit 3 — register in sMovementTypeCallbacks[]
    cb_entry = "[MOVEMENT_TYPE_FRAME_CYCLE] = MovementType_FrameCycle,"
    if cb_entry not in stext:
        ok, stext = _fc_insert_before_array_close(
            stext,
            r"sMovementTypeCallbacks\[MOVEMENT_TYPES_COUNT\]",
            "    " + cb_entry + "  // PorySuite-Z frame-cycle\n",
        )
        if not ok:
            return False, [
                "could not locate sMovementTypeCallbacks[] in "
                "event_object_movement.c"]
        msgs.append("event_object_movement.c: registered MovementType_"
                    "FrameCycle in sMovementTypeCallbacks[]")

    # Edit 4 — initial facing direction
    dir_entry = "[MOVEMENT_TYPE_FRAME_CYCLE] = DIR_SOUTH,"
    if dir_entry not in stext:
        ok, stext = _fc_insert_before_array_close(
            stext,
            r"gInitialMovementTypeFacingDirections\[MOVEMENT_TYPES_COUNT\]",
            "    " + dir_entry + "  // PorySuite-Z frame-cycle\n",
        )
        if not ok:
            return False, [
                "could not locate gInitialMovementTypeFacingDirections[] in "
                "event_object_movement.c"]
        msgs.append("event_object_movement.c: added frame-cycle entry to "
                    "gInitialMovementTypeFacingDirections[]")

    # Edit 5 — the function definition
    if "void MovementType_FrameCycle(struct Sprite *sprite)" not in stext:
        anchor = "movement_type_empty_callback(MovementType_None)"
        if anchor not in stext:
            return False, [
                "could not find movement_type_empty_callback(MovementType_"
                "None) in event_object_movement.c"]
        stext = stext.replace(
            anchor,
            anchor + "\n\n" + _fc_movement_function_block(),
            1,
        )
        msgs.append("event_object_movement.c: defined MovementType_FrameCycle")

    if stext != before:
        if not _atomic_write_text(src, stext):
            return False, [f"failed to write {src}"]

    return True, msgs
