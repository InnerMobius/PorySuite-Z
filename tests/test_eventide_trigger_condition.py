"""A trigger's firing condition has to be editable, and has to warn.

A trigger fires when the player steps on its tile AND its variable holds its
value. That pair is the whole of a trigger's behaviour — whether it ever runs
again is decided there and nowhere else.

It had no editor. The variable was displayed read-only in the box used for an
NPC's name, wearing that box's tooltip, which says "A NAME for this event ...
NOT a number"; the value was not shown anywhere at all. So the only way to
change either was to edit the map file by hand.

The failure that leaves you with is a trigger on a TEMP variable. Those reset
every time the map loads, so the condition is always true, the trigger fires on
every single step onto its tile, and since any running script hides the HUD
(`LockPlayerFieldControls`), the HUD flashes each time.

Maps here are built in a temp directory; no game source is read.
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
        {'graphics_id': 'OBJ_EVENT_GFX_MAN', 'x': 11, 'y': 18,
         'script': 'Woods_EventScript_Postman'},
    ],
    'coord_events': [
        {'type': 'trigger', 'x': 19, 'y': 18, 'elevation': 0,
         'var': 'VAR_TEMP_0', 'var_value': '0',
         'script': 'Woods_EventScript_Intro'},
        {'type': 'trigger', 'x': 20, 'y': 18, 'elevation': 0,
         'var': 'VAR_0x402A', 'var_value': '3', 'script': 'NULL'},
    ],
}


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

    d = tmp_path / 'data' / 'maps' / 'Woods'
    d.mkdir(parents=True)
    (d / 'map.json').write_text(json.dumps(MAP), encoding='utf-8')
    (d / 'scripts.inc').write_text('', encoding='utf-8')
    (d / 'text.inc').write_text('', encoding='utf-8')

    t = eet.EventEditorTab(_StubMW())
    t._root_dir = tmp_path
    t._load_map(d)
    return t


def _indices(tab, etype):
    return [i for i, o in enumerate(tab._objects)
            if o.get('_event_type') == etype]


def _select_trigger(tab, n=0):
    idx = _indices(tab, 'coord')[n]
    tab._display_object(idx)
    return idx


# ── the condition is visible and editable ─────────────────────────────────

def test_the_condition_is_shown_for_a_trigger(tab):
    _select_trigger(tab)
    assert tab.trigger_row.isVisibleTo(tab)


def test_it_shows_the_variable_the_map_actually_stores(tab):
    _select_trigger(tab)
    assert tab.trigger_var_picker.selected_constant() == 'VAR_TEMP_0'


def test_it_shows_the_value_too(tab):
    """The value was not displayed anywhere before this existed."""
    _select_trigger(tab, 1)
    assert tab.trigger_val_combo.selected_value() == '3'


def test_choosing_a_variable_is_written_back(tab):
    _select_trigger(tab)
    tab.trigger_var_picker.set_constant('VAR_0x402A')
    tab.trigger_val_combo.set_value('1')
    tab._collect_current()
    obj = tab._objects[_indices(tab, 'coord')[0]]
    assert (obj['var'], obj['var_value']) == ('VAR_0x402A', '1')


def test_an_untouched_trigger_keeps_what_it_had(tab):
    idx = _select_trigger(tab, 1)
    tab._collect_current()
    assert (tab._objects[idx]['var'],
            tab._objects[idx]['var_value']) == ('VAR_0x402A', '3')


def test_a_blank_variable_is_not_written(tab):
    """An empty var fails the build, so leave whatever was there alone."""
    idx = _select_trigger(tab)
    tab.trigger_var_picker.set_constant('')
    tab._collect_current()
    assert tab._objects[idx]['var'] == 'VAR_TEMP_0'


# ── the warning that explains the repeating trigger ───────────────────────

def test_a_temp_variable_is_warned_about(tab):
    _select_trigger(tab)
    text = tab.trigger_warn_lbl.text().lower()
    assert 'reset' in text and 'every visit' in text


def test_a_lasting_variable_does_not_get_the_temp_warning(tab):
    _select_trigger(tab, 1)
    assert 'is a temp variable' not in tab.trigger_warn_lbl.text()


def test_the_temp_warning_goes_away_when_the_variable_is_changed(tab):
    """Changing away from a temp variable must retire that warning.

    It may be replaced by one of the condition warnings below — this fixture's
    trigger has no script setting anything — so the test is that the TEMP
    message is gone, not that the label is empty.
    """
    _select_trigger(tab)
    assert 'is a temp variable' in tab.trigger_warn_lbl.text()
    tab.trigger_var_picker.set_constant('VAR_0x402A')
    tab._update_trigger_warning()
    assert 'is a temp variable' not in tab.trigger_warn_lbl.text()


# ── the three ways a condition goes wrong ─────────────────────────────────
#
# The engine test is `VarGet(trigger) == (u8)index` (field_control_avatar.c).
# Variables start at 0, so a trigger must fire on the value the variable
# ALREADY has and be switched off by its script setting something else. Get it
# backwards and the trigger silently never fires; leave it unset and it fires
# forever. Neither says anything in game.

def _set_script(tab, idx, commands):
    """Give the trigger a script.

    Both the stored page AND the live command list, because that is how they
    relate in the app: `_cmd_tuples` is what the user is looking at and what
    gets saved, so the checks read it and see unsaved edits.
    """
    tab._objects[idx]['_pages'] = [{'commands': list(commands)}]
    tab._cmd_tuples = list(commands)


def test_a_script_setting_the_fire_value_is_caught(tab):
    """Fires on 1 and sets itself to 1: re-arms instead of switching off."""
    idx = _select_trigger(tab)
    _set_script(tab, idx, [('setvar', 'VAR_0x402A, 1')])
    tab.trigger_var_picker.set_constant('VAR_0x402A')
    tab.trigger_val_combo.set_value('1')
    tab._update_trigger_warning()
    text = tab.trigger_warn_lbl.text()
    assert 'never switches itself off' in text
    assert 'can never fire at all' in text      # because 1 is not the start


def test_firing_on_the_starting_value_is_fine(tab):
    """Fires on 0, script sets 1 — the correct shape, no warning."""
    idx = _select_trigger(tab)
    _set_script(tab, idx, [('setvar', 'VAR_0x402A, 1')])
    tab.trigger_var_picker.set_constant('VAR_0x402A')
    tab.trigger_val_combo.set_value('0')
    tab._update_trigger_warning()
    assert tab.trigger_warn_lbl.text() == ''


def test_a_script_that_never_sets_the_variable_is_warned(tab):
    idx = _select_trigger(tab)
    _set_script(tab, idx, [('msgbox', 'Text_1, MSGBOX_DEFAULT')])
    tab.trigger_var_picker.set_constant('VAR_0x402A')
    tab.trigger_val_combo.set_value('0')
    tab._update_trigger_warning()
    assert 'Fires every time' in tab.trigger_warn_lbl.text()


def test_an_unreachable_value_with_no_setter_is_warned(tab):
    idx = _select_trigger(tab)
    _set_script(tab, idx, [('msgbox', 'Text_1, MSGBOX_DEFAULT')])
    tab.trigger_var_picker.set_constant('VAR_0x402A')
    tab.trigger_val_combo.set_value('4')
    tab._update_trigger_warning()
    assert 'will not fire' in tab.trigger_warn_lbl.text()


def test_the_temp_warning_wins_over_the_others(tab):
    """A temp variable is the more fundamental problem, so say that first."""
    idx = _select_trigger(tab)
    _set_script(tab, idx, [('setvar', 'VAR_TEMP_0, 0')])
    tab.trigger_var_picker.set_constant('VAR_TEMP_0')
    tab.trigger_val_combo.set_value('0')
    tab._update_trigger_warning()
    assert 'is a temp variable' in tab.trigger_warn_lbl.text()


def test_another_variables_setvar_is_not_mistaken_for_this_one(tab):
    idx = _select_trigger(tab)
    _set_script(tab, idx, [('setvar', 'VAR_0x402B, 1')])
    tab.trigger_var_picker.set_constant('VAR_0x402A')
    tab.trigger_val_combo.set_value('0')
    tab._update_trigger_warning()
    assert 'Fires every time' in tab.trigger_warn_lbl.text()


# ── "Run once" ────────────────────────────────────────────────────────────
#
# Playing a scene once is the commonest thing anyone wants from a trigger, and
# by hand it needs four facts that appear nowhere on screen: use a lasting
# variable, variables start at 0, the condition must therefore be 0 and NOT the
# value you end up setting, and the script must set something else at the end.
# The tick box writes all four, and READS all four back, so a scene arranged by
# hand shows as ticked and the box can never claim something the script does
# not do.

def _arrange(tab, idx, var, value, commands):
    _set_script(tab, idx, commands)
    tab._cmd_tuples = list(commands)
    tab.trigger_var_picker.set_constant(var)
    tab.trigger_val_combo.set_value(value)
    tab._update_trigger_warning()


def test_a_hand_built_run_once_shows_as_ticked(tab):
    idx = _select_trigger(tab)
    _arrange(tab, idx, 'VAR_0x402A', '0', [('setvar', 'VAR_0x402A, 1')])
    assert tab.run_once_check.isChecked()


def test_firing_on_the_value_the_script_sets_is_not_run_once(tab):
    """The backwards arrangement must not be reported as working."""
    idx = _select_trigger(tab)
    _arrange(tab, idx, 'VAR_0x402A', '1', [('setvar', 'VAR_0x402A, 1')])
    assert not tab.run_once_check.isChecked()


def test_a_temp_variable_is_never_run_once(tab):
    idx = _select_trigger(tab)
    _arrange(tab, idx, 'VAR_TEMP_0', '0', [('setvar', 'VAR_TEMP_0, 1')])
    assert not tab.run_once_check.isChecked()


def test_no_off_switch_is_not_run_once(tab):
    idx = _select_trigger(tab)
    _arrange(tab, idx, 'VAR_0x402A', '0', [('msgbox', 'Text_1')])
    assert not tab.run_once_check.isChecked()


def test_setting_it_back_to_zero_is_not_an_off_switch(tab):
    """`setvar VAR, 0` re-arms the trigger rather than switching it off."""
    idx = _select_trigger(tab)
    _arrange(tab, idx, 'VAR_0x402A', '0', [('setvar', 'VAR_0x402A, 0')])
    assert not tab.run_once_check.isChecked()


def test_ticking_it_corrects_a_backwards_condition(tab):
    """The exact bug it exists to prevent: fires on 1, script sets 1."""
    idx = _select_trigger(tab)
    _arrange(tab, idx, 'VAR_0x402A', '1', [('setvar', 'VAR_0x402A, 1')])
    tab._loading = False
    tab.run_once_check.setChecked(True)
    assert tab.trigger_val_combo.selected_value() == '0'
    assert tab.run_once_check.isChecked()


def test_ticking_it_adds_the_off_switch(tab):
    idx = _select_trigger(tab)
    _arrange(tab, idx, 'VAR_0x402A', '0', [('msgbox', 'Text_1')])
    tab._loading = False
    tab.run_once_check.setChecked(True)
    assert ('setvar', 'VAR_0x402A, 1') in tab._cmd_tuples


def test_ticking_it_does_not_add_a_second_off_switch(tab):
    idx = _select_trigger(tab)
    _arrange(tab, idx, 'VAR_0x402A', '0', [('setvar', 'VAR_0x402A, 1')])
    tab._loading = False
    tab.run_once_check.setChecked(True)
    setvars = [c for c in tab._cmd_tuples if c and c[0] == 'setvar']
    assert len(setvars) == 1


def test_the_off_switch_goes_before_a_trailing_release(tab):
    """releaseall has to stay last, or the player is left frozen."""
    idx = _select_trigger(tab)
    _arrange(tab, idx, 'VAR_0x402A', '0',
             [('msgbox', 'Text_1'), ('releaseall',)])
    tab._loading = False
    tab.run_once_check.setChecked(True)
    assert tab._cmd_tuples[-1] == ('releaseall',)
    assert ('setvar', 'VAR_0x402A, 1') in tab._cmd_tuples


def test_unticking_removes_the_off_switch(tab):
    idx = _select_trigger(tab)
    _arrange(tab, idx, 'VAR_0x402A', '0', [('setvar', 'VAR_0x402A, 1')])
    tab._loading = False
    tab.run_once_check.setChecked(False)
    assert not [c for c in tab._cmd_tuples if c and c[0] == 'setvar']


def test_unticking_leaves_other_setvars_alone(tab):
    idx = _select_trigger(tab)
    _arrange(tab, idx, 'VAR_0x402A', '0',
             [('setvar', 'VAR_0x4050, 7'), ('setvar', 'VAR_0x402A, 1')])
    tab._loading = False
    tab.run_once_check.setChecked(False)
    assert ('setvar', 'VAR_0x4050, 7') in tab._cmd_tuples


# ── it must not bleed into other event types ──────────────────────────────

def test_an_npc_does_not_show_a_trigger_condition(tab):
    tab._display_object(_indices(tab, 'object')[0])
    assert not tab.trigger_row.isVisibleTo(tab)


def test_an_npcs_name_box_is_still_its_own(tab):
    """The name box must go back to being editable for an NPC."""
    tab._display_object(_indices(tab, 'object')[0])
    assert tab.obj_id_edit.isEnabled()


def test_a_triggers_variable_no_longer_squats_in_the_name_box(tab):
    """It used to appear there, read-only, under the NPC-name tooltip."""
    _select_trigger(tab)
    assert tab.obj_id_edit.text() == ''
