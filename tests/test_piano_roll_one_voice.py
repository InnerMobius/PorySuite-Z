"""Regression: the piano roll models ONE instrument per track. Saving must not
re-emit mid-track VOICE changes — they desync from the notes on any move/drag
(a moved note slides onto the wrong instrument: the "note went silent after I
dragged the song left" bug). Positional controls (VOL/PAN) are still kept.
"""
import os
import sys
import unittest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for _p in (ROOT_DIR, os.path.join(ROOT_DIR, "core")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.sound.song_writer import notes_to_track_commands
from core.sound.song_parser import TrackCommand


def _orig():
    # A track whose imported source has a mid-track instrument switch (VOICE 21
    # -> VOICE 10 at tick 50) plus a mid-track volume swell at tick 40.
    return [
        TrackCommand(cmd='KEYSH', tick=0, value=0),
        TrackCommand(cmd='VOICE', tick=0, value=21),
        TrackCommand(cmd='VOL', tick=0, value=100),
        TrackCommand(cmd='VOL', tick=40, value=80),     # positional — keep
        TrackCommand(cmd='VOICE', tick=50, value=10),   # mid-track — drop
    ]


class OneVoicePerTrackTest(unittest.TestCase):
    def _commands(self):
        notes = [
            {'tick': 0, 'pitch': 64, 'duration': 24, 'velocity': 100, 'track': 0},
            {'tick': 60, 'pitch': 62, 'duration': 24, 'velocity': 100, 'track': 0},
        ]
        return notes_to_track_commands(
            notes, track_index=0, voice=21, volume=100,
            original_commands=_orig())

    def test_exactly_one_voice_at_tick0(self):
        cmds = self._commands()
        voices = [c for c in cmds if c.cmd == 'VOICE']
        self.assertEqual(len(voices), 1, "a track must save exactly one instrument")
        self.assertEqual(voices[0].value, 21)   # the track's initial instrument
        self.assertEqual(voices[0].tick, 0)

    def test_mid_track_voice_is_dropped(self):
        cmds = self._commands()
        self.assertFalse(
            any(c.cmd == 'VOICE' and (c.tick or 0) > 0 for c in cmds),
            "mid-track VOICE changes must not be re-emitted (they desync on move)")

    def test_notes_survive(self):
        cmds = self._commands()
        notes = [c for c in cmds if c.cmd in ('NOTE', 'TIE')]
        self.assertEqual(len(notes), 2)

    def test_positional_volume_is_kept(self):
        # Dropping VOICE must NOT drop other mid-song controls (VOL swell).
        cmds = self._commands()
        self.assertTrue(
            any(c.cmd == 'VOL' and (c.tick or 0) > 0 for c in cmds),
            "mid-song VOL (a volume swell) is positional and must be preserved")


if __name__ == "__main__":
    unittest.main()
