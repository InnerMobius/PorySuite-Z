"""
EVENTide utilities — script parsing, constant loading, friendly labels.

Ported from the standalone eventide_utils.py. All paths are relative to a
project root directory passed as a parameter, NOT derived from __file__.

Module-level constants (FRIENDLY_COMMANDS, etc.) are initialized empty and
populated when load_project_constants(root_dir) is called.
"""

import hashlib
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

def parse_script_snippet(text: str, texts: dict | None = None) -> list[tuple]:
    """Parse a loose chunk of pasted script text into command tuples.

    This is for the Paste button: the user copies a few lines out of a wiki, a
    reference `.inc`, or another map and drops them straight into the command
    list. Unlike :func:`parse_raw_script_text`, which is the whole-script
    editor's parser and needs well-formed ``Label::`` blocks, this accepts
    whatever was on the clipboard:

    * bare command lines with no label at all (the common case);
    * a leading ``Label::`` line, which is dropped — pasting INTO a page means
      the page already has its own label;
    * further ``Label::`` lines, kept as `_label_marker` so a multi-block
      paste still splits into sub-labels on save, the same as a loaded script;
    * blank lines and ``@`` comments, which are skipped;
    * leading indentation, which is irrelevant.

    *texts* is the map's label->string store, so a pasted ``msgbox Some_Text``
    shows its real wording in the command list instead of "(empty)".

    Returns [] when nothing parsed, so a caller can fall back to its own
    clipboard rather than inserting garbage. Never raises — the worst a
    nonsense paste produces is a generic ``(word, rest)`` command, which is
    what the file parser would have made of the same line.
    """
    if not text or not text.strip():
        return []
    lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')

    out: list[tuple] = []
    pending: list[str] = []
    seen_label = False

    def _flush():
        if pending:
            out.extend(_parse_script_lines(pending, texts or {}))
            pending.clear()

    label_re = re.compile(r'^([A-Za-z0-9_]+)::')
    for raw in lines:
        stripped = raw.strip()
        m = label_re.match(stripped)
        if m:
            _flush()
            # The FIRST label is the snippet's own heading and is redundant —
            # it is being pasted into a page that already has a label. Later
            # ones are real sub-labels worth keeping.
            if seen_label:
                out.append(('_label_marker', m.group(1)))
            seen_label = True
            continue
        if not stripped or stripped.startswith('@'):
            continue
        # Drop a trailing inline comment; the command parser would otherwise
        # fold it into the arguments. Scan for an `@` OUTSIDE quotes —
        # `msgbox "mail me @ home"` is text, not a comment.
        code, in_quote = stripped, False
        for i, ch in enumerate(stripped):
            if ch == '"':
                in_quote = not in_quote
            elif ch == '@' and not in_quote:
                code = stripped[:i].rstrip()
                break
        if code:
            pending.append(code)
    _flush()
    return out


def file_fingerprint(path: Path) -> str | None:
    """Return a content hash for *path*, or None if it does not exist.

    Content, not mtime+size: a same-size edit landing inside one filesystem
    timestamp tick is exactly the case a staleness check must not miss, and
    these files are a few KB each so hashing them is free. ``None`` is a real
    state meaning "absent", so a file that is deleted or created under the
    editor compares as changed.
    """
    try:
        return hashlib.sha1(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None
    except Exception:
        # Unreadable is not the same as unchanged — report a value that can
        # never match a real hash, so the caller treats it as changed.
        return 'unreadable'


def parse_text_inc(path: Path) -> OrderedDict:
    """Return an OrderedDict mapping labels to message strings.

    Recognises both ``.string "..."`` (regular dialogue) and
    ``.braille "..."`` (braille). Braille labels are stored with a
    private ``__BRAILLE__`` prefix on the value so consumers can tell
    them apart without a parallel data structure. The prefix is
    stripped by ``write_text_inc`` before emitting the corresponding
    directive — it never appears in the output bytes.
    """
    texts = OrderedDict()
    if not path or not path.exists():
        return texts
    label_re = re.compile(r"^([A-Za-z0-9_]+)::")
    string_re = re.compile(r"\.string\s+\"((?:\\.|[^\"])*)\"")
    braille_re = re.compile(r"\.braille\s+\"((?:\\.|[^\"])*)\"")
    current = None
    buf: list[str] = []
    is_braille = False
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            m = label_re.match(line.strip())
            if m:
                if current is not None:
                    val = "".join(buf)
                    if is_braille:
                        val = _BRAILLE_PREFIX + val
                    texts[current] = val
                current = m.group(1)
                buf = []
                is_braille = False
                continue
            if current is None:
                continue
            m = braille_re.search(line)
            if m:
                segment = m.group(1)
                segment = segment.replace("\\n", "\n").replace("\\p", "\n\n")
                buf.append(segment)
                is_braille = True
                continue
            m = string_re.search(line)
            if m:
                segment = m.group(1)
                segment = segment.replace("\\n", "\n").replace("\\p", "\n\n")
                buf.append(segment)
    if current is not None:
        val = "".join(buf)
        if is_braille:
            val = _BRAILLE_PREFIX + val
        texts[current] = val
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


_BRAILLE_PREFIX = '__BRAILLE__'


def _strip_braille_prefix(text: str) -> str:
    """Remove the private braille marker before exposing the value to UI."""
    if text.startswith(_BRAILLE_PREFIX):
        return text[len(_BRAILLE_PREFIX):]
    return text


def write_text_inc(texts: dict, path: Path):
    """Write the label->text mapping back to text.inc.

    Texts whose value carries the private ``__BRAILLE__`` prefix are
    emitted with the ``.braille`` directive instead of ``.string``.
    The prefix is stripped before writing so it doesn't end up in the
    compiled bytes.
    """
    lines = []
    for label, text in texts.items():
        if text.startswith(_BRAILLE_PREFIX):
            actual = text[len(_BRAILLE_PREFIX):]
            escaped = actual.replace("\n\n", "\\p").replace("\n", "\\n")
            escaped = escaped.replace('"', '\\"')
            lines.append(f"{label}::\n")
            lines.append(f"    .braille \"{escaped}\"\n\n")
        else:
            escaped = text.replace("\n\n", "\\p").replace("\n", "\\n")
            escaped = escaped.replace('"', '\\"')
            lines.append(f"{label}::\n")
            # Emit ONE .string per display line (split after each \n / \p), to
            # match pokefirered's own formatting. Writing the whole block as a
            # single collapsed .string reformatted EVERY label, so a one-label
            # edit churned the entire map's text.inc in git (spurious diffs).
            segs = re.findall(r'.*?\\[npl]|.+', escaped) or [escaped]
            for seg in segs:
                lines.append(f"    .string \"{seg}\"\n")
            lines.append("\n")
    with path.open('w', encoding='utf-8', newline='\n') as fh:
        fh.writelines(lines)


# ── Map "scene" scripts: per-page NPC appearance ⇄ On Transition ────────────
# An NPC whose look/behaviour changes with story progress (graphic, movement
# type, position per state) is driven by the map's On Transition script, which
# runs on entering the map and `call_if_*`s a small "SetX" script per state.
# These two functions turn per-page appearance state into that On Transition
# structure and back, so the event editor can present it as RPG-Maker-style
# pages while EVENTide writes/reads the map script for the user. Verified to
# reproduce the vanilla ViridianCity Old Man exactly and round-trip.

_GOTO_TO_CALL = {
    'goto_if_set': 'call_if_set', 'goto_if_unset': 'call_if_unset',
    'goto_if_eq': 'call_if_eq', 'goto_if_ne': 'call_if_ne',
    'goto_if_lt': 'call_if_lt', 'goto_if_ge': 'call_if_ge',
    'goto_if_le': 'call_if_le', 'goto_if_gt': 'call_if_gt',
}
_CALL_TO_GOTO = {v: k for k, v in _GOTO_TO_CALL.items()}


def build_scene_scripts(map_name: str, states: list, preamble: list | None = None):
    """Generate the ``<Map>_OnTransition`` script + one ``SetX`` script per
    state from a list of per-NPC appearance states.

    Each *state* is a dict: ``{'suffix', 'localid', 'condition': (goto_if_*,
    args...), 'movement'?, 'x'?, 'y'?, 'gfx'?}``. *preamble* is any non-scene
    On Transition content to keep (e.g. ``setworldmapflag``). Returns
    ``(scripts, on_transition_label)`` where *scripts* maps label → command
    tuples in the generic ``(cmd, argstr)`` form the writer emits."""
    out: dict = {}
    ot = list(preamble or [])
    for s in states:
        set_label = f'{map_name}_EventScript_Set{s["suffix"]}'
        cond = tuple(s['condition'])
        call = _GOTO_TO_CALL.get(cond[0], 'call_if_set')
        cond_args = ', '.join(str(a) for a in cond[1:])
        ot.append((call, f'{cond_args}, {set_label}'))
        body: list = []
        if s.get('gfx'):
            body.append(('setvar', f'VAR_OBJ_GFX_ID_0, {s["gfx"]}'))
        if s.get('movement'):
            body.append(('setobjectmovementtype',
                         f'{s["localid"]}, {s["movement"]}'))
        if s.get('x') is not None and s.get('y') is not None:
            body.append(('setobjectxyperm',
                         f'{s["localid"]}, {s["x"]}, {s["y"]}'))
        body.append(('return',))
        out[set_label] = body
    ot.append(('end',))
    out[f'{map_name}_OnTransition'] = ot
    return out, f'{map_name}_OnTransition'


def _scene_argparts(ct: tuple) -> list:
    """Flatten a command tuple's arguments to a list of strings, regardless of
    whether the args are one comma-joined string ``(cmd, 'a, b, c')`` — the form
    :func:`build_scene_scripts` emits — or separate tuple elements
    ``(cmd, 'a', 'b', 'c')`` — the form :func:`parse_scripts_inc` produces."""
    raw = ', '.join(str(a) for a in ct[1:])
    return [x.strip() for x in raw.split(',') if x.strip() != '']


def parse_scene_scripts(map_name: str, scripts: dict):
    """Inverse of :func:`build_scene_scripts`. Reads ``<Map>_OnTransition`` and
    its ``SetX`` targets back into ``(states, preamble)`` — so an existing
    scene NPC (e.g. the Old Man) folds into per-page appearance states, and any
    non-scene On Transition command (setworldmapflag, …) is preserved.

    Handles both command-tuple shapes (see :func:`_scene_argparts`)."""
    ot = scripts.get(f'{map_name}_OnTransition', [])
    states: list = []
    preamble: list = []
    for ct in ot:
        if not ct:
            continue
        if ct[0] in _CALL_TO_GOTO and len(ct) > 1:
            a = _scene_argparts(ct)
            if not a:
                continue
            set_label = a[-1]
            cond = (_CALL_TO_GOTO[ct[0]],) + tuple(a[:-1])
            st: dict = {
                'suffix': set_label.split('_EventScript_Set')[-1],
                'condition': cond, 'set_label': set_label}
            for b in scripts.get(set_label, []):
                if not b:
                    continue
                if b[0] == 'setobjectmovementtype':
                    p = _scene_argparts(b)
                    if p:
                        st['localid'] = p[0]
                    if len(p) > 1:
                        st['movement'] = p[1]
                elif b[0] == 'setobjectxyperm':
                    p = _scene_argparts(b)
                    if p:
                        st['localid'] = p[0]
                    if len(p) > 2:
                        st['x'] = int(p[1])
                        st['y'] = int(p[2])
                elif b[0] == 'setvar':
                    p = _scene_argparts(b)
                    if len(p) > 1 and 'VAR_OBJ_GFX_ID_0' in p[0]:
                        st['gfx'] = p[1]
            states.append(st)
        elif ct[0] != 'end':
            preamble.append(ct)
    return states, preamble


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


# ── Multi-condition page support ──────────────────────────────────────────
# A condition-page can require SEVERAL flag/var checks that must ALL be true
# (logical AND), like RPG Maker's page conditions. A single condition keeps the
# vanilla positive-jump form (no churn on existing single-flag scripts); 2+
# conditions lower to a guard block that jumps to a skip label if ANY check
# fails, then falls into the page body only when every check passed.
#
# A "condition" is a target-less positive check tuple, reusing the command
# vocab so _condition_text and the pickers work unchanged:
#     ('goto_if_set',  FLAG)                — flag must be ON
#     ('goto_if_unset', FLAG)               — flag must be OFF
#     ('goto_if_eq', VAR, VALUE) (or ne/lt/ge/le/gt)  — variable comparison

_NEGATE_GOTO = {
    'goto_if_set': 'goto_if_unset', 'goto_if_unset': 'goto_if_set',
    'goto_if_eq': 'goto_if_ne', 'goto_if_ne': 'goto_if_eq',
    'goto_if_lt': 'goto_if_ge', 'goto_if_ge': 'goto_if_lt',
    'goto_if_le': 'goto_if_gt', 'goto_if_gt': 'goto_if_le',
}


def _cond_with_target(cond: tuple, target: str) -> tuple:
    """Append *target* to a target-less condition tuple → a real goto tuple."""
    return tuple(cond) + (target,)


def _cond_without_target(goto_tuple: tuple) -> tuple:
    """Strip the trailing target label from a goto_if_* tuple → a condition."""
    return tuple(goto_tuple[:-1])


def _negate_cond(cond: tuple) -> tuple:
    """Return the logical negation of a condition (for guard-block jumps)."""
    return (_NEGATE_GOTO.get(cond[0], cond[0]),) + tuple(cond[1:])


def lower_conditions_to_gotos(conditions: list, page_label: str,
                              skip_label: str) -> list:
    """Turn a page's AND-conditions into the command tuples that select it.

    * 1 condition  → ``[positive_goto → page_label]`` (vanilla form, no churn).
    * 2+ conditions → guard block: negated check → *skip_label* for each, then
      ``goto page_label``, then an inline ``skip_label::`` marker so the next
      page's checks / the default body continue there.
    """
    conditions = [tuple(c) for c in conditions if c]
    if not conditions:
        return []
    if len(conditions) == 1:
        return [_cond_with_target(conditions[0], page_label)]
    out = [_cond_with_target(_negate_cond(c), skip_label) for c in conditions]
    out.append(('goto', page_label))
    out.append(('_label_marker', skip_label))
    return out


_CONDITION_GOTOS = frozenset(_NEGATE_GOTO.keys())


def gather_condition_selectors(entry_label: str, scripts: dict,
                               preamble_cmds: frozenset):
    """Chain-walk a script's leading condition checks, following the skip labels
    that the save-split turns into separate blocks, so multi-condition guard
    blocks reassemble correctly on load.

    Returns ``(preamble, selectors, default_label, default_body, consumed,
    inline)``:
      * *selectors* — flat list of ``{conditions, page_label, ...}`` per page.
      * *default_label* — label whose block holds the fall-through body.
      * *default_body* — the fall-through command list.
      * *consumed* — set of skip-label blocks absorbed by the walk (the caller
        must not also present them as their own pages).
      * *inline* — the inline hand-written selector if one was folded, else None.
    """
    preamble_out: list = []
    selectors_out: list = []
    consumed: set = set()
    cur_label = entry_label
    cur = list(scripts.get(entry_label, []))
    first = True
    guard = 0  # safety bound against pathological loops
    while guard < 4096:
        guard += 1
        pre, sels, bstart = lift_leading_conditions(cur, preamble_cmds)
        if first:
            preamble_out = pre
            first = False
        inl = next((s for s in sels if s.get('inline')), None)
        selectors_out.extend([s for s in sels if not s.get('inline')])
        if inl is not None:
            return (preamble_out, selectors_out, cur_label,
                    list(cur[bstart:]), consumed, inl)
        cont = None
        if selectors_out and bstart >= len(cur):
            sk = selectors_out[-1].get('skip_label')
            if sk and sk in scripts and sk not in consumed and sk != cur_label:
                cont = sk
        if cont:
            consumed.add(cont)
            cur_label = cont
            cur = list(scripts.get(cont, []))
            continue
        return (preamble_out, selectors_out, cur_label,
                list(cur[bstart:]), consumed, None)
    return preamble_out, selectors_out, cur_label, [], consumed, None


def lift_leading_conditions(cmds: list, preamble_cmds: frozenset):
    """Walk a script's leading checks and group them into page selectors.

    Recognises three shapes and returns ``(preamble, selectors, body_start)``:

    * **single positive jump** — ``goto_if_set FLAG, Label`` → one selector with
      one condition, body under ``Label`` (unchanged vanilla behavior).
    * **EVENTide guard block** — negated checks all jumping to a shared skip
      label, then ``goto PageLabel``, then that ``skip_label::`` inline → one
      selector with the checks re-negated to their required (positive) form,
      body under ``PageLabel``.
    * **hand-written guard chain** — 2+ consecutive same-target checks followed
      by an INLINE body (no ``goto``) → one selector requiring the negated
      checks, whose body is the inline entry body and whose fallback is the
      shared target. ``page_label=None`` marks the inline-body case.

    Each selector is ``{'conditions': [...], 'page_label': str|None,
    'skip_label': str|None, 'inline': bool}``. ``body_start`` is the index in
    *cmds* where the default/inline body begins.
    """
    def _last(t):
        return t[-1] if t and len(t) > 1 else None

    preamble: list = []
    selectors: list = []
    i, n = 0, len(cmds)
    while i < n:
        c = cmds[i]
        if not c:
            i += 1
            continue
        cmd = c[0]
        if cmd in preamble_cmds:
            preamble.append(c)
            i += 1
            continue
        if cmd not in _CONDITION_GOTOS:
            break  # first real body command

        target = _last(c)
        run = [c]
        j = i + 1
        while (j < n and cmds[j] and cmds[j][0] in _CONDITION_GOTOS
               and _last(cmds[j]) == target):
            run.append(cmds[j])
            j += 1

        # Guard block: 2+ checks that SHARE a target (the skip label), followed
        # by `goto PageLabel`. The checks are negated back to their required
        # (positive) form. Covers both the pre-split form (an inline
        # `_label_marker skip` follows) and the reloaded form (skip is a
        # separate block reached by fall-through, no marker).
        if (len(run) >= 2 and j < n and cmds[j] and cmds[j][0] == 'goto'
                and len(cmds[j]) > 1):
            page_label = cmds[j][1]
            conditions = [_negate_cond(_cond_without_target(g)) for g in run]
            selectors.append({'conditions': conditions,
                              'page_label': page_label,
                              'skip_label': target, 'inline': False})
            i = j + 1
            if (i < n and cmds[i] and cmds[i][0] == '_label_marker'
                    and _last(cmds[i]) == target):
                i += 1  # swallow the inline skip marker (pre-split form)
            continue

        # Single positive jump (vanilla one-condition page): condition kept
        # as-is, body under its own target. A `goto` that may follow is the
        # default jump, left for the body walk to pick up.
        if len(run) == 1:
            selectors.append({'conditions': [_cond_without_target(c)],
                              'page_label': target, 'skip_label': None,
                              'inline': False})
            i += 1
            continue

        # 2+ shared-target checks with an INLINE body (hand-written, no trailing
        # goto): fold into one AND-page whose body is what follows inline.
        conditions = [_negate_cond(_cond_without_target(g)) for g in run]
        selectors.append({'conditions': conditions, 'page_label': None,
                          'skip_label': target, 'inline': True})
        i = j
        break

    return preamble, selectors, i


def parse_raw_script_text(raw_text: str, texts: dict | None = None
                          ) -> tuple[dict[str, list], dict[str, str], list[str]]:
    """Parse hand-edited raw ``.inc`` text (the Raw Script Code editor) back
    into command tuples and text strings.

    The text may contain a mix of script label blocks (``Label::`` followed by
    command lines) and text label blocks (``Label::`` followed by ``.string``/
    ``.braille`` lines). Each ``Label::`` starts a new block; a block is a TEXT
    block if its body has any ``.string``/``.braille`` directive, otherwise it
    is a SCRIPT block parsed with the same parser the file loader uses.

    Returns ``(scripts, out_texts, order)`` where *scripts* maps label →
    command-tuple list, *out_texts* maps label → string (``__BRAILLE__``-
    prefixed for braille), and *order* is the label order as written. Text
    blocks are resolved BEFORE scripts so a ``msgbox Label`` picks up the
    edited string. Raises ``ValueError`` with a plain-English message on a
    malformed block so the caller can refuse to save.
    """
    label_re = re.compile(r'^([A-Za-z0-9_]+)::')
    string_re = re.compile(r'\.string\s+"((?:\\.|[^"])*)"')
    braille_re = re.compile(r'\.braille\s+"((?:\\.|[^"])*)"')

    # ── Split into (label, body_lines) blocks ────────────────────────────
    blocks: list[tuple[str, list[str]]] = []
    current: str | None = None
    body: list[str] = []
    for raw_line in raw_text.splitlines():
        stripped = raw_line.strip()
        m = label_re.match(stripped)
        if m:
            if current is not None:
                blocks.append((current, body))
            current = m.group(1)
            body = []
            continue
        if current is None:
            # Non-blank, non-comment content before any label is an error.
            if stripped and not stripped.startswith('@'):
                raise ValueError(
                    f'Line before the first label:: — "{stripped[:40]}"')
            continue
        body.append(raw_line)
    if current is not None:
        blocks.append((current, body))

    if not blocks:
        raise ValueError('No script labels (Name::) found.')

    # ── First pass: text blocks ──────────────────────────────────────────
    merged_texts: dict[str, str] = dict(texts or {})
    out_texts: dict[str, str] = {}
    order: list[str] = []
    script_blocks: list[tuple[str, list[str]]] = []
    for label, blines in blocks:
        order.append(label)
        is_text = any(string_re.search(l) or braille_re.search(l)
                      for l in blines)
        if is_text:
            buf: list[str] = []
            is_braille = False
            for l in blines:
                bm = braille_re.search(l)
                if bm:
                    is_braille = True
                    buf.append(bm.group(1).replace('\\n', '\n')
                               .replace('\\p', '\n\n'))
                    continue
                sm = string_re.search(l)
                if sm:
                    buf.append(sm.group(1).replace('\\n', '\n')
                               .replace('\\p', '\n\n'))
            val = ''.join(buf)
            if is_braille:
                val = _BRAILLE_PREFIX + val
            out_texts[label] = val
            merged_texts[label] = val
        else:
            script_blocks.append((label, blines))

    # ── Second pass: script blocks (with texts resolved) ─────────────────
    scripts: dict[str, list] = {}
    for label, blines in script_blocks:
        scripts[label] = _parse_script_lines(blines, merged_texts)

    return scripts, out_texts, order


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

        # The command NAME is the first whitespace-delimited token. Every
        # branch below matches on `head`, never on `stripped.startswith(name)`.
        #
        # DO NOT go back to startswith(). A prefix match has no word boundary,
        # so `delay` swallowed the movement macros `delay_1/_4/_8/_16/_32` and
        # re-emitted them as `delay _16` — not a command, and the project would
        # no longer assemble (241 lines in the live project sit on this fault). The same
        # hazard hid `applymovementat` behind `applymovement`. See
        # docs/BUGS.md → "EVENTide: delay_16 mangled into `delay _16`".
        head = stripped.split(None, 1)[0]

        # ── Messages ─────────────────────────────────────────────────
        if head == 'msgbox':
            # NOTE: the character class is [^"\\] (single char), NOT [^"]* — a
            # nested unbounded quantifier here caused catastrophic backtracking
            # (an app freeze) on an unterminated quote, e.g. a msgbox whose text
            # spans two physical lines. This linear form fails fast instead.
            m = re.match(r'msgbox\s+"((?:\\.|[^"\\])*)"(?:\s*,\s*(\S+))?', stripped)
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
                cmds.append((
                    'message', label,
                    _strip_braille_prefix(texts.get(label, '')),
                    msg_type))
                i += 1
                continue

        # ── Braille messages — same tuple shape, render='braille' ────
        # `braillemessage <label>` doesn't take a MSGBOX_TYPE; the
        # window style is fixed by the engine. The text value here has
        # the private __BRAILLE__ prefix stripped — that prefix is only
        # used between parse_text_inc and write_text_inc to remember
        # which directive to emit, and shouldn't leak into the command
        # tuple the dialog reads.
        if head == 'braillemessage':
            m = re.match(r'braillemessage\s+([^,\s]+)', stripped)
            if m:
                label = m.group(1).rstrip(',')
                cmds.append((
                    'message', label,
                    _strip_braille_prefix(texts.get(label, '')),
                    '', 'braille'))
                i += 1
                continue

        # ── Warp variants ────────────────────────────────────────────
        # The engine's `formatwarp map, a, b, c` accepts FOUR arities:
        #   warp MAP                  -> dummy warp id and coords
        #   warp MAP, warpId          -> named warp, dummy coords
        #   warp MAP, x, y            -> dummy warp id, explicit coords
        #   warp MAP, warpId, x, y    -> everything explicit
        # A fixed three-capture regex only understood the third form and
        # silently dropped the Y of the fourth, landing the player on the
        # wrong tile. Capture whatever is actually there instead.
        # See docs/BUGS.md → "EVENTide: 4-argument warp loses its Y".
        _warp_matched = False
        for warp_cmd in ('warpteleport', 'warpsilent', 'warpdoor', 'warphole', 'warp'):
            if head == warp_cmd:
                rest = stripped[len(warp_cmd):].strip()
                if rest:
                    parts_w = [p.strip() for p in rest.split(',')]
                    args_w = [int(p) if p.lstrip('-').isdigit() else p
                              for p in parts_w if p != '']
                    cmds.append((warp_cmd, *args_w))
                    i += 1
                    _warp_matched = True
                break
        if _warp_matched:
            continue

        # ── Sound ────────────────────────────────────────────────────
        if head == 'playse':
            # playse can have 1 or 3 args
            m = re.match(r'playse\s+([^,]+?)(?:\s*,\s*(\d+)\s*,\s*(\d+))?$', stripped)
            if m:
                sfx = m.group(1).strip()
                cmds.append(('playse', sfx))
                i += 1
                continue
        elif head == 'playfanfare':
            m = re.match(r'playfanfare\s+(\S+)', stripped)
            if m:
                cmds.append(('playfanfare', m.group(1)))
                i += 1
                continue
        elif head == 'playbgm':
            m = re.match(r'playbgm\s+([^,]+),\s*(\d+)', stripped)
            if m:
                cmds.append(('playbgm', m.group(1).strip(), int(m.group(2))))
                i += 1
                continue

        # ── Trainer battle (multi-arg) ───────────────────────────────
        elif head == 'trainerbattle':
            # Handle variants: trainerbattle_single, trainerbattle_no_intro, etc.
            parts = stripped.split(None, 1)
            tb_cmd = parts[0]  # e.g. 'trainerbattle_single'
            args = parts[1] if len(parts) > 1 else ''
            cmds.append((tb_cmd, args))
            i += 1
            continue

        # ── Wild battle (setwildbattle + optional shiny + dowildbattle) ──
        elif head == 'setwildbattle':
            m = re.match(r'setwildbattle\s+([^,]+),\s*(\d+)', stripped)
            if m:
                species = m.group(1).strip()
                level = int(m.group(2))
                shiny = False
                j = i + 1
                if j < len(lines) and lines[j].strip() == 'setflag FLAG_SHINY_SPECIAL':
                    shiny = True
                    j += 1
                if j < len(lines) and lines[j].split(None, 1)[:1] == ['dowildbattle']:
                    cmds.append(('wildbattle', species, level, shiny))
                    i = j + 1
                    continue

        # ── Items (with optional quantity) ───────────────────────────
        elif head == 'additem':
            args = stripped[len('additem'):].strip()
            cmds.append(('additem', args))
            i += 1
            continue
        elif head == 'removeitem':
            args = stripped[len('removeitem'):].strip()
            cmds.append(('removeitem', args))
            i += 1
            continue
        elif head == 'checkitem':
            args = stripped[len('checkitem'):].strip()
            cmds.append(('checkitem', args))
            i += 1
            continue
        elif head == 'checkitemspace':
            args = stripped[len('checkitemspace'):].strip()
            cmds.append(('checkitemspace', args))
            i += 1
            continue

        # ── Pokemon ──────────────────────────────────────────────────
        elif head == 'givemon':
            args = stripped[len('givemon'):].strip()
            cmds.append(('givemon', args))
            i += 1
            continue
        elif head == 'giveegg':
            args = stripped[len('giveegg'):].strip()
            cmds.append(('giveegg', args))
            i += 1
            continue
        elif head == 'checkpartymove':
            args = stripped[len('checkpartymove'):].strip()
            cmds.append(('checkpartymove', args))
            i += 1
            continue

        # ── Movement ─────────────────────────────────────────────────
        elif head == 'applymovement':
            m = re.match(r'applymovement\s+([^,]+),\s*(\S+)', stripped)
            if m:
                cmds.append(('applymovement', m.group(1).strip(), m.group(2).strip()))
                i += 1
                continue
        elif head == 'applymovementat':
            args = stripped[len('applymovementat'):].strip()
            cmds.append(('applymovementat', args))
            i += 1
            continue

        # ── NPC / Object control ─────────────────────────────────────
        elif head == 'removeobject':
            args = stripped[len('removeobject'):].strip()
            cmds.append(('removeobject', args))
            i += 1
            continue
        elif head == 'addobject':
            args = stripped[len('addobject'):].strip()
            cmds.append(('addobject', args))
            i += 1
            continue
        elif head == 'showobjectat':
            args = stripped[len('showobjectat'):].strip()
            cmds.append(('showobjectat', args))
            i += 1
            continue
        elif head == 'hideobjectat':
            args = stripped[len('hideobjectat'):].strip()
            cmds.append(('hideobjectat', args))
            i += 1
            continue
        elif head == 'turnobject':
            args = stripped[len('turnobject'):].strip()
            cmds.append(('turnobject', args))
            i += 1
            continue
        elif head == 'setobjectxy':
            args = stripped[len('setobjectxy'):].strip()
            cmds.append(('setobjectxy', args))
            i += 1
            continue
        elif head == 'setobjectxyperm':
            args = stripped[len('setobjectxyperm'):].strip()
            cmds.append(('setobjectxyperm', args))
            i += 1
            continue

        # ── Flags & Variables ────────────────────────────────────────
        elif head == 'setflag':
            args = stripped[len('setflag'):].strip()
            cmds.append(('setflag', args))
            i += 1
            continue
        elif head == 'clearflag':
            args = stripped[len('clearflag'):].strip()
            cmds.append(('clearflag', args))
            i += 1
            continue
        elif head == 'checkflag':
            args = stripped[len('checkflag'):].strip()
            cmds.append(('checkflag', args))
            i += 1
            continue
        elif head == 'setvar':
            args = stripped[len('setvar'):].strip()
            cmds.append(('setvar', args))
            i += 1
            continue
        elif head == 'addvar':
            args = stripped[len('addvar'):].strip()
            cmds.append(('addvar', args))
            i += 1
            continue
        elif head == 'subvar':
            args = stripped[len('subvar'):].strip()
            cmds.append(('subvar', args))
            i += 1
            continue
        elif head == 'copyvar':
            args = stripped[len('copyvar'):].strip()
            cmds.append(('copyvar', args))
            i += 1
            continue
        elif head == 'compare_var_to_value':
            args = stripped[len('compare_var_to_value'):].strip()
            cmds.append(('compare_var_to_value', args))
            i += 1
            continue
        elif head == 'compare_var_to_var':
            args = stripped[len('compare_var_to_var'):].strip()
            cmds.append(('compare_var_to_var', args))
            i += 1
            continue

        # ── Conditional branches (must be before generic goto/call) ──
        elif head == 'goto_if_eq':
            m = re.match(r'goto_if_eq\s+([^,]+),\s*([^,]+),\s*(\S+)', stripped)
            if m:
                cmds.append(('goto_if_eq', m.group(1).strip(), m.group(2).strip(), m.group(3).strip()))
                i += 1; continue
        elif head == 'goto_if_ne':
            m = re.match(r'goto_if_ne\s+([^,]+),\s*([^,]+),\s*(\S+)', stripped)
            if m:
                cmds.append(('goto_if_ne', m.group(1).strip(), m.group(2).strip(), m.group(3).strip()))
                i += 1; continue
        elif head == 'goto_if_lt':
            m = re.match(r'goto_if_lt\s+([^,]+),\s*([^,]+),\s*(\S+)', stripped)
            if m:
                cmds.append(('goto_if_lt', m.group(1).strip(), m.group(2).strip(), m.group(3).strip()))
                i += 1; continue
        elif head == 'goto_if_ge':
            m = re.match(r'goto_if_ge\s+([^,]+),\s*([^,]+),\s*(\S+)', stripped)
            if m:
                cmds.append(('goto_if_ge', m.group(1).strip(), m.group(2).strip(), m.group(3).strip()))
                i += 1; continue
        elif head == 'goto_if_le':
            m = re.match(r'goto_if_le\s+([^,]+),\s*([^,]+),\s*(\S+)', stripped)
            if m:
                cmds.append(('goto_if_le', m.group(1).strip(), m.group(2).strip(), m.group(3).strip()))
                i += 1; continue
        elif head == 'goto_if_gt':
            m = re.match(r'goto_if_gt\s+([^,]+),\s*([^,]+),\s*(\S+)', stripped)
            if m:
                cmds.append(('goto_if_gt', m.group(1).strip(), m.group(2).strip(), m.group(3).strip()))
                i += 1; continue
        elif head == 'goto_if_set':
            m = re.match(r'goto_if_set\s+([^,]+),\s*(\S+)', stripped)
            if m:
                cmds.append(('goto_if_set', m.group(1).strip(), m.group(2).strip()))
                i += 1; continue
        elif head == 'goto_if_unset':
            m = re.match(r'goto_if_unset\s+([^,]+),\s*(\S+)', stripped)
            if m:
                cmds.append(('goto_if_unset', m.group(1).strip(), m.group(2).strip()))
                i += 1; continue
        elif head == 'call_if_eq':
            m = re.match(r'call_if_eq\s+([^,]+),\s*([^,]+),\s*(\S+)', stripped)
            if m:
                cmds.append(('call_if_eq', m.group(1).strip(), m.group(2).strip(), m.group(3).strip()))
                i += 1; continue
        elif head == 'call_if_ne':
            m = re.match(r'call_if_ne\s+([^,]+),\s*([^,]+),\s*(\S+)', stripped)
            if m:
                cmds.append(('call_if_ne', m.group(1).strip(), m.group(2).strip(), m.group(3).strip()))
                i += 1; continue
        elif head == 'call_if_lt':
            m = re.match(r'call_if_lt\s+([^,]+),\s*([^,]+),\s*(\S+)', stripped)
            if m:
                cmds.append(('call_if_lt', m.group(1).strip(), m.group(2).strip(), m.group(3).strip()))
                i += 1; continue
        elif head == 'call_if_ge':
            m = re.match(r'call_if_ge\s+([^,]+),\s*([^,]+),\s*(\S+)', stripped)
            if m:
                cmds.append(('call_if_ge', m.group(1).strip(), m.group(2).strip(), m.group(3).strip()))
                i += 1; continue
        elif head == 'call_if_set':
            m = re.match(r'call_if_set\s+([^,]+),\s*(\S+)', stripped)
            if m:
                cmds.append(('call_if_set', m.group(1).strip(), m.group(2).strip()))
                i += 1; continue
        elif head == 'call_if_unset':
            m = re.match(r'call_if_unset\s+([^,]+),\s*(\S+)', stripped)
            if m:
                cmds.append(('call_if_unset', m.group(1).strip(), m.group(2).strip()))
                i += 1; continue

        # ── Flow control ─────────────────────────────────────────────
        elif head == 'goto':
            args = stripped[len('goto'):].strip()
            cmds.append(('goto', args) if args else ('goto',))
            i += 1
            continue
        elif head == 'call':
            args = stripped[len('call'):].strip()
            cmds.append(('call', args) if args else ('call',))
            i += 1
            continue
        elif head == 'specialvar':
            args = stripped[len('specialvar'):].strip()
            cmds.append(('specialvar', args))
            i += 1
            continue
        elif head == 'special':
            args = stripped[len('special'):].strip()
            cmds.append(('special', args))
            i += 1
            continue

        # ── Weather ──────────────────────────────────────────────────
        elif head == 'setweather':
            args = stripped[len('setweather'):].strip()
            cmds.append(('setweather', args))
            i += 1
            continue

        # ── Screen effects ───────────────────────────────────────────
        elif head == 'fadescreenspeed':
            args = stripped[len('fadescreenspeed'):].strip()
            cmds.append(('fadescreenspeed', args))
            i += 1
            continue
        elif head == 'fadescreen':
            args = stripped[len('fadescreen'):].strip()
            cmds.append(('fadescreen', args))
            i += 1
            continue
        elif head == 'setflashlevel':
            args = stripped[len('setflashlevel'):].strip()
            cmds.append(('setflashlevel', args))
            i += 1
            continue

        # ── Timing ───────────────────────────────────────────────────
        elif head == 'delay':
            args = stripped[len('delay'):].strip()
            cmds.append(('delay', args))
            i += 1
            continue

        # ── Money & Coins ────────────────────────────────────────────
        elif head == 'addmoney':
            args = stripped[len('addmoney'):].strip()
            cmds.append(('addmoney', args))
            i += 1
            continue
        elif head == 'removemoney':
            args = stripped[len('removemoney'):].strip()
            cmds.append(('removemoney', args))
            i += 1
            continue
        elif head == 'checkmoney':
            args = stripped[len('checkmoney'):].strip()
            cmds.append(('checkmoney', args))
            i += 1
            continue
        elif head == 'addcoins':
            args = stripped[len('addcoins'):].strip()
            cmds.append(('addcoins', args))
            i += 1
            continue
        elif head == 'removecoins':
            args = stripped[len('removecoins'):].strip()
            cmds.append(('removecoins', args))
            i += 1
            continue

        # ── Buffers ──────────────────────────────────────────────────
        elif head == 'bufferspeciesname':
            args = stripped[len('bufferspeciesname'):].strip()
            cmds.append(('bufferspeciesname', args))
            i += 1
            continue
        elif head == 'bufferitemname':
            args = stripped[len('bufferitemname'):].strip()
            cmds.append(('bufferitemname', args))
            i += 1
            continue
        elif head == 'buffermovename':
            args = stripped[len('buffermovename'):].strip()
            cmds.append(('buffermovename', args))
            i += 1
            continue
        elif head == 'buffernumberstring':
            args = stripped[len('buffernumberstring'):].strip()
            cmds.append(('buffernumberstring', args))
            i += 1
            continue
        elif head == 'bufferstring':
            args = stripped[len('bufferstring'):].strip()
            cmds.append(('bufferstring', args))
            i += 1
            continue

        # ── Respawn / misc ───────────────────────────────────────────
        elif head == 'setrespawn':
            args = stripped[len('setrespawn'):].strip()
            cmds.append(('setrespawn', args))
            i += 1
            continue
        elif head == 'playmoncry':
            args = stripped[len('playmoncry'):].strip()
            cmds.append(('playmoncry', args))
            i += 1
            continue

        # ── Doors ────────────────────────────────────────────────────
        elif head == 'opendoor':
            args = stripped[len('opendoor'):].strip()
            cmds.append(('opendoor', args))
            i += 1
            continue
        elif head == 'closedoor':
            args = stripped[len('closedoor'):].strip()
            cmds.append(('closedoor', args))
            i += 1
            continue
        elif head == 'waitdooranim':
            cmds.append(('waitdooranim',))
            i += 1
            continue

        # ── Decorations ──────────────────────────────────────────────
        elif head == 'adddecoration':
            args = stripped[len('adddecoration'):].strip()
            cmds.append(('adddecoration', args))
            i += 1
            continue
        elif head == 'removedecoration':
            args = stripped[len('removedecoration'):].strip()
            cmds.append(('removedecoration', args))
            i += 1
            continue

        # ── Pokemart ─────────────────────────────────────────────────
        elif head == 'pokemart':
            args = stripped[len('pokemart'):].strip()
            cmds.append(('pokemart', args))
            i += 1
            continue

        # ── Metatile ─────────────────────────────────────────────────
        elif head == 'setmetatile':
            args = stripped[len('setmetatile'):].strip()
            cmds.append(('setmetatile', args))
            i += 1
            continue

        # ── Map setup commands ───────────────────────────────────────
        elif head == 'map_script' or head == 'map_script_2':
            # These are map header script entries — pass through as generic
            parts_m = stripped.split(None, 1)
            cmds.append((parts_m[0], parts_m[1] if len(parts_m) > 1 else ''))
            i += 1
            continue
        elif head == '.equ' or head == '.byte' or head == '.2byte':
            # Assembler directives — pass through as generic
            parts_m = stripped.split(None, 1)
            cmds.append((parts_m[0], parts_m[1] if len(parts_m) > 1 else ''))
            i += 1
            continue

        # ── Text display ─────────────────────────────────────────────
        elif head == 'message':
            # `message` is NOT `msgbox`. The engine macros differ:
            #   message = .byte 0x67 + .4byte <text>   (loads the text only)
            #   msgbox  = loadword 0, <text> + callstd <type>
            #             (the full show / wait / button / close routine)
            # Conflating them used to rewrite every `message` line as
            # `msgbox`, which changed in-game behaviour in 112 places (e.g. a
            # `message` + `waitmessage` + `multichoice` sequence had the box
            # closed out from under the multichoice). The 5th tuple element —
            # the same render slot braille uses — keeps them apart.
            # See docs/BUGS.md → "EVENTide: `message` rewritten as `msgbox`".
            m = re.match(r'message\s+(\S+)', stripped)
            if m:
                label = m.group(1)
                cmds.append(('message', label,
                             _strip_braille_prefix(texts.get(label, '')),
                             '', 'raw'))
                i += 1
                continue
        elif head == 'textcolor':
            args = stripped[len('textcolor'):].strip()
            cmds.append(('textcolor', args))
            i += 1
            continue

        # ── Special variants ─────────────────────────────────────────
        elif head == 'famechecker':
            args = stripped[len('famechecker'):].strip()
            cmds.append(('famechecker', args))
            i += 1
            continue

        # ── Object control (extended) ────────────────────────────────
        elif head == 'copyobjectxytoperm':
            args = stripped[len('copyobjectxytoperm'):].strip()
            cmds.append(('copyobjectxytoperm', args))
            i += 1
            continue
        elif head == 'setobjectmovementtype':
            m = re.match(r'setobjectmovementtype\s+([^,]+),\s*(\S+)', stripped)
            if m:
                cmds.append(('setobjectmovementtype', m.group(1).strip(), m.group(2).strip()))
                i += 1
                continue
        elif head == 'setworldmapflag':
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
# Yes/No choice branches — RPG-Maker-style desugar / resugar
#
# The Event Editor represents a yes/no branch as four flat marker commands:
#     ('choice_yesno',)  ('when_yes',)  ('when_no',)  ('branch_end',)
# with the question living in the message command immediately ABOVE the choice.
#
# On the way OUT (desugar) these expand to the exact idiom real pokefirered
# scripts use — the preceding msgbox becomes MSGBOX_YESNO and a compare/goto
# with two sub-labels carries the branches:
#
#     msgbox <question>, MSGBOX_YESNO
#     goto_if_eq VAR_RESULT, NO, <base>_YesNo1_No
#     <yes body>
#     goto <base>_YesNo1_End
#     <base>_YesNo1_No::            (emitted via a _label_marker)
#     <no body>
#     <base>_YesNo1_End::           (emitted via a _label_marker)
#
# On the way IN (resugar) that same shape — a message(YESNO) + goto_if_eq
# VAR_RESULT NO L + goto E + label L + label E — collapses back to the four
# markers. Matching is structural (targets line up with the two labels), so it
# also folds hand-authored yes/no scripts, and anything that doesn't match is
# left as raw primitives (still fully editable, just not collapsed).
# ═════════════════════════════════════════════════════════════════════════════

_CHOICE_MARKERS = ('choice_yesno', 'when_yes', 'when_no', 'branch_end')


def _retype_message_yesno(msg: tuple) -> tuple:
    """Return a copy of a ('message', label, text, type, [render]) tuple with
    its MSGBOX type set to MSGBOX_YESNO.

    A yes/no box is by definition `msgbox ..., MSGBOX_YESNO`, so this also
    forces the render mode back to normal: the bare `message` command and
    `braillemessage` take no MSGBOX type, and leaving either in place would
    make the writer emit a command that drops the YESNO and breaks the branch.
    """
    parts = list(msg)
    while len(parts) < 4:
        parts.append('' if len(parts) != 1 else None)
    parts[3] = 'MSGBOX_YESNO'
    if len(parts) > 4:
        parts[4] = 'normal'
    return tuple(parts)


def _split_choice_block(cmds: list, start: int) -> tuple:
    """Given cmds[start] == ('choice_yesno',), return (yes_body, no_body,
    end_index) where end_index is the position of the matching branch_end.
    Nested choices are kept intact inside the bodies (depth-tracked)."""
    yes_body: list = []
    no_body: list = []
    target = yes_body
    depth = 0
    i = start + 1
    end_index = len(cmds) - 1
    while i < len(cmds):
        c = cmds[i]
        head = c[0] if c else None
        if head == 'choice_yesno':
            depth += 1
            target.append(c)
        elif head == 'branch_end':
            if depth == 0:
                end_index = i
                break
            depth -= 1
            target.append(c)
        elif head == 'when_yes' and depth == 0:
            target = yes_body
        elif head == 'when_no' and depth == 0:
            target = no_body
        else:
            target.append(c)
        i += 1
    return yes_body, no_body, end_index


def desugar_choices(commands: list, base_label: str) -> list:
    """Expand ('choice_yesno' ...) marker blocks into real pokefirered
    primitives (msgbox YESNO + compare/goto + sub-labels). Recursive, so
    nested branches expand correctly. Unique labels are derived from
    *base_label*."""
    counter = [0]

    def expand(cmds: list) -> list:
        out: list = []
        i = 0
        while i < len(cmds):
            c = cmds[i]
            if c and c[0] == 'choice_yesno':
                yes_body, no_body, end_i = _split_choice_block(cmds, i)
                yes_lines = expand(yes_body)
                no_lines = expand(no_body)
                counter[0] += 1
                n = counter[0]
                l_no = f'{base_label}_YesNo{n}_No'
                l_end = f'{base_label}_YesNo{n}_End'
                # merge the immediately-preceding message into the yes/no box
                for bi in range(len(out) - 1, -1, -1):
                    if not out[bi]:
                        continue
                    if out[bi][0] == 'message':
                        out[bi] = _retype_message_yesno(out[bi])
                    break
                out.append(('goto_if_eq', 'VAR_RESULT', 'NO', l_no))
                out.extend(yes_lines)
                out.append(('goto', l_end))
                out.append(('_label_marker', l_no))
                out.extend(no_lines)
                out.append(('_label_marker', l_end))
                i = end_i + 1
                continue
            out.append(c)
            i += 1
        return out

    # nothing to do if there are no choice markers (fast path)
    if not any(c and c[0] in _CHOICE_MARKERS for c in commands):
        return list(commands)
    return expand(commands)


def resugar_choices(commands: list) -> list:
    """Collapse the desugared yes/no idiom back into choice markers for
    display. Structural match; unrecognised shapes are returned unchanged."""

    def _target(ct):
        # goto_if_eq VAR_RESULT, NO, LABEL  -> LABEL ; goto LABEL -> LABEL
        if not ct:
            return None
        if ct[0] == 'goto' and len(ct) > 1:
            return ct[1]
        if ct[0] == 'goto_if_eq' and len(ct) > 3:
            return ct[3]
        return None

    def collapse(cmds: list) -> list:
        out: list = []
        i = 0
        n = len(cmds)
        while i < n:
            c = cmds[i]
            # detect: goto_if_eq VAR_RESULT, NO, L  ... goto E ... _lm L ... _lm E
            # Only when the branch is genuinely a yes/no box — i.e. the command
            # right above is a MSGBOX_YESNO message. This avoids mis-collapsing
            # an unrelated VAR_RESULT branch (a special/multichoice result).
            prev_is_yesno = bool(
                out and out[-1] and out[-1][0] == 'message'
                and len(out[-1]) > 3 and out[-1][3] == 'MSGBOX_YESNO')
            if (prev_is_yesno and c and c[0] == 'goto_if_eq' and len(c) > 3
                    and c[1] == 'VAR_RESULT' and str(c[2]).upper() in ('NO', '0')):
                l_no = c[3]
                # find `goto E` and the two label markers at this level
                yes_body = []
                j = i + 1
                goto_end = None
                while j < n:
                    cj = cmds[j]
                    if cj and cj[0] == 'goto' and len(cj) > 1:
                        goto_end = cj[1]
                        break
                    if cj and cj[0] == '_label_marker':
                        break  # ran into labels without a goto — not our shape
                    yes_body.append(cj)
                    j += 1
                if goto_end is None:
                    out.append(c); i += 1; continue
                # next must be _label_marker l_no
                if not (j + 1 < n and cmds[j + 1] and cmds[j + 1][0] == '_label_marker'
                        and cmds[j + 1][1] == l_no):
                    out.append(c); i += 1; continue
                # collect no_body until _label_marker goto_end
                no_body = []
                k = j + 2
                found_end = False
                while k < n:
                    ck = cmds[k]
                    if ck and ck[0] == '_label_marker' and ck[1] == goto_end:
                        found_end = True
                        break
                    no_body.append(ck)
                    k += 1
                if not found_end:
                    out.append(c); i += 1; continue
                # SUCCESS — retype the preceding message back to plain, emit block
                for bi in range(len(out) - 1, -1, -1):
                    if not out[bi]:
                        continue
                    if out[bi][0] == 'message' and len(out[bi]) > 3 and out[bi][3] == 'MSGBOX_YESNO':
                        m = list(out[bi]); m[3] = ''
                        out[bi] = tuple(m)
                    break
                out.append(('choice_yesno',))
                out.append(('when_yes',))
                out.extend(collapse(yes_body))
                out.append(('when_no',))
                out.extend(collapse(no_body))
                out.append(('branch_end',))
                i = k + 1  # past the _label_marker end
                continue
            out.append(c)
            i += 1
        return out

    return collapse(list(commands))


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
    *hidden*: non-visible lines from the original script, scanned so an
    ``nop`` filler is not added to a block that already has one.
    *texts*: mutable dict of label->text; message commands with labels update it.

    This writer emits ONLY what the command list holds. It does not guess at
    missing ``lock`` / ``release``: that guess adds 289 locks and 197 releases
    when every label is re-rendered, and a ``lock`` added to
    an On Transition map script freezes the player on map entry. If a script
    looks like it is missing a lock, say so in the UI — do not rewrite it.
    See docs/BUGS.md → "EVENTide: lock/release/end injected into scripts".
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

    # Build the set of commands already present (visible and hidden) so the
    # empty-script `nop` filler below is not duplicated.
    cmd_names: set[str] = set()
    for data in commands:
        if data:
            cmd_names.add(data[0])
    if hidden:
        for _, line in hidden:
            token = line.strip().split()
            if token:
                cmd_names.add(token[0])

    for data in commands:
        if not data:
            continue
        cmd = data[0]

        # ── Inline sub-label (defensive) ─────────────────────────────
        # The save path normally SPLITS on _label_marker into separate
        # Label:: blocks before reaching here, so this rarely fires. If a
        # marker does leak through (any caller that doesn't split), emit a
        # real label line instead of a garbage `_label_marker X` command.
        if cmd == '_label_marker':
            lbl = data[1] if len(data) > 1 else ''
            if lbl:
                visible.append(f'{lbl}::\n')
            continue

        # ── Message ──────────────────────────────────────────────────
        if cmd == 'message':
            label_name = data[1] if len(data) > 1 else None
            text = data[2] if len(data) > 2 else ''
            msg_type = data[3] if len(data) > 3 else ''
            # 5th element (optional) selects render mode. 'braille'
            # means emit `braillemessage <label>` and tell the text
            # writer to use `.braille` instead of `.string`. 'raw' means
            # emit the bare `message <label>` command — a DIFFERENT engine
            # opcode from msgbox (see the reader's `message` branch).
            render = data[4] if len(data) > 4 else 'normal'

            if render == 'raw':
                # `message` takes a text POINTER, so there is no inline-string
                # form. Synthesise a label if one is somehow missing, the same
                # way braille does, so the data still round-trips.
                if not label_name:
                    label_name = f'Text_{abs(hash(text)) & 0xFFFFFF:06X}'
                visible.append(f'message {label_name}\n')
                texts[label_name] = text
                continue

            if render == 'braille':
                # Braille MUST use a label — `braillemessage` doesn't
                # support an inline string form. If we got here without
                # a label we synthesise one from a hash so the data
                # round-trips, but UI should generally enforce a label.
                if not label_name:
                    label_name = f'BrailleText_{abs(hash(text)) & 0xFFFFFF:06X}'
                visible.append(f'braillemessage {label_name}\n')
                # Stash the text PLUS a render-type marker that the
                # text.inc writer reads to pick `.braille` vs `.string`.
                # The marker is a private prefix the writer strips
                # before emitting; it never leaks into the actual
                # output bytes.
                texts[label_name] = '__BRAILLE__' + text
                continue

            if label_name:
                line = f'msgbox {label_name}'
                if msg_type:
                    line += f', {msg_type}'
                visible.append(line + '\n')
                texts[label_name] = text
            else:
                # Escape so the whole string stays on ONE physical line — the
                # reader is line-based, and an embedded real newline both breaks
                # parsing and (historically) froze it. Inverse of the reader's
                # `\n`->newline / `\p`->blank-line mapping: paragraph breaks
                # (blank line) become \p, single breaks become \n.
                escaped = text.replace('"', '\\"')
                escaped = escaped.replace('\n\n', '\\p').replace('\n', '\\n')
                line = f'msgbox "{escaped}"'
                if msg_type:
                    line += f', {msg_type}'
                visible.append(line + '\n')

        # ── Warps (map plus 0-3 further positional args) ─────────────
        # Emit exactly the arguments the tuple carries — the engine macro
        # takes anywhere from one to four, and hard-coding three used to
        # truncate `warp MAP, warpId, x, y` to `warp MAP, warpId, x`.
        elif cmd in ('warp', 'warpsilent', 'warpdoor', 'warphole', 'warpteleport'):
            if len(data) > 2:
                visible.append(
                    f'{cmd} ' + ', '.join(str(a) for a in data[1:]) + '\n')
            else:
                args = data[1] if len(data) > 1 else ''
                visible.append((f'{cmd} {args}' if args else cmd) + '\n')

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
    fallthrough: set[str] | None = None,
) -> list[str]:
    """Render a single label's script lines from command data.

    *fallthrough*: labels whose block in the ORIGINAL file deliberately ran
    on into the next label instead of terminating. Vanilla uses that idiom
    (``..._TrySetDayCareManPos::`` falls into ``..._EndSetDayCareManPos::``,
    which does the ``return``). Appending an `end` to such a block severs the
    fall-through and changes what the script does, so those labels are left
    exactly as they are. Labels NOT in this set are either real terminating
    scripts or brand-new ones EVENTide just created — several New Script
    templates rely on the `end` being supplied here, so it still is.
    """
    out: list[str] = []
    falls = fallthrough or frozenset()

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
            if page_label not in falls and _needs_end_terminator(merged):
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
        if label not in falls and _needs_end_terminator(merged):
            out.append('end\n')
        out.append('\n')

    return out


# Commands after which execution never falls through to the next label, so
# the writer must not append an `end`. `releaseall` is deliberately NOT here:
# in vanilla it is always followed by `end`, and a script ending on it really
# does run past into the next block.
_SCRIPT_TERMINATORS = frozenset({
    'end', 'return', 'step_end', 'goto', 'gotostd',
})


def _needs_end_terminator(merged: list[str]) -> bool:
    """True if a rendered label block should get an `end` appended.

    Two cases say no:
      * the block already terminates (see ``_SCRIPT_TERMINATORS``);
      * the block is DATA, not code — a ``<Map>_MapScripts::`` table is a
        list of ``map_script`` entries closed by ``.byte 0``, and appending
        `end` to it writes a stray opcode byte past the table terminator.
        Any block whose last line is an assembler directive is data.

    Blank lines and ``@`` comments are skipped when looking for the last
    command. They come back in through ``merge_hidden_lines``, and a trailing
    comment that really belongs to the NEXT label used to be mistaken for a
    non-terminating command, which put an `end` after it — 86 of them across
    the project even once the lock/release injection was gone.
    See docs/BUGS.md → "EVENTide: lock/release/end injected into scripts".
    """
    last = ''
    for line in reversed(merged):
        s = line.strip()
        if s and not s.startswith('@'):
            last = s
            break
    if not last:
        return True
    if last.startswith('.'):
        return False
    return last.split(None, 1)[0] not in _SCRIPT_TERMINATORS


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

    # Which blocks deliberately fall through into the next label? Anything
    # whose own last command is not a terminator did, and re-rendering it must
    # not quietly staple an `end` on and cut the fall-through. The original
    # file is the only place that knows, which is why this is computed here and
    # handed down rather than guessed at inside _render_label_block.
    fallthrough: set[str] = set()
    for block_label, block_lines in blocks:
        body = block_lines[1:]  # drop the `Label::` line itself
        if block_label and body and _needs_end_terminator(body):
            fallthrough.add(block_label)

    # Build the output: for labels in pages, replace with rendered
    # version.  For everything else, keep the original lines.
    written_labels: set[str] = set()
    out_lines = []

    for block_label, block_lines in blocks:
        if block_label and block_label in pages:
            rendered = _render_label_block(
                block_label, pages[block_label], hidden_lines, texts,
                fallthrough,
            )
            # Keep the ORIGINAL `Label::` line verbatim. _render_label_block
            # rebuilds it as a bare `Label::`, which drops anything the author
            # wrote after it — vanilla decorates several with a ROM address
            # (`Label:: @ 8169002`), and that is the user's text to keep.
            if rendered and block_lines:
                rendered[0] = block_lines[0]
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
