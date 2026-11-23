"""PorySuite must run wherever it is installed — no machine-specific paths.

WHY (2026-11-22). The Piano Roll's `_debug` helper opened a HARDCODED
``C:/GBA/porysuite/piano_roll_debug.log`` on the first click, inside the mouse
handler — on any machine without that exact folder, clicking a note raised
``FileNotFoundError`` mid-interaction. Renaming words in comments does not make
an application portable; paths like that one do the damage. These tests pin the
fix and add a behavioral guard so the class of bug cannot come back unnoticed.

The guard scans LIVE string literals only (docstrings excluded via ast), so
documentation examples and test fixtures that legitimately show absolute paths
are not policed. Known tool-discovery defaults with an environment override
(devkitPro's own installer location, standalone MSYS2) are allowlisted with
reasons — an allowlisted entry is a reviewed decision, not an exemption from
review.
"""

import ast
import os
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


# ── the piano roll debug log ───────────────────────────────────────────────

def _canvas():
    pytest.importorskip('PyQt6')
    from ui.piano_roll_widget import PianoRollCanvas
    return PianoRollCanvas


def test_piano_roll_debug_is_silent_and_safe_by_default(monkeypatch):
    """Disabled (the default): no file created, no exception, mouse-path safe."""
    from core import diag_log
    monkeypatch.delenv(diag_log.ENV_VAR, raising=False)
    diag_log.reset_cache()
    try:
        log = ROOT / 'piano_roll_debug.log'
        existed = log.exists()
        # `_debug` must not touch self at all when disabled — calling it with
        # no real canvas proves the mouse path cannot be broken by logging.
        _canvas()._debug(None, 'click at (1, 2)')
        assert log.exists() == existed, 'disabled diagnostics created a file'
    finally:
        diag_log.reset_cache()


def test_piano_roll_debug_path_is_derived_from_the_app_not_a_drive(monkeypatch):
    """Enabled: the destination comes from where the app IS, not C:/GBA."""
    import ui.piano_roll_widget as prw
    from core import diag_log
    monkeypatch.setenv(diag_log.ENV_VAR, '1')
    diag_log.reset_cache()
    seen = []
    monkeypatch.setattr(diag_log, 'append',
                        lambda path, line: seen.append((path, line)) or True)
    try:
        _canvas()._debug(None, 'hello')
        assert len(seen) == 1
        want = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(prw.__file__))),
            'piano_roll_debug.log')
        assert seen[0][0] == want
        assert 'hello' in seen[0][1]
    finally:
        diag_log.reset_cache()


def test_diag_append_cannot_raise_when_the_destination_is_unavailable(
        monkeypatch, tmp_path):
    """A log that cannot be written is dropped, never thrown."""
    from core import diag_log
    monkeypatch.setenv(diag_log.ENV_VAR, '1')
    diag_log.reset_cache()
    try:
        blocker = tmp_path / 'blocker'
        blocker.write_text('a file, not a folder', encoding='utf-8')
        impossible = str(blocker / 'sub' / 'x.log')   # parent is a file
        assert diag_log.append(impossible, 'line') is False
    finally:
        diag_log.reset_cache()


# ── external-tool discovery does not assume one developer's filesystem ─────

def test_devkitpro_discovery_follows_the_env_var_to_another_drive(monkeypatch):
    from core import programsetup as ps
    fake = {
        r'D:\dkp\devkitARM\bin\arm-none-eabi-gcc.exe',
        r'D:\dkp\msys2\usr\bin\bash.exe',
    }
    monkeypatch.setattr(os.path, 'isfile', lambda p: p in fake)
    monkeypatch.setenv('DEVKITPRO', r'D:\dkp')
    assert ps._find_devkitpro() == r'D:\dkp'
    # And bash discovery follows it there, rather than only probing C:.
    assert ps._find_bash() == r'D:\dkp\msys2\usr\bin\bash.exe'


def test_devkitpro_msys_style_env_var_works_for_any_drive(monkeypatch):
    """`DEVKITPRO=/d/dkp` (MSYS spelling) used to be skipped: only /c/ was
    converted. Any drive letter converts now; /opt/… is still rejected."""
    from core import programsetup as ps
    gcc = r'D:\dkp\devkitARM\bin\arm-none-eabi-gcc.exe'
    monkeypatch.setattr(os.path, 'isfile', lambda p: p == gcc)
    monkeypatch.setenv('DEVKITPRO', '/d/dkp')
    assert ps._find_devkitpro() == r'D:\dkp'
    monkeypatch.setenv('DEVKITPRO', '/opt/devkitpro')
    assert ps._find_devkitpro() == ''


# ── the behavioral guard ───────────────────────────────────────────────────

#: (file, literal-prefix, why) — reviewed, env-overridable discovery defaults.
_ALLOWED = (
    ('core/programsetup.py', 'C:\\devkitPro',
     "devkitPro's own installer default; DEVKITPRO env var overrides it"),
    ('core/programsetup.py', 'C:\\msys64',
     'standalone MSYS2 installer default; shutil.which is the fallback'),
    ('ui/diagnostics_tab.py', 'C:\\devkitpro',
     'diagnostic probe of the installer default; DEVKITARM env var first'),
    ('ui/diagnostics_tab.py', 'C:\\devkitPro',
     'diagnostic probe of the installer default; DEVKITARM env var first'),
    ('ui/mainwindow.py', 'devkitPro was not found',
     'user-facing message that NAMES the default location it probed'),
    ('ui/mainwindow.py', 'MSYS2 was not found',
     'user-facing message that NAMES the default location it probed'),
    ('ui/mainwindow.py', 'C:\\Program Files',
     'Git for Windows installer defaults; falls back to "git" on PATH'),
    ('ui/unified_mainwindow.py', 'C:\\Program Files',
     'Git for Windows installer defaults; falls back to "git" on PATH'),
    ('eventide/mainwindow.py', 'C:\\Program Files',
     'Git for Windows installer defaults; falls back to "git" on PATH'),
    ('core/programsetup.py', 'C:\\Windows\\System32\\wsl.exe',
     'canonical WSL location, probed only after shutil.which("wsl")'),
    ('core/programsetup.py', 'ARM GBA toolchain (devkitARM)',
     'setup-wizard text that NAMES the recommended install location'),
)

#: A single drive letter followed by :\ or :/ — `https://…` has letters
#: before the colon, so URLs do not match.
_DRIVE = re.compile(r'(?<![A-Za-z0-9])[A-Za-z]:[\\/]')


def _docstring_ids(tree):
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef,
                             ast.AsyncFunctionDef, ast.ClassDef)):
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                out.add(id(node.body[0].value))
    return out


def test_no_machine_specific_paths_in_live_strings():
    """No absolute drive path in any executable string literal of the app.

    Scans core/, ui/, eventide/, porymap_bridge/, enginehost/ and app.py.
    Docstrings are skipped (examples are fine); tests/ is not scanned (fixtures
    may simulate any filesystem they like). Passing the project-name guard is
    NOT evidence of portability — this is the check that actually looks."""
    offenders, scanned = [], 0
    surfaces = ['core', 'ui', 'eventide', 'porymap_bridge', 'enginehost']
    files = [ROOT / 'app.py']
    for d in surfaces:
        files += sorted((ROOT / d).rglob('*.py'))
    for path in files:
        if '__pycache__' in path.parts or not path.is_file():
            continue
        scanned += 1
        rel = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding='utf-8', errors='replace'))
        ds = _docstring_ids(tree)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and id(node) not in ds and _DRIVE.search(node.value)):
                if any(rel == f and node.value.startswith(p)
                       for f, p, _ in _ALLOWED):
                    continue
                offenders.append(f'{rel}:{node.lineno}: {node.value[:80]!r}')
    assert scanned > 200, f'only scanned {scanned} files; the walk is wrong'
    assert not offenders, (
        'machine-specific path in live application code — derive it from the '
        'app location, the loaded project, or a configurable discovery '
        'default:\n' + '\n'.join(offenders))
