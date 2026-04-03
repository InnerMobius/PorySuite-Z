"""
Region Map Manager — visual region map editor backend.

Ported from TriforceGUI/RegionMapManager.py. All paths relative to root_dir.
"""

import os
import json
import re
import shutil
import struct
import subprocess
from typing import List, Tuple, Optional, Dict

from eventide.backend.file_utils import replace_in_file, replace_repo_wide

try:
    from PIL import Image
    _PIL_AVAILABLE = True
except ImportError:
    Image = None
    _PIL_AVAILABLE = False


class RegionMapManager:
    TILE_SIZE = 8

    def __init__(self, root_dir: str):
        self.root_dir = root_dir
        self.sections_path = os.path.join(
            root_dir, 'src', 'data', 'region_map', 'region_map_sections.json')
        self.template_dir = os.path.join(root_dir, 'src', 'data', 'region_map')
        self.constants_dir = os.path.join(root_dir, 'include', 'constants')
        self.graphics_dir = os.path.join(root_dir, 'graphics', 'region_map')
        self.tileset_path = os.path.join(self.graphics_dir, 'region_map.png')
        self.edge_tiles_path = os.path.join(self.graphics_dir, 'map_edge.png')
        self.edges_map_path = os.path.join(self.graphics_dir, 'map_edge.bin')

        self.sections = self.load_sections()
        # layouts[region] = grid for LAYER_MAP
        self.layouts: Dict[str, List[List[Optional[str]]]] = {}
        # dungeon_layouts[region] = grid for LAYER_DUNGEON
        self.dungeon_layouts: Dict[str, List[List[Optional[str]]]] = {}
        self._load_all_layouts()
        regions = list(self.layouts.keys())
        self.region = regions[0] if regions else 'kanto'

    def _sanitize_name(self, name: str) -> str:
        clean = re.sub(r"[^A-Za-z0-9_]+", "_", name)
        clean = clean.strip("_").lower()
        return clean or "region"

    def _update_source_refs(self, old: str, new: str):
        src = os.path.join(self.root_dir, 'src', 'region_map.c')
        replacements = [(f'region_map_layout_{old}.h',
                         f'region_map_layout_{new}.h')]

        for path in self._tilemap_files(old):
            name = os.path.basename(path)
            new_name = name.replace(old, new)
            replacements.extend([
                (f'graphics/region_map/{name}.lz',
                 f'graphics/region_map/{new_name}.lz'),
                (f'graphics/region_map/{name}',
                 f'graphics/region_map/{new_name}'),
            ])

        if os.path.exists(src):
            replace_in_file(src, replacements)

    def load_sections(self) -> dict:
        if not os.path.exists(self.sections_path):
            raise FileNotFoundError(self.sections_path)
        with open(self.sections_path, encoding="utf-8") as f:
            return json.load(f)

    def _load_layout(self, filename: str) -> Optional[
            Tuple[List[List[Optional[str]]], List[List[Optional[str]]]]]:
        """Load both layers from a layout file.

        Returns (map_grid, dungeon_grid) or None if the file is missing/invalid.
        """
        path = os.path.join(self.template_dir, filename)
        if not os.path.exists(path):
            return None
        with open(path) as f:
            lines = f.readlines()

        def _parse_layer(lines, start_marker, end_marker=None):
            start = None
            for i, line in enumerate(lines):
                if start_marker in line:
                    start = i + 1
                    break
            if start is None:
                return []
            rows = []
            for line in lines[start:]:
                if end_marker and end_marker in line:
                    break
                # Also stop at closing brace of the outer array
                if line.strip().startswith('};'):
                    break
                m = re.search(r'\{([^}]+)\}', line)
                if m:
                    row = [s.strip() for s in m.group(1).split(',') if s.strip()]
                    rows.append([None if c == 'MAPSEC_NONE' else c for c in row])
            return rows

        map_rows = _parse_layer(lines, '[LAYER_MAP]', '[LAYER_DUNGEON]')
        dungeon_rows = _parse_layer(lines, '[LAYER_DUNGEON]')
        if not map_rows:
            return None
        return map_rows, dungeon_rows

    def _layout_file_map(self) -> Dict[str, str]:
        mapping: Dict[str, str] = {}
        if os.path.isdir(self.template_dir):
            for name in os.listdir(self.template_dir):
                m = re.match(r'region_map_layout_(.+)\.h$', name)
                if m:
                    mapping[m.group(1)] = name
        return mapping

    def _load_all_layouts(self):
        layout_files = self._layout_file_map()
        for key, fname in layout_files.items():
            result = self._load_layout(fname)
            if result:
                map_grid, dungeon_grid = result
                self.layouts[key] = map_grid
                self.dungeon_layouts[key] = dungeon_grid

    def save_sections(self):
        with open(self.sections_path, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(self.sections, f, indent=2, ensure_ascii=False)
            f.write('\n')
        self._generate_headers()

    def _ensure_jsonproc(self) -> str:
        exe = os.path.join(self.root_dir, 'tools', 'jsonproc',
                           'jsonproc' + ('.exe' if os.name == 'nt' else ''))
        if os.path.exists(exe):
            return exe
        makefile_dir = os.path.join(self.root_dir, 'tools', 'jsonproc')
        subprocess.run(['make', '-C', makefile_dir], check=True)
        if not os.path.exists(exe):
            raise RuntimeError('jsonproc executable not found after build')
        return exe

    def _generate_headers(self):
        mappings = [
            ('region_map_sections.entries.json.txt',
             os.path.join('src', 'data', 'region_map', 'region_map_entries.h')),
            ('region_map_sections.strings.json.txt',
             os.path.join('src', 'data', 'region_map', 'region_map_entry_strings.h')),
            ('region_map_sections.constants.json.txt',
             os.path.join('include', 'constants', 'region_map_sections.h')),
        ]
        jsonproc = self._ensure_jsonproc()
        for template, out_rel in mappings:
            tpl = os.path.join(self.template_dir, template)
            out = os.path.join(self.root_dir, out_rel)
            subprocess.run([jsonproc, self.sections_path, tpl, out], check=True)

    def set_region(self, region: str):
        if region in self.layouts:
            self.region = region

    def _is_visible_entry(self, entry: Dict) -> bool:
        required = ('x', 'y', 'width', 'height')
        if not all(k in entry for k in required):
            return False
        if (entry['x'] == 0 and entry['y'] == 0 and
                entry['width'] <= 1 and entry['height'] <= 1):
            return False
        return True

    def get_section_ids(self, region: Optional[str] = None) -> List[str]:
        region = region or self.region
        grid, _, _ = self.load_grid(region)
        ids = sorted({cell for row in grid for cell in row if cell})
        return ids

    def rename_section(self, old_id: str, new_id: str):
        if old_id == new_id:
            return
        changed = False
        for entry in self.sections.get('map_sections', []):
            if entry['id'] == old_id:
                entry['id'] = new_id
                changed = True
        if changed:
            self.save_sections()
            replace_repo_wide(self.root_dir, [(old_id, new_id)])

    def list_regions(self) -> List[str]:
        return sorted(self.layouts.keys())

    def clone_region(self, source: str, new_name: str) -> str:
        files = self._layout_file_map()
        src_file = files.get(source)
        if not src_file:
            raise ValueError(f'Unknown region {source}')
        clean = self._sanitize_name(new_name)
        dest_file = f'region_map_layout_{clean}.h'
        src_path = os.path.join(self.template_dir, src_file)
        dest_path = os.path.join(self.template_dir, dest_file)
        if os.path.exists(dest_path):
            raise FileExistsError(dest_path)
        shutil.copy(src_path, dest_path)

        for path in self._tilemap_files(source):
            new_path = path.replace(source, clean)
            if os.path.exists(path):
                shutil.copy(path, new_path)

        self.layouts[clean] = [row[:] for row in self.layouts[source]]
        if source in self.dungeon_layouts:
            self.dungeon_layouts[clean] = [row[:] for row in self.dungeon_layouts[source]]
        self._update_source_refs(source, clean)
        self.save_sections()
        return clean

    def rename_region(self, old: str, new: str) -> str:
        clean = self._sanitize_name(new)
        if old == clean:
            return clean
        files = self._layout_file_map()
        src_file = files.get(old)
        if not src_file:
            raise ValueError(f'Unknown region {old}')
        dest_file = f'region_map_layout_{clean}.h'
        src_path = os.path.join(self.template_dir, src_file)
        dest_path = os.path.join(self.template_dir, dest_file)
        if os.path.exists(dest_path):
            raise FileExistsError(dest_path)
        os.rename(src_path, dest_path)
        for path in self._tilemap_files(old):
            new_path = path.replace(old, clean)
            if os.path.exists(path):
                os.rename(path, new_path)
        self.layouts[clean] = self.layouts.pop(old)
        if old in self.dungeon_layouts:
            self.dungeon_layouts[clean] = self.dungeon_layouts.pop(old)
        self._update_source_refs(old, clean)
        self.save_sections()
        return clean

    def delete_region(self, region: str):
        region = self._sanitize_name(region)
        files = self._layout_file_map()
        layout_file = files.get(region)
        if layout_file:
            path = os.path.join(self.template_dir, layout_file)
            if os.path.exists(path):
                os.remove(path)
        for path in self._tilemap_files(region):
            if os.path.exists(path):
                os.remove(path)
        self.layouts.pop(region, None)
        self.dungeon_layouts.pop(region, None)
        self.save_sections()

    def load_grid(self, region: Optional[str] = None) -> Tuple[List[List[Optional[str]]], int, int]:
        region = region or self.region
        grid = self.layouts.get(region)
        if grid:
            height = len(grid)
            width = len(grid[0]) if height else 0
            return [row[:] for row in grid], width, height

        width = 0
        height = 0
        for entry in self.sections.get('map_sections', []):
            if self._is_visible_entry(entry):
                width = max(width, entry['x'] + entry['width'])
                height = max(height, entry['y'] + entry['height'])
        width = max(width, 1)
        height = max(height, 1)
        grid = [[None for _ in range(width)] for _ in range(height)]
        for entry in self.sections.get('map_sections', []):
            if self._is_visible_entry(entry):
                sid = entry['id']
                for x in range(entry['width']):
                    for y in range(entry['height']):
                        xi = entry['x'] + x
                        yi = entry['y'] + y
                        if 0 <= yi < height and 0 <= xi < width:
                            grid[yi][xi] = sid
        return grid, width, height

    def load_dungeon_grid(self, region: Optional[str] = None
                         ) -> Tuple[List[List[Optional[str]]], int, int]:
        """Load the dungeon layer grid for the given region."""
        region = region or self.region
        grid = self.dungeon_layouts.get(region)
        if grid and grid[0]:
            height = len(grid)
            width = len(grid[0])
            return [row[:] for row in grid], width, height
        # Fall back to an empty grid with same dimensions as the map layer
        map_grid = self.layouts.get(region)
        if map_grid:
            height = len(map_grid)
            width = len(map_grid[0]) if height else 0
        else:
            width, height = 22, 15  # default region map size
        return [[None for _ in range(width)] for _ in range(height)], width, height

    def save_grid(self, grid: List[List[Optional[str]]], layer: str = 'map'):
        if layer == 'dungeon':
            # Dungeon layer: update in-memory grid and write to file.
            # Don't recalculate section coordinates — those come from the map layer.
            self.dungeon_layouts[self.region] = [row[:] for row in grid]
            self._save_layout_both()
            return

        # Map layer: recalculate section coordinates from the grid
        by_id: Dict[str, Dict] = {s['id']: s for s in self.sections.get('map_sections', [])}
        ids_in_grid = {cell for row in grid for cell in row if cell}

        prev_grid = self.layouts.get(self.region, [])
        region_ids = {cell for row in prev_grid for cell in row if cell}
        for sid in region_ids:
            entry = by_id.get(sid)
            if entry:
                entry.pop('x', None)
                entry.pop('y', None)
                entry.pop('width', None)
                entry.pop('height', None)

        for sid in ids_in_grid:
            coords = [(x, y) for y, row in enumerate(grid) for x, cell in enumerate(row) if cell == sid]
            if not coords:
                continue
            xs = [c[0] for c in coords]
            ys = [c[1] for c in coords]
            entry = by_id.get(sid)
            if entry is None:
                entry = {'id': sid}
                self.sections['map_sections'].append(entry)
            entry['x'] = min(xs)
            entry['y'] = min(ys)
            entry['width'] = max(xs) - min(xs) + 1
            entry['height'] = max(ys) - min(ys) + 1

        self.layouts[self.region] = [row[:] for row in grid]
        self._save_layout_both()
        self.save_sections()

    def _grid_to_rows(self, grid: List[List[Optional[str]]]) -> List[str]:
        """Convert an in-memory grid to C source rows."""
        rows = []
        for row in grid:
            cells = [c if c else 'MAPSEC_NONE' for c in row]
            rows.append(f"        {{{', '.join(cells)}}},\n")
        return rows

    def _save_layout_both(self):
        """Write both MAP and DUNGEON layers to the layout .h file."""
        file_map = self._layout_file_map()
        fname = file_map.get(self.region)
        if not fname:
            return
        path = os.path.join(self.template_dir, fname)
        if not os.path.exists(path):
            return
        with open(path, 'r', encoding='utf-8') as fh:
            lines = fh.readlines()

        # Find the MAP layer region
        map_start = map_end = None
        dng_start = dng_end = None
        for i, line in enumerate(lines):
            if map_start is None and '[LAYER_MAP]' in line:
                map_start = i + 2  # skip marker + opening brace/comment
            elif map_start is not None and map_end is None and '[LAYER_DUNGEON]' in line:
                map_end = i - 1    # before the closing brace line
                dng_start = i + 2  # skip marker + opening brace/comment
            elif dng_start is not None and dng_end is None:
                # End of dungeon layer: closing brace of the array
                if line.strip().startswith('},') or line.strip().startswith('};'):
                    dng_end = i
                    break

        if map_start is None or map_end is None:
            return

        # Write MAP layer
        map_grid = self.layouts.get(self.region, [])
        map_rows = self._grid_to_rows(map_grid)
        lines[map_start:map_end] = map_rows

        # Recalculate dungeon positions after MAP layer replacement
        if dng_start is not None and dng_end is not None:
            offset = len(map_rows) - (map_end - map_start)
            dng_start += offset
            dng_end += offset

            dungeon_grid = self.dungeon_layouts.get(self.region, [])
            if dungeon_grid:
                dng_rows = self._grid_to_rows(dungeon_grid)
                lines[dng_start:dng_end] = dng_rows

        with open(path, 'w', encoding='utf-8') as fh:
            fh.writelines(lines)

    # Keep old name as alias for backwards compatibility
    _save_layout = _save_layout_both

    def _tilemap_path(self, region: Optional[str] = None) -> str:
        region = region or self.region
        return os.path.join(self.graphics_dir, f'{region}.bin')

    def _tilemap_files(self, region: str) -> List[str]:
        if not os.path.isdir(self.graphics_dir):
            return []
        files = []
        for name in os.listdir(self.graphics_dir):
            if name.endswith('.bin') and region in name:
                files.append(os.path.join(self.graphics_dir, name))
        return files

    def _detect_playable_area(self) -> Tuple[int, int, int, int]:
        if hasattr(self, '_playable_area'):
            return self._playable_area

        width = 30
        height = 20
        edges = self._load_tilemap(self.edges_map_path, width, height)

        xs: List[int] = []
        ys: List[int] = []
        for y, row in enumerate(edges):
            for x, val in enumerate(row):
                if val == 0x2007:
                    xs.append(x)
                    ys.append(y)

        if xs and ys:
            self._playable_area = (min(xs), min(ys),
                                   max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)
        else:
            self._playable_area = (5, 2, 20, 15)

        return self._playable_area

    def _grid_dimensions(self) -> Tuple[int, int]:
        width = 0
        height = 0
        for entry in self.sections.get('map_sections', []):
            if self._is_visible_entry(entry):
                width = max(width, entry['x'] + entry['width'])
                height = max(height, entry['y'] + entry['height'])
        return max(width, 1), max(height, 1)

    def get_tile_offset(self) -> Tuple[float, float]:
        if hasattr(self, '_tile_offset'):
            return self._tile_offset

        parsed = self._parse_offset_from_source()
        if parsed:
            self._tile_offset = parsed
            return parsed

        area_x, area_y, area_w, area_h = self._detect_playable_area()
        grid_w, grid_h = self._grid_dimensions()

        off_x = area_x
        if area_w > grid_w:
            off_x += (area_w - grid_w) // 2

        off_y = area_y
        if area_h > grid_h:
            off_y += (area_h - grid_h) // 2

        half_tile = (self.TILE_SIZE / 2) / self.TILE_SIZE
        self._tile_offset = (off_x - half_tile, off_y - half_tile)
        return self._tile_offset

    def _parse_offset_from_source(self) -> Optional[Tuple[float, float]]:
        path = os.path.join(self.root_dir, 'src', 'region_map.c')
        if not os.path.exists(path):
            return None
        with open(path) as f:
            text = f.read()
        mx = re.search(r'8\s*\*\s*sMapCursor->x\s*\+\s*(\d+)', text)
        my = re.search(r'8\s*\*\s*sMapCursor->y\s*\+\s*(\d+)', text)
        if mx and my:
            half_tile = (self.TILE_SIZE / 2) / self.TILE_SIZE
            return (int(mx.group(1)) / self.TILE_SIZE - half_tile,
                    int(my.group(1)) / self.TILE_SIZE - half_tile)
        return None

    def _load_tile_image(self, path: str, tiles_x: int, tiles_y: int):
        if _PIL_AVAILABLE:
            img = Image.open(path).convert('RGBA')
            tiles = []
            for ty in range(tiles_y):
                for tx in range(tiles_x):
                    box = (tx * self.TILE_SIZE, ty * self.TILE_SIZE,
                           (tx + 1) * self.TILE_SIZE, (ty + 1) * self.TILE_SIZE)
                    tiles.append(img.crop(box))
            return tiles
        else:
            from PyQt6.QtGui import QImage
            tiles = []
            img = QImage(path).convertToFormat(QImage.Format.Format_RGBA8888)
            for ty in range(tiles_y):
                for tx in range(tiles_x):
                    rect = (tx * self.TILE_SIZE, ty * self.TILE_SIZE,
                            self.TILE_SIZE, self.TILE_SIZE)
                    tiles.append(img.copy(*rect))
            return tiles

    def _load_tilemap(self, path: str, width: int, height: int) -> List[List[int]]:
        with open(path, 'rb') as f:
            data = f.read()
        expected_size = width * height * 2
        if len(data) != expected_size:
            raise ValueError(
                f"Tilemap {path} is {len(data)} bytes; expected {expected_size}")
        entries = struct.unpack('<' + 'H' * (width * height), data)
        grid = [list(entries[i * width:(i + 1) * width]) for i in range(height)]
        return grid

    def build_region_map_image(self, region: Optional[str] = None):
        region = region or self.region
        tilemap_path = self._tilemap_path(region)
        if not (os.path.exists(self.tileset_path) and os.path.exists(tilemap_path)):
            raise FileNotFoundError('Region map graphics missing')

        tiles_base = self._load_tile_image(self.tileset_path, 16, 20)
        tiles_edge = self._load_tile_image(self.edge_tiles_path, 16, 4)
        map_base = self._load_tilemap(tilemap_path, 30, 20)
        map_edges = self._load_tilemap(self.edges_map_path, 30, 20)

        if _PIL_AVAILABLE:
            out = Image.new('RGBA', (30 * self.TILE_SIZE, 20 * self.TILE_SIZE))

            def paste_tile(tile_list, idx, x, y, attr):
                if idx >= len(tile_list):
                    return
                tile = tile_list[idx]
                if attr & 0x400:
                    tile = tile.transpose(Image.FLIP_LEFT_RIGHT)
                if attr & 0x800:
                    tile = tile.transpose(Image.FLIP_TOP_BOTTOM)
                out.paste(tile, (x * self.TILE_SIZE, y * self.TILE_SIZE))

            for y, row in enumerate(map_base):
                for x, val in enumerate(row):
                    idx = val & 0x3FF
                    paste_tile(tiles_base, idx, x, y, val)

            for y, row in enumerate(map_edges):
                for x, val in enumerate(row):
                    idx = val & 0x3FF
                    if idx and idx != 7:
                        paste_tile(tiles_edge, idx, x, y, val)

            return out
        else:
            from PyQt6.QtGui import QImage, QPainter

            out = QImage(30 * self.TILE_SIZE, 20 * self.TILE_SIZE,
                         QImage.Format.Format_RGBA8888)
            out.fill(0)
            painter = QPainter(out)

            def paste_tile(tile_list, idx, x, y, attr):
                if idx >= len(tile_list):
                    return
                tile = tile_list[idx]
                if attr & 0x400:
                    tile = tile.mirrored(True, False)
                if attr & 0x800:
                    tile = tile.mirrored(False, True)
                painter.drawImage(x * self.TILE_SIZE, y * self.TILE_SIZE, tile)

            for y, row in enumerate(map_base):
                for x, val in enumerate(row):
                    idx = val & 0x3FF
                    paste_tile(tiles_base, idx, x, y, val)

            for y, row in enumerate(map_edges):
                for x, val in enumerate(row):
                    idx = val & 0x3FF
                    if idx and idx != 7:
                        paste_tile(tiles_edge, idx, x, y, val)

            painter.end()
            return out
