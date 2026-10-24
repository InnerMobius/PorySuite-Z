"""Guards for the EVENTide authoring features: paste script in, copy script out,
the object's own visibility flag, and the chest template.

These exist because the editor's practical usability was the point of the work,
and each one has a way of quietly going wrong:

  * Pasting is the one place raw text the user wrote by hand enters the command
    list. It has to cope with a leading `Label::`, trailing `@` comments and
    indentation, and must return [] rather than garbage when handed something
    that is not script, so Paste can fall back to the editor's own clipboard.

  * The object's `flag` field is map.json's own "don't spawn this event while
    the flag is set". It is NOT a flag a script reads. map.json spells "no flag"
    as the string "0", and writing "" there fails the build -- so the empty case
    is the one to pin.

  * A one-time pickup only stays picked up if two things agree: removeobject
    hides the sprite, and the SAME flag is named in the script and on the
    object. Setting just the script flag leaves the object reappearing on
    re-entry, which is exactly the bug a user would blame on the engine.
"""

import json
import pathlib
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, 'core'), os.path.join(_ROOT, 'ui')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from eventide.backend.eventide_utils import parse_script_snippet  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Pasting script text in
# ─────────────────────────────────────────────────────────────────────────────

def test_paste_parses_plain_command_lines():
    cmds = parse_script_snippet('lock\nfaceplayer\nend\n')
    assert cmds == [('lock',), ('faceplayer',), ('end',)]


def test_paste_ignores_indentation():
    assert parse_script_snippet('\tlock\n    faceplayer\n') == [
        ('lock',), ('faceplayer',)]


def test_paste_drops_the_snippets_own_leading_label():
    """Pasting INTO a page that already has a label — the heading is noise."""
    cmds = parse_script_snippet('Town_EventScript_Boy::\n\tlock\n\tend\n')
    assert cmds == [('lock',), ('end',)]


def test_paste_keeps_later_labels_as_sub_label_markers():
    """A multi-block paste still splits into sub-labels on save."""
    cmds = parse_script_snippet(
        'First::\n\tlock\n\tend\n\nSecond::\n\trelease\n\tend\n')
    assert ('_label_marker', 'Second') in cmds
    assert ('_label_marker', 'First') not in cmds


def test_paste_strips_comments():
    cmds = parse_script_snippet(
        '@ a whole-line comment\n'
        '\tlockall\n'
        '\tplayse SE_LIFT_UP   @ chest-open sound\n')
    assert cmds == [('lockall',), ('playse', 'SE_LIFT_UP')]


def test_paste_keeps_an_at_sign_inside_a_quoted_string():
    cmds = parse_script_snippet('\tmsgbox "mail me @ home"\n')
    assert 'home' in str(cmds[0])


def test_paste_of_nothing_returns_empty_so_the_caller_can_fall_back():
    for junk in ('', '   \n\n', '@ only a comment\n'):
        assert parse_script_snippet(junk) == []


def test_paste_resolves_message_text_from_the_map_store():
    cmds = parse_script_snippet('\tmsgbox Town_Text_Hi\n',
                                {'Town_Text_Hi': 'Hello!$'})
    assert cmds[0][0] == 'message' and 'Hello' in cmds[0][2]


def test_paste_never_raises_on_nonsense():
    cmds = parse_script_snippet('%%%% not a script $$$$\n<<<>>>\n')
    assert isinstance(cmds, list)


def test_paste_round_trips_a_script_full_of_engine_additions():
    """A project that has added its own specials, items and text to the engine
    must still be able to paste its scripts in.

    Every invented name below is deliberately made up. The editor has no idea
    what any of them mean, which is the point: it has to carry them through
    untouched rather than drop or rewrite the lines it does not recognise.
    """
    snippet = (
        'Town_EventScript_Container::\n'
        '\tlockall\n'
        '\tplayse SE_LIFT_UP                  @ opening sound\n'
        '\tremoveobject VAR_LAST_TALKED       @ reveals the tile beneath\n'
        '\tsetflag FLAG_UNUSED_0x4A7\n'
        '\tsetvar VAR_0x8004, CUSTOMITEM_THING_ONE\n'
        '\tspecial CustomCutscene_Start\n'
        '\twaitstate\n'
        '\tmsgbox gText_CustomMessage, MSGBOX_DEFAULT\n'
        '\tspecial CustomCutscene_End\n'
        '\treleaseall\n'
        '\tend\n')
    cmds = parse_script_snippet(snippet)
    heads = [c[0] for c in cmds]
    assert heads == ['lockall', 'playse', 'removeobject', 'setflag', 'setvar',
                     'special', 'waitstate', 'message', 'special',
                     'releaseall', 'end']
    assert ('special', 'CustomCutscene_Start') in cmds
    assert ('special', 'CustomCutscene_End') in cmds
    assert ('setvar', 'VAR_0x8004, CUSTOMITEM_THING_ONE') in cmds


# ─────────────────────────────────────────────────────────────────────────────
# The editor-side features need Qt
# ─────────────────────────────────────────────────────────────────────────────

pytest.importorskip('PyQt6.QtWidgets', reason='needs PyQt6')

from PyQt6.QtWidgets import QApplication  # noqa: E402

MAP_JSON = {
    "id": "MAP_TOWN", "name": "Town", "layout": "LAYOUT_TOWN",
    "music": "MUS_PALLET", "region_map_section": "MAPSEC_PALLET_TOWN",
    "requires_flash": False, "weather": "WEATHER_NONE",
    "map_type": "MAP_TYPE_TOWN", "allow_cycling": True,
    "allow_escaping": False, "allow_running": True, "show_map_name": True,
    "floor_number": 0, "battle_scene": "MAP_BATTLE_SCENE_NORMAL",
    "connections": [], "warp_events": [], "coord_events": [], "bg_events": [],
    "object_events": [{
        "graphics_id": "OBJ_EVENT_GFX_ITEM_BALL", "x": 5, "y": 5,
        "elevation": 3, "movement_type": "MOVEMENT_TYPE_NONE",
        "movement_range_x": 0, "movement_range_y": 0,
        "trainer_type": "TRAINER_TYPE_NONE",
        "trainer_sight_or_berry_tree_id": "0",
        "script": "Town_EventScript_Chest", "flag": "0",
        "local_id": "LOCALID_TOWN_CHEST"}],
}


class _StubMW:
    def __init__(self):
        self.modified = False
    def log_message(self, msg): pass
    def isWindowModified(self): return self.modified
    def setWindowModified(self, v): self.modified = v


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def tab(tmp_path, qapp, monkeypatch):
    import eventide.ui.event_editor_tab as mod
    for name in ('information', 'warning', 'critical', 'question'):
        monkeypatch.setattr(mod.QMessageBox, name,
                            staticmethod(lambda *a, **k: None), raising=False)
    d = tmp_path / 'data' / 'maps' / 'Town'
    d.mkdir(parents=True)
    (d / 'map.json').write_text(json.dumps(MAP_JSON), encoding='utf-8')
    (d / 'scripts.inc').write_text('Town_EventScript_Chest::\n\tend\n',
                                   encoding='utf-8')
    (d / 'text.inc').write_text('Town_Text_Hi::\n\t.string "Hi$"\n',
                                encoding='utf-8')
    t = mod.EventEditorTab(_StubMW())
    t._root_dir = tmp_path
    t._load_map(d)
    return t


# ── the object's own visibility flag ────────────────────────────────────────

def test_object_flag_is_shown_and_written_back(tab):
    assert tab.obj_flag_combo.selected_constant() == ''      # map.json had "0"
    tab.obj_flag_combo.set_constant('FLAG_UNUSED_0x4A7')
    tab._collect_current()
    assert tab._objects[0]['flag'] == 'FLAG_UNUSED_0x4A7'


def test_clearing_the_flag_writes_zero_not_empty(tab):
    """map.json spells "no flag" as "0"; an empty string fails the build."""
    tab.obj_flag_combo.set_constant('FLAG_UNUSED_0x4A7')
    tab._collect_current()
    tab.obj_flag_combo.setEditText('')
    tab._collect_current()
    assert tab._objects[0]['flag'] == '0'


def test_elevation_round_trips(tab):
    tab.elev_spin.setValue(7)
    tab._collect_current()
    assert tab._objects[0]['elevation'] == 7


def test_object_only_fields_are_disabled_for_a_non_object_event(tab):
    """Triggers and signs have no sprite to hide and no height layer."""
    tab._set_object_only_fields_enabled(False)
    assert not tab.obj_flag_combo.isEnabled()
    assert not tab.elev_spin.isEnabled()
    assert tab.obj_flag_combo.currentText() == ''   # cleared, not left stale


# ── templates: placeholders and flag syncing ────────────────────────────────

def test_template_placeholder_becomes_a_real_flag(tab, monkeypatch):
    """Each event from a one-time template must get its OWN flag.

    Hard-coding the flag the template was built with makes it good exactly
    once: every event sharing it would vanish together the first time any
    one of them fired.
    """
    import eventide.ui.event_editor_tab as mod
    monkeypatch.setattr(mod.EventEditorTab, '_find_unused_flags',
                        lambda self, n=1: ['FLAG_UNUSED_0x4A9'][:n])
    monkeypatch.setattr(mod.EventEditorTab, '_load_page_templates',
                        lambda self: {'Chest': [
                            ('lockall',),
                            ('setflag', mod.EventEditorTab.TEMPLATE_UNUSED_FLAG),
                            ('end',)]})
    obj = tab._objects[0]
    tab._apply_page_template(obj, 'Town_EventScript_Chest', 'Chest')
    cmds = obj['_pages'][0]['commands']
    assert ('setflag', 'FLAG_UNUSED_0x4A9') in cmds
    assert not any(mod.EventEditorTab.TEMPLATE_UNUSED_FLAG in str(c) for c in cmds)


def test_template_syncs_the_object_visibility_flag(tab, monkeypatch):
    """Setting only the SCRIPT flag leaves the chest reappearing on re-entry."""
    import eventide.ui.event_editor_tab as mod
    monkeypatch.setattr(mod.EventEditorTab, '_find_unused_flags',
                        lambda self, n=1: ['FLAG_UNUSED_0x4A9'][:n])
    monkeypatch.setattr(mod.EventEditorTab, '_load_page_templates',
                        lambda self: {'Chest': [
                            ('setflag', mod.EventEditorTab.TEMPLATE_UNUSED_FLAG),
                            ('end',)]})
    obj = tab._objects[0]
    tab._apply_page_template(obj, 'Town_EventScript_Chest', 'Chest')
    assert obj['flag'] == 'FLAG_UNUSED_0x4A9'


def test_template_does_not_overwrite_a_flag_the_event_already_has(tab, monkeypatch):
    import eventide.ui.event_editor_tab as mod
    monkeypatch.setattr(mod.EventEditorTab, '_find_unused_flags',
                        lambda self, n=1: ['FLAG_UNUSED_0x4A9'][:n])
    monkeypatch.setattr(mod.EventEditorTab, '_load_page_templates',
                        lambda self: {'Chest': [
                            ('setflag', mod.EventEditorTab.TEMPLATE_UNUSED_FLAG),
                            ('end',)]})
    obj = tab._objects[0]
    obj['flag'] = 'FLAG_SOMETHING_DELIBERATE'
    tab._apply_page_template(obj, 'Town_EventScript_Chest', 'Chest')
    assert obj['flag'] == 'FLAG_SOMETHING_DELIBERATE'


def test_template_refuses_rather_than_reusing_a_flag_when_none_are_free(tab, monkeypatch):
    import eventide.ui.event_editor_tab as mod
    monkeypatch.setattr(mod.EventEditorTab, '_find_unused_flags',
                        lambda self, n=1: [])
    monkeypatch.setattr(mod.EventEditorTab, '_load_page_templates',
                        lambda self: {'Chest': [
                            ('setflag', mod.EventEditorTab.TEMPLATE_UNUSED_FLAG),
                            ('end',)]})
    obj = tab._objects[0]
    before = json.dumps(obj.get('_pages'), default=str)
    tab._apply_page_template(obj, 'Town_EventScript_Chest', 'Chest')
    assert json.dumps(obj.get('_pages'), default=str) == before


#: Names that belong to one person's game, not to PorySuite. Anything built on
#: a project's own engine additions lives in that project's My Templates store
#: — a file inside the game folder — and never in this repo. Not even as an
#: example in a comment: this repo is public, and a worked example naming
#: somebody's own items reads as if the editor knows about their game.
PROJECT_SPECIFIC_TOKENS = ('REGITEM_', 'ChestItemGet_', 'ChestRegistry')


def test_no_project_specific_names_ship_in_eventide(tab):
    """Scans every shipped EVENTide module, not just the one that was caught
    first — the leak that prompted this widening was in `widgets.py`, which the
    old single-module version of this test never looked at."""
    root = pathlib.Path(__file__).resolve().parent.parent / 'eventide'
    scanned = 0
    for path in sorted(root.rglob('*.py')):
        if '__pycache__' in path.parts:
            continue
        scanned += 1
        src = path.read_text(encoding='utf-8')
        for token in PROJECT_SPECIFIC_TOKENS:
            assert token not in src, (
                f'{token} is one project\'s own name, found in '
                f'{path.relative_to(root.parent)} — shipped code must be generic')
    assert scanned > 5, f'only scanned {scanned} files; the glob is wrong'


def test_the_guard_above_would_actually_catch_something(tmp_path):
    """A scan that silently matches nothing is worse than no scan."""
    fake = tmp_path / 'leaky.py'
    fake.write_text('BAD = "REGITEM_SOMETHING"\n', encoding='utf-8')
    src = fake.read_text(encoding='utf-8')
    assert any(t in src for t in PROJECT_SPECIFIC_TOKENS)


# ── copy out ───────────────────────────────────────────────────────────────

def _build_a_page(tab, monkeypatch):
    """Put a few real commands on the page via the template path."""
    import eventide.ui.event_editor_tab as mod
    monkeypatch.setattr(mod.EventEditorTab, '_find_unused_flags',
                        lambda self, n=1: ['FLAG_UNUSED_0x4A9'][:n])
    monkeypatch.setattr(mod.EventEditorTab, '_load_page_templates',
                        lambda self: {'T': [
                            ('lockall',),
                            ('playse', 'SE_LIFT_UP'),
                            ('removeobject', 'VAR_LAST_TALKED'),
                            ('setflag', mod.EventEditorTab.TEMPLATE_UNUSED_FLAG),
                            ('releaseall',),
                            ('end',)]})
    tab._apply_page_template(tab._objects[0], 'Town_EventScript_Chest', 'T')
    return tab._objects[0]


def test_copy_as_script_renders_real_inc_text(tab, monkeypatch):
    obj = _build_a_page(tab, monkeypatch)
    raw = tab._render_event_raw(obj)
    assert raw and 'Town_EventScript_Chest::' in raw
    assert 'removeobject VAR_LAST_TALKED' in raw
    assert 'playse SE_LIFT_UP' in raw


def test_copy_out_and_paste_back_in_is_stable(tab, monkeypatch):
    """The two halves must agree: what Copy writes, Paste must read."""
    obj = _build_a_page(tab, monkeypatch)
    raw = tab._render_event_raw(obj)
    reparsed = parse_script_snippet(raw.split('@ ====')[0], tab._texts)
    original = obj['_pages'][0]['commands']
    assert [c[0] for c in reparsed] == [c[0] for c in original]


def test_render_returns_none_for_an_event_with_nothing(tab):
    tab._objects[0]['_pages'] = []
    assert tab._render_event_raw(tab._objects[0]) is None


# ── Set Flag: find a spare flag, and offer the follow-up page ───────────────

def test_set_flag_widget_offers_find_unused_when_the_hook_is_registered(tab):
    """A command widget lives inside a modal dialog with no pointer back to
    the tab, so it asks through a module-level hook. Building the tab
    registers it; without one the button hides rather than doing nothing."""
    import eventide.ui.event_editor_tab as mod
    w = mod._SetFlagWidget('')
    assert mod._FIND_UNUSED_FLAG_CB is not None
    assert w._btn_find.isVisibleTo(w)


def test_set_flag_find_unused_fills_the_picker(tab, monkeypatch):
    import eventide.ui.event_editor_tab as mod
    monkeypatch.setattr(mod, '_FIND_UNUSED_FLAG_CB',
                        lambda n=1: ['FLAG_UNUSED_0x4AB'][:n])
    w = mod._SetFlagWidget('')
    w._on_find_unused()
    assert w.to_tuple() == ('setflag', 'FLAG_UNUSED_0x4AB')


def test_setting_a_flag_offers_a_page_for_afterwards(tab, monkeypatch):
    """The two-page idiom is the thing people do not discover on their own."""
    import eventide.ui.event_editor_tab as mod
    monkeypatch.setattr(mod.QMessageBox, 'question',
                        staticmethod(lambda *a, **k: mod.QMessageBox.StandardButton.Yes))
    obj = tab._objects[0]
    before = len(obj['_pages'])
    tab._maybe_offer_condition_page(('setflag', 'FLAG_UNUSED_0x4A7'))
    assert len(obj['_pages']) == before + 1
    assert obj['_pages'][-1]['_conditions'] == [('goto_if_set', 'FLAG_UNUSED_0x4A7')]


def test_declining_the_offer_adds_nothing(tab, monkeypatch):
    import eventide.ui.event_editor_tab as mod
    monkeypatch.setattr(mod.QMessageBox, 'question',
                        staticmethod(lambda *a, **k: mod.QMessageBox.StandardButton.No))
    obj = tab._objects[0]
    before = len(obj['_pages'])
    tab._maybe_offer_condition_page(('setflag', 'FLAG_UNUSED_0x4A7'))
    assert len(obj['_pages']) == before


def test_it_does_not_nag_once_the_flag_already_drives_a_page(tab, monkeypatch):
    import eventide.ui.event_editor_tab as mod
    asked = []
    monkeypatch.setattr(mod.QMessageBox, 'question',
                        staticmethod(lambda *a, **k: (asked.append(1),
                                     mod.QMessageBox.StandardButton.Yes)[1]))
    tab._maybe_offer_condition_page(('setflag', 'FLAG_UNUSED_0x4A7'))
    asked.clear()
    tab._maybe_offer_condition_page(('setflag', 'FLAG_UNUSED_0x4A7'))
    assert asked == [], 'asked again for a flag that already has a page'


def test_a_non_setflag_edit_offers_nothing(tab, monkeypatch):
    import eventide.ui.event_editor_tab as mod
    asked = []
    monkeypatch.setattr(mod.QMessageBox, 'question',
                        staticmethod(lambda *a, **k: (asked.append(1),
                                     mod.QMessageBox.StandardButton.Yes)[1]))
    for cmd in (('clearflag', 'FLAG_X'), ('lock',), ('setflag',), None):
        tab._maybe_offer_condition_page(cmd)
    assert asked == []
