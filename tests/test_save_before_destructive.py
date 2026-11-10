"""A destructive step must not proceed when the save it depends on did not happen.

THE FAMILY OF DEFECTS (found 2026-10-29, repaired 2026-11-08).

Four places asked the user "you have unsaved changes, save first?", were told
Save, and then went ahead with the destructive step whether or not the save
actually happened:

  * ``UnifiedMainWindow.closeEvent``   — closed the window
  * ``UnifiedMainWindow._refresh_project`` (F5) — reloaded everything from disk
  * ``MainWindow.try_save_before_closing`` / ``load_save_data`` /
    ``update_action`` (New Project) — closed, switched project, replaced project
  * ``EventideMainWindow._try_save_before_closing`` — closed

The two unified sites called ``_on_save_all()``, which returned ``None`` on
every path, so no caller could check. The three PorySuite sites called
``update_save()`` bare, so an exception escaped the event handler entirely.
EVENTide called ``_on_save()`` and returned True unconditionally, although that
handler catches its own errors and has early returns that write nothing.

A FIFTH defect in the same family: ``_has_unsaved_changes`` answered a shorter
question than Save All asks, so the prompt never appeared at all for a pending
tilemap, tile-animation, instrument or piano-roll edit.

THE RULE (do not simplify away):

  * The assertion that matters is NOT that a warning appears — it is that the
    destructive operation does not proceed.
  * ``_on_save_all`` returns True only when nothing failed and nothing was
    partial. A cancelled confirmation is False: nothing was written.
  * Cancel must still block. An EXPLICIT Discard must still proceed — that is
    the user's own choice. A clean save must still proceed.
  * No "Close Anyway" button in the failure dialog. The user already chose Save
    over Discard; a second destructive choice there invites an accidental loss.
  * ``_has_unsaved_changes`` reads the window's own modified flag, which
    subsumes the hand-written editor list rather than extending it.

If you touch any of those methods, keep this file green.
"""

import ast
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "core"), os.path.join(_ROOT, "ui")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

try:
    from PyQt6.QtWidgets import QApplication, QWidget, QMessageBox
    _QT = True
except Exception:                                      # pragma: no cover
    _QT = False

pytestmark = pytest.mark.skipif(not _QT, reason="PyQt6 not available")

if _QT:
    _APP = QApplication.instance() or QApplication([])

UNIFIED = os.path.join(_ROOT, "ui", "unified_mainwindow.py")
MAIN = os.path.join(_ROOT, "ui", "mainwindow.py")
EVENTIDE = os.path.join(_ROOT, "eventide", "mainwindow.py")


# ════════════════════════════════════════════════════════════════════════════
# helpers
# ════════════════════════════════════════════════════════════════════════════
class _Status:
    def __init__(self):
        self.msg = ""

    def showMessage(self, m, t=0):
        self.msg = m


class _Event:
    """Stands in for a QCloseEvent; records which way it was answered."""

    def __init__(self):
        self.accepted = None

    def accept(self):
        self.accepted = True

    def ignore(self):
        self.accepted = False


def _unified_stand(**attrs):
    """Bind the SHIPPING unified methods to a minimal real QWidget.

    A QWidget rather than a bare object because these methods hand ``self`` to
    QMessageBox as the dialog parent, and giving Qt a non-widget there aborts
    the process at the C++ level. Not a second QMainWindow — constructing one
    beside an existing full window crashes Qt outright.
    """
    import ui.unified_mainwindow as um
    U = um.UnifiedMainWindow

    class Stand(QWidget):
        closeEvent = U.closeEvent
        _has_unsaved_changes = U._has_unsaved_changes
        _refresh_project = U._refresh_project

        def __init__(self):
            super().__init__()
            self.log = []
            self._st = _Status()
            self._modified = False
            self._porysuite_window = None
            self._eventide_window = None
            self.project_info = {"dir": ""}
            self._return_to_launcher = True
            self.relaunched = 0
            self.save_calls = 0
            self.save_result = True
            for k, v in attrs.items():
                setattr(self, k, v)

        # the thing under test calls this; the real one is covered separately
        def _on_save_all(self):
            self.save_calls += 1
            return self.save_result

        def _maybe_relaunch_to_launcher(self):
            self.relaunched += 1

        def log_message(self, m):
            self.log.append(m)

        def statusBar(self):
            return self._st

        def setWindowModified(self, v):
            self._modified = v

        def isWindowModified(self):
            return self._modified

    return Stand()


@pytest.fixture
def dialog(monkeypatch):
    """Drive ``create_unsaved_changes_dialog`` and capture every warning.

    The methods under test use ``from app_util import ...`` inside the method
    body, so the name is resolved at call time out of the already-imported
    module — patching the module attribute is enough.
    """
    import app_util
    box = {"answer": QMessageBox.StandardButton.Save, "warnings": []}

    monkeypatch.setattr(app_util, "create_unsaved_changes_dialog",
                        lambda *a, **k: box["answer"])

    def rec(parent, title, text, *a, **k):
        box["warnings"].append((title, text))
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "warning", staticmethod(rec))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(rec))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(rec))
    return box


# ════════════════════════════════════════════════════════════════════════════
# 1. _on_save_all must tell its caller what happened
# ════════════════════════════════════════════════════════════════════════════
def _save_all_stand(**editors):
    """Bind the real ``_on_save_all`` so its RETURN VALUE can be inspected."""
    import ui.unified_mainwindow as um
    from core.shared_data import ProjectData

    class Stand(QWidget):
        _on_save_all = um.UnifiedMainWindow._on_save_all
        _report_save_outcome = um.UnifiedMainWindow._report_save_outcome
        _editor_dirty = um.UnifiedMainWindow._editor_dirty
        _confirm_saved = um.UnifiedMainWindow._confirm_saved

        def __init__(self):
            super().__init__()
            self.log = []
            self._st = _Status()
            self._modified = True
            self.project_data = ProjectData({})
            self._porysuite_window = None
            self._eventide_window = None
            for k, v in editors.items():
                setattr(self, k, v)

        def set_page_dirty(self, page, dirty):
            pass

        def log_message(self, m):
            self.log.append(m)

        def statusBar(self):
            return self._st

        def setWindowModified(self, v):
            self._modified = v

        def isWindowModified(self):
            return self._modified

    return Stand()


class _FailingEditor:
    """An editor that is dirty and whose save raises."""

    def has_unsaved_changes(self):
        return True

    def _on_save(self):
        raise PermissionError("[Errno 13] Permission denied: 'credits.h'")

    def _save_labels(self):
        self._on_save()


class _CleanEditor:
    def has_unsaved_changes(self):
        return False


@pytest.fixture
def yes_to_save_all(monkeypatch):
    seen = []

    def rec(parent, title, text, *a, **k):
        seen.append((title, text))
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "warning", staticmethod(rec))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(rec))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(
        lambda *a, **k: QMessageBox.StandardButton.Yes))
    return seen


def test_on_save_all_reports_success_to_its_caller(yes_to_save_all):
    """THE DEFECT: it returned None on every path, so nobody could check."""
    s = _save_all_stand(_credits_editor=_CleanEditor())
    assert s._on_save_all() is True


def test_on_save_all_reports_failure_to_its_caller(yes_to_save_all):
    s = _save_all_stand(_credits_editor=_FailingEditor())
    assert s._on_save_all() is False


def test_a_cancelled_confirmation_is_reported_as_not_saved(monkeypatch):
    """Declining "Save all changes to disk?" wrote nothing, so a destructive
    caller must not treat it as a save."""
    monkeypatch.setattr(QMessageBox, "question", staticmethod(
        lambda *a, **k: QMessageBox.StandardButton.Cancel))
    s = _save_all_stand(_credits_editor=_CleanEditor())
    assert s._on_save_all() is False


def test_a_failure_is_still_reported_to_the_user(yes_to_save_all):
    """The return value is in ADDITION to the dialog, not instead of it."""
    s = _save_all_stand(_credits_editor=_FailingEditor())
    s._on_save_all()
    assert any("NOT saved" in t for t, _ in yes_to_save_all), yes_to_save_all


# ════════════════════════════════════════════════════════════════════════════
# 2. closing
# ════════════════════════════════════════════════════════════════════════════
def test_a_failed_save_does_not_close_the_application(dialog):
    """THE DEFECT: the user was shown "NOT saved" and the window shut anyway."""
    s = _unified_stand(_modified=True, save_result=False)
    ev = _Event()
    s.closeEvent(ev)
    assert s.save_calls == 1, "the save the user asked for must be attempted"
    assert ev.accepted is False, "a failed save must not be followed by a close"


def test_a_blocked_close_does_not_leave_the_relaunch_armed(dialog):
    """Quit to Launcher routes through close(); a blocked quit must not go on
    to spawn a fresh process."""
    s = _unified_stand(_modified=True, save_result=False)
    s._return_to_launcher = True
    s.closeEvent(_Event())
    assert s.relaunched == 0
    assert s._return_to_launcher is False


def test_a_successful_save_still_closes(dialog):
    """PRESERVATION. The repair must not make a clean save un-closeable."""
    s = _unified_stand(_modified=True, save_result=True)
    ev = _Event()
    s.closeEvent(ev)
    assert s.save_calls == 1
    assert ev.accepted is True
    assert s.relaunched == 1


def test_cancel_still_blocks_the_close(dialog):
    """PRESERVATION. Cancel aborts and saves nothing."""
    dialog["answer"] = QMessageBox.StandardButton.Cancel
    s = _unified_stand(_modified=True)
    ev = _Event()
    s.closeEvent(ev)
    assert s.save_calls == 0
    assert ev.accepted is False


def test_discard_still_closes_without_saving(dialog):
    """PRESERVATION. An explicit Discard is the user's own decision."""
    dialog["answer"] = QMessageBox.StandardButton.Discard
    s = _unified_stand(_modified=True)
    ev = _Event()
    s.closeEvent(ev)
    assert s.save_calls == 0
    assert ev.accepted is True


def test_a_clean_window_closes_with_no_prompt(dialog):
    """PRESERVATION. Nothing pending means no question and no save."""
    s = _unified_stand(_modified=False)
    ev = _Event()
    s.closeEvent(ev)
    assert s.save_calls == 0
    assert ev.accepted is True


def test_no_close_anyway_button_is_offered_on_failure(dialog):
    """Design decision recorded 2026-10-29: the user already chose Save over
    Discard, so a second destructive button at that moment invites an
    accidental discard."""
    s = _unified_stand(_modified=True, save_result=False)
    s.closeEvent(_Event())
    blob = " ".join(t + " " + x for t, x in dialog["warnings"]).lower()
    assert "close anyway" not in blob
    assert "discard" not in blob


def test_a_second_attempt_after_fixing_the_problem_succeeds(dialog):
    """The retry path must work, or the user is trapped with no way out."""
    s = _unified_stand(_modified=True, save_result=False)
    first = _Event()
    s.closeEvent(first)
    assert first.accepted is False
    s.save_result = True                    # user fixed the permission problem
    second = _Event()
    s.closeEvent(second)
    assert second.accepted is True


# ════════════════════════════════════════════════════════════════════════════
# 3. F5 refresh
# ════════════════════════════════════════════════════════════════════════════
class _Refreshable:
    def __init__(self):
        self.refreshes = 0
        self.project_info = None

    def _refresh_project(self):
        self.refreshes += 1

    def isWindowModified(self):
        return False


def _drive_refresh(stand, ps):
    """Run the real F5 and tolerate ONLY what happens after the reload.

    ``_refresh_project`` goes on to touch far more of the real window than
    these stands carry, so it raises eventually. An exception raised BEFORE
    the reload would make "the reload did not happen" meaningless, so it is
    re-raised rather than swallowed.
    """
    try:
        stand._refresh_project()
    except Exception:
        if ps.refreshes == 0:
            raise


def test_f5_does_not_refresh_when_the_save_failed(dialog):
    """THE DEFECT: the refresh reloaded from disk and discarded exactly the
    edits the user had just asked to protect."""
    ps = _Refreshable()
    s = _unified_stand(_modified=True, save_result=False, _porysuite_window=ps)
    s._refresh_project()
    assert s.save_calls == 1
    assert ps.refreshes == 0, "the destructive reload must not have happened"


def test_f5_still_refreshes_after_a_clean_save(dialog):
    """THE CONTROL. Without this the test above would also pass if F5 were
    simply broken for everyone."""
    ps = _Refreshable()
    s = _unified_stand(_modified=True, save_result=True, _porysuite_window=ps)
    _drive_refresh(s, ps)
    assert s.save_calls == 1
    assert ps.refreshes == 1


def test_f5_cancel_still_aborts(dialog):
    """PRESERVATION."""
    dialog["answer"] = QMessageBox.StandardButton.Cancel
    ps = _Refreshable()
    s = _unified_stand(_modified=True, _porysuite_window=ps)
    s._refresh_project()
    assert s.save_calls == 0
    assert ps.refreshes == 0


def test_f5_on_a_clean_project_does_not_prompt(dialog):
    """PRESERVATION: no pending work, no question, and the refresh still runs."""
    ps = _Refreshable()
    s = _unified_stand(_modified=False, _porysuite_window=ps)
    _drive_refresh(s, ps)
    assert s.save_calls == 0
    assert ps.refreshes == 1


# ════════════════════════════════════════════════════════════════════════════
# 4. the prompt must appear at all — _has_unsaved_changes
# ════════════════════════════════════════════════════════════════════════════
def test_the_windows_own_modified_flag_counts_as_unsaved_work():
    """THE DEFECT: the Tilemap Editor, Tile Animations, the instruments tab and
    the piano roll report their dirt ONLY by setting this flag. The named list
    could not see them, so closing discarded the work with no prompt."""
    s = _unified_stand(_modified=True)
    assert s._has_unsaved_changes() is True


def test_a_clean_window_still_reports_nothing_to_save():
    """THE CONTROL. The new check must not fire spuriously, or the prompt
    appears forever and the user learns to click through it."""
    s = _unified_stand(_modified=False)
    assert s._has_unsaved_changes() is False


def test_an_embedded_windows_flag_is_still_consulted():
    """PRESERVATION. The unified flag does not cover the two embedded windows;
    they track their own and must keep being asked."""
    class _Child:
        def isWindowModified(self):
            return True

    s = _unified_stand(_modified=False, _porysuite_window=_Child())
    assert s._has_unsaved_changes() is True

    s2 = _unified_stand(_modified=False, _eventide_window=_Child())
    assert s2._has_unsaved_changes() is True


def test_an_editors_own_answer_is_still_consulted():
    """PRESERVATION. An editor exposing has_unsaved_changes() is a more precise
    answer than the window flag and must keep being asked."""
    class _Dirty:
        def has_unsaved_changes(self):
            return True

    s = _unified_stand(_modified=False, _credits_editor=_Dirty())
    assert s._has_unsaved_changes() is True


def test_the_dirty_check_reads_the_window_flag_in_source():
    """Structural guard. The one-line channel is the whole point of the fix;
    if someone replaces it with four more attribute names the drift starts
    again."""
    src = open(UNIFIED, encoding="utf-8").read()
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_has_unsaved_changes")
    body = ast.get_source_segment(src, fn) or ""
    assert "self.isWindowModified()" in body


# ════════════════════════════════════════════════════════════════════════════
# 5. PorySuite's three prompts
# ════════════════════════════════════════════════════════════════════════════
def _main_stand(**attrs):
    """Bind the SHIPPING MainWindow save/close helpers to a minimal QWidget."""
    import ui.mainwindow as mw
    M = mw.MainWindow

    class Stand(QWidget):
        _save_for_destructive_action = M._save_for_destructive_action
        try_save_before_closing = M.try_save_before_closing

        def __init__(self):
            super().__init__()
            self.log = []
            self._modified = True
            self.saves = 0
            self.raise_on_save = None
            self._item_save_errors = []
            self.item_errors_after_save = []
            for k, v in attrs.items():
                setattr(self, k, v)

        def update_save(self):
            self.saves += 1
            if self.raise_on_save:
                raise self.raise_on_save
            self._item_save_errors = list(self.item_errors_after_save)

        def log_message(self, m):
            self.log.append(m)

        def isWindowModified(self):
            return self._modified

        def setWindowModified(self, v):
            self._modified = v

    return Stand()


def test_a_raising_save_is_reported_and_refuses_the_action(dialog):
    """THE DEFECT: the exception escaped the event handler. Nothing told the
    user, and the destructive step ran or the process died."""
    s = _main_stand(raise_on_save=PermissionError("Permission denied"))
    assert s._save_for_destructive_action("close") is False
    assert s.saves == 1
    assert any("NOT saved" in t for t, _ in dialog["warnings"]), dialog["warnings"]


def test_a_quietly_recorded_item_failure_also_refuses_the_action(dialog):
    """The Items flush records failures instead of raising. That is the one
    other failure signal update_save() actually has, so it is read too."""
    s = _main_stand(item_errors_after_save=["could not write item_icon.png"])
    assert s._save_for_destructive_action("switch projects") is False
    assert any("item_icon.png" in x for _, x in dialog["warnings"])


def test_a_clean_save_permits_the_action(dialog):
    """THE CONTROL."""
    s = _main_stand()
    assert s._save_for_destructive_action("close") is True
    assert s.saves == 1
    assert dialog["warnings"] == []


def test_the_failure_message_names_what_did_not_happen(dialog):
    """Plain English, and specific: "did not close" rather than "error"."""
    s = _main_stand(raise_on_save=OSError("disk full"))
    s._save_for_destructive_action("create the new project")
    blob = " ".join(x for _, x in dialog["warnings"])
    assert "create the new project" in blob
    assert "disk full" in blob
    assert "still here" in blob, "tell the user their work was not lost"


def test_exit_does_not_close_when_the_save_raises(dialog):
    """try_save_before_closing returning False makes closeEvent ignore()."""
    s = _main_stand(raise_on_save=PermissionError("nope"))
    assert s.try_save_before_closing() is False


def test_exit_still_closes_after_a_clean_save(dialog):
    """PRESERVATION."""
    s = _main_stand()
    assert s.try_save_before_closing() is True
    assert s.saves == 1


def test_exit_cancel_still_blocks_and_discard_still_closes(dialog):
    """PRESERVATION, both branches."""
    dialog["answer"] = QMessageBox.StandardButton.Cancel
    assert _main_stand().try_save_before_closing() is False
    dialog["answer"] = QMessageBox.StandardButton.Discard
    s = _main_stand()
    assert s.try_save_before_closing() is True
    assert s.saves == 0, "Discard must not save"


def test_a_clean_window_exits_without_saving(dialog):
    """PRESERVATION."""
    s = _main_stand(_modified=False)
    assert s.try_save_before_closing() is True
    assert s.saves == 0


# ── the other two PorySuite sites, read structurally ────────────────────────
#
# load_save_data and update_action are driven by Qt signals and reach far more
# of the real window than a stand can carry, so these assert the SHAPE: the
# save result is checked and the destructive step is abandoned when it is
# False. That is exactly what regressed, and it is visible in the source.

def _fn_source(path, name):
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == name)
    return ast.get_source_segment(src, fn) or ""


@pytest.mark.parametrize("name,action", [
    ("load_save_data", "switch projects"),
    ("update_action", "create the new project"),
])
def test_the_remaining_prompts_check_the_save_result(name, action):
    body = _fn_source(MAIN, name)
    assert "self.update_save()" not in body, (
        f"{name} calls update_save() bare again — a failed save there is "
        f"invisible and the destructive step proceeds")
    assert f'_save_for_destructive_action("{action}")' in body \
        or f'_save_for_destructive_action(\n' in body, body[:400]
    assert "if not self._save_for_destructive_action" in body, (
        f"{name} must ABANDON its destructive step when the save failed; "
        f"calling the helper and ignoring the answer is the original defect")


def test_no_prompt_site_calls_update_save_bare():
    """Whole-file guard. Any future "save before you lose it" prompt must go
    through the helper, which is the only place that reports a failure."""
    src = open(MAIN, encoding="utf-8").read()
    tree = ast.parse(src)
    helper = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "_save_for_destructive_action")
    bare = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if (isinstance(f, ast.Attribute) and f.attr == "update_save"
                and isinstance(f.value, ast.Name) and f.value.id == "self"):
            if not (helper.lineno <= node.lineno <= helper.end_lineno):
                bare.append(node.lineno)
    assert bare == [], (
        f"update_save() is called outside the reporting helper at lines {bare}; "
        f"a failure there cannot reach the user")


# ════════════════════════════════════════════════════════════════════════════
# 6. EVENTide's own close prompt
# ════════════════════════════════════════════════════════════════════════════
def _eventide_stand(**attrs):
    import eventide.mainwindow as em

    class _Tab:
        def __init__(self, outer):
            self.outer = outer

        def _on_save(self):
            self.outer.saves += 1
            if self.outer.raise_on_save:
                raise self.outer.raise_on_save
            if self.outer.save_takes:
                self.outer._modified = False

    class Stand(QWidget):
        _try_save_before_closing = em.EventideMainWindow._try_save_before_closing

        def __init__(self):
            super().__init__()
            self.log = []
            self._modified = True
            self.saves = 0
            self.raise_on_save = None
            self.save_takes = True
            self.event_editor_tab = _Tab(self)
            for k, v in attrs.items():
                setattr(self, k, v)

        def log_message(self, m):
            self.log.append(m)

        def isWindowModified(self):
            return self._modified

        def setWindowModified(self, v):
            self._modified = v

    return Stand()


def test_eventide_does_not_close_when_the_save_did_not_take(dialog):
    """THE DEFECT: `_on_save` catches its own errors and returns normally, and
    it has early returns that write nothing, so EVENTide returned True and
    closed over a save that never happened."""
    s = _eventide_stand(save_takes=False)
    assert s._try_save_before_closing() is False
    assert s.saves == 1
    assert any("NOT saved" in t for t, _ in dialog["warnings"])


def test_eventide_does_not_close_when_the_save_raises(dialog):
    s = _eventide_stand(raise_on_save=PermissionError("denied"))
    assert s._try_save_before_closing() is False


def test_eventide_still_closes_after_a_clean_save(dialog):
    """THE CONTROL."""
    s = _eventide_stand(save_takes=True)
    assert s._try_save_before_closing() is True
    assert s.saves == 1
    assert dialog["warnings"] == []


def test_eventide_cancel_and_discard_are_unchanged(dialog):
    """PRESERVATION."""
    dialog["answer"] = QMessageBox.StandardButton.Cancel
    assert _eventide_stand()._try_save_before_closing() is False
    dialog["answer"] = QMessageBox.StandardButton.Discard
    s = _eventide_stand()
    assert s._try_save_before_closing() is True
    assert s.saves == 0


def test_eventide_clean_window_closes_without_saving(dialog):
    """PRESERVATION."""
    s = _eventide_stand(_modified=False)
    assert s._try_save_before_closing() is True
    assert s.saves == 0


# ════════════════════════════════════════════════════════════════════════════
# 6b. EVENTide's map switch and its own F5
#
# Found 2026-11-08 by sweeping EVERY "save before you lose your work" prompt in
# the app instead of only the four already recorded. Both have the identical
# shape, and the map switch is the one that fires in ordinary use — it runs
# every time the user clicks a different map, not only on the way out.
# ════════════════════════════════════════════════════════════════════════════
def _map_switch_stand(**attrs):
    import eventide.ui.event_editor_tab as eet

    class _MW:
        def __init__(self, outer):
            self.outer = outer
            self.log = []

        def isWindowModified(self):
            return self.outer._modified

        def log_message(self, m):
            self.log.append(m)

    class Stand(QWidget):
        _check_unsaved_before_map_switch = \
            eet.EventEditorTab._check_unsaved_before_map_switch

        def __init__(self):
            super().__init__()
            self._modified = True
            self.saves = 0
            self.raise_on_save = None
            self.save_takes = True
            for k, v in attrs.items():
                setattr(self, k, v)
            self._mw = _MW(self)

        def _on_save(self):
            self.saves += 1
            if self.raise_on_save:
                raise self.raise_on_save
            if self.save_takes:
                self._modified = False

    return Stand()


def test_eventide_does_not_switch_map_when_the_save_did_not_take(dialog):
    """THE DEFECT: `return True` followed a save that never happened, and the
    caller then loaded a different map over the unsaved edits."""
    s = _map_switch_stand(save_takes=False)
    assert s._check_unsaved_before_map_switch() is False
    assert s.saves == 1
    assert any("NOT saved" in t for t, _ in dialog["warnings"])


def test_eventide_does_not_switch_map_when_the_save_raises(dialog):
    s = _map_switch_stand(raise_on_save=PermissionError("denied"))
    assert s._check_unsaved_before_map_switch() is False


def test_eventide_still_switches_map_after_a_clean_save(dialog):
    """THE CONTROL — without it the guard above would also pass if map
    switching were simply broken."""
    s = _map_switch_stand(save_takes=True)
    assert s._check_unsaved_before_map_switch() is True
    assert s.saves == 1
    assert dialog["warnings"] == []


def test_eventide_map_switch_cancel_and_discard_are_unchanged(dialog):
    """PRESERVATION."""
    dialog["answer"] = QMessageBox.StandardButton.Cancel
    assert _map_switch_stand()._check_unsaved_before_map_switch() is False
    dialog["answer"] = QMessageBox.StandardButton.Discard
    s = _map_switch_stand()
    assert s._check_unsaved_before_map_switch() is True
    assert s.saves == 0


def test_eventide_map_switch_is_silent_with_nothing_pending(dialog):
    """PRESERVATION: no pending edits, no dialog, no save."""
    s = _map_switch_stand(_modified=False)
    assert s._check_unsaved_before_map_switch() is True
    assert s.saves == 0


def _eventide_refresh_stand(**attrs):
    import eventide.mainwindow as em

    class _Tab:
        def __init__(self, outer):
            self.outer = outer

        def _on_save(self):
            self.outer.saves += 1
            if self.outer.raise_on_save:
                raise self.outer.raise_on_save
            if self.outer.save_takes:
                self.outer._modified = False

    class Stand(QWidget):
        _refresh_project = em.EventideMainWindow._refresh_project

        def __init__(self):
            super().__init__()
            self.log = []
            self._st = _Status()
            self._modified = True
            self.saves = 0
            self.reloads = 0
            self.raise_on_save = None
            self.save_takes = True
            self.project_info = {"dir": ""}
            self.event_editor_tab = _Tab(self)
            for k, v in attrs.items():
                setattr(self, k, v)

        def load_data(self, info):
            self.reloads += 1

        def log_message(self, m):
            self.log.append(m)

        def statusBar(self):
            return self._st

        def isWindowModified(self):
            return self._modified

        def setWindowModified(self, v):
            self._modified = v

    return Stand()


def test_eventide_f5_does_not_reload_when_the_save_did_not_take(dialog):
    """THE DEFECT: the reload ran unconditionally after the save attempt."""
    s = _eventide_refresh_stand(save_takes=False)
    s._refresh_project()
    assert s.saves == 1
    assert s.reloads == 0, "the destructive reload must not have happened"


def test_eventide_f5_still_reloads_after_a_clean_save(dialog):
    """THE CONTROL."""
    s = _eventide_refresh_stand(save_takes=True)
    s._refresh_project()
    assert s.saves == 1
    assert s.reloads == 1


def test_eventide_f5_cancel_still_aborts(dialog):
    """PRESERVATION."""
    dialog["answer"] = QMessageBox.StandardButton.Cancel
    s = _eventide_refresh_stand()
    s._refresh_project()
    assert s.saves == 0
    assert s.reloads == 0


def test_eventide_f5_on_a_clean_project_just_reloads(dialog):
    """PRESERVATION."""
    s = _eventide_refresh_stand(_modified=False)
    s._refresh_project()
    assert s.saves == 0
    assert s.reloads == 1


# ════════════════════════════════════════════════════════════════════════════
# 7. structural guards on the unified sites
# ════════════════════════════════════════════════════════════════════════════
def test_on_save_all_has_no_path_that_returns_nothing():
    """A single forgotten `return` reintroduces the whole family: None is
    falsy, so a caller silently refuses every destructive action instead."""
    src = open(UNIFIED, encoding="utf-8").read()
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_on_save_all")
    bare = [r.lineno for r in ast.walk(fn)
            if isinstance(r, ast.Return) and r.value is None]
    assert bare == [], f"bare `return` in _on_save_all at lines {bare}"
    last = fn.body[-1]
    assert isinstance(last, ast.Return), (
        "_on_save_all must end in a return, or it falls off the end as None")


@pytest.mark.parametrize("name", ["closeEvent", "_refresh_project"])
def test_every_unified_caller_checks_the_save_result(name):
    body = _fn_source(UNIFIED, name)
    assert "self._on_save_all()" in body
    assert "if not self._on_save_all()" in body, (
        f"{name} must not call _on_save_all and ignore the answer — that is "
        f"the original defect")
