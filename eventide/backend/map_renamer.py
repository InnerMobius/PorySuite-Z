"""
Map Renamer — manages map groups, renaming, moving, deleting maps.

Ported from the standalone MapRenamer.py. All paths are relative to root_dir.
"""

import os
import json
import re
import subprocess
from typing import List, Tuple, Callable, Optional

from eventide.backend.file_utils import replace_in_file, replace_repo_wide


class UnsafeMapOperationError(RuntimeError):
    """A map rename or delete was refused because it would alter content
    belonging to OTHER maps.

    C1 containment, added 2026-10-31 (finding item 0). Both operations end in
    `replace_repo_wide` with PLAIN SUBSTRING pairs, so a map whose name occurs
    inside another map's name rewrites that one too.

    Delete was the worse of the two: it passed ``(map_folder, '')`` — an EMPTY
    replacement — which `replace_repo_wide`'s own docstring forbids in as many
    words ("never pass an empty `new` here at all"). Measured: deleting a map
    called ``Route1`` turned ``data/maps/Route10/map.bin`` into
    ``data/maps/0/map.bin``, ``Route11_Text`` into ``1_Text`` and
    ``EventScript_Route1_Sign`` into ``EventScript__Sign`` — every one of them
    in a file belonging to a different map.

    This is CONTAINMENT, not the repair. Some legitimate operations are now
    refused rather than performed unsafely; the safe implementation is C2.
    """

    def __init__(self, operation: str, subject: str, reasons):
        self.operation = operation
        self.subject = subject
        self.reasons = list(reasons)
        super().__init__(self.plain_english())

    def plain_english(self) -> str:
        body = "\n".join(f"  • {r}" for r in self.reasons[:10])
        more = ("\n  • …and %d more" % (len(self.reasons) - 10)
                if len(self.reasons) > 10 else "")
        return ("The %s of '%s' was stopped before anything was changed:\n"
                "%s%s\n\n"
                "Nothing has been changed. Resolve the items above — rename "
                "the conflicting map, or update the files that still refer to "
                "this one — and try again."
                % (self.operation, self.subject, body, more))


def _folder_to_map_id(folder: str) -> str:
    """Convert a map FOLDER name to its MAP_ constant the way mapjson does:
    insert an underscore at every CamelCase boundary, then upper-case.

    ``PalletTown_PlayersHouse_1F`` -> ``MAP_PALLET_TOWN_PLAYERS_HOUSE_1F``.
    NOTE the plain ``MAP_{folder.upper()}`` form is WRONG for multi-word
    folders — it drops the boundary underscores (``MAP_PALLETTOWN_PLAYERSHOUSE_1F``),
    a constant that exists nowhere, so a rename using it never replaces the real
    constant and silently breaks heal_locations.json / warps after the rename.
    """
    snake = re.sub(r'(?<=[a-z])(?=[A-Z])', '_', folder)
    return 'MAP_' + snake.upper()


class MapRenamer:
    def __init__(self, root_dir: str):
        self.root_dir = root_dir
        self.map_groups_json = os.path.join(root_dir, 'data', 'maps', 'map_groups.json')
        self.maps_dir = os.path.join(root_dir, 'data', 'maps')
        self.layouts_dir = os.path.join(root_dir, 'data', 'layouts')
        self.groups = self.load_map_groups()
        # Warnings from the most recent rename_map() about heal locations that
        # still point at a now-missing map (empty when all is well).
        self.last_stale_heal_locations: List[str] = []
        # Files that still mention the most recently deleted map's folder
        # name. These used to be "handled" by deleting that text from every
        # file in the project, which corrupted neighbouring maps; they are now
        # reported so the user can resolve them deliberately.
        self.last_delete_leftovers: List[str] = []

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
        # Atomic write via temp + os.replace — wrapped in try so a failure
        # at ANY point (write, validation, replace) cleans up the .tmp file
        # rather than leaving it polluting the user's project tree.
        tmp = self.map_groups_json + '.tmp'
        try:
            with open(tmp, 'w', newline='\n') as f:
                json.dump(self.groups, f, indent=2)
                f.write('\n')
            try:
                with open(tmp) as chk:
                    json.load(chk)
            except Exception as e:
                raise RuntimeError(f'Failed to write valid JSON: {e}') from e
            os.replace(tmp, self.map_groups_json)
        except Exception:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            raise

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
        # OLD constant: read the actual `id` from the current map.json (the only
        # 100%-accurate source). Only if that's missing do we derive it from the
        # folder name — using the CamelCase-aware helper, NOT MAP_{folder.upper()}
        # which drops the boundary underscores and leaves the real constant
        # un-replaced (that's what silently breaks heal_locations.json).
        old_id = self._read_map_id(map_folder) or _folder_to_map_id(map_folder)
        new_id = new_id or _folder_to_map_id(new_folder)
        # map.json 'name' is always kept in sync with the folder name.
        # External tools (Porymap, mapjson) rely on this convention.

        # C1: check BEFORE the first write. Everything below this point
        # renames folders and rewrites files; refusing afterwards would leave
        # the project half-renamed.
        if map_folder != new_folder or old_id != new_id:
            conflicts = self.map_operation_conflicts(map_folder, old_id)
            if conflicts:
                raise UnsafeMapOperationError("rename", map_folder, conflicts)

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

        # heal_locations.json references maps by MAP_ constant. replace_repo_wide
        # above already swapped old_id -> new_id inside it, but flag anything
        # that STILL points at a map the project no longer defines so the caller
        # can warn the user instead of letting the build fail later.
        self.last_stale_heal_locations = self._stale_heal_locations()

    # ── C1: map-operation containment ────────────────────────────────────

    def live_map_folders(self) -> List[str]:
        """Every map folder the project currently defines."""
        out = []
        for group, maps in (self.groups or {}).items():
            if group == 'group_order' or not isinstance(maps, list):
                continue
            out.extend(m for m in maps if isinstance(m, str))
        try:
            if os.path.isdir(self.maps_dir):
                out.extend(n for n in os.listdir(self.maps_dir)
                           if os.path.isdir(os.path.join(self.maps_dir, n)))
        except OSError:
            pass
        return sorted(set(out))

    def map_operation_conflicts(self, map_folder: str,
                                old_id: Optional[str] = None) -> List[str]:
        """Reasons an operation on *map_folder* would hit other maps.

        The criterion is the operation's OWN rule — a plain substring replace
        repo-wide — so a conflict is another LIVE map whose folder name or
        MAP_ constant contains ours. Sharing a prefix is not in itself a
        conflict; being contained is.
        """
        reasons: List[str] = []
        others = [m for m in self.live_map_folders() if m != map_folder]
        if map_folder:
            for other in others:
                if map_folder in other:
                    reasons.append(
                        "the map '%s' contains '%s' in its name, so the "
                        "replacement would rewrite it too" % (other, map_folder))
        if old_id:
            for other in others:
                other_id = self._read_map_id(other) or _folder_to_map_id(other)
                if other_id and other_id != old_id and old_id in other_id:
                    reasons.append(
                        "the constant %s belongs to the map '%s' and contains "
                        "%s" % (other_id, other, old_id))
        return reasons

    def _read_map_id(self, map_folder: str) -> Optional[str]:
        """Return the MAP_ constant recorded in a map's map.json, or None."""
        p = os.path.join(self.maps_dir, map_folder, 'map.json')
        try:
            with open(p, encoding='utf-8') as f:
                mid = json.load(f).get('id')
            return mid or None
        except Exception:
            return None

    def _stale_heal_locations(self) -> List[str]:
        """Return human-readable warnings for heal locations that reference a
        map constant the project no longer defines (post-rename safety net)."""
        try:
            from eventide.backend import heal_locations as hl
            from eventide.backend.constants_manager import ConstantsManager
        except Exception:
            return []
        try:
            # Read the FRESH constants from the header we just regenerated,
            # not the class attribute cached when the project was opened.
            valid = set(ConstantsManager._from_header(
                self.root_dir, 'include/constants/map_groups.h', 'MAP_') or [])
        except Exception:
            valid = set()
        if not valid:
            return []
        warnings: List[str] = []
        for lid, issues in hl.validate(
                self.root_dir, hl.load(self.root_dir), valid).items():
            warnings.append(f"{lid}: {'; '.join(issues)}")
        return warnings

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

        # ── C1 containment ───────────────────────────────────────────────
        # Check BEFORE anything is removed. This used to delete the map
        # folder first and only then run the replacements, so a problem
        # discovered later could not undo the deletion.
        conflicts = self.map_operation_conflicts(map_folder, map_id)
        if conflicts:
            raise UnsafeMapOperationError("deletion", map_folder, conflicts)

        # FAIL CLOSED on references this deletion cannot resolve.
        #
        # An earlier version of this repair deleted the map and then REPORTED
        # the references it had left behind. That is the same shape as finding
        # 0j: a surviving reference to something that no longer exists stops
        # the project linking, and by then the map is already gone. A map may
        # only be deleted when every surviving reference can be safely updated
        # or is provably harmless, so anything left over refuses the operation
        # BEFORE the folder is touched.
        unresolved = self._unresolved_references(map_folder, map_id)
        if unresolved:
            raise UnsafeMapOperationError(
                "deletion", map_folder,
                ["%s still refers to '%s', and this deletion cannot safely "
                 "update it" % (p, map_folder) for p in unresolved[:10]])

        self._delete_map_folder(map_folder)
        if map_folder in self.groups.get(group, []):
            self.groups[group].remove(map_folder)
            if not self.groups[group]:
                del self.groups[group]
                self.groups['group_order'].remove(group)
        self.save_map_groups()

        # The MAP_ constant is repointed as a WHOLE IDENTIFIER. Without that,
        # deleting MAP_ROUTE1 also rewrote the middle of MAP_ROUTE10.
        repls: List[Tuple[str, str]] = []
        if map_id:
            repls.append((map_id, 'MAP_UNDEFINED'))
            replace_repo_wide(self.root_dir, repls, callback, whole_word=True)

        # The blanket `(map_folder, '')` replacement that used to run here has
        # been REMOVED, not made safe. `replace_repo_wide`'s own docstring
        # forbids it outright — "never pass an empty `new` here at all" —
        # because it deletes that text from every file in the project.
        # Measured on a throwaway copy, deleting a map called `Route1` turned
        # `data/maps/Route10/map.bin` into `data/maps/0/map.bin`,
        # `Route11_Text` into `1_Text` and `EventScript_Route1_Sign` into
        # `EventScript__Sign` — all in OTHER maps' files.
        #
        # Nothing safe can be substituted here without knowing which textual
        # occurrences belong to this map, which is the C2 work. So the
        # deletion does what it can prove is correct, and REPORTS the
        # references it has deliberately left alone rather than guessing.
        # Always empty on a successful delete: anything unresolved refused the
        # operation above. Kept so a caller can state that plainly.
        self.last_delete_leftovers = []
        self._generate_headers()

    def _unresolved_references(self, map_folder: str,
                               map_id: Optional[str]) -> List[str]:
        """Files referring to this map that the deletion cannot fix.

        Excluded, because the operation genuinely handles them:
          * anything inside the map's own folder — it is being removed;
          * `map_groups.json` — the entry is removed from it;
          * occurrences of the MAP_ constant — repointed to MAP_UNDEFINED.

        What remains is a reference to a map that is about to stop existing,
        which is why it refuses rather than reports.
        """
        hits: List[str] = []
        if not map_folder:
            return hits
        own_dir = os.path.normcase(os.path.join(self.maps_dir, map_folder))
        groups_json = os.path.normcase(self.map_groups_json)
        for root, dirs, files in os.walk(self.root_dir):
            if '.git' in dirs:
                dirs.remove('.git')
            if os.path.normcase(root).startswith(own_dir):
                dirs[:] = []
                continue
            for name in files:
                path = os.path.join(root, name)
                if os.path.normcase(path) == groups_json:
                    continue
                try:
                    with open(path, 'r', encoding='utf-8',
                              errors='surrogateescape') as f:
                        text = f.read()
                except (OSError, UnicodeDecodeError):
                    continue
                if map_folder not in text:
                    continue
                # Occurrences that are only part of the MAP_ constant are
                # handled by the MAP_UNDEFINED repointing above.
                if map_id:
                    text = text.replace(map_id, '')
                    if map_folder not in text:
                        continue
                hits.append(os.path.relpath(path, self.root_dir))
                if len(hits) >= 200:
                    return hits
        return hits

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
        # RELATIVE paths, run from the project root — exactly as the Makefile
        # invokes mapjson. It writes its directory argument verbatim into the
        # generated `.include` lines, so an absolute Windows path produces
        # `.include "C:\GBA\…/events.inc"` and the assembler then fails with
        # `unexpected escape '\G' in include string`. The generated files are
        # committed-adjacent build inputs; they must stay portable.
        exe = self._ensure_mapjson()
        subprocess.run(
            [exe, 'groups', 'firered', 'data/maps/map_groups.json',
             'data/maps', 'include/constants'],
            check=True, capture_output=True, text=True, cwd=self.root_dir,
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
                map_jsons.append(
                    os.path.relpath(path, self.root_dir).replace(os.sep, '/'))
        subprocess.run(
            [exe, 'event_constants', 'firered', *map_jsons,
             'include/constants/map_event_ids.h'],
            check=True, capture_output=True, text=True, cwd=self.root_dir,
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
        return None
