"""
GBA M4A audio synthesis engine.

Renders instrument sounds in Python using the actual project samples and
synth definitions. Supports:
- DirectSound (PCM sample playback with pitch shifting and looping)
- Square wave synthesis (2 variants with duty cycle)
- Programmable wave synthesis (16-byte wavetable)
- Noise generation
- ADSR envelopes for all types
- Multi-track mixing with volume, panning, and reverb
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from core.sound.sound_constants import C_V
from core.sound.voicegroup_parser import Instrument, Voicegroup, VoicegroupData
from core.sound.sample_loader import SampleData, DirectSoundSample, ProgrammableWaveSample


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OUTPUT_SAMPLE_RATE = 32768  # GBA native mixing rate (close to actual hardware)
TICKS_PER_FRAME = 1         # M4A runs at ~60 fps, 1 tick per frame at tempo scale
GBA_FRAME_RATE = 59.7275    # GBA refresh rate in Hz

# ---------------------------------------------------------------------------
# GBA M4A pitch tables (from m4a_tables.c)
# ---------------------------------------------------------------------------

# gScaleTable: 180 bytes, maps MIDI key 0-179 to (shift << 4 | freq_index)
_GBA_SCALE_TABLE = [
    0xE0, 0xE1, 0xE2, 0xE3, 0xE4, 0xE5, 0xE6, 0xE7, 0xE8, 0xE9, 0xEA, 0xEB,
    0xD0, 0xD1, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7, 0xD8, 0xD9, 0xDA, 0xDB,
    0xC0, 0xC1, 0xC2, 0xC3, 0xC4, 0xC5, 0xC6, 0xC7, 0xC8, 0xC9, 0xCA, 0xCB,
    0xB0, 0xB1, 0xB2, 0xB3, 0xB4, 0xB5, 0xB6, 0xB7, 0xB8, 0xB9, 0xBA, 0xBB,
    0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0xA6, 0xA7, 0xA8, 0xA9, 0xAA, 0xAB,
    0x90, 0x91, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98, 0x99, 0x9A, 0x9B,
    0x80, 0x81, 0x82, 0x83, 0x84, 0x85, 0x86, 0x87, 0x88, 0x89, 0x8A, 0x8B,
    0x70, 0x71, 0x72, 0x73, 0x74, 0x75, 0x76, 0x77, 0x78, 0x79, 0x7A, 0x7B,
    0x60, 0x61, 0x62, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68, 0x69, 0x6A, 0x6B,
    0x50, 0x51, 0x52, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59, 0x5A, 0x5B,
    0x40, 0x41, 0x42, 0x43, 0x44, 0x45, 0x46, 0x47, 0x48, 0x49, 0x4A, 0x4B,
    0x30, 0x31, 0x32, 0x33, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39, 0x3A, 0x3B,
    0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x29, 0x2A, 0x2B,
    0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17, 0x18, 0x19, 0x1A, 0x1B,
    0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B,
]

# gFreqTable: 12 frequency multipliers (32-bit fixed-point, equal temperament)
_GBA_FREQ_TABLE = [
    2147483648, 2275179671, 2410468894, 2553802834,
    2705659852, 2866546760, 3037000500, 3217589947,
    3408917802, 3611622603, 3826380858, 4053909305,
]


def _midi_key_to_freq(wav_freq_raw: int, key: int) -> float:
    """Python port of the GBA's MidiKeyToFreq function.

    Args:
        wav_freq_raw: The raw freq field from the WaveData header (fixed-point).
        key: MIDI note number (0-127).

    Returns:
        Playback rate as a float (samples per output sample).
    """
    key = max(0, min(178, key))
    s1 = _GBA_SCALE_TABLE[key]
    val1 = _GBA_FREQ_TABLE[s1 & 0xF] >> (s1 >> 4)
    s2 = _GBA_SCALE_TABLE[key + 1]
    val2 = _GBA_FREQ_TABLE[s2 & 0xF] >> (s2 >> 4)
    # MidiKeyToFreq: umul3232H32(wav->freq, val1)
    # With fineAdjust=0, the result is just: (wav_freq * val1) >> 32
    playback_freq = (wav_freq_raw * val1) >> 32
    # Convert to a ratio against our output sample rate
    return playback_freq / OUTPUT_SAMPLE_RATE


# ---------------------------------------------------------------------------
# GBA ADSR envelope
# ---------------------------------------------------------------------------
# GBA ADSR values are 0-255. The envelope runs per-VBlank frame (~59.7 Hz).
# Attack: volume ramps 0->255 over (256 - attack) frames
# Decay: volume ramps from peak to sustain over (decay) frames
# Sustain: held at (sustain) level (0-255)
# Release: volume ramps from sustain to 0 over (256 - release) frames


# ---------------------------------------------------------------------------
# CGB ADSR conversion
# ---------------------------------------------------------------------------
# CGB channels (square, wave, noise) use a different ADSR scale:
#   Attack: 0 = instant, 1-7 = slower
#   Decay:  0-7
#   Sustain: 0-15 (15 = full volume)
#   Release: 0 = instant, 1-7 = slower

def _cgb_to_ds_adsr(
    attack: int, decay: int, sustain: int, release: int,
) -> tuple[int, int, int, int]:
    """Convert CGB (0-7/0-15) ADSR to DirectSound (0-255) ADSR scale."""
    ds_attack = 255 if attack == 0 else max(0, 255 - attack * 32)
    ds_decay = min(255, decay * 32)
    ds_sustain = min(255, sustain * 17)   # 0-15 -> 0-255
    ds_release = min(255, release * 32)
    return ds_attack, ds_decay, ds_sustain, ds_release


# ---------------------------------------------------------------------------
# ADSR Envelope Generator
# ---------------------------------------------------------------------------

def generate_adsr_envelope(
    attack: int,
    decay: int,
    sustain: int,
    release: int,
    note_on_samples: int,
    release_samples: int = 0,
    sample_rate: int = OUTPUT_SAMPLE_RATE,
) -> np.ndarray:
    """Generate an ADSR envelope matching GBA M4A behavior.

    GBA ADSR values are 0-255. The envelope updates per-VBlank (~59.7 Hz).
    - Attack 255 = instant, 0 = ~4.3 seconds (256 frames)
    - Decay: higher = slower decay to sustain
    - Sustain 255 = full volume, 0 = silent
    - Release 0 = instant, 255 = ~4.3 seconds
    """
    attack = max(0, min(255, attack))
    decay = max(0, min(255, decay))
    sustain = max(0, min(255, sustain))
    release = max(0, min(255, release))

    sustain_level = sustain / 255.0
    total_samples = note_on_samples + release_samples
    samples_per_frame = sample_rate / GBA_FRAME_RATE

    envelope = np.ones(total_samples, dtype=np.float32)

    # Attack: ramp from 0 to 1.0 over (256 - attack) frames
    if attack < 255:
        attack_frames = 256 - attack
        attack_len = int(attack_frames * samples_per_frame)
        if attack_len > 0:
            actual_attack = min(attack_len, note_on_samples)
            ramp = np.linspace(0.0, 1.0, actual_attack, dtype=np.float32)
            envelope[:actual_attack] = ramp
    # attack == 255: instant, already 1.0

    # Decay: ramp from 1.0 to sustain level
    attack_len = int((256 - attack) * samples_per_frame) if attack < 255 else 0
    decay_start = min(attack_len, note_on_samples)
    if decay > 0 and sustain_level < 1.0:
        decay_frames = 256 - decay
        decay_len = int(decay_frames * samples_per_frame)
        if decay_len > 0 and decay_start < note_on_samples:
            actual_decay = min(decay_len, note_on_samples - decay_start)
            ramp = np.linspace(1.0, sustain_level, actual_decay, dtype=np.float32)
            envelope[decay_start:decay_start + actual_decay] = ramp

    # Sustain: hold at sustain level
    sustain_start = decay_start + (int((256 - decay) * samples_per_frame) if decay > 0 else 0)
    if sustain_start < note_on_samples:
        envelope[sustain_start:note_on_samples] = sustain_level

    # Release: ramp from sustain level to 0
    if release_samples > 0:
        if release < 255:
            release_frames = 256 - release
            release_len = int(release_frames * samples_per_frame)
            if release_len > 0:
                actual_release = min(release_len, release_samples)
                ramp = np.linspace(sustain_level, 0.0, actual_release, dtype=np.float32)
                envelope[note_on_samples:note_on_samples + actual_release] = ramp
                envelope[note_on_samples + actual_release:] = 0.0
            else:
                envelope[note_on_samples:] = 0.0
        else:
            # Very slow release
            ramp = np.linspace(sustain_level, 0.0, release_samples, dtype=np.float32)
            envelope[note_on_samples:] = ramp

    return envelope


# ---------------------------------------------------------------------------
# DirectSound Sample Renderer
# ---------------------------------------------------------------------------

def render_directsound(
    sample: DirectSoundSample,
    midi_note: int,
    base_midi_key: int,
    velocity: int,
    duration_samples: int,
    release_samples: int = 2048,
    attack: int = 0,
    decay: int = 0,
    sustain: int = 15,
    release: int = 0,
    no_resample: bool = False,
) -> np.ndarray:
    """Render a DirectSound instrument for a given note.

    Uses the GBA's MidiKeyToFreq lookup tables to calculate pitch, matching
    real hardware behavior. The sample's raw freq field encodes both sample
    rate and base key in a single fixed-point value.

    If no_resample is True (voice_directsound_no_resample / 0x08), the sample
    plays at its native rate without any pitch shifting.
    """
    if not sample.pcm_data:
        return np.zeros(duration_samples + release_samples, dtype=np.float32)

    # Convert signed 8-bit PCM to float32
    raw = np.frombuffer(sample.pcm_data, dtype=np.int8).astype(np.float32) / 128.0

    # Calculate playback rate using GBA's actual pitch calculation
    if no_resample:
        # voice_directsound_no_resample: play at native sample rate, no pitch shift
        src_rate = sample.sample_rate if sample.sample_rate > 0 else 8000
        total_ratio = src_rate / OUTPUT_SAMPLE_RATE
    elif sample.header.freq_raw > 0:
        # Use GBA's MidiKeyToFreq with the sample's raw freq field
        total_ratio = _midi_key_to_freq(sample.header.freq_raw, midi_note)
        if total_ratio <= 0:
            # Fallback if tables produce zero
            src_rate = sample.sample_rate if sample.sample_rate > 0 else 8000
            total_ratio = src_rate / OUTPUT_SAMPLE_RATE
    else:
        # No freq data — fall back to simple semitone calculation
        semitone_diff = midi_note - base_midi_key
        pitch_ratio = 2.0 ** (semitone_diff / 12.0)
        src_rate = sample.sample_rate if sample.sample_rate > 0 else 8000
        total_ratio = pitch_ratio * (src_rate / OUTPUT_SAMPLE_RATE)

    total_len = duration_samples + release_samples

    # Generate the resampled audio
    if sample.has_loop and sample.header.loop_start > 0:
        # Looping sample: play from start, then loop the sustain portion
        # Fully vectorized — no Python for-loop
        loop_start = sample.header.loop_start
        loop_end = len(raw)
        loop_len = loop_end - loop_start

        if loop_len <= 0:
            loop_start = 0
            loop_len = len(raw)

        # Generate all source positions at once
        positions = np.arange(total_len, dtype=np.float64) * total_ratio
        int_pos = positions.astype(np.int64)
        fracs = (positions - int_pos).astype(np.float32)

        # Map positions that exceed the sample into the loop region
        needs_loop = int_pos >= loop_end
        int_pos[needs_loop] = loop_start + ((int_pos[needs_loop] - loop_start) % loop_len)

        # Clamp indices for safe array access
        idx0 = np.clip(int_pos, 0, len(raw) - 1)
        idx1 = np.clip(int_pos + 1, 0, len(raw) - 1)
        # Wrap idx1 into loop region too
        idx1_needs_loop = idx1 >= loop_end
        idx1[idx1_needs_loop] = loop_start + ((idx1[idx1_needs_loop] - loop_start) % loop_len)
        idx1 = np.clip(idx1, 0, len(raw) - 1)

        # Linear interpolation (vectorized)
        output = raw[idx0] * (1.0 - fracs) + raw[idx1] * fracs
    else:
        # Non-looping sample: one-shot playback
        # Generate source indices
        indices = np.arange(total_len, dtype=np.float64) * total_ratio
        int_indices = indices.astype(np.int64)
        fracs = (indices - int_indices).astype(np.float32)

        # Clamp to sample length
        valid = int_indices < len(raw) - 1
        output = np.zeros(total_len, dtype=np.float32)

        valid_idx = int_indices[valid]
        valid_frac = fracs[valid]
        output[valid] = raw[valid_idx] * (1.0 - valid_frac) + raw[valid_idx + 1] * valid_frac

    # Apply ADSR envelope
    env = generate_adsr_envelope(attack, decay, sustain, release,
                                  duration_samples, release_samples)
    output *= env

    # Apply velocity
    vel_scale = velocity / 127.0
    output *= vel_scale

    return output


# ---------------------------------------------------------------------------
# Square Wave Renderer
# ---------------------------------------------------------------------------

# GBA square wave duty cycles
DUTY_CYCLES = {
    0: 0.125,   # 12.5%
    1: 0.25,    # 25%
    2: 0.50,    # 50%
    3: 0.75,    # 75%
}


def render_square_wave(
    midi_note: int,
    velocity: int,
    duration_samples: int,
    duty_cycle: int = 2,
    sweep: int = 0,
    release_samples: int = 2048,
    attack: int = 0,
    decay: int = 0,
    sustain: int = 15,
    release: int = 0,
) -> np.ndarray:
    """Render a square wave tone for a given MIDI note."""
    duty = DUTY_CYCLES.get(duty_cycle, 0.5)
    freq = 440.0 * (2.0 ** ((midi_note - 69) / 12.0))

    total_len = duration_samples + release_samples
    t = np.arange(total_len, dtype=np.float64) / OUTPUT_SAMPLE_RATE

    # Apply sweep if non-zero (frequency slides)
    if sweep != 0:
        # Sweep changes frequency over time
        sweep_rate = sweep / 128.0 * freq
        freqs = freq + sweep_rate * t
        phase = np.cumsum(freqs / OUTPUT_SAMPLE_RATE)
    else:
        phase = freq * t

    # Generate square wave using duty cycle
    wave = np.where((phase % 1.0) < duty, 0.5, -0.5).astype(np.float32)

    # CGB channels use 0-7/0-15 ADSR scale, convert to 0-255
    ds_a, ds_d, ds_s, ds_r = _cgb_to_ds_adsr(attack, decay, sustain, release)
    env = generate_adsr_envelope(ds_a, ds_d, ds_s, ds_r,
                                  duration_samples, release_samples)
    wave *= env

    # Apply velocity (GBA uses same volume pipeline for all channel types)
    vel_scale = velocity / 127.0
    wave *= vel_scale

    return wave


# ---------------------------------------------------------------------------
# Programmable Wave Renderer
# ---------------------------------------------------------------------------

def render_programmable_wave(
    wave_sample: ProgrammableWaveSample,
    midi_note: int,
    velocity: int,
    duration_samples: int,
    release_samples: int = 2048,
    attack: int = 0,
    decay: int = 0,
    sustain: int = 15,
    release: int = 0,
) -> np.ndarray:
    """Render a programmable wave instrument (16-byte wavetable)."""
    # Get 32 4-bit samples from the waveform
    samples_4bit = wave_sample.samples_4bit if wave_sample.raw_data else [8] * 32

    # Convert to float (-1 to 1)
    wavetable = np.array([(s - 8) / 8.0 for s in samples_4bit], dtype=np.float32)
    table_len = len(wavetable)

    freq = 440.0 * (2.0 ** ((midi_note - 69) / 12.0))
    total_len = duration_samples + release_samples

    # Generate by stepping through the wavetable at the right speed
    phase_inc = freq * table_len / OUTPUT_SAMPLE_RATE
    phase = np.arange(total_len, dtype=np.float64) * phase_inc
    indices = (phase % table_len).astype(np.int32)

    wave = wavetable[indices]

    # CGB channels use 0-7/0-15 ADSR scale, convert to 0-255
    ds_a, ds_d, ds_s, ds_r = _cgb_to_ds_adsr(attack, decay, sustain, release)
    env = generate_adsr_envelope(ds_a, ds_d, ds_s, ds_r,
                                  duration_samples, release_samples)
    wave *= env

    # Apply velocity
    vel_scale = velocity / 127.0
    wave *= vel_scale

    return wave


# ---------------------------------------------------------------------------
# Noise Renderer
# ---------------------------------------------------------------------------

def render_noise(
    midi_note: int,
    velocity: int,
    duration_samples: int,
    period: int = 0,
    release_samples: int = 2048,
    attack: int = 0,
    decay: int = 0,
    sustain: int = 15,
    release: int = 0,
) -> np.ndarray:
    """Render a noise instrument (GBA noise channel approximation)."""
    total_len = duration_samples + release_samples

    # GBA noise uses a linear feedback shift register
    # Period 0 = 15-bit LFSR (white noise), Period 1 = 7-bit LFSR (metallic)
    if period == 1:
        # 7-bit LFSR — shorter period, more tonal/metallic
        lfsr_len = 127
    else:
        # 15-bit LFSR — white noise
        lfsr_len = 32767

    # Generate noise using numpy random (close enough approximation)
    rng = np.random.RandomState(42)  # deterministic for consistency
    if period == 1:
        # Repeat a short noise pattern for metallic sound
        base_noise = rng.uniform(-1, 1, lfsr_len).astype(np.float32)
        repeats = (total_len // lfsr_len) + 1
        noise = np.tile(base_noise, repeats)[:total_len]
    else:
        noise = rng.uniform(-1, 1, total_len).astype(np.float32)

    # Pitch affects the noise update rate (higher note = faster noise)
    freq = 440.0 * (2.0 ** ((midi_note - 69) / 12.0))
    step = max(1, int(OUTPUT_SAMPLE_RATE / freq))
    if step > 1:
        # Sample-and-hold: repeat each noise sample for 'step' output samples
        held = np.repeat(noise[::step], step)[:total_len]
        if len(held) < total_len:
            held = np.pad(held, (0, total_len - len(held)))
        noise = held

    # CGB channels use 0-7/0-15 ADSR scale, convert to 0-255
    ds_a, ds_d, ds_s, ds_r = _cgb_to_ds_adsr(attack, decay, sustain, release)
    env = generate_adsr_envelope(ds_a, ds_d, ds_s, ds_r,
                                  duration_samples, release_samples)
    noise *= env

    # Apply velocity
    vel_scale = velocity / 127.0
    noise *= vel_scale

    return noise


# ---------------------------------------------------------------------------
# Instrument Renderer (dispatcher)
# ---------------------------------------------------------------------------

def render_instrument(
    instrument: Instrument,
    midi_note: int,
    velocity: int,
    duration_samples: int,
    sample_data: SampleData,
    voicegroup_data: VoicegroupData,
    release_samples: int = 2048,
    _depth: int = 0,
) -> np.ndarray:
    """Render a note using the given instrument definition.

    Handles keysplit routing by recursing into sub-voicegroups.

    TONEDATA_TYPE_FIX (bit 3 = 0x08) behavior differs by channel type:
    - DirectSound: mixer ignores computed frequency, plays sample at native
      rate (step = 0x800000 = 1.0). This IS fixed pitch.
    - CGB (square/wave/noise): only applies minor frequency rounding in
      CgbSound for anti-aliasing. Pitch still varies normally with MIDI key.
    So _alt CGB instruments should NOT have their pitch overridden.
    """
    if _depth > 5:
        return np.zeros(duration_samples + release_samples, dtype=np.float32)

    # --- Keysplit routing ---
    if instrument.is_keysplit:
        target_vg_name = instrument.target_voicegroup
        if not target_vg_name:
            return np.zeros(duration_samples + release_samples, dtype=np.float32)

        target_vg = voicegroup_data.get_voicegroup(target_vg_name)
        if not target_vg:
            return np.zeros(duration_samples + release_samples, dtype=np.float32)

        if instrument.keysplit_table:
            # Use keysplit table to find the right instrument slot
            ks_table = voicegroup_data.keysplit_tables.get(instrument.keysplit_table)
            if ks_table:
                slot = ks_table.get_instrument_index(midi_note)
            else:
                slot = 0
            target_inst = target_vg.get_instrument(slot)
        else:
            # keysplit_all (0x80): use the MIDI note number as the index
            # into the target voicegroup — this is how drums/rhythm work.
            # On GBA, this can overflow into the next voicegroup in memory.
            slot = midi_note
            target_inst = voicegroup_data.get_instrument_overflow(
                target_vg_name, slot)

        if target_inst:
            return render_instrument(
                target_inst, midi_note, velocity, duration_samples,
                sample_data, voicegroup_data, release_samples,
                _depth=_depth + 1,
            )
        return np.zeros(duration_samples + release_samples, dtype=np.float32)

    # --- DirectSound ---
    if instrument.is_directsound and instrument.sample_label:
        sample = sample_data.direct_sound.get(instrument.sample_label)
        if sample:
            no_resample = 'no_resample' in instrument.voice_type
            return render_directsound(
                sample, midi_note, instrument.base_midi_key, velocity,
                duration_samples, release_samples,
                instrument.attack, instrument.decay,
                instrument.sustain, instrument.release,
                no_resample=no_resample,
            )
        return np.zeros(duration_samples + release_samples, dtype=np.float32)

    # --- Square wave ---
    if instrument.is_square:
        return render_square_wave(
            midi_note, velocity, duration_samples,
            instrument.duty_cycle, instrument.sweep,
            release_samples,
            instrument.attack, instrument.decay,
            instrument.sustain, instrument.release,
        )

    # --- Programmable wave ---
    if instrument.is_programmable_wave and instrument.wave_label:
        wave = sample_data.programmable_waves.get(instrument.wave_label)
        if wave:
            return render_programmable_wave(
                wave, midi_note, velocity, duration_samples,
                release_samples,
                instrument.attack, instrument.decay,
                instrument.sustain, instrument.release,
            )
        return np.zeros(duration_samples + release_samples, dtype=np.float32)

    # --- Noise ---
    if instrument.is_noise:
        return render_noise(
            midi_note, velocity, duration_samples,
            instrument.period, release_samples,
            instrument.attack, instrument.decay,
            instrument.sustain, instrument.release,
        )

    # Unknown instrument type
    return np.zeros(duration_samples + release_samples, dtype=np.float32)


# ---------------------------------------------------------------------------
# Simple Reverb
# ---------------------------------------------------------------------------

def apply_reverb(audio: np.ndarray, reverb_level: int, sample_rate: int = OUTPUT_SAMPLE_RATE) -> np.ndarray:
    """Apply simple delay-based reverb matching GBA behavior.

    reverb_level: 0-127 (from the song's reverb setting)
    """
    if reverb_level <= 0:
        return audio

    # GBA reverb is a single feedback delay
    delay_ms = 40  # ~40ms delay (approximate)
    delay_samples = int(sample_rate * delay_ms / 1000)
    feedback = reverb_level / 255.0  # scale to 0-0.5 range

    output = audio.copy()
    if delay_samples < len(output):
        output[delay_samples:] += audio[:-delay_samples] * feedback

    return np.clip(output, -1.0, 1.0)


# ---------------------------------------------------------------------------
# Stereo Panning
# ---------------------------------------------------------------------------

def apply_pan(mono: np.ndarray, pan: int) -> np.ndarray:
    """Convert mono to stereo with panning.

    pan: 0-127 where 64 (c_v) is center. 0=full left, 127=full right.

    Uses GBA's linear crossfade (matching ChnVolSetAsm):
      right = (pan + 128) / 255   (where pan is signed: pan_byte - C_V)
      left  = (127 - pan) / 255
    But our pan input is unsigned 0-127, so signed_pan = pan - 64.
    """
    pan = max(0, min(127, pan))
    signed_pan = pan - 64  # convert to GBA signed range (-64 to +63)

    # GBA formula: right_gain = (signed_pan + 128) / 255
    #              left_gain  = (127 - signed_pan) / 255
    right_gain = (signed_pan + 128) / 255.0
    left_gain = (127 - signed_pan) / 255.0

    stereo = np.zeros((len(mono), 2), dtype=np.float32)
    stereo[:, 0] = mono * left_gain
    stereo[:, 1] = mono * right_gain
    return stereo


# ---------------------------------------------------------------------------
# Audio Output Manager
# ---------------------------------------------------------------------------

class AudioPlayer:
    """Manages real-time audio output via sounddevice."""

    def __init__(self):
        self._stream = None
        self._buffer: Optional[np.ndarray] = None
        self._position = 0
        self._playing = False
        self._lock = threading.Lock()
        self._volume = 0.8

    @property
    def is_playing(self) -> bool:
        return self._playing

    @property
    def position(self) -> int:
        """Current playback position in samples."""
        return self._position

    @property
    def volume(self) -> float:
        return self._volume

    @volume.setter
    def volume(self, val: float):
        self._volume = max(0.0, min(1.0, val))

    def play(self, audio: np.ndarray, sample_rate: int = OUTPUT_SAMPLE_RATE,
             mono: bool = False):
        """Start playing an audio buffer.

        audio: float32 array, shape (N,) for mono or (N, 2) for stereo.
        mono: if True, mix stereo down to mono before playback.
        """
        self.stop()

        import sounddevice as sd

        # If mono requested and audio is stereo, mix down
        if mono and audio.ndim == 2 and audio.shape[1] == 2:
            audio = audio.mean(axis=1)

        with self._lock:
            self._buffer = audio
            self._position = 0
            self._playing = True

        channels = 2 if audio.ndim == 2 and audio.shape[1] == 2 else 1

        def callback(outdata, frames, time_info, status):
            with self._lock:
                if not self._playing or self._buffer is None:
                    outdata[:] = 0
                    return

                start = self._position
                end = start + frames

                if start >= len(self._buffer):
                    outdata[:] = 0
                    self._playing = False
                    return

                chunk = self._buffer[start:end]
                actual = len(chunk)

                if channels == 1:
                    outdata[:actual, 0] = chunk * self._volume
                    outdata[actual:] = 0
                else:
                    outdata[:actual] = chunk * self._volume
                    outdata[actual:] = 0

                self._position = end

        self._stream = sd.OutputStream(
            samplerate=sample_rate,
            channels=channels,
            dtype='float32',
            callback=callback,
            blocksize=1024,
        )
        self._stream.start()

    def stop(self):
        """Stop playback."""
        with self._lock:
            self._playing = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def pause(self):
        """Pause playback (can be resumed)."""
        with self._lock:
            self._playing = False

    def resume(self):
        """Resume paused playback."""
        with self._lock:
            if self._buffer is not None and self._position < len(self._buffer):
                self._playing = True

    def seek(self, sample_position: int):
        """Jump to a specific position in the buffer."""
        with self._lock:
            if self._buffer is not None:
                self._position = max(0, min(sample_position, len(self._buffer)))
