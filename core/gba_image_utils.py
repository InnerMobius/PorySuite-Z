"""
core/gba_image_utils.py
GBA-compatible image utilities — quantize, index, palette reorder, and export.

Uses QImage + numpy only (no PIL dependency).

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

import numpy as np
from PyQt6.QtGui import QImage, QColor

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


# ── QImage ↔ numpy helpers ──────────────────────────────────────────────────

def _qimage_to_rgb_array(img: QImage) -> tuple[np.ndarray, np.ndarray | None]:
    """Convert any QImage to an (H, W, 3) uint8 RGB array.

    Returns (rgb_array, alpha_array_or_None).
    """
    # Normalise to ARGB32
    if img.format() == QImage.Format.Format_Indexed8:
        img = img.convertToFormat(QImage.Format.Format_ARGB32)
    elif img.format() != QImage.Format.Format_ARGB32:
        img = img.convertToFormat(QImage.Format.Format_ARGB32)

    w, h = img.width(), img.height()
    ptr = img.bits()
    ptr.setsize(h * w * 4)
    arr = np.frombuffer(ptr, dtype=np.uint8).reshape(h, w, 4).copy()
    # ARGB32 byte order is BGRA on little-endian
    b_ch = arr[:, :, 0]
    g_ch = arr[:, :, 1]
    r_ch = arr[:, :, 2]
    a_ch = arr[:, :, 3]
    rgb = np.stack([r_ch, g_ch, b_ch], axis=2)

    alpha = a_ch if np.any(a_ch < 255) else None
    return rgb, alpha


def _indexed_array_to_qimage(
    indices: np.ndarray,
    palette: list[Color],
    transparent_index: int = 0,
) -> QImage:
    """Build a QImage Format_Indexed8 from an index array and palette."""
    h, w = indices.shape
    img = QImage(indices.data.tobytes(), w, h, w, QImage.Format.Format_Indexed8)
    # QImage doesn't copy the data — force a deep copy
    img = img.copy()

    # Build color table
    ct = []
    for i, (r, g, b) in enumerate(palette):
        alpha = 0 if i == transparent_index else 255
        ct.append((alpha << 24) | (r << 16) | (g << 8) | b)
    # Pad to 256
    while len(ct) < 256:
        ct.append(0xFF000000)
    img.setColorTable(ct)
    return img


# ── Median-cut quantization ─────────────────────────────────────────────────

def _median_cut(pixels: np.ndarray, n_colors: int) -> list[Color]:
    """Median-cut colour quantization. Returns up to n_colors centroids."""
    if len(pixels) == 0:
        return [(0, 0, 0)]

    buckets = [pixels]
    while len(buckets) < n_colors:
        # Find the bucket with the widest range on any channel
        best_i = 0
        best_range = -1
        best_ch = 0
        for i, bk in enumerate(buckets):
            if len(bk) < 2:
                continue
            for ch in range(3):
                rng = int(bk[:, ch].max()) - int(bk[:, ch].min())
                if rng > best_range:
                    best_range = rng
                    best_i = i
                    best_ch = ch
        if best_range <= 0:
            break  # Can't split further
        bk = buckets.pop(best_i)
        median = int(np.median(bk[:, best_ch]))
        lo = bk[bk[:, best_ch] <= median]
        hi = bk[bk[:, best_ch] > median]
        if len(lo) == 0:
            lo = hi[:1]
            hi = hi[1:]
        elif len(hi) == 0:
            hi = lo[-1:]
            lo = lo[:-1]
        buckets.append(lo)
        buckets.append(hi)

    # Compute centroid of each bucket
    result: list[Color] = []
    for bk in buckets:
        if len(bk) == 0:
            continue
        mean = bk.mean(axis=0).astype(int)
        result.append((int(mean[0]), int(mean[1]), int(mean[2])))
    return result[:n_colors]


# ── Quantize mode helpers ────────────────────────────────────────────────────

# Mode constants
QMODE_BALANCED = "balanced"
QMODE_SMOOTH = "smooth"
QMODE_PRESERVE_RARE = "preserve_rare"
QMODE_MANUAL = "manual"

QUANTIZE_MODES = {
    QMODE_BALANCED: "Balanced — wide colour variety, fair to small details",
    QMODE_SMOOTH: "Smooth Gradients — best for backgrounds with subtle shading",
    QMODE_PRESERVE_RARE: "Preserve Rare Colors — keeps unique colours even if they cover few pixels",
    QMODE_MANUAL: "Manual Pick — choose which colours to keep from a larger candidate set",
}


def _balanced_quantize(unique_pixels: np.ndarray, n: int) -> list[Color]:
    """Each unique colour gets one vote regardless of pixel count.

    This prevents large uniform areas (backgrounds) from hogging palette
    slots.  Small-but-distinct colours (like a red highlight on a mostly
    blue image) get equal representation.
    """
    return _median_cut(unique_pixels, n)


def _preserve_rare_quantize(flat_pixels: np.ndarray, n: int) -> list[Color]:
    """Oversample, then greedily pick the N most *distinct* colours.

    Maximises minimum distance between palette entries, so colours that
    are far apart in RGB space survive even if they cover very few pixels.
    """
    # Oversample to 3x target
    candidates = _median_cut(flat_pixels, min(n * 3, 48))
    if len(candidates) <= n:
        return candidates

    # Greedy farthest-point selection
    cand_arr = np.array(candidates, dtype=np.int32)
    picked: list[int] = [0]  # Start with the first candidate
    min_dists = np.sum((cand_arr - cand_arr[0]) ** 2, axis=1).astype(np.float64)

    for _ in range(n - 1):
        # Pick the candidate farthest from all already-picked
        # Mask out already-picked
        for pi in picked:
            min_dists[pi] = -1
        next_idx = int(np.argmax(min_dists))
        if min_dists[next_idx] <= 0:
            break
        picked.append(next_idx)
        # Update min distances
        new_dists = np.sum((cand_arr - cand_arr[next_idx]) ** 2, axis=1).astype(np.float64)
        min_dists = np.minimum(min_dists, new_dists)

    return [candidates[i] for i in picked]


def get_quantize_candidates(
    img: QImage,
    n_candidates: int = 24,
    gba_clamp: bool = True,
) -> list[Color]:
    """Return a set of candidate colours for manual selection.

    Oversamples to n_candidates using balanced quantization, so the user
    sees a wide spread of colours to pick from.
    """
    rgb, alpha = _qimage_to_rgb_array(img)
    if alpha is not None:
        rgb[alpha == 0] = [0, 0, 0]
    flat = rgb.reshape(-1, 3)
    unique = np.unique(flat, axis=0)

    if len(unique) <= n_candidates:
        candidates = [tuple(c) for c in unique.tolist()]
    else:
        candidates = _balanced_quantize(unique, n_candidates)

    if gba_clamp:
        candidates = gba_clamp_palette(candidates)
    # Deduplicate
    seen: set[Color] = set()
    result: list[Color] = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            result.append(c)
    return result


# ── Quantization ─────────────────────────────────────────────────────────────

def quantize_image(
    img: QImage,
    max_colors: int = 16,
    dither: bool = False,
    gba_clamp: bool = True,
    mode: str = QMODE_BALANCED,
    manual_palette: list[Color] | None = None,
) -> tuple[QImage, list[Color]]:
    """
    Quantize an image to at most `max_colors` colors.

    Parameters:
        img:            QImage (any format)
        max_colors:     16 for 4bpp, 256 for 8bpp
        dither:         True for Floyd-Steinberg dithering (never forced)
        gba_clamp:      True to round all colors to GBA 15-bit
        mode:           QMODE_BALANCED, QMODE_SMOOTH, QMODE_PRESERVE_RARE,
                        or QMODE_MANUAL
        manual_palette: When mode=QMODE_MANUAL, the user-selected colours

    Returns:
        (indexed_qimage, palette) where indexed_qimage is Format_Indexed8.
    """
    rgb, alpha = _qimage_to_rgb_array(img)
    h, w = rgb.shape[:2]

    # Zero out transparent pixels so they don't waste palette entries
    if alpha is not None:
        rgb[alpha == 0] = [0, 0, 0]

    flat = rgb.reshape(-1, 3)
    unique = np.unique(flat, axis=0)

    if len(unique) <= max_colors:
        # Already within limit — no quantization needed
        palette = [tuple(c) for c in unique.tolist()]
    elif mode == QMODE_MANUAL and manual_palette:
        palette = list(manual_palette[:max_colors])
    elif mode == QMODE_SMOOTH:
        # Pixel-weighted median-cut — subtle gradients preserved
        palette = _median_cut(flat, max_colors)
    elif mode == QMODE_PRESERVE_RARE:
        # Oversample + farthest-point — rare colours survive
        palette = _preserve_rare_quantize(flat, max_colors)
    else:
        # QMODE_BALANCED (default) — unique-colour-weighted
        palette = _balanced_quantize(unique, max_colors)

    # GBA clamp
    if gba_clamp:
        palette = gba_clamp_palette(palette)

    # Deduplicate after clamping
    seen: dict[Color, int] = {}
    deduped: list[Color] = []
    for c in palette:
        if c not in seen:
            seen[c] = len(deduped)
            deduped.append(c)
    palette = deduped

    # Pad to target
    while len(palette) < max_colors:
        palette.append((0, 0, 0))
    palette = palette[:max_colors]

    # Map each pixel to nearest palette entry
    indices = _remap_pixels(rgb, palette, dither)

    # Handle transparency — force transparent pixels to index 0
    if alpha is not None:
        # Make sure index 0 is the BG colour (0,0,0)
        if palette[0] != (0, 0, 0):
            # Find black or insert it
            try:
                black_idx = palette.index((0, 0, 0))
            except ValueError:
                black_idx = len(palette) - 1
                palette[black_idx] = (0, 0, 0)
            # Swap in index array
            mask_0 = indices == 0
            mask_b = indices == black_idx
            indices[mask_0] = black_idx
            indices[mask_b] = 0
            palette[0], palette[black_idx] = palette[black_idx], palette[0]
        indices[alpha == 0] = 0

    result = _indexed_array_to_qimage(indices, palette, transparent_index=0)
    return result, palette


def _remap_pixels(
    rgb: np.ndarray,
    palette: list[Color],
    dither: bool,
) -> np.ndarray:
    """Map each pixel to the nearest palette index. Returns uint8 index array."""
    h, w = rgb.shape[:2]
    pal_arr = np.array(palette, dtype=np.int32)

    if dither:
        work = rgb.astype(np.float64)
        out = np.zeros((h, w), dtype=np.uint8)
        for y in range(h):
            for x in range(w):
                old = np.clip(work[y, x], 0, 255)
                dists = np.sum((pal_arr - old.astype(np.int32)) ** 2, axis=1)
                idx = int(np.argmin(dists))
                out[y, x] = idx
                error = old - np.array(palette[idx], dtype=np.float64)
                if x + 1 < w:
                    work[y, x + 1] += error * (7.0 / 16.0)
                if y + 1 < h:
                    if x > 0:
                        work[y + 1, x - 1] += error * (3.0 / 16.0)
                    work[y + 1, x] += error * (5.0 / 16.0)
                    if x + 1 < w:
                        work[y + 1, x + 1] += error * (1.0 / 16.0)
    else:
        flat = rgb.reshape(-1, 3).astype(np.int32)
        diffs = flat[:, np.newaxis, :] - pal_arr[np.newaxis, :, :]
        dists = np.sum(diffs ** 2, axis=2)
        out = np.argmin(dists, axis=1).astype(np.uint8).reshape(h, w)

        # Clean up orphan pixels — without dithering, subtle source
        # variations cause scattered single-pixel noise that looks like
        # accidental dithering.  A 3×3 majority filter replaces each pixel
        # with the most common index in its neighbourhood when the pixel
        # disagrees with the majority.
        out = _majority_filter(out)

    return out


def _majority_filter(indices: np.ndarray) -> np.ndarray:
    """Replace orphan/scattered pixels with the most common neighbour.

    For each pixel, look at its 3×3 neighbourhood.  If the centre pixel's
    index is not the most common one, replace it.  This eliminates the
    "accidental dithering" effect of nearest-colour mapping on images with
    subtle colour gradients or anti-aliased edges.

    Only affects single isolated outliers — solid regions and intentional
    detail are preserved.
    """
    h, w = indices.shape
    if h < 3 or w < 3:
        return indices

    n_pal = int(indices.max()) + 1

    # For 16-color palettes the one-hot approach is fast and compact.
    # For 256-color palettes the one-hot array would be (H,W,9,256) int32
    # which is ~2.4 GB for a 512×512 image — use bincount per-row instead.
    if n_pal > 32:
        return _majority_filter_large(indices, n_pal)

    # Pad borders by 1 pixel (replicate edge)
    padded = np.pad(indices, 1, mode="edge")

    # Collect all 9 neighbours into a (h, w, 9) stack — fully vectorised
    neighbours = np.stack([
        padded[dy:dy + h, dx:dx + w]
        for dy in range(3) for dx in range(3)
    ], axis=2)  # shape (h, w, 9)

    # Count occurrences of each palette index per pixel
    one_hot = np.eye(n_pal, dtype=np.uint8)[neighbours]  # (h, w, 9, n_pal)
    counts = one_hot.sum(axis=2)  # (h, w, n_pal)

    majority = counts.argmax(axis=2).astype(np.uint8)  # (h, w)
    majority_count = counts.max(axis=2)  # (h, w)

    # Only replace outliers: majority must hold ≥5 of 9 votes,
    # and the current pixel must disagree with it
    is_outlier = (majority != indices) & (majority_count >= 5)
    out = np.where(is_outlier, majority, indices)

    return out


def _majority_filter_large(indices: np.ndarray, n_pal: int) -> np.ndarray:
    """Memory-safe majority filter for large palettes (8bpp / 256 colors).

    Uses np.bincount row-by-row instead of a giant one-hot tensor.
    """
    h, w = indices.shape
    padded = np.pad(indices, 1, mode="edge")

    # Collect 9 neighbours: (h, w, 9)
    neighbours = np.stack([
        padded[dy:dy + h, dx:dx + w]
        for dy in range(3) for dx in range(3)
    ], axis=2)

    out = indices.copy()
    flat_neighbours = neighbours.reshape(h * w, 9)
    flat_centre = indices.ravel()

    for i in range(h * w):
        centre = flat_centre[i]
        block = flat_neighbours[i]
        counts = np.bincount(block, minlength=n_pal)
        majority = int(np.argmax(counts))
        if majority != centre and counts[majority] >= 5:
            out.ravel()[i] = majority

    return out


# ── Closest-color remapping ──────────────────────────────────────────────────

def remap_to_palette(
    img: QImage,
    target_palette: list[Color],
    dither: bool = False,
) -> QImage:
    """
    Remap an image's colors to the nearest match in `target_palette`.

    Returns an indexed QImage using target_palette.
    """
    if not target_palette:
        # Empty palette — return a black indexed image
        target_palette = [(0, 0, 0)]
    rgb, _alpha = _qimage_to_rgb_array(img)
    indices = _remap_pixels(rgb, target_palette, dither)
    return _indexed_array_to_qimage(indices, target_palette, transparent_index=0)


# ── Palette reordering ───────────────────────────────────────────────────────

def reorder_palette(
    indexed_img: QImage,
    palette: list[Color],
    new_order: list[int],
) -> tuple[QImage, list[Color]]:
    """
    Reorder palette entries and remap all pixel indices to match.

    new_order: list of old indices in new position order.
    """
    n = len(palette)
    old_to_new = [0] * n
    for new_idx, old_idx in enumerate(new_order):
        if old_idx < n:
            old_to_new[old_idx] = new_idx

    new_palette: list[Color] = [(0, 0, 0)] * n
    for new_idx, old_idx in enumerate(new_order):
        if old_idx < len(palette):
            new_palette[new_idx] = palette[old_idx]

    # Read pixel indices from the indexed QImage
    arr = _qimage_index_array(indexed_img)
    lut = np.array(old_to_new, dtype=np.uint8)
    arr = np.clip(arr, 0, n - 1)
    new_arr = lut[arr]

    result = _indexed_array_to_qimage(new_arr, new_palette)
    return result, new_palette


def _qimage_index_array(img: QImage) -> np.ndarray:
    """Extract pixel index array from a Format_Indexed8 QImage."""
    if img.format() != QImage.Format.Format_Indexed8:
        raise ValueError("Image is not indexed (Format_Indexed8)")
    w, h = img.width(), img.height()
    # bytesPerLine may include padding
    bpl = img.bytesPerLine()
    ptr = img.bits()
    ptr.setsize(h * bpl)
    raw = np.frombuffer(ptr, dtype=np.uint8).reshape(h, bpl)
    return raw[:, :w].copy()


def move_color_to_index(
    indexed_img: QImage,
    palette: list[Color],
    from_idx: int,
    to_idx: int = 0,
) -> tuple[QImage, list[Color]]:
    """Move a palette entry from one index to another (shifting others)."""
    n = len(palette)
    order = list(range(n))
    order.remove(from_idx)
    order.insert(to_idx, from_idx)
    return reorder_palette(indexed_img, palette, order)


def swap_palette_entries(
    indexed_img: QImage,
    palette: list[Color],
    idx_a: int,
    idx_b: int,
) -> tuple[QImage, list[Color]]:
    """Swap two palette entries and remap all pixels."""
    n = len(palette)
    order = list(range(n))
    order[idx_a], order[idx_b] = order[idx_b], order[idx_a]
    return reorder_palette(indexed_img, palette, order)


# ── Export ───────────────────────────────────────────────────────────────────

def export_indexed_png(
    indexed_img: QImage,
    palette: list[Color],
    output_path: str,
    transparent_index: int = 0,
) -> bool:
    """Save an indexed QImage as PNG. Returns True on success."""
    try:
        # Rebuild the colour table to make sure it matches our palette
        img = _rebuild_color_table(indexed_img, palette, transparent_index)
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        return img.save(output_path, "PNG")
    except Exception:
        return False


def _rebuild_color_table(
    img: QImage, palette: list[Color], transparent_index: int = 0,
) -> QImage:
    """Ensure a Format_Indexed8 QImage has the correct color table."""
    if img.format() != QImage.Format.Format_Indexed8:
        return img
    ct = []
    for i, (r, g, b) in enumerate(palette):
        alpha = 0 if i == transparent_index else 255
        ct.append((alpha << 24) | (r << 16) | (g << 8) | b)
    while len(ct) < 256:
        ct.append(0xFF000000)
    result = img.copy()
    result.setColorTable(ct)
    return result


def export_palette(palette: list[Color], output_path: str) -> bool:
    """Save palette as JASC .pal file with GBA clamping."""
    clamped = gba_clamp_palette(palette)
    try:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    except Exception:
        pass
    try:
        padded = list(clamped)
        # Pad to the nearest GBA-standard size (16 or 256) for tool
        # compatibility.  Custom counts (e.g. 37) pad up to 256 since
        # they're building towards a full 8bpp palette.
        if len(padded) <= 16:
            target_count = 16
        else:
            target_count = 256
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
    """Get basic info about an image file using QImage."""
    try:
        img = QImage(path)
        if img.isNull():
            return {}
        is_indexed = img.format() == QImage.Format.Format_Indexed8
        ct = img.colorTable() if is_indexed else []
        info = {
            "width": img.width(),
            "height": img.height(),
            "mode": "Indexed" if is_indexed else "RGB",
            "is_indexed": is_indexed,
            "color_count": len(ct) if is_indexed else -1,
            "has_alpha": img.hasAlphaChannel(),
        }
        if not is_indexed:
            # Count unique colours (cap to avoid slow scans on huge images)
            rgb, _ = _qimage_to_rgb_array(img)
            flat = rgb.reshape(-1, 3)
            if len(flat) <= 262144:  # 512x512
                unique = np.unique(flat, axis=0)
                info["color_count"] = len(unique)
            else:
                # Sample for speed
                sample = flat[::4]
                unique = np.unique(sample, axis=0)
                info["color_count"] = len(unique)
        return info
    except Exception:
        return {}
