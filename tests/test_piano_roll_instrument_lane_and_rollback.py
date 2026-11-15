"""Mid-song instrument changes, and interrupted saves.

* The Instrument lane shows every instrument change where it plays (they
  were invisible: the canvas showed one instrument per track) and edits
  them; the save writes only that track's program changes.
* A save interrupted while it replaces the files (the second file refuses)
  is rolled back: the .mid and .s are byte-identical to before and no
  temporary file is left; the window keeps its edits. A refusal that lasts
  only a moment (a virus scanner holding the file) is retried and succeeds.

Real Sound Editor -> Piano Roll path on a throwaway project copy.
"""
import glob
import os
import unittest
from unittest import mock

from piano_roll_gui_support import GuiProjectCase, available, app

if available():
    import mido
    from PyQt6.QtCore import Qt, QPoint
    from PyQt6.QtTest import QTest
    from core.sound.song_parser import parse_song_file
    from core.sound.midi_exporter import expand_song
    import core.sound.safe_song_save as sss


def _programs(path):
    out = {}
    for tr in mido.MidiFile(path).tracks:
        now = 0
        for m in tr:
            now += m.time
            if m.type == "program_change":
                out.setdefault(m.channel, []).append((now, m.program))
    return out


@unittest.skipUnless(available(), "real PyQt6 / game project / mid2agb unavailable")
class InstrumentLaneRollbackTest(GuiProjectCase):

    def _song_with_voice_changes(self):
        for f in sorted(os.listdir(os.path.dirname(self.midi_path("x", ".s")))):
            if not f.startswith("mus_") or not f.endswith(".s"):
                continue
            label = f[:-2]
            if not os.path.isfile(self.midi_path(label, ".mid")):
                continue
            song = parse_song_file(self.midi_path(label, ".s"))
            for ti, ex in enumerate(expand_song(song)):
                end = ex.goto_time if ex.goto_time is not None else 10 ** 9
                if any(c.cmd == "VOICE" and 0 < t < end for t, _s, c in ex.events):
                    return label, ti
        self.skipTest("no song with a mid-song instrument change")

    def test_instrument_lane_shows_and_edits_mid_song_changes(self):
        label, ti = self._song_with_voice_changes()
        song = parse_song_file(self.midi_path(label, ".s"))
        ex = expand_song(song)[ti]
        end = ex.goto_time if ex.goto_time is not None else 10 ** 9
        played = {(t, c.value) for t, _s, c in ex.events if c.cmd == "VOICE" and t < end}
        before = _programs(self.midi_path(label, ".mid"))
        win = self.open_song(label)
        win.show()
        app.processEvents()
        c = win._piano_roll.canvas
        win._track_sidebar.select_row(ti)
        combo = win._piano_roll._lane_combo
        combo.setCurrentIndex(combo.findData("VOICE"))
        lane = win._piano_roll.lane
        shown = {(e["tick"], e["value"]) for e in c._control_events
                 if e["track"] == ti and e["type"] == "VOICE" and e["tick"] < end}
        self.assertEqual(shown, played, "the lane does not show the instrument changes")
        t, v = sorted(x for x in shown if x[0] > 0)[0]
        new_v = (v + 7) % 128
        win._piano_roll.scroll_to_tick(t)
        app.processEvents()
        p = QPoint(int(lane.tick_to_x(t)), int(lane.value_to_y(v)))
        QTest.mousePress(lane, Qt.MouseButton.LeftButton, pos=p)
        QTest.mouseMove(lane, QPoint(p.x(), int(lane.value_to_y(new_v))))
        QTest.mouseRelease(lane, Qt.MouseButton.LeftButton, pos=QPoint(p.x(), int(lane.value_to_y(new_v))))
        got = dict((tt, vv) for tt, vv in self.track_ctrls(c, ti, "VOICE"))[t]
        self.assertNotEqual(got, v)
        self.save_close(win)
        self.assert_rebuild_matches(label)
        ex2 = expand_song(parse_song_file(self.midi_path(label, ".s")))[ti]
        self.assertIn((t, got), {(tt, cc.value) for tt, _s, cc in ex2.events if cc.cmd == "VOICE"})
        after = _programs(self.midi_path(label, ".mid"))
        changed = [ch for ch in set(before) | set(after) if before.get(ch) != after.get(ch)]
        self.assertEqual(len(changed), 1, f"program changes moved on channels {changed}")

    def test_interrupted_save_rolls_back_and_a_brief_lock_is_retried(self):
        label = "mus_kakariko"
        mid, s = self.midi_path(label, ".mid"), self.midi_path(label, ".s")
        mid0, s0 = open(mid, "rb").read(), open(s, "rb").read()
        win = self.open_song(label)
        c = win._piano_roll.canvas
        c.push_undo()
        c._notes[3]["pitch"] += 1
        win._mark_dirty()
        real = os.replace

        def refuse_s(src, dst):            # the .mid lands, then the .s refuses for good
            if dst.endswith(".s"):
                raise PermissionError(13, "Access is denied (test)")
            return real(src, dst)
        with mock.patch.object(sss.os, "replace", side_effect=refuse_s):
            del self.warnings[:]
            win._btn_save.click()
        self.assertTrue(any("Save Failed" in w for w in self.warnings), self.warnings)
        self.assertEqual(open(mid, "rb").read(), mid0, "the .mid was not rolled back")
        self.assertEqual(open(s, "rb").read(), s0, "the .s changed")
        self.assertEqual(glob.glob(os.path.join(os.path.dirname(s), "*.porysuite-tmp")), [])
        self.assertTrue(win.has_unsaved_changes(), "the edit was lost after the failed save")

        calls = {"n": 0}

        def brief_lock(src, dst):          # refused twice, then fine
            if dst.endswith(".s") and calls["n"] < 2:
                calls["n"] += 1
                raise PermissionError(13, "in use (test)")
            return real(src, dst)
        with mock.patch.object(sss.os, "replace", side_effect=brief_lock):
            self.save(win)
        self.assertEqual(calls["n"], 2)
        self.assertNotEqual(open(s, "rb").read(), s0)
        self.assert_rebuild_matches(label)


if __name__ == "__main__":
    unittest.main()
