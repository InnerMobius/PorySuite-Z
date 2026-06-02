"""Shared manual-palette-pick entry point for every image-import path.

Background
==========

The Image Indexer tab (`ui/image_indexer_tab.py`) ships a sophisticated
manual-pick dialog (`_ManualPickDialog`) that lets the user:

  - See every distinct GBA-clamped colour in the source image as a
    clickable swatch grid
  - Drag the result palette swatches to reorder them
  - Right-click any result slot to "Set as Background" (sends to slot 0)
  - Double-click any result slot to set a custom RGB (for colours not
    in the source — e.g. when the palette is shared with other sprites)
  - "+ Custom colour…" button (color picker, appends to next empty)
  - Auto-fill (first N candidates in discovery order)
  - Live preview of the remap

That dialog was originally private to the Image Indexer tab.  This
module exposes it via two entry points so every PNG-import path across
the app can offer "Import Manually…":

  - `pick_palette_manually_from_path` — palette-only.  Use when the
    caller just wants the chosen 16 colours (no image remap).
  - `import_image_manually_from_path` — FULL import.  Opens the dialog,
    then ALSO remaps the source PNG's pixels to the chosen palette and
    returns both the palette AND the remapped indexed QImage so the
    caller can save it as the editor's source PNG.  This is the
    correct flow for replacing a sprite's image AND its palette in one
    step.

The dialog itself isn't duplicated — we re-export the canonical
`_ManualPickDialog` from `image_indexer_tab` so there's exactly one
source of truth for behaviour.
"""

from __future__ import annotations

import os
from typing import Optional, Tuple, List

from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QDialog, QMessageBox, QWidget

Color = Tuple[int, int, int]


def pick_palette_manually_from_path(
    png_path: str,
    target_colors: int = 16,
    parent: Optional[QWidget] = None,
    *,
    n_candidates: Optional[int] = None,
) -> Optional[List[Color]]:
    """Palette-only: open the manual-pick dialog seeded from a PNG file
    on disk.  Returns the chosen palette (length == `target_colors`,
    slot 0 = BG) or None if the user cancelled / the image couldn't be
    loaded.

    Use `import_image_manually_from_path` instead when the caller also
    needs the source image remapped to the chosen palette (the normal
    "replace this sprite with that PNG" workflow).
    """
    img = QImage(png_path)
    if img.isNull():
        if parent is not None:
            QMessageBox.warning(
                parent, "Could Not Load PNG",
                f"PorySuite couldn't load this image:\n{png_path}",
            )
        return None
    return pick_palette_manually(
        img, target_colors=target_colors, parent=parent,
        n_candidates=n_candidates,
        source_label=os.path.basename(png_path),
    )


def _extract_indexed_palette(
    source_img: QImage, target_colors: int,
) -> Optional[List[Color]]:
    """If `source_img` is already an indexed (Format_Indexed8) PNG and
    its colour table has ≤ `target_colors` real entries, return that
    table as a `List[Color]` in slot order — so the manual picker can
    open at the source's existing palette, not a generic auto-fill.

    Returns None when the source isn't indexed (or has more than
    `target_colors` distinct colour-table entries, in which case the
    user probably wants quantization rather than slot-order preservation).

    Trailing pure-black slots are kept so that the source's intended
    slot layout (including a black transparent slot 0) is preserved
    exactly.
    """
    if source_img is None or source_img.isNull():
        return None
    if source_img.format() != QImage.Format.Format_Indexed8:
        return None
    ct = source_img.colorTable()
    if not ct:
        return None
    if len(ct) > target_colors:
        return None
    # GBA-clamp each entry so the seeded palette is what the project
    # would actually store, not the raw 8-bit-per-channel PNG values.
    from core.gba_image_utils import clamp_to_gba
    colors: List[Color] = []
    for entry in ct[:target_colors]:
        r = (entry >> 16) & 0xFF
        g = (entry >> 8) & 0xFF
        b = entry & 0xFF
        colors.append(clamp_to_gba(r, g, b))
    return colors


def pick_palette_manually(
    source_img: QImage,
    target_colors: int = 16,
    parent: Optional[QWidget] = None,
    *,
    n_candidates: Optional[int] = None,
    source_label: str = "",
) -> Optional[List[Color]]:
    """Open the manual-pick dialog with `source_img` as the source.

    The dialog seeds its candidate pool by oversampling distinct colours
    from `source_img` (defaults to roughly 4× the target slot count so
    the user has a generous selection range).

    **Indexed-source shortcut:** when `source_img` is already
    `Format_Indexed8` with ≤ `target_colors` colour-table entries, the
    dialog opens with that existing palette pre-loaded in slot order.
    The user can then reorder / edit / replace any of them, but the
    "starting point" matches what the source already encodes — no need
    to manually re-pick a palette that's already correct.  This is the
    natural workflow for "I've got an indexed PNG of my sprite, just
    let me confirm / tweak the slot order before saving".

    Returns the picked palette as a list of `target_colors` `(r,g,b)`
    tuples in slot order (slot 0 = BG / transparent).  Returns None if
    the user cancelled.
    """
    # Lazy import to avoid hard dependency cycles at module-load time.
    from core.gba_image_utils import get_quantize_candidates
    from ui.image_indexer_tab import _ManualPickDialog

    if n_candidates is None:
        # Generous pool — let the user see lots of options.  Capped at
        # 96 (a 10×10 swatch grid with one trailing row) so the dialog
        # doesn't grow unbounded for images with hundreds of colours.
        n_candidates = min(96, max(target_colors * 4, target_colors + 8))

    candidates = get_quantize_candidates(
        source_img, n_candidates=n_candidates, gba_clamp=True,
    )
    if not candidates:
        if parent is not None:
            QMessageBox.warning(
                parent, "No Colours Found",
                "Could not extract any colours from this image.",
            )
        return None

    initial = _extract_indexed_palette(source_img, target_colors)

    dlg = _ManualPickDialog(
        candidates=candidates,
        target=target_colors,
        source_img=source_img,
        parent=parent,
        initial_palette=initial,
        bg_transparent=True,
    )
    if source_label:
        title_suffix = " (indexed source — palette pre-loaded)" if initial else ""
        dlg.setWindowTitle(
            f"Pick & order {target_colors} colours — "
            f"{source_label}{title_suffix}"
        )
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None

    palette = dlg.selected_colors()
    # Defensive: ensure exactly `target_colors` entries even if the user
    # bypassed normalization somehow.
    while len(palette) < target_colors:
        palette.append((0, 0, 0))
    return palette[:target_colors]


def import_image_manually_from_path(
    png_path: str,
    target_colors: int = 16,
    parent: Optional[QWidget] = None,
    *,
    n_candidates: Optional[int] = None,
    dither: bool = False,
) -> Optional[Tuple[List[Color], QImage]]:
    """Full image-import flow: pick a palette manually, then remap the
    source PNG's pixels to that palette.

    Returns `(palette, remapped_indexed_qimage)` or None if the user
    cancelled.  The returned QImage is `Format_Indexed8`, has its
    colour table set to `palette`, and is ready to be passed to
    `core.gba_image_utils.export_indexed_png(remapped, palette, dest)`
    or to any code path that expects a project-format indexed PNG.

    Slot 0 of `palette` is the BG / transparent colour, per the
    sprite convention used throughout the project.
    """
    img = QImage(png_path)
    if img.isNull():
        if parent is not None:
            QMessageBox.warning(
                parent, "Could Not Load PNG",
                f"PorySuite couldn't load this image:\n{png_path}",
            )
        return None

    palette = pick_palette_manually(
        img, target_colors=target_colors, parent=parent,
        n_candidates=n_candidates,
        source_label=os.path.basename(png_path),
    )
    if palette is None:
        return None

    # Remap pixels.  `remap_to_palette` returns an Indexed8 QImage with
    # the colour table already set to `palette` and slot 0 marked
    # transparent (via tRNS) when the source has alpha=0 pixels.
    from core.gba_image_utils import remap_to_palette
    try:
        remapped = remap_to_palette(
            img, palette, dither=dither, bg_transparent=True,
        )
    except Exception as exc:
        if parent is not None:
            QMessageBox.warning(
                parent, "Remap Failed",
                f"Could not remap the image to the chosen palette:\n{exc}",
            )
        return None

    if remapped is None or remapped.isNull():
        if parent is not None:
            QMessageBox.warning(
                parent, "Remap Failed",
                "The pixel remap returned an empty image.",
            )
        return None

    return palette, remapped


def save_remapped_image(
    remapped: QImage,
    palette: List[Color],
    dest_path: str,
) -> bool:
    """Write a remapped indexed image to disk as an indexed PNG.

    Thin wrapper around `core.gba_image_utils.export_indexed_png` so
    callers don't have to import that module directly.  Returns True on
    success, False on failure.
    """
    from core.gba_image_utils import export_indexed_png
    return export_indexed_png(remapped, palette, dest_path, transparent_index=0)
