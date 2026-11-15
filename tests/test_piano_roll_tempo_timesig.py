"""Tempo map and time signatures in the Piano Roll.

Real Sound Editor -> Piano Roll path on a throwaway project copy:
* the Tempo lane shows every tempo change where it plays; mid-song tempo
  points are added, moved and deleted with the mouse, saved, and only the
  .mid's tempo events change;
* the BPM box changes the starting tempo — and the preview's starting tempo
  event with it (it used to keep the old one);
* time signatures are edited by bar, move the bar lines, undo / redo, and
  are saved as the .mid's time-signature events without changing what plays.
"""
import unittest

from piano_roll_gui_support import GuiProjectCase, available, app

if available():
    import mido
    from PyQt6.QtCore import Qt, QPoint
    from PyQt6.QtTest import QTest
    from core.sound.song_parser import parse_song_file
    from core.sound.midi_exporter import expand_song, song_time_signatures
    from core.sound.safe_song_save import audible_signature, signature_differences
    from ui.piano_roll_time_signatures import (TimeSignatureDialog, bars_to_ticks,
                                               ticks_to_bars)


def _metas(path, kind):
    mf = mido.MidiFile(path)
    out = []
    for tr in mf.tracks:
        now = 0
        for m in tr:
            now += m.time
            if m.type == kind:
                out.append((now * 24 // mf.ticks_per_beat, m))
    return out


def _non(path, kinds):
    """Every event except *kinds*, with absolute times, per MIDI track."""
    out = []
    for tr in mido.MidiFile(path).tracks:
        now, msgs = 0, []
        for m in tr:
            now += m.time
            if m.type not in kinds:
                msgs.append((now, str(m.copy(time=0))))
        out.append(sorted(msgs))
    return out


@unittest.skipUnless(available(), "real PyQt6 / game project / mid2agb unavailable")
class TempoTimeSigTest(GuiProjectCase):

    def _lane(self, win, kind):
        combo = win._piano_roll._lane_combo
        combo.setCurrentIndex(combo.findData(kind))
        return win._piano_roll.lane

    def test_tempo_lane_shows_edits_and_saves_the_tempo_map(self):
        label = "mus_silph"
        mid, s = self.midi_path(label, ".mid"), self.midi_path(label, ".s")
        song = parse_song_file(s)
        played = sorted((t, c.value) for t, _x, c in expand_song(song)[0].events if c.cmd == "TEMPO")
        others_before = _non(mid, {"set_tempo"})
        win = self.open_song(label)
        win.show()
        app.processEvents()
        c = win._piano_roll.canvas
        lane = self._lane(win, "TEMPO")

        def tempos():
            return sorted((e["tick"], e["value"]) for e in c._control_events
                          if e["type"] == "TEMPO" and e["track"] == 0)
        self.assertEqual(sorted(set(tempos())), sorted(set(played)), "tempo lane != played tempo map")
        # Move one mid-song tempo point later, delete another, add a new one.
        mids = [(t, v) for t, v in tempos() if t > 0]
        (t1, v1), (t2, v2) = mids[0], mids[-1]
        win._piano_roll.scroll_to_tick(t1)
        app.processEvents()
        p = QPoint(int(lane.tick_to_x(t1)), int(lane.value_to_y(v1)))
        QTest.mousePress(lane, Qt.MouseButton.LeftButton, pos=p)
        QTest.mouseMove(lane, QPoint(int(lane.tick_to_x(t1 + 24)), int(lane.value_to_y(v1 + 10))))
        QTest.mouseRelease(lane, Qt.MouseButton.LeftButton,
                           pos=QPoint(int(lane.tick_to_x(t1 + 24)), int(lane.value_to_y(v1 + 10))))
        win._piano_roll.scroll_to_tick(t2)
        app.processEvents()
        QTest.mouseClick(lane, Qt.MouseButton.RightButton,
                         pos=QPoint(int(lane.tick_to_x(t2)), int(lane.value_to_y(v2))))
        t_new = 96 * 3 + 48
        win._piano_roll.scroll_to_tick(t_new)
        app.processEvents()
        QTest.mouseClick(lane, Qt.MouseButton.LeftButton,
                         pos=QPoint(int(lane.tick_to_x(t_new)), int(lane.value_to_y(40))))
        want = tempos()
        self.assertNotIn((t2, v2), want)
        self.assertIn(t_new, [t for t, _v in want])
        notes = sorted(map(tuple, (sorted(n.items()) for n in c.get_notes())))

        self.save_close(win)
        self.assert_rebuild_matches(label)
        saved = sorted((t, x.value) for t, _x, x in
                       expand_song(parse_song_file(s))[0].events if x.cmd == "TEMPO")
        self.assertEqual(sorted(set(saved)), sorted(set(want)))
        self.assertEqual(_non(mid, {"set_tempo"}), others_before,
                         "something other than tempo changed in the .mid")
        win2 = self.open_song(label)
        self.assertEqual(sorted(map(tuple, (sorted(n.items()) for n in win2._piano_roll.canvas.get_notes()))),
                         notes)

    def test_bpm_box_moves_the_preview_starting_tempo_too(self):
        label = "mus_victory_wild"
        win = self.open_song(label)
        c = win._piano_roll.canvas
        win._bpm_spin.setValue(win._bpm_spin.value() + 20)
        tbs = max(1, int(win._song.tempo_base or 1))
        start = [e["value"] for e in c._control_events
                 if e["type"] == "TEMPO" and e["track"] == 0 and e["tick"] == 0]
        self.assertEqual(start, [int(win._bpm_spin.value() * tbs / 2)],
                         "the preview would start at the old tempo")
        self.save_close(win)
        self.assert_rebuild_matches(label)

    def test_time_signatures_edit_bars_not_music(self):
        label = "mus_castle_town"
        s, mid = self.midi_path(label, ".s"), self.midi_path(label, ".mid")
        before_sig = audible_signature(parse_song_file(s))
        win = self.open_song(label)
        c = win._piano_roll.canvas
        old_sigs = list(win._time_sigs)
        old_bars = list(c._bar_starts)

        def run_dialog(dlg):
            dlg._add_row(5, 3, 4)                     # from bar 5: 3/4
            dlg._add_row(9, 4, 4)                     # from bar 9: 4/4 again
            dlg._accept()
            return dlg.DialogCode.Accepted
        orig = TimeSignatureDialog.exec
        TimeSignatureDialog.exec = run_dialog
        try:
            win._structure_panel._btn_time_sigs.click()
        finally:
            TimeSignatureDialog.exec = orig
        want = [(384, 3, 4), (384 + 4 * 72, 4, 4)]
        self.assertEqual(win._time_sigs, sorted(old_sigs + want))
        self.assertIn(384 + 72, c._bar_starts, "bar lines did not follow 3/4")
        win._btn_undo.click()
        self.assertEqual(list(c._bar_starts), old_bars)
        win._btn_redo.click()
        self.assertIn(384 + 72, c._bar_starts)

        self.save_close(win)
        self.assert_rebuild_matches(label)
        self.assertEqual(song_time_signatures(parse_song_file(s)), sorted(old_sigs + want))
        ts = [(t, m.numerator, m.denominator) for t, m in _metas(mid, "time_signature")]
        self.assertEqual(sorted(ts), sorted(old_sigs + want))
        self.assertEqual(signature_differences({0: before_sig}, {0: audible_signature(parse_song_file(s))}),
                         [], "changing time signatures changed what plays")

    def test_bar_tick_conversion_round_trips(self):
        for sigs in ([], [(0, 3, 4)], [(0, 1, 8), (12, 4, 4), (108, 3, 4)],
                     [(384, 2, 4), (432, 4, 4)], [(0, 3, 32), (9, 4, 4), (105, 6, 4)]):
            self.assertEqual(bars_to_ticks(ticks_to_bars(sigs)), sorted(sigs), sigs)


if __name__ == "__main__":
    unittest.main()
