"""
ConstantsManager — centralized loader and cache for all pokefirered project
constants used by the EVENTide script editor.

Call ``ConstantsManager.load(root_dir)`` once when a project is opened. Every
constant list is then accessible as a class attribute (e.g.
``ConstantsManager.ITEMS``). Widgets pull from these lists at creation time.

Each constant category is loaded from the corresponding header in
``include/constants/``. Constants that contain ``(`` (macros) are skipped.
"""

import re
from pathlib import Path


class ConstantsManager:
    """Singleton-style class that holds every constant list the editor needs."""

    # ── Core scripting constants ─────────────────────────────────────────────
    ITEMS: list[str] = []
    SPECIES: list[str] = []
    MOVES: list[str] = []
    FLAGS: list[str] = []
    VARS: list[str] = []

    # ── Audio ────────────────────────────────────────────────────────────────
    MUSIC: list[str] = []           # MUS_*
    SFX: list[str] = []             # SE_*

    # ── Maps & locations ─────────────────────────────────────────────────────
    MAP_CONSTANTS: list[str] = []   # MAP_* from map_groups.h
    MAP_NAMES: list[str] = []       # Folder names from data/maps/
    HEAL_LOCATIONS: list[str] = []  # HEAL_LOCATION_*

    # ── Trainers & opponents ─────────────────────────────────────────────────
    TRAINERS: list[str] = []        # TRAINER_* from opponents.h

    # ── Weather & screen ─────────────────────────────────────────────────────
    WEATHER: list[str] = []         # WEATHER_* from weather.h
    FADE_TYPES: list[str] = [       # Static — always the same 4
        'FADE_FROM_BLACK', 'FADE_TO_BLACK',
        'FADE_FROM_WHITE', 'FADE_TO_WHITE',
    ]

    # ── Movement ─────────────────────────────────────────────────────────────
    MOVEMENT_TYPES: list[str] = []  # MOVEMENT_TYPE_*

    # ── Object graphics ──────────────────────────────────────────────────────
    OBJECT_GFX: list[str] = []           # OBJ_EVENT_GFX_*
    OBJECT_GFX_PATHS: dict[str, Path] = {}  # const -> sprite PNG path

    # ── Decorations ──────────────────────────────────────────────────────────
    DECORATIONS: list[str] = []     # DECOR_*

    # ── Special functions ────────────────────────────────────────────────────
    SPECIALS: list[str] = []        # Function names from data/specials.inc

    # ── Trainer battle types (static) ────────────────────────────────────────
    TRAINER_BATTLE_TYPES: list[tuple[str, str]] = [
        ('0', 'Standard (single battle)'),
        ('1', 'Rival/Gym (continue script after)'),
        ('2', 'Rival/Gym (continue + no defeat speech)'),
        ('3', 'Double battle'),
        ('4', 'Rematch'),
        ('5', 'Double (no defeat speech)'),
    ]

    # ── Message box types (static) ───────────────────────────────────────────
    MSG_TYPES: list[str] = [
        'MSGBOX_DEFAULT',
        'MSGBOX_YESNO',
        'MSGBOX_AUTOCLOSE',
        'MSGBOX_NPC',
        'MSGBOX_SIGN',
    ]

    # ── Compare operators (static) ───────────────────────────────────────────
    COMPARE_OPS: list[tuple[str, str]] = [
        ('VAR_EQUAL', '== (equal)'),
        ('VAR_NOT_EQUAL', '!= (not equal)'),
        ('VAR_LESS_THAN', '< (less than)'),
        ('VAR_GREATER_THAN', '> (greater than)'),
        ('VAR_LESS_OR_EQUAL', '<= (less or equal)'),
        ('VAR_GREATER_OR_EQUAL', '>= (greater or equal)'),
    ]

    # ── Directions (static) ──────────────────────────────────────────────────
    DIRECTIONS: list[tuple[str, str]] = [
        ('DIR_DOWN', 'Down'),
        ('DIR_UP', 'Up'),
        ('DIR_LEFT', 'Left'),
        ('DIR_RIGHT', 'Right'),
    ]

    _loaded = False
    _root: Path | None = None

    # ════════════════════════════════════════════════════════════════════════
    # Public API
    # ════════════════════════════════════════════════════════════════════════

    @classmethod
    def load(cls, root_dir: str) -> None:
        """Load all constants from the given project root directory."""
        root = Path(root_dir)
        cls._root = root
        cls._loaded = True

        cls.ITEMS = cls._from_header(root, 'include/constants/items.h', 'ITEM_')
        cls.SPECIES = cls._from_header(root, 'include/constants/species.h', 'SPECIES_')
        cls.MOVES = cls._from_header(root, 'include/constants/moves.h', 'MOVE_')
        cls.MUSIC = cls._from_header(root, 'include/constants/songs.h', 'MUS_')
        cls.SFX = cls._from_header(root, 'include/constants/songs.h', 'SE_')
        cls.TRAINERS = cls._from_header(root, 'include/constants/opponents.h', 'TRAINER_')
        cls.MOVEMENT_TYPES = cls._from_header(
            root, 'include/constants/event_object_movement.h', 'MOVEMENT_TYPE_')
        cls.DECORATIONS = cls._from_header(root, 'include/constants/decorations.h', 'DECOR_')

        # Flags — filter out internal/system flags
        cls.FLAGS = cls._from_header_filtered(
            root, 'include/constants/flags.h', 'FLAG_',
            exclude=[r'^FLAG_TEMP_', r'^TEMP_FLAGS_', r'^FLAG_0x',
                     r'^FLAG_SYS_', r'^FLAG_SPECIAL_', r'^SPECIAL_FLAGS_',
                     r'^FLAGS_', r'_START$', r'_END$', r'_COUNT$'])

        # Vars — filter out internal vars
        cls.VARS = cls._from_header_filtered(
            root, 'include/constants/vars.h', 'VAR_',
            exclude=[r'^VAR_TEMP_', r'^TEMP_VARS_', r'^VAR_OBJ_GFX_ID_',
                     r'^VAR_0x', r'^VARS_', r'^SPECIAL_VARS_',
                     r'^VAR_SPECIAL_', r'_START$', r'_END$', r'_COUNT$'])

        # Weather constants from weather.h
        cls.WEATHER = cls._from_header(root, 'include/constants/weather.h', 'WEATHER_')

        # Heal locations from enum
        cls.HEAL_LOCATIONS = cls._load_enum_constants(
            root, 'include/constants/heal_locations.h', 'HEAL_LOCATION_')

        # Map constants from map_groups.h
        cls.MAP_CONSTANTS = cls._from_header(root, 'include/constants/map_groups.h', 'MAP_')

        # Map folder names from data/maps/
        cls.MAP_NAMES = cls._load_map_folder_names(root)

        # Object graphics
        cls.OBJECT_GFX, cls.OBJECT_GFX_PATHS = cls._load_object_gfx(root)

        # Special functions from data/specials.inc
        cls.SPECIALS = cls._load_specials(root)

    @classmethod
    def is_loaded(cls) -> bool:
        return cls._loaded

    @classmethod
    def refresh(cls) -> bool:
        """Re-read every constants file from disk using the cached root.

        Call this after another editor writes headers (item/flag/var renames,
        new trainers, etc.) so EVENTide dropdowns pick up the changes without
        requiring a project reload. No-op if ``load()`` has never been called.
        Returns True if the reload ran, False if there was no cached root.
        """
        if not cls._loaded or cls._root is None:
            return False
        cls.load(str(cls._root))
        return True

    @classmethod
    def root(cls) -> Path | None:
        return cls._root

    # ════════════════════════════════════════════════════════════════════════
    # Internal loaders
    # ════════════════════════════════════════════════════════════════════════

    @classmethod
    def _from_header(cls, root: Path, header_rel: str, prefix: str) -> list[str]:
        """Load #define constants with the given prefix from a header file."""
        header = root / header_rel
        results = []
        seen: set[str] = set()
        if not header.exists():
            return results
        with header.open(encoding='utf-8') as fh:
            for line in fh:
                line = line.strip()
                if not line.startswith(f'#define {prefix}'):
                    continue
                parts = line.split()
                if len(parts) < 2:
                    continue
                name = parts[1]
                if '(' in name or name in seen:
                    continue
                seen.add(name)
                results.append(name)
        return results

    @classmethod
    def _from_header_filtered(cls, root: Path, header_rel: str, prefix: str,
                              exclude: list[str]) -> list[str]:
        """Load constants, filtering out names matching any exclude pattern."""
        exclude_res = [re.compile(p) for p in exclude]
        all_consts = cls._from_header(root, header_rel, prefix)
        return [c for c in all_consts if not any(r.search(c) for r in exclude_res)]

    @classmethod
    def _load_enum_constants(cls, root: Path, header_rel: str,
                             prefix: str) -> list[str]:
        """Load enum values matching a prefix from a header file."""
        header = root / header_rel
        results = []
        if not header.exists():
            return results
        with header.open(encoding='utf-8') as fh:
            for line in fh:
                stripped = line.strip().rstrip(',')
                if stripped.startswith(prefix):
                    # Get just the identifier (before any = or comment)
                    name = stripped.split('=')[0].split('//')[0].strip()
                    if name:
                        results.append(name)
        return results

    @classmethod
    def _load_map_folder_names(cls, root: Path) -> list[str]:
        """Load map folder names from data/maps/."""
        maps_dir = root / 'data' / 'maps'
        if not maps_dir.is_dir():
            return []
        return sorted(
            name for name in maps_dir.iterdir()
            if name.is_dir() and (name / 'map.json').is_file()
        )

    @classmethod
    def _load_object_gfx(cls, root: Path) -> tuple[list[str], dict[str, Path]]:
        """Load OBJ_EVENT_GFX_* constants and find their sprite PNGs."""
        consts = cls._from_header(root, 'include/constants/event_objects.h',
                                  'OBJ_EVENT_GFX_')
        paths: dict[str, Path] = {}
        pics_root = root / 'graphics' / 'object_events' / 'pics'
        for const in consts:
            base = const[len('OBJ_EVENT_GFX_'):].lower()
            for sub in ['people', 'pokemon', 'misc']:
                path = pics_root / sub / f'{base}.png'
                if path.exists():
                    paths[const] = path
                    break
        return consts, paths

    @classmethod
    def _load_specials(cls, root: Path) -> list[str]:
        """Load special function names from data/specials.inc.

        Each line looks like ``def_special FuncName``.  We collect unique
        non-Null names and return them sorted.
        """
        specials_file = root / 'data' / 'specials.inc'
        if not specials_file.exists():
            return []
        results: list[str] = []
        seen: set[str] = set()
        with specials_file.open(encoding='utf-8') as fh:
            for line in fh:
                line = line.strip()
                if not line.startswith('def_special '):
                    continue
                name = line.split()[1].split('@')[0].strip()
                if name and name != 'NullFieldSpecial' and name not in seen:
                    seen.add(name)
                    results.append(name)
        return sorted(results)

    # ════════════════════════════════════════════════════════════════════════
    # Helper — human-readable constant name
    # ════════════════════════════════════════════════════════════════════════

    @classmethod
    def pretty(cls, const: str) -> str:
        """Return a human-readable version of a constant name.

        ``ITEM_POKE_BALL`` → ``Poke Ball``
        ``SPECIES_PIKACHU`` → ``Pikachu``
        ``MAP_PALLET_TOWN`` → ``Pallet Town``
        """
        # Strip common prefixes
        for prefix in ('ITEM_', 'SPECIES_', 'MOVE_', 'MAP_', 'TRAINER_',
                       'FLAG_', 'VAR_', 'WEATHER_', 'HEAL_LOCATION_',
                       'MOVEMENT_TYPE_', 'DECOR_', 'OBJ_EVENT_GFX_',
                       'MUS_', 'SE_'):
            if const.startswith(prefix):
                const = const[len(prefix):]
                break
        return const.replace('_', ' ').title()
