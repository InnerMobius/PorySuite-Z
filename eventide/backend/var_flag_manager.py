"""
Variables & Flags manager — RPG-Maker-style naming for pokefirered's numbered
var / flag slots.

pokefirered stores every variable in one of a fixed set of numbered slots
(0x4000–0x40FF) and every flag in a numbered bit, giving each a name in
``include/constants/vars.h`` / ``flags.h``. Most of those names are vanilla
systems a hack will never use (Battle Points, the Frontier, contests), and the
unused slots carry placeholder names like ``VAR_0x40EC``.

This module lets the editor present those slots as a friendly list the user can
name, rename, and repurpose — exactly like RPG Maker's Variables / Switches
database — WITHOUT the user ever opening a header. The engine still reads the
same numbered slots; only the human labels change.

Everything here is pure logic (no Qt) so it can be unit-tested offline.
"""

from __future__ import annotations

import re
from pathlib import Path

# ── Slot layout knowledge ───────────────────────────────────────────────────
VARS_HEADER = 'include/constants/vars.h'
FLAGS_HEADER = 'include/constants/flags.h'

# Reserved engine slots that must never be renamed (renaming only breaks things
# — these are plumbing, not "vanilla features you don't need").
_VAR_RESERVED_PREFIXES = ('VAR_TEMP_', 'VAR_OBJ_GFX_ID_', 'VAR_0x8000',
                          'VAR_0x8001', 'VAR_0x8002', 'VAR_0x8003',
                          'VAR_0x8004', 'VAR_0x8005', 'VAR_0x8006',
                          'VAR_0x8007', 'VAR_0x8008', 'VAR_0x8009',
                          'VAR_0x800A', 'VAR_0x800B', 'VAR_RESULT',
                          'VAR_SPECIAL_')
_FLAG_RESERVED_PREFIXES = ('FLAG_TEMP_', 'FLAG_SYS_', 'FLAG_SPECIAL_',
                           'FLAG_TRAINER_FLAG_START', 'FLAG_HIDDEN_ITEM')

_VAR_PLACEHOLDER = re.compile(r'^VAR_0x[0-9A-Fa-f]{3,4}$')
_FLAG_PLACEHOLDER = re.compile(r'^FLAG_UNUSED_')

_DEF_RE_TMPL = r'^\s*#define\s+({prefix}\w+)\s+(.+?)\s*$'

# Directories scanned when counting references / performing renames. The two
# constant headers themselves are handled specially (their #define line is the
# definition, not a use).
_SCAN_DIRS = ('src', 'data', 'include', 'asm')
_SCAN_EXTS = ('.c', '.h', '.inc', '.s', '.json', '.txt')


def _iter_scan_files(root: Path):
    for d in _SCAN_DIRS:
        base = root / d
        if not base.is_dir():
            continue
        for p in base.rglob('*'):
            if p.is_file() and p.suffix in _SCAN_EXTS:
                yield p


def _count_references(root: Path, prefix: str, header_rel: str) -> dict:
    """One pass over the project counting every ``<prefix>NAME`` token.

    Returns ``{symbol: {'src', 'data', 'other', 'total', 'samples'[]}}``. The
    constant header's own ``#define`` lines are NOT counted as uses.
    """
    token_re = re.compile(r'\b(' + prefix + r'\w+)\b')
    header_abs = (root / header_rel).resolve()
    counts: dict[str, dict] = {}

    for path in _iter_scan_files(root):
        is_header = path.resolve() == header_abs
        try:
            text = path.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            continue
        if prefix not in text:
            continue
        try:
            rel = str(path.relative_to(root)).replace('\\', '/')
        except ValueError:
            rel = path.name
        bucket = ('src' if rel.startswith('src/')
                  else 'data' if rel.startswith('data/')
                  else 'other')
        for lineno, line in enumerate(text.splitlines(), 1):
            # Skip a header definition line ("#define VAR_X ...") — that's the
            # definition, not a use.
            if is_header and line.lstrip().startswith('#define '):
                continue
            for m in token_re.finditer(line):
                sym = m.group(1)
                c = counts.setdefault(
                    sym, {'src': 0, 'data': 0, 'other': 0, 'total': 0,
                          'samples': []})
                c[bucket] += 1
                c['total'] += 1
                if len(c['samples']) < 5:
                    c['samples'].append(f'{rel}:{lineno}')
    return counts


def _parse_defs(root: Path, prefix: str, header_rel: str) -> list:
    """Return ``[(name, value_str, lineno)]`` in file order for a header."""
    header = root / header_rel
    out: list = []
    if not header.exists():
        return out
    def_re = re.compile(_DEF_RE_TMPL.format(prefix=prefix))
    for lineno, line in enumerate(header.read_text(encoding='utf-8',
                                                   errors='ignore').splitlines(), 1):
        m = def_re.match(line)
        if not m:
            continue
        name = m.group(1)
        val = m.group(2).split('//')[0].strip()
        if '(' in name:      # function-like macro, skip
            continue
        out.append((name, val, lineno))
    return out


def _is_reserved(kind: str, name: str) -> bool:
    pres = _VAR_RESERVED_PREFIXES if kind == 'var' else _FLAG_RESERVED_PREFIXES
    return any(name.startswith(p) for p in pres)


def _is_placeholder(kind: str, name: str) -> bool:
    rx = _VAR_PLACEHOLDER if kind == 'var' else _FLAG_PLACEHOLDER
    return bool(rx.match(name))


def scan(root, kind: str) -> list:
    """Scan every var/flag slot and classify it.

    *kind* is ``'var'`` or ``'flag'``. Returns a list of dicts in header order:
    ``{name, value, refs_total, refs_src, refs_data, samples, status,
    reserved, placeholder}`` where *status* is one of ``free`` (empty & unnamed),
    ``unused`` (has a vanilla name but referenced nowhere — safe to reclaim),
    ``yours`` (used only by map scripts), ``vanilla`` (used by engine C code),
    or ``reserved`` (engine plumbing — locked).
    """
    root = Path(root)
    prefix = 'VAR_' if kind == 'var' else 'FLAG_'
    header_rel = VARS_HEADER if kind == 'var' else FLAGS_HEADER
    refs = _count_references(root, prefix, header_rel)
    out: list = []
    for name, val, lineno in _parse_defs(root, prefix, header_rel):
        r = refs.get(name, {'src': 0, 'data': 0, 'other': 0, 'total': 0,
                            'samples': []})
        reserved = _is_reserved(kind, name)
        placeholder = _is_placeholder(kind, name)
        if reserved:
            status = 'reserved'
        elif r['total'] == 0:
            status = 'free' if placeholder else 'unused'
        elif r['src'] > 0:
            status = 'vanilla'
        else:
            status = 'yours'
        out.append({
            'name': name, 'value': val, 'lineno': lineno,
            'refs_total': r['total'], 'refs_src': r['src'],
            'refs_data': r['data'], 'samples': r['samples'],
            'status': status, 'reserved': reserved, 'placeholder': placeholder,
        })
    return out


def normalize_name(kind: str, friendly: str) -> str:
    """Turn whatever the user typed into a valid ``VAR_``/``FLAG_`` C identifier.

    ``'Cucco Quest'`` → ``'VAR_CUCCO_QUEST'``. A leading digit is prefixed with
    ``N`` so the macro is legal. An already-prefixed name is kept as-is.
    """
    prefix = 'VAR_' if kind == 'var' else 'FLAG_'
    s = (friendly or '').strip()
    if not s:
        return ''
    up = s.upper()
    if up.startswith(prefix):
        body = up[len(prefix):]
    elif up.startswith('VAR_') or up.startswith('FLAG_'):
        body = up.split('_', 1)[1]
    else:
        body = up
    body = re.sub(r'[^A-Z0-9]+', '_', body).strip('_')
    if not body:
        return ''
    if body[0].isdigit():
        body = 'N' + body
    return prefix + body


def validate_name(kind: str, name: str, existing: set) -> str:
    """Return '' if *name* is a legal, unused symbol, else an error message."""
    prefix = 'VAR_' if kind == 'var' else 'FLAG_'
    if not name:
        return 'Please type a name.'
    if not re.match(r'^' + prefix + r'[A-Z0-9_]+$', name):
        return f'Name must look like {prefix}SOMETHING (letters, numbers, _).'
    if name in existing:
        return f'{name} already exists — pick another name.'
    return ''


def rename_symbol(root, old: str, new: str) -> tuple:
    """Whole-word rename *old* → *new* across the whole project.

    Touches the constant header (the ``#define``) and every reference in
    src / data / include / asm. Returns ``(files_changed, [rel_paths])``.
    """
    root = Path(root)
    pat = re.compile(r'\b' + re.escape(old) + r'\b')
    changed: list = []
    for path in _iter_scan_files(root):
        try:
            text = path.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            continue
        if old not in text:
            continue
        new_text = pat.sub(new, text)
        if new_text != text:
            try:
                path.write_text(new_text, encoding='utf-8', newline='\n')
            except OSError:
                continue
            try:
                changed.append(str(path.relative_to(root)).replace('\\', '/'))
            except ValueError:
                changed.append(path.name)
    return len(changed), changed


def first_free(entries: list) -> dict | None:
    """The first slot safe to claim for a brand-new var/flag."""
    for e in entries:
        if e['status'] == 'free':
            return e
    for e in entries:            # fall back to any unreferenced vanilla slot
        if e['status'] == 'unused':
            return e
    return None
