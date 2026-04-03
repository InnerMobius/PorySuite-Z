"""
Map Renamer — manages map groups, renaming, moving, deleting maps.

Ported from TriforceGUI/MapRenamer.py. All paths are relative to root_dir.
"""

import os
import json
import re
import subprocess
from typing import List, Tuple, Callable, Optional

from eventide.backend.file_utils import replace_in_file, replace_repo_wide


class MapRenamer:
    def __init__(self, root_dir: str):
        self.root_dir = root_dir
        self.map_groups_json = os.path.join(root_dir, 'data', 'maps', 'map_groups.json')
        self.maps_dir = os.path.join(root_dir, 'data', 'maps')
        self.layouts_dir = os.path.join(root_dir, 'data', 'layouts')
        self.groups = self.load_map_groups()

    def load_map_groups(self) -> dict:
        if not os.path.exists(self.map_groups_json):
            raise FileNotFoundError(self.map_groups_json)
        try:
            with open(self.map_groups_json) as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            if getattr(self, "groups", None):
                try:
                    self.save_map_groups()
                    with open(self.map_groups_json) as chk:
                        return json.load(chk)
                except Exception as e2:
                    raise RuntimeError(f"Invalid map_groups.json: {e2}") from e
            raise RuntimeError(f"Invalid map_groups.json: {e}") from e

    def save_map_groups(self):
        tmp = self.map_groups_json + '.tmp'
        with open(tmp, 'w', newline='\n') as f:
            json.dump(self.groups, f, indent=2)
            f.write('\n')
        try:
            with open(tmp) as chk:
                json.load(chk)
        except Exception as e:
            raise RuntimeError(f'Failed to write valid JSON: {e}') from e
        os.replace(tmp, self.map_groups_json)

    def create_group(self, group_name: str):
        if group_name in self.groups:
            raise ValueError(f"Group {group_name} already exists")
        self.groups['group_order'].append(group_name)
        self.groups[group_name] = []
        self.save_map_groups()

    def delete_group(self, group_name: str):
        if group_name not in self.groups:
            raise KeyError(group_name)
        if self.groups[group_name]:
            raise ValueError(f"Group {group_name} is not empty")
        self.groups['group_order'].remove(group_name)
        del self.groups[group_name]
        self.save_map_groups()

    def rename_group(self, old: str, new: str):
        if old == new:
            return
        order = self.groups['group_order']
        for i, g in enumerate(order):
            if g == old:
                order[i] = new
                break
        self.groups[new] = self.groups.pop(old)
        self.save_map_groups()

    def rename_map(self, group: str, map_folder: str, *, new_group: str = None,
                   new_folder: str = None, new_id: str = None,
                   callback: Optional[Callable[[str], None]] = None):
        new_group = new_group or group
        new_folder = new_folder or map_folder
        old_id = f"MAP_{map_folder.upper()}"
        new_id = new_id or old_id

        if map_folder != new_folder:
            old_path = os.path.join(self.maps_dir, map_folder)
            new_path = os.path.join(self.maps_dir, new_folder)
            if os.path.exists(new_path):
                raise FileExistsError(new_path)
            os.rename(old_path, new_path)

            old_layout = os.path.join(self.layouts_dir, map_folder)
            new_layout = os.path.join(self.layouts_dir, new_folder)
            if os.path.exists(old_layout):
                if os.path.exists(new_layout):
                    raise FileExistsError(new_layout)
                os.rename(old_layout, new_layout)

        self.update_map_json(new_folder, new_folder, new_id)

        if group != new_group:
            self.groups[group].remove(map_folder)
            if new_group not in self.groups:
                self.groups['group_order'].append(new_group)
                self.groups[new_group] = []
            self.groups[new_group].append(new_folder)
        else:
            self._rename_map_in_groups(new_group, map_folder, new_folder)
        self.save_map_groups()

        repls: List[Tuple[str, str]] = []
        if map_folder != new_folder:
            repls.append((map_folder, new_folder))
            repls.append((f"maps/{map_folder}", f"maps/{new_folder}"))
        if old_id != new_id:
            repls.append((old_id, new_id))
        if group != new_group:
            repls.append((group, new_group))
        replace_repo_wide(self.root_dir, repls, callback)
        self._generate_headers()

    def move_map(self, group: str, map_folder: str, new_group: str):
        if group == new_group:
            return
        if map_folder not in self.groups.get(group, []):
            raise ValueError(f"Map {map_folder} not in group {group}")
        self.groups[group].remove(map_folder)
        if new_group not in self.groups:
            self.groups['group_order'].append(new_group)
            self.groups[new_group] = []
        self.groups[new_group].append(map_folder)
        self.save_map_groups()

        new_section = self._guess_section_for_group(new_group)
        if new_section:
            self._update_map_section(map_folder, new_section)

    def delete_map(self, group: str, map_folder: str,
                   callback: Optional[Callable[[str], None]] = None):
        map_path = os.path.join(self.maps_dir, map_folder)
        map_json = os.path.join(map_path, 'map.json')
        map_id = None
        if os.path.exists(map_json):
            with open(map_json) as f:
                data = json.load(f)
                map_id = data.get('id')
        self._delete_map_folder(map_folder)
        if map_folder in self.groups.get(group, []):
            self.groups[group].remove(map_folder)
            if not self.groups[group]:
                del self.groups[group]
                self.groups['group_order'].remove(group)
        self.save_map_groups()

        repls: List[Tuple[str, str]] = []
        if map_id:
            repls.append((map_id, 'MAP_UNDEFINED'))
        repls.append((map_folder, ''))
        replace_repo_wide(self.root_dir, repls, callback)
        self._generate_headers()

    def clean_orphaned_map_data(
        self,
        ensure_headers: bool = False,
        callback: Optional[Callable[[str], None]] = None,
    ) -> int:
        self.groups = self.load_map_groups()
        removed_maps: List[str] = []
        removed_ids: List[str] = []

        for group in self.groups.get('group_order', [])[:]:
            maps = self.groups.get(group, [])[:]
            for m in maps:
                map_dir = os.path.join(self.maps_dir, m)
                map_json = os.path.join(map_dir, 'map.json')
                bad = False
                if not os.path.isdir(map_dir):
                    bad = True
                else:
                    try:
                        with open(map_json) as f:
                            json.load(f)
                    except Exception:
                        bad = True
                if bad:
                    self.groups[group].remove(m)
                    removed_maps.append(m)
                    removed_ids.append(f"MAP_{m.upper()}")
                    self._delete_map_folder(m)
            if not self.groups.get(group):
                del self.groups[group]
                self.groups['group_order'].remove(group)

        if removed_maps:
            self.save_map_groups()
            repls = [(mid, 'MAP_UNDEFINED') for mid in removed_ids]
            repls += [(m, '') for m in removed_maps]
            replace_repo_wide(self.root_dir, repls, callback)

        try:
            with open(self.map_groups_json) as f:
                json.load(f)
        except Exception as e:
            try:
                self.save_map_groups()
                with open(self.map_groups_json) as f:
                    json.load(f)
            except Exception as e2:
                raise RuntimeError(f'Invalid map_groups.json: {e2}') from e

        attempts = 0
        group_attempts = 0
        while True:
            try:
                if removed_maps or ensure_headers or attempts or group_attempts:
                    self._generate_headers()
                break
            except subprocess.CalledProcessError as e:
                msg = e.stderr.strip() if e.stderr else str(e)
                if 'map_groups.json' in msg and group_attempts < 2:
                    try:
                        self.save_map_groups()
                    except Exception as e2:
                        raise RuntimeError(f'Invalid map_groups.json: {e2}') from e
                    group_attempts += 1
                    continue
                m = re.search(r"(?:[A-Za-z]:)?[^\n]*?data[\\/]+maps[\\/]+([^\\/]+)[\\/]+map\.json", msg)
                if m and attempts < 5:
                    folder = os.path.basename(m.group(1))
                    if self._remove_map_from_groups(folder):
                        removed_maps.append(folder)
                        removed_ids.append(f"MAP_{folder.upper()}")
                        self.save_map_groups()
                        attempts += 1
                        continue
                raise RuntimeError(f'Failed to generate map headers: {msg}') from e

        return len(removed_maps)

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
        groups = os.path.join(self.root_dir, 'data', 'maps', 'map_groups.json')
        subprocess.run(
            [exe, 'groups', 'firered', groups,
             os.path.join(self.root_dir, 'data', 'maps'),
             os.path.join(self.root_dir, 'include', 'constants')],
            check=True, capture_output=True, text=True,
        )
        map_jsons = []
        for root, dirs, files in os.walk(self.maps_dir):
            if 'map.json' in files:
                path = os.path.join(root, 'map.json')
                try:
                    with open(path) as fh:
                        json.load(fh)
                except Exception:
                    continue
                map_jsons.append(path)
        subprocess.run(
            [exe, 'event_constants', 'firered', *map_jsons,
             os.path.join(self.root_dir, 'include', 'constants', 'map_event_ids.h')],
            check=True, capture_output=True, text=True,
        )

    def _rename_map_in_groups(self, group: str, old_map: str, new_map: str):
        maps = self.groups[group]
        for i, m in enumerate(maps):
            if m == old_map:
                maps[i] = new_map
                break

    def _remove_map_from_groups(self, map_folder: str) -> bool:
        removed = False
        for grp in self.groups.get('group_order', [])[:]:
            maps = self.groups.get(grp, [])
            if map_folder in maps:
                maps.remove(map_folder)
                removed = True
                if not maps:
                    del self.groups[grp]
                    self.groups['group_order'].remove(grp)
        if removed:
            self._delete_map_folder(map_folder)
        return removed

    def _delete_dir(self, path: str) -> None:
        if not os.path.isdir(path):
            return
        for root, dirs, files in os.walk(path, topdown=False):
            for name in files:
                os.remove(os.path.join(root, name))
            for name in dirs:
                os.rmdir(os.path.join(root, name))
        os.rmdir(path)

    def _delete_map_folder(self, folder: str) -> None:
        self._delete_dir(os.path.join(self.maps_dir, folder))
        self._delete_dir(os.path.join(self.layouts_dir, folder))

    def update_map_json(self, folder: str, new_name: str, new_id: str):
        path = os.path.join(self.maps_dir, folder, 'map.json')
        if not os.path.exists(path):
            return
        with open(path) as f:
            map_data = json.load(f)
        map_data['name'] = new_name
        map_data['id'] = new_id
        with open(path, 'w', newline='\n') as f:
            json.dump(map_data, f, indent=2)
            f.write('\n')

    def _update_map_section(self, folder: str, section: str):
        path = os.path.join(self.maps_dir, folder, 'map.json')
        if not os.path.exists(path):
            return
        with open(path) as f:
            data = json.load(f)
        if data.get('region_map_section') == section:
            return
        data['region_map_section'] = section
        with open(path, 'w', newline='\n') as f:
            json.dump(data, f, indent=2)
            f.write('\n')

    def _guess_section_for_group(self, group: str) -> Optional[str]:
        if not group.startswith('gMapGroup_Indoor'):
            return None
        maps = self.groups.get(group, [])
        for m in maps:
            path = os.path.join(self.maps_dir, m, 'map.json')
            if os.path.exists(path):
                try:
                    with open(path) as fh:
                        sec = json.load(fh).get('region_map_section')
                    if sec:
                        return sec
                except Exception:
                    pass
        base = group[len('gMapGroup_Indoor'):]
        towns = self.groups.get('gMapGroup_TownsAndRoutes', [])
        for m in towns:
            if m.startswith(base):
                path = os.path.join(self.maps_dir, m, 'map.json')
                if os.path.exists(path):
                    try:
                        with open(path) as fh:
                            sec = json.load(fh).get('region_map_section')
                        if sec:
                            return sec
                    except Exception:
                        pass
        sec_guess = 'MAPSEC_' + re.sub(r'(?<=[a-z0-9])(?=[A-Z0-9])', '_', base).upper()
        return None
