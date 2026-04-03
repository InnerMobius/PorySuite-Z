"""
EVENTide utilities — script parsing, constant loading, friendly labels.

Ported from ProjectZeldamon/eventide_utils.py. All paths are relative to a
project root directory passed as a parameter, NOT derived from __file__.

Module-level constants (FRIENDLY_COMMANDS, etc.) are initialized empty and
populated when load_project_constants(root_dir) is called.
"""

import re
from pathlib import Path
from collections import OrderedDict

# EVENTide's own docs directory (ships with the app, not the project)
_EVENTIDE_DOCS = Path(__file__).resolve().parent.parent / 'docs'


# ═════════════════════════════════════════════════════════════════════════════
# Module-level constants — populated by load_project_constants()
# ═════════════════════════════════════════════════════════════════════════════

FRIENDLY_COMMANDS: dict[str, str] = {}
OBJECT_GFX_MAP: dict[str, Path] = {}
MUSIC_CONSTANTS: list[str] = []
SFX_CONSTANTS: list[str] = []
FLAG_CONSTANTS: list[str] = []
VAR_CONSTANTS: list[str] = []
MOVEMENT_CONSTANTS: list[str] = []

COMMON_MOVEMENT_LABELS = {
    "Common_Movement_FaceOriginalDirection": "Face Original Direction",
    "Common_Movement_FacePlayer": "Face Player",
    "Common_Movement_FaceAwayPlayer": "Face Away From Player",
    "Common_Movement_Delay32": "Delay 32 Frames",
    "Common_Movement_Delay48": "Delay 48 Frames",
    "Common_Movement_ExclamationMark": "Exclamation Mark",
    "Common_Movement_QuestionMark": "Question Mark",
    "Common_Movement_WalkInPlaceFasterDown": "Walk In Place Faster Down",
    "Common_Movement_WalkInPlaceFasterUp": "Walk In Place Faster Up",
    "Common_Movement_WalkInPlaceFasterLeft": "Walk In Place Faster Left",
    "Common_Movement_WalkInPlaceFasterRight": "Walk In Place Faster Right",
}


def load_project_constants(root_dir: str) -> None:
    """Load all project-specific constants from the given project root.

    Call this once when a project is opened to populate the module-level
    constants used by the script parser and editor.
    """
    global FRIENDLY_COMMANDS, OBJECT_GFX_MAP
    global MUSIC_CONSTANTS, SFX_CONSTANTS, FLAG_CONSTANTS, VAR_CONSTANTS, MOVEMENT_CONSTANTS

    root = Path(root_dir)
    FRIENDLY_COMMANDS.clear()
    FRIENDLY_COMMANDS.update(_load_friendly_commands(root))
    OBJECT_GFX_MAP.clear()
    OBJECT_GFX_MAP.update(_load_object_graphics_map(root))
    MUSIC_CONSTANTS.clear()
    MUSIC_CONSTANTS.extend(_load_music(root))
    SFX_CONSTANTS.clear()
    SFX_CONSTANTS.extend(_load_sound_effects(root))
    FLAG_CONSTANTS.clear()
    FLAG_CONSTANTS.extend(_load_flags(root))
    VAR_CONSTANTS.clear()
    VAR_CONSTANTS.extend(_load_vars(root))
    MOVEMENT_CONSTANTS.clear()
    MOVEMENT_CONSTANTS.extend(_load_movements(root))


# ═════════════════════════════════════════════════════════════════════════════
# Loaders — all accept a Path root_dir
# ═════════════════════════════════════════════════════════════════════════════

def _load_friendly_commands(root: Path) -> dict[str, str]:
    mapping = {}
    # Look in EVENTide's own docs first, then the project root as fallback
    whitelist = _EVENTIDE_DOCS / 'eventide_whitelist.md'
    if not whitelist.exists():
        whitelist = root / 'docs' / 'eventide_whitelist.md'
    pattern = re.compile(r"\"([^\"]+)\"\s*:\s*\"([^\"]+)\"")
    if whitelist.exists():
        with whitelist.open(encoding='utf-8') as fh:
            for line in fh:
                m = pattern.search(line)
                if m:
                    mapping[m.group(1)] = m.group(2)
    if 'message' in mapping and 'msgbox' not in mapping:
        mapping['msgbox'] = mapping['message']
    return mapping


def _load_object_graphics_map(root: Path) -> dict[str, Path]:
    mapping = {}
    header = root / 'include' / 'constants' / 'event_objects.h'
    pics_root = root / 'graphics' / 'object_events' / 'pics'
    if not header.exists():
        return mapping
    with header.open(encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if not line.startswith('#define OBJ_EVENT_GFX_'):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            const = parts[1]
            if '(' in const:
                continue
            base = const[len('OBJ_EVENT_GFX_'):].lower()
            for sub in ['people', 'pokemon', 'misc']:
                path = pics_root / sub / f'{base}.png'
                if path.exists():
                    mapping[const] = path
                    break
    return mapping


def _load_constants_from_header(root: Path, header_rel: str, prefix: str) -> list[str]:
    """Generic loader for #define constants from a header file."""
    header = root / header_rel
    results = []
    seen = set()
    if header.exists():
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


def _load_music(root: Path) -> list[str]:
    return _load_constants_from_header(root, 'include/constants/songs.h', 'MUS_')


def _load_sound_effects(root: Path) -> list[str]:
    return _load_constants_from_header(root, 'include/constants/songs.h', 'SE_')


def _load_flags(root: Path, exclude_patterns: list[str] | None = None) -> list[str]:
    default_excludes = [
        r'^FLAG_TEMP_', r'^TEMP_FLAGS_', r'^FLAG_0x', r'^FLAG_SYS_',
        r'^FLAG_SPECIAL_', r'^SPECIAL_FLAGS_', r'^FLAGS_', r'_START$',
        r'_END$', r'_COUNT$',
    ]
    if exclude_patterns:
        default_excludes.extend(exclude_patterns)
    exclude_res = [re.compile(p) for p in default_excludes]

    all_flags = _load_constants_from_header(root, 'include/constants/flags.h', 'FLAG_')
    return [f for f in all_flags if not any(r.search(f) for r in exclude_res)]


def _load_vars(root: Path, exclude_patterns: list[str] | None = None) -> list[str]:
    default_excludes = [
        r'^VAR_TEMP_', r'^TEMP_VARS_', r'^VAR_OBJ_GFX_ID_', r'^VAR_0x',
        r'^VARS_', r'^SPECIAL_VARS_', r'^VAR_SPECIAL_', r'_START$',
        r'_END$', r'_COUNT$',
    ]
    if exclude_patterns:
        default_excludes.extend(exclude_patterns)
    exclude_res = [re.compile(p) for p in default_excludes]

    all_vars = _load_constants_from_header(root, 'include/constants/vars.h', 'VAR_')
    return [v for v in all_vars if not any(r.search(v) for r in exclude_res)]


def _load_movements(root: Path) -> list[str]:
    return _load_constants_from_header(root, 'include/constants/event_object_movement.h', 'MOVEMENT_TYPE_')


# ═════════════════════════════════════════════════════════════════════════════
# Friendly labels
# ═════════════════════════════════════════════════════════════════════════════

def friendly_label_for_line(line: str) -> str:
    line = line.strip()
    if not line:
        return ''
    if line.startswith('msgbox'):
        start = line.find('"')
        end = line.rfind('"')
        text = line[start + 1:end] if start != -1 and end != -1 else line[6:].strip()
        label = FRIENDLY_COMMANDS.get('msgbox', 'Show Text')
        return f'{label}: {text}' if text else label
    if line.startswith('call '):
        args = line.split(None, 1)[1] if ' ' in line else ''
        label = FRIENDLY_COMMANDS.get('call', 'Call Script')
        return f'{label}: {args}' if args else label
    if line.startswith('applymovement'):
        m = re.match(r'applymovement\s+([^,]+),\s*(\S+)', line)
        if m:
            target, movement = m.group(1), m.group(2)
            move_label = COMMON_MOVEMENT_LABELS.get(movement, movement)
            label = FRIENDLY_COMMANDS.get('applymovement', 'Apply Movement')
            return f'{label}: {target}, {move_label}'
    parts = line.split(None, 1)
    cmd = parts[0]
    args = parts[1] if len(parts) > 1 else ''
    friendly = FRIENDLY_COMMANDS.get(cmd)
    if not friendly:
        friendly = cmd.replace('_', ' ').title()
    return f'{friendly}: {args}' if args else friendly


def friendly_movement(name: str) -> str:
    if name.startswith('MOVEMENT_TYPE_'):
        name = name[len('MOVEMENT_TYPE_'):]
    return name.replace('_', ' ').title()


def load_command_categories(root_dir: str) -> dict[str, list[tuple[str, str]]]:
    """Return command categories parsed from the whitelist."""
    categories: dict[str, list[tuple[str, str]]] = {}
    current = None
    pattern = re.compile(r"\"([^\"]+)\"\s*:\s*\"([^\"]+)\"")
    whitelist = _EVENTIDE_DOCS / 'eventide_whitelist.md'
    if not whitelist.exists():
        whitelist = Path(root_dir) / 'docs' / 'eventide_whitelist.md'
    if whitelist.exists():
        with whitelist.open(encoding='utf-8') as fh:
            for line in fh:
                stripped = line.strip()
                if stripped.startswith('#'):
                    name = stripped.lstrip('#').strip()
                    if name and not name.startswith('EVENTide'):
                        current = name
                        categories.setdefault(current, [])
                    else:
                        current = None
                    continue
                m = pattern.search(stripped)
                if m and current:
                    categories[current].append((m.group(2), m.group(1)))
    return categories


# ═════════════════════════════════════════════════════════════════════════════
# Script parsing
# ═════════════════════════════════════════════════════════════════════════════

def parse_text_inc(path: Path) -> OrderedDict:
    """Return an OrderedDict mapping labels to message strings."""
    texts = OrderedDict()
    if not path or not path.exists():
        return texts
    label_re = re.compile(r"^([A-Za-z0-9_]+)::")
    string_re = re.compile(r"\.string\s+\"((?:\\.|[^\"])*)\"")
    current = None
    buf: list[str] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            m = label_re.match(line.strip())
            if m:
                if current is not None:
                    texts[current] = "".join(buf)
                current = m.group(1)
                buf = []
                continue
            if current is None:
                continue
            m = string_re.search(line)
            if m:
                segment = m.group(1)
                segment = segment.replace("\\n", "\n").replace("\\p", "\n\n")
                buf.append(segment)
    if current is not None:
        texts[current] = "".join(buf)
    return texts


def parse_all_texts(map_dir: Path, root_dir: Path) -> OrderedDict:
    """Load texts from the map-local text.inc AND data/text/*.inc.

    Map-local labels take priority (they are loaded first). The global
    data/text/ directory contains shared text used across multiple maps
    (e.g. sign_lady.inc, fame_checker.inc).
    """
    texts = parse_text_inc(map_dir / 'text.inc')

    # Also scan data/text/*.inc for additional labels
    global_text_dir = root_dir / 'data' / 'text'
    if global_text_dir.is_dir():
        for inc_file in sorted(global_text_dir.glob('*.inc')):
            extra = parse_text_inc(inc_file)
            for label, content in extra.items():
                if label not in texts:
                    texts[label] = content
    return texts


def write_text_inc(texts: dict, path: Path):
    """Write the label->text mapping back to text.inc."""
    lines = []
    for label, text in texts.items():
        escaped = text.replace("\n\n", "\\p").replace("\n", "\\n")
        escaped = escaped.replace('"', '\\"')
        lines.append(f"{label}::\n")
        lines.append(f"    .string \"{escaped}\"\n\n")
    with path.open('w', encoding='utf-8', newline='\n') as fh:
        fh.writelines(lines)


def parse_scripts_inc(map_dir: Path, texts: OrderedDict | None = None) -> tuple[dict[str, list], dict[str, list]]:
    """Return script commands and hidden lines for each label.

    Uses ``_parse_script_lines`` for the actual command parsing so there is
    only one parser implementation to maintain.  Hidden lines (blank lines,
    comments) are tracked separately with their position index.

    If *texts* is provided it is used for label→text lookups; otherwise the
    map-local ``text.inc`` is loaded automatically.
    """
    scripts_inc = map_dir / 'scripts.inc'
    if texts is None:
        texts = parse_text_inc(map_dir / 'text.inc')
    results: dict[str, list] = {}
    hidden_lines: dict[str, list] = {}
    if not scripts_inc.exists():
        return results, hidden_lines

    label_re = re.compile(r'^([A-Za-z0-9_]+)::')
    current = None
    visible_buf: list[str] = []  # lines to parse as commands
    hbuf: list = []              # (index, line) for hidden lines
    visible_index = 0

    with scripts_inc.open(encoding='utf-8') as fh:
        all_lines = [ln.rstrip('\n') for ln in fh]

    def _flush():
        nonlocal current, visible_buf, hbuf, visible_index
        if current is not None:
            results[current] = _parse_script_lines(visible_buf, texts)
            hidden_lines[current] = hbuf

    for line in all_lines:
        m = label_re.match(line.strip())
        if m:
            _flush()
            current = m.group(1)
            visible_buf = []
            hbuf = []
            visible_index = 0
            continue
        if current is None:
            hbuf.append((visible_index, line))
            continue

        stripped = line.strip()
        if not stripped or stripped.startswith('@'):
            hbuf.append((visible_index, line))
            continue

        visible_buf.append(line)
        visible_index += 1

    _flush()
    return results, hidden_lines


_FLAG_RE = re.compile(r'\bFLAG_[A-Za-z0-9_]+\b')
_VAR_RE = re.compile(r'\bVAR_[A-Za-z0-9_]+\b')


def _constants_in_lines(lines: list[str]) -> tuple[set[str], set[str]]:
    flags: set[str] = set()
    vars_: set[str] = set()
    for ln in lines:
        flags.update(_FLAG_RE.findall(ln))
        vars_.update(_VAR_RE.findall(ln))
    return flags, vars_


def _parse_script_lines(lines: list[str], texts: dict) -> list[tuple]:
    """Return command tuples parsed from a list of script lines.

    Each line is converted to a command tuple that the Event Editor can
    display with its specialized widgets.  Commands with known structure
    get dedicated tuple formats; everything else falls through to a
    generic ``(cmd, args_string)`` tuple.
    """
    cmds: list[tuple] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith('@'):
            i += 1
            continue

        # ── Messages ─────────────────────────────────────────────────
        if stripped.startswith('msgbox'):
            m = re.match(r'msgbox\s+"((?:\\.|[^\"]*)*)"(?:\s*,\s*(\S+))?', stripped)
            if m:
                text = m.group(1).replace('\\n', '\n').replace('\\p', '\n\n')
                msg_type = m.group(2)
                cmds.append(('message', None, text, msg_type))
                i += 1
                continue
            m = re.match(r'msgbox\s+([^,\s]+)\s*(?:,\s*(\S+))?', stripped)
            if m:
                label = m.group(1).rstrip(',')
                msg_type = m.group(2)
                cmds.append(('message', label, texts.get(label, ''), msg_type))
                i += 1
                continue

        # ── Warp variants ────────────────────────────────────────────
        _warp_matched = False
        for warp_cmd in ('warpteleport', 'warpsilent', 'warpdoor', 'warphole', 'warp'):
            if stripped.startswith(warp_cmd):
                m = re.match(rf'{warp_cmd}\s+([^,]+),\s*(\d+),\s*(\d+)', stripped)
                if m:
                    cmds.append((warp_cmd, m.group(1).strip(),
                                 int(m.group(2)), int(m.group(3))))
                    i += 1
                    _warp_matched = True
                break
        if _warp_matched:
            continue

        # ── Sound ────────────────────────────────────────────────────
        if stripped.startswith('playse'):
            # playse can have 1 or 3 args
            m = re.match(r'playse\s+([^,]+?)(?:\s*,\s*(\d+)\s*,\s*(\d+))?$', stripped)
            if m:
                sfx = m.group(1).strip()
                cmds.append(('playse', sfx))
                i += 1
                continue
        elif stripped.startswith('playfanfare'):
            m = re.match(r'playfanfare\s+(\S+)', stripped)
            if m:
                cmds.append(('playfanfare', m.group(1)))
                i += 1
                continue
        elif stripped.startswith('playbgm'):
            m = re.match(r'playbgm\s+([^,]+),\s*(\d+)', stripped)
            if m:
                cmds.append(('playbgm', m.group(1).strip(), int(m.group(2))))
                i += 1
                continue

        # ── Trainer battle (multi-arg) ───────────────────────────────
        elif stripped.startswith('trainerbattle'):
            # Handle variants: trainerbattle_single, trainerbattle_no_intro, etc.
            parts = stripped.split(None, 1)
            tb_cmd = parts[0]  # e.g. 'trainerbattle_single'
            args = parts[1] if len(parts) > 1 else ''
            cmds.append((tb_cmd, args))
            i += 1
            continue

        # ── Wild battle (setwildbattle + optional shiny + dowildbattle) ──
        elif stripped.startswith('setwildbattle'):
            m = re.match(r'setwildbattle\s+([^,]+),\s*(\d+)', stripped)
            if m:
                species = m.group(1).strip()
                level = int(m.group(2))
                shiny = False
                j = i + 1
                if j < len(lines) and lines[j].strip() == 'setflag FLAG_SHINY_SPECIAL':
                    shiny = True
                    j += 1
                if j < len(lines) and lines[j].strip().startswith('dowildbattle'):
                    cmds.append(('wildbattle', species, level, shiny))
                    i = j + 1
                    continue

        # ── Items (with optional quantity) ───────────────────────────
        elif stripped.startswith('additem'):
            args = stripped[len('additem'):].strip()
            cmds.append(('additem', args))
            i += 1
            continue
        elif stripped.startswith('removeitem'):
            args = stripped[len('removeitem'):].strip()
            cmds.append(('removeitem', args))
            i += 1
            continue
        elif stripped.startswith('checkitem '):
            args = stripped[len('checkitem'):].strip()
            cmds.append(('checkitem', args))
            i += 1
            continue
        elif stripped.startswith('checkitemspace'):
            args = stripped[len('checkitemspace'):].strip()
            cmds.append(('checkitemspace', args))
            i += 1
            continue

        # ── Pokemon ──────────────────────────────────────────────────
        elif stripped.startswith('givemon'):
            args = stripped[len('givemon'):].strip()
            cmds.append(('givemon', args))
            i += 1
            continue
        elif stripped.startswith('giveegg'):
            args = stripped[len('giveegg'):].strip()
            cmds.append(('giveegg', args))
            i += 1
            continue
        elif stripped.startswith('checkpartymove'):
            args = stripped[len('checkpartymove'):].strip()
            cmds.append(('checkpartymove', args))
            i += 1
            continue

        # ── Movement ─────────────────────────────────────────────────
        elif stripped.startswith('applymovement'):
            m = re.match(r'applymovement\s+([^,]+),\s*(\S+)', stripped)
            if m:
                cmds.append(('applymovement', m.group(1).strip(), m.group(2).strip()))
                i += 1
                continue
        elif stripped.startswith('applymovementat'):
            args = stripped[len('applymovementat'):].strip()
            cmds.append(('applymovementat', args))
            i += 1
            continue

        # ── NPC / Object control ─────────────────────────────────────
        elif stripped.startswith('removeobject'):
            args = stripped[len('removeobject'):].strip()
            cmds.append(('removeobject', args))
            i += 1
            continue
        elif stripped.startswith('addobject'):
            args = stripped[len('addobject'):].strip()
            cmds.append(('addobject', args))
            i += 1
            continue
        elif stripped.startswith('showobjectat'):
            args = stripped[len('showobjectat'):].strip()
            cmds.append(('showobjectat', args))
            i += 1
            continue
        elif stripped.startswith('hideobjectat'):
            args = stripped[len('hideobjectat'):].strip()
            cmds.append(('hideobjectat', args))
            i += 1
            continue
        elif stripped.startswith('turnobject'):
            args = stripped[len('turnobject'):].strip()
            cmds.append(('turnobject', args))
            i += 1
            continue
        elif stripped.startswith('setobjectxy '):
            args = stripped[len('setobjectxy'):].strip()
            cmds.append(('setobjectxy', args))
            i += 1
            continue
        elif stripped.startswith('setobjectxyperm'):
            args = stripped[len('setobjectxyperm'):].strip()
            cmds.append(('setobjectxyperm', args))
            i += 1
            continue

        # ── Flags & Variables ────────────────────────────────────────
        elif stripped.startswith('setflag'):
            args = stripped[len('setflag'):].strip()
            cmds.append(('setflag', args))
            i += 1
            continue
        elif stripped.startswith('clearflag'):
            args = stripped[len('clearflag'):].strip()
            cmds.append(('clearflag', args))
            i += 1
            continue
        elif stripped.startswith('checkflag'):
            args = stripped[len('checkflag'):].strip()
            cmds.append(('checkflag', args))
            i += 1
            continue
        elif stripped.startswith('setvar'):
            args = stripped[len('setvar'):].strip()
            cmds.append(('setvar', args))
            i += 1
            continue
        elif stripped.startswith('addvar'):
            args = stripped[len('addvar'):].strip()
            cmds.append(('addvar', args))
            i += 1
            continue
        elif stripped.startswith('subvar'):
            args = stripped[len('subvar'):].strip()
            cmds.append(('subvar', args))
            i += 1
            continue
        elif stripped.startswith('copyvar'):
            args = stripped[len('copyvar'):].strip()
            cmds.append(('copyvar', args))
            i += 1
            continue
        elif stripped.startswith('compare_var_to_value'):
            args = stripped[len('compare_var_to_value'):].strip()
            cmds.append(('compare_var_to_value', args))
            i += 1
            continue
        elif stripped.startswith('compare_var_to_var'):
            args = stripped[len('compare_var_to_var'):].strip()
            cmds.append(('compare_var_to_var', args))
            i += 1
            continue

        # ── Conditional branches (must be before generic goto/call) ──
        elif stripped.startswith('goto_if_eq '):
            m = re.match(r'goto_if_eq\s+([^,]+),\s*([^,]+),\s*(\S+)', stripped)
            if m:
                cmds.append(('goto_if_eq', m.group(1).strip(), m.group(2).strip(), m.group(3).strip()))
                i += 1; continue
        elif stripped.startswith('goto_if_ne '):
            m = re.match(r'goto_if_ne\s+([^,]+),\s*([^,]+),\s*(\S+)', stripped)
            if m:
                cmds.append(('goto_if_ne', m.group(1).strip(), m.group(2).strip(), m.group(3).strip()))
                i += 1; continue
        elif stripped.startswith('goto_if_lt '):
            m = re.match(r'goto_if_lt\s+([^,]+),\s*([^,]+),\s*(\S+)', stripped)
            if m:
                cmds.append(('goto_if_lt', m.group(1).strip(), m.group(2).strip(), m.group(3).strip()))
                i += 1; continue
        elif stripped.startswith('goto_if_ge '):
            m = re.match(r'goto_if_ge\s+([^,]+),\s*([^,]+),\s*(\S+)', stripped)
            if m:
                cmds.append(('goto_if_ge', m.group(1).strip(), m.group(2).strip(), m.group(3).strip()))
                i += 1; continue
        elif stripped.startswith('goto_if_le '):
            m = re.match(r'goto_if_le\s+([^,]+),\s*([^,]+),\s*(\S+)', stripped)
            if m:
                cmds.append(('goto_if_le', m.group(1).strip(), m.group(2).strip(), m.group(3).strip()))
                i += 1; continue
        elif stripped.startswith('goto_if_gt '):
            m = re.match(r'goto_if_gt\s+([^,]+),\s*([^,]+),\s*(\S+)', stripped)
            if m:
                cmds.append(('goto_if_gt', m.group(1).strip(), m.group(2).strip(), m.group(3).strip()))
                i += 1; continue
        elif stripped.startswith('goto_if_set '):
            m = re.match(r'goto_if_set\s+([^,]+),\s*(\S+)', stripped)
            if m:
                cmds.append(('goto_if_set', m.group(1).strip(), m.group(2).strip()))
                i += 1; continue
        elif stripped.startswith('goto_if_unset '):
            m = re.match(r'goto_if_unset\s+([^,]+),\s*(\S+)', stripped)
            if m:
                cmds.append(('goto_if_unset', m.group(1).strip(), m.group(2).strip()))
                i += 1; continue
        elif stripped.startswith('call_if_eq '):
            m = re.match(r'call_if_eq\s+([^,]+),\s*([^,]+),\s*(\S+)', stripped)
            if m:
                cmds.append(('call_if_eq', m.group(1).strip(), m.group(2).strip(), m.group(3).strip()))
                i += 1; continue
        elif stripped.startswith('call_if_ne '):
            m = re.match(r'call_if_ne\s+([^,]+),\s*([^,]+),\s*(\S+)', stripped)
            if m:
                cmds.append(('call_if_ne', m.group(1).strip(), m.group(2).strip(), m.group(3).strip()))
                i += 1; continue
        elif stripped.startswith('call_if_lt '):
            m = re.match(r'call_if_lt\s+([^,]+),\s*([^,]+),\s*(\S+)', stripped)
            if m:
                cmds.append(('call_if_lt', m.group(1).strip(), m.group(2).strip(), m.group(3).strip()))
                i += 1; continue
        elif stripped.startswith('call_if_ge '):
            m = re.match(r'call_if_ge\s+([^,]+),\s*([^,]+),\s*(\S+)', stripped)
            if m:
                cmds.append(('call_if_ge', m.group(1).strip(), m.group(2).strip(), m.group(3).strip()))
                i += 1; continue
        elif stripped.startswith('call_if_set '):
            m = re.match(r'call_if_set\s+([^,]+),\s*(\S+)', stripped)
            if m:
                cmds.append(('call_if_set', m.group(1).strip(), m.group(2).strip()))
                i += 1; continue
        elif stripped.startswith('call_if_unset '):
            m = re.match(r'call_if_unset\s+([^,]+),\s*(\S+)', stripped)
            if m:
                cmds.append(('call_if_unset', m.group(1).strip(), m.group(2).strip()))
                i += 1; continue

        # ── Flow control ─────────────────────────────────────────────
        elif stripped.startswith('goto ') or stripped == 'goto':
            args = stripped[len('goto'):].strip()
            cmds.append(('goto', args) if args else ('goto',))
            i += 1
            continue
        elif stripped.startswith('call ') or stripped == 'call':
            args = stripped[len('call'):].strip()
            cmds.append(('call', args) if args else ('call',))
            i += 1
            continue
        elif stripped.startswith('specialvar '):
            args = stripped[len('specialvar'):].strip()
            cmds.append(('specialvar', args))
            i += 1
            continue
        elif stripped.startswith('special'):
            args = stripped[len('special'):].strip()
            cmds.append(('special', args))
            i += 1
            continue

        # ── Weather ──────────────────────────────────────────────────
        elif stripped.startswith('setweather'):
            args = stripped[len('setweather'):].strip()
            cmds.append(('setweather', args))
            i += 1
            continue

        # ── Screen effects ───────────────────────────────────────────
        elif stripped.startswith('fadescreenspeed'):
            args = stripped[len('fadescreenspeed'):].strip()
            cmds.append(('fadescreenspeed', args))
            i += 1
            continue
        elif stripped.startswith('fadescreen'):
            args = stripped[len('fadescreen'):].strip()
            cmds.append(('fadescreen', args))
            i += 1
            continue
        elif stripped.startswith('setflashlevel'):
            args = stripped[len('setflashlevel'):].strip()
            cmds.append(('setflashlevel', args))
            i += 1
            continue

        # ── Timing ───────────────────────────────────────────────────
        elif stripped.startswith('delay'):
            args = stripped[len('delay'):].strip()
            cmds.append(('delay', args))
            i += 1
            continue

        # ── Money & Coins ────────────────────────────────────────────
        elif stripped.startswith('addmoney'):
            args = stripped[len('addmoney'):].strip()
            cmds.append(('addmoney', args))
            i += 1
            continue
        elif stripped.startswith('removemoney'):
            args = stripped[len('removemoney'):].strip()
            cmds.append(('removemoney', args))
            i += 1
            continue
        elif stripped.startswith('checkmoney'):
            args = stripped[len('checkmoney'):].strip()
            cmds.append(('checkmoney', args))
            i += 1
            continue
        elif stripped.startswith('addcoins'):
            args = stripped[len('addcoins'):].strip()
            cmds.append(('addcoins', args))
            i += 1
            continue
        elif stripped.startswith('removecoins'):
            args = stripped[len('removecoins'):].strip()
            cmds.append(('removecoins', args))
            i += 1
            continue

        # ── Buffers ──────────────────────────────────────────────────
        elif stripped.startswith('bufferspeciesname'):
            args = stripped[len('bufferspeciesname'):].strip()
            cmds.append(('bufferspeciesname', args))
            i += 1
            continue
        elif stripped.startswith('bufferitemname'):
            args = stripped[len('bufferitemname'):].strip()
            cmds.append(('bufferitemname', args))
            i += 1
            continue
        elif stripped.startswith('buffermovename'):
            args = stripped[len('buffermovename'):].strip()
            cmds.append(('buffermovename', args))
            i += 1
            continue
        elif stripped.startswith('buffernumberstring'):
            args = stripped[len('buffernumberstring'):].strip()
            cmds.append(('buffernumberstring', args))
            i += 1
            continue
        elif stripped.startswith('bufferstring'):
            args = stripped[len('bufferstring'):].strip()
            cmds.append(('bufferstring', args))
            i += 1
            continue

        # ── Respawn / misc ───────────────────────────────────────────
        elif stripped.startswith('setrespawn'):
            args = stripped[len('setrespawn'):].strip()
            cmds.append(('setrespawn', args))
            i += 1
            continue
        elif stripped.startswith('playmoncry'):
            args = stripped[len('playmoncry'):].strip()
            cmds.append(('playmoncry', args))
            i += 1
            continue

        # ── Doors ────────────────────────────────────────────────────
        elif stripped.startswith('opendoor'):
            args = stripped[len('opendoor'):].strip()
            cmds.append(('opendoor', args))
            i += 1
            continue
        elif stripped.startswith('closedoor'):
            args = stripped[len('closedoor'):].strip()
            cmds.append(('closedoor', args))
            i += 1
            continue
        elif stripped.startswith('waitdooranim'):
            cmds.append(('waitdooranim',))
            i += 1
            continue

        # ── Decorations ──────────────────────────────────────────────
        elif stripped.startswith('adddecoration'):
            args = stripped[len('adddecoration'):].strip()
            cmds.append(('adddecoration', args))
            i += 1
            continue
        elif stripped.startswith('removedecoration'):
            args = stripped[len('removedecoration'):].strip()
            cmds.append(('removedecoration', args))
            i += 1
            continue

        # ── Pokemart ─────────────────────────────────────────────────
        elif stripped.startswith('pokemart'):
            args = stripped[len('pokemart'):].strip()
            cmds.append(('pokemart', args))
            i += 1
            continue

        # ── Metatile ─────────────────────────────────────────────────
        elif stripped.startswith('setmetatile'):
            args = stripped[len('setmetatile'):].strip()
            cmds.append(('setmetatile', args))
            i += 1
            continue

        # ── Map setup commands ───────────────────────────────────────
        elif stripped.startswith('map_script ') or stripped.startswith('map_script_2 '):
            # These are map header script entries — pass through as generic
            parts_m = stripped.split(None, 1)
            cmds.append((parts_m[0], parts_m[1] if len(parts_m) > 1 else ''))
            i += 1
            continue
        elif stripped.startswith('.equ ') or stripped.startswith('.byte ') or stripped.startswith('.2byte '):
            # Assembler directives — pass through as generic
            parts_m = stripped.split(None, 1)
            cmds.append((parts_m[0], parts_m[1] if len(parts_m) > 1 else ''))
            i += 1
            continue

        # ── Text display ─────────────────────────────────────────────
        elif stripped.startswith('message '):
            m = re.match(r'message\s+(\S+)', stripped)
            if m:
                label = m.group(1)
                cmds.append(('message', label, texts.get(label, ''), ''))
                i += 1
                continue
        elif stripped.startswith('textcolor '):
            args = stripped[len('textcolor'):].strip()
            cmds.append(('textcolor', args))
            i += 1
            continue

        # ── Special variants ─────────────────────────────────────────
        elif stripped.startswith('famechecker '):
            args = stripped[len('famechecker'):].strip()
            cmds.append(('famechecker', args))
            i += 1
            continue

        # ── Object control (extended) ────────────────────────────────
        elif stripped.startswith('copyobjectxytoperm '):
            args = stripped[len('copyobjectxytoperm'):].strip()
            cmds.append(('copyobjectxytoperm', args))
            i += 1
            continue
        elif stripped.startswith('setobjectmovementtype '):
            m = re.match(r'setobjectmovementtype\s+([^,]+),\s*(\S+)', stripped)
            if m:
                cmds.append(('setobjectmovementtype', m.group(1).strip(), m.group(2).strip()))
                i += 1
                continue
        elif stripped.startswith('setworldmapflag '):
            args = stripped[len('setworldmapflag'):].strip()
            cmds.append(('setworldmapflag', args))
            i += 1
            continue

        # ── No-arg commands ──────────────────────────────────────────
        elif stripped in {
            'lock', 'lockall', 'faceplayer', 'release', 'releaseall',
            'end', 'return', 'waitmessage', 'closemessage', 'waitse',
            'waitfanfare', 'waitmoncry', 'waitmovement', 'waitbuttonpress',
            'waitstate', 'doweather', 'resetweather', 'nop', 'nop1',
            'getpartysize', 'checkplayergender', 'dowildbattle',
            'showmoneybox', 'hidemoneybox', 'updatemoneybox',
            'showcoinsbox', 'hidecoinsbox', 'updatecoinsbox',
            'signmsg', 'normalmsg',
        }:
            cmds.append((stripped,))
            i += 1
            continue

        # ── Generic fallback ─────────────────────────────────────────
        parts = stripped.split(None, 1)
        cmd = parts[0]
        args = parts[1] if len(parts) > 1 else ''
        cmds.append((cmd, args) if args else (cmd,))
        i += 1
    return cmds


def parse_script_pages(map_dir: Path, texts: OrderedDict | None = None) -> tuple[dict[str, list[dict]], dict[str, str]]:
    """Return event pages parsed from scripts.inc.

    If *texts* is provided it is used for label→text lookups; otherwise the
    map-local ``text.inc`` is loaded automatically.
    """
    pages: dict[str, list[dict]] = {}
    types: dict[str, str] = {}
    scripts_inc = map_dir / 'scripts.inc'
    if not scripts_inc.exists():
        return pages, types

    if texts is None:
        texts = parse_text_inc(map_dir / 'text.inc')
    label_re = re.compile(r'^([A-Za-z0-9_]+)::')
    script_lines: dict[str, list[str]] = {}
    current = None

    with scripts_inc.open(encoding='utf-8') as fh:
        lines = [ln.rstrip('\n') for ln in fh]

    for line in lines:
        m = label_re.match(line.strip())
        if m:
            current = m.group(1)
            script_lines.setdefault(current, [])
            continue
        if current is not None:
            script_lines[current].append(line)

    def page_from_lines(plines: list[str], condition: dict | None = None) -> dict:
        cmds = _parse_script_lines(plines, texts)
        fl, vr = _constants_in_lines(plines)
        if condition:
            cond_lines = []
            for key in ('switch', 'switch2', 'var'):
                val = condition.get(key)
                if val:
                    cond_lines.append(val)
            cf, cv = _constants_in_lines(cond_lines)
            fl.update(cf)
            vr.update(cv)
        return {
            'commands': cmds,
            'flag_options': sorted(fl),
            'var_options': sorted(vr),
            'conditions': condition or {},
        }

    def split_if_lines(iflines: list[str]) -> list[dict]:
        plist: list[dict] = []
        current_lines: list[str] = []
        condition: list[str] | None = None
        for ln in iflines:
            stripped = ln.strip()
            if stripped.startswith('.if'):
                if current_lines:
                    plist.append(page_from_lines(current_lines, condition=None))
                    current_lines = []
                condition = [stripped[3:].strip()]
                continue
            elif stripped.startswith('.else'):
                info = page_from_lines(current_lines, condition=None)
                fl, vr = _constants_in_lines(condition or [])
                info['flag_options'] = sorted(set(info['flag_options']).union(fl))
                info['var_options'] = sorted(set(info['var_options']).union(vr))
                plist.append(info)
                current_lines = []
                continue
            elif stripped.startswith('.endif'):
                info = page_from_lines(current_lines, condition=None)
                fl, vr = _constants_in_lines(condition or [])
                info['flag_options'] = sorted(set(info['flag_options']).union(fl))
                info['var_options'] = sorted(set(info['var_options']).union(vr))
                plist.append(info)
                current_lines = []
                condition = None
                continue
            current_lines.append(ln)
        if current_lines and any(ln.strip() for ln in current_lines):
            plist.append(page_from_lines(current_lines, condition=None))
        return plist

    base_labels = {
        re.sub(r'_Page\d+$', '', lbl)
        for lbl in script_lines
    }

    for base in base_labels:
        base_lines = script_lines.get(base, [])
        has_if = any(ln.strip().startswith('.if') for ln in base_lines)
        if has_if:
            types[base] = 'if'
            pages[base] = split_if_lines(base_lines)
            continue

        page_list: list[dict] = []
        n = 1
        while True:
            label = base if n == 1 else f"{base}_Page{n}"
            if label not in script_lines:
                break
            page_list.append(page_from_lines(script_lines[label]))
            n += 1

        if page_list:
            types[base] = 'legacy' if n > 2 else 'single'
            pages[base] = page_list
            continue

        if base_lines:
            pages[base] = [page_from_lines(base_lines)]
        else:
            pages[base] = [page_from_lines([])]
        types[base] = 'single'

    return pages, types


# ═════════════════════════════════════════════════════════════════════════════
# Script writing — command tuples back to .inc lines
# ═════════════════════════════════════════════════════════════════════════════

def lines_from_commands(
    commands: list[tuple],
    texts: dict,
    conditions: dict | None = None,
    hidden: list[tuple[int, str]] | None = None,
) -> list[str]:
    """Convert a list of command tuples back to script lines.

    *conditions*: page condition dict — inserts checkflag / compare_var_to_value.
    *hidden*: non-visible lines from the original script, scanned to avoid
    duplicating auto-inserted lock/release.
    *texts*: mutable dict of label->text; message commands with labels update it.
    """
    visible: list[str] = []

    if conditions:
        sw = conditions.get('switch')
        if sw:
            visible.append(f'checkflag {sw}\n')
        sw2 = conditions.get('switch2')
        if sw2:
            visible.append(f'checkflag {sw2}\n')
        var = conditions.get('var')
        if var:
            value = conditions.get('var_value', '0')
            visible.append(f'compare_var_to_value {var}, {value}\n')

    # Build set of commands already present (both visible and hidden) to
    # avoid injecting duplicates of lock/release/end.
    cmd_names: set[str] = set()
    for data in commands:
        if data:
            cmd_names.add(data[0])
    if hidden:
        for _, line in hidden:
            token = line.strip().split()
            if token:
                cmd_names.add(token[0])

    # Don't inject lock into sub-scripts (those ending with 'return')
    # or scripts that already have lock/lockall.
    needs_lock = (
        'lock' not in cmd_names and 'lockall' not in cmd_names and
        'return' not in cmd_names and
        any(cmd and cmd[0] in {'applymovement', 'call', 'wildbattle'}
            for cmd in commands)
    )

    if needs_lock:
        visible.append('lock\n')

    for data in commands:
        if not data:
            continue
        cmd = data[0]

        # ── Message ──────────────────────────────────────────────────
        if cmd == 'message':
            label_name = data[1] if len(data) > 1 else None
            text = data[2] if len(data) > 2 else ''
            msg_type = data[3] if len(data) > 3 else ''
            if label_name:
                line = f'msgbox {label_name}'
                if msg_type:
                    line += f', {msg_type}'
                visible.append(line + '\n')
                texts[label_name] = text
            else:
                escaped = text.replace('"', '\\"')
                line = f'msgbox "{escaped}"'
                if msg_type:
                    line += f', {msg_type}'
                visible.append(line + '\n')

        # ── Warps (all have map, x, y as positional args) ────────────
        elif cmd in ('warp', 'warpsilent', 'warpdoor', 'warphole', 'warpteleport'):
            if len(data) >= 4:
                visible.append(f'{cmd} {data[1]}, {data[2]}, {data[3]}\n')
            else:
                args = data[1] if len(data) > 1 else ''
                visible.append(f'{cmd} {args}\n')

        # ── Movement (target, label as positional) ───────────────────
        elif cmd == 'applymovement':
            if len(data) >= 3:
                visible.append(f'applymovement {data[1]}, {data[2]}\n')
            else:
                args = data[1] if len(data) > 1 else ''
                visible.append(f'applymovement {args}\n')

        # ── Sound (simplified — just the constant name) ──────────────
        elif cmd == 'playse':
            visible.append(f'playse {data[1]}\n')
        elif cmd == 'playfanfare':
            visible.append(f'playfanfare {data[1]}\n')
        elif cmd == 'playbgm':
            if len(data) >= 3:
                visible.append(f'playbgm {data[1]}, {data[2]}\n')
            else:
                visible.append(f'playbgm {data[1]}\n')

        # ── Wild battle (composite: setwildbattle + optional shiny + dowildbattle)
        elif cmd == 'wildbattle':
            visible.append(f'setwildbattle {data[1]}, {data[2]}\n')
            if len(data) > 3 and data[3]:
                visible.append('setflag FLAG_SHINY_SPECIAL\n')
            visible.append('dowildbattle\n')

        # ── Pokemart — just the label reference ────────────────────────
        elif cmd == 'pokemart':
            label = data[1] if len(data) > 1 else ''
            visible.append(f'pokemart {label}\n')

        # ── Conditional branches (positional args) ───────────────────
        elif cmd in ('goto_if_eq', 'goto_if_ne', 'goto_if_lt', 'goto_if_ge',
                     'goto_if_le', 'goto_if_gt',
                     'call_if_eq', 'call_if_ne', 'call_if_lt', 'call_if_ge'):
            # (cmd, var, value, label)
            if len(data) >= 4:
                visible.append(f'{cmd} {data[1]}, {data[2]}, {data[3]}\n')
            else:
                args = data[1] if len(data) > 1 else ''
                visible.append(f'{cmd} {args}\n')
        elif cmd in ('goto_if_set', 'goto_if_unset',
                     'call_if_set', 'call_if_unset'):
            # (cmd, flag, label)
            if len(data) >= 3:
                visible.append(f'{cmd} {data[1]}, {data[2]}\n')
            else:
                args = data[1] if len(data) > 1 else ''
                visible.append(f'{cmd} {args}\n')

        # ── Object control with positional args ─────────────────────
        elif cmd == 'setobjectmovementtype' and len(data) >= 3:
            visible.append(f'{cmd} {data[1]}, {data[2]}\n')

        # ── Everything else: single args string in data[1] ───────────
        else:
            args = data[1] if len(data) > 1 else ''
            line = cmd if not args else f'{cmd} {args}'
            visible.append(line + '\n')

    needs_release = (
        needs_lock and
        'release' not in cmd_names and 'releaseall' not in cmd_names
    )

    if needs_release:
        visible.append('release\n')

    if not commands and 'nop' not in cmd_names and 'nop1' not in cmd_names:
        visible.append('nop\n')

    return visible


def merge_hidden_lines(visible: list[str], hidden: list[tuple[int, str]]) -> list[str]:
    """Insert hidden script lines back into a list of visible lines.

    *hidden* is a list of (position_index, line) tuples where position_index
    indicates where the hidden line appeared relative to visible lines.
    """
    lines: list[str] = []
    hidden = sorted(hidden, key=lambda x: x[0])
    vi = hi = 0
    while vi < len(visible):
        while hi < len(hidden) and hidden[hi][0] == vi:
            line = hidden[hi][1]
            if not line.endswith('\n'):
                line += '\n'
            lines.append(line)
            hi += 1
        lines.append(visible[vi])
        vi += 1
    while hi < len(hidden):
        line = hidden[hi][1]
        if not line.endswith('\n'):
            line += '\n'
        lines.append(line)
        hi += 1
    return lines


def _render_label_block(
    label: str,
    data,
    hidden_lines: dict[str, list],
    texts: dict,
) -> list[str]:
    """Render a single label's script lines from command data."""
    out: list[str] = []

    if data and isinstance(data, list) and data and isinstance(data[0], dict):
        # Legacy format: list of page dicts
        for i, page in enumerate(data):
            page_label = label if i == 0 else f'{label}_Page{i + 1}'
            out.append(f'{page_label}::\n')
            cmd_lines = lines_from_commands(
                page.get('commands', []),
                texts,
                page.get('conditions'),
                page.get('hidden_lines') or hidden_lines.get(page_label, []),
            )
            merged = merge_hidden_lines(
                cmd_lines,
                page.get('hidden_lines') or hidden_lines.get(page_label, []),
            )
            out.extend(merged)
            # Only append end if the commands don't already end with
            # end/return/releaseall
            last_cmd = _last_nonblank(merged)
            if last_cmd not in ('end', 'return', 'releaseall', 'step_end'):
                out.append('end\n')
            out.append('\n')
    else:
        # New format: flat list of command tuples
        commands = data if isinstance(data, list) else []
        out.append(f'{label}::\n')
        cmd_lines = lines_from_commands(
            commands, texts, None, hidden_lines.get(label, []),
        )
        merged = merge_hidden_lines(
            cmd_lines, hidden_lines.get(label, []),
        )
        out.extend(merged)
        last_cmd = _last_nonblank(merged)
        if last_cmd not in ('end', 'return', 'releaseall', 'step_end'):
            out.append('end\n')
        out.append('\n')

    return out


def _last_nonblank(lines: list[str]) -> str:
    """Return the stripped last non-blank line, or empty string."""
    for line in reversed(lines):
        s = line.strip()
        if s:
            return s
    return ''


def write_scripts_inc(
    pages,
    hidden_lines: dict[str, list],
    texts: dict,
    scripts_inc_path: Path,
) -> None:
    """Write all scripts back to scripts.inc.

    Preserves any content in the existing file that isn't part of the
    labels being written (movement data, macros, .equ directives, etc.).
    Only replaces the label blocks that are in *pages*.

    *pages*: Can be either:
      - **New format**: ``{label: [commands_list]}`` — one label per entry,
        commands as a flat list of tuples.
      - **Legacy format**: ``{base_label: [page_dicts]}`` — base label with
        list of page dicts containing 'commands' key.
    *hidden_lines*: label -> list of (index, line) hidden line tuples
    *texts*: label -> text string dict (mutated by lines_from_commands)
    """
    label_re = re.compile(r'^([A-Za-z0-9_]+)::')

    # Read the existing file so we can preserve non-event content
    existing_lines: list[str] = []
    if scripts_inc_path.exists():
        with scripts_inc_path.open(encoding='utf-8') as fh:
            existing_lines = fh.readlines()

    if not existing_lines:
        # No existing file — write everything from scratch
        out_lines: list[str] = []
        for label, data in pages.items():
            out_lines.extend(
                _render_label_block(label, data, hidden_lines, texts)
            )
        with scripts_inc_path.open('w', encoding='utf-8', newline='\n') as fh:
            fh.writelines(out_lines)
        return

    # Parse existing file into label blocks.
    # Each block is (label_or_None, [lines]).
    # label=None means pre-label content or content between labels that
    # doesn't belong to an event script we're managing.
    blocks: list[tuple[str | None, list[str]]] = []
    current_label: str | None = None
    current_lines: list[str] = []

    for line in existing_lines:
        m = label_re.match(line.strip())
        if m:
            # Save previous block
            if current_lines or current_label is not None:
                blocks.append((current_label, current_lines))
            current_label = m.group(1)
            current_lines = [line]
        else:
            current_lines.append(line)
    # Save last block
    if current_lines or current_label is not None:
        blocks.append((current_label, current_lines))

    # Build the output: for labels in pages, replace with rendered
    # version.  For everything else, keep the original lines.
    written_labels: set[str] = set()
    out_lines = []

    for block_label, block_lines in blocks:
        if block_label and block_label in pages:
            rendered = _render_label_block(
                block_label, pages[block_label], hidden_lines, texts,
            )
            out_lines.extend(rendered)
            written_labels.add(block_label)
        else:
            # Keep original content (movement data, macros, non-event
            # scripts, .equ directives, etc.)
            out_lines.extend(block_lines)

    # Append any new labels from pages that weren't in the original file
    for label, data in pages.items():
        if label not in written_labels:
            out_lines.append('\n')
            out_lines.extend(
                _render_label_block(label, data, hidden_lines, texts)
            )

    with scripts_inc_path.open('w', encoding='utf-8', newline='\n') as fh:
        fh.writelines(out_lines)
