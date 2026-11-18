"""Clicking an instrument's name plays it at middle C — in both editors.

The real ``SoundEditorTab`` loads a throwaway copy of the game's sound folder
and the test clicks real rows with the mouse (offscreen). Rendering and the
audio device are both faked: the renderer records which instrument and note it
was asked for, and the player records play/stop. Nothing in the real project
is read for writing, and the copy is hashed before and after the clicks to
prove a preview changes no file.

What is pinned:
* a name click in the Instruments tab previews THAT instrument at note 60;
* a name click in the Voicegroups tab previews THAT slot's instrument at note
  60, through the same single preview player;
* a second click stops the first preview instead of stacking a second sound;
* clicks on other columns (and on group header rows) stay silent;
* no click marks anything unsaved or changes any file.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
import time
import types

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
pytest.importorskip('PyQt6.QtWidgets')

from PyQt6.QtCore import QPoint, Qt  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
GAME = os.path.join(ROOT_DIR, 'pokefirered')

pytestmark = pytest.mark.skipif(
    not os.path.isfile(os.path.join(GAME, 'sound', 'voice_groups.inc'))
    or not os.path.isfile(os.path.join(GAME, 'include', 'constants', 'songs.h')),
    reason='needs the pokefirered sound folder and songs.h to copy')


class _FakePlayer:
    created: list = []

    def __init__(self):
        self.volume = 0.0
        self.played = False
        self.stopped = False
        _FakePlayer.created.append(self)

    def play(self, audio, rate, mono=False):
        self.played = True

    def stop(self):
        self.stopped = True


def _hash_tree(root: str) -> dict:
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            p = os.path.join(dirpath, name)
            with open(p, 'rb') as fh:
                out[os.path.relpath(p, root)] = hashlib.sha1(fh.read()).hexdigest()
    return out


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope='module')
def editor(qapp, tmp_path_factory):
    """A real Sound Editor on a throwaway copy of the game's sound data."""
    real_sd = sys.modules.get('sounddevice')
    sys.modules['sounddevice'] = types.SimpleNamespace(OutputStream=None)

    root = str(tmp_path_factory.mktemp('name_click_preview') / 'game')
    shutil.copytree(os.path.join(GAME, 'sound'), os.path.join(root, 'sound'))
    os.makedirs(os.path.join(root, 'include', 'constants'))
    shutil.copy2(os.path.join(GAME, 'include', 'constants', 'songs.h'),
                 os.path.join(root, 'include', 'constants'))

    from ui.sound_editor_tab import SoundEditorTab
    se = SoundEditorTab()
    se.resize(1400, 900)
    se.show()
    se.load_project(root)
    qapp.processEvents()
    se.test_root = root
    yield se

    se.hide()
    se.deleteLater()
    qapp.processEvents()
    if real_sd is not None:
        sys.modules['sounddevice'] = real_sd
    else:
        sys.modules.pop('sounddevice', None)
    slug = hashlib.sha1(os.path.abspath(root).encode()).hexdigest()[:12]
    shutil.rmtree(os.path.join(ROOT_DIR, 'cache', slug), ignore_errors=True)


@pytest.fixture
def fakes(monkeypatch, editor):
    """Fake the renderer and the audio device; record every request."""
    import core.sound.audio_engine as audio_engine
    import core.sound.track_renderer as tr

    _FakePlayer.created = []
    monkeypatch.setattr(audio_engine, 'AudioPlayer', _FakePlayer)
    calls = []

    def _fake_render(inst, midi, samples, vg, duration_ms=0, velocity=100):
        calls.append((inst, midi, duration_ms))
        return [0.0] * 16

    monkeypatch.setattr(tr, 'render_instrument_preview', _fake_render)

    modified = []
    editor.modified.connect(lambda: modified.append(True))
    yield types.SimpleNamespace(calls=calls, modified=modified)
    try:
        editor.modified.disconnect()
    except TypeError:
        pass
    # Leave no player behind for the next test.
    editor._instruments_tab._stop_preview_player()


def _wait_for_players(qapp, n: int, timeout: float = 5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        qapp.processEvents()
        if sum(p.played for p in _FakePlayer.created) >= n:
            return
        time.sleep(0.01)
    qapp.processEvents()


def _click(qapp, tree, item, column: int):
    tree.scrollToItem(item)
    qapp.processEvents()
    rect = tree.visualItemRect(item)
    x = tree.header().sectionViewportPosition(column) + 8
    QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier,
                     QPoint(x, rect.center().y()))
    qapp.processEvents()


def _assert_nothing_dirty(editor, fakes):
    assert not fakes.modified, 'a preview click emitted "modified"'
    assert not editor._instruments_tab._dirty_inst_keys
    assert not editor._voicegroups_tab.has_unsaved_changes()
    assert not editor._dirty_songs


def _previewable_instrument_rows(itab, count: int):
    """Instrument-list rows that the Play button could preview."""
    rows = []
    tree = itab._inst_tree
    for gi in range(tree.topLevelItemCount()):
        group = tree.topLevelItem(gi)
        group.setExpanded(True)
        for ci in range(group.childCount()):
            child = group.child(ci)
            inst, _vg = itab._instrument_for_item(child)
            if inst is not None and not inst.is_keysplit:
                rows.append((child, inst))
                if len(rows) == count:
                    return rows
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# Instruments tab
# ─────────────────────────────────────────────────────────────────────────────

def test_instruments_tab_name_click_plays_that_instrument_at_60(
        qapp, editor, fakes):
    editor._tab_widget.setCurrentWidget(editor._instruments_tab)
    qapp.processEvents()
    itab = editor._instruments_tab
    before = _hash_tree(editor.test_root)

    rows = _previewable_instrument_rows(itab, 2)
    assert len(rows) == 2, 'the copy should have previewable instruments'
    (item_a, inst_a), (item_b, inst_b) = rows

    _click(qapp, itab._inst_tree, item_a, 0)
    _wait_for_players(qapp, 1)
    assert [(c[0], c[1]) for c in fakes.calls] == [(inst_a, 60)]
    assert fakes.calls[0][2] == 800, 'a name click is the short preview'
    assert len(_FakePlayer.created) == 1 and _FakePlayer.created[0].played

    # A second click replaces the first preview rather than stacking.
    _click(qapp, itab._inst_tree, item_b, 0)
    _wait_for_players(qapp, 2)
    assert [(c[0], c[1]) for c in fakes.calls] == [(inst_a, 60), (inst_b, 60)]
    first, second = _FakePlayer.created
    assert first.stopped, 'the first preview kept sounding under the second'
    assert second.played and not second.stopped
    assert itab._audio_player is second

    _assert_nothing_dirty(editor, fakes)
    assert _hash_tree(editor.test_root) == before, 'a preview click changed a file'


def test_instruments_tab_other_column_and_group_rows_stay_silent(
        qapp, editor, fakes):
    editor._tab_widget.setCurrentWidget(editor._instruments_tab)
    qapp.processEvents()
    itab = editor._instruments_tab
    (item, _inst), = _previewable_instrument_rows(itab, 1)

    _click(qapp, itab._inst_tree, item, 1)            # "Used In" column
    _click(qapp, itab._inst_tree, item.parent(), 0)   # type-group header
    _wait_for_players(qapp, 1, timeout=0.5)
    assert fakes.calls == []
    assert _FakePlayer.created == []


# ─────────────────────────────────────────────────────────────────────────────
# Voicegroups tab
# ─────────────────────────────────────────────────────────────────────────────

def _voicegroup_with_previewable_slots(vgtab, count: int):
    """Select a voicegroup with ``count`` previewable slots; return the rows."""
    from ui.voicegroups_tab import _is_filler_instrument, _ROLE_SLOT_IDX
    tree = vgtab._vg_tree
    for i in range(tree.topLevelItemCount()):
        tree.setCurrentItem(tree.topLevelItem(i))
        vg = vgtab._current_vg
        rows = []
        for si in range(vgtab._slot_tree.topLevelItemCount()):
            row = vgtab._slot_tree.topLevelItem(si)
            inst = vg.get_instrument(row.data(0, _ROLE_SLOT_IDX))
            if (inst and not _is_filler_instrument(inst)
                    and not inst.is_keysplit):
                rows.append((row, inst))
                if len(rows) == count:
                    return rows
    return []


def test_voicegroups_tab_name_click_plays_that_slot_at_60(qapp, editor, fakes):
    editor._tab_widget.setCurrentWidget(editor._voicegroups_tab)
    qapp.processEvents()
    vgtab = editor._voicegroups_tab
    before = _hash_tree(editor.test_root)

    rows = _voicegroup_with_previewable_slots(vgtab, 2)
    assert len(rows) == 2, 'the copy should have a voicegroup to preview'
    (row_a, inst_a), (row_b, inst_b) = rows

    _click(qapp, vgtab._slot_tree, row_a, vgtab._SLOT_NAME_COL)
    _wait_for_players(qapp, 1)
    assert [(c[0], c[1]) for c in fakes.calls] == [(inst_a, 60)]
    assert fakes.calls[0][0] is inst_a, 'previewed a different instrument'

    _click(qapp, vgtab._slot_tree, row_b, vgtab._SLOT_NAME_COL)
    _wait_for_players(qapp, 2)
    assert [(c[0], c[1]) for c in fakes.calls] == [(inst_a, 60), (inst_b, 60)]
    first, second = _FakePlayer.created
    assert first.stopped, 'the first preview kept sounding under the second'
    assert second.played and not second.stopped

    _assert_nothing_dirty(editor, fakes)
    assert _hash_tree(editor.test_root) == before, 'a preview click changed a file'


def test_voicegroups_tab_other_columns_stay_silent(qapp, editor, fakes):
    editor._tab_widget.setCurrentWidget(editor._voicegroups_tab)
    qapp.processEvents()
    vgtab = editor._voicegroups_tab
    (row, _inst), = _voicegroup_with_previewable_slots(vgtab, 1)

    for col in (0, 1, 3):
        _click(qapp, vgtab._slot_tree, row, col)
    _wait_for_players(qapp, 1, timeout=0.5)
    assert fakes.calls == []
    assert _FakePlayer.created == []
    _assert_nothing_dirty(editor, fakes)


def test_a_click_in_one_tab_replaces_a_preview_from_the_other(
        qapp, editor, fakes):
    """Both editors share ONE preview player, so sounds never stack."""
    editor._tab_widget.setCurrentWidget(editor._voicegroups_tab)
    qapp.processEvents()
    vgtab = editor._voicegroups_tab
    (row, inst_v), = _voicegroup_with_previewable_slots(vgtab, 1)
    _click(qapp, vgtab._slot_tree, row, vgtab._SLOT_NAME_COL)
    _wait_for_players(qapp, 1)

    editor._tab_widget.setCurrentWidget(editor._instruments_tab)
    qapp.processEvents()
    itab = editor._instruments_tab
    (item, inst_i), = _previewable_instrument_rows(itab, 1)
    _click(qapp, itab._inst_tree, item, 0)
    _wait_for_players(qapp, 2)

    assert [(c[0], c[1]) for c in fakes.calls] == [(inst_v, 60), (inst_i, 60)]
    assert all(p.stopped for p in _FakePlayer.created[:-1])
    assert itab._audio_player is _FakePlayer.created[-1]
    _assert_nothing_dirty(editor, fakes)


# ─────────────────────────────────────────────────────────────────────────────
# The note label beside Play Note
# ─────────────────────────────────────────────────────────────────────────────

def test_the_note_label_names_the_note_actually_previewed(qapp, editor, fakes):
    """It started as a hard-coded "C4 (60)" while the piano called 60 "C5",
    and after a piano key it kept naming that key while Play Note played 60."""
    import ui.instruments_tab as itab_mod
    itab = editor._instruments_tab
    label = itab._preview_note_label

    fresh = itab_mod.InstrumentsTab()
    assert fresh._preview_note_label.text() == 'C5 (60)'
    assert fresh._preview_note_label.text() == (
        f'{itab_mod._midi_to_name(60)} (60)'), 'label and piano disagree'
    fresh.deleteLater()

    editor._tab_widget.setCurrentWidget(itab)
    qapp.processEvents()
    (item, _inst), = _previewable_instrument_rows(itab, 1)
    _click(qapp, itab._inst_tree, item, 0)
    _wait_for_players(qapp, 1)
    assert label.text() == 'C5 (60)'

    itab._on_piano_key(64)
    itab._on_piano_key_released(64)
    assert label.text() == 'E5 (64)'

    itab._on_play_preview()
    assert label.text() == 'C5 (60)', 'Play Note left the old key named'
    _wait_for_players(qapp, 2)
    _assert_nothing_dirty(editor, fakes)
