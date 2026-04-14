"""
Event Editor tab — visual script editor for pokefirered maps.

Loads ALL event types from map.json — object_events (NPCs),
coord_events (triggers), bg_events (signs/hidden items), and
map_scripts (on-transition/on-frame scripts from scripts.inc).
Provides per-command editing with specialized widgets, modeled after
RPG Maker XP's event editor: RMXP-style text list display,
double-click edit dialogs, 3-page command selector, searchable
constant pickers, and specialized parameter dialogs for every
command type.

Architecture:
  - ConstantsManager provides all dropdown data (items, species, etc.)
  - ConstantPicker / MapPicker are reusable searchable combo widgets
  - Each command type has its own _CommandWidget subclass with to_tuple()
  - Commands display as RMXP-style @> text list in a QListWidget
  - Double-click opens a _CommandEditDialog wrapping the _CommandWidget
  - Sub-labels reachable via goto/call become tabs (like RMXP pages)
  - Save writes back scripts.inc, text.inc, and map.json
"""

import os
import json
import re
from pathlib import Path
from collections import OrderedDict

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap, QColor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QScrollArea,
    QTreeWidget, QTreeWidgetItem, QComboBox, QPushButton,
    QGroupBox, QLabel, QLineEdit, QSpinBox, QTabWidget,
    QCheckBox, QPlainTextEdit, QSlider, QCompleter,
    QDialog, QDialogButtonBox, QFormLayout, QFrame,
    QMessageBox, QInputDialog,
    QListWidget, QListWidgetItem, QAbstractItemView,
    QMenu, QStackedWidget,
)

from eventide.backend.constants_manager import ConstantsManager
from eventide.ui.widgets import ConstantPicker, MapPicker, SpritePreview
from ui.custom_widgets.scroll_guard import install_scroll_guard_recursive

# ── Sound Editor integration callbacks (set by unified_mainwindow) ──────────
# These let the playbgm/playse/playfanfare widgets talk to the Sound Editor
# without threading parent refs through dozens of constructors.
_preview_song_cb = None      # Callable[[str], bool] — constant -> play it
_open_in_sound_editor_cb = None  # Callable[[str], None] — constant -> switch page
_stop_preview_cb = None      # Callable[[], None] — stop any playing preview


# ═════════════════════════════════════════════════════════════════════════════
# Base command widget
# ═════════════════════════════════════════════════════════════════════════════

# Category colors for command header bars (RPG Maker-style visual grouping)
_CATEGORY_COLORS = {
    'dialogue':  '#2980b9',   # Blue — messages, text
    'flag_var':  '#8e44ad',   # Purple — flags, variables, conditions
    'flow':      '#c0392b',   # Red — goto, call, end, return, conditionals
    'movement':  '#8b2252',   # Maroon — warps, NPC control, movement (RMXP style)
    'sound':     '#d35400',   # Orange — audio, music, fanfares
    'screen':    '#16a085',   # Teal — weather, fade, flash, effects
    'battle':    '#e74c3c',   # Bright red — trainer/wild battles
    'pokemon':   '#f39c12',   # Gold — give mon, moves, party checks
    'item':      '#2ecc71',   # Lime — items, money, coins
    'system':    '#7f8c8d',   # Gray — misc, respawn, buffers
    'generic':   '#95a5a6',   # Light gray — unknown/fallback
}

# ── Module-level context for widgets ────────────────────────────────────────
# Populated by EventEditorTab when a map is loaded, so widgets can offer
# dropdowns of objects/labels from the current map without dependency injection.
_SCRIPT_LABELS: list[str] = []     # All script labels in the current file
_OBJECT_LOCAL_IDS: list[str] = []  # Local IDs from current map's object_events
_MOVEMENT_LABELS: list[str] = []   # Movement labels from the current script file
_ALL_SCRIPTS: dict[str, list] = {} # label → [cmd_tuples] for movement step lookup
_CURRENT_PAGES: list[dict] = []    # Current object's pages — for setflag→page linking

# ── Display name resolution ─────────────────────────────────────────────────
# Populated by EventEditorTab.set_display_names() when the unified window
# passes in the shared data layer.  Used by _stringize() and color coding.
#
# Each dict maps a raw constant (e.g. "TRAINER_BROCK") to its display name
# (e.g. "Gym Leader Brock").  If a constant isn't in any dict, _stringize
# falls back to auto-generated names (strip prefix, title-case).
_DISPLAY_NAMES: dict[str, str] = {}   # Combined lookup: any constant → display name
_DISPLAY_TYPES: dict[str, str] = {}   # constant → type tag ('trainer','item','species','move','flag','var')
_LABEL_MANAGER_LABELS: dict[str, str] = {}  # flag/var constant → user-set label from Label Manager


def _set_display_data(species: list[tuple], items: list[tuple],
                      moves: list[tuple], trainers: dict,
                      labels: dict[str, dict] | None = None):
    """Populate module-level display name dicts from shared data.

    Called by EventEditorTab when project data is available.
    Each list is [(constant, display_name), ...].
    trainers is {constant: {trainerClass, trainerName, ...}}.
    labels is {constant: {label, notes}} from the Label Manager.
    """
    global _DISPLAY_NAMES, _DISPLAY_TYPES, _LABEL_MANAGER_LABELS
    _DISPLAY_NAMES = {}
    _DISPLAY_TYPES = {}
    _LABEL_MANAGER_LABELS = {}

    for const, name in species:
        if const != "SPECIES_NONE":
            _DISPLAY_NAMES[const] = name
            _DISPLAY_TYPES[const] = 'species'

    for const, name in items:
        if const != "ITEM_NONE":
            _DISPLAY_NAMES[const] = name
            _DISPLAY_TYPES[const] = 'item'

    for const, name in moves:
        if const != "MOVE_NONE":
            _DISPLAY_NAMES[const] = name
            _DISPLAY_TYPES[const] = 'move'

    # Trainers: build "Class Name" display string
    for const, data in trainers.items():
        cls_name = ""
        tr_name = ""
        if isinstance(data, dict):
            # Extract trainer name from _("NAME") format
            raw_name = data.get('trainerName', '')
            m = re.search(r'_\(\s*"([^"]*)"\s*\)', raw_name)
            if m:
                tr_name = m.group(1).title()
            else:
                tr_name = raw_name.title() if raw_name else ''
            # Extract class display name
            cls_name = data.get('trainerClassName', '')
            if not cls_name:
                cls_name = data.get('trainerClass', '')
                if cls_name:
                    # Strip TRAINER_CLASS_ prefix and title-case
                    cls_name = cls_name.replace('TRAINER_CLASS_', '').replace('_', ' ').title()
        if cls_name and tr_name:
            _DISPLAY_NAMES[const] = f"{cls_name} {tr_name}"
        elif tr_name:
            _DISPLAY_NAMES[const] = tr_name
        elif cls_name:
            _DISPLAY_NAMES[const] = cls_name
        else:
            # Fallback: strip TRAINER_ prefix and title-case
            _DISPLAY_NAMES[const] = const.replace('TRAINER_', '').replace('_', ' ').title()
        _DISPLAY_TYPES[const] = 'trainer'

    # Label Manager labels for flags/vars
    if labels:
        for const, entry in labels.items():
            label_text = entry.get('label', '').strip()
            if label_text:
                _LABEL_MANAGER_LABELS[const] = label_text

    # Register flag/var types (even without labels, so color coding works)
    for flag in ConstantsManager.FLAGS:
        _DISPLAY_TYPES[flag] = 'flag'
    for var in ConstantsManager.VARS:
        _DISPLAY_TYPES[var] = 'var'
    # Also register any labeled constants that might not be in the filtered lists
    if labels:
        for const in labels:
            if const.startswith('FLAG_') and const not in _DISPLAY_TYPES:
                _DISPLAY_TYPES[const] = 'flag'
            elif const.startswith('VAR_') and const not in _DISPLAY_TYPES:
                _DISPLAY_TYPES[const] = 'var'


def _resolve_name(const: str) -> str:
    """Get the best display name for a constant.

    Priority:
    1. Label Manager label (flags/vars only)
    2. Shared data display name (trainers/items/species/moves)
    3. Auto-generated from constant name (strip prefix, title-case)
    """
    # Label Manager labels take priority for flags/vars
    if const in _LABEL_MANAGER_LABELS:
        return _LABEL_MANAGER_LABELS[const]
    # Rich display name from project data
    if const in _DISPLAY_NAMES:
        return _DISPLAY_NAMES[const]
    # Auto-generate: strip known prefixes and title-case
    for prefix in ('FLAG_', 'VAR_', 'TRAINER_', 'ITEM_', 'SPECIES_', 'MOVE_',
                   'SE_', 'MUS_', 'WEATHER_', 'OBJ_EVENT_GFX_'):
        if const.startswith(prefix):
            return const[len(prefix):].replace('_', ' ').title()
    return const


def _const_type(const: str) -> str:
    """Return the type tag for a constant ('trainer','item','flag', etc.).

    Falls back to prefix-based detection so that FLAG_TEMP_*, VAR_TEMP_*,
    and other constants excluded from ConstantsManager still get colored.
    """
    t = _DISPLAY_TYPES.get(const, '')
    if t:
        return t
    # Prefix fallback — everything FLAG_ is a flag, everything VAR_ is a var
    if const.startswith('FLAG_'):
        return 'flag'
    if const.startswith('VAR_'):
        return 'var'
    if const.startswith('TRAINER_'):
        return 'trainer'
    if const.startswith('ITEM_'):
        return 'item'
    if const.startswith('SPECIES_'):
        return 'species'
    if const.startswith('MOVE_'):
        return 'move'
    return ''


# ── Color coding for command list items ─────────────────────────────────────
# Default colors per constant type.  Overridable from Settings > Event Colors.
_TYPE_DISPLAY_COLORS: dict[str, str] = {
    'flag':    '#2ecc71',   # Green
    'var':     '#3498db',   # Blue
    'trainer': '#e74c3c',   # Red
    'item':    '#f39c12',   # Gold/amber
    'species': '#9b59b6',   # Purple
    'move':    '#1abc9c',   # Teal
}


_EVENT_TOOLTIPS_ENABLED: bool = True


def _load_color_settings():
    """Load custom colors and tooltip preference from settings.ini."""
    global _EVENT_TOOLTIPS_ENABLED
    try:
        from app_info import get_settings_path
        from PyQt6.QtCore import QSettings
        settings = QSettings(get_settings_path(), QSettings.Format.IniFormat)

        # Tooltip toggle
        _EVENT_TOOLTIPS_ENABLED = bool(
            settings.value("editor/event_tooltips", True, type=bool))

        # Constant type colors
        for key in list(_TYPE_DISPLAY_COLORS.keys()):
            saved = settings.value(f"event_colors/{key}", '')
            if saved:
                _TYPE_DISPLAY_COLORS[key] = saved

        # Category colors
        cat_keys = {
            'dialogue': 'dialogue', 'flag_var': 'flag_var',
            'flow': 'flow', 'movement': 'movement',
            'sound': 'sound', 'screen': 'screen',
            'battle': 'battle', 'pokemon': 'pokemon',
            'item_cmd': 'item', 'system': 'system',
        }
        for settings_key, cat_key in cat_keys.items():
            saved = settings.value(f"event_cat_colors/{settings_key}", '')
            if saved and cat_key in _CATEGORY_COLORS:
                _CATEGORY_COLORS[cat_key] = saved
    except Exception:
        pass  # Settings file may not exist yet


def _tt(tip: str) -> str:
    """Return *tip* if event tooltips are enabled, else empty string.

    At construction time this gates whether a tooltip is set at all.
    After construction, ``reload_tooltip_setting()`` on the editor tab
    can toggle tooltips on/off without restarting.
    """
    return tip if _EVENT_TOOLTIPS_ENABLED else ''


def _apply_tooltip_visibility(root_widget, enabled: bool):
    """Walk *root_widget*'s children, hiding or restoring help tooltips.

    Tooltips that were set via ``_tt()`` are toggled.  Position-override
    tooltips (set dynamically at runtime with no ``_orig_tip`` property)
    are left untouched.
    """
    from PyQt6.QtWidgets import QWidget  # local to avoid circular at module level
    for child in root_widget.findChildren(QWidget):
        tip = child.toolTip()
        stored = child.property('_orig_tip')
        if not enabled:
            # Stash the current tip and clear it
            if tip and stored is None:
                child.setProperty('_orig_tip', tip)
                child.setToolTip('')
        else:
            # Restore previously stashed tip
            if stored:
                child.setToolTip(stored)
                child.setProperty('_orig_tip', None)


# Load on import
_load_color_settings()


def _primary_const_in_cmd(cmd_tuple: tuple) -> str:
    """Extract the primary constant referenced by a command tuple.

    Returns the raw constant name (e.g. 'FLAG_GOT_STARTER', 'TRAINER_BROCK')
    or '' if no recognizable constant is found.
    """
    if not cmd_tuple or len(cmd_tuple) < 2:
        return ''
    cmd = cmd_tuple[0]
    arg1 = cmd_tuple[1] if len(cmd_tuple) > 1 else ''

    # Flags
    if cmd in ('setflag', 'clearflag', 'checkflag'):
        return str(arg1)
    # Vars
    if cmd in ('setvar', 'addvar', 'subvar', 'compare_var_to_value'):
        return str(arg1)
    # Conditionals — flag or var in arg1
    if cmd in ('goto_if_set', 'goto_if_unset', 'call_if_set', 'call_if_unset'):
        return str(arg1)
    if cmd in ('goto_if_eq', 'goto_if_ne', 'goto_if_lt', 'goto_if_ge',
               'goto_if_le', 'goto_if_gt', 'call_if_eq', 'call_if_ne',
               'call_if_lt', 'call_if_ge'):
        return str(arg1)
    # Trainer battles — trainer const is first part of comma-separated args
    if cmd in ('trainerbattle', 'trainerbattle_single', 'trainerbattle_no_intro',
               'trainerbattle_earlyrival', 'trainerbattle_double',
               'trainerbattle_rematch', 'trainerbattle_rematch_double'):
        parts = [p.strip() for p in str(arg1).split(',')]
        return parts[0] if parts else ''
    # Wild battle — species
    if cmd == 'wildbattle':
        return str(arg1)
    # Give pokemon — species in first comma part
    if cmd == 'givemon':
        parts = [p.strip() for p in str(arg1).split(',')]
        return parts[0] if parts else ''
    # Items
    if cmd in ('finditem', 'additem', 'removeitem', 'checkitem', 'checkitemspace'):
        parts = [p.strip() for p in str(arg1).split(',')]
        return parts[0] if parts else ''
    # Play cry — species
    if cmd == 'playmoncry':
        parts = [p.strip() for p in str(arg1).split(',')]
        return parts[0] if parts else ''
    return ''


def _apply_cmd_color(item: QListWidgetItem, cmd_tuple: tuple):
    """Apply color coding to a list item based on its command type.

    RMXP-style: specific functional commands get their category color,
    plain structural stuff (text, choices, branches) stays default.

    Colored categories (each customisable in Settings → Event Colors):
      - Flow navigation (goto, call) + conditionals
      - Movement block (Set Move Route + steps) — maroon
      - Flag/switch control (setflag, setvar, etc.)
      - Item commands (additem, finditem, pokemart, money, coins)
      - Sound (playbgm, playse, fanfares)
      - Screen effects (fadescreen, weather, delay)
      - Battles (trainerbattle, wildbattle)
      - Pokemon (givemon, giveegg, party checks)
      - Label markers (structural separators)
    Plain (no color): dialogue/text, choices, end/return, lock/release,
      system/buffer commands, and anything else.
    """
    if not cmd_tuple:
        return
    cmd = cmd_tuple[0] if cmd_tuple else ''

    # Label markers — bold orange separator for inline sub-labels
    if cmd == '_label_marker':
        item.setForeground(QColor('#f39c12'))
        return

    # Flow navigation (RMXP: Jump to Label, Label)
    if cmd in ('goto', 'call'):
        item.setForeground(QColor(_CATEGORY_COLORS.get('flow', '#c0392b')))
        return
    if cmd.startswith(('goto_if_', 'call_if_')):
        item.setForeground(QColor('#e8a838'))
        return

    # Movement block — maroon (RMXP: Set Move Route)
    if cmd in ('applymovement', 'applymovementat'):
        item.setForeground(QColor(_CATEGORY_COLORS.get('movement', '#8b2252')))
        return

    # Flag/switch control (RMXP: Control Switches / Control Variables)
    if cmd in ('setflag', 'clearflag', 'checkflag', 'setvar', 'addvar',
               'subvar', 'copyvar', 'setworldmapflag',
               'compare_var_to_value', 'compare_var_to_var'):
        item.setForeground(QColor(_CATEGORY_COLORS.get('flag_var', '#8e44ad')))
        return

    # Item commands (RMXP: Change Items)
    if cmd in ('additem', 'removeitem', 'checkitem', 'checkitemspace',
               'finditem', 'giveitem', 'givepcitem',
               'addmoney', 'removemoney', 'checkmoney',
               'addcoins', 'removecoins', 'checkcoins', 'showcoins', 'hidecoins',
               'pokemart', 'pokemartdecor'):
        item.setForeground(QColor(_CATEGORY_COLORS.get('item', '#2ecc71')))
        return

    # Sound — audio, music, fanfares
    if cmd in ('playse', 'waitse', 'playfanfare', 'waitfanfare', 'playbgm',
               'fadeoutbgm', 'fadeinbgm', 'fadedefaultbgm', 'savebgm',
               'playmoncry', 'waitmoncry', 'stopbgm'):
        item.setForeground(QColor(_CATEGORY_COLORS.get('sound', '#d35400')))
        return

    # Screen effects — visual, weather, timing
    if cmd in ('fadescreen', 'fadescreenspeed', 'setflashlevel', 'animateflash',
               'setweather', 'doweather', 'resetweather', 'delay',
               'dofieldeffect', 'waitfieldeffect',
               'setanimation', 'createsprite', 'waitstate',
               'showmoneybox', 'hidemoneybox', 'updatemoneybox'):
        item.setForeground(QColor(_CATEGORY_COLORS.get('screen', '#16a085')))
        return

    # Battles — trainer and wild
    if cmd in ('trainerbattle', 'trainerbattle_single', 'trainerbattle_no_intro',
               'trainerbattle_earlyrival', 'trainerbattle_double',
               'trainerbattle_rematch', 'trainerbattle_rematch_double',
               'wildbattle', 'setwildbattle', 'dowildbattle',
               'dotrainerbattle'):
        item.setForeground(QColor(_CATEGORY_COLORS.get('battle', '#e74c3c')))
        return

    # Pokemon — party, species, moves
    if cmd in ('givemon', 'giveegg', 'setmonmove', 'checkpartymove',
               'getpartysize', 'checkplayergender', 'showmonpic', 'hidemonpic',
               'bufferspeciesname', 'healplayerteam', 'checkattack',
               'bufferpokemon', 'bufferpartymon'):
        item.setForeground(QColor(_CATEGORY_COLORS.get('pokemon', '#f39c12')))
        return

    # Color by primary constant type (FLAG_*, VAR_*, TRAINER_*, etc.)
    # Catches commands not in a category above but referencing a known constant
    primary = _primary_const_in_cmd(cmd_tuple)
    if primary:
        ctype = _const_type(primary)
        color = _TYPE_DISPLAY_COLORS.get(ctype, '')
        if color:
            item.setForeground(QColor(color))


def _category_for_cmd(cmd: str) -> str:
    """Return the category key for a command name."""
    if cmd in ('message', 'msgbox', 'yesnobox', 'multichoice', 'multichoicedefault',
               'multichoicegrid', 'waitmessage', 'closemessage', 'waitbuttonpress',
               'textcolor', 'signmsg', 'normalmsg'):
        return 'dialogue'
    if cmd in ('setflag', 'clearflag', 'checkflag', 'setvar', 'addvar', 'subvar',
               'copyvar', 'compare_var_to_value', 'compare_var_to_var',
               'setworldmapflag'):
        return 'flag_var'
    if cmd in ('goto', 'call', 'end', 'return', 'special', 'specialvar',
               'callstd', 'gotostd',
               'goto_if_eq', 'goto_if_ne', 'goto_if_lt', 'goto_if_ge',
               'goto_if_le', 'goto_if_gt', 'goto_if_set', 'goto_if_unset',
               'call_if_eq', 'call_if_ne', 'call_if_lt', 'call_if_ge',
               'call_if_set', 'call_if_unset'):
        return 'flow'
    if cmd in ('warp', 'warpsilent', 'warpdoor', 'warphole', 'warpteleport',
               'warpflymap', 'setdivewarp', 'setholewarp',
               'applymovement', 'applymovementat', 'waitmovement',
               'removeobject', 'addobject',
               'showobjectat', 'hideobjectat', 'faceplayer', 'turnobject',
               'setobjectxy', 'setobjectxyperm', 'setobjectmovementtype',
               'copyobjectxytoperm',
               'lock', 'lockall', 'release', 'releaseall',
               'opendoor', 'closedoor', 'waitdooranim'):
        return 'movement'
    if cmd in ('playse', 'waitse', 'playfanfare', 'waitfanfare', 'playbgm',
               'fadeoutbgm', 'fadeinbgm', 'fadedefaultbgm', 'savebgm',
               'playmoncry', 'waitmoncry', 'stopbgm'):
        return 'sound'
    if cmd in ('fadescreen', 'fadescreenspeed', 'setflashlevel', 'animateflash',
               'setweather', 'doweather', 'resetweather', 'delay',
               'dofieldeffect', 'waitfieldeffect',
               'setanimation', 'createsprite', 'waitstate',
               'showmoneybox', 'hidemoneybox', 'updatemoneybox'):
        return 'screen'
    if cmd in ('trainerbattle', 'trainerbattle_single', 'trainerbattle_no_intro',
               'trainerbattle_earlyrival', 'trainerbattle_double',
               'trainerbattle_rematch', 'trainerbattle_rematch_double',
               'wildbattle', 'setwildbattle', 'dowildbattle',
               'dotrainerbattle'):
        return 'battle'
    if cmd in ('givemon', 'giveegg', 'setmonmove', 'checkpartymove',
               'getpartysize', 'checkplayergender', 'showmonpic', 'hidemonpic',
               'bufferspeciesname', 'healplayerteam', 'checkattack',
               'bufferpokemon', 'bufferpartymon'):
        return 'pokemon'
    if cmd in ('additem', 'removeitem', 'checkitem', 'checkitemspace',
               'finditem', 'giveitem', 'givepcitem',
               'addmoney', 'removemoney', 'checkmoney',
               'addcoins', 'removecoins', 'checkcoins', 'showcoins', 'hidecoins',
               'bufferitemname', 'adddecoration', 'removedecoration',
               'pokemart', 'pokemartdecor'):
        return 'item'
    if cmd in ('setrespawn', 'buffermovename', 'buffernumberstring', 'bufferstring',
               'famechecker', 'map_script', 'map_script_2', '.equ', '.byte', '.2byte'):
        return 'system'
    return 'generic'


# ═════════════════════════════════════════════════════════════════════════════
# Stringizer — convert command tuples to RMXP-style display text
# ═════════════════════════════════════════════════════════════════════════════

# Friendly names for commands (used by stringizer and display)
_FRIENDLY_NAMES: dict[str, str] = {
    'message': 'Text', 'yesnobox': 'Show Choices: Yes/No',
    'multichoice': 'Multi-Choice', 'waitmessage': 'Wait for Message',
    'closemessage': 'Close Message', 'waitbuttonpress': 'Wait for Button',
    'setflag': 'Set Flag', 'clearflag': 'Clear Flag', 'checkflag': 'Check Flag',
    'setvar': 'Set Variable', 'addvar': 'Add to Variable',
    'subvar': 'Sub from Variable', 'copyvar': 'Copy Variable',
    'compare_var_to_value': 'Compare Var to Value',
    'compare_var_to_var': 'Compare Var to Var',
    'goto': 'Jump to Label', 'call': 'Call Script', 'end': 'End',
    'return': 'Return', 'special': 'Special', 'specialvar': 'Special Var',
    'callstd': 'Call Std', 'gotostd': 'Goto Std',
    'warp': 'Warp', 'warpsilent': 'Warp (Silent)',
    'warpdoor': 'Warp (Door)', 'warphole': 'Warp (Hole)',
    'warpteleport': 'Teleport',
    'applymovement': 'Set Move Route', 'waitmovement': "Wait for Move's Completion",
    'removeobject': 'Remove Object', 'addobject': 'Add Object',
    'showobjectat': 'Show Object', 'hideobjectat': 'Hide Object',
    'faceplayer': 'Face Player', 'turnobject': 'Turn Object',
    'setobjectxy': 'Move Object', 'setobjectmovementtype': 'Set Movement Type',
    'lock': 'Lock', 'lockall': 'Lock All',
    'release': 'Release', 'releaseall': 'Release All',
    'fadescreen': 'Fade Screen', 'fadescreenspeed': 'Fade Screen (Speed)',
    'setflashlevel': 'Set Flash Level',
    'playse': 'Play SE', 'waitse': 'Wait for SE',
    'playfanfare': 'Play Fanfare', 'waitfanfare': 'Wait for Fanfare',
    'playbgm': 'Play BGM', 'fadeoutbgm': 'Fade Out BGM', 'fadeinbgm': 'Fade In BGM',
    'playmoncry': 'Play Cry',
    'setweather': 'Set Weather', 'doweather': 'Do Weather',
    'resetweather': 'Reset Weather',
    'delay': 'Wait', 'waitstate': 'Wait State',
    'opendoor': 'Open Door', 'closedoor': 'Close Door',
    'waitdooranim': 'Wait for Door',
    'trainerbattle': 'Trainer Battle',
    'trainerbattle_single': 'Trainer Battle (Single)',
    'trainerbattle_no_intro': 'Trainer Battle (No Intro)',
    'trainerbattle_earlyrival': 'Trainer Battle (Early Rival)',
    'trainerbattle_double': 'Trainer Battle (Double)',
    'trainerbattle_rematch': 'Trainer Battle (Rematch)',
    'trainerbattle_rematch_double': 'Trainer Battle (Rematch Double)',
    'healplayerteam': 'Heal Player Team',
    'getplayerxy': 'Get Player Position', 'random': 'Random Number',
    'setobjectxyperm': 'Set NPC Position (Permanent)',
    'savebgm': 'Save Current Music', 'fadedefaultbgm': 'Restore Saved Music',
    'buffernumberstring': 'Buffer Number', 'bufferstring': 'Buffer String',
    'wildbattle': 'Wild Battle',
    'givemon': 'Give Pokémon', 'giveegg': 'Give Egg',
    'finditem': 'Find Item',
    'additem': 'Give Item', 'removeitem': 'Remove Item', 'checkitem': 'Check Item',
    'checkitemspace': 'Check Item Space',
    'addmoney': 'Give Money', 'removemoney': 'Take Money', 'checkmoney': 'Check Money',
    'addcoins': 'Give Coins', 'removecoins': 'Take Coins',
    'setrespawn': 'Set Respawn', 'checkpartymove': 'Check Party Move',
    'bufferspeciesname': 'Buffer Species', 'bufferitemname': 'Buffer Item',
    'buffermovename': 'Buffer Move',
    'adddecoration': 'Add Decoration', 'removedecoration': 'Remove Decoration',
    'getpartysize': 'Get Party Size', 'checkplayergender': 'Check Gender',
    'setmonmove': 'Set Mon Move', 'showmonpic': 'Show Mon Pic',
    'hidemonpic': 'Hide Mon Pic', 'pokemart': 'PokéMart',
    'setmetatile': 'Set Metatile', 'textcolor': 'Text Color',
    'setworldmapflag': 'Set World Map Flag',
    'famechecker': 'Fame Checker',
    'map_script': 'Map Script', 'map_script_2': 'Map Script (Conditional)',
    '.byte': 'End Script Table', '.2byte': 'Data',
    'signmsg': 'Sign Message Mode', 'normalmsg': 'Normal Message Mode',
    'goto_if_questlog': 'If Quest Log → Goto',
    'switch': 'Switch', 'case': 'Case',
    'incrementgamestat': 'Increment Game Stat',
    'dotrainerbattle': 'Do Trainer Battle',
}

# Comparison operator display names
_OP_DISPLAY = {
    'goto_if_eq': '==', 'goto_if_ne': '!=', 'goto_if_lt': '<',
    'goto_if_ge': '>=', 'goto_if_le': '<=', 'goto_if_gt': '>',
    'call_if_eq': '==', 'call_if_ne': '!=', 'call_if_lt': '<',
    'call_if_ge': '>=', 'call_if_le': '<=', 'call_if_gt': '>',
}

# Map script type constants → friendly names
_MAP_SCRIPT_TYPES = {
    'MAP_SCRIPT_ON_LOAD': 'On Load',
    'MAP_SCRIPT_ON_TRANSITION': 'On Transition',
    'MAP_SCRIPT_ON_FRAME_TABLE': 'On Frame',
    'MAP_SCRIPT_ON_WARP_INTO_MAP_TABLE': 'On Warp In',
    'MAP_SCRIPT_ON_RESUME': 'On Resume',
    'MAP_SCRIPT_ON_RETURN_TO_FIELD': 'On Return to Field',
    'MAP_SCRIPT_ON_DIVE_WARP': 'On Dive Warp',
}

# Fadescreen constants → friendly names
_FADESCREEN_NAMES = {
    'FADE_TO_BLACK': 'Fade to Black',
    'FADE_FROM_BLACK': 'Fade from Black',
    'FADE_TO_WHITE': 'Fade to White',
    'FADE_FROM_WHITE': 'Fade from White',
    '0': 'Fade to Black', '1': 'Fade from Black',
    '2': 'Fade to White', '3': 'Fade from White',
}

# Weather constants → friendly names
_WEATHER_NAMES = {
    'WEATHER_NONE': 'None', 'WEATHER_CLOUDS': 'Clouds',
    'WEATHER_SUNNY': 'Sunny', 'WEATHER_RAIN': 'Rain',
    'WEATHER_SNOW': 'Snow', 'WEATHER_RAIN_THUNDERSTORM': 'Thunderstorm',
    'WEATHER_FOG_HORIZONTAL': 'Fog (Horizontal)',
    'WEATHER_FOG_DIAGONAL': 'Fog (Diagonal)',
    'WEATHER_VOLCANIC_ASH': 'Volcanic Ash',
    'WEATHER_SANDSTORM': 'Sandstorm',
    'WEATHER_SHADE': 'Shade', 'WEATHER_DROUGHT': 'Drought',
    'WEATHER_DOWNPOUR': 'Downpour',
    'WEATHER_UNDERWATER': 'Underwater',
}


def _stringize(cmd_tuple: tuple) -> str:
    """Convert a command tuple to a single-line RMXP-style display string.

    Returns text like:
        @>Text: Hello world
        @>Set Flag: FLAG_GOT_STARTER
        @>Conditional Goto: If VAR == 2 → Label
        @>Lock
        @>End
    """
    if not cmd_tuple:
        return '@>'
    cmd = cmd_tuple[0]

    # Label markers — inline sub-label boundaries (RMXP-style)
    if cmd == '_label_marker':
        label = cmd_tuple[1] if len(cmd_tuple) > 1 else '???'
        # Shorten: strip common map prefix
        short = label
        if '_EventScript_' in label:
            short = label.split('_EventScript_', 1)[1]
        return f'@>Label: {short}'

    friendly = _FRIENDLY_NAMES.get(cmd, cmd)

    # ── No-arg commands ─────────────────────────────────────────────────
    if cmd in ('end', 'return', 'faceplayer', 'lock', 'lockall', 'release',
               'releaseall', 'waitse', 'waitfanfare', 'doweather',
               'resetweather', 'waitdooranim', 'getpartysize',
               'checkplayergender', 'waitmessage', 'closemessage',
               'hidemonpic', 'waitstate', 'waitbuttonpress',
               'signmsg', 'normalmsg'):
        return f'@>{friendly}'

    # ── Messages ────────────────────────────────────────────────────────
    if cmd == 'message':
        label = cmd_tuple[1] if len(cmd_tuple) > 1 else ''
        text = cmd_tuple[2] if len(cmd_tuple) > 2 else ''
        if text:
            # Strip trailing $ (end-of-string marker in pokefirered)
            display = text.rstrip('$')
            # Show full text with RMXP-style continuation lines
            lines = display.split('\n')
            first = f'@>Text: {lines[0]}'
            if len(lines) > 1:
                continuation = '\n'.join(f' :        : {ln}' for ln in lines[1:] if ln)
                return f'{first}\n{continuation}' if continuation else first
            return first
        if label:
            return f'@>Text: {label}'
        return '@>Text: (empty)'

    # ── Flags ───────────────────────────────────────────────────────────
    if cmd in ('setflag', 'clearflag', 'checkflag'):
        flag = cmd_tuple[1] if len(cmd_tuple) > 1 else ''
        text = f'@>{friendly}: {_resolve_name(flag)}'
        # Check if this flag activates a condition page
        if cmd == 'setflag' and flag and _CURRENT_PAGES:
            for pi, pg in enumerate(_CURRENT_PAGES):
                cc = pg.get('_condition_cmd')
                if cc and cc[0] == 'goto_if_set' and len(cc) > 1 and cc[1] == flag:
                    text += f'  → activates Page {pi + 1}'
                    break
        elif cmd == 'clearflag' and flag and _CURRENT_PAGES:
            for pi, pg in enumerate(_CURRENT_PAGES):
                cc = pg.get('_condition_cmd')
                if cc and cc[0] == 'goto_if_unset' and len(cc) > 1 and cc[1] == flag:
                    text += f'  → activates Page {pi + 1}'
                    break
        return text

    # ── Variables ───────────────────────────────────────────────────────
    if cmd in ('setvar', 'addvar', 'subvar', 'compare_var_to_value'):
        var = cmd_tuple[1] if len(cmd_tuple) > 1 else ''
        val = cmd_tuple[2] if len(cmd_tuple) > 2 else ''
        op = {'setvar': '=', 'addvar': '+=', 'subvar': '-=',
              'compare_var_to_value': '?='}
        text = f'@>{friendly}: [{_resolve_name(var)}] {op.get(cmd, "=")} {val}'
        # Check if this var assignment activates a condition page
        if cmd == 'setvar' and var and val and _CURRENT_PAGES:
            for pi, pg in enumerate(_CURRENT_PAGES):
                cc = pg.get('_condition_cmd')
                if cc and cc[0] == 'goto_if_eq' and len(cc) > 2:
                    if cc[1] == var and str(cc[2]) == str(val):
                        text += f'  → activates Page {pi + 1}'
                        break
        return text
    if cmd == 'copyvar':
        args = cmd_tuple[1] if len(cmd_tuple) > 1 else ''
        return f'@>Copy Variable: {args}'
    if cmd == 'compare_var_to_var':
        args = cmd_tuple[1] if len(cmd_tuple) > 1 else ''
        return f'@>Compare Var to Var: {args}'

    # ── Conditional branches (compare) ──────────────────────────────────
    if cmd in _OP_DISPLAY:
        is_call = cmd.startswith('call_if')
        action = 'Call' if is_call else 'Goto'
        if cmd in ('goto_if_set', 'goto_if_unset', 'call_if_set', 'call_if_unset'):
            flag = cmd_tuple[1] if len(cmd_tuple) > 1 else ''
            label = cmd_tuple[2] if len(cmd_tuple) > 2 else ''
            state = 'SET' if cmd.endswith('_set') else 'NOT SET'
            return f'@>Conditional {action}: If {_resolve_name(flag)} is {state} → {label}'
        var = cmd_tuple[1] if len(cmd_tuple) > 1 else ''
        val = cmd_tuple[2] if len(cmd_tuple) > 2 else ''
        label = cmd_tuple[3] if len(cmd_tuple) > 3 else ''
        op = _OP_DISPLAY[cmd]
        return f'@>Conditional {action}: If [{_resolve_name(var)}] {op} {val} → {label}'

    # ── Flag conditionals (not in _OP_DISPLAY) ──────────────────────────
    if cmd in ('goto_if_set', 'goto_if_unset', 'call_if_set', 'call_if_unset'):
        is_call = cmd.startswith('call_if')
        action = 'Call' if is_call else 'Goto'
        flag = cmd_tuple[1] if len(cmd_tuple) > 1 else ''
        label = cmd_tuple[2] if len(cmd_tuple) > 2 else ''
        state = 'SET' if cmd.endswith('_set') else 'NOT SET'
        return f'@>Conditional {action}: If {_resolve_name(flag)} is {state} → {label}'

    # ── Goto / Call ─────────────────────────────────────────────────────
    if cmd == 'goto':
        label = cmd_tuple[1] if len(cmd_tuple) > 1 else ''
        return f'@>Jump to Label: {label}'
    if cmd == 'call':
        label = cmd_tuple[1] if len(cmd_tuple) > 1 else ''
        return f'@>Call Script: {label}'
    if cmd == 'goto_if_questlog':
        label = cmd_tuple[1] if len(cmd_tuple) > 1 else ''
        return f'@>If Quest Log → {label}'
    if cmd == 'callstd':
        std = cmd_tuple[1] if len(cmd_tuple) > 1 else ''
        return f'@>Call Standard: {std}'
    if cmd == 'gotostd':
        std = cmd_tuple[1] if len(cmd_tuple) > 1 else ''
        return f'@>Goto Standard: {std}'

    # ── Switch / Case ──────────────────────────────────────────────────
    if cmd == 'switch':
        var = cmd_tuple[1] if len(cmd_tuple) > 1 else ''
        return f'@>Switch: [{_resolve_name(var)}]'
    if cmd == 'case':
        args = cmd_tuple[1] if len(cmd_tuple) > 1 else ''
        parts = [p.strip() for p in str(args).split(',')]
        val = parts[0] if parts else ''
        label = parts[1] if len(parts) > 1 else ''
        return f' :    Case {val} → {label}'

    # ── Warps ───────────────────────────────────────────────────────────
    if cmd in ('warp', 'warpsilent', 'warpdoor', 'warphole', 'warpteleport'):
        dest = cmd_tuple[1] if len(cmd_tuple) > 1 else ''
        x = cmd_tuple[2] if len(cmd_tuple) > 2 else ''
        y = cmd_tuple[3] if len(cmd_tuple) > 3 else ''
        return f'@>{friendly}: {dest} ({x}, {y})'

    # ── Movement ────────────────────────────────────────────────────────
    if cmd == 'applymovement':
        target = cmd_tuple[1] if len(cmd_tuple) > 1 else ''
        movement = cmd_tuple[2] if len(cmd_tuple) > 2 else ''
        header = f'@>Set Move Route: {target}'
        # Look up movement steps and show them inline with $> prefix (RMXP style)
        steps = _ALL_SCRIPTS.get(str(movement), [])
        if steps:
            # Friendly names for common movement commands
            step_names = {
                'walk_up': 'Move Up', 'walk_down': 'Move Down',
                'walk_left': 'Move Left', 'walk_right': 'Move Right',
                'walk_in_place_faster_up': 'Turn Up',
                'walk_in_place_faster_down': 'Turn Down',
                'walk_in_place_faster_left': 'Turn Left',
                'walk_in_place_faster_right': 'Turn Right',
                'face_up': 'Turn Up', 'face_down': 'Turn Down',
                'face_left': 'Turn Left', 'face_right': 'Turn Right',
                'step_end': None,  # Don't show step_end
                'set_invisible': 'Set Invisible',
                'set_visible': 'Set Visible',
            }
            step_lines = []
            for step in steps:
                step_cmd = step[0] if step else ''
                if step_cmd == 'step_end':
                    continue
                name = step_names.get(step_cmd, step_cmd.replace('_', ' ').title())
                step_lines.append(f' :          : $>{name}')
            if step_lines:
                return header + '\n' + '\n'.join(step_lines)
        elif movement:
            header += f', {movement}'
        return header
    if cmd == 'waitmovement':
        target = cmd_tuple[1] if len(cmd_tuple) > 1 else '0'
        return f"@>Wait for Move's Completion" if str(target) == '0' else f"@>Wait for Move's Completion: {target}"

    # ── Object control ──────────────────────────────────────────────────
    if cmd in ('removeobject', 'addobject'):
        obj_id = cmd_tuple[1] if len(cmd_tuple) > 1 else ''
        return f'@>{friendly}: {obj_id}'
    if cmd in ('showobjectat', 'hideobjectat'):
        args = cmd_tuple[1] if len(cmd_tuple) > 1 else ''
        parts = [p.strip() for p in str(args).split(',')]
        resolved = [_resolve_name(p) if p.startswith(('OBJ_EVENT_GFX_', 'MAP_')) else p for p in parts]
        return f'@>{friendly}: {", ".join(resolved)}'
    if cmd == 'turnobject':
        args = cmd_tuple[1] if len(cmd_tuple) > 1 else ''
        parts = [p.strip() for p in str(args).split(',')]
        if len(parts) >= 2:
            direction = parts[1].replace('DIR_', '').replace('_', ' ').title() if parts[1].startswith('DIR_') else parts[1]
            return f'@>Turn Object: {parts[0]}, {direction}'
        return f'@>Turn Object: {args}'
    if cmd == 'setobjectxy':
        args = cmd_tuple[1] if len(cmd_tuple) > 1 else ''
        return f'@>Move Object: {args}'

    # ── Delay ───────────────────────────────────────────────────────────
    if cmd == 'delay':
        frames = cmd_tuple[1] if len(cmd_tuple) > 1 else ''
        return f'@>Wait: {frames} frames'

    # ── Doors ───────────────────────────────────────────────────────────
    if cmd in ('opendoor', 'closedoor'):
        args = cmd_tuple[1] if len(cmd_tuple) > 1 else ''
        return f'@>{friendly}: {args}'

    # ── Trainer / Wild Battle ───────────────────────────────────────────
    if cmd in ('trainerbattle', 'trainerbattle_single', 'trainerbattle_earlyrival',
                'trainerbattle_no_intro', 'trainerbattle_double',
                'trainerbattle_rematch', 'trainerbattle_rematch_double'):
        args = cmd_tuple[1] if len(cmd_tuple) > 1 else ''
        parts = [p.strip() for p in str(args).split(',')]
        # Args layout: TRAINER is always parts[0] (type is in the command name)
        trainer_const = parts[0] if parts else str(args)
        trainer_name = _resolve_name(trainer_const)
        header = f'@>Trainer Battle: {trainer_name}'
        details = []
        texts = _ALL_SCRIPTS.get('__texts__', {})

        if cmd == 'trainerbattle_no_intro':
            # TRAINER, DEFEAT
            defeat = parts[1] if len(parts) > 1 else ''
            if defeat:
                dt = texts.get(defeat, '')
                line = f'"{dt.split(chr(10))[0][:60]}"' if dt else defeat
                details.append(f' :          : Defeat: {line}')
        elif cmd == 'trainerbattle_earlyrival':
            # TRAINER, FLAGS, DEFEAT, VICTORY
            defeat = parts[2] if len(parts) > 2 else ''
            victory = parts[3] if len(parts) > 3 else ''
            if defeat:
                dt = texts.get(defeat, '')
                line = f'"{dt.split(chr(10))[0][:60]}"' if dt else defeat
                details.append(f' :          : Defeat: {line}')
            if victory:
                details.append(f' :          : Victory: {victory}')
        else:
            # _single / _double: TRAINER, INTRO, DEFEAT [, ...]
            intro = parts[1] if len(parts) > 1 else ''
            defeat = parts[2] if len(parts) > 2 else ''
            if intro:
                it = texts.get(intro, '')
                line = f'"{it.split(chr(10))[0][:60]}"' if it else intro
                details.append(f' :          : Intro: {line}')
            if defeat:
                dt = texts.get(defeat, '')
                line = f'"{dt.split(chr(10))[0][:60]}"' if dt else defeat
                details.append(f' :          : Defeat: {line}')
            # Continue script for single/double
            cont_idx = 3 if cmd != 'trainerbattle_double' else 4
            cont_script = parts[cont_idx] if len(parts) > cont_idx else ''
            if cont_script:
                details.append(f' :          : Continue → {cont_script}')

        if details:
            return header + '\n' + '\n'.join(details)
        return header
    if cmd == 'wildbattle':
        species = cmd_tuple[1] if len(cmd_tuple) > 1 else ''
        level = cmd_tuple[2] if len(cmd_tuple) > 2 else ''
        return f'@>Wild Battle: {_resolve_name(species)} Lv.{level}'

    # ── Give Pokemon ────────────────────────────────────────────────────
    if cmd == 'givemon':
        args = cmd_tuple[1] if len(cmd_tuple) > 1 else ''
        parts = [p.strip() for p in str(args).split(',')]
        species = parts[0] if parts else ''
        level = parts[1] if len(parts) > 1 else ''
        item = parts[2] if len(parts) > 2 else 'ITEM_NONE'
        header = f'@>Give Pokémon: {_resolve_name(species)} Lv.{level}'
        details = []
        if item and item != 'ITEM_NONE':
            details.append(f' :          : Held Item: {_resolve_name(item)}')
        # Show custom moves if any are set
        custom_moves = [parts[i] for i in range(3, min(7, len(parts)))
                        if parts[i] and parts[i] != 'MOVE_NONE']
        if custom_moves:
            move_names = [_resolve_name(m) for m in custom_moves]
            details.append(f' :          : Moves: {", ".join(move_names)}')
        else:
            details.append(f' :          : Moves: (default by level)')
        if details:
            return header + '\n' + '\n'.join(details)
        return header

    # ── Items ───────────────────────────────────────────────────────────
    if cmd in ('finditem', 'additem', 'removeitem', 'checkitem', 'checkitemspace'):
        args = cmd_tuple[1] if len(cmd_tuple) > 1 else ''
        parts = [p.strip() for p in str(args).split(',')]
        item_const = parts[0] if parts else args
        item_name = _resolve_name(item_const)
        if len(parts) > 1 and parts[1] and parts[1] != '1':
            return f'@>{friendly}: {item_name} ×{parts[1]}'
        return f'@>{friendly}: {item_name}'

    # ── Money / Coins ───────────────────────────────────────────────────
    if cmd in ('addmoney', 'removemoney', 'checkmoney', 'addcoins', 'removecoins'):
        amount = cmd_tuple[1] if len(cmd_tuple) > 1 else ''
        return f'@>{friendly}: {amount}'

    # ── Play Cry ────────────────────────────────────────────────────────
    if cmd == 'playmoncry':
        args = cmd_tuple[1] if len(cmd_tuple) > 1 else ''
        parts = [p.strip() for p in str(args).split(',')]
        species = parts[0] if parts else args
        rest = ', '.join(parts[1:]) if len(parts) > 1 else ''
        name = _resolve_name(species)
        if rest:
            return f'@>{friendly}: {name}, {rest}'
        return f'@>{friendly}: {name}'

    # ── Special ─────────────────────────────────────────────────────────
    if cmd == 'specialvar':
        args = cmd_tuple[1] if len(cmd_tuple) > 1 else ''
        parts = [p.strip() for p in str(args).split(',')]
        if len(parts) >= 2:
            return f'@>{friendly}: {_resolve_name(parts[0])}, {parts[1]}'
        return f'@>{friendly}: {args}'
    if cmd == 'special':
        func = cmd_tuple[1] if len(cmd_tuple) > 1 else ''
        return f'@>{friendly}: {func}'

    # ── Buffers ─────────────────────────────────────────────────────────
    if cmd in ('bufferspeciesname', 'bufferitemname', 'buffermovename'):
        args = cmd_tuple[1] if len(cmd_tuple) > 1 else ''
        # Args are usually "buffer_num, CONSTANT" — resolve the constant part
        parts = [p.strip() for p in str(args).split(',')]
        if len(parts) >= 2:
            return f'@>{friendly}: {parts[0]}, {_resolve_name(parts[1])}'
        return f'@>{friendly}: {_resolve_name(args)}'

    # ── Screen effects ──────────────────────────────────────────────────
    if cmd in ('fadescreen', 'fadescreenspeed'):
        args = cmd_tuple[1] if len(cmd_tuple) > 1 else ''
        return f'@>{friendly}: {_FADESCREEN_NAMES.get(str(args).strip(), args)}'
    if cmd == 'setflashlevel':
        args = cmd_tuple[1] if len(cmd_tuple) > 1 else ''
        return f'@>{friendly}: {args}'

    # ── Weather ────────────────────────────────────────────────────────
    if cmd in ('setweather', 'doweather', 'resetweather'):
        args = cmd_tuple[1] if len(cmd_tuple) > 1 else ''
        if args:
            return f'@>{friendly}: {_WEATHER_NAMES.get(str(args).strip(), _resolve_name(str(args).strip()))}'
        return f'@>{friendly}'

    # ── World map flags ────────────────────────────────────────────────
    if cmd == 'setworldmapflag':
        flag = cmd_tuple[1] if len(cmd_tuple) > 1 else ''
        name = _resolve_name(flag.strip()) if flag else ''
        return f'@>{friendly}: {name}'

    # ── Map script table entries ───────────────────────────────────────
    if cmd == 'map_script':
        args = cmd_tuple[1] if len(cmd_tuple) > 1 else ''
        parts = [p.strip() for p in str(args).split(',')]
        if len(parts) >= 2:
            script_type = _MAP_SCRIPT_TYPES.get(parts[0], parts[0])
            return f'@>{friendly} ({script_type}): {parts[1]}'
        return f'@>{friendly}: {args}'
    if cmd == 'map_script_2':
        args = cmd_tuple[1] if len(cmd_tuple) > 1 else ''
        parts = [p.strip() for p in str(args).split(',')]
        if len(parts) >= 3:
            var_name = _resolve_name(parts[0])
            return f'@>{friendly}: If [{var_name}] == {parts[1]} → {parts[2]}'
        return f'@>{friendly}: {args}'

    # ── Script table terminator ────────────────────────────────────────
    if cmd == '.byte' and str(cmd_tuple[1]).strip() == '0':
        return '@>End of Script Table'
    if cmd in ('.byte', '.2byte'):
        args = cmd_tuple[1] if len(cmd_tuple) > 1 else ''
        return f'@>{friendly}: {args}'

    # ── Object control — resolve constant names ────────────────────────
    if cmd in ('setobjectmovementtype',):
        args = cmd_tuple[1] if len(cmd_tuple) > 1 else ''
        parts = [p.strip() for p in str(args).split(',')]
        if len(parts) >= 2:
            return f'@>Set Movement Type: {parts[0]}, {_resolve_name(parts[1])}'
        return f'@>{friendly}: {args}'

    # ── Sound — resolve constant names ─────────────────────────────────
    if cmd in ('playse', 'playfanfare'):
        name = cmd_tuple[1] if len(cmd_tuple) > 1 else ''
        return f'@>{friendly}: {_resolve_name(str(name).strip())}'
    if cmd == 'playbgm':
        args = cmd_tuple[1] if len(cmd_tuple) > 1 else ''
        parts = [p.strip() for p in str(args).split(',')]
        return f'@>{friendly}: {_resolve_name(parts[0])}'

    # ── Fallback: resolve any recognizable constants in args ───────────
    args = cmd_tuple[1] if len(cmd_tuple) > 1 else ''
    if args:
        # Try to resolve each comma-separated argument
        parts = [p.strip() for p in str(args).split(',')]
        resolved = []
        for p in parts:
            if p and any(p.startswith(pfx) for pfx in
                         ('FLAG_', 'VAR_', 'TRAINER_', 'ITEM_', 'SPECIES_',
                          'MOVE_', 'SE_', 'MUS_', 'WEATHER_', 'OBJ_EVENT_GFX_')):
                resolved.append(_resolve_name(p))
            else:
                resolved.append(p)
        return f'@>{friendly}: {", ".join(resolved)}'
    return f'@>{friendly}'


# ═════════════════════════════════════════════════════════════════════════════
# Command Edit Dialog — wraps a _CommandWidget in a popup for double-click editing
# ═════════════════════════════════════════════════════════════════════════════

class _CommandEditDialog(QDialog):
    """Popup dialog for editing a single command's parameters.

    Wraps the appropriate _CommandWidget (the same widget classes used before)
    inside a dialog with OK / Cancel buttons.  Returns the edited tuple on
    accept, or None on cancel.

    If the command references a script label (call, goto, call_if_*, goto_if_*,
    etc.) a "Go To" button appears that saves and navigates to the target.
    """

    # Custom result code for Go To action
    GoToResult = 2

    def __init__(self, cmd_tuple: tuple, parent=None):
        super().__init__(parent)
        cmd = cmd_tuple[0] if cmd_tuple else 'nop'
        friendly = _FRIENDLY_NAMES.get(cmd, cmd)
        self.setWindowTitle(f'Edit: {friendly}')
        self.setMinimumWidth(480)
        self._goto_label: str = ''

        layout = QVBoxLayout(self)

        # Create the parameter widget
        self._widget = _widget_for_tuple(cmd_tuple)

        # If it's a header-only (no-arg) command, just show a label
        if getattr(self._widget, '_header_only', False):
            layout.addWidget(QLabel(f'This command has no parameters: {friendly}'))
        else:
            # Apply scroll guard to prevent accidental scroll changes
            install_scroll_guard_recursive(self._widget)
            layout.addWidget(self._widget)

        # Button row
        btn_layout = QHBoxLayout()

        # Add "Go To →" button if the widget has a navigable label field
        self._goto_btn = None
        if hasattr(self._widget, 'label_combo'):
            self._goto_btn = QPushButton('Go To \u2192')
            self._goto_btn.setToolTip(
                'Save changes and navigate to the target script')
            self._goto_btn.clicked.connect(self._on_goto)
            btn_layout.addWidget(self._goto_btn)

        btn_layout.addStretch()

        # OK / Cancel buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        btn_layout.addWidget(buttons)

        layout.addLayout(btn_layout)

    def _on_goto(self):
        """Handle Go To button — save the target label and close."""
        if hasattr(self._widget, 'label_combo'):
            self._goto_label = self._widget.label_combo.currentText().strip()
        if self._goto_label:
            self.done(self.GoToResult)

    def result_tuple(self) -> tuple:
        """Return the edited command tuple."""
        return self._widget.to_tuple()

    @staticmethod
    def edit_command(cmd_tuple: tuple, parent=None) -> tuple | None:
        """Show the dialog and return the edited tuple, or None if cancelled."""
        dlg = _CommandEditDialog(cmd_tuple, parent)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            return dlg.result_tuple()
        return None


class _CommandWidget(QWidget):
    """Base for all command-type widgets. Subclasses implement to_tuple()."""

    # Override to True in no-arg widgets (no parameters to edit)
    _header_only = False

    def to_tuple(self) -> tuple:
        return ('nop',)

    def friendly_name(self) -> str:
        return 'Command'


# ═════════════════════════════════════════════════════════════════════════════
# Page 1 commands — Dialogue & Logic
# ═════════════════════════════════════════════════════════════════════════════

class _MessageWidget(_CommandWidget):
    """Show Message / msgbox — text editor with character limit enforcement."""

    def __init__(self, label_name=None, text='', msg_type='', parent=None):
        from ui.game_text_edit import GameTextEdit

        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)

        top = QHBoxLayout()
        top.addWidget(QLabel('Label:'))
        self.label_combo = _make_label_combo(label_name or '')
        self.label_combo.setPlaceholderText('(inline text if empty)')
        self.label_combo.setToolTip(_tt(
            'Text label name in text.inc\n'
            'Leave empty to use inline text instead of a named label'))
        top.addWidget(self.label_combo, 1)

        top.addWidget(QLabel('Type:'))
        self.type_combo = QComboBox()
        self.type_combo.setEditable(True)
        self.type_combo.setToolTip(_tt(
            'Message box style:\n'
            'MSGBOX_DEFAULT — standard dialogue box\n'
            'MSGBOX_YESNO — yes/no choice\n'
            'MSGBOX_AUTOCLOSE — closes after a delay\n'
            'MSGBOX_NPC — talk to NPC (face player)\n'
            'MSGBOX_SIGN — signpost style'))
        self.type_combo.addItems(ConstantsManager.MSG_TYPES)
        if msg_type:
            idx = self.type_combo.findText(msg_type)
            if idx >= 0:
                self.type_combo.setCurrentIndex(idx)
            else:
                self.type_combo.setEditText(msg_type)
        else:
            self.type_combo.setCurrentIndex(0)
        self.type_combo.setMaximumWidth(200)
        top.addWidget(self.type_combo)
        layout.addLayout(top)

        # GameTextEdit handles escape codes, $ stripping, char limits,
        # {COMMAND} blue highlighting, and right-click insert menu
        self.text_edit = GameTextEdit(max_chars_per_line=36, max_lines=20)
        self.text_edit.setMaximumHeight(80)
        self.text_edit.setPlaceholderText('Message text...')
        self.text_edit.set_eventide_text(text or '')
        layout.addWidget(self.text_edit)

    def to_tuple(self):
        label = self.label_combo.currentText().strip() or None
        text = self.text_edit.get_eventide_text()
        msg_type = self.type_combo.currentText().strip()
        return ('message', label, text, msg_type)

    def friendly_name(self):
        return 'Show Message'


class _YesNoWidget(_CommandWidget):
    """Yes/No choice box — yesnobox x, y."""
    def __init__(self, x=0, y=0, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Position — X:'))
        self.x_spin = QSpinBox()
        self.x_spin.setRange(0, 29)
        self.x_spin.setValue(x)
        self.x_spin.setToolTip(_tt('Horizontal position of the choice box on screen (0–29 tiles)'))
        layout.addWidget(self.x_spin)
        layout.addWidget(QLabel('Y:'))
        self.y_spin = QSpinBox()
        self.y_spin.setRange(0, 19)
        self.y_spin.setValue(y)
        self.y_spin.setToolTip(_tt('Vertical position of the choice box on screen (0–19 tiles)'))
        layout.addWidget(self.y_spin)
        layout.addStretch()

    def to_tuple(self):
        return ('yesnobox', f'{self.x_spin.value()}, {self.y_spin.value()}')

    def friendly_name(self):
        return 'Yes/No Choice'


class _MultiChoiceWidget(_CommandWidget):
    """Multi-choice box — multichoice x, y, list_id, allow_cancel."""
    def __init__(self, x=0, y=0, list_id='0', cancel='0', parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('X:'))
        self.x_spin = QSpinBox(); self.x_spin.setRange(0, 29); self.x_spin.setValue(x)
        self.x_spin.setToolTip(_tt('Horizontal position of the choice box (0–29 tiles)'))
        layout.addWidget(self.x_spin)
        layout.addWidget(QLabel('Y:'))
        self.y_spin = QSpinBox(); self.y_spin.setRange(0, 19); self.y_spin.setValue(y)
        self.y_spin.setToolTip(_tt('Vertical position of the choice box (0–19 tiles)'))
        layout.addWidget(self.y_spin)
        layout.addWidget(QLabel('List ID:'))
        self.list_edit = QLineEdit(str(list_id))
        self.list_edit.setToolTip(_tt('Index of the multichoice list defined in the game data'))
        self.list_edit.setMaximumWidth(60)
        layout.addWidget(self.list_edit)
        self.cancel_check = QCheckBox('Allow Cancel')
        self.cancel_check.setToolTip(_tt('If checked, pressing B closes the menu and returns a cancel value'))
        self.cancel_check.setChecked(str(cancel) != '0')
        layout.addWidget(self.cancel_check)
        layout.addStretch()

    def to_tuple(self):
        cancel = '1' if self.cancel_check.isChecked() else '0'
        return ('multichoice', f'{self.x_spin.value()}, {self.y_spin.value()}, '
                f'{self.list_edit.text()}, {cancel}')

    def friendly_name(self):
        return 'Multi-Choice Box'


class _SetFlagWidget(_CommandWidget):
    """Set Flag — searchable flag picker."""
    def __init__(self, flag='', parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Flag:'))
        self.picker = ConstantPicker(ConstantsManager.FLAGS, prefix='FLAG_')
        self.picker.setToolTip(_tt('Turn this flag ON — it stays on until cleared\nType to search flag names'))
        if flag:
            self.picker.set_constant(flag)
        layout.addWidget(self.picker, 1)

    def to_tuple(self):
        return ('setflag', self.picker.selected_constant())

    def friendly_name(self):
        return 'Set Flag'


class _ClearFlagWidget(_CommandWidget):
    """Clear Flag — searchable flag picker."""
    def __init__(self, flag='', parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Flag:'))
        self.picker = ConstantPicker(ConstantsManager.FLAGS, prefix='FLAG_')
        self.picker.setToolTip(_tt('Turn this flag OFF — resets it to its default state\nType to search flag names'))
        if flag:
            self.picker.set_constant(flag)
        layout.addWidget(self.picker, 1)

    def to_tuple(self):
        return ('clearflag', self.picker.selected_constant())

    def friendly_name(self):
        return 'Clear Flag'


class _CheckFlagWidget(_CommandWidget):
    """Check Flag — searchable flag picker."""
    def __init__(self, flag='', parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Flag:'))
        self.picker = ConstantPicker(ConstantsManager.FLAGS, prefix='FLAG_')
        self.picker.setToolTip(_tt('Check whether this flag is ON or OFF\nResult is used by the next conditional command'))
        if flag:
            self.picker.set_constant(flag)
        layout.addWidget(self.picker, 1)

    def to_tuple(self):
        return ('checkflag', self.picker.selected_constant())

    def friendly_name(self):
        return 'Check Flag'


class _SetVarWidget(_CommandWidget):
    """Set Variable — var picker + value."""
    def __init__(self, var='', value='0', parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Variable:'))
        self.picker = ConstantPicker(ConstantsManager.VARS, prefix='VAR_')
        self.picker.setToolTip(_tt('Set this variable to a specific value\nType to search variable names'))
        if var:
            self.picker.set_constant(var)
        layout.addWidget(self.picker, 1)
        layout.addWidget(QLabel('Value:'))
        self.value_combo = _make_value_combo(value)
        self.value_combo.setToolTip(_tt('The value to store in the variable (0–65535)'))
        layout.addWidget(self.value_combo)

    def to_tuple(self):
        return ('setvar', f'{self.picker.selected_constant()}, {self.value_combo.currentText()}')

    def friendly_name(self):
        return 'Set Variable'


class _AddVarWidget(_CommandWidget):
    """Add to Variable — var picker + amount."""
    def __init__(self, var='', value='1', parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Variable:'))
        self.picker = ConstantPicker(ConstantsManager.VARS, prefix='VAR_')
        self.picker.setToolTip(_tt('Add a value to this variable (variable += amount)'))
        if var:
            self.picker.set_constant(var)
        layout.addWidget(self.picker, 1)
        layout.addWidget(QLabel('Amount:'))
        self.value_combo = _make_value_combo(value)
        self.value_combo.setToolTip(_tt('How much to add to the variable'))
        layout.addWidget(self.value_combo)

    def to_tuple(self):
        return ('addvar', f'{self.picker.selected_constant()}, {self.value_combo.currentText()}')

    def friendly_name(self):
        return 'Add to Variable'


class _SubVarWidget(_CommandWidget):
    """Subtract from Variable."""
    def __init__(self, var='', value='1', parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Variable:'))
        self.picker = ConstantPicker(ConstantsManager.VARS, prefix='VAR_')
        self.picker.setToolTip(_tt('Subtract a value from this variable (variable -= amount)'))
        if var:
            self.picker.set_constant(var)
        layout.addWidget(self.picker, 1)
        layout.addWidget(QLabel('Amount:'))
        self.value_combo = _make_value_combo(value)
        self.value_combo.setToolTip(_tt('How much to subtract from the variable'))
        layout.addWidget(self.value_combo)

    def to_tuple(self):
        return ('subvar', f'{self.picker.selected_constant()}, {self.value_combo.currentText()}')

    def friendly_name(self):
        return 'Subtract from Variable'


class _CompareVarWidget(_CommandWidget):
    """Compare Variable to Value — for conditional checks."""
    def __init__(self, var='', value='0', parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Variable:'))
        self.picker = ConstantPicker(ConstantsManager.VARS, prefix='VAR_')
        self.picker.setToolTip(_tt(
            'Compare this variable against a value\n'
            'Use with goto_if/call_if to branch based on the result'))
        if var:
            self.picker.set_constant(var)
        layout.addWidget(self.picker, 1)
        layout.addWidget(QLabel('Value:'))
        self.value_combo = _make_value_combo(value)
        self.value_combo.setToolTip(_tt('The value to compare the variable against'))
        layout.addWidget(self.value_combo)

    def to_tuple(self):
        return ('compare_var_to_value', f'{self.picker.selected_constant()}, {self.value_combo.currentText()}')

    def friendly_name(self):
        return 'Compare Variable to Value'


def _make_label_combo(value=''):
    """Create a searchable combo populated with script labels from the current map."""
    combo = QComboBox()
    combo.setEditable(True)
    combo.setMinimumWidth(160)
    # First item is blank — means "none" / not set
    combo.addItem('')
    if _SCRIPT_LABELS:
        combo.addItems(_SCRIPT_LABELS)
    completer = QCompleter(
        _SCRIPT_LABELS or [], combo)
    completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
    completer.setFilterMode(Qt.MatchFlag.MatchContains)
    combo.setCompleter(completer)
    if value:
        idx = combo.findText(value)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        else:
            combo.setEditText(value)
    else:
        combo.setCurrentIndex(0)  # Select the blank entry
    return combo


def _make_object_combo(value=''):
    """Create a searchable combo populated with object local IDs from the current map."""
    combo = QComboBox()
    combo.setEditable(True)
    combo.setMaximumWidth(200)
    if _OBJECT_LOCAL_IDS:
        combo.addItems(_OBJECT_LOCAL_IDS)
    if value:
        idx = combo.findText(value)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        else:
            combo.setEditText(value)
    return combo


def _make_value_combo(value='0'):
    """Create an editable combo for script values (numbers, TRUE/FALSE, constants).

    Pre-populated with common values used in pokefirered scripting.
    User can type any custom value since it's editable.
    """
    combo = QComboBox()
    combo.setEditable(True)
    combo.setMaximumWidth(100)
    common = ['0', '1', '2', '3', '4', '5', 'TRUE', 'FALSE']
    combo.addItems(common)
    if value:
        idx = combo.findText(str(value))
        if idx >= 0:
            combo.setCurrentIndex(idx)
        else:
            combo.setEditText(str(value))
    else:
        combo.setCurrentIndex(0)
    return combo


class _GotoIfCompareWidget(_CommandWidget):
    """Conditional Goto (compare) — goto_if_eq/ne/lt/ge/le/gt VAR, VALUE, LABEL.

    Two-row layout:
      Row 1: Variable picker | comparison operator | value
      Row 2: → Target label picker
    """
    _COMPARE_OPS = [
        ('goto_if_eq', '=='),
        ('goto_if_ne', '!='),
        ('goto_if_lt', '<'),
        ('goto_if_ge', '>='),
        ('goto_if_le', '<='),
        ('goto_if_gt', '>'),
    ]

    def __init__(self, cmd='goto_if_eq', var='', value='', label='', parent=None):
        super().__init__(parent)
        self._cmd = cmd
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 2, 4, 2)
        outer.setSpacing(2)

        # Row 1: Variable | operator | value
        row1 = QHBoxLayout()
        row1.addWidget(QLabel('Variable:'))
        self.var_picker = ConstantPicker(ConstantsManager.VARS, prefix='VAR_')
        self.var_picker.setToolTip(_tt('The variable to check'))
        if var:
            self.var_picker.set_constant(var)
        row1.addWidget(self.var_picker, 1)
        self.op_combo = QComboBox()
        self.op_combo.setToolTip(_tt('Comparison: jump only when this condition is true'))
        self.op_combo.setMaximumWidth(60)
        for raw, pretty in self._COMPARE_OPS:
            self.op_combo.addItem(pretty, raw)
        idx = self.op_combo.findData(cmd)
        if idx >= 0:
            self.op_combo.setCurrentIndex(idx)
        row1.addWidget(self.op_combo)
        self.value_combo = _make_value_combo(value)
        self.value_combo.setToolTip(_tt('The value to compare against'))
        row1.addWidget(self.value_combo)
        outer.addLayout(row1)

        # Row 2: → Target label
        row2 = QHBoxLayout()
        row2.addWidget(QLabel('→ Goto:'))
        self.label_combo = _make_label_combo(label)
        self.label_combo.setToolTip(_tt('Jump to this script label if the condition is true\nExecution continues from there (does not return)'))
        row2.addWidget(self.label_combo, 1)
        outer.addLayout(row2)

    def to_tuple(self):
        op = self.op_combo.currentData() or self._cmd
        return (op, self.var_picker.selected_constant(),
                self.value_combo.currentText().strip(),
                self.label_combo.currentText().strip())

    def friendly_name(self):
        op = self.op_combo.currentData() or self._cmd
        names = dict(self._COMPARE_OPS)
        return f'If Var {names.get(op, "==")} → Goto'


class _GotoIfFlagWidget(_CommandWidget):
    """Conditional Goto (flag) — goto_if_set/unset FLAG, LABEL.

    Two-row layout:
      Row 1: Flag picker | set/unset toggle
      Row 2: → Target label picker
    """

    def __init__(self, cmd='goto_if_set', flag='', label='', parent=None):
        super().__init__(parent)
        self._is_set = cmd.endswith('_set')
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 2, 4, 2)
        outer.setSpacing(2)

        # Row 1: Flag | set/unset
        row1 = QHBoxLayout()
        row1.addWidget(QLabel('Flag:'))
        self.flag_picker = ConstantPicker(ConstantsManager.FLAGS, prefix='FLAG_')
        self.flag_picker.setToolTip(_tt('The flag to check'))
        if flag:
            self.flag_picker.set_constant(flag)
        row1.addWidget(self.flag_picker, 1)
        self.state_combo = QComboBox()
        self.state_combo.setToolTip(_tt('Jump when the flag is SET (ON) or NOT set (OFF)'))
        self.state_combo.addItem('is SET', 'goto_if_set')
        self.state_combo.addItem('is NOT set', 'goto_if_unset')
        self.state_combo.setCurrentIndex(0 if self._is_set else 1)
        row1.addWidget(self.state_combo)
        outer.addLayout(row1)

        # Row 2: → Target label
        row2 = QHBoxLayout()
        row2.addWidget(QLabel('→ Goto:'))
        self.label_combo = _make_label_combo(label)
        self.label_combo.setToolTip(_tt('Jump to this script label if the flag matches\nExecution continues from there (does not return)'))
        row2.addWidget(self.label_combo, 1)
        outer.addLayout(row2)

    def to_tuple(self):
        cmd = self.state_combo.currentData()
        return (cmd, self.flag_picker.selected_constant(),
                self.label_combo.currentText().strip())

    def friendly_name(self):
        state = 'Set' if self.state_combo.currentData() == 'goto_if_set' else 'Not Set'
        return f'If Flag {state} → Goto'


class _CallIfCompareWidget(_CommandWidget):
    """Conditional Call (compare) — call_if_eq/ne/lt/ge VAR, VALUE, LABEL.

    Two-row layout:
      Row 1: Variable picker | comparison operator | value
      Row 2: → Target label picker
    """
    _COMPARE_OPS = [
        ('call_if_eq', '=='),
        ('call_if_ne', '!='),
        ('call_if_lt', '<'),
        ('call_if_gt', '>'),
        ('call_if_le', '<='),
        ('call_if_ge', '>='),
    ]

    def __init__(self, cmd='call_if_eq', var='', value='', label='', parent=None):
        super().__init__(parent)
        self._cmd = cmd
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 2, 4, 2)
        outer.setSpacing(2)

        # Row 1: Variable | operator | value
        row1 = QHBoxLayout()
        row1.addWidget(QLabel('Variable:'))
        self.var_picker = ConstantPicker(ConstantsManager.VARS, prefix='VAR_')
        self.var_picker.setToolTip(_tt('The variable to check'))
        if var:
            self.var_picker.set_constant(var)
        row1.addWidget(self.var_picker, 1)
        self.op_combo = QComboBox()
        self.op_combo.setToolTip(_tt('Comparison: call only when this condition is true'))
        self.op_combo.setMaximumWidth(60)
        for raw, pretty in self._COMPARE_OPS:
            self.op_combo.addItem(pretty, raw)
        idx = self.op_combo.findData(cmd)
        if idx >= 0:
            self.op_combo.setCurrentIndex(idx)
        row1.addWidget(self.op_combo)
        self.value_combo = _make_value_combo(value)
        self.value_combo.setToolTip(_tt('The value to compare against'))
        self.value_combo.setMaximumWidth(100)
        row1.addWidget(self.value_combo)
        outer.addLayout(row1)

        # Row 2: → Target label
        row2 = QHBoxLayout()
        row2.addWidget(QLabel('→ Call:'))
        self.label_combo = _make_label_combo(label)
        self.label_combo.setToolTip(_tt('Call this script label if the condition is true\nExecution returns here after the called script ends'))
        row2.addWidget(self.label_combo, 1)
        outer.addLayout(row2)

    def to_tuple(self):
        op = self.op_combo.currentData() or self._cmd
        return (op, self.var_picker.selected_constant(),
                self.value_combo.currentText().strip(),
                self.label_combo.currentText().strip())

    def friendly_name(self):
        op = self.op_combo.currentData() or self._cmd
        names = dict(self._COMPARE_OPS)
        return f'If Var {names.get(op, "==")} → Call'


class _CallIfFlagWidget(_CommandWidget):
    """Conditional Call (flag) — call_if_set/unset FLAG, LABEL.

    Two-row layout:
      Row 1: Flag picker | set/unset toggle
      Row 2: → Target label picker
    """

    def __init__(self, cmd='call_if_set', flag='', label='', parent=None):
        super().__init__(parent)
        self._is_set = cmd.endswith('_set')
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 2, 4, 2)
        outer.setSpacing(2)

        # Row 1: Flag | set/unset
        row1 = QHBoxLayout()
        row1.addWidget(QLabel('Flag:'))
        self.flag_picker = ConstantPicker(ConstantsManager.FLAGS, prefix='FLAG_')
        self.flag_picker.setToolTip(_tt('The flag to check'))
        if flag:
            self.flag_picker.set_constant(flag)
        row1.addWidget(self.flag_picker, 1)
        self.state_combo = QComboBox()
        self.state_combo.setToolTip(_tt('Call when the flag is SET (ON) or NOT set (OFF)'))
        self.state_combo.addItem('is SET', 'call_if_set')
        self.state_combo.addItem('is NOT set', 'call_if_unset')
        self.state_combo.setCurrentIndex(0 if self._is_set else 1)
        row1.addWidget(self.state_combo)
        outer.addLayout(row1)

        # Row 2: → Target label
        row2 = QHBoxLayout()
        row2.addWidget(QLabel('→ Call:'))
        self.label_combo = _make_label_combo(label)
        self.label_combo.setToolTip(_tt('Call this script label if the flag matches\nExecution returns here after the called script ends'))
        row2.addWidget(self.label_combo, 1)
        outer.addLayout(row2)

    def to_tuple(self):
        cmd = self.state_combo.currentData()
        return (cmd, self.flag_picker.selected_constant(),
                self.label_combo.currentText().strip())

    def friendly_name(self):
        state = 'Set' if self.state_combo.currentData() == 'call_if_set' else 'Not Set'
        return f'If Flag {state} → Call'


class _CallWidget(_CommandWidget):
    """Call Script — searchable label dropdown."""
    def __init__(self, script='', parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Call:'))
        self.label_combo = _make_label_combo(script)
        self.label_combo.setToolTip(_tt('Call a sub-script — execution returns here when it hits "return"'))
        layout.addWidget(self.label_combo, 1)

    def to_tuple(self):
        return ('call', self.label_combo.currentText().strip())

    def friendly_name(self):
        return 'Call Script'


class _GotoWidget(_CommandWidget):
    """Goto — searchable label dropdown."""
    def __init__(self, label='', parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Goto:'))
        self.label_combo = _make_label_combo(label)
        self.label_combo.setToolTip(_tt('Jump to another script label — execution does NOT return'))
        layout.addWidget(self.label_combo, 1)

    def to_tuple(self):
        return ('goto', self.label_combo.currentText().strip())

    def friendly_name(self):
        return 'Goto'


class _EndWidget(_CommandWidget):
    """End Script — no parameters."""
    _header_only = True
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('End Script'))
        layout.addStretch()

    def to_tuple(self):
        return ('end',)

    def friendly_name(self):
        return 'End Script'


class _ReturnWidget(_CommandWidget):
    """Return from Script — no parameters."""
    _header_only = True
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Return from Script'))
        layout.addStretch()

    def to_tuple(self):
        return ('return',)

    def friendly_name(self):
        return 'Return'


class _SpecialWidget(_CommandWidget):
    """Special function call — searchable dropdown of special function names."""
    def __init__(self, special_id='', parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Special:'))
        self.id_combo = QComboBox()
        self.id_combo.setEditable(True)
        self.id_combo.setMinimumWidth(200)
        self.id_combo.setToolTip(_tt(
            'Call a built-in special function by name\n'
            'These are C functions registered in data/specials.inc\n'
            'Type to search — e.g. HealPlayerParty, ShakeScreen'))
        if ConstantsManager.SPECIALS:
            self.id_combo.addItems(ConstantsManager.SPECIALS)
        completer = QCompleter(ConstantsManager.SPECIALS or [], self.id_combo)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.id_combo.setCompleter(completer)
        if special_id:
            idx = self.id_combo.findText(str(special_id))
            if idx >= 0:
                self.id_combo.setCurrentIndex(idx)
            else:
                self.id_combo.setEditText(str(special_id))
        layout.addWidget(self.id_combo, 1)

    def to_tuple(self):
        return ('special', self.id_combo.currentText().strip())

    def friendly_name(self):
        return 'Special Function'


class _SpecialVarWidget(_CommandWidget):
    """Special Var — store result of a special function into a variable."""
    def __init__(self, dest_var='VAR_RESULT', special_id='', parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Dest Var:'))
        self.var_picker = ConstantPicker(ConstantsManager.VARS, prefix='VAR_')
        self.var_picker.setToolTip(_tt('Variable to store the special function result in\nUsually VAR_RESULT'))
        if dest_var:
            self.var_picker.set_constant(dest_var)
        layout.addWidget(self.var_picker, 1)
        layout.addWidget(QLabel('Special:'))
        self.id_combo = QComboBox()
        self.id_combo.setEditable(True)
        self.id_combo.setMinimumWidth(200)
        self.id_combo.setToolTip(_tt('Special function to call — its return value is stored in the variable'))
        if ConstantsManager.SPECIALS:
            self.id_combo.addItems(ConstantsManager.SPECIALS)
        completer = QCompleter(ConstantsManager.SPECIALS or [], self.id_combo)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.id_combo.setCompleter(completer)
        if special_id:
            idx = self.id_combo.findText(str(special_id))
            if idx >= 0:
                self.id_combo.setCurrentIndex(idx)
            else:
                self.id_combo.setEditText(str(special_id))
        layout.addWidget(self.id_combo, 1)

    def to_tuple(self):
        return ('specialvar', f'{self.var_picker.selected_constant()}, '
                f'{self.id_combo.currentText().strip()}')

    def friendly_name(self):
        return 'Special Var (Store Result)'


class _WaitbuttonWidget(_CommandWidget):
    """Wait for Button Press — no parameters."""
    _header_only = True
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Wait for Button Press'))
        layout.addStretch()

    def to_tuple(self):
        return ('waitbuttonpress',)

    def friendly_name(self):
        return 'Wait for Button Press'


# ═════════════════════════════════════════════════════════════════════════════
# Page 2 commands — World & Characters
# ═════════════════════════════════════════════════════════════════════════════

class _WarpWidget(_CommandWidget):
    """Warp Player — map picker + coordinates."""
    def __init__(self, warp_cmd='warp', dest_map='', x=0, y=0, parent=None):
        super().__init__(parent)
        self._cmd = warp_cmd
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        self.map_picker = MapPicker(
            [p.name if hasattr(p, 'name') else str(p)
             for p in ConstantsManager.MAP_NAMES])
        self.map_picker.setToolTip(_tt(
            'Choose the destination map and landing coordinates\n'
            'warp = standard, warpsilent = no transition,\n'
            'warpdoor = door animation, warphole = fall animation'))
        self.map_picker.set_values(dest_map, x, y)
        layout.addWidget(self.map_picker)

    def to_tuple(self):
        return (self._cmd, self.map_picker.map_name(),
                self.map_picker.x(), self.map_picker.y())

    def friendly_name(self):
        names = {
            'warp': 'Warp Player',
            'warpsilent': 'Warp (Silent)',
            'warpdoor': 'Warp Through Door',
            'warphole': 'Fall Through Hole',
            'warpteleport': 'Teleport Player',
        }
        return names.get(self._cmd, 'Warp')


# ── Movement step categories for the Move Route Editor ──────────────────────
# Organized like RMXP's Move Route dialog: columns of buttons by category

_MOVE_STEPS = {
    'Move': [
        ('walk_down', 'Move Down'),
        ('walk_left', 'Move Left'),
        ('walk_right', 'Move Right'),
        ('walk_up', 'Move Up'),
        ('walk_slow_down', 'Move Down (Slow)'),
        ('walk_slow_left', 'Move Left (Slow)'),
        ('walk_slow_right', 'Move Right (Slow)'),
        ('walk_slow_up', 'Move Up (Slow)'),
        ('walk_fast_down', 'Move Down (Fast)'),
        ('walk_fast_left', 'Move Left (Fast)'),
        ('walk_fast_right', 'Move Right (Fast)'),
        ('walk_fast_up', 'Move Up (Fast)'),
        ('walk_faster_down', 'Move Down (Faster)'),
        ('walk_faster_left', 'Move Left (Faster)'),
        ('walk_faster_right', 'Move Right (Faster)'),
        ('walk_faster_up', 'Move Up (Faster)'),
        ('walk_slower_down', 'Move Down (Slower)'),
        ('walk_slower_left', 'Move Left (Slower)'),
        ('walk_slower_right', 'Move Right (Slower)'),
        ('walk_slower_up', 'Move Up (Slower)'),
        ('walk_slowest_down', 'Move Down (Slowest)'),
        ('walk_slowest_left', 'Move Left (Slowest)'),
        ('walk_slowest_right', 'Move Right (Slowest)'),
        ('walk_slowest_up', 'Move Up (Slowest)'),
    ],
    'Jump': [
        ('jump_down', 'Jump Down'),
        ('jump_left', 'Jump Left'),
        ('jump_right', 'Jump Right'),
        ('jump_up', 'Jump Up'),
        ('jump_2_down', 'Jump 2 Down'),
        ('jump_2_left', 'Jump 2 Left'),
        ('jump_2_right', 'Jump 2 Right'),
        ('jump_2_up', 'Jump 2 Up'),
        ('jump_in_place_down', 'Jump in Place (Down)'),
        ('jump_in_place_up', 'Jump in Place (Up)'),
        ('jump_in_place_left', 'Jump in Place (Left)'),
        ('jump_in_place_right', 'Jump in Place (Right)'),
        ('jump_special_down', 'Jump Special Down'),
        ('jump_special_up', 'Jump Special Up'),
        ('jump_special_left', 'Jump Special Left'),
        ('jump_special_right', 'Jump Special Right'),
    ],
    'Turn / Face': [
        ('face_down', 'Face Down'),
        ('face_up', 'Face Up'),
        ('face_left', 'Face Left'),
        ('face_right', 'Face Right'),
        ('face_player', 'Face Player'),
        ('face_away_player', 'Face Away from Player'),
        ('face_original_direction', 'Face Original Direction'),
        ('walk_in_place_down', 'Walk in Place (Down)'),
        ('walk_in_place_up', 'Walk in Place (Up)'),
        ('walk_in_place_left', 'Walk in Place (Left)'),
        ('walk_in_place_right', 'Walk in Place (Right)'),
        ('walk_in_place_fast_down', 'Walk in Place Fast (Down)'),
        ('walk_in_place_fast_up', 'Walk in Place Fast (Up)'),
        ('walk_in_place_fast_left', 'Walk in Place Fast (Left)'),
        ('walk_in_place_fast_right', 'Walk in Place Fast (Right)'),
        ('walk_in_place_faster_down', 'Walk in Place Faster (Down)'),
        ('walk_in_place_faster_up', 'Walk in Place Faster (Up)'),
        ('walk_in_place_faster_left', 'Walk in Place Faster (Left)'),
        ('walk_in_place_faster_right', 'Walk in Place Faster (Right)'),
    ],
    'Slide / Glide': [
        ('slide_down', 'Slide Down'),
        ('slide_up', 'Slide Up'),
        ('slide_left', 'Slide Left'),
        ('slide_right', 'Slide Right'),
        ('glide_down', 'Glide Down'),
        ('glide_up', 'Glide Up'),
        ('glide_left', 'Glide Left'),
        ('glide_right', 'Glide Right'),
    ],
    'Special': [
        ('delay_1', 'Wait (1 frame)'),
        ('delay_2', 'Wait (2 frames)'),
        ('delay_4', 'Wait (4 frames)'),
        ('delay_8', 'Wait (8 frames)'),
        ('delay_16', 'Wait (16 frames)'),
        ('set_invisible', 'Set Invisible'),
        ('set_visible', 'Set Visible'),
        ('disable_anim', 'Disable Animation'),
        ('restore_anim', 'Restore Animation'),
        ('lock_facing_direction', 'Lock Facing Direction'),
        ('unlock_facing_direction', 'Unlock Facing Direction'),
        ('set_fixed_priority', 'Set Fixed Priority'),
        ('clear_fixed_priority', 'Clear Fixed Priority'),
        ('nurse_joy_bow', 'Nurse Joy Bow'),
        ('reveal_trainer', 'Reveal Trainer'),
        ('rock_smash_break', 'Rock Smash Break'),
        ('cut_tree', 'Cut Tree'),
        ('emote_exclamation_mark', 'Emote: !'),
        ('emote_question_mark', 'Emote: ?'),
        ('emote_x', 'Emote: X'),
        ('emote_double_exclamation_mark', 'Emote: !!'),
        ('emote_smile', 'Emote: Smile'),
    ],
    'Run / Spin': [
        ('player_run_down', 'Player Run Down'),
        ('player_run_up', 'Player Run Up'),
        ('player_run_left', 'Player Run Left'),
        ('player_run_right', 'Player Run Right'),
        ('player_run_down_slow', 'Player Run Down (Slow)'),
        ('player_run_up_slow', 'Player Run Up (Slow)'),
        ('player_run_left_slow', 'Player Run Left (Slow)'),
        ('player_run_right_slow', 'Player Run Right (Slow)'),
        ('spin_down', 'Spin Down'),
        ('spin_up', 'Spin Up'),
        ('spin_left', 'Spin Left'),
        ('spin_right', 'Spin Right'),
        ('fly_up', 'Fly Up'),
        ('fly_down', 'Fly Down'),
    ],
}

# Friendly name lookup for display in the step list
_STEP_FRIENDLY: dict[str, str] = {}
for _cat_steps in _MOVE_STEPS.values():
    for _macro, _friendly in _cat_steps:
        _STEP_FRIENDLY[_macro] = _friendly

# ═════════════════════════════════════════════════════════════════════════════
# Camera Move Route — movement macros appropriate for cutscene camera control
# ═════════════════════════════════════════════════════════════════════════════

# Movement macros that get applied to LOCALID_CAMERA via applymovement.
# These go INTO a movement label, not as standalone script commands.
_CAMERA_MOV_STEPS = {
    'Pan': [
        ('walk_down', 'Pan Down'),
        ('walk_up', 'Pan Up'),
        ('walk_left', 'Pan Left'),
        ('walk_right', 'Pan Right'),
        ('walk_slow_down', 'Pan Down (Slow)'),
        ('walk_slow_up', 'Pan Up (Slow)'),
        ('walk_slow_left', 'Pan Left (Slow)'),
        ('walk_slow_right', 'Pan Right (Slow)'),
        ('walk_fast_down', 'Pan Down (Fast)'),
        ('walk_fast_up', 'Pan Up (Fast)'),
        ('walk_fast_left', 'Pan Left (Fast)'),
        ('walk_fast_right', 'Pan Right (Fast)'),
        ('walk_faster_down', 'Pan Down (Faster)'),
        ('walk_faster_up', 'Pan Up (Faster)'),
        ('walk_faster_left', 'Pan Left (Faster)'),
        ('walk_faster_right', 'Pan Right (Faster)'),
        ('walk_slowest_down', 'Pan Down (Slowest)'),
        ('walk_slowest_up', 'Pan Up (Slowest)'),
        ('walk_slowest_left', 'Pan Left (Slowest)'),
        ('walk_slowest_right', 'Pan Right (Slowest)'),
    ],
    'Slide': [
        ('slide_down', 'Slide Down'),
        ('slide_up', 'Slide Up'),
        ('slide_left', 'Slide Left'),
        ('slide_right', 'Slide Right'),
        ('glide_down', 'Glide Down'),
        ('glide_up', 'Glide Up'),
        ('glide_left', 'Glide Left'),
        ('glide_right', 'Glide Right'),
    ],
}

# All camera movement macros (for quick lookup)
_CAMERA_MOV_MACROS: set[str] = set()
for _cat in _CAMERA_MOV_STEPS.values():
    for _macro, _ in _cat:
        _CAMERA_MOV_MACROS.add(_macro)
# Movement-macro delays also go inside the movement label
_CAMERA_MOV_MACROS.update({'delay_1', 'delay_2', 'delay_4', 'delay_8', 'delay_16'})


class _CameraMoveRouteDialog(QDialog):
    """Camera cutscene editor — build a full camera sequence with panning,
    screen effects, shaking, sound, and timing in one dialog.

    Left side: ordered step list (movement macros + script commands mixed).
    Right side: categorized button tabs to add steps.

    On output, movement macros are grouped into applymovement blocks and
    script commands become standalone lines between them.  The full
    SpawnCameraObject / RemoveCameraObject wrapper is generated automatically.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Move Camera — Cutscene Editor')
        self.setMinimumSize(800, 550)
        self.resize(900, 600)

        root = QVBoxLayout(self)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter, 1)

        # ── Left: step list ──────────────────────────────────────────────
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 4, 0)
        ll.addWidget(QLabel('Camera Sequence:'))

        self._step_list = QListWidget()
        self._step_list.setAlternatingRowColors(True)
        ll.addWidget(self._step_list, 1)

        ctrl = QHBoxLayout()
        for label, slot in [('Delete', self._on_delete),
                            ('▲ Up', self._on_up),
                            ('▼ Down', self._on_down),
                            ('Clear All', self._on_clear)]:
            b = QPushButton(label)
            b.clicked.connect(slot)
            ctrl.addWidget(b)
        ctrl.addStretch()
        ll.addLayout(ctrl)
        splitter.addWidget(left)

        # ── Right: categorized buttons ───────────────────────────────────
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(4, 0, 0, 0)
        rl.addWidget(QLabel('Add Step:'))

        tabs = QTabWidget()
        tabs.setTabPosition(QTabWidget.TabPosition.North)

        # Pan tab
        self._add_mov_tab(tabs, 'Pan', _CAMERA_MOV_STEPS['Pan'])
        # Slide tab
        self._add_mov_tab(tabs, 'Slide', _CAMERA_MOV_STEPS['Slide'])
        # Screen tab
        self._add_screen_tab(tabs)
        # Effects tab
        self._add_effects_tab(tabs)
        # Timing tab
        self._add_timing_tab(tabs)
        # Sound tab
        self._add_sound_tab(tabs)

        rl.addWidget(tabs, 1)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    # ── Tab builders ─────────────────────────────────────────────────

    # Tooltips for movement macro buttons
    _MOV_TOOLTIPS = {
        'walk_down': 'Pan camera 1 tile down at normal walk speed',
        'walk_up': 'Pan camera 1 tile up at normal walk speed',
        'walk_left': 'Pan camera 1 tile left at normal walk speed',
        'walk_right': 'Pan camera 1 tile right at normal walk speed',
        'walk_slow_down': 'Pan camera 1 tile down at slow speed (half normal)',
        'walk_slow_up': 'Pan camera 1 tile up at slow speed (half normal)',
        'walk_slow_left': 'Pan camera 1 tile left at slow speed (half normal)',
        'walk_slow_right': 'Pan camera 1 tile right at slow speed (half normal)',
        'walk_fast_down': 'Pan camera 1 tile down at fast speed (2× normal)',
        'walk_fast_up': 'Pan camera 1 tile up at fast speed (2× normal)',
        'walk_fast_left': 'Pan camera 1 tile left at fast speed (2× normal)',
        'walk_fast_right': 'Pan camera 1 tile right at fast speed (2× normal)',
        'walk_faster_down': 'Pan camera 1 tile down at very fast speed (4× normal)',
        'walk_faster_up': 'Pan camera 1 tile up at very fast speed (4× normal)',
        'walk_faster_left': 'Pan camera 1 tile left at very fast speed (4× normal)',
        'walk_faster_right': 'Pan camera 1 tile right at very fast speed (4× normal)',
        'walk_slowest_down': 'Pan camera 1 tile down at slowest speed (quarter normal)',
        'walk_slowest_up': 'Pan camera 1 tile up at slowest speed (quarter normal)',
        'walk_slowest_left': 'Pan camera 1 tile left at slowest speed (quarter normal)',
        'walk_slowest_right': 'Pan camera 1 tile right at slowest speed (quarter normal)',
        'slide_down': 'Slide camera 1 tile down — smooth motion, no walk animation',
        'slide_up': 'Slide camera 1 tile up — smooth motion, no walk animation',
        'slide_left': 'Slide camera 1 tile left — smooth motion, no walk animation',
        'slide_right': 'Slide camera 1 tile right — smooth motion, no walk animation',
        'glide_down': 'Glide camera 1 tile down — slower smooth slide',
        'glide_up': 'Glide camera 1 tile up — slower smooth slide',
        'glide_left': 'Glide camera 1 tile left — slower smooth slide',
        'glide_right': 'Glide camera 1 tile right — slower smooth slide',
    }

    _TAB_SUMMARIES = {
        'Pan': 'Move the camera tile-by-tile with walk animation.\nEach button pans 1 tile. Add multiple to pan further.',
        'Slide': 'Move the camera smoothly without walk animation.\nSlide = normal speed, Glide = slower.',
    }

    def _add_mov_tab(self, tabs: QTabWidget, name: str,
                     steps: list[tuple[str, str]]):
        """Add a tab with movement macro buttons."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(4, 4, 4, 4)
        vbox.setSpacing(4)
        summary = self._TAB_SUMMARIES.get(name, '')
        if summary:
            lbl = QLabel(summary)
            lbl.setWordWrap(True)
            lbl.setStyleSheet('color: #888; font-size: 11px; padding-bottom: 4px;')
            vbox.addWidget(lbl)
        from PyQt6.QtWidgets import QGridLayout
        grid_w = QWidget()
        grid = QGridLayout(grid_w)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(2)
        for i, (macro, friendly) in enumerate(steps):
            btn = QPushButton(friendly)
            btn.setStyleSheet('text-align: left; padding: 3px 8px;')
            tip = self._MOV_TOOLTIPS.get(macro, '')
            if tip:
                btn.setToolTip(_tt(tip))
            btn.clicked.connect(
                lambda checked, m=macro, f=friendly: self._add_mov(m, f))
            grid.addWidget(btn, i // 2, i % 2)
        grid.setRowStretch(len(steps) // 2 + 1, 1)
        vbox.addWidget(grid_w, 1)
        scroll.setWidget(container)
        tabs.addTab(scroll, name)

    def _add_screen_tab(self, tabs: QTabWidget):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(4, 4, 4, 4)
        vbox.setSpacing(4)
        lbl = QLabel('Fade the screen to/from black or white, or control\ncave flash darkness. Use custom speed for cinematic fades.')
        lbl.setWordWrap(True)
        lbl.setStyleSheet('color: #888; font-size: 11px; padding-bottom: 4px;')
        vbox.addWidget(lbl)
        from PyQt6.QtWidgets import QGridLayout
        grid_w = QWidget()
        grid = QGridLayout(grid_w)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(2)
        screen_buttons = [
            ('Fade to Black', ('fadescreen', 'FADE_TO_BLACK'),
             'Instantly fade the screen to black (default speed)'),
            ('Fade from Black', ('fadescreen', 'FADE_FROM_BLACK'),
             'Instantly fade the screen back in from black (default speed)'),
            ('Fade to White', ('fadescreen', 'FADE_TO_WHITE'),
             'Instantly fade the screen to white (default speed)'),
            ('Fade from White', ('fadescreen', 'FADE_FROM_WHITE'),
             'Instantly fade the screen back in from white (default speed)'),
        ]
        for i, (friendly, cmd, tip) in enumerate(screen_buttons):
            btn = QPushButton(friendly)
            btn.setStyleSheet('text-align: left; padding: 3px 8px;')
            btn.setToolTip(_tt(tip))
            btn.clicked.connect(
                lambda checked, c=cmd, f=friendly: self._add_cmd(c, f))
            grid.addWidget(btn, i // 2, i % 2)

        row = len(screen_buttons) // 2 + 1
        # Fade with speed — prompts for speed
        btn = QPushButton('Fade to Black (Custom Speed)...')
        btn.setStyleSheet('text-align: left; padding: 3px 8px;')
        btn.setToolTip(_tt('Fade to black with a custom speed — lower is slower, higher is faster'))
        btn.clicked.connect(lambda: self._add_fade_speed('FADE_TO_BLACK'))
        grid.addWidget(btn, row, 0)
        btn2 = QPushButton('Fade from Black (Custom Speed)...')
        btn2.setStyleSheet('text-align: left; padding: 3px 8px;')
        btn2.setToolTip(_tt('Fade in from black with a custom speed — lower is slower, higher is faster'))
        btn2.clicked.connect(lambda: self._add_fade_speed('FADE_FROM_BLACK'))
        grid.addWidget(btn2, row, 1)
        row += 1
        btn3 = QPushButton('Fade to White (Custom Speed)...')
        btn3.setStyleSheet('text-align: left; padding: 3px 8px;')
        btn3.setToolTip(_tt('Fade to white with a custom speed — lower is slower, higher is faster'))
        btn3.clicked.connect(lambda: self._add_fade_speed('FADE_TO_WHITE'))
        grid.addWidget(btn3, row, 0)
        btn4 = QPushButton('Fade from White (Custom Speed)...')
        btn4.setStyleSheet('text-align: left; padding: 3px 8px;')
        btn4.setToolTip(_tt('Fade in from white with a custom speed — lower is slower, higher is faster'))
        btn4.clicked.connect(lambda: self._add_fade_speed('FADE_FROM_WHITE'))
        grid.addWidget(btn4, row, 1)
        row += 1
        # Flash level
        btn5 = QPushButton('Set Flash Level...')
        btn5.setStyleSheet('text-align: left; padding: 3px 8px;')
        btn5.setToolTip(_tt('Instantly set the cave flash darkness level\n0 = fully lit, 8 = completely dark\nUsed for Flash/dark cave effects'))
        btn5.clicked.connect(self._add_flash_level)
        grid.addWidget(btn5, row, 0)
        btn6 = QPushButton('Animate Flash...')
        btn6.setStyleSheet('text-align: left; padding: 3px 8px;')
        btn6.setToolTip(_tt('Smoothly animate the flash level to a target over time\n0 = fully lit, 8 = completely dark\nUnlike Set Flash Level, this transitions gradually'))
        btn6.clicked.connect(self._add_animate_flash)
        grid.addWidget(btn6, row, 1)

        grid.setRowStretch(row + 1, 1)
        vbox.addWidget(grid_w, 1)
        scroll.setWidget(container)
        tabs.addTab(scroll, 'Screen')

    def _add_effects_tab(self, tabs: QTabWidget):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(4, 4, 4, 4)
        vbox.setSpacing(4)
        lbl = QLabel('Screen shake, weather changes, and visual effects.\nWeather takes effect immediately on the current map.')
        lbl.setWordWrap(True)
        lbl.setStyleSheet('color: #888; font-size: 11px; padding-bottom: 4px;')
        vbox.addWidget(lbl)
        from PyQt6.QtWidgets import QGridLayout
        grid_w = QWidget()
        grid = QGridLayout(grid_w)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(2)
        effects = [
            ('Shake Screen',
             lambda: self._add_cmd(('special', 'ShakeScreen'), 'Shake Screen'),
             'Earthquake-style screen shake — plays a brief rumble effect'),
            ('Set Weather...',
             self._add_weather,
             'Change the map weather (rain, snow, fog, sandstorm, etc.)\nTakes effect immediately'),
            ('Reset Weather',
             lambda: self._add_multi_cmd(
                 [('resetweather',), ('doweather',)], 'Reset Weather'),
             'Clear any custom weather and return to the map default'),
        ]
        for i, (friendly, slot, tip) in enumerate(effects):
            btn = QPushButton(friendly)
            btn.setStyleSheet('text-align: left; padding: 3px 8px;')
            btn.setToolTip(_tt(tip))
            btn.clicked.connect(slot)
            grid.addWidget(btn, i // 2, i % 2)
        grid.setRowStretch(len(effects) // 2 + 1, 1)
        vbox.addWidget(grid_w, 1)
        scroll.setWidget(container)
        tabs.addTab(scroll, 'Effects')

    def _add_timing_tab(self, tabs: QTabWidget):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(4, 4, 4, 4)
        vbox.setSpacing(4)
        lbl = QLabel('⏱ Pauses stay inside the pan (camera holds position).\n⏸ Delays break the pan and pause everything.')
        lbl.setWordWrap(True)
        lbl.setStyleSheet('color: #888; font-size: 11px; padding-bottom: 4px;')
        vbox.addWidget(lbl)
        from PyQt6.QtWidgets import QGridLayout
        grid_w = QWidget()
        grid = QGridLayout(grid_w)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(2)
        # Movement-macro pauses (go into the movement label)
        mov_pauses = [
            ('delay_1', 'Pause (1 frame)', 'Brief pause (~1/60th second)\nStays inside the pan — camera holds position'),
            ('delay_2', 'Pause (2 frames)', 'Short pause (~1/30th second)\nStays inside the pan — camera holds position'),
            ('delay_4', 'Pause (4 frames)', 'Medium pause (~1/15th second)\nStays inside the pan — camera holds position'),
            ('delay_8', 'Pause (8 frames)', 'Longer pause (~1/8th second)\nStays inside the pan — camera holds position'),
            ('delay_16', 'Pause (16 frames)', 'Long pause (~1/4 second)\nStays inside the pan — camera holds position'),
        ]
        for i, (macro, friendly, tip) in enumerate(mov_pauses):
            btn = QPushButton(f'⏱ {friendly}')
            btn.setToolTip(_tt(tip))
            btn.setStyleSheet('text-align: left; padding: 3px 8px;')
            btn.clicked.connect(
                lambda checked, m=macro, f=friendly: self._add_mov(m, f))
            grid.addWidget(btn, i // 2, i % 2)

        row = len(mov_pauses) // 2 + 1
        # Script-command delays (break the movement block)
        btn = QPushButton('⏸ Delay (Custom Frames)...')
        btn.setToolTip(_tt('Pause the entire script for a number of frames\n60 frames ≈ 1 second\nNote: this ends the current pan — use movement pauses above to pause mid-pan'))
        btn.setStyleSheet('text-align: left; padding: 3px 8px;')
        btn.clicked.connect(self._add_delay)
        grid.addWidget(btn, row, 0)
        btn2 = QPushButton('⏸ Wait for Button Press')
        btn2.setToolTip(_tt('Freeze the cutscene until the player presses A or B\nUseful for dramatic pauses or "press to continue" moments'))
        btn2.setStyleSheet('text-align: left; padding: 3px 8px;')
        btn2.clicked.connect(
            lambda: self._add_cmd(('waitbuttonpress',), 'Wait for Button Press'))
        grid.addWidget(btn2, row, 1)
        grid.setRowStretch(row + 1, 1)
        vbox.addWidget(grid_w, 1)
        scroll.setWidget(container)
        tabs.addTab(scroll, 'Timing')

    def _add_sound_tab(self, tabs: QTabWidget):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(4, 4, 4, 4)
        vbox.setSpacing(4)
        lbl = QLabel('Sound effects, background music, fanfares, and species cries.\nUse "Wait for..." to pause until the sound finishes.')
        lbl.setWordWrap(True)
        lbl.setStyleSheet('color: #888; font-size: 11px; padding-bottom: 4px;')
        vbox.addWidget(lbl)
        from PyQt6.QtWidgets import QGridLayout
        grid_w = QWidget()
        grid = QGridLayout(grid_w)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(2)
        sound_buttons = [
            ('Play Sound Effect...', self._add_sfx,
             'Play a one-shot sound effect (SE_PIN, SE_DOOR, etc.)'),
            ('Wait for Sound', lambda: self._add_cmd(
                ('waitse',), 'Wait for Sound'),
             'Pause the script until the current sound effect finishes playing'),
            ('Play Music...', self._add_music,
             'Change the background music — replaces the current BGM'),
            ('Fade Out Music...', self._add_fade_out_music,
             'Gradually fade the current background music to silence'),
            ('Fade In Music...', self._add_fade_in_music,
             'Gradually fade the background music back in from silence'),
            ('Play Fanfare...', self._add_fanfare,
             'Play a short jingle over the BGM (item get, level up, etc.)'),
            ('Wait for Fanfare', lambda: self._add_cmd(
                ('waitfanfare',), 'Wait for Fanfare'),
             'Pause the script until the fanfare jingle finishes'),
            ('Play Cry...', self._add_cry,
             'Play a species cry sound'),
            ('Wait for Cry', lambda: self._add_cmd(
                ('waitmoncry',), 'Wait for Cry'),
             'Pause the script until the species cry finishes playing'),
        ]
        for i, (friendly, slot, tip) in enumerate(sound_buttons):
            btn = QPushButton(friendly)
            btn.setStyleSheet('text-align: left; padding: 3px 8px;')
            btn.setToolTip(_tt(tip))
            btn.clicked.connect(slot)
            grid.addWidget(btn, i // 2, i % 2)
        grid.setRowStretch(len(sound_buttons) // 2 + 1, 1)
        vbox.addWidget(grid_w, 1)
        scroll.setWidget(container)
        tabs.addTab(scroll, 'Sound')

    # ── Step list operations ─────────────────────────────────────────

    def _add_mov(self, macro: str, friendly: str):
        """Add a movement macro step (goes into the movement label)."""
        item = QListWidgetItem(f'  ▸ {friendly}')
        item.setData(Qt.ItemDataRole.UserRole, ('mov', macro))
        item.setForeground(QColor(_CATEGORY_COLORS.get('movement', '#8b2252')))
        self._step_list.addItem(item)
        self._step_list.setCurrentItem(item)

    def _add_cmd(self, cmd_tuple: tuple, friendly: str):
        """Add a script command step (breaks the movement block)."""
        item = QListWidgetItem(f'⬥ {friendly}')
        item.setData(Qt.ItemDataRole.UserRole, ('cmd',) + cmd_tuple)
        item.setForeground(QColor(_CATEGORY_COLORS.get('screen', '#16a085')))
        self._step_list.addItem(item)
        self._step_list.setCurrentItem(item)

    def _add_multi_cmd(self, cmds: list[tuple], friendly: str):
        """Add multiple script commands as one logical step."""
        item = QListWidgetItem(f'⬥ {friendly}')
        item.setData(Qt.ItemDataRole.UserRole, ('multi',) + tuple(cmds))
        item.setForeground(QColor(_CATEGORY_COLORS.get('screen', '#16a085')))
        self._step_list.addItem(item)
        self._step_list.setCurrentItem(item)

    # ── Parameter prompts ────────────────────────────────────────────

    def _add_fade_speed(self, mode: str):
        speed, ok = QInputDialog.getInt(
            self, 'Fade Speed', 'Speed (1=slow, 10=fast):', 4, 1, 10)
        if ok:
            friendly = mode.replace('FADE_', '').replace('_', ' ').title()
            self._add_cmd(
                ('fadescreenspeed', mode, str(speed)),
                f'Fade {friendly} (speed {speed})')

    def _add_flash_level(self):
        level, ok = QInputDialog.getInt(
            self, 'Flash Level',
            'Level (0=bright, 8=full dark):', 0, 0, 8)
        if ok:
            self._add_cmd(('setflashlevel', str(level)),
                          f'Set Flash Level: {level}')

    def _add_animate_flash(self):
        level, ok = QInputDialog.getInt(
            self, 'Animate Flash',
            'Animate to level (0=bright, 8=dark):', 0, 0, 8)
        if ok:
            self._add_cmd(('animateflash', str(level)),
                          f'Animate Flash → {level}')

    def _add_delay(self):
        frames, ok = QInputDialog.getInt(
            self, 'Delay', 'Frames (~60 per second):', 30, 1, 600)
        if ok:
            secs = frames / 60
            self._add_cmd(('delay', str(frames)),
                          f'Delay: {frames} frames (~{secs:.1f}s)')

    def _add_weather(self):
        weathers = sorted(ConstantsManager.WEATHER) if ConstantsManager.WEATHER else [
            'WEATHER_NONE', 'WEATHER_SUNNY_CLOUDS', 'WEATHER_SUNNY',
            'WEATHER_RAIN', 'WEATHER_SNOW', 'WEATHER_RAIN_THUNDERSTORM',
            'WEATHER_FOG_HORIZONTAL', 'WEATHER_VOLCANIC_ASH',
            'WEATHER_SANDSTORM', 'WEATHER_FOG_DIAGONAL',
            'WEATHER_UNDERWATER', 'WEATHER_SHADE',
        ]
        choice, ok = QInputDialog.getItem(
            self, 'Set Weather', 'Weather type:', weathers, editable=False)
        if ok:
            short = choice.replace('WEATHER_', '').replace('_', ' ').title()
            self._add_multi_cmd(
                [('setweather', choice), ('doweather',)],
                f'Set Weather: {short}')

    def _add_sfx(self):
        sfx = sorted(ConstantsManager.SFX) if hasattr(ConstantsManager, 'SFX') and ConstantsManager.SFX else []
        if sfx:
            choice, ok = QInputDialog.getItem(
                self, 'Play Sound Effect', 'Sound:', sfx, editable=True)
        else:
            choice, ok = QInputDialog.getText(
                self, 'Play Sound Effect', 'Sound constant (e.g. SE_PIN):')
        if ok and choice:
            short = choice.replace('SE_', '').replace('_', ' ').title()
            self._add_cmd(('playse', choice), f'Play SE: {short}')

    def _add_music(self):
        songs = sorted(ConstantsManager.SONGS) if hasattr(ConstantsManager, 'SONGS') and ConstantsManager.SONGS else []
        if songs:
            choice, ok = QInputDialog.getItem(
                self, 'Play Music', 'Song:', songs, editable=True)
        else:
            choice, ok = QInputDialog.getText(
                self, 'Play Music', 'Music constant (e.g. MUS_FOLLOW_ME):')
        if ok and choice:
            short = choice.replace('MUS_', '').replace('_', ' ').title()
            self._add_cmd(('playbgm', choice, '0'), f'Play BGM: {short}')

    def _add_fade_out_music(self):
        speed, ok = QInputDialog.getInt(
            self, 'Fade Out Music', 'Speed (1=slow, 10=fast):', 4, 1, 10)
        if ok:
            self._add_cmd(('fadeoutbgm', str(speed)),
                          f'Fade Out Music (speed {speed})')

    def _add_fade_in_music(self):
        speed, ok = QInputDialog.getInt(
            self, 'Fade In Music', 'Speed (1=slow, 10=fast):', 4, 1, 10)
        if ok:
            self._add_cmd(('fadeinbgm', str(speed)),
                          f'Fade In Music (speed {speed})')

    def _add_fanfare(self):
        songs = sorted(ConstantsManager.SONGS) if hasattr(ConstantsManager, 'SONGS') and ConstantsManager.SONGS else []
        if songs:
            choice, ok = QInputDialog.getItem(
                self, 'Play Fanfare', 'Fanfare:', songs, editable=True)
        else:
            choice, ok = QInputDialog.getText(
                self, 'Play Fanfare', 'Fanfare constant:')
        if ok and choice:
            short = choice.replace('MUS_', '').replace('_', ' ').title()
            self._add_cmd(('playfanfare', choice), f'Fanfare: {short}')

    def _add_cry(self):
        species = sorted(ConstantsManager.SPECIES) if ConstantsManager.SPECIES else []
        if species:
            choice, ok = QInputDialog.getItem(
                self, 'Play Cry', 'Species:', species, editable=True)
        else:
            choice, ok = QInputDialog.getText(
                self, 'Play Cry', 'Species constant:')
        if ok and choice:
            short = choice.replace('SPECIES_', '').replace('_', ' ').title()
            self._add_cmd(('playmoncry', choice, 'CRY_MODE_NORMAL'),
                          f'Cry: {short}')

    # ── List controls ────────────────────────────────────────────────

    def _on_delete(self):
        r = self._step_list.currentRow()
        if r >= 0:
            self._step_list.takeItem(r)

    def _on_up(self):
        r = self._step_list.currentRow()
        if r > 0:
            item = self._step_list.takeItem(r)
            self._step_list.insertItem(r - 1, item)
            self._step_list.setCurrentRow(r - 1)

    def _on_down(self):
        r = self._step_list.currentRow()
        if r < self._step_list.count() - 1:
            item = self._step_list.takeItem(r)
            self._step_list.insertItem(r + 1, item)
            self._step_list.setCurrentRow(r + 1)

    def _on_clear(self):
        self._step_list.clear()

    # ── Output ───────────────────────────────────────────────────────

    def get_output(self, map_name: str,
                   all_scripts: dict) -> tuple[list[tuple], dict[str, list]]:
        """Build the command sequence and movement labels.

        Returns (commands, movements) where:
          commands = list of command tuples to insert into the script
          movements = dict of movement_label → [step_tuples] to register
        """
        commands: list[tuple] = []
        movements: dict[str, list] = {}
        current_mov: list[tuple] = []
        mov_n = 1

        def _next_label() -> str:
            nonlocal mov_n
            base = f'{map_name}_CameraMovement_'
            while f'{base}{mov_n}' in all_scripts or f'{base}{mov_n}' in movements:
                mov_n += 1
            label = f'{base}{mov_n}'
            mov_n += 1
            return label

        def _flush_mov():
            if current_mov:
                label = _next_label()
                steps = list(current_mov) + [('step_end',)]
                movements[label] = steps
                commands.append(('applymovement', 'LOCALID_CAMERA', label))
                commands.append(('waitmovement', '0'))
                current_mov.clear()

        commands.append(('special', 'SpawnCameraObject'))

        for i in range(self._step_list.count()):
            item = self._step_list.item(i)
            data = item.data(Qt.ItemDataRole.UserRole)
            if not data:
                continue

            step_type = data[0]
            if step_type == 'mov':
                current_mov.append((data[1],))
            elif step_type == 'cmd':
                _flush_mov()
                commands.append(data[1:])
            elif step_type == 'multi':
                _flush_mov()
                for cmd in data[1:]:
                    commands.append(cmd)

        _flush_mov()
        commands.append(('special', 'RemoveCameraObject'))
        return commands, movements

    @staticmethod
    def create_sequence(map_name: str, all_scripts: dict,
                        parent=None) -> tuple[list[tuple], dict[str, list]] | None:
        """Show the dialog and return (commands, movements) or None."""
        dlg = _CameraMoveRouteDialog(parent)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            return dlg.get_output(map_name, all_scripts)
        return None


class _MoveRouteDialog(QDialog):
    """RMXP-style Move Route Editor popup.

    Left side: ordered list of movement steps with Up/Down/Delete buttons.
    Right side: categorized button grid to add steps.
    """

    def __init__(self, steps: list[tuple], parent=None):
        super().__init__(parent)
        self.setWindowTitle('Edit Move Route')
        self.setMinimumSize(750, 500)
        self.resize(850, 550)

        root = QVBoxLayout(self)

        # Main area: step list (left) + button grid (right)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter, 1)

        # ── Left: step list ──────────────────────────────────────────────
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 4, 0)
        left_layout.addWidget(QLabel('Movement Steps:'))

        self._step_list = QListWidget()
        self._step_list.setAlternatingRowColors(True)
        left_layout.addWidget(self._step_list, 1)

        # Control buttons
        ctrl_row = QHBoxLayout()
        btn_del = QPushButton('Delete')
        btn_del.clicked.connect(self._on_delete)
        ctrl_row.addWidget(btn_del)
        btn_up = QPushButton('▲ Up')
        btn_up.clicked.connect(self._on_move_up)
        ctrl_row.addWidget(btn_up)
        btn_down = QPushButton('▼ Down')
        btn_down.clicked.connect(self._on_move_down)
        ctrl_row.addWidget(btn_down)
        btn_clear = QPushButton('Clear All')
        btn_clear.clicked.connect(self._on_clear)
        ctrl_row.addWidget(btn_clear)
        ctrl_row.addStretch()
        left_layout.addLayout(ctrl_row)

        splitter.addWidget(left)

        # ── Right: categorized buttons in a tabbed view ──────────────────
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(4, 0, 0, 0)
        right_layout.addWidget(QLabel('Add Step:'))

        cat_tabs = QTabWidget()
        cat_tabs.setTabPosition(QTabWidget.TabPosition.North)

        for cat_name, cat_steps in _MOVE_STEPS.items():
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            container = QWidget()
            from PyQt6.QtWidgets import QGridLayout
            grid = QGridLayout(container)
            grid.setContentsMargins(4, 4, 4, 4)
            grid.setSpacing(2)
            cols = 2
            for i, (macro, friendly) in enumerate(cat_steps):
                btn = QPushButton(friendly)
                btn.setStyleSheet('text-align: left; padding: 3px 8px;')
                btn.clicked.connect(lambda checked, m=macro: self._add_step(m))
                grid.addWidget(btn, i // cols, i % cols)
            # Fill remaining space
            grid.setRowStretch(len(cat_steps) // cols + 1, 1)
            scroll.setWidget(container)
            cat_tabs.addTab(scroll, cat_name)

        right_layout.addWidget(cat_tabs, 1)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)

        # Dialog buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        # Populate with existing steps
        for step in steps:
            macro = step[0] if step else ''
            if macro == 'step_end':
                continue
            self._add_step_item(macro)

    def _add_step(self, macro: str):
        """Add a step button was clicked — append to the list."""
        self._add_step_item(macro)

    def _add_step_item(self, macro: str):
        """Add a step to the list widget."""
        friendly = _STEP_FRIENDLY.get(macro, macro.replace('_', ' ').title())
        item = QListWidgetItem(f'$> {friendly}')
        item.setData(Qt.ItemDataRole.UserRole, macro)
        self._step_list.addItem(item)
        self._step_list.setCurrentItem(item)

    def _on_delete(self):
        row = self._step_list.currentRow()
        if row >= 0:
            self._step_list.takeItem(row)

    def _on_move_up(self):
        row = self._step_list.currentRow()
        if row > 0:
            item = self._step_list.takeItem(row)
            self._step_list.insertItem(row - 1, item)
            self._step_list.setCurrentRow(row - 1)

    def _on_move_down(self):
        row = self._step_list.currentRow()
        if row < self._step_list.count() - 1:
            item = self._step_list.takeItem(row)
            self._step_list.insertItem(row + 1, item)
            self._step_list.setCurrentRow(row + 1)

    def _on_clear(self):
        self._step_list.clear()

    def get_steps(self) -> list[tuple]:
        """Return the current step list as tuples (macro_name,)."""
        result = []
        for i in range(self._step_list.count()):
            item = self._step_list.item(i)
            macro = item.data(Qt.ItemDataRole.UserRole)
            if macro:
                result.append((macro,))
        result.append(('step_end',))
        return result

    @staticmethod
    def edit_steps(steps: list[tuple], parent=None) -> list[tuple] | None:
        """Show the dialog and return the new steps, or None if cancelled."""
        dlg = _MoveRouteDialog(steps, parent)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            return dlg.get_steps()
        return None


class _ApplyMovementWidget(_CommandWidget):
    """Apply Movement — object dropdown + movement label dropdown + Edit Steps button."""
    def __init__(self, target='', movement='', parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Target:'))
        self.target_combo = _make_object_combo(target)
        self.target_combo.setToolTip(_tt('Which object to move — local ID or OBJ_EVENT_ID_PLAYER'))
        layout.addWidget(self.target_combo)
        layout.addWidget(QLabel('Movement:'))
        self.move_combo = QComboBox()
        self.move_combo.setEditable(True)
        self.move_combo.setToolTip(_tt('Movement label — a list of steps defined in scripts.inc'))
        self.move_combo.setMinimumWidth(180)
        # Populate with movement labels from current script + common movements
        from eventide.backend.eventide_utils import COMMON_MOVEMENT_LABELS
        all_moves = list(_MOVEMENT_LABELS)
        for lbl in COMMON_MOVEMENT_LABELS:
            if lbl not in all_moves:
                all_moves.append(lbl)
        self.move_combo.addItems(sorted(all_moves))
        completer = QCompleter(
            sorted(all_moves), self.move_combo)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.move_combo.setCompleter(completer)
        if movement:
            idx = self.move_combo.findText(movement)
            if idx >= 0:
                self.move_combo.setCurrentIndex(idx)
            else:
                self.move_combo.setEditText(movement)
        layout.addWidget(self.move_combo, 1)

        # Edit Steps button — opens the Move Route dialog
        self._edit_btn = QPushButton('Edit Steps...')
        self._edit_btn.setToolTip(_tt('Open RMXP-style Move Route editor'))
        self._edit_btn.clicked.connect(self._on_edit_steps)
        layout.addWidget(self._edit_btn)

        # Track modified steps (None = not modified, use original)
        self._modified_steps: list[tuple] | None = None
        self._movement_label = movement

    def _on_edit_steps(self):
        """Open the Move Route dialog with the current movement steps."""
        label = self.move_combo.currentText().strip()
        steps = list(_ALL_SCRIPTS.get(label, []))
        result = _MoveRouteDialog.edit_steps(steps, self)
        if result is not None:
            self._modified_steps = result
            self._movement_label = label

    def get_modified_steps(self) -> tuple[str, list[tuple]] | None:
        """Return (movement_label, new_steps) if steps were modified."""
        if self._modified_steps is not None:
            label = self.move_combo.currentText().strip()
            return (label, self._modified_steps)
        return None

    def to_tuple(self):
        return ('applymovement', self.target_combo.currentText().strip(),
                self.move_combo.currentText().strip())

    def friendly_name(self):
        return 'Apply Movement'


class _WaitMovementWidget(_CommandWidget):
    """Wait for Movement — optional target object dropdown."""
    def __init__(self, target='0', parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Wait for:'))
        self.target_combo = _make_object_combo(target)
        self.target_combo.setToolTip(_tt('Which object to wait for — 0 waits for all moving objects to finish'))
        # Add "0" as a special entry meaning "wait for all"
        if self.target_combo.findText('0') < 0:
            self.target_combo.insertItem(0, '0')
        if not target or target == '0':
            self.target_combo.setCurrentIndex(self.target_combo.findText('0'))
        layout.addWidget(self.target_combo)
        layout.addWidget(QLabel('(0 = all objects)'))
        layout.addStretch()

    def to_tuple(self):
        t = self.target_combo.currentText().strip()
        return ('waitmovement', t) if t and t != '0' else ('waitmovement',)

    def friendly_name(self):
        return 'Wait for Movement'


class _RemoveObjectWidget(_CommandWidget):
    """Remove NPC/Object — object dropdown."""
    def __init__(self, obj_id='', parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Remove:'))
        self.id_combo = _make_object_combo(obj_id)
        layout.addWidget(self.id_combo)
        layout.addStretch()

    def to_tuple(self):
        return ('removeobject', self.id_combo.currentText().strip())

    def friendly_name(self):
        return 'Remove NPC/Object'


class _AddObjectWidget(_CommandWidget):
    """Add NPC/Object — object dropdown."""
    def __init__(self, obj_id='', parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Add:'))
        self.id_combo = _make_object_combo(obj_id)
        layout.addWidget(self.id_combo)
        layout.addStretch()

    def to_tuple(self):
        return ('addobject', self.id_combo.currentText().strip())

    def friendly_name(self):
        return 'Add NPC/Object'


class _ShowObjectWidget(_CommandWidget):
    """Show NPC at map — object dropdown + map dropdown."""
    def __init__(self, obj_id='', map_id='', parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Show:'))
        self.id_combo = _make_object_combo(obj_id)
        layout.addWidget(self.id_combo)
        layout.addWidget(QLabel('on Map:'))
        self.map_combo = ConstantPicker(ConstantsManager.MAP_CONSTANTS, prefix='MAP_')
        if map_id:
            self.map_combo.set_constant(map_id)
        layout.addWidget(self.map_combo, 1)

    def to_tuple(self):
        return ('showobjectat', f'{self.id_combo.currentText()}, {self.map_combo.selected_constant()}')

    def friendly_name(self):
        return 'Show NPC/Object'


class _HideObjectWidget(_CommandWidget):
    """Hide NPC at map — object dropdown + map dropdown."""
    def __init__(self, obj_id='', map_id='', parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Hide:'))
        self.id_combo = _make_object_combo(obj_id)
        layout.addWidget(self.id_combo)
        layout.addWidget(QLabel('on Map:'))
        self.map_combo = ConstantPicker(ConstantsManager.MAP_CONSTANTS, prefix='MAP_')
        if map_id:
            self.map_combo.set_constant(map_id)
        layout.addWidget(self.map_combo, 1)

    def to_tuple(self):
        return ('hideobjectat', f'{self.id_combo.currentText()}, {self.map_combo.selected_constant()}')

    def friendly_name(self):
        return 'Hide NPC/Object'


class _FacePlayerWidget(_CommandWidget):
    """Make NPC face player — no parameters."""
    _header_only = True
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Make NPC Face Player'))
        layout.addStretch()

    def to_tuple(self):
        return ('faceplayer',)

    def friendly_name(self):
        return 'Face Player'


class _TurnObjectWidget(_CommandWidget):
    """Turn NPC — object dropdown + direction dropdown."""
    def __init__(self, obj_id='', direction='DIR_DOWN', parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Object:'))
        self.id_combo = _make_object_combo(obj_id)
        layout.addWidget(self.id_combo)
        layout.addWidget(QLabel('Direction:'))
        self.dir_combo = QComboBox()
        for raw, pretty in ConstantsManager.DIRECTIONS:
            self.dir_combo.addItem(f'{pretty}  ({raw})', raw)
        idx = self.dir_combo.findData(direction)
        if idx >= 0:
            self.dir_combo.setCurrentIndex(idx)
        layout.addWidget(self.dir_combo)
        layout.addStretch()

    def to_tuple(self):
        return ('turnobject', f'{self.id_combo.currentText()}, {self.dir_combo.currentData()}')

    def friendly_name(self):
        return 'Turn NPC'


class _SetObjectXYWidget(_CommandWidget):
    """Move NPC to coordinates — object dropdown + x/y spinners."""
    def __init__(self, obj_id='', x=0, y=0, cmd='setobjectxy', parent=None):
        super().__init__(parent)
        self._cmd = cmd
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Object:'))
        self.id_combo = _make_object_combo(obj_id)
        layout.addWidget(self.id_combo)
        layout.addWidget(QLabel('X:'))
        self.x_spin = QSpinBox(); self.x_spin.setRange(0, 999); self.x_spin.setValue(x)
        layout.addWidget(self.x_spin)
        layout.addWidget(QLabel('Y:'))
        self.y_spin = QSpinBox(); self.y_spin.setRange(0, 999); self.y_spin.setValue(y)
        layout.addWidget(self.y_spin)
        layout.addStretch()

    def to_tuple(self):
        return (self._cmd, f'{self.id_combo.currentText()}, {self.x_spin.value()}, {self.y_spin.value()}')

    def friendly_name(self):
        return 'Set NPC Position (Permanent)' if self._cmd == 'setobjectxyperm' else 'Move NPC'


class _LockWidget(_CommandWidget):
    """Lock movement — lock or lockall."""
    _header_only = True
    def __init__(self, cmd='lock', parent=None):
        super().__init__(parent)
        self._cmd = cmd
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Lock All Movement' if cmd == 'lockall' else 'Lock Player Movement'))
        layout.addStretch()

    def to_tuple(self):
        return (self._cmd,)

    def friendly_name(self):
        return 'Lock All Movement' if self._cmd == 'lockall' else 'Lock Player Movement'


class _ReleaseWidget(_CommandWidget):
    """Release movement — release or releaseall."""
    _header_only = True
    def __init__(self, cmd='release', parent=None):
        super().__init__(parent)
        self._cmd = cmd
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Release All Movement' if cmd == 'releaseall' else 'Release Player Movement'))
        layout.addStretch()

    def to_tuple(self):
        return (self._cmd,)

    def friendly_name(self):
        return 'Release All Movement' if self._cmd == 'releaseall' else 'Release Player Movement'


class _FadeScreenWidget(_CommandWidget):
    """Fade Screen — fade type dropdown."""
    def __init__(self, fade_type='FADE_TO_BLACK', parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Fade:'))
        self.combo = QComboBox()
        self.combo.setToolTip(_tt('Screen fade direction — TO fades out, FROM fades back in'))
        self.combo.addItems(ConstantsManager.FADE_TYPES)
        idx = self.combo.findText(fade_type)
        if idx >= 0:
            self.combo.setCurrentIndex(idx)
        layout.addWidget(self.combo)
        layout.addStretch()

    def to_tuple(self):
        return ('fadescreen', self.combo.currentText())

    def friendly_name(self):
        return 'Fade Screen'


class _FadeScreenSpeedWidget(_CommandWidget):
    """Fade Screen with Speed — fade type + speed spinner."""
    def __init__(self, fade_type='FADE_TO_BLACK', speed=0, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Fade:'))
        self.combo = QComboBox()
        self.combo.setToolTip(_tt('Screen fade direction — TO fades out, FROM fades back in'))
        self.combo.addItems(ConstantsManager.FADE_TYPES)
        idx = self.combo.findText(fade_type)
        if idx >= 0:
            self.combo.setCurrentIndex(idx)
        layout.addWidget(self.combo)
        layout.addWidget(QLabel('Speed:'))
        self.speed_spin = QSpinBox()
        self.speed_spin.setRange(0, 16)
        self.speed_spin.setValue(speed)
        self.speed_spin.setToolTip(_tt('Fade speed — lower is slower, higher is faster (0 = default)'))
        layout.addWidget(self.speed_spin)
        layout.addStretch()

    def to_tuple(self):
        return ('fadescreenspeed', f'{self.combo.currentText()}, {self.speed_spin.value()}')

    def friendly_name(self):
        return 'Fade Screen (Speed)'


class _PlaySEWidget(_CommandWidget):
    """Play Sound Effect — SFX picker."""
    def __init__(self, sfx='', parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Sound:'))
        self.picker = ConstantPicker(ConstantsManager.SFX, prefix='SE_')
        self.picker.setToolTip(_tt('Sound effect constant — type to search (e.g. SE_PIN, SE_DOOR)'))
        if sfx:
            self.picker.set_constant(sfx)
        layout.addWidget(self.picker, 1)

        btn_preview = QPushButton("▶")
        btn_preview.setFixedSize(28, 24)
        btn_preview.setToolTip("Preview this sound effect")
        btn_preview.clicked.connect(lambda: _preview_song_cb and _preview_song_cb(self.picker.selected_constant()))
        layout.addWidget(btn_preview)

        btn_stop = QPushButton("■")
        btn_stop.setFixedSize(28, 24)
        btn_stop.setToolTip("Stop preview")
        btn_stop.clicked.connect(lambda: _stop_preview_cb and _stop_preview_cb())
        layout.addWidget(btn_stop)

        btn_open = QPushButton("🔊")
        btn_open.setFixedSize(28, 24)
        btn_open.setToolTip("Open in Sound Editor")
        btn_open.clicked.connect(lambda: _open_in_sound_editor_cb and _open_in_sound_editor_cb(self.picker.selected_constant()))
        layout.addWidget(btn_open)

    def to_tuple(self):
        return ('playse', self.picker.selected_constant())

    def friendly_name(self):
        return 'Play Sound Effect'


class _WaitSEWidget(_CommandWidget):
    """Wait for Sound Effect — no parameters."""
    _header_only = True
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Wait for Sound Effect'))
        layout.addStretch()

    def to_tuple(self):
        return ('waitse',)

    def friendly_name(self):
        return 'Wait for Sound'


class _PlayFanfareWidget(_CommandWidget):
    """Play Fanfare — music picker (fanfares use MUS_ constants)."""
    def __init__(self, fanfare='', parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Fanfare:'))
        self.picker = ConstantPicker(ConstantsManager.MUSIC, prefix='MUS_')
        self.picker.setToolTip(_tt('Short jingle that plays over the BGM (item get, level up, etc.)'))
        if fanfare:
            self.picker.set_constant(fanfare)
        layout.addWidget(self.picker, 1)

        btn_preview = QPushButton("▶")
        btn_preview.setFixedSize(28, 24)
        btn_preview.setToolTip("Preview this fanfare")
        btn_preview.clicked.connect(lambda: _preview_song_cb and _preview_song_cb(self.picker.selected_constant()))
        layout.addWidget(btn_preview)

        btn_stop = QPushButton("■")
        btn_stop.setFixedSize(28, 24)
        btn_stop.setToolTip("Stop preview")
        btn_stop.clicked.connect(lambda: _stop_preview_cb and _stop_preview_cb())
        layout.addWidget(btn_stop)

        btn_open = QPushButton("🔊")
        btn_open.setFixedSize(28, 24)
        btn_open.setToolTip("Open in Sound Editor")
        btn_open.clicked.connect(lambda: _open_in_sound_editor_cb and _open_in_sound_editor_cb(self.picker.selected_constant()))
        layout.addWidget(btn_open)

    def to_tuple(self):
        return ('playfanfare', self.picker.selected_constant())

    def friendly_name(self):
        return 'Play Fanfare'


class _WaitFanfareWidget(_CommandWidget):
    """Wait for Fanfare — no parameters."""
    _header_only = True
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Wait for Fanfare'))
        layout.addStretch()

    def to_tuple(self):
        return ('waitfanfare',)

    def friendly_name(self):
        return 'Wait for Fanfare'


class _PlayBGMWidget(_CommandWidget):
    """Play Background Music — music picker + loop checkbox."""
    def __init__(self, music='', loop=True, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Music:'))
        self.picker = ConstantPicker(ConstantsManager.MUSIC, prefix='MUS_')
        self.picker.setToolTip(_tt('Background music track to play — replaces the current BGM'))
        if music:
            self.picker.set_constant(music)
        layout.addWidget(self.picker, 1)
        self.loop_check = QCheckBox('Loop')
        self.loop_check.setChecked(loop)
        layout.addWidget(self.loop_check)

        btn_preview = QPushButton("▶")
        btn_preview.setFixedSize(28, 24)
        btn_preview.setToolTip("Preview this song")
        btn_preview.clicked.connect(lambda: _preview_song_cb and _preview_song_cb(self.picker.selected_constant()))
        layout.addWidget(btn_preview)

        btn_stop = QPushButton("■")
        btn_stop.setFixedSize(28, 24)
        btn_stop.setToolTip("Stop preview")
        btn_stop.clicked.connect(lambda: _stop_preview_cb and _stop_preview_cb())
        layout.addWidget(btn_stop)

        btn_open = QPushButton("🔊")
        btn_open.setFixedSize(28, 24)
        btn_open.setToolTip("Open in Sound Editor")
        btn_open.clicked.connect(lambda: _open_in_sound_editor_cb and _open_in_sound_editor_cb(self.picker.selected_constant()))
        layout.addWidget(btn_open)

    def to_tuple(self):
        loop_val = 1 if self.loop_check.isChecked() else 0
        return ('playbgm', self.picker.selected_constant(), loop_val)

    def friendly_name(self):
        return 'Play Music'


class _FadeOutBGMWidget(_CommandWidget):
    """Fade Out Background Music — speed spinner."""
    def __init__(self, speed=4, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Fade Out Music — Speed:'))
        self.speed_spin = QSpinBox()
        self.speed_spin.setRange(0, 16)
        self.speed_spin.setValue(speed)
        layout.addWidget(self.speed_spin)
        layout.addStretch()

    def to_tuple(self):
        return ('fadeoutbgm', str(self.speed_spin.value()))

    def friendly_name(self):
        return 'Fade Out Music'


class _FadeInBGMWidget(_CommandWidget):
    """Fade In Background Music — speed spinner."""
    def __init__(self, speed=4, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Fade In Music — Speed:'))
        self.speed_spin = QSpinBox()
        self.speed_spin.setRange(0, 16)
        self.speed_spin.setValue(speed)
        layout.addWidget(self.speed_spin)
        layout.addStretch()

    def to_tuple(self):
        return ('fadeinbgm', str(self.speed_spin.value()))

    def friendly_name(self):
        return 'Fade In Music'


class _SetWeatherWidget(_CommandWidget):
    """Set Weather — weather type picker."""
    def __init__(self, weather='WEATHER_NONE', parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Weather:'))
        self.picker = ConstantPicker(ConstantsManager.WEATHER, prefix='WEATHER_')
        if weather:
            self.picker.set_constant(weather)
        layout.addWidget(self.picker, 1)

    def to_tuple(self):
        return ('setweather', self.picker.selected_constant())

    def friendly_name(self):
        return 'Set Weather'


class _DoWeatherWidget(_CommandWidget):
    """Trigger Weather Effect — no parameters."""
    _header_only = True
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Trigger Weather Effect'))
        layout.addStretch()

    def to_tuple(self):
        return ('doweather',)

    def friendly_name(self):
        return 'Trigger Weather'


class _ResetWeatherWidget(_CommandWidget):
    """Reset Weather — no parameters."""
    _header_only = True
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Reset Weather'))
        layout.addStretch()

    def to_tuple(self):
        return ('resetweather',)

    def friendly_name(self):
        return 'Reset Weather'


class _DelayWidget(_CommandWidget):
    """Wait (Timed Delay) — frame count spinner with seconds label."""
    def __init__(self, frames=60, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Delay:'))
        self.frames_spin = QSpinBox()
        self.frames_spin.setRange(1, 9999)
        self.frames_spin.setValue(frames)
        self.frames_spin.setToolTip(_tt('Number of frames to pause (60 frames ≈ 1 second)'))
        layout.addWidget(self.frames_spin)
        layout.addWidget(QLabel('frames'))
        self.sec_label = QLabel()
        layout.addWidget(self.sec_label)
        self.frames_spin.valueChanged.connect(self._update_sec)
        self._update_sec()
        layout.addStretch()

    def _update_sec(self):
        secs = self.frames_spin.value() / 60.0
        self.sec_label.setText(f'(≈ {secs:.1f}s)')

    def to_tuple(self):
        return ('delay', str(self.frames_spin.value()))

    def friendly_name(self):
        return 'Delay (Frames)'


class _SetFlashLevelWidget(_CommandWidget):
    """Set Flash Level — level spinner."""
    def __init__(self, level=0, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Flash Level:'))
        self.level_spin = QSpinBox()
        self.level_spin.setRange(0, 10)
        self.level_spin.setValue(level)
        self.level_spin.setToolTip(_tt('Cave flash darkness level\n0 = fully lit, 8+ = completely dark\nUsed for Flash/dark cave effects'))
        layout.addWidget(self.level_spin)
        layout.addStretch()

    def to_tuple(self):
        return ('setflashlevel', str(self.level_spin.value()))

    def friendly_name(self):
        return 'Set Flash Level'


class _PlayMonCryWidget(_CommandWidget):
    """Play Pokemon Cry — species picker + mode."""
    def __init__(self, species='', mode='0', parent=None):
        super().__init__(parent)
        self._mode = str(mode).strip() if mode else '0'
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Species:'))
        self.picker = ConstantPicker(ConstantsManager.SPECIES, prefix='SPECIES_')
        self.picker.setToolTip(_tt('Play the cry sound for this species'))
        if species:
            self.picker.set_constant(species)
        layout.addWidget(self.picker, 1)
        self.preview_btn = QPushButton('\u25B6 Preview')
        self.preview_btn.setToolTip(_tt('Play this cry from the project .wav sample'))
        self.preview_btn.setMaximumWidth(90)
        self.preview_btn.clicked.connect(self._on_preview)
        layout.addWidget(self.preview_btn)

    def _on_preview(self):
        try:
            from ui.audio_player import get_audio_player
            player = get_audio_player()
            root = ConstantsManager._root
            if root is not None:
                player.set_project_root(str(root))
            species = self.picker.selected_constant() or ''
            if not species:
                return
            player.play_cry(species)
        except Exception:
            pass

    def to_tuple(self):
        return ('playmoncry', f'{self.picker.selected_constant()}, {self._mode}')

    def friendly_name(self):
        return 'Play Pokémon Cry'


# ═════════════════════════════════════════════════════════════════════════════
# Page 3 commands — Battles, Items & System
# ═════════════════════════════════════════════════════════════════════════════

def _make_text_field(label_combo, texts: dict, label_text: str = 'Text:'):
    """Create a label combo + editable text area pair.

    Returns (group_widget, combo, text_edit) where:
      - combo is the label dropdown (selects which text label to reference)
      - text_edit is a GameTextEdit showing/editing the actual text content
    When the user picks a different label, the text area updates to show
    that label's content.  When the user edits the text, it updates
    the in-memory text data so it gets saved with the next save.
    """
    from ui.game_text_edit import GameTextEdit

    group = QWidget()
    vbox = QVBoxLayout(group)
    vbox.setContentsMargins(0, 0, 0, 0)
    vbox.setSpacing(2)

    # Label dropdown row
    row = QHBoxLayout()
    row.addWidget(QLabel(f'{label_text}'))
    row.addWidget(label_combo, 1)
    vbox.addLayout(row)

    # Editable text content — uses GameTextEdit for character limits,
    # {COMMAND} highlighting, and proper escape code display
    text_edit = GameTextEdit(max_chars_per_line=36, max_lines=20)
    text_edit.setMaximumHeight(80)
    text_edit.setPlaceholderText('(select a label above to edit text)')

    # Load initial text from label
    current_label = label_combo.currentText().strip()
    if current_label and current_label in texts:
        text_edit.set_eventide_text(texts[current_label])

    def _on_label_changed(new_text):
        lbl = new_text.strip()
        if lbl in texts:
            text_edit.set_eventide_text(texts[lbl])
        else:
            text_edit.set_eventide_text('')

    def _on_text_edited():
        lbl = label_combo.currentText().strip()
        if not lbl:
            return
        texts[lbl] = text_edit.get_eventide_text()

    label_combo.currentTextChanged.connect(_on_label_changed)
    text_edit.connectChanged(_on_text_edited)
    vbox.addWidget(text_edit)

    return group, label_combo, text_edit


class _TrainerBattleWidget(_CommandWidget):
    """Trainer Battle — variant-aware widget for pokefirered battle macros.

    pokefirered uses named macro variants instead of a numeric type field:
        trainerbattle_single   TRAINER, INTRO, DEFEAT [, CONTINUE [, NO_MUSIC]]
        trainerbattle_double   TRAINER, INTRO, DEFEAT, NOT_ENOUGH [, CONTINUE [, NO_MUSIC]]
        trainerbattle_no_intro TRAINER, DEFEAT
        trainerbattle_earlyrival TRAINER, FLAGS, DEFEAT, VICTORY
    The type is encoded in the command name, NOT as an argument.

    Each text label field has an inline text editor below it showing the
    actual dialogue content.  Edits update the in-memory text data and
    get written to text.inc on save.
    """

    _VARIANT_LABELS = {
        'trainerbattle_single': 'Single',
        'trainerbattle_double': 'Double',
        'trainerbattle_no_intro': 'No Intro',
        'trainerbattle_earlyrival': 'Early Rival',
        'trainerbattle_rematch': 'Rematch',
        'trainerbattle_rematch_double': 'Rematch Double',
        'trainerbattle': 'Generic',
    }

    def __init__(self, variant='trainerbattle_single', trainer='',
                 intro_text='', defeat_text='', extra1='', extra2='',
                 parent=None):
        super().__init__(parent)
        self._variant = variant
        self._texts = _ALL_SCRIPTS.get('__texts__', {})
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)

        # Row 1: variant label + trainer
        row1 = QHBoxLayout()
        type_label = self._VARIANT_LABELS.get(variant, variant)
        row1.addWidget(QLabel(f'<b>{type_label}</b>'))
        row1.addWidget(QLabel('Trainer:'))
        self.trainer_picker = ConstantPicker(ConstantsManager.TRAINERS, prefix='TRAINER_')
        self.trainer_picker.setToolTip(_tt('Trainer constant from trainers.h — defines their party and AI'))
        if trainer:
            self.trainer_picker.set_constant(trainer)
        row1.addWidget(self.trainer_picker, 1)
        layout.addLayout(row1)

        # Text label fields with inline editors
        self.intro_combo = None
        self.defeat_combo = None
        self.extra1_combo = None
        self.extra2_combo = None
        self._intro_edit = None
        self._defeat_edit = None

        if variant == 'trainerbattle_no_intro':
            # Only defeat text
            combo = _make_label_combo(defeat_text)
            combo.setPlaceholderText('defeat text label')
            grp, self.defeat_combo, self._defeat_edit = _make_text_field(
                combo, self._texts, 'Defeat:')
            layout.addWidget(grp)

        elif variant == 'trainerbattle_earlyrival':
            # FLAGS field (not a text label — just a constant)
            flags_row = QHBoxLayout()
            flags_row.addWidget(QLabel('Flags:'))
            self.extra1_combo = _make_label_combo(intro_text)
            self.extra1_combo.setPlaceholderText('battle flags constant')
            flags_row.addWidget(self.extra1_combo, 1)
            layout.addLayout(flags_row)
            # Defeat text
            combo = _make_label_combo(defeat_text)
            combo.setPlaceholderText('defeat text label')
            grp, self.defeat_combo, self._defeat_edit = _make_text_field(
                combo, self._texts, 'Defeat:')
            layout.addWidget(grp)
            # Victory text
            combo2 = _make_label_combo(extra1)
            combo2.setPlaceholderText('victory text label')
            grp2, self.extra2_combo, _ = _make_text_field(
                combo2, self._texts, 'Victory:')
            layout.addWidget(grp2)

        else:
            # Single & Double: INTRO + DEFEAT with text editors
            combo_i = _make_label_combo(intro_text)
            combo_i.setPlaceholderText('intro text label')
            grp_i, self.intro_combo, self._intro_edit = _make_text_field(
                combo_i, self._texts, 'Intro:')
            layout.addWidget(grp_i)

            combo_d = _make_label_combo(defeat_text)
            combo_d.setPlaceholderText('defeat text label')
            grp_d, self.defeat_combo, self._defeat_edit = _make_text_field(
                combo_d, self._texts, 'Defeat:')
            layout.addWidget(grp_d)

            if variant in ('trainerbattle_double', 'trainerbattle_rematch_double'):
                # Not enough pokemon text
                combo_ne = _make_label_combo(extra1)
                combo_ne.setPlaceholderText('not enough pokemon text')
                grp_ne, self.extra1_combo, _ = _make_text_field(
                    combo_ne, self._texts, 'Not Enough Pokémon:')
                layout.addWidget(grp_ne)

        # Continue script row (single/double only, not a text label)
        if variant in ('trainerbattle_single', 'trainerbattle_double'):
            cont_row = QHBoxLayout()
            cont_row.addWidget(QLabel('Continue Script:'))
            cont_val = extra1 if variant == 'trainerbattle_single' else extra2
            self.extra2_combo = _make_label_combo(cont_val)
            self.extra2_combo.setPlaceholderText('(optional continue script)')
            cont_row.addWidget(self.extra2_combo, 1)
            layout.addLayout(cont_row)

    def to_tuple(self):
        trainer = self.trainer_picker.selected_constant()

        if self._variant == 'trainerbattle_no_intro':
            defeat = self.defeat_combo.currentText().strip()
            return (self._variant, f'{trainer}, {defeat}')

        elif self._variant == 'trainerbattle_earlyrival':
            flags = self.extra1_combo.currentText().strip()
            defeat = self.defeat_combo.currentText().strip()
            victory = self.extra2_combo.currentText().strip()
            return (self._variant, f'{trainer}, {flags}, {defeat}, {victory}')

        elif self._variant in ('trainerbattle_double', 'trainerbattle_rematch_double'):
            intro = self.intro_combo.currentText().strip()
            defeat = self.defeat_combo.currentText().strip()
            not_enough = self.extra1_combo.currentText().strip()
            parts = [trainer, intro, defeat, not_enough]
            cont = self.extra2_combo.currentText().strip() if self.extra2_combo else ''
            if cont:
                parts.append(cont)
            return (self._variant, ', '.join(parts))

        else:  # trainerbattle_single, trainerbattle_rematch, or generic
            intro = self.intro_combo.currentText().strip()
            defeat = self.defeat_combo.currentText().strip()
            parts = [trainer, intro, defeat]
            cont = self.extra2_combo.currentText().strip() if self.extra2_combo else ''
            if cont:
                parts.append(cont)
            return (self._variant, ', '.join(parts))

    def friendly_name(self):
        type_label = self._VARIANT_LABELS.get(self._variant, 'Trainer Battle')
        return f'Trainer Battle ({type_label})'


class _WildBattleWidget(_CommandWidget):
    """Wild Battle — species picker + level + shiny."""
    def __init__(self, species='', level=1, shiny=False, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Species:'))
        self.species_picker = ConstantPicker(ConstantsManager.SPECIES, prefix='SPECIES_')
        if species:
            self.species_picker.set_constant(species)
        layout.addWidget(self.species_picker, 1)
        layout.addWidget(QLabel('Level:'))
        self.level_spin = QSpinBox()
        self.level_spin.setRange(1, 100)
        self.level_spin.setValue(level)
        layout.addWidget(self.level_spin)
        self.shiny_check = QCheckBox('Shiny')
        self.shiny_check.setChecked(shiny)
        layout.addWidget(self.shiny_check)

    def to_tuple(self):
        return ('wildbattle', self.species_picker.selected_constant(),
                self.level_spin.value(), self.shiny_check.isChecked())

    def friendly_name(self):
        return 'Wild Battle'


class _GiveMonWidget(_CommandWidget):
    """Give Pokemon — species, level, held item, 4 moves."""
    def __init__(self, species='', level=5, item='ITEM_NONE',
                 moves=None, parent=None):
        super().__init__(parent)
        moves = moves or ['MOVE_NONE'] * 4
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel('Species:'))
        self.species_picker = ConstantPicker(ConstantsManager.SPECIES, prefix='SPECIES_')
        self.species_picker.setToolTip(_tt('Species to give the player'))
        if species:
            self.species_picker.set_constant(species)
        row1.addWidget(self.species_picker, 1)
        row1.addWidget(QLabel('Level:'))
        self.level_spin = QSpinBox()
        self.level_spin.setRange(1, 100)
        self.level_spin.setValue(level)
        self.level_spin.setToolTip(_tt('Level of the gifted creature (1–100)'))
        row1.addWidget(self.level_spin)
        row1.addWidget(QLabel('Item:'))
        self.item_picker = ConstantPicker(ConstantsManager.ITEMS, prefix='ITEM_')
        self.item_picker.setToolTip(_tt('Held item — ITEM_NONE for no item'))
        self.item_picker.set_constant(item)
        row1.addWidget(self.item_picker, 1)
        layout.addLayout(row1)

        # Moves row — MOVE_NONE means "auto-fill from learnset by level"
        row2 = QHBoxLayout()
        row2.addWidget(QLabel('Moves:'))
        self.move_pickers = []
        for i in range(4):
            mp = ConstantPicker(ConstantsManager.MOVES, prefix='MOVE_')
            move_val = moves[i] if i < len(moves) else 'MOVE_NONE'
            mp.set_constant(move_val)
            mp.setToolTip(_tt('MOVE_NONE = auto-fill from learnset by level'))
            mp.setMaximumWidth(200)
            row2.addWidget(mp)
            self.move_pickers.append(mp)
        layout.addLayout(row2)
        note = QLabel('💡 None = game auto-fills moves from learnset at the given level')
        note.setStyleSheet('color: #7f8c8d; font-size: 11px; font-style: italic;')
        layout.addWidget(note)

    def to_tuple(self):
        species = self.species_picker.selected_constant()
        level = self.level_spin.value()
        item = self.item_picker.selected_constant()
        moves = [mp.selected_constant() for mp in self.move_pickers]
        args = f'{species}, {level}, {item}'
        # If ANY move is explicitly set, output all 4 slots
        # (pokefirered expects all 4 positional args when moves are specified)
        has_custom = any(m and m != 'MOVE_NONE' for m in moves)
        if has_custom:
            for m in moves:
                args += f', {m or "MOVE_NONE"}'
        return ('givemon', args)

    def friendly_name(self):
        return 'Give Pokémon'


class _GiveEggWidget(_CommandWidget):
    """Give Egg — species picker."""
    def __init__(self, species='', parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Species:'))
        self.picker = ConstantPicker(ConstantsManager.SPECIES, prefix='SPECIES_')
        self.picker.setToolTip(_tt('Species of the egg to give the player'))
        if species:
            self.picker.set_constant(species)
        layout.addWidget(self.picker, 1)

    def to_tuple(self):
        return ('giveegg', self.picker.selected_constant())

    def friendly_name(self):
        return 'Give Egg'


class _FindItemWidget(_CommandWidget):
    """Find Item (item ball pickup) — item picker only, no quantity."""
    def __init__(self, item='', parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Item:'))
        self.item_picker = ConstantPicker(ConstantsManager.ITEMS, prefix='ITEM_')
        self.item_picker.setToolTip(_tt('Item the player picks up from a ground item ball'))
        if item:
            self.item_picker.set_constant(item)
        layout.addWidget(self.item_picker, 1)

    def to_tuple(self):
        return ('finditem', self.item_picker.selected_constant())

    def friendly_name(self):
        return 'Find Item'


class _CheckItemSpaceWidget(_CommandWidget):
    """Check Item Space — item picker + quantity."""
    def __init__(self, item='', quantity=1, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Item:'))
        self.item_picker = ConstantPicker(ConstantsManager.ITEMS, prefix='ITEM_')
        self.item_picker.setToolTip(_tt('Check if the player has room for this item'))
        if item:
            self.item_picker.set_constant(item)
        layout.addWidget(self.item_picker, 1)
        layout.addWidget(QLabel('Qty:'))
        self.qty_spin = QSpinBox()
        self.qty_spin.setRange(1, 999)
        self.qty_spin.setValue(quantity)
        self.qty_spin.setToolTip(_tt('How many of the item to check space for'))
        layout.addWidget(self.qty_spin)

    def to_tuple(self):
        item = self.item_picker.selected_constant()
        qty = self.qty_spin.value()
        if qty > 1:
            return ('checkitemspace', f'{item}, {qty}')
        return ('checkitemspace', item)

    def friendly_name(self):
        return 'Check Item Space'


class _GiveItemWidget(_CommandWidget):
    """Give Item — item picker + quantity."""
    def __init__(self, item='', quantity=1, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Item:'))
        self.item_picker = ConstantPicker(ConstantsManager.ITEMS, prefix='ITEM_')
        self.item_picker.setToolTip(_tt('Item to give the player — added to their bag'))
        if item:
            self.item_picker.set_constant(item)
        layout.addWidget(self.item_picker, 1)
        layout.addWidget(QLabel('Qty:'))
        self.qty_spin = QSpinBox()
        self.qty_spin.setRange(1, 999)
        self.qty_spin.setValue(quantity)
        self.qty_spin.setToolTip(_tt('How many to give'))
        layout.addWidget(self.qty_spin)

    def to_tuple(self):
        item = self.item_picker.selected_constant()
        qty = self.qty_spin.value()
        if qty > 1:
            return ('additem', f'{item}, {qty}')
        return ('additem', item)

    def friendly_name(self):
        return 'Give Item'


class _RemoveItemWidget(_CommandWidget):
    """Remove Item — item picker + quantity."""
    def __init__(self, item='', quantity=1, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Item:'))
        self.item_picker = ConstantPicker(ConstantsManager.ITEMS, prefix='ITEM_')
        self.item_picker.setToolTip(_tt('Item to remove from the player\'s bag'))
        if item:
            self.item_picker.set_constant(item)
        layout.addWidget(self.item_picker, 1)
        layout.addWidget(QLabel('Qty:'))
        self.qty_spin = QSpinBox()
        self.qty_spin.setRange(1, 999)
        self.qty_spin.setValue(quantity)
        self.qty_spin.setToolTip(_tt('How many to remove'))
        layout.addWidget(self.qty_spin)

    def to_tuple(self):
        item = self.item_picker.selected_constant()
        qty = self.qty_spin.value()
        if qty > 1:
            return ('removeitem', f'{item}, {qty}')
        return ('removeitem', item)

    def friendly_name(self):
        return 'Remove Item'


class _CheckItemWidget(_CommandWidget):
    """Check for Item — item picker + quantity."""
    def __init__(self, item='', quantity=1, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Item:'))
        self.item_picker = ConstantPicker(ConstantsManager.ITEMS, prefix='ITEM_')
        self.item_picker.setToolTip(_tt('Check if the player has this item in their bag'))
        if item:
            self.item_picker.set_constant(item)
        layout.addWidget(self.item_picker, 1)
        layout.addWidget(QLabel('Qty:'))
        self.qty_spin = QSpinBox()
        self.qty_spin.setRange(1, 999)
        self.qty_spin.setValue(quantity)
        self.qty_spin.setToolTip(_tt('Minimum quantity to check for'))
        layout.addWidget(self.qty_spin)

    def to_tuple(self):
        item = self.item_picker.selected_constant()
        qty = self.qty_spin.value()
        if qty > 1:
            return ('checkitem', f'{item}, {qty}')
        return ('checkitem', item)

    def friendly_name(self):
        return 'Check for Item'


class _AddMoneyWidget(_CommandWidget):
    """Give Money — amount spinner."""
    def __init__(self, amount=100, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Give Money — Amount:'))
        self.amount_spin = QSpinBox()
        self.amount_spin.setRange(1, 999999)
        self.amount_spin.setValue(amount)
        self.amount_spin.setToolTip(_tt('Amount of money to add to the player\'s wallet'))
        layout.addWidget(self.amount_spin)
        layout.addStretch()

    def to_tuple(self):
        return ('addmoney', f'{self.amount_spin.value()}, 0')

    def friendly_name(self):
        return 'Give Money'


class _RemoveMoneyWidget(_CommandWidget):
    """Take Money — amount spinner."""
    def __init__(self, amount=100, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Take Money — Amount:'))
        self.amount_spin = QSpinBox()
        self.amount_spin.setRange(1, 999999)
        self.amount_spin.setValue(amount)
        self.amount_spin.setToolTip(_tt('Amount of money to take from the player'))
        layout.addWidget(self.amount_spin)
        layout.addStretch()

    def to_tuple(self):
        return ('removemoney', f'{self.amount_spin.value()}, 0')

    def friendly_name(self):
        return 'Take Money'


class _CheckMoneyWidget(_CommandWidget):
    """Check Money — amount spinner."""
    def __init__(self, amount=100, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Check Money — Amount:'))
        self.amount_spin = QSpinBox()
        self.amount_spin.setRange(1, 999999)
        self.amount_spin.setValue(amount)
        self.amount_spin.setToolTip(_tt('Check if the player has at least this much money'))
        layout.addWidget(self.amount_spin)
        layout.addStretch()

    def to_tuple(self):
        return ('checkmoney', f'{self.amount_spin.value()}, 0')

    def friendly_name(self):
        return 'Check Money'


class _AddCoinsWidget(_CommandWidget):
    """Give Coins — amount spinner."""
    def __init__(self, amount=1, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Give Coins — Amount:'))
        self.amount_spin = QSpinBox()
        self.amount_spin.setRange(1, 9999)
        self.amount_spin.setValue(amount)
        self.amount_spin.setToolTip(_tt('Game Corner coins to add'))
        layout.addWidget(self.amount_spin)
        layout.addStretch()

    def to_tuple(self):
        return ('addcoins', str(self.amount_spin.value()))

    def friendly_name(self):
        return 'Give Coins'


class _RemoveCoinsWidget(_CommandWidget):
    """Take Coins."""
    def __init__(self, amount=1, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Take Coins — Amount:'))
        self.amount_spin = QSpinBox()
        self.amount_spin.setRange(1, 9999)
        self.amount_spin.setValue(amount)
        self.amount_spin.setToolTip(_tt('Game Corner coins to remove'))
        layout.addWidget(self.amount_spin)
        layout.addStretch()

    def to_tuple(self):
        return ('removecoins', str(self.amount_spin.value()))

    def friendly_name(self):
        return 'Take Coins'


class _SetRespawnWidget(_CommandWidget):
    """Set Respawn Point — heal location picker."""
    def __init__(self, location='', parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Respawn:'))
        self.picker = ConstantPicker(
            ConstantsManager.HEAL_LOCATIONS, prefix='HEAL_LOCATION_')
        if location:
            self.picker.set_constant(location)
        layout.addWidget(self.picker, 1)

    def to_tuple(self):
        return ('setrespawn', self.picker.selected_constant())

    def friendly_name(self):
        return 'Set Respawn Point'


class _CheckPartyMoveWidget(_CommandWidget):
    """Check Party for Move — move picker."""
    def __init__(self, move='', parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Move:'))
        self.picker = ConstantPicker(ConstantsManager.MOVES, prefix='MOVE_')
        self.picker.setToolTip(_tt('Check if any party member knows this move (used for field moves like Cut, Surf)'))
        if move:
            self.picker.set_constant(move)
        layout.addWidget(self.picker, 1)

    def to_tuple(self):
        return ('checkpartymove', self.picker.selected_constant())

    def friendly_name(self):
        return 'Check Party for Move'


class _BufferSpeciesWidget(_CommandWidget):
    """Buffer Species Name — buffer slot (0-2) + species picker."""
    def __init__(self, slot='0', species='', parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Buffer:'))
        self.slot_spin = QSpinBox()
        self.slot_spin.setRange(0, 2)
        self.slot_spin.setValue(int(slot) if str(slot).isdigit() else 0)
        self.slot_spin.setMaximumWidth(50)
        self.slot_spin.setToolTip(_tt('Buffer slot (0–2) — use {STR_VAR_1/2/3} in text to display it'))
        layout.addWidget(self.slot_spin)
        layout.addWidget(QLabel('Species:'))
        self.picker = ConstantPicker(ConstantsManager.SPECIES, prefix='SPECIES_')
        self.picker.setToolTip(_tt('Species name to store in the text buffer'))
        if species:
            self.picker.set_constant(species)
        layout.addWidget(self.picker, 1)

    def to_tuple(self):
        return ('bufferspeciesname', f'{self.slot_spin.value()}, {self.picker.selected_constant()}')

    def friendly_name(self):
        return 'Buffer Species Name'


class _BufferItemWidget(_CommandWidget):
    """Buffer Item Name — buffer slot (0-2) + item picker."""
    def __init__(self, slot='0', item='', parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Buffer:'))
        self.slot_spin = QSpinBox()
        self.slot_spin.setRange(0, 2)
        self.slot_spin.setValue(int(slot) if str(slot).isdigit() else 0)
        self.slot_spin.setMaximumWidth(50)
        self.slot_spin.setToolTip(_tt('Buffer slot (0–2) — use {STR_VAR_1/2/3} in text to display it'))
        layout.addWidget(self.slot_spin)
        layout.addWidget(QLabel('Item:'))
        self.picker = ConstantPicker(ConstantsManager.ITEMS, prefix='ITEM_')
        self.picker.setToolTip(_tt('Item name to store in the text buffer'))
        if item:
            self.picker.set_constant(item)
        layout.addWidget(self.picker, 1)

    def to_tuple(self):
        return ('bufferitemname', f'{self.slot_spin.value()}, {self.picker.selected_constant()}')

    def friendly_name(self):
        return 'Buffer Item Name'


class _BufferMoveWidget(_CommandWidget):
    """Buffer Move Name — buffer slot (0-2) + move picker."""
    def __init__(self, slot='0', move='', parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Buffer:'))
        self.slot_spin = QSpinBox()
        self.slot_spin.setRange(0, 2)
        self.slot_spin.setValue(int(slot) if str(slot).isdigit() else 0)
        self.slot_spin.setToolTip(_tt('Buffer slot (0–2) — use {STR_VAR_1/2/3} in text to display it'))
        self.slot_spin.setMaximumWidth(50)
        layout.addWidget(self.slot_spin)
        layout.addWidget(QLabel('Move:'))
        self.picker = ConstantPicker(ConstantsManager.MOVES, prefix='MOVE_')
        if move:
            self.picker.set_constant(move)
        layout.addWidget(self.picker, 1)

    def to_tuple(self):
        return ('buffermovename', f'{self.slot_spin.value()}, {self.picker.selected_constant()}')

    def friendly_name(self):
        return 'Buffer Move Name'


# ═════════════════════════════════════════════════════════════════════════════
# Additional command widgets — Doors, Decorations, Misc
# ═════════════════════════════════════════════════════════════════════════════

class _OpenDoorWidget(_CommandWidget):
    """Open Door — x, y coordinates."""
    def __init__(self, x=0, y=0, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Open Door — X:'))
        self.x_spin = QSpinBox(); self.x_spin.setRange(0, 999); self.x_spin.setValue(x)
        layout.addWidget(self.x_spin)
        layout.addWidget(QLabel('Y:'))
        self.y_spin = QSpinBox(); self.y_spin.setRange(0, 999); self.y_spin.setValue(y)
        layout.addWidget(self.y_spin)
        layout.addStretch()

    def to_tuple(self):
        return ('opendoor', f'{self.x_spin.value()}, {self.y_spin.value()}')

    def friendly_name(self):
        return 'Open Door'


class _CloseDoorWidget(_CommandWidget):
    """Close Door — x, y coordinates."""
    def __init__(self, x=0, y=0, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Close Door — X:'))
        self.x_spin = QSpinBox(); self.x_spin.setRange(0, 999); self.x_spin.setValue(x)
        layout.addWidget(self.x_spin)
        layout.addWidget(QLabel('Y:'))
        self.y_spin = QSpinBox(); self.y_spin.setRange(0, 999); self.y_spin.setValue(y)
        layout.addWidget(self.y_spin)
        layout.addStretch()

    def to_tuple(self):
        return ('closedoor', f'{self.x_spin.value()}, {self.y_spin.value()}')

    def friendly_name(self):
        return 'Close Door'


class _WaitDoorAnimWidget(_CommandWidget):
    """Wait for Door Animation — no parameters."""
    _header_only = True
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Wait for Door Animation'))
        layout.addStretch()

    def to_tuple(self):
        return ('waitdooranim',)

    def friendly_name(self):
        return 'Wait for Door Animation'


class _AddDecorationWidget(_CommandWidget):
    """Add Decoration — decoration picker."""
    def __init__(self, decor='', parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Decoration:'))
        self.picker = ConstantPicker(ConstantsManager.DECORATIONS, prefix='DECOR_')
        if decor:
            self.picker.set_constant(decor)
        layout.addWidget(self.picker, 1)

    def to_tuple(self):
        return ('adddecoration', self.picker.selected_constant())

    def friendly_name(self):
        return 'Add Decoration'


class _RemoveDecorationWidget(_CommandWidget):
    """Remove Decoration — decoration picker."""
    def __init__(self, decor='', parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Decoration:'))
        self.picker = ConstantPicker(ConstantsManager.DECORATIONS, prefix='DECOR_')
        if decor:
            self.picker.set_constant(decor)
        layout.addWidget(self.picker, 1)

    def to_tuple(self):
        return ('removedecoration', self.picker.selected_constant())

    def friendly_name(self):
        return 'Remove Decoration'


class _GetPartySizeWidget(_CommandWidget):
    """Get Party Size — no parameters (result in RESULT var)."""
    _header_only = True
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Get Party Size'))
        layout.addStretch()

    def to_tuple(self):
        return ('getpartysize',)

    def friendly_name(self):
        return 'Get Party Size'


class _CheckPlayerGenderWidget(_CommandWidget):
    """Check Player Gender — no parameters (result in RESULT var)."""
    _header_only = True
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Check Player Gender'))
        layout.addStretch()

    def to_tuple(self):
        return ('checkplayergender',)

    def friendly_name(self):
        return 'Check Player Gender'


class _WaitMessageWidget(_CommandWidget):
    """Wait for Message — no parameters."""
    _header_only = True
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Wait for Message'))
        layout.addStretch()

    def to_tuple(self):
        return ('waitmessage',)

    def friendly_name(self):
        return 'Wait for Message'


class _CloseMessageWidget(_CommandWidget):
    """Close Message — no parameters."""
    _header_only = True
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Close Message'))
        layout.addStretch()

    def to_tuple(self):
        return ('closemessage',)

    def friendly_name(self):
        return 'Close Message'


class _SetMonMoveWidget(_CommandWidget):
    """Set Pokemon Move — species, move slot, move picker."""
    def __init__(self, args='', parent=None):
        super().__init__(parent)
        parts = [a.strip() for a in args.split(',')] if args else []
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Party Slot:'))
        self.slot_spin = QSpinBox()
        self.slot_spin.setRange(0, 5)
        self.slot_spin.setValue(int(parts[0]) if parts and parts[0].isdigit() else 0)
        layout.addWidget(self.slot_spin)
        layout.addWidget(QLabel('Move Slot:'))
        self.move_slot_spin = QSpinBox()
        self.move_slot_spin.setRange(0, 3)
        self.move_slot_spin.setValue(int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0)
        layout.addWidget(self.move_slot_spin)
        layout.addWidget(QLabel('Move:'))
        self.picker = ConstantPicker(ConstantsManager.MOVES, prefix='MOVE_')
        if len(parts) > 2:
            self.picker.set_constant(parts[2])
        layout.addWidget(self.picker, 1)

    def to_tuple(self):
        return ('setmonmove', f'{self.slot_spin.value()}, {self.move_slot_spin.value()}, '
                f'{self.picker.selected_constant()}')

    def friendly_name(self):
        return 'Set Pokémon Move'


class _ShowMonPicWidget(_CommandWidget):
    """Show Pokemon Picture — species picker + x, y."""
    def __init__(self, species='', x=10, y=3, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Species:'))
        self.picker = ConstantPicker(ConstantsManager.SPECIES, prefix='SPECIES_')
        if species:
            self.picker.set_constant(species)
        layout.addWidget(self.picker, 1)
        layout.addWidget(QLabel('X:'))
        self.x_spin = QSpinBox(); self.x_spin.setRange(0, 29); self.x_spin.setValue(x)
        layout.addWidget(self.x_spin)
        layout.addWidget(QLabel('Y:'))
        self.y_spin = QSpinBox(); self.y_spin.setRange(0, 19); self.y_spin.setValue(y)
        layout.addWidget(self.y_spin)

    def to_tuple(self):
        return ('showmonpic', f'{self.picker.selected_constant()}, '
                f'{self.x_spin.value()}, {self.y_spin.value()}')

    def friendly_name(self):
        return 'Show Pokémon Picture'


class _HideMonPicWidget(_CommandWidget):
    """Hide Pokemon Picture — no parameters."""
    _header_only = True
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Hide Pokémon Picture'))
        layout.addStretch()

    def to_tuple(self):
        return ('hidemonpic',)

    def friendly_name(self):
        return 'Hide Pokémon Picture'


class _PokeMartWidget(_CommandWidget):
    """PokeMart — editable list of items for sale.

    In scripts this looks like:
        pokemart PalletTown_Mart_Items
    with a data table elsewhere.  For new scripts we build the item list
    inline using the .4byte format.
    """
    def __init__(self, items_label='', items=None, parent=None):
        super().__init__(parent)
        self._items_label = items_label
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)

        top = QHBoxLayout()
        top.addWidget(QLabel('Items Label:'))
        self.label_combo = _make_label_combo(items_label)
        self.label_combo.setPlaceholderText('data label (e.g. MyMart_Items)')
        top.addWidget(self.label_combo, 1)
        layout.addLayout(top)

        layout.addWidget(QLabel('Items for sale:'))
        self._item_pickers: list[ConstantPicker] = []
        self._items_layout = QVBoxLayout()
        layout.addLayout(self._items_layout)

        # Pre-populate with existing items or 3 empty slots
        initial = items or ['ITEM_NONE'] * 3
        for item in initial:
            self._add_item_row(item)

        btn_row = QHBoxLayout()
        btn_add = QPushButton('+ Add Item')
        btn_add.clicked.connect(lambda: self._add_item_row('ITEM_NONE'))
        btn_row.addWidget(btn_add)
        btn_remove = QPushButton('- Remove Last')
        btn_remove.clicked.connect(self._remove_last_item)
        btn_row.addWidget(btn_remove)
        btn_row.addStretch()
        layout.addLayout(btn_row)

    def _add_item_row(self, item: str):
        picker = ConstantPicker(ConstantsManager.ITEMS, prefix='ITEM_')
        picker.set_constant(item)
        self._item_pickers.append(picker)
        self._items_layout.addWidget(picker)

    def _remove_last_item(self):
        if len(self._item_pickers) <= 1:
            return
        picker = self._item_pickers.pop()
        self._items_layout.removeWidget(picker)
        picker.deleteLater()

    def to_tuple(self):
        items = [p.selected_constant() for p in self._item_pickers
                 if p.selected_constant() and p.selected_constant() != 'ITEM_NONE']
        label = self.label_combo.currentText().strip()
        # Store as pokemart with the label; the items list is metadata
        return ('pokemart', label, items)

    def friendly_name(self):
        return 'PokéMart'


class _CopyVarWidget(_CommandWidget):
    """Copy Variable — dest var + source var."""
    def __init__(self, dest='', src='', parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Dest:'))
        self.dest_picker = ConstantPicker(ConstantsManager.VARS, prefix='VAR_')
        if dest:
            self.dest_picker.set_constant(dest)
        layout.addWidget(self.dest_picker, 1)
        layout.addWidget(QLabel('Source:'))
        self.src_picker = ConstantPicker(ConstantsManager.VARS, prefix='VAR_')
        if src:
            self.src_picker.set_constant(src)
        layout.addWidget(self.src_picker, 1)

    def to_tuple(self):
        return ('copyvar', f'{self.dest_picker.selected_constant()}, {self.src_picker.selected_constant()}')

    def friendly_name(self):
        return 'Copy Variable'


class _CompareVarToVarWidget(_CommandWidget):
    """Compare Variable to Variable."""
    def __init__(self, var1='', var2='', parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Var 1:'))
        self.var1_picker = ConstantPicker(ConstantsManager.VARS, prefix='VAR_')
        if var1:
            self.var1_picker.set_constant(var1)
        layout.addWidget(self.var1_picker, 1)
        layout.addWidget(QLabel('Var 2:'))
        self.var2_picker = ConstantPicker(ConstantsManager.VARS, prefix='VAR_')
        if var2:
            self.var2_picker.set_constant(var2)
        layout.addWidget(self.var2_picker, 1)

    def to_tuple(self):
        return ('compare_var_to_var', f'{self.var1_picker.selected_constant()}, {self.var2_picker.selected_constant()}')

    def friendly_name(self):
        return 'Compare Variable to Variable'


class _WaitstateWidget(_CommandWidget):
    """Waitstate — no parameters."""
    _header_only = True
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Wait State (wait for async operation)'))
        layout.addStretch()

    def to_tuple(self):
        return ('waitstate',)

    def friendly_name(self):
        return 'Wait State'


class _GetPlayerXYWidget(_CommandWidget):
    """Get Player Position — store X and Y into two variables."""
    def __init__(self, var_x='VAR_0x8004', var_y='VAR_0x8005', parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Store X in:'))
        self.var_x_edit = QLineEdit(var_x)
        self.var_x_edit.setPlaceholderText('variable for X')
        self.var_x_edit.setToolTip(_tt('Variable to store the player\'s X (horizontal) position'))
        layout.addWidget(self.var_x_edit, 1)
        layout.addWidget(QLabel('Store Y in:'))
        self.var_y_edit = QLineEdit(var_y)
        self.var_y_edit.setPlaceholderText('variable for Y')
        self.var_y_edit.setToolTip(_tt('Variable to store the player\'s Y (vertical) position'))
        layout.addWidget(self.var_y_edit, 1)

    def to_tuple(self):
        return ('getplayerxy', f'{self.var_x_edit.text().strip()}, {self.var_y_edit.text().strip()}')

    def friendly_name(self):
        return 'Get Player Position'


class _RandomWidget(_CommandWidget):
    """Random Number — generate a random value from 0 to max-1."""
    def __init__(self, limit=10, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Random 0 to:'))
        self.limit_spin = QSpinBox()
        self.limit_spin.setRange(1, 65535)
        self.limit_spin.setValue(int(limit) if str(limit).isdigit() else 10)
        self.limit_spin.setToolTip(_tt('Upper bound (exclusive) — result is 0 to this-minus-1\nStored in VAR_RESULT'))
        layout.addWidget(self.limit_spin)
        layout.addWidget(QLabel('(result in VAR_RESULT)'))
        layout.addStretch()

    def to_tuple(self):
        return ('random', str(self.limit_spin.value()))

    def friendly_name(self):
        return 'Random Number'


class _HealPlayerTeamWidget(_CommandWidget):
    """Heal Player Team — emits 'special HealPlayerParty'."""
    _header_only = True
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Heal Player Team (full HP/PP/status restore)'))
        layout.addStretch()

    def to_tuple(self):
        return ('special', 'HealPlayerParty')

    def friendly_name(self):
        return 'Heal Player Team'


class _SaveBgmWidget(_CommandWidget):
    """Save BGM — requires a song parameter."""
    def __init__(self, song='', parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Save Music:'))
        self.picker = ConstantPicker(ConstantsManager.MUSIC, prefix='MUS_')
        self.picker.setToolTip(_tt(
            'Song to save — restores later with fadedefaultbgm.\n'
            'The macro requires a song parameter.'))
        if song:
            self.picker.set_constant(song)
        layout.addWidget(self.picker, 1)

    def to_tuple(self):
        return ('savebgm', self.picker.selected_constant())

    def friendly_name(self):
        return 'Save Current Music'


class _FadeDefaultBgmWidget(_CommandWidget):
    """Restore saved BGM — no parameters."""
    _header_only = True
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Restore Saved Music (fade back to BGM saved by savebgm)'))
        layout.addStretch()

    def to_tuple(self):
        return ('fadedefaultbgm',)

    def friendly_name(self):
        return 'Restore Saved Music'


class _BufferNumberWidget(_CommandWidget):
    """Buffer Number — buffer slot (0-2) + number value."""
    def __init__(self, slot='0', value='0', parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Buffer:'))
        self.slot_spin = QSpinBox()
        self.slot_spin.setRange(0, 2)
        self.slot_spin.setValue(int(slot) if str(slot).isdigit() else 0)
        self.slot_spin.setMaximumWidth(50)
        self.slot_spin.setToolTip(_tt('Buffer slot (0–2) — use {STR_VAR_1/2/3} in text to display it'))
        layout.addWidget(self.slot_spin)
        layout.addWidget(QLabel('Number:'))
        self.value_edit = QLineEdit(str(value))
        self.value_edit.setPlaceholderText('number or variable')
        self.value_edit.setToolTip(_tt('Number to convert to text in the buffer'))
        layout.addWidget(self.value_edit, 1)

    def to_tuple(self):
        return ('buffernumberstring', f'{self.slot_spin.value()}, {self.value_edit.text().strip()}')

    def friendly_name(self):
        return 'Buffer Number'


class _BufferStringWidget(_CommandWidget):
    """Buffer String — buffer slot (0-2) + text label."""
    def __init__(self, slot='0', label='', parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Buffer:'))
        self.slot_spin = QSpinBox()
        self.slot_spin.setRange(0, 2)
        self.slot_spin.setValue(int(slot) if str(slot).isdigit() else 0)
        self.slot_spin.setMaximumWidth(50)
        self.slot_spin.setToolTip(_tt('Buffer slot (0–2) — use {STR_VAR_1/2/3} in text to display it'))
        layout.addWidget(self.slot_spin)
        layout.addWidget(QLabel('Text label:'))
        self.label_edit = QLineEdit(str(label))
        self.label_edit.setPlaceholderText('text label from strings')
        self.label_edit.setToolTip(_tt('Script label pointing to the text to store'))
        layout.addWidget(self.label_edit, 1)

    def to_tuple(self):
        return ('bufferstring', f'{self.slot_spin.value()}, {self.label_edit.text().strip()}')

    def friendly_name(self):
        return 'Buffer String'


class _SetMetatileWidget(_CommandWidget):
    """Set Metatile — x, y, metatile_id, impassable."""
    def __init__(self, x=0, y=0, tile_id='', impassable=False, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('X:'))
        self.x_spin = QSpinBox(); self.x_spin.setRange(0, 999); self.x_spin.setValue(x)
        layout.addWidget(self.x_spin)
        layout.addWidget(QLabel('Y:'))
        self.y_spin = QSpinBox(); self.y_spin.setRange(0, 999); self.y_spin.setValue(y)
        layout.addWidget(self.y_spin)
        layout.addWidget(QLabel('Tile:'))
        self.tile_edit = QLineEdit(str(tile_id))
        self.tile_edit.setPlaceholderText('metatile ID or constant')
        layout.addWidget(self.tile_edit, 1)
        self.impass_check = QCheckBox('Impassable')
        self.impass_check.setChecked(impassable)
        layout.addWidget(self.impass_check)

    def to_tuple(self):
        impass = '1' if self.impass_check.isChecked() else '0'
        return ('setmetatile', f'{self.x_spin.value()}, {self.y_spin.value()}, '
                f'{self.tile_edit.text()}, {impass}')

    def friendly_name(self):
        return 'Set Metatile'


# ═════════════════════════════════════════════════════════════════════════════
class _SetObjectMovementTypeWidget(_CommandWidget):
    """Set Object Movement Type — object local ID + MOVEMENT_TYPE picker."""
    def __init__(self, obj_id='', movement_type='', parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel('Object:'))
        self.obj_combo = _make_object_combo(obj_id)
        layout.addWidget(self.obj_combo)
        layout.addWidget(QLabel('Movement Type:'))
        self.movement_picker = ConstantPicker(
            ConstantsManager.MOVEMENT_TYPES, prefix='MOVEMENT_TYPE_')
        if movement_type:
            self.movement_picker.set_constant(movement_type)
        layout.addWidget(self.movement_picker, 1)

    def to_tuple(self):
        return ('setobjectmovementtype',
                f'{self.obj_combo.currentText().strip()}, '
                f'{self.movement_picker.selected_constant()}')

    def friendly_name(self):
        return 'Set Object Movement Type'


# ═════════════════════════════════════════════════════════════════════════════
# Generic fallback widget
# ═════════════════════════════════════════════════════════════════════════════

class _GenericWidget(_CommandWidget):
    """Fallback for any command without a specialized widget."""
    def __init__(self, cmd='', args='', parent=None):
        super().__init__(parent)
        self._cmd = cmd
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        label = cmd.replace('_', ' ').title()
        layout.addWidget(QLabel(f'{label}:'))
        self.args_edit = QLineEdit(args)
        self.args_edit.setPlaceholderText('arguments')
        layout.addWidget(self.args_edit, 1)

    def to_tuple(self):
        args = self.args_edit.text().strip()
        return (self._cmd, args) if args else (self._cmd,)

    def friendly_name(self):
        from eventide.backend.eventide_utils import FRIENDLY_COMMANDS
        return FRIENDLY_COMMANDS.get(self._cmd, self._cmd.replace('_', ' ').title())


# ═════════════════════════════════════════════════════════════════════════════
# Widget factory — maps command tuples to the right widget
# ═════════════════════════════════════════════════════════════════════════════

def _safe_int(val, default=0):
    """Convert a value to int, returning default on failure."""
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _split_args(args_str: str) -> list[str]:
    """Split a comma-separated args string into stripped parts."""
    return [a.strip() for a in str(args_str).split(',')]


def _widget_for_tuple(cmd_tuple: tuple) -> _CommandWidget:
    """Create the appropriate widget for a command tuple."""
    if not cmd_tuple:
        return _GenericWidget('nop')
    cmd = cmd_tuple[0]
    args = cmd_tuple[1] if len(cmd_tuple) > 1 else ''

    # ── Dialogue & Logic (Page 1) ────────────────────────────────────────
    if cmd == 'message':
        label = cmd_tuple[1] if len(cmd_tuple) > 1 else None
        text = cmd_tuple[2] if len(cmd_tuple) > 2 else ''
        msg_type = cmd_tuple[3] if len(cmd_tuple) > 3 else ''
        return _MessageWidget(label, text, msg_type)

    if cmd == 'yesnobox':
        parts = _split_args(args)
        return _YesNoWidget(_safe_int(parts[0]), _safe_int(parts[1]) if len(parts) > 1 else 0)

    if cmd == 'multichoice':
        parts = _split_args(args)
        return _MultiChoiceWidget(
            _safe_int(parts[0]), _safe_int(parts[1]) if len(parts) > 1 else 0,
            parts[2] if len(parts) > 2 else '0', parts[3] if len(parts) > 3 else '0')

    if cmd == 'setflag':
        return _SetFlagWidget(str(args))
    if cmd == 'clearflag':
        return _ClearFlagWidget(str(args))
    if cmd == 'checkflag':
        return _CheckFlagWidget(str(args))

    if cmd == 'setvar':
        parts = _split_args(args)
        return _SetVarWidget(parts[0], parts[1] if len(parts) > 1 else '0')
    if cmd == 'addvar':
        parts = _split_args(args)
        return _AddVarWidget(parts[0], parts[1] if len(parts) > 1 else '1')
    if cmd == 'subvar':
        parts = _split_args(args)
        return _SubVarWidget(parts[0], parts[1] if len(parts) > 1 else '1')
    if cmd == 'compare_var_to_value':
        parts = _split_args(args)
        return _CompareVarWidget(parts[0], parts[1] if len(parts) > 1 else '0')

    # ── Conditional branches (compare) ─────────────────────────────────
    if cmd in ('goto_if_eq', 'goto_if_ne', 'goto_if_lt', 'goto_if_ge',
               'goto_if_le', 'goto_if_gt'):
        var = cmd_tuple[1] if len(cmd_tuple) > 1 else ''
        val = cmd_tuple[2] if len(cmd_tuple) > 2 else ''
        lbl = cmd_tuple[3] if len(cmd_tuple) > 3 else ''
        return _GotoIfCompareWidget(cmd, var, val, lbl)

    # ── Conditional branches (flag) ──────────────────────────────────
    if cmd in ('goto_if_set', 'goto_if_unset'):
        flag = cmd_tuple[1] if len(cmd_tuple) > 1 else ''
        lbl = cmd_tuple[2] if len(cmd_tuple) > 2 else ''
        return _GotoIfFlagWidget(cmd, flag, lbl)

    # ── Conditional calls (compare) ──────────────────────────────────
    if cmd in ('call_if_eq', 'call_if_ne', 'call_if_lt', 'call_if_gt',
                'call_if_le', 'call_if_ge'):
        var = cmd_tuple[1] if len(cmd_tuple) > 1 else ''
        val = cmd_tuple[2] if len(cmd_tuple) > 2 else ''
        lbl = cmd_tuple[3] if len(cmd_tuple) > 3 else ''
        return _CallIfCompareWidget(cmd, var, val, lbl)

    # ── Conditional calls (flag) ─────────────────────────────────────
    if cmd in ('call_if_set', 'call_if_unset'):
        flag = cmd_tuple[1] if len(cmd_tuple) > 1 else ''
        lbl = cmd_tuple[2] if len(cmd_tuple) > 2 else ''
        return _CallIfFlagWidget(cmd, flag, lbl)

    if cmd == 'call':
        return _CallWidget(str(args))
    if cmd == 'goto':
        return _GotoWidget(str(args))
    if cmd == 'end':
        return _EndWidget()
    if cmd == 'return':
        return _ReturnWidget()
    if cmd == 'special':
        # Friendly widget for known specials
        if str(args).strip() == 'HealPlayerParty':
            return _HealPlayerTeamWidget()
        return _SpecialWidget(str(args))
    if cmd == 'specialvar':
        parts = _split_args(str(args))
        dest = parts[0] if len(parts) > 0 else 'VAR_RESULT'
        func = parts[1] if len(parts) > 1 else ''
        return _SpecialVarWidget(dest, func)
    if cmd == 'waitbuttonpress':
        return _WaitbuttonWidget()

    # ── World & Characters (Page 2) ──────────────────────────────────────
    if cmd in ('warp', 'warpsilent', 'warpdoor', 'warphole', 'warpteleport'):
        if len(cmd_tuple) >= 4:
            return _WarpWidget(cmd, cmd_tuple[1], cmd_tuple[2], cmd_tuple[3])
        parts = _split_args(args)
        return _WarpWidget(cmd, parts[0], _safe_int(parts[1]) if len(parts) > 1 else 0,
                           _safe_int(parts[2]) if len(parts) > 2 else 0)

    if cmd == 'applymovement':
        if len(cmd_tuple) >= 3:
            return _ApplyMovementWidget(cmd_tuple[1], cmd_tuple[2])
        parts = _split_args(args)
        return _ApplyMovementWidget(parts[0], parts[1] if len(parts) > 1 else '')

    if cmd == 'waitmovement':
        return _WaitMovementWidget(str(args) if args else '0')

    if cmd == 'removeobject':
        return _RemoveObjectWidget(str(args))
    if cmd == 'addobject':
        return _AddObjectWidget(str(args))
    if cmd == 'showobjectat':
        parts = _split_args(args)
        return _ShowObjectWidget(parts[0], parts[1] if len(parts) > 1 else '')
    if cmd == 'hideobjectat':
        parts = _split_args(args)
        return _HideObjectWidget(parts[0], parts[1] if len(parts) > 1 else '')
    if cmd == 'faceplayer':
        return _FacePlayerWidget()
    if cmd == 'turnobject':
        parts = _split_args(args)
        return _TurnObjectWidget(parts[0], parts[1] if len(parts) > 1 else 'DIR_DOWN')
    if cmd in ('setobjectxy', 'setobjectxyperm'):
        parts = _split_args(args)
        return _SetObjectXYWidget(parts[0], _safe_int(parts[1]) if len(parts) > 1 else 0,
                                  _safe_int(parts[2]) if len(parts) > 2 else 0,
                                  cmd=cmd)
    if cmd == 'setobjectmovementtype':
        parts = _split_args(args)
        return _SetObjectMovementTypeWidget(
            parts[0] if parts else '',
            parts[1] if len(parts) > 1 else '')

    if cmd == 'lock':
        return _LockWidget('lock')
    if cmd == 'lockall':
        return _LockWidget('lockall')
    if cmd == 'release':
        return _ReleaseWidget('release')
    if cmd == 'releaseall':
        return _ReleaseWidget('releaseall')

    if cmd == 'fadescreen':
        return _FadeScreenWidget(str(args))
    if cmd == 'fadescreenspeed':
        parts = _split_args(args)
        return _FadeScreenSpeedWidget(parts[0], _safe_int(parts[1]) if len(parts) > 1 else 0)

    if cmd == 'playse':
        # Handle both old tuple format (name, vol, pitch) and new (just name)
        if len(cmd_tuple) >= 2:
            return _PlaySEWidget(str(cmd_tuple[1]))
        return _PlaySEWidget(str(args))
    if cmd == 'waitse':
        return _WaitSEWidget()
    if cmd == 'playfanfare':
        if len(cmd_tuple) >= 2:
            return _PlayFanfareWidget(str(cmd_tuple[1]))
        return _PlayFanfareWidget(str(args))
    if cmd == 'waitfanfare':
        return _WaitFanfareWidget()
    if cmd == 'playbgm':
        if len(cmd_tuple) >= 3:
            return _PlayBGMWidget(str(cmd_tuple[1]),
                                  bool(_safe_int(cmd_tuple[2], 1)))
        parts = _split_args(args)
        return _PlayBGMWidget(parts[0], bool(_safe_int(parts[1], 1)) if len(parts) > 1 else True)
    if cmd == 'fadeoutbgm':
        return _FadeOutBGMWidget(_safe_int(args, 4))
    if cmd == 'fadeinbgm':
        return _FadeInBGMWidget(_safe_int(args, 4))

    if cmd == 'setweather':
        return _SetWeatherWidget(str(args))
    if cmd == 'doweather':
        return _DoWeatherWidget()
    if cmd == 'resetweather':
        return _ResetWeatherWidget()

    if cmd == 'delay':
        return _DelayWidget(_safe_int(args, 60))
    if cmd == 'setflashlevel':
        return _SetFlashLevelWidget(_safe_int(args, 0))
    if cmd == 'playmoncry':
        parts = _split_args(args)
        return _PlayMonCryWidget(
            parts[0] if parts else '',
            parts[1] if len(parts) > 1 else '0')

    # ── Battles, Items & System (Page 3) ─────────────────────────────────
    if cmd in ('trainerbattle', 'trainerbattle_single', 'trainerbattle_no_intro',
                'trainerbattle_earlyrival', 'trainerbattle_double',
                'trainerbattle_rematch', 'trainerbattle_rematch_double'):
        parts = _split_args(args)
        # Args layout depends on variant (type is in the command name, NOT in args):
        #   _single:     TRAINER, INTRO, DEFEAT [, CONTINUE]
        #   _double:     TRAINER, INTRO, DEFEAT, NOT_ENOUGH [, CONTINUE]
        #   _no_intro:   TRAINER, DEFEAT
        #   _earlyrival: TRAINER, FLAGS, DEFEAT, VICTORY
        trainer = parts[0] if parts else ''
        if cmd == 'trainerbattle_no_intro':
            return _TrainerBattleWidget(cmd, trainer,
                                        '', parts[1] if len(parts) > 1 else '')
        elif cmd == 'trainerbattle_earlyrival':
            return _TrainerBattleWidget(cmd, trainer,
                                        parts[1] if len(parts) > 1 else '',  # flags
                                        parts[2] if len(parts) > 2 else '',  # defeat
                                        parts[3] if len(parts) > 3 else '')  # victory
        elif cmd == 'trainerbattle_rematch':
            return _TrainerBattleWidget(cmd, trainer,
                                        parts[1] if len(parts) > 1 else '',  # intro
                                        parts[2] if len(parts) > 2 else '')  # defeat
        elif cmd in ('trainerbattle_double', 'trainerbattle_rematch_double'):
            return _TrainerBattleWidget(cmd, trainer,
                                        parts[1] if len(parts) > 1 else '',  # intro
                                        parts[2] if len(parts) > 2 else '',  # defeat
                                        parts[3] if len(parts) > 3 else '',  # not_enough
                                        parts[4] if len(parts) > 4 else '')  # continue
        else:  # trainerbattle_single or plain trainerbattle
            return _TrainerBattleWidget(cmd, trainer,
                                        parts[1] if len(parts) > 1 else '',  # intro
                                        parts[2] if len(parts) > 2 else '',  # defeat
                                        parts[3] if len(parts) > 3 else '')  # continue

    if cmd == 'wildbattle':
        return _WildBattleWidget(
            cmd_tuple[1] if len(cmd_tuple) > 1 else '',
            cmd_tuple[2] if len(cmd_tuple) > 2 else 1,
            cmd_tuple[3] if len(cmd_tuple) > 3 else False)

    if cmd == 'givemon':
        parts = _split_args(args)
        return _GiveMonWidget(
            parts[0] if parts else '',
            _safe_int(parts[1], 5) if len(parts) > 1 else 5,
            parts[2] if len(parts) > 2 else 'ITEM_NONE',
            parts[3:7] if len(parts) > 3 else None)

    if cmd == 'giveegg':
        return _GiveEggWidget(str(args))

    if cmd == 'finditem':
        parts = _split_args(args)
        return _FindItemWidget(parts[0] if parts else '')
    if cmd == 'checkitemspace':
        parts = _split_args(args)
        return _CheckItemSpaceWidget(parts[0] if parts else '',
                                      _safe_int(parts[1], 1) if len(parts) > 1 else 1)
    if cmd == 'additem':
        parts = _split_args(args)
        return _GiveItemWidget(parts[0] if parts else '',
                               _safe_int(parts[1], 1) if len(parts) > 1 else 1)
    if cmd == 'removeitem':
        parts = _split_args(args)
        return _RemoveItemWidget(parts[0] if parts else '',
                                 _safe_int(parts[1], 1) if len(parts) > 1 else 1)
    if cmd == 'checkitem':
        parts = _split_args(args)
        return _CheckItemWidget(parts[0] if parts else '',
                                _safe_int(parts[1], 1) if len(parts) > 1 else 1)

    if cmd == 'addmoney':
        parts = _split_args(args)
        return _AddMoneyWidget(_safe_int(parts[0], 100))
    if cmd == 'removemoney':
        parts = _split_args(args)
        return _RemoveMoneyWidget(_safe_int(parts[0], 100))
    if cmd == 'checkmoney':
        parts = _split_args(args)
        return _CheckMoneyWidget(_safe_int(parts[0], 100))
    if cmd == 'addcoins':
        return _AddCoinsWidget(_safe_int(args, 1))
    if cmd == 'removecoins':
        return _RemoveCoinsWidget(_safe_int(args, 1))

    if cmd == 'setrespawn':
        return _SetRespawnWidget(str(args))
    if cmd == 'checkpartymove':
        return _CheckPartyMoveWidget(str(args))

    if cmd == 'bufferspeciesname':
        parts = _split_args(args)
        return _BufferSpeciesWidget(parts[0] if parts else '0',
                                    parts[1] if len(parts) > 1 else '')
    if cmd == 'bufferitemname':
        parts = _split_args(args)
        return _BufferItemWidget(parts[0] if parts else '0',
                                 parts[1] if len(parts) > 1 else '')
    if cmd == 'buffermovename':
        parts = _split_args(args)
        return _BufferMoveWidget(parts[0] if parts else '0',
                                 parts[1] if len(parts) > 1 else '')

    # ── Doors ────────────────────────────────────────────────────────────
    if cmd == 'opendoor':
        parts = _split_args(args)
        return _OpenDoorWidget(_safe_int(parts[0]), _safe_int(parts[1]) if len(parts) > 1 else 0)
    if cmd == 'closedoor':
        parts = _split_args(args)
        return _CloseDoorWidget(_safe_int(parts[0]), _safe_int(parts[1]) if len(parts) > 1 else 0)
    if cmd == 'waitdooranim':
        return _WaitDoorAnimWidget()

    # ── Decorations ──────────────────────────────────────────────────────
    if cmd == 'adddecoration':
        return _AddDecorationWidget(str(args))
    if cmd == 'removedecoration':
        return _RemoveDecorationWidget(str(args))

    # ── Misc no-arg commands ─────────────────────────────────────────────
    if cmd == 'getpartysize':
        return _GetPartySizeWidget()
    if cmd == 'checkplayergender':
        return _CheckPlayerGenderWidget()
    if cmd == 'waitmessage':
        return _WaitMessageWidget()
    if cmd == 'closemessage':
        return _CloseMessageWidget()

    # ── Pokemon moves ────────────────────────────────────────────────────
    if cmd == 'setmonmove':
        return _SetMonMoveWidget(str(args))
    if cmd == 'showmonpic':
        parts = _split_args(args)
        return _ShowMonPicWidget(parts[0] if parts else '',
                                 _safe_int(parts[1], 10) if len(parts) > 1 else 10,
                                 _safe_int(parts[2], 3) if len(parts) > 2 else 3)
    if cmd == 'hidemonpic':
        return _HideMonPicWidget()

    # ── PokeMart ─────────────────────────────────────────────────────────
    if cmd == 'pokemart':
        label = str(args) if isinstance(args, str) else ''
        items = cmd_tuple[2] if len(cmd_tuple) > 2 and isinstance(cmd_tuple[2], list) else None
        return _PokeMartWidget(label, items)

    # ── Additional var commands ──────────────────────────────────────────
    if cmd == 'copyvar':
        parts = _split_args(args)
        return _CopyVarWidget(parts[0] if parts else '', parts[1] if len(parts) > 1 else '')
    if cmd == 'compare_var_to_var':
        parts = _split_args(args)
        return _CompareVarToVarWidget(parts[0] if parts else '', parts[1] if len(parts) > 1 else '')

    # ── Waitstate ────────────────────────────────────────────────────────
    if cmd == 'waitstate':
        return _WaitstateWidget()

    # ── Set Metatile ─────────────────────────────────────────────────────
    if cmd == 'setmetatile':
        parts = _split_args(args)
        return _SetMetatileWidget(
            _safe_int(parts[0]), _safe_int(parts[1]) if len(parts) > 1 else 0,
            parts[2] if len(parts) > 2 else '',
            parts[3] == '1' if len(parts) > 3 else False)

    # ── New commands ───────────────────────────────────────────────────────
    if cmd == 'getplayerxy':
        parts = _split_args(args)
        return _GetPlayerXYWidget(parts[0] if parts else 'VAR_0x8004',
                                   parts[1] if len(parts) > 1 else 'VAR_0x8005')
    if cmd == 'random':
        return _RandomWidget(_safe_int(args, 10))
    if cmd == 'healplayerteam':
        return _HealPlayerTeamWidget()
    if cmd == 'savebgm':
        return _SaveBgmWidget(str(args) if args else '')
    if cmd == 'fadedefaultbgm':
        return _FadeDefaultBgmWidget()
    if cmd == 'buffernumberstring':
        parts = _split_args(args)
        return _BufferNumberWidget(parts[0] if parts else '0',
                                    parts[1] if len(parts) > 1 else '0')
    if cmd == 'bufferstring':
        parts = _split_args(args)
        return _BufferStringWidget(parts[0] if parts else '0',
                                    parts[1] if len(parts) > 1 else '')

    # ── Fallback ─────────────────────────────────────────────────────────
    return _GenericWidget(cmd, str(args))


# ═════════════════════════════════════════════════════════════════════════════
# 3-Page Command Selector Dialog (RPG Maker XP style)
# ═════════════════════════════════════════════════════════════════════════════

# Each page is a list of (section_name, [(friendly_label, raw_command), ...])
# ── Page 1: Everyday Scripting ──────────────────────────────────────────────
# The commands you reach for most when writing NPC scripts, cutscenes,
# trainer encounters, and item hand-outs.  "Open palette → click" with
# zero page-switching for the 80% case.
_PAGE_1_COMMANDS = [
    ('NPC Basics', [
        ('Lock Player', 'lock'),
        ('Lock All', 'lockall'),
        ('Face Player', 'faceplayer'),
        ('Release Player', 'release'),
        ('Release All', 'releaseall'),
    ]),
    ('Dialogue', [
        ('Show Message', 'message'),
        ('Yes/No Choice', 'yesnobox'),
        ('Multi-Choice Box', 'multichoice'),
        ('Wait for Message', 'waitmessage'),
        ('Close Message', 'closemessage'),
    ]),
    ('Movement', [
        ('Set Move Route', 'applymovement'),
        ('Wait for Movement', 'waitmovement'),
        ('Turn NPC', 'turnobject'),
    ]),
    ('Battles', [
        ('Trainer Battle (Single)', 'trainerbattle_single'),
        ('Trainer Battle (Double)', 'trainerbattle_double'),
        ('Trainer Battle (No Intro)', 'trainerbattle_no_intro'),
        ('Trainer Battle (Rematch)', 'trainerbattle_rematch'),
        ('Wild Battle', 'wildbattle'),
    ]),
    ('Items & Rewards', [
        ('Give Item', 'additem'),
        ('Find Item (Item Ball)', 'finditem'),
        ('Check for Item', 'checkitem'),
        ('Check Item Space', 'checkitemspace'),
        ('Remove Item', 'removeitem'),
        ('PokéMart', 'pokemart'),
    ]),
    ('Flags & Variables', [
        ('Set Flag', 'setflag'),
        ('Clear Flag', 'clearflag'),
        ('Set Variable', 'setvar'),
        ('Add to Variable', 'addvar'),
        ('Subtract from Variable', 'subvar'),
    ]),
    ('Flow Control', [
        ('If Flag → Goto', 'goto_if_set'),
        ('If Flag (Off) → Goto', 'goto_if_unset'),
        ('If Variable → Goto', 'goto_if_eq'),
        ('If Variable → Call', 'call_if_eq'),
        ('If Flag → Call', 'call_if_set'),
        ('If Flag (Off) → Call', 'call_if_unset'),
        ('Call Script', 'call'),
        ('Goto', 'goto'),
        ('End Script', 'end'),
        ('Return', 'return'),
    ]),
]

# ── Page 2: World, Characters & Effects ─────────────────────────────────────
# NPC manipulation, warps, camera, sound, screen effects, timing —
# everything about the world and presentation.
_PAGE_2_COMMANDS = [
    ('NPC & Object Control', [
        ('Remove NPC/Object', 'removeobject'),
        ('Add NPC/Object', 'addobject'),
        ('Show NPC/Object', 'showobjectat'),
        ('Hide NPC/Object', 'hideobjectat'),
        ('Set NPC Position', 'setobjectxy'),
        ('Set NPC Position (Permanent)', 'setobjectxyperm'),
        ('Set NPC Movement Type', 'setobjectmovementtype'),
        ('Get Player Position', 'getplayerxy'),
    ]),
    ('Warps & Teleportation', [
        ('Warp Player', 'warp'),
        ('Warp (Silent)', 'warpsilent'),
        ('Warp Through Door', 'warpdoor'),
        ('Fall Through Hole', 'warphole'),
        ('Teleport Player', 'warpteleport'),
    ]),
    ('Camera', [
        ('Move Camera (Cutscene)', 'movecamera'),
    ]),
    ('Screen Effects', [
        ('Fade Screen', 'fadescreen'),
        ('Fade Screen (Speed)', 'fadescreenspeed'),
        ('Set Flash Level', 'setflashlevel'),
    ]),
    ('Doors', [
        ('Open Door', 'opendoor'),
        ('Close Door', 'closedoor'),
        ('Wait for Door Animation', 'waitdooranim'),
    ]),
    ('Sound & Music', [
        ('Play Sound Effect', 'playse'),
        ('Wait for Sound', 'waitse'),
        ('Play Fanfare', 'playfanfare'),
        ('Wait for Fanfare', 'waitfanfare'),
        ('Play Music', 'playbgm'),
        ('Fade Out Music', 'fadeoutbgm'),
        ('Fade In Music', 'fadeinbgm'),
        ('Save Current Music', 'savebgm'),
        ('Restore Saved Music', 'fadedefaultbgm'),
    ]),
    ('Weather', [
        ('Set Weather', 'setweather'),
        ('Trigger Weather', 'doweather'),
        ('Reset Weather', 'resetweather'),
    ]),
    ('Timing', [
        ('Delay (Frames)', 'delay'),
        ('Wait State', 'waitstate'),
        ('Wait for Button Press', 'waitbuttonpress'),
    ]),
    ('Map', [
        ('Set Metatile', 'setmetatile'),
        ('Set World Map Flag', 'setworldmapflag'),
    ]),
    ('Text Formatting', [
        ('Text Color', 'textcolor'),
        ('Sign Message Mode', 'signmsg'),
        ('Normal Message Mode', 'normalmsg'),
    ]),
]

# ── Page 3: Data, System & Advanced ─────────────────────────────────────────
# Pokémon party manipulation, money, buffers, advanced flow control,
# specials, and everything else.
_PAGE_3_COMMANDS = [
    ('Pokémon', [
        ('Give Pokémon', 'givemon'),
        ('Give Egg', 'giveegg'),
        ('Heal Player Team', 'healplayerteam'),
        ('Set Pokémon Move', 'setmonmove'),
        ('Check Party for Move', 'checkpartymove'),
        ('Get Party Size', 'getpartysize'),
        ('Check Player Gender', 'checkplayergender'),
        ('Show Pokémon Picture', 'showmonpic'),
        ('Hide Pokémon Picture', 'hidemonpic'),
        ('Play Pokémon Cry', 'playmoncry'),
    ]),
    ('Money & Coins', [
        ('Give Money', 'addmoney'),
        ('Take Money', 'removemoney'),
        ('Check Money', 'checkmoney'),
        ('Give Coins', 'addcoins'),
        ('Take Coins', 'removecoins'),
    ]),
    ('Buffers', [
        ('Buffer Species Name', 'bufferspeciesname'),
        ('Buffer Item Name', 'bufferitemname'),
        ('Buffer Move Name', 'buffermovename'),
        ('Buffer Number', 'buffernumberstring'),
        ('Buffer String', 'bufferstring'),
    ]),
    ('Advanced Flow Control', [
        ('If Var ≠ → Goto', 'goto_if_ne'),
        ('If Var < → Goto', 'goto_if_lt'),
        ('If Var > → Goto', 'goto_if_gt'),
        ('If Var ≤ → Goto', 'goto_if_le'),
        ('If Var ≥ → Goto', 'goto_if_ge'),
        ('If Var ≠ → Call', 'call_if_ne'),
        ('If Var < → Call', 'call_if_lt'),
        ('If Var > → Call', 'call_if_gt'),
        ('If Var ≤ → Call', 'call_if_le'),
        ('If Var ≥ → Call', 'call_if_ge'),
        ('Check Flag', 'checkflag'),
        ('Compare Variable to Value', 'compare_var_to_value'),
        ('Compare Variable to Variable', 'compare_var_to_var'),
        ('Copy Variable', 'copyvar'),
        ('Random Number', 'random'),
    ]),
    ('Specials & System', [
        ('Call Standard', 'callstd'),
        ('Goto Standard', 'gotostd'),
        ('Special Function', 'special'),
        ('Special Var', 'specialvar'),
        ('Set Respawn Point', 'setrespawn'),
    ]),
    ('Trainer Battle Variants', [
        ('Trainer Battle (Early Rival)', 'trainerbattle_earlyrival'),
        ('Trainer Battle (Rematch Double)', 'trainerbattle_rematch_double'),
    ]),
    ('Decorations', [
        ('Add Decoration', 'adddecoration'),
        ('Remove Decoration', 'removedecoration'),
    ]),
]


# ── Recently used commands (session-persistent) ────────────────────────────
_RECENT_COMMANDS: list[str] = []  # raw cmd names, most-recent first
_RECENT_MAX = 8                    # how many recent entries to show

# Build a reverse lookup: raw_cmd → friendly name
_CMD_FRIENDLY_NAMES: dict[str, str] = {}
for _page_cmds in (_PAGE_1_COMMANDS, _PAGE_2_COMMANDS, _PAGE_3_COMMANDS):
    for _section_name, _cmds in _page_cmds:
        for _friendly, _raw in _cmds:
            _CMD_FRIENDLY_NAMES[_raw] = _friendly


def _record_recent(cmd: str):
    """Record a command as recently used (most-recent first, no duplicates)."""
    if cmd in _RECENT_COMMANDS:
        _RECENT_COMMANDS.remove(cmd)
    _RECENT_COMMANDS.insert(0, cmd)
    if len(_RECENT_COMMANDS) > _RECENT_MAX:
        _RECENT_COMMANDS.pop()


_CMD_TOOLTIPS: dict[str, str] = {
    # ── Page 1: Dialogue ─────────────────────────────────────────────
    'message': 'Display a text box with dialogue\nSupports \\n, \\p, \\l line breaks and {COMMANDS}',
    'yesnobox': 'Show a Yes/No choice prompt\nResult stored in VAR_RESULT (1=Yes, 0=No)',
    'multichoice': 'Show a list of choices for the player to pick from\nResult stored in VAR_RESULT (0-based index)',
    'waitmessage': 'Pause the script until the current message finishes displaying',
    'closemessage': 'Close any open message box on screen',

    # ── Page 1: Flags & Variables ────────────────────────────────────
    'setflag': 'Turn a flag ON — it stays on until cleared\nFlags are permanent on/off switches',
    'clearflag': 'Turn a flag OFF — resets it to its default state',
    'checkflag': 'Test if a flag is ON or OFF\nUse with goto_if/call_if to branch',
    'setvar': 'Set a variable to a specific number (0–65535)\nVariables store numeric values',
    'addvar': 'Add to a variable (variable += amount)',
    'subvar': 'Subtract from a variable (variable -= amount)',
    'copyvar': 'Copy the value of one variable into another',
    'compare_var_to_value': 'Compare a variable against a number\nUse with goto_if/call_if to branch on the result',
    'compare_var_to_var': 'Compare two variables against each other\nUse with goto_if/call_if to branch on the result',

    # ── Page 1: Flow Control ─────────────────────────────────────────
    'goto_if_eq': 'If a variable matches a value, jump to a label\nAlso supports !=, <, >=, <=, > comparisons',
    'goto_if_set': 'If a flag is ON (or OFF), jump to a label\nExecution does NOT return to this point',
    'call_if_eq': 'If a variable matches a value, call a sub-script\nExecution returns here when the called script ends',
    'call_if_set': 'If a flag is ON (or OFF), call a sub-script\nExecution returns here when the called script ends',
    'call': 'Call a sub-script by label\nExecution returns here when it hits "return"',
    'goto': 'Jump to another script label permanently\nExecution does NOT return to this point',
    'callstd': 'Call a standard library script by ID\n(nurse healing, PC access, etc.)',
    'gotostd': 'Jump to a standard library script by ID',
    'end': 'End the current script — stops execution entirely',
    'return': 'Return from a called script\nResumes at the point after the "call" command',
    'special': 'Call a built-in C function by name\n(e.g. HealPlayerParty, ShakeScreen, ChoosePartyMon)',
    'specialvar': 'Call a special function and store its result in a variable\nUsually stores into VAR_RESULT',
    'waitbuttonpress': 'Pause the script until the player presses A or B',

    # ── Page 2: Warps & Teleportation ────────────────────────────────
    'warp': 'Teleport the player to a different map\nShows the standard screen transition',
    'warpsilent': 'Teleport the player with no screen transition\nUseful for seamless map connections',
    'warpdoor': 'Teleport with a door-opening animation\nUsed for building entrances',
    'warphole': 'Teleport with a falling animation\nUsed for holes, trapdoors, ledge falls',
    'warpteleport': 'Teleport with the Teleport/Fly animation effect',

    # ── Page 2: NPC & Object Control ─────────────────────────────────
    'applymovement': 'Assign a movement route to an NPC or the player\nOpens the Move Route editor for step-by-step paths',
    'waitmovement': 'Pause the script until movement finishes\n0 = wait for all objects, or specify a local ID',
    'removeobject': 'Remove an NPC from the map (disappear)\nUsed for one-time encounters or cutscene exits',
    'addobject': 'Add a previously removed NPC back to the map',
    'showobjectat': 'Show a hidden NPC at a specific position',
    'hideobjectat': 'Hide an NPC at a specific position\nNPC stays in memory but becomes invisible',
    'faceplayer': 'Make the current NPC turn to face the player\nUsually the first command in an NPC script',
    'turnobject': 'Turn an NPC to face a specific direction\n(DIR_UP, DIR_DOWN, DIR_LEFT, DIR_RIGHT)',

    # ── Page 2: Movement Locking ─────────────────────────────────────
    'lock': 'Freeze the talking NPC in place during the script\nPrevents them from walking their route',
    'lockall': 'Freeze ALL NPCs on the map during the script',
    'release': 'Let the talking NPC resume their movement route',
    'releaseall': 'Let ALL NPCs resume their movement routes',

    # ── Page 2: Camera ───────────────────────────────────────────────
    'movecamera': 'Open the cutscene camera editor\nPan, fade, shake, weather, sound — all in one dialog\nAuto-generates the full command sequence',

    # ── Page 2: Screen Effects ───────────────────────────────────────
    'fadescreen': 'Fade the screen to/from black or white\nDefault speed — use Fade Screen (Speed) for custom timing',
    'fadescreenspeed': 'Fade the screen with a custom speed\nLower = slower fade, higher = faster fade',
    'setflashlevel': 'Set cave flash darkness instantly\n0 = fully lit, 8 = completely dark',

    # ── Page 2: Doors ────────────────────────────────────────────────
    'opendoor': 'Play the door-opening animation at X/Y coordinates',
    'closedoor': 'Play the door-closing animation at X/Y coordinates',
    'waitdooranim': 'Pause the script until the door animation finishes',

    # ── Page 2: Sound & Music ────────────────────────────────────────
    'playse': 'Play a one-shot sound effect (SE_PIN, SE_DOOR, etc.)',
    'waitse': 'Pause the script until the sound effect finishes',
    'playfanfare': 'Play a short jingle over the BGM\n(item received, evolution, etc.)',
    'waitfanfare': 'Pause the script until the fanfare jingle finishes',
    'playbgm': 'Change the background music\nReplaces whatever is currently playing',
    'fadeoutbgm': 'Gradually fade the background music to silence',
    'fadeinbgm': 'Gradually fade the background music back in',

    # ── Page 2: Weather ──────────────────────────────────────────────
    'setweather': 'Set the weather type (rain, snow, fog, etc.)\nDoesn\'t show until you use Trigger Weather',
    'doweather': 'Activate the weather set by Set Weather\nMakes the weather effect visible on screen',
    'resetweather': 'Clear custom weather and return to the map default',

    # ── Page 2: Timing ───────────────────────────────────────────────
    'delay': 'Pause the script for a number of frames\n60 frames ≈ 1 second',
    'waitstate': 'Wait for a pending game state change to complete\n(field effects, animations, etc.)',

    # ── Page 2: Map ──────────────────────────────────────────────────
    'setmetatile': 'Change a map tile at X/Y to a different metatile\nUsed for breaking rocks, cutting trees, etc.',
    'setworldmapflag': 'Mark a section as visited on the world map\nReveals the name on the Town Map',

    # ── Page 2: Text Formatting ──────────────────────────────────────
    'textcolor': 'Change the text color for subsequent messages\n(0=dark, 1=red, 2=blue, etc.)',
    'signmsg': 'Switch to signpost message box style\nChanges the text box appearance',
    'normalmsg': 'Switch back to normal dialogue box style',

    # ── Page 3: Battles ──────────────────────────────────────────────
    'trainerbattle_single': 'Start a 1v1 trainer battle\nIncludes intro text, defeat text, and optional continue script',
    'trainerbattle_double': 'Start a 2v2 trainer battle\nIncludes "not enough" text if player has only 1 party member',
    'trainerbattle_no_intro': 'Start a trainer battle with no intro dialogue\nJumps straight into battle',
    'wildbattle': 'Start a wild encounter with a specific species and level',

    # ── Page 3: Pokémon ──────────────────────────────────────────────
    'givemon': 'Give the player a creature\nSet species, level, held item, and optional custom moves',
    'giveegg': 'Give the player an egg of a species',
    'setmonmove': 'Change a move on a party member\'s moveset',
    'checkpartymove': 'Check if any party member knows a move\nUsed for field moves (Cut, Surf, Strength, etc.)',
    'getpartysize': 'Store the number of party members into VAR_RESULT',
    'checkplayergender': 'Store the player\'s gender into VAR_RESULT\n(0=male, 1=female)',
    'showmonpic': 'Display a species sprite on screen\nUsed for "received" popups and dex displays',
    'hidemonpic': 'Remove the species sprite from screen',
    'playmoncry': 'Play the cry sound for a species',

    # ── Page 3: Items ────────────────────────────────────────────────
    'finditem': 'Item ball pickup — give item + set flag + show message\nUsed for ground items the player picks up',
    'additem': 'Add an item to the player\'s bag\nNo message shown — use finditem for item balls',
    'removeitem': 'Remove an item from the player\'s bag',
    'checkitem': 'Check if the player has an item\nResult stored in VAR_RESULT (1=yes, 0=no)',
    'checkitemspace': 'Check if the player has room for an item\nResult stored in VAR_RESULT (1=yes, 0=no)',
    'pokemart': 'Open a shop with a list of items to buy\nUses a mart item list defined in the game data',

    # ── Page 3: Money & Coins ────────────────────────────────────────
    'addmoney': 'Give money to the player',
    'removemoney': 'Take money from the player',
    'checkmoney': 'Check if the player has at least this much money\nResult stored in VAR_RESULT',
    'addcoins': 'Give Game Corner coins to the player',
    'removecoins': 'Take Game Corner coins from the player',

    # ── Page 3: Buffers ──────────────────────────────────────────────
    'bufferspeciesname': 'Store a species name in a text buffer\nUse {STR_VAR_1}, {STR_VAR_2}, or {STR_VAR_3} in messages to display it',
    'bufferitemname': 'Store an item name in a text buffer\nUse {STR_VAR_1}, {STR_VAR_2}, or {STR_VAR_3} in messages to display it',
    'buffermovename': 'Store a move name in a text buffer\nUse {STR_VAR_1}, {STR_VAR_2}, or {STR_VAR_3} in messages to display it',

    # ── Page 3: Decorations ──────────────────────────────────────────
    'adddecoration': 'Add a decoration to the player\'s Secret Base inventory',
    'removedecoration': 'Remove a decoration from the player\'s Secret Base inventory',

    # ── Page 3: System ───────────────────────────────────────────────
    'setrespawn': 'Set where the player respawns after whiting out\nUsually the nearest healing location',

    # ── New commands ─────────────────────────────────────────────────
    'goto_if_unset': 'If a flag is OFF, jump to a label\nExecution does NOT return to this point',
    'call_if_unset': 'If a flag is OFF, call a sub-script\nExecution returns here when the called script ends',
    'goto_if_ne': 'If a variable does NOT equal a value, jump to a label',
    'goto_if_lt': 'If a variable is LESS than a value, jump to a label',
    'goto_if_gt': 'If a variable is GREATER than a value, jump to a label',
    'goto_if_le': 'If a variable is ≤ a value, jump to a label',
    'goto_if_ge': 'If a variable is ≥ a value, jump to a label',
    'call_if_ne': 'If a variable does NOT equal a value, call a sub-script',
    'call_if_lt': 'If a variable is LESS than a value, call a sub-script',
    'call_if_gt': 'If a variable is GREATER than a value, call a sub-script',
    'call_if_le': 'If a variable is ≤ a value, call a sub-script',
    'call_if_ge': 'If a variable is ≥ a value, call a sub-script',
    'trainerbattle_rematch': 'Start a rematch trainer battle\nUsed with the VS Seeker system',
    'trainerbattle_earlyrival': 'Start an early rival battle\nSpecial variant with unique intro handling',
    'trainerbattle_rematch_double': 'Start a rematch double battle\nVS Seeker system with two-on-two format',
    'setobjectxy': 'Move an NPC to a new position instantly\nOnly lasts until the map reloads',
    'setobjectxyperm': 'Move an NPC to a new position permanently\nPersists across map reloads until flag/var resets it',
    'setobjectmovementtype': 'Change an NPC\'s movement behavior\n(e.g. from standing still to walking a route)',
    'getplayerxy': 'Store the player\'s X and Y position into two variables',
    'random': 'Generate a random number from 0 to (max-1)\nResult stored in VAR_RESULT',
    'savebgm': 'Save the currently playing music so it can be restored later',
    'fadedefaultbgm': 'Fade back to the previously saved background music',
    'healplayerteam': 'Fully heal the player\'s entire party\n(HP, PP, status — like visiting a healing center)',
    'buffernumberstring': 'Store a number as text in a buffer\nUse {STR_VAR_1}, {STR_VAR_2}, or {STR_VAR_3} in messages',
    'bufferstring': 'Store a raw text string in a buffer\nUse {STR_VAR_1}, {STR_VAR_2}, or {STR_VAR_3} in messages',
}


class _CommandSelectorDialog(QDialog):
    """3-page tabbed command selector modeled after RPG Maker XP.

    Each page uses a 2-column button grid layout matching the RMXP
    Event Commands dialog screenshot. A "Recent" row at the top shows
    the last 8 commands the user picked (session-persistent).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Event Commands')
        self.resize(520, 580)
        self.selected = None

        layout = QVBoxLayout(self)

        # Search bar
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel('Search:'))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText('Type to filter commands...')
        self.search_edit.textChanged.connect(self._on_search)
        search_row.addWidget(self.search_edit, 1)
        layout.addLayout(search_row)

        # ── Recent commands row ─────────────────────────────────────────
        self._recent_frame = QWidget()
        recent_layout = QVBoxLayout(self._recent_frame)
        recent_layout.setContentsMargins(0, 0, 0, 4)
        lbl = QLabel('<b>Recent</b>')
        recent_layout.addWidget(lbl)
        self._recent_btn_layout = QHBoxLayout()
        self._recent_btn_layout.setSpacing(4)
        self._recent_buttons: list[tuple[QPushButton, str, str]] = []
        self._populate_recent()
        recent_layout.addLayout(self._recent_btn_layout)
        layout.addWidget(self._recent_frame)
        if not _RECENT_COMMANDS:
            self._recent_frame.hide()

        # 3-page tabs (labeled 1, 2, 3 like RMXP)
        self.tabs = QTabWidget()
        self._all_buttons: list[tuple[QPushButton, str, str]] = []

        pages = [
            ('1', _PAGE_1_COMMANDS),
            ('2', _PAGE_2_COMMANDS),
            ('3', _PAGE_3_COMMANDS),
        ]
        for page_name, sections in pages:
            page_widget = QWidget()
            page_layout = QVBoxLayout(page_widget)
            page_layout.setSpacing(4)

            # Flatten all commands from all sections into a single list
            # and lay them out in a 2-column grid
            from PyQt6.QtWidgets import QGridLayout
            grid = QGridLayout()
            grid.setSpacing(4)
            row_idx = 0

            for section_name, cmds in sections:
                for friendly, raw in cmds:
                    col = row_idx % 2
                    btn = QPushButton(f'{friendly}...' if friendly[-3:] != '...' else friendly)
                    btn.setMinimumHeight(26)
                    tip = _CMD_TOOLTIPS.get(raw, '')
                    if tip:
                        btn.setToolTip(_tt(tip))
                    btn.clicked.connect(lambda _, c=raw: self._choose(c))
                    grid.addWidget(btn, row_idx // 2, col)
                    self._all_buttons.append((btn, friendly.lower(), raw))
                    row_idx += 1

            page_layout.addLayout(grid)
            page_layout.addStretch()
            scroll = QScrollArea()
            scroll.setWidget(page_widget)
            scroll.setWidgetResizable(True)
            self.tabs.addTab(scroll, page_name)

        layout.addWidget(self.tabs, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _populate_recent(self):
        """Build buttons for recently used commands."""
        # Clear existing buttons
        for btn, _, _ in self._recent_buttons:
            btn.deleteLater()
        self._recent_buttons.clear()
        for cmd in _RECENT_COMMANDS:
            friendly = _CMD_FRIENDLY_NAMES.get(cmd, cmd.replace('_', ' ').title())
            btn = QPushButton(friendly)
            btn.setMinimumHeight(24)
            btn.setStyleSheet('font-size: 11px;')
            tip = _CMD_TOOLTIPS.get(cmd, '')
            if tip:
                btn.setToolTip(_tt(tip))
            btn.clicked.connect(lambda _, c=cmd: self._choose(c))
            self._recent_btn_layout.addWidget(btn)
            self._recent_buttons.append((btn, friendly.lower(), cmd))
        self._recent_btn_layout.addStretch()

    def _choose(self, cmd):
        _record_recent(cmd)
        self.selected = cmd
        self.accept()

    def _on_search(self, text):
        text = text.lower().strip()
        for btn, friendly, raw in self._all_buttons:
            visible = not text or text in friendly or text in raw
            btn.setVisible(visible)
        # Also filter recent buttons
        for btn, friendly, raw in self._recent_buttons:
            visible = not text or text in friendly or text in raw
            btn.setVisible(visible)
        # Show/hide the recent section based on whether any match
        if text:
            any_visible = any(btn.isVisible() for btn, _, _ in self._recent_buttons)
            self._recent_frame.setVisible(any_visible)
        else:
            self._recent_frame.setVisible(bool(_RECENT_COMMANDS))

    @staticmethod
    def get_command(parent=None):
        dlg = _CommandSelectorDialog(parent)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            return dlg.selected
        return None


# ═════════════════════════════════════════════════════════════════════════════
# Hidden Item property panel (replaces command list for hidden_item bg_events)
# ═════════════════════════════════════════════════════════════════════════════

class _HiddenItemPanel(QWidget):
    """Property editor shown instead of the command list when a hidden item
    bg_event is selected.  Hidden items are pure data — no script needed."""

    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 0, 0, 0)

        # Header
        header = QLabel('Hidden Item Properties')
        header.setStyleSheet('font-size: 14px; font-weight: bold; margin-bottom: 4px;')
        layout.addWidget(header)
        note = QLabel('This is a hidden item — the player finds it by pressing A '
                       'on this tile (or using Itemfinder).\nNo script needed — '
                       'the game engine handles pickup automatically.')
        note.setWordWrap(True)
        note.setStyleSheet('color: #999; font-size: 11px; margin-bottom: 8px;')
        layout.addWidget(note)

        form = QFormLayout()
        form.setSpacing(8)

        # Item picker
        self.item_picker = ConstantPicker(ConstantsManager.ITEMS, prefix='ITEM_')
        self.item_picker.setToolTip(_tt(
            'Which item the player picks up from this tile'))
        self.item_picker.wheelEvent = lambda e: e.ignore()
        self.item_picker.currentIndexChanged.connect(self._emit_changed)
        form.addRow('Item:', self.item_picker)

        # Flag picker
        self.flag_picker = ConstantPicker(ConstantsManager.FLAGS, prefix='FLAG_')
        self.flag_picker.setToolTip(_tt(
            'Flag that tracks whether this item was already collected\n'
            'Once set, the item won\'t appear again'))
        self.flag_picker.wheelEvent = lambda e: e.ignore()
        self.flag_picker.currentIndexChanged.connect(self._emit_changed)
        form.addRow('Flag:', self.flag_picker)

        # Quantity
        self.qty_spin = QSpinBox()
        self.qty_spin.setRange(1, 99)
        self.qty_spin.setValue(1)
        self.qty_spin.setToolTip(_tt('How many of this item to give (usually 1)'))
        self.qty_spin.valueChanged.connect(self._emit_changed)
        form.addRow('Quantity:', self.qty_spin)

        # Position
        pos_row = QHBoxLayout()
        self.x_spin = QSpinBox()
        self.x_spin.setRange(0, 999)
        self.x_spin.setToolTip(_tt('X position on the map (tiles from left edge)'))
        self.x_spin.valueChanged.connect(self._emit_changed)
        pos_row.addWidget(QLabel('X:'))
        pos_row.addWidget(self.x_spin)
        self.y_spin = QSpinBox()
        self.y_spin.setRange(0, 999)
        self.y_spin.setToolTip(_tt('Y position on the map (tiles from top edge)'))
        self.y_spin.valueChanged.connect(self._emit_changed)
        pos_row.addWidget(QLabel('Y:'))
        pos_row.addWidget(self.y_spin)
        pos_row.addStretch()
        form.addRow('Position:', pos_row)

        # Elevation
        self.elev_spin = QSpinBox()
        self.elev_spin.setRange(0, 15)
        self.elev_spin.setValue(0)
        self.elev_spin.setToolTip(_tt(
            'Tile elevation layer (0 = ground, 3 = standard walkable)'))
        self.elev_spin.valueChanged.connect(self._emit_changed)
        form.addRow('Elevation:', self.elev_spin)

        # Underfoot checkbox
        self.underfoot_check = QCheckBox('Requires Itemfinder')
        self.underfoot_check.setToolTip(_tt(
            'If checked, the item is only detectable with the Itemfinder —\n'
            'pressing A alone won\'t find it'))
        self.underfoot_check.stateChanged.connect(self._emit_changed)
        form.addRow('', self.underfoot_check)

        layout.addLayout(form)
        layout.addStretch()

        # Delete button
        self.btn_delete = QPushButton('Delete This Hidden Item')
        self.btn_delete.setToolTip(_tt('Remove this hidden item from the map'))
        self.btn_delete.setStyleSheet(
            'QPushButton { color: #e74c3c; }'
            'QPushButton:hover { background: #3a1a1a; }')
        layout.addWidget(self.btn_delete)

    def _emit_changed(self):
        self.changed.emit()

    def load(self, obj: dict):
        """Populate fields from a hidden_item bg_event dict."""
        self.blockSignals(True)
        self.item_picker.set_constant(obj.get('item', ''))
        self.flag_picker.set_constant(obj.get('flag', ''))
        self.qty_spin.setValue(int(obj.get('quantity', 1)))
        self.x_spin.setValue(int(obj.get('x', 0)))
        self.y_spin.setValue(int(obj.get('y', 0)))
        self.elev_spin.setValue(int(obj.get('elevation', 0)))
        self.underfoot_check.setChecked(bool(obj.get('underfoot', False)))
        self.blockSignals(False)

    def collect(self) -> dict:
        """Return field values as a dict (keys match map.json)."""
        return {
            'item': self.item_picker.selected_constant(),
            'flag': self.flag_picker.selected_constant(),
            'quantity': self.qty_spin.value(),
            'x': self.x_spin.value(),
            'y': self.y_spin.value(),
            'elevation': self.elev_spin.value(),
            'underfoot': self.underfoot_check.isChecked(),
        }


# ═════════════════════════════════════════════════════════════════════════════
# Drag-reorderable command list
# ═════════════════════════════════════════════════════════════════════════════

class _DraggableCommandList(QListWidget):
    """QListWidget with internal drag-and-drop reorder support.

    Emits ``rows_reordered`` after a drop so the parent can rebuild
    ``_cmd_tuples`` from the new visual order.
    """
    rows_reordered = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)

    def dropEvent(self, event):
        super().dropEvent(event)
        # After the drop completes, emit so the data layer can sync
        self.rows_reordered.emit()


# ═════════════════════════════════════════════════════════════════════════════
# Main Event Editor Tab
# ═════════════════════════════════════════════════════════════════════════════

class EventEditorTab(QWidget):
    # Emitted when the user makes any edit (commands, properties, pages).
    # The main window connects this to setWindowModified(True).
    data_changed = pyqtSignal()

    # Phase 3: cross-editor navigation signals
    # Emitted when user double-clicks a TRAINER_ constant → jump to Trainers tab
    jump_to_trainer = pyqtSignal(str)   # trainer constant e.g. "TRAINER_HIKER_BOB"
    # Emitted when user double-clicks an ITEM_ constant → jump to Items tab
    jump_to_item = pyqtSignal(str)      # item constant e.g. "ITEM_POTION"
    # Emitted when user wants to edit a flag/var label → jump to Label Manager
    jump_to_label = pyqtSignal(str)     # flag/var constant e.g. "FLAG_GOT_STARTER"
    # Phase 7: Porymap sync — emitted when a map is loaded so Porymap can follow
    map_loaded = pyqtSignal(str)         # map folder name e.g. "PalletTown"

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self._mw = main_window
        self.project_info = None
        self._root_dir = None
        self._map_dir = None
        self._map_data = None
        self._objects = []
        self._texts = OrderedDict()
        self._local_text_labels: set[str] = set()
        self._external_script_labels: set[str] = set()  # labels from data/scripts/
        self._external_script_files: dict[str, Path] = {}  # label → source file path
        self._hidden_lines = {}
        self._pages = {}
        self._page_types = {}
        self._current_obj_idx = -1
        self._current_page_idx = 0
        self._cmd_tuples: list[tuple] = []
        self._loading = False  # True while populating UI fields (suppress dirty)
        self._script_index = None  # Built in load_project()
        self._build_ui()

    def _mark_dirty(self):
        """Mark the project as having unsaved changes."""
        if not self._loading:
            # Commit the active page's live command edits back into both
            # the per-event page dict AND _all_scripts[label] so other
            # tabs (e.g. Trainers → Dialogue) can see in-RAM script edits
            # without needing the user to save first.
            self._sync_live_script_state()
            self.data_changed.emit()

    def _sync_live_script_state(self) -> None:
        """Push live script edits into _all_scripts so cross-tab readers
        can see them without a save first.

        Two steps:
          1. Commit the active page's _cmd_tuples back into its page
             dict (canonical per-event store).
          2. Mirror EVERY page of the current event into
             _all_scripts[page_label]. This covers the active page AND
             any non-active pages that were edited directly (e.g.
             _on_rename_page rewrites commands across sibling pages).

        _ALL_SCRIPTS is the same dict object as self._all_scripts (set
        at map-load time), so updating self._all_scripts implicitly
        updates the module-level mirror that other tabs consume.
        Idempotent — safe to call after every _mark_dirty().
        """
        if self._current_obj_idx < 0 or self._current_obj_idx >= len(self._objects):
            return
        obj = self._objects[self._current_obj_idx]
        pages = obj.get('_pages', [])
        # (1) Flush _cmd_tuples into the active page dict.
        if 0 <= self._current_page_idx < len(pages):
            pages[self._current_page_idx]['commands'] = list(self._cmd_tuples)
        # (2) Mirror every page's commands into _all_scripts[label].
        default_label = obj.get('script', '')
        for page in pages:
            label = page.get('_label') or default_label
            if not label or label == '0x0':
                continue
            cmds = page.get('commands', [])
            if isinstance(cmds, list):
                self._all_scripts[label] = list(cmds)

    def has_unsaved_changes(self) -> bool:
        """Return True if the window has been marked as modified."""
        return self._mw.isWindowModified()

    def _check_unsaved_before_map_switch(self) -> bool:
        """Check for unsaved changes before loading a different map.

        Returns True if it's OK to proceed (saved, discarded, or no changes).
        Returns False if the user cancelled.
        """
        if not self._mw.isWindowModified():
            return True
        from app_util import create_unsaved_changes_dialog
        ret = create_unsaved_changes_dialog(
            self, 'You have unsaved changes to the current map.\n'
                  'Would you like to save before switching?')
        from PyQt6.QtWidgets import QMessageBox
        if ret == QMessageBox.StandardButton.Save:
            self._on_save()
            return True
        elif ret == QMessageBox.StandardButton.Discard:
            return True
        else:  # Cancel
            return False

    @staticmethod
    def _divider_line():
        """Return a thin vertical separator line for toolbar rows."""
        line = QFrame()
        line.setFrameShape(QFrame.Shape.VLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        line.setFixedWidth(2)
        return line

    # ── Live settings reload ─────────────────────────────────────────────
    def reload_settings(self):
        """Re-read ALL event editor settings and apply immediately.

        Called by the unified main window after the Settings dialog closes.
        Handles colors, tooltips, and anything else cached at import time.
        """
        # 1. Reload colors + tooltip flag from settings.ini
        _load_color_settings()

        # 2. Recolor every item in the current command list
        from PyQt6.QtGui import QBrush
        for i in range(self._cmd_list.count()):
            item = self._cmd_list.item(i)
            if item:
                cmd_tuple = item.data(Qt.ItemDataRole.UserRole)
                if cmd_tuple:
                    # Reset to default foreground, then re-apply category color
                    item.setForeground(QBrush())
                    _apply_cmd_color(item, cmd_tuple)

        # 3. Toggle tooltip visibility
        _apply_tooltip_visibility(self, _EVENT_TOOLTIPS_ENABLED)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        # ── Top bar: map selector ────────────────────────────────────────
        top = QHBoxLayout()
        self.btn_open = QPushButton('Open Map')
        self.btn_open.setToolTip(_tt('Choose a map to edit its events and scripts'))
        self.btn_open.setEnabled(False)
        self.btn_open.clicked.connect(self._on_open_map)
        top.addWidget(self.btn_open)
        self.map_label = QLabel('No map loaded')
        self.map_label.setToolTip(_tt('Currently loaded map name'))
        top.addWidget(self.map_label, 1)
        root.addLayout(top)

        # ── Page control row (RMXP-style: page buttons at top) ───────────
        page_ctrl = QHBoxLayout()
        btn_add = QPushButton('New Page')
        btn_add.setToolTip(_tt('Add a new script page (sub-label) to this event\nPages are condition-based branches like RPG Maker event pages'))
        btn_add.clicked.connect(self._on_add_page)
        btn_rename = QPushButton('Rename')
        btn_rename.setToolTip(_tt('Rename the current page label\nAlso updates all goto/call references to the old name'))
        btn_rename.clicked.connect(self._on_rename_page)
        btn_del = QPushButton('Delete Page')
        btn_del.setToolTip(_tt('Delete the current page and all its commands'))
        btn_del.clicked.connect(self._on_del_page)
        self._btn_new_script = QPushButton('New Script ▾')
        self._btn_new_script.setToolTip(_tt(
            'Create a new script from a template'))
        self._btn_new_script.clicked.connect(self._on_new_npc_script)
        btn_find_flag = QPushButton('Find Unused Flag')
        btn_find_flag.setToolTip(_tt('Scan project for the next available unused flag'))
        btn_find_flag.clicked.connect(self._on_find_unused_flag)
        btn_find_script = QPushButton('Find Script')
        btn_find_script.setToolTip(_tt('Search for script labels across all maps (Ctrl+Shift+F)'))
        btn_find_script.clicked.connect(self._on_find_script)
        page_ctrl.addWidget(btn_add)
        page_ctrl.addWidget(btn_rename)
        page_ctrl.addWidget(btn_del)
        page_ctrl.addWidget(self._divider_line())
        page_ctrl.addWidget(self._btn_new_script)
        page_ctrl.addWidget(btn_find_flag)
        page_ctrl.addWidget(btn_find_script)
        page_ctrl.addWidget(self._divider_line())
        self._btn_open_porymap = QPushButton('Open in Porymap')
        self._btn_open_porymap.setToolTip('Open the current map in Porymap (Ctrl+F7)')
        self._btn_open_porymap.clicked.connect(self._on_open_in_porymap)
        page_ctrl.addWidget(self._btn_open_porymap)
        page_ctrl.addStretch()
        self.btn_save = QPushButton('Save')
        self.btn_save.setToolTip(_tt('Save all changes to scripts.inc, text.inc, and map.json (Ctrl+S)'))
        self.btn_save.clicked.connect(self._on_save)
        page_ctrl.addWidget(self.btn_save)
        root.addLayout(page_ctrl)

        # ── Page tabs (script labels as RMXP event pages) ────────────────
        self.page_tabs = QTabWidget()
        self.page_tabs.setTabPosition(QTabWidget.TabPosition.North)
        self.page_tabs.setUsesScrollButtons(True)
        self.page_tabs.setMaximumHeight(40)
        self.page_tabs.setToolTip(_tt('Script pages — each page is a sub-label in the script\nLike RPG Maker event pages with different conditions'))
        self.page_tabs.currentChanged.connect(self._on_page_changed)
        root.addWidget(self.page_tabs)

        # ── Main splitter: properties (left) + commands (right) ──────────
        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter, 1)

        # ── Left: event selector + properties (RMXP left panel) ──────────
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)

        obj_row = QHBoxLayout()
        obj_row.addWidget(QLabel('Event:'))
        self.obj_combo = QComboBox()
        self.obj_combo.setToolTip(_tt('Select an event on this map to edit\nShows NPCs, triggers, signs, and map scripts'))
        self.obj_combo.wheelEvent = lambda e: e.ignore()
        self.obj_combo.currentIndexChanged.connect(self._on_object_changed)
        obj_row.addWidget(self.obj_combo, 1)
        ll.addLayout(obj_row)

        # ── Conditions GroupBox (RMXP-style — above Event Properties) ───
        self._conditions_box = QGroupBox('Conditions')
        self._conditions_box.setToolTip(_tt(
            'Set when this page activates — like RPG Maker event page conditions\n'
            'Flag and Variable are mutually exclusive (only one can be active)'))
        cond_layout = QVBoxLayout(self._conditions_box)
        cond_layout.setContentsMargins(8, 8, 8, 8)
        cond_layout.setSpacing(6)

        # Flag condition row — editable
        flag_row = QHBoxLayout()
        self._cond_flag_check = QCheckBox('Flag')
        self._cond_flag_check.setToolTip(_tt(
            'Enable a flag condition for this page\n'
            'The page activates when the chosen flag is ON or OFF'))
        self._cond_flag_check.toggled.connect(self._on_cond_flag_toggled)
        flag_row.addWidget(self._cond_flag_check)
        self._cond_flag_picker = QComboBox()
        self._cond_flag_picker.setEditable(True)
        self._cond_flag_picker.setMinimumWidth(120)
        self._cond_flag_picker.setToolTip(_tt('Choose which flag controls this page — type to search'))
        self._cond_flag_picker.wheelEvent = lambda e: e.ignore()
        self._cond_flag_picker.setEnabled(False)
        self._cond_flag_picker.currentTextChanged.connect(self._on_cond_changed)
        flag_row.addWidget(self._cond_flag_picker, 1)
        self._cond_flag_state = QComboBox()
        self._cond_flag_state.addItems(['is ON', 'is OFF'])
        self._cond_flag_state.setToolTip(_tt('Page activates when flag is ON or OFF'))
        self._cond_flag_state.wheelEvent = lambda e: e.ignore()
        self._cond_flag_state.setEnabled(False)
        self._cond_flag_state.currentIndexChanged.connect(self._on_cond_changed)
        flag_row.addWidget(self._cond_flag_state)
        cond_layout.addLayout(flag_row)

        # Variable condition row — editable
        var_row = QHBoxLayout()
        self._cond_var_check = QCheckBox('Variable')
        self._cond_var_check.setToolTip(_tt(
            'Enable a variable condition for this page\n'
            'The page activates when the variable meets the comparison'))
        self._cond_var_check.toggled.connect(self._on_cond_var_toggled)
        var_row.addWidget(self._cond_var_check)
        self._cond_var_picker = QComboBox()
        self._cond_var_picker.setEditable(True)
        self._cond_var_picker.setMinimumWidth(120)
        self._cond_var_picker.setToolTip(_tt('Choose which variable controls this page — type to search'))
        self._cond_var_picker.wheelEvent = lambda e: e.ignore()
        self._cond_var_picker.setEnabled(False)
        self._cond_var_picker.currentTextChanged.connect(self._on_cond_changed)
        var_row.addWidget(self._cond_var_picker, 1)
        self._cond_var_op = QComboBox()
        self._cond_var_op.addItems(['==', '!=', '<', '>=', '<=', '>'])
        self._cond_var_op.setToolTip(_tt('Comparison operator — how the variable is compared to the value'))
        self._cond_var_op.wheelEvent = lambda e: e.ignore()
        self._cond_var_op.setEnabled(False)
        self._cond_var_op.setMaximumWidth(60)
        self._cond_var_op.currentIndexChanged.connect(self._on_cond_changed)
        var_row.addWidget(self._cond_var_op)
        self._cond_var_val = QSpinBox()
        self._cond_var_val.setRange(0, 65535)
        self._cond_var_val.setToolTip(_tt('The value to compare the variable against (0–65535)'))
        self._cond_var_val.setEnabled(False)
        self._cond_var_val.valueChanged.connect(self._on_cond_changed)
        var_row.addWidget(self._cond_var_val)
        cond_layout.addLayout(var_row)

        ll.addWidget(self._conditions_box)

        # Object properties
        props = QGroupBox('Event Properties')
        props.setToolTip(_tt('Properties of the currently selected event object'))
        pf = QFormLayout(props)
        self.obj_id_edit = QLineEdit()
        self.obj_id_edit.setPlaceholderText('local_id')
        self.obj_id_edit.setToolTip(_tt(
            'Local ID number for this event on the map\n'
            'Used by commands like applymovement and removeobject to target this NPC'))
        pf.addRow('ID:', self.obj_id_edit)
        pos_row = QHBoxLayout()
        self.x_spin = QSpinBox(); self.x_spin.setRange(0, 999)
        self.x_spin.setToolTip(_tt('X position on the map (tiles from left edge)'))
        pos_row.addWidget(QLabel('X:')); pos_row.addWidget(self.x_spin)
        self.y_spin = QSpinBox(); self.y_spin.setRange(0, 999)
        self.y_spin.setToolTip(_tt('Y position on the map (tiles from top edge)'))
        pos_row.addWidget(QLabel('Y:')); pos_row.addWidget(self.y_spin)
        pf.addRow('Position:', pos_row)
        self.script_edit = QLineEdit()
        self.script_edit.setPlaceholderText('script label')
        self.script_edit.setToolTip(_tt(
            'The script label this event runs when activated\n'
            'This is the entry point label in scripts.inc'))
        pf.addRow('Script:', self.script_edit)
        self.gfx_combo = QComboBox()
        self.gfx_combo.setEditable(True)
        self.gfx_combo.setToolTip(_tt(
            'The sprite graphic for this NPC\n'
            'OBJ_EVENT_GFX constants — type to search'))
        self.gfx_combo.wheelEvent = lambda e: e.ignore()
        self.gfx_combo.currentTextChanged.connect(self._on_gfx_changed)
        pf.addRow('Graphic:', self.gfx_combo)

        # Mark dirty when the user edits any property field
        self.obj_id_edit.textEdited.connect(lambda: self._mark_dirty())
        self.x_spin.valueChanged.connect(lambda: self._mark_dirty())
        self.y_spin.valueChanged.connect(lambda: self._mark_dirty())
        self.script_edit.textEdited.connect(lambda: self._mark_dirty())
        self.gfx_combo.currentIndexChanged.connect(lambda: self._mark_dirty())
        ll.addWidget(props)

        # Cross-reference: scripts that modify this object's position
        self._xref_label = QLabel('')
        self._xref_label.setWordWrap(True)
        self._xref_label.setTextFormat(Qt.TextFormat.RichText)
        self._xref_label.setOpenExternalLinks(False)
        self._xref_label.setStyleSheet(
            'font-size: 11px; padding: 2px 4px;')
        self._xref_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction)
        self._xref_label.linkActivated.connect(self._on_xref_clicked)
        self._xref_label.hide()
        ll.addWidget(self._xref_label)

        # Sprite preview
        self.sprite_preview = SpritePreview()
        self.sprite_preview.setToolTip(_tt('Animated preview of the selected NPC sprite'))
        ll.addWidget(self.sprite_preview)

        self._open_sprite_btn = QPushButton("Open Sprite in Folder")
        self._open_sprite_btn.setToolTip(_tt('Open the sprite PNG file in your file manager for manual editing'))
        self._open_sprite_btn.setStyleSheet(
            "background: #2a2a3a; color: #aac; border: 1px solid #3a3a4a; "
            "padding: 4px 12px; border-radius: 3px; font-size: 10px;"
        )
        self._open_sprite_btn.clicked.connect(self._open_sprite_folder)
        ll.addWidget(self._open_sprite_btn)

        ll.addStretch()
        splitter.addWidget(left)

        # ── Right: RMXP-style command list (primary area) ────────────────
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(4, 0, 0, 0)
        rl.addWidget(QLabel('List of Event Commands:'))

        # ── Inline search bar (Ctrl+F) ─────────────────────────────────
        self._search_bar = QWidget()
        sb_layout = QHBoxLayout(self._search_bar)
        sb_layout.setContentsMargins(0, 0, 0, 2)
        sb_layout.addWidget(QLabel('Find:'))
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText('Search commands... (Ctrl+F)')
        self._search_edit.textChanged.connect(self._on_search_commands)
        self._search_edit.returnPressed.connect(self._on_search_next)
        sb_layout.addWidget(self._search_edit, 1)
        btn_next = QPushButton('Next')
        btn_next.setToolTip(_tt('Jump to next matching command (Enter)'))
        btn_next.clicked.connect(self._on_search_next)
        btn_next.setMaximumWidth(50)
        sb_layout.addWidget(btn_next)
        btn_prev = QPushButton('Prev')
        btn_prev.setToolTip(_tt('Jump to previous matching command'))
        btn_prev.clicked.connect(self._on_search_prev)
        btn_prev.setMaximumWidth(50)
        sb_layout.addWidget(btn_prev)
        self._search_count_lbl = QLabel('')
        self._search_count_lbl.setToolTip(_tt('Number of commands matching the search'))
        sb_layout.addWidget(self._search_count_lbl)
        btn_close_search = QPushButton('×')
        btn_close_search.setToolTip(_tt('Close search bar (Esc)'))
        btn_close_search.setMaximumWidth(24)
        btn_close_search.clicked.connect(self._close_search)
        sb_layout.addWidget(btn_close_search)
        self._search_bar.hide()
        rl.addWidget(self._search_bar)

        # Ctrl+F shortcut (in-page search)
        from PyQt6.QtGui import QShortcut, QKeySequence
        find_shortcut = QShortcut(QKeySequence('Ctrl+F'), self)
        find_shortcut.activated.connect(self._toggle_search)
        # Ctrl+Shift+F shortcut (project-wide script search)
        find_script_shortcut = QShortcut(QKeySequence('Ctrl+Shift+F'), self)
        find_script_shortcut.activated.connect(self._on_find_script)

        self._cmd_list = _DraggableCommandList()
        self._cmd_list.setToolTip(_tt(
            'Script commands for this event page\n'
            'Double-click to edit • Right-click for context menu • Drag to reorder'))
        self._cmd_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._cmd_list.setWordWrap(True)
        self._cmd_list.itemDoubleClicked.connect(self._on_edit_command)
        self._cmd_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._cmd_list.customContextMenuRequested.connect(self._on_cmd_context_menu)
        self._cmd_list.rows_reordered.connect(self._on_rows_moved)
        self._cmd_list.setObjectName('cmdList')
        self._cmd_list.setStyleSheet(
            '#cmdList { font-size: 12px; }'
            '#cmdList::item { padding: 2px 4px; }'
            '#cmdList::item:selected { background: #3498db; color: white; }'
            '#cmdList::item:hover { background: palette(midlight); }')
        rl.addWidget(self._cmd_list, 1)

        # Command action buttons
        cmd_btns = QHBoxLayout()
        self.btn_add_cmd = QPushButton('Add Command')
        self.btn_add_cmd.setToolTip(_tt('Insert a new command at the selected position\nOpens the command selector with all available script commands'))
        self.btn_add_cmd.clicked.connect(self._on_add_command)
        cmd_btns.addWidget(self.btn_add_cmd)
        self.btn_del_cmd = QPushButton('Delete')
        self.btn_del_cmd.setToolTip(_tt('Delete the selected command from the list'))
        self.btn_del_cmd.clicked.connect(self._on_del_command)
        cmd_btns.addWidget(self.btn_del_cmd)
        self.btn_move_up = QPushButton('▲ Up')
        self.btn_move_up.setToolTip(_tt('Move the selected command up one position'))
        self.btn_move_up.clicked.connect(self._on_move_up)
        self.btn_move_up.setMaximumWidth(60)
        cmd_btns.addWidget(self.btn_move_up)
        self.btn_move_down = QPushButton('▼ Down')
        self.btn_move_down.setToolTip(_tt('Move the selected command down one position'))
        self.btn_move_down.clicked.connect(self._on_move_down)
        self.btn_move_down.setMaximumWidth(60)
        cmd_btns.addWidget(self.btn_move_down)
        self.btn_duplicate = QPushButton('Duplicate')
        self.btn_duplicate.setToolTip(_tt('Create a copy of the selected command below it'))
        self.btn_duplicate.clicked.connect(self._on_duplicate)
        cmd_btns.addWidget(self.btn_duplicate)
        self.btn_goto = QPushButton('Go To →')
        self.btn_goto.setToolTip(_tt(
            'Follow a goto/call/trainerbattle target to its script'))
        self.btn_goto.clicked.connect(self._on_goto_target)
        cmd_btns.addWidget(self.btn_goto)
        cmd_btns.addStretch()
        rl.addLayout(cmd_btns)

        # Second row: copy/cut/paste
        cmd_btns2 = QHBoxLayout()
        btn_copy = QPushButton('Copy')
        btn_copy.setToolTip(_tt('Copy the selected command to the clipboard'))
        btn_copy.clicked.connect(self._on_copy)
        cmd_btns2.addWidget(btn_copy)
        btn_cut = QPushButton('Cut')
        btn_cut.setToolTip(_tt('Cut the selected command (copy and delete)'))
        btn_cut.clicked.connect(self._on_cut)
        cmd_btns2.addWidget(btn_cut)
        btn_paste = QPushButton('Paste')
        btn_paste.setToolTip(_tt('Paste a previously copied command below the selection'))
        btn_paste.clicked.connect(self._on_paste)
        cmd_btns2.addWidget(btn_paste)
        btn_find = QPushButton('Find (Ctrl+F)')
        btn_find.setToolTip(_tt('Search within the current command list\nHighlights matching commands with Next/Prev navigation'))
        btn_find.clicked.connect(self._toggle_search)
        cmd_btns2.addWidget(btn_find)
        cmd_btns2.addStretch()
        rl.addLayout(cmd_btns2)

        # ── Hidden Item panel (shown instead of commands for hidden_item) ──
        self._hidden_item_panel = _HiddenItemPanel()
        self._hidden_item_panel.changed.connect(self._on_hidden_item_changed)
        self._hidden_item_panel.btn_delete.clicked.connect(
            self._delete_hidden_item)

        # Stack: 0 = command list, 1 = hidden item editor
        self._right_stack = QStackedWidget()
        self._right_stack.addWidget(right)
        self._right_stack.addWidget(self._hidden_item_panel)

        splitter.addWidget(self._right_stack)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

    # ─────────────────────────────────────────────────────────────────────
    # Project loading
    # ─────────────────────────────────────────────────────────────────────

    def load_project(self, project_info: dict):
        self.project_info = project_info
        self._root_dir = Path(project_info.get('dir', ''))

        # Load all constants via ConstantsManager
        try:
            ConstantsManager.load(str(self._root_dir))
        except Exception as e:
            self._mw.log_message(f'Event Editor: constants load error: {e}')

        # Also load the old module-level constants for the script parser
        from eventide.backend.eventide_utils import load_project_constants
        try:
            load_project_constants(str(self._root_dir))
        except Exception:
            pass

        # GFX combo
        self._refresh_gfx_combo()

        self.btn_open.setEnabled(True)
        self.map_label.setText('No map loaded — click Open Map')

        # Build project-wide script label index
        from eventide.backend.script_index import ScriptIndex
        self._script_index = ScriptIndex()
        n_labels = self._script_index.build_index(self._root_dir)

        counts = (f'{len(ConstantsManager.ITEMS)} items, '
                  f'{len(ConstantsManager.SPECIES)} species, '
                  f'{len(ConstantsManager.MOVES)} moves, '
                  f'{len(ConstantsManager.FLAGS)} flags, '
                  f'{len(ConstantsManager.TRAINERS)} trainers, '
                  f'{n_labels} script labels')
        self._mw.log_message(f'Event Editor: ready ({counts})')

    def _refresh_gfx_combo(self) -> None:
        """Repopulate the graphics dropdown from ConstantsManager.

        Called during load_project and whenever new OW sprites are added
        so the event editor immediately sees new OBJ_EVENT_GFX_ constants.
        """
        prev = self.gfx_combo.currentText()
        # Block signals during repopulation — changing the combo index
        # fires currentIndexChanged → _mark_dirty, which falsely marks
        # EVENTide as having unsaved changes.
        self.gfx_combo.blockSignals(True)
        self.gfx_combo.clear()
        for gfx_const in sorted(ConstantsManager.OBJECT_GFX):
            self.gfx_combo.addItem(gfx_const)
        # Restore previous selection if it still exists
        if prev:
            idx = self.gfx_combo.findText(prev)
            if idx >= 0:
                self.gfx_combo.setCurrentIndex(idx)
        self.gfx_combo.blockSignals(False)

    def refresh_gfx_constants(self) -> None:
        """Public method for cross-tab refresh of GFX constants.

        Called by the unified main window when the Overworld editor adds
        new sprites. Re-reads ConstantsManager and repopulates the combo.
        """
        self._refresh_gfx_combo()

    # ─────────────────────────────────────────────────────────────────────
    # Map loading
    # ─────────────────────────────────────────────────────────────────────

    def _on_open_map(self):
        if not self._root_dir:
            return
        # Check for unsaved changes before switching maps
        if not self._check_unsaved_before_map_switch():
            return
        maps_dir = self._root_dir / 'data' / 'maps'
        if not maps_dir.is_dir():
            QMessageBox.warning(self, 'Open Map', f'Maps directory not found:\n{maps_dir}')
            return
        folders = sorted(
            name for name in os.listdir(maps_dir)
            if (maps_dir / name / 'map.json').is_file()
        )
        if not folders:
            QMessageBox.information(self, 'Open Map', 'No maps with map.json found.')
            return
        choice, ok = QInputDialog.getItem(
            self, 'Open Map', 'Select a map:', folders, editable=False)
        if not ok:
            return
        self._load_map(maps_dir / choice)

    def open_map_and_select(self, map_name: str, local_id: str = '',
                            trainer_const: str = '',
                            text_label: str = ''):
        """Public API: load a map and optionally select an NPC or find a trainer.

        Called by the unified window for cross-editor navigation.
        *map_name*: folder name like "PalletTown"
        *local_id*: object local_id to select (e.g. "LOCALID_PALLET_FAT_MAN")
        *trainer_const*: trainer constant to find (e.g. "TRAINER_HIKER_FATBOY")
        *text_label*: text constant to find (e.g. "PalletTown_Text_CanStoreItemsAndMonsInPC")
                      — searches every NPC's full command tree for a msgbox
                      referencing this text, regardless of script chain depth.
        """
        if not self._root_dir:
            return
        map_dir = self._root_dir / 'data' / 'maps' / map_name
        if not (map_dir / 'map.json').is_file():
            return
        self._load_map(map_dir)

        # Try to select by local_id first
        if local_id:
            for i, obj in enumerate(self._objects):
                if obj.get('local_id') == local_id:
                    self.obj_combo.setCurrentIndex(i)
                    return

        # Try to find NPC whose script tree contains a msgbox with this text
        if text_label:
            for i, obj in enumerate(self._objects):
                for page in obj.get('_pages', []):
                    for cmd in page.get('commands', []):
                        if not cmd or len(cmd) < 2:
                            continue
                        if cmd[0] in ('msgbox', 'message'):
                            # Args may be separate elements or comma-joined
                            args = ' '.join(str(a) for a in cmd[1:])
                            if text_label in args:
                                self.obj_combo.setCurrentIndex(i)
                                return

        # Try to find NPC with trainerbattle referencing this trainer constant
        if trainer_const:
            for i, obj in enumerate(self._objects):
                for page in obj.get('_pages', []):
                    for cmd in page.get('commands', []):
                        if (cmd and len(cmd) >= 2 and
                            'trainerbattle' in cmd[0] and
                            trainer_const in cmd[1]):
                            self.obj_combo.setCurrentIndex(i)
                            return

    def _resolve_external_scripts(self, missing: set[str]):
        """Search shared script files for missing labels.

        Overworld item ball scripts, shared trainer scripts, etc. live in
        data/scripts/*.inc instead of the map's own scripts.inc.  Common
        scripts (nurse, union room, etc.) may also live in
        data/event_scripts.s.  This method searches both locations and adds
        found scripts to self._all_scripts so the Event Editor can display
        them.

        Found labels are tracked in ``_external_script_labels`` so the save
        logic knows not to write them into the map's scripts.inc.
        """
        from eventide.backend.eventide_utils import _parse_script_lines

        found = 0
        label_re = re.compile(r'^([A-Za-z0-9_]+)::')

        # Build list of files to search:
        # 1) All .inc files in data/scripts/
        # 2) data/event_scripts.s (common shared scripts)
        search_files: list[Path] = []

        shared_dir = self._root_dir / 'data' / 'scripts'
        if shared_dir.is_dir():
            for f in shared_dir.iterdir():
                if f.suffix == '.inc' and f.is_file():
                    search_files.append(f)

        event_scripts_s = self._root_dir / 'data' / 'event_scripts.s'
        if event_scripts_s.is_file():
            search_files.append(event_scripts_s)

        for src_file in search_files:
            if not missing:
                break  # found everything

            try:
                text = src_file.read_text(encoding='utf-8')
            except Exception:
                continue

            # Quick check: does any missing label appear in this file?
            if not any(lbl in text for lbl in missing):
                continue

            # Parse label blocks from this file
            current_label = None
            current_lines: list[str] = []

            for line in text.splitlines():
                m = label_re.match(line.strip())
                if m:
                    # Flush previous label
                    if current_label and current_label in missing:
                        cmds = _parse_script_lines(current_lines, self._texts)
                        self._all_scripts[current_label] = cmds
                        self._external_script_labels.add(current_label)
                        self._external_script_files[current_label] = src_file
                        missing.discard(current_label)
                        found += 1
                    current_label = m.group(1)
                    current_lines = []
                    continue
                stripped = line.strip()
                if current_label and stripped and not stripped.startswith('@'):
                    current_lines.append(line)

            # Flush final label
            if current_label and current_label in missing:
                cmds = _parse_script_lines(current_lines, self._texts)
                self._all_scripts[current_label] = cmds
                self._external_script_labels.add(current_label)
                self._external_script_files[current_label] = src_file
                missing.discard(current_label)
                found += 1

        if found:
            self._mw.log_message(
                f'Event Editor: resolved {found} external script(s) '
                f'from shared files')

    def _load_map(self, map_dir: Path):
        # Reset current selection index BEFORE rebuilding _objects.
        # Without this, _collect_current() (called from _on_object_changed)
        # can corrupt new map data by writing stale UI values from the
        # previous map into the new _objects list at the old index.
        self._current_obj_idx = -1
        self._external_script_labels = set()

        self._map_dir = map_dir
        map_json = map_dir / 'map.json'
        name = map_dir.name

        from eventide.backend.eventide_utils import (
            parse_scripts_inc, parse_all_texts, parse_script_pages,
        )

        try:
            with map_json.open(encoding='utf-8') as fh:
                self._map_data = json.load(fh)
            name = self._map_data.get('name', name)
        except Exception as e:
            QMessageBox.critical(self, 'Load Map', f'Failed to read map.json:\n{e}')
            return

        try:
            # Load local text labels first so we know which ones to write back
            from eventide.backend.eventide_utils import parse_text_inc
            local_texts = parse_text_inc(map_dir / 'text.inc')
            self._local_text_labels = set(local_texts.keys())
            self._texts = parse_all_texts(map_dir, self._root_dir)
            scripts, self._hidden_lines = parse_scripts_inc(map_dir, self._texts)
            self._pages, self._page_types = parse_script_pages(map_dir, self._texts)
        except Exception as e:
            self._mw.log_message(f'Event Editor: script parse error: {e}')
            scripts = {}
            self._hidden_lines = {}
            self._pages = {}
            self._page_types = {}

        # ── Populate module-level context for widget dropdowns ───────
        global _SCRIPT_LABELS, _OBJECT_LOCAL_IDS, _MOVEMENT_LABELS, _ALL_SCRIPTS
        _SCRIPT_LABELS = sorted(set(list(scripts.keys()) + list(self._pages.keys())))

        # Collect object local IDs (both LOCALID_ constants and raw strings)
        obj_ids = set()
        for obj in self._map_data.get('object_events', []):
            lid = obj.get('local_id')
            if lid:
                obj_ids.add(str(lid))
        # Also add LOCALID_PLAYER which is commonly used
        obj_ids.add('LOCALID_PLAYER')
        _OBJECT_LOCAL_IDS = sorted(obj_ids)

        # Collect movement labels (labels containing "Movement" in their name)
        _MOVEMENT_LABELS = sorted(
            lbl for lbl in _SCRIPT_LABELS
            if 'Movement' in lbl or 'movement' in lbl
        )

        # Build a unified label→commands dict from BOTH parsers.
        # parse_scripts_inc gives {label: [cmd_tuples]} for each label.
        # parse_script_pages gives {label: [page_dicts]} — extract commands.
        self._all_scripts: dict[str, list] = dict(scripts)
        for label, page_list in self._pages.items():
            if label not in self._all_scripts and page_list:
                if isinstance(page_list[0], dict):
                    self._all_scripts[label] = page_list[0].get('commands', [])

        # ── Resolve external scripts (item balls, shared scripts) ────
        # Collect all script labels referenced by map events, then find
        # any that aren't in the map's own scripts.inc.  Search for them
        # in data/scripts/*.inc and data/event_scripts.s.
        # After resolving, scan loaded scripts for goto/call targets that
        # also need resolution (recursive — follows the full dependency
        # chain so scripts like Common_EventScript_UnionRoomAttendant →
        # call CableClub_EventScript_UnionRoomAttendant all get loaded).
        referenced_labels = set()
        for ev_list_key in ('object_events', 'coord_events', 'bg_events'):
            for ev in self._map_data.get(ev_list_key, []):
                s = ev.get('script', '')
                if s and s != '0x0':
                    referenced_labels.add(s)

        # Also scan map's own scripts for goto/call to external labels
        for cmds in self._all_scripts.values():
            if not isinstance(cmds, list):
                continue
            for cmd in cmds:
                target = self._extract_goto_target(cmd) if cmd else None
                if target:
                    referenced_labels.add(target)

        missing = referenced_labels - set(self._all_scripts.keys())
        if missing and self._root_dir:
            # Recursive resolution: keep resolving until no new labels found
            max_passes = 5  # safety limit
            for _ in range(max_passes):
                before = len(self._all_scripts)
                self._resolve_external_scripts(missing)
                if len(self._all_scripts) == before:
                    break  # nothing new found
                # Scan newly loaded scripts for more goto/call targets
                new_refs = set()
                for label in list(self._external_script_labels):
                    cmds = self._all_scripts.get(label, [])
                    if not isinstance(cmds, list):
                        continue
                    for cmd in cmds:
                        target = self._extract_goto_target(cmd) if cmd else None
                        if target:
                            new_refs.add(target)
                missing = new_refs - set(self._all_scripts.keys())
                if not missing:
                    break

        # Make available to stringizer for movement step lookups
        # Also stash texts under a special key so trainer battle stringizer
        # can look up intro/defeat dialogue.
        #
        # IMPORTANT: share texts by REFERENCE, not copy. The trainer battle
        # widget captures this dict and writes user edits back into it; if
        # we copy here, those edits never flow back to self._texts and get
        # silently dropped at save time. Sharing the reference also lets
        # the Trainers tab Dialogue view peek at live in-RAM edits before
        # the user saves the project.
        _ALL_SCRIPTS = self._all_scripts
        _ALL_SCRIPTS['__texts__'] = self._texts
        _ALL_SCRIPTS['__texts_map__'] = (
            self._map_dir.name if self._map_dir else None)

        # Build position override lookup from OnTransition scripts
        self._build_position_overrides()
        self._current_pos_override_source = None  # tracks if active page has override

        self._objects = []
        self.obj_combo.blockSignals(True)
        self.obj_combo.clear()

        # ── Object events (NPCs + item balls) ────────────────────────
        for obj in self._map_data.get('object_events', []):
            script = obj.get('script', '')
            obj['_event_type'] = 'object'
            if script and script != '0x0' and script in self._all_scripts:
                obj['_pages'] = self._build_script_pages(script, self._all_scripts)
            else:
                obj['_pages'] = [{'commands': [], '_label': script or '(empty)',
                                  '_short_label': script or '(empty)'}]
            self._objects.append(obj)

            # Item balls get a special [Item] tag with the item name
            gfx = obj.get('graphics_id', '')
            if gfx == 'OBJ_EVENT_GFX_ITEM_BALL':
                # Try to extract item name from the finditem command
                item_display = ''
                for page in obj.get('_pages', []):
                    for cmd in page.get('commands', []):
                        if cmd and cmd[0] == 'finditem' and len(cmd) > 1:
                            item_const = cmd[1].strip().split(',')[0].strip()
                            item_display = _resolve_name(item_const)
                            break
                    if item_display:
                        break
                if not item_display:
                    # Fallback: derive from script label
                    short = script
                    if '_EventScript_' in script:
                        short = script.split('_EventScript_', 1)[1]
                    item_display = short
                self.obj_combo.addItem(f'[Item] {item_display}')
            else:
                lid = obj.get('local_id') or script or 'object'
                self.obj_combo.addItem(f'[NPC] {lid}')

        # ── Coord events (triggers — step-on tiles) ─────────────────
        for i, ev in enumerate(self._map_data.get('coord_events', [])):
            script = ev.get('script', '')
            ev['_event_type'] = 'coord'
            if script and script in self._all_scripts:
                ev['_pages'] = self._build_script_pages(script, self._all_scripts)
            else:
                ev['_pages'] = [{'commands': [], '_label': script or '(empty)',
                                 '_short_label': script or '(empty)'}]
            self._objects.append(ev)
            label = script or f'trigger_{i}'
            # Shorten: remove map prefix for display
            short = label
            if '_EventScript_' in label:
                short = label.split('_EventScript_', 1)[1]
            self.obj_combo.addItem(f'[Trigger] {short}')

        # ── BG events (signs, hidden items) ──────────────────────────
        for i, ev in enumerate(self._map_data.get('bg_events', [])):
            script = ev.get('script', '')
            ev['_event_type'] = 'bg'
            if script and script in self._all_scripts:
                ev['_pages'] = self._build_script_pages(script, self._all_scripts)
            else:
                ev['_pages'] = [{'commands': [], '_label': script or '(empty)',
                                 '_short_label': script or '(empty)'}]
            self._objects.append(ev)
            raw_type = ev.get('type', 'sign')
            if raw_type == 'hidden_item':
                # Show the item's display name instead of "bg_0"
                item_const = ev.get('item', '')
                item_name = _resolve_name(item_const) if item_const else f'bg_{i}'
                self.obj_combo.addItem(f'[Hidden Item] {item_name}')
            else:
                # Regular sign — show script short name
                label = script or f'bg_{i}'
                short = label
                if '_EventScript_' in label:
                    short = label.split('_EventScript_', 1)[1]
                bg_label = raw_type.replace('_', ' ').title()
                self.obj_combo.addItem(f'[{bg_label}] {short}')

        # ── Map scripts (on-transition, on-frame, etc.) ──────────────
        # These live in scripts.inc as <MapName>_MapScripts:: with
        # map_script commands pointing to sub-labels.
        map_scripts_label = f'{name}_MapScripts'
        map_script_cmds = self._all_scripts.get(map_scripts_label, [])
        if map_script_cmds:
            # Build a summary of script types for the main label
            script_types = []
            for ct in map_script_cmds:
                if ct and ct[0] == 'map_script' and len(ct) > 1:
                    type_arg = str(ct[1]).split(',')[0].strip()
                    script_types.append(_MAP_SCRIPT_TYPES.get(type_arg, type_arg))
                elif ct and ct[0] == 'map_script_2':
                    script_types.append('Conditional')
            type_summary = ', '.join(script_types) if script_types else 'Scripts'

            # The MapScripts label itself is a pseudo-event
            ms_entry = {
                'script': map_scripts_label,
                '_event_type': 'map_script',
                '_pages': self._build_script_pages(
                    map_scripts_label, self._all_scripts),
            }
            self._objects.append(ms_entry)
            self.obj_combo.addItem(f'[MapScript] Script Table ({type_summary})')

            # Also add each individual map_script target as its own entry
            for cmd_tuple in map_script_cmds:
                if not cmd_tuple:
                    continue
                cmd = cmd_tuple[0]
                if cmd in ('map_script', 'map_script_2') and len(cmd_tuple) > 1:
                    args = str(cmd_tuple[1]).split(',')
                    # map_script: TYPE, LABEL
                    # map_script_2: VAR, VALUE, LABEL
                    if cmd == 'map_script' and len(args) >= 2:
                        script_type = _MAP_SCRIPT_TYPES.get(args[0].strip(), args[0].strip())
                    else:
                        script_type = 'Conditional'
                    target_label = args[-1].strip() if args else ''
                    if target_label and target_label in self._all_scripts:
                        ms_sub = {
                            'script': target_label,
                            '_event_type': 'map_script',
                            '_pages': self._build_script_pages(
                                target_label, self._all_scripts),
                        }
                        self._objects.append(ms_sub)
                        short = target_label
                        if '_' in short:
                            short = target_label.split(f'{name}_', 1)[-1]
                        self.obj_combo.addItem(f'[MapScript] {script_type}: {short}')

        self.obj_combo.blockSignals(False)

        self.map_label.setText(f'Map: {name}')
        n_npc = len(self._map_data.get('object_events', []))
        n_trig = len(self._map_data.get('coord_events', []))
        n_bg = len(self._map_data.get('bg_events', []))
        n_ms = 1 if map_script_cmds else 0
        self._mw.log_message(
            f'Event Editor: loaded {name} — '
            f'{n_npc} NPC(s), {n_trig} trigger(s), {n_bg} sign(s), '
            f'{n_ms} map script(s)')

        # ── Self-verification: warn about problems the user should know ──
        self._verify_loaded_events(name)

        if self._objects:
            self.obj_combo.setCurrentIndex(0)
            self._on_object_changed(0)

        # Phase 7: notify listeners (e.g. Porymap sync) that a map was loaded
        self.map_loaded.emit(name)

    # ─────────────────────────────────────────────────────────────────────
    # Self-verification — log warnings when data looks wrong
    # ─────────────────────────────────────────────────────────────────────

    def _verify_loaded_events(self, map_name: str):
        """Check loaded data for problems and log warnings.

        This runs after every map load so issues are immediately visible
        in the log panel — no silent failures.
        """
        warnings = []

        # Check 1: Any events with scripts that weren't found in scripts.inc
        missing_scripts = []
        for obj in self._objects:
            script = obj.get('script', '')
            etype = obj.get('_event_type', 'object')
            if not script or script == '0x0':
                continue
            pages = obj.get('_pages', [])
            # If all pages are empty, the script label wasn't in scripts.inc
            if pages and all(not p.get('commands') for p in pages):
                missing_scripts.append(f'{script} ({etype})')
        if missing_scripts:
            warnings.append(
                f'Scripts not found in scripts.inc: {", ".join(missing_scripts)}')

        # Check 2: scripts.inc had labels that no event references (orphans)
        referenced = set()
        for obj in self._objects:
            for page in obj.get('_pages', []):
                referenced.add(page.get('_label', ''))
        all_labels = set(self._all_scripts.keys())
        orphans = sorted(all_labels - referenced)
        # Filter out movement labels and .equ directives — those are expected
        orphans = [o for o in orphans if 'Movement' not in o
                   and not o.startswith('.')]
        if orphans and len(orphans) <= 10:
            warnings.append(
                f'{len(orphans)} unreferenced label(s) in scripts.inc: '
                f'{", ".join(orphans[:5])}{"..." if len(orphans) > 5 else ""}')

        # Check 3: Empty map (no events at all)
        if not self._objects:
            warnings.append('No events found — map has no scripts to edit')

        # Check 4: Verify event type counts match map.json
        type_counts = {}
        for obj in self._objects:
            t = obj.get('_event_type', 'object')
            type_counts[t] = type_counts.get(t, 0) + 1
        expected_obj = len(self._map_data.get('object_events', []))
        actual_obj = type_counts.get('object', 0)
        if actual_obj != expected_obj:
            warnings.append(
                f'Object count mismatch: map.json has {expected_obj}, '
                f'loaded {actual_obj}')

        for w in warnings:
            self._mw.log_message(f'Event Editor WARNING: {w}')

        if not warnings:
            self._mw.log_message(
                f'Event Editor: {map_name} verified — all events and scripts OK')

    # ─────────────────────────────────────────────────────────────────────
    # Script page building — collect reachable sub-labels as tabs
    # ─────────────────────────────────────────────────────────────────────

    _CONDITION_CMDS = frozenset({
        'goto_if_set', 'goto_if_unset',
        'goto_if_eq', 'goto_if_ne', 'goto_if_lt',
        'goto_if_ge', 'goto_if_le', 'goto_if_gt',
    })

    def _shorten_label(self, label: str) -> str:
        """Remove the common map prefix from a label for display."""
        if '_EventScript_' in label:
            return label.split('_EventScript_', 1)[1]
        if '_' in label:
            parts = label.split('_', 1)
            if len(parts) > 1:
                return parts[1]
        return label

    def _condition_text(self, cmd_tuple: tuple) -> str:
        """Return a human-readable condition string for the conditions banner.

        Produces text like:
            Flag: [Temp 2] is ON
            Variable: [Map Scene] == 2
        """
        if not cmd_tuple:
            return ''
        cmd = cmd_tuple[0]
        if cmd == 'goto_if_set' and len(cmd_tuple) > 1:
            name = _resolve_name(str(cmd_tuple[1]))
            return f'Flag: [{name}] is ON'
        if cmd == 'goto_if_unset' and len(cmd_tuple) > 1:
            name = _resolve_name(str(cmd_tuple[1]))
            return f'Flag: [{name}] is OFF'
        if cmd in ('goto_if_eq', 'goto_if_ne', 'goto_if_lt',
                    'goto_if_ge', 'goto_if_le', 'goto_if_gt'):
            op_map = {'goto_if_eq': '==', 'goto_if_ne': '!=',
                      'goto_if_lt': '<', 'goto_if_ge': '>=',
                      'goto_if_le': '<=', 'goto_if_gt': '>'}
            op = op_map.get(cmd, '?')
            var = _resolve_name(str(cmd_tuple[1])) if len(cmd_tuple) > 1 else '?'
            val = _resolve_name(str(cmd_tuple[2])) if len(cmd_tuple) > 2 else '?'
            return f'Variable: [{var}] {op} {val}'
        return ''

    def _update_conditions_box(self, page: dict | None, pages: list[dict] | None = None):
        """Populate the RMXP-style Conditions GroupBox from a page dict."""
        # Block signals while populating to avoid triggering _on_cond_changed
        self._cond_flag_check.blockSignals(True)
        self._cond_flag_picker.blockSignals(True)
        self._cond_flag_state.blockSignals(True)
        self._cond_var_check.blockSignals(True)
        self._cond_var_picker.blockSignals(True)
        self._cond_var_op.blockSignals(True)
        self._cond_var_val.blockSignals(True)

        # Populate flag/var pickers if not already done
        if self._cond_flag_picker.count() == 0:
            flags = sorted(ConstantsManager.FLAGS)
            self._cond_flag_picker.addItems(flags)
            completer = QCompleter(flags, self._cond_flag_picker)
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            completer.setFilterMode(Qt.MatchFlag.MatchContains)
            self._cond_flag_picker.setCompleter(completer)
        if self._cond_var_picker.count() == 0:
            vars_ = sorted(ConstantsManager.VARS)
            self._cond_var_picker.addItems(vars_)
            completer = QCompleter(vars_, self._cond_var_picker)
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            completer.setFilterMode(Qt.MatchFlag.MatchContains)
            self._cond_var_picker.setCompleter(completer)

        # Reset
        self._cond_flag_check.setChecked(False)
        self._cond_flag_picker.setCurrentIndex(-1)
        self._cond_flag_picker.setEditText('')
        self._cond_flag_picker.setEnabled(False)
        self._cond_flag_state.setCurrentIndex(0)
        self._cond_flag_state.setEnabled(False)
        self._cond_var_check.setChecked(False)
        self._cond_var_picker.setCurrentIndex(-1)
        self._cond_var_picker.setEditText('')
        self._cond_var_picker.setEnabled(False)
        self._cond_var_op.setCurrentIndex(0)
        self._cond_var_op.setEnabled(False)
        self._cond_var_val.setValue(0)
        self._cond_var_val.setEnabled(False)

        if page:
            cond_cmd = page.get('_condition_cmd')
            if cond_cmd:
                cmd = cond_cmd[0]
                if cmd in ('goto_if_set', 'goto_if_unset'):
                    self._cond_flag_check.setChecked(True)
                    flag = str(cond_cmd[1]) if len(cond_cmd) > 1 else ''
                    idx = self._cond_flag_picker.findText(flag)
                    if idx >= 0:
                        self._cond_flag_picker.setCurrentIndex(idx)
                    else:
                        self._cond_flag_picker.setEditText(flag)
                    self._cond_flag_picker.setEnabled(True)
                    state_idx = 0 if cmd == 'goto_if_set' else 1
                    self._cond_flag_state.setCurrentIndex(state_idx)
                    self._cond_flag_state.setEnabled(True)
                elif cmd in ('goto_if_eq', 'goto_if_ne', 'goto_if_lt',
                              'goto_if_ge', 'goto_if_le', 'goto_if_gt'):
                    self._cond_var_check.setChecked(True)
                    var = str(cond_cmd[1]) if len(cond_cmd) > 1 else ''
                    idx = self._cond_var_picker.findText(var)
                    if idx >= 0:
                        self._cond_var_picker.setCurrentIndex(idx)
                    else:
                        self._cond_var_picker.setEditText(var)
                    self._cond_var_picker.setEnabled(True)
                    op_map = {'goto_if_eq': 0, 'goto_if_ne': 1,
                              'goto_if_lt': 2, 'goto_if_ge': 3,
                              'goto_if_le': 4, 'goto_if_gt': 5}
                    self._cond_var_op.setCurrentIndex(op_map.get(cmd, 0))
                    self._cond_var_op.setEnabled(True)
                    raw_val = str(cond_cmd[2]) if len(cond_cmd) > 2 else '0'
                    try:
                        self._cond_var_val.setValue(int(raw_val))
                    except (ValueError, TypeError):
                        self._cond_var_val.setValue(0)
                    self._cond_var_val.setEnabled(True)

        # Unblock signals
        self._cond_flag_check.blockSignals(False)
        self._cond_flag_picker.blockSignals(False)
        self._cond_flag_state.blockSignals(False)
        self._cond_var_check.blockSignals(False)
        self._cond_var_picker.blockSignals(False)
        self._cond_var_op.blockSignals(False)
        self._cond_var_val.blockSignals(False)

    def _on_cond_flag_toggled(self, checked: bool):
        """User toggled the Flag checkbox in the Conditions box."""
        self._cond_flag_picker.setEnabled(checked)
        self._cond_flag_state.setEnabled(checked)
        if checked:
            # If Variable is also checked, uncheck it (flag/var are exclusive
            # for a single condition page — matches RMXP behavior)
            self._cond_var_check.setChecked(False)
        self._apply_condition_edit()

    def _on_cond_var_toggled(self, checked: bool):
        """User toggled the Variable checkbox in the Conditions box."""
        self._cond_var_picker.setEnabled(checked)
        self._cond_var_op.setEnabled(checked)
        self._cond_var_val.setEnabled(checked)
        if checked:
            self._cond_flag_check.setChecked(False)
        self._apply_condition_edit()

    def _on_cond_changed(self, *_args):
        """User changed a flag/var picker, operator, or value."""
        self._apply_condition_edit()

    def _apply_condition_edit(self):
        """Write the edited condition back into the current page's data."""
        if self._loading:
            return
        if self._current_obj_idx < 0:
            return
        obj = self._objects[self._current_obj_idx]
        pages = obj.get('_pages', [])
        idx = self._current_page_idx
        if idx < 0 or idx >= len(pages):
            return
        page = pages[idx]

        # Build the new condition command tuple from the UI
        if self._cond_flag_check.isChecked():
            flag = self._cond_flag_picker.currentText().strip()
            if flag:
                # Determine the target label for the goto
                target = page.get('_label', '')
                state_idx = self._cond_flag_state.currentIndex()
                cmd = 'goto_if_set' if state_idx == 0 else 'goto_if_unset'
                page['_condition_cmd'] = (cmd, flag, target)
                page['_condition'] = self._condition_text(page['_condition_cmd'])
                self._mark_dirty()
                return
        elif self._cond_var_check.isChecked():
            var = self._cond_var_picker.currentText().strip()
            if var:
                target = page.get('_label', '')
                op_cmds = ['goto_if_eq', 'goto_if_ne', 'goto_if_lt',
                           'goto_if_ge', 'goto_if_le', 'goto_if_gt']
                op_idx = self._cond_var_op.currentIndex()
                cmd = op_cmds[op_idx] if 0 <= op_idx < len(op_cmds) else 'goto_if_eq'
                val = str(self._cond_var_val.value())
                page['_condition_cmd'] = (cmd, var, val, target)
                page['_condition'] = self._condition_text(page['_condition_cmd'])
                self._mark_dirty()
                return

        # If nothing checked, clear the condition
        if '_condition_cmd' in page:
            del page['_condition_cmd']
        page['_condition'] = None
        self._mark_dirty()

    def _merge_sublabels(self, entry_label: str, scripts: dict,
                         exclude: set[str] | None = None) -> list[tuple]:
        """Follow goto/call targets from entry_label and merge into one list.

        Sub-labels are joined with _label_marker pseudo-commands between
        sections.  Labels in ``exclude`` are skipped (they belong to other
        condition pages).
        """
        if exclude is None:
            exclude = set()

        visited = set()
        ordered: list[str] = []
        queue = [entry_label]

        while queue:
            label = queue.pop(0)
            if label in visited or label in exclude:
                continue
            visited.add(label)

            cmds = scripts.get(label, [])
            if not cmds and label not in scripts:
                continue

            ordered.append(label)

            for ct in cmds:
                target = self._extract_goto_target(ct) if ct else None
                if target and target not in visited and target not in exclude:
                    queue.append(target)

        merged: list[tuple] = []
        for i, label in enumerate(ordered):
            if i > 0:
                merged.append(('_label_marker', label))
            merged.extend(scripts.get(label, []))

        return merged

    def _build_script_pages(self, entry_label: str, scripts: dict) -> list[dict]:
        """Build RMXP-style pages from a script's entry point.

        Leading conditional gotos (goto_if_set, goto_if_eq, etc.) at the
        TOP of the entry script become separate condition pages — like
        RMXP's "Switch [001] is ON" page system.  Each condition page tab
        shows the condition that activates it.

        The default page (commands after the leading conditionals) is
        always Page 1.  Sub-labels reached via goto/call WITHIN a page's
        body are merged inline with _label_marker separators — like
        RMXP's Label / Jump to Label.
        """
        entry_cmds = scripts.get(entry_label, [])
        if not entry_cmds:
            return [{'commands': [], '_label': entry_label,
                     '_short_label': self._shorten_label(entry_label),
                     '_sub_labels': [entry_label]}]

        # ── Step 1: Find leading conditionals ────────────────────────
        # Walk the entry commands from the top.  Commands like lock,
        # faceplayer, etc. are "preamble" — they execute before the
        # conditionals and belong to every page.  Conditional gotos that
        # redirect to another label become condition pages.
        _PREAMBLE_CMDS = frozenset({
            'lock', 'lockall', 'faceplayer', 'textcolor',
        })

        preamble: list[tuple] = []
        condition_entries: list[tuple] = []   # (cmd_tuple, target_label)
        body_start = 0

        for i, ct in enumerate(entry_cmds):
            if not ct:
                continue
            cmd = ct[0]
            if cmd in _PREAMBLE_CMDS:
                preamble.append(ct)
                body_start = i + 1
            elif cmd in self._CONDITION_CMDS:
                target = self._extract_goto_target(ct)
                if target:
                    condition_entries.append((ct, target))
                body_start = i + 1
            else:
                break  # First non-preamble, non-conditional = body starts

        # If no conditions found, return a single page with everything
        # merged inline (simple script with no branching at entry)
        if not condition_entries:
            merged = self._merge_sublabels(entry_label, scripts)
            return [{
                'commands': merged,
                '_label': entry_label,
                '_short_label': self._shorten_label(entry_label),
                '_sub_labels': [entry_label],
            }]

        # ── Step 2: Collect condition page target labels ─────────────
        condition_targets = {target for _, target in condition_entries}

        # ── Step 3: Build default page (body after conditionals) ─────
        default_body = list(preamble) + entry_cmds[body_start:]
        # Follow sub-labels from default body, excluding condition targets
        default_sublabels = [entry_label]
        default_merged = list(default_body)

        # BFS from default body commands
        visited_default = {entry_label} | condition_targets
        queue = []
        for ct in default_body:
            target = self._extract_goto_target(ct) if ct else None
            if target and target not in visited_default:
                queue.append(target)

        while queue:
            label = queue.pop(0)
            if label in visited_default:
                continue
            visited_default.add(label)
            sub_cmds = scripts.get(label, [])
            if not sub_cmds and label not in scripts:
                continue
            default_sublabels.append(label)
            default_merged.append(('_label_marker', label))
            default_merged.extend(sub_cmds)
            for ct in sub_cmds:
                target = self._extract_goto_target(ct) if ct else None
                if target and target not in visited_default:
                    queue.append(target)

        pages = [{
            'commands': default_merged,
            '_label': entry_label,
            '_short_label': self._shorten_label(entry_label),
            '_sub_labels': default_sublabels,
            '_condition': None,
        }]

        # ── Step 4: Build each condition page ────────────────────────
        for cond_cmd, target_label in condition_entries:
            cond_text = self._condition_text(cond_cmd)
            # Merge sub-labels from the target, but exclude labels
            # that belong to other pages
            other_targets = condition_targets - {target_label}
            page_cmds = list(preamble)  # preamble runs on every page
            page_merged = self._merge_sublabels(
                target_label, scripts, exclude=other_targets)
            page_cmds.extend(page_merged)

            # Collect sub-labels for save
            page_sublabels = [target_label]
            for ct in page_cmds:
                if ct and ct[0] == '_label_marker' and len(ct) > 1:
                    page_sublabels.append(ct[1])

            short = self._shorten_label(target_label)

            pages.append({
                'commands': page_cmds,
                '_label': target_label,
                '_short_label': short,
                '_sub_labels': page_sublabels,
                '_condition': cond_text,
                '_condition_cmd': cond_cmd,
            })

        return pages

    # ─────────────────────────────────────────────────────────────────────
    # Object selection
    # ─────────────────────────────────────────────────────────────────────

    def _on_object_changed(self, idx):
        self._collect_current()
        self._current_obj_idx = idx
        self._loading = True  # Suppress dirty while populating fields
        if idx < 0 or idx >= len(self._objects):
            self._loading = False
            return
        obj = self._objects[idx]
        etype = obj.get('_event_type', 'object')

        # Properties panel — adapt to event type
        if etype == 'object':
            self.obj_id_edit.setText(str(obj.get('local_id', '')))
            self.obj_id_edit.setEnabled(True)
            self.x_spin.setValue(int(obj.get('x', 0)))
            self.y_spin.setValue(int(obj.get('y', 0)))
            self.x_spin.setEnabled(True)
            self.y_spin.setEnabled(True)
            gfx = obj.get('graphics_id', '')
            gidx = self.gfx_combo.findText(gfx)
            if gidx >= 0:
                self.gfx_combo.setCurrentIndex(gidx)
            else:
                self.gfx_combo.setEditText(str(gfx))
            self.gfx_combo.setEnabled(True)
            self._update_sprite(gfx)
        elif etype == 'bg' and obj.get('type') == 'hidden_item':
            # Hidden item — show dedicated panel instead of command list
            self._right_stack.setCurrentIndex(1)
            self._hidden_item_panel.load(obj)
            self.obj_id_edit.setText('Hidden Item')
            self.obj_id_edit.setEnabled(False)
            self.x_spin.setValue(int(obj.get('x', 0)))
            self.y_spin.setValue(int(obj.get('y', 0)))
            self.x_spin.setEnabled(False)
            self.y_spin.setEnabled(False)
            self.gfx_combo.setEditText('')
            self.gfx_combo.setEnabled(False)
            self.sprite_preview.set_sprite(None)
            self.script_edit.setText('')
            self.script_edit.setEnabled(False)
            self.page_tabs.hide()
            self._conditions_box.hide()
            self._loading = False
            return
        elif etype in ('coord', 'bg'):
            self.obj_id_edit.setText(obj.get('var', '') if etype == 'coord'
                                     else obj.get('type', 'sign'))
            self.obj_id_edit.setEnabled(False)
            self.x_spin.setValue(int(obj.get('x', 0)))
            self.y_spin.setValue(int(obj.get('y', 0)))
            self.x_spin.setEnabled(False)
            self.y_spin.setEnabled(False)
            self.gfx_combo.setEditText('')
            self.gfx_combo.setEnabled(False)
            self.sprite_preview.set_sprite(None)
        else:  # map_script
            self.obj_id_edit.setText('(map script)')
            self.obj_id_edit.setEnabled(False)
            self.x_spin.setValue(0)
            self.y_spin.setValue(0)
            self.x_spin.setEnabled(False)
            self.y_spin.setEnabled(False)
            self.gfx_combo.setEditText('')
            self.gfx_combo.setEnabled(False)
            self.sprite_preview.set_sprite(None)

        # Ensure command list panel is shown (hidden items switch away)
        self._right_stack.setCurrentIndex(0)
        self.page_tabs.show()
        self._conditions_box.show()
        self.script_edit.setEnabled(True)
        self.script_edit.setText(obj.get('script', ''))

        pages = obj.get('_pages', [{'commands': []}])
        global _CURRENT_PAGES
        _CURRENT_PAGES = pages
        self.page_tabs.blockSignals(True)
        self.page_tabs.clear()
        for i, page in enumerate(pages):
            self.page_tabs.addTab(QWidget(), str(i + 1))
        self.page_tabs.blockSignals(False)
        self._current_page_idx = 0
        if pages:
            self.page_tabs.setCurrentIndex(0)
            self._display_page(pages[0])
            self._update_conditions_box(pages[0], pages)
        else:
            self._update_conditions_box(None)

        # Apply position override for the initial page
        self._apply_position_override(obj, pages[0] if pages else None)

        # Scan for cross-references (other scripts that modify this object)
        self._update_xref(obj)
        self._loading = False

    def _on_gfx_changed(self, text):
        self._update_sprite(text)

    def _update_sprite(self, gfx_const):
        path = ConstantsManager.OBJECT_GFX_PATHS.get(gfx_const)
        self.sprite_preview.set_sprite(path)
        self._current_sprite_path = str(path) if path else ""

    def _open_sprite_folder(self):
        from ui.open_folder_util import open_in_folder
        path = getattr(self, "_current_sprite_path", "")
        if path:
            open_in_folder(path)

    def _update_xref(self, obj: dict):
        """Scan all scripts for commands that modify this object's position.

        Shows the actual coordinates from setobjectxyperm/setobjectxy
        commands with clickable labels to navigate there.
        """
        self._xref_label.hide()
        self._xref_label.setText('')

        etype = obj.get('_event_type', 'object')
        if etype != 'object':
            return

        local_id = str(obj.get('local_id', ''))
        if not local_id:
            return

        # Scan all loaded scripts for setobjectxyperm/setobjectxy
        # targeting this NPC, and extract the coordinates
        entries: list[tuple[str, str, str, str]] = []  # (label, x, y, cmd_name)
        seen_labels: set[str] = set()

        for label, cmds in self._all_scripts.items():
            for ct in cmds:
                if not ct:
                    continue
                cmd = ct[0]
                if cmd not in ('setobjectxyperm', 'setobjectxy'):
                    continue
                # Parse args: could be positional tuple or comma-separated
                if len(ct) >= 4:
                    # Positional: (cmd, local_id, x, y)
                    if str(ct[1]).strip() == local_id:
                        entries.append((label, str(ct[2]), str(ct[3]), cmd))
                        seen_labels.add(label)
                elif len(ct) > 1:
                    # Flat args: "LOCALID, x, y"
                    args = str(ct[1])
                    if local_id in args:
                        parts = [p.strip() for p in args.split(',')]
                        if len(parts) >= 3:
                            entries.append((label, parts[1], parts[2], cmd))
                            seen_labels.add(label)

            # Also check for setobjectmovementtype (no coords but relevant)
            if label not in seen_labels:
                for ct in cmds:
                    if not ct or ct[0] != 'setobjectmovementtype':
                        continue
                    args = str(ct[1]) if len(ct) > 1 else ''
                    if local_id in args:
                        # Extract movement type
                        parts = [p.strip() for p in args.split(',')]
                        mvtype = parts[1] if len(parts) >= 2 else '?'
                        mvtype = mvtype.replace('MOVEMENT_TYPE_', '').replace(
                            '_', ' ').title()
                        seen_labels.add(label)
                        break

        if not entries and not seen_labels:
            return

        # Build display with coordinates shown inline
        lines = []
        for label, x, y in [(e[0], e[1], e[2]) for e in entries]:
            short = self._shorten_label(label)
            lines.append(
                f'<a href="{label}" style="color: #e8a838;">{short}</a>'
                f' <span style="color: #aaa;">→ ({x}, {y})</span>')

        if lines:
            html = ('<span style="color: #888;">Position also set by:</span>'
                    '<br>' + '<br>'.join(lines))
            self._xref_label.setText(html)
            self._xref_label.show()

    def _on_xref_clicked(self, link: str):
        """User clicked a cross-reference link — navigate to that script."""
        target = link  # The href is the full label name

        # Search 1: Direct page label or sub-label match
        for ei, ev in enumerate(self._objects):
            pages = ev.get('_pages', [])
            for pi, page in enumerate(pages):
                if page.get('_label') == target:
                    self.obj_combo.setCurrentIndex(ei)
                    if pi != self._current_page_idx:
                        self.page_tabs.setCurrentIndex(pi)
                    self._mw.log_message(
                        f'Event Editor: jumped to "{target}"')
                    return
                for sub in page.get('_sub_labels', []):
                    if sub == target:
                        self.obj_combo.setCurrentIndex(ei)
                        if pi != self._current_page_idx:
                            self.page_tabs.setCurrentIndex(pi)
                        # Scroll to the label marker in the command list
                        # Need to display the page first if we switched events
                        self._display_page(page)
                        for ci in range(self._cmd_list.count()):
                            item = self._cmd_list.item(ci)
                            ct = item.data(Qt.ItemDataRole.UserRole) if item else None
                            if (ct and ct[0] == '_label_marker'
                                    and len(ct) > 1 and ct[1] == target):
                                self._cmd_list.setCurrentRow(ci)
                                self._cmd_list.scrollToItem(
                                    item,
                                    QAbstractItemView.ScrollHint.PositionAtCenter)
                                break
                        self._mw.log_message(
                            f'Event Editor: jumped to "{target}"')
                        return

        # Search 2: The target label might be inside a page's command list
        # as an inline label, even if not tracked in _sub_labels.
        # Scan command lists for _label_marker matching the target.
        for ei, ev in enumerate(self._objects):
            pages = ev.get('_pages', [])
            for pi, page in enumerate(pages):
                for ct in page.get('commands', []):
                    if (ct and ct[0] == '_label_marker'
                            and len(ct) > 1 and ct[1] == target):
                        self.obj_combo.setCurrentIndex(ei)
                        if pi != self._current_page_idx:
                            self.page_tabs.setCurrentIndex(pi)
                        self._display_page(page)
                        for ci in range(self._cmd_list.count()):
                            item = self._cmd_list.item(ci)
                            cd = item.data(Qt.ItemDataRole.UserRole) if item else None
                            if (cd and cd[0] == '_label_marker'
                                    and len(cd) > 1 and cd[1] == target):
                                self._cmd_list.setCurrentRow(ci)
                                self._cmd_list.scrollToItem(
                                    item,
                                    QAbstractItemView.ScrollHint.PositionAtCenter)
                                break
                        self._mw.log_message(
                            f'Event Editor: jumped to "{target}"')
                        return

        # Search 3: Find which parent script calls/gotos this target,
        # then navigate to the parent event
        for ei, ev in enumerate(self._objects):
            script = ev.get('script', '')
            if not script:
                continue
            # Check if this event's script tree references the target
            visited = set()
            queue = [script]
            while queue:
                lbl = queue.pop(0)
                if lbl in visited:
                    continue
                visited.add(lbl)
                if lbl == target:
                    # Found it — navigate to this event
                    self.obj_combo.setCurrentIndex(ei)
                    self._mw.log_message(
                        f'Event Editor: jumped to event containing "{target}"')
                    return
                for ct in self._all_scripts.get(lbl, []):
                    t = self._extract_goto_target(ct) if ct else None
                    if t and t not in visited:
                        queue.append(t)

        self._mw.log_message(
            f'Event Editor: could not find script "{target}"')

    def _build_position_overrides(self):
        """Scan OnTransition / map scripts for setobjectxyperm commands.

        Builds a lookup so condition pages can show the effective NPC
        position instead of the base map.json position.

        The lookup maps:
            (local_id, var_or_flag, value) → (x, y, source_label, cmd_index)

        This works globally for any map — it scans ALL loaded scripts for
        conditional branches that lead to setobjectxyperm.
        """
        self._pos_overrides: dict[tuple, tuple] = {}

        # Find all scripts that contain setobjectxyperm/setobjectxy
        pos_scripts: dict[str, list[tuple[str, str, str]]] = {}  # label → [(local_id, x, y)]
        for label, cmds in self._all_scripts.items():
            for ci, ct in enumerate(cmds):
                if not ct or ct[0] not in ('setobjectxyperm', 'setobjectxy'):
                    continue
                if len(ct) >= 4:
                    pos_scripts.setdefault(label, []).append(
                        (str(ct[1]).strip(), str(ct[2]), str(ct[3])))
                elif len(ct) > 1:
                    parts = [p.strip() for p in str(ct[1]).split(',')]
                    if len(parts) >= 3:
                        pos_scripts.setdefault(label, []).append(
                            (parts[0], parts[1], parts[2]))

        if not pos_scripts:
            return

        # Now find which conditional calls/gotos lead to those scripts.
        # Walk all scripts looking for call_if_*/goto_if_* whose targets
        # (directly or one hop away) contain setobjectxyperm.
        for label, cmds in self._all_scripts.items():
            for ct in cmds:
                if not ct:
                    continue
                cmd = ct[0]

                # Extract condition info and target
                cond_var = None
                cond_val = None
                cond_flag = None
                target = None

                if cmd in ('call_if_eq', 'goto_if_eq') and len(ct) >= 4:
                    cond_var = ct[1]
                    cond_val = str(ct[2])
                    target = str(ct[3]).strip()
                elif cmd in ('call_if_ne', 'goto_if_ne') and len(ct) >= 4:
                    cond_var = ct[1]
                    cond_val = f'!{ct[2]}'
                    target = str(ct[3]).strip()
                elif cmd in ('call_if_set', 'goto_if_set') and len(ct) >= 3:
                    cond_flag = ct[1]
                    cond_val = 'SET'
                    target = str(ct[2]).strip()
                elif cmd in ('call_if_unset', 'goto_if_unset') and len(ct) >= 3:
                    cond_flag = ct[1]
                    cond_val = 'UNSET'
                    target = str(ct[2]).strip()

                if not target:
                    continue

                # Check if target directly has position commands
                positions = pos_scripts.get(target)
                if not positions:
                    # One hop: check targets reachable from the target script
                    for sub_ct in self._all_scripts.get(target, []):
                        sub_target = self._extract_goto_target(sub_ct) if sub_ct else None
                        if sub_target and sub_target in pos_scripts:
                            positions = pos_scripts[sub_target]
                            break

                if not positions:
                    continue

                # Register each position override
                for local_id, x, y in positions:
                    if cond_var:
                        key = (local_id, cond_var, cond_val)
                    elif cond_flag:
                        key = (local_id, cond_flag, cond_val)
                    else:
                        continue
                    self._pos_overrides[key] = (x, y, target)

    def _get_position_for_page(self, obj: dict, page: dict) -> tuple[int, int] | None:
        """Return (x, y) override for a condition page, or None.

        Matches the page's condition var/flag against OnTransition position
        overrides. If the exact (var, value) match isn't found, tries
        matching just the variable name (shows the last known position for
        any state of that variable).
        """
        if not hasattr(self, '_pos_overrides') or not self._pos_overrides:
            return None

        local_id = str(obj.get('local_id', ''))
        if not local_id:
            return None

        cond_cmd = page.get('_condition_cmd')
        if not cond_cmd:
            return None

        cmd = cond_cmd[0]
        # Extract the var/flag this page checks
        if cmd in ('goto_if_set', 'goto_if_unset'):
            flag = cond_cmd[1] if len(cond_cmd) > 1 else ''
            # For goto_if_set page, the position was set when flag was UNSET
            # (OnTransition set up the position, then something set the flag)
            # Check both orientations
            for check_val in ('SET', 'UNSET'):
                key = (local_id, flag, check_val)
                if key in self._pos_overrides:
                    x, y, _ = self._pos_overrides[key]
                    try:
                        return (int(x), int(y))
                    except ValueError:
                        pass
        elif cmd in ('goto_if_eq', 'goto_if_ne', 'goto_if_lt',
                     'goto_if_ge', 'goto_if_le', 'goto_if_gt'):
            var = cond_cmd[1] if len(cond_cmd) > 1 else ''
            val = str(cond_cmd[2]) if len(cond_cmd) > 2 else ''

            # Exact match first
            key = (local_id, var, val)
            if key in self._pos_overrides:
                x, y, _ = self._pos_overrides[key]
                try:
                    return (int(x), int(y))
                except ValueError:
                    pass

            # Fuzzy: any position override for this NPC + this variable
            # (different value — the position was set for a previous state)
            for okey, (x, y, _src) in self._pos_overrides.items():
                if okey[0] == local_id and okey[1] == var:
                    try:
                        return (int(x), int(y))
                    except ValueError:
                        pass

        return None

    def _save_position_to_script(self, obj: dict, new_x: int, new_y: int):
        """Write edited X/Y back to the setobjectxyperm command in a script.

        When a condition page shows an overridden position from OnTransition,
        editing X/Y should update the script command, not the base map.json.
        """
        source = self._current_pos_override_source
        if not source or source not in self._all_scripts:
            return

        local_id = str(obj.get('local_id', ''))
        cmds = self._all_scripts[source]
        for i, ct in enumerate(cmds):
            if not ct or ct[0] not in ('setobjectxyperm', 'setobjectxy'):
                continue
            # Match the local_id
            if len(ct) >= 4 and str(ct[1]).strip() == local_id:
                cmds[i] = (ct[0], ct[1], str(new_x), str(new_y))
                return
            elif len(ct) > 1:
                parts = [p.strip() for p in str(ct[1]).split(',')]
                if len(parts) >= 3 and parts[0] == local_id:
                    cmds[i] = (ct[0], f'{local_id}, {new_x}, {new_y}')
                    return

    def _apply_position_override(self, obj: dict, page: dict | None):
        """Update X/Y spinboxes if this page has a position override.

        When an OnTransition script sets a different position for this NPC
        based on a flag/var condition that matches this page's condition,
        the spinboxes show that position instead of the base map.json one.
        A tooltip indicates the override source.
        """
        self._current_pos_override_source = None

        if not page or obj.get('_event_type') != 'object':
            # Reset to base position styling
            self.x_spin.setToolTip('')
            self.y_spin.setToolTip('')
            self.x_spin.setStyleSheet('')
            self.y_spin.setStyleSheet('')
            return

        # Block signals so spinbox changes don't trigger dirty tracking
        self.x_spin.blockSignals(True)
        self.y_spin.blockSignals(True)

        override = self._get_position_for_page(obj, page)
        if override:
            x, y = override
            self.x_spin.setValue(x)
            self.y_spin.setValue(y)
            # Find the source label for the tooltip
            source = ''
            cond_cmd = page.get('_condition_cmd')
            if cond_cmd and hasattr(self, '_pos_overrides'):
                local_id = str(obj.get('local_id', ''))
                for okey, (ox, oy, src) in self._pos_overrides.items():
                    if okey[0] == local_id and ox == str(x) and oy == str(y):
                        source = src
                        break
            self._current_pos_override_source = source
            tip = f'Position set by script: {source}' if source else 'Position from OnTransition script'
            self.x_spin.setToolTip(tip)
            self.y_spin.setToolTip(tip)
            self.x_spin.setStyleSheet('QSpinBox { background-color: #3a3a20; }')
            self.y_spin.setStyleSheet('QSpinBox { background-color: #3a3a20; }')
        else:
            # Reset to base position from the object dict
            self.x_spin.setValue(int(obj.get('x', 0)))
            self.y_spin.setValue(int(obj.get('y', 0)))
            self.x_spin.setToolTip('')
            self.y_spin.setToolTip('')
            self.x_spin.setStyleSheet('')
            self.y_spin.setStyleSheet('')

        self.x_spin.blockSignals(False)
        self.y_spin.blockSignals(False)

    # ─────────────────────────────────────────────────────────────────────
    # Page display
    # ─────────────────────────────────────────────────────────────────────

    def _on_page_changed(self, idx):
        self._collect_current_page()
        self._current_page_idx = idx
        if self._current_obj_idx < 0:
            return
        obj = self._objects[self._current_obj_idx]
        pages = obj.get('_pages', [])
        if 0 <= idx < len(pages):
            self._display_page(pages[idx])
            self._update_conditions_box(pages[idx], pages)
            self._apply_position_override(obj, pages[idx])

    def _display_page(self, page: dict):
        self._cmd_list.clear()
        self._cmd_tuples: list[tuple] = []

        commands = page.get('commands', [])
        for cmd_tuple in commands:
            # During initial load, append directly (don't use insert-before-empty logic)
            text = _stringize(cmd_tuple)
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, cmd_tuple)
            _apply_cmd_color(item, cmd_tuple)
            self._cmd_list.addItem(item)
            self._cmd_tuples.append(cmd_tuple)

        # Add the empty @> insertion line at the bottom (like RMXP)
        empty_item = QListWidgetItem('@>')
        empty_item.setData(Qt.ItemDataRole.UserRole, None)
        self._cmd_list.addItem(empty_item)

    def _add_list_item(self, cmd_tuple: tuple, at_idx: int = -1):
        """Add a command tuple to the list as a display string."""
        text = _stringize(cmd_tuple)

        item = QListWidgetItem(text)
        item.setData(Qt.ItemDataRole.UserRole, cmd_tuple)
        _apply_cmd_color(item, cmd_tuple)

        if at_idx < 0:
            # Insert before the empty @> line at the bottom
            count = self._cmd_list.count()
            pos = count - 1 if count > 0 else 0
            self._cmd_list.insertItem(pos, item)
            self._cmd_tuples.insert(pos, cmd_tuple)
        else:
            self._cmd_list.insertItem(at_idx, item)
            self._cmd_tuples.insert(at_idx, cmd_tuple)
        return item

    def _refresh_item(self, idx: int):
        """Update the display text for a single list item."""
        if 0 <= idx < len(self._cmd_tuples):
            item = self._cmd_list.item(idx)
            if item:
                cmd_tuple = self._cmd_tuples[idx]
                item.setText(_stringize(cmd_tuple))
                item.setData(Qt.ItemDataRole.UserRole, cmd_tuple)
                # Reset to default color, then apply type-based color
                item.setForeground(self._cmd_list.palette().text().color())
                _apply_cmd_color(item, cmd_tuple)

    def refresh_display_names(self):
        """Re-render all command list items with current display names.

        Called after _set_display_data() updates the module-level name dicts,
        so that items already on screen pick up the new friendly names and
        color coding.
        """
        for i in range(self._cmd_list.count()):
            item = self._cmd_list.item(i)
            if item is None:
                continue
            cmd_tuple = item.data(Qt.ItemDataRole.UserRole)
            if cmd_tuple is None:
                continue  # the empty @> insertion line
            item.setText(_stringize(cmd_tuple))
            item.setForeground(self._cmd_list.palette().text().color())
            _apply_cmd_color(item, cmd_tuple)

    def _collect_current_page(self):
        if self._current_obj_idx < 0 or self._current_obj_idx >= len(self._objects):
            return
        obj = self._objects[self._current_obj_idx]
        pages = obj.get('_pages', [])
        if 0 <= self._current_page_idx < len(pages):
            pages[self._current_page_idx]['commands'] = list(self._cmd_tuples)

    def _collect_current(self):
        self._collect_current_page()
        if self._current_obj_idx < 0 or self._current_obj_idx >= len(self._objects):
            return
        obj = self._objects[self._current_obj_idx]
        etype = obj.get('_event_type', 'object')
        # Only write back editable properties for NPC object events
        if etype == 'object':
            # Only write local_id if non-empty.  An empty string causes
            # the build to fail with "local_id cannot be empty".  Objects
            # that never had a local_id should not get one added.
            lid = self.obj_id_edit.text().strip()
            if lid:
                obj['local_id'] = lid

            # If current page has a position override from a script,
            # save edited X/Y back to the setobjectxyperm command in
            # _all_scripts rather than the base map.json position.
            if self._current_pos_override_source:
                self._save_position_to_script(
                    obj, self.x_spin.value(), self.y_spin.value())
            else:
                obj['x'] = self.x_spin.value()
                obj['y'] = self.y_spin.value()

            obj['script'] = self.script_edit.text()
            obj['graphics_id'] = self.gfx_combo.currentText()
        elif etype == 'bg' and obj.get('type') == 'hidden_item':
            data = self._hidden_item_panel.collect()
            obj['item'] = data['item']
            obj['flag'] = data['flag']
            obj['quantity'] = data['quantity']
            obj['x'] = data['x']
            obj['y'] = data['y']
            obj['elevation'] = data['elevation']
            obj['underfoot'] = data['underfoot']

    # ─────────────────────────────────────────────────────────────────────
    # Command editing (double-click to open dialog)
    # ─────────────────────────────────────────────────────────────────────

    def _selected_cmd_idx(self) -> int:
        """Return the index of the currently selected command, or -1."""
        row = self._cmd_list.currentRow()
        # Don't count the empty @> line at the end as a command
        if row >= len(self._cmd_tuples):
            return -1
        return row

    def _on_cmd_context_menu(self, pos):
        """Right-click context menu on the command list — Edit, Cut, Copy, Paste, Delete."""
        item = self._cmd_list.itemAt(pos)
        if item:
            self._cmd_list.setCurrentItem(item)
        idx = self._selected_cmd_idx()
        has_cmd = idx >= 0

        menu = QMenu(self)
        act_edit = menu.addAction('Edit')
        act_edit.setEnabled(has_cmd)
        menu.addSeparator()
        act_cut = menu.addAction('Cut')
        act_cut.setEnabled(has_cmd)
        act_copy = menu.addAction('Copy')
        act_copy.setEnabled(has_cmd)
        act_paste = menu.addAction('Paste')
        act_paste.setEnabled(EventEditorTab._clipboard is not None)
        act_dup = menu.addAction('Duplicate')
        act_dup.setEnabled(has_cmd)
        menu.addSeparator()
        act_up = menu.addAction('Move Up')
        act_up.setEnabled(has_cmd and idx > 0)
        act_down = menu.addAction('Move Down')
        act_down.setEnabled(has_cmd and idx < len(self._cmd_tuples) - 1)
        menu.addSeparator()
        act_insert = menu.addAction('Insert Command...')
        act_del = menu.addAction('Delete')
        act_del.setEnabled(has_cmd)
        menu.addSeparator()
        act_goto = menu.addAction('Go To →')
        act_goto.setEnabled(has_cmd)

        # Cross-editor navigation actions
        trainer_const = self._extract_trainer_const(idx) if has_cmd else ''
        item_const = self._extract_item_const(idx) if has_cmd else ''
        flag_var_const = self._extract_flag_var_const(idx) if has_cmd else ''
        act_edit_trainer = act_edit_item = act_edit_label = None
        if trainer_const or item_const or flag_var_const:
            menu.addSeparator()
        if trainer_const:
            trainer_name = _resolve_name(trainer_const)
            act_edit_trainer = menu.addAction(
                f'Edit Trainer Party ({trainer_name})')
        if item_const:
            item_name = _resolve_name(item_const)
            act_edit_item = menu.addAction(
                f'Edit Item ({item_name})')
        if flag_var_const:
            label_name = _resolve_name(flag_var_const)
            act_edit_label = menu.addAction(
                f'Edit Label ({label_name})')

        action = menu.exec(self._cmd_list.mapToGlobal(pos))
        if action == act_edit and has_cmd:
            self._on_edit_command(self._cmd_list.currentItem())
        elif action == act_cut:
            self._on_cut()
        elif action == act_copy:
            self._on_copy()
        elif action == act_paste:
            self._on_paste()
        elif action == act_dup:
            self._on_duplicate()
        elif action == act_up:
            self._on_move_up()
        elif action == act_down:
            self._on_move_down()
        elif action == act_insert:
            self._on_add_command()
        elif action == act_del:
            self._on_del_command()
        elif action == act_goto:
            self._on_goto_target()
        elif action and action == act_edit_trainer and trainer_const:
            self.jump_to_trainer.emit(trainer_const)
        elif action and action == act_edit_item and item_const:
            self.jump_to_item.emit(item_const)
        elif action and action == act_edit_label and flag_var_const:
            self.jump_to_label.emit(flag_var_const)

    def _extract_trainer_const(self, idx: int) -> str:
        """Return TRAINER_* constant from the command at idx, or ''."""
        if idx < 0 or idx >= len(self._cmd_tuples):
            return ''
        cmd = self._cmd_tuples[idx]
        if not cmd or len(cmd) < 2:
            return ''
        args = cmd[1] if len(cmd) > 1 else ''
        for part in args.replace(',', ' ').split():
            if part.startswith('TRAINER_') and part != 'TRAINER_NONE':
                return part
        return ''

    def _extract_item_const(self, idx: int) -> str:
        """Return ITEM_* constant from the command at idx, or ''."""
        if idx < 0 or idx >= len(self._cmd_tuples):
            return ''
        cmd = self._cmd_tuples[idx]
        if not cmd or len(cmd) < 2:
            return ''
        args = cmd[1] if len(cmd) > 1 else ''
        for part in args.replace(',', ' ').split():
            if part.startswith('ITEM_') and part != 'ITEM_NONE':
                return part
        return ''

    def _extract_flag_var_const(self, idx: int) -> str:
        """Return FLAG_* or VAR_* constant from the command at idx, or ''."""
        if idx < 0 or idx >= len(self._cmd_tuples):
            return ''
        cmd_tuple = self._cmd_tuples[idx]
        if not cmd_tuple or len(cmd_tuple) < 2:
            return ''
        cmd = cmd_tuple[0]
        # Direct flag commands
        if cmd in ('setflag', 'clearflag', 'checkflag'):
            return str(cmd_tuple[1])
        # Direct var commands
        if cmd in ('setvar', 'addvar', 'subvar', 'compare_var_to_value'):
            return str(cmd_tuple[1])
        # Conditional flag checks
        if cmd in ('goto_if_set', 'goto_if_unset', 'call_if_set', 'call_if_unset'):
            return str(cmd_tuple[1])
        # Conditional var comparisons
        if cmd in ('goto_if_eq', 'goto_if_ne', 'goto_if_lt', 'goto_if_ge',
                   'goto_if_le', 'goto_if_gt', 'call_if_eq', 'call_if_ne',
                   'call_if_lt', 'call_if_ge'):
            arg = str(cmd_tuple[1])
            if arg.startswith(('FLAG_', 'VAR_')):
                return arg
        return ''

    def _on_edit_command(self, item: QListWidgetItem):
        """Double-click handler — open edit dialog for the clicked command."""
        idx = self._cmd_list.row(item)
        if idx >= len(self._cmd_tuples):
            # Double-clicked the empty @> line — insert a new command
            self._on_add_command()
            return
        cmd_tuple = self._cmd_tuples[idx]
        # Label markers are not editable — they're structural dividers
        if cmd_tuple and cmd_tuple[0] == '_label_marker':
            return
        # For applymovement, use the full edit dialog which includes
        # the Edit Steps button for the Move Route editor
        dlg = _CommandEditDialog(cmd_tuple, self)
        result_code = dlg.exec()
        if result_code in (QDialog.DialogCode.Accepted,
                           _CommandEditDialog.GoToResult):
            result = dlg.result_tuple()
            self._cmd_tuples[idx] = result
            # Check if movement steps were modified
            widget = dlg._widget
            if isinstance(widget, _ApplyMovementWidget):
                mod = widget.get_modified_steps()
                if mod:
                    label, new_steps = mod
                    if label:
                        _ALL_SCRIPTS[label] = new_steps
                        if not hasattr(self, '_modified_movements'):
                            self._modified_movements = {}
                        self._modified_movements[label] = new_steps
            self._refresh_item(idx)
            self._mark_dirty()

            # Go To: navigate to the target label after saving
            if result_code == _CommandEditDialog.GoToResult:
                target = dlg._goto_label
                if target:
                    current_map = (self._map_dir.name
                                   if self._map_dir else None)
                    self._navigate_to_script_label(
                        target, current_map)

    def _on_add_command(self):
        cmd = _CommandSelectorDialog.get_command(self)
        if not cmd:
            return

        # Move Camera is a multi-command sequence — open the camera dialog
        if cmd == 'movecamera':
            self._on_add_camera_sequence()
            return

        # For trainer battle variants, auto-scaffold text labels
        cmd_tuple = self._scaffold_command(cmd)

        idx = self._selected_cmd_idx()
        insert_at = idx + 1 if idx >= 0 else len(self._cmd_tuples)
        self._add_list_item(cmd_tuple, insert_at)
        self._cmd_list.setCurrentRow(insert_at)
        self._mark_dirty()
        self._mw.log_message(f'Event Editor: added {cmd}')

    def _on_add_camera_sequence(self):
        """Open the Camera Move Route dialog and insert the full command sequence."""
        map_name = self._map_dir.name if self._map_dir else 'Map'
        result = _CameraMoveRouteDialog.create_sequence(
            map_name, _ALL_SCRIPTS, parent=self)
        if result is None:
            return

        commands, movements = result

        # Register all generated movement labels
        if not hasattr(self, '_modified_movements'):
            self._modified_movements = {}
        for label, steps in movements.items():
            _ALL_SCRIPTS[label] = steps
            self._all_scripts[label] = steps
            self._modified_movements[label] = steps
            if label not in _MOVEMENT_LABELS:
                _MOVEMENT_LABELS.append(label)

        # Insert all commands at current position
        idx = self._selected_cmd_idx()
        insert_at = idx + 1 if idx >= 0 else len(self._cmd_tuples)
        for i, cmd_tuple in enumerate(commands):
            self._add_list_item(cmd_tuple, insert_at + i)

        # Select the last inserted command
        if commands:
            self._cmd_list.setCurrentRow(insert_at + len(commands) - 1)

        self._mark_dirty()
        self._mw.log_message(
            f'Event Editor: added camera sequence ({len(commands)} commands, '
            f'{len(movements)} movement labels)')

    def _scaffold_command(self, cmd: str) -> tuple:
        """Build a command tuple with auto-created supporting data.

        For trainer battles, this creates text labels in text.inc
        with placeholder content so the game won't crash if built
        before editing.  Uses the map name for label prefixes.
        For applymovement, auto-creates a movement label with step_end.
        For most commands, just returns the bare (cmd,) tuple.
        """
        # Auto-scaffold applymovement with a new movement label
        if cmd == 'applymovement':
            map_name = self._map_dir.name if self._map_dir else 'Map'
            # Find next available movement number
            base = f'{map_name}_Movement_'
            n = 1
            while f'{base}{n}' in _ALL_SCRIPTS or f'{base}{n}' in self._all_scripts:
                n += 1
            mov_label = f'{base}{n}'
            # Create movement with a default face_player + step_end
            _ALL_SCRIPTS[mov_label] = [('face_player',), ('step_end',)]
            self._all_scripts[mov_label] = [('face_player',), ('step_end',)]
            if not hasattr(self, '_modified_movements'):
                self._modified_movements = {}
            self._modified_movements[mov_label] = [('face_player',), ('step_end',)]
            _MOVEMENT_LABELS.append(mov_label)
            return ('applymovement', 'OBJ_EVENT_ID_PLAYER', mov_label)

        if cmd not in ('trainerbattle_single', 'trainerbattle_double',
                        'trainerbattle_no_intro', 'trainerbattle_earlyrival'):
            return (cmd,)

        # Build label prefix from map name
        map_name = self._map_dir.name if self._map_dir else 'Map'
        prefix = f'{map_name}_Text'

        intro_label = f'{prefix}_Intro'
        defeat_label = f'{prefix}_Defeat'

        if cmd == 'trainerbattle_no_intro':
            if defeat_label not in self._texts:
                self._register_text(defeat_label, 'You won...$')
            return (cmd, f'TRAINER_NONE, {defeat_label}')

        elif cmd == 'trainerbattle_earlyrival':
            victory_label = f'{prefix}_Victory'
            if defeat_label not in self._texts:
                self._register_text(defeat_label, 'You won...$')
            if victory_label not in self._texts:
                self._register_text(victory_label, 'I won!$')
            return (cmd, f'TRAINER_NONE, 0, {defeat_label}, {victory_label}')

        else:
            # trainerbattle_single / _double
            if intro_label not in self._texts:
                self._register_text(intro_label, "Let's battle!$")
            if defeat_label not in self._texts:
                self._register_text(defeat_label, 'You won...$')
            if cmd == 'trainerbattle_double':
                not_enough_label = f'{prefix}_NotEnough'
                if not_enough_label not in self._texts:
                    self._register_text(not_enough_label,
                                        "You don't have enough\nPokémon for a battle!$")
                return (cmd, f'TRAINER_NONE, {intro_label}, {defeat_label}, {not_enough_label}')
            return (cmd, f'TRAINER_NONE, {intro_label}, {defeat_label}')

    def _on_del_command(self):
        idx = self._selected_cmd_idx()
        if idx < 0 or idx >= len(self._cmd_tuples):
            return
        # Label markers are structural — can't be deleted
        if self._cmd_tuples[idx] and self._cmd_tuples[idx][0] == '_label_marker':
            return
        self._cmd_tuples.pop(idx)
        self._cmd_list.takeItem(idx)
        new_idx = min(idx, len(self._cmd_tuples) - 1)
        if new_idx >= 0:
            self._cmd_list.setCurrentRow(new_idx)
        self._mark_dirty()

    def _on_rows_moved(self, *_args):
        """Rebuild _cmd_tuples from list widget items after drag-and-drop reorder."""
        new_tuples = []
        for i in range(self._cmd_list.count()):
            data = self._cmd_list.item(i).data(Qt.ItemDataRole.UserRole)
            if data is not None:  # Skip the empty @> insertion line
                new_tuples.append(data)
        self._cmd_tuples = new_tuples
        self._mark_dirty()

    def _on_move_up(self):
        idx = self._selected_cmd_idx()
        if idx <= 0 or idx >= len(self._cmd_tuples):
            return
        # Swap in data
        self._cmd_tuples[idx], self._cmd_tuples[idx - 1] = (
            self._cmd_tuples[idx - 1], self._cmd_tuples[idx])
        # Swap in list widget
        self._refresh_item(idx)
        self._refresh_item(idx - 1)
        self._cmd_list.setCurrentRow(idx - 1)
        self._mark_dirty()

    def _on_move_down(self):
        idx = self._selected_cmd_idx()
        if idx < 0 or idx >= len(self._cmd_tuples) - 1:
            return
        self._cmd_tuples[idx], self._cmd_tuples[idx + 1] = (
            self._cmd_tuples[idx + 1], self._cmd_tuples[idx])
        self._refresh_item(idx)
        self._refresh_item(idx + 1)
        self._cmd_list.setCurrentRow(idx + 1)
        self._mark_dirty()

    def _on_duplicate(self):
        idx = self._selected_cmd_idx()
        if idx < 0 or idx >= len(self._cmd_tuples):
            return
        insert_at = idx + 1
        self._add_list_item(self._cmd_tuples[idx], insert_at)
        self._cmd_list.setCurrentRow(insert_at)
        self._mark_dirty()
        self._mw.log_message('Event Editor: duplicated command')

    # ─────────────────────────────────────────────────────────────────────
    # Go To → navigation (follow goto/call/trainerbattle targets)
    # ─────────────────────────────────────────────────────────────────────

    def _extract_goto_target(self, cmd_tuple: tuple) -> str | None:
        """Extract the script label a command jumps/calls to, or None."""
        if not cmd_tuple:
            return None
        cmd = cmd_tuple[0]

        # Direct goto/call — target is arg[1]
        if cmd in ('goto', 'call') and len(cmd_tuple) > 1:
            return str(cmd_tuple[1]).strip()

        # Conditional compare variants — target is the last meaningful arg
        if cmd in ('goto_if_eq', 'goto_if_ne', 'goto_if_lt',
                   'goto_if_ge', 'goto_if_le', 'goto_if_gt',
                   'call_if_eq', 'call_if_ne', 'call_if_lt',
                   'call_if_ge') and len(cmd_tuple) > 3:
            return str(cmd_tuple[3]).strip()

        # Conditional flag variants — target is arg[2]
        if cmd in ('goto_if_set', 'goto_if_unset',
                   'call_if_set', 'call_if_unset') and len(cmd_tuple) > 2:
            return str(cmd_tuple[2]).strip()

        # Trainer battle — may have a continue script label
        # trainerbattle TYPE, TRAINER, INTRO_TEXT, DEFEAT_TEXT[, CONTINUE_SCRIPT]
        # trainerbattle_single has continue_script as 4th comma-separated arg
        if cmd in ('trainerbattle', 'trainerbattle_single',
                   'trainerbattle_earlyrival', 'trainerbattle_no_intro',
                   'trainerbattle_rematch', 'trainerbattle_rematch_double'):
            args = str(cmd_tuple[1]) if len(cmd_tuple) > 1 else ''
            parts = [p.strip() for p in args.split(',')]
            # For trainerbattle_single: parts[4] is the continue script
            # For others: check if last part looks like a script label
            for part in reversed(parts):
                if part and not part.startswith(('"', "'")) and '_EventScript_' in part:
                    return part
            # Also check parts[4] specifically (continue script slot)
            if len(parts) > 4 and parts[4]:
                candidate = parts[4].strip()
                if candidate and not candidate.startswith(('0', '1', '"')):
                    return candidate

        # map_script / map_script_2 — target label is the last arg
        if cmd in ('map_script', 'map_script_2') and len(cmd_tuple) > 1:
            args = str(cmd_tuple[1]).split(',')
            target = args[-1].strip() if args else ''
            if target and not target.startswith(('0', '1', '"')):
                return target

        # msgbox with a text label — not a script target, skip
        return None

    def _on_goto_target(self):
        """Navigate to the script label that the selected command jumps to.

        Also handles setflag/clearflag/setvar → jump to the condition page
        that checks that flag/var, if one exists.
        """
        idx = self._selected_cmd_idx()
        if idx < 0 or idx >= len(self._cmd_tuples):
            self._mw.log_message(
                'Event Editor: select a goto/call command first')
            return

        cmd_tuple = self._cmd_tuples[idx]
        cmd = cmd_tuple[0] if cmd_tuple else ''

        # setflag/clearflag → jump to the condition page that checks this flag
        if cmd in ('setflag', 'clearflag') and len(cmd_tuple) > 1:
            flag = cmd_tuple[1]
            check_cmd = 'goto_if_set' if cmd == 'setflag' else 'goto_if_unset'
            obj = self._objects[self._current_obj_idx]
            pages = obj.get('_pages', [])
            for pi, pg in enumerate(pages):
                cc = pg.get('_condition_cmd')
                if cc and cc[0] == check_cmd and len(cc) > 1 and cc[1] == flag:
                    self.page_tabs.setCurrentIndex(pi)
                    self._mw.log_message(
                        f'Event Editor: jumped to Page {pi + 1} '
                        f'(condition: {pg.get("_condition", "")})')
                    return
            self._mw.log_message(
                f'Event Editor: no condition page found for {flag}')
            return

        # setvar → jump to the condition page that checks this var == value
        if cmd == 'setvar' and len(cmd_tuple) > 2:
            var = cmd_tuple[1]
            val = str(cmd_tuple[2])
            obj = self._objects[self._current_obj_idx]
            pages = obj.get('_pages', [])
            for pi, pg in enumerate(pages):
                cc = pg.get('_condition_cmd')
                if cc and cc[0] == 'goto_if_eq' and len(cc) > 2:
                    if cc[1] == var and str(cc[2]) == val:
                        self.page_tabs.setCurrentIndex(pi)
                        self._mw.log_message(
                            f'Event Editor: jumped to Page {pi + 1} '
                            f'(condition: {pg.get("_condition", "")})')
                        return
            self._mw.log_message(
                f'Event Editor: no condition page found for {var} == {val}')
            return

        target = self._extract_goto_target(cmd_tuple)
        if not target:
            self._mw.log_message(
                f'Event Editor: "{cmd}" has no script target to follow')
            return

        # 1) Check for an inline label marker in the current command list
        #    (sub-labels are now merged into one list with _label_marker)
        for ci, ct in enumerate(self._cmd_tuples):
            if ct and ct[0] == '_label_marker' and len(ct) > 1:
                if ct[1] == target:
                    self._cmd_list.setCurrentRow(ci)
                    self._cmd_list.scrollToItem(
                        self._cmd_list.item(ci),
                        QAbstractItemView.ScrollHint.PositionAtCenter)
                    self._mw.log_message(
                        f'Event Editor: scrolled to label "{target}"')
                    return

        # 2) Check if the target is the entry label of any event
        for ei, ev in enumerate(self._objects):
            pages = ev.get('_pages', [])
            for pi, page in enumerate(pages):
                if page.get('_label') == target:
                    self.obj_combo.setCurrentIndex(ei)
                    if pi != self._current_page_idx:
                        self.page_tabs.setCurrentIndex(pi)
                    self._mw.log_message(
                        f'Event Editor: jumped to event "{target}"')
                    return
                # Also check sub-labels within merged pages
                for sub in page.get('_sub_labels', []):
                    if sub == target:
                        self.obj_combo.setCurrentIndex(ei)
                        if pi != self._current_page_idx:
                            self.page_tabs.setCurrentIndex(pi)
                        # Scroll to the label marker
                        cmds = page.get('commands', [])
                        for ci, ct in enumerate(cmds):
                            if ct and ct[0] == '_label_marker' and len(ct) > 1:
                                if ct[1] == target:
                                    self._cmd_list.setCurrentRow(ci)
                                    self._cmd_list.scrollToItem(
                                        self._cmd_list.item(ci),
                                        QAbstractItemView.ScrollHint.PositionAtCenter)
                                    break
                        self._mw.log_message(
                            f'Event Editor: jumped to label "{target}"')
                        return

        # 3) Try on-demand resolution from external files
        if target not in self._all_scripts and self._root_dir:
            self._resolve_external_scripts({target})

        if target in self._all_scripts:
            # Dynamically add the target as a new tab on the current event
            if self._current_obj_idx >= 0:
                obj = self._objects[self._current_obj_idx]
                new_page = self._build_script_pages(target, self._all_scripts)
                if new_page:
                    pages = obj.get('_pages', [])
                    pages.extend(new_page)
                    obj['_pages'] = pages
                    self.page_tabs.blockSignals(True)
                    for np in new_page:
                        self.page_tabs.addTab(
                            QWidget(),
                            np.get('_short_label', target))
                    self.page_tabs.blockSignals(False)
                    self.page_tabs.setCurrentIndex(len(pages) - len(new_page))
                    self._mw.log_message(
                        f'Event Editor: added and jumped to "{target}"')
                    return

        self._mw.log_message(
            f'Event Editor: target "{target}" not found in any loaded script')

    _clipboard: tuple | None = None  # Class-level clipboard for copy/paste

    def _on_copy(self):
        idx = self._selected_cmd_idx()
        if idx < 0 or idx >= len(self._cmd_tuples):
            return
        EventEditorTab._clipboard = self._cmd_tuples[idx]
        self._mw.log_message('Event Editor: copied command')

    def _on_cut(self):
        idx = self._selected_cmd_idx()
        if idx < 0 or idx >= len(self._cmd_tuples):
            return
        EventEditorTab._clipboard = self._cmd_tuples[idx]
        self._on_del_command()
        self._mw.log_message('Event Editor: cut command')

    def _on_paste(self):
        if EventEditorTab._clipboard is None:
            return
        idx = self._selected_cmd_idx()
        insert_at = idx + 1 if idx >= 0 else len(self._cmd_tuples)
        self._add_list_item(EventEditorTab._clipboard, insert_at)
        self._cmd_list.setCurrentRow(insert_at)
        self._mark_dirty()
        self._mw.log_message('Event Editor: pasted command')

    def _on_add_page(self):
        if self._current_obj_idx < 0:
            return
        obj = self._objects[self._current_obj_idx]
        pages = obj.setdefault('_pages', [])
        script = obj.get('script', 'NewScript')
        new_label = f'{script}_Sub{len(pages) + 1}'
        pages.append({'commands': [], '_label': new_label, '_short_label': f'Sub{len(pages) + 1}'})
        self.page_tabs.addTab(QWidget(), f'Sub{len(pages)}')
        self.page_tabs.setCurrentIndex(len(pages) - 1)
        self._mark_dirty()

    def _on_rename_page(self):
        """Rename the current page's script label."""
        if self._current_obj_idx < 0:
            return
        obj = self._objects[self._current_obj_idx]
        pages = obj.get('_pages', [])
        if not pages or self._current_page_idx >= len(pages):
            return
        page = pages[self._current_page_idx]
        old_label = page.get('_label', '')

        new_label, ok = QInputDialog.getText(
            self, 'Rename Page',
            f'New script label for this page:\n(old: {old_label})',
            text=old_label)
        if not ok or not new_label.strip() or new_label.strip() == old_label:
            return

        new_label = new_label.strip()
        # Update the page data
        page['_label'] = new_label
        # Generate a short label for the tab
        short = new_label
        map_name = self._map_dir.name if self._map_dir else ''
        if map_name and new_label.startswith(f'{map_name}_EventScript_'):
            short = new_label.split('_EventScript_', 1)[1]
        elif map_name and new_label.startswith(f'{map_name}_'):
            short = new_label.split(f'{map_name}_', 1)[1]
        page['_short_label'] = short

        # Update the tab text
        self.page_tabs.setTabText(self._current_page_idx, short)

        # Update any goto/call commands in OTHER pages that reference the old label
        if old_label != new_label:
            updated = 0
            for p in pages:
                for ci, cmd_tuple in enumerate(p.get('commands', [])):
                    if not cmd_tuple:
                        continue
                    cmd = cmd_tuple[0]
                    new_tuple = self._replace_label_in_cmd(cmd_tuple, old_label, new_label)
                    if new_tuple is not cmd_tuple:
                        p['commands'][ci] = new_tuple
                        updated += 1
            if updated:
                self._mw.log_message(
                    f'Event Editor: renamed "{old_label}" → "{new_label}", '
                    f'updated {updated} reference(s)')
                # Refresh current display
                self._display_page(pages[self._current_page_idx])
            else:
                self._mw.log_message(
                    f'Event Editor: renamed page → "{new_label}"')

        self._mark_dirty()

        # Update module-level labels for dropdown use
        global _SCRIPT_LABELS
        if old_label in _SCRIPT_LABELS:
            _SCRIPT_LABELS.remove(old_label)
        if new_label not in _SCRIPT_LABELS:
            _SCRIPT_LABELS.append(new_label)
            _SCRIPT_LABELS.sort()

    def _replace_label_in_cmd(self, cmd_tuple: tuple, old: str, new: str) -> tuple:
        """If cmd_tuple references old label, return a new tuple with it replaced."""
        if not cmd_tuple:
            return cmd_tuple
        cmd = cmd_tuple[0]
        # goto/call — target is [1]
        if cmd in ('goto', 'call') and len(cmd_tuple) > 1:
            if str(cmd_tuple[1]).strip() == old:
                return (cmd, new)
        # conditional compare — target is [3]
        if cmd in ('goto_if_eq', 'goto_if_ne', 'goto_if_lt', 'goto_if_ge',
                   'goto_if_le', 'goto_if_gt',
                   'call_if_eq', 'call_if_ne', 'call_if_lt', 'call_if_ge'):
            if len(cmd_tuple) > 3 and str(cmd_tuple[3]).strip() == old:
                return (*cmd_tuple[:3], new)
        # conditional flag — target is [2]
        if cmd in ('goto_if_set', 'goto_if_unset', 'call_if_set', 'call_if_unset'):
            if len(cmd_tuple) > 2 and str(cmd_tuple[2]).strip() == old:
                return (cmd, cmd_tuple[1], new)
        return cmd_tuple

    def _on_del_page(self):
        if self._current_obj_idx < 0:
            return
        obj = self._objects[self._current_obj_idx]
        pages = obj.get('_pages', [])
        if len(pages) <= 1:
            QMessageBox.information(self, 'Delete Page', 'Cannot delete the only page.')
            return
        idx = self._current_page_idx
        pages.pop(idx)
        self._on_object_changed(self._current_obj_idx)
        self._mark_dirty()

    # ─────────────────────────────────────────────────────────────────────
    # Unused flag finder
    # ─────────────────────────────────────────────────────────────────────

    def _on_find_unused_flag(self):
        """Scan the project for used flags and find the next available one."""
        if not self._root_dir:
            QMessageBox.information(self, 'Find Flag', 'No project loaded.')
            return

        # Collect all FLAG_ constants defined in the project
        all_flags = set(ConstantsManager.FLAGS)

        # Scan scripts for actually used flags (setflag/clearflag/checkflag/goto_if_set etc.)
        used_flags = set()
        maps_dir = self._root_dir / 'data' / 'maps'
        if maps_dir.is_dir():
            for scripts_file in maps_dir.rglob('scripts.inc'):
                try:
                    text = scripts_file.read_text(encoding='utf-8')
                    for m in re.finditer(r'\b(FLAG_\w+)\b', text):
                        used_flags.add(m.group(1))
                except Exception:
                    pass

        # Also scan src/ for C code references
        src_dir = self._root_dir / 'src'
        if src_dir.is_dir():
            for c_file in src_dir.rglob('*.c'):
                try:
                    text = c_file.read_text(encoding='utf-8')
                    for m in re.finditer(r'\b(FLAG_\w+)\b', text):
                        used_flags.add(m.group(1))
                except Exception:
                    pass

        # Find FLAG_UNUSED_* that aren't referenced anywhere
        unused = sorted(
            f for f in all_flags
            if f.startswith('FLAG_UNUSED_') and f not in used_flags
        )

        if unused:
            # Show the first few and copy the first one
            first = unused[0]
            msg = (f'Found {len(unused)} unused flags.\n\n'
                   f'Next available: {first}\n\n'
                   f'First 10:\n' + '\n'.join(unused[:10]))
            QMessageBox.information(self, 'Unused Flags', msg)
            self._mw.log_message(
                f'Event Editor: {len(unused)} unused flags found, '
                f'next available: {first}')
        else:
            QMessageBox.warning(self, 'Unused Flags',
                                'No FLAG_UNUSED_* constants found. '
                                'You may need to define new flags in '
                                'include/constants/flags.h')

    # ─────────────────────────────────────────────────────────────────────
    # Script Lookup (project-wide search)
    # ─────────────────────────────────────────────────────────────────────

    def _on_find_script(self):
        """Open the project-wide script search dialog."""
        if not hasattr(self, '_script_index') or not self._script_index:
            QMessageBox.information(self, 'Find Script',
                                    'No project loaded.')
            return

        from eventide.ui.script_search_dialog import ScriptSearchDialog
        dlg = ScriptSearchDialog(self._script_index, parent=self)
        dlg.navigate_requested.connect(self._navigate_to_script_label)
        dlg.exec()

    def _navigate_to_script_label(self, label: str,
                                   map_name: str | None):
        """Navigate to a script label — load the map and select the event.

        For map scripts: loads the map, finds which event owns the label
        (either as the entry script or as a sub-page), and selects it.
        For shared scripts: loads the first map that references it, or
        shows a message if no map references it.
        """
        if not self._root_dir:
            return

        if not map_name:
            # Shared script — try to find a map that references it
            map_name = self._find_map_referencing_label(label)
            if not map_name:
                self._mw.log_message(
                    f'Find Script: "{label}" is a shared script not '
                    f'directly attached to any map event.')
                QMessageBox.information(
                    self, 'Find Script',
                    f'"{label}" is a shared script.\n\n'
                    f'It lives in data/scripts/ and may be called by '
                    f'multiple maps. Load a map that uses it to view it '
                    f'in context.')
                return

        # Check for unsaved changes
        if not self._check_unsaved_before_map_switch():
            return

        # Load the target map
        map_dir = self._root_dir / 'data' / 'maps' / map_name
        if not (map_dir / 'map.json').is_file():
            QMessageBox.warning(self, 'Find Script',
                                f'Map directory not found:\n{map_dir}')
            return

        self._load_map(map_dir)

        # Find the event that owns this label
        # 1. Direct match: event's script field == label
        for i, obj in enumerate(self._objects):
            if obj.get('script') == label:
                self.obj_combo.setCurrentIndex(i)
                self._mw.log_message(
                    f'Find Script: navigated to {label} in {map_name}')
                return

        # 2. Check page labels on each event
        for i, obj in enumerate(self._objects):
            for pi, page in enumerate(obj.get('_pages', [])):
                page_label = page.get('_label', '')
                if page_label == label:
                    self.obj_combo.setCurrentIndex(i)
                    if pi > 0:
                        self.page_tabs.setCurrentIndex(pi)
                    self._mw.log_message(
                        f'Find Script: navigated to {label} '
                        f'(page {pi + 1}) in {map_name}')
                    return

        # 3. Check inline sub-labels inside page commands
        for i, obj in enumerate(self._objects):
            for pi, page in enumerate(obj.get('_pages', [])):
                for cmd in page.get('commands', []):
                    if (cmd and cmd[0] == '_label_marker' and
                            len(cmd) > 1 and cmd[1] == label):
                        self.obj_combo.setCurrentIndex(i)
                        if pi > 0:
                            self.page_tabs.setCurrentIndex(pi)
                        self._mw.log_message(
                            f'Find Script: navigated to {label} '
                            f'(inline in page {pi + 1}) in {map_name}')
                        return

        # 4. Label is in this map's scripts.inc but not attached to an event
        #    (orphaned or map-level script). Still loaded the right map.
        self._mw.log_message(
            f'Find Script: loaded {map_name} — label "{label}" '
            f'found in scripts.inc but not attached to a visible event.')

    def _find_map_referencing_label(self, label: str) -> str | None:
        """Search map.json files for an event that references this label."""
        maps_dir = self._root_dir / 'data' / 'maps'
        if not maps_dir.is_dir():
            return None

        for map_folder in sorted(maps_dir.iterdir()):
            map_json = map_folder / 'map.json'
            if not map_json.is_file():
                continue
            try:
                text = map_json.read_text(encoding='utf-8', errors='replace')
                if f'"{label}"' in text:
                    return map_folder.name
            except OSError:
                continue
        return None

    # ─────────────────────────────────────────────────────────────────────
    # Hidden Item creation / editing
    # ─────────────────────────────────────────────────────────────────────

    def _on_hidden_item_changed(self):
        """Called when any field in the hidden item panel is edited."""
        if self._loading:
            return
        # Update the combo label to reflect the new item name
        idx = self._current_obj_idx
        if 0 <= idx < len(self._objects):
            item_const = self._hidden_item_panel.item_picker.selected_constant()
            name = _resolve_name(item_const) if item_const else '(no item)'
            self.obj_combo.setItemText(idx, f'[Hidden Item] {name}')
        self._mark_dirty()

    def _create_new_hidden_item(self):
        """Add a new hidden_item bg_event to the current map."""
        if not self._map_data:
            return

        # Auto-find an unused flag
        flags = self._find_unused_flags(1)
        flag = flags[0] if flags else ''

        # Build the new event dict
        new_ev = {
            'type': 'hidden_item',
            'x': 0,
            'y': 0,
            'elevation': 0,
            'item': 'ITEM_NONE',
            'flag': flag,
            'quantity': 1,
            'underfoot': False,
            '_event_type': 'bg',
            '_pages': [{'commands': [], '_label': '(hidden item)',
                        '_short_label': '(hidden item)'}],
        }
        self._objects.append(new_ev)
        self.obj_combo.addItem('[Hidden Item] None')
        self.obj_combo.setCurrentIndex(self.obj_combo.count() - 1)
        self._mark_dirty()

    def _delete_hidden_item(self):
        """Remove the currently selected hidden item from the map."""
        idx = self._current_obj_idx
        if idx < 0 or idx >= len(self._objects):
            return
        obj = self._objects[idx]
        if obj.get('_event_type') != 'bg' or obj.get('type') != 'hidden_item':
            return

        item_name = _resolve_name(obj.get('item', ''))
        reply = QMessageBox.question(
            self, 'Delete Hidden Item',
            f'Delete hidden item "{item_name}" at ({obj.get("x", 0)}, '
            f'{obj.get("y", 0)})?\n\nThis removes it from the map.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._objects.pop(idx)
        self.obj_combo.removeItem(idx)
        self._current_obj_idx = -1
        if self.obj_combo.count() > 0:
            self.obj_combo.setCurrentIndex(max(0, idx - 1))
        self._mark_dirty()

    # ─────────────────────────────────────────────────────────────────────
    # New NPC Script templates
    # ─────────────────────────────────────────────────────────────────────

    def _on_new_npc_script(self):
        """Show a menu of script templates organized by category."""
        menu = QMenu(self)

        # ── New Hidden Item (always available, no script needed) ─────
        menu.addAction('New Hidden Item — invisible ground pickup',
                       self._create_new_hidden_item)
        menu.addSeparator()

        # Script-based templates require a selected event with a label
        if self._current_obj_idx < 0:
            act = menu.addAction('(Select an event to see script templates)')
            act.setEnabled(False)
            menu.exec(self._btn_new_script.mapToGlobal(
                self._btn_new_script.rect().bottomLeft()))
            return
        obj = self._objects[self._current_obj_idx]
        script = obj.get('script', '')
        if not script or script == '0x0':
            act = menu.addAction('(Set a script name first for script templates)')
            act.setEnabled(False)
            menu.exec(self._btn_new_script.mapToGlobal(
                self._btn_new_script.rect().bottomLeft()))
            return

        gfx = obj.get('graphics_id', '')

        # ── Context-aware: item ball gets its template first ─────────
        if gfx == 'OBJ_EVENT_GFX_ITEM_BALL':
            menu.addAction('Item Ball — pick-up item (finditem)',
                           lambda: self._create_item_ball_script(obj, script))
            menu.addSeparator()

        # ── NPC Templates ────────────────────────────────────────────
        npc_menu = menu.addMenu('NPC Scripts')
        npc_menu.addAction('Simple Talker — just says something',
                           lambda: self._create_npc_talker(obj, script))
        npc_menu.addAction('Trainer — battle with intro/defeat dialogue',
                           lambda: self._create_npc_trainer(obj, script))
        npc_menu.addAction('Item Giver — gives an item once (flag-gated)',
                           lambda: self._create_npc_item_giver(obj, script))
        npc_menu.addAction('Flag-gated NPC — changes dialogue based on a flag',
                           lambda: self._create_npc_flag_gated(obj, script))

        # ── Sign / BG Event Templates ────────────────────────────────
        sign_menu = menu.addMenu('Signs && BG Events')
        sign_menu.addAction('Simple Sign — displays a message',
                            lambda: self._create_sign_simple(obj, script))
        sign_menu.addAction('Hidden Item Script — finditem with custom script',
                            lambda: self._create_sign_hidden_item(obj, script))

        # ── Map/Warp Templates ───────────────────────────────────────
        map_menu = menu.addMenu('Map Scripts')
        map_menu.addAction('Door Warp — warp after door animation',
                           lambda: self._create_door_warp(obj, script))
        map_menu.addAction('Cave Warp — warp with no door',
                           lambda: self._create_cave_warp(obj, script))

        # ── Wrapper / Callstd Templates ──────────────────────────────
        wrap_menu = menu.addMenu('Standard Wrappers')
        wrap_menu.addAction('Nurse — heal party (callstd)',
                            lambda: self._create_nurse_wrapper(obj, script))
        wrap_menu.addAction('PC — access storage (callstd)',
                            lambda: self._create_pc_wrapper(obj, script))
        wrap_menu.addAction('Mart — shop with item list',
                            lambda: self._create_mart_wrapper(obj, script))

        # ── Field Object Templates ───────────────────────────────────
        field_menu = menu.addMenu('Field Objects')
        field_menu.addAction('Cut Tree — requires Cut',
                             lambda: self._create_field_cut(obj, script))
        field_menu.addAction('Rock Smash — requires Rock Smash',
                             lambda: self._create_field_rocksmash(obj, script))
        field_menu.addAction('Strength Boulder — requires Strength',
                             lambda: self._create_field_strength(obj, script))

        menu.exec(self._btn_new_script.mapToGlobal(
            self._btn_new_script.rect().bottomLeft()))

    def _find_unused_flags(self, count=1) -> list[str]:
        """Find the next N unused FLAG_UNUSED_* constants in the project."""
        all_flags = set(ConstantsManager.FLAGS)
        used_flags: set[str] = set()
        maps_dir = self._root_dir / 'data' / 'maps'
        if maps_dir.is_dir():
            for sf in maps_dir.rglob('scripts.inc'):
                try:
                    text = sf.read_text(encoding='utf-8')
                    for m in re.finditer(r'\b(FLAG_\w+)\b', text):
                        used_flags.add(m.group(1))
                except Exception:
                    pass
        unused = sorted(
            f for f in all_flags
            if f.startswith('FLAG_UNUSED_') and f not in used_flags)
        return unused[:count]

    def _register_text(self, label: str, content: str):
        """Add a text entry to the in-memory texts dict and update __texts__."""
        self._texts[label] = content
        self._local_text_labels.add(label)
        texts = _ALL_SCRIPTS.get('__texts__')
        if texts is not None:
            texts[label] = content

    def _register_labels(self, labels: list[str]):
        """Add script labels to the global list for dropdown population."""
        global _SCRIPT_LABELS
        for lbl in labels:
            if lbl not in _SCRIPT_LABELS:
                _SCRIPT_LABELS.append(lbl)
        _SCRIPT_LABELS.sort()

    def _create_item_ball_script(self, obj, script):
        """Item ball that gives a finditem pickup.

        Creates a simple script with ``finditem ITEM_XXX`` and writes it
        to ``data/scripts/item_ball_scripts.inc`` so it lives alongside
        the other item ball scripts in the project.
        """
        # Let the user pick an item from the full item list
        items = sorted(ConstantsManager.ITEMS)
        if not items:
            QMessageBox.warning(self, 'Item Ball',
                                'No items loaded. Open a project first.')
            return

        # Build display list with friendly names
        display_items = []
        for const in items:
            name = _resolve_name(const)
            display_items.append(f'{name}  ({const})')

        choice, ok = QInputDialog.getItem(
            self, 'Item Ball', 'Select the item to place:', display_items,
            editable=False)
        if not ok:
            return

        # Extract constant from "Display Name  (ITEM_CONSTANT)"
        item_const = choice.rsplit('(', 1)[-1].rstrip(')')

        cmds = [
            ('finditem', item_const),
            ('end',),
        ]
        obj['_pages'] = [{'commands': cmds, '_label': script,
                          '_short_label': script.split('_')[-1]}]

        # Mark this as an external script that should be saved to
        # data/scripts/item_ball_scripts.inc
        item_ball_file = self._root_dir / 'data' / 'scripts' / 'item_ball_scripts.inc'
        self._external_script_labels.add(script)
        self._external_script_files[script] = item_ball_file

        self._register_labels([script])
        self._on_object_changed(self._current_obj_idx)
        self._mark_dirty()

        item_name = _resolve_name(item_const)
        self._mw.log_message(
            f'Event Editor: created item ball script for {script} '
            f'(item: {item_name})')

        # Update the combo box text to show the item name
        if self._current_obj_idx >= 0:
            self.obj_combo.setItemText(
                self._current_obj_idx, f'[Item] {item_name}')

        QMessageBox.information(
            self, 'Item Ball Created',
            f'Created item ball script:\n\n'
            f'  Script: {script}\n'
            f'  Item: {item_name} ({item_const})\n\n'
            f'The script will be saved to\n'
            f'data/scripts/item_ball_scripts.inc when you save.')

    def _create_npc_talker(self, obj, script):
        """Simple NPC that says one message."""
        text_label = f'{script}_Text'
        placeholder = 'Hello there!$'

        cmds = [
            ('message', text_label, placeholder, 'MSGBOX_NPC'),
        ]
        obj['_pages'] = [{'commands': cmds, '_label': script,
                          '_short_label': script.split('_')[-1]}]

        self._register_text(text_label, placeholder)
        self._register_labels([script])
        self._on_object_changed(self._current_obj_idx)
        self._mark_dirty()
        self._mw.log_message(f'Event Editor: created talker script for {script}')

    def _load_porysuite_trainers(self) -> dict:
        """Load PorySuite's trainers.json for trainer names and metadata."""
        if not self._root_dir:
            return {}
        trainers_json = self._root_dir / 'src' / 'data' / 'trainers.json'
        if not trainers_json.exists():
            return {}
        try:
            import json
            with trainers_json.open(encoding='utf-8') as fh:
                return json.load(fh)
        except Exception:
            return {}

    def _trainer_display_name(self, const: str, data: dict) -> str:
        """Extract display name from trainer data, e.g. 'IRIS' from '_("IRIS")'."""
        raw = data.get('trainerName', '')
        # Parse _("NAME") format
        import re
        m = re.search(r'_\(\s*"([^"]*)"\s*\)', raw)
        if m and m.group(1):
            return m.group(1).title()
        # Fallback: derive from constant name (TRAINER_LASS_IRIS → Iris)
        parts = const.replace('TRAINER_', '').split('_')
        return parts[-1].title() if parts else 'Trainer'

    def _create_npc_trainer(self, obj, script):
        """Trainer NPC — pick from PorySuite's trainer list, auto-scaffold everything."""
        # Load PorySuite trainer data for the picker
        ps_trainers = self._load_porysuite_trainers()

        # Build a picker dialog with trainer names
        from PyQt6.QtWidgets import QDialog, QDialogButtonBox
        dlg = QDialog(self)
        dlg.setWindowTitle('Select Trainer')
        dlg.resize(400, 120)
        dlg_layout = QVBoxLayout(dlg)
        dlg_layout.addWidget(QLabel(
            'Pick the trainer constant (from PorySuite\'s trainer editor):'))
        trainer_picker = ConstantPicker(ConstantsManager.TRAINERS, prefix='TRAINER_')
        dlg_layout.addWidget(trainer_picker)

        # Show trainer's display name as a preview
        name_preview = QLabel('')
        name_preview.setStyleSheet('color: #888; font-style: italic;')
        dlg_layout.addWidget(name_preview)

        def _on_trainer_changed():
            const = trainer_picker.selected_constant()
            if const in ps_trainers:
                display = self._trainer_display_name(const, ps_trainers[const])
                cls = ps_trainers[const].get('trainerClass', '')
                cls_short = cls.replace('TRAINER_CLASS_', '').replace('_', ' ').title()
                name_preview.setText(f'{cls_short} {display}')
            else:
                name_preview.setText('')

        trainer_picker.currentTextChanged.connect(lambda _: _on_trainer_changed())

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        dlg_layout.addWidget(btns)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        trainer_const = trainer_picker.selected_constant()
        if not trainer_const:
            return

        # Get a short name for label naming from PorySuite's data
        trainer_data = ps_trainers.get(trainer_const, {})
        short_name = self._trainer_display_name(trainer_const, trainer_data)

        # Build map-specific text labels
        map_name = self._map_dir.name if self._map_dir else 'Map'
        intro_label = f'{map_name}_Text_{short_name}Intro'
        defeat_label = f'{map_name}_Text_{short_name}Defeat'
        post_label = f'{map_name}_Text_{short_name}PostBattle'

        # Get trainer class for flavor text
        cls = trainer_data.get('trainerClass', '')
        cls_display = cls.replace('TRAINER_CLASS_', '').replace('_', ' ').title()
        name_display = short_name

        self._register_text(intro_label,
                            f"I'm {cls_display} {name_display}!\\nReady to battle!$")
        self._register_text(defeat_label,
                            f"You beat me...$")
        self._register_text(post_label,
                            f"That was a good battle!$")

        cmds = [
            ('trainerbattle_single',
             f'{trainer_const}, {intro_label}, {defeat_label}'),
            ('message', post_label, 'That was a good battle!$', 'MSGBOX_AUTOCLOSE'),
            ('end',),
        ]
        obj['_pages'] = [{'commands': cmds, '_label': script,
                          '_short_label': script.split('_')[-1]}]

        self._register_labels([script])
        self._on_object_changed(self._current_obj_idx)
        self._mark_dirty()
        self._mw.log_message(
            f'Event Editor: created trainer script for {trainer_const}\n'
            f'  Intro: {intro_label}\n'
            f'  Defeat: {defeat_label}\n'
            f'  Post-battle: {post_label}')

    def _create_npc_item_giver(self, obj, script):
        """NPC that gives an item once, using a flag to track it."""
        flags = self._find_unused_flags(1)
        if not flags:
            QMessageBox.warning(self, 'New NPC Script',
                                'No unused flags available (FLAG_UNUSED_*).')
            return
        flag = flags[0]

        text_give = f'{script}_Text_Give'
        text_done = f'{script}_Text_AlreadyGot'
        label_done = f'{script}_AlreadyGot'

        self._register_text(text_give,
                            'Here, take this!$')
        self._register_text(text_done,
                            'I hope that item is useful to you!$')

        main_cmds = [
            ('goto_if_set', flag, label_done),
            ('message', text_give, 'Here, take this!$', 'MSGBOX_DEFAULT'),
            ('additem', 'ITEM_POTION, 1'),
            ('setflag', flag),
            ('end',),
        ]
        done_cmds = [
            ('message', text_done,
             'I hope that item is useful to you!$', 'MSGBOX_NPC'),
        ]

        obj['_pages'] = [
            {'commands': main_cmds, '_label': script,
             '_short_label': script.split('_')[-1]},
            {'commands': done_cmds, '_label': label_done,
             '_short_label': 'AlreadyGot'},
        ]

        self._register_labels([script, label_done])
        self._on_object_changed(self._current_obj_idx)
        self._mark_dirty()
        self._mw.log_message(
            f'Event Editor: created item giver for {script} (flag: {flag})')
        QMessageBox.information(
            self, 'Item Giver Created',
            f'Created item giver script:\n\n'
            f'  Flag: {flag}\n'
            f'  Page 1: Gives item, sets flag\n'
            f'  Page 2: Already-received dialogue\n\n'
            f'Double-click the Give Item command to change\n'
            f'the item. Edit the text to change what the NPC says.')

    def _create_npc_flag_gated(self, obj, script):
        """NPC that says different things based on a flag."""
        flags = self._find_unused_flags(1)
        if not flags:
            QMessageBox.warning(self, 'New NPC Script',
                                'No unused flags available (FLAG_UNUSED_*).')
            return
        flag = flags[0]

        text_before = f'{script}_Text_Before'
        text_after = f'{script}_Text_After'
        label_after = f'{script}_After'

        self._register_text(text_before,
                            "I'm waiting for something to happen...$")
        self._register_text(text_after,
                            "Things are different now!$")

        main_cmds = [
            ('goto_if_set', flag, label_after),
            ('message', text_before,
             "I'm waiting for something to happen...$", 'MSGBOX_NPC'),
        ]
        after_cmds = [
            ('message', text_after,
             "Things are different now!$", 'MSGBOX_NPC'),
        ]

        obj['_pages'] = [
            {'commands': main_cmds, '_label': script,
             '_short_label': script.split('_')[-1]},
            {'commands': after_cmds, '_label': label_after,
             '_short_label': 'After'},
        ]

        self._register_labels([script, label_after])
        self._on_object_changed(self._current_obj_idx)
        self._mark_dirty()
        self._mw.log_message(
            f'Event Editor: created flag-gated script for {script} (flag: {flag})')
        QMessageBox.information(
            self, 'Flag-Gated NPC Created',
            f'Created flag-gated NPC script:\n\n'
            f'  Flag: {flag}\n'
            f'  Page 1: Default dialogue (flag not set)\n'
            f'  Page 2: After flag is set\n\n'
            f'Set the flag from another script (e.g. a trigger\n'
            f'or another NPC) to change what this NPC says.')

    # ─────────────────────────────────────────────────────────────────────
    # Sign / BG Event templates
    # ─────────────────────────────────────────────────────────────────────

    def _create_sign_simple(self, obj, script):
        """Simple sign that shows a message when examined."""
        text_label = f'{script}_Text'
        self._register_text(text_label, 'This is a sign.$')

        cmds = [
            ('message', text_label, 'This is a sign.$', 'MSGBOX_SIGN'),
            ('end',),
        ]
        obj['_pages'] = [{'commands': cmds, '_label': script,
                          '_short_label': script.split('_')[-1]}]
        self._register_labels([script])
        self._on_object_changed(self._current_obj_idx)
        self._mark_dirty()
        self._mw.log_message(f'Event Editor: created sign script for {script}')

    def _create_sign_hidden_item(self, obj, script):
        """Hidden item pickup — invisible item on a BG event tile."""
        items = sorted(ConstantsManager.ITEMS)
        if not items:
            QMessageBox.warning(self, 'Hidden Item',
                                'No items loaded. Open a project first.')
            return

        display_items = []
        for const in items:
            name = _resolve_name(const)
            display_items.append(f'{name}  ({const})')

        choice, ok = QInputDialog.getItem(
            self, 'Hidden Item', 'Select the hidden item:', display_items,
            editable=False)
        if not ok:
            return
        item_const = choice.rsplit('(', 1)[-1].rstrip(')')

        flags = self._find_unused_flags(1)
        if not flags:
            QMessageBox.warning(self, 'Hidden Item',
                                'No unused flags available (FLAG_UNUSED_*).')
            return
        flag = flags[0]

        cmds = [
            ('finditem', item_const),
            ('end',),
        ]
        obj['_pages'] = [{'commands': cmds, '_label': script,
                          '_short_label': script.split('_')[-1]}]
        self._register_labels([script])
        self._on_object_changed(self._current_obj_idx)
        self._mark_dirty()
        item_name = _resolve_name(item_const)
        self._mw.log_message(
            f'Event Editor: created hidden item script for {script} '
            f'(item: {item_name})')

    # ─────────────────────────────────────────────────────────────────────
    # Map / Warp templates
    # ─────────────────────────────────────────────────────────────────────

    def _create_door_warp(self, obj, script):
        """Door warp — plays door open/close, then warps the player."""
        cmds = [
            ('lockall',),
            ('applymovement', 'OBJ_EVENT_ID_PLAYER, Common_Movement_WalkInPlace'),
            ('waitmovement', '0'),
            ('setvar', 'VAR_0x8004, 0'),
            ('setvar', 'VAR_0x8005, 0'),
            ('special', 'DoFallWarp'),
            ('end',),
        ]
        obj['_pages'] = [{'commands': cmds, '_label': script,
                          '_short_label': script.split('_')[-1]}]
        self._register_labels([script])
        self._on_object_changed(self._current_obj_idx)
        self._mark_dirty()
        self._mw.log_message(f'Event Editor: created door warp for {script}')
        QMessageBox.information(
            self, 'Door Warp Created',
            f'Created door warp script:\n\n'
            f'Edit the warp destination by changing the\n'
            f'VAR_0x8004 (map) and VAR_0x8005 (warp ID)\n'
            f'values, or replace with a direct warp command.')

    def _create_cave_warp(self, obj, script):
        """Simple warp with no door animation."""
        cmds = [
            ('lockall',),
            ('warp', 'MAP_PALLET_TOWN, 0, 5, 5'),
            ('waitstate',),
            ('end',),
        ]
        obj['_pages'] = [{'commands': cmds, '_label': script,
                          '_short_label': script.split('_')[-1]}]
        self._register_labels([script])
        self._on_object_changed(self._current_obj_idx)
        self._mark_dirty()
        self._mw.log_message(f'Event Editor: created cave warp for {script}')
        QMessageBox.information(
            self, 'Warp Created',
            f'Created warp script:\n\n'
            f'Double-click the warp command to change the\n'
            f'destination map, warp ID, and coordinates.')

    # ─────────────────────────────────────────────────────────────────────
    # Standard wrapper templates (callstd)
    # ─────────────────────────────────────────────────────────────────────

    def _create_nurse_wrapper(self, obj, script):
        """Nurse NPC — heals the party using callstd STD_POKEMON_CENTER_NURSE."""
        cmds = [
            ('lock',),
            ('faceplayer',),
            ('callstd', 'STD_POKEMON_CENTER_NURSE'),
            ('end',),
        ]
        obj['_pages'] = [{'commands': cmds, '_label': script,
                          '_short_label': script.split('_')[-1]}]
        self._register_labels([script])
        self._on_object_changed(self._current_obj_idx)
        self._mark_dirty()
        self._mw.log_message(f'Event Editor: created nurse script for {script}')

    def _create_pc_wrapper(self, obj, script):
        """PC — opens the storage system using callstd STD_PC."""
        cmds = [
            ('lockall',),
            ('callstd', 'STD_PC'),
            ('releaseall',),
            ('end',),
        ]
        obj['_pages'] = [{'commands': cmds, '_label': script,
                          '_short_label': script.split('_')[-1]}]
        self._register_labels([script])
        self._on_object_changed(self._current_obj_idx)
        self._mark_dirty()
        self._mw.log_message(f'Event Editor: created PC script for {script}')

    def _create_mart_wrapper(self, obj, script):
        """Mart NPC — opens a shop with an editable item list."""
        text_label = f'{script}_Text'
        mart_label = f'{script}_Mart'
        self._register_text(text_label, 'Welcome! How may I help you?$')

        cmds = [
            ('lock',),
            ('faceplayer',),
            ('message', text_label, 'Welcome! How may I help you?$',
             'MSGBOX_DEFAULT'),
            ('pokemart', mart_label),
            ('release',),
            ('end',),
        ]
        obj['_pages'] = [{'commands': cmds, '_label': script,
                          '_short_label': script.split('_')[-1]}]
        self._register_labels([script])
        self._on_object_changed(self._current_obj_idx)
        self._mark_dirty()
        self._mw.log_message(f'Event Editor: created mart script for {script}')
        QMessageBox.information(
            self, 'Mart Created',
            f'Created mart script:\n\n'
            f'The mart item list label is: {mart_label}\n'
            f'You will need to define the item list in your\n'
            f'scripts.inc file as a .align 2 / .2byte block.')

    # ─────────────────────────────────────────────────────────────────────
    # Field object templates (Cut/Rock Smash/Strength)
    # ─────────────────────────────────────────────────────────────────────

    def _create_field_cut(self, obj, script):
        """Cut tree — standard cuttable tree script."""
        cmds = [
            ('lockall',),
            ('special', 'EventScript_CutTree'),
            ('end',),
        ]
        obj['_pages'] = [{'commands': cmds, '_label': script,
                          '_short_label': script.split('_')[-1]}]
        self._register_labels([script])
        self._on_object_changed(self._current_obj_idx)
        self._mark_dirty()
        self._mw.log_message(f'Event Editor: created cut tree script for {script}')
        QMessageBox.information(
            self, 'Cut Tree Created',
            f'Created cut tree script.\n\n'
            f'Make sure the object graphic is set to the\n'
            f'cuttable tree sprite for your project.')

    def _create_field_rocksmash(self, obj, script):
        """Rock Smash — standard smashable rock script."""
        cmds = [
            ('lockall',),
            ('special', 'EventScript_SmashRock'),
            ('end',),
        ]
        obj['_pages'] = [{'commands': cmds, '_label': script,
                          '_short_label': script.split('_')[-1]}]
        self._register_labels([script])
        self._on_object_changed(self._current_obj_idx)
        self._mark_dirty()
        self._mw.log_message(
            f'Event Editor: created rock smash script for {script}')

    def _create_field_strength(self, obj, script):
        """Strength boulder — pushable boulder script."""
        cmds = [
            ('lockall',),
            ('special', 'EventScript_StrengthBoulder'),
            ('end',),
        ]
        obj['_pages'] = [{'commands': cmds, '_label': script,
                          '_short_label': script.split('_')[-1]}]
        self._register_labels([script])
        self._on_object_changed(self._current_obj_idx)
        self._mark_dirty()
        self._mw.log_message(
            f'Event Editor: created strength boulder script for {script}')

    # ─────────────────────────────────────────────────────────────────────
    # Find in commands (Ctrl+F)
    # ─────────────────────────────────────────────────────────────────────

    def _toggle_search(self):
        """Show/hide the inline search bar."""
        if self._search_bar.isVisible():
            self._close_search()
        else:
            self._search_bar.show()
            self._search_edit.setFocus()
            self._search_edit.selectAll()

    def _close_search(self):
        """Hide the search bar and clear highlights."""
        self._search_bar.hide()
        self._search_edit.clear()
        self._search_count_lbl.clear()
        # Remove highlight from all items
        for i in range(self._cmd_list.count()):
            item = self._cmd_list.item(i)
            if item:
                item.setBackground(QColor(0, 0, 0, 0))

    def _on_search_commands(self, text):
        """Highlight matching commands as the user types."""
        text = text.lower().strip()
        match_count = 0
        for i in range(self._cmd_list.count()):
            item = self._cmd_list.item(i)
            if not item:
                continue
            if text and text in item.text().lower():
                item.setBackground(QColor(52, 152, 219, 60))  # subtle blue
                match_count += 1
            else:
                item.setBackground(QColor(0, 0, 0, 0))
        if text:
            self._search_count_lbl.setText(f'{match_count} found')
        else:
            self._search_count_lbl.clear()

    def _on_search_next(self):
        """Jump to the next matching command after current selection."""
        text = self._search_edit.text().lower().strip()
        if not text:
            return
        start = self._cmd_list.currentRow() + 1
        for i in range(start, self._cmd_list.count()):
            item = self._cmd_list.item(i)
            if item and text in item.text().lower():
                self._cmd_list.setCurrentRow(i)
                return
        # Wrap around to the top
        for i in range(0, start):
            item = self._cmd_list.item(i)
            if item and text in item.text().lower():
                self._cmd_list.setCurrentRow(i)
                return

    def _on_search_prev(self):
        """Jump to the previous matching command before current selection."""
        text = self._search_edit.text().lower().strip()
        if not text:
            return
        start = self._cmd_list.currentRow() - 1
        for i in range(start, -1, -1):
            item = self._cmd_list.item(i)
            if item and text in item.text().lower():
                self._cmd_list.setCurrentRow(i)
                return
        # Wrap around to the bottom
        for i in range(self._cmd_list.count() - 1, start, -1):
            item = self._cmd_list.item(i)
            if item and text in item.text().lower():
                self._cmd_list.setCurrentRow(i)
                return

    # ─────────────────────────────────────────────────────────────────────
    # Save
    # ─────────────────────────────────────────────────────────────────────

    def _on_save(self):
        if not self._map_dir or not self._map_data:
            QMessageBox.information(self, 'Save', 'No map loaded.')
            return

        self._collect_current()

        from eventide.backend.eventide_utils import (
            write_scripts_inc, write_text_inc,
        )

        # Collect all page data keyed by their actual script label.
        # Pages may contain merged sub-labels separated by _label_marker
        # pseudo-commands — split them back into separate label:: blocks.
        # Condition pages need their conditional gotos re-inserted into the
        # entry label (they were extracted for display).
        # Skip external scripts — those live in their own files.
        #
        # IMPORTANT: Process the currently-selected event LAST.
        # Multiple events can share sub-labels via goto (e.g. three triggers
        # all goto the same battle script).  Each event has its own merged
        # copy of the sub-label.  The user can only edit one at a time, so
        # the currently-selected event's copy has the latest edits — it must
        # be saved last so it overwrites stale copies from other events.
        save_pages = OrderedDict()
        ordered_objects = list(self._objects)
        if 0 <= self._current_obj_idx < len(ordered_objects):
            current_obj = ordered_objects.pop(self._current_obj_idx)
            ordered_objects.append(current_obj)
        for obj in ordered_objects:
            script = obj.get('script', '')
            if not script or script == '0x0':
                continue
            pages = obj.get('_pages', [{'commands': []}])

            # Identify condition pages and collect their goto commands.
            # These need to be prepended to the entry/default page.
            entry_label = script
            condition_gotos: list[tuple] = []
            default_page = None
            preamble_cmds: list[tuple] = []

            for page in pages:
                cond_cmd = page.get('_condition_cmd')
                if cond_cmd:
                    condition_gotos.append(cond_cmd)
                elif page.get('_condition') is None:
                    # This is the default page (no condition)
                    default_page = page
                    entry_label = page.get('_label', script)

            # Process each page — split on _label_marker boundaries
            for page in pages:
                page_label = page.get('_label', script)
                if page_label == '0x0':
                    continue
                all_cmds = page.get('commands', [])

                # For the default page, detect preamble commands that were
                # shared with condition pages, then insert the condition
                # gotos after the preamble.
                is_default = (page.get('_condition') is None
                              and not page.get('_condition_cmd'))

                if is_default and condition_gotos:
                    # Find where preamble ends in this page's commands
                    _PREAMBLE = frozenset({
                        'lock', 'lockall', 'faceplayer', 'textcolor'})
                    preamble_end = 0
                    for ci, ct in enumerate(all_cmds):
                        if ct and ct[0] in _PREAMBLE:
                            preamble_end = ci + 1
                        else:
                            break
                    # Reconstruct: preamble + condition gotos + body
                    reconstructed = (list(all_cmds[:preamble_end])
                                     + list(condition_gotos)
                                     + list(all_cmds[preamble_end:]))
                    all_cmds = reconstructed
                    page_label = entry_label  # save under entry label
                elif not is_default and page.get('_condition_cmd'):
                    # Condition page — strip preamble commands since they
                    # are shared from the entry label (already saved there)
                    _PREAMBLE = frozenset({
                        'lock', 'lockall', 'faceplayer', 'textcolor'})
                    stripped = []
                    past_preamble = False
                    for ct in all_cmds:
                        if not past_preamble and ct and ct[0] in _PREAMBLE:
                            continue  # skip preamble — lives in entry
                        past_preamble = True
                        stripped.append(ct)
                    all_cmds = stripped

                # Split on _label_marker boundaries
                current_label = page_label
                current_cmds: list[tuple] = []
                for cmd in all_cmds:
                    if cmd and cmd[0] == '_label_marker':
                        if current_label not in self._external_script_labels:
                            save_pages[current_label] = current_cmds
                        current_label = cmd[1] if len(cmd) > 1 else current_label
                        current_cmds = []
                    else:
                        current_cmds.append(cmd)
                if current_label not in self._external_script_labels:
                    save_pages[current_label] = current_cmds

        # Include any modified movement routes in save_pages
        if hasattr(self, '_modified_movements'):
            for mov_label, mov_steps in self._modified_movements.items():
                save_pages[mov_label] = mov_steps

        scripts_path = self._map_dir / 'scripts.inc'
        text_path = self._map_dir / 'text.inc'

        try:
            write_scripts_inc(save_pages, self._hidden_lines, self._texts, scripts_path)
            # Only write labels that came from this map's text.inc (or were
            # created during editing).  Global labels from data/text/*.inc
            # must NOT be dumped into the map's local text.inc.
            local_texts = OrderedDict()
            for label, content in self._texts.items():
                if label in self._local_text_labels:
                    local_texts[label] = content
            if local_texts:
                write_text_inc(local_texts, text_path)

            # Split objects back into their original map.json arrays
            obj_events = []
            coord_events = []
            bg_events = []
            for obj in self._objects:
                obj.pop('_pages', None)
                etype = obj.pop('_event_type', 'object')
                if etype == 'object':
                    obj_events.append(obj)
                elif etype == 'coord':
                    coord_events.append(obj)
                elif etype == 'bg':
                    bg_events.append(obj)
                # map_script entries are synthetic — don't write to map.json
            self._map_data['object_events'] = obj_events
            self._map_data['coord_events'] = coord_events
            self._map_data['bg_events'] = bg_events
            map_json = self._map_dir / 'map.json'
            with map_json.open('w', encoding='utf-8', newline='\n') as fh:
                json.dump(self._map_data, fh, indent=2)
                fh.write('\n')

            # Save any modified external scripts back to their source files
            self._save_external_scripts()

            self._mw.log_message(
                f'Event Editor: saved {self._map_dir.name} '
                f'(scripts.inc + text.inc + map.json)')
            self._mw.setWindowModified(False)
            QMessageBox.information(self, 'Save', f'Saved {self._map_dir.name}.')
            self._load_map(self._map_dir)

        except Exception as e:
            QMessageBox.critical(self, 'Save', str(e))

    def _save_external_scripts(self):
        """Write modified external scripts back to their source files.

        For each external script label (e.g. item ball scripts), collect
        the current commands, convert them back to script text, and update
        the label block in the original .inc file.
        """
        if not self._external_script_labels:
            return

        from eventide.backend.eventide_utils import lines_from_commands

        # Collect current external script commands from loaded objects
        ext_pages: dict[str, list] = {}
        for obj in self._objects:
            script = obj.get('script', '')
            if not script:
                continue
            for page in obj.get('_pages', []):
                label = page.get('_label', script)
                if label in self._external_script_labels:
                    ext_pages[label] = page.get('commands', [])

        if not ext_pages:
            return

        # Group labels by source file
        by_file: dict[Path, dict[str, list]] = {}
        for label, cmds in ext_pages.items():
            src = self._external_script_files.get(label)
            if src:
                by_file.setdefault(src, {})[label] = cmds

        label_re = re.compile(r'^([A-Za-z0-9_]+)::')

        for src_file, labels_to_update in by_file.items():
            # Read existing file (create if it doesn't exist)
            try:
                if src_file.exists():
                    original = src_file.read_text(encoding='utf-8')
                else:
                    original = ''
            except Exception:
                continue

            lines = original.splitlines(keepends=True)

            # Track which labels already exist in the file
            existing_labels: set[str] = set()
            for line in lines:
                m = label_re.match(line.strip())
                if m:
                    existing_labels.add(m.group(1))

            # Rebuild the file, replacing existing label blocks
            output: list[str] = []
            current_label = None
            skipping = False

            for line in lines:
                m = label_re.match(line.strip())
                if m:
                    lbl = m.group(1)
                    if skipping and current_label in labels_to_update:
                        # Emit replacement content for previous label
                        cmds = labels_to_update[current_label]
                        cmd_lines = lines_from_commands(cmds, self._texts)
                        output.extend(cmd_lines)

                    current_label = lbl
                    output.append(line)
                    skipping = lbl in labels_to_update
                    continue

                if skipping:
                    continue  # skip old content
                output.append(line)

            # Flush final label
            if skipping and current_label in labels_to_update:
                cmds = labels_to_update[current_label]
                cmd_lines = lines_from_commands(cmds, self._texts)
                output.extend(cmd_lines)

            # Append new labels that weren't in the original file
            for label, cmds in labels_to_update.items():
                if label not in existing_labels:
                    output.append(f'\n{label}::\n')
                    cmd_lines = lines_from_commands(cmds, self._texts)
                    output.extend(cmd_lines)

            try:
                src_file.parent.mkdir(parents=True, exist_ok=True)
                src_file.write_text(''.join(output), encoding='utf-8')
                self._mw.log_message(
                    f'Event Editor: updated {len(labels_to_update)} script(s) '
                    f'in {src_file.name}')
            except Exception as e:
                self._mw.log_message(
                    f'Event Editor: WARNING — could not update {src_file.name}: {e}')

    # ═════════════════════════════════════════════════════════════════════════
    # Porymap bridge API — called by unified_mainwindow bridge signal handlers
    # ═════════════════════════════════════════════════════════════════════════

    @property
    def _current_map(self) -> str:
        """Return the currently loaded map name, or empty string."""
        if self._map_dir:
            return self._map_dir.name
        return ''

    def navigate_to_map(self, map_name: str):
        """Load a map by name (e.g. 'PalletTown'). No-op if already loaded."""
        if self._current_map == map_name:
            return
        if not self._root_dir:
            return
        map_dir = self._root_dir / 'data' / 'maps' / map_name
        if (map_dir / 'map.json').is_file():
            self._load_map(map_dir)

    def select_event_by_bridge(self, event_type: str, event_index: int,
                                script_label: str = '') -> bool:
        """Select an event by Porymap's type/index. Maps Porymap event types
        to our obj_combo indices. Returns True if an event was selected."""
        if not self._objects:
            return False

        # Try matching by script label first (most reliable)
        if script_label:
            for i, obj in enumerate(self._objects):
                if obj.get('script', '') == script_label:
                    self.obj_combo.setCurrentIndex(i)
                    return True
                # Check page labels too
                for page in obj.get('_pages', []):
                    if page.get('label', '') == script_label:
                        self.obj_combo.setCurrentIndex(i)
                        return True

        # Fall back to type + index matching
        # Porymap types: "Object", "Warp", "Trigger", "Sign", "HiddenItem"
        # Our _objects list mixes all types with type markers
        type_map = {
            'Object': 'object_events',
            'Warp': 'warp_events',
            'Trigger': 'coord_events',
            'WeatherTrigger': 'coord_events',
            'Sign': 'bg_events',
            'HiddenItem': 'bg_events',
            'SecretBase': 'bg_events',
        }
        target_group = type_map.get(event_type, '')
        if not target_group:
            return False

        # Count through objects to find the matching one
        group_count = 0
        for i, obj in enumerate(self._objects):
            if obj.get('_event_group', '') == target_group:
                if group_count == event_index:
                    self.obj_combo.setCurrentIndex(i)
                    return True
                group_count += 1
        return False

    def select_event_at_position(self, x: int, y: int) -> bool:
        """Select the event closest to (x, y) on the current map.
        Returns True if an event within 2 tiles was found and selected."""
        if not self._objects:
            return False
        best_idx = -1
        best_dist = float('inf')
        for i, obj in enumerate(self._objects):
            ox = obj.get('x', -999)
            oy = obj.get('y', -999)
            dist = abs(ox - x) + abs(oy - y)
            if dist < best_dist:
                best_dist = dist
                best_idx = i
        if best_idx >= 0 and best_dist <= 2:
            self.obj_combo.setCurrentIndex(best_idx)
            return True
        return False

    def reload_current_map(self, force: bool = False):
        """Reload the currently loaded map from disk (e.g. after Porymap saves).

        If the Event Editor has unsaved edits and *force* is False, prompts
        the user before clobbering their work. The watcher-driven call paths
        (Porymap bridge, SharedFileWatcher) always use the guarded default.
        """
        if not (self._map_dir and (self._map_dir / 'map.json').is_file()):
            return
        if not force and self._mw.isWindowModified():
            from PyQt6.QtWidgets import QMessageBox
            ret = QMessageBox.question(
                self, 'External Changes Detected',
                'The current map was modified outside the Event Editor '
                '(likely by Porymap), but you have unsaved changes here.\n\n'
                'Save your edits first, discard them and reload from disk, '
                'or keep working and ignore the external change?',
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if ret == QMessageBox.StandardButton.Save:
                self._on_save()
                return
            if ret != QMessageBox.StandardButton.Discard:
                return  # Cancel / closed dialog — keep user's work intact
        self._load_map(self._map_dir)

    def _on_open_in_porymap(self):
        """Open in Porymap button clicked — delegate to unified window."""
        # Walk up to the unified window and call its method
        parent = self.parent()
        while parent:
            if hasattr(parent, '_open_in_porymap'):
                parent._open_in_porymap()
                return
            parent = parent.parent()
