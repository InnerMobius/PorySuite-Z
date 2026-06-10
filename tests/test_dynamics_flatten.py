"""Regression tests for core.sound.dynamics — counting + flattening the hidden
mid-song control envelopes (volume / tempo / vibrato / pan / bend) the piano
roll doesn't show.  Guards the 2026-06-10 'Flatten Dynamics' feature.
"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "core"))

from core.sound.song_parser import TrackCommand, Track  # noqa: E402
from core.sound import dynamics as D  # noqa: E402


def _track():
    cmds = [
        TrackCommand(cmd="VOL", tick=0, value=100),     # tick-0 base
        TrackCommand(cmd="TEMPO", tick=0, value=60),    # tick-0 base
        TrackCommand(cmd="NOTE", tick=0, pitch=60, velocity=100, duration=24),
        TrackCommand(cmd="VOL", tick=24, value=40),     # mid-song envelope
        TrackCommand(cmd="MOD", tick=24, value=20),     # mid-song
        TrackCommand(cmd="NOTE", tick=24, pitch=62, velocity=100, duration=24),
        TrackCommand(cmd="TEMPO", tick=48, value=120),  # mid-song tempo shift
        TrackCommand(cmd="VOL", tick=48, value=90),     # mid-song
    ]
    return Track(index=0, label="t", commands=cmds)


class _Song:
    def __init__(self, tracks):
        self.tracks = tracks


def test_count_mid_song_only():
    c = D.count_track_dynamics(_track())
    assert c["VOL"] == 2      # ticks 24, 48 (NOT the tick-0 base)
    assert c["MOD"] == 1
    assert c["TEMPO"] == 1
    assert c["PAN"] == 0


def test_flatten_removes_midsong_keeps_base_and_notes():
    tr = _track()
    removed = D.flatten_track_dynamics(tr)
    assert removed == 4  # 2 VOL + 1 MOD + 1 TEMPO at tick > 0
    base = {(c.cmd, c.value) for c in tr.commands if c.cmd in ("VOL", "TEMPO")}
    assert ("VOL", 100) in base and ("TEMPO", 60) in base   # tick-0 base survives
    assert all(not (c.tick > 0 and c.cmd in D.DYNAMICS_CMDS)
               for c in tr.commands)                         # no mid-song dynamics
    assert sum(1 for c in tr.commands if c.cmd == "NOTE") == 2  # notes preserved


def test_voice_change_is_not_flattened():
    # A mid-song VOICE (instrument) change is structural, not a dynamic — keep it.
    tr = Track(index=0, label="t", commands=[
        TrackCommand(cmd="VOICE", tick=0, value=0),
        TrackCommand(cmd="VOICE", tick=48, value=5),   # mid-song instrument swap
        TrackCommand(cmd="VOL", tick=48, value=40),    # mid-song dynamic
    ])
    removed = D.flatten_track_dynamics(tr)
    assert removed == 1  # only the VOL
    assert any(c.cmd == "VOICE" and c.tick == 48 for c in tr.commands)


def test_describe_skips_zero_types():
    s = D.describe_dynamics(
        {"VOL": 38, "MOD": 16, "TEMPO": 0, "PAN": 0, "BEND": 0, "BENDR": 0})
    assert "38 volume" in s and "16 vibrato" in s
    assert "tempo" not in s and "pan" not in s


def test_song_level_count_and_flatten():
    song = _Song([_track(), _track()])
    assert D.count_song_dynamics(song)["VOL"] == 4
    removed = D.flatten_song_dynamics(song)
    assert removed == 8
    assert D.total_mid_song_events(D.count_song_dynamics(song)) == 0
