"""
Layout Renamer — rename, delete, clean layouts and apply tilesets.

Ported from TriforceGUI/LayoutRenamer.py. All paths relative to root_dir.
"""

import os
import json
import subprocess
from typing import List, Tuple

from eventide.backend.file_utils import replace_repo_wide


class LayoutRenamer:
    def __init__(self, root_dir: str):
        self.root_dir = root_dir
        self.layouts_json = os.path.join(root_dir, 'data', 'layouts', 'layouts.json')
        self.layouts_dir = os.path.join(root_dir, 'data', 'layouts')
        self.data = self.load_layouts()

    def load_layouts(self) -> dict:
        if not os.path.exists(self.layouts_json):
            raise FileNotFoundError(self.layouts_json)
        with open(self.layouts_json) as f:
            return json.load(f)

    def save_layouts(self):
        with open(self.layouts_json, 'w', newline='\n') as f:
            json.dump(self.data, f, indent=2)
            f.write('\n')

    def get_layouts(self) -> List[dict]:
        return self.data.get('layouts', [])

    def rename_layout(self, old_id: str, new_id: str, *, new_folder: str = None,
                      primary_tileset: str = None, secondary_tileset: str = None):
        layout = None
        for l in self.data.get('layouts', []):
            if l['id'] == old_id:
                layout = l
                break
        if layout is None:
            raise ValueError(f"Layout {old_id} not found")
        folder = os.path.basename(os.path.dirname(layout['blockdata_filepath']))
        new_folder = new_folder or folder
        if folder != new_folder:
            old_path = os.path.join(self.layouts_dir, folder)
            new_path = os.path.join(self.layouts_dir, new_folder)
            if os.path.exists(new_path):
                raise FileExistsError(new_path)
            os.rename(old_path, new_path)
            layout['border_filepath'] = layout['border_filepath'].replace(folder, new_folder)
            layout['blockdata_filepath'] = layout['blockdata_filepath'].replace(folder, new_folder)
        if primary_tileset:
            layout['primary_tileset'] = primary_tileset
        if secondary_tileset:
            layout['secondary_tileset'] = secondary_tileset
        if old_id != new_id:
            layout['id'] = new_id
            layout['name'] = layout['name'].replace(
                old_id.replace('LAYOUT_', ''), new_id.replace('LAYOUT_', ''))
            replace_repo_wide(self.root_dir, [(old_id, new_id)])
        self.save_layouts()

    def _ensure_mapjson(self) -> str:
        exe = os.path.join(self.root_dir, 'tools', 'mapjson',
                           'mapjson' + ('.exe' if os.name == 'nt' else ''))
        if os.path.exists(exe):
            return exe
        makefile_dir = os.path.join(self.root_dir, 'tools', 'mapjson')
        subprocess.run(['make', '-C', makefile_dir], check=True)
        if not os.path.exists(exe):
            raise RuntimeError('mapjson executable not found after build')
        return exe

    def _generate_headers(self) -> None:
        exe = self._ensure_mapjson()
        layouts = os.path.join(self.root_dir, 'data', 'layouts', 'layouts.json')
        subprocess.run(
            [exe, 'layouts', 'firered', layouts,
             os.path.join(self.root_dir, 'data', 'layouts'),
             os.path.join(self.root_dir, 'include', 'constants')],
            check=True,
        )

    def maps_using_layout(self, layout_id: str) -> List[str]:
        matches: List[str] = []
        maps_dir = os.path.join(self.root_dir, 'data', 'maps')
        for root, dirs, files in os.walk(maps_dir):
            if 'map.json' not in files:
                continue
            path = os.path.join(root, 'map.json')
            try:
                with open(path) as f:
                    data = json.load(f)
                if data.get('layout') == layout_id:
                    matches.append(path)
            except Exception:
                continue
        return matches

    def delete_layout(self, layout_id: str):
        layout = None
        for l in self.data.get('layouts', []):
            if l['id'] == layout_id:
                layout = l
                break
        if layout is None:
            raise ValueError(f"Layout {layout_id} not found")
        refs = self.maps_using_layout(layout_id)
        if refs:
            raise ValueError(f"Layout {layout_id} is used by {len(refs)} map(s)")
        folder = os.path.basename(os.path.dirname(layout['blockdata_filepath']))
        layout_path = os.path.join(self.layouts_dir, folder)
        if os.path.isdir(layout_path):
            for root, dirs, files in os.walk(layout_path, topdown=False):
                for name in files:
                    os.remove(os.path.join(root, name))
                for name in dirs:
                    os.rmdir(os.path.join(root, name))
            os.rmdir(layout_path)
        self.data['layouts'].remove(layout)
        self.save_layouts()
        replace_repo_wide(self.root_dir, [(layout_id, ''), (folder, '')])
        self._generate_headers()

    def clean_orphaned_layouts(self) -> int:
        self.data = self.load_layouts()
        removed_ids: List[str] = []
        removed_folders: List[str] = []
        for layout in self.data.get('layouts', [])[:]:
            folder = os.path.basename(os.path.dirname(layout['blockdata_filepath']))
            layout_path = os.path.join(self.layouts_dir, folder)
            if not os.path.isdir(layout_path) and not self.maps_using_layout(layout['id']):
                self.data['layouts'].remove(layout)
                removed_ids.append(layout['id'])
                removed_folders.append(folder)
        if removed_ids:
            self.save_layouts()
            repls: List[Tuple[str, str]] = []
            repls += [(lid, '') for lid in removed_ids]
            repls += [(f, '') for f in removed_folders]
            replace_repo_wide(self.root_dir, repls)
            self._generate_headers()
        return len(removed_ids)
