"""Regression: the piano-roll canvas must extend far enough to cover the LOOP
region, not just the notes.

A loop whose end sits past the last note (e.g. "play once, then loop", or a
loop point in trailing silence) used to fall off the right edge: total_ticks
stopped at the last note, so the ruler/cursor couldn't reach the loop tick and a
SAVED loop end rendered off-screen — which looks exactly like "my loop didn't
save". The save is fine; this pins the visible extent.
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

# A sibling test may have installed a minimal PyQt6 stub; this test builds a real
# widget, so purge it and import the genuine package (skip cleanly if absent).
for _m in [m for m in list(sys.modules) if m == "PyQt6" or m.startswith("PyQt6.")]:
    del sys.modules[_m]

try:
    from PyQt6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication(sys.argv)
    from core.sound.song_parser import parse_song_file
    from ui.piano_roll_widget import PianoRollWidget
    _QT_OK = True
except Exception:  # pragma: no cover - environment without real Qt
    _QT_OK = False

# Notes end at tick 48; the loop (LABEL mus_t_1_B1 -> GOTO) ends at tick 60,
# 12 ticks PAST the last note.
_SONG = """\t.include "MPlayDef.s"
\t.equ mus_t_grp, voicegroup013
\t.section .rodata
\t.global mus_t
\t.align 2
mus_t_1:
\t.byte KEYSH , 0
\t.byte VOICE , 21
\t.byte VOL , 100
mus_t_1_B1:
\t.byte N24 , Cn4 , v100
\t.byte W24
\t.byte N24 , Dn4 , v100
\t.byte W24
\t.byte W12
\t.byte GOTO
\t.word mus_t_1_B1
\t.byte FINE
mus_t:
\t.byte 1
\t.byte 0
\t.byte 0
\t.byte 0
\t.word mus_t_grp
\t.word mus_t_1
\t.end
"""


@unittest.skipUnless(_QT_OK, "real PyQt6 unavailable")
class LoopExtentTest(unittest.TestCase):
    def _canvas(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "mus_t.s")
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write(_SONG)
            song = parse_song_file(p)
            w = PianoRollWidget()
            w.load_song_data(song, track_index=-1)
            return w.canvas

    def test_loop_end_detected_past_notes(self):
        c = self._canvas()
        self.assertEqual(c._loop_end, 60)

    def test_extent_covers_loop_end(self):
        c = self._canvas()
        # The canvas must extend AT LEAST to the loop end — otherwise the loop
        # is off-screen and unreachable. (Notes alone would have capped at 48.)
        self.assertGreaterEqual(
            c._total_ticks, c._loop_end,
            "canvas extent must cover a loop ending past the last note")

    def test_extent_has_trailing_room_for_placement(self):
        c = self._canvas()
        # A bit of room past the loop end so a loop point can be PLACED just
        # past the content (the cursor is bounded by the extent).
        self.assertGreater(c._total_ticks, c._loop_end)


if __name__ == "__main__":
    unittest.main()
