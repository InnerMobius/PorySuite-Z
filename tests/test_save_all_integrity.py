"""Save All must never present an unsuccessful save as a successful one.

THE CRASH (confirmed in a real user session 2026-10-29 08:56:41, fixed same day).
`ui/unified_mainwindow.py::_on_save_all` called
`self.project_data.notify_scripts_changed()`. `ProjectData` declares the SIGNAL
`scripts_changed = pyqtSignal()  # scripts.inc content changed` but the
`notify_scripts_changed()` wrapper was never written — both the signal and the
call site arrived in the same commit (ca3f3cf, 2026-04-03), so the wrapper was
simply forgotten. Every other signal has one.

Result: pressing Save All with EVENTide map edits pending raised AttributeError
and killed the application — AFTER PorySuite and EVENTide data had been written
but BEFORE credits, labels, sound, tilemap, tile animations and shops, so
unsaved work in those six was lost with no warning.

THE RULE (do not simplify away):
  * Every `*_changed` signal on ProjectData keeps a matching `notify_*` wrapper.
    The wrapper set being incomplete is what caused the crash; an incomplete set
    is a latent crash.
  * A dirty indicator is cleared ONLY when the change was persisted, or when
    there genuinely was nothing to save. "Nothing to save", "saved", "failed"
    and "partly saved" are four different states and must not collapse.
  * One summary at the end naming which editors saved and which did not. Never
    report overall success when any editor failed. No warning when there was
    nothing to save.
  * Keep the detailed log lines — they just must not be the only signal.

If you touch _on_save_all, ProjectData's signals, or SaveOutcome, keep this green.
"""

import os
import re
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "core"), os.path.join(_ROOT, "ui")):
    sys.path.insert(0, _p)

import pytest  # noqa: E402

try:
    from PyQt6.QtWidgets import QApplication
    _QT = True
except Exception:
    _QT = False

pytestmark = pytest.mark.skipif(not _QT, reason="PyQt6 not available")

if _QT:
    _APP = QApplication.instance() or QApplication([])

UNIFIED = os.path.join(_ROOT, "ui", "unified_mainwindow.py")
SHARED = os.path.join(_ROOT, "core", "shared_data.py")


# ── 1. the crash itself ──────────────────────────────────────────────────────

def test_project_data_has_notify_scripts_changed():
    """The exact attribute whose absence killed the app."""
    from core.shared_data import ProjectData
    assert hasattr(ProjectData, "notify_scripts_changed"), (
        "Save All calls this; without it the application dies mid-save"
    )
    assert callable(ProjectData.notify_scripts_changed)


def test_notify_scripts_changed_emits_the_declared_signal():
    """It must EMIT, not merely exist — the point is cross-editor sync."""
    from core.shared_data import ProjectData
    pd = ProjectData({})
    seen = []
    pd.scripts_changed.connect(lambda: seen.append(1))
    pd.notify_scripts_changed()
    assert seen == [1], "notify_scripts_changed must emit scripts_changed"


def test_every_declared_signal_has_a_notify_wrapper():
    """The root cause was an incomplete wrapper set. Keep it complete."""
    import ast
    with open(SHARED, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    sigs, wrappers = [], set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "ProjectData":
            for b in node.body:
                if isinstance(b, ast.Assign) and "pyqtSignal" in ast.dump(b.value):
                    for t in b.targets:
                        if isinstance(t, ast.Name):
                            sigs.append(t.id)
                elif isinstance(b, ast.FunctionDef) and b.name.startswith("notify_"):
                    wrappers.add(b.name)
    missing = [s for s in sigs
               if f"notify_{s.replace('_changed', '')}_changed" not in wrappers]
    assert not missing, f"signals with no notify_ wrapper: {missing}"


def test_every_project_data_call_site_resolves():
    """Guards against another typo'd notification crashing a save."""
    import ast
    import glob
    from core.shared_data import ProjectData
    members = set(dir(ProjectData))
    bad = []
    for f in (glob.glob(os.path.join(_ROOT, "ui", "**", "*.py"), recursive=True)
              + glob.glob(os.path.join(_ROOT, "eventide", "**", "*.py"),
                          recursive=True)):
        txt = open(f, encoding="utf-8", errors="ignore").read()
        for m in re.finditer(r"project_data\.(\w+)", txt):
            if m.group(1) not in members:
                bad.append((os.path.relpath(f, _ROOT),
                            txt[:m.start()].count("\n") + 1, m.group(1)))
    assert not bad, f"calls to members ProjectData does not have: {bad}"


# ── 2. the outcome recorder ──────────────────────────────────────────────────

def _outcome():
    from ui.save_outcome import SaveOutcome
    return SaveOutcome()


def test_nothing_to_save_is_not_a_failure():
    o = _outcome()
    o.nothing_to_save("Credits")
    assert o.all_ok() is True
    assert o.any_failed() is False
    assert o.failed_names() == []
    assert o.should_warn() is False, "no warning when there was nothing to save"


def test_a_success_is_recorded_and_reported():
    o = _outcome()
    o.saved("Credits")
    assert o.all_ok() and not o.any_failed()
    assert "Credits" in o.saved_names()
    assert o.should_warn() is False


def test_a_failure_is_recorded_and_warns():
    o = _outcome()
    o.failed("Credits", "disk full")
    assert o.any_failed() is True
    assert o.all_ok() is False
    assert o.failed_names() == ["Credits"]
    assert o.should_warn() is True
    assert "disk full" in o.summary_text()


def test_partial_success_is_described_as_partial():
    o = _outcome()
    o.saved("PorySuite data")
    o.failed("Sound Editor", "voicegroup missing")
    assert o.any_failed() and not o.all_ok()
    text = o.summary_text()
    assert "PorySuite data" in text and "Sound Editor" in text
    assert "voicegroup missing" in text
    low = text.lower()
    assert "saved" in low and ("not saved" in low or "did not" in low), (
        "the summary must name BOTH what saved and what did not"
    )


def test_summary_never_claims_everything_saved_when_one_failed():
    o = _outcome()
    o.saved("A")
    o.saved("B")
    o.failed("C", "boom")
    text = o.summary_text().lower()
    assert "everything saved" not in text
    assert "all saved" not in text


def test_partial_within_one_editor_is_its_own_state():
    o = _outcome()
    o.partial("Graphics", "3 of 5 sprites written")
    assert o.any_failed() is True, "a partial save is not a clean success"
    assert o.should_warn() is True
    assert "3 of 5" in o.summary_text()


def test_outcome_keeps_insertion_order():
    o = _outcome()
    for n in ("one", "two", "three"):
        o.saved(n)
    assert o.saved_names() == ["one", "two", "three"]


def test_dirty_should_clear_only_on_persisted_or_nothing_to_save():
    o = _outcome()
    o.saved("ok")
    o.nothing_to_save("empty")
    o.failed("bad", "why")
    o.partial("half", "some")
    assert o.may_clear_dirty("ok") is True
    assert o.may_clear_dirty("empty") is True, (
        "nothing outstanding means the marker is legitimately clear"
    )
    assert o.may_clear_dirty("bad") is False, (
        "a failed save must keep its marker so the user can retry"
    )
    assert o.may_clear_dirty("half") is False
    assert o.may_clear_dirty("never-recorded") is False


# ── 3. _on_save_all must use it, and must not clear 'events' blindly ────────

@pytest.fixture(scope="module")
def save_all_src():
    with open(UNIFIED, encoding="utf-8") as fh:
        src = fh.read()
    m = re.search(r"\n    def _on_save_all\(.*?(?=\n    def )", src, re.S)
    assert m, "_on_save_all not found"
    return m.group(0)


def test_save_all_records_outcomes(save_all_src):
    assert "SaveOutcome" in save_all_src, (
        "_on_save_all must record per-editor outcomes rather than ad-hoc flags"
    )


def test_events_dirty_is_no_longer_cleared_unconditionally(save_all_src):
    """The original bug: this ran even when the EVENTide save threw."""
    lines = save_all_src.splitlines()
    idx = next((i for i, l in enumerate(lines)
                if 'set_page_dirty("events", False)' in l), None)
    assert idx is not None, "the events marker is never cleared at all — too far"
    before = lines[idx - 1].strip()
    assert before.startswith("if ") and "may_clear_dirty" in before, (
        f"the events clear must be gated on the recorded outcome; the "
        f"preceding line was: {before!r}"
    )
    # the window-modified flag must be gated the same way
    assert 'may_clear_dirty("EVENTide maps and scripts")' in save_all_src


def test_save_all_shows_one_summary(save_all_src):
    assert "_report_save_outcome" in save_all_src or "summary_text" in save_all_src, (
        "failures must reach the user, not only the log panel"
    )


def test_save_all_keeps_the_detailed_log_lines(save_all_src):
    assert save_all_src.count("log_message") >= 8, (
        "the per-editor log lines are still wanted for troubleshooting"
    )


def test_notification_block_cannot_abort_the_remaining_saves(save_all_src):
    """Even with the wrapper restored, a signal handler raising must not stop
    credits/labels/sound/tilemap/shops from saving."""
    m = re.search(r"notify_scripts_changed", save_all_src)
    assert m
    assert "try:" in save_all_src[:m.start()], (
        "no try: precedes the notification block"
    )
    tail = save_all_src[m.start():].split("# Save credits")[0]
    assert "except Exception" in tail, (
        "the cross-editor notification block must be guarded so a listener "
        "cannot abort the rest of the save"
    )
    assert "could not notify the other editor" in save_all_src, (
        "a notification failure must be reported as a warning, not a save "
        "failure — nothing was lost"
    )


# ── 4. logging must not explode without a console ───────────────────────────

def test_crashlog_tolerates_a_missing_stderr():
    """Under pythonw sys.stderr is None; a StreamHandler on it raises on every
    record, which buried the real traceback 13 times in the user's session."""
    import core.crashlog as crashlog
    src = open(crashlog.__file__, encoding="utf-8").read()
    # The handler binds sys.stdout, so that is what must be guarded. Under
    # pythonw BOTH stdout and stderr are None.
    assert "_stream_is_usable" in src, (
        "crashlog must not attach a stream handler when there is no console"
    )
    assert "_stream_is_usable(sys.stdout)" in src
    from core.crashlog import _stream_is_usable
    assert _stream_is_usable(None) is False


def test_crashlog_still_installs_a_file_handler():
    """Suppressing console noise must not cost us the crash log."""
    src = open(
        os.path.join(_ROOT, "core", "crashlog.py"), encoding="utf-8").read()
    assert "FileHandler" in src


def test_excepthook_is_still_installed():
    """Genuine exceptions must still be recorded, not swallowed.

    `_excepthook` is nested inside `init_logging`, so it is asserted at source
    level: the hook must still be built and assigned to `sys.excepthook`.
    """
    src = open(
        os.path.join(_ROOT, "core", "crashlog.py"), encoding="utf-8").read()
    assert "def _excepthook(" in src
    assert "sys.excepthook = _excepthook" in src, (
        "suppressing console noise must not cost us the exception hook"
    )


def test_console_handler_is_conditional_not_removed():
    """We skip the console handler when there is no console — we do not delete
    console logging for people who DO run from a terminal."""
    src = open(
        os.path.join(_ROOT, "core", "crashlog.py"), encoding="utf-8").read()
    assert "StreamHandler" in src, "console logging must still exist"
    assert "_stream_is_usable(sys.stdout)" in src


def test_stream_usability_check():
    from core.crashlog import _stream_is_usable
    import io
    assert _stream_is_usable(None) is False
    assert _stream_is_usable(io.StringIO()) is True
    closed = io.StringIO()
    closed.close()
    assert _stream_is_usable(closed) is False

    class NoWrite:
        pass
    assert _stream_is_usable(NoWrite()) is False


# ── 5. diagnostic logging is opt-in ─────────────────────────────────────────

def test_diagnostic_logging_is_off_by_default(monkeypatch, tmp_path):
    from core import diag_log
    monkeypatch.delenv(diag_log.ENV_VAR, raising=False)
    diag_log.reset_cache()
    assert diag_log.enabled() is False
    target = tmp_path / "diag.log"
    diag_log.append(str(target), "should not be written")
    assert not target.exists(), "nothing may be written while disabled"


def test_diagnostic_logging_can_be_turned_on(monkeypatch, tmp_path):
    from core import diag_log
    monkeypatch.setenv(diag_log.ENV_VAR, "1")
    diag_log.reset_cache()
    assert diag_log.enabled() is True
    target = tmp_path / "diag.log"
    diag_log.append(str(target), "hello")
    assert target.exists() and "hello" in target.read_text(encoding="utf-8")


@pytest.mark.parametrize("value,expected", [
    ("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True),
    ("0", False), ("false", False), ("", False), ("no", False), ("off", False),
])
def test_diagnostic_flag_parsing(monkeypatch, value, expected):
    from core import diag_log
    monkeypatch.setenv(diag_log.ENV_VAR, value)
    diag_log.reset_cache()
    assert diag_log.enabled() is expected


def test_both_diag_writers_go_through_the_gate():
    src = open(os.path.join(_ROOT, "ui", "mainwindow.py"),
               encoding="utf-8").read()
    for fn in ("def _diag(", "def _log_save_diag("):
        i = src.index(fn)
        body = src[i:i + 900]
        assert "diag_log" in body, (
            f"{fn.strip()} must consult the diagnostic gate so it is silent "
            f"by default"
        )


def test_the_gate_cannot_leak_into_a_release():
    """An env var cannot be committed; assert no file turns it on by default."""
    from core import diag_log
    src = open(diag_log.__file__, encoding="utf-8").read()
    assert "default" in src.lower()
    # the module must not hardcode an enabled default anywhere
    assert not re.search(r"_ENABLED\s*=\s*True", src)


# ── 6. the partial-save dirty traps found by the integration run ────────────
# `saved_tilemap` / `saved_tile_anim` / `saved_shops` are set when ANY item was
# written, so on a PARTIAL save (some written, some refused) they cleared the
# page's dirty dot while work was still outstanding. Caught by driving the real
# _on_save_all on 2026-10-29.

def test_tilesets_dot_is_not_cleared_on_a_partial_save(save_all_src):
    lines = save_all_src.splitlines()
    idx = next((i for i, l in enumerate(lines)
                if 'set_page_dirty("tilesets", False)' in l), None)
    assert idx is not None
    window = "\n".join(lines[max(0, idx - 12):idx])
    assert "_tiles_clean" in window or "may_clear_dirty" in window, (
        "the tilesets dot must not clear when the tilemap or tile-animation "
        "save was partial or failed"
    )
    assert 'outcome.state("Tilemap Editor")' in save_all_src
    assert 'outcome.state("Tile Animations")' in save_all_src


def test_shops_dot_is_not_cleared_on_a_partial_save(save_all_src):
    lines = save_all_src.splitlines()
    idx = next((i for i, l in enumerate(lines)
                if 'set_page_dirty("shops", False)' in l), None)
    assert idx is not None
    before = lines[idx - 1].strip()
    assert "may_clear_dirty" in before, (
        f"the shops dot must follow the recorded outcome; preceding line was "
        f"{before!r}"
    )


def test_partial_counts_as_not_clearable():
    """The rule the two tests above rely on."""
    from ui.save_outcome import SaveOutcome
    o = SaveOutcome()
    o.partial("Tilemap Editor", "one refused")
    assert o.may_clear_dirty("Tilemap Editor") is False
    assert o.state("Tilemap Editor") == "partial"


def test_window_modified_only_cleared_when_nothing_failed(save_all_src):
    m = re.search(r"if outcome\.all_ok\(\):\s*\n\s*self\.setWindowModified\(False\)",
                  save_all_src)
    assert m, (
        "the window-modified flag must stay set after a failure — it is the "
        "last sign that work is still outstanding"
    )


def test_status_bar_never_says_all_saved_when_something_failed(save_all_src):
    """The original misreport: 'All changes saved.' fired if ANY editor saved."""
    # Target the STATEMENT, not the explanatory comment above it.
    i = save_all_src.rindex('showMessage("All changes saved."')
    head = save_all_src[:i]
    assert "should_warn()" in head, (
        "the success message must sit in an elif after the failure check"
    )
    # and the nearest enclosing branch must be the saved-something elif
    guard = next((l.strip() for l in reversed(head.splitlines())
                  if l.strip().startswith(("if ", "elif ", "else"))), "")
    assert guard.startswith("elif") and "saved_names()" in guard, (
        f"the success message must be gated on having actually saved "
        f"something, after the failure check; guard was {guard!r}"
    )
    assert "Save incomplete" in save_all_src


# ── 7. a broken DIRTY CHECK must not abort the save either ──────────────────
#
# Found 2026-10-29 during the final Save All verification, in answer to "what
# happens if an editor unexpectedly raises outside its normal failure-handling
# path". Each editor's save is wrapped in try/except, but the question that
# PRECEDES it — has_unsaved_changes() / isWindowModified() / _dirty_inst_keys —
# sat OUTSIDE that guard at nine call sites. A raising predicate therefore
# killed Save All mid-sequence and skipped every later editor: the same data
# loss as the original crash, by a different route. Reproduced before the fix
# (scratchpad/repro_dirtycheck.py): Credits' has_unsaved_changes raising
# AttributeError left Label Manager with 0 save attempts.
#
# The rule: when an editor cannot say whether it has unsaved work, that editor
# is recorded FAILED (so its marker STAYS and the summary names it) and the
# save continues with the rest. It must NOT be silently treated as clean.

class _Status:
    def __init__(self):
        self.msg = ""

    def showMessage(self, m, t=0):
        self.msg = m


def _make_stand(**editors):
    """Bind the SHIPPING _on_save_all to a minimal real QWidget.

    A QWidget, not a bare object: the method passes `self` to QMessageBox as
    the dialog parent, and handing Qt a non-widget there aborts the process at
    the C++ level. A second QMainWindow is not used — constructing one beside
    an existing full window crashed Qt outright.
    """
    from PyQt6.QtWidgets import QWidget
    import ui.unified_mainwindow as um
    from core.shared_data import ProjectData

    class Stand(QWidget):
        _on_save_all = um.UnifiedMainWindow._on_save_all
        _report_save_outcome = um.UnifiedMainWindow._report_save_outcome
        _editor_dirty = getattr(um.UnifiedMainWindow, "_editor_dirty", None)
        _confirm_saved = getattr(um.UnifiedMainWindow, "_confirm_saved", None)

        def __init__(self):
            super().__init__()
            self.dirty_calls = []
            self.log = []
            self._st = _Status()
            self._modified = True
            self.project_data = ProjectData({})
            self._porysuite_window = None
            self._eventide_window = None
            for k, v in editors.items():
                setattr(self, k, v)

        def set_page_dirty(self, page, dirty):
            self.dirty_calls.append((page, dirty))

        def log_message(self, m):
            self.log.append(m)

        def statusBar(self):
            return self._st

        def setWindowModified(self, v):
            self._modified = v

        def isWindowModified(self):
            return self._modified

        def logtext(self):
            return "\n".join(self.log)

    return Stand()


class _Exploding:
    """An editor whose DIRTY CHECK raises — not its save."""

    def __init__(self, name="Exploding"):
        self.name = name
        self.attempts = 0

    def has_unsaved_changes(self):
        raise AttributeError(
            "'" + self.name + "' object has no attribute '_entries'")

    def _on_save(self):
        self.attempts += 1


class _Willing:
    def __init__(self, name="Willing"):
        self.name = name
        self.attempts = 0
        self._dirty = True

    def has_unsaved_changes(self):
        return self._dirty

    def _on_save(self):
        self.attempts += 1
        self._dirty = False

    def _save_labels(self):
        self._on_save()


@pytest.fixture
def quiet_boxes(monkeypatch):
    """Capture dialogs instead of showing them."""
    from PyQt6.QtWidgets import QMessageBox
    seen = []

    def rec(parent, title, text, *a, **k):
        seen.append((title, text))
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "warning", staticmethod(rec))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(rec))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(rec))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(
        lambda *a, **k: QMessageBox.StandardButton.Yes))
    return seen


def test_raising_dirty_check_does_not_abort_save_all(quiet_boxes):
    """THE DEFECT: a raising predicate skipped every later editor."""
    later = _Willing("Labels")
    s = _make_stand(_credits_editor=_Exploding("CreditsEditor"),
                    _label_manager=later)
    s._on_save_all()                      # must not raise
    assert later.attempts == 1, (
        "an editor AFTER the broken dirty check must still be given a chance "
        "to save — this is the six-editor data loss, by a different route"
    )


def test_unanswerable_dirty_check_is_recorded_as_failed(quiet_boxes):
    """'I cannot tell' must not be filed as 'nothing to save'.

    SaveOutcome.nothing_to_save() OVERWRITES, so a helper that merely returned
    False would fall through to the `elif hasattr(...)` below each check and
    relabel the failure as 'nothing to save' — clearing the user's marker with
    the work still unsaved. The check must therefore distinguish
    dirty / clean / unanswerable.
    """
    s = _make_stand(_credits_editor=_Exploding("CreditsEditor"),
                    _label_manager=_Willing("Labels"))
    s._on_save_all()
    assert quiet_boxes, "a failure the user must know about shows one dialog"
    text = quiet_boxes[0][1]
    assert "Credits" in text and "NOT saved" in text, (
        "the summary must name the editor that could not be checked: "
        + repr(text))
    assert "all changes saved" not in text.lower()
    assert "incomplete" in s._st.msg.lower(), s._st.msg
    assert s._modified is True, (
        "work is still outstanding, so the window-modified flag must stay")


def test_broken_dirty_check_does_not_clear_that_editors_marker(quiet_boxes):
    s = _make_stand(_credits_editor=_Exploding("CreditsEditor"),
                    _label_manager=_Willing("Labels"))
    s._on_save_all()
    assert ("credits", False) not in s.dirty_calls, (
        "an editor we could not even check must keep its unsaved marker")
    assert ("labels", False) in s.dirty_calls, (
        "the editor that did save must still have its marker cleared")


def test_broken_dirty_check_is_logged(quiet_boxes):
    s = _make_stand(_credits_editor=_Exploding("CreditsEditor"))
    s._on_save_all()
    assert "unsaved changes" in s.logtext().lower(), s.logtext()


def test_no_dirty_check_is_left_outside_a_guard(save_all_src):
    """Source guard: every predicate in _on_save_all must be guarded.

    Catches a new editor being added to the sequence with a bare
    `x.has_unsaved_changes()` in its `if`, which would reopen the defect.
    """
    offenders = []
    depth_of_try = None
    stmt = ""          # logical statement: a call may span several lines
    stmt_open = 0

    def judge(text):
        if "has_unsaved_changes" not in text and "isWindowModified" not in text \
                and "_dirty_inst_keys" not in text:
            return
        # _editor_dirty(...) IS the guard; a bare call is not.
        if "_editor_dirty(" not in text:
            offenders.append(" ".join(text.split()))

    for raw in save_all_src.splitlines():
        line = raw.split("#")[0].rstrip() if not raw.strip().startswith("#") \
            else ""
        if not line.strip():
            continue
        indent = len(raw) - len(raw.lstrip())
        if stmt_open == 0:
            if line.strip().startswith("try:"):
                depth_of_try = indent
            elif depth_of_try is not None and indent <= depth_of_try and not \
                    line.strip().startswith(("except", "finally", "else:")):
                depth_of_try = None
        if depth_of_try is not None:
            continue                       # inside a try block: guarded
        stmt = (stmt + " " + line.strip()) if stmt_open else line.strip()
        stmt_open += line.count("(") - line.count(")")
        if stmt_open <= 0 and not stmt.rstrip().endswith("\\"):
            judge(stmt)
            stmt, stmt_open = "", 0
    assert not offenders, (
        "these dirty checks can raise and abort Save All before the later "
        "editors save — route them through self._editor_dirty(...):\n  "
        + "\n  ".join(offenders)
    )


# ── 8. a save that fails WITHOUT raising must not be reported as success ────
#
# Found 2026-10-29. Save All's per-editor try/except only notices an editor
# that RAISES. But three of the editors it calls for side effect only —
#   ui/credits_editor.py::_on_save
#   ui/label_manager.py::_save_labels
#   eventide/ui/event_editor_tab.py::_on_save
# each catch their own write error, show their OWN dialog and return normally.
# Two of them also have early `return`s that write nothing (no project
# directory, no map loaded, the user cancelling the overwrite prompt), and
# EVENTide has a "save cancelled, nothing written" path.
#
# So a returning call is NOT proof of a save. Reproduced before the fix
# (scratchpad/repro_silent_fail.py): two editors failed to write, both still
# reported unsaved changes, and Save All said "All changes saved.", cleared the
# labels dot and cleared the window-modified flag. That is exactly the
# misreport this whole file exists to prevent, arriving by a route the original
# repair did not cover.
#
# THE FIX IS CONFINED TO _on_save_all: after the save call returns, ask the
# editor again whether it still has unsaved changes. Each of these editors
# clears its dirty flag ONLY after a successful write, so still-dirty is proof
# the work is not on disk. No editor was modified — their own Save buttons rely
# on that self-contained error handling.

class _SilentlyFailing:
    """Mimics credits_editor._on_save's own error handling, exactly:
    show a dialog, leave the dirty flag set, return normally."""

    def __init__(self, name="Credits"):
        self.name = name
        self._dirty = True
        self.attempts = 0

    def has_unsaved_changes(self):
        return self._dirty

    def _on_save(self):
        self.attempts += 1
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.critical(None, "Save Error", "Failed to save: read-only")
        # dirty flag deliberately NOT cleared — as in the real editor

    def _save_labels(self):
        self._on_save()


class _SilentlyDoingNothing:
    """Mimics the early `return` paths: no project dir / no map / cancelled."""

    def __init__(self, name="Credits"):
        self.name = name
        self._dirty = True
        self.attempts = 0

    def has_unsaved_changes(self):
        return self._dirty

    def _on_save(self):
        self.attempts += 1          # returns having written nothing

    def _save_labels(self):
        self._on_save()


def test_silent_write_failure_is_not_reported_as_saved(quiet_boxes):
    """THE DEFECT: 'All changes saved.' while nothing reached the disk."""
    cred = _SilentlyFailing("Credits")
    s = _make_stand(_credits_editor=cred)
    s._on_save_all()
    assert cred.attempts == 1
    assert "all changes saved" not in s._st.msg.lower(), (
        f"the editor never wrote anything and still reports unsaved changes; "
        f"Save All must not claim success. status was {s._st.msg!r}"
    )
    assert "incomplete" in s._st.msg.lower(), s._st.msg


def test_silent_write_failure_keeps_the_markers(quiet_boxes):
    cred = _SilentlyFailing("Credits")
    lab = _SilentlyFailing("Labels")
    s = _make_stand(_credits_editor=cred, _label_manager=lab)
    s._on_save_all()
    assert ("labels", False) not in s.dirty_calls, (
        "the labels dot must not be cleared when the write did not happen")
    assert s._modified is True, (
        "the window-modified flag is the last sign work is outstanding")


def test_silent_write_failure_is_named_in_the_summary(quiet_boxes):
    s = _make_stand(_credits_editor=_SilentlyFailing("Credits"),
                    _label_manager=_SilentlyFailing("Labels"))
    s._on_save_all()
    # The editors' OWN "Save Error" dialogs are their existing behaviour and
    # are left alone deliberately — they carry the specific error. What must
    # not multiply is Save All's own summary.
    summaries = [t for t in quiet_boxes if "NOT saved" in t[0]]
    assert len(summaries) == 1, (
        f"Save All must add exactly one summary, not a barrage: "
        f"got {len(summaries)} of {[t for t, _ in quiet_boxes]}")
    text = summaries[0][1]
    assert "Credits" in text and "Label Manager" in text, text
    assert "NOT saved" in text, text


def test_a_save_that_writes_nothing_is_not_a_success(quiet_boxes):
    """The early-return paths, which raise nothing at all."""
    cred = _SilentlyDoingNothing("Credits")
    s = _make_stand(_credits_editor=cred)
    s._on_save_all()
    assert cred.attempts == 1
    assert "all changes saved" not in s._st.msg.lower(), s._st.msg


def test_a_genuine_save_is_still_reported_as_saved(quiet_boxes):
    """The fix must not turn working saves into false failures."""
    s = _make_stand(_credits_editor=_Willing("Credits"),
                    _label_manager=_Willing("Labels"))
    s._on_save_all()
    assert not quiet_boxes, (
        f"a clean save must not warn: {quiet_boxes[:1]}")
    assert "All changes saved" in s._st.msg, s._st.msg
    assert ("labels", False) in s.dirty_calls
    assert s._modified is False


def test_save_outcome_is_confirmed_not_assumed(save_all_src):
    """Source guard: the editors called for side effect must be confirmed.

    Catches someone reinstating `outcome.saved("Credits")` straight after the
    call, which is what made the misreport possible.
    """
    import re as _re
    for label, call in (("Credits", r"_credits_editor\._on_save\(\)"),
                        ("Label Manager", r"_label_manager\._save_labels\(\)"),
                        ("EVENTide maps and scripts", r"eet\._on_save\(\)")):
        m = _re.search(call + r"(.{0,400})", save_all_src, _re.S)
        assert m, f"{label}: save call not found"
        after = m.group(1)
        assert "_confirm_saved(" in after, (
            f"{label}: the save call must be followed by _confirm_saved(...) "
            f"— this editor swallows its own write errors, so a returning "
            f"call is not proof of a save"
        )
        head = after[:after.index("_confirm_saved(")]
        assert f'outcome.saved("{label}")' not in head, (
            f"{label}: outcome.saved(...) must not be recorded before the "
            f"save is confirmed"
        )
