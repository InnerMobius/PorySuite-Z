"""Reference Pokémon sprites for the Battle Anims preview.

The Move Animations preview composites effect sprites over a battle scene,
but without the actual mons drawn it's hard to judge placement (Cut should
land on the target's body; Fly hides the mon and shows an orb; etc.).  This
module lists the project's species that have battle sprites and resolves
each one's front/back PNG + palette so the preview can drop a real mon into
the attacker / target slots.

Pure (no Qt): listing + path/palette resolution only.  The UI does the
QPixmap loading via the sanctioned ``sprite_render`` + palette-bus path.
"""

from __future__ import annotations

import os
from typing import List, Optional, Tuple

Color = Tuple[int, int, int]

_POKEMON_REL = os.path.join("graphics", "pokemon")


def mon_display_name(slug: str) -> str:
    """``charizard`` → ``Charizard``; ``nidoran_f`` → ``Nidoran F``."""
    return " ".join(p.capitalize() for p in slug.replace("-", "_").split("_") if p)


def list_mon_sprites(project_root: str) -> List[Tuple[str, str]]:
    """Return ``[(slug, dir_path)]`` for every species folder that has a
    ``front.png``, sorted by slug.  Empty when the tree is absent."""
    base = os.path.join(project_root, _POKEMON_REL)
    if not os.path.isdir(base):
        return []
    out: List[Tuple[str, str]] = []
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return []
    for name in names:
        d = os.path.join(base, name)
        if os.path.isfile(os.path.join(d, "front.png")):
            out.append((name, d))
    return out


def mon_sprite_path(dir_path: str, view: str) -> str:
    """Absolute path of a mon's ``front`` or ``back`` PNG (``""`` if absent)."""
    fname = "back.png" if view == "back" else "front.png"
    p = os.path.join(dir_path, fname)
    return p if os.path.isfile(p) else ""


def _read_gbapal(path: str) -> List[Color]:
    """Read a binary ``.gbapal`` (raw 15-bit BGR555, 2 bytes/colour) into
    16 RGB tuples.  Self-contained so it works in any context."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return []
    out: List[Color] = []
    for i in range(0, min(len(data), 32), 2):
        if i + 1 >= len(data):
            break
        v = data[i] | (data[i + 1] << 8)
        r = (v & 0x1F)
        g = (v >> 5) & 0x1F
        b = (v >> 10) & 0x1F
        # 5-bit → 8-bit (replicate high bits for a faithful scale).
        out.append(((r << 3) | (r >> 2), (g << 3) | (g >> 2), (b << 3) | (b >> 2)))
    return out


def mon_palette(project_root: str, slug: str, dir_path: str) -> List[Color]:
    """Resolve a mon's 16-colour palette.

    Prefers the palette bus (so unsaved edits from the Pokémon Graphics tab
    show here too), falling back to a self-contained read of the species'
    ``normal.gbapal``.  Never raises; returns ``[]`` on total miss.
    """
    const = "SPECIES_" + slug.upper()
    try:
        from core.sprite_palette_bus import get_bus
        pal = get_bus().ensure_pokemon_palette(project_root, const)
        if pal:
            return list(pal)
    except Exception:
        pass
    gbapal = os.path.join(dir_path, "normal.gbapal")
    if os.path.isfile(gbapal):
        pal = _read_gbapal(gbapal)
        if pal:
            return pal
    return []
