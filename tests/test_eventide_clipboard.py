"""Copy and paste a command the way every other program does it.

Copying a command only ever set a variable inside the editor; it never touched
the SYSTEM clipboard. Paste reads the system clipboard first, so Ctrl+C then
Ctrl+V pasted whatever had last been copied anywhere else on the machine — a
URL from a browser, in the report that found this.

Worse, the snippet parser accepts any line at all as a command:

    'https://example.com/a?b=1'  ->  [('https://example.com/a?b=1',)]

so that URL went in as a command rather than being refused.

Both halves are covered here: copying puts real script on the system
clipboard, and pasting only accepts text that is actually script — while
still allowing script copied from a file or a wiki, which is a real feature.
"""
from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

pytest.importorskip('PyQt6.QtWidgets')

from PyQt6.QtWidgets import QApplication  # noqa: E402

import eventide.ui.event_editor_tab as eet  # noqa: E402


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


MAP = {
    'name': 'Woods',
    'object_events': [
        {'graphics_id': 'OBJ_EVENT_GFX_MAN', 'x': 1, 'y': 1,
         'script': 'Woods_EventScript_Man'},
    ],
}

SCRIPTS = """Woods_EventScript_Man::
lockall
waitmovement
releaseall
end
"""

# A tiny stand-in for the project's macro list, which is what decides whether
# clipboard text is script. Real macro files are tested in their own suite.
MACROS = ("\t.macro lockall\n\t.endm\n"
          "\t.macro waitmovement\n\t.endm\n"
          "\t.macro releaseall\n\t.endm\n"
          "\t.macro end\n\t.endm\n"
          "\t.macro msgbox text:req, type=0\n\t.endm\n")


@pytest.fixture
def tab(qapp, tmp_path, monkeypatch):
    for name in ('information', 'warning', 'critical', 'question'):
        monkeypatch.setattr(eet.QMessageBox, name,
                            staticmethod(lambda *a, **k: None), raising=False)

    class _StubMW:
        def log_message(self, *a, **k):
            pass

        def isWindowModified(self):
            return False

        def setWindowModified(self, *a):
            pass

    (tmp_path / 'asm' / 'macros').mkdir(parents=True)
    (tmp_path / 'asm' / 'macros' / 'event.inc').write_text(
        MACROS, encoding='utf-8')
    d = tmp_path / 'data' / 'maps' / 'Woods'
    d.mkdir(parents=True)
    (d / 'map.json').write_text(json.dumps(MAP), encoding='utf-8')
    (d / 'scripts.inc').write_text(SCRIPTS, encoding='utf-8')
    (d / 'text.inc').write_text('', encoding='utf-8')

    from eventide.backend.macro_params import clear_cache
    clear_cache()
    t = eet.EventEditorTab(_StubMW())
    t._root_dir = tmp_path
    t._load_map(d)
    t._display_object(0)
    t._loading = False
    eet.EventEditorTab._clipboard = None
    eet.EventEditorTab._clipboard_text = None
    yield t
    # The SYSTEM clipboard is shared with every other test in the run, and
    # these tests deliberately fill it with script. Leaving it dirty made an
    # older test that pastes from the editor's own clipboard read this file's
    # leftovers instead — it passed alone and failed in the suite.
    QApplication.clipboard().setText('')
    eet.EventEditorTab._clipboard = None
    eet.EventEditorTab._clipboard_text = None
    clear_cache()


# ── copying reaches the system clipboard ──────────────────────────────────

def test_copying_a_command_puts_script_on_the_system_clipboard(tab, qapp):
    tab._cmd_list.setCurrentRow(0)
    tab._on_copy()
    assert 'lockall' in QApplication.clipboard().text()


def test_what_was_copied_is_what_pastes(tab, qapp):
    """The reported bug: a URL on the clipboard pasted instead of the copy."""
    QApplication.clipboard().setText('https://example.com/some/page?a=1')
    tab._cmd_list.setCurrentRow(0)
    tab._on_copy()
    before = len(tab._cmd_tuples)
    tab._on_paste()
    assert len(tab._cmd_tuples) == before + 1
    assert not any('http' in str(c) for c in tab._cmd_tuples)


def test_cut_also_reaches_the_system_clipboard(tab, qapp):
    tab._cmd_list.setCurrentRow(0)
    tab._on_cut()
    assert 'lockall' in QApplication.clipboard().text()


# ── pasting refuses what is not script ────────────────────────────────────

def test_a_url_is_not_pasted_as_a_command(tab, qapp):
    QApplication.clipboard().setText('https://example.com/some/page?a=1')
    before = list(tab._cmd_tuples)
    tab._on_paste()
    assert tab._cmd_tuples == before


def test_a_sentence_is_not_pasted_as_a_command(tab, qapp):
    QApplication.clipboard().setText('just some notes I copied earlier')
    before = list(tab._cmd_tuples)
    tab._on_paste()
    assert tab._cmd_tuples == before


def test_a_file_path_is_not_pasted_as_a_command(tab, qapp):
    QApplication.clipboard().setText(r'C:\Users\me\Desktop\thing.png')
    before = list(tab._cmd_tuples)
    tab._on_paste()
    assert tab._cmd_tuples == before


# ── but real script from elsewhere still pastes ───────────────────────────

def test_script_copied_from_a_file_still_pastes(tab, qapp):
    QApplication.clipboard().setText('lockall\nwaitmovement\nreleaseall\n')
    before = len(tab._cmd_tuples)
    tab._on_paste()
    assert len(tab._cmd_tuples) == before + 3


def test_labels_and_comments_do_not_disqualify_it(tab, qapp):
    QApplication.clipboard().setText(
        '@ a comment\nWoods_EventScript_X::\nlockall\nreleaseall\n')
    before = len(tab._cmd_tuples)
    tab._on_paste()
    assert len(tab._cmd_tuples) > before


def test_one_bad_line_disqualifies_the_whole_paste(tab, qapp):
    """Half script, half something else is not script — do not guess."""
    QApplication.clipboard().setText(
        'lockall\nhttps://example.com/oops\nreleaseall\n')
    before = list(tab._cmd_tuples)
    tab._on_paste()
    assert tab._cmd_tuples == before


# ── never refuse on a guess ───────────────────────────────────────────────

def test_with_no_macro_list_anything_is_still_allowed(tab, qapp, monkeypatch):
    """If the project's commands could not be read, do not block pasting."""
    monkeypatch.setattr('eventide.backend.macro_params.get_macros',
                        lambda _root: {})
    assert tab._looks_like_script('whatever this is')


def test_an_empty_clipboard_with_a_copy_still_pastes_the_copy(tab, qapp):
    tab._cmd_list.setCurrentRow(0)
    tab._on_copy()
    QApplication.clipboard().setText('')
    before = len(tab._cmd_tuples)
    tab._on_paste()
    assert len(tab._cmd_tuples) == before + 1
