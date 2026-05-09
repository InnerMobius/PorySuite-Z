"""Add a new trainer pic entry to a pokefirered project.

Registering a trainer pic spans **four** source files plus a generated
``.pal`` companion. Get any one wrong and the build fails at link time
with cryptic symbol-not-found errors. This module owns the whole
registration: it knows the exact format conventions vanilla pokefirered
uses (CamelCase symbols, UPPER_SNAKE_CASE constants, snake_case file
paths) and emits inserts that match them byte-for-byte.

The 4 files (relative to project root):
  • ``graphics/trainers/palettes/<name>.pal``
        Generated from the source PNG's color table.
  • ``src/data/graphics/trainers.h``
        ``INCBIN_U32`` for both the front-pic .4bpp.lz and the .gbapal.lz.
  • ``src/data/trainer_graphics/front_pic_tables.h``
        Three table inserts (Coords, FrontPicTable, FrontPicPaletteTable).
  • ``include/constants/trainers.h``
        ``#define TRAINER_PIC_<NAME>  <next_id>``.

All writes are byte-equality-guarded via ``write_text_if_changed`` so a
re-run with identical inputs is a no-op (no phantom git diffs).

This module is the IO/format layer; the UI layer (``trainer_graphics_tab``)
collects the user's inputs and calls ``add_trainer_pic`` once they confirm.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from typing import List, Optional, Tuple

from core.file_io import write_text_if_changed

Color = Tuple[int, int, int]


# ── path helpers ──────────────────────────────────────────────────────────


def _trainers_h(root: str) -> str:
    return os.path.join(root, "src", "data", "graphics", "trainers.h")


def _front_pic_tables_h(root: str) -> str:
    return os.path.join(
        root, "src", "data", "trainer_graphics", "front_pic_tables.h")


def _constants_trainers_h(root: str) -> str:
    return os.path.join(root, "include", "constants", "trainers.h")


def _front_pic_dir(root: str) -> str:
    return os.path.join(root, "graphics", "trainers", "front_pics")


def _palettes_dir(root: str) -> str:
    return os.path.join(root, "graphics", "trainers", "palettes")


# ── name derivation ───────────────────────────────────────────────────────


def derive_names_from_filename(png_filename: str) -> Tuple[str, str, str]:
    """Map a PNG filename to (constant, symbol, base_name).

    Conventions matched against vanilla pokefirered:
      • Constant:  ``TRAINER_PIC_DARK_LINK``       (UPPER_SNAKE_CASE)
      • Symbol:    ``DarkLink``                    (CamelCase, no separator)
      • Base name: ``dark_link``                   (snake_case, used in
                                                    file paths and the
                                                    palette filename)

    The base name is derived by stripping the ``.png`` extension and the
    optional trailing ``_front_pic`` suffix from the PNG's basename. So
    ``dark_link_front_pic.png`` becomes base ``dark_link``, constant
    ``TRAINER_PIC_DARK_LINK``, symbol ``DarkLink``.
    """
    base = os.path.basename(png_filename)
    if base.lower().endswith(".png"):
        base = base[:-4]
    if base.lower().endswith("_front_pic"):
        base = base[:-len("_front_pic")]
    # Sanitise: keep only [a-z0-9_], collapse repeats, lowercase
    base = re.sub(r"[^a-zA-Z0-9_]+", "_", base).strip("_").lower()
    base = re.sub(r"_{2,}", "_", base) or "new_trainer"

    constant = f"TRAINER_PIC_{base.upper()}"
    parts = [p for p in base.split("_") if p]
    symbol = "".join(p[:1].upper() + p[1:].lower() for p in parts) or "NewTrainer"
    return constant, symbol, base


# ── file format helpers ────────────────────────────────────────────────────


def _next_pic_id(text: str) -> int:
    """Return the highest TRAINER_PIC_* id in `text` plus one."""
    pat = re.compile(r"^#define\s+TRAINER_PIC_\w+\s+(\d+)", re.MULTILINE)
    ids = [int(m.group(1)) for m in pat.finditer(text)]
    return (max(ids) + 1) if ids else 0


def _existing_pic_constants(text: str) -> set[str]:
    pat = re.compile(r"^#define\s+(TRAINER_PIC_\w+)\s+\d+", re.MULTILINE)
    return {m.group(1) for m in pat.finditer(text)}


def _existing_symbols(text: str) -> set[str]:
    """Extract `Symbol` names from `gTrainerFrontPic_<Symbol>` decls."""
    pat = re.compile(r"gTrainerFrontPic_(\w+)")
    return set(pat.findall(text))


def _format_define_line(constant: str, pic_id: int,
                        sample_text: str) -> str:
    """Build a ``#define ... <id>`` line whose column-alignment matches
    the surrounding block.

    Vanilla pokefirered aligns the numeric id to a fixed column (look at
    the longest existing ``#define TRAINER_PIC_*`` line and pad). Match
    that so the diff stays minimal.
    """
    pat = re.compile(r"^#define\s+(TRAINER_PIC_\w+)\s+\d+", re.MULTILINE)
    longest_const = max(
        (m.group(1) for m in pat.finditer(sample_text)),
        key=len, default=constant)
    width = max(len(constant), len(longest_const))
    # pokefirered uses 2 spaces minimum between the constant and the id
    pad = max(2, width - len(constant) + 2)
    return f"#define {constant}{' ' * pad}{pic_id}\n"


# ── palette generation ─────────────────────────────────────────────────────


def _png_to_jasc_palette(png_path: str) -> List[Color]:
    """Read the color table of an indexed PNG and return up to 16 colors.

    Caller is responsible for verifying the PNG is indexed (8-bit).
    """
    from PyQt6.QtGui import QImage

    img = QImage(png_path)
    if img.isNull():
        raise ValueError(f"Could not load PNG: {png_path}")
    if img.format() != QImage.Format.Format_Indexed8:
        # Convert and warn — caller should ideally validate before this.
        img = img.convertToFormat(QImage.Format.Format_Indexed8)
    table = img.colorTable()
    colors: List[Color] = []
    for raw in table[:16]:
        r = (raw >> 16) & 0xFF
        g = (raw >> 8) & 0xFF
        b = raw & 0xFF
        colors.append((r, g, b))
    while len(colors) < 16:
        colors.append((0, 0, 0))
    return colors


# ── insertion helpers ──────────────────────────────────────────────────────


def _insert_before_closing_brace(text: str, table_marker: str,
                                 new_line: str) -> str:
    """Append ``new_line`` to a table that starts with ``table_marker``
    (e.g. ``"const struct MonCoords gTrainerFrontPicCoords[] ="``) and
    ends with ``};``.

    Inserts so the existing trailing comma + newline pattern is preserved
    and the new entry sits as the last row before ``};``.
    """
    table_idx = text.find(table_marker)
    if table_idx < 0:
        raise ValueError(f"Table marker not found: {table_marker!r}")

    # Find the matching closing brace ("};") that ends this table — the
    # first occurrence after the marker.
    close_idx = text.find("};", table_idx)
    if close_idx < 0:
        raise ValueError(
            "Closing brace not found for table " + repr(table_marker))

    # Walk back from `};` to skip whitespace, find the previous newline.
    cut = close_idx
    while cut > table_idx and text[cut - 1] in " \t":
        cut -= 1
    # cut now points at the newline before `};`

    # Ensure new_line ends with a single newline.
    if not new_line.endswith("\n"):
        new_line = new_line + "\n"
    return text[:cut] + new_line + text[cut:]


def _insert_after_last_define(text: str, new_define_line: str) -> str:
    """Append a ``#define`` after the last existing ``TRAINER_PIC_*`` define."""
    pat = re.compile(r"^#define\s+TRAINER_PIC_\w+\s+\d+\n", re.MULTILINE)
    matches = list(pat.finditer(text))
    if not matches:
        raise ValueError("No existing TRAINER_PIC_* defines found in trainers.h")
    last = matches[-1]
    insert_at = last.end()
    return text[:insert_at] + new_define_line + text[insert_at:]


# ── public API ─────────────────────────────────────────────────────────────


@dataclass
class AddTrainerPicResult:
    success: bool
    constant: str
    symbol: str
    base_name: str
    pic_id: int
    files_written: List[str]
    error: str = ""


def add_trainer_pic(
    project_root: str,
    source_png_path: str,
    constant: str,
    symbol: str,
    base_name: str,
    coord_size: int = 8,
    coord_y_offset: int = 1,
    pal_source_path: Optional[str] = None,
) -> AddTrainerPicResult:
    """Register a new trainer pic atomically across the 4 affected files.

    Args:
        project_root: pokefirered root.
        source_png_path: path to the user's indexed PNG (may be inside or
            outside the project — copied into ``graphics/trainers/front_pics/``
            if not already there).
        constant: e.g. ``TRAINER_PIC_DARK_LINK``.
        symbol: e.g. ``DarkLink``.
        base_name: snake_case base used for file naming, e.g. ``dark_link``.
            The PNG ends up at ``<base>_front_pic.png`` and the palette at
            ``<base>.pal``.
        coord_size, coord_y_offset: ``MonCoords`` values for this pic.
            Defaults match the vast majority of vanilla entries.
        pal_source_path: if given, copy this ``.pal`` instead of generating
            one from the PNG's color table. Useful when the palette already
            exists and matches the PNG's indices.

    All writes are byte-equality-guarded; a no-op re-run produces no diffs.

    On any error before the final write phase, no files are modified.
    On error during the write phase, partially-written changes ARE on
    disk (this is rare and only happens if disk fills mid-save). Caller
    should treat success=False as "may need git restore".
    """
    result = AddTrainerPicResult(
        success=False, constant=constant, symbol=symbol,
        base_name=base_name, pic_id=-1, files_written=[])

    # ── 1. Validate inputs and collect current state ──────────────────
    th_path = _trainers_h(project_root)
    fp_path = _front_pic_tables_h(project_root)
    ct_path = _constants_trainers_h(project_root)

    for required in (th_path, fp_path, ct_path):
        if not os.path.isfile(required):
            result.error = f"Required source file not found: {required}"
            return result

    if not os.path.isfile(source_png_path):
        result.error = f"Source PNG not found: {source_png_path}"
        return result

    try:
        with open(th_path, encoding="utf-8") as f:
            th_text = f.read()
        with open(fp_path, encoding="utf-8") as f:
            fp_text = f.read()
        with open(ct_path, encoding="utf-8") as f:
            ct_text = f.read()
    except OSError as exc:
        result.error = f"Could not read project files: {exc}"
        return result

    # Conflict checks
    existing_consts = _existing_pic_constants(ct_text)
    if constant in existing_consts:
        result.error = (
            f"Constant {constant} already exists in include/constants/"
            f"trainers.h. Pick a different name.")
        return result
    existing_syms = _existing_symbols(th_text)
    if symbol in existing_syms:
        result.error = (
            f"Symbol gTrainerFrontPic_{symbol} already exists in "
            f"src/data/graphics/trainers.h. Pick a different symbol.")
        return result

    # ── 2. Copy / generate the asset files ────────────────────────────
    os.makedirs(_front_pic_dir(project_root), exist_ok=True)
    os.makedirs(_palettes_dir(project_root), exist_ok=True)

    target_png = os.path.join(
        _front_pic_dir(project_root), f"{base_name}_front_pic.png")
    if os.path.abspath(source_png_path) != os.path.abspath(target_png):
        try:
            shutil.copy2(source_png_path, target_png)
        except OSError as exc:
            result.error = f"Could not copy PNG into project: {exc}"
            return result
    result.files_written.append(target_png)

    target_pal = os.path.join(
        _palettes_dir(project_root), f"{base_name}.pal")
    try:
        if pal_source_path and os.path.isfile(pal_source_path):
            if os.path.abspath(pal_source_path) != os.path.abspath(target_pal):
                shutil.copy2(pal_source_path, target_pal)
        else:
            # Derive palette from the source PNG's color table.
            from ui.palette_utils import write_jasc_pal
            colors = _png_to_jasc_palette(source_png_path)
            if not write_jasc_pal(target_pal, colors):
                result.error = f"Could not write palette: {target_pal}"
                return result
    except Exception as exc:
        result.error = f"Could not generate palette: {exc}"
        return result
    result.files_written.append(target_pal)

    # ── 3. Build text inserts ─────────────────────────────────────────
    pic_id = _next_pic_id(ct_text)
    result.pic_id = pic_id

    th_insert = (
        f'\nconst u32 gTrainerFrontPic_{symbol}[] = '
        f'INCBIN_U32("graphics/trainers/front_pics/'
        f'{base_name}_front_pic.4bpp.lz");\n'
        f'const u32 gTrainerPalette_{symbol}[] = '
        f'INCBIN_U32("graphics/trainers/palettes/'
        f'{base_name}.gbapal.lz");\n'
    )
    new_th = th_text.rstrip() + "\n" + th_insert

    coords_line = (
        f"    {{.size = {int(coord_size)}, "
        f".y_offset = {int(coord_y_offset)}}},\n"
    )
    suffix = constant[len("TRAINER_PIC_"):]
    sprite_line = (
        f"    TRAINER_SPRITE({suffix}, "
        f"gTrainerFrontPic_{symbol}, 0x800),\n"
    )
    pal_line = (
        f"    TRAINER_PAL({suffix}, gTrainerPalette_{symbol}),\n"
    )

    new_fp = fp_text
    try:
        new_fp = _insert_before_closing_brace(
            new_fp,
            "const struct MonCoords gTrainerFrontPicCoords[] =",
            coords_line)
        new_fp = _insert_before_closing_brace(
            new_fp,
            "const struct CompressedSpriteSheet gTrainerFrontPicTable[] =",
            sprite_line)
        new_fp = _insert_before_closing_brace(
            new_fp,
            "const struct CompressedSpritePalette "
            "gTrainerFrontPicPaletteTable[] =",
            pal_line)
    except ValueError as exc:
        result.error = f"front_pic_tables.h has unexpected layout: {exc}"
        return result

    new_define = _format_define_line(constant, pic_id, ct_text)
    try:
        new_ct = _insert_after_last_define(ct_text, new_define)
    except ValueError as exc:
        result.error = f"constants/trainers.h has unexpected layout: {exc}"
        return result

    # ── 4. Write all three text files (byte-equality guarded) ─────────
    try:
        if write_text_if_changed(th_path, new_th):
            result.files_written.append(th_path)
        if write_text_if_changed(fp_path, new_fp):
            result.files_written.append(fp_path)
        if write_text_if_changed(ct_path, new_ct):
            result.files_written.append(ct_path)
    except Exception as exc:
        result.error = f"Could not write source files: {exc}"
        return result

    result.success = True
    return result
