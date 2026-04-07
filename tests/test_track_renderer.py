"""
Integration test for track_renderer.py — renders a real song from the project.
Run from porysuite root: python tests/test_track_renderer.py
"""

import sys
import os
import time
import types

project_root = os.path.join(os.path.dirname(__file__), '..')
sys.path.insert(0, project_root)
local_env_mod = types.ModuleType('local_env')
local_env_mod.LocalUtil = type('LocalUtil', (), {})
sys.modules['local_env'] = local_env_mod

import numpy as np

from core.sound.song_parser import parse_song_file, get_song_tempo, get_song_duration_ticks, get_loop_info
from core.sound.voicegroup_parser import load_voicegroup_data
from core.sound.sample_loader import load_sample_data
from core.sound.track_renderer import render_track, render_song, ticks_to_samples, OUTPUT_SAMPLE_RATE

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), '..', 'pokefirered')


def test_ticks_to_samples():
    """Basic tick-to-sample conversion."""
    # At 120 BPM, tbs=1: ticks_per_frame = 120/150 = 0.8
    # ticks_per_second = 0.8 * 59.7275 = 47.782
    # 96 ticks (one whole note) ~= 96/47.782 * 32768 ~= 65,826 samples
    samples = ticks_to_samples(96, 120, 1)
    assert 60000 < samples < 70000, f"96 ticks at 120bpm = {samples} samples"
    print(f"  96 ticks at 120bpm = {samples} samples ({samples/OUTPUT_SAMPLE_RATE:.2f}s)")


def test_render_single_track():
    """Render track 1 of mus_cycling."""
    if not os.path.isdir(PROJECT_ROOT):
        print("  SKIP: project not found")
        return

    song = parse_song_file(os.path.join(PROJECT_ROOT, 'sound', 'songs', 'midi', 'mus_cycling.s'))
    vg_data = load_voicegroup_data(PROJECT_ROOT)
    sample_data = load_sample_data(PROJECT_ROOT)

    bpm = get_song_tempo(song)
    vg = vg_data.get_voicegroup(song.voicegroup)
    assert vg is not None, f"Voicegroup {song.voicegroup} not found"

    # Render just the first track
    track = song.tracks[0]
    note_count = sum(1 for c in track.commands if c.cmd == 'NOTE')
    print(f"  Song: {song.label}, BPM: {bpm}, VG: {song.voicegroup}")
    print(f"  Track 0: {track.label}, {note_count} notes")

    t0 = time.time()
    stereo = render_track(
        track, vg, sample_data, vg_data, bpm, song.tempo_base,
        song.master_volume, song.key_shift,
    )
    elapsed = time.time() - t0

    assert len(stereo) > 0, "Track rendered empty"
    peak = np.max(np.abs(stereo))
    duration_sec = len(stereo) / OUTPUT_SAMPLE_RATE
    print(f"  Rendered: {duration_sec:.1f}s, peak={peak:.3f}, time={elapsed:.2f}s")
    assert peak > 0.001, "Track is silent"


def test_render_full_song():
    """Render all tracks of mus_cycling and mix."""
    if not os.path.isdir(PROJECT_ROOT):
        print("  SKIP: project not found")
        return

    song = parse_song_file(os.path.join(PROJECT_ROOT, 'sound', 'songs', 'midi', 'mus_cycling.s'))
    vg_data = load_voicegroup_data(PROJECT_ROOT)
    sample_data = load_sample_data(PROJECT_ROOT)

    print(f"  Rendering {song.label} ({len(song.tracks)} tracks)...")

    t0 = time.time()
    stereo = render_song(
        song, vg_data, sample_data,
        progress_callback=lambda i, n: print(f"    Track {i+1}/{n}...") if i < n else None,
    )
    elapsed = time.time() - t0

    assert stereo.ndim == 2 and stereo.shape[1] == 2, f"Expected stereo, got shape {stereo.shape}"
    duration_sec = len(stereo) / OUTPUT_SAMPLE_RATE
    peak = np.max(np.abs(stereo))
    print(f"  Result: {duration_sec:.1f}s stereo, peak={peak:.3f}, render time={elapsed:.1f}s")
    assert peak > 0.01, "Song is nearly silent"


def test_render_short_song():
    """Render a shorter song (mus_berry_pick) to verify pattern handling."""
    if not os.path.isdir(PROJECT_ROOT):
        print("  SKIP: project not found")
        return

    song = parse_song_file(os.path.join(PROJECT_ROOT, 'sound', 'songs', 'midi', 'mus_berry_pick.s'))
    vg_data = load_voicegroup_data(PROJECT_ROOT)
    sample_data = load_sample_data(PROJECT_ROOT)

    bpm = get_song_tempo(song)
    duration_ticks = get_song_duration_ticks(song)
    loop_info = get_loop_info(song)

    print(f"  Song: {song.label}, BPM: {bpm}, ticks: {duration_ticks}")
    print(f"  Loop: {loop_info}")

    t0 = time.time()
    stereo = render_song(song, vg_data, sample_data)
    elapsed = time.time() - t0

    duration_sec = len(stereo) / OUTPUT_SAMPLE_RATE
    peak = np.max(np.abs(stereo))
    print(f"  Result: {duration_sec:.1f}s, peak={peak:.3f}, time={elapsed:.1f}s")


if __name__ == '__main__':
    print("=== Track Renderer Tests ===\n")
    tests = [
        test_ticks_to_samples,
        test_render_single_track,
        test_render_full_song,
        test_render_short_song,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            print(f"\n{t.__name__}:")
            t()
            print(f"  PASS")
            passed += 1
        except Exception as e:
            print(f"  FAIL: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
