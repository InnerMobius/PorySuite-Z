"""JASC-PAL (text) palette read/write helpers for .pal files in pokefirered.

Format:
    JASC-PAL
    0100
    16
    R G B    (16 times, values 0-255)

GBA palettes are 15-bit BGR555, so RGB values are effectively multiples of 8.
We clamp on write.
"""

from __future__ import annotations

import os
from typing import List, Tuple


Color = Tuple[int, int, int]  # (r, g, b) 0-255


def clamp_to_gba(r: int, g: int, b: int) -> Color:
    """Round each channel down to a multiple of 8 (5-bit per channel)."""
    def q(v: int) -> int:
        v = max(0, min(255, int(v)))
        return (v >> 3) << 3
    return (q(r), q(g), q(b))


def read_jasc_pal(path: str, max_colors: int = 0) -> List[Color]:
    """Read a JASC-PAL file and return a list of (r,g,b) tuples.

    If max_colors is 0, returns all colors declared in the file (16 or 256).
    If max_colors is set, caps at that many.
    Returns an empty list on any failure.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = [ln.strip() for ln in f.readlines() if ln.strip()]
    except Exception:
        return []
    # lines[0] = 'JASC-PAL', lines[1] = '0100', lines[2] = count
    if len(lines) < 4 or lines[0] != "JASC-PAL":
        return []
    try:
        count = int(lines[2])
    except Exception:
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
        except Exception:
            continue
    # Cap if requested
    if max_colors > 0:
        colors = colors[:max_colors]
    # Pad to at least 16
    while len(colors) < 16:
        colors.append((0, 0, 0))
    return colors


def write_jasc_pal(path: str, colors: List[Color]) -> bool:
    """Write a JASC-PAL file. Returns True on success."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    except Exception:
        pass
    try:
        # Write GBA-clamped colors — 16 or 256 depending on input
        padded = list(colors)
        # If >16 colors, write as 256-entry palette; otherwise 16
        target_count = 256 if len(padded) > 16 else 16
        while len(padded) < target_count:
            padded.append((0, 0, 0))
        padded = [clamp_to_gba(*c) for c in padded[:target_count]]
        lines = ["JASC-PAL", "0100", str(target_count)]
        for (r, g, b) in padded:
            lines.append(f"{r} {g} {b}")
        # pokefirered .pal files end with CRLF on Windows typically.  Use
        # Unix newlines + trailing newline — matches the existing files.
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines) + "\n")
        return True
    except Exception:
        return False
