"""
Quick integration test for the audio engine against real project data.
Run from porysuite root: python -m pytest tests/test_audio_engine.py -v
"""

import sys
import os

# Add project root to path so core.sound imports work without dragging in
# the full app (which needs local_env on the path)
project_root = os.path.join(os.path.dirname(__file__), '..')
sys.path.insert(0, project_root)
# Stub out local_env and prevent core.__init__ from importing the rest of the app
import types
local_env_mod = types.ModuleType('local_env')
local_env_mod.LocalUtil = type('LocalUtil', (), {})
sys.modules['local_env'] = local_env_mod

import numpy as np

from core.sound.sample_loader import load_sample_data
from core.sound.voicegroup_parser import load_voicegroup_data
from core.sound.audio_engine import (
    generate_adsr_envelope,
    generate_cgb_wave_envelope,
    engine_cgb_envelope_frames,
    engine_pcm_envelope_frames,
    pcm_release_samples,
    release_tail_frames,
    render_directsound,
    render_square_wave,
    render_programmable_wave,
    render_noise,
    render_instrument,
    apply_engine_output_stage,
    apply_reverb_stereo,
    apply_pan,
    GBA_FRAME_RATE,
    OUTPUT_SAMPLE_RATE,
)

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), '..', 'pokefirered')


# ---------------------------------------------------------------------------
# PCM envelope — must stay a frame-for-frame port of src/m4a_1.s
#
# These lock in the engine's actual arithmetic, which is NOT what a
# synthesiser normally does: attack is ADDITIVE (big byte = fast), decay and
# release are MULTIPLICATIVE (big byte = slow). An earlier version of this
# module read all three as linear ramps over "256 - byte" frames, which made
# attack up to 48x too slow and got decay and release backwards, so every
# preview sounded unlike the ROM. If one of these fails, the port has drifted.
# ---------------------------------------------------------------------------

FRAME_MS = 1000.0 / GBA_FRAME_RATE


def _ms(frames):
    return frames * FRAME_MS


def test_pcm_attack_is_additive():
    """attack byte adds to the volume each frame; 256/attack frames to full."""
    # attack 255 reaches full on the first frame.
    assert engine_pcm_envelope_frames(255, 0, 255, 0, 10, 10)[0] == 255
    # attack 64 takes four frames (64, 128, 192, 255) — not 192 frames.
    f = engine_pcm_envelope_frames(64, 0, 255, 0, 10, 10)
    assert f[:4] == [64, 128, 192, 255]
    # attack 0 never rises: on real hardware that note is silent forever.
    # (sound_constants.clamp_psg_envelope exists to stop that value reaching
    # a CGB voice; the PCM envelope must still report it faithfully.)
    assert set(engine_pcm_envelope_frames(0, 0, 255, 0, 40, 40)) == {0}


def test_pcm_decay_is_multiplicative_and_not_inverted():
    """decay multiplies by decay/256 each frame — a BIG byte decays SLOWLY."""
    slow = engine_pcm_envelope_frames(255, 250, 0, 0, 400, 400)
    fast = engine_pcm_envelope_frames(255, 100, 0, 0, 400, 400)

    def frames_to_half(frames):
        return next(i for i, v in enumerate(frames) if v <= 127)

    assert frames_to_half(slow) > frames_to_half(fast), \
        "decay 250 must fall more slowly than decay 100"
    # One step is exactly (vol * decay) >> 8 from the 255 peak.
    assert slow[1] == (255 * 250) >> 8


def test_pcm_release_is_multiplicative_and_not_inverted():
    """release multiplies by release/256 each frame — BIG byte = LONG tail."""
    long_tail = release_tail_frames(240, 255)
    short_tail = release_tail_frames(100, 255)
    assert long_tail > short_tail, \
        "release 240 must ring longer than release 100"
    assert release_tail_frames(0, 255) == 1       # release 0 cuts instantly


def test_pcm_envelope_matches_real_project_voices():
    """Measured against ADSR values taken straight out of voice_groups.inc.

    The engine timings here were read off src/m4a_1.s by simulation; they are
    the numbers the ROM produces. Generous tolerances, because the point is to
    catch a regression of the old linear model (which was wrong by 10x to 48x),
    not to pin exact frame counts.
    """
    cases = [
        # name,                 a,   d,   s,   r,   attack_ms, decay_ms
        ('sc88pro_accordion',   64, 188, 108, 165,  67.0,   50.2),
        ('sc88pro_flute',      255, 127, 231, 127,  16.7,   16.7),
        ('nylon_str_guitar',   128, 249,  25, 127,  33.5, 1138.5),
    ]
    for name, a, d, s, r, want_attack, want_decay in cases:
        frames = engine_pcm_envelope_frames(a, d, s, r, 600, 600)
        at_full = next(i for i, v in enumerate(frames) if v >= 255) + 1
        to_sustain = next(i for i, v in enumerate(frames)
                          if i >= at_full - 1 and v <= max(s, 1)) + 1
        assert abs(_ms(at_full) - want_attack) < 5.0, \
            f"{name}: attack {_ms(at_full):.1f}ms, expected {want_attack}ms"
        assert abs(_ms(to_sustain - at_full) - want_decay) < 60.0, \
            f"{name}: decay {_ms(to_sustain - at_full):.1f}ms, expected {want_decay}ms"


def test_pcm_envelope_holds_within_a_frame():
    """The hardware envelope only changes on a frame boundary."""
    env = generate_adsr_envelope(64, 0, 255, 0, 4000)
    spf = int(OUTPUT_SAMPLE_RATE / GBA_FRAME_RATE)
    # Everything inside the first frame is one value, then it steps.
    assert np.allclose(env[:spf - 1], env[0])
    assert env[spf + 1] > env[0]


def test_release_buffer_is_sized_from_the_envelope():
    """A real tail gets a real buffer, not a flat 62.5 ms.

    The 2048-sample cap used to chop every ringing instrument off mid-fade.
    """
    ringing = pcm_release_samples(255, 255, 255, 240, OUTPUT_SAMPLE_RATE // 2)
    clipped = pcm_release_samples(255, 255, 255, 60, OUTPUT_SAMPLE_RATE // 2)
    assert ringing > 2048, "a release-240 tail needs more than the old cap"
    assert ringing > clipped
    # A note cut off during its attack releases from a low level, so it needs
    # far less room than a sustained one.
    short_note = pcm_release_samples(8, 255, 255, 240, 500)
    assert short_note < ringing


def test_cgb_envelope_is_four_bit_and_separate():
    """Square / wave / noise use the CGB envelope, not the PCM one."""
    frames = engine_cgb_envelope_frames(0, 0, 15, 0, 200, 200)
    assert max(frames) == 15, "CGB levels top out at 15, not 255"
    assert set(frames) <= set(range(16))
    # A bigger attack step time means a slower climb.
    quick = engine_cgb_envelope_frames(0, 0, 15, 0, 200, 200)
    slow = engine_cgb_envelope_frames(7, 0, 15, 0, 200, 200)
    assert quick.index(15) < slow.index(15)


def test_cgb_wave_envelope_uses_only_five_levels():
    """gCgb3Vol gives the wave channel mute / 25 / 50 / 75 / 100% and nothing else."""
    env = generate_cgb_wave_envelope(0, 0, 15, 0, 40000)
    levels = sorted(set(np.round(env, 4).tolist()))
    assert len(levels) <= 5, f"wave channel should step, got {levels}"
    for lv in levels:
        assert min(abs(lv - t) for t in (0.0, 0.25, 0.5, 0.75, 1.0)) < 0.02


def test_engine_output_stage_quantises_and_band_limits():
    """13379 Hz / 8-bit / master volume 12-of-15, as src/m4a.c sets it."""
    n = 8192
    t = np.arange(n) / OUTPUT_SAMPLE_RATE
    # A 10 kHz tone is above the engine's 6.7 kHz Nyquist, so it cannot come
    # back out unchanged — it has to alias.
    tone = np.sin(2 * np.pi * 10000 * t).astype(np.float32) * 0.5
    stereo = np.stack([tone, tone], axis=1)
    out = apply_engine_output_stage(stereo)
    assert out.shape == stereo.shape
    assert not np.allclose(out, stereo)
    # Master volume 12 scales by 13/16, so the peak cannot exceed that.
    assert np.max(np.abs(out)) <= 0.5 * (13 / 16) + 0.01
    # Output is 8-bit, so every value sits on a 1/128 step.
    steps = np.unique(np.round(out[:, 0] * 128, 6))
    assert np.allclose(steps, np.round(steps)), "output is not 8-bit quantised"


def test_reverb_feeds_back_and_collapses_stereo():
    """The engine's reverb is a two-tap feedback that pulls stereo to centre."""
    n = OUTPUT_SAMPLE_RATE // 4
    left = np.zeros(n, dtype=np.float32)
    left[:64] = 0.5                     # a click on the left only
    stereo = np.stack([left, np.zeros(n, dtype=np.float32)], axis=1)
    out = apply_reverb_stereo(stereo, 50)
    # Energy appears on the right channel, which a per-channel delay could
    # never do.
    assert np.max(np.abs(out[:, 1])) > 0.001
    # And it keeps arriving well after the click, i.e. it feeds back.
    block = int(round(OUTPUT_SAMPLE_RATE / GBA_FRAME_RATE))
    assert np.max(np.abs(out[3 * block:5 * block, 0])) > 0.001
    assert np.max(np.abs(out)) <= 1.0


def test_square_wave_renders():
    """Square wave produces non-silent audio."""
    audio = render_square_wave(60, 100, 4000, duty_cycle=2)
    assert len(audio) == 4000 + 2048  # duration + default release
    assert np.max(np.abs(audio)) > 0.01


def test_noise_renders():
    """Noise produces non-silent audio."""
    audio = render_noise(60, 100, 4000, period=0)
    assert len(audio) == 4000 + 2048
    assert np.max(np.abs(audio)) > 0.01


def test_noise_metallic():
    """Metallic noise (period=1) renders."""
    audio = render_noise(60, 100, 4000, period=1)
    assert np.max(np.abs(audio)) > 0.01


def test_apply_pan_center():
    """Center panning produces equal left/right."""
    mono = np.ones(100, dtype=np.float32) * 0.5
    stereo = apply_pan(mono, 64)
    assert stereo.shape == (100, 2)
    # Center pan should be roughly equal
    assert abs(stereo[0, 0] - stereo[0, 1]) < 0.05


def test_apply_pan_left():
    """Full left panning."""
    mono = np.ones(100, dtype=np.float32) * 0.5
    stereo = apply_pan(mono, 0)
    assert stereo[0, 0] > stereo[0, 1]  # left louder


# --- Integration tests against real project data ---

def _load_project_data():
    if not os.path.isdir(PROJECT_ROOT):
        return None, None
    sample_data = load_sample_data(PROJECT_ROOT, load_pcm=True)
    vg_data = load_voicegroup_data(PROJECT_ROOT)
    return sample_data, vg_data


def test_directsound_sample_render():
    """Render a DirectSound sample from the actual project."""
    sample_data, vg_data = _load_project_data()
    if sample_data is None:
        return  # skip if project not found

    # Pick the first sample that has PCM data
    for label, sample in sample_data.direct_sound.items():
        if sample.pcm_data:
            audio = render_directsound(
                sample, midi_note=60, base_midi_key=60,
                velocity=100, duration_samples=8000,
            )
            assert len(audio) == 8000 + 2048
            assert np.max(np.abs(audio)) > 0.001, f"Sample {label} rendered silent"
            print(f"OK: rendered {label} ({sample.sample_rate}Hz, {len(sample.pcm_data)} bytes)")
            return
    print("No samples with PCM data found")


def test_directsound_pitch_shift():
    """Pitch shifting produces different audio for different notes."""
    sample_data, _ = _load_project_data()
    if sample_data is None:
        return

    for label, sample in sample_data.direct_sound.items():
        if sample.pcm_data:
            low = render_directsound(sample, 48, 60, 100, 4000)
            high = render_directsound(sample, 72, 60, 100, 4000)
            # They should be different
            assert not np.allclose(low, high), "Pitch shift had no effect"
            return


def test_programmable_wave_render():
    """Render a programmable wave from real project data."""
    sample_data, _ = _load_project_data()
    if sample_data is None:
        return

    for label, wave in sample_data.programmable_waves.items():
        if wave.raw_data:
            audio = render_programmable_wave(wave, 60, 100, 4000)
            assert np.max(np.abs(audio)) > 0.001, f"Wave {label} rendered silent"
            print(f"OK: rendered wave {label}")
            return


def test_render_instrument_directsound():
    """Full render_instrument path for a DirectSound instrument."""
    sample_data, vg_data = _load_project_data()
    if sample_data is None:
        return

    # Use voicegroup141 (mus_cycling uses this)
    vg = vg_data.get_voicegroup('voicegroup141')
    if not vg:
        print("voicegroup141 not found")
        return

    # Find a DirectSound instrument
    for inst in vg.instruments:
        if inst.is_directsound and inst.sample_label:
            audio = render_instrument(
                inst, 60, 100, 8000, sample_data, vg_data,
            )
            assert len(audio) == 8000 + 2048
            if inst.sample_label in sample_data.direct_sound:
                assert np.max(np.abs(audio)) > 0.001
                print(f"OK: instrument slot {inst.slot_index} -> {inst.sample_label}")
            return


def test_render_instrument_square():
    """Full render_instrument path for a square wave."""
    sample_data, vg_data = _load_project_data()
    if sample_data is None:
        return

    # Find a square wave instrument
    for vg in vg_data.voicegroups.values():
        for inst in vg.instruments:
            if inst.is_square:
                audio = render_instrument(
                    inst, 60, 100, 4000, sample_data, vg_data,
                )
                assert np.max(np.abs(audio)) > 0.001
                print(f"OK: square wave in {vg.name} slot {inst.slot_index}")
                return


if __name__ == '__main__':
    print("=== Audio Engine Tests ===\n")
    tests = [
        test_pcm_attack_is_additive,
        test_pcm_decay_is_multiplicative_and_not_inverted,
        test_pcm_release_is_multiplicative_and_not_inverted,
        test_pcm_envelope_matches_real_project_voices,
        test_pcm_envelope_holds_within_a_frame,
        test_release_buffer_is_sized_from_the_envelope,
        test_cgb_envelope_is_four_bit_and_separate,
        test_cgb_wave_envelope_uses_only_five_levels,
        test_engine_output_stage_quantises_and_band_limits,
        test_reverb_feeds_back_and_collapses_stereo,
        test_square_wave_renders,
        test_noise_renders,
        test_noise_metallic,
        test_apply_pan_center,
        test_apply_pan_left,
        test_directsound_sample_render,
        test_directsound_pitch_shift,
        test_programmable_wave_render,
        test_render_instrument_directsound,
        test_render_instrument_square,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS: {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL: {t.__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
