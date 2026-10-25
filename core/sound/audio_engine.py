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

OUTPUT_SAMPLE_RATE = 32768  # internal render rate (downsampled to the engine's)
TICKS_PER_FRAME = 1         # M4A runs at ~60 fps, 1 tick per frame at tempo scale
GBA_FRAME_RATE = 59.7275    # GBA refresh rate in Hz

# ---------------------------------------------------------------------------
# What the engine actually runs at — src/m4a.c:80-83
#
#   m4aSoundMode(SOUND_MODE_DA_BIT_8
#              | SOUND_MODE_FREQ_13379
#              | (12 << SOUND_MODE_MASVOL_SHIFT)
#              | (5 << SOUND_MODE_MAXCHN_SHIFT));
#
# so: 13379 Hz, 8-bit DAC, master volume 12 of 15, at most 5 PCM channels.
# Rendering at a higher rate in floating point sounds cleaner than the console
# can physically be, which is most of why previews never matched the ROM.
# ---------------------------------------------------------------------------

ENGINE_MIXER_RATE = 13379       # SOUND_MODE_FREQ_13379
ENGINE_MASTER_VOLUME = 12       # SOUND_MODE_MASVOL value
ENGINE_MAX_PCM_CHANNELS = 5     # SOUND_MODE_MAXCHN value
ENGINE_DAC_BITS = 8             # SOUND_MODE_DA_BIT_8

# The engine steps a CGB envelope once per frame, plus a second time on every
# 15th frame "to keep up with the hardware envelope rate (1/64 s)" — see the
# envelope_step_complete block in src/m4a.c. That works out at 63.7 Hz.
_CGB_EXTRA_TICK_EVERY = 15

# gCgb3Vol (src/m4a_tables.c) maps a CGB wave channel's 0-15 envelope level
# onto NR32, which has only five settings. Expressed here as output gain, so
# the programmable-wave preview steps between the same five levels the
# hardware does instead of fading smoothly.
_CGB3_VOL_GAIN = (
    0.0, 0.0,                    # 0x00 — muted
    0.25, 0.25, 0.25, 0.25,      # 0x60 — 25%
    0.50, 0.50, 0.50, 0.50,      # 0x40 — 50%
    0.75, 0.75, 0.75, 0.75,      # 0x80 — 75%
    1.00, 1.00,                  # 0x20 — 100%
)

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


def _midi_key_to_freq(wav_freq_raw: int, key: float) -> float:
    """Python port of the GBA's MidiKeyToFreq function.

    Supports fractional keys for smooth pitch bending.  On the real GBA
    the fractional part comes from the BEND command and is passed as
    ``fineAdjust`` (0-255).  The hardware interpolates between two
    adjacent scale-table entries:

        val = val1 + (val2 - val1) * fineAdjust / 256
        result = umul3232H32(wav->freq, val)

    We replicate this exactly: the integer part of *key* selects val1/val2,
    and the fractional part drives the interpolation.

    Args:
        wav_freq_raw: The raw freq field from the WaveData header (fixed-point).
        key: MIDI note number, may be fractional (e.g. 60.5 for a half-semitone bend).

    Returns:
        Playback rate as a float (samples per output sample).
    """
    key = max(0.0, min(178.0, float(key)))
    int_key = int(key)
    fine_adjust = key - int_key  # 0.0 .. <1.0

    s1 = _GBA_SCALE_TABLE[int_key]
    val1 = _GBA_FREQ_TABLE[s1 & 0xF] >> (s1 >> 4)
    s2 = _GBA_SCALE_TABLE[min(int_key + 1, 179)]
    val2 = _GBA_FREQ_TABLE[s2 & 0xF] >> (s2 >> 4)

    # Interpolate between val1 and val2 (matches GBA fineAdjust)
    val = val1 + (val2 - val1) * fine_adjust
    playback_freq = (wav_freq_raw * val) / (1 << 32)
    # Convert to a ratio against our output sample rate
    return playback_freq / OUTPUT_SAMPLE_RATE


def _fit_curve(curve: np.ndarray, n: int) -> np.ndarray:
    """Coerce a per-sample control curve (e.g. a bend envelope) to length ``n``:
    clip if longer, hold the last value if shorter. Keeps the renderers safe
    against off-by-one length mismatches between the curve and total_len."""
    m = len(curve)
    if m == n:
        return curve
    if m > n:
        return curve[:n]
    out = np.empty(n, dtype=curve.dtype)
    out[:m] = curve
    out[m:] = curve[-1] if m else 0.0
    return out


# ---------------------------------------------------------------------------
# PCM (DirectSound) envelope — port of src/m4a_1.s, SoundMainRAM_ChanLoop
# ---------------------------------------------------------------------------
# The engine runs this once per audio frame (59.7275 Hz). The three bytes are
# NOT durations, and only one of the three behaves the way a synthesiser
# normally would:
#
#   attack  (_081DCFF8)  envelopeVolume += attack, clamped at 0xFF
#                        -> ADDITIVE. Time to full = 256 / attack frames,
#                           so a BIG attack byte means FAST.
#   decay   (_081DCFD6)  envelopeVolume = (envelopeVolume * decay) >> 8
#                        until it falls to sustain
#                        -> MULTIPLICATIVE. A BIG decay byte means SLOW.
#   sustain              held at the sustain byte (0-255).
#   release (_081DCFB6)  envelopeVolume = (envelopeVolume * release) >> 8
#                        until it falls to pseudoEchoVolume
#                        -> MULTIPLICATIVE. A BIG release byte means SLOW.
#
# Treating any of them as a linear ramp over "256 - byte" frames — which is
# what this module used to do — makes attack far too slow and gets decay and
# release backwards, so instruments previewed nothing like the ROM. Keep this
# a per-frame integer state machine; do not "simplify" it into a curve.
# ---------------------------------------------------------------------------

_ENV_ATTACK = 3     # SOUND_CHANNEL_SF_ENV_ATTACK
_ENV_DECAY = 2      # SOUND_CHANNEL_SF_ENV_DECAY
_ENV_SUSTAIN = 1    # SOUND_CHANNEL_SF_ENV_SUSTAIN
_ENV_RELEASE = 0    # SOUND_CHANNEL_SF_ENV_RELEASE


def engine_pcm_envelope_frames(
    attack: int,
    decay: int,
    sustain: int,
    release: int,
    note_on_frames: int,
    total_frames: int,
    pseudo_echo_volume: int = 0,
    pseudo_echo_length: int = 0,
) -> list[int]:
    """Per-frame envelope volumes (0-255) exactly as the mixer computes them.

    ``note_on_frames`` is how many frames the key is held; after that the
    channel's STOP flag is set and the release multiply takes over. Pseudo-echo
    (the engine's SOUND_CHANNEL_SF_IEC path) re-triggers the note at
    ``pseudo_echo_volume`` for ``pseudo_echo_length`` frames once release falls
    to that floor; both default to off, which is what every song in a stock
    project uses, since they are set by the xiecv / xiecl track commands.
    """
    attack = max(0, min(255, int(attack)))
    decay = max(0, min(255, int(decay)))
    sustain = max(0, min(255, int(sustain)))
    release = max(0, min(255, int(release)))
    echo_vol = max(0, min(255, int(pseudo_echo_volume)))
    echo_len = max(0, int(pseudo_echo_length))

    out: list[int] = []
    vol = 0
    state = _ENV_ATTACK
    in_echo = False
    echo_left = echo_len
    stopped = False

    for frame in range(max(0, total_frames)):
        if stopped:
            out.append(0)
            continue

        if in_echo:
            # _081DCFA0: count the echo down; the volume is held meanwhile.
            echo_left -= 1
            if echo_left <= 0:
                vol = 0
                stopped = True
            out.append(vol)
            continue

        if frame >= note_on_frames:
            # _081DCFB6: the key is up, so release multiplies regardless of
            # which envelope phase the note had reached.
            vol = (vol * release) >> 8
            if vol <= echo_vol:
                # _081DCFC8
                vol = echo_vol
                if vol == 0:
                    stopped = True
                else:
                    in_echo = True
                    echo_left = echo_len if echo_len else 1
        elif state == _ENV_DECAY:
            vol = (vol * decay) >> 8
            if vol <= sustain:
                vol = sustain
                if vol == 0:
                    vol = echo_vol
                    if vol == 0:
                        stopped = True
                    else:
                        in_echo = True
                        echo_left = echo_len if echo_len else 1
                else:
                    state = _ENV_SUSTAIN
        elif state == _ENV_ATTACK:
            vol += attack
            if vol >= 0xFF:
                vol = 0xFF
                state = _ENV_DECAY
        # _ENV_SUSTAIN: held, nothing to do.

        out.append(vol)

    return out


def _frames_to_samples(frames, n_samples: int, scale: float,
                       sample_rate: int) -> np.ndarray:
    """Expand a per-frame envelope to per-sample, held flat within each frame.

    The hardware envelope only changes on a frame boundary, so a zero-order
    hold is what actually happens — interpolating would smooth over steps the
    ear can hear on a fast decay.
    """
    if n_samples <= 0:
        return np.zeros(0, dtype=np.float32)
    if not frames:
        return np.zeros(n_samples, dtype=np.float32)
    arr = np.asarray(frames, dtype=np.float32) * scale
    idx = (np.arange(n_samples, dtype=np.float64)
           * (GBA_FRAME_RATE / float(sample_rate))).astype(np.int64)
    np.clip(idx, 0, len(arr) - 1, out=idx)
    return arr[idx]


def release_tail_frames(release: int, start_level: int,
                        pseudo_echo_volume: int = 0,
                        pseudo_echo_length: int = 0) -> int:
    """How many frames the engine's release actually takes from ``start_level``.

    Used to size a preview buffer. The old code hard-capped every release at
    2048 samples (62.5 ms), which chopped the tail off anything that rings.
    """
    release = max(0, min(255, int(release)))
    vol = max(0, min(255, int(start_level)))
    echo_vol = max(0, min(255, int(pseudo_echo_volume)))
    frames = 0
    while vol > echo_vol and frames < 1024:
        nxt = (vol * release) >> 8
        frames += 1
        if nxt == vol:          # release 256/256 would never fall — cannot
            break               # happen with a byte, but do not spin
        vol = nxt
    return frames + max(0, int(pseudo_echo_length))


def pcm_release_samples(attack: int, decay: int, sustain: int, release: int,
                        note_on_samples: int,
                        sample_rate: int = OUTPUT_SAMPLE_RATE,
                        pseudo_echo_volume: int = 0,
                        pseudo_echo_length: int = 0) -> int:
    """Buffer length a PCM note's release needs, in samples.

    Measured, not guessed: the envelope is run for the note-on period to find
    the level the release actually starts from, then the release multiply is
    counted down from there. A note cut short during its attack releases from
    a low level and needs almost no tail; a sustained one with release 0xF0
    rings for most of a second. The old code used a flat 2048 samples
    (62.5 ms) for both, which chopped every real tail off mid-fade.
    """
    spf = sample_rate / GBA_FRAME_RATE
    note_on_frames = max(1, int(math.ceil(max(0, note_on_samples) / spf)))
    held = engine_pcm_envelope_frames(
        attack, decay, sustain, release, note_on_frames, note_on_frames,
        pseudo_echo_volume, pseudo_echo_length)
    start_level = held[-1] if held else 0
    frames = release_tail_frames(release, start_level,
                                 pseudo_echo_volume, pseudo_echo_length)
    return int(frames * spf) + 1


def generate_adsr_envelope(
    attack: int,
    decay: int,
    sustain: int,
    release: int,
    note_on_samples: int,
    release_samples: int = 0,
    sample_rate: int = OUTPUT_SAMPLE_RATE,
    pseudo_echo_volume: int = 0,
    pseudo_echo_length: int = 0,
) -> np.ndarray:
    """Per-sample PCM envelope, 0.0-1.0, matching the engine frame for frame."""
    total_samples = max(0, note_on_samples) + max(0, release_samples)
    if total_samples <= 0:
        return np.zeros(0, dtype=np.float32)

    spf = sample_rate / GBA_FRAME_RATE
    note_on_frames = int(math.ceil(max(0, note_on_samples) / spf))
    total_frames = int(math.ceil(total_samples / spf)) + 1

    frames = engine_pcm_envelope_frames(
        attack, decay, sustain, release, note_on_frames, total_frames,
        pseudo_echo_volume, pseudo_echo_length)
    return _frames_to_samples(frames, total_samples, 1.0 / 255.0, sample_rate)


# ---------------------------------------------------------------------------
# CGB envelope — port of the envelope_* block in src/m4a.c (CgbSound)
# ---------------------------------------------------------------------------
# Square, programmable-wave and noise voices do NOT use the PCM envelope. The
# CGB envelope is a different machine: envelopeCounter counts the attack /
# decay / release bytes down as STEP TIMES in 1/64-second ticks, and each time
# it reaches zero a 4-bit volume (0-15) moves by one step. Converting the CGB
# bytes onto the PCM scale and reusing the PCM envelope — which is what this
# module used to do — is both mis-scaled and backwards.
# ---------------------------------------------------------------------------

def engine_cgb_envelope_frames(
    attack: int,
    decay: int,
    sustain: int,
    release: int,
    note_on_frames: int,
    total_frames: int,
    envelope_goal: int = 15,
    pseudo_echo_volume: int = 0,
    pseudo_echo_length: int = 0,
) -> list[int]:
    """Per-frame CGB envelope levels (0-15) as CgbSound computes them."""
    attack = max(0, int(attack))
    decay = max(0, int(decay))
    sustain = max(0, min(15, int(sustain)))
    release = max(0, int(release))
    goal = max(0, min(15, int(envelope_goal)))
    # CgbModVol: sustainGoal = (envelopeGoal * sustain + 15) >> 4
    sustain_goal = (goal * sustain + 15) >> 4
    echo_vol = max(0, min(255, int(pseudo_echo_volume)))
    echo_len = max(0, int(pseudo_echo_length))

    out: list[int] = []
    vol = 0
    state = _ENV_ATTACK
    counter = attack
    in_echo = False
    echo_left = echo_len
    stopped = False
    released = False

    def _step() -> None:
        """One envelope tick — the body of envelope_step_repeat."""
        nonlocal vol, state, counter, in_echo, echo_left, stopped
        if stopped or in_echo:
            return
        if counter > 0:
            counter -= 1
            return
        if state == _ENV_RELEASE:
            vol -= 1
            if vol <= 0:
                # envelope_pseudoecho_start
                vol = ((goal * echo_vol) + 0xFF) >> 8
                if vol:
                    in_echo = True
                    echo_left = echo_len if echo_len else 1
                else:
                    vol = 0
                    stopped = True
            else:
                counter = release
        elif state == _ENV_SUSTAIN:
            vol = sustain_goal
            counter = 7
        elif state == _ENV_DECAY:
            vol -= 1
            if vol <= sustain_goal:
                if sustain == 0:
                    vol = ((goal * echo_vol) + 0xFF) >> 8
                    if vol:
                        in_echo = True
                        echo_left = echo_len if echo_len else 1
                    else:
                        vol = 0
                        stopped = True
                else:
                    state = _ENV_SUSTAIN
                    vol = sustain_goal
                    counter = 7
            else:
                counter = decay
        else:  # _ENV_ATTACK
            vol += 1
            if vol >= goal:
                vol = goal
                state = _ENV_DECAY
                counter = decay
            else:
                counter = attack

    for frame in range(max(0, total_frames)):
        if not released and frame >= note_on_frames:
            # The STOP branch: switch to release and load its step time.
            released = True
            if not stopped and not in_echo:
                state = _ENV_RELEASE
                counter = release

        if in_echo:
            echo_left -= 1
            if echo_left <= 0:
                vol = 0
                stopped = True
        elif not stopped:
            _step()
            # "every 15 frames, envelope calculation has to be done twice to
            # keep up with the hardware envelope rate (1/64 s)"
            if frame % _CGB_EXTRA_TICK_EVERY == _CGB_EXTRA_TICK_EVERY - 1:
                _step()

        out.append(0 if stopped else max(0, min(15, vol)))

    return out


def _cgb_envelope_samples(attack, decay, sustain, release, note_on_samples,
                          release_samples, sample_rate, *, wave_levels=False,
                          envelope_goal=15):
    total_samples = max(0, note_on_samples) + max(0, release_samples)
    if total_samples <= 0:
        return np.zeros(0, dtype=np.float32)
    spf = sample_rate / GBA_FRAME_RATE
    note_on_frames = int(math.ceil(max(0, note_on_samples) / spf))
    total_frames = int(math.ceil(total_samples / spf)) + 1
    frames = engine_cgb_envelope_frames(
        attack, decay, sustain, release, note_on_frames, total_frames,
        envelope_goal=envelope_goal)
    if wave_levels:
        # The wave channel's level goes through gCgb3Vol, which has only five
        # settings — so map straight onto those gains. Rounding them back into
        # the 0-15 integer scale first would smear them off the real levels.
        gains = [_CGB3_VOL_GAIN[max(0, min(15, v))] for v in frames]
        return _frames_to_samples(gains, total_samples, 1.0, sample_rate)
    return _frames_to_samples(frames, total_samples, 1.0 / 15.0, sample_rate)


def generate_cgb_envelope(attack: int, decay: int, sustain: int, release: int,
                          note_on_samples: int, release_samples: int = 0,
                          sample_rate: int = OUTPUT_SAMPLE_RATE) -> np.ndarray:
    """Per-sample CGB envelope, 0.0-1.0, for square and noise voices."""
    return _cgb_envelope_samples(attack, decay, sustain, release,
                                 note_on_samples, release_samples, sample_rate)


def generate_cgb_wave_envelope(attack: int, decay: int, sustain: int,
                               release: int, note_on_samples: int,
                               release_samples: int = 0,
                               sample_rate: int = OUTPUT_SAMPLE_RATE
                               ) -> np.ndarray:
    """Per-sample envelope for a programmable-wave voice (five levels only)."""
    return _cgb_envelope_samples(attack, decay, sustain, release,
                                 note_on_samples, release_samples, sample_rate,
                                 wave_levels=True)


def cgb_release_samples(release: int, sustain: int,
                        sample_rate: int = OUTPUT_SAMPLE_RATE) -> int:
    """Buffer length a CGB note's release needs, in samples.

    The release byte is a step time in 1/64-second ticks and the level walks
    down one step at a time from the sustain level, so the tail is
    ``release * levels`` ticks long.
    """
    release = max(0, int(release))
    sustain = max(0, min(15, int(sustain)))
    levels = max(1, (15 * sustain + 15) >> 4) if sustain else 15
    ticks = max(1, release) * levels
    seconds = ticks / (GBA_FRAME_RATE * (1.0 + 1.0 / _CGB_EXTRA_TICK_EVERY))
    return int(seconds * sample_rate) + 1


# ---------------------------------------------------------------------------
# DirectSound Sample Renderer
# ---------------------------------------------------------------------------

def render_directsound(
    sample: DirectSoundSample,
    midi_note: float,
    base_midi_key: int,
    velocity: int,
    duration_samples: int,
    release_samples: int = 2048,
    attack: int = 255,
    decay: int = 0,
    sustain: int = 255,
    release: int = 0,
    no_resample: bool = False,
    bend_curve: Optional[np.ndarray] = None,
) -> np.ndarray:
    """Render a DirectSound instrument for a given note.

    Uses the GBA's MidiKeyToFreq lookup tables to calculate pitch, matching
    real hardware behavior. The sample's raw freq field encodes both sample
    rate and base key in a single fixed-point value.

    The ADSR defaults are deliberately the neutral envelope — instant on, full
    volume, instant off. They used to be ``attack=0, sustain=15``, which only
    sounded like anything because the envelope generator was wrong: on real
    hardware ``attack=0`` adds nothing per frame, so the note never rises and
    plays SILENT, and ``sustain=15`` is 6% of full, not full. Any caller that
    cares about the envelope passes the instrument's real bytes.

    If no_resample is True (voice_directsound_no_resample / 0x08), the sample
    plays at the engine's mixer rate without pitch shifting.

    ``bend_curve`` (per-sample added semitones) glides the read rate within the
    note so a mid-note BEND (held-note sweep) is heard. Ignored for no_resample
    (that voice is fixed-pitch by design). None = fixed rate.
    """
    if not sample.pcm_data:
        return np.zeros(duration_samples + release_samples, dtype=np.float32)

    # Convert signed 8-bit PCM to float32
    raw = np.frombuffer(sample.pcm_data, dtype=np.int8).astype(np.float32) / 128.0

    # Calculate playback rate using GBA's actual pitch calculation
    if no_resample:
        # voice_directsound_no_resample (TONEDATA_TYPE_FIX). The mixer's fixed
        # path (_081DD07C in src/m4a_1.s) reads one source byte per output
        # sample, so the sample plays at the MIXER's rate — 13379 Hz — no
        # matter what rate it was authored at. Using the sample's own rate
        # here was right only by coincidence, because most fixed-pitch drums
        # in a stock project happen to be 13379 Hz; the ones that are not
        # previewed at the wrong pitch (an 11025 Hz hit sounds a third too
        # low, a 26758 Hz one a whole octave).
        total_ratio = ENGINE_MIXER_RATE / OUTPUT_SAMPLE_RATE
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

    # Per-sample source positions. With a bend curve the read rate glides
    # (cumulative), so the pitch slides within the note; otherwise it's the
    # flat arange*ratio as before.
    if bend_curve is not None and not no_resample:
        rate_curve = total_ratio * (2.0 ** (_fit_curve(bend_curve, total_len) / 12.0))
        positions_full = np.cumsum(rate_curve)
    else:
        positions_full = np.arange(total_len, dtype=np.float64) * total_ratio

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

        # Generate all source positions at once (bend-aware; see positions_full)
        positions = positions_full
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
        # Non-looping sample: one-shot playback (bend-aware; see positions_full)
        indices = positions_full
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
    midi_note: float,
    velocity: int,
    duration_samples: int,
    duty_cycle: int = 2,
    sweep: int = 0,
    release_samples: int = 2048,
    attack: int = 0,
    decay: int = 0,
    sustain: int = 15,
    release: int = 0,
    bend_curve: Optional[np.ndarray] = None,
) -> np.ndarray:
    """Render a square wave tone for a given MIDI note.

    ``bend_curve`` (per-sample added semitones) makes the pitch GLIDE within the
    note — needed so a held note with mid-note BEND commands (e.g. an Oracle
    jump's rising sweep) previews correctly instead of flat. None = fixed pitch.
    """
    duty = DUTY_CYCLES.get(duty_cycle, 0.5)
    freq = 440.0 * (2.0 ** ((midi_note - 69) / 12.0))

    total_len = duration_samples + release_samples
    t = np.arange(total_len, dtype=np.float64) / OUTPUT_SAMPLE_RATE

    if bend_curve is not None:
        # Per-sample frequency from the bent MIDI key; phase-accumulate so the
        # tone slides continuously (combines with any voicegroup sweep).
        mc = _fit_curve(bend_curve, total_len)
        freqs = 440.0 * (2.0 ** ((midi_note + mc - 69) / 12.0))
        if sweep != 0:
            freqs = freqs + (sweep / 128.0 * freq) * t
        phase = np.cumsum(freqs / OUTPUT_SAMPLE_RATE)
    # Apply sweep if non-zero (frequency slides)
    elif sweep != 0:
        # Sweep changes frequency over time
        sweep_rate = sweep / 128.0 * freq
        freqs = freq + sweep_rate * t
        phase = np.cumsum(freqs / OUTPUT_SAMPLE_RATE)
    else:
        phase = freq * t

    # Generate square wave using duty cycle
    wave = np.where((phase % 1.0) < duty, 0.5, -0.5).astype(np.float32)

    # Square voices run the CGB envelope, not the PCM one: a 4-bit level
    # stepped on a 1/64 s clock by the attack/decay/release step times.
    env = generate_cgb_envelope(attack, decay, sustain, release,
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
    midi_note: float,
    velocity: int,
    duration_samples: int,
    release_samples: int = 2048,
    attack: int = 0,
    decay: int = 0,
    sustain: int = 15,
    release: int = 0,
    bend_curve: Optional[np.ndarray] = None,
) -> np.ndarray:
    """Render a programmable wave instrument (16-byte wavetable).

    ``bend_curve`` (per-sample added semitones) glides the pitch within the note
    (mid-note BEND). None = fixed pitch."""
    # Get 32 4-bit samples from the waveform
    samples_4bit = wave_sample.samples_4bit if wave_sample.raw_data else [8] * 32

    # Convert to float (-1 to 1)
    wavetable = np.array([(s - 8) / 8.0 for s in samples_4bit], dtype=np.float32)
    table_len = len(wavetable)

    freq = 440.0 * (2.0 ** ((midi_note - 69) / 12.0))
    total_len = duration_samples + release_samples

    # Generate by stepping through the wavetable at the right speed
    if bend_curve is not None:
        mc = _fit_curve(bend_curve, total_len)
        freqs = 440.0 * (2.0 ** ((midi_note + mc - 69) / 12.0))
        phase = np.cumsum(freqs * table_len / OUTPUT_SAMPLE_RATE)
    else:
        phase_inc = freq * table_len / OUTPUT_SAMPLE_RATE
        phase = np.arange(total_len, dtype=np.float64) * phase_inc
    indices = (phase % table_len).astype(np.int32)

    wave = wavetable[indices]

    # The wave channel's level goes through gCgb3Vol, which has only five
    # settings, so its envelope steps between those instead of fading.
    env = generate_cgb_wave_envelope(attack, decay, sustain, release,
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
    midi_note: float,
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

    # Noise is a CGB channel too — same 4-bit stepped envelope as the squares.
    env = generate_cgb_envelope(attack, decay, sustain, release,
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
    midi_note: float,
    velocity: int,
    duration_samples: int,
    sample_data: SampleData,
    voicegroup_data: VoicegroupData,
    release_samples: int = 2048,
    _depth: int = 0,
    bend_curve: Optional[np.ndarray] = None,
) -> np.ndarray:
    """Render a note using the given instrument definition.

    ``bend_curve`` (per-sample added semitones, or None) glides the note's pitch
    for mid-note BEND — passed through to the pitched channel renderers.

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

    # Debug logging for first few calls
    if _depth == 0 and not hasattr(render_instrument, '_log_count'):
        render_instrument._log_count = 0
    if _depth == 0:
        render_instrument._log_count = getattr(render_instrument, '_log_count', 0) + 1
        if render_instrument._log_count <= 20:
            import os, time as _t
            _dbg = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'sound_debug.log')
            with open(_dbg, 'a', encoding='utf-8', newline='\n') as _f:
                _f.write(f"[{_t.strftime('%H:%M:%S')}] render_instrument: note={midi_note} vel={velocity} "
                         f"dur={duration_samples} type={instrument.voice_type} "
                         f"ds={instrument.is_directsound} sq={instrument.is_square} "
                         f"ks={instrument.is_keysplit} sample={instrument.sample_label}\n")

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
            # (table lookup needs integer key — fractional pitch is for audio only)
            ks_table = voicegroup_data.keysplit_tables.get(instrument.keysplit_table)
            if ks_table:
                slot = ks_table.get_instrument_index(int(midi_note))
            else:
                slot = 0
            target_inst = target_vg.get_instrument(slot)
        else:
            # keysplit_all (0x80): use the MIDI note number as the index
            # into the target voicegroup — this is how drums/rhythm work.
            # On GBA, this can overflow into the next voicegroup in memory.
            slot = int(midi_note)
            target_inst = voicegroup_data.get_instrument_overflow(
                target_vg_name, slot)

        if target_inst:
            return render_instrument(
                target_inst, midi_note, velocity, duration_samples,
                sample_data, voicegroup_data, release_samples,
                _depth=_depth + 1, bend_curve=bend_curve,
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
                no_resample=no_resample, bend_curve=bend_curve,
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
            bend_curve=bend_curve,
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
                bend_curve=bend_curve,
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

def apply_reverb_stereo(stereo: np.ndarray, reverb_level: int,
                        sample_rate: int = OUTPUT_SAMPLE_RATE) -> np.ndarray:
    """Apply the engine's reverb — src/m4a_1.s, SoundMainRAM_Reverb.

    Per sample position the engine sums four bytes: left and right from the
    half of the double buffer it is about to overwrite (two frames old) and
    left and right from the other half (one frame old), multiplies by the
    reverb level and shifts right 9. It writes that single value into BOTH
    channels as the frame's starting point, and the voices are then mixed in
    on top — so it is a two-tap feedback comb at roughly one and two frames
    (~17 ms and ~33 ms) that also pulls the stereo image toward the middle.

    That is nothing like a single dry echo tap, which is what this used to be.
    Every song in a stock project sets ``reverb_set+50``, so getting the shape
    wrong coloured all of them.

    ``stereo`` is (n, 2) float; the return is a new array of the same shape.
    """
    if reverb_level <= 0 or stereo.size == 0:
        return stereo
    return ReverbState(reverb_level, sample_rate).process(stereo)


class ReverbState:
    """Stateful form of :func:`apply_reverb_stereo`.

    The engine's reverb feeds its own output back, so it cannot be applied to
    one isolated block and forgotten. Live playback hands the mixer a new
    buffer every callback, so it keeps one of these across callbacks instead of
    restarting the comb (and audibly re-seeding it) every time.
    """

    def __init__(self, reverb_level: int,
                 sample_rate: int = OUTPUT_SAMPLE_RATE):
        self.reverb = int(max(0, min(127, reverb_level)))
        self.block = max(1, int(round(sample_rate / GBA_FRAME_RATE)))
        self._prev1 = np.zeros((self.block, 2), dtype=np.float32)
        self._prev2 = np.zeros((self.block, 2), dtype=np.float32)

    def reset(self) -> None:
        self._prev1[:] = 0.0
        self._prev2[:] = 0.0

    def process(self, stereo: np.ndarray) -> np.ndarray:
        if self.reverb <= 0 or stereo.size == 0:
            return stereo
        block = self.block
        out = np.array(stereo, dtype=np.float32, copy=True)
        for start in range(0, out.shape[0], block):
            end = min(start + block, out.shape[0])
            length = end - start
            # prev1 is one block back, prev2 two blocks back — both already
            # reverbed, which is what makes this feed back rather than decay
            # in a single hop.
            seed = ((self._prev2[:length, 0] + self._prev2[:length, 1]
                     + self._prev1[:length, 0] + self._prev1[:length, 1])
                    * self.reverb) / 512.0
            out[start:end, 0] += seed
            out[start:end, 1] += seed
            # The engine stores the result as a signed byte, so the buffer
            # itself clips — the feedback loop cannot run away.
            np.clip(out[start:end], -1.0, 1.0, out=out[start:end])
            self._prev2 = self._prev1
            nxt = np.zeros((block, 2), dtype=np.float32)
            nxt[:length] = out[start:end]
            self._prev1 = nxt
        return out


def apply_engine_output_stage(stereo: np.ndarray,
                              sample_rate: int = OUTPUT_SAMPLE_RATE,
                              master_volume: int = ENGINE_MASTER_VOLUME
                              ) -> np.ndarray:
    """Put a finished mix through the console's output stage.

    src/m4a.c sets SOUND_MODE_FREQ_13379 and SOUND_MODE_DA_BIT_8 with master
    volume 12 of 15, so the hardware never produces anything brighter than a
    6.7 kHz Nyquist or finer than 256 levels. Rendering in floating point at
    32768 Hz and stopping there is why previews sounded cleaner and fuller
    than the ROM ever can.

    Three steps, in the order the console does them:

    1. Scale by ``(master_volume + 1) / 16`` — the ``>> 4`` the mixer applies
       right after the envelope (_081DD006 in src/m4a_1.s).
    2. Resample to 13379 Hz by picking nearest samples, with no anti-alias
       filter. The engine does not filter either; it simply computes samples
       at that rate, aliasing and all, and the aliasing is part of the sound.
    3. Quantise to 8-bit signed and hold each sample for one DAC period on
       the way back up to the playback rate, which is what the DMA does.
    """
    if stereo.size == 0:
        return stereo

    scaled = np.asarray(stereo, dtype=np.float32) * ((master_volume + 1) / 16.0)

    n = scaled.shape[0]
    n_engine = max(1, int(round(n * ENGINE_MIXER_RATE / float(sample_rate))))
    pick = np.minimum(
        (np.arange(n_engine, dtype=np.float64)
         * (float(sample_rate) / ENGINE_MIXER_RATE)).astype(np.int64),
        n - 1)
    at_engine_rate = scaled[pick]

    levels = 1 << (ENGINE_DAC_BITS - 1)          # 128
    quantised = np.clip(np.rint(at_engine_rate * levels),
                        -levels, levels - 1) / levels

    hold = np.minimum(
        (np.arange(n, dtype=np.float64)
         * (ENGINE_MIXER_RATE / float(sample_rate))).astype(np.int64),
        n_engine - 1)
    return quantised[hold].astype(np.float32)


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
