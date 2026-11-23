"""Regression guard: one Repair window, not a Tools menu full of repairs.

WHY THIS EXISTS. Every PorySuite bug that wrote wrong bytes into the user's
game files needs a repair they can run, and that list only grows. Each one was
getting its own Tools menu entry; by 2026-11-22 there were two ("Repair Map
Script Damage…", "Repair Overflowing Dialogue…") and the owner stopped it:
"maybe dont flood the tools menu with repair this repair that, and add a repair
popup box like settings and include them all in there? with a descriptor that
these repair bugs potentially created by previous versions of porysuite".

So repairs are registered in `core.repairs` and listed by one
`ui.dialogs.repair_dialog.RepairDialog`, reached from a single Tools → Repair…
entry.

What is guarded here:

* **The menu cannot be flooded again.** `test_tools_menu_has_exactly_one_repair_entry`
  parses `ui/unified_mainwindow.py` and fails if a second per-repair QAction
  appears. Parsed with `ast`, not matched with a regex -- a regex over source
  answers the wrong question (it sees commented-out and quoted text as real).
* **Opening the window writes nothing.** Every registered scan runs the moment
  it opens, before the user has agreed to anything, so a scan that writes would
  change the project just by looking at it.
* **A row never claims a fix that did not happen.** The window re-scans after a
  handler returns instead of assuming the handler wrote.
* **One broken detector cannot hide the others.**
"""
import ast
import os
import sys

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

pytest.importorskip('PyQt6.QtWidgets')

from PyQt6.QtWidgets import QApplication  # noqa: E402

import core.repairs as repairs  # noqa: E402
from core.repairs import (  # noqa: E402
    KEY_DIALOGUE, KEY_MAP_SCRIPTS, available_repairs, repair_by_key, scan_all,
)
from ui.dialogs.repair_dialog import RepairDialog  # noqa: E402

_app = QApplication.instance() or QApplication([])

BS = chr(92)
NL = BS + 'n'


def broken_project(root):
    """A throwaway project whose dialogue has a line the game cannot draw."""
    d = root / 'data' / 'maps' / 'SomeHouse'
    d.mkdir(parents=True, exist_ok=True)
    (d / 'text.inc').write_text(
        'SomeHouse_Text_1::\n'
        f'    .string "line one{NL}"\n'
        f'    .string "line two{NL}"\n'
        '    .string "line three$"\n',
        encoding='utf-8', newline='')
    return root


# -- the registry ------------------------------------------------------------

def test_every_repair_is_described_for_a_human():
    specs = available_repairs()
    assert specs, 'no repairs registered'
    for s in specs:
        assert s.key and s.title and s.description
        assert callable(s.scan) and callable(s.describe)
        # The owner reads these. They must not be function names or jargon.
        assert len(s.description) > 40, s.key
        assert '(' not in s.title, s.key


def test_repair_keys_are_unique():
    keys = [s.key for s in available_repairs()]
    assert len(keys) == len(set(keys)), keys


def test_repair_by_key_finds_the_registered_ones():
    assert repair_by_key(KEY_MAP_SCRIPTS) is not None
    assert repair_by_key(KEY_DIALOGUE) is not None
    assert repair_by_key('not_a_repair') is None


def test_describe_says_nothing_when_there_is_nothing():
    for s in available_repairs():
        assert s.describe([]) == ''


def test_scan_all_covers_every_repair(tmp_path):
    out = scan_all(tmp_path)
    assert set(out) == {s.key for s in available_repairs()}


def test_one_broken_detector_does_not_hide_the_others(tmp_path, monkeypatch):
    def boom(_root):
        raise RuntimeError('detector exploded')

    original = available_repairs()
    patched = tuple(
        repairs.RepairSpec(key=s.key, title=s.title,
                           description=s.description,
                           scan=(boom if s.key == KEY_MAP_SCRIPTS else s.scan),
                           describe=s.describe)
        for s in original)
    monkeypatch.setattr(repairs, '_SPECS', patched)

    out = scan_all(broken_project(tmp_path))
    assert out[KEY_MAP_SCRIPTS] is None          # reported, not raised
    assert out[KEY_DIALOGUE] and len(out[KEY_DIALOGUE]) == 1


# -- the window --------------------------------------------------------------

def test_window_lists_a_row_for_every_repair(tmp_path):
    dlg = RepairDialog(str(tmp_path), {})
    try:
        assert set(dlg._rows) == {s.key for s in available_repairs()}
    finally:
        dlg.deleteLater()


def test_opening_the_window_writes_nothing(tmp_path):
    """Every scan runs on open, before the user has agreed to anything."""
    broken_project(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    dlg = RepairDialog(str(tmp_path), {})
    try:
        after = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
        assert after == before
    finally:
        dlg.deleteLater()


def test_a_found_problem_enables_its_button_and_says_what_it_found(tmp_path):
    broken_project(tmp_path)
    dlg = RepairDialog(str(tmp_path), {KEY_DIALOGUE: lambda: None})
    try:
        row = dlg._rows[KEY_DIALOGUE]
        assert 'Found' in row['status'].text()
        assert '1 text box' in row['status'].text()
        assert row['button'].isEnabled()
    finally:
        dlg.deleteLater()


def test_a_clean_project_disables_every_button(tmp_path):
    dlg = RepairDialog(str(tmp_path), {KEY_DIALOGUE: lambda: None})
    try:
        for row in dlg._rows.values():
            assert not row['button'].isEnabled()
            assert 'Nothing to repair' in row['status'].text()
        assert 'Nothing needs repairing' in dlg._summary.text()
    finally:
        dlg.deleteLater()


def test_a_repair_with_no_handler_is_not_offered(tmp_path):
    broken_project(tmp_path)
    dlg = RepairDialog(str(tmp_path), {})        # no handlers at all
    try:
        assert not dlg._rows[KEY_DIALOGUE]['button'].isEnabled()
    finally:
        dlg.deleteLater()


def test_the_row_rescans_after_a_handler_runs(tmp_path):
    """A handler can write, cancel, or refuse. The row must re-read the files
    rather than assume it wrote."""
    broken_project(tmp_path)

    def really_repair():
        from eventide.backend.dialogue_repair import scan, repair
        repair(scan(tmp_path))

    dlg = RepairDialog(str(tmp_path), {KEY_DIALOGUE: really_repair})
    try:
        assert 'Found' in dlg._rows[KEY_DIALOGUE]['status'].text()
        dlg._run(repair_by_key(KEY_DIALOGUE))
        assert 'Nothing to repair' in dlg._rows[KEY_DIALOGUE]['status'].text()
    finally:
        dlg.deleteLater()


def test_a_handler_that_raises_does_not_take_the_window_down(tmp_path):
    broken_project(tmp_path)

    def explode():
        raise RuntimeError('flow failed')

    dlg = RepairDialog(str(tmp_path), {KEY_DIALOGUE: explode})
    try:
        dlg._run(repair_by_key(KEY_DIALOGUE))     # must not propagate
        assert 'Found' in dlg._rows[KEY_DIALOGUE]['status'].text()
    finally:
        dlg.deleteLater()


def test_the_window_explains_where_these_problems_came_from(tmp_path):
    """The owner asked for a descriptor saying these repair bugs that earlier
    PorySuite versions may have created. Keep it."""
    dlg = RepairDialog(str(tmp_path), {})
    try:
        head = next(w.text() for w in dlg.findChildren(type(dlg._summary))
                    if 'earlier' in w.text())
        assert 'earlier' in head and 'PorySuite' in head
        assert 'asks before' in head
    finally:
        dlg.deleteLater()


# -- the menu ----------------------------------------------------------------

def _qaction_labels(path):
    """First string argument of every `QAction(...)` call in *path*.

    Parsed, not pattern-matched: a regex over source counts labels inside
    comments and docstrings, which is how a lexical check like this gets a
    confidently wrong answer.
    """
    tree = ast.parse(open(path, encoding='utf-8').read())
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, 'id', '')
        if name != 'QAction' or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            out.append(first.value)
    return out


def test_tools_menu_has_exactly_one_repair_entry():
    """Fails the moment a new per-repair menu item is added. Register it in
    `core.repairs` and give it a row in the Repair window instead."""
    path = os.path.join(ROOT_DIR, 'ui', 'unified_mainwindow.py')
    labels = _qaction_labels(path)
    assert labels, 'no QAction labels parsed — did the menu move?'
    repair_labels = [s for s in labels if 'Repair' in s or 'repair' in s]
    assert repair_labels == ['Repair…'], repair_labels
