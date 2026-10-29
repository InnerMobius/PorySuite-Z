"""
Variables & Flags manager — RPG-Maker-style naming for pokefirered's numbered
var / flag slots, plus a reference-driven view of what every slot is actually
for.

pokefirered stores every variable in one of a fixed set of numbered slots
(0x4000–0x40FF) and every flag in a numbered bit, giving each a name in
``include/constants/vars.h`` / ``flags.h``. Most of those names are vanilla
systems a hack will never use, and the unreferenced slots carry placeholder
names like ``VAR_0x40EC`` or ``FLAG_0x021``.

This module lets the editor present those slots as a friendly list the user can
name, rename, and reclaim — exactly like RPG Maker's Variables / Switches
database — WITHOUT the user ever opening a header. The engine still reads the
same numbered slots; only the human labels change.

CLASSIFICATION IS EVIDENCE-BASED, NOT NAME-BASED
------------------------------------------------
An earlier version split slots into "your scripts" vs "vanilla engine" purely
on whether ``src/`` mentioned them. That is wrong: a vanilla hide-the-NPC flag
is referenced only from map data, so every one of them was labelled as the
user's own. :func:`scan` now works out, for each slot:

* its NUMERIC VALUE, resolved through the header's own arithmetic, so the slot
  can be placed in the region the engine reserves it for (temporary, general,
  hidden items, trainers, system). Those region bounds are read from the
  project's own headers, so a project that moves them stays correct.
* WHERE it is actually referenced — which maps, which shared script files,
  which engine source files.

From those two facts it derives a *category* (what the slot is for) and a
*protection* level (how safe it is to take over):

``free``        referenced nowhere — claim it immediately.
``review``      a pickup or a one-off event on a single map — easy to check.
``preserve``    trainer slots. Kept by default: trainer numbering moves around
                as a project repoints battles onto its own maps.
``investigate`` shared between maps, or in a block the engine indexes by
                number (hidden items). Not safe until a human has looked.
``protected``   temporary slots, badges, progression, anything engine C code
                reads. Never offered for reclaiming.

Reclaiming is always an explicit act by the user (see :func:`rename_symbol`,
which can take a snapshot of every file it is about to touch first). Nothing in
this module reclaims, frees or rewrites a slot on its own.

Everything here is pure logic (no Qt) so it can be unit-tested offline.
"""

from __future__ import annotations

import re
import shutil
import time
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
                           'FLAG_TRAINER_FLAG_START')

# A slot whose name is only its own number has never been given a purpose.
_VAR_PLACEHOLDER = re.compile(r'^VAR_0x[0-9A-Fa-f]{3,4}$')
_FLAG_PLACEHOLDER = re.compile(r'^FLAG_(?:UNUSED_0x[0-9A-Fa-f]+|0x[0-9A-Fa-f]{3,4})$')

# Name shapes used ONLY to split one-map references into "a pickup" or "an
# event", and to hold trainer-ish slots back. They never promote a slot to a
# safer level than its references earn.
_ITEMISH = ('FLAG_ITEM_', 'FLAG_HIDDEN_ITEM_', 'FLAG_GOT_', 'FLAG_RECEIVED_',
            'FLAG_OBTAINED_', 'FLAG_FOUND_', 'FLAG_BOUGHT_', 'FLAG_PICKED_UP_')
_TRAINERISH = ('FLAG_DEFEATED_', 'FLAG_BEAT_', 'FLAG_FOUGHT_', 'FLAG_TRAINER_',
               'FLAG_REMATCH_')
_PROGRESSION = ('FLAG_BADGE', 'FLAG_SYS_', 'FLAG_IS_CHAMPION',
                'FLAG_GAME_CLEAR', 'FLAG_ENTERED_HALL_OF_FAME')

_DEF_RE_TMPL = r'^\s*#define\s+({prefix}\w+)\s+(.+?)\s*$'
_ANY_DEF_RE = re.compile(r'^\s*#define\s+([A-Za-z_]\w*)\s+(.+?)\s*$')
# "(?<!\w)" keeps the "xFF" inside "0xFF" from being read as an identifier.
_IDENT_RE = re.compile(r'(?<!\w)([A-Za-z_]\w*)')
_ARITH_RE = re.compile(r'^[0-9xXa-fA-F_()+\-*/ ]+$')

# Directories scanned when counting references / performing renames. The two
# constant headers themselves are handled specially (their #define line is the
# definition, not a use).
_SCAN_DIRS = ('src', 'data', 'include', 'asm')
_SCAN_EXTS = ('.c', '.h', '.inc', '.s', '.json', '.txt')

# ── Category / protection vocabulary ────────────────────────────────────────
# Kept here, not in the UI, so the words the user reads and the words the
# backend reasons with can never drift apart.
CATEGORIES = {
    'free':      'Free',
    'item':      'Item pickup',
    'event':     'Map / story event',
    'trainer':   'Trainer',
    'shared':    'Shared between maps',
    'engine':    'Engine system',
    'system':    'Badge / progression / system',
    'temporary': 'Temporary (not saved)',
    'unknown':   'Unknown',
}

PROTECTION = {
    'free':        'Free to use',
    'review':      'Reclaim candidate',
    'preserve':    'Preserved by default',
    'investigate': 'Protected until investigated',
    'protected':   'Protected',
}

# Only these two may be reclaimed without the user overruling a warning.
RECLAIMABLE = ('free', 'review')

# Regions the engine addresses by SLOT NUMBER rather than by name — hidden
# items as FLAG_HIDDEN_ITEMS_START + index, trainers as TRAINER_FLAGS_START +
# trainer, system flags as SYS_FLAGS + n, and the temporary block the engine
# wipes wholesale. A free slot inside one of these works today, but it is the
# ground the engine grows into, so nothing hands one out on its own: a chest
# template takes a general-region slot instead. The user can still reclaim one
# deliberately from the Flag Manager, where the region is on screen.
INDEXED_REGIONS = ('temporary', 'hidden items', 'hidden items (spare)',
                   'trainers', 'system', 'scratch', 'unknown')


def _iter_scan_files(root: Path):
    for d in _SCAN_DIRS:
        base = root / d
        if not base.is_dir():
            continue
        for p in base.rglob('*'):
            if p.is_file() and p.suffix in _SCAN_EXTS:
                yield p


# ── Numeric values ──────────────────────────────────────────────────────────

def _define_corpus(root: Path, header_rel: str) -> tuple:
    """Every ``#define NAME expr`` under ``include/``, target header first.

    A flag's value is often written as ``(SYS_FLAGS + 0x20)`` and the pieces
    live in other headers (``MAX_TRAINERS_COUNT`` is in ``opponents.h``), so
    the whole include tree is needed to resolve one header's arithmetic.
    """
    raw: dict[str, str] = {}
    order: list[str] = []
    target = root / header_rel
    files = [target] if target.is_file() else []
    inc = root / 'include'
    if inc.is_dir():
        files += sorted(p for p in inc.rglob('*.h') if p != target)
    for p in files:
        try:
            text = p.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            continue
        for line in text.splitlines():
            m = _ANY_DEF_RE.match(line)
            if not m:
                continue
            name = m.group(1)
            if name in raw:
                continue
            expr = m.group(2).split('//')[0].split('/*')[0].strip()
            if not expr:
                continue
            raw[name] = expr
            order.append(name)
    return raw, order


def resolve_values(root, header_rel: str) -> dict:
    """``{define_name: int}`` for every define whose value is plain arithmetic.

    Names whose value is a cast, a struct, a string or a reference to something
    outside the headers are simply absent — a caller must treat a missing value
    as "unknown", never as zero.
    """
    root = Path(root)
    raw, order = _define_corpus(root, header_rel)
    vals: dict[str, int] = {}
    unresolvable: set[str] = set()

    def resolve(name: str, stack: frozenset) -> int | None:
        if name in vals:
            return vals[name]
        if name in unresolvable or name in stack or name not in raw:
            return None
        expr = raw[name]
        subs: dict[str, int] = {}
        for ident in set(_IDENT_RE.findall(expr)):
            v = resolve(ident, stack | {name})
            if v is None:
                unresolvable.add(name)
                return None
            subs[ident] = v
        filled = _IDENT_RE.sub(lambda m: str(subs.get(m.group(1), m.group(1))),
                               expr)
        if not _ARITH_RE.match(filled):
            unresolvable.add(name)
            return None
        try:
            value = int(eval(filled, {'__builtins__': {}}, {}))  # arithmetic only
        except Exception:
            unresolvable.add(name)
            return None
        vals[name] = value
        return value

    # Iterative outer loop with a generous recursion budget; header arithmetic
    # nests only a handful deep in practice.
    for n in order:
        try:
            resolve(n, frozenset())
        except RecursionError:
            unresolvable.add(n)
    return vals


def flag_regions(values: dict) -> dict:
    """Region bounds for flags, read from the project's own header defines.

    Falls back to vanilla FireRed's layout for anything the project does not
    define, so a stripped-down header still classifies sensibly.
    """
    def pick(name, default):
        v = values.get(name)
        return default if v is None else v

    temp_end = pick('TEMP_FLAGS_END', 0x1F)
    hidden_start = pick('FLAG_HIDDEN_ITEMS_START', 1000)
    trainer_start = pick('TRAINER_FLAGS_START', 1280)
    trainer_end = pick('TRAINER_FLAGS_END', 2047)
    sys_start = pick('SYS_FLAGS', trainer_end + 1)
    total = pick('FLAGS_COUNT', 2304)

    # The hidden-item block ends at the last slot a FLAG_HIDDEN_ITEM_* name
    # actually occupies, not at the start of the trainer block. FireRed leaves
    # a tail of spare slots between the two (0x4A7–0x4AF in vanilla) and then
    # puts ordinary story flags, like the gym-leader clears, immediately after
    # them. Treating the whole gap as hidden-item territory would lock away
    # dozens of perfectly ordinary flags.
    named_hidden = [v for n, v in values.items()
                    if n.startswith('FLAG_HIDDEN_ITEM_')
                    and hidden_start <= v < trainer_start]
    hidden_end = max(named_hidden) if named_hidden else hidden_start - 1

    return {
        'temp_end': temp_end,
        'general_start': temp_end + 1,
        'general_end': hidden_start - 1,
        'hidden_start': hidden_start,
        'hidden_end': hidden_end,
        'trainer_start': trainer_start,
        'trainer_end': trainer_end,
        'sys_start': sys_start,
        'count': total,
    }


def var_regions(values: dict) -> dict:
    def pick(name, default):
        v = values.get(name)
        return default if v is None else v
    temp_end = pick('TEMP_VARS_END', 0x400F)
    return {
        'temp_end': temp_end,
        'general_start': temp_end + 1,
        'general_end': pick('VARS_END', 0x40FF),
        'special_start': pick('SPECIAL_VARS_START', 0x8000),
        'count': pick('VARS_COUNT', 0x100),
    }


# ── References ──────────────────────────────────────────────────────────────

def _count_references(root: Path, prefix: str, header_rel: str) -> dict:
    """One pass over the project recording every ``<prefix>NAME`` token.

    Returns ``{symbol: {...}}`` with the reference split by WHERE it lives:
    ``maps`` (map folder names), ``engine`` (``src/`` files), ``scripts``
    (shared script / data files), ``headers`` (other ``include/`` files),
    ``other``. The constant header's own ``#define`` lines are NOT counted as
    uses.
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
        parts = rel.split('/')
        if rel.startswith('src/'):
            bucket, where = 'engine', rel
        elif rel.startswith('data/maps/') and len(parts) > 2:
            bucket, where = 'maps', parts[2]
        elif rel.startswith('data/'):
            bucket, where = 'scripts', rel
        elif rel.startswith('include/'):
            bucket, where = 'headers', rel
        else:
            bucket, where = 'other', rel
        for lineno, line in enumerate(text.splitlines(), 1):
            # Skip a header definition line ("#define VAR_X ...") — that's the
            # definition, not a use.
            if is_header and line.lstrip().startswith('#define '):
                continue
            for m in token_re.finditer(line):
                sym = m.group(1)
                c = counts.get(sym)
                if c is None:
                    c = counts[sym] = {
                        'maps': set(), 'engine': set(), 'scripts': set(),
                        'headers': set(), 'other': set(), 'total': 0,
                        'samples': []}
                c[bucket].add(where)
                c['total'] += 1
                if len(c['samples']) < 8:
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


# ── Classification ──────────────────────────────────────────────────────────

def classify_flag(name: str, value, refs: dict, regions: dict) -> tuple:
    """``(category, protection, reason)`` for one flag.

    *value* may be ``None`` when the header arithmetic could not be resolved —
    an unplaceable slot is protected rather than guessed at.
    """
    n_maps = len(refs['maps'])
    n_engine = len(refs['engine'])
    n_other = len(refs['scripts']) + len(refs['headers']) + len(refs['other'])
    total = refs['total']

    if name.startswith('FLAG_TEMP_') or (value is not None
                                         and value <= regions['temp_end']):
        return ('temporary', 'protected',
                'a temporary flag, wiped every time a map loads — it is never saved')
    if _is_reserved('flag', name):
        return ('system', 'protected',
                'reserved engine plumbing — renaming it only breaks things')
    if value is None:
        return ('unknown', 'investigate',
                'its slot number could not be worked out from the header')
    if value >= regions['sys_start']:
        return ('system', 'protected',
                'in the engine\'s system block, alongside badges and progression')
    if any(name.startswith(p) for p in _PROGRESSION):
        return ('system', 'protected', 'a badge or game-progression flag')
    if regions['hidden_start'] <= value <= regions['hidden_end']:
        return ('item', 'investigate',
                'in the hidden-item block, which engine code addresses by slot '
                'number rather than by name')
    if regions['trainer_start'] <= value <= regions['trainer_end']:
        return ('trainer', 'preserve',
                'in the trainer block, which engine code addresses by trainer '
                'number rather than by name')
    if total == 0:
        if regions['hidden_end'] < value < regions['trainer_start']:
            return ('free', 'free',
                    'referenced nowhere in the project, though it sits in the '
                    'spare tail of the hidden-item block — usable, but kept '
                    'out of the pool templates draw from')
        return ('free', 'free', 'referenced nowhere in the project')
    if any(name.startswith(p) for p in _TRAINERISH):
        return ('trainer', 'preserve',
                'looks like a trainer or rematch flag — kept by default')
    if n_engine:
        return ('engine', 'protected', 'read by engine source code')
    if n_other or n_maps > 1:
        return ('shared', 'investigate',
                'used in more than one place, so taking it over would change '
                'behaviour somewhere else')
    if n_maps == 1:
        if any(name.startswith(p) for p in _ITEMISH) or name.endswith('_BALL'):
            return ('item', 'review', 'an item pickup on a single map')
        return ('event', 'review', 'a one-off event on a single map')
    return ('unknown', 'investigate', 'referenced somewhere unexpected')


def classify_var(name: str, value, refs: dict, regions: dict) -> tuple:
    n_maps = len(refs['maps'])
    n_engine = len(refs['engine'])
    n_other = len(refs['scripts']) + len(refs['headers']) + len(refs['other'])
    total = refs['total']

    if name.startswith('VAR_TEMP_') or (value is not None
                                        and value <= regions['temp_end']):
        return ('temporary', 'protected',
                'a temporary variable, wiped every time a map loads')
    if value is not None and value >= regions['special_start']:
        return ('system', 'protected',
                'a scratch variable held in RAM, not in the save file')
    if _is_reserved('var', name):
        return ('system', 'protected',
                'reserved engine plumbing — renaming it only breaks things')
    if total == 0:
        return ('free', 'free', 'referenced nowhere in the project')
    if n_engine:
        return ('engine', 'protected', 'read by engine source code')
    if n_other or n_maps > 1:
        return ('shared', 'investigate', 'used in more than one place')
    if n_maps == 1:
        return ('event', 'review', 'used by a single map')
    return ('unknown', 'investigate', 'referenced somewhere unexpected')


_EMPTY_REFS = {'maps': frozenset(), 'engine': frozenset(),
               'scripts': frozenset(), 'headers': frozenset(),
               'other': frozenset(), 'total': 0, 'samples': []}


def scan(root, kind: str) -> list:
    """Scan every var/flag slot and classify it.

    *kind* is ``'var'`` or ``'flag'``. Returns a list of dicts in header order::

        {name, slot, region,
         refs_total, maps[], engine[], scripts[], samples[],
         category, protection, reason, placeholder}

    ``slot`` is the resolved slot NUMBER (``None`` when the header arithmetic
    could not be followed); ``category`` / ``protection`` are keys into
    :data:`CATEGORIES` / :data:`PROTECTION`.
    """
    root = Path(root)
    prefix = 'VAR_' if kind == 'var' else 'FLAG_'
    header_rel = VARS_HEADER if kind == 'var' else FLAGS_HEADER
    refs = _count_references(root, prefix, header_rel)
    values = resolve_values(root, header_rel)
    regions = var_regions(values) if kind == 'var' else flag_regions(values)
    classify = classify_var if kind == 'var' else classify_flag

    out: list = []
    for name, _expr, _lineno in _parse_defs(root, prefix, header_rel):
        r = refs.get(name, _EMPTY_REFS)
        slot = values.get(name)
        category, protection, reason = classify(name, slot, r, regions)
        out.append({
            'name': name, 'slot': slot,
            'region': _region_of(kind, slot, regions),
            'refs_total': r['total'],
            'maps': sorted(r['maps']),
            'engine': sorted(r['engine']),
            'scripts': sorted(r['scripts']) + sorted(r['headers']) + sorted(r['other']),
            'samples': list(r['samples']),
            'category': category, 'protection': protection, 'reason': reason,
            'placeholder': _is_placeholder(kind, name),
        })
    return out


def _region_of(kind: str, slot, regions: dict) -> str:
    if slot is None:
        return 'unknown'
    if kind == 'var':
        if slot >= regions['special_start']:
            return 'scratch'
        if slot <= regions['temp_end']:
            return 'temporary'
        return 'general'
    if slot <= regions['temp_end']:
        return 'temporary'
    if regions['hidden_start'] <= slot <= regions['hidden_end']:
        return 'hidden items'
    if regions['hidden_start'] <= slot < regions['trainer_start']:
        # Past the last NAMED hidden item but still inside the window
        # FLAG_HIDDEN_ITEMS_START + index could reach. Ordinary flags live
        # here (FireRed's gym-leader clears, for one), so they are classified
        # on their references like anything else -- but a slot here is not
        # handed out automatically. See INDEXED_REGIONS.
        return 'hidden items (spare)'
    if regions['trainer_start'] <= slot <= regions['trainer_end']:
        return 'trainers'
    if slot >= regions['sys_start']:
        return 'system'
    return 'general'


def capacity(entries: list) -> dict:
    """How many slots each region holds and how many are still free."""
    out: dict[str, dict] = {}
    for e in entries:
        r = out.setdefault(e['region'], {'named': 0, 'free': 0})
        r['named'] += 1
        if e['protection'] == 'free':
            r['free'] += 1
    return out


# ── Naming ──────────────────────────────────────────────────────────────────

def normalize_name(kind: str, friendly: str) -> str:
    """Turn whatever the user typed into a valid ``VAR_``/``FLAG_`` C identifier.

    ``'Side Quest'`` → ``'VAR_SIDE_QUEST'``. A leading digit is prefixed with
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


# ── Renaming / reclaiming ───────────────────────────────────────────────────

def _files_mentioning(root: Path, symbol: str) -> list:
    pat = re.compile(r'\b' + re.escape(symbol) + r'\b')
    hits = []
    for path in _iter_scan_files(root):
        try:
            text = path.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            continue
        if symbol in text and pat.search(text):
            hits.append(path)
    return hits


def backup_files(root, paths, backup_root) -> str:
    """Copy *paths* into a timestamped folder under *backup_root*, keeping the
    project-relative layout. Returns the folder, or '' if nothing was copied.

    A rename rewrites files all over the game tree. Taking the snapshot first
    means the whole edit can be put back by hand if it turns out wrong.
    """
    root = Path(root)
    paths = list(paths)
    if not paths:
        return ''
    dest = Path(backup_root) / time.strftime('%Y%m%d-%H%M%S')
    for p in paths:
        p = Path(p)
        try:
            rel = p.relative_to(root)
        except ValueError:
            rel = Path(p.name)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, target)
    return str(dest)


def rename_symbol(root, old: str, new: str, backup_root=None) -> tuple:
    """Whole-word rename *old* → *new* across the whole project.

    Touches the constant header (the ``#define``) and every reference in
    src / data / include / asm. When *backup_root* is given, every file about
    to change is copied there first.

    Returns ``(files_changed, [rel_paths], backup_dir)``.
    """
    root = Path(root)
    pat = re.compile(r'\b' + re.escape(old) + r'\b')
    targets = _files_mentioning(root, old)
    backup_dir = backup_files(root, targets, backup_root) if backup_root else ''

    changed: list = []
    for path in targets:
        try:
            text = path.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            continue
        new_text = pat.sub(new, text)
        if new_text == text:
            continue
        try:
            path.write_text(new_text, encoding='utf-8', newline='\n')
        except OSError:
            continue
        try:
            changed.append(str(path.relative_to(root)).replace('\\', '/'))
        except ValueError:
            changed.append(path.name)
    return len(changed), changed, backup_dir


def first_free(entries: list) -> dict | None:
    """The first slot safe to claim for a brand-new var/flag.

    Prefers a slot that has never been given a purpose and sits outside the
    regions the engine addresses by number; only if the project has run out of
    those does it fall back to any free slot at all.
    """
    pools = (
        [e for e in entries if e['region'] not in INDEXED_REGIONS],
        list(entries),
    )
    for pool in pools:
        for e in pool:
            if e['protection'] == 'free' and e['placeholder']:
                return e
        for e in pool:
            if e['protection'] == 'free':
                return e
    return None


def free_names(entries: list) -> list:
    """Every slot name that is referenced nowhere, in header order."""
    return [e['name'] for e in entries if e['protection'] == 'free']


def auto_claim_names(entries: list) -> list:
    """Free slots that a template or picker may hand out WITHOUT asking.

    This is :func:`free_names` minus anything sitting in a region the engine
    addresses by slot number (see :data:`INDEXED_REGIONS`). Those slots are
    unreferenced and would work, but they are where the engine expands, so a
    chest template must not quietly take one. They remain reclaimable by hand
    from the Flag Manager, which shows the region.
    """
    return [e['name'] for e in entries
            if e['protection'] == 'free'
            and e['region'] not in INDEXED_REGIONS]


# ── Cached scanning ─────────────────────────────────────────────────────────
# A full scan is a second or two. The flag picker in the Event Editor asks for
# free slots every time a template is applied, so the result is memoised per
# project and dropped whenever this module changes the project itself.

_cache: dict = {}


def cached_scan(root, kind: str) -> list:
    key = (str(Path(root)), kind)
    hit = _cache.get(key)
    if hit is None:
        hit = _cache[key] = scan(root, kind)
    return hit


def invalidate_cache(root=None) -> None:
    if root is None:
        _cache.clear()
        return
    r = str(Path(root))
    for key in [k for k in _cache if k[0] == r]:
        _cache.pop(key, None)
