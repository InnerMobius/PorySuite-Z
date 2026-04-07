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
    render_directsound,
    render_square_wave,
    render_programmable_wave,
    render_noise,
    render_instrument,
    apply_reverb,
    apply_pan,
    OUTPUT_SAMPLE_RATE,
)

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), '..', 'pokefirered')


def test_adsr_envelope_basic():
    """ADSR envelope generates correct shape (GBA 0-255 range)."""
    # attack=255 = instant, sustain=255 = full volume
    env = generate_adsr_envelope(255, 0, 255, 0, 1000)
    assert len(env) == 1000
    assert env[0] == 1.0  # instant attack, full sustain
    assert env[999] == 1.0


def test_adsr_envelope_with_release():
    """ADSR with release tail."""
    env = generate_adsr_envelope(0, 0, 15, 3, 1000, release_samples=500)
    assert len(env) == 1500
    assert env[999] == 1.0  # still sustaining at end of note-on
    assert env[1499] < env[999]  # release decays


def test_adsr_envelope_with_attack():
    """ADSR with slow attack starts at 0."""
    env = generate_adsr_envelope(7, 0, 15, 0, 16000)
    assert env[0] < 0.1  # starts near zero
    # After attack completes, sustain should be at full
    assert env[-1] == 1.0  # sustain at full


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


def test_apply_reverb():
    """Reverb produces output without crashing."""
    audio = np.random.randn(5000).astype(np.float32) * 0.3
    result = apply_reverb(audio, 64)
    assert len(result) == len(audio)
    assert np.max(np.abs(result)) <= 1.0


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
        test_adsr_envelope_basic,
        test_adsr_envelope_with_release,
        test_adsr_envelope_with_attack,
        test_square_wave_renders,
        test_noise_renders,
        test_noise_metallic,
        test_apply_pan_center,
        test_apply_pan_left,
        test_apply_reverb,
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
