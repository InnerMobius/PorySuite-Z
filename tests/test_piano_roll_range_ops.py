"""Insert / remove time across all tracks (roadmap M7), and triplet snap (M6).

Real Sound Editor -> Piano Roll path on a throwaway project copy. Inserting a
bar moves every note, drawn control, the loop and the time signatures after
it; it saves, rebuilds and reopens shifted. Removing the same bar again gives
back exactly what the song played before. Both are single undo steps.
"""
import unittest

from piano_roll_gui_support import GuiProjectCase, available, app

if available():
    from core.sound.song_parser import parse_song_file
    from core.sound.safe_song_save import audible_signature, signature_differences
    from ui.piano_roll_widget import SNAP_VALUES


@unittest.skipUnless(available(), "real PyQt6 / game project / mid2agb unavailable")
class RangeOpsTest(GuiProjectCase):

    def _all(self, c):
        return (sorted((n["track"], n["tick"], n["pitch"], n["duration"]) for n in c.get_notes()),
                sorted((e["track"], e["tick"], e["type"], e["value"]) for e in c._control_events))

    def test_insert_then_remove_a_bar(self):
        label = "mus_kakariko"
        s = self.midi_path(label, ".s")
        sig0 = audible_signature(parse_song_file(s))
        win = self.open_song(label)
        c = win._piano_roll.canvas
        notes0, ctrls0 = self._all(c)
        loop0 = (c._loop_start, c._loop_end)
        at = 96 * 2
        win._structure_panel.set_cursor_tick(at)
        win._structure_panel.shift_time_requested.emit(at, 96)      # what Insert Time… emits
        notes1, ctrls1 = self._all(c)
        # After the point: moved. Held across it: lengthened by the insert.
        self.assertEqual(notes1, sorted((tr, t + 96 if t >= at else t, p,
                                         d + 96 if t < at < t + d else d)
                                        for tr, t, p, d in notes0))
        self.assertEqual(ctrls1, sorted((tr, t + 96 if t >= at else t, k, v)
                                        for tr, t, k, v in ctrls0))
        self.assertEqual((c._loop_start, c._loop_end),
                         tuple(x + 96 if x >= at else x for x in loop0))
        win._btn_undo.click()
        self.assertEqual(self._all(c), (notes0, ctrls0))
        win._btn_redo.click()
        self.save_close(win)
        self.assert_rebuild_matches(label)

        win2 = self.open_song(label)
        c2 = win2._piano_roll.canvas
        self.assertEqual(self._all(c2)[0], notes1, "the inserted bar was not saved")
        win2._structure_panel.shift_time_requested.emit(at, -96)    # what Remove Time… emits
        self.assertEqual(self._all(c2)[0], notes0)
        self.save_close(win2)
        self.assert_rebuild_matches(label)
        self.assertEqual(signature_differences({0: sig0}, {0: audible_signature(parse_song_file(s))}),
                         [], "inserting then removing a bar did not give the song back")

    def test_remove_time_shortens_a_note_reaching_into_it(self):
        label = "mus_castle_town"
        win = self.open_song(label)
        c = win._piano_roll.canvas
        n = next(x for x in sorted(c._notes, key=lambda x: x["tick"])
                 if x["duration"] >= 12 and x["tick"] > 0)
        at = n["tick"] + 6                                 # cut starts inside the note
        t, d = n["tick"], n["duration"]
        win._structure_panel.shift_time_requested.emit(at, -3)
        moved = [x for x in c._notes if x["tick"] == t and x["pitch"] == n["pitch"]
                 and x["track"] == n["track"]]
        self.assertTrue(moved)
        self.assertEqual(moved[0]["duration"], d - 3 if t + d > at + 3 else at - t)

    def test_triplet_snap_fits_the_game_clock(self):
        for name, ticks in SNAP_VALUES.items():
            if "triplet" in name:
                self.assertEqual((24 * 4) % (ticks * 3), 0, name)


if __name__ == "__main__":
    unittest.main()
