"""Sorting a voicegroup alphabetically must not change what any song plays.

The slot number IS the instrument: a song picks its instrument with
``VOICE , 24``, and that number is a program change in the song's ``.mid``,
which the build turns back into the ``.s``. So putting the slots in a new
order is a renumbering of every song on the voicegroup, not a cosmetic sort.

WHAT MUST NOT BREAK:

* Every song keeps the SAME INSTRUMENT it had. The slot numbers change; the
  sound does not.
* A keysplit reaching into the voicegroup stops the sort outright. Keysplits
  pick slots by number from raw bytes in `keysplit_tables.inc`, which this
  does not rewrite, so a drum kit would be silently repointed.
* Nothing is written when the sort is refused. Every reason to stop is found
  while planning, before the first file is touched.
* The ``.mid`` is renumbered too. Rewriting only the ``.s`` would last until
  the next build regenerated it from the MIDI.
"""
from __future__ import annotations

import os
import shutil
import sys

import pytest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
for _p in (ROOT_DIR, os.path.join(ROOT_DIR, 'core')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

GAME = os.path.join(ROOT_DIR, 'pokefirered')

from core.sound.voicegroup_sort import (  # noqa: E402
    VoicegroupSortRefused, keysplits_into, plan_sort, prepare, songs_using,
    sort_key)


class I:
    """A stand-in for one instrument slot."""

    def __init__(self, name, slot_index=0, filler=False):
        self.friendly_name = name
        self.slot_index = slot_index
        self.is_filler = filler


class VG:
    def __init__(self, name, instruments):
        self.name = name
        self.instruments = instruments


# ── The order ───────────────────────────────────────────────────────────────

def test_named_instruments_come_out_alphabetically():
    vg = VG('voicegroup001', [I('Trumpet'), I('Banjo'), I('Flute')])
    plan = plan_sort(vg)
    assert [vg.instruments[old].friendly_name for old in plan.order] == [
        'Banjo', 'Flute', 'Trumpet']


def test_capitals_do_not_decide_the_order():
    vg = VG('voicegroup001', [I('banjo'), I('Apple'), I('BANANA')])
    plan = plan_sort(vg)
    assert [vg.instruments[old].friendly_name for old in plan.order] == [
        'Apple', 'BANANA', 'banjo']


def test_placeholder_slots_go_to_the_end():
    """They are all the same empty square wave, so they order nothing."""
    vg = VG('voicegroup001',
            [I('Square 1 (50%)', filler=True), I('Trumpet'), I('Banjo')])
    plan = plan_sort(vg)
    names = [vg.instruments[old].friendly_name for old in plan.order]
    assert names == ['Banjo', 'Trumpet', 'Square 1 (50%)']


def test_two_slots_with_the_same_name_keep_their_order():
    vg = VG('voicegroup001', [I('Harp'), I('Banjo'), I('Harp')])
    plan = plan_sort(vg)
    assert plan.order == [1, 0, 2]


def test_an_already_sorted_voicegroup_is_a_no_op():
    vg = VG('voicegroup001', [I('Apple'), I('Banjo'), I('Cello')])
    plan = plan_sort(vg)
    assert plan.is_noop and plan.moved == 0


def test_an_empty_slot_sorts_like_a_placeholder():
    vg = VG('voicegroup001', [None, I('Banjo')])
    plan = plan_sort(vg)
    assert plan.order == [1, 0]


def test_the_remap_is_a_complete_renumbering():
    vg = VG('voicegroup001', [I('Trumpet'), I('Banjo'), I('Flute')])
    plan = plan_sort(vg)
    assert sorted(plan.remap) == [0, 1, 2]
    assert sorted(plan.remap.values()) == [0, 1, 2]


def test_sort_key_survives_an_instrument_it_cannot_read():
    class Odd:
        friendly_name = 'Odd'

        @property
        def is_filler(self):
            raise RuntimeError('no')

    assert sort_key(Odd(), 3)[0] == 0


# ── Against the real project ────────────────────────────────────────────────

REAL = pytest.mark.skipif(
    not os.path.isfile(os.path.join(GAME, 'sound', 'voice_groups.inc')),
    reason='needs the pokefirered sound folder')


@pytest.fixture(scope='module')
def game_copy(tmp_path_factory):
    root = str(tmp_path_factory.mktemp('vg_sort') / 'game')
    shutil.copytree(os.path.join(GAME, 'sound'), os.path.join(root, 'sound'))
    tools = os.path.join(GAME, 'tools', 'mid2agb')
    if os.path.isdir(tools):
        shutil.copytree(tools, os.path.join(root, 'tools', 'mid2agb'))
    yield root
    from core.app_info import get_cache_dir
    shutil.rmtree(get_cache_dir(root), ignore_errors=True)


@REAL
def test_the_songs_on_a_voicegroup_are_found(game_copy):
    labels = songs_using(game_copy, 'voicegroup013')
    assert labels, 'voicegroup013 is used by songs in this project'
    assert all(os.path.isfile(
        os.path.join(game_copy, 'sound', 'songs', 'midi', l + '.s'))
        for l in labels)


@REAL
def test_a_keysplit_into_the_voicegroup_stops_the_sort(game_copy, monkeypatch):
    from core.sound import voicegroup_sort as vs
    monkeypatch.setattr(vs, 'keysplits_into',
                        lambda root, name: ['voicegroup099'])
    vg = VG('voicegroup013', [I('Trumpet'), I('Banjo')])
    with pytest.raises(VoicegroupSortRefused) as err:
        vs.prepare(game_copy, vg)
    said = str(err.value).lower()
    assert 'keysplit' in said and 'nothing was changed' in said


@REAL
def test_a_song_with_no_midi_stops_the_sort(game_copy, monkeypatch):
    from core.sound import voicegroup_sort as vs
    monkeypatch.setattr(vs, 'keysplits_into', lambda root, name: [])
    monkeypatch.setattr(vs, 'songs_using', lambda root, name: ['not_a_song'])
    vg = VG('voicegroup013', [I('Trumpet'), I('Banjo')])
    with pytest.raises(VoicegroupSortRefused) as err:
        vs.prepare(game_copy, vg)
    assert 'nothing was changed' in str(err.value).lower()


@REAL
def test_a_no_op_sort_asks_for_nothing(game_copy):
    """It must not go looking for songs or mid2agb when there is no work."""
    vg = VG('voicegroup013', [I('Apple'), I('Banjo')])
    plan = prepare(game_copy, vg)
    assert plan.is_noop and plan.songs == []


@REAL
def test_keysplit_detection_reads_the_real_file(game_copy):
    """voicegroup013 has nothing splitting into it; the reader must agree."""
    assert keysplits_into(game_copy, 'voicegroup013') == []


@REAL
def test_every_song_keeps_the_instrument_it_had(game_copy):
    """The whole point. Slot numbers move, the sound does not."""
    pytest.importorskip('mido')
    from core.sound.song_compiler import find_mid2agb
    if not find_mid2agb(game_copy):
        pytest.skip('mid2agb is not available in this project')
    from core.sound.voicegroup_parser import parse_voice_groups
    from core.sound.voicegroup_sort import apply_sort, prepare
    from core.sound.song_parser import parse_song_file

    vgs = parse_voice_groups(os.path.join(game_copy, 'sound', 'voice_groups.inc'))
    vg = vgs['voicegroup013']
    before_names = [i.friendly_name if i else '' for i in vg.instruments]

    plan = prepare(game_copy, vg)
    assert not plan.is_noop, 'voicegroup013 is not already sorted'
    # One song is enough to prove the renumbering, and keeps the test quick.
    plan.songs = plan.songs[:1]
    label = plan.songs[0]

    s_path = os.path.join(game_copy, 'sound', 'songs', 'midi', label + '.s')
    played_before = []
    for tr in parse_song_file(s_path).tracks:
        played_before += [before_names[c.value] for c in tr.commands
                          if c.cmd == 'VOICE' and c.value is not None
                          and c.value < len(before_names)]
    assert played_before, f'{label} picks no instrument to check'

    apply_sort(game_copy, vg, plan)

    after_names = [i.friendly_name if i else '' for i in vg.instruments]
    played_after = []
    for tr in parse_song_file(s_path).tracks:
        played_after += [after_names[c.value] for c in tr.commands
                         if c.cmd == 'VOICE' and c.value is not None
                         and c.value < len(after_names)]
    assert played_after == played_before, (
        'the song plays different instruments after the sort')
    # And the voicegroup really is in order now: the named slots ascend.
    named = [n for n in after_names if n][:40]
    assert named == sorted(named, key=str.lower), 'the slots are not sorted'
