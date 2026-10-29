"""A failed Items save must never look like a successful one.

THE DEFECT (confirmed 2026-10-29). Both places that save items wrapped the
work in a blanket swallow:

    ui/mainwindow.py  update_main_tabs()   try: ... except AttributeError: pass
    ui/mainwindow.py  update_save()        try: save_items_table()
                                                items_editor.save_icon_changes()
                                           except Exception: pass

so if `save_items_table()` raised, the user's pending item edits were silently
discarded — on a tab switch, or during the main save. `update_save()` is what
Save All records as "PorySuite data", so the failure was still reported as
**saved**. And `save_icon_changes()` returns a bool that BOTH call sites threw
away, making a failed `item_icon_table.h` write invisible.

THE REPAIR, deliberately narrow: one shared `_flush_items_for_save()` that both
paths call. It records what happened on `self._item_save_errors` instead of
discarding it, leaves the editor's pending edits in place, and lets Save All
read an accurate outcome. The blanket handlers around the OTHER editors are
left exactly as they were — this is not a rewrite of update_save.

DO NOT replace the specific handling with another bare `except: pass`, and do
not let a failing Items save stop the other editors from saving.
"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "core"), os.path.join(_ROOT, "ui")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PyQt6.QtWidgets import QApplication
    _QT = True
except Exception:
    _QT = False
if _QT:
    _APP = QApplication.instance() or QApplication([])

pytestmark = pytest.mark.skipif(not _QT, reason="PyQt6 not available")


# ── a stand-in carrying the REAL method ─────────────────────────────────────

class _Editor:
    def __init__(self, icons_ok=True, icon_raises=False):
        self._icons_ok = icons_ok
        self._icon_raises = icon_raises
        self.icon_calls = 0
        self.pending = {"ITEM_POTION": {"price": 999}}   # the user's edits

    def save_icon_changes(self):
        self.icon_calls += 1
        if self._icon_raises:
            raise OSError("item_icon_table.h is read-only")
        return self._icons_ok

    def has_icon_changes(self):
        return True


def _stand(flush_raises=None, **kw):
    """Bind MainWindow._flush_items_for_save to a minimal object."""
    import ui.mainwindow as mwmod

    class Stand:
        _flush_items_for_save = mwmod.MainWindow._flush_items_for_save

        def __init__(self):
            self.items_editor = _Editor(**kw)
            self.logged = []
            self._item_save_errors = []

        def save_items_table(self):
            if flush_raises is not None:
                raise flush_raises

        def log_message(self, m):
            self.logged.append(m)

    return Stand()


# ── 1. the method exists and both paths use it ──────────────────────────────

def test_a_single_shared_flush_exists():
    import ui.mainwindow as mwmod
    assert hasattr(mwmod.MainWindow, "_flush_items_for_save"), (
        "both save paths must go through one place that records failures")


def test_both_save_paths_use_it():
    src = open(os.path.join(_ROOT, "ui", "mainwindow.py"),
               encoding="utf-8", errors="replace").read()
    import re
    for fn in ("update_main_tabs", "update_save"):
        m = re.search(r"\n    def " + fn + r"\(.*?(?=\n    def )", src, re.S)
        assert m, fn
        assert "_flush_items_for_save()" in m.group(0), (
            f"{fn} must flush items through the shared recorder, not inline "
            f"inside a blanket exception handler")


# ── 2. a failure is recorded, not swallowed ─────────────────────────────────

def test_a_raising_item_save_is_recorded():
    """THE DEFECT: this vanished into `except Exception: pass`."""
    s = _stand(flush_raises=OSError("disk full"))
    s._flush_items_for_save()
    assert s._item_save_errors, "the failure was discarded"
    assert any("disk full" in e for e in s._item_save_errors), \
        s._item_save_errors


def test_a_raising_item_save_does_not_propagate():
    """It must not abort the caller — other editors still need to save."""
    s = _stand(flush_raises=RuntimeError("boom"))
    s._flush_items_for_save()          # must not raise
    assert s._item_save_errors


def test_pending_edits_survive_a_failure():
    s = _stand(flush_raises=OSError("disk full"))
    s._flush_items_for_save()
    assert s.items_editor.pending == {"ITEM_POTION": {"price": 999}}, (
        "the user's unsaved item edits must still be there to retry")


def test_the_failure_is_logged_for_troubleshooting():
    s = _stand(flush_raises=OSError("disk full"))
    s._flush_items_for_save()
    assert any("item" in m.lower() for m in s.logged), s.logged


# ── 3. icon failures are detected ───────────────────────────────────────────

def test_a_false_return_from_save_icon_changes_is_detected():
    """THE DEFECT: the bool was thrown away at both call sites."""
    s = _stand(icons_ok=False)
    s._flush_items_for_save()
    assert s._item_save_errors, "a failed icon write was reported as success"
    assert any("icon" in e.lower() for e in s._item_save_errors), \
        s._item_save_errors


def test_a_raising_icon_save_is_detected():
    s = _stand(icon_raises=True)
    s._flush_items_for_save()
    assert any("icon" in e.lower() for e in s._item_save_errors), \
        s._item_save_errors


def test_icons_are_still_attempted_when_the_data_save_fails():
    """One failure must not skip the other half of the work."""
    s = _stand(flush_raises=OSError("disk full"))
    s._flush_items_for_save()
    assert s.items_editor.icon_calls == 1, (
        "the icon write was skipped because the data save raised")


def test_a_partial_save_names_both_parts():
    s = _stand(flush_raises=OSError("disk full"), icons_ok=False)
    s._flush_items_for_save()
    joined = " ".join(s._item_save_errors).lower()
    assert "disk full" in joined and "icon" in joined, s._item_save_errors


# ── 4. success stays silent ─────────────────────────────────────────────────

def test_a_clean_save_records_nothing():
    s = _stand()
    s._flush_items_for_save()
    assert s._item_save_errors == [], (
        "a successful save must not produce a warning")


def test_the_recorder_is_reset_between_saves():
    s = _stand()
    s._item_save_errors = ["stale failure from last time"]
    s._flush_items_for_save()
    assert s._item_save_errors == [], (
        "a previous failure must not be re-reported after a good save")


# ── 5. Save All must see it ─────────────────────────────────────────────────

def test_save_all_consults_the_item_errors():
    """Save All records update_save() as 'PorySuite data'. If items failed
    inside it, that outcome must not say saved."""
    src = open(os.path.join(_ROOT, "ui", "unified_mainwindow.py"),
               encoding="utf-8", errors="replace").read()
    import re
    m = re.search(r"\n    def _on_save_all\(.*?(?=\n    def )", src, re.S)
    assert m
    assert "_item_save_errors" in m.group(0), (
        "Save All must read the item-save failures recorded by update_save, "
        "otherwise a failed Items save is still reported as 'PorySuite data: "
        "saved'")


def test_update_save_no_longer_swallows_the_items_block():
    src = open(os.path.join(_ROOT, "ui", "mainwindow.py"),
               encoding="utf-8", errors="replace").read()
    import re
    m = re.search(r"\n    def update_save\(.*?(?=\n    def )", src, re.S)
    assert m
    body = m.group(0)
    i = body.index("_flush_items_for_save()")
    window = body[max(0, i - 200):i]
    assert "save_items_table()" not in window, (
        "the old inline items block is still there alongside the new one")
