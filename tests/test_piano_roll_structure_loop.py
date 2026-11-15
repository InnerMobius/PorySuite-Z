"""Regression: the Song Structure panel and the canvas agree on the loop, on
the playback clock, and loop edits made there save where they were made.

BUGS.md 2026-11-15 — "Song Structure panel showed and set the loop at text
positions". The panel listed commands at their place in the song's TEXT, where
a pattern call takes no time, so in songs with repeats the loop showed (and
was set) too early: 40 of the project's 81 looping songs. Its Section /
Pattern Call / End Song buttons changed nothing a save wrote.

Real Sound Editor -> Piano Roll path on a throwaway project copy.
"""
import unittest

from piano_roll_gui_support import GuiProjectCase, available

if available():
    from core.sound.song_parser import parse_song_file
    from core.sound.midi_exporter import song_loop_region
    import mido

COMPLEX = ("mus_kakariko", "mus_lostwoods", "mus_faron_woods")


@unittest.skipUnless(available(), "real PyQt6 / game project / mid2agb unavailable")
class StructureLoopTest(GuiProjectCase):

    def _panel_loop(self, win):
        kinds = {k: t for t, k, _n in win._structure_panel._entries
                 if k in ("loop_start", "loop_end")}
        return kinds.get("loop_start"), kinds.get("loop_end")

    def _mid_markers(self, label):
        mf = mido.MidiFile(self.midi_path(label, ".mid"))
        now, out = 0, []
        for m in mf.tracks[0]:
            now += m.time
            if m.type == "marker" and m.text in ("[", "]"):
                out.append((m.text, now * 24 // mf.ticks_per_beat))
        return out

    def test_panel_and_canvas_show_the_loop_where_it_plays(self):
        for label in COMPLEX:
            with self.subTest(label):
                played = song_loop_region(parse_song_file(self.midi_path(label, ".s")))
                win = self.open_song(label)
                c = win._piano_roll.canvas
                self.assertEqual((c._loop_start, c._loop_end), played)
                self.assertEqual(self._panel_loop(win), played)
                # Pattern repeats are listed each time they play, in order.
                pats = [t for t, k, _n in win._structure_panel._entries if k == "pattern"]
                self.assertGreater(len(pats), 3)
                self.assertEqual(pats, sorted(pats))
                win.close()

    def test_move_loop_end_with_the_panel_save_rebuild_reopen(self):
        label = "mus_kakariko"
        win = self.open_song(label)
        c = win._piano_roll.canvas
        start, end = c._loop_start, c._loop_end
        notes = sorted(map(tuple, (sorted(n.items()) for n in c.get_notes())))
        new_end = end - 96 * 4                       # four bars earlier
        win._structure_panel.set_cursor_tick(new_end)
        win._structure_panel._btn_end_here.click()
        self.assertEqual((c._loop_start, c._loop_end), (start, new_end))
        self.assertEqual(self._panel_loop(win), (start, new_end))
        self.save_close(win)

        self.assert_rebuild_matches(label)
        self.assertEqual(song_loop_region(parse_song_file(self.midi_path(label, ".s"))),
                         (start, new_end))
        self.assertIn(("]", new_end), self._mid_markers(label))
        win2 = self.open_song(label)
        c2 = win2._piano_roll.canvas
        self.assertEqual((c2._loop_start, c2._loop_end), (start, new_end))
        self.assertEqual(self._panel_loop(win2), (start, new_end))
        # Notes before the new loop end are untouched.
        before = [n for n in notes if dict(n)["tick"] < new_end]
        after = sorted(map(tuple, (sorted(n.items()) for n in c2.get_notes())))
        self.assertEqual([n for n in after if dict(n)["tick"] < new_end], before)

    def test_create_then_remove_a_loop_and_undo(self):
        label = "mus_castle_town"                   # plays once, no loop
        win = self.open_song(label)
        c = win._piano_roll.canvas
        self.assertEqual((c._loop_start, c._loop_end), (None, None))
        win._structure_panel.loop_requested.emit(192, 96 * 40)   # what "Set Loop…" emits
        self.assertEqual((c._loop_start, c._loop_end), (192, 3840))
        c.undo()
        self.assertEqual((c._loop_start, c._loop_end), (None, None), "undo kept the loop")
        c.redo()
        self.assertEqual((c._loop_start, c._loop_end), (192, 3840), "redo lost the loop")
        self.save_close(win)
        self.assert_rebuild_matches(label)
        self.assertEqual(song_loop_region(parse_song_file(self.midi_path(label, ".s"))),
                         (192, 3840))

        win2 = self.open_song(label)
        self.assertEqual(self._panel_loop(win2), (192, 3840))
        win2._structure_panel._btn_remove.click()
        self.assertEqual(self._panel_loop(win2), (None, None))
        self.save_close(win2)
        self.assert_rebuild_matches(label)
        self.assertIsNone(song_loop_region(parse_song_file(self.midi_path(label, ".s"))))
        self.assertEqual(self._mid_markers(label), [])

    def test_loop_start_here_on_a_song_with_repeats(self):
        label = "mus_faron_woods"
        win = self.open_song(label)
        c = win._piano_roll.canvas
        start, end = c._loop_start, c._loop_end
        win._structure_panel.set_cursor_tick(start + 96 * 8)
        win._structure_panel._btn_start_here.click()
        self.save_close(win)
        self.assert_rebuild_matches(label)
        self.assertEqual(song_loop_region(parse_song_file(self.midi_path(label, ".s"))),
                         (start + 96 * 8, end))


if __name__ == "__main__":
    unittest.main()
