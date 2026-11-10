"""A flag can be made to start switched ON, without hand-editing the game.

Flags are off in a fresh save file. That is wrong for a hide flag: the engine
skips spawning an object while its hide flag is ON, so "this person is not in
the world yet" has to START on and be cleared when they should appear. Vanilla
does it with one script run once at the beginning of a new game — a long run of
``setflag`` lines — and nothing in PorySuite could read or write it.

The visible cost: a hide flag set in the editor worked perfectly and then never
came on. The NPC stood in the street from the first save file, and the scene
that was supposed to reveal them cleared a flag that was already clear. The
only ways out were editing the game's own source by hand, which this project
forbids, or giving up on the feature.

Three things here are easy to get wrong:

* **The script's name.** It is whatever ``new_game.c`` hands to
  ``RunScriptImmediately``. Hardcoding ``EventScript_ResetAllMapFlags`` works
  until somebody renames it, and then silently edits nothing.
* **Where a new line goes.** After the terminator it never runs. Inside a
  conditional-assembly branch it may not be built.
* **Which lines belong to this script.** ``data/event_scripts.s`` holds dozens
  of scripts. A ``setflag`` for the same flag in a different one is somebody
  else's line.

Projects here are built in temp directories; the real game source is never
read or written.
"""
from __future__ import annotations

import pytest

from eventide.backend import new_game_state as ngs

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

NEW_GAME_C = """\
#include "global.h"

static void NewGameInitData(void)
{
    ClearPokedexFlags();
    SetAllRenewableItemFlags();
    RunScriptImmediately(EventScript_ResetAllMapFlags);
}
"""

SCRIPTS_S = """\
EventScript_SomethingElse::
\tsetflag FLAG_SHARED_NAME
\tend

EventScript_ResetAllMapFlags::
\tsetflag FLAG_HIDE_OAK_IN_PALLET_TOWN
\tsetflag FLAG_HIDE_CERULEAN_RIVAL
\tsetflag FLAG_SHARED_NAME
\tsetvar VAR_MASSAGE_COOLDOWN_STEP_COUNTER, 500
\tend

EventScript_AfterwardsEntirely::
\tsetflag FLAG_NOT_OURS
\tend
"""


def _project(tmp_path, new_game_c: str = NEW_GAME_C,
             scripts_s: str = SCRIPTS_S, *, name='event_scripts.s'):
    (tmp_path / 'src').mkdir(parents=True, exist_ok=True)
    (tmp_path / 'src' / 'new_game.c').write_text(new_game_c, encoding='utf-8')
    (tmp_path / 'data').mkdir(parents=True, exist_ok=True)
    p = tmp_path / 'data' / name
    with p.open('w', encoding='utf-8', newline='') as fh:
        fh.write(scripts_s)
    return tmp_path


def _raw(tmp_path, name='event_scripts.s') -> bytes:
    return (tmp_path / 'data' / name).read_bytes()


def _block(tmp_path, name='event_scripts.s') -> list[str]:
    text = (tmp_path / 'data' / name).read_text(encoding='utf-8')
    body = text.split('EventScript_ResetAllMapFlags::', 1)[1]
    out = []
    for line in body.splitlines():
        s = line.strip()
        if not s:
            continue
        out.append(s)
        if s in ('end', 'return'):
            break
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Finding the script
# ─────────────────────────────────────────────────────────────────────────────

def test_the_script_is_found_through_new_game_c(tmp_path):
    s = ngs.read(_project(tmp_path))
    assert s.label == 'EventScript_ResetAllMapFlags'
    assert s.path is not None and s.path.name == 'event_scripts.s'
    assert s.found


def test_a_renamed_script_is_still_found(tmp_path):
    """The whole reason the name is read instead of written down.

    Hardcoding the vanilla name would silently edit nothing here.
    """
    root = _project(
        tmp_path,
        new_game_c=NEW_GAME_C.replace('EventScript_ResetAllMapFlags',
                                      'EventScript_HyruleNewGame'),
        scripts_s=SCRIPTS_S.replace('EventScript_ResetAllMapFlags',
                                    'EventScript_HyruleNewGame'))
    s = ngs.read(root)
    assert s.label == 'EventScript_HyruleNewGame'
    assert s.sets_flag('FLAG_HIDE_CERULEAN_RIVAL')


def test_a_script_in_an_inc_file_is_found_too(tmp_path):
    root = _project(tmp_path, name='new_game_scripts.inc')
    assert ngs.read(root).path.name == 'new_game_scripts.inc'


def test_no_new_game_c_means_no_script(tmp_path):
    (tmp_path / 'data').mkdir(parents=True)
    assert not ngs.read(tmp_path).found


def test_a_call_whose_label_is_nowhere_is_not_claimed(tmp_path):
    root = _project(tmp_path, scripts_s='EventScript_Unrelated::\n\tend\n')
    assert not ngs.read(root).found


def test_the_script_with_the_most_setflags_wins(tmp_path):
    """A behaviour test, not a name test.

    A project that runs two scripts at new game gets the one that is actually
    doing the flag work, whatever either is called.
    """
    c = NEW_GAME_C.replace(
        '    RunScriptImmediately(EventScript_ResetAllMapFlags);',
        '    RunScriptImmediately(EventScript_Trivial);\n'
        '    RunScriptImmediately(EventScript_ResetAllMapFlags);')
    s = SCRIPTS_S + '\nEventScript_Trivial::\n\tsetflag FLAG_ONE\n\tend\n'
    assert ngs.read(_project(tmp_path, new_game_c=c, scripts_s=s)).label \
        == 'EventScript_ResetAllMapFlags'


# ─────────────────────────────────────────────────────────────────────────────
# Reading it
# ─────────────────────────────────────────────────────────────────────────────

def test_it_reads_the_flags_in_order(tmp_path):
    assert ngs.read(_project(tmp_path)).flags == [
        'FLAG_HIDE_OAK_IN_PALLET_TOWN',
        'FLAG_HIDE_CERULEAN_RIVAL',
        'FLAG_SHARED_NAME',
    ]


def test_it_reads_starting_values_too(tmp_path):
    assert ngs.read(_project(tmp_path)).vars == {
        'VAR_MASSAGE_COOLDOWN_STEP_COUNTER': '500'}


def test_it_stops_at_the_terminator(tmp_path):
    """The next script's flags are not this script's flags."""
    assert 'FLAG_NOT_OURS' not in ngs.read(_project(tmp_path)).flags


def test_a_line_past_the_terminator_is_not_read_as_ours(tmp_path):
    """`end` ends the block, not merely the next label.

    Without this the previous test passes on the strength of the next
    ``::`` label alone, so disabling the terminator check entirely goes
    unnoticed. Here the stray line sits after ``end`` and before any label —
    exactly where only the terminator can stop the read. It is unreachable
    code the engine never runs, so reporting it as a new-game flag would put
    a tick against a flag that is never set.
    """
    s = SCRIPTS_S.replace(
        '\tsetvar VAR_MASSAGE_COOLDOWN_STEP_COUNTER, 500\n\tend\n',
        '\tsetvar VAR_MASSAGE_COOLDOWN_STEP_COUNTER, 500\n\tend\n'
        '\tsetflag FLAG_PAST_THE_END\n')
    state = ngs.read(_project(tmp_path, scripts_s=s))
    assert state.flags == ['FLAG_HIDE_OAK_IN_PALLET_TOWN',
                           'FLAG_HIDE_CERULEAN_RIVAL',
                           'FLAG_SHARED_NAME']
    assert not state.sets_flag('FLAG_PAST_THE_END')


def test_a_new_flag_still_lands_before_that_terminator(tmp_path):
    """And the writer agrees with the reader about where the block ends."""
    s = SCRIPTS_S.replace(
        '\tsetvar VAR_MASSAGE_COOLDOWN_STEP_COUNTER, 500\n\tend\n',
        '\tsetvar VAR_MASSAGE_COOLDOWN_STEP_COUNTER, 500\n\tend\n'
        '\tsetflag FLAG_PAST_THE_END\n')
    root = _project(tmp_path, scripts_s=s)
    assert ngs.set_flag(root, 'FLAG_0x022', True)[0]
    body = _raw(root).decode('utf-8').split(
        'EventScript_ResetAllMapFlags::', 1)[1]
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    assert lines.index('setflag FLAG_0x022') < lines.index('end')


def test_it_does_not_start_early(tmp_path):
    """Nor are the PREVIOUS script's. `FLAG_SHARED_NAME` appears in both, so
    its presence proves nothing — `FLAG_HIDE_OAK...` being first does."""
    assert ngs.read(_project(tmp_path)).flags[0] \
        == 'FLAG_HIDE_OAK_IN_PALLET_TOWN'


def test_conditional_assembly_locks_the_block(tmp_path):
    """A line added here might land in a branch that is not built."""
    s = SCRIPTS_S.replace(
        '\tsetflag FLAG_HIDE_CERULEAN_RIVAL',
        '\t.ifdef HYRULE\n\tsetflag FLAG_HIDE_CERULEAN_RIVAL\n\t.endif')
    state = ngs.read(_project(tmp_path, scripts_s=s))
    assert state.found
    assert state.locked
    assert not state.editable


def test_an_ordinary_block_is_not_locked(tmp_path):
    assert ngs.read(_project(tmp_path)).editable


# ─────────────────────────────────────────────────────────────────────────────
# Switching a flag on
# ─────────────────────────────────────────────────────────────────────────────

def test_adding_a_flag_adds_exactly_one_line(tmp_path):
    root = _project(tmp_path)
    before = _raw(root).decode('utf-8').splitlines()
    changed, msg = ngs.set_flag(root, 'FLAG_0x022', True)
    assert changed, msg
    after = _raw(root).decode('utf-8').splitlines()
    assert len(after) - len(before) == 1
    assert set(after) - set(before) == {'\tsetflag FLAG_0x022'}


def test_the_new_line_goes_before_the_terminator(tmp_path):
    """A command after `end` never runs."""
    root = _project(tmp_path)
    ngs.set_flag(root, 'FLAG_0x022', True)
    block = _block(root)
    assert block[-1] == 'end'
    assert 'setflag FLAG_0x022' in block


def test_the_new_line_goes_after_the_last_flag_not_after_the_value(tmp_path):
    """Vanilla's shape: the flags, then the starting values, then `end`."""
    root = _project(tmp_path)
    ngs.set_flag(root, 'FLAG_0x022', True)
    block = _block(root)
    assert block.index('setflag FLAG_0x022') \
        < block.index('setvar VAR_MASSAGE_COOLDOWN_STEP_COUNTER, 500')


def test_the_reader_sees_what_the_writer_wrote(tmp_path):
    root = _project(tmp_path)
    ngs.set_flag(root, 'FLAG_0x022', True)
    assert ngs.read(root).sets_flag('FLAG_0x022')


def test_adding_a_flag_that_is_already_there_changes_nothing(tmp_path):
    root = _project(tmp_path)
    before = _raw(root)
    changed, msg = ngs.set_flag(root, 'FLAG_HIDE_CERULEAN_RIVAL', True)
    assert not changed
    assert 'already' in msg
    assert _raw(root) == before


# ─────────────────────────────────────────────────────────────────────────────
# Switching it back off
# ─────────────────────────────────────────────────────────────────────────────

def test_removing_what_was_added_restores_the_file_byte_for_byte(tmp_path):
    """The strongest statement available: nothing else moved."""
    root = _project(tmp_path)
    before = _raw(root)
    ngs.set_flag(root, 'FLAG_0x022', True)
    assert _raw(root) != before
    ngs.set_flag(root, 'FLAG_0x022', False)
    assert _raw(root) == before


def test_removing_a_flag_that_is_not_there_changes_nothing(tmp_path):
    root = _project(tmp_path)
    before = _raw(root)
    changed, msg = ngs.set_flag(root, 'FLAG_NEVER_SET', False)
    assert not changed
    assert 'not switched on' in msg
    assert _raw(root) == before


def test_removing_a_flag_leaves_the_same_flag_in_another_script_alone(tmp_path):
    """Containment. `FLAG_SHARED_NAME` is set by two different scripts.

    A checkbox about the new-game script must reach only into that script —
    stripping the other one would change unrelated behaviour the user never
    asked about.
    """
    root = _project(tmp_path)
    changed, _ = ngs.set_flag(root, 'FLAG_SHARED_NAME', False)
    assert changed
    text = _raw(root).decode('utf-8')
    other = text.split('EventScript_ResetAllMapFlags::', 1)[0]
    assert 'setflag FLAG_SHARED_NAME' in other
    assert 'FLAG_SHARED_NAME' not in '\n'.join(_block(root))


def test_the_other_scripts_survive_a_write_untouched(tmp_path):
    root = _project(tmp_path)
    ngs.set_flag(root, 'FLAG_0x022', True)
    text = _raw(root).decode('utf-8')
    assert 'EventScript_SomethingElse::' in text
    assert 'EventScript_AfterwardsEntirely::' in text
    assert 'setflag FLAG_NOT_OURS' in text


# ─────────────────────────────────────────────────────────────────────────────
# The project's own formatting
# ─────────────────────────────────────────────────────────────────────────────

def test_crlf_endings_are_not_converted(tmp_path):
    root = _project(tmp_path, scripts_s=SCRIPTS_S.replace('\n', '\r\n'))
    ngs.set_flag(root, 'FLAG_0x022', True)
    raw = _raw(root)
    assert b'\r\n' in raw
    assert raw.count(b'\n') == raw.count(b'\r\n'), 'a bare LF was introduced'


def test_lf_endings_are_not_converted(tmp_path):
    root = _project(tmp_path)
    ngs.set_flag(root, 'FLAG_0x022', True)
    assert b'\r\n' not in _raw(root)


def test_space_indentation_is_matched(tmp_path):
    """A project that indents with spaces keeps them."""
    root = _project(tmp_path, scripts_s=SCRIPTS_S.replace('\t', '    '))
    ngs.set_flag(root, 'FLAG_0x022', True)
    text = _raw(root).decode('utf-8')
    assert '    setflag FLAG_0x022' in text
    assert '\tsetflag FLAG_0x022' not in text


# ─────────────────────────────────────────────────────────────────────────────
# Refusals
# ─────────────────────────────────────────────────────────────────────────────

def test_a_locked_block_is_refused_with_a_reason(tmp_path):
    s = SCRIPTS_S.replace(
        '\tsetflag FLAG_HIDE_CERULEAN_RIVAL',
        '\t.ifdef HYRULE\n\tsetflag FLAG_HIDE_CERULEAN_RIVAL\n\t.endif')
    root = _project(tmp_path, scripts_s=s)
    before = _raw(root)
    changed, msg = ngs.set_flag(root, 'FLAG_0x022', True)
    assert not changed
    assert 'conditional assembly' in msg
    assert _raw(root) == before


def test_a_project_with_no_script_is_refused_with_a_reason(tmp_path):
    (tmp_path / 'data').mkdir(parents=True)
    changed, msg = ngs.set_flag(tmp_path, 'FLAG_0x022', True)
    assert not changed
    assert 'no new-game script' in msg


# ─────────────────────────────────────────────────────────────────────────────
# Starting values
# ─────────────────────────────────────────────────────────────────────────────

def test_a_starting_value_can_be_changed(tmp_path):
    root = _project(tmp_path)
    changed, _ = ngs.set_var(root, 'VAR_MASSAGE_COOLDOWN_STEP_COUNTER', 999)
    assert changed
    assert ngs.read(root).vars['VAR_MASSAGE_COOLDOWN_STEP_COUNTER'] == '999'


def test_changing_a_value_does_not_add_a_line(tmp_path):
    root = _project(tmp_path)
    n_before = len(_raw(root).decode('utf-8').splitlines())
    ngs.set_var(root, 'VAR_MASSAGE_COOLDOWN_STEP_COUNTER', 999)
    assert len(_raw(root).decode('utf-8').splitlines()) == n_before


def test_a_new_starting_value_can_be_given(tmp_path):
    root = _project(tmp_path)
    changed, _ = ngs.set_var(root, 'VAR_0x402A', 3)
    assert changed
    assert ngs.read(root).vars['VAR_0x402A'] == '3'
    assert _block(root)[-1] == 'end'


def test_a_starting_value_can_be_cleared(tmp_path):
    root = _project(tmp_path)
    changed, _ = ngs.set_var(root, 'VAR_MASSAGE_COOLDOWN_STEP_COUNTER', None)
    assert changed
    assert ngs.read(root).vars == {}


def test_setting_a_value_it_already_has_changes_nothing(tmp_path):
    root = _project(tmp_path)
    before = _raw(root)
    changed, msg = ngs.set_var(root, 'VAR_MASSAGE_COOLDOWN_STEP_COUNTER', 500)
    assert not changed
    assert 'already starts at 500' in msg
    assert _raw(root) == before


def test_a_value_round_trips_byte_for_byte(tmp_path):
    root = _project(tmp_path)
    before = _raw(root)
    ngs.set_var(root, 'VAR_MASSAGE_COOLDOWN_STEP_COUNTER', 999)
    ngs.set_var(root, 'VAR_MASSAGE_COOLDOWN_STEP_COUNTER', 500)
    assert _raw(root) == before


# ─────────────────────────────────────────────────────────────────────────────
# The checkbox in the Variables & Flags screen
# ─────────────────────────────────────────────────────────────────────────────

import os  # noqa: E402

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
pytest.importorskip('PyQt6.QtWidgets')

from PyQt6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def manager(qapp, tmp_path, monkeypatch):
    import ui.label_manager as lm
    for name in ('information', 'warning', 'critical', 'question'):
        monkeypatch.setattr(lm.QMessageBox, name,
                            staticmethod(lambda *a, **k: None), raising=False)
    # A header so the constant list has something in it.
    inc = tmp_path / 'include' / 'constants'
    inc.mkdir(parents=True)
    (inc / 'flags.h').write_text(
        '#define FLAG_HIDE_OAK_IN_PALLET_TOWN 0x02C\n'
        '#define FLAG_HIDE_CERULEAN_RIVAL     0x030\n'
        '#define FLAG_SHARED_NAME             0x031\n'
        '#define FLAG_0x022                   0x022\n', encoding='utf-8')
    (inc / 'vars.h').write_text(
        '#define VAR_MASSAGE_COOLDOWN_STEP_COUNTER 0x4001\n'
        '#define VAR_0x402A                        0x402A\n', encoding='utf-8')
    _project(tmp_path)
    w = lm.LabelManagerWidget()
    w.load_project(str(tmp_path))
    return w


def test_the_box_is_ticked_for_a_flag_the_script_sets(manager):
    manager.select_constant('FLAG_HIDE_OAK_IN_PALLET_TOWN')
    assert manager._newgame_check.isChecked()


def test_the_box_is_clear_for_a_flag_it_does_not(manager):
    manager.select_constant('FLAG_0x022')
    assert not manager._newgame_check.isChecked()


def test_ticking_the_box_writes_the_line(manager, tmp_path):
    manager.select_constant('FLAG_0x022')
    manager._newgame_check.setChecked(True)
    assert ngs.read(tmp_path).sets_flag('FLAG_0x022')


def test_unticking_it_takes_the_line_back_out(manager, tmp_path):
    manager.select_constant('FLAG_0x022')
    manager._newgame_check.setChecked(True)
    manager._newgame_check.setChecked(False)
    assert not ngs.read(tmp_path).sets_flag('FLAG_0x022')


def test_the_status_line_says_what_changed(manager):
    manager.select_constant('FLAG_0x022')
    manager._newgame_check.setChecked(True)
    text = manager._status_label.text()
    assert 'FLAG_0x022' in text
    assert 'new game' in text


def test_merely_selecting_a_flag_writes_nothing(manager, tmp_path):
    """Browsing the list must not edit the game's source."""
    before = _raw(tmp_path)
    for name in ('FLAG_0x022', 'FLAG_HIDE_OAK_IN_PALLET_TOWN',
                 'FLAG_SHARED_NAME', 'VAR_0x402A'):
        manager.select_constant(name)
    assert _raw(tmp_path) == before


def test_the_variable_row_shows_its_starting_value(manager):
    manager.select_constant('VAR_MASSAGE_COOLDOWN_STEP_COUNTER')
    assert manager._newgame_value.text() == '500'


def test_the_tick_box_is_not_offered_for_a_variable(manager):
    manager.select_constant('VAR_0x402A')
    assert not manager._newgame_check.isVisibleTo(manager)


def test_the_value_box_is_not_offered_for_a_flag(manager):
    manager.select_constant('FLAG_0x022')
    assert not manager._newgame_value.isVisibleTo(manager)
