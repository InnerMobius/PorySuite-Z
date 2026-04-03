"""
Tileset Renamer — rename secondary tilesets with repo-wide reference updates.

Ported from TriforceGUI/TilesetRenamer.py. All paths relative to root_dir.
"""

import os
import re
from typing import List, Tuple

from eventide.backend.file_utils import replace_in_file, replace_repo_wide

TILESET_REGEX = re.compile(
    r'gTilesetTiles_(\w+)\[]\s*=\s*INCBIN_\w+\("data/tilesets/secondary/([^/]+)/tiles'
)


def _get_short_name(folder: str) -> str:
    return folder.replace('islands', '').replace('__', '_').strip('_')


class TilesetRenamer:
    def __init__(self, root_dir: str):
        self.root_dir = root_dir
        self.graphics_h = os.path.join(root_dir, 'src', 'data', 'tilesets', 'graphics.h')
        self.metatiles_h = os.path.join(root_dir, 'src', 'data', 'tilesets', 'metatiles.h')
        self.headers_h = os.path.join(root_dir, 'src', 'data', 'tilesets', 'headers.h')
        self.layouts_json = os.path.join(root_dir, 'data', 'layouts', 'layouts.json')
        self.tileset_rules = os.path.join(root_dir, 'tileset_rules.mk')
        self.tileset_dir = os.path.join(root_dir, 'data', 'tilesets', 'secondary')

    def parse_tilesets(self) -> List[Tuple[str, str]]:
        tilesets = []
        if not os.path.exists(self.graphics_h):
            raise FileNotFoundError(self.graphics_h)
        with open(self.graphics_h) as f:
            for line in f:
                m = TILESET_REGEX.search(line)
                if m:
                    tilesets.append((m.group(1), m.group(2)))
        return tilesets

    def rename_tileset(self, old_label: str, old_folder: str,
                       new_label: str, new_folder: str):
        if old_folder != new_folder:
            old_path = os.path.join(self.tileset_dir, old_folder)
            new_path = os.path.join(self.tileset_dir, new_folder)
            if os.path.exists(new_path):
                raise FileExistsError(new_path)
            os.rename(old_path, new_path)

        repls: List[Tuple[str, str]] = []
        if old_folder != new_folder:
            repls.append((f'secondary/{old_folder}', f'secondary/{new_folder}'))
        short_old = _get_short_name(old_folder)
        short_new = _get_short_name(new_folder)
        if short_old != short_new:
            repls.append((f'door_anims/{short_old}', f'door_anims/{short_new}'))
        if old_label != new_label:
            repls.extend([
                (f'gTilesetTiles_{old_label}', f'gTilesetTiles_{new_label}'),
                (f'gTilesetPalettes_{old_label}', f'gTilesetPalettes_{new_label}'),
                (f'gMetatiles_{old_label}', f'gMetatiles_{new_label}'),
                (f'gMetatileAttributes_{old_label}', f'gMetatileAttributes_{new_label}'),
                (f'gTileset_{old_label}', f'gTileset_{new_label}'),
                (f'METATILE_{old_label}', f'METATILE_{new_label}'),
            ])
        files = [self.graphics_h, self.metatiles_h, self.headers_h,
                 self.tileset_rules, self.layouts_json,
                 os.path.join(self.root_dir, 'include', 'constants', 'metatile_labels.h')]
        for path in files:
            if os.path.exists(path):
                replace_in_file(path, repls)
        self._rename_door_anim_files(old_label, new_label, old_folder, new_folder)
        replace_repo_wide(self.root_dir, repls)

    def _rename_door_anim_files(self, old_label: str, new_label: str,
                                old_folder: str, new_folder: str):
        door_dir = os.path.join(self.root_dir, 'graphics', 'door_anims')
        if not os.path.isdir(door_dir):
            return
        for name in os.listdir(door_dir):
            new_name = name
            if old_folder in name:
                new_name = new_name.replace(old_folder, new_folder)
            if old_label.lower() in name.lower():
                pattern = old_label.lower()
                idx = new_name.lower().find(pattern)
                if idx != -1:
                    new_name = new_name[:idx] + new_label.lower() + new_name[idx + len(pattern):]
            short_old = _get_short_name(old_folder)
            short_new = _get_short_name(new_folder)
            if short_old in new_name:
                new_name = new_name.replace(short_old, short_new)
            if new_name != name:
                os.rename(os.path.join(door_dir, name), os.path.join(door_dir, new_name))
