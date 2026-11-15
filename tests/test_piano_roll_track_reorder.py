"""Track reordering in the Piano Roll: Move Up / Move Down.

Every note, drawn control, instrument, mute / solo and unsaved edit moves with
its track; undo / redo step through moves; the save puts each track's MIDI
track in the new place (mid2agb numbers tracks by their order in the .mid),
moving whole MIDI tracks with their names and every other event intact.

Real Sound Editor -> Piano Roll path on a throwaway project copy.
"""
import unittest

from piano_roll_gui_support import GuiProjectCase, available, app

if available():
    import mido
    from core.sound.midi_patch import _reorder_chunks, MidiPatchUnavailable


def _chunks(path):
    """Per MIDI track: (name, sorted messages with absolute times)."""
    out = []
    for tr in mido.MidiFile(path).tracks:
        now, msgs, name = 0, [], None
        for m in tr:
            now += m.time
            if m.type == "track_name":
                name = m.name
            msgs.append((now, str(m.copy(time=0))))
        out.append((name, sorted(msgs)))
    return out


@unittest.skipUnless(available(), "real PyQt6 / game project / mid2agb unavailable")
class TrackReorderTest(GuiProjectCase):

    def test_move_tracks_with_pending_edits_save_reopen(self):
        label = "mus_castle_town"
        before_chunks = _chunks(self.midi_path(label, ".mid"))
        win = self.open_song(label)
        c = win._piano_roll.canvas
        n = len(win._song.tracks)
        # A pending note edit and instrument change on track 3; mute track 3.
        c.push_undo()
        note = next(x for x in c._notes if x.get("track", 0) == 2)
        note["pitch"] += 1
        win._mark_dirty()
        row = win._track_sidebar._rows[2]
        row._inst_combo.setCurrentIndex((row._inst_combo.currentIndex() + 2) % row._inst_combo.count())
        win._track_sidebar.set_mute_solo_state({2}, set())
        voice3 = [(x.cmd, x.value) for x in win._song.tracks[2].commands if x.cmd == "VOICE"]
        per_track = [self.track_notes(c, i) for i in range(n)]

        # Track 3 down twice with the sidebar buttons; track 7 up once from the menu signal.
        win._track_sidebar.select_row(2)
        win._track_sidebar._btn_down.click()
        win._track_sidebar._btn_down.click()
        win._track_sidebar._rows[6].move_requested.emit(6, -1)
        order = [0, 1, 3, 4, 2, 6, 5]               # old index now at each position
        self.assertEqual([self.track_notes(c, i) for i in range(n)],
                         [per_track[j] for j in order])
        self.assertEqual([(x.cmd, x.value) for x in win._song.tracks[4].commands
                          if x.cmd == "VOICE"], voice3)
        self.assertEqual(win._track_sidebar.get_mute_solo_state()["muted"], {4},
                         "mute did not move with the track")

        # Undo the three moves, redo them.
        for _ in range(3):
            win._btn_undo.click()
        self.assertEqual([self.track_notes(c, i) for i in range(n)], per_track)
        self.assertEqual(win._track_sidebar.get_mute_solo_state()["muted"], {2})
        for _ in range(3):
            win._btn_redo.click()
        self.assertEqual([self.track_notes(c, i) for i in range(n)],
                         [per_track[j] for j in order])

        self.save_close(win)
        self.assert_rebuild_matches(label)
        win2 = self.open_song(label)
        c2 = win2._piano_roll.canvas
        self.assertEqual([self.track_notes(c2, i) for i in range(n)],
                         [per_track[j] for j in order], "saved track order is wrong")
        self.assertEqual([(x.cmd, x.value) for x in win2._song.tracks[4].commands
                          if x.cmd == "VOICE"], voice3, "the instrument did not move with the track")

        # The .mid: MIDI tracks moved whole. Names and events of tracks with no
        # edit are identical, just in their new place.
        after_chunks = _chunks(self.midi_path(label, ".mid"))
        self.assertEqual(len(after_chunks), len(before_chunks))
        before_names = [nm for nm, _ in before_chunks]
        self.assertEqual(sorted(map(str, before_names)), sorted(map(str, (nm for nm, _ in after_chunks))))
        untouched = [nm for nm, _ in before_chunks if nm is not None]
        b_by_name = {nm: msgs for nm, msgs in before_chunks}
        a_by_name = {nm: msgs for nm, msgs in after_chunks}
        edited_chunk = None
        for nm in untouched:
            if b_by_name[nm] != a_by_name.get(nm):
                self.assertIsNone(edited_chunk, f"more than one MIDI track changed: {nm}")
                edited_chunk = nm
        self.assertIsNotNone(edited_chunk, "the edited track's MIDI track should differ")

    def test_reorder_rules(self):
        # Single-channel note tracks in chunks 1..3, conductor in chunk 0.
        self.assertEqual(_reorder_chunks(4, [(1, 0), (2, 1), (3, 2)]), [0, 1, 2, 3])
        self.assertEqual(_reorder_chunks(4, [(3, 2), (1, 0), (2, 1)]), [0, 3, 1, 2])
        # A new track (appended chunk 4) moved to the front.
        self.assertEqual(_reorder_chunks(5, [(4, None), (1, 0), (2, 1), (3, 2)]), [0, 4, 1, 2, 3])
        # Two channels of one MIDI track cannot be separated or swapped.
        with self.assertRaises(MidiPatchUnavailable):
            _reorder_chunks(3, [(1, 0), (2, 3), (1, 1)])
        with self.assertRaises(MidiPatchUnavailable):
            _reorder_chunks(2, [(1, 1), (1, 0)])
        # The first MIDI track (tempo and loop markers) cannot move behind another.
        with self.assertRaises(MidiPatchUnavailable):
            _reorder_chunks(3, [(1, 0), (0, 1), (2, 2)])


if __name__ == "__main__":
    unittest.main()
