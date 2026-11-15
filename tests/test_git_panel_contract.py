"""The Git panel must only call methods its host window actually has.

The bug this guards against
---------------------------
``ui/dialogs/git_panel.py`` talks to whichever main window opened it through
``self._mw.<name>(...)``. Two windows can open it: the legacy
``ui/mainwindow.py::MainWindow`` and the unified PorySuite-Z window
``ui/unified_mainwindow.py::UnifiedMainWindow`` — the one people actually use.

v0.1.41b made the Commit list call ``self._mw._git_status_entries()`` and wrote
that method on the legacy window only. In PorySuite-Z the call raised
AttributeError inside ``GitPanel.refresh()``, which runs its sections in order,
so everything after the Commit list — Branches, Stash, History, Remotes —
silently never filled in. It looked like the git setup was empty.

Neither window module can be imported under the test suite (see
docs/BUGS.md: importing ``ui/mainwindow.py`` exits the interpreter), so this
reads the source with ``ast`` instead: every ``self._mw.X`` in the panel must be
a method or attribute assigned on BOTH window classes.
"""

from __future__ import annotations

import ast
import os

import pytest

from core.git_status import parse_porcelain_z

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PANEL = os.path.join(_ROOT, "ui", "dialogs", "git_panel.py")
_WINDOWS = {
    "MainWindow": os.path.join(_ROOT, "ui", "mainwindow.py"),
    "UnifiedMainWindow": os.path.join(_ROOT, "ui", "unified_mainwindow.py"),
}


def _parse(path: str) -> ast.Module:
    with open(path, encoding="utf-8") as f:
        return ast.parse(f.read(), filename=path)


def _panel_calls() -> "set[str]":
    """Every NAME in a ``self._mw.NAME`` expression inside the panel."""
    names: "set[str]" = set()
    for node in ast.walk(_parse(_PANEL)):
        if (isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Attribute)
                and node.value.attr == "_mw"
                and isinstance(node.value.value, ast.Name)
                and node.value.value.id == "self"):
            names.add(node.attr)
    return names


def _class_members(path: str, class_name: str) -> "set[str]":
    """Methods defined on the class, plus every ``self.X = ...`` it assigns."""
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            members: "set[str]" = set()
            for sub in ast.walk(node):
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    members.add(sub.name)
                elif isinstance(sub, ast.Attribute) and isinstance(sub.ctx, ast.Store) \
                        and isinstance(sub.value, ast.Name) and sub.value.id == "self":
                    members.add(sub.attr)
            return members
    raise AssertionError(f"class {class_name} not found in {path}")


def test_panel_calls_are_found_at_all():
    # A parser that sees nothing would pass the real test vacuously.
    calls = _panel_calls()
    assert "_git_run" in calls and "_git_status_entries" in calls, calls


@pytest.mark.parametrize("class_name", sorted(_WINDOWS))
def test_every_panel_call_exists_on_the_window(class_name):
    members = _class_members(_WINDOWS[class_name], class_name)
    missing = sorted(_panel_calls() - members)
    assert not missing, (
        f"git_panel.py calls self._mw.{missing} but {class_name} has no such "
        f"member — the panel's refresh would raise AttributeError and leave "
        f"its later sections empty."
    )


def _panel_call_sites() -> "list[tuple[str, int, set[str], int]]":
    """(name, positional-arg count, keyword names, line) for every self._mw.NAME(...) call."""
    sites = []
    for node in ast.walk(_parse(_PANEL)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "_mw"
                and isinstance(node.func.value.value, ast.Name)
                and node.func.value.value.id == "self"):
            if any(isinstance(a, ast.Starred) for a in node.args) or \
                    any(k.arg is None for k in node.keywords):
                continue   # *args / **kwargs at the call site: can't check statically
            sites.append((node.func.attr, len(node.args),
                          {k.arg for k in node.keywords}, node.lineno))
    return sites


def _class_defs(path: str, class_name: str) -> "dict[str, ast.FunctionDef]":
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {f.name: f for f in node.body
                    if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))}
    raise AssertionError(f"class {class_name} not found in {path}")


def _accepts(fn: ast.FunctionDef, n_pos: int, kws: "set[str]") -> "str | None":
    """None if fn(self, <n_pos positionals>, **kws) binds; else why not."""
    a = fn.args
    params = [p.arg for p in (a.posonlyargs + a.args)][1:]   # drop self
    if n_pos > len(params) and a.vararg is None:
        return f"takes {len(params)} positional argument(s), the panel passes {n_pos}"
    names = set(params[n_pos:]) | {p.arg for p in a.kwonlyargs}
    bad = sorted(k for k in kws if k not in names)
    if bad and a.kwarg is None:
        return f"has no parameter {bad}"
    return None


@pytest.mark.parametrize("class_name", sorted(_WINDOWS))
def test_every_panel_call_binds_to_the_window_signature(class_name):
    # Existence is not enough: on 2026-11-14 the Switch button called
    # _git_checkout_branch(branch, remote_only=...) and the unified window's
    # method took only (branch) — TypeError on every click, nothing switched.
    defs = _class_defs(_WINDOWS[class_name], class_name)
    problems = []
    for name, n_pos, kws, line in _panel_call_sites():
        fn = defs.get(name)
        if fn is None:
            continue   # not a method (or inherited) — the existence test covers it
        why = _accepts(fn, n_pos, kws)
        if why:
            problems.append(f"git_panel.py:{line} self._mw.{name}(...) — {class_name}.{name} {why}")
    assert not problems, "\n".join(problems)


def test_signature_check_catches_an_unknown_keyword():
    # The mutation that must break it: the exact pre-fix signature.
    fn = ast.parse("def _git_checkout_branch(self, branch): pass").body[0]
    assert _accepts(fn, 1, {"remote_only"}) is not None
    fn = ast.parse("def _git_checkout_branch(self, branch, remote_only=False): pass").body[0]
    assert _accepts(fn, 1, {"remote_only"}) is None


def test_contract_check_catches_a_missing_method():
    # The mutation that must break it: a name no window has.
    members = _class_members(_WINDOWS["UnifiedMainWindow"], "UnifiedMainWindow")
    assert "_definitely_not_a_git_method" not in members
    assert "_git_status_entries" in members


# ── switching only switches ───────────────────────────────────────────────────

def _git_verbs_and_dialogs(fn: ast.FunctionDef) -> "tuple[set[str], set[str]]":
    """(first string arg of every self._git_run(...), every _MB/QMessageBox.X called)."""
    verbs, dialogs = set(), set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        f = node.func
        if f.attr == "_git_run" and node.args and isinstance(node.args[0], ast.Constant):
            verbs.add(node.args[0].value)
        if isinstance(f.value, ast.Name) and f.value.id in ("_MB", "QMessageBox"):
            dialogs.add(f.attr)
    return verbs, dialogs


def test_switch_branch_never_updates_or_asks_afterwards():
    # 2026-11-15: after a switch, PorySuite asked "Update to latest?" and
    # fast-forwarded the branch from GitHub. The owner: people have their own
    # plan when they switch — switching must only switch; updating is Pull's job.
    defs = _class_defs(_WINDOWS["MainWindow"], "MainWindow")
    verbs, dialogs = _git_verbs_and_dialogs(defs["_git_finish_switch"])
    assert "branch" in verbs, verbs          # it still wires up tracking
    assert not verbs & {"merge", "pull", "rebase", "reset"}, verbs
    assert not dialogs, f"_git_finish_switch pops up {dialogs} after switching"
    verbs, _ = _git_verbs_and_dialogs(defs["_git_checkout_branch"])
    assert not verbs & {"merge", "pull", "rebase", "reset"}, verbs


def test_switch_guard_catches_the_old_offer():
    # Mutation: the removed code must trip the guard.
    old = ast.parse(
        "def _git_finish_switch(self, branch, d):\n"
        "    self._git_run('branch', '--set-upstream-to=x', branch)\n"
        "    if _MB.question(self, 't', 'm') == 1:\n"
        "        self._git_run('merge', '--ff-only', 'origin/x')\n").body[0]
    verbs, dialogs = _git_verbs_and_dialogs(old)
    assert "merge" in verbs and "question" in dialogs


# ── the status parser itself ──────────────────────────────────────────────────

def test_paths_with_spaces_come_back_verbatim():
    out = " M src/a.c\x00?? concept/UI/trainer card concept.png\x00"
    assert parse_porcelain_z(out) == [
        (" M", "src/a.c"),
        ("??", "concept/UI/trainer card concept.png"),
    ]


def test_rename_skips_the_origin_field():
    out = "R  new name.c\x00old name.c\x00 D gone.c\x00"
    assert parse_porcelain_z(out) == [("R ", "new name.c"), (" D", "gone.c")]


def test_leading_space_status_is_kept():
    assert parse_porcelain_z(" M x\x00")[0][0] == " M"


def test_empty_output():
    assert parse_porcelain_z("") == []
    assert parse_porcelain_z(None) == []
