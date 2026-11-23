"""PorySuite is a generic pokefirered editor: no one game's names in what it ships.

Many creators use PorySuite on their own hacks, so the app itself -- its code,
its comments, the words it shows (labels, tooltips, help, dialogs) and its
public docs -- must not name or assume one particular game's maps, items,
routines or concepts. What a project adds for itself lives in that project's
own files (its macros, its My Templates store, its scripts).

What is NOT scanned: ``tests/``. Those tests belong to the maintainer and run
against their own project, so they may use its songs, species, maps and
instruments. Renaming them is not part of this rule.

The names to watch for come from a PRIVATE, git-ignored list
(docs/PRIVATE_PROJECT_NAMES.txt), so this public file does not leak them
itself. On a checkout without that list there is nothing to check against,
and the guard skips rather than pretending to pass.
"""

import pathlib
import re
import subprocess

import pytest

_REPO = pathlib.Path(__file__).resolve().parent.parent
_NAME_LIST = _REPO / 'docs' / 'PRIVATE_PROJECT_NAMES.txt'
_SUFFIXES = ('.py', '.md', '.ui', '.json', '.txt')


def _project_name_patterns():
    if not _NAME_LIST.is_file():
        return []
    out = []
    for line in _NAME_LIST.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            out.append(re.compile(line))
    return out


def _shipped_files():
    """Every file PorySuite ships: tracked or new, never git-ignored, minus tests/
    and the game folder. Private notes are git-ignored, so they drop out here."""
    out = subprocess.run(
        ['git', 'ls-files', '--cached', '--others', '--exclude-standard'],
        cwd=_REPO, capture_output=True, text=True, check=True).stdout
    files = []
    for rel in out.splitlines():
        top = rel.split('/', 1)[0]
        if top in ('tests', 'pokefirered') or not rel.endswith(_SUFFIXES):
            continue
        path = _REPO / rel
        if path.is_file():
            files.append(path)
    return files


def test_no_project_specific_names_ship_in_porysuite():
    patterns = _project_name_patterns()
    if not patterns:
        pytest.skip('no private name list on this checkout')
    found = []
    files = _shipped_files()
    for path in files:
        text = path.read_text(encoding='utf-8', errors='replace')
        for n, line in enumerate(text.splitlines(), 1):
            for pat in patterns:
                if pat.search(line):
                    found.append(f'{path.relative_to(_REPO)}:{n}: '
                                 f'{pat.pattern!r} in {line.strip()[:80]!r}')
                    break
    assert len(files) > 200, f'only scanned {len(files)} files; the listing is wrong'
    assert not found, ("one game's own names in PorySuite's code or docs -- "
                       'use vanilla or invented names:\n' + '\n'.join(found))


def test_the_guard_above_would_actually_catch_something():
    """A scan that silently matches nothing is worse than no scan."""
    patterns = _project_name_patterns()
    if not patterns:
        pytest.skip('no private name list on this checkout')
    # The first line of the list is a literal name; it must trip its own rule.
    literal = re.sub(r'\\b|\(\?i\)', '', patterns[0].pattern)
    assert patterns[0].search(f'BAD = "{literal}SOMETHING"')
    assert len(patterns) > 10


def test_tests_are_not_scanned():
    """The maintainer's tests may use their own project's names."""
    assert not any(p.relative_to(_REPO).parts[0] == 'tests' for p in _shipped_files())
