"""Regression: the per-track volume slider must SCALE a track's VOL envelope
proportionally (preserving any fade/decay shape), not flatten every step to one
value.

A decaying SFX (e.g. a confirm chime that echoes quieter each repeat) used to
collapse to a constant level when its volume was adjusted ("no decay / ^v^v^v").
This drives the real PianoRollWindow: open a song with a VOL decay, move the
track volume slider, save, and confirm the decay survives — uniformly scaled.
"""
import os
import sys
import tempfile
import unittest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for _p in (ROOT_DIR, os.path.join(ROOT_DIR, "core")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# A sibling test may have stubbed PyQt6; this builds a real widget.
for _m in [m for m in list(sys.modules) if m == "PyQt6" or m.startswith("PyQt6.")]:
    del sys.modules[_m]

try:
    from PyQt6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication(sys.argv)
    from core.sound.song_parser import parse_song_file
    from ui.piano_roll_window import PianoRollWindow
    _QT_OK = True
except Exception:  # pragma: no cover
    _QT_OK = False

# VOL envelope decays 96 -> 72 -> 48 -> 24 (raw multipliers; master mvl=80).
_SONG = """\t.include "MPlayDef.s"
\t.equ mus_f_grp, voicegroup013
\t.equ mus_f_mvl, 80
\t.section .rodata
\t.global mus_f
\t.align 2
mus_f_1:
\t.byte KEYSH , 0
\t.byte VOICE , 81
\t.byte VOL , 96*mus_f_mvl/mxv
\t.byte N03 , Bn4 , v127
\t.byte W08
\t.byte VOL , 72*mus_f_mvl/mxv
\t.byte N03 , Cn5 , v127
\t.byte W08
\t.byte VOL , 48*mus_f_mvl/mxv
\t.byte N03 , Dn5 , v127
\t.byte W08
\t.byte VOL , 24*mus_f_mvl/mxv
\t.byte N03 , En5 , v127
\t.byte W08
\t.byte FINE
mus_f:
\t.byte 1
\t.byte 0
\t.byte 0
\t.byte 0
\t.word mus_f_grp
\t.word mus_f_1
\t.end
"""


@unittest.skipUnless(_QT_OK, "real PyQt6 unavailable")
class VolumeFadeTest(unittest.TestCase):
    def _roundtrip(self, new_volume):
        """Open the song in a piano roll, set the track volume, save, and
        return (before_envelope, after_envelope) as effective VOL values."""
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "mus_f.s")
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write(_SONG)
            song = parse_song_file(p)
            song.file_path = p
            before = [c.value for c in song.tracks[0].commands if c.cmd == 'VOL']
            win = PianoRollWindow(song)
            win._on_track_volume(0, new_volume)
            win.save_to_disk()
            after = [c.value for c in parse_song_file(p).tracks[0].commands
                     if c.cmd == 'VOL']
            return before, after

    def test_decay_survives_volume_change(self):
        before, after = self._roundtrip(30)   # before[0] is 60; halve it
        self.assertEqual(len(after), len(before))
        # NOT flattened
        self.assertGreater(len(set(after)), 1, "envelope was flattened to one level")
        # still strictly decreasing (the decay shape)
        self.assertTrue(all(after[i] > after[i + 1] for i in range(len(after) - 1)),
                        f"decay not preserved: {after}")

    def test_scale_is_uniform(self):
        before, after = self._roundtrip(30)
        # every step scaled by the same factor (within integer rounding)
        scale = after[0] / before[0]
        for b, a in zip(before, after):
            self.assertAlmostEqual(a, b * scale, delta=2,
                                   msg=f"non-uniform scale: {before} -> {after}")

    def test_louder_keeps_decay(self):
        # Raising the volume must also keep the shape, not flatten.
        before, after = self._roundtrip(75)   # raise above the first (60)
        self.assertGreater(len(set(after)), 1)
        self.assertTrue(all(after[i] > after[i + 1] for i in range(len(after) - 1)))


if __name__ == "__main__":
    unittest.main()
