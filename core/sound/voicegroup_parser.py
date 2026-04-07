"""
Parser for voice_groups.inc — the instrument bank definitions.

Each voicegroup contains 128 instrument slots. Instruments can be:
- DirectSound (PCM sample playback)
- Square wave (2 hardware variants, each with an alt)
- Programmable wave (custom 16-byte waveform)
- Noise
- Keysplit (routes note ranges to other voicegroups)

Also parses keysplit_tables.inc for note-range routing.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Optional

from core.sound.sound_constants import (
    VOICE_MACRO_TYPES, DIRECTSOUND_VOICE_TYPES, PROGRAMMABLE_WAVE_VOICE_TYPES,
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Instrument:
    """One instrument slot within a voicegroup."""
    slot_index: int              # 0-127 within the voicegroup
    voice_type: str              # macro name e.g. 'voice_directsound'
    type_byte: int               # numeric type (0x00, 0x01, etc.)
    base_midi_key: int = 60
    pan: int = 0

    # DirectSound specific
    sample_label: Optional[str] = None  # e.g. 'DirectSoundWaveData_sc88pro_organ2'

    # Square wave specific
    sweep: int = 0               # square_1 only
    duty_cycle: int = 0          # 0-3

    # Programmable wave specific
    wave_label: Optional[str] = None  # e.g. 'ProgrammableWaveData_1'

    # Noise specific
    period: int = 0

    # ADSR envelope
    attack: int = 0
    decay: int = 0
    sustain: int = 0
    release: int = 0

    # Keysplit specific
    target_voicegroup: Optional[str] = None  # e.g. 'voicegroup001'
    keysplit_table: Optional[str] = None     # e.g. 'KeySplitTable1'

    @property
    def is_directsound(self) -> bool:
        return self.voice_type in DIRECTSOUND_VOICE_TYPES

    @property
    def is_square(self) -> bool:
        return 'square' in self.voice_type

    @property
    def is_programmable_wave(self) -> bool:
        return self.voice_type in PROGRAMMABLE_WAVE_VOICE_TYPES

    @property
    def is_noise(self) -> bool:
        return 'noise' in self.voice_type

    @property
    def is_keysplit(self) -> bool:
        return 'keysplit' in self.voice_type

    @property
    def friendly_name(self) -> str:
        """Human-readable name for the instrument."""
        if self.is_directsound and self.sample_label:
            # DirectSoundWaveData_sc88pro_organ2 -> SC88Pro Organ 2
            name = self.sample_label
            if name.startswith('DirectSoundWaveData_'):
                name = name[len('DirectSoundWaveData_'):]
            return name.replace('_', ' ').title()
        if self.is_square:
            duty_names = {0: '12.5%', 1: '25%', 2: '50%', 3: '75%'}
            duty = duty_names.get(self.duty_cycle, f'{self.duty_cycle}')
            variant = '1' if 'square_1' in self.voice_type else '2'
            return f'Square {variant} ({duty})'
        if self.is_programmable_wave and self.wave_label:
            num = self.wave_label.split('_')[-1] if '_' in self.wave_label else '?'
            return f'Prog. Wave {num}'
        if self.is_noise:
            return f'Noise (period={self.period})'
        if self.is_keysplit:
            target = self.target_voicegroup or '?'
            if self.keysplit_table:
                return f'Keysplit ->{target} ({self.keysplit_table})'
            return f'Keysplit All ->{target}'
        return self.voice_type


@dataclass
class Voicegroup:
    """A bank of 128 instruments."""
    name: str                    # e.g. 'voicegroup141'
    number: int                  # e.g. 141
    instruments: list[Instrument] = field(default_factory=list)

    def get_instrument(self, slot: int) -> Optional[Instrument]:
        if 0 <= slot < len(self.instruments):
            return self.instruments[slot]
        return None

    @property
    def used_sample_labels(self) -> set[str]:
        """All DirectSoundWaveData labels referenced by this voicegroup."""
        return {
            inst.sample_label for inst in self.instruments
            if inst.sample_label
        }

    @property
    def used_wave_labels(self) -> set[str]:
        """All ProgrammableWaveData labels referenced."""
        return {
            inst.wave_label for inst in self.instruments
            if inst.wave_label
        }


@dataclass
class KeySplitTable:
    """Maps MIDI note numbers to instrument indices within a voicegroup."""
    name: str                    # e.g. 'KeySplitTable1'
    offset: int                  # the base note number (e.g. 36)
    entries: list[int] = field(default_factory=list)  # instrument index per note

    def get_instrument_index(self, midi_note: int) -> int:
        """Which instrument slot handles this MIDI note."""
        idx = midi_note - self.offset
        if 0 <= idx < len(self.entries):
            return self.entries[idx]
        return 0


@dataclass
class VoicegroupData:
    """All parsed voicegroup data from a project."""
    voicegroups: dict[str, Voicegroup] = field(default_factory=dict)  # name -> Voicegroup
    keysplit_tables: dict[str, KeySplitTable] = field(default_factory=dict)

    def get_voicegroup(self, name: str) -> Optional[Voicegroup]:
        return self.voicegroups.get(name)

    def get_voicegroup_by_number(self, num: int) -> Optional[Voicegroup]:
        return self.voicegroups.get(f'voicegroup{num:03d}')

    def get_instrument_overflow(self, vg_name: str, slot: int) -> Optional[Instrument]:
        """Get instrument with GBA-style overflow into the next voicegroup.

        On GBA hardware, voicegroups are contiguous in memory. keysplit_all
        uses the MIDI note as a direct index, which can overflow past the
        end of the target voicegroup into the next one in memory (e.g.
        voicegroup001[40] -> voicegroup002[11] when vg001 has 29 slots).
        """
        vg = self.get_voicegroup(vg_name)
        if vg is None:
            return None

        # Try direct lookup first
        inst = vg.get_instrument(slot)
        if inst is not None:
            return inst

        # Overflow: walk through subsequent voicegroups in numeric order
        remaining = slot - len(vg.instruments)
        nums = self.voicegroup_numbers
        try:
            start_idx = nums.index(vg.number) + 1
        except ValueError:
            return None

        for i in range(start_idx, len(nums)):
            next_vg = self.get_voicegroup_by_number(nums[i])
            if next_vg is None:
                continue
            if remaining < len(next_vg.instruments):
                return next_vg.instruments[remaining]
            remaining -= len(next_vg.instruments)

        return None

    @property
    def voicegroup_numbers(self) -> list[int]:
        """Sorted list of all voicegroup numbers."""
        return sorted(vg.number for vg in self.voicegroups.values())


# ---------------------------------------------------------------------------
# Parser: voice_groups.inc
# ---------------------------------------------------------------------------

_RE_VG_LABEL = re.compile(r'^(voicegroup\d+)::')
_RE_VOICE_LINE = re.compile(r'(voice_\w+)\s+(.*)')


def _parse_voice_args(voice_type: str, args_str: str) -> Instrument:
    """Parse a single voice macro line into an Instrument."""
    # Split args on commas, strip whitespace
    args = [a.strip() for a in args_str.split(',') if a.strip()]
    inst = Instrument(
        slot_index=0,  # will be set by caller
        voice_type=voice_type,
        type_byte=VOICE_MACRO_TYPES.get(voice_type, 0),
    )

    if voice_type == 'voice_keysplit_all':
        # voice_keysplit_all voicegroup_pointer
        if args:
            inst.target_voicegroup = args[0]
        return inst

    if voice_type == 'voice_keysplit':
        # voice_keysplit voicegroup_pointer, keysplit_table
        if len(args) >= 1:
            inst.target_voicegroup = args[0]
        if len(args) >= 2:
            inst.keysplit_table = args[1]
        return inst

    if voice_type in DIRECTSOUND_VOICE_TYPES:
        # voice_directsound base_midi_key, pan, sample_data_pointer, attack, decay, sustain, release
        if len(args) >= 1:
            inst.base_midi_key = _safe_int(args[0])
        if len(args) >= 2:
            inst.pan = _safe_int(args[1])
        if len(args) >= 3:
            inst.sample_label = args[2]
        if len(args) >= 4:
            inst.attack = _safe_int(args[3])
        if len(args) >= 5:
            inst.decay = _safe_int(args[4])
        if len(args) >= 6:
            inst.sustain = _safe_int(args[5])
        if len(args) >= 7:
            inst.release = _safe_int(args[6])
        return inst

    if 'square_1' in voice_type:
        # voice_square_1 base_midi_key, pan, sweep, duty_cycle, attack, decay, sustain, release
        if len(args) >= 1:
            inst.base_midi_key = _safe_int(args[0])
        if len(args) >= 2:
            inst.pan = _safe_int(args[1])
        if len(args) >= 3:
            inst.sweep = _safe_int(args[2])
        if len(args) >= 4:
            inst.duty_cycle = _safe_int(args[3])
        if len(args) >= 5:
            inst.attack = _safe_int(args[4])
        if len(args) >= 6:
            inst.decay = _safe_int(args[5])
        if len(args) >= 7:
            inst.sustain = _safe_int(args[6])
        if len(args) >= 8:
            inst.release = _safe_int(args[7])
        return inst

    if 'square_2' in voice_type:
        # voice_square_2 base_midi_key, pan, duty_cycle, attack, decay, sustain, release
        if len(args) >= 1:
            inst.base_midi_key = _safe_int(args[0])
        if len(args) >= 2:
            inst.pan = _safe_int(args[1])
        if len(args) >= 3:
            inst.duty_cycle = _safe_int(args[2])
        if len(args) >= 4:
            inst.attack = _safe_int(args[3])
        if len(args) >= 5:
            inst.decay = _safe_int(args[4])
        if len(args) >= 6:
            inst.sustain = _safe_int(args[5])
        if len(args) >= 7:
            inst.release = _safe_int(args[6])
        return inst

    if 'programmable_wave' in voice_type:
        # voice_programmable_wave base_midi_key, pan, wave_pointer, attack, decay, sustain, release
        if len(args) >= 1:
            inst.base_midi_key = _safe_int(args[0])
        if len(args) >= 2:
            inst.pan = _safe_int(args[1])
        if len(args) >= 3:
            inst.wave_label = args[2]
        if len(args) >= 4:
            inst.attack = _safe_int(args[3])
        if len(args) >= 5:
            inst.decay = _safe_int(args[4])
        if len(args) >= 6:
            inst.sustain = _safe_int(args[5])
        if len(args) >= 7:
            inst.release = _safe_int(args[6])
        return inst

    if 'noise' in voice_type:
        # voice_noise base_midi_key, pan, period, attack, decay, sustain, release
        if len(args) >= 1:
            inst.base_midi_key = _safe_int(args[0])
        if len(args) >= 2:
            inst.pan = _safe_int(args[1])
        if len(args) >= 3:
            inst.period = _safe_int(args[2])
        if len(args) >= 4:
            inst.attack = _safe_int(args[3])
        if len(args) >= 5:
            inst.decay = _safe_int(args[4])
        if len(args) >= 6:
            inst.sustain = _safe_int(args[5])
        if len(args) >= 7:
            inst.release = _safe_int(args[6])
        return inst

    return inst


def _safe_int(s: str) -> int:
    """Parse an integer, handling hex."""
    s = s.strip()
    try:
        if s.startswith('0x') or s.startswith('0X'):
            return int(s, 16)
        return int(s)
    except ValueError:
        return 0


def parse_voice_groups(filepath: str) -> dict[str, Voicegroup]:
    """Parse voice_groups.inc into {name: Voicegroup}."""
    voicegroups: dict[str, Voicegroup] = {}
    current_vg: Optional[Voicegroup] = None
    slot_index = 0

    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            stripped = line.strip()
            # Remove comments
            code = stripped.split('@')[0].strip()
            if not code:
                continue

            # Check for voicegroup label
            m = _RE_VG_LABEL.match(code)
            if m:
                vg_name = m.group(1)
                # Extract number from name
                num_match = re.search(r'(\d+)$', vg_name)
                vg_num = int(num_match.group(1)) if num_match else 0

                current_vg = Voicegroup(name=vg_name, number=vg_num)
                voicegroups[vg_name] = current_vg
                slot_index = 0
                continue

            # Check for voice macro
            m = _RE_VOICE_LINE.match(code)
            if m and current_vg is not None:
                voice_type = m.group(1)
                args_str = m.group(2)

                if voice_type in VOICE_MACRO_TYPES:
                    inst = _parse_voice_args(voice_type, args_str)
                    inst.slot_index = slot_index
                    current_vg.instruments.append(inst)
                    slot_index += 1

    return voicegroups


# ---------------------------------------------------------------------------
# Parser: keysplit_tables.inc
# ---------------------------------------------------------------------------

_RE_KEYSPLIT_SET = re.compile(r'\.set\s+(KeySplitTable\d+)\s*,\s*\.\s*-\s*(\d+)')
_RE_BYTE_VAL = re.compile(r'\.byte\s+(\d+)')


def parse_keysplit_tables(filepath: str) -> dict[str, KeySplitTable]:
    """Parse keysplit_tables.inc into {name: KeySplitTable}."""
    tables: dict[str, KeySplitTable] = {}
    current_table: Optional[KeySplitTable] = None

    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            stripped = line.strip()
            code = stripped.split('@')[0].strip()
            if not code:
                continue

            # New table definition
            m = _RE_KEYSPLIT_SET.match(code)
            if m:
                # Save previous table
                name = m.group(1)
                offset = int(m.group(2))
                current_table = KeySplitTable(name=name, offset=offset)
                tables[name] = current_table
                continue

            # Byte entries
            m = _RE_BYTE_VAL.match(code)
            if m and current_table is not None:
                current_table.entries.append(int(m.group(1)))

    return tables


# ---------------------------------------------------------------------------
# Main loader
# ---------------------------------------------------------------------------

def load_voicegroup_data(project_root: str) -> VoicegroupData:
    """Load all voicegroup and keysplit data from a project."""
    vg_path = os.path.join(project_root, 'sound', 'voice_groups.inc')
    ks_path = os.path.join(project_root, 'sound', 'keysplit_tables.inc')

    data = VoicegroupData()

    if os.path.isfile(vg_path):
        data.voicegroups = parse_voice_groups(vg_path)

    if os.path.isfile(ks_path):
        data.keysplit_tables = parse_keysplit_tables(ks_path)

    return data
