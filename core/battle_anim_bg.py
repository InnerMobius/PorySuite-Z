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

from PyQt6.QtGui import QImage, QPixmap, qRgb, qRgba

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


# ── task-loaded backgrounds: parsed from the task's OWN source ──────────────
# A class of moves builds a full-screen BG inside a createvisualtask (Surf's
# water, Sandstorm's dust, Scary Face, Attract's hearts) rather than via
# fadetobg. There is NO hardcoded task→file table: we READ THE TASK'S C BODY and
# extract whichever graphics it loads, so a renamed / duplicated / brand-new BG
# task works identically (engine-accurate, dup-proof). The load CALL fixes each
# asset's role regardless of the symbol's name:
#   AnimLoadCompressedBgGfx(bgId, IMG, off) / LoadBgTiles(bgId, IMG, …)  -> tiles
#   AnimLoadCompressedBgTilemap(bgId, TMAP)                              -> tilemap
#   LoadCompressedPalette(PAL, …) / LoadPalette(&PAL, …)                 -> palette
# Side-specific tilemaps (…Player / …Opponent) are kept apart so the right one is
# picked by who is attacking. Symbols resolve to files via the project INCBINs.

_INCBIN_DEF = re.compile(r'\b(\w+)\s*\[\s*\]\s*=\s*INCBIN_\w+\(\s*"([^"]+)"')
_LOAD_GFX = re.compile(
    r'\b(?:AnimLoadCompressedBgGfx|LoadBgTiles)\s*\(\s*[^,]+,\s*&?(\w+)')
_LOAD_TMAP = re.compile(r'\bAnimLoadCompressedBgTilemap\s*\(\s*[^,]+,\s*&?(\w+)')
_LOAD_PAL = re.compile(r'\b(?:LoadCompressedPalette|LoadPalette)\s*\(\s*&?(\w+)')

_incbin_cache: Dict[str, Dict[str, str]] = {}
_taskbg_cache: Dict[Tuple[str, str], Optional[dict]] = {}


def clear_caches() -> None:
    """Drop the INCBIN + task-BG-parse caches so a reload / project switch / F5
    re-reads the source from disk."""
    _incbin_cache.clear()
    _taskbg_cache.clear()


def _incbin_map(project_root: str) -> Dict[str, str]:
    """{symbol: absolute on-disk path} for every INCBIN graphics define in the
    project, with the trailing compression suffix (.lz/.smol/.rl) stripped so the
    path is the uncompressed asset the assembler reads (water.4bpp, …). Scans the
    top-level src/*.c (graphics.c et al.) plus src/data/graphics/**/*.h."""
    cached = _incbin_cache.get(project_root)
    if cached is not None:
        return cached
    out: Dict[str, str] = {}
    files = []
    src = os.path.join(project_root, "src")
    if os.path.isdir(src):
        files += [os.path.join(src, f) for f in os.listdir(src)
                  if f.endswith(".c")]
    gdir = os.path.join(src, "data", "graphics")
    if os.path.isdir(gdir):
        for base, _d, fs in os.walk(gdir):
            files += [os.path.join(base, f) for f in fs if f.endswith(".h")]
    for p in files:
        try:
            txt = open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        if "INCBIN" not in txt:
            continue
        for m in _INCBIN_DEF.finditer(txt):
            sym, path = m.group(1), m.group(2)
            for suf in (".lz", ".smol", ".rl"):
                if path.endswith(suf):
                    path = path[:-len(suf)]
                    break
            out.setdefault(sym, os.path.join(project_root, *path.split("/")))
    _incbin_cache[project_root] = out
    return out


def _task_body(project_root: str, task: str) -> str:
    """The C source body (brace-balanced) of a battle-anim task function, or ''."""
    src = os.path.join(project_root, "src")
    if not os.path.isdir(src) or not task:
        return ""
    sig = re.compile(r'\bvoid\s+' + re.escape(task) + r'\s*\(')
    for f in sorted(os.listdir(src)):
        if not (f.startswith("battle_anim") and f.endswith(".c")):
            continue
        try:
            txt = open(os.path.join(src, f), encoding="utf-8",
                       errors="replace").read()
        except OSError:
            continue
        for m in sig.finditer(txt):
            i = txt.find("{", m.end())
            if i < 0:
                continue
            # Skip a forward DECLARATION (`void NAME(...);`) — its `)` is followed
            # by `;` before any `{`. Only the DEFINITION (`) {`) has the body.
            semi = txt.find(";", m.end())
            if 0 <= semi < i:
                continue
            depth = 0
            for j in range(i, len(txt)):
                if txt[j] == "{":
                    depth += 1
                elif txt[j] == "}":
                    depth -= 1
                    if depth == 0:
                        return txt[i:j + 1]
            return txt[i:]
    return ""


def _task_body_chain(project_root: str, task: str, max_depth: int = 6) -> str:
    """The task's body PLUS the bodies of its state-machine steps — functions it
    assigns to ``gTasks[taskId].func`` or calls directly as ``X(taskId)`` — all
    concatenated. Lets BG detection see a load done in a LATER step: Stats Change
    loads its arrow mask in ``StatsChangeAnimation_Step2``, reached via
    ``AnimTask_StatsChange`` → ``InitStatsChangeAnimation`` → ``…_Step1`` →
    ``…_Step2``. Bounded + cycle-guarded; only follows functions that have a
    battle-anim body. Harmless for non-nested tasks (their loads are at depth 0)."""
    seen: set = set()
    parts: List[str] = []

    def _walk(fn: str, depth: int):
        if depth > max_depth or fn in seen:
            return
        seen.add(fn)
        body = _task_body(project_root, fn)
        if not body:
            return
        parts.append(body)
        for nxt in re.findall(r'\.func\s*=\s*([A-Za-z_]\w*)', body):
            _walk(nxt, depth + 1)
        for nxt in re.findall(r'\b([A-Za-z_]\w*)\s*\(\s*taskId\s*\)', body):
            _walk(nxt, depth + 1)

    _walk(task, 0)
    return "\n".join(parts)


def _parse_task_bg(project_root: str, task: str) -> Optional[dict]:
    """{image, pal, tmap / tmap_player / tmap_opponent} of the SYMBOLS a task
    loads, or None if it loads no BG. Driven entirely by the task's source body
    (and its state-machine steps — see ``_task_body_chain``)."""
    key = (project_root, task)
    if key in _taskbg_cache:
        return _taskbg_cache[key]
    body = _task_body_chain(project_root, task)
    res = None
    if body:
        imgs = _LOAD_GFX.findall(body)
        tmaps = [t for t in _LOAD_TMAP.findall(body) if "Contest" not in t]
        pals = _LOAD_PAL.findall(body)
        if imgs and tmaps:
            res = {"image": imgs[0], "pal": pals[0] if pals else None}
            tp = next((t for t in tmaps if "Player" in t), None)
            to = next((t for t in tmaps if "Opponent" in t), None)
            if tp or to:
                res["tmap_player"] = tp or to
                res["tmap_opponent"] = to or tp
            else:
                res["tmap"] = tmaps[0]
    _taskbg_cache[key] = res
    return res


def task_loads_bg(project_root: str, task: str) -> bool:
    """True if ``task``'s source body loads a full-screen background."""
    return _parse_task_bg(project_root, task) is not None


def task_bg_files(project_root: str, task: str, player_attacks: bool = True):
    """(image, palette, tilemap) on-disk paths for a task-loaded BG, picking the
    player/opponent tilemap by who attacks. (None, None, None) if not a BG task."""
    info = _parse_task_bg(project_root, task)
    if not info:
        return None, None, None
    syms = _incbin_map(project_root)
    img = syms.get(info.get("image"))
    pal = syms.get(info.get("pal")) if info.get("pal") else None
    tmap_sym = info.get("tmap") or (info.get("tmap_player") if player_attacks
                                    else info.get("tmap_opponent"))
    tmap = syms.get(tmap_sym)
    return img, pal, tmap


def assemble_task_bg(project_root: str, task: str,
                     player_attacks: bool = True,
                     screen_size: int = -1) -> Optional[QPixmap]:
    """Assemble a task-loaded background (Surf's water, Sandstorm's dust), with the
    side-specific tilemap chosen by who's attacking. ``screen_size`` is the
    engine's BG SCREEN_SIZE (picks the screenblock layout — Surf is 512x256)."""
    img, pal, tmap = task_bg_files(project_root, task, player_attacks)
    if not (img and pal and tmap):
        return None
    return assemble_bg(img, pal, tmap, screen_size)


def bg_files(project_root: str, bg_id: str, bg_id_map=None,
             player_attacks: bool = True):
    """(image_path, palette_path, tilemap_path) for ANY bg id — a 'task:<fn>' task
    BG (Surf) resolved by reading the task source, or a fadetobg id (psychic)
    resolved from ``bg_id_map``. (None, None, None) if unknown."""
    if bg_id.startswith("task:"):
        return task_bg_files(project_root, bg_id[5:], player_attacks)
    files = (bg_id_map or {}).get(bg_id)
    return files if files else (None, None, None)


def read_palette_bgr555(path: str):
    """The first 16 palette entries as raw BGR555 u16 — what the engine's palette
    buffer holds. Used to LOAD the real BG palette into the engine so the move's
    own tasks animate it (and the result is read back). Returns a 16-int list."""
    try:
        raw = open(path, "rb").read()
    except Exception:
        return [0] * 16
    out = [(raw[i * 2] | (raw[i * 2 + 1] << 8)) if i * 2 + 1 < len(raw) else 0
           for i in range(16)]
    return out


def bgr555_to_qrgb(c: int) -> int:
    """A single BGR555 word → QRgb (5→8 bit expanded), opaque."""
    r = (c & 31) << 3
    g = ((c >> 5) & 31) << 3
    b = ((c >> 10) & 31) << 3
    return qRgb(r | r >> 5, g | g >> 5, b | b >> 5)


def _read_palette(path: str):
    pal = [bgr555_to_qrgb(c) for c in read_palette_bgr555(path)]
    while len(pal) < 16:
        pal.append(qRgb(0, 0, 0))
    return pal


def _bg_tile_dims(cells: int, screen_size: int):
    """(cols, rows) in TILES for the BG, given the cell count + the GBA SCREEN_SIZE
    the move set (0=256x256, 1=512x256, 2=256x512, 3=512x512). A 512-wide BG stores
    its tilemap as 32x32 screenblocks laid left-to-right then top-to-bottom, so the
    layout — NOT just a flat 32-col strip — depends on the size. -1/unknown: assume
    32 wide (256-wide BG) as a fallback."""
    if screen_size == 1:
        return 64, 32
    if screen_size == 2:
        return 32, 64
    if screen_size == 3:
        return 64, 64
    if screen_size == 0:
        return 32, 32
    return 32, max(1, cells // 32)         # unknown → linear 256-wide


def _tilemap_cell(tx: int, ty: int, cols: int) -> int:
    """Cell index in the tilemap for tile (tx, ty), honouring the GBA's 32x32
    SCREENBLOCK layout: blocks are 1024 cells each, arranged left-to-right then
    top-to-bottom. (For a 32-wide BG this reduces to the plain ty*32+tx.)"""
    blocks_wide = max(1, (cols + 31) // 32)
    block = (ty // 32) * blocks_wide + (tx // 32)
    return block * 1024 + (ty % 32) * 32 + (tx % 32)


def tilemap_palette_slot(tilemap_path: str) -> int:
    """The GBA palette number (0..15) this BG's tilemap references — bits 12-15 of
    its cells. The engine loads + ANIMATES the BG palette at this slot (Surf=8,
    psychic=2), so it's the slot to load our palette into + read back. 0 if
    unreadable. Reads it from the tilemap itself — no per-move assumption."""
    try:
        d = open(tilemap_path, "rb").read()
    except Exception:
        return 0
    for i in range(0, len(d) - 1, 2):
        cell = d[i] | (d[i + 1] << 8)
        if cell & 0x3FF:                 # first non-blank tile's palette number
            return (cell >> 12) & 0xF
    return 0


def assemble_bg_indexed(image_path: str, palette_path: str,
                        tilemap_path: str, screen_size: int = -1):
    """Compose the 4bpp tiles + tilemap into an INDEXED (Format_Indexed8) QImage
    whose pixels are 0..15 palette indices, with the palette as its colour table.
    Returns (QImage, pal, slot) or (None, None, 0). ``screen_size`` picks the
    screenblock layout (wide BGs side-by-side). ``slot`` is the GBA palette number
    the tilemap uses (for the engine palette read-back). BG palette index 0 is
    TRANSPARENT (GBA rule) so the scene/sky shows above the water, not an opaque
    backdrop colour."""
    for p in (image_path, palette_path, tilemap_path):
        if not os.path.isfile(p):
            return None, None, 0
    tiles = open(image_path, "rb").read()
    tmap = open(tilemap_path, "rb").read()
    pal = _read_palette(palette_path)
    pal[0] = qRgba(0, 0, 0, 0)           # BG colour 0 is transparent on GBA
    slot = tilemap_palette_slot(tilemap_path)

    cells = len(tmap) // 2
    cols, rows = _bg_tile_dims(cells, screen_size)
    img = QImage(cols * 8, rows * 8, QImage.Format.Format_Indexed8)
    # GBA BG palette index 0 is TRANSPARENT (shows the scene/backdrop below) — it's
    # not the colour stored in .pal. Drawing it opaque is what painted Surf's
    # "green sky band" (index 0 = a pale green) over the area that should be the
    # battle scene above the wave. Make it transparent so the layer below shows.
    ct = list(pal)
    ct[0] = qRgba(0, 0, 0, 0)
    img.setColorTable(ct)
    ntiles = len(tiles) // 32

    for cy in range(rows):
        for cx in range(cols):
            idx = _tilemap_cell(cx, cy, cols)
            if idx >= cells:
                continue
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
                    img.setPixel(cx * 8 + px, cy * 8 + py, pix)
    return img, pal, slot


def assemble_bg(image_path: str, palette_path: str,
                tilemap_path: str, screen_size: int = -1) -> Optional[QPixmap]:
    """Compose the 4bpp tiles + tilemap + palette into a background QPixmap.
    ``screen_size`` picks the screenblock layout (see assemble_bg_indexed)."""
    img, _pal, _slot = assemble_bg_indexed(image_path, palette_path, tilemap_path,
                                           screen_size)
    return QPixmap.fromImage(img) if img is not None else None
