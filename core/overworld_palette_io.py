"""Paired .pal (JASC text) + .gbapal (32-byte binary) I/O for overworld
palettes — the single source of truth for both file formats.

Background
==========

pokefirered's overworld engine reads palette data via INCBIN of the
binary `.gbapal` file (16 colours × 2 bytes = 32 bytes, GBA 15-bit BGR
packed).  But the human-editable representation is the JASC `.pal` text
sibling that lives next to it.  Both files MUST encode the same colours
or the build (which uses `.gbapal`) and the editor UI (which reads
`.pal`) silently disagree.

PorySuite's overworld save path historically only wrote whichever of
the two extensions `_pal_paths[tag]` happened to resolve to:

  - On a stock pokefirered fork (no `.pal` siblings exist), `_pal_paths`
    falls back to the `.gbapal` path.  Writing JASC text to that path
    corrupts the binary file the build INCBINs.  gbagfx then rejects
    it with "Size 2 doesn't evenly divide file size N" and the build
    fails.
  - On a fork that already has `.pal` siblings, save writes JASC to
    `.pal` correctly — but the `.gbapal` binary is never re-baked, so
    PorySuite's UI shows the new colours while the build (and the
    in-game appearance) keeps using the stale binary.

This module ends both failure modes by always writing BOTH files from
the same in-memory colour list, plus a self-heal helper for project
open that creates any missing `.pal` sibling from its existing
`.gbapal` binary.

Project-agnostic by design
==========================

Nothing in this module assumes vanilla NPC names, palette tags, or
frame sizes.  Callers supply paths and colour lists; the module
handles atomic writes, byte-equality short-circuits, and the GBA
15-bit clamp.  Safe for any pokefirered fork — stock vanilla, heavily
modded, or brand-new.

Garbage-free contract
=====================

This module **only** creates files that the caller asks for.  It does
NOT leave temp/backup files behind.  All writes use the
`write-to-tmp-then-os.replace` atomic pattern; the `.tmp` is deleted
on any failure.  The self-heal helper only creates `.pal` siblings
for `.gbapal` files that ALREADY EXIST and are referenced by a
palette tag — it never invents files for non-existent palettes.
"""

from __future__ import annotations

import os
from typing import List, Optional, Tuple

Color = Tuple[int, int, int]


# ────────────────────────── colour-space helpers ────────────────────────────

def _gba_clamp(c: int) -> int:
    """Round a 0-255 channel to the GBA 15-bit grid (multiples of 8)."""
    return max(0, min(255, int(c))) & 0xF8


def _pack_gba_color(r: int, g: int, b: int) -> int:
    """Pack an RGB triple into GBA 15-bit BGR format (R in low bits)."""
    r5 = min(_gba_clamp(r) >> 3, 31)
    g5 = min(_gba_clamp(g) >> 3, 31)
    b5 = min(_gba_clamp(b) >> 3, 31)
    return r5 | (g5 << 5) | (b5 << 10)


def _unpack_gba_color(packed: int) -> Color:
    """Reverse `_pack_gba_color`: GBA 15-bit → 0-255 RGB triple."""
    r = (packed & 0x1F) << 3
    g = ((packed >> 5) & 0x1F) << 3
    b = ((packed >> 10) & 0x1F) << 3
    return (r, g, b)


# ────────────────────────── binary .gbapal I/O ──────────────────────────────

def encode_gbapal(colors: List[Color]) -> bytes:
    """Return 32 bytes of GBA 15-bit palette data (16 colours × 2 bytes)."""
    out = bytearray()
    n = min(len(colors), 16)
    for i in range(n):
        r, g, b = colors[i]
        out += _pack_gba_color(r, g, b).to_bytes(2, "little")
    for _ in range(16 - n):
        out += b"\x00\x00"
    return bytes(out)


def decode_gbapal(data: bytes) -> List[Color]:
    """Decode 32 bytes of GBA 15-bit palette data into RGB triples.

    Pads with black to 16 entries if the file is short.  Truncates if
    longer.  Returns an empty list if the input is not a valid binary
    .gbapal (e.g. someone accidentally wrote JASC text to a .gbapal
    path — that file starts with "JASC-PAL" instead of binary data).
    """
    if not data:
        return []
    # Safety: if the bytes look like JASC text (start with "JA"), refuse
    # to decode as binary — that's a corrupt-by-prior-bug file and the
    # caller should repair from the JASC content instead.
    if data[:4] == b"JASC":
        return []
    colors: List[Color] = []
    for i in range(0, min(32, len(data)), 2):
        if i + 1 >= len(data):
            break
        val = data[i] | (data[i + 1] << 8)
        colors.append(_unpack_gba_color(val))
    while len(colors) < 16:
        colors.append((0, 0, 0))
    return colors[:16]


# ────────────────────────── JASC text I/O ───────────────────────────────────

def encode_jasc(colors: List[Color]) -> str:
    """Return JASC-PAL 0100 text for a 16-colour palette."""
    lines = ["JASC-PAL", "0100", "16"]
    n = min(len(colors), 16)
    for i in range(n):
        r, g, b = colors[i]
        lines.append(f"{_gba_clamp(r)} {_gba_clamp(g)} {_gba_clamp(b)}")
    for _ in range(16 - n):
        lines.append("0 0 0")
    return "\n".join(lines) + "\n"


def decode_jasc(text: str) -> List[Color]:
    """Parse JASC-PAL text into RGB triples.  Returns [] on any failure."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) < 4 or lines[0] != "JASC-PAL":
        return []
    try:
        count = int(lines[2])
    except ValueError:
        return []
    colors: List[Color] = []
    for i in range(count):
        idx = 3 + i
        if idx >= len(lines):
            break
        parts = lines[idx].split()
        if len(parts) < 3:
            continue
        try:
            r, g, b = int(parts[0]), int(parts[1]), int(parts[2])
            colors.append((r, g, b))
        except ValueError:
            pass
    while len(colors) < 16:
        colors.append((0, 0, 0))
    return colors[:16]


# ────────────────────────── atomic file writes ──────────────────────────────

def _atomic_write_bytes(path: str, data: bytes) -> bool:
    """Write `data` to `path` via temp+rename so a partial write never
    appears on disk.  Byte-equality short-circuit avoids dirtying mtime
    when the file already contains the same bytes.

    Returns True on success.  On any failure, the `.tmp` is cleaned up
    so we never leave garbage behind.
    """
    try:
        if os.path.isfile(path):
            try:
                with open(path, "rb") as f:
                    if f.read() == data:
                        return True
            except OSError:
                pass
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        try:
            with open(tmp, "wb") as f:
                f.write(data)
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


def _atomic_write_text(path: str, text: str) -> bool:
    """Same atomic-rename pattern as `_atomic_write_bytes` but for text."""
    return _atomic_write_bytes(path, text.encode("utf-8"))


# ────────────────────────── public API ──────────────────────────────────────

def write_palette_pair(
    gbapal_path: str,
    pal_path: str,
    colors: List[Color],
) -> Tuple[bool, bool]:
    """Write a palette to BOTH disk representations atomically.

    Args:
        gbapal_path: absolute path to the binary `.gbapal` file the
            build INCBINs (e.g. `graphics/.../boy.gbapal`).
        pal_path:    absolute path to the JASC text sibling (e.g.
            `graphics/.../boy.pal`).
        colors:      16-entry list of RGB triples.  Shorter lists are
            padded with black; longer lists are truncated.

    Returns:
        (gbapal_ok, pal_ok) — True for each file that successfully
        landed on disk (or was already byte-identical).

    Caller is responsible for ensuring the paths point at the correct
    project locations — this module doesn't infer paths from tag names.
    """
    gba_bytes = encode_gbapal(colors)
    jasc_text = encode_jasc(colors)
    ok_gba = _atomic_write_bytes(gbapal_path, gba_bytes)
    ok_pal = _atomic_write_text(pal_path, jasc_text)
    return ok_gba, ok_pal


def pal_sibling_for_gbapal(gbapal_path: str) -> str:
    """Return the JASC `.pal` path that should sit next to a `.gbapal`.

    Pure path manipulation, no I/O.
    """
    base, _ = os.path.splitext(gbapal_path)
    return base + ".pal"


def ensure_pal_sibling(gbapal_path: str) -> bool:
    """If a `.gbapal` exists but its `.pal` sibling doesn't, create the
    sibling from the binary content.  Idempotent and garbage-free:

      - If `.gbapal` doesn't exist: do nothing (no `.pal` invented from
        thin air for absent palettes).
      - If `.pal` already exists: do nothing (don't overwrite user-edited
        JASC content with a fresh derivation from the binary).
      - If the `.gbapal` is corrupt (contains JASC text from the prior
        save-path bug), recover by parsing the JASC content and writing
        BOTH files cleanly.

    Returns True if the project-open scan left the pair in a healthy
    state (existed already, or successfully created/repaired); False
    only if a read/write operation hard-failed.
    """
    if not os.path.isfile(gbapal_path):
        return True  # not a palette we manage — nothing to heal
    pal_path = pal_sibling_for_gbapal(gbapal_path)
    try:
        with open(gbapal_path, "rb") as f:
            raw = f.read()
    except OSError:
        return False

    # Detect the corrupt-by-prior-bug case: .gbapal contains JASC text.
    if raw[:4] == b"JASC":
        try:
            jasc_text = raw.decode("utf-8", errors="replace")
        except Exception:
            jasc_text = ""
        colors = decode_jasc(jasc_text)
        if not colors:
            return False
        # Write the real binary back to .gbapal, ensure .pal sibling.
        ok_gba, ok_pal = write_palette_pair(gbapal_path, pal_path, colors)
        return ok_gba and ok_pal

    # Healthy binary .gbapal.  Create .pal sibling only if missing.
    if os.path.isfile(pal_path):
        return True
    colors = decode_gbapal(raw)
    if not colors:
        return False
    return _atomic_write_text(pal_path, encode_jasc(colors))


def read_palette_pair(gbapal_path: str) -> Optional[List[Color]]:
    """Read whichever file in the pair represents the current state,
    preferring the JASC `.pal` sibling when it exists.

    Returns 16 RGB triples, or None on read failure.

    Use this in the editor's load path so that whatever was last saved
    (via `write_palette_pair`) is reflected accurately.
    """
    pal_path = pal_sibling_for_gbapal(gbapal_path)
    if os.path.isfile(pal_path):
        try:
            with open(pal_path, encoding="utf-8") as f:
                colors = decode_jasc(f.read())
            if colors:
                return colors
        except OSError:
            pass
    if os.path.isfile(gbapal_path):
        try:
            with open(gbapal_path, "rb") as f:
                raw = f.read()
            if raw[:4] == b"JASC":
                # Corrupt .gbapal containing JASC text — read as JASC.
                return decode_jasc(raw.decode("utf-8", errors="replace"))
            return decode_gbapal(raw)
        except OSError:
            pass
    return None
