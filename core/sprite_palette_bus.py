"""core/sprite_palette_bus.py

Cross-tab palette cache + change-notification bus for sprite rendering.

**Problem it solves.** Editor tabs (Graphics, Trainer Graphics, Overworld
Graphics) hold their own in-memory `_palettes` dicts for unsaved edits.
Viewer tabs (Pokedex, Starters, Items, Trainers list, species tree,
info panel…) historically did a flat `QPixmap(path)` and never saw those
edits. Even once those viewers started routing through ``sprite_render``,
they still needed to know WHICH palette to reskin with — and that palette
might be mid-edit, not yet saved to the `.pal` file on disk.

The bus is the single source of truth for in-RAM palettes. Editor tabs
*push* on every mutation; viewer tabs *pull* (RAM first, disk fallback)
on every render and *subscribe* to its signal to invalidate caches when
something they care about changes.

Categories currently supported:

* ``"pokemon"`` — key ``"<SPECIES_CONST>:<kind>"`` where kind is
  ``"normal"`` or ``"shiny"``. 16 RGB entries.
* ``"trainer_pic"`` — key ``"<TRAINER_PIC_CONST>"``. 16 RGB entries.
* ``"item_icon"`` — key ``"<item_slug>"``. 16 RGB entries.
* ``"icon_palette"`` — key ``"<0|1|2>"`` (the three shared Pokemon icon
  palettes in ``graphics/pokemon/icon_palettes/``). 16 RGB entries.
* ``"overworld"`` — key ``"<palette_tag>"``. 16 RGB entries.

Signal payload: ``palette_changed.emit(category, key)`` — subscribers
should filter on category and (if caching) on key to avoid unnecessary
redraws. For example, the Pokedex tab listens for ``"pokemon"`` only and
invalidates the single affected species card.

No synchronous disk I/O happens inside the bus itself. Disk reads are
done by the helper ``ensure_*`` methods which use :mod:`ui.palette_utils`
``read_jasc_pal`` only as a fallback when the bus has no RAM value for
the requested key.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import QObject, pyqtSignal


Color = Tuple[int, int, int]


# Category name constants — import these instead of hand-typing strings.
CAT_POKEMON = "pokemon"
CAT_TRAINER_PIC = "trainer_pic"
CAT_ITEM_ICON = "item_icon"
CAT_ICON_PALETTE = "icon_palette"
CAT_OVERWORLD = "overworld"


def _pokemon_key(species_const: str, kind: str) -> str:
    return f"{species_const}:{kind}"


class SpritePaletteBus(QObject):
    """Singleton RAM cache + pyqtSignal notification bus."""

    # (category, key) — subscribers filter by category.
    palette_changed = pyqtSignal(str, str)

    def __init__(self) -> None:
        super().__init__()
        self._cache: Dict[Tuple[str, str], List[Color]] = {}

    # ── Generic set/get ──────────────────────────────────────────────

    def set_palette(self, category: str, key: str,
                    colors: List[Color]) -> None:
        """Store *colors* in RAM and emit :attr:`palette_changed`.

        Pass a COPY if the caller is likely to keep mutating the list;
        the bus stores the reference verbatim.
        """
        self._cache[(category, key)] = list(colors)
        self.palette_changed.emit(category, key)

    def get_palette(self, category: str, key: str) -> Optional[List[Color]]:
        """Return the RAM-cached palette, or ``None`` if not cached."""
        pal = self._cache.get((category, key))
        return list(pal) if pal is not None else None

    def forget(self, category: str, key: str) -> None:
        """Drop a cached entry (e.g. on species delete)."""
        self._cache.pop((category, key), None)

    def clear(self) -> None:
        """Drop the entire cache (e.g. on project close)."""
        self._cache.clear()

    # ── Pokemon-specific conveniences ────────────────────────────────

    def set_pokemon_palette(self, species_const: str, kind: str,
                            colors: List[Color]) -> None:
        self.set_palette(CAT_POKEMON, _pokemon_key(species_const, kind),
                         colors)

    def get_pokemon_palette(self, species_const: str,
                            kind: str = "normal") -> Optional[List[Color]]:
        return self.get_palette(CAT_POKEMON,
                                _pokemon_key(species_const, kind))

    def ensure_pokemon_palette(self, project_root: str,
                               species_const: str,
                               kind: str = "normal") -> List[Color]:
        """RAM-first, disk fallback. Always returns a list (empty on miss).

        The returned list is a COPY, safe to mutate without disturbing
        the cache. If the disk fallback loads a palette, the result is
        populated into the cache so future callers skip disk.
        """
        pal = self.get_pokemon_palette(species_const, kind)
        if pal is not None:
            return pal
        from ui.graphics_data import species_pal_paths
        from ui.palette_utils import read_jasc_pal
        npath, spath = species_pal_paths(project_root, species_const)
        path = spath if kind == "shiny" else npath
        if not os.path.isfile(path):
            return []
        colors = read_jasc_pal(path, 16) or []
        if colors:
            # Seed cache quietly (no signal — nothing changed, we just
            # hydrated from disk).
            self._cache[(CAT_POKEMON,
                         _pokemon_key(species_const, kind))] = list(colors)
        return list(colors)

    # ── Trainer pic ──────────────────────────────────────────────────

    def set_trainer_palette(self, pic_const: str,
                            colors: List[Color]) -> None:
        self.set_palette(CAT_TRAINER_PIC, pic_const, colors)

    def get_trainer_palette(self, pic_const: str) -> Optional[List[Color]]:
        return self.get_palette(CAT_TRAINER_PIC, pic_const)

    def ensure_trainer_palette_from_png(self, png_path: str,
                                        pic_const: str = "") -> List[Color]:
        """Resolve a trainer pic palette from its PNG path.

        If *pic_const* is supplied, cache under that key for future
        lookups by const. Otherwise we cache under the PNG path itself,
        which is less convenient but still keyed consistently.
        """
        key = pic_const or png_path
        pal = self.get_palette(CAT_TRAINER_PIC, key)
        if pal is not None:
            return pal
        from ui.graphics_data import trainer_pal_path_from_png
        from ui.palette_utils import read_jasc_pal
        pal_path = trainer_pal_path_from_png(png_path)
        if not os.path.isfile(pal_path):
            return []
        colors = read_jasc_pal(pal_path, 16) or []
        if colors:
            self._cache[(CAT_TRAINER_PIC, key)] = list(colors)
        return list(colors)

    # ── Item icon ────────────────────────────────────────────────────

    def set_item_palette(self, item_slug: str,
                         colors: List[Color]) -> None:
        self.set_palette(CAT_ITEM_ICON, item_slug, colors)

    def get_item_palette(self, item_slug: str) -> Optional[List[Color]]:
        return self.get_palette(CAT_ITEM_ICON, item_slug)

    def ensure_item_palette(self, project_root: str,
                            item_slug: str) -> List[Color]:
        pal = self.get_item_palette(item_slug)
        if pal is not None:
            return pal
        from ui.graphics_data import item_icon_paths
        from ui.palette_utils import read_jasc_pal
        _png, pal_path = item_icon_paths(project_root, item_slug)
        if not os.path.isfile(pal_path):
            return []
        colors = read_jasc_pal(pal_path, 16) or []
        if colors:
            self._cache[(CAT_ITEM_ICON, item_slug)] = list(colors)
        return list(colors)

    def ensure_item_palette_from_png(self, png_path: str,
                                     item_slug: str = "") -> List[Color]:
        """Resolve an item icon palette from its PNG path.

        If *item_slug* is supplied, cache under that key. Otherwise
        fall back to caching by PNG path.
        """
        key = item_slug or png_path
        pal = self.get_palette(CAT_ITEM_ICON, key)
        if pal is not None:
            return pal
        from ui.graphics_data import item_pal_path_from_png
        from ui.palette_utils import read_jasc_pal
        pal_path = item_pal_path_from_png(png_path)
        if not os.path.isfile(pal_path):
            return []
        colors = read_jasc_pal(pal_path, 16) or []
        if colors:
            self._cache[(CAT_ITEM_ICON, key)] = list(colors)
        return list(colors)

    # ── Icon palette (shared 0/1/2) ──────────────────────────────────

    def set_icon_palette(self, idx: int, colors: List[Color]) -> None:
        self.set_palette(CAT_ICON_PALETTE, str(int(idx)), colors)

    def get_icon_palette(self, idx: int) -> Optional[List[Color]]:
        return self.get_palette(CAT_ICON_PALETTE, str(int(idx)))

    def ensure_icon_palette(self, project_root: str,
                            idx: int) -> List[Color]:
        idx = max(0, min(2, int(idx)))
        pal = self.get_icon_palette(idx)
        if pal is not None:
            return pal
        from ui.graphics_data import icon_palette_pal_path
        from ui.palette_utils import read_jasc_pal
        pal_path = icon_palette_pal_path(project_root, idx)
        if not os.path.isfile(pal_path):
            return []
        colors = read_jasc_pal(pal_path, 16) or []
        if colors:
            self._cache[(CAT_ICON_PALETTE, str(idx))] = list(colors)
        return list(colors)

    # ── Overworld palette (shared pools) ─────────────────────────────

    def set_overworld_palette(self, tag: str,
                              colors: List[Color]) -> None:
        self.set_palette(CAT_OVERWORLD, tag, colors)

    def get_overworld_palette(self, tag: str) -> Optional[List[Color]]:
        return self.get_palette(CAT_OVERWORLD, tag)


# ── Module-level singleton ──────────────────────────────────────────

_BUS_SINGLETON: Optional[SpritePaletteBus] = None


def get_bus() -> SpritePaletteBus:
    """Return the process-wide SpritePaletteBus singleton.

    Lazy-constructed on first call so importing this module from a test
    or headless script doesn't require a running QApplication. (QObject
    construction itself is safe without a QApplication; only signal
    *emission* needs an event loop to deliver to subscribers.)
    """
    global _BUS_SINGLETON
    if _BUS_SINGLETON is None:
        _BUS_SINGLETON = SpritePaletteBus()
    return _BUS_SINGLETON
