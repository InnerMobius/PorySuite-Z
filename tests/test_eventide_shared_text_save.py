"""Regression guard: editing BORROWED dialogue must reach the file that owns it.

THE BUG (reported 2026-11-18). On a map with a PC, the editor showed the bare
symbol `OneIsland_PokemonCenter_1F_Text_UsualPCServicesUnavailable` where the
words should be. Editing it and pressing Save appeared to work, and the text
came back unchanged every time.

Two halves, both silent:

* `parse_all_texts` reads the CURRENT map's `text.inc` and `data/text/*.inc`
  and nothing else. A shared script prints text owned by whichever map
  happened to define it, so on every other map that label resolved to nothing
  and the command list showed the symbol instead of the dialogue.
* the map save path writes only `_local_text_labels` and DROPS the rest with
  no warning, so the edit was written nowhere at all.

This is the same shape as "EVENTide: edits to a SHARED script were shown, then
silently dropped on save" (2026-10-25) one file over. That fix gave shared
scripts a writer; text had only the filter that excluded it.

THE RULE: `_resolve_external_texts` loads borrowed dialogue and records its
owning file plus a baseline; `_save_external_texts` writes back the ones that
actually changed, patching ONLY that block so the owner file's other labels and
its comments survive. Borrowed text is SHARED -- changing it changes it
everywhere -- so the write is reported, never silent.
"""
import json
import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

pytest.importorskip('PyQt6.QtWidgets')

from PyQt6.QtWidgets import QApplication  # noqa: E402

import eventide.ui.event_editor_tab as eet  # noqa: E402
from eventide.backend.eventide_utils import (  # noqa: E402
    find_text_definitions, patch_text_in_file, parse_text_inc,
)

BORROWED = 'OtherTown_PokemonCenter_1F_Text_PCUnavailable'

MAP_JSON = {
    "object_events": [
        {"type": "object", "graphics_id": "OBJ_EVENT_GFX_ITEM_BALL",
         "x": 8, "y": 4, "elevation": 0,
         "movement_type": "MOVEMENT_TYPE_NONE",
         "movement_range_x": 0, "movement_range_y": 0,
         "trainer_type": "TRAINER_TYPE_NONE",
         "trainer_sight_or_berry_tree_id": "0",
         "script": "Route1_EventScript_PC", "flag": "0"},
    ],
    "warp_events": [], "coord_events": [], "bg_events": [],
}

MAP_SCRIPT = """Route1_MapScripts::
.byte 0


Route1_EventScript_PC::
lockall
call EventScript_PC
releaseall
end
"""

# The shared script, in data/scripts/ — it prints text it does not own.
SHARED_SCRIPT = """@ The PC every town shares.
EventScript_PC::
\tmsgbox OtherTown_PokemonCenter_1F_Text_PCUnavailable, MSGBOX_DEFAULT
\treturn
"""

# The OWNER map's text.inc: the borrowed label plus neighbours and comments,
# so the test proves a one-label edit leaves the rest of the file alone.
OWNER_TEXT = """@ Dialogue for the town's Pokemon Center.

OtherTown_PokemonCenter_1F_Text_Welcome::
\t.string "Welcome!$"

OtherTown_PokemonCenter_1F_Text_PCUnavailable::
\t.string "The usual PC services aren't\\n"
\t.string "available right now.$"

@ Said by the attendant upstairs.
OtherTown_PokemonCenter_1F_Text_Goodbye::
\t.string "Do come again!$"
"""

LOCAL_TEXT = """Route1_Text_1::
\t.string "A sign.$"
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
    (shared / 'pc.inc').write_text(SHARED_SCRIPT, encoding='utf-8')

    owner = tmp_path / 'data' / 'maps' / 'OtherTown_PokemonCenter_1F'
    owner.mkdir(parents=True)
    (owner / 'text.inc').write_text(OWNER_TEXT, encoding='utf-8')

    d = tmp_path / 'data' / 'maps' / 'Route1'
    d.mkdir(parents=True)
    (d / 'map.json').write_text(json.dumps(MAP_JSON), encoding='utf-8')
    (d / 'scripts.inc').write_text(MAP_SCRIPT, encoding='utf-8')
    (d / 'text.inc').write_text(LOCAL_TEXT, encoding='utf-8')

    class StubMW:
        def __init__(self):
            self.messages = []

        def log_message(self, msg, *a, **k):
            self.messages.append(msg)

        def isWindowModified(self):
            return False

    mw = StubMW()
    t = eet.EventEditorTab(mw)
    t._root_dir = tmp_path
    t._load_map(d)
    return t, tmp_path, mw


def _owner_file(root):
    return (root / 'data' / 'maps' / 'OtherTown_PokemonCenter_1F' / 'text.inc')


def _message_cmd(t):
    """The resolved `message` command for the borrowed label, if any."""
    for cmds in list(t._all_scripts.values()):
        if not isinstance(cmds, list):
            continue
        for cmd in cmds:
            if cmd and cmd[0] == 'message' and len(cmd) > 1 and cmd[1] == BORROWED:
                return cmd
    return None


# ── The premise: it is found, and it is shown ───────────────────────────────

def test_borrowed_text_is_resolved_to_its_owning_file(tab):
    t, root, _ = tab
    assert BORROWED in t._external_text_files, \
        'the borrowed text was never resolved'
    assert t._external_text_files[BORROWED] == _owner_file(root)


def test_the_words_are_shown_not_the_bare_symbol(tab):
    """What the user actually reported seeing."""
    t, _, _ = tab
    cmd = _message_cmd(t)
    assert cmd is not None, 'the shared script was never resolved'
    assert cmd[2], 'the command still carries no text — the symbol would show'
    assert "usual PC services" in cmd[2]


def test_a_baseline_is_kept_for_the_borrowed_text(tab):
    t, _, _ = tab
    assert t._external_text_baseline[BORROWED] == t._texts[BORROWED]


# ── The fix: an edit reaches the owning file ────────────────────────────────

def test_editing_borrowed_text_writes_it_to_the_owner(tab):
    t, root, _ = tab
    t._texts[BORROWED] = "This PC is out of order.\nAsk inside."
    t._save_external_texts()
    assert parse_text_inc(_owner_file(root))[BORROWED] == \
        "This PC is out of order.\nAsk inside."


def test_the_write_is_reported_because_the_text_is_shared(tab):
    """Changing borrowed text changes it everywhere; the user is told."""
    t, _, mw = tab
    mw.messages.clear()
    t._texts[BORROWED] = 'Closed for repairs.'
    t._save_external_texts()
    assert any(BORROWED in m for m in mw.messages), mw.messages


def test_the_owner_files_other_texts_are_untouched(tab):
    t, root, _ = tab
    before = parse_text_inc(_owner_file(root))
    t._texts[BORROWED] = 'Closed for repairs.'
    t._save_external_texts()
    after = parse_text_inc(_owner_file(root))
    assert set(after) == set(before)
    for label in before:
        if label != BORROWED:
            assert after[label] == before[label], f'{label} was changed'


def test_the_owner_files_comments_survive(tab):
    """Rebuilding a shared file from a parsed dict strips its comments."""
    t, root, _ = tab
    t._texts[BORROWED] = 'Closed for repairs.'
    t._save_external_texts()
    now = _owner_file(root).read_text(encoding='utf-8')
    assert "@ Dialogue for the town's Pokemon Center." in now
    assert '@ Said by the attendant upstairs.' in now


def test_an_untouched_borrowed_text_leaves_its_file_alone(tab):
    """The other half — a plain save must not rewrite other maps' files."""
    t, root, _ = tab
    before = _owner_file(root).read_bytes()
    t._save_external_texts()
    assert _owner_file(root).read_bytes() == before


def test_borrowed_text_is_not_copied_into_this_maps_text_inc(tab):
    """A label defined twice is a duplicate symbol and the build stops."""
    t, root, _ = tab
    t._texts[BORROWED] = 'Closed for repairs.'
    t._on_save(external_ack=True)
    local = parse_text_inc(root / 'data' / 'maps' / 'Route1' / 'text.inc')
    assert BORROWED not in local


def test_the_save_button_reaches_the_owner_file(tab):
    """Proves the writer is WIRED IN, not merely correct when called."""
    t, root, _ = tab
    t._texts[BORROWED] = 'Out of order until further notice.'
    t._on_save(external_ack=True)
    assert parse_text_inc(_owner_file(root))[BORROWED] == \
        'Out of order until further notice.'


# ── Per-map records, like the shared-script ones ────────────────────────────

def test_the_records_are_cleared_when_another_map_opens(tab):
    """Or a text resolved for the PREVIOUS map stays eligible for write-back."""
    t, root, _ = tab
    other = root / 'data' / 'maps' / 'Elsewhere'
    other.mkdir(parents=True)
    (other / 'map.json').write_text(
        json.dumps({"object_events": [], "warp_events": [],
                    "coord_events": [], "bg_events": []}), encoding='utf-8')
    (other / 'scripts.inc').write_text('Elsewhere_MapScripts::\n.byte 0\n',
                                       encoding='utf-8')
    t._load_map(other)
    assert BORROWED not in t._external_text_files
    assert BORROWED not in t._external_text_baseline


# ── The resolver's own edges ────────────────────────────────────────────────

def test_a_script_label_is_never_claimed_as_a_text(tab):
    """`EventScript_PC` has no .string under it — it is a script."""
    _, root, _ = tab
    assert find_text_definitions(root, {'EventScript_PC'}) == {}


def test_an_unknown_label_is_simply_not_found(tab):
    _, root, _ = tab
    assert find_text_definitions(root, {'No_Such_Text_Anywhere'}) == {}


def test_patching_a_label_that_is_not_there_reports_failure(tab):
    """So the caller can warn instead of believing the edit was saved."""
    _, root, _ = tab
    assert patch_text_in_file(_owner_file(root), 'Missing_Label', 'x') is False


def test_patching_the_same_text_twice_does_not_rewrite_the_file(tab):
    t, root, _ = tab
    t._texts[BORROWED] = 'Closed for repairs.'
    t._save_external_texts()
    stamp = _owner_file(root).read_bytes()
    assert patch_text_in_file(_owner_file(root), BORROWED,
                              'Closed for repairs.') is True
    assert _owner_file(root).read_bytes() == stamp


def test_multi_line_text_round_trips(tab):
    t, root, _ = tab
    value = 'Line one.\nLine two.\n\nA new page.'
    t._texts[BORROWED] = value
    t._save_external_texts()
    assert parse_text_inc(_owner_file(root))[BORROWED] == value
