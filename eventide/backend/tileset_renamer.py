"""
Tileset Renamer — rename secondary tilesets with repo-wide reference updates.

Ported from the standalone TilesetRenamer.py. All paths relative to root_dir.
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


class UnsafeTilesetRenameError(RuntimeError):
    """A tileset rename was refused because it would move files that belong
    to a DIFFERENT tileset, or would collide at the destination.

    C1 containment, added 2026-10-31 (finding 0e). `_rename_door_anim_files`
    matched filenames in `graphics/door_anims` by three independent SUBSTRING
    rules and called `os.rename` on each hit immediately. Renaming a tileset
    called `lab` therefore also moved the door graphics belonging to
    `cinnabar_lab` and `oaks_lab`.

    Unlike the text renames, this one MOVES FILES, so there is no
    partial-content recovery — which is why the whole move set is now planned
    and validated before the first rename.
    """

    def __init__(self, reasons):
        self.reasons = list(reasons)
        super().__init__(self.plain_english())

    def plain_english(self) -> str:
        body = "\n".join(f"  • {r}" for r in self.reasons[:10])
        more = ("\n  • …and %d more" % (len(self.reasons) - 10)
                if len(self.reasons) > 10 else "")
        return ("This tileset rename was stopped before anything was changed, "
                "because it would have moved files that do not belong to it:\n"
                + body + more +
                "\n\nNothing has been renamed. Rename the conflicting tileset "
                "first, or choose a name that is not contained in another "
                "tileset's name.")


class TilesetRenameRollbackError(RuntimeError):
    """A file move failed AND the rollback could not fully undo it.

    Carries the paths still in the wrong place so the user can be told
    exactly what to put back. Never raised while the project is consistent.
    """

    def __init__(self, original_error, unrecovered):
        self.original_error = original_error
        self.unrecovered = list(unrecovered)
        super().__init__(
            "A file could not be renamed (%s), and undoing the moves already "
            "made did not fully succeed. These files are NOT where the "
            "project expects them:\n%s"
            % (original_error,
               "\n".join("  • %s -> %s" % (a, b) for a, b in self.unrecovered)))


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
        # C1: validate the door-animation move set BEFORE the first write.
        # This used to run last, after the tileset folder had already been
        # renamed and six files rewritten — so refusing there would have left
        # the project half-renamed. "Check before the first write" is the
        # whole point of containment.
        _door_plan = self.plan_door_anim_renames(
            old_label, new_label, old_folder, new_folder)
        _reasons = self.validate_door_anim_plan(_door_plan, old_label, old_folder)
        if _reasons:
            raise UnsafeTilesetRenameError(_reasons)

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

    # ── door animations: plan, validate, then move with rollback ─────────
    # The three matching rules below are the ORIGINAL ones, unchanged — the
    # repair is that they now produce a PLAN which is checked before any file
    # moves, instead of calling os.rename on each hit as it is found.

    def _door_anim_new_name(self, name: str, old_label: str, new_label: str,
                            old_folder: str, new_folder: str) -> str:
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
        return new_name

    def plan_door_anim_renames(self, old_label: str, new_label: str,
                               old_folder: str, new_folder: str):
        """Every (src_name, dst_name) this rename would perform. No writes."""
        door_dir = os.path.join(self.root_dir, 'graphics', 'door_anims')
        if not os.path.isdir(door_dir):
            return []
        plan = []
        for name in sorted(os.listdir(door_dir)):
            new_name = self._door_anim_new_name(
                name, old_label, new_label, old_folder, new_folder)
            if new_name != name:
                plan.append((name, new_name))
        return plan

    def validate_door_anim_plan(self, plan, old_label: str, old_folder: str):
        """Reasons this plan is unsafe. Empty list means safe to run.

        Ownership is NOT inferred from a prefix alone. A file is treated as
        belonging to another tileset when that other tileset's folder name or
        short name appears in the filename AND contains our own name — i.e.
        the other tileset is the more specific explanation for the match.
        `cinnabar_lab_door.png` belongs to `cinnabar_lab`, not to `lab`.
        """
        door_dir = os.path.join(self.root_dir, 'graphics', 'door_anims')
        reasons = []

        try:
            others = [(lbl, fld) for lbl, fld in self.parse_tilesets()
                      if fld != old_folder or lbl != old_label]
        except Exception:
            # If the tileset list cannot be read we cannot establish
            # ownership, and guessing is what caused this defect. Refuse.
            return ["The list of tilesets could not be read, so ownership of "
                    "the door-animation files cannot be established."]

        short_old = _get_short_name(old_folder)
        for src, dst in plan:
            for lbl, fld in others:
                for other_name in {fld, _get_short_name(fld)}:
                    if not other_name or other_name not in src:
                        continue
                    # Only a CONFLICT when the other tileset's name is the
                    # more specific match — it contains ours.
                    if (old_folder in other_name and old_folder != other_name) \
                            or (short_old and short_old in other_name
                                and short_old != other_name):
                        reasons.append(
                            "%s looks like it belongs to the tileset '%s', "
                            "not '%s'." % (src, fld, old_folder))
                        break
                else:
                    continue
                break

        # Destination already present, or two sources landing on one name.
        seen = {}
        for src, dst in plan:
            if os.path.exists(os.path.join(door_dir, dst)) and dst not in \
                    {s for s, _d in plan}:
                reasons.append("%s would be overwritten by renaming %s."
                               % (dst, src))
            if dst in seen:
                reasons.append("%s and %s would both be renamed to %s."
                               % (seen[dst], src, dst))
            seen[dst] = src
        return reasons

    def _apply_door_anim_plan(self, plan):
        """Perform the planned moves, undoing them in reverse on failure.

        BEST-EFFORT rollback, deliberately not described as atomic: a
        filesystem can still fail during the undo. If it does, the paths left
        in the wrong place are reported rather than swallowed.
        """
        door_dir = os.path.join(self.root_dir, 'graphics', 'door_anims')
        done = []
        try:
            for src, dst in plan:
                src_p = os.path.join(door_dir, src)
                dst_p = os.path.join(door_dir, dst)
                if os.path.exists(dst_p):
                    raise FileExistsError(dst_p)
                os.rename(src_p, dst_p)
                done.append((src, dst))
        except Exception as exc:
            unrecovered = []
            for src, dst in reversed(done):
                try:
                    os.rename(os.path.join(door_dir, dst),
                              os.path.join(door_dir, src))
                except Exception:
                    unrecovered.append((dst, src))
            if unrecovered:
                raise TilesetRenameRollbackError(exc, unrecovered) from exc
            raise
        return len(done)

    def _rename_door_anim_files(self, old_label: str, new_label: str,
                                old_folder: str, new_folder: str):
        plan = self.plan_door_anim_renames(
            old_label, new_label, old_folder, new_folder)
        reasons = self.validate_door_anim_plan(plan, old_label, old_folder)
        if reasons:
            raise UnsafeTilesetRenameError(reasons)
        return self._apply_door_anim_plan(plan)
