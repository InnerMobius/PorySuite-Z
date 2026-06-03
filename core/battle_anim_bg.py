"""Assemble a battle-animation BACKGROUND into a QPixmap.

A large class of moves (Surf, Cosmic Power, Sandstorm, Psychic, Ice/Aurora,
Dark, Ghost, Dig's scanline, …) is mostly a full-screen scrolling BACKGROUND,
not sprites — so they showed "nothing" in the preview. The engine's BG-load
tasks are stubbed (no VRAM), but the project keeps the uncompressed GBA BG
data on disk:

  graphics/battle_anims/backgrounds/<name>.4bpp     (8x8 4bpp tiles)
  graphics/battle_anims/backgrounds/<name>.bin      (32x32 tilemap, 2 bytes/cell)
  graphics/battle_anims/backgrounds/<name>.gbapal   (16 BGR555 colours)

This module maps a ``fadetobg``/``changebg`` BG id (e.g. ``BG_COSMIC``) to those
files (via the project's gBattleAnimBackgroundTable + the INCBIN paths) and
assembles the 256x256 background image. The tab draws it behind the mons,
scrolled by the engine's BG-scroll globals.

Pure stdlib parsing + PyQt image assembly. Results are cached by the caller.
"""

from __future__ import annotations

import os
import re
from typing import Dict, Optional, Tuple

from PyQt6.QtGui import QImage, QPixmap, qRgb

# [BG_X] = {gImage, gPalette, gTilemap}
_TABLE = re.compile(
    r"\[\s*(BG_[A-Z0-9_]+)\s*\]\s*=\s*\{\s*"
    r"([A-Za-z0-9_]+)\s*,\s*([A-Za-z0-9_]+)\s*,\s*([A-Za-z0-9_]+)\s*\}")
# gVar[] = INCBIN_U32("graphics/battle_anims/backgrounds/NAME.EXT.lz")
_INCBIN = re.compile(
    r'(g[A-Za-z0-9_]+)\[\]\s*=\s*INCBIN_\w+\(\s*"([^"]*backgrounds/[^"]+)"')


def _gfx_var_files(project_root: str) -> Dict[str, str]:
    """Map each gBattleAnimBg* symbol → its backgrounds/<name>.<ext> base path
    (the .lz stripped, so callers can pick .4bpp/.bin/.gbapal)."""
    out: Dict[str, str] = {}
    gfx = os.path.join(project_root, "src", "graphics.c")
    if not os.path.isfile(gfx):
        return out
    text = open(gfx, encoding="utf-8", errors="replace").read()
    for m in _INCBIN.finditer(text):
        var, path = m.group(1), m.group(2)
        # strip the trailing .lz and the type suffix to get the on-disk base
        rel = path[:-3] if path.endswith(".lz") else path     # .../cosmic.4bpp
        out[var] = os.path.join(project_root, *rel.split("/"))
    return out


def parse_bg_map(project_root: str) -> Dict[str, Tuple[str, str, str]]:
    """BG id constant → (image_path, palette_path, tilemap_path) on disk."""
    out: Dict[str, Tuple[str, str, str]] = {}
    vars_ = _gfx_var_files(project_root)
    # The table lives in a data header; scan the likely files.
    for rel in ("src/data/battle_anim.h", "src/battle_anim.c"):
        p = os.path.join(project_root, *rel.split("/"))
        if not os.path.isfile(p):
            continue
        text = open(p, encoding="utf-8", errors="replace").read()
        for m in _TABLE.finditer(text):
            bg, img, pal, tmap = m.groups()
            if img in vars_ and pal in vars_ and tmap in vars_:
                out[bg] = (vars_[img], vars_[pal], vars_[tmap])
    return out


def _read_palette(path: str):
    raw = open(path, "rb").read()
    pal = []
    for i in range(min(16, len(raw) // 2)):
        c = raw[i * 2] | (raw[i * 2 + 1] << 8)          # BGR555
        r = (c & 31) << 3
        g = ((c >> 5) & 31) << 3
        b = ((c >> 10) & 31) << 3
        pal.append(qRgb(r | r >> 5, g | g >> 5, b | b >> 5))
    while len(pal) < 16:
        pal.append(qRgb(0, 0, 0))
    return pal


def assemble_bg(image_path: str, palette_path: str,
                tilemap_path: str) -> Optional[QPixmap]:
    """Compose the 4bpp tiles + tilemap + palette into a background QPixmap
    (typically 256x256). Returns None if any file is missing."""
    for p in (image_path, palette_path, tilemap_path):
        if not os.path.isfile(p):
            return None
    tiles = open(image_path, "rb").read()
    tmap = open(tilemap_path, "rb").read()
    pal = _read_palette(palette_path)

    cells = len(tmap) // 2
    cols = 32
    rows = max(1, cells // cols)
    img = QImage(cols * 8, rows * 8, QImage.Format.Format_RGB32)
    ntiles = len(tiles) // 32

    for cy in range(rows):
        for cx in range(cols):
            idx = cy * cols + cx
            if idx >= cells:
                break
            entry = tmap[idx * 2] | (tmap[idx * 2 + 1] << 8)
            tile = entry & 0x3FF
            hflip = (entry >> 10) & 1
            vflip = (entry >> 11) & 1
            if tile >= ntiles:
                tile = 0
            base = tile * 32
            for py in range(8):
                sy = 7 - py if vflip else py
                row = base + sy * 4
                for px in range(8):
                    sx = 7 - px if hflip else px
                    byte = tiles[row + (sx >> 1)]
                    pix = (byte >> 4) if (sx & 1) else (byte & 0xF)
                    img.setPixel(cx * 8 + px, cy * 8 + py, pal[pix])
    return QPixmap.fromImage(img)
