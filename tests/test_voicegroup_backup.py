"""Rewriting sound/voice_groups.inc must leave a copy of what it replaced.

BUGS.md 2026-11-18, "Something overwrote 17 slots of voicegroup013". Saving
voicegroups writes the WHOLE file from memory -- every voicegroup, every
slot -- so a wrong or stale in-memory state overwrites the lot, silently and
without a diff. Nothing kept a copy: `src/` has backups and
`data/event_scripts.s` has backups, `sound/` had none. When seventeen slots of
one voicegroup were overwritten, the committed copy in git was the only
surviving record of what had been there, and an uncommitted project would have
had nothing at all.

WHAT MUST NOT BREAK:

* Every path that rewrites `voice_groups.inc` copies the existing file first.
  There are two: the Voicegroups tab's save, and `append_voicegroup_to_inc`,
  which looks additive but REPLACES the block when the name already exists.
* The copy lands in PorySuite's cache, never in the game tree (*Hands Off
  pokefirered* -- a backup file dropped in `sound/` would be assembled).
* A backup that cannot be made STOPS THE WRITE. An unsaved edit can be made
  again; an overwritten voicegroup cannot. The pending edits stay pending so
  the caller reports a failed save rather than a silent one.
"""
from __future__ import annotations

import hashlib
import io
import os
import shutil
import sys
import types

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
pytest.importorskip('PyQt6.QtWidgets')

from PyQt6.QtWidgets import QApplication  # noqa: E402

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
GAME = os.path.join(ROOT_DIR, 'pokefirered')

pytestmark = pytest.mark.skipif(
    not os.path.isfile(os.path.join(GAME, 'sound', 'voice_groups.inc'))
    or not os.path.isfile(os.path.join(GAME, 'include', 'constants', 'songs.h')),
    reason='needs the pokefirered sound folder and songs.h to copy')

VG_REL = os.path.join('sound', 'voice_groups.inc')


def _read(path: str) -> str:
    with io.open(path, encoding='utf-8', newline='') as fh:
        return fh.read()


def _backups(root: str) -> list:
    """Every backed-up voice_groups.inc for *root*, newest last."""
    from core.app_info import get_cache_dir
    d = os.path.join(get_cache_dir(root), 'voicegroup_backups')
    if not os.path.isdir(d):
        return []
    out = []
    for stamp in sorted(os.listdir(d)):
        p = os.path.join(d, stamp, 'voice_groups.inc')
        if os.path.isfile(p):
            out.append(p)
    return out


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope='module')
def editor(qapp, tmp_path_factory):
    """A real Sound Editor on a throwaway copy of the game's sound folder."""
    real_sd = sys.modules.get('sounddevice')
    sys.modules['sounddevice'] = types.SimpleNamespace(OutputStream=None)
    root = str(tmp_path_factory.mktemp('vg_backup') / 'game')
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


# ── The Voicegroups tab's save ──────────────────────────────────────────────

def test_saving_keeps_a_copy_of_what_was_replaced(editor):
    root = editor.test_root
    path = os.path.join(root, VG_REL)
    before = _read(path)
    had = len(_backups(root))

    editor._voicegroups_tab.save_to_disk()

    kept = _backups(root)
    assert len(kept) == had + 1, 'the save did not leave a backup'
    assert _read(kept[-1]) == before, (
        'the backup is not what was on disk before the save')


def test_the_copy_is_not_left_in_the_game_tree(editor):
    """A stray .inc under sound/ would be assembled into the ROM."""
    root = os.path.abspath(editor.test_root)
    for p in _backups(root):
        assert not os.path.abspath(p).startswith(root + os.sep), p


def test_a_backup_that_fails_stops_the_write(editor, monkeypatch):
    """The point of the whole thing: refuse rather than overwrite blind."""
    root = editor.test_root
    path = os.path.join(root, VG_REL)
    vgtab = editor._voicegroups_tab
    vgtab.mark_voicegroups_dirty(['voicegroup013'])
    before = _read(path)

    def no_backup(*a, **k):
        raise OSError('disk full')

    monkeypatch.setattr('core.sound.song_sync.backup_song_file', no_backup)
    with pytest.raises(OSError):
        vgtab.save_to_disk()

    assert _read(path) == before, 'the file was overwritten anyway'
    assert vgtab.has_unsaved_changes(), (
        'the edits were marked saved even though nothing was written')


def test_the_failure_says_nothing_was_changed(editor, monkeypatch):
    """The caller puts this message in front of the user."""
    vgtab = editor._voicegroups_tab
    vgtab.mark_voicegroups_dirty(['voicegroup013'])

    def no_backup(*a, **k):
        raise OSError('disk full')

    monkeypatch.setattr('core.sound.song_sync.backup_song_file', no_backup)
    with pytest.raises(OSError) as err:
        vgtab.save_to_disk()
    said = str(err.value).lower()
    assert 'not saved' in said or 'not written' in said
    assert 'nothing was changed' in said


# ── append_voicegroup_to_inc ────────────────────────────────────────────────

def _tiny_vg(name, sample):
    from core.sound.voicegroup_parser import Instrument, Voicegroup
    inst = Instrument(slot_index=0, voice_type='voice_directsound',
                      type_byte=0, sample_label=sample,
                      attack=255, decay=0, sustain=255, release=0)
    vg = Voicegroup(name=name, number=int(name[-3:]))
    vg.instruments = [inst]
    return vg


def test_appending_backs_up_too_because_it_can_replace(tmp_path):
    """It is not additive when the name already exists -- it replaces the
    whole block, every slot of it."""
    from core.sound.voicegroup_writer import append_voicegroup_to_inc
    root = tmp_path / 'game'
    (root / 'sound').mkdir(parents=True)
    path = root / 'sound' / 'voice_groups.inc'
    path.write_text(
        '\t.align 2\n'
        'voicegroup013::\n'
        '\tvoice_directsound 60, 0, DirectSoundWaveData_sc88pro_flute, 255, 127, 231, 127\n',
        encoding='utf-8', newline='\n')
    before = _read(str(path))

    append_voicegroup_to_inc(_tiny_vg('voicegroup013', 'DirectSoundWaveData_other'),
                             str(root))

    kept = _backups(str(root))
    assert kept, 'replacing a block left no backup'
    assert _read(kept[-1]) == before
    assert 'sc88pro_flute' not in _read(str(path)), 'the block was not replaced'
    assert 'sc88pro_flute' in _read(kept[-1]), 'the backup lost the old slot'
    from core.app_info import get_cache_dir
    shutil.rmtree(get_cache_dir(str(root)), ignore_errors=True)


def test_the_very_first_write_has_nothing_to_back_up(tmp_path):
    """No file yet is not a failure."""
    from core.sound.voicegroup_writer import append_voicegroup_to_inc
    root = tmp_path / 'fresh'
    (root / 'sound').mkdir(parents=True)
    append_voicegroup_to_inc(_tiny_vg('voicegroup001', 'DirectSoundWaveData_x'),
                             str(root))
    assert (root / 'sound' / 'voice_groups.inc').is_file()
    assert _backups(str(root)) == []
    from core.app_info import get_cache_dir
    shutil.rmtree(get_cache_dir(str(root)), ignore_errors=True)
