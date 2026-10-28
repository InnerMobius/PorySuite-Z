"""Stop one test file's fake modules reaching the next one.

Eleven files in here import GUI code without a Qt runtime by putting
hand-built stand-ins into ``sys.modules`` — not just ``PyQt6`` but the whole
``ui`` package, ``mainwindow``, and several dialog modules. Twenty-five other
files need the REAL PyQt6.

That works a file at a time and cannot work in one shared process. pytest
imports every test module during collection, before running anything, so by
the time the first test runs ``sys.modules`` holds whatever the last-collected
file left there. Files wanting real Qt get a stand-in; files wanting the
stand-in sometimes get another file's. Measured before this existed:
``pytest tests`` reported 169 failures and 135 errors, while the same tests run
one file at a time were 981 passed, 0 failed.

The stubs are installed with ``setdefault``, so they are deliberately polite —
they only fill a gap. The problem is purely that the gap stays filled.

So: snapshot the sensitive parts of ``sys.modules`` around each test module's
import, and put them back afterwards. Anything the module added is removed and
anything it replaced is restored, which leaves the next module to import into
the same clean state it would have had on its own. The test module itself
keeps direct references to whatever it imported, so its own tests are
unaffected by the cleanup.

This is not as thorough as a process per file (``pytest-forked``, or
``pytest-xdist --dist loadfile``), which is the usual answer. It needs no
plugin, which is why it is here.
"""

import sys

import pytest

#: Exactly the namespaces the stub files stand in for, and nothing else.
#:
#: Scope matters in both directions. Too little and a stub survives into the
#: next file. Too much and modules nobody faked get evicted and re-imported
#: with fresh class-level state, so a test reading what another file set up
#: finds an empty list — 10 tests failed that way when `eventide` was in here,
#: and `eventide` is never faked by anything.
_VOLATILE_PREFIXES = (
    'PyQt6',
    'ui',
    'core',
)

#: Bare module names the stub files inject, which belong to the same problem
#: but do not sit under a package prefix.
_VOLATILE_NAMES = frozenset({
    'mainwindow',
    'app_util',
    'newproject',
    'exportingwindow',
    'plugininfodialog',
    'local_env',
    'git_push_errors',
    'battle_anim_data',
    'battle_anim_script',
    'battle_anim_tasks',
    'battle_anim_vm',
    'battle_mon_ref',
})


def _is_volatile(name: str) -> bool:
    if name in _VOLATILE_NAMES:
        return True
    return any(name == p or name.startswith(p + '.')
               for p in _VOLATILE_PREFIXES)


def _snapshot() -> tuple:
    """Everything a test module can change that another one would feel.

    `sys.meta_path` matters as much as `sys.modules` here: the stub helper
    installs an import finder that MANUFACTURES any `PyQt6.Qt*` module on
    demand. Clearing sys.modules alone achieves nothing while that finder is
    still in place — the next `from PyQt6.QtWidgets import ...` simply has
    another fake made for it. That is why cleaning only sys.modules left a
    real-Qt file still holding a stand-in QApplication.
    """
    return ({k: v for k, v in sys.modules.items() if _is_volatile(k)},
            list(sys.meta_path))


def _restore(before: tuple) -> None:
    """Undo this module's additions and replacements, leave everything else."""
    mods, meta = before
    for name in [k for k in sys.modules if _is_volatile(k)]:
        if name not in mods:
            del sys.modules[name]                 # it added this
        elif sys.modules[name] is not mods[name]:
            sys.modules[name] = mods[name]        # it replaced this
    for name, mod in mods.items():                # it deleted this
        sys.modules.setdefault(name, mod)
    if sys.meta_path != meta:
        sys.meta_path[:] = meta


#: Module node id -> the volatile world that module created while importing.
_ENVS: dict = {}


class _IsolatedModule(pytest.Module):
    """A test module that imports into its own world and gives it back.

    `_getobj` is where pytest actually imports the file. It was
    `_importtestmodule` in older pytest; overriding that name silently does
    nothing here, which looks exactly like the isolation not working.

    The import is bracketed rather than just cleaned up afterwards. Clearing
    the stubs straight after the import is not enough, because the module's
    TESTS run later — pytest imports everything during collection and only
    then starts running — so a file would find the stubs it set up gone by the
    time it needed them. Five tests failed that way, including when run alone.

    So the state each module creates is kept, and swapped back in around that
    module's own tests.
    """

    def _getobj(self):
        before = _snapshot()
        try:
            return super()._getobj()
        finally:
            _ENVS[self.nodeid] = _snapshot()
            _restore(before)


def pytest_pycollect_makemodule(module_path, parent):
    return _IsolatedModule.from_parent(parent, path=module_path)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    """Run each test in the world its own file imported into."""
    module = item.getparent(pytest.Module)
    env = _ENVS.get(module.nodeid) if module is not None else None
    if env is None:
        yield
        return
    saved = _snapshot()
    _restore(env)
    try:
        yield
    finally:
        _restore(saved)
