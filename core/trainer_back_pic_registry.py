"""Read and register trainer BACK pics in a pokefirered project.

Back pics differ from front pics in several ways that this module owns:

  • They are MULTI-FRAME vertical strips (64 x 64*N): frame 0 is the idle
    pose, frames 1..N-1 are the throw animation.
  • Their graphics are UNCOMPRESSED (``INCBIN_U8`` of a ``.4bpp``), not the
    compressed ``INCBIN_U32`` of a ``.4bpp.lz`` the front pics use.
  • The sprite-table size field is ``frames * 0x800`` (a 64x64 4bpp frame is
    0x800 bytes), so it varies (0x2000 for 4 frames, 0x2800 for 5).
  • They carry a THROW ANIMATION table (``back_pic_anims.h``) the front pics
    have no equivalent of.
  • The back-pic tables use DESIGNATED-INITIALIZER Coords and have NO trailing
    comma on the last entry, so appends add the comma to the old last row.

Registering a back pic spans FIVE source files (relative to project root):
  • ``graphics/trainers/back_pics/<base>_back_pic.png``   (the strip)
  • ``graphics/trainers/palettes/<base>_back_pic.pal``    (committable source)
  • ``src/data/graphics/trainers.h``                      (the two INCBINs)
  • ``src/data/trainer_graphics/back_pic_tables.h``       (Coords/Table/PalTable)
  • ``src/data/trainer_graphics/back_pic_anims.h``        (the throw anim + ptr)
  • ``include/constants/trainers.h``                      (TRAINER_BACK_PIC_*)

All writes are byte-equality-guarded, so a no-op re-run produces no git diffs.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from core.file_io import write_text_if_changed

Color = Tuple[int, int, int]

FRAME_BYTES = 0x800   # one 64x64 4bpp frame


# ── path helpers ──────────────────────────────────────────────────────────


def _trainers_h(root: str) -> str:
    return os.path.join(root, "src", "data", "graphics", "trainers.h")


def _back_pic_tables_h(root: str) -> str:
    return os.path.join(
        root, "src", "data", "trainer_graphics", "back_pic_tables.h")


def _back_pic_anims_h(root: str) -> str:
    return os.path.join(
        root, "src", "data", "trainer_graphics", "back_pic_anims.h")


def _constants_trainers_h(root: str) -> str:
    return os.path.join(root, "include", "constants", "trainers.h")


def _back_pic_dir(root: str) -> str:
    return os.path.join(root, "graphics", "trainers", "back_pics")


def _palettes_dir(root: str) -> str:
    return os.path.join(root, "graphics", "trainers", "palettes")


# ── name derivation ───────────────────────────────────────────────────────


def derive_back_names_from_filename(png_filename: str) -> Tuple[str, str, str]:
    """Map a PNG filename to (constant, symbol, base_name) for a back pic.

      • Constant:  ``TRAINER_BACK_PIC_DARK_LINK``   (UPPER_SNAKE_CASE)
      • Symbol:    ``DarkLink``                      (CamelCase)
      • Base:      ``dark_link``                      (snake_case, file naming)

    Strips ``.png`` and an optional ``_back_pic`` suffix from the basename.
    """
    base = os.path.basename(png_filename)
    if base.lower().endswith(".png"):
        base = base[:-4]
    if base.lower().endswith("_back_pic"):
        base = base[:-len("_back_pic")]
    base = re.sub(r"[^a-zA-Z0-9_]+", "_", base).strip("_").lower()
    base = re.sub(r"_{2,}", "_", base) or "new_trainer"

    constant = f"TRAINER_BACK_PIC_{base.upper()}"
    parts = [p for p in base.split("_") if p]
    symbol = "".join(p[:1].upper() + p[1:].lower() for p in parts) or "NewTrainer"
    return constant, symbol, base


# ── reading the existing back pics ─────────────────────────────────────────


@dataclass
class BackPicEntry:
    constant: str                 # TRAINER_BACK_PIC_RED
    pic_id: int                   # 0
    symbol: str                   # Red (from gTrainerBackPic_Red)
    png_path: str                 # abs path to <base>_back_pic.png (may not exist)
    pal_path: str                 # abs path to the .pal source (may not exist)
    frames: int                   # frame count (size // 0x800), >=1
    throw_anim: List[Tuple[int, int]] = field(default_factory=list)  # (idx, dur)


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def parse_back_pic_anims(root: str) -> Dict[str, List[Tuple[int, int]]]:
    """Return {symbol: [(frame_index, duration), ...]} from back_pic_anims.h.

    Parses each ``sAnimCmd_<Symbol>_1[]`` block's ``ANIMCMD_FRAME(idx, dur)``
    rows (the throw sequence). ANIMCMD_END terminates the block.
    """
    path = _back_pic_anims_h(root)
    if not os.path.isfile(path):
        return {}
    text = _read(path)
    out: Dict[str, List[Tuple[int, int]]] = {}
    blk = re.compile(
        r"sAnimCmd_(\w+?)_1\[\]\s*=\s*\{(.*?)\}", re.DOTALL)
    frame = re.compile(r"ANIMCMD_FRAME\(\s*(\d+)\s*,\s*(\d+)\s*\)")
    for m in blk.finditer(text):
        sym, body = m.group(1), m.group(2)
        out[sym] = [(int(a), int(b)) for a, b in frame.findall(body)]
    return out


def parse_back_pics(root: str) -> List[BackPicEntry]:
    """Return the project's back pics, ordered by pic id.

    Joins four sources: the TRAINER_BACK_PIC_* constants (name->id), the
    gTrainerBackPicTable (id-order -> graphics symbol + size), the graphics
    INCBINs (symbol -> .4bpp path), the palette table (id-order -> palette
    symbol) and the palette INCBINs (palette symbol -> .gbapal.lz path). The
    PNG/.pal *sources* sit next to those build artifacts (same base, .png/.pal).
    """
    th = _trainers_h(root)
    bt = _back_pic_tables_h(root)
    ct = _constants_trainers_h(root)
    for p in (th, bt, ct):
        if not os.path.isfile(p):
            return []
    th_text, bt_text, ct_text = _read(th), _read(bt), _read(ct)

    # constants: name -> id, and id -> name
    id_to_const: Dict[int, str] = {}
    for m in re.finditer(r"#define\s+(TRAINER_BACK_PIC_\w+)\s+(\d+)", ct_text):
        id_to_const[int(m.group(2))] = m.group(1)

    # graphics INCBINs: symbol -> .4bpp path
    gfx_path: Dict[str, str] = {}
    for m in re.finditer(
            r'gTrainerBackPic_(\w+)\[\]\s*=\s*INCBIN_U8\("([^"]+)"\)', th_text):
        gfx_path[m.group(1)] = m.group(2)

    # palette INCBINs: palette symbol -> path (.gbapal.lz)
    pal_path: Dict[str, str] = {}
    for m in re.finditer(
            r'(gTrainerPalette_\w+)\[\]\s*=\s*INCBIN_U\d+\("([^"]+)"\)', th_text):
        pal_path[m.group(1)] = m.group(2)

    # sprite table: ordered (symbol, size, tag)
    tbl = re.search(
        r"gTrainerBackPicTable\[\]\s*=\s*\{(.*?)\};", bt_text, re.DOTALL)
    sheet = []
    if tbl:
        for m in re.finditer(
                r"gTrainerBackPic_(\w+)\s*,\s*(0x[0-9A-Fa-f]+|\d+)\s*,\s*(\d+)",
                tbl.group(1)):
            sheet.append((m.group(1), int(m.group(2), 0), int(m.group(3))))

    # palette table: tag -> palette symbol
    ptbl = re.search(
        r"gTrainerBackPicPaletteTable\[\]\s*=\s*\{(.*?)\};", bt_text, re.DOTALL)
    pal_by_tag: Dict[int, str] = {}
    if ptbl:
        for m in re.finditer(
                r"(gTrainerPalette_\w+)\s*,\s*(\d+)", ptbl.group(1)):
            pal_by_tag[int(m.group(2))] = m.group(1)

    anims = parse_back_pic_anims(root)

    def _src(build_path: str, *exts: str) -> str:
        """Build-artifact path -> committable source path (same base, new ext)."""
        for e in (".4bpp.lz", ".4bpp", ".gbapal.lz", ".gbapal", ".png", ".pal"):
            if build_path.endswith(e):
                build_path = build_path[:-len(e)]
                break
        return build_path + exts[0]

    out: List[BackPicEntry] = []
    for sym, size, tag in sheet:
        png_rel = _src(gfx_path.get(sym, ""), ".png") if sym in gfx_path else ""
        pal_sym = pal_by_tag.get(tag, "")
        pal_rel = _src(pal_path.get(pal_sym, ""), ".pal") if pal_sym in pal_path else ""
        out.append(BackPicEntry(
            constant=id_to_const.get(tag, f"TRAINER_BACK_PIC_{tag}"),
            pic_id=tag,
            symbol=sym,
            png_path=os.path.join(root, png_rel) if png_rel else "",
            pal_path=os.path.join(root, pal_rel) if pal_rel else "",
            frames=max(1, size // FRAME_BYTES),
            throw_anim=anims.get(sym, []),
        ))
    out.sort(key=lambda e: e.pic_id)
    return out


# ── insertion helpers ──────────────────────────────────────────────────────


def _append_no_trailing_comma(text: str, marker: str, new_entry: str) -> str:
    """Append ``new_entry`` as the last row of a ``marker ... };`` brace block
    whose LAST existing entry has NO trailing comma (the back-pic table style).
    A comma is added to the old last row; the new row gets no trailing comma."""
    i = text.find(marker)
    if i < 0:
        raise ValueError(f"marker not found: {marker!r}")
    close = text.find("};", i)
    if close < 0:
        raise ValueError(f"closing brace not found for {marker!r}")
    cut = close
    while cut > i and text[cut - 1] in " \t\r\n":
        cut -= 1
    return text[:cut] + ",\n" + new_entry.rstrip("\n") + text[cut:]


def _insert_before(text: str, marker: str, block: str) -> str:
    """Insert ``block`` immediately before the line containing ``marker``."""
    i = text.find(marker)
    if i < 0:
        raise ValueError(f"marker not found: {marker!r}")
    line_start = text.rfind("\n", 0, i) + 1
    if not block.endswith("\n"):
        block = block + "\n"
    return text[:line_start] + block + text[line_start:]


def _insert_after_last_define(text: str, new_define_line: str) -> str:
    pat = re.compile(r"^#define\s+TRAINER_BACK_PIC_\w+\s+\d+\n", re.MULTILINE)
    matches = list(pat.finditer(text))
    if not matches:
        raise ValueError("No TRAINER_BACK_PIC_* defines found in trainers.h")
    last = matches[-1]
    return text[:last.end()] + new_define_line + text[last.end():]


def _next_back_id(text: str) -> int:
    ids = [int(m.group(1)) for m in
           re.finditer(r"#define\s+TRAINER_BACK_PIC_\w+\s+(\d+)", text)]
    return (max(ids) + 1) if ids else 0


def _format_define_line(constant: str, pic_id: int, sample_text: str) -> str:
    pat = re.compile(r"#define\s+(TRAINER_BACK_PIC_\w+)\s+\d+")
    longest = max((m.group(1) for m in pat.finditer(sample_text)),
                  key=len, default=constant)
    width = max(len(constant), len(longest))
    pad = max(2, width - len(constant) + 2)
    return f"#define {constant}{' ' * pad}{pic_id}\n"


def _default_throw_anim(frames: int) -> List[Tuple[int, int]]:
    """A sensible default throw sequence for a strip with `frames` frames:
    hold frame 1 (wind-up), step through the middle frames, hold the last
    (release), then snap back to the idle frame 0. Mirrors vanilla pacing."""
    if frames <= 1:
        return [(0, 1)]
    seq: List[Tuple[int, int]] = [(1, 20)]
    for i in range(2, frames - 1):
        seq.append((i, 8))
    if frames - 1 >= 2:
        seq.append((frames - 1, 24))
    seq.append((0, 1))
    return seq


# ── public API ─────────────────────────────────────────────────────────────


@dataclass
class AddBackPicResult:
    success: bool
    constant: str
    symbol: str
    base_name: str
    pic_id: int
    frames: int
    files_written: List[str]
    error: str = ""


def add_back_pic(
    project_root: str,
    source_png_path: str,
    constant: str,
    symbol: str,
    base_name: str,
    frames: int,
    coord_size: int = 8,
    coord_y_offset: int = 4,
    pal_source_path: Optional[str] = None,
    throw_anim: Optional[List[Tuple[int, int]]] = None,
) -> AddBackPicResult:
    """Register a new trainer BACK pic atomically across all source files.

    ``frames`` is the number of 64x64 frames in the strip (PNG height // 64);
    the sprite-table size becomes ``frames * 0x800``. ``throw_anim`` is an
    optional [(frame_index, duration)] sequence; a default is generated if
    omitted. The PNG is copied to ``back_pics/<base>_back_pic.png`` and the
    palette written to ``palettes/<base>_back_pic.pal`` (committable sources;
    the build regenerates the .4bpp / .gbapal.lz the INCBINs reference)."""
    res = AddBackPicResult(
        success=False, constant=constant, symbol=symbol,
        base_name=base_name, pic_id=-1, frames=frames, files_written=[])

    th_path, bt_path = _trainers_h(project_root), _back_pic_tables_h(project_root)
    ba_path, ct_path = _back_pic_anims_h(project_root), _constants_trainers_h(project_root)
    for required in (th_path, bt_path, ba_path, ct_path):
        if not os.path.isfile(required):
            res.error = f"Required source file not found: {required}"
            return res
    if not os.path.isfile(source_png_path):
        res.error = f"Source PNG not found: {source_png_path}"
        return res
    frames = max(1, int(frames))
    res.frames = frames

    try:
        th_text, bt_text = _read(th_path), _read(bt_path)
        ba_text, ct_text = _read(ba_path), _read(ct_path)
    except OSError as exc:
        res.error = f"Could not read project files: {exc}"
        return res

    # conflict checks
    if re.search(rf"#define\s+{re.escape(constant)}\s+\d+", ct_text):
        res.error = f"{constant} already exists. Pick a different name."
        return res
    if re.search(rf"gTrainerBackPic_{re.escape(symbol)}\b", th_text):
        res.error = f"gTrainerBackPic_{symbol} already exists. Pick a different name."
        return res

    # ── assets ────────────────────────────────────────────────────────────
    os.makedirs(_back_pic_dir(project_root), exist_ok=True)
    os.makedirs(_palettes_dir(project_root), exist_ok=True)

    target_png = os.path.join(
        _back_pic_dir(project_root), f"{base_name}_back_pic.png")
    if os.path.abspath(source_png_path) != os.path.abspath(target_png):
        try:
            shutil.copy2(source_png_path, target_png)
        except OSError as exc:
            res.error = f"Could not copy PNG into project: {exc}"
            return res
    res.files_written.append(target_png)

    target_pal = os.path.join(
        _palettes_dir(project_root), f"{base_name}_back_pic.pal")
    try:
        if pal_source_path and os.path.isfile(pal_source_path):
            if os.path.abspath(pal_source_path) != os.path.abspath(target_pal):
                shutil.copy2(pal_source_path, target_pal)
        else:
            from core.trainer_pic_registry import _png_to_jasc_palette
            from ui.palette_utils import write_jasc_pal
            colors = _png_to_jasc_palette(source_png_path)
            if not write_jasc_pal(target_pal, colors):
                res.error = f"Could not write palette: {target_pal}"
                return res
    except Exception as exc:
        res.error = f"Could not generate palette: {exc}"
        return res
    res.files_written.append(target_pal)

    # ── text inserts ────────────────────────────────────────────────────────
    pic_id = _next_back_id(ct_text)
    res.pic_id = pic_id
    size = frames * FRAME_BYTES

    # trainers.h — two INCBINs (graphics uncompressed u8, palette compressed u32)
    th_insert = (
        f'\nconst u8 gTrainerBackPic_{symbol}[] = '
        f'INCBIN_U8("graphics/trainers/back_pics/{base_name}_back_pic.4bpp");\n'
        f'const u32 gTrainerPalette_{symbol}BackPic[] = '
        f'INCBIN_U32("graphics/trainers/palettes/{base_name}_back_pic.gbapal.lz");\n'
    )
    new_th = th_text.rstrip() + "\n" + th_insert

    # back_pic_tables.h — three appends (no trailing comma on last row)
    coords_entry = (
        f"    [{constant}] = {{.size = {int(coord_size)}, "
        f".y_offset = {int(coord_y_offset)}}}")
    sheet_entry = (
        f"    {{ (const u32 *)gTrainerBackPic_{symbol}, 0x{size:X}, {pic_id} }}")
    pal_entry = (
        f"    {{ gTrainerPalette_{symbol}BackPic, {pic_id} }}")
    try:
        new_bt = _append_no_trailing_comma(
            bt_text, "gTrainerBackPicCoords[]", coords_entry)
        new_bt = _append_no_trailing_comma(
            new_bt, "gTrainerBackPicTable[]", sheet_entry)
        new_bt = _append_no_trailing_comma(
            new_bt, "gTrainerBackPicPaletteTable[]", pal_entry)
    except ValueError as exc:
        res.error = f"back_pic_tables.h has unexpected layout: {exc}"
        return res

    # back_pic_anims.h — anim cmd + anims array (before the ptr table), then
    # append the anims array to the ptr table.
    seq = throw_anim if throw_anim else _default_throw_anim(frames)
    anim_rows = "".join(f"    ANIMCMD_FRAME({i}, {d}),\n" for i, d in seq)
    anim_block = (
        f"static const union AnimCmd sAnimCmd_{symbol}_1[] = {{\n"
        f"{anim_rows}"
        f"    ANIMCMD_END\n"
        f"}};\n\n"
        f"const union AnimCmd *const sBackAnims_{symbol}[] = {{\n"
        f"    sAnim_GeneralFrame0,\n"
        f"    sAnimCmd_{symbol}_1\n"
        f"}};\n\n"
    )
    try:
        new_ba = _insert_before(
            ba_text, "gTrainerBackAnimsPtrTable[]", anim_block)
        new_ba = _append_no_trailing_comma(
            new_ba, "gTrainerBackAnimsPtrTable[]", f"    sBackAnims_{symbol}")
    except ValueError as exc:
        res.error = f"back_pic_anims.h has unexpected layout: {exc}"
        return res

    # constants/trainers.h
    try:
        new_ct = _insert_after_last_define(
            ct_text, _format_define_line(constant, pic_id, ct_text))
    except ValueError as exc:
        res.error = f"constants/trainers.h has unexpected layout: {exc}"
        return res

    # ── write all (byte-equality guarded) ──────────────────────────────────
    try:
        for path, new in ((th_path, new_th), (bt_path, new_bt),
                          (ba_path, new_ba), (ct_path, new_ct)):
            if write_text_if_changed(path, new):
                res.files_written.append(path)
    except Exception as exc:
        res.error = f"Could not write source files: {exc}"
        return res

    res.success = True
    return res
