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
from dataclasses import dataclass, field
from typing import List, Tuple, Optional, Dict

from eventide.backend.file_utils import replace_in_file, replace_repo_wide
from eventide.backend import region_codegen as _codegen

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

        self.region_map_c_path = os.path.join(root_dir, 'src', 'region_map.c')

        self.sections = self.load_sections()
        # layouts[region] = grid for LAYER_MAP
        self.layouts: Dict[str, List[List[Optional[str]]]] = {}
        # dungeon_layouts[region] = grid for LAYER_DUNGEON
        self.dungeon_layouts: Dict[str, List[List[Optional[str]]]] = {}
        self._load_all_layouts()

        # Engine state: parsed once from src/region_map.c, mirrors the on-disk
        # truth. Updated in-place during region ops; flushed via codegen on save.
        self._engine_state: _codegen.EngineState = self._parse_engine_state()

        # Staging — see Phase 5 plan. New region ops add to these lists; old
        # methods (clone_region/rename_region/delete_region) are immediate-write
        # for now and run codegen inline. Phase 5 will switch the UI to staging.
        self._pending_creates: List[_codegen.RegionRecord] = []
        self._pending_clones: List[Tuple[str, str]] = []  # (source, new)
        self._pending_renames: List[Tuple[str, str]] = []  # (old, new)
        self._pending_deletes: List[str] = []

        regions = [r.name for r in self._engine_state.regions]
        if not regions:
            regions = list(self.layouts.keys())
        self.region = regions[0] if regions else 'kanto'

    # Reserved C keywords that would collide if a region name is uppercased
    # for the enum constant. Refused at validation time.
    _RESERVED_C_KEYWORDS = frozenset({
        "auto", "break", "case", "char", "const", "continue", "default",
        "do", "double", "else", "enum", "extern", "float", "for", "goto",
        "if", "inline", "int", "long", "register", "restrict", "return",
        "short", "signed", "sizeof", "static", "struct", "switch", "typedef",
        "union", "unsigned", "void", "volatile", "while", "count",
    })

    def _sanitize_name(self, name: str) -> str:
        clean = re.sub(r"[^A-Za-z0-9_]+", "_", name)
        clean = clean.strip("_").lower()
        return clean or "region"

    def validate_region_name(self, name: str) -> Tuple[bool, str]:
        """Strict validation for new/renamed region names.

        Returns (ok, error_message). Error is "" on success.

        Rules:
          - Non-empty after sanitization.
          - Lowercase letters, digits, underscores only.
          - Must start with a letter (filenames + C identifier safety).
          - 1..32 chars.
          - Not a reserved C keyword (would collide with enum const).
          - No collision with existing or pending region names.
        """
        if not name:
            return False, "name is empty"
        clean = self._sanitize_name(name)
        if not clean:
            return False, "name is empty after sanitization"
        if not re.match(r"^[a-z][a-z0-9_]*$", clean):
            return False, ("name must start with a lowercase letter and "
                           "contain only lowercase letters, digits, and "
                           "underscores")
        if len(clean) > 32:
            return False, f"name too long ({len(clean)} > 32 chars)"
        if clean in self._RESERVED_C_KEYWORDS:
            return False, f"'{clean}' is a reserved C keyword"
        existing = {r.name for r in self._engine_state.regions}
        pending = (
            {r.name for r in self._pending_creates}
            | {new for _old, new in self._pending_renames}
            | {new for _src, new in self._pending_clones}
        )
        if clean in existing or clean in pending:
            return False, f"region '{clean}' already exists or is pending"
        return True, ""

    # ------------------------------------------------------------------
    # Engine-state plumbing (Phase 2 — codegen-driven src/region_map.c)
    # ------------------------------------------------------------------

    @staticmethod
    def _camel(name: str) -> str:
        return "".join(p.capitalize() for p in name.split("_"))

    def _parse_engine_state(self) -> _codegen.EngineState:
        """Read src/region_map.c and reconstruct the engine state.

        Cross-references the enum constants with the actual layout folder
        names on disk so we recover the correct underscore-bearing folder
        ids ('sevii_123', not 'sevii123').
        """
        if not os.path.exists(self.region_map_c_path):
            return _codegen.EngineState()
        with open(self.region_map_c_path, "r", encoding="utf-8") as f:
            content = f.read()
        folder_names = sorted(self._layout_file_map().keys())
        state = _codegen.parse_existing_state(content, folder_names=folder_names)
        if state is None:
            return _codegen.EngineState()
        for r in state.regions:
            r.mapsecs = self._compute_mapsecs_for_region(r.name)
        return state

    def _compute_mapsecs_for_region(self, region_name: str) -> List[str]:
        """Distinct MAPSECs in this region's LAYER_MAP and LAYER_DUNGEON grids,
        in stable order (top-to-bottom, left-to-right first appearance),
        MAPSEC_NONE excluded.

        Drives sRegionMapsecLookup. The engine uses this to identify which
        region the player is in based on their current map's MAPSEC, including
        dungeon interiors (which appear on LAYER_DUNGEON, not LAYER_MAP).
        Slot 0 (base region) is excluded by the codegen.
        """
        seen: List[str] = []
        seen_set = set()

        def _walk(grid):
            if not grid:
                return
            for row in grid:
                for cell in row:
                    if cell and cell != "MAPSEC_NONE" and cell not in seen_set:
                        seen.append(cell)
                        seen_set.add(cell)

        _walk(self.layouts.get(region_name))
        _walk(self.dungeon_layouts.get(region_name))
        return seen

    def _refresh_engine_mapsecs(self) -> None:
        """Recompute every region's mapsec list from the current grids."""
        for r in self._engine_state.regions:
            r.mapsecs = self._compute_mapsecs_for_region(r.name)

    def _rewrite_layout_internal_symbols(self, old_name: str, new_name: str) -> None:
        """Inside the (already renamed) layout .h file, rename the section
        grid symbol so the codegen's get_section_dispatch references resolve.

        Vanilla layout .h contains only:
            static const u8 sRegionMapSections_<Camel>[LAYER_COUNT][...]...

        The per-region tilemap INCBIN_U32 (sKanto_Tilemap etc.) lives in
        region_map.c, NOT in the .h — that's owned by the tilemap_incbins
        marker block in codegen, so we don't touch it here.
        """
        if old_name == new_name:
            return
        layout_path = os.path.join(
            self.template_dir, f"region_map_layout_{new_name}.h"
        )
        if not os.path.exists(layout_path):
            return
        old_camel = self._camel(old_name)
        new_camel = self._camel(new_name)
        with open(layout_path, "r", encoding="utf-8") as f:
            text = f.read()
        text = re.sub(
            rf"\bsRegionMapSections_{old_camel}\b",
            f"sRegionMapSections_{new_camel}",
            text,
        )
        with open(layout_path, "w", encoding="utf-8") as f:
            f.write(text)

    def find_external_region_references(self, region_name: str,
                                        max_hits: int = 20
                                        ) -> List[Tuple[str, int, str]]:
        """Grep src/, include/, data/ for `REGIONMAP_<UPPER>` references
        OUTSIDE the marker blocks in src/region_map.c. The codegen owns
        every reference inside region_map.c's markers, but the user might
        have custom code anywhere else (scripts, other .c files, custom
        flags, etc.) that hardcodes the constant by name. Deleting or
        renaming the region without updating those breaks the build.

        Returns up to `max_hits` (path_relative_to_root, line_number, line)
        tuples. Empty list = safe to delete/rename.
        """
        if not region_name:
            return []
        # Build the C constant we're looking for. Strip underscores to
        # match vanilla's enum-naming convention (sevii_123 → SEVII123).
        const = f"REGIONMAP_{region_name.replace('_', '').upper()}"
        target_dirs = [
            os.path.join(self.root_dir, "src"),
            os.path.join(self.root_dir, "include"),
            os.path.join(self.root_dir, "data"),
        ]
        # Code extensions worth scanning. Skip binaries, images, json, etc.
        extensions = {".c", ".h", ".inc", ".s", ".cpp", ".hpp"}
        skip_path = os.path.normcase(os.path.abspath(self.region_map_c_path))
        # Word-boundary regex so REGIONMAP_KANTO doesn't match REGIONMAP_KANTOSOMETHING.
        pat = re.compile(rf"\b{re.escape(const)}\b")
        hits: List[Tuple[str, int, str]] = []
        for base in target_dirs:
            if not os.path.isdir(base):
                continue
            for root, _dirs, files in os.walk(base):
                for fname in files:
                    ext = os.path.splitext(fname)[1].lower()
                    if ext not in extensions:
                        continue
                    full = os.path.join(root, fname)
                    if os.path.normcase(os.path.abspath(full)) == skip_path:
                        continue  # we own region_map.c via codegen
                    try:
                        with open(full, "r", encoding="utf-8",
                                  errors="replace") as f:
                            for lineno, line in enumerate(f, 1):
                                if pat.search(line):
                                    rel = os.path.relpath(full, self.root_dir)
                                    hits.append((rel, lineno, line.strip()))
                                    if len(hits) >= max_hits:
                                        return hits
                    except OSError:
                        continue
        return hits

    def is_first_engine_codegen(self) -> bool:
        """True if the next codegen run will insert markers into a vanilla
        (un-migrated) src/region_map.c. Used by the UI to surface a one-time
        warning before the first engine rewrite.
        """
        if not os.path.exists(self.region_map_c_path):
            return False
        try:
            with open(self.region_map_c_path, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError:
            return False
        return not _codegen.has_markers(content)

    def _run_engine_codegen(self) -> None:
        """Refresh derived data, run the codegen pass, atomically rewrite
        src/region_map.c. Raises CodegenError on failure (file untouched).
        """
        if not os.path.exists(self.region_map_c_path):
            return
        self._refresh_engine_mapsecs()
        with open(self.region_map_c_path, "r", encoding="utf-8") as f:
            original = f.read()
        new_content = _codegen.apply_codegen(original, self._engine_state)
        _codegen.verify_marker_integrity(new_content)
        if new_content == original:
            return  # nothing to write
        # Atomic write via temp + os.replace — never leaves a partial file.
        tmp_path = self.region_map_c_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(new_content)
        os.replace(tmp_path, self.region_map_c_path)

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
        """Clone the source region's ARTWORK only; the new region gets a
        BLANK MAPSEC grid. This avoids the in-game lookup conflict that
        would happen if two regions both claimed ownership of the same
        MAPSECs (the engine's lookup loop matches the first region in
        slot order — the clone would silently shadow the source for any
        MAPSEC they both contain).

        If a user genuinely wants identical regions, they can paint the
        same MAPSECs into the clone manually — but they shouldn't, because
        the conflict is real.
        """
        files = self._layout_file_map()
        src_file = files.get(source)
        if not src_file:
            raise ValueError(f'Unknown region {source}')
        clean = self._sanitize_name(new_name)
        if any(r.name == clean for r in self._engine_state.regions):
            raise FileExistsError(f'region {clean} already exists')
        dest_file = f'region_map_layout_{clean}.h'
        src_path = os.path.join(self.template_dir, src_file)
        dest_path = os.path.join(self.template_dir, dest_file)
        if os.path.exists(dest_path):
            raise FileExistsError(dest_path)
        shutil.copy(src_path, dest_path)

        # Copy artwork files (.bin and .bin.lz if it exists).
        for path in self._tilemap_files(source):
            name = os.path.basename(path)
            if not name.startswith(source):
                continue
            new_path = os.path.join(self.graphics_dir,
                                    name.replace(source, clean, 1))
            shutil.copy(path, new_path)

        # Build BLANK grids of the same dimensions as the source. We do
        # NOT copy the source's MAPSEC layout — see docstring for why.
        src_grid = self.layouts.get(source, [])
        src_dng = self.dungeon_layouts.get(source, [])
        if src_grid:
            blank_map = [[None for _ in row] for row in src_grid]
        else:
            blank_map = [[None for _ in range(22)] for _ in range(20)]
        if src_dng:
            blank_dng = [[None for _ in row] for row in src_dng]
        else:
            blank_dng = [[None for _ in range(22)] for _ in range(20)]
        self.layouts[clean] = blank_map
        self.dungeon_layouts[clean] = blank_dng

        # Rename the section grid symbol inside the cloned .h so the
        # codegen's get_section_dispatch references resolve.
        self._rewrite_layout_internal_symbols(source, clean)

        # Write the blank grids back to the cloned .h (overwrites the
        # MAPSEC content that shutil.copy carried over from the source).
        prev_region = self.region
        try:
            self.region = clean
            self._save_layout_both()
        finally:
            self.region = prev_region

        # Engine table: append new region.
        self._engine_state.regions.append(_codegen.RegionRecord(name=clean))
        self._run_engine_codegen()
        self.save_sections()
        return clean

    def rename_region(self, old: str, new: str) -> str:
        clean = self._sanitize_name(new)
        if old == clean:
            return clean
        if any(r.name == clean for r in self._engine_state.regions):
            raise FileExistsError(f'region {clean} already exists')
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
            name = os.path.basename(path)
            if not name.startswith(old):
                continue
            new_path = os.path.join(self.graphics_dir,
                                    name.replace(old, clean, 1))
            os.rename(path, new_path)
        self.layouts[clean] = self.layouts.pop(old)
        if old in self.dungeon_layouts:
            self.dungeon_layouts[clean] = self.dungeon_layouts.pop(old)

        self._rewrite_layout_internal_symbols(old, clean)

        # Engine table: rename in place (preserves slot order).
        for rec in self._engine_state.regions:
            if rec.name == old:
                rec.name = clean
                break
        # Visibility gates: rewire to new region name.
        for gate in self._engine_state.gates:
            if gate.region_name == old:
                gate.region_name = clean
        # Update self.region pointer if it was the renamed region.
        if self.region == old:
            self.region = clean
        self._run_engine_codegen()
        self.save_sections()
        return clean

    def delete_region(self, region: str):
        region = self._sanitize_name(region)
        # Refuse to delete the last remaining region — the engine needs at
        # least one slot.
        living = [r for r in self._engine_state.regions if r.name != region]
        if not living:
            raise ValueError('cannot delete the only remaining region')

        files = self._layout_file_map()
        layout_file = files.get(region)
        if layout_file:
            path = os.path.join(self.template_dir, layout_file)
            if os.path.exists(path):
                os.remove(path)
        for path in self._tilemap_files(region):
            name = os.path.basename(path)
            if not name.startswith(region):
                continue
            if os.path.exists(path):
                os.remove(path)
        self.layouts.pop(region, None)
        self.dungeon_layouts.pop(region, None)

        # Engine table: drop the slot.
        self._engine_state.regions = [
            r for r in self._engine_state.regions if r.name != region
        ]
        # Drop visibility gates anchored to the deleted region — silent
        # (the user repurposed the slot, the gate's reason for existing is gone).
        self._engine_state.gates = [
            g for g in self._engine_state.gates if g.region_name != region
        ]
        # If the deleted region was current, switch to the new slot 0.
        if self.region == region and self._engine_state.regions:
            self.region = self._engine_state.regions[0].name

        self._run_engine_codegen()
        self.save_sections()

    # ------------------------------------------------------------------
    # Phase 5 staging API (UI cutover lands in Phase 5)
    # ------------------------------------------------------------------
    # The methods above run codegen immediately so the existing UI works
    # end-to-end today. The staging methods below queue ops in memory; a
    # future flush_staged_region_ops() method will execute them in order.
    # Intentionally unused in this phase — the UI still calls the immediate
    # methods. Phase 5 will switch the UI over.

    def stage_create_region(self, name: str) -> str:
        ok, err = self.validate_region_name(name)
        if not ok:
            raise ValueError(err)
        clean = self._sanitize_name(name)
        self._pending_creates.append(_codegen.RegionRecord(name=clean))
        return clean

    def stage_clone_region(self, source: str, new_name: str) -> str:
        if not any(r.name == source for r in self._engine_state.regions):
            raise ValueError(f'unknown source region {source}')
        ok, err = self.validate_region_name(new_name)
        if not ok:
            raise ValueError(err)
        clean = self._sanitize_name(new_name)
        self._pending_clones.append((source, clean))
        return clean

    def stage_rename_region(self, old: str, new: str) -> str:
        clean_new = self._sanitize_name(new)
        if old == clean_new:
            return clean_new
        ok, err = self.validate_region_name(new)
        if not ok:
            raise ValueError(err)
        self._pending_renames.append((old, clean_new))
        return clean_new

    def stage_delete_region(self, region: str) -> None:
        region = self._sanitize_name(region)
        living = [r.name for r in self._engine_state.regions
                  if r.name != region and r.name not in self._pending_deletes]
        if not living:
            raise ValueError('cannot delete the only remaining region')
        if region not in self._pending_deletes:
            self._pending_deletes.append(region)

    def has_pending_region_ops(self) -> bool:
        return bool(self._pending_creates or self._pending_clones
                    or self._pending_renames or self._pending_deletes)

    def discard_pending_region_ops(self) -> None:
        self._pending_creates.clear()
        self._pending_clones.clear()
        self._pending_renames.clear()
        self._pending_deletes.clear()

    def pending_region_ops_summary(self) -> str:
        """Short human-readable summary of staged region ops, e.g.
        "+2 new, ~1 renamed, -1 deleted, 1 cloned"."""
        bits = []
        if self._pending_creates:
            bits.append(f"+{len(self._pending_creates)} new")
        if self._pending_renames:
            bits.append(f"~{len(self._pending_renames)} renamed")
        if self._pending_deletes:
            bits.append(f"-{len(self._pending_deletes)} deleted")
        if self._pending_clones:
            bits.append(f"{len(self._pending_clones)} cloned")
        return ", ".join(bits)

    def create_empty_region_files(self, name: str) -> None:
        """Create blank layout .h + blank .bin tilemap on disk for a brand-new
        region. Used by flush_pending_region_ops() — not for direct UI use.
        """
        layout_path = os.path.join(
            self.template_dir, f"region_map_layout_{name}.h"
        )
        if os.path.exists(layout_path):
            raise FileExistsError(layout_path)
        camel = self._camel(name)
        # 20 rows × 22 cols — vanilla MAP_HEIGHT × MAP_WIDTH (matches the
        # existing layouts).
        rows = []
        rows.append(
            f"static const u8 sRegionMapSections_{camel}[LAYER_COUNT][MAP_HEIGHT][MAP_WIDTH] = {{\n"
        )
        for layer in ("[LAYER_MAP]", "[LAYER_DUNGEON]"):
            rows.append(f"    {layer} =\n    {{\n")
            for _ in range(20):
                cells = ", ".join(["MAPSEC_NONE"] * 22)
                rows.append(f"        {{{cells}}},\n")
            rows.append("    },\n")
        rows.append("};\n")
        # newline='' (not '\n') is the right choice for round-trip safety:
        # vanilla decomp uses LF and our rows already end in '\n'. Using
        # newline='\n' would still be LF on disk, but '' is the canonical
        # "leave my line endings exactly as I wrote them" mode.
        with open(layout_path, "w", encoding="utf-8", newline="") as f:
            f.writelines(rows)

        # Blank tilemap: 30 cols × 20 rows × 2 bytes = 1200 bytes of zeros.
        bin_path = os.path.join(self.graphics_dir, f"{name}.bin")
        if not os.path.exists(bin_path):
            with open(bin_path, "wb") as f:
                f.write(b"\x00" * (30 * 20 * 2))

    def flush_pending_region_ops(self) -> None:
        """Apply every staged region op to disk in dependency-safe order:
        deletes, renames, clones, creates. Each op runs its own codegen
        pass (idempotent — final file state is the same regardless of how
        many times codegen runs during the flush).

        On any exception, pending lists are preserved so the user can fix
        and retry; partial work already on disk is NOT rolled back.
        """
        # 1. Deletes — free up names + slots.
        for name in list(self._pending_deletes):
            self.delete_region(name)
            self._pending_deletes.remove(name)

        # 2. Renames — old -> new. Apply in order; later renames may chain.
        for old, new in list(self._pending_renames):
            self.rename_region(old, new)
            self._pending_renames.remove((old, new))

        # 3. Clones.
        for source, new in list(self._pending_clones):
            self.clone_region(source, new)
            self._pending_clones.remove((source, new))

        # 4. Creates — new blank regions.
        for rec in list(self._pending_creates):
            self.create_empty_region_files(rec.name)
            # Refresh in-memory state.
            result = self._load_layout(f"region_map_layout_{rec.name}.h")
            if result:
                map_grid, dungeon_grid = result
                self.layouts[rec.name] = map_grid
                self.dungeon_layouts[rec.name] = dungeon_grid
            self._engine_state.regions.append(rec)
            self._pending_creates.remove(rec)
            self._run_engine_codegen()

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
        # newline='' preserves whatever line endings the file had (LF in
        # vanilla pokefirered). Without it, Python on Windows converts
        # CRLF on read AND writes CRLF, silently flipping the whole file.
        with open(path, 'r', encoding='utf-8', newline='') as fh:
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
                # End of dungeon block — the inner closing brace of the
                # LAYER_DUNGEON array, which is `}` (vanilla, no comma —
                # last entry in outer array) or `},` (intermediate). Row
                # lines start with `{`, so stripping a row gives `{...}`,
                # which never matches a stripped `}` exactly. This is the
                # ONLY safe way to find the inner close — earlier code
                # matched `};` and wound up eating the inner `}` along with
                # the dungeon rows.
                stripped = line.strip()
                if stripped == '}' or stripped == '},':
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

        with open(path, 'w', encoding='utf-8', newline='') as fh:
            fh.writelines(lines)

    def _tilemap_path(self, region: Optional[str] = None) -> str:
        region = region or self.region
        return os.path.join(self.graphics_dir, f'{region}.bin')

    def _tilemap_files(self, region: str) -> List[str]:
        """Exact-match per-region tilemap files.

        Returns only `<region>.bin` and `<region>.bin.lz`. Avoids false hits
        like switch_map_kanto_sevii_123.bin matching region 'kanto'.
        """
        if not os.path.isdir(self.graphics_dir):
            return []
        wanted = {f"{region}.bin", f"{region}.bin.lz"}
        files = []
        for name in os.listdir(self.graphics_dir):
            if name in wanted:
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
