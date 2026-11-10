"""Find unused must not silently swap out a flag that is already doing a job.

The button hands out a BRAND-NEW flag. That is right for an event with no flag
and wrong for one that already has a flag its own scripts name — which is the
normal case for an item ball, or a one-off NPC who leaves and stays gone.

What replacing it does: the object starts hiding on the new flag while every
`setflag` / `clearflag` still names the old one. The pairing is broken, so the
event comes back every single time the player re-enters the map. It assembles,
it links, and only playing it shows the fault.

This happened to a real map. The postman on Faron hid on `FLAG_0x022` and his
two intro triggers both ended `setflag FLAG_0x022`. One press of Find unused
offered `FLAG_0x023` with no warning at all; saving it would have left him
re-appearing forever.

So the button now asks first, and says how many scripts on the map still name
the flag it is about to drop. It still does what it is told — the point is
that the user is told what they are telling it.

Projects here are built in temp directories; no game source is read.
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
        # Carries a flag its own scripts use — the dangerous case.
        {'graphics_id': 'OBJ_EVENT_GFX_MAN', 'x': 11, 'y': 18, 'elevation': 3,
         'local_id': 'LOCALID_WOODS_POSTMAN', 'flag': 'FLAG_0x022',
         'movement_type': 'MOVEMENT_TYPE_NONE',
         'script': 'Woods_EventScript_Postman'},
        # Carries a flag nothing references.
        {'graphics_id': 'OBJ_EVENT_GFX_WOMAN', 'x': 4, 'y': 7, 'elevation': 3,
         'local_id': 'LOCALID_WOODS_LADY', 'flag': 'FLAG_0x030',
         'movement_type': 'MOVEMENT_TYPE_NONE',
         'script': 'Woods_EventScript_Lady'},
        # Carries no flag at all — must not be interrupted.
        {'graphics_id': 'OBJ_EVENT_GFX_BOY', 'x': 6, 'y': 9, 'elevation': 3,
         'local_id': 'LOCALID_WOODS_BOY', 'flag': '0',
         'movement_type': 'MOVEMENT_TYPE_NONE',
         'script': 'Woods_EventScript_Boy'},
    ],
    'coord_events': [],
    'bg_events': [],
}

SCRIPTS = """\
Woods_MapScripts::
\t.byte 0

Woods_EventScript_Postman::
\tend

Woods_EventScript_Intro::
\tremoveobject 8
\tsetflag FLAG_0x022
\tend

Woods_EventScript_IntroRight::
\tremoveobject 8
\tsetflag FLAG_0x022
\tend

Woods_EventScript_Lady::
\tend

Woods_EventScript_Boy::
\tend
"""


@pytest.fixture
def tab(qapp, tmp_path, monkeypatch):
    for name in ('information', 'warning', 'critical'):
        monkeypatch.setattr(eet.QMessageBox, name,
                            staticmethod(lambda *a, **k: None), raising=False)

    class _StubMW:
        project_dir = ''

        def __init__(self):
            self.logs = []

        def log_message(self, msg, *a, **k):
            self.logs.append(msg)

        def isWindowModified(self):
            return False

    inc = tmp_path / 'include' / 'constants'
    inc.mkdir(parents=True)
    (inc / 'flags.h').write_text(
        '\n'.join(f'#define FLAG_0x{v:03X}   0x{v:03X}'
                  for v in range(0x20, 0x40)) + '\n', encoding='utf-8')

    d = tmp_path / 'data' / 'maps' / 'Woods'
    d.mkdir(parents=True)
    (d / 'map.json').write_text(json.dumps(MAP), encoding='utf-8')
    (d / 'scripts.inc').write_text(SCRIPTS, encoding='utf-8')
    (d / 'text.inc').write_text('', encoding='utf-8')

    t = eet.EventEditorTab(_StubMW())
    t._root_dir = tmp_path
    t._load_map(d)
    return t


def _select(tab, label: str):
    idx = next(i for i, o in enumerate(tab._objects)
               if o.get('script') == label)
    tab._display_object(idx)
    return idx


def _asked(monkeypatch, answer):
    """Record every confirmation shown, and answer with *answer*."""
    seen = []

    def q(_parent, title, body, *a, **k):
        seen.append((title, body))
        return answer

    monkeypatch.setattr(eet.QMessageBox, 'question', staticmethod(q))
    return seen


# ─────────────────────────────────────────────────────────────────────────────
# Which flags the map's own scripts name
# ─────────────────────────────────────────────────────────────────────────────

def test_it_finds_the_scripts_that_name_the_flag(tab):
    users = set(tab._flag_users_on_this_map('FLAG_0x022'))
    assert users == {'Woods_EventScript_Intro', 'Woods_EventScript_IntroRight'}


def test_a_flag_nothing_names_has_no_users(tab):
    assert tab._flag_users_on_this_map('FLAG_0x030') == []


def test_a_blank_flag_has_no_users(tab):
    assert tab._flag_users_on_this_map('') == []
    assert tab._flag_users_on_this_map('0') == []


def test_a_near_miss_name_is_not_counted(tab):
    """`FLAG_0x02` is not `FLAG_0x022` — nothing names the shorter one."""
    assert tab._flag_users_on_this_map('FLAG_0x099') == []


# ─────────────────────────────────────────────────────────────────────────────
# It asks before replacing
# ─────────────────────────────────────────────────────────────────────────────

def test_it_asks_before_replacing_a_flag_in_use(tab, monkeypatch):
    seen = _asked(monkeypatch, eet.QMessageBox.StandardButton.Cancel)
    _select(tab, 'Woods_EventScript_Postman')
    tab._on_pick_unused_object_flag()
    assert len(seen) == 1


def test_cancelling_leaves_the_flag_exactly_as_it_was(tab, monkeypatch):
    """THE regression. One unwarned press used to be enough to lose it."""
    _asked(monkeypatch, eet.QMessageBox.StandardButton.Cancel)
    _select(tab, 'Woods_EventScript_Postman')
    tab._on_pick_unused_object_flag()
    assert tab.obj_flag_combo.selected_constant() == 'FLAG_0x022'


def test_the_question_names_the_flag_being_dropped(tab, monkeypatch):
    seen = _asked(monkeypatch, eet.QMessageBox.StandardButton.Cancel)
    _select(tab, 'Woods_EventScript_Postman')
    tab._on_pick_unused_object_flag()
    assert 'FLAG_0x022' in seen[0][1]


def test_the_question_says_how_many_scripts_still_use_it(tab, monkeypatch):
    seen = _asked(monkeypatch, eet.QMessageBox.StandardButton.Cancel)
    _select(tab, 'Woods_EventScript_Postman')
    tab._on_pick_unused_object_flag()
    body = seen[0][1]
    assert '2 script(s)' in body
    assert 'Woods_EventScript_Intro' in body


def test_saying_yes_still_replaces_it(tab, monkeypatch):
    """The guard warns; it does not overrule."""
    _asked(monkeypatch, eet.QMessageBox.StandardButton.Yes)
    _select(tab, 'Woods_EventScript_Postman')
    tab._on_pick_unused_object_flag()
    got = tab.obj_flag_combo.selected_constant()
    assert got and got != 'FLAG_0x022'


def test_the_change_is_logged_with_both_flags(tab, monkeypatch):
    _asked(monkeypatch, eet.QMessageBox.StandardButton.Yes)
    _select(tab, 'Woods_EventScript_Postman')
    tab._on_pick_unused_object_flag()
    assert any('FLAG_0x022 to FLAG_' in m for m in tab._mw.logs)


# ─────────────────────────────────────────────────────────────────────────────
# …and does not get in the way otherwise
# ─────────────────────────────────────────────────────────────────────────────

def test_an_event_with_no_flag_is_not_interrupted(tab, monkeypatch):
    seen = _asked(monkeypatch, eet.QMessageBox.StandardButton.Cancel)
    _select(tab, 'Woods_EventScript_Boy')
    tab._on_pick_unused_object_flag()
    assert seen == []
    assert tab.obj_flag_combo.selected_constant().startswith('FLAG_')


def test_a_flag_nothing_uses_still_asks_but_says_so(tab, monkeypatch):
    """Still a replacement, so still the user's call — but the wording must
    not imply damage that is not there."""
    seen = _asked(monkeypatch, eet.QMessageBox.StandardButton.Cancel)
    _select(tab, 'Woods_EventScript_Lady')
    tab._on_pick_unused_object_flag()
    assert len(seen) == 1
    assert 'probably harmless' in seen[0][1]


def test_the_flag_it_hands_out_is_not_one_already_in_use(tab, monkeypatch):
    _asked(monkeypatch, eet.QMessageBox.StandardButton.Yes)
    _select(tab, 'Woods_EventScript_Postman')
    tab._on_pick_unused_object_flag()
    got = tab.obj_flag_combo.selected_constant()
    assert got not in ('FLAG_0x022', 'FLAG_0x030')
