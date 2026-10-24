"""Picking things in EVENTide: maps, sprites, and the command palette itself.

Three complaints, each one a check here:

* choosing a map meant leaving the Event Editor for the Maps tab, typing a
  name there, and being bounced straight back;
* the command palette threw away the section headings its own data carried
  and rendered 125 commands as one undifferentiated grid;
* choosing a sprite meant knowing what ``OBJ_EVENT_GFX_CUTTABLE_TREE`` looks
  like before you clicked it.

Everything here runs against invented names and a map tree built in a temp
directory, so no game source has to be on disk.
"""
from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

pytest.importorskip('PyQt6.QtWidgets')

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

import eventide.ui.event_editor_tab as eet  # noqa: E402


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


# ═══════════════════════════════════════════════════════════════════════════
# The command palette
# ═══════════════════════════════════════════════════════════════════════════

def all_sections():
    for page in (eet._PAGE_1_COMMANDS, eet._PAGE_2_COMMANDS,
                 eet._PAGE_3_COMMANDS):
        for name, cmds in page:
            yield name, cmds


def test_every_section_gets_a_heading_on_screen(qapp):
    """The sections were in the data and never reached the screen.

    This is the regression that matters: the dialog used to flatten every
    page into one 2-column grid and drop the section names entirely, so the
    organisation existed only in the source.
    """
    dlg = eet._CommandSelectorDialog()
    drawn = [hdr.text() for hdr, _ in dlg._sections]
    expected = [name for name, _ in all_sections()]
    assert drawn == expected


def test_a_section_with_no_matches_hides_its_heading(qapp):
    """A heading with nothing under it is worse than no heading."""
    dlg = eet._CommandSelectorDialog()
    dlg._on_search('zzzznotacommand')
    assert not any(not hdr.isHidden() for hdr, _ in dlg._sections)
    dlg._on_search('')
    assert all(not hdr.isHidden() for hdr, _ in dlg._sections)


def test_search_keeps_the_heading_of_whatever_matched(qapp):
    dlg = eet._CommandSelectorDialog()
    dlg._on_search('flag')
    shown = {hdr.text() for hdr, _ in dlg._sections if not hdr.isHidden()}
    assert shown, 'searching a real word hid everything'
    for hdr, btns in dlg._sections:
        if hdr.isHidden():
            assert all(b.isHidden() for b in btns)


def test_the_commands_people_reach_for_are_on_the_first_page_first(qapp):
    """The page used to open with lock/release, which EVENTide injects for
    you — the first thing on screen was the thing nobody needs to click."""
    first_section, first_cmds = eet._PAGE_1_COMMANDS[0]
    raws = [raw for _, raw in first_cmds]
    assert 'message' in raws
    assert 'giveitem' in raws
    assert 'setflag' in raws
    # lock/release still exist, just not in front.
    last_section, last_cmds = eet._PAGE_1_COMMANDS[-1]
    assert {'lock', 'lockall', 'release', 'releaseall'} <= {
        raw for _, raw in last_cmds}


def test_a_command_listed_twice_is_still_the_same_command(qapp):
    """The shortcut section repeats commands that also live under their own
    heading. Two buttons for one command is fine; two commands wearing one
    name is not."""
    labels = {}
    for _section, cmds in all_sections():
        for friendly, raw in cmds:
            if raw in labels:
                assert labels[raw] == friendly, (
                    f'{raw} is called both {labels[raw]!r} and {friendly!r}')
            labels[raw] = friendly


# ═══════════════════════════════════════════════════════════════════════════
# Give Item vs Add Item
# ═══════════════════════════════════════════════════════════════════════════

def test_give_item_and_add_item_are_not_the_same_command(qapp):
    """The palette called `additem` "Give Item". It is not: `additem` is
    silent, `giveitem` plays the jingle and prints "You got the X!"."""
    give = eet._widget_for_tuple(('giveitem', 'ITEM_THING_ONE'))
    add = eet._widget_for_tuple(('additem', 'ITEM_THING_ONE'))
    assert give.to_tuple()[0] == 'giveitem'
    assert add.to_tuple()[0] == 'additem'
    assert give.friendly_name() != add.friendly_name()


@pytest.mark.parametrize('cmd', ['giveitem', 'additem'])
def test_item_commands_round_trip_their_quantity(qapp, cmd):
    w = eet._widget_for_tuple((cmd, 'ITEM_THING_ONE, 5'))
    assert w.to_tuple() == (cmd, 'ITEM_THING_ONE, 5')


def test_both_item_commands_explain_themselves(qapp):
    """Two commands that look identical in the list need the difference
    spelled out, or picking between them is a coin toss."""
    assert 'You got' in eet._command_help('giveitem')
    assert 'no message' in eet._command_help('additem').lower() or \
           'no sound' in eet._command_help('additem').lower()


# ═══════════════════════════════════════════════════════════════════════════
# The map box on the toolbar
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def fake_project(tmp_path):
    """A map tree with four real maps and one folder that only looks like one."""
    maps = tmp_path / 'data' / 'maps'
    for name in ('FirstTown', 'SecondTown', 'ACave', 'Orphan'):
        (maps / name).mkdir(parents=True)
        (maps / name / 'map.json').write_text('{}', encoding='utf-8')
    # A folder with no map.json is not a map and must not be offered.
    (maps / 'NotAMap').mkdir(parents=True)
    (maps / 'map_groups.json').write_text(json.dumps({
        'group_order': ['gMapGroup_TownsAndRoutes'],
        'gMapGroup_TownsAndRoutes': ['FirstTown', 'SecondTown'],
    }), encoding='utf-8')
    return tmp_path


@pytest.fixture
def tab(qapp, fake_project, monkeypatch):
    """An Event Editor pointed at the fake project, with no map open."""
    for name in ('information', 'warning', 'critical', 'question'):
        monkeypatch.setattr(eet.QMessageBox, name,
                            staticmethod(lambda *a, **k: None), raising=False)

    class _StubMW:
        def log_message(self, *a, **k):
            pass

        def isWindowModified(self):
            return False

    t = eet.EventEditorTab(_StubMW())
    t._root_dir = fake_project
    t._populate_map_combo()
    return t


def test_the_box_lists_every_map_and_nothing_else(tab):
    listed = {tab.map_combo.itemText(i) for i in range(tab.map_combo.count())}
    assert listed == {'FirstTown', 'SecondTown', 'ACave', 'Orphan'}


def test_a_map_no_group_claims_is_still_listed(tab):
    """The box is the only way to open a map, so a map missing from
    map_groups.json must not fall out of it."""
    listed = {tab.map_combo.itemText(i) for i in range(tab.map_combo.count())}
    assert 'Orphan' in listed


def test_the_box_is_typeable_and_filters_on_any_part_of_a_name(tab):
    assert tab.map_combo.isEditable()
    comp = tab.map_combo.completer()
    assert comp is not None
    # Starts-with would miss "Town" inside "FirstTown", which is exactly how
    # people remember map names.
    assert comp.filterMode() == Qt.MatchFlag.MatchContains
    assert comp.caseSensitivity() == Qt.CaseSensitivity.CaseInsensitive


def test_the_wheel_does_not_change_the_map(tab):
    """Scrolling the page over this box would otherwise load another map and
    throw away where you were."""
    from PyQt6.QtGui import QWheelEvent
    from PyQt6.QtCore import QPoint, QPointF
    before = tab.map_combo.currentIndex()
    ev = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, 120),
                     Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                     Qt.ScrollPhase.NoScrollPhase, False)
    tab.map_combo.wheelEvent(ev)
    assert tab.map_combo.currentIndex() == before


def test_typing_half_a_name_and_pressing_enter_opens_that_map(tab, monkeypatch):
    opened = []
    monkeypatch.setattr(type(tab), '_load_map',
                        lambda self, d: opened.append(d.name))
    tab.map_combo.setEditText('cave')
    tab._on_map_combo_entered()
    assert opened == ['ACave']


def test_picking_from_the_list_opens_that_map(tab, monkeypatch):
    opened = []
    monkeypatch.setattr(type(tab), '_load_map',
                        lambda self, d: opened.append(d.name))
    idx = tab.map_combo.findText('SecondTown')
    tab._on_map_combo_chosen(idx)
    assert opened == ['SecondTown']


def test_a_name_that_matches_nothing_opens_nothing(tab, monkeypatch):
    opened = []
    monkeypatch.setattr(type(tab), '_load_map',
                        lambda self, d: opened.append(d.name))
    tab.map_combo.setEditText('zzzznotamap')
    tab._on_map_combo_entered()
    assert opened == []


def test_a_cancelled_switch_leaves_the_box_showing_what_is_open(tab,
                                                                monkeypatch):
    """A box naming a map you are not editing is worse than no box."""
    tab._map_dir = fake_dir = tab._root_dir / 'data' / 'maps' / 'FirstTown'
    tab._sync_map_combo()
    assert tab.map_combo.currentText() == 'FirstTown'

    monkeypatch.setattr(type(tab), '_check_unsaved_before_map_switch',
                        lambda self: False)
    opened = []
    monkeypatch.setattr(type(tab), '_load_map',
                        lambda self, d: opened.append(d.name))
    tab.map_combo.setEditText('ACave')
    tab._on_map_combo_entered()
    assert opened == [], 'cancelling must not load the map'
    assert tab.map_combo.currentText() == 'FirstTown', 'box went stale'
    assert fake_dir  # silence the unused-name warning


def test_reopening_the_map_already_open_is_a_no_op(tab, monkeypatch):
    """Re-selecting the current map used to reload it, quietly discarding
    whatever was unsaved."""
    tab._map_dir = tab._root_dir / 'data' / 'maps' / 'FirstTown'
    tab._sync_map_combo()
    opened = []
    monkeypatch.setattr(type(tab), '_load_map',
                        lambda self, d: opened.append(d.name))
    tab._open_map_named('FirstTown')
    assert opened == []


def test_the_box_is_disabled_when_the_project_has_no_maps(qapp, tmp_path,
                                                          monkeypatch):
    for name in ('information', 'warning', 'critical', 'question'):
        monkeypatch.setattr(eet.QMessageBox, name,
                            staticmethod(lambda *a, **k: None), raising=False)

    class _StubMW:
        def log_message(self, *a, **k):
            pass

        def isWindowModified(self):
            return False

    t = eet.EventEditorTab(_StubMW())
    t._root_dir = tmp_path
    t._populate_map_combo()
    assert t.map_combo.count() == 0
    assert not t.map_combo.isEnabled()


# ═══════════════════════════════════════════════════════════════════════════
# The sprite gallery
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def fake_sprites():
    from eventide.backend.constants_manager import ConstantsManager
    gfx_before = ConstantsManager.OBJECT_GFX
    path_before = ConstantsManager.OBJECT_GFX_PATHS
    ConstantsManager.OBJECT_GFX = ['OBJ_EVENT_GFX_SOMEBODY',
                                   'OBJ_EVENT_GFX_CUTTABLE_THING',
                                   'OBJ_EVENT_GFX_NO_PICTURE']
    # Two of the three have no file on disk; the gallery still has to list
    # them, because they are valid choices.
    ConstantsManager.OBJECT_GFX_PATHS = {}
    eet._GFX_THUMB_CACHE.clear()
    yield
    ConstantsManager.OBJECT_GFX = gfx_before
    ConstantsManager.OBJECT_GFX_PATHS = path_before
    eet._GFX_THUMB_CACHE.clear()


def test_the_gallery_lists_every_sprite_the_project_defines(qapp, fake_sprites):
    dlg = eet._SpriteGalleryDialog()
    assert dlg.list.count() == 3


def test_a_sprite_with_no_picture_is_still_offered(qapp, fake_sprites):
    """A constant with no PNG is still something a script can switch to."""
    dlg = eet._SpriteGalleryDialog()
    names = {dlg.list.item(i).text() for i in range(dlg.list.count())}
    assert 'No Picture' in names


def test_the_gallery_hands_back_the_constant_not_the_label(qapp, fake_sprites):
    """Same trap as every other picker: the tile reads "Cuttable Thing" and
    the script must get OBJ_EVENT_GFX_CUTTABLE_THING."""
    dlg = eet._SpriteGalleryDialog(current='OBJ_EVENT_GFX_CUTTABLE_THING')
    dlg._accept()
    assert dlg.selected == 'OBJ_EVENT_GFX_CUTTABLE_THING'


def test_searching_the_gallery_matches_the_label_and_the_constant(qapp,
                                                                  fake_sprites):
    dlg = eet._SpriteGalleryDialog()
    dlg._filter('cuttable')
    shown = [dlg.list.item(i).text() for i in range(dlg.list.count())
             if not dlg.list.item(i).isHidden()]
    assert shown == ['Cuttable Thing']
    dlg._filter('OBJ_EVENT_GFX_SOMEBODY')
    shown = [dlg.list.item(i).text() for i in range(dlg.list.count())
             if not dlg.list.item(i).isHidden()]
    assert shown == ['Somebody']


def test_sprite_labels_are_readable(qapp):
    assert eet._gfx_label('OBJ_EVENT_GFX_CUTTABLE_TREE') == 'Cuttable Tree'


def test_change_graphic_still_writes_the_raw_constant(qapp, fake_sprites):
    """Adding a Browse button next to the picker must not change what the
    picker itself hands the script."""
    w = eet._widget_for_tuple(
        ('setobjectgraphics', 'LOCALID_PLAYER, OBJ_EVENT_GFX_SOMEBODY'))
    cmd, args = w.to_tuple()
    assert cmd == 'setobjectgraphics'
    assert args == 'LOCALID_PLAYER, OBJ_EVENT_GFX_SOMEBODY'


# ═══════════════════════════════════════════════════════════════════════════
# The frame-width rule both sprite views share
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize('sheet_w,h,expected', [
    (16, 16, 16),     # single square frame
    (32, 32, 32),
    (64, 64, 64),
    (64, 32, 32),     # two square frames, not four 16px ones
    (48, 32, 16),     # three directional stands
    (144, 32, 16),    # full nine-frame walk sheet
    (160, 32, 16),
])
def test_frame_width_is_inferred_the_same_way_everywhere(sheet_w, h, expected):
    """The animated preview and the gallery must agree about where a frame
    ends, or the same sprite is cropped differently in two places."""
    from eventide.ui.widgets import overworld_frame_width
    assert overworld_frame_width(sheet_w, h) == expected
