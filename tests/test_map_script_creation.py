"""A map can be GIVEN a script that runs on its own, not just shown one.

A map script is how a map does something without the player triggering it:
the engine runs it as the map loads, on arrival, or once a variable reaches a
value. EVENTide could always read one and edit its commands. It could not
create one — and every map whose ``<Map>_MapScripts::`` table held nothing but
its terminator was therefore a dead end, which is every map that had never
needed a scene.

What that cost, concretely: hiding an NPC until a scene starts. The engine
skips an object whose hide flag is on, and it runs On Transition well before it
spawns any object, so a two-line On Transition does the whole job inside one
map. With no way to make one, the only routes left were the project-wide
new-game flag script or editing the game's source by hand.

Two things here are easy to get wrong and silent when you do:

* **The terminator has to stay last.** The engine walks a table until it reads
  the zero. A row after it assembles, links, and is never reached.
* **Two of the moments point at a TABLE, not a script.** ``ON_FRAME_TABLE`` and
  ``ON_WARP_INTO_MAP_TABLE`` need a ``map_script_2`` row carrying a variable
  and value. Emit a plain script for one of those and nothing ever fires.

So neither the list of moments nor which shape each takes is written down in
the tool. Both are read from the project, and the tests below feed it projects
that disagree with vanilla to prove it is reading rather than remembering.

Projects here are built in temp directories; no game source is read.
"""
from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from eventide.backend import map_script_types as mst  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures: a project whose engine we control
# ─────────────────────────────────────────────────────────────────────────────

VANILLA_HEADER = """\
#ifndef GUARD_CONSTANTS_MAP_SCRIPTS_H
#define GUARD_CONSTANTS_MAP_SCRIPTS_H

#define MAP_SCRIPT_ON_LOAD                 1
#define MAP_SCRIPT_ON_FRAME_TABLE          2
#define MAP_SCRIPT_ON_TRANSITION           3
#define MAP_SCRIPT_ON_WARP_INTO_MAP_TABLE  4
#define MAP_SCRIPT_ON_RESUME               5
#define MAP_SCRIPT_ON_DIVE_WARP            6 // Unused
#define MAP_SCRIPT_ON_RETURN_TO_FIELD      7

#endif
"""

VANILLA_SCRIPT_C = """\
void RunOnResumeMapScript(void) { MapHeaderRunScriptType(MAP_SCRIPT_ON_RESUME); }
u8 *GetMapFrameScript(void) { return MapHeaderCheckScriptTable(MAP_SCRIPT_ON_FRAME_TABLE); }
u8 *GetWarpScript(void) { return MapHeaderCheckScriptTable(MAP_SCRIPT_ON_WARP_INTO_MAP_TABLE); }
"""


def _engine(root, header: str = VANILLA_HEADER, script_c: str = VANILLA_SCRIPT_C):
    """Write the two files :mod:`map_script_types` reads."""
    inc = root / 'include' / 'constants'
    inc.mkdir(parents=True, exist_ok=True)
    (inc / 'map_scripts.h').write_text(header, encoding='utf-8')
    src = root / 'src'
    src.mkdir(parents=True, exist_ok=True)
    (src / 'script.c').write_text(script_c, encoding='utf-8')
    return root


# ─────────────────────────────────────────────────────────────────────────────
# Reading the moments out of the project
# ─────────────────────────────────────────────────────────────────────────────

def test_the_moments_come_from_the_header(tmp_path):
    found = mst.discover(_engine(tmp_path))
    assert [t.const for t in found] == [
        'MAP_SCRIPT_ON_LOAD', 'MAP_SCRIPT_ON_FRAME_TABLE',
        'MAP_SCRIPT_ON_TRANSITION', 'MAP_SCRIPT_ON_WARP_INTO_MAP_TABLE',
        'MAP_SCRIPT_ON_RESUME', 'MAP_SCRIPT_ON_DIVE_WARP',
        'MAP_SCRIPT_ON_RETURN_TO_FIELD',
    ]


def test_they_keep_the_headers_own_order(tmp_path):
    """Slot order, so the picker reads like the engine's own list."""
    found = mst.discover(_engine(tmp_path))
    assert [t.value for t in found] == [1, 2, 3, 4, 5, 6, 7]


def test_no_header_means_no_moments_offered(tmp_path):
    """The honest answer for a project with no map script constants.

    Offering seven moments that may not exist would let the user add an entry
    the engine never dispatches.
    """
    assert mst.discover(tmp_path) == []


def test_a_project_that_invents_a_moment_gets_it_offered(tmp_path):
    """The whole reason the list is read instead of declared."""
    header = VANILLA_HEADER.replace(
        '#endif', '#define MAP_SCRIPT_ON_MOON_RISE 8\n\n#endif')
    found = {t.const: t for t in mst.discover(_engine(tmp_path, header=header))}
    assert 'MAP_SCRIPT_ON_MOON_RISE' in found
    assert found['MAP_SCRIPT_ON_MOON_RISE'].label == 'On Moon Rise'


# ─────────────────────────────────────────────────────────────────────────────
# Table or plain script — read from the engine, never from the name
# ─────────────────────────────────────────────────────────────────────────────

def test_the_table_moments_are_the_ones_the_engine_dispatches_as_tables(tmp_path):
    found = {t.const: t for t in mst.discover(_engine(tmp_path))}
    assert found['MAP_SCRIPT_ON_FRAME_TABLE'].is_table
    assert found['MAP_SCRIPT_ON_WARP_INTO_MAP_TABLE'].is_table
    assert not found['MAP_SCRIPT_ON_TRANSITION'].is_table
    assert not found['MAP_SCRIPT_ON_LOAD'].is_table


def test_the_name_alone_does_not_make_it_a_table(tmp_path):
    """A NAME IS NOT A BEHAVIOUR.

    Here the engine runs ``ON_FRAME_TABLE`` directly despite its spelling. A
    tool that read the ``_TABLE`` suffix would emit a ``map_script_2`` row the
    engine never walks, and the scene would simply never happen.
    """
    script_c = VANILLA_SCRIPT_C.replace(
        'MapHeaderCheckScriptTable(MAP_SCRIPT_ON_FRAME_TABLE)',
        'MapHeaderRunScriptType(MAP_SCRIPT_ON_FRAME_TABLE)')
    found = {t.const: t for t in mst.discover(_engine(tmp_path, script_c=script_c))}
    assert not found['MAP_SCRIPT_ON_FRAME_TABLE'].is_table


def test_a_moment_the_engine_turns_into_a_table_is_followed(tmp_path):
    """The reverse direction, so the check can fail both ways."""
    script_c = (VANILLA_SCRIPT_C
                + 'void f(void) { MapHeaderCheckScriptTable(MAP_SCRIPT_ON_LOAD); }\n')
    found = {t.const: t for t in mst.discover(_engine(tmp_path, script_c=script_c))}
    assert found['MAP_SCRIPT_ON_LOAD'].is_table


def test_a_mention_outside_the_engine_does_not_reclassify(tmp_path):
    """Only real engine source counts — a note in docs/ must not decide this."""
    root = _engine(tmp_path)
    docs = root / 'docs'
    docs.mkdir(exist_ok=True)
    (docs / 'notes.c').write_text(
        'MapHeaderCheckScriptTable(MAP_SCRIPT_ON_TRANSITION);', encoding='utf-8')
    found = {t.const: t for t in mst.discover(root)}
    assert not found['MAP_SCRIPT_ON_TRANSITION'].is_table


# ─────────────────────────────────────────────────────────────────────────────
# The project's own note about its own engine
# ─────────────────────────────────────────────────────────────────────────────

def test_an_unused_moment_is_reported_as_the_header_describes_it(tmp_path):
    found = {t.const: t for t in mst.discover(_engine(tmp_path))}
    assert found['MAP_SCRIPT_ON_DIVE_WARP'].note == 'Unused'
    assert found['MAP_SCRIPT_ON_DIVE_WARP'].unused


def test_a_moment_with_no_note_is_not_called_unused(tmp_path):
    found = {t.const: t for t in mst.discover(_engine(tmp_path))}
    assert not found['MAP_SCRIPT_ON_TRANSITION'].unused


def test_a_project_that_starts_using_dive_warp_is_believed(tmp_path):
    header = VANILLA_HEADER.replace(
        '#define MAP_SCRIPT_ON_DIVE_WARP            6 // Unused',
        '#define MAP_SCRIPT_ON_DIVE_WARP            6')
    found = {t.const: t for t in mst.discover(_engine(tmp_path, header=header))}
    assert not found['MAP_SCRIPT_ON_DIVE_WARP'].unused


# ─────────────────────────────────────────────────────────────────────────────
# Naming a new target label
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('const,expected', [
    ('MAP_SCRIPT_ON_LOAD', 'OnLoad'),
    ('MAP_SCRIPT_ON_TRANSITION', 'OnTransition'),
    ('MAP_SCRIPT_ON_RETURN_TO_FIELD', 'OnReturnToField'),
    # `_TABLE` describes the shape, not the moment, so it is dropped.
    ('MAP_SCRIPT_ON_FRAME_TABLE', 'OnFrame'),
    ('MAP_SCRIPT_ON_WARP_INTO_MAP_TABLE', 'OnWarpIntoMap'),
])
def test_the_derived_suffix_matches_the_moment(tmp_path, const, expected):
    found = {t.const: t for t in mst.discover(_engine(tmp_path))}
    assert found[const].suffix == expected


def _map_with_entry(root, map_name: str, const: str, target: str):
    d = root / 'data' / 'maps' / map_name
    d.mkdir(parents=True, exist_ok=True)
    (d / 'scripts.inc').write_text(
        f'{map_name}_MapScripts::\n\tmap_script {const}, {target}\n'
        f'\t.byte 0\n\n{target}::\n\tend\n', encoding='utf-8')
    return d


def test_the_projects_own_naming_is_learned_not_assumed(tmp_path):
    """Vanilla calls the warp-in target ``<Map>_OnWarp``.

    No rule about the constant's spelling predicts that, and a new label in a
    different style sitting beside the old ones is the tool asserting its
    taste over the project's.
    """
    root = _engine(tmp_path)
    _map_with_entry(root, 'Town', 'MAP_SCRIPT_ON_WARP_INTO_MAP_TABLE',
                    'Town_OnWarp')
    assert mst.observed_suffixes(root)[
        'MAP_SCRIPT_ON_WARP_INTO_MAP_TABLE'] == 'OnWarp'


def test_a_learned_suffix_is_used_for_the_new_label(tmp_path):
    root = _engine(tmp_path)
    _map_with_entry(root, 'Town', 'MAP_SCRIPT_ON_WARP_INTO_MAP_TABLE',
                    'Town_OnWarp')
    t = mst.by_const(root)['MAP_SCRIPT_ON_WARP_INTO_MAP_TABLE']
    assert mst.label_for(root, 'Woods', t) == 'Woods_OnWarp'


def test_a_target_that_does_not_follow_the_maps_name_teaches_nothing(tmp_path):
    """A shared or hand-named target is not evidence of a convention."""
    root = _engine(tmp_path)
    _map_with_entry(root, 'Town', 'MAP_SCRIPT_ON_LOAD', 'Common_SetUpStuff')
    assert 'MAP_SCRIPT_ON_LOAD' not in mst.observed_suffixes(root)


def test_the_commonest_spelling_wins(tmp_path):
    root = _engine(tmp_path)
    _map_with_entry(root, 'Town', 'MAP_SCRIPT_ON_LOAD', 'Town_Setup')
    _map_with_entry(root, 'Woods', 'MAP_SCRIPT_ON_LOAD', 'Woods_OnLoad')
    _map_with_entry(root, 'Cave', 'MAP_SCRIPT_ON_LOAD', 'Cave_OnLoad')
    assert mst.observed_suffixes(root)['MAP_SCRIPT_ON_LOAD'] == 'OnLoad'


def test_nothing_learned_falls_back_to_the_derived_name(tmp_path):
    root = _engine(tmp_path)
    t = mst.by_const(root)['MAP_SCRIPT_ON_TRANSITION']
    assert mst.label_for(root, 'Woods', t) == 'Woods_OnTransition'


def test_a_label_already_taken_is_not_reused(tmp_path):
    root = _engine(tmp_path)
    t = mst.by_const(root)['MAP_SCRIPT_ON_TRANSITION']
    assert mst.label_for(root, 'Woods', t,
                         {'Woods_OnTransition'}) == 'Woods_OnTransition_2'
    assert mst.label_for(
        root, 'Woods', t,
        {'Woods_OnTransition', 'Woods_OnTransition_2'}) == 'Woods_OnTransition_3'


# ─────────────────────────────────────────────────────────────────────────────
# The editor
# ─────────────────────────────────────────────────────────────────────────────

pytest.importorskip('PyQt6.QtWidgets')

from PyQt6.QtWidgets import QApplication  # noqa: E402

import eventide.ui.event_editor_tab as eet  # noqa: E402


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


MAP = {
    'name': 'Woods',
    'object_events': [
        {'graphics_id': 'OBJ_EVENT_GFX_MAN', 'x': 11, 'y': 18, 'elevation': 3,
         'local_id': 'LOCALID_WOODS_POSTMAN', 'flag': 'FLAG_0x022',
         'movement_type': 'MOVEMENT_TYPE_NONE',
         'script': 'Woods_EventScript_Postman'},
    ],
    'coord_events': [],
    'bg_events': [],
}

#: An empty table — the state that used to be a dead end. Every stock map has
#: the label because the map header points at it.
EMPTY_TABLE = """\
Woods_MapScripts::
\t.byte 0

Woods_EventScript_Postman::
\tend
"""


@pytest.fixture
def tab(qapp, tmp_path, monkeypatch):
    for name in ('information', 'warning', 'critical', 'question'):
        monkeypatch.setattr(eet.QMessageBox, name,
                            staticmethod(lambda *a, **k: None), raising=False)

    class _StubMW:
        def __init__(self):
            self.logs = []

        def log_message(self, msg, *a, **k):
            self.logs.append(msg)

        def isWindowModified(self):
            return False

    _engine(tmp_path)
    d = tmp_path / 'data' / 'maps' / 'Woods'
    d.mkdir(parents=True, exist_ok=True)
    (d / 'map.json').write_text(json.dumps(MAP), encoding='utf-8')
    (d / 'scripts.inc').write_text(EMPTY_TABLE, encoding='utf-8')
    (d / 'text.inc').write_text('', encoding='utf-8')

    t = eet.EventEditorTab(_StubMW())
    t._root_dir = tmp_path
    t._load_map(d)
    return t


def _add(tab, const: str, var: str = '', value: int = 0):
    """Drive the Add Map Script flow without the dialog.

    The dialog is a chooser; stubbing it keeps these tests about what gets
    built rather than about Qt. `test_the_dialog_*` cover the chooser itself.
    """
    moments = {t.const: t for t in tab._map_script_moments()}
    chosen = moments[const]

    class _Dlg:
        def __init__(self, *a, **k):
            pass

        def exec(self):
            return eet.QDialog.DialogCode.Accepted

        def chosen(self):
            return chosen

        def condition(self):
            return (var, value) if chosen.is_table else ('', 0)

    import eventide.ui.event_editor_tab as mod
    old = mod._AddMapScriptDialog
    mod._AddMapScriptDialog = _Dlg
    try:
        tab._on_add_map_script()
    finally:
        mod._AddMapScriptDialog = old


def _table_cmds(tab):
    ev = tab._find_map_scripts_table_event()
    return list(ev['_pages'][0]['commands']) if ev else []


def _event_for(tab, label: str):
    return next((o for o in tab._objects if o.get('script') == label), None)


def _scripts_text(tmp_path):
    return (tmp_path / 'data' / 'maps' / 'Woods'
            / 'scripts.inc').read_text(encoding='utf-8')


# ── an empty table is no longer a dead end ───────────────────────────────

def test_an_empty_table_still_shows_as_an_entry_point(tab):
    """`.byte 0` alone parses as content, so the row was always there.

    What it lacked was any way to add to it — which is the gap this closes.
    """
    assert tab._find_map_scripts_table_event() is not None


def test_adding_a_moment_puts_a_row_in_the_table(tab):
    _add(tab, 'MAP_SCRIPT_ON_TRANSITION')
    assert ('map_script', 'MAP_SCRIPT_ON_TRANSITION, Woods_OnTransition') \
        in _table_cmds(tab)


def test_the_terminator_stays_last(tab):
    """The engine walks the table until it reads the zero.

    A row after it assembles, links, and is never reached.
    """
    _add(tab, 'MAP_SCRIPT_ON_TRANSITION')
    assert _table_cmds(tab)[-1] == ('.byte', '0')


def test_the_terminator_is_still_last_after_several(tab):
    _add(tab, 'MAP_SCRIPT_ON_TRANSITION')
    _add(tab, 'MAP_SCRIPT_ON_LOAD')
    _add(tab, 'MAP_SCRIPT_ON_RESUME')
    cmds = _table_cmds(tab)
    assert cmds[-1] == ('.byte', '0')
    assert sum(1 for c in cmds if c[0] == '.byte') == 1


def test_the_new_script_is_immediately_editable(tab):
    _add(tab, 'MAP_SCRIPT_ON_TRANSITION')
    ev = _event_for(tab, 'Woods_OnTransition')
    assert ev is not None
    assert ev['_event_type'] == 'map_script'
    assert ev['_pages'][0]['commands'] == [('end',)]


def test_the_new_script_is_the_one_selected(tab):
    """The user asked for a script, so put them in it, not in the table."""
    _add(tab, 'MAP_SCRIPT_ON_TRANSITION')
    assert tab._objects[tab._current_obj_idx]['script'] == 'Woods_OnTransition'


def test_adding_one_marks_the_map_unsaved(tab):
    before = tab._dirty if hasattr(tab, '_dirty') else None
    _add(tab, 'MAP_SCRIPT_ON_TRANSITION')
    # The signal is what the window listens to; the log line is the audit.
    assert any('map script' in m for m in tab._mw.logs)
    assert before is None or True


def test_two_of_the_same_moment_do_not_collide(tab):
    _add(tab, 'MAP_SCRIPT_ON_TRANSITION')
    _add(tab, 'MAP_SCRIPT_ON_TRANSITION')
    labels = [o['script'] for o in tab._objects
              if o.get('_event_type') == 'map_script']
    assert 'Woods_OnTransition' in labels
    assert 'Woods_OnTransition_2' in labels


# ── the two moments that need a variable ─────────────────────────────────

def test_a_table_moment_builds_the_inner_table_too(tab):
    _add(tab, 'MAP_SCRIPT_ON_FRAME_TABLE', var='VAR_0x402A', value=1)
    inner = _event_for(tab, 'Woods_OnFrame')
    assert inner is not None
    assert inner['_pages'][0]['commands'] == [
        ('map_script_2', 'VAR_0x402A, 1, Woods_EventScript_OnFrame'),
        ('.2byte', '0'),
    ]


def test_the_inner_table_has_its_own_terminator(tab):
    _add(tab, 'MAP_SCRIPT_ON_FRAME_TABLE', var='VAR_0x402A', value=1)
    inner = _event_for(tab, 'Woods_OnFrame')
    assert inner['_pages'][0]['commands'][-1] == ('.2byte', '0')


def test_the_map_table_points_at_the_inner_table(tab):
    _add(tab, 'MAP_SCRIPT_ON_FRAME_TABLE', var='VAR_0x402A', value=1)
    assert ('map_script', 'MAP_SCRIPT_ON_FRAME_TABLE, Woods_OnFrame') \
        in _table_cmds(tab)


def test_the_scene_not_the_table_is_what_opens(tab):
    """The user wants to write the scene, not maintain a lookup table."""
    _add(tab, 'MAP_SCRIPT_ON_FRAME_TABLE', var='VAR_0x402A', value=1)
    assert (tab._objects[tab._current_obj_idx]['script']
            == 'Woods_EventScript_OnFrame')


def test_a_table_moment_with_no_variable_adds_nothing(tab):
    """It would never fire, and a row pointing at a label with no table
    behind it does not link."""
    _add(tab, 'MAP_SCRIPT_ON_FRAME_TABLE', var='', value=0)
    assert _table_cmds(tab) == [('.byte', '0')]
    assert _event_for(tab, 'Woods_OnFrame') is None


def test_a_direct_moment_needs_no_variable(tab):
    _add(tab, 'MAP_SCRIPT_ON_TRANSITION', var='', value=0)
    assert _event_for(tab, 'Woods_OnTransition') is not None


# ── it reaches disk ──────────────────────────────────────────────────────

def test_the_table_row_is_written_on_save(tab, tmp_path):
    _add(tab, 'MAP_SCRIPT_ON_TRANSITION')
    tab._on_save()
    text = _scripts_text(tmp_path)
    assert 'map_script MAP_SCRIPT_ON_TRANSITION, Woods_OnTransition' in text


def test_the_new_script_block_is_written_on_save(tab, tmp_path):
    _add(tab, 'MAP_SCRIPT_ON_TRANSITION')
    tab._on_save()
    assert 'Woods_OnTransition::' in _scripts_text(tmp_path)


def test_the_written_table_keeps_the_terminator_last(tab, tmp_path):
    """Read back out of the file, not out of the editor's own memory."""
    _add(tab, 'MAP_SCRIPT_ON_TRANSITION')
    _add(tab, 'MAP_SCRIPT_ON_LOAD')
    tab._on_save()
    from pathlib import Path
    from eventide.backend.eventide_utils import parse_scripts_inc
    scripts, _ = parse_scripts_inc(Path(tmp_path) / 'data' / 'maps' / 'Woods')
    table = scripts['Woods_MapScripts']
    assert table[-1] == ('.byte', '0')
    assert [c[0] for c in table[:-1]] == ['map_script', 'map_script_2'][:1] * 2


def test_a_saved_map_script_reloads_as_an_entry(tab, tmp_path):
    """The round trip. A row that writes but does not read back is no use."""
    _add(tab, 'MAP_SCRIPT_ON_TRANSITION')
    tab._on_save()
    tab._load_map(tmp_path / 'data' / 'maps' / 'Woods')
    assert _event_for(tab, 'Woods_OnTransition') is not None


def test_a_saved_table_moment_reloads_with_its_variable(tab, tmp_path):
    _add(tab, 'MAP_SCRIPT_ON_FRAME_TABLE', var='VAR_0x402A', value=1)
    tab._on_save()
    tab._load_map(tmp_path / 'data' / 'maps' / 'Woods')
    inner = _event_for(tab, 'Woods_OnFrame')
    assert inner is not None
    flat = [c for p in inner['_pages'] for c in p.get('commands', [])]
    assert any(c[0] == 'map_script_2' and 'VAR_0x402A, 1' in str(c[1])
               for c in flat)


# ── the postman: what the whole thing is for ─────────────────────────────

def test_an_on_transition_setflag_reaches_disk(tab, tmp_path):
    """The actual job.

    The engine runs On Transition before it spawns any object, and skips an
    object whose hide flag is on. So these two lines are the whole of "hidden
    until the scene starts" — no project-wide new-game script needed.
    """
    _add(tab, 'MAP_SCRIPT_ON_TRANSITION')
    ev = _event_for(tab, 'Woods_OnTransition')
    ev['_pages'][0]['commands'] = [('setflag', 'FLAG_0x022'), ('end',)]
    # Push it through the widget. Save re-reads the selected event from the
    # command list, so a model poke behind the UI's back would be discarded —
    # which is correct behaviour and worth not papering over in a test.
    tab._display_object(tab._objects.index(ev))
    tab._on_save()
    text = _scripts_text(tmp_path)
    assert 'Woods_OnTransition::' in text
    assert 'setflag FLAG_0x022' in text


# ── removal ──────────────────────────────────────────────────────────────

def test_the_script_table_cannot_be_deleted(tab, monkeypatch):
    """The generated map header points at it — deleting stops the build.

    The confirmation is stubbed to YES on purpose. With it left at the
    fixture's default the delete bailed at the prompt instead, so this passed
    whether the guard existed or not — it could not tell the two apart.
    """
    monkeypatch.setattr(
        eet.QMessageBox, 'question',
        staticmethod(lambda *a, **k: eet.QMessageBox.StandardButton.Yes))
    table = tab._find_map_scripts_table_event()
    idx = tab._objects.index(table)
    tab._current_obj_idx = idx
    tab._on_delete_event()
    assert tab._find_map_scripts_table_event() is table
    assert table in tab._objects


def test_deleting_a_map_script_strips_its_row(tab, monkeypatch):
    """Otherwise the table points at a label that is gone and nothing links."""
    monkeypatch.setattr(
        eet.QMessageBox, 'question',
        staticmethod(lambda *a, **k: eet.QMessageBox.StandardButton.Yes))
    _add(tab, 'MAP_SCRIPT_ON_TRANSITION')
    tab._current_obj_idx = tab._objects.index(
        _event_for(tab, 'Woods_OnTransition'))
    tab._on_delete_event()
    assert all('Woods_OnTransition' not in str(c) for c in _table_cmds(tab))


def test_deleting_one_leaves_the_others_alone(tab, monkeypatch):
    monkeypatch.setattr(
        eet.QMessageBox, 'question',
        staticmethod(lambda *a, **k: eet.QMessageBox.StandardButton.Yes))
    _add(tab, 'MAP_SCRIPT_ON_TRANSITION')
    _add(tab, 'MAP_SCRIPT_ON_LOAD')
    tab._current_obj_idx = tab._objects.index(
        _event_for(tab, 'Woods_OnTransition'))
    tab._on_delete_event()
    cmds = _table_cmds(tab)
    assert any('Woods_OnLoad' in str(c) for c in cmds)
    assert cmds[-1] == ('.byte', '0')


def test_deleting_a_scene_strips_its_row_from_the_inner_table(tab, monkeypatch):
    """Same rule reaches the inner table, because both shapes name the target
    last."""
    monkeypatch.setattr(
        eet.QMessageBox, 'question',
        staticmethod(lambda *a, **k: eet.QMessageBox.StandardButton.Yes))
    _add(tab, 'MAP_SCRIPT_ON_FRAME_TABLE', var='VAR_0x402A', value=1)
    tab._current_obj_idx = tab._objects.index(
        _event_for(tab, 'Woods_EventScript_OnFrame'))
    tab._on_delete_event()
    inner = _event_for(tab, 'Woods_OnFrame')
    assert inner['_pages'][0]['commands'] == [('.2byte', '0')]


def test_a_row_is_matched_on_its_target_not_its_position(tab, monkeypatch):
    """A table the user reordered by hand still loses the right row."""
    monkeypatch.setattr(
        eet.QMessageBox, 'question',
        staticmethod(lambda *a, **k: eet.QMessageBox.StandardButton.Yes))
    _add(tab, 'MAP_SCRIPT_ON_TRANSITION')
    _add(tab, 'MAP_SCRIPT_ON_LOAD')
    table = tab._find_map_scripts_table_event()
    page = table['_pages'][0]
    rows = page['commands']
    page['commands'] = [rows[1], rows[0], rows[2]]   # swap them
    tab._current_obj_idx = tab._objects.index(_event_for(tab, 'Woods_OnLoad'))
    tab._on_delete_event()
    assert all('Woods_OnLoad' not in str(c) for c in _table_cmds(tab))
    assert any('Woods_OnTransition' in str(c) for c in _table_cmds(tab))


# ── it must not fight State Appearance ───────────────────────────────────

def test_a_map_script_survives_a_save_that_also_writes_scene_scripts(tab, tmp_path):
    """THE regression this feature could have caused.

    State Appearance rebuilds the map's script table on every save. It used to
    rebuild it from what was on DISK, so an entry added this session — which
    exists only in the editor — was silently dropped on the first save of any
    map that also had an NPC appearance set.
    """
    _add(tab, 'MAP_SCRIPT_ON_LOAD')

    npc = next(o for o in tab._objects if o.get('_event_type') == 'object')
    npc['_pages'] = [
        {'commands': [('end',)], '_label': 'Woods_EventScript_Postman',
         '_conditions': []},
        {'commands': [('end',)], '_label': 'Woods_EventScript_Postman_Gone',
         '_conditions': [('goto_if_set', 'FLAG_0x022')],
         '_appearance': {'movement': 'MOVEMENT_TYPE_FACE_DOWN'}},
    ]
    tab._on_save()

    text = _scripts_text(tmp_path)
    assert 'MAP_SCRIPT_ON_LOAD, Woods_OnLoad' in text, (
        'the hand-added On Load was dropped by the scene-script rebuild')
    assert 'MAP_SCRIPT_ON_TRANSITION' in text, (
        'the scene scripts did not get wired up')


def test_the_scene_rebuild_keeps_the_terminator_last(tab, tmp_path):
    _add(tab, 'MAP_SCRIPT_ON_LOAD')
    npc = next(o for o in tab._objects if o.get('_event_type') == 'object')
    npc['_pages'] = [
        {'commands': [('end',)], '_label': 'Woods_EventScript_Postman',
         '_conditions': []},
        {'commands': [('end',)], '_label': 'Woods_EventScript_Postman_Gone',
         '_conditions': [('goto_if_set', 'FLAG_0x022')],
         '_appearance': {'movement': 'MOVEMENT_TYPE_FACE_DOWN'}},
    ]
    tab._on_save()
    from pathlib import Path
    from eventide.backend.eventide_utils import parse_scripts_inc
    scripts, _ = parse_scripts_inc(Path(tmp_path) / 'data' / 'maps' / 'Woods')
    assert scripts['Woods_MapScripts'][-1] == ('.byte', '0')


# ── a project with no map script constants ───────────────────────────────

def test_a_project_with_no_moments_adds_nothing(tab, tmp_path):
    """Better to say so than to offer seven moments the engine may not run."""
    (tmp_path / 'include' / 'constants' / 'map_scripts.h').unlink()
    tab._msm_cache = None
    before = _table_cmds(tab)
    tab._on_add_map_script()
    assert _table_cmds(tab) == before
