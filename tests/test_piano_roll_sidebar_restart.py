"""An instrument or pan picked in the Piano Roll sidebar survives a restart.

Reported: change a track's instrument while the song plays and you hear the
new one, but as soon as playback starts again from the top (Stop then Play,
the song ending, a loop back to the start) the SAVED instrument comes back.

Why: playback is fed from the canvas's control-event list, which holds its
own copy of each track's opening instrument / pan as a tick-0 event and
replays it whenever playback passes tick 0. The sidebar changed the song's
opening command and the live track state, but not that tick-0 event, so the
first thing a restart did was put the old value back. (The tempo box had the
same defect, fixed 2026-11-15.)

Real Sound Editor -> Piano Roll path on a throwaway project copy, real
sidebar widgets. The audio device is a stand-in; the sequencer's own audio
callback is called by hand, so its real event-processing code decides which
instrument the track ends up using.
"""
import os
import sys
import unittest

from piano_roll_gui_support import GuiProjectCase, available, app

if available():
    import numpy as np
    from core.sound.song_parser import parse_song_file


class _FakeStream:
    def __init__(self, *a, **k):
        pass

    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        pass


_OPENING_ENDS = ('WAIT', 'NOTE', 'TIE', 'EOT', 'PATT', 'GOTO', 'FINE')


def _opening(track, kind):
    for c in track.commands:
        if c.cmd in _OPENING_ENDS:
            return None
        if c.cmd == kind and c.value is not None:
            return c.value
    return None


@unittest.skipUnless(available(), "real PyQt6 / game project / mid2agb unavailable")
class SidebarSurvivesRestartTest(GuiProjectCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sys.modules["sounddevice"].OutputStream = _FakeStream

    def _song_with_opening_voice_and_pan(self):
        songs = os.path.dirname(self.midi_path("x", ".s"))
        for f in sorted(os.listdir(songs)):
            if not (f.startswith("mus_") and f.endswith(".s")):
                continue
            label = f[:-2]
            if not os.path.isfile(self.midi_path(label, ".mid")):
                continue
            song = parse_song_file(self.midi_path(label, ".s"))
            for ti, tr in enumerate(song.tracks):
                if _opening(tr, "VOICE") is not None and _opening(tr, "PAN") is not None:
                    return label, ti
        self.skipTest("no song with an opening instrument and pan")

    @staticmethod
    def _run_from_top(win, chunks=4):
        """Start playback from tick 0 through the real Play button and let the
        sequencer's audio callback process the opening events."""
        win._on_stop()
        win._btn_play.click()
        seq = win._sequencer
        buf = np.zeros((1024, 2), dtype=np.float32)
        for _ in range(chunks):
            seq._audio_callback(buf, 1024, None, None)
        return seq

    def test_sidebar_instrument_and_pan_survive_stop_and_play(self):
        label, ti = self._song_with_opening_voice_and_pan()
        win = self.open_song(label)
        canvas = win._piano_roll.canvas
        row = win._track_sidebar._rows[ti]

        saved_voice = _opening(win._song.tracks[ti], "VOICE")
        saved_pan = _opening(win._song.tracks[ti], "PAN")
        self.assertIn((0, saved_voice), self.track_ctrls(canvas, ti, "VOICE"),
                      "precondition: the canvas holds a tick-0 instrument event")

        # Control: before any change, a restart plays the saved instrument.
        seq = self._run_from_top(win)
        self.assertEqual(seq._track_states[ti].voice, saved_voice)

        new_voice = (saved_voice + 1) % 128
        new_pan = 10 if saved_pan != 10 else 120

        # While playing, pick a new instrument and pan in the sidebar.
        row._inst_combo.setCurrentIndex(new_voice)
        row._pan_slider.setValue(new_pan)
        app.processEvents()
        self.assertEqual(seq._track_states[ti].voice, new_voice,
                         "the live change did not reach playback")

        # THE regression: Stop, then Play from the top.
        seq = self._run_from_top(win)
        self.assertEqual(seq._track_states[ti].voice, new_voice,
                         "a restart put the saved instrument back")
        self.assertEqual(seq._track_states[ti].pan, new_pan,
                         "a restart put the saved pan back")

        # The Instrument lane's tick-0 point shows what is played.
        self.assertIn((0, new_voice), self.track_ctrls(canvas, ti, "VOICE"))
        self.assertNotIn((0, saved_voice), self.track_ctrls(canvas, ti, "VOICE"))

        # Undo takes both back together.
        win._on_stop()
        win._btn_undo.click()      # the pan change
        win._btn_undo.click()      # the instrument change
        app.processEvents()
        seq = self._run_from_top(win)
        self.assertEqual(seq._track_states[ti].voice, saved_voice,
                         "undo left the new instrument in the preview")
        self.assertEqual(seq._track_states[ti].pan, saved_pan)

        # Redo, save, and the saved song is what a build makes and plays it.
        win._on_stop()
        row = win._track_sidebar._rows[ti]
        row._inst_combo.setCurrentIndex(new_voice)
        row._pan_slider.setValue(new_pan)
        app.processEvents()
        win._on_stop()
        self.save(win)
        self.assert_rebuild_matches(label)
        reread = parse_song_file(self.midi_path(label, ".s"))
        self.assertEqual(_opening(reread.tracks[ti], "VOICE"), new_voice)
        self.assertEqual(_opening(reread.tracks[ti], "PAN"), new_pan)


if __name__ == "__main__":
    unittest.main()
