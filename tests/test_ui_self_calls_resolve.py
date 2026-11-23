"""Structural guard: every ``self._private_method()`` call in the UI must exist.

THE BUG THIS CATCHES (found 2026-11-22, fixed 2026-11-22). The 2026-10-22
dead-code sweep (commit 181629b, "Remove the non-functional per-box Front/Back
Import buttons + their dead code") deleted twelve methods from
``ui/graphics_tab_widget.py``. Seven really were dead. FIVE still had live
callers:

* ``_icon_target_slot``            (the Import Menu Icon confirmation)
* ``_refresh_imported_icon_preview`` (Import Menu Icon, and the icon-palette combo)
* ``_remapped_imported_icon``      (the icon save loop)
* ``_reindex_existing_icon``       (the icon save loop)
* ``_current_indexed_for``         (deriving a shiny palette from a sprite PNG)

Python only notices a missing method when the line runs, and both call sites
are inside handlers whose exceptions are swallowed:
``MainWindow._save`` wraps ``graphics_tab_widget.flush_to_disk()`` in
``except Exception: logging.exception(...)``, and Qt swallows a raise out of a
slot. So the two user-visible symptoms were SILENT:

1. "Import Menu Icon..." opened its file picker and then did nothing at all.
2. Switching a species between the three shared mini/icon palettes and saving
   never rewrote its ``icon.png`` (and, because the icon loop aborted the whole
   flush, pending footprints were not written either).

A name-based check is not enough here, which is why this test asks ``ast``
rather than a regex: it resolves each call against the methods and the
``self.x = ...`` attributes actually defined in the class and in its
same-file base classes, and it honours ``hasattr(self, "name")`` guards.

Scope is every module under ``ui/``, ``eventide/ui/``, ``core/`` and
``eventide/backend/``, so the next dead-code sweep anywhere in the app cannot
delete a live method unnoticed. ``core/`` and ``eventide/backend/`` were clean
when this was written (measured) and are included to keep them that way. Only
underscore-private names are checked — a public ``self.foo()`` may legitimately
come from PyQt's C++ base classes, which ``ast`` cannot see.
"""

import ast
import os

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Calls that are deliberately optional: guarded by ``hasattr`` at the call
#: site AND intended to be absent. Anything else must resolve.
_ALLOWED_ABSENT = {
    # mainwindow.py::_set_female_art_ui logs a traceback through an optional
    # diagnostics hook that this build does not install. Written as
    # ``self._log_diag(...) if hasattr(self, "_log_diag") else None``.
    ("MainWindow", "_log_diag"),
}


def _sources():
    """Every app module worth auditing, as repo-relative-ish absolute paths."""
    out = []
    for rel in ("ui", os.path.join("eventide", "ui"),
                "core", os.path.join("eventide", "backend")):
        root = os.path.join(_ROOT, rel)
        if not os.path.isdir(root):
            continue
        for base, dirs, files in os.walk(root):
            dirs[:] = sorted(d for d in dirs if d != "__pycache__")
            for name in sorted(files):
                if name.endswith(".py"):
                    out.append(os.path.join(base, name))
    return out


def _defined_names(node):
    """Method names, and ``self.x``/``x`` assignments, inside one class body."""
    names = set()
    for b in ast.walk(node):
        if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(b.name)
        elif isinstance(b, ast.Assign):
            targets = []
            for t in b.targets:
                targets.extend(t.elts if isinstance(t, ast.Tuple) else [t])
            for t in targets:
                if (isinstance(t, ast.Attribute)
                        and isinstance(t.value, ast.Name)
                        and t.value.id == "self"):
                    names.add(t.attr)
                elif isinstance(t, ast.Name):
                    names.add(t.id)
        elif isinstance(b, (ast.AnnAssign, ast.For)):
            t = b.target
            if (isinstance(t, ast.Attribute)
                    and isinstance(t.value, ast.Name)
                    and t.value.id == "self"):
                names.add(t.attr)
            elif isinstance(t, ast.Name):
                names.add(t.id)
    return names


def _hasattr_guarded(node):
    """Names appearing in a ``hasattr(self, "name")`` test in this class."""
    guarded = set()
    for b in ast.walk(node):
        if (isinstance(b, ast.Call) and isinstance(b.func, ast.Name)
                and b.func.id == "hasattr" and len(b.args) == 2):
            obj, attr = b.args
            if (isinstance(obj, ast.Name) and obj.id == "self"
                    and isinstance(attr, ast.Constant)
                    and isinstance(attr.value, str)):
                guarded.add(attr.value)
    return guarded


def _unresolved(path):
    """Return [(class, method, [lines])] for self._x() calls with no definition."""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())

    classes = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}

    def resolve(cname, seen=()):
        if cname in seen or cname not in classes:
            return set()
        node = classes[cname]
        names = _defined_names(node)
        for base in node.bases:
            names |= resolve(ast.unparse(base).split(".")[-1], seen + (cname,))
        return names

    findings = []
    for cname, node in classes.items():
        # A class that builds attributes through setattr() is too dynamic to
        # audit this way; skip it rather than report a false positive.
        if any(isinstance(b, ast.Call) and isinstance(b.func, ast.Name)
               and b.func.id == "setattr" for b in ast.walk(node)):
            continue
        defined = resolve(cname) | _hasattr_guarded(node)
        calls = {}
        for b in ast.walk(node):
            if (isinstance(b, ast.Call) and isinstance(b.func, ast.Attribute)
                    and isinstance(b.func.value, ast.Name)
                    and b.func.value.id == "self"
                    and b.func.attr.startswith("_")
                    and not b.func.attr.startswith("__")):
                calls.setdefault(b.func.attr, set()).add(b.lineno)
        for name, lines in sorted(calls.items()):
            if name in defined or (cname, name) in _ALLOWED_ABSENT:
                continue
            findings.append((cname, name, sorted(lines)))
    return findings


@pytest.mark.parametrize("path", _sources(),
                         ids=lambda p: os.path.relpath(p, _ROOT))
def test_every_private_self_call_resolves(path):
    """No class may call a private method of its own that does not exist."""
    findings = _unresolved(path)
    assert not findings, "\n".join(
        f"{os.path.relpath(path, _ROOT)}: {cls}.{name}() called at line(s) "
        f"{lines} but {name} is not defined in {cls} or its base classes"
        for cls, name, lines in findings)


def test_graphics_tab_icon_helpers_are_present():
    """The five methods commit 181629b deleted while they were still live.

    Named explicitly so a future sweep that removes one gets a failure that
    says which feature breaks, not just "a name is missing"."""
    import ui.graphics_tab_widget as mod

    cls = mod.GraphicsTabWidget
    needed = {
        "_icon_target_slot":
            "Import Menu Icon + the icon-palette save both need it",
        "_refresh_imported_icon_preview":
            "the icon preview after an import / palette-slot change",
        "_remapped_imported_icon":
            "writing a newly imported icon.png on save",
        "_reindex_existing_icon":
            "rewriting icon.png after an icon-palette slot change",
        "_current_indexed_for":
            "deriving a shiny palette from an imported sprite PNG",
    }
    missing = {n: why for n, why in needed.items()
               if not callable(getattr(cls, n, None))}
    assert not missing, "GraphicsTabWidget is missing: " + "; ".join(
        f"{n} ({why})" for n, why in sorted(missing.items()))

    # The helper they depend on must be imported into the module too — the
    # same sweep removed `remap_to_palette` from the import list.
    assert callable(getattr(mod, "remap_to_palette", None)), (
        "ui.graphics_tab_widget must import remap_to_palette from "
        "core.gba_image_utils — _remapped_imported_icon calls it")


# ─────────────────────────────────────────────────────────────────────────────
# Guard 2: a local import must never shadow a name the same function
# already used.
#
# THE BUG THIS CATCHES (shipped 2026-11-22, killed every launch): the
# Suggest-EV-Yield block added late in ``MainWindow.__init__`` did
# ``from PyQt6.QtWidgets import QPushButton`` inside its try — which makes
# QPushButton a LOCAL for the entire 2000-line __init__, so the
# pre-existing ``QPushButton("↻ Reset")`` two hundred lines EARLIER raised
# UnboundLocalError before the window ever appeared. No test constructs
# the full MainWindow, so only a real launch could catch it — until this.
#
# The rule: inside one function scope, if a name's ONLY bindings are
# function-local imports, no load of that name may appear on an earlier
# line than the first such import. Nested functions/lambdas/comprehensions
# are separate scopes and are skipped (their bodies resolve at call time).
# ─────────────────────────────────────────────────────────────────────────────

_SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


def _walk_same_scope(node):
    """Yield nodes of *node*'s body without descending into nested scopes
    or comprehensions (each of which is its own Python scope)."""
    skip = _SCOPE_NODES + (ast.ListComp, ast.SetComp, ast.DictComp,
                           ast.GeneratorExp)
    stack = list(ast.iter_child_nodes(node))
    while stack:
        n = stack.pop()
        yield n
        if not isinstance(n, skip):
            stack.extend(ast.iter_child_nodes(n))


def _shadowing_imports(path):
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    problems = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        import_lines = {}   # name -> first local-import line
        other_bound = set(a.arg for a in fn.args.args)
        other_bound |= {a.arg for a in fn.args.kwonlyargs}
        other_bound |= {a.arg for a in fn.args.posonlyargs}
        if fn.args.vararg:
            other_bound.add(fn.args.vararg.arg)
        if fn.args.kwarg:
            other_bound.add(fn.args.kwarg.arg)
        loads = {}          # name -> first load line
        # NOTE: _walk_same_scope yields nodes in stack order, NOT source
        # order — always min() the line numbers. The first version of this
        # guard used setdefault ("first encountered") and silently missed
        # the very launch-killing bug it was written for; the fix was only
        # caught because the guard was fed the broken shape afterwards.
        for n in _walk_same_scope(fn):
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                for alias in n.names:
                    name = (alias.asname or alias.name).split(".")[0]
                    import_lines[name] = min(
                        n.lineno, import_lines.get(name, n.lineno))
            elif isinstance(n, ast.Name):
                if isinstance(n.ctx, ast.Load):
                    loads[n.id] = min(n.lineno, loads.get(n.id, n.lineno))
                else:   # Store/Del: an ordinary binding
                    other_bound.add(n.id)
            elif isinstance(n, _SCOPE_NODES):
                # a nested def/lambda binds its own name in THIS scope
                name = getattr(n, "name", None)
                if name:
                    other_bound.add(name)
        for name, imp_line in import_lines.items():
            if name in other_bound:
                continue    # also bound some other way — not our pattern
            use = loads.get(name)
            if use is not None and use < imp_line:
                problems.append(
                    f"{os.path.relpath(path, _ROOT)}:{use} — "
                    f"{fn.name}() loads {name!r} at line {use}, but the "
                    f"name is only bound by a local import at line "
                    f"{imp_line}; at runtime the earlier line raises "
                    f"UnboundLocalError. Use the module-level import or "
                    f"move the local import above every use.")
    return problems


@pytest.mark.parametrize("path", _sources(),
                         ids=lambda p: os.path.relpath(p, _ROOT))
def test_no_local_import_shadows_an_earlier_use(path):
    problems = _shadowing_imports(path)
    assert not problems, "\n".join(problems)
