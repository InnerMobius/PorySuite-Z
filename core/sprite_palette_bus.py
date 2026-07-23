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
* ``"battle_anim"`` — key ``"<ANIM_TAG_*>"`` (battle-animation sprite
  palette). 16 RGB entries.

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
import re
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import QObject, pyqtSignal


Color = Tuple[int, int, int]


# Category name constants — import these instead of hand-typing strings.
CAT_POKEMON = "pokemon"
CAT_TRAINER_PIC = "trainer_pic"
CAT_ITEM_ICON = "item_icon"
CAT_ICON_PALETTE = "icon_palette"
CAT_OVERWORLD = "overworld"
CAT_BATTLE_ANIM = "battle_anim"  # key = ANIM_TAG_* const. 16 RGB entries.
# key = FAMECHECKER_* person const. 16 RGB entries. Only the persons the
# engine draws with CUSTOM art have one; everyone else uses CAT_TRAINER_PIC,
# because the engine blits their trainer palette into the same OBJ slot.
CAT_FAME_CHECKER_PIC = "fame_checker_pic"

# A 4bpp GBA palette is 16 colours x 2 bytes. Anything else is a different
# kind of file (see `ensure_fame_checker_palette`).
_GBAPAL_16_COLORS = 16
_GBAPAL_16_BYTES = _GBAPAL_16_COLORS * 2


def fame_checker_palette_key(gbapal_path: str) -> str:
    """Stable cache key for a portrait palette: its normalised absolute path.

    Normalised, or the same file reached two different ways (relative vs
    absolute, different capitalisation on Windows) becomes two cache entries
    that can hold different colours.
    """
    return os.path.normcase(os.path.abspath(gbapal_path)) if gbapal_path else ""


def read_fame_checker_palette(gbapal_path: str) -> tuple:
    """(16 colours, "") — or ([], reason) explaining why it cannot be shown.

    Two situations both produce an empty palette and they are NOT the same, so
    the reason is returned rather than left for the caller to guess:

    * **No palette file at all.** The `.gbapal` is a BUILD ARTIFACT — a freshly
      cloned decomp ships only the `.png`, sometimes a JASC `.pal`, and no
      binary. Requiring the binary would lock every portrait on any project
      that has not been built. In that case the PNG's own colour table is the
      only source of truth there is, and the caller should say so.
    * **A file in a shape this editor cannot show.** In this very folder
      `bg.gbapal` is 64 bytes: `gFameCheckerBgPals[][16]`, TWO palettes, loaded
      with `2 * PLTT_SIZE_4BPP`. `decode_gbapal` truncates to 16 and
      `encode_gbapal` always writes 32, so treating it like the others would
      display half its colours and, on save, shrink the file — leaving the
      engine to read whatever follows as a second background palette.

    The size check must follow the precedence `read_palette_pair` ACTUALLY
    uses. Two holes this closes, both measured:

    * `decode_jasc` pads and truncates to exactly 16, so "check the returned
      length" can never fail for a text palette — a 64-byte binary alongside a
      32-colour `.pal` sailed straight through.
    * If the `.pal` is present but unparseable, `read_palette_pair` falls back
      to the **binary** — so skipping the size check because a sibling exists
      let the 64-byte file through with no warning at all.

    So: parse the `.pal`'s own declared count when it is readable; otherwise
    size-check the binary. Guarding one file while reading another is the same
    mismatch that caused the original `.gbapal` corruption bug.
    """
    if not gbapal_path:
        return [], "no palette file is associated with this sprite"
    from core.overworld_palette_io import (
        decode_jasc, pal_sibling_for_gbapal, read_palette_pair)
    pal_path = pal_sibling_for_gbapal(gbapal_path)
    try:
        jasc_ok = False
        if os.path.isfile(pal_path):
            with open(pal_path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            declared = re.search(r"JASC-PAL\s+\S+\s+(\d+)", text)
            if declared:
                n = int(declared.group(1))
                if n != _GBAPAL_16_COLORS:
                    return [], (
                        f"{os.path.basename(pal_path)} declares {n} colours, "
                        f"not {_GBAPAL_16_COLORS}, so it is not a single "
                        f"sprite palette this editor can show")
                jasc_ok = bool(decode_jasc(text))
        if not jasc_ok:
            # Either there is no sibling, or it could not be parsed — in both
            # cases the BINARY is what gets read, so it is what gets checked.
            if os.path.isfile(gbapal_path):
                size = os.path.getsize(gbapal_path)
                if size != _GBAPAL_16_BYTES:
                    return [], (
                        f"{os.path.basename(gbapal_path)} is {size} bytes — "
                        f"that is {size // 2} colours, not "
                        f"{_GBAPAL_16_COLORS}, so it is not a single sprite "
                        f"palette this editor can show")
            elif not os.path.isfile(pal_path):
                return [], ("no separate palette file — showing the colours "
                            "stored in the image itself")
    except OSError as exc:
        return [], f"could not read the palette file ({exc.strerror})"

    colors = read_palette_pair(gbapal_path) or []
    if len(colors) != _GBAPAL_16_COLORS:
        return [], (f"the palette file holds {len(colors)} colours, not "
                    f"{_GBAPAL_16_COLORS}")
    return colors, ""


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

    # ── Fame Checker custom portraits ────────────────────────────────

    def set_fame_checker_palette(self, gbapal_path: str,
                                 colors: List[Color]) -> None:
        """Push a Fame Checker portrait palette edit, keyed by its FILE."""
        self.set_palette(CAT_FAME_CHECKER_PIC,
                         fame_checker_palette_key(gbapal_path), colors)

    def get_fame_checker_palette(self,
                                 gbapal_path: str) -> Optional[List[Color]]:
        return self.get_palette(CAT_FAME_CHECKER_PIC,
                                fame_checker_palette_key(gbapal_path))

    def ensure_fame_checker_palette_with_reason(self, gbapal_path: str) -> tuple:
        """`(colors, reason)` — the reason is "" when the palette was read.

        The caller needs both, and the reason is produced by the same read that
        produces the colours. Returning only the colours meant every render of
        a portrait with no palette file paid two stats and a read to fetch the
        reason back again.
        """
        key = fame_checker_palette_key(gbapal_path)
        pal = self.get_palette(CAT_FAME_CHECKER_PIC, key)
        if pal is not None:
            return pal, ""
        colors, reason = read_fame_checker_palette(gbapal_path)
        if colors:
            self._cache[(CAT_FAME_CHECKER_PIC, key)] = list(colors)
        return list(colors), reason

    def ensure_fame_checker_palette(self, gbapal_path: str) -> List[Color]:
        """RAM-first, disk fallback for a Fame Checker portrait palette.

        **Keyed on the FILE, not the person.** The palette is a property of the
        file: two people could legitimately point at one palette, and keying by
        person would then hold two cache entries for the same bytes and let
        them drift — the precise failure this bus exists to prevent. Person
        constants are also renumbered by this very tool, and symbols get
        renamed; the normalised path is the only stable identity.

        See `read_fame_checker_palette` for what counts as readable and why.
        Returns a COPY. A successful read seeds the cache quietly (nothing
        changed — we hydrated).
        """
        key = fame_checker_palette_key(gbapal_path)
        pal = self.get_palette(CAT_FAME_CHECKER_PIC, key)
        if pal is not None:
            return pal
        colors, _reason = read_fame_checker_palette(gbapal_path)
        if colors:
            self._cache[(CAT_FAME_CHECKER_PIC, key)] = list(colors)
        return list(colors)

    # ── Battle-animation sprite palette ──────────────────────────────

    def set_battle_anim_palette(self, tag: str,
                                colors: List[Color]) -> None:
        """Push a battle-anim palette edit, keyed by its ``ANIM_TAG_*``."""
        self.set_palette(CAT_BATTLE_ANIM, tag, colors)

    def get_battle_anim_palette(self, tag: str) -> Optional[List[Color]]:
        return self.get_palette(CAT_BATTLE_ANIM, tag)

    def ensure_battle_anim_palette(self, tag: str,
                                   pal_path: str) -> List[Color]:
        """RAM-first, disk fallback for a battle-anim sprite palette.

        Battle-anim palettes build to compressed ``.gbapal.lz`` but the
        editable source is a ``.pal`` JASC sidecar (preferred) or the
        ``.gbapal`` binary -- ``read_palette_pair`` resolves whichever is
        current.  ``pal_path`` is the path the data layer resolved (may
        be a ``.pal`` or a ``.gbapal``; normalised to ``.gbapal`` here so
        the pair reader's sibling logic applies).

        Returns a COPY (safe to mutate); empty list when the sprite has
        no dedicated palette on disk (the caller then falls back to the
        PNG's own embedded colour table).  A successful disk read seeds
        the cache quietly (no signal -- nothing changed, we hydrated).
        """
        pal = self.get_palette(CAT_BATTLE_ANIM, tag)
        if pal is not None:
            return pal
        if not pal_path:
            return []
        gbapal = pal_path
        if gbapal.endswith(".pal"):
            gbapal = gbapal[: -len(".pal")] + ".gbapal"
        from core.overworld_palette_io import read_palette_pair
        colors = read_palette_pair(gbapal) or []
        if colors:
            self._cache[(CAT_BATTLE_ANIM, tag)] = list(colors)
        return list(colors)


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
