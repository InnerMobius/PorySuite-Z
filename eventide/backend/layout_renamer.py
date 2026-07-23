"""
Layout Renamer — rename, delete, clean layouts and apply tilesets.

Ported from TriforceGUI/LayoutRenamer.py. All paths relative to root_dir.
"""

import os
import re
import json
import subprocess
from typing import List, Tuple

from eventide.backend.file_utils import replace_repo_wide, is_text_file


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
            # whole_word: renaming LAYOUT_HOUSE3 must not rewrite
            # the middle of LAYOUT_HOUSE30.
            replace_repo_wide(self.root_dir, [(old_id, new_id)],
                              whole_word=True)
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
        # RELATIVE paths, run from the project root — see map_renamer. mapjson
        # writes its directory argument verbatim into the generated `.include`
        # lines, so an absolute Windows path bakes `C:\GBA\…` into them and the
        # assembler fails on the `\G` escape.
        exe = self._ensure_mapjson()
        subprocess.run(
            [exe, 'layouts', 'firered', 'data/layouts/layouts.json',
             'data/layouts', 'include/constants'],
            check=True, cwd=self.root_dir,
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

    def _layout_folder(self, layout: dict) -> str:
        """The layout's own directory under data/layouts, or '' if it has none.

        `os.path.basename(os.path.dirname(blockdata_filepath))` returns
        ``"layouts"`` for any entry whose blockdata sits directly in
        ``data/layouts/`` rather than in a subfolder — and that value used to be
        fed to a repo-wide search-and-replace. Deleting one such layout deleted
        the word "layouts" from every text file in the project: `data/layouts/`
        became `data//`, `constants/layouts.h` became `constants/.h`, and the
        `"layouts"` key in layouts.json became `""`. Anything that resolves to
        the layouts directory itself is not a per-layout folder.
        """
        raw = layout.get('blockdata_filepath') or ''
        folder = os.path.basename(os.path.dirname(raw))
        if not folder or os.path.normcase(folder) == 'layouts':
            return ''
        candidate = os.path.join(self.layouts_dir, folder)
        if not os.path.isdir(candidate):
            return ''
        return folder

    def references_outside_generated(self, layout_id: str) -> List[str]:
        """Files naming this layout that a regeneration will NOT rewrite.

        `data/layouts/layouts.inc`, `layouts_table.inc` and
        `include/constants/layouts.h` are produced by mapjson, so a reference
        there disappears on its own. A reference anywhere else is real code,
        and blanking it (as this class used to) turns it into a syntax error
        instead of a clean removal.
        """
        generated = {
            os.path.normcase(os.path.join(self.layouts_dir, 'layouts.inc')),
            os.path.normcase(os.path.join(self.layouts_dir, 'layouts_table.inc')),
            os.path.normcase(os.path.join(self.root_dir, 'include', 'constants',
                                          'layouts.h')),
            os.path.normcase(self.layouts_json),
        }
        hits: List[str] = []
        pattern = re.compile(r'\b%s\b' % re.escape(layout_id))
        for root, dirs, files in os.walk(self.root_dir):
            dirs[:] = [d for d in dirs if d not in ('.git', 'build')]
            for name in files:
                path = os.path.join(root, name)
                if os.path.normcase(path) in generated:
                    continue
                if not is_text_file(path):
                    continue
                try:
                    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                        if pattern.search(f.read()):
                            hits.append(os.path.relpath(path, self.root_dir))
                except OSError:
                    continue
        return hits

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
        code_refs = self.references_outside_generated(layout_id)
        if code_refs:
            raise ValueError(
                "%s is still referred to by %d file(s), so deleting it would "
                "break the build:\n  %s\nRemove those references first."
                % (layout_id, len(code_refs), "\n  ".join(code_refs[:10])))
        folder = self._layout_folder(layout)
        if folder:
            layout_path = os.path.join(self.layouts_dir, folder)
            for root, dirs, files in os.walk(layout_path, topdown=False):
                for name in files:
                    os.remove(os.path.join(root, name))
                for name in dirs:
                    os.rmdir(os.path.join(root, name))
            os.rmdir(layout_path)
        self.data['layouts'].remove(layout)
        self.save_layouts()
        # NO repo-wide text replacement. The only other places this id appears
        # are the generated tables, which _generate_headers rewrites from the
        # JSON — and every non-generated reference was refused above.
        self._generate_headers()

    def clean_orphaned_layouts(self) -> int:
        self.data = self.load_layouts()
        removed_ids: List[str] = []
        removed_folders: List[str] = []
        for layout in self.data.get('layouts', [])[:]:
            raw = layout.get('blockdata_filepath') or ''
            folder = os.path.basename(os.path.dirname(raw))
            # An entry whose blockdata sits directly in data/layouts has no
            # folder of its own; treating "layouts" as one made this routine
            # delete the word from every file in the project.
            if not folder or os.path.normcase(folder) == 'layouts':
                continue
            layout_path = os.path.join(self.layouts_dir, folder)
            if not os.path.isdir(layout_path) \
                    and not self.maps_using_layout(layout['id']) \
                    and not self.references_outside_generated(layout['id']):
                self.data['layouts'].remove(layout)
                removed_ids.append(layout['id'])
                removed_folders.append(folder)
        if removed_ids:
            self.save_layouts()
            # NO repo-wide text replacement — see delete_layout. Regenerating
            # the tables from the JSON is what removes these ids.
            self._generate_headers()
        return len(removed_ids)
