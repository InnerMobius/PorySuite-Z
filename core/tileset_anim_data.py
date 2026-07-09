"""
core/tileset_anim_data.py
Parser for GBA tileset animation definitions from src/tileset_anims.c.

Extracts:
- Animation names, frame file paths, frame counts
- Tile counts and VRAM destination offsets
- Timing info (divisor, phase) from the dispatch functions
- Tileset type (primary/secondary) and init function names
- Frame ordering (including ping-pong sequences like celadon_gym flowers)

The parser works entirely from the C source and the frame PNG files on disk.
No hardcoded animation names — everything is discovered dynamically.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class AnimFrame:
    """One frame of a tile animation."""
    index: int              # frame index in the sequence
    png_path: str           # absolute path to the .png file
    var_name: str = ""      # C variable name (for reference)


@dataclass
class TileAnimation:
    """A single tile animation definition parsed from tileset_anims.c."""
    name: str                       # human-readable name (e.g. "Flower")
    anim_id: str                    # C-style ID (e.g. "General_Flower")
    tileset_name: str               # e.g. "general", "celadon_city"
    tileset_type: str               # "primary" or "secondary"
    frames: List[AnimFrame] = field(default_factory=list)
    frame_order: List[int] = field(default_factory=list)  # indices into frames (may repeat for ping-pong)
    tile_count: int = 0             # tiles per frame
    dest_tile: int = 0              # VRAM tile destination offset
    divisor: int = 1                # timer divisor (how many vblanks between advances)
    phase: int = 0                  # timer phase offset
    counter_max: int = 0            # total animation cycle length
    init_func: str = ""             # e.g. "InitTilesetAnim_General"
    palette_hint: int = -1          # palette slot from "// palette: X NN" comment, -1 = unknown
    dispatch_func: str = ""         # e.g. "TilesetAnim_General"

    @property
    def display_name(self) -> str:
        """Friendly name for UI display."""
        ts = self.tileset_name.replace("_", " ").title()
        # Use the original C-style anim_id for better readability
        # e.g. "General_Water_Current_LandWatersEdge" -> extract after tileset
        nm = self.name.replace("_", " ").title()
        return f"{ts} \u2014 {nm}"

    @property
    def frame_count(self) -> int:
        """Number of unique frames (PNG files)."""
        return len(self.frames)

    @property
    def sequence_length(self) -> int:
        """Length of the frame order sequence (may be > frame_count for ping-pong)."""
        return len(self.frame_order)

    @property
    def fps(self) -> float:
        """Approximate frames per second on GBA hardware (60 Hz vblank)."""
        if self.divisor <= 0:
            return 0.0
        return 60.0 / self.divisor

    @property
    def frame_duration_ms(self) -> float:
        """Milliseconds per animation frame."""
        if self.divisor <= 0:
            return 0.0
        return (self.divisor / 60.0) * 1000.0

    @property
    def total_cycle_ms(self) -> float:
        """Total animation cycle in milliseconds."""
        return self.frame_duration_ms * self.sequence_length

    @property
    def anim_dir(self) -> str:
        """Directory containing the frame PNGs."""
        if self.frames:
            return os.path.dirname(self.frames[0].png_path)
        return ""


def parse_tileset_anims(project_dir: str) -> List[TileAnimation]:
    """Parse src/tileset_anims.c and return all tile animation definitions.

    Args:
        project_dir: Root of the pokefirered-style project.

    Returns:
        List of TileAnimation objects, one per animation group.
    """
    src_path = os.path.join(project_dir, "src", "tileset_anims.c")
    if not os.path.isfile(src_path):
        return []

    with open(src_path, "r", encoding="utf-8", errors="replace") as f:
        source = f.read()

    # ── Step 1: Find all INCBIN_U16 frame declarations ──────────────────────
    # Pattern: INCBIN_U16("data/tilesets/{type}/{tileset}/anim/{name}/{N}.4bpp")
    incbin_re = re.compile(
        r'static\s+const\s+u16\s+(\w+)\[\]\s*=\s*INCBIN_U16\(\s*"([^"]+)"\s*\)',
        re.MULTILINE
    )

    # Map: C var name -> relative .4bpp path
    frame_vars: Dict[str, str] = {}
    for m in incbin_re.finditer(source):
        var_name = m.group(1)
        rel_path = m.group(2)
        frame_vars[var_name] = rel_path

    # ── Step 2: Find frame array definitions ────────────────────────────────
    # Pattern: const u16 *const sName[] = { var0, var1, ... };
    array_re = re.compile(
        r'static\s+const\s+u16\s+\*\s*const\s+(\w+)\[\]\s*=\s*\{([^}]+)\}',
        re.MULTILINE | re.DOTALL
    )

    # Map: array var name -> list of frame var names (in order, may repeat)
    frame_arrays: Dict[str, List[str]] = {}
    for m in array_re.finditer(source):
        arr_name = m.group(1)
        entries_str = m.group(2)
        entries = [e.strip() for e in entries_str.split(",") if e.strip()]
        frame_arrays[arr_name] = entries

    # ── Step 3: Find AppendTilesetAnimToBuffer calls ────────────────────────
    # Pattern: AppendTilesetAnimToBuffer(array[idx], (u16*)(BG_VRAM + TILE_OFFSET_4BPP(N)), M * TILE_SIZE_4BPP)
    append_re = re.compile(
        r'AppendTilesetAnimToBuffer\(\s*'
        r'(\w+)\[([^\]]*)\]\s*,\s*'             # array[index]
        r'\(u16\s*\*\)\s*\(\s*BG_VRAM\s*\+\s*TILE_OFFSET_4BPP\(\s*(\d+)\s*\)\s*\)\s*,\s*'  # dest
        r'(\d+)\s*\*\s*TILE_SIZE_4BPP\s*\)',     # size
        re.MULTILINE
    )

    # Map: queue function name -> (array_name, dest_tile, tile_count)
    queue_info: Dict[str, Tuple[str, int, int]] = {}

    # Find each QueueAnimTiles function and extract its AppendTilesetAnimToBuffer call
    queue_func_re = re.compile(
        r'static\s+void\s+(QueueAnimTiles_\w+)\s*\(u16\s+timer\)\s*\{([^}]+)\}',
        re.MULTILINE | re.DOTALL
    )

    for m in queue_func_re.finditer(source):
        func_name = m.group(1)
        func_body = m.group(2)
        append_m = append_re.search(func_body)
        if append_m:
            arr_name = append_m.group(1)
            dest_tile = int(append_m.group(3))
            tile_count = int(append_m.group(4))
            queue_info[func_name] = (arr_name, dest_tile, tile_count)

    # ── Step 4: Find TilesetAnim dispatch functions (timing) ────────────────
    # Pattern: if (timer % DIVISOR == PHASE) QueueFunc(timer / DIVISOR);
    dispatch_re = re.compile(
        r'static\s+void\s+(TilesetAnim_\w+)\s*\(u16\s+timer\)\s*\{([^}]+(?:\{[^}]*\})*[^}]*)\}',
        re.MULTILINE | re.DOTALL
    )

    timing_re = re.compile(
        r'if\s*\(\s*timer\s*%\s*(\d+)\s*==\s*(\d+)\s*\)\s*\n?\s*(QueueAnimTiles_\w+)\s*\(\s*timer\s*/\s*\d+\s*\)',
        re.MULTILINE
    )

    # Map: queue func name -> (divisor, phase, dispatch func name)
    timing_info: Dict[str, Tuple[int, int, str]] = {}

    for m in dispatch_re.finditer(source):
        dispatch_name = m.group(1)
        body = m.group(2)
        for tm in timing_re.finditer(body):
            divisor = int(tm.group(1))
            phase = int(tm.group(2))
            queue_func = tm.group(3)
            timing_info[queue_func] = (divisor, phase, dispatch_name)

    # ── Step 5: Find Init functions (counter_max, callback assignment) ──────
    # Pattern: void InitTilesetAnim_X(void) { ... CounterMax = N; ... Callback = TilesetAnim_X; }
    init_re = re.compile(
        r'void\s+(InitTilesetAnim_\w+)\s*\(void\)\s*\{([^}]+)\}',
        re.MULTILINE | re.DOTALL
    )

    counter_max_re = re.compile(
        r's(?:Primary|Secondary)TilesetAnimCounterMax\s*=\s*(\d+)'
    )
    callback_re = re.compile(
        r's(?:Primary|Secondary)TilesetAnimCallback\s*=\s*(\w+)'
    )
    primary_re = re.compile(r'sPrimaryTilesetAnimCounter\b')

    # Map: dispatch func name -> (init func name, counter_max, is_primary)
    init_info: Dict[str, Tuple[str, int, bool]] = {}

    for m in init_re.finditer(source):
        init_name = m.group(1)
        body = m.group(2)
        cm = counter_max_re.search(body)
        cb = callback_re.search(body)
        if cm and cb:
            counter_max = int(cm.group(1))
            dispatch_func = cb.group(1)
            is_primary = bool(primary_re.search(body))
            init_info[dispatch_func] = (init_name, counter_max, is_primary)

    # ── Step 5b: Parse palette hints ─────────────────────────────────────
    palette_hints = parse_palette_hints(source)

    # ── Step 6: Assemble TileAnimation objects ──────────────────────────────
    animations: List[TileAnimation] = []

    for queue_func, (arr_name, dest_tile, tile_count) in queue_info.items():
        if arr_name not in frame_arrays:
            continue

        frame_var_list = frame_arrays[arr_name]

        # Get unique frames (deduplicate for ping-pong sequences)
        unique_vars = []
        seen = set()
        for v in frame_var_list:
            if v not in seen:
                unique_vars.append(v)
                seen.add(v)

        # Build frame objects from unique INCBIN vars
        frames: List[AnimFrame] = []
        for idx, var in enumerate(unique_vars):
            if var in frame_vars:
                rel_4bpp = frame_vars[var]
                # Convert .4bpp path to .png path
                rel_png = re.sub(r'\.4bpp$', '.png', rel_4bpp)
                abs_png = os.path.join(project_dir, rel_png)
                frames.append(AnimFrame(
                    index=idx,
                    png_path=abs_png,
                    var_name=var,
                ))

        # Build frame order (indices into the unique frames list)
        var_to_idx = {v: i for i, v in enumerate(unique_vars)}
        frame_order = []
        for v in frame_var_list:
            if v in var_to_idx:
                frame_order.append(var_to_idx[v])

        # Extract tileset info from the array name
        # e.g. sTilesetAnims_General_Flower -> tileset=general, name=flower
        parts = arr_name.replace("sTilesetAnims_", "")
        tileset_name, anim_name = _split_tileset_anim_name(parts, project_dir)

        # Determine primary/secondary from frame paths
        tileset_type = "primary"
        if frames:
            if "/secondary/" in frames[0].png_path.replace("\\", "/"):
                tileset_type = "secondary"

        # Get timing
        divisor, phase, dispatch_name = timing_info.get(
            queue_func, (1, 0, ""))
        init_name, counter_max, _ = init_info.get(
            dispatch_name, ("", 0, False))

        # Get palette hint — check the first INCBIN var for this animation
        pal_hint = -1
        if unique_vars:
            first_var = unique_vars[0]
            if first_var in palette_hints:
                pal_hint = palette_hints[first_var]

        anim = TileAnimation(
            name=anim_name,
            anim_id=parts,
            tileset_name=tileset_name,
            tileset_type=tileset_type,
            frames=frames,
            frame_order=frame_order,
            tile_count=tile_count,
            dest_tile=dest_tile,
            divisor=divisor,
            phase=phase,
            counter_max=counter_max,
            init_func=init_name,
            palette_hint=pal_hint,
            dispatch_func=dispatch_name,
        )
        animations.append(anim)

    # Sort by tileset type (primary first), then tileset name, then name
    animations.sort(key=lambda a: (
        0 if a.tileset_type == "primary" else 1,
        a.tileset_name,
        a.name,
    ))

    return animations


def _camel_to_snake(name: str) -> str:
    """Convert CamelCase to snake_case: 'CeladonCity' -> 'celadon_city'."""
    s = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1_\2', name)
    s = re.sub(r'([a-z\d])([A-Z])', r'\1_\2', s)
    return s.lower()


def _snake_to_camel(name: str) -> str:
    """Convert snake_case to CamelCase: 'celadon_city' -> 'CeladonCity'."""
    return "".join(part.capitalize() for part in name.split("_") if part)


def _split_tileset_anim_name(combined: str, project_dir: str) -> Tuple[str, str]:
    """Split a combined C-style name like 'CeladonCity_Fountain' into
    (tileset_name, anim_name) by checking which tileset directories exist.

    Converts CamelCase to snake_case first (CeladonCity -> celadon_city),
    then tries progressively longer prefixes against the filesystem.
    Also tries collapsing underscores in the anim portion since some
    directory names are run-together (e.g. 'motorizeddoor' not 'motorized_door').
    """
    tilesets_dir = os.path.join(project_dir, "data", "tilesets")

    # Convert CamelCase segments to snake_case, then rejoin with underscore
    raw_parts = combined.split("_")
    snake_parts = []
    for p in raw_parts:
        snake_parts.append(_camel_to_snake(p))
    snake = "_".join(snake_parts)

    # Try progressively longer prefixes as the tileset name
    parts = snake.split("_")
    for i in range(1, len(parts)):
        candidate_tileset = "_".join(parts[:i])
        candidate_anim = "_".join(parts[i:])
        # Also try without underscores in the anim name
        candidate_anim_flat = candidate_anim.replace("_", "")

        for ts_type in ("primary", "secondary"):
            for anim_try in (candidate_anim, candidate_anim_flat):
                anim_dir = os.path.join(
                    tilesets_dir, ts_type, candidate_tileset, "anim", anim_try)
                if os.path.isdir(anim_dir):
                    return candidate_tileset, anim_try

    # Fallback: first part = tileset, rest = anim
    return parts[0], "_".join(parts[1:]) if len(parts) > 1 else "unknown"


def write_timing_to_source(project_dir: str, anim: TileAnimation,
                           new_divisor: int) -> bool:
    """Update the timer divisor for an animation in tileset_anims.c.

    Finds the dispatch function's `if (timer % OLD == PHASE)` line and
    rewrites OLD to new_divisor.  Also updates the `timer / OLD` on the
    same line.

    Returns True on success.
    """
    src_path = os.path.join(project_dir, "src", "tileset_anims.c")
    if not os.path.isfile(src_path):
        return False

    with open(src_path, "r", encoding="utf-8", errors="replace") as f:
        source = f.read()

    # Build the queue function name from the anim_id
    queue_func = f"QueueAnimTiles_{anim.anim_id}"

    # Find:  if (timer % OLD == PHASE)\n    QueueFunc(timer / OLD);
    pattern = re.compile(
        r'(if\s*\(\s*timer\s*%\s*)(\d+)(\s*==\s*'
        + re.escape(str(anim.phase))
        + r'\s*\)\s*\n\s*'
        + re.escape(queue_func)
        + r'\s*\(\s*timer\s*/\s*)(\d+)(\s*\))',
        re.MULTILINE
    )

    match = pattern.search(source)
    if not match:
        return False

    new_source = (source[:match.start()]
                  + match.group(1) + str(new_divisor) + match.group(3)
                  + str(new_divisor) + match.group(5)
                  + source[match.end():])

    with open(src_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(new_source)
    return True


def add_frame_to_anim(project_dir: str, anim: TileAnimation,
                      new_png_path: str) -> Optional[str]:
    """Add a new frame PNG to an existing animation.

    Copies new_png_path to the animation's frame directory as the next
    numbered frame (e.g. if 5 frames exist, copies as 5.png).

    Then updates tileset_anims.c:
    - Adds a new INCBIN_U16 declaration
    - Adds the new frame to the frame array

    Returns the new .png path on success, None on failure.
    """
    import shutil

    if not anim.frames:
        return None

    anim_dir = anim.anim_dir
    if not os.path.isdir(anim_dir):
        return None

    # Determine the next frame number
    existing_nums = []
    for f in os.listdir(anim_dir):
        if f.endswith(".png") and f[:-4].isdigit():
            existing_nums.append(int(f[:-4]))
    next_num = max(existing_nums) + 1 if existing_nums else 0

    # Copy the PNG
    dest_png = os.path.join(anim_dir, f"{next_num}.png")
    try:
        shutil.copy2(new_png_path, dest_png)
    except Exception:
        return None

    # Now update tileset_anims.c
    src_path = os.path.join(project_dir, "src", "tileset_anims.c")
    if not os.path.isfile(src_path):
        return dest_png  # PNG copied but can't update C source

    with open(src_path, "r", encoding="utf-8", errors="replace") as f:
        source = f.read()

    # Derive the C variable name pattern from the last frame
    last_frame = anim.frames[-1]
    last_var = last_frame.var_name
    # Replace the frame number in the var name
    # e.g. sTilesetAnims_General_Flower_Frame4 -> sTilesetAnims_General_Flower_Frame5
    new_var = re.sub(r'Frame\d+$', f'Frame{next_num}', last_var)

    # Derive the .4bpp path from the last frame's path
    last_4bpp = None
    for var, path in _get_frame_vars_from_source(source).items():
        if var == last_var:
            last_4bpp = path
            break
    if not last_4bpp:
        return dest_png

    new_4bpp = re.sub(r'/\d+\.4bpp$', f'/{next_num}.4bpp', last_4bpp)

    # 1. Add INCBIN_U16 declaration after the last frame declaration
    incbin_line = f'static const u16 {new_var}[] = INCBIN_U16("{new_4bpp}");'
    last_incbin_pattern = re.escape(
        f'static const u16 {last_var}[] = INCBIN_U16("{last_4bpp}");')
    source = re.sub(
        last_incbin_pattern,
        lambda m: m.group(0) + "\n" + incbin_line,
        source, count=1
    )

    # 2. Add the new var to the frame array by inserting before the closing
    # brace. The OLD approach matched `lastVar\s*}` which silently FAILED
    # whenever the array had a trailing comma (`lastVar,\n}`) — the common case
    # — so frames got INCBIN'd but never listed, and never played. Inserting
    # before `};` and normalising the trailing comma is robust either way.
    arr_name = f"sTilesetAnims_{anim.anim_id}"
    arr_close = re.compile(
        r'(static\s+const\s+u16\s+\*\s*const\s+' + re.escape(arr_name)
        + r'\[\]\s*=\s*\{.*?)(\n?\}\s*;)',
        re.DOTALL,
    )

    def _add_entry(mm):
        body = mm.group(1).rstrip()
        if not body.endswith('{') and not body.endswith(','):
            body += ','
        return body + f"\n    {new_var}," + mm.group(2)

    source, _n = arr_close.subn(_add_entry, source, count=1)

    with open(src_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(source)

    return dest_png


def remove_frame_from_anim(project_dir: str, anim: TileAnimation,
                           frame_idx: int) -> bool:
    """Remove a frame from an animation in tileset_anims.c.

    Does NOT delete the PNG file — only removes the C references.
    The user can delete the file manually from Explorer.

    Returns True on success.
    """
    if frame_idx < 0 or frame_idx >= len(anim.frames):
        return False
    if len(anim.frames) <= 1:
        return False  # Can't remove the last frame

    frame = anim.frames[frame_idx]
    src_path = os.path.join(project_dir, "src", "tileset_anims.c")
    if not os.path.isfile(src_path):
        return False

    with open(src_path, "r", encoding="utf-8", errors="replace") as f:
        source = f.read()

    # 1. Remove the INCBIN_U16 line for this frame
    incbin_pattern = re.compile(
        r'^static\s+const\s+u16\s+' + re.escape(frame.var_name)
        + r'\[\].*?;\s*\n',
        re.MULTILINE
    )
    source = incbin_pattern.sub('', source, count=1)

    # 2. Remove from frame array (handle trailing/leading commas)
    arr_name = f"sTilesetAnims_{anim.anim_id}"
    # Remove the var from the array, handling commas
    # Pattern: var_name followed by comma, or comma followed by var_name
    source = re.sub(
        re.escape(frame.var_name) + r'\s*,\s*', '', source, count=1
    ) if frame_idx < len(anim.frames) - 1 else re.sub(
        r',\s*' + re.escape(frame.var_name), '', source, count=1
    )

    with open(src_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(source)
    return True


def _get_frame_vars_from_source(source: str) -> Dict[str, str]:
    """Extract all INCBIN_U16 var -> path mappings from source text."""
    incbin_re = re.compile(
        r'static\s+const\s+u16\s+(\w+)\[\]\s*=\s*INCBIN_U16\(\s*"([^"]+)"\s*\)',
        re.MULTILINE
    )
    return {m.group(1): m.group(2) for m in incbin_re.finditer(source)}


def discover_anim_dirs(project_dir: str) -> List[Tuple[str, str, str]]:
    """Find all anim/ directories under data/tilesets/ by scanning the filesystem."""
    results = []
    tilesets_dir = os.path.join(project_dir, "data", "tilesets")
    if not os.path.isdir(tilesets_dir):
        return results
    for ts_type in ("primary", "secondary"):
        type_dir = os.path.join(tilesets_dir, ts_type)
        if not os.path.isdir(type_dir):
            continue
        for tileset_name in os.listdir(type_dir):
            anim_dir = os.path.join(type_dir, tileset_name, "anim")
            if not os.path.isdir(anim_dir):
                continue
            for anim_name in os.listdir(anim_dir):
                anim_path = os.path.join(anim_dir, anim_name)
                if not os.path.isdir(anim_path):
                    continue
                pngs = sorted([
                    f for f in os.listdir(anim_path)
                    if f.endswith(".png") and f[:-4].isdigit()
                ])
                if pngs:
                    results.append((ts_type, tileset_name, anim_name))
    return results


@dataclass
class OrphanFrame:
    """A frame PNG on disk that no animation in tileset_anims.c references."""
    abs_path: str
    rel_path: str          # path relative to project_dir, forward slashes
    size: int              # bytes
    tileset: str           # tileset folder name
    tileset_type: str      # "primary" / "secondary"
    anim_name: str         # anim folder name
    whole_anim_orphaned: bool  # True if NO frame in this folder is referenced


def find_orphaned_frames(project_dir: str) -> List["OrphanFrame"]:
    """Return every numbered frame PNG on disk that is not referenced by any
    animation in ``src/tileset_anims.c``.

    Two ways a frame becomes orphaned:
    * a single frame was deleted from an animation (the ``N.png`` stayed on
      disk) — its folder still has other, referenced frames;
    * a whole animation was removed or renamed — its old folder still has all
      its PNGs but nothing references them.

    The distinction is recorded in ``whole_anim_orphaned`` so the UI can group
    them.  Files are matched case-insensitively on their absolute, normalised
    path, so a Windows path never mismatches a reference parsed from the C
    source.
    """
    def _norm(p: str) -> str:
        return os.path.normcase(os.path.abspath(p))

    referenced = set()
    for anim in parse_tileset_anims(project_dir):
        for fr in anim.frames:
            if fr.png_path:
                referenced.add(_norm(fr.png_path))

    orphans: List[OrphanFrame] = []
    tilesets_dir = os.path.join(project_dir, "data", "tilesets")
    if not os.path.isdir(tilesets_dir):
        return orphans

    for ts_type in ("primary", "secondary"):
        type_dir = os.path.join(tilesets_dir, ts_type)
        if not os.path.isdir(type_dir):
            continue
        for tileset_name in os.listdir(type_dir):
            anim_root = os.path.join(type_dir, tileset_name, "anim")
            if not os.path.isdir(anim_root):
                continue
            for anim_name in os.listdir(anim_root):
                anim_path = os.path.join(anim_root, anim_name)
                if not os.path.isdir(anim_path):
                    continue
                pngs = [
                    f for f in os.listdir(anim_path)
                    if f.lower().endswith(".png") and f[:-4].isdigit()
                ]
                if not pngs:
                    continue
                abs_pngs = {f: os.path.join(anim_path, f) for f in pngs}
                any_referenced = any(
                    _norm(p) in referenced for p in abs_pngs.values())
                for fname, apath in sorted(abs_pngs.items()):
                    if _norm(apath) in referenced:
                        continue
                    try:
                        size = os.path.getsize(apath)
                    except OSError:
                        size = 0
                    rel = os.path.relpath(apath, project_dir).replace("\\", "/")
                    orphans.append(OrphanFrame(
                        abs_path=apath,
                        rel_path=rel,
                        size=size,
                        tileset=tileset_name,
                        tileset_type=ts_type,
                        anim_name=anim_name,
                        whole_anim_orphaned=not any_referenced,
                    ))
    return orphans


# ═══════════════════════════════════════════════════════════════════════════════
#  Door Animation Parser — parses src/field_door.c
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class DoorAnimation:
    """A door animation parsed from field_door.c."""
    name: str               # e.g. "General", "Pallet"
    var_name: str           # C var name, e.g. "sDoorAnimTiles_General"
    png_path: str           # absolute path to the .png file
    metatile_id: str        # e.g. "METATILE_General_Door"
    sound_type: str         # "normal" or "sliding"
    door_size: str          # "1x1" or "1x2"
    palette_nums: List[int] = field(default_factory=list)
    frame_count: int = 3    # doors always have 3 animation frames
    frame_w: int = 16       # pixels per frame width
    frame_h: int = 16       # pixels per frame height (16 for 1x1, 32 for 1x2)

    @property
    def display_name(self) -> str:
        nm = self.name.replace("_", " ").title()
        return f"Door \u2014 {nm}"

    @property
    def frame_duration_ms(self) -> float:
        """Door frames are 4 ticks each at 60fps."""
        return (4 / 60.0) * 1000.0  # ~67ms

    @property
    def anim_dir(self) -> str:
        return os.path.dirname(self.png_path)


def parse_door_anims(project_dir: str) -> List[DoorAnimation]:
    """Parse field_door.c and return all door animation definitions."""
    src_path = os.path.join(project_dir, "src", "field_door.c")
    if not os.path.isfile(src_path):
        return []

    with open(src_path, "r", encoding="utf-8", errors="replace") as f:
        source = f.read()

    # Find INCBIN_U8 declarations for door tiles
    incbin_re = re.compile(
        r'static\s+const\s+u8\s+(sDoorAnimTiles_\w+)\[\]\s*=\s*'
        r'INCBIN_U8\(\s*"([^"]+)"\s*\)',
        re.MULTILINE
    )
    door_tiles: Dict[str, str] = {}  # var_name -> rel path
    for m in incbin_re.finditer(source):
        door_tiles[m.group(1)] = m.group(2)

    # Find palette assignments
    pal_re = re.compile(
        r'static\s+const\s+u8\s+(sDoorAnimPalettes_\w+)\[\]\s*=\s*\{([^}]+)\}',
        re.MULTILINE
    )
    door_pals: Dict[str, List[int]] = {}
    for m in pal_re.finditer(source):
        name = m.group(1)
        vals = [int(v.strip()) for v in m.group(2).split(",") if v.strip().isdigit()]
        door_pals[name] = vals

    # Find sDoorGraphics entries
    gfx_re = re.compile(
        r'\{\s*(METATILE_\w+)\s*,\s*(DOOR_SOUND_\w+)\s*,\s*(DOOR_SIZE_\w+)\s*,\s*'
        r'(sDoorAnimTiles_\w+)\s*,\s*(sDoorAnimPalettes_\w+)\s*\}',
        re.MULTILINE
    )

    results: List[DoorAnimation] = []
    for m in gfx_re.finditer(source):
        metatile_id = m.group(1)
        sound = "sliding" if "SLIDING" in m.group(2) else "normal"
        size = "1x2" if "1x2" in m.group(3) else "1x1"
        tiles_var = m.group(4)
        pals_var = m.group(5)

        if tiles_var not in door_tiles:
            continue  # empty/placeholder door

        rel_4bpp = door_tiles[tiles_var]
        rel_png = re.sub(r'\.4bpp$', '.png', rel_4bpp)
        abs_png = os.path.join(project_dir, rel_png)

        if not os.path.isfile(abs_png):
            continue

        # Extract name from var: sDoorAnimTiles_General -> General
        name = tiles_var.replace("sDoorAnimTiles_", "")

        pal_nums = door_pals.get(pals_var, [])
        frame_h = 32 if size == "1x2" else 16

        results.append(DoorAnimation(
            name=name,
            var_name=tiles_var,
            png_path=abs_png,
            metatile_id=metatile_id,
            sound_type=sound,
            door_size=size,
            palette_nums=pal_nums,
            frame_w=16,
            frame_h=frame_h,
        ))

    return results


# ═══════════════════════════════════════════════════════════════════════════════
#  Field Effect Animation Parser
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class FieldEffectAnimation:
    """A field effect animation (sprite-based) parsed from the source."""
    name: str               # e.g. "TallGrass", "Splash"
    png_path: str           # absolute path to spritesheet .png
    frame_w: int            # frame width in pixels (tile_w * 8)
    frame_h: int            # frame height in pixels (tile_h * 8)
    frame_count: int        # number of frames in the spritesheet
    anim_sequence: List[Tuple[int, int]] = field(default_factory=list)
    # list of (frame_idx, duration_ticks)

    @property
    def display_name(self) -> str:
        # CamelCase to spaced: TallGrass -> Tall Grass
        nm = re.sub(r'([a-z])([A-Z])', r'\1 \2', self.name)
        return f"Field Effect \u2014 {nm}"

    @property
    def frame_duration_ms(self) -> float:
        """Average frame duration if sequence exists."""
        if self.anim_sequence:
            avg_ticks = sum(d for _, d in self.anim_sequence) / len(self.anim_sequence)
            return (avg_ticks / 60.0) * 1000.0
        return (8 / 60.0) * 1000.0  # default 8 ticks

    @property
    def anim_dir(self) -> str:
        return os.path.dirname(self.png_path)


def parse_field_effect_anims(project_dir: str) -> List[FieldEffectAnimation]:
    """Parse field effect object graphics and animation tables.

    Reads:
    - src/data/object_events/object_event_graphics.h for INCBIN paths
    - src/data/field_effects/field_effect_objects.h for frame tables and anim cmds
    """
    gfx_path = os.path.join(
        project_dir, "src", "data", "object_events",
        "object_event_graphics.h")
    feo_path = os.path.join(
        project_dir, "src", "data", "field_effects",
        "field_effect_objects.h")

    if not os.path.isfile(gfx_path) or not os.path.isfile(feo_path):
        return []

    with open(gfx_path, "r", encoding="utf-8", errors="replace") as f:
        gfx_source = f.read()
    with open(feo_path, "r", encoding="utf-8", errors="replace") as f:
        feo_source = f.read()

    # Step 1: Find INCBIN paths for field effect pics
    incbin_re = re.compile(
        r'(?:const\s+u\d+\s+)(gFieldEffectObjectPic_(\w+))\[\]\s*=\s*'
        r'INCBIN_U\d+\(\s*"([^"]+)"\s*\)',
        re.MULTILINE
    )
    pic_paths: Dict[str, Tuple[str, str]] = {}  # pic_name -> (var, rel_path)
    for m in incbin_re.finditer(gfx_source):
        var_name = m.group(1)
        pic_name = m.group(2)  # e.g. "TallGrass"
        rel_path = m.group(3)
        pic_paths[pic_name] = (var_name, rel_path)

    # Step 2: Parse overworld_frame tables to get frame dimensions and count
    # overworld_frame(gFieldEffectObjectPic_NAME, W, H, N)
    frame_re = re.compile(
        r'overworld_frame\(\s*gFieldEffectObjectPic_(\w+)\s*,\s*'
        r'(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)',
        re.MULTILINE
    )

    # Map: pic_name -> (tile_w, tile_h, max_frame_idx)
    frame_info: Dict[str, Tuple[int, int, int]] = {}
    for m in frame_re.finditer(feo_source):
        pic_name = m.group(1)
        tw = int(m.group(2))
        th = int(m.group(3))
        frame_n = int(m.group(4))
        if pic_name not in frame_info:
            frame_info[pic_name] = (tw, th, frame_n)
        else:
            old_tw, old_th, old_max = frame_info[pic_name]
            frame_info[pic_name] = (tw, th, max(old_max, frame_n))

    # Step 3: Parse ANIMCMD_FRAME sequences
    # Find sAnim_Name[] = { ANIMCMD_FRAME(idx, dur), ... ANIMCMD_END };
    anim_block_re = re.compile(
        r'static\s+const\s+union\s+AnimCmd\s+sAnim_(\w+)\[\]\s*=\s*\{([^;]+);',
        re.MULTILINE | re.DOTALL
    )
    anim_frame_re = re.compile(r'ANIMCMD_FRAME\(\s*(\d+)\s*,\s*(\d+)')

    anim_sequences: Dict[str, List[Tuple[int, int]]] = {}
    for m in anim_block_re.finditer(feo_source):
        anim_name = m.group(1)
        body = m.group(2)
        frames_list = []
        for fm in anim_frame_re.finditer(body):
            frames_list.append((int(fm.group(1)), int(fm.group(2))))
            # Stop at first ANIMCMD_END/ANIMCMD_JUMP
            rest = body[fm.end():]
            if re.match(r'\s*\}\s*$', rest) or 'ANIMCMD_END' in rest[:40]:
                pass  # continue reading
        # Just grab all ANIMCMD_FRAME entries
        frames_list = [(int(fm.group(1)), int(fm.group(2)))
                       for fm in anim_frame_re.finditer(body)]
        if frames_list:
            anim_sequences[anim_name] = frames_list

    # Step 4: Assemble
    results: List[FieldEffectAnimation] = []
    for pic_name, (var_name, rel_path) in sorted(pic_paths.items()):
        rel_png = re.sub(r'\.4bpp$', '.png', rel_path)
        abs_png = os.path.join(project_dir, rel_png)
        if not os.path.isfile(abs_png):
            continue

        tw, th, max_frame = frame_info.get(pic_name, (2, 2, 0))
        frame_w = tw * 8
        frame_h = th * 8
        frame_count = max_frame + 1

        # Try to match anim sequence by name
        anim_seq = anim_sequences.get(pic_name, [])

        results.append(FieldEffectAnimation(
            name=pic_name,
            png_path=abs_png,
            frame_w=frame_w,
            frame_h=frame_h,
            frame_count=frame_count,
            anim_sequence=anim_seq,
        ))

    return results


# ═══════════════════════════════════════════════════════════════════════════════
#  Palette Hint Parser
# ═══════════════════════════════════════════════════════════════════════════════


def parse_palette_hints(source: str) -> Dict[str, int]:
    """Parse ``// palette: tileset NN`` comments that appear before INCBIN
    blocks in tileset_anims.c.

    Returns a mapping of the first INCBIN variable name that follows each
    comment to the palette slot number *NN*.
    """
    # Pattern: a "// palette: WORD NN" comment followed (possibly after
    # blank lines) by a static const u16 sTilesetAnims_... INCBIN decl.
    hint_re = re.compile(
        r'//\s*palette:\s*\w+\s+(\d+)\s*\n'
        r'(?:\s*\n)*'
        r'static\s+const\s+u16\s+(\w+)\[\]',
        re.MULTILINE,
    )
    result: Dict[str, int] = {}
    for m in hint_re.finditer(source):
        slot = int(m.group(1))
        var_name = m.group(2)
        result[var_name] = slot
    return result


# ═══════════════════════════════════════════════════════════════════════════════
#  Tileset Header Parser
# ═══════════════════════════════════════════════════════════════════════════════


def parse_tilesets_from_headers(project_dir: str) -> List[dict]:
    """Parse ``src/data/tilesets/headers.h`` to get every tileset definition.

    Returns a list of dicts with keys:
        name, c_name, is_secondary, callback, dir_name
    """
    hdr_path = os.path.join(
        project_dir, "src", "data", "tilesets", "headers.h")
    if not os.path.isfile(hdr_path):
        return []

    with open(hdr_path, "r", encoding="utf-8", errors="replace") as f:
        source = f.read()

    # Match struct initialisers like:
    # const struct Tileset gTileset_General = {
    #     .isSecondary = FALSE,
    #     ...
    #     .callback = InitTilesetAnim_General,
    #  };
    tileset_re = re.compile(
        r'const\s+struct\s+Tileset\s+(\w+)\s*=\s*\{([^}]+)\}',
        re.MULTILINE | re.DOTALL,
    )
    secondary_re = re.compile(r'\.isSecondary\s*=\s*(\w+)')
    callback_re = re.compile(r'\.callback\s*=\s*(\w+)')

    results: List[dict] = []
    for m in tileset_re.finditer(source):
        c_name = m.group(1)  # e.g. gTileset_General
        body = m.group(2)

        sm = secondary_re.search(body)
        is_secondary = False
        if sm and sm.group(1) not in ("FALSE", "0"):
            is_secondary = True

        cm = callback_re.search(body)
        callback = None
        if cm and cm.group(1) != "NULL":
            callback = cm.group(1)

        # Derive human name: gTileset_PalletTown -> PalletTown
        raw_name = c_name.replace("gTileset_", "")
        dir_name = _camel_to_snake(raw_name)

        results.append({
            "name": raw_name,
            "c_name": c_name,
            "is_secondary": is_secondary,
            "callback": callback,
            "dir_name": dir_name,
        })

    return results


# ═══════════════════════════════════════════════════════════════════════════════
#  Source-level write helpers
# ═══════════════════════════════════════════════════════════════════════════════


def write_start_tile_to_source(project_dir: str, anim: TileAnimation,
                               new_start_tile: int) -> bool:
    """Rewrite the TILE_OFFSET_4BPP value in the QueueAnimTiles function
    for *anim* to *new_start_tile*.  Returns True on success.
    """
    src_path = os.path.join(project_dir, "src", "tileset_anims.c")
    if not os.path.isfile(src_path):
        return False

    with open(src_path, "r", encoding="utf-8", errors="replace") as f:
        source = f.read()

    queue_func = f"QueueAnimTiles_{anim.anim_id}"
    # Find the function body
    func_re = re.compile(
        r'(static\s+void\s+' + re.escape(queue_func)
        + r'\s*\(u16\s+timer\)\s*\{)',
        re.MULTILINE,
    )
    fm = func_re.search(source)
    if not fm:
        return False

    # Find TILE_OFFSET_4BPP(N) inside the function body
    body_start = fm.end()
    brace_end = source.find("}", body_start)
    if brace_end == -1:
        return False
    body = source[body_start:brace_end]

    offset_re = re.compile(r'TILE_OFFSET_4BPP\(\s*(\d+)\s*\)')
    om = offset_re.search(body)
    if not om:
        return False

    abs_start = body_start + om.start()
    abs_end = body_start + om.end()
    replacement = f"TILE_OFFSET_4BPP({new_start_tile})"
    new_source = source[:abs_start] + replacement + source[abs_end:]

    with open(src_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(new_source)
    return True


def write_tile_amount_to_source(project_dir: str, anim: TileAnimation,
                                new_tile_amount: int) -> bool:
    """Rewrite the tile-count multiplier (``OLD * TILE_SIZE_4BPP``) inside
    the QueueAnimTiles function for *anim*.  Returns True on success.
    """
    src_path = os.path.join(project_dir, "src", "tileset_anims.c")
    if not os.path.isfile(src_path):
        return False

    with open(src_path, "r", encoding="utf-8", errors="replace") as f:
        source = f.read()

    queue_func = f"QueueAnimTiles_{anim.anim_id}"
    func_re = re.compile(
        r'(static\s+void\s+' + re.escape(queue_func)
        + r'\s*\(u16\s+timer\)\s*\{)',
        re.MULTILINE,
    )
    fm = func_re.search(source)
    if not fm:
        return False

    body_start = fm.end()
    brace_end = source.find("}", body_start)
    if brace_end == -1:
        return False
    body = source[body_start:brace_end]

    size_re = re.compile(r'(\d+)\s*\*\s*TILE_SIZE_4BPP')
    sm = size_re.search(body)
    if not sm:
        return False

    abs_start = body_start + sm.start(1)
    abs_end = body_start + sm.end(1)
    new_source = source[:abs_start] + str(new_tile_amount) + source[abs_end:]

    with open(src_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(new_source)
    return True


def write_phase_to_source(project_dir: str, anim: TileAnimation,
                          new_phase: int) -> bool:
    """Rewrite the timer phase for *anim*'s dispatch call.  Returns True on
    success.
    """
    src_path = os.path.join(project_dir, "src", "tileset_anims.c")
    if not os.path.isfile(src_path):
        return False

    with open(src_path, "r", encoding="utf-8", errors="replace") as f:
        source = f.read()

    queue_func = f"QueueAnimTiles_{anim.anim_id}"
    # Pattern: if (timer % DIVISOR == OLD_PHASE)\n    QueueAnimTiles_...(timer / DIVISOR);
    pattern = re.compile(
        r'(if\s*\(\s*timer\s*%\s*\d+\s*==\s*)(\d+)'
        r'(\s*\)\s*\n\s*' + re.escape(queue_func) + r'\s*\()',
        re.MULTILINE,
    )
    m = pattern.search(source)
    if not m:
        return False

    new_source = (source[:m.start(2)]
                  + str(new_phase)
                  + source[m.end(2):])

    with open(src_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(new_source)
    return True


def write_counter_max_to_source(project_dir: str, anim: TileAnimation,
                                new_counter_max: int) -> bool:
    """Rewrite the counter-max value in *anim*'s Init function.  Returns True
    on success.
    """
    if not anim.init_func:
        return False

    src_path = os.path.join(project_dir, "src", "tileset_anims.c")
    if not os.path.isfile(src_path):
        return False

    with open(src_path, "r", encoding="utf-8", errors="replace") as f:
        source = f.read()

    # Find the Init function body
    init_re = re.compile(
        r'(void\s+' + re.escape(anim.init_func) + r'\s*\(void\)\s*\{)',
        re.MULTILINE,
    )
    im = init_re.search(source)
    if not im:
        return False

    body_start = im.end()
    brace_end = source.find("}", body_start)
    if brace_end == -1:
        return False
    body = source[body_start:brace_end]

    cm_re = re.compile(
        r'(s(?:Primary|Secondary)TilesetAnimCounterMax\s*=\s*)(\d+)'
    )
    cm = cm_re.search(body)
    if not cm:
        return False

    abs_start = body_start + cm.start(2)
    abs_end = body_start + cm.end(2)
    new_source = source[:abs_start] + str(new_counter_max) + source[abs_end:]

    with open(src_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(new_source)
    return True


# ═══════════════════════════════════════════════════════════════════════════════
#  Add / Remove full animations
# ═══════════════════════════════════════════════════════════════════════════════


def add_animation_to_tileset(
    project_dir: str,
    tileset_name: str,
    tileset_type: str,
    anim_name: str,
    start_tile: int,
    tile_amount: int,
    divisor: int,
    frame_png_paths: List[str],
) -> Optional[TileAnimation]:
    """Create a brand-new animation for a tileset and wire it into C source.

    *tileset_name* / *anim_name* are in snake_case (e.g. ``"general"``,
    ``"waterfall"``).  *frame_png_paths* is a list of existing PNG files that
    will be copied into the animation directory as ``0.png``, ``1.png``, etc.

    Returns a populated :class:`TileAnimation` on success, or ``None`` on
    failure.
    """
    import shutil

    if not frame_png_paths:
        return None

    src_path = os.path.join(project_dir, "src", "tileset_anims.c")
    if not os.path.isfile(src_path):
        return None

    frame_count = len(frame_png_paths)

    # ── 1. Create frame directory and copy PNGs ───────────────────────────
    anim_dir = os.path.join(
        project_dir, "data", "tilesets", tileset_type, tileset_name,
        "anim", anim_name)
    os.makedirs(anim_dir, exist_ok=True)

    for idx, png_path in enumerate(frame_png_paths):
        dest = os.path.join(anim_dir, f"{idx}.png")
        shutil.copy2(png_path, dest)

    # ── 2. Build C-style identifier ──────────────────────────────────────
    tileset_camel = _snake_to_camel(tileset_name)
    anim_camel = _snake_to_camel(anim_name)
    c_id = f"{tileset_camel}_{anim_camel}"  # e.g. "General_Waterfall"

    rel_base = f"data/tilesets/{tileset_type}/{tileset_name}/anim/{anim_name}"

    # ── 3. Read existing source ──────────────────────────────────────────
    with open(src_path, "r", encoding="utf-8", errors="replace") as f:
        source = f.read()

    # ── 4a. Build INCBIN declarations ────────────────────────────────────
    incbin_lines = [f"// palette: {tileset_name} 00"]
    for idx in range(frame_count):
        var = f"sTilesetAnims_{c_id}_Frame{idx}"
        incbin_lines.append(
            f'static const u16 {var}[] = INCBIN_U16("{rel_base}/{idx}.4bpp");'
        )
    incbin_block = "\n".join(incbin_lines) + "\n"

    # ── 4b. Build frame array ────────────────────────────────────────────
    arr_entries = ",\n    ".join(
        f"sTilesetAnims_{c_id}_Frame{i}" for i in range(frame_count))
    arr_block = (
        f"\nstatic const u16 *const sTilesetAnims_{c_id}[] = {{\n"
        f"    {arr_entries},\n"
        f"}};\n"
    )

    # ── 4c. Build QueueAnimTiles function ────────────────────────────────
    queue_block = (
        f"\nstatic void QueueAnimTiles_{c_id}(u16 timer)\n"
        f"{{\n"
        f"    AppendTilesetAnimToBuffer(sTilesetAnims_{c_id}"
        f"[timer % ARRAY_COUNT(sTilesetAnims_{c_id})], "
        f"(u16 *)(BG_VRAM + TILE_OFFSET_4BPP({start_tile})), "
        f"{tile_amount} * TILE_SIZE_4BPP);\n"
        f"}}\n"
    )

    # ── Insert INCBIN + array before the first QueueAnimTiles_ line ──────
    first_queue = re.search(
        r'^static\s+void\s+QueueAnimTiles_', source, re.MULTILINE)
    if first_queue:
        insert_pos = first_queue.start()
        source = (source[:insert_pos]
                  + incbin_block + arr_block + "\n"
                  + source[insert_pos:])
    else:
        # No existing queue functions — append before end of file
        source += "\n" + incbin_block + arr_block + "\n"

    # ── Insert QueueAnimTiles before the dispatch for this tileset ────────
    dispatch_name = f"TilesetAnim_{tileset_camel}"
    dispatch_match = re.search(
        r'^static\s+void\s+' + re.escape(dispatch_name) + r'\s*\(',
        source, re.MULTILINE,
    )
    if dispatch_match:
        insert_pos = dispatch_match.start()
        source = source[:insert_pos] + queue_block + "\n" + source[insert_pos:]
    else:
        # Insert before the first dispatch function, or at end
        first_dispatch = re.search(
            r'^static\s+void\s+TilesetAnim_', source, re.MULTILINE)
        if first_dispatch:
            insert_pos = first_dispatch.start()
            source = (source[:insert_pos] + queue_block + "\n"
                      + source[insert_pos:])
        else:
            source += queue_block + "\n"

    # ── 5. Wire into dispatch / init ─────────────────────────────────────
    # Re-search after insertions
    dispatch_match = re.search(
        r'(static\s+void\s+' + re.escape(dispatch_name)
        + r'\s*\(u16\s+timer\)\s*\{)([^}]+(?:\{[^}]*\})*[^}]*)\}',
        source, re.MULTILINE | re.DOTALL,
    )

    primary_secondary = "Primary" if tileset_type == "primary" else "Secondary"
    init_func_name = f"InitTilesetAnim_{tileset_camel}"

    if dispatch_match:
        # Existing dispatch — find the highest phase and add ours
        body = dispatch_match.group(2)
        existing_phases = [int(x) for x in re.findall(
            r'timer\s*%\s*\d+\s*==\s*(\d+)', body)]
        new_phase = max(existing_phases) + 1 if existing_phases else 0

        new_line = (
            f"\n    if (timer % {divisor} == {new_phase})\n"
            f"        QueueAnimTiles_{c_id}(timer / {divisor});"
        )
        # Insert before the closing brace of the dispatch function
        body_end = dispatch_match.end(2)
        source = source[:body_end] + new_line + source[body_end:]
        phase = new_phase

        # Find existing counter_max from the init function
        init_match = re.search(
            r'void\s+' + re.escape(init_func_name)
            + r'\s*\(void\)\s*\{([^}]+)\}',
            source, re.MULTILINE | re.DOTALL,
        )
        counter_max = 0
        if init_match:
            cm_m = re.search(
                r's' + primary_secondary + r'TilesetAnimCounterMax\s*=\s*(\d+)',
                init_match.group(1))
            if cm_m:
                counter_max = int(cm_m.group(1))
    else:
        # Create new dispatch + init
        new_phase = 0
        phase = 0
        counter_max = divisor * frame_count * 10

        dispatch_block = (
            f"\nstatic void {dispatch_name}(u16 timer)\n"
            f"{{\n"
            f"    if (timer % {divisor} == 0)\n"
            f"        QueueAnimTiles_{c_id}(timer / {divisor});\n"
            f"}}\n"
        )

        init_block = (
            f"\nvoid {init_func_name}(void)\n"
            f"{{\n"
            f"    s{primary_secondary}TilesetAnimCounter = 0;\n"
            f"    s{primary_secondary}TilesetAnimCounterMax = {counter_max};\n"
            f"    s{primary_secondary}TilesetAnimCallback = {dispatch_name};\n"
            f"}}\n"
        )

        # Find where to insert — after the last Init function or at end
        last_init = None
        for m in re.finditer(
                r'void\s+InitTilesetAnim_\w+\s*\(void\)\s*\{[^}]+\}',
                source, re.MULTILINE | re.DOTALL):
            last_init = m
        if last_init:
            insert_pos = last_init.end()
            source = (source[:insert_pos] + dispatch_block + init_block
                      + source[insert_pos:])
        else:
            source += dispatch_block + init_block

        # Update headers.h to wire the callback
        hdr_path = os.path.join(
            project_dir, "src", "data", "tilesets", "headers.h")
        if os.path.isfile(hdr_path):
            with open(hdr_path, "r", encoding="utf-8", errors="replace") as f:
                hdr = f.read()
            # Find gTileset_{TilesetCamel} and change .callback = NULL
            tileset_c = f"gTileset_{tileset_camel}"
            ts_match = re.search(
                r'(const\s+struct\s+Tileset\s+' + re.escape(tileset_c)
                + r'\s*=\s*\{[^}]*?\.callback\s*=\s*)NULL',
                hdr, re.DOTALL,
            )
            if ts_match:
                hdr = (hdr[:ts_match.end(1)]
                       + init_func_name
                       + hdr[ts_match.end():])
                with open(hdr_path, "w", encoding="utf-8",
                          newline="\n") as f:
                    f.write(hdr)

        # A brand-new InitTilesetAnim_<Tileset> must ALSO be declared in
        # include/tileset_anims.h, or tilesets.c fails to compile
        # ("InitTilesetAnim_X undeclared here") when headers.h references it as
        # the tileset .callback.
        ensure_init_declaration(project_dir, init_func_name)

    # ── Write updated source ─────────────────────────────────────────────
    with open(src_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(source)

    # ── 6. Build and return TileAnimation ────────────────────────────────
    frames = []
    for idx in range(frame_count):
        frames.append(AnimFrame(
            index=idx,
            png_path=os.path.join(anim_dir, f"{idx}.png"),
            var_name=f"sTilesetAnims_{c_id}_Frame{idx}",
        ))

    return TileAnimation(
        name=anim_name,
        anim_id=c_id,
        tileset_name=tileset_name,
        tileset_type=tileset_type,
        frames=frames,
        frame_order=list(range(frame_count)),
        tile_count=tile_amount,
        dest_tile=start_tile,
        divisor=divisor,
        phase=phase,
        counter_max=counter_max,
        init_func=init_func_name,
        palette_hint=0,
        dispatch_func=dispatch_name,
    )


def ensure_init_declaration(project_dir: str, init_func_name: str) -> bool:
    """Make sure ``void <init_func_name>(void);`` is declared in
    include/tileset_anims.h. Returns True if the header now contains it."""
    hdr = os.path.join(project_dir, "include", "tileset_anims.h")
    if not os.path.isfile(hdr):
        return False
    with open(hdr, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    decl = f"void {init_func_name}(void);"
    if decl in text:
        return True
    # Insert after the last existing InitTilesetAnim_* declaration, else before
    # the include guard's #endif.
    decls = list(re.finditer(
        r'void\s+InitTilesetAnim_\w+\s*\(void\);', text))
    if decls:
        pos = decls[-1].end()
        text = text[:pos] + "\n" + decl + text[pos:]
    else:
        m = re.search(r'\n#endif\b', text)
        if m:
            text = text[:m.start()] + "\n" + decl + "\n" + text[m.start():]
        else:
            text = text.rstrip() + "\n" + decl + "\n"
    with open(hdr, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return True


def remove_init_declaration(project_dir: str, init_func_name: str) -> bool:
    """Remove a ``void <init_func_name>(void);`` line from
    include/tileset_anims.h (called when the init function itself is deleted, so
    no orphaned prototype is left behind). Returns True if a line was removed."""
    hdr = os.path.join(project_dir, "include", "tileset_anims.h")
    if not os.path.isfile(hdr):
        return False
    with open(hdr, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    new = re.sub(
        r'^[ \t]*void[ \t]+' + re.escape(init_func_name) + r'[ \t]*\(void\);[ \t]*\n',
        '', text, flags=re.MULTILINE)
    if new == text:
        return False
    with open(hdr, "w", encoding="utf-8", newline="\n") as f:
        f.write(new)
    return True


def sync_tileset_anim_declarations(project_dir: str) -> int:
    """Self-heal: ensure EVERY ``void InitTilesetAnim_X(void)`` defined in
    src/tileset_anims.c has a matching declaration in include/tileset_anims.h.
    Fixes projects where an animation was added before the declaration step
    existed (the "InitTilesetAnim_X undeclared here" build error). Returns the
    number of declarations added."""
    src = os.path.join(project_dir, "src", "tileset_anims.c")
    if not os.path.isfile(src):
        return 0
    with open(src, "r", encoding="utf-8", errors="replace") as f:
        source = f.read()
    defined = re.findall(r'void\s+(InitTilesetAnim_\w+)\s*\(void\)\s*\{', source)
    added = 0
    for name in dict.fromkeys(defined):  # de-dup, keep order
        hdr = os.path.join(project_dir, "include", "tileset_anims.h")
        before = ''
        if os.path.isfile(hdr):
            with open(hdr, "r", encoding="utf-8", errors="replace") as f:
                before = f.read()
        if f"void {name}(void);" not in before:
            if ensure_init_declaration(project_dir, name):
                added += 1
    return added


def resync_anim_frame_array(project_dir: str, anim: "TileAnimation") -> bool:
    """Rebuild an animation's ``sTilesetAnims_<id>[]`` array so it lists EVERY
    ``Frame<N>`` INCBIN that exists for it, in numeric order. Repairs animations
    whose frames were INCBIN'd but never added to the array (the "Add Frame said
    it worked but the frame didn't play" bug). Returns True if the array was
    rewritten."""
    src = os.path.join(project_dir, "src", "tileset_anims.c")
    if not os.path.isfile(src):
        return False
    with open(src, "r", encoding="utf-8", errors="replace") as f:
        source = f.read()
    prefix = f"sTilesetAnims_{anim.anim_id}_Frame"
    frame_nums = sorted(int(n) for n in re.findall(
        re.escape(prefix) + r'(\d+)\b', source))
    frame_nums = list(dict.fromkeys(frame_nums))
    if not frame_nums:
        return False
    arr_name = f"sTilesetAnims_{anim.anim_id}"
    arr_re = re.compile(
        r'static\s+const\s+u16\s+\*\s*const\s+' + re.escape(arr_name)
        + r'\[\]\s*=\s*\{.*?\}\s*;', re.DOTALL)
    m = arr_re.search(source)
    if not m:
        return False
    entries = ",\n    ".join(f"{prefix}{n}" for n in frame_nums)
    new_arr = (f"static const u16 *const {arr_name}[] = {{\n"
               f"    {entries},\n}};")
    source = source[:m.start()] + new_arr + source[m.end():]
    with open(src, "w", encoding="utf-8", newline="\n") as f:
        f.write(source)
    return True


def rebuild_anim_frames_from_disk(project_dir: str,
                                  anim: "TileAnimation") -> bool:
    """Rebuild an animation's INCBIN declarations AND its frame array from the
    ``N.png`` files that exist on disk, in clean numeric order (0,1,2,…).

    Repairs animations whose frame declarations got scrambled or dropped by
    add/remove/resize (e.g. missing Frame0/Frame2, out-of-order entries). It is
    idempotent — running it on a healthy animation is a no-op. Returns True if
    the source was rewritten."""
    src = os.path.join(project_dir, "src", "tileset_anims.c")
    if not os.path.isfile(src):
        return False
    anim_dir = anim.anim_dir
    if not anim_dir or not os.path.isdir(anim_dir):
        return False

    nums = sorted(int(f[:-4]) for f in os.listdir(anim_dir)
                  if f.lower().endswith(".png") and f[:-4].isdigit())
    if not nums:
        return False

    # Derive the INCBIN relative path from the tileset/anim identity.
    rel_base = (f"data/tilesets/{anim.tileset_type}/{anim.tileset_name}/anim/"
                f"{anim.name}")
    cid = anim.anim_id

    with open(src, "r", encoding="utf-8", errors="replace") as f:
        source = f.read()

    # Remove every existing Frame INCBIN line for this animation.
    incbin_re = re.compile(
        r'^static\s+const\s+u16\s+sTilesetAnims_' + re.escape(cid)
        + r'_Frame\d+\[\]\s*=\s*INCBIN_U16\([^)]*\);\s*\n',
        re.MULTILINE)
    first_incbin = incbin_re.search(source)
    insert_at = first_incbin.start() if first_incbin else None
    source = incbin_re.sub('', source)

    # Build clean INCBIN block + array.
    incbins = "\n".join(
        f'static const u16 sTilesetAnims_{cid}_Frame{n}[] = '
        f'INCBIN_U16("{rel_base}/{n}.4bpp");' for n in nums)
    entries = ",\n    ".join(f"sTilesetAnims_{cid}_Frame{n}" for n in nums)
    new_array = (f"static const u16 *const sTilesetAnims_{cid}[] = {{\n"
                 f"    {entries},\n}};")

    # Replace the existing array in place.
    arr_re = re.compile(
        r'static\s+const\s+u16\s+\*\s*const\s+sTilesetAnims_' + re.escape(cid)
        + r'\[\]\s*=\s*\{.*?\}\s*;', re.DOTALL)
    arr_m = arr_re.search(source)
    if not arr_m:
        return False
    source = source[:arr_m.start()] + new_array + source[arr_m.end():]

    # Re-insert the clean INCBIN block just before the array.
    arr_m = arr_re.search(source)  # re-find after array replacement
    ins = arr_m.start() if arr_m else (insert_at or 0)
    source = source[:ins] + incbins + "\n" + source[ins:]

    with open(src, "w", encoding="utf-8", newline="\n") as f:
        f.write(source)
    return True


def remove_animation_from_tileset(project_dir: str,
                                  anim: TileAnimation) -> bool:
    """Remove an entire animation from tileset_anims.c.

    Removes INCBIN declarations, the frame array, the QueueAnimTiles
    function, and the dispatch-call line.  If the dispatch function becomes
    empty, removes it and the Init function too, and sets ``.callback = NULL``
    in headers.h.

    Does **not** delete PNG files on disk.  Returns True on success.
    """
    src_path = os.path.join(project_dir, "src", "tileset_anims.c")
    if not os.path.isfile(src_path):
        return False

    with open(src_path, "r", encoding="utf-8", errors="replace") as f:
        source = f.read()

    # 1. Remove INCBIN_U16 lines for each frame
    incbin_pattern = re.compile(
        r'^(?://\s*palette:.*\n)?'
        r'static\s+const\s+u16\s+sTilesetAnims_'
        + re.escape(anim.anim_id) + r'_Frame\d+\[\].*?;\s*\n',
        re.MULTILINE,
    )
    source = incbin_pattern.sub('', source)

    # Also clean up any orphaned palette comment that preceded the first frame
    # (already handled by the optional prefix in the pattern above, but clean
    # up straggling blank comment lines)
    source = re.sub(
        r'^//\s*palette:\s*' + re.escape(anim.tileset_name) + r'.*\n'
        r'(?=\s*\n)',
        '', source, flags=re.MULTILINE,
    )

    # 2. Remove the frame array definition
    arr_pattern = re.compile(
        r'\n?static\s+const\s+u16\s+\*\s*const\s+sTilesetAnims_'
        + re.escape(anim.anim_id) + r'\[\]\s*=\s*\{[^}]*\};\s*\n',
        re.DOTALL,
    )
    source = arr_pattern.sub('\n', source)

    # 3. Remove the QueueAnimTiles function
    queue_pattern = re.compile(
        r'\n?static\s+void\s+QueueAnimTiles_'
        + re.escape(anim.anim_id) + r'\s*\(u16\s+timer\)\s*\{[^}]*\}\s*\n',
        re.DOTALL,
    )
    source = queue_pattern.sub('\n', source)

    # 4. Remove the dispatch if-line
    call_pattern = re.compile(
        r'\s*if\s*\(\s*timer\s*%\s*\d+\s*==\s*\d+\s*\)\s*\n'
        r'\s*QueueAnimTiles_' + re.escape(anim.anim_id) + r'\s*\([^)]*\)\s*;',
        re.MULTILINE,
    )
    source = call_pattern.sub('', source)

    # 5. Check if the dispatch function is now empty
    if anim.dispatch_func:
        dispatch_re = re.compile(
            r'(static\s+void\s+' + re.escape(anim.dispatch_func)
            + r'\s*\(u16\s+timer\)\s*\{)([^}]*(?:\{[^}]*\})*[^}]*)\}',
            re.MULTILINE | re.DOTALL,
        )
        dm = dispatch_re.search(source)
        if dm:
            body = dm.group(2).strip()
            # Check if there are any remaining QueueAnimTiles calls
            if not re.search(r'QueueAnimTiles_', body):
                # Dispatch is empty — remove it
                # Remove dispatch function
                full_dispatch = re.compile(
                    r'\n?static\s+void\s+' + re.escape(anim.dispatch_func)
                    + r'\s*\(u16\s+timer\)\s*\{[^}]*(?:\{[^}]*\})*[^}]*\}\s*\n',
                    re.DOTALL,
                )
                source = full_dispatch.sub('\n', source)

                # Remove Init function
                if anim.init_func:
                    init_pattern = re.compile(
                        r'\n?void\s+' + re.escape(anim.init_func)
                        + r'\s*\(void\)\s*\{[^}]*\}\s*\n',
                        re.DOTALL,
                    )
                    source = init_pattern.sub('\n', source)

                    # Drop its now-orphaned prototype from tileset_anims.h.
                    remove_init_declaration(project_dir, anim.init_func)

                    # Set .callback = NULL in headers.h
                    hdr_path = os.path.join(
                        project_dir, "src", "data", "tilesets", "headers.h")
                    if os.path.isfile(hdr_path):
                        with open(hdr_path, "r", encoding="utf-8",
                                  errors="replace") as f:
                            hdr = f.read()
                        hdr = re.sub(
                            r'(\.callback\s*=\s*)' + re.escape(anim.init_func),
                            r'\g<1>NULL',
                            hdr,
                        )
                        with open(hdr_path, "w", encoding="utf-8",
                                  newline="\n") as f:
                            f.write(hdr)

    # Clean up excessive blank lines (3+ -> 2)
    source = re.sub(r'\n{3,}', '\n\n', source)

    with open(src_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(source)
    return True


def _sanitize_anim_name(name: str) -> str:
    """Normalise a user-entered animation name to a valid snake_case C-ident
    fragment (lowercase, [a-z0-9_], never leading-digit)."""
    s = re.sub(r'[^A-Za-z0-9]+', '_', (name or '').strip()).strip('_').lower()
    if s and s[0].isdigit():
        s = 'a_' + s
    return s


def rename_animation(project_dir: str, anim: TileAnimation,
                     new_name: str) -> Optional[TileAnimation]:
    """Rename an animation EVERYWHERE and return the mutated *anim* (or None on
    failure / no-op / collision).

    Renames, consistently: the C symbols in ``tileset_anims.c``
    (``sTilesetAnims_<id>`` — the frame vars + the array — and
    ``QueueAnimTiles_<id>``), the INCBIN frame paths, and the on-disk frame
    directory. Only the ANIM part of the id changes; the tileset prefix stays.
    """
    import shutil
    new_snake = _sanitize_anim_name(new_name)
    if not new_snake or new_snake == anim.name:
        return None

    tileset_camel = _snake_to_camel(anim.tileset_name)
    new_camel = _snake_to_camel(new_snake)
    old_c_id = anim.anim_id                       # e.g. "General_Flower"
    new_c_id = f"{tileset_camel}_{new_camel}"     # e.g. "General_Waterfall"
    if new_c_id == old_c_id:
        return None

    src_path = os.path.join(project_dir, "src", "tileset_anims.c")
    if not os.path.isfile(src_path):
        return None
    with open(src_path, encoding="utf-8", errors="replace") as f:
        source = f.read()

    # Refuse a collision with an existing animation of the new id.
    if re.search(r'sTilesetAnims_' + re.escape(new_c_id) + r'(?![A-Za-z0-9])',
                 source):
        return None
    new_dir = os.path.join(project_dir, "data", "tilesets", anim.tileset_type,
                           anim.tileset_name, "anim", new_snake)
    if os.path.exists(new_dir):
        return None

    # 1. Rename the C symbols. The negative lookahead keeps a longer sibling id
    #    (Flower vs FlowerBig) from being partially matched, while still catching
    #    the ``_Frame0`` suffixes and the ``[`` array uses.
    for prefix in ("sTilesetAnims_", "QueueAnimTiles_"):
        source = re.sub(
            re.escape(prefix + old_c_id) + r'(?![A-Za-z0-9])',
            prefix + new_c_id, source)

    # 2. Rename the INCBIN frame paths (they use the snake anim name), scoped to
    #    THIS tileset so a same-named anim in another tileset isn't touched.
    old_rel = f"{anim.tileset_type}/{anim.tileset_name}/anim/{anim.name}/"
    new_rel = f"{anim.tileset_type}/{anim.tileset_name}/anim/{new_snake}/"
    source = source.replace(old_rel, new_rel)

    with open(src_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(source)

    # 3. Move the frame directory on disk.
    old_dir = os.path.join(project_dir, "data", "tilesets", anim.tileset_type,
                           anim.tileset_name, "anim", anim.name)
    if os.path.isdir(old_dir):
        try:
            shutil.move(old_dir, new_dir)
        except OSError:
            pass

    # 4. Update the in-memory anim so the UI reflects it without a full reload.
    anim.name = new_snake
    anim.anim_id = new_c_id
    for fr in anim.frames:
        fr.png_path = os.path.join(new_dir, os.path.basename(fr.png_path))
        fr.var_name = re.sub(
            r'sTilesetAnims_' + re.escape(old_c_id) + r'(?![A-Za-z0-9])',
            f'sTilesetAnims_{new_c_id}', fr.var_name)
    return anim


# ═══════════════════════════════════════════════════════════════════════════════
#  Palette Loader
# ═══════════════════════════════════════════════════════════════════════════════


def find_primary_for_secondary(project_dir: str, secondary_name: str) -> str:
    """Return the snake-case dir name of the primary tileset a secondary is
    paired with in layouts.json (e.g. ``'general'``). Secondary tilesets don't
    define palette slots 0..6 — those come from whatever primary is loaded with
    them at runtime — so we need the pairing to show the right colours.
    Defaults to ``'general'`` (the standard outdoor primary) if not found."""
    import json
    sec_sym = f"gTileset_{_snake_to_camel(secondary_name)}"
    layouts = os.path.join(project_dir, "data", "layouts", "layouts.json")
    if os.path.isfile(layouts):
        try:
            with open(layouts, encoding="utf-8") as f:
                data = json.load(f)
            for lay in data.get("layouts", []):
                if lay and lay.get("secondary_tileset") == sec_sym:
                    prim = lay.get("primary_tileset", "")
                    if prim.startswith("gTileset_"):
                        return _camel_to_snake(prim[len("gTileset_"):])
        except Exception:
            pass
    return "general"


def load_tileset_palettes(
    project_dir: str, tileset_name: str, tileset_type: str,
) -> List[List[Tuple[int, int, int]]]:
    """Load all 16 palette ``.pal`` files for a tileset.

    Returns a list of 16 palettes, each containing 16 ``(r, g, b)`` tuples
    clamped to GBA 15-bit colour depth.

    For a SECONDARY tileset, palette slots 0..6 (``NUM_PALS_IN_PRIMARY``) are
    supplied by the paired PRIMARY tileset at runtime — not by the secondary's
    own placeholder files — so those slots are loaded from the primary (General
    by default). This matches what the game actually renders.
    """
    from ui.palette_utils import read_jasc_pal

    NUM_PALS_IN_PRIMARY = 7

    def _pal_dir(name: str, ttype: str) -> str:
        return os.path.join(project_dir, "data", "tilesets", ttype, name,
                            "palettes")

    own_dir = _pal_dir(tileset_name, tileset_type)
    primary_dir = None
    if tileset_type == "secondary":
        primary_name = find_primary_for_secondary(project_dir, tileset_name)
        primary_dir = _pal_dir(primary_name, "primary")

    black16: List[Tuple[int, int, int]] = [(0, 0, 0)] * 16
    palettes: List[List[Tuple[int, int, int]]] = []

    for slot in range(16):
        if primary_dir is not None and slot < NUM_PALS_IN_PRIMARY:
            pal_path = os.path.join(primary_dir, f"{slot:02d}.pal")
        else:
            pal_path = os.path.join(own_dir, f"{slot:02d}.pal")
        if not os.path.isfile(pal_path):
            palettes.append(list(black16))
            continue
        try:
            colours = read_jasc_pal(pal_path)
            # GBA 15-bit clamp: each channel rounded down to nearest 8
            clamped = [
                ((r >> 3) << 3, (g >> 3) << 3, (b >> 3) << 3)
                for r, g, b in colours
            ]
            # Pad or truncate to 16 entries
            while len(clamped) < 16:
                clamped.append((0, 0, 0))
            palettes.append(clamped[:16])
        except Exception:
            palettes.append(list(black16))

    return palettes
