"""EVENTide command dialogs must be usable by someone who doesn't write code.

Four things are checked, each one a defect the dialogs actually had:

* every command the picker offers carries a plain-English description;
* every field in every command dialog has a tooltip explaining it;
* no field is width-capped so small you can't read what you picked;
* a value is picked from a list rather than typed as a raw constant.

And, just as important, the pickers must hand the SCRIPT the raw value and
not the pretty label the user read — getting that wrong writes
``setvar VAR_0x8004, Great Ball  (ITEM_GREAT_BALL)`` into a script file.
"""
from __future__ import annotations

import inspect
import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

pytest.importorskip('PyQt6.QtWidgets')

from PyQt6.QtWidgets import (QApplication, QComboBox, QLineEdit,  # noqa: E402
                             QPlainTextEdit, QSpinBox, QTextEdit)

import eventide.ui.event_editor_tab as eet  # noqa: E402

TYPED = (QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTextEdit)

# The fallback editor for commands that have no dialog of their own. Its one
# field really is free text — it takes the command's arguments exactly as the
# script file spells them — so it is allowed to be, as long as it says so.
FREE_BY_DESIGN = {'_GenericWidget.args_edit'}


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


#: Made-up constants, so these tests say nothing about any one project and
#: pass with no game source on disk. The names are deliberately not any
#: real game's.
FAKE_CONSTANTS = {
    'ITEMS': ['ITEM_NONE', 'ITEM_THING_ONE', 'ITEM_THING_TWO'],
    'SPECIES': ['SPECIES_NONE', 'SPECIES_CRITTER_ONE'],
    'MOVES': ['MOVE_NONE', 'MOVE_SOMETHING'],
    'FLAGS': ['FLAG_UNUSED_0x001', 'FLAG_UNUSED_0x002'],
    'VARS': ['VAR_TEMP_1', 'VAR_TEMP_2', 'VAR_0x8004', 'VAR_0x8005'],
    'MUSIC': ['MUS_NONE', 'MUS_SOMETHING'],
    'SFX': ['SE_NONE', 'SE_SOMETHING'],
    'MAP_CONSTANTS': ['MAP_TOWN', 'MAP_OTHER_TOWN'],
    'HEAL_LOCATIONS': ['HEAL_LOCATION_TOWN'],
    'TRAINERS': ['TRAINER_NONE', 'TRAINER_SOMEBODY'],
    'WEATHER': ['WEATHER_NONE', 'WEATHER_RAIN'],
    'MOVEMENT_TYPES': ['MOVEMENT_TYPE_NONE', 'MOVEMENT_TYPE_WANDER_AROUND'],
    'OBJECT_GFX': ['OBJ_EVENT_GFX_SOMEBODY'],
    'DECORATIONS': ['DECOR_NONE', 'DECOR_SOMETHING'],
    'SPECIALS': ['DoSomething', 'DoSomethingElse'],
}


@pytest.fixture(scope='module')
def seeded(qapp):
    """The module-level map context the widgets read, filled in the way a
    loaded map would fill it, so pickers are judged on what the user sees."""
    from eventide.backend.constants_manager import ConstantsManager
    const_before = {k: getattr(ConstantsManager, k) for k in FAKE_CONSTANTS}
    for key, values in FAKE_CONSTANTS.items():
        setattr(ConstantsManager, key, list(values))
    # The value picker caches whatever it scanned the first time; drop it so
    # it sees the list above instead of a previous test's.
    eet._VALUE_CHOICES_CACHE = None
    eet._METATILE_CHOICES_CACHE = ['METATILE_SomeTileset_Thing']

    before = (eet._SCRIPT_LABELS, eet._OBJECT_CHOICES, eet._OBJECT_LOCAL_IDS,
              eet._MOVEMENT_LABELS, eet._ALL_SCRIPTS)
    eet._SCRIPT_LABELS = ['Town_EventScript_Joe', 'Town_EventScript_Sign']
    eet._OBJECT_CHOICES = [
        ('0', 'This event (the one you are editing)'),
        ('LOCALID_PLAYER', 'The player'),
        ('LOCALID_TOWN_JOE', 'Town Joe — Man'),
    ]
    eet._OBJECT_LOCAL_IDS = [raw for raw, _ in eet._OBJECT_CHOICES]
    eet._MOVEMENT_LABELS = ['Common_Movement_WalkUp']
    eet._ALL_SCRIPTS = {'__texts__': {'Town_Text_Hello': 'Hi'}}
    yield
    (eet._SCRIPT_LABELS, eet._OBJECT_CHOICES, eet._OBJECT_LOCAL_IDS,
     eet._MOVEMENT_LABELS, eet._ALL_SCRIPTS) = before
    for key, values in const_before.items():
        setattr(ConstantsManager, key, values)
    eet._VALUE_CHOICES_CACHE = None
    eet._METATILE_CHOICES_CACHE = None


def command_widgets():
    """Every command-editor widget class, by name."""
    return sorted(
        (n, c) for n, c in vars(eet).items()
        if inspect.isclass(c) and issubclass(c, eet._CommandWidget)
        and c is not eet._CommandWidget)


def fields(widget):
    """The widget's own input fields, as (attribute name, field) pairs.

    Reads the instance attributes rather than walking the child tree,
    because an editable combo owns an internal QLineEdit that is not a
    field of its own.
    """
    return [(attr, obj) for attr, obj in vars(widget).items()
            if isinstance(obj, TYPED)]


def picker_commands():
    cmds = set()
    for page in (eet._PAGE_1_COMMANDS, eet._PAGE_2_COMMANDS,
                 eet._PAGE_3_COMMANDS):
        for _section, entries in page:
            for _friendly, raw in entries:
                cmds.add(raw)
    return cmds


# ── Group 1: every command says what it does ───────────────────────────────

def test_every_command_in_the_picker_has_a_description():
    missing = sorted(c for c in picker_commands() if not eet._command_help(c))
    assert not missing, f'no plain-English description for: {missing}'


def test_descriptions_are_one_plain_sentence():
    for cmd, text in eet._COMMAND_HELP.items():
        assert text, cmd
        assert text[0].isupper(), f'{cmd}: should start with a capital'
        assert text.endswith('.'), f'{cmd}: should end with a full stop'
        assert '(' not in text or ')' in text, cmd


def test_the_edit_dialog_shows_the_description(seeded):
    from PyQt6.QtWidgets import QLabel
    dlg = eet._CommandEditDialog(('setvar', 'VAR_TEMP_1', '3'))
    texts = [lbl.text() for lbl in dlg.findChildren(QLabel)]
    assert eet._command_help('setvar') in texts


def test_a_no_parameter_command_still_gets_its_description(seeded):
    from PyQt6.QtWidgets import QLabel
    dlg = eet._CommandEditDialog(('lock',))
    texts = [lbl.text() for lbl in dlg.findChildren(QLabel)]
    assert eet._command_help('lock') in texts


def test_the_command_picker_can_describe_what_it_is_pointing_at(seeded):
    dlg = eet._CommandSelectorDialog()
    assert dlg._btn_cmd, 'the picker should know what each button stands for'
    for btn, cmd in dlg._btn_cmd.items():
        assert btn.toolTip(), f'{cmd}: picker button with no hint'


# ── Group 2: nothing is squeezed into an unreadable box ────────────────────

def test_no_field_is_capped_too_small_to_read(seeded):
    squeezed = []
    for name, cls in command_widgets():
        w = cls()
        for attr, obj in fields(w):
            if isinstance(obj, QSpinBox):
                continue  # a spin box holds a short number by definition
            if 0 < obj.maximumWidth() < 120:
                squeezed.append(f'{name}.{attr} max={obj.maximumWidth()}')
    assert not squeezed, f'too narrow to read: {squeezed}'


def test_comparison_operators_read_as_words(seeded):
    for cls in (eet._GotoIfCompareWidget, eet._CallIfCompareWidget):
        labels = [pretty for _raw, pretty in cls._COMPARE_OPS]
        assert all(' ' in lbl for lbl in labels), f'{cls.__name__}: {labels}'
        assert not any(c in ''.join(labels) for c in '=<>')


def test_spelled_out_operators_still_pick_the_right_command(seeded):
    for raw, _pretty in eet._GotoIfCompareWidget._COMPARE_OPS:
        w = eet._GotoIfCompareWidget(cmd=raw, var='VAR_TEMP_1', value='3',
                                     label='Lbl')
        assert w.to_tuple() == (raw, 'VAR_TEMP_1', '3', 'Lbl')


# ── Group 3: every field is explained ─────────────────────────────────────

def test_every_field_has_a_tooltip(seeded):
    bare = []
    for name, cls in command_widgets():
        # Keep the parent alive: let it go and Qt deletes its children,
        # which turns every field lookup after it into a RuntimeError.
        w = cls()
        for attr, obj in fields(w):
            if not obj.toolTip():
                bare.append(f'{name}.{attr}')
    assert not bare, f'no explanation on: {bare}'


# ── Group 4: values are picked, not typed ─────────────────────────────────

def test_nothing_has_to_be_typed_by_hand(seeded):
    typed = []
    for name, cls in command_widgets():
        w = cls()
        for attr, obj in fields(w):
            fid = f'{name}.{attr}'
            if fid in FREE_BY_DESIGN:
                continue
            if isinstance(obj, QLineEdit):
                typed.append(fid)
            elif isinstance(obj, QComboBox) and obj.count() == 0:
                typed.append(fid + ' (nothing to pick)')
    assert not typed, f'has to be typed by hand: {typed}'


def test_the_one_free_field_explains_itself(seeded):
    w = eet._GenericWidget('somecommand', 'a, b')
    assert w.args_edit.toolTip()
    assert w.to_tuple() == ('somecommand', 'a, b')


# ── The pickers must write raw values, never the pretty label ────────────

def test_object_picker_writes_the_local_id_not_the_label(seeded):
    w = eet._HideObjectWidget('LOCALID_TOWN_JOE', 'MAP_TOWN')
    assert w.id_combo.currentText() == 'Town Joe — Man'
    assert w.to_tuple() == ('hideobjectat', 'LOCALID_TOWN_JOE, MAP_TOWN')


def test_object_picker_keeps_an_id_it_does_not_recognise(seeded):
    # An object that lives on another map, or a constant spelled another way,
    # must come back out exactly as it went in — the editor never quietly
    # rewrites somebody's script.
    w = eet._RemoveObjectWidget('OBJ_EVENT_ID_PLAYER')
    assert w.to_tuple() == ('removeobject', 'OBJ_EVENT_ID_PLAYER')


def test_wait_for_movement_zero_means_everyone(seeded):
    w = eet._WaitMovementWidget()
    assert 'Everyone' in w.target_combo.currentText()
    assert w.to_tuple() == ('waitmovement',)


def test_value_picker_writes_the_constant_not_the_label(seeded):
    w = eet._SetVarWidget('VAR_TEMP_1', 'TRUE')
    cmd, args = w.to_tuple()
    assert cmd == 'setvar'
    assert args == 'VAR_TEMP_1, TRUE', args


def test_value_picker_keeps_a_plain_number(seeded):
    assert eet._SetVarWidget('VAR_TEMP_1', '7').to_tuple() == (
        'setvar', 'VAR_TEMP_1, 7')


def test_value_picker_keeps_a_constant_it_has_never_seen(seeded):
    assert eet._SetVarWidget('VAR_TEMP_1', 'SOME_PROJECT_THING').to_tuple() == (
        'setvar', 'VAR_TEMP_1, SOME_PROJECT_THING')


def test_player_position_goes_into_variable_pickers(seeded):
    w = eet._GetPlayerXYWidget('VAR_TEMP_1', 'VAR_TEMP_2')
    assert not isinstance(w.var_x_edit, QLineEdit)
    assert w.to_tuple() == ('getplayerxy', 'VAR_TEMP_1, VAR_TEMP_2')


def test_buffer_number_can_take_a_variable(seeded):
    w = eet._BufferNumberWidget('1', 'VAR_0x8004')
    assert w.to_tuple() == ('buffernumberstring', '1, VAR_0x8004')


def test_buffer_string_offers_the_map_s_own_text(seeded):
    w = eet._BufferStringWidget('0', 'Town_Text_Hello')
    shown = [w.label_edit.itemText(i) for i in range(w.label_edit.count())]
    assert any('Town_Text_Hello' in s for s in shown), shown
    assert w.to_tuple() == ('bufferstring', '0, Town_Text_Hello')


def test_multichoice_keeps_a_raw_list_number(seeded):
    assert eet._MultiChoiceWidget(0, 0, '3', '0').to_tuple() == (
        'multichoice', '0, 0, 3, 0')


def test_set_metatile_keeps_a_named_tile(seeded):
    w = eet._SetMetatileWidget(3, 4, 'METATILE_SomeTileset_Thing', True)
    assert w.to_tuple() == (
        'setmetatile', '3, 4, METATILE_SomeTileset_Thing, 1')


# ── The Conditions box reads its operator by index ───────────────────────

def test_the_conditions_operator_order_matches_the_commands_it_maps_to():
    """The page Conditions box stores its operator by combo index, so the
    order of its entries and the order of the commands it maps to must stay
    in step. Reordering one list without the other silently writes the wrong
    comparison into every conditional page.
    """
    import re
    from pathlib import Path
    src = Path(eet.__file__).read_text(encoding='utf-8')

    items = re.search(r"_cond_var_op\.addItems\(\[(.*?)\]\)", src, re.S)
    assert items, 'could not find the Conditions box operator list'
    labels = re.findall(r"'([^']+)'", items.group(1))

    cmds = re.search(r"op_cmds = \[(.*?)\]", src, re.S)
    assert cmds, 'could not find op_cmds'
    commands = re.findall(r"'([^']+)'", cmds.group(1))

    mapping = re.search(r"op_map = \{'goto_if_eq': 0,(.*?)\}", src, re.S)
    assert mapping, 'could not find op_map'
    pairs = [('goto_if_eq', '0')] + re.findall(
        r"'([^']+)':\s*(\d+)", mapping.group(1))

    assert len(labels) == len(commands) == len(pairs) == 6
    for cmd, idx in pairs:
        assert commands[int(idx)] == cmd, (
            f'op_map puts {cmd} at index {idx}, but op_cmds has '
            f'{commands[int(idx)]} there')
