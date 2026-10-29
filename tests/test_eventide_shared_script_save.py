"""Regression guard: editing a SHARED script must reach its file on save.

THE BUG (reported 2026-10-25). A chest's open sound was changed in the Event
Editor. The command list showed the edit, Save reported success, and the game
went on playing the old sound -- because the edit was never written anywhere.

A shared script (`data/scripts/*.inc`, `data/event_scripts.s`) is resolved when
a map that calls it is opened, and its commands are appended into the CALLING
page's list behind a `_label_marker`. That is why `Std_ChestOpen` appears under
the event that calls it rather than as a page of its own. `_save_external_scripts`
collected candidates by testing each page's `_label` -- which is the CALLER's
label, never the shared one -- so the shared segment was never collected. The
map-file writer meanwhile correctly SKIPS external labels, so the edit landed in
neither file and vanished with no error.

THE RULE: `_save_external_scripts` splits each page's commands on
`_label_marker` exactly as the map-file save path does, and collects every
segment whose owning label is external. Untouched shared scripts must still be
left completely alone -- that is the other half of this, guarded below, and the
reason the baseline comparison exists at all (docs/BUGS.md, "EVENTide: saving a
map rewrites shared scripts and deletes their comments").
"""
import json
import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

pytest.importorskip('PyQt6.QtWidgets')

from PyQt6.QtWidgets import QApplication  # noqa: E402

import eventide.ui.event_editor_tab as eet  # noqa: E402


MAP_JSON = {
    "object_events": [
        {"type": "object", "graphics_id": "OBJ_EVENT_GFX_ITEM_BALL",
         "x": 8, "y": 4, "elevation": 0,
         "movement_type": "MOVEMENT_TYPE_NONE",
         "movement_range_x": 0, "movement_range_y": 0,
         "trainer_type": "TRAINER_TYPE_NONE", "trainer_sight_or_berry_tree_id": "0",
         "script": "Faron_EventScript_ItemBall1", "flag": "0"},
    ],
    "warp_events": [], "coord_events": [], "bg_events": [],
}

MAP_SCRIPT = """Faron_MapScripts::
.byte 0


Faron_EventScript_ItemBall1::
setvar VAR_0x8004, REGITEM_RUPEE_RED
call Std_ChestOpen
msgbox Faron_Text_2, MSGBOX_DEFAULT
call Std_ChestClose
end
"""

# Two shared scripts in one file, with comments and blank lines, so the test
# also proves the untouched one keeps its formatting.
SHARED = """@ Oracle-style dungeon chest.
Std_ChestOpen::
\tlockall
\tremoveobject VAR_LAST_TALKED                @ ball vanishes
\tplayse SE_LIFT_UP                           @ the unlock
\tspecial ChestItemGet_Start
\twaitstate
\treturn

Std_ChestClose::
\tspecial ChestItemGet_End                    @ destroy the icon
\treleaseall
\treturn
"""

TEXT = """Faron_Text_2:
\t.string "You found 20 Rupees!$"
"""


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def tab(qapp, tmp_path, monkeypatch):
    for name in ('information', 'warning', 'critical', 'question'):
        monkeypatch.setattr(eet.QMessageBox, name,
                            staticmethod(lambda *a, **k: None), raising=False)
    shared = tmp_path / 'data' / 'scripts'
    shared.mkdir(parents=True)
    (shared / 'chest.inc').write_text(SHARED, encoding='utf-8')

    d = tmp_path / 'data' / 'maps' / 'Faron'
    d.mkdir(parents=True)
    (d / 'map.json').write_text(json.dumps(MAP_JSON), encoding='utf-8')
    (d / 'scripts.inc').write_text(MAP_SCRIPT, encoding='utf-8')
    (d / 'text.inc').write_text(TEXT, encoding='utf-8')

    class StubMW:
        def log_message(self, *a, **k):
            pass

        def isWindowModified(self):
            return False

    t = eet.EventEditorTab(StubMW())
    t._root_dir = tmp_path
    t._load_map(d)
    return t, tmp_path


def _shared_text(root):
    return (root / 'data' / 'scripts' / 'chest.inc').read_text(encoding='utf-8')


def _find_page_with(t, head):
    """The page holding a command whose opcode is `head`, plus that index."""
    for obj in t._objects:
        for page in obj.get('_pages', []):
            for i, cmd in enumerate(page.get('commands', [])):
                if cmd and cmd[0] == head:
                    return page, i
    return None, -1


def test_shared_script_is_resolved_into_the_calling_page(tab):
    """The premise: Std_ChestOpen is NOT its own page, it is a marker inside one."""
    t, _ = tab
    assert 'Std_ChestOpen' in t._external_script_labels

    page, idx = _find_page_with(t, 'playse')
    assert page is not None, 'the shared script was never resolved for display'
    # Its page belongs to the CALLER, which is exactly why testing the page's
    # own label missed it.
    assert page.get('_label') != 'Std_ChestOpen'
    markers = [c[1] for c in page['commands']
               if c and c[0] == '_label_marker' and len(c) > 1]
    assert 'Std_ChestOpen' in markers


def test_editing_a_shared_script_writes_it_back(tab):
    """The bug: this edit used to be shown, then silently dropped."""
    t, root = tab
    page, idx = _find_page_with(t, 'playse')
    # The parser keeps a trailing inline comment inside the argument, so this
    # is a prefix check rather than an equality one.
    assert page['commands'][idx][1].startswith('SE_LIFT_UP')
    page['commands'][idx] = ('playse', 'SE_CLICK')

    t._save_external_scripts()

    out = _shared_text(root)
    assert 'SE_CLICK' in out, 'the edit never reached data/scripts/chest.inc'
    assert 'SE_LIFT_UP' not in out


def test_edited_shared_script_keeps_its_comments_and_neighbours(tab):
    """Writing one label must not strip the file or disturb the other block."""
    t, root = tab
    page, idx = _find_page_with(t, 'playse')
    page['commands'][idx] = ('playse', 'SE_CLICK')

    t._save_external_scripts()

    out = _shared_text(root)
    # The untouched neighbour survives intact, body and trailing comment.
    assert 'Std_ChestClose::' in out
    assert 'special ChestItemGet_End' in out
    assert 'destroy the icon' in out
    # The edited block keeps the comments that sat beside its other commands.
    assert 'ball vanishes' in out
    assert 'special ChestItemGet_Start' in out


def test_untouched_shared_script_is_left_byte_identical(tab):
    """The other half: a plain save must not rewrite shared files at all."""
    t, root = tab
    before = _shared_text(root)

    t._save_external_scripts()

    assert _shared_text(root) == before


def test_caller_page_does_not_absorb_the_shared_body(tab):
    """The shared segment is collected under its OWN label, not the caller's.

    The previous code, had it matched at all, would have written the whole
    page -- caller commands included -- into the shared file.
    """
    t, root = tab
    page, idx = _find_page_with(t, 'playse')
    page['commands'][idx] = ('playse', 'SE_CLICK')

    t._save_external_scripts()

    out = _shared_text(root)
    assert 'Faron_EventScript_ItemBall1' not in out
    assert 'REGITEM_RUPEE_RED' not in out
