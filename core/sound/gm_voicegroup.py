"""GM (General MIDI) Voicegroup Generator.

Scans all existing voicegroups in a project, catalogs every real DirectSound
instrument, and builds a new 128-slot voicegroup that maps as many GM program
numbers as possible to actual samples.

The GBA pokefirered engine has ~89 unique DirectSound samples (SC-88 Pro,
SD-90, Trinity, ethnic instruments, etc.). This module maps them to the
closest GM program slot by name, then fills remaining slots with the best
available fallback (square wave or the closest existing sample).

Usage:
    from core.sound.gm_voicegroup import generate_gm_voicegroup
    new_vg = generate_gm_voicegroup(voicegroup_data)
    # new_vg is a Voicegroup with 128 slots, ready to add to voicegroup_data
"""

from __future__ import annotations

import re
from typing import Optional

from core.sound.voicegroup_parser import Instrument, Voicegroup, VoicegroupData
from core.sound.sound_constants import VOICE_MACRO_TYPES


# ---------------------------------------------------------------------------
# GM Program Number -> Name mapping (General MIDI Level 1)
# ---------------------------------------------------------------------------

GM_PROGRAM_NAMES = [
    # 0-7: Piano
    "Acoustic Grand Piano", "Bright Acoustic Piano", "Electric Grand Piano",
    "Honky-tonk Piano", "Electric Piano 1", "Electric Piano 2",
    "Harpsichord", "Clavinet",
    # 8-15: Chromatic Percussion
    "Celesta", "Glockenspiel", "Music Box", "Vibraphone",
    "Marimba", "Xylophone", "Tubular Bells", "Dulcimer",
    # 16-23: Organ
    "Drawbar Organ", "Percussive Organ", "Rock Organ", "Church Organ",
    "Reed Organ", "Accordion", "Harmonica", "Tango Accordion",
    # 24-31: Guitar
    "Acoustic Guitar (nylon)", "Acoustic Guitar (steel)",
    "Electric Guitar (jazz)", "Electric Guitar (clean)",
    "Electric Guitar (muted)", "Overdriven Guitar",
    "Distortion Guitar", "Guitar Harmonics",
    # 32-39: Bass
    "Acoustic Bass", "Electric Bass (finger)", "Electric Bass (pick)",
    "Fretless Bass", "Slap Bass 1", "Slap Bass 2",
    "Synth Bass 1", "Synth Bass 2",
    # 40-47: Strings
    "Violin", "Viola", "Cello", "Contrabass",
    "Tremolo Strings", "Pizzicato Strings", "Orchestral Harp",
    "Timpani",
    # 48-55: Ensemble
    "String Ensemble 1", "String Ensemble 2", "Synth Strings 1",
    "Synth Strings 2", "Choir Aahs", "Voice Oohs",
    "Synth Choir", "Orchestra Hit",
    # 56-63: Brass
    "Trumpet", "Trombone", "Tuba", "Muted Trumpet",
    "French Horn", "Brass Section", "Synth Brass 1", "Synth Brass 2",
    # 64-71: Reed
    "Soprano Sax", "Alto Sax", "Tenor Sax", "Baritone Sax",
    "Oboe", "English Horn", "Bassoon", "Clarinet",
    # 72-79: Pipe
    "Piccolo", "Flute", "Recorder", "Pan Flute",
    "Blown Bottle", "Shakuhachi", "Whistle", "Ocarina",
    # 80-87: Synth Lead
    "Lead 1 (square)", "Lead 2 (sawtooth)", "Lead 3 (calliope)",
    "Lead 4 (chiff)", "Lead 5 (charang)", "Lead 6 (voice)",
    "Lead 7 (fifths)", "Lead 8 (bass+lead)",
    # 88-95: Synth Pad
    "Pad 1 (new age)", "Pad 2 (warm)", "Pad 3 (polysynth)",
    "Pad 4 (choir)", "Pad 5 (bowed)", "Pad 6 (metallic)",
    "Pad 7 (halo)", "Pad 8 (sweep)",
    # 96-103: Synth Effects
    "FX 1 (rain)", "FX 2 (soundtrack)", "FX 3 (crystal)",
    "FX 4 (atmosphere)", "FX 5 (brightness)", "FX 6 (goblins)",
    "FX 7 (echoes)", "FX 8 (sci-fi)",
    # 104-111: Ethnic
    "Sitar", "Banjo", "Shamisen", "Koto",
    "Kalimba", "Bagpipe", "Fiddle", "Shanai",
    # 112-119: Percussive
    "Tinkle Bell", "Agogo", "Steel Drums", "Woodblock",
    "Taiko Drum", "Melodic Tom", "Synth Drum", "Reverse Cymbal",
    # 120-127: Sound Effects
    "Guitar Fret Noise", "Breath Noise", "Seashore", "Bird Tweet",
    "Telephone Ring", "Helicopter", "Applause", "Gunshot",
]


# ---------------------------------------------------------------------------
# Sample-to-GM mapping table
# ---------------------------------------------------------------------------
# Maps DirectSound sample labels to the best-fit GM program number(s).
# Built by matching SC-88 Pro / SD-90 sample names to their GM equivalents.

_SAMPLE_TO_GM: dict[str, list[int]] = {
    # Piano
    'DirectSoundWaveData_sc88pro_piano1_48': [0, 1, 2, 3],
    'DirectSoundWaveData_sc88pro_piano1_60': [0, 1, 2, 3],
    'DirectSoundWaveData_sc88pro_piano1_72': [0, 1, 2, 3],
    'DirectSoundWaveData_sc88pro_piano1_84': [0, 1, 2, 3],
    'DirectSoundWaveData_steinway_b_piano': [0, 1],
    'DirectSoundWaveData_sd90_classical_detuned_ep1_low': [4],
    'DirectSoundWaveData_sd90_classical_detuned_ep1_high': [5],

    # Chromatic Percussion
    'DirectSoundWaveData_sc88pro_glockenspiel': [9],
    'DirectSoundWaveData_sc88pro_xylophone': [13],
    'DirectSoundWaveData_sc88pro_tubular_bell': [14],

    # Organ
    'DirectSoundWaveData_sc88pro_organ2': [16, 17, 18, 19],
    'DirectSoundWaveData_sc88pro_accordion': [21, 23],
    'DirectSoundWaveData_sc88pro_accordion_duplicate': [21],

    # Guitar
    'DirectSoundWaveData_sc88pro_nylon_str_guitar': [24, 25],
    'DirectSoundWaveData_sd90_classical_overdrive_guitar': [29],
    'DirectSoundWaveData_sd90_classical_distortion_guitar_low': [30],
    'DirectSoundWaveData_sd90_classical_distortion_guitar_high': [30],
    'DirectSoundWaveData_unused_guitar_separates_power_chord': [29, 30],

    # Bass
    'DirectSoundWaveData_sc88pro_fingered_bass': [33],
    'DirectSoundWaveData_sc88pro_fretless_bass': [35],
    'DirectSoundWaveData_sc88pro_slap_bass': [36, 37],
    'DirectSoundWaveData_sc88pro_synth_bass': [38, 39],
    'DirectSoundWaveData_trinity_30303_mega_bass': [38, 39],

    # Strings
    'DirectSoundWaveData_sc88pro_string_ensemble_60': [48, 49, 44],
    'DirectSoundWaveData_sc88pro_string_ensemble_72': [48, 49, 44],
    'DirectSoundWaveData_sc88pro_string_ensemble_84': [48, 49, 44],
    'DirectSoundWaveData_sc88pro_pizzicato_strings': [45],
    'DirectSoundWaveData_sc88pro_harp': [46],
    'DirectSoundWaveData_sc88pro_timpani': [47],

    # Choir / Ensemble
    'DirectSoundWaveData_classical_choir_voice_ahhs': [52, 53],

    # Brass
    'DirectSoundWaveData_sc88pro_trumpet_60': [56, 59],
    'DirectSoundWaveData_sc88pro_trumpet_72': [56, 59],
    'DirectSoundWaveData_sc88pro_trumpet_84': [56, 59],
    'DirectSoundWaveData_sc88pro_tuba_39': [58],
    'DirectSoundWaveData_sc88pro_tuba_51': [58],
    'DirectSoundWaveData_sc88pro_french_horn_60': [60, 61],
    'DirectSoundWaveData_sc88pro_french_horn_72': [60, 61],

    # Reed
    'DirectSoundWaveData_sd90_classical_oboe': [68],
    'DirectSoundWaveData_unused_sd90_oboe': [68],

    # Pipe
    'DirectSoundWaveData_sc88pro_flute': [73, 72, 74],
    'DirectSoundWaveData_sd90_enhanced_delay_shaku': [77],
    'DirectSoundWaveData_sd90_classical_whistle': [78],

    # Synth Lead
    'DirectSoundWaveData_sc88pro_square_wave': [80],

    # Synth Pad / Effects
    'DirectSoundWaveData_sc88pro_wind': [122],
    'DirectSoundWaveData_sc88pro_bubbles': [98],

    # Ethnic percussion
    'DirectSoundWaveData_sc88pro_taiko': [116],
    'DirectSoundWaveData_ethnic_flavours_atarigane': [113],
    'DirectSoundWaveData_ethnic_flavours_hyoushigi': [115],
    'DirectSoundWaveData_ethnic_flavours_kotsuzumi': [116],
    'DirectSoundWaveData_ethnic_flavours_ohtsuzumi': [116],

    # Bells / Percussion
    'DirectSoundWaveData_sc88pro_jingle_bell': [112],
    'DirectSoundWaveData_bicycle_bell': [112],
    'DirectSoundWaveData_sc88pro_tambourine': [113],

    # Misc
    'DirectSoundWaveData_trinity_big_boned': [87],
    'DirectSoundWaveData_sd90_special_scream_drive': [85],
    'DirectSoundWaveData_register_noise': [121],
}


# ---------------------------------------------------------------------------
# Filler detection
# ---------------------------------------------------------------------------

def _is_filler(inst: Instrument) -> bool:
    """Check if an instrument is a default filler (unused placeholder)."""
    return (
        inst.voice_type == 'voice_square_1'
        and inst.duty_cycle == 2
        and inst.attack == 0
        and inst.decay == 0
        and inst.sustain == 15
        and inst.release == 0
        and inst.base_midi_key == 60
        and inst.pan == 0
        and inst.sweep == 0
    )


def _make_filler(slot: int) -> Instrument:
    """Create a default filler instrument for an empty slot."""
    return Instrument(
        slot_index=slot,
        voice_type='voice_square_1',
        type_byte=VOICE_MACRO_TYPES.get('voice_square_1', 0x01),
        base_midi_key=60,
        pan=0,
        sweep=0,
        duty_cycle=2,
        attack=0,
        decay=0,
        sustain=15,
        release=0,
    )


def _make_square_fallback(slot: int, duty: int = 2) -> Instrument:
    """Create a usable square wave instrument as a fallback."""
    return Instrument(
        slot_index=slot,
        voice_type='voice_square_1',
        type_byte=VOICE_MACRO_TYPES.get('voice_square_1', 0x01),
        base_midi_key=60,
        pan=0,
        sweep=0,
        duty_cycle=duty,
        attack=255,
        decay=0,
        sustain=255,
        release=165,
    )


# ---------------------------------------------------------------------------
# Catalog builder
# ---------------------------------------------------------------------------

def catalog_real_instruments(
    voicegroup_data: VoicegroupData,
) -> dict[str, Instrument]:
    """Scan all voicegroups and return the best instrument definition for
    each unique DirectSound sample label.

    "Best" means: prefer instruments with non-zero ADSR, then pick the
    first occurrence.
    """
    catalog: dict[str, Instrument] = {}

    for vg_name in sorted(voicegroup_data.voicegroups):
        vg = voicegroup_data.voicegroups[vg_name]
        for inst in vg.instruments:
            if not inst.is_directsound or not inst.sample_label:
                continue
            label = inst.sample_label
            if label not in catalog:
                catalog[label] = inst
            else:
                # Prefer instruments with fuller ADSR (non-zero sustain)
                existing = catalog[label]
                if existing.sustain == 0 and inst.sustain > 0:
                    catalog[label] = inst

    return catalog


# ---------------------------------------------------------------------------
# GM voicegroup generator
# ---------------------------------------------------------------------------

def _catalog_all_instruments(
    voicegroup_data: VoicegroupData,
) -> list[Instrument]:
    """Collect every unique non-filler instrument from all voicegroups.

    Includes DirectSound, square waves, programmable waves, noise — anything
    that isn't a default filler placeholder. Deduplicates by a signature
    derived from the instrument's key properties.
    """
    seen: dict[str, Instrument] = {}

    for vg_name in sorted(voicegroup_data.voicegroups):
        vg = voicegroup_data.voicegroups[vg_name]
        for inst in vg.instruments:
            if _is_filler(inst):
                continue

            # Build a signature to detect duplicates
            if inst.is_directsound and inst.sample_label:
                sig = f"ds:{inst.sample_label}"
            elif inst.is_square:
                sig = f"sq:{inst.voice_type}:d{inst.duty_cycle}:a{inst.attack}:d{inst.decay}:s{inst.sustain}:r{inst.release}"
            elif inst.is_programmable_wave and inst.wave_label:
                sig = f"pw:{inst.wave_label}"
            elif inst.is_noise:
                sig = f"noise:a{inst.attack}:d{inst.decay}:s{inst.sustain}:r{inst.release}"
            else:
                # Keysplit or unknown — skip for GM voicegroup
                continue

            if sig not in seen:
                seen[sig] = inst
            else:
                # Prefer instruments with fuller ADSR
                existing = seen[sig]
                if existing.sustain == 0 and inst.sustain > 0:
                    seen[sig] = inst

    return list(seen.values())


def generate_gm_voicegroup(
    voicegroup_data: VoicegroupData,
    voicegroup_number: Optional[int] = None,
) -> Voicegroup:
    """Build a 128-slot GM voicegroup using available instruments.

    Maps real DirectSound samples to their closest GM program numbers.
    Remaining slots get unique non-DirectSound instruments (square waves,
    programmable waves, noise) if available, then filler for the rest.
    No duplicate instruments — each unique instrument appears at most once.

    Args:
        voicegroup_data: All parsed voicegroup data from the project.
        voicegroup_number: Number for the new voicegroup (auto-picks next
            available if None).

    Returns:
        A new Voicegroup object ready to be added to voicegroup_data.
    """
    # Find next available voicegroup number
    if voicegroup_number is None:
        used = set()
        for name in voicegroup_data.voicegroups:
            m = re.search(r'(\d+)', name)
            if m:
                used.add(int(m.group(1)))
        voicegroup_number = 0
        while voicegroup_number in used:
            voicegroup_number += 1

    vg_name = f'voicegroup{voicegroup_number:03d}'

    # Catalog all real DirectSound samples
    catalog = catalog_real_instruments(voicegroup_data)

    # Also catalog ALL unique non-filler instruments (square, prog wave, etc.)
    all_unique = _catalog_all_instruments(voicegroup_data)

    # Build reverse map: GM slot -> best sample label (DirectSound only)
    gm_slots: list[Optional[Instrument]] = [None] * 128

    # Priority: assign each sample to its primary (first) GM slot
    # then secondary slots if still empty
    for label, gm_programs in _SAMPLE_TO_GM.items():
        if label not in catalog:
            continue
        for prog in gm_programs:
            if gm_slots[prog] is None:
                gm_slots[prog] = catalog[label]

    # For keysplit instruments (piano, strings, brass, etc.), pick the
    # best single sample for the GM slot
    _prefer = {
        0: 'DirectSoundWaveData_sc88pro_piano1_60',
        1: 'DirectSoundWaveData_steinway_b_piano',
        48: 'DirectSoundWaveData_sc88pro_string_ensemble_60',
        56: 'DirectSoundWaveData_sc88pro_trumpet_60',
        58: 'DirectSoundWaveData_sc88pro_tuba_51',
        60: 'DirectSoundWaveData_sc88pro_french_horn_60',
    }
    for slot, label in _prefer.items():
        if label in catalog:
            gm_slots[slot] = catalog[label]

    # Collect non-DirectSound unique instruments to fill appropriate GM slots
    # Square waves -> Lead 1 (slot 80), synth slots
    # Programmable waves -> synth pad slots
    # Noise -> percussion slots
    non_ds_instruments: list[Instrument] = [
        inst for inst in all_unique
        if not (inst.is_directsound and inst.sample_label)
    ]

    # Place non-DirectSound instruments in suitable empty GM slots
    # Group by type for better placement
    square_insts = [i for i in non_ds_instruments if i.is_square]
    wave_insts = [i for i in non_ds_instruments if i.is_programmable_wave]
    noise_insts = [i for i in non_ds_instruments if i.is_noise]

    # Synth lead slots (80-87) for square waves
    sq_iter = iter(square_insts)
    for slot in range(80, 88):
        if gm_slots[slot] is None:
            inst = next(sq_iter, None)
            if inst:
                gm_slots[slot] = inst

    # Synth pad slots (88-95) for programmable waves
    pw_iter = iter(wave_insts)
    for slot in range(88, 96):
        if gm_slots[slot] is None:
            inst = next(pw_iter, None)
            if inst:
                gm_slots[slot] = inst

    # Percussion slots (112-119) for noise instruments
    n_iter = iter(noise_insts)
    for slot in range(112, 120):
        if gm_slots[slot] is None:
            inst = next(n_iter, None)
            if inst:
                gm_slots[slot] = inst

    # Any remaining unique instruments go in the first empty slots
    placed_ids = {id(inst) for inst in gm_slots if inst is not None}
    remaining = [i for i in non_ds_instruments if id(i) not in placed_ids]
    rem_iter = iter(remaining)
    for slot in range(128):
        if gm_slots[slot] is None:
            inst = next(rem_iter, None)
            if inst:
                gm_slots[slot] = inst

    # Find the highest slot that has a real instrument — no need to pad
    # all the way to 128 if nothing lives up there
    highest_slot = -1
    for slot in range(128):
        if gm_slots[slot] is not None:
            highest_slot = slot
    num_slots = highest_slot + 1 if highest_slot >= 0 else 0

    # Build the voicegroup — only up to the last real instrument
    instruments: list[Instrument] = []

    for slot in range(num_slots):
        src = gm_slots[slot]
        if src is not None:
            # Clone the instrument into this slot
            inst = Instrument(
                slot_index=slot,
                voice_type=src.voice_type,
                type_byte=src.type_byte,
                base_midi_key=src.base_midi_key,
                pan=src.pan,
                sweep=src.sweep,
                duty_cycle=src.duty_cycle,
                sample_label=src.sample_label,
                wave_label=src.wave_label,
                attack=src.attack,
                decay=src.decay,
                sustain=src.sustain,
                release=src.release,
            )
            instruments.append(inst)
        else:
            # Gap between real instruments — silent filler placeholder
            instruments.append(_make_filler(slot))

    vg = Voicegroup(
        name=vg_name,
        number=voicegroup_number,
        instruments=instruments,
    )

    return vg


def get_gm_coverage_report(
    voicegroup_data: VoicegroupData,
) -> dict:
    """Analyze how many GM slots can be filled with real instruments.

    Returns a dict with:
        'total_ds_samples': int — unique DirectSound samples found
        'total_unique': int — all unique non-filler instruments
        'mapped_slots': int — GM slots filled with real instruments
        'filler_slots': int — GM slots that will be silent filler
        'slot_details': list of (slot, gm_name, instrument_description_or_None)
    """
    catalog = catalog_real_instruments(voicegroup_data)
    all_unique = _catalog_all_instruments(voicegroup_data)

    # Quick count: just check how many GM slots would be filled
    gm_slots: list[Optional[str]] = [None] * 128

    for label, gm_programs in _SAMPLE_TO_GM.items():
        if label not in catalog:
            continue
        for prog in gm_programs:
            if gm_slots[prog] is None:
                gm_slots[prog] = label

    details = []
    mapped = 0
    for i in range(128):
        label = gm_slots[i]
        if label:
            mapped += 1
        details.append((i, GM_PROGRAM_NAMES[i], label))

    return {
        'total_ds_samples': len(catalog),
        'total_unique': len(all_unique),
        'mapped_slots': mapped,
        'filler_slots': 128 - mapped,
        'slot_details': details,
    }
