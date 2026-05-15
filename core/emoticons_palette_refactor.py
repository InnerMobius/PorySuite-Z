"""Emoticons palette refactor — DOWP companion patch.

Vanilla ``src/trainer_see.c`` declares ``sSpriteTemplate_Emoticons`` with
``paletteTag = 0xFFFF`` (``TAG_NONE``) and ``paletteNum = 0`` baked into
the OAM data.  The ``FldEff_*Icon`` functions create the sprite via
``CreateSpriteAtEnd`` and never explicitly load a palette — they rely on
whatever palette happens to be in OBJ palette slot 0 at runtime.

In vanilla pokefirered, the player's palette is statically loaded into
slot 0 at field-map init, and the emoticons sprite's pixel indices were
chosen so that yellow / red / etc. land on the right palette positions
for the player's colour table.

Under **Dynamic Overworld Palettes (DOWP)**, slot 0 is no longer
deterministic — the dynamic allocator just picks the next free slot,
which can be ANY NPC palette.  The exclamation mark that should render
yellow ends up using whatever colour is at index 13 of whatever NPC
palette landed in slot 0 — most commonly pink, sometimes brown, never
yellow.

The fix is an engine refactor that gives the emoticons their own
``OBJ_EVENT_PAL_TAG_EMOTICONS`` tag, bakes a dedicated ``.gbapal`` from
the source PNG, registers the palette in ``sObjectEventSpritePalettes``,
points the sprite template at the new tag, and inserts a
``LoadObjectEventPalette`` call inside every ``FldEff_*Icon`` function so
the palette is allocated before ``CreateSpriteAtEnd`` resolves the tag
to a slot.

Applies idempotently on every DOWP enable; reverses cleanly on disable
(restoring the vanilla ``paletteTag = 0xFFFF`` and stripping the load
calls, define, INCBIN, sprite-palettes entry, and ``.gbapal`` file).
"""

from __future__ import annotations

import os
import re
from typing import List, Tuple

# Tag namespace map (cross-referenced with BUGS.md's reserved-tag list):
#   0x1100–0x111F   vanilla OBJ_EVENT_PAL_TAG_*
#   0x1120–0x11FE   engine-refactor reserved (EMOTICONS lives here)
#   0x11FF          OBJ_EVENT_PAL_TAG_NONE
#   0x1200          TAG_WEATHER_START (do NOT use)
#   0x1300–0x1301   FLDEFF_PAL_TAG_FLDEFF_SHADOW / SURF_BLOB
#   0x1400+         per-sprite fork tags (allocated by overworld_palette_fork)
TAG_VALUE = 0x1140
TAG_CONST = "OBJ_EVENT_PAL_TAG_EMOTICONS"
DATA_SYMBOL = "gObjectEventPal_Emoticons"
GBAPAL_REL = "graphics/misc/emoticons.gbapal"
SOURCE_PNG_REL = "graphics/misc/emoticons.png"

# Every FldEff_*Icon function in trainer_see.c that constructs an
# emoticon sprite.  Each gets a ``LoadObjectEventPalette`` call inserted
# at its top so the palette is in a slot before CreateSpriteAtEnd runs.
FLDEFF_FUNCTIONS = (
    "FldEff_ExclamationMarkIcon1",
    "FldEff_DoubleExclMarkIcon",
    "FldEff_XIcon",
    "FldEff_SmileyFaceIcon",
    "FldEff_QuestionMarkIcon",
)


# ── File IO ────────────────────────────────────────────────────────────

def _read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def _write(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)


# ── Palette baking ────────────────────────────────────────────────────

def _bake_gbapal_from_png(png_path: str, gbapal_path: str) -> bool:
    """Extract the source PNG's 16-entry indexed palette and write a
    GBA-format 32-byte ``.gbapal`` file.

    Returns True on success.  Returns False without writing if the PNG
    is missing or not indexed.
    """
    if not os.path.isfile(png_path):
        return False
    try:
        from PyQt6.QtGui import QImage
    except Exception:
        return False
    img = QImage(png_path)
    if img.isNull():
        return False
    ct = img.colorTable()
    if not ct:
        return False
    out = bytearray()
    for i in range(16):
        if i < len(ct):
            c = ct[i]
            r = (c >> 16) & 0xFF
            g = (c >> 8) & 0xFF
            b = c & 0xFF
        else:
            r = g = b = 0
        # GBA 15-bit BGR: 0bBBBBBGGGGGRRRRR
        r5 = min(r >> 3, 31)
        g5 = min(g >> 3, 31)
        b5 = min(b >> 3, 31)
        val = r5 | (g5 << 5) | (b5 << 10)
        out.append(val & 0xFF)
        out.append((val >> 8) & 0xFF)
    os.makedirs(os.path.dirname(gbapal_path), exist_ok=True)
    with open(gbapal_path, "wb") as f:
        f.write(bytes(out))
    return True


# ── Tag constant in event_object_movement.c ───────────────────────────

def _add_tag_define(eom_text: str) -> Tuple[str, bool]:
    """Insert ``#define OBJ_EVENT_PAL_TAG_EMOTICONS 0x1140`` next to the
    other ``OBJ_EVENT_PAL_TAG_*`` defines.  Idempotent.
    """
    if re.search(
        r"^#define\s+" + re.escape(TAG_CONST) + r"\s+0x",
        eom_text, flags=re.MULTILINE,
    ):
        return eom_text, False  # already present
    # Anchor: insert immediately after the last vanilla tag define
    # (OBJ_EVENT_PAL_TAG_RS_SUBMARINE_SHADOW), before OBJ_EVENT_PAL_TAG_NONE.
    anchor = re.search(
        r"(#define\s+OBJ_EVENT_PAL_TAG_RS_SUBMARINE_SHADOW\s+0x[0-9A-Fa-f]+)\n",
        eom_text,
    )
    if not anchor:
        return eom_text, False
    new_line = f"#define {TAG_CONST:<44s} 0x{TAG_VALUE:04X}\n"
    return (
        eom_text[:anchor.end()] + new_line + eom_text[anchor.end():],
        True,
    )


def _remove_tag_define(eom_text: str) -> Tuple[str, bool]:
    pat = re.compile(
        r"^#define\s+" + re.escape(TAG_CONST) + r"\s+0x[0-9A-Fa-f]+\n",
        flags=re.MULTILINE,
    )
    new_text, n = pat.subn("", eom_text, count=1)
    return new_text, n > 0


# ── sObjectEventSpritePalettes entry ──────────────────────────────────

def _add_palette_entry(eom_text: str) -> Tuple[str, bool]:
    """Add ``{gObjectEventPal_Emoticons, OBJ_EVENT_PAL_TAG_EMOTICONS}``
    to ``sObjectEventSpritePalettes`` right before the ``{},`` sentinel.
    """
    entry_pat = re.compile(
        r"\{" + re.escape(DATA_SYMBOL) + r"\s*,\s*"
        + re.escape(TAG_CONST) + r"\}",
    )
    if entry_pat.search(eom_text):
        return eom_text, False  # already present
    # Match the LAST `{},` inside the sObjectEventSpritePalettes array.
    # The array is delimited; the sentinel sits at the bottom.
    array_match = re.search(
        r"(static\s+const\s+struct\s+SpritePalette\s+sObjectEventSpritePalettes\s*\[\]\s*=\s*\{)",
        eom_text,
    )
    if not array_match:
        return eom_text, False
    sentinel_pat = re.compile(r"\n\s*\{\}\s*,\s*\n\s*\};")
    sentinel = sentinel_pat.search(eom_text, pos=array_match.end())
    if not sentinel:
        return eom_text, False
    pad = " " * max(1, 32 - len(DATA_SYMBOL))
    new_line = f"    {{{DATA_SYMBOL},{pad}{TAG_CONST}}},\n"
    return (
        eom_text[:sentinel.start()] + "\n" + new_line + eom_text[sentinel.start() + 1:],
        True,
    )


def _remove_palette_entry(eom_text: str) -> Tuple[str, bool]:
    pat = re.compile(
        r"^\s*\{" + re.escape(DATA_SYMBOL) + r"\s*,\s*"
        + re.escape(TAG_CONST) + r"\}\s*,\s*\n",
        flags=re.MULTILINE,
    )
    new_text, n = pat.subn("", eom_text, count=1)
    return new_text, n > 0


# ── INCBIN declaration in object_event_graphics.h ─────────────────────

def _add_incbin(gfx_text: str) -> Tuple[str, bool]:
    """Append ``const u16 gObjectEventPal_Emoticons[] = INCBIN_U16(...)``
    after the last existing palette INCBIN.  Idempotent.
    """
    if DATA_SYMBOL in gfx_text:
        return gfx_text, False
    last_pal = list(re.finditer(
        r"const\s+u16\s+gObjectEventPal_\w+\[\]\s*=\s*INCBIN_U\d+\([^)]+\);",
        gfx_text,
    ))
    if not last_pal:
        return gfx_text, False
    insert_pos = last_pal[-1].end()
    new_line = (
        f'\nconst u16 {DATA_SYMBOL}[] = INCBIN_U16("{GBAPAL_REL}");'
    )
    return (
        gfx_text[:insert_pos] + new_line + gfx_text[insert_pos:],
        True,
    )


def _remove_incbin(gfx_text: str) -> Tuple[str, bool]:
    pat = re.compile(
        r"\nconst\s+u16\s+" + re.escape(DATA_SYMBOL)
        + r"\[\]\s*=\s*INCBIN_U\d+\([^)]+\);",
    )
    new_text, n = pat.subn("", gfx_text, count=1)
    return new_text, n > 0


# ── trainer_see.c: sprite template paletteTag + load calls ────────────

# Vanilla sprite template line:
#     .paletteTag = 0xFFFF,
# After patch, it should be:
#     .paletteTag = OBJ_EVENT_PAL_TAG_EMOTICONS,
#
# We anchor on the surrounding lines of sSpriteTemplate_Emoticons rather
# than a free-floating ``.paletteTag = 0xFFFF,`` to avoid touching
# unrelated templates that legitimately use TAG_NONE.

_TEMPLATE_BLOCK_RE = re.compile(
    r"(static\s+const\s+struct\s+SpriteTemplate\s+sSpriteTemplate_Emoticons\s*=\s*\{\s*\n"
    r"\s*\.tileTag\s*=\s*0xFFFF,\s*\n"
    r"\s*\.paletteTag\s*=\s*)(0xFFFF)(,)",
)


def _patch_template_tag(ts_text: str) -> Tuple[str, bool]:
    """Change the emoticons template paletteTag from 0xFFFF to
    OBJ_EVENT_PAL_TAG_EMOTICONS.  Idempotent.
    """
    if TAG_CONST in ts_text:
        # Already patched (or partially patched in a previous run)
        if not _TEMPLATE_BLOCK_RE.search(ts_text):
            return ts_text, False
    m = _TEMPLATE_BLOCK_RE.search(ts_text)
    if not m:
        return ts_text, False
    return (
        ts_text[:m.start(2)] + TAG_CONST + ts_text[m.end(2):],
        True,
    )


_REVERSE_TEMPLATE_RE = re.compile(
    r"(static\s+const\s+struct\s+SpriteTemplate\s+sSpriteTemplate_Emoticons\s*=\s*\{\s*\n"
    r"\s*\.tileTag\s*=\s*0xFFFF,\s*\n"
    r"\s*\.paletteTag\s*=\s*)(" + re.escape(TAG_CONST) + r")(,)",
)


def _restore_template_tag(ts_text: str) -> Tuple[str, bool]:
    m = _REVERSE_TEMPLATE_RE.search(ts_text)
    if not m:
        return ts_text, False
    return (
        ts_text[:m.start(2)] + "0xFFFF" + ts_text[m.end(2):],
        True,
    )


# Each FldEff_*Icon function gets a one-line LoadObjectEventPalette call
# inserted at the top of its body (right after the opening brace).
# Vanilla example:
#     u8 FldEff_ExclamationMarkIcon1(void)
#     {
#         u8 spriteId = CreateSpriteAtEnd(&sSpriteTemplate_Emoticons, 0, 0, 0x53);
#         ...
# Patched:
#     u8 FldEff_ExclamationMarkIcon1(void)
#     {
#         // DOWP: ensure the emoticons palette is loaded before the sprite
#         // is created so CreateSpriteAtEnd can resolve OBJ_EVENT_PAL_TAG_EMOTICONS
#         // to its allocated OBJ palette slot.
#         LoadObjectEventPalette(OBJ_EVENT_PAL_TAG_EMOTICONS);
#         u8 spriteId = CreateSpriteAtEnd(&sSpriteTemplate_Emoticons, 0, 0, 0x53);
#         ...

_LOAD_LINE = (
    "    // DOWP: ensure the emoticons palette is loaded before the sprite\n"
    "    // is created so CreateSpriteAtEnd can resolve OBJ_EVENT_PAL_TAG_EMOTICONS\n"
    "    // to its allocated OBJ palette slot.\n"
    "    LoadObjectEventPalette(" + TAG_CONST + ");\n"
)


def _patch_fldeff_body(ts_text: str, func_name: str) -> Tuple[str, bool]:
    """Insert the LoadObjectEventPalette call at the top of one
    FldEff_*Icon function body.  Idempotent.
    """
    pat = re.compile(
        r"(u8\s+" + re.escape(func_name) + r"\s*\(\s*void\s*\)\s*\n\{\n)",
    )
    m = pat.search(ts_text)
    if not m:
        return ts_text, False
    body_start = m.end()
    # Already patched?  Skip.
    if ts_text[body_start:body_start + 60].startswith("    // DOWP: ensure the emoticons"):
        return ts_text, False
    return (
        ts_text[:body_start] + _LOAD_LINE + ts_text[body_start:],
        True,
    )


def _unpatch_fldeff_body(ts_text: str, func_name: str) -> Tuple[str, bool]:
    pat = re.compile(
        r"(u8\s+" + re.escape(func_name) + r"\s*\(\s*void\s*\)\s*\n\{\n)"
        + re.escape(_LOAD_LINE),
    )
    m = pat.search(ts_text)
    if not m:
        return ts_text, False
    return (
        ts_text[:m.end(1)] + ts_text[m.end():],
        True,
    )


# Extern declaration so trainer_see.c can call LoadObjectEventPalette
# (which is in event_object_movement.c).  Inserted near the top of the
# includes / static declarations.

_EXTERN_BLOCK = (
    "// DOWP: emoticons palette refactor — these symbols live in\n"
    "// event_object_movement.c.  Vanilla pokefirered's tag namespace is\n"
    "// file-local, so we re-declare the tag value next to the function\n"
    "// extern to keep this self-contained.\n"
    "#define " + TAG_CONST + "                  "
    + f"0x{TAG_VALUE:04X}\n"
    "extern void LoadObjectEventPalette(u16 paletteTag);\n"
)


def _patch_extern_decl(ts_text: str) -> Tuple[str, bool]:
    """Add an ``extern`` declaration for ``LoadObjectEventPalette`` and
    the matching ``OBJ_EVENT_PAL_TAG_EMOTICONS`` define so trainer_see.c
    can reference both.  DOWP makes ``LoadObjectEventPalette`` non-static
    in event_object_movement.c, but the tag define is file-local — the
    cleanest cross-file usage is a self-contained re-declaration block.
    """
    if _EXTERN_BLOCK in ts_text:
        return ts_text, False
    # Anchor: right before the first static function declaration.
    anchor = re.search(r"\nstatic\s+bool8\s+TrainerSeeFunc_", ts_text)
    if not anchor:
        return ts_text, False
    return (
        ts_text[:anchor.start() + 1] + _EXTERN_BLOCK + ts_text[anchor.start() + 1:],
        True,
    )


def _unpatch_extern_decl(ts_text: str) -> Tuple[str, bool]:
    """Remove the extern block we inserted.  Also self-heals stale state
    from earlier patcher versions: an older revision inserted only the
    extern line (no #define, no comment header).  Strip that too if
    present, AND deduplicate any double-inserted extern lines.
    """
    changed = False
    new_text = ts_text
    if _EXTERN_BLOCK in new_text:
        new_text = new_text.replace(_EXTERN_BLOCK, "", 1)
        changed = True

    # Old-format standalone extern (from pre-fix patcher runs)
    old_extern = "extern void LoadObjectEventPalette(u16 paletteTag);\n"
    # Don't touch if vanilla pokefirered already exported this somehow —
    # only strip the ones we added.  Vanilla pokefirered does NOT have
    # this declaration in trainer_see.c, so any occurrence is ours.
    while old_extern in new_text:
        new_text = new_text.replace(old_extern, "", 1)
        changed = True

    return new_text, changed


# ── Public apply / remove ─────────────────────────────────────────────


def apply(project_root: str) -> Tuple[bool, List[str], List[str]]:
    """Apply the emoticons palette refactor.

    Returns ``(success, applied_messages, failed_messages)``.
    """
    applied: List[str] = []
    failed: List[str] = []

    # 1. Bake the .gbapal from the PNG.  Always re-bake — the PNG is the
    #    source of truth, and the user may have edited it via PorySuite's
    #    Field Effect Sprites tab between DOWP toggles.
    png_path = os.path.join(project_root, SOURCE_PNG_REL)
    gbapal_path = os.path.join(project_root, GBAPAL_REL)
    if _bake_gbapal_from_png(png_path, gbapal_path):
        applied.append(f"Baked {GBAPAL_REL} from emoticons.png")
    else:
        failed.append(
            "Could not bake emoticons.gbapal — PNG missing or not indexed"
        )
        return False, applied, failed

    # 2. Add the tag define and palette entry to event_object_movement.c.
    eom_path = os.path.join(project_root, "src", "event_object_movement.c")
    if not os.path.isfile(eom_path):
        failed.append("event_object_movement.c missing")
        return False, applied, failed
    eom = _read(eom_path)

    eom, did = _add_tag_define(eom)
    if did:
        applied.append(f"Added #define {TAG_CONST} 0x{TAG_VALUE:04X}")
    elif TAG_CONST in eom:
        applied.append(f"#define {TAG_CONST} already present")
    else:
        failed.append(f"Could not add #define {TAG_CONST}")

    eom, did = _add_palette_entry(eom)
    if did:
        applied.append(
            f"Added {{{DATA_SYMBOL}, {TAG_CONST}}} to sObjectEventSpritePalettes"
        )
    elif DATA_SYMBOL in eom:
        applied.append(
            f"sObjectEventSpritePalettes already has {DATA_SYMBOL} entry"
        )
    else:
        failed.append(
            f"Could not add {DATA_SYMBOL} to sObjectEventSpritePalettes"
        )

    _write(eom_path, eom)

    # 3. Add INCBIN in object_event_graphics.h.
    gfx_path = os.path.join(
        project_root, "src", "data", "object_events", "object_event_graphics.h"
    )
    if os.path.isfile(gfx_path):
        gfx = _read(gfx_path)
        gfx, did = _add_incbin(gfx)
        if did:
            applied.append(f"Added {DATA_SYMBOL} INCBIN")
            _write(gfx_path, gfx)
        elif DATA_SYMBOL in gfx:
            applied.append(f"{DATA_SYMBOL} INCBIN already present")
        else:
            failed.append(f"Could not add {DATA_SYMBOL} INCBIN")
    else:
        failed.append("object_event_graphics.h missing")

    # 4. Patch trainer_see.c: extern decl, template tag, FldEff_* bodies.
    ts_path = os.path.join(project_root, "src", "trainer_see.c")
    if not os.path.isfile(ts_path):
        failed.append("trainer_see.c missing")
        return False, applied, failed
    ts = _read(ts_path)

    ts, did = _patch_extern_decl(ts)
    if did:
        applied.append("trainer_see.c: extern LoadObjectEventPalette added")
    elif _EXTERN_LINE in ts:
        applied.append("trainer_see.c: extern LoadObjectEventPalette already present")
    else:
        failed.append("Could not add extern LoadObjectEventPalette in trainer_see.c")

    ts, did = _patch_template_tag(ts)
    if did:
        applied.append(
            f"trainer_see.c: sSpriteTemplate_Emoticons.paletteTag ->{TAG_CONST}"
        )
    elif TAG_CONST in ts:
        applied.append(
            "trainer_see.c: sSpriteTemplate_Emoticons.paletteTag already patched"
        )
    else:
        failed.append("Could not patch sSpriteTemplate_Emoticons.paletteTag")

    patched_funcs: List[str] = []
    for fn in FLDEFF_FUNCTIONS:
        ts, did = _patch_fldeff_body(ts, fn)
        if did:
            patched_funcs.append(fn)
    if patched_funcs:
        applied.append(
            f"trainer_see.c: inserted LoadObjectEventPalette into "
            f"{len(patched_funcs)} FldEff_*Icon function(s)"
        )

    _write(ts_path, ts)

    return len(failed) == 0, applied, failed


def remove(project_root: str) -> Tuple[bool, List[str], List[str]]:
    """Reverse the emoticons palette refactor.

    Restores ``trainer_see.c`` to its pre-DOWP state and strips the tag
    define, palette entry, INCBIN, and ``.gbapal`` artefact.

    Returns ``(success, reverted_messages, failed_messages)``.
    """
    reverted: List[str] = []
    failed: List[str] = []

    # 1. trainer_see.c — restore template tag + strip extern + load calls.
    ts_path = os.path.join(project_root, "src", "trainer_see.c")
    if os.path.isfile(ts_path):
        ts = _read(ts_path)
        unpatched_count = 0
        for fn in FLDEFF_FUNCTIONS:
            ts, did = _unpatch_fldeff_body(ts, fn)
            if did:
                unpatched_count += 1
        if unpatched_count:
            reverted.append(
                f"trainer_see.c: stripped LoadObjectEventPalette from "
                f"{unpatched_count} FldEff_*Icon function(s)"
            )

        ts, did = _restore_template_tag(ts)
        if did:
            reverted.append(
                "trainer_see.c: sSpriteTemplate_Emoticons.paletteTag ->0xFFFF"
            )

        ts, did = _unpatch_extern_decl(ts)
        if did:
            reverted.append("trainer_see.c: removed extern LoadObjectEventPalette")

        _write(ts_path, ts)

    # 2. object_event_graphics.h — strip INCBIN.
    gfx_path = os.path.join(
        project_root, "src", "data", "object_events", "object_event_graphics.h"
    )
    if os.path.isfile(gfx_path):
        gfx = _read(gfx_path)
        gfx, did = _remove_incbin(gfx)
        if did:
            reverted.append(f"Removed {DATA_SYMBOL} INCBIN")
            _write(gfx_path, gfx)

    # 3. event_object_movement.c — strip palette entry and tag define.
    eom_path = os.path.join(project_root, "src", "event_object_movement.c")
    if os.path.isfile(eom_path):
        eom = _read(eom_path)

        eom, did = _remove_palette_entry(eom)
        if did:
            reverted.append(
                f"Removed {{{DATA_SYMBOL}, {TAG_CONST}}} from sObjectEventSpritePalettes"
            )

        eom, did = _remove_tag_define(eom)
        if did:
            reverted.append(f"Removed #define {TAG_CONST}")

        _write(eom_path, eom)

    # 4. Delete the baked .gbapal so vanilla state has no residue.
    gbapal_path = os.path.join(project_root, GBAPAL_REL)
    if os.path.isfile(gbapal_path):
        try:
            os.remove(gbapal_path)
            reverted.append(f"Deleted {GBAPAL_REL}")
        except OSError as exc:
            failed.append(f"Could not delete {GBAPAL_REL}: {exc}")

    return len(failed) == 0, reverted, failed
