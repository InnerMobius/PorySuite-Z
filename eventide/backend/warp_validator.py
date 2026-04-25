"""
Warp Validator — find and clean invalid warp destinations.

Ported from TriforceGUI/WarpValidator.py. All paths relative to root_dir.
"""

import os
import json
from typing import List


class WarpIssue:
    def __init__(self, map_path: str, index: int, dest_map: str):
        self.map_path = map_path
        self.index = index
        self.dest_map = dest_map


class WarpValidator:
    def __init__(self, root_dir: str):
        self.root_dir = root_dir
        self.maps_dir = os.path.join(root_dir, 'data', 'maps')
        self.map_ids = self.collect_map_ids()

    def collect_map_ids(self) -> List[str]:
        ids = []
        for root, dirs, files in os.walk(self.maps_dir):
            if 'map.json' in files:
                path = os.path.join(root, 'map.json')
                try:
                    with open(path) as f:
                        data = json.load(f)
                        if 'id' in data:
                            ids.append(data['id'])
                except Exception:
                    pass
        return ids

    def find_invalid_warps(self) -> List[WarpIssue]:
        issues: List[WarpIssue] = []
        valid = set(self.map_ids)
        for root, dirs, files in os.walk(self.maps_dir):
            if 'map.json' not in files:
                continue
            path = os.path.join(root, 'map.json')
            try:
                with open(path) as f:
                    data = json.load(f)
            except Exception:
                continue
            warps = data.get('warp_events') or []
            for i, w in enumerate(warps):
                dest = w.get('dest_map')
                if dest not in valid and dest not in {'MAP_DYNAMIC', 'MAP_UNDEFINED'}:
                    issues.append(WarpIssue(path, i, dest))
        return issues

    def references_to_map(self, target: str) -> List[WarpIssue]:
        issues: List[WarpIssue] = []
        for root, dirs, files in os.walk(self.maps_dir):
            if 'map.json' not in files:
                continue
            path = os.path.join(root, 'map.json')
            try:
                with open(path) as f:
                    data = json.load(f)
            except Exception:
                continue
            warps = data.get('warp_events') or []
            for i, w in enumerate(warps):
                if w.get('dest_map') == target:
                    issues.append(WarpIssue(path, i, target))
        return issues

    def clean_invalid_warps(self) -> tuple[int, list[str]]:
        """Remove invalid warp events from all map.json files.

        Returns (removed_count, affected_map_folders).
        """
        issues = self.find_invalid_warps()
        if not issues:
            return 0, []

        by_map: dict[str, list[int]] = {}
        for issue in issues:
            by_map.setdefault(issue.map_path, []).append(issue.index)

        removed = 0
        affected_folders: list[str] = []
        for path, indices in by_map.items():
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            warps = data.get('warp_events') or []
            for idx in sorted(indices, reverse=True):
                if 0 <= idx < len(warps):
                    del warps[idx]
                    removed += 1
            data['warp_events'] = warps
            with open(path, 'w', encoding='utf-8', newline='\n') as f:
                json.dump(data, f, indent=2)
                f.write('\n')
            # Extract folder name (parent dir of map.json)
            affected_folders.append(os.path.basename(os.path.dirname(path)))

        return removed, affected_folders
