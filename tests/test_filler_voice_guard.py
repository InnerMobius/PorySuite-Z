"""Guards for the empty-instrument-slot problem.

A voicegroup pads every slot no song references with one exact placeholder:

    voice_square_1 60, 0, 0, 2, 0, 0, 15, 0

A song pointed at one of those assembles and plays, but every voice aimed at a
placeholder makes the same featureless beep — so an imported song can land on a
near-empty voicegroup (a couple of real entries padded out with that square)
and come out sounding nothing like itself, which reads as "the app didn't build
my file".

These tests lock in the three pieces of the fix:
  * ONE definition of "filler", shared by every path (it used to be copy-pasted
    into four modules).
  * A helper that finds the nearest slot holding a real instrument, so the
    import wizards can default to something audible.
  * A validator warning, so every path that writes a song can report it — not
    just the wizard the user happened to use.
"""

import os
import sys
import types

project_root = os.path.join(os.path.dirname(__file__), '..')
sys.path.insert(0, project_root)
local_env_mod = types.ModuleType('local_env')
local_env_mod.LocalUtil = type('LocalUtil', (), {})
sys.modules['local_env'] = local_env_mod

from core.sound.voicegroup_parser import (  # noqa: E402
    Instrument,
    count_real_instruments,
    is_filler_instrument,
    nearest_real_slot,
)
from core.sound.song_validator import validate_song_warnings  # noqa: E402


def _filler(slot=0):
    """The exact padding entry, as voice_groups.inc writes it."""
    return Instrument(
        slot_index=slot, voice_type='voice_square_1', type_byte=0x01,
        base_midi_key=60, pan=0, sweep=0, duty_cycle=2,
        attack=0, decay=0, sustain=15, release=0,
    )


def _real(slot=0, label='DirectSoundWaveData_harp'):
    return Instrument(
        slot_index=slot, voice_type='voice_directsound', type_byte=0x00,
        base_midi_key=60, pan=0, sample_label=label,
        attack=255, decay=246, sustain=0, release=235,
    )


# ── one shared definition ──────────────────────────────────────────────────

def test_filler_is_recognised():
    assert is_filler_instrument(_filler())
    assert not is_filler_instrument(_real())


def test_a_square_with_any_envelope_is_not_filler():
    """Only the bare padding entry counts — a real square must not be hidden."""
    tweaked = _filler()
    tweaked.attack = 7
    assert not is_filler_instrument(tweaked)

    tweaked = _filler()
    tweaked.duty_cycle = 0
    assert not is_filler_instrument(tweaked)

    tweaked = _filler()
    tweaked.sweep = 3
    assert not is_filler_instrument(tweaked)


def test_instrument_property_matches_the_function():
    assert _filler().is_filler is True
    assert _real().is_filler is False


def test_every_module_shares_the_one_definition():
    """The predicate used to be copy-pasted into four files and drift was a
    matter of time. Each caller must now resolve to the same object."""
    from core.sound import gm_voicegroup
    import ui.voicegroups_tab as vg_tab
    import ui.instruments_tab as inst_tab
    assert gm_voicegroup.is_filler_instrument is is_filler_instrument
    assert vg_tab._is_filler_instrument is is_filler_instrument
    assert inst_tab._is_filler_instrument is is_filler_instrument


# ── picking a slot that actually plays something ───────────────────────────

def test_nearest_real_slot_keeps_a_good_slot():
    bank = [_filler(i) for i in range(16)]
    bank[5] = _real(5)
    assert nearest_real_slot(bank, 5) == 5


def test_nearest_real_slot_moves_off_filler():
    bank = [_filler(i) for i in range(16)]
    bank[4] = _real(4)
    bank[12] = _real(12)
    assert nearest_real_slot(bank, 6) == 4      # 4 is two away, 12 is six
    assert nearest_real_slot(bank, 11) == 12


def test_nearest_real_slot_prefers_the_lower_index_on_a_tie():
    bank = [_filler(i) for i in range(16)]
    bank[2] = _real(2)
    bank[8] = _real(8)
    assert nearest_real_slot(bank, 5) == 2


def test_nearest_real_slot_gives_up_on_an_all_filler_bank():
    """A wizard must be able to tell the difference between 'move it' and
    'this voicegroup has nothing to move it to'."""
    assert nearest_real_slot([_filler(i) for i in range(16)], 3) is None
    assert nearest_real_slot([], 3) is None


def test_nearest_real_slot_handles_out_of_range():
    bank = [_filler(i) for i in range(8)]
    bank[7] = _real(7)
    assert nearest_real_slot(bank, 120) == 7


def test_count_real_instruments():
    bank = [_filler(i) for i in range(10)]
    bank[1] = _real(1)
    bank[9] = _real(9)
    assert count_real_instruments(bank) == 2
    assert count_real_instruments([]) == 0
    assert count_real_instruments(None) == 0


# ── the validator reports it on every path ─────────────────────────────────

class _Cmd:
    def __init__(self, cmd, value):
        self.cmd = cmd
        self.value = value


class _Track:
    def __init__(self, commands):
        self.commands = commands


class _Song:
    def __init__(self, voicegroup, voices):
        self.label = 'mus_test'
        self.voicegroup = voicegroup
        self.tracks = [_Track([_Cmd('VOICE', v)]) for v in voices]


class _VgData:
    """Minimal stand-in for VoicegroupData with one stub voicegroup."""

    def __init__(self, instruments):
        self._instruments = instruments

    def get_voicegroup(self, name):
        return types.SimpleNamespace(instruments=self._instruments)

    def get_instrument_overflow(self, name, slot):
        if 0 <= slot < len(self._instruments):
            return self._instruments[slot]
        return None


def test_validator_warns_about_a_filler_voice():
    bank = [_filler(i) for i in range(128)]
    bank[1] = _real(1)
    song = _Song('voicegroup014', [46, 120, 123])
    warnings = validate_song_warnings(song, None, vg_data=_VgData(bank))
    assert len(warnings) == 3
    for w in warnings:
        assert 'EMPTY slot' in w


def test_validator_is_quiet_about_a_real_voice():
    bank = [_real(i) for i in range(128)]
    song = _Song('voicegroup013', [46, 120, 123])
    assert validate_song_warnings(song, None, vg_data=_VgData(bank)) == []


def test_filler_warning_does_not_block_a_save():
    """It is legal M4A, so it must stay a WARNING — a blocking error here
    would refuse songs that assemble and run perfectly well."""
    from core.sound.song_validator import validate_song_voices
    bank = [_filler(i) for i in range(128)]
    song = _Song('voicegroup014', [46])
    assert validate_song_voices(song, None, vg_data=_VgData(bank)) == []
