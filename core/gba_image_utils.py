"""
core/gba_image_utils.py
GBA-compatible image utilities — quantize, index, palette reorder, and export.

Handles:
  - Quantizing any PNG (RGB/RGBA) to 16 or 256 GBA-safe colors
  - Closest-color remapping when importing an image with too many colors
  - Palette reordering (move background/transparent color to index 0)
  - Floyd-Steinberg dithering for better visual quality
  - Indexed PNG and JASC .pal export
  - GBA 15-bit BGR555 color clamping on all output
"""
from __future__ import annotations

import os
from typing import Optional

from PIL import Image
import numpy as np

Color = tuple[int, int, int]  # (r, g, b) 0-255


# ── GBA color helpers ────────────────────────────────────────────────────────

def clamp_to_gba(r: int, g: int, b: int) -> Color:
    """Round each channel down to a multiple of 8 (5-bit per channel)."""
    def q(v: int) -> int:
        v = max(0, min(255, int(v)))
        return (v >> 3) << 3
    return (q(r), q(g), q(b))


def gba_clamp_palette(colors: list[Color]) -> list[Color]:
    """Clamp every color in a palette to GBA 15-bit (5 bits per channel)."""
    return [clamp_to_gba(*c) for c in colors]


def _color_distance_sq(a: tuple[int, ...], b: tuple[int, ...]) -> int:
    """Squared Euclidean distance in RGB space."""
    return sum((int(x) - int(y)) ** 2 for x, y in zip(a[:3], b[:3]))


def find_closest_color(color: Color, palette: list[Color]) -> int:
    """Return the palette index of the closest color to `color`."""
    best_idx = 0
    best_dist = _color_distance_sq(color, palette[0])
    for i in range(1, len(palette)):
        d = _color_distance_sq(color, palette[i])
        if d < best_dist:
            best_dist = d
            best_idx = i
    return best_idx


# ── Quantization ─────────────────────────────────────────────────────────────

def quantize_image(
    img: Image.Image,
    max_colors: int = 16,
    dither: bool = True,
    gba_clamp: bool = True,
) -> tuple[Image.Image, list[Color]]:
    """
    Quantize an image to at most `max_colors` colors.

    Parameters:
        img:        PIL Image (any mode — RGB, RGBA, P, L, etc.)
        max_colors: 16 for 4bpp, 256 for 8bpp
        dither:     True for Floyd-Steinberg dithering
        gba_clamp:  True to round all colors to GBA 15-bit after quantization

    Returns:
        (indexed_image, palette) where indexed_image is mode 'P'
        and palette is a list of (r, g, b) tuples.
    """
    # Ensure RGB(A)
    if img.mode == "P":
        img = img.convert("RGBA")
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")

    # Track alpha for transparency handling
    has_alpha = img.mode == "RGBA"
    alpha_mask = None
    if has_alpha:
        alpha_mask = np.array(img)[:, :, 3]
        # Replace fully transparent pixels with a consistent background
        # so quantization doesn't waste colors on invisible pixels
        rgb = img.convert("RGB")
        arr = np.array(rgb)
        arr[alpha_mask == 0] = [0, 0, 0]
        rgb = Image.fromarray(arr, "RGB")
    else:
        rgb = img.convert("RGB")

    # If already has fewer colors than target, just extract palette
    existing_colors = rgb.getcolors(maxcolors=max_colors + 1)
    if existing_colors is not None and len(existing_colors) <= max_colors:
        # Already within limit — just convert to indexed
        indexed = rgb.quantize(
            colors=max_colors,
            dither=Image.Dither.NONE,
        )
    else:
        # Quantize
        dither_mode = (
            Image.Dither.FLOYDSTEINBERG if dither
            else Image.Dither.NONE
        )
        indexed = rgb.quantize(
            colors=max_colors,
            dither=dither_mode,
        )

    # Extract the palette
    raw_pal = indexed.getpalette()
    if raw_pal is None:
        raw_pal = [0] * (max_colors * 3)

    palette: list[Color] = []
    for i in range(min(max_colors, len(raw_pal) // 3)):
        r, g, b = raw_pal[i * 3], raw_pal[i * 3 + 1], raw_pal[i * 3 + 2]
        if gba_clamp:
            r, g, b = clamp_to_gba(r, g, b)
        palette.append((r, g, b))

    # Pad palette to target size
    while len(palette) < max_colors:
        palette.append((0, 0, 0))

    # If GBA clamping, remap pixels to the clamped palette
    if gba_clamp:
        indexed = _remap_to_clamped(indexed, palette)

    # Restore transparency at index 0 if the original had alpha
    if has_alpha and alpha_mask is not None:
        indexed, palette = _apply_transparency(indexed, palette, alpha_mask)

    return indexed, palette


def _remap_to_clamped(
    indexed: Image.Image, palette: list[Color]
) -> Image.Image:
    """Rebuild the indexed image with the GBA-clamped palette applied."""
    # Build a flat palette list for PIL
    flat = []
    for r, g, b in palette:
        flat.extend([r, g, b])
    # Pad to 256 entries (PIL requires 256*3 for 'P' mode)
    while len(flat) < 768:
        flat.extend([0, 0, 0])
    indexed.putpalette(flat)
    return indexed


def _apply_transparency(
    indexed: Image.Image,
    palette: list[Color],
    alpha_mask: np.ndarray,
) -> tuple[Image.Image, list[Color]]:
    """
    Ensure fully transparent pixels use index 0 and set palette[0]
    to the background color (black by default for GBA transparency).
    """
    arr = np.array(indexed)

    # Find what index transparent pixels currently map to (most common)
    trans_pixels = arr[alpha_mask == 0]
    if len(trans_pixels) == 0:
        return indexed, palette

    # Move the transparent color to index 0 if it isn't already
    # We'll just set index 0 = (0, 0, 0) and remap transparent pixels
    old_idx_0_color = palette[0]

    # Set transparent pixels to index 0
    arr[alpha_mask == 0] = 0

    # If palette[0] wasn't (0,0,0), swap it
    if old_idx_0_color != (0, 0, 0):
        # Find if (0,0,0) exists elsewhere
        black_idx = None
        for i, c in enumerate(palette):
            if c == (0, 0, 0) and i != 0:
                black_idx = i
                break

        if black_idx is not None:
            # Swap palette entries
            palette[0], palette[black_idx] = palette[black_idx], palette[0]
            # Remap pixels
            mask_0 = arr == 0
            mask_b = arr == black_idx
            arr[mask_0] = black_idx
            arr[mask_b] = 0
            # Re-set transparent pixels
            arr[alpha_mask == 0] = 0
        else:
            # Just set palette[0] to black
            palette[0] = (0, 0, 0)

    result = Image.fromarray(arr, "P")
    result = _remap_to_clamped(result, palette)
    return result, palette


# ── Closest-color remapping ──────────────────────────────────────────────────

def remap_to_palette(
    img: Image.Image,
    target_palette: list[Color],
    dither: bool = False,
) -> Image.Image:
    """
    Remap an image's colors to the nearest match in `target_palette`.

    This is for when you already HAVE a palette (e.g., from a tileset)
    and want to force an imported image to use only those colors.

    Parameters:
        img:            PIL Image (any mode)
        target_palette: The palette to map to (16 or 256 colors)
        dither:         True for error-diffusion dithering during remap

    Returns:
        Indexed PIL Image using target_palette.
    """
    if img.mode != "RGB":
        img = img.convert("RGB")

    n_colors = len(target_palette)
    arr = np.array(img)
    h, w = arr.shape[:2]

    # Build palette array for fast distance computation
    pal_arr = np.array(target_palette, dtype=np.int32)

    if dither:
        # Floyd-Steinberg dithering with closest-color matching
        work = arr.astype(np.float64)
        out = np.zeros((h, w), dtype=np.uint8)

        for y in range(h):
            for x in range(w):
                old_pixel = work[y, x].copy()
                # Clamp to valid range
                old_pixel = np.clip(old_pixel, 0, 255)
                # Find closest palette color
                dists = np.sum((pal_arr - old_pixel[:3].astype(np.int32)) ** 2, axis=1)
                idx = int(np.argmin(dists))
                out[y, x] = idx
                # Compute error
                new_pixel = np.array(target_palette[idx], dtype=np.float64)
                error = old_pixel[:3] - new_pixel
                # Distribute error
                if x + 1 < w:
                    work[y, x + 1, :3] += error * (7.0 / 16.0)
                if y + 1 < h:
                    if x > 0:
                        work[y + 1, x - 1, :3] += error * (3.0 / 16.0)
                    work[y + 1, x, :3] += error * (5.0 / 16.0)
                    if x + 1 < w:
                        work[y + 1, x + 1, :3] += error * (1.0 / 16.0)
    else:
        # Simple nearest-color, vectorized
        flat = arr.reshape(-1, 3).astype(np.int32)
        # Compute distances to all palette entries at once
        # Shape: (n_pixels, n_colors)
        diffs = flat[:, np.newaxis, :] - pal_arr[np.newaxis, :, :]
        dists = np.sum(diffs ** 2, axis=2)
        indices = np.argmin(dists, axis=1).astype(np.uint8)
        out = indices.reshape(h, w)

    result = Image.fromarray(out, "P")
    result = _remap_to_clamped(result, target_palette)
    return result


# ── Palette reordering ───────────────────────────────────────────────────────

def reorder_palette(
    indexed_img: Image.Image,
    palette: list[Color],
    new_order: list[int],
) -> tuple[Image.Image, list[Color]]:
    """
    Reorder palette entries and remap all pixel indices to match.

    Parameters:
        indexed_img: PIL Image in mode 'P'
        palette:     Current palette
        new_order:   List of old indices in new position order.
                     e.g., [3, 0, 1, 2] means old index 3 is now index 0,
                     old index 0 is now index 1, etc.

    Returns:
        (reordered_image, reordered_palette)
    """
    n = len(palette)
    # Build the reverse mapping: old_idx → new_idx
    old_to_new = [0] * n
    for new_idx, old_idx in enumerate(new_order):
        if old_idx < n:
            old_to_new[old_idx] = new_idx

    # Reorder palette
    new_palette: list[Color] = [(0, 0, 0)] * n
    for new_idx, old_idx in enumerate(new_order):
        if old_idx < len(palette):
            new_palette[new_idx] = palette[old_idx]

    # Remap pixels
    arr = np.array(indexed_img)
    lut = np.array(old_to_new, dtype=np.uint8)
    # Clamp indices to valid range
    arr = np.clip(arr, 0, n - 1)
    new_arr = lut[arr]

    result = Image.fromarray(new_arr, "P")
    result = _remap_to_clamped(result, new_palette)
    return result, new_palette


def move_color_to_index(
    indexed_img: Image.Image,
    palette: list[Color],
    from_idx: int,
    to_idx: int = 0,
) -> tuple[Image.Image, list[Color]]:
    """
    Move a palette entry from one index to another (shifting others).

    Common use: move background/transparent color to index 0.
    """
    n = len(palette)
    order = list(range(n))
    order.remove(from_idx)
    order.insert(to_idx, from_idx)
    return reorder_palette(indexed_img, palette, order)


def swap_palette_entries(
    indexed_img: Image.Image,
    palette: list[Color],
    idx_a: int,
    idx_b: int,
) -> tuple[Image.Image, list[Color]]:
    """Swap two palette entries and remap all pixels."""
    n = len(palette)
    order = list(range(n))
    order[idx_a], order[idx_b] = order[idx_b], order[idx_a]
    return reorder_palette(indexed_img, palette, order)


# ── Export ───────────────────────────────────────────────────────────────────

def export_indexed_png(
    indexed_img: Image.Image,
    palette: list[Color],
    output_path: str,
    transparent_index: int = 0,
) -> bool:
    """
    Save an indexed image as PNG with the given palette.

    Parameters:
        indexed_img:       PIL Image in mode 'P'
        palette:           Color list
        output_path:       Where to save
        transparent_index: Which palette index is transparent (default 0)

    Returns True on success.
    """
    try:
        # Ensure palette is applied
        indexed_img = _remap_to_clamped(indexed_img, palette)
        # Save with transparency info
        indexed_img.save(
            output_path,
            transparency=transparent_index,
            optimize=False,
        )
        return True
    except Exception:
        return False


def export_palette(palette: list[Color], output_path: str) -> bool:
    """Save palette as JASC .pal file with GBA clamping."""
    clamped = gba_clamp_palette(palette)
    try:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    except Exception:
        pass
    try:
        padded = list(clamped)
        target_count = 256 if len(padded) > 16 else 16
        while len(padded) < target_count:
            padded.append((0, 0, 0))
        padded = padded[:target_count]
        lines = ["JASC-PAL", "0100", str(target_count)]
        for (r, g, b) in padded:
            lines.append(f"{r} {g} {b}")
        with open(output_path, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines) + "\n")
        return True
    except Exception:
        return False


# ── Image info ───────────────────────────────────────────────────────────────

def get_image_info(path: str) -> dict:
    """Get basic info about an image file."""
    try:
        img = Image.open(path)
        info = {
            "width": img.width,
            "height": img.height,
            "mode": img.mode,
            "is_indexed": img.mode == "P",
            "color_count": 0,
            "has_alpha": img.mode in ("RGBA", "PA", "LA"),
        }
        if img.mode == "P":
            pal = img.getpalette()
            if pal:
                info["color_count"] = len(pal) // 3
            else:
                info["color_count"] = 0
        else:
            colors = img.convert("RGB").getcolors(maxcolors=65536)
            info["color_count"] = len(colors) if colors else -1  # -1 = too many
        return info
    except Exception:
        return {}
