"""Pure data layer for the Battle Animations tab.

Parses pokefirered's battle-animation sprite registry into a flat list
of ``BattleAnimSprite`` records the UI can browse and edit.  No Qt, no
project-data-manager imports -- stdlib only -- so it's testable in
isolation (the UI tab consumes this; the tab is the Qt part).

What it reads
=============

Battle-anim sprites are registered by two parallel tables in
``src/data/battle_anim.h``, joined on an ``ANIM_TAG_*`` constant:

    const struct CompressedSpriteSheet gBattleAnimPicTable[] =
    {
        {gBattleAnimSpriteGfx_Bone, 0x0200, ANIM_TAG_BONE},
        ...
    };
    const struct CompressedSpritePalette gBattleAnimPaletteTable[] =
    {
        {gBattleAnimSpritePal_Bone, ANIM_TAG_BONE},
        ...
    };

The gfx and palette symbols are INCBIN'd in ``src/graphics.c``:

    const u32 gBattleAnimSpriteGfx_Bone[] =
        INCBIN_U32("graphics/battle_anims/sprites/bone.4bpp.lz");
    const u32 gBattleAnimSpritePal_Bone[] =
        INCBIN_U32("graphics/battle_anims/sprites/bone.gbapal.lz");

Compression vs overworld
========================

Unlike overworld sprites (uncompressed ``.4bpp`` + ``.pal``), battle-anim
sprites build to LZ77-compressed ``.4bpp.lz`` / ``.gbapal.lz``.  That's a
build-side concern only -- gbagfx compresses from the ``.png`` source and
the palette source at build time.  The EDITOR always works on the
uncompressed source: the ``<name>.png`` (pixels) and the authoritative
palette (a sibling ``<name>.pal`` JASC file when present, else the
``<name>.gbapal`` binary, else the PNG's own embedded palette).  This
mirrors the overworld discipline: PNG carries a baked palette but the
sidecar palette file is authoritative, and RAM edits (via the palette
bus) trump both.

Scope
=====

Phase 1 = browse + palette editing.  This module resolves each sprite's
PNG + palette path and a human display name.  Frame dimensions (from the
sprite template's OAM) and the timeline scripts are out of scope here --
they live in later phases / their own modules.
"""

from __future__ import annotations

import glob
import os
import re
from collections import Counter
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


# Relative source paths inside the pokefirered tree.
_GRAPHICS_C_REL = os.path.join("src", "graphics.c")
_BATTLE_ANIM_H_REL = os.path.join("src", "data", "battle_anim.h")
_SPRITES_DIR_REL = os.path.join("graphics", "battle_anims", "sprites")


# ───────────────────────────────────────────────────────────── dataclass ──

@dataclass
class BattleAnimSprite:
    """One battle-animation sprite, joined from the pic + palette tables.

    ``png_path`` / ``pal_path`` are absolute paths (or "" when the symbol
    couldn't be resolved to an INCBIN).  ``pal_path`` prefers a ``.pal``
    JASC sidecar, falling back to the ``.gbapal`` binary; it's "" when the
    sprite has no dedicated palette entry (it shares another sprite's
    palette at runtime — the UI falls back to the PNG's embedded table).
    """

    tag: str                 # ANIM_TAG_BONE
    gfx_symbol: str          # gBattleAnimSpriteGfx_Bone
    pal_symbol: str          # gBattleAnimSpritePal_Bone  ("" if none)
    vram_size: int           # 0x200  (VRAM byte allocation from the pic table)
    png_path: str            # absolute path to bone.png   ("" if unresolved)
    pal_path: str            # absolute path to bone.pal / bone.gbapal ("" if none)

    @property
    def display_name(self) -> str:
        """``ANIM_TAG_AIR_WAVE`` -> ``Air Wave`` for the list label."""
        name = self.tag
        if name.startswith("ANIM_TAG_"):
            name = name[len("ANIM_TAG_"):]
        parts = [p for p in name.split("_") if p]
        if not parts:
            return name or "Unknown"
        return " ".join(p.capitalize() for p in parts)

    @property
    def png_exists(self) -> bool:
        return bool(self.png_path) and os.path.isfile(self.png_path)


# ───────────────────────────────────────────────────────────── parsing ──

# INCBIN line: const u32 SYMBOL[] = INCBIN_U32("graphics/.../name.EXT");
# u8/u16/u32 all appear in the codebase — accept any width.
def _incbin_symbol_map(graphics_c_text: str, ext: str) -> Dict[str, str]:
    """Map ``{symbol: relpath}`` for every INCBIN of files ending ``ext``.

    ``ext`` is e.g. ``".4bpp.lz"`` or ``".gbapal.lz"``.  Relpath is the
    project-relative forward-slash path as written in the INCBIN.
    """
    out: Dict[str, str] = {}
    pat = re.compile(
        r"\b(\w+)\s*\[\]\s*=\s*INCBIN_U(?:8|16|32)\s*\(\s*\""
        r"([^\"]+" + re.escape(ext) + r")\"",
    )
    for sym, relpath in pat.findall(graphics_c_text):
        out[sym] = relpath
    return out


def _parse_pic_table(battle_anim_h_text: str) -> List[Tuple[str, int, str]]:
    """Parse ``gBattleAnimPicTable[]`` -> ``[(gfx_symbol, vram_size, tag)]``."""
    m = re.search(
        r"gBattleAnimPicTable\s*\[\]\s*=\s*\{(.*?)\n\};",
        battle_anim_h_text, re.DOTALL,
    )
    if not m:
        return []
    body = m.group(1)
    entries: List[Tuple[str, int, str]] = []
    row = re.compile(
        r"\{\s*(\w+)\s*,\s*(0x[0-9A-Fa-f]+|\d+)\s*,\s*(ANIM_TAG_\w+)\s*\}",
    )
    for sym, size_raw, tag in row.findall(body):
        try:
            size = int(size_raw, 0)
        except ValueError:
            size = 0
        entries.append((sym, size, tag))
    return entries


def _parse_palette_table(battle_anim_h_text: str) -> Dict[str, str]:
    """Parse ``gBattleAnimPaletteTable[]`` -> ``{tag: pal_symbol}``."""
    m = re.search(
        r"gBattleAnimPaletteTable\s*\[\]\s*=\s*\{(.*?)\n\};",
        battle_anim_h_text, re.DOTALL,
    )
    if not m:
        return {}
    body = m.group(1)
    out: Dict[str, str] = {}
    row = re.compile(r"\{\s*(\w+)\s*,\s*(ANIM_TAG_\w+)\s*\}")
    for sym, tag in row.findall(body):
        out[tag] = sym
    return out


def _resolve_pal_path(project_root: str, pal_relpath: str) -> str:
    """Given the ``.gbapal.lz`` INCBIN relpath, return the best on-disk
    palette source: a ``.pal`` JASC sidecar if it exists, else the
    ``.gbapal`` binary if it exists, else "".
    """
    base = pal_relpath
    for suffix in (".gbapal.lz", ".gbapal", ".lz"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    base_abs = os.path.join(project_root, base.replace("/", os.sep))
    pal = base_abs + ".pal"
    if os.path.isfile(pal):
        return pal
    gbapal = base_abs + ".gbapal"
    if os.path.isfile(gbapal):
        return gbapal
    return ""


def _png_from_gfx_relpath(project_root: str, gfx_relpath: str) -> str:
    """``graphics/.../bone.4bpp.lz`` -> absolute path to ``bone.png``."""
    base = gfx_relpath
    for suffix in (".4bpp.lz", ".4bpp", ".lz"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    return os.path.join(project_root, (base + ".png").replace("/", os.sep))


def parse_battle_anim_sprites(project_root: str) -> List[BattleAnimSprite]:
    """Parse the battle-anim sprite registry into ``BattleAnimSprite``s.

    Returns sprites in pic-table order (callers usually sort by
    ``display_name``).  Never raises: a missing/unreadable source file
    yields an empty list, and individual entries whose gfx symbol can't
    be resolved to an INCBIN are skipped (so a hand-edited project with
    inline gfx doesn't crash the tab — those sprites simply don't appear
    until the symbol resolves).
    """
    gfx_path = os.path.join(project_root, _GRAPHICS_C_REL)
    bah_path = os.path.join(project_root, _BATTLE_ANIM_H_REL)
    if not (os.path.isfile(gfx_path) and os.path.isfile(bah_path)):
        return []
    try:
        with open(gfx_path, encoding="utf-8", errors="replace") as f:
            graphics_c = f.read()
        with open(bah_path, encoding="utf-8", errors="replace") as f:
            battle_anim_h = f.read()
    except OSError:
        return []

    gfx_map = _incbin_symbol_map(graphics_c, ".4bpp.lz")
    pal_map = _incbin_symbol_map(graphics_c, ".gbapal.lz")
    pic_entries = _parse_pic_table(battle_anim_h)
    pal_by_tag = _parse_palette_table(battle_anim_h)

    out: List[BattleAnimSprite] = []
    for gfx_symbol, vram_size, tag in pic_entries:
        gfx_relpath = gfx_map.get(gfx_symbol)
        if not gfx_relpath:
            # gfx symbol not found as an INCBIN (inline/synthetic gfx) —
            # skip; can't browse a sprite we can't locate on disk.
            continue
        png_path = _png_from_gfx_relpath(project_root, gfx_relpath)

        pal_symbol = pal_by_tag.get(tag, "")
        pal_path = ""
        if pal_symbol:
            pal_relpath = pal_map.get(pal_symbol)
            if pal_relpath:
                pal_path = _resolve_pal_path(project_root, pal_relpath)

        out.append(BattleAnimSprite(
            tag=tag,
            gfx_symbol=gfx_symbol,
            pal_symbol=pal_symbol,
            vram_size=vram_size,
            png_path=png_path,
            pal_path=pal_path,
        ))
    return out


def battle_anim_sprites_dir(project_root: str) -> str:
    """Absolute path of the battle-anim sprites source folder."""
    return os.path.join(project_root, _SPRITES_DIR_REL)


# ──────────────────────────────────────── frame-size resolution ──
# A battle-anim sprite's true per-frame size comes from the SpriteTemplate
# that uses its ANIM_TAG: the template's ``.oam`` points at an OamData
# struct whose ``SPRITE_SIZE(WxH)`` (and conventional ``_WxH`` symbol
# suffix) give the frame's pixel dimensions.  Resolving this lets the UI
# slice the sheet into exact frames instead of guessing square frames.

_OAM_NAME_SIZE_RE = re.compile(r"_(\d+)x(\d+)\s*$")
_OAM_STRUCT_RE = re.compile(
    r"struct\s+OamData\s+(\w+)\s*=\s*\{(?P<body>.*?)\};", re.DOTALL)
_SPRITE_SIZE_RE = re.compile(r"SPRITE_SIZE\(\s*(\d+)x(\d+)\s*\)")
_TEMPLATE_RE = re.compile(
    r"struct\s+SpriteTemplate\s+(?P<name>\w+)\s*=\s*\{(?P<body>.*?)\};",
    re.DOTALL)
_TPL_TILETAG_RE = re.compile(r"\.tileTag\s*=\s*(ANIM_TAG_\w+)")
_TPL_OAM_RE = re.compile(r"\.oam\s*=\s*&?(\w+)")


def _oam_size_from_name(symbol: str) -> Optional[Tuple[int, int]]:
    m = _OAM_NAME_SIZE_RE.search(symbol)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


def _build_oam_size_map(texts: List[str]) -> Dict[str, Tuple[int, int]]:
    """``{oam_symbol: (w, h)}`` from every OamData struct in *texts*,
    read from its ``SPRITE_SIZE(WxH)`` field."""
    out: Dict[str, Tuple[int, int]] = {}
    for text in texts:
        for m in _OAM_STRUCT_RE.finditer(text):
            sym = m.group(1)
            sm = _SPRITE_SIZE_RE.search(m.group("body"))
            if sm:
                out[sym] = (int(sm.group(1)), int(sm.group(2)))
    return out


def _battle_anim_source_texts(project_root: str) -> List[str]:
    """Read the source files that hold battle-anim SpriteTemplates +
    OamData structs (``src/data/battle_anim.h`` + ``src/battle_anim*.c``).

    Returns the file contents as a list of strings (skipping unreadable
    files).  Shared by every template-scanning parser so the disk is read
    once per call site, never raises.
    """
    files: List[str] = []
    bah = os.path.join(project_root, _BATTLE_ANIM_H_REL)
    if os.path.isfile(bah):
        files.append(bah)
    files.extend(sorted(glob.glob(
        os.path.join(project_root, "src", "battle_anim*.c"))))

    texts: List[str] = []
    for path in files:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                texts.append(f.read())
        except OSError:
            continue
    return texts


def parse_template_tags(project_root: str) -> Dict[str, str]:
    """Map ``{template_symbol: ANIM_TAG_*}`` for every SpriteTemplate whose
    ``.tileTag`` is an ``ANIM_TAG_*``.

    This is the link a ``createsprite gXxxTemplate, ...`` command needs to
    resolve to the battle-anim sprite it spawns, so the composite preview
    can show the right image for each layer.  Templates whose tileTag is
    not an ANIM_TAG (or has none) are omitted.  Never raises.
    """
    out: Dict[str, str] = {}
    for text in _battle_anim_source_texts(project_root):
        for m in _TEMPLATE_RE.finditer(text):
            tt = _TPL_TILETAG_RE.search(m.group("body"))
            if tt:
                out[m.group("name")] = tt.group(1)
    return out


# ──────────────────────────────── sprite-callback motion archetypes ──
# A createsprite's on-screen position + motion are decided by the sprite
# template's C ``.callback`` — NOT by the script args directly.  We can't
# port all ~340 callbacks, but most funnel through a few shared helpers
# (InitSpritePosToAnimAttacker/Target + a linear/arc translator), so we
# classify each callback into a coarse motion archetype the preview can
# reproduce.  Anything we can't read confidently → UNKNOWN (the UI then
# falls back to a clearly-labelled static placement).

MOTION_STATIC_TARGET = "static_target"      # sits at target + (arg0,arg1)
MOTION_STATIC_ATTACKER = "static_attacker"  # sits at attacker + (arg0,arg1)
MOTION_ON_MON_POS = "on_mon_pos"            # attacker if arg2==0 else target
MOTION_LINEAR_TO_TARGET = "linear_to_target"  # attacker(arg0,1) → target(arg2,3), dur arg4
MOTION_ARC_TO_TARGET = "arc_to_target"      # like linear but a parabolic toss
MOTION_INVISIBLE = "invisible"              # utility/controller sprite — never drawn
MOTION_UNKNOWN = "unknown"                  # couldn't classify

# Curated overrides for callbacks whose bodies were read directly — these
# win over the heuristic (which can misread unusual structures).
_CURATED_ARCHETYPES = {
    "TranslateAnimSpriteToTargetMonLocation": MOTION_LINEAR_TO_TARGET,
    "AnimThrowProjectile": MOTION_ARC_TO_TARGET,
    "AnimMissileArc": MOTION_ARC_TO_TARGET,
    "AnimToTargetInSinWave": MOTION_LINEAR_TO_TARGET,
    "AnimSpriteOnMonPos": MOTION_ON_MON_POS,       # arg2==0 ? attacker : target
    "AnimHitSplatBasic": MOTION_ON_MON_POS,        # same arg2 anchor rule (190 uses)
    "AnimFirePlume": MOTION_STATIC_ATTACKER,
    "SpriteCallbackDummy": MOTION_STATIC_TARGET,
    # Invisible utility sprites: palette fades + mon-movers — never drawn.
    "AnimSimplePaletteBlend": MOTION_INVISIBLE,
    "AnimComplexPaletteBlend": MOTION_INVISIBLE,
    "SlideMonToOriginalPos": MOTION_INVISIBLE,
    "SlideMonToOffset": MOTION_INVISIBLE,
    "DoHorizontalLunge": MOTION_INVISIBLE,
}

_SPRITE_FN_RE = re.compile(
    r"(?:static\s+)?void\s+(\w+)\s*\(\s*struct\s+Sprite\s*\*\s*\w+\s*\)\s*\{")


def _extract_sprite_callback_bodies(texts: List[str]) -> Dict[str, str]:
    """Map ``{callback_name: body}`` for every ``void Name(struct Sprite *)``
    function across *texts*, brace-matched so the body is the real block."""
    out: Dict[str, str] = {}
    for text in texts:
        for m in _SPRITE_FN_RE.finditer(text):
            name = m.group(1)
            i = text.index("{", m.start())
            depth = 0
            j = i
            n = len(text)
            while j < n:
                ch = text[j]
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            out[name] = text[i:j + 1]
    return out


def _classify_callback_body(body: str) -> str:
    """Heuristic motion archetype from a sprite callback's C body."""
    # Utility/controller sprites mark themselves invisible — don't draw them.
    if "->invisible = TRUE" in body or "-> invisible = TRUE" in body:
        return MOTION_INVISIBLE
    attacker_init = "InitSpritePosToAnimAttacker" in body
    target_init = "InitSpritePosToAnimTarget" in body
    # Does it set up a translation that ends on the target?
    moves_to_target = (
        "gBattleAnimTarget" in body and (
            "StartAnimLinearTranslation" in body
            or "InitAnimLinearTranslation" in body
            or "InitAnimArcTranslation" in body
            or "TranslateAnimHorizontalArc" in body
            or "BATTLER_COORD_X_2" in body))
    is_arc = ("InitAnimArcTranslation" in body
              or "TranslateAnimHorizontalArc" in body
              or "TranslateAnimArc" in body
              or "Parabol" in body or "Parabola" in body)
    if moves_to_target and (attacker_init or "gBattleAnimAttacker" in body):
        return MOTION_ARC_TO_TARGET if is_arc else MOTION_LINEAR_TO_TARGET
    if attacker_init and not target_init:
        return MOTION_STATIC_ATTACKER
    if target_init and not attacker_init:
        return MOTION_STATIC_TARGET
    return MOTION_UNKNOWN


def parse_template_callbacks(project_root: str) -> Dict[str, str]:
    """Map ``{template_symbol: callback_symbol}`` for every battle-anim
    SpriteTemplate (so the preview can resolve a createsprite's motion)."""
    _TPL_CB_RE = re.compile(r"\.callback\s*=\s*(\w+)")
    out: Dict[str, str] = {}
    for text in _battle_anim_source_texts(project_root):
        for m in _TEMPLATE_RE.finditer(text):
            cb = _TPL_CB_RE.search(m.group("body"))
            if cb:
                out[m.group("name")] = cb.group(1)
    return out


def classify_anim_callbacks(project_root: str) -> Dict[str, str]:
    """Map ``{callback_symbol: MOTION_*}`` for every sprite callback in the
    battle-anim sources.  Curated reads win; otherwise a body heuristic;
    callbacks with no readable body are omitted (caller treats as UNKNOWN).
    Never raises."""
    texts = _battle_anim_source_texts(project_root)
    bodies = _extract_sprite_callback_bodies(texts)
    out: Dict[str, str] = {}
    for name, body in bodies.items():
        out[name] = _CURATED_ARCHETYPES.get(name) or _classify_callback_body(body)
    # Make sure curated names exist even if their body wasn't matched.
    for name, arch in _CURATED_ARCHETYPES.items():
        out.setdefault(name, arch)
    return out


def parse_anim_frame_sizes(project_root: str) -> Dict[str, Tuple[int, int]]:
    """Map ``{ANIM_TAG_*: (frame_w, frame_h)}`` by resolving each tag
    through the SpriteTemplate that uses it → its OAM → pixel size.

    Scans ``src/battle_anim*.c`` + ``src/data/battle_anim.h`` (where the
    templates and OamData structs live).  When a tag is used by multiple
    templates with differing sizes, the most common size wins (tie →
    largest area).  Tags with no resolvable template are simply absent
    (the caller falls back to square-frame inference).  Never raises.
    """
    texts = _battle_anim_source_texts(project_root)
    if not texts:
        return {}

    oam_size = _build_oam_size_map(texts)

    def _size_for_oam(sym: str) -> Optional[Tuple[int, int]]:
        if sym in oam_size:
            return oam_size[sym]
        return _oam_size_from_name(sym)

    # tag -> list of (w, h) from every template that uses it.
    per_tag: Dict[str, List[Tuple[int, int]]] = {}
    for text in texts:
        for m in _TEMPLATE_RE.finditer(text):
            body = m.group("body")
            tt = _TPL_TILETAG_RE.search(body)
            oam = _TPL_OAM_RE.search(body)
            if not (tt and oam):
                continue
            size = _size_for_oam(oam.group(1))
            if size:
                per_tag.setdefault(tt.group(1), []).append(size)

    out: Dict[str, Tuple[int, int]] = {}
    for tag, sizes in per_tag.items():
        # Most common size wins; tie-break by largest area.
        counts = Counter(sizes)
        best = max(counts, key=lambda s: (counts[s], s[0] * s[1]))
        out[tag] = best
    return out
