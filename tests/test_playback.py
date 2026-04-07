"""
End-to-end playback test: renders a song and saves it as a .wav file.

Run from porysuite root:
    python tests/test_playback.py

This will create tests/output_mus_cycling.wav that you can listen to.
"""

import sys
import os
import types
import struct
import time

project_root = os.path.join(os.path.dirname(__file__), '..')
sys.path.insert(0, project_root)
local_env_mod = types.ModuleType('local_env')
local_env_mod.LocalUtil = type('LocalUtil', (), {})
sys.modules['local_env'] = local_env_mod

import numpy as np

from core.sound.song_parser import parse_song_file, get_song_tempo
from core.sound.voicegroup_parser import load_voicegroup_data
from core.sound.sample_loader import load_sample_data
from core.sound.track_renderer import render_song, OUTPUT_SAMPLE_RATE

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), '..', 'pokefirered')
OUTPUT_DIR = os.path.join(os.path.dirname(__file__))


def write_wav(filepath: str, audio: np.ndarray, sample_rate: int):
    """Write a float32 stereo array to a .wav file."""
    # Convert float32 [-1,1] to int16
    audio_16 = (audio * 32767).clip(-32768, 32767).astype(np.int16)

    channels = audio_16.shape[1] if audio_16.ndim == 2 else 1
    sample_count = len(audio_16)
    data_bytes = audio_16.tobytes()

    with open(filepath, 'wb') as f:
        # RIFF header
        f.write(b'RIFF')
        f.write(struct.pack('<I', 36 + len(data_bytes)))
        f.write(b'WAVE')
        # fmt chunk
        f.write(b'fmt ')
        f.write(struct.pack('<I', 16))  # chunk size
        f.write(struct.pack('<H', 1))   # PCM format
        f.write(struct.pack('<H', channels))
        f.write(struct.pack('<I', sample_rate))
        f.write(struct.pack('<I', sample_rate * channels * 2))  # byte rate
        f.write(struct.pack('<H', channels * 2))  # block align
        f.write(struct.pack('<H', 16))  # bits per sample
        # data chunk
        f.write(b'data')
        f.write(struct.pack('<I', len(data_bytes)))
        f.write(data_bytes)


def render_and_save(song_filename: str, output_name: str):
    """Render a song and save as WAV."""
    song_path = os.path.join(PROJECT_ROOT, 'sound', 'songs', 'midi', song_filename)
    if not os.path.isfile(song_path):
        print(f"  Song file not found: {song_path}")
        return

    print(f"  Parsing {song_filename}...")
    song = parse_song_file(song_path)
    vg_data = load_voicegroup_data(PROJECT_ROOT)
    sample_data = load_sample_data(PROJECT_ROOT)

    bpm = get_song_tempo(song)
    print(f"  Song: {song.label}, BPM: {bpm}, Tracks: {len(song.tracks)}, VG: {song.voicegroup}")

    print(f"  Rendering...")
    t0 = time.time()
    stereo = render_song(song, vg_data, sample_data)
    elapsed = time.time() - t0

    duration_sec = len(stereo) / OUTPUT_SAMPLE_RATE
    print(f"  Rendered {duration_sec:.1f}s in {elapsed:.1f}s")

    wav_path = os.path.join(OUTPUT_DIR, output_name)
    write_wav(wav_path, stereo, OUTPUT_SAMPLE_RATE)
    file_size_kb = os.path.getsize(wav_path) / 1024
    print(f"  Saved: {wav_path} ({file_size_kb:.0f} KB)")


if __name__ == '__main__':
    if not os.path.isdir(PROJECT_ROOT):
        print(f"Project not found at {PROJECT_ROOT}")
        sys.exit(1)

    print("=== Song Playback Test ===\n")

    # Render mus_cycling (the classic Cycling Road theme — 8 tracks)
    render_and_save('mus_cycling.s', 'output_mus_cycling.wav')
    print()

    # Render mus_pallet (simpler song, good for quick test)
    render_and_save('mus_pallet.s', 'output_mus_pallet.wav')
    print()

    print("Done! Listen to the .wav files in the tests/ folder.")
