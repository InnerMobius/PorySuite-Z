"""Export / import a tileset animation as a portable ``.pszt`` package.

A ``.pszt`` is just a ZIP (renamed) holding the animation's frame PNGs plus a
small ``manifest.json`` describing the animation data — timing, tile count,
frame order, palette hint. It lets one animation (say the General tileset's
``sandwatersedge``) be dropped into any other primary or secondary tileset
without hand-copying folders and hand-wiring C.

Import is deliberately simple: the caller picks the destination tileset, a name,
and the START TILE (where the animated tiles live in that tileset — the one
tileset-specific thing the package can't know). Everything else — the frame
folder, the INCBIN block, the frame array, the queue function, the dispatch
registration, the init/callback, the header prototype — is done by
``core.tileset_anim_data.add_animation_to_tileset``.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import zipfile
from typing import Optional

from core.tileset_anim_data import (
    TileAnimation, add_animation_to_tileset, write_counter_max_to_source,
    resync_anim_frame_array)

PSZT_EXT = ".pszt"
_MANIFEST = "manifest.json"
_FORMAT = "pszt"
_VERSION = 1


def _snake(name: str) -> str:
    """A folder/symbol-safe snake_case slug from a user-typed name."""
    s = re.sub(r"[^0-9A-Za-z]+", "_", name.strip())
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", s)   # camelCase → snake
    return re.sub(r"_+", "_", s).strip("_").lower() or "animation"


# ── export ──────────────────────────────────────────────────────────────────

def export_animation(anim: TileAnimation, dest_path: str) -> str:
    """Write *anim* to a ``.pszt`` at *dest_path*. Returns the path written.

    Bundles the UNIQUE frame PNGs (frame_order, which may repeat them for
    ping-pong, is recorded in the manifest — not duplicated on disk, keeping the
    package small)."""
    if not dest_path.lower().endswith(PSZT_EXT):
        dest_path += PSZT_EXT

    manifest = {
        "format": _FORMAT,
        "version": _VERSION,
        "name": anim.name,
        "tile_count": int(anim.tile_count or 1),
        "divisor": int(anim.divisor or 1),
        "phase": int(anim.phase or 0),
        "counter_max": int(anim.counter_max or 0),
        "frame_order": list(anim.frame_order) if anim.frame_order else
        list(range(len(anim.frames))),
        "palette_hint": int(anim.palette_hint),
        "frame_count": len(anim.frames),
        "source": {"tileset": anim.tileset_name, "type": anim.tileset_type},
    }

    tmp = dest_path + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(_MANIFEST, json.dumps(manifest, indent=2))
        for fr in anim.frames:
            if fr.png_path and os.path.isfile(fr.png_path):
                z.write(fr.png_path, "frames/%d.png" % fr.index)
            else:
                raise FileNotFoundError(
                    "frame image missing for %s: %s" % (anim.name, fr.png_path))
    os.replace(tmp, dest_path)
    return dest_path


# ── import ──────────────────────────────────────────────────────────────────

def read_package(pszt_path: str) -> dict:
    """The manifest of a package, so a UI can show its defaults before import."""
    with zipfile.ZipFile(pszt_path) as z:
        return json.loads(z.read(_MANIFEST).decode("utf-8"))


def import_animation(project_dir: str, pszt_path: str, tileset_name: str,
                     tileset_type: str, anim_name: str,
                     start_tile: int) -> Optional[TileAnimation]:
    """Add the packaged animation to *tileset_name* at *start_tile*.

    *anim_name* is a user-facing name; it is slugged to snake_case for the
    folder/symbols. Returns the created :class:`TileAnimation`, or None.
    """
    with zipfile.ZipFile(pszt_path) as z:
        manifest = json.loads(z.read(_MANIFEST).decode("utf-8"))
        frame_names = sorted(
            (n for n in z.namelist()
             if n.startswith("frames/") and n.lower().endswith(".png")),
            key=lambda n: int(re.search(r"(\d+)\.png$", n).group(1)))
        if not frame_names:
            return None
        tmp = tempfile.mkdtemp(prefix="pszt_")
        frame_paths = []
        for n in frame_names:
            p = os.path.join(tmp, os.path.basename(n))
            with open(p, "wb") as fh:
                fh.write(z.read(n))
            frame_paths.append(p)

    tile_count = int(manifest.get("tile_count", 1)) or 1
    divisor = int(manifest.get("divisor", 1)) or 1
    slug = _snake(anim_name)

    anim = add_animation_to_tileset(
        project_dir, tileset_name, tileset_type, slug,
        int(start_tile), tile_count, divisor, frame_paths)
    if anim is None:
        return None

    # Faithfully restore the parts add_animation_to_tileset doesn't take:
    # the total cycle length, and a ping-pong frame order.
    cmax = int(manifest.get("counter_max", 0))
    if cmax and cmax != anim.counter_max:
        try:
            write_counter_max_to_source(project_dir, anim, cmax)
            anim.counter_max = cmax
        except Exception:
            pass
    order = manifest.get("frame_order")
    if isinstance(order, list) and order and \
            order != list(range(len(anim.frames))):
        try:
            anim.frame_order = [i for i in order if 0 <= i < len(anim.frames)]
            resync_anim_frame_array(project_dir, anim)
        except Exception:
            pass
    return anim
